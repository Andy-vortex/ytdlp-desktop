"""2.0.0 S4 B2: the collect page (docs/2.0.0/handoff/S4/collect.md).

    python -X utf8 -B test_page_collect.py

The layout without cards (2.5), the options row without labels whose values carry their units, the
scrolls menu only in dynamic mode, Start and Stop in one cell, the one solid button of each stage (2.6),
the results toolbar or the empty state, the '⋯' menu and its right-click mirror with every entry's enabled
rule (4.2), Ctrl+A, the in-table "no matches", the privacy / background line, the status line, and the
English minimum window (2.4: every control of the options row whole and inside the content's right edge).
Windows are never on screen (alpha 0, no frame); no network; the collector is a fake that emits nothing.
"""
import _sandbox
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import collection_ui
import i18n
import ui_theme
import updater
from studio import StudioApp


def pump(app, condition=None, timeout=0.3):
    return _sandbox.pump(app, condition, timeout, step=0.005)


class FakeRunner:
    """Stands in for collector.CollectionRunner: run() waits until released and emits nothing."""
    instances = []

    def __init__(self, emit):
        self.emit = emit
        self.release = threading.Event()
        FakeRunner.instances.append(self)

    def run(self, options):
        self.options = options
        self.release.wait(20)

    def cancel(self):
        self.release.set()


def links(count, kind='media'):
    return [{'url': f'https://media.example.org/v/{number:03d}.mp4', 'title': f'第 {number} 集', 'kind': kind,
             'source': 'https://example.org/list'} for number in range(count)]


def page(items, text='正文'):
    return {'url': 'https://example.org/list', 'title': '示例列表', 'text': text, 'markdown': '# 正文', 'links': items}


def shown(widget):
    return bool(widget.winfo_manager())


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', side_effect=AssertionError('no network')),
                        mock.patch.object(StudioApp, 'clipboard_get', side_effect=tk.TclError('no clipboard')),
                        mock.patch.object(collection_ui, 'CollectionRunner', FakeRunner)):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeRunner.instances = []
        self.addCleanup(lambda: [runner.release.set() for runner in FakeRunner.instances])

    def make_app(self, size='1120x810', lang=None):
        if lang is not None:
            context = _sandbox.language(lang)
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/pages', str(_sandbox.out('page-collect-' + self._testMethodName))))
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.geometry(size + '+0+0')
        self.addCleanup(self.close, app)
        app.show_page('collect')
        pump(app)
        return app

    @staticmethod
    def close(app):
        try:
            writer = getattr(app, 'history_writer', None)
            if writer is not None:
                writer.idle(5)
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def results(app, count=3, text='正文', web=0):
        """Results as a finished collection leaves them."""
        items = links(count) + links(web, 'web')[:web]
        for number, item in enumerate(items[count:]):
            item['url'] = f'https://example.org/page/{number}'
        app.collected_pages = [page(items, text)]
        app.collected_links = {item['url']: item for item in items}
        app.render_collection_links()
        app.refresh_collect_actions()       # as handle_collection and load_collection_record do

    @staticmethod
    def primaries(app):
        """The solid buttons of the collect page a user can see now."""
        found = []
        stack = [app.pages['collect']]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            if isinstance(widget, ui_theme.AppButton) and widget.kind == 'primary' and widget.winfo_ismapped():
                found.append(widget)
        return found


class LayoutTests(Base):
    def test_no_cards_and_no_labels_in_the_options_row(self):
        app = self.make_app()
        for gone in ('collect_card', 'collect_results', 'collect_export', 'collect_text', 'collect_options_row',
                     'collect_placeholder', '_collect_layout_options'):
            self.assertFalse(hasattr(app, gone), gone)
        self.assertFalse(hasattr(collection_ui, 'NARROW_OPTIONS'))
        page = app.pages['collect']
        # every widget sits directly on the page: no frame with a border or a fill of its own around them
        self.assertIs(app.collect_urls.master, page)
        self.assertIs(app.collect_options.master, page)
        self.assertIs(app.collect_toolbar.master, page)
        self.assertIs(app.collect_tree.container.master, page)
        self.assertIs(app.collect_empty.master, page)
        self.assertIs(app.collect_status.master, page)
        for frame in (app.collect_options, app.collect_toolbar):
            self.assertEqual(frame.cget('fg_color'), 'transparent')
        self.assertIsInstance(app.collect_urls, ui_theme.AppTextbox)
        self.assertEqual(app.collect_urls.placeholder_label.cget('text'), i18n.t('collect.placeholder'))
        # the options row: the controls only, in this order, no CTkLabel
        row = sorted(app.collect_options.grid_slaves(), key=lambda w: int(w.grid_info()['column']))
        self.assertEqual(row, [app.collect_mode_menu, app.collect_pages_menu, app.collect_scope,
                               app.collect_subscribe, app.collect_start])
        self.assertFalse([w for w in app.collect_options.winfo_children() if isinstance(w, ctk.CTkLabel)])
        self.assertEqual(app.collect_subscribe.kind, 'ghost')
        self.assertEqual(app.collect_subscribe.cget('text'), i18n.t('collect.save_subscription'))
        # Start and Stop: one cell, one width
        self.assertEqual(app.collect_stop.grid_info().get('column'), None)       # removed, remembered
        self.assertEqual(app.collect_start.cget('width'), app.collect_stop.cget('width'))
        self.assertGreaterEqual(app.collect_start.cget('width'),
                                ui_theme.fitted_width(app.collect_options, i18n.t('collect.start')) + 22)
        self.assertGreaterEqual(app.collect_stop.cget('width'),
                                ui_theme.fitted_width(app.collect_options, i18n.t('common.stop')))
        self.assertEqual(app.collect_add.cget('width'), ui_theme.fitted_width(app.collect_toolbar, app.add_text(9999)))
        # the rows of the page (collect.md 2.5); the table and the empty state share row 5, one at a time
        rows = [int(w.grid_info()['row']) for w in (app.collect_urls, app.collect_options, app.collect_empty,
                                                    app.collect_status)]
        self.assertEqual(rows, [1, 2, 5, 6])
        self.results(app)
        self.assertEqual(int(app.collect_tree.container.grid_info()['row']), 5)
        self.assertEqual(int(app.collect_toolbar.grid_info()['row']), 4)
        self.assertEqual(int(app.collect_note.grid_info()['row']) if shown(app.collect_note) else 3, 3)

    def test_the_menus_show_their_values_with_units(self):
        app = self.make_app()
        self.assertEqual(app.collect_scrolls_menu.cget('values'),
                         [i18n.t('collect.value.scrolls', count=0), i18n.t('collect.value.scrolls_one')]
                         + [i18n.t('collect.value.scrolls', count=n) for n in range(2, 6)])
        self.assertEqual(app.collect_pages_menu.cget('values'),
                         [i18n.t('collect.value.pages_one')] + [i18n.t('collect.value.pages', count=n) for n in range(2, 6)])
        self.assertEqual(app.collect_scrolls_menu.stored_values, [str(n) for n in range(6)])
        self.assertEqual(app.collect_pages_menu.stored_values, [str(n) for n in range(1, 6)])
        # (final review) a value carries its unit: never the bare number (the wording lives in i18n_collect)
        for value in app.collect_scrolls_menu.cget('values') + app.collect_pages_menu.cget('values'):
            self.assertRegex(value, r'\d.*\D|\D.*\d', value)
        # the variables keep their codes: a label chosen in the menu stores the number
        app.collect_pages_menu._dropdown_callback(i18n.t('collect.value.pages', count=3))
        self.assertEqual(app.collect_pages_limit.get(), '3')
        app.collect_mode.set(collection_ui.MODE_DYNAMIC)
        app.collect_scrolls_menu._dropdown_callback(i18n.t('collect.value.scrolls_one'))
        self.assertEqual(app.collect_scrolls.get(), '1')
        self.assertEqual(app.collect_mode_menu.cget('values'), [i18n.t('collect.mode.static'), i18n.t('collect.mode.dynamic')])
        self.assertEqual(app.collect_filter_menu.cget('values'), [i18n.t('collect.filter.media'), i18n.t('collect.filter.all')])
        with _sandbox.language('en'):
            self.assertEqual(collection_ui.scrolls_label(1), _sandbox.en_t('collect.value.scrolls_one'))
            self.assertEqual(collection_ui.scrolls_label(3), _sandbox.en_t('collect.value.scrolls', count=3))
            self.assertEqual(collection_ui.pages_label(1), _sandbox.en_t('collect.value.pages_one'))
            self.assertEqual(collection_ui.pages_label(5), _sandbox.en_t('collect.value.pages', count=5))

    def test_scrolls_only_in_dynamic_mode_and_the_hidden_value_still_counts(self):
        app = self.make_app()
        app.collect_urls.delete('1.0', 'end')
        app.collect_urls.insert('1.0', 'https://example.org/list')
        app.collect_scrolls.set('3')
        self.assertFalse(shown(app.collect_scrolls_menu))
        self.assertFalse(shown(app.collect_note))
        # the static options are exactly what they were: the hidden menu's value goes into CollectOptions
        options = app.collection_options()
        self.assertEqual((options.dynamic, options.scrolls, options.max_pages), (False, 3, 1))
        app.collect_mode.set(collection_ui.MODE_DYNAMIC)
        pump(app)
        self.assertTrue(shown(app.collect_scrolls_menu))
        self.assertEqual(int(app.collect_scrolls_menu.grid_info()['column']), 1)
        self.assertTrue(shown(app.collect_note))
        self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.note.dynamic'))
        self.assertEqual(app.collect_note.cget('text_color'), ui_theme.MUTED)
        self.assertTrue(app.collection_options().dynamic)
        app.collect_mode.set(collection_ui.MODE_STATIC)
        self.assertFalse(shown(app.collect_scrolls_menu))
        self.assertFalse(shown(app.collect_note))

    def test_the_background_check_line(self):
        app = self.make_app()
        app.refresh_collect_note(background='夜谈')
        self.assertTrue(shown(app.collect_note))
        self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.background', name='夜谈'))
        self.assertEqual(app.collect_note.cget('text_color'), ui_theme.ACCENT_TEXT)
        app.refresh_collect_note()
        self.assertFalse(shown(app.collect_note))

    def test_the_restart_restore_and_the_dimmed_box(self):
        # (stage C) the forwarders sync_collect_placeholder / _dim_collect_box are gone: ui_settings and
        # studio call the box itself
        app = self.make_app()
        self.assertFalse(hasattr(type(app), 'sync_collect_placeholder'))
        self.assertFalse(hasattr(type(app), '_dim_collect_box'))
        app.collect_urls.delete('1.0', 'end')
        pump(app)
        self.assertTrue(app.collect_urls.placeholder_label.winfo_ismapped())
        app.collect_urls._textbox.insert('1.0', 'https://example.org/')     # as code that bypasses the widget
        app.collect_urls.sync_placeholder()
        pump(app)
        self.assertFalse(app.collect_urls.placeholder_label.winfo_ismapped())
        app.operation = 'download'
        app.set_busy(True)
        app.show_page('collect')                    # studio.show_page dims the box of a page shown while busy
        self.assertTrue(app.collect_urls.dimmed)
        app.set_busy(False)
        self.assertFalse(app.collect_urls.dimmed)


class StageTests(Base):
    def test_the_empty_state_then_results(self):
        app = self.make_app()
        self.assertFalse(app.collected_links)
        self.assertTrue(shown(app.collect_empty))
        self.assertEqual(app.collect_empty.hint_label.cget('text'), i18n.t('collect.empty'))
        self.assertIsNone(app.collect_empty.action_button)          # Start is right above it (00-system 2.11)
        self.assertFalse(shown(app.collect_toolbar))
        self.assertFalse(shown(app.collect_tree.container))
        self.assertEqual(app.collect_start.kind, 'primary')
        self.assertEqual(self.primaries(app), [app.collect_start])
        self.assertEqual(app.collect_status.cget('text'), '')
        self.results(app)
        pump(app)
        self.assertFalse(shown(app.collect_empty))
        self.assertTrue(shown(app.collect_toolbar))
        self.assertTrue(shown(app.collect_tree.container))
        self.assertEqual(app.collect_start.kind, 'soft')
        self.assertEqual(app.collect_add.kind, 'primary')
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        self.assertEqual(app.collect_add.cget('text'), i18n.t('collect.add'))
        self.assertEqual(self.primaries(app), [app.collect_add])
        self.assertEqual(app.collect_count.cget('text'), i18n.t('collect.count', shown=3, total=3))
        app.collect_tree.selection_set(('0', '2'))
        pump(app)
        self.assertEqual(app.collect_add.cget('state'), 'normal')
        self.assertEqual(app.collect_add.cget('text'), f"{i18n.t('collect.add')} (2)")
        # back to nothing (a history record with no links): the empty state, Start solid again
        app.collected_links, app.collected_pages = {}, []
        app.render_collection_links()
        app.refresh_collect_actions()
        self.assertTrue(shown(app.collect_empty))
        self.assertFalse(shown(app.collect_toolbar))
        self.assertEqual(app.collect_start.kind, 'primary')

    def test_page_text_without_links_still_reaches_save_and_archive(self):
        """(final review B1) A collection whose pages have text but no link (a CSS scope around the article
        body): the toolbar and its '⋯' stay visible, the table says "no matches", and Save text / Archive
        text are enabled; the empty state is not shown."""
        app = self.make_app()
        app.collected_pages = [page([], text='只有正文的页面')]
        app.collected_links = {}
        app.render_collection_links()
        app.refresh_collect_actions()
        pump(app)
        self.assertTrue(shown(app.collect_toolbar))
        self.assertTrue(app.collect_menu.winfo_ismapped())
        self.assertTrue(shown(app.collect_tree.container))
        self.assertFalse(shown(app.collect_empty))
        items = {item.label: item.enabled for item in app.collect_menu_items() if item is not None}
        self.assertTrue(items[i18n.t('collect.save_text')])
        self.assertTrue(items[i18n.t('collect.archive_text')])
        self.assertFalse(items[i18n.t('collect.add')])
        self.assertEqual(app.collect_add.cget('state'), 'disabled')

    def test_only_a_change_touches_the_buttons(self):
        app = self.make_app()
        self.results(app)
        with mock.patch.object(app.collect_start, 'set_kind', wraps=app.collect_start.set_kind) as kind, \
                mock.patch.object(ui_theme, 'show', wraps=ui_theme.show) as show:
            for _ in range(3):
                app.refresh_collect_actions()
                app.set_busy(False)
                app.render_collection_links()
            self.assertEqual(kind.call_count, 0)
            # (stage C) set_busy also runs the other pages' hooks; this page shows or hides nothing
            page = str(app.pages['collect'])
            mine = [call for call in show.call_args_list if str(call.args[0]).startswith(page + '.')]
            self.assertEqual(mine, [])

    def test_collecting_swaps_start_and_stop(self):
        app = self.make_app()
        self.results(app)
        app.collect_urls.delete('1.0', 'end')
        app.collect_urls.insert('1.0', 'https://example.org/list')
        app.start_collection()
        pump(app)
        self.assertTrue(app.busy)
        self.assertFalse(shown(app.collect_start))
        self.assertTrue(shown(app.collect_stop))
        self.assertEqual(app.collect_stop.cget('state'), 'normal')
        self.assertEqual(int(app.collect_stop.grid_info()['column']), 5)
        self.assertTrue(app.collect_urls.dimmed)
        # the old results are gone: the empty state, no solid button at all (Stop is danger)
        self.assertTrue(shown(app.collect_empty))
        self.assertEqual(self.primaries(app), [])
        # the first page arrives: the toolbar comes back, Add to downloads stays grey while collecting
        app.handle('collect_page', page(links(2)))
        pump(app)
        self.assertTrue(shown(app.collect_toolbar))
        app.collect_tree.selection_set(('0',))
        pump(app)
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        self.assertTrue(shown(app.collect_stop))
        self.assertEqual(app.collect_stop.cget('state'), 'normal')
        self.assertLessEqual(len(self.primaries(app)), 1)
        # Stop greys itself until the run ends
        app.stop()
        self.assertEqual(app.collect_stop.cget('state'), 'disabled')
        app.handle('collect_done', {'cancelled': True})
        pump(app)
        self.assertFalse(app.busy)
        self.assertTrue(shown(app.collect_start))
        self.assertFalse(shown(app.collect_stop))
        self.assertEqual(app.collect_start.cget('state'), 'normal')
        # the job's record (the fake collector stored no page) is what the page shows now: nothing, Start solid
        self.assertEqual(app.collect_start.kind, 'soft' if app.collected_links else 'primary')
        self.assertEqual(len(self.primaries(app)), 1)
        self.assertFalse(app.collect_urls.dimmed)
        self.assertTrue(ui_theme.full_text(app.collect_status).startswith(i18n.t('collect.status.stopped')))
        # a second run: Stop is usable again
        app.start_collection()
        pump(app)
        self.assertTrue(shown(app.collect_stop))
        self.assertEqual(app.collect_stop.cget('state'), 'normal')
        app.handle('collect_done', {'cancelled': False})
        pump(app)

    def test_a_background_check_keeps_the_results_and_offers_stop(self):
        app = self.make_app()
        self.results(app)
        app.collect_tree.selection_set(('1',))
        app.collection_background = True
        app.operation = 'collect'
        app.set_busy(True)
        self.assertTrue(shown(app.collect_stop))
        self.assertFalse(shown(app.collect_start))
        self.assertTrue(shown(app.collect_toolbar))
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        items = {item.label: item for item in app.collect_menu_items() if item is not None}
        self.assertTrue(items[i18n.t('collect.save_text')].enabled, 'only a collection on this page greys it')
        self.assertFalse(items[i18n.t('collect.archive_text')].enabled)
        app.collection_background = False
        app.set_busy(False)
        self.assertTrue(shown(app.collect_start))

    def test_a_download_leaves_add_to_downloads_usable(self):
        app = self.make_app()
        self.results(app)
        app.collect_tree.selection_set(('0', '1'))
        app.operation = 'download'
        app.set_busy(True)
        self.assertEqual(app.collect_add.cget('state'), 'normal')
        self.assertEqual(app.collect_start.cget('state'), 'disabled')
        self.assertTrue(shown(app.collect_start))
        self.assertFalse(shown(app.collect_stop))
        app.operation = 'update'
        app.set_busy(True)
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        app.set_busy(False)
        self.assertEqual(app.collect_add.cget('state'), 'normal')

    def test_filtered_to_nothing_says_no_matches(self):
        app = self.make_app()
        self.results(app, count=2, web=1)
        self.assertEqual(app.collect_count.cget('text'), i18n.t('collect.count', shown=2, total=3))
        app.collect_search.set('nothing-like-this')
        pump(app)
        self.assertEqual(app.collect_tree.get_children(), ())
        self.assertTrue(shown(app.collect_toolbar), 'the toolbar stays: the search is in it')
        self.assertEqual(app.collect_tree.empty_label.cget('text'), i18n.t('common.no_match'))
        self.assertTrue(app.collect_tree.empty_label.winfo_ismapped())
        self.assertEqual(app.collect_count.cget('text'), i18n.t('collect.count', shown=0, total=3))
        app.collect_search.set('')
        app.collect_filter.set('all')
        self.assertEqual(len(app.collect_tree.get_children()), 3)


class MenuTests(Base):
    def labels(self, items):
        return [None if item is None else item.label for item in items]

    def enabled(self, app):
        return [item.enabled for item in app.collect_menu_items() if item is not None]

    def test_the_entries_and_their_order(self):
        app = self.make_app()
        self.results(app)
        app.collect_tree.selection_set(('0', '1', '2'))
        self.assertEqual(self.labels(app.collect_menu_items()),
                         [f"{i18n.t('collect.add')} (3)", i18n.t('common.select_all'), None, i18n.t('collect.export_links'),
                          i18n.t('collect.save_text'), i18n.t('collect.archive_text')])
        self.assertEqual(app.collect_menu.menu_items, app.collect_menu_items)     # the same function: right click = '⋯'
        self.assertEqual(app.collect_menu.menu_items(), app.collect_menu_items())
        self.assertIs(app.collect_menu.master, app.collect_toolbar)
        self.assertEqual(int(app.collect_menu.grid_info()['column']), 5)
        self.assertEqual(int(app.collect_add.grid_info()['column']), 4)

    def test_enabled_by_the_state_of_the_moment(self):
        app = self.make_app()
        # nothing collected: only nothing
        self.assertEqual(self.enabled(app), [False, False, False, False, False])
        self.results(app, text='')
        self.assertEqual(self.enabled(app), [False, True, False, False, True])
        self.results(app)
        app.collect_tree.selection_set(('1',))
        self.assertEqual(self.enabled(app), [True, True, True, True, True])
        for operation, expected in (('download', [True, True, True, True, False]),
                                    ('update', [False, True, True, True, False]),
                                    ('collect', [False, True, True, False, False])):
            app.operation = operation
            app.collection_background = False
            app.set_busy(True)
            app.collect_tree.selection_set(('1',))
            self.assertEqual(self.enabled(app), expected, operation)
            app.set_busy(False)
        app.collection_background = False

    def test_the_commands(self):
        app = self.make_app()
        self.results(app)
        with mock.patch.object(app, 'archive_pages') as archive:
            app.collect_menu_items()[-1].command()
        archive.assert_called_once_with()
        app.collect_menu_items()[1].command()
        self.assertEqual(app.collect_tree.selection(), ('0', '1', '2'))
        with mock.patch.object(app, 'queue_collected_items') as queue:
            app.collect_menu_items()[0].command()
        self.assertEqual(len(queue.call_args.args[0]), 3)

    def test_ctrl_a_and_the_right_click_mirror(self):
        app = self.make_app()
        self.results(app, count=4)
        for sequence in ('<Control-Key-a>', '<Control-Key-A>'):
            self.assertIn(sequence, app.collect_tree.bind())
        self.assertNotIn('<Control-Key-a>', app.collect_search_entry._entry.bind())
        self.assertEqual(app._collect_select_all(), 'break')
        pump(app)
        self.assertEqual(app.collect_tree.selection(), ('0', '1', '2', '3'))
        self.assertEqual(app.collect_add.cget('text'), f"{i18n.t('collect.add')} (4)")
        # right click on a row not selected: that row only, then the same menu as '⋯' (a mouse event reaches the
        # widget it is generated on, whatever window is in the foreground)
        app.collect_tree.selection_set(('0',))
        pump(app)
        for sequence in ('<Button-3>', '<Shift-Key-F10>'):
            self.assertIn(sequence, app.collect_tree.bind())
        box = app.collect_tree.bbox('2')
        self.assertTrue(box)
        with mock.patch.object(ui_theme, 'popup') as popup:
            app.collect_tree.event_generate('<Button-3>', x=box[0] + 5, y=box[1] + box[3] // 2)
            pump(app)
        self.assertTrue(popup.called)
        self.assertEqual(app.collect_tree.selection(), ('2',))
        self.assertEqual(self.labels(popup.call_args.args[1]()), self.labels(app.collect_menu_items()))


class MinimumWindowTests(Base):
    def test_english_dynamic_fits_one_row(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(size='1000x740', lang=lang)
                app.collect_mode.set(collection_ui.MODE_DYNAMIC)
                self.results(app, count=20)
                pump(app, timeout=0.5)
                page = app.pages['collect']
                right = page.winfo_rootx() + page.winfo_width()
                content = app.collect_urls.winfo_rootx() + app.collect_urls.winfo_width()
                row = [app.collect_mode_menu, app.collect_scrolls_menu, app.collect_pages_menu, app.collect_scope,
                       app.collect_subscribe, app.collect_start]
                for widget in row:
                    self.assertTrue(widget.winfo_ismapped(), widget)
                    self.assertEqual(int(widget.grid_info()['row']), 0, 'one row, never wrapped')
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), min(right, content) + 1, widget)
                scale = ctk.ScalingTracker.get_widget_scaling(page)
                for menu in row[:3]:
                    self.assertGreaterEqual(menu.winfo_width(),
                                            round(ui_theme.menu_width(page, menu.cget('values')) * scale) - 1, menu)
                for button in row[4:]:
                    self.assertGreaterEqual(button.winfo_width(),
                                            round(ui_theme.fitted_width(page, button.cget('text')) * scale) - 1, button)
                self.assertGreaterEqual(app.collect_scope.winfo_width(), round(96 * scale) - 1)
                # the toolbar too, and the table keeps at least 4 rows
                for widget in (app.collect_search_entry, app.collect_filter_menu, app.collect_count, app.collect_add,
                               app.collect_menu):
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), content + 1, widget)
                self.assertGreaterEqual(app.collect_tree.container.winfo_height(), round(ui_theme.TABLE_ROW_H * 5 * scale))
                status = app.collect_status
                self.assertLessEqual(status.winfo_rooty() + status.winfo_height(), page.winfo_rooty() + page.winfo_height() + 1)
                self.close(app)


if __name__ == '__main__':
    unittest.main()
