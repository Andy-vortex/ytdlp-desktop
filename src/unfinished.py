"""The unfinished list, failed-downloads.json, as plain dicts (format 2; 2.0.0 S2-DESIGN.md 1.3).

    {"version": 2, "entries": [{"key", "format", "url", "title", "reason", "error", "time", "referer", "options"}]}

Imports paths, urls and formats; never engine.  An entry is one download that did not finish: ``key`` is
urls.link_key of its link, ``format`` formats.format_key, ``reason`` one of failed | stopped | unstarted |
blocked, ``error`` a message ({"k", "p"}, messages.to_json) or the text itself, ``options`` the Options
data it ran with (never the proxy or the cookies path, without the per-link maps; values equal to
OPTION_DEFAULTS are left out in the file and put back when it is read).  Compact JSON, written atomically
through one path (_write), kept below SAFE_SIZE (the oldest entries go to an overflow file).  A file
that exists but cannot be read completely is renamed to failed-downloads.bad-<time>.json before a new
list is written.  A file that is not version 2 (1.5.1-1.8.1 wrote others) is such a file: its format
cannot be recognised (PLAN 3.10); load_at_start sets it aside when the window starts.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import formats
import messages
import paths
import urls

VERSION = 2
MAX_SIZE = 5 * 1024 * 1024          # what is read at most
SAFE_SIZE = 4 * 1024 * 1024         # what is written at most (bytes of the whole file)
# (2.0.0) The text positions of an entry (library.SLOTS): a message ({"k", "p"}) or the text itself.
SLOTS = (('error',),)
_log = logging.getLogger('YtDlpDesktop.unfinished')
# Per thread: the report callback that load_for_merge hands to the save of the same merge,
# and the file it has just read completely (so that save does not read it a second time).
_local = threading.local()
REASONS = ('failed', 'stopped', 'unstarted', 'blocked')
FINISHED = ('done', 'skipped')
PER_LINK = ('referers', 'archive_contexts')   # option keys that are not part of an entry's options
SESSION = ('proxy', 'cookies')                # option keys that are never written with a value
# The defaults of engine.Options (the tests compare the two).  An entry's options are written
# without the values that equal these (output and mode are always written) and get them back
# when the list is read: a long list stays small, and Options.from_dict reads either form.
OPTION_DEFAULTS = {'mode': formats.MODES[0], 'resolution': formats.BEST, 'playlist': False, 'proxy': '',
                   'batch_safe': True, 'archive_root': '', 'cookies': '', 'naming': 'title',
                   'embed_metadata': True, 'embed_cover': True, 'subtitles': False, 'skip_downloaded': False}
ALWAYS_WRITTEN = ('output', 'mode')
# A read is repeated for up to this many seconds while another program holds the file with an
# exclusive handle (a virus scanner, a backup tool): such a list is not "unreadable".
READ_RETRY = 1.0
# Read-change-write of the list from two threads (the worker's merge, a click on "remove")
# takes turns:  with unfinished.LOCK: entries = load(); ...; save(entries)
LOCK = threading.RLock()


def _options(options_dict) -> dict:
    """An entry's options: a copy without the per-link maps and with blank session values."""
    data = {key: value for key, value in dict(options_dict).items() if key not in PER_LINK}
    for key in SESSION:
        if key in data:
            data[key] = ''
    return data


def _is_default(key, value) -> bool:
    default = OPTION_DEFAULTS.get(key, ...)
    return default is not ... and type(value) is type(default) and value == default


def _written_options(options) -> dict:
    """An entry's options as they are written: without the values that equal OPTION_DEFAULTS
    (output and mode always stay)."""
    return {key: value for key, value in options.items() if key in ALWAYS_WRITTEN or not _is_default(key, value)}


def _full_options(options) -> dict:
    """An entry's options as they are used: every default that was left out is put back."""
    data = dict(options)
    for key, value in OPTION_DEFAULTS.items():
        data.setdefault(key, value)
    return data


def _text(value) -> str:
    return value if isinstance(value, str) else ''


def _clean(raw):
    """One stored entry with every key present, or None when it cannot be used."""
    if not isinstance(raw, dict):
        return None
    if not all(isinstance(raw.get(name), str) and raw[name] for name in ('key', 'format', 'url')):
        return None
    if not isinstance(raw.get('options'), dict):
        return None
    stamp = raw.get('time')
    # (2.0.0) A copy of raw with its error typed: the stored form through messages.from_json; an entry
    # made in this session (merge, entry) already holds the Message itself, which stays as it is.
    error = raw.get('error')
    result = dict(raw) if isinstance(error, messages.Message) else messages.read_slots(dict(raw), SLOTS)
    result.update(title=_text(raw.get('title')), error=_text(result.get('error')), referer=_text(raw.get('referer')),
                  reason=raw['reason'] if raw.get('reason') in REASONS else 'failed',
                  time=float(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else 0.0,
                  options=_full_options(_options(raw['options'])))
    return result


def _missing(error) -> bool:
    """Is this OSError of stat/open 'there is no such file' (also: its folder does not exist)?"""
    return isinstance(error, (FileNotFoundError, NotADirectoryError))


def _read_text(path) -> str:
    """paths.read_text, repeated for up to READ_RETRY seconds while the file is held by another
    program (PermissionError: a sharing violation); then that error is raised."""
    deadline = time.monotonic() + READ_RETRY
    while True:
        try:
            return paths.read_text(path)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(paths.REPLACE_PAUSE)


def _read(path):
    """(entries, problem, stamp) of the list file.

    ``problem`` is '' when there is no file or when every part of it was understood, else a
    short reason (a core.unfinished.problem.* message) why the file cannot be read completely; ``entries`` are then the
    entries that could be used (often none).  ``stamp`` identifies the file that was read
    completely (path, size, mtime), else None.  Never raises.  (2.0.0) An object that is not
    version 2 has no entries and the problem core.unfinished.problem.unknown_format.
    """
    try:
        status = path.stat()
    except OSError as error:
        return [], ('' if _missing(error) else messages.msg('core.unfinished.problem.unreadable')), None
    if status.st_size > MAX_SIZE:
        return [], messages.msg('core.unfinished.problem.too_large', size=f'{status.st_size / 1024 / 1024:.1f}'), None
    try:
        data = json.loads(_read_text(path))
    except OSError as error:
        return [], ('' if _missing(error) else messages.msg('core.unfinished.problem.unreadable')), None
    except ValueError:
        return [], messages.msg('core.unfinished.problem.damaged'), None
    stamp = (str(path), status.st_size, status.st_mtime_ns)
    try:
        if not isinstance(data, dict):
            return [], messages.msg('core.unfinished.problem.unknown_format'), None
        if data.get('version') != VERSION or not isinstance(data.get('entries'), list):
            return [], messages.msg('core.unfinished.problem.unknown_format'), None
        stored = data['entries']
        entries = [item for item in (_clean(raw) for raw in stored) if item is not None]
        dropped = len(stored) - len(entries)
        problem = messages.msg('core.unfinished.problem.dropped', dropped=dropped) if dropped else ''
        return entries, problem, (None if dropped else stamp)
    except Exception:
        return [], messages.msg('core.unfinished.problem.unknown_format'), None


def load() -> list[dict]:
    """The stored entries, oldest first.  [] when there is no list, when the file cannot
    be read or understood, and when it is larger than 5 MB.  Never raises; changes nothing
    (the merge after a run uses load_for_merge)."""
    return _read(paths.failed_file())[0]


def _report(text, log=None) -> None:
    """One Chinese line to download.log and, when given, to ``log`` (the task log page).  (1.8.0)
    ``text`` is a message: download.log gets its zh text, as in 1.7.0; ``log`` the message."""
    try:
        _log.warning('%s', text)     # (1.8.0) the plain zh text: a handler composes it
    except Exception:
        pass
    if log is not None:
        try:
            log(text)
        except Exception:
            pass


def _keep_unreadable(path, problem, log) -> None:
    """Rename the list file to failed-downloads.bad-<time>.json (a free name: nothing is ever
    replaced) and report it.  OSError when it cannot be renamed."""
    target = paths.set_aside(path, 'bad')
    if target is None:
        raise OSError(messages.msg('core.unfinished.cannot_set_aside', path=os.fspath(path)))
    _report(messages.msg('core.unfinished.kept_aside', problem=problem, target=target.name), log)


def load_at_start(log) -> list[dict]:
    """(2.0.0, PLAN 3.10) load() for the window's start.  A list file that is there but gives no entry
    (not version 2, damaged, too large, unreadable) is set aside now and reported to ``log``; a version-2
    file with some unusable entries keeps them and is left to the first merge (1.8.1).  Never raises."""
    path = paths.failed_file()
    with LOCK:
        entries, problem, _ = _read(path)
        if problem and not entries:
            try:
                _keep_unreadable(path, problem, log)
            except OSError:
                pass        # in use: the merge after the next download tries again and says so
        return entries


def load_for_merge(log=None) -> list[dict]:
    """The stored entries for the merge after a run (Runner.download's finally block):
    ``unfinished.merge(unfinished.load_for_merge(), results)``, then save().

    Unlike load(), a list file that exists but cannot be read completely is first renamed
    to failed-downloads.bad-<time>.json (nothing is overwritten) and reported in Chinese
    (download.log, and ``log(text)`` when given), so that the save which follows can never
    replace a list it could not read.  The entries that could be used are returned.
    Raises OSError when such a file cannot be set aside: then nothing may be written.
    ``log`` is also used by the save() of the same merge on this thread (overflow notice).
    """
    path = paths.failed_file()
    with LOCK:
        _local.log = log
        entries, problem, stamp = _read(path)
        _local.checked = stamp
        if problem:
            _keep_unreadable(path, problem, log)
        return entries


def _guard(path, log) -> None:
    """save() never overwrites or deletes a file it cannot read: such a file is set aside first."""
    checked, _local.checked = getattr(_local, 'checked', None), None
    try:
        status = path.stat()
    except OSError as error:
        if _missing(error):
            return
    else:
        if checked == (str(path), status.st_size, status.st_mtime_ns):
            return      # load_for_merge has just read this very file completely
    problem = _read(path)[1]
    if problem:
        _keep_unreadable(path, problem, log)


def _written(item) -> dict:
    return dict(item, options=_written_options(item['options']))


def _stored(item) -> dict:
    """An entry as it is written: _written, with its error in its format-2 form (messages.to_json)."""
    return messages.write_slots(_written(item), SLOTS)


def _encode(entries) -> str:
    """The file text: compact JSON, the entries without their default option values."""
    return json.dumps({'version': VERSION, 'entries': [_stored(item) for item in entries]},
                      ensure_ascii=False, separators=(',', ':'))


def _fit(entries):
    """(kept, moved, text): the entries that fit into SAFE_SIZE, the oldest ones that do not
    (by time, then list order), and the file text of the kept ones."""
    text = _encode(entries)
    size = len(text.encode('utf-8'))
    if size <= SAFE_SIZE:
        return entries, [], text
    order = sorted(range(len(entries)), key=lambda index: (entries[index].get('time') or 0, index))
    sizes = [len(json.dumps(_stored(item), ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1
             for item in entries]
    moved, position, excess = set(), 0, size - SAFE_SIZE
    while True:
        # Move at least the estimated excess, plus 1 % of the limit as a margin.
        target = excess + SAFE_SIZE // 100
        while target > 0 and position < len(order):
            moved.add(order[position])
            target -= sizes[order[position]]
            position += 1
        kept = [item for index, item in enumerate(entries) if index not in moved]
        text = _encode(kept)
        excess = len(text.encode('utf-8')) - SAFE_SIZE
        if excess <= 0 or position >= len(order):
            return kept, [item for index, item in enumerate(entries) if index in moved], text


def _write_overflow(path, moved, log) -> None:
    """Write the moved entries to failed-downloads.overflow-<time>.json (a free name, never
    replacing a file) before the shorter list is written.  OSError when that fails."""
    text = _encode(moved)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.overflow.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(temporary, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for target in paths.free_names(path, 'overflow'):
            if os.path.lexists(target):
                continue
            try:
                os.rename(temporary, target)
                break
            except FileExistsError:
                continue
        else:
            raise OSError(messages.msg('core.unfinished.no_overflow_name', path=os.fspath(path)))
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
    _report(messages.msg('core.unfinished.overflow', count=len(moved), target=target.name), log)


def save(entries, log=None):
    """Write the list (atomically, compact JSON); returns its path.  An empty list deletes
    the file and returns None: no list, no file.  Raises OSError when the file cannot be
    written or removed.

    A file that is there but cannot be read completely is never overwritten or deleted:
    it is renamed to failed-downloads.bad-<time>.json first.  A list larger than SAFE_SIZE
    keeps its newest entries; the oldest go to failed-downloads.overflow-<time>.json.
    ``log(text)`` gets those notices (default: the one load_for_merge got on this thread).
    """
    entries = [item for item in (_clean(raw) for raw in entries) if item is not None]
    path = paths.failed_file()
    with LOCK:
        if log is None:
            log = getattr(_local, 'log', None)
        _local.log = None
        _guard(path, log)
        if not entries:
            _delete(path)
            return None
        _write(path, entries, log)
    return path


def _write(path, entries, log) -> None:
    """(2.0.0, S2-DESIGN.md 1.3) The one way the list file is written: the entries that fit into
    SAFE_SIZE (the oldest others to an overflow file first), atomically."""
    kept, moved, text = _fit(entries)
    if moved:
        _write_overflow(path, moved, log)
    paths.atomic_write_text(path, text)


def _delete(path) -> None:
    for attempt in range(paths.REPLACE_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:   # somebody is reading it this very moment
            if attempt + 1 >= paths.REPLACE_ATTEMPTS:
                raise
            time.sleep(paths.REPLACE_PAUSE)


def entry(url, options_dict, reason, *, playlist=False, error='', title='', referer='') -> dict:
    """A new entry for a link.  ValueError when the options name no known format."""
    options = _options(options_dict)
    preset = formats.by_id(options.get('mode'))
    return {'key': urls.link_key(url, playlist), 'format': formats.format_key(preset, options.get('resolution')), 'url': url,
            'title': _text(title), 'reason': reason if reason in REASONS else 'failed',
            'error': messages.cut(_text(error), 200),
            'time': time.time(), 'referer': _text(referer), 'options': options}


def signature(e) -> tuple:
    return e['key'], e['format']


def remove(entries, signatures) -> list[dict]:
    """The entries without those whose (key, format) is in ``signatures``."""
    gone = {tuple(item) for item in signatures}
    return [item for item in entries if signature(item) not in gone]


def merge(entries, results) -> list[dict]:
    """The list after a run.

    ``results``: one dict per queue item, {url, key, format, options, outcome, error, title,
    referer, media_keys}, outcome in done | skipped | failed | stopped | unstarted | blocked.

    1. A download that finished (done, or skipped because the file is already there) leaves
       the list: every entry of the same format whose key is the item's link key or one of
       its media keys ("<extractor>:<id>" as the engine reported it; never the generic
       extractor, whose ids are file names).
    2. An item that failed, was stopped, never started or was blocked is stored, replacing
       an older entry with the same (key, format) in its place; a new one goes to the end.
       When the same download also finished in this run, it is not stored.
    3. Everything else stays as it is, in its order.
    """
    results = list(results)
    finished = set()
    for result in results:
        if result.get('outcome') in FINISHED:
            keys = [result.get('key')] + [key for key in (result.get('media_keys') or ())
                                          if isinstance(key, str) and not key.startswith('generic:')]
            finished.update((key, result.get('format')) for key in keys if key)
    merged = [dict(item) for item in entries if signature(item) not in finished]
    position = {signature(item): index for index, item in enumerate(merged)}
    for result in results:
        outcome = result.get('outcome')
        if outcome not in REASONS:
            continue
        mark = (result.get('key'), result.get('format'))
        if not all(mark) or mark in finished:
            continue
        item = {'key': mark[0], 'format': mark[1], 'url': result.get('url'), 'title': _text(result.get('title')),
                'reason': outcome, 'error': messages.cut(_text(result.get('error')), 200), 'time': time.time(),
                'referer': _text(result.get('referer')), 'options': _options(result.get('options') or {})}
        if mark in position:
            older = merged[position[mark]]
            if not item['title']:
                item['title'] = _text(older.get('title'))
            merged[position[mark]] = item
        else:
            position[mark] = len(merged)
            merged.append(item)
    return merged
