"""2.0.0 S4 B3: the library page (library.md, subscriptions.md): the header's view switch, the notice bar, the
history, subscription and archive views, their '⋯' menus (the same lists as the tables' right click), the
empty states, double click / Enter, F5, the stage of the subscription view's one solid button, the
schedule words and the defaults a new subscription takes from the settings page.

    python -X utf8 -B test_page_library.py

Every action of the page is reached from a visible control: the tests call the ``*_menu_items`` functions
(what ⋯ and the right click show) and check the label and the enabled state of every entry.  Focus checks
read ``focus -lastfor`` (the window is never the foreground window).  Windows are never on screen (alpha 0,
no frame); no network.
"""
import _sandbox
import re
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import formats
import i18n
import library_ui
import messages
import settings
import ui_theme
from test_i18n_pages import AppCase, pump, walk

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
PAGE = 'https://example.org/feed'
OPTIONS = {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
VIEWS = (library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE)
TABLES = {library_ui.TAB_HISTORY: 'history_tree', library_ui.TAB_SUBSCRIPTIONS: 'subscription_tree',
          library_ui.TAB_ARCHIVE: 'archive_tree'}
BUSY = (('download', True), ('collect', True), ('update', True))


def lastfocus(app):
    """The widget that has (or gets, when the window is activated) the keyboard focus."""
    name = str(app.tk.call('focus', '-lastfor', app._w))
    try:
        return app.nametowidget(name)
    except KeyError:
        return None


def inside(widget, target):
    """``widget`` is ``target`` or one of its children (a CTk text box focuses its inner tk.Text)."""
    return widget is not None and (widget is target or str(widget).startswith(str(target) + '.'))


def row_event(tree, iid):
    """A mouse event in the middle of row ``iid`` (for the handlers table_actions binds)."""
    box = tree.bbox(iid)
    return mock.Mock(x=box[0] + 10, y=box[1] + box[3] // 2)


def entries(items):
    """(label, enabled) of every entry of a menu list, None for a separator."""
    return [None if item is None else (item.label, item.enabled) for item in items]


class LibraryCase(AppCase):
    def library_app(self, view=library_ui.TAB_HISTORY, size='1120x810'):
        # A library of its own for every app (the smoke library lives in the output folder).
        self._made = getattr(self, '_made', 0) + 1
        app = self.make_app(size=size, output=f'page-library-{self._testMethodName}-{self._made}')
        app.show_page('library')
        app.library_tabs.set(view)
        pump(app, timeout=0.2)
        return app

    @staticmethod
    def job(app, status='interrupted', kind='download', title='Night song'):
        store = app.library
        job = store.create_job(kind, title, {'urls': [PAGE + '/' + title.replace(' ', '-')],
                                             'output': str(_sandbox.out('page-library-out')),
                                             'mode': formats.MODES[0]})
        store.change_job(job, status=status)
        return job

    @staticmethod
    def subscription(app, name='Night list', count=2, page=PAGE, interval=3600):
        key = app.library.subscription(name, dict(OPTIONS, urls=[page]), interval, True, True)
        links = [{'url': f'{page}/item-{n}.mp3', 'title': f'Item {n}', 'kind': 'media', 'source': page}
                 for n in range(count)]
        app.library.finish_subscription(key, [{'url': page, 'title': 'Feed', 'text': '', 'markdown': '',
                                               'links': links}], True)
        return key

    @staticmethod
    def archive(app, number=1, uploader=''):
        app.library.archive_index(str(_sandbox.out('page-library-archive', f'item-{number}')),
                                  {'url': f'{PAGE}/item-{number}', 'source': PAGE, 'title': f'Item {number}',
                                   'uploader': uploader, 'files': ['a.m4a'], 'transcripts': []})

    @staticmethod
    def busy(app, operation):
        app.operation = operation
        app.set_busy(True)

    @staticmethod
    def idle(app):
        app.set_busy(False)
        app.operation = None


class FrameTests(LibraryCase):
    def test_the_view_ids_and_the_switch_in_the_header(self):
        app = self.library_app()
        self.assertEqual(set(app.library_tabs._frames), set(VIEWS))
        self.assertEqual(app.library_tabs.get(), library_ui.TAB_HISTORY)
        page = app.pages['library']
        self.assertIs(app.library_tabs.bar.master, page.header.left)
        self.assertEqual(app.library_tabs.bar.cget('values'), [i18n.label('library_tab', view) for view in VIEWS])
        self.assertEqual(app.library_tabs.bar.cget('height'), ui_theme.CONTROL_H_SM)
        # The header holds the title and the switch only: no '⋯' (refresh is in each view's ⋯).
        self.assertEqual(page.header.right.winfo_children(), [])
        self.assertEqual(page.header.left.winfo_children(), [app.library_tabs.bar])
        self.assertEqual(page.header.title.cget('text'), i18n.t('nav.library'))

    def test_focus_page_gives_the_focus_to_the_view_table(self):
        app = self.library_app()
        self.job(app)
        self.subscription(app)
        self.archive(app)
        app.refresh_library()
        pump(app, timeout=0.2)      # the tables are mapped (a table that is not takes no focus)
        for view in VIEWS:
            with self.subTest(view=view):
                app.show_page('download')
                app.urls.focus_set()
                self.assertTrue(inside(lastfocus(app), app.urls))
                app.library_tabs.set(view)
                app.show_page('library')
                self.assertIs(lastfocus(app), getattr(app, TABLES[view]))
        # Switching the view moves a focus left in the old view's table to the new one.
        for view in VIEWS:
            with self.subTest(switch=view):
                app.library_tabs._clicked(i18n.label('library_tab', view))
                self.assertIs(lastfocus(app), getattr(app, TABLES[view]))

    def test_the_notice_bar(self):
        app = self.library_app()
        notice = app.library_notice
        self.assertFalse(notice.shown)
        with mock.patch.object(app.library, 'history', side_effect=RuntimeError('broken file')):
            app.refresh_library()
        self.assertTrue(notice.shown)
        self.assertEqual(notice.full_text, i18n.t('library.unreadable', error=messages.error_text(RuntimeError('broken file'))))
        self.assertEqual([button.cget('text') for button in notice.buttons], [i18n.t('common.refresh')])
        notice.buttons[0].invoke()      # Refresh: it works now, so the error goes
        self.assertFalse(notice.shown, 'the next refresh that works takes the error away')
        # A locked library: the calm line without a button; it goes by itself.
        app.library_waiting = True
        app.show_library_problem('')
        self.assertTrue(notice.shown)
        self.assertEqual(notice.full_text, i18n.t('library.busy'))
        self.assertEqual(notice.buttons, ())
        app.library_waiting = False
        app.show_library_problem('')
        self.assertFalse(notice.shown)
        # The warning of open_library (a temporary library, a file set aside): Got it closes it for good.
        app.library_warning = messages.raw('kept aside')
        app.show_library_problem('')
        self.assertEqual(notice.full_text, 'kept aside')
        self.assertEqual([button.cget('text') for button in notice.buttons], [i18n.t('common.got_it')])
        notice.buttons[0].invoke()
        self.assertFalse(notice.shown)
        app.refresh_library()
        self.assertFalse(notice.shown)

    def test_f5_refreshes_the_library_only_on_its_page(self):
        app = self.library_app()
        # Both F5 bindings of the window stay (add='+'): the download page's retry and this page's refresh.
        script = str(app.bind('<F5>'))
        self.assertIn('library_key_refresh', script)
        self.assertEqual(len([line for line in script.splitlines() if line.strip()]), 2, script)
        with mock.patch.object(app, 'refresh_library') as refresh, mock.patch.object(app, 'retry_failed') as retry:
            app.library_key_refresh()
            self.assertEqual(refresh.call_count, 1)
            app.show_page('download')
            app.library_key_refresh()
            app._key(app.retry_failed)
            self.assertEqual((refresh.call_count, retry.call_count), (1, 1))
            app.show_page('library')
            app._key(app.retry_failed)
            self.assertEqual(retry.call_count, 1, 'F5 of the download page does nothing on the library page')

    def test_an_empty_view_keeps_the_focus_on_the_library_page(self):
        """The empty state lies over the view's empty table, which stays gridded: the focus goes to it (a table that
        is not mapped takes none, and the focus would stay on the hidden download page)."""
        app = self.library_app()
        app.refresh_library()
        pump(app, timeout=0.2)
        for view in VIEWS:
            with self.subTest(view=view):
                table = getattr(app, TABLES[view])
                self.assertTrue(table.container.winfo_manager())
                self.assertEqual(table.get_children(), ())
                app.show_page('download')
                app.urls.focus_set()
                app.library_tabs.set(view)
                app.show_page('library')
                self.assertIs(lastfocus(app), table)


class HistoryTests(LibraryCase):
    def select(self, app, job):
        app.history_tree.selection_set(job)
        app.history_selection()

    def test_the_menu_matrix(self):
        app = self.library_app()
        open_job = self.job(app, 'interrupted', title='Open job')
        done_job = self.job(app, 'completed', title='Done job')
        app.refresh_library()
        labels = [i18n.t('library.view_results'), i18n.t('library.resume'), i18n.t('common.refresh')]
        cases = [(None, False, False), (open_job, True, True), (done_job, True, False)]
        for job, view, resume in cases:
            for operation, busy in ((None, False),) + BUSY:
                with self.subTest(job=job, operation=operation):
                    app.history_tree.selection_remove(app.history_tree.selection())
                    if job:
                        self.select(app, job)
                    else:
                        app.history_selection()
                    if busy:
                        self.busy(app, operation)
                    items = app.history_menu_items()
                    self.assertEqual(entries(items), [(labels[0], view and not busy), (labels[1], resume and not busy),
                                                      None, (labels[2], True)])
                    self.assertEqual(app.history_resume.cget('state'), 'normal' if resume and not busy else 'disabled')
                    self.assertEqual(app.history_menu.menu_items, app.history_menu_items)
                    self.idle(app)
        self.assertEqual(app.history_resume.kind, 'primary')
        self.assertNotIn(app.history_resume, app.controls)

    def test_the_footer(self):
        app = self.library_app()
        job = self.job(app, 'interrupted')
        app.refresh_library()
        self.assertEqual(ui_theme.full_text(app.history_note), '', 'nothing selected: no line')
        self.assertEqual(app.history_count.cget('text'), i18n.t('library.history.count', total=1))
        self.assertFalse(app.history_more.winfo_manager())
        self.select(app, job)
        self.assertEqual(ui_theme.full_text(app.history_note), app.history_note_text(app.library.job(job)))
        app.history_tree.selection_remove(job)
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), '')
        with mock.patch.object(app.library, 'job', side_effect=RuntimeError('broken')):
            self.select(app, job)
        self.assertEqual(ui_theme.full_text(app.history_note), i18n.t('library.history.unreadable'))
        self.assertEqual(app.history_resume.cget('state'), 'disabled')
        self.assertEqual([entry and entry.enabled for entry in app.history_menu_items()], [False, False, None, True])
        app.show_history_count(300, 650)
        self.assertEqual(app.history_count.cget('text'), i18n.t('library.history.count', total=650))
        self.assertEqual(app.history_more.cget('text'), i18n.t('library.history.more'))
        self.assertTrue(app.history_more.winfo_manager())

    def test_double_click_and_enter_view_the_results(self):
        app = self.library_app()
        job = self.job(app, 'interrupted')
        app.refresh_library()
        self.select(app, job)
        pump(app, timeout=0.2)
        tree = app.history_tree
        self.assertTrue(tree.bind('<Double-Button-1>'))
        self.assertTrue(tree.bind('<Return>'))
        heading = mock.Mock(x=10, y=5)
        self.assertEqual(tree.identify_region(heading.x, heading.y), 'heading')
        # The handlers call the action named when the page was built: view_history itself does the checks.
        with mock.patch.object(app, 'show_page') as show, mock.patch.object(app, 'add_links') as add:
            tree.on_double_click(heading)       # the heading: resizes columns, opens nothing
            add.assert_not_called()
            tree.on_double_click(row_event(tree, job))
            add.assert_called_once()
            show.assert_called_with('download')
            add.reset_mock()
            self.assertEqual(tree.on_enter(None), 'break', 'Enter stops at the table, not at the window')
            add.assert_called_once()
            add.reset_mock()
            self.busy(app, 'download')
            tree.on_enter(None)
            tree.on_double_click(row_event(tree, job))
            add.assert_not_called()
            self.idle(app)

    def test_the_empty_state_and_the_search(self):
        app = self.library_app()
        app.refresh_library()
        self.assertTrue(app.history_empty.winfo_manager())
        self.assertEqual(app.history_empty.hint_label.cget('text'), i18n.t('library.history.empty'))
        self.assertIsNone(app.history_empty.action_button, 'the history fills itself: no action')
        for widget in (app.history_bar, app.history_footer):
            self.assertFalse(widget.winfo_manager())
        self.assertTrue(app.history_tree.container.winfo_manager(), 'under the empty state, for the focus')
        self.job(app)
        app.refresh_library()
        self.assertFalse(app.history_empty.winfo_manager())
        for widget in (app.history_bar, app.history_tree.container, app.history_footer):
            self.assertTrue(widget.winfo_manager())
        app.history_search.set('nothing like it')
        app.run_history_search()
        self.assertFalse(app.history_empty.winfo_manager(), 'a search with no match keeps the toolbar')
        self.assertTrue(app.history_bar.winfo_manager())
        self.assertEqual(app.history_tree.get_children(), ())
        self.assertEqual(app.history_tree._empty, i18n.t('common.no_match'))
        self.assertEqual(app.history_count.cget('text'), i18n.t('library.history.found', total=0))
        self.assertEqual(app.history_search_entry.placeholder_label.cget('text'), i18n.t('common.search'))


class ArchiveTests(LibraryCase):
    def test_the_menu_matrix_and_open_folder_while_busy(self):
        app = self.library_app(library_ui.TAB_ARCHIVE)
        self.archive(app, 1)
        app.refresh_library()
        labels = [i18n.t('common.open_folder'), i18n.t('library.archive.attach'), i18n.t('common.refresh')]
        for picked in (False, True):
            for operation, busy in ((None, False),) + BUSY:
                with self.subTest(picked=picked, operation=operation):
                    app.archive_tree.selection_set(('0',) if picked else ())
                    app.archive_selection()
                    if busy:
                        self.busy(app, operation)
                    self.assertEqual(entries(app.archive_menu_items()),
                                     [(labels[0], picked), (labels[1], picked and not busy), None, (labels[2], True)])
                    self.assertEqual(app.archive_open.cget('state'), 'normal' if picked else 'disabled')
                    self.idle(app)
        self.assertEqual(app.archive_open.kind, 'soft')
        self.assertNotIn(app.archive_open, app.controls)
        app.archive_tree.selection_set(())
        with mock.patch('library_ui.messagebox') as box, mock.patch('library_ui.os.startfile') as start:
            app.open_archive()
            app.attach_transcript()
        self.assertEqual(box.mock_calls, [], 'nothing selected: no "select first" dialog')
        start.assert_not_called()

    def test_double_click_and_enter_open_the_folder(self):
        app = self.library_app(library_ui.TAB_ARCHIVE)
        self.archive(app, 1)
        app.refresh_library()
        app.archive_tree.selection_set('0')
        app.archive_selection()
        pump(app, timeout=0.2)
        tree = app.archive_tree
        self.assertTrue(tree.bind('<Double-Button-1>'))
        self.assertTrue(tree.bind('<Return>'))
        with mock.patch('library_ui.os.startfile') as start:
            tree.on_double_click(mock.Mock(x=10, y=5))
            start.assert_not_called()
            tree.on_double_click(row_event(tree, '0'))
            self.assertEqual(start.call_count, 1)
            self.busy(app, 'download')
            self.assertEqual(tree.on_enter(None), 'break')
            self.assertEqual(start.call_count, 2, 'Open folder also while a task runs')
            self.idle(app)

    def test_the_empty_state_follows_the_archive_switch(self):
        app = self.library_app(library_ui.TAB_ARCHIVE)
        app.archive_enabled.set(False)
        app.refresh_library()
        empty = app.archive_empty
        self.assertTrue(empty.winfo_manager())
        self.assertFalse(app.archive_bar.winfo_manager())
        self.assertTrue(app.archive_tree.container.winfo_manager(), 'under the empty state, for the focus')
        self.assertEqual(empty.hint_label.cget('text'), i18n.t('library.archive.empty'))
        self.assertEqual(empty.action_button.cget('text'), i18n.t('library.archive.go_settings'))
        self.assertTrue(empty.action_button.winfo_manager())
        app.archive_enabled.set(True)
        self.assertFalse(empty.action_button.winfo_manager(), 'archiving is on: nothing to set')
        app.archive_enabled.set(False)
        self.assertTrue(empty.action_button.winfo_manager())
        with mock.patch.object(app, 'show_settings') as show:
            empty.action_button.invoke()
        show.assert_called_once_with('downloads')
        self.archive(app, 1)
        app.refresh_library()
        self.assertFalse(empty.winfo_manager())
        self.assertTrue(app.archive_bar.winfo_manager())
        app.archive_search.set('nothing like it')
        self.assertFalse(empty.winfo_manager())
        self.assertEqual(app.archive_tree.get_children(), ())
        self.assertEqual(app.archive_tree._empty, i18n.t('common.no_match'))
        self.assertEqual(app.archive_search_entry.placeholder_label.cget('text'), i18n.t('common.search'))

    def test_an_empty_author_is_a_dash_in_both_languages(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.library_app(library_ui.TAB_ARCHIVE)
                self.archive(app, 1, uploader='')
                self.archive(app, 2, uploader='City Night')
                app.refresh_library()
                authors = sorted(app.archive_tree.full_values(iid)[1] for iid in app.archive_tree.get_children())
                self.assertEqual(authors, sorted(['—', 'City Night']))
                self.close(app)


class SubscriptionTests(LibraryCase):
    def labels(self):
        return [i18n.t('library.subscription.check_now'), i18n.t('library.subscription.pause'),
                i18n.t('table.schedule'), i18n.t('library.subscription.rename'), i18n.t('common.refresh'),
                i18n.t('library.subscription.delete')]

    def test_the_first_subscription_is_selected(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        first = self.subscription(app, 'First', 2)
        self.subscription(app, 'Second', 1, page='https://example.org/other')
        app.refresh_library()
        self.assertEqual(len(app.subscription_tree.selection()), 1)
        shown = app.subscription_tree.selection()[0]
        self.assertEqual(shown, app.subscription_tree.get_children()[0])
        self.assertEqual(len(app.pending_tree.get_children()), 2 if shown == first else 1)
        self.assertEqual(app.subscription_check.cget('state'), 'normal')

    def test_the_menu_matrix(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        key = self.subscription(app, 'Night list', 2)
        app.refresh_library()
        for picked in (False, True):
            for operation, busy in ((None, False),) + BUSY:
                with self.subTest(picked=picked, operation=operation):
                    app.subscription_tree.selection_set((key,) if picked else ())
                    app.subscription_selection()
                    if busy:
                        self.busy(app, operation)
                    items = app.subscription_menu_items()
                    usable = picked and not busy
                    self.assertEqual(entries(items), [(self.labels()[0], usable), (self.labels()[1], usable),
                                                      (self.labels()[2], usable), (self.labels()[3], usable),
                                                      (self.labels()[4], True), None, (self.labels()[5], usable)])
                    schedule = items[2]
                    self.assertEqual([child.label for child in schedule.children],
                                     [i18n.label('interval', seconds) for seconds in settings.SCHEDULES])
                    self.assertEqual([child.checked for child in schedule.children],
                                     [picked and seconds == 3600 for seconds in settings.SCHEDULES])
                    self.assertEqual({child.enabled for child in schedule.children}, {usable})
                    self.assertTrue(items[-1].danger)
                    self.assertEqual(app.subscription_check.cget('state'), 'normal' if usable else 'disabled')
                    self.idle(app)
        self.assertEqual(app.subscription_menu.menu_items, app.subscription_menu_items)
        # Paused: Resume instead of Pause.
        app.subscription_tree.selection_set(key)
        app.toggle_subscription()
        self.assertEqual(app.subscription_menu_items()[1].label, i18n.t('library.subscription.resume'))
        self.assertEqual(app.subscription_tree.full_values(key)[1], i18n.t('library.state.paused'))
        app.subscription_menu_items()[1].command()
        self.assertEqual(app.subscription_menu_items()[1].label, i18n.t('library.subscription.pause'))
        # The check of this subscription greys Delete only.
        app.active_subscription = key
        try:
            items = app.subscription_menu_items()
            self.assertFalse(items[-1].enabled)
            self.assertTrue(items[3].enabled)
        finally:
            app.active_subscription = None

    def test_the_schedule_submenu_writes_only_a_change(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        key = self.subscription(app, 'Night list', 1)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        children = app.subscription_menu_items()[2].children
        with mock.patch.object(app.library, 'configure_subscription') as configure:
            children[settings.SCHEDULES.index(3600)].command()
        configure.assert_not_called()
        children[settings.SCHEDULES.index(86400)].command()
        row = next(row for row in app.library.subscriptions() if row['id'] == key)
        self.assertEqual(row['interval'], 86400)
        self.assertEqual(app.subscription_tree.full_values(key)[1], i18n.label('interval', 86400))
        self.assertEqual([child.checked for child in app.subscription_menu_items()[2].children],
                         [seconds == 86400 for seconds in settings.SCHEDULES])

    def test_the_new_items_menu_and_the_stage_of_the_solid_button(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        key = self.subscription(app, 'Night list', 3)
        empty = self.subscription(app, 'Quiet list', 0, page='https://example.org/quiet')
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        # New items, none picked: Add to downloads is the solid one (grey), Check now soft.
        self.assertEqual(app.pending_count.cget('text'), i18n.t('library.pending.count', count=3))
        self.assertEqual((app.pending_queue.kind, app.subscription_check.kind), ('primary', 'soft'))
        self.assertEqual(app.pending_queue.cget('state'), 'disabled')
        self.assertEqual(app.pending_queue.cget('text'), i18n.t('collect.add'))
        self.assertEqual(entries(app.pending_menu_items()),
                         [(i18n.t('collect.add'), False), (i18n.t('common.ignore'), False)])
        # Ctrl+A picks every new item.
        for sequence in ('<Control-a>', '<Control-A>'):
            self.assertTrue(app.pending_tree.bind(sequence))
        self.assertEqual(app.select_all_pending(), 'break')
        self.assertEqual(len(app.pending_tree.selection()), 3)
        app.subscription_tools()
        self.assertEqual(app.pending_queue.cget('text'), app.add_text(3))
        for operation, busy in ((None, False),) + BUSY:
            with self.subTest(operation=operation):
                if busy:
                    self.busy(app, operation)
                queue = not busy or operation == 'download'
                self.assertEqual(entries(app.pending_menu_items()),
                                 [(app.add_text(3), queue), (i18n.t('common.ignore'), not busy)])
                self.assertEqual(app.pending_queue.cget('state'), 'normal' if queue else 'disabled')
                self.assertEqual(app.pending_ignore.cget('state'), 'normal' if not busy else 'disabled')
                self.idle(app)
        # While a download runs the picked items still go to the download page.
        self.busy(app, 'download')
        with mock.patch.object(app, 'queue_collected_items') as queue:
            app.pending_queue.invoke()
        self.assertEqual(len(queue.call_args[0][0]), 3)
        self.idle(app)
        self.busy(app, 'collect')
        with mock.patch.object(app, 'queue_collected_items') as queue:
            app.queue_subscription()
        queue.assert_not_called()
        self.idle(app)
        # No new items: Check now is the solid one.
        app.subscription_tree.selection_set(empty)
        app.subscription_selection()
        self.assertEqual(app.pending_count.cget('text'), i18n.t('library.pending.count', count=0))
        self.assertEqual((app.pending_queue.kind, app.subscription_check.kind), ('soft', 'primary'))
        self.assertEqual(app.pending_tree._empty, i18n.t('library.pending.empty'))
        # One solid button in the view at any time.
        for selected in (key, empty):
            app.subscription_tree.selection_set(selected)
            app.subscription_selection()
            kinds = [widget.kind for widget in (app.subscription_check, app.pending_queue, app.pending_ignore)]
            self.assertEqual(kinds.count('primary'), 1)
        # (final polish) No subscription selected: the solid button stays where the last selection put it.
        for selected, kinds in ((key, ('primary', 'soft')), (empty, ('soft', 'primary'))):
            app.subscription_tree.selection_set(selected)
            app.subscription_selection()
            app.subscription_tree.selection_remove(app.subscription_tree.selection())
            app.subscription_selection()
            self.assertEqual(app.subscription_tree.selection(), ())
            self.assertEqual((app.pending_queue.kind, app.subscription_check.kind), kinds, selected)
            self.assertEqual((app.pending_queue.cget('state'), app.subscription_check.cget('state')),
                             ('disabled', 'disabled'))

    def test_the_two_action_lines_end_on_one_edge(self):
        """(final polish) Check now and Add to downloads end on the same right edge; the '⋯' beside Check now is
        the only thing right of it."""
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        self.subscription(app, 'Night list', 3)
        app.refresh_library()
        pump(app, timeout=0.3)
        check, queue, menu = app.subscription_check, app.pending_queue, app.subscription_menu
        for widget in (check, queue, menu):
            self.assertTrue(widget.winfo_ismapped(), widget)
        self.assertLessEqual(abs(check.winfo_rootx() + check.winfo_width() - queue.winfo_rootx() - queue.winfo_width()), 1)
        self.assertLessEqual(abs(menu.winfo_rootx() + menu.winfo_width() - app.pending_bar.winfo_rootx()
                                 - app.pending_bar.winfo_width()), 1)

    def test_the_schedule_words(self):
        row = {'name': 'x', 'enabled': True, 'interval': 3600, 'next_check': 1000.0, 'pending': 0,
               'data': {'last_check': 0}}
        self.assertEqual(library_ui.subscription_row(row, now=999)[1], i18n.label('interval', 3600))
        self.assertEqual(library_ui.subscription_row(row, now=1000)[1], i18n.t('library.state.due'))
        self.assertEqual(library_ui.subscription_row(dict(row, interval=0), now=5000)[1], i18n.label('interval', 0))
        self.assertEqual(library_ui.subscription_row(dict(row, enabled=False), now=5000)[1],
                         i18n.t('library.state.paused'))
        self.assertEqual(library_ui.subscription_row(row, checking=True, now=5000)[1], i18n.t('library.state.checking'))
        self.assertEqual(library_ui.subscription_row(row)[2], i18n.t('library.never_checked'))
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        key = self.subscription(app, 'Night list', 1)
        app.refresh_library()
        self.assertEqual(app.subscription_tree.full_values(key)[1], i18n.label('interval', 3600))
        app.active_subscription = key
        try:
            app.refresh_subscription_rows()
            self.assertEqual(app.subscription_tree.full_values(key)[1], i18n.t('library.state.checking'))
        finally:
            app.active_subscription = None
        with mock.patch('library_ui.time.time', return_value=time.time() + 7200):
            app.refresh_subscription_rows()
        self.assertEqual(app.subscription_tree.full_values(key)[1], i18n.t('library.state.due'))

    def test_the_last_error_is_the_note(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        key = self.subscription(app, 'Night list', 1)
        app.library.finish_subscription(key, [], False)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        self.assertEqual(ui_theme.full_text(app.subscription_note), i18n.t('value.subscription.interrupted'))
        self.assertEqual(app.subscription_note.cget('text_color'), ui_theme.DANGER)

    def test_the_empty_state_goes_to_collect(self):
        app = self.library_app(library_ui.TAB_SUBSCRIPTIONS)
        app.refresh_library()
        empty = app.subscription_empty
        self.assertTrue(empty.winfo_manager())
        for widget in (app.subscription_bar, app.pending_bar, app.pending_tree.container):
            self.assertFalse(widget.winfo_manager())
        self.assertTrue(app.subscription_tree.container.winfo_manager(), 'under the empty state, for the focus')
        self.assertEqual(empty.hint_label.cget('text'), i18n.t('library.subscription.empty'))
        self.assertEqual(empty.action_button.cget('text'), i18n.t('library.subscription.go_collect'))
        self.assertEqual(entries(app.subscription_menu_items())[-1], (i18n.t('library.subscription.delete'), False))
        with mock.patch.object(app.library, 'subscription') as create:
            app.check_subscription()
            app.queue_subscription()
            app.ignore_pending()
            app.delete_subscription()
            app.rename_subscription()
        create.assert_not_called()
        self.assertEqual(ui_theme.full_text(app.subscription_note), '')
        empty.action_button.invoke()
        self.assertEqual(app.current_page, 'collect')
        self.assertTrue(inside(lastfocus(app), app.collect_urls))
        self.subscription(app)
        app.refresh_library()
        self.assertFalse(empty.winfo_manager())
        self.assertTrue(app.subscription_bar.winfo_manager())

    def test_subscribe_takes_the_defaults_of_the_settings_page(self):
        app = self.library_app(library_ui.TAB_HISTORY)
        app.collect_urls.delete('1.0', 'end')
        app.collect_urls.insert('1.0', 'https://example.org/podcast/list')
        app.subscription_interval.set(86400)
        app.subscription_media_only.set(False)
        app.subscription_first_new.set(False)
        app.save_subscription()
        rows = app.library.subscriptions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(messages.show(row['name']), 'https://example.org/podcast/list')
        self.assertEqual(row['interval'], 86400)
        self.assertEqual((row['data']['media_only'], row['data']['first_new']), (False, False))
        self.assertEqual(app.current_page, 'library')
        self.assertEqual(app.library_tabs.get(), library_ui.TAB_SUBSCRIPTIONS)
        self.assertEqual(app.subscription_tree.selection(), (row['id'],))
        self.assertFalse(hasattr(app, 'subscription_media'))
        self.assertFalse(hasattr(app, 'subscription_first'))
        self.assertFalse(hasattr(library_ui, 'INTERVALS'))


class LanguageTests(LibraryCase):
    def test_no_cjk_on_the_english_page(self):
        with _sandbox.language('en'):
            app = self.library_app(size='1000x740')
            self.job(app, 'interrupted', title='Night song')
            self.subscription(app, 'Night list', 2)
            self.archive(app, 1, uploader='')
            app.refresh_library()
            job = app.history_tree.get_children()[0]
            app.history_tree.selection_set(job)
            app.history_selection()
            app.archive_tree.selection_set('0')
            for view in VIEWS:
                app.library_tabs.set(view)
                pump(app, timeout=0.1)
                texts = []
                for widget in walk(app.pages['library']):
                    try:
                        texts.append(str(widget.cget('text')))
                    except (tk.TclError, ValueError, AttributeError):
                        pass
                    if isinstance(widget, ui_theme.AppTable):
                        texts += [str(widget.heading(column, 'text')) for column in widget['columns']]
                        texts += [str(cell) for iid in widget.get_children() for cell in widget.full_values(iid)]
                for items in (app.history_menu_items(), app.archive_menu_items(), app.subscription_menu_items(),
                              app.pending_menu_items()):
                    texts += [item.label for item in items if item is not None]
                    texts += [child.label for item in items if item is not None for child in item.children]
                found = [text for text in texts if CJK.search(text)]
                self.assertEqual(found, [], view)

    def test_the_toolbars_fit_at_the_minimum_size(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.library_app(size='1000x740')
                self.job(app, 'interrupted', title='Night song ' * 12)
                self.archive(app, 1)
                app.refresh_library()
                app.history_tree.selection_set(app.history_tree.get_children()[0])
                app.history_selection()
                app.library_waiting = True
                app.show_library_problem('')
                for view, bar, widgets in (
                        (library_ui.TAB_HISTORY, app.history_bar,
                         (app.history_search_entry, app.history_resume, app.history_menu)),
                        (library_ui.TAB_ARCHIVE, app.archive_bar,
                         (app.archive_search_entry, app.archive_open, app.archive_menu))):
                    app.library_tabs.set(view)
                    pump(app, timeout=0.3)
                    tab = app.library_tabs.tab(view)
                    right = tab.winfo_rootx() + tab.winfo_width()
                    self.assertEqual(bar.winfo_rootx(), tab.winfo_rootx(), 'aligned with the page edge')
                    for widget in widgets:
                        self.assertTrue(widget.winfo_ismapped())
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1)
                        if isinstance(widget, ctk.CTkButton):
                            self.assertGreaterEqual(widget.winfo_width() + 1, widget.winfo_reqwidth())
                    table = getattr(app, TABLES[view])
                    self.assertGreaterEqual(table.container.winfo_height(), ui_theme.TABLE_ROW_H * 5)
                header = app.pages['library'].header
                self.assertLessEqual(app.library_tabs.bar.winfo_rootx() + app.library_tabs.bar.winfo_width(),
                                     header.winfo_rootx() + header.winfo_width())
                app.library_waiting = False
                self.close(app)


if __name__ == '__main__':
    unittest.main()
