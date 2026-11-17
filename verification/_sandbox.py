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
    modules = [module for module in (sys.modules.get('paths'), sys.modules.get('engine'))
               if module is not None and hasattr(module, 'STATE_DIR')]
    if not modules:
        raise RuntimeError('state_patch: import engine (or paths) before redirecting STATE_DIR')
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


def write_settings(**values) -> Path:
    """Sandbox settings.json for a non-smoke StudioApp: no update check, no clipboard hint."""
    path = state_dir() / 'settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'check_engine_updates': False, 'clipboard_hint': False, **values}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def assert_isolated() -> None:
    """Raise unless the app modules resolve their state and app folders inside the sandbox."""
    import engine
    checks = [('engine.STATE_DIR', engine.STATE_DIR), ('engine.APP_DIR', engine.APP_DIR),
              ('tempfile.gettempdir()', tempfile.gettempdir())]
    paths = sys.modules.get('paths')
    if paths is not None:
        checks += [(f'paths.{name}', getattr(paths, name)) for name in ('STATE_DIR', 'APP_DIR') if hasattr(paths, name)]
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


def check_golden(test, name: str, data) -> None:
    """Compare ``data`` with verification/golden/<name>.

    The golden is rewritten only with env GOLDEN_UPDATE=1 and only when the file is run
    directly (never through run_tests.py). Otherwise the parsed JSON is compared with
    assertEqual and a unified diff of the pretty-printed text is printed on mismatch.
    """
    path = GOLDEN / name
    text = golden_text(data)
    actual = json.loads(text)
    if os.environ.get('GOLDEN_UPDATE') == '1':
        if RUNNER_MODE:
            test.fail('GOLDEN_UPDATE=1 is ignored under run_tests.py; run the test file directly to rewrite a golden')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(f'golden updated: {path}', flush=True)
        return
    if not path.is_file():
        test.fail(f'golden file is missing: {path} (record it with GOLDEN_UPDATE=1, running this file directly)')
    expected = json.loads(path.read_text(encoding='utf-8'))
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
