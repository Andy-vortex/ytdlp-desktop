"""1.7.0 language adaptation, WP-L1-A: the shell, the download page and the log page (I18N.md 13).

    python -X utf8 -B test_i18n_download.py

Unit tests of the L1-A rewrites: the title after 清除未完成 (2.0.0 S4: it never counts the unfinished
links, so it stays as it is), classify with English lines (8.6), append_log localization and the proxy
mask (8.6; since 1.8.0 core lines arrive as messages and are shown with messages.show, MESSAGES.md 6.2 /
8.3), the start button compared with the running language's texts (8.7), the unfinished-row detail and
the other 9.3 sites, the sidebar status and the window title (8.5, 10.4); a quick English check that the
download page (its menus included), the log page and the sidebar show no Chinese except user data.
(2.0.0 S4) The stats line and its room rules (10.5, ENV-1, ENV3-1) are gone with the line; the
constants the tests read keep their Chinese
values (H3; (2.0.0 S2 Q4) the SHA-256 pins of set_busy and poll are gone: test_busy_matrix and
test_poll_order test what they do).  Smoke-mode windows (alpha 0, no frame), no network, no real engine.
"""
import _sandbox
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
import library_ui
import messages
import studio
import ui_download
import ui_log
import ui_settings
import ui_theme
import updater
from studio import StudioApp

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
# (2.0.0 S3 W4) The expected app texts are read by key in a fixed language (S3-DESIGN.md 4.3): the test
# pins which key and which params; the wording lives only in the catalog.
from _sandbox import en_t, pump, zh_t


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
class TitleAfterClearTests(AppCase):
    """(2.0.0 S4, download-tasks 5.1) The title of a job that left links unfinished counts the successes only
    (download.title.finished_n), so 清除未完成 never rewrites it (was: the header rendered again from the job's
    numbers, download.title.finished_cleared); the "links kept" metrics line goes, the reason stays."""

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

    def test_job_end_then_clear_in_both_languages(self):
        for lang, tr in (('en', en_t), ('zh', zh_t)):
            with self.subTest(lang=lang):
                app = self.make_app(lang)
                self.finish(app, 2, 1)
                title = tr('download.title.finished_n', success=2)
                self.assertEqual(ui_theme.full_text(app.current_title), title)
                self.assertEqual(ui_theme.full_text(app.metrics), tr('download.metrics.finished_unfinished'))
                self.assertEqual(self.clear(app), title, 'the title stays')
                self.assertEqual(ui_theme.full_text(app.metrics), '', '"links kept" no longer holds')
                # the failed row keeps its reason, marked as removed from the unfinished list
                self.assertEqual(app.row_model[2]['chip'], tr('download.chip.cleared'))
                self.assertEqual(app.row_model[2]['detail'],
                                 f"{tr('download.detail.cleared')} · {tr('core.reason.network')}")
                self.assertFalse(app.retry_button.winfo_manager() or app.task_menu.winfo_manager())
                self.close(app)

    def test_no_title_is_rewritten(self):
        app = self.make_app('en', visible=False)
        for title in (i18n.t('download.title.finished_n', success=3), i18n.t('download.title.stopped'),
                      i18n.t('download.title.bot'), i18n.t('download.title.done', success=2),
                      'Task finished · Succeeded: x · Unfinished: 1', 'My song · Unfinished: 2'):
            app.current_title.configure(text=title)
            self.assertEqual(self.clear(app), title)


# ---------------------------------------------------------------------------------------- 8.6
class ClassifyTests(unittest.TestCase):
    ENGLISH = [
        ('ERROR: [youtube] abc: Video unavailable', 'error'),
        ('WARNING: [youtube] abc: Some formats may be missing', 'warn'),
        ('[youtube] abc: Downloading webpage', 'dim'),
        ('[ExtractAudio] Deleting original file a.webm', 'dim'),
        (en_t('download.title.finished_n', success=1), 'error'),
        (en_t('download.title.all_skipped', skipped=3), 'app'),
        (en_t('log.task_started', count=3, format=en_t('format.audio_mp3'), folder='D:\\Music'), 'app'),
        (en_t('log.file_saved', path='D:\\Music\\a.m4a'), 'ok'),
        (en_t('download.title.done', success=2), 'ok'),
        (en_t('core.engine.error', error='the page is gone'), 'error'),
        (en_t('log.ui_error', kind='ValueError', value='x'), 'error'),
        (en_t('log.ready', version='2026.11.03'), 'app'),
        (en_t('log.link_n', index=2, total=8, url='https://example.org/a'), 'app'),
    ]

    def test_english_lines(self):
        for lang in ('en', 'zh'):        # classify does not depend on the running language
            with _sandbox.language(lang):
                for line, tag in self.ENGLISH:
                    self.assertEqual(ui_log.classify(line), tag, line)

    def test_every_tagged_key_in_english(self):
        with _sandbox.language('en'):
            for key, tag in i18n.TAGS.items():
                text = i18n.t(key, **{name: '1' for name in re.findall(r'\{(\w+)\}', i18n.CATALOG['en'][key])})
                self.assertEqual(ui_log.classify(text), tag, f'{key}: {text!r}')

    # (2.0.0 S4 A1) The colours the 1.6.0 rule gave these Chinese lines (classify_160, gone with ui_log's Chinese
    # starts): classify now finds them by the key whose text they are (i18n.TAGS); every colour is the same.
    # (2.0.0 S4 B1) download.title.finished_n is the title of a job that left links unfinished: red, as that
    # job's title always was.  (S4 stage C, deferred-to-C 3) The app's lines are rendered from their keys, so the
    # table pins the colours and not the wording; only the engine's own lines are written out.
    CHINESE = [
        (zh_t('core.engine.item_unfinished', code=1), 'error'), (zh_t('download.title.finished_n', success=1), 'error'),
        (zh_t('log.file_saved', path='a.m4a'), 'ok'), (zh_t('download.title.done', success=2), 'ok'),
        (zh_t('download.title.all_skipped', skipped=2), 'app'), (zh_t('log.ready', version='2026.11.03'), 'app'),
        (zh_t('core.engine.error', error='页面不存在'), 'error'), (zh_t('log.ui_error', kind='ValueError', value='x'), 'error'),
        (zh_t('log.settings_not_saved'), 'error'),
        ('[youtube] abc: Downloading webpage', 'dim'), ('ERROR: x', 'error'), ('WARNING: y', 'warn'),
        (zh_t('log.task_started', count=3, format='MP3', folder='D:/x'), 'app'),
    ]

    def test_chinese_lines_keep_the_1_6_0_rule(self):
        for lang in ('en', 'zh'):        # classify does not depend on the running language
            with _sandbox.language(lang):
                for line, tag in self.CHINESE:
                    self.assertEqual(ui_log.classify(line), tag, line)
        # every tagged key's Chinese line gets its tag (the 1.6.0 rule gave the same to all of them before S4;
        # the six kept-aside lines of S2 are tagged 'error' since S4, log.md Q3)
        for key, tag in i18n.TAGS.items():
            zh = i18n.CATALOG['zh'][key]
            line = zh.format(**{name: '1' for name in re.findall(r'\{(\w+)\}', zh)})
            self.assertEqual(ui_log.classify(line), tag, f'{key}: {line!r}')
        for area in ('library', 'unfinished', 'archive'):
            for name in ('kept_aside', 'cannot_set_aside'):
                self.assertEqual(i18n.TAGS[f'core.{area}.{name}'], 'error')


# ---------------------------------------------------------------------------------------- 8.6, 8.7, 9.3
class ShellAndLogTests(AppCase):

    def test_append_log_localizes_and_masks_the_proxy(self):
        for lang, mask in (('en', en_t('core.proxy_mask')), ('zh', zh_t('core.proxy_mask'))):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                written = []
                app.logger = mock.Mock(info=written.append)
                app.log_buffer.clear()
                app.proxy.set('http://127.0.0.1:8899')
                # (1.8.0) the core line is the message the engine emits (its value is the 1.7.0 text)
                core = engine.msg('core.engine.item_unfinished', code=1)
                self.assertEqual(core, zh_t('core.engine.item_unfinished', code=1))
                raw = '[download]  12.5% of 3.00MiB at 1.2MiB/s ETA 00:02'
                for line in (core, raw, '代理 http://127.0.0.1:8899 无响应', engine.NETWORK_REASON):
                    app.append_log(line)
                expected = [messages.show(core), raw, '代理 ' + mask + ' 无响应', messages.show(engine.NETWORK_REASON)]
                self.assertEqual(app.log_buffer, expected)
                self.assertEqual(written, expected, 'download.log holds what the owner saw')
                if lang == 'en':
                    self.assertEqual(app.log_buffer[0], en_t('core.engine.item_unfinished', code=1))
                    self.assertEqual(app.log_buffer[3], en_t('core.reason.network'))
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
        self.assertEqual(i18n.t('download.start'), zh_t('download.start'), 'the language is zh again')

    def test_sidebar_status_window_title_and_badges(self):
        # (the settings page writes the badge again with its own engine check: L1-B's code, not run here)
        with mock.patch.object(studio, 'engine_version', return_value=updater.BUILTIN),                 mock.patch.object(ui_settings, 'engine_version', return_value=updater.BUILTIN),                 mock.patch.object(StudioApp, 'settings_startup', lambda self: None),                 mock.patch.object(StudioApp, 'refresh_engine_labels', lambda self: None):
            app = self.make_app('en')
        self.assertEqual(app.title(), en_t('app.title'))
        # (2.0.0 S4 A2) The yt-dlp version is on the settings page only (version_label); the sidebar shows the
        # 'Update available' link while a newer engine is offered, and its status is one word that is never cut.
        self.assertEqual(app.version_label.cget('text'), 'yt-dlp ' + en_t('core.update.builtin'))
        self.assertFalse(hasattr(app, 'engine_badge') or hasattr(app, 'app_badge'))
        self.assertEqual(self.logged(app)[0], en_t('log.ready', version=en_t('core.update.builtin')),
                         self.logged(app)[:2])
        self.assertEqual(app.engine_notice.cget('text'), en_t('shell.engine_update'))
        self.assertEqual(app.engine_notice.winfo_manager(), '')
        app.show_engine_notice(True)
        self.assertEqual(app.engine_notice.winfo_manager(), 'grid')
        app.show_engine_notice(False)
        self.assertEqual(app.engine_notice.winfo_manager(), '')
        links = [f'https://example.org/{number}' for number in range(456)]
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        app.handle('item', {'index': 123, 'total': 456, 'url': links[122]})
        pump(app, timeout=0.2)
        self.assertEqual(app.side_status.cget('text'), en_t('shell.status.downloading'))
        self.assertIsNone(getattr(app.side_status, '_fit_text', None), 'the status is not managed by fit')
        self.assertTrue(app.title().startswith(f"123/456 — {en_t('app.title')}"), app.title())
        self.assertTrue(ui_theme.full_text(app.current_title).startswith(
            en_t('download.title.downloading_n', index=123, total=456)))
        app.set_window_title(i18n.t('window.title.unfinished', unfinished=2))
        self.assertEqual(app.title(), f"{en_t('window.title.unfinished', unfinished=2)} — {en_t('app.title')}")

    def test_unfinished_rows_show_the_reason_and_format_in_the_running_language(self):
        entries = [entry(1, 'blocked', 'audio_mp3'), entry(2, 'failed', 'no-such-format', mode='audio_flac'),
                   entry(3, 'stopped', 'video_mp4'), entry(4, 'unstarted', 'audio_mp3'), entry(5, 'odd', 'audio_mp3')]
        # (2.0.0 S3 W4, R4) download.detail.unfinished of the reason word (ui_download.REASON_KEYS) and the
        # format label (format.<id>, or format.<stored mode> when the format key is unknown; 2.0.0 S4 A1: one name)
        parts = [('download.reason.blocked', 'format.audio_mp3'), ('download.reason.failed', 'format.audio_flac'),
                 ('download.chip.stopped', 'format.video_mp4'), ('download.chip.unstarted', 'format.audio_mp3'),
                 ('download.reason.failed', 'format.audio_mp3')]
        self.assertEqual([ui_download.REASON_KEYS.get(e['reason'], 'download.reason.failed') for e in entries],
                         [reason for reason, _label in parts], 'the keys the rows use are the ones REASON_KEYS names')
        expected = {lang: [tr('download.detail.unfinished', reason=tr(reason), format=tr(label)) for reason, label in parts]
                    for lang, tr in (('en', en_t), ('zh', zh_t))}
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                app = self.make_app(lang, visible=False)
                app.unfinished = list(entries)
                app.show_unfinished_rows()
                self.assertEqual([model['detail'] for model in app.row_model], expected[lang])
                self.assertEqual([model['entry']['options'].get('mode') for model in app.row_model][1], 'audio_flac',
                                 'the entry keeps its stored mode')
                self.assertEqual(app.row_model[0]['state'], 'unfinished')
                self.assertEqual(app.rows[0]['badge'].cget('text'), i18n.label('row_state', 'unfinished'))
                self.close(app)

    def test_task_start_log_line_and_stored_job_title(self):
        app = self.make_app('en', visible=False)
        mode = formats.by_id('audio_mp3').id
        app.format_menu._dropdown_callback(i18n.t('format.audio_mp3'))
        self.assertEqual(app.mode.get(), mode, 'the menu stores the preset id')
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
        head = en_t('log.task_started', count=2, format=en_t('format.audio_mp3'), folder='')
        started = [line for line in self.logged(app) if line.startswith(head)]
        self.assertEqual(len(started), 1, self.logged(app))
        # (2.0.0 S2 P2) the stored title is '' until the first media title; the history shows the first link
        self.assertEqual(created, [''])
        self.assertEqual(library_ui.history_title(app.library.history()[0]),
                         en_t('value.job_title.multi', title='example.org/a', count=2))

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
        self.assertEqual(app.row_model[0]['detail'], en_t('core.reason.network'))
        self.assertEqual(app.row_model[0]['error'], raw)
        self.assertEqual(app.row_reason(0), en_t('core.reason.network')
                         + en_t('dialog.reason.engine_error', error=raw))
        self.assertEqual(app.row_model[1]['detail'],
                         i18n.t('download.detail.stale_update', reason=i18n.t('core.reason.stale')))
        self.assertEqual(app.row_model[1]['detail'],
                         en_t('download.detail.stale_update', reason=en_t('core.reason.stale')), 'in English')
        self.assertTrue(ui_theme.full_text(app.metrics).startswith(
            en_t('download.metrics.wait', reason=en_t('core.wait.pace'), time='01:30')),
                        ui_theme.full_text(app.metrics))
        self.assertTrue(app.side_status.cget('text').startswith(en_t('shell.status.waiting', time='')))
        app.proxy.set('http://127.0.0.1:8899')
        app.job_options = (engine.Options(_sandbox.out('dl'), proxy='http://127.0.0.1:8899'), {})
        app.handle('item_done', {'index': 3, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                 'reason': engine.NETWORK_REASON})
        self.assertEqual(app.row_model[2]['detail'], en_t('download.reason.proxy'))
        stored = ui_download.history_value('item_done', {'reason': engine.NETWORK_REASON}, True)
        self.assertEqual(stored['reason'], ui_download.PROXY_REASON, 'the history stores the canonical text')


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
                if widget is app.urls._textbox or widget is app.output_button or widget.master is app.output_button:
                    continue        # the link box and the download folder: user data
                if CJK.search(value):
                    found.append(f'{widget}: {value!r}')
        found += [f'title: {app.title()!r}'] if CJK.search(app.title()) else []
        return found

    @staticmethod
    def menu_labels(items):
        """(2.0.0 S4) The labels of a menu, from its generator (*_menu_items), without opening it."""
        return [item.label for item in items if item is not ui_theme.SEPARATOR]

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
        menus = {'row': self.menu_labels(app.row_menu_items(1)), 'skipped row': self.menu_labels(app.row_menu_items(2)),
                 'link': self.menu_labels(app.link_menu_items()), 'task': self.menu_labels(app.task_menu_items())}
        self.assertEqual([len(labels) for labels in menus.values()], [6, 6, 4, 1])   # task: 清除未完成 (final polish)
        for name, labels in menus.items():
            self.assertEqual([label for label in labels if CJK.search(label)], [], name)
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
        self.assertEqual(values, list(formats.MENU_ORDER))      # (2.0.0 S4 A1) the menu's values are the preset ids
        for value in values:
            label = i18n.t('format.' + value)
            self.assertIsNone(CJK.search(label), label)
            app.format_menu._dropdown_callback(label)
            self.assertEqual(app.mode.get(), value)
        app.mode.set('video_mp4')
        app.on_format()
        app.quality_menu._dropdown_callback(en_t('value.resolution.best'))
        self.assertEqual(app.resolution.get(), engine.RESOLUTIONS[0])
        app.quality_menu._dropdown_callback('1080p')
        self.assertEqual(app.resolution.get(), '1080p')
        # (2.0.0 S4 A1) as wide as its longest name needs (the 250 floor went with download-input 7.2)
        self.assertEqual(app.format_menu.cget('width'),
                         ui_theme.menu_width(app.format_menu.master, [i18n.t('format.' + i) for i in formats.MENU_ORDER]))


# ------------------------------------------------------------------------------ final fix round 3
class FinalFixRound3Tests(AppCase):
    """(SWEEP-1, SWEEP-2) core error texts of the reason dialog and of the unfinished-list dialogs in the
    running language.  (2.0.0 S4) The ENV3-1 cases (a finished job's stats form chosen by the title, shorter
    title forms) went with the stats line: the title has a line of its own (test_page_download)."""

    def test_sweep_1_the_error_of_a_link_that_could_not_start_reads_english(self):
        missing = 'C:\\Missing\\cookies.txt'
        # (1.8.0) the item's error is the message build_command raised (its value is the 1.7.0 text)
        core = engine.msg('core.cookies.missing', file=missing)
        self.assertEqual(core, zh_t('core.cookies.missing', file=missing))
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
                    self.assertEqual(shown, en_t('core.cookies.missing', file=missing))
                else:
                    self.assertEqual(shown, core)
                self.assertTrue(app.row_reason(0).endswith(i18n.t('dialog.reason.engine_error', error=shown)))
                self.assertTrue(app.row_reason(1).endswith(i18n.t('dialog.reason.engine_error', error=raw)),
                                'a raw yt-dlp line stays raw')
                self.assertEqual(app.row_model[0]['error'], core, 'the row keeps the stored form')
                # the same error stored with an unfinished entry (failed-downloads.json), read back the way
                # unfinished.load reads it ((2.0.0 S2 P4) format 2: the error slot is {"k", "p"})
                stored = dict(entry(7), error=messages.from_json(messages.to_json(core)))
                self.assertEqual(stored['error'].key, 'core.cookies.missing')
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
        self.assertEqual(str(core), zh_t('core.unfinished.cannot_set_aside', path=problem))
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
                    self.assertEqual(expected, en_t('core.unfinished.cannot_set_aside', path=problem))
                else:
                    self.assertEqual(expected, str(core))
                self.close(app)


if __name__ == '__main__':
    unittest.main()
