"""Process and window helpers (1.6.0). Standard library + ctypes; imports paths only.

Child processes: HIDDEN, process_environment, alive, JobObject, terminate_tree.
Windows shell: single_instance, focus_window, reveal_in_folder, flash_window, is_foreground.
Every ctypes function gets explicit restype/argtypes (handles are 64-bit).  Nothing here
raises because a Windows API is missing or fails: the helpers report False / None instead.
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

import paths

HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
INSTANCE_NAME = 'Local\\YtDlpDesktop.Instance'
ERROR_ALREADY_EXISTS = 183
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
SW_RESTORE = 9
FLASHW_TRAY = 0x2
FLASHW_TIMERNOFG = 0xC

_log = logging.getLogger('YtDlpDesktop.procutil')
_libraries = {}
_job_failure_logged = False
_assign_failure_logged = False


def process_environment(reset=True):
    environment = os.environ.copy()
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYINSTALLER_RESET_ENVIRONMENT='1')
    if not reset:
        # A worker that is this same program reuses the files the parent already unpacked.
        del environment['PYINSTALLER_RESET_ENVIRONMENT']
    environment['PATH'] = str(paths.BIN) + os.pathsep + environment.get('PATH', '')
    return environment


def alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- ctypes plumbing ----------------------------------------------------------------------

class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),   # LARGE_INTEGER
                ('PerJobUserTimeLimit', ctypes.c_longlong),       # LARGE_INTEGER
                ('LimitFlags', ctypes.c_ulong),                   # DWORD
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', ctypes.c_ulong),           # DWORD
                ('Affinity', ctypes.c_size_t),                    # ULONG_PTR
                ('PriorityClass', ctypes.c_ulong),                # DWORD
                ('SchedulingClass', ctypes.c_ulong)]              # DWORD


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimitInformation),
                ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _FlashInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('hwnd', ctypes.c_void_p), ('dwFlags', ctypes.c_ulong),
                ('uCount', ctypes.c_uint), ('dwTimeout', ctypes.c_ulong)]


def _kernel32():
    kernel32 = _libraries.get('kernel32')
    if kernel32 is None:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.IsProcessInJob.restype = ctypes.c_int
        kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        _libraries['kernel32'] = kernel32
    return kernel32


def _user32():
    user32 = _libraries.get('user32')
    if user32 is None:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        _libraries['EnumWindowsProc'] = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows.restype = ctypes.c_int
        user32.EnumWindows.argtypes = [_libraries['EnumWindowsProc'], ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsIconic.restype = ctypes.c_int
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.restype = ctypes.c_int
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetForegroundWindow.argtypes = []
        user32.FlashWindowEx.restype = ctypes.c_int
        user32.FlashWindowEx.argtypes = [ctypes.POINTER(_FlashInfo)]
        _libraries['user32'] = user32
    return user32


def _handle_value(hwnd) -> int:
    """A window handle as a plain int (ctypes hands out None for NULL)."""
    if hwnd is None:
        return 0
    value = getattr(hwnd, 'value', hwnd)
    return int(value or 0)


# --- job object ---------------------------------------------------------------------------

class JobObject:
    """A Windows job with KILL_ON_JOB_CLOSE: what is assigned to it dies with the job, with
    terminate(), or when this program ends for any reason.

    One per Runner / CollectionRunner, never one for the whole program (Explorer windows
    opened with os.startfile must survive).  Never raises; ``handle`` is None when no job is
    available (not Windows, or an API failure, which is logged once).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._spent = False   # terminate() ran and nothing was assigned since: the job is empty
        self.handle = self._create()

    @staticmethod
    def _create():
        """A new job handle with the kill-on-close limit, or None."""
        global _job_failure_logged
        if os.name != 'nt':
            return None
        try:
            kernel32 = _kernel32()
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            information = _ExtendedLimitInformation()
            information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                                    ctypes.byref(information), ctypes.sizeof(information)):
                error = ctypes.WinError(ctypes.get_last_error())
                kernel32.CloseHandle(handle)
                raise error
            return handle
        except Exception as error:
            if not _job_failure_logged:
                _job_failure_logged = True
                _log.warning('job object unavailable, child processes are stopped with taskkill only: %s', error)
            return None

    def assign(self, process) -> bool:
        """Put a subprocess.Popen into the job (right after it was started).

        False when there is no job, and when Windows refuses the process: it then runs
        outside the job, is stopped with taskkill only and would outlive a crash of this
        program.  A refusal is logged, once per program run.
        """
        try:
            with self._lock:
                if not self.handle:
                    return False
                kernel32, target = _kernel32(), int(process._handle)
                if kernel32.AssignProcessToJobObject(self.handle, target):
                    self._spent = False
                    return True
                reason = f'Windows error {ctypes.get_last_error()}'
                if self._spent:
                    # Right after TerminateJobObject, Windows can refuse the job for a moment
                    # (ERROR_ACCESS_DENIED while its processes are still being torn down; seen
                    # when this program itself runs inside a job).  The job holds nothing we
                    # want to keep, so a fresh one takes its place.
                    fresh = self._create()
                    if fresh:
                        spent, self.handle, self._spent = self.handle, fresh, False
                        kernel32.CloseHandle(spent)
                        if kernel32.AssignProcessToJobObject(fresh, target):
                            return True
                        reason = f'Windows error {ctypes.get_last_error()}'
        except Exception as error:
            reason = repr(error)
        _assign_failed(process, reason)
        return False

    def holds(self, process):
        """Whether the process is in this job: True or False, None when Windows cannot tell."""
        try:
            with self._lock:
                if not self.handle:
                    return False
                inside = ctypes.c_int(0)
                if _kernel32().IsProcessInJob(int(process._handle), self.handle, ctypes.byref(inside)):
                    return bool(inside.value)
        except Exception:
            pass
        return None

    def terminate(self) -> bool:
        """End every process in the job at once; the job stays usable."""
        try:
            with self._lock:
                if self.handle and _kernel32().TerminateJobObject(self.handle, 1):
                    self._spent = True
                    return True
                return False
        except Exception:
            return False

    def close(self) -> None:
        """Release the job; processes still in it are ended by Windows."""
        try:
            with self._lock:
                handle, self.handle = self.handle, None
            if handle:
                _kernel32().CloseHandle(handle)
        except Exception:
            pass

    def __del__(self):
        self.close()


def _assign_failed(process, reason) -> None:
    global _assign_failure_logged
    if not _assign_failure_logged:
        _assign_failure_logged = True
        _log.warning('process %s could not be put into its job object (%s): it is stopped with taskkill only '
                     'and would outlive a crash of this program', getattr(process, 'pid', '?'), reason)


def terminate_tree(process, job=None) -> None:
    """Stop a child process and everything it started."""
    if not process or process.poll() is not None:
        return
    if job is not None:
        # Asked before the job is ended.  A process the job never got (assign() failed) does
        # not die with it: waiting for that would only delay the taskkill below.
        holds = getattr(job, 'holds', None)
        inside = holds(process) if holds else None
        if job.terminate() and inside is not False:
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                pass
    if process and process.poll() is None:
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, creationflags=HIDDEN, timeout=10)
            else:
                process.terminate()
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


# --- single instance ----------------------------------------------------------------------

class _Held:
    """Stand-in for the mutex handle where no mutex can tell (not Windows, API failure)."""


def single_instance(name=None):
    """The mutex handle of this instance (keep it for the life of the process), or None
    when another instance already holds the name.

    name: the argument, else env YTDLP_DESKTOP_INSTANCE (every sandboxed launch has its own,
    because the mutex is visible to the whole login session), else INSTANCE_NAME.
    """
    name = name or os.environ.get('YTDLP_DESKTOP_INSTANCE') or INSTANCE_NAME
    if os.name != 'nt':
        return _Held()
    try:
        kernel32 = _kernel32()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
    except Exception as failure:
        _log.warning('single-instance check unavailable: %s', failure)
        return _Held()
    if error == ERROR_ALREADY_EXISTS:
        if handle:
            kernel32.CloseHandle(handle)
        return None
    if not handle:
        # Not "already running": do not keep the program from starting.
        _log.warning('single-instance mutex could not be created (error %s)', error)
        return _Held()
    return handle


def release_instance(handle) -> None:
    """Give the name back before the process ends (tests; the app keeps it until exit)."""
    if handle and not isinstance(handle, _Held) and os.name == 'nt':
        try:
            _kernel32().CloseHandle(handle)
        except Exception:
            pass


def _find_window(title_suffix):
    """Handle of a visible Tk top-level window whose title ends with the suffix, or None."""
    user32 = _user32()
    found = []

    def visit(hwnd, _parameter):
        try:
            if not user32.IsWindowVisible(hwnd):
                return 1
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value != 'TkTopLevel':
                return 1
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 2)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value.endswith(title_suffix):
                found.append(hwnd)
                return 0
        except Exception:
            pass
        return 1

    callback = _libraries['EnumWindowsProc'](visit)
    user32.EnumWindows(callback, None)
    return found[0] if found else None


def focus_window(title_suffix='YT-DLP 下载器', timeout=3.0) -> bool:
    """Bring the running instance's window forward; False when none shows up in time."""
    if os.name != 'nt':
        return False
    deadline = time.monotonic() + timeout
    while True:
        try:
            hwnd = _find_window(title_suffix)
            if hwnd:
                user32 = _user32()
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                return True
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


# --- shell --------------------------------------------------------------------------------

def _shell32():
    shell32 = _libraries.get('shell32')
    if shell32 is None:
        ole32 = ctypes.WinDLL('ole32', use_last_error=True)
        ole32.CoInitialize.restype = ctypes.c_long
        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
        shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
        shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong]
        shell32.ILFree.restype = None
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        _libraries['ole32'], _libraries['shell32'] = ole32, shell32
    return shell32


def _shell_select(path) -> bool:
    """Open the folder of ``path`` in Explorer with the file selected (reuses an open window)."""
    shell32 = _shell32()
    _libraries['ole32'].CoInitialize(None)
    item = shell32.ILCreateFromPathW(str(path))
    if not item:
        return False
    try:
        return shell32.SHOpenFolderAndSelectItems(item, 0, None, 0) == 0
    finally:
        shell32.ILFree(item)


def reveal_in_folder(path) -> bool:
    """Show a file in Explorer.  A missing file opens its folder instead and gives False."""
    path = Path(path)
    try:
        if not path.exists():
            os.startfile(path.parent)
            return False
    except OSError:
        return False
    try:
        if os.name == 'nt' and _shell_select(path):
            return True
    except Exception:
        pass
    try:
        subprocess.Popen(f'explorer /select,"{path}"')
        return True
    except OSError:
        return False


def flash_window(hwnd) -> None:
    """Flash the taskbar button until the window comes to the foreground."""
    if os.name != 'nt':
        return
    try:
        information = _FlashInfo(ctypes.sizeof(_FlashInfo), _handle_value(hwnd), FLASHW_TRAY | FLASHW_TIMERNOFG, 0, 0)
        _user32().FlashWindowEx(ctypes.byref(information))
    except Exception:
        pass


def is_foreground(hwnd) -> bool:
    if os.name != 'nt':
        return False
    try:
        return _handle_value(_user32().GetForegroundWindow()) == _handle_value(hwnd)
    except Exception:
        return False
