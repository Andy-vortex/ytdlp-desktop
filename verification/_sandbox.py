"""Sandbox for everything under verification/ (1.6.0 WP0).

Import it first, before any src module:  ``import _sandbox``

After the import this process (and every child it starts) uses a private
LOCALAPPDATA, application folder, TEMP and output folder, so no test can touch
the owner's real %LOCALAPPDATA%\\YtDlpDesktop, the installed app's data folder
or the source tree.

Two modes:
* runner mode (env YTDLP_VERIFY_SANDBOX set by run_tests.py, or inherited from a
  sandboxed parent process): the environment already exists and is only verified;
* standalone mode (a file run on its own): the sandbox is created here, in a
  fresh temp folder that is removed at exit (YTDLP_VERIFY_KEEP=1 keeps it,
  YTDLP_VERIFY_BASE=DIR uses and keeps DIR).

No src module is imported here.

(1.7.0) Tests run in Chinese: YTDLP_DESKTOP_LANG=zh in this process and its children.
language(lang, env=True) runs a block in another language (env=False: without the variable, like
a normal start); fake_system(ui_langid, locale) fixes what i18n.detect() sees.  Both import i18n
when they are used.
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
STATE_NAME = 'YtDlpDesktop'
GOLDEN = ROOT / 'verification' / 'golden'
RUNNER_MODE = bool(os.environ.get('YTDLP_VERIFY_SANDBOX'))


def instance_name(base) -> str:
    """Name of the single-instance mutex of a sandbox (never the owner's real one)."""
    return 'Local\\YtDlpDesktop.Test.' + hashlib.sha1(str(base).encode()).hexdigest()[:12]


def _inside(path, base) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(base).resolve())
    except (OSError, ValueError):
        return False


# (1.7.0, I18N.md 11.1) Tests run in Chinese.  _sandbox.language() changes the language of a
# block (and of the processes started in it); while it does, LANG_OVERRIDE marks the environment
# so that a sandboxed child accepts a variable that is not 'zh' (or absent).
LANG_ENV = 'YTDLP_DESKTOP_LANG'
LANG_OVERRIDE = 'YTDLP_VERIFY_LANG_OVERRIDE'


def _sandbox_variables(base) -> dict:
    return {'LOCALAPPDATA': str(base / 'localappdata'),
            'YTDLP_DESKTOP_APP_DIR': str(base / 'app'),
            'TEMP': str(base / 'tmp'),
            'TMP': str(base / 'tmp'),
            'VERIFY_OUT': str(base / 'out'),
            'XDG_CACHE_HOME': str(base / 'cache'),
            'PYTHONIOENCODING': 'utf-8',
            'YTDLP_DESKTOP_INSTANCE': instance_name(base),
            LANG_ENV: 'zh'}


def _remove_base():
    # Close log files first: Windows cannot delete a file that is still open.
    logging = sys.modules.get('logging')
    if logging is not None:
        try:
            logging.shutdown()
        except Exception:
            pass
    shutil.rmtree(BASE, ignore_errors=True)


if RUNNER_MODE:
    BASE = Path(os.environ['YTDLP_VERIFY_SANDBOX']).resolve()
    _problems = [name for name in ('LOCALAPPDATA', 'YTDLP_DESKTOP_APP_DIR', 'TEMP', 'TMP', 'VERIFY_OUT')
                 if not os.environ.get(name) or not _inside(os.environ[name], BASE)]
    if not os.environ.get('YTDLP_DESKTOP_INSTANCE'):
        _problems.append('YTDLP_DESKTOP_INSTANCE')
    if os.environ.get('YTDLP_DESKTOP_STATE'):
        _problems.append('YTDLP_DESKTOP_STATE (must be absent)')
    if os.environ.get(LANG_ENV) != 'zh' and not os.environ.get(LANG_OVERRIDE):
        _problems.append(f'{LANG_ENV} (must be zh)')
    if _problems:
        raise RuntimeError('verification sandbox is not isolated; refusing to run. BASE=%s; offending variables: %s'
                           % (BASE, ', '.join(_problems)))
    REAL_STATE = Path(os.environ.get('YTDLP_VERIFY_REAL_STATE')
                      or Path.home() / 'AppData' / 'Local' / STATE_NAME)
else:
    # The real state folder is computed BEFORE the override; it is never written.
    REAL_STATE = Path(os.environ.get('YTDLP_VERIFY_REAL_STATE')
                      or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / STATE_NAME)
    if os.environ.get('YTDLP_VERIFY_BASE'):
        BASE = Path(os.environ['YTDLP_VERIFY_BASE'])
        BASE.mkdir(parents=True, exist_ok=True)
        BASE = BASE.resolve()
    else:
        BASE = Path(tempfile.mkdtemp(prefix='ytdlp-verify-')).resolve()
        if os.environ.get('YTDLP_VERIFY_KEEP') != '1':
            atexit.register(_remove_base)
    if _inside(BASE, REAL_STATE) or _inside(REAL_STATE, BASE):
        raise RuntimeError(f'verification sandbox {BASE} overlaps the real state folder {REAL_STATE}')
    os.environ.update(_sandbox_variables(BASE))
    os.environ.pop('YTDLP_DESKTOP_STATE', None)
    # Children that import _sandbox (a second sandbox_app.py, a test started by a test)
    # join this sandbox instead of creating their own.
    os.environ['YTDLP_VERIFY_SANDBOX'] = str(BASE)
    os.environ['YTDLP_VERIFY_REAL_STATE'] = str(REAL_STATE)

# Children started from here (collect worker, nested scripts) must not write __pycache__ either.
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
# (1.7.0) Chinese, in this process and in every child, unless a test changed it on purpose.
if not os.environ.get(LANG_OVERRIDE):
    os.environ[LANG_ENV] = 'zh'
OUT = Path(os.environ['VERIFY_OUT'])
for _name in ('LOCALAPPDATA', 'YTDLP_DESKTOP_APP_DIR', 'TEMP', 'TMP', 'VERIFY_OUT', 'XDG_CACHE_HOME'):
    if os.environ.get(_name):
        Path(os.environ[_name]).mkdir(parents=True, exist_ok=True)
# tempfile caches the temp folder it found before TEMP changed.
tempfile.tempdir = None
# The environment variable does not affect the already running process.
sys.dont_write_bytecode = True
if str(SRC) in sys.path:
    sys.path.remove(str(SRC))
sys.path.insert(0, str(SRC))


def out(*parts) -> Path:
    """OUT / parts, with the parent folder created."""
    path = OUT.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """The sandbox's own state folder (LOCALAPPDATA\\YtDlpDesktop inside BASE)."""
    return Path(os.environ['LOCALAPPDATA']) / STATE_NAME


def _state_modules():
    # (2.0.0 S2 P4) engine has no copy of STATE_DIR any more: paths is the one module that holds it
    modules = [module for module in (sys.modules.get('paths'), sys.modules.get('engine'))
               if module is not None and hasattr(module, 'STATE_DIR')]
    if not modules:
        raise RuntimeError('state_patch: import paths (or engine) before redirecting STATE_DIR')
    return modules


def _checked_state(path) -> Path:
    path = Path(path)
    if _inside(path, REAL_STATE) or _inside(REAL_STATE, path):
        raise RuntimeError(f'refusing to point STATE_DIR at the real state folder: {path}')
    return path


class _StatePatch:
    """Redirect STATE_DIR on every loaded module that has it (paths, engine)."""

    def __init__(self, path):
        self.path = _checked_state(path)
        self._saved = []

    def start(self):
        if self._saved:
            raise RuntimeError('state_patch is already started')
        for module in _state_modules():
            self._saved.append((module, module.STATE_DIR))
            module.STATE_DIR = self.path
        return self.path

    def stop(self):
        while self._saved:
            module, value = self._saved.pop()
            module.STATE_DIR = value

    def __enter__(self):
        return self.start()

    def __exit__(self, *_exc):
        self.stop()
        return False


def state_patch(path) -> _StatePatch:
    """Object with start()/stop(), also a context manager."""
    return _StatePatch(path)


def set_state_dir(path) -> None:
    """Permanent variant of state_patch for script-style tests."""
    path = _checked_state(path)
    for module in _state_modules():
        module.STATE_DIR = path


@contextlib.contextmanager
def language(lang, env=True):
    """(1.7.0) Run a block in ``lang`` ('zh' / 'en'): i18n.set_language(lang), restored afterwards
    (also on an error).

    env=True also sets YTDLP_DESKTOP_LANG=lang, so the processes started in the block run in
    ``lang`` too.  env=False removes the variable for the block, so the app under test behaves like
    a normal start (its stored choice and detection decide; combine with fake_system).
    """
    import i18n
    saved = {name: os.environ.get(name) for name in (LANG_ENV, LANG_OVERRIDE)}
    previous = i18n._current
    try:
        os.environ[LANG_OVERRIDE] = '1'
        if env:
            os.environ[LANG_ENV] = lang
        else:
            os.environ.pop(LANG_ENV, None)
        i18n.set_language(lang)
        yield lang
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        i18n.set_language(previous)


class _FakeWindows:
    """Stand-in for i18n._win32: fixed answers, or an exception (a class or an instance) raised."""

    def __init__(self, ui_langid, locale):
        self._ui, self._locale = ui_langid, locale

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException) or (isinstance(value, type) and issubclass(value, BaseException)):
            raise value
        return value

    def ui_language(self):
        return self._answer(self._ui)

    def locale_name(self):
        return self._answer(self._locale)


@contextlib.contextmanager
def fake_system(ui_langid, locale):
    """(1.7.0) Make i18n.detect() see this Windows display language (LANGID, e.g. 0x0409) and user
    locale (e.g. 'zh-CN'); either may be an exception to raise.  Restored afterwards."""
    import i18n
    saved = i18n._win32
    i18n._win32 = _FakeWindows(ui_langid, locale)
    try:
        yield i18n._win32
    finally:
        i18n._win32 = saved


def app_args(smoke_url=None, smoke_output=None) -> argparse.Namespace:
    """The namespace app.main() would hand to StudioApp."""
    return argparse.Namespace(self_test=None, smoke_url=smoke_url, smoke_output=smoke_output,
                              smoke_archive=False, collect_worker=None)


def fixture_site():
    """Serve verification/fixture on 127.0.0.1:0 from a daemon thread; returns (base URL, server)."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # A cancelled download drops its connection; that is not a test failure.
            if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                super().handle_error(request, client_address)

    server = Server(('127.0.0.1', 0), partial(Handler, directory=str(ROOT / 'verification' / 'fixture')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}', server


def write_report(name: str, data: dict) -> Path:
    """OUT / name as UTF-8 JSON; run_tests.py collects these into build/verify/report.json."""
    path = out(name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def save_unfinished(urls, options) -> Path:
    """(2.0.0 S2 P4) What engine.store_failed_job (a 1.5.1 wrapper, gone) did, for tests that set up an
    unfinished list: the list replaced by one 'failed' entry per link, as the engine writes them
    (unfinished.entry with engine._entry_options; the link's referer).  Returns the file's path."""
    import engine
    import paths
    import unfinished
    unfinished.save([unfinished.entry(url, engine._entry_options(options), 'failed', playlist=options.playlist,
                                      referer=options.referers.get(url, ''))
                     for url in dict.fromkeys(urls)])
    return paths.failed_file()


def write_settings(**values) -> Path:
    """Sandbox settings.json for a non-smoke StudioApp: no update check, no clipboard hint."""
    path = state_dir() / 'settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'check_engine_updates': False, 'clipboard_hint': False, **values}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def assert_isolated() -> None:
    """Raise unless the app modules resolve their state and app folders inside the sandbox."""
    import paths
    checks = [('paths.STATE_DIR', paths.STATE_DIR), ('paths.APP_DIR', paths.APP_DIR),
              ('tempfile.gettempdir()', tempfile.gettempdir())]
    for label, value in checks:
        if not _inside(value, BASE):
            raise RuntimeError(f'not isolated: {label} = {value} is outside the sandbox {BASE}')
        if _inside(value, REAL_STATE):
            raise RuntimeError(f'not isolated: {label} = {value} is the real state folder')


# --- golden files -------------------------------------------------------------------------

def path_forms(path) -> list[str]:
    """The spellings under which one folder can appear in output (as given, and resolved)."""
    forms = [str(path)]
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = forms[0]
    if resolved not in forms:
        forms.append(resolved)
    return forms


def normalise(value, replacements):
    """Replace machine-specific strings in a JSON-like value.

    ``replacements`` is a list of (text, token); longer texts are replaced first,
    so a folder inside another folder keeps its own token.
    """
    table = sorted(((str(text), token) for text, token in replacements if str(text)),
                   key=lambda pair: len(pair[0]), reverse=True)

    def walk(item):
        if isinstance(item, str):
            for text, token in table:
                item = item.replace(text, token)
            return item
        if isinstance(item, dict):
            return {walk(key) if isinstance(key, str) else key: walk(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(entry) for entry in item]
        return item

    return walk(value)


def golden_text(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def _differing_entries(expected, actual) -> str:
    """Names of the top-level golden entries (cases, scenarios, steps) that differ."""
    missing = object()
    if isinstance(expected, dict) and isinstance(actual, dict):
        names = [str(key) for key in sorted(set(expected) | set(actual))
                 if expected.get(key, missing) != actual.get(key, missing)]
    elif isinstance(expected, list) and isinstance(actual, list):
        names = []
        for index in range(max(len(expected), len(actual))):
            old = expected[index] if index < len(expected) else missing
            new = actual[index] if index < len(actual) else missing
            if old != new:
                entry = new if isinstance(new, dict) else old
                label = entry.get('step') if isinstance(entry, dict) else None
                names.append(f'#{index} {label}' if label else f'#{index}')
    else:
        return ''
    shown = names[:30]
    return (f'{len(names)} entries differ: ' + '; '.join(shown)
            + (f'; ... {len(names) - len(shown)} more' if len(names) > len(shown) else ''))


# --- window-test helpers (2.0.0 final review: one copy instead of one per test file) --------

def pump(app, condition=None, timeout=5.0, step=0.01):
    """Run ``app``'s event loop for up to ``timeout`` s, sleeping ``step`` s between rounds.  With ``condition``:
    stop as soon as it is true and return whether it became true; without one: run the whole time, return True."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(step)
    return condition is None


def no_network(*_args, **_kwargs):
    """Stands in for updater.check_latest: a window test never goes online."""
    raise AssertionError('updater.check_latest must not be called in this test')


def no_clipboard(self, *_args, **_kwargs):
    """Stands in for StudioApp.clipboard_get: Tk's own error for an empty clipboard."""
    import tkinter
    raise tkinter.TclError('CLIPBOARD selection doesn\'t exist or form "STRING" not defined')


# --- catalog texts (2.0.0 S3) ---------------------------------------------------------------
# A test takes an interface text by its key, never by copying it: the text lives only in its
# src/i18n_<page>.py, so changing it changes one line (S3-DESIGN 4.3, 4.4).

def zh_t(key, **values) -> str:
    """The zh text of catalog ``key`` with its placeholders filled, whatever the running language."""
    import i18n
    return i18n._t_in('zh', key, values)


def en_t(key, **values) -> str:
    """The en text of catalog ``key`` with its placeholders filled, whatever the running language."""
    import i18n
    return i18n._t_in('en', key, values)


def zh_head(key) -> str:
    """The zh text of catalog ``key`` up to its first placeholder (the start of a log line of that text)."""
    import string
    import i18n
    names = {field for _literal, field, _spec, _conv in string.Formatter().parse(i18n.CATALOG['zh'][key]) if field}
    return zh_t(key, **{name: '\0' for name in names}).split('\0')[0]


_KEYED_INDEX = None
# (S3 review fix) Record time only: a text that is no catalog text as a whole is split at these joiners and its parts
# are keyed one by one ('02  ·  等待解析' -> {"cat": ["02", "  ·  ", {"zh": "download.detail.waiting"}]}); a part
# that is a catalog text only with the joiner in front ('2  /  跳过 1') is keyed with it.
SPLITS = ('  ·  ', '  /  ', ' · ')
_MAX_RENDERINGS = 64


def _keyed_index():
    """(plain, templates, lengths) built once from i18n.CATALOG.

    plain: shown text of a key without placeholders -> [(lang, key)];
    templates: first literal part -> [(lang, key, compiled fullmatch pattern, names)], only templates
    whose literal parts have 2 characters or more and that use each placeholder once; one that is the same in
    both languages is taken (as zh) only with 6 literal characters or more ('{reason} · {format}' names no language
    and would make every text with a ' · ' ambiguous; the progress line '{percent} · {done} / {total} · …' does not);
    lengths: the distinct lengths of those first literal parts (the prefix index).
    """
    global _KEYED_INDEX
    if _KEYED_INDEX is None:
        import re
        import string
        import i18n
        formatter = string.Formatter()
        plain, templates = {}, {}
        for lang in i18n.LANGUAGES:
            for key, text in i18n.CATALOG[lang].items():
                same = all(i18n.CATALOG[other].get(key) == text for other in i18n.LANGUAGES)
                try:
                    parts = list(formatter.parse(text))
                except ValueError:
                    continue
                names = [field for _literal, field, _spec, _conv in parts if field is not None]
                literals = [literal for literal, _field, _spec, _conv in parts]
                own = ''.join(literals)
                # (S4 stage C) a text without a letter or a character of its own (the empty sentence joiner, the
                # dash of an empty cell, ' / ') says nothing in either language: it is not keyed, or every empty
                # widget and every '—' would be recorded as that key
                words = re.search(r'\w', own) is not None
                if not names:
                    if words:
                        plain.setdefault(own, []).append((lang, key))
                    continue
                if len(set(names)) != len(names) or len(own) < 2 or not (words or len(own) >= 6):
                    continue
                # a template that reads the same in both languages is recorded once, as zh, and only when its own
                # characters say something ('{reason} · {format}' would match any text with a ' · ')
                if same and (lang != 'zh' or len(own) < 6):
                    continue
                pattern = ''.join(re.escape(literal) + ('(.+?)' if field is not None else '')
                                  for literal, field, _spec, _conv in parts)
                templates.setdefault(literals[0], []).append((lang, key, re.compile(pattern, re.S), names))
        _KEYED_INDEX = (plain, templates, sorted({len(first) for first in templates}))
    return _KEYED_INDEX


def _keyed_text(text, split=True):
    plain, templates, lengths = _keyed_index()
    found = plain.get(text)
    if found:
        if len(found) == 1:
            lang, key = found[0]
            return {lang: key}
        # the text of several keys (or of one key in both languages): every candidate is kept, and the comparison
        # accepts the text of any of them, so changing one of them changes no other's record
        node = {}
        for lang, key in sorted(found):
            node.setdefault(lang, []).append(key)
        return node
    keyed_whole = _keyed_template(text, plain, templates, lengths)
    if keyed_whole is not None or not split:
        return text if keyed_whole is None else keyed_whole
    for joiner in SPLITS:
        if joiner in text:
            parts = text.split(joiner)
            joined = [_keyed_text(parts[0])]
            for part in parts[1:]:
                alone = _keyed_text(part)
                with_joiner = _keyed_text(joiner + part, split=False) if isinstance(alone, str) else joiner + part
                # a template that starts with the joiner ('  /  跳过 {skipped}') takes it along
                joined += [with_joiner] if not isinstance(with_joiner, str) else [joiner, alone]
            if any(not isinstance(part, str) for part in joined):
                return {'cat': joined}
    return text


def _keyed_template(text, plain, templates, lengths):
    """{lang: key, 'p': {...}} of the one template that explains ``text`` best, else None."""
    matches = []
    for length in lengths:
        if length > len(text):
            break
        for lang, key, pattern, names in templates.get(text[:length], ()):
            match = pattern.fullmatch(text)
            if match:
                fixed = len(text) - sum(len(value) for value in match.groups())
                matches.append((fixed, lang, key, dict(zip(names, match.groups()))))
    # Several templates match: the one that explains the most of the text with its own words wins
    # ('成功 0  /  未完成 1  /  共 1' is the longer of two such templates, not the shorter with done='0  /  未完成 1');
    # a tie (two keys with the same words) keeps the text as it is.
    best = [match for match in matches if match[0] == max((m[0] for m in matches), default=0)]
    if len(best) != 1:
        return None
    _fixed, lang, key, values = best[0]
    return {lang: key, 'p': {name: _keyed_text(value) for name, value in values.items()}}


def keyed(data):
    """``data`` with every string value that is a catalog text written as its key (S3-DESIGN 4.4).  Used only when
    a golden is recorded; the comparison renders the golden instead (``rendered``).

    A string equal to the shown text of exactly one key and language becomes {"zh"|"en": key}, of several keys
    {"zh": [key, ...]} (any of them matches later); else a string that fully matches a template becomes
    {"zh"|"en": key, "p": {name: keyed(value)}} (of several matching templates the one with the most literal
    characters, when it is one); else a string with a joiner of SPLITS some part of which is a catalog text becomes
    {"cat": [part, joiner, part, ...]}.  Everything else (user data, paths, joined clauses) stays as it is.
    Dictionary keys are not changed.  So a golden keeps which text with which values every state shows, and not
    the wording, which lives only in the catalog.
    """
    if isinstance(data, str):
        return _keyed_text(data)
    if isinstance(data, dict):
        return {key: keyed(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [keyed(value) for value in data]
    return data


_keyed = keyed


def _key_node(value):
    """True for a recorded catalog text: {"zh"|"en": key or [key, ...], "p": {...}?} or {"cat": [...]}."""
    if not isinstance(value, dict) or not value:
        return False
    if set(value) == {'cat'}:
        return isinstance(value['cat'], list)
    langs = set(value) - {'p'}
    return bool(langs) and langs <= {'zh', 'en'} and isinstance(value.get('p', {}), dict) and all(
        isinstance(value[lang], str) or (isinstance(value[lang], list) and value[lang]
                                         and all(isinstance(key, str) for key in value[lang])) for lang in langs)


def _renderings(value) -> list:
    """The texts a recorded value stands for under the current catalog (a few, for candidates and joins)."""
    import itertools
    import i18n
    if isinstance(value, str):
        return [value]
    if not _key_node(value):
        return [None]
    if 'cat' in value:
        options = [_renderings(part) for part in value['cat']]
        found = [''.join(str(piece) for piece in combo) for combo in itertools.islice(itertools.product(*options),
                                                                                       _MAX_RENDERINGS)]
    else:
        params = value.get('p', {})
        names = sorted(params)
        found = []
        for lang in ('zh', 'en'):
            keys = value.get(lang)
            for key in ([keys] if isinstance(keys, str) else keys or ()):
                for combo in itertools.islice(itertools.product(*(_renderings(params[name]) for name in names)),
                                              _MAX_RENDERINGS):
                    found.append(i18n._t_in(lang, key, dict(zip(names, combo))))
    unique = []
    for text in found:
        if text not in unique:
            unique.append(text)
    return unique or [None]


def rendered(expected, actual):
    """``expected`` (a recorded golden) with every recorded catalog text rendered by the current catalog: where
    ``actual`` holds one of the texts a record stands for, that text, else the first of them.  So
    ``rendered(golden, actual) == actual`` exactly when every text is the text of the recorded key with the
    recorded values (the wording comes from the catalog of this run), and a diff shows texts, not keys."""
    if isinstance(actual, str) and _key_node(expected):
        options = _renderings(expected)
        return actual if actual in options else options[0]
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: rendered(value, actual[key]) if key in actual else rendered(value, None)
                for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list):
        return [rendered(value, actual[index] if index < len(actual) else None)
                for index, value in enumerate(expected)]
    if _key_node(expected):
        return _renderings(expected)[0]
    if isinstance(expected, dict):
        return {key: rendered(value, None) for key, value in expected.items()}
    if isinstance(expected, list):
        return [rendered(value, None) for value in expected]
    return expected


def check_golden(test, name: str, data, keyed=False) -> None:
    """Compare ``data`` with verification/golden/<name>.

    The golden is rewritten only with env GOLDEN_UPDATE=1 and only when the file is run
    directly (never through run_tests.py). Otherwise the parsed JSON is compared with
    assertEqual and a unified diff of the pretty-printed text is printed on mismatch.
    (2.0.0 S3, S3 review fix) keyed=True: the golden is recorded as keyed(data) (catalog texts as their keys, the
    one place a text is looked up), and compared by rendering it with the current catalog (``rendered``): no
    wording change, and no text that becomes equal to another, changes the result.
    """
    path = GOLDEN / name
    text = golden_text(data)
    actual = json.loads(text)
    if os.environ.get('GOLDEN_UPDATE') == '1':
        if RUNNER_MODE:
            test.fail('GOLDEN_UPDATE=1 is ignored under run_tests.py; run the test file directly to rewrite a golden')
        if keyed:
            record = _keyed(actual)
            back = rendered(record, actual)
            test.assertEqual(back, actual, f'{name}: the keyed record does not render back to this run '
                                           '(_sandbox.keyed / rendered disagree); the golden was not written')
            text = golden_text(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(f'golden updated: {path}', flush=True)
        return
    if not path.is_file():
        test.fail(f'golden file is missing: {path} (record it with GOLDEN_UPDATE=1, running this file directly)')
    expected = json.loads(path.read_text(encoding='utf-8'))
    if keyed:
        expected = rendered(expected, actual)
    if expected != actual:
        diff = list(difflib.unified_diff(golden_text(expected).splitlines(), text.splitlines(),
                                         f'golden/{name}', 'this run', lineterm='', n=3))
        summary = _differing_entries(expected, actual)
        print(f'{name}: this run differs from the golden. {summary}', flush=True)
        print('\n'.join(diff), flush=True)
        shown = diff[:120]
        more = f'\n... {len(diff) - len(shown)} more diff lines on stdout' if len(diff) > len(shown) else ''
        test.assertEqual(expected, actual,
                         f'\n{name}: this run differs from the golden. {summary}\n'
                         'Unified diff (golden -> this run):\n' + '\n'.join(shown) + more)
