"""1.7.0 language adaptation, WP-L1-B: the settings page, the collect page and the library page (I18N.md 13).

    python -X utf8 -B test_i18n_pages.py

Unit tests of the L1-B work: the language row 语言 / Language with its restart, held and
environment notes (6.2); the restart with an injected launcher, restart-state.json written,
restored and refused (6.3), and that no real program is ever started under the runner; the
choice saved additively through prefs['_extra'] and read by a 1.5.1-shaped load (5.1); the
labeled interval / collect mode / filter / naming menus keep the stored values and naming_label
its zh value, the library tab ids stay (7); stored texts in the history and subscription notes
(a collect job's error, a '；'-joined last_error, job titles, 未命名订阅, the page-title fallback)
in the running language (9.3); the archive tools row and the Type column widths (10.1); the
engine badge (9.3) and an English check of the three pages.  Windows are never on screen
(alpha 0, no frame); no network (the GitHub check is patched); no real engine.

(1.8.0, MESSAGES.md 8.3) Live events carry the messages the 1.8.0 core emits; stored rows are seeded
as keyless legacy text and read as the library reads them (stored_job / stored_subscription), so
the legacy reader lifts them; user data that reads like an app text runs inside fixture_texts.
"""
import _sandbox
import json
import os
import re
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import customtkinter as ctk
import collection_ui
import i18n
import library
import library_ui
import messages
import naming
import paths
import procutil
import settings
import ui_settings
import ui_theme
import updater
from messages import msg, raw

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
ALLOWED = set('【】『』')
TABS = ('任务历史与续采', '增量订阅', '统一资料归档')        # the 1.6.0 tab ids


def cjk(text):
    text = str(text)
    for key in ('settings.language.zh', 'settings.language.title'):
        for allowed in i18n.all_values(key):
            text = text.replace(allowed, '')
    return [char for char in text if CJK.match(char) and char not in ALLOWED]


def stored_job(row):
    """(1.8.0) A history row as Library.history returns it: the stored record and its title column
    decoded (a keyless 1.5.1 / 1.6.0 / 1.7.0 text lifted by the legacy reader, MESSAGES.md 3.4)."""
    columns = {'kind': row.get('kind')}
    if 'title' in row:
        columns['title'] = row['title']
    data, columns = library._read_record(row.get('data') or {}, columns)
    return dict(row, data=data, **{name: value for name, value in columns.items() if name in row})


def stored_subscription(row):
    """(1.8.0) A subscription row as Library.subscriptions returns it: the name column decoded."""
    return dict(row, **messages.decode_columns(row['data'], 'subscription', {'name': row['name']}))


USER_DATA = 'a stored title or name that reads like an app text is user data (CR2-2); show() keeps it'


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.005)
    return condition is None


def walk(widget):
    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.winfo_children())


def texts_of(root):
    """Every text the widgets below ``root`` show or offer (as test_i18n_ui.widget_texts)."""
    found = []
    for widget in walk(root):
        if isinstance(widget, ctk.CTkLabel):
            found.append(ui_theme.full_text(widget))
        elif isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkSwitch)):
            found.append(widget.cget('text'))
        if isinstance(widget, ctk.CTkOptionMenu):
            found.append(widget._text_label.cget('text'))
            found += list(widget.cget('values'))
        elif isinstance(widget, ctk.CTkSegmentedButton):
            found += list(widget.cget('values'))
        elif isinstance(widget, ctk.CTkEntry):
            found.append(widget.cget('placeholder_text') or '')
        elif isinstance(widget, ui_theme.AppTable):
            found += [widget.heading(column, 'text') for column in widget['columns']]
            found.append(getattr(widget, '_empty', ''))
    return [text for text in found if text]


class AppCase(unittest.TestCase):
    """Smoke or sandboxed (settings.json in the sandbox) StudioApps, never on screen; the language
    contexts entered by a test are left at its end."""

    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', side_effect=AssertionError('no network')),
                        mock.patch('studio.StudioApp.clipboard_get', side_effect=tk.TclError('no clipboard'))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state_file = paths.settings_file().with_name('restart-state.json')
        for path in (paths.settings_file(), self.state_file):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._apps = []

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def owner_pc(self, env=False, lang='zh'):
        """The owner's PC (display en-US, format zh-CN), a normal start (no YTDLP_DESKTOP_LANG)."""
        self.enter(_sandbox.language(lang, env=env))
        self.enter(_sandbox.fake_system(0x0409, 'zh-CN'))

    def make_app(self, smoke=True, size='1120x810', output='i18n-pages', **stored):
        from studio import StudioApp
        for earlier in self._apps:
            self.close(earlier)
        self._apps = []
        ctk.set_appearance_mode('light')
        if smoke:
            app = StudioApp(_sandbox.app_args('https://example.invalid/pages', str(_sandbox.out(output))))
            app.urls.delete('1.0', 'end')
            app.links_changed()
        else:
            _sandbox.write_settings(output=str(_sandbox.out('i18n-pages-out')), batch_safe=False, **stored)
            app = StudioApp(_sandbox.app_args())
        self._apps.append(app)
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.geometry(size + '+0+0')
        self.addCleanup(self.close, app)
        pump(app, timeout=0.3)
        return app

    @staticmethod
    def close(app):
        try:
            writer = getattr(app, 'history_writer', None)
            if writer is not None:
                writer.idle(5)
            if not getattr(app, '_finished', False) and app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def stored():
        return json.loads(paths.settings_file().read_text(encoding='utf-8'))

    def strip(self, app):
        """(note keys shown, restart button shown, its state) of the language row."""
        app.show_page('settings')
        pump(app, timeout=0.2)
        shown = {key for key in ('settings.language.restart_note', 'settings.language.restart_busy',
                                 'settings.language.env_fixed', 'settings.language.held')
                 if app.language_note.winfo_viewable() and ui_theme.full_text(app.language_note) == i18n.t(key)}
        button = app.language_restart.winfo_viewable()
        return shown, button, app.language_restart.cget('state') if button else None

    def logged(self, app):
        return app.logs.get('1.0', 'end-1c') + '\n' + '\n'.join(app.log_buffer)


# ------------------------------------------------------------------------------ 6.2 the language row
class LanguageRowTests(AppCase):
    def test_the_row_sits_after_appearance_and_shows_the_stored_choice(self):
        self.owner_pc()
        app = self.make_app(smoke=False, language='en')
        self.assertEqual(i18n.language(), 'zh', 'the sandboxed app was built in zh; the stored en applies at a start')
        self.assertEqual(app.language_choice.get(), 'en', 'the stored choice, not the resolved language')
        self.assertEqual(list(app.language_choice.cget('values')), ['跟随系统', '简体中文', 'English'])
        self.assertEqual(self.strip(app), (set(), False, None), 'an untouched row shows no strip')
        # card 提醒与界面: the row comes right after 外观, the hidden strip below it
        def row_of(control):            # control -> control_area -> setting row
            return control.master.master
        language, appearance = row_of(app.language_choice), row_of(app.appearance_choice)
        self.assertIs(language.master, appearance.master)
        self.assertEqual(int(language.grid_info()['row']), int(appearance.grid_info()['row']) + 1)
        self.assertEqual(language.title_label.cget('text'), '语言 / Language')
        self.assertTrue(any(widget is app.language_choice for widget in walk(app.settings_cards[4])))
        self.assertNotIn(app.language_choice, app.controls, 'the choice may change while busy')

    def test_choosing_stores_additively_and_offers_the_restart(self):
        self.owner_pc()
        app = self.make_app(smoke=False)
        self.assertEqual(app.language_choice.get(), 'system')
        app.choose_language('en')
        self.assertEqual(app.prefs['_extra']['language'], 'en')
        app.save_settings()
        stored = self.stored()
        self.assertEqual(stored['language'], 'en', 'top level, through _extra')
        self.assertNotIn('_extra', stored)
        self.assertEqual(set(settings.defaults()) - set(stored), set(), 'every 1.6.0 key is still written')
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        app.operation = 'download'
        app.set_busy(True)
        self.assertEqual(self.strip(app), ({'settings.language.restart_busy'}, True, 'disabled'))
        app.set_busy(False)
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        for value in ('zh', 'system'):
            app.choose_language(value)
            self.assertEqual(self.strip(app), (set(), False, None), f'{value} resolves to the running zh')
        # a click on a segment hands over the stored value
        app.language_choice._buttons_dict['English'].invoke()
        pump(app, timeout=0.1)
        self.assertEqual(app.prefs['_extra']['language'], 'en')
        app.choose_language('system')
        app.save_settings()
        self.assertEqual(self.stored()['language'], 'system')

    def test_in_english_the_row_reads_english_first(self):
        self.owner_pc(lang='en')
        app = self.make_app(smoke=False)
        self.assertEqual(list(app.language_choice.cget('values')), ['System', '简体中文', 'English'])
        app.choose_language('zh')
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        self.assertEqual(app.language_restart.cget('text'), 'Restart now')
        app.choose_language('en')
        self.assertEqual(self.strip(app), (set(), False, None))

    def test_a_set_variable_and_a_held_start(self):
        self.owner_pc(env=True)
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.assertEqual(self.strip(app), ({'settings.language.env_fixed'}, False, None))
        app.save_settings()
        self.assertEqual(self.stored()['language'], 'en', 'the choice is saved all the same')
        app.prefs[settings.HELD] = {'loaded': {}, 'changed': set()}
        app.choose_language('zh')
        self.assertEqual(self.strip(app), ({'settings.language.held'}, False, None))
        self.assertEqual(app.prefs['_extra']['language'], 'en', 'a held session does not keep the change')

    def test_smoke_mode_never_offers_the_restart(self):
        self.owner_pc()
        app = self.make_app(smoke=True)
        app.choose_language('en')
        shown, button, _state = self.strip(app)
        self.assertEqual((shown, button), ({'settings.language.restart_note'}, False))


# ------------------------------------------------------------------------------ 5.1 additive, 1.5.1
class StoredChoiceTests(AppCase):
    def test_a_1_5_1_shaped_load_reads_the_file_and_a_1_5_1_save_drops_the_choice(self):
        self.owner_pc()
        app = self.make_app(smoke=False, mode='音频 · FLAC', resolution='1080p', cookies='', archive_enabled=False)
        app.choose_language('en')
        app.save_settings()
        raw = paths.settings_file().read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 without BOM')
        # 1.5.1 (studio.py of the backup): json.loads of the flat object and .get() per key
        legacy = json.loads(raw.decode('utf-8'))
        self.assertIsInstance(legacy, dict)
        self.assertEqual(legacy.get('mode'), '音频 · FLAC')
        self.assertEqual(legacy.get('resolution'), '1080p')
        self.assertIs(legacy.get('batch_safe', True) is not False, False)
        self.assertEqual(legacy.get('output'), str(_sandbox.out('i18n-pages-out')))
        self.assertTrue(all(not isinstance(value, dict) for key, value in legacy.items() if key != 'geometry'))
        # A 1.5.1 save writes only its own keys: the choice is gone, 跟随系统 again.
        paths.settings_file().write_text(json.dumps({key: legacy[key] for key in ('output', 'mode', 'resolution',
                                                                                  'batch_safe', 'cookies')},
                                                    ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertEqual(i18n.stored_choice(settings.load()), 'system')
        app.save_settings()
        self.assertEqual(self.stored()['language'], 'en', 'the running session writes its choice again')


# ------------------------------------------------------------------------------ 6.3 the restart
class RestartTests(AppCase):
    def setUp(self):
        super().setUp()
        self.owner_pc()

    @staticmethod
    def recorder(error=None):
        calls = []

        def launch(command, env):
            calls.append((list(command), dict(env)))
            if error is not None:
                raise error
        return calls, launch

    @staticmethod
    def fill(app):
        app.urls.insert('1.0', 'https://www.example.org/a.mp3\nhttps://www.example.org/b.mp3')
        app.links_changed()
        app.collect_urls.insert('1.0', 'https://www.example.org/podcast/list')
        app.subscription_name.set('Weekly podcast')

    def test_the_restart_hands_over_the_command_and_keeps_the_input(self):
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.fill(app)
        calls, app.restart_launcher = self.recorder()
        app.restart_app()
        self.assertEqual(len(calls), 1)
        command, env = calls[0]
        self.assertEqual(command[-2:], ['--restarted-from', str(os.getpid())])
        self.assertEqual(command[:-2], procutil.relaunch_command()[:-2])
        self.assertEqual(env.get('PYINSTALLER_RESET_ENVIRONMENT'), '1')
        self.assertNotIn('YTDLP_DESKTOP_LANG', env)
        self.assertEqual(env.get('LOCALAPPDATA'), os.environ['LOCALAPPDATA'], 'a sandboxed process stays sandboxed')
        self.assertTrue(app._finished, 'the window closes')
        self.assertEqual(self.stored()['language'], 'en')
        state = json.loads(self.state_file.read_text(encoding='utf-8'))
        self.assertEqual(set(state), {'version', 'created', 'pid', 'urls', 'collect_urls', 'subscription_name'})
        self.assertEqual((state['version'], state['pid']), (1, os.getpid()))
        self.assertEqual(state['subscription_name'], 'Weekly podcast')
        # the next start restores the three inputs once, logs it and deletes the file
        app = self.make_app(smoke=False)
        self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5))
        pump(app, timeout=0.2)
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://www.example.org/a.mp3\nhttps://www.example.org/b.mp3')
        self.assertEqual(app.collect_urls.get('1.0', 'end-1c'), 'https://www.example.org/podcast/list')
        self.assertEqual(app.subscription_name.get(), 'Weekly podcast')
        self.assertIn(i18n.t('log.restart_restored'), self.logged(app))
        self.assertIn('2', app.link_count.cget('text'), 'the link count follows the restored box')

    def test_nothing_typed_writes_no_state_file(self):
        app = self.make_app(smoke=False)
        calls, app.restart_launcher = self.recorder()
        app.restart_app()
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.state_file.exists())

    def test_a_stale_broken_or_foreign_state_file_is_deleted_and_ignored(self):
        old = {'version': 1, 'created': time.time() - 200, 'pid': 1, 'urls': 'https://x.example/a',
               'collect_urls': '', 'subscription_name': ''}
        for content in (json.dumps(old), '{broken', json.dumps(dict(old, created=time.time(), version=2)), '[]'):
            self.state_file.write_text(content, encoding='utf-8')
            app = self.make_app(smoke=False)
            self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5), content)
            self.assertEqual(app.urls.get('1.0', 'end-1c'), '', content)
            self.assertNotIn(i18n.t('log.restart_restored'), self.logged(app))

    def test_a_restored_input_never_overwrites_what_is_there(self):
        self.state_file.write_text(json.dumps({'version': 1, 'created': time.time(), 'pid': 1, 'urls': 'https://x.example/a',
                                               'collect_urls': 'https://x.example/list', 'subscription_name': 'Kept'}),
                                   encoding='utf-8')
        with mock.patch.object(ui_settings.SettingsUI, 'restore_restart_state', lambda self: None):
            app = self.make_app(smoke=False)
        app.subscription_name.set('Typed')
        app.restore_restart_state()
        self.assertFalse(self.state_file.exists())
        self.assertEqual(app.subscription_name.get(), 'Typed')
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://x.example/a')

    def test_smoke_mode_neither_restarts_nor_restores(self):
        self.state_file.write_text(json.dumps({'version': 1, 'created': time.time(), 'pid': 1, 'urls': 'https://x.example/a',
                                               'collect_urls': '', 'subscription_name': ''}), encoding='utf-8')
        app = self.make_app(smoke=True)
        pump(app, timeout=0.3)
        self.assertTrue(self.state_file.exists(), 'smoke mode skips the restore')
        calls, app.restart_launcher = self.recorder()
        app.restart_app()
        self.assertEqual(calls, [])
        self.assertFalse(getattr(app, '_finished', False))

    def test_a_failing_launcher_keeps_the_window_and_leaves_no_state(self):
        app = self.make_app(smoke=False)
        self.fill(app)
        calls, app.restart_launcher = self.recorder(OSError('spawn refused'))
        with mock.patch('ui_settings.messagebox') as dialogs:
            app.restart_app()
        self.assertEqual(len(calls), 1)
        self.assertFalse(getattr(app, '_finished', False))
        self.assertTrue(app.winfo_exists())
        self.assertFalse(self.state_file.exists())
        dialogs.showerror.assert_called_once()
        self.assertEqual(dialogs.showerror.call_args.args[0], i18n.t('dialog.restart_failed.title'))
        self.assertIn('spawn refused', dialogs.showerror.call_args.args[1])

    def test_an_unwritable_state_file_asks_and_no_cancels_yes_restarts(self):
        app = self.make_app(smoke=False)
        self.fill(app)
        calls, app.restart_launcher = self.recorder()
        refused = mock.Mock(side_effect=OSError('disk full'))
        with mock.patch.object(paths, 'atomic_write_text', refused), mock.patch('ui_settings.messagebox') as dialogs:
            dialogs.askyesno.return_value = False
            app.restart_app()
            self.assertEqual(calls, [])
            self.assertFalse(getattr(app, '_finished', False))
            self.assertEqual(dialogs.askyesno.call_args.args[:2],
                             (i18n.t('dialog.restart_unsaved.title'), i18n.t('dialog.restart_unsaved.text')))
            dialogs.askyesno.return_value = True
            app.restart_app()
        self.assertEqual(len(calls), 1)
        self.assertTrue(app._finished)

    def test_the_default_launcher_never_starts_a_program_under_the_runner(self):
        self.assertTrue(os.environ.get('YTDLP_VERIFY_SANDBOX'), 'the sandbox marks the run')
        app = self.make_app(smoke=False)
        app.choose_language('en')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('a real process was started')) as popen, \
                mock.patch('ui_settings.messagebox') as dialogs:
            app.restart_app()
        popen.assert_not_called()
        self.assertFalse(getattr(app, '_finished', False), 'the refused restart keeps the window')
        self.assertEqual(dialogs.showerror.call_args.args[0], i18n.t('dialog.restart_failed.title'))

    def test_busy_or_closing_never_restarts(self):
        app = self.make_app(smoke=False)
        calls, app.restart_launcher = self.recorder()
        app.busy = True
        app.restart_app()
        app.busy, app.closing = False, True
        app.restart_app()
        app.closing = False
        self.assertEqual(calls, [])


# ------------------------------------------------------------------------------ 7 stored values
class StoredValueTests(AppCase):
    def menu_of(self, app, variable):
        menus = [widget for widget in walk(app) if isinstance(widget, ctk.CTkOptionMenu)
                 and str(widget.cget('variable')) == str(variable)]
        self.assertEqual(len(menus), 1)
        return menus[0]

    def test_labeled_menus_keep_the_stored_values_in_english(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app(smoke=False)
        cases = {'subscription_interval': (set(library_ui.INTERVALS), ['Manual only', 'Every 15 minutes', 'Hourly', 'Daily']),
                 'collect_mode': ({collection_ui.MODE_STATIC, collection_ui.MODE_DYNAMIC},
                                  ['Static page', 'Dynamic page (Chrome)']),
                 'collect_filter': (set(collection_ui.FILTERS), ['Media links', 'All links']),
                 'naming': (set(naming.NAMING), [i18n.t('naming.title'), i18n.t('naming.clean'), i18n.t('naming.title_id')])}
        for name, (allowed, labels) in cases.items():
            variable = getattr(app, name)
            menu = self.menu_of(app, variable)
            self.assertEqual(list(menu.cget('values')), labels, name)
            reached = set()
            for shown in labels:
                menu._dropdown_callback(shown)
                pump(app, timeout=0.05)
                self.assertIn(variable.get(), allowed, f'{name} after {shown!r}')
                self.assertEqual(menu._text_label.cget('text'), shown)
                reached.add(variable.get())
            self.assertEqual(reached, allowed, name)
        self.assertEqual(app.naming.get(), 'title_id')
        self.assertEqual(app.naming_label.get(), naming.LABELS['title_id'], 'naming_label keeps its zh value (H4)')
        app.naming.set('clean')
        self.assertEqual(app.naming_label.get(), naming.LABELS['clean'])
        self.assertEqual(self.menu_of(app, app.naming)._text_label.cget('text'), i18n.t('naming.clean'))
        # the interval stored for a subscription is the seconds of the zh label
        app.subscription_interval.set('每天')
        self.assertEqual(library_ui.INTERVALS[app.subscription_interval.get()], 86400)
        app.save_settings()
        self.assertEqual(self.stored()['naming'], 'clean')

    def test_appearance_stores_the_mode(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app(smoke=False)
        self.assertEqual(list(app.appearance_choice.cget('values')), ['Light', 'Dark', 'System'])
        app.appearance_choice._buttons_dict['Dark'].invoke()
        pump(app, timeout=0.1)
        self.assertEqual(app.prefs['appearance'], 'dark')
        app.choose_appearance('浅色')                    # the 1.6.0 label still works
        self.assertEqual((app.prefs['appearance'], app.appearance_choice.get()), ('light', 'light'))

    def test_tab_ids_stay_and_the_bar_shows_labels(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        self.assertEqual((library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE), TABS)
        self.assertEqual(app.library_tabs.get(), TABS[0])
        self.assertEqual(list(app.library_tabs._bar.cget('values')), ['History & Resume', 'Subscriptions', 'Archive'])
        app.library_tabs._clicked('Archive')
        self.assertEqual(app.library_tabs.get(), TABS[2])
        app.library_tabs.set(TABS[1])
        self.assertEqual(app.library_tabs._bar.get(), 'Subscriptions')


# ------------------------------------------------------------------------------ 9.3 stored texts
class StoredTextTests(AppCase):
    def test_history_notes_and_titles(self):
        self.enter(_sandbox.language('en'))
        collect = stored_job({'kind': 'collect', 'pages': [{}, {}], 'data': {'completed': [], 'errors': [
            '网站返回 HTTP 403，可尝试动态模式或稍后重试。']}})
        note = library_ui.LibraryUI.history_note_text(collect)
        self.assertEqual(note, 'Saved pages: 2 · Downloaded: 0 · Last error: '
                               + i18n.t('core.collect.http_status', status=403)[:130])
        self.assertEqual(cjk(note), [], note)
        download = stored_job({'kind': 'download', 'pages': [], 'data': {'completed': ['a'], 'errors': ['ERROR: raw engine line'],
                    'last_reason': {'error': 'ERROR: raw engine line', 'reason': '无法连接代理或网站，请检查代理地址'}}})
        note = library_ui.LibraryUI.history_note_text(download)
        self.assertEqual(note, 'Downloaded: 1 · Last error: ' + i18n.t('download.reason.proxy'))
        # job titles: the app-made forms localized, user data as it is
        # (final fix round 2, CR2-2) the placeholder of an untitled job; a 1.5.1 record has no 'titled'
        row = stored_job({'kind': 'download', 'title': '3条 · 音频 · MP3 320K', 'data': {}})
        self.assertEqual(library_ui.history_title(row), 'Links: 3 · Audio · MP3 320K')
        row = stored_job({'kind': 'collect', 'title': '订阅：未命名订阅', 'data': {}})
        self.assertEqual(library_ui.history_title(row), 'Subscription: Untitled subscription')
        row = stored_job({'kind': 'collect', 'title': '检查未完成', 'data': {}})
        with _sandbox.fixture_texts(USER_DATA):
            self.assertEqual(library_ui.history_title(row), '检查未完成', 'a title equal to a core text stays')
        row = stored_job({'kind': 'download', 'title': '2 条 · 音频 · FLAC',
                          'data': {'options': {'urls': ['https://example.org/a', 'https://example.org/b']}}})
        self.assertEqual(library_ui.history_title(row), 'example.org/a and others (2 links)')
        self.assertEqual(library_ui.history_status({'kind': 'download', 'status': 'partial', 'data': {}}), 'Unfinished')
        self.assertEqual(library_ui.history_status({'kind': 'collect', 'status': 'interrupted', 'data': {}}), 'Interrupted')
        self.assertEqual(library_ui.history_status({'kind': 'collect', 'status': 'odd', 'data': {}}), 'odd')
        cells = library_ui.subscription_row(stored_subscription({'name': '未命名订阅', 'enabled': True, 'interval': 900,
                                                                 'data': {'last_check': 0}, 'pending': 2}))
        self.assertEqual(cells, ('Untitled subscription', 'Every 15 minutes', 'Not checked yet', 2))
        # (1.8.0) a name the owner typed is stored raw (Library.subscription); one equal to a core text stays
        with _sandbox.fixture_texts(USER_DATA):
            cells = library_ui.subscription_row({'name': raw('检查未完成'), 'enabled': False, 'interval': 900,
                                                 'data': {'last_check': 0}, 'pending': 0})
        self.assertEqual(cells[:2], ('检查未完成', 'Paused'), 'a name is user data')
        with _sandbox.language('zh'):
            self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '订阅：未命名订阅',
                                                                  'data': {}})), '订阅：未命名订阅')
            self.assertEqual(library_ui.LibraryUI.history_note_text(collect),
                             '2 个已保存页面 · 0 条下载成功 · 最近错误：网站返回 HTTP 403，可尝试动态模式或稍后重试。')

    def test_subscription_note_with_a_joined_last_error_and_the_stored_forms(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        options = {'urls': ['https://www.example.org/news'], 'dynamic': False, 'scrolls': 0, 'selector': '',
                   'max_pages': 1, 'proxy': ''}
        # (1.8.0) an empty name is the app's own untitled name (Library.subscription, C-F24)
        key = app.library.subscription('', options)
        self.assertEqual(app.library.subscriptions()[0]['name'], '未命名订阅', 'stored in its canonical form')
        # the collect job's record as Library.job returns it: the collector's messages
        record = {'status': 'partial', 'pages': [], 'data': {'errors': [
            msg('core.collect.http_status', status=403), msg('core.collect.too_large')]}}
        pages, successful, error = collection_ui.subscription_result(record, False)
        self.assertEqual(error, '网站返回 HTTP 403，可尝试动态模式或稍后重试。；页面超过 8 MB，请缩小采集范围。',
                         'stored in its canonical form')
        self.assertEqual(error.key, 'common.clauses', 'C-F39: one clauses message')
        self.assertEqual(collection_ui.subscription_result({'status': 'completed', 'pages': [], 'data': {'errors': []}},
                                                           True)[2], '采集已停止')
        self.assertEqual(collection_ui.subscription_result({'status': 'partial', 'pages': [], 'data': {'errors': []}},
                                                           False)[2], '检查未完成')
        app.library.finish_subscription(key, pages, successful, error)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        note = ui_theme.full_text(app.subscription_note)
        self.assertTrue(note.endswith('The site returned HTTP 403. Try dynamic mode or try again later. '
                                      'The page is larger than 8 MB. Narrow the collection scope.'), note)
        self.assertEqual(app.subscription_tree.item(key, 'values')[0], 'Untitled subscription')
        self.assertEqual(cjk(note), [], note)
        # an automatic check stores the canonical job title and shows the localized name
        with mock.patch.object(app, 'launch_collection') as launch:
            app.check_subscription(app.library.subscriptions()[0], automatic=True)
        self.assertEqual(launch.call_args.kwargs['title'], '订阅：未命名订阅')
        self.assertEqual(launch.call_args.kwargs['title'].key, 'value.job_title.subscription', 'C-F40')
        self.assertEqual(ui_theme.full_text(app.link_hint),
                         i18n.t('download.hint.subscription_check', name='Untitled subscription'))
        self.assertIn('Untitled subscription', ui_theme.full_text(app.collect_note))

    def test_the_collect_page_shows_the_page_title_fallback_and_collector_texts_localized(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        source = 'https://www.example.org/list'
        # what the 1.8.0 collector emits: the fallback title as a message (also as the label of a link
        # without one, C-H-10), a link label as raw user data
        fallback = msg('value.page_title.fallback')
        links = [{'url': 'https://www.example.org/a.mp4', 'title': fallback, 'kind': '媒体直链', 'source': source},
                 {'url': 'https://www.example.org/b', 'title': raw('网页正文'), 'kind': '网页链接', 'source': source}]
        app.handle('collect_page', {'url': source, 'title': fallback, 'text': 'x', 'markdown': 'x', 'links': links})
        pump(app, timeout=0.2)
        rows = [app.collect_tree.item(iid, 'values') for iid in app.collect_tree.get_children()]
        self.assertEqual([tuple(row[:2]) for row in rows], [('Direct link', 'Web page')], 'the media filter drops the web link')
        app.collect_filter.set(collection_ui.FILTERS[1])
        rows = [app.collect_tree.item(iid, 'values') for iid in app.collect_tree.get_children()]
        self.assertEqual([tuple(row[:2]) for row in rows], [('Direct link', 'Web page'), ('Web link', '网页正文')])
        self.assertIn('Page collected: Web page · Links: 2', self.logged(app))
        app.handle('collect_error', msg('core.collect.too_large'))
        self.assertEqual(ui_theme.full_text(app.collect_status),
                         'Some pages did not finish: ' + i18n.t('core.collect.too_large'))
        self.assertEqual(cjk(ui_theme.full_text(app.collect_status)), [])

    def test_archive_and_new_item_titles_and_1_5_1_errors(self):
        """(final fix round 1, CR1-1) the archive table and the new-items table show the fallback page
        title in the running language; (ENV-2) a 1.5.1-shaped stored collector error reads English."""
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app(output=f'i18n-pages-titles-{lang}')     # a smoke history of its own
                source = f'https://www.example.org/{lang}/list'
                folder = _sandbox.out('i18n-pages-archive', lang, 'untitled')
                app.library.archive_index(str(folder), {'url': source + '/a.m4a', 'source': source, 'title': '网页',
                                                        'uploader': '', 'files': [], 'transcripts': []})
                options = {'urls': [source], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
                key = app.library.subscription('City Night Talk ' + lang, options, 3600, True, True)
                page = {'url': source, 'title': '网页', 'text': '', 'markdown': '',
                        'links': [{'url': source + '/b.m4a', 'title': '网页', 'kind': '媒体地址', 'source': source}]}
                app.library.finish_subscription(key, [page], True)         # one new item (first_new)
                # what 1.5.1 stored: its collector texts without spaces, '；'-joined (collection_ui)
                app.library.finish_subscription(key, [], False,
                                                error='网站返回HTTP 503，可尝试动态模式或稍后重试。；页面超过8MB，请缩小采集范围。')
                app.refresh_library()
                shown = i18n.t('value.page_title.fallback')
                titles = [app.archive_tree.full_values(iid)[0] for iid in app.archive_tree.get_children()]
                self.assertIn(shown, titles)
                app.archive_search.set(shown[:4])
                pump(app, timeout=0.1)
                found = [app.archive_tree.full_values(iid)[0] for iid in app.archive_tree.get_children()]
                self.assertTrue(found and set(found) == {shown}, ('the shown title is searched', found))
                app.archive_search.set('')
                app.subscription_tree.selection_set(key)
                app.subscription_selection()
                pending = [app.pending_tree.full_values(iid)[0] for iid in app.pending_tree.get_children()]
                note = ui_theme.full_text(app.subscription_note)
                if lang == 'en':
                    self.assertEqual(pending, ['Web page'])
                    self.assertTrue(note.endswith('The site returned HTTP 503. Try dynamic mode or try again later. '
                                                  'The page is larger than 8 MB. Narrow the collection scope.'), note)
                    self.assertEqual(cjk(note), [], note)
                else:
                    self.assertEqual(pending, ['网页'])
                    self.assertIn('网站返回HTTP 503，可尝试动态模式或稍后重试。；页面超过8MB，请缩小采集范围。', note)
                self.close(app)

    def test_the_engine_badge_and_the_app_badge(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        with mock.patch.object(ui_settings, 'engine_version', return_value=updater.BUILTIN):
            app.prefs = {'latest_engine': '2099.01.01'}
            app.refresh_engine_labels()
        self.assertEqual(app.engine_badge.cget('text'), 'yt-dlp built-in')
        self.assertEqual(app.version_label.cget('text'), 'yt-dlp  built-in')
        self.assertTrue(ui_theme.full_text(app.app_badge).endswith('· new engine'), ui_theme.full_text(app.app_badge))
        app.on_update_error(msg('core.update.no_github', reason=raw('HTTP 403')))
        self.assertEqual(cjk(app.update_status.cget('text')), [])



# ------------------------------------------------------------------------------ final fix round 2
class FinalFixRound2Tests(AppCase):
    MEDIA = 'https://media.example.org/podcast/episode-12.mp3'

    def test_cr2_1_a_direct_media_start_page_reads_english(self):
        """The collector's title of a start page that is a media file (媒体直链, collector.py) in the
        collect table, the 'Page collected' log line, the archive table and the new-items table."""
        for lang, shown in (('en', 'Direct link'), ('zh', '媒体直链')):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app(output=f'i18n-pages-direct-{lang}')     # a smoke history of its own
                url = self.MEDIA.replace('episode', f'{lang}-episode')
                # what collector.py emits for such a page (without its checkpoint): one message (C-F12)
                direct = msg('value.link_kind.direct')
                app.handle('collect_page', {'url': url, 'title': direct, 'text': '', 'markdown': '',
                                            'links': [{'url': url, 'title': direct, 'kind': '媒体地址',
                                                       'source': url}], 'next': None})
                pump(app, timeout=0.2)
                rows = [tuple(app.collect_tree.full_values(iid)) for iid in app.collect_tree.get_children()]
                self.assertEqual(rows, [(i18n.label('link_kind', '媒体地址'), shown, url)])
                line = i18n.t('log.collect.page', title=shown, count=1)
                self.assertIn(line, self.logged(app))
                app.collect_search.set(shown[:6].lower() if lang == 'en' else shown[:2])
                app.render_collection_links()
                self.assertEqual(len(app.collect_tree.get_children()), 1, 'the shown title is searched')
                app.collect_search.set('')
                app.render_collection_links()
                folder = _sandbox.out('i18n-pages-archive', lang, 'direct')
                app.library.archive_index(str(folder), {'url': url, 'source': url, 'title': '媒体直链',
                                                        'uploader': '', 'files': [], 'transcripts': []})
                options = {'urls': [url], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
                key = app.library.subscription('Episode feed ' + lang, options, 3600, True, True)
                page = {'url': url, 'title': '媒体直链', 'text': '', 'markdown': '',
                        'links': [{'url': url, 'title': '媒体直链', 'kind': '媒体地址', 'source': url}]}
                app.library.finish_subscription(key, [page], True)
                app.refresh_library()
                titles = [app.archive_tree.full_values(iid)[0] for iid in app.archive_tree.get_children()]
                self.assertIn(shown, titles)
                app.subscription_tree.selection_set(key)
                app.subscription_selection()
                pending = [app.pending_tree.full_values(iid)[0] for iid in app.pending_tree.get_children()]
                self.assertEqual(pending, [shown])
                if lang == 'en':
                    self.assertEqual(cjk(line), [], line)
                self.close(app)

    def test_cr2_2_a_media_title_is_never_taken_for_an_app_made_form(self):
        self.enter(_sandbox.language('en'))
        one = {'options': {'urls': ['https://example.org/a']}, 'titled': True}
        three = {'options': {'urls': ['https://example.org/a', 'https://example.org/b', 'https://example.org/c']},
                 'titled': True}
        # (1.8.0) the rows as Library.history returns them (stored_job): the legacy reader applies the rule
        for title in ('订阅：周杰伦官方频道', '未命名订阅', '3 条 · 现场版', '3条 · 音频 · MP3 320K', '网页', 'City Night Talk'):
            with self.subTest(title=title), _sandbox.fixture_texts(USER_DATA):
                self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': title, 'data': one})),
                                 title)
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '现场版 等 3 条', 'data': three})),
                         '现场版 and others (3 links)')
        with _sandbox.fixture_texts(USER_DATA):         # the media title inside the lifted form
            self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '订阅：周杰伦官方频道 等 3 条',
                                                                  'data': three})), '订阅：周杰伦官方频道 and others (3 links)')
        # an untitled download job: only its placeholder; a collect job: only '订阅：…'
        with _sandbox.fixture_texts(USER_DATA):
            self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '订阅：X', 'data': {}})),
                             '订阅：X')
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '订阅：X', 'data': {}})),
                         'Subscription: X')
        with _sandbox.fixture_texts(USER_DATA):
            self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '3 条 · 音频 · FLAC',
                                                                  'data': {}})), '3 条 · 音频 · FLAC')
        with _sandbox.language('zh'), _sandbox.fixture_texts(USER_DATA):
            for row in ({'kind': 'download', 'title': '订阅：周杰伦官方频道', 'data': one},
                        {'kind': 'download', 'title': '现场版 等 3 条', 'data': three},
                        {'kind': 'collect', 'title': '订阅：未命名订阅', 'data': {}}):
                self.assertEqual(library_ui.history_title(stored_job(row)), row['title'])

    def test_cr2_3_a_core_error_of_the_collect_page_reads_english(self):
        from collector import CollectOptions
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app()
                shown = i18n.t('core.library.no_job')
                missing = ValueError(msg('core.library.no_job'))      # what Library.job raises (C-F21)
                with mock.patch.object(app.library, 'create_job', side_effect=missing), \
                        mock.patch('collection_ui.messagebox') as dialogs:
                    self.assertFalse(app.launch_collection(CollectOptions(['https://www.example.org/list'])))
                text = dialogs.showwarning.call_args.args[1]
                self.assertEqual(text, i18n.t('dialog.collect.not_started.text', error=shown))
                self.assertIn(i18n.t('log.collect.not_started', error=shown), self.logged(app))
                # the bookkeeping after a collection, and loading its record
                app.collection_job = 'missing-job'
                with mock.patch.object(app.library, 'job', side_effect=missing):
                    app.handle('collect_done', {'cancelled': False})
                self.assertIn(i18n.t('log.collect.record_failed', error=shown), self.logged(app))
                app.collection_job = 'some-job'
                with mock.patch.object(app.library, 'job', return_value={'data': {}, 'pages': []}), \
                        mock.patch.object(app, 'load_collection_record', side_effect=missing):
                    app.handle('collect_done', {'cancelled': False})
                self.assertIn(i18n.t('log.collect.load_failed', error=shown), self.logged(app))
                if lang == 'en':
                    self.assertEqual(shown, 'Task record not found.')
                    self.assertEqual(cjk(text), [], text)
                else:
                    self.assertEqual(shown, '找不到任务记录。')
                self.close(app)

    def test_env2_1_the_note_for_dropped_playlist_parts(self):
        for lang, note in (('en', 'Playlist or mix parts ignored · Links: 1; only the current track is downloaded.'),
                           ('zh', '已忽略 1 条链接里的播放列表/合辑参数，只下载当前这一首。')):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app()
                app.urls.insert('1.0', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ')
                app.links_changed()
                pump(app, timeout=0.1)
                self.assertEqual(ui_theme.full_text(app.link_note), note)
                self.close(app)

# ------------------------------------------------------------------------------ 10.1 widths
# ------------------------------------------------------------------------------ final fix round 3
class FinalFixRound3Tests(AppCase):
    PAGE = 'https://shop.example.org/live'
    REQUEST = 'https://cdn.example.org/hls/master.m3u8'

    def test_cr3_1_a_captured_media_request_reads_english(self):
        """The collector's title of a media request a dynamic collection captured (动态页面媒体请求,
        collector.py parse_page) in the collect table, its filter, the new-items table and the archive.
        (1.8.0, MESSAGES.md 6.4) No key-prefix list is left to compare."""
        for module in (collection_ui, library_ui, library):
            self.assertFalse(hasattr(module, 'PAGE_TITLE_KEYS'), module.__name__)
        for lang, shown in (('en', 'Page request'), ('zh', '动态页面媒体请求')):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app(output=f'i18n-pages-request-{lang}')     # a smoke history of its own
                request = self.REQUEST.replace('master', f'master-{lang}')
                links = [{'url': request, 'title': msg('value.link_kind.dynamic_request'), 'kind': '媒体地址',
                          'source': self.PAGE},
                         {'url': self.PAGE + '/next', 'title': raw('Next'), 'kind': '网页链接', 'source': self.PAGE}]
                # what collector.py emits for such a page in dynamic mode (without its checkpoint, C-F10)
                app.handle('collect_page', {'url': self.PAGE, 'title': raw('Live stream'), 'text': '', 'markdown': '',
                                            'links': links, 'next': None})
                pump(app, timeout=0.2)
                rows = [tuple(app.collect_tree.full_values(iid)) for iid in app.collect_tree.get_children()]
                self.assertEqual(rows, [(i18n.label('link_kind', '媒体地址'), shown, request)])
                for term in (shown, '动态页面媒体请求'):
                    app.collect_search.set(term)
                    app.render_collection_links()
                    self.assertEqual(len(app.collect_tree.get_children()), 1, (lang, term))
                app.collect_search.set('')
                app.render_collection_links()
                options = {'urls': [self.PAGE], 'dynamic': True, 'scrolls': 0, 'selector': '', 'max_pages': 1}
                key = app.library.subscription('Live ' + lang, options, 3600, True, True)
                page = {'url': self.PAGE, 'title': 'Live stream', 'text': '', 'markdown': '', 'links': links[:1]}
                app.library.finish_subscription(key, [page], True)
                app.refresh_library()
                app.subscription_tree.selection_set(key)
                app.subscription_selection()
                pending = [app.pending_tree.full_values(iid)[0] for iid in app.pending_tree.get_children()]
                self.assertEqual(pending, [shown])
                folder = _sandbox.out('i18n-pages-archive', lang, 'request')
                app.library.archive_index(str(folder), {'url': request, 'source': self.PAGE,
                                                        'title': '动态页面媒体请求', 'uploader': '', 'files': [],
                                                        'transcripts': []})
                app.refresh_library()
                titles = [app.archive_tree.full_values(iid)[0] for iid in app.archive_tree.get_children()]
                self.assertIn(shown, titles)
                if lang == 'en':
                    self.assertEqual(cjk(rows), [], rows)
                self.close(app)

    def test_sweep_3_the_archive_files_name_app_made_titles_in_the_running_language(self):
        """网页正文.txt / 阅读说明.txt write the collector's app-made titles in the running language (the
        record keeps the stored title); Chinese writes the stored text, a user title stays as it is."""
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                archive = library.Archive(_sandbox.out('i18n-pages-sweep3', lang))
                page = {'url': self.PAGE, 'title': '网页', 'text': 'Body', 'markdown': 'Body'}
                folder = archive.prepare(self.REQUEST, {'source': self.PAGE, 'title': '动态页面媒体请求', 'page': page})
                text = (folder / '网页正文.txt').read_text(encoding='utf-8-sig')
                readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']), ('动态页面媒体请求', '网页'))
                first = {'en': ('Web page', 'Page request'), 'zh': ('网页', '动态页面媒体请求')}[lang]
                self.assertEqual(text.splitlines()[0], first[0])
                self.assertEqual(readme.splitlines()[0], first[1])
                folder = archive.prepare(self.REQUEST + '?2', {'source': self.PAGE, 'title': 'Live 媒体直链 set'})
                readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
                self.assertEqual(readme.splitlines()[0], 'Live 媒体直链 set', 'a user title is written as it is')

    def test_env3_2_the_subscription_status_line_fits_at_1000x740(self):
        """The English status line above the new-items table (counts + status_note) and the note
        without a selection are shown whole at 1000x740, also with three-digit counts; zh as 1.8.1
        words it.  (1.8.1) with the Ignore button beside Add."""
        self.assertEqual(i18n.CATALOG['zh']['library.subscription.status_note'],
                         '下载成功或忽略后移出；可多选后添加或忽略。')
        self.assertEqual(i18n.CATALOG['zh']['library.subscription.note'],
                         '新增内容会保留，直到下载成功或被忽略；不会自动下载。')
        self.enter(_sandbox.language('en'))
        app = self.make_app(size='1000x740', output='i18n-pages-env3-2')
        app.show_page('library')
        app.library_tabs.set(library_ui.TAB_SUBSCRIPTIONS)
        pump(app, timeout=0.3)
        self.assertTrue(app.pending_ignore.winfo_ismapped(), '(1.8.1) the Ignore button shares the line')
        app.subscription_selection()
        pump(app, timeout=0.2)
        self.assertEqual(ui_theme.full_text(app.subscription_note), i18n.t('library.subscription.note'))
        self.assertEqual(app.subscription_note.cget('text'), i18n.t('library.subscription.note'), 'not cut')
        options = {'urls': [self.PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
        key = app.library.subscription('City Night Talk', options, 3600, True, True)
        links = [{'url': f'{self.PAGE}/item-{n}.mp3', 'title': f'Item {n}', 'kind': '媒体地址', 'source': self.PAGE}
                 for n in range(120)]
        page = {'url': self.PAGE, 'title': 'Live stream', 'text': '', 'markdown': '', 'links': links}
        app.library.finish_subscription(key, [page], True)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        pump(app, timeout=0.3)
        full = ui_theme.full_text(app.subscription_note)
        self.assertEqual(full, i18n.t('library.subscription.status', new=120, pending=120)
                         + i18n.t('library.subscription.status_note'))
        self.assertEqual(app.subscription_note.cget('text'), full, 'not cut')


class WidthTests(AppCase):
    def logical(self, app, pixels):
        return pixels / ctk.ScalingTracker.get_widget_scaling(app)

    def test_the_archive_tools_row_fits_and_keeps_the_entry_at_1000x740(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(size='1000x740')
                app.show_page('library')
                app.library_tabs.set(TABS[2])
                pump(app, timeout=0.3)
                tools = app.archive_search_entry.master
                buttons = [widget for widget in tools.winfo_children() if isinstance(widget, ctk.CTkButton)]
                self.assertEqual(len(buttons), 4)
                right = max(button.winfo_rootx() + button.winfo_width() for button in buttons)
                room = tools.winfo_rootx() + tools.winfo_width()
                self.assertLessEqual(right, room + 1, f'{lang}: the last button ends inside the row')
                tab = app.library_tabs.tab(TABS[2])
                self.assertLessEqual(room, tab.winfo_rootx() + tab.winfo_width() + 1)
                self.assertAlmostEqual(self.logical(app, app.archive_search_entry.winfo_width()), 280, delta=1.5)
                for button in buttons:
                    text = button.cget('text')
                    self.assertGreaterEqual(button.winfo_width() + 1,
                                            ui_theme._measure_font(button, ui_theme.font('body')).measure(text), text)
                self.close(app)

    def uncut(self, tree, column, texts):
        """Every text fits the column as AppTable's cut measures it (no '…')."""
        scale = ui_theme._scaling(tree)
        width_of = ui_theme._width_lookup(ui_theme._measure_font(tree, (ui_theme.FAMILY, ui_theme.TABLE_FONT)))
        room = round(tree._widths[column][0] * scale) - ui_theme.cell_spare(scale)
        return [text for text in texts if ui_theme._cut_cell(text, width_of, room) != text]

    def test_the_type_column_and_the_headings(self):
        # (final fix round 1, ENV-3) widest label + the room AppTable's cut keeps (20 px) + 1, not + 16
        for lang, expected in (('en', 98), ('zh', 117)):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app()
                labels = [text for _value, text in i18n.choices('link_kind')]
                widest = max(library_ui.text_width(app, text) for text in labels)
                width = max(88, widest + 21)
                self.assertAlmostEqual(width, expected, delta=3, msg='I18N.md 10.1')
                self.assertAlmostEqual(app.collect_tree._widths['kind'][0], width, delta=1)
                self.assertEqual(self.uncut(app.collect_tree, 'kind', labels), [], 'no link kind is cut')
                # (ENV-4) the Schedule column of the subscriptions: every interval label and 已暂停 / Paused
                schedule = [i18n.label('interval', seconds) for seconds in library_ui.INTERVALS.values()]
                schedule.append(i18n.t('library.state.paused'))
                self.assertEqual(self.uncut(app.subscription_tree, 'state', schedule), [])
                if lang == 'zh':
                    self.assertEqual(app.subscription_tree._widths['state'][0], 110, 'Chinese keeps 110 px')
                for tree in (app.collect_tree, app.history_tree, app.subscription_tree, app.pending_tree, app.archive_tree):
                    for column in tree['columns']:
                        heading = tree.heading(column, 'text')
                        self.assertLessEqual(library_ui.text_width(app, heading) + 24, tree._widths[column][0], heading)
                self.close(app)

    def test_segments_have_even_physical_widths(self):
        """(final fix round 1, zh-1) an odd segment leaves a one-px line of the bar colour beside the
        rounded end of the last segment (the language row: 'English' grew to 123 px at 200 %)."""
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(size='1000x740')
                app.show_page('settings')
                app.settings_area.canvas.yview_moveto(1.0)
                pump(app, timeout=0.4)
                for bar in (app.appearance_choice, app.language_choice):
                    widths = [button.winfo_width() for button in bar._buttons_dict.values()]
                    self.assertTrue(all(width > 1 and width % 2 == 0 for width in widths), (lang, widths))
                    self.assertEqual(bar.winfo_width(), sum(widths), (lang, 'the bar is its segments'))
                    for button in bar._buttons_dict.values():
                        text = button.cget('text')
                        self.assertGreaterEqual(button.winfo_width() + 1,
                                                ui_theme._measure_font(button, ui_theme.font('body')).measure(text), text)
                self.close(app)

    def test_fixed_buttons_keep_their_1_6_0_width_in_chinese_and_grow_in_english(self):
        widths = {}
        for lang in ('zh', 'en'):
            with _sandbox.language(lang):
                app = self.make_app()
                widths[lang] = {name: getattr(app, name).cget('width') for name in
                                ('collect_stop', 'collect_start', 'collect_add', 'history_resume', 'choose_cookies_button',
                                 'clear_cookies_button')}
                widths[lang]['cookies_entry'] = app.cookies_entry.cget('width')
                if lang == 'zh':
                    self.zh_add_width = library_ui.text_button_width(app.collect_add.master, '添加到下载列表 (9999)')
                self.close(app)
        # (I18N.md 10.1) the add button is measured with the longest count suffix '(9999)' in both
        # languages, so it never changes width; in zh that is wider than the 1.6.0 132 px (recorded).
        self.assertEqual(widths['zh'].pop('collect_add'), max(132, self.zh_add_width))
        self.assertEqual(widths['zh'], {'collect_stop': 64, 'collect_start': 112,
                                        'history_resume': 112, 'choose_cookies_button': 60, 'clear_cookies_button': 60,
                                        'cookies_entry': 376 - 2 * (8 + 60)})
        for name, width in widths['en'].items():
            if name in widths['zh']:
                self.assertGreaterEqual(width, widths['zh'][name], name)
        # the cookies row and the proxy row stay aligned: entry + 2 buttons + gaps = proxy width
        en = widths['en']
        self.assertEqual(en['cookies_entry'] + 2 * (8 + en['choose_cookies_button']),
                         376 + 2 * (en['choose_cookies_button'] - 60))


# ------------------------------------------------------------------------------ English check
class EnglishPagesTests(AppCase):
    def test_no_cjk_on_the_settings_collect_and_library_pages(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app(size='1000x740')
        failures = []
        for page in ('settings', 'collect', 'library'):
            app.show_page(page)
            pump(app, timeout=0.2)
            for tab in (TABS if page == 'library' else (None,)):
                if tab:
                    app.library_tabs.set(tab)
                    pump(app, timeout=0.2)
                failures += [f'{page}/{tab}: {text!r}' for text in texts_of(app.pages[page]) if cjk(text)]
        app.choose_language('zh')
        failures += [f'language strip: {text!r}' for text in texts_of(app.language_strip) if cjk(text)]
        self.assertEqual(failures, [])

    def test_dialogs_and_exports_in_english(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        source = 'https://www.example.org/list'
        links = [{'url': f'https://www.example.org/{n}.mp4', 'title': f'Episode {n}', 'kind': '媒体地址', 'source': source}
                 for n in range(2)]
        app.handle('collect_page', {'url': source, 'title': 'List', 'text': 'Notes', 'markdown': '# Notes', 'links': links,
                                    'text_truncated': True})
        pump(app, timeout=0.2)
        app.collect_tree.selection_set(app.collect_tree.get_children())
        app.collection_selection_changed()
        self.assertEqual(app.collect_add.cget('text'), 'Add to download list (2)')
        target = _sandbox.out('i18n-pages-export', 'page.txt')
        with mock.patch('collection_ui.filedialog') as files:
            files.asksaveasfilename.return_value = str(target)
            app.export_collected_text()
        self.assertEqual(files.asksaveasfilename.call_args.kwargs['initialfile'], 'page-text.txt')
        text = target.read_text(encoding='utf-8-sig')
        self.assertTrue(text.startswith('List\nSource: https://www.example.org/list\nNote: '), text)
        self.assertEqual(cjk(text), [])
        with mock.patch('library_ui.messagebox') as dialogs:
            app.archive_root.set('')
            app.archive_pages()
        self.assertEqual(dialogs.showerror.call_args.args[:2], ('Archiving failed', 'Choose an archive folder first.'))
        with mock.patch('collection_ui.messagebox') as dialogs:
            app.collect_urls.delete('1.0', 'end')
            app.collect_urls.insert('1.0', 'not a link')
            app.start_collection()
        texts = [str(value) for call in dialogs.method_calls for value in call.args]
        self.assertTrue(texts)
        self.assertEqual([text for text in texts if cjk(text)], [], texts)

    def test_chinese_exports_are_the_1_6_0_bytes(self):
        self.enter(_sandbox.language('zh'))
        app = self.make_app()
        pages = [{'url': 'https://www.example.org/list', 'title': '节目列表', 'text': '正文', 'markdown': '# 正文',
                  'links': [], 'text_truncated': True},
                 {'url': 'https://www.example.org/b', 'title': '第二页', 'text': '二', 'markdown': '# 二', 'links': []}]
        app.collected_pages = pages
        for suffix in ('.txt', '.md'):
            target = _sandbox.out('i18n-pages-export', 'zh' + suffix)
            with mock.patch('collection_ui.filedialog') as files:
                files.asksaveasfilename.return_value = str(target)
                app.export_collected_text()
            self.assertEqual(files.asksaveasfilename.call_args.kwargs['initialfile'], '网页正文.txt')
            markdown = suffix == '.md'
            parts = []
            for p in pages:             # 1.6.0 collection_ui.export_collected_text, verbatim
                note = '\n说明：该页正文超出 40 万字符，以下为截断内容。' if p.get('text_truncated') else ''
                parts.append(f'{"# " if markdown else ""}{p["title"]}\n来源：{p["url"]}{note}\n\n{p["markdown"] if markdown else p["text"]}')
            legacy = _sandbox.out('i18n-pages-export', 'legacy' + suffix)
            legacy.write_text('\n\n---\n\n'.join(parts) + '\n', encoding='utf-8-sig')
            self.assertEqual(target.read_bytes(), legacy.read_bytes(), suffix)


if __name__ == '__main__':
    unittest.main()
