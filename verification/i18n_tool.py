"""Language tooling of 1.7.0 (WP-L0, I18N.md sections 4, 8.1, 9.3 and 11.1).

    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_tool.py scan [--src DIR] [--out JSON]
    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_tool.py catalog [--inventory JSON]
    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_tool.py parity
    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_tool.py suspects

scan(src)            every str and f-string literal of src/*.py (ast; adjacent literals are one node;
                     docstrings marked), the logic of the design's find_literals.py; CJK literals get
                     the inventory id '<stem>:<line>' or '<stem>:<line>#<n>'.
catalog(inventory)   writes src/i18n_maps.py and the eight src/i18n_<part>.py catalogs from
                     docs/1.7.0/spec/strings-inventory.json, and the inventory copy
                     verification/i18n_inventory.json the catalog test reads (docs/ is not part of
                     the work copies).
parity_table()       the constants and Tk variables that tests read with their Chinese values
                     (I18N.md 8.1), each paired with its inventory key.  Imports src modules: run it
                     under _sandbox (the CLI does).
suspects(src)        UI sites that show a stored or core value without localize/label/t (9.3).

No src module is imported at module level; only the standard library.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import json
import re
import string
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
VERIFICATION = ROOT / 'verification'
SPEC_INVENTORY = ROOT / 'docs' / '1.7.0' / 'spec' / 'strings-inventory.json'
INVENTORY = VERIFICATION / 'i18n_inventory.json'

UI_FILES = ('studio.py', 'ui_download.py', 'ui_log.py', 'ui_settings.py', 'collection_ui.py', 'library_ui.py')
CORE_FILES = ('engine.py', 'collector.py', 'updater.py', 'naming.py', 'unfinished.py', 'urls.py', 'formats.py',
              'paths.py', 'library.py', 'settings.py', 'procutil.py', 'app.py', 'applog.py', 'progress.py',
              'ui_theme.py')
# The catalog module of each UI file (I18N.md 4).
FILE_MODULE = {'studio.py': 'shell', 'ui_download.py': 'download', 'ui_log.py': 'log', 'ui_settings.py': 'settings',
               'collection_ui.py': 'collect', 'library_ui.py': 'library'}
MODULES = ('core', 'common', 'shell', 'download', 'log', 'settings', 'collect', 'library')
OWNERS = {'core': 'L1-C', 'common': 'L0 (frozen in L1; changes go through the L2 merge)', 'shell': 'L1-A',
          'download': 'L1-A', 'log': 'L1-A', 'settings': 'L1-B', 'collect': 'L1-B', 'library': 'L1-B'}
# The src files that must use i18n before the "every key is used" check of a catalog applies.
# (L2) the real users of each catalog: shell keys are also shown by ui_settings.py (the app badge),
# download keys by library_ui.py (the hints it puts on the download page).
MODULE_USERS = {'core': ('library.py', 'app.py'), 'common': UI_FILES, 'shell': ('studio.py', 'ui_settings.py'),
                'download': ('ui_download.py', 'library_ui.py'), 'log': ('ui_log.py',), 'settings': ('ui_settings.py',),
                'collect': ('collection_ui.py',), 'library': ('library_ui.py',)}
# The modules whose test-visible constants keep their Chinese values (I18N.md 8.1).
PARITY_MODULES = ('studio', 'ui_download', 'ui_log', 'ui_settings', 'collection_ui', 'library_ui', 'naming', 'formats')
# The constants I18N.md 8.1 names (read by tests or by other modules): part of the parity table
# whether or not a test of this state reads them.
H4_CONSTANTS = {
    'ui_download': ('TITLE', 'STATE_LOOK', 'CLEARED_CHIP', 'CLEARED_DETAIL', 'FINISHED_UNFINISHED', 'CLEARED_METRICS',
                    'FINISHED_PREFIX', 'CLEARED_TITLE_SUFFIX', 'REASON_WORDS', 'HINT_DEFAULT', 'PLACEHOLDER',
                    'METRICS_IDLE', 'BOT_TEXT', 'BLOCKED_DETAIL', 'FOREIGN_TEXT', 'BOT_TEXT_COOKIES', 'PROXY_REASON',
                    'SKIPPED_TEXT', 'FORMATS'),
    'studio': ('TITLE',),
    'ui_settings': ('UPDATE_IDLE', 'APPEARANCE_LABELS'),
    'library_ui': ('INTERVALS', 'STATES', 'NOTHING_DONE', 'RESUME_LATER_LOG', 'RESUME_DROPPED_LOG', 'HISTORY_NOTE',
                   'SUBSCRIPTION_NOTE', 'HISTORY_UNREADABLE', 'LIBRARY_UNREADABLE', 'PICK_SUBSCRIPTION',
                   'NO_SUBSCRIPTION', 'PICK_PENDING', 'NO_PENDING', 'PICK_ARCHIVE'),
    'collection_ui': ('MODE_STATIC', 'MODE_DYNAMIC', 'FILTERS', 'STATUS_IDLE', 'NOTE_PAGES', 'NOTE_DYNAMIC', 'ADD_TEXT',
                      'BACKGROUND_NOTE', 'SUBTITLE'),
    'naming': ('LABELS',),
    'formats': ('PRESETS',),
}
TRANSLATED_FLAGS = ('ui', 'core', 'persisted', 'compared', 'filetext')

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
HAN = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
HOLDER = re.compile(r'\{([A-Za-z_][A-Za-z_0-9]*)\}')
UPPER = re.compile(r'^[A-Z][A-Z0-9_]*$')

# Placeholders that hold numbers (I18N.md 9.2) and the regex of each.
NUMERIC = ('count', 'code', 'seconds', 'line', 'page', 'pages', 'links', 'dropped', 'attempt', 'limit', 'status',
           'minutes', 'length', 'size')
# Captured values that are core texts themselves: localized again, each with its own key filter
# (None = every LOCALIZE key).
RECURSE = OrderedDict([('reason', None), ('error', None), ('problem', ('core.unfinished.problem.',)),
                       ('mode', ('value.mode.',)), ('name', ('value.subscription.untitled',))])
# Keys a UI module shows that the core (or stored data) produces besides core.* and value.*.
LOCALIZE_EXTRA = ('download.reason.proxy', 'common.unfinished')
# Keys in the catalogs that need no t() call: language-neutral texts of the inventory.
NEUTRAL_FLAGS = ('neutral',)

# Group maps (I18N.md 3.7 and 7): group -> [(stored value, key or None)] in menu order.  A str key
# whose zh text is the stored value is written as ('=', key).  None: shown as stored (language-neutral).
GROUP_SPEC = OrderedDict([
    ('mode', [('=', 'value.mode.audio_best'), ('=', 'value.mode.audio_wav'), ('=', 'value.mode.audio_flac'),
              ('=', 'value.mode.audio_mp3'), ('=', 'value.mode.video_mkv'), ('=', 'value.mode.audio_m4a'),
              ('=', 'value.mode.video_mp4')]),                       # engine.MODES order
    ('format', [('=', 'format.audio_best'), ('=', 'format.audio_m4a'), ('=', 'format.audio_mp3'),
                ('=', 'format.audio_flac'), ('=', 'format.audio_wav'), ('=', 'format.video_mp4'),
                ('=', 'format.video_mkv')]),                         # formats.MENU_ORDER, keyed by the zh Preset.label
    ('resolution', [('=', 'value.resolution.best'), ('2160p / 4K', None), ('1440p', None), ('1080p', None),
                    ('720p', None), ('480p', None)]),                # engine.RESOLUTIONS
    ('naming', [('title', 'naming.title'), ('clean', 'naming.clean'), ('title_id', 'naming.title_id')]),
    ('appearance', [('light', 'settings.appearance.light'), ('dark', 'settings.appearance.dark'),
                    ('system', 'settings.appearance.system')]),
    ('language', [('system', 'settings.language.system'), ('zh', 'settings.language.zh'),
                  ('en', 'settings.language.en')]),
    ('interval', [(0, 'library.interval.manual'), (900, 'library.interval.15min'), (3600, 'library.interval.hourly'),
                  (86400, 'library.interval.daily')]),
    ('job_status', [('running', 'library.state.running'), ('completed', 'library.state.completed'),
                    ('partial', 'library.state.partial'), ('interrupted', 'library.state.interrupted')]),
    ('job_kind', [('download', 'library.kind.download'), ('collect', 'library.kind.collect')]),
    ('link_kind', [('=', 'value.link_kind.media'), ('=', 'value.link_kind.video_page'), ('=', 'value.link_kind.web'),
                   ('=', 'value.link_kind.direct'), ('=', 'value.link_kind.dynamic_request')]),
    ('collect_mode', [('=', 'collect.mode.static'), ('=', 'collect.mode.dynamic')]),
    ('collect_filter', [('=', 'collect.filter.media'), ('=', 'collect.filter.all')]),
    ('library_tab', [('=', 'library.tab.history'), ('=', 'library.tab.subscriptions'), ('=', 'library.tab.archive')]),
    ('row_state', [('waiting', 'download.chip.waiting'), ('active', 'download.chip.active'),
                   ('done', 'download.chip.done'), ('failed', 'download.chip.failed'),
                   ('stopped', 'download.chip.stopped'), ('unstarted', 'download.chip.unstarted'),
                   ('blocked', 'download.chip.blocked'), ('skipped', 'download.chip.skipped'),
                   ('unfinished', 'common.unfinished')]),             # ui_download.STATE_LOOK
])

# Starts of log lines that ui_log.classify tags (1.6.0 _ERROR_STARTS and the ok starts): the keys
# whose zh text starts so are tagged in i18n_log.TAGS, as are the keys the inventory notes as
# "error tag" / "ok tag".
ERROR_STARTS = ('此链接未完成', '此链接无法开始', '更新失败', '网页采集未完成', '订阅检查失败', '界面错误', '设置未保存',
                '错误：', '无法启动下载引擎')
OK_STARTS = ('文件已保存', '下载完成')
# classify's special 1.6.0 rule: a line starting 任务结束 that says 未完成 (I18N.md 8.6).
TAG_EXTRA = {'download.title.finished_unfinished': 'error'}

# Sites suspects() finds that show user data (titles, links, paths, names), not a stored core
# text: (file, expression) -> reason.  Lines are not part of the entry (they move).
SUSPECT_ALLOW = {
    ('library_ui.py', "data['last_new']"): 'a number',
    ('library_ui.py', "row['pending']"): 'a number',
    ('ui_download.py', 'options.mode'): 'the job title stored before the first title arrives keeps the canonical '
                                        'mode string (value.job_title.placeholder, H2); history_title localizes it',
    ('ui_download.py', "value['url']"): 'a link (user data)',
    ('ui_download.py', "value['files']"): 'a number',
    ('ui_download.py', "value['failed_path']"): 'a path (user data)',
    ('ui_settings.py', "value.get('latest')"): 'a yt-dlp version number',
}

_FORMATTER = string.Formatter()


# --------------------------------------------------------------------------------- scanning

def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = func.value
        prefix = ''
        if isinstance(base, ast.Name):
            prefix = base.id + '.'
        elif isinstance(base, ast.Attribute):
            prefix = '….' + base.attr + '.'
        return prefix + func.attr
    return ast.unparse(func)[:40]


def _fstring_text(node):
    parts, holders = [], []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value).replace('{', '{{').replace('}', '}}'))
        elif isinstance(value, ast.FormattedValue):
            expr = ast.unparse(value.value)
            spec = ''
            if value.format_spec is not None:
                spec = ':' + ''.join(str(v.value) if isinstance(v, ast.Constant) else '{' + ast.unparse(v.value) + '}'
                                     for v in value.format_spec.values)
            conv = '' if value.conversion == -1 else '!' + chr(value.conversion)
            parts.append('{' + expr + conv + spec + '}')
            holders.append(expr)
    return ''.join(parts), holders


class _Finder(ast.NodeVisitor):
    """find_literals.py of the design, unchanged in what it reports."""

    def __init__(self, file):
        self.file = file
        self.stack = []
        self.out = []
        self.skip = set()
        self.docstrings = set()
        self.concat = set()

    def _scope(self, node):
        body = getattr(node, 'body', None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            self.docstrings.add(id(body[0].value))

    def visit_Module(self, node):
        self._scope(node)
        self.generic_visit(node)

    def _named(self, node):
        self._scope(node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _named

    def visit_Call(self, node):
        name = _call_name(node)
        for arg in node.args:
            self._mark(arg, name, None)
        for keyword in node.keywords:
            self._mark(keyword.value, name, keyword.arg)
        self.generic_visit(node)

    @staticmethod
    def _mark(value, name, kwarg):
        for sub in ast.walk(value):
            if isinstance(sub, (ast.Constant, ast.JoinedStr)) and not hasattr(sub, '_call'):
                sub._call = name
                sub._kwarg = kwarg

    def visit_BinOp(self, node):
        if isinstance(node.op, (ast.Add, ast.Mod)):
            for sub in (node.left, node.right):
                if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                    self.concat.add(id(sub))
        self.generic_visit(node)

    def visit_JoinedStr(self, node):
        text, holders = _fstring_text(node)
        self._add(node, 'fstr', text, holders)
        for value in node.values:
            if isinstance(value, ast.Constant):
                self.skip.add(id(value))
            elif isinstance(value, ast.FormattedValue):
                self.visit(value.value)
                if value.format_spec is not None:
                    for item in value.format_spec.values:
                        self.skip.add(id(item))

    def visit_Constant(self, node):
        if id(node) in self.skip or not isinstance(node.value, str):
            return
        holders = re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', node.value)
        holders += re.findall(r'%[sd]|%\([a-z_]+\)[sd]', node.value)
        self._add(node, 'str', node.value, holders)

    def _add(self, node, kind, text, holders):
        self.out.append({
            'file': self.file, 'line': node.lineno, 'col': node.col_offset, 'end_line': node.end_lineno,
            'kind': kind, 'text': text, 'placeholders': holders,
            'func': '.'.join(self.stack), 'call': getattr(node, '_call', None),
            'kwarg': getattr(node, '_kwarg', None), 'cjk': bool(HAN.search(text)),
            'docstring': id(node) in self.docstrings, 'in_concat': id(node) in self.concat,
        })


def scan_file(path) -> list:
    path = Path(path)
    finder = _Finder(path.name)
    finder.visit(ast.parse(path.read_text(encoding='utf-8'), filename=str(path)))
    records = sorted(finder.out, key=lambda r: (r['line'], r['col']))
    per_line = defaultdict(list)
    for record in records:
        if record['cjk'] and not record['docstring']:
            per_line[record['line']].append(record)
    stem = path.name[:-3]
    for line, group in per_line.items():
        for number, record in enumerate(group, 1):
            record['id'] = f'{stem}:{line}' if len(group) == 1 else f'{stem}:{line}#{number}'
    return records


def scan(src=SRC) -> list:
    """Every str / f-string literal of src/*.py (records as find_literals.py writes them)."""
    records = []
    for path in sorted(Path(src).glob('*.py')):
        records.extend(scan_file(path))
    records.sort(key=lambda r: (r['file'], r['line'], r['col']))
    return records


def cjk_literals(src=SRC, files=None) -> list:
    """The CJK literals (docstrings excluded) of src, optionally of some files only."""
    return [r for r in scan(src) if r['cjk'] and not r['docstring'] and (files is None or r['file'] in files)]


def shape(text, kind='template') -> str:
    """A text with every placeholder replaced by \\0 and doubled braces undone.

    kind 'template' / 'fstr': '{{' and '}}' are braces, '{...}' is a placeholder (also with a
    format spec); kind 'str': a plain literal, only '{name}' is a placeholder (str.format templates).
    """
    if kind == 'str':
        return HOLDER.sub('\0', text)
    return re.sub(r'\{\{|\}\}|\{[^{}]*\}', lambda m: {'{{': '{', '}}': '}'}.get(m.group(0), '\0'), text)


def record_shape(record) -> str:
    return shape(record['text'], 'fstr' if record['kind'] == 'fstr' else 'str')


def imports_i18n(path) -> bool:
    """Whether a src file imports i18n (anywhere in it)."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == 'i18n' for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == 'i18n':
            return True
    return False


# --------------------------------------------------------------------------------- inventory

def load_inventory(path=None) -> dict:
    if path is None:
        path = INVENTORY if INVENTORY.is_file() else SPEC_INVENTORY
    return json.loads(Path(path).read_text(encoding='utf-8'))


def zh_template(entry) -> str:
    """The catalog text of an inventory entry: its zh template, else its literal text (braces escaped)."""
    if entry.get('zh_template'):
        return entry['zh_template']
    return entry['text'].replace('{', '{{').replace('}', '}}') if not HOLDER.search(entry['text']) \
        else entry['text']


def keyed_texts(inventory) -> OrderedDict:
    """key -> {'zh', 'en', 'files', 'flag', 'source'} for every key of the inventory (literals, the
    language-neutral extras and the 1.7.0 strings)."""
    keys = OrderedDict()
    for entry in inventory['entries']:
        key = entry.get('key')
        if not key or not entry.get('translate'):
            continue
        item = keys.get(key)
        if item is None:
            keys[key] = item = {'zh': zh_template(entry), 'en': entry['en'], 'files': [], 'flag': entry['flag'],
                                'source': 'literal', 'context': entry.get('context', '')}
        file = entry['file'].split('/')[-1]
        if file not in item['files']:
            item['files'].append(file)
    for extra in inventory.get('extra_display_strings_without_cjk', []):
        key = extra['key']
        if key not in keys:
            text = extra['text']
            keys[key] = {'zh': text, 'en': extra['en'], 'files': [extra['file'].split('/')[-1]],
                         'flag': extra['flag'], 'source': 'extra', 'context': extra.get('context', '')}
    for new in inventory.get('new_strings_1_7_0', []):
        keys[new['key']] = {'zh': new['zh'], 'en': new['en'], 'files': [], 'flag': 'ui', 'source': 'new',
                            'context': new.get('context', '')}
    return keys


def module_of(key, files) -> str:
    """The catalog module of a key (I18N.md 4): by its prefix where the prefix names a page or the
    core, else by the UI file of its first use (two or more UI files: common)."""
    head = key.split('.')[0]
    ui = [file for file in files if file in FILE_MODULE]
    if key.startswith('value.link_kind.'):
        return 'collect'            # shown through the link_kind group in the collect table (I18N.md 4)
    if head in ('core', 'value', 'format', 'naming', 'app', 'unit'):
        return 'core'
    if head == 'filetext':
        return 'core' if files and files[0] in CORE_FILES else FILE_MODULE.get(ui[0], 'core') if ui else 'core'
    if head in ('common', 'table', 'error') or key.startswith('dialog.filetype.'):
        return 'common'
    if head in ('nav', 'shell', 'window'):
        return 'shell'
    if head in ('download', 'settings', 'collect', 'library'):
        return head
    if key.startswith(('dialog.restart_', 'log.restart_')):
        return 'settings'           # the restart flow of the language row (I18N.md 6.3)
    if len(set(ui)) >= 2:
        return 'common'
    if ui:
        return FILE_MODULE[ui[0]]
    return 'core'


def group_maps(keys) -> OrderedDict:
    groups = OrderedDict()
    for group, items in GROUP_SPEC.items():
        mapping = OrderedDict()
        for value, key in items:
            if value == '=':
                value = keys[key]['zh']
            mapping[value] = key
        groups[group] = mapping
    return groups


def localize_keys(keys) -> list:
    return sorted(key for key, item in keys.items()
                  if (key.startswith(('core.', 'value.')) and item['flag'] not in NEUTRAL_FLAGS
                      and item['source'] != 'extra') or key in LOCALIZE_EXTRA)


def log_tags(keys, inventory) -> OrderedDict:
    tags = {}
    for entry in inventory['entries']:
        note = entry.get('note') or ''
        key = entry.get('key')
        if key and 'error tag' in note:
            tags[key] = 'error'
        elif key and 'ok tag' in note:
            tags.setdefault(key, 'ok')
    for key, item in keys.items():
        if item['zh'].startswith(ERROR_STARTS):
            tags.setdefault(key, 'error')
        elif item['zh'].startswith(OK_STARTS):
            tags.setdefault(key, 'ok')
    tags.update(TAG_EXTRA)
    return OrderedDict(sorted(tags.items()))


def _py(value) -> str:
    """A Python literal of a str / int / tuple / None, one line."""
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, tuple):
        return '(' + ', '.join(_py(item) for item in value) + (',)' if len(value) == 1 else ')')
    return repr(value)


def _catalog_module(name, entries, keys, tags=None) -> str:
    owner = OWNERS[name]
    lines = [
        f'"""Catalog {name} of the 1.7.0 language adaptation (I18N.md 4). Owner in L1: {owner}.',
        '',
        'Generated by verification/i18n_tool.py catalog from docs/1.7.0/spec/strings-inventory.json (WP-L0).',
        'ZH holds the exact Chinese text the code showed in 1.6.0 / the 1.7.0 fix phase (the catalog test',
        'compares it with the source); EN the English text (GLOSSARY.md).  Same keys in both, sorted, one',
        'entry per line; placeholders are named ({count}); a literal brace is doubled.',
        '"""',
        '',
        'ZH = {',
    ]
    lines += [f'    {_py(key)}: {_py(keys[key]["zh"])},' for key in entries]
    lines.append('}')
    lines.append('')
    lines.append('EN = {')
    lines += [f'    {_py(key)}: {_py(keys[key]["en"])},' for key in entries]
    lines.append('}')
    if tags is not None:
        lines += ['', '# Log-page tag of a key\'s line (ui_log.classify): "error" or "ok"; i18n.prefixes(tag) reads it.',
                  'TAGS = {']
        lines += [f'    {_py(key)}: {_py(tag)},' for key, tag in tags.items()]
        lines.append('}')
    return '\n'.join(lines) + '\n'


def _maps_module(groups, localize, variants, reserved, new_keys) -> str:
    lines = [
        '"""Maps of the 1.7.0 language adaptation (I18N.md 3.7 and 9.2): key names only, no texts.',
        '',
        'Generated by verification/i18n_tool.py catalog (WP-L0); frozen in L1 (a team that needs a change',
        'records it in its handoff and L2 applies it).',
        '',
        'GROUPS    {group: {stored value: key}} in menu order; label(group, value) shows the key\'s text.',
        '          A key None means the stored value is shown as it is (language-neutral).',
        'LOCALIZE  the keys whose zh text the core (or stored data) produces; i18n.localize maps such a',
        '          text to the running language.',
        'VARIANTS  older zh forms of a LOCALIZE key found in stored data: (key, zh template).',
        'NUMERIC   placeholders that capture digits only (code may be negative, size a decimal).',
        'RECURSE   captured placeholders that are core texts themselves: localized again with the key',
        '          prefixes given (None: all LOCALIZE keys); every other capture is inserted unchanged.',
        'RESERVED  keys that need no t() call (language-neutral texts listed by the inventory).',
        'NEW_KEYS  the keys of strings new in 1.7.0 (no 1.6.0 literal).',
        '"""',
        '',
        'GROUPS = {',
    ]
    for group, mapping in groups.items():
        lines.append(f'    {_py(group)}: {{')
        for value, key in mapping.items():
            lines.append(f'        {_py(value)}: {_py(key)},')
        lines.append('    },')
    lines.append('}')
    lines += ['', 'LOCALIZE = (']
    lines += [f'    {_py(key)},' for key in localize]
    lines.append(')')
    lines += ['', 'VARIANTS = (']
    lines += [f'    ({_py(key)}, {_py(template)}),' for key, template in variants]
    lines.append(')')
    lines += ['', 'NUMERIC = (']
    lines += [f'    {_py(name)},' for name in NUMERIC]
    lines.append(')')
    lines += ['', 'RECURSE = {']
    lines += [f'    {_py(name)}: {_py(prefixes)},' for name, prefixes in RECURSE.items()]
    lines.append('}')
    lines += ['', 'RESERVED = (']
    lines += [f'    {_py(key)},' for key in reserved]
    lines.append(')')
    lines += ['', 'NEW_KEYS = (']
    lines += [f'    {_py(key)},' for key in new_keys]
    lines.append(')')
    return '\n'.join(lines) + '\n'


def plan(inventory) -> dict:
    """Everything catalog() writes, without writing it: keys, module of each key, maps, problems."""
    keys = keyed_texts(inventory)
    problems = []
    modules = OrderedDict((name, []) for name in MODULES)
    for key, item in keys.items():
        modules[module_of(key, item['files'])].append(key)
        for language in ('zh', 'en'):
            try:
                fields = [field for _lit, field, _spec, _conv in _FORMATTER.parse(item[language]) if field is not None]
            except ValueError as error:
                problems.append(f'{key} {language}: does not parse ({error})')
                continue
            bad = [field for field in fields if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', field)]
            if bad:
                problems.append(f'{key} {language}: placeholders that are not names: {bad}')
    for name in modules:
        modules[name].sort()
    groups = group_maps(keys)
    localize = localize_keys(keys)
    exact = defaultdict(set)
    for key in localize:
        if not HOLDER.search(keys[key]['zh']):
            exact[keys[key]['zh']].add(keys[key]['en'])
    for text, english in exact.items():
        if len(english) > 1:
            problems.append(f'LOCALIZE text {text!r} has two English texts: {sorted(english)}')
    variants = [(item['key'], item['zh_template']) for item in inventory.get('localize_zh_variants', [])]
    grouped = {key for mapping in groups.values() for key in mapping.values() if key}
    reserved = sorted(key for key, item in keys.items() if item['source'] == 'extra' and key not in grouped)
    new_keys = [item['key'] for item in inventory.get('new_strings_1_7_0', [])]
    return {'keys': keys, 'modules': modules, 'groups': groups, 'localize': localize, 'variants': variants,
            'reserved': reserved, 'new_keys': new_keys, 'tags': log_tags(keys, inventory), 'problems': problems}


def catalog(inventory=None, src=SRC, inventory_copy=INVENTORY) -> dict:
    """Write i18n_maps.py and the eight catalogs into src; returns plan() (raises on a problem)."""
    if inventory is None or isinstance(inventory, (str, Path)):
        source = Path(inventory) if inventory else SPEC_INVENTORY
        raw = source.read_bytes()
        inventory = json.loads(raw.decode('utf-8'))
    else:
        raw = (json.dumps(inventory, ensure_ascii=False, indent=1) + '\n').encode('utf-8')
    result = plan(inventory)
    if result['problems']:
        raise ValueError('catalog problems:\n' + '\n'.join(result['problems']))
    src = Path(src)
    written = {}
    for name, entries in result['modules'].items():
        text = _catalog_module(name, entries, result['keys'], result['tags'] if name == 'log' else None)
        written[f'i18n_{name}.py'] = text
    written['i18n_maps.py'] = _maps_module(result['groups'], result['localize'], result['variants'],
                                           result['reserved'], result['new_keys'])
    for file, text in written.items():
        with open(src / file, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
    if inventory_copy is not None:
        Path(inventory_copy).write_bytes(raw)
    result['written'] = sorted(written)
    return result


# --------------------------------------------------------------------------------- parity table

def _cjk_leaves(value, depth=0):
    """Every str containing CJK inside a constant (dict keys and values, sequences, dataclasses)."""
    if depth > 6:
        return
    if isinstance(value, str):
        if CJK.search(value):
            yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _cjk_leaves(key, depth + 1)
            yield from _cjk_leaves(item, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in (sorted(value, key=str) if isinstance(value, (set, frozenset)) else value):
            yield from _cjk_leaves(item, depth + 1)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _cjk_leaves(getattr(value, field.name), depth + 1)


def key_of_text(text, inventory, prefer_files=()) -> str | None:
    """The inventory key whose zh text is ``text`` (a stored or constant value), preferring entries
    of the given files; f-string and str.format templates compare by shape."""
    target = shape(text, 'str') if HOLDER.search(text) else text
    found = []
    for entry in inventory['entries']:
        key = entry.get('key')
        if not key or not entry.get('translate'):
            continue
        candidates = {entry['text']} if entry['literal'] == 'string' else set()
        if entry.get('zh_template'):
            candidates.add(entry['zh_template'].replace('{{', '{').replace('}}', '}'))
        shapes = {shape(item, 'str') for item in candidates}
        if text in candidates or target in shapes:
            found.append((entry['file'].split('/')[-1] not in prefer_files, key))
    for extra in inventory.get('extra_display_strings_without_cjk', []):
        if extra['text'] == text:
            found.append((True, extra['key']))
    return min(found)[1] if found else None


def _referenced_constants(verification):
    """{(module, NAME)} read by verification/*.py, and the Tk-variable comparisons with a CJK literal."""
    constants, variables = set(), set()
    for path in sorted(Path(verification).glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError):
            continue
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in PARITY_MODULES:
                        aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module in PARITY_MODULES:
                for alias in node.names:
                    if UPPER.match(alias.name):
                        constants.add((node.module, alias.name, path.name))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id in aliases and UPPER.match(node.attr):
                constants.add((aliases[node.value.id], node.attr, path.name))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ('assertEqual', 'assertNotEqual', 'assertIn'):
                pairs = [node.args[:2]] if len(node.args) >= 2 else []
            elif isinstance(node, ast.Compare) and len(node.comparators) == 1:
                pairs = [(node.left, node.comparators[0])]
            else:
                pairs = []
            for a, b in pairs:
                for call, other in ((a, b), (b, a)):
                    name = _variable_get(call)
                    if name and isinstance(other, ast.Constant) and isinstance(other.value, str) \
                            and CJK.search(other.value):
                        variables.add((name, other.value, path.name))
    return constants, variables


def _variable_get(node):
    """'name' for an expression ``<...>app.<name>.get()``, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' \
            and not node.args and isinstance(node.func.value, ast.Attribute):
        owner = node.func.value.value
        if (isinstance(owner, ast.Name) and owner.id == 'app') or \
                (isinstance(owner, ast.Attribute) and owner.attr == 'app'):
            return node.func.value.attr
    return None


def _relation(value, key, keys):
    """How a constant's text relates to its key's zh text: 'equal', or 'prefix' (the constant is
    the literal start of a template, e.g. FINISHED_PREFIX), else 'differs'."""
    zh = keys.get(key, {}).get('zh') if key else None
    if zh is None:
        return 'missing', None
    if zh.replace('{{', '{').replace('}}', '}') == value or zh == value:
        return 'equal', zh
    start = next(iter(_FORMATTER.parse(zh)), ('', None, None, None))[0]
    if start and start == value:
        return 'prefix', zh
    return 'differs', zh


def parity_table(verification=VERIFICATION, inventory=None) -> list:
    """Rows {kind, module, name, value, key, zh, relation, tests}: a constant or Tk variable that
    tests read (or that I18N.md 8.1 names) with its Chinese value, paired with the inventory key of
    that text (key None: not found).  Imports the src modules (run under _sandbox)."""
    inventory = inventory or load_inventory()
    keys = keyed_texts(inventory)
    constants, variables = _referenced_constants(verification)
    tests = defaultdict(set)
    for module, name, test in constants:
        tests[(module, name)].add(test)
    for module, names in H4_CONSTANTS.items():
        for name in names:
            tests[(module, name)].add('I18N.md 8.1')
    rows = []
    for module, name in sorted(tests):
        try:
            value = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            continue
        for leaf in dict.fromkeys(_cjk_leaves(value)):
            key = key_of_text(leaf, inventory, (f'{module}.py',))
            relation, zh = _relation(leaf, key, keys)
            rows.append(OrderedDict(kind='constant', module=module, name=name, value=leaf, key=key, zh=zh,
                                    relation=relation, tests=sorted(tests[(module, name)])))
    seen = defaultdict(set)
    for name, literal, test in variables:
        seen[(name, literal)].add(test)
    for (name, literal), test_files in sorted(seen.items()):
        key = key_of_text(literal, inventory, UI_FILES)
        relation, zh = _relation(literal, key, keys)
        rows.append(OrderedDict(kind='variable', module='studio', name=f'StudioApp.{name}', value=literal, key=key,
                                zh=zh, relation=relation, tests=sorted(test_files)))
    return rows


def parity_definitions(src=SRC, table=None) -> dict:
    """{file: [(first line, last line)]} of the module-level assignments of the parity constants."""
    table = table if table is not None else parity_table()
    names = defaultdict(set)
    for row in table:
        if row['kind'] == 'constant':
            names[f'{row["module"]}.py'].add(row['name'])
    spans = defaultdict(list)
    for file, wanted in names.items():
        tree = ast.parse((Path(src) / file).read_text(encoding='utf-8'))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] \
                if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(target, ast.Name) and target.id in wanted for target in targets):
                spans[file].append((node.lineno, node.end_lineno))
    return dict(spans)


# --------------------------------------------------------------------------------- suspects

_STORED_NAMES = ('record', 'data', 'entry', 'row', 'value', 'errors')
_WRAPPERS = ('localize', 'label', 't')


def _is_wrapped(node) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ''
        return name in _WRAPPERS
    return False


def _suspect_expression(node) -> bool:
    if _is_wrapped(node):
        return False
    if isinstance(node, ast.Attribute) and node.attr in ('label', 'mode'):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ''
        if name == 'engine_version':
            return True
        if name == 'get' and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id in _STORED_NAMES:
            return True
    if isinstance(node, ast.Subscript):
        base = node.value
        while isinstance(base, ast.Subscript):
            base = base.value
        if isinstance(base, ast.Name) and base.id in _STORED_NAMES:
            return True
    return False


def suspects(src=SRC, files=UI_FILES) -> list:
    """[{file, line, expr, site}] of interpolated stored / core values not wrapped in localize,
    label or t: in f-strings and in the keyword values of t() calls (I18N.md 9.3)."""
    found = []
    for file in files:
        path = Path(src) / file
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            expressions = []
            if isinstance(node, ast.JoinedStr):
                expressions = [(value.value, 'f-string') for value in node.values if isinstance(value, ast.FormattedValue)]
            elif isinstance(node, ast.Call) and _is_wrapped(node) and \
                    (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id) == 't':
                expressions = [(keyword.value, 't()') for keyword in node.keywords]
            for expression, site in expressions:
                if _suspect_expression(expression):
                    found.append(OrderedDict(file=file, line=expression.lineno, expr=ast.unparse(expression), site=site))
    found.sort(key=lambda item: (item['file'], item['line'], item['expr']))
    return found


def unexplained_suspects(src=SRC, files=UI_FILES) -> list:
    return [item for item in suspects(src, files) if (item['file'], item['expr']) not in SUSPECT_ALLOW]


# --------------------------------------------------------------------------------- calls of the i18n API

_API = {'t': 'key', 'all_values': 'key', 'label': 'group', 'choices': 'group', 'sep': 'key'}
# sep(name) shows the key 'common.' + name (I18N.md 3.8); api_calls reports it under that key (L2).
_KEY_PREFIX = {'sep': 'common.'}


def api_calls(src=SRC) -> list:
    """[{file, line, function, kind, name}] of i18n.t / label / choices / all_values / sep calls (also
    as bare names imported from i18n) whose first argument is a constant string; for sep the name is
    the key it shows ('common.<name>')."""
    found = []
    for path in sorted(Path(src).glob('*.py')):
        if path.name == 'i18n.py' or path.name.startswith('i18n_'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        bare = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'i18n':
                bare |= {alias.asname or alias.name for alias in node.names if alias.name in _API}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'i18n' \
                    and func.attr in _API:
                name = func.attr
            elif isinstance(func, ast.Name) and func.id in bare:
                name = func.id
            else:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append(OrderedDict(file=path.name, line=node.lineno, function=name, kind=_API[name],
                                         name=_KEY_PREFIX.get(name, '') + first.value))
    return found


# --------------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('scan')
    one.add_argument('--src', default=str(SRC))
    one.add_argument('--out')
    two = sub.add_parser('catalog')
    two.add_argument('--inventory', default=str(SPEC_INVENTORY))
    two.add_argument('--src', default=str(SRC))
    sub.add_parser('parity')
    sub.add_parser('suspects')
    options = parser.parse_args(argv)
    if options.command == 'scan':
        records = scan(options.src)
        cjk = [r for r in records if r['cjk'] and not r['docstring']]
        if options.out:
            Path(options.out).write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding='utf-8')
        print('all literals', len(records), 'cjk non-docstring', len(cjk))
        for name, count in sorted(Counter(r['file'] for r in cjk).items()):
            print(f'  {name}: {count}')
        return 0
    if options.command == 'catalog':
        result = catalog(options.inventory, options.src)
        for name, entries in result['modules'].items():
            print(f'i18n_{name}.py: {len(entries)} keys')
        print(f'i18n_maps.py: {len(result["groups"])} groups, {len(result["localize"])} LOCALIZE keys, '
              f'{len(result["variants"])} variants, {len(result["tags"])} log tags')
        return 0
    sys.path.insert(0, str(VERIFICATION))
    import _sandbox  # noqa: F401  (src imports below run inside a sandbox)
    if options.command == 'parity':
        for row in parity_table():
            print(f'{row["kind"]:8} {row["module"]}.{row["name"]:28} {row["key"]!s:40} {row["relation"]:7} '
                  f'{row["value"]!r}')
        return 0
    for item in suspects():
        allowed = SUSPECT_ALLOW.get((item['file'], item['expr']))
        print(f'{item["file"]}:{item["line"]} {item["site"]} {item["expr"]}' + (f'   allowed: {allowed}' if allowed else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
