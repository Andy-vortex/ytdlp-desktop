"""Final review of 1.6.0, area ui: regression tests of the fixed findings (final-fix-ui).

polish-function-1        launch checks each option set once, not build_command for every link
polish-function-2        清除未完成 takes the last job's rows out of the unfinished count
polish-function-3, polish-visual-2  the hidden library tabs take no Tab focus; a focus left there moves
polish-function-4        the footer says 会照常下载 only until the job has ended
polish-function-5        the history note of a proxy failure uses the row's proxy wording
polish-visual-1          the pages stay gridded and stacked (a switch unmaps nothing); covered pages take no Tab focus
polish-visual-3          an active item beyond the row cap is also named in the metrics line
final-acceptance-ui-1    Ctrl+L and Ctrl+E also with Caps Lock on
final-e2e-ui-1           the log keeps following the end after the window got smaller
final-e2e-ui-2           加入队列 updates the task header, the window title and the sidebar at once
final-e2e-ui-3           a percent-encoded link is shown decoded in its row (display only)
final-architecture-2     a collection whose history record cannot be written does not start, and says so
final-architecture-3     StudioApp.failed_job is gone; engine.load_failed_job stays
final-privacy-security-1 a collected link holding a second link reaches the download box whole
final-merge              an over-long link is failed alone by the Runner, not refused with the whole job;
                         a held settings.json is named once in the task log (final-fix-core requests 1, 2)
fix round 1              on_format redraws only what changed; a late row-build step yields once
                         (polish-function-1); 清除未完成 also rewrites the 任务结束 header (rv-findings-1-1)

Every app is created and destroyed on its own; the clipboard is never read and nothing is downloaded
(the Runner is replaced by FakeRunner).
"""
import _sandbox
import re
import sqlite3
import threading
import time
import tkinter as tk
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit

import customtkinter as ctk
import collection_ui
import engine
import formats
import library_ui
import ui_download
import ui_theme
import unfinished
import updater
import urls
from collector import CollectOptions
from engine import MODES, Options
from studio import StudioApp


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
        self.opened = True
        self.enqueued = []
        FakeRunner.instances.append(self)

    def download(self, urls_, options, overrides=None):
        self.urls, self.options, self.overrides = list(urls_), options, overrides
        if FakeRunner.script is not None:
            FakeRunner.script(self)
        else:
            self.release.wait(20)

    def enqueue(self, urls_, options=None, overrides=None):
        self.enqueued.append((list(urls_), options))
        return list(urls_)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None


def focus_ok(widget):
    """Whether Tk's Tab traversal would stop at ``widget``."""
    return str(widget.tk.call('::tk::FocusOK', str(widget))) == '1'


def tab_walk(start, steps=60):
    widget, seen = start, []
    for _ in range(steps):
        widget = widget.tk_focusNext()
        if widget is None:
            break
        seen.append(str(widget))
    return seen


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard),
                        mock.patch.object(engine, 'PACE_SECONDS', (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        engine.Runner.reset_pacing()
        engine.save_unfinished([])
        FakeRunner.instances = []
        FakeRunner.script = None
        self.addCleanup(self._release_runners)

    def _release_runners(self):
        for runner in FakeRunner.instances:
            runner.release.set()
        FakeRunner.script = None
        engine.save_unfinished([])

    def make_app(self, smoke=True, visible=True, size='1000x740', **values):
        if not smoke:
            _sandbox.write_settings(**values)
        ctk.set_appearance_mode('light')
        args = (_sandbox.app_args('https://example.invalid/smoke', str(_sandbox.out('smoke-out')))
                if smoke else _sandbox.app_args())
        app = StudioApp(args)
        if visible:
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            app.geometry(size + '+0+0')
        else:
            app.withdraw()
        self.addCleanup(self.close, app)
        app.urls.delete('1.0', 'end')
        pump(app, timeout=0.3)
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

    def run_job(self, app, script, action):
        FakeRunner.script = script
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            action()
            self.assertTrue(pump(app, lambda: not app.busy, 10))
        pump(app, timeout=0.2)

    @staticmethod
    def build_all(app, timeout=30):
        return pump(app, lambda: not app.row_queue and app.row_after is None, timeout)


class A_LaunchTests(Base):
    def test_each_option_set_is_checked_once(self):
        """polish-function-1."""
        app = self.make_app()
        calls = []
        real = ui_download.build_command

        def counted(url, options, *args, **kwargs):
            calls.append(url)
            return real(url, options, *args, **kwargs)
        links = [f'https://example.org/long/{n}' for n in range(300)]
        other = self.options(mode=MODES[2])
        with mock.patch.object(ui_download, 'build_command', counted), \
                mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(links, self.options(), overrides={links[5]: other}))
        self.assertEqual(calls, [links[0], links[5]])
        self.assertTrue(app.busy)
        self.assertEqual(len(app.row_model), 300)

    def test_a_bad_referer_of_a_later_link_still_stops_the_launch(self):
        """polish-function-1: the per-link part of the check (the referer) still runs for every referer."""
        app = self.make_app()
        links = ['https://example.org/a', 'https://example.org/b']
        options = self.options(referers={links[1]: 'javascript:alert(1)'})
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(ui_download.messagebox, 'showwarning') as warning:
            self.assertFalse(app.launch(links, options))
        warning.assert_called_once()
        self.assertFalse(app.busy)
        self.assertEqual(FakeRunner.instances, [])


class B_ClearTests(Base):
    def test_clear_takes_the_job_rows_out_of_the_unfinished_count(self):
        """polish-function-2."""
        app = self.make_app(smoke=False, visible=False, output=str(_sandbox.out('downloads')))
        broken, fine = 'https://example.org/broken', 'https://example.org/fine'

        def script(runner):
            runner.emit('item', {'index': 1, 'total': 2, 'url': broken})
            item = engine.QueueItem(1, broken, runner.options)
            result = {'url': broken, 'key': item.key, 'format': item.format,
                      'options': engine._entry_options(runner.options), 'outcome': 'failed', 'error': 'ERROR: x',
                      'title': '', 'referer': '', 'media_keys': []}
            with unfinished.LOCK:
                unfinished.save(unfinished.merge(unfinished.load_for_merge(lambda text: None), [result]))
            runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': False, 'files': 0,
                                      'bot_check': False, 'url': broken,
                                      'reason': engine.msg('core.reason.http_404')})     # (1.8.0) as the engine emits it
            runner.emit('item', {'index': 2, 'total': 2, 'url': fine})
            runner.emit('item_done', {'index': 2, 'cancelled': False, 'success': True, 'files': 1,
                                      'bot_check': False, 'url': fine})
            runner.emit('done', {'success': 1, 'failed': 1, 'files': [], 'cancelled': False, 'failed_urls': [broken],
                                 'pending_urls': [broken], 'failed_path': None, 'archive_errors': [],
                                 'bot_check': False})
        self.run_job(app, script, lambda: app.launch([broken, fine], self.options()))
        self.assertEqual(app.queue_stats.cget('text'), '成功 1  /  未完成 1  /  共 2')
        self.assertEqual(app.retry_button.cget('text'), '重试未完成 (1)')
        self.assertEqual(ui_theme.full_text(app.metrics), ui_download.FINISHED_UNFINISHED)
        self.assertEqual(ui_theme.full_text(app.current_title), '任务结束 · 1 条成功，1 条未完成')
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
            app.clear_unfinished()
        self.assertEqual(app.unfinished, [])
        self.assertEqual(app.queue_stats.cget('text'), '成功 1  /  未完成 0  /  共 2  /  已移出 1')
        # (final fix round 1, rv-findings-1-1) The header no longer names the unfinished link.
        # (1.7.0 U5) Shorter, so that it fits at 1000x740; the metrics line says 未完成记录已清除.
        self.assertEqual(ui_theme.full_text(app.current_title), '任务结束 · 1 条成功')
        self.assertEqual((app.retry_button.cget('text'), app.retry_button.cget('state')), ('重试未完成', 'disabled'))
        self.assertEqual(ui_theme.full_text(app.metrics), ui_download.CLEARED_METRICS)
        model = app.row_model[0]
        self.assertTrue(model['cleared'])
        self.assertEqual(app.rows[0]['badge'].cget('text'), ui_download.CLEARED_CHIP)
        self.assertEqual(model['detail'], f'{ui_download.CLEARED_DETAIL} · 链接不存在（404）')
        # Its reason and 重试此条 stay available (the state is still 'failed').
        self.assertIn(model['state'], ui_download.REASON_STATES)
        self.assertIn(model['state'], ui_download.RETRYABLE)
        # A new state of the row (another run) drops the flag.
        app.update_row(0, state='waiting')
        self.assertFalse(app.row_model[0]['cleared'])


class C_FocusTests(Base):
    def test_hidden_tabs_take_no_tab_focus(self):
        """polish-function-3 / polish-visual-2, at the widget level."""
        root = ctk.CTk()

        def close():
            for job in root.tk.splitlist(root.tk.call('after', 'info')):
                root.tk.call('after', 'cancel', job)        # customtkinter's pending title-bar icon call
            root.destroy()
        self.addCleanup(close)
        root.attributes('-alpha', 0.0)
        tabs = ui_theme.tabs(root, ['一', '二', '三'])
        tabs.grid(row=0, column=0, sticky='nsew')
        entries = {}
        for name in ('一', '二', '三'):
            entries[name] = tk.Entry(tabs.tab(name))
            entries[name].grid(row=0, column=0)
        root.update()
        for name in ('一', '二', '三'):
            tabs.set(name)
            root.update()
            for other, entry in entries.items():
                self.assertEqual(focus_ok(entry), other == name, f'{other} while {name} is shown')
            self.assertTrue(all(tabs.tab(other).winfo_ismapped() for other in entries))     # stacked, not unmapped
        # A focus left in a tab that is switched away from moves to the new tab.
        tabs.set('一')
        entries['一'].focus_set()
        tabs.set('二')
        self.assertEqual(str(root.tk.call('focus', '-lastfor', root._w)), str(tabs.tab('二')))

    def test_library_tab_walk_stays_in_the_shown_tab(self):
        """polish-function-3 / polish-visual-2, in the window; the focus moves to the new tab's table."""
        app = self.make_app()
        app.show_page('library')
        names = {'任务历史与续采': app.history_tree, '增量订阅': app.subscription_tree, '统一资料归档': app.archive_tree}
        for name, table in names.items():
            app.library_tabs.set(name)
            pump(app, timeout=0.05)
            hidden = [str(app.library_tabs.tab(other)) for other in names if other != name]
            stops = tab_walk(table)
            self.assertTrue(stops)
            self.assertFalse([stop for stop in stops for path in hidden if stop.startswith(path + '.')], name)
        app.library_tabs.set('任务历史与续采')
        app.history_tree.focus_set()
        app.library_tabs._clicked('统一资料归档')          # a click on the tab bar
        self.assertEqual(str(app.tk.call('focus', '-lastfor', app._w)), str(app.archive_tree))

    def test_pages_stay_gridded_and_covered_pages_take_no_tab_focus(self):
        """polish-visual-1."""
        app = self.make_app()
        app.populate_queue([f'https://example.org/long/{n}' for n in range(120)])
        self.assertTrue(self.build_all(app))
        for name in ('collect', 'download', 'library', 'download', 'settings', 'logs', 'download'):
            began = time.perf_counter()
            app.show_page(name)
            took = time.perf_counter() - began
            self.assertEqual(sorted(key for key, page in app.pages.items() if page.winfo_manager() == 'grid'),
                             sorted(app.pages), 'no page is un-gridded')
            self.assertLess(took, 1.0)       # generous (busy machine); was 0.3 s, plus as much at the next update
            pump(app, timeout=0.05)
            stops = tab_walk(app.nametowidget(str(app.tk.call('focus', '-lastfor', app._w))))
            others = [str(page) for key, page in app.pages.items() if key != name]
            self.assertFalse([stop for stop in stops for path in others if stop.startswith(path + '.')], name)


class D_FooterAndHeaderTests(Base):
    def test_footer_after_the_job_ended(self):
        """polish-function-4."""
        app = self.make_app()
        links = [f'https://example.org/long/{n:03d}' for n in range(300)]
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        self.assertTrue(self.build_all(app))
        hidden = 300 - len(app.rows)
        self.assertEqual(ui_theme.full_text(app.row_footer), f'还有 {hidden} 条未显示，会照常下载')
        stored = [{'url': url, 'key': urls.link_key(url, False), 'format': 'audio_mp3', 'reason': 'stopped',
                   'options': {}} for url in links[3:]]
        with mock.patch.object(engine, 'load_unfinished', return_value=stored):
            app.handle('done', {'success': 3, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [],
                                'pending_urls': links[3:]})
        self.assertTrue(self.build_all(app))
        self.assertEqual(ui_theme.full_text(app.row_footer), f'还有 {hidden} 条未显示，可点“重试未完成”')
        app.unfinished = []
        app._update_footer()
        self.assertEqual(ui_theme.full_text(app.row_footer), f'还有 {hidden} 条未显示')

    def test_the_active_item_beyond_the_cap_is_in_the_metrics_line(self):
        """polish-visual-3."""
        app = self.make_app()
        links = [f'https://example.org/long/{n}' for n in range(120)]
        app.populate_queue(links)
        self.assertTrue(self.build_all(app))
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 2, 'total': 120, 'url': links[1]})
        self.assertEqual(ui_theme.full_text(app.metrics), '正在连接网站…')          # a row with widgets
        app.handle('item', {'index': 100, 'total': 120, 'url': links[99]})
        self.assertEqual(ui_theme.full_text(app.metrics), '正在连接网站…   ·   当前：example.org/long/99')
        app.handle('title', '第一百首')
        self.assertEqual(ui_theme.full_text(app.metrics), '正在连接网站…   ·   当前：第一百首')
        app.handle('progress', {'fraction': 0.5, 'downloaded': 50, 'total': 100, 'speed': 10.0, 'eta': 5,
                                'status': 'downloading'})
        self.assertTrue(ui_theme.full_text(app.metrics).startswith('50.0%'))
        self.assertTrue(ui_theme.full_text(app.metrics).endswith('当前：第一百首'))
        app.set_busy(False)

    def test_enqueue_updates_the_totals_at_once(self):
        """final-e2e-ui-2."""
        app = self.make_app()
        links = [f'https://example.org/media/s{n}.mp4' for n in range(1, 5)]
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        app.runner = FakeRunner(lambda *a: None)
        try:
            app.handle('item', {'index': 1, 'total': 4, 'url': links[0]})
            app.handle('item_done', {'index': 1, 'cancelled': False, 'success': True, 'files': 1, 'bot_check': False})
            app.handle('item', {'index': 2, 'total': 4, 'url': links[1]})
            app.urls.insert('1.0', 'https://example.org/media/s5.mp4\nhttps://example.org/media/s6.mp4')
            app.start()                                      # 加入队列
            self.assertEqual(len(app.row_model), 6)
            self.assertEqual(ui_theme.full_text(app.current_title), '正在下载 2 / 6')
            self.assertEqual(app.side_status.cget('text'), '正在下载 2 / 6')
            self.assertTrue(app.title().startswith('2/6'))
            # The engine's own 'queued' event changes nothing more.
            app.handle('queued', {'urls': ['https://example.org/media/s5.mp4', 'https://example.org/media/s6.mp4'],
                                  'first_index': 5, 'total': 6})
            self.assertEqual(len(app.row_model), 6)
            self.assertEqual(ui_theme.full_text(app.current_title), '正在下载 2 / 6')
        finally:
            app.runner = None
            app.set_busy(False)


class E_KeysAndLogTests(Base):
    @staticmethod
    def fire(app, sequence):
        """Run the Python callbacks bound to ``sequence`` on the window, as Tk does for the key (a
        generated key event is dropped while another program has the keyboard focus)."""
        script = app.bind(sequence)
        commands = re.findall(r'\[(\S+) %#', script)
        keysym = sequence.strip('<>').rsplit('-', 1)[-1]
        # %# %b %f %h %k %s %t %w %x %y %A %E %K %N %W %T %X %Y %D (tkinter's bind substitution)
        fields = ('1', '??', '0', '0', '0', '6', '0', '0', '0', '0', keysym, '0', keysym, '0', str(app), '2', '0', '0', '0')
        errors = []
        with mock.patch.object(app, 'report_callback_exception', lambda *exc: errors.append(exc[1])):
            for command in commands:
                app.tk.call(command, *fields)
        if errors:
            raise errors[0]
        return len(commands)

    def test_ctrl_l_and_ctrl_e_with_caps_lock(self):
        """final-acceptance-ui-1: the uppercase keysyms (Caps Lock on) run the same actions."""
        app = self.make_app()
        calls = []
        app.reveal_last_file = lambda: calls.append('reveal')
        for lower, upper in (('<Control-l>', '<Control-L>'), ('<Control-e>', '<Control-E>')):
            self.assertEqual(len(re.findall(r'\[(\S+) %#', app.bind(upper))), 1, upper)
            self.assertEqual(len(re.findall(r'\[(\S+) %#', app.bind(lower))), 1, lower)
        app.start_button._canvas.focus_set()
        self.assertEqual(self.fire(app, '<Control-L>'), 1)
        self.assertEqual(str(app.tk.call('focus', '-lastfor', app._w)), str(getattr(app.urls, '_textbox', app.urls)))
        self.assertEqual(self.fire(app, '<Control-E>'), 1)
        self.assertEqual(calls, ['reveal'])
        # Not on another page (the shortcuts belong to the download page).
        app.show_page('collect')
        self.fire(app, '<Control-E>')
        self.assertEqual(calls, ['reveal'])

    def test_the_log_keeps_following_after_the_window_got_smaller(self):
        """final-e2e-ui-1."""
        app = self.make_app(size='1120x810')
        app.show_page('logs')

        def burst(count=12):
            for n in range(count):
                app.append_log(f'[download] Destination: C:\\work\\a long path\\song-{n} [song-{n}].mp4 ' + 'x' * 60)
            pump(app, timeout=0.08)
        for _ in range(10):
            burst()
        self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        app.geometry('1000x740+0+0')                       # restored from a bigger size
        pump(app, timeout=0.3)
        for _ in range(5):
            burst()
            self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        # The same while another page is shown (as during a download).
        app.show_page('download')
        app.geometry('1120x810+0+0')
        pump(app, timeout=0.2)
        app.geometry('1000x740+0+0')
        pump(app, timeout=0.2)
        for _ in range(5):
            burst()
        app.show_page('logs')
        pump(app, timeout=0.2)
        self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        # The owner scrolls up: the view stays there; back at the end it follows again.
        app.logs.yview_moveto(0.2)
        app._log_scrolled()
        pump(app, timeout=0.1)
        self.assertFalse(app.log_follow)
        first = app.logs.yview()[0]
        burst()
        self.assertLess(app.logs.yview()[1], 0.999)
        self.assertLessEqual(abs(app.logs.yview()[0] - first), 0.05)
        app.logs.yview_moveto(1.0)
        app._log_scrolled()
        pump(app, timeout=0.1)
        self.assertTrue(app.log_follow)
        burst()
        self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        # 只看错误 keeps a following reader at the end.
        app.toggle_errors()
        pump(app, timeout=0.05)
        self.assertGreaterEqual(app.logs.yview()[1], 0.999)
        app.toggle_errors()


class F_LinkTests(Base):
    def test_percent_encoded_links_are_shown_decoded(self):
        """final-e2e-ui-3."""
        name = '周杰伦 - 晴天【官方MV】「高音质」.mp4'
        url = collection_ui.queueable_url(f'http://127.0.0.1:52103/media/{name}')
        self.assertIn('%E5%91%A8', url)
        self.assertEqual(ui_download.link_title(url), f'127.0.0.1:52103/media/{name}')
        self.assertEqual(ui_download.link_title('https://example.org/a%0Ab.mp4'), 'example.org/a b.mp4')
        self.assertEqual(ui_download.link_title('https://example.org/%zz'), 'example.org/%zz')
        app = self.make_app()
        app.populate_queue([url])
        self.assertEqual(app.row_model[0]['title'], urls.display(f'127.0.0.1:52103/media/{name}'))
        self.assertEqual(app.row_model[0]['url'], url)            # the link itself is unchanged
        self.assertEqual(app.row_model[0]['key'], urls.link_key(url, False))

    def test_collected_links_holding_a_second_link_stay_whole(self):
        """final-privacy-security-1."""
        for href in ('https://site.example/a.mp4?a=1|https://other.example/y.mp4',
                     'https://site.example/a.mp4?u=x%7Chttps://other.example/y.mp4',
                     'https://site.example/go;https://other.example/y.mp4',
                     'https://site.example/a.mp4,https://other.example/y.mp4',
                     'https://site.example/watch?next=https://other.example/x.mp4'):
            queued = collection_ui.queueable_url(href)
            self.assertEqual(urls.extract_urls(queued), [queued], href)
            self.assertEqual(urls.parse_download_text(queued), [queued], href)
            self.assertEqual(urlsplit(queued).netloc, 'site.example')
        self.assertEqual(unquote(collection_ui.queueable_url('https://site.example/go;https://other.example/y.mp4')),
                         'https://site.example/go;https://other.example/y.mp4')

    def test_a_link_that_cannot_stay_whole_is_left_out(self):
        """final-privacy-security-1: never queued in pieces."""
        app = self.make_app()
        items = [{'url': 'https://site.example/a.mp4', 'title': 'a', 'kind': '媒体地址', 'source': 'https://site.example/'},
                 {'url': 'https://site.example/b.mp4', 'title': 'b', 'kind': '媒体地址', 'source': 'https://site.example/'}]
        split = {'https://site.example/b.mp4': 'https://site.example/b.mp4|https://other.example/c.mp4'}
        with mock.patch.object(collection_ui, 'queueable_url', lambda url: split.get(url, url)):
            app.queue_collected_items(items)
        self.assertEqual(urls.extract_urls(app.box_text()), ['https://site.example/a.mp4'])
        logged = '\n'.join(app.log_buffer) + app.logs.get('1.0', 'end-1c')
        self.assertIn('这条采集链接无法完整放入下载列表，已跳过：https://site.example/b.mp4', logged)


class G_HistoryTests(Base):
    def run_failure(self, app, proxy):
        line = "ERROR: [generic] x: Unable to download webpage: <urlopen error [WinError 10061]>"

        def script(runner):
            url = runner.urls[0]
            runner.emit('item', {'index': 1, 'total': 1, 'url': url})
            runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                      'url': url, 'reason': engine.NETWORK_REASON, 'error': line})
            runner.emit('done', {'success': 0, 'failed': 1, 'files': [], 'cancelled': False, 'failed_urls': [url],
                                 'pending_urls': [url], 'failed_path': None, 'archive_errors': [], 'bot_check': False})
        self.run_job(app, script, lambda: app.launch(['https://example.org/song'], self.options(proxy=proxy)))
        record = app.library.job(app.download_job)
        return app.row_model[0]['detail'], library_ui.LibraryUI.history_note_text(record)

    def test_the_history_note_of_a_proxy_failure_says_what_the_row_says(self):
        """polish-function-5."""
        app = self.make_app(smoke=False, visible=False, output=str(_sandbox.out('downloads')))
        detail, note = self.run_failure(app, 'http://127.0.0.1:9')
        self.assertEqual(detail, ui_download.PROXY_REASON)
        self.assertTrue(note.endswith(f'最近错误：{ui_download.PROXY_REASON}'), note)
        self.assertNotIn('127.0.0.1:9', str(app.library.job(app.download_job)['data']))     # the proxy is never stored
        detail, note = self.run_failure(app, '')
        self.assertEqual(detail, engine.NETWORK_REASON)
        self.assertTrue(note.endswith(f'最近错误：{engine.NETWORK_REASON}'), note)

    def test_history_value_is_a_copy(self):
        value = {'index': 1, 'reason': engine.NETWORK_REASON}
        stored = ui_download.history_value('item_done', value, True)
        self.assertEqual((stored['reason'], value['reason']), (ui_download.PROXY_REASON, engine.NETWORK_REASON))
        self.assertIs(ui_download.history_value('item_done', value, False), value)
        self.assertIs(ui_download.history_value('item', value, True), value)


class H_CollectionStartTests(Base):
    def test_a_history_error_does_not_start_and_says_so(self):
        """final-architecture-2."""
        app = self.make_app(smoke=False, visible=False)
        app.collected_links['https://old.example/x.mp4'] = {'url': 'https://old.example/x.mp4', 'title': 'old',
                                                             'kind': '媒体地址', 'source': 'https://old.example/'}
        app.collect_urls.insert('1.0', 'https://example.invalid/page')
        errors, boxes = [], []
        app.report_callback_exception = lambda *exc: errors.append(exc[1])
        # (1.7.0 U1) A lock held by another program no longer ends here (it waits and retries by
        # itself: test_v170_ui.U1_LockTests); a history that cannot be written still does.
        locked = sqlite3.OperationalError('disk I/O error')
        with mock.patch.object(app.library, 'create_job', side_effect=locked), \
                mock.patch.object(collection_ui.messagebox, 'showwarning',
                                  side_effect=lambda *a, **k: boxes.append(a[0])):
            app.tk.call(app.register(app.collect_start.invoke))     # through a Tcl command, as a click runs it
            app.update()
        self.assertEqual(errors, [])
        self.assertEqual(boxes, ['采集未开始'])
        self.assertFalse(app.busy)
        self.assertIsNone(app.operation)
        self.assertIn('https://old.example/x.mp4', app.collected_links)       # the results on screen stay
        self.assertTrue(ui_theme.full_text(app.collect_status).startswith('采集未开始'))
        # A resume (change_job) fails the same way; a background check re-raises for its back-off.
        options = CollectOptions(['https://example.org/list']).validate()
        with mock.patch.object(app.library, 'change_job', side_effect=locked), \
                mock.patch.object(collection_ui.messagebox, 'showwarning') as warning:
            self.assertFalse(app.launch_collection(options, job_id='42'))
            warning.assert_called_once()
        with mock.patch.object(app.library, 'create_job', side_effect=locked):
            with self.assertRaises(sqlite3.OperationalError):
                app.launch_collection(options, background=True)
        self.assertFalse(app.busy)


class I_DeadStateTests(Base):
    def test_failed_job_is_gone(self):
        """final-architecture-3."""
        app = self.make_app(smoke=False, visible=False)
        self.assertFalse(hasattr(app, 'failed_job'))
        self.assertFalse(hasattr(ui_download, 'load_failed_job'))
        self.assertTrue(callable(engine.load_failed_job))       # the public 1.5.1 wrapper stays


class J_FinalMergeRequestTests(Base):
    """final-merge: the cross-area requests of final-fix-core (requests 1 and 2)."""
    LONG = 'https://example.org/v/' + 'a' * (engine.COMMAND_LINE_LIMIT + 100)

    def test_an_over_long_link_is_left_to_the_runner(self):
        """Request 2: an over-long first link of its option set no longer refuses the job; the
        other links are checked and the Runner fails the long one alone."""
        app = self.make_app()
        links = [self.LONG, 'https://example.org/b', 'https://example.org/c']
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(ui_download.messagebox, 'showwarning') as warning:
            self.assertTrue(app.launch(links, self.options()))
            self.assertTrue(pump(app, lambda: FakeRunner.instances and hasattr(FakeRunner.instances[-1], 'urls'), 5))
        warning.assert_not_called()
        self.assertEqual(FakeRunner.instances[-1].urls, links)

    def test_only_over_long_links_still_start_nothing(self):
        app = self.make_app()
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(ui_download.messagebox, 'showwarning') as warning:
            self.assertFalse(app.launch([self.LONG], self.options()))
        warning.assert_called_once()
        self.assertIn('链接过长', warning.call_args[0][1])
        self.assertFalse(app.busy)
        self.assertEqual(FakeRunner.instances, [])

    def test_other_refusals_still_stop_the_launch(self):
        """A bad referer of the link after an over-long one is still found before anything starts."""
        app = self.make_app()
        links = [self.LONG, 'https://example.org/b']
        options = self.options(referers={links[1]: 'javascript:alert(1)'})
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(ui_download.messagebox, 'showwarning') as warning:
            self.assertFalse(app.launch(links, options))
        warning.assert_called_once()
        self.assertNotIn('链接过长', warning.call_args[0][1])
        self.assertEqual(FakeRunner.instances, [])

    def test_held_settings_are_named_in_the_task_log(self):
        """Request 1: a settings.json that could not be read is named once in the task log."""
        import settings
        _sandbox.write_settings()
        values = settings.load()
        self.assertNotIn(settings.HELD, values)
        held = dict(values)
        held[settings.HELD] = {'loaded': {key: values[key] for key in values if not key.startswith('_')},
                               'changed': set()}
        with mock.patch.object(settings, 'load', return_value=held):
            app = self.make_app(smoke=False, visible=False)
        app.flush_log()
        text = app.logs.get('1.0', 'end')
        self.assertEqual(text.count('设置文件暂时无法读取'), 1, text)
        self.close(app)
        plain = self.make_app(smoke=False, visible=False)
        plain.flush_log()
        self.assertNotIn('设置文件暂时无法读取', plain.logs.get('1.0', 'end'))


class K_FixRound1Tests(Base):
    """Final fix round 1: polish-function-1 (the stall after 开始下载) and rv-findings-1-1 (the header
    after 清除未完成)."""

    def test_on_format_touches_nothing_that_did_not_change(self):
        app = self.make_app()
        video = next(preset for preset in formats.PRESETS if preset.kind == 'video')
        audio = next(preset for preset in formats.PRESETS if preset.kind == 'audio')
        for preset in (video, audio):
            with self.subTest(kind=preset.kind):
                app.format.set(preset.label)
                app.on_format()
                pump(app, timeout=0.1)
                calls = []
                patchers = []
                for widget in (app.quality_menu, app.subtitle_check):
                    for name in ('configure', 'grid', 'grid_remove'):
                        real = getattr(widget, name)
                        patchers.append(mock.patch.object(
                            widget, name, side_effect=lambda *a, _r=real, _n=name, **k: (calls.append(_n), _r(*a, **k))[1]))
                for patcher in patchers:
                    patcher.start()
                try:
                    app.on_format()
                finally:
                    for patcher in patchers:
                        patcher.stop()
                self.assertEqual(calls, [], 'a second on_format with nothing changed redraws nothing')
                # A busy switch still reaches both widgets; the state they show stays right.
                app.operation = 'download'
                app.set_busy(True)
                self.assertEqual(app.quality_menu.cget('state'), 'disabled')
                self.assertEqual(app.subtitle_check.cget('state'), 'disabled')
                app.set_busy(False)
                expected = 'normal' if preset.kind == 'video' else 'disabled'
                self.assertEqual((app.quality_menu.cget('state'), app.subtitle_check.cget('state')), (expected, expected))
                self.assertEqual(bool(app.quality_menu.winfo_manager()), preset.kind == 'video')
                self.assertEqual(app.mode.get(), preset.mode)

    def test_a_late_build_step_lets_the_window_through_first(self):
        app = self.make_app()
        app.populate_queue([f'https://example.org/long/{number}' for number in range(30)])
        self.assertEqual(len(app.rows), ui_download.ROW_FIRST)
        generation = app.row_generation
        # The step comes far later than asked (the click handler that started the job was long).
        app.row_due = time.perf_counter() - 1
        app.after_cancel(app.row_after)
        app.row_after = None
        app.build_rows(generation)
        self.assertEqual(len(app.rows), ui_download.ROW_FIRST, 'the late step built nothing')
        self.assertIsNotNone(app.row_after, 'it posted the next step')
        # The next step is on time and builds; a step late again after a yield builds as well.
        app.after_cancel(app.row_after)
        app.row_after = None
        app.row_due = time.perf_counter() - 1
        app.build_rows(generation)
        self.assertGreater(len(app.rows), ui_download.ROW_FIRST, 'never two yields in a row')
        self.assertTrue(self.build_all(app))
        self.assertEqual(len(app.rows), 30)

    def test_clear_rewrites_only_the_header_that_names_unfinished_links(self):
        app = self.make_app(smoke=False, visible=False, output=str(_sandbox.out('downloads')))
        entry = {'url': 'https://example.org/x', 'key': 'example.org/x', 'format': 'audio_mp3', 'reason': 'failed',
                 'options': {}, 'time': 1}
        for title, expected in (('任务结束 · 3 条成功，2 条未完成', '任务结束 · 3 条成功'),       # (1.7.0 U5)
                                ('任务已停止', '任务已停止'), ('YouTube 暂时拒绝了当前网络', 'YouTube 暂时拒绝了当前网络'),
                                ('下载完成 · 2 条成功', '下载完成 · 2 条成功')):
            with self.subTest(title=title):
                app.unfinished = [entry]
                app.current_title.configure(text=title)
                with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
                    app.clear_unfinished()
                self.assertEqual(app.unfinished, [])
                self.assertEqual(ui_theme.full_text(app.current_title), expected)


if __name__ == '__main__':
    unittest.main()
