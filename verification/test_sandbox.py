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
import paths

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
        self.assertTrue(self.inside(paths.STATE_DIR))
        self.assertTrue(self.inside(paths.APP_DIR))
        self.assertEqual(Path(paths.STATE_DIR), _sandbox.state_dir())
        self.assertTrue(self.inside(paths.data_dir()))
        self.assertTrue(self.inside(paths.catalog_path()))

    def test_assert_isolated_notices_a_leak(self):
        original = paths.APP_DIR
        paths.APP_DIR = _sandbox.ROOT
        try:
            with self.assertRaises(RuntimeError):
                _sandbox.assert_isolated()
        finally:
            paths.APP_DIR = original
        _sandbox.assert_isolated()

    def test_state_patch_redirects_and_restores(self):
        original = paths.STATE_DIR
        target = _sandbox.out('state-patch') / 'state'
        patcher = _sandbox.state_patch(target)
        self.assertEqual(patcher.start(), target)
        try:
            self.assertEqual(paths.STATE_DIR, target)
            _sandbox.save_unfinished(['https://example.invalid/a'], engine.Options(_sandbox.out('state-patch') / 'out'))
            self.assertTrue((target / 'failed-downloads.json').is_file())
        finally:
            patcher.stop()
        self.assertEqual(paths.STATE_DIR, original)
        with _sandbox.state_patch(target) as path:
            self.assertEqual((path, paths.STATE_DIR), (target, target))
        self.assertEqual(paths.STATE_DIR, original)
        self.assertFalse((_sandbox.state_dir() / 'failed-downloads.json').exists())

    def test_state_helpers_refuse_the_real_state_folder(self):
        for target in (_sandbox.REAL_STATE, _sandbox.REAL_STATE / 'sub', _sandbox.REAL_STATE.parent):
            with self.assertRaises(RuntimeError):
                _sandbox.state_patch(target)
            with self.assertRaises(RuntimeError):
                _sandbox.set_state_dir(target)

    def test_set_state_dir_is_permanent(self):
        original = paths.STATE_DIR
        try:
            _sandbox.set_state_dir(_sandbox.out('set-state') / 'state')
            self.assertEqual(paths.STATE_DIR, _sandbox.out('set-state') / 'state')
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

    def test_zh_t_and_en_t_do_not_follow_the_running_language(self):
        import i18n
        for lang in ('zh', 'en'):
            with _sandbox.language(lang):
                self.assertEqual(_sandbox.zh_t('download.hint.queued', count=3),
                                 i18n._t_in('zh', 'download.hint.queued', {'count': 3}))
                self.assertEqual(_sandbox.en_t('download.hint.queued', count=3),
                                 i18n._t_in('en', 'download.hint.queued', {'count': 3}))
        self.assertNotEqual(_sandbox.zh_t('download.empty'), _sandbox.en_t('download.empty'))

    def test_keyed_writes_catalog_texts_as_keys(self):
        import i18n
        zh_t, en_t, keyed = _sandbox.zh_t, _sandbox.en_t, _sandbox.keyed
        self.assertEqual(keyed(zh_t('download.empty')), {'zh': 'download.empty'})
        self.assertEqual(keyed(en_t('download.empty')), {'en': 'download.empty'})
        # a template; of two that both match, the one with more own words wins
        fake = {lang: dict(texts) for lang, texts in i18n.CATALOG.items()}
        fake['zh'].update({'test.final': '成功 {done}  /  未完成 {unfinished}  /  共 {total}',
                           'test.final_short': '成功 {done}  /  共 {total}'})
        try:
            with mock.patch.object(i18n, 'CATALOG', fake):
                _sandbox._KEYED_INDEX = None
                self.assertEqual(keyed('成功 1  /  未完成 0  /  共 1'),
                                 {'zh': 'test.final', 'p': {'done': '1', 'unfinished': '0', 'total': '1'}})
                self.assertEqual(keyed('成功 1  /  共 1'), {'zh': 'test.final_short', 'p': {'done': '1', 'total': '1'}})
        finally:
            _sandbox._KEYED_INDEX = None
        # a parameter that is itself a catalog text is keyed too
        wait = zh_t('download.metrics.wait', reason=zh_t('core.wait.pace_or_retry'), time='05:00')
        self.assertEqual(keyed(wait), {'zh': 'download.metrics.wait',
                                       'p': {'reason': {'zh': 'core.wait.pace_or_retry'}, 'time': '05:00'}})
        # dictionary keys, other values, user data stay; lists are walked
        title = zh_t('download.empty')
        self.assertEqual(keyed({title: [title, 3, None, '我的歌单.opus']}),
                         {title: [{'zh': 'download.empty'}, 3, None, '我的歌单.opus']})
        # (S3 review fix) the same text in both languages, and the text of several keys, keep every candidate
        plain = {}
        for lang in i18n.LANGUAGES:
            for key, text in i18n.CATALOG[lang].items():
                if '{' not in text:
                    plain.setdefault(text, set()).add((lang, key))
        same = next(text for text, owners in plain.items() if len(owners) == 2 and len({k for _l, k in owners}) == 1)
        shared = next(text for text, owners in plain.items() if len({k for _l, k in owners}) > 1)
        [(_lang, same_key)] = {(None, key) for _lang, key in plain[same]}
        self.assertEqual(keyed(same), {'zh': [same_key], 'en': [same_key]})
        self.assertEqual(keyed(shared), {lang: sorted(key for other, key in plain[shared] if other == lang)
                                         for lang in sorted({lang for lang, _key in plain[shared]})})
        # (S3 review fix) a text that is no catalog text as a whole is split at the joiners of SPLITS
        row = '02  ·  ' + zh_t('download.empty')
        self.assertEqual(keyed(row), {'cat': ['02', '  ·  ', {'zh': 'download.empty'}]})
        self.assertEqual(keyed('02  ·  我的歌单'), '02  ·  我的歌单', 'no part is a catalog text: as it is')

    def test_rendered_compares_with_the_catalog_of_the_run(self):
        """(S3 review fix) A golden is compared by rendering it, never by looking texts up again: changing one key's
        text, or making it equal to another key's text, changes no other key's record."""
        import i18n
        a, b = 'download.empty', 'download.hint.already_queued'
        record = {'x': [{'zh': a}, {'zh': [a, b]}, {'cat': ['01', '  ·  ', {'zh': b}]}, '用户']}
        shown = [_sandbox.zh_t(a), _sandbox.zh_t(a), '01  ·  ' + _sandbox.zh_t(b), '用户']
        self.assertEqual(_sandbox.rendered(record, {'x': shown}), {'x': shown})
        reworded = {lang: dict(texts) for lang, texts in i18n.CATALOG.items()}
        reworded['zh'][a] = reworded['zh'][b]                    # a now reads like b: b's records are not touched
        with mock.patch.object(i18n, 'CATALOG', reworded):
            again = [_sandbox.zh_t(a), _sandbox.zh_t(b), '01  ·  ' + _sandbox.zh_t(b), '用户']
            self.assertEqual(_sandbox.rendered(record, {'x': again}), {'x': again})
            wrong = ['其他', _sandbox.zh_t(a), '01  ·  其他', '用户']
            self.assertNotEqual(_sandbox.rendered(record, {'x': wrong}), {'x': wrong})
            self.assertEqual(_sandbox.rendered(record, {'x': wrong})['x'][0], _sandbox.zh_t(a),
                             'a difference shows the expected text, not the key')
        self.assertEqual(_sandbox.rendered(record, {'x': []}), {'x': [_sandbox.zh_t(a), _sandbox.zh_t(a),
                                                                       '01  ·  ' + _sandbox.zh_t(b), '用户']})

    def test_a_keyed_golden_does_not_pin_the_wording(self):
        import i18n

        class Probe(unittest.TestCase):
            def runTest(self):
                pass

        folder = _sandbox.out('golden-keyed-self-test', 'x').parent
        key = 'library.pending.count'
        data = {'shown': [_sandbox.zh_t(key, count='abc'), '用户写的标题']}
        try:
            with mock.patch.object(_sandbox, 'GOLDEN', folder), mock.patch.dict(os.environ), \
                    mock.patch.object(_sandbox, 'RUNNER_MODE', False), contextlib.redirect_stdout(io.StringIO()):
                os.environ['GOLDEN_UPDATE'] = '1'
                _sandbox.check_golden(Probe(), 'keyed.json', data, keyed=True)
                os.environ.pop('GOLDEN_UPDATE')
                self.assertEqual(json.loads((folder / 'keyed.json').read_text(encoding='utf-8')),
                                 {'shown': [{'zh': key, 'p': {'count': 'abc'}}, '用户写的标题']})
                reworded = {lang: dict(texts) for lang, texts in i18n.CATALOG.items()}
                reworded['zh'][key] = '改过 ' + reworded['zh'][key]
                with mock.patch.object(i18n, 'CATALOG', reworded):
                    _sandbox._KEYED_INDEX = None
                    _sandbox.check_golden(Probe(), 'keyed.json', {'shown': [_sandbox.zh_t(key, count='abc'),
                                                                            '用户写的标题']}, keyed=True)
                    with self.assertRaises(AssertionError):      # another value is still a difference
                        _sandbox.check_golden(Probe(), 'keyed.json', {'shown': [_sandbox.zh_t(key, count='abd'),
                                                                                '用户写的标题']}, keyed=True)
        finally:
            _sandbox._KEYED_INDEX = None

    def test_the_recorded_goldens_exist(self):
        for name in ('argv.json', 'events.json', 'runonce.json', 'ui.json'):
            self.assertTrue(json.loads((_sandbox.GOLDEN / name).read_text(encoding='utf-8')), name)

    def test_release_sources_and_fixture_texts(self):
        """(1.8.0) The G2 exemption helper (MESSAGES.md 7.3).  (2.0.0 S2) The frozen released
        sources are gone, and with them release_src."""
        self.assertFalse(hasattr(_sandbox, 'release_src'))
        self.assertFalse((_sandbox.ROOT / 'verification' / 'release-sources').exists())
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

    def test_markers_are_read_from_the_first_five_lines(self):
        """(2.0.0 S1) '# serial' (with or without a reason) and '# quick', like the tier comment."""
        self.assertEqual(self.runner.marks_of(self.write('m1.py', '# tier: all\n# serial: screenshots\n"""doc"""\n')),
                         {'serial'})
        self.assertEqual(self.runner.marks_of(self.write('m2.py', '# quick\n"""doc"""\n')), {'quick'})
        self.assertEqual(self.runner.marks_of(self.write('m3.py', '\n\n\n\n\n# serial\n')), set())
        self.assertEqual(self.runner.marks_of(self.write('m4.py', 'import os  # quick\n# serialized\n')), set())
        self.assertEqual(self.runner.tier_of(self.write('m5.py', '# quick\n# tier: all\n')), 'all')
        self.assertEqual(self.runner.name_filters(' a, ,b,'), ['a', 'b'])
        self.assertEqual(self.runner.name_filters(None), [])

    def test_the_marked_files_of_this_repository(self):
        """(2.0.0 S1) The quick set builds no window and starts no app; the serial set is the files that time
        the wall clock tightly, take the foreground window or take screenshots, each with its reason."""
        marks = {path.name: self.runner.marks_of(path) for path, _tier in self.runner.discover()}
        quick = sorted(name for name, found in marks.items() if 'quick' in found)
        serial = sorted(name for name, found in marks.items() if 'serial' in found)
        self.assertEqual(serial, ['test_animation_timing.py', 'test_core_procutil.py', 'test_i18n_detect.py',
                                  'test_i18n_snapshot.py', 'test_v170_ui.py'])
        for name in serial:
            line = next(line for line in self.runner.head_lines(_sandbox.ROOT / 'verification' / name)
                        if line.startswith('# serial'))
            self.assertRegex(line, r'^# serial: \S', f'{name}: say why it must run alone')
        self.assertGreaterEqual(len(quick), 20)
        tiers = {path.name: tier for path, tier in self.runner.discover()}
        for name in quick:
            text = (_sandbox.ROOT / 'verification' / name).read_text(encoding='utf-8')
            self.assertEqual(tiers[name], 'default', name)
            for needle in ('StudioApp(', 'tk.Tk(', 'ctk.CTk(', 'make_app(', 'app_args(', 'snapshot_ui'):
                self.assertNotIn(needle, text, f'{name} is marked "# quick" but builds a window ({needle})')
            self.assertNotIn('serial', marks[name], name)

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
        self.assertIn('golden/stored-keys.json', tree)
        self.assertFalse([name for name in tree if '__pycache__' in name])
        # Files carry (size, st_mtime_ns), so a rewritten file is noticed; folders carry None.
        status = (_sandbox.ROOT / 'verification' / 'run_tests.py').stat()
        self.assertEqual(tree['run_tests.py'], (status.st_size, status.st_mtime_ns))
        self.assertIsNone(tree['golden'])
        self.assertIn('golden/text-allow/engine.json', tree)

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

    def role(self, name):
        path = self.report.with_name(name)
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None

    def test_parallel_run_has_the_content_of_the_serial_run(self):
        """(2.0.0 S1) -j: the same results in the same order, the serial file after the parallel batch, and the
        files really overlap; only the serial run becomes the release gate's source report."""
        timed = ('import json, os, time\n'
                 'started = time.time()\ntime.sleep({sleep})\n'
                 'json.dump({{"started": started, "ended": time.time()}}, '
                 'open(os.path.join(os.environ["VERIFY_OUT"], "times.json"), "w"))\n'
                 'print("{name} done")\n')
        for name, sleep in (('a', 1.5), ('b', 1.5), ('c', 1.5)):
            self.add(f'test_{name}.py', timed.format(sleep=sleep, name=name))
        self.add('test_d.py', '# serial: reads the wall clock\n' + timed.format(sleep=0.1, name='d'))
        self.add('test_e.py', '# tier: all\n' + timed.format(sleep=0.1, name='e'))
        self.add('test_f.py', 'import sys\nprint("broken on purpose")\nsys.exit(4)\n')
        code, serial_text, serial = self.main('--all')
        self.assertEqual(code, 1, serial_text)
        # A failing serial run replaces the gate's copy too (the gate refuses it): no older green copy survives.
        self.assertEqual(self.role('report-source.json'), serial)
        code, text, parallel = self.main('--all', '-j', '3')
        self.assertEqual(code, 1, text)
        self.assertIn('parallel 5 files, 3 at a time; then 1 serial: test_d.py', text)
        self.assertIn('with -j 3', text)
        self.assertIn('FAILED: test_f.py', text)

        def content(report):
            return [{key: value for key, value in item.items() if key != 'seconds'} for item in report['results']]
        self.assertEqual(content(parallel), content(serial))
        self.assertEqual([item['file'] for item in parallel['results']],
                         ['test_a.py', 'test_b.py', 'test_c.py', 'test_d.py', 'test_f.py', 'test_e.py'])
        for key in ('tiers', 'filter', 'not_run', 'msg_guard', 'source_sha256', 'app_version', 'exe', 'quick',
                    'only_tier'):
            self.assertEqual(parallel[key], serial[key], key)
        self.assertEqual(set(parallel['reports']), set(serial['reports']))
        self.assertEqual((serial['jobs'], parallel['jobs']), (1, 3))
        times = {key.split('/')[0]: value for key, value in parallel['reports'].items()}
        batch = [times[name] for name in ('test_a', 'test_b', 'test_c', 'test_e')]
        self.assertGreaterEqual(times['test_d']['started'], max(item['ended'] for item in batch),
                                'the serial file runs after the parallel batch')
        self.assertLess(max(item['started'] for item in batch[:3]), min(item['ended'] for item in batch[:3]),
                        'three files at a time overlap')
        self.assertEqual(self.role('report-source.json'), serial, 'a parallel run is never the gate\'s source report')

    def test_selection_by_names_quick_and_only_tier_and_the_gate_copies(self):
        """(2.0.0 S1) -k with commas, --quick, --only-tier; report-source.json / report-exe.json are written by
        unfiltered serial runs only."""
        self.add('test_alpha.py', '# quick\nprint("alpha")\n')
        self.add('test_beta.py', 'print("beta")\n')
        self.add('test_slow.py', '# tier: all\nprint("slow")\n')
        self.add('test_gamma.py', '# tier: exe\nimport os\nassert os.path.isfile(os.environ["VERIFY_EXE"])\n')
        exe = self.root / 'app.exe'
        exe.write_bytes(b'not really an exe')
        code, text, report = self.main('-k', 'alpha,beta', '--all')
        self.assertEqual((code, [item['file'] for item in report['results']], report['filter']),
                         (0, ['test_alpha.py', 'test_beta.py'], 'alpha,beta'))
        self.assertEqual((self.role('report-source.json'), self.role('report-exe.json')), (None, None))
        code, text, report = self.main('--quick', '--all')
        self.assertEqual((code, [item['file'] for item in report['results']], report['quick']),
                         (0, ['test_alpha.py'], True))
        self.assertEqual(self.role('report-source.json'), None)
        self.assertEqual(self.main('--quick', '-k', 'beta')[0], 2)
        self.assertEqual(self.main('--only-tier', 'exe')[0], 2)
        self.assertEqual(self.main('-j', '0')[0], 2)
        code, text, report = self.main('--only-tier', 'exe', '--exe', str(exe))
        self.assertEqual(code, 0, text)
        self.assertEqual(([item['file'] for item in report['results']], report['tiers'], report['only_tier']),
                         (['test_gamma.py'], ['exe'], 'exe'))
        self.assertIn('(also report-exe.json)', text)
        self.assertEqual(self.role('report-exe.json'), report)
        self.assertIsNone(self.role('report-source.json'))
        code, text, report = self.main('--all')
        self.assertEqual(code, 0, text)
        self.assertEqual([item['file'] for item in report['results']], ['test_alpha.py', 'test_beta.py', 'test_slow.py'])
        self.assertEqual(self.role('report-source.json'), report)
        self.assertEqual(self.role('report-exe.json')['tiers'], ['exe'], 'the exe copy is left as it was')
        code, text, report = self.main('--all', '-j', '2')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.role('report-source.json')['jobs'], 1, 'the serial copy is kept')
        self.assertEqual(self.runner.report_roles(dict(report, jobs=1)), ['source'])
        self.assertEqual(self.runner.report_roles(dict(report, jobs=1, exe={'sha256': 'x'}, tiers=['default', 'all', 'exe'])),
                         ['exe'])

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
