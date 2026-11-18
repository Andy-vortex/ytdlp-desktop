"""Static guards G1 (G1a-G1f) and G5 with their self-tests (MESSAGES.md 7.2, 7.8, 7.9; WP-1).

    python -X utf8 -B test_text_guard.py

G1 runs on every src module (the catalogs and the generated data excepted) and G5 on every UI
module, unconditionally (WP-6 removed the staging flags, MESSAGES.md 7.8); the release gate (no
MESSAGES_READY flag, no localize call, no pending_keys, no stale allow entry) is checked here as well
(10.3 WP-6).  The self-tests run the scanners on the frozen
1.7.0 sources (verification/release-sources/1.7.0) with the 1.8.0 allow files and on synthetic
modules written to the sandbox: the guards must catch 媒体直链 and 动态页面媒体请求 (the misses of
1.7.0's own test 6), local / enum-source bindings in text slots, composed text in core slots, keys
that are not literals, i18n in core modules, unshown values at UI sinks and the six str(error)
dialogs of 1.7.0.  No window, no network, no src import except through the scanner's AST.
"""
import _sandbox
import ast
import json
import textwrap
import unittest

import guard_contract
import i18n_tool

OLD = _sandbox.release_src('1.7.0')


def lines(findings, rule=None):
    return sorted({(item['file'], item['line']) for item in findings if rule is None or item['rule'] == rule})


def synthetic(name, source):
    folder = _sandbox.out('guard-synthetic', 'src')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{name}.py'
    path.write_text(textwrap.dedent(source).lstrip('\n'), encoding='utf-8')
    return folder


def line_of(folder, name, needle):
    for number, text in enumerate((folder / f'{name}.py').read_text(encoding='utf-8').splitlines(), 1):
        if needle in text:
            return number
    raise AssertionError(needle)


class TreeTests(unittest.TestCase):
    """G1 on the modules it applies to now, the allow files and the boundaries."""

    def test_g1_is_clean_on_every_src_module(self):
        modules = i18n_tool.g1_modules()
        self.assertEqual(modules, [name for name in i18n_tool.src_modules() if name not in i18n_tool.G1_EXCLUDED])
        for name in i18n_tool.CORE_MODULES + i18n_tool.UI_MODULES + ('app', 'ui_theme', 'messages', 'i18n'):
            self.assertIn(name, modules)
        findings = i18n_tool.g1_scan()
        self.assertEqual([f'{f["rule"]} {f["file"]}:{f["line"]} {f["text"][:60]!r} ({f["detail"]})' for f in findings], [])
        print('G1 applies to: ' + ', '.join(modules))

    def test_g5_is_clean_on_every_ui_module(self):
        findings = i18n_tool.sinks_scan()
        self.assertEqual([f'{f["file"]}:{f["line"]} {f["text"][:60]!r} ({f["detail"]})' for f in findings], [])
        print('G5 applies to: ' + ', '.join(i18n_tool.UI_MODULES) + ' and library (filetext)')

    def test_the_release_gate(self):
        """(WP-6, MESSAGES.md 7.2 / 7.8 / 10.3) no staging flag or set, no localize, no pending_keys, no
        literal of category pending, no stale allow entry, no G1 / G5 finding."""
        gate = i18n_tool.release_gate()
        self.assertEqual({name: value for name, value in gate.items() if value}, {})
        self.assertEqual(i18n_tool.ready_flags(), [])
        for name in i18n_tool.src_modules():
            tree = ast.parse((i18n_tool.SRC / f'{name}.py').read_text(encoding='utf-8'))
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            self.assertFalse(names & {'MESSAGES_READY', 'TRANSIT_UNTIL_WP6', 'RUNTIME_ALWAYS'}, name)
        print('release gate: ' + ', '.join(f'{name} {len(value)}' for name, value in gate.items()))

    def test_the_gate_sees_a_flag_a_pending_key_and_a_stale_entry(self):
        """Self-test of release_gate on a synthetic tree: each kind of leftover is reported."""
        folder = _sandbox.out('guard-synthetic-gate', 'src')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'naming.py').write_text('MESSAGES_READY = True\n' "NAMING = ('title',)\n", encoding='utf-8')
        allow_dir = _sandbox.out('guard-synthetic-gate', 'allow')
        allow_dir.mkdir(parents=True, exist_ok=True)
        (allow_dir / 'naming.json').write_text(json.dumps({
            'module': 'naming', 'literals': {'无此文字': {'category': 'pending', 'reason': 'self-test'}},
            'composed': {"f'{gone}'": {'reason': 'self-test'}},
            'pending_keys': {'core.x': {'zh': '无此文字', 'en': 'x', 'reason': 'self-test'}}}), encoding='utf-8')
        gate = i18n_tool.release_gate(folder, allow_dir)
        self.assertEqual(gate['ready_flags'], ['naming'])
        self.assertEqual(gate['pending_keys'], ['naming: core.x'])
        self.assertEqual(gate['pending_literals'], ['naming: 无此文字'])
        self.assertEqual(sorted(gate['stale']), ["naming: composed f'{gone}'", 'naming: literals 无此文字'])

    def test_every_src_module_has_a_well_formed_allow_file(self):
        names = set(i18n_tool.src_modules())
        files = {path.stem for path in i18n_tool.ALLOW_DIR.glob('*.json')}
        self.assertEqual(files, names)
        pending = []
        for name in sorted(names):
            data = json.loads((i18n_tool.ALLOW_DIR / f'{name}.json').read_text(encoding='utf-8'))
            self.assertEqual(data.get('module'), name)
            for section in i18n_tool.ALLOW_SECTIONS:
                self.assertIsInstance(data.get(section), dict, f'{name}: {section}')
            for text, entry in data['literals'].items():
                self.assertIn(entry.get('category'), i18n_tool.CATEGORIES, f'{name}: {text!r}')
                self.assertTrue(entry.get('reason'), f'{name}: {text!r} needs a reason')
                if entry['category'] == 'token':
                    self.assertIn(name, i18n_tool.TOKEN_MODULES)
                if entry['category'] == 'h4-unused':
                    self.assertTrue(entry.get('name'), f'{name}: {text!r} names its constant')
            for section in ('raises', 'composed', 'sinks'):
                for text, entry in data[section].items():
                    self.assertTrue(entry.get('reason'), f'{name}: {section} {text!r} needs a reason')
            pending += [f'{name}: {key}' for key in data['pending_keys']]
            pending += [f'{name}: {text} (category pending)' for text, entry in data['literals'].items()
                        if entry['category'] == 'pending']
        self.assertEqual(pending, [], 'the release gate fails on pending_keys (MESSAGES.md 10.2, 10.3 WP-6)')

    def test_no_stale_entry_in_any_allow_file(self):
        context = i18n_tool.GuardContext()
        for name in i18n_tool.src_modules():
            if name not in i18n_tool.G1_EXCLUDED:
                self.assertEqual(i18n_tool.allow_stale(context, name), [], name)

    def test_the_allow_files_leave_the_converted_producers_out(self):
        """10.2: literals that become messages are not listed, so a module fails G1 until converted."""
        context = i18n_tool.GuardContext()
        self.assertNotIn('媒体直链', context.allow('collector')['literals'])
        self.assertNotIn('网页', context.allow('collector')['literals'])
        self.assertNotIn('动态页面媒体请求', context.allow('collector')['literals'])
        self.assertEqual(context.allow('collector')['literals']['媒体地址']['category'], 'stored')
        self.assertNotIn('无法连接代理或网站，请检查代理地址', context.allow('ui_download')['literals'])
        self.assertEqual(context.allow('ui_download')['literals']['上次失败']['category'], 'h4-unused')
        self.assertEqual(i18n_tool._referenced_outside(context, 'ui_download', 'REASON_WORDS'), [])
        self.assertEqual(set(context.allow('library_ui')['raises']), {'the history job was never written', 'no options'})
        raises = sum(len(context.allow(name)['raises']) for name in i18n_tool.src_modules() if name != 'messages')
        self.assertEqual(raises, 18, 'the 18 internal English raises of 1.7.0 (7.2 G1e)')

    def test_boundaries(self):
        # G1d: the 1.7.0 baseline of 52 localize call lines is pinned on the frozen 1.7.0 sources;
        # from WP-6 on none is left in src (6.2: 52 -> 0), and i18n has no localize any more.
        self.assertEqual(i18n_tool.LOCALIZE_BASELINE, 52)
        self.assertEqual(len(i18n_tool.localize_lines(OLD)), i18n_tool.LOCALIZE_BASELINE)
        self.assertEqual(i18n_tool.localize_lines(), [])
        i18n_tree = ast.parse((i18n_tool.SRC / 'i18n.py').read_text(encoding='utf-8'))
        defined = {node.name for node in i18n_tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        self.assertFalse(defined & {'localize', '_Template', '_Index', '_get_index', '_allowed', '_localized',
                                    '_localize_in'}, '6.4: the reverse machinery left i18n.py')
        self.assertEqual(i18n_tool.NEUTRAL_FIELDS & i18n_tool.TEXT_KEYS, frozenset())
        for path in sorted(i18n_tool.SRC.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, 'i18n', f'{path.name}: from i18n import (G1d)')
                if isinstance(node, (ast.Import, ast.ImportFrom)) and path.stem != 'messages':
                    names = [alias.name for alias in node.names] + [getattr(node, 'module', None) or '']
                    self.assertNotIn('i18n_legacy', names, path.name)


    def test_line_endings_kept_from_1_7_0(self):
        """(final review 1, Q1) A src file 1.7.0 had keeps its line endings (library_ui.py is CRLF in
        git, the others LF), so the 1.8.0 diff shows only the lines that changed (spec 0)."""
        def style(data):
            crlf, lf = data.count(b'\r\n'), data.count(b'\n')
            return 'crlf' if crlf and crlf == lf else 'lf' if not crlf else 'mixed'

        checked = 0
        for old in sorted(OLD.glob('*.py')):
            new = i18n_tool.SRC / old.name
            if new.is_file():
                checked += 1
                self.assertEqual(style(new.read_bytes()), style(old.read_bytes()), old.name)
        self.assertGreater(checked, 20)
        self.assertEqual(style((i18n_tool.SRC / 'library_ui.py').read_bytes()), 'crlf')


class G1SelfTests(unittest.TestCase):
    def test_g1a_and_g1b_catch_the_1_7_0_collector_titles(self):
        context = i18n_tool.GuardContext()
        findings = i18n_tool.g1_module(context, 'collector', OLD)
        flagged = {(item['line'], item['text']) for item in findings if item['rule'] == 'G1a'}
        for line, text in ((122, '网页'), (156, '动态页面媒体请求'), (203, '媒体直链'), (204, '媒体直链')):
            self.assertIn((line, text), flagged)
        allowed = context.allow('collector')['literals']
        for text in ('媒体直链', '动态页面媒体请求'):          # what 1.7.0's GROUPS allowance amounted to
            allowed[text] = {'category': 'stored', 'reason': 'self-test'}
        context._enum = None
        findings = i18n_tool.g1_module(context, 'collector', OLD)
        g1b = {(item['line'], item['detail']) for item in findings if item['rule'] == 'G1b'}
        self.assertIn((203, "allow-listed text in dict 'title'"), g1b)
        self.assertIn((204, "allow-listed text in dict 'title'"), g1b)
        self.assertIn((156, 'allow-listed text in add(label)'), g1b)
        self.assertNotIn(203, {item['line'] for item in findings if item['rule'] == 'G1a'})

    def test_synthetic_core_module(self):
        folder = synthetic('collector', '''
            import formats
            import i18n
            import messages
            from messages import msg


            def page(url):
                kind = '媒体地址'
                page = {'url': url}
                page['title'] = kind
                return page


            def errors(emit):
                for preset in formats.PRESETS:
                    emit('log', msg('core.engine.error', error=preset.label))


            def fine(emit):
                for preset in formats.PRESETS:
                    emit('log', msg('core.engine.error', error=messages.value('format', preset.label)))


            def composed(m, x):
                return {'error': f'{m}：{x}'}


            def variable(key):
                return msg(key)


            def shown(mode):
                return i18n.label('mode', mode)


            def conditional(cancelled):
                return msg('value.subscription.stopped' if cancelled else 'value.subscription.unfinished')


            def placeholders():
                return msg('core.library.temporary'), msg('core.no.such.key')
            ''')
        context = i18n_tool.GuardContext()
        findings = i18n_tool.g1_module(context, 'collector', folder)
        found = {(item['rule'], item['line']) for item in findings}
        self.assertIn(('G1b', line_of(folder, 'collector', "page['title'] = kind")), found)
        self.assertIn(('G1b', line_of(folder, 'collector', 'error=preset.label')), found)
        self.assertNotIn(('G1b', line_of(folder, 'collector', "value('format', preset.label)")), found)
        self.assertIn(('G1f', line_of(folder, 'collector', "{'error': f'{m}")), found)
        self.assertIn(('G1c', line_of(folder, 'collector', 'msg(key)')), found)
        self.assertIn(('G1d', line_of(folder, 'collector', 'i18n.label')), found)
        self.assertEqual(len([item for item in findings if item['rule'] == 'G1c'
                              and item['line'] == line_of(folder, 'collector', 'core.no.such.key')]), 2)
        conditional = line_of(folder, 'collector', 'value.subscription.stopped')
        self.assertEqual([item for item in findings if item['line'] == conditional], [], 'C-F39 is accepted')

    def test_the_i1_scanner_fixes(self):
        """I-1 amendments A1 and A6 (scanner defects of WP-2 / WP-3, RV-I1-01): the text argument of a
        typed call other than msg(), bare or as messages.<name>, is G1b-checked but not G1f-checked; a formatted number is no composition; an id-keyed
        table's keys are no text slots while its values stay enum sources; str() of a path still fails."""
        folder = synthetic('unfinished', '''
            import os
            import messages
            from messages import msg, raw, cut, masked, stripped, clauses, message_of
            NAMING = ('title', 'clean')
            LABELS = {'title': '标题（推荐）', 'clean': '精简标题'}
            LOOSE = {'title': '标题（推荐）', 'other': '精简标题'}


            def engine_line(line):
                return messages.raw(line[6:].strip()[:200]), raw(f'{line}!')


            def claimed(url):
                kind = '媒体地址'
                return messages.raw(kind)


            def typed(store, emit, job, line):
                kind = '媒体地址'
                page = {'title': raw(kind)}
                emit('log', raw(kind) or 'emitted')
                store.change_job(job, title=raw(kind) or 'stored')
                return (page,
                        raw(text=kind),
                        cut(kind, 70),
                        messages.cut(kind, 71),
                        masked(kind, []),
                        messages.masked(value=kind, pairs=[]),
                        stripped(kind),
                        messages.stripped(kind or 'module'),
                        clauses([kind, 'bare clauses']),
                        messages.clauses(parts=['module clauses', kind]),
                        message_of(kind),
                        messages.message_of(kind or 'module message_of'),
                        masked(line, [('secret', '媒体地址')]),
                        cut(line, 72),
                        clauses([f'{line}: {job}']))


            def sizes(mb, t, name, error):
                return (msg('core.unfinished.problem.too_large', size=f'{mb / 1024:.1f}'),
                        msg('core.update.start_timeout', timeout=f'{error.timeout:g}'),
                        msg('core.unfinished.problem.too_large', size=f'{mb:.1f} MB'),
                        msg('core.unfinished.problem.too_large', size=f'{name:>10}'),
                        msg('core.unfinished.problem.too_large', size=f'{name!r:.1f}'))


            def use(style):
                return {'title': LABELS[style]}


            def paths(path):
                return (msg('core.unfinished.cannot_set_aside', path=str(path)),
                        msg('core.unfinished.cannot_set_aside', path=os.fspath(path)))
            ''')
        context = i18n_tool.GuardContext(enum_src=folder)       # LABELS is an enum source of this module
        for text in ('标题（推荐）', '精简标题'):
            context.allow('unfinished')['literals'][text] = {'category': 'h4', 'reason': 'self-test'}
        context.allow('unfinished')['literals']['媒体地址'] = {'category': 'stored', 'reason': 'self-test'}
        findings = i18n_tool.g1_module(context, 'unfinished', folder)
        found = {(item['rule'], item['line']) for item in findings}
        at = lambda needle: line_of(folder, 'unfinished', needle)
        self.assertNotIn(('G1f', at('line[6:]')), found, 'the argument of raw() is accepted (7.2 G1f)')
        self.assertIn(('G1b', at('messages.raw(kind)')), found, 'raw() of an enum value is still G1b')
        # fix round 1 (RV-I1-01, amendment A6): bare and messages.<name> forms of every typed call,
        # wherever the call stands; masked()'s pairs and cut()'s n are no slots; still no G1f
        details = {(item['line'], item['detail']) for item in findings if item['rule'] == 'G1b'}
        for needle, detail in (("{'title': raw(kind)}", 'raw(text)'), ("emit('log', raw(kind)", 'raw(text)'),
                               ('title=raw(kind)', 'raw(text)'), ('raw(text=kind)', 'raw(text)'),
                               ('cut(kind, 70)', 'cut(value)'), ('messages.cut(kind, 71)', 'cut(value)'),
                               ('masked(kind, [])', 'masked(value)'), ('masked(value=kind', 'masked(value)'),
                               ('stripped(kind),', 'stripped(value)'), ("stripped(kind or 'module')", 'stripped(value)'),
                               ("clauses([kind, 'bare clauses'])", 'clauses(parts)'),
                               ("'module clauses', kind]", 'clauses(parts)'),
                               ('message_of(kind),', 'message_of(error)'),
                               ("message_of(kind or 'module message_of')", 'message_of(error)')):
            self.assertIn((at(needle), f'allow-listed text in {detail}'), details, needle)
        for needle in ("masked(line, [('secret', '媒体地址')])", 'cut(line, 72)'):
            self.assertNotIn(('G1b', at(needle)), found, needle)
        self.assertNotIn(('G1f', at("clauses([f'{line}")), found, 'a clauses() part is G1b-only (A1.1)')
        self.assertNotIn(('G1f', at("f'{mb / 1024:.1f}'")), found)
        self.assertNotIn(('G1f', at("f'{error.timeout:g}'")), found)
        for needle in ("f'{mb:.1f} MB'", "f'{name:>10}'", "f'{name!r:.1f}'"):
            self.assertIn(('G1f', at(needle)), found, needle)
        self.assertNotIn(('G1b', at('LABELS = ')), found, 'an id-keyed table: its keys are ids')
        self.assertIn(('G1b', at('LOOSE = ')), found, 'a dict without its id set stays a text slot')
        self.assertIn(('G1b', at("{'title': LABELS[style]}")), found, 'the table values stay enum sources')
        self.assertIn(('G1f', at('path=str(path)')), found)
        self.assertNotIn(('G1f', at('path=os.fspath(path)')), found)
        self.assertEqual([item for item in findings if item['rule'] == 'G1c'], [])

    def test_the_wp6_typed_call_compositions(self):
        """(WP-6, RV-I1-02, amendment A11.1) An allow-listed enum value composed inside the text argument
        of a typed call is a G1b finding (f-string hole, + / % operand, .format / .join argument, str
        method receiver); engine text sliced and stripped stays accepted (A1.1)."""
        folder = synthetic('engine', '''
            import messages
            from messages import raw, cut, clauses


            def composed(url, line, parts):
                kind = '媒体地址'
                return (raw(f"{kind}: {url}"),                         # FLAG
                        messages.raw(kind + url),                      # FLAG
                        cut(f"{kind}{url}", 70),                       # FLAG
                        clauses(parts + [kind]),                       # FLAG
                        raw("{}: {}".format(kind, url)),               # FLAG
                        raw(' '.join([url, kind])),                    # FLAG
                        raw('%s %s' % (kind, url)),                    # FLAG
                        raw(kind.strip()[:20]),                        # FLAG
                        raw(line[6:].strip()[:200]),                   # OK
                        raw(f'{line}: {url}'),                         # OK
                        cut(line + url, 70),                           # OK
                        clauses(parts + [url]))                        # OK
            ''')
        context = i18n_tool.GuardContext()
        context.allow('engine')['literals']['媒体地址'] = {'category': 'stored', 'reason': 'self-test'}
        source = (folder / 'engine.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (8, 4))
        g1b = {item['line'] for item in i18n_tool.g1_module(context, 'engine', folder) if item['rule'] == 'G1b'}
        self.assertEqual(sorted(g1b & (marked['FLAG'] | marked['OK'])), sorted(marked['FLAG']))
        self.assertEqual([item for item in i18n_tool.g1_module(context, 'engine', folder)
                          if item['rule'] == 'G1f' and item['line'] in marked['FLAG'] | marked['OK']], [],
                         'typed-call arguments stay G1b-only (A1.1)')
        # the defect: the I-2 check (expr only) sees none of the FLAG lines
        parsed = context.module('engine', folder)
        taint = i18n_tool._Taint(context, parsed)
        old = {expression.lineno for expression, _slot, strict in i18n_tool.text_slots(parsed, context)
               if strict is None and taint.expr(expression, parsed.scope_of(expression))}
        self.assertEqual(old & marked['FLAG'], set())

    WP6_UI = '''
        import time
        import i18n
        import messages


        def clock(stamp):
            return time.strftime('%m-%d %H:%M', time.localtime(stamp)) if stamp else i18n.t('library.never_checked')


        def weekday(stamp):
            return time.strftime('%A', time.localtime(stamp))


        def echo(text):
            return text


        def joined(*parts):
            return ' · '.join(parts)


        def cells(row):
            return messages.show(row['title']), clock(row['updated']), row['status']    # FLAG


        def good_cells(row):
            return messages.show(row['title']), clock(row['updated'])


        class LibraryUI:
            def set_status(self, *parts):
                self.status.configure(text=' '.join(parts))

            def mapping(self, event, extra):
                self.a.configure(text=echo(**event))                                    # FLAG
                self.a.configure(text=echo(i18n.t('x.y'), **extra))                      # OK
                self.a.configure(text=echo(text=i18n.t('x.y')))                          # OK

            def varargs(self, item, rows):
                self.set_status(item['title'])                                           # FLAG
                self.set_status(i18n.t('x.a'), i18n.t('x.b'))                            # OK
                self.set_status(*item['parts'])                                          # FLAG
                self.set_status(*[i18n.t('x.a')])                                        # OK
                self.b.configure(text=joined(i18n.t('x.a'), item['title']))              # FLAG
                self.b.configure(text=joined(i18n.t('x.a'), i18n.t('x.b')))              # OK

            def tables(self, rows):
                self.history.fill((row['id'], cells(row)) for row in rows)
                for tree, items, render in [(self.one, rows, cells), (self.two, rows, good_cells)]:
                    tree.fill((row['id'], render(row)) for row in items)
                self.good.fill((row['id'], good_cells(row)) for row in rows)            # OK
                self.c.configure(text=clock(rows[0]['updated']))                         # OK
                self.c.configure(text=weekday(rows[0]['updated']))                       # FLAG
        '''

    def test_the_wp6_sink_fixes(self):
        """(WP-6, RV-I2-02 / RV-I2-03 / RV-WP5-1, amendments A11.2-A11.4) a **mapping that may fill a
        returned parameter, a *parts vararg sink parameter, the cells of a table row made by a UI
        function (also called through a for-target, as library_ui.refresh_library does) and a
        numeric time.strftime."""
        folder = synthetic('library_ui', self.WP6_UI)
        source = (folder / 'library_ui.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (6, 7))
        context = i18n_tool.GuardContext()
        sinks = i18n_tool._Sinks(context, ['library_ui'], folder=folder)
        findings = sinks.run(allow_for=lambda name: {'sinks': {}})
        self.assertEqual(sorted({item['line'] for item in findings}), sorted(marked['FLAG']))
        cell = [item for item in findings if item['line'] == min(marked['FLAG'])]
        self.assertEqual([(item['text'], item['function'], item['detail']) for item in cell],
                         [("row['status']", 'cells', 'Treeview.fill values cell')], 'one finding per cell')
        self.assertIn('parts', sinks.sink_params['set_status'])
        # an entry scoped to the row function excuses the cell; the stale check sees it used
        entry = {"row['status']": {'line_hint': 1, 'function': 'cells', 'reason': 'self-test'}}
        rest = i18n_tool._Sinks(context, ['library_ui'], folder=folder).run(allow_for=lambda name: {'sinks': entry})
        self.assertEqual(sorted({item['line'] for item in rest}), sorted(marked['FLAG'] - {min(marked['FLAG'])}))

    def test_g1d_g1e_and_the_h4_check(self):
        folder = synthetic('studio', '''
            import i18n_legacy
            import messages
            TITLE = '找不到任务记录。'


            def leak(record):
                raise ValueError('an English text the owner would see')


            def codec(record):
                return messages.decode(record, 'job')


            def localize(text):
                return text
            ''')
        context = i18n_tool.GuardContext()
        context.allow('studio')['literals']['找不到任务记录。'] = {'category': 'h4', 'reason': 'self-test'}
        findings = i18n_tool.g1_module(context, 'studio', folder)
        details = {(item['rule'], item['line']) for item in findings}
        self.assertIn(('G1d', line_of(folder, 'studio', 'import i18n_legacy')), details)
        self.assertIn(('G1d', line_of(folder, 'studio', 'messages.decode')), details)
        self.assertIn(('G1d', line_of(folder, 'studio', 'def localize')), details)
        self.assertIn(('G1e', line_of(folder, 'studio', 'raise ValueError')), details)
        self.assertIn(('G1a', line_of(folder, 'studio', 'TITLE =')), details, 'an h4 constant pinned to a core key')

    def test_the_boundary_modules_are_reached_by_their_names(self):
        """(final review 1, Q3, amendment A13.2) G1b / G1c / G1d match messages and i18n by name, so an
        alias of either module, an aliased messages name, the module object used as a value and an
        import by string are G1d findings; the plain spellings are not."""
        folder = synthetic('naming', '''
            import importlib
            import i18n as _lang                                    # FLAG
            import messages as M                                    # FLAG
            import messages
            import i18n
            from messages import encode as enc                      # FLAG
            from messages import msg, cut


            def probe(record, url):
                codec = messages                                    # FLAG
                lang = getattr(i18n, 'language')                    # FLAG
                loaded = importlib.import_module('messages')        # FLAG
                legacy = __import__('i18n')                         # FLAG
                aliased = (_lang.t('common.unfinished'), M.encode(record, 'job'), M.msg('core.no.such.key'),
                           M.cut(f"{url}", 70), codec, lang, loaded, legacy, enc)
                return aliased, messages.msg('core.reason.network'), msg('core.reason.network'), cut(url, 70)  # OK
            ''')
        context = i18n_tool.GuardContext()
        source = (folder / 'naming.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (7, 1))
        findings = i18n_tool.g1_module(context, 'naming', folder)
        g1d = {item['line'] for item in findings if item['rule'] == 'G1d'}
        self.assertEqual(sorted(g1d & (marked['FLAG'] | marked['OK'])), sorted(marked['FLAG']))
        # the defect: without the alias rule G1 sees none of the aliased uses
        parsed = context.module('naming', folder)
        aliases = i18n_tool._g1d_aliases(parsed)
        self.assertEqual(sorted({item['line'] for item in aliases}), sorted(marked['FLAG']))
        rest = [item for item in findings if item not in aliases]
        used = line_of(folder, 'naming', 'aliased = (')
        self.assertEqual([item for item in rest if item['line'] in (used, used + 1)], [],
                         'the aliased spellings themselves pass the name-based checks')
        # the release sources and the tree have none of these forms
        for version in ('1.5.1', '1.6.0', '1.7.0'):
            old = _sandbox.release_src(version)
            for name in i18n_tool.src_modules(old):
                if name not in i18n_tool.G1_EXCLUDED:
                    self.assertEqual(i18n_tool._g1d_aliases(context.module(name, old)), [], f'{version} {name}')

    def test_star_imports_and_messages_functions_as_values(self):
        """(final review 2, Q3, amendment A14.4) a star import of messages / i18n and a messages function
        used outside call position (bound to a new name, passed on) are G1d findings: under another
        name the codec, msg() and the typed calls pass G1c / G1d / G1b unchecked.  Calls, the classes
        (`isinstance(value, messages.Raw)`) and the plain spellings stay clean."""
        folder = synthetic('naming', '''
            import messages
            from messages import *                                  # FLAG
            from messages import encode, msg, cut, raw, Raw


            def probe(record, url):
                star = decode_columns(record, 'job', {})
                fn = messages.encode                                # FLAG
                make = messages.msg                                 # FLAG
                enc = encode                                        # FLAG
                build = msg                                         # FLAG
                shorten = cut                                       # FLAG
                many = list(map(raw, LABELS.values()))              # FLAG
                handlers = {'cut': messages.cut}                    # FLAG
                return star, fn, make, enc, build, shorten, many, handlers


            def fine(record, url, value):
                kept = isinstance(value, (messages.Raw, Raw)), messages.msg('core.reason.network')  # OK
                return kept, msg('core.reason.network'), cut(url, 70), raw(url)                      # OK
            ''')
        context = i18n_tool.GuardContext()
        source = (folder / 'naming.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (8, 2))
        parsed = context.module('naming', folder)
        aliases = i18n_tool._g1d_aliases(parsed)
        self.assertEqual(sorted({item['line'] for item in aliases}), sorted(marked['FLAG']))
        findings = i18n_tool.g1_module(context, 'naming', folder)
        g1d = {item['line'] for item in findings if item['rule'] == 'G1d'}
        self.assertFalse(g1d & marked['OK'])
        # the bare codec call after the star import is reached only through that import, the finding
        self.assertIn(line_of(folder, 'naming', 'from messages import *'), g1d)
        # the tree, every module G1 scans, has none of these forms
        for name in i18n_tool.src_modules(i18n_tool.SRC):
            if name not in i18n_tool.G1_EXCLUDED:
                self.assertEqual(i18n_tool._g1d_aliases(context.module(name)), [], name)

    def test_enum_values_items_and_parameter_defaults(self):
        """(final review 2, Q5 and F1, amendment A14.3 / A14.1) G1b follows an enum source through
        `.values()`, `.items()` (the value of the pair, not its key) and `.copy()`, and takes a
        parameter's default as a binding: naming.LABELS holds ids in GROUPS, so G2 cannot catch these
        texts at run time and G1b is their only guard."""
        context = i18n_tool.GuardContext()
        source = (i18n_tool.SRC / 'naming.py').read_text(encoding='utf-8')
        extra = textwrap.dedent('''

            from messages import raw, msg


            def probe(record):
                a = {'title': LABELS['title']}                              # FLAG
                c = [{'title': t} for t in LABELS.values()]                 # FLAG
                d = [{'title': v} for k, v in LABELS.items()]               # FLAG
                e = [{'title': raw(t)} for t in LABELS.values()]            # FLAG
                for label in LABELS.copy().values():
                    g = {'title': label}                                    # FLAG
                h = msg('core.engine.error', error=list(LABELS.values())[0])   # FLAG
                keys = [{'title': k} for k, v in LABELS.items()]            # OK
                return a, c, d, e, g, h, keys


            def page(url, kind=LABELS['title']):
                return {'title': kind}                                      # FLAG


            def plain(url, kind=None):
                return {'title': kind}                                      # OK
            ''')
        folder = _sandbox.out('guard-synthetic-final2', 'naming')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'naming.py').write_text(source + extra, encoding='utf-8')
        lines_ = (folder / 'naming.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(lines_, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (7, 2))
        findings = i18n_tool.g1_module(context, 'naming', folder)
        g1b = {item['line'] for item in findings if item['rule'] == 'G1b'}
        self.assertEqual(sorted(g1b & (marked['FLAG'] | marked['OK'])), sorted(marked['FLAG']))

    def test_g1e_counts_the_1_7_0_raises(self):
        context = i18n_tool.GuardContext()
        flagged = []
        for name in i18n_tool.src_modules(OLD):
            if name in i18n_tool.G1_EXCLUDED:
                continue
            flagged += [(name, item['line']) for item in i18n_tool.g1_module(context, name, OLD) if item['rule'] == 'G1e']
        self.assertEqual(sorted(flagged), sorted([('engine', 178), ('engine', 184), ('engine', 188), ('engine', 190),
                                                  ('engine', 425), ('formats', 75), ('formats', 82), ('formats', 89)]))


class G5SelfTests(unittest.TestCase):
    SOURCE = '''
        import i18n
        import messages
        import ui_theme
        from tkinter import messagebox


        class Page:
            def bad_status(self, item):
                self.status.configure(text=item['title'])

            def good_status(self, item):
                self.status.configure(text=messages.show(item['title']))

            def bad_dialog(self):
                try:
                    self.work()
                except OSError as error:
                    messagebox.showerror(i18n.t('dialog.open_failed.title'), str(error))

            def good_dialog(self):
                try:
                    self.work()
                except OSError as error:
                    messagebox.showerror(i18n.t('dialog.open_failed.title'), messages.error_text(error))

            def bad_fill(self, rows):
                self.tree.fill((row['id'], (row['title'], row['url'])) for row in rows)

            def good_fill(self, rows):
                self.tree.fill((row['id'], (messages.show(row['title']), row['url'])) for row in rows)

            def bad_t(self):
                try:
                    self.work()
                except OSError as error:
                    self.note.configure(text=i18n.t('library.unreadable', error=error))

            def good_t(self):
                try:
                    self.work()
                except OSError as error:
                    self.note.configure(text=i18n.t('library.unreadable', error=messages.error_text(error)))

            def set_hint(self, text):
                ui_theme.fit(self.hint, text)

            def bad_hint(self, event):
                value = event.get('reason')
                self.set_hint(value)

            def good_hint(self, event):
                value = messages.show(event.get('reason'))
                self.set_hint(value)

            def bad_compose(self, event):
                x = event['title']
                y = i18n.t('download.heading')
                self.status.configure(text=f'{x} · {y}')

            def good_compose(self, event):
                x = messages.show(event['title'])
                y = i18n.t('download.heading')
                self.status.configure(text=f'{x} · {y}')
        '''

    def test_synthetic_sinks(self):
        folder = synthetic('library_ui', self.SOURCE)
        context = i18n_tool.GuardContext()
        sinks = i18n_tool._Sinks(context, ['library_ui'], folder=folder)
        findings = sinks.run(allow_for=lambda name: {'sinks': {}})
        flagged = {item['line'] for item in findings}
        source = (folder / 'library_ui.py').read_text(encoding='utf-8').splitlines()
        for needle in ("configure(text=item['title'])", 'str(error))', "(row['title'], row['url'])",
                       'error=error)', 'self.set_hint(value)', "text=f'{x} · {y}'"):
            numbers = [number for number, text in enumerate(source, 1) if needle in text]
            self.assertTrue(set(numbers) & flagged, f'{needle} is not flagged')
        good = [number for number, text in enumerate(source, 1)
                if any(word in text for word in ('messages.show', 'messages.error_text'))]
        self.assertEqual(sorted(flagged & set(good)), [], 'a show() / error_text() form is flagged')
        self.assertEqual(len(findings), 6, findings)
        self.assertIn('text', sinks.sink_params['set_hint'])

    def test_the_1_7_0_sites_with_localize_as_display_text(self):
        context = i18n_tool.GuardContext()
        findings = i18n_tool._Sinks(context, i18n_tool.UI_MODULES, folder=OLD,
                                    extra_display={('i18n', 'localize')}).run(allow_for=lambda name: {'sinks': {}})
        flagged = set(lines(findings))
        for site in (('library_ui.py', 639), ('library_ui.py', 787), ('ui_settings.py', 414), ('studio.py', 509),
                     ('ui_download.py', 1311), ('ui_log.py', 195), ('collection_ui.py', 622), ('collection_ui.py', 643),
                     ('library_ui.py', 1306)):
            self.assertIn(site, flagged)
        texts = {(item['file'], item['line']): item['text'] for item in findings}
        self.assertEqual(texts[('studio.py', 509)], 'str(error)')
        self.assertEqual(texts[('library_ui.py', 639)], 'error')
        print(f'G5 on release-sources/1.7.0 (localize as display): {len(findings)} sink positions to convert or allow')

    # I-2 (MESSAGES.md 15, amendment A7): the scanner defects WP-4 / WP-5 reported.  Lines marked
    # FLAG must be findings, lines marked OK must not; nothing else in the module may be flagged.
    I2_STUDIO = '''
        import i18n
        import messages
        import ui_theme

        HANDLED = ('title',)


        class StudioApp:
            def label(self, parent, text='', **kwargs):
                return ui_theme.label(parent, text, **kwargs)

            def wire(self):
                for kind in HANDLED:
                    self.handlers[kind] = getattr(self, f'on_{kind}')
        '''
    I2_COLLECTOR = '''
        MAX_LINKS = 2000
        TITLE = 'a stored text'
        '''
    I2_DOWNLOAD = '''
        import i18n
        import messages
        import ui_theme
        import urls as links
        from collector import MAX_LINKS, TITLE


        def time_text(value):
            if value is None:
                return '-'
            minutes, seconds = divmod(int(value), 60)
            hours, minutes = divmod(minutes, 60)
            return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


        def echo(text):
            return text


        class DownloadUI:
            def sd1(self, value, item):
                self.label(self.box, i18n.label('format', value['format']))          # OK
                ui_theme.label(self.box, i18n.label('mode', value['mode']))           # OK
                self.label(self.box, item['title'])                                   # FLAG

            def sd2(self, value):
                text, tone = i18n.t('shell.status.ready'), ui_theme.SIDE_OK
                self.status.configure(text=text, text_color=tone)                     # OK
                for _row, _icon, title in [(1, ui_theme.ICON_DOWNLOAD, i18n.t('nav.download'))]:
                    self.nav.configure(text=title)                                    # OK
                first, second = (i18n.t(key) for key in ('nav.download', 'nav.logs'))
                self.first.configure(text=first)                                      # OK
                caption, wrapped = self._caption()
                self.caption.configure(text=caption)                                  # OK
                for index, name in enumerate([i18n.t('nav.download')]):
                    self.tab.configure(text=f'{index} {name}')                        # OK
                tone2, raw = ui_theme.SIDE_OK, value['title']
                self.raw.configure(text=raw)                                          # FLAG
                for shown, _key in [(value['title'], 'a')]:
                    self.shown.configure(text=shown)                                  # FLAG

            def _caption(self):
                return i18n.t('shell.brand_caption'), False

            def sd3(self, rows):
                self.a.configure(text=i18n.t('x.count', count=max(len(rows), 1)))     # OK
                self.b.configure(text=i18n.t('x.count', count=sum(1 for row in rows if row)))  # OK
                self.c.configure(text=i18n.t('x.limit', limit=MAX_LINKS))             # OK
                self.d.configure(text=max(row['title'] for row in rows))              # FLAG
                self.e.configure(text=TITLE)                                          # FLAG

            def sd4(self, url):
                self.link.configure(text=links.display(url))                          # OK

            def sd5(self, seconds):
                self.clock.configure(text=i18n.t('x.time', time=time_text(seconds)))  # OK

            def scoped_a(self, event):
                value = event['a']
                self.g.configure(text=value)                                          # SCOPED

            def scoped_b(self, event):
                value = event['b']
                self.h.configure(text=value)                                          # FLAG

            def on_title(self, value):
                self.current.configure(text=value)                                    # FLAG

            def set_hint(self, text):
                ui_theme.fit(self.hint, text)                                         # OK

            def hint_ok(self):
                self.set_hint(i18n.t('download.hint'))                                # OK

            def pick(self, text):
                self.picked.configure(text=text)                                      # FLAG

            def build(self):
                ui_theme.button(self.box, i18n.t('x.y'), command=self.pick)           # OK

            def except_after(self):
                try:
                    self.work()
                except OSError as error:
                    self.note.configure(text=messages.error_text(error))              # OK
                error = i18n.t('x.done')
                self.note.configure(text=error)                                       # OK

            def except_inside(self):
                try:
                    self.work()
                except OSError as error:
                    self.note.configure(text=error)                                   # FLAG

            def returns(self, item):
                self.k.configure(text=echo(item['title']))                            # FLAG
                self.k.configure(text=echo(i18n.t('x.y')))                            # OK

            def starred(self, event):
                args = (event['title'],)
                self.set_hint(*args)                                                  # FLAG
                self.set_hint(**event['fields'])                                      # FLAG
                shown = (i18n.t('x.y'),)
                self.set_hint(*shown)                                                 # OK
        '''

    def test_the_i2_scanner_fixes(self):
        folder = _sandbox.out('guard-synthetic-i2', 'src')
        folder.mkdir(parents=True, exist_ok=True)
        for name, source in (('studio', self.I2_STUDIO), ('collector', self.I2_COLLECTOR),
                             ('ui_download', self.I2_DOWNLOAD)):
            (folder / f'{name}.py').write_text(textwrap.dedent(source).lstrip('\n'), encoding='utf-8')
        source = (folder / 'ui_download.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK', 'SCOPED')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK']), len(marked['SCOPED'])), (12, 19, 1))
        context = i18n_tool.GuardContext(folder)

        def scan(allow):
            sinks = i18n_tool._Sinks(context, ['studio', 'ui_download'], folder=folder)
            found = sinks.run(allow_for=lambda name: {'sinks': allow if name == 'ui_download' else {}})
            self.assertEqual([item for item in found if item['file'] != 'ui_download.py'], [])
            return sinks, {item['line'] for item in found}

        # without an allow entry: exactly the FLAG lines and the SCOPED line
        sinks, flagged = scan({})
        self.assertEqual(sorted(flagged), sorted(marked['FLAG'] | marked['SCOPED']))
        self.assertIn('text', sinks.sink_params['label'])           # StudioApp.label is still a sink method
        self.assertIn('text', sinks.sink_params['set_hint'])
        self.assertNotIn('pick', sinks.sink_params)                 # a callback's parameter is not trusted
        self.assertNotIn('on_title', sinks.sink_params)             # reached through getattr(self, f'on_{kind}')
        # SD-6: an entry excuses its expression only in the functions it names
        entry = {'value': {'line_hint': 1, 'function': 'DownloadUI.scoped_a', 'reason': 'self-test'}}
        _sinks, flagged = scan(entry)
        self.assertEqual(sorted(flagged), sorted(marked['FLAG']))
        _sinks, flagged = scan({'value': {'line_hint': 1, 'reason': 'self-test: names no function'}})
        self.assertEqual(sorted(flagged), sorted(marked['FLAG'] | marked['SCOPED']))
        # allow_stale: a scoped entry that excuses a position is used; one naming a function where the
        # expression is display text (or names no function) is stale
        allow_dir = _sandbox.out('guard-synthetic-i2', 'allow')
        allow_dir.mkdir(parents=True, exist_ok=True)
        sinks_section = {'value': {'line_hint': 1, 'function': ['DownloadUI.scoped_a', 'DownloadUI.sd1'],
                                   'reason': 'self-test'},
                         'text': {'line_hint': 1, 'reason': 'self-test: names no function'}}
        (allow_dir / 'ui_download.json').write_text(json.dumps({'module': 'ui_download', 'sinks': sinks_section}),
                                                    encoding='utf-8')
        stale = i18n_tool.allow_stale(i18n_tool.GuardContext(folder, allow_dir), 'ui_download', folder)
        self.assertEqual(sorted(stale), [('sinks', 'text (names no function)'), ('sinks', 'value @ DownloadUI.sd1')])

    MODULE_TEXTS = '''
        import i18n
        import messages
        import ui_theme
        from tkinter import messagebox

        NO_PENDING = '这个订阅目前没有待处理的新增内容。'
        PICK_ARCHIVE = 'Pick a row first.'
        GAP = 12


        class LibraryUI:
            def append_log(self, line):
                line = messages.show(line)
                self.log_buffer.append(line)

            def say_hint(self, text):
                self.note.configure(text=text)

            def picked(self):
                return NO_PENDING

            def forms(self, line):
                messagebox.showinfo(i18n.t('library.no_pending'), NO_PENDING, parent=self)               # FLAG
                note = NO_PENDING
                messagebox.showwarning(i18n.t('library.no_pending'), note, parent=self)                   # FLAG
                self.append_log(NO_PENDING)                                                                # FLAG
                self.append_log('这个订阅目前没有待处理的新增内容。')                                        # FLAG
                self.append_log(line)                                                                      # OK
                messagebox.showinfo(i18n.t('library.no_pending'), '这个订阅目前没有待处理的新增内容。', parent=self)  # FLAG
                self.say_hint(self.picked())                                                               # FLAG
                self.say_hint(NO_PENDING)                                                                  # FLAG
                self.tree.insert('', 'end', values=(NO_PENDING, 'x'))                                      # FLAG
                self.status.configure(text=PICK_ARCHIVE)                                                   # FLAG
                self.status.configure(text=f'{GAP} 秒')                                                    # FLAG
                ui_theme.fit(self.hint, NO_PENDING)                                                        # FLAG
                self.status.configure(text=GAP)                                                            # OK
                self.status.configure(text=f'{GAP} s')                                                     # OK
                self.say_hint(i18n.t('library.pick_archive'))                                              # OK
                self.append_log(messages.show(NO_PENDING))                                                 # FLAG
                self.tree.insert('', 'end', values=(i18n.t('library.no_pending'), 'x'))                    # OK
                messagebox.showinfo(i18n.t('library.no_pending'), i18n.t('library.no_pending'), parent=self)  # OK
        '''

    def test_module_texts_and_chinese_literals_at_sinks(self):
        """(final review 1, F1, amendment A13.1) a module-level name is display text only as an int /
        float constant (7.9, A7.3): a module-level str (an h4 UI constant) at a dialog, a sink method,
        a table value or append_log (a value sink: a bare value passes, text fixed in the source does
        not) is a finding, and so is a Chinese literal at a sink, which no G1b slot covers there."""
        folder = _sandbox.out('guard-synthetic-final1', 'src')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'library_ui.py').write_text(textwrap.dedent(self.MODULE_TEXTS).lstrip('\n'), encoding='utf-8')
        source = (folder / 'library_ui.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (12, 6))   # A14.2: show(NO_PENDING) is a FLAG
        context = i18n_tool.GuardContext(folder)
        sinks = i18n_tool._Sinks(context, ['library_ui'], folder=folder)
        found = sinks.run(allow_for=lambda name: {'sinks': {}})
        self.assertEqual(sorted({item['line'] for item in found}), sorted(marked['FLAG']))
        self.assertNotIn('append_log', sinks.sink_params, 'append_log is the value sink of 7.9, as in ui_log')
        # a scoped entry still excuses a module constant where its reason holds
        entry = {'PICK_ARCHIVE': {'line_hint': 1, 'function': 'LibraryUI.forms', 'reason': 'self-test'}}
        rest = i18n_tool._Sinks(context, ['library_ui'], folder=folder).run(allow_for=lambda name: {'sinks': entry})
        self.assertEqual(sorted({item['line'] for item in rest}),
                         sorted(marked['FLAG'] - {line_of(folder, 'library_ui', 'text=PICK_ARCHIVE')}))

    FIXED_TEXTS_UI_LOG = '''
        import messages

        ZH_START = '采集完成'


        class LogUI:
            def append_log(self, line):
                self.log_box.insert('end', messages.show(line))
        '''

    FIXED_TEXTS = '''
        import i18n
        import messages
        import ui_log
        from messages import show

        NO_PENDING = '这个订阅目前没有待处理的新增内容。'
        OTHER = '请先在下方列表中选择一条资料。'
        NAMES = ('全部', '视频')
        TABLE = {'a': '视频页面'}
        COLOR = '#c0392b'


        class LibraryUI:
            HEADER = '资料库'

            def picked(self):
                return NO_PENDING

            def default_hint(self, text=NO_PENDING):
                self.note.configure(text=text)                                         # FLAG

            def literal_hint(self, text='这个订阅目前没有待处理的新增内容。'):
                self.note.configure(text=text)                                         # FLAG

            def log_default(self, text=OTHER):
                self.append_log(text)                                                  # FLAG

            def plain_hint(self, text=None):
                self.note.configure(text=text)                                         # OK

            def forms(self, ok, text, value, items):
                note = NO_PENDING
                self.append_log(note)                                                  # FLAG
                self.append_log(NO_PENDING if ok else OTHER)                           # FLAG
                self.append_log(text or NO_PENDING)                                    # FLAG
                self.append_log(NAMES[0])                                              # FLAG
                self.append_log(TABLE.get('a'))                                        # FLAG
                self.append_log(self.HEADER)                                           # FLAG
                self.append_log(self.picked())                                         # FLAG
                self.append_log(ui_log.ZH_START)                                       # FLAG
                for name in NAMES:
                    self.append_log(name)                                              # FLAG
                for index, name in enumerate(NAMES):
                    self.append_log(name)                                              # FLAG
                self.note.configure(text=messages.show(NO_PENDING))                    # FLAG
                self.note.configure(text=show(OTHER))                                  # FLAG
                self.note.configure(text=messages.show('这个订阅目前没有待处理的新增内容。'))  # FLAG
                self.note.configure(text=messages.error_text(note))                    # FLAG
                self.default_hint()
                self.literal_hint()
                self.log_default()
                self.plain_hint()
                self.append_log(value['line'])                                         # OK
                self.append_log(value.get('line') or '')                               # OK
                self.append_log(i18n.t('library.no_pending'))                          # OK
                title, color = i18n.t('library.no_pending'), COLOR
                self.append_log(title)                                                 # OK
                for index, item in enumerate(items):
                    self.append_log(item)                                              # OK
                self.note.configure(text=messages.show(value))                         # OK
                self.note.configure(text=show(value['line']))                          # OK
                self.note.configure(text=messages.error_text(value))                   # OK
        '''

    def test_fixed_text_through_names_defaults_and_show(self):
        """(final review 2, F1 / F2, amendment A14.1-2) text fixed in the source (a Chinese literal, a
        module-level or class-level text, also of another module) that reaches append_log through a
        local alias, a conditional, `or`, a subscript, `.get`, `self.<name>`, a method's return or a
        loop over a table, or reaches a sink through a parameter default, or is handed to show() /
        error_text() (which give it back unchanged in English) is a finding; bare values, t() and
        show() of values that are no text fixed in the source stay clean."""
        folder = _sandbox.out('guard-synthetic-final2', 'src')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'ui_log.py').write_text(textwrap.dedent(self.FIXED_TEXTS_UI_LOG).lstrip('\n'), encoding='utf-8')
        (folder / 'library_ui.py').write_text(textwrap.dedent(self.FIXED_TEXTS).lstrip('\n'), encoding='utf-8')
        source = (folder / 'library_ui.py').read_text(encoding='utf-8').splitlines()
        marked = {mark: {number for number, text in enumerate(source, 1) if text.rstrip().endswith(f'# {mark}')}
                  for mark in ('FLAG', 'OK')}
        self.assertEqual((len(marked['FLAG']), len(marked['OK'])), (17, 9))
        context = i18n_tool.GuardContext(folder)
        found = i18n_tool._Sinks(context, ['ui_log', 'library_ui'], folder=folder).run(
            allow_for=lambda name: {'sinks': {}})
        self.assertEqual(sorted({item['line'] for item in found if item['file'] == 'library_ui.py'}),
                         sorted(marked['FLAG']))
        self.assertEqual([item for item in found if item['file'] == 'ui_log.py'], [])


class ContractTests(unittest.TestCase):
    """(converge round, MESSAGES.md 15 amendment A15; Phase D fix 1, A16) The guard contract,
    combinatorially: every SOURCE through every FLOW into every SINK kind and spelling of the contract
    (verification/guard_contract.py), the parameters at wrapper depth 1, 2 and 3, positionally and by
    keyword, with and without show(); every bad combination is flagged inside its own lines and every
    good one is not, by the scanner and by each of its two strict judgements alone; the coverage is
    checked against i18n_tool.CONTRACT and the published spec; removing a behaviour of the scanner
    (guard_contract.MUTANTS) makes a case of each of its ids come out wrong; the reviewers' probe
    forms of the final review are named cases."""

    @classmethod
    def setUpClass(cls):
        cls.results = guard_contract.evaluate(i18n_tool, _sandbox.out('guard-contract', 'cases'), judgements=True)

    def test_the_cases_cover_the_contract(self):
        """(A16.7; B2 / Q3) Coverage is checked against i18n_tool.CONTRACT, the list kept next to the
        scanner, not against the generator's own tables: every id has a bad and / or a good case (as
        its need says) for every guard it names, and the generator tags its cases with no other id."""
        self.assertEqual(list(guard_contract.G5_SINKS), [kind for kind, _mode in i18n_tool.SINKS],
                         'one G5 case template per kind of i18n_tool.SINKS (A15.4)')
        seen = {}
        for guard in ('G5', 'G1b', 'G1f'):
            for case, _flagged in self.results[guard]:
                for form in case.forms:
                    seen.setdefault((form, guard), set()).add(guard_contract.expected(guard, case))
        missing = []
        for form, (guards, need) in i18n_tool.CONTRACT.items():
            for guard in guards:
                outcomes = seen.get((form, guard), set())
                if need in ('bad', 'both') and True not in outcomes:
                    missing.append(f'{form} ({guard}): no case that must be flagged')
                if need in ('good', 'both') and False not in outcomes:
                    missing.append(f'{form} ({guard}): no case that must not be flagged')
        self.assertEqual(missing, [])
        used = {form for form, _guard in seen}
        self.assertEqual(sorted(used - set(i18n_tool.CONTRACT)), [], 'an id i18n_tool.CONTRACT does not list')
        for guard in ('G5', 'G1b'):
            variants = {case.variant for case, _flagged in self.results[guard]}
            for depth in (1, 2, 3):
                self.assertTrue(any(variant.startswith(f'depth {depth}') for variant in variants), f'{guard} depth {depth}')
            self.assertTrue(any('keyword' in variant for variant in variants), guard)
        g5 = {(case.sink, case.variant) for case, _flagged in self.results['G5']}
        for sink in guard_contract.G5_SINKS:
            if sink != 'library filetext t()':
                for variant in ('depth 2', 'depth 3 keyword', 'depth 2 show', 'show'):
                    self.assertIn((sink, variant), g5, f'G5 {sink} {variant}')

    def test_the_contract_list_is_the_published_spec(self):
        """(A16.7) The published spec lists the ids of i18n_tool.CONTRACT, one per line, in the block
        after its marker line: the contract is the same text in both places."""
        spec = (i18n_tool.ROOT / 'docs' / '1.8.0' / 'spec' / 'MESSAGES.md').read_text(encoding='utf-8')
        marker = '<!-- guard contract ids (A16.7) -->'
        self.assertIn(marker, spec)
        block = spec.split(marker, 1)[1].split('```', 2)[1]
        listed = [line.strip() for line in block.splitlines()[1:] if line.strip()]
        self.assertEqual(sorted(listed), sorted(i18n_tool.CONTRACT))
        self.assertEqual(len(listed), len(set(listed)))

    def test_every_combination_of_source_flow_and_sink(self):
        counts = guard_contract.counts(self.results)
        print('contract: ' + '; '.join(f'{guard} {row["cases"]} cases, bad flagged {row["bad_flagged"]}/{row["bad"]}, '
                                       f'good clean {row["good_clean"]}/{row["good"]}' for guard, row in counts.items()))
        wrong = [f'{guard} {case.key}: {case.source} / {case.flow} / {case.sink} {case.variant} '
                 f"({'must be flagged, is not' if guard_contract.expected(guard, case) else 'must not be flagged, is'})"
                 for guard, case in guard_contract.wrong({guard: self.results[guard] for guard in ('G5', 'G1b', 'G1f')})]
        self.assertEqual(wrong[:20], [], f'{len(wrong)} cases differ from the contract')
        self.assertGreater(counts['G5']['cases'], 40000)
        self.assertGreater(counts['G1b']['cases'], 13000)
        self.assertGreater(counts['G1f']['cases'], 1000)

    def test_the_grammar_and_the_flows_agree(self):
        """(A16.5; Q6) Each strict sink is judged by the 7.9 grammar and by the flows' value rule, and
        passes only when both accept it.  Over the generated cases each judgement alone gives the
        contract's answer (the flows alone: an event value at a strict sink needs show(), which only
        the grammar knows, so there it gives the value rule's answer): the two implementations agree."""
        wrong = [f'{guard} {case.key}: {case.source} / {case.flow} / {case.sink} {case.variant}'
                 for guard, case in guard_contract.wrong({guard: self.results[guard]
                                                         for guard in ('G5 grammar', 'G5 flows')})]
        self.assertEqual(wrong[:20], [], f'{len(wrong)} cases where a judgement alone differs from the contract')
        self.assertGreater(len(self.results['G5 grammar']), 40000)

    def test_the_reviewers_probe_forms(self):
        found = guard_contract.probes(i18n_tool, _sandbox.out('guard-contract', 'probes'))
        self.assertGreaterEqual(len(found), 130)
        self.assertGreaterEqual(len([name for name in found if name.startswith(('G5 D', 'G1 D'))]), 70)
        self.assertEqual({name: hit for name, hit in found.items() if hit != guard_contract.probe_expected(name)}, {})

    def test_removing_each_behaviour_fails_a_case(self):
        """(A16.7; B2 / Q3) The self-test of the self-test: each mutant removes one behaviour of the
        scanner (a code path, or one entry of a scanner table) in a copy of i18n_tool; then a case of
        each id the mutant names comes out wrong.  Every id of the contract has a mutant except those
        guard_contract.UNMUTATED names with the reason."""
        failed = {}
        for index, mutant in enumerate(guard_contract.MUTANTS):
            missed = guard_contract.mutant_missed(i18n_tool, _sandbox.out('guard-contract', f'mutant-{index}'), mutant)
            silent = [form for form, items in missed.items() if not items]
            if silent:
                failed[mutant.name] = silent
        self.assertEqual(failed, {}, 'mutants whose removal no case of these ids sees')
        ids = {form for mutant in guard_contract.MUTANTS for form in mutant.forms}
        self.assertEqual(sorted(ids - set(i18n_tool.CONTRACT)), [])
        self.assertEqual(sorted(set(i18n_tool.CONTRACT) - ids), sorted(guard_contract.UNMUTATED))
        print(f'mutants: {len(guard_contract.MUTANTS)} behaviours removed one at a time, {len(ids)} of '
              f'{len(i18n_tool.CONTRACT)} contract ids covered')

    def test_the_generator_sees_a_missing_flow(self):
        """Self-test of the self-test: a scanner without the attribute flow (F3) fails the cases that
        need it, and only those."""
        saved = i18n_tool._Flow._self_leaves
        i18n_tool._Flow._self_leaves = lambda self, *args, **kwargs: []
        try:
            select = lambda case: case.flow in ('self attribute', 'direct') and case.sink in ('append_log', 'emit()')
            results = guard_contract.evaluate(i18n_tool, _sandbox.out('guard-contract', 'mutant'), select=select)
        finally:
            i18n_tool._Flow._self_leaves = saved
        missed = {(case.flow, case.source) for _guard, case in guard_contract.wrong(results)}
        self.assertIn(('self attribute', 'module text'), missed)
        self.assertIn(('direct', 'class text'), missed)         # self.TEXT_x: a class attribute (F3) too
        self.assertEqual({pair for pair in missed if pair[0] != 'self attribute'
                          and pair[1] not in ('class text', 'class constant')}, set())

    def test_the_theme_sinks_cover_the_ui_theme_api(self):
        """Every public function of ui_theme with a text-like parameter is a sink (THEME_SINKS) or is
        listed as not showing that parameter (THEME_NOT_SHOWN), so the display boundary has no
        unlisted text parameter (A15.4)."""
        tree = ast.parse((i18n_tool.SRC / 'ui_theme.py').read_text(encoding='utf-8'))
        unlisted = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
                for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    if arg.arg in i18n_tool.THEME_TEXT_PARAMS and arg.arg not in i18n_tool.THEME_SINKS.get(node.name, {}) \
                            and (node.name, arg.arg) not in i18n_tool.THEME_NOT_SHOWN:
                        unlisted.append(f'{node.name}({arg.arg})')
        self.assertEqual(unlisted, [])
        for name, params in i18n_tool.THEME_SINKS.items():
            definition = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
            positional = [arg.arg for arg in definition.args.posonlyargs + definition.args.args]
            for param, (position, kind, _path) in params.items():
                self.assertIn(kind, i18n_tool.SINK_KINDS, name)
                if position is not None:
                    self.assertEqual(positional[position], param, f'{name}: {param} is argument {position}')


if __name__ == '__main__':
    unittest.main()
