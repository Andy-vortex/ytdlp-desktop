"""Collect and library pages of StudioApp (1.6.0 WP5 section E): R4, R8 and the page behaviour.

Smoke apps unless a test says otherwise; one live StudioApp at a time (each test creates and
destroys its own).  Light mode is forced.  The collection worker is replaced by a fake that never
starts a process, so nothing here touches the network or Chrome.  Last, after every app of this
file is gone: the snapshot_ui assertions for all five pages and the three library tabs.
"""
import _sandbox
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from unittest import mock
from urllib.parse import unquote

import customtkinter as ctk
import collection_ui
import engine
import i18n
import library
import library_ui
import messages
import ui_download
import ui_theme
import updater
import urls
from collector import CollectOptions
from engine import MODES
from studio import StudioApp
from _sandbox import no_clipboard, no_network, zh_t

# (1.8.0) What open_library returns: a message with the sqlite text as its raw param (C-F26).
WARNING = messages.msg('core.library.temporary', error='unable to open database file')


class FakeCollectionRunner:
    """Stands in for collector.CollectionRunner: run() waits until released and emits nothing."""
    instances = []

    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.release = threading.Event()
        FakeCollectionRunner.instances.append(self)

    def run(self, options):
        self.options = options
        self.release.wait(20)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def links(count, prefix='https://media.example.org/v/'):
    return [{'url': f'{prefix}{number:03d}.mp4', 'title': f'第 {number} 集', 'kind': 'media',
             'source': 'https://example.org/list'} for number in range(count)]


def page(items, url='https://example.org/list'):
    return {'url': url, 'title': '示例列表', 'text': '正文', 'markdown': '# 正文', 'links': items}


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard),
                        mock.patch.object(collection_ui, 'CollectionRunner', FakeCollectionRunner)):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeCollectionRunner.instances = []
        self.addCleanup(self._release_runners)

    @staticmethod
    def _release_runners():
        for runner in FakeCollectionRunner.instances:
            runner.release.set()

    def make_app(self, smoke=True, visible=False, size='1120x810'):
        if not smoke:
            _sandbox.write_settings()
        ctk.set_appearance_mode('light')
        # Each test its own output folder, so its smoke history starts empty (fix round 1).
        args = (_sandbox.app_args('https://example.invalid/pages', str(_sandbox.out('pages-out-' + self._testMethodName)))
                if smoke else _sandbox.app_args())
        app = StudioApp(args)
        if visible:
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            app.geometry(size + '+0+0')
        else:
            app.withdraw()
        self.addCleanup(self.close, app)
        app.update_idletasks()
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def logged(app):
        """Every line passed to append_log from now on."""
        lines = []
        original = app.append_log

        def capture(line):
            lines.append(str(line))
            original(line)
        app.append_log = capture
        return lines


class RegressionTests(Base):
    def test_r4_collect_done_with_a_failing_library_releases_the_window(self):
        app = self.make_app()
        lines = self.logged(app)
        app.collection_job = app.library.create_job('collect', '测试', {'urls': ['https://example.org/list']})
        app.operation = 'collect'
        app.set_busy(True)
        self.assertEqual(app.collect_stop.cget('state'), 'normal')
        with mock.patch.object(app.library, 'job', side_effect=RuntimeError('database is locked')):
            app.handle('collect_done', {'cancelled': False})
        self.assertFalse(app.busy)
        self.assertEqual(app.collect_stop.cget('state'), 'disabled')
        self.assertIsNone(app.collection_job)
        self.assertIsNone(app.worker)
        failed = i18n.CATALOG['zh']['log.collect.record_failed'].partition('{')[0]     # the text before {error}
        self.assertEqual([line for line in lines if line.startswith(failed)],
                         [zh_t('log.collect.record_failed', error='database is locked')])

    def test_r8_a_failing_tick_still_rearms_the_timer(self):
        app = self.make_app()
        app.smoke_mode = False
        try:
            with mock.patch.object(app.library, 'subscriptions', side_effect=RuntimeError('database is locked')), \
                    mock.patch.object(app, 'after') as after:
                app.subscription_tick()
            after.assert_called_once_with(15000, app.subscription_tick)
        finally:
            app.smoke_mode = True

    def test_r8_an_automatic_check_leaves_the_results_on_screen(self):
        app = self.make_app()
        lines = self.logged(app)
        app.collect_urls.insert('1.0', 'https://example.org/list')
        app.handle('collect_page', page(links(5)))
        app.collect_tree.selection_set(['1', '2'])
        app.collection_selection_changed()
        shown = (dict(app.collected_links), [app.collect_tree.item(i, 'values') for i in app.collect_tree.get_children()],
                 app.collect_urls.get('1.0', 'end-1c'), ui_theme.full_text(app.collect_status))
        # A due subscription with other start pages, checked by the timer.
        options = CollectOptions(['https://example.org/other']).validate()
        key = app.library.subscription('自动订阅', asdict(options), 3600, True, True)
        row = next(item for item in app.library.subscriptions() if item['id'] == key)
        with mock.patch.object(app, 'load_collection_record') as load:
            app.check_subscription(row, automatic=True)
            self.assertTrue(app.busy)
            self.assertEqual(app.operation, 'collect')
            self.assertTrue(app.collection_background)
            self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.subscription_check', name='自动订阅'))
            self.assertEqual(app.current_page, 'download')        # an automatic check does not switch pages
            app.handle('collect_status', '正在采集第1页…')
            app.handle('collect_page', page(links(3, 'https://media.example.org/new/'), 'https://example.org/other'))
            app.handle('collect_error', '网站返回HTTP 503')
            now = (dict(app.collected_links),
                   [app.collect_tree.item(i, 'values') for i in app.collect_tree.get_children()],
                   app.collect_urls.get('1.0', 'end-1c'), ui_theme.full_text(app.collect_status))
            self.assertEqual(now, shown)
            FakeCollectionRunner.instances[-1].release.set()
            app.handle('collect_done', {'cancelled': False})
            load.assert_not_called()
        self.assertFalse(app.busy)
        self.assertFalse(app.collection_background)
        self.assertEqual(app.link_note.winfo_manager(), '', 'nothing under the box once the check ended')
        self.assertEqual((dict(app.collected_links), app.collect_urls.get('1.0', 'end-1c')), (shown[0], shown[2]))
        self.assertIn(zh_t('log.collect.page', title='示例列表', count=3), lines)
        self.assertIn(zh_t('log.collect.unfinished', error='网站返回HTTP 503'), lines)
        # finish_subscription ran: the check is recorded (here as not successful, the job never completed).
        record = next(item for item in app.library.subscriptions() if item['id'] == key)
        self.assertTrue(record['data']['last_error'])


class WorkerTests(Base):
    def test_launch_registers_the_worker_and_the_watchdog_judges_it(self):
        app = self.make_app()
        handled = []
        original = app.handle_collection
        app.handle_collection = lambda kind, value: (handled.append(kind), original(kind, value))
        options = CollectOptions(['https://example.org/list']).validate()
        app.launch_collection(options)
        self.assertEqual(app.worker_operation, 'collect')
        self.assertIsNotNone(app.worker)
        self.assertTrue(app.worker.is_alive())
        for _ in range(3):
            app.watchdog()
        self.assertTrue(app.busy)
        self.assertEqual(handled, [])
        self.assertTrue(app.events.empty())
        worker = app.worker
        FakeCollectionRunner.instances[-1].release.set()
        worker.join(5)
        for _ in range(4):
            app.watchdog()
        self.assertEqual(handled, ['collect_done'])
        self.assertFalse(app.busy)
        self.assertIsNone(app.worker)

    def test_events_of_a_stale_collection_are_dropped(self):
        app = self.make_app()
        app.launch_collection(CollectOptions(['https://example.org/list']).validate())
        emit = FakeCollectionRunner.instances[-1].emit
        emit('collect_status', 'current')
        self.assertEqual(app.events.get_nowait(), ('collect_status', 'current'))
        app.run_token += 1
        emit('collect_status', 'stale')
        self.assertTrue(app.events.empty())
        FakeCollectionRunner.instances[-1].release.set()
        app.handle('collect_done', {'cancelled': True})
        self.assertFalse(app.busy)


class PageTests(Base):
    def test_library_warning_is_shown_above_the_tabs(self):
        store = library.Library(_sandbox.out('pages-warning', 'library.sqlite3'))
        with mock.patch.object(library_ui, 'open_library', return_value=(store, WARNING)):
            app = self.make_app()
        self.assertIs(app.library, store)
        self.assertEqual(app.library_warning, WARNING)
        self.assertTrue(app.library_notice.shown)
        self.assertEqual(app.library_notice.full_text, WARNING)
        self.assertIn(WARNING, app.log_buffer + app.logs.get('1.0', 'end').splitlines())

    def test_no_warning_no_bar(self):
        app = self.make_app()
        self.assertEqual(app.library_warning, '')
        self.assertFalse(app.library_notice.shown)

    def test_add_button_shows_the_selected_count(self):
        app = self.make_app()
        app.handle('collect_page', page(links(3)))
        self.assertEqual(app.collect_add.cget('text'), zh_t('collect.add'))
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        app.collect_tree.selection_set(['0', '2'])
        app.collection_selection_changed()
        self.assertEqual(app.collect_add.cget('text'), f"{zh_t('collect.add')} (2)")
        self.assertEqual(app.collect_add.cget('state'), 'normal')
        self.assertEqual(app.collect_count.cget('text'), zh_t('collect.count', shown=3, total=3))
        # A new filter result clears the selection (row ids are positions).
        app.collect_search.set('第 1')
        self.assertEqual(app.collect_tree.selection(), ())
        self.assertEqual(app.collect_add.cget('text'), zh_t('collect.add'))

    def test_dynamic_mode_caption(self):
        app = self.make_app()
        self.assertEqual(app.collect_note.winfo_manager(), '', 'static pages: no line')
        app.collect_mode.set(collection_ui.MODE_DYNAMIC)
        self.assertEqual(app.collect_note.winfo_manager(), 'grid')
        self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.note.dynamic'))
        app.collect_mode.set(collection_ui.MODE_STATIC)
        self.assertEqual(app.collect_note.winfo_manager(), '')

    def test_history_note_shows_the_last_error(self):
        app = self.make_app()
        job = app.library.create_job('download', '一首歌', {'urls': ['https://example.org/a'], 'output': 'x',
                                                          'mode': MODES[0]})
        app.library.download_event(job, 'item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0,
                                                       'bot_check': False, 'url': 'https://example.org/a',
                                                       'error': 'ERROR: [generic] a: HTTP Error 403: Forbidden'})
        app.refresh_library()
        app.history_tree.selection_set(job)
        app.history_selection()
        # Fix round 1 (WP5-visual-2): a download job has no saved pages, and its error is the short
        # Chinese reason of the task row, not the engine's English line.
        none_done = zh_t('library.history.downloaded', completed=0)
        self.assertEqual(ui_theme.full_text(app.history_note),
                         none_done + zh_t('library.history.last_error', error=zh_t('core.reason.http_403')))
        app.library.download_event(job, 'item_done', {
            'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': True,
            'url': 'https://example.org/a',
            'error': "ERROR: [youtube] OQMcYxMlwGA: Sign in to confirm you're not a bot. Use --cookies-from-browser"})
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note),
                         none_done + zh_t('library.history.last_error', error=zh_t('core.reason.bot_check')))
        # A collect job keeps pages and its own (Chinese) error.
        collect = app.library.create_job('collect', '列表', {'urls': ['https://example.org/list']})
        app.library.change_job(collect, errors=['网站返回HTTP 503'])
        app.refresh_library()
        app.history_tree.selection_set(collect)
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), zh_t('library.history.pages', pages=0, done=none_done)
                         + zh_t('library.history.last_error', error='网站返回HTTP 503'))
        app.history_tree.selection_remove(app.history_tree.selection())
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), '')

    def test_view_of_a_download_job_fills_the_box_only(self):
        app = self.make_app()
        app.urls.delete('1.0', 'end')
        before = (app.mode.get(), app.output.get(), app.resolution.get(), app.playlist.get())
        other = _sandbox.out('pages-other-output')
        job = app.library.create_job('download', '两首歌', {**engine.Options(other, MODES[3]).to_dict(),
                                                         'urls': ['https://example.org/one', 'https://example.org/two']})
        app.refresh_library()
        app.history_tree.selection_set(job)
        app.history_selection()
        app.view_history()
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://example.org/one\nhttps://example.org/two')
        self.assertEqual((app.mode.get(), app.output.get(), app.resolution.get(), app.playlist.get()), before)
        self.assertEqual(app.current_page, 'download')
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.history_loaded'))

    def test_resume_of_a_download_job_launches_the_rest_with_its_options(self):
        app = self.make_app()
        app.urls.delete('1.0', 'end')
        app.urls.insert('1.0', 'https://example.org/typed')
        other = _sandbox.out('pages-resume-output')
        own = engine.Options(_sandbox.out('pages-resume-mp4'), MODES[6])
        job = app.library.create_job('download', '三首歌', {
            **engine.Options(other, MODES[2], '720p').to_dict(),
            'urls': ['https://example.org/one', 'https://example.org/two', 'https://example.org/three'],
            'overrides': {'https://example.org/three': own.to_dict()}})
        # (2.0.0 S2 P2) a finished download is a (link, format) pair in 'done'
        first = library.job_downloads(app.library.job(job)['data'])[0]
        app.library.change_job(job, status='partial', done=[[first['url'], first['format']]])
        app.refresh_library()
        app.history_tree.selection_set(job)
        app.history_selection()
        with mock.patch.object(app, 'launch') as launch:
            app.resume_history()
        launch.assert_called_once()
        urls, options, overrides = launch.call_args.args
        self.assertEqual(urls, ['https://example.org/two', 'https://example.org/three'])
        self.assertEqual((options.mode, options.resolution, str(options.output)), (MODES[2], '720p', str(other)))
        self.assertEqual(list(overrides), ['https://example.org/three'])
        self.assertEqual(overrides['https://example.org/three'].mode, MODES[6])
        self.assertEqual(options.skip_downloaded, app.skip_downloaded.get())
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://example.org/typed')
        # An output setting this version cannot use: a warning, nothing launched.
        bad = app.library.create_job('download', '坏记录', {'urls': ['https://example.org/x'], 'output': 'x',
                                                         'mode': '不存在的格式'})
        app.library.change_job(bad, status='partial')
        app.refresh_library()
        app.history_tree.selection_set(bad)
        app.history_selection()
        with mock.patch.object(app, 'launch') as launch, \
                mock.patch('library_ui.messagebox.showwarning') as warning:
            app.resume_history()
        launch.assert_not_called()
        warning.assert_called_once_with(zh_t('dialog.resume.failed.title'), zh_t('dialog.resume.failed.text'), parent=app)


class FixRound1Tests(Base):
    """Regression tests of WP5 fix round 1 (finding ids in the test names' docstrings)."""

    def test_a_selected_history_row_with_a_failing_library_cannot_wedge_the_window(self):
        """WP5-bughunt-1: set_busy reads the selected history row; a failing read must not raise."""
        app = self.make_app()
        job = app.library.create_job('collect', '测试', {'urls': ['https://example.org/list']})
        app.refresh_library()
        app.history_tree.selection_set(job)
        app.collection_job = job
        app.operation = 'collect'
        app.set_busy(True)
        with mock.patch.object(app.library, 'job', side_effect=RuntimeError('database is locked')):
            app.handle('collect_done', {'cancelled': False})
            self.assertFalse(app.busy)
            self.assertEqual(app.side_status.cget('text'), zh_t('shell.status.ready'))
            self.assertIsNone(app.foreign_activity)
            self.assertEqual(ui_theme.full_text(app.history_note), i18n.t('library.history.unreadable'))
            self.assertEqual((app.history_menu_items()[0].enabled, app.history_resume.cget('state')),
                             (False, 'disabled'))
            # Starting a collection with the same failing read: started and registered, not wedged.
            app.launch_collection(CollectOptions(['https://example.org/list']).validate())
            self.assertTrue(app.busy)
            self.assertEqual(app.worker_operation, 'collect')
            self.assertTrue(app.worker.is_alive())
        FakeCollectionRunner.instances[-1].release.set()
        app.handle('collect_done', {'cancelled': True})
        self.assertFalse(app.busy)
        # And if set_busy itself raised, launch_collection releases the window before re-raising.
        with mock.patch.object(app, 'history_selection', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                app.launch_collection(CollectOptions(['https://example.org/list']).validate())
        self.assertFalse(app.busy)
        self.assertIsNone(app.collection_job)

    def test_collected_links_reach_the_download_box_whole(self):
        """WP5-bughunt-2: links the box parser would cut are queued percent-encoded, with their referer."""
        self.assertEqual(collection_ui.queueable_url('https://example.org/v/1.mp4'), 'https://example.org/v/1.mp4')
        tricky = ['https://media.example.cn/歌曲/第1集（高清）.mp4', 'https://en.wikipedia.org/wiki/Foo_(bar)',
                  "https://example.org/files/it's-me.mp3", 'https://example.org/a b.mp3?x=（1）#frag，']
        for url in tricky:
            queued = collection_ui.queueable_url(url)
            self.assertEqual(urls.extract_urls(queued), [queued], url)
            self.assertEqual(urls.parse_download_text(queued), [queued], url)
            self.assertEqual(unquote(queued), url)
        app = self.make_app()
        app.urls.delete('1.0', 'end')
        items = [{'url': url, 'title': str(n), 'kind': 'media', 'source': 'https://example.org/list'}
                 for n, url in enumerate(tricky[:3])]
        app.collected_links = {item['url']: item for item in items}
        app.render_collection_links()
        app.collect_tree.selection_set(app.collect_tree.get_children())
        app.add_collected()
        queued = [collection_ui.queueable_url(url) for url in tricky[:3]]
        found, options, _origins = app.read_form()
        self.assertEqual(found, queued)
        self.assertEqual(options.referers, {url: 'https://example.org/list' for url in queued})

    def test_a_locked_history_does_not_stop_the_window(self):
        """WP5-bughunt-3: refresh_library (also the one at start-up) survives a failing history."""
        store = library.Library(_sandbox.out('pages-locked', 'library.sqlite3'))
        error = library.sqlite3.OperationalError('database is locked')
        with mock.patch.object(library_ui, 'open_library', return_value=(store, '')), \
                mock.patch.object(store, 'history', side_effect=error):
            app = self.make_app()
            lines = self.logged(app)
            self.assertTrue(app.library_notice.shown)
            # (1.7.0 U1) A lock held by another program is said calmly and retried by itself.
            self.assertEqual(app.library_notice.full_text, i18n.t('library.busy'))
            app.refresh_library()       # the same error again: logged once only
            self.assertEqual(lines, [])
        app.refresh_library()           # readable again: the strip goes
        self.assertFalse(app.library_notice.shown)
        self.assertEqual(app.library_problem, '')
        # Any other read error still shows itself in the strip, as before.
        with mock.patch.object(store, 'history', side_effect=library.sqlite3.DatabaseError('file is not a database')):
            app.refresh_library()
        self.assertEqual(app.library_notice.full_text, zh_t('library.unreadable', error='file is not a database'))
        self.assertEqual([button.cget('text') for button in app.library_notice.buttons], [zh_t('common.refresh')])
        app.refresh_library()
        self.assertFalse(app.library_notice.shown)

    def test_a_failing_automatic_check_backs_off(self):
        """WP5-bughunt-4: a check that cannot start (locked history) is not retried every 15 s."""
        app = self.make_app()
        options = CollectOptions(['https://example.org/feed']).validate()
        key = app.library.subscription('锁住的订阅', asdict(options), 3600, True, True)
        with app.library.db() as db:
            db.execute('UPDATE subscriptions SET next_check=0 WHERE id=?', (key,))
        app.smoke_mode = False
        try:
            locked = library.sqlite3.OperationalError('database is locked')
            with mock.patch.object(app.library, 'create_job', side_effect=locked), \
                    mock.patch.object(app.library, 'finish_subscription') as finish, \
                    mock.patch.object(app, 'after'):
                app.subscription_tick()
                finish.assert_not_called()      # writing the record would wait for the same lock
            self.assertFalse(app.busy)
            self.assertIsNone(app.active_subscription)
            self.assertGreaterEqual(app.subscription_backoff[key], time.time() + 3500)
            with mock.patch.object(app, 'check_subscription') as check, mock.patch.object(app, 'after'):
                app.subscription_tick()
                check.assert_not_called()
            app.subscription_backoff[key] = 0
            with mock.patch.object(app, 'check_subscription') as check, mock.patch.object(app, 'after'):
                app.subscription_tick()
                check.assert_called_once()
        finally:
            app.smoke_mode = True

    def test_a_manual_check_shows_its_own_start_pages(self):
        """WP5-bughunt-5: the record is loaded after set_busy(False), so the box takes the pages."""
        app = self.make_app()
        app.collect_urls.insert('1.0', 'https://old.example.org/a')
        options = CollectOptions(['https://sub.example.org/feed'], selector='article').validate()
        key = app.library.subscription('手动订阅', asdict(options), 3600, True, True)
        row = next(item for item in app.library.subscriptions() if item['id'] == key)
        app.check_subscription(row)
        self.assertEqual(app.current_page, 'collect')
        FakeCollectionRunner.instances[-1].release.set()
        app.handle('collect_done', {'cancelled': False})
        self.assertFalse(app.busy)
        self.assertEqual(app.collect_urls.get('1.0', 'end-1c'), 'https://sub.example.org/feed')
        self.assertEqual(app.collect_selector.get(), 'article')

    def test_a_refresh_keeps_the_pending_selection(self):
        """WP5-bughunt-6: refresh_library re-selects the picked new entries by their link."""
        app = self.make_app()
        options = CollectOptions(['https://sub.example.org/feed']).validate()
        key = app.library.subscription('订阅', asdict(options), 3600, True, True)
        app.library.finish_subscription(key, [page(links(5), 'https://sub.example.org/feed')], True)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        self.assertEqual(len(app.pending_tree.get_children()), 5)
        app.pending_tree.selection_set(['1', '3'])
        picked = [app.visible_pending[int(n)]['url'] for n in app.pending_tree.selection()]
        app.refresh_library()
        pump(app, 0.1)      # the deferred <<TreeviewSelect>> of the subscription table too
        self.assertEqual([app.visible_pending[int(n)]['url'] for n in app.pending_tree.selection()], picked)

    def test_the_collect_page_says_a_background_check_runs(self):
        """WP5-bughunt-7, WP5-visual-3: the note under the options explains the disabled controls."""
        app = self.make_app()
        options = CollectOptions(['https://example.org/other']).validate()
        key = app.library.subscription('后台订阅', asdict(options), 3600, True, True)
        row = next(item for item in app.library.subscriptions() if item['id'] == key)
        app.check_subscription(row, automatic=True)
        self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.background', name='后台订阅'))
        self.assertEqual(app.collect_note.cget('text_color'), ui_theme.ACCENT_TEXT)
        FakeCollectionRunner.instances[-1].release.set()
        app.handle('collect_done', {'cancelled': False})
        self.assertEqual(app.collect_note.winfo_manager(), '', 'static pages: the line goes with the check')

    def test_the_start_url_box_looks_disabled_while_busy(self):
        """WP5-visual-4."""
        app = self.make_app()
        self.assertEqual(tuple(app.collect_urls.cget('text_color')), ui_theme.INK)
        app.operation = 'collect'
        app.set_busy(True)
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.FAINT)
        self.assertEqual(app.collect_urls.cget('fg_color'), ui_theme.SURFACE)
        app.set_busy(False)
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.INK)
        self.assertEqual(app.collect_urls.cget('fg_color'), ui_theme.SURFACE_ALT)

    def test_actions_without_a_selection_are_grey_and_do_nothing(self):
        """WP5-visual-5; (2.0.0 S4, subscriptions.md 4) no "please select first": the controls are grey, the
        actions do nothing, the empty view says what to do."""
        app = self.make_app()
        lines = self.logged(app)
        with mock.patch('library_ui.messagebox.showinfo') as info:
            app.check_subscription()
            self.assertTrue(app.subscription_empty.winfo_manager(), 'no subscription: the empty state')
            self.assertEqual(app.subscription_bar.winfo_manager(), '')
            options = CollectOptions(['https://example.org/feed']).validate()
            key = app.library.subscription('订阅', asdict(options), 3600, True, True)
            app.refresh_library()
            self.assertEqual(app.subscription_tree.selection(), (str(key),), 'the first row is selected by itself')
            app.subscription_tree.selection_remove(app.subscription_tree.selection())
            app.subscription_selection()
            self.assertEqual(app.subscription_check.cget('state'), 'disabled')
            self.assertFalse(any(item.enabled for item in app.subscription_menu_items()[:4]))
            for action in (app.check_subscription, app.toggle_subscription, app.queue_subscription):
                action()
                self.assertEqual(ui_theme.full_text(app.subscription_note), '')
            self.assertFalse(app.busy)
            self.assertEqual(app.archive_open.cget('state'), 'disabled')
            app.open_archive()
            app.attach_transcript()
            info.assert_not_called()
        self.assertFalse(app.busy)
        self.assertEqual(lines, [])

    def test_ctrl_enter_in_the_start_url_box_starts(self):
        """WP5-visual-7."""
        app = self.make_app()
        with mock.patch.object(app, 'start_collection') as start:
            result = app._collect_from_box()
        start.assert_called_once_with()
        self.assertEqual(result, 'break')
        self.assertTrue(app.collect_urls._textbox.bind('<Control-Return>'))


class VisibleTests(Base):
    def test_placeholders_are_shown_while_the_variables_are_empty(self):
        app = self.make_app(visible=True)
        app.handle('collect_page', page(links(2)))      # the search box is shown with results
        app.library.archive_index(str(_sandbox.out('pages-placeholder', 'item')),
                                  {'url': 'https://example.org/a', 'source': 'https://example.org/list', 'title': 'A',
                                   'uploader': '', 'files': ['a.m4a'], 'transcripts': []})
        app.refresh_library()                            # and the archive search with an archive
        entries = [('collect', None, app.collect_scope, app.collect_selector),
                   ('collect', None, app.collect_search_entry, app.collect_search),
                   ('library', library_ui.TAB_ARCHIVE, app.archive_search_entry, app.archive_search)]
        for name, tab, entry, variable in entries:
            app.show_page(name)
            if tab:
                app.library_tabs.set(tab)
            pump(app, 0.1)
            self.assertTrue(entry.placeholder_label.winfo_ismapped(), f'{name} {tab}: placeholder not shown')
            variable.set('x')
            pump(app, 0.05)
            self.assertFalse(entry.placeholder_label.winfo_ismapped(), f'{name} {tab}: placeholder stays over text')
            variable.set('')
            pump(app, 0.05)
            self.assertTrue(entry.placeholder_label.winfo_ismapped())
        app.show_page('collect')
        pump(app, 0.1)
        self.assertTrue(app.collect_urls.placeholder_label.winfo_ismapped())
        app.collect_urls.insert('1.0', 'https://example.org/list')
        pump(app, 0.05)
        self.assertFalse(app.collect_urls.placeholder_label.winfo_ismapped())

    def test_the_subscription_view_fits_at_the_minimum_size(self):
        app = self.make_app(visible=True, size='1000x740')
        options = CollectOptions(['https://example.org/feed']).validate()
        key = app.library.subscription('订阅', asdict(options), 3600, True, True)
        app.library.finish_subscription(key, [page(links(5), 'https://example.org/feed')], True)
        app.show_page('library')
        app.library_tabs.set(library_ui.TAB_SUBSCRIPTIONS)
        app.refresh_library()
        pump(app, 0.3)
        scale = ctk.ScalingTracker.get_widget_scaling(app)
        for widget in app.pending_bar.winfo_children():
            if widget.winfo_ismapped():
                self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), app.pending_bar.winfo_width(), widget)
        self.assertTrue(app.pending_queue.winfo_ismapped())
        for tree in (app.subscription_tree, app.pending_tree):
            self.assertGreaterEqual(tree.winfo_height() / scale, 4 * ui_theme.TABLE_ROW_H, 'four rows or more')
        # The CSS entry keeps its place beside the menus at the minimum width.
        app.show_page('collect')
        pump(app, 0.2)
        self.assertEqual(int(app.collect_scope.grid_info()['row']), 0)
        self.assertGreaterEqual(app.collect_scope.winfo_width() / scale, 199)


class ZSnapshotTest(unittest.TestCase):
    """Last: the layout assertions of snapshot_ui for every page (no app of this file is alive any more)."""

    def test_snapshot_assertions(self):
        import snapshot_ui
        failures = snapshot_ui.run(pages=list(snapshot_ui.PAGES), sizes=('1000x740', '1120x810'), shots=False)
        self.assertEqual(failures, [])
        failures = snapshot_ui.run(pages=['collect', 'library'], sizes=('1000x740', '1120x810'), shots=False,
                                   state='filled')
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
