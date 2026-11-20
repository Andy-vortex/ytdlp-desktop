"""The settings page of 2.0.0 (S4 B5, docs/2.0.0/handoff/S4/settings.md): one grid of six groups, the engine row,
the moved archive and new-subscription controls, the cookies path button, the language notice.

    python -X utf8 -B test_page_settings.py

On a real (invisible) window:
  - the groups are engine / downloads / subscriptions / network / interface / about, in this order, one muted
    label each; the grid is never wider than ui_theme.SETTINGS_MAX while the scroll area fills the content area
    (its scrollbar on the right edge, as on the other pages); the two drop-downs share one width;
  - every action of settings.md section 4 has a visible control that calls it (check for updates, stop, restore,
    the cookies path button and 清除, the archive folder button, 立即重启 in the language notice);
  - 检查更新 is the page's only solid button; an engine update swaps it for 停止 in the same cell; 恢复内置引擎 is
    shown only while an updated engine is in use and usable only when idle;
  - 清除 is grey while no cookies file is chosen or while busy; the archive folder button follows the busy lock
    only, whether 统一归档 is on or off;
  - the three new-subscription controls edit the persisted studio variables (settings.json keeps them);
  - show_settings(group) shows the page with that group's controls in the viewport;
  - a newer engine known from an earlier check is said at start (status line and the sidebar link), an empty
    status line takes no room;
  - the language notice sits right below the language row: 重启后生效 with 立即重启 when idle, 任务结束后可重启
    without a button while busy;
  - a restart keeps only the two link boxes (the subscription name field is gone, M5);
  - in English no label, button or menu of the page shows Chinese except the language names.
"""
import _sandbox
import json
import re
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import i18n
import settings
import ui_settings
import ui_theme
import updater
from studio import StudioApp
from _sandbox import no_network, no_clipboard

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')


def state(widget):
    return str(widget.cget('state'))


def shown(widget):
    return bool(widget.winfo_manager())


def settle(app, rounds=3):
    for _ in range(rounds):
        app.update_idletasks()
        app.update()


def descendants(widget):
    todo = [widget]
    while todo:
        widget = todo.pop()
        todo.extend(widget.winfo_children())
        yield widget


# (final review, honesty 1) Each row label and the control beside it: label key -> (control, its variable or None).
# The cookies row's control is the frame that holds the path button and 清除.
ROW_PAIRS = (
    ('settings.engine.daily.title', 'daily_switch', 'check_engine_updates'),
    ('settings.naming.title', 'naming_menu', None),
    ('settings.archive.title', 'archive_switch', 'archive_enabled'),
    ('settings.archive.folder', 'archive_folder_button', None),
    ('settings.tags.title', 'tags_switch', 'embed_metadata'),
    ('settings.cover.title', 'cover_switch', 'embed_cover'),
    ('settings.skip.title', 'skip_switch', 'skip_downloaded'),
    ('settings.steady.title', 'batch_switch', 'batch_safe'),
    ('settings.subscription.media_only', 'subscription_media_switch', 'subscription_media_only'),
    ('settings.subscription.first_new', 'subscription_first_switch', 'subscription_first_new'),
    ('settings.subscription.schedule', 'subscription_interval_menu', None),
    ('settings.proxy.title', 'proxy_entry', None),
    ('settings.cookies.title', 'cookies_button', None),
    ('settings.notify.title', 'notify_switch', 'notify_done'),
    ('settings.clipboard.title', 'clipboard_switch', 'clipboard_hint'),
    ('settings.appearance.title', 'appearance_choice', None),
    ('settings.language.title', 'language_choice', None),
)


def check_row_pairs(test, app, text_of):
    """Every row label of the settings grid sits on the grid row of the control ROW_PAIRS gives it, in that order;
    a switch is bound to its settings variable.  ``text_of`` renders a key (zh_t or en_t)."""
    area = app.settings_area
    rows = {}
    for widget in area.grid_slaves(column=0):
        info = widget.grid_info()
        if isinstance(widget, ctk.CTkLabel) and int(info.get('columnspan', 1)) == 1:
            rows[int(info['row'])] = widget
    pairs = []
    for row in sorted(rows):
        beside = area.grid_slaves(row=row, column=1)
        if beside:
            pairs.append((rows[row].cget('text'), beside[0]))
    about = [title.cget('text') for title, _ in app.about_rows]
    pairs = [(text, control) for text, control in pairs if text not in about]
    test.assertEqual([text for text, _ in pairs], [text_of(key) for key, _, _ in ROW_PAIRS])
    for (text, control), (key, name, variable) in zip(pairs, ROW_PAIRS):
        expected = getattr(app, name)
        if name == 'cookies_button':
            expected = expected.master
        test.assertIs(control, expected, key)
        if variable is not None:
            test.assertIs(control.cget('variable'), getattr(app, variable), key)


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard)):
            patcher.start()
            self.addCleanup(patcher.stop)
        settings_file = _sandbox.state_dir() / 'settings.json'
        if settings_file.exists():
            settings_file.unlink()

    def make_app(self, **values):
        _sandbox.write_settings(**values)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args())
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.geometry('1000x740+0+0')
        self.addCleanup(self.close, app)
        settle(app)
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    def in_view(self, app, widget):
        """True when ``widget`` (a child of settings_area, or inside one) lies inside the scroll viewport."""
        area = app.settings_area
        top = widget.winfo_rooty() - area.winfo_rooty()
        view_top = area.canvas.canvasy(0)
        return view_top - 1 <= top and top + widget.winfo_height() <= view_top + area.canvas.winfo_height() + 1

    # ------------------------------------------------------------------ layout
    def test_six_groups_in_order_and_the_page_cap(self):
        app = self.make_app()
        app.show_page('settings')
        settle(app)
        self.assertEqual(list(app.settings_groups), list(ui_settings.GROUPS))
        self.assertEqual(ui_settings.GROUPS, ('engine', 'downloads', 'subscriptions', 'network', 'interface', 'about'))
        ys = [label.winfo_y() for label in app.settings_groups.values()]
        self.assertEqual(ys, sorted(ys))
        for name, label in app.settings_groups.items():
            self.assertIs(label.master, app.settings_area, name)
            self.assertEqual(label.cget('text'), i18n.t(f'settings.group.{name}'))
            self.assertEqual(label.cget('text_color'), ui_theme.MUTED)
        self.assertIsNone(app.page_caps['settings'], 'the page is not capped: the scroll area fills the content area')
        scale = ctk.ScalingTracker.get_widget_scaling(app)
        area = app.settings_area
        grid_right = max(w.winfo_x() + w.winfo_width() for w in area.winfo_children() if w.winfo_ismapped())
        self.assertLessEqual(grid_right / scale, ui_theme.SETTINGS_MAX + 1, 'the grid is capped')
        self.assertGreaterEqual(grid_right / scale, ui_theme.SETTINGS_MAX - 1, 'the grid uses the cap')
        content_right = app.content.winfo_rootx() + app.content.winfo_width()
        outer = area._parent_frame
        self.assertLessEqual(abs(outer.winfo_rootx() + outer.winfo_width() - content_right), 1,
                             'the scroll area (and its scrollbar) reaches the right edge of the content area')
        self.assertTrue(area.bar_visible)
        bar = area._scrollbar
        self.assertLessEqual(abs(bar.winfo_rootx() + bar.winfo_width() - content_right), round(3 * scale),
                             'scrollbar on the edge, less the stock inset')
        self.assertEqual(app.naming_menu.cget('width'), app.subscription_interval_menu.cget('width'), 'one menu width')
        # no cards: nothing in the area has a border
        for widget in descendants(app.settings_area):
            if type(widget) is ctk.CTkFrame:
                self.assertEqual(widget.cget('border_width'), 0, widget)
        # the rows' controls line up on the right
        rights = {round(w.winfo_x() + w.winfo_width()) for w in (app.naming_menu, app.archive_switch,
                                                                   app.archive_folder_button, app.proxy_entry,
                                                                   app.subscription_interval_menu, app.language_choice)}
        self.assertEqual(len(rights), 1, rights)
        # the path-like fields share one left edge (PATH_W)
        lefts = {app.archive_folder_button.winfo_rootx(), app.proxy_entry.winfo_rootx(), app.cookies_button.winfo_rootx()}
        self.assertEqual(len(lefts), 1, lefts)

    def test_each_row_label_sits_beside_its_control(self):
        check_row_pairs(self, self.make_app(), _sandbox.zh_t)

    def test_the_old_controls_are_gone(self):
        app = self.make_app()
        for name in ('settings_cards', 'cookies_entry', 'choose_cookies_button', 'cancel_update_button',
                     'language_strip', 'language_note', 'language_restart'):
            self.assertFalse(hasattr(app, name), name)
        for name in ('settings_card', 'settings_rows', '_show_cookie_tail', 'show_language_strip'):
            self.assertFalse(hasattr(ui_settings.SettingsUI, name), name)
        self.assertFalse(hasattr(ui_settings, 'COOKIE_ROW_W'))
        self.assertNotIn('subscription_name', ui_settings.RESTART_STATE_KEYS)

    # ------------------------------------------------------------------ actions and their controls
    def test_every_action_has_a_visible_control(self):
        app = self.make_app()
        app.engine_updated = True
        app.cookies.set(str(_sandbox.out('cookies.txt')))
        app.refresh_settings_controls()
        for widget, method in ((app.check_update_button, app.update_engine), (app.restore_button, app.restore_engine),
                               (app.cookies_button, app.choose_cookies), (app.clear_cookies_button, app.clear_cookies),
                               (app.archive_folder_button, app.choose_archive), (app.stop_update_button, app.stop)):
            self.assertEqual(widget._command, method, widget)
        for widget in (app.check_update_button, app.restore_button, app.cookies_button, app.clear_cookies_button,
                       app.archive_folder_button):
            self.assertTrue(shown(widget), widget)
            self.assertEqual(state(widget), 'normal', widget)
        variables = {app.daily_switch: app.check_engine_updates, app.archive_switch: app.archive_enabled,
                     app.tags_switch: app.embed_metadata, app.cover_switch: app.embed_cover,
                     app.skip_switch: app.skip_downloaded, app.batch_switch: app.batch_safe,
                     app.subscription_media_switch: app.subscription_media_only,
                     app.subscription_first_switch: app.subscription_first_new,
                     app.notify_switch: app.notify_done, app.clipboard_switch: app.clipboard_hint}
        for widget, variable in variables.items():
            self.assertIs(widget.cget('variable'), variable, widget)
        self.assertIs(app.naming_menu.cget('variable'), app.naming)
        self.assertIs(app.subscription_interval_menu.cget('variable'), app.subscription_interval)
        self.assertEqual(app.proxy_entry.cget('textvariable'), app.proxy)
        self.assertEqual(app.archive_switch._command, app.save_settings)

    def test_check_for_updates_is_the_only_solid_button_and_swaps_with_stop(self):
        app = self.make_app()
        app.engine_updated = True
        app.refresh_settings_controls()
        settle(app)

        def solid():
            return [w for w in descendants(app.pages['settings'])
                    if isinstance(w, ui_theme.AppButton) and w.kind == 'primary' and shown(w)]
        self.assertEqual(solid(), [app.check_update_button])
        self.assertEqual(app.stop_update_button.kind, 'danger')
        cell = {key: app.check_update_button.grid_info()[key] for key in ('row', 'column')}
        self.assertEqual(app.check_update_button.cget('width'), app.stop_update_button.cget('width'))
        for operation in ('download', 'collect', 'update'):
            with self.subTest(operation=operation):
                app.operation = operation
                app.worker = object()
                app.set_busy(True)
                updating = operation == 'update'
                if updating:      # the same cell (grid_info is empty for a removed widget)
                    self.assertEqual({key: app.stop_update_button.grid_info()[key] for key in cell}, cell)
                self.assertEqual(shown(app.stop_update_button), updating)
                self.assertEqual(shown(app.check_update_button), not updating)
                self.assertEqual(state(app.stop_update_button), 'normal')
                self.assertEqual(state(app.check_update_button), 'disabled')
                self.assertTrue(shown(app.restore_button))
                self.assertEqual(state(app.restore_button), 'disabled')
                self.assertLessEqual(len(solid()), 1)
                app.set_busy(False)
                self.assertTrue(shown(app.check_update_button))
                self.assertFalse(shown(app.stop_update_button))
                self.assertEqual(state(app.check_update_button), 'normal')
                self.assertEqual(state(app.restore_button), 'normal')

    def test_restore_is_shown_only_with_an_updated_engine(self):
        app = self.make_app()
        with mock.patch.object(ui_settings, 'updated_engine_active', return_value=False):
            app.refresh_engine_labels()
        self.assertFalse(app.engine_updated)
        self.assertFalse(shown(app.restore_button))
        with mock.patch.object(ui_settings, 'updated_engine_active', return_value=True):
            app.refresh_engine_labels()
        self.assertTrue(shown(app.restore_button))
        self.assertEqual(state(app.restore_button), 'normal')

    def test_clear_cookies_follows_the_file_and_the_busy_lock(self):
        app = self.make_app()
        self.assertEqual(app.cookies.get(), '')
        self.assertEqual(state(app.clear_cookies_button), 'disabled')
        self.assertEqual(app.cookies_button.path, '')
        self.assertEqual(app.cookies_button.cget('text'), i18n.t('common.choose_ellipsis'))
        path = str(_sandbox.out('cookies', 'youtube-cookies.txt'))
        app.cookies.set(path)
        self.assertEqual(state(app.clear_cookies_button), 'normal')
        self.assertEqual(app.cookies_button.path, path)
        app.operation = 'download'
        app.worker = object()
        app.set_busy(True)
        self.assertEqual(state(app.clear_cookies_button), 'disabled')
        self.assertEqual(state(app.cookies_button), 'disabled')
        app.set_busy(False)
        self.assertEqual(state(app.clear_cookies_button), 'normal')
        app.clear_cookies_button.invoke()
        self.assertEqual(app.cookies.get(), '')
        self.assertEqual(state(app.clear_cookies_button), 'disabled')
        self.assertEqual(app.cookies_button.cget('text'), i18n.t('common.choose_ellipsis'))
        stored = json.loads((_sandbox.state_dir() / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['cookies'], '')

    def test_choosing_a_cookies_file_through_the_path_button(self):
        app = self.make_app()
        path = _sandbox.out('cookies', 'chosen.txt')
        with mock.patch.object(ui_settings.filedialog, 'askopenfilename', return_value=str(path)), \
                mock.patch.object(ui_settings, 'check_cookie_file') as check, \
                mock.patch.object(app, 'cancel_bot_retry') as cancel:
            app.cookies_button.invoke()
        check.assert_called_once_with(str(path))
        cancel.assert_called_once()
        self.assertEqual(app.cookies_button.path, str(path))
        self.assertEqual(state(app.clear_cookies_button), 'normal')
        # a file that fails the check is not taken
        with mock.patch.object(ui_settings.filedialog, 'askopenfilename', return_value=str(path) + '.bad'), \
                mock.patch.object(ui_settings, 'check_cookie_file', side_effect=ValueError('bad')), \
                mock.patch.object(ui_settings.messagebox, 'showwarning') as warning:
            app.cookies_button.invoke()
        warning.assert_called_once()
        self.assertEqual(app.cookies_button.path, str(path))

    def test_the_archive_folder_follows_the_busy_lock_only(self):
        app = self.make_app()
        for enabled in (False, True):
            with self.subTest(archive_enabled=enabled):
                app.archive_enabled.set(enabled)
                app.set_busy(False)
                self.assertEqual(state(app.archive_folder_button), 'normal')
                self.assertEqual(state(app.archive_switch), 'normal')
                app.operation = 'collect'
                app.worker = object()
                app.set_busy(True)
                self.assertEqual(state(app.archive_folder_button), 'disabled')
                self.assertEqual(state(app.archive_switch), 'disabled')
                app.set_busy(False)
        folder = str(_sandbox.out('archive here'))
        app.archive_root.set(folder)
        self.assertEqual(app.archive_folder_button.path, folder)
        with mock.patch('library_ui.filedialog.askdirectory', return_value=str(_sandbox.out('picked'))):
            app.archive_folder_button.invoke()
        self.assertEqual(app.archive_root.get(), str(_sandbox.out('picked')))
        self.assertEqual(app.archive_folder_button.path, str(_sandbox.out('picked')))

    def test_the_new_subscription_defaults_are_stored(self):
        app = self.make_app(subscription_interval=86400, subscription_media_only=False)
        self.assertEqual(app.subscription_interval_menu.get(), 86400)
        self.assertEqual(app.subscription_interval_menu.stored_values, list(settings.SCHEDULES))
        self.assertEqual(app.subscription_interval_menu.cget('values'),
                         [i18n.label('interval', seconds) for seconds in settings.SCHEDULES])
        self.assertFalse(app.subscription_media_switch.get())
        self.assertTrue(app.subscription_first_switch.get())
        app.subscription_interval_menu._dropdown_callback(i18n.label('interval', 900))
        self.assertEqual(app.subscription_interval.get(), 900)
        app.subscription_first_switch.toggle()
        app.save_settings()
        stored = json.loads((_sandbox.state_dir() / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual((stored['subscription_interval'], stored['subscription_media_only'],
                          stored['subscription_first_new']), (900, False, False))

    # ------------------------------------------------------------------ show_settings, status line
    def test_show_settings_scrolls_the_group_into_view(self):
        app = self.make_app()
        app.show_page('download')
        settle(app)
        app.settings_area.canvas.yview_moveto(1.0)
        settle(app)
        app.show_settings('engine')
        settle(app)
        self.assertEqual(app.current_page, 'settings')
        self.assertEqual(app.settings_area.canvas.yview()[0], 0.0)
        self.assertTrue(self.in_view(app, app.check_update_button.master))
        for group, widget in (('downloads', app.archive_folder_button), ('network', app.cookies_button),
                              ('about', app.about_rows[-1][1]), ('interface', app.language_choice)):
            with self.subTest(group=group):
                app.show_page('download')
                app.settings_area.canvas.yview_moveto(0.0)
                app.show_settings(group)
                settle(app)
                self.assertTrue(self.in_view(app, app.settings_groups[group]), group)
                self.assertTrue(self.in_view(app, widget), group)
        self.assertTrue(app.settings_area.bar_visible)
        app.show_settings('nothing')                # an unknown group only shows the page
        self.assertEqual(app.current_page, 'settings')

    def test_a_newer_engine_known_at_start_is_said(self):
        app = self.make_app(latest_engine='2999.01.01')
        self.assertEqual(app.update_status.cget('text'), i18n.t('settings.update.available', latest='2999.01.01'))
        self.assertTrue(shown(app.update_status))
        self.assertTrue(shown(app.engine_notice))
        app.engine_notice.invoke()
        settle(app)
        self.assertEqual(app.current_page, 'settings')
        self.assertEqual(app.settings_area.canvas.yview()[0], 0.0)

    def test_an_empty_status_line_takes_no_room(self):
        app = self.make_app()
        self.assertEqual(app.update_status.cget('text'), '')
        self.assertFalse(shown(app.update_status))
        self.assertTrue(app.version_label.cget('text').startswith('yt-dlp '))
        app.busy, app.operation = True, 'update'
        app.on_update_error('HTTP Error 404')
        self.assertEqual(app.update_status.cget('text'), i18n.t('settings.update.failed', error='HTTP Error 404'))
        self.assertTrue(shown(app.update_status))
        self.assertEqual(app.update_status.cget('text_color'), ui_theme.WARN)
        app.busy, app.operation = False, None

    # ------------------------------------------------------------------ language notice
    def test_the_language_notice_below_the_language_row(self):
        app = self.make_app()
        self.assertFalse(app.smoke_mode)
        self.assertFalse(app.language_notice.shown)
        # the test runner fixes the language with YTDLP_DESKTOP_LANG; a normal start has no such variable
        with mock.patch.object(i18n, 'env_override', return_value=None):
            self.check_language_notice(app)
        app.language_saved, app.language_notice_kind = True, None
        app.refresh_language_note()
        self.assertEqual(app.language_notice.full_text, i18n.t('settings.language.env_fixed'))
        self.assertEqual(app.language_notice.buttons, ())

    def check_language_notice(self, app):
        language_row = app.language_choice.grid_info()['row']
        other = 'en' if i18n.language() == 'zh' else 'zh'
        with mock.patch.object(app, 'schedule_save'):
            app.choose_language(other)
        self.assertTrue(app.language_notice.shown)
        self.assertEqual(app.language_notice.grid_info()['row'], language_row + 1)      # right below the row
        self.assertEqual(app.language_notice.full_text, i18n.t('settings.language.restart_note'))
        self.assertEqual(len(app.language_notice.buttons), 1)
        button = app.language_notice.buttons[0]
        self.assertEqual(button.cget('text'), i18n.t('settings.language.restart'))
        self.assertEqual(button.kind, 'ghost')
        self.assertEqual(button._command, app.restart_app)
        for operation in ('download', 'collect', 'update'):
            with self.subTest(operation=operation):
                app.operation = operation
                app.worker = object()
                app.set_busy(True)
                self.assertEqual(app.language_notice.full_text, i18n.t('settings.language.restart_busy'))
                self.assertEqual(app.language_notice.buttons, ())
                app.set_busy(False)
                self.assertEqual(app.language_notice.full_text, i18n.t('settings.language.restart_note'))
                self.assertEqual(len(app.language_notice.buttons), 1)
                self.assertEqual(state(app.language_notice.buttons[0]), 'normal')
        with mock.patch.object(app, 'schedule_save'):
            app.choose_language(i18n.language())
        self.assertFalse(app.language_notice.shown)
        app.language_saved = False
        app.refresh_language_note()
        self.assertEqual(app.language_notice.full_text, i18n.t('settings.language.held'))
        self.assertEqual(app.language_notice.buttons, ())

    # ------------------------------------------------------------------ restart keeps the two link boxes
    def test_a_restart_keeps_only_the_link_boxes(self):
        app = self.make_app()
        app.urls.insert('1.0', 'https://a.example.invalid/1')
        app.collect_urls.insert('1.0', 'https://b.example.invalid/')
        self.assertEqual(app.restart_inputs(), {'urls': 'https://a.example.invalid/1',
                                                'collect_urls': 'https://b.example.invalid/'})
        app.urls.delete('1.0', 'end')
        app.collect_urls.delete('1.0', 'end')
        path = ui_settings.restart_state_file()
        path.write_text(json.dumps({'version': 1, 'created': time.time(), 'urls': 'https://c.example.invalid/',
                                    'collect_urls': 'https://d.example.invalid/', 'subscription_name': 'kept?'}),
                        encoding='utf-8')
        with mock.patch.object(app, 'append_log') as log:
            app.restore_restart_state()
        self.assertFalse(path.exists())
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://c.example.invalid/')
        self.assertEqual(app.collect_urls.get('1.0', 'end-1c'), 'https://d.example.invalid/')
        log.assert_called_once_with(i18n.t('log.restart_restored'))


class EnglishSettingsPageTests(unittest.TestCase):
    def test_no_chinese_on_the_english_page(self):
        with _sandbox.language('en'):
            _sandbox.write_settings(latest_engine='2999.01.01')
            with mock.patch.object(updater, 'check_latest', no_network), \
                    mock.patch.object(StudioApp, 'clipboard_get', no_clipboard):
                app = StudioApp(_sandbox.app_args())
                try:
                    app.withdraw()
                    app.engine_updated = True
                    app.language_notice_kind = 'restart'
                    app.refresh_settings_controls()
                    allowed = {'简体中文', 'Language / 语言'}
                    found = []
                    for widget in descendants(app.pages['settings']):
                        if isinstance(widget, ui_theme.PathButton):
                            continue        # a folder or file path is the owner's data
                        texts = []
                        if isinstance(widget, (ctk.CTkLabel, ctk.CTkButton, ctk.CTkSwitch)):
                            texts.append(str(widget.cget('text')))
                        if isinstance(widget, (ctk.CTkOptionMenu, ctk.CTkSegmentedButton)):
                            texts += [str(value) for value in widget.cget('values')]
                        found += [text for text in texts if text not in allowed and CJK.search(text)]
                    found += [text for text in [app.language_notice.full_text] if CJK.search(text)]
                    self.assertEqual(found, [])
                    check_row_pairs(self, app, _sandbox.en_t)
                    self.assertEqual(app.check_update_button.cget('text'), _sandbox.en_t('settings.engine.check'))
                    self.assertEqual(app.settings_groups['subscriptions'].cget('text'),
                                     _sandbox.en_t('settings.group.subscriptions'))
                finally:
                    app.destroy()


if __name__ == '__main__':
    unittest.main()
