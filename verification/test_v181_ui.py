"""1.8.1 window part (build/v181/design/DESIGN.md 2, 4, 6.3): the engine update saves latest_engine (A2);
the subscription view's rename / delete / ignore (B1-B3), in their 2.0.0 S4 places (subscriptions.md 4, 8.1).

    python -X utf8 -B test_v181_ui.py

Rename and delete are items of the subscription view's ⋯ (and of its table's right click): the delete item
is red and last.  Ignore is a ghost button over the new items.  The view's controls stay out of
app.controls and app.update_buttons and follow set_busy through history_selection, first of all
(archive_selection, then subscription_tools: an error of a history widget skips neither).  Ignore: the
picked rows leave, the counts follow, one log line; nothing picked: the button is grey and a call does
nothing.  Delete: a confirmation whose default is no; nothing happens on no; after yes the busy state and
the subscription's own check are looked at again.  Rename: ui_theme.ask_text with the current name;
Cancel writes nothing, a name is written and logged, a lock and a vanished subscription each only say
so.  At 1000x740 in zh and en the toolbar and the new-items row stay inside the view.  Windows are never
on screen (alpha 0, no frame); no network.
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
# The view's three buttons; subscription_tools computes them (not in any set_busy group).
TOOLS = ('subscription_check', 'pending_queue', 'pending_ignore')
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
        links = [{'url': f'{page}/item-{n}.mp3', 'title': f'Item {n}', 'kind': 'media', 'source': page}
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

    @staticmethod
    def item(app, key):
        """The ⋯ item of the subscription view whose label is i18n.t(key)."""
        label = i18n.t(key)
        return next(entry for entry in app.subscription_menu_items() if entry is not None and entry.label == label)

    def assertHint(self, app, text):
        self.assertEqual(ui_theme.full_text(app.subscription_note), text)
        self.assertEqual(app.subscription_note.cget('text_color'), ui_theme.WARN)


class ButtonTests(V181Case):
    def test_the_tools_are_outside_the_groups_and_follow_set_busy(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        app.pending_tree.selection_set(['0'])
        app.subscription_tools()
        for name in TOOLS:
            widget = getattr(app, name)
            self.assertIsInstance(widget, ctk.CTkButton, name)
            self.assertNotIn(widget, app.controls, name)
            self.assertNotIn(widget, app.update_buttons, name)
            self.assertNotIn(widget, app.input_controls, name)
            self.assertEqual(widget.cget('state'), 'normal', name)
        app.operation = 'collect'
        app.set_busy(True)
        for name in TOOLS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        self.assertFalse(self.item(app, 'library.subscription.rename').enabled)
        self.assertFalse(self.item(app, 'library.subscription.delete').enabled)
        app.set_busy(False)
        for name in TOOLS:
            self.assertEqual(getattr(app, name).cget('state'), 'normal', name)
        self.assertTrue(self.item(app, 'library.subscription.rename').enabled)
        self.assertTrue(self.item(app, 'library.subscription.delete').enabled)

    def test_the_busy_hooks_run_before_the_history_widgets(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        app.pending_tree.selection_set(['0'])
        app.subscription_tools()
        store = app.library
        store.archive_index(str(_sandbox.out('v181-ui-archive', 'item-1')),
                            {'title': 'Item', 'source': PAGE, 'files': [], 'transcripts': []})
        app.refresh_archives()
        app.archive_tree.selection_set('0')
        app.archive_selection()
        self.assertEqual(app.archive_open.cget('state'), 'normal')
        app.operation = 'collect'
        # The note label is the first history widget history_selection configures.  A TclError there leaves
        # both hooks done.
        with mock.patch.object(app.history_note, 'configure', side_effect=tk.TclError('boom')):
            app.set_busy(True)
        for name in TOOLS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        app.set_busy(False)
        # Even the very first read of the history page comes after the hooks: when it fails, the buttons
        # already have the new state.
        app.archive_tree.selection_remove(app.archive_tree.selection())
        with mock.patch.object(app.history_tree, 'selection', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                app.set_busy(True)
        for name in TOOLS:
            self.assertEqual(getattr(app, name).cget('state'), 'disabled', name)
        self.assertEqual(app.archive_open.cget('state'), 'disabled', 'archive_selection ran first')
        app.set_busy(False)
        for name in TOOLS:
            self.assertEqual(getattr(app, name).cget('state'), 'normal', name)

    def test_the_hooks_never_raise(self):
        app = self.subscription_app()
        with mock.patch.object(app.archive_open, 'configure', side_effect=tk.TclError('gone')):
            app.archive_tree.selection_set(())
            app.archive_selection()
        with mock.patch.object(app.subscription_check, 'configure', side_effect=tk.TclError('gone')):
            app.subscription_tools()

    def test_kinds_danger_last_and_texts_in_zh_and_en(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.subscription_app()
                key = self.add(app, 'Night list', 2)
                self.select(app, key)
                self.assertEqual(app.pending_ignore.kind, 'ghost')
                self.assertEqual(app.pending_ignore.cget('text'), i18n.t('common.ignore'))
                self.assertEqual(app.subscription_check.cget('text'), i18n.t('library.subscription.check_now'))
                items = app.subscription_menu_items()
                self.assertIsNone(items[-2], 'a separator before the destructive item')
                self.assertEqual(items[-1].label, i18n.t('library.subscription.delete'))
                self.assertTrue(items[-1].danger)
                self.assertEqual([entry.danger for entry in items[:-2]], [False] * (len(items) - 2))
                self.assertEqual(self.item(app, 'library.subscription.rename').label,
                                 i18n.t('library.subscription.rename'))
                if lang == 'en':
                    for entry in items:
                        if entry is not None:
                            self.assertEqual(CJK.findall(entry.label), [], entry.label)
                    for widget in (app.pending_ignore, app.subscription_check, app.pending_queue):
                        self.assertEqual(CJK.findall(widget.cget('text')), [], widget.cget('text'))
                self.close(app)


class IgnoreTests(V181Case):
    def test_ignore_the_picked_rows(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 4)
        self.select(app, key)
        lines = self.logged(app)
        urls = [item['url'] for item in app.visible_pending]
        app.pending_tree.selection_set(['1', '3'])
        app.subscription_tools()
        self.assertEqual(app.pending_ignore.cget('state'), 'normal')
        app.pending_ignore.invoke()
        pump(app, timeout=0.1)
        self.assertEqual([item['url'] for item in app.visible_pending], [urls[0], urls[2]])
        self.assertEqual(len(app.pending_tree.get_children()), 2)
        self.assertEqual(self.row(app, key)['pending'], 2)
        self.assertEqual(str(app.subscription_tree.item(key, 'values')[3]), '2')
        self.assertEqual(app.pending_count.cget('text'), i18n.t('library.pending.count', count=2))
        self.assertEqual(lines, [i18n.t('library.pending.log_ignored', count=2, name='Night list')])

    def test_nothing_picked_is_grey_and_does_nothing(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        app.pending_tree.selection_remove(app.pending_tree.selection())
        app.subscription_tools()
        self.assertEqual(app.pending_ignore.cget('state'), 'disabled')
        self.assertFalse(app.pending_menu_items()[1].enabled)
        with mock.patch.object(app.library, 'ignore_entries') as ignore:
            app.ignore_pending()
        ignore.assert_not_called()
        self.assertEqual(ui_theme.full_text(app.subscription_note), '', 'no "select first" line')
        self.add(app, 'Empty list', 0, page='https://example.org/empty')
        empty = next(row['id'] for row in app.library.subscriptions() if row['pending'] == 0)
        self.select(app, empty)
        self.assertEqual(app.pending_ignore.cget('state'), 'disabled')
        with mock.patch.object(app.library, 'ignore_entries') as ignore:
            app.ignore_pending()
        ignore.assert_not_called()
        app.subscription_tree.selection_remove(app.subscription_tree.selection())
        app.subscription_selection()
        self.assertEqual(app.pending_ignore.cget('state'), 'disabled')
        with mock.patch.object(app.library, 'ignore_entries') as ignore:
            app.ignore_pending()
        ignore.assert_not_called()
        self.assertEqual(ui_theme.full_text(app.subscription_note), '')

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
        item = self.item(app, 'library.subscription.delete')
        self.assertTrue(item.enabled)
        with mock.patch('library_ui.messagebox.askyesno', return_value=False) as ask:
            item.command()
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
        self.assertEqual(lines, [i18n.t('library.subscription.log_deleted', name='Night list', pending=3)])
        # The view always has a current subscription: the first one left is selected, with its new items.
        first = app.subscription_tree.get_children()[0]
        self.assertEqual(app.subscription_tree.selection(), (first,))
        self.assertEqual(len(app.pending_tree.get_children()), self.row(app, first)['pending'])
        self.assertEqual(ui_theme.full_text(app.subscription_note), '')

    def test_a_check_of_this_subscription_is_refused_before_the_dialog(self):
        """(review fix) subscription_checking is looked at first: no dialog, delete_busy; the menu item is grey."""
        app = self.subscription_app()
        key = self.add(app, 'Night list', 3)
        self.select(app, key)
        # A check that waits for the library (collect_pending) of this subscription, busy False.
        app.collect_pending = (None, None, None, key, 0)
        try:
            self.assertFalse(self.item(app, 'library.subscription.delete').enabled)
            self.assertTrue(self.item(app, 'library.subscription.rename').enabled)
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

    def test_no_selection_is_grey_and_a_vanished_subscription_says_so(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 1)
        app.subscription_tree.selection_remove(app.subscription_tree.selection())
        app.subscription_selection()
        self.assertFalse(self.item(app, 'library.subscription.delete').enabled)
        with mock.patch('library_ui.messagebox.askyesno', return_value=True) as ask:
            app.delete_subscription()
        ask.assert_not_called()
        self.assertEqual(ui_theme.full_text(app.subscription_note), '')
        self.select(app, key)
        with mock.patch('library_ui.messagebox.askyesno', return_value=True), \
                mock.patch.object(app.library, 'delete_subscription', return_value=False):
            app.delete_subscription()
        self.assertHint(app, i18n.t('library.subscription.gone'))


class RenameTests(V181Case):
    def test_rename_asks_with_the_current_name_and_writes_the_answer(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        lines = self.logged(app)
        item = self.item(app, 'library.subscription.rename')
        self.assertTrue(item.enabled)
        with mock.patch('ui_theme.ask_text', return_value='New name') as ask:
            item.command()
        pump(app, timeout=0.1)
        args = ask.call_args[0]
        self.assertIs(args[0], app)
        self.assertEqual(args[1], i18n.t('library.subscription.rename_title'))
        self.assertEqual(args[2], 'Night list')
        self.assertEqual(messages.show(self.row(app, key)['name']), 'New name')
        self.assertEqual(str(app.subscription_tree.item(key, 'values')[0]), 'New name')
        self.assertEqual(app.subscription_tree.selection(), (key,))
        self.assertEqual(lines, [i18n.t('library.subscription.log_renamed', name='New name')])

    def test_cancel_no_selection_a_lock_and_a_vanished_row(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)
        with mock.patch.object(app.library, 'rename_subscription') as rename, \
                mock.patch('ui_theme.ask_text', return_value=None) as ask:
            app.rename_subscription()
            ask.assert_called_once()
            rename.assert_not_called()
            app.subscription_tree.selection_remove(app.subscription_tree.selection())
            app.subscription_selection()
            self.assertFalse(self.item(app, 'library.subscription.rename').enabled)
            ask.reset_mock()
            app.rename_subscription()
            ask.assert_not_called()
            rename.assert_not_called()
        self.select(app, key)
        with mock.patch.object(app.library, 'rename_subscription',
                               side_effect=sqlite3.OperationalError('database is locked')), \
                mock.patch('ui_theme.ask_text', return_value='New name'):
            app.rename_subscription()
        self.assertHint(app, i18n.t('library.busy_click'))
        self.assertEqual(messages.show(self.row(app, key)['name']), 'Night list')
        with mock.patch.object(app.library, 'rename_subscription', return_value=False), \
                mock.patch('ui_theme.ask_text', return_value='New name'):
            app.rename_subscription()
        self.assertHint(app, i18n.t('library.subscription.gone'))

    def test_a_subscription_deleted_during_the_dialog_says_gone(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)

        def answer(*_args, **_kwargs):
            app.library.delete_subscription(key)
            return 'New name'
        with mock.patch('ui_theme.ask_text', side_effect=answer):
            app.rename_subscription()
        self.assertIsNone(self.row(app, key))
        self.assertHint(app, i18n.t('library.subscription.gone'))

    def test_a_check_that_started_during_the_dialog_keeps_the_typed_name(self):
        app = self.subscription_app()
        key = self.add(app, 'Night list', 2)
        self.select(app, key)

        def answer(*_args, **_kwargs):
            app.busy = True
            app.active_subscription = key
            return 'New name'
        try:
            with mock.patch('ui_theme.ask_text', side_effect=answer):
                app.rename_subscription()
        finally:
            app.busy = False
            app.active_subscription = None
        self.assertEqual(messages.show(self.row(app, key)['name']), 'New name')

    def test_the_dialog_greys_ok_for_an_empty_name(self):
        app = self.subscription_app()
        dialog = ui_theme.TextDialog(app, i18n.t('library.subscription.rename_title'), 'Night list')
        try:
            self.assertEqual(dialog.ok_button.cget('state'), 'normal')
            for text in ('', '   '):
                dialog.value.set(text)
                self.assertEqual(dialog.ok_button.cget('state'), 'disabled')
                dialog.accept()
                self.assertTrue(dialog.winfo_exists(), 'an empty name does not close the dialog')
            dialog.value.set('  Kept  ')
            dialog.accept()
            self.assertEqual(dialog.result, 'Kept')
        finally:
            try:
                dialog.destroy()
            except tk.TclError:
                pass


class WidthTests(V181Case):
    def test_the_rows_fit_at_1000x740(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.subscription_app()
                key = self.add(app, 'City Night Talk', 120)
                self.select(app, key)
                app.pending_tree.selection_set(app.pending_tree.get_children()[:100])
                app.subscription_tools()
                app.library.finish_subscription(key, [], False, messages.raw('x' * 300))
                app.subscription_selection()
                pump(app, timeout=0.3)
                tab = app.library_tabs.tab(library_ui.TAB_SUBSCRIPTIONS)
                right = tab.winfo_rootx() + tab.winfo_width()
                for frame, widgets in ((app.subscription_bar, (app.subscription_check, app.subscription_menu)),
                                       (app.pending_bar, (app.pending_count, app.subscription_note,
                                                          app.pending_queue, app.pending_ignore))):
                    self.assertLessEqual(frame.winfo_rootx() + frame.winfo_width(), right + 1)
                    for widget in widgets:
                        label = widget.cget('text')
                        self.assertTrue(widget.winfo_ismapped(), f'{lang}: {label}')
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1, f'{lang}: {label}')
                        if isinstance(widget, ctk.CTkButton):
                            self.assertGreaterEqual(widget.winfo_width() + 1, widget.winfo_reqwidth(),
                                                    f'{lang}: {label} has its full width')
                # The count, the reason, Ignore and Add to downloads (N) share one row, in this order; the long
                # reason is cut, its whole text kept.
                self.assertEqual(app.pending_queue.cget('text'), app.add_text(100))
                self.assertLessEqual(app.pending_count.winfo_x() + app.pending_count.winfo_width(),
                                     app.subscription_note.winfo_x())
                self.assertLessEqual(app.subscription_note.winfo_x() + app.subscription_note.winfo_width(),
                                     app.pending_ignore.winfo_x())
                self.assertLess(app.pending_ignore.winfo_x(), app.pending_queue.winfo_x())
                self.assertEqual(ui_theme.full_text(app.subscription_note), 'x' * 300)
                self.assertNotEqual(app.subscription_note.cget('text'), 'x' * 300)
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
