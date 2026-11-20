"""2.0.0 S4 B4: the log page (docs/2.0.0/handoff/S4/log.md).

    python -X utf8 -B test_page_log.py

The page is a header ([All | Errors]  Copy  ⋯ in header.right) over the log text: no card, no inner
heading, no subtitle, no filled (primary) button.  Every action has a visible entry: the filter, the
Copy button and the ⋯ menu (Copy, Open log file, —, Clear view); a right click on the text opens the
same menu from the same generating function.  set_errors_only() is idempotent and a click on a segment
reaches it with the id; 'No errors' shows only under Errors when no error or warn line is there.  The
three header controls are never touched by set_busy.  At 1000x740 and 1120x810, zh and en, the header
controls stay right of the title and inside the page; the text fills the page width.  Windows are
never on screen (alpha 0, no frame); the clipboard and os.startfile are stubs; no network.
"""
import _sandbox
import re
import unittest
from unittest import mock

import i18n
import ui_log
import ui_theme
from test_i18n_pages import AppCase, pump, walk

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')


def menu_shape(items):
    """[(label, enabled), …] with None for a separator."""
    return [None if item is ui_theme.SEPARATOR else (item.label, item.enabled) for item in items]


class LogPageCase(AppCase):
    def log_app(self, size='1120x810', **kw):
        app = self.make_app(size=size, **kw)
        app.show_page('logs')
        app.clear_log_view()
        pump(app, timeout=0.1)
        return app

    def write(self, app, *lines):
        for line in lines:
            app.append_log(line)
        app.flush_log()
        pump(app, timeout=0.05)


class StructureTests(LogPageCase):
    def test_a_header_over_the_text(self):
        app = self.log_app()
        page = app.pages['logs']
        header = page.header
        self.assertEqual(header.title.cget('text'), i18n.t('nav.logs'))
        # The page holds the header and the text only: no card, no inner header or heading.
        self.assertIs(app.logs.master, page)
        self.assertEqual(set(page.winfo_children()), {header, app.logs})
        self.assertEqual(int(app.logs.grid_info()['row']), 1)
        self.assertEqual(str(page.grid_rowconfigure(1)['weight']), '1')
        labels = [widget for widget in walk(page) if hasattr(widget, 'cget') and type(widget).__name__ == 'CTkLabel']
        self.assertEqual([label for label in labels if label not in (header.title, app.log_empty)], [])
        # header.right holds, left to right, the filter, Copy and ⋯; header.left is empty.
        self.assertEqual(header.right.pack_slaves(), [app.log_menu, app.log_copy_button, app.log_filter])
        self.assertEqual(header.left.winfo_children(), [])
        for control in (app.log_filter, app.log_copy_button, app.log_menu):
            self.assertEqual(control.cget('height'), ui_theme.CONTROL_H_SM)
        # Kinds: Copy and ⋯ are ghost; the page has no filled button (log.md 1, 00-system 2.6).
        self.assertEqual((app.log_copy_button.kind, app.log_menu.kind), ('ghost', 'ghost'))
        kinds = [widget.kind for widget in walk(page) if isinstance(widget, ui_theme.AppButton)]
        self.assertNotIn('primary', kinds)
        # The filter is a two-segment choice of ids, labelled from the catalog; All at the start.
        self.assertIsInstance(app.log_filter, ui_theme.LabeledSegmentedButton)
        self.assertEqual(app.log_filter.stored_values, ['all', 'errors'])
        self.assertEqual(list(app.log_filter.cget('values')), [i18n.t('log.filter.all'), i18n.t('log.filter.errors')])
        self.assertEqual(app.log_filter.get(), 'all')
        self.assertFalse(app.errors_only)
        self.assertEqual(app.log_copy_button.cget('text'), i18n.t('log.copy_all'))
        # The old controls are gone.
        for name in ('errors_button', 'toggle_errors'):
            self.assertFalse(hasattr(app, name), name)
        # The text: read-only, word wrap, the caption size; 'No errors' sits on its fill.
        self.assertEqual(app.logs.cget('state'), 'disabled')
        self.assertEqual(app.logs.cget('wrap'), 'word')
        self.assertEqual(app.log_empty.cget('fg_color'), ui_theme.SURFACE_ALT)
        self.assertEqual(app.log_empty.cget('text'), i18n.t('log.no_errors'))

    def test_the_page_takes_the_focus_into_the_text(self):
        app = self.log_app()
        app.show_page('download')
        app.show_page('logs')
        self.assertEqual(str(app.tk.call('focus', '-lastfor', app._w)), str(app.logs._textbox))


class ActionTests(LogPageCase):
    def test_the_menu_entries_and_their_state(self):
        app = self.log_app()
        items = app.log_menu_items()
        expected = [(i18n.t('log.copy_all'), True), (i18n.t('log.open_file'), True), None,
                    (i18n.t('log.clear_view'), False)]
        self.assertEqual(menu_shape(items), expected)       # Clear view is grey while the text is empty
        self.assertEqual([item.command for item in items if item is not None],
                         [app.copy_log, app.open_log_file, app.clear_log_view])
        self.assertFalse(any(item.danger for item in items if item is not None))       # log.md Q5
        self.write(app, 'ERROR: x')
        expected[-1] = (i18n.t('log.clear_view'), True)
        self.assertEqual(menu_shape(app.log_menu_items()), expected)
        app.log_menu_items()[-1].command()
        self.assertEqual(app.logs.get('1.0', 'end-1c'), '')
        self.assertTrue(app.log_follow)

    def test_the_dots_and_a_right_click_open_the_same_menu(self):
        app = self.log_app()
        self.write(app, 'ERROR: x')
        self.assertEqual(app.log_menu.menu_items, app.log_menu_items)
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.log_menu.invoke()
            app.logs._textbox.event_generate('<Button-3>', x=8, y=8, rootx=20, rooty=20)
            pump(app, timeout=0.05)
        self.assertEqual(popup.call_count, 2)
        for call in popup.call_args_list:
            self.assertEqual(call.args[1], app.log_menu_items)
        self.assertIs(popup.call_args_list[0].args[0], app.log_menu)
        self.assertTrue(popup.call_args_list[0].kwargs.get('align_right'))
        self.assertIs(popup.call_args_list[1].args[0], app.logs)
        # The follow bindings on the text are still there next to the menu's.
        # (Shift+F10 and the menu key are bound by the same bind_menu; generating them needs the window in
        # front, so only their bindings are checked here: test_theme drives them.)
        for sequence in ('<MouseWheel>', '<KeyRelease>', '<B1-Motion>', '<ButtonRelease-1>', '<Button-3>',
                         '<Shift-F10>', '<App>'):
            self.assertTrue(app.logs._textbox.bind(sequence), sequence)

    def test_no_errors_has_the_same_right_click_menu(self):
        app = self.log_app()
        app.set_errors_only(True)
        pump(app, timeout=0.05)
        self.assertEqual(app.log_empty.winfo_manager(), 'place')
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.log_empty._label.event_generate('<Button-3>', x=2, y=2)
            pump(app, timeout=0.05)
        self.assertEqual(popup.call_count, 1)
        self.assertEqual(popup.call_args.args[1], app.log_menu_items)

    def test_copy_takes_every_line_also_under_errors(self):
        app = self.log_app()
        self.write(app, '[youtube] a: Downloading webpage', 'ERROR: [youtube] a: Video unavailable', 'plain line')
        app.set_errors_only(True)
        for entry in (app.log_copy_button.invoke, app.log_menu_items()[0].command):
            with mock.patch.object(app, 'clipboard_clear') as clear, \
                    mock.patch.object(app, 'clipboard_append') as append:
                entry()
            clear.assert_called_once_with()
            append.assert_called_once_with('[youtube] a: Downloading webpage\nERROR: [youtube] a: Video unavailable\n'
                                           'plain line\n')

    def test_open_log_file_and_its_failure(self):
        app = self.log_app()
        entry = app.log_menu_items()[1]
        self.assertEqual(entry.command, app.open_log_file)
        with mock.patch.object(ui_log.os, 'startfile', create=True) as start, \
                mock.patch.object(ui_log.messagebox, 'showwarning') as warn:
            entry.command()
        start.assert_called_once_with(ui_log.paths.log_file())
        warn.assert_not_called()
        with mock.patch.object(ui_log.os, 'startfile', create=True, side_effect=OSError(2, 'gone')), \
                mock.patch.object(ui_log.messagebox, 'showwarning') as warn:
            entry.command()
        warn.assert_called_once()
        self.assertEqual(warn.call_args.args[0], i18n.t('dialog.open_log_failed'))
        self.assertTrue(warn.call_args.args[1])
        self.assertIs(warn.call_args.kwargs['parent'], app)


class FilterTests(LogPageCase):
    def elided(self, app, tag):
        return str(app.logs._textbox.tag_cget(tag, 'elide'))

    def test_set_errors_only_is_idempotent(self):
        app = self.log_app()
        self.write(app, '[youtube] a: Downloading webpage', 'ERROR: a', 'WARNING: b', 'plain')
        for on in (True, True, False, False, True):
            app.set_errors_only(on)
            self.assertIs(app.errors_only, on)
            self.assertEqual(app.log_filter.get(), 'errors' if on else 'all')
            for tag in ('dim', 'app', 'ok'):
                self.assertEqual(self.elided(app, tag), '1' if on else '0', tag)
            for tag in ('error', 'warn'):
                self.assertIn(self.elided(app, tag), ('', '0'), tag)

    def test_a_click_on_a_segment_hands_over_the_id(self):
        app = self.log_app()
        buttons = app.log_filter._buttons_dict
        buttons[i18n.t('log.filter.errors')].invoke()
        self.assertIs(app.errors_only, True)
        self.assertEqual(app.log_filter.get(), 'errors')
        buttons[i18n.t('log.filter.errors')].invoke()      # the chosen segment again: no change
        self.assertIs(app.errors_only, True)
        buttons[i18n.t('log.filter.all')].invoke()
        self.assertIs(app.errors_only, False)
        self.assertEqual(app.log_filter.get(), 'all')
        with mock.patch.object(app, 'set_errors_only') as chosen:
            app.log_filter.set('errors')        # set() in code runs no command (no recursion)
        chosen.assert_not_called()

    def test_no_errors_shows_only_under_errors_without_error_lines(self):
        app = self.log_app()
        shown = lambda: app.log_empty.winfo_manager() == 'place'
        self.assertFalse(shown())
        self.write(app, '[youtube] a: Downloading webpage', 'plain line')
        app.set_errors_only(True)
        self.assertTrue(shown())
        info = app.log_empty.place_info()
        self.assertEqual((info['relx'], info['rely'], info['anchor']), ('0.5', '0.5', 'center'))
        pump(app, timeout=0.05)
        self.write(app, 'WARNING: slow')                      # a warn line arrives: hidden
        self.assertFalse(shown())
        app.clear_log_view()                                   # cleared under Errors: shown again
        self.assertTrue(shown())
        self.write(app, 'ERROR: boom')
        self.assertFalse(shown())
        app.set_errors_only(False)
        app.clear_log_view()
        self.assertFalse(shown())                              # never under All
        self.write(app, 'plain')
        self.assertFalse(shown())
        app.set_errors_only(True)
        self.assertTrue(shown())

    def test_kept_aside_lines_stay_visible_under_errors(self):
        """PLAN 3-10: a file kept aside says so in a line that Errors does not hide (log.md Q3)."""
        values = {'problem': 'x', 'target': 'a.bad', 'path': 'p', 'record': 'r', 'folder': 'f'}
        app = self.log_app()
        for area in ('library', 'unfinished', 'archive'):
            for name in ('kept_aside', 'cannot_set_aside'):
                key = f'core.{area}.{name}'
                for text in (_sandbox.zh_t(key, **values), _sandbox.en_t(key, **values)):
                    self.assertEqual(ui_log.classify(text), 'error', text)
        self.write(app, i18n.t('core.library.kept_aside', problem='x', target='a.bad'))
        app.set_errors_only(True)
        self.assertFalse(app.log_empty.winfo_manager())
        self.assertTrue(app.logs._textbox.tag_ranges('error'))


class BusyTests(LogPageCase):
    def test_the_page_works_in_every_busy_state(self):
        app = self.log_app()
        self.write(app, 'ERROR: x')
        controls = (app.log_filter, app.log_copy_button, app.log_menu)
        groups = list(app.controls) + list(app.input_controls) + list(app.update_buttons)
        for control in controls:
            self.assertNotIn(control, groups)
        for operation in ('download', 'collect', 'update'):
            app.operation = operation
            app.set_busy(True)
            for control in controls:
                self.assertEqual(control.cget('state'), 'normal', (operation, control))
            self.assertEqual([entry[1] for entry in menu_shape(app.log_menu_items()) if entry], [True, True, True])
            app.set_errors_only(True)
            app.set_errors_only(False)
            app.set_busy(False)
            app.operation = None
        for control in controls:
            self.assertEqual(control.cget('state'), 'normal')


class LayoutTests(LogPageCase):
    def check_layout(self, app, lang):
        page = app.pages['logs']
        header = page.header
        pump(app, timeout=0.3)
        left, right = page.winfo_rootx(), page.winfo_rootx() + page.winfo_width()
        title_right = header.title.winfo_rootx() + header.title.winfo_reqwidth()
        controls = (app.log_filter, app.log_copy_button, app.log_menu)
        for control in controls:
            self.assertTrue(control.winfo_ismapped(), control)
            self.assertGreater(control.winfo_rootx(), title_right, (lang, control))
            self.assertLessEqual(control.winfo_rootx() + control.winfo_width(), right, (lang, control))
        # Filter, Copy and ⋯ in this order, SECTION between "look" and "do", Copy right next to ⋯ (final polish).
        self.assertLessEqual(app.log_filter.winfo_rootx() + app.log_filter.winfo_width() + ui_theme.SECTION,
                             app.log_copy_button.winfo_rootx() + 1)
        self.assertLessEqual(app.log_copy_button.winfo_rootx() + app.log_copy_button.winfo_width(),
                             app.log_menu.winfo_rootx())
        self.assertLessEqual(abs(app.log_menu.winfo_rootx() + app.log_menu.winfo_width() - right), 1)
        # (final polish) From ink to ink, Filter -> Copy is at least twice Copy -> ⋯ (32 and 14 at 100%), so the
        # three no longer read as evenly spaced.
        label = app.log_copy_button._text_label
        word_left = app.log_copy_button.winfo_rootx() + (app.log_copy_button.winfo_width() - label.winfo_reqwidth()) // 2
        word_right = word_left + label.winfo_reqwidth()
        scale = ui_theme._scaling(app) or 1.0
        dots_left = app.log_menu.winfo_rootx() + (app.log_menu.winfo_width() - round(16 * scale)) // 2
        look = word_left - (app.log_filter.winfo_rootx() + app.log_filter.winfo_width())
        do = dots_left - word_right
        self.assertGreater(do, 0, lang)
        self.assertGreaterEqual(look, 2 * do - round(4 * scale), (lang, look, do))
        # Copy is as wide as its label + INLINE on each side (no 72 floor; final polish: was GAP), so the groups
        # read as two.
        self.assertLessEqual(app.log_copy_button.winfo_width() - label.winfo_reqwidth(),
                             2 * round(ui_theme.INLINE * scale) + 4, lang)
        # The text fills the width of the page, below the header.
        self.assertLessEqual(abs(app.logs.winfo_rootx() - left), 1)
        self.assertLessEqual(abs(app.logs.winfo_rootx() + app.logs.winfo_width() - right), 1)
        self.assertGreaterEqual(app.logs.winfo_rooty(), header.winfo_rooty() + header.winfo_height())
        texts = [app.log_copy_button.cget('text'), *app.log_filter.cget('values'), app.log_empty.cget('text'),
                 header.title.cget('text'), *(entry[0] for entry in menu_shape(app.log_menu_items()) if entry)]
        if lang == 'en':
            self.assertEqual([text for text in texts if CJK.search(text)], [])
        # Every segment shows its whole label.
        for label, button in app.log_filter._buttons_dict.items():
            self.assertLessEqual(button._text_label.winfo_reqwidth(), button.winfo_width(), (lang, label))

    def test_default_and_smallest_window_in_both_languages(self):
        for lang in ('zh', 'en'):
            with _sandbox.language(lang):
                for size in ('1120x810', '1000x740'):
                    with self.subTest(lang=lang, size=size):
                        app = self.log_app(size=size)
                        self.write(app, 'ERROR: [youtube] a: Video unavailable', 'plain')
                        self.check_layout(app, lang)
                        app.set_errors_only(True)
                        self.check_layout(app, lang)
                        self.close(app)


class CatalogTests(unittest.TestCase):
    def test_the_log_catalog(self):
        import i18n_log
        self.assertEqual(sorted(i18n_log.TEXTS), ['dialog.open_log_failed', 'log.clear_view', 'log.copy_all',
                                                  'log.filter.all', 'log.filter.errors', 'log.no_errors',
                                                  'log.open_file'])
        # (final review, deferred-to-C item 3) the wording lives only in i18n_log: here only that both languages
        # have a text and the English one has no Chinese
        for key in ('log.filter.all', 'log.filter.errors', 'log.copy_all', 'log.no_errors'):
            zh, en = i18n_log.TEXTS[key]
            self.assertTrue(zh.strip() and en.strip(), key)
            self.assertIsNone(re.search(r'[㐀-鿿]', en), key)
        for gone in ('log.heading', 'log.subtitle', 'log.show_all', 'log.errors_only'):
            self.assertNotIn(gone, i18n_log.TEXTS)
        self.assertFalse(hasattr(i18n_log, 'TAGS'))


if __name__ == '__main__':
    unittest.main()
