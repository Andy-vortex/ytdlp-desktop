<!-- Published copy of the 1.8.0 design spec (with its section 15, the amendments made during the
     implementation).  Local paths are written as ROOT (the source folder) and "the install folder". -->

# YT-DLP下载器 1.8.0 — internal message keys (normative spec)

Status: normative specification for the implementation teams. "Must" is binding; "should" is the default that a
work package may change with a reason in its handoff note. Anything this spec does not change stays exactly as in
1.7.0. Revised once after the completeness review (section 14 lists what changed).

Read with: the two maps in this folder (`map-download.md`, flows **D-F01…D-F55**, hard cases **D-H-1…D-H-11**;
`map-collect-library.md`, flows **C-F01…C-F52**, hard cases **C-H-1…C-H-18**) and the code. The three candidate
designs and the judges' reviews are background only; where they differ from this spec, this spec wins.

Line numbers are those of the 1.7.0 integration tree as read on 2026-11-17. The 1.7.0 release commit `07c9603`
("YT-DLP 下载器 1.7.0", on `main` after `728bcf2` = 1.6.0) landed the same day; its `src/*.py` and
`verification/*.py` are byte-identical to that tree (re-checked file by file during the revision), so the line
numbers hold. WP-0 confirms this again; functions and flows are normative, line numbers are not.

Names used below: **ROOT** = the source folder (the repository root); **PY** = `ROOT\.venv\Scripts\python.exe -X utf8 -B`;
**1.5.1 / 1.6.0 / 1.7.0** = the released sources frozen under `verification/release-sources/<version>/src`
(created by WP-1, section 10.3); "en" / "zh" = the running interface language.

---------------------------------------------------------------------------------------------------

## 0. Decisions in one page

| # | Decision |
|---|---|
| D1 | **Version 1.8.0.** No stored field changes its type or bytes; the only addition is an additive JSON key `msgs` that every old reader ignores and every old writer keeps (or drops together with the text it describes). Rollback to 1.7.0, 1.6.0 and 1.5.1 keeps working; `install_release.py` is unchanged. No migration runs; the upgrade writes nothing. |
| D2 | Base design: **compat-first**, with the grafts listed in D14 and the corrections in D15. |
| D3 | A core text is a **`Message`**: a `str` subclass whose string value is byte for byte the 1.7.0 Chinese text and which carries `key`, `params` and `origin`. Two more `str` subclasses: **`Raw`** (text the producer knows is user data or engine output; never translated, never reverse-read) and **`Value`** (a stored enum value nested in a message, shown through `label()`). For all three, `str(x)` and `f'{x}'` return the object itself (2.3), so `str(exception)` keeps the key and `str(raw)` keeps the type. A plain `str` means "provenance unknown". |
| D4 | Every producer in `src` emits / raises / stores `msg(key, **params)` instead of a Chinese literal or f-string. Values are identical, so every golden, comparison, old reader, `str(exception)` and download.log line is unchanged. |
| D5 | Storage keeps the 1.7.0 text in every old field. Beside it, in the same JSON object, `msgs` maps **the stored text itself** to `{"k": key, "p": params}` for a message, or to `null` for a `Raw` text that the legacy reader would otherwise translate. An entry applies only to a field whose text is exactly its map key, so any change of the text orphans it; it can be wrong only when a version that does not know sidecars writes the same text with another origin while the entry stays (L-13, en display only). Messages lifted from keyless legacy text are never persisted (origin rule). Entries 1.8.0 does not understand (written by a later version) are carried through every 1.8.0 write unchanged (4.4 step 3), so 1.8.0 is a safe rollback target for later versions. |
| D6 | One read boundary: `messages.decode()` / `decode_columns()` / `encode()`, called only by `library.py` and `unfinished.py`; read-modify-write paths load the raw stored JSON and never transform `options` (3.4). Keyless text (rows of 1.5.1 / 1.6.0 / 1.7.0, before the upgrade or after a rollback) is lifted by the frozen reader `src/i18n_legacy.py` (+ generated data `src/i18n_legacy_data.py`), generated once from the released 1.7.0 tables and never extended for new texts. |
| D7 | The UI shows values only through `messages.show(value)` and `messages.error_text(error)`. In zh `show()` returns the value's own text (= what 1.7.0 showed). All **52** `localize()` call lines and all **8** key-prefix lists leave `src`; the 1.7.0 reverse machinery leaves `i18n.py` / `i18n_maps.py`. |
| D8 | Guards (section 7) make the defect class fail a test: G1 static producer scan with per-module allow files, the slot rule (following local, module and cross-module enum bindings) and the composed-text rule; G2 runtime sentinel over the whole suite at every boundary where a text changes hands — core emits, store writes, `show()` / `error_text()` and `i18n.t()` parameters — recognising every app text including every `GROUPS` value, plus strict-mode tripwires attributed past the pass-through modules (reported through files the core cannot swallow); G3 legacy closure from the frozen old sources; G4 channel × key matrix and consumer census; G4b English replay of the frozen old producers with the G6 zh dump; G5 a positive static rule that every UI sink argument is provably display text; and an AST guard of the protected blocks. Each guard has a self-test that shows it catches `媒体直链`, `动态页面媒体请求` and the 1.5.1 collector forms on the old sources. |
| D9 | **H3 relaxed at exactly three expression nodes** (S1–S3, section 9) inside the protected blocks, plus four nodes of the SPEC-verbatim 429 loop (S4–S7, not H3). Replacement check: an AST comparison with `release-sources/1.7.0` where only the sanctioned nodes are masked. **H5 kept**: payload keys unchanged, values are the same text (now possibly a `str` subclass). |
| D10 | zh is byte-identical to 1.7.0: goldens, `test_golden_*.py`, `_sandbox.normalise`, the `set_busy` / `poll` hashes and every zh catalog text of a 1.7.0 key are unchanged and pinned by tests. |
| D11 | The frozen old sources live under the tracked folder `verification/release-sources/{1.5.1,1.6.0,1.7.0}/src`, pinned by SHA-256 in `verification/golden/release-sources.json`. The six existing tests that read the untracked 1.5.1 backup are pointed there, so a public clone runs the whole suite. |
| D12 | Cuts (70, 200, 300, 900 characters) apply to the stored / zh value only; en shows the whole message. |
| D13 | The English-only core texts (D-H-3) get keys whose **zh text is today's English** (zh parity). Translating them is a later owner decision. |
| D14 | Grafts taken: text-keyed sidecar, `columns` pruning, 2 KB cap, R4 cut-reason repair, `__str__`/`__format__` returning self (for `Message`, `Raw` and `Value`), copy/pickle support, explicit allow-list without a GROUPS allowance, verify-gate evidence and rehearsal (minimal-change); raw markers (as an explicit `Raw` type), G1b slot rule, English replay + zh dump, AST protected-block guard, consumer census, deny-list default for error-like fields (clean-model); strict-mode tripwires (goal judge). |
| D15 | Not taken: a one-time migration (risk without correctness gain), H5 relaxation and a non-`str` message type (test churn, zh risk), digest keys (`sha1` raises on lone surrogates), renaming `value.link_kind.direct` / `dynamic_request` and removing them from `GROUPS['link_kind']` (the Type column width is computed from that group: zh layout would change), key-tag log classification (zh parity risk), sidecars for legacy guesses. Corrected from compat-first: the `_run_once` waiting reason is sanctioned node S1 instead of a "plain reason and no kind" UI rule, because `test_golden_ui.py:193` feeds a plain rate-limit reason without `kind`, which that rule would mis-render in en. |
| D16 | Publication: after `run_tests.py --all`, the packaged build and gate, the zh/en smoke and the install/rollback rehearsal pass, the 1.8.0 commit is pushed to the public repo `ytdlp-desktop` of the GitHub account with id **262798495** (checked with `gh api user` before anything is created or pushed, 11.7), with the decided exclusions; README and commit message state the advantages and why this is 1.8.0 and not 2.0 (11.8). Installing 1.8.0 on the owner's machine is not part of this work and is not approved. |
| D17 | Scope ("只做这件事"): only the message-key change and what it needs. The three 1.7.0 minors carried in the project notes are **not** fixed here: the English YouTube-check task title cut at 1000x740, `snapshot_ui` check (9) not knowing `fit_alternatives`, and the missing space at `ui_download.py:346`. They stay as they are; the en snapshot gate of WP-6 compares the (9)–(12) results with the 1.7.0 baseline and lists the results these minors cause as known, so they neither block nor hide a new failure (10.3, 11.4). |

---------------------------------------------------------------------------------------------------

## 1. Version decision

**1.8.0.** Every field that 1.5.1, 1.6.0 or 1.7.0 reads, parses, compares or concatenates keeps its type and its
exact 1.7.0 text (`jobs.title`, `jobs.data.errors[*]`, `last_reason`, `subscriptions.name`, `last_error`, page / link
/ entry / archive titles, link `kind`, `mode`, `resolution`, the failed-list legacy view and `entries[].error`). The
additions are JSON keys that no old version uses (`msgs`; grep of all three source trees: no hit) in objects that
every old version reads with `json.loads` and either writes back whole or replaces whole. No sqlite column (every
version inserts positionally, C-H-2), no table, no `user_version`, no settings key, no failed-list top-level key.

Why not 2.0.0: a format break (messages inside the old fields) was tested by the clean-model designer: 1.5.1,
1.6.0 and 1.7.0 history / subscription pages crash with `TypeError`, and 1.6.0 / 1.7.0 erase stored errors on their
next save. It would also need installer-managed data backups that lose everything since the upgrade on rollback,
and it does not protect an owner who double-clicks an old backup exe. The legacy reader is needed either way
(the owner's existing rows), so a break buys nothing. 2.0.0 is reserved for the day rollback to ≤ 1.7.0 is given
up; then `i18n_legacy` can be retired after a deliberate pass.

The owner's own words placed this change at "2.0" (比如做 2.0 时再改), on the assumption that old versions could not
read the new format. The design removes that assumption, and the project's rule is "1.8.0 if old versions can still
read the data, 2.0.0 if not". The README and the commit message must say this in plain words (11.8 point 7), so the
owner sees why the number is 1.8.0.

---------------------------------------------------------------------------------------------------

## 2. Message model (`src/messages.py`, new, standard library + `i18n` only)

### 2.1 Value types

| type | what it is | value (`str`) | rendered in en | rendered in zh |
|---|---|---|---|---|
| `Message(str)` | an app text: `key` (catalog key, the structural `common.clauses`, or `None` for a `foreign` message nothing lifts), `params` (read-only mapping), `origin` ∈ `live` / `stored` / `legacy` / `foreign`; a `foreign` message also holds `entry`, the sidecar entry it was decoded from, as compact JSON text (4.4 step 3) | the 1.7.0 zh text (live: rendered from the zh catalog; decoded: the stored text, possibly a 1.5.1 form or a cut) | `i18n.t(key, **{name: show(p)})`; `common.clauses` → `i18n._join_sentences(lang, [show(p) ...])`; key `None` → the value | the value itself |
| `Value(str)` | a stored enum value inside a message (`mode` of the placeholder title); attribute `group` | the stored value | `i18n.label(group, value)` | the value itself |
| `Raw(str)` | text the producer positively knows is not app text: media / page / link titles from the source, host names, user-typed names, first start link, raw engine `ERROR:` text | the text | unchanged (token rule only) | unchanged |
| plain `str` | provenance unknown: OS / sqlite / third-party exception text, legacy keyless text, test fixtures | the text | unchanged (token rule only) | unchanged |
| numbers | `int` / `float` params; a producer that formatted with a spec passes the formatted string (`size=f'{mb:.1f}'`, `timeout=f'{t:g}'`, `mode=repr(mode)`) | — | `str()` | `str()` |

The three subclasses share the carrier rules of 2.3: `__slots__`, immutable, `__str__` and `__format__('')` return
the object, `__copy__` / `__deepcopy__` return it, `__reduce__` rebuilds it with its attributes.

Token rule: in en, the app's proxy mask token `PROXY_TOKEN = '[代理]'` inside a `Raw` / plain text (or a raw param)
is shown as `t('core.proxy_mask')`; this is the 1.7.0 rule (i18n.py:443-444) applied to raw text, not a
translation.

Plurals: none (I18N.md D8). Catalog texts keep their placeholders without format specs (test_i18n_catalog test 2).

### 2.2 API (exact names)

```text
msg(key, **params) -> Message                 live message; value = zh catalog text formatted with the zh shown params
value(group, stored) -> Value | stored        Value when `stored` is a str, else the object unchanged
raw(text) -> Raw                               '' stays '' (an empty text never gets a sidecar)
clauses(parts) -> Message | str | ''           [] -> ''; [one] -> that part; else Message('common.clauses', parts=[...]),
                                               value = '；'.join(zh parts)
cut(value, n) -> same type                     Message: value[:n] with key / params / origin kept; Raw -> Raw(text[:n]);
                                               plain -> text[:n]
masked(value, pairs) -> same type              pairs = [(secret, replacement), ...]; the value's text gets every
                                               str.replace in order (exactly the 1.7.0 operation, so the bytes match);
                                               raw (str / Raw) params are masked recursively; key kept
stripped(value) -> same type                   Message / Value returned as is; others .strip() with the type kept
message_of(error) -> Message | str             error.args[0] when it is the only arg and a Message, else str(error)
show(value) -> str                             plain str in the running language; None -> ''; never raises
                                               (on an internal error: the zh text, one WARNING per process)
error_text(error) -> str                       show(message_of(error))
zh(value) -> str                               the plain zh text (str.__str__ for subclasses)
template(key) -> str                           the zh catalog template (plain), for H4 template constants (updater MSG_*)
decode(record, kind) -> dict                   section 4.4: a shallow copy with the text fields of `kind` decoded and
                                               `msgs` removed; every other value is the same object (never `options`)
decode_columns(record, kind, columns) -> dict  the column texts (`{'title': ..., 'kind': ...}` of a job, `{'name': ...}`
                                               of a subscription) decoded with the `msgs` of the undecoded `record`
encode(record, kind, columns=None) -> dict     section 4.5; never raises
wire(value) / unwire(obj)                      section 2.8
guarded(emit, producer) -> emit                G2 hook (section 7.3); returns `emit` itself when the guard is off
exempt(reason) -> context manager              G2 / tripwire exemption for test fixtures (section 7.3)
PROXY_TOKEN = '[代理]'                          the single definition of the token in src
```

When `YTDLP_DESKTOP_MSG_GUARD` is set, importing `messages` also installs the G2 hooks of 7.3 (the tripwires and the
checked `i18n.t` wrapper); `verification/_sandbox.py` imports `messages` in such processes, so every guarded test
process has them. Without the variable nothing is installed.

`msg()` never raises: a missing key or placeholder behaves like `i18n.t()` (warning once, key / `{name}` kept). G1c
makes both impossible in committed code.

### 2.3 Behaviour of the carrier (verified on the project's Python 3.11)

| operation | result |
|---|---|
| queue put / get, dict and tuple payloads, `==`, `hash`, `in`, dict key, `re.search`, `startswith`, `len`, iteration, `encode` | unchanged `str` behaviour; key kept on the object |
| `json.dumps` (indent or not), sqlite parameter, Tcl / Tk conversion, `logging` `'%s'` | the plain zh text is written |
| `str(m)`, `f'{m}'`, `'%s' % m`, `str(ValueError(m))`, `str(OSError(m))`, `str(UpdateError(m))` | the Message itself (`__str__` returns self; `__format__('')` returns self, a non-empty spec formats the plain text) |
| the same for a `Raw` or a `Value` (`str(raw)`, `f'{value}'`, `str(errors[-1])` in `history_note_text`) | the object itself. Without these overrides CPython returns a plain copy for a `str` subclass (`str.__str__` and `str.__format__('')` copy; checked on the project's Python: a bare subclass comes back as `str` from `str()`, `f''`, `'%s' %` and `str(ValueError(x))`), which would turn a `Raw` into "provenance unknown" and a nested `Value` into an untranslated zh mode |
| `copy.copy`, `copy.deepcopy`, `dataclasses.asdict` | the same object (`__copy__` / `__deepcopy__` return self; immutable) |
| `pickle` | rebuilt with key, params, origin (and `entry`), with `group` for a `Value` (`__reduce__`) |
| Python-level methods on a `Message` or a `Value`: slice, `strip`, `+`, `%` with other text, `replace`, `split`, `format`, case changes, … (full list 2.9) | **plain `str`, key lost.** In a guarded test process these calls are tripwires when the calling code is a guarded `src` module (2.9): recorded and raised at the site |
| operations that never call a method of the object: `f'x{m}'` (the f-string concatenates in C after `__format__`), `'；'.join([...m...])`, `re.sub(..., m)`, `textwrap.*`, `'{}…'.format(m)`, `json.loads`, another process | **plain `str`, key lost, no tripwire possible.** The result is caught where it lands (7.3: core emit, store write, `show()` / `error_text()`, `i18n.t()` parameter) when it is recognisable app text, and G1 forbids composing text in the text slots of core modules (7.2 G1f) |

These two last rows are exactly the 1.7.0 defect sites (D-H-8); section 3.5 replaces every known one. What no guard
can catch is a composition of a *short* app text (under six CJK characters, not a whole text) with other text on a
path no test runs (L-1).

`Message` is immutable: `__slots__ = ('key', '_params', 'origin', 'entry')`, `params` is a `types.MappingProxyType`.
`Value`: `__slots__ = ('group',)`; `Raw`: `__slots__ = ()`. `__eq__` / `__hash__` are `str`'s: a message equals its
plain text (as 1.7.0 compared texts). `Raw` has no tripwires: a flattened `Raw` renders exactly like the plain text it
becomes.

### 2.4 Nesting and params

Params are numbers, plain / `Raw` text, nested `Message`s, `Value`s, or (only for `common.clauses`) a list of parts.
An "error" or "reason" param holds whatever the producer has: `message_of(error)` gives the exception's `Message`
or its raw text (OS strerror in the Windows language, sqlite, curl / Scrapling / Playwright English: D-H-5, C-H-6).
Raw params are never translated.

### 2.5 Exceptions

The core raises its existing exception classes with a message: `ValueError(msg(...))`, `OSError(msg(...))`,
`RuntimeError(msg(...))`, `LinkTooLong(msg(...))`, `UpdateError(msg(...))`. `str(error)` is the 1.7.0 text (and keeps
the key), so tests that compare `str(UpdateError)` with `updater.MSG_*` stay green (C-H-8). Display:
`error_text(error)`; composition: `message_of(error)`.

### 2.6 Rendering

```text
show(value):
  None                   -> ''
  zh                     -> zh(value)                          (1.7.0 localize in zh returned the text unchanged)
  en, Message            -> t(key, **{n: show(p)}); 'common.clauses' -> _join_sentences('en', [show(p) for p in parts])
                            (a cut message renders whole, D12)
  en, Message, key None  -> zh(value)                          (a foreign entry nothing lifts, 4.4 step 3)
  en, Value              -> i18n.label(group, value)
  en, Raw / plain str    -> text with PROXY_TOKEN replaced by t('core.proxy_mask')
  anything else          -> str(value)
```

`show()` never parses text. `i18n.t()` is not changed in `i18n.py`; in guarded test processes `messages` replaces
the module attribute with a checking wrapper (7.3). UI templates take shown params:
`t('log.collect.page', title=show(page['title']), ...)`. `show()` calls the original `t` with shown params, so its
own rendering never trips the wrapper.

### 2.7 Events and queue items (in-process)

| channel | 1.8.0 value | note |
|---|---|---|
| `log` (bare str, D-H-8) | `Message` for every app line; plain `str` for raw engine output | H5: no field added, same text |
| `waiting.reason` | `Message` (`core.wait.*`); `kind` unchanged | the `_run_once` reason is the constant `WAIT_PACE_OR_RETRY` (S1) |
| `item_done.reason` / `.error` | `Message` / `Raw` / plain, same text as 1.7.0 | keys unchanged |
| `done.archive_errors` | `str(error)` as today (a Message when the error carries one) | never shown; stored in `result`; never read |
| `collect_status`, `collect_error`, `update_status`, `update_error`, legacy `title` (updater with `legacy_events`) | `Message` (plain for third-party / OS text) | bare str stays a str |
| `collect_page` | page dict whose `title` and `links[].title` are `Message` / `Raw` | |
| `engine_version()` (not an event) | `BUILTIN = msg('core.update.builtin')` when unknown | `== '已内置'`, `version_newer` unchanged (C-H-18) |

Every consumer accepts `Message`, `Raw` and plain `str` (the golden replays feed plain strings, C-H-7).

### 2.8 Collector IPC (`job-*/request.events`; worker and window are always the same exe)

`worker_main.emit` writes `json.dumps([kind, wire(value)], ensure_ascii=False)`. `wire` walks dicts and lists:
`Message` → `{"$m": [text, key, wired params, origin]}`, `Value` → `{"$v": [group, value]}`, `Raw` → `{"$r": text}`,
everything else unchanged. `CollectionRunner.run` reads `kind, value = json.loads(line)` and emits `unwire(value)`;
`unwire` raises `ValueError` on a malformed object, so the existing `except (ValueError, TypeError)` skips the line.
The worker's own `Library.collection_event` call receives the in-memory values before the IPC. `request.json`
(`asdict(CollectOptions)`) is unchanged.

### 2.9 Strict-mode tripwires (tests only)

When `YTDLP_DESKTOP_MSG_GUARD` is set (section 7.3), `Message` and `Value` override `__getitem__`, `__add__`,
`__radd__`, `__mul__`, `__rmul__`, `__mod__`, `__rmod__`, `strip`, `lstrip`, `rstrip`, `replace`, `split`, `rsplit`,
`splitlines`, `partition`, `rpartition`, `format`, `format_map`, `ljust`, `rjust`, `center`, `lower`, `upper`,
`casefold`, `title`, `capitalize`, `swapcase`, `translate`, `expandtabs`, `zfill`, `removeprefix`, `removesuffix`.
Each finds its **producer frame** by walking outward from the caller (`sys._getframe(1)`): frames of `TRANSIT`
modules (7.8), the standard library and third-party code are skipped (so `textwrap.shorten(m)` called by a `src`
module is charged to that module); the walk ends **with** a producer at the first `src` frame outside `TRANSIT`, and
**without** one at a `verification/` frame (e.g. `unittest`'s diff of two texts), at a Tk callback boundary
(`tkinter.CallWrapper.__call__`: the frames below it did not hand the value over), or at the bottom of the stack.
When a producer exists and its module is guarded (7.8), the call is **recorded as a G2 violation and raises
`MessageFlattened`**; otherwise it behaves exactly like `str` (no record, no exception), so code that is not
converted yet keeps working. Without the variable the methods are the plain `str` ones (no cost in the
program). The record makes a violation visible even when the core's own broad `except` (or `i18n.localize`'s) swallows
the exception.

`TRANSIT` holds the modules that pass texts through without deciding what is shown: `messages`, `i18n_legacy` and
`i18n` always, and `ui_theme` until WP-6. A flattening inside them is charged to the module that handed them the
value. From WP-6 on `ui_theme` leaves `TRANSIT`: it is the display boundary, and any text operation of `ui_theme` on a
`Message` or `Value` (`_ellipsize` slicing, `Treeview._cut_values`' `str(value).replace(...)`, `fit` / wrap code) means
a value reached a widget without `show()`; that is then recorded with `ui_theme` as producer, also when it runs from a
Tk callback with no other `src` frame on the stack. The tripwires cannot see operations that call no method of the
object (2.3, last row); G2's landing checks (7.3) and G1f cover those.

---------------------------------------------------------------------------------------------------

## 3. Producers

### 3.1 Rules

- **P1** Every app text a `src` module emits, raises, logs to the page or stores is `msg(key, **params)` (or a module
  constant defined as one). No Chinese literal and no f-string with Chinese reaches a channel (G1).
- **P2** Params are computed exactly as the 1.7.0 f-string computed them (`_sentence(error)`, `math.ceil`,
  `Path(...).name`, `urltools.display`, `repr`, `f'{x:.1f}'`), so the zh value is byte-identical.
- **P3** Module constants that tests or other modules read keep their names and zh values (H4) and become `Message`
  constants; template constants that tests `.format()` (`updater.MSG_*`) become `messages.template(key)`.
- **P4** Every derived-string operation on a value that may be a message uses `cut` / `masked` / `stripped` /
  `clauses` / `message_of` / nesting (3.5).
- **P5** User data and raw engine text that is stored is wrapped with `raw()` at the producer, after its last
  string operation (3.2, 3.3).
- **P6** Logging calls (download.log only, never the page) keep their literals (`settings.py`, `applog`, English
  `log.warning(...)`); `unfinished._report` keeps `_log.warning('%s', text)` (writes the zh text, D-H-10).
- **P7** Stored enum values (link kinds, `MODES`, `PRESETS`, `RESOLUTIONS[0]`, `BEST`, `naming.LABELS`, `'网页链接'`
  compares), on-disk names (I18N.md D10), window titles, the self-test texts and UI H4 constants stay literals and
  are listed in the G1 allow-list.
- **P8** Masking happens before anything is emitted or stored, as in 1.7.0: `engine._masked` becomes a wrapper of
  `messages.masked(value, [(proxy, PROXY_TOKEN), (credentials + '@', '')])`; the collector uses
  `[(options.proxy, PROXY_TOKEN)]`.

### 3.2 Download side (WP-2 unless marked; `engine.py`, `unfinished.py`, `naming.py`, `urls.py`, `formats.py`)

| flow | producer (1.7.0 line) | 1.8.0 value |
|---|---|---|
| D-F01 | `Runner._wait` 1151 | `msg('core.engine.waiting', reason=reason, seconds=math.ceil(seconds))`; `reason` is the message the caller passes |
| D-F02 | `_intercept` 1195 | `COOKIE_LINE_FAILED = msg('core.cookies.line_failed')`, `COOKIE_LINE_SKIPPED = msg('core.cookies.line_skipped')` (H4 constants, no params: H6) |
| D-F03 | `_intercept` 1200 | `msg('core.engine.present_skipped', name=filtered.group(1))` |
| D-F04 | 1215, 1254 (`say`), 1746 | `msg('core.engine.kept_existing', name=Path(...).name)` |
| D-F05 | `_source_line` 997-999 (emitted 1223, 1939) | `msg('core.engine.other_format_source', name=name)` |
| D-F06 | `naming.finalize` 88, 92, 96, 98 | `msg('core.rename.locked', name=)`, `msg('core.rename.failed_named', reason=error.strerror or type(error).__name__, name=)`, `msg('core.rename.too_many', name=)`, `msg('core.rename.failed', reason=type(error).__name__)` |
| D-F07 | `_run_once` 1373 (**S2**), `_present_done` 1738; `_SafeArchive.media` 698 | `msg('core.engine.archive_info_failed', error=message_of(error))`; 698: `OSError(msg('core.archive.unwritable', error=message_of(error)))`. Line 1374 / 1739 `self.archive_errors.append(str(error))` unchanged |
| D-F08 | `_cooled` 1653 (**S4**) | `msg('core.engine.cannot_start_engine', error=_sentence(error))` (raw OS text) |
| D-F09 | `_cooled` 1662 (**S5**) | `msg('core.engine.rate_limited_out')` |
| D-F10 | 1710-1711 | `msg('core.engine.present_untagged', count=...)` |
| D-F11 | 1806 | `msg('core.engine.duplicate_merged', url=url)` |
| D-F12 | 1834 | `msg('core.engine.record_unreadable', error=message_of(error))` |
| D-F13 | 1875 | `msg('core.engine.no_collect_history')` |
| D-F14 | 1889 | `msg('core.engine.resume_partial')` |
| D-F15 | 1901-1902 | `msg('core.engine.picture_exists', name=_shown_name(pictures[0]))` |
| D-F16 | 1916, 1974 | `msg('core.engine.cannot_start_link', error=message_of(error))`; nested producers: 464 `ValueError(msg('core.proxy.scheme'))`, 472 `ValueError(msg('core.collect.bad_source'))`, 491 `LinkTooLong(msg('core.links.too_long', length=len(url), url=urltools.display(url)))`, D-F36, D-F07 (698), C-F27/C-F28, 425 `ValueError(msg('core.options.unsupported'))` (new key) |
| D-F17 | 1963 | `msg('core.engine.subtitles_dropped')` |
| D-F18 | 2025 | `msg('core.engine.incompatible')` |
| D-F19 | 2039 | `BOT_CHECK_HINT = msg('core.engine.bot_check_hint')`, `BOT_CHECK_STALE_HINT = msg('core.engine.bot_check_stale')` |
| D-F20 | 2041 | `msg('core.engine.item_unfinished', code=result)` |
| D-F21 | 2052 | `msg('core.engine.error', error=message_of(error))` |
| D-F22 | `download` finally 2089 (**S3**) | `msg('core.engine.unfinished_not_saved', error=message_of(error))`; nested: `unfinished` 246 / 349 `OSError(msg('core.unfinished.cannot_set_aside' / 'core.unfinished.no_overflow_name', path=str(path)))` |
| D-F23 | `unfinished._read` 173-201, `_keep_unreadable` 251-252, `_write_overflow` 356 | problems `msg('core.unfinished.problem.unreadable' / 'too_large' (size=f'{mb:.1f}') / 'damaged' / 'unknown_format' / 'dropped' (dropped=n) / 'bad_entries')`; notices `msg('core.unfinished.kept_aside', problem=..., target=target.name)`, `msg('core.unfinished.overflow', count=len(moved), target=target.name)`; `_report` unchanged (zh WARNING; the callback gets the message). The window-thread gap D-H-11 stays as it is (zh parity) |
| D-F24 | raw engine output 1375 | unchanged plain `str`, never translated |
| D-F25 | callers of `_wait`: 1668 (**S6**), 1672 (**S7**), 1850 | `msg('core.wait.rate_queue')`, `msg('core.wait.rate_retry', attempt=self._rate_attempt, limit=MAX_RATE_LIMIT_RETRIES)`, `msg('core.wait.pace')` |
| D-F26 | `_run_once` 1354 (**S1**) | `'reason': WAIT_PACE_OR_RETRY`, module constant `WAIT_PACE_OR_RETRY = msg('core.wait.pace_or_retry')` |
| D-F27 | studio `refresh_shell` | unchanged `t()` |
| D-F28 | `explain_error` 100-112 | `ERROR_REASONS` pairs `(pattern, msg('core.reason.region' / 'unavailable' / 'page_403' / 'page_404' / 'page_410' / 'network' / 'http_403' / 'http_404' / 'http_410' / 'format' / 'unsupported'))`; `NETWORK_REASON`, `BOT_CHECK_REASON`, `STALE_REASON`, `ENGINE_ERROR_REASON` = the matching `msg('core.reason.network' / 'bot_check' / 'stale' / 'engine_error')`; `msg('core.reason.exit_code', code=code)`. Patterns run on `zh(text).strip()` as today. Fallback keeps the input's type: a `Message` → `cut(text, 70)` (**D-H-2 fixed**: the key is never cut); `Raw` → `raw(_EXTRACTOR_PREFIX.sub('', text, count=1)[:70])`; plain → the 1.7.0 expression |
| D-F29 | `item.error` 1918, 2005, 2050; `_note_line` 1290-1293; 1975 | 1918 and 2050: `item.error = cut(_masked(message_of(error), item.options), 200)`; 2005: `cut(_masked(self.last_error, item.options), 200) if result != 0 else ''` (`_masked` is the P8 wrapper; it keeps the value's type). `_note_line`: an echoed cookies line sets `self.last_error = COOKIE_LINE_ITEM` (`msg('core.cookies.line_failed_item')`, new key, zh = the 1.7.0 stored fragment; **D-H-1 fixed**), any other `ERROR:` line `self.last_error = raw(line[6:].strip()[:200])`; 1975 `self.last_error = message_of(error)` |
| D-F30 | `unfinished.merge` / `save` / `_clean` | stored through `encode` / `decode`, kind `failed_entry` (4.3); `merge` uses `cut(_text(error), 200)` |
| D-F31, D-F32 | `done.archive_errors`, `done.failed_path` | unchanged |
| D-F33 | media titles in `title` / `media` / `item_done` events | unchanged plain `str` in events; `library` stores them as `Raw` (C-F22) |
| D-F34 | `urls.parse_download_text` 143, 148 | `ValueError(msg('core.links.no_link_on_line', line=number))`, `ValueError(msg('core.links.none'))` |
| D-F35 | `build_command` | as D-F16 |
| D-F36 | `check_cookie_file` 233-256 | same exception classes with `msg('core.cookies.missing', file=path)`, `'core.cookies.too_large'`, `'core.cookies.json'`, `'core.cookies.not_netscape'` |
| D-F37 | `parse_urls` 123, 127; `read_url_file` 134, 142 | `msg('core.links.bad_line', line=line_number)`, `'core.links.none'`, `'core.links.file_too_large'`, `'core.links.encoding'` |
| D-F38 | `Options.from_dict` 178-190; `formats.by_mode` 75, `by_id` 82, `by_label` 89 (English only, D-H-3) | new keys (Appendix A): `core.options.not_object`, `core.options.no_output`, `core.formats.bad_mode` (mode=repr(mode); used by both `from_dict` and `by_mode`), `core.options.bad_resolution` (resolution=repr(...)), `core.formats.bad_id` (id=repr(...)), `core.formats.bad_label` (label=repr(...)); zh = en = the 1.7.0 English text |
| D-F39 | `unfinished.save` OSErrors | as D-F22 |
| D-F40 | `ui_download.decode_link_file` 139-151 (WP-4) | `ValueError(msg('core.links.file_too_large' / 'core.links.encoding'))` |
| D-F41 | `ui_download.launch` 1541 (WP-4) | `ValueError(msg('core.links.none'))` |
| D-F42 | library errors in `launch` (WP-4) | `t('log.history_write_failed', error=error_text(error))` |
| D-F43 | `updater.engine_version` (WP-3) | `BUILTIN`; studio shows `show(engine_version())` (WP-4) |
| D-F44 | `Runner.update` legacy events | updater messages (WP-3); `on_title` shows `show(value)` (WP-4) |
| D-F45 | `ui_download` 1579 placeholder title (WP-4) | `msg('value.job_title.placeholder', count=len(urls), mode=value('mode', options.mode))` |
| D-F46 | `ui_download.PROXY_REASON` (WP-4) | `PROXY_REASON = msg('download.reason.proxy')` (H4 value kept); `history_value` unchanged otherwise |
| D-F47 | `ui_log.append_log` (WP-4) | `line = messages.show(line)`; proxy mask, `logger.info`, buffer unchanged |
| D-F48, D-F50-D-F55 | log-file lines, window titles, UI `t()` lines, self-test texts, folder names, stored enum values | unchanged (G1 categories `logfile`, `h4`, `internal`, `file`, `stored`) |
| D-F49 | `studio.report_error` (WP-4) | `value=error_text(value)` |

`APP_VERSION = '1.8.0'` (WP-2).

### 3.3 Collect / library / update side (WP-3 unless marked; `collector.py`, `library.py`, `updater.py`)

| flow | producer (1.7.0 line) | 1.8.0 value |
|---|---|---|
| C-F01 | `ca_bundle` 56 | `RuntimeError(msg('core.collect.ca_cache'))` |
| C-F02…C-F07 | `CollectOptions.validate` 101-112 | `ValueError(msg('core.collect.too_many_pages' / 'limits' / 'bad_address' / 'bad_proxy' / 'needs_chrome' / 'bad_resume'))` |
| C-F08 | `parse_page` 122-123, 139 | `title = raw((' '.join(...).strip() or urlsplit(base).hostname or '')[:300]) or msg('value.page_title.fallback')`; a link label is `raw(re.sub(r'\s+', ' ', label).strip()[:200]) or title` (the same object, C-H-10); `add()` keeps a `Message` label unchanged |
| C-F09 | 129 | `ValueError(msg('core.collect.no_match'))` |
| C-F10 | 156 | `add(url, msg('value.link_kind.dynamic_request'), True)` |
| C-F11 | 199 | `msg('core.collect.page_n', page=len(visited) + 1)` |
| C-F12 | 203-204 | page and link `title = msg('value.link_kind.direct')`; the link `kind` stays the stored constant `'媒体地址'` |
| C-F13 | 234 | `ValueError(msg('core.collect.http_status', status=response.status))` |
| C-F14 | 236 | `ValueError(msg('core.collect.too_large'))` |
| C-F15, C-F16 | 245-249, 278-285 | `cut(masked(message_of(error), [(options.proxy, PROXY_TOKEN)] if options.proxy else []), 900)` |
| C-F17 | 359 | `msg('core.collect.worker_exit', code=code)` |
| C-F18 | 360-362 | `cut(message_of(error), 900)` |
| C-F19, C-F20 | link kinds; log-file line | unchanged |
| C-F21 | `Library.job` 378 | `ValueError(msg('core.library.no_job'))` |
| C-F22 | `download_event('title')` 576-580 | column title `msg('value.job_title.multi', title=raw(value), count=count)` when `count > 1`, else `raw(value)`; the job's sidecar is rewritten in the same transaction |
| C-F23 | 647, 668 | `ValueError(msg('core.library.bad_interval'))` |
| C-F24 | `subscription` 655 | name `raw(name.strip()[:100]) or msg('value.subscription.untitled')` |
| C-F25 | `finish_subscription` 691 | `error or msg('value.subscription.interrupted')` |
| C-F26 | `open_library` 780 | `msg('core.library.temporary', error=message_of(error))` |
| C-F27, C-F28 | 798 / 852; 847 | `ValueError(msg('core.archive.outside_root'))`; `OSError(msg('core.archive.cannot_set_aside', record=str(record)))` |
| C-F29 | `Archive.media` 868 | `ValueError(msg('core.archive.media_mismatch'))` (nested by S2) |
| C-F30, C-F31 | 880, 886 | `ValueError(msg('core.archive.transcript_type'))`, `ValueError(msg('core.archive.transcript_outside'))` |
| C-F32, C-F33 | `Archive.prepare` 825, `Archive.save` 857 | `t('filetext.page_text', title=show(page['title']), ...)`, `t('filetext.readme_head', title=show(data['title']), ...)`; `PAGE_TITLE_KEYS` and `_shown_title` deleted |
| C-F34 | `download_event('item_done')` 626-633 | `data['errors'].append(value['error'])` (type kept), `last_reason = {'error': data['errors'][-1], 'reason': stripped(reason)}` |
| C-F35 | `finish_subscription` 686-687 | `payload = dict(item, page_url=page['url'], page_title=page['title'])` from decoded pages; `encode(payload, 'entry')` |
| C-F36 | `contexts` 712-723 | titles from decoded pages (`Message` / `Raw` / plain) |
| C-F37, C-F38 | on-disk names, log lines | unchanged |
| C-F39 | `collection_ui.subscription_result` 38-46 (WP-5) | `clauses(record['data']['errors'][-2:]) or msg('value.subscription.stopped' if cancelled else 'value.subscription.unfinished')` |
| C-F40 | `library_ui` 1143 (WP-5) | `title=msg('value.job_title.subscription', name=row['name'])` (the decoded name) |
| C-F41 | `library_ui.check_subscription` 1165 (WP-5) | `finish_subscription(row['id'], [], False, message_of(error))` |
| C-F42 | placeholder | D-F45 |
| C-F43 | `engine_version` 108 | `BUILTIN = msg('core.update.builtin')` |
| C-F44 | 395, 417, 448 | `self._status(msg('core.update.checking' / 'downloading' / 'testing'))` |
| C-F45 | `MSG_*` raise sites; `_reason`, `_file_reason`, `_check_start` 359-368; `run` 473-478 | `UpdateError(msg('core.update.no_github', reason=_reason(error)))` (MSG_NETWORK), `bad_release` (MSG_RELEASE), `no_exe` (MSG_NO_ASSET), `bad_host` (MSG_ORIGIN), `no_sha` (MSG_NO_DIGEST), `bad_size` (MSG_SIZE), `bad_hash` (MSG_MISMATCH), `incompatible` (MSG_PROBE), `write_failed` / `save_failed` / `record_failed` (reason=`_file_reason(error)`, raw), `start_failed` (reason = `msg('core.update.start_timeout', timeout=f'{error.timeout:g}')`, `msg('core.update.start_cannot')`, `msg('core.update.start_exit', code=code)`, `msg('core.update.start_no_version')`); `_reason`: raw `f'HTTP {code}'`, `msg('core.update.timeout')`, raw text; `run`: `emit('update_error', message_of(error))`. `MSG_* = messages.template(key)` (names and values kept) |
| C-F46 | `restore_bundled` 247 | `UpdateError(msg('core.update.restore_failed'))` |
| C-F47-C-F51 | swallowed check, cookie errors (D-F36), log lines, settings warnings, defaults | unchanged (`settings.py` is not edited) |
| C-F52 | `'[代理]'` | `messages.PROXY_TOKEN` in engine and collector; `show()` renders it in en |

Collect-job title (WP-5, `collection_ui` 341): `create_job('collect', title or raw(options.urls[0]), ...)`.

Emit hooks (G2): `Runner.__init__`, `EngineUpdater.__init__`, `CollectionRunner.__init__` set
`self.emit = messages.guarded(emit, '<module>')`; `collector.worker_main` wraps its local `emit` the same way.

The `msg()` key of C-F39 is a conditional expression of two literals; G1c accepts that form (7.2).

### 3.4 Library and unfinished store paths (WP-3 / WP-2)

Every `library.py` path that reads a stored record calls `messages.decode`; every write calls `messages.encode`
on the complete object being written (4.4, 4.5). There are two load paths and they must not be mixed:

- **Display reads** use `_read_record(raw, columns)` = `messages.decode(raw, 'job')` + `read_options` on the result's
  `options` (1.7.0 behaviour: the real 1.6.0-only format back in `mode`, `MODE_KEY` removed) + the lift of
  `options.archive_contexts[*].title` (kind `context_title`, lift only, no sidecar) + `decode_columns`. Used by: `job`
  (job + its pages, kind `page`), `history` (the rows it returns), `subscriptions`, `pending` (kind `entry`),
  `archives` (kind `archive`, decoded before `dict(folder=..., **data)`), `contexts` (kind `page`). For kinds other
  than `job` only `decode` (and `decode_columns` for subscriptions) applies. Nothing read this way is ever written
  back.
- **Read-modify-write** uses `_load_for_write(raw_json, kind, columns)` = `json.loads` of the stored JSON, then
  `messages.decode` (text fields of the kind only) and `decode_columns`; **never `_read_record`, `read_options` or any
  other transformation of `options`**. `decode` returns a shallow copy in which every non-text value is the very same
  object, so `options` (with its stored 1.5.1-compatible `mode` and `MODE_KEY` = `mode_v2`), `checkpoint`, `result`
  and every other key are written back exactly as stored, unless the operation itself changes them through the
  existing 1.7.0 code (`stored_options` in `change_job(options=...)`, `_extend`'s own `read_options` /
  `stored_options` pair). Used by: `change_job`, `collection_event`, `extend_job`, `download_event`,
  `finish_subscription`, `rename_job` (becomes one transaction that updates the column and `data.msgs`), the
  interrupted marking in `history` (it calls `change_job(row_id, status='interrupted')`, which loads the row again
  for writing; the row already decoded for display is never written), `Archive.prepare` / `media` / `attach`
  (`资料信息.json`, kind `archive`; `Archive.save` encodes), `unfinished.merge` / `save`. Each selects `kind`,
  `title` (jobs) or `name` (subscriptions) together with `data` and passes the decoded columns as `columns=` to
  `encode`, so the column's entry survives (C-H-3).
- whole writes: `create_job`, `subscription`, `collection_event` page rows (`INSERT OR REPLACE`), `finish_subscription`
  entries, `archive_index`, `write_json` of `资料信息.json`;
- `_storable` keeps the types: an encodable `Message` / `Raw` / `Value` is returned as is; a non-encodable one is
  rebuilt with the repaired text (and, for a `Message`, recursively repaired raw params, same key; a `foreign`
  message keeps its `entry`).
- `unfinished.py`: `_clean` decodes each entry (kind `failed_entry`); `_encode` and `_write_overflow` encode each
  entry; `merge` keeps value types (`dict(item)`, `cut`).

Required test (I1, `test_messages_store.py` and `test_rollback_readers.py`, WP-3): a download job created with a
1.6.0-only format (`formats.MODES[5]` and `formats.MODES[6]`, so the stored `options.mode` is the nearest 1.5.1 mode
and `options.mode_v2` the real one) and a sidecar is driven through every read-modify-write path that can touch it —
the `history()` interrupted marking (status `running`, dead owner), `change_job(status=...)` with no other update,
`change_job(worker=...)`, `download_event` (`item_done`, `done`, `title`), `extend_job`, `rename_job` — and after each
step the stored `options` object serialises to exactly the bytes it had before (`json.dumps(..., ensure_ascii=False)`
of `data['options']` compared), `mode_v2` is present, and the sidecar entries of unchanged texts are present. The
rollback test then runs the 1.5.1 resume path (`LibraryUI.restore_download_options` of 1.5.1, stub widgets, on the
stored options) and the 1.6.0 / 1.7.0 `job()` reader on the same row: 1.5.1 gets a mode of its own, 1.6.0 / 1.7.0 get
the real one. (The existing `test_review_state.py:529-551` checks only `change_job(options=...)`, not the status-only
rewrites.)

### 3.5 Derived-string sites replaced (the D-H-8 list)

`_masked(...)[:200]` (engine 1918, 2005, 2050) → `cut(masked(...), 200)`; `explain_error` `.strip()` / `[:70]`
(102, 112) → 3.2 D-F28; `line[6:].strip()[:200]` for the cookies echo (1293) → `COOKIE_LINE_ITEM`; `_text(error)[:200]`
(unfinished.merge) → `cut`; `str(value['error'])` and `reason.strip()` (library 627, 632) → type kept / `stripped`;
collector `title[:300]`, `message[:900]`, `.replace(proxy, '[代理]')` → 3.3 C-F08, C-F15; `'；'.join(errors[-2:])`
(collection_ui 44) → `clauses`; `str(error)` in collector / updater emits → `message_of`. The collect / archive
search strings concatenate `zh(title)` and `show(title)` explicitly (6.2). Any other site is found by the tripwires
(method calls) or by G2 where its result lands (7.3); G1f forbids new ones in core text slots.

### 3.6 New catalog keys

Eight keys, added through the inventory (`verification/i18n_inventory.json`, new section `new_strings_1_8_0`) and
generated into `i18n_core.py` by `i18n_tool.py catalog` (Appendix A). `common.clauses` is structural (handled in
`messages.py`, never in a catalog). Every zh text of a 1.7.0 key stays byte-identical (test, 8.1). Every other key
named in sections 3.2, 3.3 and 6 exists in the 1.7.0 catalogs with both languages (checked during the revision:
all key families of the producer tables are present; only the eight of Appendix A are new). WP-1 adds all eight, so
no later package needs to touch the inventory or a catalog module (10.2, rule for a missing key).

Key registry (forward compatibility): a key that 1.8.0 can write into a sidecar is never removed, renamed or given
other placeholders by a later version; a changed meaning gets a new key. WP-1 generates
`verification/golden/sidecar-keys.json` (every `core.*`, `value.*`, `download.reason.*` key with its sorted
placeholder names, plus `common.clauses`); `test_messages.py` fails when a pinned key is missing or its placeholders
differ. Later versions may only add to it.

---------------------------------------------------------------------------------------------------

## 4. Storage and legacy conversion

### 4.1 What 1.8.0 writes

Exactly the 1.7.0 bytes in every text field, plus one `msgs` map per message-bearing JSON object. Nothing is
written at upgrade; a row gets a sidecar only when 1.8.0 writes it in normal work. The owner's library,
settings.json, unfinished list, failed list and history are untouched by the upgrade itself.

### 4.2 Sidecar format

```json
"msgs": {
  "此链接无法开始：链接过长（2100 个字符），无法交给下载引擎：youtu.be/…":
      {"k": "core.engine.cannot_start_link",
       "p": {"error": {"k": "core.links.too_long", "p": {"length": 2100, "url": "youtu.be/…"}}}},
  "Some Song": null
}
```

- Map key: the **stored text** of a field of the object (after `_storable`, masking and cuts), compared exactly.
- Entry: `{"k": key, "p": params}` (`"p"` omitted when empty) for a message; `null` for a `Raw` text that the legacy
  reader would lift (E2); a `foreign` message's own entry, verbatim (E1b).
- Params encoding: number → number; plain / `Raw` text → string; `Message` → `{"k","p"}`; `Value` → `{"g": group,
  "v": value}`; `common.clauses` parts → list.
- Location: inside the same JSON object as the text (table 4.3); for column texts (`jobs.title`,
  `subscriptions.name`) inside that row's `data`. Never inside `jobs.data.options`, `subscriptions.data.options`
  (`CollectOptions(**options)`, C-H-16), `checkpoint`, `_checkpoint`, `result`; never a key named `folder`
  (`dict(folder=..., **data)`, C-H-15).
- Collision: two fields of one object with the same text but different meanings get one entry; a message entry
  wins over `null` (1.7.0 showed both translated). Accepted (12, L-5).
- Staleness: 1.8.0 rebuilds the map on every write, so its own entries always describe the current text. A version
  without sidecars (1.5.1 / 1.6.0 / 1.7.0) keeps the map on read-modify-write and can store the **same** text with
  another origin while the entry stays — e.g. its `Archive.prepare` sets `title = context['title']` for a user page
  literally titled `网页` or `媒体直链` where 1.8.0 had written a message entry, or the reverse with a `null` entry. The
  entry then gives the wrong rendering in en (zh shows the stored text, data unaffected): L-13.

### 4.3 Record kinds and their fields

| record kind | object | text fields (field kind) | sidecar |
|---|---|---|---|
| `job` | `jobs` row + `data` | column `title` (`job_title`: context from `kind`, `data.titled`, `len(data.options.urls)`); `data.errors[*]` (`error`); `data.last_reason.error` (`error`); `data.last_reason.reason` (`reason`, paired with `.error`) | `data.msgs` |
| `job` (download options) | `data.options.archive_contexts[*].title` | `context_title` (lift only, no sidecar) | none |
| `subscription` | `subscriptions` row + `data` | column `name` (`name`); `data.last_error` (`clauses`) | `data.msgs` |
| `page` | `pages.data` | `title`, `links[*].title` (`title`) | `pages.data.msgs` (one per page) |
| `entry` | `entries.data` | `title`, `page_title` (`title`) | `entries.data.msgs` |
| `archive` | `archives.data`, `资料信息.json` | `title`, `page_title` (`title`) | `msgs` in both |
| `failed_entry` | `failed-downloads.json entries[i]` (also the bad / overflow files) | `error` (`error`) | `entries[i].msgs` |
| — | `jobs.data.result`, `options`, `checkpoint`, `downloads` table, failed-list top level, `settings.json`, `restart-state.json`, `engines\yt-dlp.json`, download.log, `阅读说明.txt`, `网页正文.*`, exports | no sidecar | — |

The schema table lives in `messages.py` (one place); `library.py` / `unfinished.py` pass only the record kind.

### 4.4 `decode(record, kind)` and `decode_columns(record, kind, columns)`

For each text field of the kind (and each column passed to `decode_columns`), with the record's `msgs` map `M`
(missing or not a dict → empty):

1. `text` not a non-empty `str` → unchanged.
2. `M[text]` is a **valid** entry → `Message(value=text, key, params, origin='stored')`; nested messages get their zh
   value from the catalog and `origin='stored'`. Valid: a dict whose `k` is a key of the live zh and en catalogs (or
   `common.clauses`), whose `p` is absent or a dict with exactly the key's placeholder names, and whose params all
   decode (numbers; strings; `{"g","v"}` with a `GROUPS` group; nested valid entries; for `common.clauses` a list of
   such parts).
3. `M[text]` is a **well-formed** entry that is not valid — written by a later version: an unknown key, other
   placeholders, or a nested entry that is not valid → `Message(value=text, origin='foreign', entry=<the entry as
   compact JSON>)` whose `key` / `params` are those of `i18n_legacy.lift(field_kind, text, context)` when that lifts
   the text, else `key=None` (shown as the stored text). Well-formed: a dict with `k` a non-empty `str` of at most
   200 characters, `p` absent or a dict, the entry at most 2048 characters as compact JSON. The entry is kept and
   written back verbatim by `encode` (E1b) while the text is still present, so a 1.8.0 write never drops what a
   newer version stored (1.8.0 stays a rollback target, 5.1).
4. `M[text]` is `null` → `Raw(text)`.
5. Otherwise (no entry, or a malformed one, which is ignored) → `i18n_legacy.lift(field_kind, text, context)`: a
   match → `Message(value=text, ..., origin='legacy')`; no match → the plain text.

The value of a decoded message is always the stored text, never a re-rendering (zh identity, C-H-14). `decode`
returns a shallow copy without `msgs` in which only the text fields of the kind are replaced (every other value, in
particular `options`, is the same object: 3.4), never raises (a failure in the reader leaves the plain text) and
caches the lift step on `(field kind, context, text)` (`functools.lru_cache`, 4096 entries as in 1.7.0; the 1.7.0
bound "10 000 lines in under a second" stays as a test of the lift).

### 4.5 `encode(record, kind, columns=None)`

Builds `msgs` from the typed values of the fields of 4.3 (and `columns`):

- **E1** `Message` that is *persistable* → entry. Persistable: `origin` is `live` or `stored` and every nested
  message param is persistable or `foreign`. A legacy-origin message (or one nesting one) gets **no** entry: a guess
  about legacy text is never written as if it were a key.
- **E1b** `Message` with `origin='foreign'` → its `entry`, verbatim (also as a nested param of a new message).
- **E2** `Raw` → `null` **only when** `i18n_legacy.lift(field_kind, text, context)` would lift the text (a user
  title literally equal to an app text such as `网页`, an engine line that quotes one); otherwise no entry, because the
  plain text reads back unchanged and renders exactly like the `Raw`. So link titles, media titles and engine text
  normally cost nothing in the store (a page has up to `collector.MAX_LINKS` = 2000 link titles).
- **E3** plain `str` → no entry (lifted on read, like legacy text).
- **E4** Pruning is implicit: the map is rebuilt from the current typed values, so entries of texts no longer
  present disappear and entries stay for texts still present (including `columns`). Every entry a reader decoded —
  valid (E1), foreign (E1b) or `null` (E2) — comes back as a typed value, which is why every read-modify-write path
  must decode before it encodes (3.4); `encode` ignores a `msgs` key in its input.
- **E5** An entry whose JSON is longer than 2048 characters, whose text or params cannot be encoded as UTF-8, or
  whose building raises, is skipped; the field then reads back through the lift. `encode` never raises; on an
  unexpected error it returns the record without `msgs` and logs one WARNING per process.
- **E6** Params are masked by the producer before they get here (P8); `encode` adds no text of its own.
- **E7** Empty map → no `msgs` key (records without messages stay byte-identical to 1.7.0, which keeps the
  existing whole-dict test assertions valid for plain fixtures).

### 4.6 The legacy reader (`src/i18n_legacy.py`, `src/i18n_legacy_data.py`)

`i18n_legacy_data.py` is **generated** by `PY verification\i18n_tool.py legacy` from
`verification/release-sources/1.7.0/src` (its `i18n_maps.LOCALIZE`, `VARIANTS`, `NUMERIC`, `RECURSE` and the zh
catalogs) plus the frozen additions below, and is never edited by hand. It holds `(key, zh template)` pairs as
data (a later zh wording change cannot break recognition), `NUMERIC`, `RECURSE`, the field-kind key sets and the
SHA-256 of the files it was generated from. `test_messages.py` regenerates it and compares. Frozen additions:
`('core.cookies.line_failed_item', 'cookies 文件中有一行格式不对，无法读取（内容不记录）')` (the D-H-1 fragment)
and nothing else unless G3 finds a stored shape 1.7.0 lacked (then it is added in WP-1 with the G3 entry naming it).

`i18n_legacy.py` holds the 1.7.0 matcher (`_Template`, `_Index` moved and adapted to return `(key, params)`) and
the rules. API: `lift(field_kind, text, context=None) -> (key, params) | None`; `recognise(text, display=False) ->
key | group | None` (G2 only, 7.3: R-text rules (a)–(d) over the frozen table, the live `core.*` / `value.*` /
`download.reason.*` zh texts and every CJK `GROUPS` value; `display=True` adds the UI-text exclusion). `encode` uses
`lift` (E2), never `recognise`, so the program's behaviour does not depend on the test-only rules. Rules:

| rule | field kinds | behaviour |
|---|---|---|
| L1 exact | all | the whole text equals a placeholder-free frozen text allowed for the kind |
| L2 template | `error`, `reason`, `clauses`, `job_title` | the whole text matches a frozen template allowed for the kind (1.7.0 order: longest literal first; `NUMERIC` captures; `RECURSE` captures lifted again, depth ≤ 3: `reason` / `error` with all keys, `problem` with `core.unfinished.problem.*`, `mode` → `Value('mode', capture)`, `name` with the `name` kind) |
| L3 cut prefix | `error` when `len(text)` is 200 or 900; `reason` when it is 70 | the text is a prefix of an instance of a template: the parts before the cut match fully, the cut falls inside a literal part or a capture; accepted only if ≥ 8 characters of the template's literal text matched and the text starts with the template's first literal part. A capture cut by the limit keeps what remains (en shows the partial value) |
| L4 R4 pairing | `reason` with a sibling `last_reason.error` | if `reason == _EXTRACTOR_PREFIX.sub('', error.strip(), count=1)[:70]` (the frozen 1.7.0 fallback), the reason takes the lift of `error` (kind `error`) with the reason's own text as value. This repairs the 1.6.0 / 1.7.0 D-H-2 rows the owner already has |
| L5 clauses | `clauses` | the whole text first (L1 / L2: `value.subscription.interrupted` and `needs_chrome` contain `；`); then split on `；` and choose the grouping of contiguous parts that minimises (unlifted groups, number of groups); lifted groups become messages, unlifted ones stay plain; the result is a `common.clauses` message when at least one group lifted (C-H-5 fixed for legacy rows) |
| L6 job title | `job_title` | collect job → `value.job_title.subscription` (`name` capture with the `name` kind); download job with `titled` and > 1 link → `value.job_title.multi` (`title` capture plain, `count` numeric); `titled` with one link → no lift; untitled → `value.job_title.placeholder` and its 1.5.1 variant `{count}条 · {mode}` (CR2-2 rule, moved from `library_ui.history_title`) |
| L7 titles | `title`, `context_title` | L1 only, keys `TITLE_KEYS = ('value.page_title.fallback', 'value.link_kind.direct', 'value.link_kind.dynamic_request')`; G3 asserts that this set equals the set of app-made title literals of the three old producer trees |
| L8 names | `name` | L1 only, `NAME_KEYS = ('value.subscription.untitled',)` |
| L9 deny-list default | `error`, `reason`, `clauses` | every frozen key is allowed (no per-field allow-lists); G3 lists any exemption by name |

The 1.7.0 `'[代理]'` token is not a lift: raw text keeps it and `show()` renders it (2.6).

### 4.7 Storage invariants (each enforced by a test, section 7)

- **I1** Every old text field keeps its type (`str`) and its exact 1.7.0 bytes for the same operation.
- **I2** No sqlite column, table, index or `user_version` is added; no top-level key in `failed-downloads.json`; no
  key in `settings.json`.
- **I3** Sidecars only as the additive `msgs` key in the objects of 4.3; never inside `options`, `checkpoint`,
  `_checkpoint`, `result`; never the key `folder`.
- **I4** A decoded value always carries the stored text; re-saving writes that text, never a re-rendering.
- **I5** Legacy-origin messages are never persisted (E1).
- **I6** `encode` / `decode` never raise and never block a write.
- **I7** No cookie content and no unmasked proxy string in any stored byte, sidecar included (H6).
- **I8** No write at upgrade: opening a 1.7.0 library, settings.json and failed list with 1.8.0 and only reading
  changes no byte (the history `interrupted` marking is the 1.7.0 behaviour and is the only write a read path may do).
- **I9** The collector worker writes through the same `Library` code (same codec).
- **I10** A read-modify-write never changes a stored value it was not asked to change: `options` (including `mode`
  and `mode_v2`), `checkpoint`, `result` and every unknown key keep their bytes (3.4 required test).
- **I11** Well-formed sidecar entries that 1.8.0 does not understand survive every 1.8.0 write while their text is
  present (4.4 step 3, E1b; `test_messages.py` and `test_messages_store.py`: entries with an unknown key, with other
  placeholders and with an unknown nested key go through `change_job(status=...)`, `download_event('item_done')`,
  `finish_subscription`, `Archive.media` and `unfinished.save` and are found again byte for byte; en shows the lift or
  the stored text, never an exception).

---------------------------------------------------------------------------------------------------

## 5. Rollback

### 5.1 How each old reader treats 1.8.0 data

| stored form | 1.5.1 | 1.6.0 | 1.7.0 |
|---|---|---|---|
| `jobs.title` | shown raw | `PLACEHOLDER_TITLE` parse, then raw / first link | parse + `localize` per job kind |
| `jobs.data` (`errors`, `last_reason`, `msgs`) | `errors[-1][:130]`, `'；'.join(errors[-2:])` (need `str`: kept); RMW of the whole JSON keeps `msgs` | `str(errors[-1])`, `last_reason` pairing, raw display; RMW keeps `msgs` | same + `localize` |
| `pages.data` (+`msgs`) | titles raw; `kind` compared; rows replaced whole by whoever collects (a replaced row has no `msgs`: correct) | same | same + `localize` |
| `subscriptions.name`, `data.last_error` (+`data.msgs`) | concatenated raw; `finish_subscription` RMW keeps `msgs`, overwrites `last_error` (the old entry is orphaned: correct) | same | same + `localize` |
| `entries.data` (+`msgs`) | raw; rows replaced whole | same | same |
| `archives.data`, `资料信息.json` (+`msgs`) | `archives()` `dict(folder=..., **data)` works (no `folder` key); prepare / media / attach load-modify-save keep `msgs`; `Archive.media` replaces `title` (entry orphaned: correct) | same | same |
| `failed-downloads.json` | reads the legacy view (`load_failed_job`), ignores `entries`; its own run rewrites the file without `entries` (accepted since 1.6.0) | `_clean` keeps `entries[i].msgs` (`dict(raw)`); `merge` keeps untouched entries, rebuilds a re-failed one without it | same |
| `settings.json`, `restart-state.json`, `engines\yt-dlp.json`, `downloads` | unchanged by 1.8.0 | unchanged | unchanged |
| `jobs.data.options` after 1.8.0 read-modify-writes (`mode` = nearest 1.5.1 mode, `mode_v2` = the real one) | `mode` is one of its own; `mode_v2` ignored (resume works) | `read_options` restores the real mode | same |

After a rollback and a return, 1.8.0 shows the rows the old version wrote through the legacy reader; in zh exactly
as stored. An old writer that stored the same text with another origin leaves a misleading entry (L-13, en only).

1.8.0 itself as a rollback target (from a later version that adds keys): rows decode, entries with keys 1.8.0 does not
know are rendered through the lift or as stored text and are written back verbatim by every 1.8.0 write (4.4 step 3,
E1b, I11), so returning to the later version loses nothing. The key registry (3.6) keeps the reverse direction safe.

### 5.2 How it was checked, and the permanent checks

Evidence from the design phase (non-normative, Appendix C): compat-first ran the real 1.5.1 / 1.6.0 / 1.7.0
`Library`, `Archive`, `unfinished`, `engine.load_failed_job` / `store_failed_job` readers **and** writers and their
history-note / subscription-note methods (stub widgets) on 1.8.0-shaped data: no failure, normal display, sidecars
kept or correctly orphaned. clean-model showed the same and that messages *inside* the old fields crash every old
version. minimal-change's 1.5.1 failed-list check read nothing (wrong state directory); the permanent test below
must not repeat that mistake.

Permanent tests (release gate, they run in every `run_tests.py` run):

- `test_messages_download.py` (WP-2): 1.8.0 writes a failed list with messages, `Raw` errors and sidecars; the
  1.6.0 and 1.7.0 `unfinished` modules (imported from `release-sources` under other names, state redirected) load,
  merge and save it; 1.5.1 `engine.load_failed_job` (the existing `old_engine(state)` pattern, with **its** state
  folder) returns the expected job, and its `store_failed_job` output is read back by 1.8.0. Assertions: no
  exception, old fields unchanged, `msgs` kept on untouched entries and absent on rebuilt ones, 1.8.0 decodes all.
- `test_rollback_readers.py` (WP-3): 1.8.0 fills a sandbox library (every record kind of 4.3, every value type);
  each old version, in a child process whose import path is its `release-sources/<v>/src`, runs its readers
  (`history`, `job`, `subscriptions`, `pending`, `archives`, `contexts`; 1.6.0 / 1.7.0 `history_title`,
  `history_note_text`, `subscription_row`; 1.5.1 `LibraryUI.history_selection` / `subscription_selection` with stub
  widgets) and its writers (`change_job`, `collection_event`, `download_event` item_done / done / title,
  `finish_subscription`, `configure_subscription`, `archive_index`, `Archive.prepare` / `save` / `media`).
  Assertions: no exception; every displayed text equals what the same version shows for the same data written by
  1.7.0; old fields byte-identical; `msgs` survives RMW writes and is orphaned exactly where the text changed;
  1.8.0 then decodes every row (orphans ignored, changed texts lifted). It also contains the 1.6.0-only-format job of
  3.4 after 1.8.0's read-modify-writes (1.5.1 `LibraryUI.restore_download_options` with stub widgets accepts the
  stored options — its `MODES.index(raw['mode'])` raises on a mode 1.5.1 does not know, which is what a lost
  `stored_options` form would cause; 1.6.0 / 1.7.0 `job()` return the real mode) and a row whose sidecar holds
  foreign entries (old versions ignore them; 1.8.0 keeps them).
- The install / rollback rehearsal (11.5) repeats this with the packaged programs on a copy of the owner's data.

### 5.3 Installer and chain

`install_release.py` is unchanged. Installing 1.8.0 over the onedir 1.7.0 moves 1.7.0 to `_previous`; the first
`--rollback` returns to 1.7.0, the next one to the one-file 1.5.1 backup exe (the receipt chain). 1.6.0 compatibility
is still verified (owners may keep a 1.6.0 build) but is no longer an installer step.

---------------------------------------------------------------------------------------------------

## 6. UI rendering

### 6.1 Rules

- Every widget text, dialog text, log line, hint, table cell, export or archive file text that may hold a value
  from the core or the store is `show(value)` (exceptions: `error_text(error)`); UI captions stay `t()`; stored enum
  values stay `label()`; compositions with ` · ` join shown parts (unchanged).
- `show()` / `error_text()` take values from the core or the store only, never the output of `t()` / `label()`
  (that is already in the running language). `i18n.t()` parameters are always already shown: a `Message`, `Value`,
  `Raw` or exception object passed to `t()` is a defect (G2 records it, G5 rejects it statically). An exception is
  shown with `error_text(error)`, never `str(error)` and never as the exception object.
- Comparisons stay value comparisons: `== engine.NETWORK_REASON`, `== engine.STALE_REASON`,
  `last_reason['error'] == errors[-1]`, `PLACEHOLDER_TITLE.fullmatch(zh(title))`, `version == engine_version()`.
- Search strings use `zh(x)` and `show(x)` explicitly (zh: the 1.7.0 string; en: stored and shown text).

### 6.2 The 52 `localize()` call lines (1.7.0 lines)

| file | lines | replacement |
|---|---|---|
| `studio.py` (WP-4) | 244, 357 | `show(engine_version())` |
| `ui_download.py` (WP-4) | 574, 1370, 1421, 1519, 1570, 1586, 1632, 1686, 1807 | `error_text(error)` |
| | 1347 `row_reason` | `error = show(model.get('error') or '')`; containment check unchanged |
| | 1953 `render_wait` | `reason = show(self.wait_context['reason'])` |
| | 2168 `on_item_done` | `reason = show(canonical) if canonical else t('download.detail.see_log')` |
| `ui_log.py` (WP-4) | 151 `append_log` | `line = messages.show(line)` |
| `ui_settings.py` (WP-5) | 97 `shown_engine_version` | `return messages.show(engine_version() if version is None else version)` |
| | 497, 566 | `error_text(error)` |
| | 576, 600 | `show(value)` |
| `collection_ui.py` (WP-5) | 319, 370, 373, 495, 514 | `error_text(error)` |
| | 446, 468 | `show(value)` |
| | 449, 462 | `title=show(value['title'])` |
| | 534 | `term not in (item['url'] + ' ' + zh(item['title']) + ' ' + show(item['title'])).casefold()` |
| | 543, 639 | `show(item['title'])`, `title=show(p['title'])` |
| `library_ui.py` (WP-5) | 330, 333, 335 `history_title` | see 6.3 |
| | 360, 1148, 1168 (name) | `show(row['name'])` |
| | 423, 652 | `show(self.library_warning)` |
| | 869 `history_note_text` | `error = show(error)` (the value is the decoded reason, error or `explain_error` result) |
| | 935, 1066, 1169, 1171, 1268, 1332 | `error_text(error)` |
| | 1103 | `show(item['title'])` |
| | 1112 | `show(data['last_error'])` |
| | 1283, 1288 | `shown = show(r['title'])`; search `zh(r['title'])` plus `' ' + shown` when different (the 1.7.0 rule); cell `show(row['title'])` |
| `library.py` (WP-3) | 246 `_shown_title` (used 825, 857) | deleted; `show(title)` at the two uses |

### 6.2b Display sites 1.7.0 left without a wrapper

Found while revising the guards (an AST scan of every `except ... as error` body in the UI modules); G5 and the
`t()` hook reject each of them, so they are converted with the module. zh output is unchanged (for OS / sqlite
errors `error_text(error)` in zh is exactly `str(error)`).

| file | 1.7.0 line | 1.7.0 expression | 1.8.0 |
|---|---|---|---|
| `library_ui.py` (WP-5) | 639, 787 | `i18n.t('library.unreadable', error=error)` (exception object) | `error=error_text(error)` |
| `ui_settings.py` (WP-5) | 414 | `i18n.t('dialog.restart_failed.text', error=error)` (exception object) | `error=error_text(error)` |
| `studio.py` (WP-4) | 509 | `messagebox.showerror(..., str(error), ...)` | `error_text(error)` |
| `ui_download.py` (WP-4) | 1311 | same | `error_text(error)` |
| `ui_log.py` (WP-4) | 195 | `messagebox.showwarning(..., str(error), ...)` | `error_text(error)` |
| `collection_ui.py` (WP-5) | 622, 643 | `messagebox.showerror(..., str(error), ...)` | `error_text(error)` |
| `library_ui.py` (WP-5) | 1306 | same | `error_text(error)` |

### 6.3 Other UI changes

- `library_ui.history_title`: the structure stays (zh parity): collect job → `show(title)`; titled → `show(title)`;
  untitled and `PLACEHOLDER_TITLE.fullmatch(zh(title))` and links → the first link (or
  `t('value.job_title.multi', title=display(urls[0]), count=len(urls))`) exactly as 1.7.0; otherwise `show(title)`.
  The key filters are gone because `decode` applied the CR2-2 context rule (L6).
- `ui_download.on_title` shows `show(value)`; `studio` badge `f'yt-dlp {show(engine_version())}'`.
- `ui_download.history_value` stores `PROXY_REASON` (now a message) as before.
- `library_ui.TAB_*` and `collection_ui.WEB_LINK` keep their values via `i18n.all_values(key)[0]` (they are ids and
  a compared stored value, not messages).
- UI-raised errors use `msg()` (D-F40, D-F41); `report_error` uses `error_text`.
- `ui_log.classify` is unchanged: it classifies the shown line with the 1.7.0 starts and `i18n.prefixes`
  (catalog-driven, no per-text list), so zh classifies exactly as 1.7.0.

### 6.4 Code that is deleted (exact)

| where | what (1.7.0 lines) | package |
|---|---|---|
| `collection_ui.py` | `PAGE_TITLE_KEYS` + comment (56-61); `stored_text` import (29); `subscription_result` join (44-45) | WP-5 |
| `library_ui.py` | `JOB_TITLE_KEYS` (dead), `NAME_KEYS`, `TITLED_JOB_KEYS`, `UNTITLED_JOB_KEYS`, `COLLECT_JOB_KEYS`, `PAGE_TITLE_KEYS` + comments (64-79); `stored_text` (82-86) | WP-5 |
| `library.py` | `PAGE_TITLE_KEYS`, `_shown_title` + comment (238-246) | WP-3 |
| `ui_download.py` | `stored_text` (186-189) | WP-4 |
| `ui_settings.py` | the `localize` body of `shown_engine_version` (97) | WP-5 |
| `i18n.py` | `MAX_DEPTH` (49); `_Template`, `_Index`, `_index`, `_get_index`, `_allowed`, `_localized`, `_localize_in`, `localize` (336-468); imports that become unused | WP-6 |
| `i18n_maps.py` | `LOCALIZE`, `VARIANTS`, `NUMERIC`, `RECURSE` and their docstring lines (109-284), by changing the generator | WP-6 |
| `verification/test_i18n_catalog.py` | `LocalizeTests` (test 6 template occurrence, test 7 round trips / 9.2 cases / 1.5.1 collector forms, test 9 timing) move to `test_messages.py` as legacy-reader tests | WP-1 (move) / WP-6 (delete the live-`localize` variants) |
| `verification/test_i18n_pages.py` | the `PAGE_TITLE_KEYS` equality test (745-746) | WP-5 |
| `verification/i18n_tool.py` | the name-guessing suspects scan: `_STORED_NAMES`, `_WRAPPERS`, `_is_wrapped`, `_suspect_expression`, `suspects`, `unexplained_suspects` (734-790) and `SUSPECT_ALLOW`; replaced by the G5 sink rule (7.9) and the per-module allow files | WP-1 |

Measured result: 52 → 0 `localize` call lines; 8 → 0 key-prefix lists; about 173 lines of reverse maps and 134
lines of reverse code leave the live `i18n` path (they reappear, frozen and generated, only behind
`messages.decode`). Code does not shrink much; the maintenance does: a new core text needs one catalog pair and one
`msg()` call, and the guards fail when that is forgotten.

### 6.5 What stays

`t`, `label`, `choices`, `GROUPS` (including `媒体直链` / `动态页面媒体请求` in `link_kind` for the Type column width),
`all_values` (window-title search, ids), `prefixes` + `ui_log.classify`, `sep`, `join_sentences` /
`_join_sentences` (en rendering of clauses), `PLACEHOLDER_TITLE`, the UI H4 constants.

---------------------------------------------------------------------------------------------------

## 7. Guards

The defect class: an app-made text reaches the screen (or the store) without its key, so en shows Chinese. Each
guard below needs nobody to think of the text, and each has a self-test on the frozen old sources.

### 7.1 Overview

| guard | kind | file (package) | catches |
|---|---|---|---|
| G1 | static, all `src` | `test_text_guard.py` (WP-1, final WP-6) | CJK literals outside the per-module allow files; English raise texts; bad keys / placeholders; boundary breaches |
| G1b | static slot rule | same | an allow-listed enum / H4 / file / internal literal — or a name, attribute or loop variable bound to one, in this module or through an enum source of another — in a text slot: the exact `媒体直链` mechanism |
| G1f | static composed-text rule | same | an f-string, concatenation, `%`, `.join`, `.format`, `re.sub`, `.replace` or slice standing in a text slot of a core module (the flattening operations no tripwire sees) |
| G2 | runtime, every test process | `messages.py` hooks + `run_tests.py` | recognisable app text (every core / value / reason text and every `GROUPS` value) arriving as plain text at a core emit, a store write, `show()` / `error_text()` or an `i18n.t()` parameter; a `Message` / `Value` / `Raw` / exception passed to `i18n.t()`; flattening method calls (tripwires) |
| G3 | static + unit, frozen old sources | `test_legacy_texts.py` (WP-1) | any text an old version could have stored that the legacy reader cannot lift |
| G4 | runtime matrix | `test_messages_ui_download.py` (WP-4), `test_messages_ui.py` (WP-5), `test_messages_store.py` (WP-3) | a channel that does not render a key in en, or differs from `zh(msg)` in zh |
| G4b / G6 | runtime replay | `test_english_replay.py` (WP-6) | computed legacy texts (cuts, the cookies echo) and any page that shows Chinese for old data in en; zh pages different from 1.7.0 |
| G5 | static, positive sink rule (7.9) | `test_text_guard.py` + `i18n_tool.sinks` (WP-1, applied per ready UI module) | any argument of a UI sink (widget text, dialog, hint, log line, table cell, `t()` parameter, file text) that is not provably display text |
| H3 guard | static AST | `test_protected_blocks.py` (WP-2) | any change in the protected blocks beyond S1–S7 |

Where each guard looks, by the path a text takes: producer (G1, G1b, G1f static; G2 emit hook and tripwires at run
time) → store (G2 `encode` hook; G3 for what old versions stored) → UI boundary (G5 static; G2 `show()` /
`error_text()` / `t()` hooks; the `ui_theme` display tripwire after WP-6) → screen (G4 / G4b / the en snapshot). A
value that skips `show()` on its way to a widget is caught at the boundary statically (G5) and, on every tested
path, at run time (the `t()` hook, the display tripwire, G4).

### 7.2 G1 (static, `verification/test_text_guard.py`)

AST scan (`i18n_tool.scan`, docstrings excluded, f-strings as one node, adjacent literals as one) of every
`src/*.py` except the catalogs (`i18n_core`, `i18n_common`, `i18n_shell`, `i18n_download`, `i18n_log`,
`i18n_settings`, `i18n_collect`, `i18n_library`), `i18n_maps.py` (GROUPS data, checked by G1b at its uses) and
`i18n_legacy_data.py` (generated, checked by its regeneration test).

Allow files: one per `src` module, `verification/golden/text-allow/<module>.json` (so parallel packages never share
one, 10.2), with the sections `literals` (`{"<literal>": {"category": ..., "reason": ...}}`), `raises` (G1e),
`composed` (G1f), `sinks` (G5) and `pending_keys` (10.2). Every entry has a reason; the release gate (WP-6) fails on
any `pending_keys` entry and on an entry that no longer matches anything (stale).

- **G1a** A CJK literal fails unless it is (i) the first argument of a logging call (`logfile`), or (ii) listed in
  its module's allow file with category `stored` (stored / compared enum value), `file` (D10 on-disk name), `h4` (UI
  constant pinned by ParityTests to a **UI** key; a pinned constant whose key is `core.*`, `value.*` or
  `download.reason.*` fails, because such a text is stored), `h4-unused` (a constant kept only because a test pins
  its value: the scanner checks that no `src` module refers to the name outside its own definition, so it can be
  neither stored nor shown; the one 1.7.0 case is `ui_download.REASON_WORDS`, pinned to `download.reason.failed` /
  `download.reason.blocked` while the page shows `REASON_KEYS`), `internal` (self-test texts, regexes such as
  `PLACEHOLDER_TITLE`, character classes, the `ui_log` zh starts), `token` (`'[代理]'` and `'；'` in
  `messages.py`, `i18n.py` and `i18n_legacy.py` only: L5 splits on `'；'`) or `pending` (a producer left unconverted
  for a missing key, 10.2; allowed between integrations, fails the release gate). There is **no** automatic allowance
  for `GROUPS` values (that allowance let `媒体直链` pass 1.7.0's test 6).
- **G1b** An allow-listed literal must not stand in a text slot, neither directly nor through a binding. Text
  slots: a dict value under a key in `TEXT_KEYS = {'title', 'name', 'label', 'error', 'reason', 'message', 'text',
  'last_error', 'page_title', 'detail'}`; a keyword argument with such a name; a positional argument of a function
  whose parameter has such a name (same module, or a `src` function called through its module name); an argument
  of `emit(...)` / `self.emit(...)` / `say(...)` / `log(...)`, of `msg(...)` (a param) and of a store writer
  (`create_job`, `rename_job`, `change_job`, `download_event`, `collection_event`, `finish_subscription`,
  `subscription`, `archive_index`, `unfinished.merge` / `save`); an argument of a `raise`. Bindings followed (one
  hop, transitively through further hops of the same kinds): a module-level name; a local name of the same function
  (assignment, augmented assignment, `for` target, comprehension target, `with ... as`); an **enum source**: any
  module-level name of any `src` module whose value contains an allow-listed literal (computed by the scanner, e.g.
  `formats.PRESETS`, `formats.MODES`, `formats.LEGACY_MODES`, `formats.BEST`, `engine.RESOLUTIONS`, `naming.LABELS`,
  `collection_ui.WEB_LINK`, `library_ui.TAB_*`), reached as `module.NAME`, `NAME[...]`, `NAME.get(...)`, iteration
  over it, or a call into `formats` returning one of its records (`by_mode`, `by_id`, `by_label`, `by_key`); and the
  attributes `.label` / `.mode` of any such record. This rejects `{'title': '媒体直链'}`, `add(url,
  '动态页面媒体请求', True)` (parameter `label`), `kind = '媒体地址'; page['title'] = kind`, and
  `msg('core.engine.x', format=preset.label)` (must be `value('format', preset.label)`).
- **G1c** Every `msg(<key>, **kw)` / `messages.msg(...)` names keys present in the zh and en catalogs (or
  `common.clauses` inside `messages.py`) and passes exactly their placeholders. `<key>` is a string literal or a
  conditional expression whose branches are such literals (recursively), each branch checked (C-F39 uses
  `msg('value.subscription.stopped' if cancelled else 'value.subscription.unfinished')`); anything else fails.
- **G1d** Boundaries: `i18n_legacy` is imported only by `messages.py`; `messages.decode` / `decode_columns` /
  `encode` are called only by `library.py` and `unfinished.py`; `from i18n import ...` is not used in `src` (the
  `t()` hook replaces the module attribute, 7.3); a core module (`engine`, `unfinished`, `naming`, `urls`, `formats`,
  `collector`, `library`, `updater`) uses no `i18n` attribute at all — no `t`, `label`, `choices`, `sep`,
  `all_values`, `prefixes`, `language`, `localize`, `join_sentences`, each of which returns running-language text that
  could be stored — except `library`, which may call `i18n.t` with a literal `filetext.*` key (archive files are
  written in the running language, I18N.md 8.8) and `messages.show` for its parameters; a ready module (7.8) neither
  defines nor calls `localize`, and from WP-6 on no `src` module does. Before WP-6, the number of `localize` call
  lines may only decrease (baseline 52).
- **G1e** `raise X(<non-CJK literal or f-string>)` fails unless its module's allow file lists it under `raises`
  (never shown). The 1.7.0 scan (AST, measured during the revision) finds 26: the **8** that become messages
  (engine 178, 184, 188, 190, 425; formats 75, 82, 89 — D-F38, Appendix A) and **18** internal ones: `ui_theme` 8
  (155, 185, 505, 632, 638, 708, 898, 1391), `settings` 3 (61, 146, 167), `library_ui` 2 (152 `require_job`'s
  `LookupError`, caught by the history writer and logged to download.log only; 1036 `resume_options`' `ValueError('no
  options')`, caught at 955 where a fixed `t()` dialog is shown instead), `i18n` 164, `procutil` 450, `library` 805
  (caught in `Archive.prepare`), `updater` 88, `engine` 2129. WP-1 lists the 18 in the allow files of their modules
  (the two of `library_ui` included, so WP-5 meets no surprise).
- **G1f** In a core module (and in the arguments of `raise`, emits and store writers anywhere), an expression in a
  text slot (G1b) that composes text fails: an f-string with at least one hole, `+` with a non-numeric operand, `%`
  formatting, `.join(...)`, `.format(...)` / `.format_map(...)`, `re.sub(...)`, `.replace(...)`, a slice, or
  `str(...)` of anything but an exception. Accepted instead: the argument of `msg`, `raw`, `cut`, `masked`,
  `stripped`, `clauses`, `message_of` (each produces a typed value, and `raw(...)` is an explicit claim checked at run
  time by G2's `Raw` rule), or an entry in the module's `composed` allow section with a reason. This is the static
  side of the operations no tripwire sees (2.3).
- **G1 self-tests** (`test_text_guard.py`, on `release-sources/1.7.0/src` with the 1.8.0 allow files): G1a must flag
  `collector.py` 122 `网页`, 156 `动态页面媒体请求`, 203 and 204 `媒体直链`; with `媒体直链` and `动态页面媒体请求`
  allow-listed as `stored` (what 1.7.0's GROUPS allowance amounted to), G1b must still flag the `'title'` dict slots
  at 203-204 and the `add(..., label)` call at 156. Synthetic modules (in the test, written to the sandbox) must be
  flagged for: a local `kind = '媒体地址'` put under `'title'`; `msg('core.engine.error', error=preset.label)` with
  `preset` from `for preset in formats.PRESETS`; `f'{m}：{x}'` under `'error'` in a core module; `msg(key_variable)`;
  `i18n.label(...)` in a core module; and must **not** be flagged for the C-F39 conditional key.

### 7.3 G2 (runtime sentinel) and tripwires

Switch: `run_tests.py` sets `YTDLP_DESKTOP_MSG_GUARD=1` for every test file except `GUARD_OFF = {'test_golden_ui.py':
'replays recorded 1.5.1 / 1.6.0 events with plain strings', 'test_golden_runonce.py': 'byte-identical golden test;
its FakeArchive raises the 1.5.1 plain text that becomes a raw param'}`. A file is added to `GUARD_OFF` only by a
spec change. With the variable unset nothing below runs.

Checked points and fields (each hook is installed by `messages` when the variable is set, 2.2):

| hook | what is checked | rule |
|---|---|---|
| `guarded(emit)` in `Runner`, `EngineUpdater`, `CollectionRunner`, `collector.worker_main` | `log`; `waiting.reason`; `item_done.reason` / `.error`; `collect_status`; `collect_error`; `collect_page` `title` and `links[].title`; `update_status`; `update_error` — each value and its nested message params | R-text |
| `encode` (store writes requested by a `src` module) | every text field of 4.3 and its nested params | R-text |
| `show(value)` and `error_text(error)` | a plain `str` value (for `error_text`: the text `message_of` returns when it is plain), and every plain nested param of a message being rendered | R-text (plain only, with the UI-text exclusion) |
| `i18n.t(key, **params)` — `messages` replaces the module attribute `i18n.t` with a checking wrapper (UI and `library` call it as `i18n.t`, G1d) | every param | R-param; in en also R-text (plain only, with the UI-text exclusion) |
| tripwires (2.9) | any flattening method call on a `Message` or `Value` | always a violation |

Rules:

- **R-text** — a value that is a plain `str` (or, at the emit and `encode` hooks, a `Raw`) and is **recognisable
  app text**: `i18n_legacy.recognise(text)` finds (a) an exact or template match among the frozen table, the live zh
  catalog of `core.*`, `value.*` and `download.reason.*` keys, and **every CJK value of `i18n_maps.GROUPS`, whatever
  its group or key range** (`mode`, `format` preset labels such as `MKV · 保留源画质`, `resolution` `最佳`,
  `link_kind`, `collect_mode`, `collect_filter`, `library_tab`): a stored enum value must arrive as a `Value` (shown
  through `label()`) or inside a `value.*` message, never as text; (b) the same after removing a leading `ERROR: ` /
  `WARNING: `; (c) a prefix cut at 70 / 200 / 300 / 900 (L3 conditions); or (d) a run of ≥ 6 consecutive CJK
  characters that occurs in the literal text of such a catalog entry or `GROUPS` value. `done.archive_errors` and media
  titles in events are not checked (never shown / user data). At `show()` / `error_text()` a recorded value is a
  flattened message, an unconverted producer, or a G3 gap, reported at the place where the defect would show.
- **UI-text exclusion** (only at `show()`, `error_text()` and `t()`): a text that is a whole instance of the zh
  template of a key outside the recognised ranges (a UI caption, log line or dialog text that `t()` produced) whose
  template has at least four literal CJK characters is not recorded. The log page passes UI lines and core lines
  through the same `show()` (D-F47), and some UI texts share wording with core texts.
- **R-param** — a param of `i18n.t` that is a `Message`, a `Value`, a `Raw` or an exception object: in 1.8.0 every
  `t()` param arrives already shown (6.1). This is the rule that catches `t('library.unreadable', error=error)` with
  an exception (6.2b) and `t(..., title=page['title'])` without `show()`; the static side is G5.

Attribution. Each hook finds a **producer frame** with the walk of 2.9 (skipping `TRANSIT`, standard-library and
third-party frames; ending at a `verification/` frame, a Tk callback boundary or the stack bottom without a
producer): for an emit hook from the emit call; for `show()` / `error_text()` / `t()` from their caller; for the
tripwires from the flattening call. For `encode` the walk starts at `encode` and also skips `library.py` and
`unfinished.py`: the first remaining `src` or `verification/` frame is the **requester**; when it is a
`verification/` file the write is exempt (tests seed legacy rows on purpose), otherwise the requester's module is
the producer. A violation is recorded only when the producer's module is guarded (7.8), so a 1.7.0 module that is
not converted yet is never reported.
Exemptions: code inside `messages.exempt('<reason>')` (fixtures whose user data resembles app text;
`_sandbox.fixture_texts(reason)` is the test-side name; the census legs that feed plain / `Raw` text on purpose, 7.5;
the D-H-4 regression line). Every exemption is counted in the per-file report, so it is visible in review.
Library's own compositions called directly from tests are checked by the type assertions of
`test_messages_store.py`.

G2 self-tests (`test_messages.py`, WP-1): each must be recorded — a plain `媒体直链` title through a guarded emit and
through `encode`; a plain `MKV · 保留源画质` as a nested `msg()` param at a guarded emit; `show('媒体直链')` and
`error_text(ValueError('找不到任务记录。'))` from a guarded module; `t('library.unreadable', error=ValueError(...))`
and `t(..., title=msg(...))` from a guarded module; a slice of a message in a guarded module; a slice of a message
inside an `i18n` function called from a guarded module. Each must **not** be recorded — the same calls from a module
that is not guarded; a slice inside `i18n` called from a module that is not guarded (it must also not raise);
`show()` of a UI log line made by `t()` in zh, as `append_log` does (UI-text exclusion); `ui_theme`'s
`Treeview._cut_values` on a message with `ui_theme` in `TRANSIT` and no guarded caller. With `TRANSIT` patched to the
WP-6 set, the last case must be recorded with producer `ui_theme`.

Reporting: each violation is appended as one JSON line to `%VERIFY_OUT%\msg-guard-<pid>.jsonl`: `{"rule", "where"
(hook and field), "producer" (nearest guarded src frame, file:line), "key" (recognised), "text" (first 80
characters), "test"}`. `run_tests.py` reads every `msg-guard-*.jsonl` of a file's sandbox after the file ends; any
line marks the file **failed** with the first five violations in its tail; the run report gets `msg_guard:
{files_checked, violations, exemptions}`. A file is used rather than an exception so that the core's broad
`except` blocks (history writer, archive, merge) cannot hide a violation.

### 7.4 G3 legacy closure (`verification/test_legacy_texts.py`, `verification/golden/legacy-corpus.json`)

`PY verification\i18n_tool.py corpus` extracts mechanically from `release-sources/{1.5.1,1.6.0,1.7.0}/src` (producer
modules `collector`, `library`, `engine`, `unfinished`, `updater`, `naming`, `urls`, `formats`, `studio`
(1.5.1 placeholder), `library_ui`, `collection_ui`, `ui_download`): every CJK literal, every f-string (holes named by
expression), every `'<CJK>' + expr` concatenation, and every argument expression of the store-writing calls
(`create_job`, `rename_job`, `change_job`, `finish_subscription`, `subscription`, `collection_event`,
`download_event`, `archive_index`, `unfinished.merge` / `save`, `store_failed_job`) following one assignment hop
(finds `{n}条 · {mode}`, `'订阅：' + name`, the `；` join). Shapes that a release builds **from catalog keys** rather
than literals are resolved to that release's own zh catalog template (read from its `release-sources/<v>/src`
catalog modules): `stored_text('<key>', ...)` (1.7.0 `ui_download.py:186`, `library_ui.py:82`; used for
`value.job_title.placeholder` at `ui_download.py:1579`, `value.job_title.subscription` at `library_ui.py:1143`,
`value.subscription.stopped` / `.unfinished` at `collection_ui.py:45`), `i18n.all_values('<key>')[0]` with or
without `.format(...)` / `.join(...)` (`collection_ui.py:44`'s clause joiner), `i18n.t('<key>', ...)` and
`i18n.CATALOG['zh']['<key>']`; a key given as a conditional expression of literals yields one shape per branch. A
call whose key is not a literal (or such a conditional) fails the extraction, so a future indirection cannot hide a
stored shape. Each shape has one category in the corpus, decided once in WP-1 with a reason: `message`, `title`,
`name`, `job_title.collect` / `.titled` / `.untitled`, or `not_stored` (`ui`, `log`, `file`, `internal`, `enum`).
The test:

1. re-extracts and fails on any shape missing from the corpus or any corpus shape not extracted;
2. for every `message` shape, instantiated with samples (digits for `NUMERIC` names, `Qq` otherwise, a sample
   nested message for `RECURSE` names): `lift('error' | 'reason' | 'clauses', sample)` returns a key whose en
   rendering has no CJK outside the samples and whose decoded zh value equals the sample; also for the transforms
   ERROR-strip, cuts at 70 / 200 / 900 of long samples, a proxy token inside a raw capture, and every pair of
   message shapes joined with `；` (kind `clauses`);
3. for every `title` / `name` / `job_title` shape: lifted by its kind; and `TITLE_KEYS` equals the set of title
   shapes;
4. pins the corpus SHA-256 and the release-source hashes.

Self-test (in the same file, on the real old sources): with the frozen `VARIANTS` removed the closure flags exactly
the ten 1.5.1 collector forms without spaces and the 1.5.1 placeholder `{count}条 · {mode}`; with the title kind
restricted to the 1.7.0 first-round filter `('value.page_title.',)` it flags exactly `媒体直链` and
`动态页面媒体请求`. (compat-first measured 415 shapes, clean-model 338 literals with 33 legitimate exemptions;
these counts are expectations, not requirements.)

### 7.5 G4 channel × key matrix and consumer census

For every `core.*` / `value.*` / `download.reason.*` key (sample params; nested where a param is a message) and every
channel of its side, feed the message **live** and as a **stored record with a sidecar**, and assert: en widget text
has no CJK outside raw params (samples are ASCII); zh widget text equals `zh(msg)`. Channels: log page and
download.log line; waiting metrics and row detail; item row detail and reason dialog; unfinished row and reason
dialog (after save / load); dialogs and the hint; collect status / log / table / export; history title and note;
subscription name / note / pending table / hint / log; archive table, search and files; update status / error;
engine badge and `log.ready`. No channel has a key list of its own, so none can miss one.

Consumer census: every event kind `StudioApp.handle` routes that carries text (`log`, `waiting`, `item_done`,
`title`, `collect_status`, `collect_error`, `collect_page`, `update_status`, `update_error`) and
`library.download_event` / `collection_event` are fed the same text as a `Message`, a `Raw` and a plain `str`: no
exception; zh widget state and stored bytes identical for the three; en: the message renders its key, the other two
are shown as they are. The `Raw` and plain legs run inside `messages.exempt('census: text from an unconverted
producer')` (they imitate the defect on purpose).

Regression cases (WP-2 / WP-4 / WP-5): D-H-1 cookies echo (row detail, reason dialog, history note in en);
D-H-2 `core.cookies.not_netscape` and `core.cookies.missing` with a 200-character path (row reason, stored reason
bytes = the 1.7.0 70-character cut, en full text); D-H-3 English texts keyed, zh unchanged; D-H-4 a raw line
`ERROR: [generic] x: 链接不存在（404）；网站拒绝访问（403）` passes unchanged in en (inside `exempt`: engine output that
quotes app text is recognisable by design); C-H-5 a `last_error` pair with `needs_chrome` in en (new and legacy rows);
the 6.2b sites (an exception with a message in `library.unreadable`, `dialog.restart_failed.text` and the OS-error
dialogs) in en.

### 7.6 G4b English replay and G6 zh dump (`verification/test_english_replay.py`, WP-6)

In the sandbox, run the frozen producers of 1.5.1, 1.6.0 and 1.7.0 (each in a child process importing its
`release-sources/<v>/src`, `-B`, sandbox environment): their `Library` writes; `collector.collect()` with a fake
`Fetcher` for a normal page, a page whose title is empty, a media start page and a dynamic capture; the
`CollectOptions.validate` errors; subscription checks with the version's own composition (`subscription_result` or
the inline join, `'订阅：' + name`, the placeholder title); a `Runner` (1.6.0, 1.7.0) on scripted output with an
engine error, a cookies echo, an item that cannot start (missing cookies file with a long path), a 429 run-out and
a bot check; the failed list. User data is ASCII. Then the 1.8.0 code opens the result **in English**, decodes every
record and renders every page model (history list and notes, subscription rows and notes, pending table, collect
table, archive table and files, unfinished rows and reason dialog): **no CJK may remain**.

G6: the same data rendered in zh by the frozen 1.7.0 page functions (`history_title`, `history_note_text`,
`subscription_row`, the subscription note, collect / archive / pending / unfinished row models) and by 1.8.0 must be
identical; then the same scenarios are produced by 1.8.0 itself and rendered by both 1.7.0 and 1.8.0 in zh:
identical again (zh parity for new rows, and proof that 1.7.0 reads 1.8.0 rows as its own).

En snapshot (WP-6): `verification/snapshot_ui.py` gets assertion **(13)**, run only with `--language en` / `both`:
every text of every mapped widget of every captured page and state (labels, buttons, entries' placeholders, option
menus, segments, table headings and cells, textboxes, the log page) contains no CJK character, except texts that
are, or contain, a user-data string of the snapshot fixtures (the fixtures' own titles and paths, collected by
`snapshot_ui` from its fixture data). Assertions (9)–(12) keep their 1.7.0 behaviour; the gate compares their
failures with a baseline run of the same command on the 1.7.0 commit (`git archive 07c9603` extracted to
`build\v180\baseline-1.7.0\源码` with `vendor` copied in, same machine, same sandbox) and fails on any failure the
baseline does not have. The failures caused by the carried 1.7.0 minors (D17) appear in both runs and are listed as
known in the WP-6 handoff.

### 7.7 H3 AST guard (`verification/test_protected_blocks.py`, WP-2)

Parse `src/engine.py` and `release-sources/1.7.0/src/engine.py`; select (1) the function `Runner._run_once`, (2)
the `try` statement inside `Runner.download`'s `finally` whose body calls `unfinished.merge`, (3) the `while` loop of
`Runner._cooled`. In the 1.7.0 tree locate the sanctioned nodes S1–S7 by their literal content; in the 1.8.0 tree
take the nodes at the same AST paths; replace both by a placeholder and require equal `ast.dump(...,
include_attributes=False)`. Then require each 1.8.0 sanctioned node to dump equal to its expected expression (9.2)
and its zh value, evaluated with sample values, to equal the 1.7.0 literal's. The `set_busy` / `poll` SHA-256
pins (`test_i18n_download.py:37`) stay unchanged. Self-test: changing one condition in `_run_once` of a copy makes
the guard report a difference.

### 7.8 Staging (why the tree stays green between packages)

Each converted module declares `MESSAGES_READY = True` at module level (temporary). Four sets decide what applies
where (the first in `test_text_guard.py`, the others in `messages.py`):

| set | members | effect |
|---|---|---|
| `STATIC_ALWAYS` | `app`, `applog`, `paths`, `procutil`, `progress`, `settings`, `ui_theme`, `messages`, `i18n`, `i18n_legacy` | G1, G1b, G1e, G1f apply from WP-1 on (their literals and raises are pre-filled in their allow files by WP-1) |
| ready modules | modules with `MESSAGES_READY = True` | G1 family and G5 apply; guarded at run time |
| `RUNTIME_ALWAYS` | `app`, `applog`, `paths`, `procutil`, `progress`, `settings` | guarded at run time from WP-1 on (they handle no message today; a record there is a real finding) |
| `TRANSIT` | `messages`, `i18n_legacy`, `i18n`; plus `ui_theme` until WP-6 (`TRANSIT_UNTIL_WP6 = {'ui_theme'}`) | skipped when a runtime hook or tripwire looks for the producer frame (2.9, 7.3) |

A runtime check records a violation only when its producer frame belongs to a ready module or to `RUNTIME_ALWAYS`
(after WP-6: to any `src` module outside `TRANSIT`); otherwise the call behaves exactly like `str`.

Why every package can end green with the guard on. From WP-2 / WP-3 on, the core emits messages into the 1.7.0 UI.
That UI hands them to `i18n.localize` and to `ui_theme` widgets, which do text operations on them — measured in the
1.7.0 code: `i18n._Index.candidates` `text[:1]` (`i18n.py:396`), `_localize_in` `text.split(JOIN)` (439) and
`text.replace('[代理]', ...)` (444) in en; `ui_theme.Treeview._cut_values` `str(value)` (which returns the message)
then `text.replace('\n', ' ')` (`ui_theme.py:1749-1752`) and `_ellipsize`'s slices (357) in zh. Because `i18n` and
`ui_theme` are in `TRANSIT`, each of these is charged to the calling UI module, which is not ready: no record, no
exception, and `localize` keeps working (a message is its zh text, so dict lookups and `re` matching see the text).
Calls from a Tk callback with only `TRANSIT` and Tk frames on the stack have no producer and are not recorded before
WP-6. After WP-4 / WP-5 the UI modules are ready and pass only shown text on. WP-6 removes every `MESSAGES_READY`
line and the gating code and removes `ui_theme` from `TRANSIT`: from then on `ui_theme` is a deliberate display
tripwire (2.9), G1 / G5 / G2 apply to every module unconditionally, and the release gate checks that no flag remains.

### 7.9 G5 — positive UI sink rule (`i18n_tool.sinks`, run by `test_text_guard.py`)

G5 replaces the 1.7.0 suspects scan (which flagged only f-string holes and `t()` keyword values named `record`,
`data`, `entry`, `row`, `value`, `errors`, `.label` / `.mode` and `engine_version()`, and so would not flag a missing
`show(item['title'])` or `error=error`). It proves, statically, that what reaches a sink is display text.

**Sinks** (in the UI modules `studio`, `ui_download`, `ui_log`, `collection_ui`, `library_ui`, `ui_settings`, and
for `library`'s `filetext` calls; the list lives in `i18n_tool.SINKS`, may grow, and shrinks only by a spec change):

- `<any>.configure(text=…)`, the `text=` / `placeholder=` / `placeholder_text=` keyword of a widget constructor
  (`ctk.CTk*`, `tk.*`, `ttk.*`) and of the `ui_theme` factories (`label`, `button`, `chip`, `checkbox`, `switch`,
  `entry`, `option_menu` / `segmented` `labels`, `Chip.set`), `<tree>.heading(..., text=…)`, `title(...)` of a window;
- `ui_theme.fit(label, text)`, `ui_theme.fit_alternatives(label, text, alternatives)`, `Treeview.set_empty(text)`,
  `Treeview.fill(rows)` (the values of each `(iid, values)` pair, not the iid), `<tree>.insert(..., values=…)` /
  `<tree>.item(..., values=…)`, `<textbox>.insert(index, text, ...)` of a log or text box;
- `messagebox.showinfo / showwarning / showerror / askyesno / askokcancel / askyesnocancel(title, message)`;
- every keyword value of `i18n.t(...)` (the result is shown or written, so its params must be shown);
- every positional or keyword argument of a **sink method**: a function or method of the UI modules (the window is
  one class made of mixins, so methods resolve by name across the six modules) whose parameter flows unchanged into a
  sink position — computed by the scanner as a fixed point (e.g. `set_hint(text)` → `ui_theme.fit`,
  `show_library_problem(text)`, `set_collect_status(text, ...)`);
- `append_log(line)` is a **value sink**: it calls `show()` itself (D-F47), so a core value (an event's `log` line)
  may be passed as it is, but a composed argument (f-string, `+`, `%`, `.join`, `.format`) must consist of
  display-text parts. (`studio.report_error` is a Tk error hook, not called by app code; it uses `error_text`, D-F49.)

**Display text** (accepted in a sink position), recursively: a string literal (CJK ones are G1's business) or a
number; a call of `i18n.t`, `i18n.label`, `i18n.sep`, `i18n.choices`, `messages.show`, `messages.error_text` (also
imported bare); a number expression (`len`, `int`, `round`, `math.*`, arithmetic of numbers, an f-string hole with a
numeric format spec); `urltools.display(...)`, `Path(...).name`, `os.fspath(...)`; a subscript, `.get('<key>')` or
attribute whose final key or name is in `NEUTRAL_FIELDS = {'url', 'urls', 'page_url', 'webpage_url', 'path',
'folder', 'output', 'id', 'version', 'uploader', 'source'}` (language-neutral data; the scanner checks that
`NEUTRAL_FIELDS` and `TEXT_KEYS` are disjoint); an f-string, `+`, `%`, `.join`, `.format`, conditional expression or `or` / `and` whose
parts are all display text; a tuple / list / generator of display text (table rows); a local name whose every binding
in the function is display text (one hop, transitively); a call of a function of the UI modules whose every `return`
value is display text (fixed point over the six modules, e.g. `history_title`, `history_row`, `subscription_row`,
`row_reason`, `shown_engine_version`); a parameter of a sink method (checked at its callers instead).

Everything else in a sink position fails unless the module's allow file lists it under `sinks` (`{"<expression as
ast.unparse prints it>": {"line_hint": ..., "reason": ...}}`), e.g. a user-typed search string. Explicitly not
display text: `str(...)` of anything that is not display text (so `str(error)` fails: use `error_text`), an exception
object, `messages.zh(...)` (the zh text is for searches and comparisons, never for a sink), a bare subscript or
attribute of stored / event data (`item['title']`, `row['name']`, `value`), and `engine_version()`.

G5 self-tests: on synthetic modules each of these is flagged — `self.status.configure(text=item['title'])`,
`messagebox.showerror(i18n.t('x'), str(error))`, `tree.fill((row['id'], (row['title'], row['url'])) for row in
rows)`, `i18n.t('library.unreadable', error=error)`, a call `self.set_hint(value)` through a sink method, `f'{x} ·
{y}'` in `configure(text=...)` with `x` from an event — and none of their `show()` / `error_text()` forms. On
`release-sources/1.7.0/src` with `i18n.localize` counted as display text (it was 1.7.0's `show`), G5 must flag
`library_ui.py` 639 and 787 and `ui_settings.py` 414 (`error=error`), and the six `str(error)` dialogs of 6.2b.

---------------------------------------------------------------------------------------------------

## 8. zh parity and golden handling

### 8.1 What stays byte-identical

- `verification/golden/argv-1.5.1.json`, `argv-1.6.0.json`, `events-1.5.1.json`, `events-1.6.0.json`,
  `runonce-1.5.1.json`, `ui-1.5.1.json`, `ui-1.6.0.json`, and `test_golden_argv.py`, `test_golden_events.py`,
  `test_golden_runonce.py`, `test_golden_ui.py`: not edited (review compares SHA-256 with the 1.7.0 commit).
  `json.dumps` writes a message as its text; `copy.deepcopy` returns the message; `_sandbox.normalise` (unchanged)
  turns it into plain text with `.replace`.
- `StudioApp.set_busy` / `poll` source hashes; the protected blocks except S1–S3.
- Every zh catalog text of every key that 1.7.0 has (`test_messages.py` compares the live zh catalog with the
  `release-sources/1.7.0` catalogs key by key).
- Core H4 constants (engine reasons, hints, cookie lines, `NETWORK_REASON`, `PROXY_REASON`, `updater.MSG_*`,
  `BUILTIN`): equal to their 1.7.0 values (ParityTests extended, values read from `release-sources/1.7.0` by AST).
- Stored bytes for the same operation (I1), download.log lines (`append_log` writes the shown line; the zh
  WARNING of `unfinished._report` unchanged; `test_review_state.py:163` unchanged).

### 8.2 Why zh cannot change

`show()` in zh returns the value's own text (the canonical live text or the stored text, 1.5.1 no-space forms and
70-character cuts included), which is what 1.7.0's `localize` returned in zh. No zh path renders a key where 1.7.0
showed text. Comparisons see the same strings. The English-only texts keep English in zh (D13). `classify` is
unchanged. G6 compares whole zh page dumps with the frozen 1.7.0 page functions.

### 8.3 Test files that change (sanctioned edits)

English tests that fed plain zh strings and expected `localize` to translate them (`test_i18n_download.py`,
`test_i18n_pages.py`, `test_i18n_ui.py`, `test_ui_pages.py`, `test_polish_pages.py`, `test_v170_ui.py`,
`test_final_fix_ui.py`) feed messages for live events, seed legacy rows as legacy (plain text written by a test file
is keyless and is lifted), or move to the legacy tests. Tests whose fixtures feed plain core texts through UI paths
that store them use `_sandbox.fixture_texts(reason)`. `test_i18n_catalog.py` test 10 (the suspects scan and
`SUSPECT_ALLOW`) is replaced by G5 in `test_text_guard.py` (WP-1); the `REASON_WORDS` parity pin stays (category
`h4-unused`). Reviewers compare assertion counts per file (11.2); a lower count needs a reason in the handoff.

---------------------------------------------------------------------------------------------------

## 9. Rules kept and relaxed

### 9.1 Table

| rule | status | reason | replacement check |
|---|---|---|---|
| H1 no new dependencies | kept | stdlib only; new modules are plain imports (no `app.spec` change) | release gate file list |
| H2 shared files additive | kept | only the additive `msgs` key; read-modify-writes never touch `options` (3.4) | I1–I3, I10, I11 tests, `test_rollback_readers.py`, `test_messages_download.py` |
| **H3** protected blocks verbatim | **relaxed at S1–S3** (and S4–S7 of the SPEC-verbatim 429 loop) | these expressions are live producers; leaving them would keep a text-to-key parse on the event path, and the "no kind" UI rule would mis-render `test_golden_ui.py:193` in en | 7.7 AST guard; `runonce-1.5.1.json`, events goldens and `test_golden_runonce.py` byte-identical; `set_busy` / `poll` hashes |
| H4 Chinese constant values | kept | constants keep names and values, now as messages or catalog templates | ParityTests extended (8.1) |
| H5 additive event payloads | kept | no key added or removed; text values are the same text | goldens byte-identical; consumer census |
| H6 privacy | kept | cookie messages have no params; masking before emit / store; sidecars masked and capped | I7 test: a proxy with credentials and a broken cookies file in collect and download jobs; no proxy string or cookie line in `library.sqlite3`, failed list, download.log, `request.events`; the existing H6 search |
| H7 sandboxed tests | kept | old code runs only from `release-sources` in sandboxed child processes; the rehearsal works in `build\v180` with redirected state | `run_tests.py` isolation check (also: no `__pycache__` written under `release-sources`) |
| 1.5.1 goldens, `test_golden_runonce.py` | kept byte-identical | — | review hash check |

### 9.2 The sanctioned nodes

| id | block | 1.7.0 expression | 1.8.0 expression |
|---|---|---|---|
| S1 | `Runner._run_once` (H3) | `'下载间隔 / 重试等待'` (waiting payload `reason`) | `WAIT_PACE_OR_RETRY` |
| S2 | `Runner._run_once` (H3) | `f'归档信息保存失败（媒体已保留）：{error}'` | `msg('core.engine.archive_info_failed', error=message_of(error))` |
| S3 | `Runner.download` finally merge (H3) | `f'失败清单未能保存：{error}'` | `msg('core.engine.unfinished_not_saved', error=message_of(error))` |
| S4 | `_cooled` 429 loop | `f'无法启动下载引擎：{_sentence(error)}。可在“偏好设置”恢复内置引擎。'` | `msg('core.engine.cannot_start_engine', error=_sentence(error))` |
| S5 | `_cooled` 429 loop | `'此链接多次触发 HTTP 429，已记入失败清单。'` | `msg('core.engine.rate_limited_out')` |
| S6 | `_cooled` 429 loop | `'网站限流，暂停整个下载队列'` | `msg('core.wait.rate_queue')` |
| S7 | `_cooled` 429 loop | `f'网站限流，冷却后重试此链接（{self._rate_attempt}/{MAX_RATE_LIMIT_RETRIES}）'` | `msg('core.wait.rate_retry', attempt=self._rate_attempt, limit=MAX_RATE_LIMIT_RETRIES)` |

Nothing else on those lines or in those blocks changes. `self.archive_errors.append(str(error))` stays.

---------------------------------------------------------------------------------------------------

## 10. Work packages

### 10.1 Working copies and commands

- **WP-0 (gate, orchestrator):** ROOT's HEAD is the released 1.7.0 commit (`07c9603` when this spec was written;
  if a 1.7.0 fix commit followed, use the latest 1.7.0 commit and record it), the working tree is clean,
  `src/engine.py` says `APP_VERSION = '1.7.0'`, `build\package-1.7.0\YT-DLP下载器` was built from that commit.
  Record the commit hash; `release-sources/1.7.0` is `git archive <that commit> src`. The L2 tree is no longer used.
  Re-anchor this spec's line numbers if 1.7.0 moved (a short note in `docs/1.8.0/handoff/WP-0.md`); flows and
  function names are normative, line numbers are not.
- Each package works in its own copy `ROOT\build\v180\<wp>\源码` made from the previous state with
  `robocopy <source> <copy> /E /XD build .venv .git __pycache__` (vendor and the untracked 1.5.1 backup come
  along). Integration copies: `build\v180\int1\源码` (after WP-2 ∥ WP-3), `build\v180\int2\源码` (after WP-4 ∥ WP-5),
  `build\v180\rc\源码` (WP-6). Never edit ROOT, the install folder's `data\`, the installed program or the owner's state.
- Tests: from the copy root, `PY verification\run_tests.py [--all] [-k NAME]` (the runner takes its root from its
  own location). Never edit `verification/` while it runs. Old sources are imported with `-B`
  (`PYTHONDONTWRITEBYTECODE=1` from `_sandbox`).
- Each package ends green (`run_tests.py` default tier; `--all` in WP-6) and writes
  `docs/1.8.0/handoff/<WP>.md` (gitignored): changed files with SHA-256, deviations with reasons, test summary,
  assertion-count diff per edited test file, open issues, notes for the next package.

### 10.2 Order

`WP-0 → WP-1 → (WP-2 ∥ WP-3) → I-1 → (WP-4 ∥ WP-5) → I-2 → WP-6 → release (section 11)`.
Parallel packages never share a file. A later package may edit a file an earlier one edited. What makes this hold
for the guards' data:

- the allow files are per module (`verification/golden/text-allow/<module>.json`, 7.2); WP-1 creates one for every
  `src` module and pre-fills its `literals` and `raises` from the 1.7.0 scan with everything that stays in 1.8.0 (the
  producer tables of section 3 say which literals become messages; those are **not** listed, so a module fails G1
  until it is converted). The `composed` and `sinks` sections start empty except for the `STATIC_ALWAYS` modules and
  are filled, with reasons, by the package that converts the module. A package edits only the allow files of the
  modules it owns;
- `i18n_tool.py`, `i18n_inventory.json`, the catalogs and `sidecar-keys.json` belong to WP-1 (and later WP-6) only.
  WP-1 adds every key the spec names (3.6), so no other package needs a new key. If one turns out to be missing, the
  package does **not** add it: it leaves that producer unconverted, records it under `pending_keys` in the module's
  allow file (key, zh, en, placeholders, the producer line) with the literal allow-listed as category `pending`, and
  names it in its handoff; the orchestrator adds the key and converts the producer at the next integration step
  (I-1 / I-2). The release gate fails while any `pending_keys` entry exists;
- a scanner defect found by a package is recorded in its handoff and fixed by the orchestrator at the next
  integration step, never in the package.

### 10.3 Packages

**WP-1 Foundation** (no producer changes)

- Files: `src/messages.py` (new), `src/i18n_legacy.py` (new), `src/i18n_legacy_data.py` (new, generated),
  `src/i18n_core.py` and any catalog module the generator rewrites (new keys only), `src/i18n_maps.py`
  (regenerated; reverse sections still present); `verification/i18n_tool.py` (subcommands `legacy`, `corpus`,
  `release-sources`; G1 scanner with categories, slot inference, enum sources and the composed-text rule; the G5
  `sinks` scan replacing `suspects` (6.4); inventory section `new_strings_1_8_0`), `verification/i18n_inventory.json`,
  `verification/release-sources/1.5.1/src` (byte copy of the untracked backup's `src`), `.../1.6.0/src`
  (`git archive 728bcf2 src`), `.../1.7.0/src` (`git archive <1.7.0 commit> src`),
  `verification/golden/release-sources.json` (commit / origin and SHA-256 per file),
  `verification/golden/legacy-corpus.json`, `verification/golden/sidecar-keys.json` (3.6),
  `verification/golden/text-allow/<module>.json` (one per `src` module, pre-filled, 10.2), `verification/_sandbox.py`
  (`release_src(version)`, `fixture_texts(reason)`, imports `messages` in guarded processes), `verification/run_tests.py`
  (guard variable, `GUARD_OFF`, guard files → failure, report field), `verification/test_sandbox.py`,
  `verification/test_i18n_catalog.py` (LocalizeTests moved; test 6 retargeted to the corpus; test 10 replaced by G5),
  `verification/test_messages.py` (new), `verification/test_legacy_texts.py` (new), `verification/test_text_guard.py`
  (new); one-line `BACKUP` → `_sandbox.release_src('1.5.1')` edits in `test_core_engine.py`, `test_library_store.py`,
  `test_review_state.py`, `test_unfinished.py`, `test_urls.py`, `test_i18n_catalog.py`.
- Delivers: the API of 2.2 (with tripwires, `TRANSIT` attribution and the `show()` / `error_text()` / `t()` hooks),
  `decode` / `decode_columns` / `encode` with the schema of 4.3 and foreign-entry pass-through, the legacy reader of
  4.6 (`recognise` including every `GROUPS` value and the UI-text exclusion), the eight keys, the G1 / G1f / G3 / G5
  machinery and their self-tests, scrub check of `release-sources` (the scrub patterns of 11.7, built at run time from
  the environment).
- Tests: `test_messages.py` (carrier table 2.3 for `Message`, `Raw` and `Value`; `show` in zh / en; `cut` / `masked`
  / `clauses` / `stripped` / `message_of`; wire round trip incl. malformed lines; sidecar build / prune / collision /
  cap / surrogates / never raise; `null` written only for liftable `Raw` text; decode of entries, `null`, orphans,
  broken entries; **foreign entries** — unknown key, other placeholders, unknown nested key — decoded, rendered in en
  as lift or stored text, and re-encoded byte for byte, also nested in a new `clauses` message; the key registry; the
  decode copy keeps `options` as the same object; L1–L9 incl. D-H-1 fragment, R4, cut prefixes, the `needs_chrome`
  pair, the 1.5.1 variants, the CR2-2 job-title rule; zh catalog equals 1.7.0; frozen data regeneration; the G2
  self-tests of 7.3), `test_legacy_texts.py` (G3 incl. catalog-key-built shapes + self-test), `test_text_guard.py`
  (G1 family on `STATIC_ALWAYS` modules, G5 on nothing yet; all self-tests of 7.2 and 7.9 on `release-sources/1.7.0`
  and synthetic modules). Everything else unchanged and green, with the guard on.

**WP-2 Download core** (depends on WP-1)

- Files: `src/engine.py`, `src/unfinished.py`, `src/naming.py`, `src/urls.py`, `src/formats.py` (each gets
  `MESSAGES_READY = True`); their allow files `verification/golden/text-allow/{engine,unfinished,naming,urls,formats}.json`;
  `verification/test_messages_download.py` (new), `verification/test_protected_blocks.py` (new); only if a test needs
  it (recorded): `test_engine.py`, `test_queue.py`, `test_review_engine.py`, `test_review_state.py`,
  `test_unfinished.py`, `test_v170_core.py`, `test_urls.py`, `test_formats_naming.py`, `test_core_engine.py`,
  `test_integration.py`, `test_batch_resilience.py`, `test_enqueue_persist.py`.
- Delivers: 3.2 (WP-2 rows), 3.4 (unfinished), S1–S7, `APP_VERSION = '1.8.0'`.
- Tests: G1 family (incl. G1f) for the five modules; G2 clean on the whole suite with the guard on (the 1.7.0 UI is
  not ready, so its handling of the new messages is not charged, 7.8); H3 guard; regression cases D-H-1 / D-H-2 /
  D-H-3 / D-H-4 (key, stored bytes, en text via `show`); failed-list rollback (5.2) incl. foreign entries through
  `unfinished.save`; goldens byte-identical; `test_review_state.py:163` unchanged.

**WP-3 Collect / library / update core and store** (depends on WP-1; parallel with WP-2)

- Files: `src/collector.py`, `src/library.py`, `src/updater.py` (`MESSAGES_READY = True`); their allow files
  `verification/golden/text-allow/{collector,library,updater}.json`; `verification/test_messages_store.py` (new),
  `verification/test_rollback_readers.py` (new); only if needed: `test_library_store.py`, `test_library.py`,
  `test_collection.py`, `test_core_updater.py`, `test_core_paths_settings.py`, `test_packaged_collection.py`,
  `test_v170_merge.py`.
- Delivers: 3.3 (WP-3 rows), 3.4 (library, with the two load paths), IPC 2.8, emit hooks, `library.py` 6.4 deletion.
- Tests: round trip of every `Library` writer / reader pair for every record kind and value type (sidecar, `null`,
  orphan pruning, `columns`, legacy rows lifted per field kind, legacy guesses not persisted, foreign entries kept,
  `_storable` with lone surrogates, 2 KB cap); the 3.4 required test (I10: a 1.6.0-only-format job through every
  read-modify-write, stored `options` bytes unchanged); `contexts` → `Archive` → `资料信息.json` → files (`show`
  titles); IPC round trip (page titles, nested errors, malformed line skipped); `str(UpdateError) == MSG_*`; I2
  (schema and `PRAGMA user_version` unchanged), I3, I7, I8, I11; a 2000-link page encodes without `null` entries for
  ordinary titles; `test_rollback_readers.py` (5.2).

**I-1 Integration** (orchestrator): merge the WP-2 and WP-3 copies (disjoint files) into `int1`; full default suite
with the guard on; a failure goes back to the owning package.

**WP-4 Download UI** (depends on I-1)

- Files: `src/studio.py`, `src/ui_download.py`, `src/ui_log.py` (`MESSAGES_READY = True`); their allow files
  `verification/golden/text-allow/{studio,ui_download,ui_log}.json`; `verification/test_i18n_download.py` (keep the
  `PROTECTED` hashes and the `REASON_WORDS` pin), `verification/test_messages_ui_download.py` (new); only if needed:
  `test_ui_state.py`, `test_ui.py`, `test_polish_download.py`, `test_final_fix_ui.py`.
- Delivers: 6.2 / 6.2b / 6.3 / 6.4 for these files, D-F40…D-F49. `ui_download.py:346` stays as it is (D17).
- Tests: G4 for the download-side channels, census for `log`, `waiting`, `item_done`, `title`; G5 for the three
  modules; G2 clean on the whole suite; golden UI tests byte-identical.

**WP-5 Collect / library / settings UI** (depends on I-1; parallel with WP-4)

- Files: `src/collection_ui.py`, `src/library_ui.py`, `src/ui_settings.py` (`MESSAGES_READY = True`); their allow
  files `verification/golden/text-allow/{collection_ui,library_ui,ui_settings}.json`;
  `verification/test_i18n_pages.py`, `test_i18n_ui.py`, `test_ui_pages.py`, `test_polish_pages.py`,
  `test_v170_ui.py`, `test_library_ui.py`, `test_i18n_widgets.py`, `verification/test_messages_ui.py` (new).
- Delivers: 6.2 / 6.2b / 6.3 / 6.4 for these files, C-F39…C-F41, the collect-job raw title. `library_ui.py` 152 and
  1036 keep their English internal raises (already in the allow file, G1e).
- Tests: G4 for the collect / library / settings channels, census for `collect_*`, `update_*`; G5 for the three
  modules; G2 clean on the whole suite; history titles (CR2-2) and notes (R4 rows) in both languages.

**I-2 Integration** (orchestrator): merge WP-4 and WP-5 into `int2`; full default suite.

**WP-6 Integration, guards final, documents** (depends on I-2)

- Files: `src/i18n.py`, `src/i18n_maps.py` (generator drops the reverse sections), `src/i18n_legacy.py` (drops any
  transitional code), `src/messages.py` (removes `TRANSIT_UNTIL_WP6` and the readiness gating), every `src` module's
  `MESSAGES_READY` line; `verification/i18n_tool.py` (generator; G5 over all UI modules; stale-entry and
  `pending_keys` checks), every `verification/golden/text-allow/*.json` (stale entries only),
  `verification/test_text_guard.py` (unconditional), `verification/test_i18n_catalog.py` (remaining live-localize
  tests), `verification/test_english_replay.py` (new), `verification/test_i18n_snapshot.py`,
  `verification/snapshot_ui.py` (assertion (13), 7.6; nothing else, D17), `verification/verify_release.py` (evidence
  must show `msg_guard.violations == 0`, no `pending_keys`, and the release-source hashes),
  `verification/test_release_tooling.py`, `README.md`, `使用说明.txt` (title line, a rollback sentence), `架构说明.txt`
  (messages / legacy boundary), `docs/1.8.0/spec/MESSAGES.md` (this spec, publishable: no local scratch paths except
  `%TEMP%` forms).
- Tests: `run_tests.py --all` green with `ui_theme` out of `TRANSIT` (the display tripwire active); G4b / G6; the en
  snapshot gate of 7.6 ((13) clean; (9)–(12) no failure beyond the 1.7.0 baseline); no `localize`, no
  `MESSAGES_READY`, no `pending_keys`, no stale allow entry; review checks of 11.2.

### 10.4 File ownership summary

| package | `src` | `verification` |
|---|---|---|
| WP-1 | messages, i18n_legacy, i18n_legacy_data, catalogs (new keys), i18n_maps (regenerated) | i18n_tool, i18n_inventory.json, release-sources/, golden/{release-sources, legacy-corpus, sidecar-keys}.json, golden/text-allow/ (creates all; afterwards keeps those of the modules nobody converts), _sandbox, run_tests, test_sandbox, test_i18n_catalog, test_messages, test_legacy_texts, test_text_guard; `BACKUP` lines of five more tests |
| WP-2 | engine, unfinished, naming, urls, formats | text-allow/ of those five; test_messages_download, test_protected_blocks; listed download-core tests if needed |
| WP-3 | collector, library, updater | text-allow/ of those three; test_messages_store, test_rollback_readers; listed store tests if needed |
| WP-4 | studio, ui_download, ui_log | text-allow/ of those three; test_i18n_download, test_messages_ui_download; listed UI tests if needed |
| WP-5 | collection_ui, library_ui, ui_settings | text-allow/ of those three; test_i18n_pages, test_i18n_ui, test_ui_pages, test_polish_pages, test_v170_ui, test_library_ui, test_i18n_widgets, test_messages_ui |
| WP-6 | i18n, i18n_maps, i18n_legacy, messages (gating removal), flag lines | i18n_tool, text-allow/ (stale entries), test_english_replay, test_text_guard, test_i18n_catalog, test_i18n_snapshot, snapshot_ui ((13) only), verify_release, test_release_tooling; README, 使用说明, 架构说明, docs/1.8.0 |
| orchestrator (I-1, I-2 only) | a producer listed under `pending_keys` | the missing key in i18n_inventory.json / catalogs; scanner fixes recorded by a package |
| never | settings, paths, procutil, progress, applog, ui_theme, app (allow files only) | golden/*.json of 1.5.1 / 1.6.0, test_golden_*.py, install_release.py |

A test file that unexpectedly needs a change in a parallel package's file is recorded in the handoff and changed
at the next integration step.

---------------------------------------------------------------------------------------------------

## 11. Integration, review and release

### 11.1 Integration

I-1 and I-2 as in 10.3; WP-6 produces the release candidate `build\v180\rc\源码`. Every integration runs the full
default suite with the guard on, compares the golden files and `test_golden_*.py` with the 1.7.0 commit by SHA-256,
and checks `set_busy` / `poll` hashes.

### 11.2 Reviews

- After each package: an independent reviewer (fresh agent, reads this spec, the handoff and the diff) checks file
  ownership, the package's guard results, the sanctioned nodes, assertion counts per edited test file (`assert`
  calls counted before and after; any decrease justified), and that no test was weakened to pass (e.g. an
  exemption without a reason, a `GUARD_OFF` addition, a removed English assertion).
- Final review of the release candidate, four lenses, each with its own agent: **data safety and rollback**
  (I1–I11, 5.2 tests, the 3.4 two-load-path rule, rehearsal plan), **zh parity** (8.1, G6, a zh snapshot comparison
  with the 1.7.0 screenshots), **English completeness / defect class** (guards' self-tests, the per-module allow files
  read entry by entry — every `sinks`, `composed` and `stored` entry must have a reason that holds —, an en
  walkthrough with `snapshot_ui.py --appearance both --language en` and its assertion (13), the replay), **code
  quality** (boundaries, no dead code, spec conformance).
  Findings go to fix rounds; each fix round re-runs the full suite and the affected guard self-tests.

### 11.3 Packaged build

From the merged ROOT (11.6 step 1): `PY build.py` (tests `--all`, PyInstaller, exe tests, release gate) →
`build\package-1.8.0\YT-DLP下载器` and `build\verify\release-1.8.0.json`. The gate must pass, including the evidence
fields of WP-6.

### 11.4 zh / en smoke

The packaged exe's `--self-test` and GUI smoke (verify_release) under `YTDLP_DESKTOP_LANG=zh` and `=en`;
`snapshot_ui.py --appearance both --language both` from the release candidate: zh pictures match the 1.7.0 ones
(review); en: assertion (13) passes (no Chinese outside user data) and (9)–(12) show no failure beyond the 1.7.0
baseline (7.6). The carried 1.7.0 minors (D17) are expected in both runs and are not fixed by this release.

### 11.5 Install / rollback rehearsal in the owner's folder layout

All under `ROOT\build\v180\rehearsal\` (same drive as the owner's install), with `LOCALAPPDATA` redirected into it;
nothing outside it is written. Every `install_release.py` call passes `--instance-name Local\YtDlpDesktop.Test.<uuid>`
(the owner's running program neither blocks nor is touched; the 1.6.0 WP6 rehearsal rule), and each staged package
has its `build\verify\release-<version>.json`. The receipts name their files relative to the target, so a copied
layout installs and rolls back like the original. Comparisons of `library.sqlite3` are row-level dumps of every
table (a read may create journal files); the only difference a read path may cause is the 1.7.0 behaviour of
`history()` marking stale `running` jobs `interrupted` (their `status` / `updated`), which the comparison lists
explicitly.

1. **Target**: copy the installed layout of the install folder (exe, `_internal`, the two backup exes,
   `install-receipt.json`; read only) to `rehearsal\YT-DLP下载器`. **Data**: a read-only snapshot of the owner's
   `data\library.sqlite3` of the install folder (sqlite backup API from a `mode=ro` connection) into
   `rehearsal\YT-DLP下载器\data\`, and copies of the owner's `settings.json` and `failed-downloads.json` (with any
   bad / overflow files) into the redirected state folder. Record SHA-256 of every copied file and of the owner's
   originals.
2. Install the released 1.7.0 package with `install_release.py --stage build\package-1.7.0\YT-DLP下载器 --target
   rehearsal\YT-DLP下载器` (as the owner would); run its `--self-test`. Run the 1.7.0 reader probe (frozen 1.7.0
   page functions in a child process, zh) over the data → dump **A**.
3. Install `build\package-1.8.0\YT-DLP下载器` the same way; `--self-test` in zh and en. **I8 check**: after the 1.8.0
   reader probe has read everything, every copied JSON file is byte-identical to its state after step 2 and the
   library is row-level identical (only the listed `interrupted` marking may differ). Reader probe: decode every
   record; zh dump **B** must equal **A**; en dump: no exception, and a census (counts only: rows with sidecars,
   lifted, keyless CJK in app-text fields) for review.
4. 1.8.0 writes in normal work through its own code on the rehearsal data: a download job (scripted Runner, local
   fixture server), a collect job (fake fetcher), a subscription check, a failed-list merge. Assert I1 / I3 / I10 on
   the changed rows and files (every pre-existing job row's `options` bytes unchanged).
5. `install_release.py --rollback --target ...` → 1.7.0: `--self-test`; 1.7.0 readers over everything (incl. the
   1.8.0 rows): no exception, old rows still dump **A**; 1.7.0 writes (change_job, finish_subscription, unfinished
   merge): sidecars kept or orphaned as in 5.1.
6. `--rollback` again → the 1.5.1 backup exe: `--self-test` if it has one, else start-and-close; 1.5.1 readers
   (stub-widget history and subscription selection, `load_failed_job` with its own paths): no exception.
7. Reinstall 1.8.0; reader probe: every row decodes, zh dump equals what the frozen 1.7.0 functions give for the
   same data, en census.
8. Variant: repeat steps 3-7 once on a fresh copy of step 1 **without** step 2 (1.8.0 directly over the owner's
   current 1.6.0; the first rollback then returns to 1.6.0, whose frozen readers are run instead of 1.7.0's).
9. The owner's originals (step 1 hashes; the install folder's top-level files and `data\`, `%LOCALAPPDATA%\YtDlpDesktop`)
   are unchanged. Delete the data copies; keep only `rehearsal\report.json` (hashes, counts, pass / fail; no texts).

If the owner's data cannot be read (locked, absent), use real-shaped data produced by the G4b replay and say so in
the report.

### 11.6 Merge, commit

1. Apply the release candidate to ROOT with a merge list (every differing file with SHA-256, as in 1.7.0); run
   `run_tests.py --all` on ROOT; run 11.3 and 11.4 on ROOT's build if the candidate was built elsewhere.
2. Commit on `main` with the repository's configured identity, unchanged: `user.email` is the id-based GitHub
   noreply address (`262798495+<login>` at `users.noreply.github.com`) that the 1.6.0 and 1.7.0 commits carry, and
   `user.name` the login the account had then. GitHub attributes an id-based noreply address to the account by its
   id, so the commits stay linked to the account after its rename; keeping the identity keeps the three release
   commits consistent and rewrites no history. (Changing `user.name` to the current login for future commits is the
   owner's choice, 13.4; it is not done by default.) Message per 11.8, ending with the attribution lines the session
   requires. Tag `v1.8.0`; tag the 1.7.0 commit `v1.7.0` and the 1.6.0 commit `v1.6.0` if they have no tag (none has
   one when this spec was revised).

### 11.7 Publication gate and push

The owner's chat instruction for this run: when everything is done and the tests show no problem, upload to the
GitHub repository directly and state the advantages clearly. So, only when **all** of the following pass, push
without asking again; otherwise stop and report:

- `run_tests.py --all` on ROOT: no failure, `msg_guard.violations == 0`; the packaged gate (11.3); the smoke (11.4);
  the rehearsal (11.5); the final review has no open major finding.
- Scrub gate over `git ls-files` of the commit: none of `vendor/`, `.venv/`, `build/`, `data/`,
  `verification/backup-*/`, `docs/*/handoff/`, `docs/1.6.0/{audit,baseline,prototype}/`, `*.log`, cookies files, old
  report JSONs; no tracked file contains the Windows user name (`%USERNAME%`, also its 8.3 short form), a path under
  `%USERPROFILE%` or `%LOCALAPPDATA%` (expanded at run time, so this spec itself never holds the patterns), or the
  owner's e-mail address. `verification/release-sources/` is published (scrubbed; needed by the guards and six
  existing tests).
- Account check, before anything is created or pushed: the target is the GitHub account with id **262798495**.
  That account was renamed: `gh auth status` still lists the keyring entry under the login it had when the project
  notes were written, while `gh api user` returns the current login with the same id (the id also appears in the
  configured noreply address). Run `gh api user --jq '.id, .login'`; if the id is not 262798495, stop and report.
  Otherwise the repository is `<current login>/ytdlp-desktop`: check it with `gh repo view <current
  login>/ytdlp-desktop`; create it if it does not exist (public, MIT licence file from the tree, source only, no
  template, no release); the remote URL uses the current login (no reliance on GitHub's rename redirect). Push
  `main` and the tags. No release asset is uploaded. Installing 1.8.0 on the owner's machine is not done.

### 11.8 What the README and the commit message say (the advantages)

README gets a section **“1.8.0：内部改用消息编号”** (Chinese, then an English summary in the English part), and the
commit message body the same points:

1. 不再逐条打补丁：程序内部只传“第几号消息 + 参数”，界面显示时按当前语言查表；新增一句话只需在中英文目录各加一条。
2. 同类问题不会再出现：静态扫描、全部测试中的运行时哨兵、旧版本存储文字的完整性检查和英文回放会让任何漏掉编号的文字直接导致测试失败；
   用这些检查回放 1.7.0 的旧代码，能自动抓到当时三轮审查才发现的“媒体直链”“动态页面媒体请求”和 1.5.1 旧格式采集错误。
3. 修复 1.7.0 英文界面的已知问题：cookies 行格式错误、被截成 70 字的原因、只有英文的内部提示、被误改写的 yt-dlp 原始输出、
   含“；”的订阅错误被拆坏。
4. 中文界面与 1.7.0 完全一致：所有中文黄金记录逐字节不变。
5. 数据安全、可回退：资料库、设置、未完成清单、失败清单和历史记录升级时不改写；新增的只是旧版本会忽略的附加字段；
   1.7.0 / 1.6.0 / 1.5.1 仍能照常读写同一份数据（已用旧版本自己的代码验证，并做了安装 / 回退演练）。
6. 不增加依赖，隐私规则不变：cookies 内容从不记录，代理地址先屏蔽再保存。
7. 为什么是 1.8.0 而不是 2.0：原先以为改用消息编号会改变数据格式、旧版本读不懂，所以打算留到 2.0；现在的做法没有改变任何旧字段，
   编号只放在旧版本会忽略的附加字段里，1.7.0 / 1.6.0 / 1.5.1 仍能读写同一份数据、可以照常回退，所以按项目规则是 1.8.0。
   2.0 留给将来决定不再支持回退到这些旧版本的那一天。

English summary: message keys instead of per-text translation patches; guards that make a missed key fail the
tests (and catch the three 1.7.0 misses on the old code); 1.7.0 English defects fixed; Chinese interface
byte-identical; data untouched by the upgrade and readable by 1.7.0 / 1.6.0 / 1.5.1 after a rollback; no new
dependency, same privacy rules; why 1.8.0 and not 2.0 (no format break, rollback still works; 2.0 is kept for the
day rollback to these versions is given up). The README keeps the program's existing strengths section (unattended
YouTube bot-check handling, web collector, library and subscriptions, safe update and install, privacy, bundled
tools, zh / en). The commit message carries points 1, 2, 5 and 7 in full and the others in one line each.

---------------------------------------------------------------------------------------------------

## 12. Risks and accepted limitations

| # | item | handling |
|---|---|---|
| L-1 | A future string operation flattens a message on a path no test runs, or composes a short app text (under six CJK characters, not a whole text) with other text: en shows Chinese there (zh and data unaffected) | G1 forbids literal producers and G1f composed text in core text slots; G5 rejects, at every sink the contract lists (A15.4 with A16.1; the ids of `i18n_tool.CONTRACT`, A16.7), a value that the flows (A15.3 with A16.2 / A16.3) trace to a source of A15.2, and a strict-sink value the 7.9 grammar cannot prove display text (forms outside that contract: L-15); tripwires and the G2 landing checks cover every tested path; `cut` / `masked` / `clauses` / `message_of` make the right call the easy one; a flattened text is plain, so it is lifted on read rather than frozen as raw (E3) |
| L-2 | A producer forgets `raw()` for user data | that text is lifted like 1.7.0 data: the old CR2-1 / CR2-2 / CR3-1 ambiguity, en only |
| L-3 | Legacy rows: user titles equal to app texts (a page literally titled `网页`, a subscription named `未命名订阅` by 1.7.0) | translated in en as in 1.7.0; zh always shows the stored text |
| L-4 | `options.archive_contexts` titles are lifted on resume (no sidecar inside `options`) | a resumed archive of a user page titled `网页` shows `Web page` in en |
| L-5 | Two fields of one object with the same text and different meanings | one entry; the message wins (1.7.0 translated both) |
| L-6 | History search stays `LIKE` on the stored zh title (CR1-2) | unchanged |
| L-7 | OS / sqlite / curl text nested in messages stays in its own language (D-H-5) | by definition |
| L-8 | Window-thread unfinished notices reach download.log only (D-H-11) | unchanged (zh parity) |
| L-9 | The English-only texts stay English in zh (D13) | deliberate |
| L-10 | `i18n_legacy` stays while rollback to ≤ 1.7.0 is supported | frozen and generated; retired only by a 2.0 decision |
| L-11 | G2's ≥ 6-CJK-run rule can flag a fixture (a Chinese user title or engine line that shares six characters with an app text) | visible exemption with a reason; G2 runs only in tests, never in the program |
| L-12 | Sidecars grow `library.sqlite3` | one entry per distinct message text of an object (the map is keyed by text, so 2000 links titled `媒体直链` cost one entry), `null` only for user text equal to an app text (E2); capped at 2 KB per entry; pruned on every 1.8.0 write. `contexts()` / `pending()` decode each page / entry row they read: a dict lookup per text plus the cached lift |
| L-13 | A version without sidecars (≤ 1.7.0) stores the same text with another origin while the 1.8.0 entry stays (its `Archive.prepare` sets `title = context['title']` for a user page literally titled `网页` / `媒体直链`, or the reverse with a `null` entry) | en shows the entry's rendering for that row until 1.8.0 rewrites it; zh shows the stored text; data unaffected. Needs a user text equal to an app text and a rollback; accepted |
| L-14 | After a rollback from a later version to 1.8.0, texts stored with keys 1.8.0 does not know are shown through the lift or as stored (zh) in en | entries for the text fields of 4.3 kept and written back verbatim (I11), so the later version shows them correctly again; an entry for a field 1.8.0 does not treat as a text field is dropped at the next 1.8.0 read-modify-write, the field kept (A15.8.3) |
| L-15 | (converge round, A15; Phase D fix 1, A16) Forms outside the static guard contract of A15.2–A15.4 and A16. Each is named with the check that covers it on the tested paths, and with what that check does not cover. | **Dynamic names** (`getattr` / `setattr` with a computed name, `globals()` / `vars()`, `eval` / `exec`). `getattr(self, 'X')` with a constant is F3, and G1d reports a computed import and `sys.modules` of a boundary module (A15.8.1). Run time: G2 checks at the emits, the store, `show()` / `error_text()` and `t()` (in English with the UI texts, A15.5), the tripwires and the display tripwire for `Message` / `Value`, and en snapshot (13). Not covered: a plain fixed text that such code puts into a widget on a path that no test or snapshot runs. **Run-time replacement** (`library.py`'s `setattr(Library, name, _ui_retrying(...))`, monkey-patching, decorators). The flows read the definitions as written. Run time: G2 and G4 run the replaced code. Not covered: a replacement that brings in its own fixed text on an untested path. **Containers handed over through code the flows do not follow** (the event queue `events.put` → `poll` → `handle` → the `getattr` handlers, row models kept in other objects, `self.app.X`, an attribute of an arbitrary object, values written to sqlite / JSON and read back). These are not followed: at a value sink they are treated as user or core data, at a strict sink they are not display text. Run time: the emit hook for core events, the `encode` hook for the store, the `show()` / `t()` hooks, G4 (every channel × key) and the G4b replay. Not covered: a fixed UI text that the window itself puts into such a container and shows without `show()` on an untested path. **Tk variables and widget state** (`StringVar.set` / `.get`, `textvariable=`, `cget('text')`, `ui_theme.full_text`). Not followed; in the tree they hold links, paths and searches the owner typed. Run time: en snapshot (13) on the captured pages. **Callbacks registered outside the scanned modules** (`command=`, `bind`, customtkinter's own callbacks). A strict parameter of a function used as a value is not display text (A7.6); a reference or a lambda that is only called through a local alias is no such use (A16.3). Its value-mode parameters are checked only at the calls that G5 sees. Run time: the `show()` / `t()` hooks inside the handler. **Values from modules G5 does not scan** (a core function's return, a record, an event value). These are opaque. G1 checks the core's own slots and G2 checks the core's emits and store writes. **Flow-insensitivity and coarsening.** Every binding of a name counts, except F1's shown-on-entry rule. A selection deeper than three steps and a write under a computed key are taken whole. This can give a finding where none is needed; it drops no leaf, so it causes no miss by itself. **ui_theme itself** (the display boundary) is not a G5 module (G1 scans it like every src module). Its text parameters are sinks (`THEME_SINKS`, with a completeness test), and its operations on a `Message` / `Value` are the display tripwire. **Seven 1.7.0 test files replace `Runner.emit` with a plain function** (`test_queue`, `test_batch_resilience`, `test_review_engine`, `test_v170_core`, `test_polish_pages`, `test_ui_pages`, `test_v170_ui`), so G2's emit hook does not see their events. G1a and G1f check those producers statically, and the `show()` / `t()` / `encode` hooks check what is shown or stored. **Forms of the Phase D probes that stay outside** (A16.9). A fold with a function argument (`functools.reduce(lambda a, b: a + b, NAMES)`), the value of a standard-library call that returns its argument (`with contextlib.nullcontext(X) as v`: F1 follows a with-as binding to its context expression, F9 does not enter the standard library), an attribute without a TEXT_KEYS name of an arbitrary object (`obj.note = a + b`, `obj` a parameter), a Chip / text-box receiver whose binding the flows do not see and whose name gives no sign (a parameter, an attribute of another object), the text / tag pairs after the first text of a tkinter `insert(index, text, tags, text2, tags2)`, and a positional `cnf` of configure that is no dict display or local dict name. Run time: G2 at the emits, the store, `show()` / `error_text()` and `t()` (in English with the UI texts, A15.5), the tripwires and the display tripwire for a `Message` / `Value`, and en snapshot (13) on the captured pages. Not covered: a fixed text that such code puts into a widget on a path that no test or snapshot runs. **The wider Tk-level sentinel was evaluated and not built** (A15.5 b). |
| L-16 | (Phase D, A17) The static scan is a help, not a proof. Review round 2 of Phase D found contract forms the scanner does not implement, contract behaviours no generated case depends on, and more realistic and contrived forms outside the contract (all listed in A17). The owner chose not to extend the scanner further for 1.8.0; they are not fixed. | Run time: G2 at the emits (except the seven test files of L-15), the store, `show()` / `error_text()` and `t()` (in English with the UI texts, A15.5), the tripwires and the display tripwire for a `Message` / `Value`, en snapshot (13), the G3 closure and the G4b replay. Not covered: a fixed text that such code puts on screen on a path that no test or snapshot runs. |

---------------------------------------------------------------------------------------------------

## 13. Open questions (owner; none blocks the implementation)

1. **Publishing `verification/release-sources/`.** The roadmap left "verification backups" out of the public repo
   because they contained the Windows user name and local paths. The new folder holds only the three released
   `src` trees, scrub-checked, and the guards and six existing tests need it. Default: publish it. If the owner
   prefers not to, the guard tests must skip in a clone without it, and the public suite then checks less.
2. **The rehearsal reads a snapshot of the owner's real library and state files** (local copy under `build\`,
   deleted afterwards, report with counts and hashes only). Default: yes, because it is the strongest evidence that
   the real data survives upgrade and rollback. If the owner objects, the rehearsal uses replay-generated data.
3. **Chinese wording for the English-only texts** (D-H-3, eight keys). Default for 1.8.0: zh = English (parity). A
   later version may give them Chinese; that changes zh on purpose and is a one-line catalog change each.
4. **Commit author name after the account rename.** The configured `user.name` is the account's former login; the
   e-mail is the id-based noreply address, which GitHub keeps attributing to the account. Default: keep both for the
   1.8.0 commit (consistent with 1.6.0 / 1.7.0, no history rewrite). The owner may ask to use the current login for
   later commits; old commits are never rewritten.

---------------------------------------------------------------------------------------------------

## Appendix A. New catalog keys (`i18n_core.py`, inventory section `new_strings_1_8_0`)

| key | zh | en | placeholders |
|---|---|---|---|
| `core.cookies.line_failed_item` | `cookies 文件中有一行格式不对，无法读取（内容不记录）` | `A line of the cookies file has a wrong format and cannot be read (content not logged)` | — |
| `core.options.not_object` | `Options data must be an object` | same | — |
| `core.options.no_output` | `Options data has no usable output folder` | same | — |
| `core.options.bad_resolution` | `Unsupported resolution: {resolution}` | same | resolution (`repr`) |
| `core.options.unsupported` | `Unsupported output option` | same | — |
| `core.formats.bad_mode` | `Unsupported output format: {mode}` | same | mode (`repr`) |
| `core.formats.bad_id` | `Unsupported output format id: {id}` | same | id (`repr`) |
| `core.formats.bad_label` | `Unsupported output format label: {label}` | same | label (`repr`) |

`common.clauses` (structural, `messages.py`): zh `'；'.join(parts)`, en `_join_sentences('en', parts)`.

## Appendix B. Frozen legacy data (generated content, for review)

`TEMPLATES`: every key of the released 1.7.0 `i18n_maps.LOCALIZE` with its 1.7.0 zh catalog text; the 11 `VARIANTS`
(`{count}条 · {mode}` and the ten 1.5.1 collector forms without spaces); the D-H-1 fragment. `NUMERIC`, `RECURSE`
as released. `TITLE_KEYS`, `NAME_KEYS`, the job-title context sets (L6–L8). `SOURCES`: SHA-256 of the files read.
`STRIPPED = {'core.cookies.line_failed': 'core.cookies.line_failed_item'}` (L1 data for the stripped echo).

## Appendix C. Evidence of the design phase (non-normative)

Scratch folders under `%TEMP%\ytdlp180\`: `download\` and `collect-library\` (maps' probes, old-reader drives),
`compat-first\` (`msg_proto.py`, `drive18.py`, `oldread.py`, `stub151.py`, `report18.json`, `guard_demo.json`),
`minimal\` (`probe_msg.py`, `proto_check.py`, `report_sidecar.json`), `clean-model\` (`readers-report.json`,
`h3_guard.py`, `g3_proto.py`, the runonce prototype), `spec\` (carrier checks of this spec: `str()` / f-string /
`%` / exception / json / sqlite / Tcl / logging / deepcopy / `asdict` / pickle behaviour and the frame-checked
tripwires). They contain local paths and are never copied into the repository.

## Appendix D. Identifiers

D-F01…D-F55, D-H-1…D-H-11: `map-download.md`. C-F01…C-F52, C-H-1…C-H-18: `map-collect-library.md`. H1–H7: the hard
rules of the 1.6.0 / 1.7.0 specs. S1–S7: sanctioned nodes (9.2). I1–I11: storage invariants (4.7). L1–L9: legacy
rules (4.6). L-1…L-14: limitations (12). E1–E7 and E1b: encode rules (4.5). P1–P8: producer rules (3.1). G1–G6
(with G1a–G1f): guards (7). R-text, R-param: G2 rules (7.3). `STATIC_ALWAYS`, `RUNTIME_ALWAYS`, `TRANSIT`: staging
sets (7.8). D1–D17: decisions (0).

---------------------------------------------------------------------------------------------------

## 14. Revision (after the completeness review, 2026-11-17)

Each finding was checked against the 1.7.0 code (`07c9603`, identical to the integration tree) before it was fixed.

| finding | change |
|---|---|
| major: no guard at the display boundary; G5 guessed from variable names; tripwires blind to C-level flattening; 2.3 overstated | G2 hooks added at `show()` / `error_text()` (plain recognisable text) and `i18n.t()` (R-param: `Message` / `Value` / `Raw` / exception params; R-text in en) with a UI-text exclusion (7.3); `ui_theme` becomes a display tripwire after WP-6 (2.9, 7.8); G5 rewritten as a positive sink rule with sink methods, display-text grammar and per-module allow entries (7.9); new G1f composed-text rule (7.2); 2.3 now separates method-call flattening (tripwires) from C-level flattening (landing checks + G1f) and names what stays uncaught (L-1); six `str(error)` dialogs and three exception-object `t()` params of 1.7.0 listed for conversion (6.2b) |
| major: staging could not stay green (tripwires in `i18n` / `ui_theme` frames) | `TRANSIT` set: runtime checks are charged to the nearest frame outside `messages`, `i18n_legacy`, `i18n` and (until WP-6) `ui_theme`; unattributable calls behave like `str`; separate `STATIC_ALWAYS` / `RUNTIME_ALWAYS` sets; the measured 1.7.0 sites are named (7.8) |
| major: G2 recognised only `core.*`, `value.*`, `download.reason.*`; G1b followed module-level names only | R-text recognises every CJK `GROUPS` value (formats, collect modes / filters, library tabs, link kinds, modes, `最佳`) and requires a `Value` (7.3); G1b follows local bindings, loop targets, enum sources of other modules and `.label` / `.mode` (7.2) |
| major: rollback to 1.8.0 would drop a later version's sidecar entries | well-formed entries 1.8.0 does not understand decode to `foreign` messages that keep their entry and are re-encoded verbatim (4.4 step 3, E1b, I11); key registry `sidecar-keys.json` (3.6); tests in `test_messages.py` / `test_messages_store.py`; L-14 |
| major: read-modify-write paths could reuse `_read_record` and lose `mode_v2` | two load paths defined, RMW never touches `options` (3.4, I10); required test of every RMW path on a 1.6.0-only-format job, also in `test_rollback_readers.py` with 1.5.1's `restore_download_options` (3.4, 5.2) |
| minor: parallel packages shared the allow list | one allow file per module, pre-filled by WP-1; missing-key procedure via `pending_keys` handled by the orchestrator (7.2, 10.2, 10.4) |
| minor: `REASON_WORDS` had no category | category `h4-unused` with a no-reference check (7.2 G1a) |
| minor: G1c literal-only key vs C-F39; `'；'` token in `i18n_legacy` | conditional key of literals accepted; token allowed in `i18n_legacy.py` (7.2) |
| minor: G1e count; `library_ui` raises; G1d gaps | exact AST count 8 + 18 with lines and handling of `library_ui` 152 / 1036; G1d forbids every `i18n` attribute in core modules except `library`'s `filetext` `t()` (7.2) |
| minor: "can never go stale" overstated | D5 and 4.2 reworded; L-13 added |
| minor: `Raw` / `Value` carrier unspecified | `__str__` / `__format__` / copy / pickle for all three types, measured CPython behaviour stated; tripwires also on `Value` (2.1, 2.3, 2.9) |
| minor: `null` entry for every `Raw` text | `null` only when the lift would translate the text (E2); L-12 restated |
| minor: G3 extractor missed catalog-key-built shapes | `stored_text`, `i18n.all_values(...)[0]`, `i18n.t`, `CATALOG['zh']` and conditional keys resolved to the release's own templates (7.4) |
| minor: publication account ambiguous | account named by id 262798495, `gh api user` check before create / push, repository under the current login, commit identity kept by default (D16, 11.6, 11.7, 13.4) |
| minor: README / commit did not say why 1.8.0 and not 2.0 | section 1 paragraph and 11.8 point 7 |
| minor: 1.7.0 minors carried in the notes | declared out of scope (D17); the en snapshot gate compares with a 1.7.0 baseline and adds assertion (13) for CJK (7.6, 10.3, 11.4) |

---------------------------------------------------------------------------------------------------

## 15. Amendments during implementation

Decisions taken while the packages were built. Each amendment overrides the sections it names; where an amendment
and an earlier section disagree, the amendment wins. Later packages read this section first.

### A1 (I-1, 2026-11-17) — G1 scanner rules for typed-call arguments, formatted numbers, id tables and paths

Overrides 7.2 G1b and G1f (and the `str(path)` / `file=path` / `record=str(record)` spellings in the producer rows of
3.2 and 3.3). Scanner defects reported by WP-2 and WP-3, fixed in `verification/i18n_tool.py` with self-tests in
`test_text_guard.py` (`G1SelfTests.test_the_i1_scanner_fixes`).

1. **Arguments of typed calls.** The arguments of `raw`, `cut`, `masked`, `stripped`, `clauses` and `message_of`
   (bare or as `messages.<name>`) are G1b slots only: an allow-listed literal or a binding to one is still a finding
   there (`raw(kind)` with `kind = '媒体地址'`), but G1f does not apply to them (`raw(line[6:].strip()[:200])` is
   accepted, as 7.2 says). The keyword params of `msg(...)` stay G1f slots.
2. **Formatted numbers.** G1f accepts an f-string that consists only of holes, each a number expression (a name, an
   attribute, a number literal, arithmetic `+ - * / //` or a unary sign of such, or a numeric call) with a constant
   format spec ending in `d`, `f`, `g`, `e`, `%` or `n` and no conversion (`size=f'{mb:.1f}'`,
   `timeout=f'{error.timeout:g}'`, the forms 2.1 prescribes; a numeric spec raises on text). An f-string with any
   literal text (`f'{mb:.1f} MB'`), a non-numeric spec (`f'{x:>10}'`) or a conversion is still composition.
3. **Id-keyed tables.** A dict display that is the whole value of a module-level assignment, whose keys and values
   are all string literals and whose keys are exactly the members of a module-level tuple / list / set of string
   literals of the same module (the ids it labels: `naming.LABELS` over `naming.NAMING`) has no text slots: its keys
   are ids, not text fields. Its name stays an enum source, so every use of its values in a text slot is still a G1b
   finding.
4. **Paths.** `str(...)` of anything but an exception in a text slot stays a G1f finding. A path param is passed as
   `os.fspath(path)` (the same text as the 1.7.0 f-string hole for a `str` or a `Path`); the producer rows of 3.2 /
   3.3 that write `str(path)`, `file=path` or `record=str(record)` mean `os.fspath(...)`.

Allow entries removed because they existed only for these defects: `engine.json` `composed`
`line[6:].strip()[:200]`, `unfinished.json` `composed` `f'{status.st_size / 1024 / 1024:.1f}'`, `updater.json`
`composed` `f'{error.timeout:g}'`, `naming.json` `slot_ok` of `标题（推荐）`. Kept, with their own reasons:
`engine.json` `composed` `f'{name}: {process.stderr}'` (an English internal raise; G1f checks every raise),
`collector.json` `composed` `plain[:MAX_TEXT]` (the page body under the dict key `text`, user data cut as in 1.7.0),
`library.json` `sinks` `body` (G5, the page body written to the archive file), `app.json` `slot_ok` (self-test text,
P7).

### A2 (I-1) — `'*'` in a schema path walks dict values too

Overrides 4.3 (the meaning of `[*]` in `options.archive_contexts[*].title`) and WP-1's `_walk`. `archive_contexts`
is a dict `{link: context}` in every release, so `'*'` walks the items of a list and the values of a dict
(`messages._walk`; the path step of a dict value is its key). `library._context_titles` calls
`messages.decode(options, 'job_options')` directly. The stored options are never changed (I10): `decode` returns a
shallow copy in which only the containers on the way to a lifted title are copied, and `_context_titles` returns
the stored options object itself when no title was lifted. No sidecar is written inside options (unchanged).

### A3 (I-1) — G2 exemptions do not reach child processes

Clarifies 7.3 (exemptions). `messages.exempt(reason)` / `_sandbox.fixture_texts(reason)` act in the process that
enters them (process-wide, all threads); they are not passed to child processes. Child processes of a guarded test
(the collector worker) are guarded themselves and write their own `msg-guard-<pid>.jsonl`, which the runner reads,
so nothing they do escapes G2. No mechanism to pass exemptions to children is added: it would exempt a whole child
process for the life of the block, it would be program code that only tests use, and no test needs it. Rule: a
fixture whose data a child process handles uses user data that does not resemble app text; the case "user data
equal to an app text" is tested in-process with a narrow `fixture_texts(reason)` (as `test_messages_store` does for
a media titled `网页` and `test_messages_download` for the E2 `null` entry of the failed list). WP-3's change of one `test_collection.py` fixture link label from
`视频页面` (a stored link-kind value) to `YouTube 视频` follows this rule; no assertion refers to the label.

### A4 (I-1) — failed-list sidecars hold only the entries the lift cannot reproduce

Overrides 4.5 E1 for record kind `failed_entry` (spec conflict E1 vs 8.1). `test_golden_events.py` compares the whole
`failed-downloads.json` of each scenario with the byte-identical `events-1.6.0.json`, so plain E1 (an entry for every
persistable message) changes a golden. `unfinished._stored` therefore encodes each entry with `messages.encode` and
then drops every sidecar entry that is redundant: an entry is redundant when it decodes as a `stored` message whose
key and params equal, recursively, those of the legacy lift of the bare stored text (a `Raw` and a plain param of the
same text count as equal). `null` entries (E2) and foreign entries (E1b) are always kept, and so is an entry whose
text the lift reads differently (a 200-character cut inside a parameter, for example). Effect: an error that reads
back as the same message without a sidecar keeps the 1.7.0 bytes; on read it decodes with origin `legacy` instead of
`stored` and renders identically in zh and en. `messages.encode` itself is unchanged, and every other record kind
(the library) keeps its full sidecars: no golden compares library bytes, and full entries keep 4.5 E1 as written.
The rule stays scoped to the failed list (`unfinished.py`).

### A5 (I-1) — package deviations accepted as the spec

Clarifies the sections named; recorded so later packages do not undo them.

1. A `Message` handed to `logging` is logged as `messages.zh(message)` (WP-2, `unfinished._report`; 3.2 P6): a
   logging handler composes the record text (`msg + terminator`), which is a tripwire in a ready module. The written
   zh bytes are the 1.7.0 ones.
2. `library.PAGE_TITLE_KEYS` stays until WP-5 deletes it together with the `test_i18n_pages.py` assertion that pins
   it (6.4 lists it for WP-3; the test belongs to WP-5). `_shown_title` and its `localize` call are gone.
3. The archive file texts decode a title as the stored title it is before `show()`
   (`messages.decode({'title': title}, 'archive')['title']`, WP-3 `_decoded_title`; 6.2 `library.py` row): a plain
   keyless title is lifted like any stored text, a typed value is unchanged.
4. `library._stripped_reason` keeps the 1.7.0 `.strip()` of `last_reason.reason` for a cut `Message` (rebuilt with the
   stripped text, key / params / origin kept), because `messages.stripped` returns a `Message` unchanged (2.2).

No pending keys were recorded by WP-2 or WP-3; none was added at I-1.

### A6 (I-1 fix round 1, 2026-11-17) — which argument of a typed call is the G1b slot

Makes A1.1 precise (review finding RV-I1-01: the I-1 scanner made only the positional argument of `messages.raw` /
`messages.message_of` and the keyword `raw(text=...)` a slot, so a bare `raw(kind)` imported with
`from messages import raw`, and every form of `cut`, `masked`, `stripped` and `clauses`, escaped G1b). The G1b-only
slot of each typed call other than `msg` is its text argument, given positionally or by keyword, bare or as
`messages.<name>`, wherever the call stands (a dict value, an emit, a store writer, a return): `raw(text)`,
`cut(value)`, `masked(value)`, `stripped(value)`, `message_of(error)`, and for `clauses(parts)` each element of a
list / tuple / set display (the whole expression otherwise). `masked`'s `pairs` and `cut`'s `n` are no slots. G1f
still does not apply to these slots. The scanner (`i18n_tool.TYPED_VALUE`, `_typed_values`, `text_slots`) and
`G1SelfTests.test_the_i1_scanner_fixes` implement it; the live tree has no finding under the rule.

### A7 (I-2, 2026-11-17) — G5: call resolution, element-wise unpacking, numbers, scoped `sinks` entries

Overrides 7.9 (sink methods, the display-text grammar) and the `sinks` entry format of 7.2 / 7.9. Scanner defects
SD-1 … SD-7 (WP-4) and SD-W5-1 (WP-5), plus one found at I-2 (item 8), fixed in `verification/i18n_tool.py`
(`_Sinks`, `_library_sinks`, `_sink_entries_used`, `allow_stale`; `_Module.from_imports`, `_Scope.positions`), with
the self-test `G5SelfTests.test_the_i2_scanner_fixes` in `test_text_guard.py`. G1 is not changed (it keeps reading
the same bindings).

1. **Resolution of calls (SD-1).** A call reaches a function of the scanned UI modules only through its receiver:
   a bare name → a function of the same module, or the module-level function that `from <module> import <name>`
   names; `<module alias>.f` → the module-level `f` of that module when it is scanned, never a method (so
   `i18n.label`, `ui_theme.label` and `ui_theme.button` are not `StudioApp.label` / `.button`); any other receiver
   (`self.f`, `self.app.f`, `super().f`) → the methods named `f`. Sink methods, the return fixed point and the
   callers' checks all use this resolution. `ui_theme`'s factories stay sinks of their own (7.9).
2. **Unpacking (SD-2, SD-5).** A name bound by tuple unpacking or as a `for` target is display text when every
   element it receives is: by position in a tuple / list display; each element of an iterated tuple / list; the
   element of a generator or comprehension; `enumerate` (the counter is a number), `zip`, `sorted`, `reversed`,
   `list`, `tuple`; the element at that position of every `return` of a called UI function; both elements of
   `divmod(...)` (numbers). In any other case the whole value must be display text.
3. **Numbers (SD-3).** `sum(...)` (without a non-numeric start), `float(...)`, and `max(...)` / `min(...)` whose
   arguments (and `default=`) are all numbers or display text; a module-level int / float constant of a `src`
   module, imported by name or reached as `module.NAME` (`collector.MAX_LINKS`, `library.HISTORY_PAGE`). Event and
   row fields are **not** recognised by name: a field name does not prove a type (`files` is a list in `done` and an
   int in `item_done`; `skipped` is a text id in `item_done`), so such counts keep scoped allow entries.
4. **Aliases (SD-4).** Display and neutral calls are matched after resolving the module alias (`import urls as
   links`: `links.display` is `urls.display`) and for bare names imported with `from <module> import <name>`.
   `NEUTRAL_CALLS = {urls.display, urltools.display (the literal 7.9 names), os.fspath}`.
5. **Scoped `sinks` entries (SD-6).** Every `sinks` entry has the field `"function"`: the qualified name of the
   function in which it excuses its expression (`Class.method`, `function.inner`, `<module>`), or a list of such
   names. The same expression text at a sink of any other function is checked on its own. An entry without
   `function` excuses nothing. `line_hint` stays (an int, informational). `allow_stale` reports every
   entry-function pair that excuses no sink position G5 cannot prove (computed with every ready UI module, so sink
   methods resolve across the mixins) and every entry without `function`; so an entry the scanner no longer needs is
   stale, not only one whose text is gone.
6. **Functions used as values (SD-7).** A parameter of a function that is also used as a value — a bare name or
   `self.<method>` outside call position (a callback, a `partial` target, a dict of handlers), or a method that
   `getattr(obj, 'name')` / `getattr(obj, f'prefix{...}')` reaches (`StudioApp.handlers` is filled with
   `getattr(self, f'on_{kind}')`) — is never display text: no call G5 sees supplies it. The `on_<kind>` handlers
   therefore pass their event values to a sink through `show()` / `error_text()` or with a scoped entry.
7. **`except ... as name` (SD-W5-1).** Inside its handler the name is the exception object (not display text);
   outside the handler that binding is ignored (Python unbinds the name when the block ends) and the other
   bindings decide.
8. **Functions that return a parameter (found at I-2).** The return fixed point records which parameters a
   function's returned text depends on; a call of such a function is display text only when those arguments are
   (before, `def echo(text): return text` made `echo(item['title'])` display text, because the accepted parameter
   was dropped after the fixed point).
9. **Starred arguments of sink methods (I-2 fix round 1, RV-I2-01).** When a `*args` argument of a call to a sink
   method may fill a sink parameter's position (it stands at or before that position), each such starred argument
   is a strict sink position (`f(*) sink method`); `display(*x)` is `display(x)`, so the iterable's elements must be
   display text. Every `**mapping` argument of a call to a sink method is a strict sink position
   (`f(**) sink method`): the mapping expression itself must be display text (a parameter of the caller, which then
   becomes a sink parameter, or a scoped `sinks` entry). Before, item 1's rewrite skipped the position when a
   starred argument hid it, and no G5 version checked `**mapping`. Self-test lines in
   `G5SelfTests.test_the_i2_scanner_fixes` (`DownloadUI.starred`). The integrated tree has no such call that reaches a
   sink method (its starred calls go to `handle` and `_restore_offset`, which have no sink parameters), so its
   findings, allow entries and stale lists do not change.

Effect on the integrated tree: the sink positions G5 cannot prove fall from 120 to 66; the `sinks` entries from 79
to 50 (studio 8 → 4, ui_download 45 → 29, ui_log 2 → 1, collection_ui 5 → 3, library_ui 14 → 10, ui_settings 4 → 2,
library 1 → 1), each now naming its function(s); one entry is new (ui_download `value` in `DownloadUI.on_file`, the
`file` event's saved path, found by item 6) and two reasons were rewritten (ui_download `total`, studio `caption`)
because they described scanner limits that no longer exist. No `src` file changed for these fixes.

### A8 (I-2) — snapshot_ui feeds the engine's messages

Overrides 10.3 WP-6 ("`verification/snapshot_ui.py` (assertion (13), 7.6; nothing else, D17)") for two fixture lines,
as WP-4 recorded (open issue 2): `active_scene`'s unfinished-item log line and `scene_failures`' waiting reason feed
`engine.msg('core.engine.item_unfinished', code=1)` and `engine.msg('core.wait.pace')` instead of the plain 1.7.0
texts. The zh values are byte-identical, so the zh pictures do not change; with the ready UI modules the plain texts
are G2 R-text records in `test_i18n_snapshot.py` (tier all) and would show Chinese in the en snapshot. Made at I-2
because `--all` must be green with the guard on (8.3: tests feed messages for live events). WP-6's own edit of
`snapshot_ui.py` stays assertion (13) only; the D17 minors are untouched.

No pending keys were recorded by WP-4 or WP-5; none was added at I-2. WP-4's request for `test_ui_pages.py:30` was
already carried out by WP-5 (`messages.msg('core.library.temporary', ...)`), so nothing was applied at I-2.

### A9 (I-2) — two test fixes the merge of WP-4 and WP-5 needed

Clarifies 7.5 (census) and 7.9 (which modules G5 runs on in a package test). Both appear only when WP-4's and WP-5's
modules are ready in one tree; neither changes a `src` file or an assertion.

1. `test_messages_ui_download.CensusTests.feed` removes its history job after reading the stored fields. The census's
   `Raw` and plain legs store an app text (`正在检查官方更新…`) as a titled job's media title inside
   `messages.exempt(...)`; with `library_ui` ready the history page showed those rows later, outside the exemption
   (the next legs, and the later tests of the file through the shared smoke history), and G2 recorded 122 R-text
   records at `library_ui.history_title`. This is the A3 rule: user data that imitates an app text may exist only
   inside a narrow exemption. The assertions are made before the row is removed.
2. `test_messages_ui.ReadyTests` runs G5 over all six UI modules and asserts that none of its findings lie in its
   three files (as `test_messages_ui_download` does). On its three modules alone a call into another mixin
   (`added = self.add_links(...)`, ui_download) cannot resolve, so a position that is display text on the whole
   window would need an allow entry that `allow_stale` (computed on the whole window, A7.5) reports as stale.

### A10 (I-2 fix round 1, 2026-11-17) — G5 checks `*args` / `**mapping` passed to a sink method

Adds item 9 to A7 (overrides 7.9 as A7 does): a starred argument that may fill a sink method's sink parameter, and
every `**mapping` argument of a call to a sink method, are strict sink positions. Review finding RV-I2-01; no `src`
file, allow entry or catalog key changes.

### A11 (WP-6, 2026-11-17) — staging removed, the release gate, the carried scanner minors

Overrides 7.2 (G1b in typed-call arguments, G1d localize), 7.8 (staging), 7.9 (G5 grammar and table rows), 10.3 WP-6
and 10.4 (files touched) where named. Implemented in `verification/i18n_tool.py` with self-tests in
`test_text_guard.py` (`G1SelfTests.test_the_wp6_typed_call_compositions`, `test_the_wp6_sink_fixes`,
`TreeTests.test_the_release_gate`, `test_the_gate_sees_a_flag_a_pending_key_and_a_stale_entry`).

1. **Compositions inside a typed-call argument (RV-I1-02).** The G1b check of the text argument of `raw`, `cut`,
   `masked`, `stripped`, `clauses` and `message_of` (A1.1, A6) looks into what is composed there: an f-string hole,
   an operand of `+` / `%`, the receiver and the arguments of `.format` / `.format_map` / `.join`, the receiver and
   the arguments of a str method (`strip`, `replace`, `lower`, ...), the value of a slice, the elements of a tuple /
   list / set display and the branches of a conditional (`_Taint.composed`). `raw(f"{kind}: {url}")`,
   `messages.raw(kind + url)`, `cut(f"{kind}{url}", 70)`, `clauses(parts + [kind])`, `raw("{}: {}".format(kind, url))`,
   `raw(' '.join([url, kind]))`, `raw('%s %s' % (kind, url))` and `raw(kind.strip()[:20])` with an enum-bound `kind`
   are G1b findings; engine text sliced and stripped stays accepted (`raw(line[6:].strip()[:200])`), and G1f still
   does not apply to these slots. Other slots are checked as before.
2. **`**mapping` and returned parameters (RV-I2-02).** A call of a function whose returned text depends on a
   parameter (A7.8) is display text only when that parameter receives display text; with a `**mapping` argument a
   parameter the call does not pass explicitly may come from the mapping, so the call is then not display text
   (`echo(**event)`; `echo(i18n.t(...), **extra)` stays display text). Extends A7.8 and A10.
3. **`*parts` vararg sink parameters (RV-I2-03).** When a sink parameter of a sink method (or a parameter the
   returned text depends on) is the function's `*vararg`, every positional argument of a call at or after its
   position, and every starred argument before it (it may spill into the vararg), is a strict sink position
   (`f(*parts) sink method`). Before, `_argument` found no position for a vararg and the callers were never checked.
4. **Table rows cell by cell, numeric clock (RV-WP5-1).** A table-value sink (`Treeview.fill` values, `insert` /
   `item(values=)`) whose value is a call of UI functions that return only tuple displays is checked cell by cell
   in those functions; a cell that is not display text is reported (and allow-listed) at the cell, with the row
   function as `function`. The callee is also found through a local name bound only as a `for` target over a
   tuple / list display of function names (`for tree, rows, render in [(…, …, history_row), …]: render(row)`,
   `library_ui.refresh_library`). `time.strftime` with a constant format made only of numeric directives and
   separators (`%d %m %y %Y %H %M %S %j %I`, `- : / .` and spaces) is display text (language-neutral digits), so
   `library_ui.clock` is proved. `library_ui.json`: the whole-row entries `history_row(row)` @
   `LibraryUI.refresh_history` and `render(row)` @ `LibraryUI.refresh_library` are removed; the cells G5 cannot
   prove get scoped entries: `history_status(row)` @ `history_row` (the stored status id of a status 1.8.0 does not
   know, shown as it is) and `row['pending']` @ `subscription_row` (an int; added to the existing entry's function
   list). G5 on the tree: 0 findings; stale: none.
5. **`engine._sentence` (the WP-2 / I-1 / I-2 note).** `_sentence` is outside the protected blocks (it is called
   by sanctioned node S4, whose expression `msg('core.engine.cannot_start_engine', error=_sentence(error))` is
   unchanged). An error that carries a `Message` now keeps it: `cut(message, len(stripped zh text))` instead of
   `str(error).rstrip()...` (a tripwire in a guarded module); the zh text is the 1.7.0 one, en shows the whole
   message; other errors (the `subprocess.Popen` OS errors) take the 1.7.0 path unchanged.
   Test: `test_english_replay.SentenceTests`; `test_protected_blocks` unchanged and green.
6. **Staging removed (7.8).** No `src` module declares `MESSAGES_READY`; `messages.TRANSIT` is
   `{'messages', 'i18n_legacy', 'i18n'}` (no `TRANSIT_UNTIL_WP6`, no `RUNTIME_ALWAYS`, no readiness check): a runtime
   check records a violation whenever its producer frame is a `src` module outside `TRANSIT`, so `ui_theme` is the
   display tripwire. `i18n_tool`: `is_ready` and `STATIC_ALWAYS` are gone; `g1_modules()` is every `src` module
   outside `G1_EXCLUDED`; `sinks_scan()` and `allow_stale` use every UI module; G1d reports any `localize`
   definition or call in any module and `g1_scan` any localize call line (1.7.0 had 52, pinned on
   `release-sources/1.7.0`); new `ready_flags()` and `release_gate()` (flags, staging names left in
   `messages.py`, localize lines, `pending_keys`, `pending` literals, stale entries of every allow file, G1, G5).
   The tests that encoded the staging change with it although 10.4 does not list them for WP-6 (they asserted the
   transitional state): `test_messages_download.py`, `test_messages_ui.py`, `test_messages_ui_download.py` assert
   that the flag is gone and the module is in `g1_modules()` (one assertion replaces one); `test_messages.py`
   GuardTests take as "not guarded" a probe module outside `src` (no producer frame) instead of one without the
   flag, and the ui_theme case asserts the recorded display tripwire (and no record with the pre-WP-6 set);
   `test_reverse_maps_unchanged_until_wp6` became `test_the_reverse_maps_left_i18n_maps`; `LiveLocalizeTests` are
   deleted with `localize` (6.4; their legacy-reader counterparts stay).
7. **Reverse sections and `localize` (6.4).** The generator (`i18n_tool.catalog`) writes `i18n_maps.py` with
   `GROUPS`, `RESERVED` and `NEW_KEYS` only (`NUMERIC`, `RECURSE`, `LOCALIZE_EXTRA`, `NEUTRAL_FLAGS` and
   `localize_keys` left the tool); the eight catalogs it rewrites are byte-identical. `i18n.py` lost `MAX_DEPTH`,
   `_Template`, `_Index`, `_index`, `_get_index`, `_allowed`, `_localized`, `_localize_in`, `localize` and the
   now unused imports `functools` / `re`; `i18n.json` lost the stale `'[代理]'` token entry. `test_i18n_catalog.py`
   reads the core's `msg()` keys and `i18n_legacy_data.TEMPLATES` where it read `LOCALIZE` / `VARIANTS`, and
   compares the whole generated `i18n_maps.py`. `i18n_legacy.py` had no transitional code; it is unchanged.
8. **Generic `sinks` entries (RV-WP5-2 / RV-WP4-1).** Checked entry by entry with the scanner (every entry-function
   pair and the positions it excuses): each bare-name entry (`value`, `name`, `text`, `label`, `success`,
   `unfinished`, `skipped`, `index`, `total`, `new`, `url`, `caption`, ...) excuses only positions of that same
   variable inside the one function it names, each the value its reason describes (several positions only where
   the same count is passed to several `t()` calls of one function: `success` 4 and `skipped` 3 in
   `DownloadUI.on_done`, `unfinished` 2, `index` / `total` 2 in `DownloadUI.on_item`, `name` 2 in
   `LibraryUI.save_subscription`). None needed tightening; none was removed for this reason.
9. **G4b / G6 (7.6) as built.** `test_english_replay.py` (tier default) renders the page models with the page code
   itself on stub widgets (the same harness source runs in a 1.7.0 child process and in the guarded 1.8.0 test
   process): history rows and notes, subscription rows and notes, the pending table, the collect table of every
   collect job (`load_collection_record`, `render_collection_links`), the archive table (`refresh_archives`), the
   unfinished rows and the reason dialog (`_append_tail`, `show_row_reason`). The producers are the frozen code
   (worker_main with a fake Scrapling fetcher, Runner with scripted `_run_once` as `test_messages_download` does);
   1.5.1 runs no Runner (7.6 names 1.6.0 and 1.7.0) but writes its download job and its failed list. A raw engine
   line that quotes app text (D-H-4) is not replayed: it is recognisable by design and is covered by
   `test_messages_download` / `test_messages_ui_download` inside a narrow exemption. The archive files are checked
   in en (1.8.0 writes them; on-disk names stay Chinese, D10). The producers patch the collector's `ca_bundle`:
   the frozen 1.5.1 collector writes a certificate cache beside its program folder or at a drive root when the
   folder path is not ASCII.
10. **En snapshot assertion (13) (7.6) as built.** The user data is an explicit `USER_DATA` tuple of
   `snapshot_ui.py` that `user_data()` checks (every entry is part of a string literal of the file, no entry is an
   app text `i18n_legacy.recognise` knows), plus the fixture's own save and archive folders read from the window;
   user-data substrings are taken out of a text before it is checked (stricter than "a text that contains user
   data is exempt"); cut texts are read whole. (13) also runs in the active-scene sidebar and finished-job checks.
   Self-tests in `test_i18n_snapshot.ChineseOutsideUserDataTests`.
11. **verify_release (10.3).** Step 5 adds `check_message_evidence`: `report.msg_guard` checked at least one file
   and has `violations == 0`, no allow file holds a `pending_keys` entry or a `pending` literal, and every file of
   `verification/release-sources` equals its pin in `golden/release-sources.json` (nothing changed, missing or
   added); the receipt `release-<version>.json` records `msg_guard`, `pending_keys` and per version the file count
   and the SHA-256 of its (name, SHA-256) list. Tests: `test_release_tooling.GateMessageEvidence`.

### A12 (WP-6) — a test-harness hang the full run showed

`test_polish_pages.ResumeTests.test_resume_runs_both_formats_of_a_link` hung in the first `--all` run of the release
candidate (and again alone, and directly): a stack dump showed the Runner thread of the test calling
`tkinter.font.Font.__del__` — a font of the window an earlier test had destroyed, sitting in a reference cycle, was
collected by a garbage collection that the Runner thread happened to trigger, and the Tcl call into the destroyed
interpreter waits forever. Only the order of the earlier tests decides where the collection runs (each class alone
passes; int2 passes by chance with its other allocation pattern). It cannot happen in the program, which has one
interpreter for its whole life and whose worker threads reach a live one. Fix in the test file (WP-5's, changed by
WP-6 because `--all` must be green): `Base.close` runs `gc.collect()` on the window's thread after `destroy()`. No
assertion changed; no `src` file changed.

### A13 (final review 1, 2026-11-17) — G5 module texts and Chinese literals, G1d boundary names, the `explain_error` fallback

Overrides 7.9 and A7.3 (the G5 display-text grammar), 7.2 G1d, and the fallback of D-F28 (3.2). Review findings F1,
Q3 and ZH-1 of the final review of the release candidate; implemented in `verification/i18n_tool.py` and
`src/engine.py`, with the self-tests `G5SelfTests.test_module_texts_and_chinese_literals_at_sinks`,
`G1SelfTests.test_the_boundary_modules_are_reached_by_their_names` (`test_text_guard.py`) and
`test_english_replay.ExplainErrorTests`.

1. **G5: module-level names and Chinese literals (F1).** A name that is bound at module scope is display text only
   when every binding is an int / float constant (A7.3); a module-level `str` (an h4 UI constant such as
   `library_ui.PICK_ARCHIVE`, or any other text) or any other module-level value at a sink is a finding unless it is
   shown with `show()` / `t()` or a scoped `sinks` entry excuses it. Before, `_Sinks._name_is_display` climbed to the
   module scope and accepted any string literal there, so an h4 constant could reach a dialog, a sink method, a table
   value or `append_log` and show Chinese in English with no guard failing. In addition, a string literal (or an
   f-string literal part) that contains a CJK character is not display text at a sink: 7.9 leaves literals to G1,
   but the G1b slots do not cover a messagebox message, a sink-method argument, a table value or `append_log`, so an
   allow-listed Chinese literal written straight into one of them passed both guards. At the value sink
   `append_log` (7.9: a bare value passes, its `show()` renders a message) text fixed in the source — a string
   literal, or a name whose every binding is a string literal — is not a bare value: `show()` passes it unchanged,
   so it must be display text as at any other sink (`self.append_log(NO_PENDING)` is a finding). Effect on the tree: none (G5
   0 findings, no allow entry added, none stale); the mutation that puts `PICK_ARCHIVE` back into the
   `LibraryUI` archive dialog now fails `test_text_guard` (`test_g5_is_clean_on_every_ui_module`, the release gate).
2. **G1d: the boundary modules are reached by their names (Q3).** G1b, G1c and G1d match `messages` and `i18n` by
   these names (`messages.msg`, `messages.encode`, `i18n.t`) and the names imported from `messages` by their own
   names. G1d therefore also reports, in every module G1 scans: `import messages as X` / `import i18n as X` (X another
   name), `from messages import <name> as <other>`, the module object of an imported `messages` / `i18n` used as a
   value (`M = messages`, `getattr(i18n, 't')`, passing it to a call) and `importlib.import_module('messages')` /
   `__import__('i18n')` (`i18n_tool._g1d_aliases`). The tree and the frozen 1.5.1 / 1.6.0 / 1.7.0 sources have none
   of these forms, so no finding and no allow entry changes.
3. **`explain_error` fallback for a Message (ZH-1, corrects D-F28).** The fallback text of a `Message` is the 1.7.0
   expression `_EXTRACTOR_PREFIX.sub('', zh(text).strip(), count=1)[:70]`. When that equals `zh(text)[:70]` the
   result is `cut(text, 70)` as before; otherwise (edge whitespace, or an `[extractor] id: ` prefix at the start of
   the text, which a keyless stored error that the legacy reader lifts can have) the result is a `Message` with that
   zh text and the input's key, params, origin and entry, rebuilt the way `cut()` and `library._stripped_reason`
   rebuild one, so en still shows the whole message. Raw and plain texts are unchanged. The history note of such a
   stored row (`library_ui.history_note_text`) is again byte-identical to 1.7.0 in zh (8.1 / 8.2); the test compares
   with the frozen 1.7.0 `explain_error` for the review's seven inputs and two plain controls.
4. **Notes (no rule change).** `src/library_ui.py` keeps its CRLF line endings (the 1.7.0 file is CRLF in git); the
   test `TreeTests.test_line_endings_kept_from_1_7_0` checks that every `src` file 1.7.0 had keeps 1.7.0's line-ending
   style (Q1). `架构说明.txt` section 十三 marks the 1.7.0 statements about `localize`, the `i18n.py` imports and
   the protected blocks as history and names `verification/i18n_inventory.json` as the generator's source (Q2). The
   header line of the generated catalog modules, which names `docs/1.7.0/spec/strings-inventory.json`, stays as it
   is: changing the generator's header would rewrite seven catalogs that are otherwise byte-identical to 1.7.0
   (section 0, D17); the line records where the 1.7.0 catalogs were generated from.

### A14 (final review 2, 2026-11-17) — G5 text fixed in the source, show() of it, G1b `.values()` / `.items()` / defaults, G1d star imports and function values

Overrides 7.9, A7.3 and A13.1 (the G5 display-text grammar and the value sink), 7.2 G1b and G1d, and A13.2. Review
findings F1, F2, Q3 and Q5 of the second final review of the release candidate; implemented in
`verification/i18n_tool.py`, with the self-tests `G5SelfTests.test_fixed_text_through_names_defaults_and_show`,
`G5SelfTests.test_module_texts_and_chinese_literals_at_sinks` (one line flipped), and
`G1SelfTests.test_star_imports_and_messages_functions_as_values` /
`G1SelfTests.test_enum_values_items_and_parameter_defaults` (`test_text_guard.py`). Finding Q4 is a document fix
(`架构说明.txt`), no rule change.

1. **G5: text fixed in the source is followed to the value sink, and a parameter default is checked (F1).** A13.1
   took only a string literal, or a name whose every binding is one, as text fixed in the source at `append_log`.
   G5 now collects the *fixed leaves* of a value (`_Sinks._fixed_leaves`): the string literals it may give,
   followed through local names (also loop and unpacking targets, element by element where the element can be
   told: `enumerate`, `zip`, tuple displays), module-level names (also of another scanned module: `ui_log.ZH_START`,
   and `from` imports), parameter defaults, conditionals and `or` / `and`, containers and their subscripts, `.get` /
   `.values()` / `.pop` / str methods, `self.<name>` / `cls.<name>` (a class attribute, or any `self.<name> = ...`
   in the module), and the returns of the UI functions and methods a call reaches; `show()` / `error_text()` give
   what they are given; `t()`, the other display and neutral calls and the number builtins give none. A leaf is
   display text only when it is a literal written inside a function and has no CJK character; a module-level or
   class-level text is never display text (A13.1), a Chinese literal never is (A13.1). At the value sink
   `append_log` a bare (not composed) value passes when every fixed leaf is display text and is a finding
   otherwise; a composed value must be display text as before. A parameter is display text "checked at its
   callers" (7.9) only when its default, if it has one, is display text itself (a call that leaves the argument
   out supplies the default, and no caller check sees it); `_param_default`. The finding is reported at the sink
   inside the function. Effect on the tree: G5 0 findings, no allow entry added, none stale; the review's
   mutations M10 (a sink-method default `text=PICK_SUBSCRIPTION`), M11 (`append_log(PICK_ARCHIVE if ... else
   PICK_SUBSCRIPTION)`) and M11b (a local alias) are now G5 findings.
2. **G5: show() / error_text() of text fixed in the source is not display text (F2).** 7.9 counted
   `messages.show(x)` as display text whatever x is; but show() returns a plain str unchanged, so
   `show(PICK_ARCHIVE)` showed Chinese in English. `show(x)` / `error_text(x)` (also bare, from `from messages
   import`) is display text only when every fixed leaf of x is display text (1). The self-test line
   `self.append_log(messages.show(NO_PENDING))` is now a FLAG line. Effect on the tree: none; the review's mutation
   M12 (`messages.show(PICK_ARCHIVE)` in the archive dialog) is now a G5 finding.
3. **G1b: `.values()`, `.items()`, `.copy()` and parameter defaults (Q5, F1).** An enum source reached through
   `.values()`, `.items()` or `.copy()` is tainted as through `.get` (A1.3: every use of its values in a text slot is
   a G1b finding); of the pairs `.items()` gives, a target that takes only the key (`for key, label in
   LABELS.items()`: `key`) is not tainted, the value is. A parameter's default is a binding of the parameter
   (`def page(url, kind=KIND): return {'title': kind}` is a finding). Effect on the tree: none (every `.values()` /
   `.items()` use of an enum source is outside a text slot).
4. **G1d: star imports and messages functions used as values (Q3).** In addition to A13.2, G1d reports in every
   module G1 scans `from messages import *` and `from i18n import *` (a star import brings in names the guards
   cannot list), and a public module-level function of `messages.py` (or a typed / codec name) used outside call
   position, as a name imported from messages or as `messages.<name>`: bound to a new name (`fn =
   messages.encode`, `make = msg`, `shorten = cut`), passed on (`map(raw, ...)`) or stored. Classes
   (`isinstance(value, messages.Raw)`) and calls stay fine. Effect on the tree and on the frozen release sources:
   none.
5. **Boundary (no rule change).** G5 does not follow values out of modules it does not scan (a core function's
   return is a Message or data; G1 forbids literal producers there) or the attributes of arbitrary objects (event
   values, records); G1 and the G2 run-time checks cover the core texts that reach them. README point 2 and L-1 stand with this grammar (superseded: A15.8.8 and L-1, L-15).

### A15 (converge round, 2026-11-18) — the guard contract: sources, flows, sinks; its generator; the English display check

Overrides 7.2 (G1b slots and bindings, G1d, G1f), 7.3 (R-text at `show()` / `error_text()` / `t()` in English), 7.9
(the G5 sinks and the display-text grammar), A1, A6, A7, A11, A13, A14 and the last sentence of A14.5, and the
sections named in A15.8. Findings F1 (blocker) and F2 (major) of the third English review, Q6 (major) and Q7–Q10
(minor) of the third quality review, and the minors still open (A15.8). Implemented in `verification/i18n_tool.py`
(`_Flow`, `_Taint`, `g1_slots`, `_Sinks`, `SINKS`, `THEME_SINKS`, `_library_sinks`), `src/i18n_legacy.py`
(`recognise(..., english)`) and `src/messages.py` (`_check_shown`, `_checked_t`, `_render`); proved by the generator
`verification/guard_contract.py`, run by `test_text_guard.ContractTests`. The scanners no longer grow by example:
a form is inside the contract or it is listed in L-15.

**A15.1 How to read it.** A form is *inside* when its value comes from a SOURCE of A15.2, every step from the source
to the sink is a FLOW of A15.3 and the place it reaches is a SINK of A15.4. Inside, the guard reports the form
(unless a scoped allow entry with a reason excuses it), and the generator proves that for every combination
(A15.6). Every form that is not inside is in L-15 (section 12), with the run-time check that covers it on the tested
paths and what that check does not cover. (The two sentences on the generator and L-15 are superseded by A16.7: the
contract is the listed ids; the generator has cases for each, and L-15 lists the outside forms the reviews found.)

**A15.2 SOURCES** (text fixed in the source that must not stand in a text slot or reach a sink unconverted).

| id | source | G1b (text slots) | G5 (sinks) |
|---|---|---|---|
| S1 | a str literal its module's allow file lists (any category) | source | — (S2 / S3) |
| S2 | a str literal with a CJK character, also a literal part of an f-string | G1a finding, or S1 when allow-listed | source |
| S3 | a str literal written at module level or in a class body (a module or class constant), CJK or not | S1 when allow-listed | source |
| S4 | a constant of another scanned module: `module.NAME`, `from module import NAME` | S1 / S5 there | source (S2 / S3 there) |
| S5 | an enum source (7.2: a module-level name of a src module whose value holds an allow-listed literal, transitively) that the flows cannot follow exactly (records, calls); its values, items, elements, the `.label` / `.mode` of its records; the `formats.by_*` records | source | source |
| S6 | `messages.template(key)`: a zh catalog template as plain text | source | source |

Never a source, i.e. display text: a str literal written inside a function without a CJK character; a number; `i18n.t`,
`label`, `sep`, `choices`; `msg()` and `value()` (their key decides the text; `msg()`'s params are slots of their
own); the typed calls and `show()` / `error_text()` of a value without a source (F9, F11).

**A15.3 FLOWS** (superseded in part by A16.5: `_Flow.leaves` finds the sources everywhere, and the strict rule adds the
display grammar, a second implementation; the original wording: one implementation, `_Flow.leaves`, for G1b in every slot, for G1f, for the G5 value rule and for
the G5 proofs at strict sinks).

- **F1 names**: every binding of a name in its scope — assignment, augmented and annotated assignment, `:=`, `for`
  and comprehension targets (each comprehension its own scope), `with … as` —; a parameter rebound in its function;
  aliases, transitively. A parameter that the function's first statement replaces by its shown value (`line =
  messages.show(line)`) holds that shown text afterwards (the other bindings must be display text with it).
- **F2 module names**: the module-level names of the module; `module.NAME` of an imported src module and `from
  module import NAME`. (An imported module's name as a function sees it: a class attribute of the same name does not
  hide it.)
- **F3 attributes**: `self.X`, `cls.X`, `<Class>.X` and `getattr(self | cls, 'X'[, default])`: the class-body
  bindings of X and every assignment to `self.X` / `cls.X` / `<Class>.X` — also as an element of a tuple / list
  target, augmented, and `setattr(self, 'X', value)` — and what a mutation puts into it (F5); G1: of the module; G5:
  of all six UI modules (the window is one class made of mixins).
- **F4 conditionals**: both branches of `a if c else b`; every operand of `or` / `and`.
- **F5 containers**: tuple / list / set displays (by index, by slice, by iteration; a starred element: any element);
  dict displays (by constant key, `.get` / `.pop` / `.setdefault` with their defaults, `.values()` / `.keys()` /
  `.items()` / `.popitem()`, iteration (the keys), `**` spreads); comprehensions (the element; a dict
  comprehension's keys and values); and what a mutation puts in: `x.append(v)`, `.add(v)`, `.insert(i, v)`,
  `.extend(vs)`, `x[k] = v`, `x[k] += v`, `.setdefault(k, v)`, `.update(...)` — of a local name, or of `self.X`
  across the window. A dict standing whole in a G1b slot carries nothing (its text-key values are slots of their
  own); at a G5 sink all of it. A selection deeper than three steps is taken whole.
- **F6 unpacking**: `a, b = …`, `a, *b = …`, nested targets, element by element where the element can be told
  (displays, `enumerate`, `zip`, `.items()`), the whole value otherwise.
- **F7 iteration wrappers**: `enumerate` (the counter is a number), `zip`, `map` (the elements; a proof needs
  `map(str, …)`), `filter`, `sorted`, `reversed`, `list`, `tuple`, `set`, `frozenset`, `iter`, `next` (and its
  default), `dict` (its argument and keywords), `min` / `max` (one iterable, several arguments, `default=`).
- **F8 composition**: f-strings (holes and literal parts), `+`, `*`, `%`, `.format` / `.format_map` (receiver and
  arguments), `.join` (receiver and every element), the str methods (receiver; the inserted text of `replace`,
  `ljust`, `rjust`, `center`), `str()`, `format()`, `re.sub` (the replacement and the string), slices. `repr()`
  gives no text (the 2.1 form of an id).
- **F9 calls**: the `return` / `yield` values of the functions and methods of the scanned modules (G1: the module,
  and `module.f` / `from module import f` of another src module; G5: the six UI modules, resolved as A7.1, and the
  module-level functions of library for its filetext sinks); a lambda called in place or through a local name; a
  parameter in the returned value stands for what the call passes. The typed calls `raw`, `cut`, `masked`,
  `stripped`, `clauses`, `message_of` and `show`, `error_text`, `zh` carry their text argument (for G1f they stop
  it: A1.1).
- **F10 parameters**: a parameter whose value a sink position carries (through F1–F9) is a sink parameter of that
  kind, and what every call passes to it — positionally or by keyword, a starred argument, a `**mapping`, a
  `*param` / `**param`, the callback forms of A15.4 — is a sink position of the same kind; its default is a binding
  (A14.1, A14.3); a fixed point, so any wrapper depth. G1: within the module, and through the module-level
  functions of the other src modules (not the message API `messages` / `i18n` / `i18n_legacy` and not `ui_theme`,
  whose arguments have rules of their own); a record slot (an emit's payload, a store writer's positional or
  non-text keyword argument) passes on only its text fields (no selection under a key outside TEXT_KEYS); a
  positional argument for a TEXT_KEYS-named parameter is a slot when the parameter reaches a slot, or belongs to the
  message API or ui_theme. G5: across the window; a function that is also used as a value (A7.6) keeps its strict
  parameters out of display text, and its value-mode parameters are checked at the calls G5 sees.
- **F11 wrappers**: `show()` / `error_text()` — their argument is a value position (A15.4); `t()` / `label()` /
  `sep()` / `choices()` — display text (`t()`'s params are sinks); `urls.display`, `os.fspath`, `Path(...).name` —
  language-neutral data, so no source may reach them.

**A15.4 SINKS** (`i18n_tool.SINKS`, by kind; *strict*: the value must be display text by the grammar of 7.9 / A7 /
A15.3; *value*: no source of A15.2 among its leaves, and every composition among them (f-string, `+`, `%`, `.join`,
`.format`) display text as a whole).

- **G1b text slots** (7.2, A6): a dict value under a TEXT_KEYS key; `x['<text key>'] = …`; a keyword argument with a
  TEXT_KEYS name; a positional argument for a TEXT_KEYS-named parameter (F10); every argument of `emit` / `say` /
  `log`, of a store writer and of `unfinished.merge` / `save` (record slots: their text fields); a `raise` argument;
  every keyword param of `msg()`; the text argument of the typed calls (G1b only, A1.1); the parameter slots of F10.
  **G1f**: the slots of the core modules and the raise / emit / store-writer slots of every module, through F1–F4,
  F9 and F10 — a composition the slot's value *is* (not a container element, not a part of another composition).
- **G5 strict**: `configure` / `config(text= / placeholder= / placeholder_text=)`; a `ctk.*` / `tk.*` / `ttk.*` /
  `tkinter.*` constructor (`text=`, `placeholder=`, `placeholder_text=`, `values=`); the ui_theme API —
  `THEME_SINKS`: `label` / `button` / `chip` / `checkbox` / `switch` text, `entry` placeholder, `option_menu` /
  `segmented` labels, `option_menu` values and `tabs` names when no labels are given, `tabs` labels, `setting_row`
  title and description, `empty_state` text, `table` empty, `fit` / `fit_alternatives` text and alternatives
  (`THEME_NOT_SHOWN` names the measured parameters; a test fails on a text parameter of ui_theme that neither lists);
  `Chip.set`; `heading(text=)`; `title()` / `wm_title()`; `set_empty`; `Treeview.fill` values (cell by cell,
  A11.4); `insert(values=)` / `item(values=)`; the insert of a text / log / box widget; a menu item's `label=`
  (`add_command`, `add_checkbutton`, `add_radiobutton`, `add_cascade`, `insert_*`, `entryconfigure`);
  `messagebox.*` title, message and `detail=`; `filedialog.*` `title=`, `initialfile=`, `filetypes=`; every keyword
  value of `i18n.t`; library's filetext `t()` keyword values; the strict sink parameters (F10).
- **G5 value**: `append_log(line)` and `append_log(line=…)`; the argument of every `show()` / `error_text()` call
  wherever it stands; the column headings of `ui_theme.table()` (element 1 of every column spec); the values of a
  `**mapping` given to `t()`; the value-mode sink parameters (F10).
- **Calls**: every `ast.Call`, and the callback forms, which call their callable with what they hold:
  `<widget>.after(ms, f, *args)`, `after_idle(f, *args)`, `functools.partial(f, *args, **kw)`,
  `threading.Thread(target=f, args=(…), kwargs={…})`.

**A15.5 The English display check (the run-time backstop).** Two ways were evaluated.
(a) *messages side, implemented* (`messages._check_shown`, `messages._checked_t`, `i18n_legacy.recognise(...,
english=True)`): in a guarded test process whose language is English, R-text at `show()` / `error_text()` and the
`t()` parameter rule (plain `str` params) count, besides the texts of 7.3, the zh text of each UI key whose template
has at least four CJK characters of its own: whole, as a template instance, or by a run of six CJK characters that
the English catalog does not contain (an index built on the first English check, so a process that checks only zh
texts does not pay for it). A key whose English text is its zh text (the language name `简体中文`) is not
counted. The UI-text exclusion of 7.3 stays for zh, where `t()` gives Chinese UI lines that pass through `show()`.
`Raw` text is not checked there (its type is the producer's claim of user data), and `messages.exempt` blocks count
as before. This catches at run time, on every tested path, the defect of F2: a fixed UI text (an h4 constant such as
`library_ui.PICK_ARCHIVE`) that reaches `show()` / `error_text()` (so also `append_log`, which shows its line) or a
`t()` parameter, and so shows Chinese in English (`test_messages.GuardTests.test_a_zh_ui_text_shown_in_english`).
The suite needed no new exemption for it (A15.7).
(b) *a Tk-level sentinel in `_sandbox`* (every text a widget receives in an English test process): not implemented.
At the Tk level the value's type is gone, so user data of the fixtures that equals or contains an app text (L-3,
L-11, the media titled `网页`, the D-H-4 engine line) cannot be told from a defect, and the widgets show such data
from Tk callbacks and `after()` timers outside the `exempt` blocks that cover the producing code, so every such test
would need a new, wider exemption; it would also wrap private tkinter / customtkinter internals (`Misc._options`,
`ttk._format_optdict`, `Text.insert`, `wm_title`, the dialog commands) in every test process. What (b) would add —
a value that reaches a widget without `show()` / `t()` through a form outside the static contract — is covered on the
captured pages by the en snapshot assertion (13) (every text of every mapped widget, user data taken out), and on
the other tested paths by (a) and the display tripwire wherever the value is a `Message` or `Value`.

**A15.6 The generator** (`verification/guard_contract.py`; its tables and counts as of A16: A16.7). G5: 12 sources (8 bad, 4 good: an `i18n.t` text, `show()`
of a core value, a number, `show()` of `raw()` user data) × 56 flows × the 22 kinds of `SINKS` (the two sink-method
kinds as a wrapper's parameter), then per sink kind and source a rotating flow at wrapper depth 2 and 3,
positionally and by keyword, with and without `show()`, and the source straight into `show()`: 16 877 cases. G1b:
10 sources (7 bad, 3 good: a `msg()`, `raw()` of user data, a number) × 56 flows × 10 slot classes, then for two slot
classes each source at wrapper depth 1–3, positionally and by keyword: 5 580 cases. A flow that needs another module
of the window (a mixin) is generated only for the classes that have one. Each case is one method with its own
helpers, attributes and constants; a bad case must be flagged within its own lines, a good one must not be. The
reviewers' probe forms of round 3 are named cases: the 35 methods of `probe_r3` (32 `flag_*`, 3 `ok_*`) and the 22
G1b lines of `probe_enum` / `probe_enum3` / `probe_q35` (20 `E*` / `N*` flagged, 2 `C*` not), 57 in all. A scanner
without the attribute flow F3 (the mutant of `test_the_generator_sees_a_missing_flow`) misses only cases that need
F3: the attribute flow, and the class-attribute sources (the self-test of the self-test). Building the generator
found two gaps of the new scanner itself, both closed before the counts below: a str literal in a class body was
taken as display text at a strict sink (816 cases), and library's filetext `t()` sinks had no parameter fixed point.
Counts, bad cases flagged / good cases clean:

| scanner | G5 bad | G5 good | G1b bad | G1b good | probe forms wrong |
|---|---|---|---|---|---|
| int2 (`build\v180\int2`) | 5 539 / 11 263 | 3 438 / 5 614 | 784 / 3 914 | 1 666 / 1 666 | 52 / 57 |
| this release candidate before A15 (A14) | 7 272 / 11 263 | 3 438 / 5 614 | 880 / 3 914 | 1 666 / 1 666 | 34 / 57 |
| with A15 | 11 263 / 11 263 | 5 614 / 5 614 | 3 914 / 3 914 | 1 666 / 1 666 | 0 / 57 |

**A15.7 Effect on the tree.** G1 and G5: 0 findings; no stale entry. In src the stricter contract found four
compositions in text slots, at seven places, none of them a shown text. `engine._present_done` keeps a media file
path as `kept.append(str(path))` for the `file` event; it is now `os.fspath(path)` (A1.4; the same string for the
`Path` it is). `naming.finalize` returns its file path as `str(path)` (four places) and `str(candidate)`, and the
runner forwards it through the generic `self.emit(kind, value)` as the `file` value and the `media` filepath; these
are `composed` entries in `naming.json`, because `str()` stays: finalize never raises, and a test passes it `17` and
`None`. `Library.rename_job`'s `title = str(title)` is a `composed` entry in `library.json` (`str()` of a `Message`
/ `Raw` returns the object, 2.3). The more precise grammar proves nine positions that had `sinks` entries, which were
removed as stale (`ui_download`: `forms[0]`, `text`, `url`, `self.metrics_base`, `self.final_stats()`,
`self.undo_count`; `library_ui`: `self.library_problem`; `collection_ui`: `self.collection_errors`; `ui_log`:
`line + '\n'`). One obligation moved to the callers: `search` is now an entry at `LibraryUI.refresh_library` /
`refresh_history` (the owner's typed search text) instead of `search[:30]` inside `show_history_count`. The dead
names `_is_call` and `SINK_MODULES` are gone; `SINKS` is the table `sink_positions` reads (Q7). The run-time
sentinel of A15.5 (a) needed no new exemption: the `--all` run has 0 violations and the same 50 exemptions as the
last run before this amendment.

**A15.8 The other findings of the final review.**

1. **G1d (Q8)**: `importlib.import_module('i18n_legacy')` / `__import__('i18n_legacy')` outside `messages.py`, an
   import by a computed name, and `sys.modules[...]` / `sys.modules.get(...)` of a boundary module or a computed key
   are G1d findings (the tree and the frozen sources have none).
2. **zh rendering of a list (ZH-2, 2.6)**: in zh `show()` / `error_text()` return `zh(value)` before the list branch,
   so a stored JSON list in a text field shows `str(value)` as in 1.7.0; the en join of shown parts stays.
3. **Foreign sidecar entries (RV-FD1-2; overrides D5, the last paragraph of 5.1 and L-14)**: entries 1.8.0 does not
   understand are kept by every 1.8.0 write *for the text fields of 4.3*; an entry for a field 1.8.0 does not treat
   as a text field is dropped at the next 1.8.0 read-modify-write (the map is rebuilt, E4) while the field itself is
   kept, so a later version's English rendering of that field falls back to its own lift. Key registry rule (3.6): a
   later version that wants 1.8.0 to keep its entries keeps its message texts in the fields of 4.3.
4. **The rehearsal (RV-FD1-3; overrides 11.5)**: `TEMP` and `TMP` are redirected into `rehearsal\` as well (the
   1.5.1 one-file exe unpacks there, `install_release.py` makes its gate folders with `tempfile`). Step 9 compares
   only the paths the rehearsal could write; the owner's originals are recorded with their mtimes in step 1, and a
   change made while the owner's own program runs (its subscription checks write the library every 15 s) is
   attributed to it, not to the rehearsal, before anything is called a failure.
5. **I8 (RV-FD1-1)**: `test_messages_store.UpgradeReadTests` also has the frozen 1.7.0 code write `settings.json`,
   `failed-downloads.json`, a bad and an overflow file, and checks that `unfinished.load`, `settings.load`,
   `i18n.stored_choice` and `engine.load_failed_job` change no byte and no file name of them, and that the 1.7.0
   error text is read back as its message (legacy origin).
6. **Data kept as documentation (Q4 of round 1)**: `i18n_legacy_data.STRIPPED` (Appendix B) records which key's text
   without its `ERROR: ` prefix is the D-H-1 fragment; the reader matches the fragment through `TEMPLATES` (the frozen
   addition of 4.6), `test_messages` pins the value, and the generated file keeps it so that the regenerated data
   stays byte-identical. No code reads it; it is not dead code of the reader.
7. **Test harnesses that replace the guarded emit (F2 of round 1)**: seven 1.7.0 test files replace `Runner.emit` with
   a plain function, so G2's emit hook does not see their events; recorded in L-15 (their texts are still checked by
   G1a / G1f statically and, where they are shown or stored, by the `show()` / `t()` / `encode` hooks).
8. **Documents (overrides point 2 of 11.8 and the guards clause of its English summary, and the guard summary of
   D8 and of the introduction of section 7)**: README point 2 and its English summary, `架构说明.txt` section 十四 and
   the commit draft claim what the guards guarantee (the static contract of this amendment over the code as written,
   the run-time sentinel on every tested path, the legacy closure, the English replay) instead of "any missed key".
   Point 2 now begins `同类问题由检查拦住：静态扫描按规范第 15 节 A15 写明的来源、传递方式和显示位置检查全部代码`, names
   the generated cases, L-15 and its run-time backstops, the sentinel on every tested path (in English also the zh
   text of a UI key), the legacy closure and the English replay; the English summary says "guards that catch a
   missed key" with the same parts. The replay of the 1.7.0 code (`媒体直链`, `动态页面媒体请求`, the 1.5.1 old-format
   collect error) stands. D8 and the introduction of section 7 are read the same way: the guards make the defect
   class fail a test inside the contract of this amendment and on the tested paths (L-15 for the rest); G5 is the
   rule of A15.4 (strict and value sinks), not a proof that every UI sink argument is display text; and the guards
   together, not each one, catch the three misses on the old sources (G1 the two titles, G3 and the English replay
   all three; G2's self-tests use a probe module, and G4 has none on old code). `架构说明.txt` line 81 gives the
   1.8.0 package path (Q9); section 十四 says that the checks together, not each one, catch the three misses on the
   old code (F3 of round 1). README point 1 says that a frozen copy of the 1.7.0 matcher reads the old rows (Q8 of
   round 1). The two design maps are published next to this spec, scrubbed by the same step (Q10).
9. **Allow reasons (F3 of round 2)**: the `engine.json` `raises` and `composed` entries of `f'{name}: {process.stderr}'`
   say what holds: the `RuntimeError` of `engine.diagnostics()`, which only the `--self-test` entry of `app.py`
   runs; it catches the error and writes the traceback into the self-test report file, never into the window,
   `download.log` or the store.
10. **Not changed (D17 scope, owner actions)**: the absolute local paths of the rollback example in `使用说明.txt`
    (unchanged since 1.7.0, no user path; Q7 / Q8 of rounds 1–2), while the heading above them now names 1.7.0 as
    well; the certificate-cache folder `.yt-dlp-cache` that the first replay run of WP-6 created at the root of the
    source drive, outside the allowed places (RV-WP6-6): the owner deletes it.
11. **Unused imports (Q5 of round 1)**: removed from the test files 1.8.0 edited or added (`test_i18n_catalog`,
    `test_messages`, `test_messages_store`, `test_messages_ui_download`, `test_english_replay`,
    `test_messages_download`).

### A16 (Phase D fix round 1, 2026-11-18) — the guard contract completed and listed as data: sink arguments by name, real callee names and aliases, receiver bindings, the missing flows, object text fields, the strict rule as grammar and flows, the mutants

Overrides A15.1 (its last two sentences), A15.3 (the sentence that names one implementation; F1, F3, F5, F7, F8, F9,
F11), A15.4 (how a sink argument is read, Chip.set and the text-box insert, widget constructors, `**mapping`, the G1b
slots and the G1f compositions), A15.6 (the generator and its counts), 7.9 (the display grammar: `*`, `Path(...).name`,
the neutral names, `re.sub` / `textwrap` / `format()` / `popitem()`), A7.5 (a `sinks` entry may name `parts`), and in
section 12 the rows L-1 and L-15. Findings ZH-CONV-1 (blocker), ZH-CONV-2, ZH-CONV-3 (major) of the zh lens; B1, B2,
B3 (blocker), M1, M2, M3 (major) of the English lens; Q1, Q2, Q3 (blocker), Q4, Q5, Q6 (major) of the quality lens
(final review of phase D, round 1). Implemented in `verification/i18n_tool.py` (`_call_arguments`, `_Sinks._qualified`,
`_Sinks._receiver_kinds`, `_callee_aliases`, `_augmented`, `_Module._owner`, `_is_property`, `_Sinks.strict_ok`,
`_entry_parts`, `CONTRACT`); proved by `verification/guard_contract.py` (generator, probe forms, `MUTANTS`) through
`test_text_guard.ContractTests`. No `src` file changed.

**A16.1 Sinks (ZH-CONV-1, B1, M1, Q4 c).**
- Every sink argument is read positionally *or* by its parameter name, a starred argument at or before its position
  supplies an element, and every `**mapping` supplies its item under each such name (`_call_arguments`):
  `show(value)`, `error_text(error)`, `set_empty(text)`, `Chip.set(text)`, the text-box insert's text (position 1,
  `chars` / `text`), `title` / `wm_title(string)`, `fill(rows)`, the message boxes' `title` / `message` (positions 0
  and 1) and `detail`, `append_log(line)`, every keyword sink (configure / config, constructors, ui_theme, heading,
  menu items, file dialogs). A keyword-only `show()` / `error_text()` is display text only when its keyword value is
  proved, as a positional one is.
- A callee is matched by the name it really has (`_Sinks._qualified`): imports and their aliases (`import
  customtkinter as ck`, `import tkinter.messagebox as mb`, `import ui_theme as uit`), from-imports (`from
  customtkinter import CTkLabel`, `from tkinter.messagebox import showinfo`, `from ui_theme import fit as theme_fit`)
  and dotted paths (`tkinter.messagebox.showinfo`). Widget constructors are the callees of `customtkinter`,
  `tkinter`, `tkinter.ttk` and `tkinter.scrolledtext`, the unresolved bases `ctk` / `tk` / `ttk` / `tkinter` of 7.9,
  and the classes of `ui_theme`.
- `Chip.set` and the insert of a text box are known by what the receiver is bound to (`_Sinks._receiver_kinds`): a call
  of `ui_theme.chip` / `Chip` (a chip) or `ui_theme.textbox` / `CTkTextbox` / `tkinter.Text` / `ScrolledText` (a text
  box), reached through a local or module name, `self.<attr>` across the window, an item of a row model (`row['badge']`:
  every dict item and item assignment of the window under that key, read by name as F3 reads attributes), a UI
  function that returns one, or a conditional. The receiver's name (`chip`; `text` / `log` / `box`) stays a second,
  independent sign.
- A `**mapping` given to any sink call is read under each text keyword of that sink; a tkinter `cnf` mapping
  (`configure({'text': ...})`) as the first argument of configure too.
- A sink parameter may carry a path: an item `kw['text']` of a function's own `**kw` that reaches a sink makes what each
  caller passes as `text=` a sink position (F10 `**param`). A lambda called in place, or bound to a local name that is
  only called, has sink parameters like a function.

**A16.2 Bindings (Q2, ZH-CONV-2, M1).**
- An augmented assignment `x op= v` binds `x` to the composition `x op v` (one synthesized `ast.BinOp` of the old value
  and `v`) — for a name, `self.X` and an item `x[k]`. `text += b` is the `+` composition it is (G1f, the G5 value rule),
  and both of its parts stay followed.
- `global` / `nonlocal`: a function's binding of a name it declares `global` is a binding of the module scope;
  `nonlocal`, of the nearest enclosing function that binds it. `:=` inside a comprehension binds in the scope that
  contains the comprehension.
- A `@property` (also `functools.cached_property`) of a scanned class: `self.<name>` is what its getter returns (F3).

**A16.3 Calls and compositions (ZH-CONV-2, M1, B3, Q1).**
- An aliased callee is a call of what the alias holds (`_callee_aliases`): a local or module name whose every binding
  is a reference (`configure = self.status.configure`, `warn = messagebox.showwarning`, `fit = ui_theme.fit`, `log =
  self.append_log`, `render = messages.show`, `say = self.emit`) or a lambda; `getattr(obj, 'name')(...)`;
  `self.<attr>` (not a method) whose every assignment is a reference; an item of a dict / list / tuple display of
  references or lambdas (`{'a': lambda: X}['a']()`). It applies to the sinks (A16.1), to the flows and to G1b's slots.
  A reference that is only called through such an alias is no "function used as a value" (A7.6).
- F7 adds `copy.copy` / `copy.deepcopy` (the same value). F8 adds `textwrap.shorten` / `fill` / `wrap` / `dedent` /
  `indent` (the text, the prefix and the placeholder) and the str methods called unbound (`str.strip(x)` is
  `x.strip()`). F9: a generator's elements are its `yield` values, in the flows and in the strict grammar.
- F11: `urls.display`, `os.fspath` and `Path(...).name` carry their argument in the flows, so no source passes through
  them at a value sink either.

**A16.4 G1b slots and G1f compositions (Q4 a / b, Q2).**
- New G1b slots, which are G1f slots in a core module: an assignment to an attribute with a TEXT_KEYS name
  (`item.reason = v`; `item.title, item.path = a, b` element by element) and `x.setdefault('<text key>', v)`; an item
  assignment `x['title'] += v` takes the composition.
- G1f's compositions are those of F8: an f-string with a hole; `+` with a non-numeric operand; `*` unless both factors
  are numbers (text times a number repeats the text, so a number on one side proves nothing); `%`; every str method,
  also unbound; `.join` / `.format` / `.format_map`; `re.sub`; `textwrap.*`; `str()` of anything but an exception;
  `format()` without a numeric format spec; a slice.
- The compositions of the G5 value rule (`VALUE_COMPOSITIONS`) add `*`, `textwrap` and `re.sub` to those of A15.4:
  each of them flattens a message without a tripwire (2.3).

**A16.5 The strict rule is the grammar and the flows (Q6, M2, Q1, B3).**
- A strict sink position passes only when the 7.9 grammar proves it display text *and* the flows (`_Flow.leaves`)
  find no source of A15.2 among its leaves (`_Sinks.strict_ok`). This corrects A15.3: the sources are found by one
  implementation, `_Flow.leaves`, for G1b, G1f, the G5 value rule and the G5 strict rule; the strict rule in addition
  needs the display grammar (`_Sinks.display`), a second implementation of the flows over display text, which also
  rejects values the flows do not follow (an event value without `show()`). Because every strict sink is also judged
  by the flows, the grammar can no longer accept what the flows trace to a source. The test
  `ContractTests.test_the_grammar_and_the_flows_agree` judges every generated case by each of the two alone and
  requires both to give the contract's answer (the flows alone: an event value at a strict sink needs `show()`, which
  only the grammar knows, so there they give the value rule's answer).
- Grammar corrections: a product `a * b` is display text only when both factors are (a number is); `+ - / //` with a
  number stay arithmetic (text with a number raises). `Path(x).name` is language-neutral only when no source reaches
  `x`. The neutral-name rule (`NEUTRAL_FIELDS`) is for data the flows do not follow — event and record values,
  parameters: a neutral-named item of a dict display, a local dict, a `dict()` call or a comprehension, and
  `self.<neutral name>` with assignments in the window, are proved by their bindings (F3 / F5). `re.sub`, `textwrap.*`,
  `format()` and `popitem()` of display text are display text.

**A16.6 Scoped `sinks` entries with parts (B1: the mutation M11c).** A `sinks` entry may name `parts`: sub-expressions
(as `ast.unparse` prints them) that count as display text inside the function it names, while the rest of the value
must still be proved — so a changed binding of the value is a finding again (an entry without parts excuses the
expression whatever it is bound to). `allow_stale` reports a part that is no expression of the entry's functions. The
two `_paint_row` entries of `ui_download.json` use it: `chip_text` (parts `model.get('chip')`) and `detail_text`
(parts `model.get('detail')`).

**A16.7 The contract as data, the generator, the mutants, and what the documents claim (B2, Q3, Q5, M3, ZH-CONV-3).**
- The contract is the list `i18n_tool.CONTRACT`: one id per source and kind of display text of A15.2 (`S`, `N`; `O`
  for an event value, the data the flows do not follow), per flow form of A15.3 with this amendment (`F1`…`F11`), per
  sink kind of A15.4 (`A15.4`) and per sink spelling (`A16.1`: every callee, keyword, receiver binding, alias and
  `**mapping` form and every `THEME_SINKS` parameter; `A16.4`: the G1b slot spellings), with the guards it binds and
  the cases it needs. It is kept next to the scanner, independent of the generator; the block below lists the same ids,
  and `ContractTests` checks that the generator tags a case that must be flagged and / or one that must not (as the id
  needs) for every id and guard, that it uses no other id, and that this block equals the list.
- The generator (`guard_contract.py`): G5 — 17 sources (8 bad, 8 good, 1 event value; 4 of the good ones are module,
  class and other-module numbers, crossed with three flows only) × 118 flows × the 22 kinds of `SINKS` (the two
  sink-method kinds as a wrapper's parameter), then per kind and source a rotating flow at wrapper depth 2 and 3,
  positionally and by keyword, with and without `show()`, and the source straight into `show()`, then each of the 143
  sink spellings with every source through three flows: 43 638 cases (27 807 must be flagged, 15 831 must not);
  judged by the grammar alone and by the flows alone, 42 277 window cases each. G1b — 15 sources (7 bad, 8 good with
  the event value) × 118 flows × 10 slot classes, the wrapper depths for two of them, and 22 slot spellings: 13 940
  cases (8 666 / 5 274). G1f — the event value's compositions in every slot class and spelling of a core module:
  1 198 cases (179 / 1 019). All of them come out as the contract says, by the scanner and by each judgement alone.
  The rc scanner before this round, run on the same generator: G5 25 696 of 27 807 flagged and 14 698 of 15 831 clean,
  G1b 7 742 of 8 666, G1f 104 of 179.
- `guard_contract.MUTANTS` removes one behaviour of the scanner at a time — a code path (a source-text change of a copy
  of `i18n_tool`, nothing written) or one entry of a scanner table (menu items, message boxes, file dialogs, ui_theme
  factories, widget modules and factories, text keywords, iteration wrappers, container and str methods, textwrap,
  value compositions, emitters, store writers, typed calls) — and names the ids it serves; with it removed, a case of
  each of those ids comes out wrong (`test_removing_each_behaviour_fails_a_case`). 219 mutants (125 code paths, 94
  table entries) cover 335 of the 340 ids; `guard_contract.UNMUTATED` names the other five with the reason (`N msg()`,
  `N raw()`: no code path makes them a source; `F9 returned parameter`, `F11 show()`, `A15.4 append_log`: each is
  implemented twice or three times, so removing one implementation leaves another).
- Probe forms: the 57 of round 3 and 75 of this review (59 G5 methods of the zh, English and quality probes that were
  missed, with 5 controls; 16 G1b / G1f lines), 132 in all; the rc scanner before this round had 66 of the 75 wrong.
- What the documents may claim (overrides A15.1's "the generator proves that for every combination" and the guard
  sentences of README point 2, its English bullet, `架构说明.txt` section 十四 and the commit draft): G1 and G5 check
  every form the contract lists; for every listed id the generator has cases, and all of them come out right; removing
  a behaviour of the scanner is seen by the cases of its ids. They do not claim that every way of writing Python is
  inside the contract: L-15 lists the forms the reviews found outside it, with the run-time checks that cover them on
  the tested paths.

<!-- guard contract ids (A16.7) -->
```text
S1 allow-listed literal
S2 Chinese literal
S3 class text
S3 module text
S4 other module
S5 enum source
S6 zh template
N msg()
N raw()
N number
N show()
N show(raw())
N t()
O G1f composition
O event value
F1 alias chain
F1 annotated assignment
F1 assignment
F1 augmented assignment
F1 comprehension target
F1 direct value
F1 for target
F1 global
F1 nonlocal
F1 parameter rebound
F1 rebinding
F1 walrus
F1 with as
F2 from import
F2 module name
F2 module.NAME
F3 annotated attribute
F3 attribute mutation
F3 augmented attribute
F3 class attribute
F3 class name attribute
F3 cls attribute
F3 getattr
F3 getattr default
F3 property
F3 self attribute
F3 setattr
F3 tuple-target attribute
F3 cross-module attribute
F4 conditional
F4 or / and
F5 ** spread
F5 add
F5 append
F5 dict comprehension
F5 dict comprehension keys
F5 dict get
F5 dict item
F5 dict iteration
F5 extend
F5 generator
F5 get default
F5 insert
F5 item assignment
F5 item augmented
F5 items()
F5 keys()
F5 list comprehension
F5 list slice
F5 nested selection
F5 neutral-named item
F5 pop
F5 pop default
F5 popitem()
F5 set comprehension
F5 set display
F5 setdefault
F5 starred element
F5 tuple index
F5 update(k=)
F5 update(mapping)
F5 values()
F6 enumerate
F6 items() unpacking
F6 nested unpacking
F6 starred unpacking
F6 unpacking
F6 zip
F7 copy.copy
F7 dict(k=)
F7 dict(mapping)
F7 dict(pairs)
F7 filter
F7 frozenset()
F7 iter()
F7 list()
F7 map
F7 max()
F7 max() several
F7 min()
F7 min() default
F7 next()
F7 next() default
F7 reversed
F7 set()
F7 sorted
F7 tuple()
F8 %
F8 *
F8 +
F8 .format
F8 .format_map
F8 .join
F8 .join receiver
F8 center inserted
F8 f-string
F8 f-string conversion
F8 format()
F8 re.sub
F8 replace inserted
F8 str method
F8 str method unbound
F8 str()
F8 text slice
F8 textwrap
F9 alias of a function
F9 classmethod
F9 dict of lambdas
F9 from-import function
F9 function return
F9 getattr method
F9 lambda
F9 lambda name
F9 method return
F9 nested function
F9 other module function
F9 returned parameter
F9 staticmethod
F9 yield
F10 depth 2
F10 depth 3
F10 keyword
F10 parameter default
F10 positional
F11 Path().name
F11 os.fspath
F11 urls.display
F11 show()
A15.4 G1b ['title'] =
A15.4 G1b clauses(parts)
A15.4 G1b dict 'title'
A15.4 G1b emit()
A15.4 G1b keyword title=
A15.4 G1b msg param
A15.4 G1b raise
A15.4 G1b raw(text)
A15.4 G1b store writer
A15.4 G1b store writer record
A15.4 append_log
A15.4 chip set
A15.4 configure
A15.4 constructor
A15.4 filedialog
A15.4 fill
A15.4 heading
A15.4 library filetext t()
A15.4 menu
A15.4 messagebox
A15.4 set_empty
A15.4 show()
A15.4 sink method
A15.4 t()
A15.4 t() mapping
A15.4 textbox insert
A15.4 theme
A15.4 theme columns
A15.4 theme unlabelled
A15.4 title
A15.4 value sink method
A15.4 values
A16.1 append_log after
A16.1 append_log callee alias
A16.1 append_log getattr
A16.1 append_log line=
A16.1 append_log partial
A16.1 chip set keyword
A16.1 chip set local
A16.1 chip set row item
A16.1 chip set self binding
A16.1 config text=
A16.1 configure **mapping display
A16.1 configure **mapping name
A16.1 configure callee alias
A16.1 configure cnf mapping
A16.1 configure partial
A16.1 configure placeholder=
A16.1 configure placeholder_text=
A16.1 constructor **kw wrapper
A16.1 constructor **mapping
A16.1 constructor customtkinter
A16.1 constructor from-import
A16.1 constructor import alias
A16.1 constructor placeholder=
A16.1 constructor placeholder_text=
A16.1 constructor tk
A16.1 constructor tkinter
A16.1 constructor ttk
A16.1 constructor values=
A16.1 error_text()
A16.1 error_text(error=)
A16.1 filedialog askdirectory
A16.1 filedialog askopenfile
A16.1 filedialog askopenfilename
A16.1 filedialog askopenfilenames
A16.1 filedialog askopenfiles
A16.1 filedialog asksaveasfile
A16.1 filedialog asksaveasfilename
A16.1 filedialog filetypes=
A16.1 filedialog initialfile=
A16.1 fill rows=
A16.1 heading **mapping
A16.1 item values=
A16.1 menu add_cascade
A16.1 menu add_checkbutton
A16.1 menu add_command
A16.1 menu add_radiobutton
A16.1 menu entryconfig
A16.1 menu entryconfigure
A16.1 menu insert_cascade
A16.1 menu insert_checkbutton
A16.1 menu insert_command
A16.1 menu insert_radiobutton
A16.1 messagebox askokcancel
A16.1 messagebox askquestion
A16.1 messagebox askretrycancel
A16.1 messagebox askyesno
A16.1 messagebox askyesnocancel
A16.1 messagebox callee alias
A16.1 messagebox detail=
A16.1 messagebox dotted
A16.1 messagebox from-import
A16.1 messagebox message=
A16.1 messagebox module alias
A16.1 messagebox showerror
A16.1 messagebox showinfo
A16.1 messagebox showwarning
A16.1 messagebox starred
A16.1 messagebox title
A16.1 messagebox title=
A16.1 set_empty text=
A16.1 show at a strict sink by keyword
A16.1 show bare
A16.1 show callee alias
A16.1 show(value=)
A16.1 sink method ** argument
A16.1 sink method **param
A16.1 sink method *param
A16.1 sink method Thread args
A16.1 sink method Thread kwargs
A16.1 sink method after
A16.1 sink method after_idle
A16.1 sink method callee alias
A16.1 sink method getattr
A16.1 sink method keyword
A16.1 sink method keyword-only
A16.1 sink method lambda
A16.1 sink method module function
A16.1 sink method nested
A16.1 sink method partial
A16.1 sink method partial keyword
A16.1 sink method starred argument
A16.1 t() mapping name
A16.1 textbox insert binding
A16.1 textbox insert chars=
A16.1 textbox insert ctk binding
A16.1 textbox insert text=
A16.1 theme **mapping
A16.1 theme button text
A16.1 theme button text keyword
A16.1 theme callee alias
A16.1 theme checkbox text
A16.1 theme checkbox text keyword
A16.1 theme chip text
A16.1 theme chip text keyword
A16.1 theme empty_state text
A16.1 theme empty_state text keyword
A16.1 theme entry placeholder
A16.1 theme entry placeholder keyword
A16.1 theme fit text
A16.1 theme fit text keyword
A16.1 theme fit_alternatives alternatives
A16.1 theme fit_alternatives alternatives keyword
A16.1 theme fit_alternatives text
A16.1 theme fit_alternatives text keyword
A16.1 theme from-import
A16.1 theme label text
A16.1 theme label text keyword
A16.1 theme module alias
A16.1 theme option_menu labels
A16.1 theme option_menu labels keyword
A16.1 theme option_menu values
A16.1 theme option_menu values keyword
A16.1 theme segmented labels
A16.1 theme segmented labels keyword
A16.1 theme setting_row description
A16.1 theme setting_row description keyword
A16.1 theme setting_row title
A16.1 theme setting_row title keyword
A16.1 theme switch text
A16.1 theme switch text keyword
A16.1 theme table columns
A16.1 theme table columns keyword
A16.1 theme table empty
A16.1 theme table empty keyword
A16.1 theme tabs labels keyword
A16.1 theme tabs names
A16.1 theme tabs names keyword
A16.1 title string=
A16.1 value sink method **param
A16.1 value sink method keyword
A16.1 value sink method lambda
A16.1 value sink method through show
A16.1 wm_title
A16.4 .reason = in a tuple target
A16.4 .title =
A16.4 ['title'] +=
A16.4 cut(value)
A16.4 emit callee alias
A16.4 emit keyword
A16.4 emit payload
A16.4 finish_subscription
A16.4 keyword TEXT_KEYS parameter
A16.4 masked(value)
A16.4 message_of(error)
A16.4 other module parameter slot
A16.4 positional TEXT_KEYS parameter
A16.4 raw(text=)
A16.4 say()
A16.4 self.emit()
A16.4 setdefault('title')
A16.4 store writer keyword
A16.4 stripped(value)
A16.4 unfinished.merge
A16.4 unfinished.merge record
A16.4 unfinished.save record
```

**A16.8 Effect on the tree.** No `src` file changed. G1: four G1f compositions that the object text fields and the
str-method compositions of A16.4 reach, each the 1.7.0 code on engine or OS text, each now a `composed` entry of
`engine.json` with its reason: `_EXTRACTOR_PREFIX.sub('', plain, count=1)[:70]` (`explain_error`'s fallback for an
error without a Message, reached as `item.reason`), `str(row.get('title') or '')` (an earlier download's media title,
`item.title`), `raw.rstrip('\r\n')` (`_run_once`, a protected block: a line of yt-dlp's output) and
`str(error).rstrip().rstrip('。.．').rstrip()` (`_sentence` for an OS error). G5: eight positions that the receiver
bindings, aliases and `**mapping` of A16.1 make sink positions, each with a scoped `sinks` entry and its reason:
`arguments.smoke_url` (studio, the `--smoke-url` link put into the link box), `prefix + '\n'.join(added)` and
`self.last_submitted + (...)` (ui_download, the owner's links put into the link box), `chip_text` (ui_download, with
parts, A16.6), `kwargs` (ui_download, `configure_title`, the 1.7.0 wrapper that replaces `current_title.configure`:
three positions), `pages` (ui_settings, the collect link box restored from restart-state.json). `detail_text` got parts
(A16.6). G1 0, G5 0, release gate empty, no stale entry. The reviewers' real-tree mutations M1 (`PICK_ARCHIVE * 1`), M2
(`line += value` in `on_log`), M11c (`chip_text = _chip`), M11d (`CLEARED_CHIP` at the badge), M13 (`options['text'] =
SETTINGS_HELD_NOTICE`) and M14 (`from ui_theme import label as theme_label`) are G5 (and for M13 G1b) findings now.

**A16.9 Forms outside the contract (added to L-15).** Of the forms the reviewers probed, these stay outside and are
listed in L-15 with their run-time backstop: a fold with a function argument (`functools.reduce(lambda a, b: a + b,
NAMES)`); the value of a standard-library call whose result is its argument (`with contextlib.nullcontext(X) as v` —
F1 follows a with-as binding to its context expression, and F9 does not enter the standard library); an attribute of
an arbitrary object that has no TEXT_KEYS name (`obj.note = a + b` with `obj` a parameter); a Chip / text-box receiver
whose binding the flows do not see (a parameter or an attribute of another object, without a sign in its name); the
text / tag pairs after the first text of a tkinter `insert(index, text, tags, text2, tags2)`; a positional `cnf` of
configure that is no dict display or local dict name. A parameter default is reported at the sink inside the function
(A14.1), not at the call that leaves the argument out.

**A17 Release decision: the static scan is a help, not a proof (Phase D review round 2, 2026-11-18).** The owner
asked not to extend the scanner any further for 1.8.0. Review round 2 of Phase D (zh, English and quality lenses; the
data lens had passed in round 1) found the following, all in the scanner and its self-test. No finding concerns the
program's behaviour: the tree has 0 G1 and 0 G5 findings, 0 G2 violations, and every test passes. They are recorded
in L-16 and are not fixed.
- Contract forms the scanner does not implement: a `self.<attr>` bound as an element of a tuple / list target and then
  used as a Chip / text-box receiver (B1); a neutral-named item of a parameter (`row['path']` and the other
  NEUTRAL_FIELDS) at a strict sink, when a caller puts fixed text under that key (Q7); the str methods that split a
  text (`split`, `rsplit`, `splitlines`, `partition`, `rpartition`) at value sinks, G1b slots and G1f (Q8).
- Contract behaviours that no generated case or mutant depends on (Q3, B2): among them the inserted text of `ljust` /
  `rjust`, most str methods, the prefix of `textwrap.indent`, the emitter `log`, the store writer `rename_job`, a
  receiver reached through a conditional or through a UI function that returns one, the `self.<attr>` callee alias,
  and `:=` inside a comprehension. The claim of A16.7 that removing any behaviour of the scanner is seen by a case
  therefore does not hold.
- Realistic forms outside the contract: an exception bound to a name and raised later (`error = X(...)` then
  `raise error`, as `procutil.py` does with an OS error; ZH-CONV2-1, Q9 b), including an English text that reaches
  the raise through a local name; dict union `|` / `|=` (Q9 a, M4); a method of the same class called through the
  class with an explicit `self` (Q9 c); `itertools.chain` / `islice`, `collections.deque` with `popleft`,
  `type(self).X` / `self.__class__.X`, and a sink or emitter passed as a callback to a helper (M4); the key of
  `i18n.t()` and the value of `i18n.label()` when it is a module constant (M5); `configure(values=...)`, an entry's
  `insert`, `CTkOptionMenu.set`, `Tabs.add` / `Tabs.set`, `CTkInputDialog(title=)`, `tkinter.simpledialog.ask*`,
  and a Chip reached through a loop target, a list item or `row.get(...)` (M6); the scoped `sinks` entries
  `chip_text` and `show_notice`'s `label` of `ui_download.json`, which also excuse callers in other functions (m1).
- Contrived forms (ZH-CONV2-3, Q10): `string.Template(...).substitute`, `collections.OrderedDict`, match capture
  patterns, `CONST.encode().decode()`, a `functools.partial` called in place, `emit(**{...})`, `type(self).method(self,
  X)`, `setattr(obj, 'reason', X)` on an object that is not `self`. The unused constants `CALLBACK_CALLS`
  (`i18n_tool.py`) and `FLOW_NAMES` (`guard_contract.py`) stay.
- README point 2 and the commit draft overstated the emit check (ZH-CONV2-2) and the self-test (Q5, M3).

What the documents claim (supersedes the last bullet of A16.7 and A15.1's sentence on the generator): the run-time
sentinel checks, on the paths the tests run, what the core emits (except in the seven test files of L-15) and stores
and what is handed to `show()` / `error_text()` / `t()`; en snapshot (13) checks the captured pages; the G3 closure
check and the G4b English replay check what older versions stored; the static scan checks the forms of the contract
with the exceptions above, and it catches the three 1.7.0 misses. None of the checks claims to catch every way of
writing a fixed text. README point 2, its English bullet, `架构说明.txt` section 十四 and the commit draft say this.
