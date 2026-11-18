"""1.8.0 library data read and written by the frozen 1.5.1 / 1.6.0 / 1.7.0 code (MESSAGES.md 3.4, 5.1, 5.2; WP-3).

    python -X utf8 -B test_rollback_readers.py

1.8.0 fills a sandbox library with every record kind of 4.3 and every value type (messages with
nested params and enum values, raw titles, plain engine text, a clauses last_error, a job with a
1.6.0-only format after 1.8.0's read-modify-writes, a row whose sidecar holds foreign entries) and an
archive folder.  Two copies are made per old version: A as 1.8.0 left it, and B, the same data
without any `msgs` key, which is the data 1.7.0 writes for the same operations (every old field
keeps its 1.7.0 bytes, I1).  In a child process whose import path is release-sources/<v>/src (-B,
the sandbox environment), the old version runs its readers (history, job, subscriptions, pending,
archives, contexts; 1.6.0 / 1.7.0 history_title and history_note_text, 1.7.0 subscription_row and
the subscription note, 1.5.1 LibraryUI.history_selection / subscription_selection /
restore_download_options and 1.6.0 subscription_selection with stub widgets) and its writers
(change_job, collection_event, download_event item_done / done / title, finish_subscription,
configure_subscription, archive_index, Archive.prepare / save / media) on A and on B.

Assertions: no exception; every displayed text of A equals that of B, before and after the writes;
after the writes every old field of A equals that of B (row by row, file by file); `msgs` survives
every old read-modify-write unchanged (its entries of changed texts are orphaned, never rewritten)
and is absent exactly from the rows an old writer replaced whole; 1.5.1 resumes the 1.6.0-only-format
job with a mode of its own, 1.6.0 / 1.7.0 get the real one; then 1.8.0 reads every row of A (orphans
ignored, changed texts lifted, never 'stored') and keeps the foreign entries on its next write.
"""
import _sandbox
import contextlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import engine
import formats
import library
import messages
from library import Archive, Library
from messages import Message, msg, raw, value

A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE, PAGE2 = 'https://example.invalid/list', 'https://example.invalid/list?page=2'
MEDIA, MEDIA2 = 'https://cdn.example.invalid/clip.mp4', 'https://cdn.example.invalid/live.m3u8'
VOLATILE = {'last_check', 'downloaded'}
FOREIGN = {
    '找不到任务记录。': {'k': 'core.future.unknown', 'p': {'a': [1, 2]}},
    '网站返回 HTTP 7，可尝试动态模式或稍后重试。': {'k': 'core.collect.http_status', 'p': {'status': 7, 'extra': 1}},
}

CHILD = r'''
import json, os, sys
src, version, spec_path, out_path = sys.argv[1:5]
sys.path.insert(0, src)
import library
import library_ui
from library_ui import LibraryUI

spec = json.load(open(spec_path, encoding='utf-8'))


class Widget:
    def __init__(self, selection=()):
        self._selection, self.text, self.rows, self.value = list(selection), None, [], None

    def selection(self):
        return list(self._selection)

    def selection_set(self, *items):
        pass

    def selection_remove(self, *items):
        pass

    def configure(self, **options):
        if 'text' in options:
            self.text = options['text']

    def fill(self, rows):
        self.rows = [list(values) for _iid, values in rows]

    def delete(self, *items):
        self.rows = []

    def get_children(self):
        return [str(n) for n in range(len(self.rows))]

    def insert(self, parent, index, iid=None, values=()):
        self.rows.append(list(values))

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


def page_of(lib):
    page = LibraryUI.__new__(LibraryUI)
    page.library, page.busy, page.visible_pending, page.download_referers = lib, False, [], {}
    for name in ('history_tree', 'history_note', 'history_view', 'history_resume', 'pending_tree', 'subscription_tree',
                 'subscription_note', 'output', 'mode', 'format', 'resolution', 'playlist', 'batch_safe',
                 'archive_enabled', 'archive_root'):
        setattr(page, name, Widget())
    page.on_format = lambda: None
    page.library_busy = lambda error: None
    return page


def cells(row):
    if version == '1.5.1':
        return [row['kind'], row['title'], row['status']]
    if version == '1.6.0':
        return [library_ui.history_title(row), library_ui.history_status(row)]
    return list(library_ui.history_row(row)[:3])


def read(folder):
    lib = library.Library(os.path.join(folder, 'library.sqlite3'))
    root = os.path.join(folder, 'archive')
    shown = {'history': {row['id']: cells(row) for row in lib.history()}}
    page = page_of(lib)
    for job in spec['jobs']:
        record = lib.job(job)
        if version == '1.5.1':
            page.history_tree = Widget([job])
            LibraryUI.history_selection(page)
            shown['note ' + job] = page.history_note.text
        else:
            shown['note ' + job] = LibraryUI.history_note_text(record)
        shown['pages ' + job] = [[p['title']] + [link['title'] for link in p['links']] for p in record['pages']]
        shown['errors ' + job] = record['data'].get('errors')
    for row in lib.subscriptions():
        page.subscription_tree, page.pending_tree, page.visible_pending = Widget([row['id']]), Widget(), []
        if version == '1.7.0':
            LibraryUI._subscription_selection(page)
            listed = list(library_ui.subscription_row(row))
            shown['row ' + row['id']] = [listed[0], listed[1], listed[3]]
        else:
            LibraryUI.subscription_selection(page)
        shown['subscription ' + row['id']] = [row['name'], page.subscription_note.text, page.pending_tree.rows,
                                              [item['title'] for item in lib.pending(row['id'])]]
    shown['archives'] = sorted([os.path.relpath(item['folder'], root), item['title'], item.get('page_title')]
                               for item in lib.archives())
    shown['contexts'] = {url: item['title'] for url, item in sorted(lib.contexts(spec['context_urls']).items())}
    options = lib.job(spec['modern'])['data']['options']
    if version == '1.5.1':
        LibraryUI.restore_download_options(page, options)        # MODES.index(raw['mode']) of 1.5.1
        shown['restore'] = [page.mode.value, page.format.value]
    else:
        shown['restore'] = [options['mode'], 'mode_v2' in options]
    return shown


def write(folder):
    lib = library.Library(os.path.join(folder, 'library.sqlite3'))
    jobs = spec['ids']
    lib.change_job(jobs['multi'], status='partial')
    lib.change_job(jobs['foreign'], status='completed')
    lib.change_job(jobs['modern'], status='partial')
    lib.collection_event(jobs['collect'], 'collect_error', '页面超过 8 MB，请缩小采集范围。')
    lib.collection_event(jobs['collect'], 'collect_checkpoint', {'visited': [spec['page']], 'todo': []})
    lib.collection_event(jobs['collect'], 'collect_page', {
        'url': spec['page2'], 'title': '网页', 'text': '', 'markdown': '', 'next': None,
        'links': [{'url': spec['media2'], 'title': '动态页面媒体请求', 'kind': '媒体地址', 'source': spec['page2']}],
        '_checkpoint': {'visited': [spec['page'], spec['page2']], 'todo': []}})
    lib.download_event(jobs['untitled'], 'item', {'url': spec['a']})
    lib.download_event(jobs['untitled'], 'item_done', {'success': False, 'url': spec['a'], 'error': 'ERROR: [generic] a: boom',
                                                       'reason': '链接不存在（404）'})
    lib.download_event(jobs['untitled'], 'title', 'Other Song')
    lib.download_event(jobs['multi'], 'done', {'cancelled': False, 'failed': 0})
    lib.finish_subscription(spec['subscriptions']['untitled'], [], False, 'old version error text')
    lib.finish_subscription(spec['subscriptions']['named'], lib.job(jobs['collect'])['pages'], True)
    lib.configure_subscription(spec['subscriptions']['untitled'], False, 900)
    root = os.path.join(folder, 'archive')
    archive = library.Archive(root, lib)
    context = lib.contexts([spec['media']])[spec['media']]
    folder_path = archive.prepare(spec['media'], dict(context, page=lib.job(jobs['collect'])['pages'][0]))
    archive.media(folder_path, {'filepath': os.path.join(str(folder_path), 'clip.mp4'), 'title': 'Clip 2'})
    with open(os.path.join(str(folder_path), '资料信息.json'), encoding='utf-8') as handle:
        lib.archive_index(folder_path, json.load(handle))
    lib.history()


result = {}
for side in ('A', 'B'):
    result[side] = {'before': read(spec[side])}
for side in ('A', 'B'):
    write(spec[side])
for side in ('A', 'B'):
    result[side]['after'] = read(spec[side])
with open(out_path, 'w', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False)
'''


@contextlib.contextmanager
def opened(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def strip(item):
    """A stored object without its sidecar and the time stamps."""
    if isinstance(item, dict):
        return {key: strip(child) for key, child in item.items() if key != 'msgs' and key not in VOLATILE}
    if isinstance(item, (list, tuple)):
        return type(item)(strip(child) for child in item)
    return item


def rows(path, root):
    """{(table, key): (columns..., data object)} of a library, archive folders relative to ``root``."""
    found = {}
    with opened(path) as db:
        for job, kind, title, status, data in db.execute('SELECT id, kind, title, status, data FROM jobs'):
            found[('jobs', job)] = (kind, title, status, json.loads(data))
        for job, url, data in db.execute('SELECT job, url, data FROM pages'):
            found[('pages', job, url)] = (json.loads(data),)
        for key, name, enabled, interval, data in db.execute('SELECT id, name, enabled, interval, data FROM subscriptions'):
            found[('subscriptions', key)] = (name, enabled, interval, json.loads(data))
        for key, item, pending, data in db.execute('SELECT subscription, key, pending, data FROM entries'):
            found[('entries', key, item)] = (pending, json.loads(data))
        for folder, data in db.execute('SELECT folder, data FROM archives'):
            found[('archives', os.path.relpath(folder, root))] = (json.loads(data),)
        found[('downloads',)] = tuple(sorted(db.execute('SELECT * FROM downloads')))
    return found


def files(root):
    found = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            relative = str(path.relative_to(root))
            found[relative] = json.loads(path.read_text(encoding='utf-8')) if path.name.endswith('.json') \
                else path.read_bytes()
    return found


def sidecars(path, root):
    found = {}
    for key, columns in rows(path, root).items():
        if key == ('downloads',):
            continue
        data = columns[-1]
        if isinstance(data, dict) and 'msgs' in data:
            found[key] = data['msgs']
    for relative, data in files(root).items():
        if isinstance(data, dict) and 'msgs' in data:
            found[('file', relative)] = data['msgs']
    return found


def without_sidecars(path, root):
    with opened(path) as db:
        for table, key in (('jobs', 'id'), ('pages', 'rowid'), ('subscriptions', 'id'), ('entries', 'rowid'),
                           ('archives', 'folder')):
            for ident, text in list(db.execute(f'SELECT {key}, data FROM {table}')):
                data = json.loads(text)
                if isinstance(data, dict) and 'msgs' in data:
                    data.pop('msgs')
                    db.execute(f'UPDATE {table} SET data=? WHERE {key}=?', (json.dumps(data, ensure_ascii=False), ident))
    for record in Path(root).rglob('资料信息.json'):
        data = json.loads(record.read_text(encoding='utf-8'))
        data.pop('msgs', None)
        record.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class RollbackReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.base = Path(cls.directory.name)
        cls.source = cls.base / '1.8.0'
        cls.source.mkdir()
        cls.spec = cls.fill(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @classmethod
    def fill(cls, folder):
        """The 1.8.0 library: every record kind, every value type, written by 1.8.0's own code."""
        store = Library(folder / 'library.sqlite3')
        output = str(folder / 'media')

        def options(mode=formats.MODES[0], links=(A, B)):
            return dict(engine.Options(Path(output), mode=mode).to_dict(), urls=list(links))

        ids = {}
        ids['multi'] = store.create_job('download', msg('value.job_title.placeholder', count=2,
                                                        mode=value('mode', formats.MODES[0])), options())
        store.download_event(ids['multi'], 'item', {'url': A})
        store.download_event(ids['multi'], 'title', 'Some Song')
        store.download_event(ids['multi'], 'item_done', {
            'success': False, 'url': A, 'reason': msg('core.reason.http_403'),
            'error': msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=2100, url='youtu.be/x'))})
        store.download_event(ids['multi'], 'item', {'url': B})
        store.download_event(ids['multi'], 'item_done', {'success': True, 'url': B})
        ids['untitled'] = store.create_job('download', msg('value.job_title.placeholder', count=2,
                                                           mode=value('mode', formats.MODES[2])), options(formats.MODES[2]))
        store.download_event(ids['untitled'], 'item_done', {'success': False, 'url': B, 'error': raw('ERROR: [youtube] b: gone'),
                                                            'reason': msg('core.reason.unavailable')})
        store.download_event(ids['untitled'], 'done', {'cancelled': False, 'failed': 1})
        # The 1.6.0-only format, through 1.8.0's read-modify-writes (3.4).
        ids['modern'] = store.create_job('download', msg('value.job_title.placeholder', count=2,
                                                         mode=value('mode', formats.MODES[5])), options(formats.MODES[5]))
        store.download_event(ids['modern'], 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        store.change_job(ids['modern'], status='running', owner=None, worker=None)
        store.history()
        store.change_job(ids['modern'], worker=None)
        store.download_event(ids['modern'], 'done', {'cancelled': True, 'failed': 0})
        ids['collect'] = store.create_job('collect', raw(PAGE), dict(urls=[PAGE], dynamic=False, scrolls=0, selector='',
                                                                     max_pages=2))
        store.collection_event(ids['collect'], 'collect_page', {
            'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': 'Body', 'markdown': 'Body', 'next': None,
            'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE},
                      {'url': MEDIA2, 'title': msg('value.link_kind.dynamic_request'), 'kind': '媒体地址', 'source': PAGE},
                      {'url': A, 'title': raw('Song A'), 'kind': '视频页面', 'source': PAGE},
                      {'url': C, 'title': raw('Web'), 'kind': '网页链接', 'source': PAGE}],
            '_checkpoint': {'visited': [PAGE], 'todo': [{'url': PAGE2, 'depth': 1}]}})
        store.collection_event(ids['collect'], 'collect_error', msg('core.collect.http_status', status=503))
        store.collection_event(ids['collect'], 'collect_error', 'curl: (7) Failed to connect')
        store.change_job(ids['collect'], status='partial')
        ids['check'] = store.create_job('collect', msg('value.job_title.subscription', name=msg('value.subscription.untitled')),
                                        dict(urls=[PAGE], dynamic=False, scrolls=0, selector='', max_pages=1))
        store.change_job(ids['check'], status='completed')
        ids['foreign'] = store.create_job('download', raw('Foreign row'), options())
        with opened(store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (ids['foreign'],)).fetchone()[0])
            data.update(errors=list(FOREIGN), last_reason={'error': list(FOREIGN)[-1], 'reason': list(FOREIGN)[0]},
                        msgs=dict(FOREIGN))
            db.execute('UPDATE jobs SET data=?, status=? WHERE id=?', (json.dumps(data, ensure_ascii=False), 'partial',
                                                                       ids['foreign']))
        subscriptions = {'untitled': store.subscription('', dict(urls=[PAGE])),
                         'named': store.subscription('My list', dict(urls=[PAGE]))}
        pages = store.job(ids['collect'])['pages']
        store.finish_subscription(subscriptions['untitled'], pages, True)
        store.finish_subscription(subscriptions['untitled'], [], False, messages.clauses(
            [msg('core.collect.needs_chrome'), msg('core.collect.http_status', status=503)]))
        store.finish_subscription(subscriptions['named'], pages, True)
        root = folder / 'archive'
        archive = Archive(root, store)
        item = archive.prepare(MEDIA, dict(store.contexts([MEDIA])[MEDIA], page=pages[0]))
        (item / 'clip.mp4').write_bytes(b'clip')
        archive.media(item, {'filepath': str(item / 'clip.mp4'), 'title': 'Clip', 'uploader': 'Qq'})
        archive.prepare(MEDIA2, dict(store.contexts([MEDIA2])[MEDIA2], page=pages[0]))
        return {'ids': ids, 'jobs': list(ids.values()), 'subscriptions': subscriptions, 'modern': ids['modern'],
                'context_urls': [MEDIA, MEDIA2, A], 'page': PAGE, 'page2': PAGE2, 'media': MEDIA, 'media2': MEDIA2, 'a': A}

    def copy(self, version, side):
        target = self.base / version / side
        shutil.copytree(self.source, target)
        with opened(target / 'library.sqlite3') as db:
            for (folder,) in list(db.execute('SELECT folder FROM archives')):
                moved = str(target / 'archive' / Path(folder).relative_to(self.source / 'archive'))
                db.execute('UPDATE archives SET folder=? WHERE folder=?', (moved, folder))
        if side == 'B':
            without_sidecars(target / 'library.sqlite3', target / 'archive')
        return target

    def run_version(self, version):
        sides = {side: self.copy(version, side) for side in ('A', 'B')}
        spec = dict(self.spec, A=str(sides['A']), B=str(sides['B']))
        spec_path, out_path, script = (self.base / version / name for name in ('spec.json', 'out.json', 'child.py'))
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        script.write_text(CHILD, encoding='utf-8')
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', YTDLP_DESKTOP_LANG='zh')
        environment.pop(_sandbox.MSG_GUARD, None)           # the old code has no messages module
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(_sandbox.release_src(version)), version,
                               str(spec_path), str(out_path)], cwd=str(self.base / version), env=environment,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        self.assertEqual(done.returncode, 0, f'{version} raised:\n{done.stderr[-4000:]}')
        return sides, json.loads(out_path.read_text(encoding='utf-8'))

    def check_version(self, version):
        before = sidecars(self.source / 'library.sqlite3', self.source / 'archive')
        sides, shown = self.run_version(version)
        # 1. Old readers: the sidecar changes nothing they show, before and after their own writes.
        for moment in ('before', 'after'):
            self.assertEqual(shown['A'][moment], shown['B'][moment], f'{version} {moment}: displayed texts differ')
        first = shown['A']['before']
        self.assertEqual(first['history'][self.spec['ids']['multi']][1 if version != '1.6.0' else 0],
                         'Some Song 等 2 条')
        self.assertEqual(first['errors ' + self.spec['ids']['foreign']], list(FOREIGN))
        if version == '1.5.1':
            mode, label = first['restore']
            self.assertIn(mode, formats.LEGACY_MODES, '1.5.1 resumes the job with a mode of its own')
            self.assertTrue(label)
        else:
            self.assertEqual(first['restore'], [formats.MODES[5], False], f'{version} reads the real format back')
        # 2. Old writers: every old field of A equals that of B; msgs is kept or replaced whole.
        rows_a = rows(sides['A'] / 'library.sqlite3', sides['A'] / 'archive')
        rows_b = rows(sides['B'] / 'library.sqlite3', sides['B'] / 'archive')
        self.assertEqual(set(rows_a), set(rows_b))
        for key in rows_a:
            self.assertEqual(strip(rows_a[key]), strip(rows_b[key]), f'{version} {key}')
        files_a, files_b = files(sides['A'] / 'archive'), files(sides['B'] / 'archive')
        self.assertEqual(set(files_a), set(files_b))
        for name in files_a:
            self.assertEqual(strip(files_a[name]), strip(files_b[name]), f'{version} {name}')
        after = sidecars(sides['A'] / 'library.sqlite3', sides['A'] / 'archive')
        replaced = {key for key in before if key not in after}
        for key in replaced:
            self.assertEqual(key[0], 'entries', f'{version}: only rows an old writer replaces whole lose msgs: {key}')
            self.assertEqual(key[1], self.spec['subscriptions']['named'])
        self.assertTrue(replaced, 'the named subscription check replaced its entries whole')
        for key, entries in after.items():
            self.assertIn(key, before, f'{version}: an old writer never adds a sidecar: {key}')
            self.assertEqual(entries, before[key], f'{version}: msgs is kept unchanged by {key}')
        # Orphans exactly where an old writer changed the text.
        subscription = ('subscriptions', self.spec['subscriptions']['untitled'])
        last_error = rows_a[subscription][-1]['last_error']
        self.assertEqual(last_error, 'old version error text')
        self.assertNotIn(last_error, after[subscription])
        self.assertIn('未命名订阅', after[subscription], 'the entry of the unchanged name stays valid')
        # 3. 1.8.0 reads every row the old version wrote: orphans ignored, changed texts lifted.
        store = Library(sides['A'] / 'library.sqlite3')
        for row in store.history():
            record = store.job(row['id'])
            for page in record['pages']:
                self.assertNotIn('msgs', page)
        decoded = {row['id']: row for row in store.subscriptions()}
        error = decoded[self.spec['subscriptions']['untitled']]['data']['last_error']
        self.assertFalse(isinstance(error, Message) and error.origin == 'stored', 'an orphan is never applied')
        self.assertEqual(decoded[self.spec['subscriptions']['untitled']]['name'].origin, 'stored')
        for key in self.spec['subscriptions'].values():
            for item in store.pending(key):
                self.assertNotIn('msgs', item)
        for item in store.archives():
            self.assertNotIn('msgs', item)
        untitled = store.job(self.spec['ids']['untitled'])
        if version != '1.5.1':
            self.assertEqual(untitled['title'], 'Other Song 等 2 条', '1.6.0 / 1.7.0 named the job')
            self.assertNotEqual(getattr(untitled['title'], 'origin', None), 'stored')
        else:
            self.assertEqual(untitled['title'].key, 'value.job_title.placeholder')
        modern = store.job(self.spec['modern'])['data']['options']
        self.assertEqual(modern['mode'], formats.MODES[5])
        store.change_job(self.spec['ids']['foreign'], status='partial')
        with opened(store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (self.spec['ids']['foreign'],)).fetchone()[0])
        for text, entry in FOREIGN.items():
            self.assertEqual(json.dumps(data['msgs'][text], ensure_ascii=False), json.dumps(entry, ensure_ascii=False))
        return shown

    def test_1_5_1(self):
        shown = self.check_version('1.5.1')
        note = shown['A']['before']['note ' + self.spec['ids']['multi']]
        self.assertIn('最近错误：此链接无法开始：链接过长', note)

    def test_1_6_0(self):
        shown = self.check_version('1.6.0')
        note = shown['A']['before']['note ' + self.spec['ids']['multi']]
        self.assertIn('最近错误：网站拒绝访问（403）', note, 'the stored reason pairs with its error')

    def test_1_7_0(self):
        shown = self.check_version('1.7.0')
        first = shown['A']['before']
        note = first['note ' + self.spec['ids']['multi']]
        self.assertIn('网站拒绝访问（403）', note)
        self.assertEqual(first['row ' + self.spec['subscriptions']['untitled']][0], '未命名订阅')

    def test_the_source_library_is_1_8_0_shaped(self):
        found = sidecars(self.source / 'library.sqlite3', self.source / 'archive')
        tables = {key[0] for key in found}
        self.assertEqual(tables, {'jobs', 'pages', 'subscriptions', 'entries', 'archives', 'file'})
        with opened(self.source / 'library.sqlite3') as db:
            options = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (self.spec['modern'],)).fetchone()[0])['options']
        self.assertEqual((options['mode'], options['mode_v2']), (formats.LEGACY_MODES[0], formats.MODES[5]))


if __name__ == '__main__':
    unittest.main()
