"""Local durable jobs, subscriptions, and a portable source archive."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from dataclasses import asdict
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

import formats
import i18n                 # (1.7.0) the labels of the files written for the owner (I18N.md 8.8)
import paths
import procutil
from urls import identity   # moved to urls.py unchanged; kept here for importers

# Kept for importers; Library() without a path resolves its file when it is created.
CATALOG = paths.data_dir() / 'library.sqlite3'
WRITE_CHECK_TIMEOUT = 2   # seconds open_library waits for another writer before it stops checking
_log = logging.getLogger('YtDlpDesktop.library')

# --- (1.7.0 U1) the busy timeout of the window's own thread ----------------------------------
# Worker threads, the collector process and every other caller wait up to WORKER_TIMEOUT for a
# lock held by another connection, as before.  The thread that runs the window (set_ui_thread)
# never waits that long: another program holding library.sqlite3 (1.5.1 at the same time, a DB
# browser with an unsaved edit, a backup tool) froze the window for 22 to 28 s per call.  On that
# thread a public Library method tries with SQLite's busy timeout 0 and repeats the whole call
# (each method is one transaction, or ends in a state another call may repeat) for up to UI_WAIT
# seconds, enough for this program's own short transactions; then it raises the "database is
# locked" error, which the window turns into a calm notice and a later retry (library_ui).  After
# such an error the next calls of that thread try once only, for UI_QUIET seconds.  On Windows one
# try against an exclusive lock costs about 30 ms, so a call waits about 0.15 s at most.
WORKER_TIMEOUT = 20       # s
UI_WAIT = 0.12            # s: how long the window's thread repeats a call that met a lock
UI_PAUSE = 0.015          # s between those tries
UI_QUIET = 2.0            # s: after a call gave up, the next ones try once only
UI_DIRECT_TIMEOUT = 0.05  # s: db()/tx() used directly on the window's thread (not through a method)
FIND_TIMEOUT = 2.0        # s: the Runner's find_download on its worker thread (worker_wait)
_ui_thread = None         # threading.get_ident() of the window's thread; None in other processes
_ui_quiet_until = 0.0     # time.monotonic() until which the window's thread does not repeat calls
_local = threading.local()


def set_ui_thread(ident=None):
    """Name the thread that runs the window (default: the calling thread)."""
    global _ui_thread
    _ui_thread = threading.get_ident() if ident is None else ident


def on_ui_thread():
    return _ui_thread is not None and threading.get_ident() == _ui_thread


def is_busy(error):
    """True for SQLite's "database is locked" (SQLITE_BUSY or SQLITE_LOCKED): another connection
    holds the lock; trying again later can succeed.  Other errors are not busy."""
    if not isinstance(error, sqlite3.OperationalError):
        return False
    code = getattr(error, 'sqlite_errorcode', None)
    if isinstance(code, int):
        return code & 0xFF in (5, 6)
    text = str(error).lower()
    return 'database is locked' in text or 'database table is locked' in text or 'database is busy' in text


@contextmanager
def worker_wait(seconds):
    """(1.7.0 fix round 1) Connections opened by the calling thread (not the window's) inside this
    block wait at most ``seconds`` for a lock instead of WORKER_TIMEOUT: for a read whose failure
    the caller already handles (the Runner's "already downloaded" check, find_download, which then
    downloads as usual), so that another program's lock does not hold the download's start."""
    previous = getattr(_local, 'timeout', None)
    _local.timeout = seconds
    try:
        yield
    finally:
        _local.timeout = previous


def _timeout():
    """The busy timeout for a connection opened by the calling thread."""
    if not on_ui_thread():
        override = getattr(_local, 'timeout', None)
        return WORKER_TIMEOUT if override is None else override
    return 0 if getattr(_local, 'depth', 0) else UI_DIRECT_TIMEOUT


@contextmanager
def ui_wait(seconds):
    """(1.7.0 fix round 1) The Library calls of the window's thread inside this block repeat a call
    that met a lock for at most ``seconds`` instead of UI_WAIT: for a call whose busy result the
    caller handles calmly anyway (a write the HistoryWriter defers, a read whose failure keeps what
    is shown), so that a lock held by another program costs the window one try, not four.  0 = try
    once.  Other threads: no effect."""
    previous = getattr(_local, 'wait', None)
    _local.wait = seconds
    try:
        yield
    finally:
        _local.wait = previous


def _ui_retrying(method):
    """Wrap a public Library method: on the window's thread a call that meets a lock is repeated
    (busy timeout 0) for up to UI_WAIT seconds (or what ui_wait() names), then it raises.  Other
    threads: unchanged."""
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        global _ui_quiet_until
        if not on_ui_thread() or getattr(_local, 'depth', 0):
            return method(self, *args, **kwargs)
        _local.depth = 1
        try:
            now = time.monotonic()
            wait = getattr(_local, 'wait', None)
            deadline = now if now < _ui_quiet_until else now + (UI_WAIT if wait is None else wait)
            while True:
                try:
                    result = method(self, *args, **kwargs)
                except sqlite3.OperationalError as error:
                    if not is_busy(error):
                        raise
                    if time.monotonic() >= deadline:
                        _ui_quiet_until = time.monotonic() + UI_QUIET
                        raise
                    time.sleep(UI_PAUSE)
                    continue
                _ui_quiet_until = 0.0
                return result
        finally:
            _local.depth = 0
    call.ui_retrying = True
    return call
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


# (final fix round 3, SWEEP-3) The app-made page and link titles of the collector (the fallback 网页,
# 媒体直链, 动态页面媒体请求; library_ui.PAGE_TITLE_KEYS has the same value) are written in the
# running language into the files for the owner (网页正文.txt / .md, 阅读说明.txt; I18N.md 8.8, 9.3);
# the record keeps the stored title, and any other title (user data) is written as it is.
PAGE_TITLE_KEYS = ('value.page_title.', 'value.link_kind.direct', 'value.link_kind.dynamic_request')


def _shown_title(title):
    return i18n.localize(title, keys=PAGE_TITLE_KEYS) if isinstance(title, str) else title


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


SCHEMA = '''
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
'''
HISTORY_PAGE = 300        # jobs per page of the history list (1.7.0 U2: more are loaded on request)


def _like(text):
    """A LIKE pattern (ESCAPE '\\') that finds ``text`` anywhere."""
    return '%' + re.sub(r'([\\%_])', r'\\\1', str(text)) + '%'


# (1.7.0 U2) The history search: the job's title or one of its links contains the text.  A
# record that is not valid JSON is found by its title only (json_each would stop the query).
_HISTORY_MATCH = ('''(title LIKE :pattern ESCAPE '\\' OR CASE WHEN json_valid(data) THEN EXISTS(
    SELECT 1 FROM json_each(data, '$.options.urls') WHERE json_each.value LIKE :pattern ESCAPE '\\')
    ELSE 0 END)''')


class Library:
    def __init__(self, path=None):
        self.path = Path(path or paths.catalog_path()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False
        try:
            with self.db():
                pass
        except sqlite3.OperationalError as error:
            # (1.7.0 U1) On the window's thread a lock held by another program does not stop the
            # start: the file exists and is usable; the tables are made by the first call that
            # gets the lock (_ensure_schema).
            if not (on_ui_thread() and is_busy(error)):
                raise
            _log.warning('the history is locked by another program at start-up (%s); retried later', error)

    def _ensure_schema(self, db):
        if not self._schema_ready:
            db.executescript(SCHEMA)
            self._schema_ready = True

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=_timeout())
        db.row_factory = sqlite3.Row
        try:
            self._ensure_schema(db)
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
        db = sqlite3.connect(self.path, timeout=_timeout())
        db.row_factory = sqlite3.Row
        try:
            self._ensure_schema(db)
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

    def history(self, limit=HISTORY_PAGE, search=''):
        """The newest ``limit`` jobs, newest first; with ``search``, only the jobs whose title or
        one of whose links contains it (1.7.0 U2: the database filters, the list pages).

        A job still marked running whose process is gone is marked interrupted.  When that write
        meets a lock (another program holds the file), the row is shown interrupted anyway and
        the next history() writes it (1.7.0 U1)."""
        search = str(search or '').strip()
        with self.db() as db:
            if search:
                query = f'SELECT * FROM jobs WHERE {_HISTORY_MATCH} ORDER BY updated DESC LIMIT :limit'
                rows = [dict(r) for r in db.execute(query, {'pattern': _like(search), 'limit': limit})]
            else:
                rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY updated DESC LIMIT ?', (limit,))]
        for row in rows:
            try:
                data = json.loads(row['data'])
            except (ValueError, TypeError):
                data = None
            if not isinstance(data, dict):
                # (1.7.0 fix round 1) A record that is not a JSON object (never written by this
                # program) is listed by its title with empty data, as the search's json_valid guard
                # intends; its status is shown as stored and nothing is written to it.
                row['data'] = {}
                continue
            row['data'] = _read_record(data)
            if row['status'] == 'running' and not alive(row['data'].get('worker') or row['data'].get('owner')):
                try:
                    self.change_job(row['id'], status='interrupted')
                except sqlite3.OperationalError as error:
                    if not is_busy(error):
                        raise
                row['status'] = 'interrupted'
        return rows

    def history_count(self, search=''):
        """How many jobs history(search=search) could list without a limit."""
        search = str(search or '').strip()
        with self.db() as db:
            if search:
                return db.execute(f'SELECT count(*) FROM jobs WHERE {_HISTORY_MATCH}',
                                  {'pattern': _like(search)}).fetchone()[0]
            return db.execute('SELECT count(*) FROM jobs').fetchone()[0]

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
                entries = value.get('items')
                if isinstance(entries, list) and entries:
                    # (1.7.0, q-minor-2) One entry per queued item, in queue order: the same link in
                    # two formats keeps both (a link plus a variant), as when each 加入队列 is recorded
                    # on its own.  'overrides' is keyed by link and keeps the last format only.
                    for entry in entries:
                        if isinstance(entry, dict) and isinstance(entry.get('url'), str):
                            own = entry.get('options')
                            self._extend(data, [entry['url']], {entry['url']: own} if isinstance(own, dict) else None)
                else:
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


# (1.7.0 U1) Every public method that opens the database repeats itself briefly on the window's
# thread instead of waiting WORKER_TIMEOUT there (_ui_retrying); other threads are not affected.
UI_RETRIED = ('create_job', 'job', 'change_job', 'collection_event', 'history', 'history_count', 'extend_job',
              'rename_job', 'record_download', 'find_download', 'download_event', 'subscription',
              'subscriptions', 'configure_subscription', 'finish_subscription', 'pending', 'downloaded',
              'archive_index', 'archives', 'contexts')
for _name in UI_RETRIED:
    setattr(Library, _name, _ui_retrying(getattr(Library, _name)))
del _name


def _prove_writable(path) -> None:
    """Raise sqlite3.Error unless this database can be written.

    Opening proves nothing: CREATE TABLE IF NOT EXISTS writes nothing when the tables
    exist, so a database in a write-protected folder opens without an error and fails at
    the first real write.  Here a write transaction is started and a page is changed,
    which makes SQLite open its journal file beside the database; then everything is
    rolled back, so the file itself stays as it was.  A database that is only busy
    (another writer is at work right now) counts as writable.
    """
    # (1.7.0 U1) The window's thread does not wait 2 s for a lock here either: busy counts as writable.
    db = sqlite3.connect(path, timeout=UI_DIRECT_TIMEOUT if on_ui_thread() else WRITE_CHECK_TIMEOUT,
                         isolation_level=None)
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
            # (1.7.0) The labels are in the running language (zh: the 1.6.0 bytes); the file names stay (D10).
            note = i18n.t('filetext.truncated_source') if page.get('text_truncated') else ''
            for filename, body in [('网页正文.txt', page.get('text', '')), ('网页正文.md', page.get('markdown', ''))]:
                (folder / filename).write_text(i18n.t('filetext.page_text', title=_shown_title(page['title']),
                                                      url=page['url'],
                                                      note=note, body=body), encoding='utf-8-sig')
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
        # (1.7.0) The readme's labels are in the running language (zh: the 1.6.0 bytes); it is rewritten
        # on every save, so a readme of the other language is replaced the next time (I18N.md 8.8).
        (folder / '阅读说明.txt').write_text(
            i18n.t('filetext.readme_head', title=_shown_title(data['title']), url=data['url'], source=data['source'],
                   author=data.get('uploader') or i18n.t('filetext.author_unknown'))
            + data.get('description', '') + i18n.t('filetext.readme_media') + '\n'.join(data['files'])
            + i18n.t('filetext.readme_transcripts') + '\n'.join(data['transcripts']) + '\n', encoding='utf-8-sig')
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
