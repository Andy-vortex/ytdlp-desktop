"""G4b English replay and G6 zh dump (MESSAGES.md 7.6; 1.8.0 WP-6).

    python -X utf8 -B test_english_replay.py

The frozen producers of 1.5.1, 1.6.0 and 1.7.0 run in the sandbox, each in a child process that
imports its own verification/release-sources/<v>/src (-B, the sandbox environment, one library
file and one state folder in that order, as the owner's upgrades did): their Library writes; their
collector through their own worker_main with a fake Scrapling fetcher (a normal page, a page whose
title is empty, a media start page, a dynamic capture, an HTTP error, a page over 8 MB, a CSS range
that matches nothing); the CollectOptions.validate errors (through worker_main as well); subscription
checks with the version's own composition (the inline '；' join or subscription_result, '订阅：' +
name or stored_text, the cancelled and unfinished texts); a download job with the version's own
placeholder title; a Runner (1.6.0, 1.7.0) on scripted output with an engine error, a bot check, a
429 run-out, a cookies echo and an item that cannot start (missing cookies file with a long path);
the failed list (1.5.1 store_failed_job, then the Runners' merges).  User data is ASCII.

G4b: 1.8.0 opens the result in English, decodes every record and renders every page model with the
page code itself (stub widgets): history rows and notes, subscription rows and notes, the pending
table, the collect table of every collect job, the archive table, the unfinished rows with their
reason dialog; and the archive files 1.8.0 writes for the stored contexts.  No CJK may remain (and
no G2 record: the renders run in this guarded process).  A self-test puts the legacy reader back in
its 1.7.0 first-round state (no frozen 1.5.1 variants, the title kind limited to value.page_title.)
and finds exactly the misses of 1.7.0: 媒体直链, 动态页面媒体请求 and the 1.5.1 old-format collect
errors.

G6: the same data rendered in zh by the frozen 1.7.0 page code (a child process on
release-sources/1.7.0) and by 1.8.0 is identical; then the same scenarios produced by 1.8.0 itself
are rendered by both in zh: identical again, and in en without CJK.  Also (MESSAGES.md 15 A11.5)
engine._sentence keeps a message an OSError carries.
"""
import _sandbox
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import collection_ui
import engine
import i18n
import i18n_legacy
import i18n_tool
import library
import library_ui
import messages
import ui_download
import unfinished
from messages import Message, msg

ROOT = _sandbox.SRC.parent
VERSIONS = ('1.5.1', '1.6.0', '1.7.0')
CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
# On-disk names of the archive (D10: the folder and file names stay Chinese in both languages).
ON_DISK = ('资料信息.json', '阅读说明.txt', '网页正文.txt', '网页正文.md', '媒体', '转写稿', '来源', '资料归档')

# ------------------------------------------------------------------ the producers (child process)
PRODUCER = r'''
import json, os, sys, types
from dataclasses import asdict
from pathlib import Path
from unittest import mock
src, version, folder, vendor = sys.argv[1:5]
sys.path.insert(0, src)
folder = Path(folder)
NEW = version == '1.8.0'
if version != '1.5.1':
    import paths
    paths.VENDOR = Path(vendor)
    paths.BIN = paths.VENDOR / 'bin'
import engine
import library
import collector
from scrapling import Selector
import scrapling.fetchers as fetchers
if NEW:
    import messages
if version == '1.5.1':
    engine.STATE_DIR = Path(os.environ['YTDLP_DESKTOP_STATE'])

BASE = 'https://pages.example.invalid'
PAGES = {
    BASE + '/list': '<html><head><title>Example list</title></head><body><main><p>Hello there</p>'
                    '<a href="/clip.mp4">Clip one</a><a href="https://www.youtube.com/watch?v=abcdefghijk">Video one</a>'
                    '<a href="/about"></a></main></body></html>',
    BASE + '/untitled': '<html><head><title></title></head><body><p>No title</p><a href="/song.mp3"></a></body></html>',
    BASE + '/dynamic': '<html><head><title>Live page</title></head><body><p>Player</p></body></html>',
    BASE + '/gone': '<html><head><title>Gone</title></head><body></body></html>',
    BASE + '/huge': '<html><head><title>Huge</title></head><body></body></html>',
}
STATUS = {BASE + '/gone': 503}


class Response(Selector):
    """A Scrapling response for collect(): real Selector parsing, a status and a body size."""
    def __init__(self, url):
        super().__init__(PAGES[url], url=url)
        self.__dict__['status'] = STATUS.get(url, 200)
        self.__dict__['size'] = 9 * 1024 * 1024 if url.endswith('/huge') else 0

    @property
    def status(self):
        return self.__dict__['status']

    @property
    def body(self):
        return b' ' * self.__dict__['size'] if self.__dict__['size'] else PAGES[self.url].encode()


class Browser:
    """The page object StealthyFetcher hands to page_setup / page_action: two media responses."""
    def __init__(self):
        self.handlers = []

    def on(self, event, handler):
        self.handlers.append(handler)

    def evaluate(self, *args):
        pass

    def wait_for_timeout(self, *args):
        pass

    def locator(self, css):
        return types.SimpleNamespace(evaluate_all=lambda script: [])


def get(url, **options):
    return Response(url)


def fetch(url, page_setup=None, page_action=None, **options):
    page = Browser()
    if page_setup:
        page_setup(page)
    for handler in page.handlers:
        handler(types.SimpleNamespace(url='https://cdn.example.invalid/live.m3u8',
                                      headers={'content-type': 'application/vnd.apple.mpegurl'}))
    if page_action:
        page_action(page)
    return Response(url)


lib = library.Library(str(folder / 'library.sqlite3'))
ids = {'collect': {}, 'subscriptions': {}, 'download': {}}
OPTION_FIELDS = dict(dynamic=False, scrolls=0, selector='', max_pages=1)


def raw(text):
    return messages.raw(text) if NEW else text


def collect(name, urls, title=None, subscription=None, chrome=True, **fields):
    """A collect job the way the window starts one (create_job), run by the version's own worker_main."""
    options = collector.CollectOptions(urls=list(urls), **dict(OPTION_FIELDS, **fields))
    job = lib.create_job('collect', title or raw(options.urls[0]), asdict(options), subscription)
    request = dict(asdict(options), job_id=job, history_path=str(folder / 'library.sqlite3'))
    path = folder / f'job-{name}.json'
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    with mock.patch.object(fetchers.Fetcher, 'get', staticmethod(get)), \
            mock.patch.object(fetchers.StealthyFetcher, 'fetch', staticmethod(fetch)), \
            mock.patch.object(collector, 'browser_available', lambda: chrome),             mock.patch.object(collector, 'ca_bundle', lambda: ''), \
            mock.patch('time.sleep', lambda seconds: None):
        collector.worker_main(str(path))
    ids['collect'][name] = job
    return job


# pages: a normal page, a page whose title is empty, a media start page, a dynamic capture, an HTTP
# error, a page over 8 MB, a CSS range that matches nothing
collect('pages', [BASE + '/list', BASE + '/untitled', BASE + '/start.mp4', BASE + '/gone', BASE + '/huge'])
collect('dynamic', [BASE + '/dynamic'], dynamic=True)
collect('selector', [BASE + '/list'], selector='.nothing-matches')
# the CollectOptions.validate errors (what the worker stores when its own validate() refuses)
collect('too_many', [f'{BASE}/p{n}' for n in range(21)])
collect('scrolls', [BASE + '/list'], scrolls=9)
collect('address', ['ftp://files.example.invalid/x'])
collect('proxy', [BASE + '/list'], proxy='not a proxy')
collect('chrome', [BASE + '/dynamic'], dynamic=True, chrome=False)
collect('resume', [BASE + '/list'], resume={'visited': [], 'todo': [{'url': 'ftp://x', 'depth': 0}]})

# the archive: Archive.prepare with the context the Runner builds (the stored link title and its page),
# then media() for one item, as an archived download does
archive = library.Archive(str(folder / 'archive'), lib)
for url in (BASE + '/start.mp4', 'https://cdn.example.invalid/live.m3u8', BASE + '/clip.mp4'):
    context = dict(lib.contexts([url]).get(url) or {})
    if context.get('job_id'):
        record = lib.job(context['job_id'])
        context['page'] = next((p for p in record['pages'] if p['url'] == context['page_url']), None)
    item = Path(archive.prepare(url, context))
    if url.endswith('clip.mp4'):
        (item / 'clip.mp4').write_bytes(b'clip')
        archive.media(item, {'filepath': str(item / 'clip.mp4'), 'title': raw('Clip one'), 'uploader': 'Example uploader'})

# subscription checks, each with the version's own composition of the job title and of last_error
if version == '1.7.0':
    import collection_ui
    import library_ui
elif NEW:
    import collection_ui


def job_title(row):
    if NEW:
        return messages.msg('value.job_title.subscription', name=row['name'])
    if version == '1.7.0':
        return library_ui.stored_text('value.job_title.subscription', name=row['name'])
    return '订阅：' + row['name']


def last_error(record, cancelled):
    if NEW or version == '1.7.0':
        return collection_ui.subscription_result(record, cancelled)[2]
    return '；'.join(record['data']['errors'][-2:]) or ('采集已停止' if cancelled else '检查未完成')


def check(name, subscription, cancelled=False, partial=False, **fields):
    row = next(item for item in lib.subscriptions() if item['id'] == subscription)
    options = row['data']['options']
    urls = options['urls']
    job = collect(name, urls, title=job_title(row), subscription=subscription,
                  **dict({key: options[key] for key in ('dynamic', 'scrolls', 'selector', 'max_pages') if key in options},
                         **fields))
    if partial:
        lib.change_job(job, status='partial')
    record = lib.job(job)
    successful = not cancelled and record['status'] == 'completed'
    lib.finish_subscription(subscription, record['pages'], successful, last_error(record, cancelled))
    return job


subscription_options = lambda urls, dynamic=False: dict(urls=urls, dynamic=dynamic, scrolls=0, selector='', max_pages=1)
untitled = lib.subscription('', subscription_options([BASE + '/list', BASE + '/gone', BASE + '/huge']))
named = lib.subscription('Weekly feed', subscription_options([BASE + '/start.mp4']))
dynamic = lib.subscription('Live feed', subscription_options([BASE + '/dynamic'], True))
ids['subscriptions'] = {'untitled': untitled, 'named': named, 'dynamic': dynamic}
check('check_errors', untitled)                      # two errors joined
check('check_cancelled', named, cancelled=True)      # 采集已停止 / value.subscription.stopped
check('check_unfinished', named, partial=True)       # 检查未完成 / value.subscription.unfinished
check('check_chrome', dynamic, chrome=False)         # needs_chrome: a text that holds the joiner itself

# a download job with the version's own placeholder title
A, B, C = ('https://media.example.invalid/' + name for name in 'abc')
YT1, YT2 = 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb'
R = 'https://media.example.invalid/limited'
output = folder / 'media'
output.mkdir(exist_ok=True)


def placeholder(urls, options):
    if NEW:
        return messages.msg('value.job_title.placeholder', count=len(urls), mode=messages.value('mode', options.mode))
    if version == '1.7.0':
        import ui_download
        return ui_download.stored_text('value.job_title.placeholder', count=len(urls), mode=options.mode)
    if version == '1.6.0':
        return f'{len(urls)} 条 · {options.mode}'
    return f'{len(urls)}条 · {options.mode}'


if version == '1.5.1':
    options = engine.Options(output=output)
    urls = [A, B]
    settings = asdict(options)
    settings.update(output=str(options.output), urls=urls)
    job = lib.create_job('download', placeholder(urls, options), settings)
    for index, url in enumerate(urls, 1):
        lib.download_event(job, 'item', {'index': index, 'total': 2, 'url': url})
        lib.download_event(job, 'item_done', {'index': index, 'cancelled': False, 'success': False, 'files': 0,
                                              'url': url})
    lib.download_event(job, 'done', {'success': 0, 'failed': 2, 'files': [], 'cancelled': False})
    ids['download']['placeholder'] = job
    engine.store_failed_job(urls, options)          # the 1.5.1 failed list (its legacy view)
else:
    import ui_download

    def bot(runner, command, index, total, files):
        line = 'ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm you’re not a bot. Use --cookies-from-browser'
        runner.emit('log', line)
        runner.bot_check = True
        runner._note_line(line)
        return 1, False, None

    def failure(runner, command, index, total, files):
        runner.emit('title', 'Song A')
        line = 'ERROR: [generic] a: HTTP Error 404: Not Found'
        runner.emit('log', line)
        runner._note_line(line)
        return 1, False, None

    def limited(runner, command, index, total, files):
        return 1, True, None

    def echo(runner, command, index, total, files):
        line = "ERROR: invalid Netscape format cookies file 'cookies.txt': '.example.invalid\tTRUE\t/\tFALSE\t0\tSID\tsecret-value'"
        kind, value = runner._intercept('log', line)
        runner.emit(kind, value)
        runner._note_line(line)
        return 1, False, None

    def run(name, urls, steps, **fields):
        options = engine.Options(output, **fields)
        data = {**options.to_dict(), 'urls': urls}
        job = lib.create_job('download', placeholder(urls, options), data)
        scripted = list(steps)

        def emit(kind, value):
            if kind in ui_download.HISTORY_KINDS:
                lib.download_event(job, kind, ui_download.history_value(kind, value, False))

        def run_once(command, index, total, files):
            step = scripted.pop(0)
            return step(runner, command, index, total, files)

        runner = engine.Runner(emit)
        engine.Runner.reset_pacing()
        with mock.patch.object(engine, 'PACE_SECONDS', (0, 0)), \
                mock.patch.object(engine, 'MAX_RATE_LIMIT_RETRIES', 0), \
                mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(urls, options)
        assert not scripted, (name, scripted)
        ids['download'][name] = job

    cookies = folder / 'cookies.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    missing = folder / ('long-folder-' + 'p' * 150) / 'cookies.txt'
    run('mixed', [A, YT1, YT2, R], [failure, bot, limited])
    run('echo', [B], [echo], cookies=str(cookies))
    run('missing', [C], [], cookies=str(missing))

with open(folder / f'ids-{version}.json', 'w', encoding='utf-8') as handle:
    json.dump(ids, handle)
'''

# ------------------------------------------------------------------ the page models (both renderers)
HARNESS = r'''
from unittest import mock


class Widget:
    """A stub of every widget the page models touch; it keeps the texts and rows they get."""
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

    def cget(self, name):
        return ''

    def fill(self, rows):
        self.rows = [[str(cell) for cell in values] for _iid, values in rows]

    def get(self, *args):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self, *args):
        pass

    def insert(self, *args, **options):
        pass

    def get_children(self):
        return [str(n) for n in range(len(self.rows))]


def render(library_ui, collection_ui, ui_download, lib, entries):
    """Every page model of the stored data, as the page code makes it: {name: [texts]}."""
    Page = type('Page', (library_ui.LibraryUI, collection_ui.CollectionUI, ui_download.DownloadUI), {})
    page = Page.__new__(Page)
    page.library = lib
    for name in ('collect_urls', 'collect_mode', 'collect_scrolls', 'collect_pages_limit', 'collect_selector',
                 'collect_text', 'collect_tree', 'pending_tree', 'subscription_note', 'archive_tree'):
        setattr(page, name, Widget())
    page.collect_filter, page.collect_search, page.archive_search = Widget(collection_ui.FILTERS[1]), Widget(), Widget()
    page.set_collect_status = lambda text, tone=None: setattr(page, 'collect_status_text', text)
    page.collection_selection_changed = lambda: None
    page._entry_options = lambda entry, log=True: None
    page.library_busy = lambda error: None
    page.visible_pending = []
    result = {}
    for row in lib.history(1000):
        key = row['id']
        record = lib.job(key)
        result['history ' + key] = [str(cell) for cell in library_ui.history_row(row)]
        result['note ' + key] = [str(library_ui.LibraryUI.history_note_text(record))]
        if record['kind'] == 'collect':
            library_ui.LibraryUI.load_collection_record(page, record)
            result['collect ' + key] = [cell for row_ in page.collect_tree.rows for cell in row_] + \
                [str(page.collect_status_text)]
    for row in lib.subscriptions():
        page.selected_subscription = lambda row=row: row
        library_ui.LibraryUI._subscription_selection(page)
        result['subscription ' + row['id']] = [str(cell) for cell in library_ui.subscription_row(row)]
        result['subscription note ' + row['id']] = [str(page.subscription_note.text)]
        result['pending ' + row['id']] = [cell for row_ in page.pending_tree.rows for cell in row_]
    library_ui.LibraryUI.refresh_archives(page)
    result['archives'] = [cell for row_ in page.archive_tree.rows for cell in row_]
    page.row_model, page.job_options, page.unfinished = [], None, list(entries)
    ui_download.DownloadUI._append_tail(page, list(entries))
    shown = []
    with mock.patch.object(ui_download.messagebox, 'showinfo') as dialog:
        for index, model in enumerate(page.row_model):
            ui_download.DownloadUI.show_row_reason(page, index)
            shown += [str(model['title']), str(model['detail']), str(dialog.call_args.args[0]),
                      str(dialog.call_args.args[1])]
    result['unfinished'] = shown
    return result
'''

RENDER_1_7_0 = r'''
import json, os, sys
src, db, out = sys.argv[1:4]
sys.path.insert(0, src)
import i18n
i18n.set_language('zh')
import collection_ui
import library
import library_ui
import ui_download
import unfinished
namespace = {}
exec(compile(open(os.path.join(os.path.dirname(out), 'harness.py'), encoding='utf-8').read(), 'harness', 'exec'), namespace)
result = namespace['render'](library_ui, collection_ui, ui_download, library.Library(db), unfinished.load())
with open(out, 'w', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False, indent=1)
'''


def _harness():
    namespace = {}
    exec(compile(HARNESS, 'harness', 'exec'), namespace)
    return namespace['render']


def child_environment(state, guarded=False):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', YTDLP_DESKTOP_LANG='zh', YTDLP_DESKTOP_STATE=str(state))
    if not guarded:
        environment.pop(_sandbox.MSG_GUARD, None)           # the old code has no messages module
    return environment


def run_child(script, arguments, cwd, environment):
    done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script)] + [str(item) for item in arguments],
                          cwd=str(cwd), env=environment, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=600)
    if done.returncode:
        raise AssertionError(f'{script.name} {arguments[1:2]} failed:\n{done.stderr[-6000:]}')
    return done


def produce(folder, versions):
    """Run the producers of ``versions`` in that order on one library and one state folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'state').mkdir(exist_ok=True)
    script = folder / 'producer.py'
    script.write_text(PRODUCER, encoding='utf-8')
    ids = {}
    for version in versions:
        source = _sandbox.SRC if version == '1.8.0' else _sandbox.release_src(version)
        run_child(script, [source, version, folder, ROOT / 'vendor'], folder,
                  child_environment(folder / 'state', guarded=version == '1.8.0'))
        ids[version] = json.loads((folder / f'ids-{version}.json').read_text(encoding='utf-8'))
    return ids


def copy_of(folder, name):
    """A copy of a produced folder (library and state), so each render reads the same bytes."""
    target = folder.parent / f'{folder.name}-{name}'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    shutil.copyfile(folder / 'library.sqlite3', target / 'library.sqlite3')
    shutil.copytree(folder / 'state', target / 'state')
    return target


def render_by_1_7_0(folder):
    copy = copy_of(folder, 'render-1-7-0')
    (copy / 'harness.py').write_text(HARNESS, encoding='utf-8')
    script = copy / 'render.py'
    script.write_text(RENDER_1_7_0, encoding='utf-8')
    out = copy / 'render.json'
    run_child(script, [_sandbox.release_src('1.7.0'), copy / 'library.sqlite3', out], copy,
              child_environment(copy / 'state'))
    return json.loads(out.read_text(encoding='utf-8'))


def render_by_1_8_0(folder, lang, name):
    copy = copy_of(folder, f'render-1-8-0-{name}-{lang}')
    with _sandbox.state_patch(copy / 'state'), _sandbox.language(lang):
        entries = unfinished.load()
        return _harness()(library_ui, collection_ui, ui_download, library.Library(copy / 'library.sqlite3'), entries)


def chinese(result):
    """[(model, text)] of every rendered text with a CJK character."""
    return [(name, text) for name, texts in result.items() for text in texts if CJK.search(text)]


class ReplayCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _sandbox.out('english-replay')
        if cls.base.exists():
            shutil.rmtree(cls.base)
        cls.old = cls.base / 'old'
        cls.new = cls.base / 'new'
        cls.old_ids = produce(cls.old, VERSIONS)
        cls.new_ids = produce(cls.new, ('1.8.0',))


class EnglishReplayTests(ReplayCase):
    """G4b: the data of 1.5.1 / 1.6.0 / 1.7.0 rendered by 1.8.0 in English."""

    def test_the_replay_produced_every_scenario(self):
        store = library.Library(self.old / 'library.sqlite3')
        jobs = store.history(1000)
        self.assertEqual(len(jobs), 3 * 13 + 1 + 2 * 3, 'collect, check and download jobs of the three versions')
        record = store.job(self.old_ids['1.7.0']['collect']['pages'])
        self.assertEqual([page['url'] for page in record['pages']][:3], ['https://pages.example.invalid/list',
                                                                         'https://pages.example.invalid/untitled',
                                                                         'https://pages.example.invalid/start.mp4'])
        self.assertEqual(len(record['data']['errors']), 2, 'the HTTP error and the page over 8 MB')
        with _sandbox.state_patch(copy_of(self.old, 'count') / 'state'):
            entries = unfinished.load()
        self.assertGreaterEqual(len(entries), 6, entries)
        self.assertEqual(len(store.archives()), 3, "the three archived links (the later versions update the same folders)")

    def test_no_chinese_on_any_page_model(self):
        result = render_by_1_8_0(self.old, 'en', 'old')
        self.assertGreater(len(result), 50)
        self.assertEqual(chinese(result), [])
        for name, texts in result.items():
            for text in texts:
                self.assertNotIn('[代理]', text, name)

    def test_the_three_1_7_0_misses_and_the_hard_cases_read_in_english(self):
        result = render_by_1_8_0(self.old, 'en', 'cases')
        with _sandbox.language('en'):
            direct, request = i18n.t('value.link_kind.direct'), i18n.t('value.link_kind.dynamic_request')
            http = i18n.t('core.collect.http_status', status=503)
            large = i18n.t('core.collect.too_large')
            chrome = i18n.t('core.collect.needs_chrome')
            echo = i18n.t('core.cookies.line_failed_item')
        for version in VERSIONS:
            pages = result['collect ' + self.old_ids[version]['collect']['pages']]
            self.assertIn(direct, pages, f'{version}: 媒体直链 (the media start page)')
            dynamic = result['collect ' + self.old_ids[version]['collect']['dynamic']]
            self.assertIn(request, dynamic, f'{version}: 动态页面媒体请求 (the dynamic capture)')
            note = result['note ' + self.old_ids[version]['collect']['pages']][0]
            self.assertIn(large, note, f'{version}: the last collect error of the job')
            untitled = self.old_ids[version]['subscriptions']['untitled']
            joined = result['subscription note ' + untitled][0]
            self.assertIn(http, joined, f'{version}: the HTTP error ({"1.5.1 form, no spaces" if version == "1.5.1" else "with spaces"})')
            self.assertIn(large, joined, f'{version}: the joined last_error')
            dynamic = self.old_ids[version]['subscriptions']['dynamic']
            self.assertTrue(result['subscription note ' + dynamic][0].endswith(chrome),
                            f'{version}: a last_error that holds the joiner itself stays whole (C-H-5)')
        with _sandbox.language('en'):
            head, tail = (part.strip() for part in i18n.t('core.cookies.missing', file='|').split('|'))
            started = i18n.t('core.engine.cannot_start_link', error='|').split('|')[0]
        self.assertIn(direct, result['archives'], 'the archive of the media start page')
        self.assertIn(request, result['archives'], 'the archive of the dynamic capture')
        self.assertIn('Clip one', result['archives'])
        reasons = ' '.join(result['unfinished'])
        self.assertIn(echo, reasons, 'the cookies echo of 1.6.0 / 1.7.0 (D-H-1)')
        dialogs = [text for text in result['unfinished'] if head in text and '\n\n' in text]
        self.assertEqual(len(dialogs), 1, 'the reason dialog of the item that could not start (one entry per link)')
        for text in dialogs:
            self.assertTrue(text.endswith(tail), text)
        for version in ('1.6.0', '1.7.0'):
            note = result['note ' + self.old_ids[version]['download']['missing']][0]
            # the stored reason is the 70-character cut of 1.6.0 / 1.7.0; it reads as the English message
            self.assertIn(head, note, f'{version}: the cut reason reads in English (D-H-2)')
            self.assertNotIn(started, note, f'{version}: the reason (not the error) is shown, as in 1.7.0')

    def test_self_test_the_legacy_reader_of_the_1_7_0_first_round_finds_the_misses(self):
        """With the frozen 1.5.1 variants removed and the title kind limited to value.page_title. (the
        state 1.7.0's own tests passed in its first review round), the replay shows Chinese exactly where
        the three review rounds of 1.7.0 found it: 媒体直链, 动态页面媒体请求 and the 1.5.1 collect errors."""
        variants = {tuple(item) for item in i18n_tool._module_constants(
            _sandbox.release_src('1.7.0') / 'i18n_maps.py', {'VARIANTS'})['VARIANTS']}
        self.assertEqual(len(variants), 11)
        first_round = i18n_legacy.Reader(templates=[item for item in i18n_legacy.DATA.TEMPLATES
                                                    if tuple(item) not in variants],
                                         title_keys=('value.page_title.',))
        with mock.patch.object(i18n_legacy, '_default', first_round), \
                _sandbox.fixture_texts('G4b self-test: the 1.7.0 first-round reader leaves app text unlifted'):
            result = render_by_1_8_0(self.old, 'en', 'first-round')
        found = {text for _name, text in chinese(result)}
        self.assertIn('媒体直链', found)
        self.assertIn('动态页面媒体请求', found)
        self.assertTrue(any('网站返回HTTP 503' in text for text in found), sorted(found))
        self.assertFalse(any('网站返回 HTTP 503' in text for text in found), 'the 1.6.0 form lifts without variants')

    def test_the_archive_files_1_8_0_writes_for_stored_contexts_are_english(self):
        copy = copy_of(self.old, 'archive-en')
        store = library.Library(copy / 'library.sqlite3')
        urls = ['https://pages.example.invalid/start.mp4', 'https://cdn.example.invalid/live.m3u8',
                'https://pages.example.invalid/clip.mp4']
        contexts = store.contexts(urls)
        self.assertEqual(sorted(contexts), sorted(urls))
        root = copy / 'archive'
        with _sandbox.language('en'):
            archive = library.Archive(root, store)
            for url in urls:
                folder = archive.prepare(url, contexts[url])
                archive.save(folder, dict(json.loads((folder / '资料信息.json').read_text(encoding='utf-8')),
                                          files=['clip.mp4'], transcripts=[]))
        written = [path for path in root.rglob('*') if path.is_file() and path.suffix in ('.txt', '.md')]
        self.assertGreaterEqual(len(written), 3)
        for path in written:
            text = path.read_text(encoding='utf-8-sig')
            for name in ON_DISK:
                text = text.replace(name, '')
            self.assertEqual(CJK.findall(text), [], f'{path.name}: {text[:200]!r}')


class ZhDumpTests(ReplayCase):
    """G6: zh pages of 1.8.0 equal those of the frozen 1.7.0 page code, for old and for new data."""

    # (1.8.1) the note names 忽略; the only sanctioned zh difference (build/v181/design/DESIGN.md 5.2, 6.2:
    # the owner asked for the wording of library.subscription.status_note to follow the Ignore button).
    OLD_STATUS_NOTE = '仅下载成功后移出；可多选新增内容添加到下载列表。'

    def assertSameDump(self, mine, theirs):
        self.assertEqual(sorted(mine), sorted(theirs))
        new_note = i18n.CATALOG['zh']['library.subscription.status_note']
        for name in sorted(mine):
            expected = theirs[name]
            if name.startswith('subscription note '):
                expected = [text.replace(self.OLD_STATUS_NOTE, new_note) for text in expected]
            self.assertEqual(mine[name], expected, name)

    def test_old_data_zh_is_what_1_7_0_shows(self):
        theirs = render_by_1_7_0(self.old)
        mine = render_by_1_8_0(self.old, 'zh', 'old')
        self.assertGreater(len(mine), 50)
        self.assertSameDump(mine, theirs)

    def test_new_data_zh_is_what_1_7_0_shows_and_en_has_no_chinese(self):
        theirs = render_by_1_7_0(self.new)
        mine = render_by_1_8_0(self.new, 'zh', 'new')
        self.assertSameDump(mine, theirs)
        english = render_by_1_8_0(self.new, 'en', 'new')
        self.assertEqual(chinese(english), [])
        self.assertEqual(sorted(english), sorted(mine))

    def test_the_new_rows_carry_sidecars_and_the_old_rows_none(self):
        import sqlite3
        with sqlite3.connect(self.new / 'library.sqlite3') as db:
            new = [json.loads(data) for (data,) in db.execute('SELECT data FROM jobs')]
        with sqlite3.connect(self.old / 'library.sqlite3') as db:
            old = [json.loads(data) for (data,) in db.execute('SELECT data FROM jobs')]
        self.assertTrue(any('msgs' in data for data in new), '1.8.0 wrote sidecars')
        self.assertFalse(any('msgs' in data for data in old), 'the old versions wrote none')


class SentenceTests(unittest.TestCase):
    """(MESSAGES.md 15 A11.5) engine._sentence keeps the Message an OSError carries."""

    def test_an_os_error_with_a_message(self):
        error = OSError(msg('core.unfinished.cannot_set_aside', path='x.json'))
        with messages.guard_capture() as records:
            shown = engine._sentence(error)
            line = msg('core.engine.cannot_start_engine', error=shown)
        self.assertEqual(records, [])
        self.assertIsInstance(shown, Message)
        self.assertEqual(shown.key, 'core.unfinished.cannot_set_aside')
        zh = messages.zh(msg('core.unfinished.cannot_set_aside', path='x.json'))
        self.assertEqual(messages.zh(shown), zh.rstrip().rstrip('。.．').rstrip(), 'the 1.7.0 zh text')
        with _sandbox.language('en'):
            self.assertEqual(messages.show(shown), i18n.t('core.unfinished.cannot_set_aside', path='x.json'))
            self.assertIsNone(CJK.search(messages.show(line)))
        plain = OSError(2, '系统找不到指定的文件。')
        self.assertEqual(engine._sentence(plain), str(plain).rstrip().rstrip('。.．').rstrip())
        self.assertIs(type(engine._sentence(plain)), str)


def explain_error_1_7_0():
    """engine.explain_error of the frozen 1.7.0 source with the constants it reads (no 1.7.0 import)."""
    tree = ast.parse((_sandbox.release_src('1.7.0') / 'engine.py').read_text(encoding='utf-8'))
    wanted = {'ERROR_REASONS', 'NETWORK_REASON', 'BOT_CHECK_REASON', 'STALE_REASON', '_EXTRACTOR_PREFIX'}
    body = [node for node in tree.body
            if isinstance(node, ast.Assign) and {getattr(t, 'id', None) for t in node.targets} & wanted
            or isinstance(node, ast.FunctionDef) and node.name == 'explain_error']
    namespace = {'re': re}
    exec(compile(ast.Module(body=body, type_ignores=[]), 'release-sources/1.7.0/src/engine.py', 'exec'), namespace)
    return namespace['explain_error']


class ExplainErrorTests(unittest.TestCase):
    """(final review 1, ZH-1, MESSAGES.md 15 A13.3) explain_error's fallback for a stored keyless error
    that the legacy reader lifts to a Message: the zh text is the 1.7.0 one (stripped, the extractor
    prefix removed, 70 characters), the key is kept, and the history note is 1.7.0's."""

    ERRORS = ('资料库无法写入：disk I/O error  ',
              '[youtube] abcdefghijk: Something failed here，等待 3 秒；可随时点击停止。',
              '  [youtube] abcdefghijk: Something failed here，等待 3 秒；可随时点击停止。',
              '[youtube] abcdefghijk: Something failed here 秒内没有结束',
              '  [youtube] abcdefghijk: Something failed here 秒内没有结束',
              '[youtube] abcdefghijk: Something failed here 等 3 条',
              '  [youtube] abcdefghijk: Something failed here 等 3 条',
              '资料库无法写入：disk I/O error',
              '[youtube] abcdefghijk: Something failed here')

    def test_lifted_messages_give_the_1_7_0_text(self):
        old = explain_error_1_7_0()
        lifted_seen = 0
        for text in self.ERRORS:
            lifted = messages.decode({'errors': [text]}, 'job')['errors'][0]
            lifted_seen += isinstance(lifted, Message)
            for value in (text, messages.raw(text), lifted):
                for code, bot, stale in ((1, False, False), (2, False, False), (1, False, True)):
                    with self.subTest(text=text, value=type(value).__name__, code=code, stale=stale):
                        with messages.guard_capture() as records:
                            reason = engine.explain_error(value, code, bot, stale)
                        self.assertEqual(records, [])
                        self.assertEqual(messages.zh(reason), old(text, code, bot, stale))
                        if isinstance(value, Message) and not stale:
                            self.assertIsInstance(reason, Message)
                            self.assertEqual(reason.key, value.key)
                            with _sandbox.language('en'):
                                self.assertEqual(messages.show(reason), messages.show(value), 'en shows it whole')
            record = {'kind': 'download', 'data': {'errors': [lifted]}, 'pages': []}
            note = library_ui.LibraryUI.history_note_text(record)
            self.assertEqual(note, i18n.t('library.history.downloaded', completed=0)
                             + i18n.t('library.history.last_error', error=old(text)[:130]))
        self.assertGreaterEqual(lifted_seen, 7, 'the legacy reader lifts the edge cases to messages')


if __name__ == '__main__':
    unittest.main()
