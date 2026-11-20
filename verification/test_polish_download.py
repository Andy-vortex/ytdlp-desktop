"""Polish round of 1.6.0, area download-shell: regression tests of the fixed review findings.

ui-daily-flow-1     the task list and 重试未完成 (N) describe the same links (unfinished rows below the job)
ui-wording-consistency-1  page switches move the keyboard focus; hidden pages are not gridded
ui-daily-flow-2     a transport or proxy failure is never an outdated engine; a proxy is named
ui-daily-flow-3     the bot-check countdown replaces the undo notice of a finished job
ui-daily-flow-4     a job that only skipped links says so
ui-daily-flow-7     the progress bar of a job with unfinished links shows the share that succeeded
ui-wording-consistency-8  the log calls the stored list 未完成列表
ui-performance-1    a long job builds at most ROW_LIMIT rows in short steps; the active row beyond them is named
lead-review item 3  number and unit spacing of the download history title

Every app is created and destroyed on its own (one live StudioApp per process); non-smoke apps start
from _sandbox.write_settings().  The clipboard is never read: StudioApp.clipboard_get is patched.
"""
import _sandbox
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import formats
import i18n
import library_ui
import ui_download
import ui_theme
import unfinished
import updater
from engine import MODES, Options
from studio import StudioApp


from _sandbox import no_clipboard, no_network, pump, zh_t


LINK = 'https://www.youtube.com/watch?v=AAAAAAAAAAA'
PROXY_LINE = ("ERROR: [generic] song-g: Unable to download webpage: ('Unable to connect to proxy', "
              "OSError('Tunnel connection failed: 0')) (caused by ProxyError('Unable to connect to proxy')); "
              'please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the '
              'appropriate issue template. Confirm you are on the latest version using  yt-dlp -U')
STALE_LINE = ('ERROR: [youtube] AAAAAAAAAAA: Signature solving failed: Some formats may be missing; please report '
              'this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= . Confirm you are on the latest version '
              'using  yt-dlp -U')


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


def store_unfinished(urls, options):
    """Put ``urls`` into the stored unfinished list as failed links (the engine's merge)."""
    results = []
    for url in urls:
        item = engine.QueueItem(1, url, options)
        results.append({'url': url, 'key': item.key, 'format': item.format, 'options': engine._entry_options(options),
                        'outcome': 'failed', 'error': 'ERROR: x', 'title': '', 'referer': '', 'media_keys': []})
    with unfinished.LOCK:
        entries = unfinished.merge(unfinished.load_for_merge(lambda text: None), results)
        unfinished.save(entries)


def drop_unfinished(url):
    with unfinished.LOCK:
        unfinished.save([entry for entry in unfinished.load_for_merge(lambda text: None) if entry['url'] != url])


def lastfocus(app):
    """The widget that has (or gets, when the window is activated) the keyboard focus."""
    name = str(app.tk.call('focus', '-lastfor', app._w))
    try:
        return app.nametowidget(name)
    except KeyError:
        return None


def page_of(app, widget):
    for name, frame in app.pages.items():
        current = widget
        while current is not None:
            if current is frame:
                return name
            current = getattr(current, 'master', None)
    return None


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

    def make_app(self, smoke=False, visible=False, size='1120x810', **values):
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

    def run_job(self, app, script, action):
        FakeRunner.script = script
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            action()
            self.assertTrue(pump(app, lambda: not app.busy, 10))
        pump(app, timeout=0.2)

    @staticmethod
    def states(app):
        return [(model['state'], model['url'].rsplit('/', 1)[-1]) for model in app.row_model]


def succeed_one(runner):
    url = runner.urls[0]
    runner.emit('item', {'index': 1, 'total': 1, 'url': url})
    runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': True, 'files': 1, 'bot_check': False,
                              'url': url})
    drop_unfinished(url)
    runner.emit('done', {'success': 1, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                         'pending_urls': [], 'failed_path': None, 'archive_errors': [], 'bot_check': False})


class A_UnfinishedRowsTests(Base):
    """ui-daily-flow-1 (major)."""

    def test_idle_retry_of_one_row_keeps_the_other_unfinished_rows(self):
        store_unfinished(['https://example.org/a', 'https://example.org/b', 'https://example.org/c'], self.options())
        app = self.make_app()
        pump(app, timeout=0.2)
        self.assertEqual(self.states(app), [('unfinished', 'a'), ('unfinished', 'b'), ('unfinished', 'c')])
        self.run_job(app, succeed_one, lambda: app.retry_row(1))
        self.assertEqual(self.states(app), [('done', 'b'), ('unfinished', 'a'), ('unfinished', 'c')])
        self.assertEqual(len(app.unfinished), 2)
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=2))
        # (2.0.0 S4) no stats line: the unfinished count is on 重试未完成 (N), shown with the task '⋯' while
        # there are unfinished links (was: download.stats.final beside it, the same count again)
        self.assertTrue(app.retry_button.winfo_manager() and app.task_menu.winfo_manager())
        self.assertEqual([row['index'] for row in app.rows], [0, 1, 2])
        self.assertEqual(app.rows[1]['badge'].cget('text'), zh_t('common.unfinished'))
        # The per-row actions work on a leftover row again: 从列表移除 removes it and its entry.
        app.remove_row(2)
        self.assertEqual(self.states(app), [('done', 'b'), ('unfinished', 'a')])
        self.assertEqual([entry['url'] for entry in engine.load_unfinished()], ['https://example.org/a'])
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=1))

    def test_a_new_job_shows_the_leftovers_below_its_rows(self):
        store_unfinished(['https://example.org/old1', 'https://example.org/old2'], self.options())
        app = self.make_app()
        pump(app, timeout=0.2)
        FakeRunner.script = None             # the job waits until released
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            self.assertTrue(app.launch(['https://example.org/new1', 'https://example.org/new2'], self.options()))
            self.assertEqual(self.states(app), [('waiting', 'new1'), ('waiting', 'new2'),
                                                ('unfinished', 'old1'), ('unfinished', 'old2')])
            self.assertEqual(app.job_row_count(), 2)
            # (2.0.0 S4) the leftovers' count is on 重试未完成 (N) (grey while the job runs), not in a stats line
            self.assertEqual((app.retry_button.cget('text'), str(app.retry_button.cget('state'))),
                             (zh_t('download.retry_unfinished_n', count=2), 'disabled'))
            # 加入队列 while it runs: the new link joins the job's rows, the leftovers move down,
            # and the engine's item numbers still address the job's rows.
            app.urls.insert('1.0', 'https://example.org/new3')
            self.assertEqual(app.enqueue_links(), 1)
            self.assertEqual(self.states(app), [('waiting', 'new1'), ('waiting', 'new2'), ('waiting', 'new3'),
                                                ('unfinished', 'old1'), ('unfinished', 'old2')])
            app.handle('item', {'index': 3, 'total': 3, 'url': 'https://example.org/new3'})
            self.assertEqual(app.row_model[2]['state'], 'active')
            self.assertEqual(app.row_model[3]['state'], 'unfinished')
            # 重试此条 on a leftover while the job runs: it is queued and leaves the leftover rows.
            app.retry_row(4)
            self.assertEqual(FakeRunner.instances[-1].enqueued[-1][0], ['https://example.org/old2'])
            self.assertEqual(self.states(app), [('waiting', 'new1'), ('waiting', 'new2'), ('active', 'new3'),
                                                ('waiting', 'old2'), ('unfinished', 'old1')])
            # An item event beyond the known rows (a link the engine took in itself) is inserted above the tail.
            app.handle('queued', {'urls': ['https://example.org/new5'], 'first_index': 5, 'total': 5})
            self.assertEqual(self.states(app)[4:], [('waiting', 'new5'), ('unfinished', 'old1')])
            FakeRunner.instances[-1].release.set()
            app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [],
                                'pending_urls': [], 'failed_path': None})
        self.assertFalse(app.busy)
        self.assertTrue(pump(app, lambda: not app.row_queue, 5))
        self.assertEqual([row['index'] for row in app.rows], list(range(len(app.row_model))))
        self.assertEqual([row['url'] for row in app.rows], [model['url'] for model in app.row_model])

    def test_stats_count_the_leftovers_like_the_retry_button(self):
        store_unfinished(['https://example.org/left'], self.options())
        app = self.make_app()

        def fail(runner):
            url = runner.urls[0]
            store_unfinished([url], self.options())
            runner.emit('item', {'index': 1, 'total': 1, 'url': url})
            runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': False, 'files': 0,
                                      'bot_check': False, 'url': url,
                                      'reason': engine.msg('core.reason.http_404')})     # (1.8.0) as the engine emits it
            runner.emit('done', {'success': 0, 'failed': 1, 'files': [], 'cancelled': False, 'failed_urls': [url],
                                 'pending_urls': [url], 'failed_path': None, 'archive_errors': [],
                                 'bot_check': False})
        self.run_job(app, fail, lambda: app.launch(['https://example.org/broken'], self.options()))
        self.assertEqual(self.states(app), [('failed', 'broken'), ('unfinished', 'left')])
        # (2.0.0 S4) the title names the successes only; every link 重试未完成 retries is counted on the button
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.finished_n', success=0))
        self.assertEqual(app.retry_button.cget('text'), zh_t('download.retry_unfinished_n', count=2))
        # 清除未完成 empties the list and takes the leftover rows with it.
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
            app.clear_unfinished()
        self.assertEqual(self.states(app), [('failed', 'broken')])


class B_FocusTests(Base):
    """ui-wording-consistency-1 (major)."""

    def setUp(self):
        super().setUp()
        self.app = self.make_app(smoke=True, visible=True, size='1000x740')
        self.app.urls.delete('1.0', 'end')
        self.app.links_changed()
        pump(self.app, timeout=0.3)

    def paste_link(self, widget):
        """<<Paste>> on ``widget`` with a link in the (patched) clipboard.  A box of another page records
        the paste instead of reading the real clipboard."""
        got = []
        inner = getattr(self.app.collect_urls, '_textbox', self.app.collect_urls)
        binding = inner.bind('<<Paste>>', lambda event: (got.append(True), 'break')[1])
        try:
            with mock.patch.object(StudioApp, 'clipboard_get', lambda self, *a, **k: LINK):
                widget.event_generate('<<Paste>>')
                pump(self.app, timeout=0.1)
        finally:
            inner.unbind('<<Paste>>', binding)
        return bool(got)

    def test_only_the_current_page_takes_the_focus(self):
        # (final-fix-ui, polish-visual-1) The pages stay gridded and stacked, as in INT: a switch
        # unmaps nothing.  The covered pages take no Tab focus (was: only the current page is gridded).
        app = self.app
        fields = {'download': getattr(app.urls, '_textbox', app.urls),
                  'collect': getattr(app.collect_urls, '_textbox', app.collect_urls), 'library': app.history_tree}
        for name in ('collect', 'library', 'logs', 'settings', 'download'):
            app.show_page(name)
            pump(app, timeout=0.05)
            self.assertTrue(app.pages[name].winfo_ismapped() or not app.winfo_ismapped())
            for page, widget in fields.items():
                self.assertEqual(app.tk.call('::tk::FocusOK', str(widget)) in (1, '1', True), page == name,
                                 f'{page} field while {name} is shown')

    def test_switch_to_collect_then_paste_leaves_the_download_box(self):
        app = self.app
        app.urls.focus_set()
        pump(app, timeout=0.05)
        app.show_page('collect')                     # what Ctrl+2 and the sidebar do
        focus = lastfocus(app)
        self.assertEqual(page_of(app, focus), 'collect')
        self.assertIs(focus, getattr(app.collect_urls, '_textbox', app.collect_urls))
        self.assertTrue(self.paste_link(focus))
        self.assertEqual(app.urls.get('1.0', 'end-1c'), '')

    def test_back_to_download_then_paste_fills_the_download_box(self):
        app = self.app
        app.show_page('collect')
        app.collect_urls.focus_set()
        pump(app, timeout=0.05)
        app.show_page('download')                    # Ctrl+1 from the collect box
        focus = lastfocus(app)
        self.assertEqual(page_of(app, focus), 'download')
        self.assertFalse(self.paste_link(focus))
        self.assertEqual(app.urls.get('1.0', 'end-1c'), LINK)
        self.assertEqual(app.link_count.cget('text'), '1')        # (2.0.0 S4) the chip shows the number only

    def press(self, widget, sequence, fallback):
        """Deliver key ``sequence`` to ``widget``.  Tk drops generated key events while the window has no
        keyboard focus (another program is in front, as during an unattended run): the handler the
        binding would run is called then instead."""
        seen, live = [], [True]
        tk.Misc.bind(widget, sequence, lambda event: seen.append(True) if live else None, add='+')
        widget.event_generate(sequence)
        pump(self.app, timeout=0.05)
        live.clear()            # (no unbind: in Python 3.11 it would drop the app's own binding too)
        if not seen:
            fallback()

    def test_start_keys_work_after_a_page_switch(self):
        app = self.app
        app.show_page('collect')
        app.collect_urls.focus_set()
        app.show_page('download')                    # Ctrl+1 from the collect box
        focus = lastfocus(app)
        self.assertIs(focus, getattr(app.urls, '_textbox', app.urls))
        with mock.patch.object(app, 'start') as start, \
                mock.patch.object(app, 'focus_get', lambda: lastfocus(app)):
            # Ctrl+Enter (the hint's start key) reaches the link box, which starts the download.
            self.press(focus, '<Control-Return>', app._start_from_box)
            self.assertEqual(start.call_count, 1)
            # Enter starts when the focus is on the download page outside a text field (in the link
            # box itself Enter is a new line); with the focus left in the hidden collect box it did nothing.
            app.start_button.focus_set()
            pump(app, timeout=0.05)
            button = lastfocus(app)
            self.assertEqual(page_of(app, button), 'download')
            self.assertNotIsInstance(button, (tk.Text, tk.Entry))
            self.press(button, '<Return>', lambda: app._key(app.start, outside_text=True))
            self.assertEqual(start.call_count, 2)

    def test_natural_fields(self):
        app = self.app
        expected = {'download': getattr(app.urls, '_textbox', app.urls),
                    'collect': getattr(app.collect_urls, '_textbox', app.collect_urls),
                    'logs': getattr(app.logs, '_textbox', app.logs), 'settings': app.pages['settings']}
        for name, widget in expected.items():
            app.show_page('library' if name != 'library' else 'download')
            app.show_page(name)
            self.assertIs(lastfocus(app), widget, name)
        app.library_tabs.set(library_ui.TAB_ARCHIVE)
        app.show_page('library')
        self.assertIs(lastfocus(app), app.archive_tree)
        # Focus already inside the new page stays where it is.
        app.show_page('download')
        app.output_button.focus_set()       # (2.0.0 S4) the folder is a button now (no entry)
        app.show_page('download')
        self.assertEqual(page_of(app, lastfocus(app)), 'download')
        self.assertIsNot(lastfocus(app), expected['download'])

    def test_tab_stays_on_the_shown_page(self):
        app = self.app
        app.show_page('download')
        widget = lastfocus(app)
        for _ in range(8):
            widget = widget.tk_focusNext()
            if widget is None:
                break
            self.assertIn(page_of(app, widget), ('download', None))


class C_EngineReasonTests(Base):
    """ui-daily-flow-2."""

    def test_a_proxy_failure_is_not_an_outdated_engine(self):
        runner = engine.Runner(lambda *args: None)
        runner._note_line(PROXY_LINE)
        self.assertFalse(runner.stale)
        self.assertEqual(engine.explain_error(runner.last_error, stale=runner.stale), engine.NETWORK_REASON)

    def test_the_stale_pattern_still_counts(self):
        for line in (STALE_LINE, 'ERROR: [youtube] x: nsig extraction failed: n challenge solving failed',
                     'WARNING: Your yt-dlp version is older than 90 days'):
            runner = engine.Runner(lambda *args: None)
            runner._note_line(line)
            self.assertTrue(runner.stale, line)

    def test_row_text_of_a_network_failure(self):
        app = self.make_app()
        app.populate_queue(['https://example.org/p1', 'https://example.org/p2', 'https://example.org/p3'],
                           self.options(proxy='http://127.0.0.1:9'),
                           {'https://example.org/p2': self.options()})
        app.operation = 'download'
        # (1.8.0) the reasons as the engine emits them: messages whose values are the 1.7.0 texts
        for index, reason in ((1, engine.NETWORK_REASON), (2, engine.NETWORK_REASON), (3, engine.msg('core.reason.http_404'))):
            app.handle('item_done', {'index': index, 'cancelled': False, 'success': False, 'files': 0,
                                     'bot_check': False, 'reason': reason, 'stale_engine': True})
        # (2.0.0 S4) one space each side of the '·' (00-system 2.7)
        self.assertEqual(ui_theme.full_text(app.rows[0]['detail']), f"01 · {zh_t('download.reason.proxy')}")
        self.assertEqual(ui_theme.full_text(app.rows[1]['detail']), f'02 · {engine.NETWORK_REASON}')
        self.assertEqual(ui_theme.full_text(app.rows[2]['detail']),
                         f"03 · {zh_t('download.detail.stale_maybe', reason=zh_t('core.reason.http_404'))}")


class D_DoneTests(Base):
    """ui-daily-flow-3, -4, -7 and ui-wording-consistency-8."""

    def test_bot_countdown_replaces_the_undo_notice(self):
        app = self.make_app()

        def bot(runner):
            urls = runner.urls
            runner.emit('item', {'index': 1, 'total': 2, 'url': urls[0]})
            runner.emit('item_done', {'index': 1, 'cancelled': False, 'success': True, 'files': 1,
                                      'bot_check': False, 'url': urls[0]})
            runner.emit('item_done', {'index': 2, 'cancelled': False, 'success': False, 'files': 0,
                                      'bot_check': True, 'skipped': 'bot_check', 'url': urls[1]})
            runner.emit('done', {'success': 1, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                                 'pending_urls': [urls[1]], 'failed_path': None, 'archive_errors': [],
                                 'bot_check': True, 'blocked': 'bot_check'})
        app.urls.insert('1.0', 'https://example.org/song\n' + LINK)
        self.run_job(app, bot, app.start)
        self.assertIsNotNone(app.bot_retry)
        self.assertEqual(app.notice_kind, 'bot')
        # the countdown changes; what follows it is fixed
        after = zh_t('download.notice.bot_retry', time='{time}', round=1, rounds=3).split('{time}')[1]
        self.assertIn(after, app.notice_bar.full_text)
        self.assertEqual([button.cget('text') for button in app.notice_bar.buttons],
                         [zh_t('download.notice.retry_now'), zh_t('common.cancel')])
        self.assertEqual(app.progress.target, 0.5)                 # ui-daily-flow-7: 1 of 2
        app.cancel_bot_retry()

    def test_undo_after_the_job_ended(self):
        app = self.make_app()
        app.urls.insert('1.0', 'https://example.org/one\nhttps://example.org/two')

        def done(runner):
            for number, url in enumerate(runner.urls, 1):
                runner.emit('item', {'index': number, 'total': 2, 'url': url})
                runner.emit('item_done', {'index': number, 'cancelled': False, 'success': True, 'files': 1,
                                          'bot_check': False, 'url': url})
            runner.emit('done', {'success': 2, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                                 'pending_urls': [], 'failed_path': None, 'archive_errors': [], 'bot_check': False})
        self.run_job(app, done, app.start)
        self.assertEqual(app.notice_kind, 'undo')
        # (2.0.0 S4) the notice keeps its words when the job ends (download.notice.undo is gone); it still
        # puts the links back, and goes away by itself
        self.assertEqual(app.notice_bar.full_text, zh_t('download.notice.started', count=2))
        self.assertEqual([button.cget('text') for button in app.notice_bar.buttons],
                         [zh_t('download.notice.restore_links')])
        app.notice_bar.buttons[0].invoke()
        self.assertFalse(app.notice_bar.shown)
        self.assertEqual(app.urls.get('1.0', 'end-1c'), 'https://example.org/one\nhttps://example.org/two')

    def test_undo_keeps_its_place_while_the_job_runs(self):
        app = self.make_app()
        app.arm_bot_retry(1)
        app.urls.insert('1.0', 'https://example.org/other')
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
            self.assertTrue(app.busy)
            self.assertIsNotNone(app.bot_retry)             # another site leaves the countdown armed
            app.bot_retry_tick()
            self.assertEqual(app.notice_kind, 'undo')
        app.cancel_bot_retry()

    def skip_job(self, app, success):
        def script(runner):
            for number, url in enumerate(runner.urls, 1):
                done = number <= success
                runner.emit('item_done', {'index': number, 'cancelled': False, 'success': done, 'files': int(done),
                                          'bot_check': False, 'skipped': '' if done else 'downloaded', 'url': url,
                                          'path': ''})
            runner.emit('done', {'success': success, 'failed': 0, 'files': [], 'cancelled': False,
                                 'failed_urls': [], 'pending_urls': [], 'failed_path': None, 'archive_errors': [],
                                 'bot_check': False, 'skipped': len(runner.urls) - success})
        return script

    def test_a_job_that_only_skipped(self):
        app = self.make_app()
        self.run_job(app, self.skip_job(app, 0),
                     lambda: app.launch(['https://example.org/a', 'https://example.org/b'], self.options()))
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.all_skipped', skipped=2))
        # (2.0.0 S4) the title says it; the rows say 已下载过 and offer 仍然下载 in their menu (no metrics sentence)
        self.assertEqual(ui_theme.full_text(app.metrics), '')
        self.assertEqual([model['state'] for model in app.row_model], ['skipped', 'skipped'])
        self.assertIn(zh_t('download.menu.anyway'), [item.label for item in app.row_menu_items(0) if item])
        self.assertEqual(app.progress.target, 1)

    def test_a_job_that_downloaded_and_skipped(self):
        app = self.make_app()
        self.run_job(app, self.skip_job(app, 1),
                     lambda: app.launch(['https://example.org/a', 'https://example.org/b'], self.options()))
        # (2.0.0 S4) one title for a successful job; the skipped row keeps its chip (已下载过)
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.done', success=1))
        self.assertEqual(ui_theme.full_text(app.metrics), '')
        self.assertEqual(app.rows[1]['badge'].cget('text'), zh_t('download.chip.skipped'))

    def test_partial_job_progress_and_log_wording(self):
        app = self.make_app()

        def partial(runner):
            urls = runner.urls
            for number, url in enumerate(urls, 1):
                runner.emit('item', {'index': number, 'total': 3, 'url': url})
                runner.emit('progress', {'fraction': 1.0, 'status': 'downloading'})
                ok = number != 1
                runner.emit('item_done', {'index': number, 'cancelled': False, 'success': ok, 'files': int(ok),
                                          'bot_check': False, 'url': url})
            runner.emit('done', {'success': 2, 'failed': 1, 'files': [], 'cancelled': False,
                                 'failed_urls': [urls[0]], 'pending_urls': [urls[0]],
                                 'failed_path': 'X:\\state\\failed-downloads.json', 'archive_errors': [],
                                 'bot_check': False, 'unfinished': 1})
        self.run_job(app, partial, lambda: app.launch([f'https://example.org/{n}' for n in 'abc'], self.options()))
        self.assertAlmostEqual(app.progress.target, 2 / 3, places=3)
        app.flush_log()
        text = app.logs.get('1.0', 'end-1c')
        self.assertIn(zh_t('log.unfinished_list', path='X:\\state\\failed-downloads.json'), text)
        self.assertNotIn('失败清单', text)

    def test_history_title_spacing(self):
        app = self.make_app()
        created = []
        real = app.library.create_job

        def record(kind, title, data):
            created.append(title)
            return real(kind, title, data)
        FakeRunner.script = lambda runner: None
        with mock.patch.object(app.library, 'create_job', record), \
                mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.launch(['https://example.org/a', 'https://example.org/b'], self.options(mode=MODES[0]))
            pump(app, lambda: not app.busy, 5)
        # (2.0.0 S2 P2) no placeholder title any more: the history names the job after its links
        self.assertEqual(created, [''])
        row = app.library.history()[0]
        self.assertEqual(library_ui.history_title(row), i18n.t('value.job_title.multi', title='example.org/a', count=2))


class E_LongListTests(Base):
    """ui-performance-1."""

    def test_a_long_job_builds_few_rows_in_short_steps(self):
        app = self.make_app(visible=True)
        pump(app, timeout=0.5)
        steps = []
        real = app.build_rows

        def timed(generation, scheduled=True):
            began = time.perf_counter()
            real(generation, scheduled)
            steps.append((scheduled, time.perf_counter() - began))
        app.build_rows = timed
        stalls = []
        state = {'last': None, 'stop': False}

        def tick():
            now = time.perf_counter()
            if state['last'] is not None:
                stalls.append(now - state['last'] - 0.02)
            state['last'] = now
            if not state['stop']:
                app.after(20, tick)
        app.after(20, tick)
        began = time.perf_counter()
        app.populate_queue([f'https://example.org/long/{number}' for number in range(300)])
        self.assertEqual(len(app.rows), ui_download.ROW_FIRST)
        self.assertTrue(pump(app, lambda: not app.row_queue and app.row_after is None, 60))
        app.update_idletasks()
        built = time.perf_counter() - began
        state['stop'] = True
        pump(app, timeout=0.1)
        self.assertEqual(len(app.rows), min(ui_download.ROW_LIMIT, app.row_cap()))
        self.assertEqual(ui_theme.full_text(app.row_footer), zh_t('download.footer.hidden', hidden=300 - len(app.rows)))
        # Every timed step stops after ROW_SLICE s (plus the row that was being built).
        self.assertTrue(all(scheduled for scheduled, _ in steps[1:]))
        _sandbox.write_report('polish-long-list.json', {
            'rows': len(app.rows), 'build_seconds': round(built, 3),
            'longest_stalls_ms': sorted((round(stall * 1000) for stall in stalls), reverse=True)[:8],
            'longest_steps_ms': sorted((round(seconds * 1000) for _, seconds in steps), reverse=True)[:8]})
        # Generous bounds (the machine may be busy); the measured numbers are in the report.
        self.assertLess(max(stalls), 2.0)
        self.assertLess(built, 20)

    def test_the_active_row_beyond_the_cap_is_named_in_the_header(self):
        app = self.make_app()
        links = [f'https://example.org/long/{number}' for number in range(120)]
        app.populate_queue(links)
        self.assertTrue(pump(app, lambda: not app.row_queue, 30))
        app.operation = 'download'
        app.handle('item', {'index': 100, 'total': 120, 'url': links[99]})
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.downloading_n', index=100, total=120) + ' · example.org/long/99')
        app.handle('title', '第一百首')
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.downloading_n', index=100, total=120) + ' · 第一百首')
        app.handle('item', {'index': 2, 'total': 120, 'url': links[1]})
        self.assertEqual(ui_theme.full_text(app.current_title), zh_t('download.title.downloading_n', index=2, total=120))


if __name__ == '__main__':
    unittest.main(verbosity=1)
