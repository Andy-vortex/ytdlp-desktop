<!-- Published copy of a 1.8.0 design-phase map (background for MESSAGES.md, which wins where they
     differ; line numbers are those of the 1.7.0 integration tree).  Local paths are written as ROOT
     (the source folder), %TEMP% and "the install folder". -->

# Map "download": every text the download side makes, where it goes, who shows it, who stores it

Input for the after-1.7.0 design "the core passes message keys + parameters; the UI looks the text up".
Mapper: download. Files: `engine.py, unfinished.py, progress.py, procutil.py, urls.py, naming.py, formats.py,
paths.py, applog.py, studio.py, ui_download.py, ui_log.py, app.py` of L2
(`ROOT\build\v170\i18n-L2\源码`, read 2026-11-17). Line numbers are L2 unless marked
**1.6.0** (= git HEAD `728bcf2`, `git show HEAD:src/<file>`) or **1.5.1**
(= `verification/backup-before-1.6.0-20261115/src`).

Method: an ast scan of every str / f-string literal with CJK (docstrings excluded, comments counted separately);
every function that emits an event, raises, logs or stores was read; consumers were followed into
`studio.py`, `ui_download.py`, `ui_log.py` and, where my events end up, into `library.py`, `library_ui.py`,
`ui_settings.py`, `collection_ui.py` (only as far as needed to name the consumer). Old readers were
read in the 1.6.0 and 1.5.1 sources, not assumed. A probe (`probe.py`) ran `i18n.localize` in English on a
copy of `src` in `%TEMP%\ytdlp180\download\` (scratch: `scan.py`, `table.py`,
`probe.py`, `h_*.py` = the 1.6.0 files); its results are quoted in section 8. Nothing in L2, the repo root,
the install folder's `data\` or the installed program was touched.

---------------------------------------------------------------------------------------------------

## 0. Summary

- 158 CJK literal nodes in the 13 files: engine 63, ui_download 36, unfinished 14, formats 14, ui_log 13,
  naming 7, studio 3, app 3, procutil 2, urls 2, paths 1, progress 0, applog 0 (plus comments/docstrings,
  section 7). Every one is accounted for in section 7.
- **55 flows** (F01-F55, section 4): 24 engine / unfinished / naming log lines (F01-F24), 3 waiting-reason
  rows (F25-F27), 6 item_done / done / unfinished-list / media-title flows (F28-F33), 11 exception texts that
  reach a dialog, hint or log (F34-F44), 3 texts the UI itself writes into storage or download.log (F45-F47),
  8 log-file-only / UI-log / window-title / self-test / folder-name / stored-value rows (F48-F55).
- The core reaches the screen through **7 channels** (section 2). Four of them end in a `localize()` call in
  my UI files; one ends in `library_ui` (history notes); one (python logging) is never translated.
- **15 `localize()` call sites** in my UI files (studio 2, ui_download 12, ui_log 1), all with `keys=None`.
  My files have **no key-prefix list** of the `PAGE_TITLE_KEYS` kind; their prefix machinery is
  `ui_log._ERROR_STARTS/_OK_STARTS` + `i18n.prefixes('error'|'ok')` + the `finished_unfinished` special rule.
  Section 5 says for each site whether it would disappear with keys: 14 disappear; 1 (`row_reason`,
  ui_download.py:1347) must stay as a read-side fallback for `entries[].error` stored by 1.6.0 / 1.7.0.
  Outside my files, `library_ui.history_note_text` / `history_title` must keep theirs for the reasons, errors
  and job titles this side stored in `library.sqlite3` (1.5.1 / 1.6.0 / 1.7.0 rows).
- Stored forms that hold text made on this side (section 3): `failed-downloads.json` `entries[].error`,
  `library.sqlite3 jobs.title` (placeholder), `jobs.data.errors`, `jobs.data.last_reason`,
  `jobs.data.result.archive_errors`, `download.log`. `settings.json` and `restart-state.json` hold no app-made
  message (only stored values `mode` / `resolution` and user input).
- Defects found in 1.7.0 that this class of design would remove (section 8): **H-1** the cookies echo
  error is stored without its `ERROR: ` prefix and matches no template; **H-2** `explain_error` cuts a core
  exception text to 70 characters, so `cookies.not_netscape` always and `cookies.missing` with a path longer
  than 35 characters stay Chinese in English (row detail, history note); **H-3** English-only core texts
  (`Options.from_dict`, `formats.by_*`, `build_command` 'Unsupported output option') show English in the
  Chinese interface; **H-4** `localize(keys=None)` on raw yt-dlp output rewrites a raw line that contains
  `；` and a part equal to a core text; plus the stored-data and H5 constraints listed there.

---------------------------------------------------------------------------------------------------

## 1. What 1.7.0 does on this side (short)

- The core (`engine, unfinished, naming, urls, formats`) emits / raises / stores the exact 1.6.0 Chinese text
  (I18N.md D5, 9.1). `progress, procutil, paths, applog` make no message text (procutil: window-title search
  only; paths: the folder name `下载文件`).
- The UI translates at display time: `ui_log.append_log` runs `i18n.localize(line)` on **every** log line
  (core, UI, raw yt-dlp) with no key filter; `render_wait` and `on_item_done` localize the reason;
  message boxes localize `str(error)`; `_unfinished_model` maps stored codes with `t()` / `label()`;
  `row_reason` localizes the stored error.
- The UI writes canonical Chinese into storage in two places so that 1.5.1 / 1.6.0 can read it:
  `stored_text('value.job_title.placeholder', ...)` (ui_download.py:186-189, 1579) and `history_value`
  storing `PROXY_REASON` (ui_download.py:165-173).

---------------------------------------------------------------------------------------------------

## 2. Channels (how text travels from non-UI code)

| ch | path | producer side | consumer side (how shown today) |
|---|---|---|---|
| C1 | event `'log'`, payload a **bare `str`** | `Runner.emit('log', text)` (engine), `unfinished._report(text, log)` via the merge callback (engine.py:2082), `naming.finalize(log=say)` (engine.py:1184-1188, 1256-1258), raw engine stdout (engine.py:1375) | `studio.handle` (569) -> `ui_download.on_log` (1980) -> `ui_log.append_log` (147-156): `localize(str(line))` keys=None, proxy -> `t('core.proxy_mask')`, `logger.info` (download.log), buffer -> `flush_log` (158-176) -> `classify` (58-73) tag -> log page. `on_log` also tests `startswith(CONVERTING)` (engine tokens, language-neutral). |
| C2 | event `'waiting'` `{index,total,seconds,reason,kind}` (H5) | `Runner._wait` (1157-1159), `_run_once` (1353-1354, H3) | `on_waiting` (2026) -> `render_wait` (1945-1957): `reason = localize(reason)` -> metrics `t('download.metrics.wait')`, row detail `f'{reason} · {time}'`, chip `t('download.chip.waiting')`; sidebar `shell.status.waiting` shows the time only. Not stored. |
| C3 | event `'item_done'` `{..., error, reason, title, stale_engine, format, skipped}` (H5) | `Runner._item_done` (1762-1773) | (a) `on_item_done` (2131-2179): row detail `localize(reason)`, compares `reason == engine.NETWORK_REASON / STALE_REASON`, row `error` raw -> reason dialog `localize` (1347); (b) `download_emit` (1660-1679) -> `history_value` (165) -> `library.download_event` (library.py ~609-633): `data['errors']` (last 20) and `data['last_reason'] = {error, reason}` -> `library_ui.history_note_text` (858-870, `explain_error` + `localize`). |
| C4 | event `'done'` (H5) | `Runner.download` finally (2092-2103, H3 block) | `on_done` (2187-2262): counts -> `t()` titles; `failed_path` -> `t('log.unfinished_list')`; `archive_errors` only tested for truth; `library.download_event` stores the whole payload in `data['result']` (never read back by any version). |
| C5 | `failed-downloads.json` | `unfinished.merge/save` from `Runner.download` finally (2075-2089) | `engine.load_unfinished()` (studio.py:199, ui_download.py:2192) -> `self.unfinished` -> `_unfinished_model` (861-876): `t(REASON_KEYS[reason])`, `label('format', ...)` / `label('mode', ...)`, `error` raw into the model -> `row_reason` `localize` (1347). |
| C6 | exception `str(error)` (ValueError / OSError) | `urls.parse_download_text`, `engine.build_command`, `check_cookie_file`, `parse_urls`, `unfinished.save`, `Options.from_dict`, library `Archive.*` | message boxes / hint / log lines with `localize(str(error))` (ui_download 574, 1370, 1421, 1519, 1570, 1586, 1632, 1686, 1807); inside the Runner the text becomes part of a log line (`此链接无法开始：{error}`) and of `item.error` (C3, C5). |
| C7 | python `logging` (applog file handler) | `unfinished._report` -> `_log.warning` (unfinished.py:214), `applog.get(...).exception(...)` (engine, ui_download, studio), `studio.report_error` (313) | `download.log` only, never translated, never read back. |

The log page never reads `download.log`: it shows its in-memory buffer; "打开日志文件" opens the file in
another program (`ui_log.open_log_file` 191-195). So "the app log file the log page reads" is write-only for
every version.

---------------------------------------------------------------------------------------------------

## 3. Stored forms written by this side, and how each old version reads them back

| file / table / field | written by (L2) | human text in it | 1.5.1 reads it how | 1.6.0 reads it how | 1.7.0 reads it how |
|---|---|---|---|---|---|
| `failed-downloads.json` top level `version, urls, options` (the legacy view) | `unfinished._encode` (296-301) via `save` (386-410); `engine.store_failed_job` (285-290) | no message; `options.mode` = zh `MODES` string, `options.resolution` = `最佳` / heights (stored values, H2) | `engine.load_failed_job` (1.5.1 engine.py:138-160): `version == 1`, `urls` list -> `parse_urls` (one non-link refuses the whole list), `options.output`; `mode` taken as is and checked against its `MODES` by `build_command` later; unknown keys and `entries` ignored. A 1.5.1 download run rewrites the file with its own view and **drops `entries`** (accepted limit, SPEC 1.6.0 §5). Run against new files by `test_unfinished.py:234,256,599`, `test_review_state.py:206,280,321,328`. | `unfinished.load/_read` (same code as L2, 0 changed lines): migrates a file without `entries`. | same as 1.6.0 |
| `failed-downloads.json` `entries[]`: `key` (link key), `format` (preset id, `id@res`), `reason` (`failed/stopped/unstarted/blocked`, a code), `time`, `referer`, `options` (compact; `mode` zh) | `unfinished.merge` (443-485) from `Runner.download` results (2075-2078) | **`error`**: `_masked(...)[:200]` of (a) the raw engine `ERROR:` line (`last_error`, 1293) - English yt-dlp text, possibly with the token `[代理]` (583); (b) `str()` of a core exception when the item could not start (1918, 1975) - Chinese core text of engine / library / check_cookie_file / LinkTooLong; (c) the cookies-echo fragment `cookies 文件中有一行格式不对，无法读取（内容不记录）` (1292-1293, H-1); (d) an unexpected exception (2050). **`title`**: media title (user data). | ignored | `unfinished._clean` (99-113): `reason` not in REASONS -> `failed`; `error`, `title` must be str else `''`; **extra keys are kept** (`dict(raw)`), survive `merge` for untouched entries (`dict(item)`, 465) and `_written` (292); a replaced entry is rebuilt without them (474-476). Shows `reason` via `REASON_WORDS` (1.6.0 ui_download.py:757), `error` **raw** in the reason dialog (1.6.0 ui_download.py:1131-1136). | same `_clean`; `reason` -> `t(REASON_KEYS[...])`; `error` -> `localize(keys=None)` in the reason dialog (1347). |
| `failed-downloads.bad-<time>.json`, `failed-downloads.overflow-<time>.json` | `unfinished._set_aside` (230-246), `_write_overflow` (329-356) | same content as above (set aside / older part) | never read | never read (kept for the owner) | never read |
| `library.sqlite3 jobs.title` (download job) | `ui_download.launch` -> `stored_text('value.job_title.placeholder', count, mode)` (1579) = `'{n} 条 · {mode}'` zh; then `library.download_event('title')` replaces it by the first media title or `<title> 等 N 条` (library.py ~575-580, library mapper) | placeholder = canonical zh with a zh mode string; 1.5.1 wrote `'{n}条 · {mode}'` (1.5.1 studio.py:580) | shows it raw (1.5.1 library_ui.py:136) | **parses** it: `PLACEHOLDER_TITLE = re.compile(r'\d+\s*条 · .+')` (1.6.0 library_ui.py:29, 62-74) and then shows the first link instead; else raw | `library_ui.history_title` (330-335): `localize(title, keys=TITLED/UNTITLED_JOB_KEYS)` (VARIANT for the 1.5.1 form) |
| `jobs.data.options` | `launch` (1575-1577) `{**options.to_dict(), 'urls', 'overrides'}` | `mode` zh, `resolution` zh (stored values) | resume reads `mode` (1.6.0-only modes cannot be resumed; logged) | resume via `Options.from_dict` | same |
| `jobs.data.errors` (list, last 20) | `library.download_event('item_done')` from my `item_done.error` (library.py ~626-628) | same kinds as `entries[].error` (raw engine text or Chinese core exception text) | 1.5.1 history note: `最近错误：{errors[-1][:130]}` raw (1.5.1 library_ui.py:152-153) | `history_note_text`: `last_reason.reason` when it belongs to `errors[-1]`, else `explain_error(errors[-1])`; shown raw (1.6.0 library_ui.py:361-370) | same, then `localize()` (library_ui.py:858-870) |
| `jobs.data.last_reason = {error, reason}` | library.py ~629-633 from `item_done.reason` after `history_value` | `reason` = `explain_error` result (a Chinese constant of ERROR_REASONS, `BOT_CHECK_REASON`, `STALE_REASON`, `下载未完成（退出码 N）`, `ENGINE_ERROR_REASON`, or the fallback: the error text cut to 70 chars) or `PROXY_REASON` (ui_download.py:100, 172) | ignored | shown raw | `localize()` (`download.reason.proxy` and `core.reason.*` are in LOCALIZE) |
| `jobs.data.result` | library.py ~635 = the whole `done` payload | `archive_errors`: `str(error)` of archive failures (library Chinese texts, `资料库无法写入：…`, sqlite English); `failed_path` (path) | written by 1.5.1 too; never read | never read | never read |
| `library.sqlite3 downloads(title, path ...)` | library `_record_download` from my `media` events | media title / path (user data) | table unknown to 1.5.1 | `find_download` -> `item.title` of a skipped item | same |
| `settings.json` (keys this side writes) | `studio.save_settings` (511-533): `output, mode, resolution, batch_safe, archive_enabled, archive_root, cookies` (path only, H6), `naming, embed_metadata, embed_cover, subtitles, skip_downloaded, notify_done, clipboard_hint, check_engine_updates, geometry, zoomed` + every other prefs key; `ui_download` adds `last_clip` (sha1, 660) and `bot_check_at` (float, 1921) | **no message text**. Stored values: `mode` zh `MODES`, `resolution` `最佳`/heights, `output` default `…\下载文件` (D10) | `mode` checked `in MODES` else `MODES[0]`, `resolution` `in RESOLUTIONS` (1.5.1 studio.py:78-88); writes back only its 7 keys (drops the rest) | `settings.load` typed checks | same + `_extra.language` |
| `restart-state.json` (beside settings.json) | `ui_settings.restart_app` (393-399; not my file - `app.py` only takes `--restarted-from PID`, 22-23, 65-68) | none: `version, created, pid` + user input `urls, collect_urls, subscription_name` | never opened | never opened | `restore_restart_state` (ui_settings 432-483): read once, deleted in every case |
| `download.log` (+ `.1`, `.2`, RotatingFileHandler 3 MB) | `applog.setup` (21-44); lines from `append_log` (155, **already localized**, running language), `unfinished._report` (214, **always zh**, WARNING), `applog...exception` (English), `studio.report_error` (`界面错误` + traceback), app.py language line (96-97) | mixed: what the owner saw + zh WARNING notices + English tracebacks | 1.5.1 appends its own lines with format `%(asctime)s %(message)s` (1.5.1 studio.py:74-75); nobody parses | write-only | write-only |
| self-test report (`--self-test REPORT`) | app.py:28-59 | diagnostics; on failure a traceback with `下载自检需要 --smoke-output 指定测试目录。` / `打包下载自检失败：{events}` | (own report) | read by `verify_release.py` / packaging tests, never by the window | same |

Additivity facts verified in code (for a design that adds fields): `jobs.data` is a JSON dict read and
written whole by 1.5.1 (1.5.1 library.py:163-181), 1.6.0 and 1.7.0, so an extra key survives every version;
`jobs.title` is a single TEXT column (no room for an additive key: a key form would have to go into
`jobs.data`); `failed-downloads.json` `entries[]` extra keys survive 1.6.0 / 1.7.0 for untouched entries and
disappear when 1.5.1 writes the file; top-level extra keys of `failed-downloads.json` are ignored by 1.5.1
and dropped by `_encode` of 1.6.0 / 1.7.0 (it writes exactly `version, urls, options, entries`).
`settings.json` extra keys survive 1.6.0 / 1.7.0 (`_extra`) and are dropped by 1.5.1's save.

---------------------------------------------------------------------------------------------------

## 4. Flows

Columns: producer (file:line, zh literal / template) · path · consumers (file:line, how shown) ·
stored (where; old readers) · catalog key (map) · H3 / H4 · tests that pin it. "append_log" = C1 as described
in section 2 (localize keys=None -> download.log localized -> classify -> log page). Tests in *italics* pin
the zh text byte for byte through recorded goldens.

### 4.1 Engine log lines (channel C1)

| # | producer | path | consumers | stored | key | H3/H4 | tests |
|---|---|---|---|---|---|---|---|
| F01 | engine.py:1151 `Runner._wait`: `f'{reason}，等待 {math.ceil(seconds)} 秒；可随时点击停止。'`; `reason` from 1668 `'网站限流，暂停整个下载队列'`, 1672 `f'网站限流，冷却后重试此链接（{self._rate_attempt}/{MAX_RATE_LIMIT_RETRIES}）'`, 1850 `'下载间隔'` | `'log'` str | append_log: template `core.engine.waiting` matched before the `；` split, `reason` via RECURSE; classify -> app | download.log (localized) | `core.engine.waiting` + `core.wait.rate_queue/rate_retry/pace` (LOCALIZE) | `_wait` not protected; 1668/1672 are the 1.6.0 "429 loop (verbatim)" (SPEC §3.7 step 6), moved verbatim into `_cooled` in 1.7.0; 1850 not protected | *events-1.5.1/1.6.0.json* (via test_golden_events), test_queue, test_v170_core, test_i18n_catalog (round trip) |
| F02 | engine.py:1195 `_intercept`: `COOKIE_LINE_FAILED` / `COOKIE_LINE_SKIPPED` (266-267) `'ERROR: cookies 文件中有一行格式不对，无法读取（内容不记录）'`, `'WARNING: …已跳过（内容不记录）'` in place of a raw line that echoes the cookies file | `'log'` str | append_log exact match; classify by the language-neutral `ERROR`/`WARNING` start | download.log | `core.cookies.line_failed/line_skipped` | module constants read by tests (H4-like) | test_review_engine.py:244, 308, 312 (also the H6 search over the sandbox) |
| F03 | engine.py:1200 `f'保存位置已有这一条（{filtered.group(1)}，之前下载的），已跳过，原文件保留不动。'` (`{name}` = engine's title) | `'log'` | append_log | download.log | `core.engine.present_skipped` | - | test_v170_core |
| F04 | engine.py:1215, 1254, 1746 `f'保存位置已有这个文件（之前下载的），保留原文件名：{Path(...).name}'` | `'log'` (1215, 1254 through `say`) | append_log | download.log | `core.engine.kept_existing` | - | test_queue, test_integration, test_v170_core |
| F05 | engine.py:997-999 `_source_line(name)`: `f'保存位置已有这个链接的另一种格式（{name}，之前下载的）；本条用它转换成所选格式，原文件保留不动。'`, emitted at 1223 (`say`) and 1939 | `'log'` | append_log (template with `；`, matched before split) | download.log | `core.engine.other_format_source` | - | none found by text |
| F06 | naming.py:88 `f'文件正被其他程序占用，未能改名，已保留原文件名：{source.name}'`, 92 `f'文件未能改名（{error.strerror or type(error).__name__}），已保留原文件名：{source.name}'`, 96 `f'同名文件过多，未能改名，已保留原文件名：{source.name}'`, 98 `f'文件未能改名（{type(error).__name__}），已保留原文件名。'` | `naming.finalize(log=say)` -> engine.py:1184-1188 -> `'log'` | append_log; `{reason}` via RECURSE (a Windows `strerror` passes as is) | download.log | `core.rename.locked/failed_named/too_many/failed` | - | test_formats_naming (behaviour, not text) |
| F07 | engine.py:1373 `f'归档信息保存失败（媒体已保留）：{error}'` (in `_run_once`), 1738 (in `_present_done`); `{error}` = `str()` of archive errors, incl. engine.py:698 `f'资料库无法写入：{error}'` (`_SafeArchive`) and library `Archive.media` texts | `'log'`; also `self.archive_errors.append(str(error))` -> `done.archive_errors` (F31) | append_log, RECURSE `error` | download.log; `jobs.data.result.archive_errors` (never read) | `core.engine.archive_info_failed`, `core.archive.unwritable`, `core.archive.*` | **1373 inside H3 `_run_once` loop** | *runonce-1.5.1.json* (test_golden_runonce, byte-identical), *events goldens* |
| F08 | engine.py:1653 `f'无法启动下载引擎：{_sentence(error)}。可在“偏好设置”恢复内置引擎。'` (`_sentence` drops the OSError's own `。`) | `'log'` | append_log; classify error (TAGS) | download.log | `core.engine.cannot_start_engine` (TAGS error) | in `_cooled` (1.6.0: in the download loop 1510/1545/1581) | test_queue, test_review_engine (by `无法启动下载引擎`) |
| F09 | engine.py:1662 `'此链接多次触发 HTTP 429，已记入失败清单。'` | `'log'` | append_log | download.log | `core.engine.rate_limited_out` | 429 loop (verbatim, see F01) | *events-1.5.1/1.6.0.json*, test_v170_core |
| F10 | engine.py:1710-1711 `f'保存位置已有本条的 {N} 个文件；为了不改动它们，本次新下载的文件不写入标签、封面和字幕。'` | `'log'` | append_log (template with `；`) | download.log | `core.engine.present_untagged` | - | test_v170_core |
| F11 | engine.py:1806 `f'重复链接已合并：{url}'` | `'log'` | append_log | download.log | `core.engine.duplicate_merged` | - | *events-1.6.0.json*, test_queue |
| F12 | engine.py:1834 `f'下载记录无法读取，照常下载：{error}'` (library / sqlite error) | `'log'` | append_log, RECURSE `error` | download.log | `core.engine.record_unreadable` | - | test_queue |
| F13 | engine.py:1875 `'找不到原采集历史，本次归档保留来源链接，未附网页正文。'` | `'log'` | append_log | download.log | `core.engine.no_collect_history` | - | *events-1.5.1/1.6.0.json* |
| F14 | engine.py:1889 `'保存位置有这个链接之前没下载完的部分文件，本条直接在保存位置继续下载。'` | `'log'` | append_log | download.log | `core.engine.resume_partial` | - | test_review_engine |
| F15 | engine.py:1901-1902 `f'保存位置已有与本条同名的图片（{_shown_name(...)}），本条不嵌入封面，图片保留不动。'` | `'log'` | append_log | download.log | `core.engine.picture_exists` | - | none found by text |
| F16 | engine.py:1916, 1974 `f'此链接无法开始：{error}'`; `{error}` = `str()` of: 464 `'代理地址应以 http://、https:// 或 socks5:// 开头。'`, 472 `'采集来源地址无效，请重新采集后加入下载列表。'`, 491 `f'链接过长（{len(url)} 个字符），无法交给下载引擎：{urltools.display(url)}'`, 243-255 the four `check_cookie_file` texts, 698 `资料库无法写入：…`, library `Archive.prepare` texts (`core.archive.*`), 425 `'Unsupported output option'` (English), any OSError (Windows text), sqlite3.Error (English) | `'log'`; the same `str(error)` also becomes `item.error` (1918; F28) and, through `explain_error`, `item.reason` (F27) | append_log, RECURSE `error`; classify error (TAGS) | download.log; `item.error` -> failed-downloads.json + history (F28, F30) | `core.engine.cannot_start_link` (TAGS error) + `core.proxy.scheme`, `core.collect.bad_source`, `core.links.too_long`, `core.cookies.*`, `core.archive.*` | - | *events-1.6.0.json* (`此链接无法开始：采集来源地址无效…`, `<OSERROR>`), test_golden_events, test_queue.py:230-234, test_review_engine |
| F17 | engine.py:1963 `'字幕下载失败，已改为不带字幕重新下载此条。'` | `'log'` | append_log | download.log | `core.engine.subtitles_dropped` | - | *events-1.6.0.json*, test_queue, test_v170_core |
| F18 | engine.py:2025 `'当前 yt-dlp 引擎与本程序不兼容，请在“偏好设置”恢复内置引擎。'` | `'log'` | append_log | download.log | `core.engine.incompatible` | - | *events-1.6.0.json*, test_queue |
| F19 | engine.py:2039 `BOT_CHECK_STALE_HINT` (59) / `BOT_CHECK_HINT` (57-58) | `'log'` | append_log | download.log | `core.engine.bot_check_stale/bot_check_hint` | constants read by tests (H4-like) | *events-1.6.0.json*, test_engine |
| F20 | engine.py:2041 `f'此链接未完成（退出码 {result}），继续处理队列中的下一项。'` | `'log'` | append_log; classify error (zh `_ERROR_STARTS` `此链接未完成`, en TAGS prefix) | download.log | `core.engine.item_unfinished` (TAGS error) | - | *events-1.5.1/1.6.0.json*, test_queue, test_ui_state, test_i18n_download (classify) |
| F21 | engine.py:2052 `f'错误：{error}'` (queue aborted; any exception text) | `'log'` | append_log, RECURSE `error`; classify error | download.log | `core.engine.error` (TAGS error) | - | *events-1.5.1/1.6.0.json* (`错误：boom`, `错误：library is locked`), test_golden_events, test_queue, many UI tests |
| F22 | engine.py:2089 `f'失败清单未能保存：{error}'`; `{error}` = unfinished.py:246 `f'无法保留读不出的未完成清单：{path}'`, 349 `f'找不到可用的文件名保存未完成清单的较早部分：{path}'`, or a Windows OSError | `'log'` | append_log, RECURSE `error` (a Windows message stays as it is) | download.log | `core.engine.unfinished_not_saved`, `core.unfinished.cannot_set_aside/no_overflow_name` | **inside H3: the failed-list merge in `Runner.download`'s `finally`** | *events-1.5.1/1.6.0.json* (`失败清单未能保存：<OSERROR>`), test_golden_events, test_review_state.py:247, 443, test_unfinished |
| F23 | unfinished.py:251-252 `f'未完成清单（failed-downloads.json）{problem}，已原样保留为 {target.name}（没有删除），本次结果写入新的清单。'` with `problem` from `_read` 173/179 `'无法读取'`, 175 `f'超过 5 MB（{size:.1f} MB）'`, 181 `'内容已损坏'`, 185/189/201 `'格式无法识别'`, 192 `f'有 {dropped} 条记录无法识别'`, 198 `'链接或设置无法识别'`; unfinished.py:356 `f'未完成清单过长，已把最早的 {len(moved)} 条移到 {target.name}（没有删除）。'` | `_report(text, log)` (211-221): (a) `_log.warning` -> download.log **zh always**; (b) `log(text)` = engine.py:2082 `lambda text: self.emit('log', text)` -> `'log'` (only on the worker thread; `save()` from the window thread in `remove_row` / `clear_unfinished` has no callback, so those notices reach download.log only) | append_log: `core.unfinished.kept_aside` with RECURSE `problem` (prefix `core.unfinished.problem.`), `core.unfinished.overflow` | download.log twice (zh WARNING + localized INFO) | `core.unfinished.kept_aside`, `core.unfinished.overflow`, `core.unfinished.problem.*` | emitted from inside the H3 `finally` block (the callback is passed there) | test_review_state.py:163 (`超过 5 MB` in the WARNING), 196 (`未完成清单`), 319 (`最早的 N 条`), test_unfinished |
| F24 | raw engine output (yt-dlp / ffmpeg / deno lines) passed on unchanged: engine.py:1375 `self.emit(kind, value)` for `kind == 'log'`; yt-dlp lines that start with a plain `__GUI_` marker get a leading space (648) | `'log'` | append_log: `localize(keys=None)` matches nothing in the normal case and passes the line; **a raw line equal to a core text, or one that contains `；` with a part equal to a core text, is rewritten** (H-4); proxy masked; classify: `ERROR`/`WARNING` start, `[extractor]` dim, `_SLEEPING` lines dropped from the page (168-169) but kept in download.log; `on_log` tests `CONVERTING` (56-57) | download.log | none (unknown text) | - | test_ui_state, test_i18n_download.py:248-273 (append_log) |

### 4.2 Waiting reasons (channel C2)

| # | producer | path | consumers | stored | key | H3/H4 | tests |
|---|---|---|---|---|---|---|---|
| F25 | engine.py:1157-1159 `'waiting'` payload `reason` = the `_wait` argument: 1668 `'网站限流，暂停整个下载队列'`, 1672 `'网站限流，冷却后重试此链接（{a}/{l}）'`, 1850 `'下载间隔'`; `kind` additive (`rate_limit`/`interval`) | `'waiting'` dict (H5) | `on_waiting` 2026 -> `render_wait` 1953 `localize(reason)` -> `t('download.metrics.wait', reason, time)` (1954), row detail `f'{reason} · {time_text}'` (1956) | no | `core.wait.rate_queue/rate_retry/pace` | payload H5 | *ui-1.5.1/1.6.0.json* (test_golden_ui), *events goldens*, test_queue, snapshot_ui |
| F26 | engine.py:1353-1354 `'waiting'` payload `reason='下载间隔 / 重试等待'` (yt-dlp's own `Sleeping N seconds`) | `'waiting'` | same as F25 | no | `core.wait.pace_or_retry` | **inside H3 `_run_once` loop**; payload H5 | *runonce-1.5.1.json* (byte-identical), *ui goldens*, test_golden_ui |
| F27 | (UI) `refresh_shell` studio.py:472-473 `t('shell.status.waiting', time=…)` | - | sidebar | no | UI key | - | test_i18n_download |

### 4.3 Item results, the unfinished list, job end (channels C3, C4, C5)

| # | producer | path | consumers | stored | key | H3/H4 | tests |
|---|---|---|---|---|---|---|---|
| F28 | `item.reason` = engine.py:100-112 `explain_error()`: ERROR_REASONS 68-82 (`'该地区不可观看，可更换网络节点'`, `'视频不存在或已设为私享'`, `'页面不存在或无权访问（403/404/410）'`, `'连不上网站，请检查网络或 VPN'` (= `NETWORK_REASON` 84), `'网站拒绝访问（403）'`, `'链接不存在（404）'`, `'链接已失效（410）'`, `'没有可用的所选格式或画质'`, `'不支持这个链接'`), `BOT_CHECK_REASON` 92, `STALE_REASON` 93, 111 `f'下载未完成（退出码 {code}）'`, fallback 112 = the error text without an `[extractor] id: ` prefix **cut to 70 chars**; also `ENGINE_ERROR_REASON` 94 (2027) and `explain_error('', bot_check=True)` for blocked items (1821); set at 1767, 1821, 2027, 2040, 2051 | `'item_done'.reason` (1768-1771, H5) | (a) `on_item_done` 2164-2178: `localize(canonical)` keys=None; compares `canonical == engine.NETWORK_REASON` (-> `t('download.reason.proxy')` when the row went through a proxy) and `== engine.STALE_REASON` (-> `download.detail.stale_update/stale_maybe` templates); blocked rows show `t('download.detail.blocked')` instead; (b) `history_value` 170-172 replaces `NETWORK_REASON` by `PROXY_REASON` for proxied jobs -> `library.download_event` -> `data.last_reason.reason` (only for items with an `error`) -> `library_ui.history_note_text` 862-869 `localize` | `jobs.data.last_reason.reason`; 1.5.1 ignores; 1.6.0 shows raw; 1.7.0 localizes | `core.reason.*`, `core.reason.exit_code`, `download.reason.proxy` (LOCALIZE); fallback: whatever key the cut text still matches (H-2) | `ERROR_REASONS`, `NETWORK_REASON`, `BOT_CHECK_REASON`, `STALE_REASON`, `ENGINE_ERROR_REASON` are module constants compared by the UI and read by tests (H4-like); payload H5 | *events-1.6.0.json* (`下载未完成（退出码 1）` ×19, `YouTube 暂时要求验证…`, `视频不存在…`), *ui-1.6.0.json*, test_engine, test_queue.py:172, 205-207, 233-234, test_review_engine, test_i18n_download.py:362-418, test_i18n_pages (history notes) |
| F29 | `item.error`: engine.py:2005 `_masked(self.last_error, item.options)[:200]` where `last_error` = 1293 `line[6:].strip()[:200]` of the engine's `ERROR:` line - or **`COOKIE_LINE_FAILED[6:]` = `'cookies 文件中有一行格式不对，无法读取（内容不记录）'`** (1292); 1918/1975 `_masked(str(build error))[:200]` (Chinese core texts of F16); 2050 abort; `_masked` (577-587) replaces the proxy by the token `'[代理]'` (583) | `'item_done'.error` (H5); also `results[].error` (2076) -> `unfinished.merge` | (a) `on_item_done` 2178 row `error` -> `row_reason` 1347 `localize(str(error))` -> `t('dialog.reason.engine_error', error=…)` (1348) in `show_row_reason` (1350-1354), shown only when not already part of the reason; (b) history `data.errors` -> `history_note_text` (explain_error then localize) | `failed-downloads.json entries[].error`; `jobs.data.errors`, `jobs.data.last_reason.error`. 1.5.1: entries ignored, `errors[-1][:130]` raw in its history note. 1.6.0: raw in reason dialog and history note | none for the raw engine text; `core.*` texts of F16 when the item could not start; **nothing matches the cookies fragment** (H-1); `[代理]` -> token rule (`core.proxy_mask`) | - | test_queue.py:221 (`[代理]` in error), 233; test_i18n_download sweep_1 (707-740); test_review_engine (cookie test stores the failed entry) |
| F30 | failed-downloads.json `entries[]` (`reason` code, `format` id, `options.mode`, `error`, `title`) written by `unfinished.merge/save` (F29 path) and read by `engine.load_unfinished` | file -> `self.unfinished` (studio.py:199, ui_download 2192, remove/clear) | `_unfinished_model` 861-876: `t(REASON_KEYS[reason])` (105-106), `label('format', formats.by_key(format).label)` / fallback `label('mode', options.mode)`, `t('download.detail.unfinished', reason, format)`; title = stored media title or `link_title(url)`; `error` -> `row_reason` 1347 `localize`; `refresh_failed_button` 1755-1762 counts; bot retry selects `reason == 'blocked'` (1899, 1912) | itself (see section 3) | `download.reason.*`, `download.chip.*`, `format.*`, `value.mode.*` (GROUPS `format`, `mode`) | `REASON_WORDS` (85, H4 zh dict) kept, not used for display | test_i18n_download.py:321-338, test_unfinished, test_review_state, test_final_fix_ui |
| F31 | `done.archive_errors` = engine.py:1374, 1739 `str(error)` list (F07) | `'done'` (H5) | `on_done` 2239-2240: only truthiness -> `t('download.metrics.archive_errors')` | `jobs.data.result.archive_errors` (no version reads it) | none needed | inside the H3 finally (payload built there) | *runonce-1.5.1.json* (`archive_errors` list) |
| F32 | `done.failed_path` (2085, path) | `'done'` | `on_done` 2250-2252 `t('log.unfinished_list', path=…)` -> append_log | `jobs.data.result.failed_path` | UI key | H3 finally | *ui-1.6.0.json* (`未完成列表：<STATE>\failed-downloads.json`) |
| F33 | `item_done.title`, `'title'` event (1274-1277), `'media'.title` (1263-1264): media titles from the engine (user data, `_safe_text`) | events | `on_title` 2017-2024, `on_media` 2079-2081, `on_item_done` 2138-2139 -> row title; `live_metrics` / `_active_title` append the title; library job title | `jobs.title`, `downloads.title`, `entries[].title` | none (never translated; must not be) | - | *events goldens* (`白羊`, `已下载的歌`) |

### 4.4 Exception texts that reach a dialog, the hint or a UI log line (channel C6)

| # | producer | path | consumers | stored | key | H3/H4 | tests |
|---|---|---|---|---|---|---|---|
| F34 | urls.py:143 `f'第 {number} 行没有找到链接。请每行粘贴一个链接。'`, 148 `'请先粘贴至少一个视频或音频链接。'` (`parse_download_text`) | ValueError -> `read_form` 1470 | `start` 1519 `messagebox.showwarning(t('dialog.check_input'), localize(str(error)))`; `enqueue_links` 1686 `set_hint(localize(str(error)))` | no | `core.links.no_link_on_line`, `core.links.none` | - | test_urls (zh messages), test_final_fix_ui |
| F35 | engine.py:464, 472, 491 (`LinkTooLong`), 243-255 (`check_cookie_file`), 425 (`'Unsupported output option'`, English) via `build_command` | ValueError -> `read_form` 1502 (LinkTooLong passed on), `launch` 1558-1566 | `launch` 1570 messagebox `localize(str(error))` (also `raise too_long[0]` when every link is too long); `start` 1519; `enqueue_links` 1686 | no | `core.proxy.scheme`, `core.collect.bad_source`, `core.links.too_long`, `core.cookies.*` | - | test_final_fix_ui (J_FinalMergeRequestTests), test_engine |
| F36 | engine.py:233-256 `check_cookie_file` (4 texts) | ValueError | `ui_settings.choose_cookies` 494-497 `localize(str(error))` (settings mapper) | no | `core.cookies.*` | - | test_review_engine.py:251-257 (`Netscape`), test_i18n_download (missing) |
| F37 | engine.py:115-128 `parse_urls` (123 `f'第 {line_number} 行不是有效的 HTTP / HTTPS 链接。请每行填写一个链接。'`, 127 `'请先粘贴至少一个视频或音频链接。'`), 131-142 `read_url_file` (134, 142) | ValueError | `collection_ui.py:318-325` messagebox `localize` and `collector.py:99` (collect mapper); `read_url_file` has no caller in src (1.5.1 API) | no | `core.links.bad_line/none/file_too_large/encoding` | - | test_urls, test_collection |
| F38 | engine.py:174-210 `Options.from_dict` (`'Options data must be an object'`, `'Options data has no usable output folder'`, `f'Unsupported output format: {mode!r}'`, `f'Unsupported resolution: {resolution!r}'`), formats.py:75, 82, 89 `by_mode/by_id/by_label` (`'Unsupported output format…'`) - **English only** | ValueError | `_entry_options` 1368-1371 `append_log(t('log.retry_failed', url, error=localize(str(error))))` (localize is a no-op); `_queued_options` 2128 silent; `_unfinished_model` 868 catches `by_key` | download.log | **none** (English in both languages, H-3) | - | test_unfinished, test_core_engine (behaviour) |
| F39 | unfinished.py:246, 349 (OSError texts, see F22) and Windows OSErrors from `unfinished.save` | OSError | `remove_row` 1416-1421 and `clear_unfinished` 1803-1807 `messagebox.showwarning(t('dialog.remove_failed'/'dialog.clear_failed'), localize(str(error)))` | no | `core.unfinished.cannot_set_aside/no_overflow_name` | - | test_i18n_download sweep_2 (742-766) |
| F40 | ui_download.py:139-151 `decode_link_file` raises `ValueError(i18n.t('core.links.file_too_large'))` / `t('core.links.encoding')` (the UI already uses the core keys) and OSError | ValueError / OSError | `import_txt` 571-575 `localize(str(error))` (no-op: already localized or a Windows text) | no | `core.links.file_too_large/encoding` | - | test_ui_pages / test_i18n_download (import) |
| F41 | `launch` 1541 `ValueError(i18n.t('core.links.none'))` (UI-made, already localized); any exception at 1615-1632 | ValueError / Exception | 1570 / 1632 messagebox `localize(str(error))` | no | `core.links.none` | - | test_final_fix_ui |
| F42 | library `create_job` / `history_create` errors (library mapper texts, e.g. `core.library.*`) | exception at 1584 | `append_log(t('log.history_write_failed', error=localize(str(error))))` 1586; `events.put(('log', t('log.history_write_failed_short')))` 1676 | download.log | `core.library.*` | - | test_ui_state (R2) |
| F43 | `updater.engine_version()` -> `'已内置'` when the built-in version is unknown (updater mapper) | return value | studio.py:244 `t('log.ready', version=localize(engine_version()))`; studio.py:357 badge `f'yt-dlp {localize(engine_version())}'` | download.log (log.ready) | `core.update.builtin` | - | test_i18n_pages.py:609 (badges) |
| F44 | `Runner.update()` (engine.py:2105-2106) with `legacy_events=True`: updater status texts as `'title'` events, progress as `'progress'` | events | not used by the window (ui_settings runs `EngineUpdater` itself, ui_settings.py:551); a stray legacy `'title'` is ignored by `on_title` unless a download runs; `on_progress` 2065-2066 has a legacy branch (`settings.update.downloading_pct`) | no | `core.update.*` | - | test_core_updater.py:562-600 (`test_legacy_events_are_the_kinds_of_1_5_1`, `runner.update()`) |

### 4.5 Text the UI itself writes into storage, the log file, the window title

| # | producer | path | consumers | stored | key | H3/H4 | tests |
|---|---|---|---|---|---|---|---|
| F45 | ui_download.py:186-189 `stored_text(key, **values)` = `i18n.all_values(key)[0].format(...)`, used at 1579 `stored_text('value.job_title.placeholder', count=len(urls), mode=options.mode)` -> `'{n} 条 · {mode}'` | `library.create_job('download', title, data)` 1583 | `library_ui.history_title` (localize with keys) / 1.6.0 regex / 1.5.1 raw (section 3) | `jobs.title` | `value.job_title.placeholder` (+ VARIANT `{count}条 · {mode}` of 1.5.1; RECURSE `mode` -> `value.mode.`) | - | test_i18n_download.py:339-360, test_i18n_pages (history titles) |
| F46 | ui_download.py:165-173 `history_value`: `{**value, 'reason': PROXY_REASON}` (100) when `value['reason'] == engine.NETWORK_REASON` and the job runs through a proxy | `download_emit` 1665 -> `library.download_event` | history note (`localize`) | `jobs.data.last_reason.reason` | `download.reason.proxy` (LOCALIZE) | `PROXY_REASON` H4 constant (parity table) | test_polish_download, test_i18n_pages |
| F47 | ui_log.py:147-156 `append_log`: every line (UI `t()` text, core canonical text, raw engine text) -> `localize` -> proxy mask `t('core.proxy_mask')` -> `self.logger.info(shown)` | `download.log` | the owner, in another program | `download.log` in the running language (a language switch makes the file bilingual; 1.5.1 lines mixed in after a rollback) | - | - | test_i18n_download.py:248-273 |
| F48 | log-file-only lines: studio.py:313 `applog.get('ui').error('界面错误', exc_info=…)`; ui_download.py:1738 `applog...exception('加入队列: the added links could not be recorded in the history job')`; engine.py:1281, 2045, 2067, 2088 (English); unfinished.py:214 (F23 a); app.py:96-97 language line; applog.py:79, 91 hooks | python logging | download.log only | download.log | none (`logfile` flag in the inventory) | - | test_core_paths_settings, test_ui_state (`界面错误`) |
| F49 | studio.py:310-316 `report_error`: `append_log(t('log.ui_error', kind=kind.__name__, value=value))` - `value` = the exception, possibly a Chinese core ValueError, inserted raw into the English template (the whole line is not a LOCALIZE template, so `{value}` is never localized) | append_log | log page + download.log | download.log | `log.ui_error` (TAGS error) | - | test_ui_state |
| F50 | window title: studio.py:132 / ui_download.py:717-722 `t('app.title')`, `t('window.title.done/unfinished')`; constants `TITLE = 'YT-DLP 下载器'` (studio 38, ui_download 41); procutil.py:388, 397 default `title_suffix='YT-DLP 下载器'`; app.py:75 `procutil.focus_window(i18n.all_values('app.title'))` | Win32 title | a second launch finds the running window by either language's title | no | `app.title` (all_values) | `TITLE` H4 | test_i18n_detect, test_i18n_catalog, verify_release |
| F51 | studio.py:244-248, 261, 533, 638 UI log lines / notice (`log.ready`, `log.settings_held`, `shell.notice.settings_held` with H4 `SETTINGS_HELD_NOTICE` 51, `log.settings_not_saved`, `log.watchdog`) | append_log / notice bar | log page, download.log | download.log | UI keys | `SETTINGS_HELD_NOTICE` H4 | test_v170_ui.py:615-650 |
| F52 | ui_download.py:1598-1604 `log.task_started` with `format=label('format', formats.by_mode(options.mode).label)`, `folder=archive_root or output`; 2000 `log.link_n` (url); 2084 `log.file_saved` (path); 1701 `log.queued`; 1784 `log.retry.other_format_later`; 2249 `append_log(title)` (the job-end header) | append_log | log page (classify: `download.title.*` and `log.file_saved` tagged ok/error), download.log | download.log | UI keys + GROUPS `format` | - | test_i18n_download.py:339-360, *ui goldens* (zh) |
| F53 | app.py:39 `'下载自检需要 --smoke-output 指定测试目录。'`, 50 `f'打包下载自检失败：{events}'` | ValueError / RuntimeError -> traceback | self-test report JSON (`--self-test`) | the report file | none (`internal`) | - | verify_release, test_release_tooling |
| F54 | engine.py:1877 `'媒体'` (archive media folder), app.py:47 `'资料归档'`, paths.py:102 `'下载文件'` (default output) | folder names | file system; `settings.json output` holds the path | folder names (D10) | none (`file`) | - | test_core_paths_settings, events goldens (`\媒体\`) |
| F55 | naming.py:16 `LABELS` (`'标题（推荐）'`, `'精简标题'`, `'标题 [视频 ID]'`), formats.py:25-43 `PRESETS` (`mode`, `label`), formats.py:53 `BEST = '最佳'`, engine.py:45 `RESOLUTIONS[0] = '最佳'` | stored / compared values (not messages) | menus show `label('naming'/'format'/'resolution'/'mode', value)` (ui_download 285-293; ui_settings for naming); `StudioApp.naming_label` keeps the zh value | settings.json `mode`, `resolution`, `naming` (id); failed-downloads.json and job options `mode`, `resolution` | GROUPS `naming`, `format`, `mode`, `resolution` | **H4** (parity table: `formats.MODES/LEGACY_MODES/PRESETS/BEST`, `naming.LABELS`, `ui_download.FORMATS`, `studio.FORMATS`) | test_formats_naming, test_core_engine, test_ui_state.py:710, *events/ui goldens* |

UI compositions that join already-translated parts with a language-neutral separator (no core text; listed
because they would keep working with keys): `_paint_row` 1047 `f"{index+1:02d}  ·  {detail}"`, `render_wait`
1956 `f'{reason} · {time}'`, `clear_unfinished` 1822 `f'{cleared} · {reason}'`, `_active_title` 2014
`text += f" · {title}"`, `on_item_done` 2157 `f'{name} · {bytes}'`, `_update_footer` 1096
`i18n.sep('clause_sep').join(parts)`.

---------------------------------------------------------------------------------------------------

## 5. `localize()` / `label()` / prefix machinery in my UI files

### 5.1 `localize()` call sites (15; all `keys=None`)

| file:line | what it localizes | where the text comes from | disappears if the core passed keys? |
|---|---|---|---|
| studio.py:244 | `engine_version()` inside `log.ready` | updater `'已内置'` (F43) | yes, if `engine_version()` returns a sentinel / key for "built-in" (updater mapper) |
| studio.py:357 | `engine_version()` in the sidebar badge | same | yes, same condition |
| ui_download.py:574 | `str(error)` of `decode_link_file` | UI-made `t()` texts or Windows OSError (F40) | yes; already redundant today |
| ui_download.py:1347 | `model['error']` in `row_reason` | live `item_done.error` (F29) **and stored `entries[].error`** written by 1.6.0 / 1.7.0 (and by 2.0 for raw engine text) | live: yes; **stays as fallback for stored entries** that carry no key (or raw engine text, where it is a no-op) |
| ui_download.py:1370 | `str(error)` in `log.retry_failed` | `Options.from_dict` English texts (F38) | yes (and the English-in-zh defect H-3 goes with it) |
| ui_download.py:1421 | `str(error)` of `unfinished.save` in `remove_row` | unfinished OSError texts / Windows (F39) | yes |
| ui_download.py:1519 | `str(error)` of `read_form` in `start` | urls / build_command / UI `t()` / OSError (F34, F35) | yes |
| ui_download.py:1570 | `str(error)` in `launch` | build_command / `LinkTooLong` / mkdir OSError (F35) | yes |
| ui_download.py:1586 | `str(error)` in `log.history_write_failed` | library texts (F42) | yes, if library raises keyed errors |
| ui_download.py:1632 | `str(error)` in `dialog.start_failed` | any exception (F41) | yes for core texts; OS / Python texts stay raw |
| ui_download.py:1686 | `str(error)` in the hint (`enqueue_links`) | same as 1519 | yes |
| ui_download.py:1807 | `str(error)` of `unfinished.save([])` in `clear_unfinished` | F39 | yes |
| ui_download.py:1953 | `wait_context['reason']` | `'waiting'.reason` (F25, F26) | yes (payload carries the key) |
| ui_download.py:2168 | `item_done.reason` | `explain_error` results (F28) | yes for live events (this handler sees only live events) |
| ui_log.py:151 | every log line | all of C1 (F01-F24, F42, F47, F49, F51, F52) | yes for core lines; raw engine output passes unchanged anyway; UI lines are already `t()` text |

Indirect: my stored texts are localized outside my files by `library_ui.history_note_text` 869 (F28, F29:
`last_reason.reason`, `errors[-1]` via `explain_error`), `library_ui.history_title` 330-335 (F45, keys
`TITLED_JOB_KEYS` / `UNTITLED_JOB_KEYS`). Both must keep a fallback for rows written by 1.5.1 / 1.6.0 / 1.7.0.

### 5.2 `label()` / `choices()` / `all_values()` / key maps (would stay: they map stored values, not messages)

| file:line | call | note |
|---|---|---|
| ui_download.py:286 | `label('format', value)` for the format menu | stored zh `Preset.label` -> `format.*` |
| ui_download.py:291 | `label('resolution', value)` | `最佳` -> `value.resolution.best`; heights unchanged |
| ui_download.py:867, 869 | `label('format', formats.by_key(entry.format).label)`, fallback `label('mode', options.mode)` | unfinished row detail (F30) |
| ui_download.py:1042 | `label('row_state', state)` | chip text |
| ui_download.py:1602 | `label('format', formats.by_mode(options.mode).label)` | `log.task_started` |
| ui_download.py:189 | `all_values(key)[0]` in `stored_text` | writes the canonical zh (F45) |
| ui_log.py:46 | `all_values('download.title.finished_unfinished')` | classify special rule |
| studio.py:456 | `choices('library_tab')` | tab ids for `focus_page` |
| app.py:75 | `all_values('app.title')` | `focus_window` (F50) |
| ui_download.py:105-106 `REASON_KEYS`, 107-110 `FOREIGN_KEYS` | code -> key maps | already the key pattern (stored code `reason` -> key) |

### 5.3 Prefix lists (classification of shown text)

| file:line | list | today | with keys |
|---|---|---|---|
| ui_log.py:26-27 `_ERROR_STARTS` | `此链接未完成, 此链接无法开始, 更新失败, 网页采集未完成, 订阅检查失败, 界面错误, 设置未保存, 错误：, 无法启动下载引擎` (zh 1.6.0 starts) | `classify` on the shown (localized) line | replaceable by the tag of the line's key (`i18n_log.TAGS`) carried with the line; raw engine lines never needed these lists (they are classified by the `ERROR` / `WARNING` / `[extractor]` rules) |
| ui_log.py:28 `_OK_STARTS` | `文件已保存, 下载完成` | same | same |
| ui_log.py:42-55 `_starts()` | `+ i18n.prefixes('error'/'ok')` (literal starts of every TAGS key in every language) and the `finished_unfinished` special rule (`任务结束 · ` shared with `finished_cleared`) | same | the special rule exists only because two keys share a start; a key-tagged line needs no prefix guessing |
| ui_log.py:67 | `line.startswith('任务结束') and '未完成' in line` | 1.6.0 rule kept for zh | disappears with tags |

---------------------------------------------------------------------------------------------------

## 6. Places that compare or parse core / shown text (they would become key comparisons)

| file:line | comparison | kind |
|---|---|---|
| ui_download.py:170-172 `history_value` | `value.get('reason') == engine.NETWORK_REASON` -> stores `PROXY_REASON` | core text compared, zh text stored |
| ui_download.py:2171, 2176 `on_item_done` | `canonical == engine.NETWORK_REASON`, `canonical == engine.STALE_REASON` | core text compared |
| ui_download.py:1348 `row_reason` | `error not in reason` | shown text containment |
| ui_download.py:1824-1825 `clear_unfinished` | `full_text(self.metrics) in (t('download.metrics.finished_unfinished'), t('download.metrics.bot'), t('download.metrics.bot_cookies'))` | shown text compared (UI) |
| ui_download.py:1839-1853 `_finished_success` | parses the shown title with the `finished_unfinished` template (fallback after `job_summary`) | text surgery (I18N.md 8.7 kept as fallback) |
| studio.py:491-494 `refresh_shell` | `start_button.cget('text').strip() != t('download.start'/'.queue')` | shown text compared (UI) |
| ui_download.py:1982 `on_log` | `str(value).startswith(CONVERTING)` | engine tokens (language-neutral) |
| ui_log.py:58-73 `classify` | prefix lists (5.3) | shown text classified |
| library_ui (1.6.0 :29, 62-74; 1.7.0 PLACEHOLDER_TITLE kept) | regex on stored `jobs.title` | stored text parsed by old versions |
| engine.py:89-91 `_TRANSPORT_FAILURE` | built from the pattern whose reason `== NETWORK_REASON` | core constant identity (not a display issue) |

---------------------------------------------------------------------------------------------------

## 7. Every CJK literal in the 13 files

158 literal nodes (an f-string counts once; unfinished.py:175 is one f-string with a nested format spec and
appears twice in the ast). "->" = the flow above.

**engine.py (63)**: 30 `YT-DLP下载器` `APP_NAME` (1.5.1 name for importers; not shown anywhere) · 45
`最佳` RESOLUTIONS (stored value, F55) · 57-58 BOT_CHECK_HINT (F19) · 59 BOT_CHECK_STALE_HINT (F19) · 68,
69, 73, 74, 75, 77, 78, 79, 80, 81, 82 ERROR_REASONS (F28) · 84 NETWORK_REASON (F28, compared) · 92, 93, 94
BOT_CHECK/STALE/ENGINE_ERROR_REASON (F28) · 111 exit-code reason (F28) · 123, 127 parse_urls (F37) · 134, 142
read_url_file (F37) · 243, 245, 250, 254-255 check_cookie_file (F16, F35, F36) · 266, 267 COOKIE_LINE_*
(F02, F29) · 464 proxy scheme (F16, F35) · 472 bad referer (F16, F35) · 491 LinkTooLong (F16, F35) · 583
`[代理]` mask token (F29; stored inside errors) · 698 archive unwritable (F07, F16) · 999 other format (F05) ·
1151 waiting log (F01) · 1200 present skipped (F03) · 1215, 1254 kept existing (F04) · 1354 waiting reason
(F26, H3) · 1373 archive info failed (F07, H3) · 1653 cannot start engine (F08) · 1662 429 out (F09) · 1668,
1672 rate-limit reasons (F01, F25) · 1710-1711 present untagged (F10) · 1738 archive info failed (F07) ·
1746 kept existing (F04) · 1806 duplicate merged (F11) · 1834 record unreadable (F12) · 1850 pace reason
(F01, F25) · 1875 no collect history (F13) · 1877 `媒体` folder name (F54) · 1889 resume partial (F14) ·
1901-1902 picture exists (F15) · 1916, 1974 cannot start link (F16) · 1963 subtitles dropped (F17) · 2025
incompatible (F18) · 2041 item unfinished (F20) · 2052 error (F21) · 2089 failed list not saved (F22, H3).
No CJK comment or docstring.

**unfinished.py (14 nodes, 13 literals)**: 173, 179 `无法读取`; 175 `超过 5 MB（… MB）`; 181 `内容已损坏`; 185,
189, 201 `格式无法识别`; 192 `有 … 条记录无法识别`; 198 `链接或设置无法识别` (all F23 `problem`) · 246
cannot set aside (F22, F39) · 251-252 kept aside (F23) · 349 no overflow name (F22, F39) · 356 overflow (F23).

**urls.py (2)**: 143, 148 (F34). Module docstring line with CJK (`…/AAA太好听了`, an example; not a literal
that reaches anything).

**naming.py (7)**: 16 LABELS ×3 (F55, H4) · 88, 92, 96, 98 rename notices (F06).

**formats.py (14)**: 26, 28, 31, 34, 36, 38, 40 `mode` ×7 and `label` ×6 (`MP3 · 320 kbps` has no CJK) (F55,
H4) · 53 `BEST` (F55). Docstring CJK (`最佳` in `format_args`): not a literal.

**paths.py (1)**: 102 `下载文件` (F54). Docstring CJK only.

**procutil.py (2)**: 388 default `title_suffix`, 397 fallback tuple (F50).

**app.py (3)**: 39, 50 self-test (F53) · 47 `资料归档` (F54). Comments 66, 85 (`立即重启`, `外观`): no effect.

**studio.py (3)**: 38 `TITLE` (F50, H4) · 51 `SETTINGS_HELD_NOTICE` (F51, H4) · 313 `界面错误` (F48,
log file only). Comments 260, 356 and 2 docstrings: no effect.

**ui_download.py (36)**: 41 `TITLE` (F50) · 60-68 `STATE_LOOK` chips ×9 (H4; display via
`label('row_state')`) · 75 `CLEARED_CHIP`, 76 `CLEARED_DETAIL`, 77 `FINISHED_UNFINISHED`, 78 `CLEARED_METRICS`,
81 `FINISHED_PREFIX`, 85 `REASON_WORDS` ×4, 86 `HINT_DEFAULT`, 87 `PLACEHOLDER`, 88 `METRICS_IDLE`, 90
`BOT_TEXT`, 91 `BLOCKED_DETAIL`, 94-97 `FOREIGN_TEXT` ×8, 98 `BOT_TEXT_COOKIES`, 102 `SKIPPED_TEXT`: H4
constants kept with their zh values for tests (parity table); the page shows `t(<key>)` · 100 `PROXY_REASON`
(H4 **and stored**, F46) · 1738 log-file-only English message with `加入队列` (F48). 20 comment lines (71-73,
79-83, 89, 363, 406, 758, 884, 1084-1085, 1180, 1505, 1814-1815, 1818, 2251) and 9 docstrings: no effect.

**ui_log.py (13)**: 26-27 `_ERROR_STARTS` ×9, 28 `_OK_STARTS` ×2, 67 `任务结束` / `未完成`: zh-only prefixes
for `classify` (internal; section 5.3). Comment 30.

**progress.py, applog.py**: none.

---------------------------------------------------------------------------------------------------

## 8. Hard cases (what keeps the per-text patching alive)

Probe: `probe.py` with `YTDLP_DESKTOP_LANG=en` on a copy of L2 `src`. "CJK left" = Chinese remains on an
English screen.

**H-1 Cookies echo error has no template (1.7.0 defect).** engine.py:1292-1293 replaces an echoing `ERROR:`
line by `COOKIE_LINE_FAILED` and then stores `line[6:].strip()` as `last_error` -> `item.error` ->
`failed-downloads.json entries[].error`, `jobs.data.errors`. The stored text
`cookies 文件中有一行格式不对，无法读取（内容不记录）` lacks the `ERROR: ` of `core.cookies.line_failed`, so no
template matches; `explain_error` returns it unchanged as the reason (≤ 70 chars). Probe: `localize` ->
CJK left, both as error and as reason. Shown: row detail (2168), reason dialog (1347), history note
(library_ui 869). Trigger: the engine prints an `ERROR:` line that echoes a cookies-file line (a broken
cookies file in use; the H6 scenario of test_review_engine.py:216-249 and 296-312 exercises these echoes,
but no test asserts what lands in `entries[].error` for them).

**H-2 `explain_error` cuts core texts at 70 characters (1.7.0 defect).** engine.py:112 `[:70]`. When an item
cannot start (1916-1919) its `item.error` is a Chinese core text and its reason is that text cut to 70.
A template whose fixed text lies after the cut no longer matches:
`core.cookies.not_netscape` is 75 characters - **always** CJK left (probe); `core.cookies.missing` is
`找不到 cookies 文件：{file}\n请在“偏好设置 → 网络”中重新选择。` - CJK left as soon as the path is longer than
35 characters (probe with a long path; a short path is fine); also `item.error` itself is cut at 200
(1918), so a path over about 165 characters breaks the reason dialog too. Templates that end in a placeholder
(`core.links.too_long`, `core.archive.unwritable`, `core.archive.cannot_set_aside`) survive the cut. Trigger:
the cookies file is moved, deleted or replaced while a queue runs (launch checks only the first link per
option set, 1550-1566), a retry from the unfinished list, or tests that call the Runner directly
(test_review_engine.py:251-257). The same cut reason is stored in `jobs.data.last_reason.reason` and shown
in every later session. With keys the reason would be `{key: 'core.cookies.missing', file: …}` and no cut
would ever touch a key.

**H-3 English-only core texts show English in the Chinese interface.** `Options.from_dict`
(engine.py:178-190), `formats.by_mode/by_id/by_label` (75, 82, 89), `build_command` 425
`'Unsupported output option'`. Reach the screen in `log.retry_failed` (ui_download 1370: `无法重试这条记录：{url}（Options data has no usable output folder）`)
and, through `此链接无法开始：{error}` and `item.error`/reason, in a row, the reason dialog and the history.
Not in the catalog at all (no key). A key design makes them first-class messages.

**H-4 `localize(keys=None)` on raw engine output (append_log, on_item_done reason fallback).** A raw line
equal to a core text is translated (probe: `不支持这个链接` -> English; harmless). A raw line that contains
the joiner `；` is split and joined again with `. ` when one part equals a core text: probe
`ERROR: [generic] x: 链接不存在（404）；网站拒绝访问（403）` -> `ERROR: [generic] x: 链接不存在（404）. The site refused
access (403)` - the raw text is altered. Templates that embed user data can also mis-split: probe
`present_skipped` with a title containing `，之前下载的` leaves part Chinese. Keys remove the guesswork
because only app-made lines carry a key.

**H-5 OS texts and nested exceptions.** `[WinError 5] 拒绝访问。` (Chinese Windows) inside
`失败清单未能保存：{error}`, `无法启动下载引擎：{error}` (`_sentence` strips its `。`), rename `strerror`
(`core.rename.failed_named`): the OS text is not app text and stays in the Windows language (probe: CJK
left on a Chinese Windows in English mode). Not a defect; the design should keep "OS text = parameter, shown
raw" explicit.

**H-6 Stored data of three versions must stay readable.** Whatever 2.0 / 1.8.0 emits, these are already on
disk in canonical Chinese and must still be shown: `entries[].error` (1.6.0, 1.7.0), `jobs.data.errors`,
`jobs.data.last_reason.reason` (1.6.0, 1.7.0), `jobs.title` placeholders (`{n}条 · {mode}` 1.5.1,
`{n} 条 · {mode}` 1.6.0 / 1.7.0), `PROXY_REASON`. So `localize` (or an equivalent lookup) stays as a
**read-side fallback for stored data**, even when no live flow needs it. And while rollback is supported,
old versions keep writing Chinese into the same fields after a rollback (1.6.0 rewrites a re-failed entry
without any new field; 1.5.1 drops `entries` and writes its own job titles).

**H-7 Old readers parse or show the stored text.** 1.6.0 parses `jobs.title` with
`PLACEHOLDER_TITLE = r'\d+\s*条 · .+'`; 1.5.1 shows `jobs.title` and `errors[-1][:130]` raw; 1.6.0 shows
`entries[].error`, `last_reason.reason` raw; 1.5.1 compares `settings.json mode` / `resolution` and
`failed-downloads.json options.mode` with its zh `MODES` / `RESOLUTIONS`. A key **instead of** the zh text
in any of these fields shows a raw key in 1.6.0 / 1.5.1 (or resets the format to `MODES[0]`). Additive
places that every reader ignores and keeps (section 3): `jobs.data.<new key>` (all versions),
`entries[].<new key>` (1.6.0 / 1.7.0 keep it for untouched entries; 1.5.1 drops all entries anyway),
new event payload keys in dict payloads (H5).

**H-8 The `'log'` payload is a bare `str` (H5).** Additive fields are impossible on it. Options a design
has: a new event kind (it would appear in every recorded transcript: `events-1.6.0.json` is compared byte for
byte by test_golden_events, the 1.5.1 projection drops unknown kinds), or a `str` subclass whose value is
the canonical zh text and which carries `key` / `params`. Checked in the probe environment:
`json.dumps({'reason': Msg('下载间隔', 'core.wait.pace', {})})` gives `{"reason": "下载间隔"}`, `==` and
`hash` equal the plain text (goldens and comparisons unchanged), `copy.deepcopy` needs `__getnewargs__`
(else TypeError), and **every string operation drops the key** (`[:200]`, `[:70]`, `.strip()`, `+`,
`f'{m}'`, `str(m)` all return plain `str`). The places where my texts go through such operations are
exactly the defect sites: `_masked(...)[:200]` (1918, 1975, 2005, 2050), `explain_error` `[:70]` and
`.strip()` (102, 112), `line[6:].strip()[:200]` (1293), `_sentence(error)` (1005), `str(error)` everywhere
in section 5.1, `'；'.join` in other pages.

**H-9 Protected and golden-pinned producers.** `下载间隔 / 重试等待` (1354) and `归档信息保存失败（媒体已保留）：`
(1373) are inside the H3 `_run_once` loop and in `runonce-1.5.1.json` (test_golden_runonce, byte-identical);
`失败清单未能保存：` (2089) and the unfinished notices passed in (2082) are in the H3 `finally` merge; the 429
texts (1662, 1668, 1672) are the verbatim-moved 429 loop. A key design must keep these call sites' literal
text (a `str` subclass with the same value, or a mapping applied outside the block) or relax H3 with a
stated reason; the `set_busy` / `poll` hashes are pinned in test_i18n_download.py:37 (no text there).

**H-10 Log-file-only Chinese.** `unfinished._report` writes `_log.warning` in Chinese whatever the language
(F23 a), and test_review_state.py:163 asserts `超过 5 MB` in that WARNING. download.log therefore holds the
same notice twice (zh WARNING + localized INFO via the log page). With keys the WARNING could be rendered
too, but the test pins the zh text.

**H-11 Thread-local callback gap.** `unfinished.save` from the window thread (`remove_row` 1418,
`clear_unfinished` 1804) has no `log` callback (`_local.log` is per thread), so a set-aside or overflow
there reaches download.log only (zh) and never the log page. Not a language issue, but a key design that
routes notices through events should cover this path too.

---------------------------------------------------------------------------------------------------

## 9. Notes for the design (observations from the map, not decisions)

- Already key-shaped on this side: `entries[].reason` (code) -> `REASON_KEYS`; `entries[].format` (preset id)
  -> `label('format')`; `item_done.skipped` (`downloaded` / `bot_check`); `done.blocked`; `waiting.kind`;
  row states -> `label('row_state')`. The UI renders them with `t()` and no `localize`.
- The texts with real variable parts: `core.engine.waiting` (reason is itself a message ->
  nested message), `core.engine.cannot_start_link` / `core.engine.error` / `unfinished_not_saved` /
  `archive_info_failed` / `record_unreadable` / `cannot_start_engine` (error is a message **or** an OS /
  engine text), `core.unfinished.kept_aside` (problem is a message), `core.rename.*` (reason is an OS text),
  `core.reason.exit_code` (number), `core.links.too_long` (number + url), `core.cookies.missing` (path). A key
  form needs nested messages and a clear "raw text parameter" type.
- `explain_error` is the single producer of row / history reasons (also called by library_ui on stored
  errors): returning `(key, params)` there plus a separate "raw fallback text" would remove H-2 and the
  `NETWORK_REASON` / `STALE_REASON` text comparisons (section 6).
- For storage compatibility the UI already has the right helper: `stored_text(key, **values)` writes the
  canonical zh text of a key. Writing canonical zh into the old fields and the key into an additive field
  (`jobs.data`, `entries[]`) keeps every old reader as it is; the new reader prefers the key and falls back
  to `localize` for rows without it (H-6, H-7).
- Tests that would have to change if the core emitted anything other than the zh text in its current
  places: test_golden_runonce (byte-identical), test_golden_events (events-1.5.1/1.6.0), test_golden_ui
  (ui-1.5.1/1.6.0), test_queue, test_engine, test_review_engine, test_review_state, test_unfinished,
  test_v170_core, test_i18n_download, test_i18n_catalog (`test_6_every_cjk_literal_of_a_core_file_is_known`,
  `test_7_*` round trips), test_urls, test_formats_naming. Compatibility tests that execute the **1.5.1**
  readers on new files exist (test_unfinished.py:32, test_review_state.py:60, test_library_store.py:24,
  test_core_engine.py:52); there is **no** test that executes the 1.6.0 or 1.7.0 readers on newer data (they
  would need snapshots of those sources, e.g. `git show HEAD:src/...` for 1.6.0).
