# WP0 - Safety net (sandboxed runner, goldens, tests to temp dirs)

Read: SPEC.md §3.1, §6, §7; audit arch-6, robust-14, visual-9 (skeptic parts).
Goal: before anything is refactored, one command proves behaviour is unchanged, and no test can touch the owner's real `%LOCALAPPDATA%\YtDlpDesktop` or leave files in `verification/`.

Owns: `verification/run_tests.py`, `verification/_sandbox.py`, `verification/sandbox_app.py`, `verification/test_golden_argv.py`, `verification/test_golden_events.py`, `verification/test_golden_runonce.py`, `verification/test_golden_ui.py`, `verification/golden/` (new); every existing `verification/*.py` (edits listed below); `src/engine.py` line 23 only.
Do not touch: any other source file, the `backup-*` folders, `fixture/`, the old `*.json` reports (leave them; they are history).

Safety while working: never run a test before step 1 and 2 are done. Run Python as `.venv\Scripts\python.exe -B`.

## Step 1 - one source line (the only src edit)
`src/engine.py:23` becomes:
```python
APP_DIR = (Path(os.environ['YTDLP_DESKTOP_APP_DIR']) if os.environ.get('YTDLP_DESKTOP_APP_DIR')
           else Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1])
```
Reason: `library.py:19`, `library_ui.py:24`, `collector.py:36,301` and `studio.py:85` derive the history DB, the collection temp folder, the certificate cache and the default output from `APP_DIR`; `LOCALAPPDATA` alone does not isolate them (a probe in the audit created `源码\data\library.sqlite3`).

## Step 2 - `verification/_sandbox.py`
Every test file imports it as its first import (`import _sandbox`; the script folder is `sys.path[0]`). Contract:
```python
ROOT: Path            # source root
SRC: Path             # ROOT / 'src', inserted at sys.path[0]
BASE: Path            # sandbox root
OUT: Path             # BASE / 'out'   (env VERIFY_OUT)
REAL_STATE: Path      # the real state dir, computed BEFORE the override (never written)
def out(*parts) -> Path                    # OUT / parts, parent created
def state_patch(path)                      # object with start()/stop(), also a context manager
def set_state_dir(path) -> None            # permanent variant for script-style tests
def app_args(smoke_url=None, smoke_output=None) -> argparse.Namespace
def fixture_site() -> tuple[str, ThreadingHTTPServer]   # serves verification/fixture on 127.0.0.1:0, daemon thread; returns base URL
def write_report(name: str, data: dict) -> Path         # OUT / name, UTF-8 JSON
def write_settings(**values) -> Path                    # sandbox settings.json (see below)
def instance_name(base) -> str                          # 'Local\\YtDlpDesktop.Test.' + sha1(str(base).encode()).hexdigest()[:12]
def assert_isolated() -> None
```
Sandbox variables (the same set in both modes): `LOCALAPPDATA=BASE\localappdata`, `YTDLP_DESKTOP_APP_DIR=BASE\app`, `TEMP=TMP=BASE\tmp`, `VERIFY_OUT=BASE\out`, `XDG_CACHE_HOME=BASE\cache`, `PYTHONIOENCODING=utf-8`, `YTDLP_DESKTOP_INSTANCE=instance_name(BASE)` (the single-instance mutex name, WP1 §B: a test must never meet the mutex of the owner's running app), and `YTDLP_DESKTOP_STATE` removed.
Import-time behaviour:
1. Runner mode (env `YTDLP_VERIFY_SANDBOX` is set): `BASE` = that folder. The runner has already built the whole environment (Step 4.3), so isolation does not depend on this import; `_sandbox` only verifies it and raises `RuntimeError` unless `LOCALAPPDATA`, `YTDLP_DESKTOP_APP_DIR`, `TEMP`, `TMP` and `VERIFY_OUT` resolve inside `BASE`, `YTDLP_DESKTOP_INSTANCE` is set and `YTDLP_DESKTOP_STATE` is absent. `REAL_STATE = Path(os.environ['YTDLP_VERIFY_REAL_STATE'])`.
2. Standalone mode (a file run on its own): compute `REAL_STATE` from the current `LOCALAPPDATA` first. `BASE` = env `YTDLP_VERIFY_BASE` when set (kept afterwards; used by `sandbox_app.py --base`), else `tempfile.mkdtemp(prefix='ytdlp-verify-')` with an `atexit` removal (`shutil.rmtree(ignore_errors=True)`) unless env `YTDLP_VERIFY_KEEP=1`. Then set the sandbox variables itself. This makes a file run on its own as safe as a runner run.
3. Both modes: create the folders; set `tempfile.tempdir = None` (otherwise `tempfile` keeps the real temp dir it cached before `TEMP` changed); set `sys.dont_write_bytecode = True` (the environment variable does not affect the already running process); insert `SRC` into `sys.path`. Do not import any `src` module here.
`state_patch(path)` patches the attribute `STATE_DIR` on every module among `sys.modules['paths']`, `sys.modules['engine']` that has it (so it keeps working after WP1 introduces `paths.py`). `assert_isolated()` imports `engine` and raises unless `engine.STATE_DIR` and `engine.APP_DIR` resolve inside `BASE`; the golden tests call it. `write_settings(**values)` writes `{'check_engine_updates': False, 'clipboard_hint': False, **values}` as UTF-8 JSON to `BASE\localappdata\YtDlpDesktop\settings.json` (folder created) and returns the path. Every test that builds a non-smoke `StudioApp` calls it before constructing the app (WP4 §H), so no default-tier test contacts api.github.com or reads the owner's clipboard.

`verification/sandbox_app.py [--keep] [--base DIR] [app arguments...]`: the only way to start the app by hand during this upgrade. It sets `YTDLP_VERIFY_KEEP` / `YTDLP_VERIFY_BASE` from its own options, imports `_sandbox`, prints `BASE`, then runs `app.main()` with the remaining arguments. Two launches with the same `--base` share one sandbox and therefore one instance name (used for the single-instance check). `python src\app.py` is never launched directly in any acceptance step: from source `APP_DIR` is the repo root, so it would create `源码\data\library.sqlite3` and `源码\下载文件` (the leak the audit reproduced).

## Step 3 - move every test output into the sandbox
Mechanical edits; test logic unchanged. In each file put `import _sandbox` before the first `src` import and delete the local `sys.path.insert`.

| file | edit |
|---|---|
| `test_engine.py`, `test_progress.py` | import only |
| `test_batch_resilience.py` | line 24: `self.state_patch = _sandbox.state_patch(self.root / 'state')` (start/stop as today) |
| `test_ui.py` | `StudioApp(_sandbox.app_args('http://127.0.0.1/fixture.mp4', str(_sandbox.out('ui-test'))))`; lines 35-40, 53, 88, 103, 108: every `ROOT / 'verification/...'` path becomes `_sandbox.out('...')` with the same leaf name; line 135: `_sandbox.write_report('ui-report.json', result)` |
| `test_library_ui.py` | `OUT = _sandbox.out('library-ui')`; report via `write_report('library-ui-report.json', ...)` |
| `test_animation_timing.py` | smoke output `_sandbox.out('ui-test')`; report via `write_report('animation-report.json', ...)` |
| `test_library.py` | `OUT = _sandbox.out('library-test')`; line 20 `_sandbox.set_state_dir(OUT / 'state')`; report `write_report('library-report.json', ...)`; first line `# tier: all` |
| `test_collection.py` | `OUT = _sandbox.out('collection-test')`; line 19 `_sandbox.set_state_dir(...)`; report stays `OUT/'report.json'`; `# tier: all` |
| `test_integration.py` | replace the `server-info.json` read by `base, _server = _sandbox.fixture_site(); url = base + '/fixture.mp4'`; `RUN = _sandbox.out('integration')`; line 16 `_sandbox.set_state_dir(RUN / 'state')`; report `write_report('integration-report.json', ...)`; `# tier: all` |
| `test_packaged_collection.py` | `# tier: exe`; `EXE = Path(os.environ['VERIFY_EXE'])` (print `SKIPPED: no --exe` and exit 0 when unset); `OUT = _sandbox.out('packaged-collection')`. Its real-website part (line 40, quotes.toscrape.com) runs only when env `VERIFY_NET=1` |
| `preview_collection.py` | import `_sandbox` first (manual preview must not write real state) |
| `fixture_server.py` | stop writing `server-info.json` unless called with `--write PATH`; print the JSON line as today |
| `verify_release.py` | untouched (WP6 rewrites it) |

`ROOT / 'verification/fixture/...'` reads stay as they are.

## Step 4 - `verification/run_tests.py`
CLI: `run_tests.py [--all] [--exe PATH] [--net] [-k SUBSTRING] [--keep]`.
1. Create `BASE = tempfile.mkdtemp(prefix='ytdlp-verify-')`. Snapshot (a) all relative paths under `verification/` except `__pycache__`, (b) for every file under the real state dir `(relative path, size, st_mtime_ns)` (the folder may not exist).
2. Discover `verification/test_*.py`. Tier = the value of a `# tier: all|exe|net` comment within the first five lines, default tier otherwise. Default run = default tier; `--all` adds `all`; `--exe PATH` adds `exe` and sets `VERIFY_EXE`; `--net` sets `VERIFY_NET=1` and adds `net`. `-k` filters by file name.
3. Run each file as its own process: `[sys.executable, '-B', file]`, `cwd=ROOT`, with an environment the runner builds itself, so that a test file which forgets `import _sandbox` (or may not import it, like WP3's `test_theme.py`) is isolated all the same. Child environment = a copy of the current one with `S = BASE\<file stem>` (a fresh subfolder per file, its folders created by the runner): `YTDLP_VERIFY_SANDBOX=S`, the sandbox variables of Step 2 computed for `S` (`LOCALAPPDATA=S\localappdata`, `YTDLP_DESKTOP_APP_DIR=S\app`, `TEMP=TMP=S\tmp`, `VERIFY_OUT=S\out`, `XDG_CACHE_HOME=S\cache`, `PYTHONIOENCODING=utf-8`, `YTDLP_DESKTOP_INSTANCE=Local\YtDlpDesktop.Test.<sha1(S)[:12]>`), `YTDLP_VERIFY_REAL_STATE=<real state dir>`, `PYTHONDONTWRITEBYTECODE=1`; `YTDLP_DESKTOP_STATE`, `YTDLP_VERIFY_BASE` and `YTDLP_VERIFY_KEEP` are removed. stdout/stderr captured as UTF-8 with `errors='replace'`, timeout 900 s (1800 s for tier `all`). Status: exit 0 -> `passed` (or `skipped` when the output's first line starts with `SKIPPED`), otherwise `failed`; on timeout kill the process tree with `taskkill /T /F`.
4. After all files: compare both snapshots. Any new path under `verification/` or any difference in the real state dir is a failure named `isolation` (print the paths; do not delete them; if the installed app is running, say so in the message, because its own log writes look the same).
5. Write `ROOT\build\verify\report.json`: `{"app_version": <regex on src/engine.py APP_VERSION>, "source_sha256": <sha256 over the bytes of sorted src/*.py then app.spec>, "exe": null | {"path", "sha256"}, "tiers": [...], "started", "finished", "results": [{"file", "tier", "status", "seconds", "tail": <last 40 output lines when not passed>}], "reports": {<name>: <parsed JSON of every *.json found in any sandbox out folder>}}`.
6. Print one line per file and a summary; exit code 0 only if nothing failed. Remove `BASE` on success unless `--keep`; on failure keep it and print its path.
No third-party imports; do not import `src` modules in the runner process.

## Step 5 - golden tests (unittest files, default tier)
Both call `_sandbox.assert_isolated()` in `setUpClass`. Golden files live in `verification/golden/`; a test rewrites its golden only when env `GOLDEN_UPDATE=1` (run that file directly, not through the runner) and otherwise compares with `assertEqual` on the parsed JSON and prints a unified diff of the pretty-printed text on mismatch.

`test_golden_argv.py` -> `golden/argv-1.5.1.json` = `{case: [argv...]}`. Cases: for each of the five `engine.MODES` (index 0-4) the variants `default`, `unsafe` (`batch_safe=False`), `playlist`, `proxy` (`http://127.0.0.1:7890`), `cookies` (a temp Netscape file), `referer` (`referers={url: 'https://example.org/page'}`), `archive` (`archive_root=<tmp>`), and for index 4 also `1080p`. URL `https://example.com/watch?v=abc&list=xyz`. Normalise every argv element by replacing, longest first: the engine exe path -> `<ENGINE>`, `str(engine.BIN)` -> `<BIN>`, the output dir -> `<OUT>`, the cookies path -> `<COOKIES>`, the archive root -> `<ARCHIVE>`, the state dir -> `<STATE>`.

Determinism: wherever a transcript would contain a random or clock-derived value the test patches the source (`random.uniform` -> its first argument) or normalises the value; a golden never depends on timing.

`test_golden_events.py` -> `golden/events-1.5.1.json` = `{scenario: {"events": [[kind, value], ...], "failed_file": <parsed failed-downloads.json or null>}}`. Each scenario builds `engine.Runner(emit)` with state redirected by `state_patch`, patches `_run_once` with a scripted `side_effect` and `_wait` with a recorder returning `True`, then calls `download(urls, Options(<tmp>/out, ...))`. URLs are `https://example.invalid/a|b|c`. Scenarios:
1. `two_success` (each call appends one path to `files`, returns `(0, False, None)`);
2. `fail_then_success`;
3. `rate_limit_then_success` (`(1, True, None)` then `(0, False, None)`);
4. `rate_limit_exhausted_then_next` (three limited results for a, then b succeeds);
5. `retry_after` (`(1, True, 650)` then success);
6. `cancel_during_first` (the side effect calls `runner.cancel()` and returns `(1, False, None)`; three URLs);
7. `bot_check_first_of_two` (side effect sets `runner.bot_check = True`, returns `(1, False, None)`; `batch_safe=False`);
8. `exception_on_second_of_three` (side effect raises `RuntimeError('boom')`);
9. `previous_job_same_options` (call `store_failed_job([a, b], options)` first, then download `[a]` successfully);
10. `previous_job_other_format` (stored with `MODES[4]`, download `[a]` with `MODES[3]`).
Normalise: paths as above; recorded `_wait` calls are inserted into the transcript as `["_wait", [seconds, index, total, reason]]`.

`test_golden_runonce.py` -> `golden/runonce-1.5.1.json` = `{scenario: {"events": [[kind, value], ...], "result": [code, limited, retry_after], "bot_check": bool}}`. It runs the real `Runner._run_once` (the loop is not patched) against children `[sys.executable, '-c', script]` that print scripted lines with `flush=True` and exit with a given code; `files = []`, `index=1`, `total=2`. Scenarios: `normal` (a `__GUI_TITLE__"T"` line, `[download] Sleeping 0.15 seconds ...`, one `__GUI_PROGRESS__` line, a `__GUI_FILE__` line with a temp path, exit 0); `rate_limit` (`ERROR: HTTP Error 429: Too Many Requests`, then the child sleeps 30 s: the loop must kill it and return `limited` True); `retry_after` (a line containing `Retry-After: 7` before the 429 line -> `retry_after == 7`); `bot_check` (the `Sign in to confirm you’re not a bot` line, exit 1); `plain_failure` (`ERROR: boom`, exit 1). Normalise: `seconds` in `waiting` payloads -> `"<S>"`, the exit code of a killed child -> `"<KILLED>"`, temp paths as above. This is the transcript golden of the protected `_run_once` loop; it must stay identical after WP1 §E and WP2 §F.

`test_golden_ui.py` -> `golden/ui-1.5.1.json` = `[{"step": <label>, "state": {...}}, ...]`. It guards the riskiest move of the upgrade, the split of `studio.py` and the `handle` dispatch table (WP4 steps 1-2), which `test_ui.py` covers only in part. One smoke app (`_sandbox.app_args(...)`, withdrawn), `populate_queue` with three URLs, then a scripted sequence that reaches every branch of `handle` (studio.py:609-722) through `app.handle(kind, value)` and `app.set_busy`:
- `operation = 'download'`: `item`; `title`; `log` with an `[ExtractAudio]` line and with a plain line; `waiting`; `resuming`; `progress` with a fraction, with `fraction=None` and with `status='finished'`; `file`; `item_done` (success with files, failed, failed with `bot_check`, cancelled); `done` (all ok; with failures, `failed_urls` and `failed_path`; cancelled; with `bot_check`; with `archive_errors`);
- `operation = 'update'`: `set_busy(True)`, `title`, `progress` with `status='updating'`, `updated` (`unchanged` True and False), `update_error`, `update_done` (cancelled and not);
- `set_busy(True)` / `set_busy(False)` for each of `download`, `update`, `collect`.
After every step record: the text of `current_title`, `metrics`, `queue_stats`, `update_status`, `version_label`, `engine_badge`; text and state of `start_button`, `stop_button`, `retry_button`, `open_output_button`, `collect_start`, `collect_stop`; per row `[title text, detail text, badge text, state]`; `progress.target` (3 decimals) and `progress.cget('mode')`; `busy`, `animating`, `operation`. Normalise the engine version to `<VER>` and paths as above. The app is destroyed at the end; a test process never holds two `StudioApp` instances (SPEC §6).

`argv`, `events` and `runonce` must pass unchanged after WP1; `ui-1.5.1.json` must pass unchanged until WP4 step 2 is finished. WP2 adds `argv-1.6.0.json` / `events-1.6.0.json` and WP4 adds `ui-1.6.0.json`; do not design for that now beyond keeping each golden name a module constant.

## Acceptance (verifier)
1. `.venv\Scripts\python.exe -B verification\run_tests.py` exits 0 on the 1.5.1 sources (+ the one line); `--all` exits 0 on a machine with Chrome (report which tests were skipped otherwise).
2. During both runs nothing new appears in `verification/` and the real state dir is byte-for-byte untouched (the runner's own check, plus a manual `dir` before/after).
3. `git diff`-style comparison of `src/` against `verification/backup-before-1.6.0-20261115/src` shows only `engine.py` line 23, ignoring files that do not exist in the backup (WP3 adds `src/ui_theme.py` in parallel).
4. Running `python -B verification\test_ui.py` directly (no runner) also writes nothing outside a temp folder. A throwaway test file without `import _sandbox` that prints `os.environ['LOCALAPPDATA']` and `tempfile.gettempdir()` shows sandbox paths when run through the runner (delete it afterwards).
5. Flipping one flag in `build_command` locally makes `test_golden_argv.py` fail with a readable diff, and deleting one `elif` branch of `StudioApp.handle` makes `test_golden_ui.py` fail (try both, then revert).
6. `python -B verification\sandbox_app.py` opens the app with an empty history and creates nothing under `源码\data`, `源码\下载文件` or the real state dir.

## Handoff note must include
The runner command lines and timings, the list of tiers per file, the normalisation tokens used in the goldens, any test that had to change beyond the table above, the measured duration of `--all`, and the snapshot folder with the sha256 list (SPEC §7).
