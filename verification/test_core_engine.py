"""Engine-side changes of 1.6.0 WP1: the child process of _run_once (private TEMP, job
object, clean-up when emit raises: R12), Runner.__init__, the names 1.5.1 importers use,
the collector's final event, and the additive diagnostics keys.

The children are ``python -c`` scripts; no website and no yt-dlp process is involved.

Fix round 1: the private TEMP is always an absolute path, its parent folder carries the
app's marker file, and a state folder that refuses new folders does not hold the run up.
"""
import _sandbox
import ast
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import applog
import collector
import engine
import library
import paths
import procutil
import updater

# Prints its own pid and the pid of a sleeping grandchild, then what it sees as TEMP/TMP,
# leaves a file there, and waits to be stopped (or exits when asked).
CHILD = ('import json, os, subprocess, sys, time\n'
         'sys.stdout.reconfigure(encoding="utf-8")\n'
         'grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
         'open(os.path.join(os.environ["TEMP"], "left-by-the-child.bin"), "wb").write(b"x" * 1000)\n'
         'print(json.dumps({"pid": os.getpid(), "grandchild": grandchild.pid, "temp": os.environ["TEMP"], '
         '"tmp": os.environ["TMP"], "cwd": os.getcwd()}), flush=True)\n'
         'if "--exit" in sys.argv:\n'
         '    grandchild.kill()\n'
         '    sys.exit(0)\n'
         'time.sleep(120)\n')

NAMES_OF_1_5_1 = ('APP_NAME', 'APP_VERSION', 'RESOURCE_ROOT', 'VENDOR', 'BIN', 'APP_DIR', 'STATE_DIR', 'MODES',
                  'RESOLUTIONS', 'HIDDEN', 'RATE_LIMIT_COOLDOWN', 'MAX_RATE_LIMIT_COOLDOWN', 'MAX_RATE_LIMIT_RETRIES',
                  'RATE_LIMIT_PATTERN', 'SLEEP_PATTERN', 'RETRY_AFTER_PATTERN', 'BOT_CHECK_PATTERN', 'BOT_CHECK_HINT',
                  'BOT_CHECK_STALE_HINT', 'engine_path', 'engine_version', 'parse_urls', 'read_url_file', 'Options',
                  'check_cookie_file', 'store_failed_job', 'load_failed_job', 'build_command', 'number', 'parse_line',
                  'process_environment', 'Runner', 'diagnostics')
BACKUP = _sandbox.release_src('1.5.1')


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = _sandbox.state_patch(self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        # Stand-in processes of these tests cannot be put into a job; procutil would log that once.
        quiet = mock.patch.object(procutil, '_assign_failure_logged', True)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.events = []
        self.pids = []
        self.addCleanup(self.kill_leftovers)

    def kill_leftovers(self):
        for pid in self.pids:
            if procutil.alive(pid):
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                               creationflags=procutil.HIDDEN, timeout=30)

    def emit(self, kind, value):
        self.events.append((kind, value))

    def run_folders(self):
        """What is in the temp folder, the app's marker file aside."""
        folder = paths.tmp_dir()
        return sorted(path.name for path in folder.iterdir() if path.name != paths.OWNED) if folder.is_dir() else []

    def still_alive(self, pids, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(procutil.alive(pid) for pid in pids):
            time.sleep(0.05)
        return [pid for pid in pids if procutil.alive(pid)]

    def report(self, value):
        """The JSON line of the child, as the log event the loop made of it."""
        data = json.loads(value)
        self.pids += [data['pid'], data['grandchild']]
        return data


class RunOnceTests(EngineCase):
    def test_child_is_killed_and_temp_removed_when_emit_raises(self):
        """R12."""
        seen = {}

        def emit(kind, value):
            if kind == 'log' and 'report' not in seen:
                seen['report'] = self.report(value)
                seen['folders'] = self.run_folders()
                seen['file'] = (Path(seen['report']['temp']) / 'left-by-the-child.bin').is_file()
                raise RuntimeError('library is locked')

        runner = engine.Runner(emit)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'library is locked'):
            runner._run_once([sys.executable, '-c', CHILD], 1, 1, [])
        self.assertLess(time.monotonic() - started, 20)
        report = seen['report']
        self.assertEqual(self.still_alive([report['pid'], report['grandchild']]), [],
                         'the child (or what it started) outlived a _run_once that raised')
        self.assertIsNone(runner.process)
        self.assertEqual(len(seen['folders']), 1, 'one private temp folder while the child ran')
        self.assertTrue(seen['file'])
        self.assertEqual(self.run_folders(), [], 'the private temp folder was not removed')

    def test_child_sees_a_private_temp_folder_that_is_removed_afterwards(self):
        files = []
        runner = engine.Runner(self.emit)
        result = runner._run_once([sys.executable, '-c', CHILD, '--exit'], 1, 1, files)
        self.assertEqual(result, (0, False, None))
        report = self.report(next(value for kind, value in self.events if kind == 'log'))
        temp = Path(report['temp'])
        self.assertEqual(report['tmp'], report['temp'])
        self.assertEqual(temp.parent, paths.tmp_dir())
        self.assertTrue(temp.is_absolute())
        self.assertTrue(temp.name.startswith(f'run-{os.getpid()}-'), temp.name)
        self.assertTrue(paths.RUN_FOLDER.match(temp.name), 'the start-up purge must recognise the folder')
        self.assertTrue((paths.tmp_dir() / paths.OWNED).is_file(), 'the temp folder is created as the app\'s own')
        self.assertEqual(Path(report['cwd']), paths.BIN)
        self.assertNotEqual(Path(os.environ['TEMP']), temp, 'only the child gets the private folder')
        self.assertFalse(temp.exists())
        self.assertEqual(self.run_folders(), [])
        self.assertTrue(paths.tmp_dir().is_dir())
        self.assertIsNone(runner.process)

    def test_each_run_gets_its_own_folder(self):
        runner = engine.Runner(self.emit)
        for _ in range(2):
            runner._run_once([sys.executable, '-c', CHILD, '--exit'], 1, 1, [])
        reports = [self.report(value) for kind, value in self.events if kind == 'log']
        self.assertEqual(len(reports), 2)
        self.assertNotEqual(reports[0]['temp'], reports[1]['temp'])
        self.assertEqual(self.run_folders(), [])

    def test_cancelled_call_creates_no_folder_and_no_process(self):
        runner = engine.Runner(self.emit)
        runner.cancelled.set()
        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('a process was started')):
            self.assertEqual(runner._run_once([sys.executable, '-c', CHILD], 1, 1, []), (-1, False, None))
        self.assertFalse(paths.tmp_dir().exists(), 'a cancelled call must not even create the temp folder')
        self.assertEqual(self.events, [])

    def test_failed_process_start_leaves_no_folder(self):
        runner = engine.Runner(self.emit)
        with self.assertRaises(OSError):
            runner._run_once([str(self.root / 'no such folder' / 'missing.exe')], 1, 1, [])
        self.assertEqual(self.run_folders(), [])
        self.assertIsNone(runner.process)
        seen = []

        def popen(*arguments, **keywords):
            seen.append(Path(keywords['env']['TEMP']).is_dir())
            raise KeyboardInterrupt()

        with mock.patch.object(subprocess, 'Popen', popen), self.assertRaises(KeyboardInterrupt):
            runner._run_once([sys.executable, '-c', 'pass'], 1, 1, [])
        self.assertEqual(seen, [True])
        self.assertEqual(self.run_folders(), [])

    def test_without_a_usable_state_folder_the_child_keeps_the_normal_temp(self):
        blocker = self.root / 'a-file'
        blocker.write_bytes(b'')
        runner = engine.Runner(self.emit)
        with mock.patch.object(paths, 'STATE_DIR', blocker / 'state'):
            result = runner._run_once([sys.executable, '-c', 'import os; print(os.environ["TEMP"], flush=True)'], 1, 1, [])
        self.assertEqual(result, (0, False, None))
        self.assertEqual([value for kind, value in self.events if kind == 'log'], [os.environ['TEMP']])

    def test_child_gets_an_absolute_temp_even_from_a_relative_state_folder(self):
        """bughunt-2: the child starts in vendor\\bin, where a relative TEMP names a folder that
        does not exist (the engine then unpacked itself into the root of the drive)."""
        before = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, before)
        runner = engine.Runner(self.emit)
        with mock.patch.object(paths, 'STATE_DIR', Path('relative-state')):
            result = runner._run_once([sys.executable, '-c', CHILD, '--exit'], 1, 1, [])
        self.assertEqual(result, (0, False, None))
        report = self.report(next(value for kind, value in self.events if kind == 'log'))
        temp = Path(report['temp'])
        self.assertTrue(temp.is_absolute(), report['temp'])
        self.assertEqual(temp.parent, self.root / 'relative-state' / 'tmp')
        self.assertEqual(Path(report['cwd']), paths.BIN)
        self.assertFalse(temp.exists())
        self.assertFalse((paths.BIN / 'relative-state').exists(), 'nothing may appear beside the bundled programs')

    def test_a_temp_folder_that_refuses_new_folders_does_not_hold_the_run_up(self):
        """tempfile.mkdtemp tries name after name (for hours) where the access rights refuse a
        new folder, and _run_once holds the lock that Stop needs."""
        folder = paths.own_dir(paths.tmp_dir())

        def icacls(*arguments):
            return subprocess.run(['icacls', str(folder), *arguments], capture_output=True, timeout=60,
                                  creationflags=procutil.HIDDEN).returncode

        self.addCleanup(icacls, '/reset', '/T', '/C', '/Q')
        if icacls('/deny', '*S-1-1-0:(WD,AD,DC)', '/Q') != 0:
            self.skipTest('icacls could not change the access rights of the test folder')
        runner, result = engine.Runner(self.emit), []
        code = 'import os; print(os.environ["TEMP"], flush=True)'
        thread = threading.Thread(target=lambda: result.append(runner._run_once([sys.executable, '-c', code], 1, 1, [])),
                                  daemon=True)
        thread.start()
        thread.join(20)
        self.assertFalse(thread.is_alive(), '_run_once is still looking for a temp folder name')
        self.assertEqual(result, [(0, False, None)])
        self.assertEqual([value for kind, value in self.events if kind == 'log'], [os.environ['TEMP']],
                         'without a private folder the child keeps the normal TEMP')
        self.assertEqual(self.run_folders(), [])

    def test_cancel_stops_child_and_grandchild_through_the_job(self):
        ready = threading.Event()
        seen, result = {}, []

        def emit(kind, value):
            if kind == 'log' and 'report' not in seen:
                seen['report'] = self.report(value)
                ready.set()

        runner = engine.Runner(emit)
        self.assertIsNotNone(runner.job.handle)
        thread = threading.Thread(target=lambda: result.append(runner._run_once([sys.executable, '-c', CHILD], 1, 1, [])))
        thread.start()
        try:
            self.assertTrue(ready.wait(30), 'the child did not report')
            started = time.monotonic()
            runner.cancel()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 5)
        finally:
            runner.cancel()
            thread.join(30)
        report = seen['report']
        self.assertEqual(self.still_alive([report['pid'], report['grandchild']]), [])
        self.assertNotEqual(result[0][0], 0)
        self.assertEqual(result[0][1:], (False, None))
        self.assertEqual(self.run_folders(), [])
        self.assertIsNone(runner.process)

    def test_download_logs_the_traceback_of_an_aborted_queue(self):
        runner = engine.Runner(self.emit)
        with mock.patch.object(runner, '_run_once', side_effect=RuntimeError('boom')), \
                self.assertLogs('YtDlpDesktop.engine', 'ERROR') as logs:
            runner.download(['https://example.invalid/a'], engine.Options(self.root / 'out'))
        self.assertEqual(logs.records[0].getMessage(), 'download queue aborted')
        self.assertIs(logs.records[0].exc_info[0], RuntimeError)
        self.assertIn(('log', '错误：boom'), self.events, 'the line shown to the user is unchanged')
        self.assertEqual(self.events[-1][0], 'done')


class RunnerInitTests(EngineCase):
    def test_library_argument_and_initial_state(self):
        store = object()
        runner = engine.Runner(self.emit, library=store)
        self.assertIs(runner.library, store)
        self.assertIs(engine.Runner(self.emit, store).library, store)
        plain = engine.Runner(self.emit)
        self.assertIsNone(plain.library)
        plain.library = store    # the 1.5.1 window and app.py still attach it afterwards
        self.assertIs(plain.library, store)
        # (1.8.0) In a guarded test process emit is wrapped by the G2 emit hook (messages.guarded).
        self.assertEqual(getattr(plain.emit, '__wrapped__', plain.emit), self.emit)
        self.assertEqual((plain.archive, plain.archive_folder, plain.archive_errors, plain.last_error),
                         (None, None, [], ''))
        self.assertIsNot(plain.archive_errors, runner.archive_errors)
        self.assertIsInstance(plain.job, procutil.JobObject)
        self.assertIsNot(plain.job, runner.job, 'one job per Runner')
        self.assertFalse(plain.bot_check)
        self.assertFalse(plain.cancelled.is_set())
        self.assertIsNone(plain.process)
        self.assertFalse(paths.STATE_DIR.exists(), 'creating a Runner creates nothing on disk')

    def test_download_uses_the_library_given_to_the_constructor(self):
        store = library.Library(self.root / 'history.sqlite3')
        runner = engine.Runner(self.emit, library=store)
        archive = self.root / 'archive'
        with mock.patch.object(runner, '_run_once', return_value=(0, False, None)):
            runner.download(['https://example.invalid/a'],
                            engine.Options(self.root / 'out', batch_safe=False, archive_root=str(archive)))
        self.assertIs(runner.archive.library, store)
        self.assertEqual(len(store.archives()), 1)
        self.assertEqual(self.events[-1][1]['success'], 1)


class CompatibilityTests(unittest.TestCase):
    def test_every_engine_name_of_1_5_1_is_still_there(self):
        for name in NAMES_OF_1_5_1:
            self.assertTrue(hasattr(engine, name), name)
        namespace = {}
        exec('from engine import ' + ', '.join(NAMES_OF_1_5_1), namespace)
        self.assertEqual(engine.APP_NAME, 'YT-DLP下载器')
        # WP2 appends formats: the five of 1.5.1 keep their strings and their positions.
        self.assertEqual(engine.MODES[:5], ('音频 · 原始最佳', '音频 · WAV 32 位', '音频 · FLAC', '音频 · MP3 320K',
                                            '视频 · 最佳画质 MKV'))
        self.assertEqual(engine.RESOLUTIONS, ('最佳', '2160p / 4K', '1440p', '1080p', '720p', '480p'))
        self.assertEqual((engine.RATE_LIMIT_COOLDOWN, engine.MAX_RATE_LIMIT_COOLDOWN, engine.MAX_RATE_LIMIT_RETRIES),
                         (300, 900, 2))

    def test_names_defined_by_the_1_5_1_modules_are_still_there(self):
        """Compared with the untouched 1.5.1 sources: every module-level name and every method."""
        self.assertTrue(BACKUP.is_dir(), BACKUP)
        for module in (engine, library, collector):
            tree = ast.parse((BACKUP / f'{module.__name__}.py').read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                else:
                    continue
                for name in names:
                    self.assertTrue(hasattr(module, name), f'{module.__name__}.{name}')
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            self.assertTrue(hasattr(getattr(module, node.name), item.name),
                                            f'{module.__name__}.{node.name}.{item.name}')

    def test_old_names_are_the_new_objects(self):
        self.assertEqual((engine.RESOURCE_ROOT, engine.VENDOR, engine.BIN), (paths.RESOURCE_ROOT, paths.VENDOR, paths.BIN))
        self.assertEqual(engine.APP_DIR, paths.APP_DIR)
        self.assertEqual(engine.STATE_DIR, paths.STATE_DIR)
        self.assertIs(engine.HIDDEN, procutil.HIDDEN)
        self.assertIs(engine.process_environment, procutil.process_environment)
        self.assertIs(engine.engine_path, updater.engine_path)
        self.assertIs(engine.engine_version, updater.engine_version)
        self.assertIs(engine.Runner._terminate_process, procutil.terminate_tree)
        self.assertIs(library.alive, procutil.alive)
        positional = engine.Options(Path('D:/out'), engine.MODES[4], '1080p', True, 'http://127.0.0.1:1', False,
                                    {'a': 'b'}, 'D:/archive', {'a': {}}, 'D:/cookies.txt')
        self.assertEqual((positional.output, positional.mode, positional.resolution, positional.playlist,
                          positional.proxy, positional.batch_safe, positional.referers, positional.archive_root,
                          positional.archive_contexts, positional.cookies),
                         (Path('D:/out'), engine.MODES[4], '1080p', True, 'http://127.0.0.1:1', False, {'a': 'b'},
                          'D:/archive', {'a': {}}, 'D:/cookies.txt'))

    def test_only_procutil_starts_taskkill_and_nothing_imports_the_ui(self):
        for path in sorted(_sandbox.SRC.glob('*.py')):
            text = path.read_text(encoding='utf-8')
            if path.name != 'procutil.py':
                self.assertNotIn('taskkill', text, path.name)
        for name in ('paths', 'procutil', 'applog', 'settings', 'updater'):
            tree = ast.parse((_sandbox.SRC / f'{name}.py').read_text(encoding='utf-8'))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {alias.name.split('.')[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or '').split('.')[0])
            for forbidden in ('engine', 'library', 'collector', 'studio', 'customtkinter', 'tkinter', 'app'):
                self.assertNotIn(forbidden, imported, f'{name}.py imports {forbidden}')
        for name in ('paths', 'procutil', 'applog', 'settings'):
            tree = ast.parse((_sandbox.SRC / f'{name}.py').read_text(encoding='utf-8'))
            local = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
                     if (_sandbox.SRC / f'{alias.name}.py').is_file()}
            self.assertLessEqual(local, {'paths'}, f'{name}.py may import paths only')


class CollectorTests(EngineCase):
    def run_collection(self, options, cancel=False):
        runner = collector.CollectionRunner(self.emit)
        self.assertIsInstance(runner.job, procutil.JobObject)
        if cancel:
            runner.cancel()
        runner.run(options)
        self.assertIsNone(runner.process)
        return runner

    def test_collect_done_is_emitted_when_the_history_bookkeeping_raises(self):
        not_a_database = self.root / 'a-folder'
        not_a_database.mkdir()
        options = collector.CollectOptions(['http://127.0.0.1:9/page'], job_id='job-1',
                                           history_path=str(not_a_database))
        with self.assertRaises(sqlite3.Error):
            library.Library(not_a_database)   # what the bookkeeping will meet
        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('a worker was started')), \
                self.assertLogs('YtDlpDesktop.collector', 'ERROR') as logs:
            self.run_collection(options, cancel=True)
        self.assertEqual(self.events, [('collect_done', {'cancelled': True})])
        self.assertTrue(logs.records[0].exc_info)
        # The same when the run itself failed first (options that do not validate).
        self.events.clear()
        broken = collector.CollectOptions(['not a link'], job_id='job-1', history_path=str(not_a_database))
        with self.assertLogs('YtDlpDesktop.collector', 'ERROR'):
            self.run_collection(broken)
        self.assertEqual([kind for kind, _value in self.events], ['collect_error', 'collect_done'])
        self.assertEqual(self.events[-1], ('collect_done', {'cancelled': False}))
        # A job that is missing from a working database.
        self.events.clear()
        store = library.Library(self.root / 'history.sqlite3')
        missing = collector.CollectOptions(['http://127.0.0.1:9/page'], job_id='no-such-job', history_path=str(store.path))
        with self.assertLogs('YtDlpDesktop.collector', 'ERROR'):
            self.run_collection(missing, cancel=True)
        self.assertEqual(self.events, [('collect_done', {'cancelled': True})])

    def test_bookkeeping_still_marks_a_stopped_job_and_the_work_folder_is_removed(self):
        store = library.Library(self.root / 'history.sqlite3')
        job = store.create_job('collect', '测试', {'urls': ['http://127.0.0.1:9/page']})
        options = collector.CollectOptions(['http://127.0.0.1:9/page'], job_id=job, history_path=str(store.path))
        created = []
        real_mkdir = Path.mkdir

        def mkdir(path, *arguments, **keywords):
            created.append(path)
            return real_mkdir(path, *arguments, **keywords)

        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('a worker was started')), \
                mock.patch.object(Path, 'mkdir', mkdir):
            self.run_collection(options, cancel=True)
        self.assertEqual(self.events, [('collect_done', {'cancelled': True})])
        self.assertEqual(store.job(job)['status'], 'interrupted')
        work = {path for path in created if path.parent == paths.collect_dir()}
        self.assertEqual(len(work), 1, created)
        self.assertRegex(work.pop().name, rf'^run-{os.getpid()}-[0-9a-f]{{8}}$')
        self.assertEqual([path.name for path in paths.collect_dir().iterdir()], [paths.OWNED],
                         'the work folder of the run was not removed (the marker of the collect folder stays)')
        self.assertFalse((paths.APP_DIR / 'build').exists(), 'nothing is created beside the program any more')

    def test_worker_gets_the_work_folder_as_temp_and_no_reset_flag(self):
        seen = {}

        class Finished:
            pid, returncode, stdout = 0, 0, None

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def popen(command, **keywords):
            seen.update(command=command, env=keywords['env'], flags=keywords['creationflags'],
                        job_file=Path(command[-1]).is_file())
            return Finished()

        options = collector.CollectOptions(['http://127.0.0.1:9/page'])
        with mock.patch.dict(os.environ, {'PYINSTALLER_RESET_ENVIRONMENT': '1'}), \
                mock.patch.object(subprocess, 'Popen', popen):
            self.run_collection(options)
        self.assertEqual(self.events, [('collect_done', {'cancelled': False})])
        temp = Path(seen['env']['TEMP'])
        self.assertEqual(seen['env']['TMP'], seen['env']['TEMP'])
        self.assertEqual(temp.parent, paths.collect_dir())
        self.assertTrue(temp.is_absolute())
        self.assertRegex(temp.name, rf'^run-{os.getpid()}-[0-9a-f]{{8}}$')
        self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT', seen['env'])
        self.assertEqual(seen['env']['PATH'].split(os.pathsep)[0], str(paths.BIN))
        self.assertEqual(seen['command'][:2], [sys.executable, str(paths.RESOURCE_ROOT / 'src/app.py')])
        self.assertEqual(seen['command'][2], '--collect-worker')
        self.assertTrue(Path(seen['command'][3]).is_relative_to(temp))
        self.assertTrue(seen['job_file'])
        self.assertEqual(seen['flags'], procutil.HIDDEN)
        self.assertFalse(temp.exists())

    def test_cancel_terminates_once_through_the_job(self):
        calls = []
        release = threading.Event()

        class Slow:
            pid, returncode, stdout = 0, None, None

            def poll(self):
                return 1 if release.is_set() else None

            def wait(self, timeout=None):
                return 1

        def terminate(process, job=None):
            calls.append(job)
            if len(calls) == 1:
                threading.Timer(0.3, release.set).start()   # the process needs a moment to end

        runner = collector.CollectionRunner(self.emit)
        with mock.patch.object(subprocess, 'Popen', return_value=Slow()), \
                mock.patch.object(procutil, 'terminate_tree', terminate):
            threading.Timer(0.2, runner.cancelled.set).start()
            runner.run(collector.CollectOptions(['http://127.0.0.1:9/page']))
        self.assertEqual(self.events, [('collect_done', {'cancelled': True})])
        # Once in the loop (not on every 60 ms tick), then the two calls after the loop.
        self.assertEqual(len(calls), 3, calls)
        self.assertTrue(all(job is runner.job for job in calls))

    def test_certificate_cache_prefers_the_state_folder(self):
        import certifi
        source = self.root / '证书' / 'cacert.pem'
        source.parent.mkdir()
        source.write_bytes(b'-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n')
        app = self.root / 'app' / 'inner'
        with mock.patch.object(certifi, 'where', return_value=str(source)), mock.patch.object(paths, 'APP_DIR', app):
            self.assertTrue(str(paths.cache_dir()).isascii(), 'the test needs an ASCII sandbox path')
            first = Path(collector.ca_bundle.__wrapped__())
            self.assertEqual(first.parent, paths.cache_dir() / 'certificates')
            self.assertEqual(first.read_bytes(), source.read_bytes())
            self.assertTrue(first.name.startswith('certifi-') and first.suffix == '.pem')
            self.assertFalse(app.exists())
            # A state folder with a non-ASCII path: the old walk up from the program folder.
            with mock.patch.object(paths, 'STATE_DIR', self.root / '状态'):
                second = Path(collector.ca_bundle.__wrapped__())
            self.assertEqual(second.parent, app / '.yt-dlp-cache' / 'certificates')
            self.assertEqual(second.read_bytes(), source.read_bytes())
            self.assertFalse((self.root / '状态').exists())
            # An ASCII certifi path is used as it is.
            plain = self.root / 'plain.pem'
            plain.write_bytes(b'x')
            with mock.patch.object(certifi, 'where', return_value=str(plain)):
                self.assertEqual(collector.ca_bundle.__wrapped__(), str(plain))


class DiagnosticsTests(EngineCase):
    def test_new_keys_point_into_the_sandbox(self):
        commands = []
        real_run = subprocess.run
        components = ('yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe', 'deno.exe')

        def run(command, *arguments, **keywords):
            # Only the four component checks are replaced (importing scrapling asks Windows for its version).
            if not isinstance(command, list) or Path(command[0]).name not in components:
                return real_run(command, *arguments, **keywords)
            commands.append((command, keywords))
            return subprocess.CompletedProcess(command, 0, 'version 1.2.3\nmore\n', '')

        app = mock.patch.object(paths, 'APP_DIR', self.root / 'app')
        app.start()
        self.addCleanup(app.stop)
        with mock.patch.object(subprocess, 'run', side_effect=run):
            result = engine.diagnostics()
        self.assertEqual(result['app_dir'], str(self.root / 'app'))
        self.assertEqual(result['state_dir'], str(paths.STATE_DIR))
        self.assertEqual(result['catalog'], str(paths.catalog_path()))
        for key in ('app_dir', 'state_dir', 'catalog'):
            self.assertTrue(Path(result[key]).resolve().is_relative_to(_sandbox.BASE), (key, result[key]))
        self.assertEqual(Path(result['state_dir']), self.root / 'state')
        self.assertEqual(Path(result['catalog']), self.root / 'state' / 'library.sqlite3')
        # The 1.5.1 keys are all still there, with their old content.
        self.assertGreaterEqual(set(result), {'app_version', 'resource_root', 'components', 'app_dir', 'state_dir',
                                              'catalog'})
        self.assertEqual(result['app_version'], engine.APP_VERSION)
        self.assertEqual(result['resource_root'], str(paths.RESOURCE_ROOT))
        self.assertEqual(list(result['components']), ['scrapling', 'yt-dlp', 'ffmpeg', 'ffprobe', 'deno'])
        self.assertEqual(result['components']['yt-dlp'], {'returncode': 0, 'version': ['version 1.2.3']})
        self.assertEqual([command[0] for command, _keywords in commands],
                         [str(updater.engine_path()), str(paths.BIN / 'ffmpeg.exe'), str(paths.BIN / 'ffprobe.exe'),
                          str(paths.BIN / 'deno.exe')])
        self.assertTrue(all(keywords['cwd'] == paths.BIN for _command, keywords in commands))
        json.dumps(result)   # the self-test report is written as JSON
        self.assertFalse(paths.STATE_DIR.exists(), 'diagnostics creates nothing')


if __name__ == '__main__':
    unittest.main()
