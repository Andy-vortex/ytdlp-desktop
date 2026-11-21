"""Internal message keys (1.8.0, MESSAGES.md section 2).  Standard library + i18n only.

A core text is a Message: a str whose value is byte for byte the 1.7.0 Chinese text and which
carries its catalog key and parameters, so the UI can show it in the running language.  Raw marks
text the producer knows is not app text (titles from the source, engine output).  A plain str means
"provenance unknown".

    msg(key, **params)              a live message (value: the zh catalog text with zh params)
    raw(text) / clauses(parts)
    cut / masked / stripped         derived texts that keep the type (and the key)
    message_of(error)               the Message an exception carries, else str(error)
    show(value) / error_text(error) the text in the running language (never raises)
    zh(value)                       the plain zh text
    to_json / from_json / read_slots / write_slots   the format-2 store (2.0.0, S2-DESIGN.md 1.1;
                                    library and unfinished only)
    wire / unwire                   the collector IPC form (2.8; 2.0.0: {"$m": {"k", "p"}})

(2.0.0, S2 P4) The 1.8.x store codec (decode / decode_columns / encode), Value and a message's
origin / entry are gone: 2.0.0 does not read the stores of older versions.

(2.0.1) i18n.t shows a message param (or an exception that carries one) in the language of the call
(Message.text_in); an f-string, a concatenation or a widget text still needs show() / error_text().
"""
from __future__ import annotations

import json
import logging
import threading
import types

import i18n

PROXY_TOKEN = '[代理]'                 # the single definition of the proxy mask token in src
CLAUSES = 'common.clauses'            # structural key: zh '；'.join(parts), en i18n._join_sentences
JOIN = '；'

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
    """An app text: the 1.7.0 zh text as its value, plus key and params (2.1)."""

    __slots__ = ('key', '_params')

    def __new__(cls, value, key=None, params=None):
        self = str.__new__(cls, value)
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, '_params', types.MappingProxyType(dict(params or {})))
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
        return _message, (_plain(self), self.key, dict(self._params))

    def text_in(self, language):
        """This message's text in ``language``, whatever the running language is (i18n.t shows a message
        param with it, in the language of its call); in the running language it equals show()."""
        return _shown(self, language)


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


def _message(value, key, params):
    return Message(value, key, params)


# ------------------------------------------------------------------------------- constructors

def zh(value) -> str:
    """The plain zh text of a value ('' for None)."""
    if value is None:
        return ''
    if isinstance(value, str):
        return _plain(value)
    return str(value)


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
    return Message(text, key, params)


def raw(text):
    """A Raw text ('' stays '')."""
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
    return Message(JOIN.join(zh(part) for part in parts), CLAUSES, {'parts': parts})


def cut(value, n):
    """value[:n] keeping the type: a Message keeps key / params (en shows it whole, D12)."""
    if not isinstance(value, str):
        return value
    text = _plain(value)
    if len(text) <= n:
        return value
    if isinstance(value, Message):
        return Message(text[:n], value.key, value._params)
    if isinstance(value, Raw):
        return raw(text[:n])
    return text[:n]


def _replace(text, pairs):
    for secret, replacement in pairs:
        text = text.replace(secret, replacement)
    return text


def _mask_param(item, pairs):
    if isinstance(item, str):
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
    if not pairs or not isinstance(value, str):
        return value
    text = _plain(value)
    shown = _replace(text, pairs)
    if isinstance(value, Message):
        params = {name: _mask_param(item, pairs) for name, item in value._params.items()}
        if shown == text and all(params[name] is item for name, item in value._params.items()):
            return value
        return Message(shown, value.key, params)
    if shown == text:
        return value
    if isinstance(value, Raw):
        return raw(shown)
    return shown


def stripped(value):
    """A Message as it is; any other text .strip()ped with its type kept."""
    if isinstance(value, Message) or not isinstance(value, str):
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
            return i18n._t_in(language, value.key, {name: _render(item, language) for name, item in params.items()})
        text = _plain(value)
        return text.replace(PROXY_TOKEN, i18n._t_in(language, 'core.proxy_mask', {})) if PROXY_TOKEN in text else text
    if isinstance(value, (list, tuple)):
        return i18n._join_sentences(language, [_render(part, language) for part in value])
    return str(value)


def _shown(value, language) -> str:
    """``value`` as plain text in ``language``; never raises (on an internal error: the zh text, one
    WARNING per process)."""
    try:
        return _render(value, language)
    except Exception as error:
        _warn_once(('show', type(error).__name__), 'messages: show failed: %s', error)
        return zh(value)


def show(value) -> str:
    """``value`` as plain text in the running language; None -> ''; never raises (on an internal
    error: the zh text, one WARNING per process)."""
    return _shown(value, i18n.language())


def error_text(error) -> str:
    """show(message_of(error))."""
    return _shown(message_of(error), i18n.language())


# ------------------------------------------------------------------------------- format-2 store
#
# (2.0.0, S2-DESIGN.md 1.1) The JSON form of a text in a format-2 record: a program text is
# {"k": key, "p": {name: param}} ("p" left out when empty), any other text a plain string ('' for
# none).  A param is an int, a float, a plain string or such a message; the parts of common.clauses
# are a list.  Only the known text positions of a record ("slots") are read this way: a dict
# anywhere else is never taken for a message.  The library (library.SLOTS) and the unfinished list
# (unfinished.SLOTS) read and write their records this way.


class _Unstorable(Exception):
    pass


def _json_param(item):
    if isinstance(item, Message):
        return _json_message(item)
    if isinstance(item, str):
        return _plain(item)
    if isinstance(item, bool) or item is None:
        raise _Unstorable
    if isinstance(item, (int, float)):
        return item
    if isinstance(item, dict):
        return item                  # a param from_json kept as it was (an entry of a later version)
    raise _Unstorable


def _json_message(message) -> dict | str:
    if message.key is None:
        return _plain(message)
    params = {}
    for name, item in message._params.items():
        if message.key == CLAUSES and name == 'parts' and isinstance(item, (list, tuple)):
            params[name] = [_json_param(part) for part in item]
        else:
            params[name] = _json_param(item)
    form = {'k': message.key}
    if params:
        form['p'] = params
    return form


def to_json(value):
    """The format-2 JSON form of a slot value: a Message with a key -> {"k", "p"}; any other text ->
    its plain text; None and a dict (already a JSON form) unchanged.  Never raises: a message with a
    param that cannot be stored (True, None, an object) is stored as its zh text (one WARNING)."""
    if isinstance(value, Message):
        try:
            form = _json_message(value)
            json.dumps(form, ensure_ascii=False)
            return form
        except Exception as error:
            _warn_once(('to_json', value.key), 'messages: %s cannot be stored as a message (%s); stored as text',
                       value.key, type(error).__name__)
            return _plain(value)
    if isinstance(value, str):
        return _plain(value)
    return value


def _from_param(item):
    if isinstance(item, bool) or item is None:
        return item
    if isinstance(item, (int, float)):
        return item
    if isinstance(item, str):
        return Raw(item)
    if isinstance(item, dict):
        found = from_json(item)
        return found
    return item


def _is_param(item) -> bool:
    """P := int | float | str | M (DATA.md section 1); True, False and None are not params."""
    if isinstance(item, bool):
        return False
    return isinstance(item, (int, float, str)) or _is_form(item)


def _is_form(value) -> bool:
    """M := {"k": non-empty str, "p"?: {name: P}}; only common.clauses has a list ('parts') of P."""
    if not (isinstance(value, dict) and isinstance(value.get('k'), str) and value['k']
            and not set(value) - {'k', 'p'} and isinstance(value.get('p', {}), dict)):
        return False
    for name, item in value.get('p', {}).items():
        if value['k'] == CLAUSES and name == 'parts':
            if not (isinstance(item, list) and all(_is_param(part) for part in item)):
                return False
        elif not _is_param(item):
            return False
    return True


def from_json(value):
    """The typed value of a slot's JSON form: a string -> Raw ('' stays ''); a well-formed
    {"k": str, "p"?: dict} (_is_form) -> a Message (value: the zh rendering; an unknown key shows the key
    itself, its params are kept); anything else, also a {"k", "p"} whose params break the grammar,
    unchanged (so to_json writes it back as it was).  Never raises."""
    if isinstance(value, str):
        return Raw(value) if value else ''
    try:
        well_formed = _is_form(value)
    except RecursionError:
        well_formed = False
    if not well_formed:
        return value
    try:
        key, params = value['k'], {}
        for name, item in value.get('p', {}).items():
            if key == CLAUSES and name == 'parts' and isinstance(item, list):
                params[name] = tuple(_from_param(part) for part in item)
            else:
                params[name] = _from_param(item)
        try:
            text = _render_zh(key, params)
        except Exception:
            text = key
        return Message(text, key, params)
    except Exception as error:
        _warn_once(('from_json', type(error).__name__), 'messages: from_json failed: %s', error)
        return value


def _slot_walk(node, path, change):
    """A copy of ``node`` with change(value) at every place ``path`` reaches ('*': every item of a list
    or value of a dict); containers off the path are the very same objects."""
    if not path:
        return change(node)
    head, rest = path[0], path[1:]
    if head == '*':
        if isinstance(node, list):
            return [_slot_walk(item, rest, change) for item in node]
        if isinstance(node, dict):
            return {name: _slot_walk(item, rest, change) for name, item in node.items()}
        return node
    if isinstance(node, dict) and head in node:
        copy = dict(node)
        copy[head] = _slot_walk(node[head], rest, change)
        return copy
    return node


def _slots(record, slots, change):
    try:
        for path in slots:
            record = _slot_walk(record, tuple(path), change)
    except Exception as error:
        _warn_once(('slots', type(error).__name__), 'messages: slot walk failed: %s', error)
    return record


def read_slots(record, slots):
    """A copy of a format-2 record with every slot value through from_json (``slots``: paths such as
    ('errors', '*', 'error')).  The record itself is not changed.  Never raises."""
    return _slots(record, slots, from_json)


def write_slots(record, slots):
    """A copy of a record with every slot value through to_json, ready for json.dumps.  Never raises."""
    return _slots(record, slots, to_json)


# ------------------------------------------------------------------------------- collector IPC

def wire(value):
    """The JSON form of a value for the collector's request.events (2.8; 2.0.0, S2-DESIGN.md 1.6): a
    Message with a key is {"$m": {"k": key, "p": {name: wire(param)}}} ("p" left out when empty);
    dicts, lists and tuples are walked; any other value (a Raw or other text: its plain text) as it is."""
    if isinstance(value, Message) and value.key is not None:
        form = {'k': value.key}
        params = {name: wire(item) for name, item in value._params.items()}
        if params:
            form['p'] = params
        return {'$m': form}
    if isinstance(value, str):
        return _plain(value)
    if isinstance(value, dict):
        return {name: wire(item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def unwire(obj):
    """The reverse of wire(): {"$m": {"k", "p"?}} -> a Message (a list param -> a tuple, the parts of
    common.clauses); ValueError for a malformed marker object."""
    if isinstance(obj, list):
        return [unwire(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    if len(obj) == 1 and next(iter(obj)) == '$m':
        form = obj['$m']
        if not (isinstance(form, dict) and isinstance(form.get('k'), str) and form['k']
                and not set(form) - {'k', 'p'} and isinstance(form.get('p', {}), dict)):
            raise ValueError('malformed $m')
        key, params = form['k'], {}
        for name, item in form.get('p', {}).items():
            item = unwire(item)
            params[name] = tuple(item) if isinstance(item, list) else item
        try:
            text = _render_zh(key, params)
        except Exception:
            text = key
        return Message(text, key, params)
    return {name: unwire(item) for name, item in obj.items()}
