"""Internal message keys (1.8.0, MESSAGES.md section 2).  Standard library + i18n only.

A core text is a Message: a str whose value is byte for byte the 1.7.0 Chinese text and which
carries its catalog key and parameters, so the UI can show it in the running language.  Raw marks
text the producer knows is not app text (titles from the source, engine output); Value a stored
enum value nested in a message.  A plain str means "provenance unknown".

    msg(key, **params)              a live message (value: the zh catalog text with zh params)
    value(group, stored) / raw(text) / clauses(parts)
    cut / masked / stripped         derived texts that keep the type (and the key)
    message_of(error)               the Message an exception carries, else str(error)
    show(value) / error_text(error) the text in the running language (never raises)
    zh(value) / template(key)       the plain zh text / the zh catalog template
    decode / decode_columns / encode   the store codec (MESSAGES.md 4.3-4.5; library and
                                    unfinished only, G1d)
    wire / unwire                   the collector IPC form (2.8)
    guarded(emit, producer) / exempt(reason)   the G2 test hooks (7.3)

When YTDLP_DESKTOP_MSG_GUARD is set (tests only), importing this module installs the G2 hooks:
the strict-mode tripwires of 2.9, the checked i18n.t wrapper and the landing checks; without
the variable nothing is installed and every method is the plain str one.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import types

import i18n

PROXY_TOKEN = '[代理]'                 # the single definition of the proxy mask token in src
CLAUSES = 'common.clauses'            # structural key: zh '；'.join(parts), en i18n._join_sentences
JOIN = '；'
GUARD_ENV = 'YTDLP_DESKTOP_MSG_GUARD'
GUARD = bool(os.environ.get(GUARD_ENV))
ENTRY_LIMIT = 2048                    # characters of one sidecar entry as compact JSON (E5)
KEY_LIMIT = 200                       # characters of a well-formed entry's key (4.4 step 3)
ORIGINS = ('live', 'stored', 'legacy', 'foreign')

_log = logging.getLogger('YtDlpDesktop.messages')
_lock = threading.RLock()
_warned = set()


def _warn_once(token, message, *args) -> None:
    with _lock:
        if token in _warned:
            return
        _warned.add(token)
    _log.warning(message, *args)


# ------------------------------------------------------------------------------- value types

def _plain(text) -> str:
    return str.__str__(text)


class Message(str):
    """An app text: the 1.7.0 zh text as its value, plus key, params and origin (2.1)."""

    __slots__ = ('key', '_params', 'origin', 'entry')

    def __new__(cls, value, key=None, params=None, origin='live', entry=None):
        self = str.__new__(cls, value)
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, '_params', types.MappingProxyType(dict(params or {})))
        object.__setattr__(self, 'origin', origin if origin in ORIGINS else 'live')
        object.__setattr__(self, 'entry', entry)
        return self

    @property
    def params(self):
        return self._params

    def __setattr__(self, name, value):
        raise AttributeError('Message is immutable')

    def __delattr__(self, name):
        raise AttributeError('Message is immutable')

    def __str__(self):
        return self

    def __format__(self, spec):
        return self if not spec else format(_plain(self), spec)

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return _message, (_plain(self), self.key, dict(self._params), self.origin, self.entry)


class Value(str):
    """A stored enum value nested in a message (shown through i18n.label(group, value))."""

    __slots__ = ('group',)

    def __new__(cls, group, stored):
        self = str.__new__(cls, stored)
        object.__setattr__(self, 'group', group)
        return self

    def __setattr__(self, name, value):
        raise AttributeError('Value is immutable')

    def __delattr__(self, name):
        raise AttributeError('Value is immutable')

    def __str__(self):
        return self

    def __format__(self, spec):
        return self if not spec else format(_plain(self), spec)

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return Value, (self.group, _plain(self))


class Raw(str):
    """Text the producer knows is not app text: never translated, never reverse-read."""

    __slots__ = ()

    def __str__(self):
        return self

    def __format__(self, spec):
        return self if not spec else format(_plain(self), spec)

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return Raw, (_plain(self),)


def _message(value, key, params, origin, entry):
    return Message(value, key, params, origin, entry)


class MessageFlattened(RuntimeError):
    """(tests only) A guarded src module turned a Message or Value into plain text (2.9)."""


# ------------------------------------------------------------------------------- constructors

def zh(value) -> str:
    """The plain zh text of a value ('' for None)."""
    if value is None:
        return ''
    if isinstance(value, str):
        return _plain(value)
    return str(value)


def template(key) -> str:
    """The zh catalog template of ``key`` (plain), for H4 template constants (updater.MSG_*)."""
    return _plain(i18n.CATALOG['zh'].get(key, key))


def _zh_param(value):
    if isinstance(value, str):
        return _plain(value)
    if isinstance(value, (list, tuple)):
        return JOIN.join(_zh_param(item) for item in value)
    return value


def _render_zh(key, params) -> str:
    if key == CLAUSES:
        return JOIN.join(_zh_param(part) for part in params.get('parts', ()))
    return _plain(i18n._t_in('zh', key, {name: _zh_param(item) for name, item in params.items()}))


def msg(key, **params) -> Message:
    """A live message; never raises (a missing key or placeholder behaves like i18n.t)."""
    if 'parts' in params and key == CLAUSES:
        params['parts'] = tuple(params['parts'])
    try:
        text = _render_zh(key, params)
    except Exception as error:
        _warn_once(('msg', key), 'messages: %s cannot be rendered: %s', key, error)
        text = str(key)
    return Message(text, key, params, 'live')


def value(group, stored):
    """A Value when ``stored`` is a str, else the object unchanged."""
    if isinstance(stored, str) and not isinstance(stored, Value):
        return Value(group, _plain(stored))
    return stored


def raw(text):
    """A Raw text ('' stays '': an empty text never gets a sidecar)."""
    if not isinstance(text, str) or isinstance(text, Raw):
        return text
    text = _plain(text)
    return Raw(text) if text else ''


def clauses(parts):
    """[] -> ''; [one] -> that part; else a common.clauses message ('；'.join of the zh parts)."""
    parts = [part for part in (parts or ()) if part is not None]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    parts = tuple(parts)
    return Message(JOIN.join(zh(part) for part in parts), CLAUSES, {'parts': parts}, 'live')


def cut(value, n):
    """value[:n] keeping the type: a Message keeps key / params / origin (en shows it whole, D12)."""
    if not isinstance(value, str):
        return value
    text = _plain(value)
    if len(text) <= n:
        return value
    if isinstance(value, Message):
        return Message(text[:n], value.key, value._params, value.origin, value.entry)
    if isinstance(value, Raw):
        return raw(text[:n])
    return text[:n]


def _replace(text, pairs):
    for secret, replacement in pairs:
        text = text.replace(secret, replacement)
    return text


def _mask_param(item, pairs):
    if isinstance(item, (Message, Raw)) or (isinstance(item, str) and not isinstance(item, Value)):
        return masked(item, pairs)
    if isinstance(item, (list, tuple)):
        return tuple(_mask_param(part, pairs) for part in item)
    return item


def masked(value, pairs):
    """Every ``str.replace(secret, replacement)`` of ``pairs`` in order (the 1.7.0 operation, so the
    bytes match), on the text and recursively on raw params; the key is kept.  Empty secrets are
    skipped."""
    pairs = [(_plain(secret), _plain(replacement)) for secret, replacement in (pairs or ())
             if isinstance(secret, str) and secret]
    if not pairs or not isinstance(value, str) or isinstance(value, Value):
        return value
    text = _plain(value)
    shown = _replace(text, pairs)
    if isinstance(value, Message):
        params = {name: _mask_param(item, pairs) for name, item in value._params.items()}
        if shown == text and all(params[name] is item for name, item in value._params.items()):
            return value
        return Message(shown, value.key, params, value.origin, value.entry)
    if shown == text:
        return value
    if isinstance(value, Raw):
        return raw(shown)
    return shown


def stripped(value):
    """Message / Value as they are; any other text .strip()ped with its type kept."""
    if isinstance(value, (Message, Value)) or not isinstance(value, str):
        return value
    text = _plain(value).strip()
    return raw(text) if isinstance(value, Raw) else text


def message_of(error):
    """The Message an exception carries (its only argument), else str(error)."""
    args = getattr(error, 'args', None)
    if isinstance(args, tuple) and len(args) == 1 and isinstance(args[0], Message):
        return args[0]
    return str(error)


# ------------------------------------------------------------------------------- rendering

_ORIGINAL_T = i18n.t


def _t(key, **values):
    """i18n.t without the G2 wrapper (show() hands it shown params only)."""
    return _ORIGINAL_T(key, **values)


def _render(value, language) -> str:
    if value is None:
        return ''
    if language == 'zh':
        # (2.6) zh: the value's own text, str(value) for anything else (a list too: 1.7.0's
        # localize returned str(text) for a non-str; final review, ZH-2)
        return zh(value)
    if isinstance(value, str):
        if isinstance(value, Message):
            if value.key is None:
                return _plain(value)
            params = value._params
            if value.key == CLAUSES:
                return i18n._join_sentences(language, [_render(part, language) for part in params.get('parts', ())])
            return _t(value.key, **{name: _render(item, language) for name, item in params.items()})
        if isinstance(value, Value):
            return i18n.label(value.group, _plain(value))
        text = _plain(value)
        return text.replace(PROXY_TOKEN, _t('core.proxy_mask')) if PROXY_TOKEN in text else text
    if isinstance(value, (list, tuple)):
        return i18n._join_sentences(language, [_render(part, language) for part in value])
    return str(value)


def show(value) -> str:
    """``value`` as plain text in the running language; None -> ''; never raises (on an internal
    error: the zh text, one WARNING per process)."""
    if GUARD:
        _check_shown(value, 'show', sys._getframe(1))
    try:
        return _render(value, i18n.language())
    except Exception as error:
        _warn_once(('show', type(error).__name__), 'messages: show failed: %s', error)
        return zh(value)


def error_text(error) -> str:
    """show(message_of(error))."""
    shown = message_of(error)
    if GUARD:
        _check_shown(shown, 'error_text', sys._getframe(1))
    try:
        return _render(shown, i18n.language())
    except Exception as failure:
        _warn_once(('show', type(failure).__name__), 'messages: show failed: %s', failure)
        return zh(shown)


# ------------------------------------------------------------------------------- store codec

# (MESSAGES.md 4.3) record kind -> its text fields.  A field is (path, field kind); '*' walks the
# items of a list or the values of a dict (I-1 amendment A2).  The 'reason' of last_reason is paired with its sibling 'error' (L4).  job_options holds
# the lift-only context titles of a download job's options (no sidecar inside options).
SCHEMA = {
    'job': ((('errors', '*'), 'error'), (('last_reason', 'error'), 'error'), (('last_reason', 'reason'), 'reason')),
    'subscription': ((('last_error',), 'clauses'),),
    'page': ((('title',), 'title'), (('links', '*', 'title'), 'title')),
    'entry': ((('title',), 'title'), (('page_title',), 'title')),
    'archive': ((('title',), 'title'), (('page_title',), 'title')),
    'failed_entry': ((('error',), 'error'),),
    'job_options': ((('archive_contexts', '*', 'title'), 'context_title'),),
}
COLUMNS = {'job': {'title': 'job_title'}, 'subscription': {'name': 'name'}}
NO_SIDECAR = frozenset({'job_options'})


def _walk(node, path, at=()):
    """(path to the value, value) of every value under ``path``."""
    if not path:
        yield at, node
        return
    head, rest = path[0], path[1:]
    if head == '*':
        if isinstance(node, list):
            for index, item in enumerate(node):
                yield from _walk(item, rest, at + (index,))
        elif isinstance(node, dict):                     # archive_contexts maps link -> context
            for name, item in node.items():
                yield from _walk(item, rest, at + (name,))
    elif isinstance(node, dict) and head in node:
        yield from _walk(node[head], rest, at + (head,))


def _job_context(record, columns):
    options = record.get('options') if isinstance(record, dict) else None
    urls = options.get('urls') if isinstance(options, dict) else None
    links = len([url for url in urls if isinstance(url, str) and url]) if isinstance(urls, list) else 0
    return {'kind': (columns or {}).get('kind'), 'titled': bool(record.get('titled')), 'links': links}


def _fields(kind, record):
    """(path, field kind, value, context) of every text field of a record of ``kind``."""
    for path, field_kind in SCHEMA.get(kind, ()):
        for at, item in _walk(record, path):
            context = None
            if field_kind == 'reason' and at[:1] == ('last_reason',):
                sibling = record['last_reason'].get('error') if isinstance(record.get('last_reason'), dict) else None
                context = {'error': _plain(sibling) if isinstance(sibling, str) else None}
            yield at, field_kind, item, context


def _column_fields(kind, record, columns):
    for name, field_kind in COLUMNS.get(kind, {}).items():
        if isinstance(columns, dict) and name in columns:
            context = _job_context(record, columns) if field_kind == 'job_title' else None
            yield name, field_kind, columns[name], context


def _placeholders(key):
    return {field for _literal, field, _spec, _conv in i18n._FORMATTER.parse(i18n.CATALOG['zh'][key])
            if field is not None}


class _Invalid(Exception):
    pass


def _known(key) -> bool:
    return key == CLAUSES or (key in i18n.CATALOG['zh'] and key in i18n.CATALOG['en'])


def _decode_param(item):
    if isinstance(item, bool) or item is None:
        raise _Invalid
    if isinstance(item, (int, float)):
        return item
    if isinstance(item, str):
        return raw(item)
    if isinstance(item, dict):
        if set(item) == {'g', 'v'}:
            import i18n_maps
            if item['g'] not in i18n_maps.GROUPS or not isinstance(item['v'], (str, int)) or isinstance(item['v'], bool):
                raise _Invalid
            return value(item['g'], item['v'])
        return _decode_entry(item, None)
    raise _Invalid


def _decode_entry(entry, text):
    """The Message of a valid entry (value ``text``, or for a nested one the zh rendering); _Invalid else."""
    if not isinstance(entry, dict) or not isinstance(entry.get('k'), str) or set(entry) - {'k', 'p'}:
        raise _Invalid
    key = entry['k']
    params = entry.get('p', {})
    if not isinstance(params, dict) or not _known(key):
        raise _Invalid
    if key == CLAUSES:
        parts = params.get('parts')
        if set(params) != {'parts'} or not isinstance(parts, list) or len(parts) < 2:
            raise _Invalid
        decoded = {'parts': tuple(_decode_param(part) for part in parts)}
    else:
        if set(params) != _placeholders(key):
            raise _Invalid
        decoded = {name: _decode_param(item) for name, item in params.items()}
    if text is None:
        text = _render_zh(key, decoded)
    return Message(text, key, decoded, 'stored')


def _compact(entry) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(',', ':'))


def _well_formed(entry) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get('k'), str) or not entry['k']:
        return False
    if len(entry['k']) > KEY_LIMIT or ('p' in entry and not isinstance(entry['p'], dict)):
        return False
    try:
        return len(_compact(entry)) <= ENTRY_LIMIT
    except (TypeError, ValueError):
        return False


def _legacy_param(item):
    import i18n_legacy
    if isinstance(item, i18n_legacy.Lifted):
        return Message(item.text, item.key, _legacy_params(item.params), 'legacy')
    if isinstance(item, i18n_legacy.Enum):
        return value(item.group, item.value)
    if isinstance(item, (list, tuple)):
        return tuple(_legacy_param(part) for part in item)
    return item


def _legacy_params(params):
    return {name: _legacy_param(item) for name, item in (params or {}).items()}


def _lift(field_kind, text, context):
    import i18n_legacy
    return i18n_legacy.lift(field_kind, text, context)


def _decode_text(text, field_kind, context, sidecar):
    if not isinstance(text, str) or not text or isinstance(text, (Message, Raw, Value)):
        return text
    if text in sidecar:
        entry = sidecar[text]
        if entry is None:
            return Raw(text)                                                 # step 4
        try:
            return _decode_entry(entry, text)                                # step 2
        except _Invalid:
            pass
        except RecursionError:
            pass
        if _well_formed(entry):                                              # step 3: foreign
            found = _lift(field_kind, text, context)
            key, params = found if found else (None, {})
            return Message(text, key, _legacy_params(params), 'foreign', _compact(entry))
    found = _lift(field_kind, text, context)                                 # step 5
    if found is None:
        return text
    return Message(text, found[0], _legacy_params(found[1]), 'legacy')


def _sidecar(record):
    sidecar = record.get('msgs') if isinstance(record, dict) else None
    return sidecar if isinstance(sidecar, dict) else {}


def _set(copy, at, item, copied):
    """Set copy[at...] = item, copying each container on the way once."""
    node = copy
    for index, step in enumerate(at[:-1]):
        child = node[step]
        if id(child) not in copied:
            child = list(child) if isinstance(child, list) else dict(child)
            copied.add(id(child))
            node[step] = child
        node = child
    node[at[-1]] = item


def decode(record, kind) -> dict:
    """A shallow copy of ``record`` with the text fields of ``kind`` decoded (4.4) and ``msgs``
    removed; every other value is the very same object (never ``options``).  Never raises."""
    if not isinstance(record, dict):
        return record
    copy = dict(record)
    copy.pop('msgs', None)
    try:
        sidecar = _sidecar(record)
        copied = set()
        for at, field_kind, item, context in list(_fields(kind, record)):
            decoded = _decode_text(item, field_kind, context, sidecar)
            if decoded is not item:
                _set(copy, at, decoded, copied)
    except Exception as error:
        _warn_once(('decode', type(error).__name__), 'messages: decode failed: %s', error)
    return copy


def decode_columns(record, kind, columns) -> dict:
    """The column texts (a job's {'title', 'kind'}, a subscription's {'name'}) decoded with the
    sidecar of the undecoded ``record``; other column values unchanged.  Never raises."""
    result = dict(columns or {})
    try:
        sidecar = _sidecar(record)
        for name, field_kind, item, context in _column_fields(kind, record if isinstance(record, dict) else {},
                                                              columns):
            result[name] = _decode_text(item, field_kind, context, sidecar)
    except Exception as error:
        _warn_once(('decode', type(error).__name__), 'messages: decode failed: %s', error)
    return result


def _persistable(message) -> bool:
    if not isinstance(message, Message) or message.origin not in ('live', 'stored') or message.key is None:
        return False
    for item in message._params.values():
        for part in (item if isinstance(item, (list, tuple)) else (item,)):
            if isinstance(part, Message) and part.origin != 'foreign' and not _persistable(part):
                return False
    return True


def _encode_param(item):
    if isinstance(item, Message):
        if item.origin == 'foreign' and item.entry:
            return json.loads(item.entry)
        return _entry_of(item)
    if isinstance(item, Value):
        return {'g': item.group, 'v': _plain(item)}
    if isinstance(item, str):
        return _plain(item)
    if isinstance(item, bool) or item is None:
        raise _Invalid
    if isinstance(item, (int, float)):
        return item
    raise _Invalid


def _entry_of(message) -> dict:
    if not _persistable(message):
        raise _Invalid
    params = {}
    for name, item in message._params.items():
        if message.key == CLAUSES and name == 'parts':
            params[name] = [_encode_param(part) for part in item]
        else:
            params[name] = _encode_param(item)
    entry = {'k': message.key}
    if params:
        entry['p'] = params
    return entry


def _add_entry(entries, item, field_kind, context) -> None:
    if not isinstance(item, str) or not item:
        return
    text = _plain(item)
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return                                                               # E5: lone surrogates
    if isinstance(item, Message):
        try:
            if item.origin == 'foreign' and item.entry:
                entry = json.loads(item.entry)                               # E1b
            else:
                entry = _entry_of(item)                                      # E1
            compact = _compact(entry)
            compact.encode('utf-8')
        except (_Invalid, ValueError, TypeError, UnicodeEncodeError, RecursionError):
            return
        if len(compact) > ENTRY_LIMIT:
            return
        if entries.get(text) is None:                                        # a message entry wins over null
            entries[text] = entry
    elif isinstance(item, Raw):
        if text not in entries and _lift(field_kind, text, context) is not None:
            entries[text] = None                                             # E2


def encode(record, kind, columns=None) -> dict:
    """A shallow copy of ``record`` whose ``msgs`` describes the typed values of its text fields of
    ``kind`` (and ``columns``) (4.5); no ``msgs`` key when there is nothing to describe.  Never raises."""
    if not isinstance(record, dict):
        return record
    copy = dict(record)
    copy.pop('msgs', None)
    try:
        fields = list(_fields(kind, record)) + list(_column_fields(kind, record, columns))
        if GUARD:
            _check_stored(fields)
        if kind in NO_SIDECAR:
            return copy
        entries = {}
        for _at, field_kind, item, context in fields:
            _add_entry(entries, item, field_kind, context)
        if entries:
            copy['msgs'] = entries
    except Exception as error:
        _warn_once(('encode', type(error).__name__), 'messages: encode failed: %s', error)
        copy.pop('msgs', None)
    return copy


# ------------------------------------------------------------------------------- collector IPC

def _wire_params(params):
    return {name: wire(item) for name, item in params.items()}


def wire(value):
    """The JSON form of a value for the collector's request.events (2.8)."""
    if isinstance(value, Message):
        form = [_plain(value), value.key, _wire_params(value._params), value.origin]
        if value.entry:
            form.append(value.entry)
        return {'$m': form}
    if isinstance(value, Value):
        return {'$v': [value.group, _plain(value)]}
    if isinstance(value, Raw):
        return {'$r': _plain(value)}
    if isinstance(value, dict):
        return {name: wire(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def unwire(obj):
    """The reverse of wire(); ValueError for a malformed marker object."""
    if isinstance(obj, list):
        return [unwire(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    if len(obj) == 1 and next(iter(obj)) in ('$m', '$v', '$r'):
        (marker, form), = obj.items()
        if marker == '$r':
            if not isinstance(form, str):
                raise ValueError('malformed $r')
            return raw(form)
        if marker == '$v':
            if not (isinstance(form, list) and len(form) == 2 and isinstance(form[0], str)
                    and isinstance(form[1], str)):
                raise ValueError('malformed $v')
            return Value(form[0], form[1])
        if not (isinstance(form, list) and len(form) in (4, 5) and isinstance(form[0], str)
                and (form[1] is None or isinstance(form[1], str)) and isinstance(form[2], dict)
                and form[3] in ORIGINS and (len(form) == 4 or isinstance(form[4], str))):
            raise ValueError('malformed $m')
        params = {}
        for name, item in form[2].items():
            item = unwire(item)
            params[name] = tuple(item) if isinstance(item, list) else item
        return Message(form[0], form[1], params, form[3], form[4] if len(form) == 5 else None)
    return {name: unwire(item) for name, item in obj.items()}


# ------------------------------------------------------------------------------- G2 (tests only)

# (MESSAGES.md 7.8, 2.9) Every src module outside TRANSIT is guarded: a runtime check records a
# violation when its producer frame is any such module.  TRANSIT modules pass texts through without
# deciding what is shown and are skipped when the producer frame is looked for; ui_theme is not one
# of them: it is the display boundary, so a text operation of ui_theme on a Message or Value (a value
# that reached a widget without show()) is charged to ui_theme itself.
TRANSIT = frozenset({'messages', 'i18n_legacy', 'i18n'})
TRIPWIRES = ('__getitem__', '__add__', '__radd__', '__mul__', '__rmul__', '__mod__', '__rmod__', 'strip', 'lstrip',
             'rstrip', 'replace', 'split', 'rsplit', 'splitlines', 'partition', 'rpartition', 'format', 'format_map',
             'ljust', 'rjust', 'center', 'lower', 'upper', 'casefold', 'title', 'capitalize', 'swapcase', 'translate',
             'expandtabs', 'zfill', 'removeprefix', 'removesuffix')
EVENT_FIELDS = {'log': (None,), 'waiting': ('reason',), 'item_done': ('reason', 'error'), 'collect_status': (None,),
                'collect_error': (None,), 'update_status': (None,), 'update_error': (None,), 'collect_page': ('title',)}
STORE_MODULES = frozenset({'library', 'unfinished'})
SRC_DIR = Path(__file__).resolve().parent
VERIFICATION_DIR = SRC_DIR.parent / 'verification'

_state = threading.local()
_exempt = []                  # reasons of the active exempt() blocks (process-wide)
_capture = []                 # lists receiving records instead of the report file (self-tests)
_records = []                 # every record of this process (also written to the report file)
_kinds = {}


def _busy() -> bool:
    return getattr(_state, 'busy', False)


class _Busy:
    def __enter__(self):
        self.saved = _busy()
        _state.busy = True

    def __exit__(self, *_exc):
        _state.busy = self.saved
        return False


def _classify(filename):
    """('src', module) / ('verification', None) / ('tk', None) / ('other', None) of a code file."""
    found = _kinds.get(filename)
    if found is not None:
        return found
    found = ('other', None)
    try:
        path = Path(filename).resolve()
        if path.parent == SRC_DIR and path.suffix == '.py':
            found = ('src', path.stem)
        elif path.is_relative_to(VERIFICATION_DIR):
            found = ('verification', None)
        elif path.name == '__init__.py' and path.parent.name == 'tkinter':
            found = ('tk', None)
    except (OSError, ValueError):
        pass
    _kinds[filename] = found
    return found


def _producer(frame, skip=frozenset()):
    """The nearest src frame outside TRANSIT (and ``skip``), walking outward; None at a
    verification/ frame, a Tk callback boundary or the bottom of the stack (2.9)."""
    while frame is not None:
        kind, module = _classify(frame.f_code.co_filename)
        if kind == 'tk' and frame.f_code.co_name == '__call__':
            return None
        if kind == 'verification':
            return None
        if kind == 'src' and module not in TRANSIT and module not in skip:
            return module, frame
        frame = frame.f_back
    return None


def _current_test():
    frame = sys._getframe(1)
    while frame is not None:
        candidate = frame.f_locals.get('self')
        if candidate is not None and type(candidate).__module__ != __name__:
            test_id = getattr(candidate, 'id', None)
            if callable(test_id) and hasattr(candidate, 'assertEqual'):
                try:
                    return test_id()
                except Exception:
                    return None
        frame = frame.f_back
    return None


def _report_path():
    folder = os.environ.get('VERIFY_OUT')
    return Path(folder) / f'msg-guard-{os.getpid()}.jsonl' if folder else None


def _write(line) -> None:
    if _capture:
        _capture[-1].append(line)
        return
    _records.append(line)
    path = _report_path()
    if path is None:
        return
    try:
        with _lock, open(path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _record(rule, where, found, key, text) -> bool:
    """Write one violation when there is a producer frame (a src module outside TRANSIT) and no
    exemption is active; True when written."""
    if found is None or _exempt:
        return False
    module, frame = found
    _write({'rule': rule, 'where': where, 'producer': f'{module}.py:{frame.f_lineno}', 'key': key,
            'text': zh(text)[:80], 'test': _current_test()})
    return True


def _recognise(text, display=False, english=False):
    import i18n_legacy
    return i18n_legacy.recognise(_plain(text), display, english)


def _plain_texts(item, raw_too, depth=0):
    """The plain (and, when raw_too, Raw) texts in a value and its nested message params."""
    if depth > 6:
        return
    if isinstance(item, Message):
        for param in item._params.values():
            yield from _plain_texts(param, raw_too, depth + 1)
    elif isinstance(item, Value):
        return
    elif isinstance(item, Raw):
        if raw_too:
            yield item
    elif isinstance(item, str):
        yield item
    elif isinstance(item, (list, tuple)):
        for part in item:
            yield from _plain_texts(part, raw_too, depth + 1)


def _check_texts(values, where, frame, display, raw_too, skip=frozenset(), english=False):
    if _busy() or _exempt:
        return
    with _Busy():
        try:
            hits = []
            for item in values:
                for text in _plain_texts(item, raw_too):
                    key = _recognise(text, display, english)
                    if key:
                        hits.append((key, text))
            if hits:
                found = _producer(frame, skip)
                for key, text in hits:
                    _record('R-text', where, found, key, text)
        except Exception as error:
            _warn_once(('guard', type(error).__name__), 'messages: guard check failed: %s', error)


def _check_shown(value, where, frame):
    # (converge round, A15.5) shown in English, the zh text of a UI key is out of place as well: the
    # display exclusion is for zh, where t() gives Chinese UI lines that pass through show()
    _check_texts([value], where, frame, display=True, raw_too=False, english=i18n.language() != 'zh')


def _check_stored(fields):
    if _busy() or _exempt:
        return
    _check_texts([item for _at, _kind, item, _context in fields], 'encode', sys._getframe(2), display=False,
                 raw_too=True, skip=STORE_MODULES)


def _check_event(kind, payload, frame):
    names = EVENT_FIELDS.get(kind)
    if names is None:
        return
    values = []
    for name in names:
        if name is None:
            values.append(payload)
        elif isinstance(payload, dict):
            values.append(payload.get(name))
    if kind == 'collect_page' and isinstance(payload, dict) and isinstance(payload.get('links'), list):
        values += [link.get('title') for link in payload['links'] if isinstance(link, dict)]
    _check_texts(values, f'emit {kind}', frame, display=False, raw_too=True)


def guarded(emit, producer):
    """``emit`` with the G2 emit hook (7.3); ``emit`` itself when the guard is off."""
    if not GUARD:
        return emit

    def checked(*args, **kwargs):
        if args:
            _check_event(args[0], args[1] if len(args) > 1 else kwargs.get('value'), sys._getframe(1))
        return emit(*args, **kwargs)

    checked.__wrapped__ = emit
    checked.producer = producer
    return checked


@contextlib.contextmanager
def exempt(reason):
    """G2 / tripwire exemption for test fixtures (7.3); counted in the per-file report."""
    if not GUARD:
        yield
        return
    if not reason:
        raise ValueError('an exemption needs a reason')
    _write({'exempt': str(reason), 'test': _current_test()})
    with _lock:
        _exempt.append(reason)
    try:
        yield
    finally:
        with _lock:
            _exempt.remove(reason)


@contextlib.contextmanager
def guard_capture():
    """(self-tests only) Collect the records of a block in a list instead of the report file."""
    records = []
    _capture.append(records)
    try:
        yield records
    finally:
        _capture.remove(records)


def _checked_t(key, **values):
    """i18n.t with the G2 parameter rules: R-param (a Message / Value / Raw / exception param) and,
    in en, R-text on plain params, where the zh text of a UI key counts too (A15.5)."""
    if values and not _busy() and not _exempt:
        with _Busy():
            try:
                hits = []
                english = i18n.language() != 'zh'
                for name, item in values.items():
                    if isinstance(item, (Message, Value, Raw, BaseException)):
                        hits.append(('R-param', name, type(item).__name__, item))
                    elif english and isinstance(item, str):
                        found = _recognise(item, True, True)
                        if found:
                            hits.append(('R-text', name, found, item))
                if hits:
                    producer = _producer(sys._getframe(1))
                    for rule, name, found, item in hits:
                        _record(rule, f't {key} {name}', producer, found, item if isinstance(item, str) else str(item))
            except Exception as error:
                _warn_once(('guard', type(error).__name__), 'messages: guard check failed: %s', error)
    return _ORIGINAL_T(key, **values)


def _flattened(obj, method, frame):
    if _busy() or _exempt:
        return
    with _Busy():
        found = _producer(frame)
        written = _record('tripwire', f'{type(obj).__name__}.{method}', found, getattr(obj, 'key', None)
                          or getattr(obj, 'group', None), obj)
    if written:
        raise MessageFlattened(f'{type(obj).__name__}.{method} on {zh(obj)[:40]!r} in {found[0]}.py:{found[1].f_lineno}')


def _tripwire(name):
    if name == '__radd__':
        def method(self, other):
            if not isinstance(other, str):
                return NotImplemented
            result = str.__add__(other, self)
            _flattened(self, name, sys._getframe(1))
            return result
    elif name == '__rmod__':
        def method(self, other):
            if not isinstance(other, str):
                return NotImplemented
            result = str.__mod__(other, self)
            if result is not self:
                _flattened(self, name, sys._getframe(1))
            return result
    elif name == '__rmul__':
        def method(self, other):
            result = str.__mul__(self, other)
            _flattened(self, name, sys._getframe(1))
            return result
    else:
        original = getattr(str, name)

        def method(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            if result is not self:
                _flattened(self, name, sys._getframe(1))
            return result
    method.__name__ = name
    return method


def _install() -> None:
    for cls in (Message, Value):
        for name in TRIPWIRES:
            setattr(cls, name, _tripwire(name))
    if getattr(i18n.t, '__name__', '') != '_checked_t':
        i18n.t = _checked_t


if GUARD:
    _install()
