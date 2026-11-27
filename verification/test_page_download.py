"""The download page of 2.0.0 (S4 B1: download-input.md, download-tasks.md).

    python -X utf8 -B test_page_download.py

Every action of the page has a visible control: the link box's '⋯' (link_menu_items), the task header's
'⋯' (task_menu_items) and the selected row's '⋯' (row_menu_items); these tests read the menus from their
generators (labels and enabled states) without opening them.  Also: the start button is grey without a
link and the one solid button moves with the stage, the folder button, Ctrl+O and Ctrl+Z in the link box,
the line under the box, the clipboard notice, the task area's parts (header, buttons, progress, rows, empty
state) and its titles, the notices, and the layout at the minimum window in both languages.  Smoke-mode
windows (alpha 0, no frame), no network, no real engine, no clipboard.
"""
import _sandbox
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import i18n
import ui_download
import ui_theme
import updater
from studio import StudioApp

from _sandbox import en_t, no_clipboard, no_network, pump, zh_t

LINK = 'https://example.org/clip.mp4'


class FakeRunner:
    """Stands in for engine.Runner: download() waits until released."""

    def __init__(self, emit, library=None):
        self.emit, self.library = emit, library
        self.origins = {}
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def download(self, urls, options, overrides=None):
        self.release.wait(10)

    def enqueue(self, urls, options=None, overrides=None):
        return list(urls)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def entry(number, reason='failed'):
    return {'url': f'https://example.org/item-{number}', 'key': f'example.org/item-{number}', 'format': 'audio_mp3',
            'reason': reason, 'options': {}, 'time': number}


def labels(items):
    return [item.label for item in items if item is not ui_theme.SEPARATOR]


def enabled(items):
    return [item.enabled for item in items if item is not ui_theme.SEPARATOR]


class AppCase(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_app(self, lang='zh', size='1000x740', visible=True):
        for earlier in getattr(self, '_apps', []):
            self.close(earlier)
        self._apps = []
        context = _sandbox.language(lang)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/smoke', str(_sandbox.out('page-download'))))
        self.addCleanup(self.close, app)
        self._apps.append(app)
        if visible:
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            app.geometry(size + '+0+0')
        else:
            app.withdraw()
        app.urls.delete('1.0', 'end')
        app.links_changed()
        pump(app, timeout=0.3)
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def shown(widget):
        # a ScrollArea (the task list) is gridded through its outer frame
        return bool(getattr(widget, '_parent_frame', widget).winfo_manager())

    def busy(self, app, operation):
        app.operation = operation
        app.set_busy(True)

    def idle(self, app):
        app.set_busy(False)
        app.operation = None
        app.refresh_shell()


# ============================================================================ input area
class LinkBoxTests(AppCase):

    def test_link_menu_labels_and_states(self):
        for lang, tr in (('zh', zh_t), ('en', en_t)):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                self.assertEqual(app.link_menu.menu_items, app.link_menu_items, 'the ⋯ opens the same generator')
                items = app.link_menu_items()
                self.assertEqual(labels(items), [tr('download.paste'), tr('download.import'),
                                                 tr('common.select_all'), tr('download.clear_box')])
                self.assertIs(items[2], ui_theme.SEPARATOR, 'add, then work on the text')
                self.assertTrue(items[-1].danger, '清空 is red and last')
                self.assertEqual([item.command for item in items if item],
                                 [app.paste, app.import_txt, app.select_all_links, app.clear_links])
                # empty box, idle: add yes, select / clear no
                self.assertEqual(enabled(app.link_menu_items()), [True, True, False, False])
                app.urls.insert('1.0', LINK)
                for operation, expected in ((None, [True, True, True, True]), ('download', [True, True, True, True]),
                                            ('collect', [False, False, True, False]),
                                            ('update', [False, False, True, False])):
                    if operation:
                        self.busy(app, operation)
                    self.assertEqual(enabled(app.link_menu_items()), expected, operation)
                    if operation:
                        self.idle(app)
                self.close(app)

    def test_select_all_and_right_click_mirror(self):
        app = self.make_app()
        app.urls.insert('1.0', LINK)
        app.select_all_links()
        self.assertEqual(app.urls.get('sel.first', 'sel.last'), LINK)
        # a right click on the box, on its frame or on its corner opens the same menu as its '⋯'
        for target in (app.urls._textbox, app.link_box._canvas, app.link_tools._canvas):
            with mock.patch.object(ui_theme, 'popup') as popup:
                target.event_generate('<Button-3>', x=4, y=4)
                pump(app, timeout=0.05)
            self.assertEqual(popup.call_count, 1, target)
            self.assertEqual(popup.call_args[0][1], app.link_menu_items)

    def test_count_chip_shows_the_number_only_and_hides_at_zero(self):
        app = self.make_app(visible=False)
        self.assertFalse(self.shown(app.link_count))
        app.urls.insert('1.0', 'https://example.org/a\nhttps://example.org/b\nhttps://example.org/c')
        app.links_changed()
        self.assertTrue(self.shown(app.link_count))
        self.assertEqual(app.link_count.cget('text'), '3')
        app.clear_links()
        self.assertFalse(self.shown(app.link_count))

    def test_clear_can_be_undone(self):
        app = self.make_app(visible=False)
        self.assertEqual(str(app.urls._textbox.cget('undo')), '1')
        app.urls.insert('1.0', 'https://example.org/a\nhttps://example.org/b')
        app.links_changed()
        app.clear_links()
        self.assertEqual(app.box_text(), '')
        app.urls.edit_undo()            # Ctrl+Z in the box
        self.assertEqual(app.box_text(), 'https://example.org/a\nhttps://example.org/b')
        # a paste is one step too
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, *a, **k: LINK):
            app.paste()
        self.assertTrue(app.box_text().endswith(LINK))
        app.urls.edit_undo()
        self.assertEqual(app.box_text(), 'https://example.org/a\nhttps://example.org/b')

    def test_ctrl_o_imports_and_never_opens_a_line(self):
        app = self.make_app()
        app.urls.insert('1.0', LINK)
        with mock.patch.object(app, 'import_txt') as importer:
            app.urls.focus_force()
            app.urls.mark_set('insert', '1.0')
            pump(app, timeout=0.05)
            app.urls._textbox.event_generate('<Control-o>')
            pump(app, timeout=0.05)
            if not importer.called:         # a generated key event needs the keyboard focus (another program)
                self.assertEqual(app._import_from_box(), 'break')
            self.assertEqual(importer.call_count, 1)
        self.assertEqual(app.box_text(), LINK, 'Tk\'s Ctrl+O (open a line) does not run in the box')
        self.assertTrue(app.urls._textbox.bind('<Control-Key-o>'))
        # the window's Ctrl+O on the download page, and the busy rule of 导入 TXT…
        self.busy(app, 'collect')
        with mock.patch.object(ui_download.filedialog, 'askopenfilename') as dialog:
            app.import_txt()
        dialog.assert_not_called()
        self.assertEqual(app.link_note.cget('text'), i18n.t('download.hint.busy'))
        self.idle(app)

    def test_the_line_under_the_box(self):
        app = self.make_app(visible=False)
        self.assertFalse(self.shown(app.link_note))
        app.urls.insert('1.0', 'https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk')
        app.links_changed()
        self.assertTrue(self.shown(app.link_note))
        self.assertEqual(app.link_note.cget('text'), zh_t('download.note.lists_dropped', count=1))
        self.assertEqual(app.link_note.cget('text_color'), ui_theme.WARN, 'what will be skipped: WARN')
        # a one-off feedback comes first, then the lasting note again
        app.set_hint(zh_t('download.hint.added', added=1))
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.added', added=1))
        self.assertEqual(app.link_note.cget('text_color'), ui_theme.ACCENT_TEXT)
        app.set_hint('')
        self.assertEqual(app.link_note.cget('text'), zh_t('download.note.lists_dropped', count=1))
        app.clear_links()
        self.assertFalse(self.shown(app.link_note))
        # a subscription check: the note stays until the check ends (collection_ui clears it)
        app.hint_subscription_check('每日')
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.subscription_check', name='每日'))
        self.assertIsNone(app.hint_after, 'no timer: it lasts until set_hint(\'\')')
        app.set_hint('')
        self.assertFalse(self.shown(app.link_note))
        # a reason is wrapped, never cut (ui_theme.wrap_to_width)
        self.assertTrue(getattr(app.link_note, '_wrap_bound', False))

    def test_clipboard_notice(self):
        app = self.make_app(visible=False)
        app.current_page = 'download'
        app.clipboard_hint.set(True)
        with mock.patch.object(app, 'smoke_mode', False), \
                mock.patch.object(StudioApp, 'clipboard_get', lambda self, *a, **k: 'see ' + LINK):
            app.check_clipboard()
            self.assertTrue(app.clip_bar.shown)
            self.assertEqual(app.clip_bar.full_text, zh_t('download.clip.found', count=1, title=ui_download.link_title(LINK)))
            self.assertEqual([button.cget('text') for button in app.clip_bar.buttons],
                             [zh_t('download.clip.add'), zh_t('common.ignore')])
            # under the box: it never pushes the box down
            self.assertGreater(int(app.clip_bar.grid_info()['row']), int(app.link_box.grid_info()['row']))
            with mock.patch.object(app, 'schedule_save'):
                app.clip_bar.buttons[0].invoke()            # 添加
            self.assertFalse(app.clip_bar.shown)
            self.assertEqual(app.box_text(), LINK)
            # another activity holds the window: no offer
            self.busy(app, 'update')
            app.clip_seen = ''
            app.clear_links()
            app.check_clipboard()
            self.assertFalse(app.clip_bar.shown)
            self.idle(app)

    def test_folder_button_follows_the_folder_and_the_archive(self):
        app = self.make_app(visible=False)
        folder = str(_sandbox.out('page-download', 'Downloads'))
        app.output.set(folder)
        self.assertEqual(app.output_button.path, folder)
        self.assertEqual(app.output_button._icon_code, ui_theme.ICON_FOLDER)
        archive = str(_sandbox.out('page-download', 'Archive'))
        app.archive_root.set(archive)
        app.archive_enabled.set(True)
        self.assertEqual(app.output_button.path, archive)
        self.assertEqual(app.output_button._icon_code, ui_theme.ICON_ARCHIVE)
        # the archive folder is chosen in Settings only
        with mock.patch.object(app, 'show_settings') as settings, \
                mock.patch.object(ui_download.filedialog, 'askdirectory') as dialog:
            app.output_button.invoke()
        settings.assert_called_once_with('downloads')
        dialog.assert_not_called()
        app.archive_enabled.set(False)
        self.assertEqual(app.output_button.path, folder)
        with mock.patch.object(ui_download.filedialog, 'askdirectory', return_value=archive) as dialog:
            app.output_button.invoke()
        self.assertEqual(app.output.get(), archive)
        self.assertEqual(app.output_button.path, archive)
        # usable only while idle (controls); 打开文件夹 always
        self.busy(app, 'download')
        self.assertEqual(str(app.output_button.cget('state')), 'disabled')
        self.assertEqual(str(app.open_output_button.cget('state')), 'normal')
        self.idle(app)
        self.assertEqual(str(app.output_button.cget('state')), 'normal')
        self.assertFalse(hasattr(app, 'output_entry') or hasattr(app, 'destination_label'))


class StartButtonTests(AppCase):

    def test_grey_without_a_link_and_no_dialog(self):
        app = self.make_app(visible=False)
        self.assertEqual(str(app.start_button.cget('state')), 'disabled')
        with mock.patch.object(ui_download.messagebox, 'showwarning') as warning, \
                mock.patch.object(app, 'launch') as launch:
            app.start()
            app._start_from_box()
        warning.assert_not_called()
        launch.assert_not_called()
        app.urls.insert('1.0', LINK)
        app.links_changed()
        self.assertEqual(str(app.start_button.cget('state')), 'normal')
        # typed and started before the pause ends: the count is taken at once
        app.clear_links()
        app.urls.insert('1.0', LINK)
        with mock.patch.object(app, 'launch', return_value=False) as launch:
            app._start_from_box()
        self.assertEqual(launch.call_count, 1)

    def test_states_per_activity(self):
        app = self.make_app(visible=False)
        app.urls.insert('1.0', LINK)
        app.links_changed()
        for operation, text, state in (('download', 'download.start.queue', 'normal'),
                                       ('collect', 'download.start', 'disabled'),
                                       ('update', 'download.start', 'disabled')):
            self.busy(app, operation)
            self.assertEqual((app.start_button.cget('text'), str(app.start_button.cget('state'))),
                             (zh_t(text), state), operation)
            self.idle(app)
        self.assertEqual(str(app.start_button.cget('state')), 'normal')
        self.assertNotIn(app.start_button, app.input_controls, 'only refresh_start sets it')

    def test_the_solid_button_moves_with_the_stage(self):
        app = self.make_app(visible=False)
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('primary', 'soft'))
        app.unfinished = [entry(1), entry(2)]
        app.show_unfinished_rows()
        app.refresh_failed_button()
        # an empty box, idle, unfinished links: 重试未完成 (N) is the one solid button
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('soft', 'primary'))
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=2))
        app.urls.insert('1.0', LINK)
        app.links_changed()
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('primary', 'soft'))
        app.clear_links()
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('soft', 'primary'))
        for operation in ('download', 'collect', 'update'):
            self.busy(app, operation)
            self.assertEqual((app.start_button.kind, app.retry_button.kind), ('primary', 'soft'), operation)
            self.assertEqual(str(app.retry_button.cget('state')), 'disabled')
            self.idle(app)
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('soft', 'primary'))
        app.unfinished = []
        app.refresh_failed_button()
        self.assertEqual((app.start_button.kind, app.retry_button.kind), ('primary', 'soft'))

    def test_empty_state_paste(self):
        app = self.make_app(visible=False)
        self.assertTrue(self.shown(app.empty))
        self.assertEqual(app.empty.hint_label.cget('text'), zh_t('download.empty'))
        button = app.empty.action_button
        self.assertEqual(button.cget('text'), zh_t('download.paste'))
        self.assertEqual(button._command, app.paste)
        self.assertIn(button, app.input_controls)
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, *a, **k: LINK):
            button.invoke()
        self.assertEqual(app.box_text(), LINK)
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.added', added=1))
        # (final polish) with a link in the box the empty state keeps only its icon and sentence
        self.assertTrue(self.shown(app.empty))
        self.assertEqual(button.winfo_manager(), '')
        self.assertEqual(app.empty.hint_label.winfo_manager(), 'pack')
        app.urls.delete('1.0', 'end')
        app.links_changed()
        self.assertEqual(button.winfo_manager(), 'pack')
        self.assertIs(button.master.pack_slaves()[-1], button, 'back under the sentence')
        app.urls.insert('1.0', LINK)
        app.links_changed()
        self.assertEqual(button.winfo_manager(), '')
        for operation, state in (('download', 'normal'), ('collect', 'disabled'), ('update', 'disabled')):
            self.busy(app, operation)
            self.assertEqual(str(button.cget('state')), state, operation)
            self.idle(app)


# ============================================================================ task area
class TaskAreaTests(AppCase):

    def test_what_the_task_area_shows(self):
        app = self.make_app(visible=False)
        parts = (app.empty, app.task_header, app.progress, app.task_list)
        self.assertEqual([self.shown(part) for part in parts], [True, False, False, False], 'empty')
        # another activity holds the window: the header says so even without rows
        self.busy(app, 'collect')
        self.assertEqual([self.shown(part) for part in parts[:2]], [False, True])
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.foreign.collect.title'))
        self.assertEqual(ui_theme.full_text(app.metrics), '')
        self.assertTrue(app.urls.dimmed)
        self.assertEqual(app.link_box.cget('fg_color'), ui_theme.SURFACE)
        self.assertTrue(self.shown(app.stop_button))
        self.assertEqual(app.stop_button.cget('text'), zh_t('common.stop'))
        self.idle(app)
        self.assertFalse(app.urls.dimmed)
        self.assertEqual(app.link_box.cget('fg_color'), ui_theme.SURFACE_ALT)
        self.assertEqual([self.shown(part) for part in parts], [True, False, False, False])
        # only leftovers of earlier jobs: titled 未完成, no progress bar
        app.unfinished = [entry(1)]
        app.show_unfinished_rows()
        app.refresh_failed_button()
        self.assertEqual([self.shown(part) for part in parts], [False, True, False, True])
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('common.unfinished'))
        # a job: the progress bar too
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch([LINK], engine.Options(_sandbox.out('page-download', 'out'))))
            self.assertEqual([self.shown(part) for part in parts], [False, True, True, True])
            app.runner.release.set()
            app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'pending_urls': []})

    def assert_header_rows(self, app, operation):
        """The task buttons sit at the right end of the metrics line when there is one, else at the right end
        of the title's line (no empty line under the title)."""
        columns = [int(w.grid_info()['column']) for w in (app.current_title, app.retry_button, app.task_menu,
                                                          app.stop_button)]
        self.assertEqual(columns, sorted(columns))
        metrics = bool(ui_theme.full_text(app.metrics))
        self.assertEqual(self.shown(app.metrics), metrics, operation)
        row = 1 if metrics else 0
        self.assertTrue(all(int(w.grid_info()['row']) == row for w in (app.retry_button, app.task_menu,
                                                                        app.stop_button)), operation)
        self.assertEqual(int(app.current_title.grid_info()['columnspan']), 4 if metrics else 1, operation)
        # (final polish) a button line high, in device pixels (grid minsize is not scaled by CustomTkinter)
        line = round(ui_theme.CONTROL_H * (ui_theme._scaling(app) or 1.0))
        self.assertEqual(int(app.task_header.grid_rowconfigure(1)['minsize']), line if metrics else 0)

    def test_header_buttons(self):
        app = self.make_app(visible=False)
        self.assertEqual([self.shown(w) for w in (app.retry_button, app.task_menu, app.stop_button)],
                         [False, False, False])
        app.unfinished = [entry(1), entry(2), entry(3)]
        app.refresh_failed_button()
        self.assertEqual([self.shown(w) for w in (app.retry_button, app.task_menu, app.stop_button)],
                         [True, True, False])
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=3))
        self.assertGreaterEqual(app.retry_button.cget('width'),
                                ui_theme.fitted_width(app, zh_t('download.retry_unfinished_n', count=3)))
        for operation in ('download', 'collect', 'update'):
            self.busy(app, operation)
            self.assertEqual([self.shown(w) for w in (app.retry_button, app.task_menu, app.stop_button)],
                             [True, True, True], operation)
            self.assertEqual([str(w.cget('state')) for w in (app.retry_button, app.task_menu, app.stop_button)],
                             ['disabled', 'normal', 'normal'], operation)
            # retry, the menu and stop keep this order, danger last; another activity has no metrics line
            if operation != 'download':
                self.assertEqual(ui_theme.full_text(app.metrics), '', operation)
                self.assertFalse(self.shown(app.progress), 'no bar under another activity title')
            self.assert_header_rows(app, operation)
            if operation == 'download':
                app.live_metrics(zh_t('download.metrics.connecting'))
                self.assert_header_rows(app, operation)
            self.idle(app)
        self.assertFalse(self.shown(app.stop_button))

    def test_task_menu(self):
        app = self.make_app(visible=False)
        app.unfinished = [entry(1), entry(2)]
        app.refresh_failed_button()
        self.assertEqual(app.task_menu.menu_items, app.task_menu_items)
        items = app.task_menu_items()
        # (final polish) only 清除未完成: 重试未完成 (N) is the visible button beside '⋯', shown exactly when '⋯' is
        # (the header has no right-click menu), so a menu copy only repeated it
        self.assertEqual(labels(items), [zh_t('download.clear_unfinished')])
        self.assertTrue(items[-1].danger)
        self.assertEqual(enabled(items), [True])
        self.assertEqual(self.shown(app.retry_button), self.shown(app.task_menu))
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=2))
        for operation in ('download', 'collect', 'update'):
            self.busy(app, operation)
            self.assertEqual(enabled(app.task_menu_items()), [False], operation)
            self.assertTrue(self.shown(app.retry_button), operation)
            self.idle(app)
        # 清除未完成 keeps its confirmation
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=False) as ask:
            items[-1].command()
        self.assertEqual(ask.call_args[0][1], zh_t('dialog.clear_unfinished.text', count=2))
        self.assertEqual(len(app.unfinished), 2)
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True), \
                mock.patch.object(engine, 'save_unfinished'):
            app.task_menu_items()[-1].command()
        self.assertEqual(app.unfinished, [])
        self.assertFalse(self.shown(app.retry_button) or self.shown(app.task_menu))

    def test_row_menu_follows_the_selected_row(self):
        app = self.make_app(visible=False)
        app.populate_queue(['https://example.org/a', 'https://example.org/b'])
        self.assertFalse(self.shown(app.row_menu))
        app.select_row(1)
        self.assertTrue(self.shown(app.row_menu))
        self.assertEqual(str(app.row_menu.grid_info()['in']), str(app.rows[1]['frame']))
        self.assertEqual(int(app.row_menu.grid_info()['column']), 2)
        self.assertEqual(int(app.rows[1]['badge'].grid_info()['column']), 3, 'the chip stays at the right edge')
        app.select_row(0)
        self.assertEqual(str(app.row_menu.grid_info()['in']), str(app.rows[0]['frame']))
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.row_menu.invoke()
        self.assertEqual(labels(popup.call_args[0][1]()), labels(app.row_menu_items(0)))
        app.populate_queue([])
        self.assertFalse(self.shown(app.row_menu))
        self.assertIsNone(app.selected_row)

    def test_row_menu_items(self):
        app = self.make_app(visible=False)
        links = ['https://example.org/done', 'https://example.org/skipped', 'https://example.org/failed']
        app.populate_queue(links)
        app.operation = 'download'
        path = str(_sandbox.out('page-download', 'a.m4a'))
        app.update_row(0, state='done', path=path)
        app.update_row(1, state='skipped', path=path)
        app.update_row(2, state='failed', detail='x')
        tail = [zh_t('download.menu.play'), zh_t('download.menu.show'), zh_t('download.menu.copy_link'),
                zh_t('download.menu.reason')]
        self.assertEqual(labels(app.row_menu_items(0)), tail + [zh_t('download.menu.retry'), zh_t('download.menu.remove')])
        self.assertEqual(labels(app.row_menu_items(1)), tail + [zh_t('download.menu.anyway'), zh_t('download.menu.remove')],
                         '仍然下载 takes the place of 重试')
        self.assertEqual(enabled(app.row_menu_items(0)), [True, True, True, False, False, True])
        self.assertEqual(enabled(app.row_menu_items(1)), [True, True, True, False, True, True])
        self.assertEqual(enabled(app.row_menu_items(2)), [False, False, True, True, True, True])
        for index in range(3):
            items = app.row_menu_items(index)
            self.assertIs(items[4], ui_theme.SEPARATOR)
            self.assertTrue(items[-1].danger, '移除 is red and last')
            self.assertLessEqual(len(items), 7)
        self.busy(app, 'download')
        self.assertEqual(enabled(app.row_menu_items(2)), [False, False, True, True, True, False])
        self.idle(app)
        self.busy(app, 'collect')
        self.assertEqual(enabled(app.row_menu_items(2)), [False, False, True, True, False, False])
        self.assertEqual(enabled(app.row_menu_items(1))[4], False)
        self.idle(app)
        self.assertEqual(app.row_menu_items(None), [])
        self.assertEqual(app.row_menu_items(9), [])
        # the right click selects the row and opens the same items
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.row_context(mock.Mock(x_root=3, y_root=4), 2)
        self.assertEqual(app.selected_row, 2)
        self.assertEqual(labels(popup.call_args[0][1]()), labels(app.row_menu_items(2)))
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.open_row_menu()         # Shift+F10 / the menu key
        self.assertEqual(labels(popup.call_args[0][1]()), labels(app.row_menu_items(2)))

    # (final review, honesty 5) The key of each row chip, pinned here (golden is recorded in Chinese, where
    # download.chip.done and library.state.completed read the same): checked in English, where they differ.
    ROW_CHIP_KEYS = {'waiting': 'download.chip.waiting', 'active': 'download.chip.active',
                     'done': 'download.chip.done', 'failed': 'download.chip.failed',
                     'stopped': 'download.chip.stopped', 'unstarted': 'download.chip.unstarted',
                     'blocked': 'download.chip.blocked', 'skipped': 'download.chip.skipped',
                     'unfinished': 'common.unfinished'}

    def test_row_chips_in_english(self):
        self.assertEqual(set(self.ROW_CHIP_KEYS), set(ui_download.STATE_LOOK))
        app = self.make_app(lang='en', visible=False)
        states = list(self.ROW_CHIP_KEYS)
        app.populate_queue([f'https://example.org/{state}' for state in states])
        self.assertTrue(pump(app, lambda: len(app.rows) == len(states)))
        for index, state in enumerate(states):
            app.update_row(index, state=state)
            self.assertEqual(app.rows[index]['badge'].cget('text'), en_t(self.ROW_CHIP_KEYS[state]), state)

    def test_titles(self):
        def done(**value):
            return {'success': 0, 'failed': 0, 'files': [], 'cancelled': False, 'pending_urls': [], **value}
        cases = (
            ('stopped', done(cancelled=True), 'download.title.stopped', {}, 'download.metrics.stopped', ui_theme.MUTED),
            ('engine', done(engine_error=True), 'download.title.engine_error', {}, 'download.detail.see_log', ui_theme.WARN),
            ('bot', done(success=1, bot_check=True, pending_urls=['x']), 'download.title.bot', {}, 'download.metrics.bot',
             ui_theme.WARN),
            ('unfinished', done(success=2, failed=1, pending_urls=['x']), 'download.title.finished_n', {'success': 2},
             'download.metrics.finished_unfinished', ui_theme.MUTED),
            ('skipped', done(skipped=3), 'download.title.all_skipped', {'skipped': 3}, '', ui_theme.MUTED),
            ('done', done(success=4, skipped=1), 'download.title.done', {'success': 4}, '', ui_theme.MUTED),
            ('archive', done(success=1, archive_errors=['x']), 'download.title.done', {'success': 1},
             'download.metrics.archive_errors', ui_theme.WARN),
        )
        for lang in ('zh', 'en'):
            app = self.make_app(lang, visible=False)
            for name, value, title, params, metrics, color in cases:
                with self.subTest(lang=lang, case=name):
                    app.populate_queue([LINK])
                    app.operation = 'download'
                    app.set_busy(True)
                    app.handle('done', value)
                    self.assertEqual(ui_theme.full_text(app.current_title), i18n.t(title, **params))
                    self.assertEqual(ui_theme.full_text(app.metrics), i18n.t(metrics) if metrics else '')
                    self.assertEqual(app.metrics.cget('text_color'), color)
                    self.assertFalse(self.shown(app.stop_button))
            self.close(app)

    def test_metrics_line_keeps_the_button_rhythm(self):
        """(final polish) The task header is two lines: without buttons (a job that ended with the YouTube check)
        the second line is still as high as a button line and its sentence sits in the middle of it, as it does
        beside 重试未完成 (N)."""
        app = self.make_app()
        scale = ui_theme._scaling(app) or 1.0
        line = round(ui_theme.CONTROL_H * scale)

        def check(case):
            pump(app, timeout=0.3)
            title, metrics = app.current_title, app.metrics
            self.assertTrue(metrics.winfo_ismapped(), case)
            row_top = title.winfo_y() + title.winfo_height()
            self.assertLessEqual(abs(app.task_header.winfo_height() - row_top - line), 1, case)
            middle = metrics.winfo_y() + metrics.winfo_height() / 2
            self.assertLessEqual(abs(middle - (row_top + line / 2)), 1, case)

        app.populate_queue([LINK])
        app.operation = 'download'
        app.set_busy(True)
        app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': False, 'pending_urls': [LINK],
                            'bot_check': True})
        app.cancel_bot_retry()
        self.assertEqual(ui_theme.full_text(app.metrics), zh_t('download.metrics.bot'))
        self.assertFalse(self.shown(app.retry_button))
        check('YouTube check, no buttons')
        app.unfinished = [entry(1)]
        app.refresh_failed_button()
        app.metrics.configure(text=zh_t('download.metrics.finished_unfinished'))
        app.place_task_actions()
        self.assertTrue(self.shown(app.retry_button))
        check('with 重试未完成 (N)')

    def test_progress_and_wait_lines(self):
        app = self.make_app(visible=False)
        app.populate_queue([LINK])
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 1, 'total': 1, 'url': LINK})
        self.assertEqual(ui_theme.full_text(app.metrics), zh_t('download.metrics.connecting'))
        app.handle('progress', {'fraction': .4299, 'downloaded': 1.5 * 2 ** 20, 'total': 3.6 * 2 ** 20,
                                'speed': 507.8 * 1024, 'eta': 4, 'status': 'downloading'})
        self.assertEqual(ui_theme.full_text(app.metrics), '42% · 1.5 MB / 3.6 MB · 507.8 KB/s · 00:04')
        self.assertEqual(app.rows[0]['badge'].cget('text'), '42%', 'the chip and the line show the same value')
        app.handle('progress', {'fraction': .9999, 'status': 'downloading'})
        self.assertEqual(app.rows[0]['badge'].cget('text'), '99%', 'never 100% before it is all there')
        app.handle('waiting', {'reason': engine.msg('core.wait.pace'), 'seconds': 90})
        app.render_wait()
        self.assertTrue(ui_theme.full_text(app.metrics).startswith(zh_t('download.metrics.wait', time='01:30', reason='')))
        self.assertEqual(app.metrics.cget('text_color'), ui_theme.WARN)
        app.handle('progress', {'fraction': .5, 'status': 'downloading'})
        self.assertEqual(app.metrics.cget('text_color'), ui_theme.MUTED)
        app.handle('done', {'success': 1, 'failed': 0, 'files': [], 'cancelled': False, 'pending_urls': []})

    def test_notices(self):
        app = self.make_app(visible=False)
        app.prefs['bot_check_at'] = time.time() - 125
        app.unfinished = []
        with mock.patch.object(app, 'smoke_mode', False), mock.patch.object(app, 'after'):
            app.download_startup()
        self.assertEqual(app.notice_kind, 'bot_recent')
        self.assertEqual(app.notice_bar.full_text, zh_t('download.notice.bot_recent', minutes=2))
        self.assertEqual([button.cget('text') for button in app.notice_bar.buttons], [zh_t('common.got_it')])
        self.assertEqual(app.notice_bar.label.cget('text_color'), ui_theme.WARN, 'a warning tone')
        # the notice is above the task header, outside it: seen while the list is empty
        self.assertEqual(int(app.notice_bar.grid_info()['row']), 0)
        self.assertIs(app.notice_bar.master, app.task_area)
        self.assertFalse(self.shown(app.task_header))
        app.notice_bar.buttons[0].invoke()          # 知道了
        self.assertFalse(app.notice_bar.shown)
        app.show_notice('library', zh_t('library.busy'))
        self.assertEqual(app.notice_bar.label.cget('text_color'), ui_theme.INK, 'an idle tone')
        app.hide_notice('library')

    def test_footer(self):
        app = self.make_app(visible=False)
        links = [f'https://example.org/long/{number}' for number in range(80)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 30))
        self.assertEqual(ui_theme.full_text(app.row_footer),
                         zh_t('download.footer.hidden', hidden=len(links) - len(app.rows)))


# ============================================================================ layout
class LayoutTests(AppCase):
    """At the minimum window (1000x740) in both languages: the title has a line of its own, the buttons end
    inside the task area, the input rows inside the composer; the most crowded scene keeps four rows."""

    def edge(self, widget):
        return widget.winfo_rootx() + widget.winfo_width()

    def test_minimum_window(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, size='1000x740')
                links = [f'https://www.youtube.com/watch?v=job{number:08d}' for number in range(200)]
                app.unfinished = [entry(number) for number in range(123)]
                app.mode.set('video_mp4')
                app.on_format()
                app.urls.insert('1.0', 'https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk\n' + LINK)
                app.links_changed()
                app.clip_bar.show(i18n.t('download.clip.found', count=2, title='youtube.com/watch?v=abc'),
                                  [(i18n.t('download.clip.add'), lambda: None), (i18n.t('common.ignore'), lambda: None)])
                app.populate_queue(links)
                app.show_unfinished_tail()
                app.operation = 'download'
                app.set_busy(True)
                app.show_notice('bot', i18n.t('download.notice.bot_retry', time='14:59', round=2, rounds=3),
                                [(i18n.t('download.notice.retry_now'), lambda: None), (i18n.t('common.cancel'), lambda: None)])
                app.handle('item', {'index': 123, 'total': 456, 'url': links[122]})
                app.handle('waiting', {'reason': engine.msg('core.wait.rate_queue'), 'seconds': 299})
                app.render_wait()
                pump(app, timeout=0.6)
                area, composer = app.task_area, app.composer
                for widget in (app.retry_button, app.task_menu, app.stop_button):
                    self.assertTrue(widget.winfo_ismapped(), widget)
                    self.assertLessEqual(self.edge(widget), self.edge(area) + 1, widget)
                for widget in app.subtitle_check.master.winfo_children() + [app.start_button, app.output_button]:
                    if widget.winfo_ismapped():
                        self.assertLessEqual(self.edge(widget), self.edge(composer) + 1, widget)
                self.assertEqual(app.output_button.cget('width'), ui_theme.PATH_W)
                # the title has the whole width of the header
                self.assertGreaterEqual(app.current_title.winfo_width(), app.task_header.winfo_width() - 2)
                scale = ctk.ScalingTracker.get_widget_scaling(app)
                self.assertGreaterEqual(app.task_list.canvas.winfo_height() / scale, 4 * ui_download.ROW_PITCH - 8,
                                        'four rows at least in the most crowded scene')
                # the reason line of a stopped job is shown whole
                app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'pending_urls': []})
                pump(app, timeout=0.3)
                self.assertEqual(app.metrics.cget('text'), i18n.t('download.metrics.stopped'))
                self.assertEqual(app.current_title.cget('text'), i18n.t('download.title.stopped'))
                app.hide_notice('bot')
                self.close(app)


if __name__ == '__main__':
    unittest.main()
