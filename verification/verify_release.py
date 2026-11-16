"""Release gate for the staged onedir build (1.6.0 WP6).

    python -B verification\\verify_release.py [--stage PATH]

PATH defaults to build\\package-1.6.0\\YT-DLP下载器.  The gate fails with a clear message on the first violated
check and cannot pass on stale evidence:
  1. layout: YT-DLP下载器.exe + _internal\\; no av*.dll / sw*.dll directly in _internal; _internal\\vendor\\bin holds
     exactly the files the bundled vendor\\manifest.json lists (no hard-coded FFmpeg DLL names; ffmpeg.exe,
     ffprobe.exe, deno.exe always required), each with the manifest's SHA-256; _internal\\vendor\\yt-dlp.exe matches
     the manifest; manifest.json, licenses, the patchright node.exe and PIL's FreeType module are present; then every
     file of the stage is listed (relative path, size, SHA-256) before the program first runs;
  2. --self-test twice: sandboxed (LOCALAPPDATA and YTDLP_DESKTOP_APP_DIR in a temp folder), then with only
     LOCALAPPDATA redirected, where app_dir must be the stage folder and state_dir must lie in the temp folder;
  3. download smoke through the packaged exe against an in-process fixture server (with archive);
  4. GUI smoke: visible window titled "... YT-DLP 下载器" within 10 s, WM_CLOSE, exit code 0 within 10 s; the
     program's own log must not report that icons could not be drawn;
  5. build\\verify\\report.json of a full run (no -k) with the tiers all and exe, every result passed or skipped,
     app_version 1.6.0, source_sha256 = the sources now, exe sha256 = the staged exe; build\\package-1.6.0\\build.json
     ties the staged exe to the same sources;
  6. the stage must still match the list of step 1 file by file (the program's runs did not change it);
  7. writes build\\verify\\release-1.6.0.json with that list under "file_list" ({count, size, sha256 of the list,
     files}) and prints it without the per-file entries.  install_release.py checks the stage and its copy against
     the list.
None of the old verification\\*.json reports is read.  Standard library only; no src import.  The helpers
(self_test, download_smoke, gui_smoke, fixture_server, sandbox_environment) are also used by install_release.py.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
VERSION = '1.6.0'
APP_NAME = 'YT-DLP下载器'
EXE_NAME = APP_NAME + '.exe'
TITLE_SUFFIX = 'YT-DLP 下载器'
DEFAULT_STAGE = BUILD / f'package-{VERSION}' / APP_NAME
BUILD_RECORD = BUILD / f'package-{VERSION}' / 'build.json'
REPORT = BUILD / 'verify' / 'report.json'
RELEASE = BUILD / 'verify' / f'release-{VERSION}.json'
FIXTURE = Path(__file__).resolve().parent / 'fixture'
# The vendor/bin file list comes from vendor/manifest.json (written by prepare_vendor.py); these are always required.
VENDOR_REPOSITORIES = ('yt-dlp/yt-dlp', 'denoland/deno', 'yt-dlp/ffmpeg-builds')
REQUIRED_VENDOR_FILES = ('yt-dlp.exe', 'bin/deno.exe', 'bin/ffmpeg.exe', 'bin/ffprobe.exe')
COMPONENTS = ('yt-dlp', 'ffmpeg', 'ffprobe', 'deno', 'scrapling')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
WM_CLOSE = 0x0010
# Environment variables of the test sandbox and of this project that must not reach the program under test.
FOREIGN_VARIABLES = ('YTDLP_DESKTOP_STATE', 'YTDLP_DESKTOP_APP_DIR', 'YTDLP_DESKTOP_INSTANCE', 'YTDLP_VERIFY_SANDBOX',
                     'YTDLP_VERIFY_BASE', 'YTDLP_VERIFY_KEEP', 'YTDLP_VERIFY_REAL_STATE', 'VERIFY_OUT', 'VERIFY_EXE',
                     'VERIFY_NET', 'GOLDEN_UPDATE', 'PYINSTALLER_RESET_ENVIRONMENT', '_MEIPASS2', '_PYI_APPLICATION_HOME_DIR',
                     '_PYI_ARCHIVE_FILE', '_PYI_PARENT_PROCESS_LEVEL')


class GateError(Exception):
    """A violated check; str() names it."""


def check(condition, message: str) -> None:
    if not condition:
        raise GateError(message)


def say(text: str) -> None:
    print(f'[gate] {text}', flush=True)


# ------------------------------------------------------------------ small helpers

def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def folder_stats(folder) -> tuple:
    """(file count, total bytes) below a folder."""
    count = size = 0
    for current, _folders, files in os.walk(folder):
        for name in files:
            count += 1
            size += os.path.getsize(os.path.join(current, name))
    return count, size


def same_path(first, second) -> bool:
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except OSError:
        return False


def inside(path, folder) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except OSError:
        return False


def sandbox_environment(folder, redirect_app_dir=True) -> dict:
    """Environment for one start of the program: its state, temp and (optionally) app folder in ``folder``.

    The single-instance name is a fresh test name, so neither the owner's running window nor another start
    is ever involved.
    """
    folder = Path(folder)
    environment = os.environ.copy()
    for name in FOREIGN_VARIABLES:
        environment.pop(name, None)
    for name in ('localappdata', 'tmp', 'app'):
        (folder / name).mkdir(parents=True, exist_ok=True)
    environment.update({'LOCALAPPDATA': str(folder / 'localappdata'), 'TEMP': str(folder / 'tmp'),
                        'TMP': str(folder / 'tmp'),
                        'YTDLP_DESKTOP_INSTANCE': 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex[:12]})
    if redirect_app_dir:
        environment['YTDLP_DESKTOP_APP_DIR'] = str(folder / 'app')
    return environment


def end_process_tree(process) -> None:
    """End a process this module started, with everything it started (by its pid only)."""
    if process is None or process.poll() is not None:
        return
    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=NO_WINDOW)
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()


def run_program(command, environment, timeout) -> int:
    process = subprocess.Popen([str(part) for part in command], env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        return process.wait(timeout)
    except subprocess.TimeoutExpired:
        end_process_tree(process)
        raise GateError(f'{Path(command[0]).name} {" ".join(map(str, command[1:2]))}: no result after {timeout} s')
    except BaseException:
        end_process_tree(process)
        raise


def fixture_server(folder=FIXTURE):
    """Serve ``folder`` on 127.0.0.1 from a daemon thread; returns (base URL, server)."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                super().handle_error(request, client_address)

    check((Path(folder) / 'fixture.mp4').is_file(), f'fixture file missing: {Path(folder) / "fixture.mp4"}')
    server = Server(('127.0.0.1', 0), partial(Handler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}', server


# ------------------------------------------------------------------ the program's own checks

def self_test(exe, folder, redirect_app_dir=True, arguments=(), name='diag', timeout=300) -> dict:
    """``<exe> --self-test <folder>\\<name>.json [arguments]`` in a sandboxed environment; the parsed report."""
    folder = Path(folder)
    report = folder / f'{name}.json'
    environment = sandbox_environment(folder / name, redirect_app_dir)
    started = time.monotonic()
    code = run_program([exe, '--self-test', report, *arguments], environment, timeout)
    seconds = round(time.monotonic() - started, 2)
    check(report.is_file(), f'--self-test ({name}) wrote no report (exit code {code})')
    data = json.loads(report.read_text(encoding='utf-8'))
    check(code == 0 and data.get('status') == 'passed',
          f'--self-test ({name}) failed: exit code {code}, status {data.get("status")!r}\n{data.get("traceback", "")}')
    data['_seconds'] = seconds
    data['_state_root'] = environment['LOCALAPPDATA']
    return data


def check_self_test_report(data, stage) -> None:
    check(data.get('app_version') == VERSION, f'self-test app_version is {data.get("app_version")!r}, expected {VERSION}')
    resource = str(data.get('resource_root', ''))
    check(resource.endswith('\\_internal'), f'resource_root does not end in \\_internal: {resource}')
    check(same_path(resource, Path(stage) / '_internal'), f'resource_root {resource} is not the stage\'s _internal')
    components = data.get('components') or {}
    missing = [name for name in COMPONENTS if name not in components]
    check(not missing, 'self-test components missing: ' + ', '.join(missing))
    failed = [name for name in COMPONENTS if components[name].get('returncode') != 0]
    check(not failed, 'self-test components failed: ' + ', '.join(failed))


def download_smoke(exe, folder, base_url, timeout=300) -> dict:
    """Packaged download (WAV conversion + archive) of the fixture; returns the ``download_smoke`` payload."""
    output = Path(folder) / 'smoke-output'
    output.mkdir(parents=True, exist_ok=True)
    data = self_test(exe, folder, True, ('--smoke-url', base_url + '/fixture.mp4', '--smoke-output', output,
                                         '--smoke-archive'), name='download', timeout=timeout)
    done = data.get('download_smoke') or {}
    check(done.get('success') == 1, f'download smoke: success is {done.get("success")!r}, expected 1')
    files = [Path(path) for path in done.get('files') or []]
    check(files and all(path.is_file() for path in files), f'download smoke: no downloaded file exists: {files}')
    check(data.get('archive_entries'), 'download smoke: archive_entries is empty')
    check(not done.get('archive_errors'), f'download smoke: archive errors {done.get("archive_errors")}')
    return {'success': done['success'], 'files': [path.name for path in files], 'seconds': data['_seconds'],
            'archive_entries': len(data['archive_entries'])}


def _visible_windows(pid) -> list:
    """Titles of the visible top-level windows of a process."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    found = []
    procedure = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [procedure, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

    def visit(hwnd, _parameter):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            found.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(procedure(visit), None)
    return found


def gui_smoke(exe, folder, smoke_url, window_timeout=10, close_timeout=10) -> dict:
    """Start the GUI sandboxed in smoke mode, wait for its window, close it with WM_CLOSE.

    Returns {'time_to_window': seconds, 'title': ...}.  The window lives well under a second after it appeared.
    """
    folder = Path(folder) / 'gui'
    output = folder / 'smoke-output'
    output.mkdir(parents=True, exist_ok=True)
    environment = sandbox_environment(folder, True)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    started = time.perf_counter()
    process = subprocess.Popen([str(exe), '--smoke-url', smoke_url, '--smoke-output', str(output)], env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        window = None
        while time.perf_counter() - started < window_timeout:
            matches = [(hwnd, title) for hwnd, title in _visible_windows(process.pid) if title.endswith(TITLE_SUFFIX)]
            if matches:
                window = matches[0]
                break
            check(process.poll() is None, f'GUI smoke: the program ended (exit code {process.returncode}) before '
                                          'its window appeared')
            time.sleep(0.02)
        seconds = round(time.perf_counter() - started, 2)
        check(window is not None, f'GUI smoke: no visible window titled "...{TITLE_SUFFIX}" within {window_timeout} s')
        user32.PostMessageW(window[0], WM_CLOSE, None, None)
        try:
            code = process.wait(close_timeout)
        except subprocess.TimeoutExpired:
            raise GateError(f'GUI smoke: the window did not close within {close_timeout} s after WM_CLOSE')
        check(code == 0, f'GUI smoke: exit code {code} after WM_CLOSE')
    finally:
        end_process_tree(process)
    log = Path(environment['LOCALAPPDATA']) / 'YtDlpDesktop' / 'download.log'
    check(log.is_file(), f'GUI smoke: the program wrote no log ({log})')
    text = log.read_text(encoding='utf-8', errors='replace')
    check('icons unavailable' not in text, 'GUI smoke: the packaged program could not draw its icons '
                                           '(PIL ImageDraw/ImageFont or FreeType missing):\n' + text[-2000:])
    errors = [line for line in text.splitlines() if re.match(r'^\S+ \S+ E YtDlpDesktop', line)]
    check(not errors, 'GUI smoke: the program logged errors:\n' + '\n'.join(errors[:20]))
    return {'time_to_window': seconds, 'title': window[1]}


# ------------------------------------------------------------------ gate steps

def manifest_vendor_files(manifest) -> dict:
    """{path inside vendor ('yt-dlp.exe', 'bin/x.dll'): sha256} from the tool entries' "files"; GateError when the
    manifest has no file list (written by an older prepare_vendor.py) or lacks a required program."""
    check(isinstance(manifest, list) and all(isinstance(item, dict) for item in manifest),
          'the vendor manifest is not a list of tool entries')
    found = {}
    for repository in VENDOR_REPOSITORIES:
        entry = next((item for item in manifest if str(item.get('repository', '')).lower() == repository), None)
        files = entry.get('files') if entry else None
        check(isinstance(files, dict) and files,
              f'the vendor manifest has no file list for {repository}; run prepare_vendor.py --record-local '
              '(or prepare_vendor.py) and build again')
        found.update({str(path): str(digest).lower() for path, digest in files.items()})
    absent = [path for path in REQUIRED_VENDOR_FILES if path not in found]
    check(not absent, 'the vendor manifest does not list ' + ', '.join(absent))
    return found


def stage_file_list(stage) -> list:
    """Every file below the staged folder: [{path ('/'-separated, relative), size, sha256}], sorted by path."""
    stage = Path(stage)
    found = []
    for current, folders, files in os.walk(stage):
        folders.sort()
        relative = Path(current).relative_to(stage)
        for name in sorted(files):
            path = Path(current) / name
            found.append({'path': (relative / name).as_posix(), 'size': path.stat().st_size,
                          'sha256': file_sha256(path)})
    return sorted(found, key=lambda item: item['path'])


def file_list_sha256(files) -> str:
    """SHA-256 over the canonical JSON of a file list: binds the list inside the release record."""
    text = json.dumps([[item['path'], item['size'], item['sha256']] for item in files], ensure_ascii=False,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_list_differences(expected, actual) -> dict:
    """{'added': [...], 'missing': [...], 'changed': [...]} between two file lists (paths compared exactly)."""
    before = {item['path']: (item['size'], item['sha256']) for item in expected}
    after = {item['path']: (item['size'], item['sha256']) for item in actual}
    return {'added': sorted(set(after) - set(before)), 'missing': sorted(set(before) - set(after)),
            'changed': sorted(path for path in set(before) & set(after) if before[path] != after[path])}


def check_layout(stage: Path, source_manifest=ROOT / 'vendor' / 'manifest.json') -> dict:
    exe = stage / EXE_NAME
    internal = stage / '_internal'
    check(exe.is_file(), f'missing {exe}')
    check(internal.is_dir(), f'missing {internal}')
    doubled = sorted(item.name for item in internal.iterdir()
                     if item.is_file() and re.match(r'^(av|sw).*\.dll$', item.name, re.I))
    check(not doubled, 'FFmpeg DLLs directly in _internal (bundled twice): ' + ', '.join(doubled))
    manifest_path = internal / 'vendor' / 'manifest.json'
    check(manifest_path.is_file(), f'missing {manifest_path}')
    if source_manifest is not None and Path(source_manifest).is_file():
        check(manifest_path.read_bytes() == Path(source_manifest).read_bytes(),
              'the staged vendor manifest differs from vendor\\manifest.json')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except ValueError as error:
        raise GateError(f'the staged vendor manifest cannot be read: {error}')
    listed = manifest_vendor_files(manifest)
    expected_bin = sorted(path[4:] for path in listed if path.startswith('bin/'))
    bin_folder = internal / 'vendor' / 'bin'
    check(bin_folder.is_dir(), f'missing {bin_folder}')
    present = sorted(item.name for item in bin_folder.iterdir())
    check(present == expected_bin and all((bin_folder / name).is_file() for name in present),
          f'_internal\\vendor\\bin must hold exactly the files the manifest lists ({", ".join(expected_bin)}); '
          f'it holds {", ".join(present)}')
    changed = [path for path, digest in sorted(listed.items())
               if not (internal / 'vendor' / Path(path)).is_file()
               or file_sha256(internal / 'vendor' / Path(path)) != digest]
    check(not changed, '_internal\\vendor files differ from the SHA-256 in the manifest: ' + ', '.join(changed))
    entry = next((item for item in manifest if item.get('repository') == 'yt-dlp/yt-dlp'), {})
    engine = internal / 'vendor' / 'yt-dlp.exe'
    check(engine.is_file(), f'missing {engine}')
    engine_hash = file_sha256(engine)
    check(engine_hash == str(entry.get('sha256', '')).lower(),
          f'_internal\\vendor\\yt-dlp.exe sha256 {engine_hash} differs from the manifest ({entry.get("sha256")})')
    check((internal / 'vendor' / 'licenses').is_dir(), 'missing _internal\\vendor\\licenses')
    leftovers = [item.name for pattern in ('*.zip', '*.part') for item in (internal / 'vendor').rglob(pattern)]
    check(not leftovers, 'download leftovers in _internal\\vendor: ' + ', '.join(leftovers))
    check((internal / 'patchright' / 'driver' / 'node.exe').is_file(), 'missing _internal\\patchright\\driver\\node.exe')
    check(any((internal / 'PIL').glob('_imagingft*.pyd')), 'missing _internal\\PIL\\_imagingft*.pyd (FreeType)')
    return {'yt-dlp': entry.get('version'), 'yt-dlp_sha256': engine_hash, 'vendor_bin': expected_bin}


def check_evidence(stage: Path, run_tests) -> dict:
    """Step 5: the test report and build.json belong to these sources and this exe."""
    sources = run_tests.source_sha256()
    exe_hash = file_sha256(stage / EXE_NAME)
    check(REPORT.is_file(), f'missing {REPORT}; run build.py (or run_tests.py --all --exe <staged exe>)')
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    check(report.get('filter') is None, f'report.json is a partial run (-k {report.get("filter")!r})')
    tiers = report.get('tiers') or []
    check('all' in tiers and 'exe' in tiers, f'report.json tiers are {tiers}; the gate needs a run with all and exe')
    check(report.get('app_version') == VERSION, f'report.json app_version is {report.get("app_version")!r}')
    check(report.get('source_sha256') == sources,
          'report.json was produced from other sources (source_sha256 differs from src/*.py + app.spec now)')
    check((report.get('exe') or {}).get('sha256') == exe_hash, 'report.json was produced with another exe '
                                                               '(exe.sha256 differs from the staged exe)')
    results = report.get('results') or []
    check(results, 'report.json has no results')
    bad = [f'{item.get("file")} ({item.get("status")})' for item in results
           if item.get('status') not in ('passed', 'skipped')]
    check(not bad, 'report.json has results that did not pass: ' + ', '.join(bad))
    exe_skipped = [item['file'] for item in results if item.get('tier') == 'exe' and item.get('status') != 'passed']
    check(not exe_skipped, 'tier exe was not really exercised: ' + ', '.join(exe_skipped))
    expected = {path.name for path, tier in run_tests.discover() if tier != 'net'}
    missing = sorted(expected - {item.get('file') for item in results})
    check(not missing, 'report.json lacks test files that exist now: ' + ', '.join(missing))
    check(BUILD_RECORD.is_file(), f'missing {BUILD_RECORD}; the staged folder was not made by build.py')
    record = json.loads(BUILD_RECORD.read_text(encoding='utf-8'))
    check(record.get('source_sha256') == sources, 'build.json: the staged exe was built from other sources')
    check(record.get('exe_sha256') == exe_hash, 'build.json: the staged exe is not the one build.py staged')
    return {'report': report, 'record': record, 'sources': sources, 'exe_hash': exe_hash}


def load_run_tests():
    spec = importlib.util.spec_from_file_location('run_tests_for_gate', Path(__file__).resolve().parent / 'run_tests.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def top_level(folder: Path) -> list:
    return sorted((item.name, item.is_dir(), 0 if item.is_dir() else item.stat().st_size) for item in folder.iterdir())


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Release gate for the staged onedir build.')
    parser.add_argument('--stage', default=str(DEFAULT_STAGE), help='staged program folder')
    options = parser.parse_args(argv)
    stage = Path(options.stage).resolve()
    exe = stage / EXE_NAME
    work = Path(tempfile.mkdtemp(prefix='ytdlp-gate-')).resolve()
    server = None
    try:
        # A receipt exists only while the gate it records has passed: a failing run leaves none behind.
        if RELEASE.exists():
            RELEASE.unlink()
        run_tests = load_run_tests()
        say(f'stage {stage}')
        layout = check_layout(stage)
        clock = time.monotonic()
        files = stage_file_list(stage)
        say(f'1 layout ok (vendor\\bin as the manifest lists it: {", ".join(layout["vendor_bin"])}); '
            f'{len(files)} files listed with SHA-256 in {time.monotonic() - clock:.1f} s')

        sandboxed = self_test(exe, work, True, name='self-test')
        check_self_test_report(sandboxed, stage)
        before = top_level(stage)
        located = self_test(exe, work, False, name='self-test-app-dir')
        check_self_test_report(located, stage)
        check(same_path(located.get('app_dir', ''), stage),
              f'with only LOCALAPPDATA redirected app_dir is {located.get("app_dir")}, expected the stage {stage}')
        check(inside(located.get('state_dir', ''), work),
              f'state_dir {located.get("state_dir")} does not lie in the temp folder {work}')
        check(top_level(stage) == before, '--self-test changed the stage folder')
        say(f'2 self-test ok ({sandboxed["_seconds"]} s; app_dir = stage, state_dir in {work})')

        base, server = fixture_server()
        smoke = download_smoke(exe, work, base)
        say(f'3 download smoke ok ({smoke["files"]}, {smoke["seconds"]} s)')

        gui = gui_smoke(exe, work, base + '/fixture.mp4')
        say(f'4 GUI smoke ok (window after {gui["time_to_window"]} s: {gui["title"]})')

        evidence = check_evidence(stage, run_tests)
        say('5 evidence ok (report.json and build.json belong to these sources and this exe)')

        # The record lists the stage as it was before the program ran; it must not have changed since.
        changes = file_list_differences(files, stage_file_list(stage))
        check(not any(changes.values()), 'the stage changed while the gate ran: '
              + '; '.join(f'{kind} {", ".join(paths[:10])}' for kind, paths in changes.items() if paths))
        say('6 the stage is unchanged since step 1 (every file, SHA-256)')
        count, size = folder_stats(stage)
        check((count, size) == (len(files), sum(item['size'] for item in files)), 'file count/size changed')
        report = evidence['report']
        skipped = [item['file'] for item in report['results'] if item.get('status') == 'skipped']
        components = {name: ' '.join(sandboxed['components'][name].get('version') or []) for name in COMPONENTS}
        release = {
            'status': 'passed', 'version': VERSION,
            'exe': {'path': str(exe), 'size': exe.stat().st_size, 'sha256': evidence['exe_hash']},
            'folder': {'path': str(stage), 'size': size, 'files': count},
            'source_sha256': evidence['sources'], 'built': evidence['record'].get('built'),
            'components': components, 'yt-dlp_bundled_sha256': layout['yt-dlp_sha256'],
            'time_to_window': gui['time_to_window'], 'self_test_seconds': sandboxed['_seconds'],
            'download_smoke': smoke,
            'tests': [{'file': item['file'], 'tier': item['tier'], 'status': item['status']}
                      for item in report['results']],
            'skipped': skipped, 'tiers': report['tiers'],
            'report': {'started': report.get('started'), 'finished': report.get('finished')},
            'timestamp': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
            # Every file of the stage; install_release.py checks the stage and its copy against this list.
            'file_list': {'count': len(files), 'size': sum(item['size'] for item in files),
                          'sha256': file_list_sha256(files), 'files': files},
        }
        RELEASE.parent.mkdir(parents=True, exist_ok=True)
        temporary = RELEASE.with_name(RELEASE.name + f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, RELEASE)
        shown = dict(release, file_list={key: value for key, value in release['file_list'].items() if key != 'files'})
        print(json.dumps(shown, ensure_ascii=False, indent=2), flush=True)
        say(f'7 written {RELEASE} (with the list of all {len(files)} files)')
    except GateError as error:
        say(f'FAILED: {error}')
        say(f'temp folder kept for diagnosis: {work}')
        return 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
