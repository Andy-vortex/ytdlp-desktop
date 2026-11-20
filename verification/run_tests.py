"""Sandboxed test runner (1.6.0 WP0).

    .venv\\Scripts\\python.exe -X utf8 -B verification\\run_tests.py [--all] [--exe PATH] [--net] [--only-tier TIER]
                                                                   [-k NAME[,NAME...]] [--quick] [-j N] [--keep] [--list]

(2.0.0 S1) Selection and speed:
  -k a,b      only files whose name contains one of the comma-separated substrings
  --quick     only files marked ``# quick`` (fast, no window, no engine): the check to run after every edit
  --only-tier only that tier (``--only-tier exe --exe PATH`` is build.py's exe pass: no file except the exe tier
              reads VERIFY_EXE, so the other tiers would only run again on the same sources)
  -j N        N files at a time; files marked ``# serial`` (timing, the foreground window, screenshots) run
              one by one after the parallel batch.  Without -j the run is serial, as before.  The release gate
              takes serial runs only.
Markers (``# serial`` / ``# quick``, like ``# tier:``) are comments within the first five lines of a file.

Every verification/test_*.py runs as its own process inside a private sandbox
(LOCALAPPDATA, application folder, TEMP, output folder) that this runner builds,
so a test cannot touch the owner's real %LOCALAPPDATA%\\YtDlpDesktop or leave
files in verification/ even if it forgets ``import _sandbox``.

Tiers (a ``# tier: all|exe|net`` comment within the first five lines of a file):
  default   unit + UI tests, always run
  all       fixture server, real yt-dlp/ffmpeg and Chrome    (--all)
  exe       tests of a packaged exe                           (--exe PATH, sets VERIFY_EXE)
  net       real websites                                     (--net, sets VERIFY_NET=1)

The run fails ("isolation") when a path under verification/ appears, changes (size
or modification time of a file) or disappears, or when the real state folder
changes, and ("sources") when src/*.py or app.spec change while it runs.  Editing a
file under verification/ during a run therefore fails that run.  The result is
written to build\\verify\\report.json:
  app_version, source_sha256 (one SHA256 over the bytes of src/*.py sorted by file
  name, then app.spec), exe, tiers, filter (-k, null for a full run), not_run,
  started, finished, results [{file, tier, status, seconds, tail, leftover_processes}],
  reports {"<test stem>/<path inside its out folder>": parsed JSON},
  (2.0.0) quick (--quick), only_tier (--only-tier or null), jobs (-j, 1 when serial),
  tests_sha256 (one SHA256 over the test code and data the run used: every *.py under verification/ and
  every file under verification/golden and verification/fixture, by relative path and content, then
  vendor/manifest.json; taken when the run starts -- the isolation check fails a run during which
  verification/ changes).
The results are in the order of the selection whatever -j is, so a parallel report has the content of a serial one.
(2.0.0) The release gate reads two copies of a report, written beside report.json only by serial runs without
-k and --quick: report-source.json by a run of the tiers default and all without --exe and --only-tier;
report-exe.json by a run with --exe that includes the tier exe.
Every test file starts with YTDLP_DESKTOP_LANG=zh (1.7.0: the tests run in Chinese).
(1.8.0, MESSAGES.md 7.3) Every test file except those of GUARD_OFF also runs with
YTDLP_DESKTOP_MSG_GUARD=1: the G2 message guard writes each violation as a JSON line to
msg-guard-<pid>.jsonl in the file's output folder; any such line fails the file (its first five
violations go to the tail), and the report gets msg_guard {files_checked, violations,
exemptions}.
Exit code 0 only when nothing failed.  The sandbox is removed after a green run
(--keep keeps it) and kept, with its path printed, after a failure; the captured
output of every file is in <sandbox>\\_logs.
No third-party imports and no src imports in this process.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ctypes
import datetime
import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
VERIFICATION = ROOT / 'verification'
REPORT = ROOT / 'build' / 'verify' / 'report.json'
VENDOR_MANIFEST = ROOT / 'vendor' / 'manifest.json'
TEST_DATA_FOLDERS = ('golden', 'fixture')      # under VERIFICATION: every file counts in tests_sha256
# (2.0.0) the copies of report.json the release gate reads (see the docstring); beside REPORT
SOURCE_REPORT_NAME = 'report-source.json'
EXE_REPORT_NAME = 'report-exe.json'
STATE_NAME = 'YtDlpDesktop'
APP_IMAGE = 'YT-DLP下载器.exe'
TIER_ORDER = ('default', 'all', 'exe', 'net')
TIER_PATTERN = re.compile(r'^#\s*tier:\s*(all|exe|net)\b')
# (2.0.0) "# serial" (with an optional reason after it) and "# quick" in the first five lines
MARK_PATTERN = re.compile(r'^#\s*(serial|quick)\b')
TIMEOUT = {'default': 900, 'all': 1800, 'exe': 900, 'net': 900}
TAIL_LINES = 40
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# Places an unsandboxed source run would write to (the leak the 1.6.0 audit reproduced).
LEAK_SIGNATURES = (ROOT / 'data', ROOT / '下载文件', ROOT / 'build' / 'collection-temp',
                   Path(ROOT.anchor) / '.yt-dlp-cache')
# (1.8.0, MESSAGES.md 7.3) The G2 message guard: on for every test file except these (a file is added
# only by a spec change).
MSG_GUARD = 'YTDLP_DESKTOP_MSG_GUARD'
GUARD_OFF = {'test_golden_ui.py': 'replays the recorded events of golden/ui.json with plain strings',
             'test_golden_runonce.py': 'byte-identical golden test; its FakeArchive raises a plain Chinese text '
                                       'that becomes a raw param'}
GUARD_FILES = 'msg-guard-*.jsonl'
GUARD_TAIL = 5


def real_state_dir() -> Path:
    inherited = os.environ.get('YTDLP_VERIFY_REAL_STATE')  # set when the runner itself runs inside a sandbox
    return Path(inherited) if inherited else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / STATE_NAME


def instance_name(base) -> str:
    return 'Local\\YtDlpDesktop.Test.' + hashlib.sha1(str(base).encode()).hexdigest()[:12]


def snapshot_tree() -> dict:
    """Every relative path under verification/, __pycache__ excluded.

    A file maps to (size, st_mtime_ns), a folder to None: a test that rewrites or deletes
    something that was already there (an old report, a golden, a backup) is noticed too.
    """
    found = {}
    for folder, folders, files in os.walk(VERIFICATION):
        relative = Path(folder).relative_to(VERIFICATION)
        folders[:] = [name for name in folders if name != '__pycache__']
        for name in folders:
            found[(relative / name).as_posix()] = None
        for name in files:
            try:
                status = (Path(folder) / name).stat()
                found[(relative / name).as_posix()] = (status.st_size, status.st_mtime_ns)
            except OSError:
                found[(relative / name).as_posix()] = (-1, -1)
    return found


def snapshot_state(real: Path) -> dict:
    """(size, st_mtime_ns) of every file under the real state folder; it may not exist."""
    found = {}
    if real.is_dir():
        for folder, _folders, files in os.walk(real):
            for name in files:
                path = Path(folder) / name
                try:
                    status = path.stat()
                except OSError:
                    continue
                found[path.relative_to(real).as_posix()] = (status.st_size, status.st_mtime_ns)
    return found


def head_lines(path: Path) -> list:
    """The first five lines of a file, stripped ([] when it cannot be read)."""
    try:
        with path.open(encoding='utf-8', errors='replace') as handle:
            return [handle.readline().strip() for _ in range(5)]
    except OSError:
        return []


def tier_of(path: Path) -> str:
    for line in head_lines(path):
        match = TIER_PATTERN.match(line)
        if match:
            return match.group(1)
    return 'default'


def marks_of(path: Path) -> set:
    """(2.0.0) {'serial', 'quick'} or a part of it: the markers in the first five lines."""
    return {match.group(1) for match in map(MARK_PATTERN.match, head_lines(path)) if match}


def name_filters(text) -> list:
    """(2.0.0) -k 'a,b' -> ['a', 'b'] (empty parts dropped); None -> []."""
    return [part.strip() for part in (text or '').split(',') if part.strip()]


def discover() -> list:
    files = sorted(VERIFICATION.glob('test_*.py'), key=lambda path: path.name)
    return [(path, tier_of(path)) for path in files]


def source_sha256() -> str:
    """One SHA256 over the bytes of src/*.py (sorted by file name), then app.spec."""
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'src').glob('*.py'), key=lambda item: item.name):
        digest.update(path.read_bytes())
    digest.update((ROOT / 'app.spec').read_bytes())
    return digest.hexdigest()


def tests_sha256() -> str:
    """(2.0.0, S1 review fix) One SHA256 over what decides the results besides src: the test code and data
    (every *.py under VERIFICATION, every file under its golden/ and fixture/ folders; relative path and the
    SHA256 of the bytes, sorted by path) and VENDOR_MANIFEST (the engine and tools the tests run).  The
    release gate takes a report only when this is still the value of the tree."""
    names = set()
    for path in VERIFICATION.rglob('*'):
        relative = path.relative_to(VERIFICATION)
        if not path.is_file() or '__pycache__' in relative.parts:
            continue
        if path.suffix == '.py' or relative.parts[0] in TEST_DATA_FOLDERS:
            names.add(relative.as_posix())
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(f'verification/{name}\0{file_sha256(VERIFICATION / name)}\n'.encode('utf-8'))
    manifest = file_sha256(VENDOR_MANIFEST) if VENDOR_MANIFEST.is_file() else 'missing'
    digest.update(f'vendor/manifest.json\0{manifest}\n'.encode('utf-8'))
    return digest.hexdigest()


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def app_version():
    try:
        text = (ROOT / 'src' / 'engine.py').read_text(encoding='utf-8')
    except OSError:
        return None
    match = re.search(r'''^APP_VERSION\s*=\s*['"]([^'"]+)['"]''', text, re.M)
    return match.group(1) if match else None


def child_environment(sandbox: Path, real: Path, exe, net: bool, guard: bool = True) -> dict:
    environment = os.environ.copy()
    for name in ('YTDLP_DESKTOP_STATE', 'YTDLP_VERIFY_BASE', 'YTDLP_VERIFY_KEEP', 'GOLDEN_UPDATE',
                 'VERIFY_EXE', 'VERIFY_NET', 'YTDLP_VERIFY_LANG_OVERRIDE', MSG_GUARD):
        environment.pop(name, None)
    environment.update({
        'YTDLP_VERIFY_SANDBOX': str(sandbox),
        'LOCALAPPDATA': str(sandbox / 'localappdata'),
        'YTDLP_DESKTOP_APP_DIR': str(sandbox / 'app'),
        'TEMP': str(sandbox / 'tmp'),
        'TMP': str(sandbox / 'tmp'),
        'VERIFY_OUT': str(sandbox / 'out'),
        'XDG_CACHE_HOME': str(sandbox / 'cache'),
        'PYTHONIOENCODING': 'utf-8',
        'YTDLP_DESKTOP_INSTANCE': instance_name(sandbox),
        'YTDLP_VERIFY_REAL_STATE': str(real),
        'PYTHONDONTWRITEBYTECODE': '1',
        # (1.7.0, I18N.md 11.1) every test file starts in Chinese; _sandbox refuses anything else
        'YTDLP_DESKTOP_LANG': 'zh',
    })
    if exe:
        environment['VERIFY_EXE'] = str(exe)
    if net:
        environment['VERIFY_NET'] = '1'
    if guard:
        environment[MSG_GUARD] = '1'
    return environment


def guard_lines(sandbox: Path) -> tuple:
    """(violations, exemptions) read from every msg-guard-*.jsonl of a test file's sandbox."""
    violations, exemptions = [], []
    for path in sorted(Path(sandbox).rglob(GUARD_FILES)):
        for line in read_text(path).splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                item = {'rule': 'unreadable guard line', 'text': line[:80]}
            (exemptions if isinstance(item, dict) and 'exempt' in item else violations).append(item)
    return violations, exemptions


def guard_text(item) -> str:
    if not isinstance(item, dict):
        return f'msg-guard: {item!r}'[:200]
    return (f'msg-guard {item.get("rule")} at {item.get("where")} by {item.get("producer")}: key {item.get("key")}, '
            f'text {item.get("text")!r}, test {item.get("test")}')


class Job:
    """Best-effort Windows job object: nothing a test started survives the test (or this runner)."""

    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        try:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.restype = ctypes.c_void_p
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            kernel.SetInformationJobObject.restype = ctypes.c_int
            kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
            kernel.QueryInformationJobObject.restype = ctypes.c_int
            kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                         ctypes.c_ulong, ctypes.c_void_p]
            kernel.AssignProcessToJobObject.restype = ctypes.c_int
            kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            kernel.TerminateJobObject.restype = ctypes.c_int
            kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.restype = ctypes.c_int
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.kernel = kernel

            class Basic(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                            ('LimitFlags', ctypes.c_ulong), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', ctypes.c_ulong),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', ctypes.c_ulong),
                            ('SchedulingClass', ctypes.c_ulong)]

            class Extended(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', ctypes.c_ulonglong * 6),
                            ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                            ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

            handle = kernel.CreateJobObjectW(None, None)
            if not handle:
                return
            information = Extended()
            information.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
                kernel.CloseHandle(handle)
                return
            self.handle = handle
        except (OSError, AttributeError):
            self.handle = None

    def assign(self, process) -> bool:
        try:
            return bool(self.handle and self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)))
        except (OSError, AttributeError, ValueError):
            return False

    def members(self) -> list:
        """Process ids still alive in the job."""
        if not self.handle:
            return []

        class IdList(ctypes.Structure):
            _fields_ = [('NumberOfAssignedProcesses', ctypes.c_ulong), ('NumberOfProcessIdsInList', ctypes.c_ulong),
                        ('ProcessIdList', ctypes.c_size_t * 1024)]

        information = IdList()
        try:
            if self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(information),
                                                     ctypes.sizeof(information), None):
                return [int(information.ProcessIdList[index])
                        for index in range(min(information.NumberOfProcessIdsInList, 1024))]
        except OSError:
            pass
        return []

    def close(self) -> list:
        """Kill whatever the test left running; returns the image names of those processes."""
        if not self.handle:
            return []
        left = []
        deadline = time.monotonic() + 2
        while True:
            # The console host of a finished child needs a moment to exit by itself.
            table = process_table()
            left = [table.get(pid, f'pid {pid}') for pid in self.members()]
            left = [name for name in left if name.lower() != 'conhost.exe']
            if not left or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        try:
            self.kernel.TerminateJobObject(self.handle, 1)
            self.kernel.CloseHandle(self.handle)
        except OSError:
            pass
        self.handle = None
        return left


def kill_tree(process) -> None:
    if process.poll() is not None:
        return
    try:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                       creationflags=NO_WINDOW, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            pass


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode('utf-8', errors='replace')
    except OSError:
        return ''


# (2.0.0) the test processes running now; with -j an interrupt ends every one of them
RUNNING = set()
RUNNING_LOCK = threading.Lock()


def run_file(path: Path, tier: str, base: Path, real: Path, exe, net: bool) -> dict:
    sandbox = base / path.stem
    for name in ('localappdata', 'app', 'tmp', 'out', 'cache'):
        (sandbox / name).mkdir(parents=True, exist_ok=True)
    logs = base / '_logs'
    logs.mkdir(exist_ok=True)
    out_path, err_path = logs / f'{path.stem}.stdout.txt', logs / f'{path.stem}.stderr.txt'
    timeout = TIMEOUT.get(tier, 900)
    started = time.monotonic()
    timed_out = False
    job = Job()
    guard = path.name not in GUARD_OFF
    # Output goes to files, not pipes: a leaked grandchild holding a pipe could block the runner.
    with out_path.open('wb') as out_file, err_path.open('wb') as err_file:
        process = subprocess.Popen([sys.executable, '-B', str(path)], cwd=str(ROOT),
                                   env=child_environment(sandbox, real, exe, net, guard),
                                   stdin=subprocess.DEVNULL, stdout=out_file, stderr=err_file,
                                   creationflags=NO_WINDOW)
        job.assign(process)
        with RUNNING_LOCK:
            RUNNING.add(process)
        try:
            code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(process)
            code = process.returncode
        except BaseException:
            kill_tree(process)
            job.close()
            raise
        finally:
            with RUNNING_LOCK:
                RUNNING.discard(process)
    stray = job.close()
    seconds = round(time.monotonic() - started, 1)
    stdout, stderr = read_text(out_path), read_text(err_path)
    lines = stdout.splitlines() + stderr.splitlines()
    first = next((line for line in lines if line.strip()), '')
    if timed_out:
        status = 'failed'
        lines.append(f'TIMEOUT: killed after {timeout} s')
    elif code == 0:
        status = 'skipped' if first.startswith('SKIPPED') else 'passed'
    else:
        status = 'failed'
        lines.append(f'exit code {code}')
    violations, exemptions = guard_lines(sandbox)
    tail = [] if status == 'passed' else lines[-TAIL_LINES:]
    if violations:
        status = 'failed'
        tail = tail + [f'{len(violations)} message-guard violation(s):'] + \
            [guard_text(item) for item in violations[:GUARD_TAIL]]
    return {'file': path.name, 'tier': tier, 'status': status, 'seconds': seconds,
            'tail': tail, 'leftover_processes': stray,
            'msg_guard': {'guarded': guard, 'violations': len(violations), 'exemptions': len(exemptions)}}


def process_table() -> dict:
    """{pid: image name} of the running processes (Unicode-safe; tasklist would mangle Chinese names)."""
    if os.name != 'nt':
        return {}
    try:
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                        ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                        ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                        ('szExeFile', ctypes.c_wchar * 260)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return {}
        found = {}
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        try:
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found[int(entry.th32ProcessID)] = entry.szExeFile
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return found
    except (OSError, AttributeError):
        return {}


def app_processes() -> list:
    """Process ids whose image is the app's exe."""
    return sorted(pid for pid, name in process_table().items() if name.lower() == APP_IMAGE.lower())


def isolation_problems(tree_before, state_before, leaks_before, real: Path) -> list:
    problems = []
    tree_after = snapshot_tree()
    for name in sorted(set(tree_before) | set(tree_after)):
        if name not in tree_before:
            problems.append(f'new path in verification/: {name}')
        elif name not in tree_after:
            problems.append(f'path removed from verification/: {name}')
        elif tree_before[name] != tree_after[name]:
            problems.append(f'file changed in verification/: {name}')
    state_after = snapshot_state(real)
    state_changes = []
    for name in sorted(set(state_before) | set(state_after)):
        if name not in state_after:
            state_changes.append(f'real state file removed: {real / name}')
        elif name not in state_before:
            state_changes.append(f'real state file created: {real / name}')
        elif state_before[name] != state_after[name]:
            state_changes.append(f'real state file changed: {real / name}')
    if state_changes:
        running = app_processes()
        if running:
            state_changes.append(f'note: {APP_IMAGE} is running (pid {", ".join(map(str, running))}); its own log and '
                                 'settings writes look exactly like this. Close it and run again to be sure.')
    problems += state_changes
    for path in LEAK_SIGNATURES:
        if path.exists() and path not in leaks_before:
            problems.append(f'source-mode leak outside the sandbox: {path}')
    return problems


def collect_reports(base: Path, results: list) -> dict:
    reports = {}
    for result in results:
        stem = Path(result['file']).stem
        folder = base / stem / 'out'
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob('*.json')):
            key = f'{stem}/{path.relative_to(folder).as_posix()}'
            try:
                if path.stat().st_size > 1024 * 1024:
                    reports[key] = {'_skipped': 'larger than 1 MB'}
                else:
                    reports[key] = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as error:
                reports[key] = {'_error': str(error)}
    return reports


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def write_report(data: dict, path=None) -> None:
    path = Path(path) if path is not None else REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def report_roles(report: dict) -> list:
    """(2.0.0) The release-gate copies a report qualifies for: 'source' and/or 'exe' (see the docstring).

    Only serial, unfiltered runs qualify; the gate checks the content (hashes, every file, every result) again."""
    if report.get('jobs') != 1 or report.get('filter') is not None or report.get('quick'):
        return []
    tiers = report.get('tiers') or []
    roles = []
    if not report.get('only_tier') and report.get('exe') is None and 'default' in tiers and 'all' in tiers:
        roles.append('source')
    if report.get('exe') is not None and 'exe' in tiers:
        roles.append('exe')
    return roles


def role_path(role: str) -> Path:
    return REPORT.with_name(SOURCE_REPORT_NAME if role == 'source' else EXE_REPORT_NAME)


def previous_seconds() -> dict:
    """{file: seconds} of the last report, to start the slowest files first under -j (empty when unknown)."""
    try:
        results = json.loads(REPORT.read_text(encoding='utf-8')).get('results') or []
        return {item['file']: float(item['seconds']) for item in results if isinstance(item, dict)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def show_result(number: int, total: int, path: Path, tier: str, result: dict, whole_line: bool) -> None:
    note = ('   (leftover processes killed: ' + ', '.join(result['leftover_processes']) + ')'
            if result['leftover_processes'] else '')
    if whole_line:
        print(f'[{number:2}/{total}] {path.name} ({tier}) ...', end=' ')
    print(f'{result["status"]}  {result["seconds"]} s{note}', flush=True)
    if result['status'] != 'passed':
        for line in result['tail']:
            print('      | ' + line)


def end_running() -> None:
    with RUNNING_LOCK:
        running = list(RUNNING)
    for process in running:
        kill_tree(process)


def run_selected(selected: list, jobs: int, base: Path, real: Path, exe, net: bool) -> list:
    """Run the selected files; the results come back in the order of ``selected``.

    jobs 1: one after the other, as always.  jobs > 1: the files without ``# serial`` run ``jobs`` at a time
    (the slowest of the last report first), then the ``# serial`` files one by one."""
    total = len(selected)
    numbered = list(enumerate(selected, 1))
    results = {}
    if jobs > 1:
        parallel = [item for item in numbered if 'serial' not in marks_of(item[1][0])]
        serial = [item for item in numbered if 'serial' in marks_of(item[1][0])]
        last = previous_seconds()
        parallel.sort(key=lambda item: -last.get(item[1][0].name, float('inf')))
        print(f'parallel {len(parallel)} files, {jobs} at a time; then {len(serial)} serial: '
              + (', '.join(path.name for _number, (path, _tier) in serial) or 'none'), flush=True)
        pool = ThreadPoolExecutor(max_workers=jobs)
        try:
            pending = {pool.submit(run_file, path, tier, base, real, exe, net): (number, path, tier)
                       for number, (path, tier) in parallel}
            while pending:
                done, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    number, path, tier = pending.pop(future)
                    results[number] = future.result()
                    show_result(number, total, path, tier, results[number], True)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            end_running()
            raise
        pool.shutdown(wait=True)
        numbered = serial
    for number, (path, tier) in numbered:
        print(f'[{number:2}/{total}] {path.name} ({tier}) ...', end=' ', flush=True)
        results[number] = run_file(path, tier, base, real, exe, net)
        show_result(number, total, path, tier, results[number], False)
    return [results[number] for number in sorted(results)]


def remove_tree(path: Path) -> bool:
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.5)
    return False


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Run the verification tests inside a sandbox.')
    parser.add_argument('--all', action='store_true', help='also run tier "all" (fixture server, yt-dlp, Chrome)')
    parser.add_argument('--exe', metavar='PATH', help='also run tier "exe" against this packaged exe')
    parser.add_argument('--net', action='store_true', help='also run tier "net" (real websites)')
    parser.add_argument('--only-tier', choices=TIER_ORDER, metavar='TIER',
                        help='only this tier (default, all, exe or net); exe still needs --exe PATH')
    parser.add_argument('-k', metavar='NAME[,NAME...]',
                        help='only files whose name contains one of these comma-separated substrings')
    parser.add_argument('--quick', action='store_true',
                        help='only the files marked "# quick" (fast, no window): the check after every edit')
    parser.add_argument('-j', type=int, default=1, metavar='N',
                        help='run N files at a time; "# serial" files run alone afterwards (default 1: serial)')
    parser.add_argument('--keep', action='store_true', help='keep the sandbox folder after a green run')
    parser.add_argument('--list', action='store_true',
                        help='list the test files with their tiers (and markers) and exit')
    options = parser.parse_args(argv)
    if options.j < 1:
        print('-j: N must be 1 or more')
        return 2

    tests = discover()
    if options.list:
        for path, tier in tests:
            marks = sorted(marks_of(path))
            print(f'{tier:8} {path.name}' + (f'  [{", ".join(marks)}]' if marks else ''))
        return 0
    exe = None
    if options.exe:
        exe = Path(options.exe).resolve()
        if not exe.is_file():
            print(f'--exe: file not found: {exe}')
            return 2
    if options.only_tier == 'exe' and exe is None:
        print('--only-tier exe needs --exe PATH')
        return 2
    if options.only_tier:
        tiers = [options.only_tier]
    else:
        tiers = ['default'] + [name for name, wanted in (('all', options.all), ('exe', exe), ('net', options.net))
                               if wanted]
    net = options.net or options.only_tier == 'net'
    names = name_filters(options.k)
    selected = [(path, tier) for path, tier in tests
                if tier in tiers and (not names or any(name in path.name for name in names))
                and (not options.quick or 'quick' in marks_of(path))]
    selected.sort(key=lambda item: (TIER_ORDER.index(item[1]), item[0].name))
    if not selected:
        print('no test file selected')
        return 2

    started = now()
    clock = time.monotonic()
    sources = source_sha256()
    tests_digest = tests_sha256()
    real = real_state_dir()
    tree_before, state_before = snapshot_tree(), snapshot_state(real)
    leaks_before = {path for path in LEAK_SIGNATURES if path.exists()}
    base = Path(tempfile.mkdtemp(prefix='ytdlp-verify-')).resolve()
    print(f'sandbox  {base}')
    print(f'tiers    {", ".join(tiers)}' + (f'   exe {exe}' if exe else ''))
    try:
        results = run_selected(selected, options.j, base, real, exe, net)
    except KeyboardInterrupt:
        print('\ninterrupted; the sandbox is kept: ' + str(base))
        return 130

    problems = isolation_problems(tree_before, state_before, leaks_before, real)
    if problems:
        results.append({'file': 'isolation', 'tier': 'default', 'status': 'failed', 'seconds': 0.0,
                        'tail': problems, 'leftover_processes': []})
        print('isolation ... failed')
        for line in problems:
            print('      | ' + line)
    else:
        print('isolation ... ok (verification/ has no new, changed or removed path; the real state folder is unchanged)')

    if source_sha256() != sources:
        # The results would not belong to one source state (the release gate compares this hash).
        message = 'src/*.py or app.spec changed while the tests were running; run again'
        results.append({'file': 'sources', 'tier': 'default', 'status': 'failed', 'seconds': 0.0,
                        'tail': [message], 'leftover_processes': []})
        print('sources ... failed\n      | ' + message)

    count = {status: sum(1 for result in results
                         if result['status'] == status and result['file'] not in ('isolation', 'sources'))
             for status in ('passed', 'failed', 'skipped')}
    failed = [result['file'] for result in results if result['status'] == 'failed']
    not_run = [f'{path.name} ({tier})' for path, tier in tests if (path, tier) not in selected]
    guarded = [result['msg_guard'] for result in results if result.get('msg_guard', {}).get('guarded')]
    msg_guard = {'files_checked': len(guarded), 'violations': sum(item['violations'] for item in guarded),
                 'exemptions': sum(item['exemptions'] for item in guarded),
                 'not_guarded': sorted(result['file'] for result in results
                                       if 'msg_guard' in result and not result['msg_guard']['guarded'])}
    report = {'app_version': app_version(), 'source_sha256': sources, 'tests_sha256': tests_digest,
              'exe': {'path': str(exe), 'sha256': file_sha256(exe)} if exe else None,
              'tiers': tiers, 'filter': options.k or None, 'not_run': not_run,
              'started': started, 'finished': now(), 'results': results, 'msg_guard': msg_guard,
              'reports': collect_reports(base, results),
              'quick': options.quick, 'only_tier': options.only_tier, 'jobs': options.j}
    write_report(report)
    roles = report_roles(report)
    for role in roles:
        write_report(report, role_path(role))
    if not_run:
        print('not selected: ' + ', '.join(not_run))
    print(f'report   {REPORT}' + ''.join(f'   (also {role_path(role).name})' for role in roles))
    print(f'SUMMARY: {count["passed"]} passed, {count["failed"]} failed, {count["skipped"]} skipped; '
          f'isolation {"FAILED" if problems else "ok"}; tiers {"+".join(tiers)}; '
          f'{time.monotonic() - clock:.1f} s' + (f' with -j {options.j}' if options.j > 1 else ''))
    print(f'MSG_GUARD: {msg_guard["files_checked"]} files checked, {msg_guard["violations"]} violations, '
          f'{msg_guard["exemptions"]} exemptions; not guarded: {", ".join(msg_guard["not_guarded"]) or "none"}')
    if failed:
        print('FAILED: ' + ', '.join(failed))
        print(f'sandbox kept for diagnosis: {base}')
        return 1
    if options.keep:
        print(f'sandbox kept: {base}')
    elif not remove_tree(base):
        print(f'note: could not remove the sandbox completely: {base}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
