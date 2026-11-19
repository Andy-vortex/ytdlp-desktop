"""1.8.1 library layer (build/v181/design/DESIGN.md 1, 3, 6.3): Library.contexts without a decode of
every page (A1), ignore_entries (B1), delete_subscription (B2), rename_subscription (B3) and the None
check of finish_subscription.

    python -X utf8 -B test_v181_library.py

ContextsTests: a seeded random comparison with the 1.8.0 body of contexts (reference_contexts, a
verbatim copy), with inputs that take each of its two ways (instr prefilter: at most 4 search texts;
row screening: more, or a value that is not a str) and a trace of the SQL that proves which way ran;
no page is decoded for a link that was never collected; the newest page wins.  Ignore / Delete /
Rename: the rows, the sidecars, the job records (options bytes, updated, title kept: I10, I11), the
schema (I2).  Every new method takes BEGIN IMMEDIATE first and is in UI_RETRIED.  RollbackTests: the
frozen 1.5.1 / 1.6.0 / 1.7.0 code reads and writes a library after the three operations.  No window,
no network; the rollback children run release-sources/<v>/src with -B.
"""
import _sandbox
import contextlib
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import formats
import library
import messages
import urls
from library import Library
from messages import Message, Raw, msg, raw

PAGE = 'https://example.invalid/list'
A, B, C = ('https://example.invalid/' + name for name in 'abc')
FOREIGN = {'找不到任务记录。': {'k': 'core.future.unknown', 'p': {'a': [1, 2]}}}
# The alphabet of the random links (1.4): JSON escapes, non-ASCII, a line separator, controls.
ASCII_CHARS = list('a/?=&"\\%\' ') + ['\n', '\t']
WIDE_CHARS = ['中', '文', 'é', '😀', '\u2028', '\x7f']


@contextlib.contextmanager
def opened(path):
    """A sqlite connection committed and closed at the end of the block."""
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def reference_contexts(store, urls_):
    """Library.contexts as 247f3b5 (1.8.0) has it, verbatim but for store.db()."""
    remaining, result = set(urls_), {}
    with store.db() as db:
        for row in db.execute('SELECT job,url,data FROM pages ORDER BY rowid DESC'):
            if not remaining:
                break
            page = messages.decode(json.loads(row['data']), 'page')
            for item in page['links']:
                if item['url'] in remaining:
                    result[item['url']] = dict(title=item['title'], source=item['source'], job_id=row['job'], page_url=page['url'])
                    remaining.remove(item['url'])
    return result


def shape(result):
    """A result with each title as (type, plain text, key, origin): equal dicts with titles of one type."""
    out = {}
    for url, item in result.items():
        title = item['title']
        out[url] = dict(item, title=(type(title).__name__, str.__str__(title), getattr(title, 'key', None),
                                     getattr(title, 'origin', None)))
    return out


@contextlib.contextmanager
def traced():
    """Every SQL statement of the connections library opens in the block."""
    statements = []
    real_connect = sqlite3.connect

    def connect(*arguments, **keywords):
        connection = real_connect(*arguments, **keywords)
        connection.set_trace_callback(statements.append)
        return connection

    with mock.patch('library.sqlite3.connect', side_effect=connect):
        yield statements


def used_instr(statements):
    return any('instr(data' in text and text.lstrip().upper().startswith('SELECT') for text in statements)


def master(path):
    with opened(path) as db:
        return (sorted(db.execute('SELECT type, name, tbl_name, sql FROM sqlite_master')),
                db.execute('PRAGMA user_version').fetchone()[0])


def options_text(text):
    """The exact substring of a stored job record that holds its options object."""
    start = text.index('"options": ') + len('"options": ')
    _value, end = json.JSONDecoder().raw_decode(text, start)
    return text[start:end]


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Library(self.root / 'library.sqlite3')

    def subscription_with(self, *links, name='订阅'):
        """A subscription whose listed links are all pending."""
        sub = self.store.subscription(name, dict(urls=[PAGE]))
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=link, title=raw('t'), kind='媒体地址') for link in links])
        self.assertEqual(self.store.finish_subscription(sub, [page], True), len(links))
        return sub

    def pending(self, sub):
        return [entry['url'] for entry in self.store.pending(sub)]

    def rows(self, table, where='', parameters=()):
        with opened(self.store.path) as db:
            return sorted(db.execute(f'SELECT * FROM {table} {where}', parameters))

    def job_row(self, job):
        with opened(self.store.path) as db:
            db.row_factory = sqlite3.Row
            return db.execute('SELECT id,kind,title,status,updated,data FROM jobs WHERE id=?', (job,)).fetchone()

    def sub_row(self, key):
        with opened(self.store.path) as db:
            return db.execute('SELECT name,data FROM subscriptions WHERE id=?', (key,)).fetchone()


# ============================================================================ A1: contexts

class ContextsTests(StoreCase):
    def collect_job(self, store):
        return store.create_job('collect', raw('采集'), dict(urls=[PAGE]))

    @staticmethod
    def link_title(rng, index):
        choice = rng.randrange(4)
        if choice == 0:
            return raw(f'Song {index} 歌')
        if choice == 1:
            return msg('value.link_kind.web')
        if choice == 2:
            return '网页链接'                       # a legacy plain title: lifted on decode
        return f'plain {index}'

    def random_round(self, rng, folder):
        store = Library(folder / 'library.sqlite3')
        wide = ['https://e.invalid/' + ''.join(rng.choice(ASCII_CHARS + WIDE_CHARS) for _ in range(rng.randint(1, 6)))
                + rng.choice(WIDE_CHARS) for _ in range(8)]
        narrow = ['https://e.invalid/' + ''.join(rng.choice(ASCII_CHARS) for _ in range(rng.randint(1, 6)))
                  for _ in range(8)]
        pool = list(dict.fromkeys(wide + narrow))
        jobs = [self.collect_job(store) for _ in range(2)]
        for index in range(rng.randint(3, 8)):
            links = [dict(url=url, title=self.link_title(rng, index), kind=rng.choice(['媒体地址', '网页链接']),
                          source=rng.choice(pool)) for url in rng.sample(pool, rng.randint(0, 6))]
            page = dict(url=rng.choice(pool[:4] + [f'{PAGE}?p={index}']), title=raw(f'P{index}'), text='', markdown='',
                        links=links, next=None)
            job = rng.choice(jobs)
            if rng.random() < 0.3:
                # A row another writer stored with escaped non-ASCII (ensure_ascii=True).
                with opened(store.path) as db:
                    db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)',
                               (job, page['url'], json.dumps(messages.encode(page, 'page'), ensure_ascii=True)))
            else:
                store.collection_event(job, 'collect_page', dict(page, _checkpoint=None))
        unseen = ['https://e.invalid/never/' + str(n) for n in range(3)] + ['https://e.invalid/从未/' + str(n) for n in range(3)]
        ascii_pool = [url for url in narrow if url.isascii() and '\x7f' not in url]
        wide_pool = [url for url in wide] + unseen[3:]
        groups = [
            ('instr', rng.sample(ascii_pool + unseen[:3], rng.randint(1, 2))),          # (a) at most 2 texts
            ('instr', rng.sample(wide_pool, 2)),                                        # (b) 4 texts: the threshold
            ('screen', rng.sample(wide_pool, 3)),                                       # (c) 6 texts
            ('screen', rng.sample(pool + unseen, 5)),                                   # (c) 5 links or more
            ('screen', rng.sample(pool, 2) + [42]),                                     # (d) a value that is no str
            (None, []),
        ]
        while len(groups) < 10:
            groups.append((None, rng.sample(pool + unseen + [None], rng.randint(0, 9))))
        for way, wanted in groups:
            expected = shape(reference_contexts(store, wanted))
            with traced() as statements:
                got = shape(store.contexts(wanted))
            self.assertEqual(got, expected, f'{way}: {wanted!r}')
            if way == 'instr':
                self.assertTrue(used_instr(statements), f'the prefilter way ran for {wanted!r}')
            elif way == 'screen':
                self.assertFalse(used_instr(statements), f'the screening way ran for {wanted!r}')
            elif not wanted:
                self.assertEqual(statements, [], 'no link: the database is not opened')

    def test_equal_to_the_1_8_0_body_on_random_libraries(self):
        rng = random.Random(181)
        with _sandbox.fixture_texts('legacy plain link titles (网页链接) stored on purpose: the lift of old rows'):
            for number in range(200):
                folder = self.root / f'r{number}'
                folder.mkdir()
                with self.subTest(round=number):
                    self.random_round(rng, folder)

    def many_pages(self, count=300):
        job = self.collect_job(self.store)
        with opened(self.store.path) as db:
            for index in range(count):
                page = dict(url=f'{PAGE}?p={index}', title=raw(f'P{index}'), text='', markdown='', next=None,
                            links=[dict(url=f'{PAGE}/item/{index}/{n}', title=raw(f'Item {n}'), kind='媒体地址',
                                        source=f'{PAGE}?p={index}') for n in range(5)])
                db.execute('INSERT INTO pages VALUES(?,?,?)', (job, page['url'], library._dump(page, 'page')))
        return job

    def page_decodes(self, wanted):
        real = messages.decode
        with mock.patch('library.messages.decode', wraps=real) as decode, traced() as statements:
            result = self.store.contexts(wanted)
        return result, sum(1 for call in decode.call_args_list if call.args[1:2] == ('page',)), used_instr(statements)

    def test_a_link_never_collected_decodes_no_page(self):
        self.many_pages()
        for wanted, instr in ((['https://example.invalid/never'], True),
                              ([f'https://example.invalid/never/{n}' for n in range(6)], False)):
            result, decodes, used = self.page_decodes(wanted)
            self.assertEqual(result, {})
            self.assertEqual(decodes, 0, wanted)
            self.assertIs(used, instr)
        oldest = f'{PAGE}/item/0/3'
        for wanted, instr in (([oldest], True), ([oldest] + [f'https://example.invalid/never/{n}' for n in range(5)], False)):
            result, decodes, used = self.page_decodes(wanted)
            self.assertEqual(set(result), {oldest})
            self.assertEqual(result[oldest]['page_url'], f'{PAGE}?p=0')
            self.assertEqual(decodes, 1, 'only the page that holds the link is decoded')
            self.assertIs(used, instr)

    def test_the_newest_page_wins_on_both_ways(self):
        first, third = self.collect_job(self.store), self.collect_job(self.store)
        middle = self.collect_job(self.store)
        for job, number in ((first, 1), (middle, 2), (third, 3)):
            links = [dict(url=A, title=raw(f'A{number}'), kind='媒体地址', source=PAGE)] if number != 2 else []
            self.store.collection_event(job, 'collect_page', dict(url=f'{PAGE}?p={number}', title=raw('P'), text='',
                                                                  markdown='', links=links, next=None, _checkpoint=None))
        for wanted in ([A], [A, B, C, 'https://example.invalid/d', 'https://example.invalid/e']):
            result = self.store.contexts(wanted)
            self.assertEqual(result[A]['job_id'], third, wanted)
            self.assertEqual(result[A]['page_url'], f'{PAGE}?p=3')
            self.assertEqual(str.__str__(result[A]['title']), 'A3')
            self.assertEqual(shape(result), shape(reference_contexts(self.store, wanted)))


# ============================================================================ B1: ignore_entries

class IgnoreTests(StoreCase):
    def test_ignore_takes_the_selected_entries_out_and_keeps_the_rows(self):
        sub = self.subscription_with(A, B, C)
        other = self.subscription_with(A)
        self.assertEqual(self.store.ignore_entries(sub, [A, B]), 2)
        self.assertEqual(self.pending(sub), [C])
        with opened(self.store.path) as db:
            rows = dict(db.execute('SELECT key, pending FROM entries WHERE subscription=?', (sub,)))
        self.assertEqual(rows, {urls.identity(A): 0, urls.identity(B): 0, urls.identity(C): 1}, 'the rows stay')
        self.assertEqual(self.pending(other), [A], 'the same link in another subscription is not touched')
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=link, title=raw('t'), kind='媒体地址') for link in (A, B, C)])
        self.assertEqual(self.store.finish_subscription(sub, [page], True), 0, 'a later check lists nothing new')
        self.assertEqual(self.pending(sub), [C], 'the ignored entries stay out')
        self.assertEqual(self.store.ignore_entries(sub, [A, B]), 0, 'only entries that changed are counted')
        self.assertEqual(self.store.ignore_entries(sub, ['https://example.invalid/unknown']), 0)
        self.assertEqual(self.store.ignore_entries(sub, []), 0)
        self.assertEqual(self.store.ignore_entries('no such subscription', [C]), 0)
        self.assertEqual(self.pending(sub), [C])

    def test_bad_links_are_skipped(self):
        sub = self.subscription_with(A, B)
        with self.assertRaises(ValueError):
            urls.identity('http://[::1')
        self.assertEqual(self.store.ignore_entries(sub, ['http://[::1', None, 7, '', A, A]), 1)
        self.assertEqual(self.pending(sub), [B])

    def test_a_long_list_is_split_into_parts(self):
        links = [f'https://example.invalid/many/{n}' for n in range(1203)]
        sub = self.subscription_with(*links)
        self.assertEqual(self.store.ignore_entries(sub, links[:1201]), 1201)
        self.assertEqual(self.pending(sub), links[1201:])


# ============================================================================ B2: delete_subscription

class DeleteTests(StoreCase):
    def scenario(self):
        first = self.subscription_with(A, B, name='First')
        second = self.subscription_with(B, C, name='Second')
        options = dict(urls=[PAGE], note='中文 "q"', ratio=0.1, mode=formats.MODES[5])
        job_a = self.store.create_job('collect', raw('订阅：First'), options, subscription=first)
        self.store.collection_event(job_a, 'collect_error', msg('core.collect.too_large'))
        job_b = self.store.create_job('collect', raw('订阅：Second'), options, subscription=second)
        # A record that holds the id elsewhere (one of its links), not as its subscription.
        job_c = self.store.create_job('collect', raw('采集'), dict(urls=[PAGE + '?' + first]))
        download = self.store.create_job('download', raw('下载'), dict(urls=[A], subscription=first), subscription=first)
        return first, second, job_a, job_b, job_c, download

    def test_delete_removes_the_subscription_and_its_entries_only(self):
        first, second, job_a, job_b, job_c, download = self.scenario()
        before = {job: self.job_row(job) for job in (job_a, job_b, job_c, download)}
        second_rows = (self.sub_row(second), self.rows('entries', 'WHERE subscription=?', (second,)))
        downloads = self.rows('downloads')
        self.assertIs(self.store.delete_subscription(first), True)
        self.assertIsNone(self.sub_row(first))
        self.assertEqual(self.rows('entries', 'WHERE subscription=?', (first,)), [])
        self.assertEqual((self.sub_row(second), self.rows('entries', 'WHERE subscription=?', (second,))), second_rows)
        self.assertEqual([row['id'] for row in self.store.subscriptions()], [second])
        # The collection job of the deleted subscription forgets it; nothing else of the row changes.
        after = self.job_row(job_a)
        stored_before, stored_after = json.loads(before[job_a]['data']), json.loads(after['data'])
        self.assertEqual(stored_before['subscription'], first)
        self.assertIsNone(stored_after['subscription'])
        self.assertEqual(options_text(after['data']), options_text(before[job_a]['data']), 'I10: options keep their bytes')
        self.assertEqual({k: v for k, v in stored_after.items() if k != 'subscription'},
                         {k: v for k, v in stored_before.items() if k != 'subscription'})
        self.assertEqual(stored_after['msgs'], stored_before['msgs'], 'the sidecar is kept')
        self.assertEqual(tuple(after)[:5], tuple(before[job_a])[:5], 'id, kind, title, status and updated are kept')
        self.assertIsNone(self.store.job(job_a)['data']['subscription'])
        for job in (job_b, job_c, download):
            self.assertEqual(tuple(self.job_row(job)), tuple(before[job]), 'other jobs keep every byte')
        self.assertEqual(self.rows('downloads'), downloads)
        self.assertIs(self.store.delete_subscription(first), False, 'a second delete finds nothing')
        self.assertIs(self.store.delete_subscription(''), False)
        self.assertIs(self.store.delete_subscription(None), False)

    def test_finish_subscription_of_a_deleted_subscription_writes_nothing(self):
        first, *_rest = self.scenario()
        self.store.delete_subscription(first)
        rows = (self.rows('subscriptions'), self.rows('entries'))
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=C, title=raw('t'), kind='媒体地址')])
        self.assertEqual(self.store.finish_subscription(first, [page], True), 0)
        self.assertEqual(self.store.finish_subscription(first, [], False, 'x'), 0)
        self.assertEqual((self.rows('subscriptions'), self.rows('entries')), rows)
        self.assertEqual(self.rows('entries', 'WHERE subscription=?', (first,)), [])

    def test_i2_schema_and_user_version_unchanged(self):
        first, second, *_rest = self.scenario()
        self.store.ignore_entries(second, [B])
        self.store.rename_subscription(second, 'Renamed')
        self.store.delete_subscription(first)
        reference = self.root / 'reference.sqlite3'
        with opened(reference) as db:
            db.executescript(library.SCHEMA)
        self.assertEqual(master(self.store.path), master(reference))
        self.assertEqual(master(self.store.path)[1], 0)


# ============================================================================ B3: rename_subscription

class RenameTests(StoreCase):
    def test_a_plain_name_drops_the_untitled_entry(self):
        key = self.store.subscription('', dict(urls=[PAGE]))
        name, data = self.sub_row(key)
        self.assertEqual(json.loads(data)['msgs'], {'未命名订阅': {'k': 'value.subscription.untitled'}})
        self.assertIs(self.store.rename_subscription(key, '  My list  '), True)
        new_name, new_data = self.sub_row(key)
        self.assertEqual(new_name, 'My list')
        self.assertEqual(new_data, json.dumps({k: v for k, v in json.loads(data).items() if k != 'msgs'}, ensure_ascii=False),
                         'the record is the 1.7.0 bytes: no msgs, every other field as before')
        shown = self.store.subscriptions()[0]['name']
        self.assertEqual(str.__str__(shown), 'My list')
        self.assertNotIsInstance(shown, Message)

    def test_a_record_without_a_sidecar_change_is_not_rewritten(self):
        key = self.store.subscription('Old', dict(urls=[PAGE]))
        _name, data = self.sub_row(key)
        self.assertTrue(self.store.rename_subscription(key, 'Other'))
        self.assertEqual(self.sub_row(key), ('Other', data))

    def test_typed_untitled_text_is_raw_and_empty_is_the_message(self):
        key = self.store.subscription('Old', dict(urls=[PAGE]))
        self.store.rename_subscription(key, '未命名订阅')
        name, data = self.sub_row(key)
        self.assertEqual(name, '未命名订阅')
        self.assertEqual(json.loads(data)['msgs'], {'未命名订阅': None})
        self.assertIsInstance(self.store.subscriptions()[0]['name'], Raw)
        self.store.rename_subscription(key, '   ')
        shown = self.store.subscriptions()[0]['name']
        self.assertIsInstance(shown, Message)
        self.assertEqual(shown.key, 'value.subscription.untitled')
        self.assertEqual(json.loads(self.sub_row(key)[1])['msgs'], {'未命名订阅': {'k': 'value.subscription.untitled'}})
        self.store.rename_subscription(key, raw('x' * 150))
        self.assertEqual(self.sub_row(key)[0], 'x' * 100, 'cut to 100 characters, as a new subscription')

    def test_unknown_id_and_foreign_entries(self):
        self.assertIs(self.store.rename_subscription('no such id', 'x'), False)
        key = self.store.subscription('', dict(urls=[PAGE]))
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM subscriptions').fetchone()[0])
            data['last_error'] = '找不到任务记录。'
            data['msgs'] = dict(data['msgs'], **FOREIGN)
            db.execute('UPDATE subscriptions SET data=?', (json.dumps(data, ensure_ascii=False),))
        self.assertTrue(self.store.rename_subscription(key, 'Named'))
        stored = json.loads(self.sub_row(key)[1])
        self.assertEqual(stored['msgs'], FOREIGN, 'I11: the foreign entry survives, the untitled entry goes')
        self.assertEqual(stored['last_error'], '找不到任务记录。')
        self.assertEqual(self.store.subscriptions()[0]['data']['last_error'].origin, 'foreign')


# ============================================================================ transactions, registry

class TransactionTests(StoreCase):
    def test_the_new_methods_take_the_write_lock_first(self):
        sub = self.subscription_with(A, B)
        gone = self.subscription_with(C)
        self.store.delete_subscription(gone)
        calls = {
            'ignore_entries': lambda: self.store.ignore_entries(sub, [A]),
            'rename_subscription': lambda: self.store.rename_subscription(sub, 'New'),
            'finish_subscription (deleted)': lambda: self.store.finish_subscription(gone, [], False, 'x'),
            'delete_subscription': lambda: self.store.delete_subscription(sub),
        }
        with traced() as statements:
            for name, call in calls.items():
                statements.clear()
                call()
                self.assertEqual(statements[0], 'BEGIN IMMEDIATE', name)
                self.assertEqual(statements[-1], 'COMMIT', name)
                self.assertEqual(statements.count('BEGIN IMMEDIATE'), 1, f'{name}: one transaction, one connection')


class RegistryTests(unittest.TestCase):
    def test_the_new_methods_are_retried_on_the_window_thread(self):
        for name in ('ignore_entries', 'delete_subscription', 'rename_subscription', 'contexts', 'finish_subscription'):
            self.assertIn(name, library.UI_RETRIED)
            self.assertTrue(getattr(getattr(Library, name), 'ui_retrying', False), name)


# ============================================================================ rollback

CHILD = r'''
import json, sys
src, version, path, out_path = sys.argv[1:5]
sys.path.insert(0, src)
import library
store = library.Library(path)
out = {'names': {}, 'pending': {}, 'jobs': {}}
for row in store.subscriptions():
    out['names'][row['id']] = str(row['name'])
    out['pending'][row['id']] = sorted(entry['url'] for entry in store.pending(row['id']))
for row in store.history():
    out['jobs'][row['id']] = store.job(row['id'])['data'].get('subscription')
for key in out['names']:
    store.finish_subscription(key, [], False, 'x')
with open(out_path, 'w', encoding='utf-8') as handle:
    json.dump(out, handle, ensure_ascii=False)
'''


class RollbackTests(StoreCase):
    def test_old_versions_read_and_write_after_the_three_operations(self):
        kept = self.subscription_with(A, B, C, name='')
        deleted = self.subscription_with(A, name='Gone')
        job_kept = self.store.create_job('collect', raw('订阅：kept'), dict(urls=[PAGE]), subscription=kept)
        job_deleted = self.store.create_job('collect', raw('订阅：Gone'), dict(urls=[PAGE]), subscription=deleted)
        self.store.collection_event(job_deleted, 'collect_error', msg('core.collect.too_large'))
        self.assertEqual(self.store.ignore_entries(kept, [B]), 1)
        self.assertTrue(self.store.rename_subscription(kept, 'Renamed list'))
        self.assertTrue(self.store.delete_subscription(deleted))
        with opened(self.store.path) as db:
            hits = db.execute('SELECT count(*) FROM jobs WHERE instr(data, ?) > 0', (deleted,)).fetchone()[0]
        self.assertEqual(hits, 0, 'no job record holds the deleted id')
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', YTDLP_DESKTOP_LANG='zh')
        environment.pop(_sandbox.MSG_GUARD, None)           # the old code has no messages module
        script = self.root / 'child.py'
        script.write_text(CHILD, encoding='utf-8')
        for version in _sandbox.RELEASES:
            with self.subTest(version=version):
                folder = self.root / version
                folder.mkdir()
                copy = folder / 'library.sqlite3'
                copy.write_bytes(self.store.path.read_bytes())
                out_path = folder / 'out.json'
                done = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(_sandbox.release_src(version)),
                                       version, str(copy), str(out_path)], cwd=str(folder), env=environment,
                                      capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
                self.assertEqual(done.returncode, 0, f'{version} raised:\n{done.stderr[-4000:]}')
                out = json.loads(out_path.read_text(encoding='utf-8'))
                self.assertEqual(out['names'], {kept: 'Renamed list'})
                self.assertEqual(out['pending'], {kept: sorted([A, C])})
                self.assertEqual(out['jobs'][job_kept], kept)
                self.assertIsNone(out['jobs'][job_deleted])


if __name__ == '__main__':
    unittest.main()
