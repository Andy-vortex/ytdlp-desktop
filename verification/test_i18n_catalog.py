"""1.7.0 language adaptation: the catalogs, the maps and localize (I18N.md 11.2, WP-L0).

    python -X utf8 -B test_i18n_catalog.py

Reads the inventory copy verification/i18n_inventory.json (written by i18n_tool.py catalog from
docs/1.7.0/spec/strings-inventory.json) and the sources.  The checks that need a converted UI
module (completeness, every key used) apply to a module once it imports i18n (the L1 work); until
then they print what is still open.  No window, no network.

(1.8.0 WP-1, MESSAGES.md 6.4 / 8.3) The localize tests (round trips, the 9.2 cases, the 1.5.1
collector forms, the timing) moved to test_messages.py as legacy-reader tests; test 6 reads the
frozen release sources (the legacy corpus) as well as src; test 10 (the suspects scan) is replaced
by the G5 sink rule of test_text_guard.py.  The inventory has the section new_strings_1_8_0.

(1.8.0 WP-6, MESSAGES.md 6.4) i18n.localize and the reverse sections of i18n_maps (LOCALIZE,
VARIANTS, NUMERIC, RECURSE) are gone.  The checks that read them now read what replaced them: the
keys the core names in msg() calls and the frozen legacy templates of i18n_legacy_data (the 1.7.0
LOCALIZE keys and VARIANTS, generated from release-sources/1.7.0); the generated-files check
compares the whole i18n_maps.py with what the generator writes.
"""
import _sandbox
import ast
import importlib
import io
import json
import logging
import re
import string
import unittest
from collections import Counter

import i18n
import i18n_legacy_data
import i18n_maps
import i18n_tool

FORMATTER = string.Formatter()
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
# English values that may hold CJK: the language names and the bracket examples (I18N.md 11.2 item 3).
CJK_ALLOWED_KEYS = ('settings.language.zh', 'settings.language.title')
CJK_ALLOWED_CHARS = set('【】『』')
INVENTORY = i18n_tool.load_inventory(i18n_tool.INVENTORY)


def fields(text):
    return {field for _literal, field, _spec, _conv in FORMATTER.parse(text) if field is not None}


def modules():
    return {name: importlib.import_module(f'i18n_{name}') for name in i18n_tool.MODULES}


def converted(file):
    return i18n_tool.imports_i18n(i18n_tool.SRC / file)


def msg_keys():
    """(1.8.0) Every key a src module names in a msg() call (a literal or a conditional of literals)."""
    keys = set()
    for path in sorted(i18n_tool.SRC.glob('*.py')):
        if path.stem in i18n_tool.G1_EXCLUDED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and i18n_tool._func_name(node)[0] == 'msg' and node.args:
                keys |= set(i18n_tool._key_literals(node.args[0]) or ())
    keys.discard('common.clauses')
    return keys


def legacy_keys():
    """(1.8.0) The keys of the frozen legacy templates (what 1.7.0's LOCALIZE and VARIANTS named)."""
    return {key for key, _template in i18n_legacy_data.TEMPLATES}


class CatalogTests(unittest.TestCase):
    """Items 1 to 4 and 11 of I18N.md 11.2, and the L0 acceptance "every inventory key in both languages"."""

    def test_1_same_keys_in_zh_and_en_and_no_key_in_two_modules(self):
        seen = Counter()
        for name, module in modules().items():
            self.assertEqual(list(module.ZH), sorted(module.ZH), f'i18n_{name}.ZH is sorted')
            self.assertEqual(set(module.ZH), set(module.EN), f'i18n_{name}: ZH and EN have the same keys')
            seen.update(list(module.ZH))
        self.assertEqual([key for key, count in seen.items() if count > 1], [])
        self.assertEqual(set(i18n.CATALOG['zh']), set(seen))
        self.assertEqual(set(i18n.CATALOG['en']), set(seen))

    def test_every_inventory_key_is_in_both_catalogs_with_its_texts(self):
        keys = i18n_tool.keyed_texts(INVENTORY)
        for key, item in keys.items():
            self.assertEqual(i18n.CATALOG['zh'].get(key), item['zh'], key)
            self.assertIn(key, i18n.CATALOG['en'], key)
        self.assertEqual(set(keys), set(i18n.CATALOG['zh']), 'the catalogs hold the inventory keys and no other')
        new = {item['key'] for item in INVENTORY['new_strings_1_7_0']}
        self.assertEqual(set(i18n_maps.NEW_KEYS), new)

    def test_2_placeholders_are_the_same_and_every_text_parses(self):
        for key, zh in i18n.CATALOG['zh'].items():
            en = i18n.CATALOG['en'][key]
            self.assertEqual(fields(zh), fields(en), key)
            for text in (zh, en):
                for name in fields(text):
                    self.assertRegex(name, r'^[A-Za-z_][A-Za-z_0-9]*$', f'{key}: placeholders are plain names')
                for _literal, field, spec, conversion in FORMATTER.parse(text):
                    if field is not None:
                        self.assertEqual((spec, conversion), ('', None), f'{key}: no format spec or conversion')

    def test_3_no_cjk_in_english_except_the_allowed(self):
        for key, en in i18n.CATALOG['en'].items():
            if key in CJK_ALLOWED_KEYS:
                continue
            found = [char for char in en if CJK.match(char) and char not in CJK_ALLOWED_CHARS]
            self.assertEqual(found, [], f'{key}: {en!r}')
        self.assertEqual(i18n.CATALOG['en']['settings.language.zh'], '简体中文')
        self.assertEqual(i18n.CATALOG['en']['settings.language.title'], 'Language / 语言')
        self.assertEqual(i18n.CATALOG['zh']['settings.language.title'], '语言 / Language')

    def test_4_no_always_wrong_phrase_in_english(self):
        phrases = INVENTORY['always_wrong_en_phrases']
        self.assertIn('unfinished list', phrases)
        self.assertIn('Follow system', phrases)
        for key, en in i18n.CATALOG['en'].items():
            for phrase in phrases:
                self.assertNotIn(phrase, en, key)

    def test_8_3_a_label_after_the_separator_is_capitalised(self):
        """(final fix round 1, ENV-5) 'Label: n' parts joined by ' · ' start with a capital (I18N.md 8.3)."""
        lowercase = re.compile(r' · ([a-z][^·:{}]*): \{')
        found = [(key, en) for key, en in i18n.CATALOG['en'].items() if lowercase.search(en)]
        self.assertEqual(found, [])
        with _sandbox.language('en'):
            self.assertEqual(i18n.t('download.hint.empty_lines', empty=1), ' · Lines without a link: 1')

    def test_11_new_keys_groups_and_stored_values(self):
        literal_keys = {entry['key'] for entry in INVENTORY['entries'] if entry.get('key')}
        self.assertEqual(set(i18n_maps.NEW_KEYS) & literal_keys, set(), 'a 1.7.0 key never reuses a 1.6.0 key')
        for group, mapping in i18n_maps.GROUPS.items():
            for value, key in mapping.items():
                if key is not None:
                    self.assertIn(key, i18n.CATALOG['zh'], f'{group}: {value!r}')
        import engine
        import formats
        import naming
        import collection_ui
        import library_ui
        groups = i18n_maps.GROUPS
        self.assertEqual(list(groups['mode']), list(engine.MODES))
        self.assertEqual(list(groups['format']), [formats.by_id(i).label for i in formats.MENU_ORDER])
        self.assertEqual(set(groups['format']), {preset.label for preset in formats.PRESETS})
        self.assertEqual(list(groups['resolution']), list(engine.RESOLUTIONS))
        self.assertEqual(list(groups['naming']), list(naming.NAMING))
        self.assertEqual(set(groups['naming']), set(naming.LABELS))
        for value, text in naming.LABELS.items():
            self.assertEqual(i18n.CATALOG['zh'][groups['naming'][value]], text)
        for preset in formats.PRESETS:
            self.assertEqual(i18n.CATALOG['zh'][groups['format'][preset.label]], preset.label)
            self.assertEqual(i18n.CATALOG['zh'][groups['mode'][preset.mode]], preset.mode)
        self.assertEqual(list(groups['interval']), list(library_ui.INTERVALS.values()))
        self.assertEqual(list(groups['job_status']), list(library_ui.STATES))
        self.assertEqual([i18n.CATALOG['zh'][key] for key in groups['job_status'].values()],
                         list(library_ui.STATES.values()))
        self.assertEqual(list(groups['collect_mode']), [collection_ui.MODE_STATIC, collection_ui.MODE_DYNAMIC])
        self.assertEqual(list(groups['collect_filter']), list(collection_ui.FILTERS))
        self.assertEqual(list(groups['library_tab']), ['任务历史与续采', '增量订阅', '统一资料归档'])
        self.assertEqual(set(groups['link_kind']), {'媒体地址', '视频页面', '网页链接', '媒体直链', '动态页面媒体请求'})
        import ui_download
        import ui_settings
        self.assertEqual(set(groups['row_state']), set(ui_download.STATE_LOOK))
        for state, look in ui_download.STATE_LOOK.items():
            self.assertEqual(i18n.CATALOG['zh'][groups['row_state'][state]], look[2], state)
        self.assertEqual(set(groups['appearance']), set(ui_settings.APPEARANCE_LABELS))
        self.assertEqual(list(groups['language']), list(i18n.CHOICES))
        for value, text in ui_settings.APPEARANCE_LABELS.items():
            self.assertEqual(i18n.CATALOG['zh'][groups['appearance'][value]], text)

    def test_the_log_tags_name_existing_keys(self):
        import i18n_log
        for key, tag in i18n_log.TAGS.items():
            self.assertIn(key, i18n.CATALOG['zh'])
            self.assertIn(tag, ('error', 'ok'))


class ParityTests(unittest.TestCase):
    """Item 5 and the constant parity of I18N.md 8.1."""

    @classmethod
    def setUpClass(cls):
        cls.table = i18n_tool.parity_table(inventory=INVENTORY)

    def test_the_parity_table_pairs_every_constant_with_its_key(self):
        self.assertGreater(len(self.table), 50)
        names = {(row['module'], row['name']) for row in self.table}
        for expected in (('ui_download', 'FINISHED_UNFINISHED'), ('ui_download', 'STATE_LOOK'), ('naming', 'LABELS'),
                         ('collection_ui', 'SUBTITLE'), ('library_ui', 'INTERVALS'), ('studio', 'TITLE'),
                         ('studio', 'StudioApp.naming_label')):
            self.assertIn(expected, names)
        for row in self.table:
            self.assertIsNotNone(row['key'], row)
            self.assertIn(row['relation'], ('equal', 'prefix'), row)
            zh = i18n.CATALOG['zh'][row['key']]
            self.assertEqual(zh, row['zh'], row)
            if row['relation'] == 'equal':
                self.assertEqual(zh.replace('{{', '{').replace('}}', '}'), row['value'], row)
            else:
                self.assertEqual(next(iter(FORMATTER.parse(zh)))[0], row['value'], row)
        naming_label = [row for row in self.table if row['name'] == 'StudioApp.naming_label']
        self.assertEqual({row['key'] for row in naming_label}, {'naming.clean'})

    def test_5_every_cjk_literal_of_a_converted_ui_module_is_a_parity_constant_or_allowed(self):
        spans = i18n_tool.parity_definitions(table=self.table)
        allowed = {(entry['file'].split('/')[-1], entry['text']) for entry in INVENTORY['entries']
                   if not entry['translate']}
        pending = []
        for file in i18n_tool.UI_FILES:
            if not converted(file):
                pending.append(file)
                continue
            for record in i18n_tool.cjk_literals(files=(file,)):
                inside = any(first <= record['line'] <= last for first, last in spans.get(file, ()))
                self.assertTrue(inside or (file, record['text']) in allowed,
                                f'{file}:{record["line"]} {record["text"]!r} is neither a parity constant nor allowed')
        print('completeness pending (module does not use i18n yet): ' + (', '.join(pending) or 'none'))

    def test_5_every_api_call_names_an_existing_key_or_group(self):
        calls = i18n_tool.api_calls()
        for call in calls:
            if call['kind'] == 'key':
                self.assertIn(call['name'], i18n.CATALOG['zh'], call)
            else:
                self.assertIn(call['name'], i18n_maps.GROUPS, call)
        self.assertIn(('app.py', 'all_values', 'app.title'),
                      {(call['file'], call['function'], call['name']) for call in calls})

    def test_5_every_catalog_key_is_used_or_reserved(self):
        used = {call['name'] for call in i18n_tool.api_calls() if call['kind'] == 'key'}
        # (1.8.0 WP-6) the core's msg() keys and the frozen legacy templates (they render what older
        # versions stored) replace the LOCALIZE / VARIANTS maps of 1.7.0
        used |= msg_keys() | legacy_keys() | set(i18n_maps.RESERVED)
        used |= {item['key'] for item in INVENTORY[i18n_tool.NEW_SECTION_1_8_0]}
        used |= {key for mapping in i18n_maps.GROUPS.values() for key in mapping.values() if key}
        used |= {row['key'] for row in self.table}
        pending = []
        for name, module in modules().items():
            users = i18n_tool.MODULE_USERS[name]
            if not all(converted(file) for file in users):
                pending.append(f'i18n_{name} ({len(set(module.ZH) - used)} keys wait for {", ".join(users)})')
                continue
            self.assertEqual(sorted(set(module.ZH) - used), [], f'i18n_{name}: keys nothing uses')
        print('key use pending: ' + ('; '.join(pending) or 'none'))


class CoreParityTests(unittest.TestCase):
    """Item 6: the core keeps its canonical Chinese texts, and (1.8.0) every one of them is a msg() key
    or a frozen legacy template."""

    @classmethod
    def setUpClass(cls):
        cls.records = i18n_tool.cjk_literals()
        cls.shapes = {i18n_tool.record_shape(record) for record in cls.records}
        cls.used = {call['name'] for call in i18n_tool.api_calls() if call['kind'] == 'key'}
        # (1.8.0 WP-1) the stored-text shapes of the frozen 1.5.1 / 1.6.0 / 1.7.0 producers: once a
        # producer emits msg(key), its literal leaves src but the shape stays in the corpus.
        corpus = json.loads(i18n_tool.CORPUS.read_text(encoding='utf-8'))
        cls.corpus = {i18n_tool.shape(item['shape']) for item in corpus['shapes']}

    def test_6_every_frozen_legacy_template_occurs_in_the_sources(self):
        """(1.8.0 WP-6) The 1.7.0 test 6 read LOCALIZE / VARIANTS; they live on as the frozen legacy
        templates.  Each names a catalog key with the same placeholders and is produced by a source
        line, a msg() call or a released source (the corpus)."""
        produced = self.used | msg_keys()
        templates = list(i18n_legacy_data.TEMPLATES)
        self.assertGreater(len(templates), 100)
        for key, template in templates:
            self.assertIn(key, i18n.CATALOG['zh'], key)
            self.assertEqual(fields(template), fields(i18n.CATALOG['zh'][key]), key)
            self.assertTrue(i18n_tool.shape(template) in self.shapes or key in produced
                            or i18n_tool.shape(template) in self.corpus,
                            f'{key}: {template!r} is not produced by any source line or released source')

    def test_6_every_cjk_literal_of_a_core_file_is_known(self):
        by_file = {}
        for entry in INVENTORY['entries']:
            by_file.setdefault(entry['file'].split('/')[-1], []).append(entry)
        grouped = {key for mapping in i18n_maps.GROUPS.values() for key in mapping.values() if key}
        localize = legacy_keys() | msg_keys()          # (1.8.0) what replaced LOCALIZE
        for record in self.records:
            if record['file'] not in i18n_tool.CORE_FILES:
                continue
            shape = i18n_tool.record_shape(record)
            matches = [entry for entry in by_file.get(record['file'], [])
                       if entry['text'] == record['text'] or
                       (entry.get('zh_template') and i18n_tool.shape(entry['zh_template']) == shape)]
            self.assertTrue(matches, f'{record["file"]}:{record["line"]} {record["text"]!r} has no inventory entry')
            for entry in matches:
                key = entry.get('key')
                if not entry['translate']:
                    continue
                self.assertTrue(key in localize or key in grouped or key == 'app.title' or key.startswith('filetext.'),
                                f'{record["file"]}:{record["line"]} {key} is neither a message / legacy key nor shown by key')


class LookupTests(unittest.TestCase):
    """Item 8: t() fallbacks (the localize tests of items 7 and 9 are in test_messages.py since 1.8.0)."""

    def test_8_missing_key_and_missing_placeholder(self):
        logger = logging.getLogger('YtDlpDesktop.i18n')
        with self.assertLogs(logger, 'WARNING') as logs:
            self.assertEqual(i18n.t('no.such.key.for.test'), 'no.such.key.for.test')
            self.assertEqual(i18n.t('no.such.key.for.test'), 'no.such.key.for.test')
            logger.warning('end marker')
        self.assertEqual(len([line for line in logs.output if 'no.such.key.for.test' in line]), 1)
        saved = i18n.CATALOG['en'].pop('download.title.ready')
        try:
            with _sandbox.language('en'), self.assertLogs(logger, 'WARNING') as logs:
                self.assertEqual(i18n.t('download.title.ready'), i18n.CATALOG['zh']['download.title.ready'])
                self.assertEqual(i18n.t('download.title.ready'), i18n.CATALOG['zh']['download.title.ready'])
                logger.warning('end marker')
            self.assertEqual(len([line for line in logs.output if 'download.title.ready' in line]), 1)
        finally:
            i18n.CATALOG['en']['download.title.ready'] = saved
        with _sandbox.language('en'):
            self.assertEqual(i18n.t('download.link_count', count=3), 'Links: 3')
            self.assertEqual(i18n.t('download.link_count'), 'Links: {count}')
            self.assertEqual(i18n.t('download.link_count', count=3, extra='ignored'), 'Links: 3')
        self.assertEqual(i18n.t('download.link_count', count=3), '3 条链接')
        self.assertEqual(i18n.t(['not', 'a', 'key']), "['not', 'a', 'key']", 't never raises')
        i18n.CATALOG['en']['test.broken'] = 'broken {count'
        try:
            with _sandbox.language('en'):
                self.assertEqual(i18n.t('test.broken', count=1), 'broken {count')
        finally:
            del i18n.CATALOG['en']['test.broken']

class HelperTests(unittest.TestCase):
    def test_label_choices_all_values_prefixes_and_joiners(self):
        self.assertEqual(i18n.label('mode', '音频 · MP3 320K'), '音频 · MP3 320K')
        self.assertEqual(i18n.label('mode', 'unknown mode'), 'unknown mode')
        self.assertEqual(i18n.label('nothing', 3), '3')
        self.assertEqual(i18n.label('interval', [1]), '[1]')
        with _sandbox.language('en'):
            self.assertEqual(i18n.label('mode', '音频 · MP3 320K'), 'Audio · MP3 320K')
            self.assertEqual(i18n.label('format', '原始音频 · 保留源编码'), i18n.t('format.audio_best'))
            self.assertEqual(i18n.label('resolution', '1080p'), '1080p')
            self.assertEqual(i18n.label('resolution', '最佳'), 'Best')
            self.assertEqual(i18n.label('interval', 900), 'Every 15 minutes')
            self.assertEqual(i18n.choices('language'), [('system', 'System'), ('zh', '简体中文'), ('en', 'English')])
            self.assertEqual(i18n.sep('clause_sep'), '; ')
            self.assertEqual(i18n.sep('sentence_join'), ' ')
            self.assertEqual(i18n.join_sentences(['The page is gone', 'Retry later.', 'Why?', 'Kept (3)']),
                             'The page is gone. Retry later. Why? Kept (3)')
        self.assertEqual(i18n.sep('clause_sep'), '；')
        self.assertEqual(i18n.sep('sentence_join'), '')
        self.assertEqual(i18n.join_sentences(['a', 'b']), 'a；b')
        self.assertEqual(i18n.all_values('app.title'), ('YT-DLP 下载器', 'YT-DLP Downloader'))
        self.assertEqual(i18n.all_values('settings.language.zh'), ('简体中文',))
        errors = i18n.prefixes('error')
        for start in ('此链接未完成（退出码 ', 'This link did not finish (exit code ', 'Error: ', '错误：'):
            self.assertIn(start, errors)
        self.assertIn('File saved: ', i18n.prefixes('ok'))
        self.assertIn('Download complete · Succeeded: ', i18n.prefixes('ok'))

    def test_the_generated_files_are_current(self):
        """i18n_maps.py and the catalogs are what i18n_tool.py catalog makes of the inventory copy."""
        plan = i18n_tool.plan(INVENTORY)
        self.assertEqual(plan['problems'], [])
        for name, keys in plan['modules'].items():
            module = importlib.import_module(f'i18n_{name}')
            self.assertEqual(sorted(module.ZH), keys, f'i18n_{name} has the keys the inventory gives it')
        self.assertEqual({group: dict(mapping) for group, mapping in plan['groups'].items()},
                         {group: dict(mapping) for group, mapping in i18n_maps.GROUPS.items()})
        # (1.8.0 WP-6) the whole generated map module, without the reverse sections (MESSAGES.md 6.4)
        self.assertEqual((i18n_tool.SRC / 'i18n_maps.py').read_text(encoding='utf-8'),
                         i18n_tool._maps_module(plan['groups'], plan['reserved'], plan['new_keys']))
        for name in ('LOCALIZE', 'VARIANTS', 'NUMERIC', 'RECURSE'):
            self.assertFalse(hasattr(i18n_maps, name), name)

    def test_i18n_imports_no_app_module(self):
        tree = ast.parse((i18n_tool.SRC / 'i18n.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        local = {name for name in imported if (i18n_tool.SRC / f'{name}.py').is_file()}
        self.assertEqual(local, {'i18n_maps'} | {f'i18n_{name}' for name in i18n_tool.MODULES})
        for name in local:
            module_tree = ast.parse((i18n_tool.SRC / f'{name}.py').read_text(encoding='utf-8'))
            self.assertFalse([node for node in ast.walk(module_tree) if isinstance(node, (ast.Import, ast.ImportFrom))],
                             f'{name} imports nothing')


if __name__ == '__main__':
    unittest.main()
