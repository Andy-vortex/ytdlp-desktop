"""G3 legacy closure (MESSAGES.md 7.4, WP-1): every text an old version could have stored is lifted.

    python -X utf8 -B test_legacy_texts.py

The corpus verification/golden/legacy-corpus.json lists every stored-text shape the frozen
producers of 1.5.1 / 1.6.0 / 1.7.0 (verification/release-sources) can make, with its category
decided once in WP-1.  This file re-extracts it, instantiates every message / title / name /
job-title shape with samples and requires the legacy reader to lift it (en rendering without
Chinese outside the samples, zh value unchanged), also after the stored transforms (ERROR strip,
the cuts at 70 / 200 / 900, a proxy token in a raw capture, two messages joined with '；').  The
self-test removes the frozen 1.5.1 variants, and restricts the title kind to 1.7.0's first
filter, and shows the closure flags exactly what 1.7.0's reviews found by hand.
"""
import _sandbox
import hashlib
import json
import re
import string
import unittest

import i18n
import i18n_legacy
import i18n_tool
import messages

FORMATTER = string.Formatter()
CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
# The corpus as WP-1 decided it; regenerating it (i18n_tool.py corpus) changes this pin on purpose.
CORPUS_SHA256 = 'e9d981935675c26d1dc539013ea243f59166f896bba02cde218cbdb2e3531447'
HOLE = re.compile(r'\{([^{}]*)\}')
SAMPLE_TEXT = 'Qq'
NESTED = '链接不存在（404）'
SAMPLES = {'reason': NESTED, 'error': NESTED, 'mode': '音频 · FLAC', 'name': SAMPLE_TEXT,
           'problem': '有 4 条记录无法识别'}
STORED_CATEGORIES = ('message', 'title', 'name', 'job_title.collect', 'job_title.titled', 'job_title.untitled')
JOB_CONTEXT = {'job_title.collect': {'kind': 'collect'},
               'job_title.titled': {'kind': 'download', 'titled': True, 'links': 3},
               'job_title.untitled': {'kind': 'download', 'titled': False, 'links': 3}}


def corpus():
    return json.loads(i18n_tool.CORPUS.read_text(encoding='utf-8'))


def templates_by_shape(reader):
    found = {}
    for key, template in reader.templates:
        found.setdefault(i18n_tool.shape(template), []).append((key, template))
    return found


def instantiate(template, long_last=None, token=False):
    """The template filled with samples (digits for NUMERIC names, a nested message for RECURSE
    names, 'Qq' otherwise); ``long_last`` fills the last text capture with that text."""
    names = [field for _literal, field, _spec, _conv in FORMATTER.parse(template) if field is not None]
    text_names = [name for name in names if name not in i18n_legacy.DATA.NUMERIC and name not in ('mode', 'problem')]
    values = {}
    for name in names:
        if name in i18n_legacy.DATA.NUMERIC:
            values[name] = '12'
        else:
            values[name] = SAMPLES.get(name, SAMPLE_TEXT)
    if token and text_names:
        values[text_names[0]] = 'Qq [代理] Qq'
    if long_last is not None and text_names:
        values[text_names[-1]] = long_last
    return FORMATTER.vformat(template, (), values)


def english(text, kind, context=None):
    decoded = messages._decode_text(text, kind, context, {})
    with _sandbox.language('en'):
        return decoded, messages.show(decoded)


def closure(items, reader, kinds=None):
    """[(shape, problem)] of corpus items the reader cannot lift (G3)."""
    by_shape = templates_by_shape(reader)
    problems = []
    kinds = kinds or STORED_CATEGORIES
    for item in items:
        category = item['category']
        if category not in kinds:
            continue
        text = item['shape']
        if category == 'message' and text.startswith('{part}'):
            continue                                              # the joiners: checked by the pair test
        candidates = by_shape.get(i18n_tool.shape(text), [])
        if not candidates:
            problems.append((text, 'no frozen template has this shape'))
            continue
        key, template = candidates[0]
        sample = instantiate(template)
        kind = {'title': 'title', 'name': 'name'}.get(category, 'job_title' if category.startswith('job_title') else 'error')
        found = reader.lift(kind, sample, i18n_legacy._context_key(kind, JOB_CONTEXT.get(category)))
        if found is None:
            problems.append((text, f'{kind}: not lifted'))
    return problems


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = corpus()
        cls.items = cls.corpus['shapes']
        cls.by_category = {}
        for item in cls.items:
            cls.by_category.setdefault(item['category'], []).append(item)

    def test_1_re_extraction_equals_the_corpus(self):
        extracted = {item['shape'] for item in i18n_tool.corpus_extract()}
        recorded = {item['shape'] for item in self.items}
        self.assertEqual(sorted(extracted - recorded), [], 'a shape the corpus does not have')
        self.assertEqual(sorted(recorded - extracted), [], 'a corpus shape no release makes')
        for item in self.items:
            self.assertIn(item['category'], i18n_tool.CORPUS_CATEGORIES, item['shape'])
            self.assertTrue(item['reason'], item['shape'])
            if item['category'] == 'not_stored':
                self.assertTrue(item['reason'].startswith(i18n_tool.NOT_STORED_KINDS), item)
        self.assertGreater(len(self.by_category['message']), 100)

    def test_1_an_indirect_catalog_key_fails_the_extraction(self):
        extractor = i18n_tool._CorpusExtractor('9.9.9', {'a.key': 'x'})
        probe = _sandbox.out('corpus-probe', 'ui_download.py')
        probe.write_text("import i18n\ndef f(key):\n    return i18n.t(key)\n", encoding='utf-8')
        with self.assertRaises(i18n_tool.CorpusError):
            extractor.run(probe)
        probe.write_text("def g(c):\n    return stored_text('a.key' if c else 'a.key')\n", encoding='utf-8')
        extractor.run(probe)

    def test_2_every_message_shape_is_lifted_with_its_transforms(self):
        reader = i18n_legacy.reader()
        by_shape = templates_by_shape(reader)
        failures = []
        for item in self.by_category['message']:
            text = item['shape']
            if text.startswith('{part}'):
                continue
            candidates = by_shape.get(i18n_tool.shape(text))
            if not candidates:
                failures.append((text, 'no frozen template'))
                continue
            key, template = candidates[0]
            samples = [('error', instantiate(template), None), ('error', instantiate(template, token=True), 'token')]
            if text.startswith('ERROR: '):
                samples.append(('error', instantiate(template)[len('ERROR: '):].strip(), 'ERROR strip'))
            literal_before_last = self.literal_before_last_text_hole(template)
            if literal_before_last is not None and literal_before_last >= i18n_legacy.MIN_CUT_LITERAL:
                long = instantiate(template, long_last='Qq ' * 400)
                samples += [('error', long[:200], 'cut 200'), ('error', long[:900], 'cut 900'),
                            ('reason', long[:70], 'cut 70')]
            for kind, sample, transform in samples:
                decoded, shown = english(sample, kind)
                if not isinstance(decoded, messages.Message) or decoded.key is None:
                    failures.append((text, transform or 'plain', 'not lifted'))
                    continue
                if str.__str__(decoded) != sample:
                    failures.append((text, transform, 'the zh value is not the stored text'))
                left = CJK.findall(shown.replace(NESTED, ''))
                if left:
                    failures.append((text, transform, f'Chinese left in en: {shown!r}'))
                if transform == 'token' and '[proxy]' not in shown and '[代理]' in sample:
                    failures.append((text, transform, f'the proxy token is not shown: {shown!r}'))
        self.assertEqual(failures, [])

    @staticmethod
    def literal_before_last_text_hole(template):
        parts = list(FORMATTER.parse(template))
        names = [field for _literal, field, _spec, _conv in parts]
        text_positions = [index for index, name in enumerate(names) if name is not None
                          and name not in i18n_legacy.DATA.NUMERIC and name not in ('mode', 'problem')]
        if not text_positions:
            return None
        last = text_positions[-1]
        return sum(len(parts[index][0]) for index in range(last + 1))

    def test_2_every_pair_of_message_shapes_joined_with_the_clause_joiner(self):
        reader = i18n_legacy.reader()
        by_shape = templates_by_shape(reader)
        texts = []
        for item in self.by_category['message']:
            candidates = by_shape.get(i18n_tool.shape(item['shape']))
            if candidates and not item['shape'].startswith('{part}'):
                sample = instantiate(candidates[0][1])
                if sample not in texts:
                    texts.append(sample)
        failures = []
        count = 0
        with _sandbox.language('en'):
            for first in texts:
                for second in texts:
                    joined = first + '；' + second
                    count += 1
                    decoded = messages._decode_text(joined, 'clauses', None, {})
                    if not isinstance(decoded, messages.Message) or decoded.key is None:
                        failures.append((joined, 'not lifted'))
                        continue
                    if str.__str__(decoded) != joined:
                        failures.append((joined, 'the zh value is not the stored text'))
                    shown = messages.show(decoded)
                    if CJK.findall(shown.replace(NESTED, '')):
                        failures.append((joined, f'Chinese left in en: {shown!r}'))
        self.assertEqual((len(failures), failures[:20]), (0, []))
        self.assertGreater(len(texts), 80)
        self.assertEqual(count, len(texts) ** 2)

    def test_3_titles_names_and_job_titles_are_lifted_by_their_kind(self):
        self.assertEqual(closure(self.items, i18n_legacy.reader(), kinds=('title', 'name', 'job_title.collect',
                                                                           'job_title.titled', 'job_title.untitled')), [])
        keys = set()
        for item in self.by_category['title']:
            found = i18n_legacy.lift('title', item['shape'])
            keys.add(found[0])
        self.assertEqual(keys, set(i18n_legacy.DATA.TITLE_KEYS), 'TITLE_KEYS equals the title shapes (L7)')
        self.assertEqual({item['shape'] for item in self.by_category['title']}, {'网页', '媒体直链', '动态页面媒体请求'})
        self.assertEqual([item['shape'] for item in self.by_category['name']], ['未命名订阅'])
        with _sandbox.language('en'):
            for category, context in JOB_CONTEXT.items():
                for item in self.by_category[category]:
                    candidates = templates_by_shape(i18n_legacy.reader())[i18n_tool.shape(item['shape'])]
                    shown = messages.show(messages._decode_text(instantiate(candidates[0][1]), 'job_title', context, {}))
                    self.assertFalse(CJK.findall(shown), (item['shape'], shown))

    def test_4_pins(self):
        digest = hashlib.sha256(i18n_tool.CORPUS.read_bytes()).hexdigest()
        self.assertEqual(digest, CORPUS_SHA256, 'the corpus changed: review it and update the pin')
        for version, tree in self.corpus['releases'].items():
            self.assertEqual(i18n_tool._sha256_tree(_sandbox.release_src(version)), tree, version)
        pins = json.loads(i18n_tool.RELEASE_PINS.read_text(encoding='utf-8'))['releases']
        self.assertEqual(i18n_tool.release_hashes(), pins)

    def test_self_test_without_the_frozen_variants(self):
        released = i18n_tool._module_constants(_sandbox.release_src('1.7.0') / 'i18n_maps.py', {'VARIANTS'})['VARIANTS']
        self.assertEqual(len(released), 11)
        variants = {tuple(item) for item in released}
        reader = i18n_legacy.Reader(templates=[item for item in i18n_legacy.DATA.TEMPLATES if tuple(item) not in variants])
        flagged = sorted(text for text, _problem in closure(self.items, reader))
        expected = sorted(item['shape'] for item in self.items
                          if item['category'] in ('message', 'job_title.untitled')
                          and any(source.startswith('1.5.1/') for source in item['sources'])
                          and not any(source.startswith(('1.6.0/', '1.7.0/')) for source in item['sources'])
                          and i18n_tool.shape(item['shape']) in {i18n_tool.shape(t) for _k, t in variants})
        self.assertEqual(len(flagged), 11, flagged)
        self.assertEqual(flagged, expected)
        self.assertIn('{len(urls)}条 · {options.mode}', flagged)

    def test_self_test_with_the_first_round_title_filter(self):
        reader = i18n_legacy.Reader(title_keys=('value.page_title.',))
        flagged = sorted(text for text, _problem in closure(self.items, reader, kinds=('title',)))
        self.assertEqual(flagged, ['动态页面媒体请求', '媒体直链'])


if __name__ == '__main__':
    unittest.main()
