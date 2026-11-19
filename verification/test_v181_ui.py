"""1.8.1 window part (build/v181/design/DESIGN.md 2, 4, 6.3): the engine update saves latest_engine (A2);
the subscription tab's 应用名称 / 删除订阅 / 忽略所选 (B1-B3).

    python -X utf8 -B test_v181_ui.py

The three new buttons exist, stay out of app.controls and app.update_buttons (golden ui-1.6.0.json
records those) and follow set_busy through history_selection, first of all (an error of a history
widget does not skip them).  Ignore: the picked rows leave, the counts follow, one log line; nothing
picked says pick_ignore.  Delete: a confirmation whose default is no; nothing happens on no; after
yes the busy state and the subscription's own check are looked at again.  Apply name: the creation
strip's name box renames the selected subscription; an empty box, no selection, a locked library and
a vanished subscription each only say so.  At 1000x740 in zh and en the five subscription buttons and
the three controls of the pending line stay inside their rows.  Windows are never on screen (alpha 0,
no frame); no network.
"""
import _sandbox
import re
import sqlite3
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import i18n
import library_ui
import messages
import ui_theme
from test_i18n_pages import AppCase, pump

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
PAGE = 'https://example.org/feed'
NEW_BUTTONS = ('subscription_rename', 'subscription_delete', 'pending_ignore')
BUTTON_KEYS = {'subscription_rename': 'library.subscription.apply_name',
               'subscription_delete': 'library.subscription.delete',
               'pending_ignore': 'library.pending.ignore'}
OPTIONS = {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}


class V181Case(AppCase):
    def subscription_app(self, size='1000x740', output='v181-ui'):
        app = self.make_app(size=size, output=output)
        app.show_page('library')
        app.library_tabs.set(library_ui.TAB_SUBSCRIPTIONS)
        pump(app, timeout=0.3)
        return app

    @staticmethod
    def add(app, name, count, page=PAGE):
        key = app.library.subscription(name, dict(OPTIONS, urls=[page]), 3600, True, True)
        links = [{'url': f'{page}/item-{n}.mp3', 'title': f'Item {n}', 'kind': '媒体地址', 'source': page}
                 for n in range(count)]
        app.library.finish_subscription(key, [{'url': page, 'title': 'Feed', 'text': '', 'markdown': '',
                                               'links': links}], True)
        app.refresh_library()
        return key

    @staticmethod
    def select(app, key):
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        pump(app, timeout=0.1)

    @staticmethod
    def logged(app):
        lines = []
        original = app.append_log

        def capture(line):
            lines.append(str(line))
            original(line)
        app.append_log = capture
        return lines

    @staticmethod
    def row(app, key):
        return next((row for row in app.library.subscriptions() if row['id'] == key), None)

    def assertHint(self, app, text):
        self.assertEqual(ui_theme.full_text(app.subscription_note), text)
        self.assertEqual(app.subscription_note.cget('text_color'), ui_theme.WARN)


class ButtonTests(V181Case):
    def test_the_new_buttons_are_outside_controls_and_follow_set_busy(self):
        app = self.subscription_app()
        for name in NEW_BUTTONS:
            widget = getattr(app, name)
            self.assertIsInstance(widget, ctk.CTkButton, name)
            self.assertNotIn(widget, app.controls, name)
            self.assertNotIn(widget, app.update_buttons, name)
            self.assertEqual(widget.cget('state'), 'normal', name)
        app.operation = 'collect'
        app.set_busy(True)
        for name in NEW_BUTTONS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        app.set_busy(False)
        for name in NEW_BUTTONS:
            self.assertEqual(getattr(app, name).cget('state'), 'normal', name)

    def test_the_busy_hook_runs_before_the_history_widgets(self):
        app = self.subscription_app()
        app.operation = 'collect'
        # history_view is in app.controls (set_busy configures it itself); the note label is the first
        # history widget history_selection configures.  A TclError there leaves the hook done.
        with mock.patch.object(app.history_note, 'configure', side_effect=tk.TclError('boom')):
            app.set_busy(True)
        for name in NEW_BUTTONS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        app.set_busy(False)
        # Even the very first read of the history page comes after the hook: when it fails, the
        # buttons already have the new state.
        with mock.patch.object(app.history_tree, 'selection', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                app.set_busy(True)
        for name in NEW_BUTTONS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        app.set_busy(False)
        for name in NEW_BUTTONS:
            self.assertEqual(getattr(app, name).cget('state'), 'normal', name)

    def test_the_delete_button_is_the_danger_kind(self):
        app = self.subscription_app()
        self.assertEqual(app.subscription_delete.kind, 'danger')
        self.assertEqual(app.subscription_rename.kind, 'soft')
        self.assertEqual(app.pending_ignore.kind, 'soft')

    def test_texts_in_zh_and_en(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.subscription_app()
                for name, key in BUTTON_KEYS.items():
                    self.assertEqual(getattr(app, name).cget('text'), i18n.t(key), name)
                    if lang == 'en':
                        self.assertEqual(CJK.findall(getattr(app, name).cget('text')), [], name)
                self.close(app)


class IgnoreTests(V181Case):
    def test_ignore_the_picked_rows(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 4)
        self.select(app, key)
        lines = self.logged(app)
        urls = [item['url'] for item in app.visible_pending]
        app.pending_tree.selection_set(['1', '3'])
        app.ignore_pending()
        pump(app, timeout=0.1)
        self.assertEqual([item['url'] for item in app.visible_pending], [urls[0], urls[2]])
        self.assertEqual(len(app.pending_tree.get_children()), 2)
        self.assertEqual(self.row(app, key)['pending'], 2)
        self.assertEqual(str(app.subscription_tree.item(key, 'values')[3]), '2')
        self.assertEqual(lines, [i18n.t('library.pending.log_ignored', count=2, name='Night list')])

    def test_nothing_picked_says_so(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        with mock.patch.object(app.library, 'ignore_entries') as ignore:
            app.ignore_pending()
        ignore.assert_not_called()
        self.assertHint(app, i18n.t('library.pending.pick_ignore'))
        self.add(app, 'Empty list', 0, page='https://example.org/empty')
        empty = next(row['id'] for row in app.library.subscriptions() if row['pending'] == 0)
        self.select(app, empty)
        app.ignore_pending()
        self.assertHint(app, i18n.t('library.no_pending'))
        app.subscription_tree.selection_remove(app.subscription_tree.selection())
        app.subscription_selection()
        app.ignore_pending()
        self.assertHint(app, app.pick_subscription_text())

    def test_a_locked_library_only_says_so(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        app.pending_tree.selection_set(['0'])
        with mock.patch.object(app.library, 'ignore_entries', side_effect=sqlite3.OperationalError('database is locked')):
            app.ignore_pending()
        self.assertHint(app, i18n.t('library.busy_click'))
        self.assertEqual(self.row(app, key)['pending'], 2)


class DeleteTests(V181Case):
    def test_no_keeps_the_subscription_and_the_default_is_no(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 3)
        self.select(app, key)
        with mock.patch('library_ui.messagebox.askyesno', return_value=False) as ask:
            app.delete_subscription()
        self.assertIsNotNone(self.row(app, key))
        self.assertEqual(ask.call_count, 1)
        args, kwargs = ask.call_args
        self.assertEqual(kwargs.get('default'), 'no')
        self.assertEqual(kwargs.get('icon'), 'warning')
        self.assertEqual(args[0], i18n.t('library.subscription.delete_title'))
        self.assertEqual(args[1], i18n.t('library.subscription.delete_text', name='Night list', pending=3))

    def test_yes_deletes_refreshes_and_logs(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 3)
        other = self.add(app, 'Day list', 1, page='https://example.org/day')
        self.select(app, key)
        lines = self.logged(app)
        app.subscription_backoff[key] = 10 ** 10
        with mock.patch('library_ui.messagebox.askyesno', return_value=True):
            app.delete_subscription()
        pump(app, timeout=0.1)
        self.assertIsNone(self.row(app, key))
        self.assertIsNotNone(self.row(app, other))
        self.assertFalse(app.subscription_tree.exists(key))
        self.assertNotIn(key, app.subscription_backoff)
        self.assertEqual(app.pending_tree.get_children(), ())
        self.assertEqual(lines, [i18n.t('library.subscription.log_deleted', name='Night list', pending=3)])
        self.assertEqual(ui_theme.full_text(app.subscription_note), i18n.t('library.subscription.note'))

    def test_a_check_of_this_subscription_is_refused_before_the_dialog(self):
        """(review fix) subscription_checking is looked at first: no dialog, delete_busy."""
        app = self.subscription_app()
        key = self.add(app, 'Night list', 3)
        self.select(app, key)
        # A check that waits for the library (collect_pending) of this subscription, busy False.
        app.collect_pending = (None, None, None, key, 0)
        try:
            with mock.patch('library_ui.messagebox.askyesno', return_value=True) as ask:
                app.delete_subscription()
        finally:
            app.collect_pending = None
        ask.assert_not_called()
        self.assertIsNotNone(self.row(app, key))
        self.assertHint(app, i18n.t('library.subscription.delete_busy'))
        # The running check of this subscription: the window is busy too, still delete_busy.
        app.active_subscription = key
        app.busy = True
        try:
            with mock.patch('library_ui.messagebox.askyesno', return_value=True) as ask:
                app._delete_subscription()
        finally:
            app.active_subscription = None
            app.busy = False
        ask.assert_not_called()
        self.assertIsNotNone(self.row(app, key))
        self.assertHint(app, i18n.t('library.subscription.delete_busy'))

    def test_a_check_that_started_during_the_dialog_stops_the_delete(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 3)
        other = self.add(app, 'Day list', 1, page='https://example.org/day')
        self.select(app, key)

        def checking(*_args, **_kwargs):
            app.busy = True
            app.active_subscription = key
            return True
        try:
            with mock.patch('library_ui.messagebox.askyesno', side_effect=checking):
                app.delete_subscription()
        finally:
            app.busy = False
            app.active_subscription = None
        self.assertIsNotNone(self.row(app, key))
        self.assertHint(app, i18n.t('library.subscription.delete_busy'))
        # This subscription's check waits for the library (busy False).

        def waiting(*_args, **_kwargs):
            app.collect_pending = (None, None, None, key, 0)
            return True
        try:
            with mock.patch('library_ui.messagebox.askyesno', side_effect=waiting):
                app.delete_subscription()
        finally:
            app.collect_pending = None
        self.assertIsNotNone(self.row(app, key))
        self.assertHint(app, i18n.t('library.subscription.delete_busy'))
        # Another task made the window busy (a download, or another subscription's check): a true hint.
        for name, value in (('active_subscription', None), ('active_subscription', other)):
            def other_task(*_args, **_kwargs):
                app.busy = True
                setattr(app, name, value)
                return True
            try:
                with mock.patch('library_ui.messagebox.askyesno', side_effect=other_task):
                    app.delete_subscription()
            finally:
                app.busy = False
                app.active_subscription = None
            self.assertIsNotNone(self.row(app, key))
            self.assertHint(app, i18n.t('library.subscription.delete_task_busy'))
            self.assertNotEqual(i18n.t('library.subscription.delete_task_busy'),
                                i18n.t('library.subscription.delete_busy'))

    def test_no_selection_and_a_vanished_subscription(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 1)
        with mock.patch('library_ui.messagebox.askyesno', return_value=True) as ask:
            app.delete_subscription()
        ask.assert_not_called()
        self.assertHint(app, app.pick_subscription_text())
        self.select(app, key)
        with mock.patch('library_ui.messagebox.askyesno', return_value=True), \
                mock.patch.object(app.library, 'delete_subscription', return_value=False):
            app.delete_subscription()
        self.assertHint(app, i18n.t('library.subscription.gone'))


class RenameTests(V181Case):
    def test_apply_name_renames_the_selected_subscription(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        lines = self.logged(app)
        app.subscription_name.set('  New name  ')
        app.subscription_rename.invoke()
        pump(app, timeout=0.1)
        self.assertEqual(messages.show(self.row(app, key)['name']), 'New name')
        self.assertEqual(str(app.subscription_tree.item(key, 'values')[0]), 'New name')
        self.assertEqual(app.subscription_tree.selection(), (key,))
        self.assertEqual(lines, [i18n.t('library.subscription.log_renamed', name='New name')])
        self.assertEqual(app.subscription_name.get(), '  New name  ', 'the box is not cleared (as save_subscription)')

    def test_an_empty_box_no_selection_a_lock_and_a_vanished_row_only_say_so(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        app.subscription_name.set('New name')
        with mock.patch.object(app.library, 'rename_subscription') as rename:
            app.subscription_rename.invoke()
            rename.assert_not_called()
            self.assertHint(app, app.pick_subscription_text())
            self.select(app, key)
            for text in ('', '   '):
                app.subscription_name.set(text)
                app.subscription_rename.invoke()
                rename.assert_not_called()
                self.assertHint(app, i18n.t('library.subscription.name_empty'))
        app.subscription_name.set('New name')
        with mock.patch.object(app.library, 'rename_subscription',
                               side_effect=sqlite3.OperationalError('database is locked')):
            app.subscription_rename.invoke()
        self.assertHint(app, i18n.t('library.busy_click'))
        self.assertEqual(messages.show(self.row(app, key)['name']), 'Night list')
        with mock.patch.object(app.library, 'rename_subscription', return_value=False):
            app.subscription_rename.invoke()
        self.assertHint(app, i18n.t('library.subscription.gone'))


class WidthTests(V181Case):
    def test_the_rows_fit_at_1000x740(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.subscription_app()
                key = self.add(app, 'City Night Talk', 120)
                self.select(app, key)
                pump(app, timeout=0.3)
                tab = app.library_tabs.tab(library_ui.TAB_SUBSCRIPTIONS)
                for frame, count in ((app.subscription_actions, 5), (app.pending_actions, None)):
                    room = frame.winfo_width()
                    self.assertLessEqual(frame.winfo_rootx() + room, tab.winfo_rootx() + tab.winfo_width() + 1)
                    buttons = [w for w in frame.winfo_children() if isinstance(w, ctk.CTkButton)]
                    if count:
                        self.assertEqual(len(buttons), count)
                    for button in buttons:
                        label = button.cget('text')
                        self.assertTrue(button.winfo_ismapped(), f'{lang}: {label}')
                        self.assertLessEqual(button.winfo_x() + button.winfo_width(), room + 1, f'{lang}: {label}')
                        self.assertGreaterEqual(button.winfo_width() + 1, button.winfo_reqwidth(),
                                                f'{lang}: {label} has its full width')
                # The note, Add and Ignore share the pending line, the note left of Add.
                queue = next(w for w in app.pending_actions.winfo_children()
                             if isinstance(w, ctk.CTkButton) and w.cget('text') == i18n.t('collect.add'))
                note = app.subscription_note
                self.assertLessEqual(note.winfo_x() + note.winfo_width(), queue.winfo_x())
                self.assertLess(queue.winfo_x(), app.pending_ignore.winfo_x())
                full = ui_theme.full_text(note)
                self.assertEqual(note.cget('text'), full, f'{lang}: the status line is not cut')
                self.close(app)


class EngineUpdateTests(V181Case):
    def test_on_updated_saves_latest_engine(self):
        app = self.make_app(size='1000x740', output='v181-ui-engine')
        with mock.patch.object(app, 'schedule_save') as save:
            app.on_updated({'version': '2099.01.01', 'unchanged': False})
        self.assertEqual(app.prefs['latest_engine'], '2099.01.01')
        save.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
