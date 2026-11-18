"""H3 guard of the protected blocks (MESSAGES.md 7.7, 9.2; WP-2).

    python -X utf8 -B test_protected_blocks.py

The three protected blocks of engine.py are compared with the frozen 1.7.0 engine.py
(verification/release-sources/1.7.0/src) as ASTs:

  (1) Runner._run_once (H3),
  (2) the try statement in Runner.download's finally whose body calls unfinished.merge (H3),
  (3) the while loop of Runner._cooled (the 429 loop moved verbatim in 1.7.0).

The sanctioned nodes S1-S7 are located in the 1.7.0 tree by their literal text; the nodes at the
same AST paths of the 1.8.0 tree are taken; both are replaced by a placeholder and the dumps
(without positions) must be equal.  Each 1.8.0 sanctioned node must then dump equal to its
expected expression of 9.2, and its zh value, evaluated with sample values, must equal the value
of the 1.7.0 literal.  The StudioApp.set_busy / poll pins of test_i18n_download.py stay the 1.7.0
ones.  Self-tests: a changed condition in a copy of _run_once, a changed sanctioned expression
and an inserted statement are reported.  No window, no process.
"""
import _sandbox
import ast
import copy
import hashlib
import types
import unittest

import engine
import messages
from messages import Message

OLD = _sandbox.release_src('1.7.0')
NEW = _sandbox.SRC
# The 1.7.0 literal of each sanctioned node (its first literal part for an f-string), the block
# it belongs to, and the 1.8.0 expression of MESSAGES.md 9.2 with its key.
SANCTIONED = {
    'S1': ('_run_once', '下载间隔 / 重试等待', 'WAIT_PACE_OR_RETRY', 'core.wait.pace_or_retry'),
    'S2': ('_run_once', '归档信息保存失败（媒体已保留）：',
           "msg('core.engine.archive_info_failed', error=message_of(error))", 'core.engine.archive_info_failed'),
    'S3': ('merge', '失败清单未能保存：',
           "msg('core.engine.unfinished_not_saved', error=message_of(error))", 'core.engine.unfinished_not_saved'),
    'S4': ('_cooled', '无法启动下载引擎：',
           "msg('core.engine.cannot_start_engine', error=_sentence(error))", 'core.engine.cannot_start_engine'),
    'S5': ('_cooled', '此链接多次触发 HTTP 429，已记入失败清单。', "msg('core.engine.rate_limited_out')",
           'core.engine.rate_limited_out'),
    'S6': ('_cooled', '网站限流，暂停整个下载队列', "msg('core.wait.rate_queue')", 'core.wait.rate_queue'),
    'S7': ('_cooled', '网站限流，冷却后重试此链接（',
           "msg('core.wait.rate_retry', attempt=self._rate_attempt, limit=MAX_RATE_LIMIT_RETRIES)",
           'core.wait.rate_retry'),
}
# test_i18n_download.PROTECTED (1.7.0): sha256 of the source segments of StudioApp.set_busy / poll.
STUDIO_PINS = {'set_busy': '2947d2da0755e1c42236e6e05225e7660a900cb42f4c5b7b85c49f6af87dead7',
               'poll': 'ba3f5fb76cfd49aa2045eea313f4828b02a34abab7e2404d57a783f1601313cb'}


def parse(folder, name='engine.py'):
    return ast.parse((folder / name).read_text(encoding='utf-8'))


def _class(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _method(cls, name):
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls_merge(node) -> bool:
    return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == 'merge'
               and isinstance(sub.func.value, ast.Name) and sub.func.value.id == 'unfinished'
               for statement in node.body for sub in ast.walk(statement))


def blocks(tree) -> dict:
    """{'_run_once' | 'merge' | '_cooled': node} of a parsed engine.py."""
    runner = _class(tree, 'Runner')
    download = _method(runner, 'download')
    outer = [node for node in download.body if isinstance(node, ast.Try) and node.finalbody]
    merges = [node for statement in outer[0].finalbody for node in ast.walk(statement)
              if isinstance(node, ast.Try) and _calls_merge(node)] if len(outer) == 1 else []
    loops = [node for node in _method(runner, '_cooled').body if isinstance(node, ast.While)]
    if len(outer) != 1 or len(merges) != 1 or len(loops) != 1:
        raise AssertionError(f'protected blocks not found: finally {len(outer)}, merge try {len(merges)}, '
                             f'while {len(loops)}')
    return {'_run_once': _method(runner, '_run_once'), 'merge': merges[0], '_cooled': loops[0]}


def _children(node):
    for field, item in ast.iter_fields(node):
        if isinstance(item, ast.AST):
            yield (field,), item
        elif isinstance(item, list):
            for index, child in enumerate(item):
                if isinstance(child, ast.AST):
                    yield (field, index), child


def paths_of(root, wanted):
    """[path] of the nodes below ``root`` for which ``wanted(node)`` is true; a path is a tuple of
    (field,) / (field, index) steps."""
    found = []

    def walk(node, at):
        if wanted(node):
            found.append(at)
        for step, child in _children(node):
            walk(child, at + (step,))

    walk(root, ())
    return found


def node_at(root, path):
    node = root
    for step in path:
        node = getattr(node, step[0])
        if len(step) == 2:
            node = node[step[1]]
    return node


def replaced(root, path, placeholder):
    """A copy of ``root`` with the node at ``path`` replaced by ``placeholder``."""
    root = copy.deepcopy(root)
    if not path:
        return placeholder
    parent = node_at(root, path[:-1])
    step = path[-1]
    if len(step) == 2:
        getattr(parent, step[0])[step[1]] = placeholder
    else:
        setattr(parent, step[0], placeholder)
    return root


FSTRINGS = ('S2', 'S3', 'S4', 'S7')        # the 1.7.0 node is an f-string (its first literal part is given)


def _wanted(name, text):
    if name in FSTRINGS:
        return lambda node: isinstance(node, ast.JoinedStr) and bool(node.values) \
            and isinstance(node.values[0], ast.Constant) and node.values[0].value == text
    return lambda node: isinstance(node, ast.Constant) and node.value == text


def sanctioned_paths(old_blocks) -> dict:
    """{S: (block name, path)} located in the 1.7.0 blocks by literal text; exactly one each."""
    located = {}
    for name, (block, text, _expression, _key) in SANCTIONED.items():
        found = paths_of(old_blocks[block], _wanted(name, text))
        if len(found) != 1:
            raise AssertionError(f'{name}: {len(found)} nodes with the 1.7.0 text {text!r}')
        located[name] = (block, found[0])
    return located


def dump(node) -> str:
    return ast.dump(node, include_attributes=False)


def differences(old_tree, new_tree) -> list:
    """Every difference between the protected blocks of two engine.py trees beyond S1-S7, and every
    sanctioned node of the new tree that is not its expected expression."""
    problems = []
    old_blocks, new_blocks = blocks(old_tree), blocks(new_tree)
    located = sanctioned_paths(old_blocks)
    for block in old_blocks:
        old, new = old_blocks[block], new_blocks[block]
        for name, (where, path) in sorted(located.items()):
            if where != block:
                continue
            try:
                current = node_at(new, path)
            except (AttributeError, IndexError):
                problems.append(f'{block}: {name} has no node at its 1.7.0 path')
                continue
            expected = ast.parse(SANCTIONED[name][2], mode='eval').body
            if dump(current) != dump(expected):
                problems.append(f'{block}: {name} is {ast.unparse(current)!r}, not {SANCTIONED[name][2]!r}')
            placeholder = ast.Name(id=f'__SANCTIONED_{name}__', ctx=ast.Load())
            old = replaced(old, path, placeholder)
            new = replaced(new, path, copy.deepcopy(placeholder))
        if dump(old) != dump(new):
            problems.append(f'{block}: the block differs from 1.7.0 outside the sanctioned nodes')
    return problems


def zh_value(node, namespace):
    return eval(compile(ast.Expression(body=copy.deepcopy(node)), '<sanctioned>', 'eval'), dict(namespace))


class ProtectedBlockTests(unittest.TestCase):
    def setUp(self):
        self.old, self.new = parse(OLD), parse(NEW)

    def test_the_blocks_equal_1_7_0_except_the_sanctioned_nodes(self):
        self.assertEqual(differences(self.old, self.new), [])
        located = sanctioned_paths(blocks(self.old))
        self.assertEqual(sorted(located), ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
        self.assertEqual({name: block for name, (block, _path) in located.items()},
                         {name: entry[0] for name, entry in SANCTIONED.items()})

    def test_the_sanctioned_nodes_keep_their_zh_values(self):
        """Evaluated with sample values: a plain OS error, errors that carry a message (not for S4:
        the engine start fails with the OS's own error only), and each retry attempt; the 1.8.0
        node gives a Message with its key and the 1.7.0 literal's value."""
        old_blocks, new_blocks = blocks(self.old), blocks(self.new)
        located = sanctioned_paths(old_blocks)
        errors = [OSError(2, '系统找不到指定的文件。'), ValueError('database is locked'),
                  OSError(messages.msg('core.archive.unwritable', error='disk I/O error')),
                  ValueError(messages.msg('core.archive.media_mismatch'))]
        checked = 0
        for name, (block, path) in sorted(located.items()):
            old_node, new_node = node_at(old_blocks[block], path), node_at(new_blocks[block], path)
            for error in errors[:2] if name == 'S4' else errors:
                for attempt in (1, engine.MAX_RATE_LIMIT_RETRIES):
                    namespace = {'msg': engine.msg, 'message_of': engine.message_of, '_sentence': engine._sentence,
                                 'WAIT_PACE_OR_RETRY': engine.WAIT_PACE_OR_RETRY,
                                 'MAX_RATE_LIMIT_RETRIES': engine.MAX_RATE_LIMIT_RETRIES,
                                 'self': types.SimpleNamespace(_rate_attempt=attempt), 'error': error}
                    old, new = zh_value(old_node, namespace), zh_value(new_node, namespace)
                    self.assertIsInstance(new, Message, name)
                    self.assertEqual(new.key, SANCTIONED[name][3], name)
                    self.assertEqual(messages.zh(new), old, f'{name} with {error!r}')
                    checked += 1
            if name in ('S2', 'S3'):
                carried = errors[2]
                value = zh_value(new_node, {'msg': engine.msg, 'message_of': engine.message_of, 'error': carried})
                self.assertIs(value.params['error'], carried.args[0], f'{name}: the nested message keeps its key')
        self.assertEqual(checked, (6 * 4 + 2) * 2)

    def test_the_studio_set_busy_and_poll_pins_are_the_1_7_0_ones(self):
        for folder in (OLD, NEW):
            source = (folder / 'studio.py').read_text(encoding='utf-8')
            studio = _class(ast.parse(source), 'StudioApp')
            found = {name: hashlib.sha256(ast.get_source_segment(source, _method(studio, name)).encode('utf-8')).hexdigest()
                     for name in STUDIO_PINS}
            self.assertEqual(found, STUDIO_PINS, folder)
        pinned = {}
        for node in parse(_sandbox.ROOT / 'verification', 'test_i18n_download.py').body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PROTECTED' for t in node.targets):
                pinned = ast.literal_eval(node.value)
        self.assertEqual(pinned, STUDIO_PINS, 'test_i18n_download.PROTECTED is unchanged')


class SelfTests(unittest.TestCase):
    """The guard reports what it must report."""

    def changed(self, old_text, new_text, count=1):
        source = (NEW / 'engine.py').read_text(encoding='utf-8')
        self.assertEqual(source.count(old_text), count, old_text)
        return ast.parse(source.replace(old_text, new_text))

    def test_a_changed_condition_in_run_once_is_reported(self):
        tree = self.changed('                    if seconds == 0:\n', '                    if seconds <= 0:\n')
        self.assertEqual(differences(parse(OLD), tree),
                         ['_run_once: the block differs from 1.7.0 outside the sanctioned nodes'])

    def test_a_changed_sanctioned_expression_is_reported(self):
        tree = self.changed("msg('core.engine.rate_limited_out')", "msg('core.engine.incompatible')")
        problems = differences(parse(OLD), tree)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn('S5', problems[0])

    def test_an_inserted_statement_in_the_merge_block_is_reported(self):
        tree = self.changed('                stored = len(entries)\n',
                            '                stored = len(entries)\n                stored += 0\n')
        self.assertEqual(differences(parse(OLD), tree),
                         ['merge: the block differs from 1.7.0 outside the sanctioned nodes'])

    def test_a_changed_line_around_a_sanctioned_node_is_reported(self):
        tree = self.changed("'reason': WAIT_PACE_OR_RETRY", "'why': WAIT_PACE_OR_RETRY")
        self.assertEqual(differences(parse(OLD), tree),
                         ['_run_once: the block differs from 1.7.0 outside the sanctioned nodes'])

    def test_the_1_7_0_blocks_against_themselves_report_only_the_sanctioned_nodes(self):
        problems = differences(parse(OLD), parse(OLD))
        self.assertEqual(sorted(problem.split(':')[1].split()[0] for problem in problems),
                         ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])


if __name__ == '__main__':
    unittest.main()
