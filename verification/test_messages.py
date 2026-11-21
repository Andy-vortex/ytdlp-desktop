# quick
"""The message model, its store and IPC forms (MESSAGES.md 2; 2.0.0 S2-DESIGN.md 1.1, 1.6).

    python -X utf8 -B test_messages.py

The carrier rules of 2.3 for Message and Raw; show() / error_text() in zh and en; the derived texts (cut,
masked, clauses, stripped, message_of); the format-2 store form (to_json / from_json / read_slots /
write_slots: round trip, a key of a later version, a param that cannot be stored); the collector wire form
({"$m": {"k", "p"}}, a malformed marker); the scan for machine paths and user names (PLAN 3.11).  No window,
no network.  (2.0.1) The G2 message guard (tripwires on Message, R-param, exempt) and its self-tests are
gone: what they caught is caught by the English page tests, the channel matrices and the key assertions.

(2.0.0 S2 P2) G2's text recognition (R-text, messages.guarded, i18n_legacy.recognise) is gone, its self-tests
with it.  (S2 P4) The 1.8.x store codec (decode / decode_columns / encode), Value, a
message's origin / entry and the reader of keyless stored text left the runtime (S2 Q1: 2.0.0 does not
read the stores of older versions, so their tests are gone too); what pinned the writing half (prune,
collision, the 2 KB cap, foreign entries written back, the 1.8.x key registry) has nothing left to pin:
golden/stored-keys.json is the key registry of format 2.

(2.0.0 S3 W2) The letter-for-letter pins of the keys added after 1.7.0 (NEW_KEYS, CHANGED_1_8_1, ADDED_1_8_1,
ADDED_2_0_0) and the i18n_maps check are gone: the catalogs are the single hand-written source, checked by
test_i18n_catalog.CatalogRuleTests (C9: no key nobody uses; C8: no key the code names is missing; C10b: the
texts that tell a file was kept aside keep their facts) (S3-DESIGN.md 3.2).
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
import time
import unittest
from pathlib import Path
from unittest import mock

import i18n
import i18n_tool
import messages
from messages import Message, Raw, msg, raw

FORMATTER = string.Formatter()
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')


def fields(text):
    return {field for _literal, field, _spec, _conv in FORMATTER.parse(text) if field is not None}


# (S3 review fix) The mechanism is tested on keys this file puts into the catalog for its own run, so the wording
# of the app's texts (the catalog's) is pinned nowhere here.  Keys of the app appear only where no wording is
# compared.
FAKE = {
    'test.start': ('无法开始：{error}', 'Could not start: {error}'),
    'test.too_long': ('过长（{length} 个字符）：{url}', 'Too long ({length} characters): {url}'),
    'test.wait': ('{reason}，等待 {seconds} 秒', '{reason}, waiting {seconds} s'),
    'test.pace': ('间隔', 'Pacing'),
    'test.multi': ('{title} 等 {count} 条', '{title} and others ({count} links)'),
    'test.name': ('名字', 'Name'),
    'test.english': ('Unsupported: {mode}', 'Unsupported: {mode}'),
    'test.first': ('第一句。', 'The first sentence.'),
    'test.second': ('第二句。', 'The second sentence.'),
    'test.plain': ('找不到记录。', 'Record not found.'),
    'test.error': ('错误：{error}', 'Error: {error}'),
}


def setUpModule():
    for key, pair in FAKE.items():
        for language, text in zip(i18n.LANGUAGES, pair):
            i18n.CATALOG[language][key] = text


def tearDownModule():
    for key in FAKE:
        for language in i18n.LANGUAGES:
            i18n.CATALOG[language].pop(key, None)


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
        return [msg('test.start', error=msg('test.too_long', length=2100, url='youtu.be/x')),
                raw('Some Song 网页'), messages.clauses([msg('test.first'), raw('外来')])]

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
        text = '无法开始：过长（2100 个字符）：youtu.be/x'
        self.assertEqual(m, text)
        self.assertEqual(hash(m), hash(text))
        self.assertIn(m, {text})
        self.assertEqual({m: 1}[text], 1)
        self.assertTrue(re.search('无法开始', m))
        self.assertTrue(m.startswith('无法'))
        self.assertEqual(len(m), len(text))
        self.assertEqual(list(m), list(text))
        self.assertEqual(m.encode('utf-8'), text.encode('utf-8'))
        self.assertEqual(m.key, 'test.start')
        self.assertFalse(hasattr(m, 'origin') or hasattr(m, 'entry'), '(2.0.0 S2 P4) key and params only')
        self.assertEqual(m.params['error'].params, {'length': 2100, 'url': 'youtu.be/x'})
        with self.assertRaises(TypeError):
            m.params['error'] = 'x'
        for name in ('key', 'origin', 'other'):
            with self.assertRaises(AttributeError):
                setattr(m, name, 'x')
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
        self.assertEqual(m.key, 'test.start')
        self.assertEqual(m.params['error'].key, 'test.too_long')
        joined = pickle.loads(pickle.dumps(self.samples()[2]))
        self.assertEqual((joined.key, [type(part) for part in joined.params['parts']]), (messages.CLAUSES, [Message, Raw]))
        keyless = pickle.loads(pickle.dumps(Message('x')))
        self.assertEqual((keyless, keyless.key, dict(keyless.params)), ('x', None, {}))

    def test_methods_return_plain_str_outside_guarded_modules(self):
        m = self.samples()[0]
        for result in (m[:5], m + 'x', 'x' + m, m * 2, m.strip(), m.replace('无', '有'), m.split('：')[0], m.lower()):
            self.assertIs(type(result if isinstance(result, str) else str(result)), str)
        self.assertEqual(m[:5], str.__str__(m)[:5])

    def test_t_shows_a_message_param_in_the_language_of_the_call(self):
        """(2.0.1 D3) i18n.t shows a message param (or an exception that carries only a message) as show() would
        in the language of the call (Message.text_in), nested params and clauses too; anything else is str()."""
        m = msg('test.too_long', length=9, url='u')
        nested = msg('test.multi', count=2, title=msg('test.name'))
        joined = messages.clauses([msg('test.first'), msg('test.wait', reason=msg('test.pace'), seconds=3)])
        with _sandbox.language('en'):
            for value in (m, nested, joined):
                self.assertEqual(i18n.t('test.start', error=value), i18n.t('test.start', error=messages.show(value)))
            self.assertEqual(i18n.t('test.start', error=m), 'Could not start: Too long (9 characters): u')
            self.assertEqual(i18n.t('test.start', error=nested), 'Could not start: Name and others (2 links)')
            self.assertEqual(i18n.t('test.start', error=joined),
                             'Could not start: The first sentence. Pacing, waiting 3 s.')
            self.assertEqual(i18n.t('test.start', error=ValueError(m)), i18n.t('test.start', error=messages.show(m)))
            self.assertEqual(i18n.t('test.start', error=OSError(nested)),
                             i18n.t('test.start', error=messages.show(nested)))
            self.assertEqual(i18n.t('test.start', error=ValueError('plain')), 'Could not start: plain')
            self.assertEqual(i18n.t('test.start', error=ValueError(m, 'two')),
                             'Could not start: ' + str(ValueError(m, 'two')), 'two args: str()')
            self.assertEqual(i18n.t('test.start', error=raw('中文')), 'Could not start: 中文')
            self.assertEqual(i18n.t('test.start', error=5), 'Could not start: 5')
            self.assertEqual(i18n.t('test.start', error=Message('自定义')), 'Could not start: 自定义')
            self.assertEqual(m.text_in('en'), 'Too long (9 characters): u')
            self.assertEqual(m.text_in('zh'), str.__str__(m))
            self.assertEqual(nested.text_in('zh'), str.__str__(nested), 'en UI: text_in("zh") is the Chinese')
        # (2.0.1) the language argument holds in a zh UI too: text_in('en') is the English, nested and clauses too
        self.assertEqual(m.text_in('en'), 'Too long (9 characters): u')
        self.assertEqual(joined.text_in('en'), 'The first sentence. Pacing, waiting 3 s.')
        for value in (m, nested, joined, ValueError(m), raw('中文')):
            self.assertEqual(i18n.t('test.start', error=value), '无法开始：' + str.__str__(str(value)))
        self.assertEqual(m.text_in('zh'), str.__str__(m))
        self.assertIs(type(m.text_in('zh')), str)
        # (2.0.2) the proxy mask token too: in a zh UI, text_in('en') (and _shown(…, 'en')) give the English token
        self.assertEqual(i18n.language(), 'zh')
        proxy = _sandbox.en_t('core.proxy_mask')
        self.assertNotEqual(proxy, messages.PROXY_TOKEN)
        token_text = messages.PROXY_TOKEN + ' x'
        masked = msg('test.start', error=raw(token_text))
        self.assertEqual(masked.text_in('en'), 'Could not start: ' + proxy + ' x')
        self.assertEqual(messages._shown(raw(token_text), 'en'), proxy + ' x')
        self.assertEqual(messages._shown(token_text, 'en'), proxy + ' x')
        self.assertEqual(messages.show(masked), '无法开始：' + token_text, 'the zh UI keeps the stored token')


# ============================================================================ constructors

class ConstructorTests(unittest.TestCase):
    def test_msg_renders_the_zh_catalog_text_with_zh_params(self):
        m = msg('test.wait', reason=msg('test.pace'), seconds=3)
        self.assertEqual(m, '间隔，等待 3 秒')
        self.assertEqual(msg('test.multi', count=2, title=msg('test.name')), '名字 等 2 条')
        self.assertEqual(msg('test.english', mode=repr('x')), "Unsupported: 'x'", 'a zh text may be English')

    def test_msg_never_raises_for_a_missing_key_or_placeholder(self):
        logger = logging.getLogger('YtDlpDesktop.i18n')
        with self.assertLogs(logger, 'WARNING'):
            self.assertEqual(msg('core.no.such.key.for.test'), 'core.no.such.key.for.test')
        with self.assertLogs(logger, 'WARNING'):
            self.assertEqual(msg('test.too_long', length=3), '过长（3 个字符）：{url}')

    def test_raw_and_clauses(self):
        self.assertFalse(hasattr(messages, 'value') or hasattr(messages, 'Value'), '(2.0.0 S2 P4) no Value any more')
        self.assertEqual(raw(''), '')
        self.assertIs(type(raw('')), str)
        self.assertIs(type(raw('x')), Raw)
        self.assertEqual(messages.clauses([]), '')
        one = msg('test.first')
        self.assertIs(messages.clauses([one]), one)
        joined = messages.clauses([msg('test.second'), 'plain'])
        self.assertEqual(joined, '第二句。；plain')
        self.assertEqual(joined.key, 'common.clauses')
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
        m = msg('test.start', error=msg('test.too_long', length=9, url='u'))
        for item in (m, raw('[代理] x'), 'plain', msg('test.name')):
            self.assertEqual(messages.show(item), str.__str__(item))
            self.assertIs(type(messages.show(item)), str)
        self.assertEqual(messages.show(None), '')
        self.assertEqual(messages.show(5), '5')
        # (final review, ZH-2) a stored list value: str(value) in zh, as 1.7.0's localize gave it (2.6)
        parts = ['一行文字', 'x']                 # (no app text: the joined form would be '一行文字；x')
        self.assertEqual(messages.show(parts), str(parts))
        self.assertEqual(messages.error_text(ValueError(parts)), str(parts))

    def test_en_renders_keys_values_and_raw_tokens(self):
        m = msg('test.start', error=msg('test.too_long', length=9, url='u'))
        self.assertEqual(en(m), 'Could not start: Too long (9 characters): u')
        self.assertEqual(en(msg('test.name')), 'Name')
        # the stored proxy token is shown as the English of core.proxy_mask
        proxy = _sandbox.en_t('core.proxy_mask')
        self.assertNotEqual(proxy, messages.PROXY_TOKEN)
        self.assertEqual(_sandbox.zh_t('core.proxy_mask'), messages.PROXY_TOKEN, 'zh shows the stored token itself')
        self.assertEqual(en(raw('[代理] 无响应')), proxy + ' 无响应')
        self.assertEqual(en('[代理] x'), proxy + ' x')
        self.assertEqual(en(msg('test.multi', count=2, title=msg('test.name'))), 'Name and others (2 links)')
        joined = messages.clauses([msg('test.first'), msg('test.wait', reason=msg('test.pace'), seconds=3)])
        self.assertEqual(en(joined), 'The first sentence. Pacing, waiting 3 s.')
        self.assertEqual(en(Message('自定义')), '自定义', 'a keyless message shows its text')

    def test_error_text_and_never_raises(self):
        self.assertEqual(messages.error_text(ValueError(msg('test.plain'))), '找不到记录。')
        with _sandbox.language('en'):
            self.assertEqual(messages.error_text(ValueError(msg('test.plain'))), 'Record not found.')
            self.assertEqual(messages.error_text(OSError(5, '拒绝访问。')), '[Errno 5] 拒绝访问。')
            broken = Message('zh text', 'core.engine.error', {'error': object()})
            with mock.patch.object(i18n, '_t_in', side_effect=RuntimeError('broken')):  # (2.0.1) _render calls _t_in
                self.assertEqual(messages.show(broken), 'zh text')


# ============================================================================ IPC (2.8)

class WireTests(unittest.TestCase):
    """(2.0.0, S2-DESIGN.md 1.6) {"$m": {"k", "p"}}: a message keeps its key and params (nested messages
    and the parts of common.clauses too); any other text arrives as its plain text."""

    def test_round_trip(self):
        page = {'title': msg('value.link_kind.direct'),
                'links': [{'url': 'u', 'title': raw('Song'), 'kind': 'media'},
                          {'url': 'v', 'title': msg('value.link_kind.dynamic_request'), 'kind': 'media'}],
                'error': messages.clauses([msg('core.collect.too_large'), raw('外来')]),
                'mode': msg('core.engine.cannot_start_link', error=msg('core.links.too_long', length=2100, url='y'))}
        line = json.dumps(['collect_page', messages.wire(page)], ensure_ascii=False)
        kind, back = json.loads(line)
        self.assertEqual(json.loads(line)[1]['title'], {'$m': {'k': 'value.link_kind.direct'}})
        self.assertNotIn('$r', line)
        self.assertNotIn('$v', line)
        back = messages.unwire(back)
        self.assertEqual(back, page)
        self.assertEqual(back['title'].key, 'value.link_kind.direct')
        self.assertIs(type(back['links'][0]['title']), str)
        self.assertEqual(back['error'].key, messages.CLAUSES)
        self.assertEqual(back['error'].params['parts'][0].key, 'core.collect.too_large')
        self.assertEqual(back['error'].params['parts'][1], '外来')
        self.assertEqual(back['mode'].params['error'].key, 'core.links.too_long')
        self.assertEqual(back['mode'].params['error'].params['length'], 2100)
        self.assertEqual(en(back['mode']), en(page['mode']))
        self.assertEqual(messages.unwire(messages.wire('plain')), 'plain')
        self.assertEqual(messages.wire(Message('keyless')), 'keyless')

    def test_malformed_markers_raise_value_error(self):
        for bad in ({'$m': 'x'}, {'$m': ['t', 'k', {}, 'live']}, {'$m': {'k': ''}}, {'$m': {'k': 'a', 'x': 1}},
                    {'$m': {'k': 'a', 'p': []}}, [{'$m': {'p': {}}}]):
            with self.assertRaises(ValueError):
                messages.unwire(bad)
        self.assertEqual(messages.unwire({'$m': 1, 'other': 2}), {'$m': 1, 'other': 2})
        self.assertEqual(messages.unwire({'$v': ['g', 'x']}), {'$v': ['g', 'x']}, 'the 1.8.x markers are gone')


# ============================================================================ the format-2 store form (S2-DESIGN.md 1.1)

class FormatTwoTests(unittest.TestCase):
    """to_json / from_json / read_slots / write_slots (2.0.0): a message is {"k", "p"} and comes back with its key
    and params; any other text is its text; a key of a later version shows the key and is written back as it
    was; a param that cannot be stored makes the whole message its zh text (one WARNING), never an exception."""

    def test_round_trip(self):
        message = msg('core.engine.waiting', reason=msg('core.wait.rate_retry', attempt=1, limit=2), seconds=3)
        form = messages.to_json(message)
        self.assertEqual(form, {'k': 'core.engine.waiting', 'p': {'reason': {'k': 'core.wait.rate_retry',
                                                                              'p': {'attempt': 1, 'limit': 2}},
                                                                   'seconds': 3}})
        back = messages.from_json(json.loads(json.dumps(form, ensure_ascii=False)))
        self.assertEqual((back, back.key, back.params['reason'].key), (message, message.key, 'core.wait.rate_retry'))
        self.assertEqual(en(back), en(message))
        self.assertEqual(messages.to_json(back), form)
        joined = messages.clauses([msg('core.collect.needs_chrome'), raw('外来')])
        form = messages.to_json(joined)
        self.assertEqual(form, {'k': messages.CLAUSES, 'p': {'parts': [{'k': 'core.collect.needs_chrome'}, '外来']}})
        back = messages.from_json(form)
        self.assertEqual((back, type(back.params['parts'])), (joined, tuple))
        self.assertEqual(en(back), en(joined))
        self.assertEqual(messages.to_json(msg('core.reason.network')), {'k': 'core.reason.network'}, 'no empty "p"')
        for text in (raw('Some Song'), 'plain', Message('keyless')):
            self.assertEqual(messages.to_json(text), str.__str__(text))
            self.assertIs(type(messages.to_json(text)), str)
        self.assertIsNone(messages.to_json(None))
        self.assertIs(type(messages.from_json('Clip')), Raw)
        self.assertEqual(messages.from_json(''), '')

    def test_a_key_of_a_later_version_and_odd_forms(self):
        later = {'k': 'core.no.such.key', 'p': {'x': 1, 'inner': {'k': 'core.future.inner'}}}
        back = messages.from_json(later)
        self.assertEqual((messages.zh(back), back.key), ('core.no.such.key', 'core.no.such.key'))
        with _sandbox.language('en'):
            self.assertEqual(messages.show(back), 'core.no.such.key')
        self.assertEqual(messages.to_json(back), later, 'written back as it was')
        extra = messages.from_json({'k': 'test.plain', 'p': {'extra': 1}})
        self.assertEqual((en(extra), messages.to_json(extra)), ('Record not found.',
                                                                {'k': 'test.plain', 'p': {'extra': 1}}))
        for odd in ([1], 3, {'kk': 1}, {'k': 'a', 'x': 1}, {'k': ''}, {'k': 'a', 'p': []}, None):
            self.assertEqual(messages.from_json(odd), odd)
        # (S2 review F3) A {"k", "p"} whose params break the grammar of DATA.md section 1 (P := int | float |
        # str | M; a list only as the parts of common.clauses) is another shape: returned as it is and
        # written back unchanged, never flattened into text that loses the params.
        for odd in ({'k': 'x', 'p': {'n': None, 't': True}},
                    {'k': 'core.library.kept_aside', 'p': {'problem': 1.5, 'target': [1, 2]}},
                    {'k': messages.CLAUSES, 'p': {'parts': 'not a list'}},
                    {'k': messages.CLAUSES, 'p': {'parts': ['a', None]}},
                    {'k': 'core.engine.error', 'p': {'error': {'k': 'core.reason.network', 'p': {'x': False}}}},
                    {'k': 'core.engine.error', 'p': {'error': ['a']}}):
            back = messages.from_json(odd)
            self.assertNotIsInstance(back, Message, odd)
            self.assertEqual(messages.to_json(back), odd)
            record = {'errors': [{'error': odd, 'reason': None}]}
            slots = (('errors', '*', 'error'),)
            again = messages.write_slots(messages.read_slots(record, slots), slots)
            self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(record, sort_keys=True), odd)
        for good in ({'k': 'core.engine.error', 'p': {'error': {'k': 'core.reason.network'}}},
                     {'k': messages.CLAUSES, 'p': {'parts': [{'k': 'core.reason.network'}, 'x', 2, 1.5]}},
                     {'k': 'core.no.such.key', 'p': {'a': 1, 'b': 'x', 'c': {'k': 'also.unknown'}}}):
            back = messages.from_json(good)
            self.assertIsInstance(back, Message, good)
            self.assertEqual(messages.to_json(back), good)

    def test_a_param_that_cannot_be_stored(self):
        for params in ({'flag': True}, {'none': None}, {'thing': object()}):
            broken = Message('文字', 'core.reason.network', params)
            self.assertEqual(messages.to_json(broken), '文字', params)
            self.assertEqual(messages.to_json(msg('test.error', error=broken)), '错误：文字',
                             'a nested one makes the whole message its text')

    def test_slots(self):
        message = msg('test.first')
        record = {'errors': [{'error': message, 'reason': None}, {'error': raw('x'), 'reason': ''}],
                  'options': {'title': message}}
        slots = (('errors', '*', 'error'), ('errors', '*', 'reason'))
        written = messages.write_slots(record, slots)
        self.assertEqual(written['errors'], [{'error': {'k': 'test.first'}, 'reason': None},
                                             {'error': 'x', 'reason': ''}])
        self.assertIs(written['options'], record['options'], 'off the slots: the same object, never read as text')
        self.assertIs(record['errors'][0]['error'], message, 'the record itself is not changed')
        read = messages.read_slots(json.loads(json.dumps(written, ensure_ascii=False)), slots)
        self.assertEqual(read['errors'][0]['error'].key, 'test.first')
        # (2.0.0 S2 Q1, from test_upgrade_v2.TextTests) a slot read back shows in English as the message did
        self.assertEqual(en(read['errors'][0]['error']), en(message))
        self.assertIs(type(read['errors'][1]['error']), Raw)
        self.assertEqual(read['options'], {'title': '第一句。'})
        self.assertNotIsInstance(read['options']['title'], Message)
        self.assertEqual(messages.read_slots('not a record', slots), 'not a record')


# ============================================================================ catalogs and frozen data

class ScrubTests(unittest.TestCase):
    def test_release_sources_are_pinned_and_scrubbed(self):
        """(2.0.0 S2 Q4) The scan for this machine's user name, user folders and e-mail addresses (PLAN
        3.11) runs on verification/golden (the recordings), with a probe that it finds what it should;
        the pins of the frozen released sources went with them.  The whole tree is scanned in S5."""
        golden = _sandbox.ROOT / 'verification' / 'golden'
        # what it scans: the recordings and (2.0.1) the one allow list of the Chinese literals of src
        names = {path.name for path in golden.glob('*.json')}
        self.assertTrue({'ui.json', 'events.json', 'stored-keys.json', 'text-allow.json'} <= names, names)
        self.assertTrue(json.loads((golden / 'text-allow.json').read_text(encoding='utf-8')), 'text-allow.json reads')
        self.assertEqual(i18n_tool.scrub_findings([golden]), [])
        self.assertTrue(i18n_tool.scrub_patterns(), 'the patterns come from the environment')
        with mock.patch.dict(os.environ, {'USERNAME': 'zz-probe-user'}):
            self.assertIn(('user name', 'zz-probe-user'), i18n_tool.scrub_patterns())
        probe = Path(os.environ['TEMP']) / 'scrub-probe'
        probe.mkdir(exist_ok=True)
        (probe / 'a.py').write_text("X = 'mail me at someone@example.org'\n", encoding='utf-8')
        self.assertEqual(i18n_tool.scrub_findings([probe]), [('a.py', 'e-mail address')])
        # A user name and a profile path in either slash form are found too (a made-up machine).
        (probe / 'b.json').write_text('{"out": "C:/Users/zz-probe-user/Videos"}\n', encoding='utf-8')
        (probe / 'c.txt').write_text('saved in C:\\Users\\zz-probe-user\\Videos\n', encoding='utf-8')
        machine = {'USERNAME': 'zz-probe-user', 'USERPROFILE': 'C:\\Users\\zz-probe-user'}
        self.assertEqual(sorted(i18n_tool.scrub_findings([probe], environ=machine)),
                         [('a.py', 'e-mail address'), ('b.json', 'USERPROFILE'), ('b.json', 'user name'),
                          ('c.txt', 'USERPROFILE'), ('c.txt', 'user name')])


if __name__ == '__main__':
    unittest.main()
