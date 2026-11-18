"""The frozen reader of keyless stored text (1.8.0, MESSAGES.md 4.6).  Standard library only.

Rows written by 1.5.1, 1.6.0 and 1.7.0 (and by 1.8.0 before a sidecar describes them) hold app
texts without their keys.  lift() recognises such a text by the frozen tables of
i18n_legacy_data.py (generated from the released 1.7.0 tables, never extended for new texts) and
returns its key and parameters.  Only messages.py imports this module (G1d).

    lift(field_kind, text, context=None) -> (key, params) | None
        field kinds: 'error', 'reason', 'clauses', 'job_title', 'title', 'context_title', 'name'
        context: job_title {'kind', 'titled', 'links'}; reason {'error': the sibling error text}
        params: str captures, Lifted (a nested app text), Enum (a stored enum value), and for
        'common.clauses' {'parts': [Lifted | str, ...]}
    recognise(text, display=False) -> key | None
        (tests only, G2 of MESSAGES.md 7.3) whether a plain text is recognisable app text.

The rules L1-L9 of MESSAGES.md 4.6.  Nothing here raises for odd input; lift() returns None.
"""
from __future__ import annotations

import collections
import functools
import re
import string
import threading

import i18n_legacy_data as DATA

JOIN = '；'                                   # the stored joiner of clauses (L5)
MAX_DEPTH = 3
CUTS = {'error': (200, 900), 'reason': (70,)}  # stored cut lengths per field kind (L3)
RECOGNISED_CUTS = (70, 200, 300, 900)          # G2 rule (c)
MIN_CUT_LITERAL = 8
CJK_RUN = 6
CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')
STRIPPED_PREFIXES = ('ERROR: ', 'WARNING: ')
# engine.explain_error's 1.7.0 fallback (engine.py:97, 112), frozen: the R4 pairing of L4.
EXTRACTOR_PREFIX = re.compile(r'^\[(?!(?:WinError|Errno)\b)[A-Za-z][^\]]*\]\s+\S+:\s+')
FIELD_KINDS = ('error', 'reason', 'clauses', 'job_title', 'title', 'context_title', 'name')
# Key ranges whose live zh texts are app texts a core module produces or stores (G2 R-text).
RECOGNISED_PREFIXES = ('core.', 'value.', 'download.reason.')

_FORMATTER = string.Formatter()
_lock = threading.RLock()

Lifted = collections.namedtuple('Lifted', 'key params text')      # a nested app text
Enum = collections.namedtuple('Enum', 'group value')               # a stored enum value (mode)


def _fields(template):
    return [field for _literal, field, _spec, _conv in _FORMATTER.parse(template) if field is not None]


def _parts(template):
    """[(literal, field or None)] of a template (doubled braces undone in the literals)."""
    return [(literal, field) for literal, field, _spec, _conv in _FORMATTER.parse(template)]


class _Template:
    """One frozen template compiled: the 1.7.0 i18n._Template, returning (key, params)."""

    __slots__ = ('key', 'template', 'parts', 'regex', 'names', 'literal', 'first', 'numeric', '_prefix')

    def __init__(self, key, template, numeric):
        self.key = key
        self.template = template
        self.parts = _parts(template)
        self.numeric = numeric
        pattern, names, literal = [], [], 0
        for index, (text, field) in enumerate(self.parts):
            pattern.append(re.escape(text))
            literal += len(text)
            if field is None:
                continue
            pattern.append(self._field_pattern(field, names, last=index == len(self.parts) - 1))
            if field not in names:
                names.append(field)
        self.regex = re.compile(''.join(pattern) + r'\Z', re.S)
        self.names = tuple(names)
        self.literal = literal
        self.first = self.parts[0][0][:1] if self.parts and self.parts[0][0] else ''
        self._prefix = {}

    def _field_pattern(self, field, seen, last):
        if field in seen:
            return f'(?P={field})'
        if field in self.numeric:
            return f'(?P<{field}>' + {'code': r'-?\d+', 'size': r'\d+(?:\.\d+)?'}.get(field, r'\d+') + ')'
        return f'(?P<{field}>.+)' if last else f'(?P<{field}>.+?)'

    def prefix_patterns(self):
        """[(regex, literal count before the tail, partial literal or None, partial field or None)]:
        one pattern per place a cut can fall (L3), compiled once."""
        cached = self._prefix.get('all')
        if cached is not None:
            return cached
        found = []
        literals = [text for text, _field in self.parts]
        for k, (text, field) in enumerate(self.parts):
            head, names = [], []
            for index in range(k):
                literal_i, field_i = self.parts[index]
                head.append(re.escape(literal_i))
                if field_i is not None:
                    head.append(self._field_pattern(field_i, names, last=False))
                    if field_i not in names:
                        names.append(field_i)
            before = sum(len(literals[i]) for i in range(k))
            if text:                    # the cut falls inside literal k (at least one character of it)
                tail = '|'.join(re.escape(text[:j]) for j in range(len(text), 0, -1))
                found.append((re.compile(''.join(head) + f'(?P<_tail>{tail})' + r'\Z', re.S), before, text, None))
            if field is not None and field not in names:   # ... or inside the capture after it
                regex = ''.join(head) + re.escape(text) + f'(?P<{field}>.+)' + r'\Z'
                found.append((re.compile(regex, re.S), before + len(text), None, field))
        self._prefix['all'] = found
        return found


class _Index:
    """Exact texts and templates by first character (the 1.7.0 i18n._Index)."""

    def __init__(self, templates, numeric):
        self.exact = {}
        self.buckets = {}
        self.items = []
        shared = []
        for key, template in templates:
            if not _fields(template):
                text = template.replace('{{', '{').replace('}}', '}')
                self.exact.setdefault(text, []).append(key)
                continue
            item = _Template(key, template, numeric)
            self.items.append(item)
            if not item.first:
                shared.append(item)
        order = lambda item: (-item.literal, item.key)     # noqa: E731
        shared.sort(key=order)
        for item in self.items:
            if item.first:
                self.buckets.setdefault(item.first, []).append(item)
        for first, items in self.buckets.items():
            self.buckets[first] = sorted(items + shared, key=order)
        self.shared = shared
        self.items.sort(key=order)

    def candidates(self, text):
        return self.buckets.get(text[:1], self.shared)


def _allowed(key, keys) -> bool:
    return keys is None or key in keys or key.startswith(tuple(k for k in keys if k.endswith('.')))


class Reader:
    """The lift rules over one set of frozen tables (tests build variants for the G3 self-test)."""

    def __init__(self, templates=DATA.TEMPLATES, numeric=DATA.NUMERIC, recurse=DATA.RECURSE,
                 title_keys=DATA.TITLE_KEYS, name_keys=DATA.NAME_KEYS, job_title=DATA.JOB_TITLE):
        self.templates = tuple(tuple(item) for item in templates)
        self.numeric = tuple(numeric)
        self.recurse = dict(recurse)
        self.title_keys = tuple(title_keys)
        self.name_keys = tuple(name_keys)
        self.job_title = {kind: tuple(keys) for kind, keys in job_title.items()}
        self.index = _Index(self.templates, self.numeric)
        self.keys = frozenset(key for key, _template in self.templates)
        self.lift = functools.lru_cache(maxsize=4096)(self._lift_cached)

    # ------------------------------------------------------------------ public

    def _lift_cached(self, kind, text, context):
        try:
            return self._lift(kind, text, context, 0, False)
        except Exception:                    # a broken template must never break a read
            return None

    def job_keys(self, context):
        """The job-title keys a stored title may be, by the CR2-2 context (L6); None: all three."""
        if context is None:
            return tuple(key for keys in self.job_title.values() for key in keys)
        kind, titled, links = context
        if kind == 'collect':
            return self.job_title['collect']
        if titled:
            return self.job_title['titled'] if links != 1 else ()
        return self.job_title['untitled']

    # ------------------------------------------------------------------ rules

    def _lift(self, kind, text, context, depth, cut):
        if not isinstance(text, str) or not text:
            return None
        if kind in ('title', 'context_title'):
            return self._exact(text, self.title_keys)                       # L7
        if kind == 'name':
            return self._exact(text, self.name_keys)                        # L8
        if kind == 'job_title':
            keys = self.job_keys(context)                                   # L6
            return self._match(text, keys, depth) if keys else None
        if kind == 'reason' and context is not None:                        # L4 (R4 pairing)
            error = context
            if text == EXTRACTOR_PREFIX.sub('', error.strip(), count=1)[:70]:
                found = self._lift('error', error, None, depth, False)
                if found is not None:
                    return found
        if kind == 'clauses':
            return self._clauses(text, depth)                               # L5
        if kind not in ('error', 'reason'):
            return None
        cut = cut or len(text) in CUTS[kind]
        found = self._match(text, None, depth, cut)                         # L1 / L2 (L9: every key)
        if found is None and cut:
            found = self._prefix(text, None, depth)                         # L3
        return found

    def _exact(self, text, keys):
        for key in self.index.exact.get(text, ()):
            if _allowed(key, keys):
                return key, {}
        return None

    def _match(self, text, keys, depth, cut=False):
        """L1 / L2.  ``cut``: the text may be a stored cut, so a capture that ends the template may
        itself be cut (its own lift then also tries L3)."""
        found = self._exact(text, keys)
        if found is not None:
            return found
        for item in self.index.candidates(text):
            if not _allowed(item.key, keys):
                continue
            match = item.regex.match(text)
            if match is None:
                continue
            last = item.parts[-1][1] if cut and item.parts else None
            return item.key, self._captures(match.groupdict(), depth, cut_field=last)
        return None

    def _captures(self, values, depth, cut_field):
        params = {}
        for name, value in values.items():
            params[name] = self._capture(name, value, depth, cut=name == cut_field)
        return params

    def _capture(self, name, value, depth, cut):
        if not value or name not in self.recurse or depth >= MAX_DEPTH:
            return value
        if name == 'mode':
            return Enum('mode', value)
        if name == 'name':
            found = self._exact(value, self.name_keys)
        elif name in ('reason', 'error'):
            found = self._match(value, None, depth + 1)
            if found is None and cut:
                found = self._prefix(value, None, depth + 1)
        else:
            found = self._match(value, self.recurse[name], depth + 1)
        if found is None:
            return value
        return Lifted(found[0], found[1], value)

    def _prefix(self, text, keys, depth):
        """L3: ``text`` is the start of an instance of a template, cut inside a literal or a capture."""
        best = None
        for item in self.index.items:
            if not _allowed(item.key, keys):
                continue
            first = item.parts[0][0] if item.parts else ''
            if not text.startswith(first):
                continue
            for regex, before, literal, field in item.prefix_patterns():
                match = regex.match(text)
                if match is None:
                    continue
                values = match.groupdict()
                count = before + (len(values.pop('_tail')) if literal is not None else 0)
                if count < MIN_CUT_LITERAL or (best is not None and count <= best[0]):
                    continue
                if literal is not None and before == 0 and len(text) < len(first):
                    continue
                for name in item.names:
                    values.setdefault(name, '')
                best = (count, item.key, values, field)
        if best is None:
            return None
        _count, key, values, field = best
        return key, self._captures(values, depth, cut_field=field)

    def _clauses(self, text, depth):
        """L5: the whole text first, then the grouping of contiguous '；' parts with the fewest
        unlifted parts, then the fewest groups; a common.clauses message when at least one group
        lifted and there are two or more groups.

        Reading of "the whole text first" (WP-1 fix round 1): the whole text is one candidate
        grouping (a single group), taken when its lift keeps every '；' in a template literal
        (value.subscription.interrupted, needs_chrome, core.engine.waiting).  A lift whose plain
        capture swallows a '；' joiner (a leading or trailing capture holding the next clause) counts
        each swallowed joiner as an unlifted part, and ties go to the grouping that swallows fewer,
        so '资料库暂时无法使用：x；页面超过 8 MB…' is two clauses, not one message with a Chinese capture."""
        parts = text.split(JOIN)
        if len(parts) < 2 or len(parts) > 24:
            return self._match(text, None, depth)
        last = len(parts)
        best = [None] * (last + 1)
        best[0] = (0, 0, 0, [])
        for end in range(1, last + 1):
            for start in range(end):
                if best[start] is None:
                    continue
                group = JOIN.join(parts[start:end])
                whole = start == 0 and end == last
                level = depth if whole else depth + 1
                lifted = self._match(group, None, level) if group else None
                if lifted is None and group and not whole and len(group) in CUTS['error']:
                    lifted = self._prefix(group, None, level)
                unlifted, swallowed, groups, items = best[start]
                if lifted is not None:
                    piece = Lifted(lifted[0], lifted[1], group)
                    held = _swallowed(lifted[1])
                else:
                    piece = group
                    held = 0
                # unlifted counts the '；' parts left unlifted, so one app text next to a raw text
                # is lifted rather than kept whole with it
                candidate = (unlifted + (0 if lifted else end - start), swallowed + held, groups + 1,
                             items + [piece])
                if best[end] is None or _rank(candidate) < _rank(best[end]):
                    best[end] = candidate
        _unlifted, _swallowed_count, groups, items = best[last]
        if groups == 1:
            item = items[0]
            return (item.key, item.params) if isinstance(item, Lifted) else None
        if all(not isinstance(item, Lifted) for item in items):
            return None
        return 'common.clauses', {'parts': items}


def _rank(candidate):
    """L5 order of a grouping (unlifted parts, swallowed joiners, groups, items): unlifted parts
    and swallowed joiners together first, then fewer swallowed joiners, then fewer groups."""
    unlifted, swallowed, groups, _items = candidate
    return unlifted + swallowed, swallowed, groups


def _swallowed(params) -> int:
    """The '；' joiners inside the plain (unlifted) captures of a lift, nested lifts included."""
    count = 0
    for value in params.values():
        if isinstance(value, Lifted):
            count += _swallowed(value.params)
        elif isinstance(value, str):
            count += value.count(JOIN)
    return count


def _context_key(kind, context):
    """A hashable form of a lift context (the lru_cache key)."""
    if context is None:
        return None
    if kind == 'job_title':
        if not isinstance(context, dict):
            return None
        try:
            links = int(context.get('links') or 0)
        except (TypeError, ValueError):
            links = 0
        return (context.get('kind'), bool(context.get('titled')), links)
    if kind == 'reason':
        error = context.get('error') if isinstance(context, dict) else context
        return error if isinstance(error, str) and error else None
    return None


_default = None


def reader() -> Reader:
    global _default
    if _default is None:
        with _lock:
            if _default is None:
                _default = Reader()
    return _default


def lift(field_kind, text, context=None):
    """(key, params) of a keyless stored text an old version (or 1.8.0 without a sidecar) wrote,
    or None.  ``text`` is the stored text; the caller keeps it as the value."""
    if field_kind not in FIELD_KINDS or not isinstance(text, str) or not text:
        return None
    text = str.__str__(text)
    return reader().lift(field_kind, text, _context_key(field_kind, context))


# ---------------------------------------------------------------------------- recognise (tests only)

class _Recogniser:
    """G2's R-text (MESSAGES.md 7.3): the frozen table, the live zh texts of core.* / value.* /
    download.reason.* keys and every CJK GROUPS value; the UI-text index for the display exclusion;
    in English also the UI texts themselves (A15.5)."""

    def __init__(self):
        import i18n                         # lazily: the program's reads never need it
        import i18n_maps
        zh = i18n.CATALOG['zh']
        templates = list(DATA.TEMPLATES)
        templates += [(key, text) for key, text in sorted(zh.items()) if key.startswith(RECOGNISED_PREFIXES)]
        self.groups = {}
        for group, mapping in i18n_maps.GROUPS.items():
            for value, key in mapping.items():
                if isinstance(value, str) and CJK.search(value):
                    self.groups.setdefault(value, key or f'group:{group}')
        self.index = _Index(templates, DATA.NUMERIC)
        self.reader = Reader(templates=templates)
        self.runs = {}
        for key, template in templates:
            for literal, _field in _parts(template):
                self._add_runs(literal, key)
        for value, key in self.groups.items():
            self._add_runs(value, key)
        ui = [(key, text) for key, text in sorted(zh.items()) if not key.startswith(RECOGNISED_PREFIXES)
              and len(CJK.findall(''.join(literal for literal, _f in _parts(text)))) >= 4]
        self.ui = _Index(ui, DATA.NUMERIC)
        self._ui_texts = ui
        self._english = None            # (index, runs): _english_index(), on the first English check

    def _english_index(self):
        """(1.8.0 converge round, MESSAGES.md 15 A15.5) In English every zh catalog text is out of place
        on the screen, a UI text too: the English index holds the UI texts the display exclusion
        leaves out, without the deliberate ones (a key whose English text is its zh text, such as a
        language name) and without the CJK runs that the English catalog itself contains.  Built on
        the first English check, so a process that checks only zh texts never pays for it."""
        found = self._english
        if found is None:
            import i18n
            en = i18n.CATALOG['en']
            english = [(key, text) for key, text in self._ui_texts if en.get(key) != text]
            pattern = re.compile(CJK.pattern + '{%d,}' % CJK_RUN)
            known = {run[start:start + CJK_RUN] for text in en.values() for run in pattern.findall(str(text))
                     for start in range(len(run) - CJK_RUN + 1)}
            runs = {}
            for key, template in english:
                for literal, _field in _parts(template):
                    for run in pattern.findall(literal):
                        for start in range(len(run) - CJK_RUN + 1):
                            piece = run[start:start + CJK_RUN]
                            if piece not in known:
                                runs.setdefault(piece, key)
            found = self._english = (_Index(english, DATA.NUMERIC), runs)
        return found

    def _add_runs(self, text, key):
        for run in re.findall(CJK.pattern + '{%d,}' % CJK_RUN, text):
            for start in range(len(run) - CJK_RUN + 1):
                self.runs.setdefault(run[start:start + CJK_RUN], key)

    def _whole(self, index, text):
        for key in index.exact.get(text, ()):
            return key
        for item in index.candidates(text):
            if item.regex.match(text):
                return item.key
        return None

    def recognise(self, text, display, english=False):
        if english:
            found = self.recognise(text, False)
            if found:
                return found
            index, runs = self._english_index()
            found = self._whole(index, text)
            if found:
                return found
            for run in re.findall(CJK.pattern + '{%d,}' % CJK_RUN, text):
                for start in range(len(run) - CJK_RUN + 1):
                    key = runs.get(run[start:start + CJK_RUN])
                    if key:
                        return key
            return None
        if text in self.groups:
            return self.groups[text]
        if display and self._whole(self.ui, text):
            return None
        found = self._whole(self.index, text)                                # (a)
        if found:
            return found
        for prefix in STRIPPED_PREFIXES:                                     # (b)
            if text.startswith(prefix):
                stripped = text[len(prefix):].strip()
                found = self.groups.get(stripped) or self._whole(self.index, stripped)
                if found:
                    return found
        if len(text) in RECOGNISED_CUTS:                                     # (c)
            found = self.reader._prefix(text, None, MAX_DEPTH)
            if found:
                return found[0]
        for run in re.findall(CJK.pattern + '{%d,}' % CJK_RUN, text):          # (d)
            for start in range(len(run) - CJK_RUN + 1):
                key = self.runs.get(run[start:start + CJK_RUN])
                if key:
                    return key
        return None


_recogniser = None


@functools.lru_cache(maxsize=8192)
def _recognised(text, display, english=False):
    global _recogniser
    if _recogniser is None:
        with _lock:
            if _recogniser is None:
                _recogniser = _Recogniser()
    return _recogniser.recognise(text, display, english)


def recognise(text, display=False, english=False):
    """The key (or 'group:<name>') of a recognisable app text, else None.  Used only by the G2
    hooks of messages.py in guarded test processes; never by encode / decode.  ``english``: the
    text is shown in English, where the zh text of a UI key is out of place too (A15.5)."""
    if not isinstance(text, str) or not text or not CJK.search(text):
        return None
    try:
        return _recognised(str.__str__(text), bool(display), bool(english))
    except Exception:
        return None
