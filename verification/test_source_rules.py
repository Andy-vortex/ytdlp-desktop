# quick
"""Rules read from the source files (2.0.1; replaces test_text_guard.py and its data flow).

    python -X utf8 -B test_source_rules.py

S1  every Chinese literal of src outside the catalogs (src/i18n_*.py) is listed, with a reason, under its module
    in verification/golden/text-allow.json; no entry is stale or the Chinese of a catalog key; ALLOW_COUNT pins
    the number of entries (only texts that are never shown may be listed: log lines, names on disk, stored marks)
S2  the core modules do not use i18n (they write messages.msg; library writes its owner files with filetext.*)
S3  no `from i18n import`; no legacy catalog or data converter in src
R1  every repository text file uses LF     R2  no personal data in a repository file
R3  repo_files reads .gitignore / .gitattributes the way git does
No window, no network, no src import.
"""
import _sandbox
import ast
import json
import os
import re
import subprocess
import textwrap
import unittest

import i18n_tool
import repo_files

ALLOW_FILE = _sandbox.ROOT / 'verification' / 'golden' / 'text-allow.json'
# Pinned on purpose: a text a user can see (even in a rare state) goes into a catalog, never into this list.
ALLOW_COUNT = 30
# Listed texts that equal the Chinese of a catalog key.  Each is a name on disk or a stored mark that happens to read
# like an interface word; renaming it would lose existing data.  Only "equal -> reported" is checked, never "still
# equal": the wording drill (i18n_drill.py) changes the catalog texts.
EXCEPTIONS = {
    ('engine', '媒体'),             # the archive's media subfolder on disk (value.link_kind.media)
    ('library', '来源'),            # the archive's source folder on disk when a link has no host (table.source)
    ('library', '网页正文.txt'),    # the page text file in an archive (dialog.export_text.file)
    ('library', '转写稿'),          # the transcripts folder in an archive (dialog.filetype.transcript)
    ('messages', '[代理]'),         # the stored proxy mask, shown as core.proxy_mask
}
CORE_MODULES = ('engine', 'unfinished', 'library', 'collector', 'updater', 'urls', 'formats', 'naming')

HOW_LISTED = ('是写在代码里的中文。显示给用户的文字放进 src\\i18n_<页>.py 的 TEXTS，代码里写 i18n.t(\'编号\')'
              '（核心模块写 messages.msg(\'编号\')）。只有写进日志文件、磁盘文件名或存储标记、从不显示的中文，'
              '才登记到 verification\\golden\\text-allow.json 的 "{module}" 下，写一句理由（它去哪）；'
              '登记会让 ALLOW_COUNT 检查失败，见那条报错。')


def chinese_literals(tree):
    """(line, text) of every str literal and f-string of a parsed module that holds Chinese; docstrings are left
    out and an f-string is one literal (its text with the {holes})."""
    skip = i18n_tool.docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                skip.add(id(value))
                if isinstance(value, ast.FormattedValue) and value.format_spec is not None:
                    skip |= {id(value.format_spec)} | {id(item) for item in value.format_spec.values}
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = i18n_tool._fstring_text(node)[0]
        else:
            continue
        if i18n_tool.CJK.search(text):
            yield node.lineno, text


def literal_problems(src, allow, catalog, exceptions=EXCEPTIONS):
    """S1 on a src folder: allow is {module: {text: reason}}, catalog {zh text: [keys]}."""
    problems = []
    modules = sorted(path.stem for path in src.glob('*.py') if not path.stem.startswith('i18n_'))
    stale = []
    for module in modules:
        listed = allow.get(module, {})
        found = set()
        for line, text in chinese_literals(ast.parse((src / f'{module}.py').read_text(encoding='utf-8'))):
            found.add(text)
            if text not in listed:
                problems.append(f'src/{module}.py:{line} {text!r} ' + HOW_LISTED.format(module=module))
        stale += [(module, text) for text in sorted(set(listed) - found)]
    left = sum(map(len, allow.values())) - len(stale)
    for module, text in stale:
        problems.append(f'text-allow.json "{module}": {text!r} 在 src/{module}.py 里已经没有了：删掉这一条；'
                        f'删完全部 {len(stale)} 条过期条目后，把 ALLOW_COUNT 改成 {left}。')
    for module, entries in sorted(allow.items()):
        if module not in modules:
            problems.append(f'text-allow.json "{module}"：没有 src/{module}.py（目录文件不登记）：删掉这一段。')
            continue
        if not isinstance(entries, dict):
            problems.append(f'text-allow.json "{module}" 要写成 {{"文字": "理由"}}。')
            continue
        for text, reason in sorted(entries.items()):
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f'text-allow.json "{module}": {text!r} 缺理由：写一句它去哪'
                                f'（日志文件 / 磁盘名 / 存储标记 / 字符表）。')
            if text in catalog and (module, text) not in exceptions:
                keys = ', '.join(catalog[text])
                problems.append(f'text-allow.json "{module}": {text!r} 就是目录里 {keys} 的中文：这是界面文字，'
                                f"代码里写 i18n.t('{catalog[text][0]}')（核心模块写 messages.msg），不要登记。")
    return problems


def catalog_zh(src=i18n_tool.SRC):
    found = {}
    for key, text in sorted(i18n_tool.catalog_texts(src)['zh'].items()):
        found.setdefault(text, []).append(key)
    return found


def load_allow():
    return json.loads(ALLOW_FILE.read_text(encoding='utf-8'))


def parsed_src():
    """(module, tree) of every src module outside the catalogs."""
    for path in sorted(i18n_tool.SRC.glob('*.py')):
        if not path.stem.startswith('i18n_'):
            yield path.stem, ast.parse(path.read_text(encoding='utf-8'))


class LiteralTests(unittest.TestCase):
    """S1: the Chinese literals of src."""

    def test_every_chinese_literal_is_listed_with_a_reason(self):
        problems = literal_problems(i18n_tool.SRC, load_allow(), catalog_zh())
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_the_number_of_listed_texts_is_pinned(self):
        count = sum(len(entries) for entries in load_allow().values())
        self.assertEqual(count, ALLOW_COUNT,
                         f'text-allow.json 有 {count} 条，ALLOW_COUNT 钉的是 {ALLOW_COUNT}。界面上看得到的文字（包括只在少见'
                         f'状态出现的）必须进目录，不能登记；登记的只能是写进日志文件、磁盘名、存储标记、从不显示的中文。'
                         f'ALLOW_COUNT 是故意钉死的：只在确认这段中文从不显示时才改它，并在交接里写明这一条去哪。')

    def test_the_check_on_a_synthetic_module(self):
        """S1 sees what it should: an unlisted literal, a stale entry and an entry that is a catalog text are
        reported, nothing else (a docstring, a listed literal, a listed f-string, an exception pair)."""
        src = _sandbox.out('source-rules', 'src')
        src.mkdir(parents=True, exist_ok=True)
        for old in src.glob('*.py'):
            old.unlink()
        (src / 'probe.py').write_text(textwrap.dedent('''
            """模块说明不算。"""
            NAME = '磁盘名'
            SHOWN = '未登记'
            MARK = '假目录'


            def line(count):
                """函数说明也不算。"""
                return f'共 {count} 条'
        ''').lstrip('\n'), encoding='utf-8')
        (src / 'i18n_probe.py').write_text("TEXTS = {'probe.x': ('目录文件不扫', 'not scanned')}\n", encoding='utf-8')
        allow = {'probe': {'磁盘名': 'a name on disk', '共 {count} 条': 'only written to the log file',
                           '假目录': 'a stored mark', '已删掉': 'stale'}}
        catalog = {'假目录': ['probe.mark'], '磁盘名': ['probe.disk']}
        problems = literal_problems(src, allow, catalog, exceptions={('probe', '磁盘名')})
        self.assertEqual(len(problems), 3, '\n'.join(problems))
        self.assertTrue(problems[0].startswith("src/probe.py:3 '未登记' 是写在代码里的中文"), problems[0])
        self.assertIn("'已删掉' 在 src/probe.py 里已经没有了", problems[1])
        self.assertIn('ALLOW_COUNT 改成 3。', problems[1])
        self.assertIn("'假目录' 就是目录里 probe.mark 的中文", problems[2])
        # two stale entries at once: each names the count after both are gone
        two = {'probe': dict(allow['probe'], 也删掉='stale')}
        stale = [item for item in literal_problems(src, two, catalog, exceptions={('probe', '磁盘名')})
                 if '已经没有了' in item]
        self.assertEqual(len(stale), 2, stale)
        self.assertTrue(all('全部 2 条过期条目后，把 ALLOW_COUNT 改成 3。' in item for item in stale), stale)
        # without its catalog collision the exception pair is listed like any other entry; an empty reason, a
        # module with no file and a stale entry make every entry stale when the scanner sees nothing
        problems = literal_problems(src, {'probe': {'磁盘名': ' '}, 'gone': {}}, {})
        self.assertTrue(any("'磁盘名' 缺理由" in item for item in problems), problems)
        self.assertTrue(any('没有 src/gone.py' in item for item in problems), problems)
        (src / 'probe.py').write_text('X = 1\n', encoding='utf-8')
        stale = [item for item in literal_problems(src, allow, {}) if '已经没有了' in item]
        self.assertEqual(len(stale), 4, stale)


class BoundaryTests(unittest.TestCase):
    """S2, S3."""

    @staticmethod
    def _filetext_call(node, parents):
        """library's i18n.t('filetext.…', …): the labels of the files it writes for the owner."""
        attribute = parents.get(id(node))
        call = parents.get(id(attribute))
        return isinstance(attribute, ast.Attribute) and attribute.attr == 't' and isinstance(call, ast.Call) \
            and call.func is attribute and call.args and isinstance(call.args[0], ast.Constant) \
            and isinstance(call.args[0].value, str) and call.args[0].value.startswith('filetext.')

    def test_core_modules_do_not_use_i18n(self):
        problems = []
        how = ('核心模块不用 i18n：要显示或存储的文字写 messages.msg(\'编号\', …)，界面在显示时按当时的语言翻译。'
               '（library 写给用户的文件可以用 i18n.t(\'filetext.…\')）')
        for module, tree in parsed_src():
            if module not in CORE_MODULES:
                continue
            parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    used = any(alias.name.split('.')[0] == 'i18n' for alias in node.names)
                    allowed = module == 'library' and [alias.name for alias in node.names] == ['i18n'] \
                        and node.names[0].asname is None
                elif isinstance(node, ast.ImportFrom):
                    used, allowed = (node.module or '').split('.')[0] == 'i18n', False
                elif isinstance(node, ast.Name):
                    used = node.id == 'i18n'
                    allowed = module == 'library' and self._filetext_call(node, parents)
                elif isinstance(node, ast.Attribute):
                    used, allowed = node.attr == 'i18n', False
                else:
                    continue
                if used and not allowed:
                    problems.append(f'src/{module}.py:{node.lineno} {how}')
        self.assertEqual(problems, [], '\n'.join(problems))
        self.assertEqual(sorted(set(CORE_MODULES) - {module for module, _tree in parsed_src()}), [],
                         'a core module was renamed: update CORE_MODULES')

    def test_no_from_i18n_import_and_no_legacy_files(self):
        problems = []
        for module, tree in parsed_src():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 'i18n' and node.level == 0:
                    problems.append(f'src/{module}.py:{node.lineno} 写 import i18n，再用 i18n.t(...)：'
                                    f'from i18n import t 会让 S2 和改字演练都看不见这次调用。')
        gone = sorted(path.name for pattern in ('i18n_legacy*.py', 'upgrade_v2*.py')
                      for path in i18n_tool.SRC.glob(pattern))
        problems += [f'src/{name} 不该存在：2.0.0 不读旧版数据，也不留旧目录（AGENTS.md"数据格式"）。' for name in gone]
        self.assertEqual(problems, [], '\n'.join(problems))


class TreeTests(unittest.TestCase):
    """R1-R3 (moved unchanged from test_text_guard.TreeTests)."""

    def test_text_files_use_lf(self):
        """(2.0.0 S1; replaces the 1.8.0 rule "keep 1.7.0's line endings") Every repository text file
        uses LF.  The repository files are those .gitignore does not exclude; .gitattributes says which
        are text: everything but the binary types.  (2.0.0 S2 Q4) No exception: the one frozen folder
        (the release sources) is gone."""
        root = _sandbox.ROOT
        rules = repo_files.load_attributes(root)
        self.assertEqual(rules[0], ('*', {'text': 'auto', 'eol': 'lf'}), 'the first rule of .gitattributes')
        frozen = sorted(pattern for pattern, values in rules if values.get('text') is False and not values.get('binary'))
        self.assertEqual(frozen, [],
                         'a new unconverted folder needs a reason in the 2.0.0 plan, not only a .gitattributes line')
        crlf, unmarked, text = [], [], []
        for relative in repo_files.repository_files(root):
            if repo_files.attributes_of(relative, rules).get('text') is False:
                continue
            data = (root / relative).read_bytes()
            if b'\0' in data:
                unmarked.append(relative)
            elif b'\r' in data:
                crlf.append(relative)
            else:
                text.append(relative)
        self.assertEqual(crlf, [], 'convert these to LF (the content stays the same): data.replace(b"\\r\\n", b"\\n")')
        self.assertEqual(unmarked, [], 'binary files: add their type to .gitattributes as "binary"')
        for name in ('src/library_ui.py', 'src/engine.py', 'verification/golden/stored-keys.json', 'requirements-lock.txt',
                     'requirements-build.txt', 'app.spec', 'vendor.lock.json', '.gitattributes', 'README.md'):
            self.assertIn(name, text)
        # (2.0.1) 128 text files: the 23 allow files, test_text_guard, guard_contract and docs/GUARDS.md went, test_v201_fixes
        # came (185 at 2.0.0 S1)
        self.assertGreater(len(text), 100, 'the walk found too few files')

    # (2.0.0 S5) The made-up user folder names the tests use; any other name after <drive>:\Users\ is real.
    PLACEHOLDER_USERS = frozenset({'某人', 'x', 'zz-probe-user', '...', '…', '<you>', '<user>', 'Public'})
    # Reserved test domains (RFC 2606 / 6761): the only e-mail addresses a public repository may hold.
    PLACEHOLDER_MAIL = re.compile(r'@(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|example|test|invalid)$', re.I)

    def test_repository_files_hold_no_personal_data(self):
        """(2.0.0 S5, AGENTS.md "Hard rules") The repository is public: no repository text file holds this
        machine's user name, the user folder, a real <drive>:\\Users\\<name> path, the git e-mail or any
        e-mail address outside the reserved test domains."""
        root = _sandbox.ROOT
        rules = repo_files.load_attributes(root)
        needles = set()
        name = os.environ.get('USERNAME', '').strip()
        if len(name) >= 3:
            needles.add(name)
        home = os.environ.get('USERPROFILE', '').strip()
        if home:
            needles |= {home, home.replace('\\', '/'), home.replace('\\', '\\\\')}
        try:
            mail = subprocess.run(['git', 'config', 'user.email'], cwd=root, capture_output=True, text=True,
                                  timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            mail = ''
        if mail:
            needles.add(mail)
        users = re.compile(r'[A-Za-z]:(?:\\|/)+Users(?:\\|/)+([^\\/\s"\'`<>:;,)]+)', re.I)
        address = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
        findings, scanned = [], 0
        for relative in repo_files.repository_files(root):
            if repo_files.attributes_of(relative, rules).get('text') is False:
                continue
            try:
                text = (root / relative).read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), 1):
                lowered = line.lower()
                where = f'{relative}:{number}'
                findings += [f'{where}: this machine\'s user name, user folder or git e-mail'
                             for needle in needles if needle.lower() in lowered][:1]
                findings += [f'{where}: a user folder {match.group(0)!r}' for match in users.finditer(line)
                             if match.group(1) not in self.PLACEHOLDER_USERS]
                findings += [f'{where}: an e-mail address {match.group(0)!r}' for match in address.finditer(line)
                             if not self.PLACEHOLDER_MAIL.search(match.group(0))]
        self.assertGreater(scanned, 100, 'the walk found too few files (128 at 2.0.1)')
        self.assertEqual(findings, [], 'personal data in a repository file: write the path relative to the repository '
                                       'root or with a placeholder (%LOCALAPPDATA%, C:\\Users\\<you>), and an address '
                                       'only on a reserved test domain (example.org)')

    def test_the_repository_file_rules(self):
        """repo_files reads .gitignore / .gitattributes the way git does for the patterns they use."""
        self.assertTrue(repo_files.matches('verification/*.json', 'verification/report.json'))
        self.assertFalse(repo_files.matches('verification/*.json', 'verification/golden/a.json'))
        self.assertTrue(repo_files.matches('docs/*/handoff', 'docs/2.0.0/handoff'))
        self.assertTrue(repo_files.matches('*.png', 'docs/screenshots/a.png'))
        self.assertTrue(repo_files.matches('verification/x/**', 'verification/x/1.7.0/src/x.py'))
        self.assertFalse(repo_files.matches('verification/x/**', 'verification/x.json'))
        self.assertTrue(repo_files.matches('__pycache__', 'src/__pycache__'))
        rules = [('build', False, True), ('verification/*.json', False, False),
                 ('verification/keep.json', True, False), ('*.log', False, False)]
        self.assertTrue(repo_files.ignored('build', True, rules))
        self.assertFalse(repo_files.ignored('build', False, rules), 'a folder rule does not apply to a file')
        self.assertTrue(repo_files.ignored('verification/report.json', False, rules))
        self.assertFalse(repo_files.ignored('verification/keep.json', False, rules))
        self.assertTrue(repo_files.ignored('src/x.log', False, rules))
        files = repo_files.repository_files(_sandbox.ROOT)
        self.assertIn('verification/golden/stored-keys.json', files)
        self.assertIn('verification/golden/text-allow.json', files)      # (2.0.1) the allow list is tracked
        self.assertFalse([name for name in files if repo_files.matches('verification/*.json', name)], 'ignored')
        self.assertFalse([name for name in files if name.startswith(('build/', 'vendor/', '.venv/', '.git/', 'data/'))
                          or '__pycache__' in name or '/handoff/' in name])
        attributes = repo_files.load_attributes(_sandbox.ROOT)
        self.assertIs(repo_files.attributes_of('app.ico', attributes).get('text'), False)
        self.assertEqual(repo_files.attributes_of('src/library_ui.py', attributes), {'text': 'auto', 'eol': 'lf'})
        # a later "-text" line overrides the first rule for the files it matches (a made-up folder)
        unconverted = attributes + [('verification/x/**', {'text': False})]
        self.assertIs(repo_files.attributes_of('verification/x/1.7.0/src/library_ui.py', unconverted).get('text'), False)
        self.assertEqual(repo_files.attributes_of('src/library_ui.py', unconverted), {'text': 'auto', 'eol': 'lf'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
