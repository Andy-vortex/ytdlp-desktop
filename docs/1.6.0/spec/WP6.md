# WP6 - Packaging, release gate, version 1.6.0, manuals, install and rollback

Read: SPEC.md §5, §6; all handoff notes; audit arch-1, arch-5, arch-6 (gate part), robust-5 (skeptic notes on the worker), and "missed" in the architecture lens (build entry point, stale gate).
Owns: `app.spec`, new `build.py`, new `install_release.py`, `verification/verify_release.py`, `verification/test_packaged_collection.py`, `使用说明.txt`, `架构说明.txt`, and the `APP_VERSION` line of `src/engine.py` (set to `'1.6.0'` as the first step).
Must not edit: any other source or test file; `vendor/`; `prepare_vendor.py`; `requirements-*.txt`.
Never write into `D:\YT-DLP下载器` (the installed app) in this WP. The real installation is a separate, explicitly approved action by the owner; this WP delivers the staged folder, the installer script and a rehearsal in a temp folder.

## A. `app.spec` (onedir; arch-1 + arch-5; the audit's PoC spec was built and launched twice)
- Keep `collect_data_files('customtkinter')` and the scrapling/browserforge/apify/tld/patchright data and hidden imports as they are.
- `data` keeps only `(str(root / 'app.ico'), '.')` besides those; remove every `vendor` entry from `data` (this is what bundled the FFmpeg DLLs twice).
- `hidden += ['PIL.ImageDraw', 'PIL.ImageFont']` (icons; the 1.5.1 build shipped only `PIL.Image`/`ImageTk`).
- Replace the `EXE(...)` call by:
```python
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='YT-DLP下载器', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, icon=str(root / 'app.ico'),
          contents_directory='_internal')
vendor = Tree(str(root / 'vendor'), prefix='vendor', excludes=['verification', '*.zip', '*.part'])
coll = COLLECT(exe, a.binaries, a.datas, vendor, strip=False, upx=False, name='YT-DLP下载器')
```
Result: `dist\YT-DLP下载器\YT-DLP下载器.exe` + `_internal\` (about 465 MB, `sys._MEIPASS = <exe folder>\_internal`, so `paths.RESOURCE_ROOT` and `paths.APP_DIR` need no change).

## B. `build.py` (one entry point, never downloads)
`python -B build.py [--skip-tests]`:
1. Check `vendor/`: sha256 of `vendor/yt-dlp.exe` equals the `yt-dlp/yt-dlp` entry of `vendor/manifest.json`; the ten files of `vendor/bin` exist (`avcodec-63.dll, avdevice-63.dll, avfilter-12.dll, avformat-63.dll, avutil-61.dll, deno.exe, ffmpeg.exe, ffprobe.exe, swresample-7.dll, swscale-10.dll`); no `*.zip`/`*.part` left.
2. Unless `--skip-tests`: `verification\run_tests.py --all` must exit 0.
3. `.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm --distpath build\dist --workpath build\work`.
4. Stage: copy `build\dist\YT-DLP下载器` to `build\package-1.6.0\YT-DLP下载器` (replace an existing stage) and write `build\package-1.6.0\build.json`: `{'source_sha256': <same definition as run_tests.py: sorted src/*.py then app.spec, computed before step 3>, 'exe_sha256', 'built': <ISO time>}`. This ties the staged exe to the sources it was built from; without it an old stage plus a fresh `run_tests.py --exe` would pass the gate.
5. `verification\run_tests.py --all --exe build\package-1.6.0\YT-DLP下载器\YT-DLP下载器.exe`, then `verification\verify_release.py`.
All output stays under `build\`.

## C. `verification/verify_release.py` (gate that cannot pass on stale evidence)
Arguments: `--stage PATH` (default `build\package-1.6.0\YT-DLP下载器`). Fails with a clear message on the first violated check:
1. `YT-DLP下载器.exe` and `_internal\` exist; no `av*.dll` or `sw*.dll` directly in `_internal`; exactly the ten files in `_internal\vendor\bin`; `_internal\vendor\yt-dlp.exe` matches the manifest hash; `_internal\vendor\manifest.json` and `_internal\vendor\licenses` exist; `_internal\patchright\driver\node.exe` exists; `_internal\PIL` contains `_imagingft*.pyd`.
2. `<exe> --self-test <tmp>\diag.json` exits 0 with `status == 'passed'`, `app_version == '1.6.0'`, `resource_root` ending in `\_internal`, components `yt-dlp, ffmpeg, ffprobe, deno, scrapling` present. Run it with `LOCALAPPDATA` and `YTDLP_DESKTOP_APP_DIR` pointing into a temp folder. Then run it once more with only `LOCALAPPDATA` redirected (no `YTDLP_DESKTOP_APP_DIR`) and require `app_dir` (WP1 §K) to equal the stage folder and `state_dir` to lie in the temp folder: this is the only observation of where the onedir build resolves `APP_DIR` (`--self-test` writes nothing there).
3. Download smoke through the packaged exe against an in-process fixture server (`--self-test R --smoke-url http://127.0.0.1:<port>/fixture.mp4 --smoke-output <tmp> --smoke-archive`): `download_smoke.success == 1`, a file exists, `archive_entries` non-empty.
4. GUI smoke: start the exe (sandboxed environment, `--smoke-url`/`--smoke-output` so the single-instance guard is skipped), wait up to 10 s for a visible top-level window of that process whose title ends with `YT-DLP 下载器`, post `WM_CLOSE`, expect exit code 0 within 10 s. Record the time to window.
5. `build\verify\report.json` exists, every result is `passed` or `skipped` (list the skipped ones in the receipt), `app_version == '1.6.0'`, `source_sha256` equals the value recomputed now from `src/*.py` + `app.spec`, and `exe.sha256` equals the staged exe's hash. The run must include the tiers `all` and `exe`. `build\package-1.6.0\build.json` exists and its `source_sha256` and `exe_sha256` equal the same two recomputed values (a stage built from older sources fails here).
6. Write `build\verify\release-1.6.0.json`: version, exe size and sha256, total folder size and file count, component versions, time to window, the list of test files with status, timestamp. Print it.
Do not read any of the old `verification\*.json` reports.

## D. `verification/test_packaged_collection.py` (`# tier: exe`)
Rewrite so that the packaged worker is exercised through the in-app path (the 1.5.x test launched the exe directly, so `CollectionRunner.run` with its environment and temp handling was never tested): with `sys.frozen = True` and `sys.executable = VERIFY_EXE` patched for the duration of the call, run `collector.CollectionRunner(emit).run(options)` against the local test site for the static page, the dynamic (Chrome) page when Chrome is installed, stop during a slow page (process tree gone within 8 s, `collect_done {'cancelled': True}`), and the stop/restart/resume scenario of the old test. Afterwards `paths.collect_dir()` contains no `run-*` folder. The quotes.toscrape.com part runs only with `VERIFY_NET=1`. Outputs go to `_sandbox.out('packaged-collection')`.

## E. `install_release.py` (prepared and rehearsed here; run on the real folder only by the owner's decision)
`python -B install_release.py --stage <staged folder> --target <app folder> [--dry-run] [--shortcut] [--rollback] [--old-version V] [--instance-name NAME]`. Never merges into an existing `_internal`; never touches `data\`, `下载文件\`, `源码\` or any other entry of the target. It must be correct for a repeated install as well (a 1.6.0 rebuild installed over 1.6.0), not only for the first 1.5.1 -> 1.6.0 run.
Install:
1. Refuse when the app is running: the mutex `--instance-name` (default `Local\YtDlpDesktop.Instance`, the name of WP1 §B) can be opened (1.6.0), or opening `<target>\YT-DLP下载器.exe` with `open(path, 'r+b')` raises `PermissionError` (1.5.1). Message: `请先关闭 YT-DLP 下载器再安装。`
2. Require `build\verify\release-1.6.0.json` whose exe sha256 equals the staged exe.
3. Copy `_internal` to `<target>\_internal.new` and the exe to `<target>\YT-DLP下载器.new.exe`; compare file count and total size with the stage.
4. Move the previous program aside according to its layout:
   - onedir (`<target>\_internal` exists, i.e. a previous 1.6.0 install): move `YT-DLP下载器.exe` and `_internal` together into `<target>\_previous\` (delete an older `_previous` first). No `…备份.exe` is created - a onedir exe cannot run alone and must not be labelled as a backup - and an existing `YT-DLP下载器-1.5.1备份.exe` is left alone.
   - onefile (exe without `_internal`, the current 1.5.1 installation): rename the exe to `YT-DLP下载器-<version>备份.exe`. `<version>` = `--old-version` when given; otherwise read it from the program itself: run `<old exe> --self-test <tmp>\old.json` with `LOCALAPPDATA` redirected to a temp folder (timeout 300 s) and take `app_version`; if that fails use `旧版`. For the current installation the result is `YT-DLP下载器-1.5.1备份.exe`; if the name exists, append a timestamp.
   - none (no exe): nothing to move.
5. Rename `_internal.new` -> `_internal`, `YT-DLP下载器.new.exe` -> `YT-DLP下载器.exe`, then write `<target>\install-receipt.json` atomically: `{"installed": "1.6.0", "time": ..., "previous": {"layout": "onefile" | "onedir" | "none", "version": ..., "exe": <exact backup file name, or "_previous\\YT-DLP下载器.exe">, "internal": "_previous\\_internal" | null}}`. The exe keeps its full path, so an existing shortcut, `data\library.sqlite3`, `下载文件\` and `%LOCALAPPDATA%\YtDlpDesktop` keep working with no migration.
6. Post-install gate from the installed path: `--self-test` (sandboxed `LOCALAPPDATA`; `app_dir` must equal the target), the fixture download smoke, the GUI smoke. On failure run the rollback automatically and report.
7. `--shortcut`: create `YT-DLP 下载器.lnk` on the Desktop pointing at the exe with the app folder as working directory (PowerShell `WScript.Shell`), only if none exists. Off by default (the audit found no shortcut today).
8. Print what remains for the owner: one real YouTube audio download (this is the only check of `deno.exe` and FFmpeg running from the non-ASCII `_internal\vendor\bin` against the real site); delete `_previous` when satisfied; two orphaned `_MEI*` folders (about 640 MB each) in `%TEMP%` from 1.5.1 can be deleted by hand.
Rollback (`--rollback`, also the automatic path): acts only on `install-receipt.json` - never on a guessed file name. Refuse while running; refuse with a message (and change nothing) when the receipt is missing or a file it names does not exist. Move the current `YT-DLP下载器.exe` and `_internal` together into `<target>\_rolledback-1.6.0\` (replace an older one), so that no 1.6.0 exe is left behind without its `_internal` (double-clicking such an exe gives a bootloader error). Then restore the previous program as the receipt says: `onefile` -> rename the named backup exe back to `YT-DLP下载器.exe`; `onedir` -> move `_previous\YT-DLP下载器.exe` and `_previous\_internal` back; `none` -> nothing. Finally rename the receipt to `install-receipt.rolledback.json`. Without the script, after the first install: double-click `YT-DLP下载器-1.5.1备份.exe`; it is the complete 1.5.1 program and uses the same `data\` folder and settings (SPEC §5 lists what 1.5.1 ignores and what it overwrites).
Rehearsal (mandatory, automated in temp folders; every call passes `--instance-name Local\YtDlpDesktop.Test.<uuid>` so that the owner's running app neither blocks nor is touched by the rehearsal):
- first install: a fake target containing a dummy `YT-DLP下载器.exe`, `data\library.sqlite3` (copy of a sandbox DB) and `下载文件\x.txt`; install with the real stage and `--old-version 1.5.1`; assert the layout, the receipt, that `data\` and `下载文件\` are byte-identical, and that the installed exe passes `--self-test` with only `LOCALAPPDATA` redirected, reporting `app_dir == <target>` and `catalog == <target>\data\library.sqlite3`; `--rollback`; assert the dummy exe is back under its original name and `_rolledback-1.6.0` holds exe + `_internal`;
- install -> install -> rollback: the second install moves the first into `_previous`, creates no second `…备份.exe` and leaves `YT-DLP下载器-1.5.1备份.exe` untouched; the rollback restores the first onedir install, which passes `--self-test`; no exe exists anywhere without an `_internal` beside it except the onefile backup;
- a dummy old exe without `--old-version`: the backup is named `YT-DLP下载器-旧版备份.exe`;
- "app is running" (the test holds the mutex of `--instance-name`): install and rollback both refuse and change nothing;
- "post-install gate fails" (stage with a broken exe): the automatic rollback restores the previous state;
- `--rollback` without a receipt refuses.

## F. Manuals
`使用说明.txt` (rewrite the affected parts, keep the plain-text style and the existing collection/library chapters where behaviour is unchanged). Must state:
- Title `YT-DLP 下载器 1.6.0`; the program is now a folder: `YT-DLP下载器.exe` and `_internal` must stay together; starts in about a second; a second double-click brings the running window forward.
- Daily flow: paste (share text is fine, same video is merged, radio/playlist parameters are ignored unless 整个播放列表 is ticked; playlist/search/channel links download only the first item and the page says so), choose a format (seven formats, including M4A and MP4), 开始下载; the links move from the box into the task list; more links can be added with 加入队列 while downloading; 恢复刚才的链接.
- File names: default without `[视频ID]`; the three naming styles; when 整个播放列表 is ticked or 统一归档 is on, names always keep `[视频ID]` so that running the same playlist again skips what is already there; existing names are never overwritten (` (2)`); tags and cover are embedded (not the cover for WAV); work files are kept out of the save folder; 内嵌字幕 for video.
- Task rows: double-click plays, right-click menu (在文件夹中显示, 复制链接, 重试此条, 仍然下载, 从列表移除); 已下载过 and how to download again; shortcuts (Ctrl+V, Ctrl+Enter, Enter, Ctrl+L, Ctrl+E, F4, F5, Ctrl+1..5).
- Pacing: no wait before a single download; 10-20 s between items in 稳健模式; request spacing and the 429 cooldown rules unchanged (keep items 12-13).
- 未完成 list: what is kept (failed, stopped, not started, deferred), 重试未完成, 清除未完成, single retry; it no longer replaces older entries and retry does not change the form.
- YouTube verification: the queue defers the remaining YouTube links, the block usually clears by itself in about 30 minutes, the automatic retry countdown (30, 15, 15 minutes while the program stays open; downloads from other sites can go on meanwhile and do not cancel it; starting a YouTube download yourself does; it is not resumed after the program is closed), 立即重试 / 取消; cookies remain optional; cookies paragraph kept (path only; yt-dlp writes updated cookies back to that file).
- Settings: every switch of the settings page; engine update with compatibility check, 恢复内置引擎, the daily version check (one request to api.github.com, never installs).
- Completion reminder, window title, clipboard hint (never starts by itself, clipboard content is not stored).
- Log page buttons; data locations: `%LOCALAPPDATA%\YtDlpDesktop` (settings.json, failed-downloads.json, download.log, engines, tmp, work, collect, cache) and `程序目录\data\library.sqlite3` (unchanged); what to back up.
- Rollback to 1.5.1 and its limits: 1.6.0-only settings are reset by 1.5.1; 未完成 entries in M4A/MP4 formats are not shown there; and any download made with 1.5.1 replaces the 未完成 list with 1.5.1's own list, so 1.6.0 entries in other formats or with other settings are lost (the downloaded files and the history are not affected).
- Remove the outdated statements (single file, unpacking delay, "每条下载前随机等待", "不同输出设置的新失败任务会替换清单", the verification list of 1.5.x).
`架构说明.txt`: module map from SPEC §2 in Chinese; event flow (worker -> Queue -> 25 ms poll -> dispatch table); the queue model with `enqueue`; unfinished list format and merge rules; download history table; paths and overrides; job object and per-run TEMP; updater probe; design system and DPI call; test runner, goldens, snapshot script; build and install procedure. Keep the 1.4.0/1.5.0/1.5.1 chapters as history, marking superseded statements.

## Acceptance (verifier)
1. `python -B build.py` finishes with `release-1.6.0.json`; the stage contains no top-level `av*.dll`/`sw*.dll` in `_internal` and is about 465 MB.
2. The staged exe copied to a temp folder with a Chinese name passes `--self-test`, the fixture download smoke and the GUI smoke; time to window is reported (target: under 2 s on the owner's machine).
3. Changing one byte in any `src/*.py` after the test run makes `verify_release.py` fail (stale evidence check); restore afterwards.
4. All rehearsals of §E pass; nothing under `D:\YT-DLP下载器` other than `源码\build` was created or modified (compare a directory listing with sizes and mtimes of `D:\YT-DLP下载器` excluding `源码` before and after the WP).
5. Both manuals mention every item of §F; no sentence describes removed behaviour.

## Handoff note must include
Build time, folder size, time to window, the receipt, the exact command the owner (or the lead) must run to install, the rollback command, the manual real-network check that remains, and the snapshot folder with the sha256 list (SPEC §7).
