# quick
"""Store, collector IPC and updater messages (MESSAGES.md 2.8, 3.3, 7.5; WP-3; 2.0.0 S2: format 2).

    python -X utf8 -B test_messages_store.py

(2.0.0 S2 P2, S2-DESIGN.md 1.1-1.2, 4.1) The library is format 2: every Library writer / reader pair
for every record kind, each text slot holding a message ({"k", "p"}) or the text itself, and nothing
else; the store census (a Message fed to every writer lands as {"k"}, a Raw as a string, nothing else
of a record is read as a message); the job title column holds text only (title_links / via say how the
history names a job); I10 in format 2 (a job whose options carry an archive-context title message
through every read-modify-write: the options keep their bytes, extend_job changes only the links);
unknown keys and params of a later version kept through every write; _storable with lone surrogates;
a 2000-link page; contexts -> Archive -> 资料信息.json -> the owner's files; the collector IPC round
trip ({"$m": {"k", "p"}}, a malformed line skipped); str(UpdateError) == its zh catalog text; I2 (the schema
of 1.7.0, frozen here, user_version 2); I7 (no proxy credential in any stored or IPC byte); the census
of the unfinished list; the store channels of G4 (every core.collect / core.library / core.archive /
core.update / value key stored and read back: en without Chinese, zh the message's text).  No window,
no network.

(2.0.0 S2 P4) The I8 tests that let the frozen 1.7.0 code write a library and a
failed list and read them back are gone: 2.0.0 does not read the stores of older versions (S2 Q1).  The
1.5.1 events of the updater are gone too, and so is what this file pinned of the 1.8.0 store codec.
"""
import _sandbox
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import collector
import formats
import i18n
import library
import messages
import paths
import updater
from library import Archive, Library
from messages import Message, Raw, msg, raw

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
FORMATTER = string.Formatter()
A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE = 'https://example.invalid/list'
MEDIA = 'https://cdn.example.invalid/clip.mp4'
# (2.0.0 S2 P4) library.py's SCHEMA of 1.7.0 (git archive 07c9603), frozen: the tables
# and columns never change (PLAN 5)
SCHEMA_OF_1_7_0 = """
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, title TEXT,
        status TEXT, updated REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS pages(job TEXT, url TEXT, data TEXT, PRIMARY KEY(job,url));
    CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, name TEXT,
        enabled INTEGER, interval INTEGER, next_check REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS entries(subscription TEXT, key TEXT, pending INTEGER,
        data TEXT, PRIMARY KEY(subscription,key));
    CREATE TABLE IF NOT EXISTS archives(folder TEXT PRIMARY KEY, updated REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS downloads(key TEXT, format TEXT, url TEXT, title TEXT, path TEXT,
        finished REAL, job TEXT, PRIMARY KEY(key, format));
"""
NUMERIC = {'count', 'code', 'page', 'status', 'length', 'seconds', 'attempt', 'limit', 'line', 'pages', 'links'}
FALLBACK = {'k': 'value.page_title.fallback'}
DIRECT = {'k': 'value.link_kind.direct'}


# (2.0.0 S3 W4) the zh / en text of a catalog key, whatever the current language (S3-DESIGN 4.3)
from _sandbox import en_t, zh_t


@contextlib.contextmanager
def opened(path):
    """A sqlite connection that is committed and closed at the end of the block (Windows keeps an
    open file in use; sqlite3's own context manager only commits)."""
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def en(value_):
    with _sandbox.language('en'):
        return messages.show(value_)


def zh(value_):
    with _sandbox.language('zh'):
        return messages.show(value_)


def plain(text):
    return str.__str__(text)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Library(self.root / 'library.sqlite3')

    def raw_row(self, table, key_column, key, columns='*'):
        with opened(self.store.path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute(f'SELECT {columns} FROM {table} WHERE {key_column}=?', (key,)).fetchone())

    def raw_job(self, job):
        row = self.raw_row('jobs', 'id', job)
        return row['title'], json.loads(row['data']), row['data']

    def raw_subscription(self, key):
        row = self.raw_row('subscriptions', 'id', key)
        return row['name'], json.loads(row['data'])

    def raw_pages(self, job):
        with opened(self.store.path) as db:
            return [json.loads(text) for (text,) in db.execute('SELECT data FROM pages WHERE job=? ORDER BY rowid', (job,))]

    def raw_entries(self, key):
        with opened(self.store.path) as db:
            return [json.loads(text) for (text,) in db.execute('SELECT data FROM entries WHERE subscription=? ORDER BY rowid',
                                                                (key,))]

    def download_job(self, links=(A, B), title='', **options):
        return self.store.create_job('download', title, dict(urls=list(links), output=str(self.root), **options))

    def collect_job(self, title=None, via=None):
        return self.store.create_job('collect', title if title is not None else raw(PAGE),
                                     dict(urls=[PAGE], dynamic=False, scrolls=0, selector='', max_pages=1), via=via)

    def assertMessage(self, item, key):
        self.assertIsInstance(item, Message, item)
        self.assertEqual(item.key, key, item)

    def assertEnglish(self, item, expected=None):
        shown = en(item)
        self.assertEqual(CJK.findall(shown), [], shown)
        if expected is not None:
            self.assertEqual(shown, expected)
        self.assertEqual(zh(item), plain(item))


# ============================================================================ jobs (record kind job)

class JobTests(StoreCase):
    def test_create_job_writes_the_title_text_and_reads_it_back(self):
        job = self.download_job()
        title, data, _text = self.raw_job(job)
        self.assertEqual(title, '', 'no title yet: the history names it after its links')
        self.assertEqual((data['done'], data['errors'], data['files']), ([], [], []))
        for key in ('msgs', 'completed', 'completed_variants', 'titled', 'last_reason', 'via', 'title_links'):
            self.assertNotIn(key, data)
        for record in (self.store.job(job), self.store.history()[0]):
            self.assertEqual(record['title'], '')
            self.assertEqual(library.title_parts(record), ('multi_link', A, 2))
        # A collect job named by its first link: the link itself, read back as raw text.
        collect = self.collect_job()
        self.assertEqual(self.raw_job(collect)[0], PAGE)
        self.assertIs(type(self.store.job(collect)['title']), Raw)
        # A subscription check: the subscription's own name ('' for the untitled one) and via.
        named = self.collect_job('', via='subscription')
        title, data, _text = self.raw_job(named)
        self.assertEqual((title, data['via']), ('', 'subscription'))
        self.assertEqual(library.title_parts(self.store.job(named)), ('subscription', ''))
        # The title column holds text only: a message given as a title is stored as its zh text.
        made = self.collect_job(msg('value.page_title.fallback'))
        self.assertEqual(self.raw_job(made)[0], zh_t('value.page_title.fallback'))
        self.assertIs(type(self.store.job(made)['title']), Raw)
        # A raw title that reads like an app text stays the text it is, in every language.
        odd = self.collect_job(raw('订阅：x'))
        self.assertIs(type(self.store.job(odd)['title']), Raw)
        self.assertEqual(en(self.store.job(odd)['title']), '订阅：x')

    def test_the_title_event_names_the_job_in_one_transaction(self):
        multi, single, quirky = self.download_job(), self.download_job((C,)), self.download_job()
        self.store.download_event(multi, 'title', 'Some Song')
        self.store.download_event(single, 'title', 'Some Song')
        self.store.download_event(quirky, 'title', '网页')
        title, data, _text = self.raw_job(multi)
        self.assertEqual((title, data['title_links']), ('Some Song', 2))
        self.assertEqual(library.title_parts(self.store.job(multi)), ('multi', 'Some Song', 2))
        title, data, _text = self.raw_job(single)
        self.assertEqual(title, 'Some Song')
        self.assertNotIn('title_links', data)
        self.assertEqual(self.store.job(single)['title'], 'Some Song')
        record = self.store.job(quirky)
        self.assertIs(type(record['title']), Raw)
        self.assertEqual(en(record['title']), '网页', 'a media title is never translated')
        # Later titles do not rename it; an empty title never does.
        self.store.download_event(multi, 'title', 'Other')
        self.store.download_event(single, 'title', '   ')
        self.assertEqual(self.raw_job(multi)[0], 'Some Song')

    def test_item_done_errors_and_reasons(self):
        job = self.download_job()
        error = msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=2100, url='youtu.be/x'))
        reason = msg('core.reason.http_403')
        self.store.download_event(job, 'item', {'url': A})
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': error, 'reason': reason})
        self.store.download_event(job, 'item_done', {'success': False, 'url': B, 'error': raw('ERROR: [generic] b: boom'),
                                                     'reason': '  '})
        self.store.download_event(job, 'item_done', {'success': False, 'url': B, 'error': raw('找不到任务记录。'),
                                                     'reason': raw(' Qq ')})
        _title, data, _text = self.raw_job(job)
        self.assertEqual(data['errors'], [
            {'error': {'k': 'core.engine.cannot_start_link',
                       'p': {'error': {'k': 'core.links.too_long', 'p': {'length': 2100, 'url': 'youtu.be/x'}}}},
             'reason': {'k': 'core.reason.http_403'}},
            {'error': 'ERROR: [generic] b: boom', 'reason': None},
            {'error': '找不到任务记录。', 'reason': 'Qq'}])
        record = self.store.job(job)['data']
        self.assertMessage(record['errors'][0]['error'], 'core.engine.cannot_start_link')
        self.assertEqual(plain(record['errors'][0]['error']), plain(error))
        self.assertMessage(record['errors'][0]['reason'], 'core.reason.http_403')
        self.assertEnglish(record['errors'][0]['reason'], en_t('core.reason.http_403'))
        self.assertIs(type(record['errors'][1]['error']), Raw)
        self.assertIsNone(record['errors'][1]['reason'])
        self.assertIs(type(record['errors'][2]['error']), Raw, 'the source said it: raw, never an app text')
        self.assertEqual(en(record['errors'][2]['error']), '找不到任务记录。')
        # errors[-20:]
        for number in range(25):
            self.store.download_event(job, 'item_done', {'success': False, 'url': A,
                                                         'error': msg('core.engine.error', error=f'e{number}')})
        errors = self.raw_job(job)[1]['errors']
        self.assertEqual(len(errors), 20)
        self.assertEqual(errors[-1], {'error': {'k': 'core.engine.error', 'p': {'error': 'e24'}}, 'reason': None})

    def test_a_cut_reason_is_stored_as_its_message(self):
        """(2.0.0, S2-DESIGN.md D7) A reason the task row cut to 70 characters (D-F28) keeps its key and
        params; stored as {"k", "p"}, it reads back whole (en always showed it whole; the note cuts its
        own text to 130 characters)."""
        error = msg('core.engine.cannot_start_link',
                    error=msg('core.cookies.missing', file='C:/x' + 'a' * 18 + ' b/cookies.txt'))
        reason = messages.cut(error, 70)
        self.assertNotEqual(plain(reason), plain(error))
        job = self.download_job()
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': error, 'reason': reason})
        stored = self.raw_job(job)[1]['errors'][0]
        self.assertEqual(stored['reason'], messages.to_json(error))
        last = self.store.job(job)['data']['errors'][0]['reason']
        self.assertMessage(last, 'core.engine.cannot_start_link')
        self.assertEqual((zh(last), en(last)), (plain(error), en(error)))
        self.assertEnglish(last)

    def test_library_errors_are_messages(self):
        with self.assertRaises(ValueError) as caught:
            self.store.job('no-such-job')
        self.assertMessage(caught.exception.args[0], 'core.library.no_job')
        self.assertEqual(str(caught.exception), zh_t('core.library.no_job'))
        for call in (lambda: self.store.subscription('x', {}, interval=5),
                     lambda: self.store.configure_subscription('x', True, 5)):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertMessage(caught.exception.args[0], 'core.library.bad_interval')
        folder = self.root / 'not-a-database'
        folder.mkdir()
        store, warning = library.open_library(folder)
        self.assertMessage(warning, 'core.library.temporary')
        self.assertTrue(plain(warning).startswith(zh_t('core.library.temporary', error='')), warning)
        self.assertIs(type(warning.params['error']), str, 'the sqlite / OS text is a raw param')
        self.assertNotEqual(store.path.parent, folder)

    def test_collection_events_pages_and_errors(self):
        job = self.collect_job()
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': '', 'markdown': '',
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE},
                          {'url': A, 'title': raw('Song A'), 'kind': 'video_page', 'source': PAGE},
                          {'url': B, 'title': raw('网页'), 'kind': 'web', 'source': PAGE}],
                'next': None, '_checkpoint': {'visited': [PAGE], 'todo': []}}
        self.store.collection_event(job, 'collect_page', page)
        self.store.collection_event(job, 'collect_error', msg('core.collect.http_status', status=503))
        self.store.collection_event(job, 'collect_error', 'curl: (7) Failed to connect')
        self.store.collection_event(job, 'collect_checkpoint', {'visited': [PAGE], 'todo': [{'url': C, 'depth': 0}]})
        stored = self.raw_pages(job)[0]
        self.assertEqual(stored['title'], FALLBACK)
        self.assertEqual([link['title'] for link in stored['links']], [DIRECT, 'Song A', '网页'])
        self.assertEqual([link['kind'] for link in stored['links']], ['media', 'video_page', 'web'])
        self.assertNotIn('_checkpoint', stored)
        self.assertNotIn('msgs', stored)
        _title, data, _text = self.raw_job(job)
        self.assertEqual(data['checkpoint'], {'visited': [PAGE], 'todo': [{'url': C, 'depth': 0}]})
        self.assertEqual(data['errors'], [{'error': {'k': 'core.collect.http_status', 'p': {'status': 503}}, 'reason': None},
                                          {'error': 'curl: (7) Failed to connect', 'reason': None}])
        record = self.store.job(job)
        self.assertMessage(record['pages'][0]['title'], 'value.page_title.fallback')
        self.assertMessage(record['pages'][0]['links'][0]['title'], 'value.link_kind.direct')
        self.assertIs(type(record['pages'][0]['links'][1]['title']), Raw)
        self.assertEqual(en(record['pages'][0]['links'][2]['title']), '网页', 'a link literally titled 网页 is raw')
        self.assertEnglish(record['data']['errors'][0]['error'], en_t('core.collect.http_status', status=503))
        # INSERT OR REPLACE: a page collected again replaces the row.
        self.store.collection_event(job, 'collect_page', dict(page, title=raw('Real title'), links=[], _checkpoint=None))
        self.assertEqual(self.raw_pages(job)[0]['title'], 'Real title')

    def test_the_interrupted_marking_reads_the_row_again_for_writing(self):
        job = self.download_job()
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        self.store.change_job(job, owner=None, worker=None)
        _title, before, text = self.raw_job(job)
        rows = self.store.history()
        self.assertEqual(rows[0]['status'], 'interrupted')
        self.assertMessage(rows[0]['data']['errors'][0]['error'], 'core.collect.too_large')
        _title, after, after_text = self.raw_job(job)
        self.assertEqual((after, after_text), (before, text))
        self.assertEqual(self.raw_row('jobs', 'id', job)['status'], 'interrupted')


# ============================================================================ the required I10 test (3.4), format 2

class OptionsBytesTests(StoreCase):
    """(S2-DESIGN.md 4.1, review 16) A job whose options carry an archive-context title message, through
    every read-modify-write: the options' JSON bytes stay; extend_job changes only the links."""

    def test_every_read_modify_write_keeps_the_stored_options(self):
        for mode in (formats.MODES[5], formats.MODES[6]):
            with self.subTest(mode=mode):
                job = self.download_job((A, B), mode=mode, resolution='best',
                                        archive_root=str(self.root / 'archive'),
                                        archive_contexts={A: {'title': msg('value.page_title.fallback'), 'source': PAGE,
                                                              'job_id': 'j', 'page_url': PAGE},
                                                          B: {'title': raw('Song B'), 'source': PAGE, 'job_id': 'j',
                                                              'page_url': PAGE}})
                self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large'),
                                                             'reason': msg('core.reason.network')})

                def options_bytes():
                    return json.dumps(self.raw_job(job)[1]['options'], ensure_ascii=False)

                before = options_bytes()
                options = json.loads(before)
                self.assertEqual(options['mode'], mode)
                self.assertNotIn('mode_v2', options)
                self.assertEqual(options['archive_contexts'][A]['title'], FALLBACK)
                self.assertEqual(options['archive_contexts'][B]['title'], 'Song B')
                shown = self.store.job(job)['data']['options']
                self.assertMessage(shown['archive_contexts'][A]['title'], 'value.page_title.fallback')
                self.assertIs(type(shown['archive_contexts'][B]['title']), Raw)
                self.assertEqual(options_bytes(), before)
                steps = [
                    ('history interrupted marking', lambda: (self.store.change_job(job, status='running', owner=None,
                                                                                   worker=None), self.store.history())),
                    ('change_job(status=...)', lambda: self.store.change_job(job, status='partial')),
                    ('change_job(worker=...)', lambda: self.store.change_job(job, worker=os.getpid())),
                    ('change_job(options=read options)', lambda: self.store.change_job(
                        job, options=self.store.job(job)['data']['options'])),
                    ('item_done', lambda: self.store.download_event(job, 'item_done', {'success': True, 'url': A})),
                    ('done', lambda: self.store.download_event(job, 'done', {'cancelled': False, 'failed': 1})),
                    ('title', lambda: self.store.download_event(job, 'title', 'Some Song')),
                ]
                for name, step in steps:
                    step()
                    self.assertEqual(options_bytes(), before, name)
                    errors = self.raw_job(job)[1]['errors']
                    self.assertEqual(errors[0], {'error': {'k': 'core.collect.too_large'},
                                                 'reason': {'k': 'core.reason.network'}}, name)
                self.store.extend_job(job, [C])
                after = self.raw_job(job)[1]['options']
                self.assertEqual(after['urls'], [A, B, C])
                self.assertEqual({key: item for key, item in after.items() if key != 'urls'},
                                 {key: item for key, item in json.loads(before).items() if key != 'urls'})
                self.assertEqual(self.store.job(job)['data']['options']['mode'], mode)


# ============================================================================ subscriptions and entries

class SubscriptionTests(StoreCase):
    def options(self):
        return dict(urls=[PAGE], dynamic=False, scrolls=0, selector='', max_pages=1)

    def test_names(self):
        untitled, typed, named = (self.store.subscription(name, self.options()) for name in ('  ', '未命名订阅', ' My list '))
        self.assertEqual(self.raw_subscription(untitled)[0], '', 'no name: the untitled one is shown')
        self.assertEqual(self.raw_subscription(typed)[0], '未命名订阅', 'the owner typed it')
        name, data = self.raw_subscription(named)
        self.assertEqual(name, 'My list')
        self.assertEqual(json.dumps(data, ensure_ascii=False), json.dumps(
            dict(options=dict(self.options(), proxy=''), media_only=True, first_new=True, initialized=False,
                 last_check=None, last_error='', last_new=0), ensure_ascii=False), 'the 1.7.0 bytes')
        rows = {row['id']: row for row in self.store.subscriptions()}
        self.assertMessage(rows[untitled]['name'], 'value.subscription.untitled')
        self.assertEqual(rows[untitled]['name_text'], '')
        self.assertIs(type(rows[typed]['name']), Raw)
        self.assertEqual((en(rows[untitled]['name']), en(rows[typed]['name'])), (en_t('value.subscription.untitled'), '未命名订阅'))
        self.assertEqual((rows[named]['name'], rows[named]['name_text']), ('My list', 'My list'))
        self.assertNotIn('msgs', rows[untitled]['data'])
        # Renaming writes the name column only; an empty name is the untitled one again.
        _name, before = self.raw_subscription(named)
        self.assertTrue(self.store.rename_subscription(named, '  ' + 'x' * 150))
        self.assertEqual(self.raw_subscription(named), ('x' * 100, before))
        self.assertTrue(self.store.rename_subscription(named, msg('value.page_title.fallback')))
        self.assertEqual(self.raw_subscription(named)[0], zh_t('value.page_title.fallback'))
        self.assertTrue(self.store.rename_subscription(named, ' '))
        self.assertMessage(self.store.subscriptions()[2]['name'], 'value.subscription.untitled')
        self.assertFalse(self.store.rename_subscription('no-such', 'x'))

    def test_last_error_clauses_and_the_entries_of_a_check(self):
        key = self.store.subscription('', self.options())
        joined = messages.clauses([msg('core.collect.needs_chrome'), msg('core.collect.http_status', status=503)])
        self.store.finish_subscription(key, [], False, joined)
        name, data = self.raw_subscription(key)
        self.assertEqual(data['last_error'], {'k': 'common.clauses', 'p': {'parts': [
            {'k': 'core.collect.needs_chrome'}, {'k': 'core.collect.http_status', 'p': {'status': 503}}]}})
        self.assertEqual(name, '')
        row = self.store.subscriptions()[0]
        self.assertMessage(row['data']['last_error'], 'common.clauses')
        self.assertEqual(plain(row['data']['last_error']),
                         zh_t('core.collect.needs_chrome') + i18n.JOIN
                         + zh_t('core.collect.http_status', status=503))
        self.assertEnglish(row['data']['last_error'])
        self.store.finish_subscription(key, [], False)
        data = self.raw_subscription(key)[1]
        self.assertEqual(data['last_error'], {'k': 'value.subscription.interrupted'})
        self.store.finish_subscription(key, [], False, raw('curl: (6) x'))
        self.assertEqual(self.raw_subscription(key)[1]['last_error'], 'curl: (6) x')
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'),
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE},
                          {'url': A, 'title': raw('Song A'), 'kind': 'video_page', 'source': PAGE},
                          {'url': B, 'title': raw('Web'), 'kind': 'web', 'source': PAGE}]}
        self.assertEqual(self.store.finish_subscription(key, [page], True), 2)
        data = self.raw_subscription(key)[1]
        self.assertEqual(data['last_error'], '')
        entries = self.raw_entries(key)
        self.assertEqual([(entry['title'], entry['page_title']) for entry in entries], [(DIRECT, FALLBACK),
                                                                                        ('Song A', FALLBACK)])
        self.assertNotIn('msgs', entries[0])
        pending = self.store.pending(key)
        self.assertMessage(pending[0]['title'], 'value.link_kind.direct')
        self.assertMessage(pending[1]['page_title'], 'value.page_title.fallback')
        self.assertIs(type(pending[1]['title']), Raw)
        self.assertEnglish(pending[0]['title'], en_t('value.link_kind.direct'))


# ============================================================================ archive (contexts -> Archive -> files)

class ArchiveTests(StoreCase):
    def collected(self):
        job = self.collect_job()
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': 'Body', 'markdown': 'Body',
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE}],
                'next': None, '_checkpoint': {'visited': [PAGE], 'todo': []}}
        self.store.collection_event(job, 'collect_page', page)
        return job

    def test_contexts_archive_record_and_files(self):
        job = self.collected()
        context = self.store.contexts([MEDIA])[MEDIA]
        self.assertMessage(context['title'], 'value.link_kind.direct')
        self.assertEqual((context['job_id'], context['source'], context['page_url']), (job, PAGE, PAGE))
        page = self.store.job(job)['pages'][0]
        for lang, first, readme in (('zh', zh_t('value.page_title.fallback'), zh_t('value.link_kind.direct')),
                                    ('en', en_t('value.page_title.fallback'), en_t('value.link_kind.direct'))):
            with self.subTest(lang=lang), _sandbox.language(lang):
                archive = Archive(self.root / f'archive-{lang}', self.store)
                folder = archive.prepare(MEDIA, dict(context, page=page))
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']), (DIRECT, FALLBACK))
                self.assertNotIn('msgs', record)
                self.assertEqual((folder / '网页正文.txt').read_text(encoding='utf-8-sig').splitlines()[0], first)
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], readme)
                indexed = next(item for item in self.store.archives() if item['folder'] == str(folder))
                self.assertMessage(indexed['title'], 'value.link_kind.direct')
                self.assertNotIn('msgs', indexed)
                # Archive.media: the media's own title (raw) replaces the app-made one.
                clip = folder / 'clip.mp4'
                clip.write_bytes(b'x')
                archive.media(folder, {'filepath': str(clip), 'title': 'Clip', 'uploader': 'Qq'})
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']), ('Clip', FALLBACK))
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], 'Clip')
                transcript = self.root / f'notes-{lang}.txt'
                transcript.write_text('notes', encoding='utf-8')
                archive.attach(folder, transcript)
                again = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual({key: item for key, item in again.items() if key != 'transcripts'},
                                 {key: item for key, item in record.items() if key != 'transcripts'})
                self.assertEqual(len(again['transcripts']), 1)
                # A media literally titled 网页 is the media's own text: raw in every language.
                archive.media(folder, {'filepath': str(clip), 'title': '网页'})
                self.assertEqual(json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))['title'], '网页')
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], '网页')

    def test_plain_titles_are_the_texts_themselves(self):
        """(2.0.0, S2-DESIGN.md 1.1) A string in a slot is the source's or the owner's own text: shown as it
        is in every language (the program's own titles arrive as messages)."""
        for lang in ('zh', 'en'):
            with self.subTest(lang=lang), _sandbox.language(lang):
                archive = Archive(self.root / f'plain-{lang}')
                folder = archive.prepare(MEDIA, {'source': PAGE, 'title': 'Clip page',
                                                 'page': {'url': PAGE, 'title': 'Page', 'text': 'B', 'markdown': 'B'}})
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']), ('Clip page', 'Page'))
                self.assertEqual((folder / '网页正文.txt').read_text(encoding='utf-8-sig').splitlines()[0], 'Page')
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], 'Clip page')

    def test_an_unreadable_record_is_set_aside_with_a_message(self):
        archive = Archive(self.root / 'aside')
        folder = archive.prepare(MEDIA, {'source': PAGE})
        record = folder / '资料信息.json'
        with mock.patch('library.os.rename', side_effect=FileExistsError), self.assertRaises(OSError) as caught:
            Archive._set_aside(record)
        self.assertMessage(caught.exception.args[0], 'core.archive.cannot_set_aside')
        self.assertEqual(str(caught.exception), zh_t('core.archive.cannot_set_aside', record=record))
        with self.assertRaises(ValueError) as caught:
            archive.save(self.root, {})
        self.assertMessage(caught.exception.args[0], 'core.archive.outside_root')


# ============================================================================ keys of a later version (I11 in format 2)

FUTURE = {'k': 'core.future.unknown', 'p': {'a': 1, 'b': 'x', 'c': {'k': 'core.future.inner'}}}
EXTRA = {'k': 'core.collect.http_status', 'p': {'status': 7, 'extra': 1}}


class LaterKeyTests(StoreCase):
    """(S2-DESIGN.md 1.1) A slot holding a key this version does not know (or a known key with an extra
    param) shows the key / the known text, never raises, and is written back exactly as it was."""

    def test_unknown_keys_survive_every_write(self):
        job = self.download_job()
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
            data['errors'] = [{'error': FUTURE, 'reason': EXTRA}]
            db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), job))
        record = self.store.job(job)['data']
        self.assertMessage(record['errors'][0]['error'], 'core.future.unknown')
        self.assertEqual(en(record['errors'][0]['error']), 'core.future.unknown')
        self.assertEqual(en(record['errors'][0]['reason']), en_t('core.collect.http_status', status=7))
        self.store.change_job(job, status='partial')
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        self.assertEqual(self.raw_job(job)[1]['errors'][0], {'error': FUTURE, 'reason': EXTRA})
        key = self.store.subscription('x', {'urls': [PAGE]})
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM subscriptions').fetchone()[0])
            data['last_error'] = FUTURE
            db.execute('UPDATE subscriptions SET data=?', (json.dumps(data, ensure_ascii=False),))
        self.store.finish_subscription(key, [], False, self.store.subscriptions()[0]['data']['last_error'])
        self.assertEqual(self.raw_subscription(key)[1]['last_error'], FUTURE)
        archive = Archive(self.root / 'archive')
        folder = archive.prepare(MEDIA, {'source': PAGE})
        path = folder / '资料信息.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data.update(page_title=FUTURE)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        clip = folder / 'c.mp4'
        clip.write_bytes(b'x')
        archive.media(folder, {'filepath': str(clip), 'title': 'Clip'})
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['page_title'], FUTURE)


# ============================================================================ limits

class LimitTests(StoreCase):
    def test_storable_repairs_lone_surrogates_and_keeps_types(self):
        job = self.download_job()
        broken = msg('core.engine.error', error=raw('bad \ud800 text'))
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': broken,
                                                     'reason': raw('r \udc80')})
        self.store.download_event(job, 'title', 'Song \ud83d')
        title, data, _text = self.raw_job(job)
        self.assertEqual(data['errors'], [{'error': {'k': 'core.engine.error', 'p': {'error': 'bad ? text'}},
                                           'reason': 'r ?'}])
        self.assertEqual((title, data['title_links']), ('Song ?', 2))
        repaired = library._storable({'e': broken, 'r': raw('\ud800'), 'p': '\ud800'})
        self.assertEqual((type(repaired['e']), repaired['e'].key), (Message, 'core.engine.error'))
        self.assertIs(type(repaired['e'].params['error']), Raw)
        self.assertEqual((type(repaired['r']), type(repaired['p'])), (Raw, str))
        same = msg('core.collect.too_large')
        self.assertIs(library._storable(same), same)

    def test_a_message_with_a_long_param_is_stored_whole(self):
        job = self.collect_job()
        big = msg('core.library.temporary', error=raw('x' * 3000))
        self.store.collection_event(job, 'collect_error', big)
        self.assertEqual(self.raw_job(job)[1]['errors'], [{'error': {'k': 'core.library.temporary', 'p': {'error': 'x' * 3000}},
                                                           'reason': None}])
        back = self.store.job(job)['data']['errors'][0]['error']
        self.assertMessage(back, 'core.library.temporary')
        self.assertEqual(plain(back), plain(big))

    def test_a_2000_link_page(self):
        job = self.collect_job()
        links = [{'url': f'https://example.invalid/v{n}', 'title': raw(f'Video number {n}'), 'kind': 'video_page',
                  'source': PAGE} for n in range(collector.MAX_LINKS - 2)]
        links += [{'url': MEDIA + str(n), 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE}
                  for n in range(2)]
        page = {'url': PAGE, 'title': raw('Example list'), 'text': '', 'markdown': '', 'links': links, 'next': None,
                '_checkpoint': {'visited': [PAGE], 'todo': []}}
        started = time.perf_counter()
        self.store.collection_event(job, 'collect_page', page)
        elapsed = time.perf_counter() - started
        stored = self.raw_pages(job)[0]
        self.assertEqual(sum(link['title'] == DIRECT for link in stored['links']), 2)
        self.assertEqual(len(stored['links']), collector.MAX_LINKS)
        self.assertLess(elapsed, 5.0)
        read = self.store.job(job)['pages'][0]
        self.assertEqual(sum(isinstance(link['title'], Message) for link in read['links']), 2)
        self.assertEqual(self.store.contexts([MEDIA + '1'])[MEDIA + '1']['title'].key, 'value.link_kind.direct')


# ============================================================================ invariants

def schema_of_1_7_0():
    return SCHEMA_OF_1_7_0


def master(path):
    with opened(path) as db:
        return (sorted(db.execute('SELECT type, name, tbl_name, sql FROM sqlite_master')),
                db.execute('PRAGMA user_version').fetchone()[0])


TABLE_KINDS = {'jobs': 'job', 'pages': 'page', 'subscriptions': 'subscription', 'entries': 'entry', 'archives': 'archive'}


def stored_objects(path, archive_roots=()):
    """(record kind, object) of every JSON object a library and its archive folders store."""
    found = []
    with opened(path) as db:
        for table, kind in TABLE_KINDS.items():
            for (text,) in db.execute(f'SELECT data FROM {table}'):
                found.append((kind, json.loads(text)))
    for root in archive_roots:
        for record in Path(root).rglob('资料信息.json'):
            found.append(('archive', json.loads(record.read_text(encoding='utf-8'))))
    return found


def is_message(value) -> bool:
    """A stored message form: {"k": <non-empty key>} with an optional object "p", nothing else."""
    return (isinstance(value, dict) and isinstance(value.get('k'), str) and bool(value['k'])
            and not set(value) - {'k', 'p'} and isinstance(value.get('p', {}), dict))


def message_paths(item, path=()):
    """The paths of the outermost message forms ({"k", "p"?}) in a stored object."""
    if is_message(item):
        yield path
    elif isinstance(item, dict):
        for key, child in item.items():
            yield from message_paths(child, path + (key,))
    elif isinstance(item, list):
        for index, child in enumerate(item):
            yield from message_paths(child, path + (index,))


def stored_message_keys(item) -> set:
    """The keys of every message form in a stored object, the ones in a message's params included."""
    found = set()
    if is_message(item):
        found.add(item['k'])
        item = item.get('p', {})
    if isinstance(item, dict):
        for child in item.values():
            found |= stored_message_keys(child)
    elif isinstance(item, list):
        for child in item:
            found |= stored_message_keys(child)
    return found


def on_slot(path, slots):
    """Is ``path`` one of the places ``slots`` names ('*': any key or index)?"""
    return any(len(slot) == len(path) and all(part == '*' or part == step for part, step in zip(slot, path))
               for slot in slots)


class InvariantTests(StoreCase):
    def scenario(self, title=None):
        """Every writer once; ``title`` (default: messages) is the text each writer gets."""
        texts = (lambda key: msg(key)) if title is None else (lambda key: title)
        download, collect = self.download_job(mode=formats.MODES[5], archive_contexts={
            A: {'title': texts('value.page_title.fallback'), 'source': PAGE, 'job_id': 'j', 'page_url': PAGE}}), \
            self.collect_job()
        self.store.download_event(download, 'item', {'url': A})
        self.store.download_event(download, 'title', 'Song')
        self.store.download_event(download, 'item_done', {'success': False, 'url': A, 'error': texts('core.collect.too_large'),
                                                          'reason': texts('core.reason.network')})
        self.store.download_event(download, 'done', {'cancelled': False, 'failed': 1, 'engine_error': 'x',
                                                     'archive_errors': [plain(msg('core.archive.media_mismatch'))]})
        self.store.extend_job(download, [C])
        self.store.collection_event(collect, 'collect_page', {
            'url': PAGE, 'title': texts('value.page_title.fallback'), 'text': '', 'markdown': '', 'next': None,
            'links': [{'url': MEDIA, 'title': texts('value.link_kind.direct'), 'kind': 'media', 'source': PAGE}],
            '_checkpoint': {'visited': [PAGE], 'todo': [{'url': B, 'depth': 0}]}})
        self.store.collection_event(collect, 'collect_error', texts('core.collect.too_large'))
        self.store.change_job(collect, status='partial')
        key = self.store.subscription('', {'urls': [PAGE]})
        self.store.finish_subscription(key, self.store.job(collect)['pages'], True)
        self.store.finish_subscription(key, [], False, texts('core.collect.needs_chrome'))
        archive = Archive(self.root / 'archive', self.store)
        folder = archive.prepare(MEDIA, dict(self.store.contexts([MEDIA])[MEDIA], page=self.store.job(collect)['pages'][0]))
        return download, collect, key, folder

    def test_i2_schema_unchanged_and_user_version_2(self):
        self.scenario()
        reference = self.root / 'reference.sqlite3'
        with opened(reference) as db:
            db.executescript(schema_of_1_7_0())
        self.assertEqual(master(self.store.path)[0], master(reference)[0], 'the tables and columns of 1.7.0')
        self.assertEqual(master(self.store.path)[1], 2, 'format 2 (S2-DESIGN.md 1.2)')
        self.assertEqual(library.SCHEMA, schema_of_1_7_0())

    def test_store_census_messages_only_on_slots(self):
        """(S2-DESIGN.md 4.1, store census) Every writer fed messages: each lands as {"k"} on one of the
        slots of its record kind (library.SLOTS) and nowhere else; no 'msgs' anywhere; the result's
        engine_error is a flag.  Fed raw texts: the same slots hold the strings, no message form at all."""
        self.scenario()
        found, keys = {}, set()
        for kind, item in stored_objects(self.store.path, [self.root / 'archive']):
            for path in message_paths(item):
                self.assertTrue(on_slot(path, library.SLOTS[kind]), (kind, path))
                found.setdefault(kind, set()).add(path[:1])
            keys |= stored_message_keys(item)
            self.assertNotIn('msgs', json.dumps(item, ensure_ascii=False))
            if kind == 'job' and 'result' in item:
                self.assertIs(item['result']['engine_error'], True)
        # (S2-AMEND 3.4 Q3-7) every key the writers stored, nested ones included, is in the key registry
        registry = set(json.loads((_sandbox.GOLDEN / 'stored-keys.json').read_text(encoding='utf-8'))['keys'])
        self.assertEqual(keys, {'core.collect.needs_chrome', 'core.collect.too_large', 'core.reason.network',
                                'value.link_kind.direct', 'value.page_title.fallback'})
        self.assertLessEqual(keys, registry, sorted(keys - registry))
        self.assertEqual(sorted(found), ['archive', 'entry', 'job', 'page', 'subscription'])
        self.assertEqual(found['job'], {('errors',), ('options',)})
        # The same writers fed raw texts: strings on the slots, no message form anywhere.
        store = self.store = Library(self.root / 'raw.sqlite3')
        archive_root = self.root / 'archive'
        shutil.rmtree(archive_root)
        self.scenario(raw('网页'))
        for kind, item in stored_objects(store.path, [archive_root]):
            self.assertEqual(list(message_paths(item)), [], (kind, item))
        objects = dict((kind, item) for kind, item in stored_objects(store.path) if kind in ('page', 'entry'))
        self.assertEqual(objects['page']['title'], '网页')
        self.assertEqual(objects['entry']['title'], '网页')

    def test_i7_no_proxy_credentials_in_any_stored_or_ipc_byte(self):
        secret = 'http://owner:hunter2secret@127.0.0.1:9'
        history = self.root / 'history.sqlite3'
        job = Library(history).create_job('collect', raw(PAGE), dict(urls=[PAGE], proxy=secret))
        request = self.root / 'job' / 'request.json'
        request.parent.mkdir()
        options = collector.CollectOptions([PAGE], proxy=secret, job_id=job, history_path=str(history))
        request.write_text(json.dumps(collector.asdict(options), ensure_ascii=False), encoding='utf-8')

        class Failing:
            @staticmethod
            def get(url, **kwargs):
                raise OSError(f'curl: (7) Failed to connect to {kwargs["proxy"]} via {kwargs["proxy"]}')

        fetchers = mock.MagicMock(Fetcher=Failing, StealthyFetcher=Failing)
        with mock.patch.dict(sys.modules, {'scrapling.fetchers': fetchers}), \
                mock.patch.object(collector, 'ca_bundle', return_value=''):
            self.assertEqual(collector.worker_main(str(request)), 0)
        events = request.with_suffix('.events').read_bytes()
        store_bytes = Path(history).read_bytes()
        for blob in (events, store_bytes):
            self.assertNotIn(b'hunter2secret', blob)
            self.assertNotIn(b'owner:', blob)
        error = Library(history).job(job)['data']['errors'][0]['error']
        self.assertIn(messages.PROXY_TOKEN, error)
        mask, en_mask = messages.PROXY_TOKEN, en_t('core.proxy_mask')     # stored: the token; shown: the catalog text
        self.assertEqual(error, f'curl: (7) Failed to connect to {mask} via {mask}')
        self.assertEqual(en(error), f'curl: (7) Failed to connect to {en_mask} via {en_mask}')
        # A message param masked by its producer (P8) is stored masked.
        masked = messages.masked(msg('core.library.temporary', error=raw(f'proxy {secret} refused')),
                                 [(secret, messages.PROXY_TOKEN)])
        self.store.collection_event(self.collect_job(), 'collect_error', masked)
        self.assertNotIn(b'hunter2secret', Path(self.store.path).read_bytes())
        self.assertEqual(Library(self.store.path).job(self.store.history()[0]['id'])['data']['errors'][0]['error']
                         .params['error'], f'proxy {mask} refused')


def tree_hashes(*roots):
    found = {}
    for root in roots:
        for path in sorted(Path(root).rglob('*')):
            if path.is_file() and not path.name.endswith(('-journal', '-wal', '-shm')):
                found[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def table_dump(path):
    with opened(path) as db:
        return {table: sorted(db.execute(f'SELECT * FROM {table}'))
                for table in ('jobs', 'pages', 'subscriptions', 'entries', 'archives', 'downloads')}


class UnfinishedCensusTests(StoreCase):
    def test_unfinished_census_messages_only_in_the_error_slot(self):
        """(S2-DESIGN.md 4.1 P3, store census) unfinished.merge / save fed messages: the file holds each as
        {"k"} in the entry's error slot (unfinished.SLOTS) and nowhere else, no 'msgs', no 1.5.1 view;
        read back, it is the same message.  Fed raw texts: strings, no message form."""
        import engine
        import unfinished
        state = self.root / 'state'
        options = engine._entry_options(engine.Options(self.root / 'out'))
        for fed, form in ((msg('core.engine.cannot_start_link', error=msg('core.collect.too_large')), dict),
                          (raw('网页'), str)):
            with self.subTest(fed=type(fed).__name__), _sandbox.state_patch(state):
                results = [{'url': A, 'key': 'ka', 'format': 'audio_best', 'options': options, 'outcome': 'failed',
                            'error': fed, 'title': 'Song', 'referer': '', 'media_keys': []}]
                with unfinished.LOCK:
                    unfinished.save(unfinished.merge(unfinished.load_for_merge(), results))
                data = json.loads(paths.failed_file().read_text(encoding='utf-8'))
                self.assertEqual(list(data), ['version', 'entries'])
                self.assertEqual(data['version'], 2)
                self.assertNotIn('msgs', json.dumps(data, ensure_ascii=False))
                entry = data['entries'][0]
                self.assertIsInstance(entry['error'], form)
                self.assertEqual([path for path in message_paths(data)], [('entries', 0, 'error')] if form is dict else [])
                if form is dict:
                    self.assertEqual(entry['error'], messages.to_json(fed))
                    loaded = unfinished.load()[0]['error']
                    self.assertMessage(loaded, fed.key)
                    self.assertEqual(loaded.params['error'].key, 'core.collect.too_large')
                    self.assertEnglish(loaded)
                else:
                    self.assertEqual(entry['error'], '网页')
                paths.failed_file().unlink()


# ============================================================================ collector IPC (2.8)

class IpcTests(StoreCase):
    def fake_collect(self, options, emit):
        emit('collect_status', msg('core.collect.page_n', page=1))
        emit('collect_page', {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': 'x', 'markdown': 'x',
                              'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': 'media', 'source': PAGE},
                                        {'url': A, 'title': raw('Song A'), 'kind': 'video_page', 'source': PAGE},
                                        {'url': B, 'title': raw('Web'), 'kind': 'web', 'source': PAGE}],
                              'next': None, '_checkpoint': {'visited': [PAGE], 'todo': []}})
        emit('collect_error', msg('core.library.temporary', error=msg('core.collect.http_status', status=503)))
        emit('collect_error', 'curl: (6) Could not resolve host')
        emit('collect_checkpoint', {'visited': [PAGE], 'todo': []})

    def test_worker_store_and_window_get_the_same_values(self):
        history = self.root / 'history.sqlite3'
        job = Library(history).create_job('collect', raw(PAGE), dict(urls=[PAGE]))
        request = self.root / 'job' / 'request.json'
        request.parent.mkdir()
        options = collector.CollectOptions([PAGE], job_id=job, history_path=str(history))
        request.write_text(json.dumps(collector.asdict(options), ensure_ascii=False), encoding='utf-8')
        with mock.patch.object(collector, 'collect', self.fake_collect):
            self.assertEqual(collector.worker_main(str(request)), 0)
        lines = request.with_suffix('.events').read_bytes().split(b'\n')
        lines = [line for line in lines if line]
        self.assertEqual(len(lines), 5)
        self.assertEqual(json.loads(lines[0]), ['collect_status', {'$m': {'k': 'core.collect.page_n', 'p': {'page': 1}}}])
        received = []
        runner = collector.CollectionRunner(lambda kind, value_: received.append((kind, value_)))
        malformed = [b'not json', b'["collect_error", {"$m": [1]}]', b'["collect_page", {"$m": {"k": ""}}]',
                     b'["collect_status", {"$m": {"k": "core.collect.page_n", "p": [1]}}]', b'[1]']
        for line in malformed[:2] + lines[:2] + malformed[2:] + lines[2:]:
            runner._forward(line)
        self.assertEqual([kind for kind, _value in received],
                         ['collect_status', 'collect_page', 'collect_error', 'collect_error', 'collect_checkpoint'])
        status, page, nested, plain_error = (value_ for _kind, value_ in received[:4])
        self.assertMessage(status, 'core.collect.page_n')
        self.assertEqual(plain(status), zh_t('core.collect.page_n', page=1))
        self.assertMessage(page['title'], 'value.page_title.fallback')
        self.assertMessage(page['links'][0]['title'], 'value.link_kind.direct')
        self.assertEqual([type(page['links'][index]['title']) for index in (1, 2)], [str, str],
                         'a source text arrives as its plain text')
        self.assertMessage(nested, 'core.library.temporary')
        self.assertEqual(nested.params['error'].key, 'core.collect.http_status')
        self.assertEqual(nested.params['error'].params['status'], 503)
        self.assertIs(type(plain_error), str)
        self.assertEnglish(nested, en_t('core.library.temporary', error=en_t('core.collect.http_status', status=503)))
        # The worker's own store write received the in-memory values (I9).
        with opened(history) as db:
            row = json.loads(db.execute('SELECT data FROM pages').fetchone()[0])
            data = json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])
        self.assertEqual((row['title'], [link['title'] for link in row['links']]), (FALLBACK, [DIRECT, 'Song A', 'Web']))
        self.assertEqual(data['errors'][0], {'error': {'k': 'core.library.temporary', 'p': {
            'error': {'k': 'core.collect.http_status', 'p': {'status': 503}}}}, 'reason': None})
        self.assertEqual(Library(history).job(job)['status'], 'partial')

    def test_the_parse_page_titles(self):
        class Response:
            status = 200

            def __init__(self, title, url=PAGE):
                self.title, self.url = title, url

            def css(self, query):
                return Query(self.title, query)

        class Query(list):
            def __init__(self, title, query):
                super().__init__()
                self.title, self.query = title, query

            def getall(self):
                return [self.title] if self.query == 'title::text' and self.title else []

            def get(self):
                return None

        fake_selector = mock.MagicMock()
        fake_selector.return_value.css.return_value = []
        fake_selector.return_value.html_content = ''
        fake_selector.return_value.get_all_text.return_value = ''
        modules = {'scrapling': mock.MagicMock(Selector=fake_selector), 'markdownify': mock.MagicMock(markdownify=lambda *a, **k: '')}
        with mock.patch.dict(sys.modules, modules):
            titled = collector.parse_page(Response('  Some   page '), PAGE, captured=[MEDIA])
            untitled = collector.parse_page(Response(''), PAGE, captured=[MEDIA])
            fallback = collector.parse_page(Response('', 'file:///page'), 'file:///page')
        self.assertIs(type(titled['title']), Raw)
        self.assertEqual(titled['title'], 'Some   page')
        self.assertMessage(titled['links'][0]['title'], 'value.link_kind.dynamic_request')
        self.assertEqual(titled['links'][0]['kind'], 'media')
        self.assertEqual((type(untitled['title']), untitled['title']), (Raw, 'example.invalid'), 'the host name')
        self.assertMessage(fallback['title'], 'value.page_title.fallback')
        self.assertEqual(plain(fallback['title']), zh_t('value.page_title.fallback'))


# ============================================================================ updater

class UpdaterTests(unittest.TestCase):
    def test_update_errors_keep_the_templates_and_carry_keys(self):
        import urllib.error
        cases = [
            (urllib.error.URLError(TimeoutError()), 'core.update.no_github', zh_t('core.update.no_github', reason=zh_t('core.update.timeout'))),
            (urllib.error.HTTPError(updater.RELEASE_URL, 503, 'x', {}, None), 'core.update.no_github',
             zh_t('core.update.no_github', reason='HTTP 503')),
            (ValueError('not json'), 'core.update.bad_release', zh_t('core.update.bad_release')),
        ]
        for failure, key, text in cases:
            with self.subTest(key=key, text=text), mock.patch('urllib.request.urlopen', side_effect=failure), \
                    self.assertRaises(updater.UpdateError) as caught:
                updater.check_latest()
            self.assertEqual(str(caught.exception), text)
            self.assertIsInstance(caught.exception.args[0], Message)
            self.assertEqual(caught.exception.args[0].key, key)
            with _sandbox.language('en'):
                self.assertEqual(CJK.findall(messages.error_text(caught.exception)), [])
        self.assertEqual(updater._reason(urllib.error.URLError(TimeoutError())).key, 'core.update.timeout')
        self.assertIs(type(updater._reason(OSError('boom'))), Raw)
        self.assertIs(type(updater._file_reason(PermissionError(13, 'Access is denied'))), Raw)

    def test_the_start_check_reasons(self):
        engine = updater.EngineUpdater(lambda *_: None, mock.MagicMock(is_set=lambda: False))
        timeout = subprocess.TimeoutExpired('x', 60)
        for outcome, reason_key, text in (
                (timeout, 'core.update.start_timeout', zh_t('core.update.start_failed', reason=zh_t('core.update.start_timeout', timeout=60))),
                (OSError('no'), 'core.update.start_cannot', zh_t('core.update.start_failed', reason=zh_t('core.update.start_cannot'))),
                ((3, b'', b''), 'core.update.start_exit', zh_t('core.update.start_failed', reason=zh_t('core.update.start_exit', code=3))),
                ((0, b'hello', b''), 'core.update.start_no_version', zh_t('core.update.start_failed', reason=zh_t('core.update.start_no_version')))):
            with self.subTest(reason=reason_key):
                patch = mock.patch.object(updater, '_version_output', side_effect=outcome) \
                    if isinstance(outcome, BaseException) else mock.patch.object(updater, '_version_output', return_value=outcome)
                with patch, self.assertRaises(updater.UpdateError) as caught:
                    engine._check_start('x.exe')
                self.assertEqual(str(caught.exception), text)
                message = caught.exception.args[0]
                self.assertEqual((message.key, message.params['reason'].key), ('core.update.start_failed', reason_key))
                self.assertEnglish(message)

    def assertEnglish(self, item):
        self.assertEqual(CJK.findall(en(item)), [], en(item))

    def test_restore_builtin_and_run_events(self):
        with mock.patch.object(updater, '_remove_updated', return_value=False), \
                self.assertRaises(updater.UpdateError) as caught:
            updater.restore_bundled()
        self.assertEqual(str(caught.exception), zh_t('core.update.restore_failed'))
        self.assertEqual(caught.exception.args[0].key, 'core.update.restore_failed')
        self.assertEqual(updater.BUILTIN, zh_t('core.update.builtin'))
        self.assertEqual(updater.BUILTIN.key, 'core.update.builtin')
        with mock.patch.object(updater, 'engine_path', side_effect=OSError):
            self.assertIs(updater.engine_version(), updater.BUILTIN)
        events = []
        engine = updater.EngineUpdater(lambda kind, item: events.append((kind, item)), mock.MagicMock(is_set=lambda: False))
        with mock.patch.object(updater, 'check_latest', side_effect=updater.UpdateError(msg('core.update.no_exe'))):
            engine.run()
        self.assertEqual([kind for kind, _item in events], ['update_status', 'update_error', 'update_done'])
        self.assertEqual(events[0][1].key, 'core.update.checking')
        self.assertEqual((events[1][1].key, events[1][1]), ('core.update.no_exe', zh_t('core.update.no_exe')))
        # (2.0.0 S2 P4) the 1.5.1 events (legacy_events) are gone; an unexpected error is its plain text
        plain = []
        engine = updater.EngineUpdater(lambda kind, item: plain.append((kind, item)), mock.MagicMock(is_set=lambda: False))
        with mock.patch.object(updater, 'check_latest', side_effect=RuntimeError('boom')):
            engine.run()
        self.assertEqual(plain[0], ('update_status', zh_t('core.update.checking')))
        self.assertEqual(plain[0][1].key, 'core.update.checking')
        self.assertEqual(plain[1], ('update_error', 'boom'))
        self.assertIs(type(plain[1][1]), str)


# ============================================================================ G4: the store channels

def sample_params(key):
    params = {}
    for name in sorted({field for _l, field, _s, _c in FORMATTER.parse(i18n.CATALOG['zh'][key]) if field}):
        if name in NUMERIC:
            params[name] = 7
        elif name == 'mode':
            # (2.0.0 S2 P4) no Value any more: a format is its display message
            params[name] = msg('format.' + formats.MODES[1])      # (2.0.0 S4 A1) the one name of a format
        elif name in ('error', 'reason'):
            params[name] = msg('core.collect.too_large')
        elif name == 'name':
            params[name] = msg('value.subscription.untitled')
        else:
            params[name] = 'Qq'
    return params


CHANNEL_PREFIXES = ('core.collect.', 'core.library.', 'core.archive.', 'core.update.', 'value.page_title.',
                    'value.link_kind.direct', 'value.link_kind.dynamic_request', 'value.job_title.', 'value.subscription.')


class ChannelMatrixTests(StoreCase):
    def keys(self):
        return sorted(key for key in i18n.CATALOG['zh'] if key.startswith(CHANNEL_PREFIXES))

    def test_every_key_through_every_store_field(self):
        """Five message slots (job error and reason, page title, link title, subscription last_error) for
        every key: stored as the message's JSON form, read back as that message.  (2.0.0) The title
        column holds text only (the job title is never a message)."""
        keys = self.keys()
        self.assertGreater(len(keys), 40)
        job, collect = self.download_job(), self.collect_job()
        subscription = self.store.subscription('Qq', {'urls': [PAGE]})
        checked = 0
        for key in keys:
            message = msg(key, **sample_params(key))
            with self.subTest(key=key):
                self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': message, 'reason': message})
                self.store.collection_event(collect, 'collect_page', {'url': PAGE, 'title': message, 'links': [
                    {'url': MEDIA, 'title': message, 'kind': 'media', 'source': PAGE}], '_checkpoint': None})
                self.store.finish_subscription(subscription, [], False, messages.clauses([message, msg('core.collect.too_large')]))
                record, collected = self.store.job(job), self.store.job(collect)
                subscription_row = self.store.subscriptions()[0]
                values = [record['data']['errors'][-1]['error'], record['data']['errors'][-1]['reason'],
                          collected['pages'][0]['title'], collected['pages'][0]['links'][0]['title'],
                          subscription_row['data']['last_error']]
                for item in values:
                    self.assertIsInstance(item, Message)
                    shown = en(item)
                    self.assertEqual(CJK.findall(shown), [], f'{key}: {shown}')
                    self.assertEqual(zh(item), plain(item))
                    checked += 1
                self.assertEqual(values[0].key, key)
                self.assertEqual(plain(values[0]), plain(message))
                self.assertEqual(self.raw_job(job)[1]['errors'][-1]['error'], messages.to_json(message))
        self.assertEqual(checked, 5 * len(keys))


if __name__ == '__main__':
    unittest.main()
