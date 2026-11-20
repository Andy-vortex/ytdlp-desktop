"""Chinese / English texts (1.7.0, I18N.md sections 3 and 9). Standard library only.

It imports the eight catalog modules i18n_<part> and no other module of the app, so every module may
import it without a cycle.  (2.0.0 S3) Each catalog is hand-written and the only source of its texts:
TEXTS {key: ('中文', 'English')}, and optionally GROUPS (menu value -> key) and TAGS (key -> log-line tag)
for its own keys.  CATALOG, GROUPS and TAGS below are their merge; nothing is generated.

The language ('zh' or 'en') is fixed for the life of a process: app.py calls init() once with
the stored choice; everything else reads language(), which resolves lazily when init() was never
called (tests that build a StudioApp directly, the collector worker).  Order of precedence:
environment YTDLP_DESKTOP_LANG > the stored choice ('zh' / 'en') > detect() ('system').

    t(key, **values)        the text of a key in the running language (zh, then the key, as fallbacks)
    label(group, value)     the label of a stored value; choices(group) the menu (value, label) pairs
    all_values(key)         the text of a key in every language; log_tag(line) the TAGS tag of a log line

Nothing here raises for a missing key, a missing placeholder, an unknown stored value or a
Windows API failure: the result degrades and a WARNING is logged once.

(1.8.0, MESSAGES.md 6.4) The reverse lookup of 1.7.0 (localize: a Chinese core text back to its
key) is gone.  The core passes messages (messages.py) and the UI shows them with messages.show();
keyless text stored by older versions is not read (2.0.0).
"""
from __future__ import annotations

import ctypes
import logging
import os
import string
import threading
import unicodedata

import i18n_core
import i18n_common
import i18n_shell
import i18n_download
import i18n_log
import i18n_settings
import i18n_collect
import i18n_library

LANGUAGES = ('zh', 'en')
CHOICES = ('system', 'zh', 'en')          # settings.json 'language'
ENV = 'YTDLP_DESKTOP_LANG'                 # override: zh / en (also zh-CN, en-US, ...: by prefix)
HELD = '_held'                             # settings.HELD: the session started without settings.json
CATALOG_MODULES = (i18n_core, i18n_common, i18n_shell, i18n_download, i18n_log, i18n_settings, i18n_collect,
                   i18n_library)
LANG_CHINESE = 0x04
LOCALE_NAME_MAX_LENGTH = 85
JOIN = '；'                                 # the stored joiner of clauses (collection_ui last_error)

_log = logging.getLogger('YtDlpDesktop.i18n')
_lock = threading.RLock()
_current = None
_warned = set()
_FORMATTER = string.Formatter()


def _merge(attribute):
    merged = {}
    for module in CATALOG_MODULES:
        merged.update(getattr(module, attribute, {}))     # a name in two modules: the later wins (C2 finds it)
    return merged


_TEXTS = _merge('TEXTS')
CATALOG = {language_: {key: pair[index] for key, pair in _TEXTS.items()}
           for index, language_ in enumerate(LANGUAGES)}
# Stored value or code -> key, per group (label / choices; tests read this name too).
GROUPS = _merge('GROUPS')
# Key -> 'error' / 'ok': the log-line tag of the key's text (log_tag, ui_log.classify).
TAGS = dict(sorted(_merge('TAGS').items()))


def _warn_once(token, message, *args) -> None:
    with _lock:
        if token in _warned:
            return
        _warned.add(token)
    _log.warning(message, *args)


# ------------------------------------------------------------------------------- detection

class _Win32:
    """The two Windows calls detect() needs; tests replace the module's ``_win32`` instance."""

    @staticmethod
    def _kernel32():
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetUserDefaultUILanguage.restype = wintypes.WORD
        kernel32.GetUserDefaultUILanguage.argtypes = ()
        kernel32.GetUserDefaultLocaleName.restype = ctypes.c_int
        kernel32.GetUserDefaultLocaleName.argtypes = (wintypes.LPWSTR, ctypes.c_int)
        return kernel32

    def ui_language(self) -> int:
        """LANGID of the Windows display language (0 when unknown)."""
        return int(self._kernel32().GetUserDefaultUILanguage())

    def locale_name(self) -> str:
        """Name of the user locale (regional format), e.g. 'zh-CN' ('' when unknown)."""
        buffer = ctypes.create_unicode_buffer(LOCALE_NAME_MAX_LENGTH)
        if not self._kernel32().GetUserDefaultLocaleName(buffer, LOCALE_NAME_MAX_LENGTH):
            return ''
        return buffer.value


_win32 = _Win32()


def _system():
    """(ui LANGID or None, locale name or None): None where the call failed or gave nothing."""
    try:
        ui = int(_win32.ui_language()) or None
    except Exception:
        ui = None
    try:
        locale = str(_win32.locale_name() or '') or None
    except Exception:
        locale = None
    return ui, locale


def detect() -> str:
    """'zh' when the Windows display language or the regional format is Chinese, else 'en';
    'zh' (the program's historical language) when both calls fail.  Never raises."""
    ui, locale = _system()
    if ui is not None and ui & 0x3FF == LANG_CHINESE:
        return 'zh'
    if locale is not None and locale.lower().startswith('zh'):
        return 'zh'
    if ui is None and locale is None:
        return 'zh'
    return 'en'


def env_override(environ=None):
    """'zh' / 'en' when YTDLP_DESKTOP_LANG holds a known value (by prefix, any case), else None."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(ENV) or '').strip().lower()
    for language in LANGUAGES:
        if value.startswith(language):
            return language
    return None


def resolve(choice='system', environ=None) -> str:
    """The language a start with this stored choice gets.  ``environ={}`` ignores the override."""
    override = env_override(environ)
    if override:
        return override
    if choice in LANGUAGES:
        return choice
    return detect()


def init(choice='system') -> str:
    """Resolve and fix the language of this process (app.py, once); returns it."""
    language_ = resolve(choice)
    set_language(language_)
    return language_


def set_language(lang) -> None:
    """Fix the language (init and tests).  None returns to "not resolved yet" (tests)."""
    global _current
    if lang is not None and lang not in LANGUAGES:
        raise ValueError(f'unknown language {lang!r}')
    with _lock:
        _current = lang


def language() -> str:
    """The running language; resolved with resolve('system') on first use when init() was not called."""
    current = _current
    if current is None:
        with _lock:
            if _current is None:
                set_language(resolve('system'))
            current = _current
    return current


def diagnostics(choice=None) -> dict:
    """What decided the language: Windows values, the choice, the variable and the result."""
    ui, locale = _system()
    return {'ui_language': None if ui is None else f'0x{ui:04X}', 'locale': locale, 'choice': choice,
            'env': os.environ.get(ENV), 'detected': detect(),
            'resolved': resolve(choice) if choice is not None else language()}


def stored_choice(prefs) -> str:
    """The stored choice: prefs['language'] when it is one of CHOICES, else 'system'."""
    try:
        value = prefs.get('language') if isinstance(prefs, dict) else None
        return value if isinstance(value, str) and value in CHOICES else 'system'
    except Exception:
        return 'system'


def store_choice(prefs, value) -> bool:
    """Keep the choice in prefs['language'] (settings.save() writes it).  False for an unknown value
    and for a session that started with settings.json held (it would be lost)."""
    if not isinstance(prefs, dict) or HELD in prefs or value not in CHOICES:
        return False
    prefs['language'] = value
    return True


# ------------------------------------------------------------------------------- lookup

class _Values(dict):
    """The values of one t() call; a missing placeholder stays '{name}' (logged once)."""

    def __init__(self, values, language_, key):
        super().__init__((name, str(value)) for name, value in values.items())
        self.language = language_
        self.key = key

    def __missing__(self, name):
        _warn_once(('placeholder', self.language, self.key, name),
                   'i18n: no value for {%s} of %s (%s)', name, self.key, self.language)
        return '{' + str(name) + '}'


def _text(key, language_):
    if not isinstance(key, str):
        key = str(key)
    text = CATALOG.get(language_, {}).get(key)
    if text is None:
        _warn_once(('key', language_, key), 'i18n: missing key %s (%s)', key, language_)
        text = CATALOG['zh'].get(key)
        if text is None:
            return str(key)
    return text


def _format(text, values, language_, key) -> str:
    if '{' not in text and '}' not in text:
        return text
    try:
        return _FORMATTER.vformat(text, (), _Values(values, language_, key))
    except Exception as error:              # ValueError / IndexError / KeyError of a broken catalog text
        _warn_once(('format', language_, key), 'i18n: text of %s (%s) cannot be formatted: %s', key, language_, error)
        return text


def t(key, **values) -> str:
    """The text of ``key`` in the running language with its named placeholders filled."""
    language_ = language()
    return _format(_text(key, language_), values, language_, key)


def _t_in(language_, key, values) -> str:
    return _format(_text(key, language_), values, language_, key)


def all_values(key) -> tuple:
    """The text of ``key`` in every language, zh first, without repeats."""
    found = []
    for language_ in LANGUAGES:
        text = CATALOG[language_].get(key)
        if text is None:
            text = CATALOG['zh'].get(key, str(key))
        if text not in found:
            found.append(text)
    return tuple(found)


_line_patterns = {}     # tuple(TAGS.items()) -> ((tag, anchored, words), ...)


def _words(text):
    """(anchored, words) of a text: its literal parts in order, the first as it is (the line starts with it when
    the text does not start with a placeholder), the later ones without the separators around them."""
    try:
        literals = [literal for literal, _field, _spec, _conv in _FORMATTER.parse(text)]
    except ValueError:
        literals = [text]
    words = [literals[0]] + [_bare(part) for part in literals[1:]]
    return bool(literals[0]), tuple(word for word in words if word)


def _bare(part):
    """``part`` without the spaces and punctuation (any script) at its ends."""
    kept = [index for index, char in enumerate(part) if not (char.isspace() or unicodedata.category(char)[0] == 'P')]
    return part[kept[0]:kept[-1] + 1] if kept else ''


def _patterns():
    snapshot = tuple(TAGS.items())
    found = _line_patterns.get(snapshot)
    if found is None:
        found = tuple((tag, *_words(text)) for tag in ('error', 'ok') for key, value in snapshot if value == tag
                      for text in all_values(key))
        _line_patterns.clear()
        _line_patterns[snapshot] = found
    return found


def log_tag(line):
    """'error' or 'ok' when ``line`` is the text, in any language, of a key TAGS names, else None (ui_log.classify).

    A line is a key's text when it starts with the text's literal start and the text's other literal parts follow
    in order (the spaces and punctuation around a placeholder are ignored).  'error' keys are tried first."""
    for tag, anchored, words in _patterns():
        if not words or (anchored and not line.startswith(words[0])):
            continue
        position = 0
        for word in words:
            position = line.find(word, position)
            if position < 0:
                break
            position += len(word)
        else:
            return tag
    return None


def _join_sentences(language_, parts) -> str:
    """zh: the parts joined with '；' (the stored form, JOIN); en: each part ends with a full stop and the parts
    are joined with common.sentence_join (messages renders common.clauses with it)."""
    parts = [str(part) for part in parts]
    if language_ == 'zh':
        return JOIN.join(parts)
    shown = []
    for part in parts:
        part = part.strip()
        if part and not part.endswith(('.', '…', '?', ')')):
            part += '.'
        shown.append(part)
    return _t_in(language_, 'common.sentence_join', {}).join(shown)


# ------------------------------------------------------------------------------- stored values

def label(group, value) -> str:
    """The label of a stored value of ``group`` (I18N.md 7); an unknown value is shown as str(value)."""
    try:
        key = GROUPS.get(group, {}).get(value)
    except TypeError:
        key = None
    if key is None:
        return '' if value is None else str(value)
    return t(key)


def choices(group) -> list:
    """[(stored value, label)] of ``group`` in menu order."""
    return [(value, label(group, value)) for value in GROUPS.get(group, {})]

