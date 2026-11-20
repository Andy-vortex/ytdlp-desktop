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
import messages
from messages import message_of, msg, raw, stripped
import paths
import procutil
import settings             # (2.0.0 S4) settings.SCHEDULES: the allowed subscription schedules
from urls import identity

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
    text = messages.zh(str(error)).lower()     # (2.0.0) NotV2 carries a Message
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
# Keys of a download job's options that are not options of one download.
JOB_OPTION_KEYS = ('urls', 'overrides', 'variants')

# (2.0.0, S2-DESIGN.md 1.2) The text positions ("slots") of each record kind of the format-2 store:
# a slot holds a message ({"k", "p"}, messages.to_json) or the text itself (a plain string, '' for
# none); '*' walks the items of a list or the values of a dict.  Every read and write of a record
# goes through _load / _dump with this table; nothing else in a record is read as a message.
SLOTS = {
    'job': (('errors', '*', 'error'), ('errors', '*', 'reason'), ('options', 'archive_contexts', '*', 'title')),
    'subscription': (('last_error',),),
    'page': (('title',), ('links', '*', 'title')),
    'entry': (('title',), ('page_title',)),
    'archive': (('title',), ('page_title',)),
}
STORE_VERSION = 2           # PRAGMA user_version of a format-2 library
ARCHIVE_VERSION = 2         # "version" of a 资料信息.json file (only the file has it, not archives.data)
_FOREIGN_CODES = (11, 26)   # SQLITE_CORRUPT, SQLITE_NOTADB: the file's content itself is not a library


class NotV2(sqlite3.OperationalError):
    """(2.0.0, PLAN 3.10) A database with tables whose user_version is not 2 (1.5.1-1.8.1 wrote 0, a later
    version may write 3).  An OperationalError without a busy code: Library() does not take it for a
    lock; open_library sets the file aside and starts a new library."""


def _load(text, kind):
    """A stored record: json.loads with every slot of ``kind`` read as a typed value (messages.from_json)."""
    return messages.read_slots(json.loads(text), SLOTS[kind])


def _dump(data, kind):
    """The JSON text a record is stored as: every slot of ``kind`` in its format-2 form (messages.to_json)."""
    return json.dumps(messages.write_slots(data, SLOTS[kind]), ensure_ascii=False)


def _title(text):
    """The title / name column as read: the stored text is the owner's or the source's own (Raw)."""
    return raw(text) if isinstance(text, str) else text


def _format_of(options):
    """The format key (formats.format_key) of one download's options dict, or None."""
    try:
        return formats.format_key(formats.by_id(options.get('mode')), options.get('resolution'))
    except (ValueError, TypeError, AttributeError):
        return None


def _done_pairs(data):
    """{(url, format)} of a job record's 'done' list (pairs of another shape are skipped)."""
    pairs = data.get('done') if isinstance(data.get('done'), list) else []
    found = set()
    for pair in pairs:
        if isinstance(pair, list) and len(pair) == 2:
            try:
                found.add(tuple(pair))
            except TypeError:
                continue
    return found


def job_downloads(data):
    """Every download a download job stands for, in job order: [{'url', 'format', 'options',
    'done'}].  ``data`` is a job record's data (Library.job(id)['data']).

    One entry per (link, format): the job's links with the job's options (or the link's
    own from 'overrides'), then the links that were added again in another format
    ('variants', core fix review-e2e-4).  ``options`` is a plain options dict (no job keys);
    ``done`` says whether that download finished: (link, format) is in the job's 'done' list.
    """
    data = data if isinstance(data, dict) else {}
    options = data.get('options') or {}
    if not isinstance(options, dict):
        return []
    base = {key: value for key, value in options.items() if key not in JOB_OPTION_KEYS}
    overrides = options.get('overrides') if isinstance(options.get('overrides'), dict) else {}
    finished = _done_pairs(data)
    result = []
    for url in options.get('urls') or []:
        if not isinstance(url, str):
            continue
        own = overrides.get(url)
        own = dict(own) if isinstance(own, dict) else dict(base)
        key = _format_of(own)
        result.append({'url': url, 'format': key, 'options': own, 'done': (url, key) in finished})
    for variant in options.get('variants') or []:
        if not isinstance(variant, dict) or not isinstance(variant.get('url'), str):
            continue
        own = variant.get('options')
        own = dict(own) if isinstance(own, dict) else dict(base)
        key = variant.get('format') or _format_of(own)
        result.append({'url': variant['url'], 'format': key, 'options': own,
                       'done': (variant['url'], key) in finished})
    return result


def title_parts(row) -> tuple:
    """(2.0.0, S2-DESIGN.md 1.2) What a job's history title is made of; the structure only (no display
    call): ('subscription', name) | ('multi', title, n) | ('plain', title) | ('link', url) |
    ('multi_link', url, n).  ``row``: {'kind', 'title', 'data'}, the title column holding the owner's /
    the media's own text ('' or None: none), data['via'] == 'subscription' for a job a subscription
    made, data['title_links'] the n of '<title> 等 n 条'.  library_ui.history_title shows it."""
    title = row.get('title')
    title = title if isinstance(title, str) else ''
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    if data.get('via') == 'subscription':
        return ('subscription', title)
    if row.get('kind') != 'download':
        return ('plain', title)
    count = data.get('title_links')
    if title and isinstance(count, int) and not isinstance(count, bool) and count >= 2:
        return ('multi', title, count)
    if title:
        return ('plain', title)
    options = data.get('options') if isinstance(data.get('options'), dict) else {}
    links = options.get('urls') if isinstance(options.get('urls'), list) else []
    links = [url for url in links if isinstance(url, str) and url]
    if len(links) == 1:
        return ('link', links[0])
    if links:
        return ('multi_link', links[0], len(links))
    return ('plain', '')


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
    cut inside an emoji, which yt-dlp prints escaped) becomes '?'.  Other values unchanged.
    (1.8.0) The types are kept: a Message / Raw that cannot be written is rebuilt with the
    repaired text (a Message keeps its key, its params repaired the same way)."""
    if isinstance(value, str):
        try:
            value.encode('utf-8')
            return value
        except UnicodeEncodeError:
            text = messages.zh(value).encode('utf-8', 'replace').decode('utf-8')
        if isinstance(value, messages.Message):
            params = {name: _storable(item) for name, item in value.params.items()}
            return messages.Message(text, value.key, params)
        return raw(text) if isinstance(value, messages.Raw) else text
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
        """(2.0.0) The first connection of this object: a file without any table gets the tables and
        user_version 2 in one transaction (an exit half-way leaves no table behind); a file with tables
        must have user_version 2, else NotV2 (2.0.0 reads no other format)."""
        if self._schema_ready:
            return
        if db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone() is None:
            db.executescript(f'BEGIN; {SCHEMA} PRAGMA user_version = {STORE_VERSION}; COMMIT;')
        elif int(db.execute('PRAGMA user_version').fetchone()[0]) != STORE_VERSION:
            raise NotV2(msg('core.library.not_v2'))
        else:
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

    def create_job(self, kind, title, options, subscription=None, via=None):
        """A new job record (S2-DESIGN.md 1.2).  ``title``: the owner's / the source's own text ('' for
        none: the history names the job after its links); ``via='subscription'`` for the job of a
        subscription check (its title is the subscription's name, '' for the untitled one)."""
        options = dict(options)
        options['proxy'] = ''  # Login/proxy secrets never enter persisted settings.
        options.pop('resume', None)
        options.pop('job_id', None)
        options.pop('history_path', None)
        job = uuid.uuid4().hex
        data = dict(options=options, subscription=subscription, checkpoint=None, errors=[],
                    owner=os.getpid(), worker=None, done=[], files=[])
        if via:
            data['via'] = via
        title = messages.zh(title) if isinstance(title, str) else ''
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job, kind, title, 'running', time.time(),
                                                                 _dump(data, 'job')))
        return job

    def job(self, job):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
            if not row:
                raise ValueError(msg('core.library.no_job'))
            result = dict(row)
            result['data'] = _load(row['data'], 'job')
            result['title'] = _title(row['title'])
            result['pages'] = [_load(r[0], 'page')
                               for r in db.execute('SELECT data FROM pages WHERE job=? ORDER BY rowid', (job,))]
            return result

    def change_job(self, job, status=None, **updates):
        with self.tx() as db:
            row = db.execute('SELECT data,status FROM jobs WHERE id=?', (job,)).fetchone()
            data = _load(row[0], 'job')
            data.update(updates)
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (_dump(data, 'job'), status or row[1], time.time(), job))

    def collection_event(self, job, kind, value):
        # Page and its frontier commit together, inside the worker BEFORE GUI notification.
        with self.tx() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()
            data = _load(row[0], 'job')
            if kind == 'collect_page':
                page = dict(value)
                data['checkpoint'] = page.pop('_checkpoint')
                db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)', (job, page['url'], _dump(page, 'page')))
            elif kind == 'collect_checkpoint':
                data['checkpoint'] = value
            elif kind == 'collect_error':
                data['errors'].append({'error': value, 'reason': None})
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (_dump(data, 'job'), time.time(), job))

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
                row['title'] = _title(row['title'])
                continue
            row['data'] = messages.read_slots(data, SLOTS['job'])
            row['title'] = _title(row['title'])
            if row['status'] == 'running' and not alive(row['data'].get('worker') or row['data'].get('owner')):
                try:
                    # A read-modify-write of its own: the row read for display is never written.
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
        base = {key: value for key, value in options.items() if key not in JOB_OPTION_KEYS}
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
    def _mark_done(data, url, format):
        """(2.0.0) Remember a finished download as [link, format] in the job's 'done' list (once;
        format None when it is not known)."""
        if not isinstance(url, str) or not url:
            return
        format = format if isinstance(format, str) and format else None
        pairs = data.setdefault('done', [])
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
            data = _load(row[0], 'job')
            self._extend(data, urls, overrides)
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (_dump(data, 'job'), time.time(), job))

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
            row = db.execute('SELECT data,status,title FROM jobs WHERE id=?', (job,)).fetchone()
            data, status = _load(row[0], 'job'), row[1]
            if kind == 'item':
                data['current'] = value['url']
                data['current_key'] = value.get('key')
                data['current_format'] = value.get('format')
                data['current_origin'] = value.get('origin')
                data['current_multi'] = bool(value.get('multi'))
            elif kind == 'title':
                # The first title names the job, so the history shows a song instead of a link.
                # (2.0.0, S2-DESIGN.md 1.2) The title column gets the media's own text; with several
                # links, title_links keeps how many the job had at that moment ('<title> 等 n 条').
                if isinstance(value, str) and messages.zh(value).strip() and not row['title']:
                    count = len((data.get('options') or {}).get('urls') or [])
                    db.execute('UPDATE jobs SET title=? WHERE id=?', (messages.zh(value), job))
                    if count > 1:
                        data['title_links'] = count
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
                        self._mark_done(data, url, value.get('format') or self._sole_format(data, url))
                        for key in dict.fromkeys(identity(link) for link in (url, value.get('origin')) if link):
                            db.execute('UPDATE entries SET pending=0 WHERE key=?', (key,))
                elif value['success'] and data.get('current'):
                    url = data['current']
                    self._mark_done(data, url, value.get('format') or data.get('current_format')
                                    or self._sole_format(data, url))
                    for key in dict.fromkeys(identity(link) for link in (url, data.get('current_origin')) if link):
                        db.execute('UPDATE entries SET pending=0 WHERE key=?', (key,))
                if value.get('error'):
                    # (1.8.0, C-F34) The error keeps its type (Message / Raw / plain text).  (2.0.0) With
                    # it the short reason the task row showed for that line, which the history note
                    # shows (polish ui-wording-consistency-5); None when there was none.
                    error = value['error']
                    reason = value.get('reason')
                    reason = stripped(reason) if isinstance(reason, str) and messages.zh(reason).strip() else None
                    error = error if isinstance(error, str) else raw(str(error))
                    data.setdefault('errors', []).append({'error': error, 'reason': reason})
                    data['errors'] = data['errors'][-20:]
            elif kind == 'done':
                # (2.0.0) engine_error is stored as a flag (its only reader asks whether there was one)
                data['result'] = dict(value, engine_error=bool(value.get('engine_error'))) \
                    if isinstance(value, dict) and 'engine_error' in value else value
                # Not completed while anything is left: a failure, an archive error, a bot-check
                # block, an engine that could not run (its links are stored as unstarted), or
                # any other link of this run that did not finish.
                status = 'interrupted' if value['cancelled'] else (
                    'partial' if (value['failed'] or value.get('archive_errors') or value.get('blocked')
                                  or value.get('engine_error') or value.get('pending_urls')) else 'completed')
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (_dump(data, 'job'), status, time.time(), job))

    def subscription(self, name, options, interval=3600, media_only=True, first_new=True):
        if interval not in settings.SCHEDULES:
            raise ValueError(msg('core.library.bad_interval'))
        options = dict(options)
        options['proxy'] = ''
        key = uuid.uuid4().hex
        data = dict(options=options, media_only=media_only, first_new=first_new,
                    initialized=False, last_check=None, last_error='', last_new=0)
        # (2.0.0) The name column holds the name the owner typed ('' for none: shown as the untitled one).
        name = messages.zh(name).strip()[:100]
        with self.db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?)',
                       (key, name, 1, interval, time.time(), _dump(data, 'subscription')))
        return key

    def subscriptions(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM subscriptions ORDER BY rowid')]
            for row in rows:
                # (2.0.0) 'name': the name to show (the owner's text, or the untitled message);
                # 'name_text': the stored text ('' for an untitled subscription)
                row['data'] = _load(row['data'], 'subscription')
                row['name_text'] = row['name'] if isinstance(row['name'], str) else ''
                row['name'] = raw(row['name_text']) or msg('value.subscription.untitled')
                row['pending'] = db.execute('SELECT count(*) FROM entries WHERE subscription=? AND pending=1', (row['id'],)).fetchone()[0]
            return rows

    def configure_subscription(self, key, enabled, interval):
        if interval not in settings.SCHEDULES:
            raise ValueError(msg('core.library.bad_interval'))
        with self.db() as db:
            db.execute('UPDATE subscriptions SET enabled=?,interval=?,next_check=? WHERE id=?', (int(enabled), interval, time.time() + interval, key))

    def rename_subscription(self, key, name):
        """(1.8.1) The name column, in one transaction.  The owner's text, cut to 100 characters; an
        empty name is the untitled one (as subscription()).  The record is not touched.  True when the
        subscription exists."""
        name = messages.zh(str(name)).strip()[:100]
        with self.tx() as db:
            return db.execute('UPDATE subscriptions SET name=? WHERE id=?', (name, key)).rowcount > 0

    def delete_subscription(self, key):
        """(1.8.1) Remove a subscription and its entries, in one transaction.  The collection jobs of its
        checks stay in the history; their record forgets the subscription (data['subscription'] =
        None; every other value keeps its bytes): a resumed
        check never looks for it (1.5.1 to 1.8.0 read that id and their finish_subscription has no
        None check; 1.5.1 would stay busy).  jobs.updated is kept, so the history order stays.
        Downloads, archives and the job history are not touched.  True when the subscription existed."""
        if not isinstance(key, str) or not key:
            return False
        with self.tx() as db:
            found = db.execute('DELETE FROM subscriptions WHERE id=?', (key,)).rowcount
            db.execute('DELETE FROM entries WHERE subscription=?', (key,))
            # The id is 32 ASCII hex digits: no JSON escape hides it from instr; each hit is compared exactly.
            rows = db.execute("SELECT id,data FROM jobs WHERE kind='collect' AND instr(data, ?) > 0",
                              (key,)).fetchall()
            for row in rows:
                try:
                    stored = json.loads(row['data'])
                except (TypeError, ValueError):
                    continue
                if not isinstance(stored, dict) or stored.get('subscription') != key:
                    continue
                stored['subscription'] = None
                db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(stored, ensure_ascii=False), row['id']))
        return bool(found)

    def finish_subscription(self, key, pages, successful, error=''):
        with self.tx() as db:
            row = db.execute('SELECT * FROM subscriptions WHERE id=?', (key,)).fetchone()
            if row is None:
                return 0        # (1.8.1) deleted meanwhile (delete_subscription): nothing to record
            data = _load(row['data'], 'subscription')
            new = 0
            if successful:
                for page in pages:
                    for item in page['links']:
                        if data['media_only'] and item['kind'] == 'web':
                            continue
                        item_key = identity(item['url'])
                        old = db.execute('SELECT pending FROM entries WHERE subscription=? AND key=?', (key, item_key)).fetchone()
                        pending = old[0] if old else int(data['initialized'] or data['first_new'])
                        new += int(old is None and pending)
                        payload = dict(item, page_url=page['url'], page_title=page['title'])
                        db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?)', (key, item_key, pending,
                                                                                      _dump(payload, 'entry')))
                data.update(initialized=True, last_check=time.time(), last_error='', last_new=new)
            else:
                # A stopped/failed scan never establishes a baseline or consumes new items.
                data['last_error'] = error or msg('value.subscription.interrupted')
            db.execute('UPDATE subscriptions SET data=?,next_check=? WHERE id=?',
                       (_dump(data, 'subscription'), time.time() + max(900, row['interval']), key))
            return new

    def pending(self, key):
        with self.db() as db:
            return [_load(r[0], 'entry')
                    for r in db.execute('SELECT data FROM entries WHERE subscription=? AND pending=1 ORDER BY rowid', (key,))]

    def downloaded(self, url):
        with self.db() as db:
            db.execute('UPDATE entries SET pending=0 WHERE key=?', (identity(url),))

    def ignore_entries(self, key, urls):
        """(1.8.1) The owner's 忽略所选: the entries of these links (by their link key, urls.identity, as
        finish_subscription stores them) leave the pending list of this subscription the way a
        download takes them out (pending=0).  The rows stay, so a later check keeps them out
        (finish_subscription keeps an existing row's pending); other subscriptions are not touched.
        One transaction; returns how many entries changed."""
        keys = []
        for url in urls:
            if isinstance(url, str) and url:
                try:
                    keys.append(identity(url))
                except ValueError:
                    continue
        keys = list(dict.fromkeys(keys))
        if not keys:
            return 0
        changed = 0
        with self.tx() as db:
            for start in range(0, len(keys), 500):      # within the 999 parameters of any SQLite
                part = keys[start:start + 500]
                changed += db.execute('UPDATE entries SET pending=0 WHERE subscription=? AND pending=1 AND key IN (%s)'
                                      % ','.join('?' * len(part)), (key, *part)).rowcount
        return changed

    def archive_index(self, folder, data):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO archives VALUES(?,?,?)', (str(folder), time.time(), _dump(data, 'archive')))

    def archives(self):
        with self.db() as db:
            return [dict(folder=r['folder'], **_load(r['data'], 'archive'))
                    for r in db.execute('SELECT * FROM archives ORDER BY updated DESC')]

    def contexts(self, urls):
        """The newest collected page context of each link: {url: {title, source, job_id, page_url}}.

        (1.8.1) The window calls this before a download with archiving on; a link that was never
        collected used to cost a read of every stored page (seconds with thousands of pages).  Now
        only pages that can hold a wanted link are read: with at most `prefilter` search texts
        the database keeps the rows whose data contains one of them (instr; each text costs one
        pass over the data column); otherwise each row is loaded and read only when one of its
        links is wanted.  The result is the same: newest page first (rowid DESC), each link
        compared exactly, done once every link is found.

        A search text is the link as json.dumps writes it in a page record (_dump: ensure_ascii=False,
        the form every version of this program wrote), and as ensure_ascii=True writes it when that
        differs (records another tool rewrote with escaped non-ASCII); a text that cannot be stored
        (a lone surrogate) is never in a row and is skipped.  A row another tool rewrote with other
        escapes (\\/, upper-case \\u hex) is missed by the instr way only; the link is then archived
        as never collected.  A broken row (not JSON, no links) on the instr way: one whose data holds
        no search text is never read, so it is skipped where the old loop raised; one that holds a
        search text raises as before.  On the row-by-row way every row reached is loaded (json.loads
        and its links), so a row that is not JSON or has no links raises as in the old loop; only
        a row that parses but holds no wanted link is not read further (its slots), so a fault that
        only that would find is not raised for it.  Rows after the last wanted link was found are
        not read on either way, as before."""
        prefilter = 4           # measured break-even of the two ways (1.8.1 design 1.1)
        remaining, result = set(urls), {}
        if not remaining:
            return result
        needles = {}
        for url in remaining:
            if not isinstance(url, str):
                continue
            for ascii_only in (False, True):
                text = json.dumps(url, ensure_ascii=ascii_only)
                try:
                    text.encode('utf-8')
                except UnicodeEncodeError:
                    continue
                needles[text] = None
        needles = list(needles)
        screen = len(needles) > prefilter or any(not isinstance(url, str) for url in remaining)
        with self.db() as db:
            if screen:
                rows = db.execute('SELECT job,url,data FROM pages ORDER BY rowid DESC')
            elif needles:
                rows = db.execute('SELECT job,url,data FROM pages WHERE '
                                  + ' OR '.join(['instr(data, ?) > 0'] * len(needles))
                                  + ' ORDER BY rowid DESC', needles)
            else:
                return result
            for row in rows:
                if not remaining:
                    break
                stored = json.loads(row['data'])
                if screen and not any(isinstance(item, dict) and item.get('url') in remaining
                                      for item in stored['links']):
                    continue
                page = messages.read_slots(stored, SLOTS['page'])
                for item in page['links']:
                    if item['url'] in remaining:
                        result[item['url']] = dict(title=item['title'], source=item['source'], job_id=row['job'],
                                                   page_url=page['url'])
                        remaining.remove(item['url'])
        return result


# (1.7.0 U1) Every public method that opens the database repeats itself briefly on the window's
# thread instead of waiting WORKER_TIMEOUT there (_ui_retrying); other threads are not affected.
UI_RETRIED = ('create_job', 'job', 'change_job', 'collection_event', 'history', 'history_count', 'extend_job',
              'record_download', 'find_download', 'download_event', 'subscription',
              'subscriptions', 'configure_subscription', 'finish_subscription', 'pending', 'downloaded',
              'archive_index', 'archives', 'contexts',
              'ignore_entries', 'delete_subscription', 'rename_subscription')
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


def _opened(path):
    """(Library, '') of ``path`` (None: paths.catalog_path()), proven writable."""
    store = Library(path)
    _prove_writable(store.path)
    beside = paths.data_dir() / 'library.sqlite3'
    if path is None and beside.is_file() and store.path != beside.resolve():
        _log.warning('the history beside the program cannot be written (%s); this session uses %s',
                     beside, store.path)
    return store, ''


def _temporary(error):
    """(Library, warning): a throw-away database in the temp folder for this session."""
    folder = Path(tempfile.mkdtemp(prefix='ytdlp-history-'))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return Library(folder / 'library.sqlite3'), msg('core.library.temporary', error=message_of(error))


def _foreign(error) -> bool:
    """Is the file itself not a library of this version: NotV2, or SQLite's "not a database" /
    "malformed" (never a lock, a missing right or an IO error: those leave the file alone)?"""
    if isinstance(error, NotV2):
        return True
    return type(error) is sqlite3.DatabaseError and \
        (getattr(error, 'sqlite_errorcode', None) or 0) & 0xFF in _FOREIGN_CODES


def _keep_aside(file) -> Path:
    """Rename a library that is not of this version to library.bad-<time>.sqlite3 (never replacing a
    file).  OSError when it cannot be: a -wal file is there (part of the content would stay behind), the
    file is in use (also by a connection in a transaction: Windows refuses the rename), every name is
    taken.  A hot -journal was already rolled back by SQLite when _opened read the file; a journal SQLite
    does not take for hot holds nothing of the file and does not stop the rename (S2 review F4)."""
    if file.with_name(file.name + '-wal').exists():
        raise OSError(msg('core.library.cannot_set_aside', path=os.fspath(file)))
    target = paths.set_aside(file, 'bad')
    if target is None:
        raise OSError(msg('core.library.cannot_set_aside', path=os.fspath(file)))
    return target


def open_library(path=None):
    """(Library, warning).  The history is optional: when its file cannot be opened or
    cannot be written, a throw-away database in the temp folder serves this session and
    the warning says so.  (2.0.0, PLAN 3.10) A file that is not a library of this version (NotV2,
    damaged) is renamed to library.bad-<time>.sqlite3 in its folder and a new library starts in
    its place; the warning (core.library.kept_aside) says so."""
    try:
        return _opened(path)
    except (OSError, sqlite3.Error) as error:
        if not _foreign(error):
            return _temporary(error)
        file = Path(path or paths.catalog_path()).resolve()
        try:
            target = _keep_aside(file)
        except OSError as failure:
            return _temporary(failure)      # the file stays as it is; the next start tries again
        kept = msg('core.library.kept_aside', problem=message_of(error), target=target.name)
        try:
            store, _ = _opened(file)        # the same place, not catalog_path() again (data\ stays)
            return store, kept
        except (OSError, sqlite3.Error) as again:
            store, _ = _temporary(again)
            return store, msg('core.library.temporary', error=kept)


def _archive_record(data):
    """资料信息.json as read: a version-2 record (files and transcripts lists) without its version key,
    its slots typed (messages.from_json).  ValueError (core.archive.not_v2) for anything else."""
    if not (isinstance(data, dict) and data.get('version') == ARCHIVE_VERSION
            and isinstance(data.get('files'), list) and isinstance(data.get('transcripts'), list)):
        raise ValueError(msg('core.archive.not_v2'))
    return messages.read_slots({key: value for key, value in data.items() if key != 'version'}, SLOTS['archive'])


class Archive:
    def __init__(self, root, library=None, log=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = library
        self.log = log          # (2.0.0) gets the line of a record set aside (the log page, download.log)

    def prepare(self, url, context=None):
        context = context or {}
        source = context.get('source') or url
        host = urlsplit(source).hostname or '来源'
        source_key = hashlib.sha256(identity(source).encode()).hexdigest()[:8]
        item_key = hashlib.sha256(identity(url).encode()).hexdigest()[:16]
        # Stable ID paths survive title changes and retries, and avoid duplicate media.
        folder = self.root / (safe_name(host, 45) + '_' + source_key) / item_key
        if not folder.resolve().is_relative_to(self.root):
            raise ValueError(msg('core.archive.outside_root'))
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / '资料信息.json'
        try:
            data = _archive_record(json.loads(paths.read_text(record)))
        except FileNotFoundError:
            data = None
        except ValueError:
            # Unreadable (cut off, edited by hand) or not version 2 (an older version wrote it): kept
            # aside as 资料信息.bad.json and rebuilt, so that the link can be archived again.  The media
            # files in the folder stay.
            target = self._set_aside(record)
            if self.log:
                self.log(msg('core.archive.kept_aside', target=target.name, folder=os.fspath(folder)))
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
                (folder / filename).write_text(i18n.t('filetext.page_text', title=messages.show(page['title']),
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
        raise OSError(msg('core.archive.cannot_set_aside', record=os.fspath(record)))

    def save(self, folder, data):
        folder = Path(folder).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError(msg('core.archive.outside_root'))
        write_json(folder / '资料信息.json', dict(messages.write_slots(data, SLOTS['archive']), version=ARCHIVE_VERSION))
        # (1.7.0) The readme's labels are in the running language (zh: the 1.6.0 bytes); it is rewritten
        # on every save, so a readme of the other language is replaced the next time (I18N.md 8.8).
        (folder / '阅读说明.txt').write_text(
            i18n.t('filetext.readme_head', title=messages.show(data['title']), url=data['url'], source=data['source'],
                   author=data.get('uploader') or i18n.t('filetext.author_unknown'))
            + data.get('description', '') + i18n.t('filetext.readme_media') + '\n'.join(data['files'])
            + i18n.t('filetext.readme_transcripts') + '\n'.join(data['transcripts']) + '\n', encoding='utf-8-sig')
        if self.library:
            self.library.archive_index(folder, data)

    def media(self, folder, metadata):
        folder = Path(folder).resolve()
        path = Path(metadata['filepath']).resolve()
        if not path.is_relative_to(folder) or not folder.is_relative_to(self.root) or not path.is_file():
            raise ValueError(msg('core.archive.media_mismatch'))
        data = _archive_record(json.loads(paths.read_text(folder / '资料信息.json')))
        found = {key: metadata[key] for key in ('id', 'title', 'uploader', 'description', 'webpage_url', 'upload_date') if metadata.get(key) is not None}
        if isinstance(found.get('title'), str):
            found['title'] = raw(found['title'])      # (1.8.0) the media's own title is raw text (P5)
        data.update(found)
        relative = str(path.relative_to(folder))
        if relative not in data['files']:
            data['files'].append(relative)
        data['downloaded'] = time.time()
        self.save(folder, data)

    def attach(self, folder, source):
        folder, source = Path(folder).resolve(), Path(source).resolve()
        if not folder.is_relative_to(self.root) or source.suffix.lower() not in ('.txt', '.srt', '.md', '.vtt'):
            raise ValueError(msg('core.archive.transcript_type'))
        data = _archive_record(json.loads(paths.read_text(folder / '资料信息.json')))
        target = folder / '转写稿' / safe_name(source.stem, 75)
        target = target.with_name(target.name + '_' + hashlib.sha256(source.read_bytes()).hexdigest()[:10] + source.suffix.lower())
        target.parent.mkdir(exist_ok=True)
        if not target.resolve().is_relative_to(folder):
            raise ValueError(msg('core.archive.transcript_outside'))
        if not target.exists():
            shutil.copy2(source, target)
        relative = str(target.relative_to(folder))
        if relative not in data['transcripts']:
            data['transcripts'].append(relative)
        self.save(folder, data)
        return target
