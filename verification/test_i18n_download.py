"""1.7.0 language adaptation, WP-L1-A: the shell, the download page and the log page (I18N.md 13).

    python -X utf8 -B test_i18n_download.py

Unit tests of the L1-A rewrites: the 清除未完成 header rendered from the job's numbers (8.7),
classify with English lines (8.6), append_log localization and the proxy mask (8.6; since 1.8.0
core lines arrive as messages and are shown with messages.show, MESSAGES.md 6.2 / 8.3), the start
button compared with the running language's texts (8.7), the unfinished-row detail and the
other 9.3 sites, the stats line that gives way to the task title (10.5), the sidebar status and
the window title (8.5, 10.4); a quick English check that the download page, the log page and
the sidebar show no Chinese except user data; set_busy and poll byte-identical to the F state
(H3).  Smoke-mode windows (alpha 0, no frame), no network, no real engine.
"""
import _sandbox
import ast
import hashlib
import re
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import customtkinter as ctk
import engine
import formats
import i18n
import messages
import studio
import ui_download
import ui_log
import ui_theme
import updater
from studio import StudioApp

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
# StudioApp.set_busy and StudioApp.poll of the F state (build/v170/fix-merged): sha256 of their
# source text (ast.get_source_segment).  Protected blocks (H3): never edited by the language work.
PROTECTED = {'set_busy': '2947d2da0755e1c42236e6e05225e7660a900cb42f4c5b7b85c49f6af87dead7',
             'poll': 'ba3f5fb76cfd49aa2045eea313f4828b02a34abab7e2404d57a783f1601313cb'}


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None


class FakeRunner:
    """Stands in for engine.Runner: download() returns at once (nothing is downloaded)."""

    def __init__(self, emit, library=None):
        self.emit, self.library = emit, library
        self.origins = {}
        self.cancelled = threading.Event()
        self.closed = False

    def download(self, urls, options, overrides=None):
        self.urls = list(urls)

    def enqueue(self, urls, options=None, overrides=None):
        return list(urls)

    def cancel(self):
        self.cancelled.set()


def entry(number, reason='failed', format_key='audio_mp3', mode=None):
    """A stored unfinished entry (as engine.load_unfinished returns it)."""
    options = {'mode': mode} if mode else {}
    return {'url': f'https://example.org/item-{number}', 'key': f'example.org/item-{number}', 'format': format_key,
            'reason': reason, 'options': options, 'time': number}


class AppCase(unittest.TestCase):
    """Builds smoke-mode windows in a language; the language stays set until the test ends."""

    def make_app(self, lang='en', size='1000x740', visible=True):
        # A process never holds two live StudioApp instances (a failed subtest may leave one).
        for earlier in getattr(self, '_apps', []):
            self.close(earlier)
        self._apps = []
        context = _sandbox.language(lang)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/smoke', str(_sandbox.out('i18n-download'))))
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

    def logged(self, app):
        """Every line the log page got (shown or still buffered)."""
        return app.logs.get('1.0', 'end-1c').splitlines() + list(app.log_buffer)

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------------------- 8.7
class HeaderRewriteTests(AppCase):
    """清除未完成 renders the header from the job's numbers (download.title.finished_cleared)."""

    def clear(self, app):
        app.unfinished = [entry(1)]
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
            app.clear_unfinished()
        self.assertEqual(app.unfinished, [])
        return ui_theme.full_text(app.current_title)

    def finish(self, app, success, unfinished):
        links = [f'https://example.org/job-{number}' for number in range(success + unfinished)]
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        for number in range(success):
            app.handle('item', {'index': number + 1, 'total': len(links), 'url': links[number]})
            app.handle('item_done', {'index': number + 1, 'success': True, 'cancelled': False, 'files': 1,
                                     'bot_check': False})
        for number in range(success, len(links)):
            app.handle('item', {'index': number + 1, 'total': len(links), 'url': links[number]})
            app.handle('item_done', {'index': number + 1, 'success': False, 'cancelled': False, 'files': 0,
                                     'bot_check': False, 'reason': engine.NETWORK_REASON})
        app.handle('done', {'success': success, 'failed': unfinished, 'files': [], 'cancelled': False,
                            'pending_urls': links[success:]})

    def test_english_job_end_then_clear(self):
        app = self.make_app('en')
        self.finish(app, 1, 1)
        self.assertEqual(app.job_summary, (1, 1))
        self.assertEqual(ui_theme.full_text(app.current_title), 'Task finished · Succeeded: 1 · Unfinished: 1')
        self.assertEqual(self.clear(app), 'Task finished · Succeeded: 1')
        self.assertEqual(ui_theme.full_text(app.metrics), i18n.t('download.metrics.cleared'))

    def test_chinese_job_end_then_clear_is_the_f_state_text(self):
        app = self.make_app('zh')
        self.finish(app, 2, 1)
        self.assertEqual(ui_theme.full_text(app.current_title), '任务结束 · 2 条成功，1 条未完成')
        self.assertEqual(self.clear(app), '任务结束 · 2 条成功')
        self.assertEqual(ui_theme.full_text(app.metrics), ui_download.CLEARED_METRICS)

    def test_only_the_unfinished_header_is_rewritten_in_both_languages(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                self.addCleanup(self.close, app)
                app.job_summary = None          # a header written without on_done: read with the template
                for title, expected in (
                        (i18n.t('download.title.finished_unfinished', success=3, unfinished=2),
                         i18n.t('download.title.finished_cleared', success=3)),
                        (i18n.t('download.title.finished_unfinished', success=12, unfinished=345),
                         i18n.t('download.title.finished_cleared', success=12)),
                        (i18n.t('download.title.stopped'), i18n.t('download.title.stopped')),
                        (i18n.t('download.title.bot'), i18n.t('download.title.bot')),
                        (i18n.t('download.title.done', success=2), i18n.t('download.title.done', success=2)),
                        (i18n.t('download.title.finished_cleared', success=2),
                         i18n.t('download.title.finished_cleared', success=2))):
                    app.current_title.configure(text=title)
                    self.assertEqual(self.clear(app), expected, title)
                self.close(app)

    def test_a_media_title_is_never_cut(self):
        app = self.make_app('en', visible=False)
        for title in ('Task finished · Succeeded: x · Unfinished: 1', 'My song · Unfinished: 2',
                      '任务结束 · 1 条成功，1 条未完成'):
            app.current_title.configure(text=title)
            self.assertEqual(self.clear(app), title)


# ---------------------------------------------------------------------------------------- 8.6
def classify_160(line):
    """ui_log.classify of 1.6.0 / the F state (the reference for Chinese lines)."""
    if line.startswith('ERROR'):
        return 'error'
    if line.startswith('WARNING'):
        return 'warn'
    if re.match(r'^\[[\w:]+\]', line) or 'Deleting original file' in line:
        return 'dim'
    if line.startswith(ui_log._ERROR_STARTS) or (line.startswith('任务结束') and '未完成' in line):
        return 'error'
    if line.startswith(('文件已保存', '下载完成')):
        return 'ok'
    return 'app'


class ClassifyTests(unittest.TestCase):
    ENGLISH = [
        ('ERROR: [youtube] abc: Video unavailable', 'error'),
        ('WARNING: [youtube] abc: Some formats may be missing', 'warn'),
        ('[youtube] abc: Downloading webpage', 'dim'),
        ('[ExtractAudio] Deleting original file a.webm', 'dim'),
        ('Task finished · Succeeded: 1 · Unfinished: 1', 'error'),
        ('Task finished · Succeeded: 3', 'app'),
        ('Task started · Links: 3 · MP3 · 320 kbps · Save to D:\\Music', 'app'),
        ('File saved: D:\\Music\\a.m4a', 'ok'),
        ('Files saved: double-click a task to play it; Ctrl+E shows it in its folder.', 'ok'),
        ('Download complete · Succeeded: 2', 'ok'),
        ('Download complete · Succeeded: 2 · Skipped: 1', 'ok'),
        ('Error: the page is gone', 'error'),
        ('Interface error: ValueError: x', 'error'),
        ('Ready: yt-dlp 2026.11.03; FFmpeg, ffprobe and Deno are built in.', 'app'),
        ('Link 2 / 8: https://example.org/a', 'app'),
    ]

    def test_english_lines(self):
        for lang in ('en', 'zh'):        # classify does not depend on the running language
            with _sandbox.language(lang):
                for line, tag in self.ENGLISH:
                    self.assertEqual(ui_log.classify(line), tag, line)

    def test_every_tagged_key_in_english(self):
        with _sandbox.language('en'):
            for key, tag in __import__('i18n_log').TAGS.items():
                text = i18n.t(key, **{name: '1' for name in re.findall(r'\{(\w+)\}', i18n.CATALOG['en'][key])})
                self.assertEqual(ui_log.classify(text), tag, f'{key}: {text!r}')

    def test_chinese_lines_keep_the_1_6_0_rule(self):
        lines = ['此链接未完成（退出码 1），继续处理队列中的下一项。', '任务结束 · 1 条成功，1 条未完成', '任务结束 · 1 条成功',
                 '文件已保存：a.m4a', '下载完成 · 2 条成功', '就绪：yt-dlp 2026.11.03；FFmpeg、ffprobe 和 Deno 已内置。',
                 '错误：页面不存在', '界面错误：ValueError: x', '设置未保存：无法写入 settings.json，详情见日志文件。',
                 '[youtube] abc: Downloading webpage', 'ERROR: x', 'WARNING: y', '开始任务：3 条链接']
        for key in __import__('i18n_log').TAGS:
            zh = i18n.CATALOG['zh'][key]
            lines.append(zh.format(**{name: '1' for name in re.findall(r'\{(\w+)\}', zh)}))
        for line in lines:
            self.assertEqual(ui_log.classify(line), classify_160(line), line)


# ---------------------------------------------------------------------------------------- 8.6, 8.7, 9.3
class ShellAndLogTests(AppCase):

    def test_append_log_localizes_and_masks_the_proxy(self):
        for lang, mask in (('en', '[proxy]'), ('zh', '[代理]')):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                written = []
                app.logger = mock.Mock(info=written.append)
                app.log_buffer.clear()
                app.proxy.set('http://127.0.0.1:8899')
                # (1.8.0) the core line is the message the engine emits (its value is the 1.7.0 text)
                core = engine.msg('core.engine.item_unfinished', code=1)
                self.assertEqual(core, '此链接未完成（退出码 1），继续处理队列中的下一项。')
                raw = '[download]  12.5% of 3.00MiB at 1.2MiB/s ETA 00:02'
                for line in (core, raw, '代理 http://127.0.0.1:8899 无响应', engine.NETWORK_REASON):
                    app.append_log(line)
                expected = [messages.show(core), raw, '代理 ' + mask + ' 无响应', messages.show(engine.NETWORK_REASON)]
                self.assertEqual(app.log_buffer, expected)
                self.assertEqual(written, expected, 'download.log holds what the owner saw')
                if lang == 'en':
                    self.assertTrue(app.log_buffer[0].startswith('This link did not finish (exit code 1)'))
                    self.assertEqual(app.log_buffer[3], "Can't reach the site; check your network or VPN")
                else:
                    self.assertEqual(app.log_buffer[0], core)
                app.flush_log()
                shown = app.logs.get('1.0', 'end-1c')
                self.assertIn(expected[0], shown)
                start = app.logs.search(expected[0], '1.0')
                self.assertIn('error', app.logs.tag_names(start))
                self.close(app)

    def test_start_button_follows_the_running_language(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                self.assertEqual(app.start_button.cget('text').strip(), i18n.t('download.start'))
                width = app.start_button.cget('width')
                for text in (i18n.t('download.start'), i18n.t('download.start.queue')):
                    self.assertGreaterEqual(width, ui_theme.fitted_width(app, text) + ui_download.START_ICON
                                            + ui_download.IMAGE_SPACING)
                app.busy, app.operation = True, 'download'
                app.refresh_shell()
                self.assertEqual(app.start_button.cget('text'), i18n.t('download.start.queue'))
                self.assertEqual(app.start_button.cget('width'), width, 'the start button keeps its width')
                app.busy, app.operation = False, None
                app.refresh_shell()
                self.assertEqual(app.start_button.cget('text'), i18n.t('download.start'))
                self.close(app)
        self.assertEqual(i18n.t('download.start'), '开始下载')

    def test_sidebar_status_window_title_and_badges(self):
        # (the settings page writes the badge again with its own engine check: L1-B's code, not run here)
        with mock.patch.object(studio, 'engine_version', return_value=updater.BUILTIN),                 mock.patch.object(StudioApp, 'settings_startup', lambda self: None),                 mock.patch.object(StudioApp, 'refresh_engine_labels', lambda self: None):
            app = self.make_app('en')
        self.assertEqual(app.title(), 'YT-DLP Downloader')
        self.assertEqual(app.engine_badge.cget('text'), 'yt-dlp built-in')
        self.assertTrue(self.logged(app)[0].startswith('Ready: yt-dlp built-in;'), self.logged(app)[:2])
        app.show_app_badge(True)
        self.assertEqual(app.app_badge.cget('text'), f'v{engine.APP_VERSION} · new engine')
        app.show_app_badge(False)
        self.assertEqual(app.app_badge.cget('text'), f'v{engine.APP_VERSION}')
        links = [f'https://example.org/{number}' for number in range(456)]
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 123, 'total': 456, 'url': links[122]})
        pump(app, timeout=0.2)
        self.assertEqual(ui_theme.full_text(app.side_status), 'Item 123 / 456')
        self.assertEqual(app.side_status.cget('text'), 'Item 123 / 456', 'fits the sidebar without a cut')
        self.assertTrue(app.title().startswith('123/456 — YT-DLP Downloader'), app.title())
        self.assertTrue(ui_theme.full_text(app.current_title).startswith('Downloading 123 / 456'))
        app.side_status.configure(text='A status that is much too long for the sidebar of the window')
        pump(app, timeout=0.2)
        self.assertTrue(app.side_status.cget('text').endswith('…'), 'the status is cut with an ellipsis')
        app.set_window_title(i18n.t('window.title.unfinished', unfinished=2))
        self.assertEqual(app.title(), '✗ Unfinished: 2 — YT-DLP Downloader')

    def test_unfinished_rows_show_the_reason_and_format_in_the_running_language(self):
        entries = [entry(1, 'blocked', 'audio_mp3'), entry(2, 'failed', 'no-such-format', mode='音频 · FLAC'),
                   entry(3, 'stopped', 'video_mp4')]
        expected = {'en': ['YouTube check · MP3 · 320 kbps', 'Failed last time · Audio · FLAC',
                           'Stopped · MP4 · most compatible (H.264)'],
                    'zh': ['YouTube 验证 · MP3 · 320 kbps', '上次失败 · 音频 · FLAC', '已停止 · MP4 · 兼容优先（H.264）']}
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                app.unfinished = list(entries)
                app.show_unfinished_rows()
                self.assertEqual([model['detail'] for model in app.row_model], expected[lang])
                self.assertEqual([model['entry']['options'].get('mode') for model in app.row_model][1], '音频 · FLAC',
                                 'the entry keeps its stored mode')
                self.assertEqual(app.row_model[0]['state'], 'unfinished')
                self.assertEqual(app.rows[0]['badge'].cget('text'), i18n.label('row_state', 'unfinished'))
                self.close(app)

    def test_task_start_log_line_and_stored_job_title(self):
        app = self.make_app('en', visible=False)
        mode = formats.by_id('audio_mp3').mode
        app.format.set(formats.by_id('audio_mp3').label)
        app.on_format()
        self.assertEqual(app.mode.get(), mode, 'the stored mode stays the canonical Chinese string')
        app.urls.insert('1.0', 'https://example.org/a\nhttps://example.org/b')
        created = []
        real_create = app.library.create_job

        def create(kind, title, data):
            created.append(title)
            return real_create(kind, title, data)
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(app.library, 'create_job', side_effect=create):
            app.start()
            pump(app, lambda: not app.busy, timeout=5)
        started = [line for line in self.logged(app) if line.startswith('Task started')]
        self.assertEqual(len(started), 1, self.logged(app))
        self.assertTrue(started[0].startswith('Task started · Links: 2 · MP3 · 320 kbps · Save to '), started[0])
        self.assertEqual(created, [f'2 条 · {mode}'], 'the history keeps the canonical title (H2)')
        # (1.8.0, D-F45) the title is a message: its key travels with the stored zh text; en shows it in English
        self.assertEqual((created[0].key, created[0].params['count'], created[0].params['mode']),
                         ('value.job_title.placeholder', 2, mode))
        self.assertIsInstance(created[0].params['mode'], messages.Value)
        self.assertEqual(messages.show(created[0]), i18n.t('value.job_title.placeholder', count=2,
                                                           mode=i18n.label('mode', mode)))

    def test_row_reasons_are_localized_and_the_engine_error_stays_raw(self):
        app = self.make_app('en', visible=False)
        links = ['https://example.org/a', 'https://example.org/b', 'https://example.org/c']
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        raw = 'ERROR: [generic] a: Unable to download webpage: HTTP Error 404'
        app.handle('item', {'index': 1, 'total': 3, 'url': links[0]})
        app.handle('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.NETWORK_REASON, 'error': raw})
        app.handle('item', {'index': 2, 'total': 3, 'url': links[1]})
        app.handle('item_done', {'index': 2, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.STALE_REASON, 'stale_engine': True})
        app.handle('item', {'index': 3, 'total': 3, 'url': links[2]})
        app.handle('waiting', {'reason': engine.msg('core.wait.pace'), 'seconds': 90})
        app.render_wait()
        self.assertEqual(app.row_model[0]['detail'], "Can't reach the site; check your network or VPN")
        self.assertEqual(app.row_model[0]['error'], raw)
        self.assertEqual(app.row_reason(0), "Can't reach the site; check your network or VPN\n\nEngine error as reported:\n"
                         + raw)
        self.assertEqual(app.row_model[1]['detail'],
                         i18n.t('download.detail.stale_update', reason=i18n.t('core.reason.stale')))
        self.assertTrue(app.row_model[1]['detail'].endswith('; you can update it in Settings'))
        self.assertTrue(ui_theme.full_text(app.metrics).startswith('Download pause · 01:30 left'),
                        ui_theme.full_text(app.metrics))
        self.assertTrue(ui_theme.full_text(app.side_status).startswith('Waiting '))
        app.proxy.set('http://127.0.0.1:8899')
        app.job_options = (engine.Options(_sandbox.out('dl'), proxy='http://127.0.0.1:8899'), {})
        app.handle('item_done', {'index': 3, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.NETWORK_REASON})
        self.assertEqual(app.row_model[2]['detail'], 'Cannot reach the proxy or the site; check the proxy address')
        stored = ui_download.history_value('item_done', {'reason': engine.NETWORK_REASON}, True)
        self.assertEqual(stored['reason'], ui_download.PROXY_REASON, 'the history stores the canonical text')


# ---------------------------------------------------------------------------------------- 10.5
class StatsRoomTests(AppCase):
    """The running stats drop their unfinished part when the task title would get less than 160 px."""

    def scene(self, app, done, waiting, unfinished):
        links = [f'https://example.org/job-{number}' for number in range(done + 1 + waiting)]
        app.unfinished = [entry(number) for number in range(unfinished)]
        app.populate_queue(links)
        app.show_unfinished_tail()
        app.operation = 'download'
        app.set_busy(True)
        for number in range(done):
            app.row_model[number]['state'] = 'done'
        app.update_row(done, state='active', detail='')
        app.active_index = done
        app.current_title.configure(text=app._active_title(done + 1, len(links)))
        app.refresh_failed_button()
        app.refresh_stats()
        pump(app, timeout=0.4)
        scale = ctk.ScalingTracker.get_widget_scaling(app)
        return app.current_title.winfo_width() / scale

    def test_title_keeps_160_px(self):
        for lang in ('en', 'zh'):
            for done, waiting, unfinished in ((12, 37, 4), (120, 335, 123)):
                with self.subTest(lang=lang, counts=(done, waiting, unfinished)):
                    app = self.make_app(lang)
                    title = self.scene(app, done, waiting, unfinished)
                    shown = app.queue_stats.cget('text')
                    full, short = app.stats_texts
                    self.assertEqual(full, short + i18n.t('download.stats.running_unfinished', unfinished=unfinished))
                    self.assertIn(shown, (full, short))
                    self.assertGreaterEqual(title, ui_download.TITLE_MIN - 1, (lang, shown, title))
                    self.assertEqual(app.retry_button.cget('text'),
                                     i18n.t('download.retry_unfinished_n', count=unfinished))
                    if lang == 'zh':
                        self.assertEqual(shown, full, 'Chinese never drops the part at 1000x740')
                    if lang == 'en' and (done, unfinished) == (12, 4):
                        self.assertEqual(shown, '12 done · 1 active · 37 waiting')
                    self.close(app)

    def test_a_wider_window_shows_the_full_stats_again(self):
        app = self.make_app('en')
        self.scene(app, 12, 37, 4)
        self.assertEqual(app.queue_stats.cget('text'), app.stats_texts[1])
        app.geometry('1600x900+0+0')
        pump(app, lambda: app.queue_stats.cget('text') == app.stats_texts[0], timeout=2)
        self.assertEqual(app.queue_stats.cget('text'), '12 done · 1 active · 37 waiting · 4 unfinished')


class FinishedStatsRoomTests(AppCase):
    """(final fix round 1, ENV-1) A finished job's stats follow the room rule too: after a job that ended
    with the YouTube check, after 清除未完成 and while a collection or an update runs after it, the title
    keeps 160 px and the stop button ends inside the task card (1000x740)."""

    def inside(self, app):
        card = app.task_card
        edge = card.winfo_rootx() + card.winfo_width() - card._apply_widget_scaling(card.cget('border_width'))
        return app.stop_button.winfo_rootx() + app.stop_button.winfo_width() <= edge + 1

    def title_px(self, app):
        pump(app, timeout=0.4)
        return app.current_title.winfo_width() / ctk.ScalingTracker.get_widget_scaling(app)

    def test_finished_job_cleared_then_collecting(self):
        import snapshot_ui
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang)
                snapshot_ui._finished_bot_job(app)
                forms = app.final_stats_forms()
                self.assertEqual(forms[0], app.final_stats())
                self.assertEqual(forms[-1], i18n.t('download.stats.final_short', done=3, total=12))
                self.assertEqual(app.stats_texts, (forms[0], tuple(forms[1:])))
                scenes = [('ended', self.title_px(app), app.queue_stats.cget('text'), self.inside(app))]
                with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
                    app.clear_unfinished()
                scenes.append(('cleared', self.title_px(app), app.queue_stats.cget('text'), self.inside(app)))
                cleared_forms = app.final_stats_forms()
                self.assertTrue(cleared_forms[0].endswith(i18n.t('download.stats.final_cleared', cleared=8)))
                for operation in ('collect', 'update'):
                    app.operation = operation
                    app.set_busy(True)
                    scenes.append((operation, self.title_px(app), app.queue_stats.cget('text'), self.inside(app)))
                    self.assertEqual(app.stop_button.cget('text'), i18n.t(f'download.foreign.{operation}.stop'))
                    app.set_busy(False)
                for name, title, shown, inside in scenes:
                    self.assertGreaterEqual(title, ui_download.TITLE_MIN - 1, (lang, name, shown))
                    self.assertTrue(inside, (lang, name, 'the stop button ends inside the task card'))
                    self.assertIn(shown, app.final_stats_forms() if name != 'ended' else forms, (lang, name))
                if lang == 'zh':
                    # Chinese keeps the full line at 1000x740 (its 1.6.0 behaviour)
                    self.assertEqual([shown for _n, _t, shown, _i in scenes],
                                     [forms[0]] + [cleared_forms[0]] * 3)
                else:
                    self.assertNotEqual(scenes[2][2], cleared_forms[0], 'English drops the tails while collecting')
                app.geometry('1600x900+0+0')
                if lang == 'zh':
                    pump(app, lambda: app.queue_stats.cget('text') == app.stats_texts[0], timeout=2)
                    self.assertEqual(app.queue_stats.cget('text'), cleared_forms[0], 'a wide window shows it in full')
                else:
                    # (final fix round 3, ENV3-1) English: the whole title comes first.  The page content stops
                    # growing at about 1048 px of header, where the YouTube-check title and the full line do not
                    # fit together (13 px short): the title is shown whole beside the line without its tails.
                    pump(app, lambda: app.queue_stats.cget('text') == cleared_forms[1], timeout=2)
                    self.assertEqual(app.queue_stats.cget('text'), cleared_forms[1])
                    self.assertEqual(app.current_title.cget('text'), ui_theme.full_text(app.current_title))
                self.close(app)


# ---------------------------------------------------------------------------------------- 13 (A)
class EnglishPagesTests(AppCase):
    """No Chinese on the download page, the log page and the sidebar in English (user data aside)."""

    USER_DATA = ('https://example.org', 'D:\\', '/')

    def shown(self, widget):
        stack = [widget]
        while stack:
            current = stack.pop()
            stack.extend(current.winfo_children())
            for option in ('text', 'placeholder_text'):
                try:
                    value = current.cget(option)
                except (ValueError, tk.TclError, AttributeError, KeyError):
                    continue
                if isinstance(value, str) and value:
                    yield current, value
            if isinstance(current, ctk.CTkOptionMenu) or isinstance(current, ctk.CTkSegmentedButton):
                for value in current.cget('values') or ():
                    yield current, str(value)
            if isinstance(current, tk.Text):
                yield current, current.get('1.0', 'end-1c')

    def chinese(self, app):
        found = []
        roots = [app.pages['download'], app.pages['logs'], app.nav_buttons['download'].master]
        for root in roots:
            for widget, value in self.shown(root):
                if widget is app.urls._textbox or widget is getattr(app.output_entry, '_entry', None):
                    continue        # the link box and the save folder: user data
                if CJK.search(value):
                    found.append(f'{widget}: {value!r}')
        found += [f'title: {app.title()!r}'] if CJK.search(app.title()) else []
        return found

    def menu_labels(self, app, build):
        with mock.patch.object(app, '_popup'):
            build()
        menu = app.row_menu
        return [menu.entrycget(index, 'label') for index in range(menu.index('end') + 1)
                if menu.type(index) == 'command']

    def test_idle_running_and_finished_scenes(self):
        app = self.make_app('en')
        self.assertEqual(self.chinese(app), [], 'idle')
        app.unfinished = [entry(1, 'blocked'), entry(2, 'failed')]
        app.show_unfinished_rows()
        app.refresh_failed_button()
        links = [f'https://example.org/clip-{number}' for number in range(6)]
        app.urls.insert('1.0', 'https://www.youtube.com/watch?v=abc&list=PL123\nhttps://example.org/x')
        app.links_changed()
        app.populate_queue(links)
        app.show_unfinished_tail()
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 1, 'total': 6, 'url': links[0]})
        app.handle('title', 'A song title')
        app.handle('progress', {'fraction': .42, 'downloaded': 1.5 * 2 ** 20, 'total': 3.6 * 2 ** 20,
                                'speed': 507.8 * 1024, 'eta': 4, 'status': 'downloading'})
        app.handle('log', '[ExtractAudio] Destination: a.m4a')
        app.handle('item_done', {'index': 1, 'success': True, 'cancelled': False, 'files': 1, 'bot_check': False})
        app.handle('item', {'index': 2, 'total': 6, 'url': links[1]})
        app.handle('item_done', {'index': 2, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': True,
                                 'skipped': 'bot_check'})
        app.handle('item', {'index': 3, 'total': 6, 'url': links[2]})
        app.handle('item_done', {'index': 3, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                 'skipped': 'downloaded'})
        app.handle('item', {'index': 4, 'total': 6, 'url': links[3]})
        app.handle('log', engine.msg('core.engine.item_unfinished', code=1))
        app.handle('waiting', {'reason': engine.msg('core.wait.pace'), 'seconds': 30})
        app.render_wait()
        app.refresh_shell()
        app.flush_log()
        pump(app, timeout=0.3)
        self.assertEqual(self.chinese(app), [], 'running')
        self.assertTrue(all(CJK.search(label) is None for label in
                            self.menu_labels(app, lambda: app.row_context(mock.Mock(x_root=0, y_root=0), 1))))
        self.assertTrue(all(CJK.search(label) is None for label in
                            self.menu_labels(app, lambda: app._box_menu(mock.Mock(x_root=0, y_root=0)))))
        app.handle('done', {'success': 1, 'failed': 1, 'files': [], 'cancelled': False, 'bot_check': True,
                            'pending_urls': links[1:]})
        app.arm_bot_retry(1)
        app.flush_log()
        pump(app, timeout=0.3)
        self.assertEqual(self.chinese(app), [], 'ended with the YouTube check')
        self.assertEqual(ui_theme.full_text(app.metrics), i18n.t('download.metrics.bot'))
        for kind in ('collect', 'update'):
            app.operation = kind
            app.set_busy(True)
            pump(app, timeout=0.1)
            self.assertEqual(self.chinese(app), [], kind)
            app.set_busy(False)
            app.operation = None
            app.refresh_shell()

    def test_labeled_menus_keep_the_stored_values(self):
        app = self.make_app('en', visible=False)
        values = app.format_menu.stored_values
        self.assertEqual(values, [formats.by_id(i).label for i in formats.MENU_ORDER])
        for value in values:
            label = i18n.label('format', value)
            self.assertIsNone(CJK.search(label), label)
            app.format_menu._dropdown_callback(label)
            self.assertEqual(app.format.get(), value)
            self.assertEqual(app.mode.get(), formats.by_label(value).mode)
        app.format.set(formats.by_id('video_mp4').label)
        app.on_format()
        app.quality_menu._dropdown_callback('Best')
        self.assertEqual(app.resolution.get(), engine.RESOLUTIONS[0])
        app.quality_menu._dropdown_callback('1080p')
        self.assertEqual(app.resolution.get(), '1080p')
        self.assertGreaterEqual(app.format_menu.cget('width'), 250)


# ------------------------------------------------------------------------------ final fix round 3
class FinalFixRound3Tests(AppCase):
    """(ENV3-1) English: a finished job's stats form is chosen by the title and a title that does not
    fit shows a shorter form whole; Chinese keeps its header.  (SWEEP-1, SWEEP-2) core error texts of
    the reason dialog and of the unfinished-list dialogs in the running language."""

    def finish(self, app, total, done, unfinished):
        links = [f'https://www.youtube.com/watch?v=r3{n:09d}' for n in range(total)]
        app.unfinished = []
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        for number, url in enumerate(links, 1):
            app.handle('item', {'index': number, 'total': total, 'url': url})
            app.handle('item_done', {'index': number, 'success': number <= done, 'cancelled': False,
                                     'files': int(number <= done), 'bot_check': False,
                                     **({} if number <= done else {'reason': engine.NETWORK_REASON})})
        app.handle('done', {'success': done, 'failed': unfinished, 'files': [], 'cancelled': False,
                            'pending_urls': links[done:], 'bot_check': False, 'archive_errors': []})
        app.unfinished = [{'url': url, 'key': 'youtube:' + url[-11:], 'format': 'audio_best', 'reason': 'failed',
                           'title': '', 'error': '', 'time': n, 'options': {}} for n, url in enumerate(links[done:])]
        app.show_unfinished_tail()
        app.refresh_failed_button()
        app.refresh_stats(final=app.final_stats())
        pump(app, timeout=0.4)

    def header(self, app, size):
        app.geometry(size + '+0+0')
        pump(app, timeout=0.5)
        scale = ctk.ScalingTracker.get_widget_scaling(app)
        return (app.current_title.cget('text'), ui_theme.full_text(app.current_title),
                app.queue_stats.cget('text'), app.current_title.winfo_width() / scale)

    def test_env3_1_english_finished_titles_are_never_a_fragment(self):
        app = self.make_app('en')
        self.finish(app, 4, 3, 1)
        full = i18n.t('download.title.finished_unfinished', success=3, unfinished=1)
        forms = app.final_stats_forms()
        shown, title, stats, small = self.header(app, '1000x740')
        self.assertEqual(title, full, 'the full title stays the label text')
        self.assertEqual(shown, 'Task finished · Unfinished: 1', 'a shorter form, whole, instead of a fragment')
        self.assertEqual(stats, forms[-1], 'the shortest stats leave the title the most room')
        shown, title, stats, large = self.header(app, '1120x810')
        self.assertEqual(shown, full, 'shown in full at 1120x810')
        self.assertGreaterEqual(large, small, 'a wider window never gives the title less room')
        shown, title, stats, _ = self.header(app, '1600x900')
        self.assertEqual((shown, stats), (full, forms[0]), 'a wide window shows both in full')
        # three-digit counts: the shortest form 'Task finished' (the counts are beside it)
        self.finish(app, 240, 120, 120)
        shown, title, stats, _ = self.header(app, '1000x740')
        self.assertEqual(shown, 'Task finished')
        self.assertEqual(stats, i18n.t('download.stats.final_short', done=120, total=240))
        self.assertEqual(app.retry_button.cget('text'), i18n.t('download.retry_unfinished_n', count=120))
        # a successful job and one that skipped links
        self.finish(app, 3, 3, 0)
        shown, title, stats, _ = self.header(app, '1000x740')
        self.assertEqual(shown, title, 'Download complete · Succeeded: 3 in full')
        self.assertNotIn('…', shown)
        # a new title (a collection starts) measures the room again: its title is shown in full
        app.geometry('1120x810+0+0')
        self.finish(app, 4, 3, 1)
        app.operation = 'collect'
        app.set_busy(True)
        shown, title, stats, _ = self.header(app, '1120x810')
        self.assertEqual(shown, i18n.t('download.foreign.collect.title'))
        app.set_busy(False)
        shown, title, stats, _ = self.header(app, '1120x810')
        self.assertEqual(shown, full)
        # 清除未完成: 'Task finished · Succeeded: 3' (with 'Task finished' as its shorter form)
        with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True):
            app.clear_unfinished()
        shown, title, stats, _ = self.header(app, '1000x740')
        self.assertEqual(title, i18n.t('download.title.finished_cleared', success=3))
        self.assertIn(shown, (title, 'Task finished'))

    def test_env3_1_chinese_header_keeps_the_round_1_rule(self):
        app = self.make_app('zh')
        self.finish(app, 4, 3, 1)
        self.assertIsNone(getattr(app.current_title, '_fit_alternatives', None), 'no shorter forms in Chinese')
        self.assertFalse(app.stats_title_first)
        for size in ('1000x740', '1120x810'):
            shown, title, stats, _ = self.header(app, size)
            self.assertEqual((shown, title, stats), ('任务结束 · 3 条成功，1 条未完成', '任务结束 · 3 条成功，1 条未完成',
                                                     '成功 3  /  未完成 1  /  共 4'), size)

    def test_sweep_1_the_error_of_a_link_that_could_not_start_reads_english(self):
        missing = 'C:\\Missing\\cookies.txt'
        # (1.8.0) the item's error is the message build_command raised (its value is the 1.7.0 text)
        core = engine.msg('core.cookies.missing', file=missing)
        self.assertEqual(core, f'找不到 cookies 文件：{missing}\n请在“偏好设置 → 网络”中重新选择。')
        raw = 'ERROR: [generic] a: Unable to download webpage: HTTP Error 404'
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                links = ['https://example.org/a', 'https://example.org/b']
                app.populate_queue(links)
                app.operation = 'download'
                app.set_busy(True)
                for index, error in ((1, core), (2, raw)):
                    app.handle('item', {'index': index, 'total': 2, 'url': links[index - 1]})
                    app.handle('item_done', {'index': index, 'success': False, 'cancelled': False, 'files': 0,
                                             'bot_check': False, 'error': error})
                shown = messages.show(core)
                if lang == 'en':
                    self.assertEqual(shown, f'Cookies file not found: {missing}\n'
                                            'Choose it again in Settings → Network.')
                else:
                    self.assertEqual(shown, core)
                self.assertTrue(app.row_reason(0).endswith(i18n.t('dialog.reason.engine_error', error=shown)))
                self.assertTrue(app.row_reason(1).endswith(i18n.t('dialog.reason.engine_error', error=raw)),
                                'a raw yt-dlp line stays raw')
                self.assertEqual(app.row_model[0]['error'], core, 'the row keeps the stored form')
                # the same error stored with an unfinished entry (failed-downloads.json): a 1.7.0 entry
                # without a sidecar, read back the way unfinished.load decodes it (lifted, origin legacy)
                stored = messages.decode(dict(entry(7), error=messages.zh(core)), 'failed_entry')
                self.assertEqual((stored['error'].key, stored['error'].origin), ('core.cookies.missing', 'legacy'))
                model = app._unfinished_model(stored)
                app.row_model.append(model)
                self.assertTrue(app.row_reason(len(app.row_model) - 1).endswith(
                    i18n.t('dialog.reason.engine_error', error=shown)))
                if lang == 'en':
                    self.assertEqual(CJK.findall(app.row_reason(0)), [])
                self.close(app)

    def test_sweep_2_the_unfinished_list_dialogs_read_english(self):
        problem = 'C:\\State\\failed-downloads.json'
        # (1.8.0) unfinished.save raises its OSError with a message (its value is the 1.7.0 text)
        core = OSError(messages.msg('core.unfinished.cannot_set_aside', path=problem))
        self.assertEqual(str(core), f'无法保留读不出的未完成清单：{problem}')
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                app.unfinished = [entry(1), entry(2)]
                app.show_unfinished_rows()
                shown = []
                with mock.patch.object(ui_download.messagebox, 'askyesno', return_value=True), \
                        mock.patch.object(ui_download.messagebox, 'showwarning',
                                          side_effect=lambda title, text, **kw: shown.append(text)), \
                        mock.patch.object(engine, 'save_unfinished', side_effect=core), \
                        mock.patch.object(engine, 'load_unfinished', return_value=[entry(1), entry(2)]):
                    app.clear_unfinished()
                    app.remove_row(0)
                expected = messages.error_text(core)
                self.assertEqual(shown, [expected, expected])
                if lang == 'en':
                    self.assertEqual(expected, f'Could not set aside the unreadable Unfinished list: {problem}')
                else:
                    self.assertEqual(expected, str(core))
                self.close(app)


# ---------------------------------------------------------------------------------------- H3
class ProtectedTests(unittest.TestCase):
    def test_set_busy_and_poll_are_the_f_state_text(self):
        source = (Path(studio.__file__)).read_text(encoding='utf-8')
        tree = ast.parse(source)
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'StudioApp':
                for function in node.body:
                    if isinstance(function, ast.FunctionDef) and function.name in PROTECTED:
                        segment = ast.get_source_segment(source, function)
                        found[function.name] = hashlib.sha256(segment.encode('utf-8')).hexdigest()
        self.assertEqual(found, PROTECTED)

    def test_test_visible_constants_keep_their_chinese_values(self):
        self.assertEqual(studio.TITLE, 'YT-DLP 下载器')
        self.assertEqual(ui_download.TITLE, 'YT-DLP 下载器')
        self.assertEqual(ui_download.FINISHED_PREFIX, '任务结束 · ')
        self.assertEqual(ui_download.CLEARED_TITLE_SUFFIX, '')
        self.assertEqual(ui_download.STATE_LOOK['unfinished'][2], '未完成')
        self.assertEqual({state: i18n.CATALOG['zh'][key] for state, key in ui_download.REASON_KEYS.items()},
                         ui_download.REASON_WORDS)
        for name, keys in ui_download.FOREIGN_KEYS.items():
            self.assertEqual(tuple(i18n.CATALOG['zh'][key] for key in keys), ui_download.FOREIGN_TEXT[name])


if __name__ == '__main__':
    unittest.main()
