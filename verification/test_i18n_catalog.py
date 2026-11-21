# quick
"""The hand-written catalogs src/i18n_<part>.py and the keys the code names (2.0.0 S3-DESIGN.md 2.1).

    python -X utf8 -B test_i18n_catalog.py

(2.0.0 S3 W2) Every catalog file is the single source of its texts: TEXTS {key: ('中文', 'English')}, sorted
by key, and optionally GROUPS (menu value -> key) and TAGS (key -> log-line tag) for its own keys; i18n merges
them.  The inventory verification/i18n_inventory.json, its generator and i18n_maps.py are gone; the rules of
CatalogRuleTests check the catalogs directly and every failure says which file and line to change and how:

    C1  the shape of a catalog file (only TEXTS / GROUPS / TAGS / RETIRED, literals, no duplicate key, sorted)
    C2  a key lives in one file, the file of its prefix; TAGS name keys of their own file; group names are unique
    C3  key names        C4  placeholders        C5  no Chinese in English        C6  nothing left untranslated
    C7  English wording (the always-wrong phrases, capitals after ' · ')
    C8  every key and group the code names exists, t() gets the placeholders of its text      (S3 W1)
    C9  every key is used                                                                      (S3 W1)
    C10 golden/stored-keys.json is a contract     C10b the kept-aside texts keep their facts
    C12 i18n imports the catalogs only and they import nothing
    C13 the log tags colour a key's lines alike in Chinese and English (i18n.log_tag, ui_log.classify)

(2.0.0 S4 A1) C11 went with formats.Preset.label: a format has one name, i18n.t('format.' + id) (test_11 checks
that every preset has it in both languages).

(2.0.0 S3 W1) ParityTests went with the Chinese constants only tests read; the CJK literals of src are checked by
test_source_rules.py (S1).  No window, no network.
"""
import _sandbox
import ast
import difflib
import importlib
import json
import logging
import re
import string
import unittest

import i18n
import i18n_tool

FORMATTER = string.Formatter()
CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
# (C5) English values that may hold CJK: the language names and the bracket examples (I18N.md 11.2 item 3).
CJK_ALLOWED_KEYS = ('settings.language.zh', 'settings.language.title')
CJK_ALLOWED_CHARS = set('【】『』')
# (C7) English the glossary rules out (1.7.0 docs/1.7.0/spec/GLOSSARY.md; moved here from the inventory's
# always_wrong_en_phrases, unchanged).  The term to use instead: Downloads, Unfinished (list), Retry, Cannot,
# System, Clean title, Add to queue, YouTube verification, cookies file, Media link, Resume, Done / Partly done,
# Skipped, Held, Settings, YT-DLP Downloader, Library archive, Subscriptions.
ALWAYS_WRONG_EN = ('Download Center', 'Download Hub', 'Failed list', 'failed list', 'Retry failed', 'Unable to',
                   'Follow system', 'Short title', 'Enqueue', 'bot detection', 'captcha', 'Cookie jar', 'cookie jar',
                   'Media address', 'unfinished list', 'Continue unfinished', 'Partial', 'Already downloaded',
                   'Deferred', 'Preferences', 'YT-DLP Desktop', 'Unified material archive', 'Incremental subscription')
# (C1) The module-level names a catalog file may have.
CATALOG_NAMES = ('TEXTS', 'GROUPS', 'TAGS', 'RETIRED')
LOG_TAGS = ('error', 'ok')
# (C3, S3-DESIGN.md 1.3) The first parts a key may have, and (C2, 1.2) the src catalog file of a key by its prefix
# (the longest prefix wins; log. / dialog. / filetext. keys outside the table live with the page that shows them,
# in i18n_common.py when two pages do: advice, not checked, S3-DESIGN.md 3.3 item 5).
FIRST_PARTS = frozenset('download collect library settings nav shell window common table error core value format '
                        'naming app unit log dialog filetext'.split())
KEY_SHAPE = re.compile(r'^[a-z][a-z0-9]*(\.[a-z0-9_]+)+$')
NAME_SHAPE = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*$')
FILE_SUFFIXES = frozenset({'json', 'sqlite3', 'log', 'py', 'ico', 'txt', 'exe'})     # 'settings.json' is a file
STRUCTURAL = frozenset({'common.clauses'})      # messages.clauses: 'parts' and no catalog text
PREFIX_FILES = (('value.link_kind.', 'collect'), ('dialog.filetype.', 'common'), ('dialog.restart_', 'settings'),
                ('log.restart_', 'settings'), ('core.', 'core'), ('value.', 'core'), ('format.', 'core'),
                ('naming.', 'core'), ('app.', 'core'), ('unit.', 'core'), ('common.', 'common'), ('table.', 'common'),
                ('error.', 'common'), ('nav.', 'shell'), ('shell.', 'shell'), ('window.', 'shell'),
                ('download.', 'download'), ('collect.', 'collect'), ('library.', 'library'),
                ('settings.', 'settings'))
# (C10b, PLAN 4.7) Texts that tell the user a file was kept aside (or could not be): the facts they must keep.
# The wording may change and shrink; the key and these placeholders stay.
MUST_SAY = {
    'core.library.kept_aside': {'target'},
    'core.library.cannot_set_aside': {'path'},
    'core.library.not_v2': set(),
    'core.unfinished.kept_aside': {'target'},
    'core.unfinished.cannot_set_aside': {'path'},
    'core.archive.kept_aside': {'target', 'folder'},
    'core.archive.cannot_set_aside': {'record'},
    'core.archive.not_v2': set(),
}


# (C10, S3 review fix) The stored keys of 2.0.0.  A user's library and Unfinished list hold them, so
# golden/stored-keys.json may only grow: removing or renaming a key there also changes this set, where a reviewer
# sees it.
STORED_2_0_0 = frozenset("""
    common.clauses common.unfinished core.archive.cannot_set_aside core.archive.media_mismatch
    core.archive.outside_root core.archive.transcript_outside core.archive.transcript_type core.archive.unwritable
    core.collect.bad_address core.collect.bad_proxy core.collect.bad_resume core.collect.bad_source
    core.collect.ca_cache core.collect.http_status core.collect.limits core.collect.needs_chrome
    core.collect.no_match core.collect.page_n core.collect.too_large core.collect.too_many_pages
    core.collect.worker_exit core.cookies.json core.cookies.line_failed core.cookies.line_failed_item
    core.cookies.line_skipped core.cookies.missing core.cookies.not_netscape core.cookies.too_large
    core.engine.archive_info_failed core.engine.bot_check_hint core.engine.bot_check_stale
    core.engine.cannot_start_engine core.engine.cannot_start_link core.engine.duplicate_merged core.engine.error
    core.engine.incompatible core.engine.item_unfinished core.engine.kept_existing core.engine.no_collect_history
    core.engine.other_format_source core.engine.picture_exists core.engine.present_skipped
    core.engine.present_untagged core.engine.rate_limited_out core.engine.record_unreadable
    core.engine.resume_partial core.engine.subtitles_dropped core.engine.unfinished_not_saved core.engine.waiting
    core.formats.bad_id core.formats.bad_mode core.library.bad_interval core.library.no_job core.library.temporary
    core.links.bad_line core.links.encoding core.links.file_too_large core.links.no_link_on_line core.links.none
    core.links.too_long core.options.bad_resolution core.options.no_output core.options.not_object
    core.options.unsupported core.proxy.scheme core.proxy_mask core.reason.bot_check core.reason.engine_error
    core.reason.exit_code core.reason.format core.reason.http_403 core.reason.http_404 core.reason.http_410
    core.reason.network core.reason.page_403 core.reason.page_404 core.reason.page_410 core.reason.region
    core.reason.stale core.reason.unavailable core.reason.unsupported core.rename.failed core.rename.failed_named
    core.rename.locked core.rename.too_many core.unfinished.cannot_set_aside core.unfinished.kept_aside
    core.unfinished.no_overflow_name core.unfinished.overflow core.unfinished.problem.damaged
    core.unfinished.problem.dropped core.unfinished.problem.too_large core.unfinished.problem.unknown_format
    core.unfinished.problem.unreadable core.update.bad_hash core.update.bad_host core.update.bad_release
    core.update.bad_size core.update.builtin core.update.checking core.update.downloading core.update.incompatible
    core.update.no_exe core.update.no_github core.update.no_sha core.update.record_failed core.update.restore_failed
    core.update.save_failed core.update.start_cannot core.update.start_exit core.update.start_failed
    core.update.start_no_version core.update.start_timeout core.update.testing core.update.timeout
    core.update.write_failed core.wait.pace core.wait.pace_or_retry core.wait.rate_queue core.wait.rate_retry
    download.reason.blocked download.reason.failed download.reason.proxy value.job_title.multi
    value.job_title.subscription value.link_kind.direct value.link_kind.dynamic_request value.link_kind.media
    value.link_kind.video_page value.link_kind.web value.page_title.fallback value.resolution.best
    value.resolution.heights value.subscription.interrupted value.subscription.stopped value.subscription.unfinished
    value.subscription.untitled
""".split())
# (C9) The catalog files shared by the pages: a page that stops using one of their keys while the pages are changed
# in parallel writes RETIRED = ('key',) in its own src/i18n_<page>.py; the integrator deletes the key.
SHARED_FILES = ('core', 'common', 'shell')


def fields(text):
    return {field for _literal, field, _spec, _conv in FORMATTER.parse(text) if field is not None}


def suggestion(name):
    """'是不是 'log.heading'？' for a key name that is close to an existing one, else ''."""
    close = difflib.get_close_matches(name, list(i18n.CATALOG['zh']), n=1, cutoff=0.8)
    return f"是不是 '{close[0]}'？" if close else ''


def unused_problems(loaded, used, where_=None):
    """(C9) The problems of the catalog files ``loaded`` ({part: module with TEXTS and maybe RETIRED}) whose keys are
    not in ``used``.  RETIRED is the union over every file: a page writes the shared keys it stopped using in its
    own file."""
    where_ = where_ or where
    retired = {}
    for name, module in loaded.items():
        for key in getattr(module, 'RETIRED', ()):
            retired.setdefault(key, name)
    texts = {key for module in loaded.values() for key in module.TEXTS}
    problems = []
    for key, name in sorted(retired.items()):
        if key in used:
            problems.append(f"src/i18n_{name}.py 的 RETIRED 里有 '{key}'，但代码还在用它：从 RETIRED 去掉")
        elif key not in texts:
            problems.append(f"src/i18n_{name}.py 的 RETIRED 里有 '{key}'，但目录里没有这个编号：从 RETIRED 去掉")
    check = '如果是刚改的调用处换掉了它，先确认调用处没有换错；确实不再用了：'
    for name, module in loaded.items():
        for key in sorted(set(module.TEXTS) - used - set(retired)):
            if name in SHARED_FILES:
                how = (f"删掉这一条。几个页面并行修改、你只负责其中一页时，不删公共文件：在你那一页的 "
                       f"src/i18n_<页>.py 里写 RETIRED = ('{key}',)，由集成人删")
            else:
                how = '删掉这一条'
            problems.append(f"{where_(key)} '{key}' 没有任何代码用到。{check}{how}"
                            f"（存储编号不会报这条；动态拼出的编号要让拼法里出现完整编号）")
    return problems


def catalog_paths():
    """{part: path} of the src catalog files src/i18n_<part>.py."""
    return {path.stem[len('i18n_'):]: path for path in sorted(i18n_tool.SRC.glob('i18n_*.py'))}


def modules():
    return {name: importlib.import_module(f'i18n_{name}') for name in catalog_paths()}


def top_level(path):
    """{name: Assign node} of the module-level assignments of a catalog file."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {node.targets[0].id: node for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}


def key_lines(path, name='TEXTS'):
    """{key: line} of a literal dict of a catalog file."""
    node = top_level(path).get(name)
    if node is None or not isinstance(node.value, ast.Dict):
        return {}
    return {key.value: key.lineno for key in node.value.keys if isinstance(key, ast.Constant)}


def prefix_part(key):
    """(prefix, part) of the catalog file a key's prefix names, or None (S3-DESIGN.md 1.2)."""
    for prefix, name in PREFIX_FILES:
        if key.startswith(prefix):
            return prefix, name
    return None


def catalog_file(key):
    """The src catalog file a new key goes into (S3-DESIGN.md 1.2)."""
    found = prefix_part(key)
    if found:
        return f'src/i18n_{found[1]}.py'
    return '显示它的那一页的 src/i18n_<页>.py（两页以上用到时放 src/i18n_common.py）'


def where(key):
    """'src/i18n_x.py:line' of a catalog key."""
    for name, path in catalog_paths().items():
        line = key_lines(path).get(key)
        if line:
            return f'src/{path.name}:{line}'
    return 'src/i18n_*.py'


def stored_keys():
    """(2.0.0 S2 P4) The keys a stored text can carry (golden/stored-keys.json: never removed or renamed)."""
    return set(json.loads((_sandbox.GOLDEN / 'stored-keys.json').read_text(encoding='utf-8'))['keys'])


def src_constants():
    """(file, line, text) of every str constant of src outside the catalogs (CATALOG_STEMS), docstrings left out."""
    found = []
    for path in sorted(i18n_tool.SRC.glob('*.py')):
        if path.stem in i18n_tool.CATALOG_STEMS:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        docstrings = i18n_tool.docstring_ids(tree)
        found += [(path.name, node.lineno, node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]
    return found


def computed_msg_keys():
    """(2.0.1 C8(d)) [(file, line, key expression)] of the msg() / messages.msg() calls of src outside the catalogs
    whose key is not a literal (or a conditional of literals): C8 cannot check such a key or its params."""
    found = []
    for path in sorted(i18n_tool.SRC.glob('*.py')):
        if path.name == 'i18n.py' or path.name.startswith('i18n_'):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            func = getattr(node, 'func', None)
            if isinstance(node, ast.Call) and node.args and (
                    (isinstance(func, ast.Name) and func.id == 'msg')
                    or (isinstance(func, ast.Attribute) and func.attr == 'msg'
                        and isinstance(func.value, ast.Name) and func.value.id == 'messages')) \
                    and i18n_tool._key_literals(node.args[0]) is None:
                found.append((path.name, node.lineno, ast.unparse(node.args[0])))
    return found


def key_shaped_constants():
    """(file, line, text) of the src constants shaped like a key (C8 b): KEY_SHAPE with one of the FIRST_PARTS,
    not a file name ('settings.json') and not a structural key."""
    return [(file, line, text) for file, line, text in src_constants()
            if KEY_SHAPE.match(text) and text.split('.')[0] in FIRST_PARTS
            and text.split('.')[-1] not in FILE_SUFFIXES and text not in STRUCTURAL]


def well_formed_keys():
    """The keys whose TEXTS value is a pair of strings (C1 reports the others; C4-C7 leave them out)."""
    return {key for module in modules().values() for key, pair in module.TEXTS.items()
            if isinstance(pair, tuple) and len(pair) == 2 and all(isinstance(text, str) for text in pair)}


def filled(text):
    """A log line of ``text``: every placeholder written as '1'."""
    return text.format(**{name: '1' for name in fields(text)})


class CatalogTests(unittest.TestCase):
    def test_11_groups_and_stored_values(self):
        """The groups' values are the code's values (S3: key sets, never texts; the texts are the catalog's)."""
        for group, mapping in i18n.GROUPS.items():
            for value, key in mapping.items():
                if key is not None:
                    self.assertIn(key, i18n.CATALOG['zh'], f'{group}: {value!r}')
        import engine
        import formats
        import naming
        import collection_ui
        import library_ui
        import settings
        groups = i18n.GROUPS
        # (S3 W3, 2.0.0 S4 A1) A format has one name: i18n.t('format.' + preset id), in the format menu, the
        # Unfinished rows and the log.  The menu lists every preset once; no 'mode' or 'format' group is left.
        self.assertNotIn('format', groups)
        self.assertNotIn('mode', groups)
        self.assertEqual(list(formats.MODES), list(engine.MODES))
        self.assertEqual(sorted(formats.MENU_ORDER), sorted(preset.id for preset in formats.PRESETS))
        for preset in formats.PRESETS:
            for language in i18n.LANGUAGES:
                self.assertTrue(i18n.CATALOG[language].get('format.' + preset.id), f'{language}: format.{preset.id}')
        self.assertEqual(list(groups['resolution']), list(engine.RESOLUTIONS))
        self.assertEqual(list(groups['naming']), list(naming.NAMING))
        # (S3 W3) The code's values are the groups' keys, in menu / tab order (studio.focus_page uses the tab order).
        # (2.0.0 S4) The schedules have one source, settings.SCHEDULES (library_ui.INTERVALS is gone).
        self.assertEqual(list(groups['interval']), list(settings.SCHEDULES))
        self.assertEqual(list(groups['job_status']), list(library_ui.STATES))
        self.assertEqual(list(groups['collect_mode']), [collection_ui.MODE_STATIC, collection_ui.MODE_DYNAMIC])
        self.assertEqual(list(groups['collect_filter']), list(collection_ui.FILTERS))
        self.assertEqual(list(groups['library_tab']),
                         [library_ui.TAB_HISTORY, library_ui.TAB_SUBSCRIPTIONS, library_ui.TAB_ARCHIVE])
        self.assertEqual(set(groups['link_kind']), {'media', 'video_page', 'web', 'direct', 'dynamic_request'})
        import ui_download
        import ui_settings
        self.assertEqual(set(groups['row_state']), set(ui_download.STATE_LOOK))
        self.assertEqual(list(groups['appearance']), list(ui_settings.APPEARANCE))
        self.assertEqual(list(groups['language']), list(i18n.CHOICES))


class CatalogRuleTests(unittest.TestCase):
    """(2.0.0 S3, S3-DESIGN.md 2.1) Rules on the catalogs and on the keys the code names.  Each test collects
    every problem and says where to fix it."""

    def test_c1_catalog_files_have_one_sorted_texts_dict(self):
        """Only TEXTS (required), GROUPS, TAGS and RETIRED, each a literal; TEXTS values are two str literals;
        no key twice in a dict literal (Python keeps the later one silently); TEXTS sorted by key; TAGS values
        'error' / 'ok'."""
        problems = []
        for name, path in catalog_paths().items():
            file = f'src/{path.name}'
            tree = ast.parse(path.read_text(encoding='utf-8'))
            assigned = {}
            for index, node in enumerate(tree.body):
                if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    continue                                   # the module docstring
                target = node.targets[0].id if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) else None
                if target not in CATALOG_NAMES:
                    problems.append(f'{file}:{node.lineno} 目录文件里只能有 TEXTS、GROUPS、TAGS、RETIRED 四个字面量'
                                    f'赋值：把这条语句挪到用它的模块里（目录文件什么都不导入，见 C12）')
                    continue
                if target in assigned:
                    problems.append(f'{file}:{node.lineno} {target} 赋值了两次（第 {assigned[target]} 行和第 '
                                    f'{node.lineno} 行）：合成一个')
                assigned[target] = node.lineno
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    problems += self._c1_not_literal(file, target, node.value)
                    continue
                for literal in ast.walk(node.value):
                    if isinstance(literal, ast.Dict):
                        first = {}
                        for key in literal.keys:
                            if not isinstance(key, ast.Constant):
                                continue
                            if key.value in first:
                                problems.append(f'{file}:{key.lineno} {key.value!r} 出现了两次（第 {first[key.value]} '
                                                f'行和第 {key.lineno} 行）：Python 会悄悄用后一条，删掉多余的一条')
                            else:
                                first[key.value] = key.lineno
                problems += self._c1_value(file, target, node, value)
            if 'TEXTS' not in assigned:
                problems.append(f'{file} 没有 TEXTS：每个目录文件都有 TEXTS = {{编号: (\'中文\', \'English\')}}')
        self.assertEqual(problems, [], '\n'.join(problems))

    @staticmethod
    def _c1_not_literal(file, target, node):
        """Where a name of a catalog file is not a literal: the entries of a dict that are not, else the name."""
        def literal(part):
            try:
                ast.literal_eval(part)
                return True
            except ValueError:
                return False
        how = '必须是字面量：不能有变量、表达式、函数调用或 f-string'
        if isinstance(node, ast.Dict):
            found = [f'{file}:{(key or item).lineno} {target} 里 '
                     f'{key.value if isinstance(key, ast.Constant) else ast.unparse(key or item)!r} 的值{how}'
                     for key, item in zip(node.keys, node.values)
                     if not (key is None or literal(key)) or not literal(item)]
            if found:
                return found
        return [f'{file}:{node.lineno} {target} {how}']

    @staticmethod
    def _c1_order(file, keys):
        """The keys out of alphabetical order: those outside one longest sorted run, each with where it belongs."""
        if all(before <= key for (before, _), (key, _) in zip(keys, keys[1:])):
            return []
        best, previous = [], []
        for index, (key, _line) in enumerate(keys):
            length, back = 1, -1
            for earlier in range(index):
                if keys[earlier][0] <= key and best[earlier] + 1 > length:
                    length, back = best[earlier] + 1, earlier
            best.append(length)
            previous.append(back)
        index = max(range(len(keys)), key=lambda item: best[item])
        kept = set()
        while index >= 0:
            kept.add(index)
            index = previous[index]
        problems = []
        ordered = [keys[index] for index in sorted(kept)]
        for index, (key, line) in enumerate(keys):
            if index in kept:
                continue
            after = [other for other, _ in ordered if other <= key]
            place = f"放在 '{after[-1]}' 之后" if after else f"放在 '{ordered[0][0]}' 之前"
            problems.append(f'{file}:{line} {key!r} 放错了位置：本文件的 TEXTS 按编号字母序排列，把它{place}')
        return problems

    @staticmethod
    def _c1_value(file, target, node, value):
        problems = []
        if target == 'TEXTS':
            if not isinstance(node.value, ast.Dict):
                return [f'{file}:{node.lineno} TEXTS 必须是字典字面量 {{编号: (\'中文\', \'English\')}}']
            keys = []
            for key, item in zip(node.value.keys, node.value.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    problems.append(f'{file}:{node.lineno} TEXTS 的编号必须是字符串字面量')
                    continue
                keys.append((key.value, key.lineno))
                if not (isinstance(item, ast.Tuple) and len(item.elts) == 2
                        and all(isinstance(part, ast.Constant) and isinstance(part.value, str) for part in item.elts)):
                    problems.append(f"{file}:{key.lineno} {key.value!r} 的值必须是 ('中文', 'English') 两个字符串"
                                    f"字面量")
            problems += CatalogRuleTests._c1_order(file, keys)
        elif target == 'GROUPS':
            if not isinstance(value, dict) or not all(isinstance(group, str) and isinstance(mapping, dict)
                                                      for group, mapping in value.items()):
                problems.append(f"{file}:{node.lineno} GROUPS 必须是 {{'组名': {{菜单值: '编号' 或 None}}}}")
            else:
                for group, mapping in value.items():
                    bad = [key for key in mapping.values() if key is not None and not isinstance(key, str)]
                    if bad:
                        problems.append(f"{file}:{node.lineno} GROUPS['{group}'] 的值只能是编号字符串或 None："
                                        f"{bad!r}")
        elif target == 'TAGS':
            if not isinstance(value, dict):
                problems.append(f"{file}:{node.lineno} TAGS 必须是 {{'编号': 'error' 或 'ok'}}")
            else:
                for key, tag in value.items():
                    if tag not in LOG_TAGS:
                        problems.append(f"{file}:{node.lineno} TAGS['{key}'] 是 {tag!r}：只能是 'error'（标红）或 "
                                        f"'ok'（标绿）")
        elif target == 'RETIRED':
            if not (isinstance(value, tuple) and all(isinstance(key, str) for key in value)):
                problems.append(f"{file}:{node.lineno} RETIRED 必须是编号的元组，例如 ('common.x',)")
        return problems

    def test_c2_each_key_lives_in_the_file_of_its_prefix(self):
        """A key is in one file, the file of its prefix (S3-DESIGN.md 1.2); a TAGS key is in its own file's TEXTS;
        a group name is in one file; the src catalog files are i18n.CATALOG_MODULES; CATALOG is their merge."""
        problems, owners, groups = [], {}, {}
        paths = catalog_paths()
        loaded = modules()
        for name, module in loaded.items():
            for key in module.TEXTS:
                owners.setdefault(key, []).append(name)
            for group in getattr(module, 'GROUPS', {}):
                groups.setdefault(group, []).append(name)
        for key, names in sorted(owners.items()):
            if len(names) > 1:
                problems.append(f"'{key}' 同时在 {' 和 '.join(f'src/i18n_{name}.py' for name in names)}：只保留一处")
            found = prefix_part(key)
            for name in names:
                if found and name != found[1]:
                    problems.append(f"src/i18n_{name}.py:{key_lines(paths[name]).get(key, '?')} '{key}' 在 "
                                    f"i18n_{name}.py：移到 src/i18n_{found[1]}.py（前缀 {found[0]} 的编号放在 "
                                    f"i18n_{found[1]}.py，见本文件的 PREFIX_FILES）")
        for name, module in loaded.items():
            for key in getattr(module, 'TAGS', {}):
                if key not in module.TEXTS:
                    home = owners.get(key)
                    problems.append(f"src/i18n_{name}.py:{key_lines(paths[name], 'TAGS').get(key, '?')} TAGS 里的 "
                                    f"'{key}' 不在本文件的 TEXTS 里：" +
                                    (f'标记放在编号所在的 src/i18n_{home[0]}.py 的 TAGS 里' if home
                                     else '目录里没有这个编号，删掉这条标记'))
        for group, names in sorted(groups.items()):
            if len(names) > 1:
                problems.append(f"组 '{group}' 同时在 {' 和 '.join(f'i18n_{name}.py' for name in names)} 的 GROUPS "
                                f"里：只留一处")
        registered = {module.__name__ for module in i18n.CATALOG_MODULES}
        for name in sorted(set(f'i18n_{part}' for part in paths) - registered):
            problems.append(f'新的目录文件 src/{name}.py 没有登记：在 src/i18n.py 里 import 它并加进 CATALOG_MODULES')
        for name in sorted(registered - set(f'i18n_{part}' for part in paths)):
            problems.append(f'i18n.CATALOG_MODULES 里的 {name} 不是 src/i18n_<页>.py：从 CATALOG_MODULES 去掉')
        for language in i18n.LANGUAGES:
            if set(i18n.CATALOG[language]) != set(owners):
                problems.append(f'i18n.CATALOG[{language!r}] 不是各目录文件 TEXTS 的合并：看 src/i18n.py 的 _merge')
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c3_key_names(self):
        problems = []
        for key in sorted(i18n.CATALOG['zh']):
            if not KEY_SHAPE.match(key):
                problems.append(f"{where(key)} '{key}' 不是合法编号：全小写，用点分段，至少两段，例如 download.chip.done")
            elif key.split('.')[0] not in FIRST_PARTS:
                problems.append(f"{where(key)} '{key}' 的第一段 '{key.split('.')[0]}' 不在允许的 "
                                f"{len(FIRST_PARTS)} 个里：用页面名或 core / common（{' '.join(sorted(FIRST_PARTS))}）")
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c4_placeholders_match(self):
        """Every text parses; placeholders are plain names with no format spec or conversion; zh and en name the
        same placeholders."""
        problems = []
        for key in sorted(well_formed_keys()):
            names = {}
            for language in i18n.LANGUAGES:
                text = i18n.CATALOG[language][key]
                try:
                    parsed = list(FORMATTER.parse(text))
                except ValueError as error:
                    problems.append(f"{where(key)} '{key}' 的{'中文' if language == 'zh' else '英文'}解析不了（{error}）："
                                    f"文字里的花括号写成 {{{{ 和 }}}}")
                    continue
                names[language] = set()
                for _literal, field, spec, conversion in parsed:
                    if field is None:
                        continue
                    if not NAME_SHAPE.match(field):
                        problems.append(f"{where(key)} '{key}' 的 {{{field}}} 不是命名占位符：写成 {{count}} 这样的名字")
                    if spec or conversion:
                        problems.append(f"{where(key)} '{key}' 的 {{{field}{'!' + conversion if conversion else ''}"
                                        f"{':' + spec if spec else ''}}}：不写格式说明或转换，由调用方格式化后再传入")
                    names[language].add(field)
            if len(names) == 2 and names['zh'] != names['en']:
                show = {language: ' '.join('{' + name + '}' for name in sorted(found)) or '（没有）'
                        for language, found in names.items()}
                problems.append(f"{where(key)} '{key}'：中文有 {show['zh']}，英文有 {show['en']}——两边要用同一组名字")
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c5_no_chinese_in_english(self):
        problems = []
        good = well_formed_keys()
        for key, english in sorted(i18n.CATALOG['en'].items()):
            if key in CJK_ALLOWED_KEYS or key not in good:
                continue
            found = ''.join(char for char in english if CJK.match(char) and char not in CJK_ALLOWED_CHARS)
            if found:
                problems.append(f"{where(key)} '{key}' 的英文里有中文 {found!r}：翻译它。只有语言名这类必须保留中文的，"
                                f"才加进 verification/test_i18n_catalog.py 的 CJK_ALLOWED_KEYS 并写理由")
        self.assertEqual(problems, [], '\n'.join(problems))
        # the exceptions are what they are for: the language names a reader of the other language looks for
        self.assertEqual(i18n.CATALOG['en']['settings.language.zh'], '简体中文')
        self.assertEqual(i18n.CATALOG['en']['settings.language.title'], 'Language / 语言')
        self.assertEqual(i18n.CATALOG['zh']['settings.language.title'], '语言 / Language')

    def test_c6_nothing_left_untranslated(self):
        good = well_formed_keys()
        problems = [f"{where(key)} '{key}' 的英文是空的：写上英文"
                    for key, zh in sorted(i18n.CATALOG['zh'].items())
                    if key in good and zh.strip() and not i18n.CATALOG['en'][key].strip()]
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c7_english_wording(self):
        """No always-wrong phrase; a 'Label: n' part after ' · ' starts with a capital (I18N.md 8.3)."""
        problems = []
        lowercase = re.compile(r' · ([a-z][^·:{}]*): \{')
        good = well_formed_keys()
        for key, english in sorted(i18n.CATALOG['en'].items()):
            if key not in good:
                continue
            for phrase in ALWAYS_WRONG_EN:
                if phrase in english:
                    problems.append(f"{where(key)} '{key}' 的英文里有 {phrase!r}：按术语表改，见 "
                                    f"verification/test_i18n_catalog.py 里 ALWAYS_WRONG_EN 旁的注释")
            found = lowercase.search(english)
            if found:
                problems.append(f"{where(key)} '{key}' 的英文 ' · {found.group(1)}: ' 要大写开头："
                                f"' · {found.group(1)[:1].upper()}{found.group(1)[1:]}: '")
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c8_every_key_and_group_the_code_names_exists(self):
        """(a) the literal first argument of i18n.t / all_values / msg is a key (each branch of a conditional
        msg key), and of i18n.label / choices a group; (b) every other key-shaped str constant of src outside the
        catalogs is a key, and every value of i18n.GROUPS; (c) a t() or msg() call with a literal key and no **
        passes exactly the placeholders of its text; (d) (2.0.1) the key of a msg() call is a literal (or a
        conditional of literals)."""
        zh, problems, reported = i18n.CATALOG['zh'], [], set()
        for call in i18n_tool.api_calls():
            where_ = f'src/{call["file"]}:{call["line"]}'
            name = call['name']
            if call['kind'] == 'group':
                if name not in i18n.GROUPS:
                    problems.append(f"{where_} i18n.{call['function']}('{name}', …)：没有这个组，现有的组是 "
                                    f"{', '.join(sorted(i18n.GROUPS))}")
                continue
            if name not in zh or name not in i18n.CATALOG['en']:
                reported.add((call['file'], call['line'], name))
                problems.append(f"{where_} {call['function']}('{name}')：目录里没有这个编号。{suggestion(name)}"
                                f"改成已有编号，或在 {catalog_file(name)} 的 TEXTS 里加 '{name}': ('中文', 'English')")
                continue
            if call['function'] in ('t', 'msg') and call['keywords'] is not None:
                given, wanted = set(call['keywords']), fields(zh[name])
                if given != wanted:
                    holders = ' '.join('{' + field + '}' for field in sorted(wanted)) or '（没有）'
                    passed = ', '.join(f'{field}=…' for field in sorted(given)) or '没有参数'
                    function = 'messages.msg' if call['function'] == 'msg' else 'i18n.t'
                    problems.append(f"{where_} {function}('{name}', {passed})：这条文字的占位符是 {holders}——"
                                    f"少传的会原样显示在界面上，多传的没有用：让参数名与占位符一致")
        for file, line, expression in computed_msg_keys():
            problems.append(f"src/{file}:{line} messages.msg 的编号要写成字面量（'core.…'），C8 才能核对它在目录里、"
                            f"参数对得上；要选编号就写 a if 条件 else b。（现在是 {expression}）")
        for file, line, text in key_shaped_constants():
            if text not in zh and (file, line, text) not in reported:
                problems.append(f"src/{file}:{line} '{text}' 形状像编号，但目录里没有。{suggestion(text)}改成已有编号，或在 "
                                f"{catalog_file(text)} 里加上它（它若不是编号，换一种写法）")
        for group, mapping in sorted(i18n.GROUPS.items()):
            for value, key in mapping.items():
                if key is not None and key not in zh:
                    problems.append(f"组 '{group}' 的值 {value!r} 指向不存在的编号 '{key}'：改成已有编号，或在 "
                                    f"{catalog_file(key)} 里加上它")
        self.assertEqual(problems, [], '\n'.join(problems))
        names = {(call['file'], call['function'], call['name']) for call in i18n_tool.api_calls()}
        self.assertIn(('app.py', 'all_values', 'app.title'), names, 'the scan sees the calls')

    def test_c9_every_key_is_used(self):
        """Every catalog key is used: a str constant of src outside the catalogs, a value of i18n.GROUPS, the key
        an i18n.t / msg call names, a stored key (golden/stored-keys.json), 'format.' + a preset id (S3 W3),
        or a key in the RETIRED of any catalog file that nothing uses any more (S3 review fix: the union, so a page
        retires a shared key in its own file)."""
        used = {text for _file, _line, text in src_constants()}
        used |= {key for mapping in i18n.GROUPS.values() for key in mapping.values() if key}
        used |= {call['name'] for call in i18n_tool.api_calls() if call['kind'] == 'key'}
        used |= stored_keys()
        import formats
        # (S3 W3) the format menu and the log show i18n.t('format.' + preset id)
        used |= {'format.' + preset.id for preset in formats.PRESETS}
        problems = unused_problems(modules(), used)
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c9_retired_is_read_across_the_files(self):
        """(S3 review fix) A page retires a shared key in its own file; a key still used, or not in any catalog, may
        not be retired; a shared key nobody uses tells how to retire it."""
        import types
        common = types.SimpleNamespace(TEXTS={'common.a': ('甲', 'A'), 'common.b': ('乙', 'B')})
        page = types.SimpleNamespace(TEXTS={'download.x': ('丙', 'C')}, RETIRED=('common.a',))
        place = lambda key: 'src/i18n_x.py:1'
        self.assertEqual(unused_problems({'common': common, 'download': page}, {'download.x', 'common.b'}, place), [])
        found = unused_problems({'common': common, 'download': page}, {'download.x', 'common.a', 'common.b'}, place)
        self.assertEqual(found, ["src/i18n_download.py 的 RETIRED 里有 'common.a'，但代码还在用它：从 RETIRED 去掉"])
        page.RETIRED = ('common.a', 'common.gone')
        found = unused_problems({'common': common, 'download': page}, {'download.x', 'common.b'}, place)
        self.assertEqual(found, ["src/i18n_download.py 的 RETIRED 里有 'common.gone'，但目录里没有这个编号：从 RETIRED 去掉"])
        page.RETIRED = ()
        found = unused_problems({'common': common, 'download': page}, {'download.x', 'common.b'}, place)
        self.assertEqual(len(found), 1)
        self.assertIn("RETIRED = ('common.a',)", found[0])
        found = unused_problems({'common': common, 'download': page}, {'common.a', 'common.b'}, place)
        self.assertEqual(len(found), 1)
        self.assertNotIn('RETIRED', found[0], 'a page key is simply deleted')

    def test_stored_keys_are_a_contract(self):
        """C10 (2.0.0 S2-AMEND 3.4 Q3-7) golden/stored-keys.json, the keys a format-2 store may hold: every key is in
        the Chinese and the English catalog with exactly the placeholders the file records (common.clauses,
        the structural key, has 'parts' and no catalog text).  The census of test_messages_store checks that
        every key the writers store is one of them."""
        stored = json.loads((_sandbox.GOLDEN / 'stored-keys.json').read_text(encoding='utf-8'))['keys']
        # 145: S2 review fix removed the two keys nothing writes any more (core.unfinished.problem.bad_entries,
        # value.job_title.placeholder) before 2.0.0, the first release with this contract, shipped.
        # 137: (S4 A1, S4-DESIGN 2.1 / download-input 7.2) the format names value.mode.* (7) and core.formats.bad_label
        # went with the second name of a format, also before 2.0.0 shipped; no writer stores them (test_messages_store).
        missing = sorted(STORED_2_0_0 - set(stored))
        self.assertEqual(missing, [], '用户的资料库和未完成清单里存着这些编号：把它们加回 golden/stored-keys.json'
                                      '（这个文件只能加编号，不能删除或改名）')
        contract = ("：用户的资料库和未完成清单里存着这个编号，不能改名、删除或改占位符，只能改文字。"
                    "确实要换一种说法，就加新编号，旧编号留着")
        problems = []
        for key, placeholders in sorted(stored.items()):
            if placeholders != sorted(set(placeholders)):
                problems.append(f"golden/stored-keys.json 里 '{key}' 的占位符 {placeholders} 要排序、不重复")
            if key == 'common.clauses':
                if placeholders != ['parts'] or key in i18n.CATALOG['zh']:
                    problems.append("'common.clauses' 是结构编号：stored-keys.json 里记 ['parts']，目录里没有它")
                continue
            if key not in i18n.CATALOG['zh'] or key not in i18n.CATALOG['en']:
                problems.append(f"'{key}' 在 golden/stored-keys.json 里，但目录里没有{contract}：把它加回 "
                                f"{catalog_file(key)}")
                continue
            for language in i18n.LANGUAGES:
                found = fields(i18n.CATALOG[language][key])
                if found != set(placeholders):
                    problems.append(f"{where(key)} '{key}' 的{'中文' if language == 'zh' else '英文'}占位符是 "
                                    f"{sorted(found)}，golden/stored-keys.json 记的是 {placeholders}{contract}")
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_c10b_safety_texts_keep_their_facts(self):
        """(PLAN 4.7) The texts that tell a file was kept aside, or could not be, keep their key and the
        placeholders that say where; the wording is free (no text is pinned)."""
        problems = []
        for key, facts in sorted(MUST_SAY.items()):
            if key not in i18n.CATALOG['zh']:
                problems.append(f"'{key}' 不在目录里：这句话告诉用户文件被改名保留了（PLAN 4.7），把它加回 src/i18n_core.py")
                continue
            for language in i18n.LANGUAGES:
                missing = facts - fields(i18n.CATALOG[language][key])
                for name in sorted(missing):
                    problems.append(f"{where(key)} '{key}' 的{'中文' if language == 'zh' else '英文'}不能去掉 {{{name}}}："
                                    f"这句话要告诉用户文件被保留在哪里（PLAN 4.7）。可以缩短，但要保留这个位置")
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_i18n_imports_no_app_module(self):
        """C12: i18n imports the eight catalogs and no other module of the app; a catalog imports nothing."""
        tree = ast.parse((i18n_tool.SRC / 'i18n.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        local = {name for name in imported if (i18n_tool.SRC / f'{name}.py').is_file()}
        self.assertEqual(local, {f'i18n_{name}' for name in catalog_paths()})
        for name in local:
            module_tree = ast.parse((i18n_tool.SRC / f'{name}.py').read_text(encoding='utf-8'))
            self.assertFalse([node for node in ast.walk(module_tree) if isinstance(node, (ast.Import, ast.ImportFrom))],
                             f'{name} imports nothing')
        self.assertFalse(hasattr(i18n, 'localize'), 'i18n.localize is gone (1.8.0, MESSAGES.md 6.4)')

    def test_the_log_tags_name_existing_keys(self):
        """C13: i18n.TAGS names keys with 'error' / 'ok'.  (2.0.0 S4 A1, replacing the Chinese starts of
        ui_log.classify) A tagged key's lines get its colour in Chinese and in English (i18n.log_tag matches the
        key's own text); a key that is not tagged but whose Chinese line some tagged text matches would be
        coloured in Chinese only: it is tagged too, or its text changes."""
        problems = []
        for key, tag in sorted(i18n.TAGS.items()):
            if key not in i18n.CATALOG['zh']:
                problems.append(f"TAGS 里的 '{key}' 不在目录里：删掉这条标记")
            if tag not in LOG_TAGS:
                problems.append(f"TAGS['{key}'] 是 {tag!r}：只能是 'error' 或 'ok'")
            for language in i18n.LANGUAGES:
                found = i18n.log_tag(filled(i18n.CATALOG[language].get(key, '')))
                if key in i18n.CATALOG['zh'] and tag in LOG_TAGS and found != tag:
                    problems.append(f"{where(key)} '{key}' 标的是 {tag!r}，但它的{'中文' if language == 'zh' else '英文'}"
                                    f"一行被认成 {found!r}：别的带标记的文字盖住了它，改其中一句的开头")
        for key, zh in sorted(i18n.CATALOG['zh'].items()):
            found = i18n.log_tag(filled(zh))
            if key not in i18n.TAGS and found is not None:
                problems.append(f"{where(key)} '{key}' 没有标记，但中文这一行会被认成 {found!r}（像某个带标记的文字），英文"
                                f"却不一定：在同一文件的 TAGS 里写 '{key}': '{found}'，或者改这句的开头")
        self.assertEqual(problems, [], '\n'.join(problems))


class FakeKeys:
    """(S3 review fix) The lookup machinery is tested on keys, groups and tags the test puts into i18n for its own run:
    the wording of the app's texts is the catalog's and is pinned nowhere here.  What is a rule and not wording
    (the stored clause joiner, the language names) is read from the real catalog and said so."""

    TEXTS = {
        'test.count': ('{count} 条', 'Count: {count}'),
        'test.plain': ('只有中文', 'Plain'),
        'test.same': ('Same', 'Same'),
        'test.broken': ('坏的 {count', 'broken {count'),
        'test.failed': ('无法处理（{code}），继续。', 'Could not handle ({code}); going on.'),
        'test.saved': ('已保存：{path}', 'Saved: {path}'),
    }
    GROUPS = {'test_group': {'a': 'test.plain', 'b': 'test.same', 'none': None}}
    TAGS = {'test.failed': 'error', 'test.saved': 'ok'}

    def setUp(self):
        super().setUp()
        for key, pair in self.TEXTS.items():
            for language, text in zip(i18n.LANGUAGES, pair):
                i18n.CATALOG[language][key] = text
        i18n.GROUPS.update(self.GROUPS)
        i18n.TAGS.update(self.TAGS)
        self.addCleanup(self._remove)

    def _remove(self):
        for key in self.TEXTS:
            for language in i18n.LANGUAGES:
                i18n.CATALOG[language].pop(key, None)
        for name in self.GROUPS:
            i18n.GROUPS.pop(name, None)
        for key in self.TAGS:
            i18n.TAGS.pop(key, None)


class LookupTests(FakeKeys, unittest.TestCase):
    """Item 8: t() fallbacks (the localize tests of items 7 and 9 are in test_messages.py since 1.8.0)."""

    def test_8_missing_key_and_missing_placeholder(self):
        logger = logging.getLogger('YtDlpDesktop.i18n')
        with self.assertLogs(logger, 'WARNING') as logs:
            self.assertEqual(i18n.t('no.such.key.for.test'), 'no.such.key.for.test')
            self.assertEqual(i18n.t('no.such.key.for.test'), 'no.such.key.for.test')
            logger.warning('end marker')
        self.assertEqual(len([line for line in logs.output if 'no.such.key.for.test' in line]), 1)
        del i18n.CATALOG['en']['test.plain']
        with _sandbox.language('en'), self.assertLogs(logger, 'WARNING') as logs:
            self.assertEqual(i18n.t('test.plain'), '只有中文', 'a key missing in en shows its zh text')
            self.assertEqual(i18n.t('test.plain'), '只有中文')
            logger.warning('end marker')
        self.assertEqual(len([line for line in logs.output if 'test.plain' in line]), 1)
        with _sandbox.language('en'):
            self.assertEqual(i18n.t('test.count', count=3), 'Count: 3')
            self.assertEqual(i18n.t('test.count'), 'Count: {count}')
            self.assertEqual(i18n.t('test.count', count=3, extra='ignored'), 'Count: 3')
            self.assertEqual(i18n.t('test.broken', count=1), 'broken {count')
        self.assertEqual(i18n.t('test.count', count=3), '3 条')
        self.assertEqual(i18n.t(['not', 'a', 'key']), "['not', 'a', 'key']", 't never raises')


class HelperTests(FakeKeys, unittest.TestCase):
    def test_label_choices_all_values_log_tag_and_joiners(self):
        self.assertEqual(i18n.label('test_group', 'a'), '只有中文')
        self.assertEqual(i18n.label('test_group', '只有中文'), '只有中文', '(2.0.0) a label is not a value')
        self.assertEqual(i18n.label('test_group', 'unknown'), 'unknown')
        self.assertEqual(i18n.label('test_group', 'none'), 'none', 'a value mapped to None shows itself')
        self.assertEqual(i18n.label('test_group', None), '')
        self.assertEqual(i18n.label('nothing', 3), '3')
        self.assertEqual(i18n.label('test_group', [1]), '[1]', 'an unhashable value never raises')
        with _sandbox.language('en'):
            self.assertEqual(i18n.label('test_group', 'a'), 'Plain')
            self.assertEqual(i18n.choices('test_group'), [('a', 'Plain'), ('b', 'Same'), ('none', 'none')])
            self.assertEqual(i18n._join_sentences('en', ['The page is gone', 'Retry later.', 'Why?', 'Kept (3)']),
                             'The page is gone. Retry later. Why? Kept (3)')
        self.assertEqual(i18n.all_values('test.plain'), ('只有中文', 'Plain'))
        self.assertEqual(i18n.all_values('test.same'), ('Same',), 'no repeats')
        # rules, not wording: the zh clause joiner is the stored one (i18n.JOIN, messages.JOIN); en joins sentences
        self.assertEqual(i18n._join_sentences('zh', ['a', 'b']), 'a' + i18n.JOIN + 'b')
        self.assertEqual(i18n._join_sentences('en', ['a', 'b']), 'a.' + _sandbox.en_t('common.sentence_join') + 'b.')
        # the language names stay readable to a reader of the other language (C5)
        self.assertEqual(i18n.all_values('settings.language.zh'), ('简体中文',))
        # (2.0.0 S4 A1) log_tag: the tag of a line by the tagged text it is, in any language
        for line in ('无法处理（1），继续。', 'Could not handle (exit 1); going on.', '无法处理（x），继续。后面'):
            self.assertEqual(i18n.log_tag(line), 'error', line)
        for line in ('Saved: D:\\a.m4a', '已保存：a'):
            self.assertEqual(i18n.log_tag(line), 'ok', line)
        for line in ('无法处理', '无法处理（1）', 'x 无法处理（1），继续。', 'Saved', ''):
            self.assertIsNone(i18n.log_tag(line), line)


if __name__ == '__main__':
    unittest.main()
