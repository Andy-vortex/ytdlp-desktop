"""settings.json (1.6.0): typed load, atomic save. Imports paths only.

load() always returns every known key with a value of the right type; keys this version
does not know are kept in '_extra' and written back by save(), so another version's
values survive a round trip.  The file stays readable for 1.5.1 (it shares the file as a
fallback): plain UTF-8 without BOM, a flat object, the seven 1.5.1 keys unchanged.
"""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import time

import paths

NAMING = ('title', 'clean', 'title_id')
APPEARANCE = ('light', 'dark', 'system')
BAD_NAME = 'settings.bad.json'
_log = logging.getLogger('YtDlpDesktop.settings')


def defaults(output=None) -> dict:
    """Every key with its default. ``output`` is the already loaded output folder, on which
    the default archive root depends; the default output is paths.default_output()."""
    output = output or str(paths.default_output())
    return {
        # --- keys 1.5.1 knows (names and meaning unchanged)
        'output': output,
        'mode': '音频 · 原始最佳',      # engine.MODES[0]; membership is checked by the window
        'resolution': '最佳',          # engine.RESOLUTIONS[0]; likewise
        'batch_safe': True,
        'archive_enabled': False,
        'archive_root': str(Path(output) / '资料归档'),
        'cookies': '',                 # the path of the cookies file, never its contents
        # --- new in 1.6.0 (1.5.1 ignores them and drops them when it saves)
        'naming': 'title',
        'embed_metadata': True,
        'embed_cover': True,
        'subtitles': False,
        'skip_downloaded': True,
        'notify_done': True,
        'clipboard_hint': True,
        'check_engine_updates': True,
        'last_engine_check': 0.0,
        'bot_check_at': 0.0,
        'latest_engine': '',
        'last_clip': '',
        'geometry': '',
        'zoomed': False,
        'appearance': 'light',
    }


def __getattr__(name):
    # settings.DEFAULTS, built when it is read (the default output follows paths.APP_DIR).
    if name == 'DEFAULTS':
        return defaults()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def _text(value, default):
    return value if isinstance(value, str) else default


def _flag(value, default):
    return value if isinstance(value, bool) else default


def _seconds(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else default


def _choice(allowed):
    return lambda value, default: value if isinstance(value, str) and value in allowed else default


def _folder(value, default):
    """A path typed or pasted with quotes around it is still that path."""
    if not isinstance(value, str):
        return default
    value = value.strip().strip('"').strip()
    return value or default


_CHECKS = {
    'output': _folder, 'mode': _text, 'resolution': _text, 'batch_safe': _flag,
    'archive_enabled': _flag, 'archive_root': _text, 'cookies': _text,
    'naming': _choice(NAMING), 'embed_metadata': _flag, 'embed_cover': _flag, 'subtitles': _flag,
    'skip_downloaded': _flag, 'notify_done': _flag, 'clipboard_hint': _flag,
    'check_engine_updates': _flag, 'last_engine_check': _seconds, 'bot_check_at': _seconds,
    'latest_engine': _text, 'last_clip': _text, 'geometry': _text, 'zoomed': _flag,
    'appearance': _choice(APPEARANCE),
}


READ_RETRY = 1.0       # seconds a read is repeated while another program holds the file
HELD = '_held'         # load(): the file exists but could not be read (see save())


class _Unreadable(Exception):
    """settings.json exists but cannot be opened (held by another program, no access)."""


def _read_raw(path) -> bytes:
    """paths.read_bytes, repeated for up to READ_RETRY seconds while another program holds the
    file without sharing it (PermissionError: a sharing violation), as unfinished._read_text."""
    deadline = time.monotonic() + READ_RETRY
    while True:
        try:
            return paths.read_bytes(path)   # takes turns with a save running in another thread
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(paths.REPLACE_PAUSE)


def _bad_names(path) -> list:
    """Where an unreadable file is set aside: settings.bad.json, else a free dated name."""
    stamp = time.strftime('%Y%m%d-%H%M%S')
    return [path.with_name(BAD_NAME), path.with_name(f'settings.bad-{stamp}.json')] + [
        path.with_name(f'settings.bad-{stamp}-{number}.json') for number in range(2, 100)]


def _set_aside(path) -> Path:
    """Rename the file to the first free set-aside name.  os.rename never replaces an existing
    file on Windows, so an earlier set-aside copy is never lost.  OSError when it cannot."""
    for target in _bad_names(path):
        if os.path.lexists(target):
            continue
        for attempt in range(paths.REPLACE_ATTEMPTS):
            try:
                os.rename(path, target)
                return target
            except FileExistsError:
                break
            except PermissionError:          # somebody is reading it this very moment
                if attempt + 1 >= paths.REPLACE_ATTEMPTS:
                    raise
                time.sleep(paths.REPLACE_PAUSE)
    raise OSError(f'no free name to set aside {path}')


def _read_checked():
    """The stored object, or None when there is no usable file.  Raises _Unreadable when the
    file exists but cannot be opened, also after READ_RETRY.

    A file that is not UTF-8 JSON, or whose content is not an object, is kept as
    settings.bad.json (or, when that name is taken, settings.bad-<time>.json) instead of being
    overwritten silently by the next save.
    """
    path = paths.settings_file()
    try:
        raw = _read_raw(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise _Unreadable(error) from error
    try:
        data = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('the content is not a JSON object')
        return data
    except (ValueError, RecursionError) as error:
        try:
            bad = _set_aside(path)
            _log.warning('settings.json was unreadable (%s); it is kept as %s and defaults are used', error, bad)
        except OSError as failure:
            _log.warning('settings.json was unreadable (%s) and could not be set aside: %s', error, failure)
        return None


def _read():
    """The stored object, or None when there is no usable file (also when it cannot be opened)."""
    try:
        return _read_checked()
    except _Unreadable:
        return None


def _typed(data) -> dict:
    output = _folder(data.get('output'), '')
    values = defaults(output)
    for key, check in _CHECKS.items():
        if key in data:
            values[key] = check(data[key], values[key])
    values['_extra'] = {key: value for key, value in data.items() if key not in _CHECKS}
    return values


def load() -> dict:
    """Every known key, typed; an invalid value becomes that key's default.  Never raises.

    When settings.json exists but cannot be opened (another program holds it without sharing
    it, also after READ_RETRY), the defaults are returned with the key '_held': save() then
    never writes these defaults over the owner's file (final-data-safety-3).
    """
    try:
        data = _read_checked() or {}
    except _Unreadable as error:
        _log.warning('settings.json 暂时无法读取（可能被其他程序占用：%s），本次先使用默认设置；'
                     '原文件不会被默认设置覆盖。', error.__cause__ or error)
        values = _typed({})
        values[HELD] = {'loaded': {key: values[key] for key in _CHECKS}, 'changed': set()}
        return values
    return _typed(data)


def _merged(values, held):
    """The data to write for a session that started without the stored file: the file as it is
    now, with the values the owner changed in this session.  None when the file still cannot
    be read."""
    loaded, changed = held['loaded'], held['changed']
    changed.update(key for key in _CHECKS if key in values and values[key] != loaded.get(key))
    try:
        stored = _read_checked() or {}
    except _Unreadable:
        return None
    data = dict(stored)
    data.update({key: values[key] for key in changed})
    return data


def save(values) -> bool:
    """Write settings.json atomically; False (and a log line) when that fails.

    Keys starting with '_' are not written, except that the contents of '_extra' (the
    unknown keys load() found) go back to the top level.  Values that load() returned with
    '_held' (the file could not be read) never replace the stored file: the file is read
    again, and only the values changed in this session are written into it; while it still
    cannot be read, nothing is written (False).
    """
    try:
        held = values.get(HELD)
        if isinstance(held, dict) and isinstance(held.get('loaded'), dict) and isinstance(held.get('changed'), set):
            data = _merged(values, held)
            if data is None:
                _log.warning('settings.json 仍无法读取，本次设置没有保存，原文件保持不变。')
                return False
        else:
            data = {key: values[key] for key in _CHECKS if key in values}
            data.update({key: value for key, value in values.items()
                         if isinstance(key, str) and not key.startswith('_') and key not in data})
            extra = values.get('_extra')
            if isinstance(extra, dict):
                data.update({key: value for key, value in extra.items() if isinstance(key, str) and key not in data})
        paths.atomic_write_text(paths.settings_file(), json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except (OSError, TypeError, ValueError) as error:
        _log.error('settings were not saved: %s', error)
        return False
