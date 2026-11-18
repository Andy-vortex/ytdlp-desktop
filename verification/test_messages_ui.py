"""1.8.0 collect, library and settings pages (MESSAGES.md 6, 7.5, 7.9; WP-5).

    python -X utf8 -B test_messages_ui.py

The three UI modules are converted (WP-6 removed the staging flag MESSAGES_READY) and clean under
G1 and G5; the 1.7.0 code that 6.4 deletes is gone (the key-prefix lists, stored_text, every localize call, library.PAGE_TITLE_KEYS)
and the ids keep their values.  G4: every core.* / value.* / download.reason.* key through every
channel of these pages, live and as a stored record with its sidecar (en: no Chinese, zh: the
message's own zh text in the 1.7.0 composition).  The consumer census of the collect_* and
update_* events and of Library.collection_event (a Message, a Raw and a plain str: no exception,
the same zh state and stored bytes; en: the key rendered, the other two as they are).  C-F39 /
C-F40 / C-F41 and the raw title of a collect job.  History titles (CR2-2) and notes (R4 rows) in
both languages for rows written by the frozen 1.5.1, 1.6.0 and 1.7.0 Library (child processes on
release-sources, -B) and by 1.8.0: en exact, zh equal to what the frozen 1.7.0 page functions show
for the same rows.  The 6.2b dialogs in en.  The three pages in en with that data show no Chinese
outside user data.  Smoke apps, never on screen; no network; no real engine.
"""
import _sandbox
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import time
import tkinter as tk
import unittest
from unittest import mock

import customtkinter as ctk
import collection_ui
import engine
import formats
import i18n
import i18n_tool
import library
import library_ui
import messages
import ui_settings
import ui_theme
import updater
import urls
from collector import CollectOptions
from messages import Message, cut, msg, raw, value

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
FORMATTER = string.Formatter()
MODULES = ('collection_ui', 'library_ui', 'ui_settings')
SRC = Path(library.__file__).resolve().parent
A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE = 'https://example.invalid/list'
NUMERIC = {'count', 'code', 'page', 'status', 'length', 'seconds', 'attempt', 'limit', 'line', 'pages', 'links',
           'dropped', 'new', 'pending', 'total', 'shown', 'completed', 'errors', 'percent', 'added', 'skipped'}
LONG = 'D:\\Media\\' + 'a' * 60 + '\\cookies.txt'
CENSUS = 'census: text from an unconverted producer'
OPTIONS = {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1, 'proxy': ''}


def keys():
    """Every key 1.8.0 can store or emit (MESSAGES.md 3.6: core.*, value.*, download.reason.*)."""
    return sorted(key for key in i18n.CATALOG['zh'] if key.startswith(('core.', 'value.', 'download.reason.')))


def sample_params(key):
    """ASCII samples (raw text, numbers, a stored mode value) and a nested message for error / reason."""
    params = {}
    for name in sorted({field for _l, field, _s, _c in FORMATTER.parse(i18n.CATALOG['zh'][key]) if field}):
        if name in NUMERIC:
            params[name] = 7
        elif name == 'mode':
            params[name] = value('mode', formats.MODES[1])
        elif name in ('error', 'reason', 'problem'):
            params[name] = msg('core.collect.too_large')
        elif name == 'name':
            params[name] = msg('value.subscription.untitled')
        else:
            params[name] = raw('Qq')
    return params


def sample(key):
    return msg(key, **sample_params(key))


def chinese(text):
    return CJK.findall(str(text))


def page_chinese(text):
    """CJK of a page text, without the deliberate ones of the 1.7.0 English pages (test_i18n_pages.cjk):
    the language row's own names (简体中文, Language / 语言) and the brackets 【】『』 the naming note quotes."""
    text = str(text)
    for key in ('settings.language.zh', 'settings.language.title'):
        for allowed in i18n.all_values(key):
            text = text.replace(allowed, '')
    return [char for char in CJK.findall(text) if char not in '【】『』']


def pump(app, timeout=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        time.sleep(0.005)


def walk(widget):
    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.winfo_children())


def texts_of(root):
    """Every text the widgets below ``root`` show or offer, table cells included."""
    found = []
    for widget in walk(root):
        if isinstance(widget, ctk.CTkLabel):
            found.append(ui_theme.full_text(widget))
        elif isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkSwitch)):
            found.append(widget.cget('text'))
        if isinstance(widget, ctk.CTkOptionMenu):
            found.append(widget._text_label.cget('text'))
            found += list(widget.cget('values'))
        elif isinstance(widget, ctk.CTkSegmentedButton):
            found += list(widget.cget('values'))
        elif isinstance(widget, ctk.CTkEntry):
            found.append(widget.cget('placeholder_text') or '')
        elif isinstance(widget, ui_theme.AppTable):
            found += [widget.heading(column, 'text') for column in widget['columns']]
            found.append(getattr(widget, '_empty', ''))
            for iid in widget.get_children():
                found += [str(cell) for cell in widget.full_values(iid)]
    return [text for text in found if text]


class AppCase(unittest.TestCase):
    """Smoke StudioApps, never on screen; the GitHub check is patched."""

    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', side_effect=AssertionError('no network')),
                        mock.patch('studio.StudioApp.clipboard_get', side_effect=tk.TclError('no clipboard'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_app(self, output, size='1120x810'):
        from studio import StudioApp
        for earlier in getattr(self, '_apps', []):
            self.close(earlier)             # one live StudioApp at a time
        ctk.set_appearance_mode('light')
        app = StudioApp(_sandbox.app_args('https://example.invalid/pages', str(_sandbox.out(output))))
        app.urls.delete('1.0', 'end')
        app.links_changed()
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.geometry(size + '+0+0')
        self._apps = [app]
        self.addCleanup(self.close, app)
        pump(app, 0.3)
        return app

    @staticmethod
    def close(app):
        try:
            writer = getattr(app, 'history_writer', None)
            if writer is not None:
                writer.idle(5)
            if not getattr(app, '_finished', False) and app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def logged(app):
        return app.logs.get('1.0', 'end-1c') + '\n' + '\n'.join(app.log_buffer)

    def assertShown(self, shown, expected, lang, what):
        self.assertEqual(shown, expected, what)
        if lang == 'en':
            self.assertEqual(chinese(shown), [], f'{what}: {shown}')


# ============================================================================ ready, G1, G5, 6.4
class ReadyTests(unittest.TestCase):
    def test_the_three_modules_are_ready_and_clean(self):
        for module in MODULES:
            self.assertIn(module, i18n_tool.g1_modules(), module)        # G1 / G5 unconditional since WP-6 (7.8)
        context = i18n_tool.GuardContext(i18n_tool.SRC, i18n_tool.ALLOW_DIR)
        self.assertEqual([dict(f) for f in i18n_tool.g1_scan(modules=list(MODULES))], [], 'G1 family')
        # (I-2, MESSAGES.md 15 A7) G5 with all six UI modules (calls resolve across the mixins, e.g.
        # self.add_links of ui_download); the findings in these three files must be none
        files = {f'{module}.py' for module in MODULES}
        self.assertEqual([dict(f) for f in i18n_tool.sinks_scan(modules=list(i18n_tool.UI_MODULES))
                          if f['file'] in files], [], 'G5')
        for module in MODULES:
            self.assertEqual(i18n_tool.allow_stale(context, module), [], module)
            allow = i18n_tool.load_allow(module)
            self.assertEqual(allow['pending_keys'], {}, module)
            for text, entry in allow['sinks'].items():
                self.assertTrue(entry.get('reason'), (module, text))
        self.assertEqual(set(i18n_tool.load_allow('library_ui')['raises']), {'the history job was never written', 'no options'},
                         'G1e: the two English internal raises of library_ui stay')

    def test_the_deleted_code_is_gone_and_the_ids_keep_their_values(self):
        lines = [line for line in i18n_tool.localize_lines() if line.split(':')[0][:-3] in MODULES]
        self.assertEqual(lines, [], 'no localize call line left in the three modules')
        for module, names in ((collection_ui, ('PAGE_TITLE_KEYS', 'stored_text')),
                              (library_ui, ('JOB_TITLE_KEYS', 'NAME_KEYS', 'TITLED_JOB_KEYS', 'UNTITLED_JOB_KEYS',
                                            'COLLECT_JOB_KEYS', 'PAGE_TITLE_KEYS', 'stored_text')),
                              (library, ('PAGE_TITLE_KEYS', '_shown_title'))):
            for name in names:
                self.assertFalse(hasattr(module, name), f'{module.__name__}.{name}')
        for module in MODULES:
            source = (SRC / f'{module}.py').read_text(encoding='utf-8')
            self.assertNotIn('localize(', source, module)
        self.assertEqual(collection_ui.WEB_LINK, '网页链接')
        self.assertEqual((library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE),
                         ('任务历史与续采', '增量订阅', '统一资料归档'))
        for constant in (collection_ui.WEB_LINK, library_ui.TAB_HISTORY):
            self.assertNotIsInstance(constant, Message, 'ids and compared stored values are not messages')

    def test_c_f39_the_subscription_result(self):
        errors = [msg('core.collect.http_status', status=503), msg('core.collect.too_large'), msg('core.collect.no_match')]
        record = {'status': 'partial', 'pages': [{'url': PAGE}], 'data': {'errors': errors}}
        pages, successful, error = collection_ui.subscription_result(record, False)
        self.assertEqual((pages, successful), ([{'url': PAGE}], False))
        self.assertEqual(error.key, 'common.clauses')
        self.assertEqual(error, messages.zh(errors[1]) + '；' + messages.zh(errors[2]), 'the 1.7.0 join of the last two')
        one = collection_ui.subscription_result({'status': 'partial', 'pages': [], 'data': {'errors': errors[:1]}}, False)[2]
        self.assertIs(one, errors[0], 'one error: that message itself')
        for cancelled, key in ((True, 'value.subscription.stopped'), (False, 'value.subscription.unfinished')):
            got = collection_ui.subscription_result({'status': 'completed', 'pages': [], 'data': {'errors': []}},
                                                    cancelled)[2]
            self.assertEqual(got.key, key)
        self.assertTrue(collection_ui.subscription_result({'status': 'completed', 'pages': [], 'data': {'errors': []}},
                                                          False)[1])
        with _sandbox.language('en'):
            self.assertEqual(messages.show(error), i18n.t('core.collect.too_large') + ' ' + i18n.t('core.collect.no_match'))


# ============================================================================ G4 channel x key matrix
class ChannelMatrixTests(AppCase):
    """Every key through every channel of the collect, library and settings pages (MESSAGES.md 7.5)."""

    def run_matrix(self, lang):
        with _sandbox.language(lang):
            app = self.make_app(f'messages-ui-matrix-{lang}')
            app.history_limit = 1000
            store = app.library
            all_keys = keys()
            self.assertGreater(len(all_keys), 100)
            samples = {key: sample(key) for key in all_keys}
            checked = 0
            # -------------------------------------------------- live legs
            for index, key in enumerate(all_keys):
                m = samples[key]
                shown = messages.show(m)
                if lang == 'zh':
                    self.assertEqual(shown, messages.zh(m), key)
                with self.subTest(lang=lang, key=key, leg='live'):
                    checked += self.live_channels(app, key, m, shown, lang, index)
            # -------------------------------------------------- stored legs (sidecars)
            jobs, downloads, folders = {}, {}, {}
            pending_sub = store.subscription(raw('Pending feed'), OPTIONS, 3600, True, True)
            note_sub = store.subscription(raw('Note feed'), OPTIONS, 3600, True, True)
            page_links = []
            for index, key in enumerate(all_keys):
                m = samples[key]
                job = store.create_job('collect', m, dict(OPTIONS, urls=[f'{PAGE}/{index}']))
                store.change_job(job, status='partial', errors=[m])
                jobs[key] = job
                download = store.create_job('download', raw(f'Song {index}'),
                                            {'urls': [f'{A}/{index}'], 'output': 'x', 'mode': formats.MODES[3]})
                store.download_event(download, 'item', {'url': f'{A}/{index}'})
                store.download_event(download, 'item_done', {'success': False, 'url': f'{A}/{index}', 'error': m,
                                                             'reason': m})
                downloads[key] = download
                folder = _sandbox.out(f'messages-ui-archive-{lang}', str(index))
                store.archive_index(str(folder), {'url': f'{A}/{index}.mp4', 'source': PAGE, 'title': m, 'uploader': '',
                                                  'files': [], 'transcripts': []})
                folders[str(folder)] = key
                page_links.append({'url': f'https://cdn.example.invalid/{index}.mp3', 'title': m, 'kind': '媒体地址',
                                   'source': PAGE})
            store.finish_subscription(pending_sub, [{'url': PAGE, 'title': raw('List'), 'links': page_links}], True)
            app.refresh_library()
            for key in all_keys:
                m = samples[key]
                shown = messages.show(m)
                with self.subTest(lang=lang, key=key, leg='stored'):
                    record = store.job(jobs[key])
                    self.assertEqual(record['title'].key, key)
                    self.assertEqual(record['title'].origin, 'stored')
                    self.assertShown(app.history_tree.full_values(jobs[key])[1], shown, lang, 'history title')
                    done = i18n.t('library.history.downloaded', completed=0)
                    self.assertShown(library_ui.LibraryUI.history_note_text(record),
                                     i18n.t('library.history.pages', pages=0, done=done)
                                     + i18n.t('library.history.last_error', error=shown[:130]), lang, 'history note')
                    self.assertShown(library_ui.LibraryUI.history_note_text(store.job(downloads[key])),
                                     done + i18n.t('library.history.last_error', error=shown[:130]), lang,
                                     'download note (last_reason)')
                    checked += 3
            rows = {row['folder']: row for row in store.archives()}
            for n, row in enumerate(app.visible_archives):
                key = folders[row['folder']]
                self.assertShown(app.archive_tree.full_values(str(n))[0], messages.show(samples[key]), lang, 'archive table')
                checked += 1
            self.assertEqual(len(app.visible_archives), len(all_keys))
            for key in all_keys[:5]:
                app.archive_search.set(messages.show(samples[key]))
                pump(app, 0.05)
                found = {folders[row['folder']] for row in app.visible_archives}
                self.assertIn(key, found, 'the shown title is searched')
                app.archive_search.set(messages.zh(samples[key]))
                pump(app, 0.05)
                self.assertIn(key, {folders[row['folder']] for row in app.visible_archives}, 'the stored text is searched')
            app.archive_search.set('')
            app.subscription_tree.selection_set(pending_sub)
            app.subscription_selection()
            pending = {app.visible_pending[int(iid)]['url']: app.pending_tree.full_values(iid)[0]
                       for iid in app.pending_tree.get_children()}
            for index, key in enumerate(all_keys):
                self.assertShown(pending[f'https://cdn.example.invalid/{index}.mp3'], messages.show(samples[key]), lang,
                                 f'pending table {key}')
                checked += 1
            for key in all_keys:
                m = samples[key]
                store.finish_subscription(note_sub, [], False, m)
                app.subscription_tree.selection_set(note_sub)
                app.subscription_selection()
                note = ui_theme.full_text(app.subscription_note)
                row = next(r for r in store.subscriptions() if r['id'] == note_sub)
                self.assertEqual(row['data']['last_error'].key, key)
                self.assertShown(note, i18n.t('library.subscription.status', new=0, pending=0) + messages.show(m), lang,
                                 f'subscription note {key}')
                checked += 1
            self.assertGreater(checked, 20 * len(all_keys))

    def live_channels(self, app, key, m, shown, lang, index):
        checked = 0
        # collect status and collect log (collect_status, collect_error)
        app.handle('collect_status', m)
        self.assertShown(ui_theme.full_text(app.collect_status), shown, lang, 'collect status')
        app.handle('collect_error', m)
        self.assertShown(ui_theme.full_text(app.collect_status), i18n.t('collect.status.partial', error=shown), lang,
                         'collect status (error)')
        self.assertIn(i18n.t('log.collect.unfinished', error=shown), self.logged(app))
        # collect table, its log line and the export
        app.collected_links.clear()
        app.collected_pages.clear()
        media = f'https://cdn.example.invalid/live-{index}.mp4'
        page = {'url': PAGE, 'title': m, 'text': 'Body', 'markdown': 'Body', 'next': None,
                'links': [{'url': media, 'title': m, 'kind': '媒体地址', 'source': PAGE}]}
        app.handle('collect_page', page)
        rows = [app.collect_tree.full_values(iid) for iid in app.collect_tree.get_children()]
        self.assertEqual(len(rows), 1)
        self.assertShown(rows[0][1], shown, lang, 'collect table')
        self.assertIn(i18n.t('log.collect.page', title=shown, count=1), self.logged(app))
        target = _sandbox.out(f'messages-ui-export-{lang}', f'{index}.txt')
        with mock.patch('collection_ui.filedialog') as files:
            files.asksaveasfilename.return_value = str(target)
            app.export_collected_text()
        content = target.read_text(encoding='utf-8-sig')
        self.assertTrue(content.startswith(shown + '\n'), ('export', key, content))
        if lang == 'en':
            self.assertEqual(chinese(content), [], content)
        checked += 6
        # history title, history note and subscription name (the page functions)
        self.assertShown(library_ui.history_title({'kind': 'collect', 'title': m, 'data': {}}), shown, lang, 'history title')
        done = i18n.t('library.history.downloaded', completed=0)
        self.assertShown(library_ui.LibraryUI.history_note_text({'kind': 'collect', 'pages': [], 'data': {'errors': [m]}}),
                         i18n.t('library.history.pages', pages=0, done=done)
                         + i18n.t('library.history.last_error', error=shown[:130]), lang, 'history note')
        cells = library_ui.subscription_row({'name': m, 'enabled': True, 'interval': 900, 'data': {'last_check': 0},
                                             'pending': 0})
        self.assertShown(cells[0], shown, lang, 'subscription name')
        checked += 3
        # update status and error, and their log line
        app.busy, app.operation = True, 'update'
        try:
            app.on_update_status(m)
            self.assertShown(app.update_status.cget('text'), shown, lang, 'update status')
            app.on_update_error(m)
            self.assertShown(app.update_status.cget('text'), i18n.t('settings.update.failed', error=shown), lang,
                             'update error')
            self.assertIn(i18n.t('log.update_failed', error=shown), self.logged(app))
        finally:
            app.busy, app.operation = False, None
        checked += 3
        # dialogs (MESSAGES.md 6.2 / 6.2b)
        checked += self.dialogs(app, m, shown, lang)
        return checked

    def dialogs(self, app, m, shown, lang):
        checked = 0
        with mock.patch.object(app, 'collection_options', side_effect=ValueError(m)), \
                mock.patch('collection_ui.messagebox') as dialogs:
            app.start_collection()
        self.assertShown(dialogs.showwarning.call_args.args[1], shown, lang, 'check settings dialog')
        with mock.patch.object(app.library, 'create_job', side_effect=ValueError(m)), \
                mock.patch('collection_ui.messagebox') as dialogs:
            self.assertFalse(app.launch_collection(CollectOptions([PAGE])))
        self.assertShown(dialogs.showwarning.call_args.args[1], i18n.t('dialog.collect.not_started.text', error=shown),
                         lang, 'not started dialog')
        self.assertIn(i18n.t('log.collect.not_started', error=shown), self.logged(app))
        with mock.patch.object(app, 'collection_options', side_effect=ValueError(m)), \
                mock.patch('library_ui.messagebox') as dialogs:
            app.save_subscription()
        self.assertShown(dialogs.showwarning.call_args.args[1], shown, lang, 'subscription save dialog')
        app.collected_pages = [{'url': PAGE, 'title': raw('List'), 'text': '', 'markdown': '', 'links': []}]
        app.archive_root.set(str(_sandbox.out('messages-ui-archive-root')))
        with mock.patch('library_ui.Archive') as archive, mock.patch('library_ui.messagebox') as dialogs:
            archive.return_value.prepare.side_effect = OSError(m)
            app.archive_pages()
        self.assertShown(dialogs.showerror.call_args.args[1], shown, lang, 'archive dialog')
        with mock.patch('collection_ui.filedialog') as files, mock.patch('collection_ui.Path') as path, \
                mock.patch('collection_ui.messagebox') as dialogs:
            files.asksaveasfilename.return_value = 'x.txt'
            path.return_value.write_text.side_effect = OSError(m)
            path.return_value.suffix = '.txt'
            app.collected_pages = [{'url': PAGE, 'title': raw('List'), 'text': 'Body', 'markdown': 'Body', 'links': []}]
            app.export_collected_text()
        self.assertShown(dialogs.showerror.call_args.args[1], shown, lang, 'save dialog')
        with mock.patch('ui_settings.filedialog') as files, mock.patch.object(ui_settings, 'check_cookie_file',
                                                                              side_effect=ValueError(m)), \
                mock.patch('ui_settings.messagebox') as dialogs:
            files.askopenfilename.return_value = 'cookies.txt'
            app.choose_cookies()
        self.assertShown(dialogs.showwarning.call_args.args[1], shown, lang, 'cookies dialog')
        with mock.patch.object(updater, 'restore_bundled', side_effect=updater.UpdateError(m)), \
                mock.patch('ui_settings.messagebox') as dialogs:
            dialogs.askyesno.return_value = True
            app.restore_engine()
        self.assertShown(dialogs.showwarning.call_args.args[1], shown, lang, 'restore dialog')
        with mock.patch.object(app.library, 'history', side_effect=RuntimeError(m)):
            app.refresh_library()
        self.assertShown(ui_theme.full_text(app.library_warning_label), i18n.t('library.unreadable', error=shown), lang,
                         'library.unreadable (refresh_library)')
        with mock.patch.object(app.library, 'history', side_effect=RuntimeError(m)):
            app.refresh_history()
        self.assertShown(ui_theme.full_text(app.library_warning_label), i18n.t('library.unreadable', error=shown), lang,
                         'library.unreadable (refresh_history)')
        app.show_library_problem('')
        return checked + 10

    def test_en(self):
        self.run_matrix('en')

    def test_zh(self):
        self.run_matrix('zh')


# ============================================================================ subscriptions (C-F40, C-F41, hint, log)
class SubscriptionTests(AppCase):
    def test_check_titles_names_errors_and_the_raw_collect_title(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(f'messages-ui-subscription-{lang}')
                store = app.library
                untitled = store.subscription('', OPTIONS)
                named = store.subscription('Night feed', OPTIONS)
                rows = {row['id']: row for row in store.subscriptions()}
                name = messages.show(rows[untitled]['name'])
                self.assertEqual(name, i18n.t('value.subscription.untitled'))
                # C-F40: the job title is a message holding the decoded name; the hint and the note show it
                with mock.patch.object(app, 'launch_collection') as launch:
                    app.check_subscription(rows[untitled], automatic=True)
                title = launch.call_args.kwargs['title']
                self.assertEqual((title.key, title.params['name']), ('value.job_title.subscription', rows[untitled]['name']))
                self.assertEqual(messages.show(title), i18n.t('value.job_title.subscription', name=name))
                self.assertEqual(ui_theme.full_text(app.link_hint), i18n.t('download.hint.subscription_check', name=name))
                self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.background', name=name))
                app.refresh_collect_note()
                # the stored job: create_job with that title (the real launch, the runner stubbed)
                with mock.patch('collection_ui.CollectionRunner') as runner, mock.patch.object(app, 'set_busy'):
                    runner.return_value.run = lambda options: None
                    app.active_subscription = named
                    self.assertTrue(app.launch_collection(CollectOptions([PAGE]),
                                                          title=msg('value.job_title.subscription',
                                                                    name=rows[named]['name'])))
                    job = app.collection_job
                    app.collection_job = None
                    app.run_token += 1
                stored = store.job(job)
                self.assertEqual((stored['title'].key, stored['title'].origin), ('value.job_title.subscription', 'stored'))
                self.assertEqual(library_ui.history_title(stored), i18n.t('value.job_title.subscription', name='Night feed'))
                # the collect-job title without a title: the first start link, raw user data (3.3)
                with mock.patch('collection_ui.CollectionRunner') as runner, mock.patch.object(app, 'set_busy'), \
                        mock.patch.object(store, 'create_job', wraps=store.create_job) as created:
                    runner.return_value.run = lambda options: None
                    app.active_subscription = None
                    self.assertTrue(app.launch_collection(CollectOptions([PAGE + '/start'])))
                    job = app.collection_job
                    app.collection_job, app.operation = None, None
                    app.run_token += 1
                stored = store.job(job)
                self.assertIsInstance(created.call_args.args[1], messages.Raw)
                self.assertEqual(stored['title'], PAGE + '/start')
                with sqlite3.connect(store.path) as db:
                    data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
                self.assertNotIn('msgs', data, 'an ordinary link needs no sidecar entry')
                # C-F41: a check that cannot start stores the error's message; log line and dialog show it
                error = msg('core.collect.bad_address')
                with mock.patch.object(library_ui, 'CollectOptions', side_effect=ValueError(error)), \
                        mock.patch('library_ui.messagebox') as dialogs:
                    app.check_subscription(rows[named])
                self.assertEqual(dialogs.showwarning.call_args.args[1], messages.show(error))
                self.assertIn(i18n.t('log.subscription.failed', name='Night feed', error=messages.show(error)),
                              self.logged(app))
                last = next(row for row in store.subscriptions() if row['id'] == named)['data']['last_error']
                self.assertEqual((last.key, last.origin), ('core.collect.bad_address', 'stored'))
                app.refresh_library()
                app.subscription_tree.selection_set(named)
                app.subscription_selection()
                self.assertTrue(ui_theme.full_text(app.subscription_note).endswith(messages.show(error)))
                if lang == 'en':
                    self.assertEqual(chinese(ui_theme.full_text(app.subscription_note)), [])
                self.close(app)


# ============================================================================ consumer census
class CensusTests(AppCase):
    """collect_status, collect_error, collect_page, update_status, update_error and
    Library.collection_event fed a Message, a Raw and a plain str (MESSAGES.md 7.5)."""

    MESSAGE = msg('core.collect.http_status', status=503)

    def forms(self):
        text = messages.zh(self.MESSAGE)
        return (('message', self.MESSAGE, contextlib.nullcontext), ('raw', raw(text), lambda: messages.exempt(CENSUS)),
                ('plain', text, lambda: messages.exempt(CENSUS)))

    def widget_state(self, app, item, guard):
        state = {}
        with guard():
            app.handle('collect_status', item)
            state['collect_status'] = ui_theme.full_text(app.collect_status)
            app.handle('collect_error', item)
            state['collect_error'] = ui_theme.full_text(app.collect_status)
            app.collected_links.clear()
            app.collected_pages.clear()
            app.handle('collect_page', {'url': PAGE, 'title': item, 'text': '', 'markdown': '', 'next': None,
                                        'links': [{'url': A + '.mp4', 'title': item, 'kind': '媒体地址', 'source': PAGE}]})
            state['collect_page'] = app.collect_tree.full_values(app.collect_tree.get_children()[0])[1]
            app.busy, app.operation = True, 'update'
            try:
                app.on_update_status(item)
                state['update_status'] = app.update_status.cget('text')
                app.on_update_error(item)
                state['update_error'] = app.update_status.cget('text')
            finally:
                app.busy, app.operation = False, None
            state['log'] = self.logged(app).splitlines()[-3:]
        return state

    def stored_state(self, store, item, guard):
        with guard():
            job = store.create_job('collect', raw(PAGE), OPTIONS)
            store.collection_event(job, 'collect_page', {'url': PAGE, 'title': item, 'text': '', 'markdown': '',
                                                         'next': None, '_checkpoint': None,
                                                         'links': [{'url': A, 'title': item, 'kind': '媒体地址',
                                                                    'source': PAGE}]})
            store.collection_event(job, 'collect_error', item)
        with sqlite3.connect(store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
            page = json.loads(db.execute('SELECT data FROM pages WHERE job=?', (job,)).fetchone()[0])
        data.pop('msgs', None)
        page.pop('msgs', None)
        return json.dumps(data['errors'], ensure_ascii=False), json.dumps(page, ensure_ascii=False)

    def test_census(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(f'messages-ui-census-{lang}')
                states = {name: self.widget_state(app, item, guard) for name, item, guard in self.forms()}
                stored = {name: self.stored_state(app.library, item, guard) for name, item, guard in self.forms()}
                self.assertEqual(stored['message'], stored['raw'], 'the stored bytes of the old fields')
                self.assertEqual(stored['message'], stored['plain'])
                text = messages.zh(self.MESSAGE)
                if lang == 'zh':
                    self.assertEqual(states['message'], states['raw'])
                    self.assertEqual(states['message'], states['plain'])
                    self.assertEqual(states['message']['collect_status'], text)
                else:
                    shown = i18n.t('core.collect.http_status', status=503)
                    self.assertEqual(states['message']['collect_status'], shown)
                    self.assertEqual(states['message']['collect_page'], shown)
                    self.assertEqual(states['message']['update_status'], shown)
                    self.assertEqual(states['message']['update_error'], i18n.t('settings.update.failed', error=shown))
                    for name in ('raw', 'plain'):
                        self.assertEqual(states[name]['collect_status'], text, 'shown as it is')
                        self.assertEqual(states[name]['collect_page'], text)
                        self.assertEqual(states[name]['update_error'], i18n.t('settings.update.failed', error=text))
                self.close(app)


# ============================================================================ legacy and 1.8.0 rows
WRITER = r'''
import json, os, sys
src, version, db, root, out = sys.argv[1:6]
sys.path.insert(0, src)
import library
A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE = 'https://example.invalid/list-' + version.replace('.', '-')
MEDIA = 'https://cdn.example.invalid/clip-' + version.replace('.', '-') + '.mp4'
REQUEST = 'https://cdn.example.invalid/hls-' + version.replace('.', '-') + '.m3u8'
LONG = %(long)r
OPTIONS = {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1}
store = library.Library(db)
ids = {}
if version == '1.5.1':
    ids['placeholder'] = store.create_job('download', '3条 · 音频 · MP3 320K',
                                          {'urls': [A, B, C], 'mode': '音频 · MP3 320K', 'output': root})
    store.download_event(ids['placeholder'], 'done', {'cancelled': False, 'failed': 1})
    sub = store.subscription('', OPTIONS)
    ids['subscription'] = sub
    ids['subscription_job'] = store.create_job('collect', '订阅：未命名订阅', OPTIONS, sub)
    page = {'url': PAGE, 'title': '网页', 'text': 'Body', 'markdown': 'Body', 'next': None,
            'links': [{'url': MEDIA, 'title': '网页', 'kind': '媒体地址', 'source': PAGE}]}
    store.collection_event(ids['subscription_job'], 'collect_page', dict(page, _checkpoint={'visited': [PAGE], 'todo': []}))
    store.collection_event(ids['subscription_job'], 'collect_error', '页面超过8MB，请缩小采集范围。')
    store.change_job(ids['subscription_job'], status='partial')
    store.finish_subscription(sub, [page], True)
    store.finish_subscription(sub, [], False, '网站返回HTTP 503，可尝试动态模式或稍后重试。；页面超过8MB，请缩小采集范围。')
    ids['collect'] = store.create_job('collect', PAGE, OPTIONS)
    store.change_job(ids['collect'], status='completed')
    store.archive_index(os.path.join(root, 'archive-1-5-1', 'page'),
                        {'url': MEDIA, 'source': PAGE, 'title': '网页', 'uploader': '', 'files': [], 'transcripts': []})
else:
    import engine
    ids['titled_form'] = store.create_job('download', '1 条 · 音频 · FLAC', {'urls': [A], 'mode': '音频 · FLAC', 'output': root})
    store.download_event(ids['titled_form'], 'item', {'url': A})
    store.download_event(ids['titled_form'], 'title', '订阅：Fake Channel Live' if version == '1.6.0' else '3 条 · 现场版')
    # R4: a cookies error whose reason is explain_error's 70-character fallback (the D-H-2 rows), the
    # error text made by the version's own code (engine.check_cookie_file and the item's f-string)
    try:
        engine.check_cookie_file(LONG)
        raise SystemExit('the cookies file must be missing')
    except (ValueError, OSError) as failure:
        error = f'此链接无法开始：{failure}'
    store.download_event(ids['titled_form'], 'item_done', {'success': False, 'url': A, 'error': error,
                                                           'reason': engine.explain_error(error)})
    store.download_event(ids['titled_form'], 'done', {'cancelled': False, 'failed': 1})
    ids['multi'] = store.create_job('download', '2 条 · 音频 · MP3 320K', {'urls': [A, B], 'mode': '音频 · MP3 320K',
                                                                          'output': root})
    store.download_event(ids['multi'], 'item', {'url': A})
    store.download_event(ids['multi'], 'title', 'Some Song' if version == '1.6.0' else 'Live set')
    line = 'ERROR: [generic] abc: HTTP Error 404: Not Found'
    store.download_event(ids['multi'], 'item_done', {'success': False, 'url': A, 'error': line,
                                                     'reason': engine.explain_error(line)})
    store.download_event(ids['multi'], 'done', {'cancelled': False, 'failed': 1})
    ids['placeholder'] = store.create_job('download', '2 条 · 音频 · 原始最佳', {'urls': [A, B], 'mode': '音频 · 原始最佳',
                                                                              'output': root})
    store.download_event(ids['placeholder'], 'done', {'cancelled': False, 'failed': 1})
    ids['no_link'] = store.create_job('download', '1 条 · 视频 · 最佳画质 MKV', {'urls': [], 'mode': '视频 · 最佳画质 MKV',
                                                                             'output': root})
    ids['collect'] = store.create_job('collect', PAGE, dict(OPTIONS, dynamic=True))
    page = {'url': PAGE, 'title': '媒体直链', 'text': '', 'markdown': '', 'next': None,
            'links': [{'url': MEDIA, 'title': '媒体直链', 'kind': '媒体地址', 'source': PAGE},
                      {'url': REQUEST, 'title': '动态页面媒体请求', 'kind': '媒体地址', 'source': PAGE}]}
    store.collection_event(ids['collect'], 'collect_page', dict(page, _checkpoint={'visited': [PAGE], 'todo': []}))
    store.collection_event(ids['collect'], 'collect_error', '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。')
    store.change_job(ids['collect'], status='partial')
    sub = store.subscription('Feed ' + version, OPTIONS)
    ids['subscription'] = sub
    store.finish_subscription(sub, [page], True)
    store.finish_subscription(sub, [], False, '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；网站返回 HTTP 503，可尝试动态模式或稍后重试。')
    store.archive_index(os.path.join(root, 'archive-' + version.replace('.', '-'), 'request'),
                        {'url': REQUEST, 'source': PAGE, 'title': '动态页面媒体请求', 'uploader': '', 'files': [],
                         'transcripts': []})
with open(out, 'w', encoding='utf-8') as handle:
    json.dump(ids, handle)
''' % {'long': LONG}

RENDER_1_7_0 = r'''
import json, sys
src, db, out = sys.argv[1:4]
sys.path.insert(0, src)
import i18n
i18n.set_language('zh')
import library
import library_ui
store = library.Library(db)
result = {'history': {}, 'notes': {}, 'subscriptions': {}, 'pending': {}, 'archives': {}}
for row in store.history(1000):
    result['history'][row['id']] = list(library_ui.history_row(row)[:3])
    result['notes'][row['id']] = library_ui.LibraryUI.history_note_text(store.job(row['id']))
for row in store.subscriptions():
    cells = library_ui.subscription_row(row)
    result['subscriptions'][row['id']] = [cells[0], cells[1], cells[3]]
    result['pending'][row['id']] = [i18n.localize(item['title'], keys=library_ui.PAGE_TITLE_KEYS)
                                    for item in store.pending(row['id'])]
for row in store.archives():
    result['archives'][row['folder']] = i18n.localize(row['title'], keys=library_ui.PAGE_TITLE_KEYS)
with open(out, 'w', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False)
'''


def child_environment():
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', YTDLP_DESKTOP_LANG='zh')
    environment.pop(_sandbox.MSG_GUARD, None)           # the old code has no messages module
    return environment


def write_rows(folder):
    """A library with the rows 1.5.1, 1.6.0 and 1.7.0 wrote (their frozen Library, in that order on one
    file, as the owner's upgrades did) and 1.8.0 rows with sidecars; {version: ids}."""
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / 'test-library.sqlite3'
    script = folder / 'writer.py'
    script.write_text(WRITER, encoding='utf-8')
    ids = {}
    for version in ('1.5.1', '1.6.0', '1.7.0'):
        out = folder / f'ids-{version}.json'
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(_sandbox.release_src(version)), version,
                               str(db), str(folder), str(out)], cwd=str(folder), env=child_environment(),
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        if done.returncode:
            raise AssertionError(f'{version} writer failed:\n{done.stderr[-4000:]}')
        ids[version] = json.loads(out.read_text(encoding='utf-8'))
    store = library.Library(db)
    new = {}
    sub = store.subscription('Night feed', OPTIONS)
    new['subscription'] = sub
    new['subscription_job'] = store.create_job('collect', msg('value.job_title.subscription',
                                                              name=store.subscriptions()[-1]['name']), OPTIONS, sub)
    page = {'url': PAGE, 'title': msg('value.link_kind.direct'), 'text': '', 'markdown': '', 'next': None,
            'links': [{'url': A + '.mp4', 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE},
                      {'url': A + '.m3u8', 'title': msg('value.link_kind.dynamic_request'), 'kind': '媒体地址',
                       'source': PAGE}]}
    store.collection_event(new['subscription_job'], 'collect_page', dict(page, _checkpoint={'visited': [PAGE], 'todo': []}))
    for error in (msg('core.collect.http_status', status=503), msg('core.collect.too_large')):
        store.collection_event(new['subscription_job'], 'collect_error', error)
    store.change_job(new['subscription_job'], status='partial')
    store.finish_subscription(sub, [page], True)
    store.finish_subscription(sub, [], False, collection_ui.subscription_result(store.job(new['subscription_job']),
                                                                                 False)[2])
    new['download'] = store.create_job('download', msg('value.job_title.placeholder', count=2,
                                                       mode=value('mode', formats.MODES[1])),
                                       {'urls': [A, B], 'output': str(folder), 'mode': formats.MODES[1]})
    store.download_event(new['download'], 'item', {'url': A})
    store.download_event(new['download'], 'title', 'Clip A')
    error = cut(msg('core.engine.cannot_start_link', error=msg('core.cookies.missing', file=LONG)), 200)
    store.download_event(new['download'], 'item_done', {'success': False, 'url': A, 'error': error,
                                                        'reason': engine.explain_error(error)})
    store.download_event(new['download'], 'done', {'cancelled': False, 'failed': 1})
    new['placeholder'] = store.create_job('download', msg('value.job_title.placeholder', count=1,
                                                          mode=value('mode', formats.MODES[2])),
                                          {'urls': [], 'output': str(folder), 'mode': formats.MODES[2]})
    store.archive_index(str(folder / 'archive-1-8-0' / 'request'),
                        {'url': A + '.m3u8', 'source': PAGE, 'title': msg('value.link_kind.dynamic_request'),
                         'uploader': '', 'files': [], 'transcripts': []})
    ids['1.8.0'] = new
    return db, ids


def rendered_by_1_7_0(db, folder):
    copy = folder / 'render-1-7-0.sqlite3'
    shutil.copyfile(db, copy)
    script, out = folder / 'render.py', folder / 'render-1-7-0.json'
    script.write_text(RENDER_1_7_0, encoding='utf-8')
    done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(_sandbox.release_src('1.7.0')), str(copy),
                           str(out)], cwd=str(folder), env=child_environment(), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=300)
    if done.returncode:
        raise AssertionError(f'1.7.0 render failed:\n{done.stderr[-4000:]}')
    return json.loads(out.read_text(encoding='utf-8'))


# Media titles that read like app-made forms (CR2-2): user data, shown as they are in both languages.
USER_TITLES = ('订阅：Fake Channel Live', '3 条 · 现场版')


class StoredRowsTests(AppCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = _sandbox.out('messages-ui-rows', 'library')
        cls.db, cls.ids = write_rows(cls.folder)
        cls.old = rendered_by_1_7_0(cls.db, cls.folder)

    def app_on_rows(self, lang, name):
        output = _sandbox.out(f'messages-ui-rows-{name}-{lang}', 'x').parent
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.db, output / 'test-library.sqlite3')
        # the history list shows the CR2-2 media titles (user data that reads like app forms)
        with _sandbox.fixture_texts('media titles that read like app forms (CR2-2) are user data'):
            app = self.make_app(output.name, size='1000x740')
            app.history_limit = 1000
            app.refresh_library()
        return app

    def page_rendering(self, app):
        store = app.library
        result = {'history': {}, 'notes': {}, 'subscriptions': {}, 'pending': {}, 'archives': {}}
        for row in store.history(1000):
            result['history'][row['id']] = list(library_ui.history_row(row)[:3])
            result['notes'][row['id']] = library_ui.LibraryUI.history_note_text(store.job(row['id']))
        for row in store.subscriptions():
            cells = library_ui.subscription_row(row)
            result['subscriptions'][row['id']] = [cells[0], cells[1], cells[3]]
            result['pending'][row['id']] = [messages.show(item['title']) for item in store.pending(row['id'])]
        for row in store.archives():
            result['archives'][row['folder']] = messages.show(row['title'])
        return result

    def test_zh_rows_read_exactly_as_1_7_0_shows_them(self):
        with _sandbox.language('zh'):
            app = self.app_on_rows('zh', 'parity')
            with _sandbox.fixture_texts('stored rows of old versions and media titles that read like app forms'):
                mine = self.page_rendering(app)
            for part in ('history', 'notes', 'subscriptions', 'pending', 'archives'):
                self.assertEqual(mine[part], self.old[part], part)
            self.assertEqual(len(mine['history']), 3 + 2 * 5 + 3)

    def test_en_history_titles_cr2_2_and_notes_r4(self):
        display = urls.display
        with _sandbox.language('en'):
            app = self.app_on_rows('en', 'titles')
            store = app.library
            with _sandbox.fixture_texts('media titles that read like app forms (CR2-2) are user data'):
                titles = {row['id']: library_ui.history_title(row) for row in store.history(1000)}
            old, mid, new = self.ids['1.5.1'], self.ids['1.7.0'], self.ids['1.8.0']
            expected = {
                old['placeholder']: i18n.t('value.job_title.multi', title=display(A), count=3),
                old['subscription_job']: i18n.t('value.job_title.subscription', name=i18n.t('value.subscription.untitled')),
                old['collect']: 'https://example.invalid/list-1-5-1',
                self.ids['1.6.0']['titled_form']: '订阅：Fake Channel Live',
                self.ids['1.6.0']['multi']: i18n.t('value.job_title.multi', title='Some Song', count=2),
                mid['titled_form']: '3 条 · 现场版',
                mid['multi']: i18n.t('value.job_title.multi', title='Live set', count=2),
                mid['placeholder']: i18n.t('value.job_title.multi', title=display(A), count=2),
                mid['no_link']: i18n.t('value.job_title.placeholder', count=1,
                                       mode=i18n.label('mode', '视频 · 最佳画质 MKV')),
                new['subscription_job']: i18n.t('value.job_title.subscription', name='Night feed'),
                new['download']: i18n.t('value.job_title.multi', title='Clip A', count=2),
                new['placeholder']: i18n.t('value.job_title.placeholder', count=1,
                                           mode=i18n.label('mode', formats.MODES[2])),
            }
            for job, title in expected.items():
                self.assertEqual(titles[job], title, job)
            for job, title in titles.items():
                if title not in USER_TITLES:
                    self.assertEqual(chinese(title), [], title)
            # R4: the stored 70-character reason of a cookies error reads as the whole English message
            done = i18n.t('library.history.downloaded', completed=0)
            whole = i18n.t('core.engine.cannot_start_link', error=i18n.t('core.cookies.missing', file=LONG))
            for version in ('1.6.0', '1.7.0'):
                note = library_ui.LibraryUI.history_note_text(store.job(self.ids[version]['titled_form']))
                self.assertEqual(note, done + i18n.t('library.history.last_error', error=whole[:130]), version)
            record = store.job(new['download'])
            self.assertEqual(record['data']['last_reason']['reason'].key, 'core.engine.cannot_start_link')
            self.assertEqual(len(messages.zh(record['data']['last_reason']['reason'])), 70, 'the stored reason is cut')
            note = library_ui.LibraryUI.history_note_text(record)
            self.assertEqual(note, done + i18n.t('library.history.last_error', error=whole[:130]), '1.8.0')
            for row in store.history(1000):
                note = library_ui.LibraryUI.history_note_text(store.job(row['id']))
                self.assertEqual(chinese(note), [], note)
            with _sandbox.language('zh'):
                with _sandbox.fixture_texts('stored rows of old versions'):
                    zh_note = library_ui.LibraryUI.history_note_text(store.job(self.ids['1.7.0']['titled_form']))
                self.assertEqual(zh_note, self.old['notes'][self.ids['1.7.0']['titled_form']])

    def test_en_pages_with_stored_data_show_no_chinese_outside_user_data(self):
        with _sandbox.language('en'):
            app = self.app_on_rows('en', 'pages')
            store = app.library
            failures = []

            def check(where, root):
                failures.extend(f'{where}: {text!r}' for text in texts_of(root)
                                if page_chinese(text) and text not in USER_TITLES)

            app.show_page('library')
            pump(app, 0.2)
            with _sandbox.fixture_texts('media titles that read like app forms (CR2-2) are user data'):
                for job in [row['id'] for row in store.history(1000)]:
                    app.library_tabs.set(library_ui.TAB_HISTORY)
                    app.history_tree.selection_set(job)
                    app.history_selection()
                    pump(app, 0.02)
                    check(f'history {job}', app.pages['library'])
                for row in store.subscriptions():
                    app.library_tabs.set(library_ui.TAB_SUBSCRIPTIONS)
                    app.subscription_tree.selection_set(row['id'])
                    app.subscription_selection()
                    pump(app, 0.02)
                    check(f'subscription {row["name"]}', app.pages['library'])
                app.library_tabs.set(library_ui.TAB_ARCHIVE)
                pump(app, 0.05)
                check('archive', app.pages['library'])
                # collect results: legacy records loaded from the history, then a live 1.8.0 page
                for version, name in (('1.5.1', 'subscription_job'), ('1.6.0', 'collect'), ('1.7.0', 'collect')):
                    app.load_collection_record(store.job(self.ids[version][name]))
                    app.collect_filter.set(collection_ui.FILTERS[1])
                    pump(app, 0.02)
                    check(f'collect {version}', app.pages['collect'])
                app.collected_links.clear()
                app.collected_pages.clear()
                direct = msg('value.link_kind.direct')
                app.handle('collect_page', {'url': PAGE, 'title': direct, 'text': '', 'markdown': '', 'next': None,
                                            'links': [{'url': A + '.mp4', 'title': direct, 'kind': '媒体地址', 'source': PAGE},
                                                      {'url': A + '.m3u8', 'title': msg('value.link_kind.dynamic_request'),
                                                       'kind': '媒体地址', 'source': PAGE}]})
                app.handle('collect_error', msg('core.collect.needs_chrome'))
                pump(app, 0.02)
                check('collect live', app.pages['collect'])
                app.show_page('settings')
                pump(app, 0.1)
                check('settings', app.pages['settings'])
            self.assertEqual(failures, [])
            shown = [app.collect_tree.full_values(iid)[1] for iid in app.collect_tree.get_children()]
            self.assertEqual(shown, [i18n.t('value.link_kind.direct'), i18n.t('value.link_kind.dynamic_request')])


# ============================================================================ 6.2b dialogs in en
class DialogTests(AppCase):
    def test_the_6_2b_sites_in_english(self):
        error = msg('core.archive.cannot_set_aside', record='x.json')
        with _sandbox.language('en'):
            shown = i18n.t('core.archive.cannot_set_aside', record='x.json')
            app = self.make_app('messages-ui-dialogs')
            # library.unreadable with an exception that carries a message (library_ui 639, 787)
            with mock.patch.object(app.library, 'history', side_effect=OSError(error)):
                app.refresh_library()
            self.assertEqual(ui_theme.full_text(app.library_warning_label), i18n.t('library.unreadable', error=shown))
            app.show_library_problem('')
            # the OS-error dialogs (collection_ui 622, 643; library_ui 1306)
            items = [{'url': A, 'title': raw('A'), 'kind': '媒体地址', 'source': PAGE}]
            with mock.patch.object(app, 'selected_collection_links', return_value=items), \
                    mock.patch('collection_ui.filedialog') as files, mock.patch('collection_ui.Path') as path, \
                    mock.patch('collection_ui.messagebox') as dialogs:
                files.asksaveasfilename.return_value = 'x.txt'
                path.return_value.write_text.side_effect = OSError(error)
                app.export_collected_links()
            self.assertEqual(dialogs.showerror.call_args.args[1], shown)
            with mock.patch.object(app, 'selected_archive', return_value={'folder': 'x'}), \
                    mock.patch('library_ui.os.startfile', side_effect=OSError(error), create=True), \
                    mock.patch('library_ui.messagebox') as dialogs:
                app.open_archive()
            self.assertEqual(dialogs.showerror.call_args.args[1], shown)
            with mock.patch.object(app, 'selected_archive', return_value={'folder': str(_sandbox.out('a', 'b', 'c'))}), \
                    mock.patch('library_ui.filedialog') as files, mock.patch('library_ui.Archive') as archive, \
                    mock.patch('library_ui.messagebox') as dialogs:
                files.askopenfilename.return_value = 'notes.txt'
                archive.return_value.attach.side_effect = ValueError(error)
                app.attach_transcript()
            self.assertEqual(dialogs.showerror.call_args.args[1], shown)
            # log.library.busy: the lock error as text (library_ui 673)
            app.library_busy_logged = False
            app.library_busy(sqlite3.OperationalError('database is locked'))
            self.assertIn(i18n.t('log.library.busy', error='database is locked'), self.logged(app))
            app.library_settled()
            busy = [line for line in self.logged(app).splitlines() if line == i18n.t('log.library.busy', error='database is locked')]
            self.assertEqual(len(busy), 1)
            self.assertEqual(chinese(busy[0]), [])

    def test_the_restart_failed_dialog_shows_the_error_text(self):
        """ui_settings 414: dialog.restart_failed.text with the error's text (a non-smoke app; the
        launcher is replaced, nothing is started)."""
        error = OSError(msg('core.unfinished.cannot_set_aside', path='x.json'))
        with _sandbox.language('en'):
            _sandbox.write_settings(output=str(_sandbox.out('messages-ui-restart')), batch_safe=False)
            from studio import StudioApp
            app = StudioApp(_sandbox.app_args())
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            self.addCleanup(self.close, app)
            pump(app, 0.2)

            def refuse(command, environment):
                raise error
            app.restart_launcher = refuse
            with mock.patch('ui_settings.messagebox') as dialogs:
                app.restart_app()
            text = dialogs.showerror.call_args.args[1]
            self.assertEqual(text, i18n.t('dialog.restart_failed.text',
                                          error=i18n.t('core.unfinished.cannot_set_aside', path='x.json')))
            self.assertEqual(chinese(text), [])


if __name__ == '__main__':
    unittest.main()
