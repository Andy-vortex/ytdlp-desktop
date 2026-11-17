"""1.7.0 language adaptation: the catalogs, the maps and localize (I18N.md 11.2, WP-L0).

    python -X utf8 -B test_i18n_catalog.py

Reads the inventory copy verification/i18n_inventory.json (written by i18n_tool.py catalog from
docs/1.7.0/spec/strings-inventory.json) and the sources.  The checks that need a converted UI
module (completeness, every key used, suspects) apply to a module once it imports i18n (the L1
work); until then they print what is still open.  No window, no network.
"""
import _sandbox
import ast
import importlib
import io
import logging
import re
import string
import time
import unittest
from collections import Counter
from contextlib import redirect_stdout

import i18n
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
        used |= set(i18n_maps.LOCALIZE) | set(i18n_maps.RESERVED)
        used |= {key for mapping in i18n_maps.GROUPS.values() for key in mapping.values() if key}
        used |= {row['key'] for row in self.table}
        for key, _template in i18n_maps.VARIANTS:
            used.add(key)
        pending = []
        for name, module in modules().items():
            users = i18n_tool.MODULE_USERS[name]
            if not all(converted(file) for file in users):
                pending.append(f'i18n_{name} ({len(set(module.ZH) - used)} keys wait for {", ".join(users)})')
                continue
            self.assertEqual(sorted(set(module.ZH) - used), [], f'i18n_{name}: keys nothing uses')
        print('key use pending: ' + ('; '.join(pending) or 'none'))


class CoreParityTests(unittest.TestCase):
    """Item 6: the core keeps its canonical Chinese texts and LOCALIZE knows every one of them."""

    @classmethod
    def setUpClass(cls):
        cls.records = i18n_tool.cjk_literals()
        cls.shapes = {i18n_tool.record_shape(record) for record in cls.records}
        cls.used = {call['name'] for call in i18n_tool.api_calls() if call['kind'] == 'key'}

    def test_6_every_localize_template_occurs_in_the_sources(self):
        for key in i18n_maps.LOCALIZE:
            template = i18n.CATALOG['zh'][key]
            self.assertTrue(i18n_tool.shape(template) in self.shapes or key in self.used,
                            f'{key}: {template!r} is not produced by any source line')
        for key, template in i18n_maps.VARIANTS:
            self.assertIn(key, i18n_maps.LOCALIZE)
            self.assertEqual(fields(template), fields(i18n.CATALOG['zh'][key]))

    def test_6_every_cjk_literal_of_a_core_file_is_known(self):
        by_file = {}
        for entry in INVENTORY['entries']:
            by_file.setdefault(entry['file'].split('/')[-1], []).append(entry)
        grouped = {key for mapping in i18n_maps.GROUPS.values() for key in mapping.values() if key}
        localize = set(i18n_maps.LOCALIZE)
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
                                f'{record["file"]}:{record["line"]} {key} is neither in LOCALIZE nor shown by key')


class LocalizeTests(unittest.TestCase):
    """Item 7 to 10: localize round trips, t() fallbacks, speed, suspects."""

    # Captured values that are core texts themselves (I18N.md 9.2 RECURSE), by key.
    SAMPLE = {'reason': 'core.wait.pace', 'error': 'core.reason.http_404', 'mode': 'value.mode.audio_mp3',
              'name': 'value.subscription.untitled'}

    def sample(self, name, number):
        if name in i18n_maps.NUMERIC:
            return str(number)
        if name == 'problem':
            return i18n.CATALOG['zh']['core.unfinished.problem.dropped'].format(dropped=number)
        if name in self.SAMPLE:
            return i18n.CATALOG['zh'][self.SAMPLE[name]]
        return f'Sample{name.capitalize()}'

    def expected(self, key, values):
        shown = {}
        for name, value in values.items():
            if name in i18n_maps.RECURSE:
                shown[name] = i18n.localize(value)
            else:
                shown[name] = value
        return i18n.t(key, **shown)

    def test_7_every_template_and_variant_round_trips(self):
        templates = [(key, i18n.CATALOG['zh'][key]) for key in i18n_maps.LOCALIZE] + list(i18n_maps.VARIANTS)
        failures = []
        with _sandbox.language('en'):
            for number in (1, 23):
                for key, template in templates:
                    values = {name: self.sample(name, number) for name in fields(template)}
                    text = FORMATTER.vformat(template, (), values)
                    want = self.expected(key, values)
                    got = i18n.localize(text)
                    if got != want:
                        failures.append(f'{key}: {text!r} -> {got!r}, expected {want!r}')
        self.assertEqual(failures, [])

    def test_7_the_cases_of_9_2(self):
        with _sandbox.language('en'):
            self.assertEqual(i18n.localize('网站返回 HTTP 403，可尝试动态模式或稍后重试。；页面超过 8 MB，请缩小采集范围。'),
                             'The site returned HTTP 403. Try dynamic mode or try again later. '
                             'The page is larger than 8 MB. Narrow the collection scope.')
            self.assertEqual(i18n.localize('3条 · 音频 · MP3 320K'), 'Links: 3 · Audio · MP3 320K')
            self.assertEqual(i18n.localize('1 条 · 音频 · MP3 320K'), 'Links: 1 · Audio · MP3 320K')
            self.assertEqual(i18n.localize('周杰伦 等 群星 等 3 条'), '周杰伦 等 群星 and others (3 links)')
            self.assertEqual(i18n.localize('订阅：未命名订阅'), 'Subscription: Untitled subscription')
            self.assertEqual(i18n.localize('检查中断；下次重试，不更新已见记录'),
                             i18n.t('value.subscription.interrupted'))
            job_title = ('value.job_title.', 'value.subscription.untitled')
            self.assertEqual(i18n.localize('检查未完成', keys=job_title), '检查未完成')
            self.assertEqual(i18n.localize('检查未完成'), 'Check not finished')
            self.assertEqual(i18n.localize('未命名订阅', keys=job_title), 'Untitled subscription')
            self.assertEqual(i18n.localize('网页', keys=('value.page_title.',)), 'Web page')
            self.assertEqual(i18n.localize('网页正文', keys=('value.page_title.',)), '网页正文')
            # nested: a waiting reason inside the engine's waiting line, a problem inside the kept-aside note
            self.assertEqual(i18n.localize('网站限流，暂停整个下载队列，等待 300 秒；可随时点击停止。'),
                             'The site is rate-limiting; pausing the whole queue: waiting 300 s; you can stop at any time.')
            kept = [key for key in i18n_maps.LOCALIZE if key == 'core.unfinished.kept_aside']
            if kept:
                template = i18n.CATALOG['zh'][kept[0]]
                values = {name: self.sample(name, 4) for name in fields(template)}
                text = FORMATTER.vformat(template, (), values)
                self.assertNotIn('有 2 条记录无法识别', i18n.localize(text))
            # unknown text and raw engine output pass unchanged; the proxy mask is a token
            for raw in ('ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies',
                        '[download]  12.5% of 3.00MiB at 1.2MiB/s ETA 00:02', '周杰伦 - 晴天', ''):
                self.assertEqual(i18n.localize(raw), raw)
            self.assertEqual(i18n.localize('[代理] 无响应'), '[proxy] 无响应')
            self.assertEqual(i18n.localize(None), '')
            self.assertEqual(i18n.localize(5), '5')
        for text in ('网页', '3条 · 音频 · MP3 320K', '已内置'):
            self.assertEqual(i18n.localize(text), text, 'zh: identity')

    def test_7_the_collector_texts_1_5_1_stored(self):
        """(final fix round 1, ENV-2 / zh-2) 1.5.1 wrote its collector texts without the spaces 1.6.0 put
        around Latin text and numbers; the owner's shared library.sqlite3 keeps them (collect job errors,
        subscription last_error).  Every CJK literal of the 1.5.1 collector.py (an f-string rendered with
        a number) reads English, also '；'-joined as collection_ui stores two of them."""
        backup = i18n_tool.ROOT / 'verification' / 'backup-before-1.6.0-20261115' / 'src' / 'collector.py'
        tree = ast.parse(backup.read_text(encoding='utf-8'))
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
            if CJK.search(text):
                texts.append(text)
        self.assertGreaterEqual(len(texts), 15)
        with _sandbox.language('en'):
            left = [(text, i18n.localize(text)) for text in texts if CJK.search(i18n.localize(text))]
            self.assertEqual(left, [], 'a 1.5.1 collector text stays Chinese')
            self.assertEqual(i18n.localize('网站返回HTTP 503，可尝试动态模式或稍后重试。；页面超过8MB，请缩小采集范围。'),
                             'The site returned HTTP 503. Try dynamic mode or try again later. '
                             'The page is larger than 8 MB. Narrow the collection scope.')
            self.assertEqual(i18n.localize('采集进程退出（-1），请检查页面或动态模式所需的Chrome。'),
                             i18n.t('core.collect.worker_exit', code='-1'))
        for text in texts:
            self.assertEqual(i18n.localize(text), text, 'zh: identity')

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

    def test_9_ten_thousand_lines_in_under_a_second(self):
        lines = ['下载未完成（退出码 {}）', '此链接未完成（退出码 {}），继续处理队列中的下一项。', '[download] {}% of 3MiB',
                 '文件已保存：C:\\x\\{}.mp3', '网站限流，冷却后重试此链接（{}/2）', '错误：页面 {} 不存在；稍后重试',
                 'ERROR: [generic] {}: Unable to download webpage', '周杰伦 等 {} 条']
        mixed = [lines[n % len(lines)].format(n) for n in range(10000)]
        with _sandbox.language('en'):
            started = time.perf_counter()
            for line in mixed:
                i18n.localize(line)
            seconds = time.perf_counter() - started
        print(f'10,000 mixed lines localized in {seconds:.3f} s')
        self.assertLess(seconds, 1.0)

    def test_10_suspects_are_fixed_or_allowed_in_converted_modules(self):
        pending = []
        for item in i18n_tool.unexplained_suspects():
            if converted(item['file']):
                self.fail(f'{item["file"]}:{item["line"]} shows {item["expr"]} without localize/label/t')
            pending.append(f'{item["file"]}:{item["line"]} {item["expr"]}')
        for (file, expr), reason in i18n_tool.SUSPECT_ALLOW.items():
            self.assertIn(file, i18n_tool.UI_FILES)
            self.assertTrue(reason)
        print('suspects pending (module does not use i18n yet): ' + ('; '.join(pending) or 'none'))


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
        self.assertEqual(list(i18n_maps.LOCALIZE), plan['localize'])
        self.assertEqual({group: dict(mapping) for group, mapping in plan['groups'].items()},
                         {group: dict(mapping) for group, mapping in i18n_maps.GROUPS.items()})
        self.assertEqual([tuple(item) for item in plan['variants']], [tuple(item) for item in i18n_maps.VARIANTS])

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
