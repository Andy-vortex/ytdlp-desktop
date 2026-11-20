"""Which controls StudioApp.set_busy leaves usable, per operation (2.0.0 stage S0; final list S4 stage C).

    python -X utf8 -B test_busy_matrix.py

Replaces the SHA-256 pin of StudioApp.set_busy (test_protected_blocks.py, test_i18n_download.py) with
its behaviour, independent of the layout: for every operation that takes the window (download,
collect, update) set_busy(True) and then set_busy(False) on a real window, and after each call the
state of every control it governs, directly or through the refresh calls it makes:

  CONTROLS + UPDATE_BUTTONS      disabled while busy
  INPUT_CONTROLS                 disabled while busy, except during a download (links can be added)
  HOOKED                         in no group: a hook sets them from the busy state and a selection, a content
                                 or a mode (S4-DESIGN 3: shown and usable, control by control, in expected())
  ALWAYS                         usable at any time and in no group
  the menus                      every entry of every '⋯' menu (*_menu_items), per combination
  the solid buttons              at most one shown primary button per view; the stage decides which

and that every hook of set_busy's final shape (00-system 4.7) runs on every call: refresh_failed_button,
on_format, refresh_collect_actions, collection_selection_changed, history_selection (and through it
archive_selection and subscription_tools), refresh_shell.  Two windows: one where every control has
something to act on ("rich"), one where nothing is selected or stored.  States only, never texts.

(2.0.0 S2 P1) Also what refresh_shell does on every set_busy call: refresh_foreign_activity, refresh_start
and refresh_settings_controls run once per call, the start button reads i18n.t('download.start.queue')
during a download and i18n.t('download.start') otherwise, and the language notice follows the busy state
(settings.language.restart_busy without a button while busy, settings.language.restart_note with one usable
立即重启 when idle).

(S0 review fix) The expected states do not come from app.controls / input_controls / update_buttons:
CONTROLS, INPUT_CONTROLS and UPDATE_BUTTONS below name every control by itself (its attribute, else the
app variable it edits, else the app method it calls), the matrix finds each one by walking the window's
widget tree, and test_the_groups_hold_exactly_these_controls pins the three groups to these lists.
Moving a control into, out of or between the groups fails here.  The matrix covers exactly the controls
of CONTROLS + INPUT_CONTROLS + UPDATE_BUTTONS + HOOKED (23 + 2 + 1 + 17 = 43), no more and no fewer.
A layout change that adds, removes or regroups a control updates these lists and says so, control by
control, in its handoff note (S4: docs/2.0.0/handoff/S4/S4.md, section C).
"""
import _sandbox
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import formats
import i18n
import library_ui
import ui_theme
import unfinished
import updater
from studio import StudioApp
from _sandbox import no_network, no_clipboard

OPERATIONS = ('download', 'collect', 'update')
# (2.0.0 S4 A2) every hook of set_busy's final shape (00-system 4.7), each called once per set_busy call
REFRESHES = ('refresh_failed_button', 'on_format', 'refresh_collect_actions', 'collection_selection_changed',
             'history_selection', 'archive_selection', 'subscription_tools', 'refresh_shell')
# (2.0.0 S2 P1) called by refresh_shell on every set_busy call (S4 A2: refresh_start; refresh_engine_controls renamed)
SHELL_REFRESHES = ('refresh_foreign_activity', 'refresh_start', 'refresh_settings_controls')
LINK = 'https://media.example.invalid/clip.mp4'
PAGE = 'https://media.example.invalid/feed'

# Disabled while busy, usable when idle (23).
CONTROLS = (
    # download (4)
    'attr:format_menu', 'attr:subtitle_check', 'variable:playlist', 'attr:output_button',
    # collect (7)
    'attr:collect_urls', 'attr:collect_mode_menu', 'attr:collect_scrolls_menu', 'attr:collect_pages_menu',
    'attr:collect_scope', 'attr:collect_subscribe', 'attr:collect_start',
    # settings (12)
    'attr:naming_menu', 'attr:archive_switch', 'attr:archive_folder_button', 'attr:tags_switch',
    'attr:cover_switch', 'attr:skip_switch', 'attr:batch_switch', 'attr:subscription_media_switch',
    'attr:subscription_first_switch', 'attr:subscription_interval_menu', 'attr:proxy_entry', 'attr:cookies_button',
)
# Disabled while busy, usable during a download (links can still be added) (2).
INPUT_CONTROLS = ('attr:urls', 'command:paste')         # command:paste = the 粘贴 button of the empty download page
# Disabled while busy (1).
UPDATE_BUTTONS = ('attr:check_update_button',)
# In no group: set by a hook (17); expected() says what each one is, from S4-DESIGN 3.
HOOKED = ('start_button', 'stop_button', 'retry_button', 'task_menu', 'quality_menu',
          'collect_stop', 'collect_add', 'collect_menu',
          'history_resume', 'archive_open', 'subscription_check', 'subscription_menu', 'pending_queue', 'pending_ignore',
          'restore_button', 'stop_update_button', 'clear_cookies_button')
# Usable at any time and in no group (16).
ALWAYS = ('open_output_button', 'link_menu', 'collect_search_entry', 'collect_filter_menu', 'history_search_entry',
          'history_menu', 'archive_search_entry', 'archive_menu', 'log_filter', 'log_copy_button', 'log_menu',
          'daily_switch', 'notify_switch', 'clipboard_switch', 'appearance_choice', 'language_choice')
# In no group and not in the matrix: the '⋯' of the selected task row (shown with a selected row only).
UNGROUPED = HOOKED + ALWAYS + ('row_menu',)
# (final review, honesty 4) The operable controls of the window that are in no list above, each with the reason
# set_busy leaves it alone: navigation or read-only.  test_no_operable_control_is_left_out pins the window to
# UNGROUPED + the three groups + these, so a new button that joins no list fails there.
NAVIGATION = {
    'nav_buttons': 'the five sidebar pages: moving between pages is always allowed',
    'engine_notice': 'the sidebar line that opens Settings when a newer engine is known: navigation',
    'library_tabs.bar': 'the library view switch (history / subscriptions / archive): navigation',
    'logs': 'the log text: read-only (copy and search only)',
    'history_more': 'loads more history rows into the table: reads the library, changes nothing',
    'archive_empty.action_button': "the archive empty state's button: opens Settings → Files",
    'subscription_empty.action_button': "the subscriptions empty state's button: opens the collect page",
}
OPERABLE = (ctk.CTkButton, ctk.CTkEntry, ctk.CTkSwitch, ctk.CTkCheckBox, ctk.CTkOptionMenu, ctk.CTkSegmentedButton,
            ctk.CTkTextbox)


def state(widget):
    return str(widget.cget('state'))


def key_of(app, widget):
    """'attr:<name>' of an app attribute, else 'variable:<name>' of the app variable the widget
    edits, else 'command:<name>' of the app method it calls; None for anything else."""
    names = sorted(name for name, value in vars(app).items() if value is widget)
    if names:
        return 'attr:' + names[0]
    for slot in ('_variable', '_textvariable', '_value_variable'):
        variable = getattr(widget, slot, None)
        if variable is not None:
            names = sorted(name for name, value in vars(app).items() if value is variable)
            if names:
                return 'variable:' + names[0]
    command = getattr(widget, '_command', None)
    if command is not None and getattr(command, '__self__', None) is app:
        return 'command:' + command.__name__
    return None


def _descendants(widget):
    todo = [widget]
    while todo:
        widget = todo.pop()
        todo.extend(widget.winfo_children())
        yield widget


def _neighbour(app, widget):
    """The first (sorted) app attribute among the widgets of the nearest enclosing frame."""
    attributes = {id(value): name for name, value in sorted(vars(app).items(), reverse=True)}
    master = widget.master
    while master is not None:
        names = sorted(attributes[id(other)] for other in _descendants(master)
                       if other is not widget and id(other) in attributes)
        if names:
            return names[0]
        master = master.master
    return '?'


def widgets_by_key(app):
    """{key: [widgets]} of every customtkinter widget in the window, found through the widget
    tree (not through app.controls and the other groups).  When two widgets get the same key
    each gets '@<neighbour>': the first app attribute next to it."""
    found = {}
    for widget in _descendants(app):
        if isinstance(widget, ctk.CTkBaseClass):
            key = key_of(app, widget)
            if key is not None:
                found.setdefault(key, []).append(widget)
    for key, widgets in list(found.items()):
        if len(widgets) > 1:
            del found[key]
            for widget in widgets:
                found.setdefault(f'{key}@{_neighbour(app, widget)}', []).append(widget)
    return found


def group_keys(app, members):
    keys = {id(widget): key for key, widgets in widgets_by_key(app).items() for widget in widgets}
    return [keys.get(id(widget), f'unknown {type(widget).__name__}') for widget in members]


def views(app):
    """{name: frame} of every view: the four pages and the three library views (00-system 1.1)."""
    found = {name: app.pages[name] for name in ('download', 'collect', 'logs', 'settings')}
    found.update({f'library/{name}': frame for name, frame in app.library_tabs._frames.items()})
    return found


def shown(widget, stop):
    """``widget`` and every master up to ``stop`` are managed: the control is on its view."""
    while widget is not None and widget is not stop:
        if not widget.winfo_manager():
            return False
        widget = widget.master
    return True


def view_of(app, widget):
    frames = views(app).values()
    master = widget
    while master is not None and master not in frames:
        master = master.master
    return master


def visible(app, widget):
    return shown(widget, view_of(app, widget))


def enabled(items):
    """The enabled flags of a menu, separators left out."""
    return [item.enabled for item in items if item is not None]


class BusyMatrixTests(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard),
                        mock.patch.object(engine, 'PACE_SECONDS', (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        unfinished.save([])
        settings_file = _sandbox.state_dir() / 'settings.json'
        if settings_file.exists():
            settings_file.unlink()

    def make_app(self, rich):
        _sandbox.write_settings()
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args())
        app.withdraw()
        self.addCleanup(self.close, app)
        app.update_idletasks()
        if rich:
            # something for every control to act on (S4-DESIGN 3: the data of the rich window)
            app.unfinished = [unfinished.entry(LINK, engine.Options(_sandbox.out('downloads')).to_dict(), 'failed')]
            app.populate_queue([LINK])
            app.update_row(0, state='failed', detail='x')
            app.select_row(0)
            app.mode.set(formats.MODES[4])                              # a video format (2.0.0 S4 A1: the id)
            app.on_format()
            app.urls.insert('1.0', LINK)
            app.links_changed()
            app.engine_updated = True
            app.cookies.set(str(_sandbox.out('cookies.txt')))
            store = app.library
            job = store.create_job('download', 'Matrix job', dict(
                engine.Options(_sandbox.out('downloads')).to_dict(), urls=[LINK]))
            store.change_job(job, status='partial')
            store.archive_index(str(_sandbox.out('matrix-archive', 'item-1')),
                                {'url': LINK, 'source': PAGE, 'title': 'Clip', 'uploader': '', 'files': ['a.m4a'],
                                 'transcripts': []})
            key = store.subscription('Matrix feed', {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '',
                                                     'max_pages': 1}, 3600, True, True)
            pending = [{'url': f'{PAGE}/item-{n}.mp3', 'title': f'Item {n}', 'kind': 'media', 'source': PAGE}
                       for n in range(2)]
            store.finish_subscription(key, [{'url': PAGE, 'title': 'Feed', 'text': '', 'markdown': '',
                                             'links': pending}], True)
            app.refresh_library()
            self.assertIn(job, app.history_tree.get_children())
            app.history_tree.selection_set(job)
            archived = app.archive_tree.get_children()
            self.assertEqual(len(archived), 1)
            app.archive_tree.selection_set(archived[0])
            self.assertIn(str(key), app.subscription_tree.get_children())
            app.subscription_tree.selection_set(str(key))
            app.subscription_selection()
            self.assertEqual(len(app.pending_tree.get_children()), 2)
            app.pending_tree.selection_set(app.pending_tree.get_children()[0])
            app.collected_pages = [{'url': PAGE, 'title': 'Feed', 'text': 'Some page text', 'markdown': '',
                                    'links': []}]
            app.collected_links = {LINK: {'url': LINK, 'title': 'Clip', 'kind': 'media', 'source': LINK}}
            app.render_collection_links()
            app.collect_tree.selection_set('0')
            app.append_log('matrix line')
            app.flush_log()
        else:
            app.clear_log_view()
        app.set_busy(False)
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    def expected(self, app, rich, busy, operation):
        """{widget: 'normal' | 'disabled'} of every governed control (S4-DESIGN 3)."""
        idle = not busy
        adds = idle or operation == 'download'           # links can be added while idle or during a download
        on = {True: 'normal', False: 'disabled'}
        wanted = {}
        found = widgets_by_key(app)

        def widget(key):
            self.assertEqual(len(found.get(key, [])), 1, f'exactly one widget is {key}')
            return found[key][0]
        for key in CONTROLS + UPDATE_BUTTONS:
            wanted[widget(key)] = on[idle]
        for key in INPUT_CONTROLS:
            wanted[widget(key)] = on[adds]
        wanted[app.subtitle_check] = on[rich and idle]           # on_format: a video format only
        wanted[app.start_button] = on[rich and adds]             # links in the box
        wanted[app.stop_button] = on[busy]
        wanted[app.retry_button] = on[rich and idle]
        wanted[app.task_menu] = 'normal'
        wanted[app.quality_menu] = on[rich and idle]
        wanted[app.collect_stop] = on[busy and operation == 'collect']
        wanted[app.collect_add] = on[rich and adds]
        wanted[app.collect_menu] = 'normal'
        wanted[app.history_resume] = on[rich and idle]
        wanted[app.archive_open] = on[rich]                      # not the busy state (library.md 3.3)
        wanted[app.subscription_check] = on[rich and idle]
        wanted[app.subscription_menu] = 'normal'
        wanted[app.pending_queue] = on[rich and adds]
        wanted[app.pending_ignore] = on[rich and idle]
        wanted[app.restore_button] = on[rich and idle]
        wanted[app.stop_update_button] = 'normal'                 # shown only while an update runs (expected_shown)
        wanted[app.clear_cookies_button] = on[rich and idle]
        return wanted

    def expected_shown(self, app, rich, busy, operation):
        """{name: shown} of the controls whose place depends on the state (S4-DESIGN 3, column 显示)."""
        updating = busy and operation == 'update'
        collecting = busy and operation == 'collect'
        return {'start_button': True, 'stop_button': busy, 'retry_button': rich, 'task_menu': rich,
                'quality_menu': rich, 'collect_start': not collecting, 'collect_stop': collecting,
                'collect_add': rich, 'collect_menu': rich, 'history_resume': rich, 'archive_open': rich,
                'subscription_check': rich, 'subscription_menu': rich, 'pending_queue': rich, 'pending_ignore': rich,
                'restore_button': rich, 'stop_update_button': updating, 'check_update_button': not updating,
                'clear_cookies_button': True}

    def expected_menus(self, app, rich, busy, operation):
        """{menu: enabled flags} of every '⋯' menu (separators left out)."""
        idle = not busy
        adds = idle or operation == 'download'
        collecting = busy and operation == 'collect'
        menus = {
            'link_menu_items': [adds, adds, rich, rich and adds],
            'task_menu_items': [rich and idle],             # (final polish) 清除未完成 only
            'collect_menu_items': [rich and adds, rich, rich, rich and not collecting, rich and idle],
            'history_menu_items': [rich and idle, rich and idle, True],
            'archive_menu_items': [rich, rich and idle, True],
            'subscription_menu_items': [rich and idle] * 4 + [True, rich and idle],
            'pending_menu_items': [rich and adds, rich and idle],
            'log_menu_items': [True, True, rich],
        }
        if rich:
            # 播放, 在文件夹中显示, 复制链接, 查看原因, 重试, 移除 of a failed row
            menus['row_menu_items'] = [False, False, True, True, adds, idle]
        return menus

    def check(self, app, rich, busy, operation):
        wanted = self.expected(app, rich, busy, operation)
        found = widgets_by_key(app)
        listed = {found[key][0] for key in CONTROLS + INPUT_CONTROLS + UPDATE_BUTTONS} | {
            getattr(app, name) for name in HOOKED}
        self.assertEqual(len(wanted), len(CONTROLS) + len(INPUT_CONTROLS) + len(UPDATE_BUTTONS) + len(HOOKED))
        self.assertEqual(len(wanted), 43)
        self.assertEqual(set(wanted), listed, 'the matrix covers exactly the listed controls')
        where = f'rich={rich} busy={busy} operation={operation}'
        wrong = [(self.name_of(app, widget), state(widget), value) for widget, value in wanted.items()
                 if state(widget) != value]
        self.assertEqual(wrong, [], f'{where}: (control, state, expected)')
        for name in ALWAYS:
            self.assertEqual(state(getattr(app, name)), 'normal', f'{where}: {name} is always usable')
            if rich:        # (final review, honesty 4) with something to act on, each one is also on its view
                self.assertTrue(visible(app, getattr(app, name)), f'{where}: {name} is shown')
        wrong = [(name, visible(app, getattr(app, name)), value)
                 for name, value in self.expected_shown(app, rich, busy, operation).items()
                 if visible(app, getattr(app, name)) != value]
        self.assertEqual(wrong, [], f'{where}: (control, shown, expected)')
        for name, value in self.expected_menus(app, rich, busy, operation).items():
            items = getattr(app, name)(0) if name == 'row_menu_items' else getattr(app, name)()
            self.assertEqual(enabled(items), value, f'{where}: {name}')
        # 删除订阅 is grey while that subscription is being checked, whatever else
        if rich:
            row = app.selected_subscription()
            with mock.patch.object(app, 'active_subscription', row['id']):
                self.assertFalse(app.subscription_menu_items()[-1].enabled, f'{where}: delete while checking')
        self.check_solid_buttons(app, rich, busy, operation)
        self.assertIs(app.busy, busy)
        if not busy:
            self.assertIsNone(app.worker)

    def check_solid_buttons(self, app, rich, busy, operation):
        """At most one shown primary button per view; with results / new items the add button is the solid one."""
        where = f'rich={rich} busy={busy} operation={operation}'
        for name, frame in views(app).items():
            solid = [widget for widget in _descendants(frame)
                     if isinstance(widget, ui_theme.AppButton) and widget.kind == 'primary' and shown(widget, frame)]
            self.assertLessEqual(len(solid), 1, f'{where}: {name} has {len(solid)} solid buttons')
        if rich:
            self.assertEqual((app.collect_add.kind, app.collect_start.kind), ('primary', 'soft'), where)
            self.assertEqual((app.pending_queue.kind, app.subscription_check.kind), ('primary', 'soft'), where)

    @staticmethod
    def name_of(app, widget):
        for name, value in vars(app).items():
            if value is widget:
                return name
        for group in ('controls', 'update_buttons', 'input_controls'):
            if widget in getattr(app, group):
                return f'{group}[{getattr(app, group).index(widget)}] {type(widget).__name__}'
        return type(widget).__name__

    def run_matrix(self, rich):
        app = self.make_app(rich)
        self.check(app, rich, False, None)
        for operation in OPERATIONS:
            with self.subTest(rich=rich, operation=operation):
                app.operation = operation
                app.worker = object()
                calls = {}
                patches = [mock.patch.object(app, name, wraps=getattr(app, name))
                           for name in REFRESHES + SHELL_REFRESHES]
                for patcher in patches:
                    calls[patcher.attribute] = patcher.start()
                try:
                    app.set_busy(True)
                    self.check(app, rich, True, operation)
                    self.assertIsNotNone(app.worker, 'set_busy(True) keeps the worker')
                    app.set_busy(False)
                    self.check(app, rich, False, operation)
                finally:
                    for patcher in patches:
                        patcher.stop()
                for name, spy in calls.items():
                    self.assertEqual(spy.call_count, 2, f'{name} runs on every set_busy call')
        return app

    def test_every_control_with_something_to_act_on(self):
        app = self.run_matrix(True)
        # the archive folder follows the busy state only, with the archive switch on and off
        for archive in (True, False):
            app.archive_enabled.set(archive)
            for busy in (True, False):
                with self.subTest(archive=archive, busy=busy):
                    app.operation = 'download' if busy else None
                    app.worker = object() if busy else None
                    app.set_busy(busy)
                    self.assertEqual(state(app.archive_folder_button), 'disabled' if busy else 'normal')

    def test_every_control_with_nothing_selected_or_stored(self):
        self.run_matrix(False)

    def test_the_solid_button_of_the_download_page_moves_with_the_stage(self):
        """Box empty, unfinished links, idle: 重试未完成 (N) is the solid button; links in the box or busy: 开始下载."""
        app = self.make_app(False)
        app.unfinished = [unfinished.entry(LINK, engine.Options(_sandbox.out('downloads')).to_dict(), 'failed')]
        app.refresh_failed_button()
        self.assertEqual((app.retry_button.kind, app.start_button.kind), ('primary', 'soft'))
        app.urls.insert('1.0', LINK)
        app.links_changed()
        self.assertEqual((app.retry_button.kind, app.start_button.kind), ('soft', 'primary'))
        app.urls.delete('1.0', 'end')
        app.links_changed()
        self.assertEqual((app.retry_button.kind, app.start_button.kind), ('primary', 'soft'))
        app.operation = 'download'
        app.worker = object()
        app.set_busy(True)
        self.assertEqual((app.retry_button.kind, app.start_button.kind), ('soft', 'primary'))
        app.set_busy(False)
        self.assertEqual((app.retry_button.kind, app.start_button.kind), ('primary', 'soft'))

    def test_refresh_shell_follows_every_set_busy_call(self):
        """(2.0.0 S2 P1) The start button's text and the language notice, per operation."""
        app = self.make_app(False)
        smoke = bool(app.smoke_mode)
        app.language_saved = True
        app.language_notice_kind = 'restart'
        app.show_language_notice()

        def notice(busy):
            self.assertTrue(app.language_notice.shown)
            self.assertEqual(app.language_notice.full_text,
                             i18n.t('settings.language.restart_busy' if busy else 'settings.language.restart_note'))
            if busy or smoke:
                self.assertEqual(tuple(app.language_notice.buttons), ())
            else:
                self.assertEqual(len(app.language_notice.buttons), 1)
                self.assertEqual(state(app.language_notice.buttons[0]), 'normal')
                self.assertEqual(app.language_notice.buttons[0].cget('text'), i18n.t('settings.language.restart'))
        notice(False)
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                app.operation = operation
                app.worker = object()
                app.set_busy(True)
                start = 'download.start.queue' if operation == 'download' else 'download.start'
                self.assertEqual(app.start_button.cget('text').strip(), i18n.t(start))
                notice(True)
                app.set_busy(False)
                self.assertEqual(app.start_button.cget('text').strip(), i18n.t('download.start'))
                notice(False)

    def test_the_groups_hold_exactly_these_controls(self):
        app = self.make_app(False)
        found = widgets_by_key(app)
        self.assertEqual((len(CONTROLS), len(INPUT_CONTROLS), len(UPDATE_BUTTONS), len(HOOKED), len(ALWAYS)),
                         (23, 2, 1, 17, 16))
        for group, keys in (('controls', CONTROLS), ('input_controls', INPUT_CONTROLS),
                            ('update_buttons', UPDATE_BUTTONS)):
            members = getattr(app, group)
            self.assertEqual(len(members), len(set(map(id, members))), f'{group} has no duplicates')
            self.assertEqual(sorted(group_keys(app, members)), sorted(keys), group)
            for key in keys:
                self.assertEqual(len(found.get(key, [])), 1, f'{group}: exactly one widget is {key}')
                self.assertIn(found[key][0], members, f'{group}: {key}')
        grouped = app.controls + app.update_buttons + app.input_controls
        for name in UNGROUPED:
            self.assertNotIn(getattr(app, name), grouped, name)
        self.assertIsInstance(app.stop_button, ui_theme.AppButton)
        self.assertEqual(app.paste.__self__, app)
        self.assertIs(found['command:paste'][0], app.empty.action_button, 'command:paste is the empty state button')
        self.assertIs(app.library_tabs._frames[library_ui.TAB_ARCHIVE], view_of(app, app.archive_open))


    def check_no_operable_control_is_left_out(self, rich):
        app = self.make_app(rich)
        found = widgets_by_key(app)
        known = {id(found[key][0]) for key in CONTROLS + INPUT_CONTROLS + UPDATE_BUTTONS}
        known |= {id(getattr(app, name)) for name in UNGROUPED}
        for name in NAVIGATION:
            value = app
            for part in name.split('.'):
                value = getattr(value, part)
            known |= {id(widget) for widget in (value.values() if isinstance(value, dict) else [value])}
        left_out = [f'{type(widget).__name__} next to {_neighbour(app, widget)}' for widget in _descendants(app)
                    if isinstance(widget, OPERABLE) and not isinstance(widget.master, ctk.CTkSegmentedButton)
                    and id(widget) not in known]
        self.assertEqual(left_out, [], 'an operable control in no list: add it to a group, HOOKED, ALWAYS or NAVIGATION')

    def test_no_operable_control_is_left_out_with_something_to_act_on(self):
        self.check_no_operable_control_is_left_out(True)

    def test_no_operable_control_is_left_out_with_nothing(self):
        self.check_no_operable_control_is_left_out(False)


if __name__ == '__main__':
    unittest.main()
