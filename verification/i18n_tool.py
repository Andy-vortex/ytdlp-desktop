"""Language tooling: the static text guards and the scans the tests share.

    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_tool.py g1 | sinks [--module NAME ...]

api_calls            the i18n.t / label / choices / all_values / sep, msg and messages.template calls of src with
                     a literal key or group (test_i18n_catalog C8 / C9).
catalog_texts        {'zh', 'en'} of the hand-written catalogs src/i18n_<part>.py (TEXTS), read by AST.
scrub_findings       (1.8.0) files that hold this machine's user name, user folders (patterns built
                     at run time from the environment, 11.7) or an e-mail address.
g1_scan / sinks_scan (1.8.0) the static guards G1 (G1a-G1f, 7.2) and G5 (7.9) with the per-module
                     allow files golden/text-allow/<module>.json.  G5 replaces the 1.7.0 suspects scan.

(2.0.0 S3 W2) The catalogs are hand-written (S3-DESIGN.md 1.1): the inventory i18n_inventory.json, its
generator (the scan / catalog commands) and i18n_maps.py are gone; test_i18n_catalog.CatalogRuleTests checks
the catalogs directly.

No src module is imported at module level; only the standard library.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import string
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
VERIFICATION = ROOT / 'verification'

# The catalog parts: one hand-written src/i18n_<part>.py each (S3-DESIGN.md 1.1), listed from src itself.
MODULES = tuple(sorted(path.stem[len('i18n_'):] for path in SRC.glob('i18n_*.py')))

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')

_FORMATTER = string.Formatter()


# --------------------------------------------------------------------------------- f-strings

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


# --------------------------------------------------------------------------------- calls of the i18n API

_API = {'t': 'key', 'all_values': 'key', 'label': 'group', 'choices': 'group'}


def api_calls(src=SRC) -> list:
    """[{file, line, function, kind, name, keywords}] of i18n.t / label / choices / all_values / sep calls
    (also as bare names imported from i18n) whose first argument is a constant string; for sep the name
    is the key it shows ('common.<name>').  keywords: the keyword argument names of the call, or None when
    it has a ** argument (S3 C8: a t() call passes exactly the placeholders of its text)."""
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
            elif (isinstance(func, ast.Name) and func.id == 'msg') or (
                    isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == 'messages' and func.attr in ('msg', 'template')):
                # (1.8.0) msg(key, ...) and messages.template(key) name catalog keys too (each
                # branch of a conditional key counts)
                for key in _key_literals(node.args[0]) or ():
                    if key != 'common.clauses':
                        found.append(OrderedDict(file=path.name, line=node.lineno, function='msg', kind='key',
                                                 name=key, keywords=_keywords(node)))
                continue
            else:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append(OrderedDict(file=path.name, line=node.lineno, function=name, kind=_API[name],
                                         name=first.value, keywords=_keywords(node)))
    return found


def _keywords(call):
    """The keyword argument names of a call, or None when it has a ** argument."""
    names = tuple(keyword.arg for keyword in call.keywords)
    return None if None in names else names


# --------------------------------------------------------------------------------- 1.8.0: scrub patterns

def scrub_patterns(environ=None) -> list:
    """(MESSAGES.md 11.7) The scrub patterns, built at run time from the environment so that no
    document or test holds them: the Windows user name, its 8.3 short form, the user profile and
    LOCALAPPDATA paths (both slash forms).  Returns [(label, lower-cased text)]; e-mail addresses are
    matched by EMAIL separately."""
    import os
    environ = os.environ if environ is None else environ
    found = []

    def add(label, text):
        text = str(text or '').strip()
        if len(text) >= 3 and (label, text.lower()) not in found:
            found.append((label, text.lower()))

    add('user name', environ.get('USERNAME'))
    for name in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA'):
        value = environ.get(name)
        if value:
            add(name, value)
            add(name, value.replace('\\', '/'))
    profile = environ.get('USERPROFILE')
    if profile:
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(profile, buffer, 1024):
                short = buffer.value
                add('USERPROFILE (8.3)', short)
                add('USERPROFILE (8.3)', short.replace('\\', '/'))
                add('user name (8.3)', Path(short).name)
        except (OSError, AttributeError, ValueError):
            pass
    return found


EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')


def scrub_findings(folders, environ=None) -> list:
    """[(relative path, label)] of every file under ``folders`` that holds a scrub pattern or an
    e-mail address."""
    patterns = scrub_patterns(environ)
    findings = []
    for folder in folders:
        folder = Path(folder)
        for path in sorted(folder.rglob('*')):
            if not path.is_file():
                continue
            text = path.read_bytes().decode('utf-8', errors='replace')
            lower = text.lower()
            for label, pattern in patterns:
                if pattern in lower:
                    findings.append((path.relative_to(folder).as_posix(), label))
            if EMAIL.search(text):
                findings.append((path.relative_to(folder).as_posix(), 'e-mail address'))
    return findings


def _module_constants(path, names) -> dict:
    """{name: literal value} of module-level assignments in a source file (ast.literal_eval)."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names:
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


# --------------------------------------------------------------------------------- 1.8.0: G1 (MESSAGES.md 7.2)

ALLOW_DIR = VERIFICATION / 'golden' / 'text-allow'
CATALOG_STEMS = tuple(f'i18n_{name}' for name in MODULES)
G1_EXCLUDED = CATALOG_STEMS
# (1.8.0 WP-6, MESSAGES.md 7.8) The staging sets are gone: G1 applies to every src module outside
# G1_EXCLUDED and G5 to every UI module, unconditionally.  (S3 review fix) The checks for their leftovers
# (MESSAGES_READY, the transit sets, the localize baseline, the 'pending' category and pending_keys) went too.
CORE_MODULES = ('engine', 'unfinished', 'naming', 'urls', 'formats', 'collector', 'library', 'updater')
UI_MODULES = ('studio', 'ui_download', 'ui_log', 'collection_ui', 'library_ui', 'ui_settings')
TOKEN_MODULES = ('messages', 'i18n')
# (2.0.0 S2 P5) No 'stored' category: the stored enum values are codes (S2-DESIGN.md 1.7), not CJK literals.
# (2.0.0 S3 W1) No 'h4' / 'h4-unused': the Chinese constants only tests read are gone (S3-DESIGN.md 4.1).
CATEGORIES = ('file', 'internal', 'token')
ALLOW_SECTIONS = ('literals', 'raises', 'composed', 'sinks')
# (2.0.0 final polish) The one 'file' literal that may share its text with a catalog key: {(module, text): key}.
# engine's '媒体' is the name of the archive's media subfolder on disk; renaming it would lose the existing
# archives, and the collect table's link-kind column (value.link_kind.media) shows the same word.  The owner's
# reviewer allowed exactly this entry; G1a still flags any other copy, and this one if its category or key changes.
FILE_SAME_AS_KEY = {('engine', '媒体'): 'value.link_kind.media'}
TEXT_KEYS = frozenset({'title', 'name', 'label', 'error', 'reason', 'message', 'text', 'last_error', 'page_title',
                       'detail'})
NEUTRAL_FIELDS = frozenset({'url', 'urls', 'page_url', 'webpage_url', 'path', 'folder', 'output', 'id', 'version',
                            'uploader', 'source'})
STORE_WRITERS = frozenset({'create_job', 'change_job', 'download_event', 'collection_event',
                           'finish_subscription', 'subscription', 'archive_index'})
EMITTERS = frozenset({'emit', 'say', 'log'})
TYPED = frozenset({'msg', 'raw', 'cut', 'masked', 'stripped', 'clauses', 'message_of'})
RECORD_CALLS = frozenset({'by_id', 'by_label', 'by_key'})
# (2.0.0 S2 P4) The 1.8.x codec (messages.decode / decode_columns / encode) is gone.  The format-2 slot codec
# (messages.read_slots / write_slots) is the stores' own.
SLOT_CALLS = frozenset({'read_slots', 'write_slots'})
SLOT_MODULES = ('library', 'unfinished')
KEY_RANGES_STORED = ('core.', 'value.', 'download.reason.')


def load_allow(module, allow_dir=ALLOW_DIR) -> dict:
    """The allow file of a src module (every section present, empty when the file has none)."""
    path = Path(allow_dir) / f'{module}.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return {section: dict(data.get(section) or {}) for section in ALLOW_SECTIONS} | \
        {key: value for key, value in data.items() if key not in ALLOW_SECTIONS}


def src_modules(src=SRC) -> list:
    return sorted(path.stem for path in Path(src).glob('*.py'))


def g1_modules(src=SRC) -> list:
    """The modules G1 applies to: every src module except the catalogs and the generated data
    (MESSAGES.md 7.2; unconditional since WP-6, 7.8)."""
    return [module for module in src_modules(src) if module not in G1_EXCLUDED]


def catalog_texts(src=SRC) -> dict:
    """{'zh': {...}, 'en': {...}} of the src catalogs (their TEXTS), read by AST (no import)."""
    merged = {'zh': {}, 'en': {}}
    for stem in CATALOG_STEMS:
        texts = _module_constants(Path(src) / f'{stem}.py', {'TEXTS'}).get('TEXTS', {})
        for key, (zh, en) in texts.items():
            merged['zh'][key] = zh
            merged['en'][key] = en
    return merged


def _docstring_ids(tree) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _literal_nodes(tree):
    """(node, text) of every str literal and f-string (one node each; docstrings and the parts of
    f-strings excluded)."""
    skip = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                skip.add(id(value))
                if isinstance(value, ast.FormattedValue) and value.format_spec is not None:
                    skip.add(id(value.format_spec))
                    for item in value.format_spec.values:
                        skip.add(id(item))
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node, node.value
        elif isinstance(node, ast.JoinedStr):
            yield node, _fstring_text(node)[0]


def _func_name(node):
    """'name' of a call's function (bare or the attribute), and the base name ('' when none)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id, ''
    if isinstance(func, ast.Attribute):
        base = func.value
        return func.attr, base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else '')
    return '', ''


def _imports(tree) -> dict:
    """{local name: src module} of `import x` / `import x as y` of every module name."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name] = alias.name
    return found


SEQUENCE_MUTATORS = {'append': 0, 'add': 0, 'insert': 1}


def _load_copy(target):
    """A Load-context copy of an assignment target (its children are shared, so scope_of still
    walks up from them)."""
    if isinstance(target, ast.Name):
        copy = ast.Name(id=target.id, ctx=ast.Load())
    elif isinstance(target, ast.Attribute):
        copy = ast.Attribute(value=target.value, attr=target.attr, ctx=ast.Load())
    elif isinstance(target, ast.Subscript):
        copy = ast.Subscript(value=target.value, slice=target.slice, ctx=ast.Load())
    else:
        return target
    return ast.copy_location(copy, target)


def _augmented(node, parents=None):
    """(Phase D fix 1, A16.2 / Q2) The value an augmented assignment ``x op= v`` binds: the composition
    ``x op v`` (a synthesized ``ast.BinOp`` of the target's previous value and ``v``), so ``text +=
    b`` is the '+' composition it is (G1f, the G5 value rule) and the parts of both stay followed.
    One node per statement, registered in ``parents`` (when given) under the statement."""
    combined = getattr(node, '_combined', None)
    if combined is None:
        left = _load_copy(node.target)
        combined = ast.copy_location(ast.BinOp(left=left, op=node.op, right=node.value), node)
        node._combined = combined
    if parents is not None and combined not in parents:
        parents[combined] = node
        if combined.left is not node.target:
            parents[combined.left] = combined
    return combined


def _assigned_value(node, parents=None):
    """The value an Assign / AnnAssign / AugAssign statement binds (the composition for AugAssign)."""
    if isinstance(node, ast.AugAssign):
        return _augmented(node, parents)
    return node.value


def _mutation(node, base_ok=None, parents=None) -> list:
    """(A15.3 F5) [(name, how, value, key)] of what a statement puts into the container a name holds:
    ``x.append(v)`` / ``x.add(v)`` / ``x.insert(i, v)`` -> ('element', v); ``x.extend(vs)`` ->
    ('elements', vs); ``x[k] = v`` / ``x[k] += v`` / ``x.setdefault(k, v)`` / ``x.update(k=v)`` ->
    ('item', v, the key node); ``x.update(m)`` -> ('items', m).  ``base_ok(expression)`` names the
    receiver: a bare name by default; the attribute index passes ``self.<attr>``."""
    def name_of(expression):
        if base_ok is not None:
            return base_ok(expression)
        return expression.id if isinstance(expression, ast.Name) else None

    found = []
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        name = name_of(node.func.value)
        method = node.func.attr
        if name is None:
            return found
        if method in SEQUENCE_MUTATORS and len(node.args) > SEQUENCE_MUTATORS[method]:
            found.append((name, 'element', node.args[SEQUENCE_MUTATORS[method]], None))
        elif method == 'extend' and node.args:
            found.append((name, 'elements', node.args[0], None))
        elif method == 'setdefault' and len(node.args) > 1:
            found.append((name, 'item', node.args[1], node.args[0]))
        elif method == 'update':
            found += [(name, 'items', arg, None) for arg in node.args[:1]]
            found += [(name, 'item', keyword.value, ast.Constant(value=keyword.arg)) if keyword.arg
                      else (name, 'items', keyword.value, None) for keyword in node.keywords]
    elif isinstance(node, (ast.Assign, ast.AugAssign)):
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
            if isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):
                name = name_of(target.value)
                if name is not None:
                    # A16.2 (Q2): ``x[k] += v`` puts the composition ``x[k] + v`` under k
                    found.append((name, 'item', _assigned_value(node, parents), target.slice))
    return found


class _Scope:
    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        self.bindings = defaultdict(list)       # name -> [(kind, expression)]
        # I-2 (amendment A7, SD-2): name -> [(kind, expression, path)] of every 'unpack' / 'iter' binding;
        # path = the target's index at each tuple level (None after a starred target), () for a plain
        # loop target.  G5 checks the element a target receives; G1 keeps reading `bindings`.
        self.positions = defaultdict(list)
        self.params = set()
        self.exceptions = set()
        self.tainted = set()
        # (converge round, A15.3 F5) name -> [(how, value)]: what is put into the container a name is
        # bound to (`x.append(v)`, `x.insert(i, v)`, `x.add(v)`, `x.setdefault(k, v)`, `x[k] = v`:
        # 'element'; `x.extend(vs)`, `x.update(m)`: 'elements'), also from a nested function
        self.mutations = defaultdict(list)
        # (Phase D fix 1, A16.2) the names a function declares `global` / `nonlocal`: its bindings of
        # them are bindings of the module scope / of the enclosing function that binds them
        self.globals = set()
        self.nonlocals = set()
        self.comprehension = False


class _Module:
    """One parsed module with its scopes, function signatures and helpers for G1 / G5."""

    def __init__(self, path, name=None):
        self.path = Path(path)
        self.name = name or self.path.stem
        self.source = self.path.read_text(encoding='utf-8')
        self.tree = ast.parse(self.source, filename=str(self.path))
        self.imports = _imports(self.tree)
        self.from_imports = {}                  # local name -> (module, name) of `from module import name`
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    self.from_imports[alias.asname or alias.name] = (node.module, alias.name)
        self.parents = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self.scopes = {}
        self.module_scope = _Scope(self.tree, None)
        self.scopes[self.tree] = self.module_scope
        self.functions = defaultdict(list)      # name -> [FunctionDef]
        self._collect(self.tree, self.module_scope)
        for node in ast.walk(self.tree):        # A15.3 F5: container mutations, by the scope that binds the name
            for name, how, value, key in _mutation(node, parents=self.parents):
                owner = self.lookup(name, self.scope_of(node))
                if owner is not None:
                    owner.mutations[name].append((how, value, key))

    @staticmethod
    def _declared(function):
        """(A16.2) The names a function body declares `global` / `nonlocal` (not in nested functions,
        classes or lambdas)."""
        found = {'global': set(), 'nonlocal': set()}
        stack = list(ast.iter_child_nodes(function))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, ast.Global):
                found['global'].update(node.names)
            elif isinstance(node, ast.Nonlocal):
                found['nonlocal'].update(node.names)
            stack.extend(ast.iter_child_nodes(node))
        return found

    def _collect(self, node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                inner = _Scope(child, scope)
                self.scopes[child] = inner
                if not isinstance(child, ast.Lambda):
                    self.functions[child.name].append(child)
                    self._owner(child.name, scope).bindings[child.name].append(('def', None))
                    declared = self._declared(child)
                    inner.globals, inner.nonlocals = declared['global'], declared['nonlocal']
                arguments = child.args
                for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
                    inner.params.add(arg.arg)
                for arg in (arguments.vararg, arguments.kwarg):
                    if arg is not None:
                        inner.params.add(arg.arg)
                self._collect(child, inner)
                continue
            if isinstance(child, ast.ClassDef):
                scope.bindings[child.name].append(('class', None))
                self._collect(child, scope)         # class bodies: treat as the enclosing scope
                continue
            if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                # (converge round) a comprehension has its own scope (Python 3): the targets of two
                # comprehensions of one function are different names
                inner = _Scope(child, scope)
                inner.comprehension = True
                self.scopes[child] = inner
                self._collect(child, inner)
                continue
            self._bind(child, scope)
            self._collect(child, scope)

    def _owner(self, name, scope):
        """(A16.2) The scope a binding of ``name`` in ``scope`` belongs to: the module scope for a
        `global` name, the nearest enclosing function that binds a `nonlocal` name, else ``scope``."""
        if name in scope.globals:
            return self.module_scope
        if name in scope.nonlocals:
            outer = scope.parent
            while outer is not None and outer is not self.module_scope:
                if name in outer.bindings or name in outer.params:
                    return outer
                outer = outer.parent
        return scope

    def _targets(self, target, kind, value, scope, path=()):
        if isinstance(target, ast.Name):
            scope = self._owner(target.id, scope)
            scope.bindings[target.id].append((kind, value))
            if kind in ('unpack', 'iter'):
                scope.positions[target.id].append((kind, value, path))
        elif isinstance(target, (ast.Tuple, ast.List)):
            starred = any(isinstance(item, ast.Starred) for item in target.elts)
            for index, item in enumerate(target.elts):
                self._targets(item, kind if kind != 'value' else 'unpack', value, scope,
                              path + (None if starred else index,))
        elif isinstance(target, ast.Starred):
            self._targets(target.value, kind, value, scope, path)

    def _bind(self, node, scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._targets(target, 'value', node.value, scope)
        elif isinstance(node, ast.AugAssign):
            # A16.2 (Q2): `x op= v` binds x to the composition `x op v` (its old value and v)
            self._targets(node.target, 'value', _augmented(node, self.parents), scope)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._targets(node.target, 'value', node.value, scope)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._targets(node.target, 'iter', node.iter, scope)
        elif isinstance(node, ast.comprehension):
            self._targets(node.target, 'iter', node.iter, scope)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._targets(item.optional_vars, 'with', item.context_expr, scope)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            scope.exceptions.add(node.name)
            scope.bindings[node.name].append(('except', None))
        elif isinstance(node, ast.NamedExpr):
            # PEP 572: `:=` inside a comprehension binds in the scope that contains the comprehension
            while scope.comprehension and scope.parent is not None:
                scope = scope.parent
            self._targets(node.target, 'value', node.value, scope)

    def scope_of(self, node):
        while node is not None:
            if node in self.scopes:
                return self.scopes[node]
            node = self.parents.get(node)
        return self.module_scope

    def lookup(self, name, scope):
        """The scope that binds ``name`` (innermost first), or None.  A function that declares the
        name `global` sees the module scope; `nonlocal`, the enclosing functions (A16.2)."""
        while scope is not None:
            if name in scope.globals:
                scope = self.module_scope
                continue
            if name in scope.nonlocals:
                scope = scope.parent
                continue
            if name in scope.bindings or name in scope.params:
                return scope
            scope = scope.parent
        return None


def _key_literals(node):
    """The literal keys of a msg() key argument (a str or a conditional of such); None otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _key_literals(node.body), _key_literals(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None


def _placeholder_names(text) -> set:
    try:
        return {field for _literal, field, _spec, _conv in _FORMATTER.parse(text) if field is not None}
    except ValueError:
        return set()


class GuardContext:
    """What one G1 / G5 run needs about the whole tree: allow files, enum sources, catalogs."""

    def __init__(self, src=SRC, allow_dir=ALLOW_DIR, enum_src=None):
        self.src = Path(src)
        self.allow_dir = Path(allow_dir)
        self.enum_src = Path(enum_src) if enum_src else self.src
        self._allow = {}
        self._modules = {}
        self._catalogs = None
        self._enum = None

    @property
    def src_names(self):
        if not hasattr(self, '_src_names'):
            self._src_names = set(src_modules(self.src))
        return self._src_names

    def allow(self, module):
        if module not in self._allow:
            self._allow[module] = load_allow(module, self.allow_dir)
        return self._allow[module]

    def module(self, name, folder=None):
        folder = Path(folder) if folder else self.src
        key = (folder, name)
        if key not in self._modules:
            self._modules[key] = _Module(folder / f'{name}.py', name)
        return self._modules[key]

    @property
    def catalogs(self):
        if self._catalogs is None:
            folder = self.src if (self.src / 'i18n_core.py').is_file() else SRC
            self._catalogs = catalog_texts(folder)
        return self._catalogs

    def stored_texts(self) -> dict:
        """(S3 G1a-stored) {zh text ('{{' / '}}' restored): key} of the catalog keys in KEY_RANGES_STORED."""
        if not hasattr(self, '_stored_texts'):
            self._stored_texts = {text: key for text, key in self.zh_texts().items() if key.startswith(KEY_RANGES_STORED)}
        return self._stored_texts

    def zh_texts(self) -> dict:
        """(S3 review fix) {zh text ('{{' / '}}' restored): key} of every catalog key (the first key by name when two
        keys share a text)."""
        if not hasattr(self, '_zh_texts'):
            self._zh_texts = {}
            for key, text in sorted(self.catalogs['zh'].items()):
                self._zh_texts.setdefault(text.replace('{{', '{').replace('}}', '}'), key)
        return self._zh_texts

    def g1_parameter_slots(self, module) -> dict:
        """(A15.3 F10) {function name: [(definition, param, strict, path, slot)]} of the module-level
        functions of a src module whose parameters are G1 slots there (computed once; a cycle between
        modules sees the parameters found so far)."""
        cache = self.__dict__.setdefault('_g1_parameters', {})
        if module not in cache:
            cache[module] = {}
            found = defaultdict(list)
            try:
                parsed = self.module(module)
            except (OSError, SyntaxError, ValueError):
                return cache[module]
            params = []
            g1_slots(self, parsed, params=params)
            for definition, name, kind, at, slot in params:
                if parsed.parents.get(definition) is parsed.tree:
                    found[definition.name].append((definition, name, kind, at, slot))
            cache[module] = dict(found)
        return cache[module]

    def allowed_literals(self, module) -> set:
        return set(self.allow(module)['literals'])

    def enum_sources(self) -> dict:
        """{module: {NAME}} of module-level names whose value holds an allow-listed literal of that
        module, or refers to such a name (transitively, also through ``module.NAME``)."""
        if self._enum is not None:
            return self._enum
        names = {}
        values = {}
        for module in src_modules(self.enum_src):
            if module in G1_EXCLUDED:
                continue
            parsed = self.module(module, self.enum_src)
            allowed = self.allowed_literals(module)
            names[module] = set()
            for node in parsed.tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] \
                    if isinstance(node, ast.AnnAssign) and node.value is not None else []
                for target in targets:
                    if isinstance(target, ast.Name):
                        values.setdefault(module, []).append((target.id, node.value, parsed))
                        if any(isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in allowed
                               for sub in ast.walk(node.value)):
                            names[module].add(target.id)
        changed = True
        while changed:
            changed = False
            for module, items in values.items():
                for target, value_node, parsed in items:
                    if target in names[module]:
                        continue
                    for sub in ast.walk(value_node):
                        hit = (isinstance(sub, ast.Name) and sub.id in names[module]) or \
                            (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                             and parsed.imports.get(sub.value.id) in names and sub.attr in names[parsed.imports[sub.value.id]])
                        if hit:
                            names[module].add(target)
                            changed = True
                            break
        self._enum = names
        return names


# str methods whose result is made of the receiver's text and of their arguments (A11.1)
STR_METHODS = frozenset({'format', 'format_map', 'join', 'strip', 'lstrip', 'rstrip', 'replace', 'lower', 'upper',
                         'title', 'capitalize', 'casefold', 'swapcase', 'removeprefix', 'removesuffix', 'ljust',
                         'rjust', 'center', 'zfill', 'expandtabs'})


# --------------------------------------------------------------------------------- 1.8.0: the guard contract (A15)
#
# (converge round, MESSAGES.md 15 amendment A15) One implementation of the FLOWS every static guard
# follows: G1b / G1f (_Taint, a module and the src modules it names) and G5 (_Sinks, the UI modules of
# the window).  _Flow.leaves() gives the *leaves* an expression may carry; each guard judges them.
# (Phase D fix 1, A16.5) The sources are found by _Flow.leaves everywhere, the strict sinks included;
# G5's strict rule adds the display grammar (_Sinks.display), which also needs every value to be
# provably display text.  The contract the two implement is the list CONTRACT (A16.7).

# F7: calls whose result is made of the elements of their iterable arguments (and, for dict(), of
# the values of its keyword arguments)
ITERATE_CALLS = frozenset({'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'list', 'tuple', 'set',
                           'frozenset', 'dict', 'iter', 'next', 'min', 'max'})
# F6: mapping / sequence methods whose result is (part of) the receiver's content
CONTAINER_METHODS = frozenset({'get', 'pop', 'setdefault', 'popitem', 'values', 'items', 'keys', 'copy'})
# F8 at a G5 value position: the compositions whose parts must be display text (7.9)
VALUE_COMPOSITIONS = frozenset({'f-string', '+', '%', '.join', '.format', '.format_map',
                                # (Phase D fix 1, A16.4) the other compositions that flatten a Message
                                # without a tripwire (2.3): repetition, textwrap, re.sub
                                '*', 'textwrap', 're.sub'})


class _Call:
    """A call as the guards see it (A15.3 F10, A15.4): an ``ast.Call``, or the call a callback form
    makes — ``<widget>.after(ms, f, *args)``, ``<widget>.after_idle(f, *args)``, ``functools.partial(f,
    *args, **kw)`` and ``threading.Thread(target=f, args=(...), kwargs={...})`` call ``f`` with those
    arguments.  Same attribute names as ``ast.Call`` (func, args, keywords), so the helpers take both."""
    __slots__ = ('node', 'func', 'args', 'keywords', 'lineno', 'callback')

    def __init__(self, node, func, args, keywords, callback=None):
        self.node, self.func, self.args, self.keywords = node, func, list(args), list(keywords)
        self.lineno, self.callback = node.lineno, callback


def _call_sites(tree) -> list:
    """Every call of a module, the callback forms included (A15.4)."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        sites.append(_Call(node, node.func, node.args, node.keywords))
        name, base = _func_name(node)
        if name in ('after', 'after_idle') and isinstance(node.func, ast.Attribute):
            start = 1 if name == 'after' else 0
            if len(node.args) > start and not isinstance(node.args[start], ast.Lambda):
                sites.append(_Call(node, node.args[start], node.args[start + 1:], [], name))
        elif name == 'partial' and (isinstance(node.func, ast.Name) or base == 'functools') and node.args \
                and not isinstance(node.args[0], ast.Lambda):
            sites.append(_Call(node, node.args[0], node.args[1:], node.keywords, name))
        elif name == 'Thread':
            given = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            target = given.get('target')
            if target is None or isinstance(target, ast.Lambda):
                continue
            args = given.get('args')
            positional = [] if args is None else list(args.elts) if isinstance(args, (ast.Tuple, ast.List)) \
                else [ast.Starred(value=args, ctx=ast.Load(), lineno=args.lineno, col_offset=args.col_offset)]
            extra = given.get('kwargs')
            keywords = []
            if isinstance(extra, ast.Dict):
                for key, value in zip(extra.keys, extra.values):
                    keywords.append(ast.keyword(arg=key.value if isinstance(key, ast.Constant) else None, value=value))
            elif extra is not None:
                keywords.append(ast.keyword(arg=None, value=extra))
            sites.append(_Call(node, target, positional, keywords, name))
    return sites


def _call_arguments(call, position, names) -> list:
    """(Phase D fix 1, A16.1) [(expression, path)] of what a call passes to one parameter of a callee
    G5 / G1 knows by its signature: the positional argument at ``position`` (None: keyword only), the
    keyword argument of any of ``names``, every starred argument at or before the position (path: an
    element) and every ``**mapping`` (path: its item under the parameter's name).  ZH-CONV-1: before,
    only the positional argument was read, so ``show(value=X)`` or ``set_empty(text=X)`` was no sink."""
    found = [(keyword.value, ()) for keyword in call.keywords if keyword.arg in names]
    for keyword in call.keywords:
        if keyword.arg is None:
            found += [(keyword.value, (('key', name),)) for name in names]
    if position is not None:
        for index, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                if index <= position:
                    found.append((arg.value, ('iter',)))
            elif index == position and not any(isinstance(item, ast.Starred) for item in call.args[:index]):
                found.append((arg, ()))
    return found


# (A16.1) the text parameter of show() / error_text() / zh(), by name (messages.py)
SHOWN_ARGUMENT = {'show': 'value', 'error_text': 'error', 'zh': 'value'}
# (A16.3, F8) the textwrap functions: they compose the text they are given
TEXTWRAP_CALLS = frozenset({'shorten', 'fill', 'wrap', 'dedent', 'indent'})


def _path_call(node) -> bool:
    """``Path(...)`` / ``pathlib.Path(...)`` / ``PurePath(...)`` (F11)."""
    return isinstance(node, ast.Call) and _func_name(node)[0] in ('Path', 'PurePath', 'PureWindowsPath',
                                                                  'WindowsPath')


def _unbound_str_call(call, parsed):
    """(A16.3) ``str.<method>(text, *args)`` as the bound call ``text.<method>(*args)`` (one synthesized
    node per call site, registered under it)."""
    key = call.func
    bound = getattr(key, '_bound_call', None)
    if bound is None:
        receiver = call.args[0].value if isinstance(call.args[0], ast.Starred) else call.args[0]
        attribute = ast.copy_location(ast.Attribute(value=receiver, attr=call.func.attr, ctx=ast.Load()), call.func)
        bound = ast.copy_location(ast.Call(func=attribute, args=list(call.args[1:]), keywords=list(call.keywords)),
                                  call.node)
        key._bound_call = bound
        parsed.parents.setdefault(bound, call.node)
        parsed.parents.setdefault(attribute, bound)
    return bound


def _callee_aliases(func, parsed, scope, attributes, methods, depth=0) -> list:
    """(Phase D fix 1, A16.3; ZH-CONV-2 / M1) The callables an *alias* of a callee stands for, so the
    call is checked as a call of each of them: a local or module name whose every binding is a
    reference (``configure = self.status.configure``, ``warn = messagebox.showwarning``, ``fit =
    ui_theme.fit``, ``log = self.append_log``, ``render = messages.show``) or a lambda; ``getattr(obj,
    'name')``; ``self.<attr>`` (not a method) whose every assignment is a reference; an item of a dict /
    list / tuple display of references or lambdas (also through a name bound only to such a display).
    [] when the callee is no alias.  ``attributes(attr)`` gives the F3 entries, ``methods`` the method
    names (a method is called, not aliased)."""
    if depth > 3:
        return []
    owner = scope
    if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == 'getattr' \
            and parsed.lookup('getattr', scope) is None and len(func.args) >= 2 \
            and isinstance(func.args[1], ast.Constant) and isinstance(func.args[1].value, str):
        target = getattr(func, '_alias_target', None)
        if target is None:
            target = ast.copy_location(ast.Attribute(value=func.args[0], attr=func.args[1].value, ctx=ast.Load()),
                                       func)
            func._alias_target = target
            parsed.parents.setdefault(target, func)
        return _callee_aliases(target, parsed, scope, attributes, methods, depth + 1) or [target]
    if isinstance(func, ast.Name):
        owner = parsed.lookup(func.id, scope)
        if owner is None or func.id in owner.params:
            return []
        bindings = owner.bindings.get(func.id, ())
        if not bindings or any(kind != 'value' or value is None for kind, value in bindings):
            return []
        values = [value for _kind, value in bindings]
    elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in ('self', 'cls') \
            and func.attr not in methods:
        entries = attributes(func.attr)
        if not entries or any(at or other is not parsed and not (
                isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                and value.value.id in ('self', 'cls')) for value, other, _scope, at in entries):
            return []
        values = [value for value, _other, _scope, _at in entries]
    elif isinstance(func, ast.Subscript):
        base, key = func.value, func.slice
        if isinstance(base, (ast.Dict, ast.List, ast.Tuple)):
            displays = [base]
        elif isinstance(base, ast.Name):
            owner = parsed.lookup(base.id, scope)
            if owner is None or base.id in owner.params or owner.mutations.get(base.id):
                return []
            bindings = owner.bindings.get(base.id, ())
            if not bindings or any(kind != 'value' or not isinstance(value, (ast.Dict, ast.List, ast.Tuple))
                                   for kind, value in bindings):
                return []
            displays = [value for _kind, value in bindings]
        else:
            return []
        values = []
        constant = key.value if isinstance(key, ast.Constant) else None
        for display in displays:
            if isinstance(display, ast.Dict):
                for item_key, value in zip(display.keys, display.values):
                    if item_key is None:
                        return []
                    if constant is None or not isinstance(item_key, ast.Constant) or item_key.value == constant:
                        values.append(value)
            elif isinstance(constant, int) and not isinstance(constant, bool) \
                    and -len(display.elts) <= constant < len(display.elts):
                values.append(display.elts[constant])
            else:
                values += list(display.elts)
    else:
        return []
    if not values or not all(isinstance(value, (ast.Attribute, ast.Name, ast.Lambda)) for value in values):
        return []
    found = []
    for value in values:
        inner = [] if isinstance(value, ast.Lambda) else \
            _callee_aliases(value, parsed, owner, attributes, methods, depth + 1)
        found += inner or [value]
    return found


def _bound(call, definition, method) -> bool:
    """A method called through its instance: the instance fills the first parameter."""
    return isinstance(call.func, ast.Attribute) and method and _function_params(definition)[:1] in (['self'], ['cls'])


def _passed(call, definition, param, method, path=()) -> list:
    """(A15.3 F10, A7.9, A11.3) [(expression, path)] of everything a call may pass to ``param`` of
    ``definition``, for the part ``path`` of the parameter's value: its keyword argument, the
    positional argument at its position, every starred argument at or before it (an element), a
    ``*param`` vararg's positional arguments (its elements), a ``**param`` kwarg's keyword values
    (``kw['text']``: the keyword ``text=``) and every ``**mapping`` (a named parameter is its item
    under the parameter's name; a ``**param`` is the mapping itself).  [] when the call passes
    nothing (the default applies).  (Phase D fix 1, A16.1) The returned paths include ``path``."""
    path = tuple(path)
    arguments = definition.args
    found = [(keyword.value, path) for keyword in call.keywords if keyword.arg == param]
    spreads = [keyword.value for keyword in call.keywords if keyword.arg is None]
    if arguments.kwarg is not None and arguments.kwarg.arg == param:
        named = {arg.arg for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs}
        selected = path[0][1] if path and isinstance(path[0], tuple) and isinstance(path[0][1], str) else None
        for keyword in call.keywords:
            if keyword.arg and keyword.arg not in named and (selected is None or keyword.arg == selected):
                found.append((keyword.value, path[1:] if selected is not None or path[:1] in (('values',),) else ()))
        found += [(spread, path) for spread in spreads]
        return found
    params = _function_params(definition)
    shift = 1 if _bound(call, definition, method) else 0
    element = path[1:] if path and (path[0] == 'iter' or isinstance(path[0], tuple)) else ()
    if arguments.vararg is not None and arguments.vararg.arg == param:
        offset = len(params) - shift
        for index, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                found.append((arg.value, ('iter',) + element))
            elif index >= offset:
                found.append((arg, element))
        return found
    explicit = bool(found)
    index = params.index(param) - shift if param in params else -1
    if index >= 0:
        for position, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred) and position <= index:
                found.append((arg.value, ('iter',) + path))
        if not any(isinstance(arg, ast.Starred) for arg in call.args[:index + 1]) and index < len(call.args):
            found.append((call.args[index], path))
            explicit = True
    if not explicit:                 # a **mapping supplies only a parameter the call does not pass (A11.2)
        found += [(spread, (('key', param),) + path) for spread in spreads]
    return found


def _exact(node) -> bool:
    """A value the flows follow exactly: literals and names in tuple / list / set / dict displays."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_exact(item.value if isinstance(item, ast.Starred) else item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _exact(key) for key in node.keys) and all(_exact(value) for value in node.values)
    if isinstance(node, ast.UnaryOp):
        return _exact(node.operand)
    return False


def _module_name(parsed, name, scope) -> bool:
    """An imported module's name as a function sees it: not bound by the function (or an enclosing
    one); a binding of the module scope is a class attribute of the same name (_Module binds class
    bodies there), which a function does not see."""
    owner = parsed.lookup(name, scope)
    return owner is None or owner is parsed.module_scope


def _steps(at) -> tuple:
    """The path steps of an unpacking position (an index per tuple level; None after a starred target)."""
    return tuple('iter' if index is None else ('key', index) for index in at)


def _project(path, step):
    """The path of a part of a value: ``step`` prepended (paths read outside-in)."""
    return (step,) + tuple(path)


class _Flow:
    """The FLOWS of the guard contract (MESSAGES.md 15, A15.3), shared by G1 (_Taint) and G5 (_Sinks).

    ``leaves(node, parsed, scope)`` gives every leaf an expression may carry:
      ('text', node, parsed)               a str literal, also a literal part of an f-string (S1-S4)
      ('enum', module, name)               a G1 enum source reached by name (S5)
      ('param', definition, name, parsed, path)  a parameter's value, decided at the calls (F10)
      ('composed', node, parsed, reason)   a text composition (F8; G1f and the G5 value positions)
    A value that comes from anything the flows do not follow (an event or record value, a call of a
    function outside the scanned modules, an attribute of an arbitrary object) has no leaf: it is no
    text fixed in the source (L-15 lists what that leaves to the run-time checks).

    ``path`` selects a part of a value, outside-in: 'iter' (the elements iteration yields: a
    sequence's elements, a mapping's keys), 'keys', 'values', 'items', ('key', k) (the item at a
    constant key or index).  ``compose`` follows the parts of compositions (F8); without it a
    composition is only reported as a 'composed' leaf (G1f) and its parts are not followed.

    The subclass provides: ``context``; ``_flow_module(name)`` (a parsed src module or None);
    ``_flow_attributes(attr, parsed)`` [(value, parsed, scope, path)] of the class attributes and
    ``self.<attr>`` assignments of the classes the flows follow; ``_flow_class(name, parsed)`` (a class
    of those modules); ``_flow_callees(call, parsed, scope)`` [(parsed, definition)]; and
    ``WHOLE_DICT`` (what a dict display standing as a whole value carries: G1 nothing — its text-key
    values are slots of their own —, G5 every key and value)."""

    WHOLE_DICT = 'all'

    def _memo(self, key, compute):
        """The leaves of a name, an attribute or a function's values, computed once.  A cycle (a name
        bound to itself through other names, a recursive function) is cut where it closes.  A result
        that depends on a frame still being computed is provisional: it is reused while that frame
        runs and dropped when it ends, and only complete results are kept (Tarjan's low-link)."""
        state = self.__dict__.get('_flow_state')
        if state is None:
            state = self.__dict__['_flow_state'] = {'memo': {}, 'active': {}, 'low': [float('inf')],
                                                    'partial': {}, 'pending': []}
        memo, active, low, partial, pending = (state['memo'], state['active'], state['low'], state['partial'],
                                               state['pending'])
        if key in memo:
            return memo[key]
        if key in active:
            low[-1] = min(low[-1], active[key])
            return []
        if key in partial:
            result, depends = partial[key]
            low[-1] = min(low[-1], depends)
            return result
        index = len(active)
        active[key] = index
        low.append(float('inf'))
        mark = len(pending)
        try:
            result = list(dict.fromkeys(compute()))          # a leaf once, however many ways it is reached
        finally:
            del active[key]
            depends = low.pop()
            low[-1] = min(low[-1], depends)
        if depends >= index:
            memo[key] = result
            for member in pending[mark:]:
                partial.pop(member, None)
            del pending[mark:]
        else:
            partial[key] = (result, depends)
            pending.append(key)
        return result

    # ------------------------------------------------------------------ helpers the subclass provides

    def _flow_module(self, name):
        return None

    def _flow_attributes(self, attr, parsed):
        return []

    def _flow_class(self, name, parsed) -> bool:
        return False

    def _flow_callees(self, call, parsed, scope):
        return []

    @staticmethod
    def _method(parsed, definition) -> bool:
        return isinstance(parsed.parents.get(definition), ast.ClassDef)

    # ------------------------------------------------------------------ the leaves

    MAX_PATH = 3               # a deeper selection is taken whole (coarser, never narrower)

    def leaves(self, node, parsed, scope, compose=True, path=(), seen=frozenset()) -> list:
        if node is None:
            return []
        if len(path) > self.MAX_PATH:
            path = tuple(path[:self.MAX_PATH])
        L = self.leaves
        if isinstance(node, ast.Constant):
            return [('text', node, parsed)] if isinstance(node.value, str) else []
        if isinstance(node, ast.JoinedStr):
            found = [('composed', node, parsed, 'f-string')] if not path and _composes(node, scope) else []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.append(('text', value, parsed))
                elif compose and isinstance(value, ast.FormattedValue):
                    found += L(value.value, parsed, scope, compose, (), seen)
            return found
        if isinstance(node, ast.FormattedValue):
            return L(node.value, parsed, scope, compose, (), seen)
        if isinstance(node, (ast.Starred, ast.NamedExpr, ast.Await)):
            return L(node.value, parsed, scope, compose, path, seen)
        if isinstance(node, ast.BinOp):
            reason = None if path else _composes(node, scope)      # a part taken from it is no composition
            found = [('composed', node, parsed, reason)] if reason else []
            if isinstance(node.op, (ast.Add, ast.Mult)) and (compose or path):
                found += L(node.left, parsed, scope, compose, path, seen) + L(node.right, parsed, scope, compose, path, seen)
            elif isinstance(node.op, ast.Mod) and compose:
                found += L(node.left, parsed, scope, compose, (), seen) + L(node.right, parsed, scope, compose, (), seen)
            return found
        if isinstance(node, ast.IfExp):
            return L(node.body, parsed, scope, compose, path, seen) + L(node.orelse, parsed, scope, compose, path, seen)
        if isinstance(node, ast.BoolOp):
            return [leaf for value in node.values for leaf in L(value, parsed, scope, compose, path, seen)]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return self._sequence_leaves(node, parsed, scope, compose, path, seen)
        if isinstance(node, ast.Dict):
            return self._dict_leaves(node, parsed, scope, compose, path, seen)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            if compose is None and not path:
                return []
            inner = parsed.scope_of(node)
            rest = path[1:] if path and (path[0] in ('iter', 'values', 'keys') or isinstance(path[0], tuple)) else ()
            return L(node.elt, parsed, inner, compose, rest, seen)
        if isinstance(node, ast.DictComp):
            inner = parsed.scope_of(node)
            return self._mapping_parts(path, lambda rest: L(node.key, parsed, inner, compose, rest, seen),
                                       lambda rest: L(node.value, parsed, inner, compose, rest, seen),
                                       lambda: self.WHOLE_DICT)
        if isinstance(node, ast.Subscript):
            found = [('composed', node, parsed, 'slice')] if isinstance(node.slice, ast.Slice) and not path else []
            key = node.slice
            if isinstance(key, ast.Slice):                  # a part of the base: its elements or its text
                if compose or path:                         # (G1f: a text slice is the composition itself)
                    found += L(node.value, parsed, scope, compose, _project(path[1:], 'iter') if path else (), seen)
            elif isinstance(key, ast.Constant) and isinstance(key.value, (str, int)) and not isinstance(key.value, bool):
                found += L(node.value, parsed, scope, compose, _project(path, ('key', key.value)), seen)
            else:
                found += L(node.value, parsed, scope, compose, _project(path, 'values'), seen)
            return found
        if isinstance(node, ast.Name):
            return self._name_leaves(node.id, parsed, scope, compose, path, seen)
        if isinstance(node, ast.Attribute):
            return self._attribute_leaves(node, parsed, scope, compose, path, seen)
        if isinstance(node, ast.Call):
            return self._call_leaves(_Call(node, node.func, node.args, node.keywords), parsed, scope, compose, path, seen)
        return []

    def _sequence_leaves(self, node, parsed, scope, compose, path, seen):
        L = self.leaves
        if compose is None and not path:        # G1f: a container standing whole is no composed text
            return []
        step, rest = (path[0], path[1:]) if path else (None, ())
        starred = any(isinstance(item, ast.Starred) for item in node.elts)
        if isinstance(step, tuple) and isinstance(step[1], int) and not isinstance(node, ast.Set) and not starred:
            index = step[1]
            if not -len(node.elts) <= index < len(node.elts):
                return []
            return L(node.elts[index], parsed, scope, compose, rest, seen)
        found = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                found += L(item.value, parsed, scope, compose, _project(rest, 'iter'), seen)
            else:
                found += L(item, parsed, scope, compose, rest, seen)
        return found

    @staticmethod
    def _mapping_parts(path, keys, values, whole):
        """Select the parts of a mapping display by ``path``: 'iter' / 'keys' -> keys, 'values' /
        ('key', k) -> values, 'items' -> both, no path -> what a whole dict carries."""
        if not path:
            return keys(()) + values(()) if whole() == 'all' else []
        step, rest = path[0], path[1:]
        if step in ('iter', 'keys'):
            return keys(rest)
        if step == 'items':
            return keys(rest) + values(rest)
        return values(rest)

    def _dict_leaves(self, node, parsed, scope, compose, path, seen):
        L = self.leaves
        step, rest = (path[0], path[1:]) if path else (None, ())
        if isinstance(step, tuple):
            found = []
            for key, value in zip(node.keys, node.values):
                if key is None:                                              # {**mapping}
                    found += L(value, parsed, scope, compose, path, seen)
                elif not isinstance(key, ast.Constant) or key.value == step[1]:
                    found += L(value, parsed, scope, compose, rest, seen)
            return found
        found = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                found += L(value, parsed, scope, compose, path, seen)
                continue
            found += self._mapping_parts(path, lambda r, k=key: L(k, parsed, scope, compose, r, seen),
                                         lambda r, v=value: L(v, parsed, scope, compose, r, seen),
                                         lambda: self.WHOLE_DICT)
        return found

    # ------------------------------------------------------------------ names, attributes, modules

    @staticmethod
    def _dict_valued(bindings) -> bool:
        return bool(bindings) and all(isinstance(value, (ast.Dict, ast.DictComp)) for kind, value in bindings
                                      if value is not None)

    def _name_leaves(self, name, parsed, scope, compose, path, seen):
        owner = parsed.lookup(name, scope)
        if owner is None:
            if name in parsed.from_imports:
                module, original = parsed.from_imports[name]
                return self._module_leaves(module, original, compose, path, seen)
            return []
        key = ('name', parsed.name, id(owner), name, compose, path)
        return self._memo(key, lambda: self._name_values(name, parsed, owner, compose, path, seen))

    def _name_values(self, name, parsed, owner, compose, path, seen):
        found = []
        bindings = owner.bindings.get(name, ())
        if owner is parsed.module_scope and name in self.context.enum_sources().get(parsed.name, ()) \
                and not all(_exact(value) for _kind, value in bindings if value is not None):
            # S5: an enum source whose value the flows cannot follow exactly (records, calls);
            # a display of literals is followed instead, part by part
            keys_only = path and (path[0] == 'keys' or path[0] == 'iter' and self._dict_valued(bindings))
            if not keys_only:
                found.append(('enum', parsed.name, name))
        if name in owner.params and owner is not parsed.module_scope:
            found.append(('param', owner.node, name, parsed, path))
            found += self.leaves(_param_default(owner.node, name), parsed, owner.parent, compose, path, seen)
        for kind, value in bindings:
            if value is not None and kind in ('value', 'with'):
                found += self.leaves(value, parsed, owner, compose, path, seen)
        for kind, value, at in owner.positions.get(name, ()):
            found += self._leaves_at(value, at, parsed, owner, compose, path, seen, iterate=kind == 'iter')
        for how, value, key in owner.mutations.get(name, ()):
            found += self._mutation_leaves(how, value, key, parsed, owner, compose, path, seen)
        return found

    def _mutation_leaves(self, how, value, key, parsed, scope, compose, path, seen):
        """(F5) What a container mutation puts in, selected by ``path`` like a display's items: an
        element / the elements of an iterable (a sequence), an item under ``key`` / the items of a
        mapping (a dict: standing whole it carries what WHOLE_DICT says; G1f none at all)."""
        L = self.leaves
        constant = key.value if isinstance(key, ast.Constant) else None
        sequence = how in ('element', 'elements') or isinstance(constant, int) and not isinstance(constant, bool)
        if not path:
            if compose is None or (not sequence and self.WHOLE_DICT != 'all'):
                return []
            if how == 'element' or how == 'item':
                return L(value, parsed, scope, compose, (), seen) + \
                    (L(key, parsed, scope, compose, (), seen) if how == 'item' and not sequence else [])
            return L(value, parsed, scope, compose, ('iter',) if how == 'elements' else ('items',), seen)
        step, rest = path[0], path[1:]
        if how == 'element':
            return L(value, parsed, scope, compose, rest, seen)
        if how == 'elements':
            return L(value, parsed, scope, compose, ('iter',) + tuple(rest), seen)
        if how == 'items':
            return L(value, parsed, scope, compose, path, seen)
        if isinstance(step, tuple):                      # ('key', k): the item under k only
            return L(value, parsed, scope, compose, rest, seen) if constant is None or constant == step[1] else []
        if step in ('keys',) or step == 'iter' and not sequence:
            return L(key, parsed, scope, compose, rest, seen) if key is not None else []
        if step == 'items':
            return L(value, parsed, scope, compose, rest, seen) + (L(key, parsed, scope, compose, rest, seen)
                                                                  if key is not None else [])
        return L(value, parsed, scope, compose, rest, seen)

    def _leaves_at(self, node, at, parsed, scope, compose, path, seen, iterate=False):
        """The leaves of what an unpacking target at position ``at`` receives from ``node`` (of each
        element of ``node`` when ``iterate``: a for target), then the part ``path`` of it (F5)."""
        L = self.leaves
        if iterate:
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                found = []
                for item in node.elts:
                    if isinstance(item, ast.Starred):
                        found += self._leaves_at(item.value, at, parsed, scope, compose, path, seen, iterate=True)
                    else:
                        found += self._leaves_at(item, at, parsed, scope, compose, path, seen)
                return found
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                return self._leaves_at(node.elt, at, parsed, parsed.scope_of(node), compose, path, seen)
            if isinstance(node, ast.Call):
                name, base = _func_name(node)
                plain = isinstance(node.func, ast.Name) and name not in parsed.from_imports and node.args
                if plain and name == 'enumerate' and at and at[0] is not None:
                    if at[0] == 0:
                        return []                # the counter: a number
                    return self._leaves_at(node.args[0], at[1:], parsed, scope, compose, path, seen, True)
                if plain and name == 'zip' and at and at[0] is not None and at[0] < len(node.args):
                    return self._leaves_at(node.args[at[0]], at[1:], parsed, scope, compose, path, seen, True)
                if plain and name == 'range':
                    return []
                if name == 'items' and isinstance(node.func, ast.Attribute) and not node.args:
                    if at and at[0] == 0:
                        return L(node.func.value, parsed, scope, compose, _project(path, 'keys'), seen)
                    if at and at[0] == 1:
                        return L(node.func.value, parsed, scope, compose, _project(path, 'values'), seen)
            return L(node, parsed, scope, compose, ('iter',) + _steps(at) + tuple(path), seen)
        if not at:
            return L(node, parsed, scope, compose, path, seen)
        step, rest = at[0], at[1:]
        if step is None:
            return L(node, parsed, scope, compose, _steps(at) + tuple(path), seen)
        if isinstance(node, (ast.Tuple, ast.List)) and not any(isinstance(item, ast.Starred) for item in node.elts):
            if step >= len(node.elts):
                return []
            return self._leaves_at(node.elts[step], rest, parsed, scope, compose, path, seen)
        if isinstance(node, ast.IfExp):
            return self._leaves_at(node.body, at, parsed, scope, compose, path, seen) + \
                self._leaves_at(node.orelse, at, parsed, scope, compose, path, seen)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'divmod':
            return []
        return L(node, parsed, scope, compose, _steps(at) + tuple(path), seen)

    def _module_leaves(self, module, name, compose, path, seen):
        other = self._flow_module(module)
        if other is None:
            return []
        return self._name_leaves(name, other, other.module_scope, compose, path, seen)

    def _attribute_leaves(self, node, parsed, scope, compose, path, seen):
        base = node.value
        if isinstance(base, ast.Name):
            if base.id in ('self', 'cls') or (parsed.lookup(base.id, scope) is parsed.module_scope
                                              and self._flow_class(base.id, parsed)):
                return self._self_leaves(node.attr, parsed, compose, path, seen)
            if base.id in parsed.imports and _module_name(parsed, base.id, scope):
                return self._module_leaves(parsed.imports[base.id], node.attr, compose, path, seen)
        if node.attr in ('label', 'mode'):                  # the text fields of an enum record (7.2 G1b)
            return self.leaves(base, parsed, scope, compose, path, seen)
        if node.attr == 'name' and _path_call(base) and compose is not None:
            # (Phase D fix 1, A16.3, F11) Path(x).name is a part of x: no source may reach it
            return [leaf for arg in base.args for leaf in self.leaves(arg, parsed, scope, compose, (), seen)]
        return []

    def _self_leaves(self, attr, parsed, compose, path, seen):
        """``self.<attr>`` / ``cls.<attr>`` / ``Class.<attr>`` / ``getattr(self, '<attr>')`` (F3)."""
        key = ('attr', parsed.name, attr, compose, path)

        def compute():
            found = []
            for value, owner, scope, at in self._flow_attributes(attr, parsed):
                if at and at[0] in ('element', 'elements', 'item', 'items'):
                    found += self._mutation_leaves(at[0], value, at[1], owner, scope, compose, path, seen)
                else:
                    found += self._leaves_at(value, at, owner, scope, compose, path, seen)
            return found
        return self._memo(key, compute)

    # ------------------------------------------------------------------ calls

    def _returned_values(self, owner, definition):
        """The values a function gives: its own ``return`` / ``yield`` values, a lambda's body."""
        if isinstance(definition, ast.Lambda):
            return [definition.body]
        own = owner.scopes.get(definition)
        return [node.value for node in ast.walk(definition)
                if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)) and node.value is not None
                and owner.scope_of(node) is own]

    def _iterate_leaves(self, call, name, parsed, scope, compose, path, seen):
        """F7: enumerate / zip / map / filter / sorted / reversed / list / tuple / set / frozenset / iter
        give a sequence of the elements of their iterables (enumerate's counter and the function of
        map / filter are no text); dict() the mapping of its argument and keywords; next() / min() /
        max() one element (or one argument, or the default)."""
        L = self.leaves
        args = list(call.args)
        found = []
        if compose is None and not path and name not in ('next', 'min', 'max'):
            return []                                  # G1f: a sequence standing whole
        if name == 'dict':
            if not path and self.WHOLE_DICT != 'all':      # a mapping standing whole (G1: its text keys are slots)
                return []
            for arg in args:
                found += L(arg, parsed, scope, compose, path, seen)
            for keyword in call.keywords:
                if keyword.arg is None:
                    found += L(keyword.value, parsed, scope, compose, path, seen)
                elif not path or path[0] in ('values', 'items') or path[0] == ('key', keyword.arg):
                    found += L(keyword.value, parsed, scope, compose, path[1:], seen)
            return found
        if name in ('next', 'min', 'max'):
            iterables = args[:1] if name == 'next' or len(args) == 1 else []
            for arg in iterables:
                found += L(arg, parsed, scope, compose, ('iter',) + tuple(path), seen)
            for arg in (args[1:] if name == 'next' else [] if len(args) == 1 else args):
                found += L(arg, parsed, scope, compose, path, seen)
            for keyword in call.keywords:
                if keyword.arg == 'default':
                    found += L(keyword.value, parsed, scope, compose, path, seen)
            return found
        if name in ('map', 'filter'):
            args = args[1:]
        rest = path[1:] if path and name not in ('enumerate', 'zip') else ()
        for arg in args:
            found += L(arg, parsed, scope, compose, ('iter',) + tuple(rest), seen)
        return found

    def _aliases(self, func, parsed, scope):
        """(A16.3) The callables an aliased callee stands for (_callee_aliases over the flows'
        attributes); [] when the callee is no alias."""
        return _callee_aliases(func, parsed, scope, lambda attr: self._flow_attributes(attr, parsed),
                               self._method_names(parsed))

    def _method_names(self, parsed):
        return frozenset(name for name, items in parsed.functions.items()
                         if any(self._method(parsed, item) for item in items))

    def _call_leaves(self, call, parsed, scope, compose, path, seen):
        L = self.leaves
        node = call.node
        aliases = self._aliases(call.func, parsed, scope)
        if aliases:
            # (A16.3, F1 / F3 for callees) `log = self.append_log; log(x)`, `render = messages.show`,
            # `getattr(self, 'm')(x)`, a dict of lambdas: the call is a call of what the alias holds
            found = []
            for target in aliases:
                found += self._call_leaves(_Call(node, target, call.args, call.keywords, call.callback), parsed,
                                           scope, compose, path, seen)
            return list(dict.fromkeys(found))
        name, base = _func_name(call)
        func = call.func
        if isinstance(func, ast.Name) and name in parsed.from_imports:
            resolved = parsed.from_imports[name]
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            resolved = (parsed.imports.get(base, base), name)
        else:
            resolved = (base, name)
        messages_call = resolved[0] == 'messages' or (isinstance(func, ast.Name) and name in parsed.from_imports
                                                      and parsed.from_imports[name][0] == 'messages')
        if messages_call or (isinstance(func, ast.Name) and name in TYPED_VALUE and name not in parsed.functions):
            if name == 'template':                           # S6: a zh catalog template as plain text
                return [('enum', 'messages', 'template')]
            if not compose:                                  # a typed value: no composition at the slot (A1.1)
                return []
            if name in TYPED_VALUE:                          # F9 carriers: raw / cut / masked / ... keep their text
                return [leaf for value, _param in _typed_values(call, name)
                        for leaf in L(value, parsed, scope, compose, path, seen)]
            if name in SHOWN_ARGUMENT:                       # F11: they give fixed text back unchanged
                # (A16.1, ZH-CONV-1) the argument by position or by its keyword
                return [leaf for value, at in _call_arguments(call, 0, (SHOWN_ARGUMENT[name],))
                        for leaf in L(value, parsed, scope, compose, at + tuple(path), seen)]
            return []                                        # msg(), value(), ...: key-based or typed
        if resolved[0] == 'textwrap' and name in TEXTWRAP_CALLS:
            # (A16.3, F8) textwrap.shorten / fill / wrap / dedent / indent compose the text they get
            found = [('composed', node, parsed, 'textwrap')] if not path else []
            if compose:
                parts = _call_arguments(call, 0, ('text',))
                parts += _call_arguments(call, 1, ('prefix',)) if name == 'indent' else []
                parts += [(keyword.value, ()) for keyword in call.keywords if keyword.arg == 'placeholder']
                for part, at in parts:
                    found += L(part, parsed, scope, compose, at, seen)
            return found
        if resolved in (('copy', 'copy'), ('copy', 'deepcopy')) and call.args:
            return L(call.args[0], parsed, scope, compose, path, seen)      # (A16.3, F7) the same value
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'str' \
                and name in STR_METHODS and call.args and parsed.lookup('str', scope) is None:
            # (A16.3, F8) str.strip(x), str.format(f, x): the str method called unbound
            bound = _unbound_str_call(call, parsed)
            return self._call_leaves(_Call(node, bound.func, bound.args, bound.keywords), parsed, scope, compose,
                                     path, seen)
        if resolved in NEUTRAL_CALLS or (base, name) in NEUTRAL_CALLS:
            # (Phase D fix 1, A16.3; F11) urls.display / os.fspath give a part of their argument as
            # language-neutral data: no source may reach them, so the flows follow the argument
            if compose is None:
                return []
            return [leaf for arg in call.args for leaf in L(arg, parsed, scope, compose, (), seen)]
        if resolved[0] in ('i18n', 'math', 'os', 'time', 're', 'json', 'urls', 'urltools') and name != 'sub':
            return []
        if resolved == ('re', 'sub') or (name == 'sub' and base == 're'):
            if not compose:
                return [('composed', node, parsed, 're.sub')]
            parts = list(call.args[1:3]) + [keyword.value for keyword in call.keywords if keyword.arg in ('repl', 'string')]
            return [('composed', node, parsed, 're.sub')] + \
                [leaf for part in parts for leaf in L(part, parsed, scope, compose, (), seen)]
        if name in RECORD_CALLS and (base == 'formats' or isinstance(func, ast.Name) and parsed.name == 'formats'):
            return [('enum', 'formats', name)]
        if name == 'getattr' and isinstance(func, ast.Name) and len(call.args) >= 2 \
                and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str):
            target = ast.Attribute(value=call.args[0], attr=call.args[1].value, ctx=ast.Load())
            parsed.parents[target] = node
            found = self._attribute_leaves(target, parsed, scope, compose, path, seen)
            return found + (L(call.args[2], parsed, scope, compose, path, seen) if len(call.args) > 2 else [])
        callees = self._flow_callees(call, parsed, scope)
        if callees:
            found = []
            for owner, definition in callees:
                inner = owner.scopes.get(definition)

                def compute(owner=owner, definition=definition, inner=inner):
                    return [leaf for value in self._returned_values(owner, definition)
                            for leaf in L(value, owner, inner, compose, path, seen)]
                for leaf in self._memo(('returns', id(definition), compose, path), compute):
                    if leaf[0] == 'param' and leaf[1] is definition:            # substitute the argument
                        method = self._method(owner, definition)
                        for argument, at in _passed(call, definition, leaf[2], method, leaf[4]):
                            found += L(argument, parsed, scope, compose, at, seen)
                    else:
                        found.append(leaf)
            return list(dict.fromkeys(found))
        if isinstance(func, ast.Name):
            if name in ('str', 'repr', 'format', 'ascii'):
                reason = _composes(node, scope) if name in ('str', 'format') and not path else None
                found = [('composed', node, parsed, reason)] if reason else []
                if compose and name in ('str', 'format'):                # repr(value): the 2.1 form, no text
                    found += [leaf for arg in call.args[:1] for leaf in L(arg, parsed, scope, compose, (), seen)]
                return found
            if name in ITERATE_CALLS:
                return self._iterate_leaves(call, name, parsed, scope, compose, path, seen)
            return []                                         # a constructor or a function of an unscanned module
        if isinstance(func, ast.Attribute):
            receiver = func.value
            if name in CONTAINER_METHODS:
                if name in ('get', 'pop', 'setdefault'):
                    key = call.args[0] if call.args else None
                    step = ('key', key.value) if isinstance(key, ast.Constant) and isinstance(key.value, (str, int)) \
                        else 'values'
                    found = L(receiver, parsed, scope, compose, _project(path, step), seen)
                    for default in call.args[1:2] + [keyword.value for keyword in call.keywords if keyword.arg == 'default']:
                        found += L(default, parsed, scope, compose, path, seen)
                    return found
                if name == 'copy':
                    return L(receiver, parsed, scope, compose, path, seen)
                if name == 'popitem':
                    return L(receiver, parsed, scope, compose, _project(path[1:] if path else (), 'items'), seen)
                step = {'values': 'values', 'keys': 'keys', 'items': 'items'}[name]
                return L(receiver, parsed, scope, compose, _project(path[1:] if path else (), step), seen)
            if name in STR_METHODS:
                reason = None if path else _composes(node, scope)
                found = [('composed', node, parsed, reason)] if reason else []
                if compose:                      # the parts of the result: the receiver and the inserted texts
                    parts = [(receiver, ())]
                    if name in ('format', 'format_map'):
                        parts += [(arg, ('values',) if name == 'format_map' else ()) for arg in call.args]
                        parts += [(keyword.value, ()) for keyword in call.keywords]
                    elif name == 'join':
                        parts += [(arg, ('iter',)) for arg in call.args[:1]]
                    elif name == 'replace' or name in ('ljust', 'rjust', 'center'):
                        parts += [(arg, ()) for arg in call.args[1:2]]
                    for part, at in parts:
                        found += L(part, parsed, scope, compose, at, seen)
                return found
        return []


def _class_names(parsed) -> frozenset:
    """The classes a module defines (any depth)."""
    cached = parsed.__dict__.get('_class_names')
    if cached is None:
        cached = frozenset(node.name for node in ast.walk(parsed.tree) if isinstance(node, ast.ClassDef))
        parsed.__dict__['_class_names'] = cached
    return cached


def _is_property(definition) -> bool:
    """A getter decorated `@property` / `@functools.cached_property` / `@cached_property` (A16.2)."""
    for decorator in definition.decorator_list:
        name = decorator.id if isinstance(decorator, ast.Name) else \
            decorator.attr if isinstance(decorator, ast.Attribute) else None
        if name in ('property', 'cached_property'):
            return True
    return False


def _attribute_index(parsed) -> dict:
    """(A15.3 F3) {attr: [(value, parsed, scope, at)]} of a module: the assignments of its class bodies
    and every assignment to ``self.<attr>`` / ``cls.<attr>`` / ``<Class>.<attr>`` — also as an element
    of a tuple / list target (``at``: the position), augmented (the composition, A16.2) or annotated —,
    every ``setattr(self | cls | <Class>, '<attr>', value)``, and the return values of a ``@property``
    getter (A16.2)."""
    cached = parsed.__dict__.get('_attribute_index')
    if cached is not None:
        return cached
    index = defaultdict(list)
    classes = _class_names(parsed)

    def attribute_of(expression):
        if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name) \
                and (expression.value.id in ('self', 'cls') or expression.value.id in classes):
            return expression.attr
        return None

    def add(target, value, scope, at=(), plain=False):
        if plain and isinstance(target, ast.Name):
            index[target.id].append((value, parsed, scope, at))
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                and (target.value.id in ('self', 'cls') or target.value.id in classes):
            index[target.attr].append((value, parsed, scope, at))
        elif isinstance(target, (ast.Tuple, ast.List)):
            starred = any(isinstance(item, ast.Starred) for item in target.elts)
            for position, item in enumerate(target.elts):
                add(item, value, scope, at + (None if starred else position,), plain)
        elif isinstance(target, ast.Starred):
            add(target.value, value, scope, at, plain)

    for node in ast.walk(parsed.tree):
        for attr, how, value, key in _mutation(node, attribute_of, parsed.parents):  # F5: self.<attr>.append(v), ...
            index[attr].append((value, parsed, parsed.scope_of(node), (how, key)))
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        add(target, statement.value, parsed.scope_of(statement), plain=True)
                elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)) and statement.value is not None:
                    add(statement.target, _assigned_value(statement, parsed.parents), parsed.scope_of(statement),
                        plain=True)
                elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_property(statement):
                    # (Phase D fix 1, A16.2) a @property of the class: `self.<name>` is what its getter returns
                    own = parsed.scopes.get(statement)
                    for item in ast.walk(statement):
                        if isinstance(item, ast.Return) and item.value is not None and parsed.scope_of(item) is own:
                            index[statement.name].append((item.value, parsed, own, ()))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and node.value is not None:
            scope = parsed.scope_of(node)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                add(target, _assigned_value(node, parsed.parents), scope)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'setattr' \
                and len(node.args) == 3 and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str) and isinstance(node.args[0], ast.Name) \
                and (node.args[0].id in ('self', 'cls') or node.args[0].id in classes):
            index[node.args[1].value].append((node.args[2], parsed, parsed.scope_of(node), ()))
    parsed.__dict__['_attribute_index'] = dict(index)
    return parsed.__dict__['_attribute_index']


def _lambda_bindings(name, parsed, scope) -> list:
    """The lambdas a local name is bound to, when every binding of the name is one."""
    owner = parsed.lookup(name, scope)
    if owner is None or name in owner.params:
        return []
    values = [value for _kind, value in owner.bindings.get(name, ())]
    return values if values and all(isinstance(value, ast.Lambda) for value in values) else []


def _module_callees(call, parsed, scope, module_of) -> list:
    """(A15.3 F9) [(parsed, definition)] a call reaches inside one module (G1): a lambda called in
    place, or the lambdas a local name is bound to; a bare name -> the functions of that name in the
    module (not its methods), or the module-level function a `from` import names; `<module>.f` of an
    imported src module -> its module-level f; any other receiver (`self.f`, `obj.f`) -> the methods
    named f of the module."""
    func = call.func
    if isinstance(func, ast.Lambda):
        return [(parsed, func)]

    def toplevel(other, name):
        return [(other, item) for item in other.tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name] if other else []

    if isinstance(func, ast.Name):
        lambdas = _lambda_bindings(func.id, parsed, scope)
        if lambdas:
            return [(parsed, item) for item in lambdas]
        if func.id in parsed.from_imports and parsed.lookup(func.id, scope) is None:
            module, original = parsed.from_imports[func.id]
            return toplevel(module_of(module), original)
        return [(parsed, item) for item in parsed.functions.get(func.id, ())
                if not isinstance(parsed.parents.get(item), ast.ClassDef)]
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name) and base.id in parsed.imports and _module_name(parsed, base.id, scope):
            return toplevel(module_of(parsed.imports[base.id]), func.attr)
        return [(parsed, item) for item in parsed.functions.get(func.attr, ())
                if isinstance(parsed.parents.get(item), ast.ClassDef)]
    return []


class _Taint(_Flow):
    """G1b: does an expression carry an allow-listed literal or an enum source (MESSAGES.md 7.2, A15)?
    The flows of A15.3 over the module (its names, the attributes of its classes, its functions) and
    the src modules it names (`module.NAME`, `module.f`, `from` imports); parameters are substituted
    at the calls and, at a slot, become slots of the calls (g1_slots)."""

    WHOLE_DICT = 'none'        # a dict display's text-key values are slots of their own (7.2 G1b)

    def __init__(self, context, parsed):
        self.context = context
        self.parsed = parsed
        self.allowed = context.allowed_literals(parsed.name)
        self.enum = context.enum_sources()

    def _flow_module(self, name):
        if name == self.parsed.name:
            return self.parsed
        if name in G1_EXCLUDED:
            return None
        for folder in (self.parsed.path.parent, self.context.enum_src, self.context.src):
            if (Path(folder) / f'{name}.py').is_file():
                try:
                    return self.context.module(name, folder)
                except (OSError, SyntaxError, ValueError):
                    return None
        return None

    def _flow_attributes(self, attr, parsed):
        return _attribute_index(parsed).get(attr, [])

    def _flow_class(self, name, parsed) -> bool:
        return name in _class_names(parsed)

    def _flow_callees(self, call, parsed, scope):
        return _module_callees(call, parsed, scope, self._flow_module)

    def source(self, leaf) -> bool:
        """A G1b source (A15.2): an allow-listed literal of the module that writes it, or an enum source."""
        if leaf[0] == 'enum':
            return True
        if leaf[0] == 'text':
            return leaf[1].value in self.context.allowed_literals(leaf[2].name)
        return False

    def expr(self, node, scope, seen=frozenset(), path=()):
        """The direct flows (A15.3 F1-F7, F9, F10): the value is, or is a part of, a source."""
        return any(self.source(leaf) for leaf in self.leaves(node, self.parsed, scope, False, path))

    def composed(self, node, scope, seen=frozenset(), path=()):
        """Also through compositions (A15.3 F8; A11.1 for typed-call arguments, every slot since A15)."""
        return any(self.source(leaf) for leaf in self.leaves(node, self.parsed, scope, True, path))


def _branches(node):
    """The expressions a slot may receive: IfExp / BoolOp branches, else the node."""
    if isinstance(node, ast.IfExp):
        return _branches(node.body) + _branches(node.orelse)
    if isinstance(node, ast.BoolOp):
        return [item for value in node.values for item in _branches(value)]
    return [node]


def _numeric(node) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.Call):
        name, base = _func_name(node)
        return name in ('len', 'int', 'float', 'round', 'abs', 'sum', 'min', 'max', 'ceil', 'floor') or base == 'math'
    if isinstance(node, ast.BinOp):
        return _numeric(node.left) or _numeric(node.right)
    if isinstance(node, ast.UnaryOp):
        return _numeric(node.operand)
    return False


def _typed_call(node) -> bool:
    if not isinstance(node, (ast.Call, _Call)):
        return False
    name, base = _func_name(node)
    return name in TYPED and (isinstance(node.func, ast.Name) or base == 'messages')


NUMERIC_SPEC = re.compile(r'[dfge%n]$')       # the G5 rule for an f-string hole with a numeric format spec


def _number_expression(node) -> bool:
    """A name, attribute, number or arithmetic of such (I-1 amendment A1: the value of a formatted number)."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)) and \
            _number_expression(node.left) and _number_expression(node.right)
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.USub, ast.UAdd)) and _number_expression(node.operand)
    return _numeric(node)


def _formatted_number(node) -> bool:
    """An f-string that is only holes, each a number expression with a constant numeric format spec
    (``f'{mb:.1f}'``, ``f'{t:g}'``: the formatted-number params MESSAGES.md 2.1 prescribes).  A
    numeric spec raises on text, so no text can be composed this way (I-1 amendment A1)."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return False
    for value in node.values:
        if not isinstance(value, ast.FormattedValue) or value.format_spec is None or value.conversion != -1:
            return False
        parts = value.format_spec.values
        if not all(isinstance(part, ast.Constant) and isinstance(part.value, str) for part in parts):
            return False
        if not NUMERIC_SPEC.search(''.join(part.value for part in parts)) or not _number_expression(value.value):
            return False
    return True


def _id_table(parsed, node) -> bool:
    """A module-level id-keyed table (I-1 amendment A1): a dict display that is the whole value of a
    module-level assignment, whose keys and values are all str literals and whose keys are exactly the
    members of a module-level tuple / list / set of str literals of the same module (the ids it labels,
    e.g. 1.8.1's naming.LABELS over naming.NAMING).  Its keys are ids, not text fields; its values stay enum
    sources, so every use of them in a text slot is still checked by G1b."""
    parent = parsed.parents.get(node)
    if not isinstance(parent, (ast.Assign, ast.AnnAssign)) or parsed.parents.get(parent) is not parsed.tree:
        return False
    if not node.keys or not all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                                for item in list(node.keys) + list(node.values)):
        return False
    keys = [item.value for item in node.keys]
    for statement in parsed.tree.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, (ast.Tuple, ast.List, ast.Set)):
            members = statement.value.elts
            if members and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in members) \
                    and sorted(item.value for item in members) == sorted(keys) and len(set(keys)) == len(keys):
                return True
    return False


def _composes(node, scope) -> str | None:
    """The G1f reason when ``node`` composes text (MESSAGES.md 7.2), else None."""
    if _typed_call(node):
        return None
    if isinstance(node, ast.JoinedStr):
        if _formatted_number(node):
            return None
        return 'f-string' if any(isinstance(value, ast.FormattedValue) for value in node.values) else None
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            if (_numeric(node.left) and not isinstance(node.right, (ast.Constant, ast.JoinedStr))) or \
                    (_numeric(node.right) and not isinstance(node.left, (ast.Constant, ast.JoinedStr))):
                return None
            return '+'
        if isinstance(node.op, ast.Mult):
            # (Phase D fix 1, A16.4) `*` repeats a text (F8): text * number is valid, so unlike `+` a
            # number on one side proves nothing; arithmetic only when both factors are numbers
            return None if _numeric(node.left) and _numeric(node.right) else '*'
        if isinstance(node.op, ast.Mod) and not _numeric(node.left):
            return '%'
        return None
    if isinstance(node, ast.Call):
        name, base = _func_name(node)
        if isinstance(node.func, ast.Attribute) and name in STR_METHODS:
            # (A16.4, F8) every str method gives a new text made of the receiver (A15.4 G1f: a
            # composition the slot's value is), also called unbound (`str.strip(x)`, A16.3)
            return '.' + name
        if name == 'sub' and base == 're':
            return 're.sub'
        if isinstance(node.func, ast.Name) and name == 'format' and node.args:
            # (A16.4, F8) format(x) gives a new text; a numeric format spec formats a number (A1.2)
            spec = node.args[1] if len(node.args) > 1 else None
            numeric = isinstance(spec, ast.Constant) and isinstance(spec.value, str) and NUMERIC_SPEC.search(spec.value)
            return None if numeric else 'format()'
        if isinstance(node.func, ast.Name) and name == 'str':
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Name) and scope is not None:
                owner = scope
                while owner is not None:
                    if argument.id in owner.exceptions:
                        return None
                    owner = owner.parent
            return 'str() (a path param is os.fspath(path), an exception message_of(error))'
        return None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return 'slice'
    return None


def _function_params(definition) -> list:
    arguments = definition.args
    return [arg.arg for arg in arguments.posonlyargs + arguments.args]


def _param_default(function, name):
    """(final review 2, F1, amendment A14.1) The default value of a parameter, or None.  A caller that
    leaves the argument out supplies it, so the guards treat it as a binding of the parameter."""
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return None
    arguments = function.args
    positional = arguments.posonlyargs + arguments.args
    offset = len(positional) - len(arguments.defaults)
    for index, arg in enumerate(positional):
        if arg.arg == name:
            return arguments.defaults[index - offset] if index >= offset else None
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if arg.arg == name:
            return default
    return None


TYPED_VALUE = {'raw': 'text', 'cut': 'value', 'masked': 'value', 'stripped': 'value', 'clauses': 'parts',
               'message_of': 'error'}       # the text argument of each typed call other than msg (A1.1, A6)


def _typed_values(node, name) -> list:
    """(expression, param) of the text argument of a typed call other than msg(), bare or as
    messages.<name>: the first positional argument or its keyword; each element of a clauses() list
    display; never masked()'s pairs or cut()'s n (I-1 amendments A1.1 and A6)."""
    param = TYPED_VALUE[name]
    value = node.args[0] if node.args else next((item.value for item in node.keywords if item.arg == param), None)
    if value is None:
        return []
    if name == 'clauses' and isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return [(item.value if isinstance(item, ast.Starred) else item, param) for item in value.elts]
    return [(value.value if isinstance(value, ast.Starred) else value, param)]


def _g1_sites(parsed) -> list:
    """Every call of a module for G1 (_call_sites: the callback forms included, A15.4) and, for an
    aliased callee (`say = self.emit`, `write = self.store.create_job`, `getattr(self, 'emit')`), the
    same call of each callable the alias holds (Phase D fix 1, A16.3)."""
    cached = parsed.__dict__.get('_g1_sites')
    if cached is not None:
        return cached
    attributes = _attribute_index(parsed)
    methods = frozenset(name for name, items in parsed.functions.items()
                        if any(isinstance(parsed.parents.get(item), ast.ClassDef) for item in items))
    sites = []
    for site in _call_sites(parsed.tree):
        sites.append(site)
        for target in _callee_aliases(site.func, parsed, parsed.scope_of(site.node),
                                      lambda attr: attributes.get(attr, []), methods):
            sites.append(_Call(site.node, target, site.args, site.keywords, site.callback))
    parsed.__dict__['_g1_sites'] = sites
    return sites


def text_slots(parsed, context, confirmed=None) -> list:
    """(expression, slot description, strict) of every text slot of a module (G1b); strict marks the
    slots G1f checks outside core modules (raise, emits, store writers), and is None for the text
    argument of a typed call other than msg() (raw(text), cut(value), clauses(parts), message_of(error),
    ...; wherever the call stands): G1b checks them, G1f accepts them (MESSAGES.md 7.2 G1f; I-1
    amendments A1.1 and A6).  ``confirmed`` (A15.4): the (id(definition), param) whose parameter
    reaches a slot (g1_slots); a positional argument for a parameter with a TEXT_KEYS name is then a
    slot only when its parameter is confirmed, or belongs to a module the flows do not enter
    (FOREIGN_EXCLUDED: the message API, ui_theme) — None keeps the 7.2 name rule as it was."""
    slots = []
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Dict):
            if _id_table(parsed, node):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in TEXT_KEYS:
                    slots.append((value, f"dict '{key.value}'", False))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) \
                        and target.slice.value in TEXT_KEYS:
                    slots.append((_assigned_value(node, parsed.parents), f"['{target.slice.value}'] =", False))
                elif isinstance(target, ast.Attribute) and target.attr in TEXT_KEYS:
                    # (Phase D fix 1, A16.4, Q4) `obj.title = v` / `item.reason = v`: a text field of an
                    # object (engine's queue items are emitted field by field)
                    slots.append((_assigned_value(node, parsed.parents), f'.{target.attr} =', False))
                elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(node, ast.Assign):
                    values = node.value.elts if isinstance(node.value, (ast.Tuple, ast.List)) \
                        and len(node.value.elts) == len(target.elts) else None
                    for position, item in enumerate(target.elts):
                        if isinstance(item, ast.Attribute) and item.attr in TEXT_KEYS:
                            slots.append((values[position] if values else node.value, f'.{item.attr} =', False))
                        elif isinstance(item, ast.Subscript) and isinstance(item.slice, ast.Constant) \
                                and item.slice.value in TEXT_KEYS:
                            slots.append((values[position] if values else node.value, f"['{item.slice.value}'] =",
                                          False))
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for argument in node.exc.args:
                slots.append((argument, 'raise', True))
            for keyword in node.exc.keywords:
                slots.append((keyword.value, 'raise', True))
    for node in _g1_sites(parsed):                  # every call, the callback forms and aliases included
        if True:
            name, base = _func_name(node)
            is_msg = name == 'msg' and (isinstance(node.func, ast.Name) or base == 'messages')
            if _typed_call(node) and not is_msg:
                for argument, param in _typed_values(node, name):
                    slots.append((argument, f'{name}({param})', None))
                continue
            if name == 'setdefault' and isinstance(node.func, ast.Attribute) and len(node.args) > 1 \
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value in TEXT_KEYS:
                # (Phase D fix 1, A16.4, Q4) `page.setdefault('title', v)` puts v under a text key
                slots.append((node.args[1], f"setdefault('{node.args[0].value}')", False))
            strict = name in EMITTERS or name in STORE_WRITERS or (name in ('merge', 'save') and base == 'unfinished')
            if strict:
                for argument in node.args:
                    slots.append((argument, f'{name}()', True))
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                if strict:
                    slots.append((keyword.value, f'{name}({keyword.arg}=)', True))
                elif is_msg:
                    slots.append((keyword.value, f'msg({keyword.arg}=)', False))
                elif keyword.arg in TEXT_KEYS:
                    slots.append((keyword.value, f'{name}({keyword.arg}=)', False))
            if strict or is_msg:
                continue
            definitions = []
            target = parsed.name
            if isinstance(node.func, ast.Name):
                definitions = parsed.functions.get(name, [])
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and parsed.imports.get(node.func.value.id) in context.src_names:
                target = parsed.imports[node.func.value.id]
                if target not in G1_EXCLUDED:
                    definitions = [item for item in context.module(target).tree.body
                                   if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name]
            foreign = {} if confirmed is None or target == parsed.name or target in FOREIGN_EXCLUDED \
                else context.g1_parameter_slots(target)
            for definition in definitions:
                params = _function_params(definition)
                for index, argument in enumerate(node.args):
                    if index >= len(params) or params[index] not in TEXT_KEYS:
                        continue
                    if confirmed is not None and target not in FOREIGN_EXCLUDED and \
                            (id(definition), params[index]) not in confirmed and \
                            not any(item[1] == params[index] for item in foreign.get(definition.name, ())):
                        continue                 # A15.4: a parameter that reaches no slot is no text slot
                    slots.append((argument, f'{name}({params[index]})', False))
    return slots


def g1_slots(context, parsed, taint=None, params=None) -> list:
    """(A15.3 F10, A15.4) (expression, slot, strict, path) of every text slot of a module, with the
    parameter slots computed as a fixed point: a parameter whose value a slot carries (through the
    flows) makes what every call passes to it a slot as well — of the same kind when the parameter
    reaches the slot through the direct flows (its value stands there unchanged: G1b and G1f), else a
    G1b-only one (strict None, the kind of a typed-call argument).  Calls of the module-level
    functions of another src module take that module's parameter slots (GuardContext.
    g1_parameter_slots).  ``params``, when given, collects (definition, param, strict, path, slot)."""
    taint = taint or _Taint(context, parsed)
    confirmed = None
    for _round in range(2):          # A15.4: the name rule counts the parameters that reach a slot
        found = _g1_slot_fixed_point(context, parsed, taint, text_slots(parsed, context, confirmed or set()),
                                     params if _round else None)
        confirmed = {(id(definition), name) for definition, name, _kind, _at, _slot in found[1]}
    return found[0]


def _record_slot(slot) -> bool:
    """A slot whose value is a record — an emit's payload, a store writer's positional argument or a
    keyword argument without a TEXT_KEYS name —, not a text (A15.4)."""
    match = re.fullmatch(r'([A-Za-z_][A-Za-z_0-9.]*)\((\w*)=?\)', slot.rsplit(' <- ', 1)[-1])
    if not match or match.group(1).rsplit('.', 1)[-1] not in EMITTERS | STORE_WRITERS | {'merge', 'save'}:
        return False
    return match.group(2) not in TEXT_KEYS


def _g1_slot_fixed_point(context, parsed, taint, base, params=None):
    """(slots, parameter marks) of g1_slots for one set of base slots."""
    queue = deque((expression, slot, strict, ()) for expression, slot, strict in base)
    queue += _foreign_parameter_slots(context, parsed)
    callers = defaultdict(list)          # id(definition) -> the calls that reach it (resolved once)
    for call in _g1_sites(parsed):
        for _owner, item in taint._flow_callees(call, parsed, parsed.scope_of(call.node)):
            callers[id(item)].append(call)
    done, marks, result, collected = set(), set(), [], []
    while queue:
        expression, slot, strict, path = queue.popleft()
        key = (id(expression), strict, path)
        if key in done:
            continue
        done.add(key)
        result.append((expression, slot, strict, path))
        scope = parsed.scope_of(expression)
        direct = {(id(leaf[1]), leaf[2], leaf[4]) for leaf in taint.leaves(expression, parsed, scope, None, path)
                  if leaf[0] == 'param'}
        for leaf in taint.leaves(expression, parsed, scope, True, path):
            if leaf[0] != 'param' or leaf[3] is not parsed:
                continue
            definition, name, at = leaf[1], leaf[2], leaf[4]
            kind = strict if strict is None or (id(definition), name, at) in direct else None
            mark = (id(definition), name, kind, at)
            if mark in marks:
                continue
            marks.add(mark)
            collected.append((definition, name, kind, at, slot))
            if params is not None:
                params.append((definition, name, kind, at, slot))
            method = taint._method(parsed, definition)
            label = getattr(definition, 'name', 'lambda')
            record = _record_slot(slot)
            for call in callers.get(id(definition), ()):
                if True:
                    for argument, selected in _passed(call, definition, name, method, at):
                        if record and any(isinstance(step, tuple) and isinstance(step[1], str)
                                          and step[1] not in TEXT_KEYS for step in selected):
                            continue         # A15.4: a data field of a record (format, key, url, ...) is no text
                        queue.append((argument, f'{label}({name}) <- {slot}', kind, selected))
    return result, collected


# (A15.3 F10) The message API (its arguments are slots by their own rules: msg() params, typed-call
# text arguments, t() params) and the display boundary ui_theme (its text parameters are G5 sinks)
# give no parameter slots to the modules that call them.
FOREIGN_EXCLUDED = ('messages', 'i18n', 'ui_theme')


def _foreign_parameter_slots(context, parsed) -> list:
    """The arguments a module passes to the parameters of another src module's module-level
    functions that are slots there (A15.3 F10 across modules)."""
    found = []
    for call in _g1_sites(parsed):
        func = call.func
        target = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in parsed.imports:
            target = (parsed.imports[func.value.id], func.attr)
        elif isinstance(func, ast.Name) and func.id in parsed.from_imports:
            target = parsed.from_imports[func.id]
        if target is None or target[0] == parsed.name or target[0] in G1_EXCLUDED + FOREIGN_EXCLUDED \
                or target[0] not in context.src_names:
            continue
        for definition, name, kind, at, slot in context.g1_parameter_slots(target[0]).get(target[1], ()):
            for argument, how in _passed(call, definition, name, False, at):
                found.append((argument, f'{target[0]}.{target[1]}({name}) <- {slot}', kind, how))
    return found


def _finding(rule, parsed, node, text, detail=''):
    return OrderedDict(rule=rule, file=parsed.path.name, line=getattr(node, 'lineno', 0), text=text, detail=detail)


def _slot_ok(node, literals) -> bool:
    """An allow entry may say, with a reason (``slot_ok``), that its literal stands in a text slot on
    purpose (a self-test text raised to the console); only a literal itself, never a binding."""
    return isinstance(node, ast.Constant) and bool((literals.get(node.value) or {}).get('slot_ok'))


G1A_HOW = ('是界面上的中文：放进 src/i18n_<页>.py 的 TEXTS，代码里用 i18n.t(\'编号\')（核心模块用 msg(\'编号\')）。'
           '只有写进日志文件、磁盘文件名或存储标记、从不显示给用户的中文，才登记进 '
           'verification/golden/text-allow/{module}.json 的 literals 并写类别和理由')


def g1a_module(context, module, folder=None) -> list:
    """G1a of one module: every CJK literal is in the allow file, the first argument of a logging call too (S3
    G1a-log); no allowed literal outside the 'token' category is the zh text of a stored key (S3 G1a-stored), and no
    'internal' one is the zh text of any key (S3 review fix: a second copy of an interface text, kept for tests)."""
    parsed = context.module(module, folder)
    literals = context.allow(module)['literals']
    findings = []
    stored, every = context.stored_texts(), context.zh_texts()
    for node, text in _literal_nodes(parsed.tree):
        if not CJK.search(text):
            continue
        entry = literals.get(text)
        if entry is None:
            findings.append(_finding('G1a', parsed, node, text, f'{text!r} ' + G1A_HOW.format(module=module)))
            continue
        category = entry.get('category')
        key = stored.get(text) if category != 'token' else None
        if category == 'internal' and key is None:
            key = every.get(text)
        if category not in CATEGORIES or not entry.get('reason'):
            findings.append(_finding('G1a', parsed, node, text,
                                     f'text-allow/{module}.json 里这一条缺类别或理由：类别是 {", ".join(CATEGORIES)} '
                                     f'之一，并写 reason'))
        elif category == 'token' and module not in TOKEN_MODULES:
            findings.append(_finding('G1a', parsed, node, text,
                                     f'类别 token 只给 {" / ".join(TOKEN_MODULES)}：换成 internal 或 file，或者不写中文'))
        elif key is not None and not (category == 'file' and FILE_SAME_AS_KEY.get((module, text)) == key):
            findings.append(_finding('G1a', parsed, node, text,
                                     f"{text!r} 就是编号 {key} 的中文：核心模块用 msg('{key}')，界面用 i18n.t('{key}')，"
                                     f'不要再留一份；删掉这个字面量和 text-allow/{module}.json 里的这一条'))
    return findings


def g1a_scan(src=SRC, allow_dir=ALLOW_DIR, context=None) -> list:
    """G1a of every module G1 applies to: fast (no data flow), for the quick tier (test_i18n_catalog)."""
    context = context or GuardContext(src, allow_dir)
    return [finding for module in g1_modules(src) for finding in g1a_module(context, module)]


def g1_module(context, module, folder=None) -> list:
    """G1a, G1b, G1c, G1d (module part), G1e, G1f of one module."""
    parsed = context.module(module, folder)
    allow = context.allow(module)
    literals = allow['literals']
    findings = g1a_module(context, module, folder)
    # G1b / G1f over the slots and the parameter slots (A15.3 F10)
    taint = _Taint(context, parsed)
    core = module in CORE_MODULES
    reported = set()
    for expression, slot, strict, path in g1_slots(context, parsed, taint):
        scope = parsed.scope_of(expression)
        for branch in _branches(expression):
            # A11.1 / A15: compositions are followed in every slot
            if taint.composed(branch, scope, path=path) and not _slot_ok(branch, literals):
                findings.append(_finding('G1b', parsed, branch, ast.unparse(branch), f'allow-listed text in {slot}'))
            if strict is not None and (core or strict):
                # A15: a composition the slot's value is through the direct flows (names, conditionals,
                # containers, returns, parameters)
                for leaf in taint.leaves(branch, parsed, scope, None, path):
                    if leaf[0] != 'composed' or id(leaf[1]) in reported:
                        continue
                    node, owner, reason = leaf[1], leaf[2], leaf[3]
                    if ast.unparse(node) in context.allow(owner.name)['composed']:
                        continue
                    reported.add(id(node))
                    findings.append(_finding('G1f', owner, node, ast.unparse(node), f'{reason} in {slot}'))
    # G1c
    findings += g1c_module(context, parsed)
    # G1d (module part)
    findings += g1d_module(context, parsed)
    # G1e
    raises = allow['raises']
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            first = node.exc.args[0]
            if isinstance(first, (ast.Constant, ast.JoinedStr)):
                text = first.value if isinstance(first, ast.Constant) else _fstring_text(first)[0]
                if isinstance(text, str) and not CJK.search(text) and text not in raises:
                    findings.append(_finding('G1e', parsed, node, text, 'English raise text not in the allow file'))
    return findings


def g1c_module(context, parsed) -> list:
    findings = []
    zh, en = context.catalogs['zh'], context.catalogs['en']
    for node in ast.walk(parsed.tree):
        if not (isinstance(node, ast.Call) and _func_name(node)[0] == 'msg'
                and (isinstance(node.func, ast.Name) or _func_name(node)[1] == 'messages')):
            continue
        keys = _key_literals(node.args[0]) if node.args else None
        if keys is None or len(node.args) != 1:
            findings.append(_finding('G1c', parsed, node, ast.unparse(node)[:80], 'the key is not a literal'))
            continue
        given = {keyword.arg for keyword in node.keywords}
        if None in given:
            findings.append(_finding('G1c', parsed, node, ast.unparse(node)[:80], '** params cannot be checked'))
            continue
        for key in keys:
            if key == 'common.clauses':
                if parsed.name != 'messages':
                    findings.append(_finding('G1c', parsed, node, key, 'common.clauses outside messages.py'))
                continue
            if key not in zh or key not in en:
                findings.append(_finding('G1c', parsed, node, key, 'key missing from the zh / en catalog'))
                continue
            if _placeholder_names(zh[key]) != given:
                findings.append(_finding('G1c', parsed, node, key,
                                         f'params {sorted(given)} != placeholders {sorted(_placeholder_names(zh[key]))}'))
    return findings


def g1d_module(context, parsed) -> list:
    findings = []
    module = parsed.name
    bare_slots = set()
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == 'i18n':
                findings.append(_finding('G1d', parsed, node, 'from i18n import', 'use i18n.<name> (the t() hook)'))
            if node.module == 'messages':
                bare_slots |= {alias.asname or alias.name for alias in node.names if alias.name in SLOT_CALLS}
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.Call):
            continue
        name, base = _func_name(node)
        slots = (name in SLOT_CALLS and base == 'messages') or (isinstance(node.func, ast.Name) and name in bare_slots)
        if slots and module not in SLOT_MODULES and module != 'messages':
            findings.append(_finding('G1d', parsed, node, f'messages.{name}', 'only library / unfinished read and write slots'))
    if module in CORE_MODULES:
        for node in ast.walk(parsed.tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'i18n':
                parent = parsed.parents.get(node)
                allowed = module == 'library' and node.attr == 't' and isinstance(parent, ast.Call) \
                    and parent.func is node and parent.args and isinstance(parent.args[0], ast.Constant) \
                    and str(parent.args[0].value).startswith('filetext.')
                if not allowed:
                    findings.append(_finding('G1d', parsed, node, f'i18n.{node.attr}',
                                             'a core module uses no i18n attribute (running-language text)'))
    findings += _g1d_aliases(parsed)
    for node in ast.walk(parsed.tree):                 # from WP-6 on no src module (7.2 G1d)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'localize':
            findings.append(_finding('G1d', parsed, node, 'def localize', 'a src module defines localize'))
        if isinstance(node, ast.Call) and _func_name(node)[0] == 'localize':
            findings.append(_finding('G1d', parsed, node, ast.unparse(node)[:80], 'a src module calls localize'))
    return findings


BOUNDARY_MODULES = ('messages', 'i18n')


def _g1d_aliases(parsed) -> list:
    """(final review 1, Q3) G1b / G1c / G1d match the modules messages and i18n by these names
    (`messages.msg`, `messages.show`, `i18n.t`) and the names imported from messages by their own
    names (`msg`, `cut`, `raw`, ...).  So a src module reaches them only under those names: no
    `import messages as X` / `import i18n as X`, no `from messages import <name> as <other>`, no use
    of the module object as a value (`M = messages`, `getattr(i18n, 't')`, passing it on) and no
    `importlib.import_module('messages')` / `__import__('i18n')`."""
    findings = []
    imported = set()
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BOUNDARY_MODULES:
                    imported.add(alias.name)
                    if alias.asname not in (None, alias.name):
                        findings.append(_finding('G1d', parsed, node, f'import {alias.name} as {alias.asname}',
                                                 'the guards match this module by its name: no alias'))
        elif isinstance(node, ast.ImportFrom) and node.module in BOUNDARY_MODULES and not node.level \
                and any(alias.name == '*' for alias in node.names):
            # (final review 2, Q3, A14.4) a star import brings in names the guards cannot list
            findings.append(_finding('G1d', parsed, node, f'from {node.module} import *',
                                     'the guards match the names imported from this module: import them by name'))
        elif isinstance(node, ast.ImportFrom) and node.module == 'messages' and not node.level:
            for alias in node.names:
                if alias.asname not in (None, alias.name):
                    findings.append(_finding('G1d', parsed, node, f'from messages import {alias.name} as {alias.asname}',
                                             'the guards match a messages name by its own name: no alias'))
        elif isinstance(node, ast.Call) and _func_name(node)[0] in ('import_module', '__import__') and node.args:
            # (A13.2; converge round, Q8) a boundary module imported by a string, or an import whose module
            # name is computed (the guards cannot tell what it reaches)
            first = node.args[0]
            name = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
            if name in BOUNDARY_MODULES:
                findings.append(_finding('G1d', parsed, node, ast.unparse(node)[:80],
                                         'the guards match this module by its name: import it with import'))
            elif name is None:
                findings.append(_finding('G1d', parsed, node, ast.unparse(node)[:80],
                                         'an import by a computed name: the guards cannot see what it reaches'))
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == 'modules' \
                and isinstance(node.value.value, ast.Name) and node.value.value.id == 'sys':
            # (converge round, Q8) sys.modules['messages'].show(...) reaches a module behind its name
            key = node.slice
            name = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
            if name is None or name in BOUNDARY_MODULES:
                findings.append(_finding('G1d', parsed, node, ast.unparse(node)[:80],
                                         'sys.modules of a boundary module (or a computed name): use its import'))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' \
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == 'modules' \
                and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == 'sys' and node.args:
            key = node.args[0]
            name = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
            if name is None or name in BOUNDARY_MODULES:
                findings.append(_finding('G1d', parsed, node, ast.unparse(node)[:80],
                                         'sys.modules of a boundary module (or a computed name): use its import'))
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Name) and node.id in imported and isinstance(node.ctx, ast.Load):
            parent = parsed.parents.get(node)
            if not (isinstance(parent, ast.Attribute) and parent.value is node):
                findings.append(_finding('G1d', parsed, node, ast.unparse(parent or node)[:80],
                                         f'the module {node.id} used as a value: the guards see only {node.id}.<name>'))
    # (final review 2, Q3, A14.4) a messages function used outside call position (`fn = messages.show`,
    # `make = msg`, `map(raw, ...)`): the guards check its calls only under its own name
    if parsed.name != 'messages':
        functions = _messages_functions()
        from_names = {local for local, (module, original) in parsed.from_imports.items()
                      if module == 'messages' and original in functions and local == original}
        for node in ast.walk(parsed.tree):
            if not isinstance(getattr(node, 'ctx', None), ast.Load):
                continue
            if isinstance(node, ast.Name) and node.id in from_names:
                text = node.id
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.attr in functions \
                    and parsed.imports.get(node.value.id) == 'messages':
                text = f'messages.{node.attr}'
            else:
                continue
            parent = parsed.parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                findings.append(_finding('G1d', parsed, node, ast.unparse(parent or node)[:80],
                                         f'{text} used as a value: the guards check only its calls'))
    return findings


_MESSAGES_FUNCTIONS = None


def _messages_functions() -> frozenset:
    """The module-level functions of src/messages.py (its classes, Message and Raw, are no calls the
    guards follow, so `isinstance(value, messages.Raw)` stays fine)."""
    global _MESSAGES_FUNCTIONS
    if _MESSAGES_FUNCTIONS is None:
        tree = ast.parse((SRC / 'messages.py').read_text(encoding='utf-8'))
        _MESSAGES_FUNCTIONS = frozenset(node.name for node in tree.body
                                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                        and not node.name.startswith('_')) | TYPED
    return _MESSAGES_FUNCTIONS


def g1_scan(src=SRC, allow_dir=ALLOW_DIR, modules=None, context=None) -> list:
    """Every G1-family finding of the modules G1 applies to (default: every src module outside
    G1_EXCLUDED), plus the tree-wide G1c / G1d checks of the other modules."""
    context = context or GuardContext(src, allow_dir)
    selected = g1_modules(src) if modules is None else list(modules)
    findings = []
    for module in selected:
        findings += g1_module(context, module)
    for module in src_modules(src):
        if module in G1_EXCLUDED or module in selected:
            continue
        parsed = context.module(module)
        findings += g1c_module(context, parsed)
        findings += g1d_module(context, parsed)
    return findings


def allow_stale(context, module, folder=None) -> list:
    """Entries of a module's allow file that match nothing any more (the WP-6 release gate)."""
    parsed = context.module(module, folder)
    allow = context.allow(module)
    texts = {text for _node, text in _literal_nodes(parsed.tree)}
    stale = [('literals', text) for text in allow['literals'] if text not in texts]
    raises = set()
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            first = node.exc.args[0]
            if isinstance(first, ast.Constant):
                raises.add(first.value)
            elif isinstance(first, ast.JoinedStr):
                raises.add(_fstring_text(first)[0])
    stale += [('raises', text) for text in allow['raises'] if text not in raises]
    expressions = {ast.unparse(node) for node in ast.walk(parsed.tree) if isinstance(node, ast.expr)}
    stale += [('composed', text) for text in allow['composed'] if text not in expressions]
    if module in UI_MODULES or module == 'library':
        # I-2 amendment A7 (SD-6): an entry is used only where it excuses a sink position that G5 cannot
        # prove, in each function it names; an entry the scanner no longer needs is stale.
        used = _sink_entries_used(context, module, folder) if allow['sinks'] else set()
        for text, entry in allow['sinks'].items():
            functions = _entry_functions(entry)
            if not functions:
                stale.append(('sinks', f'{text} (names no function)'))
            stale += [('sinks', f'{text} @ {function}') for function in functions if (text, function) not in used]
            if isinstance(entry, dict) and entry.get('parts'):
                # (A16.6) every part an entry names is an expression of the functions it names
                inside = {ast.unparse(node) for node in ast.walk(parsed.tree)
                          if isinstance(node, ast.expr) and _qualname(parsed, node) in functions}
                stale += [('sinks', f'{text} part {part}') for part in entry['parts'] if part not in inside]
    else:
        stale += [('sinks', text) for text in allow['sinks'] if text not in expressions]
    return stale


def release_gate(src=SRC, allow_dir=ALLOW_DIR, context=None) -> OrderedDict:
    """(1.8.0 WP-6) The guard part of the release gate (MESSAGES.md 7.2, 7.8, 10.3 WP-6): what must be
    empty.  stale: allow entries that match nothing (or excuse nothing, A7.5); g1 / g5: the findings of the
    two static guards.  (S3 review fix) The WP-6 leftovers it also listed (ready_flags, transit, localize,
    pending_keys, pending_literals) are gone: nothing can bring them back without failing C12 or the allow-file
    check of test_text_guard."""
    context = context or GuardContext(src, allow_dir)
    gate = OrderedDict(stale=[])
    for module in src_modules(src):
        if module not in G1_EXCLUDED:
            gate['stale'] += [f'{module}: {section} {text}' for section, text in allow_stale(context, module)]
    gate['g1'] = [f'{f["rule"]} {f["file"]}:{f["line"]} {f["text"][:60]}' for f in g1_scan(src, allow_dir,
                                                                                         context=context)]
    gate['g5'] = [f'{f["file"]}:{f["line"]} {f["text"][:60]}' for f in sinks_scan(src, allow_dir=allow_dir,
                                                                                context=context)]
    return gate


# --------------------------------------------------------------------------------- 1.8.0: G5 (MESSAGES.md 7.9)

WIDGET_BASES = frozenset({'ctk', 'tk', 'ttk', 'tkinter'})
# (Phase D fix 1, A16.1; M1) widget constructors by the module they really come from, also through
# `import customtkinter as ck`, `from customtkinter import CTkLabel` or the unaliased module name; the
# literal bases of WIDGET_BASES stay for a module that uses them without importing them
WIDGET_MODULES = frozenset({'customtkinter', 'tkinter', 'tkinter.ttk', 'tkinter.scrolledtext'}) | WIDGET_BASES
# (A16.1; B1) the factories whose widget has a text sink of its own: Chip.set(text) and the insert of a
# text box (by the real name of the call a receiver is bound to)
WIDGET_FACTORIES = {'ui_theme.chip': 'chip', 'ui_theme.Chip': 'chip',
                    'ui_theme.textbox': 'textbox', 'customtkinter.CTkTextbox': 'textbox', 'ctk.CTkTextbox': 'textbox',
                    'tkinter.Text': 'textbox', 'tk.Text': 'textbox', 'tkinter.scrolledtext.ScrolledText': 'textbox',
                    'scrolledtext.ScrolledText': 'textbox'}
TEXT_KWARGS = frozenset({'text', 'placeholder', 'placeholder_text'})
MESSAGEBOXES = frozenset({'showinfo', 'showwarning', 'showerror', 'askyesno', 'askokcancel', 'askyesnocancel',
                          'askquestion', 'askretrycancel'})
MESSAGEBOX_TEXTS = ('title', 'message', 'detail')
FILEDIALOGS = frozenset({'askopenfilename', 'askopenfilenames', 'asksaveasfilename', 'askdirectory',
                         'askopenfile', 'askopenfiles', 'asksaveasfile'})
FILEDIALOG_TEXTS = ('title', 'initialfile', 'filetypes')
MENU_ITEMS = frozenset({'add_command', 'add_checkbutton', 'add_radiobutton', 'add_cascade', 'insert_command',
                        'insert_checkbutton', 'insert_radiobutton', 'insert_cascade', 'entryconfigure', 'entryconfig'})
DISPLAY_CALLS = {('i18n', 't'), ('i18n', 'label'), ('i18n', 'choices'), ('messages', 'show'),
                 ('messages', 'error_text')}
DISPLAY_BARE = frozenset({'show', 'error_text'})
SHOW_CALLS = frozenset({('messages', 'show'), ('messages', 'error_text')})
# Language-neutral text.  A call through a module alias is matched by the module the alias names
# (`import urls as links`: links.display is urls.display; I-2 amendment A7, SD-4); the literal
# ('urltools', 'display') of 7.9 stays for modules that do not import it.
NEUTRAL_CALLS = {('urls', 'display'), ('urltools', 'display'), ('os', 'fspath')}
NUMBER_CALLS = frozenset({'len', 'int', 'float', 'round', 'abs'})     # bare builtins that return a number
VALUE_SINKS = frozenset({'append_log'})
VALUE_SINK_PARAM = 'line'                     # append_log(line)
# (WP-6, amendment A11.4) a time.strftime format made only of numeric directives and separators: the
# result is language-neutral digits (library_ui.clock's '%m-%d %H:%M')
NUMERIC_TIME = re.compile(r'(?:%[dmyYHMSjI]|[-:/. ])+')
TABLE_SINKS = ('Treeview.fill values', 'insert(values=)', 'item(values=)')
# (converge round, A15.4) The display parameters of the ui_theme API, the display boundary G5 does
# not scan: factory -> {parameter: (position, kind, path)}.  `values` / `names` are shown only when
# the call passes no `labels`; a table's column specs show element 1 (the heading).
THEME_SINKS = {
    'label': {'text': (1, 'theme', ())}, 'button': {'text': (1, 'theme', ())}, 'chip': {'text': (1, 'theme', ())},
    'checkbox': {'text': (1, 'theme', ())}, 'switch': {'text': (1, 'theme', ())},
    'entry': {'placeholder': (1, 'theme', ())},
    'option_menu': {'labels': (2, 'theme', ()), 'values': (1, 'theme unlabelled', ())},
    'segmented': {'labels': (2, 'theme', ())},
    'tabs': {'labels': (None, 'theme', ()), 'names': (1, 'theme unlabelled', ())},
    'empty_state': {'text': (1, 'theme', ())},
    # (2.0.0 S4 A1) the shared pieces of the pages
    'textbox': {'placeholder': (1, 'theme', ())}, 'section_label': {'text': (1, 'theme', ())},
    'ask_text': {'title': (1, 'theme', ())},
    'table': {'columns': (1, 'theme columns', ('iter', ('key', 1))), 'empty': (3, 'theme', ())},
    'fit': {'text': (1, 'theme', ())},
}
# ui_theme parameters with a text-like name that are not shown (the completeness self-test reads it)
THEME_NOT_SHOWN = {('fitted_width', 'text'): 'measures the text of a button; returns a width',
                   ('menu_width', 'labels'): 'measures the labels of a menu; returns a width',
                   ('segmented', 'values'): 'the stored ids; the labels are shown'}
THEME_TEXT_PARAMS = frozenset({'text', 'title', 'description', 'labels', 'placeholder', 'empty', 'message', 'detail',
                               'names', 'values', 'columns', 'alternatives', 'caption'})
# (A15.4) The SINKS of G5: (kind, mode).  sink_positions() reports every position under one of these
# kinds (mode 'strict': the value must be display text by the 7.9 grammar; 'value': no text fixed in
# the source that is not display text may reach it, A15.4); the contract generator of
# test_text_guard builds its cases from this table.  It may grow; it shrinks only by a spec change.
SINKS = (
    ('configure', 'strict'),             # <widget>.configure / .config(text= / placeholder= / placeholder_text=)
    ('constructor', 'strict'),           # ctk.* / tk.* / ttk.* / tkinter.*(text= / placeholder= / ... / values=)
    ('theme', 'strict'),                 # a display parameter of the ui_theme API (THEME_SINKS)
    ('theme unlabelled', 'strict'),      # option_menu values / tabs names of a call without labels
    ('theme columns', 'value'),          # ui_theme.table(): the heading of every column spec
    ('chip set', 'strict'),              # <chip>.set(text)
    ('heading', 'strict'),               # <tree>.heading(..., text=)
    ('title', 'strict'),                 # <window>.title(text) / .wm_title(text)
    ('set_empty', 'strict'),             # <table>.set_empty(text)
    ('fill', 'strict'),                  # <table>.fill(rows): the values of each (iid, values) row, cell by cell
    ('values', 'strict'),                # <tree>.insert(..., values=) / .item(..., values=)
    ('textbox insert', 'strict'),        # <text / log / box>.insert(index, text)
    ('menu', 'strict'),                  # <menu>.add_command / add_checkbutton / ...(label=)
    ('messagebox', 'strict'),            # messagebox.show* / ask*(title, message, detail=)
    ('filedialog', 'strict'),            # filedialog.ask*(title=, initialfile=, filetypes=)
    ('t()', 'strict'),                   # every keyword value of i18n.t(...)
    ('t() mapping', 'value'),            # the values of a **mapping passed to i18n.t(...)
    ('sink method', 'strict'),           # what a call passes to a strict sink parameter
    ('append_log', 'value'),             # append_log(line) / append_log(line=...)
    ('show()', 'value'),                 # the argument of every messages.show / error_text call
    ('value sink method', 'value'),      # what a call passes to a value-mode sink parameter
    ('library filetext t()', 'strict'),  # library's filetext i18n.t keyword values (_library_sinks)
)
SINK_KINDS = dict(SINKS)

# (Phase D fix 1, MESSAGES.md 15 amendment A16.7; B2 / Q3) THE GUARD CONTRACT AS DATA: one id per source
# (A15.2: S = text fixed in the source, N = never a source), per flow (A15.3 F1-F11 with the forms of
# A16.2 / A16.3), per sink kind (A15.4) and per sink spelling (A16.1: every callee, keyword, receiver
# binding, alias and **mapping form, every THEME_SINKS parameter; A16.4: the G1b slot spellings), with
# the guards it binds and the cases it needs ('bad': a case that must be flagged, 'good': one that must
# not be, 'both').  Kept next to the scanner that implements it and independent of the generator:
# test_text_guard.ContractTests requires that verification/guard_contract.py tags a bad and / or a good
# case with every id for every guard named here, that it uses no other id, that the published spec lists
# every id (A16.7), and that removing a behaviour (guard_contract.MUTANTS) fails a case of its id.
CONTRACT_FORMS = (
    (('G1b',), 'bad', (
        'S1 allow-listed literal',
    )),
    (('G5',), 'bad', (
        'S2 Chinese literal', 'S3 class text', 'S3 module text', 'S4 other module',
    )),
    (('G5', 'G1b'), 'bad', (
        'S5 enum source', 'S6 zh template',
    )),
    (('G1b',), 'good', (
        'N msg()', 'N raw()',
    )),
    (('G5', 'G1b'), 'good', (
        'N number',
    )),
    (('G5',), 'good', (
        'N show()', 'N show(raw())', 'N t()',
    )),
    (('G1f',), 'both', (
        'O G1f composition',
    )),
    (('G5',), 'both', (
        'O event value',
    )),
    (('G5', 'G1b'), 'both', (
        'F1 alias chain', 'F1 annotated assignment', 'F1 assignment', 'F1 augmented assignment',
        'F1 comprehension target', 'F1 direct value', 'F1 for target', 'F1 global', 'F1 nonlocal',
        'F1 parameter rebound', 'F1 rebinding', 'F1 walrus', 'F1 with as',
    )),
    (('G5', 'G1b'), 'both', (
        'F2 from import', 'F2 module name', 'F2 module.NAME',
    )),
    (('G5', 'G1b'), 'both', (
        'F3 annotated attribute', 'F3 attribute mutation', 'F3 augmented attribute', 'F3 class attribute',
        'F3 class name attribute', 'F3 cls attribute', 'F3 getattr', 'F3 getattr default', 'F3 property',
        'F3 self attribute', 'F3 setattr', 'F3 tuple-target attribute',
    )),
    (('G5',), 'both', (
        'F3 cross-module attribute',
    )),
    (('G5', 'G1b'), 'both', (
        'F4 conditional', 'F4 or / and',
    )),
    (('G5', 'G1b'), 'both', (
        'F5 ** spread', 'F5 add', 'F5 append', 'F5 dict comprehension', 'F5 dict comprehension keys', 'F5 dict get',
        'F5 dict item', 'F5 dict iteration', 'F5 extend', 'F5 generator', 'F5 get default', 'F5 insert',
        'F5 item assignment', 'F5 item augmented', 'F5 items()', 'F5 keys()', 'F5 list comprehension',
        'F5 list slice', 'F5 nested selection', 'F5 neutral-named item', 'F5 pop', 'F5 pop default', 'F5 popitem()',
        'F5 set comprehension', 'F5 set display', 'F5 setdefault', 'F5 starred element', 'F5 tuple index',
        'F5 update(k=)', 'F5 update(mapping)', 'F5 values()',
    )),
    (('G5', 'G1b'), 'both', (
        'F6 enumerate', 'F6 items() unpacking', 'F6 nested unpacking', 'F6 starred unpacking', 'F6 unpacking',
        'F6 zip',
    )),
    (('G5', 'G1b'), 'both', (
        'F7 copy.copy', 'F7 dict(k=)', 'F7 dict(mapping)', 'F7 dict(pairs)', 'F7 filter', 'F7 frozenset()',
        'F7 iter()', 'F7 list()', 'F7 map', 'F7 max()', 'F7 max() several', 'F7 min()', 'F7 min() default',
        'F7 next()', 'F7 next() default', 'F7 reversed', 'F7 set()', 'F7 sorted', 'F7 tuple()',
    )),
    (('G5', 'G1b'), 'both', (
        'F8 %', 'F8 *', 'F8 +', 'F8 .format', 'F8 .format_map', 'F8 .join', 'F8 .join receiver',
        'F8 center inserted', 'F8 f-string', 'F8 f-string conversion', 'F8 format()', 'F8 re.sub',
        'F8 replace inserted', 'F8 str method', 'F8 str method unbound', 'F8 str()', 'F8 text slice', 'F8 textwrap',
    )),
    (('G5', 'G1b'), 'both', (
        'F9 alias of a function', 'F9 classmethod', 'F9 dict of lambdas', 'F9 from-import function',
        'F9 function return', 'F9 getattr method', 'F9 lambda', 'F9 lambda name', 'F9 method return',
        'F9 nested function', 'F9 other module function', 'F9 returned parameter', 'F9 staticmethod', 'F9 yield',
    )),
    (('G5', 'G1b'), 'both', (
        'F10 depth 2', 'F10 depth 3', 'F10 keyword', 'F10 parameter default', 'F10 positional',
    )),
    (('G5', 'G1b'), 'both', (
        'F11 Path().name', 'F11 os.fspath', 'F11 urls.display',
    )),
    (('G5',), 'both', (
        'F11 show()',
    )),
    (('G1b',), 'both', (
        "A15.4 G1b ['title'] =", 'A15.4 G1b clauses(parts)', "A15.4 G1b dict 'title'", 'A15.4 G1b emit()',
        'A15.4 G1b keyword title=', 'A15.4 G1b msg param', 'A15.4 G1b raise', 'A15.4 G1b raw(text)',
        'A15.4 G1b store writer', 'A15.4 G1b store writer record',
    )),
    (('G5',), 'both', (
        'A15.4 append_log', 'A15.4 chip set', 'A15.4 configure', 'A15.4 constructor', 'A15.4 filedialog',
        'A15.4 fill', 'A15.4 heading', 'A15.4 library filetext t()', 'A15.4 menu', 'A15.4 messagebox',
        'A15.4 set_empty', 'A15.4 show()', 'A15.4 sink method', 'A15.4 t()', 'A15.4 t() mapping',
        'A15.4 textbox insert', 'A15.4 theme', 'A15.4 theme columns', 'A15.4 theme unlabelled', 'A15.4 title',
        'A15.4 value sink method', 'A15.4 values',
    )),
    (('G5',), 'both', (
        'A16.1 append_log after', 'A16.1 append_log callee alias', 'A16.1 append_log getattr',
        'A16.1 append_log line=', 'A16.1 append_log partial', 'A16.1 chip set keyword', 'A16.1 chip set local',
        'A16.1 chip set row item', 'A16.1 chip set self binding', 'A16.1 config text=',
        'A16.1 configure **mapping display', 'A16.1 configure **mapping name', 'A16.1 configure callee alias',
        'A16.1 configure cnf mapping', 'A16.1 configure partial', 'A16.1 configure placeholder=',
        'A16.1 configure placeholder_text=', 'A16.1 constructor **kw wrapper', 'A16.1 constructor **mapping',
        'A16.1 constructor customtkinter', 'A16.1 constructor from-import', 'A16.1 constructor import alias',
        'A16.1 constructor placeholder=', 'A16.1 constructor placeholder_text=', 'A16.1 constructor tk',
        'A16.1 constructor tkinter', 'A16.1 constructor ttk', 'A16.1 constructor values=', 'A16.1 error_text()',
        'A16.1 error_text(error=)', 'A16.1 filedialog askdirectory', 'A16.1 filedialog askopenfile',
        'A16.1 filedialog askopenfilename', 'A16.1 filedialog askopenfilenames', 'A16.1 filedialog askopenfiles',
        'A16.1 filedialog asksaveasfile', 'A16.1 filedialog asksaveasfilename', 'A16.1 filedialog filetypes=',
        'A16.1 filedialog initialfile=', 'A16.1 fill rows=', 'A16.1 heading **mapping', 'A16.1 item values=',
        'A16.1 menu add_cascade', 'A16.1 menu add_checkbutton', 'A16.1 menu add_command',
        'A16.1 menu add_radiobutton', 'A16.1 menu entryconfig', 'A16.1 menu entryconfigure',
        'A16.1 menu insert_cascade', 'A16.1 menu insert_checkbutton', 'A16.1 menu insert_command',
        'A16.1 menu insert_radiobutton', 'A16.1 messagebox askokcancel', 'A16.1 messagebox askquestion',
        'A16.1 messagebox askretrycancel', 'A16.1 messagebox askyesno', 'A16.1 messagebox askyesnocancel',
        'A16.1 messagebox callee alias', 'A16.1 messagebox detail=', 'A16.1 messagebox dotted',
        'A16.1 messagebox from-import', 'A16.1 messagebox message=', 'A16.1 messagebox module alias',
        'A16.1 messagebox showerror', 'A16.1 messagebox showinfo', 'A16.1 messagebox showwarning',
        'A16.1 messagebox starred', 'A16.1 messagebox title', 'A16.1 messagebox title=', 'A16.1 set_empty text=',
        'A16.1 show at a strict sink by keyword', 'A16.1 show bare', 'A16.1 show callee alias', 'A16.1 show(value=)',
        'A16.1 sink method ** argument', 'A16.1 sink method **param', 'A16.1 sink method *param',
        'A16.1 sink method Thread args', 'A16.1 sink method Thread kwargs', 'A16.1 sink method after',
        'A16.1 sink method after_idle', 'A16.1 sink method callee alias', 'A16.1 sink method getattr',
        'A16.1 sink method keyword', 'A16.1 sink method keyword-only', 'A16.1 sink method lambda',
        'A16.1 sink method module function', 'A16.1 sink method nested', 'A16.1 sink method partial',
        'A16.1 sink method partial keyword', 'A16.1 sink method starred argument', 'A16.1 t() mapping name',
        'A16.1 textbox insert binding', 'A16.1 textbox insert chars=', 'A16.1 textbox insert ctk binding',
        'A16.1 textbox insert text=', 'A16.1 theme **mapping', 'A16.1 theme button text',
        'A16.1 theme button text keyword', 'A16.1 theme callee alias', 'A16.1 theme checkbox text',
        'A16.1 theme checkbox text keyword', 'A16.1 theme chip text', 'A16.1 theme chip text keyword',
        'A16.1 theme empty_state text', 'A16.1 theme empty_state text keyword', 'A16.1 theme entry placeholder',
        'A16.1 theme entry placeholder keyword', 'A16.1 theme fit text', 'A16.1 theme fit text keyword',
        'A16.1 theme from-import',
        'A16.1 theme label text', 'A16.1 theme label text keyword', 'A16.1 theme module alias',
        'A16.1 theme option_menu labels', 'A16.1 theme option_menu labels keyword', 'A16.1 theme option_menu values',
        'A16.1 theme option_menu values keyword', 'A16.1 theme segmented labels',
        'A16.1 theme segmented labels keyword', 'A16.1 theme switch text', 'A16.1 theme switch text keyword',
        'A16.1 theme table columns', 'A16.1 theme table columns keyword', 'A16.1 theme table empty',
        'A16.1 theme table empty keyword', 'A16.1 theme tabs labels keyword', 'A16.1 theme tabs names',
        'A16.1 theme tabs names keyword', 'A16.1 title string=', 'A16.1 value sink method **param',
        'A16.1 value sink method keyword', 'A16.1 value sink method lambda', 'A16.1 value sink method through show',
        'A16.1 wm_title',
    )),
    (('G1b',), 'both', (
        'A16.4 .reason = in a tuple target', 'A16.4 .title =', "A16.4 ['title'] +=", 'A16.4 cut(value)',
        'A16.4 emit callee alias', 'A16.4 emit keyword', 'A16.4 emit payload', 'A16.4 finish_subscription',
        'A16.4 keyword TEXT_KEYS parameter', 'A16.4 masked(value)', 'A16.4 message_of(error)',
        'A16.4 other module parameter slot', 'A16.4 positional TEXT_KEYS parameter', 'A16.4 raw(text=)',
        'A16.4 say()', 'A16.4 self.emit()', "A16.4 setdefault('title')", 'A16.4 store writer keyword',
        'A16.4 stripped(value)', 'A16.4 unfinished.merge', 'A16.4 unfinished.merge record',
        'A16.4 unfinished.save record',
    )),
)
CONTRACT = OrderedDict((form, (guards, need)) for guards, need, forms in CONTRACT_FORMS for form in forms)


def _qualname(parsed, node) -> str:
    """The definitions enclosing a node: 'Class.method', 'function.inner' or '<module>'."""
    names = []
    node = parsed.parents.get(node)
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        node = parsed.parents.get(node)
    return '.'.join(reversed(names)) or '<module>'


def _entry_functions(entry) -> tuple:
    """The functions a `sinks` entry names (`"function": "Class.method"` or a list of them)."""
    functions = entry.get('function') if isinstance(entry, dict) else None
    if isinstance(functions, str):
        return (functions,)
    if isinstance(functions, list) and all(isinstance(item, str) for item in functions):
        return tuple(functions)
    return ()


def _allowed_sink(allow, parsed, expression) -> bool:
    """A `sinks` entry excuses its expression only inside the functions it names (I-2 amendment A7,
    SD-6): the same text at a sink of another function is checked on its own.  An entry that names
    no function excuses nothing.  An entry with ``parts`` excuses nothing whole (_entry_parts)."""
    entry = allow.get(ast.unparse(expression))
    return entry is not None and not (isinstance(entry, dict) and entry.get('parts')) \
        and _qualname(parsed, expression) in _entry_functions(entry)


def _entry_parts(allow, parsed, expression):
    """(Phase D fix 1, A16.6) (function, {part}) of a `sinks` entry with ``parts`` for this sink
    position: inside the function it names, the listed sub-expressions (as ast.unparse prints them)
    count as display text and the rest of the value must still be proved, so a changed binding of
    the value (M11c: `chip_text = _chip`) is a finding again; None for any other position."""
    entry = allow.get(ast.unparse(expression))
    if not isinstance(entry, dict) or not entry.get('parts'):
        return None
    function = _qualname(parsed, expression)
    if function not in _entry_functions(entry):
        return None
    return function, frozenset(entry['parts'])


class _Sinks(_Flow):
    """The G5 positive sink rule over a set of UI modules (MESSAGES.md 7.9, I-2 amendment A7, A15, A16):
    the sink positions (sink_positions, A16.1), the value rule of the flows (_leaves_ok) and, at a strict
    sink, the display grammar with it (strict_ok, A16.5)."""

    def __init__(self, context, modules, folder=None, extra_display=(), kinds=None):
        self.context = context
        self.kinds = None if kinds is None else frozenset(kinds)      # the sink kinds checked (all by default)
        self.folder = Path(folder) if folder else context.src
        self.parsed = {name: context.module(name, self.folder) for name in modules
                       if (self.folder / f'{name}.py').is_file()}
        self.extra = set(extra_display)          # e.g. {('i18n', 'localize')} for the 1.7.0 self-test
        self.defs = defaultdict(list)            # function name -> [(module, FunctionDef)]
        self.method = {}                         # id(definition) -> defined in a class body
        self.toplevel = {}                       # id(definition) -> defined at module level
        for name, parsed in self.parsed.items():
            for node in ast.walk(parsed.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.defs[node.name].append((name, node))
                    parent = parsed.parents.get(node)
                    self.method[id(node)] = isinstance(parent, ast.ClassDef)
                    self.toplevel[id(node)] = parent is parsed.tree
                elif isinstance(node, ast.Lambda):      # (A16.3) a lambda wrapper is called, never bound
                    self.method[id(node)] = False
                    self.toplevel[id(node)] = False
        self.returns_display = set()             # names of functions whose every return is display text
        self._returns_ok = set()                 # id(definition) whose every return is display text
        self._return_params = defaultdict(set)   # id(definition) -> {(param, path)} its returned text depends on
        self.sink_params = defaultdict(set)      # function name -> {param name} (report)
        self._def_params = defaultdict(set)      # id(definition) -> {(param, path)} checked at the callers
        self._accepted_params = []               # (definition, param, path) accepted in one pass
        # (Phase D fix 1, A16.5) which judgement a strict sink gets: 'both' (the 7.9 grammar and the
        # value rule of the flows), or one of them alone ('grammar', 'flows': the agreement self-test)
        self.strict = 'both'
        self._parts = None                       # (A16.6) (function, {part}) of the entry being applied
        # (A16.5) when a list: (file, line, grammar ok, flows ok) of every sink position of the last pass
        # (a value position: the value rule twice), for the agreement self-test of the two judgements
        self.judgements = None
        # (A15.4) value-mode sink parameters: what a call passes to them is checked by the value rule
        self.value_sink_params = defaultdict(set)          # function name -> {param name} (report)
        self._value_def_params = defaultdict(set)          # id(definition) -> {(param, path)}
        self._value_params = []                            # (definition, param, path) found in one pass
        self._sites = {name: _call_sites(parsed.tree) for name, parsed in self.parsed.items()}
        self._attributes = None
        self.referenced = self._referenced()     # id(definition) of functions also used as values (SD-7)

    # -------------------------------------------------------------- the flows (A15.3) over the window

    def _flow_module(self, name):
        return self._other(name)

    def _flow_attributes(self, attr, parsed):
        """The window is one class made of mixins: a `self.<attr>` of a scanned module is assigned in
        any of them (A15.3 F3)."""
        if parsed.name not in self.parsed:
            return _attribute_index(parsed).get(attr, [])
        if self._attributes is None:
            merged = defaultdict(list)
            for other in self.parsed.values():
                for key, items in _attribute_index(other).items():
                    merged[key] += items
            self._attributes = merged
        return self._attributes.get(attr, [])

    def _flow_class(self, name, parsed) -> bool:
        if parsed.name not in self.parsed:
            return name in _class_names(parsed)
        return any(name in _class_names(other) for other in self.parsed.values())

    def _flow_callees(self, call, parsed, scope):
        if parsed.name not in self.parsed:
            return _module_callees(call, parsed, scope, self._other)
        return [(self.parsed[module], definition) for module, definition in self._callees(call, parsed, scope)]

    def _method_names(self, parsed):
        """The methods of the window (one class made of mixins, A15.3 F3)."""
        if parsed.name not in self.parsed:
            return _Flow._method_names(self, parsed)
        if self.__dict__.get('_window_methods') is None:
            self._window_methods = frozenset(name for name, items in self.defs.items()
                                             if any(self.method[id(item)] for _module, item in items))
        return self._window_methods

    # -------------------------------------------------------------- callees by their real names (A16.1, M1)

    def _qualified(self, func, parsed, scope):
        """(Phase D fix 1, A16.1; M1) The dotted name a callee stands for, through the module's imports:
        ``ck.CTkLabel`` with ``import customtkinter as ck`` -> 'customtkinter.CTkLabel', ``CTkLabel``
        from ``from customtkinter import CTkLabel`` -> the same, ``showinfo`` from ``from
        tkinter.messagebox import showinfo`` -> 'tkinter.messagebox.showinfo', ``theme_fit`` from
        ``from ui_theme import fit as theme_fit`` -> 'ui_theme.fit'; an unresolved base keeps its own
        name ('ctk.CTkLabel' in a module that does not import ctk); None for any other callee."""
        if isinstance(func, ast.Name):
            if parsed.lookup(func.id, scope) is not None:
                return None
            if func.id in parsed.from_imports:
                module, original = parsed.from_imports[func.id]
                return f'{module}.{original}'
            return func.id
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name):
                if not _module_name(parsed, base.id, scope):
                    return None
                if base.id in parsed.imports:
                    return f'{parsed.imports[base.id]}.{func.attr}'
                if base.id in parsed.from_imports:
                    module, original = parsed.from_imports[base.id]
                    return f'{module}.{original}.{func.attr}'
                return f'{base.id}.{func.attr}' if base.id not in ('self', 'cls') else None
            if isinstance(base, ast.Attribute):
                inner = self._qualified(base, parsed, scope)
                return f'{inner}.{func.attr}' if inner else None
        return None

    def _variants(self, call, parsed, scope):
        """The call itself and, for an aliased callee, the same call of each callable the alias holds
        (A16.3)."""
        found = [call]
        for target in self._aliases(call.func, parsed, scope):
            found.append(_Call(call.node, target, call.args, call.keywords, call.callback))
        return found

    def _key_index(self):
        """(A16.1, B1) {key: [(value, parsed, scope)]} of every item a dict display or an item
        assignment of the window puts under a constant str key (``{'badge': badge}``,
        ``row['badge'] = ...``): the window's row models, by key name (as F3 reads attributes by name)."""
        if self.__dict__.get('_keys') is None:
            index = defaultdict(list)
            for parsed in self.parsed.values():
                for node in ast.walk(parsed.tree):
                    if isinstance(node, ast.Dict):
                        for key, value in zip(node.keys, node.values):
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                index[key.value].append((value, parsed, parsed.scope_of(node)))
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) \
                                    and isinstance(target.slice.value, str):
                                index[target.slice.value].append((node.value, parsed, parsed.scope_of(node)))
            self._keys = index
        return self._keys

    def _receiver_kinds(self, node, parsed, scope, seen=frozenset(), depth=0):
        """(Phase D fix 1, A16.1; B1) The widget kinds ('chip', 'textbox') a receiver may hold, by what it
        is bound to: a call of a widget factory (WIDGET_FACTORIES, by its real name), a UI function that
        returns one, a local or module name, ``self.<attr>`` across the window, an item of a row model
        (``row['badge']``: every dict item and item assignment of the window under that key), a
        conditional.  The receiver's own name is a second, independent sign (the 1.8.0 rule: a name
        with 'chip', or with 'text' / 'log' / 'box')."""
        if depth > 6:
            return set()
        found = set()
        if isinstance(node, ast.Call):
            qualified = self._qualified(node.func, parsed, scope) or ''
            for suffix, kind in WIDGET_FACTORIES.items():
                if qualified == suffix or qualified.endswith('.' + suffix):
                    found.add(kind)
            if not found:
                for module, definition in self._callees(node, parsed, scope):
                    owner = self.parsed[module]
                    own = owner.scopes.get(definition)
                    for item in ast.walk(definition):
                        if isinstance(item, ast.Return) and item.value is not None and owner.scope_of(item) is own:
                            found |= self._receiver_kinds(item.value, owner, own, seen, depth + 1)
            return found
        if isinstance(node, ast.Name):
            owner = parsed.lookup(node.id, scope)
            mark = ('name', id(owner), node.id)
            if owner is None or mark in seen:
                return found
            seen = seen | {mark}
            for kind, value in owner.bindings.get(node.id, ()):
                if value is not None and kind in ('value', 'with'):
                    found |= self._receiver_kinds(value, parsed, owner, seen, depth + 1)
            for kind, value, at in owner.positions.get(node.id, ()):
                element = value
                for step in at:
                    if kind == 'unpack' and step is not None and isinstance(element, (ast.Tuple, ast.List)) \
                            and step < len(element.elts):
                        element = element.elts[step]
                found |= self._receiver_kinds(element, parsed, owner, seen, depth + 1)
            return found
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls'):
            mark = ('attr', node.attr)
            if mark in seen:
                return found
            seen = seen | {mark}
            for value, owner, own_scope, at in self._flow_attributes(node.attr, parsed):
                if not at:
                    found |= self._receiver_kinds(value, owner, own_scope, seen, depth + 1)
            return found
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            mark = ('key', node.slice.value)
            if mark in seen:
                return found
            seen = seen | {mark}
            for value, owner, own_scope in self._key_index().get(node.slice.value, ()):
                found |= self._receiver_kinds(value, owner, own_scope, seen, depth + 1)
            return found
        if isinstance(node, ast.IfExp):
            return self._receiver_kinds(node.body, parsed, scope, seen, depth + 1) | \
                self._receiver_kinds(node.orelse, parsed, scope, seen, depth + 1)
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                found |= self._receiver_kinds(value, parsed, scope, seen, depth + 1)
        return found

    def _receiver_is(self, kind, receiver, parsed, scope):
        """Is the receiver a widget of this kind, by its binding or by its name (A16.1)?"""
        words = ('chip',) if kind == 'chip' else ('text', 'log', 'box')
        if any(word in ast.unparse(receiver).lower() for word in words):
            return True
        return kind in self._receiver_kinds(receiver, parsed, scope)

    # -------------------------------------------------------------- resolution (SD-1, SD-7)

    def resolve(self, node, parsed):
        """The definitions of the scanned modules a call (or a reference) can reach: a bare name -> a
        function of the same module, or the module-level function `from <module> import` names;
        `<module>.f` -> the module-level f of that module when it is scanned, never a method
        (`i18n.label`, `ui_theme.label`, `ui_theme.button` are not `StudioApp.label` / `.button`);
        any other receiver (`self.f`, `self.app.f`, `super().f`) -> the methods named f."""
        func = node.func if isinstance(node, (ast.Call, _Call)) else node
        if isinstance(func, ast.Name):
            if func.id in parsed.from_imports:
                module, original = parsed.from_imports[func.id]
                return [(m, d) for m, d in self.defs.get(original, ()) if m == module and self.toplevel[id(d)]]
            return [(m, d) for m, d in self.defs.get(func.id, ()) if m == parsed.name and not self.method[id(d)]]
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name) and (base.id in parsed.imports or base.id in parsed.from_imports):
                module = parsed.imports.get(base.id)
                return [(m, d) for m, d in self.defs.get(func.attr, ()) if m == module and self.toplevel[id(d)]]
            return [(m, d) for m, d in self.defs.get(func.attr, ()) if self.method[id(d)]]
        return []

    def _referenced(self):
        """Functions that are also used as values: a bare name or `self.<method>` that is not called
        (a callback, a `functools.partial` target, a dict of handlers), and the methods a
        `getattr(obj, 'name')` / `getattr(obj, f'prefix{...}')` reaches (`StudioApp.handlers` is filled
        with `getattr(self, f'on_{kind}')`).  Their parameters can receive anything without a call G5
        sees, so a parameter of such a function is never display text (SD-7)."""
        found = set()
        callbacks = {id(site.func) for sites in self._sites.values() for site in sites if site.callback}
        for parsed in self.parsed.values():
            for node in ast.walk(parsed.tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'getattr' \
                        and len(node.args) >= 2:
                    parent = parsed.parents.get(node)
                    if isinstance(parent, ast.Call) and parent.func is node:
                        continue                 # (A16.3) getattr(self, 'm')(...): a call, seen through _variants
                    name = node.args[1]
                    if isinstance(name, ast.Constant) and isinstance(name.value, str):
                        found.update(id(d) for _m, d in self.defs.get(name.value, ()) if self.method[id(d)])
                    elif isinstance(name, ast.JoinedStr) and name.values and isinstance(name.values[0], ast.Constant):
                        prefix = name.values[0].value
                        found.update(id(d) for key, definitions in self.defs.items() if key.startswith(prefix)
                                     for _m, d in definitions if self.method[id(d)])
                    continue
                if isinstance(node, ast.Attribute):
                    if not (isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls')):
                        continue                 # `preset.label`, `widget.title`: data, not a method reference
                elif not isinstance(node, ast.Name):
                    continue
                if not isinstance(node.ctx, ast.Load):
                    continue
                parent = parsed.parents.get(node)
                if isinstance(parent, ast.Call) and parent.func is node:
                    continue
                if id(node) in callbacks:         # A15.4: after / partial / Thread call it with what they hold
                    continue
                if self._called_alias(node, parent, parsed):
                    continue                     # (A16.3) `log = self.append_log; log(x)`: seen through _variants
                if (node.id if isinstance(node, ast.Name) else node.attr) not in self.defs:
                    continue
                found.update(id(definition) for _module, definition in self.resolve(node, parsed))
        return found

    def _called_lambda(self, function, parsed) -> bool:
        """(Phase D fix 1, A16.3) A lambda called in place, or the whole value of an assignment to a
        local name every use of which is a call: its parameters receive only what those calls pass,
        so they are sink parameters checked there (F10), as a function's are."""
        parent = parsed.parents.get(function)
        if isinstance(parent, ast.Call) and parent.func is function:
            return True
        return self._called_alias(function, parent, parsed)

    def _lambda_targets(self, call, parsed, scope):
        """[(module, lambda)] a call reaches: a lambda called in place, or the lambdas a local name
        is bound to (when every binding is one)."""
        func = call.func
        if isinstance(func, ast.Lambda):
            return [(parsed.name, func)]
        if isinstance(func, ast.Name):
            return [(parsed.name, item) for item in _lambda_bindings(func.id, parsed, scope)]
        return []

    @staticmethod
    def _called_alias(node, parent, parsed) -> bool:
        """(A16.3) A reference that is the whole value of an assignment to a local name every use of
        which is a call: the alias is resolved at each call (_variants), so it is no use as a value."""
        if not (isinstance(parent, ast.Assign) and parent.value is node and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)):
            return False
        name = parent.targets[0].id
        scope = parsed.scope_of(parent)
        if scope is parsed.module_scope:
            return False
        uses = [item for item in ast.walk(scope.node) if isinstance(item, ast.Name) and item.id == name
                and isinstance(item.ctx, ast.Load)]
        return all(isinstance(parsed.parents.get(item), ast.Call) and parsed.parents[item].func is item
                   for item in uses)

    def _passed_display(self, node, parsed, scope, seen, definition) -> bool:
        """Every parameter whose value the function returns as text receives display text here.  A
        `**mapping` argument may supply any parameter the call does not pass explicitly, so with one
        such a parameter is not display text (WP-6, RV-I2-02, amendment A11.2); a `*param` vararg
        receives every argument from its position on (A11.3).  (Phase D fix 1, A16.1) Read with
        _passed, as G1 and the value rule read it: keyword, position, starred, `*param`, `**param`
        (a returned ``kw['text']`` takes the caller's ``text=``), `**mapping`."""
        method = self.method.get(id(definition), False)
        for param, path in sorted(self._return_params.get(id(definition), ()), key=repr):
            for argument, at in _passed(node, definition, param, method, path):
                if not self._display_path(argument, at, parsed, scope, seen):
                    return False
        return True

    def _display_path(self, node, path, parsed, scope, seen=frozenset()) -> bool:
        """(Phase D fix 1, A16.1) The strict proof of the part ``path`` (the flows' path language,
        A15.3) of a value: an element ('iter'), the item under a constant key or index; any other or
        deeper selection is proved on the whole value (coarser, never narrower)."""
        if not path:
            return self.display(node, parsed, scope, seen)
        step, rest = path[0], tuple(path[1:])
        if step == 'iter' and not rest:
            return self._at(node, (), parsed, scope, seen, iterate=True)
        if isinstance(step, tuple) and not rest:
            if isinstance(step[1], str):
                return self._item_display(node, step[1], parsed, scope, seen)
            if isinstance(step[1], int) and not isinstance(step[1], bool):
                return self._at(node, (step[1],), parsed, scope, seen)
        return self.display(node, parsed, scope, seen)

    def _constant_number(self, module, name) -> bool:
        """A module-level int / float constant of a src module (`collector.MAX_LINKS`; SD-3)."""
        if not (self.folder / f'{module}.py').is_file():
            return False
        try:
            other = self.context.module(module, self.folder)
        except (OSError, SyntaxError, ValueError):
            return False
        bindings = other.module_scope.bindings.get(name, ())
        return bool(bindings) and all(kind == 'value' and isinstance(value, ast.Constant)
                                      and isinstance(value.value, (int, float)) and not isinstance(value.value, bool)
                                      for kind, value in bindings)

    # -------------------------------------------------------------- display grammar

    def _call_is_display(self, node, parsed, scope, seen):
        aliases = self._aliases(node.func, parsed, scope)
        if aliases:
            # (A16.3) an aliased callee: the call is display text when the call of each callable the
            # alias holds is
            key = ('alias', id(node))
            if key in seen:
                return False
            seen = seen | {key}
            return all(self._call_is_display(_Call(getattr(node, 'node', node), target, node.args, node.keywords),
                                             parsed, scope, seen) for target in aliases)
        name, base = _func_name(node)
        func = node.func
        if isinstance(func, ast.Name) and name in parsed.from_imports:
            resolved = parsed.from_imports[name]
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            resolved = (parsed.imports.get(base, base), name)
        else:
            resolved = (base, name)
        known = DISPLAY_CALLS | NEUTRAL_CALLS | self.extra
        shown = self._shown_name(node, parsed, scope)
        if shown is not None:
            # (final review 2, F2, A14.2) show() / error_text() render a Message; text fixed in the source
            # comes back unchanged (Chinese in English), so what they are given must not carry any.
            # (Phase D fix 1, A16.1, ZH-CONV-1) the argument by position or by keyword (show(value=X),
            # error_text(error=X)); a keyword-only call is display text only when its value is proved
            return all(self._leaves_ok(value, parsed, scope, at)
                       for value, at in _call_arguments(node, 0, (SHOWN_ARGUMENT[shown],)))
        if resolved in (('copy', 'copy'), ('copy', 'deepcopy')) and len(node.args) == 1 and not node.keywords:
            return self.display(node.args[0], parsed, scope, seen)             # (A16.3) the same value
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'str' \
                and name in STR_METHODS and node.args and parsed.lookup('str', scope) is None:
            bound = _unbound_str_call(node if isinstance(node, _Call) else
                                      _Call(node, node.func, node.args, node.keywords), parsed)
            return self._call_is_display(bound, parsed, scope, seen)           # (A16.3) str.strip(x) is x.strip()
        if (base, name) in NEUTRAL_CALLS or resolved in NEUTRAL_CALLS:
            # (A15.4) language-neutral data: no text fixed in the source may come through them either
            return all(self._leaves_ok(arg, parsed, scope, compositions=False) for arg in node.args)
        if (base, name) in known or resolved in known:
            return True
        if resolved == ('re', 'sub') or (base, name) == ('re', 'sub'):
            # (Phase D fix 1, A16.5) re.sub of display text with a display replacement is display text
            parts = list(node.args[1:3]) + [keyword.value for keyword in node.keywords if keyword.arg in ('repl', 'string')]
            return bool(parts) and all(self.display(part, parsed, scope, seen) for part in parts)
        if resolved[0] == 'textwrap' and name in TEXTWRAP_CALLS:
            # (A16.5) textwrap of display text (and a display prefix / placeholder) is display text
            parts = [value for value, _at in _call_arguments(node, 0, ('text',))]
            parts += [value for value, _at in _call_arguments(node, 1, ('prefix',))] if name == 'indent' else []
            parts += [keyword.value for keyword in node.keywords if keyword.arg == 'placeholder']
            return bool(parts) and all(self.display(part, parsed, scope, seen) for part in parts)
        if isinstance(func, ast.Name) and name == 'format' and node.args and parsed.lookup('format', scope) is None:
            numeric = len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str) and NUMERIC_SPEC.search(node.args[1].value)
            return bool(numeric) or self.display(node.args[0], parsed, scope, seen) and \
                all(self.display(arg, parsed, scope, seen) for arg in node.args[1:])
        if name == 'popitem' and isinstance(func, ast.Attribute) and not node.args:
            return self._mapping_parts_display(func.value, 'items', (), parsed, scope, seen)
        if isinstance(func, ast.Name) and name in NUMBER_CALLS:
            return True
        if isinstance(func, ast.Name) and name == 'sum':                 # SD-3: a number (sum of str raises)
            return len(node.args) < 2 or _numeric(node.args[1])
        if isinstance(func, ast.Name) and name in ('max', 'min'):        # SD-3: one of its display arguments
            values = list(node.args) + [keyword.value for keyword in node.keywords if keyword.arg == 'default']
            return bool(values) and all(_numeric(value) or self.display(value, parsed, scope, seen) for value in values)
        if name == 'count' and isinstance(func, ast.Attribute):
            return True                                           # a number (list / str .count)
        if isinstance(func, ast.Name) and name in ('list', 'tuple', 'sorted', 'reversed', 'set', 'frozenset') \
                and len(node.args) == 1 and name not in parsed.from_imports:     # (A15.3 F7) a sequence of elements
            return self._at(node.args[0], (), parsed, scope, seen, iterate=True)
        if isinstance(func, ast.Name) and name == 'next' and node.args and name not in parsed.from_imports:
            return self._at(node.args[0], (), parsed, scope, seen, iterate=True) and \
                all(self.display(arg, parsed, scope, seen) for arg in node.args[1:2])
        if resolved == ('time', 'strftime') and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str) and NUMERIC_TIME.fullmatch(node.args[0].value):
            return True                                           # A11.4: digits of a time stamp
        if base == 'math':
            return True
        if name == 'str' and isinstance(func, ast.Name):
            return bool(node.args) and self.display(node.args[0], parsed, scope, seen)
        if name == 'join' and isinstance(func, ast.Attribute):
            return self.display(func.value, parsed, scope, seen) and \
                all(self.display(arg, parsed, scope, seen) for arg in node.args)
        if name in ('format', 'format_map') and isinstance(func, ast.Attribute):
            return self.display(func.value, parsed, scope, seen) and \
                all(self.display(arg, parsed, scope, seen) for arg in node.args) and \
                all(self.display(keyword.value, parsed, scope, seen) for keyword in node.keywords)
        if name == 'get' and isinstance(func, ast.Attribute) and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in NEUTRAL_FIELDS \
                and not self._followed(func.value, parsed, scope):
            return True                          # (A16.5, M2) a neutral field of data the flows do not follow
        if name in ('get', 'pop', 'setdefault') and isinstance(func, ast.Attribute) and node.args \
                and isinstance(node.args[0], ast.Constant):
            # (A15.3 F6) the item of a mapping the window builds itself, and the default
            defaults = list(node.args[1:2]) + [keyword.value for keyword in node.keywords if keyword.arg == 'default']
            return self._item_display(func.value, node.args[0].value, parsed, scope, seen) and \
                all(self.display(value, parsed, scope, seen) for value in defaults)
        if name in ('get', 'set', 'insert'):
            return False
        if isinstance(func, ast.Name) and name not in parsed.from_imports and parsed.lookup(name, scope) is None:
            # (A15.3 F7) the elements of an iterable kept by map(str, ...) / filter / iter, a dict() of display text
            if name == 'iter' and len(node.args) == 1:
                return self._at(node.args[0], (), parsed, scope, seen, iterate=True)
            if name == 'filter' and len(node.args) == 2:
                return self._at(node.args[1], (), parsed, scope, seen, iterate=True)
            if name == 'map' and len(node.args) == 2 and isinstance(node.args[0], ast.Name) and node.args[0].id == 'str':
                return self._at(node.args[1], (), parsed, scope, seen, iterate=True)
            if name == 'dict':
                return all(self.display(arg, parsed, scope, seen) for arg in node.args) and \
                    all(self.display(keyword.value, parsed, scope, seen) for keyword in node.keywords)
        if isinstance(func, ast.Name) and name == 'getattr' and len(node.args) >= 2 and name not in parsed.from_imports \
                and isinstance(node.args[0], ast.Name) and node.args[0].id in ('self', 'cls') \
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            # (A15.3 F3) getattr(self, '<attr>'[, default]): the attribute, and the default
            return self._attribute_display(node.args[1].value, (), parsed, seen) and \
                all(self.display(arg, parsed, scope, seen) for arg in node.args[2:3])
        if isinstance(func, ast.Attribute) and name in STR_METHODS:
            # (A15.3 F8) a str method of display text, with the texts it inserts
            inserted = list(node.args[1:2]) if name in ('replace', 'ljust', 'rjust', 'center') else []
            return self.display(func.value, parsed, scope, seen) and \
                all(self.display(arg, parsed, scope, seen) for arg in inserted)
        candidates = self.resolve(node, parsed)
        if not candidates:                      # (A15.3 F9) a lambda called in place or through a local name
            lambdas = [item for _module, item in self._callees(node, parsed, scope) if isinstance(item, ast.Lambda)]
            return bool(lambdas) and all(self.display(item.body, parsed, parsed.scopes.get(item, scope), seen)
                                         for item in lambdas)
        return bool(candidates) and all(id(definition) in self._returns_ok
                                        and self._passed_display(node, parsed, scope, seen, definition)
                                        for _module, definition in candidates)

    def _item_display(self, base, key, parsed, scope, seen, depth=0) -> bool:
        """(A15.3 F6) `base[key]`: the item under `key` of a mapping display the window builds — a
        local name or `self.<attr>` bound only to dict displays, dict() calls and dict comprehensions
        (their items, the ``**`` spreads, and what a mutation puts in) — is display text.  A parameter
        or any other value (an event, a record) is not: show it."""
        if depth > 6:
            return False
        if isinstance(base, ast.Dict):
            for item_key, value in zip(base.keys, base.values):
                if item_key is None:
                    if not self._item_display(value, key, parsed, scope, seen, depth + 1):
                        return False
                elif (not isinstance(item_key, ast.Constant) or item_key.value == key) \
                        and not self.display(value, parsed, scope, seen):
                    return False
            return True
        if isinstance(base, ast.DictComp):
            return self.display(base.value, parsed, parsed.scope_of(base), seen)
        if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == 'dict':
            return all(self._item_display(arg, key, parsed, scope, seen, depth + 1) for arg in base.args) and \
                all(self.display(keyword.value, parsed, scope, seen) for keyword in base.keywords
                    if keyword.arg in (None, key))
        if isinstance(base, (ast.List, ast.Tuple)) and all(isinstance(item, ast.Tuple) and len(item.elts) == 2
                                                           for item in base.elts):
            # (A16.3, F7) the pairs dict() is given: the value of every pair whose key may be `key`
            return all(self.display(item.elts[1], parsed, scope, seen) for item in base.elts
                       if not isinstance(item.elts[0], ast.Constant) or item.elts[0].value == key)
        if isinstance(base, ast.IfExp):
            return self._item_display(base.body, key, parsed, scope, seen, depth + 1) and \
                self._item_display(base.orelse, key, parsed, scope, seen, depth + 1)
        if isinstance(base, ast.Name):
            owner = parsed.lookup(base.id, scope)
            if owner is not None and owner is not parsed.module_scope and base.id in owner.params:
                return self._kwarg_item(owner, base.id, key, parsed, seen)
            if owner is None or base.id in owner.params or base.id in owner.exceptions or owner is parsed.module_scope:
                return False
            mark = ('item', id(owner), base.id, key)
            if mark in seen:
                return True
            seen = seen | {mark}
            bindings = owner.bindings.get(base.id, ())
            return bool(bindings) and all(kind == 'value' and value is not None
                                          and self._item_display(value, key, parsed, owner, seen, depth + 1)
                                          for kind, value in bindings) and \
                self._mutation_items(owner.mutations.get(base.id, ()), key, parsed, owner, seen, depth)
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id in ('self', 'cls'):
            mark = ('item', 'self', base.attr, key)
            if mark in seen:
                return True
            seen = seen | {mark}
            entries = self._flow_attributes(base.attr, parsed)
            if not entries:
                return False
            for value, owner, own_scope, at in entries:
                if at and at[0] in ('element', 'elements', 'item', 'items'):
                    if not self._mutation_items([(at[0], value, at[1])], key, owner, own_scope, seen, depth):
                        return False
                elif at or not self._item_display(value, key, owner, own_scope, seen, depth + 1):
                    return False
            return True
        return False

    def _kwarg_item(self, owner, name, key, parsed, seen) -> bool:
        """(Phase D fix 1, A16.1; F10 `**param`) ``kw['text']`` of a function's own ``**kw``: a sink
        parameter with the path of its item, so what each caller passes as ``text=`` is checked
        there; any other parameter's item (an event, a record), a rebound one, or a parameter of a
        function used as a value is not display text (show it)."""
        function = owner.node
        kwarg = getattr(getattr(function, 'args', None), 'kwarg', None)
        if isinstance(function, ast.Lambda) or kwarg is None or kwarg.arg != name or id(function) in self.referenced:
            return False
        if owner.bindings.get(name) or owner.positions.get(name):
            return False
        # what the function itself puts under the key (`kwargs.setdefault('font', ...)` puts nothing there)
        if not self._mutation_items(owner.mutations.get(name, ()), key, parsed, owner, seen, 0):
            return False
        self._accepted_params.append((function, name, (('key', key),)))
        return True

    def _mapping_parts_display(self, base, part, path, parsed, scope, seen, depth=0) -> bool:
        """(A15.3 F6) The values / keys / (key, value) pairs a mapping display the window builds gives
        to an iteration; `path` selects the element of a pair."""
        if depth > 6:
            return False
        want_keys = part == 'keys' or part == 'items' and path[:1] in ((), (0,))
        want_values = part == 'values' or part == 'items' and path[:1] in ((), (1,))
        rest = path[1:] if part == 'items' else path
        if isinstance(base, ast.Dict):
            for key, value in zip(base.keys, base.values):
                if key is None:
                    if not self._mapping_parts_display(value, part, path, parsed, scope, seen, depth + 1):
                        return False
                    continue
                if want_keys and not self._at(key, rest, parsed, scope, seen):
                    return False
                if want_values and not self._at(value, rest, parsed, scope, seen):
                    return False
            return True
        if isinstance(base, ast.Name):
            owner = parsed.lookup(base.id, scope)
            if owner is None or base.id in owner.params or owner is parsed.module_scope:
                return False
            bindings = owner.bindings.get(base.id, ())
            return bool(bindings) and not owner.mutations.get(base.id) and all(
                kind == 'value' and value is not None
                and self._mapping_parts_display(value, part, path, parsed, owner, seen, depth + 1)
                for kind, value in bindings)
        return False

    def _mutation_items(self, mutations, key, parsed, scope, seen, depth) -> bool:
        for how, value, item_key in mutations:
            if how == 'item':
                constant = item_key.value if isinstance(item_key, ast.Constant) else None
                if (constant is None or constant == key) and not self.display(value, parsed, scope, seen):
                    return False
            elif how == 'items':
                if not self._item_display(value, key, parsed, scope, seen, depth + 1):
                    return False
            else:
                return False                    # a sequence mutated into: no mapping item
        return True

    def display(self, node, parsed, scope, seen=frozenset()):
        if self._parts is not None and isinstance(node, (ast.Call, ast.Subscript, ast.Attribute, ast.Name)):
            function, texts = self._parts                 # (A16.6) a part a scoped entry names
            if ast.unparse(node) in texts and _qualname(parsed, node) == function:
                return True
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and CJK.search(node.value):
                return False                     # (final review 1, F1) a Chinese literal at a sink: G1b has no slot there
            if isinstance(node.value, str) and not self._leaf_ok(('text', node, parsed)):
                return False                     # (A15.2) a text of a class body or of the module level
            return isinstance(node.value, (str, int, float)) and not isinstance(node.value, bool) or node.value is None
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and CJK.search(value.value):
                    return False
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    numeric_spec = value.format_spec is not None and \
                        re.search(r'[dfge%n]$', ''.join(str(v.value) for v in value.format_spec.values
                                                           if isinstance(v, ast.Constant)))
                    if not numeric_spec and not self.display(value.value, parsed, scope, seen):
                        return False
            return True
        if isinstance(node, ast.BinOp):
            # (Phase D fix 1, A16.5; Q1 / B3) `text * n` repeats a text (F8): a product is display text
            # only when both factors are (a number is); `+ - / //` with a number is arithmetic (text
            # with a number raises), unless a side is a str literal or an f-string
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Div, ast.FloorDiv)) and \
                    not any(isinstance(side, (ast.Constant, ast.JoinedStr)) and isinstance(getattr(side, 'value', None), str)
                            or isinstance(side, ast.JoinedStr) for side in (node.left, node.right)) and \
                    (_numeric(node.left) or _numeric(node.right)):
                return True                                       # arithmetic of numbers
            return self.display(node.left, parsed, scope, seen) and self.display(node.right, parsed, scope, seen)
        if isinstance(node, ast.UnaryOp):
            return self.display(node.operand, parsed, scope, seen)
        if isinstance(node, ast.IfExp):
            return self.display(node.body, parsed, scope, seen) and self.display(node.orelse, parsed, scope, seen)
        if isinstance(node, ast.BoolOp):
            return all(self.display(value, parsed, scope, seen) for value in node.values)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(self.display(item, parsed, scope, seen) for item in node.elts)
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            return self.display(node.elt, parsed, parsed.scope_of(node), seen)
        if isinstance(node, ast.Dict):                  # (A15.4) a mapping of labels shows its values
            return all(self.display(value, parsed, scope, seen) for value in node.values)
        if isinstance(node, ast.DictComp):
            return self.display(node.value, parsed, parsed.scope_of(node), seen)
        if isinstance(node, ast.Starred):
            return self.display(node.value, parsed, scope, seen)
        if isinstance(node, ast.Call):
            return self._call_is_display(node, parsed, scope, seen)
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in NEUTRAL_FIELDS \
                    and not self._followed(node.value, parsed, scope):
                return True                      # (A16.5, M2) a neutral field of data the flows do not follow
            # (A15.3 F6) an element of a sequence by its index, or a slice of it; the item of a mapping
            # display under a constant key (not of a parameter, an event or a record: show those)
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return self._item_display(node.value, key.value, parsed, scope, seen)
            if isinstance(key, ast.Constant) and isinstance(key.value, int) and not isinstance(key.value, bool):
                return self._at(node.value, (key.value,), parsed, scope, seen)
            if isinstance(key, ast.UnaryOp) and isinstance(key.op, ast.USub) and isinstance(key.operand, ast.Constant) \
                    and isinstance(key.operand.value, int):
                return self._at(node.value, (-key.operand.value,), parsed, scope, seen)
            if isinstance(key, ast.Slice):
                return self._at(node.value, (), parsed, scope, seen, iterate=True)
            return False
        if isinstance(node, ast.Attribute):
            base = node.value
            if node.attr == 'name' and _path_call(base):
                # (A16.5, B3 / Q1; F11) Path(x).name is language-neutral only when no source reaches x
                return all(self._leaves_ok(arg, parsed, scope, compositions=False) for arg in base.args)
            if node.attr in NEUTRAL_FIELDS and not (isinstance(base, ast.Name) and base.id in ('self', 'cls')
                                                    and self._flow_attributes(node.attr, parsed)):
                return True                      # (A16.5, M2) a neutral field of data the flows do not follow
            if isinstance(base, ast.Name) and base.id in parsed.imports:
                return self._constant_number(parsed.imports[base.id], node.attr)      # SD-3
            if isinstance(base, ast.Name) and (base.id in ('self', 'cls') or self._flow_class(base.id, parsed)
                                               and parsed.lookup(base.id, scope) in (None, parsed.module_scope)):
                return self._attribute_display(node.attr, (), parsed, seen)
            return False
        if isinstance(node, ast.Name):
            return self._name_is_display(node.id, parsed, scope, seen, node)
        return False

    def _followed(self, base, parsed, scope) -> bool:
        """(Phase D fix 1, A16.5; M2) Is ``base`` a mapping the flows follow — a dict display, a dict()
        call or dict comprehension, a local name bound to them, ``self.<attr>`` with assignments in
        the window, a conditional of such?  Then its neutral-named item is proved by its bindings (F3
        / F5), not by its name; the name rule is for event and record values and parameters."""
        if isinstance(base, (ast.Dict, ast.DictComp)):
            return True
        if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == 'dict' \
                and parsed.lookup('dict', scope) is None:
            return True
        if isinstance(base, ast.IfExp):
            return self._followed(base.body, parsed, scope) or self._followed(base.orelse, parsed, scope)
        if isinstance(base, ast.Name):
            owner = parsed.lookup(base.id, scope)
            if owner is None or owner is parsed.module_scope or base.id in owner.params:
                return False
            bindings = owner.bindings.get(base.id, ())
            return bool(bindings) and all(isinstance(value, (ast.Dict, ast.DictComp)) for _kind, value in bindings)
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id in ('self', 'cls'):
            return bool(self._flow_attributes(base.attr, parsed))
        return False

    def _shown_on_entry(self, function, name, parsed, owner):
        """The value node of the function's first statement (after its docstring) when it is
        `name = show(name)` / `name = error_text(name)`: from then on the name holds shown text; None
        otherwise."""
        body = getattr(function, 'body', None)
        if not isinstance(body, list) or not body:
            return None
        first = body[1] if len(body) > 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str) else body[0]
        if isinstance(first, ast.Assign) and len(first.targets) == 1 and isinstance(first.targets[0], ast.Name) \
                and first.targets[0].id == name and isinstance(first.value, ast.Call) and first.value.args \
                and isinstance(first.value.args[0], ast.Name) and first.value.args[0].id == name \
                and self._show_call(first.value, parsed, owner):
            return first.value
        return None

    @staticmethod
    def _inside_handler(parsed, use, name, owner) -> bool:
        """Is this use of `name` inside an `except ... as name` block of its scope?  Python unbinds the
        name when the block ends, so elsewhere the binding in force is another one (SD-W5-1)."""
        node = parsed.parents.get(use)
        while node is not None and node is not owner.node:
            if isinstance(node, ast.ExceptHandler) and node.name == name:
                return True
            node = parsed.parents.get(node)
        return False

    def _name_is_display(self, name, parsed, scope, seen, use=None):
        owner = parsed.lookup(name, scope)
        if owner is None:
            if name in parsed.from_imports:
                return self._constant_number(*parsed.from_imports[name])             # SD-3
            return False
        bindings = owner.bindings.get(name, ())
        if name in owner.exceptions:
            if use is None or self._inside_handler(parsed, use, name, owner):
                return False                     # the exception object (show it with error_text)
            bindings = [binding for binding in bindings if binding[0] != 'except']
        if owner is parsed.module_scope:
            # (final review 1, F1; 7.9 / A7.3) a name bound at module scope is display text only as an
            # int / float constant: a module-level str (a UI text constant, as 1.8.1 had,
            # or any other text) at a sink needs show(), t() or a scoped sinks entry, as 7.9 says.
            return bool(bindings) and name not in owner.positions and all(
                kind == 'value' and isinstance(value, ast.Constant) and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool) for kind, value in bindings)
        if name in owner.params and owner is not parsed.module_scope:
            function = owner.node
            shown = self._shown_on_entry(function, name, parsed, owner)
            if shown is not None:
                # (A15.3 F1) the function's first statement replaces the parameter by its shown value
                # (`line = messages.show(line)`, ui_log.append_log): later uses see that text, and
                # every later binding must be display text with it; what the callers pass is checked
                # by the value rule of that show() (A15.4)
                key = (id(owner), name)
                if key in seen:
                    return True
                inner = seen | {key}
                return all(self.display(value, parsed, owner, inner) for kind, value in bindings
                           if value is not None and value is not shown and kind in ('value', 'with')) and \
                    all(self._at(value, path, parsed, owner, inner, iterate=kind == 'iter')
                        for kind, value, path in owner.positions.get(name, ())) and \
                    self._mutations_display(owner.mutations.get(name, ()), (), parsed, owner, inner)
            if (not isinstance(function, ast.Lambda) or self._called_lambda(function, parsed)) \
                    and name not in ('self', 'cls') and id(function) not in self.referenced:
                # (final review 2, F1, A14.1) a call that leaves the argument out supplies the default,
                # which no caller check sees: it must be display text itself
                default = _param_default(function, name)
                if default is not None and not self.display(default, parsed, owner.parent, seen):
                    return False
                # (A15.3 F1) a parameter rebound in the function (`text = text or DEFAULT`): every
                # binding must be display text as well, not only what the callers pass
                key = (id(owner), name)
                if key not in seen:
                    inner = seen | {key}
                    for kind, value in bindings:
                        if value is not None and kind in ('value', 'with') \
                                and not self.display(value, parsed, owner, inner):
                            return False
                    for kind, value, path in owner.positions.get(name, ()):
                        if not self._at(value, path, parsed, owner, inner, iterate=kind == 'iter'):
                            return False
                self._accepted_params.append((function, name, ()))
                return True
            return False
        key = (id(owner), name)
        if key in seen:
            return True                          # a cycle adds nothing new
        seen = seen | {key}
        if not bindings:
            return False
        for kind, value in bindings:
            if value is None:
                return False
            if kind in ('value', 'with') and not self.display(value, parsed, owner, seen):
                return False
        for kind, value, path in owner.positions.get(name, ()):                       # SD-2
            if not self._at(value, path, parsed, owner, seen, iterate=kind == 'iter'):
                return False
        return self._mutations_display(owner.mutations.get(name, ()), (), parsed, owner, seen)

    def _mutations_display(self, mutations, path, parsed, scope, seen, iterate=False):
        """(A15.3 F5) Every element or item a mutation puts into the container is display text (at
        `path`); a mapping's key is checked where iteration yields it."""
        for how, value, key in mutations:
            rest = path if iterate else path[1:]
            if how in ('element', 'item'):
                if not self._at(value, rest, parsed, scope, seen):
                    return False
                if how == 'item' and iterate and key is not None and not self.display(key, parsed, scope, seen):
                    return False
            elif not self._at(value, rest, parsed, scope, seen, iterate=True):
                return False
        return True

    def _attribute_display(self, attr, path, parsed, seen, iterate=False):
        """(A15.3 F3) `self.<attr>` in the window: every assignment of it (element by element) and
        every mutation of it is display text; an attribute nothing assigns is not."""
        key = ('self', attr, path, iterate)
        if key in seen:
            return True
        seen = seen | {key}
        entries = self._flow_attributes(attr, parsed)
        if not entries:
            return False
        for value, owner, scope, at in entries:
            if at and at[0] in ('element', 'elements', 'item', 'items'):
                if not self._mutations_display([(at[0], value, at[1])], path, owner, scope, seen, iterate):
                    return False
            elif iterate:
                # the elements of the value at position `at` (the whole of it when it is a position)
                if not (self._at(value, path, owner, scope, seen, iterate=True) if not at
                        else self._at(value, tuple(at), owner, scope, seen)):
                    return False
            elif not self._at(value, tuple(at) + tuple(path), owner, scope, seen):
                return False
        return True

    def _at(self, node, path, parsed, scope, seen, iterate=False):
        """Is every value that a target at `path` receives from `node` display text?  `iterate`: the
        target takes the elements of `node` (a for target).  An unpacked tuple is checked element by
        element, not as a whole (I-2 amendment A7, SD-2); `divmod` gives numbers (SD-5)."""
        if iterate:
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                return all(self._at(item.value, path, parsed, scope, seen, iterate=True) if isinstance(item, ast.Starred)
                           else self._at(item, path, parsed, scope, seen) for item in node.elts)
            if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                return self._at(node.elt, path, parsed, parsed.scope_of(node), seen)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args \
                    and node.func.id not in parsed.from_imports:
                name = node.func.id
                if name == 'enumerate':
                    if not path or path[0] is None or path[0] > 1:
                        return False
                    return path[0] == 0 or self._at(node.args[0], path[1:], parsed, scope, seen, iterate=True)
                if name in ('reversed', 'sorted', 'list', 'tuple', 'set', 'frozenset', 'iter'):
                    return self._at(node.args[0], path, parsed, scope, seen, iterate=True)
                if name == 'filter' and len(node.args) == 2:
                    return self._at(node.args[1], path, parsed, scope, seen, iterate=True)
                if name == 'map' and len(node.args) == 2 and isinstance(node.args[0], ast.Name) \
                        and node.args[0].id == 'str' and not path:
                    return self._at(node.args[1], (), parsed, scope, seen, iterate=True)
                if name == 'zip':
                    if not path or path[0] is None or path[0] >= len(node.args):
                        return False
                    return self._at(node.args[path[0]], path[1:], parsed, scope, seen, iterate=True)
                if name == 'range':
                    return not path
            if isinstance(node, ast.Name):
                return self._bound_at(node, path, parsed, scope, seen, iterate=True)
            # (A15.3) the elements of a concatenation, a slice, a conditional or `or` / `and`
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return self._at(node.left, path, parsed, scope, seen, True) and \
                    self._at(node.right, path, parsed, scope, seen, True)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                return self._at(node.value, path, parsed, scope, seen, True)
            if isinstance(node, ast.IfExp):
                return self._at(node.body, path, parsed, scope, seen, True) and \
                    self._at(node.orelse, path, parsed, scope, seen, True)
            if isinstance(node, ast.BoolOp):
                return all(self._at(value, path, parsed, scope, seen, True) for value in node.values)
            if isinstance(node, ast.Constant) and node.value is None:
                return True                      # `... or None`: nothing to iterate
            if isinstance(node, ast.Dict):       # (A16.3, F5) iterating a mapping gives its keys
                return all(key is None and self._at(value, path, parsed, scope, seen, True)
                           or key is not None and self._at(key, path, parsed, scope, seen)
                           for key, value in zip(node.keys, node.values))
            if isinstance(node, ast.DictComp):
                return self._at(node.key, path, parsed, parsed.scope_of(node), seen)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.args and not node.keywords \
                    and node.func.id in ('set', 'list', 'tuple', 'dict', 'frozenset') \
                    and parsed.lookup(node.func.id, scope) is None:
                return True                      # an empty container
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and not node.args \
                    and node.func.attr in ('values', 'keys', 'items'):
                return self._mapping_parts_display(node.func.value, node.func.attr, path, parsed, scope, seen)
            if isinstance(node, ast.Call):
                candidates = self.resolve(node, parsed)
                return bool(candidates) and all(self._returns_at(module, definition, path, seen, iterate=True)
                                                for module, definition in candidates)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls'):
                return self._attribute_display(node.attr, path, parsed, seen, iterate=True)
            return False
        if not path:
            return self.display(node, parsed, scope, seen)
        step, rest = path[0], path[1:]
        if step is None:                         # after a starred target: any element
            return self._at(node, rest, parsed, scope, seen, iterate=True)
        if isinstance(node, (ast.Tuple, ast.List)):
            if any(isinstance(item, ast.Starred) for item in node.elts):
                # (A15.3 F5) a starred element: the selected one is any element
                return self._at(node, rest, parsed, scope, seen, iterate=True)
            return -len(node.elts) <= step < len(node.elts) and self._at(node.elts[step], rest, parsed, scope, seen)
        if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
            return self._at(node.elt, rest, parsed, parsed.scope_of(node), seen)
        if isinstance(node, ast.IfExp):
            return self._at(node.body, path, parsed, scope, seen) and self._at(node.orelse, path, parsed, scope, seen)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'divmod' and node.func.id not in parsed.from_imports:
                return not rest                  # (quotient, remainder): numbers
            candidates = self.resolve(node, parsed)
            if candidates and all(self._returns_at(module, definition, path, seen) for module, definition in candidates):
                return True
        if isinstance(node, ast.Name):
            return self._bound_at(node, path, parsed, scope, seen)
        return self.display(node, parsed, scope, seen)    # every part of a display text is display text

    def _bound_at(self, node, path, parsed, scope, seen, iterate=False):
        """The element at `path` of a name bound only by plain assignments; of a parameter, what the
        callers pass (the parameter is then checked at the calls, A15.3 F10)."""
        owner = parsed.lookup(node.id, scope)
        if owner is not None and node.id in owner.params and owner is not parsed.module_scope:
            return self._name_is_display(node.id, parsed, scope, seen, node)
        if owner is None or node.id in owner.params or node.id in owner.exceptions:
            return False
        key = ('at', id(owner), node.id, path, iterate)
        if key in seen:
            return True
        seen = seen | {key}
        bindings = owner.bindings.get(node.id, ())
        mutations = owner.mutations.get(node.id, ())
        if mutations and path and not iterate:
            path, iterate = path[1:], True       # (A15.3 F5) a mutated container: the element is any element
        if not bindings:
            return False
        for kind, value in bindings:
            if value is None:
                return False
            if kind in ('value', 'with') and not self._at(value, path, parsed, owner, seen, iterate):
                return False
        for kind, value, at in owner.positions.get(node.id, ()):           # what an unpacking target holds
            if None in at or iterate:
                # the elements of what the target holds: every element of the value (of the part) whole
                ok = self._at(value, (), parsed, owner, seen, iterate=True) if kind == 'iter' or None in at \
                    else self._at(value, at, parsed, owner, seen)
            else:
                ok = self._at(value, tuple(at) + tuple(path), parsed, owner, seen, iterate=kind == 'iter')
            if not ok:
                return False
        return self._mutations_display(mutations, path, parsed, owner, seen, iterate)

    def _returns_at(self, module, definition, path, seen, iterate=False):
        """The element at `path` of every value the function returns (of each element of it when
        `iterate`)."""
        key = ('return', id(definition), path, iterate)
        if key in seen:
            return True
        seen = seen | {key}
        parsed = self.parsed[module]
        own = parsed.scopes.get(definition)
        values = [node.value for node in ast.walk(definition)
                  if isinstance(node, ast.Return) and node.value is not None and parsed.scope_of(node) is own]
        yields = [node for node in ast.walk(definition)
                  if isinstance(node, (ast.Yield, ast.YieldFrom)) and parsed.scope_of(node) is own]
        if yields:
            # (Phase D fix 1, A16.3; Q6) a generator: iterating its call gives what it yields (the
            # flows read yield as _Flow._returned_values does); taken whole it is no display text
            if not iterate:
                return False
            return all(item.value is None or (self._at(item.value, path, parsed, own, seen, iterate=True)
                                               if isinstance(item, ast.YieldFrom)
                                               else self._at(item.value, path, parsed, own, seen))
                       for item in yields)
        return bool(values) and all(self._at(value, path, parsed, own, seen, iterate=iterate) for value in values)

    # -------------------------------------------------------------- sink positions

    def _show_call(self, call, parsed, scope) -> bool:
        """messages.show / messages.error_text, or the bare name imported from messages."""
        return self._shown_name(call, parsed, scope) is not None

    def _shown_name(self, call, parsed, scope):
        """'show' / 'error_text' for a call of messages.show / messages.error_text (also the bare name
        imported from messages), else None."""
        name, base = _func_name(call)
        func = call.func
        if isinstance(func, ast.Name):
            if name in parsed.from_imports:
                module, original = parsed.from_imports[name]
                return original if (module, original) in SHOW_CALLS else None
            return name if name in DISPLAY_BARE and parsed.lookup(name, scope) is None and name not in self.defs \
                else None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return name if (parsed.imports.get(base, base), name) in SHOW_CALLS else None
        return None

    def sink_positions(self, parsed):
        """(expression, sink description, mode, path) of every sink position of a module: every call,
        the callback forms included (A15.4), against the SINKS table; mode 'value' for the value
        positions (append_log, show() / error_text(), value-mode sink parameters, table headings).
        (Phase D fix 1, A16.1) Every sink argument is read by position or by its parameter name, a
        ``**mapping`` gives its item under each such name, a callee is matched by the name it really
        has (imports, from-imports, aliases: A16.3), and Chip.set / the text-box insert are known by
        what the receiver is bound to."""
        found = []

        def add(kind, expression, description, path=()):
            mode = SINK_KINDS[kind]
            if self.kinds is None or kind in self.kinds:
                found.append((expression, description, mode, tuple(path)))

        def add_all(kind, pairs, description, path=()):
            for expression, at in pairs:
                add(kind, expression, description, tuple(at) + tuple(path))

        for site in self._sites[parsed.name]:
            scope = parsed.scope_of(site.node)
            for call in self._variants(site, parsed, scope):
                self._call_sinks(call, parsed, scope, add, add_all)
        unique, positions = set(), []
        for expression, sink, mode, path in found:
            if (id(expression), sink, path) not in unique:
                unique.add((id(expression), sink, path))
                positions.append((expression, sink, mode, path))
        return positions

    def _call_sinks(self, call, parsed, scope, add, add_all):
        """The sink positions of one call (sink_positions)."""
        name, _base = _func_name(call)
        func = call.func
        qualified = self._qualified(func, parsed, scope) or ''
        module, _dot, last = qualified.rpartition('.')
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        attribute = isinstance(func, ast.Attribute)
        if name in ('configure', 'config') and attribute:
            for arg in sorted(TEXT_KWARGS):
                add_all('configure', _call_arguments(call, None, (arg,)), f'{name}({arg}=)')
            if call.args and self._mapping_value(call.args[0], parsed, scope):
                # the tkinter form configure({'text': ...}): a cnf mapping as the first argument
                for arg in sorted(TEXT_KWARGS):
                    add('configure', call.args[0], f'{name}({arg}=)', (('key', arg),))
        if module in WIDGET_MODULES or module == 'ui_theme' and last[:1].isupper():
            for arg in sorted(TEXT_KWARGS | {'values'}):
                add_all('constructor', _call_arguments(call, None, (arg,)), f'{qualified}({arg}=)')
        if module == 'ui_theme' and last in THEME_SINKS:
            for param, (position, kind, path) in THEME_SINKS[last].items():
                if kind == 'theme unlabelled' and ('labels' in keywords or len(call.args) > 2):
                    continue
                label = f'ui_theme.{last} {param}' if param != 'text' else f'ui_theme.{last} text'
                add_all(kind, _call_arguments(call, position, (param,)), label, path)
        if name == 'set' and attribute and self._receiver_is('chip', func.value, parsed, scope):
            add_all('chip set', _call_arguments(call, 0, ('text',)), 'Chip.set')
        if name == 'heading':
            add_all('heading', _call_arguments(call, None, ('text',)), 'heading(text=)')
        if name in ('title', 'wm_title') and attribute and len(call.args) <= 1 \
                and (call.args or any(keyword.arg in ('string', None) for keyword in call.keywords)):
            add_all('title', _call_arguments(call, 0, ('string',)), 'title()')
        if name == 'set_empty':
            add_all('set_empty', _call_arguments(call, 0, ('text',)), 'set_empty')
        if name == 'fill' and attribute:
            for rows, at in _call_arguments(call, 0, ('rows',)):
                for item in (self._fill_values(rows) if not at else [rows]):
                    add('fill', item, 'Treeview.fill values', at)
        if name in ('insert', 'item') and attribute:
            add_all('values', _call_arguments(call, None, ('values',)), f'{name}(values=)')
            if name == 'insert' and self._receiver_is('textbox', func.value, parsed, scope):
                add_all('textbox insert', _call_arguments(call, 1, ('chars', 'text')), 'textbox insert')
        if name in MENU_ITEMS and attribute:
            add_all('menu', _call_arguments(call, None, ('label',)), f'{name}(label=)')
        if module.rpartition('.')[2] == 'messagebox' and last in MESSAGEBOXES:
            for position, arg in enumerate(MESSAGEBOX_TEXTS):       # title, message (also positional), detail
                add_all('messagebox', _call_arguments(call, position if position < 2 else None, (arg,)),
                        f'messagebox.{last}' if position < 2 else f'messagebox.{last}({arg}=)')
        if module.rpartition('.')[2] == 'filedialog' and last in FILEDIALOGS:
            for arg in FILEDIALOG_TEXTS:
                add_all('filedialog', _call_arguments(call, None, (arg,)), f'filedialog.{last}({arg}=)')
        if qualified == 'i18n.t':
            for keyword in call.keywords:
                if keyword.arg is not None:
                    add('library filetext t()' if parsed.name == 'library' else 't()', keyword.value,
                        f't({keyword.arg}=)')
                else:
                    add('t() mapping', keyword.value, 't(**)', ('values',))
        if name in VALUE_SINKS:
            add_all('append_log', _call_arguments(call, 0, (VALUE_SINK_PARAM,)), f'{name}()')
        shown = self._shown_name(call, parsed, scope)
        if shown is not None:
            add_all('show()', _call_arguments(call, 0, (SHOWN_ARGUMENT[shown],)), f'{shown}() argument')
        for _module, definition in self.resolve(call, parsed) + self._lambda_targets(call, parsed, scope):  # SD-1
            method = self.method.get(id(definition), False)
            vararg = definition.args.vararg.arg if definition.args.vararg is not None else None
            for param, path in sorted(self._def_params.get(id(definition), ()), key=repr):
                spreads = {id(keyword.value) for keyword in call.keywords if keyword.arg is None}
                starred = {id(item.value) for item in call.args if isinstance(item, ast.Starred)}
                for argument, at in _passed(call, definition, param, method, path):
                    how = '**' if id(argument) in spreads else f'*{param}' if param == vararg else \
                        '*' if id(argument) in starred else param
                    add('sink method', argument, f'{name}({how}) sink method', at)
            for param, path in sorted(self._value_def_params.get(id(definition), ()), key=repr):
                for argument, at in _passed(call, definition, param, method, path):
                    add('value sink method', argument, f'{name}({param}) value sink method', at)

    def _mapping_value(self, node, parsed, scope) -> bool:
        """A dict display, or a local name bound only to dict displays (a tkinter cnf mapping)."""
        if isinstance(node, ast.Dict):
            return True
        owner = parsed.lookup(node.id, scope) if isinstance(node, ast.Name) else None
        if owner is None or owner is parsed.module_scope or node.id in owner.params:
            return False
        bindings = owner.bindings.get(node.id, ())
        return bool(bindings) and all(isinstance(value, ast.Dict) for _kind, value in bindings)

    @staticmethod
    def _fill_values(rows):
        """The values of each (iid, values) row of a Treeview.fill argument."""
        if isinstance(rows, (ast.GeneratorExp, ast.ListComp)) and isinstance(rows.elt, ast.Tuple) \
                and len(rows.elt.elts) == 2:
            return [rows.elt.elts[1]]
        if isinstance(rows, (ast.List, ast.Tuple)):
            values = []
            for row in rows.elts:
                values.append(row.elts[1] if isinstance(row, ast.Tuple) and len(row.elts) == 2 else row)
            return values
        return [rows]

    # -------------------------------------------------------------- fixed points

    def _returns(self):
        """Functions whose every return value is display text (fixed point over the scanned modules),
        with the parameters that returned text depends on: a call is display text only when those
        parameters receive display text (I-2 amendment A7)."""
        changed = True
        while changed:
            changed = False
            for name, definitions in self.defs.items():
                for module, definition in definitions:
                    if id(definition) in self._returns_ok:
                        continue
                    parsed = self.parsed[module]
                    own = parsed.scopes.get(definition)
                    saved, self._accepted_params = self._accepted_params, []
                    ok, any_return = True, False
                    for node in ast.walk(definition):
                        if isinstance(node, ast.Return) and node.value is not None and parsed.scope_of(node) is own:
                            any_return = True
                            if not self.display(node.value, parsed, own):
                                ok = False
                                break
                    accepted, self._accepted_params = self._accepted_params, saved
                    if ok and any_return:
                        self._returns_ok.add(id(definition))
                        self.returns_display.add(name)
                        for function, param, path in accepted:
                            if function is definition:
                                self._return_params[id(definition)].add((param, path))
                            else:                                     # a closure's outer parameter
                                self._def_params[id(function)].add((param, path))
                                self.sink_params[getattr(function, 'name', 'lambda')].add(param)
                        changed = True

    def _callees(self, node, parsed, scope):
        """The definitions a call can reach (resolve), also through a local name bound only as a `for`
        target over a tuple / list display of function names (`for tree, rows, render in [(.., ..,
        history_row), ...]: render(row)`, library_ui.refresh_library); [] when unknown."""
        if isinstance(node.func, ast.Lambda):
            return [(parsed.name, node.func)]
        found = self.resolve(node, parsed)
        if found or not isinstance(node.func, ast.Name):
            return found
        lambdas = _lambda_bindings(node.func.id, parsed, scope)
        if lambdas:
            return [(parsed.name, item) for item in lambdas]
        owner = parsed.lookup(node.func.id, scope)
        if owner is None or node.func.id in owner.params or owner.bindings.get(node.func.id) and \
                any(kind != 'iter' for kind, _value in owner.bindings[node.func.id]):
            return []
        positions = owner.positions.get(node.func.id, ())
        if not positions:
            return []
        for kind, iterable, path in positions:
            if kind != 'iter' or not isinstance(iterable, (ast.Tuple, ast.List)) or not path or None in path:
                return []
            for item in iterable.elts:
                element = item
                for step in path:
                    if not isinstance(element, (ast.Tuple, ast.List)) or step >= len(element.elts):
                        return []
                    element = element.elts[step]
                if not isinstance(element, ast.Name):
                    return []
                reached = self.resolve(element, parsed)
                if not reached:
                    return []
                found += reached
        return found

    def _cells(self, expression, parsed, scope):
        """(WP-6, RV-WP5-1, amendment A11.4) The cells of a table row made by a UI function: for a call
        whose every reachable definition returns only tuple displays, [(cell, module, scope)] of every
        element of every return; None otherwise (the row is then checked as a whole)."""
        if not isinstance(expression, ast.Call):
            return None
        cells = []
        for module, definition in self._callees(expression, parsed, scope):
            own_parsed = self.parsed[module]
            own = own_parsed.scopes.get(definition)
            values = [node.value for node in ast.walk(definition) if isinstance(node, ast.Return)
                      and node.value is not None and own_parsed.scope_of(node) is own]
            if not values or not all(isinstance(value, ast.Tuple) and not any(isinstance(item, ast.Starred)
                                                                               for item in value.elts)
                                     for value in values):
                return None
            cells += [(item, module, own) for value in values for item in value.elts]
        return cells or None

    def _other(self, module):
        if module in self.parsed:
            return self.parsed[module]
        if not (self.folder / f'{module}.py').is_file():
            return None
        try:
            return self.context.module(module, self.folder)
        except (OSError, SyntaxError, ValueError):
            return None

    @staticmethod
    def _leaf_ok(leaf) -> bool:
        """A literal written inside a function without a Chinese character is display text (7.9); a
        module-level or class-level text is not, and a Chinese literal is not (A13.1, A15.2)."""
        constant, parsed = leaf[1], leaf[2]
        if CJK.search(constant.value):
            return False
        node = parsed.parents.get(constant)
        while node is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return True
            node = parsed.parents.get(node)
        return False

    def _leaves_ok(self, node, parsed, scope, path=(), compositions=True, record=True) -> bool:
        """(A15.4 the value rule) No leaf of the value is text fixed in the source that is no display
        text — a literal outside a function, a Chinese literal, an enum source, a zh template —; a
        composition (f-string, +, %, .join, .format) among its leaves must be display text as a whole
        (7.9); a parameter is recorded as a value-mode sink parameter, so what every call passes to it
        is checked by this rule too (A15.3 F10)."""
        ok = True
        for leaf in self.leaves(node, parsed, scope, True, path):
            kind = leaf[0]
            if kind == 'text':
                ok = ok and self._leaf_ok(leaf)
            elif kind == 'enum':
                ok = False
            elif kind == 'composed' and compositions:
                if leaf[3] in VALUE_COMPOSITIONS and not self.display(leaf[1], leaf[2], leaf[2].scope_of(leaf[1])):
                    ok = False
            elif kind == 'param' and record and leaf[3].name in self.parsed:
                self._value_params.append((leaf[1], leaf[2], leaf[4], leaf[3]))
        return ok

    def strict_ok(self, node, path, parsed, scope, parts=None) -> bool:
        """(Phase D fix 1, A16.5; Q6) A strict sink position passes when the 7.9 grammar proves it display
        text *and* the flows (_Flow.leaves, the one implementation G1b, G1f and the value rule use)
        find no source of A15.2 among its leaves: every strict sink is also a value sink, so the
        grammar can never accept what the flows trace to a source (the divergences of Q1 / B3 / M2:
        `NO_PENDING * 1`, `Path(NO_PENDING).name`, `self.version = NO_PENDING`)."""
        recording = self.judgements is not None
        grammar = flows = True
        if recording or self.strict in ('both', 'grammar'):
            self._parts = parts
            try:
                grammar = self._display_path(node, path, parsed, scope)
            finally:
                self._parts = None
        if recording or self.strict in ('both', 'flows') and (grammar or self.strict == 'flows'):
            flows = self._leaves_ok(node, parsed, scope, path, compositions=False, record=False)
        if recording:
            self.judgements.append((parsed.path.name, getattr(node, 'lineno', 0), grammar, flows))
        return (grammar or self.strict == 'flows') and (flows or self.strict == 'grammar')

    def run(self, allow_for=None):
        """[finding] of every sink position that is not display text (and not allow-listed).  A table
        row made by a UI function is checked cell by cell in that function (A11.4)."""
        self._returns()
        while True:
            findings, reported = [], set()
            self._accepted_params = []
            if self.judgements is not None:
                self.judgements = []
            for module, parsed in self.parsed.items():
                allow = (allow_for(module) if allow_for else self.context.allow(module))['sinks']
                for expression, sink, mode, path in self.sink_positions(parsed):
                    scope = parsed.scope_of(expression)
                    if mode == 'value':
                        # (A14.1, A15.4) a value passes unless it may carry text fixed in the source that
                        # is no display text (show() gives such text back unchanged) or a composition of
                        # parts that are no display text
                        ok = self._leaves_ok(expression, parsed, scope, path)
                        if self.judgements is not None:
                            self.judgements.append((parsed.path.name, expression.lineno, ok, ok))
                        if ok:
                            continue
                    elif self.strict_ok(expression, path, parsed, scope, _entry_parts(allow, parsed, expression)):
                        continue
                    cells = self._cells(expression, parsed, scope) if sink in TABLE_SINKS and not path else None
                    if cells is not None:
                        for cell, owner_module, own in cells:
                            owner = self.parsed[owner_module]
                            if self.strict_ok(cell, (), owner, own):
                                continue
                            owner_allow = (allow_for(owner_module) if allow_for
                                           else self.context.allow(owner_module))['sinks']
                            if _allowed_sink(owner_allow, owner, cell) or id(cell) in reported:
                                continue
                            reported.add(id(cell))
                            findings.append(OrderedDict(rule='G5', file=owner.path.name, line=cell.lineno,
                                                        text=ast.unparse(cell), detail=f'{sink} cell',
                                                        function=_qualname(owner, cell)))
                        continue
                    if _allowed_sink(allow, parsed, expression):
                        continue
                    findings.append(OrderedDict(rule='G5', file=parsed.path.name, line=expression.lineno,
                                                text=ast.unparse(expression), detail=sink,
                                                function=_qualname(parsed, expression)))
            grown = False
            for function, param, path in self._accepted_params:
                if (param, path) not in self._def_params[id(function)]:
                    self._def_params[id(function)].add((param, path))
                    self.sink_params[getattr(function, 'name', 'lambda')].add(param)
                    grown = True
            for function, param, path, owner in self._value_params:       # A15.4 value-mode parameters
                if (param, path) not in self._value_def_params[id(function)] and \
                        (isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                         or isinstance(function, ast.Lambda) and self._called_lambda(function, owner)):
                    self._value_def_params[id(function)].add((param, path))
                    self.value_sink_params[getattr(function, 'name', 'lambda')].add(param)
                    grown = True
            self._value_params = []
            if not grown:
                return findings


LIBRARY_KINDS = frozenset({'library filetext t()', 't() mapping', 'sink method', 'value sink method'})


def _library_sinks(context, folder=None, allow=None) -> list:
    """G5 findings of library's filetext `i18n.t` calls (7.9): the same rule as the window's sinks,
    with the fixed points over library's own functions (their returns, their sink parameters and
    what their callers pass, A15.3 F9 / F10); only the t() positions are sinks in library."""
    folder = Path(folder) if folder else context.src
    if not (folder / 'library.py').is_file():
        return []
    allow = context.allow('library')['sinks'] if allow is None else allow
    sinks = _Sinks(context, ['library'], folder=folder, kinds=LIBRARY_KINDS)
    findings = sinks.run(allow_for=lambda name: {'sinks': allow})
    for item in findings:
        if item['detail'].startswith('t('):
            item['detail'] = 'filetext t()'
    return findings


def sinks_scan(src=SRC, modules=None, allow_dir=ALLOW_DIR, context=None, extra_display=()) -> list:
    """G5 findings of the given UI modules (default: every UI module) and of library's filetext calls."""
    context = context or GuardContext(src, allow_dir)
    if modules is None:
        modules = [name for name in UI_MODULES if (Path(src) / f'{name}.py').is_file()]
    findings = _Sinks(context, modules, extra_display=extra_display).run() if modules else []
    if (Path(src) / 'library.py').is_file():
        findings += _library_sinks(context)
    return findings


def _sink_entries_used(context, module, folder=None) -> set:
    """(expression, function) of every `sinks` entry pair that excuses a sink position which is not
    display text (G5 with every UI module, so sink methods resolve across the mixins)."""
    folder = Path(folder) if folder else context.src
    nothing = {'sinks': {}}
    if module == 'library':
        findings = _library_sinks(context, folder, allow={})
    else:
        modules = [name for name in UI_MODULES if (folder / f'{name}.py').is_file()]
        findings = _Sinks(context, modules, folder=folder).run(allow_for=lambda name: nothing)
    return {(item['text'], item['function']) for item in findings if item['file'] == f'{module}.py'}


# --------------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    four = sub.add_parser('g1')
    four.add_argument('--module', action='append')
    five = sub.add_parser('sinks')
    five.add_argument('--module', action='append')
    options = parser.parse_args(argv)
    if options.command == 'g1':
        findings = g1_scan(modules=options.module)
    else:
        findings = sinks_scan(modules=options.module)
    for item in findings:
        print(f'{item["rule"]} {item["file"]}:{item["line"]} {item["text"][:100]!r}  ({item["detail"]})')
    print(f'{len(findings)} findings')
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
