"""1.7.0 fix-ui: the issues deferred from 1.6.0 (docs/1.7.0/handoff/fix-ui.md).

U1  A library.sqlite3 locked by ANOTHER PROCESS (a child holding BEGIN EXCLUSIVE, or BEGIN
    IMMEDIATE with an uncommitted write = a RESERVED lock, what a DB browser with an unsaved edit
    holds) never freezes the window: an after() probe on the Tk thread sees no gap over 300 ms
    while the owner refreshes, starts a download, adds links with 加入队列, starts a collection
    and the subscription timer runs.  The window says so calmly (notice, strip, one log line), the
    download starts at once, and after the lock is released every record is in the history.
U2  The history pages (加载更多) and searches title and links in the database.
U3  A collection record loaded after a run keeps the 2,000-link display cap of the run.
U4  A settings.json that could not be read at start is said once in the notice line.
U5  The header 清除未完成 writes fits at 1000x740 without an ellipsis.
U6  The collect page's start-URL box is not redrawn by a busy switch of a download started on
    another page (the redraw laid out the whole window right after 开始下载).
No network, no real engine (the download runner is scripted); the lock holder is a plain
``python -c`` child with sqlite3.
"""
import _sandbox
import ctypes
import json
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from unittest import mock

import customtkinter as ctk
import collection_ui
import engine
import library
import library_ui
import settings
import ui_download
import ui_theme
import updater
from collector import MAX_LINKS, CollectOptions
from engine import MODES
from studio import SETTINGS_HELD_NOTICE, StudioApp

LATENCY = 0.3        # s: the largest gap between two after() ticks the owner may see
LOCKER = r'''
import sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=10)
db.execute('BEGIN EXCLUSIVE' if sys.argv[2] == 'exclusive' else 'BEGIN IMMEDIATE')
if sys.argv[2] == 'reserved':
    db.execute('UPDATE jobs SET updated=updated WHERE 0')
print('locked', flush=True)
sys.stdin.readline()
db.execute('ROLLBACK')
db.close()
'''


class Lock:
    """Another process holding library.sqlite3 until release()."""

    def __init__(self, test, path, mode):
        self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-c', LOCKER, str(path), mode],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        test.addCleanup(self.release)
        line = self.process.stdout.readline().strip()
        test.assertEqual(line, 'locked')

    def release(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('\n')
                self.process.stdin.close()
                self.process.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(10)
        if not self.process.stdout.closed:
            self.process.stdout.close()


class Probe:
    """An after(10) tick on the Tk thread; worst() is the largest gap between two ticks."""

    def __init__(self, app):
        self.app, self.ticks, self.on = app, [], True
        app.after(10, self.tick)

    def tick(self):
        self.ticks.append(time.perf_counter())
        if self.on:
            try:
                self.app.after(10, self.tick)
            except tk.TclError:
                pass

    def worst(self):
        return max((b - a for a, b in zip(self.ticks, self.ticks[1:])), default=0.0)


class ScriptRunner:
    """Stands in for engine.Runner: download() emits a real job's events for every link on its
    own thread (through the window's emit closure, so the history writes go the real way), takes
    links from enqueue(), and ends with 'done' once released."""
    instances = []

    def __init__(self, emit, library=None):
        self.emit, self.library = emit, library
        self.origins, self.inbox, self.base_options = {}, [], None
        self.cancelled, self.release = threading.Event(), threading.Event()
        self.started = threading.Event()
        self.closed, self.opened = False, True
        self.lock = threading.Lock()
        ScriptRunner.instances.append(self)

    def _item(self, index, total, url):
        self.emit('item', {'index': index, 'total': total, 'url': url, 'key': url, 'format': 'audio_best',
                           'origin': None, 'multi': False})
        self.emit('title', f'第 {index} 首')
        path = str(_sandbox.out('v170-downloads', f'{index}.m4a'))
        self.emit('file', path)
        self.emit('item_done', {'index': index, 'success': True, 'cancelled': False, 'files': 1, 'bot_check': False,
                                'path': path, 'format': 'audio_best'})
        return path

    def download(self, urls, options, overrides=None):
        self.base_options = options
        self.started.set()
        files = [self._item(n, len(urls), url) for n, url in enumerate(urls, 1)]
        self.release.wait(30)
        with self.lock:
            added, self.inbox = self.inbox, []
            if not added:
                self.closed = True
        total = len(urls) + len(added)
        if added:
            self.emit('queued', {'urls': added, 'overrides': {}})
            files += [self._item(n, total, url) for n, url in enumerate(added, len(urls) + 1)]
            self.closed = True
        self.emit('done', {'success': total, 'failed': 0, 'files': files, 'cancelled': self.cancelled.is_set(),
                           'failed_urls': [], 'pending_urls': [], 'failed_path': None, 'archive_errors': [],
                           'bot_check': False})

    def enqueue(self, urls, options=None, overrides=None):
        with self.lock:
            if self.closed:
                return []
            self.inbox += list(urls)
            return list(urls)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


class FakeCollectionRunner:
    """Stands in for collector.CollectionRunner: one stored page, then done."""

    def __init__(self, emit):
        self.emit = emit

    def run(self, options):
        store = library.Library(options.history_path)
        page = {'url': options.urls[0], 'title': '列表', 'text': '', 'markdown': '',
                'links': [{'url': 'https://media.example.org/a.mp3', 'title': 'a', 'kind': '媒体地址',
                           'source': options.urls[0]}]}
        store.collection_event(options.job_id, 'collect_page', dict(page, _checkpoint=None))
        store.change_job(options.job_id, status='completed')
        self.emit('collect_page', page)
        self.emit('collect_done', {'cancelled': False})

    def cancel(self):
        pass


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.005)
    return condition is None


def click(app, action):
    """Run ``action`` as a Tk callback (as a click runs it) and let the window go on; its result."""
    box = {}

    def run():
        box['value'] = action()
    app.after(0, run)
    pump(app, lambda: 'value' in box, 10)
    return box.get('value')


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', side_effect=AssertionError('no network')),
                        mock.patch.object(StudioApp, 'clipboard_get', side_effect=tk.TclError('no clipboard')),
                        mock.patch.object(engine, 'PACE_SECONDS', (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        engine.save_unfinished([])
        ScriptRunner.instances = []
        self.addCleanup(lambda: [runner.release.set() for runner in ScriptRunner.instances])

    def make_app(self, smoke=False, visible=False, size='1000x740'):
        ctk.set_appearance_mode('light')
        if smoke:
            app = StudioApp(_sandbox.app_args('https://example.invalid/v170',
                                              str(_sandbox.out('v170-' + self._testMethodName))))
        else:
            _sandbox.write_settings(output=str(_sandbox.out('v170-out')), batch_safe=False)
            app = StudioApp(_sandbox.app_args())
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
            app.history_writer.idle(5)
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

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
    def rows(path):
        db = sqlite3.connect(path, timeout=10)
        try:
            return {job: (kind, status, json.loads(data)) for job, kind, status, data
                    in db.execute('SELECT id, kind, status, data FROM jobs')}
        finally:
            db.close()


# ----------------------------------------------------------------------------------------- U1
class U1_LockTests(Base):
    def wait_settled(self, app, timeout=10):
        return pump(app, lambda: not app.library_waiting and not app.history_writer.waiting(), timeout)

    def test_reading_under_an_exclusive_lock_never_stops_the_window(self):
        app = self.make_app()
        job = app.library.create_job('download', '一首歌', {'urls': ['https://example.org/a'], 'output': 'x',
                                                         'mode': MODES[0]})
        app.refresh_library()
        app.history_tree.selection_set(job)
        options = CollectOptions(['https://example.org/feed']).validate()
        # 仅手动: the timer of a later window of this process never checks it.
        key = app.library.subscription('锁住时的订阅', asdict(options), 0, True, True)
        app.refresh_library()
        app.subscription_tree.selection_set(key)
        lines = self.logged(app)
        lock = Lock(self, app.library.path, 'exclusive')
        probe = Probe(app)
        click(app, app.refresh_library)
        self.assertEqual(ui_theme.full_text(app.library_warning_label), library_ui.LIBRARY_BUSY)
        self.assertEqual(app.library_warning_bar.winfo_manager(), 'grid')
        self.assertEqual((app.notice_kind, ui_theme.full_text(app.notice_text)), ('library', library_ui.LIBRARY_BUSY))
        click(app, app.view_history)            # a click that needs the record: a hint, no dialog
        self.assertEqual(ui_theme.full_text(app.history_note), library_ui.LIBRARY_BUSY_CLICK)
        click(app, app.subscription_tick)       # quietly skipped; the next tick looks again
        click(app, app.check_subscription)      # a hint on the tab, no dialog
        self.assertEqual(ui_theme.full_text(app.subscription_note), library_ui.LIBRARY_BUSY_CLICK)
        app.archive_search.set('x')             # the archive list is read on every keystroke
        pump(app, timeout=2.5)                  # library_watch tries again while the lock is held
        self.assertTrue(app.library_waiting)
        self.assertLess(probe.worst(), LATENCY, 'the window stalled while the library was locked')
        self.assertEqual([line for line in lines if '资料库' in line],
                         [library_ui.LIBRARY_BUSY_LOG.format(error='database is locked')])
        lock.release()
        self.assertTrue(self.wait_settled(app), 'the notice goes once the lock is gone')
        self.assertEqual(app.library_warning_bar.winfo_manager(), '')
        self.assertIsNone(app.notice_kind)
        self.assertIn(job, app.history_tree.get_children())
        self.assertLess(probe.worst(), LATENCY)

    def run_download_under(self, mode):
        app = self.make_app()
        lines = self.logged(app)
        first, added = 'https://example.org/v170/first', 'https://example.org/v170/added'
        with mock.patch.object(ui_download, 'Runner', ScriptRunner):
            lock = Lock(self, app.library.path, mode)
            probe = Probe(app)
            app.urls.insert('1.0', first)
            click(app, app.start)
            self.assertTrue(app.busy, 'the download started at once')
            runner = ScriptRunner.instances[-1]
            self.assertTrue(runner.started.wait(5))
            self.assertIsInstance(app.download_job, library_ui.PendingJob)
            self.assertIsNone(app.download_job.id, 'no record while the library is locked')
            pump(app, lambda: app.row_model and app.row_model[0]['state'] == 'done', 5)
            self.assertEqual(app.row_model[0]['state'], 'done', 'its events reach the window at once')
            app.urls.insert('1.0', added)
            self.assertEqual(click(app, app.enqueue_links), 1)
            pump(app, timeout=0.5)
            runner.release.set()
            self.assertTrue(pump(app, lambda: not app.busy, 10))
            pump(app, timeout=1.0)
            self.assertLess(probe.worst(), LATENCY, f'the window stalled under a {mode} lock')
            self.assertTrue(app.history_writer.waiting(), 'the records wait for the lock')
            lock.release()
            self.assertTrue(self.wait_settled(app))
        self.assertLess(probe.worst(), LATENCY)
        job = app.download_job.id
        self.assertTrue(job)
        rows = self.rows(app.library.path)
        kind, status, data = rows[job]
        self.assertEqual((kind, status), ('download', 'completed'))
        self.assertEqual(data['options']['urls'], [first, added])
        self.assertEqual(data['completed'], [first, added])
        self.assertEqual(len(data['files']), 2)
        self.assertIn(library_ui.LIBRARY_BUSY_LOG.format(error='database is locked'), lines)
        self.assertTrue(any(line.startswith('资料库已恢复') for line in lines), lines)
        self.assertIn(job, [row['id'] for row in app.library.history()])
        return app

    def test_a_download_under_an_exclusive_lock_starts_and_is_recorded_afterwards(self):
        self.run_download_under('exclusive')

    def test_a_download_under_a_reserved_lock_starts_and_is_recorded_afterwards(self):
        self.run_download_under('reserved')

    def test_enqueue_during_a_lock_keeps_the_click_order(self):
        """A job recorded before the lock: 加入队列 twice under the lock, the second link in another
        format; both are written in click order once the lock is gone (link, then variant)."""
        app = self.make_app()
        first = 'https://example.org/v170/order'
        with mock.patch.object(ui_download, 'Runner', ScriptRunner):
            app.urls.insert('1.0', first)
            click(app, app.start)
            job = app.download_job
            self.assertIsInstance(job, str)
            runner = ScriptRunner.instances[-1]
            self.assertTrue(runner.started.wait(5))
            pump(app, timeout=0.3)
            lock = Lock(self, app.library.path, 'reserved')
            probe = Probe(app)
            app.urls.insert('1.0', 'https://example.org/v170/second')
            self.assertEqual(click(app, app.enqueue_links), 1)
            app.mode.set(MODES[3])
            app.urls.insert('1.0', first)       # the same link again in another format: a variant
            self.assertEqual(click(app, app.enqueue_links), 1)
            self.assertEqual(app.history_writer.waiting(), 2)
            self.assertLess(probe.worst(), LATENCY)
            lock.release()
            self.assertTrue(app.history_writer.idle(10))
            runner.release.set()
            self.assertTrue(pump(app, lambda: not app.busy, 10))
        data = app.library.job(job)['data']
        self.assertEqual(data['options']['urls'], [first, 'https://example.org/v170/second'])
        self.assertEqual([(v['url'], v['options']['mode']) for v in data['options']['variants']], [(first, MODES[3])])

    def test_a_collection_started_under_a_lock_starts_once_it_is_gone(self):
        app = self.make_app()
        boxes = []
        with mock.patch.object(collection_ui, 'CollectionRunner', FakeCollectionRunner), \
                mock.patch.object(collection_ui.messagebox, 'showwarning', side_effect=lambda *a, **k: boxes.append(a[0])):
            app.collect_urls.insert('1.0', 'https://example.org/v170/list')
            lock = Lock(self, app.library.path, 'exclusive')
            probe = Probe(app)
            click(app, app.start_collection)
            self.assertEqual(boxes, [], 'no dialog for a lock')
            self.assertFalse(app.busy)
            self.assertEqual(ui_theme.full_text(app.collect_status), collection_ui.COLLECT_WAITING)
            self.assertIsNotNone(app.collect_pending)
            pump(app, timeout=2.5)              # one retry while the lock is still held
            self.assertIsNotNone(app.collect_pending)
            lock.release()
            self.assertTrue(pump(app, lambda: app.collect_pending is None and not app.busy
                                 and ui_theme.full_text(app.collect_status).startswith('采集结束'), 10))
        self.assertLess(probe.worst(), LATENCY)
        self.assertEqual(list(app.collected_links), ['https://media.example.org/a.mp3'])
        collects = [row for row in self.rows(app.library.path).values()
                    if row[0] == 'collect' and row[2]['options']['urls'] == ['https://example.org/v170/list']]
        self.assertEqual([status for _kind, status, _data in collects], ['completed'])

    def test_a_waiting_collection_start_gives_up_or_yields(self):
        app = self.make_app()
        options = CollectOptions(['https://example.org/v170/list']).validate()
        app.collect_pending = (options, None, None, None, time.monotonic() - 1)
        app.retry_collection()
        self.assertEqual(ui_theme.full_text(app.collect_status), collection_ui.COLLECT_GAVE_UP)
        app.collect_pending = (options, None, None, None, time.monotonic() + 60)
        app.busy = True
        try:
            app.retry_collection()
        finally:
            app.busy = False
        self.assertEqual(ui_theme.full_text(app.collect_status), collection_ui.COLLECT_DROPPED)
        self.assertIsNone(app.collect_pending)

    def test_the_window_thread_gives_up_quickly_and_workers_wait(self):
        store = library.Library(_sandbox.out('v170-quick', 'library.sqlite3'))
        store.create_job('download', 'x', {'urls': []})
        lock = Lock(self, store.path, 'exclusive')
        saved = library._ui_thread
        library.set_ui_thread()
        try:
            started = time.perf_counter()
            with self.assertRaises(sqlite3.OperationalError) as caught:
                store.history()
            self.assertTrue(library.is_busy(caught.exception))
            self.assertLess(time.perf_counter() - started, 0.5)
            started = time.perf_counter()
            with self.assertRaises(sqlite3.OperationalError):
                store.job('x')                  # right after: one try only (UI_QUIET)
            self.assertLess(time.perf_counter() - started, 0.15)
            seen = {}
            worker = threading.Thread(target=lambda: seen.setdefault('timeout', library._timeout()))
            worker.start()
            worker.join()
            self.assertEqual(seen['timeout'], library.WORKER_TIMEOUT)
        finally:
            library._ui_thread = saved
            library._ui_quiet_until = 0.0
        lock.release()
        self.assertEqual(len(store.history()), 1)

    def test_a_locked_file_at_start_up_is_used_not_replaced(self):
        path = _sandbox.out('v170-startup', 'library.sqlite3')
        library.Library(path).create_job('download', '以前的任务', {'urls': []})
        lock = Lock(self, path, 'exclusive')
        saved = library._ui_thread
        library.set_ui_thread()
        try:
            store, warning = library.open_library(path)
        finally:
            library._ui_thread = saved
            library._ui_quiet_until = 0.0
        self.assertEqual((store.path, warning), (path.resolve(), ''), 'no throw-away history for a lock')
        lock.release()
        self.assertEqual([row['title'] for row in store.history()], ['以前的任务'])

    def test_the_writer_keeps_order_retries_locks_and_drops_other_errors(self):
        logger = mock.Mock()
        writer = library_ui.HistoryWriter(logger)
        writer.RETRY = 0.2
        done, tries = [], {'n': 0}
        busy = sqlite3.OperationalError('database is locked')

        def flaky():
            tries['n'] += 1
            if tries['n'] < 3:
                raise busy
            done.append('first')
        self.assertFalse(writer.run(flaky))
        self.assertFalse(writer.run(lambda: done.append('second')), 'later writes wait behind it')
        writer.defer(lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
        writer.defer(lambda: done.append('third'))
        logged = logger.exception
        self.assertTrue(writer.idle(5))
        self.assertEqual(done, ['first', 'second', 'third'])
        self.assertEqual((writer.written, writer.dropped, logged.call_count), (3, 1, 1))
        self.assertTrue(writer.run(lambda: done.append('now')), 'nothing waits: written at once')
        with self.assertRaises(sqlite3.OperationalError):
            writer.run(lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
        writer.close()
        writer.defer(lambda: done.append('after close'))
        time.sleep(0.1)
        self.assertNotIn('after close', done)
        self.assertEqual(writer.waiting(), 1)


# ----------------------------------------------------------------------------------------- U2
class U2_HistoryTests(Base):
    def fill(self, store, count, start=1_700_000_000):
        with store.db() as db:
            for n in range(count):
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                           (f'job{n:04d}', 'download', f'第 {n} 首歌', 'interrupted', start + n,
                            json.dumps({'options': {'urls': [f'https://example.org/song/{n}'], 'output': 'x',
                                                    'mode': MODES[0]}}, ensure_ascii=False)))

    def test_the_store_pages_and_searches_title_and_links(self):
        store = library.Library(_sandbox.out('v170-history', 'library.sqlite3'))
        self.fill(store, 450)
        with store.db() as db:
            db.execute("INSERT INTO jobs VALUES('odd','download','100%_完成','completed',1,'not json')")
            db.execute("INSERT INTO jobs VALUES('plain','collect','列表','completed',2,?)",
                       (json.dumps({'options': {'urls': ['https://example.org/100%25_off']}}),))
        self.assertEqual(len(store.history()), library.HISTORY_PAGE)
        self.assertEqual(store.history_count(), 452)
        self.assertEqual([row['id'] for row in store.history(2)], ['job0449', 'job0448'])
        self.assertEqual([row['id'] for row in store.history(10, search='song/44')],
                         ['job0449', 'job0448', 'job0447', 'job0446', 'job0445', 'job0444', 'job0443',
                          'job0442', 'job0441', 'job0440'])
        self.assertEqual(store.history_count('song/44'), 11)
        self.assertEqual([row['title'] for row in store.history(search='第 7 首')], ['第 7 首歌'])
        self.assertEqual(store.history_count('EXAMPLE.ORG/SONG/1'), 111, 'ASCII ignores case')
        # % and _ are text, not wildcards; a record that is not JSON is found by its title only.
        self.assertEqual(store.history_count('100%_'), 1)
        self.assertEqual(store.history_count('%25_off'), 1)
        self.assertEqual(store.history_count('nothing like this'), 0)

    def test_the_list_loads_more_and_searches(self):
        app = self.make_app(smoke=True, visible=True)
        self.fill(app.library, 650)
        app.refresh_library()
        self.assertEqual(len(app.history_tree.get_children()), 300)
        self.assertEqual(app.history_count.cget('text'), '共 650 条，显示最近 300 条')
        self.assertEqual(app.history_more.winfo_manager(), 'grid')
        self.assertEqual(app.history_more.cget('text'), '加载更多 300 条')
        app.history_more.invoke()
        self.assertEqual(len(app.history_tree.get_children()), 600)
        self.assertEqual(app.history_more.cget('text'), '加载更多 50 条')
        app.history_more.invoke()
        self.assertEqual(len(app.history_tree.get_children()), 650)
        self.assertEqual(app.history_count.cget('text'), '共 650 条')
        self.assertEqual(app.history_more.winfo_manager(), '')
        # A refresh keeps what was loaded.
        app.refresh_library()
        self.assertEqual(len(app.history_tree.get_children()), 650)
        # The search runs after a pause in typing, from the newest page again.
        app.history_search.set('song/5')
        self.assertTrue(pump(app, lambda: app.history_count.cget('text').startswith('找到'), 3))
        self.assertEqual(app.history_count.cget('text'), '找到 111 条')
        self.assertEqual(app.history_limit, library.HISTORY_PAGE)
        # An old job (beyond the newest 300) is found and can be resumed again.
        app.history_search.set('song/12')
        self.assertTrue(pump(app, lambda: app.history_count.cget('text') == '找到 11 条', 3))
        old = [iid for iid in app.history_tree.get_children() if app.history_tree.full_values(iid)[1] == '第 12 首歌']
        self.assertEqual(old, ['job0012'])
        app.history_tree.selection_set(old)
        app.history_selection()
        self.assertEqual(app.history_resume.cget('state'), 'normal')
        app.history_search.set('没有这样的任务')
        self.assertTrue(pump(app, lambda: not app.history_tree.get_children(), 3))
        self.assertEqual(app.history_count.cget('text'), '找到 0 条')
        self.assertEqual(app.history_tree._empty, library_ui.HISTORY_NONE_FOUND.format(term='没有这样的任务'))
        app.history_search.set('')
        self.assertTrue(pump(app, lambda: len(app.history_tree.get_children()) == 300, 3))
        self.assertEqual(app.history_tree._empty, library_ui.HISTORY_EMPTY)

    def test_the_tab_fits_at_the_minimum_size(self):
        app = self.make_app(smoke=True, visible=True)
        self.fill(app.library, 400)
        app.refresh_library()
        app.show_page('library')
        pump(app, timeout=0.3)
        self.assertLessEqual(app.pages['library'].winfo_reqheight(), app.content.winfo_height() + 1)
        bar_right = app.history_search_entry.winfo_rootx() + app.history_search_entry.winfo_width()
        table_right = app.history_tree.container.winfo_rootx() + app.history_tree.container.winfo_width()
        self.assertLessEqual(bar_right, table_right + 1, 'the search field ends with the table')
        self.assertTrue(app.history_more.winfo_ismapped())


# ----------------------------------------------------------------------------------------- U3
class U3_CollectionCapTests(Base):
    def pages(self, count=10, links=1000):
        return [{'url': f'https://example.org/list?p={p}', 'title': f'第 {p} 页', 'text': '', 'markdown': '',
                 'links': [{'url': f'https://media.example.org/{p}/{n}.mp3', 'title': f'{p}-{n}', 'kind': '媒体地址',
                            'source': f'https://example.org/list?p={p}'} for n in range(links)]}
                for p in range(count)]

    def test_a_record_loaded_after_a_run_keeps_the_cap_of_the_run(self):
        app = self.make_app(smoke=True)
        options = CollectOptions([f'https://example.org/list?p={p}' for p in range(10)]).validate()
        job = app.library.create_job('collect', '大列表', asdict(options))
        pages = self.pages()
        for page in pages:
            app.library.collection_event(job, 'collect_page', dict(page, _checkpoint=None))
        app.library.change_job(job, status='completed')
        app.collection_job = job
        app.operation = 'collect'
        app.set_busy(True)
        for page in pages:
            app.handle('collect_page', page)
        live = list(app.collected_links)
        self.assertEqual(len(live), MAX_LINKS)
        app.handle('collect_done', {'cancelled': False})
        self.assertEqual(list(app.collected_links), live, 'the same first 2,000 links as during the run')
        self.assertEqual(ui_theme.full_text(app.collect_status),
                         f'采集结束：10 页 / {MAX_LINKS} 条链接；已达 {MAX_LINKS} 条上限，请分批采集')
        self.assertEqual(app.collect_count.cget('text'), f'显示 {MAX_LINKS} / 共 {MAX_LINKS} · 已选 0')
        record = app.library.job(job)
        self.assertEqual(sum(len(page['links']) for page in record['pages']), 10_000, 'the record stays whole')
        # Opened again from the history: the same cap and a status line that says so.
        app.load_collection_record(record)
        self.assertEqual(list(app.collected_links), live)
        self.assertEqual(ui_theme.full_text(app.collect_status),
                         f'历史结果：10 页，{MAX_LINKS} 条链接；已达 {MAX_LINKS} 条上限')

    def test_a_small_record_is_shown_whole_without_a_cap_note(self):
        app = self.make_app(smoke=True)
        pages = self.pages(2, 3) + self.pages(1, 2)      # the third page repeats links of the first
        record = {'pages': pages, 'data': {'options': {'urls': ['https://example.org/list?p=0']}}}
        app.load_collection_record(record)
        self.assertEqual(len(app.collected_links), 6)
        self.assertEqual(ui_theme.full_text(app.collect_status), '历史结果：3 页，6 条链接')
        self.assertEqual(library_ui.capped_links(self.pages(3, 1000)).keys(),
                         {f'https://media.example.org/{p}/{n}.mp3': 0 for p in range(2) for n in range(1000)}.keys())


# ----------------------------------------------------------------------------------------- U4
class U4_HeldSettingsTests(Base):
    def test_a_held_settings_file_is_said_once_in_the_notice(self):
        path = _sandbox.write_settings(output=str(_sandbox.out('v170-held')), mode=MODES[3])
        kernel = ctypes.windll.kernel32
        kernel.CreateFileW.restype = ctypes.c_void_p
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)     # read, no sharing
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        lines = []
        real = StudioApp.append_log

        def capture(window, line):
            lines.append(str(line))
            return real(window, line)
        try:
            with mock.patch.object(StudioApp, 'append_log', capture):
                ctk.set_appearance_mode('light')
                app = StudioApp(_sandbox.app_args())
            app.withdraw()
            self.addCleanup(self.close, app)
            app.smoke_mode = True           # this test never saves over the held file
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
        self.assertIn(settings.HELD, app.prefs)
        self.assertEqual(app.notice_kind, 'settings')
        self.assertEqual(ui_theme.full_text(app.notice_text), SETTINGS_HELD_NOTICE)
        self.assertEqual(app.notice_bar.winfo_manager(), 'grid')
        self.assertEqual(len([line for line in lines if line.startswith('设置文件暂时无法读取')]), 1, lines)
        app.notice_buttons[0].invoke()          # 知道了
        self.assertIsNone(app.notice_kind)
        self.assertEqual(app.notice_bar.winfo_manager(), '')

    def test_a_readable_settings_file_shows_no_notice(self):
        app = self.make_app()
        self.assertNotIn(settings.HELD, app.prefs)
        self.assertIsNone(app.notice_kind)


# ----------------------------------------------------------------------------------------- U5
class U5_ClearedHeaderTests(Base):
    def test_the_header_after_clearing_fits_at_the_minimum_size(self):
        app = self.make_app(visible=True, size='1000x740')
        app.output.set(str(_sandbox.out('v170-clear')))
        links = ['https://example.org/v170/a.mp3', 'https://example.org/v170/b.mp3']
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        for index, (url, success) in enumerate(zip(links, (True, False)), 1):
            app.handle('item', {'index': index, 'total': 2, 'url': url})
            app.handle('item_done', {'index': index, 'success': success, 'cancelled': False, 'files': int(success),
                                     'bot_check': False, 'reason': None if success else '网络中断'})
        app.handle('done', {'success': 1, 'failed': 1, 'files': [], 'cancelled': False, 'failed_urls': [links[1]],
                            'pending_urls': []})
        app.unfinished = [{'url': links[1], 'key': 'example.org/v170/b.mp3', 'format': 'audio_mp3', 'reason': 'failed',
                           'options': {}, 'time': 1}]
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True), \
                mock.patch.object(engine, 'save_unfinished'):
            app.clear_unfinished()
        pump(app, timeout=0.3)
        self.assertEqual(ui_theme.full_text(app.current_title), '任务结束 · 1 条成功')
        self.assertEqual(app.current_title.cget('text'), '任务结束 · 1 条成功', 'no ellipsis at 1000x740')
        self.assertEqual(ui_theme.full_text(app.metrics), ui_download.CLEARED_METRICS)
        app.smoke_mode = True


# ----------------------------------------------------------------------------------------- U6
class U6_BusySwitchTests(Base):
    def test_a_download_does_not_redraw_the_hidden_start_url_box(self):
        app = self.make_app(smoke=True)
        self.assertEqual(app.current_page, 'download')
        app.collect_urls.insert('1.0', 'https://example.org/list')
        with mock.patch.object(app, '_dim_collect_box', wraps=app._dim_collect_box) as dim, \
                mock.patch.object(app.collect_add, 'configure', wraps=app.collect_add.configure) as add:
            app.operation = 'download'
            app.set_busy(True)
            dim.assert_not_called()
            add_calls = add.call_count          # set_busy's own loop disables it once
            app.collection_selection_changed()
            self.assertEqual(add.call_count, add_calls, 'nothing changed: not configured again')
            app.show_page('collect')
            dim.assert_called_with(True)
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.FAINT)
        app.show_page('download')
        app.set_busy(False)
        app.show_page('collect')
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.INK)
        self.assertEqual(app.collect_urls.cget('fg_color'), ui_theme.SURFACE_ALT)

    def test_a_collection_still_dims_at_once(self):
        app = self.make_app(smoke=True)
        app.operation = 'collect'
        app.set_busy(True)
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.FAINT)
        app.set_busy(False)
        self.assertEqual(app.collect_urls.cget('text_color'), ui_theme.INK)


# ------------------------------------------------------------------------------ fix round 1
class FixRound1Tests(Base):
    """docs/1.7.0/handoff/fix-merge.md, "Fix round 1": v-ui-1-u1-latency-margin (a busy result that is
    handled calmly anyway costs the window one try, not four), v-ui-1-u2-nonjson-record (a record that
    is not JSON never stops the list) and v-ui-1-u1-worker-wait (the Runner's find_download does not
    hold a download's start for the worker timeout)."""

    @staticmethod
    def counting(thread):
        """A sqlite3.connect stand-in that counts the connections ``thread`` opens."""
        real, calls = sqlite3.connect, []

        def connect(*args, **kwargs):
            if threading.get_ident() == thread:
                calls.append(time.perf_counter())
            return real(*args, **kwargs)
        return connect, calls

    def test_a_handled_busy_result_costs_the_window_one_try(self):
        store = library.Library(_sandbox.out('v170-fix1-wait', 'library.sqlite3'))
        store.create_job('download', 'x', {'urls': []})
        lock = Lock(self, store.path, 'exclusive')
        saved = library._ui_thread
        library.set_ui_thread()
        writer = library_ui.HistoryWriter(mock.Mock())
        tries = {}
        try:
            for label, wait in (('default', None), ('write', library_ui.UI_WRITE_WAIT), ('once', 0)):
                library._ui_quiet_until = 0.0
                connect, calls = self.counting(threading.get_ident())
                with mock.patch.object(library.sqlite3, 'connect', connect), \
                        self.assertRaises(sqlite3.OperationalError):
                    if wait is None:
                        store.history()
                    else:
                        with library.ui_wait(wait):
                            store.history()
                tries[label] = len(calls)
                self.assertIsNone(getattr(library._local, 'wait', None), 'ui_wait() ends with its block')
            # The HistoryWriter's immediate try on the window's thread (history_create, 加入队列, ...).
            library._ui_quiet_until = 0.0
            connect, calls = self.counting(threading.get_ident())
            with mock.patch.object(library.sqlite3, 'connect', connect):
                self.assertFalse(writer.run(lambda: store.create_job('download', 'y', {'urls': []})))
            tries['writer'] = len(calls)
        finally:
            library._ui_thread = saved
            library._ui_quiet_until = 0.0
        self.assertGreaterEqual(tries['default'], 2, tries)
        self.assertLessEqual(tries['write'], 2, tries)
        self.assertEqual(tries['once'], 1, tries)
        self.assertLessEqual(tries['writer'], 2, tries)
        lock.release()
        self.assertTrue(writer.idle(10), 'the deferred write is done once the lock is gone')
        self.assertEqual(sorted(row['title'] for row in store.history()), ['x', 'y'])

    def test_history_create_and_collect_done_ask_for_the_short_wait(self):
        app = self.make_app()
        seen = []

        def create():
            seen.append(getattr(library._local, 'wait', None))
            return app.library.create_job('download', '一个任务', {'urls': ['https://example.org/v170/w']})
        job = app.history_create(create)
        self.assertIsInstance(job, str)
        self.assertEqual(seen, [library_ui.UI_WRITE_WAIT])
        self.assertIsNone(getattr(library._local, 'wait', None))
        record = app.library.create_job('collect', '列表', asdict(CollectOptions(['https://example.org/v170/l']).validate()))
        reads, real = [], app.library.job

        def job_read(job_id):
            reads.append(getattr(library._local, 'wait', None))
            return real(job_id)
        app.collection_job, app.active_subscription, app.collection_background = record, None, False
        with mock.patch.object(app.library, 'job', side_effect=job_read):
            app.handle_collection('collect_done', {'cancelled': False})
        self.assertEqual(reads[:1], [0], 'collect_done reads the record with one try')
        self.assertIsNone(app.collection_job)
        self.assertFalse(app.busy)

    def test_a_record_that_is_not_json_is_listed_and_never_stops_the_list(self):
        store = library.Library(_sandbox.out('v170-fix1-badjson', 'library.sqlite3'))
        with store.db() as db:
            db.execute("INSERT INTO jobs VALUES('new','download','新任务','completed',3,?)",
                       (json.dumps({'options': {'urls': ['https://example.org/1']}}),))
            db.execute("INSERT INTO jobs VALUES('bad','download','坏记录 badjson','running',2,'{not json')")
            db.execute("INSERT INTO jobs VALUES('list','download','列表记录','completed',1,'[1, 2]')")
        rows = store.history()
        self.assertEqual([(row['id'], row['status'], row['data']) for row in rows[1:]],
                         [('bad', 'running', {}), ('list', 'completed', {})])
        self.assertEqual([row['title'] for row in store.history(search='badjson')], ['坏记录 badjson'])
        self.assertEqual(store.history_count('badjson'), 1)
        with store.db() as db:
            self.assertEqual(tuple(db.execute("SELECT status, data FROM jobs WHERE id='bad'").fetchone()),
                             ('running', '{not json'), 'nothing is written to such a record')
        # The list: 加载更多 reaches the record (the oldest) and shows it by its title.
        app = self.make_app(smoke=True)
        with app.library.db() as db:
            for n in range(300):
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',
                           (f'job{n:04d}', 'download', f'第 {n} 首歌', 'interrupted', 1_700_000_000 + n,
                            json.dumps({'options': {'urls': [f'https://example.org/song/{n}'], 'output': 'x',
                                                    'mode': MODES[0]}})))
            db.execute("INSERT INTO jobs VALUES('bad','download','坏记录 badjson','completed',1,'{not json')")
        app.refresh_library()
        self.assertEqual(len(app.history_tree.get_children()), 300)
        self.assertEqual(app.history_more.cget('text'), '加载更多 1 条')
        app.history_more.invoke()
        self.assertEqual(len(app.history_tree.get_children()), 301)
        self.assertEqual(app.history_tree.full_values('bad')[1], '坏记录 badjson')
        self.assertEqual(app.history_count.cget('text'), '共 301 条')

    def test_the_downloaded_check_under_a_lock_does_not_hold_the_start(self):
        store = library.Library(_sandbox.out('v170-fix1-find', 'library.sqlite3'))
        store.create_job('download', 'x', {'urls': []})
        events = []
        runner = engine.Runner(lambda kind, value: events.append((time.monotonic(), kind, value)), library=store)
        # (跳过已下载过的内容 is on in the window's default settings.)
        options = engine.Options(_sandbox.out('v170-fix1-find-out'), MODES[0], batch_safe=False, skip_downloaded=True)
        lock = Lock(self, store.path, 'exclusive')
        with mock.patch.object(library, 'FIND_TIMEOUT', 0.3), \
                mock.patch.object(engine.Runner, '_run_once', lambda _runner, _command, *_args: (0, False, None)):
            started = time.monotonic()
            worker = threading.Thread(target=runner.download, args=(['https://example.org/v170/find'], options))
            worker.start()
            worker.join(30)
        lock.release()
        self.assertFalse(worker.is_alive())
        items = [moment for moment, kind, _value in events if kind == 'item']
        self.assertTrue(items, events)
        self.assertLess(items[0] - started, 5, 'the item starts after FIND_TIMEOUT, not the worker timeout')
        logs = [str(value) for _moment, kind, value in events if kind == 'log']
        self.assertTrue(any(line.startswith('下载记录无法读取，照常下载') for line in logs), logs)
        seen = {}
        worker = threading.Thread(target=lambda: seen.setdefault('timeout', library._timeout()))
        worker.start()
        worker.join()
        self.assertEqual(seen['timeout'], library.WORKER_TIMEOUT, 'other worker calls keep the worker timeout')


if __name__ == '__main__':
    unittest.main()
