"""Local durable jobs, subscriptions, and a portable source archive."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import formats
import paths
import procutil
from urls import identity   # moved to urls.py unchanged; kept here for importers

# Kept for importers; Library() without a path resolves its file when it is created.
CATALOG = paths.data_dir() / 'library.sqlite3'
WRITE_CHECK_TIMEOUT = 2   # seconds open_library waits for another writer before it stops checking
_log = logging.getLogger('YtDlpDesktop.library')
# A job's options['mode'] is read by the 1.5.1 window (继续未完成, 查看结果), which only knows
# the five old formats.  A 1.6.0-only format is therefore stored as the nearest old one in
# 'mode' and as itself in this additive key; Library.job() and history() give 1.6.0 callers
# the real format back in 'mode' (core fix review-compat-1).
MODE_KEY = 'mode_v2'
# Keys of a download job's options that are not options of one download.
JOB_OPTION_KEYS = ('urls', 'overrides', 'variants', MODE_KEY)


def _legacy_mode(mode):
    """The 1.5.1 format closest to a 1.6.0-only one (same kind: audio -> 原始最佳, video -> MKV)."""
    preset = formats.by_mode(mode)
    return formats.LEGACY_MODES[4] if preset.kind == 'video' else formats.LEGACY_MODES[0]


def stored_options(options):
    """Job options as they are written: a 1.6.0-only 'mode' becomes the nearest 1.5.1 mode,
    the real one goes to MODE_KEY.  Other values (and non-dicts) unchanged."""
    if not isinstance(options, dict):
        return options
    mode = options.get('mode')
    if mode in formats.MODES and mode not in formats.LEGACY_MODES:
        options = dict(options)
        options['mode'] = _legacy_mode(mode)
        options[MODE_KEY] = mode
    return options


def read_options(options):
    """Job options as 1.6.0 uses them: the real format back in 'mode', MODE_KEY removed."""
    if not isinstance(options, dict) or MODE_KEY not in options:
        return options
    options = dict(options)
    real = options.pop(MODE_KEY)
    if real in formats.MODES:
        options['mode'] = real
    return options


def _read_record(data):
    if isinstance(data, dict) and isinstance(data.get('options'), dict):
        data['options'] = read_options(data['options'])
    return data


def _format_of(options):
    """The format key (formats.format_key) of one download's options dict, or None."""
    try:
        return formats.format_key(formats.by_mode(options.get('mode')), options.get('resolution'))
    except (ValueError, TypeError, AttributeError):
        return None


def job_downloads(data):
    """Every download a download job stands for, in job order: [{'url', 'format', 'options',
    'done'}].  ``data`` is a job record's data (Library.job(id)['data']).

    One entry per (link, format): the job's links with the job's options (or the link's
    own from 'overrides'), then the links that were added again in another format
    ('variants', core fix review-e2e-4).  ``options`` is a plain options dict (no job keys);
    ``done`` says whether that download finished, by (link, format) in
    'completed_variants', or, for a record without such an entry for the link (1.5.1, or a
    job from before this list existed), by the link in 'completed' for its first format.
    """
    data = data if isinstance(data, dict) else {}
    options = read_options(data.get('options') or {})
    if not isinstance(options, dict):
        return []
    base = {key: value for key, value in options.items() if key not in JOB_OPTION_KEYS}
    overrides = options.get('overrides') if isinstance(options.get('overrides'), dict) else {}
    completed = data.get('completed') if isinstance(data.get('completed'), list) else []
    finished = {tuple(pair) for pair in (data.get('completed_variants') or []) if isinstance(pair, list) and len(pair) == 2}
    tracked = {url for url, _format in finished}
    result = []
    for url in options.get('urls') or []:
        if not isinstance(url, str):
            continue
        own = overrides.get(url)
        own = dict(own) if isinstance(own, dict) else dict(base)
        key = _format_of(own)
        done = (url, key) in finished or (url not in tracked and url in completed)
        result.append({'url': url, 'format': key, 'options': own, 'done': done})
    for variant in options.get('variants') or []:
        if not isinstance(variant, dict) or not isinstance(variant.get('url'), str):
            continue
        own = variant.get('options')
        own = dict(own) if isinstance(own, dict) else dict(base)
        key = variant.get('format') or _format_of(own)
        result.append({'url': variant['url'], 'format': key, 'options': own,
                       'done': (variant['url'], key) in finished})
    return result


def safe_name(value, limit=60):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:limit].rstrip(' .') or '未命名'
    if re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def write_json(path, data):
    # Readers of such a file in this process use paths.read_text: they take turns with this writer.
    paths.atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


alive = procutil.alive


def _storable(value):
    """An event value whose texts can be written as UTF-8: a lone UTF-16 surrogate (a title
    cut inside an emoji, which yt-dlp prints escaped) becomes '?'.  Other values unchanged."""
    if isinstance(value, str):
        try:
            value.encode('utf-8')
            return value
        except UnicodeEncodeError:
            return value.encode('utf-8', 'replace').decode('utf-8')
    if isinstance(value, dict):
        return {_storable(key): _storable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_storable(item) for item in value)
    return value


class Library:
    def __init__(self, path=None):
        self.path = Path(path or paths.catalog_path()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, title TEXT,
                    status TEXT, updated REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS pages(job TEXT, url TEXT, data TEXT, PRIMARY KEY(job,url));
                CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, name TEXT,
                    enabled INTEGER, interval INTEGER, next_check REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS entries(subscription TEXT, key TEXT, pending INTEGER,
                    data TEXT, PRIMARY KEY(subscription,key));
                CREATE TABLE IF NOT EXISTS archives(folder TEXT PRIMARY KEY, updated REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS downloads(key TEXT, format TEXT, url TEXT, title TEXT, path TEXT,
                    finished REAL, job TEXT, PRIMARY KEY(key, format));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def tx(self):
        """db() for read-modify-write: the write lock is taken first (BEGIN IMMEDIATE), so two
        threads changing one row take turns instead of overwriting each other.

        Every call opens its own connection.  A method must therefore never call another
        public method while its own ``with self.tx()`` / ``with self.db()`` block is open:
        the second connection would wait for the first one's lock until the timeout.  Work
        that is shared goes into private helpers that take the open connection or the
        loaded data (_record_download, _extend).
        """
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    def create_job(self, kind, title, options, subscription=None):
        options = dict(options)
        options['proxy'] = ''  # Login/proxy secrets never enter persisted settings.
        options.pop('resume', None)
        options.pop('job_id', None)
        options.pop('history_path', None)
        options = stored_options(options)
        job = uuid.uuid4().hex
        data = dict(options=options, subscription=subscription, checkpoint=None, errors=[],
                    owner=os.getpid(), worker=None, completed=[], files=[])
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job, kind, title, 'running', time.time(), json.dumps(data, ensure_ascii=False)))
        return job

    def job(self, job):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
            if not row:
                raise ValueError('找不到任务记录。')
            result = dict(row)
            result['data'] = _read_record(json.loads(row['data']))
            result['pages'] = [json.loads(r[0]) for r in db.execute('SELECT data FROM pages WHERE job=? ORDER BY rowid', (job,))]
            return result

    def change_job(self, job, status=None, **updates):
        with self.tx() as db:
            row = db.execute('SELECT data,status FROM jobs WHERE id=?', (job,)).fetchone()
            data = json.loads(row[0])
            if 'options' in updates:
                updates['options'] = stored_options(updates['options'])
            data.update(updates)
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), status or row[1], time.time(), job))

    def collection_event(self, job, kind, value):
        # Page and its frontier commit together, inside the worker BEFORE GUI notification.
        with self.tx() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()
            data = json.loads(row[0])
            if kind == 'collect_page':
                page = dict(value)
                data['checkpoint'] = page.pop('_checkpoint')
                db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)', (job, page['url'], json.dumps(page, ensure_ascii=False)))
            elif kind == 'collect_checkpoint':
                data['checkpoint'] = value
            elif kind == 'collect_error':
                data['errors'].append(value)
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (json.dumps(data, ensure_ascii=False), time.time(), job))

    def history(self, limit=300):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY updated DESC LIMIT ?', (limit,))]
        for row in rows:
            row['data'] = _read_record(json.loads(row['data']))
            if row['status'] == 'running' and not alive(row['data'].get('worker') or row['data'].get('owner')):
                self.change_job(row['id'], status='interrupted')
                row['status'] = 'interrupted'
        return rows

    @staticmethod
    def _extend(data, urls, overrides=None):
        """Add links (and the options of those that differ from the job's) to a loaded job record.

        The record keeps one option set per (link, format): a new link goes to 'urls' (its own
        options, when they differ from the job's, to 'overrides' as before); a link that the
        job has already, added again in another format, goes to the additive list
        options['variants'] = [{url, format, options}] and leaves the first download's options
        alone (core fix review-e2e-4).  The same link in the same format again (a retry, maybe
        into another folder) updates its options as before.
        """
        options = data.setdefault('options', {})
        known = options.setdefault('urls', [])
        overrides = overrides if isinstance(overrides, dict) else {}
        current = read_options(options)
        base = {key: value for key, value in current.items() if key not in JOB_OPTION_KEYS}
        given = [url for url in urls if isinstance(url, str)]
        for url in given:
            own = overrides.get(url) if isinstance(overrides.get(url), dict) else None
            if url not in known:
                known.append(url)
                if own is not None:
                    options.setdefault('overrides', {})[url] = own
                continue
            stored = (options.get('overrides') or {}).get(url)
            first = _format_of(stored if isinstance(stored, dict) else base)
            wanted = _format_of(own if own is not None else base)
            if first is None or wanted is None or wanted == first:
                if own is not None:
                    options.setdefault('overrides', {})[url] = own
                continue
            variants = options.setdefault('variants', [])
            record = dict(own) if own is not None else dict(base)
            existing = next((item for item in variants if isinstance(item, dict)
                             and item.get('url') == url and item.get('format') == wanted), None)
            if existing is None:
                variants.append({'url': url, 'format': wanted, 'options': record})
            else:
                existing['options'] = record
        # Options of links that were not in this call's list: as before.
        rest = {url: value for url, value in overrides.items() if url not in given}
        if rest:
            options.setdefault('overrides', {}).update(rest)

    @staticmethod
    def _completed_variant(data, url, format):
        """Remember a finished download by (link, format) in the additive 'completed_variants'
        list ('completed' keeps the links, as 1.5.1 reads them)."""
        if not isinstance(url, str) or not isinstance(format, str) or not format:
            return
        pairs = data.setdefault('completed_variants', [])
        if [url, format] not in pairs:
            pairs.append([url, format])

    @staticmethod
    def _sole_format(data, url):
        """The format of ``url`` in this job when the job has the link in one format only, else None."""
        found = {item['format'] for item in job_downloads(data) if item['url'] == url and item['format']}
        return found.pop() if len(found) == 1 else None

    def extend_job(self, job, urls, overrides=None):
        """Links that joined a running job: a later resume sees them, each with its own format and folder."""
        with self.tx() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()
            data = json.loads(row[0])
            self._extend(data, urls, overrides)
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (json.dumps(data, ensure_ascii=False), time.time(), job))

    def rename_job(self, job, title):
        with self.tx() as db:
            db.execute('UPDATE jobs SET title=? WHERE id=?', (str(title), job))

    @staticmethod
    def _record_download(db, keys, format, url, title, path, job):
        finished = time.time()
        for key in dict.fromkeys(key for key in keys if key):
            db.execute('INSERT OR REPLACE INTO downloads VALUES(?,?,?,?,?,?,?)',
                       (key, format, url, title, path, finished, job))

    def record_download(self, keys, format, url, title, path, job):
        """Remember a finished file under each of its keys (the link key, the media key) and its format."""
        with self.tx() as db:
            self._record_download(db, keys, format, url, title, path, job)

    def find_download(self, keys, format):
        """The newest recorded download of this format under one of the keys, with ``exists``
        (is the file still where it was saved?), or None."""
        keys = [key for key in keys if key]
        if not keys:
            return None
        with self.db() as db:
            row = db.execute('SELECT * FROM downloads WHERE format=? AND key IN (%s) ORDER BY finished DESC, rowid DESC LIMIT 1'
                             % ','.join('?' * len(keys)), (format, *keys)).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result['exists'] = bool(result['path']) and Path(result['path']).is_file()
        except (OSError, ValueError, TypeError):
            result['exists'] = False
        return result

    def download_event(self, job, kind, value):
        """Keep the job record in step with the events of a download.

        Keys that 1.6.0 added to the events are read with .get(): their absence is not an
        error (the 1.5.1 window forwards item, file, item_done and done only).  Unknown
        kinds are ignored.  Every write goes through this method's own connection.
        """
        if kind not in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done'):
            return
        value = _storable(value)
        with self.tx() as db:
            row = db.execute('SELECT data,status FROM jobs WHERE id=?', (job,)).fetchone()
            data, status = json.loads(row[0]), row[1]
            if kind == 'item':
                data['current'] = value['url']
                data['current_key'] = value.get('key')
                data['current_format'] = value.get('format')
                data['current_origin'] = value.get('origin')
                data['current_multi'] = bool(value.get('multi'))
            elif kind == 'title':
                # The first title names the job, so the history shows a song instead of a count.
                if isinstance(value, str) and value.strip() and not data.get('titled'):
                    count = len((data.get('options') or {}).get('urls') or [])
                    db.execute('UPDATE jobs SET title=? WHERE id=?',
                               (f'{value} 等 {count} 条' if count > 1 else value, job))
                    data['titled'] = True
            elif kind == 'media':
                if isinstance(value, dict) and isinstance(value.get('filepath'), str) and value['filepath']:
                    keys = []
                    extractor, media_id = value.get('extractor_key'), value.get('id')
                    if isinstance(extractor, str) and extractor and extractor.lower() != 'generic' and media_id not in (None, ''):
                        keys.append(f'{extractor.lower()}:{media_id}')
                    # A playlist, search or channel link stands for many files: its link key is not one of them.
                    if data.get('current_key') and not data.get('current_multi'):
                        keys.append(data['current_key'])
                    if keys and data.get('current_format'):
                        title = value.get('title')
                        self._record_download(db, keys, data['current_format'], data.get('current'),
                                              title if isinstance(title, str) else '', value['filepath'], job)
            elif kind == 'file':
                if value not in data['files']:
                    data['files'].append(value)
            elif kind == 'queued':
                self._extend(data, value.get('urls') or [], value.get('overrides'))
            elif kind == 'item_done':
                if value.get('skipped') == 'downloaded':
                    # No item event came before it: data['current'] is not this link.
                    url = value.get('url')
                    if url:
                        if url not in data['completed']:
                            data['completed'].append(url)
                        self._completed_variant(data, url, value.get('format') or self._sole_format(data, url))
                        for key in dict.fromkeys(identity(link) for link in (url, value.get('origin')) if link):
                            db.execute('UPDATE entries SET pending=0 WHERE key=?', (key,))
                elif value['success'] and data.get('current'):
                    url = data['current']
                    if url not in data['completed']:
                        data['completed'].append(url)
                    self._completed_variant(data, url, value.get('format') or data.get('current_format'))
                    for key in dict.fromkeys(identity(link) for link in (url, data.get('current_origin')) if link):
                        db.execute('UPDATE entries SET pending=0 WHERE key=?', (key,))
                if value.get('error'):
                    data.setdefault('errors', []).append(str(value['error']))
                    data['errors'] = data['errors'][-20:]
                    # The short Chinese reason the task row showed for that line (additive key;
                    # 1.5.1 ignores it): the history note shows it (polish ui-wording-consistency-5).
                    reason = value.get('reason')
                    if isinstance(reason, str) and reason.strip():
                        data['last_reason'] = {'error': data['errors'][-1], 'reason': reason.strip()}
            elif kind == 'done':
                data['result'] = value
                # Not completed while anything is left: a failure, an archive error, a bot-check
                # block, an engine that could not run (its links are stored as unstarted), or
                # any other link of this run that did not finish.
                status = 'interrupted' if value['cancelled'] else (
                    'partial' if (value['failed'] or value.get('archive_errors') or value.get('blocked')
                                  or value.get('engine_error') or value.get('pending_urls')) else 'completed')
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), status, time.time(), job))

    def subscription(self, name, options, interval=3600, media_only=True, first_new=True):
        if interval not in (0, 900, 3600, 86400):
            raise ValueError('不支持的订阅检查间隔。')
        options = dict(options)
        options['proxy'] = ''
        key = uuid.uuid4().hex
        data = dict(options=options, media_only=media_only, first_new=first_new,
                    initialized=False, last_check=None, last_error='', last_new=0)
        with self.db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?)',
                       (key, name.strip()[:100] or '未命名订阅', 1, interval, time.time(), json.dumps(data, ensure_ascii=False)))
        return key

    def subscriptions(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM subscriptions ORDER BY rowid')]
            for row in rows:
                row['data'] = json.loads(row['data'])
                row['pending'] = db.execute('SELECT count(*) FROM entries WHERE subscription=? AND pending=1', (row['id'],)).fetchone()[0]
            return rows

    def configure_subscription(self, key, enabled, interval):
        if interval not in (0, 900, 3600, 86400):
            raise ValueError('不支持的订阅检查间隔。')
        with self.db() as db:
            db.execute('UPDATE subscriptions SET enabled=?,interval=?,next_check=? WHERE id=?', (int(enabled), interval, time.time() + interval, key))

    def finish_subscription(self, key, pages, successful, error=''):
        with self.tx() as db:
            row = db.execute('SELECT * FROM subscriptions WHERE id=?', (key,)).fetchone()
            data = json.loads(row['data'])
            new = 0
            if successful:
                for page in pages:
                    for item in page['links']:
                        if data['media_only'] and item['kind'] == '网页链接':
                            continue
                        item_key = identity(item['url'])
                        old = db.execute('SELECT pending FROM entries WHERE subscription=? AND key=?', (key, item_key)).fetchone()
                        pending = old[0] if old else int(data['initialized'] or data['first_new'])
                        new += int(old is None and pending)
                        payload = dict(item, page_url=page['url'], page_title=page['title'])
                        db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?)', (key, item_key, pending, json.dumps(payload, ensure_ascii=False)))
                data.update(initialized=True, last_check=time.time(), last_error='', last_new=new)
            else:
                # A stopped/failed scan never establishes a baseline or consumes new items.
                data['last_error'] = error or '检查中断；下次重试，不更新已见记录'
            db.execute('UPDATE subscriptions SET data=?,next_check=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), time.time() + max(900, row['interval']), key))
            return new

    def pending(self, key):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM entries WHERE subscription=? AND pending=1 ORDER BY rowid', (key,))]

    def downloaded(self, url):
        with self.db() as db:
            db.execute('UPDATE entries SET pending=0 WHERE key=?', (identity(url),))

    def archive_index(self, folder, data):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO archives VALUES(?,?,?)', (str(folder), time.time(), json.dumps(data, ensure_ascii=False)))

    def archives(self):
        with self.db() as db:
            return [dict(folder=r['folder'], **json.loads(r['data'])) for r in db.execute('SELECT * FROM archives ORDER BY updated DESC')]

    def contexts(self, urls):
        remaining, result = set(urls), {}
        with self.db() as db:
            for row in db.execute('SELECT job,url,data FROM pages ORDER BY rowid DESC'):
                if not remaining:
                    break
                page = json.loads(row['data'])
                for item in page['links']:
                    if item['url'] in remaining:
                        result[item['url']] = dict(title=item['title'], source=item['source'], job_id=row['job'], page_url=page['url'])
                        remaining.remove(item['url'])
        return result


def _prove_writable(path) -> None:
    """Raise sqlite3.Error unless this database can be written.

    Opening proves nothing: CREATE TABLE IF NOT EXISTS writes nothing when the tables
    exist, so a database in a write-protected folder opens without an error and fails at
    the first real write.  Here a write transaction is started and a page is changed,
    which makes SQLite open its journal file beside the database; then everything is
    rolled back, so the file itself stays as it was.  A database that is only busy
    (another writer is at work right now) counts as writable.
    """
    db = sqlite3.connect(path, timeout=WRITE_CHECK_TIMEOUT, isolation_level=None)
    try:
        db.execute('BEGIN IMMEDIATE')
        try:
            version = int(db.execute('PRAGMA user_version').fetchone()[0])
            db.execute(f'PRAGMA user_version={version}')
        finally:
            db.execute('ROLLBACK')
    except sqlite3.OperationalError as error:
        # sqlite_errorcode is the extended code; its low byte is SQLITE_BUSY (5) or SQLITE_LOCKED (6).
        if (getattr(error, 'sqlite_errorcode', None) or 0) & 0xFF not in (5, 6):
            raise
    finally:
        db.close()


def open_library(path=None):
    """(Library, warning).  The history is optional: when its file cannot be opened or
    cannot be written, a throw-away database in the temp folder serves this session and
    the warning says so."""
    try:
        store = Library(path)
        _prove_writable(store.path)
        beside = paths.data_dir() / 'library.sqlite3'
        if path is None and beside.is_file() and store.path != beside.resolve():
            _log.warning('the history beside the program cannot be written (%s); this session uses %s',
                         beside, store.path)
        return store, ''
    except (OSError, sqlite3.Error) as error:
        folder = Path(tempfile.mkdtemp(prefix='ytdlp-history-'))
        atexit.register(shutil.rmtree, folder, ignore_errors=True)
        return Library(folder / 'library.sqlite3'), f'历史记录本次不保存：{error}'


class Archive:
    def __init__(self, root, library=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = library

    def prepare(self, url, context=None):
        context = context or {}
        source = context.get('source') or url
        host = urlsplit(source).hostname or '来源'
        source_key = hashlib.sha256(identity(source).encode()).hexdigest()[:8]
        item_key = hashlib.sha256(identity(url).encode()).hexdigest()[:16]
        # Stable ID paths survive title changes and retries, and avoid duplicate media.
        folder = self.root / (safe_name(host, 45) + '_' + source_key) / item_key
        if not folder.resolve().is_relative_to(self.root):
            raise ValueError('归档目录超出资料库范围。')
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / '资料信息.json'
        try:
            data = json.loads(paths.read_text(record))
            if not (isinstance(data, dict) and isinstance(data.get('files'), list)
                    and isinstance(data.get('transcripts'), list)):
                raise ValueError('not an archive record')
        except FileNotFoundError:
            data = None
        except ValueError:
            # Unreadable (cut off, edited by hand): kept aside as 资料信息.bad.json and rebuilt,
            # so that the link can be archived again.  The media files in the folder stay.
            self._set_aside(record)
            data = None
        if data is None:
            data = dict(url=url, source=source, title=context.get('title') or url,
                        created=time.time(), files=[], transcripts=[])
        # Never let an old result erase existing asset records.
        data['url'] = url
        if context.get('title'):
            data['title'] = context['title']
        if context.get('page'):
            page = context['page']
            note = '\n说明：源正文超过长度上限，以下内容已截断。' if page.get('text_truncated') else ''
            for filename, body in [('网页正文.txt', page.get('text', '')), ('网页正文.md', page.get('markdown', ''))]:
                (folder / filename).write_text(f"{page['title']}\n来源：{page['url']}{note}\n\n{body}\n", encoding='utf-8-sig')
            data['page_title'] = page['title']
        self.save(folder, data)
        return folder

    @staticmethod
    def _set_aside(record):
        """Rename an unreadable record to 资料信息.bad.json, or 资料信息.bad-<time>.json when that
        name is taken; never replaces a file.  OSError when it cannot be renamed."""
        stamp = time.strftime('%Y%m%d-%H%M%S')
        names = ['资料信息.bad.json', f'资料信息.bad-{stamp}.json'] + [f'资料信息.bad-{stamp}-{n}.json' for n in range(2, 100)]
        for name in names:
            target = record.with_name(name)
            if os.path.lexists(target):
                continue
            try:
                os.rename(record, target)
                return target
            except FileExistsError:
                continue
        raise OSError(f'无法保留损坏的资料信息文件：{record}')

    def save(self, folder, data):
        folder = Path(folder).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError('归档目录超出资料库范围。')
        write_json(folder / '资料信息.json', data)
        (folder / '阅读说明.txt').write_text(
            f"{data['title']}\n原链接：{data['url']}\n采集来源：{data['source']}\n作者：{data.get('uploader') or '来源未提供'}\n\n"
            + data.get('description', '') + '\n\n媒体文件：\n' + '\n'.join(data['files'])
            + '\n\n关联转写稿：\n' + '\n'.join(data['transcripts']) + '\n', encoding='utf-8-sig')
        if self.library:
            self.library.archive_index(folder, data)

    def media(self, folder, metadata):
        folder = Path(folder).resolve()
        path = Path(metadata['filepath']).resolve()
        if not path.is_relative_to(folder) or not folder.is_relative_to(self.root) or not path.is_file():
            raise ValueError('归档文件与本条资料目录不匹配。')
        data = json.loads(paths.read_text(folder / '资料信息.json'))
        data.update({key: metadata[key] for key in ('id', 'title', 'uploader', 'description', 'webpage_url', 'upload_date') if metadata.get(key) is not None})
        relative = str(path.relative_to(folder))
        if relative not in data['files']:
            data['files'].append(relative)
        data['downloaded'] = time.time()
        self.save(folder, data)

    def attach(self, folder, source):
        folder, source = Path(folder).resolve(), Path(source).resolve()
        if not folder.is_relative_to(self.root) or source.suffix.lower() not in ('.txt', '.srt', '.md', '.vtt'):
            raise ValueError('请选择 TXT、SRT、MD 或 VTT 转写稿。')
        data = json.loads(paths.read_text(folder / '资料信息.json'))
        target = folder / '转写稿' / safe_name(source.stem, 75)
        target = target.with_name(target.name + '_' + hashlib.sha256(source.read_bytes()).hexdigest()[:10] + source.suffix.lower())
        target.parent.mkdir(exist_ok=True)
        if not target.resolve().is_relative_to(folder):
            raise ValueError('转写稿目录超出本条资料范围。')
        if not target.exists():
            shutil.copy2(source, target)
        relative = str(target.relative_to(folder))
        if relative not in data['transcripts']:
            data['transcripts'].append(relative)
        self.save(folder, data)
        return target
