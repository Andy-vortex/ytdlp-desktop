"""1.8.0 download UI: G4 download-side channels, the consumer census and the regression cases (MESSAGES.md
6.2, 6.2b, 6.3, 6.4, 7.5, 10.3 WP-4).

    python -X utf8 -B test_messages_ui_download.py

* studio, ui_download and ui_log are converted (WP-6 removed the flag MESSAGES_READY), G1 and G5
  clean, no localize call, no stored_text; their H4 constants keep the 1.7.0 values (PROXY_REASON now
  a message, D-F46);
* D-F40 ... D-F49: the page raises messages, shows exceptions with error_text and core values with
  show (badge, log.ready, report_error, history write failure, placeholder title, proxy reason);
* G4: every core.* / value.* / download.reason.* key with sample params (nested messages and a
  Value where a param is one), live and decoded from a stored sidecar, through every download-side
  channel: log page and download.log line, waiting metrics and row detail, item row detail and
  reason dialog, unfinished row and reason dialog after save / load, dialogs and the hint, the row
  title.  en: no CJK (the samples are ASCII); zh: exactly zh(msg);
* census (7.5): log, waiting, item_done and title as a Message, a Raw and a plain str (the last two
  inside messages.exempt, they imitate an unconverted producer): no exception, the zh widget state and
  the stored history bytes identical for the three, en renders the message and shows the others as
  they are;
* regression cases through a scripted Runner into the window: D-H-1 (cookies echo), D-H-2 (reasons
  cut at 70), D-H-4 (a raw engine line that quotes app text); the 6.2b OS-error dialogs in en;
* G2 at the display boundary: an unshown core text reaching append_log, and a message handed to
  i18n.t by a ready UI module, are recorded.
Smoke-mode windows (alpha 0, no frame), no network, no real engine.
"""
import _sandbox
import ast
import json
import re
import string
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import engine
import formats
import i18n
import i18n_tool
import messages
import studio
import ui_download
import ui_log
import ui_theme
import unfinished
import updater
from messages import Message, Raw, msg
from studio import StudioApp

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
MODULES = ('studio', 'ui_download', 'ui_log')
OLD = _sandbox.release_src('1.7.0')
FORMATTER = string.Formatter()
NUMERIC = {'attempt', 'code', 'count', 'dropped', 'length', 'limit', 'line', 'links', 'minutes', 'page', 'pages',
           'seconds', 'size', 'status'}
KEYS = sorted(key for key in i18n.CATALOG['zh'] if key.startswith(('core.', 'value.', 'download.reason.')))
URL = 'https://example.invalid/matrix'
COOKIE_SECRET = 'cookie-secret-value-1234'
ECHO = f"ERROR: invalid Netscape format cookies file 'cookies.txt': '.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}'"
RAW_LINE = 'ERROR: [generic] x: 链接不存在（404）；网站拒绝访问（403）'
CENSUS = 'census: text from an unconverted producer (MESSAGES.md 7.5)'


def zh(value):
    return messages.zh(value)


def english(value):
    with _sandbox.language('en', env=False):
        return messages.show(value)


def sample_params(key):
    """ASCII samples for the placeholders of ``key``; a nested message for error / reason / problem,
    a Value for mode, as the producers pass them."""
    params = {}
    for name in sorted({field for _l, field, _s, _c in FORMATTER.parse(i18n.CATALOG['zh'][key]) if field}):
        if name in NUMERIC:
            params[name] = 7
        elif name == 'mode':
            params[name] = messages.value('mode', formats.MODES[1])
        elif name in ('error', 'reason'):
            params[name] = msg('core.reason.http_404')
        elif name == 'problem':
            params[name] = msg('core.unfinished.problem.unreadable')
        else:
            params[name] = 'Qq'
    return params


def stored(message):
    """``message`` written as a failed-list entry with its sidecar and read back (origin 'stored')."""
    record = messages.encode({'error': message}, 'failed_entry')
    return messages.decode(json.loads(json.dumps(record, ensure_ascii=False)), 'failed_entry')['error']


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


class AppCase(unittest.TestCase):
    """Smoke-mode windows in a language; the language stays set until the test ends."""

    def make_app(self, lang='en', visible=False):
        for earlier in getattr(self, '_apps', []):
            self.close(earlier)
        self._apps = []
        context = _sandbox.language(lang)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/smoke', str(_sandbox.out('ui-download-msgs'))))
        self.addCleanup(self.close, app)
        self._apps.append(app)
        if visible:
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            app.geometry('1000x740+0+0')
        else:
            app.withdraw()
        app.urls.delete('1.0', 'end')
        app.links_changed()
        pump(app, timeout=0.2)
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    def logged(self, app):
        return app.logs.get('1.0', 'end-1c').splitlines() + list(app.log_buffer)

    def assertShown(self, text, value, lang):
        """en: no CJK; zh: exactly the value's zh text."""
        if lang == 'en':
            self.assertEqual(CJK.findall(text), [], text)
        else:
            self.assertEqual(text, zh(value))

    def job(self, app, title=None):
        options = engine.Options(_sandbox.out('ui-download-msgs', 'out'))
        mode = messages.value('mode', options.mode)
        return app.library.create_job('download', title or msg('value.job_title.placeholder', count=1, mode=mode),
                                      {**options.to_dict(), 'urls': [URL]})

    def row(self, app, job):
        with app.library.db() as db:
            title, data = db.execute('SELECT title, data FROM jobs WHERE id=?', (job,)).fetchone()
        return title, json.loads(data)


# ============================================================================ modules, guards, constants

class ModuleTests(unittest.TestCase):
    def test_the_three_modules_are_ready(self):
        for name, module in (('studio', studio), ('ui_download', ui_download), ('ui_log', ui_log)):
            self.assertFalse(hasattr(module, 'MESSAGES_READY'), f'{name}: WP-6 removed the staging flag (7.8)')
            self.assertIn(name, i18n_tool.g1_modules(), name)

    def test_g1_and_g5_are_clean_and_no_allow_entry_is_stale(self):
        context = i18n_tool.GuardContext()
        findings = []
        for name in MODULES:
            findings += i18n_tool.g1_module(context, name)
            findings += i18n_tool.g1c_module(context, context.module(name))
        self.assertEqual([f'{f["rule"]} {f["file"]}:{f["line"]} {f["text"][:60]!r}' for f in findings], [])
        self.assertEqual(i18n_tool.sinks_scan(modules=list(MODULES)), [])
        # with all six UI modules (sink methods resolve across the mixins): nothing in these three files
        everywhere = i18n_tool.sinks_scan(modules=list(i18n_tool.UI_MODULES))
        self.assertEqual([f for f in everywhere if f['file'] in {f'{name}.py' for name in MODULES}], [])
        for name in MODULES:
            self.assertEqual(i18n_tool.allow_stale(context, name), [], name)
            allow = context.allow(name)
            self.assertEqual(allow['pending_keys'], {}, name)
            self.assertEqual([text for text, entry in allow['literals'].items() if entry['category'] == 'pending'], [])
            for text, entry in allow['sinks'].items():
                self.assertTrue(entry.get('reason'), f'{name}: {text!r}')
                self.assertIsInstance(entry.get('line_hint'), int, f'{name}: {text!r}')

    def test_localize_and_stored_text_are_gone(self):
        for name in MODULES:
            lines = [line for line in i18n_tool.localize_lines() if line.startswith(f'{name}.py:')]
            self.assertEqual(lines, [], name)
            tree = ast.parse((i18n_tool.SRC / f'{name}.py').read_text(encoding='utf-8'))
            names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            self.assertNotIn('stored_text', names, name)
        self.assertFalse(hasattr(ui_download, 'stored_text'), '6.4: ui_download.stored_text is deleted')
        # D17: the missing space of the 1.7.0 line (ui_download.py:346 then) stays as it is
        self.assertIn('self.queue_stats =ui_theme.label(', (i18n_tool.SRC / 'ui_download.py').read_text(encoding='utf-8'))

    def test_h4_constants_keep_their_1_7_0_values(self):
        for name, module in (('studio', studio), ('ui_download', ui_download), ('ui_log', ui_log)):
            tree = ast.parse((OLD / f'{name}.py').read_text(encoding='utf-8'))
            checked = 0
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    try:
                        old = ast.literal_eval(node.value)
                    except ValueError:
                        continue
                    if not isinstance(old, (str, tuple, dict)) or not CJK.search(repr(old)):
                        continue
                    self.assertEqual(getattr(module, node.targets[0].id), old, f'{name}.{node.targets[0].id}')
                    checked += 1
            self.assertGreater(checked, 0, name)
        self.assertIsInstance(ui_download.PROXY_REASON, Message)
        self.assertEqual(ui_download.PROXY_REASON.key, 'download.reason.proxy')
        self.assertEqual(ui_download.PROXY_REASON, '无法连接代理或网站，请检查代理地址')


# ============================================================================ D-F40 ... D-F49

class FlowTests(AppCase):
    def test_d_f40_link_file_errors_are_messages(self):
        folder = _sandbox.out('ui-download-msgs', 'files')
        folder.mkdir(parents=True, exist_ok=True)
        large, broken = folder / 'large.txt', folder / 'broken.txt'
        large.write_bytes(b'x' * (5 * 1024 * 1024 + 1))
        broken.write_bytes(b'\x80\x81\xfe\xff\xc3')         # neither UTF-8 nor GB18030
        for path, key in ((large, 'core.links.file_too_large'), (broken, 'core.links.encoding')):
            with self.assertRaises(ValueError) as caught:
                ui_download.decode_link_file(path)
            self.assertIsInstance(caught.exception.args[0], Message)
            self.assertEqual(caught.exception.args[0].key, key)
            self.assertEqual(str(caught.exception), i18n.CATALOG['zh'][key])
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            shown = []
            with mock.patch.object(ui_download.filedialog, 'askopenfilename', return_value=str(large)), \
                    mock.patch.object(ui_download.messagebox, 'showwarning',
                                      side_effect=lambda title, text, **kw: shown.append(text)):
                app.import_txt()
            self.assertEqual(shown, [i18n.t('core.links.file_too_large')])
            self.assertShown(shown[0], msg('core.links.file_too_large'), lang)

    def test_d_f41_and_d_f42_launch_errors(self):
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            shown = []
            with mock.patch.object(ui_download.messagebox, 'showwarning',
                                   side_effect=lambda title, text, **kw: shown.append(text)):
                self.assertFalse(app.launch([], engine.Options(_sandbox.out('ui-download-msgs', 'out'))))
            self.assertEqual(shown, [i18n.t('core.links.none')])
            self.assertShown(shown[0], msg('core.links.none'), lang)
            # D-F42: a library error while the history job is created reaches the log page shown
            failure = ValueError(msg('core.library.no_job'))
            with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                    mock.patch.object(app, 'history_create', side_effect=failure):
                self.assertTrue(app.launch([URL], engine.Options(_sandbox.out('ui-download-msgs', 'out'))))
                pump(app, lambda: not app.busy, timeout=5)
            line = i18n.t('log.history_write_failed', error=i18n.t('core.library.no_job'))
            self.assertIn(line, self.logged(app))
            if lang == 'en':
                self.assertEqual(CJK.findall(line), [], line)
            else:
                self.assertIn(zh(failure.args[0]), line)

    def test_d_f43_badge_and_ready_line_show_the_builtin_message(self):
        for lang, badge, ready in (('en', 'yt-dlp built-in', 'Ready: yt-dlp built-in'),
                                   ('zh', 'yt-dlp 已内置', i18n.CATALOG['zh']['log.ready'].split('{')[0])):
            with mock.patch.object(studio, 'engine_version', return_value=updater.BUILTIN), \
                    mock.patch.object(StudioApp, 'settings_startup', lambda self: None), \
                    mock.patch.object(StudioApp, 'refresh_engine_labels', lambda self: None):
                app = self.make_app(lang)
            self.assertEqual(app.engine_badge.cget('text'), badge)
            first = self.logged(app)[0]
            self.assertTrue(first.startswith(ready), first)
            self.assertEqual(first, i18n.t('log.ready', version=messages.show(updater.BUILTIN)))
            if lang == 'zh':
                self.assertIn('已内置', first)
            with mock.patch.object(studio, 'engine_version', return_value='2026.10.01'), \
                    mock.patch.object(StudioApp, 'settings_startup', lambda self: None), \
                    mock.patch.object(StudioApp, 'refresh_engine_labels', lambda self: None):
                app = self.make_app(lang)
            self.assertEqual(app.engine_badge.cget('text'), 'yt-dlp 2026.10.01')

    def test_d_f45_the_placeholder_title_is_stored_with_its_key(self):
        app = self.make_app('en')
        app.format.set(formats.by_id('audio_mp3').label)
        app.on_format()
        mode = app.mode.get()
        created = []
        real = app.library.create_job

        def create(kind, title, data):
            job = real(kind, title, data)
            created.append(job)
            return job
        app.urls.insert('1.0', 'https://example.org/a\nhttps://example.org/b')
        with mock.patch.object(ui_download, 'Runner', FakeRunner), \
                mock.patch.object(app.library, 'create_job', side_effect=create):
            app.start()
            pump(app, lambda: not app.busy, timeout=5)
        title, data = self.row(app, created[0])
        self.assertEqual(title, f'2 条 · {mode}', 'the 1.7.0 bytes of the title column (H2)')
        self.assertEqual(data['msgs'][title], {'k': 'value.job_title.placeholder',
                                               'p': {'count': 2, 'mode': {'g': 'mode', 'v': mode}}})
        read = app.library.job(created[0])['title']
        self.assertEqual((read.key, read.origin), ('value.job_title.placeholder', 'stored'))
        self.assertEqual(messages.show(read), i18n.t('value.job_title.placeholder', count=2,
                                                     mode=i18n.label('mode', mode)))
        self.assertEqual(CJK.findall(messages.show(read)), [])

    def test_d_f46_the_proxy_reason_is_stored_as_a_message(self):
        app = self.make_app('en')
        job = self.job(app)
        value = {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'url': URL,
                 'reason': engine.NETWORK_REASON, 'error': messages.raw('Unable to connect to proxy')}
        history = ui_download.history_value('item_done', value, True)
        self.assertIs(history['reason'], ui_download.PROXY_REASON)
        self.assertIs(value['reason'], engine.NETWORK_REASON, 'the window still gets the engine value')
        app.library.download_event(job, 'item', {'url': URL})
        app.library.download_event(job, 'item_done', history)
        _title, data = self.row(app, job)
        self.assertEqual(data['last_reason']['reason'], '无法连接代理或网站，请检查代理地址')
        self.assertEqual(data['msgs']['无法连接代理或网站，请检查代理地址'], {'k': 'download.reason.proxy'})
        reason = app.library.job(job)['data']['last_reason']['reason']
        self.assertEqual((reason.key, reason.origin), ('download.reason.proxy', 'stored'))
        self.assertEqual(messages.show(reason), 'Cannot reach the proxy or the site; check the proxy address')

    def test_d_f44_d_f47_d_f49_title_log_and_ui_error(self):
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            app.populate_queue([URL])
            app.operation = 'download'
            app.handle('item', {'index': 1, 'total': 1, 'url': URL})
            checking = msg('core.update.checking')          # a legacy update 'title' event (D-F44)
            app.handle('title', checking)
            self.assertEqual(app.row_model[0]['title'], messages.show(checking))
            self.assertShown(app.row_model[0]['title'], checking, lang)
            app.log_buffer.clear()
            error = ValueError(msg('core.links.none'))
            app.report_error(ValueError, error, None)
            self.assertEqual(app.log_buffer, [i18n.t('log.ui_error', kind='ValueError', value=i18n.t('core.links.none'))])
            if lang == 'en':
                self.assertEqual(CJK.findall(app.log_buffer[0]), [])
            else:
                self.assertIn(zh(error.args[0]), app.log_buffer[0])
            app.operation = None


# ============================================================================ G4: the download-side channels

class ChannelMatrixTests(AppCase):
    """Every key, live and stored, through every download-side channel of the window."""

    def channels(self, app, value, lang, written):
        shown = messages.show(value)
        if lang == 'zh':
            self.assertEqual(shown, zh(value))
        checked = 0
        # log page and download.log line (D-F47)
        app.log_buffer.clear()
        del written[:]
        app.handle('log', value)
        self.assertEqual(app.log_buffer, [shown])
        self.assertEqual(written, [shown], 'download.log holds what the page shows')
        self.assertShown(app.log_buffer[0], value, lang)
        app.flush_log()
        self.assertTrue(app.logs.get('1.0', 'end-1c').endswith(shown + '\n'), 'the log box shows the line')
        checked += 1
        # waiting metrics and row detail
        app.handle('item', {'index': 1, 'total': 1, 'url': URL})
        app.clear_wait()
        app.handle('waiting', {'reason': value, 'seconds': 30})
        wait = ui_download.time_text(app.wait_seconds)
        metrics = ui_theme.full_text(app.metrics)
        self.assertEqual(metrics, i18n.t('download.metrics.wait', reason=shown, time=wait))
        self.assertEqual(app.row_model[0]['detail'], f'{shown} · {wait}')
        self.assertEqual(ui_theme.full_text(app.rows[0]['detail']), f'01  ·  {shown} · {wait}')
        if lang == 'en':
            self.assertEqual(CJK.findall(metrics + app.row_model[0]['detail']), [])
        checked += 1
        # item row detail and reason dialog: the reason, then the error
        dialogs = []
        with mock.patch.object(ui_download.messagebox, 'showinfo',
                               side_effect=lambda title, text, **kw: dialogs.append(text)):
            app.handle('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                     'reason': value, 'error': ''})
            self.assertEqual(app.row_model[0]['detail'], shown)
            self.assertShown(app.row_model[0]['detail'], value, lang)
            app.show_row_reason(0)
            app.handle('item', {'index': 1, 'total': 1, 'url': URL})
            app.handle('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                                     'error': value})
            self.assertIs(app.row_model[0]['error'], value, 'the row keeps the typed value')
            reason = app.row_reason(0)
            self.assertEqual(reason, i18n.t('download.detail.see_log') + i18n.t('dialog.reason.engine_error', error=shown))
            app.show_row_reason(0)
        title = app.row_model[0]['title']
        self.assertEqual(dialogs, [f'{title}\n\n{shown}', f'{title}\n\n{reason}'])
        if lang == 'en':
            self.assertEqual(CJK.findall(''.join(dialogs)), [])
        checked += 2
        # dialogs and the hint (start: a dialog; 加入队列 while a download runs: the hint)
        warnings = []
        with mock.patch.object(app, 'read_form', side_effect=ValueError(value)), \
                mock.patch.object(ui_download.messagebox, 'showwarning',
                                  side_effect=lambda title, text, **kw: warnings.append(text)):
            app.busy = False
            app.start()
            app.busy = True
            app.start()
            app.busy = False
        self.assertEqual(warnings, [shown])
        self.assertEqual(ui_theme.full_text(app.link_hint), shown)
        self.assertShown(warnings[0], value, lang)
        checked += 2
        # the row title (D-F44)
        app.handle('item', {'index': 1, 'total': 1, 'url': URL})
        app.handle('title', value)
        self.assertEqual(app.row_model[0]['title'], shown)
        self.assertEqual(ui_theme.full_text(app.rows[0]['title']), shown)
        self.assertShown(app.row_model[0]['title'], value, lang)
        checked += 1
        return checked

    def test_every_key_through_every_channel(self):
        self.assertGreater(len(KEYS), 140)
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            written = []
            app.logger = mock.Mock(info=written.append)
            app.populate_queue([URL])
            app.operation = 'download'
            checked = 0
            for key in KEYS:
                live = msg(key, **sample_params(key))
                back = stored(live)
                self.assertEqual((back.key, back.origin, zh(back)), (key, 'stored', zh(live)), key)
                for form, value in (('live', live), ('stored', back)):
                    with self.subTest(lang=lang, key=key, form=form):
                        checked += self.channels(app, value, lang, written)
            self.assertEqual(checked, 7 * 2 * len(KEYS))
            app.operation = None
            self.close(app)

    def test_every_key_through_the_unfinished_rows(self):
        """Unfinished row and reason dialog after unfinished.save / load (sidecar or lift)."""
        entries = []
        options = engine.Options(_sandbox.out('ui-download-msgs', 'out')).to_dict()
        for number, key in enumerate(KEYS):
            entries.append(unfinished.entry(f'https://example.invalid/u{number}', options, 'failed',
                                            error=msg(key, **sample_params(key))))
        try:
            engine.save_unfinished(entries)
            loaded = {item['url']: item for item in engine.load_unfinished()}
            self.assertEqual(len(loaded), len(KEYS))
            for lang in ('en', 'zh'):
                app = self.make_app(lang)
                app.unfinished = list(loaded.values())
                app.show_unfinished_rows()
                self.assertEqual(len(app.row_model), len(KEYS))
                dialogs = []
                with mock.patch.object(ui_download.messagebox, 'showinfo',
                                       side_effect=lambda title, text, **kw: dialogs.append(text)):
                    for index, model in enumerate(app.row_model):
                        key = KEYS[int(model['url'].rsplit('u', 1)[1])]
                        error = model['error']
                        with self.subTest(lang=lang, key=key):
                            self.assertIsInstance(error, Message)
                            self.assertEqual(error.key, key)
                            self.assertIn(error.origin, ('stored', 'legacy'))
                            self.assertEqual(zh(error), zh(msg(key, **sample_params(key)))[:200])
                            reason = app.row_reason(index)
                            shown = messages.show(error)
                            # the 1.7.0 rule: the engine's error is added unless the reason already says it
                            engine_part = '' if shown in model['detail'] else i18n.t('dialog.reason.engine_error',
                                                                                     error=shown)
                            self.assertEqual(reason, model['detail'] + engine_part)
                            app.show_row_reason(index)
                            if lang == 'en':
                                self.assertEqual(CJK.findall(dialogs[-1]), [], key)
                                self.assertEqual(messages.show(error), english(msg(key, **sample_params(key))))
                            else:
                                self.assertEqual(shown, zh(error))
                                self.assertTrue(dialogs[-1].endswith(model['detail'] + engine_part))
                self.assertEqual(len(dialogs), len(KEYS))
                self.close(app)
        finally:
            engine.save_unfinished([])


# ============================================================================ census (7.5)

class CensusTests(AppCase):
    """log, waiting, item_done and title as a Message, a Raw and a plain str."""

    TEXTS = {'log': msg('core.engine.item_unfinished', code=1), 'waiting': msg('core.wait.pace'),
             'item_done': msg('core.reason.http_404'), 'title': msg('core.update.checking')}

    def feed(self, app, kind, value):
        """One event through the job's emit closure (history first) and the window; the widget state
        and the stored history fields (without the sidecar) afterwards."""
        job = self.job(app)
        emit = app.download_emit(job)
        app.populate_queue([URL])
        app.operation = 'download'
        app.log_buffer.clear()
        emit('item', {'index': 1, 'total': 1, 'url': URL, 'key': URL, 'format': 'audio_best'})
        if kind == 'log':
            emit('log', value)
        elif kind == 'waiting':
            app.clear_wait()
            emit('waiting', {'reason': value, 'seconds': 30})
        elif kind == 'item_done':
            emit('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                               'url': URL, 'reason': value, 'error': value})
        else:
            emit('title', value)
        app.drain_events()
        state = {'log': app.log_buffer[-1], 'detail': app.row_model[0]['detail'], 'title': app.row_model[0]['title'],
                 'metrics': ui_theme.full_text(app.metrics) if kind == 'waiting' else None,
                 'reason': app.row_reason(0) if kind == 'item_done' else None}
        title, data = self.row(app, job)
        # (I-2) The Raw and plain legs store an app text as a titled job's media title on purpose; the row
        # must not outlive the exemption, or the history page (library_ui, ready since WP-5) shows it later,
        # outside messages.exempt, as a plain app text (G2 R-text; MESSAGES.md 15 A3 / A9).
        with app.library.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job,))
        sidecar = data.pop('msgs', {})
        fields = {'title': title, 'errors': data.get('errors'), 'last_reason': data.get('last_reason')}
        app.operation = None
        return state, fields, sidecar

    def test_census_of_the_download_events(self):
        for kind, message in self.TEXTS.items():
            text = zh(message)
            for lang in ('zh', 'en'):
                app = self.make_app(lang)
                results = {}
                with self.subTest(kind=kind, lang=lang):
                    results['message'] = self.feed(app, kind, message)
                    with messages.exempt(CENSUS):
                        results['raw'] = self.feed(app, kind, messages.raw(text))
                        results['plain'] = self.feed(app, kind, text)
                    states = {form: result[0] for form, result in results.items()}
                    fields = {form: result[1] for form, result in results.items()}
                    self.assertEqual(fields['raw'], fields['message'], 'the stored bytes are the same')
                    self.assertEqual(fields['plain'], fields['message'], 'the stored bytes are the same')
                    if lang == 'zh':
                        self.assertEqual(states['raw'], states['message'])
                        self.assertEqual(states['plain'], states['message'])
                    shown = {'log': states['message']['log'], 'waiting': states['message']['metrics'],
                             'item_done': states['message']['reason'], 'title': states['message']['title']}[kind]
                    if lang == 'en':
                        self.assertEqual(CJK.findall(shown), [], shown)
                        self.assertIn(english(message), shown)
                        for form in ('raw', 'plain'):
                            other = {'log': states[form]['log'], 'waiting': states[form]['metrics'],
                                     'item_done': states[form]['reason'], 'title': states[form]['title']}[kind]
                            self.assertIn(text, other, 'shown as it is')
                    else:
                        self.assertIn(text, shown)
                    sidecars = {form: result[2] for form, result in results.items()}
                    if kind in ('item_done', 'title'):
                        self.assertEqual(sidecars['message'].get(text), {'k': message.key}, 'a message: its key')
                        self.assertNotIn(text, sidecars['plain'], 'plain text: no entry (lifted on read, E3)')
                    if kind == 'item_done':
                        # a Raw error / reason that the lift would read as an app text: null (E2)
                        self.assertIsNone(sidecars['raw'].get(text, 'absent'))
                    if kind == 'title':
                        # the library stores a plain title as raw; a titled one-link job's title is not lifted
                        self.assertNotIn(text, sidecars['raw'])
                self.close(app)


# ============================================================================ regression cases (7.5)

class RegressionTests(AppCase):
    def setUp(self):
        engine.Runner.reset_pacing()
        pace = mock.patch.object(engine, 'PACE_SECONDS', (0, 0))
        pace.start()
        self.addCleanup(pace.stop)
        self.addCleanup(engine.save_unfinished, [])

    def run_into(self, app, links, results, options):
        """One scripted Runner.download() whose events go through the window's emit closure."""
        steps = list(results)
        job = self.job(app)
        app.populate_queue(links)
        app.operation = 'download'
        app.set_busy(True)
        app.job_options = (options, {})
        runner = engine.Runner(app.download_emit(job), library=app.library)

        def run_once(command, index, total, files):
            step = steps.pop(0)
            return step(runner, command, index, total, files) if callable(step) else (step, False, None)
        with mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(links, options)
        app.drain_events()
        self.assertEqual(steps, [])
        return job

    def test_d_h_1_the_cookies_echo(self):
        cookies = _sandbox.out('ui-download-msgs', 'cookies.txt')
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        fragment = 'cookies 文件中有一行格式不对，无法读取（内容不记录）'
        english_text = 'A line of the cookies file has a wrong format and cannot be read (content not logged)'

        def echo(runner, command, index, total, files):
            kind, value = runner._intercept('log', ECHO)
            runner.emit(kind, value)
            runner._note_line(ECHO)
            return 1, False, None
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            written = []
            app.logger = mock.Mock(info=written.append)
            job = self.run_into(app, [URL], [echo], engine.Options(_sandbox.out('ui-download-msgs', 'out'),
                                                                    cookies=str(cookies)))
            model = app.row_model[0]
            self.assertEqual(model['state'], 'failed')
            self.assertEqual(model['detail'], english_text if lang == 'en' else fragment)
            self.assertEqual(model['error'].key, 'core.cookies.line_failed_item')
            reason = app.row_reason(0)
            if lang == 'en':
                self.assertEqual(reason, english_text)
                self.assertIn(i18n.t('core.cookies.line_failed'), written)
                self.assertEqual(CJK.findall(' '.join(written)), [])
            else:
                self.assertEqual(reason, fragment)
                self.assertIn(zh(engine.COOKIE_LINE_FAILED), written)
            _title, data = self.row(app, job)
            self.assertEqual(data['last_reason'], {'error': fragment, 'reason': fragment})
            self.assertEqual(messages.show(app.library.job(job)['data']['last_reason']['reason']),
                             english_text if lang == 'en' else fragment)
            # H6: the cookie line itself is nowhere the window put it
            self.assertNotIn(COOKIE_SECRET, json.dumps([written, self.logged(app), model['detail'], reason, data],
                                                       ensure_ascii=False, default=str))
            self.close(app)

    def test_d_h_2_reasons_cut_at_70(self):
        folder = _sandbox.out('ui-download-msgs', 'long-folder-' + 'p' * 150)
        missing = folder / 'cookies.txt'
        full = f'找不到 cookies 文件：{missing}\n请在“偏好设置 → 网络”中重新选择。'
        self.assertGreater(len(full), 200)
        whole = english(msg('core.cookies.missing', file=str(missing)))
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            job = self.run_into(app, [URL], [], engine.Options(_sandbox.out('ui-download-msgs', 'out'),
                                                               cookies=str(missing)))
            model = app.row_model[0]
            self.assertEqual(model['state'], 'failed')
            if lang == 'en':
                self.assertEqual(model['detail'], whole, 'en shows the whole message (D12)')
                self.assertIn(str(missing), app.row_reason(0))
                self.assertEqual(CJK.findall(app.row_reason(0)), [])
            else:
                self.assertEqual(model['detail'], full[:70], 'zh shows the 1.7.0 cut')
            _title, data = self.row(app, job)
            self.assertEqual(data['last_reason']['reason'], full[:70].strip(), 'the 1.7.0 bytes of the stored reason')
            self.assertEqual(data['last_reason']['error'], full[:200])
            back = app.library.job(job)['data']['last_reason']['reason']
            self.assertEqual(back.key, 'core.cookies.missing')
            self.assertEqual(messages.show(back), whole if lang == 'en' else full[:70].strip())
            self.close(app)

    def test_d_h_4_a_raw_engine_line_passes_unchanged(self):
        def line(runner, command, index, total, files):
            kind, value = runner._intercept(*engine.parse_line(RAW_LINE))
            runner.emit(kind, value)
            runner._note_line(RAW_LINE)
            return 1, False, None
        app = self.make_app('en')
        with _sandbox.fixture_texts('D-H-4: engine output that quotes app text is recognisable by design'):
            self.run_into(app, [URL], [line], engine.Options(_sandbox.out('ui-download-msgs', 'out')))
            self.assertIn(RAW_LINE, self.logged(app), 'never rewritten in en')
            model = app.row_model[0]
            self.assertIsInstance(model['error'], Raw)
            self.assertEqual(model['detail'], '链接不存在（404）；网站拒绝访问（403）')
            self.assertEqual(app.row_reason(0), model['detail'] + i18n.t('dialog.reason.engine_error',
                                                                         error=RAW_LINE[6:].strip()))

    def test_6_2b_os_error_dialogs(self):
        failure = OSError(msg('core.unfinished.cannot_set_aside', path='C:/State/failed-downloads.json'))
        for lang in ('en', 'zh'):
            app = self.make_app(lang)
            shown = []

            def capture(title, text, **kw):
                shown.append(text)
            app.populate_queue([URL])
            app.update_row(0, state='done', path=str(_sandbox.out('ui-download-msgs', 'a.mp3')))
            with mock.patch.object(studio.os, 'startfile', side_effect=failure, create=True), \
                    mock.patch.object(ui_download.os, 'startfile', side_effect=failure, create=True), \
                    mock.patch.object(ui_download.procutil, 'reveal_in_folder', side_effect=failure), \
                    mock.patch.object(ui_log.os, 'startfile', side_effect=failure, create=True), \
                    mock.patch.object(studio.messagebox, 'showerror', side_effect=capture), \
                    mock.patch.object(ui_download.messagebox, 'showerror', side_effect=capture), \
                    mock.patch.object(ui_log.messagebox, 'showwarning', side_effect=capture):
                app.open_folder()
                app.open_row(0)
                app.open_log_file()
            expected = messages.error_text(failure)
            self.assertEqual(shown, [expected] * 3)
            if lang == 'en':
                self.assertEqual(expected, 'Could not set aside the unreadable Unfinished list: C:/State/failed-downloads.json')
            else:
                self.assertEqual(expected, str(failure))
            self.close(app)


# ============================================================================ G2 at the display boundary

@unittest.skipUnless(messages.GUARD, 'needs the guard (run_tests.py sets it)')
class BoundaryGuardTests(AppCase):
    def test_an_unshown_core_text_at_append_log_is_recorded(self):
        app = self.make_app('en')
        with messages.guard_capture() as records:
            app.append_log('此链接未完成（退出码 1），继续处理队列中的下一项。')
            app.append_log(msg('core.engine.item_unfinished', code=1))
            app.append_log(i18n.t('log.ready', version='2026.10.01'))
        self.assertEqual([(item['rule'], item['key'], item['producer'].split(':')[0]) for item in records],
                         [('R-text', 'core.engine.item_unfinished', 'ui_log.py')])

    def test_a_message_handed_to_t_by_the_page_is_recorded(self):
        app = self.make_app('en')
        with messages.guard_capture() as records:
            app.hint_subscription_check(msg('value.subscription.untitled'))
            app.hint_subscription_check(messages.show(msg('value.subscription.untitled')))
        self.assertEqual([(item['rule'], item['producer'].split(':')[0]) for item in records],
                         [('R-param', 'ui_download.py')])


if __name__ == '__main__':
    unittest.main()
