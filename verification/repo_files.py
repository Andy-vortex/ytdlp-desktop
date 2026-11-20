"""The files of the repository and their .gitattributes line-ending rule (2.0.0 S1).

    from repo_files import repository_files, load_attributes, attributes_of

The working copy is not always a git checkout (2.0.0 is built in a copy under build\\), so this
module reads .gitignore and .gitattributes itself instead of calling git.  It understands the
subset of the syntax these two files use: comments, blank lines, ``!`` negation (.gitignore),
a trailing ``/`` (folders only), patterns with a ``/`` anchored at the root, patterns without one
matching a name at any depth, ``*`` / ``?`` / ``[...]`` inside one path segment and ``**`` for
any number of segments; attributes ``text``, ``-text``, ``text=auto``, ``eol=lf`` and the macro
``binary`` (= ``-text -diff -merge``).  Standard library only; no src import.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALWAYS_SKIPPED = ('.git',)


def _segments_match(pattern: list, parts: list) -> bool:
    if not pattern:
        return not parts
    head = pattern[0]
    if head == '**':
        return any(_segments_match(pattern[1:], parts[index:]) for index in range(len(parts) + 1))
    return bool(parts) and fnmatchcase(parts[0], head) and _segments_match(pattern[1:], parts[1:])


def matches(pattern: str, relative: str) -> bool:
    """git's path rule: a pattern with a '/' (other than a trailing one) is anchored at the root and
    matched segment by segment; a pattern without one matches the name at any depth."""
    parts = relative.split('/')
    body = pattern.strip('/') if pattern.startswith('/') else pattern.rstrip('/')
    if '/' not in body:
        return fnmatchcase(parts[-1], body)
    return _segments_match(body.split('/'), parts)


def load_ignore(root=ROOT) -> list:
    """[(pattern, negated, folders_only)] of <root>/.gitignore in file order."""
    rules = []
    path = Path(root) / '.gitignore'
    if not path.is_file():
        return rules
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        negated = line.startswith('!')
        if negated:
            line = line[1:]
        rules.append((line.rstrip('/'), negated, line.endswith('/')))
    return rules


def ignored(relative: str, is_folder: bool, rules) -> bool:
    """The last matching rule decides (a folder's own exclusion is checked by the caller's walk)."""
    result = False
    for pattern, negated, folders_only in rules:
        if folders_only and not is_folder:
            continue
        if matches(pattern, relative):
            result = not negated
    return result


def repository_files(root=ROOT) -> list:
    """Every file below ``root`` that .gitignore does not exclude, as sorted '/'-separated relative paths.

    As in git, nothing below an excluded folder comes back, whatever a later ``!`` rule says."""
    root = Path(root)
    rules = load_ignore(root)
    found = []
    for folder, folders, files in os.walk(root):
        relative = Path(folder).relative_to(root).as_posix()
        prefix = '' if relative == '.' else relative + '/'
        folders[:] = sorted(name for name in folders
                            if name not in ALWAYS_SKIPPED and not ignored(prefix + name, True, rules))
        found += [prefix + name for name in files if not ignored(prefix + name, False, rules)]
    return sorted(found)


def load_attributes(root=ROOT) -> list:
    """[(pattern, {attribute: True | False | value})] of <root>/.gitattributes in file order."""
    rules = []
    path = Path(root) / '.gitattributes'
    if not path.is_file():
        return rules
    for line in path.read_text(encoding='utf-8').splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        values = {}
        for item in fields[1:]:
            if item == 'binary':
                values.update({'text': False, 'diff': False, 'merge': False, 'binary': True})
            elif item.startswith('-'):
                values[item[1:]] = False
            elif '=' in item:
                name, value = item.split('=', 1)
                values[name] = value
            else:
                values[item] = True
        rules.append((fields[0], values))
    return rules


def attributes_of(relative: str, rules) -> dict:
    """The attributes of one file: every matching line in order, a later line overriding an earlier one."""
    result = {}
    for pattern, values in rules:
        if matches(pattern, relative):
            result.update(values)
    return result
