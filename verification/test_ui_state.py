"""UI-side state of StudioApp (1.6.0 WP4 section H): R1-R3, R5-R7, R13, R16 and the download page logic.

Every app here is created and destroyed on its own (one live StudioApp per process).  Non-smoke
apps start from _sandbox.write_settings() (update check and clipboard hint off).  The module
replaces updater.check_latest by a guard that fails the test when it is called, and
StudioApp.clipboard_get by one that raises TclError, except in the two tests made for them.
"""
import _sandbox
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import i18n
import library
import messages
import paths
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


from _sandbox import no_clipboard, no_network, pump, zh_t


SHARE = '【分享】好听 https://www.youtube.com/watch?v=AAAAAAAAAAA&si=xyz 还有这首 https://b23.tv/abcdef 复制此链接'
MIX = 'https://www.youtube.com/watch?v=BBBBBBBBBBB&list=RDBBBBBBBBBBB&start_radio=1'


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
        self.assertIn(zh_t('log.history_write_failed_short'), app.log_buffer + [app.logs.get('1.0', 'end')][0].splitlines())

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
        self.assertFalse(app.task_header.winfo_manager(), 'no rows: no task header')
        self.assertTrue(app.empty.winfo_manager(), 'the empty state instead')
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
        # the footer while the queue waits or runs (S3 review fix: either text by key, not one as the other's prefix)
        footer = ui_theme.full_text(app.row_footer)
        self.assertTrue(any(zh_t(key, hidden=400 - app.row_cap()) in footer
                            for key in ('download.footer.hidden', 'download.footer.hidden_running')), footer)

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
        self.assertEqual(chips, [zh_t('download.chip.skipped'), zh_t('download.chip.blocked'),
                                 zh_t('download.chip.stopped'), zh_t('download.chip.failed')])
        self.assertEqual([row['state'] for row in app.rows], ['skipped', 'blocked', 'stopped', 'failed'])
        # fix round 1: the skipped row does not repeat its title; the way out of a stale engine comes first
        self.assertEqual(ui_theme.full_text(app.rows[0]['detail']),
                         f"01 · {zh_t('download.detail.skipped')}")
        self.assertEqual(ui_theme.full_text(app.rows[1]['detail']), f"02 · {zh_t('download.detail.blocked')}")
        self.assertEqual(ui_theme.full_text(app.rows[3]['detail']),
                         f"04 · {zh_t('download.detail.stale_maybe', reason='网络超时')}")
        app.handle('item_done', {'index': 4, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.STALE_REASON, 'stale_engine': True, 'error': 'ERROR: x'})
        self.assertEqual(ui_theme.full_text(app.rows[3]['detail']),
                         f"04 · {zh_t('download.detail.stale_update', reason=engine.STALE_REASON)}")
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
        self.assertIn(zh_t('core.links.none'), app.link_note.cget('text'))
        app.urls.insert('1.0', 'https://example.org/late')
        app.enqueue_links()
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.already_queued'))
        runner.cancel()
        app.enqueue_links()
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.queue_ended'))
        app.set_busy(False)

    def test_enqueue_while_downloading(self):
        app = self.make_app()
        app.urls.insert('1.0', 'https://example.org/first')
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
            self.assertTrue(app.busy)
            self.assertEqual(app.start_button.cget('text'), zh_t('download.start.queue'))
            self.assertEqual(app.urls.cget('state'), 'normal')
            app.urls.insert('1.0', 'https://example.org/second')
            app.start()
        self.assertEqual([row['url'] for row in app.row_model], ['https://example.org/first', 'https://example.org/second'])
        self.assertEqual(app.runner.enqueued[0][0], ['https://example.org/second'])
        self.assertEqual(app.urls.get('1.0', 'end-1c'), '')
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.queued', count=1))

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
        self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.busy'))
        app.set_busy(False)
        self.assertEqual(app.urls.cget('state'), 'normal')

    def test_paste(self):
        app = self.make_app()
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, **_: 'just some words'):
            self.assertIsNone(app.on_box_paste())
        with mock.patch.object(StudioApp, 'clipboard_get', lambda self, **_: SHARE):
            self.assertEqual(app.paste(), 2)
            self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.added', added=2))
            self.assertEqual(app.urls.get('1.0', 'end-1c').splitlines(),
                             ['https://www.youtube.com/watch?v=AAAAAAAAAAA&si=xyz', 'https://b23.tv/abcdef'])
            self.assertEqual(app.paste(), 0)
            self.assertEqual(app.link_note.cget('text'), zh_t('download.hint.added_skipped', added=0, skipped=2))
            self.assertEqual(app.on_box_paste(), 'break')
        self.assertEqual(app.link_count.cget('text'), '2')

    def test_mix_note(self):
        app = self.make_app()
        app.urls.insert('1.0', MIX)
        app.links_changed()
        self.assertEqual(app.link_note.winfo_manager(), 'grid')
        self.assertEqual(ui_theme.full_text(app.link_note), zh_t('download.note.lists_dropped', count=1))
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
            app.format_menu._dropdown_callback(i18n.t('format.' + preset.id))      # the menu stores the id
            self.assertEqual(app.mode.get(), preset.id)
            video = preset.kind == 'video'
            self.assertEqual(app.quality_menu.winfo_manager(), 'grid' if video else '', preset.id)
            self.assertEqual(app.subtitle_check.winfo_manager(), 'grid' if video else '', preset.id)
            self.assertEqual(app.quality_menu.cget('state'), 'normal' if video else 'disabled')
        self.assertFalse(hasattr(ui_download, 'FORMATS'))        # (2.0.0 S4 A1) a format has one name, format.<id>
        self.assertFalse(hasattr(app, 'format'))

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

    def test_an_empty_bar_draws_nothing(self):
        """2.0.3: at 0 the fill is hidden, not a round dot at the left end (``immediate`` lands at once)."""
        app = self.make_app(visible=True)            # a withdrawn window gives the canvas no width
        bar = SmoothProgressBar(app, width=300, height=6)
        bar.place(x=0, y=0)
        self.assertTrue(pump(app, lambda: bar.canvas.winfo_width() > 100, 5))
        for value, state in ((0, 'hidden'), (.5, 'normal'), (0, 'hidden')):
            bar.set(value, immediate=True)
            self.assertEqual(bar.canvas.itemcget(bar._fill, 'state'), state, value)
        bar.destroy()

    def test_retry_keeps_the_form_r13(self):
        app = self.make_app()
        _sandbox.save_unfinished(['https://example.org/two'], Options(_sandbox.out('original'), MODES[2], '720p', True))
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
        self.assertEqual(app.side_status.cget('text'), zh_t('shell.status.bot_wait'))
        self.assertEqual(app.side_status.cget('text_color'), ui_theme.SIDE_WARN)     # (2.0.0 S4) amber, as the chip
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
        self.assertIn(zh_t('download.notice.bot_gave_up'), app.notice_bar.full_text)

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
        self.assertEqual(app.naming_menu._text_label.cget('text'), i18n.label('naming', 'clean'))
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
                 # (S3 review fix) the app's lines by key, so the table pins the tags and not the wording
                 (zh_t('core.engine.item_unfinished', code=1), 'error'),
                 (zh_t('download.title.finished_n', success=1), 'error'),
                 (zh_t('log.file_saved', path='a.m4a'), 'ok'), (zh_t('download.title.done', success=2), 'ok'),
                 (zh_t('log.task_started', count=1, format='MP3', folder='D:/x'), 'app'),
                 (zh_t('log.ui_error', kind='x', value='y'), 'error'), (zh_t('core.engine.error', error='boom'), 'error')]
        for line, tag in table:
            self.assertEqual(ui_log.classify(line), tag, line)
        app = self.make_app()
        app.proxy.set('http://user:secret@127.0.0.1:7890')
        app.append_log('ERROR: via http://user:secret@127.0.0.1:7890 failed')
        app.append_log('[download] Sleeping 3.0 seconds ...')
        app.append_log('[youtube] abc: Downloading webpage')
        app.flush_log()
        text = app.logs.get('1.0', 'end-1c')
        self.assertIn(f"ERROR: via {zh_t('core.proxy_mask')} failed", text)
        self.assertNotIn('secret', text)
        self.assertNotIn('Sleeping 3.0', text)
        found = app.logs._textbox.search('ERROR: via', '1.0')
        self.assertIn('error', app.logs._textbox.tag_names(found))
        app.set_errors_only(True)
        self.assertEqual(str(app.logs._textbox.tag_cget('dim', 'elide')), '1')
        self.assertEqual(app.log_filter.get(), 'errors')
        app.set_errors_only(False)
        self.assertEqual(str(app.logs._textbox.tag_cget('dim', 'elide')), '0')
        self.assertEqual(app.log_filter.get(), 'all')
        app.log_filter._buttons_dict[zh_t('log.filter.errors')].invoke()     # the click path hands over the id
        self.assertIs(app.errors_only, True)
        self.assertEqual(str(app.logs._textbox.tag_cget('dim', 'elide')), '1')
        app.set_errors_only(False)
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
        self.assertEqual(app.restore_button.winfo_manager(), '', 'the built-in engine: no restore button')

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
        self.assertEqual(app.stop_update_button.winfo_manager(), '')
        self.assertEqual(app.check_update_button.winfo_manager(), 'grid')
        for operation in ('update', 'collect'):
            app.operation = operation
            app.set_busy(True)
            # (deferred-to-C 2) the title is the one FOREIGN_KEYS names for the operation: the key, not a copy of it
            self.assertEqual(ui_theme.full_text(app.current_title), zh_t(f'download.foreign.{operation}.title'))
            self.assertEqual(ui_theme.full_text(app.current_title), zh_t(ui_download.FOREIGN_KEYS[operation]))
            self.assertEqual(ui_theme.full_text(app.metrics), '')
            self.assertEqual(app.stop_button.cget('text'), zh_t('common.stop'))
            self.assertEqual(app.link_note.winfo_manager(), '', 'nothing under the box')
            self.assertEqual(app.urls.cget('state'), 'disabled')
            self.assertEqual(app.urls.cget('text_color'), ui_theme.FAINT)
            self.assertTrue(app.urls.dimmed)
            self.assertEqual(app.link_box.cget('fg_color'), ui_theme.SURFACE)
            self.assertEqual(app.stop_update_button.winfo_manager(), 'grid' if operation == 'update' else '')
            self.assertEqual(app.check_update_button.winfo_manager(), '' if operation == 'update' else 'grid')
            app.handle(f'{operation}_done', {'cancelled': True})
            self.assertFalse(app.busy)
            self.assertEqual((ui_theme.full_text(app.current_title), ui_theme.full_text(app.metrics)), before)
            self.assertEqual(app.stop_button.cget('text'), zh_t('common.stop'))
            self.assertEqual(app.link_note.winfo_manager(), '')
            self.assertEqual(app.urls.cget('text_color'), ui_theme.INK)
            self.assertFalse(app.urls.dimmed)
            self.assertEqual(app.link_box.cget('fg_color'), ui_theme.SURFACE_ALT)
        self.assertEqual(app.stop_update_button.winfo_manager(), '')
        self.assertEqual(app.check_update_button.winfo_manager(), 'grid')

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
            self.assertEqual(app.stop_update_button.winfo_manager(), 'grid')
            self.assertEqual(app.check_update_button.winfo_manager(), '', 'the two swap in one place')
            app.stop_update_button.invoke()
            self.assertFalse(app.busy)
            self.assertEqual(app.update_status.cget('text'), zh_t('settings.update.cancelled'))
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(app.stop_update_button.winfo_manager(), '')
        self.assertEqual(app.check_update_button.winfo_manager(), 'grid')

    def test_error_filter_keeps_a_reader_at_the_end(self):
        """WP4-visual-7: Errors / All keep the view at the end when it was there."""
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
        for on in (True, False, True, True):     # the last call repeats: idempotent, still at the end
            app.set_errors_only(on)
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
            self.assertEqual(app.update_status.cget('text'), zh_t('settings.update.cancelled'))
            worker.join(5)
            app.poll()
        self.assertFalse(app.busy)
        self.assertEqual(app.update_status.cget('text'), zh_t('settings.update.cancelled'))
        self.assertTrue(app.events.empty())


class FBackgroundTests(Base):
    def test_daily_engine_check(self):
        calls = []

        def latest(timeout=25):
            calls.append(timeout)
            return {'tag': '2099.01.01', 'url': '', 'size': 0, 'sha256': ''}
        app = self.make_app(check_engine_updates=True, last_engine_check=0)
        with mock.patch.object(updater, 'check_latest', latest):
            # (2.0.0 S4 A2) the sidebar's 'Update available' link appears (it took the place of the app badge)
            self.assertTrue(pump(app, lambda: app.engine_notice.winfo_manager() == 'grid', 8))
            self.assertEqual(app.engine_notice.cget('text'), zh_t('shell.engine_update'))
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
            self.assertEqual(app.clip_bar.full_text,
                             zh_t('download.clip.found', count=2, title=ui_download.link_title(SHARE.split()[1])))
            self.assertEqual(len(app.clip_bar.buttons), 2)
            self.assertEqual(app.urls.get('1.0', 'end-1c'), '')        # never filled by itself
            app.accept_clipboard()
            self.assertEqual(len(app.urls.get('1.0', 'end-1c').splitlines()), 2)
            self.assertEqual(app.prefs['last_clip'], hashlib.sha1(SHARE.encode('utf-8')).hexdigest())
            self.assertEqual(app.clip_bar.winfo_manager(), '')
            app.urls.delete('1.0', 'end')
            app.check_clipboard()                                       # the same text is not offered again
            self.assertEqual(app.clip_bar.winfo_manager(), '')
        self.assertFalse(app.busy)


class GForeignFileTests(Base):
    """(2.0.0 S2 Q2, PLAN 3.10, S2-AMEND.md 2.5) The window with files that are not version 2: each is
    renamed in its folder (its bytes), the window starts from blank, and the log page has one line."""

    def shown_lines(self, app):
        app.flush_log()
        return app.logs.get('1.0', 'end').splitlines() + list(app.log_buffer)

    def sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_a_library_of_another_version_beside_the_program_is_kept_aside_at_the_start(self):
        app_dir = _sandbox.out('q2-library-app')
        shutil.rmtree(app_dir, ignore_errors=True)
        patcher = mock.patch.object(paths, 'APP_DIR', app_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        beside = paths.data_dir() / 'library.sqlite3'
        beside.parent.mkdir(parents=True)
        db = sqlite3.connect(beside)
        db.executescript(library.SCHEMA)
        db.execute("INSERT INTO jobs VALUES ('old', 'download', '旧任务', 'done', 1.0, '{}')")
        db.commit()
        db.close()
        before = self.sha(beside)
        app = self.make_app()
        warning = app.library_warning
        self.assertEqual((warning.key, warning.params['problem'].key), ('core.library.kept_aside', 'core.library.not_v2'))
        kept = sorted(paths.data_dir().glob('library.bad-*.sqlite3'))
        self.assertEqual([(path.name, self.sha(path)) for path in kept], [(warning.params['target'], before)])
        self.assertEqual(app.library.path, beside.resolve(), 'the new library is beside the program as well')
        self.assertEqual(app.library.history(), [])
        text = messages.show(warning)
        self.assertEqual([line for line in self.shown_lines(app) if line == text], [text], 'one line on the log page')
        self.assertIn(text, paths.log_file().read_text(encoding='utf-8'), 'and in download.log')
        self.assertTrue(app.library_notice.shown)
        self.assertEqual(app.library_notice.full_text,
                         zh_t('core.library.kept_aside', problem=zh_t('core.library.not_v2'), target=warning.params['target']))
        self.assertEqual([button.cget('text') for button in app.library_notice.buttons], [zh_t('common.got_it')])

    def test_a_list_of_another_version_is_kept_aside_at_the_start(self):
        """Item 12: the start renames it (not only the first download)."""
        path = paths.failed_file()
        self.addCleanup(lambda: [kept.unlink() for kept in path.parent.glob('failed-downloads.bad-*.json')])
        path.write_text(json.dumps({'version': 1, 'urls': ['https://example.org/a'], 'options': {'output': 'D:/x'}}),
                        encoding='utf-8')
        before = self.sha(path)
        app = self.make_app()
        self.assertFalse(path.exists())
        kept = sorted(path.parent.glob('failed-downloads.bad-*.json'))
        self.assertEqual([self.sha(item) for item in kept], [before])
        text = zh_t('core.unfinished.kept_aside', problem=zh_t('core.unfinished.problem.unknown_format'), target=kept[0].name)
        self.assertEqual([line for line in self.shown_lines(app) if line == text], [text])
        self.assertIn(text, paths.log_file().read_text(encoding='utf-8'))
        self.assertEqual((app.unfinished, app.rows), ([], []))

    def test_an_archive_record_of_another_version_is_kept_aside_by_archive_pages(self):
        """Item 14b: the library page's "archive the collected pages" passes its log as well."""
        root = _sandbox.out('q2-archive')
        shutil.rmtree(root, ignore_errors=True)
        url = 'https://example.org/page'
        folder = library.Archive(root).prepare(url, dict(title='页', source=url))
        record = folder / '资料信息.json'
        record.write_text(json.dumps({'url': url, 'source': url, 'title': '网页', 'created': 1.0, 'files': [],
                                      'transcripts': [], 'msgs': {'title': {'k': 'value.link_kind.web'}}},
                                     ensure_ascii=False), encoding='utf-8')
        before = record.read_bytes()
        app = self.make_app()
        app.archive_root.set(str(root))
        app.collected_pages = [dict(url=url, title='页', text='正文', markdown='# 正文', links=[], next=None)]
        with mock.patch('library_ui.messagebox.showerror', side_effect=AssertionError('no error dialog')):
            app.archive_pages()
        self.assertEqual((folder / '资料信息.bad.json').read_bytes(), before)
        self.assertEqual(json.loads(record.read_text(encoding='utf-8'))['version'], 2)
        text = zh_t('core.archive.kept_aside', target='资料信息.bad.json', folder=folder)
        self.assertEqual([line for line in self.shown_lines(app) if line == text], [text])

    def test_settings_of_1_8_1_keep_what_is_valid(self):
        """Item 18: the stored Chinese format name and 最佳 fall back to the defaults, _extra (the language
        of 1.8.1) is not read; the output folder and the switches stay; the first save writes v2 values."""
        output = str(_sandbox.out('q2-output'))
        app = self.make_app(mode='音频 · FLAC', resolution='最佳', _extra={'language': 'en'}, output=output,
                            embed_cover=False, subtitles=True, batch_safe=False)
        self.assertEqual((app.mode.get(), app.resolution.get()), (MODES[0], 'best'))
        self.assertEqual((app.output.get(), app.embed_cover.get(), app.subtitles.get(), app.batch_safe.get()),
                         (output, False, True, False))
        app.save_settings()
        written = json.loads(paths.settings_file().read_text(encoding='utf-8'))
        self.assertLessEqual(set(written), set(settings._CHECKS))
        self.assertEqual((written['mode'], written['resolution'], written['language'], written['output']),
                         (MODES[0], 'best', 'system', output))
        self.assertEqual((written['embed_cover'], written['subtitles'], written['batch_safe']), (False, True, False))


class ZSnapshotTest(unittest.TestCase):
    """Last: the layout assertions of snapshot_ui (no app of this file is alive any more)."""

    def test_snapshot_assertions(self):
        import snapshot_ui
        failures = snapshot_ui.run(pages=['download', 'settings', 'logs'], sizes=('1000x740', '1120x810'), shots=False)
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
