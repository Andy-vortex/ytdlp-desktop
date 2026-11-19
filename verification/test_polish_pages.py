"""Polish round of the collect and library pages (1.6.0, polish-collect-library).

Items: ui-daily-flow-5 (添加到下载列表, also while a download runs), ui-daily-flow-6 (history
status and name), ui-wording-consistency-4 and lead-review item 3 (a space between numbers or
Latin words and Chinese text), ui-wording-consistency-5 (the history note shows the reason of the
task row), review-e2e-4 (继续未完成 runs every (link, format) of a job).

Smoke apps (withdrawn), one live StudioApp at a time, light mode.  The collection worker is a fake
that never starts a process; the one download run uses the real Runner with a scripted _run_once
(no yt-dlp process, no network).
"""
import _sandbox
import gc
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

import customtkinter as ctk
import collection_ui
import engine
import formats
import library
import library_ui
import ui_theme
import updater
import urls
from collector import MAX_LINKS, CollectOptions
from engine import MODES
from studio import StudioApp

A = 'https://example.org/song.html'
B = 'https://example.org/other.html'
MP3 = MODES[3]
FLAC = MODES[2]


def no_network(*_args, **_kwargs):
    raise AssertionError('updater.check_latest must not be called in this test')


def no_clipboard(self, *_args, **_kwargs):
    raise tk.TclError('CLIPBOARD selection doesn\'t exist or form "STRING" not defined')


class FakeCollectionRunner:
    """Stands in for collector.CollectionRunner: run() waits until released and emits nothing."""
    instances = []

    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.release = threading.Event()
        FakeCollectionRunner.instances.append(self)

    def run(self, options):
        self.release.wait(20)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def links(count, prefix='https://media.example.org/v/'):
    return [{'url': f'{prefix}{number:03d}.mp4', 'title': f'第 {number} 集', 'kind': '媒体地址',
             'source': 'https://example.org/list'} for number in range(count)]


def page(items, url='https://example.org/list'):
    return {'url': url, 'title': '示例列表', 'text': '正文', 'markdown': '# 正文', 'links': items}


def pump(app, seconds=0.2, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


def texts_below(widget):
    """The full texts of every label and button below ``widget``."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, (ctk.CTkLabel, ctk.CTkButton)):
            try:
                found.append(str(ui_theme.full_text(child)))
            except Exception:
                found.append(str(child.cget('text')))
        found += texts_below(child)
    return found


class Base(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', no_network),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard),
                        mock.patch.object(collection_ui, 'CollectionRunner', FakeCollectionRunner)):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeCollectionRunner.instances = []
        self.addCleanup(lambda: [runner.release.set() for runner in FakeCollectionRunner.instances])

    def make_app(self):
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/polish',
                                          str(_sandbox.out('polish-out-' + self._testMethodName))))
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
        # (1.8.0 WP-6, MESSAGES.md 15 A12) the destroyed window's Tk objects (fonts) that sit in
        # reference cycles are collected here, on the window's thread: a collection that a Runner
        # thread of the next test triggers would call into the old Tcl interpreter and wait forever.
        gc.collect()

    @staticmethod
    def logged(app):
        lines = []
        original = app.append_log

        def capture(line):
            lines.append(str(line))
            original(line)
        app.append_log = capture
        return lines

    def history_row(self, app, job):
        app.refresh_library()
        return tuple(app.history_tree.full_values(job))


class CollectAddTests(Base):
    """ui-daily-flow-5."""

    def test_the_button_says_where_the_links_go_and_works_while_a_download_runs(self):
        app = self.make_app()
        lines = self.logged(app)
        app.urls.delete('1.0', 'end')
        app.handle('collect_page', page(links(3)))
        app.collect_tree.selection_set(['0', '2'])
        app.collection_selection_changed()
        self.assertEqual(app.collect_add.cget('text'), '添加到下载列表 (2)')
        # A download runs: the download page takes links (its button reads 加入队列), so this one does too.
        app.operation = 'download'
        app.set_busy(True)
        self.assertEqual(app.start_button.cget('text'), '加入队列')
        self.assertEqual(app.collect_add.cget('state'), 'normal')
        app.show_page('collect')
        app.add_collected()
        self.assertEqual(app.current_page, 'download')
        self.assertEqual(app.urls.get('1.0', 'end-1c').splitlines(),
                         ['https://media.example.org/v/000.mp4', 'https://media.example.org/v/002.mp4'])
        self.assertIn('采集结果已加入下载列表：新增 2 条，跳过重复 0 条。', lines)
        self.assertTrue(app.busy, 'nothing was started or stopped')
        # A collection (or an engine update) runs: the download box takes no links then.
        app.set_busy(False)
        app.operation = 'collect'
        app.set_busy(True)
        app.collect_tree.selection_set(['1'])
        app.collection_selection_changed()
        self.assertEqual(app.collect_add.cget('state'), 'disabled')
        before = app.urls.get('1.0', 'end-1c')
        app.add_collected()
        app.queue_collected_items(app.selected_collection_links())
        self.assertEqual(app.urls.get('1.0', 'end-1c'), before)
        app.set_busy(False)
        app.operation = None
        self.assertEqual(app.collect_add.cget('state'), 'normal')

    def test_no_page_says_加入下载队列_any_more(self):
        app = self.make_app()
        for name in ('collect', 'library'):
            found = [text for text in texts_below(app.pages[name]) if '加入下载队列' in text]
            self.assertEqual(found, [], name)
        self.assertIn(collection_ui.SUBTITLE, texts_below(app.pages['collect']))
        self.assertIn('添加到下载列表', texts_below(app.pages['library']))


class HistoryTests(Base):
    """ui-daily-flow-6, ui-wording-consistency-5."""

    def test_a_download_job_without_any_success_reads_unfinished(self):
        app = self.make_app()
        store = app.library
        job = store.create_job('download', '一首歌', {'urls': [A], 'output': 'x', 'mode': MP3})
        store.download_event(job, 'done', {'success': 0, 'failed': 1, 'cancelled': False, 'files': []})
        self.assertEqual(store.job(job)['status'], 'partial', 'the stored status stays (1.5.1 reads it)')
        self.assertEqual(self.history_row(app, job)[2], '未完成')
        store.change_job(job, completed=[A])
        self.assertEqual(self.history_row(app, job)[2], '部分完成')
        collect = store.create_job('collect', '列表', {'urls': [A]})
        store.change_job(collect, status='partial')
        self.assertEqual(self.history_row(app, collect)[2], '部分完成')
        for status, word in (('completed', '已完成'), ('interrupted', '已中断')):
            store.change_job(job, status=status, completed=[])
            self.assertEqual(self.history_row(app, job)[2], word)

    def test_a_job_without_a_title_is_named_after_its_first_link(self):
        app = self.make_app()
        store = app.library
        one = store.create_job('download', f'1条 · {MP3}', {'urls': [A], 'output': 'x', 'mode': MP3})
        two = store.create_job('download', f'2 条 · {MP3}', {'urls': [A, B], 'output': 'x', 'mode': MP3})
        self.assertEqual(self.history_row(app, one)[1], urls.display(A))
        self.assertEqual(self.history_row(app, two)[1], f'{urls.display(A)} 等 2 条')
        # A title from the engine wins; the stored placeholder itself is never rewritten.
        store.download_event(two, 'title', '晴天')
        self.assertEqual(self.history_row(app, two)[1], '晴天 等 2 条')
        self.assertEqual(store.job(one)['title'], f'1条 · {MP3}')
        # Other names stay as they are.
        named = store.create_job('download', '我的歌单', {'urls': [A], 'output': 'x', 'mode': MP3})
        collect = store.create_job('collect', '3条 · 列表', {'urls': [A]})
        # (1.8.0) a collect job's title that reads like the placeholder is user data, shown as it is
        with _sandbox.fixture_texts('a collect job titled like the 1.5.1 placeholder: user data'):
            self.assertEqual(self.history_row(app, named)[1], '我的歌单')
            self.assertEqual(self.history_row(app, collect)[1], '3条 · 列表')

    def test_the_note_shows_the_reason_of_the_task_row(self):
        app = self.make_app()
        store = app.library
        job = store.create_job('download', '一首歌', {'urls': [A], 'output': 'x', 'mode': MP3})
        line = 'ERROR: [youtube] abcdefghijk: Signature solving failed: Some yt-dlp text'
        store.download_event(job, 'item', {'index': 1, 'total': 1, 'url': A})
        store.download_event(job, 'item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0,
                                                'bot_check': False, 'url': A, 'error': line,
                                                'reason': engine.STALE_REASON, 'stale_engine': True})
        app.refresh_library()
        app.history_tree.selection_set(job)
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), f'0 条下载成功 · 最近错误：{engine.STALE_REASON}')
        data = store.job(job)['data']
        self.assertEqual(data['errors'], [line], 'the raw line is kept')
        self.assertEqual(data['last_reason'], {'error': line, 'reason': engine.STALE_REASON})
        # A reason the engine words itself (no pattern of explain_error finds it from the line).
        store.download_event(job, 'item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0,
                                                'bot_check': False, 'url': A, 'error': 'ERROR: something odd',
                                                'reason': '下载未完成（退出码 2）'})
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), '0 条下载成功 · 最近错误：下载未完成（退出码 2）')
        # A record without the stored reason (1.5.1, or before this round): explain_error of the line.
        store.change_job(job, last_reason=None, errors=[line])
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), f'0 条下载成功 · 最近错误：{engine.STALE_REASON}')
        store.change_job(job, errors=["ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies"])
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), f'0 条下载成功 · 最近错误：{engine.BOT_CHECK_REASON}')
        # Downloads count per (link, format).
        store.extend_job(job, [A], {A: engine.Options(_sandbox.out('polish-flac'), FLAC).to_dict()})
        flac_key = library._format_of({'mode': FLAC})
        mp3_key = library._format_of({'mode': MP3})
        store.change_job(job, completed=[A], completed_variants=[[A, mp3_key], [A, flac_key]], errors=[])
        app.history_selection()
        self.assertEqual(ui_theme.full_text(app.history_note), '2 条下载成功')


class SpacingTests(Base):
    """ui-wording-consistency-4, lead-review item 3: '3 条', '每 15 分钟', 'ID'/'TXT' with spaces."""

    def test_collect_page_numbers(self):
        app = self.make_app()
        lines = self.logged(app)
        job = app.library.create_job('collect', '列表', asdict(CollectOptions(['https://example.org/list']).validate()))
        app.collection_job = job
        app.operation = 'collect'
        app.set_busy(True)
        found = dict(page(links(3)), text_truncated=True)
        app.library.collection_event(job, 'collect_page', dict(found, _checkpoint=None))     # what the worker stores
        app.handle('collect_page', found)
        self.assertEqual(ui_theme.full_text(app.collect_status), '已采集 1 页，发现 3 条链接')
        app.handle('collect_error', '网站返回 HTTP 503')
        app.handle('collect_done', {'cancelled': False})
        self.assertEqual(ui_theme.full_text(app.collect_status), '采集结束：1 页 / 3 条链接；1 个错误，详情见任务日志')
        self.assertIn('本页正文超过 40 万字符，已截断；导出文件会注明。', lines)
        record = app.library.job(job)
        app.load_collection_record(record)
        self.assertEqual(ui_theme.full_text(app.collect_status), '历史结果：1 页，3 条链接')
        app.collected_links = {f'https://x.example.org/{n}': links(1)[0] for n in range(MAX_LINKS)}
        app.collection_errors = 0
        app.collection_job = None
        app.operation = 'collect'
        app.set_busy(True)
        app.handle('collect_done', {'cancelled': True})
        self.assertTrue(ui_theme.full_text(app.collect_status).endswith(f'；已达 {MAX_LINKS} 条上限，请分批采集'))

    def test_library_page_numbers(self):
        app = self.make_app()
        lines = self.logged(app)
        self.assertEqual(list(library_ui.INTERVALS), ['仅手动', '每 15 分钟', '每小时', '每天'])
        self.assertEqual(list(library_ui.INTERVALS.values()), [0, 900, 3600, 86400], 'the stored values stay')
        options = CollectOptions(['https://example.org/feed']).validate()
        key = app.library.subscription('订阅', asdict(options), 900, True, True)
        app.library.finish_subscription(key, [page(links(3))], True)
        app.refresh_library()
        self.assertEqual(app.subscription_tree.item(key, 'values')[1], '每 15 分钟')
        app.subscription_tree.selection_set(key)
        app.subscription_selection()
        self.assertEqual(ui_theme.full_text(app.subscription_note),
                         '最近检查新增 3 条 · 待处理 3 条 · 下载成功或忽略后移出；可多选后添加或忽略。')
        # The interval chosen in the menu is still stored as seconds.
        app.subscription_interval.set('每 15 分钟')
        app.change_interval()
        self.assertEqual(next(row for row in app.library.subscriptions() if row['id'] == key)['interval'], 900)
        app.collected_pages = [page(links(1))]
        app.archive_root.set(str(_sandbox.out('polish-archive')))
        app.archive_pages()
        self.assertIn('已归档当前 1 个页面正文。', lines)
        self.assertIn('按来源站点与稳定 ID 分目录：媒体 / 网页正文 TXT 与 MD / 资料信息 / 转写稿。旧文件不会被移动。',
                      texts_below(app.pages['library']))
        # A manual subscription check that ends: its log line.
        row = next(item for item in app.library.subscriptions() if item['id'] == key)
        app.check_subscription(row)
        FakeCollectionRunner.instances[-1].release.set()
        with mock.patch.object(app.library, 'finish_subscription', return_value=4):
            app.handle('collect_done', {'cancelled': False})
        self.assertIn('订阅本轮新增 4 条；未下载的新增内容保留在资料管理中。', lines)


class ResumeTests(Base):
    """review-e2e-4: 继续未完成 uses library.job_downloads, so every (link, format) of a job runs."""

    def job_with_two_formats(self, store, done=()):
        mp3 = engine.Options(_sandbox.out('polish-resume-mp3'), MP3)
        flac = engine.Options(_sandbox.out('polish-resume-flac'), FLAC)
        job = store.create_job('download', f'2条 · {MP3}', {**mp3.to_dict(), 'urls': [A, B]})
        store.extend_job(job, [A], {A: flac.to_dict()})        # A again, as FLAC into another folder
        store.change_job(job, status='partial', completed_variants=[list(pair) for pair in done],
                         completed=sorted({url for url, _key in done}))
        return job, mp3, flac

    def test_the_plan_takes_one_download_per_link_first(self):
        mp3_key, flac_key = library._format_of({'mode': MP3}), library._format_of({'mode': FLAC})
        data = {'options': {'urls': [A, B], 'output': 'x', 'mode': MP3,
                            'variants': [{'url': A, 'format': flac_key, 'options': {'output': 'y', 'mode': FLAC}}]},
                'completed': [], 'completed_variants': []}
        first, rest = library_ui.resume_plan(library.job_downloads(data))
        self.assertEqual([(e['url'], e['format']) for e in first], [(A, mp3_key), (B, mp3_key)])
        self.assertEqual([(e['url'], e['format']) for e in rest], [(A, flac_key)])
        # The MP3 of A is done: the FLAC of A starts with the job (job order: links, then variants).
        data['completed'], data['completed_variants'] = [A], [[A, mp3_key]]
        first, rest = library_ui.resume_plan(library.job_downloads(data))
        self.assertEqual([(e['url'], e['format']) for e in first], [(B, mp3_key), (A, flac_key)])
        self.assertEqual(rest, [])

    def test_resume_runs_both_formats_of_a_link(self):
        """The real Runner (scripted _run_once): A as MP3 and B start, A as FLAC joins the queue."""
        app = self.make_app()
        lines = self.logged(app)
        app.skip_downloaded.set(False)
        store = app.library
        old, mp3, flac = self.job_with_two_formats(store)
        app.refresh_library()
        app.history_tree.selection_set(old)
        app.history_selection()
        ran, gate = [], threading.Event()

        def run_once(runner, command, index, total, files):
            item = runner._current
            ran.append((item.url, item.format, str(item.options.output)))
            if len(ran) == 1:
                gate.wait(10)       # the run stays open until the other format has joined
            return 0, False, None

        engine.Runner.reset_pacing()
        self.addCleanup(engine.Runner.reset_pacing)
        with mock.patch.object(engine, 'PACE_SECONDS', (0, 0)), \
                mock.patch.object(engine.Runner, '_run_once', run_once), \
                mock.patch.object(engine.Runner, '_wait', lambda *args, **kwargs: True):
            app.resume_history()
            self.assertTrue(app.busy)
            new = app.download_job
            self.assertNotEqual(new, old)
            self.assertTrue(pump(app, 10, until=lambda: len(app.row_model) == 3), 'the FLAC of A did not join')
            gate.set()
            self.assertTrue(pump(app, 20, until=lambda: not app.busy), 'the run did not end')
        mp3_key, flac_key = library._format_of(mp3.to_dict()), library._format_of(flac.to_dict())
        self.assertEqual(ran, [(A, mp3_key, str(mp3.output)), (B, mp3_key, str(mp3.output)),
                               (A, flac_key, str(flac.output))])
        self.assertIn(library_ui.RESUME_LATER_LOG.format(count=1), lines)
        self.assertEqual([model['url'] for model in app.row_model], [A, B, A])
        record = store.job(new)
        self.assertEqual([(e['url'], e['format'], e['done']) for e in library.job_downloads(record['data'])],
                         [(A, mp3_key, True), (B, mp3_key, True), (A, flac_key, True)])
        self.assertEqual(record['status'], 'completed')
        # The record that was resumed is left as it was.
        self.assertEqual(store.job(old)['status'], 'partial')

    def test_the_other_formats_are_recorded_and_wait_for_the_queue(self):
        app = self.make_app()
        store = app.library
        new = store.create_job('download', '新任务', {**engine.Options(_sandbox.out('polish-n'), MP3).to_dict(),
                                                    'urls': [A]})
        flac = engine.Options(_sandbox.out('polish-f'), FLAC)
        accepted = []
        runner = SimpleNamespace(opened=False, closed=False, cancelled=threading.Event(),
                                 enqueue=lambda urls, options: accepted.append((urls, options)) or list(urls))
        app.runner, app.download_job, app.operation, app.busy = runner, new, 'download', True
        app.resume_later([(A, flac)])
        variants = store.job(new)['data']['options'].get('variants')
        self.assertEqual([(v['url'], v['format']) for v in variants], [(A, library._format_of(flac.to_dict()))],
                         'written into the new record at once')
        pump(app, 0.3)
        self.assertEqual(accepted, [], 'nothing is handed over before the queue is open')
        runner.opened = True
        pump(app, 0.3, until=lambda: accepted)
        self.assertEqual(accepted, [([A], flac)])
        app.busy = False

    def test_a_stop_leaves_the_other_formats_for_later(self):
        app = self.make_app()
        lines = self.logged(app)
        runner = SimpleNamespace(opened=True, closed=False, cancelled=threading.Event(),
                                 enqueue=lambda *args: self.fail('nothing joins a stopped run'))
        runner.cancelled.set()
        app.runner, app.download_job, app.operation, app.busy = runner, None, 'download', True
        flac = engine.Options(_sandbox.out('polish-f'), FLAC)
        with mock.patch.object(app, 'launch') as launch:
            app.resume_later([(A, flac)])
            pump(app, 0.2)
        launch.assert_not_called()
        self.assertIn(library_ui.RESUME_DROPPED_LOG.format(count=1), lines)
        app.busy = False

    def test_a_run_that_ended_first_is_followed_by_the_other_formats(self):
        app = self.make_app()
        runner = SimpleNamespace(opened=True, closed=True, cancelled=threading.Event(),
                                 enqueue=lambda *args: [])
        app.runner, app.download_job, app.busy = runner, None, False
        flac = engine.Options(_sandbox.out('polish-f'), FLAC)
        mp3 = engine.Options(_sandbox.out('polish-m'), MP3)
        wav = engine.Options(_sandbox.out('polish-w'), MODES[1])
        with mock.patch.object(app, 'launch', return_value=False) as launch:
            app._resume_later_step(runner, [(A, flac), (B, mp3), (A, wav)])
        launch.assert_called_once()
        urls_, options, overrides = launch.call_args.args
        self.assertEqual((urls_, options, overrides), ([A, B], flac, {B: mp3}))
        # Started: the rest (A as WAV) follows the same way.
        with mock.patch.object(app, 'launch', return_value=True) as launch, \
                mock.patch.object(app, 'resume_later') as later:
            app._resume_later_step(runner, [(A, flac), (A, wav)])
        self.assertEqual(launch.call_args.args, ([A], flac, {}))
        later.assert_called_once_with([(A, wav)])


if __name__ == '__main__':
    unittest.main()
