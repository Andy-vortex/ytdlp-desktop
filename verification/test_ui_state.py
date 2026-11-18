"""UI-side state of StudioApp (1.6.0 WP4 section H): R1-R3, R5-R7, R13, R16 and the download page logic.

Every app here is created and destroyed on its own (one live StudioApp per process).  Non-smoke
apps start from _sandbox.write_settings() (update check and clipboard hint off).  The module
replaces updater.check_latest by a guard that fails the test when it is called, and
StudioApp.clipboard_get by one that raises TclError, except in the two tests made for them.
"""
import _sandbox
import hashlib
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import settings
import ui_download
import ui_log
import ui_settings
import ui_theme
import unfinished
import updater
from engine import MODES, RESOLUTIONS, Options
from progress import SmoothProgressBar
from studio import StudioApp, valid_geometry

SHARE = '【分享】好听 https://www.youtube.com/watch?v=AAAAAAAAAAA&si=xyz 还有这首 https://b23.tv/abcdef 复制此链接'
MIX = 'https://www.youtube.com/watch?v=BBBBBBBBBBB&list=RDBBBBBBBBBBB&start_radio=1'


def no_network(*_args, **_kwargs):
    raise AssertionError('updater.check_latest must not be called in this test')


def no_clipboard(self, *_args, **_kwargs):
    raise tk.TclError('CLIPBOARD selection doesn\'t exist or form "STRING" not defined')


class FakeRunner:
    """Stands in for engine.Runner: download() runs ``script`` (or waits until released)."""
    instances = []
    script = None

    def __init__(self, emit, library=None):
        self.emit, self.library = emit, library
        self.origins = {}
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.enqueued = []
        FakeRunner.instances.append(self)

    def download(self, urls, options, overrides=None):
        self.urls, self.options, self.overrides = list(urls), options, overrides
        if FakeRunner.script is not None:
            FakeRunner.script(self)
        else:
            self.release.wait(20)

    def enqueue(self, urls, options=None, overrides=None):
        self.enqueued.append((list(urls), options))
        return list(urls)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def finish(runner, success=True):
    """Script: one item, its item_done and done."""
    url = runner.urls[0]
    runner.emit('item', {'index': 1, 'total': 1, 'url': url})
    runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': success, 'files': 1 if success else 0,
                              'bot_check': False, 'url': url})
    runner.emit('done', {'success': 1 if success else 0, 'failed': 0 if success else 1, 'files': [],
                         'cancelled': False, 'failed_urls': [] if success else [url], 'pending_urls': [],
                         'failed_path': None, 'archive_errors': [], 'bot_check': False})


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard),
                        mock.patch.object(engine, 'PACE_SECONDS', (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        engine.Runner.reset_pacing()
        engine.save_unfinished([])
        settings_file = _sandbox.state_dir() / 'settings.json'
        if settings_file.exists():
            settings_file.unlink()
        FakeRunner.instances = []
        FakeRunner.script = None
        self.addCleanup(self._release_runners)

    def _release_runners(self):
        for runner in FakeRunner.instances:
            runner.release.set()
        FakeRunner.script = None
        engine.save_unfinished([])

    def make_app(self, smoke=False, visible=False, **values):
        if not smoke:
            _sandbox.write_settings(**values)
        ctk.set_appearance_mode('light')
        args = (_sandbox.app_args('https://example.invalid/smoke', str(_sandbox.out('smoke-out')))
                if smoke else _sandbox.app_args())
        app = StudioApp(args)
        if visible:
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            app.geometry('1120x810+0+0')
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

    def options(self, **changes):
        return Options(_sandbox.out('downloads'), batch_safe=False, **changes)


class ARegressionTests(Base):
    def test_r1_history_failure_does_not_end_the_job_badly(self):
        app = self.make_app()
        FakeRunner.script = finish

        def broken(job, kind, value):
            if kind == 'done':
                raise RuntimeError('database is locked')
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(app.library, 'download_event', side_effect=broken):
            self.assertTrue(app.launch(['https://example.org/a'], self.options()))
            self.assertTrue(pump(app, lambda: not app.busy))
        self.assertFalse(app.busy)
        self.assertEqual(app.rows[0]['state'], 'done')
        self.assertIn('历史记录写入失败，下载本身不受影响。', app.log_buffer + [app.logs.get('1.0', 'end')][0].splitlines())

    def test_r2_create_job_failure_and_runner_failure(self):
        app = self.make_app()
        FakeRunner.script = finish
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(app.library, 'create_job', side_effect=RuntimeError('disk full')):
            self.assertTrue(app.launch(['https://example.org/a'], self.options()))
            self.assertTrue(pump(app, lambda: not app.busy))
        self.assertEqual(app.rows[0]['state'], 'done')
        with mock.patch.object(ui_download, 'Runner', side_effect=RuntimeError('no runner')), \
                mock.patch('ui_download.messagebox.showerror') as error:
            self.assertFalse(app.launch(['https://example.org/b'], self.options()))
        self.assertEqual(error.call_count, 1)
        self.assertFalse(app.busy)
        self.assertIsNone(app.worker)
        # WP4-bughunt-3: no waiting rows of a job that never ran, and its history job is not left 'running'.
        self.assertEqual(app.row_model, [])
        self.assertEqual(ui_theme.full_text(app.queue_stats), '暂无任务')
        self.assertEqual(app.library.job(app.download_job)['status'], 'interrupted')

    def test_r3_watchdog(self):
        app = self.make_app()
        FakeRunner.script = lambda runner: None          # the worker ends without a single event
        handled = []
        original = app.handlers['done']
        app.handlers['done'] = lambda value: (handled.append(value), original(value))
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/a'], self.options()))
            app.worker.join(5)
            for _ in range(4):
                app.watchdog()
        self.assertEqual(len(handled), 1)
        self.assertTrue(handled[0]['cancelled'])
        self.assertFalse(app.busy)
        self.assertIsNone(app.worker)
        # WP4-bughunt-3: the synthetic done also ends the history job.
        self.assertEqual(app.library.job(app.download_job)['status'], 'interrupted')
        # A finished download's thread is never judged again: a collection without a registered worker.
        app.operation = 'collect'
        app.set_busy(True)
        for _ in range(3):
            app.watchdog()
        self.assertTrue(app.busy)
        self.assertEqual(len(handled), 1)
        # A live registered thread is never ended.
        release = threading.Event()
        live = threading.Thread(target=release.wait, args=(10,), daemon=True)
        live.start()
        app.worker, app.worker_operation = live, 'collect'
        for _ in range(3):
            app.watchdog()
        self.assertTrue(app.busy)
        release.set()
        live.join(5)
        # A dead worker of another operation is ignored.
        app.worker, app.worker_operation = live, 'update'
        for _ in range(3):
            app.watchdog()
        self.assertTrue(app.busy)
        app.handle('collect_done', {'cancelled': True})
        self.assertFalse(app.busy)

    def test_r5_poll_survives_handler_and_tick_failures(self):
        app = self.make_app()
        original = app.handlers['log']
        calls = []

        def once(value):
            calls.append(value)
            if len(calls) == 1:
                raise RuntimeError('handler failed')
            original(value)
        app.handlers['log'] = once
        app.events.put(('log', 'first'))
        app.events.put(('log', 'second'))
        app.poll()
        app.poll()
        self.assertEqual(calls, ['first', 'second'])
        self.assertIn('second', app.log_buffer + app.logs.get('1.0', 'end').splitlines())
        with mock.patch.object(app, 'render_wait', side_effect=RuntimeError('tick failed')), \
                mock.patch.object(app, 'after') as after:
            app.poll()
        after.assert_called_with(25, app.poll)

    def test_r6_force_close_while_busy(self):
        app = self.make_app()
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/a'], self.options()))
        self.assertTrue(app.busy)
        runner = app.runner
        app.force_close()
        with self.assertRaises(tk.TclError):
            app.winfo_exists()
        self.assertTrue(runner.cancelled.wait(5))

    def test_r7_bad_stored_values_open_with_defaults(self):
        app = self.make_app(mode='x', resolution='y', naming='z')
        self.assertEqual(app.mode.get(), MODES[0])
        self.assertEqual(app.resolution.get(), RESOLUTIONS[0])
        self.assertEqual(app.naming.get(), 'title')

    def test_r16_geometry(self):
        virtual = (0, 0, 5120, 2880)
        cases = [('1120x810+100+100', '1120x810+100+100'), ('1000x740+0+0', '1000x740+0+0'),
                 ('999x810+0+0', None), ('1120x739+0+0', None), ('1120x810+5100+0', None),
                 ('1120x810+-40+10', '1120x810+-40+10'), ('1120x810+-60+10', None), ('1120x810+10+-5', None),
                 ('1120x810+10+2790', None), ('garbage', None), ('', None), (None, None)]
        for text, expected in cases:
            self.assertEqual(valid_geometry(text, virtual), expected, text)
        self.assertEqual(valid_geometry('1120x810+99999+0', None), '1120x810+99999+0')
        app = self.make_app()
        app.normal_geometry = '1200x800+50+60'
        app.save_settings()
        self.assertEqual(settings.load()['geometry'], '1200x800+50+60')

    def test_r16_zoomed_start_keeps_the_normal_geometry(self):
        stored = '1150x820+40+50'
        _sandbox.write_settings(geometry=stored, zoomed=True)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args())
        self.addCleanup(self.close, app)
        app.attributes('-alpha', 0.0)
        self.assertTrue(pump(app, lambda: app.state() == 'zoomed', 5))
        self.assertEqual(app.normal_geometry, stored)
        app.finish_close()
        values = settings.load()
        self.assertEqual(values['geometry'], stored)
        self.assertTrue(values['zoomed'])


class BRowTests(Base):
    def test_four_hundred_links_are_capped(self):
        app = self.make_app(visible=True)
        links = [f'https://example.org/v/{number}' for number in range(400)]
        app.populate_queue(links)
        # (polish, ui-performance-1) 4 at once, the rest in timed steps; at most ROW_LIMIT rows get widgets
        self.assertEqual(len(app.rows), ui_download.ROW_FIRST)
        self.assertTrue(pump(app, lambda: not app.row_queue, 30))
        pump(app, timeout=0.3)
        self.assertEqual(len(app.row_model), 400)
        self.assertEqual(len(app.rows), app.row_cap())
        self.assertLessEqual(app.row_cap(), ui_download.ROW_LIMIT)
        self.assertLess(app.task_list.winfo_reqheight(), 32767)
        self.assertTrue(app.row_footer.winfo_ismapped())
        self.assertIn(f'还有 {400 - app.row_cap()} 条未显示', ui_theme.full_text(app.row_footer))

    def test_a_new_queue_drops_the_old_build(self):
        app = self.make_app()
        app.populate_queue([f'https://example.org/a/{number}' for number in range(60)])
        app.populate_queue(['https://example.org/b/1', 'https://example.org/b/2'])
        pump(app, timeout=0.5)
        self.assertEqual(len(app.rows), 2)
        self.assertEqual([row['url'] for row in app.rows], ['https://example.org/b/1', 'https://example.org/b/2'])

    def test_appended_rows_keep_their_order(self):
        app = self.make_app()
        # (polish) 30 + 3 rows: below the row cap (ROW_LIMIT 50), so every row gets widgets
        app.populate_queue([f'https://example.org/a/{number}' for number in range(30)])
        app.append_rows(['https://example.org/x/1', 'https://example.org/x/2', 'https://example.org/x/3'])
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        self.assertEqual(len(app.rows), 33)
        self.assertTrue(all(row['index'] == number for number, row in enumerate(app.rows)))
        self.assertEqual(app.rows[32]['url'], 'https://example.org/x/3')

    def test_item_done_variants(self):
        app = self.make_app()
        app.populate_queue(['https://example.org/1', 'https://www.youtube.com/watch?v=CCCCCCCCCCC',
                            'https://example.org/3', 'https://example.org/4'])
        app.operation = 'download'
        app.handle('item_done', {'index': 1, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                 'skipped': 'downloaded', 'title': '旧歌', 'path': ''})
        app.handle('item_done', {'index': 2, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': True,
                                 'skipped': 'bot_check'})
        app.handle('item', {'index': 3, 'total': 4, 'url': 'https://example.org/3'})
        app.handle('item_done', {'index': 3, 'cancelled': True, 'success': False, 'files': 0})
        app.handle('item_done', {'index': 4, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                 'reason': '网络超时', 'stale_engine': True})
        chips = [row['badge'].cget('text') for row in app.rows]
        self.assertEqual(chips, ['已下载过', '已暂缓', '已停止', '失败'])
        self.assertEqual([row['state'] for row in app.rows], ['skipped', 'blocked', 'stopped', 'failed'])
        # fix round 1: the skipped row does not repeat its title; the way out of a stale engine comes first
        self.assertEqual(ui_theme.full_text(app.rows[0]['detail']), '01  ·  已跳过 · 右键可“仍然下载”')
        self.assertEqual(ui_theme.full_text(app.rows[1]['detail']), '02  ·  等 YouTube 验证解除后重试')
        self.assertEqual(ui_theme.full_text(app.rows[3]['detail']), '04  ·  引擎可能过旧，可在偏好设置更新 · 网络超时')
        app.handle('item_done', {'index': 4, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.STALE_REASON, 'stale_engine': True, 'error': 'ERROR: x'})
        self.assertEqual(ui_theme.full_text(app.rows[3]['detail']), f'04  ·  {engine.STALE_REASON}，可在偏好设置更新')
        self.assertIn('ERROR: x', app.row_reason(3))           # 查看原因 shows the whole reason and the error
        self.assertEqual(app.rows[2]['look'][0], ui_theme.ICON_STOP)

    def test_refill_after_scrolling_shows_the_new_rows(self):
        """WP4-bughunt-1: a short queue after a long one scrolled to its end is drawn inside the viewport."""
        app = self.make_app(visible=True)
        links = [f'https://example.org/long/{number}' for number in range(40)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        pump(app, timeout=0.3)
        canvas = app.task_list.canvas
        canvas.yview_moveto(1.0)
        pump(app, timeout=0.3)
        self.assertGreater(canvas.yview()[0], 0.5)
        app.populate_queue(links[:3])
        pump(app, timeout=0.5)
        top = app.rows[0]['frame'].winfo_rooty() - canvas.winfo_rooty()
        self.assertGreaterEqual(top, 0)
        self.assertLess(top + app.rows[0]['frame'].winfo_height(), canvas.winfo_height())
        self.assertEqual(canvas.yview(), (0.0, 1.0))
        self.assertFalse(app.task_list.bar_visible)

    def test_the_list_follows_the_active_row(self):
        """WP4-visual-4: the row being downloaded is scrolled into view, unless the owner scrolled by hand."""
        app = self.make_app(visible=True)
        links = [f'https://example.org/q/{number}' for number in range(20)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        app.operation = 'download'
        app.busy = True
        app.handle('item', {'index': 15, 'total': 20, 'url': links[14]})
        pump(app, timeout=0.4)
        canvas, frame = app.task_list.canvas, app.rows[14]['frame']
        top = frame.winfo_rooty() - canvas.winfo_rooty()
        self.assertTrue(0 <= top and top + frame.winfo_height() <= canvas.winfo_height(), top)
        canvas.yview_moveto(0)
        app.list_scrolled_at = time.monotonic()                 # what a wheel turn over the list records
        app.handle('item', {'index': 18, 'total': 20, 'url': links[17]})
        pump(app, timeout=0.4)
        self.assertEqual(canvas.yview()[0], 0.0)
        app.busy = False

    def test_a_wheel_turn_over_the_rows_stops_the_follow_during_a_wait(self):
        """WP4-verify2-1: the real <MouseWheel> binding on a row records the hand scroll, and the
        per-second 'waiting' events of a long pause do not pull the list back to the active row."""
        app = self.make_app(visible=True)
        links = [f'https://example.org/w/{number}' for number in range(20)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        app.operation = 'download'
        app.busy = True
        app.handle('item', {'index': 15, 'total': 20, 'url': links[14]})
        # (1.8.0) the waiting reason as the engine emits it (a message, MESSAGES.md 8.3)
        reason = engine.msg('core.wait.rate_queue')
        app.handle('waiting', {'index': 15, 'total': 20, 'seconds': 300, 'reason': reason})
        pump(app, timeout=0.4)
        canvas = app.task_list.canvas
        self.assertGreater(canvas.yview()[0], 0.0)                # followed to row 15 when the wait began
        app.list_scrolled_at = 0.0
        for _ in range(40):
            app.rows[10]['title'].event_generate('<MouseWheel>', delta=120, x=5, y=5)
            app.update()
        self.assertGreater(app.list_scrolled_at, 0.0)
        pump(app, timeout=0.2)
        before = canvas.yview()
        self.assertEqual(before[0], 0.0)
        app.handle('waiting', {'index': 15, 'total': 20, 'seconds': 299, 'reason': reason})
        pump(app, timeout=0.4)
        self.assertEqual(canvas.yview(), before)
        # Even after the hands-off time, the repeated events of the same wait leave the list alone.
        app.list_scrolled_at = 0.0
        app.handle('waiting', {'index': 15, 'total': 20, 'seconds': 298, 'reason': reason})
        pump(app, timeout=0.4)
        self.assertEqual(canvas.yview(), before)
        app.busy = False

    def test_removing_a_row_keeps_the_scroll_position(self):
        """WP4-verify2-2: 从列表移除 far down a long idle list does not jump the view to the first row."""
        app = self.make_app(visible=True)
        links = [f'https://example.org/r/{number}' for number in range(30)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        pump(app, timeout=0.2)
        canvas = app.task_list.canvas
        canvas.yview_moveto(1.0)
        pump(app, timeout=0.2)
        self.assertEqual(canvas.yview()[1], 1.0)
        app.remove_row(29)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        pump(app, timeout=0.3)
        self.assertEqual(len(app.row_model), 29)
        self.assertAlmostEqual(canvas.yview()[1], 1.0, places=3)  # still at the end, clamped to the shorter list
        frame = app.rows[28]['frame']
        bottom = frame.winfo_rooty() + frame.winfo_height() - canvas.winfo_rooty()
        self.assertTrue(0 < bottom <= canvas.winfo_height(), bottom)
        # A row in the middle: the view stays at the same offset.
        canvas.yview_moveto(0.4)
        pump(app, timeout=0.2)
        box = canvas.bbox('all')
        offset = canvas.yview()[0] * (box[3] - box[1])
        self.assertGreater(offset, 0)
        app.remove_row(25)
        self.assertTrue(pump(app, lambda: not app.row_queue, 10))
        pump(app, timeout=0.3)
        box = canvas.bbox('all')
        self.assertLess(abs(canvas.yview()[0] * (box[3] - box[1]) - offset), 3)


class CDownloadTests(Base):
    def test_launch_refuses_an_output_that_is_a_file(self):
        app = self.make_app()
        blocker = _sandbox.out('not-a-folder.txt')
        blocker.write_text('x', encoding='utf-8')
        app.urls.insert('1.0', 'https://example.org/keep')
        with mock.patch('ui_download.messagebox.showwarning') as warning:
            self.assertFalse(app.launch(['https://example.org/a'], Options(blocker / 'sub')))
        self.assertEqual(warning.call_count, 1)
        self.assertFalse(app.busy)
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://example.org/keep')

    def test_enqueue_with_an_empty_box_and_after_stop(self):
        app = self.make_app()
        runner = engine.Runner(lambda *_: None)
        app.runner, app.operation = runner, 'download'
        app.set_busy(True)
        self.assertEqual(app.enqueue_links(), 0)          # raises nothing, no dialog
        self.assertIn('请先粘贴', ui_theme.full_text(app.link_hint))
        app.urls.insert('1.0', 'https://example.org/late')
        app.enqueue_links()
        self.assertEqual(ui_theme.full_text(app.link_hint), '这些链接已经在队列里')
        runner.cancel()
        app.enqueue_links()
        self.assertEqual(ui_theme.full_text(app.link_hint), '队列刚结束，请再点一次“开始下载”')
        app.set_busy(False)

    def test_enqueue_while_downloading(self):
        app = self.make_app()
        app.urls.insert('1.0', 'https://example.org/first')
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
            self.assertTrue(app.busy)
            self.assertEqual(app.start_button.cget('text'), '加入队列')
            self.assertEqual(app.urls.cget('state'), 'normal')
            app.urls.insert('1.0', 'https://example.org/second')
            app.start()
        self.assertEqual([row['url'] for row in app.row_model], ['https://example.org/first', 'https://example.org/second'])
        self.assertEqual(app.runner.enqueued[0][0], ['https://example.org/second'])
        self.assertEqual(app.urls.get('1.0', 'end-1c'), '')
        self.assertEqual(ui_theme.full_text(app.link_hint), '已加入队列 1 条')

    def test_retry_one_row_while_downloading(self):
        app = self.make_app()
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/a', 'https://example.org/b'],
                                       self.options(skip_downloaded=True)))
            app.handle('item', {'index': 1, 'total': 2, 'url': 'https://example.org/a'})
            app.handle('item_done', {'index': 1, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False})
            app.retry_row(0)
        urls, options = app.runner.enqueued[0]
        self.assertEqual(urls, ['https://example.org/a'])
        self.assertFalse(options.skip_downloaded)
        self.assertEqual(len(app.row_model), 3)

    def test_input_is_refused_while_another_operation_runs(self):
        app = self.make_app()
        app.operation = 'collect'
        app.set_busy(True)
        self.assertEqual(app.urls.cget('state'), 'disabled')
        self.assertEqual(app.start_button.cget('state'), 'disabled')
        self.assertEqual(app.add_links('https://example.org/x'), 0)
        self.assertEqual(ui_theme.full_text(app.link_hint), '当前任务结束后才能添加链接')
        app.set_busy(False)
        self.assertEqual(app.urls.cget('state'), 'normal')

    def test_paste(self):
        app = self.make_app()
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, **_: 'just some words'):
            self.assertIsNone(app.on_box_paste())
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, **_: SHARE):
            self.assertEqual(app.paste(), 2)
            self.assertEqual(ui_theme.full_text(app.link_hint), '已添加 2 条')
            self.assertEqual(app.urls.get('1.0', 'end-1c').splitlines(),
                             ['https://www.youtube.com/watch?v=AAAAAAAAAAA&si=xyz', 'https://b23.tv/abcdef'])
            self.assertEqual(app.paste(), 0)
            self.assertEqual(ui_theme.full_text(app.link_hint), '已添加 0 条 · 跳过 2 条重复')
            self.assertEqual(app.on_box_paste(), 'break')
        self.assertEqual(app.link_count.cget('text'), '2 条链接')

    def test_mix_note(self):
        app = self.make_app()
        app.urls.insert('1.0', MIX)
        app.links_changed()
        self.assertEqual(app.link_note.winfo_manager(), 'grid')
        self.assertEqual(ui_theme.full_text(app.link_note), '已忽略 1 条链接里的播放列表/合辑参数，只下载当前这一首。')
        app.urls.delete('1.0', 'end')
        app.links_changed()
        self.assertEqual(app.link_note.winfo_manager(), '')

    def test_start_clears_the_box_and_undo_restores_it(self):
        app = self.make_app()
        app.urls.insert('1.0', MIX)
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
        self.assertTrue(app.busy)
        self.assertEqual(app.runner.urls, ['https://www.youtube.com/watch?v=BBBBBBBBBBB'])
        self.assertEqual(app.urls.get('1.0', 'end-1c'), '')
        self.assertEqual(app.notice_kind, 'undo')
        app.undo_launch()
        self.assertEqual(app.urls.get('1.0', 'end-1c'), MIX)
        self.assertIsNone(app.notice_kind)

    def test_on_format_for_every_preset(self):
        import formats
        app = self.make_app()
        for preset in formats.PRESETS:
            app.format.set(preset.label)
            app.on_format()
            self.assertEqual(app.mode.get(), preset.mode)
            video = preset.kind == 'video'
            self.assertEqual(app.quality_menu.winfo_manager(), 'grid' if video else '', preset.id)
            self.assertEqual(app.subtitle_check.winfo_manager(), 'grid' if video else '', preset.id)
            self.assertEqual(app.quality_menu.cget('state'), 'normal' if video else 'disabled')
        self.assertEqual(ui_download.FORMATS, [preset.label for preset in formats.PRESETS])

    def test_progress_bar_colours(self):
        app = self.make_app()
        default = SmoothProgressBar(app)
        explicit = SmoothProgressBar(app, height=8, progress_color='#123456', fg_color='#ABCDEF', surface='#FFFFFF')
        for bar in (default, explicit):
            bar.set(.5, immediate=True)
            self.assertEqual(bar.target, .5)
            bar.configure(mode='indeterminate')
            self.assertEqual(bar.cget('mode'), 'indeterminate')
            bar.start()
            bar.stop()
        self.assertEqual(default.canvas.itemcget(default._fill, 'fill').upper(), ui_theme.ACCENT[0])
        self.assertEqual(explicit.canvas.itemcget(explicit._fill, 'fill').upper(), '#123456')
        self.assertEqual(explicit.canvas.itemcget(explicit._track, 'fill').upper(), '#ABCDEF')
        default.destroy()
        explicit.destroy()

    def test_retry_keeps_the_form_r13(self):
        app = self.make_app()
        engine.store_failed_job(['https://example.org/two'], Options(_sandbox.out('original'), MODES[2], '720p', True))
        app.unfinished = engine.load_unfinished()
        app.refresh_failed_button()
        app.urls.insert('1.0', 'https://example.org/typed')
        before = (app.output.get(), app.mode.get(), app.resolution.get(), app.playlist.get())
        with mock.patch.object(app, 'launch', return_value=True) as launch, \
                mock.patch.object(app, 'save_settings') as save:
            app.retry_button.invoke()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], ['https://example.org/two'])
        self.assertEqual(launch.call_args.args[1].resolution, '720p')
        save.assert_not_called()
        self.assertEqual((app.output.get(), app.mode.get(), app.resolution.get(), app.playlist.get()), before)
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://example.org/typed')


class DBotRetryTests(Base):
    def blocked_done(self, app):
        app.operation = 'download'
        app.set_busy(True)
        app.handle('done', {'success': 0, 'failed': 1, 'files': [], 'cancelled': False,
                            'failed_urls': ['https://www.youtube.com/watch?v=DDDDDDDDDDD'],
                            'pending_urls': ['https://www.youtube.com/watch?v=DDDDDDDDDDD'], 'failed_path': None,
                            'archive_errors': [], 'bot_check': True, 'blocked': 'bot_check', 'unfinished': 1})

    def store_blocked(self):
        options = Options(_sandbox.out('yt')).to_dict()
        entries = [unfinished.entry('https://www.youtube.com/watch?v=DDDDDDDDDDD', options, 'blocked'),
                   unfinished.entry('https://www.youtube.com/watch?v=EEEEEEEEEEE', options, 'blocked'),
                   unfinished.entry('https://example.org/failed', options, 'failed')]
        engine.save_unfinished(entries)

    def test_rounds_and_give_up(self):
        app = self.make_app()
        self.store_blocked()
        self.blocked_done(app)
        self.assertEqual(app.bot_retry['round'], 1)
        self.assertAlmostEqual(app.bot_retry['deadline'] - time.monotonic(), 1800, delta=5)
        self.assertEqual(ui_theme.full_text(app.side_status), '等待 YouTube 验证')
        self.assertEqual(app.notice_kind, 'bot')
        self.assertGreater(app.prefs['bot_check_at'], 0)
        with mock.patch.object(app, 'launch', return_value=True) as launch:
            for round_number, delay in ((1, 1800), (2, 900), (3, 900)):
                self.assertEqual(app.bot_retry['round'], round_number)
                self.assertAlmostEqual(app.bot_retry['deadline'] - time.monotonic(), delay, delta=5)
                app.bot_retry['deadline'] = time.monotonic() - 1
                app.bot_retry_tick()
                self.assertEqual(launch.call_count, round_number)
                urls = launch.call_args.args[0]
                self.assertEqual(sorted(urls), ['https://www.youtube.com/watch?v=DDDDDDDDDDD',
                                                'https://www.youtube.com/watch?v=EEEEEEEEEEE'])
                self.assertTrue(launch.call_args.kwargs['auto'])
                self.blocked_done(app)
        self.assertIsNone(app.bot_retry)
        self.assertEqual(app.notice_kind, 'bot_end')
        self.assertIn('已自动重试 3 次仍被拒绝', ui_theme.full_text(app.notice_text))

    def test_manual_launches(self):
        app = self.make_app()
        self.store_blocked()
        self.blocked_done(app)
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/other'], Options(_sandbox.out('o'))))
            self.assertIsNotNone(app.bot_retry)                  # another site: still armed
            app.runner.cancel()
            app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [],
                                'pending_urls': []})
            self.assertIsNotNone(app.bot_retry)
            self.assertTrue(app.launch(['https://youtu.be/FFFFFFFFFFF'], Options(_sandbox.out('o'))))
            self.assertIsNone(app.bot_retry)                     # a YouTube link: cancelled
            app.runner.cancel()

    def test_retry_of_a_blocked_row_cancels_the_countdown_while_running(self):
        """WP4-bughunt-4: 重试此条 on a blocked row cancels the automatic retry on the enqueue path too."""
        app = self.make_app()
        self.store_blocked()
        self.blocked_done(app)
        self.assertIsNotNone(app.bot_retry)
        blocked = 'https://www.youtube.com/watch?v=DDDDDDDDDDD'
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/other', blocked], Options(_sandbox.out('o')), auto=True))
            app.handle('item_done', {'index': 2, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': True,
                                     'skipped': 'bot_check'})
            self.assertIsNotNone(app.bot_retry)
            app.retry_row(1)
        self.assertEqual(app.runner.enqueued[0][0], [blocked])
        self.assertIsNone(app.bot_retry)
        app.runner.cancel()

    def test_due_without_blocked_entries_and_cookies(self):
        app = self.make_app()
        self.store_blocked()
        self.blocked_done(app)
        engine.save_unfinished([])
        app.unfinished = []
        app.bot_retry['deadline'] = time.monotonic() - 1
        with mock.patch.object(app, 'launch') as launch:
            app.bot_retry_tick()
        launch.assert_not_called()
        self.assertIsNone(app.bot_retry)
        self.assertNotEqual(app.notice_kind, 'bot')
        self.store_blocked()
        self.blocked_done(app)
        self.assertIsNotNone(app.bot_retry)
        cookies = _sandbox.out('cookies.txt')
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        with mock.patch('ui_settings.filedialog.askopenfilename', return_value=str(cookies)):
            app.choose_cookies()
        self.assertIsNone(app.bot_retry)


class ESettingsAndLogTests(Base):
    def test_settings_saved_after_400_ms_never_in_smoke(self):
        app = self.make_app()
        path = _sandbox.state_dir() / 'settings.json'
        app.batch_safe.set(False)
        app.naming.set('clean')
        self.assertTrue(settings.load()['batch_safe'])
        pump(app, timeout=0.2)
        self.assertTrue(settings.load()['batch_safe'])
        pump(app, timeout=0.6)
        values = settings.load()
        self.assertFalse(values['batch_safe'])
        self.assertEqual(values['naming'], 'clean')
        self.assertEqual(app.naming_label.get(), '精简标题')
        self.close(app)
        path.unlink()
        smoke = self.make_app(smoke=True)
        smoke.batch_safe.set(False)
        smoke.save_settings()
        pump(smoke, timeout=0.6)
        self.assertFalse(path.exists())

    def test_log_classification_and_filter(self):
        table = [('ERROR: [youtube] x: Video unavailable', 'error'), ('WARNING: slow', 'warn'),
                 ('[youtube] x: Downloading webpage', 'dim'), ('Deleting original file a.webm', 'dim'),
                 ('此链接未完成（退出码 1），继续处理队列中的下一项。', 'error'), ('任务结束 · 1 条成功，1 条未完成', 'error'),
                 ('任务结束 · 1 条成功', 'app'), ('文件已保存：a.m4a', 'ok'), ('下载完成 · 2 条成功', 'ok'),
                 ('开始任务：1 条链接', 'app'), ('界面错误：x', 'error'), ('错误：boom', 'error')]
        for line, tag in table:
            self.assertEqual(ui_log.classify(line), tag, line)
        app = self.make_app()
        app.proxy.set('http://user:secret@127.0.0.1:7890')
        app.append_log('ERROR: via http://user:secret@127.0.0.1:7890 failed')
        app.append_log('[download] Sleeping 3.0 seconds ...')
        app.append_log('[youtube] abc: Downloading webpage')
        app.flush_log()
        text = app.logs.get('1.0', 'end-1c')
        self.assertIn('ERROR: via [代理] failed', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('Sleeping 3.0', text)
        found = app.logs._textbox.search('ERROR: via', '1.0')
        self.assertIn('error', app.logs._textbox.tag_names(found))
        app.toggle_errors()
        self.assertEqual(str(app.logs._textbox.tag_cget('dim', 'elide')), '1')
        self.assertEqual(app.errors_button.cget('text'), '显示全部')
        app.toggle_errors()
        self.assertEqual(str(app.logs._textbox.tag_cget('dim', 'elide')), '0')
        for name, token in ui_log.TAGS.items():
            self.assertEqual(str(app.logs._textbox.tag_cget(name, 'foreground')).upper(), token[0])

    def test_restore_button_is_locked_while_busy(self):
        """WP4-conformance-1 / bughunt-2: 恢复内置引擎 follows the busy lock like the other controls."""
        app = self.make_app()
        with mock.patch.object(ui_settings, 'updated_engine_active', return_value=True):
            app.refresh_engine_labels()
        self.assertEqual(app.restore_button.cget('state'), 'normal')
        for operation in ('download', 'collect', 'update'):
            app.operation = operation
            app.set_busy(True)
            self.assertEqual(app.restore_button.cget('state'), 'disabled', operation)
            app.set_busy(False)
            self.assertEqual(app.restore_button.cget('state'), 'normal', operation)
        with mock.patch.object(ui_settings, 'updated_engine_active', return_value=False):
            app.refresh_engine_labels()
        self.assertEqual(app.restore_button.cget('state'), 'disabled')

    def test_another_activity_holds_the_download_page(self):
        """WP4-visual-1: an engine update or a collection says so on the download page, and the settings
        page can cancel its own update; the download header comes back afterwards."""
        app = self.make_app()
        app.populate_queue(['https://example.org/a'])
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 1, 'total': 1, 'url': 'https://example.org/a'})
        app.handle('item_done', {'index': 1, 'cancelled': False, 'success': True, 'files': 1, 'bot_check': False})
        app.handle('done', {'success': 1, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                            'pending_urls': []})
        before = (ui_theme.full_text(app.current_title), ui_theme.full_text(app.metrics))
        self.assertEqual(app.cancel_update_button.winfo_manager(), '')
        for operation, title, stop in (('update', '正在更新下载引擎…', '停止更新'), ('collect', '正在网页采集…', '停止采集')):
            app.operation = operation
            app.set_busy(True)
            self.assertEqual(ui_theme.full_text(app.current_title), title)
            self.assertIn('结束后即可添加链接', ui_theme.full_text(app.metrics))
            self.assertEqual(app.stop_button.cget('text'), stop)
            self.assertNotIn('Ctrl+V', ui_theme.full_text(app.link_hint))
            self.assertEqual(app.urls.cget('state'), 'disabled')
            self.assertEqual(app.urls.cget('text_color'), ui_theme.FAINT)
            self.assertEqual(app.cancel_update_button.winfo_manager(), 'grid' if operation == 'update' else '')
            app.handle(f'{operation}_done', {'cancelled': True})
            self.assertFalse(app.busy)
            self.assertEqual((ui_theme.full_text(app.current_title), ui_theme.full_text(app.metrics)), before)
            self.assertEqual(app.stop_button.cget('text'), '停止')
            self.assertEqual(ui_theme.full_text(app.link_hint), ui_download.HINT_DEFAULT)
            self.assertEqual(app.urls.cget('text_color'), ui_theme.INK)
        self.assertEqual(app.cancel_update_button.winfo_manager(), '')

    def test_cancel_on_the_settings_page_stops_the_update(self):
        app = self.make_app()

        class FakeUpdater:
            def __init__(self, emit, cancelled, probe_args=None):
                self.cancelled = cancelled

            def run(self):
                self.cancelled.wait(10)
        with mock.patch.object(updater, 'EngineUpdater', FakeUpdater):
            app.update_engine()
            worker = app.worker
            self.assertEqual(app.cancel_update_button.winfo_manager(), 'grid')
            app.cancel_update_button.invoke()
            self.assertFalse(app.busy)
            self.assertEqual(app.update_status.cget('text'), '已取消更新。')
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(app.cancel_update_button.winfo_manager(), '')

    def test_error_filter_keeps_a_reader_at_the_end(self):
        """WP4-visual-7: 只看错误 / 显示全部 keep the view at the end when it was there."""
        app = self.make_app(visible=True)
        app.show_page('logs')
        for number in range(300):
            app.append_log(f'[youtube] vid{number:08d}: Downloading webpage')
            if number % 50 == 0:
                app.append_log(f'ERROR: [youtube] vid{number:08d}: Video unavailable')
        while app.log_buffer:
            app.flush_log()
        app.logs.see('end')
        pump(app, timeout=0.2)
        self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        for _ in range(2):
            app.toggle_errors()
            pump(app, timeout=0.2)
            self.assertGreaterEqual(app.logs.yview()[1], 0.999)

    def test_stop_during_update_drops_stale_events(self):
        app = self.make_app()
        started = threading.Event()

        class FakeUpdater:
            def __init__(self, emit, cancelled, probe_args=None):
                self.emit, self.cancelled = emit, cancelled

            def run(self):
                self.emit('update_status', '正在下载 yt-dlp 2099.01.01…')
                self.emit('update_progress', {'fraction': .5, 'downloaded': 5, 'total': 10})
                started.set()
                self.cancelled.wait(10)
                self.emit('update_progress', {'fraction': .9, 'downloaded': 9, 'total': 10})
                self.emit('update_done', {'cancelled': True})
        with mock.patch.object(updater, 'EngineUpdater', FakeUpdater):
            app.update_engine()
            worker = app.worker
            self.assertTrue(started.wait(5))
            app.stop()
            self.assertFalse(app.busy)
            self.assertEqual(app.update_status.cget('text'), '已取消更新。')
            worker.join(5)
            app.poll()
        self.assertFalse(app.busy)
        self.assertEqual(app.update_status.cget('text'), '已取消更新。')
        self.assertTrue(app.events.empty())


class FBackgroundTests(Base):
    def test_daily_engine_check(self):
        calls = []

        def latest(timeout=25):
            calls.append(timeout)
            return {'tag': '2099.01.01', 'url': '', 'size': 0, 'sha256': ''}
        app = self.make_app(check_engine_updates=True, last_engine_check=0)
        with mock.patch.object(updater, 'check_latest', latest):
            self.assertTrue(pump(app, lambda: '引擎有新版' in app.app_badge.cget('text'), 8))
            pump(app, timeout=0.3)
        self.assertEqual(calls, [15])
        self.assertGreater(app.prefs['last_engine_check'], 0)
        self.assertEqual(app.prefs['latest_engine'], '2099.01.01')
        self.assertIn('2099.01.01', app.update_status.cget('text'))
        self.assertTrue(ui_settings.version_newer('2026.11.02', '2026.10.21'))
        self.assertFalse(ui_settings.version_newer('2026.10.21', '2026.10.21'))

    def test_clipboard_chip(self):
        app = self.make_app(clipboard_hint=True)
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, **_: SHARE):
            app.check_clipboard()
            self.assertEqual(app.clip_bar.winfo_manager(), 'grid')
            self.assertIn('剪贴板中有 2 条链接', ui_theme.full_text(app.clip_label))
            self.assertEqual(app.urls.get('1.0', 'end-1c'), '')        # never filled by itself
            app.accept_clipboard()
            self.assertEqual(len(app.urls.get('1.0', 'end-1c').splitlines()), 2)
            self.assertEqual(app.prefs['last_clip'], hashlib.sha1(SHARE.encode('utf-8')).hexdigest())
            self.assertEqual(app.clip_bar.winfo_manager(), '')
            app.urls.delete('1.0', 'end')
            app.check_clipboard()                                       # the same text is not offered again
            self.assertEqual(app.clip_bar.winfo_manager(), '')
        self.assertFalse(app.busy)


class ZSnapshotTest(unittest.TestCase):
    """Last: the layout assertions of snapshot_ui (no app of this file is alive any more)."""

    def test_snapshot_assertions(self):
        import snapshot_ui
        failures = snapshot_ui.run(pages=['download', 'settings', 'logs'], sizes=('1000x740', '1120x810'), shots=False)
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
