"""paths.py, settings.py, applog.py and library.open_library (1.6.0 WP1).

R7: settings.json that is not an object, not JSON, has a BOM or wrong value types;
unknown keys round-trip; saving is atomic and leaves no temp file.

Fix round 1: the state and app folders are always absolute; the purge only cleans folders
the app created (marker file) or a state folder with the app's own name, and never the
file of an engine update that is still running; catalog_path and open_library ask Windows
whether the database can really be written (a folder whose access rights refuse writing is
made with icacls inside the test's own temp folder and reset afterwards); a save is
repeated while another handle has the target open, and readers of this process take turns
with the writers.
"""
import _sandbox
import builtins
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import applog
import engine
import library
import paths
import procutil
import settings

EVERYONE = '*S-1-1-0'   # the well-known group, by SID: its name differs between Windows languages
REPLACE_PAUSE = paths.REPLACE_PAUSE   # the real value; the tests run with a shorter one


def icacls(folder, *arguments):
    return subprocess.run(['icacls', str(folder), *arguments], capture_output=True, timeout=60,
                          creationflags=procutil.HIDDEN).returncode


def allow_writing(folder):
    icacls(folder, '/reset', '/T', '/C', '/Q')


def deny_writing(test, folder, inherit=True):
    """Refuse new files in a folder through its access rights, as a protected install folder
    does; with ``inherit`` the files in it cannot be written either.  Undone at clean-up."""
    test.addCleanup(allow_writing, folder)
    if icacls(folder, '/deny', f'{EVERYONE}:{"(OI)(CI)" if inherit else ""}(WD,AD,DC)', '/Q') != 0:
        test.skipTest('icacls could not change the access rights of the test folder')


class TempCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'state'
        self.app = self.root / 'app'
        patcher = _sandbox.state_patch(self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        app_patch = mock.patch.object(paths, 'APP_DIR', self.app)
        app_patch.start()
        self.addCleanup(app_patch.stop)
        # A save that cannot replace its target tries again a few times; the tests do not wait for that.
        pause = mock.patch.object(paths, 'REPLACE_PAUSE', 0.001)
        pause.start()
        self.addCleanup(pause.stop)

    def names(self, folder):
        return sorted(path.name for path in Path(folder).iterdir()) if Path(folder).is_dir() else None


class PathsTests(TempCase):
    def child(self, overrides, removed=(), prelude='', cwd=None):
        """What a fresh process computes at import, as plain strings."""
        environment = os.environ.copy()
        for name in removed:
            environment.pop(name, None)
        environment.update(overrides)
        code = ('import json, sys\n' + prelude + 'sys.path.insert(0, sys.argv[1])\nimport paths\n'
                'from pathlib import Path\n'
                'print(json.dumps({"resource": str(paths.RESOURCE_ROOT), "vendor": str(paths.VENDOR), '
                '"bin": str(paths.BIN), "app": str(paths.APP_DIR), "state": str(paths.STATE_DIR), '
                '"home": str(Path.home()), "created": [str(p) for p in (paths.APP_DIR / "data", paths.tmp_dir()) '
                'if p.exists()]}))\n')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(_sandbox.SRC)], capture_output=True,
                                encoding='utf-8', env=environment, timeout=60, creationflags=procutil.HIDDEN,
                                cwd=None if cwd is None else str(cwd))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_environment_overrides(self):
        app, state = self.root / '应用 目录', self.root / '状态 目录'
        both = self.child({'YTDLP_DESKTOP_APP_DIR': str(app), 'YTDLP_DESKTOP_STATE': str(state)})
        self.assertEqual((both['app'], both['state']), (str(app), str(state)))
        self.assertEqual(both['resource'], str(_sandbox.ROOT))
        self.assertEqual(both['vendor'], str(_sandbox.ROOT / 'vendor'))
        self.assertEqual(both['bin'], str(_sandbox.ROOT / 'vendor' / 'bin'))
        self.assertEqual(both['created'], [], 'importing paths and calling its functions creates nothing')
        # Without YTDLP_DESKTOP_STATE: LOCALAPPDATA\YtDlpDesktop (here the sandbox's).
        local = self.child({'YTDLP_DESKTOP_APP_DIR': str(app)}, removed=('YTDLP_DESKTOP_STATE',))
        self.assertEqual(local['state'], str(Path(os.environ['LOCALAPPDATA']) / 'YtDlpDesktop'))
        # Without LOCALAPPDATA: the home folder (only computed; nothing is created there).
        home = self.child({'YTDLP_DESKTOP_APP_DIR': str(app)}, removed=('YTDLP_DESKTOP_STATE', 'LOCALAPPDATA'))
        self.assertEqual(home['state'], str(Path(home['home']) / 'YtDlpDesktop'))
        # Without YTDLP_DESKTOP_APP_DIR, from source: the repository root.
        source = self.child({'YTDLP_DESKTOP_STATE': str(state)}, removed=('YTDLP_DESKTOP_APP_DIR',))
        self.assertEqual(source['app'], str(_sandbox.ROOT))
        self.assertEqual(source['created'], [])
        # An empty or blank value counts as not set.
        for nothing in ('', '  '):
            with self.subTest(value=nothing):
                empty = self.child({'YTDLP_DESKTOP_APP_DIR': nothing, 'YTDLP_DESKTOP_STATE': nothing})
                self.assertEqual(empty['app'], str(_sandbox.ROOT))
                self.assertEqual(empty['state'], str(Path(os.environ['LOCALAPPDATA']) / 'YtDlpDesktop'))
                # Also LOCALAPPDATA: the home folder then, not a folder relative to wherever the app was started.
                blank = self.child({'YTDLP_DESKTOP_APP_DIR': str(app), 'LOCALAPPDATA': nothing},
                                   removed=('YTDLP_DESKTOP_STATE',))
                self.assertEqual(blank['state'], str(Path(blank['home']) / 'YtDlpDesktop'))
                self.assertTrue(Path(blank['state']).is_absolute())
        # Blanks around a value are not part of the folder name.
        padded = self.child({'YTDLP_DESKTOP_APP_DIR': f' {app} ', 'YTDLP_DESKTOP_STATE': f'{state}  '})
        self.assertEqual((padded['app'], padded['state']), (str(app), str(state)))

    def test_state_and_app_folder_are_always_absolute(self):
        """The engine starts in vendor\\bin: a relative folder would mean another place there."""
        start = self.root / '启动 目录'
        start.mkdir()
        relative = self.child({'YTDLP_DESKTOP_APP_DIR': '应用', 'YTDLP_DESKTOP_STATE': '状态'}, cwd=start)
        self.assertEqual((relative['app'], relative['state']), (str(start / '应用'), str(start / '状态')))
        dotted = self.child({'YTDLP_DESKTOP_APP_DIR': '.', 'YTDLP_DESKTOP_STATE': 'a\\..\\b\\'}, cwd=start)
        self.assertEqual((dotted['app'], dotted['state']), (str(start), str(start / 'b')))
        for result in (relative, dotted):
            self.assertTrue(Path(result['app']).is_absolute() and Path(result['state']).is_absolute())
            self.assertEqual(result['created'], [])
        self.assertEqual(self.names(start), [], 'nothing is created in the start folder')

    def test_frozen_build_uses_the_exe_folder_and_the_unpack_folder(self):
        exe_folder, internal = self.root / '安装 目录', self.root / '安装 目录' / '_internal'
        internal.mkdir(parents=True)
        prelude = (f'sys.frozen = True\nsys._MEIPASS = {str(internal)!r}\n'
                   f'sys.executable = {str(exe_folder / "YT-DLP下载器.exe")!r}\n')
        frozen = self.child({}, removed=('YTDLP_DESKTOP_APP_DIR', 'YTDLP_DESKTOP_STATE'), prelude=prelude)
        self.assertEqual(frozen['app'], str(exe_folder.resolve()))
        self.assertEqual(frozen['resource'], str(internal))
        self.assertEqual(frozen['bin'], str(internal / 'vendor' / 'bin'))
        override = self.child({'YTDLP_DESKTOP_APP_DIR': str(self.root / 'other')},
                              removed=('YTDLP_DESKTOP_STATE',), prelude=prelude)
        self.assertEqual(override['app'], str(self.root / 'other'))

    def test_every_function_reads_the_attributes_when_called(self):
        expected = {'settings_file': self.state / 'settings.json', 'failed_file': self.state / 'failed-downloads.json',
                    'log_file': self.state / 'download.log', 'engines_dir': self.state / 'engines',
                    'tmp_dir': self.state / 'tmp', 'work_dir': self.state / 'work',
                    'collect_dir': self.state / 'collect', 'cache_dir': self.state / 'cache',
                    'data_dir': self.app / 'data', 'default_output': self.app / '下载文件',
                    'catalog_path': self.state / 'library.sqlite3'}
        for name, path in expected.items():
            self.assertEqual(getattr(paths, name)(), path, name)
        other = self.root / 'second'
        with mock.patch.object(paths, 'STATE_DIR', other / 's'), mock.patch.object(paths, 'APP_DIR', other / 'a'):
            self.assertEqual(paths.settings_file(), other / 's' / 'settings.json')
            self.assertEqual(paths.tmp_dir(), other / 's' / 'tmp')
            self.assertEqual(paths.default_output(), other / 'a' / '下载文件')
            self.assertEqual(paths.data_dir(), other / 'a' / 'data')
        self.assertEqual(self.names(self.root), [], 'no path function creates a file or folder')

    def test_engine_keeps_the_old_names_as_copies(self):
        self.assertEqual(engine.RESOURCE_ROOT, paths.RESOURCE_ROOT)
        self.assertEqual(engine.VENDOR, paths.VENDOR)
        self.assertEqual(engine.BIN, paths.BIN)
        self.assertEqual(library.CATALOG.name, 'library.sqlite3')
        self.assertEqual(library.CATALOG.parent.name, 'data')
        # Engine code follows paths.STATE_DIR, the copy engine.STATE_DIR is only for importers.
        engine.store_failed_job(['https://example.invalid/a'], engine.Options(self.root / 'out'))
        self.assertEqual(self.names(self.state), ['failed-downloads.json'])
        self.assertEqual(engine.load_failed_job()['urls'], ['https://example.invalid/a'])

    def test_catalog_path(self):
        data = self.app / 'data'
        catalog = data / 'library.sqlite3'
        fallback = self.state / 'library.sqlite3'
        self.assertEqual(paths.catalog_path(), fallback, 'no data folder')
        data.mkdir(parents=True)
        self.assertEqual(paths.catalog_path(), fallback, 'no file in the data folder')
        catalog.mkdir()
        self.assertEqual(paths.catalog_path(), fallback, 'a folder is not the database')
        catalog.rmdir()
        catalog.write_bytes(b'')
        self.assertEqual(paths.catalog_path(), catalog, 'the file beside the app, writable')
        os.chmod(catalog, stat.S_IREAD)
        try:
            self.assertEqual(paths.catalog_path(), fallback, 'a read-only file')
        finally:
            os.chmod(catalog, stat.S_IWRITE | stat.S_IREAD)
        self.assertEqual(paths.catalog_path(), catalog)
        # The folder takes no new file (SQLite could not create its journal there).
        real_open = os.open

        def no_new_file(path, flags, *arguments, **keywords):
            if flags & os.O_CREAT and Path(path).parent == data:
                raise PermissionError(13, 'Permission denied')
            return real_open(path, flags, *arguments, **keywords)

        with mock.patch.object(os, 'open', no_new_file):
            self.assertEqual(paths.catalog_path(), fallback, 'a folder that cannot be written')
        # The file does not open for writing although no read-only attribute says so.
        real_file = builtins.open

        def no_write(file, mode='r', *arguments, **keywords):
            if '+' in mode and Path(file) == catalog:
                raise PermissionError(13, 'Permission denied')
            return real_file(file, mode, *arguments, **keywords)

        with mock.patch.object(builtins, 'open', no_write):
            self.assertEqual(paths.catalog_path(), fallback, 'a file that cannot be written')
        self.assertEqual(paths.catalog_path(), catalog)
        self.assertEqual(self.names(data), ['library.sqlite3'], 'the write check leaves nothing in the data folder')
        self.assertEqual(catalog.read_bytes(), b'')
        self.assertEqual(self.names(self.state), None, 'catalog_path creates nothing')

    def test_catalog_path_in_a_folder_whose_access_rights_refuse_writing(self):
        """The usual Windows form of a read-only folder; os.access does not see it."""
        data = self.app / 'data'
        catalog, fallback = data / 'library.sqlite3', self.state / 'library.sqlite3'
        data.mkdir(parents=True)
        catalog.write_bytes(b'kept')
        self.assertEqual(paths.catalog_path(), catalog)
        deny_writing(self, data, inherit=False)   # no new file in the folder; the file itself stays writable
        self.assertTrue(os.access(catalog, os.W_OK) and os.access(data, os.W_OK), 'os.access knows no access rights')
        self.assertEqual(paths.catalog_path(), fallback, 'the folder refuses new files')
        allow_writing(data)
        self.assertEqual(paths.catalog_path(), catalog)
        deny_writing(self, data)                  # inherited by the file: it cannot be written either
        self.assertTrue(os.access(catalog, os.W_OK), 'os.access knows no access rights')
        self.assertEqual(paths.catalog_path(), fallback, 'the file refuses writing')
        allow_writing(data)
        self.assertEqual(paths.catalog_path(), catalog)
        self.assertEqual(self.names(data), ['library.sqlite3'])
        self.assertEqual(catalog.read_bytes(), b'kept')
        self.assertEqual(self.names(self.state), None)

    def test_atomic_write_text(self):
        target = self.root / 'new' / 'deep' / '设置.json'
        paths.atomic_write_text(target, '第一版\n')
        self.assertEqual(target.read_text(encoding='utf-8'), '第一版\n')
        with mock.patch.object(os, 'fsync', wraps=os.fsync) as fsync, \
                mock.patch.object(os, 'replace', wraps=os.replace) as replace:
            paths.atomic_write_text(str(target), '第二版')
        self.assertEqual(fsync.call_count, 1)
        temporary = Path(replace.call_args.args[0])
        self.assertEqual((temporary.parent, temporary.name), (target.parent, f'设置.json.{os.getpid()}.tmp'))
        self.assertEqual(target.read_text(encoding='utf-8'), '第二版')
        self.assertEqual(self.names(target.parent), ['设置.json'])
        paths.atomic_write_text(target, 'gbk 文本', encoding='gbk')
        self.assertEqual(target.read_bytes(), 'gbk 文本'.encode('gbk'))

    def test_atomic_write_failure_keeps_the_old_file_and_leaves_no_temp_file(self):
        target = self.root / 'settings.json'
        paths.atomic_write_text(target, 'old')
        with mock.patch.object(os, 'replace', side_effect=PermissionError(13, 'locked')) as replace:
            with self.assertRaises(PermissionError):
                paths.atomic_write_text(target, 'new')
        self.assertEqual(replace.call_count, paths.REPLACE_ATTEMPTS, 'a locked target is tried a few times, then given up')
        self.assertEqual(target.read_text(encoding='utf-8'), 'old')
        self.assertEqual(self.names(self.root), ['settings.json'])
        # Only "the target is open elsewhere" is worth another try.
        with mock.patch.object(os, 'replace', side_effect=OSError(28, 'No space left on device')) as replace:
            with self.assertRaises(OSError):
                paths.atomic_write_text(target, 'new')
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(self.names(self.root), ['settings.json'])
        # The folder cannot be created because a file is in the way.
        with self.assertRaises(OSError):
            paths.atomic_write_text(target / 'below-a-file' / 'x.json', 'x')
        # The text cannot be encoded: nothing of it reaches the target.
        with self.assertRaises(ValueError):
            paths.atomic_write_text(target, '中文', encoding='ascii')
        self.assertEqual(target.read_text(encoding='utf-8'), 'old')
        self.assertEqual(self.names(self.root), ['settings.json'])

    def test_atomic_write_from_several_threads(self):
        target = self.root / 'shared.json'
        failures = []

        def writer(number):
            try:
                for index in range(25):
                    paths.atomic_write_text(target, json.dumps({'writer': number, 'index': index, 'pad': 'x' * 2000}))
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=writer, args=(number,)) for number in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(failures, [])
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['index'], 24)
        self.assertEqual(self.names(self.root), ['shared.json'])

    def test_atomic_write_waits_for_a_handle_that_has_the_target_open(self):
        """Windows refuses os.replace while any handle has the target open (another version of
        the app reading it, a scanner); such a handle is gone a moment later."""
        target = self.root / 'settings.json'
        paths.atomic_write_text(target, 'old')
        real_replace, attempts = os.replace, []

        def replace(source, destination):
            attempts.append(time.monotonic())
            return real_replace(source, destination)

        with mock.patch.object(paths, 'REPLACE_PAUSE', 0.1), mock.patch.object(os, 'replace', replace):
            with open(target, 'rb') as foreign:
                threading.Timer(0.15, foreign.close).start()
                paths.atomic_write_text(target, 'new')
        self.assertGreater(len(attempts), 1, 'the first attempt must have met the open handle')
        self.assertLessEqual(len(attempts), paths.REPLACE_ATTEMPTS)
        self.assertEqual(target.read_text(encoding='utf-8'), 'new')
        self.assertEqual(self.names(self.root), ['settings.json'])
        # A handle that stays open longer than the attempts last: the old content survives, no temp file.
        with open(target, 'rb'):
            with self.assertRaises(PermissionError):
                paths.atomic_write_text(target, 'never')
        self.assertEqual(target.read_text(encoding='utf-8'), 'new')
        self.assertEqual(self.names(self.root), ['settings.json'])
        self.assertEqual((paths.REPLACE_ATTEMPTS, REPLACE_PAUSE), (6, 0.05), 'at most a quarter of a second per save')

    def test_readers_of_this_process_take_turns_with_the_writers(self):
        """bughunt-7: a thread reading the file must not make a save in another thread fail."""
        target = self.root / 'failed-downloads.json'
        paths.atomic_write_text(target, json.dumps({'index': -1}))
        stop, problems, reads = threading.Event(), [], []

        def reader():
            while not stop.is_set():
                try:
                    reads.append(json.loads(paths.read_text(target))['index'])
                    json.loads(paths.read_bytes(target).decode('utf-8'))
                except Exception as error:
                    problems.append(error)

        def writer(number):
            try:
                for index in range(40):
                    paths.atomic_write_text(target, json.dumps({'writer': number, 'index': index, 'pad': 'x' * 3000}))
            except Exception as error:
                problems.append(error)

        # No second try here: every collision between a reader and a writer would show.
        with mock.patch.object(paths, 'REPLACE_ATTEMPTS', 1):
            readers = [threading.Thread(target=reader) for _ in range(2)]
            writers = [threading.Thread(target=writer, args=(number,)) for number in range(4)]
            for thread in readers + writers:
                thread.start()
            for thread in writers:
                thread.join(60)
            stop.set()
            for thread in readers:
                thread.join(60)
        self.assertEqual(problems, [])
        self.assertTrue(reads, 'the readers never got a turn')
        self.assertEqual(self.names(self.root), ['failed-downloads.json'])
        self.assertEqual(paths.read_text(target, encoding='utf-8'), target.read_text(encoding='utf-8'))
        self.assertEqual(paths.read_bytes(target), target.read_bytes())
        with self.assertRaises(FileNotFoundError):
            paths.read_text(self.root / 'missing.json')
        with self.assertRaises(FileNotFoundError):
            paths.read_bytes(self.root / 'missing.json')
        # The race above is a matter of luck; this is not: both readers wait while a writer has the lock.
        for read in (paths.read_text, paths.read_bytes):
            with self.subTest(read=read.__name__):
                done = threading.Event()
                thread = threading.Thread(target=lambda: (read(target), done.set()))
                with paths._write_lock:
                    thread.start()
                    self.assertFalse(done.wait(0.3), 'the reader did not wait for the writer')
                self.assertTrue(done.wait(30))
                thread.join(30)

    def test_the_apps_own_readers_use_the_shared_lock(self):
        """settings.load, engine.load_failed_job and the engine receipt go through paths.read_*."""
        paths.atomic_write_text(paths.settings_file(), '{}')
        engine.store_failed_job(['https://example.invalid/a'], engine.Options(self.root / 'out'))
        with mock.patch.object(paths, 'read_bytes', wraps=paths.read_bytes) as read_bytes, \
                mock.patch.object(paths, 'read_text', wraps=paths.read_text) as read_text:
            settings.load()
            self.assertEqual(engine.load_failed_job()['urls'], ['https://example.invalid/a'])
        self.assertEqual([Path(call.args[0]) for call in read_bytes.call_args_list], [paths.settings_file()])
        self.assertEqual([Path(call.args[0]) for call in read_text.call_args_list], [paths.failed_file()])

    def test_own_dir_marks_only_a_folder_it_created(self):
        created = paths.own_dir(self.state / 'tmp')
        self.assertEqual(created, self.state / 'tmp')
        self.assertEqual(self.names(created), ['.ytdlp-desktop'])
        self.assertEqual((created / paths.OWNED).read_bytes(), b'')
        self.assertEqual(paths.own_dir(created), created, 'a second call changes nothing')
        (created / paths.OWNED).unlink()
        paths.own_dir(created)
        self.assertEqual(self.names(created), [], 'a folder that was already there is never marked afterwards')
        found = self.state / 'work'
        found.mkdir()
        (found / 'thesis.docx').write_bytes(b'x')
        self.assertEqual(paths.own_dir(str(found)), found)
        self.assertEqual(self.names(found), ['thesis.docx'])
        (self.state / 'a-file').write_bytes(b'')
        with self.assertRaises(OSError):
            paths.own_dir(self.state / 'a-file')
        with self.assertRaises(OSError):
            paths.own_dir(self.state / 'a-file' / 'below')
        self.assertEqual((self.state / 'a-file').read_bytes(), b'')

    def test_run_dir(self):
        first = paths.run_dir(paths.tmp_dir())
        second = paths.run_dir(paths.tmp_dir())
        for folder in (first, second):
            self.assertTrue(folder.is_dir() and folder.is_absolute())
            self.assertEqual(folder.parent, paths.tmp_dir())
            self.assertRegex(folder.name, rf'^run-{os.getpid()}-[0-9a-f]{{8}}$')
            self.assertTrue(paths.RUN_FOLDER.match(folder.name))
        self.assertNotEqual(first, second)
        self.assertEqual(self.names(paths.tmp_dir()), sorted(['.ytdlp-desktop', first.name, second.name]))
        # A relative parent (a patched STATE_DIR) still gives an absolute folder.
        before = os.getcwd()
        os.chdir(self.root)
        try:
            relative = paths.run_dir(Path('relative') / 'collect')
        finally:
            os.chdir(before)
        self.assertTrue(relative.is_absolute())
        self.assertEqual(relative.parent, self.root / 'relative' / 'collect')
        self.assertTrue(relative.is_dir())
        # One attempt, then OSError; tempfile.mkdtemp would try on and on where new entries are refused.
        calls = []

        def mkdir(path, *arguments, **keywords):
            calls.append(path.name)
            raise FileExistsError(17, 'exists') if path == paths.tmp_dir() else PermissionError(13, 'denied')

        with mock.patch.object(Path, 'mkdir', mkdir):
            with self.assertRaises(PermissionError):
                paths.run_dir(paths.tmp_dir())
        self.assertEqual(len(calls), 2, calls)
        self.assertEqual(calls[0], 'tmp')
        self.assertTrue(calls[1].startswith('run-'), calls)

    def test_run_dir_gives_up_at_once_where_the_access_rights_refuse_new_folders(self):
        parent = paths.own_dir(paths.tmp_dir())
        deny_writing(self, parent, inherit=False)
        result = []

        def attempt():
            try:
                result.append(paths.run_dir(parent))
            except OSError as error:
                result.append(error)

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive(), 'run_dir is still trying (tempfile.mkdtemp would, for hours)')
        self.assertIsInstance(result[0], PermissionError)
        allow_writing(parent)
        self.assertEqual(self.names(parent), ['.ytdlp-desktop'])

    def dead_pid(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
        process.wait(30)
        self.addCleanup(lambda: process)  # keeps the handle, so Windows cannot hand the pid out again
        self.assertFalse(procutil.alive(process.pid))
        return process.pid

    def fill(self, dead, mark):
        """Leftovers of earlier runs in tmp, collect, work and engines of the current state folder.

        ``mark``: the folders are created the way the app does it (own_dir leaves its marker);
        otherwise they are plain folders, as somebody else would have made them.
        Returns what a purge has to leave: {folder name: sorted names}.
        """
        own, marker = os.getpid(), ['.ytdlp-desktop'] if mark else []
        make = paths.own_dir if mark else (lambda folder: folder.mkdir(parents=True))
        for folder in (paths.tmp_dir(), paths.collect_dir()):
            make(folder)
            for name in (f'run-{dead}-abc', f'run-{dead}-', f'run-{own}-abc', 'run-notapid-abc', f'run{dead}-abc',
                         'other'):
                (folder / name / '_MEI12345').mkdir(parents=True)
                (folder / name / '_MEI12345' / 'python311.dll').write_bytes(b'x')
            (folder / f'run-{dead}-file').write_bytes(b'a file, not a run folder')
        work = paths.work_dir()
        make(work)
        (work / 'sub').mkdir()
        now = time.time()
        # final-architecture-1: one age rule for both work folders (paths.clean_work): covers and
        # ffmpeg temporaries go after WORK_STRAY_AGE (a day), every other file after WORK_MAX_AGE.
        ages = {'old.part': 8 * 86400, 'old.ytdl': 8 * 86400, 'old.webm': 8 * 86400,
                'fresh.part': 2 * 3600, 'fresh.ytdl': 2 * 3600, 'stray.jpg': 25 * 3600, 'stray.temp': 25 * 3600,
                'young.jpg': 3700, 'x [a].f137.mp4': 2 * 86400,
                'new.webm': 600, 'new.part': 600, '标题 [abc].f251.webm.part': 6 * 86400}
        for name, age in ages.items():
            (work / name).write_bytes(b'x')
            os.utime(work / name, (now - age, now - age))
        if mark:   # the marker is older than every age limit and still has to stay
            os.utime(work / paths.OWNED, (now - 30 * 86400, now - 30 * 86400))
        engines = paths.engines_dir()
        make(engines)
        for name in ('yt-dlp.exe', 'yt-dlp.json', 'yt-dlp.download', f'yt-dlp.download.{dead}.1.exe',
                     f'yt-dlp.download.{dead}.12.exe', f'yt-dlp.download.{own}.3.exe', 'yt-dlp.download-notes.txt',
                     'yt-dlp.download.backup', f'yt-dlp.download.{dead}.exe', 'my yt-dlp.download'):
            (engines / name).write_bytes(b'x')
        (engines / 'yt-dlp.download.7.7.exe').mkdir()   # a folder, whatever its name
        (paths.STATE_DIR / 'settings.json').write_text('{}', encoding='utf-8')
        runs = [f'run-{own}-abc', 'run-notapid-abc', f'run{dead}-abc', f'run-{dead}-file', 'other']
        return {'tmp': sorted(marker + runs), 'collect': sorted(marker + runs),
                'work': sorted(marker + ['fresh.part', 'fresh.ytdl', 'new.webm', 'new.part', 'sub',
                                         '标题 [abc].f251.webm.part', 'young.jpg', 'x [a].f137.mp4']),
                'engines': sorted(marker + ['yt-dlp.exe', 'yt-dlp.json', f'yt-dlp.download.{own}.3.exe',
                                            'yt-dlp.download-notes.txt', 'yt-dlp.download.backup',
                                            f'yt-dlp.download.{dead}.exe', 'my yt-dlp.download',
                                            'yt-dlp.download.7.7.exe'])}

    def tree(self, folder):
        return sorted(str(path.relative_to(folder)) for path in Path(folder).rglob('*'))

    def test_purge_stale(self):
        dead = self.dead_pid()
        kept = self.fill(dead, mark=True)

        paths.purge_stale(procutil.alive)

        self.assertEqual({name: self.names(self.state / name) for name in kept}, kept)
        self.assertTrue((paths.tmp_dir() / f'run-{os.getpid()}-abc' / '_MEI12345' / 'python311.dll').is_file())
        self.assertEqual(self.names(self.state), ['collect', 'engines', 'settings.json', 'tmp', 'work'])

    def test_purge_stale_leaves_folders_alone_that_the_app_did_not_create(self):
        """bughunt-1: YTDLP_DESKTOP_STATE on a shared folder that has its own tmp, work, collect, engines."""
        dead = self.dead_pid()
        self.fill(dead, mark=False)
        before = self.tree(self.state)

        paths.purge_stale(procutil.alive)
        paths.purge_engine_downloads(procutil.alive)

        self.assertEqual(self.tree(self.state), before, 'the purge deleted from a folder that is not the app\'s own')
        # The app then works there: it uses the folders it found and marks none of them.
        paths.run_dir(paths.tmp_dir()).rmdir()
        paths.run_dir(paths.collect_dir()).rmdir()
        paths.own_dir(paths.work_dir())
        paths.own_dir(paths.engines_dir())
        paths.purge_stale(procutil.alive)
        self.assertEqual(self.tree(self.state), before)

    def test_purge_stale_in_a_state_folder_with_the_apps_own_name(self):
        """The default location, ...\\YtDlpDesktop: its folders are the app's, marker or not
        (the engines folder there was created by 1.5.1)."""
        dead = self.dead_pid()
        for number, name in enumerate(('YtDlpDesktop', 'ytdlpdesktop')):
            with self.subTest(name=name), _sandbox.state_patch(self.root / f'profile-{number}' / name):
                kept = self.fill(dead, mark=False)
                paths.purge_stale(procutil.alive)
                self.assertEqual({key: self.names(paths.STATE_DIR / key) for key in kept}, kept)

    def test_purge_stale_refuses_a_relative_state_folder_and_a_drive_root(self):
        dead = self.dead_pid()
        self.fill(dead, mark=True)
        before = self.tree(self.state)
        for state in (Path('Z:/'), Path('//server/share/'), Path(self.state.anchor), Path('state'), Path('.')):
            with self.subTest(state=state), mock.patch.object(paths, 'STATE_DIR', state):
                self.assertFalse(paths._purge_allowed())
        with mock.patch.object(paths, 'STATE_DIR', self.state):
            self.assertTrue(paths._purge_allowed())
        # The purge itself: it does not even look.  (_entries is replaced, so that nothing could be
        # deleted here even by a purge that did.)
        listed = []
        current = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.object(paths, '_entries', lambda folder: listed.append(folder) or []):
                for state in (Path('state'), Path('Z:/')):
                    with self.subTest(state=state), mock.patch.object(paths, 'STATE_DIR', state):
                        self.assertTrue(state.is_absolute() or (state / 'tmp' / paths.OWNED).is_file())
                        paths.purge_stale(lambda pid: False)
                        paths.purge_engine_downloads(lambda pid: False)
                self.assertEqual(listed, [], 'the purge looked into a folder')
                paths.purge_stale(lambda pid: False)   # the absolute folder of the test: it does look
                # tmp, collect, work, the state folder itself (temp files of killed saves), engines.
                self.assertEqual(len(listed), 5)
        finally:
            os.chdir(current)
        self.assertEqual(self.tree(self.state), before)

    def test_purge_engine_downloads_spares_an_update_that_is_running(self):
        """bughunt-4: between its download and its installation the file of a running update is closed."""
        engines = paths.own_dir(paths.engines_dir())
        dead, own = self.dead_pid(), os.getpid()
        other = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE,
                                 creationflags=procutil.HIDDEN)   # a second instance of the app, still running
        self.addCleanup(other.wait, 30)
        self.addCleanup(other.stdin.close)
        names = {'mine': f'yt-dlp.download.{own}.1.exe', 'other': f'yt-dlp.download.{other.pid}.4.exe',
                 'dead': f'yt-dlp.download.{dead}.2.exe', 'legacy': 'yt-dlp.download'}
        for purge in (lambda: paths.purge_engine_downloads(procutil.alive), lambda: paths.purge_stale(procutil.alive)):
            for name in names.values():
                (engines / name).write_bytes(b'x')
            purge()
            self.assertEqual(self.names(engines), sorted(['.ytdlp-desktop', names['mine'], names['other']]))
        calls = []
        paths.purge_engine_downloads(lambda pid: calls.append(pid) or True)
        self.assertEqual(sorted(calls), sorted([own, other.pid]), 'alive() is asked with the pid in the file name')

    def test_purge_stale_never_raises(self):
        paths.purge_stale(procutil.alive)  # nothing exists
        paths.purge_engine_downloads(procutil.alive)
        self.assertFalse(self.state.exists(), 'the purge creates nothing')
        (paths.own_dir(paths.tmp_dir()) / 'run-1-a').mkdir()
        (paths.own_dir(paths.work_dir()) / 'x.webm').write_bytes(b'x')
        (paths.own_dir(paths.engines_dir()) / 'yt-dlp.download.1.1.exe').write_bytes(b'x')

        def broken(pid):
            raise RuntimeError('alive is broken')

        paths.purge_stale(broken)
        paths.purge_stale(None)
        paths.purge_engine_downloads(broken)
        paths.purge_engine_downloads(None)
        with mock.patch.object(paths.shutil, 'rmtree', side_effect=OSError('locked')) as rmtree, \
                mock.patch.object(Path, 'unlink', side_effect=PermissionError('locked')) as unlink:
            old = time.time() - 30 * 86400
            os.utime(paths.work_dir() / 'x.webm', (old, old))
            paths.purge_stale(lambda pid: False)
        self.assertEqual((rmtree.call_count, unlink.call_count), (1, 2), 'the purge did try all three')
        self.assertEqual(self.names(paths.tmp_dir()), ['.ytdlp-desktop', 'run-1-a'])
        self.assertEqual(self.names(paths.work_dir()), ['.ytdlp-desktop', 'x.webm'])
        self.assertEqual(self.names(paths.engines_dir()), ['.ytdlp-desktop', 'yt-dlp.download.1.1.exe'])
        with mock.patch.object(paths, 'STATE_DIR', self.root / 'a-file'):
            (self.root / 'a-file').write_bytes(b'')
            paths.purge_stale(procutil.alive)
            paths.purge_engine_downloads(procutil.alive)


class SettingsTests(TempCase):
    """R7."""

    BOOLEANS = ('batch_safe', 'archive_enabled', 'embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded',
                'notify_done', 'clipboard_hint', 'check_engine_updates', 'zoomed')
    STRINGS = ('output', 'mode', 'resolution', 'archive_root', 'cookies', 'naming', 'latest_engine', 'last_clip',
               'geometry', 'appearance')
    NUMBERS = ('last_engine_check', 'bot_check_at')

    def write(self, content):
        self.state.mkdir(parents=True, exist_ok=True)
        path = paths.settings_file()
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                            encoding='utf-8')
        return path

    def assert_typed(self, values):
        self.assertEqual(set(values), set(self.BOOLEANS + self.STRINGS + self.NUMBERS) | {'_extra'})
        for key in self.BOOLEANS:
            self.assertIs(type(values[key]), bool, key)
        for key in self.STRINGS:
            self.assertIs(type(values[key]), str, key)
        for key in self.NUMBERS:
            self.assertIs(type(values[key]), float, key)
            self.assertGreaterEqual(values[key], 0, key)
        self.assertIn(values['naming'], ('title', 'clean', 'title_id'))
        self.assertIn(values['appearance'], ('light', 'dark', 'system'))
        self.assertTrue(values['output'].strip())
        self.assertIsInstance(values['_extra'], dict)

    def defaults(self):
        output = str(self.app / '下载文件')
        return {'output': output, 'mode': engine.MODES[0], 'resolution': engine.RESOLUTIONS[0], 'batch_safe': True,
                'archive_enabled': False, 'archive_root': str(Path(output) / '资料归档'), 'cookies': '',
                'naming': 'title', 'embed_metadata': True, 'embed_cover': True, 'subtitles': False,
                'skip_downloaded': True, 'notify_done': True, 'clipboard_hint': True, 'check_engine_updates': True,
                'last_engine_check': 0.0, 'bot_check_at': 0.0, 'latest_engine': '', 'last_clip': '', 'geometry': '',
                'zoomed': False, 'appearance': 'light', '_extra': {}}

    def test_missing_file_gives_the_defaults_and_creates_nothing(self):
        values = settings.load()
        self.assertEqual(values, self.defaults())
        self.assert_typed(values)
        self.assertFalse(self.state.exists())
        self.assertEqual({**settings.DEFAULTS, '_extra': {}}, self.defaults())
        self.assertEqual(settings.defaults(), settings.DEFAULTS)
        with mock.patch.object(paths, 'APP_DIR', self.root / 'moved'):
            self.assertEqual(settings.DEFAULTS['output'], str(self.root / 'moved' / '下载文件'))
        self.assertIsNot(settings.load(), settings.load())

    def test_unreadable_files_become_settings_bad_json(self):
        cases = {'[]': '[]', 'null': 'null', 'number': '17', 'string': '"text"', 'truncated': '{"output": "D:\\\\Mus',
                 'empty': '', 'not utf-8': b'\xff\xfe{\x00}\x00', 'garbage': b'\x00\x01\x02 not json',
                 'two objects': '{} {}'}
        for name, content in cases.items():
            with self.subTest(case=name):
                path = self.write(content)
                original = path.read_bytes()
                (self.state / 'settings.bad.json').unlink(missing_ok=True)
                with self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
                    values = settings.load()
                self.assertEqual(values, self.defaults())
                self.assertEqual(self.names(self.state), ['settings.bad.json'])
                self.assertEqual((self.state / 'settings.bad.json').read_bytes(), original)
                self.assertEqual(settings.load(), self.defaults(), 'the next start is quiet')

    def test_a_second_bad_file_never_replaces_the_first(self):
        """final-data-safety-4: the first set-aside copy may hold the owner's only record of the values."""
        self.write('{"output": "D:\\\\owner\\\\music",')
        with self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
            settings.load()
        for content in ('', '[2]'):
            self.write(content)
            with self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
                settings.load()
        self.assertEqual((self.state / 'settings.bad.json').read_text(encoding='utf-8'), '{"output": "D:\\\\owner\\\\music",')
        later = sorted(name for name in self.names(self.state) if name != 'settings.bad.json')
        self.assertEqual(len(later), 2, later)
        for name in later:
            self.assertRegex(name, r'^settings\.bad-\d{8}-\d{6}(?:-\d+)?\.json$')
        self.assertEqual(sorted((self.state / name).read_text(encoding='utf-8') for name in later), ['', '[2]'])
        # The free name is taken with os.rename, which never replaces a file.
        with mock.patch.object(os, 'replace', side_effect=AssertionError('os.replace is not used')):
            self.write('[3]')
            with self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
                settings.load()
        self.assertEqual(len(self.names(self.state)), 4)

    def test_bom_and_valid_object_is_loaded(self):
        stored = {'output': 'D:\\音乐', 'mode': engine.MODES[3], 'resolution': '1080p', 'batch_safe': False,
                  'archive_enabled': True, 'archive_root': 'E:\\资料库', 'cookies': 'C:\\cookies.txt'}
        path = self.write(b'\xef\xbb\xbf' + json.dumps(stored, ensure_ascii=False, indent=2).encode('utf-8'))
        values = settings.load()
        self.assertEqual({key: values[key] for key in stored}, stored)
        self.assertEqual(values['naming'], 'title')
        self.assert_typed(values)
        self.assertEqual(self.names(self.state), ['settings.json'], 'a readable file is never set aside')
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'), 'loading does not rewrite the file')

    def test_the_file_1_5_1_writes_is_loaded_unchanged(self):
        stored = {'output': 'D:\\YT-DLP下载器\\下载文件', 'mode': '音频 · WAV 32 位', 'resolution': '最佳',
                  'batch_safe': True, 'archive_enabled': False, 'archive_root': 'D:\\YT-DLP下载器\\下载文件\\资料归档',
                  'cookies': ''}
        self.write(json.dumps(stored, ensure_ascii=False, indent=2))
        values = settings.load()
        self.assertEqual({key: values[key] for key in stored}, stored)
        self.assertEqual(values['_extra'], {})

    def test_wrong_types_fall_back_per_key(self):
        wrong = {'output': 5, 'mode': 7, 'resolution': None, 'batch_safe': 'no', 'archive_enabled': 'yes',
                 'archive_root': [], 'cookies': {}, 'naming': 'weird', 'embed_metadata': 1, 'embed_cover': 0,
                 'subtitles': None, 'skip_downloaded': 'true', 'notify_done': [], 'clipboard_hint': {},
                 'check_engine_updates': 'on', 'last_engine_check': 'yesterday', 'bot_check_at': -5,
                 'latest_engine': 2026, 'last_clip': None, 'geometry': 1120, 'zoomed': 'true', 'appearance': 'blue'}
        self.write(wrong)
        values = settings.load()
        self.assertEqual(values, self.defaults())
        self.assertEqual(self.names(self.state), ['settings.json'], 'wrong value types do not make the file bad')
        # One bad value does not cost the good ones.
        self.write({'output': 'D:\\Music', 'mode': 7, 'batch_safe': False, 'naming': 'clean', 'zoomed': 1,
                    'last_engine_check': True, 'bot_check_at': float('nan'), 'appearance': 'dark'})
        values = settings.load()
        self.assertEqual((values['output'], values['mode'], values['batch_safe'], values['naming'], values['zoomed'],
                          values['last_engine_check'], values['bot_check_at'], values['appearance']),
                         ('D:\\Music', engine.MODES[0], False, 'clean', False, 0.0, 0.0, 'dark'))
        self.assertEqual(values['archive_root'], str(Path('D:\\Music') / '资料归档'),
                         'the default archive root follows the loaded output')

    def test_valid_values_of_every_key_are_kept(self):
        stored = {'output': 'E:\\下载', 'mode': '视频 · MP4 兼容', 'resolution': '720p', 'batch_safe': False,
                  'archive_enabled': True, 'archive_root': 'E:\\库', 'cookies': 'E:\\c.txt', 'naming': 'title_id',
                  'embed_metadata': False, 'embed_cover': False, 'subtitles': True, 'skip_downloaded': False,
                  'notify_done': False, 'clipboard_hint': False, 'check_engine_updates': False,
                  'last_engine_check': 1794000000.5, 'bot_check_at': 1794000100, 'latest_engine': '2026.11.01',
                  'last_clip': 'da39a3ee5e6b4b0d3255bfef95601890afd80709', 'geometry': '1280x900+10+20',
                  'zoomed': True, 'appearance': 'system'}
        self.write(stored)
        values = settings.load()
        self.assertEqual({key: values[key] for key in stored}, {**stored, 'bot_check_at': 1794000100.0})
        self.assert_typed(values)
        # mode and resolution are only type-checked here; membership is the window's business.
        # (WP2: the stored mode is a 1.6.0 format now, still none of the five the 1.5.1 window knows.)
        self.assertNotIn(values['mode'], engine.MODES[:5])

    def test_quotes_around_the_output_folder_are_stripped(self):
        for raw, expected in (('"D:\\My Music"', 'D:\\My Music'), ('  D:\\My Music  ', 'D:\\My Music'),
                              (' "D:\\My Music" ', 'D:\\My Music'), ('""', str(self.app / '下载文件')),
                              ('   ', str(self.app / '下载文件'))):
            with self.subTest(raw=raw):
                self.write({'output': raw})
                values = settings.load()
                self.assertEqual(values['output'], expected)
                self.assertEqual(values['archive_root'], str(Path(expected) / '资料归档'))

    def test_unknown_keys_round_trip(self):
        self.write({'output': 'D:\\Music', 'future_list': [1, {'深': '层'}], 'future_flag': True, 'mode': engine.MODES[2],
                    '_private_of_another_version': 3})
        values = settings.load()
        self.assertEqual(values['_extra'], {'future_list': [1, {'深': '层'}], 'future_flag': True,
                                            '_private_of_another_version': 3})
        values['batch_safe'] = False
        values['_session_only'] = 'never written'
        self.assertIs(settings.save(values), True)
        written = json.loads(paths.settings_file().read_text(encoding='utf-8'))
        self.assertEqual(written['future_list'], [1, {'深': '层'}])
        self.assertIs(written['future_flag'], True)
        self.assertEqual(written['_private_of_another_version'], 3)
        self.assertNotIn('_extra', written)
        self.assertNotIn('_session_only', written)
        self.assertEqual((written['output'], written['mode'], written['batch_safe']),
                         ('D:\\Music', engine.MODES[2], False))
        again = settings.load()
        values.pop('_session_only')
        self.assertEqual(again, values)
        # A known key always wins over a stray copy in _extra.
        settings.save({'output': 'D:\\A', '_extra': {'output': 'D:\\B', 'kept': 1}})
        self.assertEqual(json.loads(paths.settings_file().read_text(encoding='utf-8')), {'output': 'D:\\A', 'kept': 1})

    def test_save_is_atomic_and_readable_by_1_5_1(self):
        values = settings.load()
        values.update(output='D:\\音乐', cookies='C:\\Users\\某人\\cookies.txt', geometry='1120x810+5+5')
        with mock.patch.object(os, 'fsync', wraps=os.fsync) as fsync, \
                mock.patch.object(os, 'replace', wraps=os.replace) as replace:
            self.assertIs(settings.save(values), True)
        self.assertEqual((fsync.call_count, replace.call_count), (1, 1))
        self.assertEqual(self.names(self.state), ['settings.json'], 'no temp file is left')
        raw = paths.settings_file().read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        # What studio.py of 1.5.1 does with the file (it shares it as the rollback version).
        legacy = json.loads(paths.settings_file().read_text(encoding='utf-8'))
        self.assertIsInstance(legacy, dict)
        self.assertEqual(list(legacy)[:7], ['output', 'mode', 'resolution', 'batch_safe', 'archive_enabled',
                                            'archive_root', 'cookies'])
        self.assertIn(legacy.get('mode', engine.MODES[0]), engine.MODES)
        self.assertIn(legacy.get('resolution'), engine.RESOLUTIONS)
        self.assertIs(type(legacy['batch_safe']), bool)
        self.assertIs(type(legacy['archive_enabled']), bool)
        self.assertEqual(legacy['cookies'], 'C:\\Users\\某人\\cookies.txt')
        self.assertEqual(settings.load(), values)

    def test_failed_save_returns_false_and_keeps_the_old_file(self):
        values = settings.load()
        self.assertTrue(settings.save(values))
        before = paths.settings_file().read_bytes()
        values['output'] = 'D:\\Changed'
        with mock.patch.object(os, 'replace', side_effect=PermissionError(13, 'locked')), \
                self.assertLogs('YtDlpDesktop.settings', 'ERROR'):
            self.assertIs(settings.save(values), False)
        self.assertEqual(paths.settings_file().read_bytes(), before)
        self.assertEqual(self.names(self.state), ['settings.json'])
        with self.assertLogs('YtDlpDesktop.settings', 'ERROR'):
            self.assertIs(settings.save({'output': {1, 2}}), False, 'a value JSON cannot hold')
        self.assertEqual(paths.settings_file().read_bytes(), before)
        blocker = self.root / 'a-file'
        blocker.write_bytes(b'')
        with mock.patch.object(paths, 'STATE_DIR', blocker / 'state'), \
                self.assertLogs('YtDlpDesktop.settings', 'ERROR'):
            self.assertIs(settings.save(values), False, 'the state folder cannot be created')
            self.assertEqual(settings.load()['output'], str(self.app / '下载文件'))

    def test_saving_while_another_thread_loads_never_fails(self):
        """bughunt-7: the window thread reads the file while a worker saves it (and the other way round)."""
        values = settings.load()
        self.assertTrue(settings.save(values))
        stop, failures, loaded = threading.Event(), [], []

        def loader():
            while not stop.is_set():
                loaded.append(settings.load()['geometry'])

        def saver(number):
            for index in range(40):
                if not settings.save({**values, 'geometry': f'{number}x{index}'}):
                    failures.append((number, index))

        with mock.patch.object(paths, 'REPLACE_ATTEMPTS', 1), self.assertNoLogs('YtDlpDesktop.settings', 'WARNING'):
            loaders = [threading.Thread(target=loader) for _ in range(2)]
            savers = [threading.Thread(target=saver, args=(number,)) for number in range(4)]
            for thread in loaders + savers:
                thread.start()
            for thread in savers:
                thread.join(60)
            stop.set()
            for thread in loaders:
                thread.join(60)
        self.assertEqual(failures, [])
        self.assertTrue(loaded and all(isinstance(text, str) for text in loaded))
        self.assertEqual(self.names(self.state), ['settings.json'], 'no temp file and no settings.bad.json')
        self.assertRegex(settings.load()['geometry'], r'^\dx39$')

    def test_fuzz_never_raises_and_always_gives_typed_values(self):
        generator = random.Random(160)
        pool = [None, True, False, 0, 1, -1, 2.5, float('nan'), float('inf'), '', ' ', 'x', '"q"', 'title', 'clean',
                'dark', 'system', engine.MODES[1], '1080p', 'D:\\目录', [], [1], {}, {'a': 1}, 10 ** 30]
        keys = list(self.BOOLEANS + self.STRINGS + self.NUMBERS) + ['unknown', '_extra', '_x', '']
        for round_number in range(300):
            data = {key: generator.choice(pool) for key in generator.sample(keys, generator.randint(0, len(keys)))}
            self.write(json.dumps(data, ensure_ascii=False))
            values = settings.load()
            self.assert_typed(values)
            self.assertNotIn('settings.bad.json', self.names(self.state), data)
            self.assertIs(settings.save(values), True, data)
            self.assertEqual(self.names(self.state), ['settings.json'])
            again = settings.load()
            self.assertEqual(repr(again), repr(values), f'load/save/load is not stable in round {round_number}: {data}')
        for round_number in range(100):
            content = bytes(generator.randrange(256) for _ in range(generator.randint(0, 120)))
            self.write(content)
            try:
                json.loads(content.decode('utf-8-sig'))
                continue  # by chance valid JSON
            except ValueError:
                pass
            (self.state / 'settings.bad.json').unlink(missing_ok=True)
            with self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
                values = settings.load()
            self.assertEqual(values, self.defaults())
            self.assertEqual(self.names(self.state), ['settings.bad.json'])


def hold(path):
    """Open a file without sharing it, as a backup or scanner tool may (share mode 0).  Returns
    a function that closes it."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), 'the test could not hold the file')
    closed = []

    def release():
        if not closed:
            closed.append(kernel.CloseHandle(handle))
    return release


class HeldSettingsTests(TempCase):
    """final-data-safety-3: settings.json held by another program while the app starts."""

    write = SettingsTests.write
    defaults = SettingsTests.defaults
    OWNER = {'output': 'D:\\owner\\music', 'mode': '音频 · FLAC', 'batch_safe': False, 'cookies': 'C:\\owner\\cookies.txt',
             'future_key': [1, 2]}

    def test_a_file_held_for_a_moment_is_read(self):
        path = self.write(self.OWNER)
        release = hold(path)
        self.addCleanup(release)
        timer = threading.Timer(0.3, release)
        timer.start()
        self.addCleanup(timer.cancel)
        values = settings.load()
        self.assertEqual({key: values[key] for key in ('output', 'mode', 'batch_safe', 'cookies')},
                         {key: self.OWNER[key] for key in ('output', 'mode', 'batch_safe', 'cookies')})
        self.assertNotIn(settings.HELD, values)

    def test_a_file_held_longer_is_never_overwritten_with_the_defaults(self):
        path = self.write(self.OWNER)
        original = path.read_bytes()
        release = hold(path)
        self.addCleanup(release)
        with mock.patch.object(settings, 'READ_RETRY', 0.2), self.assertLogs('YtDlpDesktop.settings', 'WARNING') as logs:
            values = settings.load()
        self.assertTrue(any('暂时无法读取' in line for line in logs.output), logs.output)
        self.assertIn(settings.HELD, values)
        self.assertEqual({key: value for key, value in values.items() if key != settings.HELD}, self.defaults())
        # What the window does: a copy of its prefs with the variables, saved after a change and on close.
        changed = dict(values, naming='title_id', geometry='1200x900+0+0')
        with mock.patch.object(settings, 'READ_RETRY', 0.2), self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
            self.assertIs(settings.save(changed), False, 'nothing is written while the file cannot be read')
        release()
        self.assertEqual(path.read_bytes(), original)
        # Once it can be read: the stored values stay, only what was changed in this session is written.
        self.assertIs(settings.save(dict(values, naming='title_id', geometry='1200x900+0+0')), True)
        written = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(written, {**self.OWNER, 'naming': 'title_id', 'geometry': '1200x900+0+0'})
        # A later save of the session still merges (its other values are still the defaults); a value
        # changed in this session is written also when it is changed back to its default.
        self.assertIs(settings.save(dict(values, naming='title', geometry='1200x900+0+0', subtitles=True)), True)
        again = settings.load()
        self.assertEqual((again['output'], again['mode'], again['cookies'], again['naming'], again['batch_safe'],
                          again['subtitles']),
                         ('D:\\owner\\music', '音频 · FLAC', 'C:\\owner\\cookies.txt', 'title', False, True))
        self.assertEqual(again['_extra'], {'future_key': [1, 2]})
        self.assertEqual(self.names(self.state), ['settings.json'])

    def test_a_held_file_that_disappears_gets_the_changed_values_only(self):
        path = self.write(self.OWNER)
        release = hold(path)
        self.addCleanup(release)
        with mock.patch.object(settings, 'READ_RETRY', 0.1), self.assertLogs('YtDlpDesktop.settings', 'WARNING'):
            values = settings.load()
        release()
        path.unlink()
        self.assertIs(settings.save(dict(values, naming='clean')), True)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'naming': 'clean'})
        self.assertEqual(settings.load()['naming'], 'clean')


class StateTempFileTests(TempCase):
    """final-data-safety-5: what a killed save leaves in the state and engines folders."""

    def test_temp_files_of_dead_processes_are_removed(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
        dead.wait(30)
        own = os.getpid()
        self.state.mkdir(parents=True)
        engines = paths.own_dir(paths.engines_dir())
        removed = [f'settings.json.{dead.pid}.tmp', f'failed-downloads.json.{dead.pid}.tmp',
                   f'failed-downloads.json.{dead.pid}.overflow.tmp']
        kept = [f'settings.json.{own}.tmp', f'failed-downloads.json.{own}.overflow.tmp', 'settings.json',
                'notes.tmp', f'other.json.{dead.pid}.tmp', f'settings.json.{dead.pid}.tmp.txt', 'settings.json.x.tmp']
        for name in removed + kept:
            (self.state / name).write_bytes(b'x')
        (self.state / f'failed-downloads.json.{dead.pid}.tmp.d').mkdir()
        folder_named_like_one = self.state / f'settings.json.{dead.pid + 1}.tmp'
        folder_named_like_one.mkdir()
        (engines / f'yt-dlp.json.{dead.pid}.tmp').write_bytes(b'x')
        (engines / f'yt-dlp.json.{own}.tmp').write_bytes(b'x')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'target.txt').write_bytes(b'x')
        link = self.state / f'settings.json.{dead.pid + 2}.tmp'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True,
                              creationflags=procutil.HIDDEN).returncode == 0
        try:
            paths.purge_stale(lambda pid: pid == own)
            names = self.names(self.state)
            for name in removed:
                self.assertNotIn(name, names)
            for name in kept + [f'failed-downloads.json.{dead.pid}.tmp.d', folder_named_like_one.name]:
                self.assertIn(name, names)
            self.assertEqual(self.names(engines), sorted(['.ytdlp-desktop', f'yt-dlp.json.{own}.tmp']))
            if made:
                self.assertTrue((outside / 'target.txt').is_file(), 'nothing is deleted through a link')
                self.assertTrue(os.path.lexists(link))
        finally:
            if made:
                subprocess.run(['cmd', '/c', 'rmdir', str(link)], capture_output=True, creationflags=procutil.HIDDEN)


class OpenLibraryTests(TempCase):
    def test_default_path_is_the_catalog_path(self):
        store = library.Library()
        self.assertEqual(store.path, (self.state / 'library.sqlite3').resolve())
        beside = library.Library(paths.data_dir() / 'library.sqlite3')   # what 1.5.1 created beside the app
        self.assertEqual(library.Library().path, beside.path)
        opened, warning = library.open_library()
        self.assertEqual((opened.path, warning), (beside.path, ''))
        explicit, warning = library.open_library(self.root / 'explicit' / 'x.sqlite3')
        self.assertEqual((explicit.path, warning), ((self.root / 'explicit' / 'x.sqlite3').resolve(), ''))
        self.assertEqual(explicit.history(), [])

    def test_falls_back_when_the_path_is_a_directory(self):
        folder = self.root / 'a-folder.sqlite3'
        folder.mkdir()
        store, warning = library.open_library(folder)
        self.assertTrue(warning.startswith('历史记录本次不保存：'), warning)
        self.assertGreater(len(warning), len('历史记录本次不保存：'))
        self.assertNotEqual(store.path, folder.resolve())
        self.assertEqual(store.path.name, 'library.sqlite3')
        self.assertTrue(store.path.parent.name.startswith('ytdlp-history-'))
        self.assertTrue(store.path.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        job = store.create_job('download', '回退库', {'urls': ['https://example.invalid/a']})
        self.assertEqual(store.job(job)['title'], '回退库')
        self.assertEqual(list(folder.iterdir()), [])

    def test_falls_back_when_the_file_is_not_a_database(self):
        broken = self.root / 'broken.sqlite3'
        broken.write_bytes(b'this is not a database' * 100)
        store, warning = library.open_library(broken)
        self.assertTrue(warning.startswith('历史记录本次不保存：'), warning)
        self.assertEqual(store.history(), [])
        self.assertEqual(broken.read_bytes(), b'this is not a database' * 100, 'the unreadable file is left alone')

    def history(self, folder):
        """A database with one job, as an earlier session left it: (Library, path, bytes)."""
        folder.mkdir(parents=True, exist_ok=True)
        store = library.Library(folder / 'library.sqlite3')
        store.create_job('download', '以前的任务', {'urls': ['https://example.invalid/a']})
        return store, folder / 'library.sqlite3', (folder / 'library.sqlite3').read_bytes()

    def assert_fallback(self, opened, warning, original):
        self.assertTrue(warning.startswith('历史记录本次不保存：'), warning)
        self.assertGreater(len(warning), len('历史记录本次不保存：'))
        self.assertNotEqual(opened.path, original.path)
        self.assertTrue(opened.path.parent.name.startswith('ytdlp-history-'))
        job = opened.create_job('download', '回退库', {'urls': []})   # this session's history works
        self.assertEqual(opened.job(job)['title'], '回退库')

    def test_the_write_check_leaves_a_healthy_database_untouched(self):
        store, path, before = self.history(self.root / 'healthy')
        status = path.stat()
        opened, warning = library.open_library(path)
        self.assertEqual((opened.path, warning), (store.path, ''))
        self.assertEqual(path.read_bytes(), before, 'the check must not change the database file')
        self.assertEqual(path.stat().st_mtime_ns, status.st_mtime_ns)
        self.assertEqual(self.names(path.parent), ['library.sqlite3'], 'no journal file is left')
        self.assertEqual([row['title'] for row in opened.history()], ['以前的任务'])
        # The check runs on the file the Library resolved, once per open.
        with mock.patch.object(library, '_prove_writable', wraps=library._prove_writable) as check:
            library.open_library(path)
        check.assert_called_once_with(store.path)

    def test_falls_back_when_the_folder_refuses_writing(self):
        """bughunt-3: a protected install folder.  The database opens (creating the tables that
        exist writes nothing) and the first real write would fail."""
        for inherit, error in ((False, 'unable to open database file'), (True, 'readonly database')):
            with self.subTest(inherit=inherit):
                store, path, before = self.history(self.root / f'protected-{inherit}')
                deny_writing(self, path.parent, inherit=inherit)
                unchecked = library.Library(path)   # what Library() alone does: no error ...
                with self.assertRaisesRegex(sqlite3.OperationalError, error):
                    unchecked.create_job('download', '写不进去', {'urls': []})   # ... until the first write
                opened, warning = library.open_library(path)
                self.assert_fallback(opened, warning, store)
                self.assertIn(error, warning)
                allow_writing(path.parent)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.names(path.parent), ['library.sqlite3'])
                self.assertEqual(library.open_library(path)[1], '', 'writable again: no fallback')

    def test_falls_back_when_the_file_is_read_only(self):
        store, path, before = self.history(self.root / 'attribute')
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(os.chmod, path, stat.S_IWRITE | stat.S_IREAD)
        opened, warning = library.open_library(path)
        self.assert_fallback(opened, warning, store)
        self.assertEqual(path.read_bytes(), before)

    def test_a_database_that_is_only_busy_counts_as_writable(self):
        store, path, _before = self.history(self.root / 'busy')
        writer = sqlite3.connect(path, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')   # another window of the app in the middle of a write
        with mock.patch.object(library, 'WRITE_CHECK_TIMEOUT', 0.2):
            started = time.monotonic()
            opened, warning = library.open_library(path)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((opened.path, warning), (store.path, ''))
        writer.execute('ROLLBACK')
        self.assertEqual(library.WRITE_CHECK_TIMEOUT, 2)

    def test_default_path_moves_to_the_state_folder_when_the_one_beside_the_app_cannot_be_written(self):
        store, path, before = self.history(paths.data_dir())
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(os.chmod, path, stat.S_IWRITE | stat.S_IREAD)
        with self.assertLogs('YtDlpDesktop.library', 'WARNING') as logs:
            opened, warning = library.open_library()
        self.assertEqual((opened.path, warning), ((self.state / 'library.sqlite3').resolve(), ''))
        self.assertIn(str(path), logs.records[0].getMessage())
        self.assertEqual(path.read_bytes(), before)
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        with self.assertNoLogs('YtDlpDesktop.library', 'WARNING'):
            self.assertEqual(library.open_library()[0].path, store.path)

    def test_second_failure_propagates(self):
        folder = self.root / 'a-folder.sqlite3'
        folder.mkdir()
        with mock.patch.object(library.tempfile, 'mkdtemp', return_value=str(folder / 'missing' / 'deeper')), \
                mock.patch.object(Path, 'mkdir', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(OSError):
                library.open_library(folder)

    def test_write_json_is_the_atomic_writer(self):
        target = self.root / '资料信息.json'
        library.write_json(target, {'标题': '测试', 'files': []})
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'标题': '测试', 'files': []})
        self.assertIn('\n  "标题"', target.read_text(encoding='utf-8'))
        self.assertEqual(self.names(self.root), ['资料信息.json'])

    def test_the_archive_reads_its_file_in_turn_with_the_writer(self):
        """bughunt-7: a transcript attached in the window while the download thread saves the same file."""
        archive = library.Archive(self.root / 'archive')
        url = 'https://example.invalid/watch?v=1'
        folder = archive.prepare(url)
        media = folder / '媒体' / 'a.wav'
        media.parent.mkdir()
        media.write_bytes(b'x')
        transcript = self.root / '字幕.srt'
        transcript.write_text('1', encoding='utf-8')
        with mock.patch.object(paths, 'read_text', wraps=paths.read_text) as read_text:
            self.assertEqual(archive.prepare(url), folder)
            archive.media(folder, {'filepath': str(media), 'id': 'x', 'title': '标题'})
            archive.attach(folder, transcript)
        self.assertEqual([Path(call.args[0]) for call in read_text.call_args_list], [folder / '资料信息.json'] * 3)
        data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
        self.assertEqual((data['title'], data['files'], len(data['transcripts'])), ('标题', [str(Path('媒体') / 'a.wav')], 1))


class AppLogTests(TempCase):
    def setUp(self):
        super().setUp()
        applog.shutdown()
        self.addCleanup(applog.shutdown)

    def test_setup_creates_the_state_folder_and_a_real_file_handler(self):
        self.assertFalse(self.state.exists())
        logger = applog.setup()
        self.assertIs(logger, logging.getLogger('YtDlpDesktop'))
        self.assertEqual(applog.NAME, 'YtDlpDesktop')
        self.assertTrue(self.state.is_dir())
        self.assertEqual(len(logger.handlers), 1)
        handler = logger.handlers[0]
        self.assertIsInstance(handler, RotatingFileHandler)
        self.assertEqual(Path(handler.baseFilename).resolve(), paths.log_file().resolve())
        self.assertEqual((handler.maxBytes, handler.backupCount, handler.encoding), (3 * 1024 * 1024, 2, 'utf-8'))
        self.assertTrue(paths.log_file().is_file(), 'download.log exists as soon as logging is set up')
        self.assertIs(applog.setup(), logger)
        self.assertEqual(logger.handlers, [handler], 'setup is idempotent')
        self.assertIs(applog.get(), logger)
        self.assertEqual(applog.get('engine').name, 'YtDlpDesktop.engine')
        applog.get('engine').error('引擎出错 %s', 42)
        logger.info('界面的一行')   # what append_log of the 1.5.1 window does
        try:
            raise RuntimeError('带回溯')
        except RuntimeError:
            applog.get('ui').exception('界面错误')
        handler.flush()
        lines = paths.log_file().read_text(encoding='utf-8').splitlines()
        self.assertRegex(lines[0], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} E YtDlpDesktop\.engine 引擎出错 42$')
        self.assertRegex(lines[1], r' I YtDlpDesktop 界面的一行$')
        self.assertRegex(lines[2], r' E YtDlpDesktop\.ui 界面错误$')
        self.assertIn('Traceback (most recent call last):', lines)
        self.assertEqual(lines[-1], 'RuntimeError: 带回溯')
        applog.shutdown()
        self.assertEqual(logger.handlers, [])
        paths.log_file().unlink()   # the file is closed
        applog.shutdown()           # twice is harmless

    def test_the_1_5_1_window_adds_no_second_handler(self):
        logger = applog.setup()
        # studio.py:71-76 of 1.5.1.
        if not logger.handlers:
            self.fail('the 1.5.1 window would attach its own handler to a second open file')
        self.assertEqual(logger.level, logging.INFO)

    def test_setup_never_raises_and_falls_back_to_a_null_handler(self):
        blocker = self.root / 'a-file'
        blocker.write_bytes(b'')
        with mock.patch.object(paths, 'STATE_DIR', blocker / 'state'):
            logger = applog.setup()
        self.assertEqual([type(handler) for handler in logger.handlers], [logging.NullHandler])
        logger.error('goes nowhere, raises nothing')
        applog.shutdown()
        with mock.patch.object(applog, 'RotatingFileHandler', side_effect=RuntimeError('boom')):
            logger = applog.setup()
        self.assertEqual([type(handler) for handler in logger.handlers], [logging.NullHandler])

    def test_hooks_log_uncaught_exceptions_of_threads_and_of_the_main_thread(self):
        applog.setup()
        main_before, thread_before = sys.excepthook, threading.excepthook
        seen = []
        quiet_main = lambda kind, value, trace: seen.append(('main', kind))
        quiet_thread = lambda arguments: seen.append(('thread', arguments.exc_type))
        sys.excepthook, threading.excepthook = quiet_main, quiet_thread
        try:
            applog.install_hooks()
            self.assertIsNot(sys.excepthook, quiet_main)
            self.assertIsNot(threading.excepthook, quiet_thread)
            installed = (sys.excepthook, threading.excepthook)
            applog.install_hooks()
            self.assertEqual((sys.excepthook, threading.excepthook), installed, 'installing twice wraps once')

            def fail():
                raise ValueError('线程里的错误')

            worker = threading.Thread(target=fail, name='worker-7')
            worker.start()
            worker.join(10)
            try:
                raise KeyError('主线程里的错误')
            except KeyError:
                sys.excepthook(*sys.exc_info())
            try:
                raise KeyboardInterrupt()
            except KeyboardInterrupt:
                sys.excepthook(*sys.exc_info())
            logging.getLogger('YtDlpDesktop').handlers[0].flush()
            text = paths.log_file().read_text(encoding='utf-8')
            self.assertIn('E YtDlpDesktop unhandled exception in thread worker-7', text)
            self.assertIn('ValueError: 线程里的错误', text)
            self.assertIn('E YtDlpDesktop unhandled exception\n', text)
            self.assertIn("KeyError: '主线程里的错误'", text)
            self.assertNotIn('KeyboardInterrupt', text)
            self.assertEqual(seen, [('thread', ValueError), ('main', KeyError), ('main', KeyboardInterrupt)],
                             'the hooks that were there before still run')
            applog.shutdown()
            self.assertEqual((sys.excepthook, threading.excepthook), (quiet_main, quiet_thread))
        finally:
            applog.shutdown()
            sys.excepthook, threading.excepthook = main_before, thread_before


if __name__ == '__main__':
    unittest.main()
