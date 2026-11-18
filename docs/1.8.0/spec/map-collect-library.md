<!-- Published copy of a 1.8.0 design-phase map (background for MESSAGES.md, which wins where they
     differ; line numbers are those of the 1.7.0 integration tree).  Local paths are written as ROOT
     (the source folder), %TEMP% and "the install folder". -->

# Map "collect-library": message flows of collector, library, collect / library / settings pages, updater, settings

Mapper: collect-library. Scope: L2 = `ROOT\build\v170\i18n-L2\源码` (1.7.0 integration tree),
files `src/collector.py, library.py, collection_ui.py, library_ui.py, ui_settings.py, settings.py, updater.py`
and the i18n machinery (`src/i18n.py`, `src/i18n_maps.py`). 1.6.0 = `git show HEAD:src/<file>` of the repo
(collector.py and settings.py are byte-identical in 1.6.0 and L2). 1.5.1 = `verification/backup-before-1.6.0-20261115/src`.
Line numbers are L2 lines unless marked "1.5.1:" or "1.6.0:".

State mapped (sha256, first 16 hex): collector `32d6bd731d5d0506`, library `8a0f6e6c8c36494b`, collection_ui
`2e1423cab79e11ab`, library_ui `1bce73d246ccc113`, ui_settings `09f39eb6a030716d`, settings `4455fe22ce8e9ad8`,
updater `371dd3886df98035`, i18n `5dc6a49a7ab23418`, i18n_maps `b937c7c11d529234`. If L2 moved after this, re-check
the line numbers; the flows themselves are stable.

Experiments (read-only on the real tree; copies only): `%TEMP%\ytdlp180\collect-library\`
(`v151\src`, `v160\src` from `git archive HEAD src`, `v170\src` copy of L2; `ops.py` runs every Library operation of one
version on one database copy, `drive.py` builds the database variants and runs all three versions, `report.json` the
full result; `typecheck.py` evaluates the old readers' expressions on non-string values). Sandboxed environment
(`YTDLP_DESKTOP_APP_DIR`, `YTDLP_DESKTOP_STATE`, `LOCALAPPDATA` pointed into that folder).

---------------------------------------------------------------------------------------------------

## 1. Findings in short

1. **Every flow of this side** (52 flows, section 4) is one of four shapes: (a) a core exception whose `str()` is
   Chinese and is shown by a dialog / log / status line (`localize(str(error))`); (b) a core event whose value is a bare
   Chinese string (`collect_status`, `collect_error`, `update_status`, `update_error`, legacy `title`); (c) a Chinese text
   the core *stores* (job titles, `jobs.data.errors`, `last_reason`, subscription `last_error` / `name`, page and link
   titles) that is shown later, possibly by another version; (d) stored enum values (link kind, interval, status, mode)
   shown through `label(group, value)` - not messages, they stay as they are.
2. **Shapes (a) and (b) can lose all their reverse translation** if the core carries a key: 22 of the 37 `localize()`
   call sites in these files show live text only (section 5). **Shape (c) cannot**, as long as rollback is kept: every
   old version (1.5.1, 1.6.0, 1.7.0) keeps writing Chinese-only records into the *same* `library.sqlite3` after the
   upgrade, and the owner's existing library is full of them (also the 1.5.1 no-space collector forms). 15 call sites
   show stored text; for those a legacy display path for key-less records stays, or such records are shown in Chinese
   in the English interface (owner decision; section 7, H-1).
3. **sqlite: no new column, ever** (verified, section 3.3): every version inserts with positional
   `INSERT INTO <t> VALUES(?,...)`; one added column (even nullable, even with a DEFAULT) makes 1.5.1, 1.6.0 **and
   1.7.0** fail to create jobs, subscriptions, entries, pages, archive rows and download records ("table jobs has 7
   columns but 6 values were supplied"). A **new table** and **`PRAGMA user_version`** are harmless to all three
   (verified). **Additive keys inside the JSON `data` columns** survive every version's read-modify-write of
   `jobs` and `subscriptions` (verified); `pages` / `entries` rows are replaced whole by whoever writes them.
4. **The stored text fields must keep their type and their Chinese text**: 1.5.1 slices `errors[-1][:130]`, joins
   `'；'.join(errors[-2:])` and concatenates `last_error` (TypeError on a dict, verified); 1.6.0 joins the errors too;
   1.7.0's `localize(dict)` shows the dict's repr. Keys can only be *added beside* the text (precedent: 1.6.0's
   `options['mode_v2']`, `last_reason = {'error', 'reason'}`).
5. **Sidecar keys go stale**: old versions (and 1.7.0) rewrite the text fields without touching an unknown sidecar
   (`change_job(errors=[])` on a collection resume, `errors[-20:]` trimming, `finish_subscription` overwriting
   `last_error`, the `title` event renaming a job, `Archive.media` replacing a title). A sidecar must carry the text it
   describes and be used only while that text is still the stored one (as `history_note_text` already checks
   `last_reason['error'] == errors[-1]`).
6. **Never add keys inside a collect job's / subscription's `options`**: all three versions rebuild them with
   `CollectOptions(**options)` (1.5.1 library_ui.py:197, 268; L2 library_ui.py:933, 1138) - an unknown key is a
   TypeError. Never add a key named `folder` to archive data: `archives()` does `dict(folder=..., **data)` (TypeError,
   verified).
7. The i18n machinery that exists only for reverse translation is about 173 lines of `i18n_maps.py` (LOCALIZE 137,
   VARIANTS 13, NUMERIC 16, RECURSE 7) and about 150 lines of `i18n.py` (`_Template` ... `localize` 133,
   `join_sentences` 17, `JOIN`), plus 21 lines of text-prefix log classification (`prefixes`, `_literal_start`) and
   six key-prefix lists in the UI (section 2).

---------------------------------------------------------------------------------------------------

## 2. The i18n machinery: what exists only to reverse-translate

### 2.1 `src/i18n.py` (468 lines)

| lines | part | purpose | would it go if the core passed keys? |
|---|---|---|---|
| 1-19 | docstring | | - |
| 40-56 | constants | `LANGUAGES, CHOICES, ENV, HELD, EXTRA, CATALOG_MODULES, LANG_CHINESE, ..., MAX_DEPTH` | stay; `MAX_DEPTH` (49) only serves `localize` recursion -> goes |
| 50 | `JOIN = '；'` | the stored clause joiner: `localize` step 3 splits on it, `_join_sentences` zh joins with it | **reverse only** (the store keeps using `'；'` via `all_values('common.clause_sep')[0]`, collection_ui.py:44) |
| 59-74 | `_merge`, `CATALOG`, `_warn_once` | catalogs | stay |
| 77-208 | `_Win32`, `detect`, `env_override`, `resolve`, `init`, `set_language`, `language`, `diagnostics`, `stored_choice`, `store_choice` | language choice (132 lines) | stay |
| 211-256 | `_Values`, `_text`, `_format`, `t`, `_t_in` | key lookup | stay (this is the target model) |
| 259-268 | `all_values(key)` | texts of a key in every language; used for the canonical stored form (`stored_text`), the window title search, ui_log | stays (storing the zh form for old readers still needs it) |
| 271-291 | `_literal_start`, `prefixes(tag)` (21 lines) | ui_log.classify colours a *rendered* line by its text start in every language | goes if a log line carries its key / tag; otherwise stays |
| 294-296 | `sep(name)` | UI composition joiners | stays |
| 299-315 | `join_sentences`, `_join_sentences` (17 lines) | only caller is `localize` step 3 (grep: no other caller in src) | **reverse only** |
| 318-333 | `label`, `choices` (16 lines) | stored enum value -> key (GROUPS) | stay (enum values stay stored as they are) |
| 336-468 | `_Template` (338-362), `_Index` (365-396), `_index` / `_get_index` (399-408), `_allowed` (411-412), `_localized` (415-417, lru_cache 4096), `_localize_in` (420-445, incl. the `'[代理]'` token rule 443-444), `localize` (448-468) - **133 lines** | regex reverse translation of core / stored Chinese text | **reverse only**; a frozen remainder is needed for key-less stored records (section 7, H-1) |

### 2.2 `src/i18n_maps.py` (317 lines)

| lines | name | size | purpose | reverse only? |
|---|---|---|---|---|
| 18-107 | `GROUPS` | 90 lines, 13 groups | stored enum value -> key (mode, format, resolution, naming, appearance, language, interval, job_status, job_kind, link_kind, collect_mode, collect_filter, library_tab, row_state) | no - stays. Exception: `link_kind` also lists `媒体直链` and `动态页面媒体请求` (80-81), which are never stored as a kind; they are page / link *titles* (collector.py:156, 203-204) and are there for the column width (collection_ui.py:202) and as `PAGE_TITLE_KEYS` targets |
| 109-245 | `LOCALIZE` | 137 lines, 135 keys (58 of them produced on this side: core.collect 13, core.library 3, core.archive 6 [2 produced by engine.py:472/698], core.update 22, value.page_title 1, value.link_kind 5, value.subscription 4, value.job_title 3, core.proxy_mask 1) | keys whose zh text the core / store produces | **yes** |
| 247-259 | `VARIANTS` | 13 lines, 11 pairs | older stored zh forms: 1.5.1 placeholder `{count}条 · {mode}` and ten 1.5.1 collector forms without spaces | **yes** (but it is exactly the legacy data a key-based version must still show, section 7 H-1) |
| 261-276 | `NUMERIC` | 16 lines, 14 names | which captures are digits | **yes** |
| 278-284 | `RECURSE` | 7 lines, 5 names | which captures are core texts themselves (`reason`, `error`, `problem`, `mode`, `name`) | **yes** |
| 286-291 | `RESERVED` | 6 | inventory bookkeeping (test_i18n_catalog) | no |
| 293-317 | `NEW_KEYS` | 25 | inventory bookkeeping | no |

### 2.3 Reverse-translation constructs in the files of this side

| file:line | construct | used by |
|---|---|---|
| library.py:238-246 | `PAGE_TITLE_KEYS`, `_shown_title(title)` (core file calling `localize`) | Archive.prepare 825, Archive.save 857 (files for the owner) |
| collection_ui.py:56-61 | `PAGE_TITLE_KEYS` | 449, 462, 534, 543, 639 |
| library_ui.py:64-79 | `JOB_TITLE_KEYS` (66, **dead**: defined, never used), `NAME_KEYS` (67), `TITLED_JOB_KEYS` (71), `UNTITLED_JOB_KEYS` (72), `COLLECT_JOB_KEYS` (73), `PAGE_TITLE_KEYS` (79) | 330-335, 360, 1103, 1148, 1168, 1283, 1288 |
| library_ui.py:37 | `PLACEHOLDER_TITLE = re.compile(r'\d+\s*条 · .+')` | zh-only regex that *parses* a stored title (1.6.0 logic; covers the 1.5.1 no-space form); stays for old records |
| library_ui.py:82-86 / ui_download.py:186-189 | `stored_text(key, **values)` = `all_values(key)[0].format(...)` | the canonical zh form written to the store (1143; ui_download 1579); stays as long as old readers read the store |
| collection_ui.py:38-46 | `subscription_result` joins with `all_values('common.clause_sep')[0]` | composes the stored `last_error` |
| ui_settings.py:95-97 | `shown_engine_version()` = `localize(engine_version())` | goes if `engine_version()` returned a sentinel / key for 已内置 |
| test_i18n_pages.py:745-746 | asserts the three `PAGE_TITLE_KEYS` are equal | |

---------------------------------------------------------------------------------------------------

## 3. Storage: library.sqlite3 and the JSON files of this side

### 3.1 Schema (L2 library.py:280-291 = 1.6.0 library.py:158-168; 1.5.1 library.py:86-95 has the first five tables)

| table | columns (all positional, no column list on INSERT) | key | first version | text fields |
|---|---|---|---|---|
| `jobs` | `id TEXT, kind TEXT, title TEXT, status TEXT, updated REAL, data TEXT` | id | 1.5.1 | `title`; `data` JSON |
| `pages` | `job TEXT, url TEXT, data TEXT` | (job, url) | 1.5.1 | `data` JSON |
| `subscriptions` | `id TEXT, name TEXT, enabled INTEGER, interval INTEGER, next_check REAL, data TEXT` | id | 1.5.1 | `name`; `data` JSON |
| `entries` | `subscription TEXT, key TEXT, pending INTEGER, data TEXT` | (subscription, key) | 1.5.1 | `data` JSON |
| `archives` | `folder TEXT, updated REAL, data TEXT` | folder | 1.5.1 | `data` JSON (= 资料信息.json) |
| `downloads` | `key TEXT, format TEXT, url TEXT, title TEXT, path TEXT, finished REAL, job TEXT` | (key, format) | 1.6.0 (SPEC 3.10) | `title` (a media title: user data) |

- Indexes: only the primary keys. `PRAGMA user_version`: never set by any version (0). Migrations: none. Every
  version runs `CREATE TABLE IF NOT EXISTS` for the tables it knows (1.5.1 and 1.6.0 in `Library.__init__`, 1.7.0
  lazily in `_ensure_schema` on its first connection, library.py:323-326); 1.5.1 simply never sees `downloads`.
- 1.6.0 / 1.7.0 `_prove_writable` (library.py:737-762) reads `PRAGMA user_version` and writes the same value back inside
  `BEGIN IMMEDIATE ... ROLLBACK` (a write probe; it never changes the value).
- Statements per version: 1.5.1 `INSERT INTO jobs VALUES(?,?,?,?,?,?)` (1.5.1:117), `pages VALUES(?,?,?)` (146),
  `subscriptions VALUES(?,?,?,?,?,?)` (192), `entries VALUES(?,?,?,?)` (225), `archives VALUES(?,?,?)` (244); 1.6.0 and
  1.7.0 the same plus `downloads VALUES(?,?,?,?,?,?,?)` (L2 529); reads are `SELECT *` + `dict(row)` (an extra column
  would only add a dict key) and `json.loads(row['data'])` (1.5.1 and 1.6.0 unguarded; 1.7.0 `history()` lists a
  non-JSON record with empty data, library.py:423-433).
- Path: 1.5.1 `APP_DIR\data\library.sqlite3` (1.5.1 library.py:19); 1.6.0 / 1.7.0 `paths.catalog_path()` (= that file
  when it exists and is writable, else `STATE\library.sqlite3`). The owner's real file is shared by every installed
  version (rollback keeps using it).

JSON shapes of the `data` columns (text fields in **bold**):

- `jobs.data`, collect job: `options` {urls, dynamic, scrolls, selector, max_pages, proxy: ''} (create_job pops resume,
  job_id, history_path, library.py:360-366), `subscription`, `checkpoint` {visited, todo}, **`errors`** [str], owner,
  worker, completed [], files [].
- `jobs.data`, download job: `options` (Options.to_dict + urls, overrides?, variants?, mode_v2?), **`errors`** [str]
  (1.6.0+: last 20), completed [url], completed_variants [[url, format]], files [path], current*, `titled`,
  **`last_reason`** {error, reason} (1.6.0+), `result` (the `done` payload: counts, urls, `failed_path`, booleans; no
  message text).
- `pages.data`: url, **title**, text, markdown (page content: user data), text_truncated, links [{url, **title**,
  **kind**, source}], next.
- `subscriptions.data`: options (asdict(CollectOptions), proxy ''), media_only, first_new, initialized, last_check,
  **`last_error`** (str), last_new.
- `entries.data`: a link item {url, **title**, **kind**, source} + page_url + **page_title** (page_title is never shown).
- `archives.data` / `资料信息.json`: url, source, **title**, created, files [], transcripts [], **page_title**, and from
  the media: id, **title**, uploader, description, webpage_url, upload_date, downloaded.

### 3.2 How each version reads the stored text (what a format change would hit)

| field | 1.5.1 | 1.6.0 | 1.7.0 (L2) |
|---|---|---|---|
| `jobs.title` | shown as is (1.5.1 library_ui.py:136) | `PLACEHOLDER_TITLE.fullmatch` (parse) then shown or replaced by the first link (1.6.0 library_ui.py:66-70) | parse + `localize(keys=…)` per job kind (library_ui.py:315-338); `title LIKE` search (library.py:302-304) |
| `jobs.data.errors` | `errors[-1][:130]` display (1.5.1 library_ui.py:152-153, needs str); `'；'.join(errors[-2:])` -> `last_error` (1.5.1 collection_ui.py:167-168, needs str); truthiness -> status (collector worker) | `str(errors[-1])`; download jobs: compared with `last_reason['error']`, else `explain_error()` parses it (BOT_CHECK / STALE regex); collect jobs shown raw; `'；'.join` (1.6.0 collection_ui.py:376) | same + `localize()` (library_ui.py:858-870); join with `all_values('common.clause_sep')[0]` |
| `jobs.data.last_reason` | ignored, preserved | compared + shown | compared + `localize` |
| `jobs.status` | `STATES.get(status, status)` | same | `label('job_status')` |
| `subscriptions.name` | shown; `'订阅：' + name` -> new job title (1.5.1 library_ui.py:272) | same | `localize(name, keys=NAME_KEYS)`; `stored_text('value.job_title.subscription', name=…)` |
| `subscriptions.interval` | `next(k for k,v in INTERVALS.items() if v == interval)` - StopIteration for any value outside 0/900/3600/86400 | same | `label('interval')` |
| `subscriptions.data.last_error` | `"…" + (last_error or '…')` (needs str, 1.5.1 library_ui.py:259) | same (1.6.0 library_ui.py:553) | `localize(last_error)` incl. the `；` split |
| `pages.data` title / links | shown raw; `kind == '网页链接'` compared (filter 1.5.1 collection_ui.py:188, finish_subscription) | same | `localize(title, keys=PAGE_TITLE_KEYS)`; `label('link_kind', kind)`; compare `WEB_LINK` |
| `entries.data.title` | shown raw | shown raw | `localize(keys=PAGE_TITLE_KEYS)` |
| `archives.data.title` | shown raw; search `r['title'] + ' ' + …` (needs str) | same | localize + search on both |
| `downloads.title` | - | engine shows it as the skipped row's title | same |

Verified with `typecheck.py`: a dict in `errors` -> 1.5.1 `errors[-1][:130]` "TypeError: unhashable type: 'slice'";
`'；'.join(...)` (1.5.1, 1.6.0, 1.7.0) "expected str instance, dict found"; a dict `last_error` -> 1.5.1 / 1.6.0
"can only concatenate str (not "dict") to str"; 1.7.0 `localize(dict)` returns the dict's repr.

### 3.3 Would an extra column, table or user_version break a version? (drive.py; report.json)

Seed: a database created and filled by the 1.7.0 copy (collect job + page, download job, subscription + entries,
archive row, download record). Each variant copied once per version; `ops.py` then runs the version's own
Library operations.

| variant | 1.5.1 | 1.6.0 | 1.7.0 |
|---|---|---|---|
| plain | all ok | all ok | all ok |
| `ALTER TABLE <each table> ADD COLUMN msg TEXT` | **fails**: create_job, subscription, archive_index ("table jobs has 7 columns but 6 values were supplied", "... archives has 4 columns but 3 ...") and everything that needs the new rows | **fails** the same + record_download ("downloads has 8 columns but 7") | **fails** the same (1.7.0 itself) |
| `ALTER TABLE jobs ADD COLUMN msg TEXT DEFAULT ''` only | **fails** create_job (DEFAULT does not help a positional VALUES) | **fails** | **fails** |
| new table `messages(owner, field, key, params)` | all ok | all ok | all ok |
| `PRAGMA user_version=2` | all ok, stays 2 | all ok, stays 2 | all ok, stays 2 |
| additive JSON keys (`msg_v2` in jobs.data and subscriptions.data; `title_key` in page / link items and archive data) | preserved through change_job, collection_event, download_event, extend_job, finish_subscription | preserved | preserved |

Consequences: message keys may live in (i) additive keys of the JSON `data` columns, (ii) a new table, or (iii)
`user_version` as a marker. Not in a new column. `pages` and `entries` rows are rewritten whole (`INSERT OR REPLACE`)
by the version that collects, so a key there exists only on rows a key-aware version wrote.

### 3.4 JSON / text files this side writes

| file | writer | contents with text | readers and behaviour with additive fields |
|---|---|---|---|
| `settings.json` (STATE) | settings.py:229-256 (`atomic_write_text`, UTF-8 no BOM, indent 2) | no messages; enum values `mode` (`音频 · 原始最佳` …), `resolution` (`最佳`), `archive_root` (path with 资料归档), 1.7.0 `language` | 1.5.1 (studio.py:78-93) reads 7 keys with `.get`, ignores the rest, and writes only its 7 keys back (drops new keys; SPEC 3.2 accepted); 1.6.0 / 1.7.0 keep unknown keys in `_extra` and write them back; a known key with a wrong type is replaced by its default (and that default is saved). New top-level keys: safe. Changed types / values of existing keys: not safe |
| `restart-state.json` (next to settings.json) | ui_settings.py:388-416 | `urls`, `collect_urls`, `subscription_name` (user input), version 1, created, pid | 1.7.0 only; read once, deleted in every case; another `version` is ignored; 1.5.1 / 1.6.0 never read it |
| `资料信息.json` (archive item folder) | library.py:849-853 (`write_json`) | title, page_title, uploader, description (see 3.1) | 1.5.1 `prepare` / `media` / `attach` `json.loads` + update + save (keeps unknown keys; a corrupt file raises); 1.6.0 / 1.7.0 the same, a non-record is set aside as 资料信息.bad.json. Additive keys: safe (verified via archive_index / archives); key name `folder`: not safe (`archives()` `dict(folder=…, **data)`); `title` must stay a str (search concatenation, readme f-string) |
| `阅读说明.txt`, `网页正文.txt` / `.md` (archive item folder) | library.py:823-827, 856-860 (UTF-8 BOM) | labels in the running language (1.7.0); titles via `_shown_title` | never read back; rewritten on every save by whichever version saves (old versions write Chinese labels and raw titles) |
| exports (user-chosen path) | collection_ui.py:611-643 | link list; page text with `filetext.export_page` labels | never read back |
| `engines\yt-dlp.json` (receipt) | updater.py:462-463 | `{version, sha256}` (no messages) | 1.5.1 (engine.py:43-58), 1.6.0, 1.7.0 read `['sha256']`, `['version']`: additive keys safe |
| collector `job-*/request.json`, `request.events` (temp) | collector.py:324-326, 257-263 | request = `asdict(CollectOptions)`; events = JSON lines `[kind, value]` incl. the Chinese texts of F01-F18 | written and read by the same program (the worker is this exe); never by another version. Path of the collector flows (section 4) |

---------------------------------------------------------------------------------------------------

## 4. Flows (producer -> path -> consumers -> storage)

Legend. **Path** names the hop chain. **Shown**: `L(x)` = `i18n.localize(x)` with all keys; `L(x, K)` = with the key
prefixes K; `label(g)` = `i18n.label(g, value)`; `raw` = shown as it is. **Stored / old readers**: where it is persisted
and what 1.5.1 / 1.6.0 / 1.7.0 do with the stored form. **Key**: today's catalog key (`LOC` = in `i18n_maps.LOCALIZE`,
`VAR` = also in `VARIANTS`, `GRP` = via GROUPS). **H3/H4**: whether the producing literal or the consuming code sits in a
protected block (H3) or is a test-visible Chinese constant (H4). **Tests**: files that pin it (non-exhaustive for
generic link-kind fixtures).

The common collector path ("**CP**") for the worker's texts: `collector.collect()` / `worker_main()` ->
`emit(kind, value)` (collector.py:259-263): for `collect_page`, `collect_checkpoint`, `collect_error` first
`Library.collection_event(job, kind, value)` (library.py:394-407: page -> `pages` row with the stored dict; error ->
`jobs.data['errors'].append(value)`), then one JSON line into `request.events` -> `CollectionRunner.run` tails the file
(collector.py:337-355) and calls `self.emit(kind, value)` -> the UI closure (collection_ui.py:399) puts it on
`StudioApp.events` -> `StudioApp.handle` routes `collect_*` (studio.py:570) -> `CollectionUI.handle_collection`
(collection_ui.py:442-525). `collect_status` is *not* stored (only page / checkpoint / error are).

The common stored-error path ("**SE**"): `jobs.data.errors` of a collect job -> (a) `history_note_text`
(library_ui.py:858-870: `L(errors[-1])` for collect jobs, cut to 130) and (b) on a subscription check
`subscription_result` (collection_ui.py:38-46) -> `'；'.join(errors[-2:])` -> `Library.finish_subscription(…, error)`
(library.py:672-694) -> `subscriptions.data.last_error` -> `_subscription_selection` (library_ui.py:1110-1112,
`L(last_error)`: the `；` split runs when no template matches the whole text). 1.5.1 and 1.6.0 do the same joins and show
both raw (3.2).

The validate path ("**VP**") for `CollectOptions.validate()` (collector.py:98-113) errors, raised (1) in the window:
`collection_options()` -> `start_collection` dialog (collection_ui.py:319, `L(str(error))`); `save_subscription`
dialog (library_ui.py:1045, 1066, `L`); `resume_history` dialog (library_ui.py:933-935, `L`); `check_subscription`
(library_ui.py:1138-1171): `finish_subscription(row['id'], [], False, str(error))` **stores** it as `last_error`, log
`log.subscription.failed` (`L`), dialog when manual (`L`); (2) in `CollectionRunner.run` (collector.py:316, 360-362):
`collect_error` to the UI only; (3) in the worker `collect()` (190) -> `worker_main` except (278-285) -> `collect_error`
**stored** in `jobs.data.errors` (CP) and `status='partial'`. 1.5.1 had the same three sites (1.5.1 library_ui.py:197,
268-279, collector) and stored its no-space forms.

### 4.1 collector.py

| ID | Producer (literal / template) | Path | Consumers (how shown) | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F01 | collector.py:56 `RuntimeError('无法建立 HTTPS 证书缓存，请将程序放到可写目录后重试。')` in `ca_bundle()` | raised inside `collect()` at `Fetcher.get(verify=ca_bundle())` (232) -> per-page except (245-250) -> `collect_error` -> CP | collection_ui.py:468-472 `L(value)` -> log `log.collect.unfinished`, status `collect.status.partial`; later SE | `jobs.data.errors` (worker); then `last_error`; 1.5.1 wrote `无法建立HTTPS证书缓存…` (no spaces); 1.5.1/1.6.0 show raw, 1.7.0 `L` | `core.collect.ca_cache` LOC VAR | - | test_i18n_catalog test_6 (parity), test_7 (1.5.1 forms) |
| F02 | collector.py:101 `ValueError('一次最多采集 20 个起始网页。')` | VP | VP sites (all `L`) | VP(1) `last_error`, VP(3) `jobs.data.errors`; 1.5.1 `一次最多采集20个起始网页。` | `core.collect.too_many_pages` LOC VAR | - | test_6, test_7 |
| F03 | collector.py:103 `'滚动次数需为 0–5，单个起始页的翻页上限需为 1–5。'` | VP | as F02 | as F02; 1.5.1 no-space form | `core.collect.limits` LOC VAR | - | test_6, test_7 |
| F04 | collector.py:105 `'采集地址须为不含用户名或密码的 HTTP / HTTPS 链接。'` | VP | as F02 | as F02; 1.5.1 `…HTTP/HTTPS链接。` | `core.collect.bad_address` LOC VAR | - | test_6, test_7 |
| F05 | collector.py:107 `'请在偏好设置中填写有效代理地址。'` | VP | as F02 | as F02 (same text in 1.5.1) | `core.collect.bad_proxy` LOC | - | test_6, test_7 |
| F06 | collector.py:109 `'动态采集需要安装 Google Chrome；普通网页模式无需浏览器。'` | VP | as F02 | as F02; 1.5.1 no-space form. **Contains the clause joiner `；` itself**: alone (F41 `last_error`) it is an exact hit; inside a `；`-joined `last_error` (F39) the split cuts it in two and English shows `动态采集需要安装 Google Chrome. 普通网页模式无需浏览器。. The page is larger than 8 MB. …` (verified in L2). Latent only: the window validates first (collector.py:316, library_ui.py:1140), so the worker practically never stores it in `errors` | `core.collect.needs_chrome` LOC VAR | - | test_6, test_7 |
| F07 | collector.py:112 `'历史续采位置无效。'` | VP (only with `resume`: resume_history, worker) | as F02 | as F02 | `core.collect.bad_resume` LOC | - | test_6, test_7 |
| F08 | collector.py:122 page title fallback `'网页'` (`title or hostname or '网页'`), propagated by 139 to every link whose own text is empty (`… or title`) | `parse_page` result `title` / `links[].title` -> `collect_page` -> CP | handle_collection: log `log.collect.page` `L(title, PAGE_TITLE_KEYS)` (449 background, 462); collect table 543 and filter 534 `L(…, PAGE_TITLE_KEYS)`; `load_collection_record` (library_ui.py:873-889 -> same render); export 639 `L(…, PAGE_TITLE_KEYS)`; pending table via entries (F35); archive via `archive_pages` (F32/F33, library_ui.py:1262) and engine contexts (F36); archive table 1283-1290 | `pages.data.title`, `links[].title`; `entries.data.title/page_title`; `archives.data.title`, 资料信息.json; all versions show raw except 1.7.0 | `value.page_title.fallback` LOC (no group) | - | test_i18n_pages 548-605, 786-805; test_i18n_catalog 316, 334 |
| F09 | collector.py:129 `ValueError('该页没有匹配的内容，请检查 CSS 范围或尝试动态模式。')` | per page -> `collect_error` -> CP | as F01 | as F01; 1.5.1 no-space form | `core.collect.no_match` LOC VAR | - | test_6, test_7 |
| F10 | collector.py:156 link title `'动态页面媒体请求'` for every captured media request (dynamic mode) | `links[].title` -> `collect_page` -> CP | as F08 (not the page title) | `pages.data.links[].title`, `entries.data.title`, archive title via contexts (until the media's own title replaces it, library.py:870) | `value.link_kind.dynamic_request` (GROUPS `link_kind` text reused as a title) via PAGE_TITLE_KEYS | - | test_i18n_pages 741-805 (CR3-1); test_i18n_catalog 136 |
| F11 | collector.py:199 `f'正在采集第 {len(visited) + 1} 页…'` | `emit('collect_status', …)` -> `.events` -> runner -> UI (CP minus storage) | collection_ui.py:444-446 `set_collect_status(L(value))` (foreground only) | not stored | `core.collect.page_n` LOC VAR (1.5.1 form never stored) | - | test_ui_pages.py:178 |
| F12 | collector.py:203-204 page title **and** link title `'媒体直链'` for a start page that is itself a media file | `collect_page` -> CP | as F08 | as F08 | `value.link_kind.direct` (GROUPS text reused) via PAGE_TITLE_KEYS | - | test_i18n_pages 627-667 (CR2-1), 803-805 |
| F13 | collector.py:234 `ValueError(f'网站返回 HTTP {response.status}，可尝试动态模式或稍后重试。')` | per page -> `collect_error` -> CP | as F01 | as F01; 1.5.1 `网站返回HTTP {status}…` | `core.collect.http_status` LOC VAR (`status` NUMERIC) | - | test_golden_ui.py:247 (ui-1.6.0.json feeds the 1.5.1 form as a live event), test_ui_pages 180-193, test_polish_pages 276, test_i18n_pages 481-618, test_i18n_catalog 303, 361 |
| F14 | collector.py:236 `ValueError('页面超过 8 MB，请缩小采集范围。')` | per page -> CP | as F01 | as F01; 1.5.1 `页面超过8MB…` | `core.collect.too_large` LOC VAR | - | test_i18n_pages 523-606, test_i18n_catalog 303, 361 |
| F15 | collector.py:245-249 any per-page exception: `str(error)` with the proxy URL replaced by `'[代理]'`, cut to 900 - F01, F09, F13, F14 and **third-party text** (curl_cffi / Scrapling / Playwright messages, English; OS errors in the Windows display language) | `collect_error` -> CP | as F01; third-party text matches nothing and stays raw; `'[代理]'` -> `[proxy]` by the token rule (i18n.py:443-444) | `jobs.data.errors` with the mask in the text | text: as above; mask `core.proxy_mask` LOC | - | test_collection.py:69, test_packaged_collection 171 |
| F16 | collector.py:278-285 worker fatal exception (`str(error)`, proxy masked at 281): VP(3) texts, JSON / Library errors (`找不到任务记录。` F21, sqlite text) | `collect_error` -> CP; stored only when `store` exists | as F01 | `jobs.data.errors`, status `partial` | per text | - | test_collection, test_ui_pages |
| F17 | collector.py:359 `f'采集进程退出（{code}），请检查页面或动态模式所需的 Chrome。'` | `CollectionRunner.run` -> `self.emit('collect_error', …)` -> UI (not through the worker, **not stored**) | as F01 (log + status line) | not stored (VARIANT exists although 1.5.1 never stored it either) | `core.collect.worker_exit` LOC VAR (`code` NUMERIC, may be negative) | - | test_i18n_catalog 364 |
| F18 | collector.py:360-362 `str(error)[:900]` of `run()`: VP(2) texts, `Popen` OSError (Windows text), paths errors | `collect_error` -> UI only | as F01 | not stored | per text | - | test_ui_pages |
| F19 | collector.py:72-75, 142, 204 link kinds `'媒体地址'`, `'视频页面'`, `'网页链接'` (enum values, not messages) | `links[].kind` -> CP | collect table `label('link_kind', kind)` (collection_ui.py:542); compared: media filter `WEB_LINK` (collection_ui.py:55, 531; `stored_text('value.link_kind.web')`), `finish_subscription` `== '网页链接'` (library.py:680), 1.5.1 / 1.6.0 compare the literal | `pages.data`, `entries.data`; 1.5.1 / 1.6.0 show raw and compare; must stay Chinese (H2) | GROUPS `link_kind` -> `value.link_kind.media/video_page/web` | compared value (H2); `collection_ui.FILTERS` / `MODE_*` are H4 | test_collection, test_library_store, test_ui, test_golden_ui, test_i18n_pages (Type column) and many fixtures |
| F20 | collector.py:384 `applog … 'collection history could not be updated'` (English) | download.log only | - | - | none | - | - |

### 4.2 library.py

| ID | Producer | Path | Consumers | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F21 | library.py:378 `ValueError('找不到任务记录。')` in `job()` (1.5.1 same) | return / raise | collection_ui.py:495 log `log.collect.record_failed` `L(str(error))`; collection_ui.py:514 (load) `L`; library_ui.py:816-823 history note -> generic `library.history.unreadable` (error not shown); `view_history` / `resume_history` via `library_job` handle only OperationalError -> ValueError reaches the Tk callback (excepthook, download.log); engine.py:1871-1874 catches it -> log `找不到原采集历史…` (`core.engine.no_collect_history`); worker F16 | not stored (except as F16 text) | `core.library.no_job` LOC | - | test_i18n_pages 692-720 (CR2-3) |
| F22 | library.py:579 `f'{value} 等 {count} 条'` (first media title of a multi-link download job; `value` = media title = user data) | `download_event('title')` -> `UPDATE jobs SET title` | library_ui.py:331-333 `history_title`: one link -> raw; several -> `L(title, TITLED_JOB_KEYS)`; history search `title LIKE` (library.py:302) | `jobs.title` (1.6.0+); 1.6.0 shows raw; 1.5.1 never writes it but shows raw | `value.job_title.multi` LOC (`title` not recursed, `count` NUMERIC) | - | test_library_store 161, 337-355, 481, 531; test_polish_pages 213-216; test_ui_state; test_i18n_pages 668-690 (CR2-2); test_i18n_catalog 308, 401 |
| F23 | library.py:647, 668 `ValueError('不支持的订阅检查间隔。')` | raised by `subscription()` / `configure_subscription()` | library_ui.py:1066 save dialog `L`; `_change_interval` via `subscription_action` (handles OperationalError only; unreachable from the menu) | not stored | `core.library.bad_interval` LOC | - | none by text |
| F24 | library.py:655 `'未命名订阅'` (empty name; 1.5.1 same) | `subscriptions.name` | `subscription_row` `L(name, NAME_KEYS)` (library_ui.py:360); background hint and collect note (1148 `L(…, NAME_KEYS)` -> `download.hint.subscription_check`, `collect.background`); log `log.subscription.failed` (1168); job title `stored_text('value.job_title.subscription', name=row['name'])` -> F40 | `subscriptions.name`; also inside `jobs.title` `订阅：未命名订阅` (all versions) | `value.subscription.untitled` LOC; RECURSE `name` | - | test_i18n_pages 494-543, 673-689; test_i18n_catalog 309, 315 |
| F25 | library.py:691 `'检查中断；下次重试，不更新已见记录'` (default when `error` is empty; 1.5.1 same) | `subscriptions.data.last_error` | `_subscription_selection` `L(last_error)` (1112): exact match before the `；` split | stored; 1.5.1 / 1.6.0 raw | `value.subscription.interrupted` LOC | - | test_i18n_catalog 310 |
| F26 | library.py:780 `f'历史记录本次不保存：{error}'` (`error` = OSError / sqlite text) | `open_library()` return value | library_ui.py:395-397 `append_log` (localizes every line, ui_log.py:151); warning strip 423, 652 `L` | not stored (log file gets the localized line) | `core.library.temporary` LOC (RECURSE `error`) | - | test_core_paths_settings 1075-1102; test_ui_pages 30 |
| F27 | library.py:798, 852 `ValueError('归档目录超出资料库范围。')` | Archive.prepare / save | library_ui.py:1268 archive dialog `L`; 1332 attach dialog `L`; engine per-item try (engine.py:1858-1876): the item fails with this error -> F34 storage + failed-downloads.json | via engine: `jobs.data.errors`, failed-downloads.json (other map) | `core.archive.outside_root` LOC | - | none by text |
| F28 | library.py:847 `OSError(f'无法保留损坏的资料信息文件：{record}')` (`record` = path with 资料信息.json) | Archive._set_aside <- prepare | as F27 | as F27 | `core.archive.cannot_set_aside` LOC | - | none by text |
| F29 | library.py:868 `ValueError('归档文件与本条资料目录不匹配。')` | Archive.media <- engine `_run_once` (engine.py:1370-1373) and engine.py:1736-1738 -> `emit('log', f'归档信息保存失败（媒体已保留）：{error}')` | `append_log` `L` (RECURSE `error`) | not stored | `core.archive.media_mismatch` LOC inside `core.engine.archive_info_failed` | **consumed inside H3** (`Runner._run_once`, engine.py:1301-1413) | test_golden_runonce.py:101 + golden/runonce-1.5.1.json (FakeArchive raises the same literal), test_golden_events 170, test_golden_ui 212 |
| F30 | library.py:880 `ValueError('请选择 TXT、SRT、MD 或 VTT 转写稿。')` (1.5.1 `请选择TXT、SRT、MD或VTT转写稿。`, never stored) | Archive.attach | library_ui.py:1332 dialog `L` | not stored | `core.archive.transcript_type` LOC | - | none by text |
| F31 | library.py:886 `ValueError('转写稿目录超出本条资料范围。')` | Archive.attach | library_ui.py:1332 `L` | not stored | `core.archive.transcript_outside` LOC | - | none by text |
| F32 | library.py:822-827 网页正文.txt / .md: `t('filetext.page_text', title=_shown_title(page['title']), url, note=t('filetext.truncated_source')?, body)` | file for the owner | (never shown in the app) | file, rewritten on each prepare; old versions write Chinese labels and raw titles | `filetext.*`; title via `_shown_title` = `L(title, PAGE_TITLE_KEYS)` (reverse translation **inside a core file**) | - | test_i18n_pages 786-805 (SWEEP-3), test_i18n_ui, test_library |
| F33 | library.py:854-860 阅读说明.txt: `filetext.readme_head` (title via `_shown_title`, author = uploader or `filetext.author_unknown`) + description + `readme_media` + files + `readme_transcripts` + transcripts | file for the owner | - | rewritten on every `save` by whichever version saves | `filetext.*` | - | test_i18n_pages 786-805, test_i18n_ui |
| F34 | library.py:626-633 `data['errors'].append(str(value['error']))` (download item; producer engine `_item_done` engine.py:1762-1773: `item.error` = raw yt-dlp line **or** a core text such as build_command errors, SWEEP-1) and `data['last_reason'] = {'error', 'reason'}` (`reason` = engine `explain_error` result, waiting reasons, or ui_download `history_value` -> `PROXY_REASON`, ui_download.py:165-173) | `download_event('item_done')` | library_ui.py:858-870 `history_note_text`: `last_reason['reason']` when `last_reason['error'] == errors[-1]`, else `explain_error(errors[-1])` (engine; parses the raw line) -> `L(…)` -> `library.history.last_error` (cut 130) | `jobs.data.errors` (last 20), `last_reason` (1.6.0+; 1.5.1 preserves unknown keys and never writes download errors); 1.6.0 shows the reason / explain_error result raw | `core.reason.*`, `download.reason.proxy`, `core.engine.*`, etc. (other map) | `item_done` payload is H5; `PROXY_REASON` H4 (ui_download) | test_i18n_pages 478-515, test_i18n_ui, test_polish_pages 238, test_library_store 165 (`errors[-1][:130]` = "what its history note shows"), 430-433 (raw line kept) |
| F35 | library.py:686-687 `entries` payload `dict(item, page_url, page_title)` (titles of F08 / F10 / F12, kind F19) | `finish_subscription` -> `entries` row -> `pending()` (696-698) | library_ui.py:1103 pending table `L(title, PAGE_TITLE_KEYS)`; `queue_subscription` -> `queue_collected_items` (urls, `source` as referer) | `entries.data` (replaced whole on every check); `page_title` never shown | as F08 | - | test_i18n_pages 567-606 |
| F36 | library.py:712-723 `contexts(urls)` -> `{title, source, job_id, page_url}` from stored pages (titles of F08 / F10 / F12) | library_ui.py:1033-1039 `resume_options` / ui_download read_form -> `Options.archive_contexts` -> engine `Archive.prepare(url, context)` (engine.py:1868-1876) | archive table 1288 `L(title, PAGE_TITLE_KEYS)`; readme F33 | `archives.data.title`, 资料信息.json `title` (replaced by the media's own title in `Archive.media`, library.py:870) | as F08 | - | test_i18n_pages 567-606 |
| F37 | library.py:250 `'未命名'`, 792 `'来源'`, 800/853/869/881 `'资料信息.json'`, 824 `'网页正文.txt'`/`'.md'`, 837 `'资料信息.bad*.json'`, 856 `'阅读说明.txt'`, 882 `'转写稿'` | on-disk names (D10: never translated) | appear on screen only inside paths (F28 `{record}`, Explorer) | file / folder names | none (D10) | - | test_library, test_i18n_pages |
| F38 | library.py:321, 774-775 `_log.warning` (English) | download.log | - | - | none | - | - |

### 4.3 collection_ui.py (compositions that end in storage or reuse core texts)

| ID | Producer | Path | Consumers | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F39 | collection_ui.py:38-46 `subscription_result`: `'；'.join(errors[-2:])` (two collector texts F01-F16, possibly 1.5.1 forms) or `stored_text('value.subscription.stopped')` `'采集已停止'` / `'value.subscription.unfinished'` `'检查未完成'` | `finish_subscription(…, error)` (483, or deferred via `HistoryWriter` 432-440) -> `subscriptions.data.last_error` | library_ui.py:1112 `L(last_error)`: exact / template first, else split on `；`, localize each part, join with `join_sentences` | stored; 1.5.1 (collection_ui.py:167-168) and 1.6.0 (376) write the same joiner and texts and show raw | `value.subscription.stopped/unfinished` LOC; parts per F01-F16; joiner `i18n.JOIN` | - | test_i18n_pages 516-547 (joined last_error), test_i18n_catalog 303, 361 |
| - | UI-only Chinese constants (H4, never core): 33-35 `COLLECT_WAITING`, `COLLECT_GAVE_UP`, `COLLECT_DROPPED` (shown via `collect.status.*`); 48-50 `MODE_STATIC`, `MODE_DYNAMIC`, `FILTERS` (Tk variable values, compared, menus show `label('collect_mode' / 'collect_filter')`); 51-53 `STATUS_IDLE`, `NOTE_PAGES`, `NOTE_DYNAMIC`; 64-65 `ADD_TEXT`, `SUBTITLE`; 67 `BACKGROUND_NOTE` | - | shown through `t()` of their keys | not stored (asdict(options) keeps `dynamic: bool`) | `collect.*` | **H4** | test_v170_ui 383-408, test_polish_pages 184 (`SUBTITLE`), test_i18n_catalog ParityTests |

### 4.4 library_ui.py (compositions that end in storage)

| ID | Producer | Path | Consumers | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F40 | library_ui.py:1143 `stored_text('value.job_title.subscription', name=row['name'])` = `'订阅：' + name` (1.5.1 library_ui.py:272 `'订阅：' + row['name']`); a manual collection's title is its first start link (collection_ui.py:341, user data) | `launch_collection(title=…)` -> `create_job('collect', title, …)` -> `jobs.title` | `history_title` `L(title, COLLECT_JOB_KEYS)` (330), name part recursed with `NAME_KEYS`-like filter (RECURSE `name`: only `value.subscription.untitled`) | `jobs.title` (all versions; raw in 1.5.1 / 1.6.0) | `value.job_title.subscription` LOC | - | test_i18n_pages 478-515, 668-690 |
| F41 | library_ui.py:1156-1169 `check_subscription`: `finish_subscription(row['id'], [], False, str(error))` for OSError / ValueError = VP(1) texts F02-F07 or Windows OS text | `subscriptions.data.last_error` | 1112 `L`; log 1168-1169 `log.subscription.failed` (`L(name, NAME_KEYS)`, `L(error)`); dialog 1171 `L` | stored (1.5.1 same, its no-space forms) | per text | - | test_i18n_pages, test_ui_pages 196 |
| F42 | placeholder job title `stored_text('value.job_title.placeholder', count=…, mode=options.mode)` = `'{n} 条 · {mode}'` (ui_download.py:1579; 1.5.1 studio.py:580 `f'{len(urls)}条 · {options.mode}'`) - producer outside this side, consumer here | `create_job('download', title, …)` -> `jobs.title` | `history_title` (331-338), jobs without `data['titled']`: title matches `PLACEHOLDER_TITLE` and the job has links -> one link: `display(urls[0])`; several: `t('value.job_title.multi', title=display(urls[0]), count=…)`; otherwise (no links, or another title) -> `L(title, UNTITLED_JOB_KEYS)` with RECURSE `mode` (`value.mode.*`) | `jobs.title`; 1.6.0 same parse; 1.5.1 raw | `value.job_title.placeholder` LOC VAR (1.5.1 form) | - | test_golden_ui (ui-1.5.1/1.6.0 history rows), test_library, test_i18n_download, test_i18n_pages, test_i18n_catalog |
| - | UI-only Chinese constants (H4): 31 `INTERVALS` (Tk variable keeps the zh label; seconds stored), 32 `STATES`, 35 `NOTHING_DONE`, 39-49 `RESUME_LATER_LOG` … `PICK_ARCHIVE`, 56-58 `LIBRARY_BUSY*`, 62-63 `HISTORY_EMPTY`, `HISTORY_NONE_FOUND`; 405 default archive root `…/资料归档` (D10 path, also settings.py:36) | - | `t()` / `label()` | `interval` stored as seconds; archive_root path in settings.json | `library.*` | **H4** | test_i18n_catalog ParityTests, test_library_ui, test_ui_pages |

### 4.5 ui_settings.py and updater.py

| ID | Producer | Path | Consumers | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F43 | updater.py:108 `engine_version()` -> `'已内置'` when no version is readable (1.5.1 engine.py:60 same) | return value | ui_settings.py:95-97 `shown_engine_version` = `L(…)` -> `version_label` (114, 512, 592), `engine_badge` (513, 593), `log.engine_restored` (571); studio.py engine badge / `log.ready` (other map). Also compared: `version == engine_version()` (updater.py:398), `version_newer(latest, version)` (ui_settings.py:517) | not stored (the receipt holds tags) | `core.update.builtin` LOC | - | test_core_updater 286, test_i18n_download 235, 296, test_i18n_pages 612 |
| F44 | updater.py:395 `'正在检查官方更新…'`, 417 `'正在下载新版引擎…'`, 448 `'正在检查新版引擎与本程序的兼容性…'` | `EngineUpdater._status` -> `emit('update_status', text)` -> ui_settings emit closure (546-548) -> events -> `on_update_status` (573-576) `L`; with `legacy_events` (engine.Runner.update, the 1.5.1 API) the first one goes out as `('title', text)` -> ui_download.on_title (other map) | `update_status` label `L(str(value))` | not stored | `core.update.checking` / `downloading` / `testing` LOC; ui_settings.py:542 also shows `t('core.update.checking')` directly | - | test_core_updater 357, 565; golden ui-1.5.1.json / ui-1.6.0.json via test_golden_ui.py:229 (`handle('title', '正在检查官方更新…')`), 281 (`'正在下载 yt-dlp 2099.03.03…'`, a non-core text that must pass unchanged) |
| F45 | updater.py:35-53 `MSG_NETWORK` (`{reason}`), `MSG_RELEASE`, `MSG_NO_ASSET`, `MSG_ORIGIN`, `MSG_NO_DIGEST`, `MSG_SIZE`, `MSG_MISMATCH`, `MSG_PROBE`, `MSG_INSTALL` (`{reason}`), `MSG_SAVE` (`{reason}`), `MSG_RECEIPT` (`{reason}`), `MSG_START` (`{reason}`); nested reasons: `_reason` 127 `f'HTTP {code}'` (no key, passes), 130 `'连接超时'`, 131 OS / http text; `_file_reason` 137 Windows `strerror`; `_check_start` 359 `f'{error.timeout:g} 秒内没有结束'`, 362 `'无法启动'`, 368 `f'退出码 {code}'` / `'没有给出版本号'`; unexpected exceptions `str(error)` (476-478) | `raise UpdateError(…)` -> `run()` except (473-478) -> `emit('update_error', str(error))` -> `on_update_error` (599-602) | `L(str(value))` (RECURSE `reason`) -> `settings.update.failed` status + `log.update_failed` | not stored | `core.update.no_github`, `bad_release`, `no_exe`, `bad_host`, `no_sha`, `bad_size`, `bad_hash`, `incompatible`, `write_failed`, `save_failed`, `record_failed`, `start_failed`, `timeout`, `start_timeout`, `start_cannot`, `start_exit`, `start_no_version` LOC | **H4-like**: tests compare `str(exception)` with `updater.MSG_*` | test_core_updater 325-335, 470, 477; test_v170_core 713; test_review_state 941; golden ui-1.6.0 ('update_error' step, English text passes) |
| F46 | updater.py:247 `UpdateError(MSG_RESTORE)` from `restore_bundled()` | raise | ui_settings.py:562-568 dialog `L(str(error))` | not stored | `core.update.restore_failed` LOC | H4-like (`updater.MSG_RESTORE`) | test_v170_core 788, test_v170_merge 42-48 |
| F47 | updater.py:156/159 `check_latest` UpdateError in the daily check (ui_settings.py:629-635) | swallowed | never shown | - | - | - | test_core_updater |
| F48 | engine `check_cookie_file` ValueError / OSError texts (engine.py:243-254, `core.cookies.*`; producer other map) | raise | ui_settings.py:494-498 dialog `L(str(error))` | not stored (cookie *path* only in settings.json; contents never, H6) | `core.cookies.*` LOC | - | test_i18n_download |
| F49 | updater.py:339-367, 474-477, 231 `applog` warnings (English, some with `' '.join(output.split())[:300]` engine output) | download.log only | - | - | none | - | - |
| - | ui_settings.py:30 `UPDATE_IDLE`, 38 `APPEARANCE_LABELS` (H4 constants; shown via `settings.update.idle`, `label('appearance')`) | - | - | `appearance` stored as `light/dark/system` | `settings.*` | **H4** | test_i18n_catalog ParityTests, test_i18n_pages 454 |
| - | `updated` / `engine_check` events carry the GitHub tag (`version`, `latest`): shown as is (590-597, 638-646) | - | - | `latest_engine`, `last_engine_check` in settings.json (no text) | - | - | test_golden_ui 231-236 |
| - | `update_progress` -> `t('settings.update.downloading_pct', percent=…)` (UI-made) | - | - | - | - | - | - |

### 4.6 settings.py and the i18n module

| ID | Producer | Path | Consumers | Stored / old readers | Key | H3/H4 | Tests |
|---|---|---|---|---|---|---|---|
| F50 | settings.py:206-207 `'settings.json 暂时无法读取（可能被其他程序占用：%s），本次先使用默认设置；原文件不会被默认设置覆盖。'`, 243 `'settings.json 仍无法读取，本次设置没有保存，原文件保持不变。'` (`_log.warning`) | logger `YtDlpDesktop.settings` -> download.log (applog.py:35) - **not** through `append_log`, so never localized | the user sees it only by opening download.log (ui_log 打开日志文件, ui_log.py:191-195); the window says it with its own `t()` notice (test_v170_ui 615) | log file | none (not in the inventory as shown text) | - | none by text |
| F51 | settings.py:32-33 defaults `'音频 · 原始最佳'`, `'最佳'`; 36 `'资料归档'` in the default archive_root | settings.json values (enum / path) | `label('mode')`, `label('resolution')` in ui_download; path shown in the entry | settings.json; 1.5.1 checks `mode in MODES` (falls back to MODES[0]) | GROUPS `mode`, `resolution`; D10 | H4 (`engine.MODES`, `RESOLUTIONS`) | test_core_paths_settings |
| F52 | i18n.py:443-444 token rule `'[代理]'` -> `core.proxy_mask` | consumer side of F15 / F16 masks (and engine / ui_log masks) | any `L()` of a line containing it | the stored errors contain `[代理]` | `core.proxy_mask` LOC | - | test_i18n_catalog, test_i18n_download, test_queue, test_ui_state |

Flow count: 52 (F01-F52).

---------------------------------------------------------------------------------------------------

## 5. Every `localize()` call site and key-prefix list in the UI files of this side

"Goes" = disappears if the core passed key + params (the UI then calls `t(key, **params)`; third-party / OS text stays
raw without any call). "Stays (legacy)" = still needed for records written without keys (before the upgrade, or by a
rolled-back version afterwards), unless the owner accepts Chinese for those records in English.

| site | shows | live / stored | if the core passed keys |
|---|---|---|---|
| collection_ui.py:319 | VP(1) error, start dialog | live exception | goes |
| collection_ui.py:370, 373 | `create_job` error (sqlite / OSError / ValueError) | live exception | goes (core ones); OS / sqlite text raw |
| collection_ui.py:446 | `collect_status` (F11) | live event | goes |
| collection_ui.py:449, 462 | `collect_page` title in the log (F08/F10/F12), `PAGE_TITLE_KEYS` | live event | goes (a `title_key` in the page dict) |
| collection_ui.py:468 | `collect_error` (F01-F18) | live event | goes for core texts; third-party text raw |
| collection_ui.py:495, 514 | `job()` / load errors (F21) | live exception | goes |
| collection_ui.py:534, 543 | collect table title + filter, `PAGE_TITLE_KEYS` | live **and** stored (`load_collection_record`) | stays (legacy) for pages without a key |
| collection_ui.py:639 | export heading title, `PAGE_TITLE_KEYS` | live and stored | stays (legacy) |
| library_ui.py:330 | collect job title, `COLLECT_JOB_KEYS` (F40) | stored | stays (legacy) |
| library_ui.py:333 | multi title, `TITLED_JOB_KEYS` (F22) | stored | stays (legacy) |
| library_ui.py:335 | placeholder title, `UNTITLED_JOB_KEYS` (F42) | stored | stays (legacy) |
| library_ui.py:360 | subscription name, `NAME_KEYS` (F24) | stored | stays (legacy) |
| library_ui.py:423, 652 | `open_library` warning (F26) | live | goes |
| library_ui.py:869 | last error / reason / `explain_error` result (F01-F16, F34) | stored | stays (legacy); `explain_error` itself is a re-derivation from a raw engine line at display time (section 7 H-9) |
| library_ui.py:935 | VP(1) resume dialog | live | goes |
| library_ui.py:1066 | VP(1) / F23 save-subscription dialog | live | goes |
| library_ui.py:1103 | new-items titles, `PAGE_TITLE_KEYS` (F35) | stored | stays (legacy) |
| library_ui.py:1112 | `last_error` incl. the `；` split (F25, F39, F41) | stored | stays (legacy) |
| library_ui.py:1148, 1168 (name) | subscription name, `NAME_KEYS` | stored | stays (legacy) |
| library_ui.py:1169, 1171 | F41 error in log / dialog | live | goes |
| library_ui.py:1268, 1332 | archive / attach dialogs (F27, F28, F30, F31) | live | goes |
| library_ui.py:1283, 1288 | archive titles + search, `PAGE_TITLE_KEYS` (F08/F10/F12/F36) | stored | stays (legacy) |
| ui_settings.py:97 | `已内置` (F43) | live | goes (sentinel instead of text) |
| ui_settings.py:497 | cookie file errors (F48) | live | goes |
| ui_settings.py:566 | `MSG_RESTORE` (F46) | live | goes |
| ui_settings.py:576 | `update_status` (F44) | live event | goes |
| ui_settings.py:600 | `update_error` (F45) | live event | goes (nested `reason` as a nested key or raw OS text) |
| library.py:246 (`_shown_title`, used 825, 857) | archive file titles (F32, F33) | stored | stays (legacy) |

Totals: 37 call sites (collection_ui 12, library_ui 19, ui_settings 5, library 1); **22 go** (live text: collection_ui
319, 370, 373, 446, 449, 462, 468, 495, 514; library_ui 423, 652, 935, 1066, 1169, 1171, 1268, 1332; ui_settings 97,
497, 566, 576, 600), **15 stay** for legacy stored records (collection_ui 534, 543, 639; library_ui 330, 333, 335, 360,
869, 1103, 1112, 1148, 1168 [name]; 1283, 1288; library.py 246 [used at 825 and 857]).

Key-prefix lists: `collection_ui.PAGE_TITLE_KEYS` (61), `library_ui.PAGE_TITLE_KEYS` (79), `library.PAGE_TITLE_KEYS`
(242), `NAME_KEYS` (67), `TITLED_JOB_KEYS` (71), `UNTITLED_JOB_KEYS` (72), `COLLECT_JOB_KEYS` (73), `JOB_TITLE_KEYS`
(66, dead). All exist because a stored *user* field (page / link title, subscription name, job title) may hold an
app-made text; with a `title_key` / `name_key` sidecar the key itself says so and the lists are not needed for new
records. They stay for legacy records (they are what keeps a user title such as a page literally named 媒体直链 from
being translated only partly - and they still mistranslate that case, the accepted CR2-1 / CR3-1 risk).

---------------------------------------------------------------------------------------------------

## 6. Every CJK literal of these files

Counts from an ast scan (`cjk_scan.py` in the scratchpad: string constants and f-strings, docstrings and comments
listed separately).

### collector.py (23 literals; no CJK docstring or comment)
56 F01 · 74 F19 · 75 `'网页链接'`, `'视频页面'` F19 · 101 F02 · 103 F03 · 105 F04 · 107 F05 · 109 F06 · 112 F07 · 122 F08 ·
129 F09 · 142 `'媒体地址'` F19 (comparison: prefer media kind) · 156 F10 · 199 F11 · 203 `'媒体直链'` F12 · 204 `'媒体地址'`
F19, `'媒体直链'` F12 · 234 F13 · 236 F14 · 247 `'[代理]'` F15 · 281 `'[代理]'` F16 · 359 F17.

### library.py (27 literals; docstrings 155, 834; comments 145, 238-240, 601, 809)
250 `'未命名'` F37 · 378 F21 · 579 F22 · 647 F23 · 655 F24 · 668 F23 · 680 `'网页链接'` F19 (comparison, H2) · 691 F25 ·
780 F26 · 792 `'来源'` F37 · 798 F27 · 800 `'资料信息.json'` F37 · 824 `'网页正文.txt'`, `'网页正文.md'` F37 · 837 three
`'资料信息.bad…json'` F37 · 847 F28 · 852 F27 · 853 F37 · 856 `'阅读说明.txt'` F37 · 868 F29 · 869 F37 · 880 F30 · 881 F37 ·
882 `'转写稿'` F37 · 886 F31. Docstrings / comments: explanations only (never shown).

### collection_ui.py (13 literals; docstrings 77, 569; comments 56, 58, 60, 63, 201, 518, 563)
33, 34, 35 H4 constants (§4.3) · 48, 49 `MODE_*` H4 (Tk values) · 50 `FILTERS` H4 (Tk values) · 51, 52, 53 H4 · 64, 65 H4 ·
67 H4. None is produced by the core; each is shown through `t()` of its key and kept for tests (H4). Comments and
docstrings: never shown.

### library_ui.py (28 literals; docstrings 83, 164, 316, 357, 687, 774, 974; comments 36, 69, 70, 75, 77, 78, 332, 478, 537, 1097)
31 `INTERVALS` (4 labels) H4 · 32 `STATES` (4) H4 · 35 `NOTHING_DONE` H4 · 37 `PLACEHOLDER_TITLE` regex `'\d+\s*条 · .+'` -
zh-only pattern used to *parse* stored titles (F42), never shown · 39-49 H4 constants (11) · 56-58 H4 (3) · 62-63 H4 (2) ·
405 `'资料归档'` twice: default archive root path (D10; F51). Docstrings / comments never shown.

### ui_settings.py (4 literals; docstrings 1, 96, 252, 296, 306, 335, 389, 520; comments 37, 209)
30 `UPDATE_IDLE` H4 · 38 `APPEARANCE_LABELS` (3) H4. Docstrings / comments never shown.

### settings.py (5 literals)
32 `'音频 · 原始最佳'` F51 · 33 `'最佳'` F51 · 36 `'资料归档'` F51 (path) · 206 F50 · 243 F50.

### updater.py (22 literals; comment 52)
35-53 the 13 `MSG_*` F45 / F46 (53 = `MSG_RESTORE` F46) · 108 F43 · 130 F45 · 359 F45 · 362 F45 · 368 `'退出码 {code}'`, `'没有给出版本号'`
F45 · 395, 417, 448 F44.

### i18n.py (2 literals)
443, 444 `'[代理]'` F52 (the token rule's search text; zh data for the reverse step).

### i18n_maps.py (37 literals)
20-26 GROUPS `mode` values (7) · 29-35 GROUPS `format` values (6 with CJK; `MP3 · 320 kbps` has none) · 38 `'最佳'` ·
77-81 `link_kind` (5; 80-81 are titles F10 / F12) · 84-85 `collect_mode` · 88-89 `collect_filter` · 92-94 `library_tab` -
all stored / compared enum values (stay) · 248-258 `VARIANTS` (11 zh templates: 1.5.1 placeholder + ten 1.5.1 collector
forms) - reverse-translation data for legacy records.

Catalog modules (`i18n_core.py`, `i18n_collect.py`, `i18n_library.py`, `i18n_settings.py`, …) hold the zh texts by
key; they are the target of `t()` and are not producers.

---------------------------------------------------------------------------------------------------

## 7. Hard cases for a key-based design

- **H-1 Legacy and rollback records have no keys.** The owner's library holds texts from 1.5.1 (no-space collector
  forms, `{n}条 · {mode}`), 1.6.0 and 1.7.0; and with rollback kept, 1.5.1 / 1.6.0 / 1.7.0 keep adding Chinese-only rows
  (jobs, errors, last_error, pages, entries, archives, 资料信息.json) to the *same* file after the upgrade. A key-based
  version must either keep a frozen reverse table for exactly those stored forms (LOCALIZE restricted to the
  `value.*`, `core.collect.*`, `core.library.*`, `core.archive.*`, `core.reason.*` … that were ever *stored*, plus
  VARIANTS) or show them in Chinese in English (owner's call). A one-time migration that adds keys to old rows is
  possible (additive JSON keys survive, 3.3) but does not cover rows an old version writes later.
- **H-2 No new sqlite column.** Verified: positional `INSERT … VALUES(?,…)` in every version, 1.7.0 included. Keys go
  into additive JSON keys of `data`, or a new table (verified harmless), with `user_version` free as a format marker.
- **H-3 Sidecars go stale.** `change_job(…, errors=[])` on a collection resume (collection_ui.py:344; 1.5.1
  collection_ui.py:129), `errors[-20:]` (library.py:628), `finish_subscription` overwriting `last_error` (691), the
  `title` event renaming a job (578-579), `Archive.media` replacing `title` (870), `INSERT OR REPLACE` of pages and
  entries: an old version changes the text and leaves an unknown key field as it was. Index-parallel lists (`errors`
  vs `error_keys`) desynchronise. The sidecar must hold the text it belongs to and be trusted only while it matches
  (the `last_reason` pattern, library_ui.py:862-865).
- **H-4 Old readers need `str` in the old fields.** errors elements, `last_error`, `title`, `name`, archive `title`
  (verified TypeErrors, 3.2). Keys never replace text in these fields; 1.7.0 would show a dict's repr.
- **H-5 Composite stored messages.** `last_error = '；'.join(errors[-2:])` (F39; written identically by 1.5.1 / 1.6.0)
  is two messages in one string; `'订阅：' + name` with `name = '未命名订阅'` nests a message in a message; the placeholder
  title nests an enum (`mode`). A key form needs a list / nested params; the zh string must still be written for the
  old readers. The textual split is ambiguous: `core.collect.needs_chrome`, `value.subscription.interrupted` and
  `core.engine.waiting` contain `；` themselves, so a joined pair with one of them is cut in the wrong place (verified
  for needs_chrome in L2: half Chinese in English; latent today, F06).
- **H-6 Nested and foreign text in messages.** `MSG_NETWORK` / `MSG_START` / `MSG_INSTALL` reasons (some are messages,
  some are Windows `strerror` in the *system* language, `HTTP 403`), collector exceptions wrapping curl / Scrapling /
  Playwright English, `历史记录本次不保存：{error}` with sqlite text, `归档信息保存失败（媒体已保留）：{error}` wrapping F29.
  Params must be able to hold "a message" or "raw text".
- **H-7 Bare-string event payloads.** `collect_status`, `collect_error`, `update_status`, `update_error`, legacy `title`
  carry a `str`; H5 allows only additive payload changes, and a string cannot get a field. Options: a new event kind,
  or a dict accepted *beside* the string. The UI must keep accepting the plain string: golden ui-1.6.0.json
  feeds `handle('collect_error', '网站返回HTTP 503…')`, `handle('title', '正在检查官方更新…')`,
  `handle('update_status', '正在下载 yt-dlp 2099.03.03…')`, `handle('update_error', 'Update checksum or size is
  unavailable')` (test_golden_ui.py:229-283); test_ui_pages / test_polish_pages do the same. The collector's IPC file
  (`request.events`) is same-version only, so its format may change freely; what it *stores* via
  `collection_event` may not (H-4).
- **H-8 Exceptions as carriers.** `str(error)` is stored (`last_error` via F41, `errors` via F16) and composed inside
  H3 (`_run_once`, F29 - pinned by golden/runonce-1.5.1.json through test_golden_runonce's FakeArchive). Tests compare
  `str(UpdateError)` with `updater.MSG_*` (test_v170_core 713, 788; test_review_state 941; test_v170_merge 42-48). A
  keyed exception must keep `str()` = today's zh text.
- **H-9 Display-time re-derivation.** `history_note_text` runs `engine.explain_error` on a stored raw engine line when
  no matching `last_reason` exists (library_ui.py:866-868): the reason is computed at display time from text, not
  stored as a key. Records from 1.5.1 have no reason at all.
- **H-10 Title fallback propagation.** collector.py:139 copies the page title (maybe the fallback 网页) into every link
  without text; a `title_key` must follow it. The link kind GROUPS entries 媒体直链 / 动态页面媒体请求 double as titles.
- **H-11 User data equal to an app text.** A page titled 媒体直链 / 网页, a media titled `订阅：x` or `3 条 · …`, a
  subscription named 未命名订阅: keys resolve this for new records; legacy records keep today's accepted ambiguity
  (CR2-1, CR2-2, CR3-1).
- **H-12 Proxy masking is textual.** collector.py:247 / 281 replace the proxy URL with `[代理]` before emitting and
  storing; the stored text contains the zh token (F52). A key design must still mask before anything is stored
  (proxy credentials), e.g. a masked `error` param.
- **H-13 History search is on stored zh text** (`title LIKE`, library.py:302-304): English template words are not
  found (CR1-2). Unchanged by keys unless the search also matches keys / params.
- **H-14 zh identity for legacy rows.** The zh interface must look exactly like 1.7.0: for a stored text, zh must show
  the *stored* text (1.5.1 no-space forms included), never a re-rendered `t(key)`; for keyed rows `t(key)` in zh equals
  the stored text only if both came from the same catalog (test_6 parity keeps ZH == the core literal).
- **H-15 Archive data key names.** `archives()` builds `dict(folder=…, **data)`: an additive key `folder` raises in
  every version (verified). `资料信息.json` / 阅读说明.txt are rewritten by whichever version saves (old versions write
  Chinese labels and raw titles into an English owner's folder).
- **H-16 Collect `options` are constructor kwargs.** `CollectOptions(**options)` in all versions: no additive keys
  inside `jobs.data.options` (collect) or `subscriptions.data.options`.
- **H-17 Log classification by text.** `ui_log.classify` tags rendered lines by their start in every language
  (`i18n.prefixes`, i18n.py:271-291); settings.py writes Chinese warnings straight to download.log (F50), never
  localized.
- **H-18 Sentinel strings.** `engine_version()` returns `'已内置'` as a value that is also compared (`version ==
  engine_version()`, `version_newer`); `collect.mode` / filter Tk values and `INTERVALS` labels are zh values compared
  in code (H4). Replacing a sentinel with a key changes those comparisons.

---------------------------------------------------------------------------------------------------

## 8. Tests that pin this side (by file)

- Goldens (byte-identical): `golden/runonce-1.5.1.json` via test_golden_runonce.py (F29 mirror at :101); `ui-1.5.1.json`
  / `ui-1.6.0.json` via test_golden_ui.py (live events with plain strings: 229 `title`, 247 `collect_error`, 238
  `update_error`, 281 `update_status`; history rows `[type, title, status]` incl. `… 条 · 音频 · 原始最佳`);
  `events-1.5.1.json` / `events-1.6.0.json` (engine; archive error text at test_golden_events.py:170).
- test_i18n_catalog.py: CoreParityTests test_6 (every CJK literal of collector / library / settings / updater is in the
  inventory and in LOCALIZE or a group), LocalizeTests test_7 (9.2 cases, the 1.5.1 collector forms and the joined
  pair), test_9 (10,000 lines < 1 s), HelperTests (`label`, `choices`, `all_values`, `prefixes`, joiners), ParityTests
  (H4 constants = ZH).
- test_i18n_pages.py: StoredTextTests 478-605 (history notes and titles, joined last_error, page-title fallback,
  archive / new items, 1.5.1 errors, engine badge), FinalFixRound2Tests 627-720 (CR2-1 媒体直链, CR2-2 titles, CR2-3
  core errors), FinalFixRound3Tests 741-805 (CR3-1 动态页面媒体请求, SWEEP-3 archive files, PAGE_TITLE_KEYS equal),
  EnglishPagesTests 943-991 (no CJK on the pages, English dialogs / exports, zh exports = 1.6.0 bytes).
- test_core_updater.py (286 已内置; 325-335, 470, 477 network messages and reasons; 357, 565 status / legacy title),
  test_v170_core.py (713 MSG_START, 788 MSG_RESTORE), test_v170_merge.py (42-48), test_review_state.py (941 MSG_SAVE).
- test_library_store.py (161, 337-355 `… 等 N 条`; 165 `errors[-1][:130]`; 430-433 raw collector error kept), test_library.py,
  test_polish_pages.py (213-216 titles, 238 raw line, 276 collect_error), test_ui_pages.py (30 temporary history, 178
  page_n, 180-196 collect_error -> last_error, 277 history note), test_core_paths_settings.py (1075-1102
  `历史记录本次不保存`), test_v170_ui.py (383-408 COLLECT_* constants, 608 `历史结果`, 790 non-JSON record),
  test_collection.py, test_packaged_collection.py (collect_error events), test_i18n_download.py (`已内置`, `[代理]`,
  stored_text), test_i18n_ui.py (filetext, last_reason).
