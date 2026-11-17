"""1.7.0 language adaptation: choosing the language and restarting (I18N.md 3.3, 3.4, 5, 6.3, 11.3; WP-L0).

    python -X utf8 -B test_i18n_detect.py

i18n._win32 is replaced by _sandbox.fake_system, so detection is the same on any build PC; the
real values of this PC are printed once (the WP-L0 handoff records them).  No window except the
hidden Tk root of the focus test; the only processes started are this Python (wait_exit) and one
sandboxed --self-test.
"""
import _sandbox
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types
import unittest
import uuid
from unittest import mock

import i18n
import paths
import procutil
import settings

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# settings.defaults() of 1.6.0: the language choice is not a typed setting (I18N.md 5.1, D11).
DEFAULT_KEYS_1_6_0 = {'output', 'mode', 'resolution', 'batch_safe', 'archive_enabled', 'archive_root', 'cookies',
                      'naming', 'embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded', 'notify_done',
                      'clipboard_hint', 'check_engine_updates', 'last_engine_check', 'bot_check_at', 'latest_engine',
                      'last_clip', 'geometry', 'zoomed', 'appearance'}


def settings_path() -> Path:
    return paths.settings_file()


def write_settings(data) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


class DetectTests(unittest.TestCase):
    def test_this_pc(self):
        info = i18n.diagnostics('system')
        print(f'this PC: ui {info["ui_language"]}, locale {info["locale"]}, detect() = {i18n.detect()}', flush=True)
        self.assertIn(i18n.detect(), i18n.LANGUAGES)

    def test_the_windows_combinations(self):
        cases = [((0x0409, 'zh-CN'), 'zh'),      # the owner's PC: display en-US, regional format zh-CN
                 ((0x0804, 'en-US'), 'zh'), ((0x0404, 'zh-TW'), 'zh'), ((0x0C04, 'en-HK'), 'zh'),
                 ((0x1004, 'en-SG'), 'zh'), ((0x0409, 'zh-Hans-HK'), 'zh'), ((0x0409, 'ZH-cn'), 'zh'),
                 ((0x0409, 'en-GB'), 'en'), ((0x0411, 'ja-JP'), 'en'), ((0x0409, 'en-US'), 'en'),
                 ((OSError('no UI language'), 'zh-CN'), 'zh'), ((OSError, 'en-US'), 'en'),
                 ((0x0804, RuntimeError('no locale')), 'zh'), ((0x0409, OSError), 'en'),
                 ((OSError, OSError), 'zh'), ((0, ''), 'zh'), ((0, 'en-US'), 'en'), ((0x0409, ''), 'en')]
        for (ui, locale), expected in cases:
            with self.subTest(ui=ui, locale=locale), _sandbox.fake_system(ui, locale):
                self.assertEqual(i18n.detect(), expected)

    def test_the_order_of_precedence(self):
        with _sandbox.fake_system(0x0409, 'zh-CN'):
            self.assertEqual(i18n.resolve('zh', environ={'YTDLP_DESKTOP_LANG': 'en'}), 'en', 'environment first')
            self.assertEqual(i18n.resolve('system', environ={'YTDLP_DESKTOP_LANG': 'en-US'}), 'en')
            self.assertEqual(i18n.resolve('en', environ={'YTDLP_DESKTOP_LANG': 'ZH-tw'}), 'zh')
            self.assertEqual(i18n.resolve('en', environ={'YTDLP_DESKTOP_LANG': 'fr'}), 'en', 'an unknown value is ignored')
            self.assertEqual(i18n.resolve('en', environ={}), 'en', 'the setting beats a Chinese system')
            self.assertEqual(i18n.resolve('system', environ={}), 'zh')
            self.assertEqual(i18n.resolve('klingon', environ={}), 'zh', 'an invalid setting uses detection')
            self.assertEqual(i18n.resolve(None, environ={}), 'zh')
        with _sandbox.fake_system(0x0409, 'en-GB'):
            self.assertEqual(i18n.resolve('system', environ={}), 'en')
            self.assertEqual(i18n.resolve('zh', environ={}), 'zh')
            self.assertEqual(os.environ['YTDLP_DESKTOP_LANG'], 'zh', 'the sandbox runs in Chinese')
            self.assertEqual(i18n.resolve('system'), 'zh', 'the variable of this process wins')
            self.assertEqual(i18n.resolve('en', environ={}), 'en', 'environ={} ignores a set variable')
        self.assertEqual(i18n.env_override({'YTDLP_DESKTOP_LANG': ' en '}), 'en')
        self.assertIsNone(i18n.env_override({}))
        self.assertIsNone(i18n.env_override({'YTDLP_DESKTOP_LANG': ''}))

    def test_init_set_language_and_the_lazy_start(self):
        previous = i18n._current
        try:
            with _sandbox.language('zh', env=False), _sandbox.fake_system(0x0409, 'en-US'):
                self.assertNotIn('YTDLP_DESKTOP_LANG', os.environ)
                self.assertEqual(i18n.init('system'), 'en')
                self.assertEqual(i18n.language(), 'en')
                i18n.set_language(None)
                self.assertEqual(i18n.language(), 'en', 'resolved lazily with resolve("system")')
                self.assertEqual(i18n.init('zh'), 'zh')
            self.assertEqual(os.environ['YTDLP_DESKTOP_LANG'], 'zh')
            self.assertNotIn(_sandbox.LANG_OVERRIDE, os.environ)
            with self.assertRaises(ValueError):
                i18n.set_language('fr')
        finally:
            i18n.set_language(previous)
        self.assertEqual(i18n.language(), 'zh')

    def test_the_sandbox_language_helper(self):
        with _sandbox.language('en'):
            self.assertEqual((i18n.language(), os.environ['YTDLP_DESKTOP_LANG']), ('en', 'en'))
            code = 'import os; print(os.environ.get("YTDLP_DESKTOP_LANG"))'
            output = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, encoding='utf-8',
                                    timeout=60, creationflags=HIDDEN).stdout.strip()
            self.assertEqual(output, 'en', 'children run in the language of the block')
            child = ('import sys; sys.path.insert(0, %r); import _sandbox, os, i18n; '
                     'print(os.environ.get("YTDLP_DESKTOP_LANG"), i18n.language())' % str(_sandbox.ROOT / 'verification'))
            output = subprocess.run([sys.executable, '-B', '-c', child], capture_output=True, encoding='utf-8',
                                    timeout=60, creationflags=HIDDEN)
            self.assertEqual(output.stdout.split(), ['en', 'en'], output.stderr)
        with self.assertRaises(KeyError):
            with _sandbox.language('en'):
                raise KeyError('restored on an error too')
        self.assertEqual((i18n.language(), os.environ['YTDLP_DESKTOP_LANG']), ('zh', 'zh'))
        output = subprocess.run([sys.executable, '-B', '-c', child], capture_output=True, encoding='utf-8',
                                timeout=60, creationflags=HIDDEN)
        self.assertEqual(output.stdout.split(), ['zh', 'zh'], output.stderr)
        # A sandboxed child refuses a language that nobody chose on purpose.
        environment = dict(os.environ, YTDLP_DESKTOP_LANG='en')
        environment.pop(_sandbox.LANG_OVERRIDE, None)
        refused = subprocess.run([sys.executable, '-B', '-c', child], capture_output=True, encoding='utf-8',
                                 timeout=60, creationflags=HIDDEN, env=environment)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('YTDLP_DESKTOP_LANG (must be zh)', refused.stderr)

    def test_the_runner_starts_every_file_in_chinese(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('run_tests', _sandbox.ROOT / 'verification' / 'run_tests.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with mock.patch.dict(os.environ, {'YTDLP_DESKTOP_LANG': 'en', _sandbox.LANG_OVERRIDE: '1'}):
            environment = runner.child_environment(Path('X:\\base\\one'), Path('X:\\state'), None, False)
        self.assertEqual(environment['YTDLP_DESKTOP_LANG'], 'zh')
        self.assertNotIn(_sandbox.LANG_OVERRIDE, environment)


class StoredChoiceTests(unittest.TestCase):
    def tearDown(self):
        for path in (settings_path(),):
            if path.exists():
                path.unlink()

    def test_stored_choice_reads_the_extra_key(self):
        write_settings({'appearance': 'dark'})
        self.assertEqual(i18n.stored_choice(settings.load()), 'system')
        write_settings({'language': 'en'})
        self.assertEqual(i18n.stored_choice(settings.load()), 'en')
        write_settings({'language': 'zh'})
        self.assertEqual(i18n.stored_choice(settings.load()), 'zh')
        write_settings({'language': 5})
        self.assertEqual(i18n.stored_choice(settings.load()), 'system')
        write_settings({'language': 'EN'})
        self.assertEqual(i18n.stored_choice(settings.load()), 'system')
        settings_path().unlink()
        self.assertEqual(i18n.stored_choice(settings.load()), 'system')
        for prefs in ({}, {'_extra': 'x'}, {'_extra': {'language': ['en']}}, None, 'x'):
            self.assertEqual(i18n.stored_choice(prefs), 'system')

    def test_store_choice_is_saved_at_the_top_level_and_keeps_other_keys(self):
        write_settings({'appearance': 'dark', 'future_key': {'a': 1}, 'language': 'zh'})
        prefs = settings.load()
        self.assertTrue(i18n.store_choice(prefs, 'en'))
        self.assertFalse(i18n.store_choice(prefs, 'fr'))
        self.assertTrue(settings.save(prefs))
        stored = json.loads(settings_path().read_bytes().decode('utf-8'))
        self.assertFalse(settings_path().read_bytes().startswith(b'\xef\xbb\xbf'), 'no BOM')
        self.assertEqual(stored['language'], 'en')
        self.assertEqual(stored['future_key'], {'a': 1})
        self.assertEqual(stored['appearance'], 'dark')
        self.assertFalse([key for key in stored if key.startswith('_')])
        self.assertEqual(i18n.stored_choice(settings.load()), 'en')
        fresh = {}
        self.assertTrue(i18n.store_choice(fresh, 'system'))
        self.assertEqual(fresh, {'_extra': {'language': 'system'}})
        self.assertFalse(i18n.store_choice(None, 'en'))

    def test_a_held_session_does_not_store_the_choice(self):
        write_settings({'appearance': 'dark', 'language': 'zh'})
        with mock.patch.object(settings, '_read_checked', side_effect=settings._Unreadable(OSError('held'))):
            prefs = settings.load()
        self.assertIn(settings.HELD, prefs)
        self.assertEqual(i18n.HELD, settings.HELD)
        self.assertFalse(i18n.store_choice(prefs, 'en'))
        prefs['appearance'] = 'system'          # a change of this session is still saved
        self.assertTrue(settings.save(prefs))
        stored = json.loads(settings_path().read_text(encoding='utf-8'))
        self.assertEqual((stored['language'], stored['appearance']), ('zh', 'system'))

    def test_settings_py_is_the_1_6_0_one(self):
        self.assertEqual(set(settings.defaults()), DEFAULT_KEYS_1_6_0)
        self.assertEqual(set(settings._CHECKS), DEFAULT_KEYS_1_6_0)
        self.assertNotIn('language', settings.defaults())
        self.assertEqual(i18n.EXTRA, '_extra')


class AppStartTests(unittest.TestCase):
    """app.main() with everything around the window replaced: what it does with the language."""

    def run_main(self, argv, prefs_language=None, **extra):
        if prefs_language is None:
            if settings_path().exists():
                settings_path().unlink()
        else:
            write_settings({'language': prefs_language, 'check_engine_updates': False})
        import app
        import applog
        import studio
        import ui_theme
        import updater
        built = []

        class FakeApp:
            def __init__(self, arguments):
                built.append((arguments, i18n.language()))

            def mainloop(self):
                pass

        previous = i18n._current
        init = mock.Mock(side_effect=i18n.init)
        patches = [mock.patch.object(sys, 'argv', ['app.py', *argv]),
                   mock.patch.object(procutil, 'single_instance', return_value=object()),
                   mock.patch.object(procutil, 'wait_exit', return_value=True),
                   mock.patch.object(procutil, 'focus_window', return_value=True),
                   mock.patch.object(applog, 'setup'), mock.patch.object(applog, 'install_hooks'),
                   mock.patch.object(updater, 'cleanup'), mock.patch.object(paths, 'purge_stale'),
                   mock.patch.object(ui_theme, 'enable_dpi_awareness'),
                   mock.patch.object(studio, 'StudioApp', FakeApp), mock.patch.object(i18n, 'init', init)]
        for name, value in extra.items():
            patches.append(mock.patch.object(procutil, name, value))
        try:
            for patch in patches:
                patch.start()
            code = app.main()
            calls = {'wait_exit': procutil.wait_exit.call_args_list,
                     'single_instance': procutil.single_instance.call_count,
                     'focus_window': procutil.focus_window.call_args_list}
        finally:
            for patch in reversed(patches):
                patch.stop()
            i18n.set_language(previous)
            if settings_path().exists():
                settings_path().unlink()
        return code, init.call_args_list, built, calls

    def test_the_start_uses_the_stored_choice(self):
        code, inits, built, calls = self.run_main([], prefs_language='en')
        self.assertEqual(code, 0)
        self.assertEqual([call.args for call in inits], [('en',)])
        self.assertEqual(built[0][1], 'zh', 'YTDLP_DESKTOP_LANG of the sandbox wins over the stored en')
        self.assertEqual(built[0][0].appearance, 'light')
        self.assertEqual(calls['wait_exit'], [])
        with _sandbox.language('zh', env=False), _sandbox.fake_system(0x0409, 'zh-CN'):
            code, inits, built, _calls = self.run_main([], prefs_language='en')
            self.assertEqual(([call.args for call in inits], built[0][1]), ([('en',)], 'en'))
            code, inits, built, _calls = self.run_main([])
            self.assertEqual(([call.args for call in inits], built[0][1]), ([('system',)], 'zh'),
                             "the owner's PC (display en-US, format zh-CN) starts in Chinese")
        with _sandbox.language('zh', env=False), _sandbox.fake_system(0x0409, 'en-US'):
            code, inits, built, _calls = self.run_main([])
            self.assertEqual(built[0][1], 'en')

    def test_smoke_mode_uses_the_environment(self):
        code, inits, built, _calls = self.run_main(['--smoke-url', 'http://127.0.0.1:1/x'], prefs_language='en')
        self.assertEqual([call.args for call in inits], [('system',)])
        self.assertEqual(built[0][1], 'zh')

    def test_restarted_from_waits_for_the_old_process_first(self):
        order = []
        wait = mock.Mock(side_effect=lambda pid, timeout: order.append(('wait', pid, timeout)) or True)
        single = mock.Mock(side_effect=lambda: order.append(('single',)) or object())
        code, _inits, built, calls = self.run_main(['--restarted-from', '4242'], wait_exit=wait,
                                                   single_instance=single)
        self.assertEqual(code, 0)
        self.assertEqual(order, [('wait', 4242, 20), ('single',)])
        self.assertEqual(built[0][0].restarted_from, 4242)

    def test_a_second_start_raises_the_window_under_either_title(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('YTDLP_DESKTOP_INSTANCE')
            code, inits, built, calls = self.run_main([], single_instance=mock.Mock(return_value=None))
        self.assertEqual((code, built, inits), (0, [], []))
        self.assertEqual([call.args for call in calls['focus_window']], [(('YT-DLP 下载器', 'YT-DLP Downloader'),)])

    def test_self_test_reports_the_language(self):
        report = _sandbox.out('self-test', 'report.json')
        write_settings({'language': 'en', 'check_engine_updates': False})
        try:
            command = [sys.executable, '-B', str(_sandbox.ROOT / 'verification' / 'sandbox_app.py'),
                       '--self-test', str(report)]
            with _sandbox.language('zh', env=False):
                environment = procutil.restart_environment()
            process = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=300,
                                     creationflags=HIDDEN, env=dict(environment, **{_sandbox.LANG_OVERRIDE: '1'}))
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['language']['choice'], 'en')
            self.assertEqual(result['language']['resolved'], 'en', 'no variable: the stored choice decides')
            self.assertIsNone(result['language']['env'])
            for key in ('ui_language', 'locale', 'detected'):
                self.assertIn(key, result['language'])
        finally:
            settings_path().unlink()


class RestartHelperTests(unittest.TestCase):
    def test_relaunch_command(self):
        command = procutil.relaunch_command()
        self.assertEqual(command[:3], [sys.executable, '-X', 'utf8'])
        self.assertEqual(Path(command[3]), (_sandbox.SRC / 'app.py').resolve())
        self.assertEqual(command[4:], ['--restarted-from', str(os.getpid())])
        with mock.patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(procutil.relaunch_command(), [sys.executable, '--restarted-from', str(os.getpid())])

    def test_restart_environment(self):
        environment = procutil.restart_environment()
        self.assertEqual(environment['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        for name in ('LOCALAPPDATA', 'YTDLP_DESKTOP_APP_DIR', 'TEMP', 'YTDLP_DESKTOP_INSTANCE', 'YTDLP_VERIFY_SANDBOX',
                     'YTDLP_DESKTOP_LANG'):
            self.assertEqual(environment[name], os.environ[name], name)
        self.assertEqual(environment['PATH'], os.environ['PATH'], 'PATH does not grow on a restart')
        self.assertIsNot(environment, os.environ)
        with _sandbox.language('zh', env=False):
            self.assertNotIn('YTDLP_DESKTOP_LANG', procutil.restart_environment())

    def test_spawn_detached(self):
        with self.assertRaises(OSError):
            procutil.spawn_detached(['x.exe'], {})              # refused under the test runner / sandbox
        environment = {'A': 'b', 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        with mock.patch.dict(os.environ), mock.patch.object(procutil.subprocess, 'Popen') as popen:
            os.environ.pop('YTDLP_VERIFY_SANDBOX')
            result = procutil.spawn_detached(['x.exe', '--restarted-from', '7'], environment)
        self.assertIs(result, popen.return_value)
        args, kwargs = popen.call_args
        self.assertEqual(args, (['x.exe', '--restarted-from', '7'],))
        self.assertIs(kwargs['env'], environment)
        self.assertEqual(kwargs['cwd'], str(paths.APP_DIR))
        self.assertTrue(kwargs['close_fds'])
        self.assertEqual(kwargs['creationflags'], 0x00000008 | 0x00000200)

    def test_wait_exit(self):
        started = time.monotonic()
        self.assertTrue(procutil.wait_exit(0))
        self.assertTrue(procutil.wait_exit('x'))
        self.assertFalse(procutil.wait_exit(os.getpid(), timeout=5))
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'], creationflags=HIDDEN)
        try:
            self.assertFalse(procutil.wait_exit(child.pid, timeout=0.3))
        finally:
            child.kill()
            child.wait(10)
        self.assertTrue(procutil.wait_exit(child.pid, timeout=5), 'a pid that is gone returns at once')
        quick = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(0.5)'], creationflags=HIDDEN)
        try:
            self.assertTrue(procutil.wait_exit(quick.pid, timeout=20))
        finally:
            quick.wait(10)
        self.assertLess(time.monotonic() - started, 15)

    def test_focus_window_takes_several_titles(self):
        import tkinter as tk
        suffix = 'YT-DLP Downloader-' + uuid.uuid4().hex
        root = tk.Tk()
        try:
            root.title('✓ Done: 3 — ' + suffix)
            root.attributes('-alpha', 0.0)
            root.update()
            self.assertTrue(procutil.focus_window(('YT-DLP 下载器-' + uuid.uuid4().hex, suffix), timeout=3.0))
            self.assertFalse(procutil.focus_window(('nothing-' + uuid.uuid4().hex,), timeout=0.3))
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
