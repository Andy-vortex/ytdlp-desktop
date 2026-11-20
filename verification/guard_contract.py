"""The guard contract of MESSAGES.md 15 (amendments A15 and A16) as data, and the combinatorial
self-test that proves the static scanners implement it (1.8.0 converge round; Phase D fix round 1).

test_text_guard.ContractTests runs it against verification/i18n_tool.py; a scratch script can run
the same generator against the scanner of another tree (``evaluate(tool, folder)``).

The contract itself is the list ``i18n_tool.CONTRACT`` (one id per source, flow, sink kind and sink
spelling, kept next to the scanner that implements them, A16.7).  Every case of this generator is
tagged with the ids it exercises; ContractTests requires a bad and a good case for every id (and no
id outside the list), so the coverage is checked against the contract, not against this file.

G5: every SOURCE (text fixed in the source that is no display text, and display text) goes through
every FLOW into every SINK kind of i18n_tool.SINKS; every sink SPELLING (each callee, keyword,
receiver binding, alias and ``**mapping`` form, every THEME_SINKS parameter) gets every source
through three flows.  G1b: every source through every flow into every text slot class, and every slot
spelling.  The parameters are crossed at wrapper depth 1, 2 and 3, positionally and by keyword, and
every G5 sink is also reached through messages.show().  Each case is one method with its own helpers,
attributes and constants (all named after the case), written to synthetic modules in the sandbox; a
bad case must be flagged inside its own lines, a good one must not be.  The reviewers' probe forms of
the final review (rounds 3 and 4) are named cases of their own (PROBES).  MUTANTS removes one
scanner behaviour at a time and names the cases that must then be missed (the self-test of the
self-test, A16.7).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import textwrap
import types
from pathlib import Path

PARAMS = 'value, item, rows, flag'          # every generated function takes these: event, record, data, flag

# ----------------------------------------------------------------------------------- sources (A15.2)


@dataclass
class Source:
    name: str
    bad: bool
    expr: str                               # the expression in a method of the case module ({c}: the case id)
    module: tuple = ()                      # module-level lines of the case module
    other: tuple = ()                       # module-level lines of the other scanned module (ui_log / naming)
    klass: tuple = ()                       # class-body lines of the case's class
    imports: tuple = ()                     # import lines of the case module
    params: bool = False                    # the expression reads the method's parameters
    selfish: bool = False                   # the expression reads self
    forms: tuple = ()                       # the contract ids it exercises (i18n_tool.CONTRACT)
    light: bool = False                     # crossed only with SPELLING_FLOWS (a good case of F2 / F3)


G5_SOURCES = (
    Source('cjk literal', True, "'这个订阅目前没有待处理的新增内容。'", forms=('S2 Chinese literal',)),
    Source('module text', True, 'TEXT_{c}', module=("TEXT_{c} = 'A fixed text of the module.'",),
           forms=('S3 module text', 'F2 module name')),
    Source('module cjk text', True, 'TEXT_{c}', module=("TEXT_{c} = '请先在下方列表中选择一条资料。'",),
           forms=('S2 Chinese literal', 'S3 module text', 'F2 module name')),
    Source('class text', True, 'self.TEXT_{c}', klass=("TEXT_{c} = 'A fixed text of the class.'",), selfish=True,
           forms=('S3 class text', 'F3 class attribute')),
    Source('other module text', True, '{O}.TEXT_{c}', other=("TEXT_{c} = '采集完成'",),
           forms=('S4 other module', 'F2 module.NAME')),
    Source('imported text', True, 'OTHER_{c}', other=("OTHER_{c} = '采集完成'",),
           imports=('from {O} import OTHER_{c}',), forms=('S4 other module', 'F2 from import')),
    Source('enum value', True, 'formats.PRESETS[0].label', forms=('S5 enum source',)),
    Source('zh template', True, "messages.template('core.update.no_github')", forms=('S6 zh template',)),
    Source('t() text', False, "i18n.t('library.no_pending')", forms=('N t()',)),
    Source('shown core value', False, "messages.show(value['reason'])", params=True, forms=('N show()',)),
    Source('number', False, 'len(rows)', params=True, forms=('N number',)),
    Source('raw user data', False, "messages.show(messages.raw(item['title']))", params=True,
           forms=('N show(raw())',)),
    # an event value (opaque to the flows): no source, so bad only where the rule needs display text —
    # at a strict sink without show() (A15.4), and as a part of a composition at a value sink (7.9)
    Source('event value', None, "value['title']", params=True, forms=('O event value',)),
    # display text through the module and class names (7.9 / A7.3: an int / float constant)
    Source('module number', False, 'GAP_{c}', module=('GAP_{c} = 12',), forms=('N number', 'F2 module name'),
           light=True),
    Source('other module number', False, '{O}.GAP_{c}', other=('GAP_{c} = 12',),
           forms=('N number', 'F2 module.NAME'), light=True),
    Source('imported number', False, 'NUM_{c}', other=('NUM_{c} = 12',), imports=('from {O} import NUM_{c}',),
           forms=('N number', 'F2 from import'), light=True),
    Source('class number', False, 'self.GAP_{c}', klass=('GAP_{c} = 12',), selfish=True,
           forms=('N number', 'F3 class attribute'), light=True),
)

G1_SOURCES = (
    Source('allow-listed literal', True, "'媒体地址'", forms=('S1 allow-listed literal',)),
    Source('module constant', True, 'KIND_{c}', module=("KIND_{c} = '媒体地址'",),
           forms=('S1 allow-listed literal', 'F2 module name')),
    Source('class constant', True, 'self.KIND_{c}', klass=("KIND_{c} = '媒体地址'",), selfish=True,
           forms=('S1 allow-listed literal', 'F3 class attribute')),
    Source('other module enum', True, 'formats.MODES[0]', forms=('S5 enum source', 'F2 module.NAME')),
    Source('imported enum', True, 'MODES[0]', imports=('from formats import MODES',),
           forms=('S5 enum source', 'F2 from import')),
    Source('enum record label', True, 'formats.PRESETS[0].label', forms=('S5 enum source',)),
    Source('zh template', True, "messages.template('core.update.no_github')", forms=('S6 zh template',)),
    Source('message', False, "msg('core.reason.network')", forms=('N msg()',)),
    Source('raw user data', False, "raw(item['title'])", params=True, forms=('N raw()',)),
    Source('number', False, 'len(rows)', params=True, forms=('N number',)),
    # an event value: no G1b source; a composition of it in a core text slot is a G1f finding (Q2)
    Source('event value', False, "value['title']", params=True, forms=('O G1f composition',)),
    # no allow-listed text through the module and class names (a message is no source)
    Source('module message', False, 'MSG_{c}', module=("MSG_{c} = msg('core.reason.network')",),
           forms=('N msg()', 'F2 module name'), light=True),
    Source('other module message', False, '{O}.MSG_{c}', other=("MSG_{c} = msg('core.reason.network')",),
           forms=('N msg()', 'F2 module.NAME'), light=True),
    Source('imported message', False, 'MSG2_{c}', other=("MSG2_{c} = msg('core.reason.network')",),
           imports=('from {O} import MSG2_{c}',), forms=('N msg()', 'F2 from import'), light=True),
    Source('class message', False, 'self.MSG_{c}', klass=("MSG_{c} = msg('core.reason.network')",), selfish=True,
           forms=('N msg()', 'F3 class attribute'), light=True),
)

# ----------------------------------------------------------------------------------- flows (A15.3, A16.2-A16.3)


@dataclass
class Flow:
    """``build(c, s, klass, other)`` -> (lines before the sink, the expression at the sink, loop depth
    of the sink, helper methods, helper module functions, helpers of the other module, imports).
    ``needs``: 'constant' (a default: no names of the method), 'noself' (no self), 'noparams' (no
    parameters of the case method), 'other' (the value is made in the other scanned module), 'window'
    (another mixin of the window: G5 only).  ``strict_good``: the 7.9 grammar proves display text
    through this flow (else display text reaches a strict sink only through show(): its good cases
    are generated for the value sinks only)."""
    name: str
    build: object
    needs: frozenset = frozenset()
    forms: tuple = ()
    strict_good: bool = True
    composes: str = ''                      # 'value': a composition of VALUE_COMPOSITIONS; 'g1f': one G1f adds
    neutral: bool = False                   # a language-neutral wrapper (F11): urls.display, os.fspath, Path().name


def _f(name, before=(), expr='{s}', depth=0, methods=(), functions=(), other=(), needs='', forms=(),
       strict_good=True, imports=(), composes='', neutral=False):
    def build(c, s, klass='LibraryUI', other_module='ui_log'):
        def fill(text):
            return text.replace('{c}', c).replace('{s}', s).replace('{K}', klass).replace('{O}', other_module)
        return ([fill(line) for line in before], fill(expr) if expr else expr, depth, [fill(m) for m in methods],
                [fill(f) for f in functions], [fill(o) for o in other], [fill(i) for i in imports])
    return Flow(name, build, frozenset(needs.split()), tuple(forms) or (name,), strict_good, composes, neutral)


SET = 'self.set_{c}(' + PARAMS + ')'
SETTER = 'def set_{c}(self, ' + PARAMS + '):\n'
GET = 'def get_{c}(self, ' + PARAMS + '):\n    return {s}'

FLOWS = (
    _f('direct', forms=('F1 direct value',)),
    _f('local name', ('v_{c} = {s}',), 'v_{c}', forms=('F1 assignment',)),
    _f('rebinding', ("v_{c} = ''", 'v_{c} = v_{c} or {s}'), 'v_{c}', forms=('F1 rebinding', 'F4 or / and')),
    _f('alias chain', ('a_{c} = {s}', 'b_{c} = a_{c}', 'v_{c} = b_{c}'), 'v_{c}', forms=('F1 alias chain',)),
    _f('augmented assignment', ("v_{c} = ''", 'v_{c} += {s}'), 'v_{c}', forms=('F1 augmented assignment', 'F8 +'), composes='value'),
    _f('annotated assignment', ('v_{c}: str = {s}',), 'v_{c}', forms=('F1 annotated assignment',)),
    _f('walrus', ('w_{c} = (v_{c} := {s})',), 'v_{c}', forms=('F1 walrus',)),
    _f('with as', ('with {s} as v_{c}:',), 'v_{c}', depth=1, forms=('F1 with as',)),
    _f('parameter rebound', ('flag = flag or {s}',), 'flag', forms=('F1 parameter rebound',)),
    _f('global', (SET,), 'G_{c}', methods=(SETTER + '    global G_{c}\n    G_{c} = {s}',), forms=('F1 global',),
       strict_good=False),
    _f('nonlocal', ("v_{c} = ''", 'def inner_{c}():\n    nonlocal v_{c}\n    v_{c} = {s}', 'inner_{c}()'), 'v_{c}',
       forms=('F1 nonlocal',)),
    _f('conditional', (), "({s} if flag else '')", forms=('F4 conditional',)),
    _f('boolop', (), "('' or {s})", forms=('F4 or / and',)),
    _f('tuple index', (), "({s}, '')[0]", forms=('F5 tuple index',)),
    _f('list slice', (), '[{s}][0:1][0]', forms=('F5 list slice',)),
    _f('nested selection', (), '[[{s}]][0][0]', forms=('F5 nested selection',)),
    _f('set display', (), 'next(iter({{s}}))', forms=('F5 set display', 'F7 iter()', 'F7 next()')),
    _f('dict item', (), "{'k': {s}}['k']", forms=('F5 dict item',)),
    _f('neutral key', (), "{'url': {s}}['url']", forms=('F5 neutral-named item',)),
    _f('neutral get', (), "{'path': {s}}.get('path')", forms=('F5 neutral-named item',)),
    _f('dict get', (), "{'k': {s}}.get('k')", forms=('F5 dict get',)),
    _f('get default', (), "{}.get('k', {s})", forms=('F5 get default',)),
    _f('dict pop', ("d_{c} = {'k': {s}}",), "d_{c}.pop('k')", forms=('F5 pop',)),
    _f('pop default', ('d_{c} = {}',), "d_{c}.pop('k', {s})", forms=('F5 pop default',)),
    _f('dict setdefault', ('d_{c} = {}',), "d_{c}.setdefault('k', {s})", forms=('F5 setdefault',)),
    _f('keys()', (), 'next(iter({{s}: 1}.keys()))', forms=('F5 keys()',)),
    _f('popitem()', (), "{'k': {s}}.popitem()[1]", forms=('F5 popitem()',)),
    _f('dict iteration', ('for v_{c} in {{s}: 1}:',), 'v_{c}', depth=1, forms=('F5 dict iteration',)),
    _f('append mutation', ('l_{c} = []', 'l_{c}.append({s})'), 'l_{c}[0]', forms=('F5 append',)),
    _f('add mutation', ('s_{c} = set()', 's_{c}.add({s})'), 'next(iter(s_{c}))', forms=('F5 add',)),
    _f('insert mutation', ('l_{c} = []', 'l_{c}.insert(0, {s})'), 'l_{c}[0]', forms=('F5 insert',)),
    _f('extend mutation', ('l_{c} = []', 'l_{c}.extend([{s}])'), 'l_{c}[0]', forms=('F5 extend',)),
    _f('item mutation', ('d_{c} = {}', "d_{c}['k'] = {s}"), "d_{c}['k']", forms=('F5 item assignment',)),
    _f('item augmented', ("d_{c} = {'k': ''}", "d_{c}['k'] += {s}"), "d_{c}['k']", forms=('F5 item augmented',), composes='value'),
    _f('update mutation', ('d_{c} = {}', 'd_{c}.update(k={s})'), "d_{c}['k']", forms=('F5 update(k=)',)),
    _f('update(mapping)', ('d_{c} = {}', "d_{c}.update({'k': {s}})"), "d_{c}['k']", forms=('F5 update(mapping)',)),
    _f('self attribute', (SET,), 'self.attr_{c}', methods=(SETTER + '    self.attr_{c} = {s}',),
       forms=('F3 self attribute',)),
    _f('tuple-target attribute', (SET,), 'self.attr_{c}',
       methods=(SETTER + "    self.attr_{c}, self.tone_{c} = {s}, 'warn'",), forms=('F3 tuple-target attribute',)),
    _f('cross-module attribute', (SET,), 'self.attr_{c}', other=(SETTER + '    self.attr_{c} = {s}',),
       needs='other window', forms=('F3 cross-module attribute',)),
    _f('setattr', (SET,), 'self.attr_{c}', methods=(SETTER + "    setattr(self, 'attr_{c}', {s})",),
       forms=('F3 setattr',)),
    _f('getattr', (SET,), "getattr(self, 'attr_{c}')", methods=(SETTER + '    self.attr_{c} = {s}',),
       forms=('F3 getattr',)),
    _f('getattr default', (SET,), "getattr(self, 'attr_{c}', {s})", methods=(SETTER + "    self.attr_{c} = ''",),
       forms=('F3 getattr default',)),
    _f('class name attribute', (SET,), '{K}.attr_{c}', methods=(SETTER + '    {K}.attr_{c} = {s}',),
       forms=('F3 class name attribute',)),
    _f('cls attribute', ('self.set_{c}()',), 'self.attr_{c}',
       methods=('@classmethod\ndef set_{c}(cls):\n    cls.attr_{c} = {s}',), needs='noself noparams',
       forms=('F3 cls attribute',)),
    _f('augmented attribute', (SET,), 'self.attr_{c}', methods=(SETTER + "    self.attr_{c} = ''\n    self.attr_{c} += {s}",),
       forms=('F3 augmented attribute', 'F8 +'), composes='value'),
    _f('annotated attribute', (SET,), 'self.attr_{c}', methods=(SETTER + '    self.attr_{c}: str = {s}',),
       forms=('F3 annotated attribute',)),
    _f('attribute mutation', (SET,), 'self.list_{c}[0]',
       methods=(SETTER + '    self.list_{c} = []\n    self.list_{c}.append({s})',), forms=('F3 attribute mutation',)),
    _f('property', (), 'self.prop_{c}', methods=('@property\ndef prop_{c}(self):\n    return {s}',), needs='noparams',
       forms=('F3 property',)),
    _f('unpacking', ("x_{c}, y_{c} = {s}, ''",), 'x_{c}', forms=('F6 unpacking',)),
    _f('starred unpacking', ("x_{c}, *y_{c} = '', {s}",), 'y_{c}[0]', forms=('F6 starred unpacking',)),
    _f('nested unpacking', ("a_{c}, (b_{c}, v_{c}) = '', ('', {s})",), 'v_{c}', forms=('F6 nested unpacking',)),
    _f('for loop', ('for v_{c} in ({s},):',), 'v_{c}', depth=1, forms=('F1 for target',)),
    _f('enumerate', ('for i_{c}, v_{c} in enumerate(({s},)):',), 'v_{c}', depth=1, forms=('F6 enumerate',)),
    _f('zip', ("for k_{c}, v_{c} in zip(('a',), ({s},)):",), 'v_{c}', depth=1, forms=('F6 zip',)),
    _f('map', (), 'next(map(str, ({s},)))', forms=('F7 map', 'F7 next()')),
    _f('filter', (), 'next(filter(None, ({s},)))', forms=('F7 filter',)),
    _f('sorted', (), 'sorted(({s},))[0]', forms=('F7 sorted',)),
    _f('reversed', (), 'next(reversed(({s},)))', forms=('F7 reversed',)),
    _f('list()', (), 'list(({s},))[0]', forms=('F7 list()',)),
    _f('tuple()', (), 'tuple([{s}])[0]', forms=('F7 tuple()',)),
    _f('set()', (), 'next(iter(set(({s},))))', forms=('F7 set()',)),
    _f('frozenset()', (), 'next(iter(frozenset(({s},))))', forms=('F7 frozenset()',)),
    _f('iter()', (), 'next(iter(({s},)))', forms=('F7 iter()',)),
    _f('next() default', (), 'next(iter(()), {s})', forms=('F7 next() default',)),
    _f('dict()', (), "dict(k={s})['k']", forms=('F7 dict(k=)',)),
    _f('dict(mapping)', (), "dict({'k': {s}})['k']", forms=('F7 dict(mapping)',)),
    _f('dict(pairs)', (), "dict([('k', {s})])['k']", forms=('F7 dict(pairs)',)),
    _f('min', (), 'min(({s},))', forms=('F7 min()',)),
    _f('max', (), "max(({s}, ''))", forms=('F7 max()',)),
    _f('max several', (), "max({s}, '')", forms=('F7 max() several',)),
    _f('min() default', (), 'min((), default={s})', forms=('F7 min() default',)),
    _f('copy.copy', (), 'copy.copy({s})', forms=('F7 copy.copy',)),
    _f('values loop', ("for v_{c} in {'k': {s}}.values():",), 'v_{c}', depth=1, forms=('F5 values()',)),
    _f('items loop', ("for k_{c}, v_{c} in {'k': {s}}.items():",), 'v_{c}', depth=1,
       forms=('F5 items()', 'F6 items() unpacking')),
    _f('list comprehension', (), '[v_{c} for v_{c} in ({s},)][0]', forms=('F5 list comprehension',
                                                                         'F1 comprehension target')),
    _f('set comprehension', (), 'next(iter({v_{c} for v_{c} in ({s},)}))', forms=('F5 set comprehension',)),
    _f('generator', (), 'next(v_{c} for v_{c} in ({s},))', forms=('F5 generator',)),
    _f('dict comprehension', (), "{k_{c}: v_{c} for k_{c}, v_{c} in (('k', {s}),)}['k']",
       forms=('F5 dict comprehension',)),
    _f('dict comprehension keys', (), 'next(iter({v_{c}: 1 for v_{c} in ({s},)}))',
       forms=('F5 dict comprehension keys',)),
    _f('** display', (), "{**{'k': {s}}}['k']", forms=('F5 ** spread',)),
    _f('* display', (), '[*({s},)][0]', forms=('F5 starred element',)),
    _f('f-string', (), 'f"{{s}} x"', forms=('F8 f-string',), composes='value'),
    _f('f-string conversion', (), 'f"{{s}!s} x"', forms=('F8 f-string conversion',), composes='value'),
    _f('concatenation', (), "{s} + ' x'", forms=('F8 +',), composes='value'),
    _f('repetition', (), '{s} * 1', forms=('F8 *',), composes='value'),
    _f('% format', (), "'%s x' % ({s},)", forms=('F8 %',), composes='value'),
    _f('.format', (), "'{} x'.format({s})", forms=('F8 .format',), composes='value'),
    _f('.format_map', (), "'{a} x'.format_map({'a': {s}})", forms=('F8 .format_map',), composes='value'),
    _f('.join', (), "' '.join([{s}])", forms=('F8 .join',), composes='value'),
    _f('.join receiver', (), "{s}.join(['a', 'b'])", forms=('F8 .join receiver',), composes='value'),
    _f('str method', (), '{s}.strip()', forms=('F8 str method',), composes='g1f'),
    _f('str method unbound', (), 'str.strip({s})', forms=('F8 str method unbound',), composes='g1f'),
    _f('replace inserted', (), "'a x'.replace('a', {s})", forms=('F8 replace inserted',), composes='g1f'),
    _f('center inserted', (), "'x'.center(5, {s})", forms=('F8 center inserted',), composes='g1f'),
    _f('str()', (), 'str({s})', forms=('F8 str()',), composes='g1f'),
    _f('format()', (), 'format({s})', forms=('F8 format()',), composes='g1f'),
    _f('re.sub repl', (), "re.sub('a', {s}, 'b')", forms=('F8 re.sub',), composes='value'),
    _f('re.sub string', (), "re.sub('a', 'b', {s})", forms=('F8 re.sub',), composes='value'),
    _f('text slice', (), '{s}[0:40]', forms=('F8 text slice',), strict_good=False, composes='g1f'),
    _f('textwrap', (), 'textwrap.shorten({s}, 30)', forms=('F8 textwrap',), composes='value'),
    _f('method return', (), 'self.get_{c}(' + PARAMS + ')', methods=(GET,), forms=('F9 method return',)),
    _f('function return', (), 'get_{c}(' + PARAMS + ')',
       functions=('def get_{c}(' + PARAMS + '):\n    return {s}',), needs='noself', forms=('F9 function return',)),
    _f('returned parameter', (), 'echo_{c}({s})', functions=('def echo_{c}(text):\n    return text',),
       forms=('F9 returned parameter',)),
    _f('yield', (), 'next(self.gen_{c}(' + PARAMS + '))',
       methods=('def gen_{c}(self, ' + PARAMS + '):\n    yield {s}',), forms=('F9 yield',)),
    _f('nested function', ('def inner_{c}():\n    return {s}',), 'inner_{c}()', forms=('F9 nested function',)),
    _f('staticmethod', (), 'self.st_{c}()', methods=('@staticmethod\ndef st_{c}():\n    return {s}',),
       needs='noself noparams', forms=('F9 staticmethod',)),
    _f('classmethod', (), 'self.cm_{c}()', methods=('@classmethod\ndef cm_{c}(cls):\n    return {s}',),
       needs='noself noparams', forms=('F9 classmethod',)),
    _f('other module function', (), '{O}.get_{c}(' + PARAMS + ')',
       other=('def get_{c}(' + PARAMS + '):\n    return {s}',), needs='noself other', forms=('F9 other module function',)),
    _f('from-import function', (), 'get_{c}(' + PARAMS + ')', other=('def get_{c}(' + PARAMS + '):\n    return {s}',),
       imports=('from {O} import get_{c}',), needs='noself other', forms=('F9 from-import function',)),
    _f('alias of a function', ('make_{c} = self.get_{c}',), 'make_{c}(' + PARAMS + ')', methods=(GET,),
       forms=('F9 alias of a function',)),
    _f('getattr method', (), "getattr(self, 'get_{c}')(" + PARAMS + ')', methods=(GET,), forms=('F9 getattr method',)),
    _f('lambda', (), '(lambda: {s})()', forms=('F9 lambda',)),
    _f('lambda name', ('make_{c} = lambda: {s}',), 'make_{c}()', forms=('F9 lambda name',)),
    _f('dict of lambdas', (), "{'a': lambda: {s}}['a']()", forms=('F9 dict of lambdas',)),
    _f('urls.display', (), 'urls.display({s})', forms=('F11 urls.display',), neutral=True),
    _f('os.fspath', (), 'os.fspath({s})', forms=('F11 os.fspath',), neutral=True),
    _f('Path().name', (), 'Path({s}).name', forms=('F11 Path().name',), neutral=True),
    _f('parameter default', (), None, needs='constant', forms=('F10 parameter default',)),
)
ROTATE = tuple(flow for flow in FLOWS if not flow.needs)        # the flows crossed with the wrappers


def applicable(flow, source, klass='LibraryUI') -> bool:
    """A combination the language allows: a default and a module function see no method names."""
    if 'constant' in flow.needs and (source.params or source.selfish):
        return False
    if 'noself' in flow.needs and source.selfish:
        return False
    if 'noparams' in flow.needs and source.params:
        return False
    if 'window' in flow.needs and klass != 'LibraryUI':
        return False                        # another mixin of the window: G5 only
    if 'other' in flow.needs and klass == 'Archive':
        return False                        # library's archive code has no other module
    return True

# ----------------------------------------------------------------------------------- sinks (A15.4, A16.1)

# G5: one template per kind of i18n_tool.SINKS ({x}: the value)
G5_SINKS = OrderedDict([
    ('configure', 'self.status.configure(text={x})'),
    ('constructor', 'ctk.CTkLabel(self.box, text={x})'),
    ('theme', 'ui_theme.label(self.box, {x})'),
    ('theme unlabelled', 'ui_theme.option_menu(self.box, [{x}])'),
    ('theme columns', "ui_theme.table(self.box, [('k', {x}, 80)])"),
    ('chip set', 'self.state_chip.set({x})'),
    ('heading', "self.tree.heading('k', text={x})"),
    ('title', 'self.title({x})'),
    ('set_empty', 'self.tree.set_empty({x})'),
    ('fill', "self.tree.fill([('i', ({x},))])"),
    ('values', "self.tree.insert('', 'end', values=({x},))"),
    ('textbox insert', "self.log_box.insert('end', {x})"),
    ('menu', 'self.menu.add_command(label={x})'),
    ('messagebox', "messagebox.showinfo(i18n.t('library.no_pending'), {x}, parent=self)"),
    ('filedialog', 'filedialog.askdirectory(parent=self, title={x})'),
    ('t()', "self.note.configure(text=i18n.t('library.unreadable', error={x}))"),
    ('t() mapping', "self.note.configure(text=i18n.t('library.unreadable', **{'error': {x}}))"),
    ('sink method', 'self.status.configure(text={x})'),        # inside a wrapper: its parameter
    ('append_log', 'self.append_log({x})'),
    ('show()', 'self.cache.append(messages.show({x}))'),
    ('value sink method', 'self.append_log({x})'),             # inside a wrapper: its parameter
    ('library filetext t()', "self.files.append(i18n.t('filetext.page_text', title={x}, url='u', body='b'))"),
])
WRAPPED = ('sink method', 'value sink method')                  # crossed at depth 1 (and 2, 3 below)


@dataclass
class Spelling:
    """(A16.1) One way of writing a sink: ``kind`` (its SINKS kind), the statement ({x}: the value; lines
    split by newlines), its helpers, and the contract ids it exercises."""
    name: str
    kind: str
    template: str
    methods: tuple = ()
    module: tuple = ()
    other: tuple = ()
    functions: tuple = ()
    forms: tuple = ()
    needs: str = ''
    shown: bool = False           # the statement itself passes the value through show()
    composes: bool = False        # the statement itself composes the value (G1f)


def _s(name, kind, template, methods=(), module=(), other=(), functions=(), needs='', shown=False, composes=False):
    return Spelling(name, kind, template, tuple(methods), tuple(module), tuple(other), tuple(functions),
                    ('A16.1 ' + name,), needs, shown, composes)


SAY = ('def say_{c}(self, text):\n    self.status.configure(text=text)',)
NOTE = ('def note_{c}(self, text):\n    self.append_log(text)',)
T = "i18n.t('library.no_pending')"

# (A16.1) every THEME_SINKS parameter, positionally and by keyword
THEME_SPELLINGS = (
    ('label text', 'theme', 'ui_theme.label(self.box, {x})', 'ui_theme.label(self.box, text={x})'),
    ('button text', 'theme', 'ui_theme.button(self.box, {x})', 'ui_theme.button(self.box, text={x})'),
    ('chip text', 'theme', "ui_theme.chip(self.box, {x})", 'ui_theme.chip(self.box, text={x})'),
    ('checkbox text', 'theme', 'ui_theme.checkbox(self.box, {x})', 'ui_theme.checkbox(self.box, text={x})'),
    ('switch text', 'theme', 'ui_theme.switch(self.box, {x})', 'ui_theme.switch(self.box, text={x})'),
    ('entry placeholder', 'theme', 'ui_theme.entry(self.box, {x})', 'ui_theme.entry(self.box, placeholder={x})'),
    ('option_menu labels', 'theme', "ui_theme.option_menu(self.box, ['a'], [{x}])",
     "ui_theme.option_menu(self.box, ['a'], labels=[{x}])"),
    ('option_menu values', 'theme unlabelled', 'ui_theme.option_menu(self.box, [{x}])',
     'ui_theme.option_menu(self.box, values=[{x}])'),
    ('segmented labels', 'theme', "ui_theme.segmented(self.box, ['a'], [{x}])",
     "ui_theme.segmented(self.box, ['a'], labels=[{x}])"),
    ('tabs labels', 'theme', None, "ui_theme.tabs(self.box, ['a'], labels=[{x}])"),
    ('tabs names', 'theme unlabelled', 'ui_theme.tabs(self.box, [{x}])', 'ui_theme.tabs(self.box, names=[{x}])'),
    ('empty_state text', 'theme', 'ui_theme.empty_state(self.box, {x})', 'ui_theme.empty_state(self.box, text={x})'),
    ('table empty', 'theme', "ui_theme.table(self.box, [], 'browse', {x})", 'ui_theme.table(self.box, [], empty={x})'),
    ('table columns', 'theme columns', "ui_theme.table(self.box, [('k', {x}, 80)])",
     "ui_theme.table(self.box, columns=[('k', {x}, 80)])"),
    ('fit text', 'theme', 'ui_theme.fit(self.status, {x})', 'ui_theme.fit(self.status, text={x})'),
)

MENU_SPELLINGS = ('add_command', 'add_checkbutton', 'add_radiobutton', 'add_cascade', 'insert_command',
                  'insert_checkbutton', 'insert_radiobutton', 'insert_cascade', 'entryconfigure', 'entryconfig')
MESSAGEBOX_SPELLINGS = ('showinfo', 'showwarning', 'showerror', 'askyesno', 'askokcancel', 'askyesnocancel',
                        'askquestion', 'askretrycancel')
FILEDIALOG_SPELLINGS = ('askopenfilename', 'askopenfilenames', 'asksaveasfilename', 'askdirectory', 'askopenfile',
                        'askopenfiles', 'asksaveasfile')


def _g5_spellings():
    found = [
        _s('config text=', 'configure', 'self.status.config(text={x})'),
        _s('configure placeholder=', 'configure', 'self.entry_box.configure(placeholder={x})'),
        _s('configure placeholder_text=', 'configure', 'self.entry_box.configure(placeholder_text={x})'),
        _s('configure **mapping display', 'configure', "self.status.configure(**{'text': {x}})"),
        _s('configure **mapping name', 'configure',
           "o_{c} = {'fg_color': 'red'}\no_{c}['text'] = {x}\nself.status.configure(**o_{c})"),
        _s('configure cnf mapping', 'configure', "self.status.configure({'text': {x}})"),
        _s('configure callee alias', 'configure', 'c_{c} = self.status.configure\nc_{c}(text={x})'),
        _s('configure partial', 'configure', 'functools.partial(self.status.configure, text={x})()'),
        _s('constructor placeholder_text=', 'constructor', 'ctk.CTkEntry(self.box, placeholder_text={x})'),
        _s('constructor placeholder=', 'constructor', 'ctk.CTkEntry(self.box, placeholder={x})'),
        _s('constructor values=', 'constructor', 'ctk.CTkOptionMenu(self.box, values=[{x}])'),
        _s('constructor tk', 'constructor', 'tk.Label(self.box, text={x})'),
        _s('constructor ttk', 'constructor', 'ttk.Button(self.box, text={x})'),
        _s('constructor tkinter', 'constructor', 'tkinter.Label(self.box, text={x})'),
        _s('constructor customtkinter', 'constructor', 'customtkinter.CTkLabel(self.box, text={x})'),
        _s('constructor import alias', 'constructor', 'ck.CTkLabel(self.box, text={x})'),
        _s('constructor from-import', 'constructor', 'CTkLabel(self.box, text={x})'),
        _s('constructor **mapping', 'constructor', "ctk.CTkLabel(self.box, **{'text': {x}})"),
        _s('constructor **kw wrapper', 'sink method', 'self.mk_{c}(text={x})',
           methods=('def mk_{c}(self, **kw):\n    return ctk.CTkLabel(self.box, **kw)',)),
        _s('theme from-import', 'theme', 'theme_fit(self.status, {x})'),
        _s('theme module alias', 'theme', 'uit.label(self.box, {x})'),
        _s('theme callee alias', 'theme', 'f_{c} = ui_theme.fit\nf_{c}(self.status, {x})'),
        _s('theme **mapping', 'theme', "ui_theme.label(self.box, **{'text': {x}})"),
        _s('chip set keyword', 'chip set', 'self.state_chip.set(text={x})'),
        _s('chip set self binding', 'chip set', 'self.count_{c}.set({x})',
           methods=("def mk_{c}(self):\n    self.count_{c} = ui_theme.chip(self.box, '')",)),
        _s('chip set row item', 'chip set', "item['badge_{c}'].set({x})",
           methods=("def mk_{c}(self):\n    return {'badge_{c}': ui_theme.chip(self.box, '')}",)),
        _s('chip set local', 'chip set', "b_{c} = ui_theme.chip(self.box, '')\nb_{c}.set({x})"),
        _s('heading **mapping', 'heading', "self.tree.heading('k', **{'text': {x}})"),
        _s('wm_title', 'title', 'self.wm_title({x})'),
        _s('title string=', 'title', 'self.title(string={x})'),
        _s('set_empty text=', 'set_empty', 'self.tree.set_empty(text={x})'),
        _s('fill rows=', 'fill', "self.tree.fill(rows=[('i', ({x},))])"),
        _s('item values=', 'values', "self.tree.item('i', values=({x},))"),
        _s('textbox insert binding', 'textbox insert', "self.notes_{c}.insert('end', {x})",
           methods=('def mk_{c}(self):\n    self.notes_{c} = ui_theme.textbox(self.box)',)),
        _s('textbox insert ctk binding', 'textbox insert', "self.pad_{c}.insert('end', {x})",
           methods=('def mk_{c}(self):\n    self.pad_{c} = ctk.CTkTextbox(self.box)',)),
        _s('textbox insert text=', 'textbox insert', "self.log_box.insert('end', text={x})"),
        _s('textbox insert chars=', 'textbox insert', "self.log_text.insert('end', chars={x})"),
        _s('messagebox title', 'messagebox', 'messagebox.showinfo({x}, ' + T + ')'),
        _s('messagebox message=', 'messagebox', 'messagebox.showinfo(title=' + T + ', message={x})'),
        _s('messagebox title=', 'messagebox', 'messagebox.showinfo(title={x}, message=' + T + ')'),
        _s('messagebox detail=', 'messagebox', 'messagebox.showinfo(' + T + ', ' + T + ', detail={x})'),
        _s('messagebox from-import', 'messagebox', 'showinfo(' + T + ', {x})'),
        _s('messagebox module alias', 'messagebox', 'mb.showinfo(' + T + ', {x})'),
        _s('messagebox dotted', 'messagebox', 'tkinter.messagebox.showinfo(' + T + ', {x})'),
        _s('messagebox callee alias', 'messagebox', 'w_{c} = messagebox.showwarning\nw_{c}(' + T + ', {x})'),
        _s('messagebox starred', 'messagebox', 'messagebox.showinfo(*(' + T + ', {x}))'),
        _s('filedialog initialfile=', 'filedialog', 'filedialog.asksaveasfilename(parent=self, initialfile={x})'),
        _s('filedialog filetypes=', 'filedialog', "filedialog.askopenfilename(parent=self, filetypes=[({x}, '*.txt')])"),
        _s('t() mapping name', 't() mapping',
           "p_{c} = {'error': {x}}\nself.note.configure(text=i18n.t('library.unreadable', **p_{c}))"),
        _s('append_log line=', 'append_log', 'self.append_log(line={x})'),
        _s('append_log callee alias', 'append_log', 'l_{c} = self.append_log\nl_{c}({x})'),
        _s('append_log getattr', 'append_log', "getattr(self, 'append_log')({x})"),
        _s('append_log after', 'append_log', 'self.after(0, self.append_log, {x})'),
        _s('append_log partial', 'append_log', 'functools.partial(self.append_log, {x})()'),
        _s('show(value=)', 'show()', 'self.cache.append(messages.show(value={x}))'),
        _s('show bare', 'show()', 'self.cache.append(show({x}))'),
        _s('error_text()', 'show()', 'self.cache.append(messages.error_text({x}))'),
        _s('error_text(error=)', 'show()', 'self.cache.append(error_text(error={x}))'),
        _s('show callee alias', 'show()', 'r_{c} = messages.show\nself.cache.append(r_{c}({x}))'),
        _s('show at a strict sink by keyword', 'configure', 'self.status.configure(text=messages.show(value={x}))',
           shown=True),
        _s('sink method **param', 'sink method', 'self.say_{c}(text={x})',
           methods=("def say_{c}(self, **kw):\n    self.status.configure(text=kw['text'])",)),
        _s('sink method keyword-only', 'sink method', 'self.say_{c}(text={x})',
           methods=('def say_{c}(self, *, text):\n    self.status.configure(text=text)',)),
        _s('sink method *param', 'sink method', 'self.say_{c}({x})',
           methods=("def say_{c}(self, *parts):\n    self.status.configure(text=' '.join(parts))",)),
        _s('sink method starred argument', 'sink method', 'self.say_{c}(*[{x}])', methods=SAY),
        _s('sink method ** argument', 'sink method', "self.say_{c}(**{'text': {x}})", methods=SAY),
        _s('sink method keyword', 'sink method', 'self.say_{c}(text={x})', methods=SAY),
        _s('sink method after', 'sink method', 'self.after(0, self.say_{c}, {x})', methods=SAY),
        _s('sink method after_idle', 'sink method', 'self.after_idle(self.say_{c}, {x})', methods=SAY),
        _s('sink method partial', 'sink method', 'functools.partial(self.say_{c}, {x})()', methods=SAY),
        _s('sink method partial keyword', 'sink method', 'functools.partial(self.say_{c}, text={x})()', methods=SAY),
        _s('sink method Thread args', 'sink method', 'threading.Thread(target=self.say_{c}, args=({x},)).start()',
           methods=SAY),
        _s('sink method Thread kwargs', 'sink method',
           "threading.Thread(target=self.say_{c}, kwargs={'text': {x}}).start()", methods=SAY),
        _s('sink method module function', 'sink method', 'ui_log.put_{c}(self.status, {x})',
           other=('def put_{c}(widget, text):\n    widget.configure(text=text)',), needs='window'),
        _s('sink method lambda', 'sink method', 'p_{c} = lambda text: self.status.configure(text=text)\np_{c}({x})'),
        _s('sink method nested', 'sink method', 'def p_{c}(text):\n    self.status.configure(text=text)\np_{c}({x})'),
        _s('sink method callee alias', 'sink method', 'w_{c} = self.say_{c}\nw_{c}({x})', methods=SAY),
        _s('sink method getattr', 'sink method', "getattr(self, 'say_{c}')({x})", methods=SAY),
        _s('value sink method keyword', 'value sink method', 'self.note_{c}(text={x})', methods=NOTE),
        _s('value sink method **param', 'value sink method', 'self.note_{c}(text={x})',
           methods=("def note_{c}(self, **kw):\n    self.append_log(kw['text'])",)),
        _s('value sink method lambda', 'value sink method', 'p_{c} = lambda line: self.append_log(line)\np_{c}({x})'),
        _s('value sink method through show', 'value sink method', 'self.note_{c}({x})',
           methods=('def note_{c}(self, text):\n    self.status.configure(text=messages.show(text))',)),
    ]
    for name, kind, positional, keyword in THEME_SPELLINGS:
        if positional:
            found.append(_s(f'theme {name}', kind, positional))
        found.append(_s(f'theme {name} keyword', kind, keyword))
    for item in MENU_SPELLINGS:
        index = '0, ' if item.startswith(('insert_', 'entryconfig')) else ''
        found.append(_s(f'menu {item}', 'menu', f'self.menu.{item}({index}label={{x}})'))
    for function in MESSAGEBOX_SPELLINGS:
        found.append(_s(f'messagebox {function}', 'messagebox', f'messagebox.{function}({T}, {{x}}, parent=self)'))
    for function in FILEDIALOG_SPELLINGS:
        found.append(_s(f'filedialog {function}', 'filedialog', f'filedialog.{function}(parent=self, title={{x}})'))
    return tuple(found)


G5_SPELLINGS = _g5_spellings()

# G1b: the text slots of 7.2 / A6 / A15.4 ({x}: the value)
G1_SLOTS = OrderedDict([
    ("dict 'title'", "out_{c} = {'title': {x}}"),
    ("['title'] =", "page_{c} = {}\npage_{c}['title'] = {x}"),
    ('keyword title=', 'value.update(title={x})'),
    ('emit()', "emit('log', {x})"),
    ('msg param', "out_{c} = msg('core.engine.error', error={x})"),
    ('store writer', 'self.store.change_job(1, title={x})'),
    ('store writer record', "self.store.create_job('download', {x})"),
    ('raise', 'raise ValueError({x})'),
    ('raw(text)', 'out_{c} = raw({x})'),
    ('clauses(parts)', 'out_{c} = clauses([{x}])'),
])
G1_WRAPPED = ("dict 'title'", 'emit()')


def _g1_spellings():
    return (
        _s("['title'] +=", 'slot', "page_{c} = {'title': ''}\npage_{c}['title'] += {x}", composes=True),
        _s("setdefault('title')", 'slot', "page_{c} = {}\npage_{c}.setdefault('title', {x})"),
        _s('.title =', 'slot', 'value.title = {x}'),
        _s('.reason = in a tuple target', 'slot', "value.reason, value.state = {x}, 'done'"),
        _s('emit keyword', 'slot', "emit(kind='log', value={x})"),
        _s('emit callee alias', 'slot', "say_{c} = emit\nsay_{c}('log', {x})"),
        _s('self.emit()', 'slot', "self.emit('log', {x})"),
        _s('emit payload', 'slot', "emit('item_done', {'reason': {x}})"),
        _s('say()', 'slot', 'say({x})'),
        _s('unfinished.merge', 'slot', "unfinished.merge([{'error': {x}}])"),
        _s('unfinished.merge record', 'slot', 'unfinished.merge([{x}])'),
        _s('unfinished.save record', 'slot', 'unfinished.save([{x}])'),
        _s('raw(text=)', 'slot', 'out_{c} = raw(text={x})'),
        _s('cut(value)', 'slot', 'out_{c} = messages.cut({x}, 5)'),
        _s('masked(value)', 'slot', 'out_{c} = messages.masked({x}, [])'),
        _s('stripped(value)', 'slot', 'out_{c} = messages.stripped({x})'),
        _s('message_of(error)', 'slot', 'out_{c} = messages.message_of({x})'),
        _s('store writer keyword', 'slot', 'self.store.change_job(1, last_error={x})'),
        _s('finish_subscription', 'slot', 'self.store.finish_subscription(1, {x})'),
        _s('positional TEXT_KEYS parameter', 'slot', "add_{c}('u', {x}, True)",
           functions=("def add_{c}(url, label, flag):\n    return {'url': url, 'title': label}",)),
        _s('keyword TEXT_KEYS parameter', 'slot', "add_{c}('u', label={x}, flag=True)",
           functions=("def add_{c}(url, label, flag):\n    return {'url': url, 'title': label}",)),
        _s('other module parameter slot', 'slot', "naming.page_{c}('u', {x})",
           other=("def page_{c}(url, label):\n    return {'title': label, 'url': url}",)),
    )


G1_SPELLINGS = _g1_spellings()
SPELLING_FLOWS = ('direct', 'local name', 'conditional')     # the flows every spelling is crossed with


@dataclass
class Case:
    key: str
    guard: str                    # 'G5' or 'G1b'
    source: str
    flow: str
    sink: str
    bad: bool
    variant: str = ''             # depth / keyword / show / spelling
    forms: tuple = ()             # the contract ids it exercises
    mode: str = ''                # G5: 'strict' or 'value' (the mode of its sink kind)
    g1f: bool = False             # G1 with the event value: a G1f finding is expected (a composition, Q2)
    flows_bad: bool = None        # G5 with the event value: what the value rule alone flags (A16.5)
    method: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    module: list = field(default_factory=list)
    klass: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    other: list = field(default_factory=list)
    other_imports: list = field(default_factory=list)


def _indent(text, depth):
    return textwrap.indent(text, '    ' * depth)


def _render(case, source, flow, sink_template, wrap_show=False, depth_params=0, keyword=False, klass='LibraryUI',
            spelling=None):
    """Fill one case.  ``depth_params``: the sink stands in a chain of wrapper methods of this depth
    whose parameter ``text`` carries the value (A15.3 F10); 0: the sink stands in the case itself."""
    c = case.key
    other_module = 'naming' if klass == 'Runner' else 'ui_log'

    def fill(text):
        return text.replace('{c}', c).replace('{O}', other_module)
    s = fill(source.expr)
    sink_template = fill(sink_template)
    case.module += [fill(line) for line in source.module]
    case.other += [fill(line) for line in source.other]
    case.klass += [fill(line) for line in source.klass]
    case.imports += [fill(line) for line in source.imports]
    if spelling is not None:
        case.methods += [fill(item) for item in spelling.methods]
        case.module += [fill(item) for item in spelling.module]
        case.other += [fill(item) for item in spelling.other]
        case.functions += [fill(item) for item in spelling.functions]
    if 'constant' in flow.needs:                # a parameter default: the sink inside the wrapper
        value = 'messages.show(text)' if wrap_show else 'text'
        case.methods.append(f'def w_{c}(self, text={s}):\n' + _indent(sink_template.replace('{x}', value), 1))
        case.method.append(f'self.w_{c}()')
        return
    before, expr, depth, methods, functions, other, imports = flow.build(c, s, klass, other_module)
    case.methods += methods
    case.functions += functions
    case.other += other
    case.imports += imports
    if 'other' in flow.needs:                   # the value is made in the other module: its names too
        case.other += [fill(line) for line in source.module]
        case.other_imports += [fill(line) for line in source.imports]
    if wrap_show:
        expr = f'messages.show({expr})'
    if depth_params:
        # w_{c}_1 holds the sink, w_{c}_k passes ``text`` on, the case calls the outermost one
        case.methods.append(f'def w_{c}_1(self, text):\n' + _indent(sink_template.replace('{x}', 'text'), 1))
        for level in range(2, depth_params + 1):
            call = f'self.w_{c}_{level - 1}(text=text)' if keyword else f'self.w_{c}_{level - 1}(text)'
            case.methods.append(f'def w_{c}_{level}(self, text):\n    {call}')
        statement = f'self.w_{c}_{depth_params}(text={expr})' if keyword else f'self.w_{c}_{depth_params}({expr})'
    else:
        statement = sink_template.replace('{x}', expr)
    case.method += list(before) + [_indent(line, depth) for line in statement.split('\n')]


_CACHE = {}                       # the generated cases (deterministic; evaluation only reads them)


def event_bad(flow, mode, show) -> bool:
    """Where an event value (no source, opaque to the flows) must be flagged: as a part of a
    composition that flattens a message (VALUE_COMPOSITIONS: at every sink, show() included), and at a
    strict sink that does not get it through show() or a language-neutral wrapper (F11)."""
    if flow.composes == 'value':
        return True
    if show:
        return False
    return mode == 'strict' and not flow.neutral


# the G1b slots whose value is a typed call's text argument (G1f does not apply there, A1.1)
G1F_EXEMPT = frozenset({'raw(text)', 'clauses(parts)', 'raw(text=)', 'cut(value)', 'masked(value)', 'stripped(value)',
                        'message_of(error)'})


def g5_cases(modes=None) -> list:
    """Every G5 combination: each source through each flow into each sink kind (a wrapper's
    parameter at depth 1 for the two sink-method kinds); then for each sink kind and source a
    rotating flow at wrapper depth 2 and 3, positionally and by keyword, with and without show(), and
    each source straight into show() at the sink; then every sink spelling (A16.1) with each source
    through SPELLING_FLOWS.  A flow whose display text the 7.9 grammar cannot prove at a strict sink
    (strict_good False) gets no good case there."""
    key = ('G5', tuple(sorted((modes or {}).items())))
    if key in _CACHE:
        return list(_CACHE[key])
    cases = []
    if modes is None:
        import i18n_tool  # noqa: PLC0415  (the scanner's table of sink kinds, A15.4)
        modes = dict(i18n_tool.SINKS)

    def new(source, flow, sink, variant='', forms=(), kind=None, show=False):
        prefix = 'l' if sink == 'library filetext t()' else 'c'
        mode = modes.get(kind or sink, 'value')
        bad = source.bad if source.bad is not None else event_bad(flow, mode, show)
        case = Case(f'{prefix}{len(cases) + 1}', 'G5', source.name, flow.name, sink, bad, variant,
                    tuple(dict.fromkeys(tuple(source.forms) + tuple(flow.forms) + tuple(forms))), mode)
        if source.bad is None:          # the value rule alone flags only a composition at a value position
            case.flows_bad = flow.composes == 'value' and (mode == 'value' or show)
        cases.append(case)
        return case

    def wanted(flow, source, kind):
        return source.bad is not False or flow.strict_good or modes.get(kind) != 'strict'

    for sink, template in G5_SINKS.items():
        klass = 'Archive' if sink == 'library filetext t()' else 'LibraryUI'
        for flow in FLOWS:
            for source in G5_SOURCES:
                if source.light and flow.name not in SPELLING_FLOWS:
                    continue
                if not applicable(flow, source, klass) or klass == 'Archive' and source.selfish \
                        or not wanted(flow, source, sink):
                    continue
                depth = 1 if sink in WRAPPED and 'constant' not in flow.needs else 0
                case = new(source, flow, sink, 'depth 1' if depth else '',
                           ('A15.4 ' + sink,) + (('F10 positional',) if depth else ()))
                _render(case, source, flow, template, depth_params=depth, klass=klass)
        if sink == 'library filetext t()':
            continue
        for index, source in enumerate(G5_SOURCES):
            flow = ROTATE[index % len(ROTATE)]
            if not wanted(flow, source, sink) or source.light:
                flow = FLOWS[0]
            for depth in (2, 3):
                for keyword in (False, True):
                    for show in (False, True):
                        variant = f"depth {depth}{' keyword' if keyword else ''}{' show' if show else ''}"
                        forms = ('A15.4 ' + sink, f'F10 depth {depth}', 'F10 keyword' if keyword else 'F10 positional')
                        case = new(source, flow, sink, variant, forms + (('F11 show()',) if show else ()), show=show)
                        _render(case, source, flow, template, wrap_show=show, depth_params=depth, keyword=keyword)
            case = new(source, FLOWS[0], sink, 'show', ('A15.4 ' + sink, 'F11 show()'), show=True)
            _render(case, source, FLOWS[0], template, wrap_show=True)
    rotate = [flow for flow in FLOWS if flow.name in SPELLING_FLOWS]
    for spelling in G5_SPELLINGS:
        for source in G5_SOURCES:
            for flow in rotate:
                if not applicable(flow, source) or not wanted(flow, source, spelling.kind):
                    continue
                case = new(source, flow, spelling.kind, 'spelling ' + spelling.name, spelling.forms, spelling.kind,
                           show=spelling.shown)
                _render(case, source, flow, spelling.template, spelling=spelling)
    _CACHE[key] = tuple(cases)
    return cases


def g1_cases() -> list:
    """Every G1b combination: each source through each flow into each slot class; then for two slot
    classes each source through a rotating flow at wrapper depth 1, 2 and 3, positionally and by
    keyword; then every slot spelling (A16.4) with each source through SPELLING_FLOWS."""
    if 'G1b' in _CACHE:
        return list(_CACHE['G1b'])
    cases = []

    def new(source, flow, slot, variant='', forms=(), composes=False):
        case = Case(f'g{len(cases) + 1}', 'G1b', source.name, flow.name, slot, source.bad, variant,
                    tuple(dict.fromkeys(tuple(source.forms) + tuple(flow.forms) + tuple(forms))))
        # G1f (7.2, A15.4, A16.4): a composition of the event value standing in a text slot of a core
        # module; not in the text argument of a typed call (A1.1)
        case.g1f = source.name == 'event value' and (flow.composes in ('value', 'g1f') or composes) \
            and slot.split(' ', 1)[-1] not in G1F_EXEMPT and variant.split(' ', 1)[-1] not in G1F_EXEMPT
        cases.append(case)
        return case

    for slot, template in G1_SLOTS.items():
        for flow in FLOWS:
            for source in G1_SOURCES:
                if source.light and flow.name not in SPELLING_FLOWS:
                    continue
                if applicable(flow, source, 'Runner'):
                    _render(new(source, flow, slot, '', ('A15.4 G1b ' + slot,)), source, flow, template, klass='Runner')
    for slot in G1_WRAPPED:
        for index, source in enumerate(G1_SOURCES):
            flow = FLOWS[0] if source.light else ROTATE[index % len(ROTATE)]
            for depth in (1, 2, 3):
                for keyword in (False, True):
                    forms = ('A15.4 G1b ' + slot, f'F10 depth {depth}' if depth > 1 else 'F10 positional',
                             'F10 keyword' if keyword else 'F10 positional')
                    case = new(source, flow, slot, f"depth {depth}{' keyword' if keyword else ''}", forms)
                    _render(case, source, flow, G1_SLOTS[slot], depth_params=depth, keyword=keyword, klass='Runner')
    rotate = [flow for flow in FLOWS if flow.name in SPELLING_FLOWS]
    for spelling in G1_SPELLINGS:
        for source in G1_SOURCES:
            for flow in rotate:
                if applicable(flow, source, 'Runner'):
                    case = new(source, flow, 'spelling', 'spelling ' + spelling.name, ('A16.4 ' + spelling.name,),
                               spelling.composes)
                    _render(case, source, flow, spelling.template, klass='Runner', spelling=spelling)
    _CACHE['G1b'] = tuple(cases)
    return cases

# ----------------------------------------------------------------------------------- the reviewers' probe forms

# (final review round 3, rv-final-en-3 probe_r3): method names say flag_* (a finding) or ok_* (none)
PROBE_UI_LOG = '''
import i18n
import messages

ZH_START = '采集完成'


class LogUI:
    def append_log(self, line):
        line = messages.show(line)
        self.log_buffer.append(line)

    def flag_cross_module_self_attribute_log(self):
        self.append_log(self.banner)

    def flag_cross_module_self_attribute_show(self):
        self.status.configure(text=messages.show(self.banner))
'''

PROBE_LIBRARY_UI = '''
import functools
import i18n
import messages
import ui_log
from tkinter import messagebox
from messages import show

NO_PENDING = '这个订阅目前没有待处理的新增内容。'
OTHER = '请先在下方列表中选择一条资料。'
NAMES = ('全部', '视频')
TABLE = {'a': '视频页面'}
CURRENT = None


class LibraryUI:
    HEADER = '资料库'

    def setup(self):
        self.banner = NO_PENDING

    def flag_r2_local_alias(self):
        note = NO_PENDING
        self.append_log(note)

    def flag_r2_ifexp(self, ok):
        self.append_log(NO_PENDING if ok else OTHER)

    def flag_r2_or(self, text):
        self.append_log(text or NO_PENDING)

    def flag_r2_subscript(self):
        self.append_log(NAMES[0])

    def flag_r2_get(self):
        self.append_log(TABLE.get('a'))

    def flag_r2_class_attribute(self):
        self.append_log(self.HEADER)

    def picked(self):
        return NO_PENDING

    def flag_r2_method_return(self):
        self.append_log(self.picked())

    def flag_r2_other_module(self):
        self.append_log(ui_log.ZH_START)

    def flag_r2_default_hint(self, text=NO_PENDING):
        self.note.configure(text=text)

    def flag_r2_show_constant(self):
        messagebox.showinfo(i18n.t('library.no_pending'), messages.show(NO_PENDING), parent=self)

    def flag_r2_show_bare(self):
        messagebox.showinfo(i18n.t('library.no_pending'), show(NO_PENDING), parent=self)

    def note(self, text):
        self.append_log(text)

    def flag_wrapper_to_append_log(self):
        self.note(NO_PENDING)

    def hint_shown(self, text):
        self.status.configure(text=messages.show(text))

    def flag_wrapper_through_show(self):
        self.hint_shown(NO_PENDING)

    def dialog_shown(self, text):
        messagebox.showinfo(i18n.t('library.no_pending'), messages.show(text), parent=self)

    def flag_wrapper_dialog_through_show(self):
        self.dialog_shown(OTHER)

    def flag_param_rebound_strict(self, text=None):
        text = text or NO_PENDING
        self.status.configure(text=text)

    def flag_param_rebound_log(self, text=None):
        text = text or NO_PENDING
        self.append_log(text)

    def flag_keyword_append_log(self):
        self.append_log(line=NO_PENDING)

    def flag_keyword_append_log_composed(self, value):
        self.append_log(line=f"{value['title']} {value['url']}")

    def flag_after_callback(self):
        self.after(0, self.append_log, NO_PENDING)

    def flag_partial_dialog(self):
        functools.partial(messagebox.showinfo, i18n.t('library.no_pending'), NO_PENDING)()

    def flag_class_name_attribute(self):
        self.append_log(LibraryUI.HEADER)

    def flag_getattr(self):
        self.append_log(getattr(self, 'HEADER'))

    def flag_lambda(self):
        make = lambda: NO_PENDING  # noqa: E731
        self.append_log(make())

    def flag_global(self):
        global CURRENT
        CURRENT = NO_PENDING
        self.append_log(CURRENT)

    def flag_comprehension(self):
        [self.append_log(name) for name in NAMES]

    def flag_values_loop(self):
        for label in TABLE.values():
            self.append_log(label)

    def flag_format_in_show(self):
        self.status.configure(text=messages.show('{}'.format(NO_PENDING)))

    def setup_pair(self):
        self.pair_note, self.pair_tone = NO_PENDING, 'warn'

    def flag_tuple_target_self_attribute_log(self):
        self.append_log(self.pair_note)

    def flag_tuple_target_self_attribute_show(self):
        self.status.configure(text=messages.show(self.pair_note))

    def handle(self, kind, value):
        if kind == 'status':
            self.status.configure(text=messages.show(value))

    def flag_own_event_path(self):
        self.handle('status', NO_PENDING)

    def ok_event(self, value):
        self.append_log(value['line'])

    def ok_t(self):
        self.append_log(i18n.t('library.no_pending'))

    def ok_show_event(self, value):
        self.status.configure(text=messages.show(value['title']))
'''

# (final review round 3, rv-final-quality-3 probe_enum / probe_enum3 / probe_q35): an enum source
# through enumerate / zip / map / filter / dict / set / sorted / reversed / min / max / list / tuple,
# {**X}, a dict comprehension, a starred element, .pop / .setdefault / .popitem; E* / N* must be G1b,
# C* must not
PROBE_G1 = '''
import formats
from messages import msg, raw

NAMING = ('title', 'clean')
LABELS = {'title': '标题（推荐）', 'clean': '精简标题'}


def probe(value):
    for i, preset in enumerate(formats.PRESETS):
        a = msg('core.engine.error', error=preset.label)          # E22 enumerate
    for k, preset in zip(range(3), formats.PRESETS):
        b = msg('core.engine.error', error=preset.label)          # E23 zip
    for i, mode in enumerate(formats.MODES):
        c = {'reason': mode}                                      # E26 enumerate MODES
    for i, v1 in enumerate(LABELS.values()):
        d = {'title': v1}                                         # E01 enumerate(values)
    for k, v4 in zip(NAMING, LABELS.values()):
        e = {'title': v4}                                         # E04 zip
    for i, (k, v5) in enumerate(LABELS.items()):
        f = {'title': v5}                                         # E05 enumerate(items)
    vals = list(LABELS.values())
    for i, v6 in enumerate(vals):
        g = {'title': v6}                                         # E06 enumerate(list)
    for v7 in sorted(LABELS.values()):
        h = raw(v7)                                               # E07 sorted
    d1 = dict(LABELS)
    i = {'title': d1['title']}                                    # N01 dict(LABELS)
    d2 = {**LABELS}
    j = {'title': d2['title']}                                    # N02 {**LABELS}
    d3 = {k: v for k, v in LABELS.items()}
    k_ = {'title': d3['title']}                                   # N03 dict comprehension
    l_ = {'title': [*LABELS.values()][0]}                         # N05 starred list
    m = {'title': LABELS.setdefault('title', '')}                 # N06 setdefault
    n = {'title': LABELS.pop('title')}                            # N07 pop
    o = {'title': max(LABELS.values())}                           # N08 max
    p = {'title': LABELS.popitem()[1]}                            # N10 popitem
    q = {'title': next(map(str, LABELS.values()))}                # N11 map
    r = {'title': next(filter(None, LABELS.values()))}            # N12 filter
    s = {'title': next(reversed(list(LABELS.values())))}          # N13 reversed
    t = {'title': next(iter(set(LABELS.values())))}              # N14 set
    for i, v3 in enumerate(NAMING):
        u = {'title': v3}                                         # C03 NAMING ids
    keys = [{'title': key} for key, label in LABELS.items()]      # C12 the key of a pair
    return a, b, c, d, e, f, g, h, i, j, k_, l_, m, n, o, p, q, r, s, t, u, keys
'''
PROBE_G1_ALLOWED = ('标题（推荐）', '精简标题')


# (final review round 4 = Phase D, rv-conv-zh-1 / rv-conv-en-1 / rv-conv-quality-1): the probe forms the
# reviewers found missed, each a flag_* method (a finding inside its own lines), with ok_* controls
PROBE_D_UI_LOG = '''
import i18n
import messages

ZH_START = '采集完成'


def put_text(widget, text):
    widget.configure(text=text)


class LogUI:
    def append_log(self, line):
        line = messages.show(line)
        self.log_buffer.append(line)

    def setup_cross(self):
        self.cross_items = []
        self.cross_items.append(ZH_START)
'''

PROBE_D_LIBRARY_UI = """
import copy
import functools
import os
import re
import textwrap
from pathlib import Path
import customtkinter
import customtkinter as ck
import customtkinter as ctk
from customtkinter import CTkLabel
import i18n
import messages
import ui_theme
from ui_theme import label as theme_label, fit as theme_fit
import ui_log
from tkinter import messagebox
from tkinter.messagebox import showinfo
from messages import show, error_text

NO_PENDING = '这个订阅目前没有待处理的新增内容。'
OTHER = '请先在下方列表中选择一条资料。'
ENGLISH = 'A fixed English text of the module.'
SEP = '-'
NAMES = ('全部', '视频')
CURRENT = None


class LibraryUI:
    HEADER = '资料库'

    @property
    def banner(self):
        return NO_PENDING

    def flag_k01_show_keyword_strict(self):
        self.status.configure(text=messages.show(value=NO_PENDING))

    def flag_k02_show_keyword_value(self):
        self.append_log(messages.show(value=NO_PENDING))

    def flag_k03_show_keyword_bare(self):
        self.status.configure(text=show(value=NO_PENDING))

    def flag_k04_error_text_keyword(self):
        messagebox.showerror(i18n.t('dialog.save_failed'), error_text(error=NO_PENDING), parent=self)

    def flag_k05_show_keyword_english_constant(self):
        self.status.configure(text=messages.show(value=ENGLISH))

    def flag_k06_show_keyword_t_param(self):
        self.append_log(i18n.t('log.collect.page', title=messages.show(value=NO_PENDING), url='u'))

    def flag_k07_set_empty_keyword(self):
        self.tree.set_empty(text=NO_PENDING)

    def flag_k08_chip_set_keyword(self):
        self.status_chip.set(text=NO_PENDING)

    def flag_k09_title_keyword(self):
        self.title(string=NO_PENDING)

    def flag_k10_textbox_insert_chars(self):
        self.log_text.insert('end', chars=NO_PENDING)

    def flag_k11_fill_keyword(self):
        self.tree.fill(rows=[('1', (NO_PENDING,))])

    def flag_k13_textbox_insert_text(self):
        self.log_box.insert('end', text=NO_PENDING)

    def flag_a01_alias_append_log(self):
        log = self.append_log
        log(NO_PENDING)

    def flag_a02_alias_configure(self):
        configure = self.status.configure
        configure(text=NO_PENDING)

    def flag_a03_alias_messagebox(self):
        warn = messagebox.showwarning
        warn(i18n.t('dialog.save_failed'), NO_PENDING, parent=self)

    def flag_a05_alias_fit(self):
        fit = ui_theme.fit
        fit(self.status, NO_PENDING)

    def flag_a06_getattr_method(self):
        getattr(self, 'append_log')(NO_PENDING)

    def flag_t01_textwrap_value(self):
        self.append_log(textwrap.shorten(NO_PENDING, 30))

    def flag_t02_textwrap_show(self):
        self.status.configure(text=messages.show(textwrap.shorten(NO_PENDING, width=30)))

    def flag_t03_textwrap_event(self, value):
        self.append_log(textwrap.shorten(value['line'], 30))

    def flag_p01_property_value(self):
        self.append_log(self.banner)

    def flag_p02_property_show(self):
        self.status.configure(text=messages.show(self.banner))

    def flag_p03_copy(self):
        self.append_log(copy.copy(NO_PENDING))

    def flag_p04_dict_of_lambdas(self):
        self.append_log({'a': lambda: NO_PENDING}['a']())

    def flag_p05_str_unbound(self):
        self.append_log(str.strip(NO_PENDING))

    def set_current(self):
        global CURRENT
        CURRENT = NO_PENDING

    def flag_g01_global_other_function_log(self):
        self.append_log(CURRENT)

    def flag_g02_nonlocal(self):
        v = ''

        def inner():
            nonlocal v
            v = NO_PENDING
        inner()
        self.status.configure(text=v)

    def set_neutral(self):
        self.version = NO_PENDING
        self.folder = OTHER

    def flag_n01_neutral_attribute(self):
        self.status.configure(text=self.version)

    def flag_n02_neutral_attribute_folder(self):
        self.status.configure(text=self.folder)

    def flag_n03_neutral_key(self):
        self.status.configure(text={'url': NO_PENDING}['url'])

    def flag_n04_neutral_get(self):
        row = {'version': NO_PENDING}
        self.status.configure(text=row.get('version'))

    def flag_n05_path_name(self):
        self.status.configure(text=Path(NO_PENDING).name)

    def flag_m01_mult_strict(self):
        self.status.configure(text=NO_PENDING * 1)

    def flag_m02_mult_english(self, rows):
        self.status.configure(text=SEP * len(rows))

    def flag_m03_mult_dialog(self):
        messagebox.showinfo(i18n.t('library.no_pending'), self.HEADER * 1, parent=self)

    def flag_m04_mult_event(self, value):
        self.status.configure(text=value['title'] * 1)

    def flag_v01_augmented(self, value):
        line = i18n.t('library.no_pending')
        line += value['title']
        self.append_log(line)

    def flag_v02_augmented_self(self, value):
        self.line_v02 = i18n.t('library.no_pending')
        self.line_v02 += value['title']
        self.append_log(self.line_v02)

    def flag_v03_augmented_item(self, value):
        parts = {'k': i18n.t('library.no_pending')}
        parts['k'] += value['title']
        self.append_log(parts['k'])

    def flag_i01_customtkinter_label(self):
        customtkinter.CTkLabel(self, text=NO_PENDING)

    def flag_i02_ctk_values_alias(self):
        ck.CTkOptionMenu(self, values=[NO_PENDING])

    def flag_i03_bare_ctk_label(self):
        CTkLabel(self, text=NO_PENDING)

    def flag_i04_theme_bare(self):
        theme_label(self, NO_PENDING)

    def flag_i05_theme_bare_fit(self):
        theme_fit(self.status, NO_PENDING)

    def flag_i06_messagebox_bare(self):
        showinfo('t', NO_PENDING, parent=self)

    def build_chips(self):
        self.link_count = ui_theme.chip(self, '', 'info')

    def flag_c01_chip_set_real_name(self):
        self.link_count.set(NO_PENDING)

    def build_row(self):
        badge = ui_theme.chip(self, '', 'idle')
        return {'badge': badge}

    def flag_c02_chip_set_row_badge(self, row):
        row['badge'].set(NO_PENDING, 'idle')

    def build_notes(self):
        self.notes = ui_theme.textbox(self)

    def flag_c03_textbox_insert_binding(self):
        self.notes.insert('end', NO_PENDING)

    def make_label(self, **kw):
        return ctk.CTkLabel(self, **kw)

    def flag_s01_constructor_kwargs_spread(self):
        self.make_label(text=NO_PENDING)

    def flag_s02_configure_mapping_display(self):
        self.status.configure(**{'text': NO_PENDING})

    def flag_s03_configure_mapping_name(self, active):
        options = {'fg_color': 'red' if active else 'blue'}
        options['text'] = NO_PENDING
        self.status.configure(**options)

    def flag_s04_mapping_event(self, value):
        self.status.configure(**{'text': value['title']})

    def flag_s05_constructor_kw_name(self, parent):
        kw = {'text': NO_PENDING}
        ctk.CTkLabel(parent, **kw)

    def w_kwarg(self, **kw):
        self.status.configure(text=kw['text'])

    def flag_s06_kwarg_wrapper(self):
        self.w_kwarg(text=NO_PENDING)

    def ok_k12_show_keyword_t(self):
        self.status.configure(text=messages.show(value=i18n.t('library.no_pending')))

    def ok_s07_mapping_shown(self, value):
        self.status.configure(**{'text': messages.show(value['title'])})

    def ok_m05_mult_literal(self, rows):
        self.status.configure(text='-' * len(rows))

    def ok_c04_chip_t(self):
        self.link_count.set(i18n.t('download.link_count', count=0))

    def ok_t04_textwrap_t(self):
        self.status.configure(text=textwrap.shorten(i18n.t('library.no_pending'), 30))
"""

# G1b and G1f probe lines (collector, a core module): `# D<n> <rule>` must give that rule's finding on
# its line, `# K<n>` none
PROBE_D_G1 = '''
import contextlib
import textwrap
import formats
import messages
from messages import msg, raw

KIND = '媒体地址'
CURRENT = None


def emit(kind, value):
    return kind, value


def set_current():
    global CURRENT
    CURRENT = KIND


def probe(value, item, a, b, preset):
    k1 = {'title': textwrap.shorten(KIND, 10)}                    # D01 G1b
    k2 = {'title': CURRENT}                                       # D02 G1b
    v = ''

    def inner():
        nonlocal v
        v = KIND
    inner()
    k3 = {'title': v}                                             # D03 G1b
    page = {}
    page.setdefault('title', KIND)                                # D04 G1b
    item.reason = KIND                                            # D05 G1b
    item.title, item.state = KIND, 'done'                         # D06 G1b
    k4 = {'title': a * 2}                                         # D07 G1f
    k5 = {'title': b.strip()}                                     # D08 G1f
    text = a
    text += b                                                     # D09 G1f
    k6 = msg('core.engine.error', error=text)
    item.error = f'{a}: {b}'                                      # D10 G1f
    kind = ''
    kind += KIND
    k7 = {'title': kind}                                          # D11 G1b
    k8 = {'title': msg('core.reason.network')}                    # K01
    k9 = {'title': raw(value['title'])}                           # K02
    item.reason = raw(value['reason'])                            # K03
    k10 = {'title': len(value)}                                   # K04
    return k1, k2, k3, k4, k5, k6, k7, k8, k9, k10


class Runner:
    @property
    def prop_kind(self):
        return KIND

    def flag_property(self):
        return {'title': self.prop_kind}                          # D12 G1b
'''

# ----------------------------------------------------------------------------------- the modules

G5_HEADER = '''import copy
import functools
import os
import re
import textwrap
import threading
from pathlib import Path
import customtkinter
import customtkinter as ck
import customtkinter as ctk
from customtkinter import CTkLabel
import tkinter
import tkinter as tk
from tkinter import ttk
import tkinter.messagebox as mb
import i18n
import messages
import ui_theme
import ui_theme as uit
from ui_theme import fit as theme_fit
import ui_log
import formats
import urls
from tkinter import filedialog, messagebox
from tkinter.messagebox import showinfo
from messages import show, error_text
'''
G1_HEADER = '''import copy
import os
import re
import textwrap
from pathlib import Path
import formats
import messages
import naming
import urls
from messages import msg, raw, clauses


def emit(kind, value):
    return kind, value
'''
G1_OTHER_HEADER = '''import copy
import os
import re
import textwrap
from pathlib import Path
import formats
import messages
import naming
import urls
from messages import msg, raw
'''
FORMATS = '''from collections import namedtuple

Preset = namedtuple('Preset', 'id label')
PRESETS = (Preset('mkv', 'MKV · 保留源画质'),)
MODES = ('音频 · 原始最佳',)
'''
FORMATS_ALLOWED = ('MKV · 保留源画质', '音频 · 原始最佳')
G1_ALLOWED = ('媒体地址',)
UI_LOG_CLASS = '''class LogUI:
    def append_log(self, line):
        self.log_buffer.append(messages.show(line))
'''


def _module(header, cases, class_name):
    """The text of a synthetic module and, per case, the line numbers it occupies."""
    lines = header.rstrip('\n').split('\n')
    for case in cases:
        lines += case.imports
    spans = {}
    for case in cases:
        start = len(lines) + 1
        lines += case.module
        for function in case.functions:
            lines += [''] + function.split('\n')
        spans.setdefault(case.key, set()).update(range(start, len(lines) + 1))
    lines += ['', '', f'class {class_name}:', '    pass']
    for case in cases:
        start = len(lines) + 1
        for line in case.klass:
            lines.append('    ' + line)
        for method in case.methods:
            lines += [''] + ['    ' + line for line in method.split('\n')]
        lines += ['', f'    def case_{case.key}(self, {PARAMS}):']
        for line in case.method or ['pass']:
            lines += ['        ' + part for part in line.split('\n')]
        spans[case.key].update(range(start, len(lines) + 1))
    return '\n'.join(lines) + '\n', spans


def _other_module(cases, header=G5_HEADER, class_text=UI_LOG_CLASS):
    """The other scanned module (G5: ui_log, the window's other mixin with the value sink append_log;
    G1: naming, another src module), with each case's module lines, functions and methods for the
    cross-module sources and flows."""
    lines = header.rstrip('\n').split('\n')
    for case in cases:
        lines += [line for line in case.other_imports if line not in lines]
    lines.append('')
    spans = {}
    for case in cases:
        for line in case.other:
            if not line.startswith('def '):
                lines.append(line)
                spans.setdefault(case.key, set()).add(len(lines))
    for case in cases:
        for line in case.other:
            if line.startswith('def ') and '(self' not in line.split('\n', 1)[0]:
                start = len(lines) + 2
                lines += [''] + line.split('\n')
                spans.setdefault(case.key, set()).update(range(start, len(lines) + 1))
    lines += ['', ''] + class_text.rstrip('\n').split('\n')
    for case in cases:
        for line in case.other:
            if line.startswith('def ') and '(self' in line.split('\n', 1)[0]:
                start = len(lines) + 2
                lines += [''] + ['    ' + part for part in line.split('\n')]
                spans.setdefault(case.key, set()).update(range(start, len(lines) + 1))
    return '\n'.join(lines) + '\n', spans


def write_g5(folder, cases):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    window = [case for case in cases if case.sink != 'library filetext t()']
    archive = [case for case in cases if case.sink == 'library filetext t()']
    text, spans = _module(G5_HEADER, window, 'LibraryUI')
    (folder / 'library_ui.py').write_text(text, encoding='utf-8')
    other, other_spans = _other_module(window + archive)    # the archive cases' other-module names too
    (folder / 'ui_log.py').write_text(other, encoding='utf-8')
    (folder / 'formats.py').write_text(FORMATS, encoding='utf-8')
    library_text, library_spans = _module(G5_HEADER, archive, 'Archive')
    (folder / 'library.py').write_text(library_text, encoding='utf-8')
    return {'library_ui.py': spans, 'ui_log.py': other_spans, 'library.py': library_spans}


def write_g1(folder, cases):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    text, spans = _module(G1_HEADER, cases, 'Runner')
    (folder / 'collector.py').write_text(text, encoding='utf-8')
    other, other_spans = _other_module(cases, G1_OTHER_HEADER, 'class Helper:\n    pass\n')
    (folder / 'naming.py').write_text(other, encoding='utf-8')
    (folder / 'formats.py').write_text(FORMATS, encoding='utf-8')
    return {'collector.py': spans, 'naming.py': other_spans}

# ----------------------------------------------------------------------------------- evaluation


def _context(tool, folder):
    context = tool.GuardContext(folder, enum_src=folder)
    for text in FORMATS_ALLOWED:
        context.allow('formats')['literals'][text] = {'category': 'internal', 'reason': 'contract self-test'}
    for text in G1_ALLOWED + PROBE_G1_ALLOWED:
        context.allow('collector')['literals'][text] = {'category': 'internal', 'reason': 'contract self-test'}
        context.allow('naming')['literals'][text] = {'category': 'internal', 'reason': 'contract self-test'}
    context._enum = None
    return context


def _judge(cases, spans, findings):
    """{case key: flagged?} by the finding lines that fall into each case's lines."""
    flagged = {case.key: False for case in cases}
    for item in findings:
        for key, lines in spans.get(item['file'], {}).items():
            if item['line'] in lines and key in flagged:
                flagged[key] = True
    return flagged


CHUNK = 240                       # cases per synthetic module set (the cases are independent)


def _chunks(cases, size=CHUNK):
    for start in range(0, len(cases), size):
        yield start // size, cases[start:start + size]


def evaluate(tool, folder, which=('G5', 'G1b'), chunk=CHUNK, select=None, judgements=False):
    """Run the scanner of ``tool`` (an i18n_tool module) over every generated case (those ``select``
    accepts), ``chunk`` cases per synthetic module set; returns {'G5': [(case, flagged)], 'G1b': [...]}
    and, with ``judgements`` (A16.5), {'G5 grammar': ..., 'G5 flows': ...}: each strict sink judged by
    the 7.9 grammar alone and by the flows' value rule alone (value sinks by the value rule)."""
    folder = Path(folder)
    results = {}
    if 'G5' in which:
        results['G5'] = []
        if judgements:
            results['G5 grammar'], results['G5 flows'] = [], []
        cases = [case for case in g5_cases(dict(tool.SINKS)) if select is None or select(case)]
        for number, part in _chunks(cases, chunk):
            where = folder / f'g5-{number}'
            spans = write_g5(where, part)
            context = _context(tool, where)
            sinks = tool._Sinks(context, ['ui_log', 'library_ui'], folder=where)
            if judgements:
                sinks.judgements = []
            findings = sinks.run(allow_for=lambda name: {'sinks': {}})
            findings += tool._library_sinks(context, where, allow={})
            flagged = _judge(part, spans, findings)
            results['G5'] += [(case, flagged[case.key]) for case in part]
            if judgements:
                for label, index in (('G5 grammar', 2), ('G5 flows', 3)):
                    failed = [{'file': item[0], 'line': item[1]} for item in sinks.judgements if not item[index]]
                    alone = _judge(part, spans, failed)
                    results[label] += [(case, alone[case.key]) for case in part if case.sink != 'library filetext t()']
    if 'G1b' in which:
        results['G1b'], results['G1f'] = [], []
        cases = [case for case in g1_cases() if select is None or select(case)]
        for number, part in _chunks(cases, chunk):
            where = folder / f'g1-{number}'
            spans = write_g1(where, part)
            context = _context(tool, where)
            findings = tool.g1_module(context, 'collector', where)
            flagged = _judge(part, spans, [item for item in findings if item['rule'] == 'G1b'])
            results['G1b'] += [(case, flagged[case.key]) for case in part]
            # G1f (Q2): the event value's cases, judged by the G1f findings inside their lines
            events = [case for case in part if case.source == 'event value']
            composed = _judge(events, spans, [item for item in findings if item['rule'] == 'G1f'])
            results['G1f'] += [(case, composed[case.key]) for case in events]
    return results


def probes(tool, folder) -> OrderedDict:
    """The reviewers' probe forms: {name: flagged?} for G5 (flag_* / ok_* methods) and G1b (E* / N* /
    C* lines) of round 3, and of round 4 (Phase D) the G5 methods and the G1 lines D<n> <rule> /
    K<n>."""
    import ast
    folder = Path(folder)
    found = OrderedDict()
    for label, ui_log_text, library_text in (('probe-g5', PROBE_UI_LOG, PROBE_LIBRARY_UI),
                                             ('probe-d-g5', PROBE_D_UI_LOG, PROBE_D_LIBRARY_UI)):
        g5 = folder / label
        g5.mkdir(parents=True, exist_ok=True)
        (g5 / 'ui_log.py').write_text(textwrap.dedent(ui_log_text).lstrip('\n'), encoding='utf-8')
        (g5 / 'library_ui.py').write_text(textwrap.dedent(library_text).lstrip('\n'), encoding='utf-8')
        context = tool.GuardContext(g5)
        findings = tool._Sinks(context, ['ui_log', 'library_ui'], folder=g5).run(allow_for=lambda name: {'sinks': {}})
        prefix = 'G5' if label == 'probe-g5' else 'G5 D'
        for module in ('ui_log', 'library_ui'):
            tree = ast.parse((g5 / f'{module}.py').read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith(('flag_', 'ok_')):
                    lines = set(range(node.lineno, node.end_lineno + 1))
                    found[f'{prefix} {node.name}'] = any(item['file'] == f'{module}.py' and item['line'] in lines
                                                         for item in findings)
    g1 = folder / 'probe-g1'
    g1.mkdir(parents=True, exist_ok=True)
    (g1 / 'naming.py').write_text(textwrap.dedent(PROBE_G1).lstrip('\n'), encoding='utf-8')
    (g1 / 'formats.py').write_text(FORMATS, encoding='utf-8')
    context = _context(tool, g1)
    findings = [item for item in tool.g1_module(context, 'naming', g1) if item['rule'] == 'G1b']
    for number, line in enumerate((g1 / 'naming.py').read_text(encoding='utf-8').splitlines(), 1):
        if '# ' in line and line.split('# ')[-1][:1] in 'ENC' and line.split('# ')[-1][1:3].isdigit():
            label = line.split('# ')[-1]
            found[f'G1b {label}'] = any(item['line'] == number for item in findings)
    g1d = folder / 'probe-d-g1'
    g1d.mkdir(parents=True, exist_ok=True)
    (g1d / 'collector.py').write_text(textwrap.dedent(PROBE_D_G1).lstrip('\n'), encoding='utf-8')
    (g1d / 'formats.py').write_text(FORMATS, encoding='utf-8')
    context = _context(tool, g1d)
    findings = tool.g1_module(context, 'collector', g1d)
    for number, line in enumerate((g1d / 'collector.py').read_text(encoding='utf-8').splitlines(), 1):
        label = line.split('# ')[-1] if '# ' in line else ''
        if label[:1] in 'DK' and label[1:3].isdigit():
            rules = {'G1b', 'G1f'} if label[:1] == 'K' else {label.split()[1]}
            found[f'G1 D {label}'] = any(item['line'] == number and item['rule'] in rules for item in findings)
    return found


def probe_expected(name) -> bool:
    """A probe form must be flagged: G5 flag_*, G1b E* / N*, G1 D*; ok_*, C* and K* must not."""
    if name.startswith('G1 D '):
        return name[5:6] == 'D'
    if name.startswith('G5'):
        return name.rsplit(' ', 1)[-1].startswith('flag_')
    return name.split(' ', 1)[1][:1] in 'EN'


def expected(guard, case) -> bool:
    """Must the case be flagged by this guard?  G1f: a composition of the event value (Case.g1f)."""
    if guard == 'G1f':
        return case.g1f
    if guard == 'G5 flows' and case.flows_bad is not None:
        return case.flows_bad
    return case.bad


def counts(results) -> OrderedDict:
    """Per guard: cases, bad cases flagged / bad cases, good cases clean / good cases."""
    table = OrderedDict()
    for guard, items in results.items():
        bad = [flagged for case, flagged in items if expected(guard, case)]
        good = [flagged for case, flagged in items if not expected(guard, case)]
        table[guard] = OrderedDict(cases=len(items), bad=len(bad), bad_flagged=sum(bad), good=len(good),
                                   good_clean=len(good) - sum(good))
    return table


def wrong(results) -> list:
    """The cases whose outcome differs from the contract: a bad one not flagged, a good one flagged."""
    return [(guard, case) for guard, items in results.items() for case, flagged in items
            if flagged != expected(guard, case)]

# ----------------------------------------------------------------------------------- the self-test of the self-test

@dataclass
class Mutant:
    """(A16.7; B2 / Q3) One scanner behaviour removed: ``old`` -> ``new`` in i18n_tool.py (each exactly
    once; a tuple of them for a behaviour written in two places), and the contract ids each of which
    must then have a missed case.  ``source``: only cases of this source are run (the one the behaviour
    matters for, e.g. the event value for a composition)."""
    name: str
    forms: tuple
    old: object
    new: object
    which: tuple = ('G5', 'G1b')
    source: str = None
    table: tuple = ()             # (table name, entry): that entry left out of a scanner table instead
    mode: str = None              # only cases of sinks of this mode ('value' for the value compositions)


MUTANTS = (
    Mutant('F1 augmented assignment binds only the right side', ('F1 augmented assignment', 'O G1f composition'),
           "self._targets(node.target, 'value', _augmented(node, self.parents), scope)",
           "self._targets(node.target, 'value', node.value, scope)"),
    Mutant('F1 walrus is no binding', ('F1 walrus',),
           '        elif isinstance(node, ast.NamedExpr):\n', '        elif False:\n'),
    Mutant('F1 with-as is no binding', ('F1 with as',),
           '        elif isinstance(node, (ast.With, ast.AsyncWith)):\n', '        elif False:\n'),
    Mutant('F1 a global binds in the function', ('F1 global',),
           '        if name in scope.globals:\n            return self.module_scope\n',
           '        if False:\n            return self.module_scope\n'),
    Mutant('F1 a nonlocal binds in the inner function', ('F1 nonlocal',),
           '        if name in scope.nonlocals:\n            outer = scope.parent\n',
           '        if False:\n            outer = scope.parent\n'),
    Mutant('F3 a property is no attribute value', ('F3 property',),
           'and _is_property(statement):', 'and False:'),
    Mutant('F3 an augmented attribute binds only the right side', ('F3 augmented attribute',),
           '                add(target, _assigned_value(node, parsed.parents), scope)',
           '                add(target, node.value, scope)', source='event value'),
    Mutant('F5 an augmented item puts only the right side', ('F5 item augmented',),
           "                    found.append((name, 'item', _assigned_value(node, parents), target.slice))",
           "                    found.append((name, 'item', node.value, target.slice))", source='event value'),
    Mutant('F3 getattr default is not followed', ('F3 getattr default',),
           '            return found + (L(call.args[2], parsed, scope, compose, path, seen) if len(call.args) > 2 else [])',
           '            return found'),
    Mutant('F5 add is no mutation', ('F5 add',),
           "SEQUENCE_MUTATORS = {'append': 0, 'add': 0, 'insert': 1}", "SEQUENCE_MUTATORS = {'append': 0, 'insert': 1}"),
    Mutant('F5 insert is no mutation', ('F5 insert',),
           "SEQUENCE_MUTATORS = {'append': 0, 'add': 0, 'insert': 1}", "SEQUENCE_MUTATORS = {'append': 0, 'add': 0}"),
    Mutant('F5 extend is no mutation', ('F5 extend',),
           "        elif method == 'extend' and node.args:", '        elif False:'),
    Mutant('F5 the default of get / pop / setdefault is not followed', ('F5 get default', 'F5 pop default'),
           "                    for default in call.args[1:2] + [keyword.value for keyword in call.keywords if keyword.arg == 'default']:",
           '                    for default in []:'),
    Mutant('F5 popitem() gives nothing', ('F5 popitem()',),
           "                return L(receiver, parsed, scope, compose, _project(path[1:] if path else (), 'items'), seen)",
           '                return []'),
    Mutant('F5 iterating a mapping gives its values', ('F5 dict iteration', 'F5 dict comprehension keys'),
           "        if step in ('iter', 'keys'):", "        if step in ('keys',):"),
    Mutant('F5 keys() gives the values', ('F5 keys()',),
           "                step = {'values': 'values', 'keys': 'keys', 'items': 'items'}[name]",
           "                step = {'values': 'values', 'keys': 'values', 'items': 'items'}[name]"),
    Mutant('F7 frozenset() gives nothing', ('F7 frozenset()',),
           "ITERATE_CALLS = frozenset({'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'list', 'tuple', 'set',\n"
           "                           'frozenset', 'dict', 'iter', 'next', 'min', 'max'})",
           "ITERATE_CALLS = frozenset({'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'list', 'tuple', 'set',\n"
           "                           'dict', 'iter', 'next', 'min', 'max'})"),
    Mutant('F7 the default of next() is not followed', ('F7 next() default',),
           "            for arg in (args[1:] if name == 'next' else [] if len(args) == 1 else args):",
           "            for arg in ([] if name == 'next' else [] if len(args) == 1 else args):"),
    Mutant('F7 copy.copy gives nothing', ('F7 copy.copy',),
           "        if resolved in (('copy', 'copy'), ('copy', 'deepcopy')) and call.args:\n            return L(",
           "        if resolved in (('copy', 'copy'), ('copy', 'deepcopy')) and call.args:\n            return [] and L("),
    Mutant('F8 the factors of * are not followed', ('F8 *',),
           '            if isinstance(node.op, (ast.Add, ast.Mult)) and (compose or path):',
           '            if isinstance(node.op, ast.Add) and (compose or path):'),
    Mutant('F8 * of a number is arithmetic in the grammar', ('F8 *',),
           '            if isinstance(node.op, (ast.Add, ast.Sub, ast.Div, ast.FloorDiv)) and \\',
           '            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)) and \\'),
    Mutant('F8 an unbound str method gives nothing', ('F8 str method unbound',),
           "        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'str' \\\n"
           "                and name in STR_METHODS and call.args and parsed.lookup('str', scope) is None:",
           "        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'str' \\\n"
           "                and name in STR_METHODS and call.args and False:"),
    Mutant('F8 textwrap gives nothing', ('F8 textwrap',),
           "        if resolved[0] == 'textwrap' and name in TEXTWRAP_CALLS:\n            # (A16.3, F8)",
           "        if resolved[0] == 'textwrap' and name in TEXTWRAP_CALLS and False:\n            # (A16.3, F8)"),
    Mutant('F8 re.sub carries nothing', ('F8 re.sub',),
           "            parts = list(call.args[1:3]) + [keyword.value for keyword in call.keywords if keyword.arg in ('repl', 'string')]\n"
           "            return [('composed', node, parsed, 're.sub')] + \\",
           "            parts = []\n            return [('composed', node, parsed, 're.sub')] + \\"),
    Mutant('F8 format() carries nothing', ('F8 format()',),
           "                if compose and name in ('str', 'format'):", "                if compose and name in ('str',):"),
    Mutant('F9 yield gives nothing', ('F9 yield',),
           'if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and node.value is not None',
           'if isinstance(node, ast.Return) and node.value is not None'),
    Mutant('A16.3 no callee is an alias', ('F9 alias of a function', 'F9 getattr method', 'F9 dict of lambdas',
                                           'A16.1 configure callee alias', 'A16.1 messagebox callee alias',
                                           'A16.1 theme callee alias', 'A16.1 append_log callee alias',
                                           'A16.1 append_log getattr', 'A16.1 show callee alias',
                                           'A16.1 sink method callee alias', 'A16.1 sink method getattr',
                                           'A16.4 emit callee alias'),
           '    if depth > 3:\n        return []\n    owner = scope\n', '    if True:\n        return []\n    owner = scope\n'),
    Mutant('F11 a neutral call carries nothing', ('F11 urls.display', 'F11 os.fspath'),
           '        if resolved in NEUTRAL_CALLS or (base, name) in NEUTRAL_CALLS:\n            # (Phase D fix 1',
           '        if False:\n            # (Phase D fix 1'),
    Mutant('F11 Path(x).name carries nothing', ('F11 Path().name',),
           "        if node.attr == 'name' and _path_call(base) and compose is not None:",
           "        if False:"),
    Mutant('F11 Path(x).name is always neutral in the grammar', ('F11 Path().name',),
           '                return all(self._leaves_ok(arg, parsed, scope, compositions=False) for arg in base.args)',
           '                return True'),
    Mutant('A16.1 a sink argument is read by position only', (
        'A16.1 configure placeholder=', 'A16.1 title string=', 'A16.1 set_empty text=', 'A16.1 fill rows=',
        'A16.1 chip set keyword', 'A16.1 textbox insert text=', 'A16.1 textbox insert chars=', 'A16.1 show(value=)',
        'A16.1 error_text(error=)', 'A16.1 show at a strict sink by keyword',
        'A16.1 messagebox message=', 'A16.1 messagebox title=', 'A16.1 messagebox detail=', 'A16.1 item values=',
        'A16.1 filedialog initialfile=', 'A16.1 filedialog filetypes=', 'A16.1 theme label text keyword'),
           '    found = [(keyword.value, ()) for keyword in call.keywords if keyword.arg in names]',
           '    found = []'),
    Mutant('A16.1 a **mapping fills no sink parameter', ('A16.1 configure **mapping display', 'A16.1 configure **mapping name',
                                                         'A16.1 constructor **mapping', 'A16.1 heading **mapping',
                                                         'A16.1 theme **mapping'),
           "            found += [(keyword.value, (('key', name),)) for name in names]",
           '            found += []'),
    Mutant('A16.1 a receiver is a chip / text box only by its name', (
        'A16.1 chip set self binding', 'A16.1 chip set row item', 'A16.1 chip set local',
        'A16.1 textbox insert binding', 'A16.1 textbox insert ctk binding'),
           '        return kind in self._receiver_kinds(receiver, parsed, scope)', '        return False'),
    Mutant('A16.1 a from-import is not resolved', ('A16.1 constructor from-import', 'A16.1 messagebox from-import',
                                                   'A16.1 theme from-import'),
           "            if func.id in parsed.from_imports:\n                module, original = parsed.from_imports[func.id]\n"
           "                return f'{module}.{original}'",
           "            if False:\n                module, original = parsed.from_imports[func.id]\n"
           "                return f'{module}.{original}'"),
    Mutant('A16.1 an import alias is not resolved', ('A16.1 constructor import alias', 'A16.1 theme module alias',
                                                     'A16.1 messagebox module alias'),
           "                    return f'{parsed.imports[base.id]}.{func.attr}'",
           "                    return f'{base.id}.{func.attr}'"),
    Mutant('A16.5 a neutral-named item is neutral whatever it is bound to', ('F5 neutral-named item',),
           "            if isinstance(key, ast.Constant) and key.value in NEUTRAL_FIELDS \\\n"
           "                    and not self._followed(node.value, parsed, scope):",
           "            if isinstance(key, ast.Constant) and key.value in NEUTRAL_FIELDS:"),
    Mutant('A16.4 an object text field is no slot', ('A16.4 .title =',),
           '                elif isinstance(target, ast.Attribute) and target.attr in TEXT_KEYS:',
           '                elif False:'),
    Mutant('A16.4 an object text field in a tuple target is no slot', ('A16.4 .reason = in a tuple target',),
           '                        if isinstance(item, ast.Attribute) and item.attr in TEXT_KEYS:',
           '                        if False:'),
    Mutant('F10 / A16.1 a keyword argument fills no parameter', ('A16.1 append_log line=', 'A16.1 sink method keyword',
                                                                'A16.1 value sink method keyword', 'F10 keyword'),
           ('    found = [(keyword.value, ()) for keyword in call.keywords if keyword.arg in names]',
            '    found = [(keyword.value, path) for keyword in call.keywords if keyword.arg == param]'),
           ('    found = []', '    found = []')),
    Mutant('A16.4 setdefault under a text key is no slot', ("A16.4 setdefault('title')",),
           "            if name == 'setdefault' and isinstance(node.func, ast.Attribute) and len(node.args) > 1 \\",
           "            if False and name == 'setdefault' and isinstance(node.func, ast.Attribute) and len(node.args) > 1 \\"),
    Mutant('A15.4 after / after_idle call nothing', ('A16.1 sink method after', 'A16.1 sink method after_idle',
                                                     'A16.1 append_log after'),
           "        if name in ('after', 'after_idle') and isinstance(node.func, ast.Attribute):",
           "        if False:"),
    Mutant('A15.4 Thread calls nothing', ('A16.1 sink method Thread args', 'A16.1 sink method Thread kwargs'),
           "        elif name == 'Thread':", '        elif False:'),
    Mutant('A15.4 partial calls nothing', ('A16.1 sink method partial', 'A16.1 sink method partial keyword',
                                           'A16.1 append_log partial', 'A16.1 configure partial'),
           "        elif name == 'partial' and", '        elif False and'),
    Mutant('F10 a **param takes no keyword', ('A16.1 sink method **param', 'A16.1 value sink method **param',
                                              'A16.1 constructor **kw wrapper'),
           '    if arguments.kwarg is not None and arguments.kwarg.arg == param:',
           '    if False:'),
    Mutant('F10 a *param takes no argument', ('A16.1 sink method *param',),
           '    if arguments.vararg is not None and arguments.vararg.arg == param:', '    if False:'),
    Mutant('A16.3 a lambda wrapper has no sink parameter', ('A16.1 sink method lambda', 'A16.1 value sink method lambda'),
           '        parent = parsed.parents.get(function)\n        if isinstance(parent, ast.Call) and parent.func is function:\n'
           '            return True\n        return self._called_alias(function, parent, parsed)',
           '        return False'),
    Mutant('F3 the window is one module', ('F3 cross-module attribute',),
           '            for other in self.parsed.values():\n                for key, items in _attribute_index(other).items():',
           '            for other in [parsed]:\n                for key, items in _attribute_index(other).items():'),
    Mutant('G1f sees no composition', ('O G1f composition',),
           '    """The G1f reason when ``node`` composes text (MESSAGES.md 7.2), else None."""\n    if _typed_call(node):',
           '    """The G1f reason when ``node`` composes text (MESSAGES.md 7.2), else None."""\n    if True:'),
)


def _table_mutants():
    """(A16.7) One entry left out of a scanner table at a time: every menu item, message box and file
    dialog function, every ui_theme factory of THEME_SINKS, the widget modules and factories, the text
    keywords, the iteration wrappers, container and str methods, the textwrap calls, the value
    compositions and the G1b emitters, store writers and typed calls, each with the ids it serves."""
    found = []

    def add(table, entry, forms, which=('G5',), source=None, mode=None):
        found.append(Mutant(f'{table} without {entry!r}', tuple(forms), None, None, which, source, (table, entry), mode))

    for item in MENU_SPELLINGS:
        add('MENU_ITEMS', item, [f'A16.1 menu {item}'])
    for function in MESSAGEBOX_SPELLINGS:
        add('MESSAGEBOXES', function, [f'A16.1 messagebox {function}'])
    for function in FILEDIALOG_SPELLINGS:
        add('FILEDIALOGS', function, [f'A16.1 filedialog {function}'])
    factories = OrderedDict()
    for name, _kind, positional, _keyword in THEME_SPELLINGS:
        forms = factories.setdefault(name.split(' ')[0], [])
        forms += ([f'A16.1 theme {name}'] if positional else []) + [f'A16.1 theme {name} keyword']
    for factory, forms in factories.items():
        add('THEME_SINKS', factory, forms)
    add('FILEDIALOG_TEXTS', 'initialfile', ['A16.1 filedialog initialfile='])
    add('FILEDIALOG_TEXTS', 'filetypes', ['A16.1 filedialog filetypes='])
    add('MESSAGEBOX_TEXTS', 'detail', ['A16.1 messagebox detail='])
    add('TEXT_KWARGS', 'placeholder', ['A16.1 configure placeholder=', 'A16.1 constructor placeholder='])
    add('TEXT_KWARGS', 'placeholder_text', ['A16.1 configure placeholder_text=', 'A16.1 constructor placeholder_text='])
    add('WIDGET_MODULES', 'tkinter', ['A16.1 constructor tkinter', 'A16.1 constructor tk'])
    add('WIDGET_MODULES', 'tkinter.ttk', ['A16.1 constructor ttk'])
    add('WIDGET_MODULES', 'customtkinter', ['A16.1 constructor customtkinter', 'A16.1 constructor import alias',
                                            'A16.1 constructor from-import'])
    add('WIDGET_FACTORIES', 'ui_theme.chip', ['A16.1 chip set self binding', 'A16.1 chip set row item',
                                              'A16.1 chip set local'])
    add('WIDGET_FACTORIES', 'ui_theme.textbox', ['A16.1 textbox insert binding'])
    add('WIDGET_FACTORIES', 'customtkinter.CTkTextbox', ['A16.1 textbox insert ctk binding'])
    for name, form in (('map', 'F7 map'), ('filter', 'F7 filter'), ('sorted', 'F7 sorted'), ('reversed', 'F7 reversed'),
                       ('list', 'F7 list()'), ('tuple', 'F7 tuple()'), ('set', 'F7 set()'), ('iter', 'F7 iter()'),
                       ('min', 'F7 min()'), ('max', 'F7 max()'), ('dict', 'F7 dict(k=)')):
        add('ITERATE_CALLS', name, [form], ('G5', 'G1b'))
    for name, form in (('get', 'F5 dict get'), ('pop', 'F5 pop'), ('setdefault', 'F5 setdefault'),
                       ('values', 'F5 values()'), ('keys', 'F5 keys()'), ('popitem', 'F5 popitem()')):
        add('CONTAINER_METHODS', name, [form], ('G5', 'G1b'))
    for name, form in (('strip', 'F8 str method'), ('replace', 'F8 replace inserted'), ('center', 'F8 center inserted'),
                       ('join', 'F8 .join'), ('format', 'F8 .format'), ('format_map', 'F8 .format_map')):
        add('STR_METHODS', name, [form], ('G5', 'G1b'))
    add('TEXTWRAP_CALLS', 'shorten', ['F8 textwrap'], ('G5', 'G1b'))
    add('SEQUENCE_MUTATORS', 'append', ['F5 append', 'F3 attribute mutation'], ('G5', 'G1b'))
    for name, form in (('f-string', 'F8 f-string'), ('+', 'F8 +'), ('*', 'F8 *'), ('%', 'F8 %'), ('.join', 'F8 .join'),
                       ('.format', 'F8 .format'), ('.format_map', 'F8 .format_map'), ('textwrap', 'F8 textwrap'),
                       ('re.sub', 'F8 re.sub')):
        add('VALUE_COMPOSITIONS', name, [form], ('G5',), 'event value', 'value')
    add('EMITTERS', 'say', ['A16.4 say()'], ('G1b',))
    add('STORE_WRITERS', 'finish_subscription', ['A16.4 finish_subscription'], ('G1b',))
    add('STORE_WRITERS', 'create_job', ['A15.4 G1b store writer record'], ('G1b',))
    for name in ('cut', 'masked', 'stripped', 'message_of'):
        add('TYPED', name, [f'A16.4 {name}({ {"message_of": "error"}.get(name, "value") })'], ('G1b',))
    return tuple(found)


MUTANTS = MUTANTS + _table_mutants()


def load_mutant(tool, mutant):
    """A copy of the scanner module ``tool`` with one behaviour removed (nothing is written)."""
    source = Path(tool.__file__).read_text(encoding='utf-8')
    if mutant.table:
        module = types.ModuleType('i18n_tool_mutant')
        module.__file__ = tool.__file__
        exec(compile(source, tool.__file__, 'exec'), module.__dict__)
        for name, entry in (mutant.table if isinstance(mutant.table[0], tuple) else (mutant.table,)):
            value = getattr(module, name)
            if entry not in value:
                raise AssertionError(f'{mutant.name}: {entry!r} is not in i18n_tool.{name}')
            if isinstance(value, dict):
                value = {key: item for key, item in value.items() if key != entry}
            elif isinstance(value, tuple):
                value = tuple(item for item in value if item != entry)
            else:
                value = type(value)(item for item in value if item != entry)
            setattr(module, name, value)
        return module
    olds = mutant.old if isinstance(mutant.old, tuple) else (mutant.old,)
    news = mutant.new if isinstance(mutant.new, tuple) else (mutant.new,)
    for old, new in zip(olds, news):
        count = source.count(old)
        if count != 1:
            raise AssertionError(f'{mutant.name}: the pattern occurs {count} times in i18n_tool.py: {old[:60]!r}')
        source = source.replace(old, new)
    module = types.ModuleType('i18n_tool_mutant')
    module.__file__ = tool.__file__
    exec(compile(source, tool.__file__, 'exec'), module.__dict__)
    return module


MUTANT_CASES = 6                  # cases run per id of a mutant, guard and sink mode (that must be flagged)


def mutant_missed(tool, folder, mutant) -> dict:
    """{id: [(guard, case)]} of the cases of each of the mutant's ids that the mutated scanner judges
    wrongly: a case that must be flagged and is not; for an id of display text (CONTRACT need
    'good'), a good case that is flagged."""
    patched = load_mutant(tool, mutant)
    forms = set(mutant.forms)
    good = {form for form in forms if tool.CONTRACT.get(form, ((), ''))[1] == 'good'}
    chosen = {}

    def select(case):
        wanted = (not case.bad) if good & set(case.forms) else (case.bad or case.g1f)
        if not wanted or mutant.source and case.source != mutant.source \
                or mutant.mode and case.mode != mutant.mode:
            return False
        keep = False
        for form in forms & set(case.forms):
            taken = chosen.setdefault((case.guard, form, case.mode), [])    # both sink modes of each id
            if len(taken) < MUTANT_CASES:
                taken.append(case.key)
                keep = True
        return keep
    results = evaluate(patched, folder, which=mutant.which, select=select, judgements=True)
    missed = {form: [] for form in mutant.forms}
    for guard, items in results.items():
        for case, flagged in items:
            if flagged != expected(guard, case):
                for form in forms & set(case.forms):
                    missed[form].append((guard, case))
    return missed

# the general mechanisms: each removed alone, with the ids that rest on it
MUTANTS += (
    Mutant('F1 an assignment binds nothing', ('F1 assignment', 'F1 alias chain', 'F1 rebinding', 'F1 direct value',
                                              'F1 parameter rebound'),
           "                self._targets(target, 'value', node.value, scope)",
           "                pass"),
    Mutant('F1 a for target binds nothing', ('F1 for target', 'F6 enumerate', 'F6 zip', 'F5 values()', 'F5 items()',
                                             'F6 items() unpacking'),
           '        elif isinstance(node, (ast.For, ast.AsyncFor)):', '        elif False:'),
    Mutant('F1 a comprehension target binds nothing', ('F1 comprehension target', 'F5 list comprehension',
                                                       'F5 set comprehension', 'F5 generator', 'F5 dict comprehension'),
           '        elif isinstance(node, ast.comprehension):', '        elif False:'),
    Mutant('F4 a conditional gives its else branch only', ('F4 conditional',),
           '            return L(node.body, parsed, scope, compose, path, seen) + L(node.orelse, parsed, scope, compose, path, seen)',
           '            return L(node.orelse, parsed, scope, compose, path, seen)'),
    Mutant('F4 or / and give their first operand only', ('F4 or / and',),
           '            return [leaf for value in node.values for leaf in L(value, parsed, scope, compose, path, seen)]',
           '            return [leaf for value in node.values[:1] for leaf in L(value, parsed, scope, compose, path, seen)]'),
    Mutant('F5 an index selects nothing', ('F5 tuple index', 'F5 nested selection'),
           '            return L(node.elts[index], parsed, scope, compose, rest, seen)', '            return []'),
    Mutant('F5 a starred element gives nothing', ('F5 starred element',),
           "                found += L(item.value, parsed, scope, compose, _project(rest, 'iter'), seen)",
           '                found += []'),
    Mutant('F5 a set display gives nothing', ('F5 set display',),
           '        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):\n            return self._sequence_leaves',
           '        if isinstance(node, (ast.Tuple, ast.List)):\n            return self._sequence_leaves'),
    Mutant('F5 a constant key selects nothing', ('F5 dict item',),
           '                elif not isinstance(key, ast.Constant) or key.value == step[1]:',
           '                elif False:'),
    Mutant('F5 update() puts nothing', ('F5 update(k=)', 'F5 update(mapping)'),
           "        elif method == 'update':", '        elif False:'),
    Mutant('F6 an unpacked element is not selected', ('F6 unpacking', 'F6 nested unpacking'),
           '            return self._leaves_at(node.elts[step], rest, parsed, scope, compose, path, seen)',
           '            return []'),
    Mutant('F6 a starred target receives nothing', ('F6 starred unpacking',),
           '        if step is None:\n            return L(node, parsed, scope, compose, _steps(at) + tuple(path), seen)',
           '        if step is None:\n            return []'),
    Mutant('F7 dict() takes no positional mapping', ('F7 dict(mapping)', 'F7 dict(pairs)'),
           '            for arg in args:\n                found += L(arg, parsed, scope, compose, path, seen)\n            for keyword in call.keywords:\n                if keyword.arg is None:',
           '            for arg in []:\n                found += L(arg, parsed, scope, compose, path, seen)\n            for keyword in call.keywords:\n                if keyword.arg is None:'),
    Mutant('F7 next() / min() / max() take no iterable', ('F7 next()', 'F7 min()', 'F7 max()'),
           "            iterables = args[:1] if name == 'next' or len(args) == 1 else []", '            iterables = []'),
    Mutant('F7 the default= of min() / max() is not followed', ('F7 min() default',),
           "                if keyword.arg == 'default':\n                    found += L(keyword.value, parsed, scope, compose, path, seen)",
           "                if False:\n                    found += L(keyword.value, parsed, scope, compose, path, seen)"),
    Mutant('F7 several arguments of min() / max() are not followed', ('F7 max() several',),
           "            for arg in (args[1:] if name == 'next' else [] if len(args) == 1 else args):",
           "            for arg in (args[1:] if name == 'next' else []):"),
    Mutant('F8 the receiver of a str method is not followed', ('F8 .join receiver', 'F8 str method'),
           '                    parts = [(receiver, ())]', '                    parts = []'),
    Mutant('F8 an f-string hole is not followed', ('F8 f-string', 'F8 f-string conversion'),
           '                elif compose and isinstance(value, ast.FormattedValue):', '                elif False:'),
    Mutant('F8 str() carries nothing', ('F8 str()',),
           "                if compose and name in ('str', 'format'):", "                if compose and name in ('format',):"),
    Mutant('F8 a slice carries nothing', ('F8 text slice', 'F5 list slice'),
           '                if compose or path:                         # (G1f: a text slice is the composition itself)',
           '                if False:'),
    Mutant('F9 a lambda returns nothing', ('F9 lambda', 'F9 lambda name', 'F9 dict of lambdas'),
           '            return [definition.body]', '            return []'),
    Mutant('F9 a function returns nothing', ('F9 method return', 'F9 function return', 'F9 nested function',
                                             'F9 staticmethod', 'F9 classmethod', 'F9 other module function',
                                             'F9 from-import function', 'F9 yield'),
           '        return [node.value for node in ast.walk(definition)', '        return [] and [node.value for node in ast.walk(definition)'),
    Mutant('F10 a parameter default is not followed', ('F10 parameter default',),
           '            found += self.leaves(_param_default(owner.node, name), parsed, owner.parent, compose, path, seen)',
           '            found += []'),
    Mutant('F10 a positional argument fills no parameter', ('F10 positional', 'F10 depth 2', 'F10 depth 3'),
           '        found.append((call.args[index], path))', '        pass'),
    Mutant('F10 a **mapping fills no named parameter', ('A16.1 sink method ** argument',),
           "    found += [(spread, (('key', param),) + path) for spread in spreads]", '    found += []'),
    Mutant('F10 a starred argument fills no parameter', ('A16.1 sink method starred argument',),
           "        if isinstance(arg, ast.Starred) and position <= index:", "        if False:"),
    Mutant('F2 module.NAME is not followed', ('F2 module.NAME', 'S4 other module'),
           '                return self._module_leaves(parsed.imports[base.id], node.attr, compose, path, seen)',
           '                return []'),
    Mutant('F2 a from-import is not followed', ('F2 from import',),
           '                return self._module_leaves(module, original, compose, path, seen)', '                return []'),
    Mutant('S3 a module or class text is display text', ('S3 module text', 'S3 class text', 'F2 module name',
                                                         'F3 class attribute'),
           '        if CJK.search(constant.value):\n            return False\n        node = parsed.parents.get(constant)',
           '        if CJK.search(constant.value):\n            return False\n        return True\n        node = parsed.parents.get(constant)',
           which=('G5',)),
    Mutant('F3 an assignment to self.X binds nothing', ('F3 self attribute', 'F3 tuple-target attribute',
                                                        'F3 class name attribute', 'F3 cls attribute',
                                                        'F3 annotated attribute', 'F3 getattr'),
           '            index[target.attr].append((value, parsed, scope, at))', '            pass'),
    Mutant('F3 setattr binds nothing', ('F3 setattr',),
           "                and (node.args[0].id in ('self', 'cls') or node.args[0].id in classes):",
           "                and False:"),
    Mutant('F3 a mutation of self.X puts nothing', ('F3 attribute mutation',),
           '        for attr, how, value, key in _mutation(node, attribute_of, parsed.parents):',
           '        for attr, how, value, key in _mutation(None, attribute_of, parsed.parents):'),
    Mutant('A15.4 configure is no sink', ('A15.4 configure', 'A16.1 config text=', 'A16.1 configure cnf mapping'),
           "        if name in ('configure', 'config') and attribute:", '        if False:'),
    Mutant('A16.1 config is no configure', ('A16.1 config text=',),
           "        if name in ('configure', 'config') and attribute:", "        if name in ('configure',) and attribute:"),
    Mutant('A16.1 a cnf mapping is no sink', ('A16.1 configure cnf mapping',),
           '            if call.args and self._mapping_value(call.args[0], parsed, scope):', '            if False:'),
    Mutant('A15.4 a widget constructor is no sink', ('A15.4 constructor', 'A16.1 constructor values='),
           "        if module in WIDGET_MODULES or module == 'ui_theme' and last[:1].isupper():", '        if False:'),
    Mutant('A16.1 values= is no constructor text', ('A16.1 constructor values=',),
           "            for arg in sorted(TEXT_KWARGS | {'values'}):", '            for arg in sorted(TEXT_KWARGS):'),
    Mutant('A15.4 the ui_theme API is no sink', ('A15.4 theme', 'A15.4 theme unlabelled', 'A15.4 theme columns'),
           "        if module == 'ui_theme' and last in THEME_SINKS:", '        if False:'),
    Mutant('A15.4 Chip.set is no sink', ('A15.4 chip set',),
           "        if name == 'set' and attribute and self._receiver_is('chip', func.value, parsed, scope):", '        if False:'),
    Mutant('A15.4 heading is no sink', ('A15.4 heading',), "        if name == 'heading':", '        if False:'),
    Mutant('A15.4 title is no sink', ('A15.4 title', 'A16.1 wm_title'),
           "        if name in ('title', 'wm_title') and attribute and len(call.args) <= 1 \\",
           "        if False and len(call.args) <= 1 \\"),
    Mutant('A16.1 wm_title is no title', ('A16.1 wm_title',),
           "        if name in ('title', 'wm_title') and attribute and len(call.args) <= 1 \\",
           "        if name in ('title',) and attribute and len(call.args) <= 1 \\"),
    Mutant('A15.4 set_empty is no sink', ('A15.4 set_empty',), "        if name == 'set_empty':", '        if False:'),
    Mutant('A15.4 fill is no sink', ('A15.4 fill',), "        if name == 'fill' and attribute:", '        if False:'),
    Mutant('A15.4 values= is no sink', ('A15.4 values',),
           "            add_all('values', _call_arguments(call, None, ('values',)), f'{name}(values=)')", '            pass'),
    Mutant('A15.4 the text-box insert is no sink', ('A15.4 textbox insert',),
           "            if name == 'insert' and self._receiver_is('textbox', func.value, parsed, scope):", '            if False:'),
    Mutant('A15.4 a menu item is no sink', ('A15.4 menu',), '        if name in MENU_ITEMS and attribute:', '        if False:'),
    Mutant('A15.4 a message box is no sink', ('A15.4 messagebox', 'A16.1 messagebox title', 'A16.1 messagebox dotted',
                                              'A16.1 messagebox starred'),
           "        if module.rpartition('.')[2] == 'messagebox' and last in MESSAGEBOXES:", '        if False:'),
    Mutant('A16.1 the title of a message box is keyword only', ('A16.1 messagebox title',),
           "                add_all('messagebox', _call_arguments(call, position if position < 2 else None, (arg,)),",
           "                add_all('messagebox', _call_arguments(call, position if position == 1 else None, (arg,)),"),
    Mutant('A16.1 a dotted module path is not resolved', ('A16.1 messagebox dotted',),
           '            if isinstance(base, ast.Attribute):\n                inner = self._qualified(base, parsed, scope)',
           '            if False:\n                inner = self._qualified(base, parsed, scope)'),
    Mutant('A16.1 a starred argument fills no sink parameter', ('A16.1 messagebox starred',),
           "                if index <= position:\n                    found.append((arg.value, ('iter',)))",
           "                if False:\n                    found.append((arg.value, ('iter',)))"),
    Mutant('A15.4 a file dialog is no sink', ('A15.4 filedialog',),
           "        if module.rpartition('.')[2] == 'filedialog' and last in FILEDIALOGS:", '        if False:'),
    Mutant('A15.4 i18n.t is no sink', ('A15.4 t()', 'A15.4 t() mapping', 'A15.4 library filetext t()',
                                       'A16.1 t() mapping name'),
           "        if qualified == 'i18n.t':", '        if False:'),
    Mutant('A15.4 a **mapping given to t() is no sink', ('A15.4 t() mapping', 'A16.1 t() mapping name'),
           "                    add('t() mapping', keyword.value, 't(**)', ('values',))", '                    pass'),
    Mutant('A15.4 the argument of show() / error_text() is no value sink', ('A15.4 show()', 'A16.1 show bare',
                                                                            'A16.1 error_text()'),
           "        if shown is not None:\n            add_all('show()'", "        if False:\n            add_all('show()'"),
    Mutant('A15.4 a wrapper of the window is no sink method', ('A15.4 sink method', 'A15.4 value sink method',
                                                               'A16.1 sink method keyword-only',
                                                               'A16.1 value sink method through show'),
           '            return [(m, d) for m, d in self.defs.get(func.attr, ()) if self.method[id(d)]]\n        return []\n\n    def _referenced',
           '            return []\n        return []\n\n    def _referenced'),
    Mutant('A16.1 a function of the window is no sink method', ('A16.1 sink method nested',),
           '            return [(m, d) for m, d in self.defs.get(func.id, ()) if m == parsed.name and not self.method[id(d)]]',
           '            return []'),
    Mutant("A15.4 a dict value under a text key is no slot", ("A15.4 G1b dict 'title'", 'A16.4 emit payload',
                                                              'A16.4 unfinished.merge'),
           "                if isinstance(key, ast.Constant) and key.value in TEXT_KEYS:\n                    slots.append((value, f\"dict '{key.value}'\", False))",
           "                if False:\n                    slots.append((value, f\"dict '{key.value}'\", False))",
           which=('G1b',)),
    Mutant("A15.4 an item assignment under a text key is no slot", ("A15.4 G1b ['title'] =", "A16.4 ['title'] +="),
           "                    slots.append((_assigned_value(node, parsed.parents), f\"['{target.slice.value}'] =\", False))",
           '                    pass', which=('G1b',)),
    Mutant('A15.4 an emit / store writer / raise argument is no slot', ('A15.4 G1b emit()', 'A15.4 G1b store writer record',
                                                                        'A16.4 self.emit()', 'A16.4 finish_subscription'),
           "            if strict:\n                for argument in node.args:", "            if False:\n                for argument in node.args:",
           which=('G1b',)),
    Mutant('A15.4 a typed-call text argument is no slot', ('A15.4 G1b raw(text)', 'A15.4 G1b clauses(parts)',
                                                           'A16.4 raw(text=)'),
           '                for argument, param in _typed_values(node, name):', '                for argument, param in []:',
           which=('G1b',)),
    Mutant('A16.4 a typed call is read by position only', ('A16.4 raw(text=)',),
           '    value = node.args[0] if node.args else next((item.value for item in node.keywords if item.arg == param), None)',
           '    value = node.args[0] if node.args else None', which=('G1b',)),
)

MUTANTS += (
    Mutant('F1 an annotated assignment binds nothing', ('F1 annotated assignment',),
           "        elif isinstance(node, ast.AnnAssign) and node.value is not None:\n            self._targets(node.target, 'value', node.value, scope)",
           "        elif False:\n            self._targets(node.target, 'value', node.value, scope)"),
    Mutant('F5 a ** spread gives nothing', ('F5 ** spread',),
           ('                if key is None:                                              # {**mapping}\n                    found += L(value, parsed, scope, compose, path, seen)',
            '            if key is None:\n                found += L(value, parsed, scope, compose, path, seen)\n                continue'),
           ('                if key is None:                                              # {**mapping}\n                    found += []',
            '            if key is None:\n                found += []\n                continue')),
    Mutant('F5 an item assignment puts nothing', ('F5 item assignment', 'F5 item augmented'),
           '    elif isinstance(node, (ast.Assign, ast.AugAssign)):\n        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):\n            if isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):',
           '    elif False:\n        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):\n            if isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):'),
    Mutant('A15.4 a raise argument is no slot', ('A15.4 G1b raise',),
           '        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):', '        elif False:',
           which=('G1b',)),
    Mutant('A16.1 a module function of the window is no sink method', ('A16.1 sink method module function',),
           '                module = parsed.imports.get(base.id)\n                return [(m, d) for m, d in self.defs.get(func.attr, ()) if m == module and self.toplevel[id(d)]]',
           '                module = parsed.imports.get(base.id)\n                return []'),
    Mutant('A15.4 no keyword argument is a slot', ('A15.4 G1b keyword title=', 'A15.4 G1b store writer',
                                                   'A16.4 store writer keyword', 'A15.4 G1b msg param',
                                                   'A16.4 emit keyword', 'A16.4 keyword TEXT_KEYS parameter'),
           ("                if strict:\n                    slots.append((keyword.value, f'{name}({keyword.arg}=)', True))",
            '                elif is_msg:', '                elif keyword.arg in TEXT_KEYS:',
            '    found = [(keyword.value, path) for keyword in call.keywords if keyword.arg == param]'),
           ("                if False:\n                    slots.append((keyword.value, f'{name}({keyword.arg}=)', True))",
            '                elif False:', '                elif False:', '    found = []'),
           which=('G1b',)),
    Mutant('A16.4 no positional argument fills a TEXT_KEYS parameter', ('A16.4 positional TEXT_KEYS parameter',
                                                                        'A16.4 other module parameter slot'),
           ('                for index, argument in enumerate(node.args):\n                    if index >= len(params) or params[index] not in TEXT_KEYS:',
            '        found.append((call.args[index], path))'),
           ('                for index, argument in enumerate([]):\n                    if index >= len(params) or params[index] not in TEXT_KEYS:',
            '        pass'),
           which=('G1b',)),
)

# the sources and the display text of A15.2: a removed check misses a bad case, a removed proof flags a good one
MUTANTS += (
    Mutant('S2 a Chinese literal is display text', ('S2 Chinese literal',),
           ('            if isinstance(node.value, str) and CJK.search(node.value):\n                return False                     # (final review 1, F1) a Chinese literal at a sink: G1b has no slot there',
            '        if CJK.search(constant.value):\n            return False\n        node = parsed.parents.get(constant)'),
           ('            if False:\n                return False',
            '        if False:\n            return False\n        node = parsed.parents.get(constant)'),
           which=('G5',)),
    Mutant('S5 an enum source is no source', ('S5 enum source',),
           ("            elif kind == 'enum':\n                ok = False", "        if leaf[0] == 'enum':\n            return True"),
           ("            elif kind == 'enum':\n                ok = ok", "        if leaf[0] == 'enum':\n            return False")),
    Mutant('S6 a zh template is no source', ('S6 zh template',),
           "            if name == 'template':                           # S6: a zh catalog template as plain text\n                return [('enum', 'messages', 'template')]",
           "            if name == 'template':                           # S6: a zh catalog template as plain text\n                return []"),
    Mutant('S1 an allow-listed literal is no source', ('S1 allow-listed literal',),
           '            return leaf[1].value in self.context.allowed_literals(leaf[2].name)', '            return False',
           which=('G1b',)),
    Mutant('O the item of an event is display text', ('O event value',),
           '            if isinstance(key, ast.Constant) and isinstance(key.value, str):\n                return self._item_display(node.value, key.value, parsed, scope, seen)',
           '            if isinstance(key, ast.Constant) and isinstance(key.value, str):\n                return True',
           which=('G5',), source='event value'),
    Mutant('N t() is no display text', ('N t()',), None, None, ('G5',), table=('DISPLAY_CALLS', ('i18n', 't'))),
    Mutant('N show() is no display text', ('N show()', 'N show(raw())'), None, None, ('G5',),
           table=(('SHOW_CALLS', ('messages', 'show')), ('DISPLAY_CALLS', ('messages', 'show')))),
    Mutant('N len() is no number', ('N number',), None, None, ('G5',), table=('NUMBER_CALLS', 'len')),
)

# the reviewers' scanner mutants of this review (rv-conv-en-1 SM05, SM09, SM21) that had no own mutant here
MUTANTS += (
    Mutant('F11 a neutral call is always neutral in the grammar', ('F11 urls.display', 'F11 os.fspath'),
           '            return all(self._leaves_ok(arg, parsed, scope, compositions=False) for arg in node.args)',
           '            return True', which=('G5',)),
    Mutant('F3 cls.X is no attribute assignment', ('F3 cls attribute',),
           "        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \\\n"
           "                and (target.value.id in ('self', 'cls') or target.value.id in classes):",
           "        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \\\n"
           "                and (target.value.id in ('self',) or target.value.id in classes):"),
    Mutant('A15.4 unfinished.merge / save is no store writer',
           ('A16.4 unfinished.merge record', 'A16.4 unfinished.save record'),
           "            strict = name in EMITTERS or name in STORE_WRITERS or (name in ('merge', 'save') and base == 'unfinished')",
           '            strict = name in EMITTERS or name in STORE_WRITERS', which=('G1b',)),
)

# (A16.7) the contract ids no mutant removes, each with the reason
UNMUTATED = OrderedDict([
    ('N msg()', 'G1b: a msg() value is no source; no code path makes it one (its text comes from the catalog, A15.2)'),
    ('N raw()', "G1b: raw() of user data is no source; its argument is a slot of its own (A6), no code path makes "
                'raw() a source'),
    ('F9 returned parameter', 'implemented twice: the call substitutes its argument for the returned parameter '
                              '(_Flow._call_leaves), and the parameter is also a value-mode sink parameter checked at '
                              'every call (F10); removing one leaves the other'),
    ('F11 show()', "implemented three times: the flows carry show()'s argument, the grammar checks it inside show() "
                   '(A14.2), and it is a value position of its own (A15.4)'),
    ('A15.4 append_log', "implemented twice: append_log is a value sink by name, and its definition's parameter "
                         'reaches show() (a value-mode sink parameter, F10)'),
])
