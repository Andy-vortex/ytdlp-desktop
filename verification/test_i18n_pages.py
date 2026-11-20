"""1.7.0 language adaptation, WP-L1-B: the settings page, the collect page and the library page (I18N.md 13).

    python -X utf8 -B test_i18n_pages.py

Unit tests of the L1-B work: the language row 语言 / Language with its restart, held and
environment notes (6.2); the restart with an injected launcher, restart-state.json written,
restored and refused (6.3), and that no real program is ever started under the runner; the
choice saved as settings.json 'language' (2.0.0 S2 P3: a known key, no longer through '_extra'); the
labeled interval / collect mode / filter / naming menus keep the stored values, the library tab ids
stay (7); stored texts in the history and subscription notes
(a collect job's error, a '；'-joined last_error, job titles, 未命名订阅, the page-title fallback)
in the running language (9.3); the archive tools row and the Type column widths (10.1); the
engine badge (9.3) and an English check of the three pages.  Windows are never on screen
(alpha 0, no frame); no network (the GitHub check is patched); no real engine.

(1.8.0, MESSAGES.md 8.3) Live events carry the messages the 1.8.0 core emits.  (2.0.0 S2 P2) Stored
rows are seeded in format 2 and read as the library reads them (stored_job / stored_subscription): a
program text is a message, the owner's or the source's text a string shown as it is.
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


from _sandbox import en_t, zh_t

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
ALLOWED = set('【】『』')
TABS = ('history', 'subscriptions', 'archive')        # the tab ids (codes, S3 W3)


def pump(app, condition=None, timeout=5.0):
    return _sandbox.pump(app, condition, timeout, step=0.005)


def cjk(text):
    text = str(text)
    for key in ('settings.language.zh', 'settings.language.title'):
        for allowed in i18n.all_values(key):
            text = text.replace(allowed, '')
    return [char for char in text if CJK.match(char) and char not in ALLOWED]


def stored_job(row):
    """(2.0.0 S2 P2) A history row as Library.history returns it from a format-2 record: the slots typed
    (messages.from_json), the title column the owner's / the media's own text."""
    result = dict(row, data=messages.read_slots(row.get('data') or {}, library.SLOTS['job']))
    if 'title' in row:
        result['title'] = raw(row['title'])
    return result


def stored_subscription(row):
    """(2.0.0 S2 P2) A subscription row as Library.subscriptions returns it: 'name' the name to show
    (the untitled message for ''), 'name_text' the stored text."""
    return dict(row, name=raw(row['name']) or msg('value.subscription.untitled'), name_text=row['name'],
                data=messages.read_slots(row['data'], library.SLOTS['subscription']),
                next_check=row.get('next_check', time.time() + 3600))




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
        elif isinstance(widget, ui_theme.PathButton) and widget.path:
            pass                                        # (2.0.0 S4) a path is user data
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
        """(note keys shown, restart button shown, its state) of the language notice."""
        app.show_page('settings')
        pump(app, timeout=0.2)
        notice = app.language_notice
        shown = {key for key in ('settings.language.restart_note', 'settings.language.restart_busy',
                                 'settings.language.env_fixed', 'settings.language.held')
                 if notice.shown and notice.full_text == i18n.t(key)}
        button = bool(notice.shown and notice.buttons)
        return shown, button, notice.buttons[0].cget('state') if button else None

    def logged(self, app):
        return app.logs.get('1.0', 'end-1c') + '\n' + '\n'.join(app.log_buffer)


# ------------------------------------------------------------------------------ 6.2 the language row
class LanguageRowTests(AppCase):
    def test_the_row_sits_after_appearance_and_shows_the_stored_choice(self):
        self.owner_pc()
        app = self.make_app(smoke=False, language='en')
        self.assertEqual(i18n.language(), 'zh', 'the sandboxed app was built in zh; the stored en applies at a start')
        self.assertEqual(app.language_choice.get(), 'en', 'the stored choice, not the resolved language')
        self.assertEqual(list(app.language_choice.cget('values')), [zh_t('settings.language.system'), '简体中文', 'English'])
        self.assertEqual(self.strip(app), (set(), False, None), 'an untouched row shows no strip')
        # (2.0.0 S4) group 界面 of the one settings grid: the row comes right after 外观, the hidden notice below it
        area = app.settings_area

        def row_of(control):
            while control.master is not area:
                control = control.master
            return int(control.grid_info()['row'])
        language, appearance = row_of(app.language_choice), row_of(app.appearance_choice)
        self.assertEqual(language, appearance + 1)
        self.assertEqual([label.cget('text') for label in area.grid_slaves(row=language, column=0)], ['语言 / Language'])
        app.language_notice.show(i18n.t('settings.language.restart_note'))
        self.assertEqual(int(app.language_notice.grid_info()['row']), language + 1)
        app.language_notice.hide()
        self.assertNotIn(app.language_choice, app.controls, 'the choice may change while busy')

    def test_choosing_stores_the_choice_and_offers_the_restart(self):
        self.owner_pc()
        app = self.make_app(smoke=False)
        self.assertEqual(app.language_choice.get(), 'system')
        app.choose_language('en')
        self.assertEqual(app.prefs['language'], 'en')
        app.save_settings()
        stored = self.stored()
        self.assertEqual(stored['language'], 'en')
        self.assertNotIn('_extra', stored)
        self.assertEqual(set(settings.defaults()) - set(stored), set(), 'every 1.6.0 key is still written')
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        app.operation = 'download'
        app.set_busy(True)
        self.assertEqual(self.strip(app), ({'settings.language.restart_busy'}, False, None))
        app.set_busy(False)
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        for value in ('zh', 'system'):
            app.choose_language(value)
            self.assertEqual(self.strip(app), (set(), False, None), f'{value} resolves to the running zh')
        # a click on a segment hands over the stored value
        app.language_choice._buttons_dict['English'].invoke()
        pump(app, timeout=0.1)
        self.assertEqual(app.prefs['language'], 'en')
        app.choose_language('system')
        app.save_settings()
        self.assertEqual(self.stored()['language'], 'system')

    def test_in_english_the_row_reads_english_first(self):
        self.owner_pc(lang='en')
        app = self.make_app(smoke=False)
        self.assertEqual(list(app.language_choice.cget('values')), [en_t('settings.language.system'), '简体中文', 'English'])
        app.choose_language('zh')
        self.assertEqual(self.strip(app), ({'settings.language.restart_note'}, True, 'normal'))
        self.assertEqual(app.language_notice.buttons[0].cget('text'), en_t('settings.language.restart'))
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
        self.assertEqual(app.prefs['language'], 'en', 'a held session does not keep the change')

    def test_smoke_mode_never_offers_the_restart(self):
        self.owner_pc()
        app = self.make_app(smoke=True)
        app.choose_language('en')
        shown, button, _state = self.strip(app)
        self.assertEqual((shown, button), ({'settings.language.restart_note'}, False))


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
        self.assertEqual(set(state), {'version', 'created', 'pid', 'urls', 'collect_urls'})
        self.assertEqual((state['version'], state['pid']), (1, os.getpid()))
        # the next start restores the two inputs once, logs it and deletes the file
        app = self.make_app(smoke=False)
        self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5))
        pump(app, timeout=0.2)
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://www.example.org/a.mp3\nhttps://www.example.org/b.mp3')
        self.assertEqual(app.collect_urls.get('1.0', 'end-1c'), 'https://www.example.org/podcast/list')
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
               'collect_urls': ''}
        for content in (json.dumps(old), '{broken', json.dumps(dict(old, created=time.time(), version=2)), '[]'):
            self.state_file.write_text(content, encoding='utf-8')
            app = self.make_app(smoke=False)
            self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5), content)
            self.assertEqual(app.urls.get('1.0', 'end-1c'), '', content)
            self.assertNotIn(i18n.t('log.restart_restored'), self.logged(app))

    def test_a_restored_input_never_overwrites_what_is_there(self):
        self.state_file.write_text(json.dumps({'version': 1, 'created': time.time(), 'pid': 1, 'urls': 'https://x.example/a',
                                               'collect_urls': 'https://x.example/list'}),
                                   encoding='utf-8')
        with mock.patch.object(ui_settings.SettingsUI, 'restore_restart_state', lambda self: None):
            app = self.make_app(smoke=False)
        app.collect_urls.insert('1.0', 'https://x.example/typed')
        app.restore_restart_state()
        self.assertFalse(self.state_file.exists())
        self.assertEqual(app.collect_urls.get('1.0', 'end-1c'), 'https://x.example/typed')
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://x.example/a')

    def test_smoke_mode_neither_restarts_nor_restores(self):
        self.state_file.write_text(json.dumps({'version': 1, 'created': time.time(), 'pid': 1, 'urls': 'https://x.example/a',
                                               'collect_urls': ''}), encoding='utf-8')
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
        # (2.0.0 S4 A1) the schedules in settings.SCHEDULES order (manual only last)
        cases = {'subscription_interval': (set(settings.SCHEDULES), [
                     en_t('library.interval.15min'), en_t('library.interval.hourly'), en_t('library.interval.daily'),
                     en_t('library.interval.manual')]),
                 'collect_mode': ({collection_ui.MODE_STATIC, collection_ui.MODE_DYNAMIC},
                                  [en_t('collect.mode.static'), en_t('collect.mode.dynamic')]),
                 'collect_filter': (set(collection_ui.FILTERS), [en_t('collect.filter.media'), en_t('collect.filter.all')]),
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
        self.assertEqual(self.menu_of(app, app.naming)._text_label.cget('text'), i18n.label('naming', 'title_id'))
        app.naming.set('clean')
        self.assertEqual(self.menu_of(app, app.naming)._text_label.cget('text'), i18n.t('naming.clean'))
        # the interval stored for a subscription is the seconds of the chosen label
        self.menu_of(app, app.subscription_interval)._dropdown_callback(en_t('library.interval.daily'))
        self.assertEqual(app.subscription_interval.get(), 86400)
        app.save_settings()
        self.assertEqual(self.stored()['naming'], 'clean')
        self.assertEqual(self.stored()['subscription_interval'], 86400, '(S4 A1) the schedule of a new one is kept')

    def test_appearance_stores_the_mode(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app(smoke=False)
        self.assertEqual(list(app.appearance_choice.cget('values')),
                         [en_t('settings.appearance.light'), en_t('settings.appearance.dark'), en_t('settings.appearance.system')])
        app.appearance_choice._buttons_dict[en_t('settings.appearance.dark')].invoke()
        pump(app, timeout=0.1)
        self.assertEqual(app.prefs['appearance'], 'dark')

    def test_tab_ids_stay_and_the_bar_shows_labels(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        self.assertEqual((library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE), TABS)
        self.assertEqual(app.library_tabs.get(), TABS[0])
        self.assertEqual(list(app.library_tabs._bar.cget('values')),
                         [en_t('library.tab.history'), en_t('library.tab.subscriptions'), en_t('library.tab.archive')])
        app.library_tabs._clicked(en_t('library.tab.archive'))
        self.assertEqual(app.library_tabs.get(), TABS[2])
        app.library_tabs.set(TABS[1])
        self.assertEqual(app.library_tabs._bar.get(), en_t('library.tab.subscriptions'))


# ------------------------------------------------------------------------------ 9.3 stored texts
class StoredTextTests(AppCase):
    def test_history_notes_and_titles(self):
        self.enter(_sandbox.language('en'))
        collect = stored_job({'kind': 'collect', 'pages': [{}, {}], 'data': {'done': [], 'errors': [
            {'error': {'k': 'core.collect.http_status', 'p': {'status': 403}}, 'reason': None}]}})
        note = library_ui.LibraryUI.history_note_text(collect)
        self.assertEqual(note, en_t('library.history.pages', pages=2, done=en_t('library.history.downloaded', completed=0))
                               + en_t('library.history.last_error', error=i18n.t('core.collect.http_status', status=403)[:130]))
        self.assertEqual(cjk(note), [], note)
        download = stored_job({'kind': 'download', 'pages': [], 'data': {'done': [['a', None]], 'errors': [
            {'error': 'ERROR: raw engine line', 'reason': {'k': 'download.reason.proxy'}}]}})
        note = library_ui.LibraryUI.history_note_text(download)
        self.assertEqual(note, en_t('library.history.downloaded', completed=1)
                         + en_t('library.history.last_error', error=i18n.t('download.reason.proxy')))
        # job titles (library.title_parts): the app-made forms localized, user data as it is
        row = stored_job({'kind': 'download', 'title': '', 'data': {'options': {'urls': [
            'https://example.org/a', 'https://example.org/b', 'https://example.org/c']}}})
        self.assertEqual(library_ui.history_title(row), en_t('value.job_title.multi', title='example.org/a', count=3))
        row = stored_job({'kind': 'collect', 'title': '', 'data': {'via': 'subscription'}})
        self.assertEqual(library_ui.history_title(row),
                         en_t('value.job_title.subscription', name=en_t('value.subscription.untitled')))
        row = stored_job({'kind': 'collect', 'title': '检查未完成', 'data': {}})
        self.assertEqual(library_ui.history_title(row), '检查未完成', 'a title equal to a core text stays')
        row = stored_job({'kind': 'download', 'title': 'Song', 'data': {'title_links': 2, 'options': {'urls': [
            'https://example.org/a', 'https://example.org/b']}}})
        self.assertEqual(library_ui.history_title(row), en_t('value.job_title.multi', title='Song', count=2))
        self.assertEqual(library_ui.history_status({'kind': 'download', 'status': 'partial', 'data': {}}),
                         en_t('common.unfinished'))
        self.assertEqual(library_ui.history_status({'kind': 'collect', 'status': 'interrupted', 'data': {}}),
                         en_t('library.state.interrupted'))
        self.assertEqual(library_ui.history_status({'kind': 'collect', 'status': 'odd', 'data': {}}), 'odd')
        cells = library_ui.subscription_row(stored_subscription({'name': '', 'enabled': True, 'interval': 900,
                                                                 'data': {'last_check': 0}, 'pending': 2}))
        self.assertEqual(cells, (en_t('value.subscription.untitled'), en_t('library.interval.15min'),
                                 en_t('library.never_checked'), 2))
        # a name the owner typed is stored as it is; one equal to a core text stays
        cells = library_ui.subscription_row(stored_subscription({'name': '检查未完成', 'enabled': False, 'interval': 900,
                                                                 'data': {'last_check': 0}, 'pending': 0}))
        self.assertEqual(cells[:2], ('检查未完成', en_t('library.state.paused')), 'a name is user data')
        with _sandbox.language('zh'):
            self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '',
                                                                  'data': {'via': 'subscription'}})),
                             zh_t('value.job_title.subscription', name=zh_t('value.subscription.untitled')))
            self.assertEqual(library_ui.LibraryUI.history_note_text(collect),
                             zh_t('library.history.pages', pages=2, done=zh_t('library.history.downloaded', completed=0))
                             + zh_t('library.history.last_error', error=zh_t('core.collect.http_status', status=403)))

    def test_subscription_note_with_a_joined_last_error_and_the_stored_forms(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        options = {'urls': ['https://www.example.org/news'], 'dynamic': False, 'scrolls': 0, 'selector': '',
                   'max_pages': 1, 'proxy': ''}
        # (1.8.0) an empty name is the app's own untitled name (Library.subscription, C-F24)
        key = app.library.subscription('', options)
        self.assertEqual(app.library.subscriptions()[0]['name'], zh_t('value.subscription.untitled'), 'stored in its canonical form')
        # the collect job's record as Library.job returns it: the collector's messages
        record = {'status': 'partial', 'pages': [], 'data': {'errors': [
            msg('core.collect.http_status', status=403), msg('core.collect.too_large')]}}
        pages, successful, error = collection_ui.subscription_result(record, False)
        self.assertEqual(error, i18n.JOIN.join([zh_t('core.collect.http_status', status=403), zh_t('core.collect.too_large')]),
                         'stored in its canonical form')
        self.assertEqual(error.key, 'common.clauses', 'C-F39: one clauses message')
        self.assertEqual(collection_ui.subscription_result({'status': 'completed', 'pages': [], 'data': {'errors': []}},
                                                           True)[2], zh_t('value.subscription.stopped'))
        self.assertEqual(collection_ui.subscription_result({'status': 'partial', 'pages': [], 'data': {'errors': []}},
                                                           False)[2], zh_t('value.subscription.unfinished'))
        app.library.finish_subscription(key, pages, successful, error)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        note = ui_theme.full_text(app.subscription_note)
        sentences = [en_t('core.collect.http_status', status=403), en_t('core.collect.too_large')]
        self.assertTrue(note.endswith(en_t('common.sentence_join').join(sentences)), note)
        self.assertEqual(app.subscription_tree.item(key, 'values')[0], en_t('value.subscription.untitled'))
        self.assertEqual(cjk(note), [], note)
        # an automatic check: the job is the subscription's (its stored name, '' here) and the localized
        # name is shown
        with mock.patch.object(app, 'launch_collection') as launch:
            app.check_subscription(app.library.subscriptions()[0], automatic=True)
        self.assertEqual((launch.call_args.kwargs['title'], launch.call_args.kwargs['via']), ('', 'subscription'))
        self.assertEqual(app.link_note.cget('text'),
                         i18n.t('download.hint.subscription_check', name=en_t('value.subscription.untitled')))
        self.assertIn(en_t('value.subscription.untitled'), ui_theme.full_text(app.collect_note))

    def test_the_collect_page_shows_the_page_title_fallback_and_collector_texts_localized(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        source = 'https://www.example.org/list'
        # what the 1.8.0 collector emits: the fallback title as a message (also as the label of a link
        # without one, C-H-10), a link label as raw user data
        fallback = msg('value.page_title.fallback')
        links = [{'url': 'https://www.example.org/a.mp4', 'title': fallback, 'kind': 'direct', 'source': source},
                 {'url': 'https://www.example.org/b', 'title': raw('网页正文'), 'kind': 'web', 'source': source}]
        app.handle('collect_page', {'url': source, 'title': fallback, 'text': 'x', 'markdown': 'x', 'links': links})
        pump(app, timeout=0.2)
        rows = [app.collect_tree.item(iid, 'values') for iid in app.collect_tree.get_children()]
        self.assertEqual([tuple(row[:2]) for row in rows], [(en_t('value.link_kind.direct'), en_t('value.page_title.fallback'))],
                         'the media filter drops the web link')
        app.collect_filter.set(collection_ui.FILTERS[1])
        rows = [app.collect_tree.item(iid, 'values') for iid in app.collect_tree.get_children()]
        self.assertEqual([tuple(row[:2]) for row in rows], [(en_t('value.link_kind.direct'), en_t('value.page_title.fallback')),
                                                                (en_t('value.link_kind.web'), '网页正文')])
        self.assertIn(en_t('log.collect.page', title=en_t('value.page_title.fallback'), count=2), self.logged(app))
        app.handle('collect_error', msg('core.collect.too_large'))
        self.assertEqual(ui_theme.full_text(app.collect_status),
                         en_t('collect.status.partial', error=i18n.t('core.collect.too_large')))
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
                fallback = msg('value.page_title.fallback')
                app.library.archive_index(str(folder), {'url': source + '/a.m4a', 'source': source, 'title': fallback,
                                                        'uploader': '', 'files': [], 'transcripts': []})
                options = {'urls': [source], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
                key = app.library.subscription('City Night Talk ' + lang, options, 3600, True, True)
                page = {'url': source, 'title': fallback, 'text': '', 'markdown': '',
                        'links': [{'url': source + '/b.m4a', 'title': fallback, 'kind': 'media', 'source': source}]}
                app.library.finish_subscription(key, [page], True)         # one new item (first_new)
                # (2.0.0 S2 P2) the collector's '；'-joined errors as the clauses message they are stored as
                app.library.finish_subscription(key, [], False, error=messages.clauses(
                    [msg('core.collect.http_status', status=503), msg('core.collect.too_large')]))
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
                    self.assertEqual(pending, [en_t('value.page_title.fallback')])
                    sentences = [en_t('core.collect.http_status', status=503), en_t('core.collect.too_large')]
                    self.assertTrue(note.endswith(en_t('common.sentence_join').join(sentences)), note)
                    self.assertEqual(cjk(note), [], note)
                else:
                    self.assertEqual(pending, [zh_t('value.page_title.fallback')])
                    self.assertIn(i18n.JOIN.join([zh_t('core.collect.http_status', status=503), zh_t('core.collect.too_large')]), note)
                self.close(app)

    def test_the_engine_version_and_the_update_notice(self):
        # (2.0.0 S4 A2) The sidebar badges are gone: the version is on the settings page (version_label, and the
        # program version in the About rows) and a newer engine shows the sidebar's 'Update available' link.
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        with mock.patch.object(ui_settings, 'engine_version', return_value=updater.BUILTIN):
            app.prefs = {'latest_engine': '2099.01.01'}
            app.refresh_engine_labels()
        self.assertEqual(app.version_label.cget('text'), f"yt-dlp {en_t('core.update.builtin')}")
        self.assertEqual(app.engine_notice.winfo_manager(), 'grid')
        self.assertEqual(app.engine_notice.cget('text'), en_t('shell.engine_update'))
        about = [shown.cget('text') for _name, shown in app.about_rows]
        self.assertIn(f'v{ui_settings.APP_VERSION}', about)
        with mock.patch.object(ui_settings, 'engine_version', return_value=updater.BUILTIN):
            app.prefs = {'latest_engine': ''}
            app.refresh_engine_labels()
        self.assertEqual(app.engine_notice.winfo_manager(), '')
        app.on_update_error(msg('core.update.no_github', reason=raw('HTTP 403')))
        self.assertEqual(cjk(app.update_status.cget('text')), [])



# ------------------------------------------------------------------------------ final fix round 2
class FinalFixRound2Tests(AppCase):
    MEDIA = 'https://media.example.org/podcast/episode-12.mp3'

    def test_cr2_1_a_direct_media_start_page_reads_english(self):
        """The collector's title of a start page that is a media file (媒体直链, collector.py) in the
        collect table, the 'Page collected' log line, the archive table and the new-items table."""
        for lang, shown in (('en', en_t('value.link_kind.direct')), ('zh', zh_t('value.link_kind.direct'))):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app(output=f'i18n-pages-direct-{lang}')     # a smoke history of its own
                url = self.MEDIA.replace('episode', f'{lang}-episode')
                # what collector.py emits for such a page (without its checkpoint): one message (C-F12)
                direct = msg('value.link_kind.direct')
                app.handle('collect_page', {'url': url, 'title': direct, 'text': '', 'markdown': '',
                                            'links': [{'url': url, 'title': direct, 'kind': 'media',
                                                       'source': url}], 'next': None})
                pump(app, timeout=0.2)
                rows = [tuple(app.collect_tree.full_values(iid)) for iid in app.collect_tree.get_children()]
                self.assertEqual(rows, [(i18n.label('link_kind', 'media'), shown, url)])
                line = i18n.t('log.collect.page', title=shown, count=1)
                self.assertIn(line, self.logged(app))
                app.collect_search.set(shown[:6].lower() if lang == 'en' else shown[:2])
                app.render_collection_links()
                self.assertEqual(len(app.collect_tree.get_children()), 1, 'the shown title is searched')
                app.collect_search.set('')
                app.render_collection_links()
                folder = _sandbox.out('i18n-pages-archive', lang, 'direct')
                app.library.archive_index(str(folder), {'url': url, 'source': url, 'title': direct,
                                                        'uploader': '', 'files': [], 'transcripts': []})
                options = {'urls': [url], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
                key = app.library.subscription('Episode feed ' + lang, options, 3600, True, True)
                page = {'url': url, 'title': direct, 'text': '', 'markdown': '',
                        'links': [{'url': url, 'title': direct, 'kind': 'media', 'source': url}]}
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
        """(2.0.0 S2 P2) The title column holds the media's / the owner's text, shown as it is, whatever it
        reads like; only how the record says the title was made (title_links, via) adds the app's words."""
        self.enter(_sandbox.language('en'))
        one = {'options': {'urls': ['https://example.org/a']}}
        three = {'options': {'urls': ['https://example.org/a', 'https://example.org/b', 'https://example.org/c']},
                 'title_links': 3}
        for title in ('订阅：周杰伦官方频道', '未命名订阅', '3 条 · 现场版', '3条 · 音频 · MP3 320K', '网页', 'City Night Talk'):
            with self.subTest(title=title):
                self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': title, 'data': one})),
                                 title)
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '现场版', 'data': three})),
                         en_t('value.job_title.multi', title='现场版', count=3))
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '订阅：周杰伦官方频道',
                                                              'data': three})),
                         en_t('value.job_title.multi', title='订阅：周杰伦官方频道', count=3))
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'download', 'title': '订阅：X', 'data': {}})),
                         '订阅：X')
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '订阅：X', 'data': {}})),
                         '订阅：X', 'a collect job titled like a subscription check is user data')
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': 'X',
                                                              'data': {'via': 'subscription'}})),
                         en_t('value.job_title.subscription', name='X'))
        self.assertEqual(library_ui.history_title(stored_job({'kind': 'collect', 'title': '3 条 · 音频 · FLAC',
                                                              'data': {}})), '3 条 · 音频 · FLAC')
        with _sandbox.language('zh'):
            for row, shown in (({'kind': 'download', 'title': '订阅：周杰伦官方频道', 'data': one}, '订阅：周杰伦官方频道'),
                               ({'kind': 'download', 'title': '现场版', 'data': three}, zh_t('value.job_title.multi', title='现场版', count=3)),
                               ({'kind': 'collect', 'title': '', 'data': {'via': 'subscription'}},
                                zh_t('value.job_title.subscription', name=zh_t('value.subscription.untitled')))):
                self.assertEqual(library_ui.history_title(stored_job(row)), shown)

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
                    self.assertEqual(cjk(shown), [], shown)          # (S3 W4, R6) it reads English
                    self.assertEqual(cjk(text), [], text)
                else:
                    self.assertTrue(cjk(shown), shown)              # (S3 W4, R6) it reads Chinese
                self.close(app)

    def test_env2_1_the_note_for_dropped_playlist_parts(self):
        for lang, note in (('en', en_t('download.note.lists_dropped', count=1)),
                           ('zh', zh_t('download.note.lists_dropped', count=1))):
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
        for lang, shown in (('en', en_t('value.link_kind.dynamic_request')), ('zh', zh_t('value.link_kind.dynamic_request'))):
            with self.subTest(lang=lang):
                self.enter(_sandbox.language(lang))
                app = self.make_app(output=f'i18n-pages-request-{lang}')     # a smoke history of its own
                request = self.REQUEST.replace('master', f'master-{lang}')
                links = [{'url': request, 'title': msg('value.link_kind.dynamic_request'), 'kind': 'media',
                          'source': self.PAGE},
                         {'url': self.PAGE + '/next', 'title': raw('Next'), 'kind': 'web', 'source': self.PAGE}]
                # what collector.py emits for such a page in dynamic mode (without its checkpoint, C-F10)
                app.handle('collect_page', {'url': self.PAGE, 'title': raw('Live stream'), 'text': '', 'markdown': '',
                                            'links': links, 'next': None})
                pump(app, timeout=0.2)
                rows = [tuple(app.collect_tree.full_values(iid)) for iid in app.collect_tree.get_children()]
                self.assertEqual(rows, [(i18n.label('link_kind', 'media'), shown, request)])
                for term in (shown, zh_t('value.link_kind.dynamic_request')):
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
                                                        'title': msg('value.link_kind.dynamic_request'), 'uploader': '',
                                                        'files': [], 'transcripts': []})
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
                # (2.0.0 S2 P2) the collector's titles are messages (a plain string is the source's own text)
                page = {'url': self.PAGE, 'title': msg('value.page_title.fallback'), 'text': 'Body', 'markdown': 'Body'}
                folder = archive.prepare(self.REQUEST, {'source': self.PAGE, 'title': msg('value.link_kind.dynamic_request'),
                                                        'page': page})
                text = (folder / '网页正文.txt').read_text(encoding='utf-8-sig')
                readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']),
                                 ({'k': 'value.link_kind.dynamic_request'}, {'k': 'value.page_title.fallback'}))
                first = {'en': (en_t('value.page_title.fallback'), en_t('value.link_kind.dynamic_request')),
                         'zh': (zh_t('value.page_title.fallback'), zh_t('value.link_kind.dynamic_request'))}[lang]
                self.assertEqual(text.splitlines()[0], first[0])
                self.assertEqual(readme.splitlines()[0], first[1])
                folder = archive.prepare(self.REQUEST + '?2', {'source': self.PAGE, 'title': 'Live 媒体直链 set'})
                readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
                self.assertEqual(readme.splitlines()[0], 'Live 媒体直链 set', 'a user title is written as it is')

    def test_env3_2_the_subscription_status_line_fits_at_1000x740(self):
        """The English count above the new-items table and its two buttons are shown whole at 1000x740, also
        with three-digit counts.  (2.0.0 S4) The note under the subscriptions says only the last error; the count
        sits on the line of 加入下载 and 忽略 (subscriptions.md 2.4)."""
        self.enter(_sandbox.language('en'))
        app = self.make_app(size='1000x740', output='i18n-pages-env3-2')
        app.show_page('library')
        app.library_tabs.set(library_ui.TAB_SUBSCRIPTIONS)
        pump(app, timeout=0.3)
        options = {'urls': [self.PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
        key = app.library.subscription('City Night Talk', options, 3600, True, True)
        links = [{'url': f'{self.PAGE}/item-{n}.mp3', 'title': f'Item {n}', 'kind': 'media', 'source': self.PAGE}
                 for n in range(120)]
        page = {'url': self.PAGE, 'title': 'Live stream', 'text': '', 'markdown': '', 'links': links}
        app.library.finish_subscription(key, [page], True)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        pump(app, timeout=0.3)
        self.assertEqual(ui_theme.full_text(app.subscription_note), '', 'no error, no note')
        self.assertEqual(ui_theme.full_text(app.pending_count), i18n.t('library.pending.count', count=120))
        self.assertEqual(app.pending_count.cget('text'), i18n.t('library.pending.count', count=120), 'not cut')
        row = app.pending_count.master
        for widget in (app.pending_count, app.pending_queue, app.pending_ignore):
            self.assertTrue(widget.winfo_ismapped(), widget)
            self.assertIs(widget.master, row, 'one line')
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), row.winfo_width() + 1, widget)


class WidthTests(AppCase):
    def logical(self, app, pixels):
        return pixels / ctk.ScalingTracker.get_widget_scaling(app)

    def test_the_archive_tools_row_fits_and_keeps_the_entry_at_1000x740(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(size='1000x740', output=f'i18n-pages-archive-tools-{lang}')
                app.library.archive_index(str(_sandbox.out('i18n-pages-archive-tools', lang)),
                                          {'url': 'https://www.example.org/a.m4a', 'source': 'https://www.example.org/list', 'title': 'A', 'uploader': '',
                                           'files': ['a.m4a'], 'transcripts': []})
                app.refresh_library()
                app.show_page('library')
                app.library_tabs.set(TABS[2])
                pump(app, timeout=0.3)
                tools = app.archive_search_entry.master
                buttons = [widget for widget in tools.winfo_children() if isinstance(widget, ctk.CTkButton)]
                self.assertEqual(set(buttons), {app.archive_open, app.archive_menu})
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
        for lang, expected in (('en', 98), ('zh', 72)):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app()
                labels = [text for _value, text in i18n.choices('link_kind')]
                widest = max(library_ui.text_width(app, text) for text in labels)
                width = max(72, widest + 21)          # (S4 B2) floor 72: the kinds are short words now
                self.assertAlmostEqual(width, expected, delta=3, msg='I18N.md 10.1')
                self.assertAlmostEqual(app.collect_tree._widths['kind'][0], width, delta=1)
                self.assertEqual(self.uncut(app.collect_tree, 'kind', labels), [], 'no link kind is cut')
                # (ENV-4) the Schedule column of the subscriptions: every interval label and every state word
                schedule = [i18n.label('interval', seconds) for seconds in settings.SCHEDULES]
                schedule += [i18n.t('library.state.paused'), i18n.t('library.state.checking'), i18n.t('library.state.due')]
                self.assertEqual(self.uncut(app.subscription_tree, 'state', schedule), [])
                self.assertGreaterEqual(app.subscription_tree._widths['state'][0], 96)
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

    def test_buttons_fit_their_text_in_both_languages(self):
        """(2.0.0 S4, 00-system 2.3) A button is as wide as its text needs (fitted_width), never a fixed 1.6.0 width:
        开始采集 / 停止 share one cell and one width; 加入下载 is measured with the longest count; the cookies row (path
        button + 清除) is as wide as a path button."""
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app()
                for name in ('collect_start', 'collect_stop', 'history_resume', 'clear_cookies_button'):
                    button = getattr(app, name)
                    self.assertGreaterEqual(button.cget('width'), ui_theme.fitted_width(button.master, button.cget('text')),
                                            name)
                self.assertEqual(app.collect_start.cget('width'), app.collect_stop.cget('width'), 'one cell, one width')
                add = app.collect_add
                self.assertGreaterEqual(add.cget('width'), ui_theme.fitted_width(add.master, f"{i18n.t('collect.add')} (9999)"))
                self.assertEqual(app.cookies_button.cget('width') + ui_theme.INLINE + app.clear_cookies_button.cget('width'),
                                 ui_theme.PATH_W)
                self.close(app)


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
        failures += [f'language notice: {text!r}' for text in texts_of(app.language_notice) if cjk(text)]
        self.assertEqual(failures, [])

    def test_dialogs_and_exports_in_english(self):
        self.enter(_sandbox.language('en'))
        app = self.make_app()
        source = 'https://www.example.org/list'
        links = [{'url': f'https://www.example.org/{n}.mp4', 'title': f'Episode {n}', 'kind': 'media', 'source': source}
                 for n in range(2)]
        app.handle('collect_page', {'url': source, 'title': 'List', 'text': 'Notes', 'markdown': '# Notes', 'links': links,
                                    'text_truncated': True})
        pump(app, timeout=0.2)
        app.collect_tree.selection_set(app.collect_tree.get_children())
        app.collection_selection_changed()
        self.assertEqual(app.collect_add.cget('text'), f"{en_t('collect.add')} (2)")
        target = _sandbox.out('i18n-pages-export', 'page.txt')
        with mock.patch('collection_ui.filedialog') as files:
            files.asksaveasfilename.return_value = str(target)
            app.export_collected_text()
        self.assertEqual(files.asksaveasfilename.call_args.kwargs['initialfile'], en_t('dialog.export_text.file'))
        text = target.read_text(encoding='utf-8-sig')
        self.assertTrue(text.startswith(en_t('filetext.export_page', heading='', title='List', url=source,
                                              note=en_t('filetext.truncated_export'), body='Notes')), text)
        self.assertEqual(cjk(text), [])
        with mock.patch('library_ui.messagebox') as dialogs:
            app.archive_root.set('')
            app.archive_pages()
        self.assertEqual(dialogs.showerror.call_args.args[:2],
                         (en_t('dialog.archive_failed'), en_t('error.archive_root_missing')))
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
            self.assertEqual(files.asksaveasfilename.call_args.kwargs['initialfile'], zh_t('dialog.export_text.file'))
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
