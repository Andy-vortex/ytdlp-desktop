# YT-DLP下载器 1.6.0 - upgrade specification (main document)

Read this file, your own `WPn.md`, earlier handoff notes (`docs/1.6.0/handoff/WPn.md`) and the audit sections you are pointed to. Paths are relative to `D:\YT-DLP下载器\源码`; "§" = this file; line numbers = 1.5.1 sources (`verification/backup-before-1.6.0-20261115`). Long tables in the WP files are normative.

## 1. Goals, non-goals, decisions
Goals: ~1 s start; no artificial wait for one song; clean names, tags, cover; no duplicate queueing; a truthful 未完成 list; no wedged window; one design system readable at 200 %; sandboxed tests. Non-goals: new dependencies, parallel downloads, moving `data\library.sqlite3`, page classes, lazy pages, inline rename, `git init`.
H1-H8 = the task's eight hard rules (H6 = privacy).

| id | decision (skeptic variant) |
|---|---|
| arch-1, arch-5 | IN WP6: onedir, `Tree(vendor)`, exe path unchanged |
| arch-2, func-3, ux-1 | IN WP2: spacing = scheduler wait between items (§3.7); no threshold |
| arch-3, robust-2 | IN: engine side WP1, UI side WP4 |
| arch-4, ux-8, func-2 | IN: box cleared at launch (undo); `Runner.enqueue` |
| arch-6, arch-7, robust-14, visual-9 | IN: WP0 + tests in every WP; process rules §7 |
| arch-8 | reduced: `Options.to_dict/from_dict`, `settings.py`; persisted format stays the MODES string |
| arch-9, arch-10 | IN WP1: `paths.py`, `procutil.py`, `updater.py`; dispatch table WP4; no activity object |
| arch-11, ux-12, visual-11 | IN: `applog.py`, buffered tagged log page |
| arch-12 | reduced: `ui_theme.py` + page mixins in one namespace |
| arch-13, arch-14 | arch-13 DEFERRED; arch-14 reduced: chunked rows, row cap <= 300 (WP4) |
| robust-1, ux-7 | IN: `entries` key in `failed-downloads.json`, identity (link key, format) |
| robust-3, func-8, ux-5 | IN: first bot check blocks YouTube for the job; UI countdown retry; on-demand cookies OUT |
| robust-4, func-4, ux-4 | IN: `urls.py`; `identity()` unchanged; hint line, no dialogs |
| robust-5, robust-6 | IN WP1: per-run TEMP, purge, worker env; robust-6 conservative via `paths.catalog_path()` |
| robust-7..13 | IN |
| func-1, ux-2 | IN: default `title` (no `[id]`), `clean` opt-in, rename in Python, never overwrite; playlist and archive runs keep `[id]` |
| func-5 | IN: tags + cover (none for WAV; artist/track rule audio only) |
| func-6, ux-6 | IN: two-line rows, selection + context menu |
| func-7, ux-11 | reduced: `downloads` table + skip hint; no `--download-archive`, no recent list |
| func-9, ux-9, func-10, func-11 | IN: taskbar flash, title, live stats (sound OUT); <= 1 release check / 24 h + stale-engine hint; `-P temp:` on the same drive |
| func-12, func-13 | IN: `formats.py`, + M4A, + MP4; subtitles off by default, video only |
| ux-3, ux-10, ux-13 | IN; ux-3 safe form: forgiving paste + clipboard chip, never prefill or auto-start |
| visual-1..8, 10 | IN (functions in `ui_theme.py`; subscription tables stay stacked) |
| visual-12 | STRETCH WP7; tokens are (light, dark) pairs from the start |
| missed | IN: early DPI awareness, wheel speed, `BEGIN IMMEDIATE`, history LIMIT, engine preflight, proxy masked in log, `build.py`. OUT: copying cookies.txt (H6), skip-current-item, saved proxy |

## 2. Module map (flat under `src/`, `pathex=[src]`)
- `paths.py`: locations, env overrides, purge (stdlib only). `procutil.py`: `HIDDEN`, `process_environment`, job object, `terminate_tree`, `alive`, single-instance mutex, reveal-in-folder, taskbar flash. `applog.py`: logging, excepthooks. `settings.py`: typed load / atomic save. These three import paths only.
- `urls.py`, `formats.py`, `naming.py`: pure helpers (§3.5, §3.3, §3.6), stdlib only.
- `unfinished.py` (failed-downloads.json as plain dicts; imports paths, urls, formats) and `updater.py` (engine path/version cache, release check, install, restore; imports paths, procutil) never import engine.
- `engine.py`: `Options`, `build_command`, `parse_line`, `Runner`, `diagnostics`; re-exports every 1.5.1 name; no top-level import of library. `library.py`, `collector.py` as today.
- `ui_theme.py`: tokens, theme patch, factories, ttk styles, DPI call; imports customtkinter and PIL only. `progress.py` takes its colours from it (WP4).
- `studio.py`: `StudioApp` shell (window, sidebar, shared state, `poll`, `handle`, `set_busy`, close); keeps `import os, time`, `from tkinter import filedialog, messagebox` and re-exports `FORMATS`, `bytes_text`, `time_text` from `ui_download` (mixin modules never import `studio` at module level).
- `ui_download.py`, `ui_settings.py`, `ui_log.py`, `collection_ui.py`, `library_ui.py`: mixins `DownloadUI`, `SettingsUI`, `LogUI`, `CollectionUI`, `LibraryUI`. `class StudioApp(DownloadUI, SettingsUI, LogUI, LibraryUI, CollectionUI, ctk.CTk)`.
- `app.py`: argv, single instance, logging, mainloop; no module-level import of `ui_theme`, `studio` or customtkinter.

## 3. Contracts

### 3.1 paths
Patchable module attributes: `RESOURCE_ROOT`, `VENDOR`, `BIN`, `APP_DIR` (env `YTDLP_DESKTOP_APP_DIR`, else exe folder / repo root), `STATE_DIR` (env `YTDLP_DESKTOP_STATE`, else `%LOCALAPPDATA%\YtDlpDesktop`). Call-time functions: `settings_file()`, `failed_file()`, `log_file()`, `engines_dir()`, `tmp_dir()`, `work_dir()`, `collect_dir()`, `cache_dir()` (all under STATE), `default_output()` (`APP_DIR/下载文件`), `catalog_path()` (= `APP_DIR/data/library.sqlite3` if it exists and is writable, else `STATE/library.sqlite3`), `purge_stale()`. Code reads `paths.X` at call time, never `from paths import X` (engine's re-exports excepted). Tests: the runner sets every child's `LOCALAPPDATA`, `YTDLP_DESKTOP_APP_DIR`, `TEMP`, `TMP`, `VERIFY_OUT`, `YTDLP_DESKTOP_INSTANCE` (mutex name); `verification/_sandbox.py` verifies them, or sets them when a file runs alone, before any `src` import (WP0.md); `_sandbox.state_patch(path)` patches `STATE_DIR` on `paths` and `engine`.

### 3.2 settings.json
`settings.load() -> dict` (typed, every key present, unknown keys kept in `'_extra'`); `settings.save(values) -> bool`. Read as utf-8-sig; a non-object or undecodable file is renamed `settings.bad.json`; an invalid value -> that key's default (`load()` checks the enums it owns, `naming` and `appearance`; `StudioApp` keeps the membership fallback for `mode`/`resolution`). Save: temp file, flush + `os.fsync`, `os.replace`. Saved 400 ms after a persisted variable changes and on close; never in smoke mode.
Existing keys (unchanged): `output` (quotes stripped), `mode`, `resolution`, `batch_safe`, `archive_enabled`, `archive_root`, `cookies` (path only).
New keys: `naming` `title|clean|title_id` (`title`); `embed_metadata`, `embed_cover` True; `subtitles` False; `skip_downloaded`, `notify_done`, `clipboard_hint`, `check_engine_updates` True; `last_engine_check`, `bot_check_at` float 0; `latest_engine`, `last_clip` (sha1 of the last offered clipboard text), `geometry` str; `zoomed` bool; `appearance` `light|dark|system` (`light`). 1.5.1 ignores new keys and drops them when it saves.

### 3.3 Options and formats
`Options` (frozen; old positional order kept): `output: Path`, `mode=MODES[0]`, `resolution=RESOLUTIONS[0]`, `playlist=False`, `proxy=''`, `batch_safe=True`, `referers={}`, `archive_root=''`, `archive_contexts={}`, `cookies=''`, new: `naming='title'`, `embed_metadata=True`, `embed_cover=True`, `subtitles=False`, `skip_downloaded=False`.
`to_dict(private=False)`: keys of `asdict`, `output` as str, `proxy`/`cookies` blank unless `private`. `Options.from_dict(data, **overrides)`: unknown keys ignored, missing defaulted, bools coerced; `ValueError` for a bad `output`, `mode` or `resolution`; unknown `naming` -> `title`. The only serialisers.
`formats.PRESETS`: frozen `Preset(id, mode, label, kind, args, cover)`; `MODES = tuple(p.mode ...)`; `by_mode`, `by_id`, `by_label`, `format_args(preset, resolution)`. Table order = `MODES` order (frozen); menu order and labels: WP2.md. `{L}` = `[height<=N]` or empty.

| id | `mode` (persisted) | args |
|---|---|---|
| audio_best | 音频 · 原始最佳 | `-f ba/b -x --audio-format best` |
| audio_wav | 音频 · WAV 32 位 | `-f ba/b -x --audio-format wav --postprocessor-args "ExtractAudio+ffmpeg_o:-c:a pcm_f32le"`; cover=False |
| audio_flac | 音频 · FLAC | `-f ba/b -x --audio-format flac --postprocessor-args "ExtractAudio+ffmpeg_o:-sample_fmt s32 -bits_per_raw_sample 24"` |
| audio_mp3 | 音频 · MP3 320K | `-f ba/b -x --audio-format mp3 --audio-quality 320K` |
| video_mkv | 视频 · 最佳画质 MKV | `-f bv*{L}+ba/b{L} --merge-output-format mkv --remux-video mkv` |
| audio_m4a (new) | 音频 · M4A 原生 | `-f ba[ext=m4a]/ba[acodec^=mp4a]/ba/b -x --audio-format m4a` |
| video_mp4 (new) | 视频 · MP4 兼容 | `-f bv*{L}+ba/b{L} -S vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac --merge-output-format mp4 --remux-video mp4` |

### 3.4 `build_command(url, options)` argv order
1. unchanged head through `-P <output>` (engine.py:171-177);
2. `-P temp:<paths.work_dir()>` only when the file will be renamed (§3.6) and output and work dir share a drive;
3. `-o "%(title).140B [%(id)s].%(ext)s"`, `--progress-template ...`, `--print before_dl:__GUI_TITLE__%(title)j`;
4. `--print "after_move:__GUI_MEDIA__%(.{id,extractor_key,title,uploader,duration,webpage_url,filepath})j"`, which must precede
5. `--print after_move:__GUI_FILE__%(filepath)j`, then the `__GUI_ARCHIVE__` print when archiving;
6. `--extractor-retries 5` and the four `--retry-sleep`;
7. if `batch_safe`: `--sleep-interval 10 --max-sleep-interval 20` only when `playlist`; then `--sleep-requests 1`;
8. playlist flags, proxy, cookies, referer;
9. `format_args(preset, resolution)`;
10. if `embed_metadata`: `--embed-metadata --parse-metadata ":(?P<meta_synopsis>)" --parse-metadata ":(?P<meta_description>)"`, and for audio presets `--parse-metadata "%(artist|)s<>%(title)s:^<>(?P<artist>.+?)\s+-\s+(?P<track>.+)$"`;
11. if `embed_cover` and `preset.cover`: `--embed-thumbnail --convert-thumbnails jpg`;
12. if `subtitles` and video preset: `--embed-subs --sub-langs "zh.*,en.*,-live_chat"`, plus `--sleep-subtitles 1` when `batch_safe`;
13. `-- <url>`.
All flags exist in the bundled 2026.10.21 help and were run offline against the fixture.

### 3.5 URLs (`urls.py`)
`extract_urls(text)` (lenient; rules: WP2.md §B); `parse_download_text(text)` = the same per line with `ValueError('第 N 行没有找到链接。')` / `ValueError('请先粘贴至少一个视频或音频链接。')`. `engine.parse_urls` stays strict and unchanged.
YouTube family: `youtube.com`, `www.`, `m.`, `music.youtube.com`, `youtu.be`, `www.youtube-nocookie.com`; video id from `/watch?v=`, `youtu.be/<id>`, `/shorts/`, `/live/`, `/embed/`.
`clean_url(url, playlist=False)`: YouTube family only, host/path/param order kept; drop `si, pp, feature, start_radio, index, ab_channel`; on URLs with a video id drop `list` when `playlist` is off or the list id starts with `RD`. Other URLs byte-identical.
`link_key(url, playlist=False)`: `youtube:<id>` for a video URL whose `list` is absent, dropped or `RD...`; `youtube:list:<listid>` for `/playlist?list=` and for video + real list with `playlist` on; Bilibili/Douyin as `identity()`; else lowercased scheme+host + path + query. `classify(url)` -> `video|mix|video_in_list|playlist|search|channel|other`. `normalize(urls, playlist)` -> `.urls` (cleaned, de-duplicated by key), `.origin` (cleaned -> original), counters. `display(url)`. `identity(url)` = library's function moved verbatim (persisted keys never change).
Examples: WP2.md §B.

### 3.6 Naming (`naming.py`)
yt-dlp always writes the work name `%(title).140B [%(id)s].%(ext)s`; after the move the Runner renames with `os.rename` (never `os.replace`):
- `title` (default, 标题（推荐）): work name minus the exact suffix ` [<id>]`;
- `clean` (精简标题): as `title`, then for audio presets `^(.+? - [^【「『#\[]+?)\s*(?:【|「|『|#|\[).*$` -> `\1`; a result under 2 chars falls back to `title`;
- `title_id` (标题 [视频ID]): work name (1.5.1 behaviour).
Target exists -> ` (2)` ... ` (99)`; `PermissionError` -> 3 tries, then keep the work name and log. No rename and no `-P temp:` when `playlist` is on or `archive_root` is set (`naming.renames`): those runs keep the 1.5.1 names, so a re-run skips or overwrites finished entries instead of adding ` (2)` copies. Nor when the stem does not end with ` [<id>]`.

### 3.7 Runner
`Runner(emit, library=None)` (`runner.library = x` still works). Public: `download(urls, options, overrides=None)` (`overrides: {url: Options}`), `enqueue(urls, options=None, overrides=None) -> list[str]`, `cancel()`, `update()`, `Runner.reset_pacing()`. Kept for tests: `_run_once(command, index, total, files) -> (code, limited, retry_after)`, `_wait(seconds, index, total, reason) -> bool`, static `_terminate_process`, attributes `bot_check, cancelled, process, lock` (`lock` guards `process` only; queue state has its own `queue_lock`).
Threading: `download` runs on one worker thread, the only emitter. `enqueue`/`cancel` may be called from any thread. `enqueue` returns `[]` unless the run is open (items built, not closed, not cancelled; the caller then starts a new job). It drops URLs whose (link key, format id) is pending, active or done in this run (failed, stopped, blocked or skipped ones may be queued again), appends the rest to an inbox and returns them in order; `download` drops duplicate initial URLs likewise. Indices follow acceptance order, so the UI appends rows at once. The worker drains the inbox before each item; its `finally` first closes the queue and drains once more, so every accepted link gets a `queued` event and, if it never ran, is stored as `unstarted`.
Per item: (1) host blocked by a bot check -> `item_done(skipped='bot_check')`; (2) `skip_downloaded`, not `multi`, library present, no archive, `find_download([key], format)` with an existing file -> `item_done(skipped='downloaded')`; (3) `item`; (4) spacing: if `batch_safe`, not `playlist` and `Runner.last_media_end` is set, `_wait(uniform(*engine.PACE_SECONDS) - elapsed, ..., '下载间隔')`; (5) destination, archive (one `Archive` per `archive_root`, from the item's own options) and `build_command` in a per-item `try` (error -> failed item, queue continues); (6) the 429 loop (verbatim); (7) one rerun without subtitles after `Unable to download video subtitles`; (8) `item_done`. `PACE_SECONDS = (10, 20)`; `last_media_end` is set after each exit 0. Exit 0 without a new file is success. Fatal for the queue: `OSError` from `Popen`; an engine line with `no such option` (`engine_error`).
`_run_once` changes only at the points listed in WP1.md and WP2.md (child env, job object, two hook lines, `finally`); its loop is otherwise byte-identical.

### 3.8 Events ("+" = new key or kind)
| kind | payload |
|---|---|
| `log` | str |
| `item` | `index, total, url, key+, format+, origin+, multi+` (the link may stand for several media); a process is about to start |
| `title` | str |
| `waiting` | `index, total, seconds, reason, kind+` (`interval`/`rate_limit`; absent = engine sleep) |
| `resuming` | `index, total` |
| `progress` | `fraction, downloaded, total, speed, eta, status` |
| `media`+ | `index, id, extractor_key, title, uploader, duration, webpage_url, filepath` (final path); before `file` |
| `file` | str (final path) |
| `archive` | dict |
| `queued`+ | `urls, first_index, total, overrides` (`{url: options dict}` where they differ from the job's) |
| `item_done` | `index, cancelled, success, files, bot_check, url+, origin+, title+, path+, error+` (<= 200 chars), `reason+` (short Chinese), `skipped+` (`''/'bot_check'/'downloaded'`), `stale_engine+` |
| `done` | `success, failed, files, cancelled, failed_urls, pending_urls, failed_path, archive_errors, bot_check, total+, skipped+` (int), `blocked+` (`''/'bot_check'`), `unfinished+` (int, entries stored), `engine_error+`; always last |
| `update_status`+, `update_progress`+ | str; `fraction, downloaded, total` (the updater no longer emits `title`/`progress`) |
| `updated`, `update_error`, `update_done`, `collect_*` | unchanged |
| `engine_check`+ | `latest, current, newer` |

`pending_urls` = this run's URLs not done or skipped; `failed_path` only when entries remain. Readers (UI, `download_event`) take "+" keys with `.get()`.
Item states: `pending, active, done, skipped, failed, blocked, stopped, unstarted` (transitions: WP2.md §G). Counts: `success` = done; `failed` = failed + the item that itself met the bot check (= `failed_urls`); `skipped` = already downloaded; items blocked without running count in neither. Every `item` gets exactly one `item_done`, except when `_run_once` raises (as in 1.5.1).

### 3.9 Unfinished list (`failed-downloads.json`)
`{"version": 1, "urls": [...], "options": {...}, "entries": [{"key", "format", "url", "title", "reason", "error", "time", "referer", "options"}]}`.
`entries` is authoritative when present; `urls`/`options` are the legacy view for 1.5.1 (newest group of entries with identical `options` and a 1.5.1 mode, else `[]`). Entry `options` = `to_dict()` minus `referers`/`archive_contexts`; `format` = preset id; `reason` in `failed|stopped|unstarted|blocked`. No `entries` key -> migrate the legacy URLs. Unreadable file -> empty list.
`unfinished.merge(entries, results)` after every run: (1) remove entries of the same `format` whose `key` equals the link key or a media key (`<extractor_key lower>:<id>`, never `generic`) of an item that completed or was skipped as downloaded; (2) upsert by `(key, format)` each item that failed, was stopped, never started or was blocked; (3) keep the rest. Empty -> delete the file; nothing to store and no file -> no write. Atomic write as §3.2.
`engine.store_failed_job(urls, options)` (replace all) and `load_failed_job()` (`{'urls', 'options'}` of the newest group, or `None`) remain as wrappers. Retry uses the entry's options with `batch_safe`, `proxy`, `cookies` from current preferences.

### 3.10 Download history (`library.sqlite3`, additive)
`CREATE TABLE IF NOT EXISTS downloads(key TEXT, format TEXT, url TEXT, title TEXT, path TEXT, finished REAL, job TEXT, PRIMARY KEY(key, format))`. `Library.record_download(keys, format, url, title, path, job)`; `find_download(keys, format) -> dict | None` (newest row, with `exists`). Also: `extend_job`, `rename_job`, `history(limit=300)`, `Library(path=None)` (-> `paths.catalog_path()`), `library.open_library(path=None) -> (Library, warning)`; read-modify-write methods use `BEGIN IMMEDIATE`. `download_event` also handles `media` (record; the link key only for non-`multi` items), `title` (first title names the job: `<title>` or `<title> 等 N 条`), `queued` (extend, with `overrides`), `item_done` skipped as downloaded (completed; clears subscription entries) and keeps `item_done.error` in `data['errors']`; it writes only through its own connection (no nested `tx()`). Job `options` may carry an additive `overrides` dict.

### 3.11 Bot check
Engine: the first item failing with `bot_check` on a YouTube-family host blocks that family for the rest of the job (remaining YouTube items skipped, others continue, `done.blocked='bot_check'`). UI: `off -> armed(round, deadline) -> retrying -> off | armed(round+1)`; armed only if the job ended blocked and no cookies file is set; `BOT_RETRY_DELAYS = (1800, 900, 900)` s; at the deadline wait until idle, then retry the `blocked` entries. Cancelled by 取消, 立即重试, a manual start or retry that includes a YouTube-family link, choosing cookies, closing; other downloads leave it armed. Due with no `blocked` entry left -> `off`. Not re-armed after a restart. Wording: usually clears by itself in about 30 minutes; cookies optional.

### 3.12 Updater
`updater.engine_path()`, `engine_version()` (cached on size + mtime), `check_latest(timeout=25)`, `EngineUpdater(emit, cancelled, probe_args).run()`, `restore_bundled()`, `cleanup()`. A new exe is accepted only if size and SHA256 match and a probe run with `engine.probe_args()` (all presets and option blocks) prints no `no such option`; cancel is honoured up to the final `os.replace`; temp name unique per run. Hosts: `api.github.com` and the official release asset only.

### 3.13 ui_theme (values: WP3.md)
Tokens are `(light, dark)` tuples (`BG, SURFACE, LINE, INK, MUTED, FAINT, ACCENT*, OK, WARN, DANGER, SIDE*, ...`; names and values: WP3.md). `FAMILY='Microsoft YaHei UI'`; roles caption 12 / body 13 / heading 15 bold / title 22 bold; `PAGE_PAD 24, CARD_PAD 16, GAP 12`; radius 12 card / 8 control; control height 36 (`sm` 28); label height 22 (caption 18). Text pairs >= 4.5:1 (disabled exempt).
Functions (signatures: WP3.md §2): `enable_dpi_awareness, apply_theme, resolve(token) -> str` (one colour, for plain Tk/ttk APIs), `on_appearance(callback), font, label, button` (`primary|soft|ghost|danger`, real disabled look), `card, chip` (`.set(text, tone)`; `idle|info|ok|warn|danger`), `icon, entry(parent, placeholder='', **kw), option_menu, checkbox, switch, textbox, scroll_area, tabs` (`add/set/get/tab`), `table` (a `ttk.Treeview` with `.container`, `.fill(rows)`, `.set_empty(text)`), `empty_state, setting_row, fit, wrap_to_width, tone_colors, apply_ttk_styles, set_page_cap`.
`enable_dpi_awareness()` = guarded one-time `shcore.SetProcessDpiAwareness(2)`; called in the GUI branch of `app.main()` and first in `StudioApp.__init__`, always before the first Tk root. Then `ctk.set_appearance_mode('light')` (tests too) and `apply_theme()`, before `super().__init__()`. `apply_ttk_styles(root)`: theme `clam`, style `App.Treeview`, negative-pixel fonts x `ScalingTracker.get_widget_scaling(root)`; re-run from a `ScalingTracker.add_widget` callback.

### 3.14 StudioApp
All shared state is created in `__init__` before any page builder (list: WP4.md): `events, runner, worker, worker_operation, run_token, job_options, busy, closing, operation, rows, controls, input_controls, update_buttons, pages, current_page, handlers, unfinished, prefs`, every Tk variable, `init_library(prefs)` and the new `init_collection()` (state moved out of `build_collection_page`).
Owners: studio.py keeps the shell methods (`label, button, card, page, show_page, set_busy, poll, handle, stop, on_close, save_settings, open_folder`); each mixin owns its page builder, callbacks and the `on_<kind>(value)` handlers of its events (download kinds -> DownloadUI; `update*`, `engine_check` -> SettingsUI; `append_log`, `flush_log` -> LogUI). New DownloadUI entry points: `read_form()`, `launch(urls, options, overrides=None, origins=None, auto=False)`, `enqueue_links()`, `add_links(text)`, `append_rows(urls)`, `clear_unfinished()`.
Every activity start (download, update, collection) bumps `run_token`, checks it in its emit and registers `worker`/`worker_operation`; `set_busy(False)` clears `worker`: the watchdog judges only the running activity (WP4.md §C).
`handle(kind, value)`: kinds starting `collect_` -> `handle_collection`; else `self.handlers.get(kind)` (dict kind -> bound method); unknown kinds ignored.
Test-visible names that must keep working (hard rule 4): the list in WP4.md §I, normative for WP4 and WP5. Sanctioned test edits: retry and history resume call `launch()` instead of filling the form (test_ui.py:87-98, test_library_ui.py:101-105).

## 4. UI (widget and string tables: WP4.md, WP5.md)
Shell: sidebar 184 px, brand, five nav buttons with Segoe icon glyphs (下载中心 E896, 网页采集 E721, 资料管理 E8F1, 任务日志 E9F9, 偏好设置 E713), status card (live state line, `yt-dlp <ver>`, `v1.6.0`). Content padding 24/16; page header = title + caption on one line. Download and settings pages are capped at 1080 logical px (left-aligned) in wider or maximised windows; the others stretch. Default 1120x810, minimum 1000x740, geometry and maximised state restored. The window title shows progress and, until focus returns, the background result (WP4.md §E).
Download page (prototype `download-1120x810.png`; where WP4.md §D differs, WP4.md wins). Rows: header, composer card, task card (weight 1).
- Composer: heading `添加下载链接`, chip `N 条链接`, hint, ghost `粘贴` `导入 TXT` `清空`; link box (height 72, `wrap='none'`, placeholder); note line and clipboard chip (hidden when empty); format menu, quality menu and `内嵌字幕` (video presets only), `整个播放列表`; `保存到`, path entry, `更改`, `打开文件夹`, primary `开始下载` (`加入队列` while downloading).
- Task card: row 1 `current_title`, `queue_stats`, `重试未完成 (N)`, `清除未完成`, danger `停止`; row 2 one-line `metrics` across the full width (the bot-check sentence must fit at 1000x740); notice bar (hidden); progress bar; 52 px rows (icon, title, detail, chip); empty state.
- Texts for every state, row chip and hint: WP4.md.
- Keys (WP4.md §E): Ctrl+Enter / Enter, Ctrl+V, Ctrl+L, Ctrl+E, F4, F5, Ctrl+1..5. Launch focus: link box.
- At 1000x740 the list viewport is >= 300 px (>= 5 rows).
Settings: scrollable cards 下载引擎, 文件与命名, 网络, 批量下载, 提醒与界面, 应用与内置组件 built from `setting_row`. Log: header buttons, tagged lines, autoscroll only at the bottom (WP4.md §G). Collect: input card with actions in its header; results table takes the height. Library: card with segmented tabs `任务历史与续采` `增量订阅` `统一资料归档`.

## 5. Compatibility, migration, rollback
- `settings.json`, `failed-downloads.json`: added keys only; 1.5.1 keeps loading both and ignores 1.6.0-only formats. Accepted limit (in the manual): a download run in 1.5.1 rewrites `failed-downloads.json` with its own list, dropping `entries`; 1.6.0 then migrates what 1.5.1 wrote.
- `library.sqlite3` stays at `<app dir>\data`: one new table, new keys inside `jobs.data`. 1.5.1 cannot resume a job with a 1.6.0-only format (logged, nothing lost).
- `engine` keeps every 1.5.1 name; `Runner(emit)`, positional `Options(...)`, `store_failed_job/load_failed_job` keep working.
- The first install keeps `YT-DLP下载器-1.5.1备份.exe` beside the new exe and writes `install-receipt.json`; rollback = run that exe, or `install_release.py --rollback` (receipt-driven, WP6).

## 6. Test plan
Runner: `.venv\Scripts\python.exe -B verification\run_tests.py [--all] [--exe PATH] [--net] [-k NAME]`: one temp sandbox, each test file in its own subprocess whose §3.1 environment the runner builds; fails if anything new appears under `verification/` or the real `%LOCALAPPDATA%\YtDlpDesktop` changes; writes `build\verify\report.json`. Default: unit + UI; `--all`: + fixture server and Chrome; `--exe`: packaged; `--net`: real sites.
Golden tests (WP0, before any refactor): `test_golden_argv.py` (argv matrix), `test_golden_events.py` (transcripts of `Runner.download` with `_run_once` scripted), `test_golden_runonce.py` (the real `_run_once` against scripted children) and `test_golden_ui.py` (widget state after a scripted `handle`/`set_busy` sequence). All byte-identical through WP1; `runonce` always; `ui` until WP4 step 2. WP2 adds `argv/events-1.6.0.json`, records the diff and keeps a projection test: the 1.5.1 scenarios on the new engine, new keys and kinds dropped, equal `events-1.5.1.json` except differences enumerated in the test.
Regression tests: R1 raising library emit; R2 `create_job` failure; R3 dead worker -> one synthetic terminal event; R4 `collect_done` with a failing library; R5 poll survives handler exceptions; R6 forced close; R7 settings fuzz + atomic save; R8 subscription tick, background check; R9 unfinished list; R10 bad item does not abort the queue; R11 bot block; R12 `_run_once` kills the child when emit raises; R13 retry leaves form and link box alone; R14 single instance; R15 job object; R16 geometry. Cases: WP files.
UI test rules: one live `StudioApp` per process (`snapshot_ui.run` only after the file's own app is destroyed); light mode forced; non-smoke apps start from `_sandbox.write_settings()` (update check and clipboard hint off); manual starts only through `verification/sandbox_app.py`.
Layout: `verification/snapshot_ui.py` (all pages and tabs at 1000x740, 1120x810, 2560x1392; assertions in WP4.md). Every WP runs `run_tests.py` before handoff (WP2, WP5, WP6 also `--all`) and pastes the summary into its note.

## 7. Work packages
Order: WP0 -> WP1 -> WP2; WP3 in parallel (new files only); then WP4 -> WP5 -> WP6 -> WP7. A WP edits only files it owns and may read everything else. A move and a behaviour change never share a step; the protected blocks (failed-list merge in `Runner.download`'s `finally`, the `_run_once` loop, `set_busy`, `poll`) move verbatim and change only where the WP file names the lines; the runner is green after every step. Handoff note `docs/1.6.0/handoff/WPn.md`: changes per file, deviations with reasons, test summary, open issues, notes for the next WP, sha256 of every owned file. Without version control each WP ends by copying `src\`, `app.spec`, `verification\*.py` and `verification\golden\` to `docs\1.6.0\handoff\snapshot-WPn\`: "the WPn state" in an acceptance check is that folder, and a failed WP is reverted from the previous one.

| WP | goal; steps | owns | needs | acceptance |
|---|---|---|---|---|
| 0 | safety net: sandbox helper, runner, tests to temp dirs, goldens | `verification/`: `run_tests, _sandbox, sandbox_app, test_golden_*, golden/`, all existing files; `src/engine.py` line 23 | - | runner green on 1.5.1 sources; nothing written to `verification/` or the real state dir |
| 1 | core: paths, procutil, applog, settings, updater; engine/collector/library moved onto them; engine-side state bugs; single instance | `src/`: `paths, procutil, applog, settings, updater, engine, collector, library, app`; `verification/test_core_*.py` | 0 | four goldens byte-identical; R7, R12, R14, R15 pass; 1.5.1 UI still works |
| 2 | engine features: urls, formats, naming, tags, temp path, pacing, queue + enqueue, unfinished list, history, bot block, subtitles | `src/`: `urls, formats, naming, unfinished, engine, library`; engine/library tests, 1.6.0 goldens | 1 | §3.3-3.11 hold; R9-R11 pass; `runonce` golden and projection test pass; `--all` green; 1.5.1 UI still works |
| 3 | design system: tokens, theme patch, factories, table, demo | `src/ui_theme`; `verification/`: `demo_theme, test_theme` | - | demo renders; contrast, DPI, disabled-state, two-root assertions pass; no app-module import; no existing file changed |
| 4 | shell split, download/settings/log pages, UI-side state bugs, snapshot script | `src/`: `studio, ui_download, ui_settings, ui_log, app, progress`; `verification/`: `test_ui, test_progress, test_animation_timing, test_golden_ui, test_ui_state, snapshot_ui` | 1, 2, 3 | `ui` golden identical after the split; §4 for these pages; R1-R3, R5, R6, R13, R16 pass; snapshot assertions pass |
| 5 | collect + library pages, robust-6/7/13 UI | `src/`: `collection_ui, library_ui`; `verification/`: `test_library_ui, test_ui_pages, snapshot_ui` (collect/library parts) | 4 | R4, R8 pass; snapshot assertions pass for all pages; `--all` green |
| 6 | onedir build, release gate, version 1.6.0, manuals, install/rollback | `app.spec, build.py, install_release.py`, `verification/`: `verify_release, test_packaged_collection`; both manuals; `APP_VERSION` line | 5 | staged folder passes the gate from a Chinese path; install, reinstall and rollback rehearsed in a temp folder |
| 7 | dark palette + appearance setting (stretch) | `src/`: `ui_theme, progress, ui_settings, ui_log, app`; `snapshot_ui, test_theme` | 6 | both modes pass the snapshot assertions; default light; droppable |
