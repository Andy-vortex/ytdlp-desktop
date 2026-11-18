"""1.8.0 store, collector IPC and updater messages (MESSAGES.md 2.8, 3.3, 3.4, 4.3-4.7, 7.5; WP-3).

    python -X utf8 -B test_messages_store.py

Every Library writer / reader pair for every record kind of 4.3 and every value type (Message, Raw,
Value, plain text): sidecar entries, null entries only for liftable Raw texts, orphan pruning, the
column texts (columns=), legacy rows lifted per field kind and never persisted, foreign entries kept
byte for byte (I11), _storable with lone surrogates, the 2 KB cap, a 2000-link page; the required I10
test of 3.4 (a 1.6.0-only-format job through every read-modify-write: the stored options keep their
bytes); contexts -> Archive -> 资料信息.json -> the owner's files; the collector IPC round trip (worker
-> request.events -> CollectionRunner, a malformed line skipped); str(UpdateError) == updater.MSG_*;
I2 (schema and user_version), I3, I7, I8 (a library, and settings.json with the failed list, written by
the frozen 1.7.0 code, read by 1.8.0); the store channels of G4 (every core.collect / core.library /
core.archive / core.update / value key stored and read back: en without Chinese, zh = the stored
text).  No window, no network; the I8 children run release-sources/1.7.0 with -B.
"""
import _sandbox
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
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
import updater
from library import Archive, Library
from messages import Message, Raw, Value, msg, raw, value

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
FORMATTER = string.Formatter()
A, B, C = ('https://example.invalid/' + name for name in 'abc')
PAGE = 'https://example.invalid/list'
MEDIA = 'https://cdn.example.invalid/clip.mp4'
OLD_170 = _sandbox.release_src('1.7.0')
NUMERIC = {'count', 'code', 'page', 'status', 'length', 'seconds', 'attempt', 'limit', 'line', 'pages', 'links'}


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


def compact(entry):
    return json.dumps(entry, ensure_ascii=False, separators=(',', ':'))


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

    def download_job(self, links=(A, B), title=None, **options):
        title = title if title is not None else msg('value.job_title.placeholder', count=len(links),
                                                     mode=value('mode', formats.MODES[0]))
        return self.store.create_job('download', title, dict(urls=list(links), output=str(self.root), **options))

    def collect_job(self, title=None):
        return self.store.create_job('collect', title if title is not None else raw(PAGE),
                                     dict(urls=[PAGE], dynamic=False, scrolls=0, selector='', max_pages=1))

    def assertMessage(self, item, key, origin='stored'):
        self.assertIsInstance(item, Message, item)
        self.assertEqual((item.key, item.origin), (key, origin), item)

    def assertEnglish(self, item, expected=None):
        shown = en(item)
        self.assertEqual(CJK.findall(shown), [], shown)
        if expected is not None:
            self.assertEqual(shown, expected)
        self.assertEqual(zh(item), plain(item))


# ============================================================================ jobs (record kind job)

class JobTests(StoreCase):
    def test_create_job_writes_the_column_entry_and_reads_it_back(self):
        job = self.download_job()
        title, data, _text = self.raw_job(job)
        self.assertEqual(title, '2 条 · ' + formats.MODES[0])
        self.assertEqual(data['msgs'], {title: {'k': 'value.job_title.placeholder',
                                                'p': {'count': 2, 'mode': {'g': 'mode', 'v': formats.MODES[0]}}}})
        for record in (self.store.job(job), self.store.history()[0]):
            self.assertMessage(record['title'], 'value.job_title.placeholder')
            self.assertIsInstance(record['title'].params['mode'], Value)
            self.assertNotIn('msgs', record['data'])
            self.assertEnglish(record['title'])
            self.assertTrue(en(record['title']).startswith('Links: 2 · '), en(record['title']))
        # A collect job named by its first link (raw): no entry, the plain text reads back.
        collect = self.collect_job()
        self.assertNotIn('msgs', self.raw_job(collect)[1])
        self.assertEqual(self.store.job(collect)['title'], PAGE)
        # A subscription check names its job with the decoded subscription name (C-F40, nested).
        named = self.collect_job(msg('value.job_title.subscription', name=msg('value.subscription.untitled')))
        title, data, _text = self.raw_job(named)
        self.assertEqual(data['msgs'][title], {'k': 'value.job_title.subscription',
                                               'p': {'name': {'k': 'value.subscription.untitled'}}})
        self.assertEnglish(self.store.job(named)['title'], 'Subscription: Untitled subscription')
        # A raw title that reads like an app text gets a null entry (E2) and stays raw.
        odd = self.collect_job(raw('订阅：x'))
        self.assertEqual(self.raw_job(odd)[1]['msgs'], {'订阅：x': None})
        self.assertIs(type(self.store.job(odd)['title']), Raw)
        self.assertEqual(en(self.store.job(odd)['title']), '订阅：x')

    def test_the_title_event_names_the_job_in_one_transaction(self):
        multi, single, quirky = self.download_job(), self.download_job((C,)), self.download_job()
        self.store.download_event(multi, 'title', 'Some Song')
        self.store.download_event(single, 'title', 'Some Song')
        self.store.download_event(quirky, 'title', '网页')
        title, data, _text = self.raw_job(multi)
        self.assertEqual(title, f'{"Some Song"} 等 {2} 条', 'the 1.7.0 bytes')
        self.assertEqual(data['msgs'], {title: {'k': 'value.job_title.multi', 'p': {'title': 'Some Song', 'count': 2}}})
        self.assertTrue(data['titled'])
        self.assertEnglish(self.store.job(multi)['title'], 'Some Song and others (2 links)')
        title, data, _text = self.raw_job(single)
        self.assertEqual(title, 'Some Song')
        self.assertNotIn('msgs', data, 'the old placeholder entry is pruned, a raw title costs nothing')
        self.assertEqual(self.store.job(single)['title'], 'Some Song')
        record = self.store.job(quirky)
        self.assertMessage(record['title'], 'value.job_title.multi')
        self.assertIs(type(record['title'].params['title']), Raw)
        self.assertEqual(en(record['title']), '网页 and others (2 links)', 'a media title is never translated')
        # Later titles do not rename it; an empty title never does.
        self.store.download_event(multi, 'title', 'Other')
        self.store.download_event(single, 'title', '   ')
        self.assertEqual(self.raw_job(multi)[0], 'Some Song 等 2 条')

    def test_item_done_errors_reasons_and_pruning(self):
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
        self.assertEqual(data['errors'], [plain(error), 'ERROR: [generic] b: boom', '找不到任务记录。'])
        self.assertEqual(data['msgs'][plain(error)], {'k': 'core.engine.cannot_start_link', 'p': {
            'error': {'k': 'core.links.too_long', 'p': {'length': 2100, 'url': 'youtu.be/x'}}}})
        self.assertIsNone(data['msgs']['找不到任务记录。'], 'a raw text equal to an app text: null (E2)')
        self.assertNotIn('ERROR: [generic] b: boom', data['msgs'])
        self.assertEqual(data['last_reason'], {'error': '找不到任务记录。', 'reason': 'Qq'}, 'stripped, type kept')
        record = self.store.job(job)['data']
        self.assertMessage(record['errors'][0], 'core.engine.cannot_start_link')
        self.assertIs(type(record['errors'][1]), str)
        self.assertIs(type(record['errors'][2]), Raw)
        self.assertIs(type(record['last_reason']['reason']), str, 'no entry for Qq: it reads back as plain text')
        # The reason of an error, with one entry for the text both fields hold (collision, L-5).
        other = self.download_job()
        self.store.download_event(other, 'item_done', {'success': False, 'url': A, 'error': error, 'reason': reason})
        _title, data, _text = self.raw_job(other)
        self.assertEqual(data['last_reason'], {'error': plain(error), 'reason': plain(reason)})
        self.assertEqual(data['msgs'][plain(reason)], {'k': 'core.reason.http_403'})
        last = self.store.job(other)['data']['last_reason']
        self.assertMessage(last['reason'], 'core.reason.http_403')
        self.assertEnglish(last['reason'], 'The site refused access (403)')
        # errors[-20:]: the entry of a dropped error is pruned with it (E4).
        third = self.download_job()
        self.store.download_event(third, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        self.assertIn('页面超过 8 MB，请缩小采集范围。', self.raw_job(third)[1]['msgs'])
        for number in range(25):
            self.store.download_event(third, 'item_done', {'success': False, 'url': A,
                                                           'error': msg('core.engine.error', error=f'e{number}')})
        title, data, _text = self.raw_job(third)
        self.assertEqual(len(data['errors']), 20)
        self.assertEqual(set(data['msgs']), set(data['errors']) | {title})
        self.assertNotIn('页面超过 8 MB，请缩小采集范围。', data['msgs'])

    def test_item_done_cut_reason_is_stored_stripped(self):
        # (fix round 1, WP3-R1-01) A reason cut to 70 characters (D-F28) can end in a space or a
        # newline; 1.7.0 stored reason.strip(), so the stored bytes are the stripped zh text and the
        # key is kept (en shows the whole message, as for cut).
        error = msg('core.engine.cannot_start_link',
                    error=msg('core.cookies.missing', file='C:/x' + 'a' * 18 + ' b/cookies.txt'))
        text = plain(error)
        cases = {'space': text.index('→ ') + 2, 'newline': text.index('\n') + 1}
        for name, length in cases.items():
            with self.subTest(name):
                reason = messages.cut(error, length)
                self.assertMessage(reason, 'core.engine.cannot_start_link', 'live')
                self.assertNotEqual(plain(reason), plain(reason).strip(), 'the cut ends in whitespace')
                job = self.download_job()
                self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': error,
                                                             'reason': reason})
                _title, data, _text = self.raw_job(job)
                self.assertEqual(data['last_reason'], {'error': text, 'reason': plain(reason).strip()}, '1.7.0 bytes')
                self.assertEqual(data['msgs'][plain(reason).strip()], data['msgs'][text], 'the key is kept')
                last = self.store.job(job)['data']['last_reason']['reason']
                self.assertMessage(last, 'core.engine.cannot_start_link')
                self.assertEqual(zh(last), plain(reason).strip())
                self.assertEqual(en(last), en(error), 'en shows the whole message')
                self.assertEnglish(last)
        # A reason without edge whitespace is stored as it is (the same object).
        self.assertIs(library._stripped_reason(error), error)
        self.assertEqual(library._stripped_reason(value('mode', ' x ')), 'x')
        self.assertIs(type(library._stripped_reason(value('mode', ' x '))), Value)

    def test_library_errors_are_messages(self):
        with self.assertRaises(ValueError) as caught:
            self.store.job('no-such-job')
        self.assertMessage(caught.exception.args[0], 'core.library.no_job', 'live')
        self.assertEqual(str(caught.exception), '找不到任务记录。')
        for call in (lambda: self.store.subscription('x', {}, interval=5),
                     lambda: self.store.configure_subscription('x', True, 5)):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertMessage(caught.exception.args[0], 'core.library.bad_interval', 'live')
        folder = self.root / 'not-a-database'
        folder.mkdir()
        store, warning = library.open_library(folder)
        self.assertMessage(warning, 'core.library.temporary', 'live')
        self.assertTrue(plain(warning).startswith('历史记录本次不保存：'), warning)
        self.assertIs(type(warning.params['error']), str, 'the sqlite / OS text is a raw param')
        self.assertNotEqual(store.path.parent, folder)

    def test_collection_events_pages_and_errors(self):
        job = self.collect_job()
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': '', 'markdown': '',
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE},
                          {'url': A, 'title': raw('Song A'), 'kind': '视频页面', 'source': PAGE},
                          {'url': B, 'title': raw('网页'), 'kind': '网页链接', 'source': PAGE}],
                'next': None, '_checkpoint': {'visited': [PAGE], 'todo': []}}
        self.store.collection_event(job, 'collect_page', page)
        self.store.collection_event(job, 'collect_error', msg('core.collect.http_status', status=503))
        self.store.collection_event(job, 'collect_error', 'curl: (7) Failed to connect')
        self.store.collection_event(job, 'collect_checkpoint', {'visited': [PAGE], 'todo': [{'url': C, 'depth': 0}]})
        stored = self.raw_pages(job)[0]
        self.assertEqual(stored['msgs'], {'网页': {'k': 'value.page_title.fallback'}, '媒体直链': {'k': 'value.link_kind.direct'}},
                         'a message wins over the null of the raw link titled 网页 (L-5)')
        self.assertNotIn('_checkpoint', stored)
        _title, data, _text = self.raw_job(job)
        self.assertEqual(data['checkpoint'], {'visited': [PAGE], 'todo': [{'url': C, 'depth': 0}]})
        self.assertEqual(data['errors'], ['网站返回 HTTP 503，可尝试动态模式或稍后重试。', 'curl: (7) Failed to connect'])
        self.assertEqual(data['msgs'], {data['errors'][0]: {'k': 'core.collect.http_status', 'p': {'status': 503}}})
        record = self.store.job(job)
        self.assertMessage(record['pages'][0]['title'], 'value.page_title.fallback')
        self.assertMessage(record['pages'][0]['links'][0]['title'], 'value.link_kind.direct')
        self.assertIs(type(record['pages'][0]['links'][1]['title']), str)
        self.assertNotIn('msgs', record['pages'][0])
        self.assertEnglish(record['data']['errors'][0], 'The site returned HTTP 503. Try dynamic mode or try again later.')
        # INSERT OR REPLACE: a page collected again with a raw title loses the old entries.
        self.store.collection_event(job, 'collect_page', dict(page, title=raw('Real title'), links=[], _checkpoint=None))
        self.assertNotIn('msgs', self.raw_pages(job)[0])

    def test_rename_job_updates_the_column_and_its_entry_together(self):
        job = self.download_job()
        _title, _data, before = self.raw_job(job)
        self.store.rename_job(job, msg('value.job_title.subscription', name=raw('Qq')))
        title, data, _text = self.raw_job(job)
        self.assertEqual(title, '订阅：Qq')
        self.assertEqual(data['msgs'], {'订阅：Qq': {'k': 'value.job_title.subscription', 'p': {'name': 'Qq'}}})
        self.store.rename_job(job, 'Plain name')
        title, data, text = self.raw_job(job)
        self.assertEqual(title, 'Plain name')
        self.assertNotIn('msgs', data)
        self.assertEqual(json.loads(text), {key: item for key, item in json.loads(before).items() if key != 'msgs'})
        self.store.rename_job(job, 'Another plain name')
        self.assertEqual(self.raw_job(job)[2], text, 'an unchanged sidecar does not rewrite the record')
        self.store.rename_job('no-such-job', 'x')           # as 1.7.0: nothing to rename, no error

    def test_legacy_rows_are_lifted_per_field_kind_and_never_persisted(self):
        data = {'options': {'urls': [A, B], 'mode': formats.MODES[0]}, 'subscription': None, 'checkpoint': None,
                'errors': ['页面超过 8 MB，请缩小采集范围。', '此链接无法开始：链接过长（2100 个字符），无法交给下载引擎：youtu.be/x'],
                'last_reason': {'error': '此链接无法开始：链接过长（2100 个字符），无法交给下载引擎：youtu.be/x',
                                'reason': '此链接无法开始：链接过长（2100 个字符），无法交给下载引擎：youtu.be/x'[:70]},
                'owner': 1, 'worker': None, 'completed': [], 'files': []}
        text = json.dumps(data, ensure_ascii=False)
        with opened(self.store.path) as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', ('old', 'download', '2条 · 音频 · 原始最佳', 'partial', 1.0, text))
            db.execute('INSERT INTO pages VALUES(?,?,?)', ('old', PAGE, json.dumps(
                {'url': PAGE, 'title': '网页', 'links': [{'url': MEDIA, 'title': '动态页面媒体请求', 'kind': '媒体地址',
                                                         'source': PAGE}]}, ensure_ascii=False)))
        record = self.store.job('old')
        self.assertMessage(record['title'], 'value.job_title.placeholder', 'legacy')
        self.assertMessage(record['data']['errors'][0], 'core.collect.too_large', 'legacy')
        self.assertMessage(record['data']['errors'][1], 'core.engine.cannot_start_link', 'legacy')
        self.assertMessage(record['data']['last_reason']['reason'], 'core.engine.cannot_start_link', 'legacy')
        self.assertEqual(record['data']['last_reason']['reason'], data['last_reason']['reason'], 'the stored text (I4)')
        self.assertMessage(record['pages'][0]['title'], 'value.page_title.fallback', 'legacy')
        self.assertMessage(record['pages'][0]['links'][0]['title'], 'value.link_kind.dynamic_request', 'legacy')
        for item in (record['title'], *record['data']['errors'], record['data']['last_reason']['reason']):
            self.assertEnglish(item)
        self.store.change_job('old', status='interrupted')
        self.assertEqual(self.raw_job('old')[2], text, 'a legacy guess is never written (I5): same bytes')

    def test_the_interrupted_marking_reads_the_row_again_for_writing(self):
        job = self.download_job()
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        self.store.change_job(job, owner=None, worker=None)
        _title, before, _text = self.raw_job(job)
        rows = self.store.history()
        self.assertEqual(rows[0]['status'], 'interrupted')
        self.assertNotIn('msgs', rows[0]['data'])
        _title, after, _text = self.raw_job(job)
        self.assertEqual(after, before)
        self.assertEqual(self.raw_row('jobs', 'id', job)['status'], 'interrupted')


# ============================================================================ the required I10 test (3.4)

class OptionsBytesTests(StoreCase):
    """A job with a 1.6.0-only format through every read-modify-write: options keep their bytes."""

    def test_every_read_modify_write_keeps_the_stored_options(self):
        for mode in (formats.MODES[5], formats.MODES[6]):
            with self.subTest(mode=mode):
                self.assertNotIn(mode, formats.LEGACY_MODES)
                job = self.download_job((A, B), mode=mode, resolution='最佳',
                                        archive_root=str(self.root / 'archive'),
                                        archive_contexts={A: {'title': '网页', 'source': PAGE, 'job_id': 'j',
                                                              'page_url': PAGE}})
                self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large'),
                                                             'reason': msg('core.reason.network')})

                def options_bytes():
                    data = self.raw_job(job)[1]
                    self.assertEqual(data['options']['mode_v2'], mode)
                    self.assertIn(data['options']['mode'], formats.LEGACY_MODES)
                    return json.dumps(data['options'], ensure_ascii=False)

                def entries():
                    return self.raw_job(job)[1].get('msgs', {})

                before, sidecar = options_bytes(), entries()
                self.assertIn('页面超过 8 MB，请缩小采集范围。', sidecar)
                self.assertNotIn('msgs', self.raw_job(job)[1]['options'])
                # Display read: the real format and the lifted context title, nothing written.
                shown = self.store.job(job)['data']['options']
                self.assertEqual(shown['mode'], mode)
                self.assertNotIn('mode_v2', shown)
                self.assertMessage(shown['archive_contexts'][A]['title'], 'value.page_title.fallback', 'legacy')
                self.assertEqual(options_bytes(), before)
                steps = [
                    ('history interrupted marking', lambda: (self.store.change_job(job, status='running', owner=None,
                                                                                   worker=None), self.store.history())),
                    ('change_job(status=...)', lambda: self.store.change_job(job, status='partial')),
                    ('change_job(worker=...)', lambda: self.store.change_job(job, worker=os.getpid())),
                    ('item_done', lambda: self.store.download_event(job, 'item_done', {'success': True, 'url': A})),
                    ('done', lambda: self.store.download_event(job, 'done', {'cancelled': False, 'failed': 1})),
                    ('title', lambda: self.store.download_event(job, 'title', 'Some Song')),
                    ('rename_job', lambda: self.store.rename_job(job, msg('value.job_title.subscription', name=raw('Qq')))),
                ]
                for name, step in steps:
                    step()
                    self.assertEqual(options_bytes(), before, name)
                    data = self.raw_job(job)[1]
                    present = set(data['errors']) | set(data['last_reason'].values())
                    kept = [text for text in sidecar if text in present]
                    self.assertEqual(len(kept), 2, name)
                    for text in kept:
                        self.assertEqual(entries().get(text), sidecar[text], f'{name}: the entry of an unchanged text')
                # extend_job changes the options on purpose (its own read_options / stored_options pair),
                # so its check is that the format bytes stay and only the links change.
                self.store.extend_job(job, [C])
                after = self.raw_job(job)[1]['options']
                self.assertEqual((after['mode'], after['mode_v2']), (json.loads(before)['mode'], mode))
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
        name, data = self.raw_subscription(untitled)
        self.assertEqual((name, data['msgs']), ('未命名订阅', {'未命名订阅': {'k': 'value.subscription.untitled'}}))
        name, data = self.raw_subscription(typed)
        self.assertEqual((name, data['msgs']), ('未命名订阅', {'未命名订阅': None}), 'the owner typed it: raw (E2)')
        name, data = self.raw_subscription(named)
        self.assertEqual(name, 'My list')
        self.assertNotIn('msgs', data)
        self.assertEqual(json.dumps(data, ensure_ascii=False), json.dumps(
            dict(options=dict(self.options(), proxy=''), media_only=True, first_new=True, initialized=False,
                 last_check=None, last_error='', last_new=0), ensure_ascii=False), 'the 1.7.0 bytes')
        rows = {row['id']: row for row in self.store.subscriptions()}
        self.assertMessage(rows[untitled]['name'], 'value.subscription.untitled')
        self.assertIs(type(rows[typed]['name']), Raw)
        self.assertEqual((en(rows[untitled]['name']), en(rows[typed]['name'])), ('Untitled subscription', '未命名订阅'))
        self.assertIs(type(rows[named]['name']), str)
        self.assertNotIn('msgs', rows[untitled]['data'])

    def test_last_error_clauses_and_the_entries_of_a_check(self):
        key = self.store.subscription('', self.options())
        joined = messages.clauses([msg('core.collect.needs_chrome'), msg('core.collect.http_status', status=503)])
        self.store.finish_subscription(key, [], False, joined)
        name, data = self.raw_subscription(key)
        self.assertEqual(data['last_error'], '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；网站返回 HTTP 503，可尝试动态模式或稍后重试。')
        self.assertEqual(data['msgs'][data['last_error']], {'k': 'common.clauses', 'p': {'parts': [
            {'k': 'core.collect.needs_chrome'}, {'k': 'core.collect.http_status', 'p': {'status': 503}}]}})
        self.assertIn(name, data['msgs'], 'the column entry survives the write (columns=)')
        row = self.store.subscriptions()[0]
        self.assertMessage(row['data']['last_error'], 'common.clauses')
        self.assertEnglish(row['data']['last_error'])
        self.store.finish_subscription(key, [], False)
        data = self.raw_subscription(key)[1]
        self.assertEqual(data['last_error'], '检查中断；下次重试，不更新已见记录')
        self.assertEqual(set(data['msgs']), {'未命名订阅', data['last_error']}, 'the old last_error entry is pruned')
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'),
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE},
                          {'url': A, 'title': raw('Song A'), 'kind': '视频页面', 'source': PAGE},
                          {'url': B, 'title': raw('Web'), 'kind': '网页链接', 'source': PAGE}]}
        self.assertEqual(self.store.finish_subscription(key, [page], True), 2)
        data = self.raw_subscription(key)[1]
        self.assertEqual(data['last_error'], '')
        self.assertEqual(set(data['msgs']), {'未命名订阅'})
        entries = self.raw_entries(key)
        self.assertEqual(entries[0]['msgs'], {'媒体直链': {'k': 'value.link_kind.direct'}, '网页': {'k': 'value.page_title.fallback'}})
        self.assertEqual(entries[1]['msgs'], {'网页': {'k': 'value.page_title.fallback'}})
        pending = self.store.pending(key)
        self.assertMessage(pending[0]['title'], 'value.link_kind.direct')
        self.assertMessage(pending[1]['page_title'], 'value.page_title.fallback')
        self.assertIs(type(pending[1]['title']), str)
        self.assertNotIn('msgs', pending[0])
        self.assertEnglish(pending[0]['title'], 'Direct link')

    def test_a_legacy_last_error_with_a_semicolon_inside_a_clause(self):
        key = self.store.subscription('Qq', self.options())
        legacy = '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；网站返回 HTTP 503，可尝试动态模式或稍后重试。'
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM subscriptions').fetchone()[0])
            data['last_error'] = legacy
            db.execute('UPDATE subscriptions SET data=?', (json.dumps(data, ensure_ascii=False),))
        shown = self.store.subscriptions()[0]['data']['last_error']
        self.assertMessage(shown, 'common.clauses', 'legacy')
        self.assertEnglish(shown)
        self.assertEqual([part.key for part in shown.params['parts']], ['core.collect.needs_chrome', 'core.collect.http_status'])


# ============================================================================ archive (contexts -> Archive -> files)

class ArchiveTests(StoreCase):
    def collected(self):
        job = self.collect_job()
        page = {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': 'Body', 'markdown': 'Body',
                'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE}],
                'next': None, '_checkpoint': {'visited': [PAGE], 'todo': []}}
        self.store.collection_event(job, 'collect_page', page)
        return job

    def test_contexts_archive_record_and_files(self):
        job = self.collected()
        context = self.store.contexts([MEDIA])[MEDIA]
        self.assertMessage(context['title'], 'value.link_kind.direct')
        self.assertEqual((context['job_id'], context['source'], context['page_url']), (job, PAGE, PAGE))
        page = self.store.job(job)['pages'][0]
        for lang, first, readme in (('zh', '网页', '媒体直链'), ('en', 'Web page', 'Direct link')):
            with self.subTest(lang=lang), _sandbox.language(lang):
                archive = Archive(self.root / f'archive-{lang}', self.store)
                folder = archive.prepare(MEDIA, dict(context, page=page))
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual((record['title'], record['page_title']), ('媒体直链', '网页'))
                self.assertEqual(record['msgs'], {'媒体直链': {'k': 'value.link_kind.direct'},
                                                  '网页': {'k': 'value.page_title.fallback'}})
                self.assertEqual((folder / '网页正文.txt').read_text(encoding='utf-8-sig').splitlines()[0], first)
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], readme)
                indexed = next(item for item in self.store.archives() if item['folder'] == str(folder))
                self.assertMessage(indexed['title'], 'value.link_kind.direct')
                self.assertNotIn('msgs', indexed)
                # Archive.media: the media's own title (raw) replaces the app-made one; its entry goes.
                clip = folder / 'clip.mp4'
                clip.write_bytes(b'x')
                archive.media(folder, {'filepath': str(clip), 'title': 'Clip', 'uploader': 'Qq'})
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual(record['title'], 'Clip')
                self.assertEqual(record['msgs'], {'网页': {'k': 'value.page_title.fallback'}})
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], 'Clip')
                transcript = self.root / f'notes-{lang}.txt'
                transcript.write_text('notes', encoding='utf-8')
                archive.attach(folder, transcript)
                again = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertEqual(again['msgs'], record['msgs'])
                self.assertEqual(len(again['transcripts']), 1)
                # A media literally titled 网页 is raw (null entry); the page title's message wins (L-5).
                with _sandbox.fixture_texts('a media literally titled 网页: user data equal to an app text (L-11)'):
                    archive.media(folder, {'filepath': str(clip), 'title': '网页'})
                self.assertEqual(json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))['msgs'],
                                 {'网页': {'k': 'value.page_title.fallback'}})

    def test_plain_titles_are_written_like_stored_text(self):
        """A caller that passes plain (keyless) titles gets the 1.7.0 file text: decoded, then shown."""
        for lang, expected in (('zh', ('网页', '动态页面媒体请求')), ('en', ('Web page', 'Page request'))):
            with self.subTest(lang=lang), _sandbox.language(lang):
                archive = Archive(self.root / f'plain-{lang}')
                folder = archive.prepare(MEDIA, {'source': PAGE, 'title': '动态页面媒体请求',
                                                 'page': {'url': PAGE, 'title': '网页', 'text': 'B', 'markdown': 'B'}})
                record = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
                self.assertNotIn('msgs', record, 'plain text is never given an entry (E3)')
                self.assertEqual((folder / '网页正文.txt').read_text(encoding='utf-8-sig').splitlines()[0], expected[0])
                self.assertEqual((folder / '阅读说明.txt').read_text(encoding='utf-8-sig').splitlines()[0], expected[1])

    def test_an_unreadable_record_is_set_aside_with_a_message(self):
        archive = Archive(self.root / 'aside')
        folder = archive.prepare(MEDIA, {'source': PAGE})
        record = folder / '资料信息.json'
        with mock.patch('library.os.rename', side_effect=FileExistsError), self.assertRaises(OSError) as caught:
            Archive._set_aside(record)
        self.assertMessage(caught.exception.args[0], 'core.archive.cannot_set_aside', 'live')
        self.assertEqual(str(caught.exception), f'无法保留损坏的资料信息文件：{record}')
        with self.assertRaises(ValueError) as caught:
            archive.save(self.root, {})
        self.assertMessage(caught.exception.args[0], 'core.archive.outside_root', 'live')


# ============================================================================ foreign entries (I11)

FOREIGN = {
    '找不到任务记录。': {'k': 'core.future.unknown', 'p': {'a': [1, 2], 'b': 'x'}},
    '网站返回 HTTP 7，可尝试动态模式或稍后重试。': {'k': 'core.collect.http_status', 'p': {'status': 7, 'extra': 1}},
    '历史记录本次不保存：新': {'k': 'core.library.temporary', 'p': {'error': {'k': 'core.future.inner'}}},
}


class ForeignEntryTests(StoreCase):
    def check(self, sidecar, texts=FOREIGN):
        for text in texts:
            self.assertEqual(compact(sidecar[text]), compact(FOREIGN[text]), text)

    def test_unknown_entries_survive_every_write(self):
        job = self.download_job()
        texts = list(FOREIGN)
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
            data['errors'] = texts
            data['last_reason'] = {'error': texts[-1], 'reason': texts[0]}
            data['msgs'] = dict(data['msgs'], **FOREIGN)
            db.execute('UPDATE jobs SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), job))
        record = self.store.job(job)['data']
        for item in record['errors']:
            self.assertEqual(item.origin, 'foreign')
            self.assertIsInstance(en(item), str)                      # rendered, never an exception
        self.assertEqual(en(record['errors'][0]), 'Task record not found.', 'the lift renders it')
        self.assertEqual(en(record['errors'][1]), 'The site returned HTTP 7. Try dynamic mode or try again later.')
        self.assertEqual(en(record['errors'][2]), 'History is not saved in this session: 新')
        self.store.change_job(job, status='partial')
        self.check(self.raw_job(job)[1]['msgs'])
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large')})
        self.check(self.raw_job(job)[1]['msgs'])
        # Subscription: the name column keeps its foreign entry through finish_subscription.
        key = self.store.subscription('x', {'urls': [PAGE]})
        with opened(self.store.path) as db:
            data = json.loads(db.execute('SELECT data FROM subscriptions').fetchone()[0])
            data['msgs'] = {'找不到任务记录。': FOREIGN['找不到任务记录。']}
            db.execute('UPDATE subscriptions SET name=?, data=?', ('找不到任务记录。', json.dumps(data, ensure_ascii=False)))
        self.store.finish_subscription(key, [], False, msg('core.collect.too_large'))
        self.store.finish_subscription(key, [{'url': PAGE, 'title': raw('P'), 'links': []}], True)
        self.check(self.raw_subscription(key)[1]['msgs'], ['找不到任务记录。'])
        self.assertEqual(self.store.subscriptions()[0]['name'].origin, 'foreign')
        # Archive.media: the page title's foreign entry survives while the title changes.
        archive = Archive(self.root / 'archive')
        folder = archive.prepare(MEDIA, {'source': PAGE})
        path = folder / '资料信息.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data.update(page_title='找不到任务记录。', msgs={'找不到任务记录。': FOREIGN['找不到任务记录。']})
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        clip = folder / 'c.mp4'
        clip.write_bytes(b'x')
        archive.media(folder, {'filepath': str(clip), 'title': 'Clip'})
        self.check(json.loads(path.read_text(encoding='utf-8'))['msgs'], ['找不到任务记录。'])


# ============================================================================ limits

class LimitTests(StoreCase):
    def test_storable_repairs_lone_surrogates_and_keeps_types(self):
        job = self.download_job()
        broken = msg('core.engine.error', error=raw('bad \ud800 text'))
        self.store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': broken,
                                                     'reason': raw('r \udc80')})
        self.store.download_event(job, 'title', 'Song \ud83d')
        title, data, _text = self.raw_job(job)
        self.assertEqual(data['errors'], ['错误：bad ? text'])
        self.assertEqual(data['msgs']['错误：bad ? text'], {'k': 'core.engine.error', 'p': {'error': 'bad ? text'}})
        self.assertEqual(title, 'Song ? 等 2 条')
        self.assertEqual(data['msgs'][title], {'k': 'value.job_title.multi', 'p': {'title': 'Song ?', 'count': 2}})
        self.assertEqual(data['last_reason']['reason'], 'r ?')
        repaired = library._storable({'e': broken, 'r': raw('\ud800'), 'v': value('mode', '\ud800'), 'p': '\ud800'})
        self.assertEqual((type(repaired['e']), repaired['e'].key, repaired['e'].origin), (Message, 'core.engine.error', 'live'))
        self.assertIs(type(repaired['e'].params['error']), Raw)
        self.assertEqual((type(repaired['r']), type(repaired['v']), type(repaired['p'])), (Raw, Value, str))
        self.assertEqual(repaired['v'].group, 'mode')
        same = msg('core.collect.too_large')
        self.assertIs(library._storable(same), same)

    def test_an_entry_over_2_kb_is_skipped(self):
        job = self.collect_job()
        big = msg('core.library.temporary', error=raw('x' * 3000))
        self.store.collection_event(job, 'collect_error', big)
        data = self.raw_job(job)[1]
        self.assertEqual(data['errors'], [plain(big)])
        self.assertNotIn('msgs', data)
        back = self.store.job(job)['data']['errors'][0]
        self.assertMessage(back, 'core.library.temporary', 'legacy')

    def test_a_2000_link_page_has_no_null_entries_for_ordinary_titles(self):
        job = self.collect_job()
        links = [{'url': f'https://example.invalid/v{n}', 'title': raw(f'Video number {n}'), 'kind': '视频页面',
                  'source': PAGE} for n in range(collector.MAX_LINKS - 2)]
        links += [{'url': MEDIA + str(n), 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE}
                  for n in range(2)]
        page = {'url': PAGE, 'title': raw('Example list'), 'text': '', 'markdown': '', 'links': links, 'next': None,
                '_checkpoint': {'visited': [PAGE], 'todo': []}}
        started = time.perf_counter()
        self.store.collection_event(job, 'collect_page', page)
        elapsed = time.perf_counter() - started
        stored = self.raw_pages(job)[0]
        self.assertEqual(stored['msgs'], {'媒体直链': {'k': 'value.link_kind.direct'}}, 'one entry for 2 links, no null')
        self.assertEqual(len(stored['links']), collector.MAX_LINKS)
        self.assertLess(elapsed, 5.0)
        read = self.store.job(job)['pages'][0]
        self.assertEqual(sum(isinstance(link['title'], Message) for link in read['links']), 2)
        self.assertEqual(self.store.contexts([MEDIA + '1'])[MEDIA + '1']['title'].key, 'value.link_kind.direct')


# ============================================================================ invariants

def schema_of_1_7_0():
    tree = ast.parse((OLD_170 / 'library.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'SCHEMA' for target in node.targets):
            return node.value.value
    raise AssertionError('SCHEMA not found in release-sources/1.7.0/src/library.py')


def master(path):
    with opened(path) as db:
        return (sorted(db.execute('SELECT type, name, tbl_name, sql FROM sqlite_master')),
                db.execute('PRAGMA user_version').fetchone()[0])


def stored_objects(path, archive_roots=()):
    """(where, object) of every JSON object 1.8.0 stores in a library and its archive folders."""
    found = []
    with opened(path) as db:
        for table, column in (('jobs', 'data'), ('pages', 'data'), ('subscriptions', 'data'), ('entries', 'data'),
                              ('archives', 'data')):
            for (text,) in db.execute(f'SELECT {column} FROM {table}'):
                found.append((table, json.loads(text)))
    for root in archive_roots:
        for record in Path(root).rglob('资料信息.json'):
            found.append(('资料信息.json', json.loads(record.read_text(encoding='utf-8'))))
    return found


def walk(item, path=()):
    yield path, item
    if isinstance(item, dict):
        for key, child in item.items():
            yield from walk(child, path + (key,))
    elif isinstance(item, list):
        for index, child in enumerate(item):
            yield from walk(child, path + (index,))


class InvariantTests(StoreCase):
    def scenario(self):
        """Every writer once, with messages, raw and plain texts."""
        download, collect = self.download_job(mode=formats.MODES[5]), self.collect_job()
        self.store.download_event(download, 'item', {'url': A})
        self.store.download_event(download, 'title', 'Song')
        self.store.download_event(download, 'item_done', {'success': False, 'url': A, 'error': msg('core.collect.too_large'),
                                                          'reason': msg('core.reason.network')})
        self.store.download_event(download, 'done', {'cancelled': False, 'failed': 1,
                                                     'archive_errors': [plain(msg('core.archive.media_mismatch'))]})
        self.store.extend_job(download, [C])
        self.store.collection_event(collect, 'collect_page', {
            'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': '', 'markdown': '', 'next': None,
            'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE}],
            '_checkpoint': {'visited': [PAGE], 'todo': [{'url': B, 'depth': 0}]}})
        self.store.collection_event(collect, 'collect_error', msg('core.collect.too_large'))
        self.store.change_job(collect, status='partial')
        key = self.store.subscription('', {'urls': [PAGE]})
        self.store.finish_subscription(key, self.store.job(collect)['pages'], True)
        self.store.finish_subscription(key, [], False, msg('core.collect.needs_chrome'))
        archive = Archive(self.root / 'archive', self.store)
        folder = archive.prepare(MEDIA, dict(self.store.contexts([MEDIA])[MEDIA], page=self.store.job(collect)['pages'][0]))
        return download, collect, key, folder

    def test_i2_schema_and_user_version_unchanged(self):
        self.scenario()
        reference = self.root / 'reference.sqlite3'
        with opened(reference) as db:
            db.executescript(schema_of_1_7_0())
        self.assertEqual(master(self.store.path), master(reference))
        self.assertEqual(master(self.store.path)[1], 0)
        self.assertEqual(library.SCHEMA, schema_of_1_7_0())

    def test_i3_sidecars_only_where_allowed(self):
        self.scenario()
        objects = stored_objects(self.store.path, [self.root / 'archive'])
        with_sidecar = 0
        for where, item in objects:
            for path, node in walk(item):
                if path and path[-1] == 'msgs':
                    self.assertEqual(len(path), 1, f'{where}: msgs only at the top of the object, found at {path}')
                    with_sidecar += 1
                    self.assertIsInstance(node, dict)
                if path and path[0] in ('options', 'checkpoint', '_checkpoint', 'result'):
                    self.assertNotEqual(path[-1], 'msgs', f'{where}: {path}')
            if where == 'archives':
                self.assertNotIn('folder', item)
        self.assertGreaterEqual(with_sidecar, 6)

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
        error = Library(history).job(job)['data']['errors'][0]
        self.assertIn(messages.PROXY_TOKEN, error)
        self.assertEqual(error, 'curl: (7) Failed to connect to [代理] via [代理]')
        self.assertEqual(en(error), 'curl: (7) Failed to connect to [proxy] via [proxy]')
        # A message param masked by its producer (P8) reaches the sidecar masked.
        masked = messages.masked(msg('core.library.temporary', error=raw(f'proxy {secret} refused')),
                                 [(secret, messages.PROXY_TOKEN)])
        self.store.collection_event(self.collect_job(), 'collect_error', masked)
        self.assertNotIn(b'hunter2secret', Path(self.store.path).read_bytes())
        self.assertEqual(Library(self.store.path).job(self.store.history()[0]['id'])['data']['errors'][0].params['error'],
                         'proxy [代理] refused')


I8_CHILD = '''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import library
db, root, out = sys.argv[2], sys.argv[3], sys.argv[4]
A, B, PAGE, MEDIA = 'https://example.invalid/a', 'https://example.invalid/b', 'https://example.invalid/list', 'https://cdn.example.invalid/clip.mp4'
store = library.Library(db)
job = store.create_job('download', '2 条 · 音频 · 原始最佳', {'urls': [A, B], 'mode': '音频 · M4A 原生', 'output': root})
store.download_event(job, 'item', {'url': A})
store.download_event(job, 'item_done', {'success': False, 'url': A, 'error': '此链接无法开始：找不到 cookies 文件：C:\\\\x\\\\c.txt', 'reason': '找不到 cookies 文件'})
store.download_event(job, 'done', {'cancelled': False, 'failed': 1})
collect = store.create_job('collect', PAGE, {'urls': [PAGE], 'dynamic': False, 'scrolls': 0, 'selector': '', 'max_pages': 1})
page = {'url': PAGE, 'title': '网页', 'text': 'Body', 'markdown': 'Body', 'next': None,
        'links': [{'url': MEDIA, 'title': '媒体直链', 'kind': '媒体地址', 'source': PAGE}]}
store.collection_event(collect, 'collect_page', dict(page, _checkpoint={'visited': [PAGE], 'todo': []}))
store.collection_event(collect, 'collect_error', '页面超过 8 MB，请缩小采集范围。')
store.change_job(collect, status='partial')
sub = store.subscription('', {'urls': [PAGE]})
store.finish_subscription(sub, [page], True)
store.finish_subscription(sub, [], False, '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；网站返回 HTTP 503，可尝试动态模式或稍后重试。')
archive = library.Archive(os.path.join(root, 'archive'), store)
archive.prepare(MEDIA, {'source': PAGE, 'title': '媒体直链', 'page': page, 'job_id': collect, 'page_url': PAGE})
running = store.create_job('download', '1 条 · 音频 · FLAC', {'urls': [A], 'mode': '音频 · FLAC', 'output': root})
with open(out, 'w', encoding='utf-8') as handle:
    json.dump({'running': running, 'jobs': [job, collect, running], 'subscription': sub}, handle)
'''


I8_STATE_CHILD = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import paths
state = Path(sys.argv[2])
paths.STATE_DIR = state
import engine, settings, unfinished
A, B = 'https://example.invalid/a', 'https://example.invalid/b'
settings.save(dict(settings.load(), mode='音频 · FLAC', proxy='', check_engine_updates=False))
options = engine.Options(state / 'out').to_dict()
results = [{'url': A, 'key': 'ka', 'format': 'audio_flac', 'options': options, 'outcome': 'failed', 'title': 'Song A',
            'error': '此链接无法开始：找不到 cookies 文件：C:\\\\x\\\\c.txt', 'referer': ''},
           {'url': B, 'key': 'kb', 'format': 'audio_flac', 'options': options, 'outcome': 'blocked', 'title': '',
            'error': '网站要求验证身份，请更换节点或稍后重试。', 'referer': ''}]
with unfinished.LOCK:
    unfinished.save(unfinished.merge(unfinished.load_for_merge(), results))
(state / 'failed-downloads.bad-20261117-120000.json').write_text('{"version": 2, "entries": [', encoding='utf-8')
(state / 'failed-downloads.overflow-20261117-120000.json').write_bytes(paths.failed_file().read_bytes())
'''


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


class UpgradeReadTests(StoreCase):
    def test_i8_reading_a_1_7_0_library_writes_nothing(self):
        folder = self.root / 'old'
        folder.mkdir()
        script = folder / 'write_1_7_0.py'
        script.write_text(I8_CHILD, encoding='utf-8')
        db, out = folder / 'library.sqlite3', folder / 'ids.json'
        subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(OLD_170), str(db), str(folder), str(out)],
                       check=True, timeout=120, cwd=str(folder), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        ids = json.loads(out.read_text(encoding='utf-8'))
        files_before = tree_hashes(folder / 'archive')
        rows_before = table_dump(db)
        store = Library(db)
        rows = store.history()
        self.assertEqual({row['id'] for row in rows}, set(ids['jobs']))
        for job in ids['jobs']:
            store.job(job)
        store.history_count()
        store.history(search='Song')
        subscriptions = store.subscriptions()
        store.pending(ids['subscription'])
        archives = store.archives()
        contexts = store.contexts(['https://cdn.example.invalid/clip.mp4'])
        store.find_download(['k'], 'audio_best')
        self.assertEqual(tree_hashes(folder / 'archive'), files_before)
        rows_after = table_dump(db)
        for table in rows_before:
            if table != 'jobs':
                self.assertEqual(rows_after[table], rows_before[table], table)
        before = {row[0]: row for row in rows_before['jobs']}
        after = {row[0]: row for row in rows_after['jobs']}
        for job, row in before.items():
            if job == ids['running']:
                # history() marks a running job of a gone process interrupted (the 1.7.0 behaviour).
                self.assertEqual((after[job][3], after[job][5]), ('interrupted', row[5]), 'status only; data bytes kept')
            else:
                self.assertEqual(after[job], row)
        # Every text of the old rows is decoded; in en nothing Chinese is left of the app texts.
        shown = [store.job(job) for job in ids['jobs']]
        self.assertMessage(shown[1]['pages'][0]['title'], 'value.page_title.fallback', 'legacy')
        self.assertMessage(shown[1]['data']['errors'][0], 'core.collect.too_large', 'legacy')
        self.assertMessage(subscriptions[0]['name'], 'value.subscription.untitled', 'legacy')
        self.assertMessage(subscriptions[0]['data']['last_error'], 'common.clauses', 'legacy')
        self.assertMessage(archives[0]['title'], 'value.link_kind.direct', 'legacy')
        self.assertMessage(contexts['https://cdn.example.invalid/clip.mp4']['title'], 'value.link_kind.direct', 'legacy')
        for item in (shown[1]['pages'][0]['title'], shown[1]['data']['errors'][0], subscriptions[0]['name'],
                     subscriptions[0]['data']['last_error'], archives[0]['title'], shown[0]['title']):
            self.assertEnglish(item)

    def test_i8_reading_1_7_0_settings_and_failed_list_writes_nothing(self):
        """(final review, RV-FD1-1; MESSAGES.md 4.7 I8) settings.json, failed-downloads.json and a bad
        and an overflow file written by the frozen 1.7.0 code keep every byte when 1.8.0 reads them:
        unfinished.load, settings.load, i18n.stored_choice and the 1.5.1 view engine.load_failed_job."""
        import engine
        import settings
        import unfinished
        state = self.root / 'state'
        state.mkdir()
        script = self.root / 'write_state_1_7_0.py'
        script.write_text(I8_STATE_CHILD, encoding='utf-8')
        subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(OLD_170), str(state)], check=True,
                       timeout=120, cwd=str(self.root), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        names = sorted(path.name for path in state.iterdir())
        self.assertEqual(names, ['failed-downloads.bad-20261117-120000.json', 'failed-downloads.json',
                                 'failed-downloads.overflow-20261117-120000.json', 'settings.json'])
        before = tree_hashes(state)
        with _sandbox.state_patch(state):
            entries = unfinished.load()
            prefs = settings.load()
            choice = i18n.stored_choice(prefs)
            legacy = engine.load_failed_job()
            again = unfinished.load()
        self.assertEqual(tree_hashes(state), before, 'reading writes nothing (I8)')
        self.assertEqual(sorted(path.name for path in state.iterdir()), names)
        self.assertEqual([item['url'] for item in entries], ['https://example.invalid/a', 'https://example.invalid/b'])
        self.assertEqual([item['error'] for item in again], [item['error'] for item in entries])
        self.assertMessage(entries[0]['error'], 'core.engine.cannot_start_link', 'legacy')
        self.assertEqual(prefs['mode'], '音频 · FLAC')
        self.assertIn(choice, i18n.CHOICES)
        self.assertIsNotNone(legacy)


# ============================================================================ collector IPC (2.8)

class IpcTests(StoreCase):
    def fake_collect(self, options, emit):
        emit('collect_status', msg('core.collect.page_n', page=1))
        emit('collect_page', {'url': PAGE, 'title': msg('value.page_title.fallback'), 'text': 'x', 'markdown': 'x',
                              'links': [{'url': MEDIA, 'title': msg('value.link_kind.direct'), 'kind': '媒体地址', 'source': PAGE},
                                        {'url': A, 'title': raw('Song A'), 'kind': '视频页面', 'source': PAGE},
                                        {'url': B, 'title': raw('Web'), 'kind': '网页链接', 'source': PAGE}],
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
        self.assertIn('"$m"', lines[0].decode('utf-8'))
        received = []
        runner = collector.CollectionRunner(lambda kind, value_: received.append((kind, value_)))
        malformed = [b'not json', b'["collect_error", {"$m": [1]}]', b'["collect_page", {"$r": 5}]',
                     b'["collect_status", {"$v": ["mode"]}]', b'[1]']
        for line in malformed[:2] + lines[:2] + malformed[2:] + lines[2:]:
            runner._forward(line)
        self.assertEqual([kind for kind, _value in received],
                         ['collect_status', 'collect_page', 'collect_error', 'collect_error', 'collect_checkpoint'])
        status, page, nested, plain_error = (value_ for _kind, value_ in received[:4])
        self.assertMessage(status, 'core.collect.page_n', 'live')
        self.assertEqual(plain(status), '正在采集第 1 页…')
        self.assertMessage(page['title'], 'value.page_title.fallback', 'live')
        self.assertMessage(page['links'][0]['title'], 'value.link_kind.direct', 'live')
        self.assertIs(type(page['links'][1]['title']), Raw)
        self.assertIs(type(page['links'][2]['title']), Raw)
        self.assertMessage(nested, 'core.library.temporary', 'live')
        self.assertEqual(nested.params['error'].key, 'core.collect.http_status')
        self.assertEqual(nested.params['error'].params['status'], 503)
        self.assertIs(type(plain_error), str)
        self.assertEnglish(nested, 'History is not saved in this session: The site returned HTTP 503. Try dynamic mode or try again later.')
        # The worker's own store write received the in-memory values (I9, same codec).
        with opened(history) as db:
            row = json.loads(db.execute('SELECT data FROM pages').fetchone()[0])
            data = json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])
        self.assertEqual(row['msgs'], {'网页': {'k': 'value.page_title.fallback'}, '媒体直链': {'k': 'value.link_kind.direct'}})
        self.assertEqual(data['msgs'][plain(nested)], {'k': 'core.library.temporary', 'p': {
            'error': {'k': 'core.collect.http_status', 'p': {'status': 503}}}})
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
        self.assertMessage(titled['links'][0]['title'], 'value.link_kind.dynamic_request', 'live')
        self.assertEqual(titled['links'][0]['kind'], '媒体地址')
        self.assertEqual((type(untitled['title']), untitled['title']), (Raw, 'example.invalid'), 'the host name')
        self.assertMessage(fallback['title'], 'value.page_title.fallback', 'live')
        self.assertEqual(plain(fallback['title']), '网页')


# ============================================================================ updater

class UpdaterTests(unittest.TestCase):
    def test_update_errors_keep_the_templates_and_carry_keys(self):
        import urllib.error
        cases = [
            (urllib.error.URLError(TimeoutError()), 'core.update.no_github', updater.MSG_NETWORK.format(reason='连接超时')),
            (urllib.error.HTTPError(updater.RELEASE_URL, 503, 'x', {}, None), 'core.update.no_github',
             updater.MSG_NETWORK.format(reason='HTTP 503')),
            (ValueError('not json'), 'core.update.bad_release', updater.MSG_RELEASE),
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
                (timeout, 'core.update.start_timeout', updater.MSG_START.format(reason='60 秒内没有结束')),
                (OSError('no'), 'core.update.start_cannot', updater.MSG_START.format(reason='无法启动')),
                ((3, b'', b''), 'core.update.start_exit', updater.MSG_START.format(reason='退出码 3')),
                ((0, b'hello', b''), 'core.update.start_no_version', updater.MSG_START.format(reason='没有给出版本号'))):
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
        self.assertEqual(str(caught.exception), updater.MSG_RESTORE)
        self.assertEqual(caught.exception.args[0].key, 'core.update.restore_failed')
        self.assertEqual(updater.BUILTIN, '已内置')
        self.assertEqual(updater.BUILTIN.key, 'core.update.builtin')
        with mock.patch.object(updater, 'engine_path', side_effect=OSError):
            self.assertIs(updater.engine_version(), updater.BUILTIN)
        events = []
        engine = updater.EngineUpdater(lambda kind, item: events.append((kind, item)), mock.MagicMock(is_set=lambda: False))
        with mock.patch.object(updater, 'check_latest', side_effect=updater.UpdateError(msg('core.update.no_exe'))):
            engine.run()
        self.assertEqual([kind for kind, _item in events], ['update_status', 'update_error', 'update_done'])
        self.assertEqual(events[0][1].key, 'core.update.checking')
        self.assertEqual((events[1][1].key, events[1][1]), ('core.update.no_exe', updater.MSG_NO_ASSET))
        legacy = []
        engine = updater.EngineUpdater(lambda kind, item: legacy.append((kind, item)), mock.MagicMock(is_set=lambda: False),
                                       legacy_events=True)
        with mock.patch.object(updater, 'check_latest', side_effect=RuntimeError('boom')):
            engine.run()
        self.assertEqual(legacy[0], ('title', '正在检查官方更新…'))
        self.assertEqual(legacy[0][1].key, 'core.update.checking')
        self.assertEqual(legacy[1], ('update_error', 'boom'))
        self.assertIs(type(legacy[1][1]), str)
        if messages.GUARD:
            self.assertTrue(hasattr(engine.emit, '__wrapped__'), 'the G2 emit hook (3.3)')
            self.assertTrue(hasattr(collector.CollectionRunner(lambda *_: None).emit, '__wrapped__'))


# ============================================================================ G4: the store channels

def sample_params(key):
    params = {}
    for name in sorted({field for _l, field, _s, _c in FORMATTER.parse(i18n.CATALOG['zh'][key]) if field}):
        if name in NUMERIC:
            params[name] = 7
        elif name == 'mode':
            params[name] = value('mode', formats.MODES[1])
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
                    {'url': MEDIA, 'title': message, 'kind': '媒体地址', 'source': PAGE}], '_checkpoint': None})
                self.store.finish_subscription(subscription, [], False, messages.clauses([message, msg('core.collect.too_large')]))
                self.store.rename_job(collect, message)
                record, collected = self.store.job(job), self.store.job(collect)
                subscription_row = self.store.subscriptions()[0]
                values = [record['data']['errors'][-1], record['data']['last_reason']['reason'], collected['title'],
                          collected['pages'][0]['title'], collected['pages'][0]['links'][0]['title'],
                          subscription_row['data']['last_error']]
                for item in values:
                    self.assertIsInstance(item, Message)
                    self.assertEqual(item.origin, 'stored')
                    shown = en(item)
                    self.assertEqual(CJK.findall(shown), [], f'{key}: {shown}')
                    self.assertEqual(zh(item), plain(item))
                    checked += 1
                self.assertEqual(values[0].key, key)
                self.assertEqual(plain(values[0]), plain(message))
        self.assertEqual(checked, 6 * len(keys))


if __name__ == '__main__':
    unittest.main()
