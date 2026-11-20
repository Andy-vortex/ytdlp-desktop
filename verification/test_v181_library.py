"""1.8.1 library layer (build/v181/design/DESIGN.md 1, 3, 6.3): Library.contexts without a decode of
every page (A1), ignore_entries (B1), delete_subscription (B2), rename_subscription (B3) and the None
check of finish_subscription.

    python -X utf8 -B test_v181_library.py

ContextsTests: a seeded random comparison with the 1.8.0 body of contexts (reference_contexts, a
verbatim copy), with inputs that take each of its two ways (instr prefilter: at most 4 search texts;
row screening: more, or a value that is not a str) and a trace of the SQL that proves which way ran;
no page is decoded for a link that was never collected; the newest page wins.  Ignore / Delete /
Rename: the rows, the job records (options bytes, updated, title kept: I10), the schema (I2).  Every
new method takes BEGIN IMMEDIATE first and is in UI_RETRIED.  No window, no network.

(2.0.0 S2 P2) The library is format 2: the reference body of contexts reads a page with the format-2
slot reader where 1.8.0 decoded it; the checks of Delete / Rename are checks of the format-2 record
(rename_subscription writes the name column only); user_version is 2.  RollbackTests (1.5.1 / 1.6.0 /
1.7.0 reading the library after the three operations) is deleted: 2.0.0 drops the old versions (PLAN 1).
"""
import _sandbox
import contextlib
import json
from pathlib import Path
import random
import sqlite3
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
FUTURE = {'k': 'core.future.unknown', 'p': {'a': 1}}
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
    """Library.contexts as 247f3b5 (1.8.0) has it, verbatim but for store.db() and (2.0.0) the format-2
    slot reader in place of messages.decode."""
    remaining, result = set(urls_), {}
    with store.db() as db:
        for row in db.execute('SELECT job,url,data FROM pages ORDER BY rowid DESC'):
            if not remaining:
                break
            page = messages.read_slots(json.loads(row['data']), library.SLOTS['page'])
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
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=link, title=raw('t'), kind='media') for link in links])
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
            return '网页链接'                       # a source's own text that reads like an app text
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
            links = [dict(url=url, title=self.link_title(rng, index), kind=rng.choice(['media', 'web']),
                          source=rng.choice(pool)) for url in rng.sample(pool, rng.randint(0, 6))]
            page = dict(url=rng.choice(pool[:4] + [f'{PAGE}?p={index}']), title=raw(f'P{index}'), text='', markdown='',
                        links=links, next=None)
            job = rng.choice(jobs)
            if rng.random() < 0.3:
                # A row another writer stored with escaped non-ASCII (ensure_ascii=True).
                with opened(store.path) as db:
                    db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)',
                               (job, page['url'], json.dumps(messages.write_slots(page, library.SLOTS['page']),
                                                             ensure_ascii=True)))
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
                            links=[dict(url=f'{PAGE}/item/{index}/{n}', title=raw(f'Item {n}'), kind='media',
                                        source=f'{PAGE}?p={index}') for n in range(5)])
                db.execute('INSERT INTO pages VALUES(?,?,?)', (job, page['url'], library._dump(page, 'page')))
        return job

    def page_decodes(self, wanted):
        real = messages.read_slots
        with mock.patch('library.messages.read_slots', wraps=real) as decode, traced() as statements:
            result = self.store.contexts(wanted)
        return result, sum(1 for call in decode.call_args_list if call.args[1:2] == (library.SLOTS['page'],)), \
            used_instr(statements)

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
            links = [dict(url=A, title=raw(f'A{number}'), kind='media', source=PAGE)] if number != 2 else []
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
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=link, title=raw('t'), kind='media') for link in (A, B, C)])
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
        page = dict(url=PAGE, title=raw('页'), links=[dict(url=C, title=raw('t'), kind='media')])
        self.assertEqual(self.store.finish_subscription(first, [page], True), 0)
        self.assertEqual(self.store.finish_subscription(first, [], False, 'x'), 0)
        self.assertEqual((self.rows('subscriptions'), self.rows('entries')), rows)
        self.assertEqual(self.rows('entries', 'WHERE subscription=?', (first,)), [])

    def test_i2_schema_unchanged_and_user_version_2(self):
        first, second, *_rest = self.scenario()
        self.store.ignore_entries(second, [B])
        self.store.rename_subscription(second, 'Renamed')
        self.store.delete_subscription(first)
        reference = self.root / 'reference.sqlite3'
        with opened(reference) as db:
            db.executescript(library.SCHEMA)
        self.assertEqual(master(self.store.path)[0], master(reference)[0])
        self.assertEqual(master(self.store.path)[1], 2, '(2.0.0) format 2')


# ============================================================================ B3: rename_subscription

class RenameTests(StoreCase):
    """(2.0.0) The name column holds the owner's text ('' for none: shown as the untitled message);
    renaming writes that column only, the record keeps every byte."""

    def test_a_plain_name_replaces_the_untitled_one(self):
        key = self.store.subscription('', dict(urls=[PAGE]))
        name, data = self.sub_row(key)
        self.assertEqual(name, '')
        self.assertIs(self.store.rename_subscription(key, '  My list  '), True)
        self.assertEqual(self.sub_row(key), ('My list', data), 'the record keeps its bytes')
        shown = self.store.subscriptions()[0]['name']
        self.assertEqual(str.__str__(shown), 'My list')
        self.assertNotIsInstance(shown, Message)

    def test_typed_untitled_text_is_raw_and_empty_is_the_message(self):
        key = self.store.subscription('Old', dict(urls=[PAGE]))
        _name, data = self.sub_row(key)
        self.store.rename_subscription(key, '未命名订阅')
        self.assertEqual(self.sub_row(key), ('未命名订阅', data))
        self.assertIsInstance(self.store.subscriptions()[0]['name'], Raw)
        self.store.rename_subscription(key, '   ')
        self.assertEqual(self.sub_row(key), ('', data))
        shown = self.store.subscriptions()[0]['name']
        self.assertIsInstance(shown, Message)
        self.assertEqual(shown.key, 'value.subscription.untitled')
        self.store.rename_subscription(key, raw('x' * 150))
        self.assertEqual(self.sub_row(key)[0], 'x' * 100, 'cut to 100 characters, as a new subscription')

    def test_unknown_id_and_a_later_key(self):
        self.assertIs(self.store.rename_subscription('no such id', 'x'), False)
        key = self.store.subscription('', dict(urls=[PAGE]))
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM subscriptions').fetchone()[0])
            data['last_error'] = FUTURE
            db.execute('UPDATE subscriptions SET data=?', (json.dumps(data, ensure_ascii=False),))
        self.assertTrue(self.store.rename_subscription(key, 'Named'))
        self.assertEqual(json.loads(self.sub_row(key)[1])['last_error'], FUTURE, 'a key of a later version survives')
        self.assertEqual(self.store.subscriptions()[0]['data']['last_error'].key, 'core.future.unknown')


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


if __name__ == '__main__':
    unittest.main()
