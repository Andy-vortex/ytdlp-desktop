"""1.8.0 message model, store codec, legacy reader and G2 self-tests (MESSAGES.md 2, 4, 7.3; WP-1).

    python -X utf8 -B test_messages.py

The carrier rules of 2.3 for Message, Raw and Value; show() / error_text() in zh and en; the derived
texts (cut, masked, clauses, stripped, message_of); the collector wire form; the sidecar codec
(build, prune, collision, cap, surrogates, foreign entries kept byte for byte); the key registry;
the legacy reader L1-L9 (moved here from test_i18n_catalog's LocalizeTests; WP-6 deleted the
live-localize variants together with localize, MESSAGES.md 6.4); zh catalog parity with release-sources/1.7.0
(with the sanctioned 1.8.1 differences CHANGED_1_8_1 / ADDED_1_8_1: the owner asked for the two subscription
notes to name 忽略, an exception to MESSAGES.md 8.1, and the subscription management added 13 library keys); the
frozen data regeneration; and the G2 self-tests of 7.3 (they need YTDLP_DESKTOP_MSG_GUARD, which
run_tests.py sets).  No window, no network.
"""
import _sandbox
import ast
import copy
import dataclasses
import json
import logging
import os
import pickle
import queue
import re
import sqlite3
import string
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import i18n
import i18n_legacy
import i18n_maps
import i18n_tool
import messages
from messages import Message, Raw, Value, msg, raw, value

FORMATTER = string.Formatter()
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
NEW_KEYS = {
    'core.cookies.line_failed_item': ('cookies 文件中有一行格式不对，无法读取（内容不记录）',
                                      'A line of the cookies file has a wrong format and cannot be read (content not logged)'),
    'core.options.not_object': ('Options data must be an object', 'Options data must be an object'),
    'core.options.no_output': ('Options data has no usable output folder', 'Options data has no usable output folder'),
    'core.options.bad_resolution': ('Unsupported resolution: {resolution}', 'Unsupported resolution: {resolution}'),
    'core.options.unsupported': ('Unsupported output option', 'Unsupported output option'),
    'core.formats.bad_mode': ('Unsupported output format: {mode}', 'Unsupported output format: {mode}'),
    'core.formats.bad_id': ('Unsupported output format id: {id}', 'Unsupported output format id: {id}'),
    'core.formats.bad_label': ('Unsupported output format label: {label}', 'Unsupported output format label: {label}'),
}


# (1.8.1, build/v181/design/DESIGN.md 5.1, 5.2, 6.2) The sanctioned differences from the 1.7.0 catalog: two
# 1.7.0 keys whose wording the owner asked to update once 忽略所选 exists (an exception to MESSAGES.md 8.1
# "the zh of a 1.7.0 key is unchanged"), and the 13 library keys of the subscription management.
CHANGED_1_8_1 = {
    'library.subscription.note': ('新增内容会保留，直到下载成功或被忽略；不会自动下载。',
                                  'New items stay until downloaded or ignored; nothing downloads by itself.'),
    'library.subscription.status_note': ('下载成功或忽略后移出；可多选后添加或忽略。',
                                         'Items leave once downloaded or ignored.'),
}
ADDED_1_8_1 = {
    'library.subscription.apply_name': ('应用名称', 'Apply name'),
    'library.subscription.delete': ('删除订阅', 'Delete'),
    'library.pending.ignore': ('忽略所选', 'Ignore'),
    'library.pending.pick_ignore': ('请先在下方选择要忽略的新增条目。', 'Select the new items to ignore in the list below first.'),
    'library.subscription.name_empty': ('请先在上方名称框输入新名称。', 'Type the new name in the name box above first.'),
    'library.subscription.delete_title': ('删除订阅', 'Delete subscription'),
    'library.subscription.delete_text': (
        '删除订阅“{name}”和它的 {pending} 条待处理内容？已见记录也会一并删除，以后重新订阅时会重新建立基线。'
        '任务历史、已下载的文件和下载记录都会保留。删除后无法恢复。',
        'Delete the subscription “{name}” and its pending items ({pending})? Its record of seen links goes too; '
        'subscribing again starts a new baseline. Task history, downloaded files and download records are kept. '
        'This cannot be undone.'),
    'library.subscription.delete_busy': ('这个订阅正在检查，检查结束后再删除。',
                                         'This subscription is being checked; delete it after the check ends.'),
    'library.subscription.delete_task_busy': ('当前任务结束后才能删除订阅。',
                                              'A subscription can be deleted when the current task ends.'),
    'library.subscription.gone': ('这个订阅已不存在。', 'This subscription no longer exists.'),
    'library.subscription.log_renamed': ('已将订阅改名为：{name}', 'Subscription renamed: {name}'),
    'library.subscription.log_deleted': ('已删除订阅：{name}（待处理内容 {pending} 条）',
                                         'Subscription deleted: {name} · Pending items removed: {pending}'),
    'library.pending.log_ignored': ('已忽略订阅“{name}”的 {count} 条新增内容。',
                                    'New items ignored: {count} · Subscription: {name}'),
}


def fields(text):
    return {field for _literal, field, _spec, _conv in FORMATTER.parse(text) if field is not None}


def en(value_):
    with _sandbox.language('en'):
        return messages.show(value_)


class CustomError(Exception):
    pass


@dataclasses.dataclass
class Holder:
    text: str


# ============================================================================ carrier (2.3)

class CarrierTests(unittest.TestCase):
    def samples(self):
        return [msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=2100, url='youtu.be/x')),
                raw('Some Song 网页'), value('mode', '音频 · MP3 320K')]

    def test_str_format_percent_and_exceptions_return_the_object(self):
        for item in self.samples():
            for result in (str(item), f'{item}', '%s' % item, str(ValueError(item)), str(OSError(item)),
                           str(CustomError(item))):
                self.assertIs(type(result), type(item), type(item).__name__)
                self.assertEqual(result, item)
            self.assertIs(type(f'x{item}'), str)                  # composed in C: plain, key lost
            self.assertEqual(format(item, '>3'), format(messages.zh(item), '>3'))
            self.assertIs(type(format(item, '>3')), str)

    def test_plain_str_behaviour_and_attributes(self):
        m = self.samples()[0]
        text = '此链接无法开始：链接过长（2100 个字符），无法交给下载引擎：youtu.be/x'
        self.assertEqual(m, text)
        self.assertEqual(hash(m), hash(text))
        self.assertIn(m, {text})
        self.assertEqual({m: 1}[text], 1)
        self.assertTrue(re.search('无法开始', m))
        self.assertTrue(m.startswith('此链接'))
        self.assertEqual(len(m), len(text))
        self.assertEqual(list(m), list(text))
        self.assertEqual(m.encode('utf-8'), text.encode('utf-8'))
        self.assertEqual((m.key, m.origin, m.entry), ('core.engine.cannot_start_link', 'live', None))
        self.assertEqual(m.params['error'].params, {'length': 2100, 'url': 'youtu.be/x'})
        with self.assertRaises(TypeError):
            m.params['error'] = 'x'
        for name in ('key', 'origin', 'other'):
            with self.assertRaises(AttributeError):
                setattr(m, name, 'x')
        with self.assertRaises(AttributeError):
            Value('mode', 'x').group = 'y'
        box = queue.Queue()
        box.put(('log', m))
        kind, got = box.get()
        self.assertIs(got, m)

    def test_json_sqlite_tcl_and_logging_write_the_plain_text(self):
        import tkinter
        for item in self.samples():
            self.assertEqual(json.dumps({'a': item}, ensure_ascii=False), json.dumps({'a': str.__str__(item)},
                                                                                     ensure_ascii=False))
            self.assertEqual(json.dumps([item], indent=2, ensure_ascii=False),
                             json.dumps([str.__str__(item)], indent=2, ensure_ascii=False))
            connection = sqlite3.connect(':memory:')
            (stored,), = connection.execute('select ?', (item,)).fetchall()
            connection.close()
            self.assertIs(type(stored), str)
            self.assertEqual(stored, item)
            tcl = tkinter.Tcl()
            tcl.call('set', 'v', item)
            self.assertEqual(tcl.getvar('v'), str.__str__(item))
            logger = logging.getLogger('test.messages.carrier')
            with self.assertLogs(logger, 'WARNING') as logs:
                logger.warning('%s', item)
            self.assertEqual(logs.records[0].getMessage(), str.__str__(item))

    def test_copy_deepcopy_asdict_and_pickle(self):
        for item in self.samples():
            self.assertIs(copy.copy(item), item)
            self.assertIs(copy.deepcopy({'x': [item]})['x'][0], item)
            self.assertIs(dataclasses.asdict(Holder(item))['text'], item)
            back = pickle.loads(pickle.dumps(item))
            self.assertIs(type(back), type(item))
            self.assertEqual(back, item)
        m = pickle.loads(pickle.dumps(self.samples()[0]))
        self.assertEqual((m.key, m.origin), ('core.engine.cannot_start_link', 'live'))
        self.assertEqual(m.params['error'].key, 'core.links.too_long')
        self.assertEqual(pickle.loads(pickle.dumps(self.samples()[2])).group, 'mode')
        foreign = Message('x', None, {}, 'foreign', '{"k":"core.future"}')
        self.assertEqual(pickle.loads(pickle.dumps(foreign)).entry, '{"k":"core.future"}')

    def test_methods_return_plain_str_outside_guarded_modules(self):
        m = self.samples()[0]
        for result in (m[:5], m + 'x', 'x' + m, m * 2, m.strip(), m.replace('此', '彼'), m.split('：')[0], m.lower()):
            self.assertIs(type(result if isinstance(result, str) else str(result)), str)
        self.assertEqual(m[:5], str.__str__(m)[:5])


# ============================================================================ constructors

class ConstructorTests(unittest.TestCase):
    def test_msg_renders_the_zh_catalog_text_with_zh_params(self):
        m = msg('core.engine.waiting', reason=msg('core.wait.pace'), seconds=3)
        self.assertEqual(m, '下载间隔，等待 3 秒；可随时点击停止。')
        self.assertEqual(msg('value.job_title.placeholder', count=2, mode=value('mode', '音频 · FLAC')),
                         '2 条 · 音频 · FLAC')
        self.assertEqual(msg('core.formats.bad_mode', mode=repr('x')), "Unsupported output format: 'x'")

    def test_msg_never_raises_for_a_missing_key_or_placeholder(self):
        logger = logging.getLogger('YtDlpDesktop.i18n')
        with self.assertLogs(logger, 'WARNING'):
            self.assertEqual(msg('core.no.such.key.for.test'), 'core.no.such.key.for.test')
        with self.assertLogs(logger, 'WARNING'):
            self.assertEqual(msg('core.links.too_long', length=3), '链接过长（3 个字符），无法交给下载引擎：{url}')

    def test_value_raw_clauses_and_template(self):
        self.assertIsInstance(value('mode', '音频 · FLAC'), Value)
        self.assertEqual(value('interval', 900), 900)
        self.assertEqual(raw(''), '')
        self.assertIs(type(raw('')), str)
        self.assertIs(type(raw('x')), Raw)
        self.assertEqual(messages.clauses([]), '')
        one = msg('core.collect.too_large')
        self.assertIs(messages.clauses([one]), one)
        joined = messages.clauses([msg('core.collect.needs_chrome'), 'plain'])
        self.assertEqual(joined, '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；plain')
        self.assertEqual(joined.key, 'common.clauses')
        self.assertEqual(messages.template('core.update.no_github'), '无法连接 GitHub，请检查网络后重试。（{reason}）')
        self.assertIs(type(messages.template('core.update.no_github')), str)
        self.assertEqual(messages.zh(None), '')
        self.assertIs(type(messages.zh(one)), str)

    def test_cut_keeps_the_type_and_the_key(self):
        m = msg('core.engine.cannot_start_link', error=raw('x' * 300))
        cut = messages.cut(m, 70)
        self.assertEqual((len(cut), cut.key, cut.params['error']), (70, m.key, 'x' * 300))
        self.assertIs(messages.cut(m, 1000), m)
        self.assertIs(type(messages.cut(raw('y' * 10), 3)), Raw)
        self.assertEqual(messages.cut('plain text', 5), 'plain')
        self.assertEqual(en(cut), en(m), 'a cut message renders whole in en (D12)')

    def test_masked_is_the_1_7_0_replace_on_text_and_raw_params(self):
        proxy = 'http://user:secret@proxy.example:8080'
        m = msg('core.engine.error', error=raw(f'connect to {proxy} failed (user:secret@host)'))
        shown = messages.masked(m, [(proxy, messages.PROXY_TOKEN), ('user:secret@', '')])
        expected = str.__str__(m).replace(proxy, '[代理]').replace('user:secret@', '')
        self.assertEqual(shown, expected)
        self.assertEqual(shown.key, 'core.engine.error')
        self.assertNotIn('secret', shown.params['error'])
        self.assertIs(type(shown.params['error']), Raw)
        self.assertIs(messages.masked(m, [('', 'x')]), m)
        self.assertEqual(messages.masked('a proxy b', [('proxy', '[代理]')]), 'a [代理] b')

    def test_stripped_and_message_of(self):
        m = msg('core.reason.network')
        self.assertIs(messages.stripped(m), m)
        self.assertEqual(messages.stripped('  x  '), 'x')
        self.assertIs(type(messages.stripped(raw(' y '))), Raw)
        self.assertIs(messages.message_of(ValueError(m)), m)
        self.assertEqual(messages.message_of(OSError(2, 'No such file')), '[Errno 2] No such file')
        self.assertEqual(messages.message_of(ValueError(m, 'two args')), str(ValueError(m, 'two args')))


# ============================================================================ rendering

class ShowTests(unittest.TestCase):
    def test_zh_returns_the_value_itself(self):
        m = msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=9, url='u'))
        for item in (m, raw('[代理] x'), 'plain', value('mode', '音频 · FLAC')):
            self.assertEqual(messages.show(item), str.__str__(item))
            self.assertIs(type(messages.show(item)), str)
        self.assertEqual(messages.show(None), '')
        self.assertEqual(messages.show(5), '5')
        # (final review, ZH-2) a stored list value: str(value) in zh, as 1.7.0's localize gave it (2.6)
        parts = ['一行文字', 'x']                 # (no app text: the joined form would be '一行文字；x')
        self.assertEqual(messages.show(parts), str(parts))
        self.assertEqual(messages.error_text(ValueError(parts)), str(parts))

    def test_en_renders_keys_values_and_raw_tokens(self):
        m = msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=9, url='u'))
        self.assertEqual(en(m), 'This link could not start: Link too long for the download engine (9 characters): u')
        self.assertEqual(en(value('mode', '音频 · FLAC')), 'Audio · FLAC')
        self.assertEqual(en(raw('[代理] 无响应')), '[proxy] 无响应')
        self.assertEqual(en('[代理] x'), '[proxy] x')
        self.assertEqual(en(msg('value.job_title.placeholder', count=2, mode=value('mode', '音频 · FLAC'))),
                         'Links: 2 · Audio · FLAC')
        joined = messages.clauses([msg('core.collect.needs_chrome'), msg('core.collect.too_large')])
        self.assertEqual(en(joined), 'Dynamic collection needs Google Chrome; static page mode works without a '
                                     'browser. The page is larger than 8 MB. Narrow the collection scope.')
        self.assertEqual(en(Message('自定义', None, {}, 'foreign', '{"k":"x.y"}')), '自定义')

    def test_error_text_and_never_raises(self):
        self.assertEqual(messages.error_text(ValueError(msg('core.library.no_job'))), '找不到任务记录。')
        with _sandbox.language('en'):
            self.assertEqual(messages.error_text(ValueError(msg('core.library.no_job'))), 'Task record not found.')
            self.assertEqual(messages.error_text(OSError(5, '拒绝访问。')), '[Errno 5] 拒绝访问。')
            broken = Message('zh text', 'core.engine.error', {'error': object()}, 'live')
            with mock.patch.object(messages, '_t', side_effect=RuntimeError('broken')):
                self.assertEqual(messages.show(broken), 'zh text')


# ============================================================================ IPC (2.8)

class WireTests(unittest.TestCase):
    def test_round_trip(self):
        page = {'title': msg('value.link_kind.direct'),
                'links': [{'url': 'u', 'title': raw('Song'), 'kind': '媒体地址'},
                          {'url': 'v', 'title': msg('value.link_kind.dynamic_request'), 'kind': '媒体地址'}],
                'error': messages.clauses([msg('core.collect.too_large'), Message('外来', None, {}, 'foreign', '{"k":"z"}')]),
                'mode': msg('value.job_title.placeholder', count=1, mode=value('mode', '音频 · FLAC'))}
        line = json.dumps(['collect_page', messages.wire(page)], ensure_ascii=False)
        kind, back = json.loads(line)
        back = messages.unwire(back)
        self.assertEqual(back, page)
        self.assertEqual(back['title'].key, 'value.link_kind.direct')
        self.assertIs(type(back['links'][0]['title']), Raw)
        self.assertEqual(back['error'].params['parts'][1].entry, '{"k":"z"}')
        self.assertEqual(back['mode'].params['mode'].group, 'mode')
        self.assertEqual(messages.unwire(messages.wire('plain')), 'plain')

    def test_malformed_markers_raise_value_error(self):
        for bad in ({'$m': 'x'}, {'$m': ['t', 'k', [], 'live']}, {'$m': ['t', 'k', {}, 'nonsense']}, {'$v': ['g']},
                    {'$r': 5}, [{'$v': [1, 'x']}]):
            with self.assertRaises(ValueError):
                messages.unwire(bad)
        self.assertEqual(messages.unwire({'$m': 1, 'other': 2}), {'$m': 1, 'other': 2})


# ============================================================================ store codec (4.3-4.5)

def stored(record):
    """The record as library writes and reads it: JSON text and back."""
    return json.loads(json.dumps(record, ensure_ascii=False))


class CodecTests(unittest.TestCase):
    def job(self):
        error = msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=2100, url='youtu.be/x'))
        return {'errors': [error, 'plain engine text'],
                'last_reason': {'error': error, 'reason': msg('core.reason.http_404')},
                'options': {'urls': ['a', 'b'], 'mode': '音频 · FLAC', 'mode_v2': '音频 · M4A 原生'},
                'checkpoint': {'x': 1}, 'titled': True, 'unknown': [1, 2]}

    def test_build_and_decode(self):
        columns = {'title': msg('value.job_title.multi', title=raw('Song'), count=2), 'kind': 'download'}
        record = messages.encode(self.job(), 'job', columns)
        sidecar = record['msgs']
        self.assertEqual(sidecar['链接不存在（404）'], {'k': 'core.reason.http_404'})
        self.assertEqual(sidecar['Song 等 2 条'], {'k': 'value.job_title.multi', 'p': {'title': 'Song', 'count': 2}})
        self.assertEqual(sidecar[str.__str__(self.job()['errors'][0])]['p']['error'],
                         {'k': 'core.links.too_long', 'p': {'length': 2100, 'url': 'youtu.be/x'}})
        self.assertNotIn('plain engine text', sidecar)
        for name in ('options', 'checkpoint'):
            self.assertNotIn('msgs', record[name])
        back = stored(record)
        decoded = messages.decode(back, 'job')
        self.assertNotIn('msgs', decoded)
        self.assertIs(decoded['options'], back['options'])
        self.assertIs(decoded['checkpoint'], back['checkpoint'])
        self.assertIs(decoded['unknown'], back['unknown'])
        first = decoded['errors'][0]
        self.assertEqual((first.key, first.origin), ('core.engine.cannot_start_link', 'stored'))
        self.assertEqual(first, back['errors'][0])
        self.assertEqual(first.params['error'].key, 'core.links.too_long')
        self.assertIs(type(decoded['errors'][1]), str)
        self.assertEqual(decoded['last_reason']['reason'].key, 'core.reason.http_404')
        title = messages.decode_columns(back, 'job', {'title': 'Song 等 2 条', 'kind': 'download'})
        self.assertEqual((title['title'].key, title['kind']), ('value.job_title.multi', 'download'))
        self.assertEqual(en(title['title']), 'Song and others (2 links)')
        self.assertEqual(json.dumps(back['options'], ensure_ascii=False),
                         json.dumps(self.job()['options'], ensure_ascii=False))

    def test_plain_records_stay_byte_identical(self):
        plain = {'errors': ['a', 'b'], 'last_reason': {'error': 'a', 'reason': 'x'}, 'options': {}}
        self.assertEqual(json.dumps(messages.encode(plain, 'job')), json.dumps(plain))
        self.assertNotIn('msgs', messages.encode({'title': 'Song'}, 'page'))

    def test_prune_collision_null_and_legacy_not_persisted(self):
        record = messages.encode({'title': msg('value.page_title.fallback')}, 'page')
        self.assertIn('网页', record['msgs'])
        changed = messages.encode(dict(messages.decode(stored(record), 'page'), title=raw('Other')), 'page')
        self.assertNotIn('msgs', changed, 'an entry of a text no longer present is pruned')
        both = messages.encode({'title': raw('网页'), 'links': [{'title': msg('value.page_title.fallback')}]}, 'page')
        self.assertEqual(both['msgs'], {'网页': {'k': 'value.page_title.fallback'}}, 'a message wins over null')
        user = messages.encode({'title': raw('网页'), 'links': [{'title': raw('Some Song')}]}, 'page')
        self.assertEqual(user['msgs'], {'网页': None}, 'null only for a Raw text the lift would translate (E2)')
        self.assertIs(type(messages.decode(stored(user), 'page')['title']), Raw)
        legacy = messages.decode({'title': '网页'}, 'page')['title']
        self.assertEqual((legacy.key, legacy.origin), ('value.page_title.fallback', 'legacy'))
        self.assertNotIn('msgs', messages.encode({'title': legacy}, 'page'), 'legacy guesses are never written (I5)')
        nested = msg('core.engine.error', error=messages.decode({'error': '链接不存在（404）'}, 'failed_entry')['error'])
        self.assertNotIn('msgs', messages.encode({'error': nested}, 'failed_entry'))

    def test_cap_surrogates_and_never_raise(self):
        big = msg('core.engine.error', error=raw('x' * 3000))
        self.assertNotIn('msgs', messages.encode({'error': big}, 'failed_entry'), 'an entry over 2 KB is skipped')
        lone = msg('core.engine.error', error=raw('bad \ud800 text'))
        self.assertNotIn('msgs', messages.encode({'error': lone}, 'failed_entry'))
        weird = Message('t', 'core.engine.error', {'error': object()}, 'live')
        for record, kind in (({'errors': 'not a list', 'last_reason': [1]}, 'job'), ({'error': weird}, 'failed_entry'),
                             ({'links': [None, 5, {'title': None}]}, 'page'), ('not a dict', 'job'), ({}, 'no such kind')):
            messages.encode(record, kind)
            messages.decode(record, kind)
        self.assertEqual(messages.decode_columns(None, 'job', {'title': 'x'}), {'title': 'x'})

    def test_orphans_and_broken_entries(self):
        record = {'error': '链接不存在（404）', 'msgs': {'gone text': {'k': 'core.reason.network'},
                                                    '链接不存在（404）': {'k': 5}}}
        decoded = messages.decode(record, 'failed_entry')['error']
        self.assertEqual((decoded.key, decoded.origin), ('core.reason.http_404', 'legacy'))
        self.assertEqual(messages.decode({'error': 'x', 'msgs': 'not a map'}, 'failed_entry')['error'], 'x')
        record = {'error': '链接不存在（404）', 'msgs': {'链接不存在（404）': {'k': 'core.reason.http_404'}}}
        self.assertEqual(messages.decode(record, 'failed_entry')['error'].origin, 'stored')

    def foreign_cases(self):
        nested = {'k': 'core.engine.cannot_start_link', 'p': {'error': {'k': 'core.future.inner', 'p': {'n': 1}}}}
        return [('找不到任务记录。', {'k': 'core.future.unknown', 'p': {'a': [1, 2], 'b': 'x'}}, 'Task record not found.'),
                ('Zzz 自定义文字', {'k': 'core.future.other'}, 'Zzz 自定义文字'),
                ('找不到任务记录。', {'k': 'core.library.no_job', 'p': {'extra': 1}}, 'Task record not found.'),
                ('此链接无法开始：新东西', nested, 'This link could not start: 新东西')]

    def test_foreign_entries_are_decoded_rendered_and_written_back_verbatim(self):
        for text, entry, english in self.foreign_cases():
            original = json.loads(json.dumps({'error': text, 'msgs': {text: entry}}, ensure_ascii=False))
            decoded = messages.decode(original, 'failed_entry')['error']
            self.assertEqual((decoded.origin, decoded), ('foreign', text))
            self.assertEqual(en(decoded), english)
            again = messages.encode(messages.decode(original, 'failed_entry'), 'failed_entry')
            self.assertEqual(json.dumps(again['msgs'][text], ensure_ascii=False), json.dumps(entry, ensure_ascii=False))
            joined = messages.clauses([decoded, msg('core.collect.too_large')])
            record = messages.encode({'last_error': joined}, 'subscription')
            self.assertEqual(json.dumps(record['msgs'][str.__str__(joined)]['p']['parts'][0], ensure_ascii=False),
                             json.dumps(entry, ensure_ascii=False))
            again = messages.decode(stored(record), 'subscription')['last_error']
            self.assertEqual((type(again), again.origin), (Message, 'foreign'))
            self.assertEqual(again.entry, messages._compact(record['msgs'][str.__str__(joined)]))
        oversized = {'k': 'core.future.big', 'p': {'x': 'y' * 3000}}
        decoded = messages.decode({'error': '找不到任务记录。', 'msgs': {'找不到任务记录。': oversized}}, 'failed_entry')
        self.assertEqual(decoded['error'].origin, 'legacy', 'an entry over 2 KB is malformed: lifted, not kept')

    def test_key_registry(self):
        pinned = json.loads(i18n_tool.SIDECAR_KEYS.read_text(encoding='utf-8'))['keys']
        live = i18n_tool.sidecar_keys()
        for key, names in pinned.items():
            self.assertIn(key, live, f'{key}: a pinned sidecar key is never removed (3.6)')
            self.assertEqual(live[key], names, f'{key}: placeholders of a pinned key never change')
        self.assertEqual(pinned['common.clauses'], ['parts'])
        for key in NEW_KEYS:
            self.assertIn(key, pinned)

    def test_schema_never_touches_options_checkpoint_result_or_folder(self):
        for kind, specs in messages.SCHEMA.items():
            for path, _field_kind in specs:
                self.assertFalse({'options', 'checkpoint', '_checkpoint', 'result', 'folder', 'msgs'} & set(path)
                                 if kind != 'job_options' else {'msgs'} & set(path), (kind, path))
        record = {'title': '网页', 'folder': 'x'}
        self.assertNotIn('folder', messages.encode({'title': msg('value.page_title.fallback')}, 'archive').get('msgs', {}))
        self.assertEqual(messages.decode(record, 'archive')['folder'], 'x')


# ============================================================================ legacy reader (4.6)

class LegacyReaderTests(unittest.TestCase):
    """L1-L9, and the localize tests of test_i18n_catalog moved here as legacy-reader tests."""

    SAMPLE = {'reason': 'core.wait.pace', 'error': 'core.reason.http_404', 'mode': 'value.mode.audio_mp3',
              'name': 'value.subscription.untitled'}

    def lifted(self, kind, text, context=None):
        return messages._decode_text(text, kind, context, {})

    def sample(self, name, number):
        if name in i18n_legacy.DATA.NUMERIC:
            return str(number)
        if name == 'problem':
            return i18n.CATALOG['zh']['core.unfinished.problem.dropped'].format(dropped=number)
        if name in self.SAMPLE:
            return i18n.CATALOG['zh'][self.SAMPLE[name]]
        return f'Sample{name.capitalize()}'

    def expected(self, key, values):
        shown = {}
        for name, item in values.items():
            if name == 'problem':
                shown[name] = i18n.t('core.unfinished.problem.dropped', dropped=re.search(r'\d+', item).group())
            elif name == 'mode':
                shown[name] = i18n.label('mode', item)
            elif name in self.SAMPLE:
                shown[name] = i18n.t(self.SAMPLE[name])
            else:
                shown[name] = item
        return i18n.t(key, **shown)

    def test_7_every_frozen_template_round_trips(self):
        failures = []
        with _sandbox.language('en'):
            for number in (1, 23):
                for key, template in i18n_legacy.DATA.TEMPLATES:
                    values = {name: self.sample(name, number) for name in fields(template)}
                    text = FORMATTER.vformat(template, (), values)
                    got = messages.show(self.lifted('error', text))
                    want = self.expected(key, values)
                    if got != want:
                        failures.append(f'{key}: {text!r} -> {got!r}, expected {want!r}')
        self.assertEqual(failures, [])

    def test_7_the_cases_of_9_2(self):
        untitled = {'kind': 'download', 'titled': False, 'links': 3}
        with _sandbox.language('en'):
            show = messages.show
            self.assertEqual(show(self.lifted('clauses', '网站返回 HTTP 403，可尝试动态模式或稍后重试。；页面超过 8 MB，请缩小采集范围。')),
                             'The site returned HTTP 403. Try dynamic mode or try again later. '
                             'The page is larger than 8 MB. Narrow the collection scope.')
            self.assertEqual(show(self.lifted('job_title', '3条 · 音频 · MP3 320K', untitled)), 'Links: 3 · Audio · MP3 320K')
            self.assertEqual(show(self.lifted('job_title', '1 条 · 音频 · MP3 320K', untitled)), 'Links: 1 · Audio · MP3 320K')
            self.assertEqual(show(self.lifted('job_title', '周杰伦 等 群星 等 3 条', {'kind': 'download', 'titled': True,
                                                                                'links': 3})),
                             '周杰伦 等 群星 and others (3 links)')
            self.assertEqual(show(self.lifted('job_title', '订阅：未命名订阅', {'kind': 'collect'})),
                             'Subscription: Untitled subscription')
            self.assertEqual(show(self.lifted('clauses', '检查中断；下次重试，不更新已见记录')),
                             i18n.t('value.subscription.interrupted'))
            self.assertEqual(show(self.lifted('job_title', '检查未完成', {'kind': 'collect'})), '检查未完成')
            self.assertEqual(show(self.lifted('clauses', '检查未完成')), 'Check not finished')
            self.assertEqual(show(self.lifted('name', '未命名订阅')), 'Untitled subscription')
            self.assertEqual(show(self.lifted('title', '网页')), 'Web page')
            self.assertEqual(show(self.lifted('title', '网页正文')), '网页正文')
            self.assertEqual(show(self.lifted('error', '网站限流，暂停整个下载队列，等待 300 秒；可随时点击停止。')),
                             'The site is rate-limiting; pausing the whole queue: waiting 300 s; you can stop at any time.')
            template = i18n_legacy.DATA.TEMPLATES[[k for k, _t in i18n_legacy.DATA.TEMPLATES].index('core.unfinished.kept_aside')][1]
            text = FORMATTER.vformat(template, (), {name: self.sample(name, 4) for name in fields(template)})
            self.assertNotIn('有 2 条记录无法识别', show(self.lifted('error', text)))
            for raw_text in ('ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies',
                             '[download]  12.5% of 3.00MiB at 1.2MiB/s ETA 00:02', '周杰伦 - 晴天', ''):
                self.assertEqual(show(self.lifted('error', raw_text)), raw_text)
            self.assertEqual(show(self.lifted('error', '[代理] 无响应')), '[proxy] 无响应')
        for text in ('网页', '3条 · 音频 · MP3 320K', '已内置'):
            self.assertEqual(messages.show(self.lifted('error', text)), text, 'zh: identity')

    def test_7_the_collector_texts_1_5_1_stored(self):
        """The 1.5.1 collector texts without the spaces 1.6.0 added (the frozen VARIANTS) read English,
        also '；'-joined as collection_ui stored two of them."""
        tree = ast.parse((_sandbox.release_src('1.5.1') / 'collector.py').read_text(encoding='utf-8'))
        inner = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
        texts = []
        for node in ast.walk(tree):
            if id(node) in inner:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = ''.join(part.value if isinstance(part, ast.Constant) else '503' for part in node.values)
            else:
                continue
            if CJK.search(text) and text not in ('媒体地址', '视频页面', '网页链接', '[代理]'):
                texts.append(text)
        self.assertGreaterEqual(len(texts), 15)
        with _sandbox.language('en'):
            left = [(text, messages.show(self.lifted('error', text))) for text in texts
                    if CJK.search(messages.show(self.lifted('error', text)))]
            self.assertEqual([item for item in left if item[0] not in ('网页', '媒体直链', '动态页面媒体请求')], [])
            self.assertEqual(messages.show(self.lifted('clauses', '网站返回HTTP 503，可尝试动态模式或稍后重试。；页面超过8MB，请缩小采集范围。')),
                             'The site returned HTTP 503. Try dynamic mode or try again later. '
                             'The page is larger than 8 MB. Narrow the collection scope.')
            self.assertEqual(messages.show(self.lifted('error', '采集进程退出（-1），请检查页面或动态模式所需的Chrome。')),
                             i18n.t('core.collect.worker_exit', code='-1'))
        for text in texts:
            self.assertEqual(messages.show(self.lifted('error', text)), text, 'zh: identity')

    def test_9_ten_thousand_lines_in_under_a_second(self):
        lines = ['下载未完成（退出码 {}）', '此链接未完成（退出码 {}），继续处理队列中的下一项。', '[download] {}% of 3MiB',
                 '文件已保存：C:\\x\\{}.mp3', '网站限流，冷却后重试此链接（{}/2）', '错误：页面 {} 不存在；稍后重试',
                 'ERROR: [generic] {}: Unable to download webpage', '周杰伦 等 {} 条']
        mixed = [lines[n % len(lines)].format(n) for n in range(10000)]
        started = time.perf_counter()
        for line in mixed:
            i18n_legacy.lift('error', line)
        seconds = time.perf_counter() - started
        print(f'10,000 mixed lines lifted in {seconds:.3f} s')
        self.assertLess(seconds, 1.0)

    def test_l1_l3_d_h_1_fragment_and_cut_prefixes(self):
        fragment = 'cookies 文件中有一行格式不对，无法读取（内容不记录）'
        self.assertEqual(i18n_legacy.lift('error', fragment), ('core.cookies.line_failed_item', {}))
        self.assertEqual(i18n_legacy.DATA.STRIPPED, {'core.cookies.line_failed': 'core.cookies.line_failed_item'})
        self.assertEqual(i18n.CATALOG['zh']['core.cookies.line_failed'][len('ERROR: '):], fragment)
        long = '此链接无法开始：找不到 cookies 文件：' + 'D:\\' + 'x' * 300 + '\n请在“偏好设置 → 网络”中重新选择。'
        for kind, size in (('error', 200), ('error', 900), ('reason', 70)):
            text = (long + 'y' * 1000)[:size] if size == 900 else long[:size]
            found = i18n_legacy.lift(kind, text)
            self.assertIsNotNone(found, (kind, size))
            self.assertEqual(found[0], 'core.engine.cannot_start_link')
            decoded = self.lifted(kind, text)
            self.assertEqual(decoded, text)
            self.assertTrue(en(decoded).startswith('This link could not start: Cookies file not found: D:' + chr(92) + 'xxx'),
                            (kind, size, en(decoded)))
        key, params = i18n_legacy.lift('error', long[:199])
        self.assertIs(type(params['error']), str, 'L3 (also for a cut capture) only at the stored cut lengths')
        self.assertIsNone(i18n_legacy.lift('error', ('此链接' + 'z' * 300)[:200]), 'needs 8 literal characters')

    def test_l4_r4_pairing(self):
        error = '这个文件不是 Netscape 格式的 cookies.txt（第一行应为 “# Netscape HTTP Cookie File”），请重新导出。'
        reason = i18n_legacy.EXTRACTOR_PREFIX.sub('', error.strip(), count=1)[:70]
        decoded = messages.decode({'last_reason': {'error': error, 'reason': reason}}, 'job')['last_reason']
        self.assertEqual(decoded['reason'], reason)
        self.assertEqual(decoded['reason'].key, 'core.cookies.not_netscape')
        self.assertEqual(en(decoded['reason']), i18n.t('core.cookies.not_netscape') if False else en(decoded['error']))
        missing = '找不到 cookies 文件：C:\\' + 'p' * 200 + '\n请在“偏好设置 → 网络”中重新选择。'
        reason = missing[:70]
        decoded = messages.decode({'last_reason': {'error': missing, 'reason': reason}}, 'job')['last_reason']
        self.assertEqual(decoded['reason'].key, 'core.cookies.missing')
        with _sandbox.language('en'):
            self.assertTrue(messages.show(decoded['reason']).startswith('Cookies file not found: C:\\ppp'))

    def test_l5_clauses_and_the_needs_chrome_pair(self):
        text = '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。；页面超过 8 MB，请缩小采集范围。'
        decoded = self.lifted('clauses', text)
        self.assertEqual(decoded.key, 'common.clauses')
        self.assertEqual([part.key for part in decoded.params['parts']], ['core.collect.needs_chrome',
                                                                           'core.collect.too_large'])
        self.assertEqual(en(decoded), 'Dynamic collection needs Google Chrome; static page mode works without a '
                                      'browser. The page is larger than 8 MB. Narrow the collection scope.')
        self.assertEqual(self.lifted('clauses', '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。').key,
                         'core.collect.needs_chrome')
        mixed = self.lifted('clauses', 'curl: (7) failed；页面超过 8 MB，请缩小采集范围。')
        self.assertEqual(en(mixed), 'curl: (7) failed. The page is larger than 8 MB. Narrow the collection scope.')
        self.assertEqual(self.lifted('clauses', 'a；b'), 'a；b')

    def test_l5_a_capture_does_not_swallow_the_next_clause(self):
        """WP-1 fix round 1 (WP1-R1-02): a whole-text lift whose capture holds a '；' joiner loses
        to the grouping that lifts each clause; a '；' inside a template literal still lifts whole."""
        text = '历史记录本次不保存：database is locked；页面超过 8 MB，请缩小采集范围。'
        decoded = self.lifted('clauses', text)
        self.assertEqual(str.__str__(decoded), text)
        self.assertEqual(decoded.key, 'common.clauses')
        self.assertEqual([part.key for part in decoded.params['parts']], ['core.library.temporary',
                                                                           'core.collect.too_large'])
        self.assertEqual(en(decoded), 'History is not saved in this session: database is locked. The page is '
                                      'larger than 8 MB. Narrow the collection scope.')
        waiting = 'Qq；链接不存在（404），等待 12 秒；可随时点击停止。'
        decoded = self.lifted('clauses', waiting)
        self.assertEqual(decoded.key, 'common.clauses')
        self.assertEqual([getattr(part, 'key', None) for part in decoded.params['parts']], [None, 'core.engine.waiting'])
        self.assertNotRegex(en(decoded).replace('链接不存在（404）', ''), '[一-鿿]')
        self.assertEqual(self.lifted('clauses', '历史记录本次不保存：database is locked').key, 'core.library.temporary')

    def test_l6_job_titles_by_context(self):
        multi = '周杰伦 等 3 条'
        self.assertIsNone(i18n_legacy.lift('job_title', multi, {'kind': 'download', 'titled': True, 'links': 1}))
        self.assertEqual(i18n_legacy.lift('job_title', multi, {'kind': 'download', 'titled': True, 'links': 3})[0],
                         'value.job_title.multi')
        self.assertIsNone(i18n_legacy.lift('job_title', multi, {'kind': 'collect'}))
        self.assertIsNone(i18n_legacy.lift('job_title', '订阅：x', {'kind': 'download', 'titled': True, 'links': 2}))
        self.assertIsNone(i18n_legacy.lift('job_title', '3 条 · 音频 · FLAC', {'kind': 'download', 'titled': True,
                                                                           'links': 3}))
        key, params = i18n_legacy.lift('job_title', '3条 · 音频 · FLAC', {'kind': 'download', 'titled': False, 'links': 3})
        self.assertEqual((key, params['mode']), ('value.job_title.placeholder', i18n_legacy.Enum('mode', '音频 · FLAC')))
        key, params = i18n_legacy.lift('job_title', '订阅：未命名订阅', {'kind': 'collect'})
        self.assertEqual(params['name'].key, 'value.subscription.untitled')
        self.assertEqual(i18n_legacy.lift('job_title', '订阅：我的订阅', {'kind': 'collect'})[1]['name'], '我的订阅')

    def test_l7_l8_titles_and_names(self):
        self.assertEqual(i18n_legacy.DATA.TITLE_KEYS, ('value.page_title.fallback', 'value.link_kind.direct',
                                                       'value.link_kind.dynamic_request'))
        for text, key in (('网页', 'value.page_title.fallback'), ('媒体直链', 'value.link_kind.direct'),
                          ('动态页面媒体请求', 'value.link_kind.dynamic_request')):
            self.assertEqual(i18n_legacy.lift('title', text), (key, {}))
            self.assertEqual(i18n_legacy.lift('context_title', text), (key, {}))
        for text in ('媒体地址', '网页正文', '未命名订阅', '链接不存在（404）'):
            self.assertIsNone(i18n_legacy.lift('title', text), text)
        self.assertEqual(i18n_legacy.lift('name', '未命名订阅'), ('value.subscription.untitled', {}))
        self.assertIsNone(i18n_legacy.lift('name', '网页'))
        options = {'archive_contexts': [{'title': '网页'}, {'title': 'Song'}], 'mode': 'x'}
        decoded = messages.decode(options, 'job_options')
        self.assertEqual(decoded['archive_contexts'][0]['title'].key, 'value.page_title.fallback')
        self.assertIs(decoded['mode'], options['mode'])
        self.assertNotIn('msgs', messages.encode({'archive_contexts': [{'title': msg('value.page_title.fallback')}]},
                                                 'job_options'))
        # I-1 amendment A2: archive_contexts is a dict link -> context in every release; '*' walks its values
        contexts = {'https://a.example/1': {'title': '媒体直链', 'page': 'p'}, 'https://a.example/2': {'title': 'Song'}}
        options = {'archive_contexts': contexts, 'mode': 'x'}
        decoded = messages.decode(options, 'job_options')
        self.assertEqual(list(decoded['archive_contexts']), list(contexts))
        self.assertEqual(decoded['archive_contexts']['https://a.example/1']['title'].key, 'value.link_kind.direct')
        self.assertEqual(decoded['archive_contexts']['https://a.example/1']['page'], 'p')
        self.assertNotIsInstance(decoded['archive_contexts']['https://a.example/2']['title'], messages.Message)
        self.assertEqual(type(contexts['https://a.example/1']['title']), str, 'the stored options are never changed')
        self.assertIs(decoded['mode'], options['mode'])
        self.assertIs(options['archive_contexts'], contexts)
        self.assertNotIn('msgs', messages.encode({'archive_contexts': {'u': {'title': msg('value.link_kind.direct')}}},
                                                 'job_options'))

    def test_l9_every_frozen_key_for_error_reason_and_clauses(self):
        for key, template in i18n_legacy.DATA.TEMPLATES:
            if fields(template):
                continue
            text = template.replace('{{', '{').replace('}}', '}')
            for kind in ('error', 'reason', 'clauses'):
                self.assertIsNotNone(i18n_legacy.lift(kind, text), (kind, key))


# ============================================================================ catalogs and frozen data

class CatalogParityTests(unittest.TestCase):
    def test_zh_catalog_equals_1_7_0_and_the_eight_new_keys(self):
        """Every 1.7.0 key keeps its zh and en (MESSAGES.md 8.1), and the catalog adds the eight core keys.

        (1.8.1) The sanctioned exceptions, nothing else: CHANGED_1_8_1 (two subscription notes the owner
        asked to name 忽略) and ADDED_1_8_1 (the 13 library keys of the subscription management)."""
        released_zh = i18n_tool.release_catalog('1.7.0', 'zh')
        released_en = i18n_tool.release_catalog('1.7.0', 'en')
        self.assertGreater(len(released_zh), 500)
        self.assertLessEqual(set(CHANGED_1_8_1), set(released_zh))
        for key, text in released_zh.items():
            if key in CHANGED_1_8_1:
                self.assertEqual((i18n.CATALOG['zh'].get(key), i18n.CATALOG['en'].get(key)), CHANGED_1_8_1[key], key)
                continue
            self.assertEqual(i18n.CATALOG['zh'].get(key), text, f'{key}: zh text of a 1.7.0 key is unchanged (8.1)')
            self.assertEqual(i18n.CATALOG['en'].get(key), released_en[key], f'{key}: en text unchanged in WP-1')
        added = set(i18n.CATALOG['zh']) - set(released_zh)
        self.assertEqual(added, set(NEW_KEYS) | set(ADDED_1_8_1))
        for key, (zh, english) in NEW_KEYS.items():
            self.assertEqual((i18n.CATALOG['zh'][key], i18n.CATALOG['en'][key]), (zh, english), key)
        for key, (zh, english) in ADDED_1_8_1.items():
            self.assertEqual((i18n.CATALOG['zh'][key], i18n.CATALOG['en'][key]), (zh, english), key)
        inventory = i18n_tool.load_inventory(i18n_tool.INVENTORY)
        self.assertEqual({item['key'] for item in inventory['new_strings_1_8_0']}, set(NEW_KEYS))

    def test_the_reverse_maps_left_i18n_maps(self):
        """(WP-6, MESSAGES.md 6.4) i18n_maps keeps GROUPS exactly as released and has no reverse
        section any more; the released NUMERIC / RECURSE live on, frozen, in i18n_legacy_data."""
        released = i18n_tool._module_constants(_sandbox.release_src('1.7.0') / 'i18n_maps.py',
                                               {'LOCALIZE', 'VARIANTS', 'NUMERIC', 'RECURSE', 'GROUPS'})
        self.assertEqual(i18n_maps.GROUPS, released['GROUPS'])
        for name in ('LOCALIZE', 'VARIANTS', 'NUMERIC', 'RECURSE'):
            self.assertFalse(hasattr(i18n_maps, name), name)
        self.assertEqual(tuple(i18n_legacy.DATA.NUMERIC), tuple(released['NUMERIC']))
        self.assertEqual(dict(i18n_legacy.DATA.RECURSE), dict(released['RECURSE']))
        self.assertFalse(hasattr(i18n, 'localize'), 'i18n.localize is gone (6.4)')

    def test_frozen_legacy_data_is_the_generated_text(self):
        self.assertEqual((_sandbox.SRC / 'i18n_legacy_data.py').read_text(encoding='utf-8'), i18n_tool.legacy_text())
        pins = i18n_legacy.DATA.SOURCES
        for name, digest in pins.items():
            self.assertEqual(i18n_tool._sha256(_sandbox.release_src('1.7.0') / name), digest, name)

    def test_release_sources_are_pinned_and_scrubbed(self):
        pins = json.loads(i18n_tool.RELEASE_PINS.read_text(encoding='utf-8'))['releases']
        self.assertEqual(i18n_tool.release_hashes(), pins)
        self.assertEqual(pins['1.7.0']['origin'], 'git archive 07c9603 src')
        self.assertEqual(i18n_tool.scrub_findings([_sandbox.RELEASE_SOURCES]), [])
        self.assertTrue(i18n_tool.scrub_patterns(), 'the patterns come from the environment')
        self.assertFalse(list(_sandbox.RELEASE_SOURCES.rglob('__pycache__')))
        with mock.patch.dict(os.environ, {'USERNAME': 'zz-probe-user'}):
            self.assertIn(('user name', 'zz-probe-user'), i18n_tool.scrub_patterns())
        probe = Path(os.environ['TEMP']) / 'scrub-probe'
        probe.mkdir(exist_ok=True)
        (probe / 'a.py').write_text("X = 'mail me at someone@example.org'\n", encoding='utf-8')
        self.assertEqual(i18n_tool.scrub_findings([probe]), [('a.py', 'e-mail address')])


# ============================================================================ G2 self-tests (7.3)

def probe_module(source, in_src=True, name='zz_guard_probe'):
    """A module whose code lives (by file name) in src, so it is guarded like every src module outside
    TRANSIT (MESSAGES.md 7.8 after WP-6); ``in_src=False`` puts it outside src and verification/,
    where a check finds no producer frame (third-party code: never charged)."""
    folder = _sandbox.SRC if in_src else _sandbox.out('guard-probe-outside-src')
    folder.mkdir(parents=True, exist_ok=True)
    filename = str(folder / f'{name}.py')
    module = types.ModuleType(name)
    module.__file__ = filename
    module.messages = messages
    module.i18n = i18n
    exec(compile(textwrap.dedent(source), filename, 'exec'), module.__dict__)
    return module


PROBE = '''
def emit_page(emit, title):
    emit('collect_page', {'title': title, 'links': []})

def emit_log(emit, line):
    emit('log', line)

def encode_page(title):
    return messages.encode({'title': title}, 'page')

def show(value):
    return messages.show(value)

def error_text(error):
    return messages.error_text(error)

def t(key, **params):
    return i18n.t(key, **params)

def cut(value):
    return value[:3]

def join(parts):
    return i18n._join_sentences('en', parts)
'''


@unittest.skipUnless(messages.GUARD, 'needs YTDLP_DESKTOP_MSG_GUARD (run_tests.py sets it)')
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.ready = probe_module(PROBE)                                # a src module: guarded
        self.plain = probe_module(PROBE, in_src=False, name='zz_plain_probe')   # outside src: no producer

    def run_both(self, call):
        with messages.guard_capture() as guarded:
            try:
                call(self.ready)
            except messages.MessageFlattened:
                pass
        with messages.guard_capture() as unguarded:
            call(self.plain)
        return guarded, unguarded

    def assertCaught(self, call, rule, key=None):
        guarded, unguarded = self.run_both(call)
        self.assertTrue(guarded, f'{rule}: nothing recorded')
        self.assertEqual(guarded[0]['rule'], rule, guarded)
        self.assertTrue(guarded[0]['producer'].startswith('zz_guard_probe.py:'), guarded)
        if key:
            self.assertEqual(guarded[0]['key'], key, guarded)
        self.assertEqual(unguarded, [], 'code outside src (no producer frame) is never reported')

    def test_plain_media_direct_title_through_emit_and_encode(self):
        emit = messages.guarded(lambda kind, value_: None, 'probe')
        self.assertIsNot(emit, None)
        self.assertCaught(lambda module: module.emit_page(emit, '媒体直链'), 'R-text', 'value.link_kind.direct')
        self.assertCaught(lambda module: module.encode_page('媒体直链'), 'R-text', 'value.link_kind.direct')
        self.assertCaught(lambda module: module.encode_page(raw('动态页面媒体请求')), 'R-text')

    def test_group_value_as_a_nested_msg_param(self):
        emit = messages.guarded(lambda kind, value_: None, 'probe')
        self.assertCaught(lambda module: module.emit_log(emit, msg('core.engine.error', error='MKV · 保留源画质')),
                          'R-text', 'format.video_mkv')
        with messages.guard_capture() as records:
            self.ready.emit_log(emit, msg('core.engine.error', error=value('format', 'MKV · 保留源画质')))
            self.ready.emit_log(emit, msg('value.page_title.fallback'))
            self.ready.emit_log(emit, '[download]  12.5% of 3.00MiB')
        self.assertEqual(records, [])

    def test_show_and_error_text(self):
        self.assertCaught(lambda module: module.show('媒体直链'), 'R-text', 'value.link_kind.direct')
        self.assertCaught(lambda module: module.error_text(ValueError('找不到任务记录。')), 'R-text', 'core.library.no_job')
        with messages.guard_capture() as records:
            self.ready.show(msg('core.library.no_job'))
            self.ready.error_text(ValueError(msg('core.library.no_job')))
            self.ready.show('Some Song')
        self.assertEqual(records, [])

    def test_t_parameters(self):
        self.assertCaught(lambda module: module.t('library.unreadable', error=ValueError('x')), 'R-param')
        self.assertCaught(lambda module: module.t('value.job_title.multi', title=msg('value.page_title.fallback'),
                                                  count=2), 'R-param')
        with _sandbox.language('en'):
            self.assertCaught(lambda module: module.t('value.job_title.multi', title='媒体直链', count=2), 'R-text')
        with messages.guard_capture() as records:
            self.ready.t('library.unreadable', error=messages.error_text(ValueError(msg('core.library.no_job'))))
            self.ready.t('value.job_title.multi', title='媒体直链', count=2)        # R-text applies in en only
        self.assertEqual(records, [])
        self.assertIs(i18n.t, messages._checked_t)

    def test_slices_in_a_guarded_module_and_inside_i18n(self):
        m = msg('core.library.no_job')
        with messages.guard_capture() as records, self.assertRaises(messages.MessageFlattened):
            self.ready.cut(m)
        self.assertEqual(records[0]['rule'], 'tripwire')
        with messages.guard_capture() as records, self.assertRaises(messages.MessageFlattened):
            self.ready.join([m, msg('core.reason.network')])
        self.assertEqual((records[0]['rule'], records[0]['producer'][:15]), ('tripwire', 'zz_guard_probe.'))
        with messages.guard_capture() as records:
            self.assertEqual(self.plain.cut(m), '找不到')
            self.assertEqual(self.plain.join([m, msg('core.reason.network')]), '找不到任务记录。. 连不上网站，请检查网络或 VPN.')
            self.assertEqual(m[:3], '找不到')                             # a verification/ frame: plain str
        self.assertEqual(records, [])
        with messages.guard_capture() as records:
            self.assertEqual(self.ready.cut(raw('Raw has no tripwires')), 'Raw')
        self.assertEqual(records, [])

    def test_ui_text_exclusion_for_a_log_line_made_by_t(self):
        line = i18n.t('collect.status.library_gave_up')
        self.assertTrue(i18n_legacy.recognise(line), 'the line shares wording with a core text')
        self.assertIsNone(i18n_legacy.recognise(line, display=True))
        with messages.guard_capture() as records:
            self.ready.show(line)
        self.assertEqual(records, [])

    def test_a_zh_ui_text_shown_in_english(self):
        """(converge round, A15.5, final review F1 / F2) In English a fixed zh UI text that reaches
        show() / error_text() or a t() parameter (an h4 constant such as library_ui.PICK_ARCHIVE) is
        recorded: show() gives it back unchanged.  In zh the UI-text exclusion still holds (t() lines
        pass through show()), and a language name the English catalog has itself is not recorded."""
        pick = i18n.CATALOG['zh']['library.pick_archive']
        self.assertIsNone(i18n_legacy.recognise(pick, display=True), 'a UI text: not recognised at zh display')
        with _sandbox.language('en'):
            self.assertEqual(i18n_legacy.recognise(pick, True, True), 'library.pick_archive')
            self.assertCaught(lambda module: module.show(pick), 'R-text', 'library.pick_archive')
            self.assertCaught(lambda module: module.error_text(ValueError(pick)), 'R-text', 'library.pick_archive')
            self.assertCaught(lambda module: module.t('library.unreadable', error=pick), 'R-text', 'library.pick_archive')
            with messages.guard_capture() as records:
                self.ready.show(i18n.t('library.pick_archive'))
                self.ready.show(raw(pick))                       # user data that equals a UI text: typed Raw
                for value_ in i18n.CATALOG['en'].values():
                    if CJK.search(value_):
                        self.ready.show(value_)                  # the English catalog's own CJK (language names)
            self.assertEqual(records, [])
        with messages.guard_capture() as records:
            self.ready.show(pick)                                # zh: the exclusion of 7.3
        self.assertEqual(records, [])

    def test_ui_theme_cut_values_is_the_display_boundary(self):
        """(MESSAGES.md 7.3, 2.9) ui_theme's table class is AppTable (the spec's Treeview).  Since WP-6
        ui_theme is not in TRANSIT: a message that reaches the table without show() is recorded with
        producer ui_theme, also with no other src frame on the stack; with the pre-WP-6 set (ui_theme
        in TRANSIT) the same call is charged to nobody."""
        import ui_theme

        class Fake:
            _cut_middle = ()
            _cut_common = {}

            def __getitem__(self, name):
                return ('title',)

        m = msg('value.page_title.fallback')
        patches = (mock.patch.object(ui_theme, '_measure_font', lambda *a: None),
                   mock.patch.object(ui_theme, '_width_lookup', lambda *a: None),
                   mock.patch.object(ui_theme, '_cut_cell', lambda text, *a: text))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertNotIn('ui_theme', messages.TRANSIT)
        with mock.patch.object(messages, 'TRANSIT', {'messages', 'i18n_legacy', 'i18n', 'ui_theme'}), \
                messages.guard_capture() as records:
            self.assertEqual(ui_theme.AppTable._cut_values(Fake(), (m,), {'title': 100}), ('网页',))
        self.assertEqual(records, [])
        with messages.guard_capture() as records, self.assertRaises(messages.MessageFlattened):
            ui_theme.AppTable._cut_values(Fake(), (m,), {'title': 100})
        self.assertEqual((records[0]['rule'], records[0]['producer'].split(':')[0]), ('tripwire', 'ui_theme.py'))
        shown = messages.show(m)
        with messages.guard_capture() as records:
            self.assertEqual(ui_theme.AppTable._cut_values(Fake(), (shown,), {'title': 100}), ('网页',))
        self.assertEqual(records, [], 'a shown text is plain: no tripwire')

    def test_exempt_is_counted_and_suppresses(self):
        emit = messages.guarded(lambda kind, value_: None, 'probe')
        with messages.guard_capture() as records:
            with _sandbox.fixture_texts('self-test: a user page literally titled 网页'):
                self.ready.emit_page(emit, '网页')
        self.assertEqual([item.get('exempt') for item in records], ['self-test: a user page literally titled 网页'])
        with self.assertRaises(ValueError):
            with messages.exempt(''):
                pass

    def test_report_file_lines(self):
        before = list(messages._records)
        path = messages._report_path()
        self.assertIsNotNone(path)
        self.assertTrue(str(path).startswith(os.environ['VERIFY_OUT']))
        self.assertEqual(messages._records, before, 'the self-tests write nothing to the report')


class GuardSwitchTests(unittest.TestCase):
    def test_the_runner_turns_the_guard_on(self):
        if os.environ.get('YTDLP_VERIFY_SANDBOX') and os.environ.get(messages.GUARD_ENV):
            self.assertTrue(messages.GUARD)
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import messages, i18n; '
                'print(messages.GUARD, i18n.t is messages._ORIGINAL_T, "__getitem__" in vars(messages.Message))')
        import subprocess
        import sys
        environment = dict(os.environ)
        environment.pop(messages.GUARD_ENV, None)
        off = subprocess.run([sys.executable, '-B', '-c', code, str(_sandbox.SRC)], capture_output=True, text=True,
                             env=environment, timeout=60)
        self.assertEqual(off.stdout.split(), ['False', 'True', 'False'], off.stderr)
        environment[messages.GUARD_ENV] = '1'
        on = subprocess.run([sys.executable, '-B', '-c', code, str(_sandbox.SRC)], capture_output=True, text=True,
                            env=environment, timeout=60)
        self.assertEqual(on.stdout.split(), ['True', 'False', 'True'], on.stderr)
        self.assertIs(messages.guarded(print, 'x') is print, not messages.GUARD)


if __name__ == '__main__':
    unittest.main()
