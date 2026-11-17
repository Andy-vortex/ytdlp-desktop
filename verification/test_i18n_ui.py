"""1.7.0 language adaptation: the English UI (I18N.md 11.4; written by WP-L1-C, run in L2).

    python -X utf8 -B test_i18n_ui.py

The UI checks run on smoke and sandboxed StudioApps in English (_sandbox.language('en')): no CJK in
any widget text of the five pages, the library tabs and the sidebar; the stored forms of every menu
stay the 1.6.0 values; a 404 link on the local fixture server shows an English reason while the
history keeps the Chinese one; the language row and the restart flow (with a recording launcher,
never a real second program); message boxes and file dialogs get English texts.

Every class runs (the L2 merge of L1-A and L1-B removed the UI_PENDING skip flag, I18N.md 13).
ArchiveTextTests: the archive readme and the page-text files are byte-identical to 1.6.0 in Chinese
and carry the English labels in English (I18N.md 8.8).
No network (the GitHub check is patched), no window on screen (alpha 0, no frame).
"""
import _sandbox
import json
import os
import re
import time
import tkinter as tk
import types
import unittest
from pathlib import Path
from unittest import mock

import customtkinter as ctk
import i18n
import library
import paths
import settings
import ui_theme
import updater

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
CJK_ALLOWED_CHARS = set('【】『』「」')          # the bracket examples of settings.naming.text


def cjk(text):
    """The CJK characters of ``text`` that the English UI may not show (I18N.md 11.2 item 3)."""
    text = str(text)
    for key in ('settings.language.zh', 'settings.language.title'):
        for allowed in i18n.all_values(key):
            text = text.replace(allowed, '')
    return [char for char in text if CJK.match(char) and char not in CJK_ALLOWED_CHARS]


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.005)
    return condition is None


def walk(widget):
    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.winfo_children())


def shown_text(widget):
    """The text a CTk widget shows (the full text of a fitted label); None for other widgets."""
    if isinstance(widget, ctk.CTkLabel):
        return ui_theme.full_text(widget)
    if isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkSwitch)):
        return widget.cget('text')
    return None


def widget_texts(root):
    """[(where, text)] of every text the widgets below ``root`` show or offer: labels, buttons,
    checkboxes, option menus (shown value and every choice), segmented bars and tab bars, Treeview
    headings and empty hints, entry placeholders.  Link boxes and entry values are user data."""
    found = []
    for widget in walk(root):
        where = f'{type(widget).__name__} {widget}'
        text = shown_text(widget)
        if text:
            found.append((where, text))
        if isinstance(widget, ctk.CTkOptionMenu):
            found.append((where, widget._text_label.cget('text')))
            found += [(where + ' (choice)', value) for value in widget.cget('values')]
        elif isinstance(widget, ctk.CTkSegmentedButton):
            found += [(where + ' (segment)', value) for value in widget.cget('values')]
        elif isinstance(widget, ctk.CTkEntry):
            placeholder = widget.cget('placeholder_text')
            if placeholder:
                found.append((where + ' (placeholder)', placeholder))
        elif isinstance(widget, ui_theme.AppTable):
            found += [(where + ' (heading)', widget.heading(column, 'text')) for column in widget['columns']]
            if getattr(widget, '_empty', ''):
                found.append((where + ' (empty)', widget._empty))
    return found


def menu_labels(menu):
    end = menu.index('end')
    return [] if end is None else [menu.entrycget(index, 'label') for index in range(end + 1)
                                   if menu.type(index) == 'command']


class AppCase(unittest.TestCase):
    """Smoke (no settings) or sandboxed (settings.json in the sandbox) StudioApps, never on screen."""

    def setUp(self):
        for patcher in (mock.patch.object(updater, 'check_latest', side_effect=AssertionError('no network')),
                        mock.patch('studio.StudioApp.clipboard_get', side_effect=tk.TclError('no clipboard'))):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('settings.json', 'restart-state.json'):
            try:
                (paths.settings_file().with_name(name)).unlink()
            except FileNotFoundError:
                pass

    def make_app(self, smoke=True, size='1120x810', **stored):
        from studio import StudioApp
        ctk.set_appearance_mode('light')
        if smoke:
            app = StudioApp(_sandbox.app_args('https://example.invalid/i18n', str(_sandbox.out('i18n-' + self._testMethodName))))
            app.urls.delete('1.0', 'end')
            app.links_changed()
        else:
            _sandbox.write_settings(output=str(_sandbox.out('i18n-out')), batch_safe=False, **stored)
            app = StudioApp(_sandbox.app_args())
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.geometry(size + '+0+0')
        self.addCleanup(self.close, app)
        pump(app, timeout=0.3)
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
    def shown(app, page, text):
        """The viewable labels and buttons of page ``page`` that show ``text`` (in full)."""
        app.show_page(page)
        pump(app, timeout=0.3)
        return [widget for widget in walk(app.pages[page])
                if shown_text(widget) == text and widget.winfo_viewable()]


# ------------------------------------------------------------------ runs now (I18N.md 8.8)
class ArchiveTextTests(unittest.TestCase):
    """The archive readme (阅读说明.txt) and the page-text files (网页正文.txt / .md): the 1.6.0 bytes
    in Chinese, English labels in English; the file names stay Chinese in both (D10)."""

    PAGE = {'url': 'https://www.example.org/podcast/list', 'title': 'City Nights · episode list',
            'text': 'Episode notes.', 'markdown': '# Episode notes', 'links': [], 'text_truncated': True}
    META = {'filepath': None, 'id': 'ep1', 'title': 'Episode 1', 'uploader': None,
            'description': 'An episode.', 'webpage_url': 'https://media.example.org/ep1.m4a'}

    @staticmethod
    def legacy_page(page, truncated):
        """The 1.6.0 text of 网页正文.txt / .md (library.Archive.prepare of 1.6.0, verbatim)."""
        note = '\n说明：源正文超过长度上限，以下内容已截断。' if truncated else ''
        return {name: f"{page['title']}\n来源：{page['url']}{note}\n\n{body}\n"
                for name, body in (('网页正文.txt', page.get('text', '')), ('网页正文.md', page.get('markdown', '')))}

    @staticmethod
    def legacy_readme(data):
        """The 1.6.0 text of 阅读说明.txt (library.Archive.save of 1.6.0, verbatim)."""
        return (f"{data['title']}\n原链接：{data['url']}\n采集来源：{data['source']}\n作者：{data.get('uploader') or '来源未提供'}\n\n"
                + data.get('description', '') + '\n\n媒体文件：\n' + '\n'.join(data['files'])
                + '\n\n关联转写稿：\n' + '\n'.join(data['transcripts']) + '\n')

    def archive(self, name):
        root = _sandbox.out('i18n-archive', name)
        root.mkdir(parents=True, exist_ok=True)
        return library.Archive(root)

    def fill(self, archive, folder):
        """One media file and one transcript, as a download and 关联转写稿 add them."""
        media = folder / '媒体' / 'Episode 1.m4a'
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(b'not really audio')
        archive.media(folder, dict(self.META, filepath=str(media), uploader='City Nights'))
        transcript = _sandbox.out('i18n-archive', 'notes.txt')
        transcript.write_text('transcript', encoding='utf-8')
        archive.attach(folder, transcript)

    @staticmethod
    def read(path):
        return Path(path).read_bytes()

    @staticmethod
    def written(text):
        """The bytes 1.6.0 wrote for ``text``: Path.write_text(text, encoding='utf-8-sig') (text mode,
        so every newline becomes CR LF on Windows)."""
        path = _sandbox.out('i18n-archive', 'legacy.txt')
        path.write_text(text, encoding='utf-8-sig')
        return path.read_bytes()

    def test_zh_files_are_byte_identical_to_1_6_0(self):
        with _sandbox.language('zh'):
            for truncated in (True, False):
                archive = self.archive(f'zh-{truncated}')
                page = dict(self.PAGE, text_truncated=truncated, title='城市夜谈 · 节目列表')
                folder = archive.prepare('https://media.example.org/ep1.m4a',
                                         {'source': page['url'], 'title': '第 1 期', 'page': page})
                for name, text in self.legacy_page(page, truncated).items():
                    self.assertEqual(self.read(folder / name), self.written(text), name)
                data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual(self.read(folder / '阅读说明.txt'), self.written(self.legacy_readme(data)))
                self.assertIn('作者：来源未提供', (folder / '阅读说明.txt').read_text(encoding='utf-8-sig'))
                self.fill(archive, folder)
                data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertTrue(data['files'] and data['transcripts'] and data['uploader'], data)
                self.assertEqual(self.read(folder / '阅读说明.txt'), self.written(self.legacy_readme(data)))

    def test_en_files_carry_the_english_labels_and_keep_their_names(self):
        with _sandbox.language('en'):
            archive = self.archive('en')
            folder = archive.prepare('https://media.example.org/ep1.m4a',
                                     {'source': self.PAGE['url'], 'title': 'Episode 1', 'page': self.PAGE})
            for name, body in (('网页正文.txt', 'Episode notes.'), ('网页正文.md', '# Episode notes')):
                text = (folder / name).read_text(encoding='utf-8-sig')
                self.assertEqual(text, i18n.t('filetext.page_text', title=self.PAGE['title'], url=self.PAGE['url'],
                                              note=i18n.t('filetext.truncated_source'), body=body))
                self.assertTrue(text.startswith('City Nights · episode list\nSource: https://www.example.org/podcast/list\n'), text)
                self.assertEqual(cjk(text), [], text)
            readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
            self.assertIn('\nAuthor: not provided by the source\n', readme)
            self.assertEqual(cjk(readme), [], readme)
            self.fill(archive, folder)
            readme = (folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
            for label in ('Original link: https://media.example.org/ep1.m4a', 'Collected from: ', 'Author: City Nights',
                          i18n.t('filetext.readme_media').strip(), i18n.t('filetext.readme_transcripts').strip()):
                self.assertIn(label, readme)
            # File and folder names stay Chinese (D10): the record, the readme, the media and transcript folders.
            self.assertTrue((folder / '资料信息.json').is_file() and (folder / '转写稿').is_dir())
            self.assertEqual(cjk(readme.replace('媒体', '').replace('转写稿', '')), [], readme)
        # The readme is written again on every save, in the running language.
        with _sandbox.language('zh'):
            data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
            archive.save(folder, data)
            self.assertEqual(self.read(folder / '阅读说明.txt'), self.written(self.legacy_readme(data)))


# ------------------------------------------------------------------ English UI (run in L2)
class EnglishWidgetTests(AppCase):
    """No CJK in the English UI; the stored forms of every menu stay the 1.6.0 values."""

    def test_no_cjk_in_any_widget_text_of_the_pages_tabs_and_sidebar(self):
        import snapshot_ui
        with _sandbox.language('en'):
            app = self.make_app()
            failures = []
            for page in snapshot_ui.PAGES:
                app.show_page(page)
                pump(app, timeout=0.2)
                tabs = snapshot_ui.LIBRARY_TABS if page == 'library' else (None,)
                for tab in tabs:
                    if tab is not None:
                        app.library_tabs.set(tab)
                        pump(app, timeout=0.2)
                    failures += [f'{page}/{tab}: {where}: {text!r}' for where, text in widget_texts(app.pages[page])
                                 if cjk(text)]
            failures += [f'sidebar: {where}: {text!r}' for where, text in widget_texts(app.side_status.master.master)
                         if cjk(text)]
            failures += [f'window title: {app.title()!r}'] if cjk(app.title()) else []
            # The context menus of the link box and of a task row.
            menus = []
            app._popup = lambda menu, event: menus.append(menu_labels(menu))
            event = types.SimpleNamespace(x_root=0, y_root=0, widget=app.urls)
            app._box_menu(event)
            app.populate_queue(['https://example.org/a.mp3'])
            pump(app, timeout=0.2)
            app.row_context(event, 0)
            self.assertEqual(len(menus), 2)
            failures += [f'context menu: {label!r}' for labels in menus for label in labels if cjk(label)]
            self.assertEqual(failures, [])

    def test_choosing_through_the_english_menus_keeps_the_stored_forms(self):
        import collection_ui
        import engine
        import library_ui
        import naming
        import ui_download
        with _sandbox.language('en'):
            app = self.make_app(smoke=False)
            stores = {'format': set(ui_download.FORMATS), 'resolution': set(engine.RESOLUTIONS),
                      'naming': set(naming.NAMING), 'subscription_interval': set(library_ui.INTERVALS),
                      'collect_mode': {collection_ui.MODE_STATIC, collection_ui.MODE_DYNAMIC},
                      'collect_filter': set(collection_ui.FILTERS)}
            menus = [widget for widget in walk(app) if isinstance(widget, ctk.CTkOptionMenu)]
            for name, allowed in stores.items():
                variable = getattr(app, name)
                owners = [menu for menu in menus if str(menu.cget('variable')) == str(variable)]
                self.assertEqual(len(owners), 1, f'one menu stores {name}')
                menu, reached = owners[0], set()
                for shown in menu.cget('values'):
                    self.assertEqual(cjk(shown), [], f'{name}: {shown!r}')
                    menu._dropdown_callback(shown)
                    pump(app, timeout=0.05)
                    self.assertIn(variable.get(), allowed, f'{name} after choosing {shown!r}')
                    reached.add(variable.get())
                    if name == 'format':
                        self.assertIn(app.mode.get(), engine.MODES)
                self.assertEqual(reached, allowed, name)
            self.assertEqual(app.naming_label.get(), naming.LABELS[app.naming.get()], 'naming_label keeps its zh value')
            app.save_settings()
            stored = json.loads(paths.settings_file().read_text(encoding='utf-8'))
            self.assertIn(stored['mode'], engine.MODES)
            self.assertIn(stored['resolution'], engine.RESOLUTIONS)
            self.assertIn(stored['naming'], naming.NAMING)
            self.assertEqual(app.library_tabs.get(), '任务历史与续采', 'the tab ids stay')


class EnglishEndToEndTests(AppCase):
    """A 404 link on the local fixture server with the bundled engine: English on screen, Chinese
    in the history; a Chinese app shows the Chinese reason from the same record."""

    def test_a_404_link_shows_english_and_stores_chinese(self):
        import engine
        base, server = _sandbox.fixture_site()
        self.addCleanup(server.shutdown)
        url = base + '/i18n-missing.mp3'
        output = str(_sandbox.out('i18n-e2e'))
        boxes = []
        with _sandbox.language('en'), mock.patch('ui_download.messagebox') as dialogs:
            from studio import StudioApp
            app = StudioApp(_sandbox.app_args(url, output))
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            self.addCleanup(self.close, app)
            app.batch_safe.set(False)
            engine.Runner.reset_pacing()
            app.start()
            self.assertTrue(pump(app, lambda: not app.busy and app.row_model, 120), 'the job ended')
            pump(app, timeout=0.3)
            boxes += [call for call in dialogs.method_calls]
            row = app.row_model[0]
            self.assertNotEqual(row['state'], 'done')
            self.assertTrue(row['detail'])
            self.assertEqual(cjk(row['detail']), [], row['detail'])
            app.flush_log()
            log = app.logs.get('1.0', 'end')
            self.assertEqual([line for line in log.splitlines() if cjk(line)], [], 'every log line is English')
            records = app.library.history()
            self.assertTrue(records)
            record = records[0]
            self.assertIn(record['data']['options']['mode'], engine.MODES, 'the history keeps the Chinese mode')
            stored = json.dumps(record['data'].get('last_reason') or record['data'].get('errors'), ensure_ascii=False)
            self.assertTrue(cjk(stored), f'the history keeps the Chinese reason: {stored}')
            app.show_page('library')
            app.history_tree.selection_set(record['id'])
            app.history_selection()
            note = ui_theme.full_text(app.history_note)
            self.assertEqual(cjk(note), [], note)
            app.destroy()
        self.assertEqual(boxes, [], 'no message box')
        with _sandbox.language('zh'):
            from studio import StudioApp
            app = StudioApp(_sandbox.app_args(url, output))
            app.attributes('-alpha', 0.0)
            app.overrideredirect(True)
            self.addCleanup(self.close, app)
            app.show_page('library')
            pump(app, timeout=0.3)
            app.history_tree.selection_set(record['id'])
            app.history_selection()
            note = ui_theme.full_text(app.history_note)
            self.assertTrue(cjk(note), note)
            self.assertEqual(note, app.history_note_text(app.library.job(record['id'])))


class LanguageRowTests(AppCase):
    """The language row (I18N.md 6.2) on the owner's PC (display en-US, format zh-CN): a normal start
    (no YTDLP_DESKTOP_LANG) that resolves to Chinese."""

    def owner_pc(self, env=False):
        self.enterContext(_sandbox.language('zh', env=env))
        self.enterContext(_sandbox.fake_system(0x0409, 'zh-CN'))

    def strip(self, app):
        """(note shown, button) of the language row: which of its texts are on screen."""
        notes = {key: bool(self.shown(app, 'settings', i18n.t(key)))
                 for key in ('settings.language.restart_note', 'settings.language.restart_busy',
                             'settings.language.env_fixed', 'settings.language.held')}
        buttons = self.shown(app, 'settings', i18n.t('settings.language.restart'))
        return {key for key, shown in notes.items() if shown}, buttons

    def test_choosing_english_stores_it_and_offers_the_restart(self):
        self.owner_pc()
        app = self.make_app(smoke=False)
        self.assertEqual(i18n.language(), 'zh')
        self.assertEqual(self.strip(app), (set(), []), 'an untouched row shows no strip')
        app.choose_language('en')
        app.save_settings()
        self.assertEqual(json.loads(paths.settings_file().read_text(encoding='utf-8'))['language'], 'en')
        notes, buttons = self.strip(app)
        self.assertEqual(notes, {'settings.language.restart_note'})
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].cget('state'), 'normal')
        app.operation = 'download'
        app.set_busy(True)
        notes, buttons = self.strip(app)
        self.assertEqual(notes, {'settings.language.restart_busy'})
        self.assertEqual([button.cget('state') for button in buttons], ['disabled'])
        app.set_busy(False)
        for value in ('zh', 'system'):
            app.choose_language(value)
            self.assertEqual(self.strip(app), (set(), []), f'{value} resolves to the running zh')
        app.save_settings()
        self.assertEqual(json.loads(paths.settings_file().read_text(encoding='utf-8'))['language'], 'system')

    def test_a_set_variable_fixes_the_session_and_a_held_start_cannot_save(self):
        self.owner_pc(env=True)
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.assertEqual(self.strip(app), ({'settings.language.env_fixed'}, []))
        app.prefs[settings.HELD] = {}
        app.choose_language('en')
        self.assertEqual(self.strip(app), ({'settings.language.held'}, []))


class RestartTests(AppCase):
    """restart_app with a recording launcher (never a real second program), the restart state."""

    def setUp(self):
        super().setUp()
        self.enterContext(_sandbox.language('zh', env=False))
        self.enterContext(_sandbox.fake_system(0x0409, 'zh-CN'))
        self.state_file = paths.settings_file().with_name('restart-state.json')

    def recorder(self, error=None):
        calls = []

        def launch(command, env):
            calls.append((list(command), dict(env)))
            if error is not None:
                raise error
        return calls, launch

    def fill(self, app):
        app.urls.insert('1.0', 'https://www.example.org/a.mp3\nhttps://www.example.org/b.mp3')
        app.links_changed()
        app.collect_urls.insert('1.0', 'https://www.example.org/podcast/list')
        app.subscription_name.set('Weekly podcast')

    def test_restart_hands_the_launcher_the_command_and_keeps_the_input(self):
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.fill(app)
        calls, app.restart_launcher = self.recorder()
        app.restart_app()
        self.assertEqual(len(calls), 1)
        command, env = calls[0]
        self.assertIn('--restarted-from', command)
        self.assertEqual(command[command.index('--restarted-from') + 1], str(os.getpid()))
        self.assertEqual(env.get('PYINSTALLER_RESET_ENVIRONMENT'), '1')
        self.assertNotIn('YTDLP_DESKTOP_LANG', env, 'a normal start: the stored choice decides')
        self.assertTrue(getattr(app, '_finished', False), 'the window closes')
        state = json.loads(self.state_file.read_text(encoding='utf-8'))
        self.assertEqual(set(state), {'version', 'created', 'pid', 'urls', 'collect_urls', 'subscription_name'})
        self.assertEqual(state['version'], 1)
        self.assertEqual(json.loads(paths.settings_file().read_text(encoding='utf-8'))['language'], 'en')
        # The next start restores the three inputs once, logs it and deletes the file.
        app = self.make_app(smoke=False)
        self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5))
        pump(app, timeout=0.2)
        self.assertEqual(app.urls.get('1.0', 'end').strip(), 'https://www.example.org/a.mp3\nhttps://www.example.org/b.mp3')
        self.assertEqual(app.collect_urls.get('1.0', 'end').strip(), 'https://www.example.org/podcast/list')
        self.assertEqual(app.subscription_name.get(), 'Weekly podcast')
        app.flush_log()
        self.assertIn(i18n.t('log.restart_restored'), app.logs.get('1.0', 'end'))

    def test_a_stale_or_broken_state_file_is_deleted_and_ignored(self):
        for content in (json.dumps({'version': 1, 'created': time.time() - 200, 'pid': 1, 'urls': 'https://x.example/a',
                                    'collect_urls': '', 'subscription_name': ''}), '{broken'):
            self.state_file.write_text(content, encoding='utf-8')
            app = self.make_app(smoke=False)
            self.assertTrue(pump(app, lambda: not self.state_file.exists(), 5))
            self.assertEqual(app.urls.get('1.0', 'end').strip(), '')
            self.close(app)

    def test_a_failing_launcher_keeps_the_window_and_leaves_no_state(self):
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.fill(app)
        calls, app.restart_launcher = self.recorder(OSError('spawn refused'))
        with mock.patch('ui_settings.messagebox') as dialogs:
            app.restart_app()
        self.assertEqual(len(calls), 1)
        self.assertFalse(getattr(app, '_finished', False))
        self.assertTrue(app.winfo_exists())
        self.assertFalse(self.state_file.exists())
        titles = [call.args[0] if call.args else call.kwargs.get('title') for call in dialogs.method_calls]
        self.assertIn(i18n.t('dialog.restart_failed.title'), titles)

    def test_an_unwritable_state_file_asks_and_no_cancels(self):
        app = self.make_app(smoke=False)
        app.choose_language('en')
        self.fill(app)
        calls, app.restart_launcher = self.recorder()
        import ui_settings
        refused = mock.Mock(side_effect=OSError('disk full'))
        with mock.patch.object(paths, 'atomic_write_text', refused), mock.patch('ui_settings.messagebox') as dialogs:
            if hasattr(ui_settings, 'atomic_write_text'):       # imported by name: patch that name too
                self.enterContext(mock.patch.object(ui_settings, 'atomic_write_text', refused))
            dialogs.askyesno.return_value = False
            app.restart_app()
        self.assertEqual(calls, [])
        self.assertFalse(getattr(app, '_finished', False))
        self.assertEqual(dialogs.askyesno.call_args.args[0] if dialogs.askyesno.call_args.args
                         else dialogs.askyesno.call_args.kwargs.get('title'), i18n.t('dialog.restart_unsaved.title'))


class EnglishDialogTests(AppCase):
    """Message boxes and file dialogs get English titles and texts; core errors arrive localized."""

    def texts(self, dialogs):
        found = []
        for call in dialogs.method_calls:
            found += [str(value) for value in call.args if isinstance(value, str)]
            found += [str(value) for key, value in call.kwargs.items() if key in ('title', 'message', 'initialfile')]
            for label, _pattern in call.kwargs.get('filetypes') or ():
                found.append(str(label))
        return found

    def test_a_bad_link_line_and_a_json_cookies_file_are_explained_in_english(self):
        with _sandbox.language('en'):
            app = self.make_app()
            app.urls.insert('1.0', 'https://www.example.org/a.mp3\nnot a link at all')
            app.links_changed()
            with mock.patch('ui_download.messagebox') as dialogs:
                app.start()
            self.assertTrue(dialogs.method_calls, 'the bad line is explained')
            self.assertEqual([text for text in self.texts(dialogs) if cjk(text)], [])
            cookies = _sandbox.out('i18n-cookies.json')
            cookies.write_text('[{"domain": ".youtube.com", "name": "x", "value": "y"}]', encoding='utf-8')
            with mock.patch('ui_settings.filedialog') as files, mock.patch('ui_settings.messagebox') as dialogs:
                files.askopenfilename.return_value = str(cookies)
                app.choose_cookies()
            self.assertTrue(dialogs.method_calls, 'the JSON file is refused')
            texts = self.texts(dialogs) + self.texts(files)
            self.assertEqual([text for text in texts if cjk(text)], [])
            self.assertIn(i18n.t('core.cookies.json'), texts)

    def test_file_dialogs_get_english_titles_types_and_names(self):
        with _sandbox.language('en'):
            app = self.make_app()
            with mock.patch('ui_download.filedialog') as files:
                files.askopenfilename.return_value = ''
                app.import_txt()
            self.assertTrue(files.method_calls)
            self.assertEqual([text for text in self.texts(files) if cjk(text)], [])
            source = 'https://www.example.org/podcast/list'
            links = [{'url': f'https://media.example.org/episode-{number}.m4a', 'title': f'Episode {number}',
                      'kind': '媒体地址', 'source': source} for number in range(3)]
            app.handle('collect_page', {'url': source, 'title': 'Episode list', 'text': 'Notes', 'markdown': '# Notes',
                                        'links': links})
            pump(app, timeout=0.2)
            app.collect_tree.selection_set(app.collect_tree.get_children()[:2])
            app.collection_selection_changed()
            with mock.patch('collection_ui.filedialog') as files:
                files.asksaveasfilename.return_value = ''
                app.export_collected_links()
                app.export_collected_text()
            self.assertEqual(len(files.method_calls), 2)
            self.assertEqual([text for text in self.texts(files) if cjk(text)], [])
            names = [call.kwargs.get('initialfile') for call in files.method_calls]
            self.assertEqual(names, ['collected-links.txt', 'page-text.txt'])


if __name__ == '__main__':
    unittest.main()
