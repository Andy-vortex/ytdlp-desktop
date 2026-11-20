"""1.8.0 collect, library and settings pages (MESSAGES.md 6, 7.5, 7.9; WP-5).

    python -X utf8 -B test_messages_ui.py

The three UI modules are converted (WP-6 removed the staging flag MESSAGES_READY) and clean under
G1 and G5; the 1.7.0 code that 6.4 deletes is gone (the key-prefix lists, stored_text, every localize call, library.PAGE_TITLE_KEYS)
and the ids keep their values.  G4: every core.* / value.* / download.reason.* key through every
channel of these pages, live and as a stored record (en: no Chinese, zh: the
message's own zh text in the 1.7.0 composition).  The consumer census of the collect_* and
update_* events and of Library.collection_event (a Message, a Raw and a plain str: no exception,
the same zh state; en: the key rendered, the other two as they are; see below for the stored forms).  C-F39 /
C-F40 / C-F41 and the raw title of a collect job.  History titles (CR2-2) and notes (R4 rows) in
English for stored rows.  The 6.2b dialogs in en.  The three pages in en with that data show no Chinese
outside user data.  Smoke apps, never on screen; no network; no real engine.

(2.0.0 S2 P2) The library is format 2: the stored legs write v2 records (a job's title column holds
text only, so it is no message channel any more; a subscription check's job is via='subscription');
the census pins the format-2 forms (a message as {"k", "p"}, a Raw or plain text as the text).  (S2 P4)
The rows of 1.5.1 / 1.6.0 / 1.7.0 written by their frozen Library in child processes and compared with
the frozen 1.7.0 page functions are gone (2.0.0 does not read older stores); the stored rows here are
the ones this tree writes.
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
from messages import Message, cut, msg, raw
from _sandbox import zh_t

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
FORMATTER = string.Formatter()
MODULES = ('collection_ui', 'library_ui', 'ui_settings')
SRC = Path(library.__file__).resolve().parent
A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE = 'https://example.invalid/list'
NUMERIC = {'count', 'code', 'page', 'status', 'length', 'seconds', 'attempt', 'limit', 'line', 'pages', 'links',
           'dropped', 'new', 'pending', 'total', 'shown', 'completed', 'errors', 'percent', 'added', 'skipped'}
LONG = 'D:\\Media\\' + 'a' * 60 + '\\cookies.txt'
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
            params[name] = msg('format.' + formats.MODES[1])   # (2.0.0 S2 P4) no Value; (S4 A1) one name
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
        elif isinstance(widget, ui_theme.PathButton) and widget.path:
            pass                                        # (2.0.0 S4) a path is user data
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
            for text, entry in allow['sinks'].items():
                self.assertTrue(entry.get('reason'), (module, text))
        self.assertEqual(set(i18n_tool.load_allow('library_ui')['raises']), {'the history job was never written', 'no options'},
                         'G1e: the two English internal raises of library_ui stay')

    def test_the_deleted_code_is_gone_and_the_ids_keep_their_values(self):
        # (S3 review fix) the localize line count went with the WP-6 leftovers: i18n has no localize (C12)
        for module, names in ((collection_ui, ('PAGE_TITLE_KEYS', 'stored_text')),
                              (library_ui, ('JOB_TITLE_KEYS', 'NAME_KEYS', 'TITLED_JOB_KEYS', 'UNTITLED_JOB_KEYS',
                                            'COLLECT_JOB_KEYS', 'PAGE_TITLE_KEYS', 'stored_text')),
                              (library, ('PAGE_TITLE_KEYS', '_shown_title'))):
            for name in names:
                self.assertFalse(hasattr(module, name), f'{module.__name__}.{name}')
        for module in MODULES:
            source = (SRC / f'{module}.py').read_text(encoding='utf-8')
            self.assertNotIn('localize(', source, module)
        self.assertEqual(collection_ui.WEB_LINK, 'web')
        self.assertEqual((library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE),
                         ('history', 'subscriptions', 'archive'))
        for constant in (collection_ui.WEB_LINK, library_ui.TAB_HISTORY):
            self.assertNotIsInstance(constant, Message, 'ids and compared stored values are not messages')

    def test_c_f39_the_subscription_result(self):
        errors = [msg('core.collect.http_status', status=503), msg('core.collect.too_large'), msg('core.collect.no_match')]
        record = {'status': 'partial', 'pages': [{'url': PAGE}], 'data': {'errors': errors}}
        pages, successful, error = collection_ui.subscription_result(record, False)
        self.assertEqual((pages, successful), ([{'url': PAGE}], False))
        self.assertEqual(error.key, 'common.clauses')
        self.assertEqual(error, messages.zh(errors[1]) + i18n.JOIN + messages.zh(errors[2]),
                         'the 1.7.0 join of the last two')
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
            # -------------------------------------------------- stored legs
            jobs, downloads, folders = {}, {}, {}
            pending_sub = store.subscription(raw('Pending feed'), OPTIONS, 3600, True, True)
            note_sub = store.subscription(raw('Note feed'), OPTIONS, 3600, True, True)
            page_links = []
            for index, key in enumerate(all_keys):
                m = samples[key]
                job = store.create_job('collect', raw(f'Page {index}'), dict(OPTIONS, urls=[f'{PAGE}/{index}']))
                store.change_job(job, status='partial', errors=[{'error': m, 'reason': None}])
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
                page_links.append({'url': f'https://cdn.example.invalid/{index}.mp3', 'title': m, 'kind': 'media',
                                   'source': PAGE})
            store.finish_subscription(pending_sub, [{'url': PAGE, 'title': raw('List'), 'links': page_links}], True)
            app.refresh_library()
            for key in all_keys:
                m = samples[key]
                shown = messages.show(m)
                with self.subTest(lang=lang, key=key, leg='stored'):
                    record = store.job(jobs[key])
                    self.assertEqual(record['data']['errors'][0]['error'].key, key)
                    done = i18n.t('library.history.downloaded', completed=0)
                    self.assertShown(library_ui.LibraryUI.history_note_text(record),
                                     i18n.t('library.history.pages', pages=0, done=done)
                                     + i18n.t('library.history.last_error', error=shown[:130]), lang, 'history note')
                    self.assertShown(library_ui.LibraryUI.history_note_text(store.job(downloads[key])),
                                     done + i18n.t('library.history.last_error', error=shown[:130]), lang,
                                     'download note (errors[-1].reason)')
                    checked += 2
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
                self.assertShown(note, messages.show(m), lang, f'subscription note {key}')
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
                'links': [{'url': media, 'title': m, 'kind': 'media', 'source': PAGE}]}
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
        # history note and subscription name (the page functions); (2.0.0) a job's title column holds
        # text only (no message channel)
        done = i18n.t('library.history.downloaded', completed=0)
        self.assertShown(library_ui.LibraryUI.history_note_text({'kind': 'collect', 'pages': [], 'data': {
            'errors': [{'error': m, 'reason': None}]}}),
                         i18n.t('library.history.pages', pages=0, done=done)
                         + i18n.t('library.history.last_error', error=shown[:130]), lang, 'history note')
        cells = library_ui.subscription_row({'name': m, 'enabled': True, 'interval': 900, 'data': {'last_check': 0},
                                             'pending': 0, 'next_check': time.time() + 3600})
        self.assertShown(cells[0], shown, lang, 'subscription name')
        checked += 2
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
        self.assertShown(app.library_notice.full_text, i18n.t('library.unreadable', error=shown), lang,
                         'library.unreadable (refresh_library)')
        with mock.patch.object(app.library, 'history', side_effect=RuntimeError(m)):
            app.refresh_history()
        self.assertShown(app.library_notice.full_text, i18n.t('library.unreadable', error=shown), lang,
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
                # (2.0.0) the job is the subscription's (via) with its stored name ('' here); the hint and the
                # note show the name
                with mock.patch.object(app, 'launch_collection') as launch:
                    app.check_subscription(rows[untitled], automatic=True)
                self.assertEqual((launch.call_args.kwargs['title'], launch.call_args.kwargs['via']), ('', 'subscription'))
                self.assertEqual(app.link_note.cget('text'), i18n.t('download.hint.subscription_check', name=name))
                self.assertEqual(ui_theme.full_text(app.collect_note), i18n.t('collect.background', name=name))
                app.refresh_collect_note()
                # the stored job: create_job with that title (the real launch, the runner stubbed)
                with mock.patch('collection_ui.CollectionRunner') as runner, mock.patch.object(app, 'set_busy'):
                    runner.return_value.run = lambda options: None
                    app.active_subscription = named
                    self.assertTrue(app.launch_collection(CollectOptions([PAGE]), title=rows[named]['name_text'],
                                                          via='subscription'))
                    job = app.collection_job
                    app.collection_job = None
                    app.run_token += 1
                stored = store.job(job)
                self.assertEqual((stored['title'], stored['data']['via']), ('Night feed', 'subscription'))
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
                self.assertEqual(created.call_args.args[1], PAGE + '/start')
                self.assertNotIn('via', stored['data'])
                self.assertEqual(stored['title'], PAGE + '/start')
                with sqlite3.connect(store.path) as db:
                    data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
                self.assertNotIn('msgs', data, 'format 2 has no msgs field')
                # C-F41: a check that cannot start stores the error's message; log line and dialog show it
                error = msg('core.collect.bad_address')
                with mock.patch.object(library_ui, 'CollectOptions', side_effect=ValueError(error)), \
                        mock.patch('library_ui.messagebox') as dialogs:
                    app.check_subscription(rows[named])
                self.assertEqual(dialogs.showwarning.call_args.args[1], messages.show(error))
                self.assertIn(i18n.t('log.subscription.failed', name='Night feed', error=messages.show(error)),
                              self.logged(app))
                last = next(row for row in store.subscriptions() if row['id'] == named)['data']['last_error']
                self.assertEqual(last.key, 'core.collect.bad_address')
                app.refresh_library()
                app.subscription_tree.selection_set(named)
                app.subscription_selection()
                self.assertEqual(ui_theme.full_text(app.subscription_note), messages.show(error))
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
        return (('message', self.MESSAGE, contextlib.nullcontext), ('raw', raw(text), contextlib.nullcontext),
                ('plain', text, contextlib.nullcontext))

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
                                        'links': [{'url': A + '.mp4', 'title': item, 'kind': 'media', 'source': PAGE}]})
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
                                                         'links': [{'url': A, 'title': item, 'kind': 'media',
                                                                    'source': PAGE}]})
            store.collection_event(job, 'collect_error', item)
        with sqlite3.connect(store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
            page = json.loads(db.execute('SELECT data FROM pages WHERE job=?', (job,)).fetchone()[0])
        return json.dumps(data['errors'], ensure_ascii=False), json.dumps(page, ensure_ascii=False)

    def test_census(self):
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                app = self.make_app(f'messages-ui-census-{lang}')
                states = {name: self.widget_state(app, item, guard) for name, item, guard in self.forms()}
                stored = {name: self.stored_state(app.library, item, guard) for name, item, guard in self.forms()}
                # (2.0.0) format 2: the message as {"k", "p"}, a Raw or plain text as the text itself
                text = messages.zh(self.MESSAGE)
                form = {'k': 'core.collect.http_status', 'p': {'status': 503}}
                self.assertEqual(json.loads(stored['message'][0]), [{'error': form, 'reason': None}])
                self.assertEqual(json.loads(stored['message'][1])['title'], form)
                self.assertEqual(json.loads(stored['raw'][0]), [{'error': text, 'reason': None}])
                self.assertEqual(json.loads(stored['raw'][1])['links'][0]['title'], text)
                self.assertEqual(stored['raw'], stored['plain'])
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


# ============================================================================ stored rows
# (2.0.0 S2 P4) The rows of 1.5.1 / 1.6.0 / 1.7.0 (written by their frozen Library in child processes, rendered
# by the frozen 1.7.0 page functions) are gone: 2.0.0 does not read older stores.  What is left: the rows this
# tree writes, shown by these pages.

def write_rows(folder):
    """A library with format-2 rows of every kind these pages show: (db, ids)."""
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / 'test-library.sqlite3'
    store = library.Library(db)
    new = {}
    sub = store.subscription('Night feed', OPTIONS)
    new['subscription'] = sub
    new['subscription_job'] = store.create_job('collect', store.subscriptions()[-1]['name_text'], OPTIONS, sub,
                                               via='subscription')
    page = {'url': PAGE, 'title': msg('value.link_kind.direct'), 'text': '', 'markdown': '', 'next': None,
            'links': [{'url': A + '.mp4', 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE},
                      {'url': A + '.m3u8', 'title': msg('value.link_kind.dynamic_request'), 'kind': 'media',
                       'source': PAGE}]}
    store.collection_event(new['subscription_job'], 'collect_page', dict(page, _checkpoint={'visited': [PAGE], 'todo': []}))
    for error in (msg('core.collect.http_status', status=503), msg('core.collect.too_large')):
        store.collection_event(new['subscription_job'], 'collect_error', error)
    store.change_job(new['subscription_job'], status='partial')
    store.finish_subscription(sub, [page], True)
    store.finish_subscription(sub, [], False, collection_ui.subscription_result(store.job(new['subscription_job']),
                                                                                 False)[2])
    new['untitled_subscription'] = store.subscription('', OPTIONS)
    new['download'] = store.create_job('download', '', {'urls': [A, B], 'output': str(folder), 'mode': formats.MODES[1]})
    store.download_event(new['download'], 'item', {'url': A})
    store.download_event(new['download'], 'title', 'Clip A')
    error = cut(msg('core.engine.cannot_start_link', error=msg('core.cookies.missing', file=LONG)), 200)
    store.download_event(new['download'], 'item_done', {'success': False, 'url': A, 'error': error,
                                                        'reason': engine.explain_error(error)})
    store.download_event(new['download'], 'done', {'cancelled': False, 'failed': 1})
    new['placeholder'] = store.create_job('download', '', {'urls': [], 'output': str(folder), 'mode': formats.MODES[2]})
    new['links'] = store.create_job('download', '', {'urls': [A, B, A + '/c'], 'output': str(folder),
                                                     'mode': formats.MODES[3]})
    store.archive_index(str(folder / 'archive-2-0-0' / 'request'),
                        {'url': A + '.m3u8', 'source': PAGE, 'title': msg('value.link_kind.dynamic_request'),
                         'uploader': '', 'files': [], 'transcripts': []})
    return db, new


class StoredRowsTests(AppCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = _sandbox.out('messages-ui-rows', 'library')
        cls.db, cls.ids = write_rows(cls.folder)

    def app_on_rows(self, lang, name):
        output = _sandbox.out(f'messages-ui-rows-{name}-{lang}', 'x').parent
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.db, output / 'test-library.sqlite3')
        app = self.make_app(output.name, size='1000x740')
        app.history_limit = 1000
        app.refresh_library()
        return app

    def test_en_history_titles_cr2_2_and_notes_r4(self):
        display = urls.display
        with _sandbox.language('en'):
            app = self.app_on_rows('en', 'titles')
            store = app.library
            titles = {row['id']: library_ui.history_title(row) for row in store.history(1000)}
            new = self.ids
            expected = {
                new['subscription_job']: i18n.t('value.job_title.subscription', name='Night feed'),
                new['download']: i18n.t('value.job_title.multi', title='Clip A', count=2),
                new['placeholder']: '',
                new['links']: i18n.t('value.job_title.multi', title=display(A), count=3),
            }
            self.assertEqual(titles, expected)
            for title in titles.values():
                self.assertEqual(chinese(title), [], title)
            # R4: a reason 2.0.0 stores is its message, read back whole (S2-DESIGN.md D7); the note cuts it
            done = i18n.t('library.history.downloaded', completed=0)
            whole = i18n.t('core.engine.cannot_start_link', error=i18n.t('core.cookies.missing', file=LONG))
            record = store.job(new['download'])
            reason = record['data']['errors'][-1]['reason']
            self.assertEqual(reason.key, 'core.engine.cannot_start_link')
            self.assertGreater(len(messages.zh(reason)), 70, '(S2-DESIGN.md D7) read back whole')
            note = library_ui.LibraryUI.history_note_text(record)
            self.assertEqual(note, done + i18n.t('library.history.last_error', error=whole[:130]), '2.0.0')
            for row in store.history(1000):
                note = library_ui.LibraryUI.history_note_text(store.job(row['id']))
                self.assertEqual(chinese(note), [], note)
            names = {row['id']: library_ui.subscription_row(row)[0] for row in store.subscriptions()}
            self.assertEqual(names, {new['subscription']: 'Night feed',
                                     new['untitled_subscription']: i18n.t('value.subscription.untitled')})

    def test_en_pages_with_stored_data_show_no_chinese_outside_user_data(self):
        with _sandbox.language('en'):
            app = self.app_on_rows('en', 'pages')
            store = app.library
            failures = []

            def check(where, root):
                for text in texts_of(root):
                    if page_chinese(str(text)):
                        failures.append(f'{where}: {text!r}')

            app.show_page('library')
            pump(app, 0.2)
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
            # collect results: a stored record loaded from the history, then a live page
            app.load_collection_record(store.job(self.ids['subscription_job']))
            app.collect_filter.set(collection_ui.FILTERS[1])
            pump(app, 0.02)
            check('collect stored', app.pages['collect'])
            app.collected_links.clear()
            app.collected_pages.clear()
            direct = msg('value.link_kind.direct')
            app.handle('collect_page', {'url': PAGE, 'title': direct, 'text': '', 'markdown': '', 'next': None,
                                        'links': [{'url': A + '.mp4', 'title': direct, 'kind': 'media', 'source': PAGE},
                                                  {'url': A + '.m3u8', 'title': msg('value.link_kind.dynamic_request'),
                                                   'kind': 'media', 'source': PAGE}]})
            app.handle('collect_error', msg('core.collect.needs_chrome'))
            pump(app, 0.02)
            check('collect live', app.pages['collect'])
            app.show_page('settings')
            pump(app, 0.1)
            check('settings', app.pages['settings'])
            self.assertEqual(failures, [])
            shown = [app.collect_tree.full_values(iid)[1] for iid in app.collect_tree.get_children()]
            self.assertEqual(shown, [i18n.t('value.link_kind.direct'), i18n.t('value.link_kind.dynamic_request')])

    def test_en_every_page_model_shows_no_app_chinese(self):
        """(2.0.0 S2-AMEND 3.4 Q3-4, PLAN 3.9) The rows of write_rows plus a subscription with pending items (a web
        link, a media link, a video page, a dynamic request), a collect job with pages and errors and an archive
        with a title, an uploader and files, all with Chinese user text: in English every page model of the
        library (history rows and notes, collect record rows and status, subscription rows, notes and pending
        lists, the archive list, the contexts' titles) holds no Chinese once the user data is taken out.  The
        user data is every text stored as text (no Chinese is stored but the user text fed here), every link and
        its shown forms, the file names and the sandbox paths."""
        import ui_download
        folder = _sandbox.out('messages-ui-models', 'library')
        folder.mkdir(parents=True, exist_ok=True)
        db = folder / 'test-library.sqlite3'
        shutil.copyfile(self.db, db)
        user, body = more_rows(db, folder)
        store = library.Library(db)
        stored = stored_texts(db)
        self.assertEqual({text for text in stored if chinese(text)}, set(user) | set(body),
                         'no Chinese is stored as text but the user text fed here')
        found = set()
        for text in stored | set(user):
            found.add(text)
            if text.startswith(('http://', 'https://')):
                found |= {urls.display(text), ui_download.link_title(text)}
        found |= {str(_sandbox.BASE), str(_sandbox.BASE).replace('\\', '/')}
        strip = sorted((text for text in found if text), key=len, reverse=True)
        with _sandbox.language('en'), mock.patch.object(library, 'alive', lambda pid: False):
            units = page_models(store)
        checked = 0
        for unit, text in units:
            rest = text
            for item in strip:
                rest = rest.replace(item, '')
            with self.subTest(unit=unit):
                self.assertEqual(chinese(rest), [], f'{unit}: {text}')
            checked += 1
        kinds = {unit.split(' ', 1)[0] for unit, _text in units}
        self.assertEqual(kinds, {'history', 'note', 'collect', 'subscription', 'pending', 'archive', 'context'})
        self.assertGreaterEqual(checked, 60)
        # the user's Chinese text is shown as it is (the strip list is not what made the check pass); the
        # archive list shows how many files and transcripts there are, not their names
        joined = '\n'.join(text for _unit, text in units)
        for text in user:
            if not text.endswith(('.mp3', '.vtt')):
                self.assertIn(text, joined)


class Stub:
    """A stand-in for the widgets the page models touch; it keeps the texts and rows they get."""

    def __init__(self, value=''):
        self.value, self.text, self.rows = value, None, []

    def selection(self):
        return []

    def selection_set(self, *items):
        pass

    def selection_remove(self, *items):
        pass

    def configure(self, **options):
        if 'text' in options:
            self.text = options['text']

    def fill(self, rows):
        self.rows = [[str(cell) for cell in values] for _iid, values in rows]

    def set_empty(self, text):
        self.empty = text

    def get(self, *args):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self, *args):
        pass

    def insert(self, *args, **options):
        pass


def more_rows(db, folder):
    """(Q3-4) More format-2 rows on the library of write_rows, written by the write paths; the Chinese user
    texts they were given."""
    store = library.Library(db)
    user = {'title': '夜曲集', 'link': '首页链接', 'job': '我的采集', 'name': '晚间订阅', 'uploader': '某位作者',
            'file': '晴天.mp3', 'transcript': '晴天.zh.vtt'}
    media, page_url = A + '/song.mp3', 'https://example.invalid/night'
    links = [{'url': B + '/home', 'title': raw(user['link']), 'kind': 'web', 'source': page_url},
             {'url': media, 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': page_url},
             {'url': 'https://www.youtube.com/watch?v=Q3Q3Q3', 'title': raw(user['title']), 'kind': 'video_page',
              'source': page_url},
             {'url': A + '/live.m3u8', 'title': msg('value.link_kind.dynamic_request'), 'kind': 'media',
              'source': page_url}]
    page = {'url': page_url, 'title': raw(user['title']), 'text': 'x', 'markdown': '', 'next': None, 'links': links}
    sub = store.subscription(user['name'], OPTIONS, media_only=False)
    store.finish_subscription(sub, [page], True)
    job = store.create_job('collect', user['job'], OPTIONS)
    store.collection_event(job, 'collect_page', dict(page, _checkpoint={'visited': [page_url], 'todo': []}))
    for error in (msg('core.collect.http_status', status=404), msg('core.collect.needs_chrome')):
        store.collection_event(job, 'collect_error', error)
    store.change_job(job, status='partial')
    store.archive_index(str(folder / 'archive-2-0-0' / 'night'),
                        {'url': media, 'source': page_url, 'title': raw(user['title']), 'uploader': user['uploader'],
                         'files': [user['file']], 'transcripts': [user['transcript']]})
    shown, body = produced_rows(store, folder)
    return sorted(user.values()) + shown, body


def produced_rows(store, folder):
    """(S2 review honesty-3) Rows the real producers write, not hand-made records: collector.parse_page (the
    Scrapling selector, a captured dynamic request) for a collect job and a media-only subscription, and
    Runner.download (_run_once scripted as in test_golden_events: a YouTube bot check, a 429 to the last
    retry, an engine failure with the engine's own text, a success) whose events go to
    Library.download_event the way the window forwards them (ui_download.HISTORY_KINDS, history_value).
    The Chinese user texts they were given (shown on the page models) and the page body Scrapling read (text
    and markdown: stored for the archive files, not shown on a page model)."""
    import collector
    import ui_download
    from scrapling import Selector
    user = {'page': '夜间列表', 'label': '第一首歌'}
    page_url = 'https://example.invalid/produced'
    html = (f'<html><head><title>{user["page"]}</title></head><body><a href="/about">{user["label"]}</a>'
            '<a href="/v/a.mp4"></a><a href="https://www.youtube.com/watch?v=P2P2P2">Video</a></body></html>')
    page = collector.parse_page(Selector(html, url=page_url), page_url, captured=[A + '/cap.m3u8'])
    options = dict(OPTIONS, urls=[page_url])
    sub = store.subscription('', options, media_only=True)
    store.finish_subscription(sub, [page], True)
    job = store.create_job('collect', page_url, options)
    store.collection_event(job, 'collect_page', dict(page, _checkpoint=None))
    store.collection_event(job, 'collect_error', msg('core.collect.no_match'))
    store.change_job(job, status='partial')

    links = ['https://www.youtube.com/watch?v=P3P3P3', A + '/limited', A + '/failed', A + '/fine']

    def bot_check(runner, command):
        runner.bot_check = True
        return 1, False, None

    def limited(runner, command):
        return 1, True, None

    def failed(runner, command):
        runner.last_error = 'ERROR: [generic] Unsupported URL: ' + command[-1]
        return 1, False, None

    def fine(runner, command, files):
        files.append(str(Path(command[command.index('-P') + 1]) / 'fine.mp3'))
        return 0, False, None

    steps = [bot_check, limited, limited, limited, failed, fine]
    out = folder / 'produced-out'
    download = store.create_job('download', '', {**engine.Options(out, batch_safe=False).to_dict(), 'urls': links})
    seen = []

    def emit(kind, value):
        seen.append(kind)
        if kind in ui_download.HISTORY_KINDS:
            store.download_event(download, kind, ui_download.history_value(kind, value, False))

    def run_once(command, index, total, files):
        step = steps.pop(0)
        return step(runner, command, files) if step is fine else step(runner, command)

    engine.Runner.reset_pacing()
    runner = engine.Runner(emit)
    try:
        with _sandbox.state_patch(folder / 'produced-state'), \
                mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(links, engine.Options(out, batch_safe=False))
    finally:
        engine.Runner.reset_pacing()
    data = store.job(download)['data']
    assert steps == [] and seen[-1] == 'done', (steps, seen)
    assert [(error['error'], error['reason'].key) for error in data['errors']] == [
        ('ERROR: [generic] Unsupported URL: ' + A + '/failed', 'core.reason.unsupported')], data['errors']
    assert (data['result']['failed'], data['result']['blocked'], data['done']) == (
        3, 'bot_check', [[A + '/fine', engine.MODES[0]]]), data
    return sorted(user.values()), [page['text'], page['markdown']]


def stored_texts(db):
    """Every text stored as text in the library: the text columns and the string leaves of the records (a
    message's key and the code values of mode, resolution and kind are not text)."""
    texts = set()

    def leaves(item, key=None):
        if isinstance(item, str):
            if key not in ('mode', 'resolution', 'kind', 'k'):
                texts.add(item)
        elif isinstance(item, dict):
            for name, child in item.items():
                leaves(child, name)
        elif isinstance(item, list):
            for child in item:
                leaves(child, key)

    connection = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        for table in ('jobs', 'pages', 'subscriptions', 'entries', 'archives', 'downloads'):
            cursor = connection.execute(f'SELECT * FROM {table}')
            columns = [item[0] for item in cursor.description]
            for row in cursor:
                for column, value in zip(columns, row):
                    if column == 'data':
                        leaves(json.loads(value))
                    elif isinstance(value, str):
                        texts.add(value)
    finally:
        connection.close()
    return texts


def page_models(store):
    """[(unit, text)] of every page model of the library in the running language, on stand-in widgets."""
    Page = type('Page', (library_ui.LibraryUI, collection_ui.CollectionUI), {})
    page = Page.__new__(Page)
    page.library = store
    for name in ('collect_urls', 'collect_mode', 'collect_scrolls', 'collect_pages_limit', 'collect_selector',
                 'collect_tree', 'pending_tree', 'pending_count', 'subscription_note', 'archive_tree',
                 'archive_count'):
        setattr(page, name, Stub())
    page.collect_filter, page.collect_search, page.archive_search = Stub(collection_ui.FILTERS[1]), Stub(), Stub()
    page.set_collect_status = lambda text, tone=None: setattr(page, 'collect_status_text', text)
    page.collection_selection_changed = lambda: None
    page.refresh_collect_actions = lambda: None         # (2.0.0 S4 A2) a button hook, no text of its own
    page.library_busy = lambda error=None: None
    page.visible_pending = []
    units = []
    for row in store.history(100000):
        units += [(f'history {row["id"]} {n}', str(cell)) for n, cell in enumerate(library_ui.history_row(row))]
        record = store.job(row['id'])
        units.append((f'note {row["id"]}', library_ui.LibraryUI.history_note_text(record)))
        if record['kind'] == 'collect':
            library_ui.LibraryUI.load_collection_record(page, record)
            units += [(f'collect {row["id"]} {n}', ' | '.join(cells)) for n, cells in enumerate(page.collect_tree.rows)]
            units.append((f'collect {row["id"]} status', str(page.collect_status_text)))
    for row in store.subscriptions():
        units += [(f'subscription {row["id"]} {n}', str(cell)) for n, cell in enumerate(library_ui.subscription_row(row))]
        page.selected_subscription = lambda row=row: row
        page.pending_tree.rows = []
        library_ui.LibraryUI._subscription_selection(page)
        units.append((f'subscription {row["id"]} note', str(page.subscription_note.text)))
        units.append((f'subscription {row["id"]} count', str(page.pending_count.text)))
        units += [(f'pending {row["id"]} {n}', ' | '.join(cells)) for n, cells in enumerate(page.pending_tree.rows)]
    library_ui.LibraryUI.refresh_archives(page)
    units += [(f'archive {n}', ' | '.join(cells)) for n, cells in enumerate(page.archive_tree.rows)]
    units.append(('archive count', str(page.archive_count.text)))      # (S4 visual fix) the count above the table
    wanted = sorted({item['url'] for row in store.history(100000) if row['kind'] == 'collect'
                     for page_record in store.job(row['id'])['pages'] for item in page_record['links']})
    units += [(f'context {url}', messages.show(found['title'])) for url, found in store.contexts(wanted).items()]
    return units


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
            self.assertEqual(app.library_notice.full_text, i18n.t('library.unreadable', error=shown))
            app.show_library_problem('')
            # the OS-error dialogs (collection_ui 622, 643; library_ui 1306)
            items = [{'url': A, 'title': raw('A'), 'kind': 'media', 'source': PAGE}]
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
