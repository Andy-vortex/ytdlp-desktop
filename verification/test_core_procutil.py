# serial: brings windows to the foreground (focus_window) and times it
"""Process and window helpers of procutil.py (1.6.0 WP1).

R14 single instance (mutex name, the sandbox's own name from the environment, a second
launch of the app exits at once), R15 job object (a child and its grandchild die with
terminate() and with close()), terminate_tree with and without a job, alive,
process_environment, and the shell helpers with the Windows calls replaced (no Explorer
window is opened and no real instance name is ever taken).

Fix round 1: an assignment that Windows refuses is logged once, JobObject.holds() tells
whether a process is in the job, and terminate_tree does not wait two seconds for a job
kill that cannot reach a process outside the job.
"""
import _sandbox
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

import paths
import procutil

# The child waits for a line on stdin before it starts the grandchild, so the test can put
# it into the job first (a process started before the assignment would not be in the job).
CHILD = ('import subprocess, sys, time\n'
         'sys.stdin.readline()\n'
         'grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
         'print(grandchild.pid, flush=True)\n'
         'time.sleep(120)\n')


def unique_name():
    return 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex


class ProcessTreeCase(unittest.TestCase):
    def setUp(self):
        # A refused assignment is logged once per program run; the tests that provoke one on
        # the way do not want that line (the test about it sets the flag back itself).
        quiet = mock.patch.object(procutil, '_assign_failure_logged', True)
        quiet.start()
        self.addCleanup(quiet.stop)

    def start_child(self):
        process = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, creationflags=procutil.HIDDEN)
        self.addCleanup(self.stop, process)
        return process

    def release(self, process):
        """Let the child start its grandchild; returns the grandchild's pid."""
        process.stdin.write('go\n')
        process.stdin.flush()
        pid = int(process.stdout.readline())
        self.addCleanup(self.kill_pid, pid)
        return pid

    @staticmethod
    def kill_pid(pid):
        if procutil.alive(pid):
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                           creationflags=procutil.HIDDEN, timeout=30)

    def stop(self, process):
        self.kill_pid(process.pid)
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass

    def still_alive(self, pids, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(procutil.alive(pid) for pid in pids):
            time.sleep(0.05)
        return [pid for pid in pids if procutil.alive(pid)]

    @staticmethod
    def refusing(real, refused):
        """kernel32 whose AssignProcessToJobObject refuses the job handles in ``refused``."""
        calls = []

        class Kernel:
            def __getattr__(self, name):
                return getattr(real, name)

            def AssignProcessToJobObject(self, job, process):
                calls.append(job)
                return 0 if job in refused else real.AssignProcessToJobObject(job, process)

        return Kernel(), calls


class JobObjectTests(ProcessTreeCase):
    """R15."""

    def test_terminate_kills_child_and_grandchild_and_the_job_stays_usable(self):
        job = procutil.JobObject()
        self.addCleanup(job.close)
        self.assertIsNotNone(job.handle, 'no job object on this Windows')
        first = self.start_child()
        self.assertTrue(job.assign(first))
        grandchild = self.release(first)
        self.assertTrue(procutil.alive(first.pid) and procutil.alive(grandchild))
        self.assertTrue(job.terminate())
        self.assertEqual(self.still_alive([first.pid, grandchild]), [])
        self.assertIsNotNone(job.handle)
        second = self.start_child()
        self.assertTrue(job.assign(second))
        grandchild = self.release(second)
        self.assertTrue(job.terminate())
        self.assertEqual(self.still_alive([second.pid, grandchild]), [])

    def test_a_terminated_job_that_refuses_a_process_is_replaced_by_a_fresh_one(self):
        # Windows can answer ERROR_ACCESS_DENIED right after TerminateJobObject, while the job's
        # processes are still being torn down (seen when this program itself runs inside a job).
        job = procutil.JobObject()
        self.addCleanup(job.close)
        first = self.start_child()
        self.assertTrue(job.assign(first))
        self.assertTrue(job.terminate())
        spent = job.handle
        kernel, calls = self.refusing(procutil._kernel32(), {spent})
        second = self.start_child()
        with mock.patch.object(procutil, '_kernel32', return_value=kernel):
            self.assertTrue(job.assign(second))
        self.assertNotIn(job.handle, (None, spent))
        self.assertEqual(calls, [spent, job.handle])
        grandchild = self.release(second)
        self.assertTrue(job.terminate())
        self.assertEqual(self.still_alive([second.pid, grandchild]), [])

    def test_a_job_with_live_processes_is_never_replaced(self):
        job = procutil.JobObject()
        self.addCleanup(job.close)
        live = self.start_child()
        self.assertTrue(job.assign(live))
        handle = job.handle
        kernel, calls = self.refusing(procutil._kernel32(), {handle})
        other = self.start_child()
        with mock.patch.object(procutil, '_kernel32', return_value=kernel):
            self.assertFalse(job.assign(other))
        self.assertEqual((job.handle, calls), (handle, [handle]))
        time.sleep(0.2)
        self.assertTrue(procutil.alive(live.pid), 'replacing the job would have killed the process in it')
        # After a terminate and a successful assignment the job counts as in use again.
        self.assertTrue(job.terminate())
        self.assertTrue(job.assign(other))
        handle = job.handle
        kernel, calls = self.refusing(procutil._kernel32(), {handle})
        with mock.patch.object(procutil, '_kernel32', return_value=kernel):
            self.assertFalse(job.assign(self.start_child()))
        self.assertEqual(job.handle, handle)
        self.assertTrue(procutil.alive(other.pid))

    def test_a_refused_assignment_is_logged_once(self):
        """conformance-1: such a process runs outside the job; download.log has to say so."""
        job = procutil.JobObject()
        self.addCleanup(job.close)
        kernel, calls = self.refusing(procutil._kernel32(), {job.handle})
        first, second = self.start_child(), self.start_child()
        with mock.patch.object(procutil, '_kernel32', return_value=kernel), \
                mock.patch.object(procutil, '_assign_failure_logged', False), \
                self.assertLogs('YtDlpDesktop.procutil', 'WARNING') as logs:
            self.assertFalse(job.assign(first))
            self.assertFalse(job.assign(second))
            self.assertFalse(job.assign(object()))   # not even a process
        self.assertEqual(calls, [job.handle] * 2)
        self.assertEqual(len(logs.records), 1, 'once per program run, not once per process')
        message = logs.records[0].getMessage()
        self.assertIn(f'process {first.pid} ', message)
        self.assertIn('job object', message)
        self.assertRegex(message, r'Windows error \d+')
        # Something that is no process at all is a failed assignment too, with its own reason.
        with mock.patch.object(procutil, '_assign_failure_logged', False), \
                self.assertLogs('YtDlpDesktop.procutil', 'WARNING') as logs:
            self.assertFalse(job.assign(object()))
        self.assertIn('AttributeError', logs.records[0].getMessage())
        # Without a job there is nothing to refuse: that was reported when the job could not be made.
        closed = procutil.JobObject()
        closed.close()
        with mock.patch.object(procutil, '_assign_failure_logged', False), \
                self.assertNoLogs('YtDlpDesktop.procutil', 'WARNING'):
            self.assertFalse(closed.assign(first))
        # A successful assignment logs nothing.
        with mock.patch.object(procutil, '_assign_failure_logged', False), \
                self.assertNoLogs('YtDlpDesktop.procutil', 'WARNING'):
            self.assertTrue(job.assign(first))

    def test_holds_tells_whether_a_process_is_in_the_job(self):
        job, other = procutil.JobObject(), procutil.JobObject()
        self.addCleanup(job.close)
        self.addCleanup(other.close)
        inside, outside = self.start_child(), self.start_child()
        self.assertTrue(job.assign(inside))
        self.assertIs(job.holds(inside), True)
        self.assertIs(job.holds(outside), False)
        self.assertIs(other.holds(inside), False)
        self.assertIsNone(job.holds(object()), 'not a process: Windows cannot tell')
        with mock.patch.object(procutil, '_kernel32', side_effect=OSError('no kernel32')):
            self.assertIsNone(job.holds(inside))
        self.assertTrue(job.terminate())
        self.assertEqual(self.still_alive([inside.pid]), [])
        other.close()
        self.assertIs(other.holds(outside), False, 'no job: nothing is in it')

    def test_close_alone_kills_child_and_grandchild(self):
        job = procutil.JobObject()
        process = self.start_child()
        self.assertTrue(job.assign(process))
        grandchild = self.release(process)
        job.close()
        self.assertIsNone(job.handle)
        self.assertEqual(self.still_alive([process.pid, grandchild]), [])
        job.close()  # a second close is harmless
        self.assertFalse(job.terminate())
        self.assertFalse(job.assign(process))

    def test_a_process_outside_the_job_survives_terminate(self):
        job = procutil.JobObject()
        self.addCleanup(job.close)
        outsider = self.start_child()
        self.assertTrue(job.terminate())  # an empty job
        time.sleep(0.2)
        self.assertTrue(procutil.alive(outsider.pid))

    def test_limit_structure_has_the_x64_layout(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8, 'the app and its tests are 64-bit')
        self.assertEqual(ctypes.sizeof(procutil._BasicLimitInformation), 64)
        self.assertEqual(ctypes.sizeof(procutil._IoCounters), 48)
        self.assertEqual(ctypes.sizeof(procutil._ExtendedLimitInformation), 144)
        self.assertEqual(procutil._BasicLimitInformation.LimitFlags.offset, 16)

    def test_an_api_failure_gives_a_job_that_does_nothing_and_never_raises(self):
        with mock.patch.object(procutil, '_kernel32', side_effect=OSError('no kernel32')), \
                mock.patch.object(procutil, '_job_failure_logged', False), \
                self.assertLogs('YtDlpDesktop.procutil', 'WARNING') as logs:
            job = procutil.JobObject()
            again = procutil.JobObject()
            self.assertIsNone(job.handle)
            self.assertFalse(job.assign(mock.Mock(_handle=1)))
            self.assertFalse(job.terminate())
            job.close()
            again.close()
        self.assertEqual(len(logs.records), 1, 'the failure is logged once, not per job')
        self.assertFalse(procutil.JobObject().assign(object()))  # not a process: False, no exception


class TerminateTreeTests(ProcessTreeCase):
    def test_without_a_job_the_whole_tree_is_killed(self):
        process = self.start_child()
        grandchild = self.release(process)
        procutil.terminate_tree(process)
        self.assertEqual(self.still_alive([process.pid, grandchild]), [])
        self.assertIsNotNone(process.poll())

    def test_with_a_job_taskkill_is_not_needed(self):
        job = procutil.JobObject()
        self.addCleanup(job.close)
        process = self.start_child()
        self.assertTrue(job.assign(process))
        grandchild = self.release(process)
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('taskkill was started')):
            procutil.terminate_tree(process, job)
        self.assertEqual(self.still_alive([process.pid, grandchild]), [])

    def waits_of(self, process):
        """Record the timeouts terminate_tree passes to process.wait()."""
        waits, real_wait = [], process.wait

        def wait(timeout=None):
            waits.append(timeout)
            return real_wait(timeout)

        patcher = mock.patch.object(process, 'wait', wait)
        patcher.start()
        self.addCleanup(patcher.stop)
        return waits

    def test_a_process_the_job_refused_is_not_waited_for(self):
        """conformance-1: the job kill cannot reach it, so the two seconds would only delay Stop."""
        job = procutil.JobObject()
        self.addCleanup(job.close)
        kernel, _calls = self.refusing(procutil._kernel32(), {job.handle})
        process = self.start_child()
        with mock.patch.object(procutil, '_kernel32', return_value=kernel):
            self.assertFalse(job.assign(process))
        grandchild = self.release(process)
        waits = self.waits_of(process)
        with mock.patch.object(job, 'terminate', wraps=job.terminate) as terminate:
            procutil.terminate_tree(process, job)
        terminate.assert_called_once_with()   # the job may hold other processes: it is still ended
        self.assertEqual(waits, [], 'terminate_tree waited for a job kill that cannot reach this process')
        self.assertEqual(self.still_alive([process.pid, grandchild]), [])

    def test_a_process_in_the_job_gets_its_two_seconds(self):
        job = procutil.JobObject()
        self.addCleanup(job.close)
        process = self.start_child()
        self.assertTrue(job.assign(process))
        waits = self.waits_of(process)
        procutil.terminate_tree(process, job)
        self.assertEqual(waits, [2])
        self.assertEqual(self.still_alive([process.pid]), [])
        # When Windows cannot tell whether the process is in the job, it waits as before.
        unknown = self.start_child()
        self.assertTrue(job.assign(unknown))
        waits = self.waits_of(unknown)
        with mock.patch.object(job, 'holds', return_value=None):
            procutil.terminate_tree(unknown, job)
        self.assertEqual(waits, [2])
        # Any other object with a terminate() still serves as a job.
        plain = self.start_child()

        class Plain:
            def terminate(self):
                return False

        procutil.terminate_tree(plain, Plain())
        self.assertEqual(self.still_alive([plain.pid]), [])

    def test_a_job_that_cannot_terminate_falls_back_to_taskkill(self):
        job = procutil.JobObject()
        job.close()  # handle None: terminate() is False
        process = self.start_child()
        grandchild = self.release(process)
        procutil.terminate_tree(process, job)
        self.assertEqual(self.still_alive([process.pid, grandchild]), [])

    def test_nothing_happens_for_none_and_for_a_finished_process(self):
        finished = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
        finished.wait(30)
        job = mock.Mock()
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('taskkill was started')):
            procutil.terminate_tree(None)
            procutil.terminate_tree(None, job)
            procutil.terminate_tree(finished)
            procutil.terminate_tree(finished, job)
        job.terminate.assert_not_called()

    def test_taskkill_failure_falls_back_to_kill(self):
        process = self.start_child()
        with mock.patch.object(subprocess, 'run', side_effect=OSError('no taskkill')):
            procutil.terminate_tree(process)
        self.assertEqual(self.still_alive([process.pid]), [])

    def test_runner_keeps_the_old_name(self):
        import engine
        self.assertIs(engine.Runner._terminate_process, procutil.terminate_tree)


class AliveAndEnvironmentTests(unittest.TestCase):
    def test_alive(self):
        self.assertTrue(procutil.alive(os.getpid()))
        self.assertFalse(procutil.alive(0))
        self.assertFalse(procutil.alive(None))
        finished = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
        finished.wait(30)
        self.assertFalse(procutil.alive(finished.pid))
        import library
        self.assertIs(library.alive, procutil.alive)

    def test_process_environment(self):
        environment = procutil.process_environment()
        self.assertEqual(environment['PATH'].split(os.pathsep)[0], str(paths.BIN))
        self.assertEqual((environment['PYTHONUTF8'], environment['PYTHONIOENCODING'],
                          environment['PYINSTALLER_RESET_ENVIRONMENT']), ('1', 'utf-8', '1'))
        self.assertEqual(environment['LOCALAPPDATA'], os.environ['LOCALAPPDATA'])
        self.assertIsNot(environment, os.environ)
        with mock.patch.dict(os.environ, {'PYINSTALLER_RESET_ENVIRONMENT': '1'}):
            worker = procutil.process_environment(reset=False)
        self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT', worker)
        self.assertEqual(worker['PYTHONUTF8'], '1')
        self.assertEqual(worker['PATH'].split(os.pathsep)[0], str(paths.BIN))
        with mock.patch.object(paths, 'BIN', Path('X:/other bin')):
            self.assertEqual(procutil.process_environment()['PATH'].split(os.pathsep)[0], str(Path('X:/other bin')))

    def test_hidden_flag(self):
        self.assertEqual(procutil.HIDDEN, subprocess.CREATE_NO_WINDOW)


class SingleInstanceTests(unittest.TestCase):
    """R14."""

    def test_second_holder_of_a_name_gets_none(self):
        name = unique_name()
        first = procutil.single_instance(name)
        self.assertTrue(first)
        try:
            self.assertIsNone(procutil.single_instance(name))
            self.assertIsNone(procutil.single_instance(name))
            other = procutil.single_instance(unique_name())
            self.assertTrue(other)
            procutil.release_instance(other)
        finally:
            procutil.release_instance(first)
        again = procutil.single_instance(name)
        self.assertTrue(again, 'the name is free again once the holder released it')
        procutil.release_instance(again)

    def test_environment_name_is_used_without_an_argument(self):
        name = unique_name()
        with mock.patch.dict(os.environ, {'YTDLP_DESKTOP_INSTANCE': name}):
            first = procutil.single_instance()
            self.assertTrue(first)
            try:
                self.assertIsNone(procutil.single_instance(name), 'the mutex does not carry the environment name')
                self.assertIsNone(procutil.single_instance())
            finally:
                procutil.release_instance(first)

    def test_default_name_is_the_module_constant(self):
        self.assertEqual(procutil.INSTANCE_NAME, 'Local\\YtDlpDesktop.Instance')
        name = unique_name()  # never the real name: the owner's running app holds that one
        with mock.patch.dict(os.environ), mock.patch.object(procutil, 'INSTANCE_NAME', name):
            os.environ.pop('YTDLP_DESKTOP_INSTANCE', None)
            first = procutil.single_instance()
            self.assertTrue(first)
            try:
                self.assertIsNone(procutil.single_instance(name))
            finally:
                procutil.release_instance(first)

    def test_an_api_failure_does_not_keep_the_app_from_starting(self):
        with mock.patch.object(procutil, '_kernel32', side_effect=OSError('no kernel32')), \
                self.assertLogs('YtDlpDesktop.procutil', 'WARNING'):
            held = procutil.single_instance(unique_name())
        self.assertTrue(held)
        procutil.release_instance(held)
        procutil.release_instance(None)

    def test_second_launch_of_the_app_exits_at_once_without_a_window(self):
        self.assertTrue(os.environ['YTDLP_DESKTOP_INSTANCE'].startswith('Local\\YtDlpDesktop.Test.'))
        log = _sandbox.state_dir() / 'download.log'
        self.assertFalse(log.exists(), 'this test must run before anything logs into the sandbox')
        held = procutil.single_instance()  # the sandbox's own name, as a running first instance would
        self.assertTrue(held)
        try:
            process = subprocess.Popen([sys.executable, '-B', str(_sandbox.ROOT / 'verification' / 'sandbox_app.py')],
                                       cwd=str(_sandbox.ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                                       creationflags=procutil.HIDDEN)
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                procutil.terminate_tree(process)
                output, _ = process.communicate(timeout=30)
                self.fail('the second launch did not exit within 10 s (it opened a window?)\n' + output)
            self.assertEqual(process.returncode, 0, output)
            self.assertIn(f'BASE {_sandbox.BASE}', output)
            # It returned before the GUI branch did anything: no log file, no settings, no history.
            self.assertFalse(log.exists(), output)
            self.assertFalse((_sandbox.state_dir() / 'settings.json').exists())
            self.assertFalse((Path(os.environ['YTDLP_DESKTOP_APP_DIR']) / 'data').exists())
        finally:
            procutil.release_instance(held)


class WindowTests(unittest.TestCase):
    def test_focus_window_finds_a_tk_window_by_title_suffix(self):
        import tkinter as tk
        title = 'focus-test-' + uuid.uuid4().hex
        self.assertFalse(procutil.focus_window(title, timeout=0.3), 'no such window yet')
        root = tk.Tk()
        try:
            root.title('一个前缀 — ' + title)
            root.geometry('240x60+60+60')
            root.attributes('-alpha', 0.0)
            root.update()
            started = time.monotonic()
            self.assertTrue(procutil.focus_window(title, timeout=3.0))
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertFalse(procutil.focus_window(title + '-other', timeout=0.3))
            self.assertFalse(procutil.focus_window('一个前缀', timeout=0.3), 'a prefix is not a suffix')
            root.iconify()
            root.update()
            self.assertEqual(root.state(), 'iconic')
            self.assertTrue(procutil.focus_window(title, timeout=3.0))
            deadline = time.monotonic() + 3
            while root.state() != 'normal' and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            self.assertEqual(root.state(), 'normal', 'a minimised window is restored')
            hwnd = int(root.frame(), 16)   # how the window will hand its handle to procutil
            self.assertIsInstance(procutil.is_foreground(hwnd), bool)
            self.assertIsNone(procutil.flash_window(hwnd))
        finally:
            root.destroy()
        self.assertFalse(procutil.focus_window(title, timeout=0.3), 'the window is gone')

    def test_is_foreground_and_flash_never_raise(self):
        self.assertFalse(procutil.is_foreground(0x7FFFFFF0))
        self.assertIsNone(procutil.flash_window(0))
        with mock.patch.object(procutil, '_user32', side_effect=OSError('no user32')):
            self.assertFalse(procutil.is_foreground(1))
            self.assertIsNone(procutil.flash_window(1))
            self.assertFalse(procutil.focus_window('x', timeout=0.1))

    def test_flash_window_fills_the_structure(self):
        calls = []

        def flash(pointer):
            info = pointer._obj
            calls.append((info.cbSize, info.hwnd, info.dwFlags, info.uCount, info.dwTimeout))
            return 0

        with mock.patch.object(procutil, '_user32', return_value=mock.Mock(FlashWindowEx=flash)):
            procutil.flash_window(0x1234)
        import ctypes
        self.assertEqual(calls, [(ctypes.sizeof(procutil._FlashInfo), 0x1234, 0x2 | 0xC, 0, 0)])


class RevealTests(unittest.TestCase):
    """No Explorer window is opened: os.startfile, the shell call and Popen are replaced."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.file = self.folder / '标题 文件.m4a'
        self.file.write_bytes(b'x')

    def test_missing_file_opens_the_folder_and_returns_false(self):
        with mock.patch.object(os, 'startfile') as startfile, \
                mock.patch.object(procutil, '_shell_select') as select, \
                mock.patch.object(subprocess, 'Popen') as popen:
            self.assertFalse(procutil.reveal_in_folder(self.folder / 'gone.m4a'))
        startfile.assert_called_once_with(self.folder)
        select.assert_not_called()
        popen.assert_not_called()

    def test_missing_folder_returns_false_without_raising(self):
        with mock.patch.object(os, 'startfile', side_effect=FileNotFoundError('gone')), \
                mock.patch.object(subprocess, 'Popen') as popen:
            self.assertFalse(procutil.reveal_in_folder(self.folder / 'no folder' / 'gone.m4a'))
        popen.assert_not_called()

    def test_existing_file_is_selected_through_the_shell(self):
        with mock.patch.object(os, 'startfile') as startfile, \
                mock.patch.object(procutil, '_shell_select', return_value=True) as select, \
                mock.patch.object(subprocess, 'Popen') as popen:
            self.assertTrue(procutil.reveal_in_folder(str(self.file)))
        select.assert_called_once_with(self.file)
        startfile.assert_not_called()
        popen.assert_not_called()

    def test_shell_call_gets_an_item_id_list_of_the_file(self):
        """The real ole32 / shell32 calls up to the one that would open the window."""
        calls = []

        def select(item, count, children, flags):
            calls.append((bool(item), count, children, flags))
            return results.pop(0)

        results = [0, -2147467259]   # S_OK, then E_FAIL
        with mock.patch.object(procutil._shell32(), 'SHOpenFolderAndSelectItems', select):
            self.assertTrue(procutil._shell_select(self.file))
            self.assertFalse(procutil._shell_select(self.file))
            self.assertFalse(procutil._shell_select(self.folder / 'no such file.m4a'), 'no item for a missing file')
        self.assertEqual(calls, [(True, 0, None, 0)] * 2)

    def test_shell_failure_falls_back_to_explorer(self):
        for failure in (dict(return_value=False), dict(side_effect=OSError('shell32'))):
            with self.subTest(failure=failure), \
                    mock.patch.object(procutil, '_shell_select', **failure), \
                    mock.patch.object(subprocess, 'Popen') as popen:
                self.assertTrue(procutil.reveal_in_folder(self.file))
                popen.assert_called_once_with(f'explorer /select,"{self.file}"')
        with mock.patch.object(procutil, '_shell_select', return_value=False), \
                mock.patch.object(subprocess, 'Popen', side_effect=OSError('no explorer')):
            self.assertFalse(procutil.reveal_in_folder(self.file))


if __name__ == '__main__':
    unittest.main()
