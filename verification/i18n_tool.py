"""Reading the texts of src: the scans the tests share (no src module is imported; only the standard library).

api_calls       the i18n.t / label / choices / all_values and msg calls of src with a literal key or group
                (test_i18n_catalog C8 / C9).
catalog_texts   {'zh', 'en'} of the hand-written catalogs src/i18n_<part>.py (TEXTS), read by AST.
scrub_findings  files that hold this machine's user name, user folders (patterns built at run time from the
                environment) or an e-mail address.
docstring_ids, _fstring_text, CJK
                what test_source_rules S1 (the Chinese literals of src) and test_i18n_catalog share.

(2.0.1) The static guards G1 / G5, their data flow and the per-module allow files are gone: the Chinese
literals of src are checked by test_source_rules.py against verification/golden/text-allow.json.
"""
from __future__ import annotations

import ast
import re
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# The catalog parts: one hand-written src/i18n_<part>.py each (S3-DESIGN.md 1.1), listed from src itself.
MODULES = tuple(sorted(path.stem[len('i18n_'):] for path in SRC.glob('i18n_*.py')))

CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')


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
    """[{file, line, function, kind, name, keywords}] of i18n.t / label / choices / all_values calls (also as
    bare names imported from i18n) and msg / messages.msg calls whose first argument is a constant string (each
    branch of a conditional msg key).  keywords: the keyword argument names of the call, or None when
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
                    and func.value.id == 'messages' and func.attr == 'msg'):
                # (1.8.0) msg(key, ...) names catalog keys too (each branch of a conditional key counts)
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


# --------------------------------------------------------------------------------- scrub patterns

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


# --------------------------------------------------------------------------------- catalog texts, docstrings, msg keys
# (2.0.1) read by test_source_rules and test_i18n_catalog


CATALOG_STEMS = tuple(f'i18n_{name}' for name in MODULES)


def catalog_texts(src=SRC) -> dict:
    """{'zh': {...}, 'en': {...}} of the src catalogs (their TEXTS), read by AST (no import)."""
    merged = {'zh': {}, 'en': {}}
    for stem in CATALOG_STEMS:
        texts = _module_constants(Path(src) / f'{stem}.py', {'TEXTS'}).get('TEXTS', {})
        for key, (zh, en) in texts.items():
            merged['zh'][key] = zh
            merged['en'][key] = en
    return merged


def docstring_ids(tree) -> set:
    """The id() of every docstring node of a parsed module (module, class and function docstrings)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _key_literals(node):
    """The literal keys of a msg() key argument (a str or a conditional of such); None otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _key_literals(node.body), _key_literals(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None
