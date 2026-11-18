"""Self-test of the safety net itself: the sandbox helper and the runner (1.6.0 WP0).

If one of these fails, no other result of the run can be trusted to be isolated.
"""
import _sandbox
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

import engine

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def load_runner():
    """run_tests.py as a module; importing it runs nothing."""
    spec = importlib.util.spec_from_file_location('run_tests', _sandbox.ROOT / 'verification' / 'run_tests.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SandboxTests(unittest.TestCase):
    def inside(self, path, base=None):
        return Path(path).resolve().is_relative_to(Path(base or _sandbox.BASE).resolve())

    def test_environment_points_into_the_sandbox(self):
        for name in ('LOCALAPPDATA', 'YTDLP_DESKTOP_APP_DIR', 'TEMP', 'TMP', 'VERIFY_OUT', 'XDG_CACHE_HOME'):
            self.assertTrue(self.inside(os.environ[name]), name)
            self.assertTrue(Path(os.environ[name]).is_dir(), name)
        self.assertEqual(os.environ['PYTHONIOENCODING'], 'utf-8')
        self.assertTrue(os.environ['YTDLP_DESKTOP_INSTANCE'].startswith('Local\\YtDlpDesktop.Test.'))
        self.assertNotIn('YTDLP_DESKTOP_STATE', os.environ)
        self.assertTrue(self.inside(tempfile.gettempdir()))
        self.assertTrue(sys.dont_write_bytecode)
        self.assertEqual(sys.path[0], str(_sandbox.SRC))
        self.assertEqual(_sandbox.OUT, Path(os.environ['VERIFY_OUT']))

    def test_real_state_folder_is_known_and_outside(self):
        self.assertEqual(_sandbox.REAL_STATE.name, 'YtDlpDesktop')
        self.assertFalse(self.inside(_sandbox.REAL_STATE))
        self.assertFalse(self.inside(_sandbox.BASE, _sandbox.REAL_STATE))

    def test_app_modules_resolve_inside_the_sandbox(self):
        _sandbox.assert_isolated()
        self.assertTrue(self.inside(engine.STATE_DIR))
        self.assertTrue(self.inside(engine.APP_DIR))
        self.assertEqual(Path(engine.STATE_DIR), _sandbox.state_dir())
        import library
        self.assertTrue(self.inside(library.CATALOG))

    def test_assert_isolated_notices_a_leak(self):
        original = engine.APP_DIR
        engine.APP_DIR = _sandbox.ROOT
        try:
            with self.assertRaises(RuntimeError):
                _sandbox.assert_isolated()
        finally:
            engine.APP_DIR = original
        _sandbox.assert_isolated()

    def test_state_patch_redirects_and_restores(self):
        original = engine.STATE_DIR
        target = _sandbox.out('state-patch') / 'state'
        patcher = _sandbox.state_patch(target)
        self.assertEqual(patcher.start(), target)
        try:
            self.assertEqual(engine.STATE_DIR, target)
            paths = sys.modules.get('paths')
            if paths is not None and hasattr(paths, 'STATE_DIR'):
                self.assertEqual(paths.STATE_DIR, target)
            engine.store_failed_job(['https://example.invalid/a'], engine.Options(_sandbox.out('state-patch') / 'out'))
            self.assertTrue((target / 'failed-downloads.json').is_file())
        finally:
            patcher.stop()
        self.assertEqual(engine.STATE_DIR, original)
        with _sandbox.state_patch(target) as path:
            self.assertEqual((path, engine.STATE_DIR), (target, target))
        self.assertEqual(engine.STATE_DIR, original)
        self.assertFalse((_sandbox.state_dir() / 'failed-downloads.json').exists())

    def test_state_helpers_refuse_the_real_state_folder(self):
        for target in (_sandbox.REAL_STATE, _sandbox.REAL_STATE / 'sub', _sandbox.REAL_STATE.parent):
            with self.assertRaises(RuntimeError):
                _sandbox.state_patch(target)
            with self.assertRaises(RuntimeError):
                _sandbox.set_state_dir(target)

    def test_set_state_dir_is_permanent(self):
        original = engine.STATE_DIR
        try:
            _sandbox.set_state_dir(_sandbox.out('set-state') / 'state')
            self.assertEqual(engine.STATE_DIR, _sandbox.out('set-state') / 'state')
        finally:
            _sandbox.set_state_dir(original)

    def test_output_helpers(self):
        path = _sandbox.out('nested', 'folder', 'file.txt')
        self.assertTrue(path.parent.is_dir() and not path.exists())
        self.assertTrue(self.inside(path, _sandbox.OUT))
        report = _sandbox.write_report('self-test-report.json', {'status': 'passed', '名称': '测试'})
        self.assertEqual(report, _sandbox.OUT / 'self-test-report.json')
        self.assertEqual(json.loads(report.read_text(encoding='utf-8')), {'status': 'passed', '名称': '测试'})

    def test_write_settings_turns_background_features_off(self):
        path = _sandbox.write_settings(mode=engine.MODES[2])
        try:
            self.assertEqual(path, _sandbox.state_dir() / 'settings.json')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),
                             {'check_engine_updates': False, 'clipboard_hint': False, 'mode': engine.MODES[2]})
        finally:
            path.unlink()

    def test_app_args_and_instance_name(self):
        arguments = _sandbox.app_args('http://127.0.0.1/x', 'out')
        self.assertEqual((arguments.smoke_url, arguments.smoke_output), ('http://127.0.0.1/x', 'out'))
        self.assertEqual((arguments.self_test, arguments.collect_worker, arguments.smoke_archive), (None, None, False))
        self.assertEqual(_sandbox.app_args().smoke_url, None)
        expected = 'Local\\YtDlpDesktop.Test.' + hashlib.sha1(b'X:\\some base').hexdigest()[:12]
        self.assertEqual(_sandbox.instance_name('X:\\some base'), expected)

    def test_fixture_site_serves_the_fixture(self):
        base, server = _sandbox.fixture_site()
        try:
            self.assertTrue(base.startswith('http://127.0.0.1:'))
            with urllib.request.urlopen(base + '/fixture.mp4', timeout=10) as response:
                data = response.read()
            self.assertEqual(data, (_sandbox.ROOT / 'verification' / 'fixture' / 'fixture.mp4').read_bytes())
        finally:
            server.shutdown()
            server.server_close()

    def test_normalise_replaces_the_longest_text_first(self):
        value = {'C:\\base\\out': ['C:\\base\\out\\a.wav', 'C:\\base\\state', ('C:\\base', 7, None)]}
        table = [('C:\\base', '<BASE>'), ('C:\\base\\out', '<OUT>'), ('', '<EMPTY>')]
        self.assertEqual(_sandbox.normalise(value, table),
                         {'<OUT>': ['<OUT>\\a.wav', '<BASE>\\state', ['<BASE>', 7, None]]})

    def test_check_golden_compares_and_writes_only_on_request_outside_the_runner(self):
        class Probe(unittest.TestCase):
            def runTest(self):
                pass

        folder = _sandbox.out('golden-self-test', 'x').parent
        data = {'one': ['a', 'b'], 'two': {'k': 1}}
        with mock.patch.object(_sandbox, 'GOLDEN', folder), mock.patch.dict(os.environ), \
                contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop('GOLDEN_UPDATE', None)
            with self.assertRaisesRegex(AssertionError, 'golden file is missing'):
                _sandbox.check_golden(Probe(), 'sample.json', data)
            self.assertFalse((folder / 'sample.json').exists())
            os.environ['GOLDEN_UPDATE'] = '1'
            with mock.patch.object(_sandbox, 'RUNNER_MODE', True):
                with self.assertRaisesRegex(AssertionError, 'ignored under run_tests.py'):
                    _sandbox.check_golden(Probe(), 'sample.json', data)
            self.assertFalse((folder / 'sample.json').exists())
            with mock.patch.object(_sandbox, 'RUNNER_MODE', False):
                _sandbox.check_golden(Probe(), 'sample.json', data)
            recorded = (folder / 'sample.json').read_bytes()
            self.assertEqual(json.loads(recorded), data)
            self.assertNotIn(b'\r', recorded)
            os.environ.pop('GOLDEN_UPDATE')
            _sandbox.check_golden(Probe(), 'sample.json', {'two': {'k': 1}, 'one': ('a', 'b')})
            with self.assertRaises(AssertionError) as caught:
                _sandbox.check_golden(Probe(), 'sample.json', {'one': ['a', 'c'], 'two': {'k': 1}})
            self.assertIn('1 entries differ: one', str(caught.exception))
            self.assertIn('-    "b"', str(caught.exception))
            self.assertIn('+    "c"', str(caught.exception))
            self.assertEqual((folder / 'sample.json').read_bytes(), recorded)

    def test_the_recorded_goldens_exist(self):
        for name in ('argv-1.5.1.json', 'events-1.5.1.json', 'runonce-1.5.1.json', 'ui-1.5.1.json'):
            self.assertTrue(json.loads((_sandbox.GOLDEN / name).read_text(encoding='utf-8')), name)

    def test_release_sources_and_fixture_texts(self):
        """(1.8.0) The frozen released sources and the G2 exemption helper (MESSAGES.md D11, 7.3)."""
        self.assertEqual(_sandbox.release_src('1.5.1'),
                         _sandbox.ROOT / 'verification' / 'release-sources' / '1.5.1' / 'src')
        for version in ('1.5.1', '1.6.0', '1.7.0'):
            self.assertTrue((_sandbox.release_src(version) / 'engine.py').is_file(), version)
        self.assertIn("APP_VERSION = '1.5.1'", (_sandbox.release_src('1.5.1') / 'engine.py').read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            _sandbox.release_src('1.4.0')
        with _sandbox.fixture_texts('test_sandbox: the helper itself'):
            pass
        guarded = bool(os.environ.get(_sandbox.MSG_GUARD))
        self.assertEqual('messages' in sys.modules, guarded or 'messages' in sys.modules)
        if guarded:
            import messages
            self.assertTrue(messages.GUARD)

    def test_a_child_that_never_imports_the_sandbox_is_isolated_too(self):
        code = 'import os, tempfile; print(os.environ["LOCALAPPDATA"]); print(tempfile.gettempdir()); ' \
               'print(os.environ.get("YTDLP_DESKTOP_APP_DIR"))'
        output = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, encoding='utf-8',
                                timeout=60, creationflags=HIDDEN).stdout.splitlines()
        self.assertEqual(len(output), 3)
        for line in output:
            self.assertTrue(self.inside(line), line)


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def run_file(self, name, text, tier='default', exe=None, net=False):
        return self.runner.run_file(self.write(name, text), tier, self.root / 'base', _sandbox.REAL_STATE, exe, net)

    def test_tier_comment_is_read_from_the_first_five_lines(self):
        self.assertEqual(self.runner.tier_of(self.write('a.py', '# tier: all\nimport os\n')), 'all')
        self.assertEqual(self.runner.tier_of(self.write('b.py', '"""doc"""\n\n#  tier:  exe\n')), 'exe')
        self.assertEqual(self.runner.tier_of(self.write('c.py', '\n\n\n\n\n# tier: net\n')), 'default')
        self.assertEqual(self.runner.tier_of(self.write('d.py', 'import os  # tier: all\n')), 'default')
        self.assertEqual(self.runner.tier_of(self.write('e.py', '# tier: everything\n')), 'default')

    def test_discovery_finds_this_file_in_the_default_tier(self):
        tests = {path.name: tier for path, tier in self.runner.discover()}
        self.assertEqual(tests['test_sandbox.py'], 'default')
        self.assertEqual(tests['test_golden_ui.py'], 'default')
        self.assertTrue(all(name.startswith('test_') and name.endswith('.py') for name in tests))

    def test_child_environment_is_a_complete_sandbox(self):
        sandbox = self.root / 'one'
        with mock.patch.dict(os.environ, {'YTDLP_DESKTOP_STATE': 'X:\\real', 'YTDLP_VERIFY_BASE': 'X:\\b',
                                          'YTDLP_VERIFY_KEEP': '1', 'GOLDEN_UPDATE': '1',
                                          'VERIFY_NET': '1', 'VERIFY_EXE': 'X:\\old.exe'}):
            plain = self.runner.child_environment(sandbox, Path('X:\\state'), None, False)
            full = self.runner.child_environment(sandbox, Path('X:\\state'), Path('X:\\app.exe'), True)
        for name in ('YTDLP_DESKTOP_STATE', 'YTDLP_VERIFY_BASE', 'YTDLP_VERIFY_KEEP', 'GOLDEN_UPDATE',
                     'VERIFY_NET', 'VERIFY_EXE'):
            self.assertNotIn(name, plain)
        expected = {'YTDLP_VERIFY_SANDBOX': str(sandbox), 'LOCALAPPDATA': str(sandbox / 'localappdata'),
                    'YTDLP_DESKTOP_APP_DIR': str(sandbox / 'app'), 'TEMP': str(sandbox / 'tmp'),
                    'TMP': str(sandbox / 'tmp'), 'VERIFY_OUT': str(sandbox / 'out'),
                    'XDG_CACHE_HOME': str(sandbox / 'cache'), 'PYTHONIOENCODING': 'utf-8',
                    'YTDLP_DESKTOP_INSTANCE': _sandbox.instance_name(sandbox),
                    'YTDLP_VERIFY_REAL_STATE': 'X:\\state', 'PYTHONDONTWRITEBYTECODE': '1'}
        self.assertEqual({name: plain[name] for name in expected}, expected)
        self.assertEqual((full['VERIFY_EXE'], full['VERIFY_NET']), ('X:\\app.exe', '1'))

    def test_child_environment_turns_the_message_guard_on_except_for_guard_off(self):
        sandbox = self.root / 'two'
        with mock.patch.dict(os.environ, {self.runner.MSG_GUARD: 'stale'}):
            on = self.runner.child_environment(sandbox, Path('X:\\state'), None, False)
            off = self.runner.child_environment(sandbox, Path('X:\\state'), None, False, guard=False)
        self.assertEqual(on[self.runner.MSG_GUARD], '1')
        self.assertNotIn(self.runner.MSG_GUARD, off)
        self.assertEqual(set(self.runner.GUARD_OFF), {'test_golden_ui.py', 'test_golden_runonce.py'})
        self.assertTrue(all(self.runner.GUARD_OFF.values()))
        seen = self.run_file('test_golden_ui.py', 'import os\nprint(os.environ.get("YTDLP_DESKTOP_MSG_GUARD"))\n')
        self.assertEqual((seen['status'], seen['msg_guard']['guarded']), ('passed', False))
        self.assertEqual((self.root / 'base' / '_logs' / 'test_golden_ui.stdout.txt').read_text(encoding='utf-8').strip(),
                         'None')
        seen = self.run_file('test_other.py', 'import os\nprint(os.environ.get("YTDLP_DESKTOP_MSG_GUARD"))\n')
        self.assertEqual((seen['status'], seen['msg_guard']['guarded']), ('passed', True))

    def test_message_guard_lines_fail_the_file(self):
        code = ('import json, os\n'
                'path = os.path.join(os.environ["VERIFY_OUT"], "msg-guard-%d.jsonl" % os.getpid())\n'
                'lines = [{"exempt": "fixture"}] + [{"rule": "R-text", "where": "show", "producer": "engine.py:%d" % n,\n'
                '          "key": "core.x", "text": "t", "test": "T"} for n in range(7)]\n'
                'open(path, "w", encoding="utf-8").write("\\n".join(json.dumps(item) for item in lines) + "\\n")\n')
        result = self.run_file('test_violating.py', code)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['msg_guard'], {'guarded': True, 'violations': 7, 'exemptions': 1})
        self.assertIn('7 message-guard violation(s):', result['tail'])
        self.assertEqual(len([line for line in result['tail'] if line.startswith('msg-guard R-text')]), 5)
        clean = self.run_file('test_exempt_only.py', code.replace('range(7)', 'range(0)'))
        self.assertEqual((clean['status'], clean['msg_guard']['exemptions']), ('passed', 1))

    def test_source_hash_definition(self):
        digest = hashlib.sha256()
        for path in sorted((_sandbox.ROOT / 'src').glob('*.py'), key=lambda item: item.name):
            digest.update(path.read_bytes())
        digest.update((_sandbox.ROOT / 'app.spec').read_bytes())
        self.assertEqual(self.runner.source_sha256(), digest.hexdigest())
        self.assertRegex(self.runner.app_version(), r'^\d+\.\d+\.\d+$')

    def test_tree_snapshot_ignores_pycache_only(self):
        tree = self.runner.snapshot_tree()
        self.assertIn('run_tests.py', tree)
        self.assertIn('golden/argv-1.5.1.json', tree)
        self.assertFalse([name for name in tree if '__pycache__' in name])
        # Files carry (size, st_mtime_ns), so a rewritten file is noticed; folders carry None.
        status = (_sandbox.ROOT / 'verification' / 'run_tests.py').stat()
        self.assertEqual(tree['run_tests.py'], (status.st_size, status.st_mtime_ns))
        self.assertIsNone(tree['golden'])
        self.assertIn('release-sources/1.7.0/src/engine.py', tree)

    def test_isolation_check_reports_new_paths_and_state_changes(self):
        real = self.root / 'real-state'
        real.mkdir()
        (real / 'settings.json').write_text('{}', encoding='utf-8')
        (real / 'download.log').write_text('a', encoding='utf-8')
        tree, state = self.runner.snapshot_tree(), self.runner.snapshot_state(real)
        self.assertEqual(sorted(state), ['download.log', 'settings.json'])
        self.assertEqual(self.runner.isolation_problems(tree, state, set(self.runner.LEAK_SIGNATURES), real), [])
        self.assertEqual(self.runner.snapshot_state(self.root / 'missing'), {})
        (real / 'download.log').write_text('ab', encoding='utf-8')
        (real / 'settings.json').unlink()
        (real / 'new.json').write_text('{}', encoding='utf-8')
        before = {name: value for name, value in tree.items() if name != 'run_tests.py'}
        size, modified = tree['_sandbox.py']
        before['_sandbox.py'] = (size, modified - 1)             # same size, rewritten later
        before['sandbox_app.py'] = (tree['sandbox_app.py'][0] + 1, tree['sandbox_app.py'][1])
        before['old-report.json'] = (2, 1)                       # was there, is gone now
        before['old-folder'] = None
        problems = self.runner.isolation_problems(before, state, set(self.runner.LEAK_SIGNATURES), real)
        text = '\n'.join(problems)
        self.assertIn('new path in verification/: run_tests.py', text)
        self.assertIn('file changed in verification/: _sandbox.py', text)
        self.assertIn('file changed in verification/: sandbox_app.py', text)
        self.assertIn('path removed from verification/: old-report.json', text)
        self.assertIn('path removed from verification/: old-folder', text)
        self.assertEqual(len([line for line in problems if 'verification/' in line]), 5, problems)
        self.assertIn('real state file changed', text)
        self.assertIn('real state file removed', text)
        self.assertIn('real state file created', text)

    def test_a_file_without_the_sandbox_import_runs_isolated(self):
        result = self.run_file('test_plain.py',
                               'import os, tempfile\n'
                               'print(os.environ["LOCALAPPDATA"])\nprint(tempfile.gettempdir())\n'
                               'print(os.environ["YTDLP_DESKTOP_APP_DIR"])\nprint(os.environ["VERIFY_OUT"])\n'
                               'open(os.path.join(os.environ["VERIFY_OUT"], "seen.json"), "w").write("{\\"ok\\": true}")\n')
        self.assertEqual((result['status'], result['tail'], result['leftover_processes']), ('passed', [], []))
        sandbox = self.root / 'base' / 'test_plain'
        lines = (self.root / 'base' / '_logs' / 'test_plain.stdout.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertTrue(Path(line).resolve().is_relative_to(sandbox.resolve()), line)
        reports = self.runner.collect_reports(self.root / 'base', [result])
        self.assertEqual(reports, {'test_plain/seen.json': {'ok': True}})

    def test_status_of_skipped_and_failed_files(self):
        skipped = self.run_file('test_skip.py', 'print("SKIPPED: nothing to do here")\n')
        self.assertEqual((skipped['status'], skipped['tail']), ('skipped', ['SKIPPED: nothing to do here']))
        failed = self.run_file('test_fail.py', 'import sys\nprint("line one")\nsys.stderr.write("boom\\n")\nsys.exit(3)\n')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['tail'], ['line one', 'boom', 'exit code 3'])
        not_skipped = self.run_file('test_skip_fail.py', 'import sys\nprint("SKIPPED: but broken")\nsys.exit(1)\n')
        self.assertEqual(not_skipped['status'], 'failed')

    def test_timeout_kills_the_whole_process_tree(self):
        code = ('import subprocess, sys, time\n'
                'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=0x08000000)\n'
                'print(child.pid, flush=True)\n'
                'time.sleep(120)\n')
        with mock.patch.dict(self.runner.TIMEOUT, {'default': 6}):
            started = time.monotonic()
            result = self.run_file('test_hang.py', code)
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('TIMEOUT: killed after 6 s', result['tail'])
        grandchild = int(result['tail'][0])
        deadline = time.monotonic() + 10
        while grandchild in self.runner.process_table() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertNotIn(grandchild, self.runner.process_table())

    def test_processes_left_behind_by_a_test_are_killed_and_reported(self):
        code = ('import subprocess, sys\n'
                'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=0x08000000)\n'
                'print("SKIPPED: " + str(child.pid), flush=True)\n')
        result = self.run_file('test_leak.py', code)
        self.assertEqual(result['status'], 'skipped')
        self.assertTrue(result['leftover_processes'], result)
        leaked = int(result['tail'][0].split()[-1])
        deadline = time.monotonic() + 10
        while leaked in self.runner.process_table() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertNotIn(leaked, self.runner.process_table())


class RunnerMainTests(unittest.TestCase):
    """run_tests.main() end to end, against a throwaway 'verification' folder and a fake real state folder."""

    def setUp(self):
        self.runner = load_runner()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.tests = self.root / 'verification'
        self.real = self.root / 'real-state'
        self.tests.mkdir()
        self.real.mkdir()
        (self.real / 'download.log').write_text('before\n', encoding='utf-8')
        self.report = self.root / 'build' / 'report.json'
        for name, value in (('VERIFICATION', self.tests), ('REPORT', self.report), ('LEAK_SIGNATURES', ()),
                            ('real_state_dir', lambda: self.real)):
            patcher = mock.patch.object(self.runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add(self, name, text):
        (self.tests / name).write_text(text, encoding='utf-8')

    def main(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.runner.main(list(arguments))
        text = output.getvalue()
        report = json.loads(self.report.read_text(encoding='utf-8')) if self.report.is_file() else None
        return code, text, report

    def kept_sandbox(self, text):
        line = next(line for line in text.splitlines() if line.startswith('sandbox kept'))
        return Path(line.split(': ', 1)[1])

    def test_green_run_writes_the_report_and_removes_the_sandbox(self):
        self.add('test_one.py', 'import os, json\n'
                                'json.dump({"status": "passed"}, open(os.path.join(os.environ["VERIFY_OUT"], "one.json"), "w"))\n')
        self.add('test_two.py', '# tier: all\nprint("slow tier")\n')
        self.add('test_three.py', '# tier: exe\nprint("SKIPPED: no --exe")\n')
        self.add('helper.py', 'raise SystemExit(9)\n')
        code, text, report = self.main()
        self.assertEqual(code, 0, text)
        self.assertIn('SUMMARY: 1 passed, 0 failed, 0 skipped; isolation ok; tiers default;', text)
        self.assertIn('not selected: test_three.py (exe), test_two.py (all)', text)
        self.assertEqual([(entry['file'], entry['tier'], entry['status']) for entry in report['results']],
                         [('test_one.py', 'default', 'passed')])
        self.assertEqual(report['tiers'], ['default'])
        self.assertEqual(report['reports'], {'test_one/one.json': {'status': 'passed'}})
        self.assertEqual((report['exe'], report['filter']), (None, None))
        self.assertEqual(report['source_sha256'], self.runner.source_sha256())
        self.assertEqual(report['msg_guard'], {'files_checked': 1, 'violations': 0, 'exemptions': 0, 'not_guarded': []})
        self.assertIn('MSG_GUARD: 1 files checked, 0 violations, 0 exemptions; not guarded: none', text)
        self.assertEqual(report['app_version'], self.runner.app_version())
        sandbox = Path(next(line for line in text.splitlines() if line.startswith('sandbox ')).split(None, 1)[1])
        self.assertFalse(sandbox.exists(), 'the sandbox of a green run must be removed')
        code, text, report = self.main('--all', '--keep')
        self.assertEqual(code, 0, text)
        self.assertEqual([(entry['file'], entry['status']) for entry in report['results']],
                         [('test_one.py', 'passed'), ('test_two.py', 'passed')])
        self.assertEqual(report['tiers'], ['default', 'all'])
        self.assertTrue(self.kept_sandbox(text).is_dir())
        code, text, report = self.main('-k', 'two', '--all')
        self.assertEqual((code, [entry['file'] for entry in report['results']], report['filter']),
                         (0, ['test_two.py'], 'two'))
        self.assertEqual(self.main('-k', 'no-such-test')[0], 2)
        code, text, _report = self.main('--list')
        self.assertEqual((code, text.split()), (0, ['default', 'test_one.py', 'exe', 'test_three.py', 'all', 'test_two.py']))

    def test_exe_tier_gets_the_exe_and_its_hash(self):
        exe = self.root / 'app.exe'
        exe.write_bytes(b'not really an exe')
        self.add('test_packaged.py', '# tier: exe\nimport os\nprint(os.environ["VERIFY_EXE"])\n'
                                     'assert os.path.isfile(os.environ["VERIFY_EXE"]) and "VERIFY_NET" not in os.environ\n')
        code, text, report = self.main('--exe', str(exe))
        self.assertEqual(code, 0, text)
        self.assertEqual(report['tiers'], ['default', 'exe'])
        self.assertEqual(report['exe'], {'path': str(exe.resolve()), 'sha256': hashlib.sha256(b'not really an exe').hexdigest()})
        self.assertEqual(self.main('--exe', str(self.root / 'missing.exe'))[0], 2)

    def test_failing_file_fails_the_run_and_keeps_the_sandbox(self):
        self.add('test_bad.py', 'print("about to fail")\nraise SystemExit("this test is broken")\n')
        self.add('test_good.py', 'print("fine")\n')
        code, text, report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('FAILED: test_bad.py', text)
        self.assertIn('SUMMARY: 1 passed, 1 failed, 0 skipped; isolation ok;', text)
        bad = report['results'][0]
        self.assertEqual((bad['file'], bad['status']), ('test_bad.py', 'failed'))
        self.assertEqual(bad['tail'], ['about to fail', 'this test is broken', 'exit code 1'])
        sandbox = self.kept_sandbox(text)
        self.assertEqual((sandbox / '_logs' / 'test_bad.stdout.txt').read_text(encoding='utf-8').strip(), 'about to fail')

    def test_new_file_in_the_test_folder_is_an_isolation_failure(self):
        self.add('test_litter.py', 'import pathlib\n'
                                   '(pathlib.Path(__file__).parent / "left-behind.json").write_text("{}")\n')
        code, text, report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('isolation ... failed', text)
        self.assertIn('new path in verification/: left-behind.json', text)
        self.assertIn('SUMMARY: 1 passed, 0 failed, 0 skipped; isolation FAILED;', text)
        self.assertEqual([(entry['file'], entry['status']) for entry in report['results']],
                         [('test_litter.py', 'passed'), ('isolation', 'failed')])
        self.assertTrue((self.tests / 'left-behind.json').is_file(), 'the runner must not delete the evidence')

    def test_rewritten_or_deleted_file_in_the_test_folder_is_an_isolation_failure(self):
        # verification/ still holds the report files the 1.5.1 tests used to write, and the goldens.
        (self.tests / 'ui-report.json').write_text('{"status": "old"}', encoding='utf-8')
        (self.tests / 'old-note.txt').write_text('history', encoding='utf-8')
        (self.tests / 'golden').mkdir()
        (self.tests / 'golden' / 'sample.json').write_text('{"a": 1}', encoding='utf-8')
        (self.tests / 'golden' / 'kept.json').write_text('{}', encoding='utf-8')
        (self.tests / 'old-folder').mkdir()
        (self.tests / 'old-folder' / 'report.json').write_text('{}', encoding='utf-8')
        old = (self.tests / 'golden' / 'sample.json').stat().st_mtime_ns
        self.add('test_overwrite.py',
                 'import os, pathlib, shutil\n'
                 'here = pathlib.Path(__file__).parent\n'
                 '(here / "ui-report.json").write_text(\'{"status": "rewritten by a test"}\')\n'
                 '(here / "old-note.txt").unlink()\n'
                 'shutil.rmtree(here / "old-folder")\n'
                 '# same content and size, only written again (one second later on the clock)\n'
                 'sample = here / "golden" / "sample.json"\n'
                 'sample.write_text(\'{"a": 1}\')\n'
                 f'os.utime(sample, ns=({old + 10 ** 9}, {old + 10 ** 9}))\n')
        code, text, report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('isolation ... failed', text)
        self.assertIn('file changed in verification/: ui-report.json', text)
        self.assertIn('path removed from verification/: old-note.txt', text)
        self.assertIn('path removed from verification/: old-folder', text)
        self.assertIn('path removed from verification/: old-folder/report.json', text)
        self.assertIn('file changed in verification/: golden/sample.json', text)
        self.assertNotIn('golden/kept.json', text)
        self.assertNotIn('new path in verification/', text)
        self.assertIn('SUMMARY: 1 passed, 0 failed, 0 skipped; isolation FAILED;', text)
        self.assertEqual([(entry['file'], entry['status']) for entry in report['results']],
                         [('test_overwrite.py', 'passed'), ('isolation', 'failed')])
        self.assertEqual(len(report['results'][-1]['tail']), 5, report['results'][-1]['tail'])
        # The runner reports; it does not undo anything.
        self.assertEqual((self.tests / 'ui-report.json').read_text(encoding='utf-8'),
                         '{"status": "rewritten by a test"}')
        self.assertFalse((self.tests / 'old-note.txt').exists())

    def test_unchanged_test_folder_with_old_files_is_not_an_isolation_failure(self):
        (self.tests / 'ui-report.json').write_text('{"status": "old"}', encoding='utf-8')
        (self.tests / 'golden').mkdir()
        (self.tests / 'golden' / 'sample.json').write_text('{"a": 1}', encoding='utf-8')
        self.add('test_reader.py', 'import pathlib\n'
                                   'here = pathlib.Path(__file__).parent\n'
                                   'print((here / "ui-report.json").read_text(), (here / "golden" / "sample.json").read_text())\n')
        code, text, _report = self.main()
        self.assertEqual(code, 0, text)
        self.assertIn('isolation ... ok', text)

    def test_pycache_under_release_sources_is_an_isolation_failure(self):
        (self.tests / 'release-sources' / '1.7.0' / 'src').mkdir(parents=True)
        self.add('test_compile.py', 'import pathlib\n'
                                    'folder = pathlib.Path(__file__).parent / "release-sources" / "1.7.0" / "src" / "__pycache__"\n'
                                    'folder.mkdir()\n')
        code, text, _report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('new path in verification/: release-sources/1.7.0/src/__pycache__', text)

    def test_change_of_the_real_state_folder_is_an_isolation_failure(self):
        self.add('test_touch.py', 'import os, pathlib\n'
                                  'real = pathlib.Path(os.environ["YTDLP_VERIFY_REAL_STATE"])\n'
                                  'with open(real / "download.log", "a") as log:\n    log.write("leak\\n")\n'
                                  '(real / "settings.json").write_text("{}")\n')
        code, text, report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('real state file changed: ' + str(self.real / 'download.log'), text)
        self.assertIn('real state file created: ' + str(self.real / 'settings.json'), text)
        self.assertEqual(report['results'][-1]['file'], 'isolation')

    def test_source_change_during_the_run_fails_it(self):
        self.add('test_fine.py', 'print("fine")\n')
        hashes = iter(['a' * 64, 'b' * 64, 'b' * 64])
        with mock.patch.object(self.runner, 'source_sha256', lambda: next(hashes)):
            code, text, report = self.main()
        self.assertEqual(code, 1, text)
        self.assertIn('sources ... failed', text)
        self.assertEqual(report['source_sha256'], 'a' * 64)
        self.assertEqual(report['results'][-1]['file'], 'sources')


if __name__ == '__main__':
    unittest.main()
