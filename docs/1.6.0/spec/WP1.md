# WP1 - Core architecture (paths, procutil, applog, settings, updater, engine-side state bugs, single instance)

Read: SPEC.md §2, §3.1, §3.2, §3.7 (last paragraph), §3.12, §5; handoff WP0; audit arch-3, arch-9, arch-10, arch-11, robust-2, robust-5, robust-9, robust-10, robust-11, robust-12 (skeptic verdicts and hints).
Goal: the non-UI foundation. After this WP the 1.5.1 UI (`studio.py`, `collection_ui.py`, `library_ui.py`, untouched) still runs on top of the new modules and all four 1.5.1 goldens (`argv`, `events`, `runonce`, `ui`) are byte-identical.

Owns: new `src/paths.py`, `procutil.py`, `applog.py`, `settings.py`, `updater.py`; edits `src/engine.py`, `collector.py`, `library.py`, `app.py`; new `verification/test_core_paths_settings.py`, `test_core_procutil.py`, `test_core_updater.py`, `test_core_engine.py`.
Must not edit: any UI module, `progress.py`, existing tests, goldens, `run_tests.py`, `_sandbox.py`.
Rule: steps A-D are move-only (goldens identical after each). Steps E-I change behaviour, one topic per step. In `Runner.download` exactly two lines change in this WP: the logging line of step G and line 475 (step C).

## A. `paths.py` (new)
```python
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
VENDOR = RESOURCE_ROOT / 'vendor'; BIN = VENDOR / 'bin'
APP_DIR = <env YTDLP_DESKTOP_APP_DIR, else exe folder when frozen, else repo root>      # same expression as engine.py:23 after WP0
STATE_DIR = <env YTDLP_DESKTOP_STATE, else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YtDlpDesktop'>
def settings_file(), failed_file(), log_file(), engines_dir(), tmp_dir(), work_dir(), collect_dir(), cache_dir() -> Path   # STATE_DIR / 'settings.json', 'failed-downloads.json', 'download.log', 'engines', 'tmp', 'work', 'collect', 'cache'
def data_dir() -> Path            # APP_DIR / 'data'
def default_output() -> Path      # APP_DIR / '下载文件'
def catalog_path() -> Path
def atomic_write_text(path, text, encoding='utf-8') -> None
def purge_stale(alive) -> None
```
All functions read the module attributes at call time and create nothing, except `atomic_write_text` (creates the parent). `catalog_path()`: `p = data_dir() / 'library.sqlite3'`; return `p` if `p.is_file()` and `os.access(p, os.W_OK)` and `os.access(p.parent, os.W_OK)`; else `STATE_DIR / 'library.sqlite3'`. `atomic_write_text`: write `path.with_name(f'{path.name}.{os.getpid()}.tmp')`, `flush()`, `os.fsync()`, `os.replace`, and remove the temp file in a `finally` if it still exists. `purge_stale(alive)` (never raises; `alive` is `procutil.alive`): in `tmp_dir()` and `collect_dir()` remove folders named `run-<pid>-*` whose pid is not alive; in `work_dir()` delete files older than 7 days and files that are not `*.part` / `*.ytdl` older than 1 hour; in `engines_dir()` delete `yt-dlp.download*`.

## B. `procutil.py` (new)
```python
HIDDEN                                   # moved from engine.py:27
def process_environment(reset=True)      # engine.py:249-253 verbatim, reading paths.BIN; PYINSTALLER_RESET_ENVIRONMENT only when reset
def alive(pid) -> bool                   # library.py:57-78 verbatim
class JobObject:                         # never raises; .handle is None when unavailable (non-Windows, API failure -> one log line)
    def assign(self, process) -> bool    # AssignProcessToJobObject(handle, int(process._handle))
    def terminate(self) -> bool          # TerminateJobObject(handle, 1); the object stays usable
    def close(self) -> None
def terminate_tree(process, job=None) -> None
def single_instance(name=None)           # mutex handle, or None when another instance holds it; name: see below
def focus_window(title_suffix='YT-DLP 下载器', timeout=3.0) -> bool
def reveal_in_folder(path) -> bool
def flash_window(hwnd) -> None
def is_foreground(hwnd) -> bool
```
ctypes rules (64-bit handles): `kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)`; set `restype`/`argtypes` on every function; handles are `c_void_p`.
- Job: `CreateJobObjectW(None, None)`; `SetInformationJobObject(handle, 9, byref(info), sizeof(info))` with the full x64 `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` (`JOBOBJECT_BASIC_LIMIT_INFORMATION` = `LARGE_INTEGER, LARGE_INTEGER, DWORD LimitFlags, SIZE_T, SIZE_T, DWORD, ULONG_PTR, DWORD, DWORD`; `IO_COUNTERS` = six `ULONGLONG`; then four `SIZE_T`), `LimitFlags = 0x2000` (KILL_ON_JOB_CLOSE). One job per Runner / CollectionRunner, never a process-wide job (Explorer windows opened by `os.startfile` must survive).
- `terminate_tree(process, job=None)`: if `process` is None or already exited, return. If `job` and `job.terminate()`: `process.wait(2)` (ignore `TimeoutExpired`). If still running, run the 1.5.1 body (engine.py:272-283: `taskkill /PID /T /F`, timeout 10, fallback `process.kill()`) unchanged.
- `single_instance(name=None)`: `name = name or os.environ.get('YTDLP_DESKTOP_INSTANCE') or INSTANCE_NAME` with the module constant `INSTANCE_NAME = 'Local\\YtDlpDesktop.Instance'`; `CreateMutexW(None, False, name)`; if `ctypes.get_last_error() == 183` close the handle and return None. Non-Windows: return a dummy object. The environment override exists because the mutex is session-global: without it every sandboxed launch and R14 would meet the mutex of the owner's running 1.6.0 (the sandbox sets a unique name, WP0 Step 2).
- `focus_window`: `EnumWindows`; a match is visible, class name `TkTopLevel`, title ends with the suffix; `ShowWindow(hwnd, 9)` when `IsIconic`, then `SetForegroundWindow`; retry every 0.2 s until `timeout`.
- `reveal_in_folder`: missing file -> `os.startfile(parent)` and return False. Else `ole32.CoInitialize(None)`; `shell32.ILCreateFromPathW` (`restype=c_void_p`, `argtypes=[c_wchar_p]`); `SHOpenFolderAndSelectItems` (`argtypes=[c_void_p, c_uint, c_void_p, c_ulong]`); `ILFree` (`argtypes=[c_void_p]`); on any failure `subprocess.Popen('explorer /select,"<path>"')`.
- `flash_window`: `FLASHWINFO(cbSize, hwnd, dwFlags=0x2 | 0xC, uCount=0, dwTimeout=0)`, `user32.FlashWindowEx.argtypes = [POINTER(FLASHWINFO)]`. `is_foreground`: `user32.GetForegroundWindow() == hwnd`.

## C. Move engine, library and collector onto them (move-only)
- `engine.py`: delete lines 20-24, 27, 249-253 and the body of `_terminate_process`; add `import paths, procutil, updater` and compatibility names `RESOURCE_ROOT = paths.RESOURCE_ROOT; VENDOR = paths.VENDOR; BIN = paths.BIN; APP_DIR = paths.APP_DIR; STATE_DIR = paths.STATE_DIR; HIDDEN = procutil.HIDDEN; process_environment = procutil.process_environment; engine_path = updater.engine_path; engine_version = updater.engine_version`. Inside engine every use becomes a call-time read: `paths.failed_file()` in `store_failed_job`/`load_failed_job` (use `paths.atomic_write_text`), `paths.BIN` in `build_command`, `_run_once` and `diagnostics`, and inside the protected `finally` of `Runner.download` line 475 becomes `failed_path = str(paths.failed_file())` (the second sanctioned change in `download()`; the goldens normalise that path, so they stay identical). `Runner._terminate_process = staticmethod(procutil.terminate_tree)`.
- `updater.py`: `engine_path()` and `engine_version()` moved from engine.py:42-60, reading `paths.engines_dir()` / `paths.VENDOR` (cache added in step H).
- `library.py`: `import paths, procutil`; `CATALOG = paths.data_dir() / 'library.sqlite3'` (kept for importers); `alive = procutil.alive`; `write_json` delegates to `paths.atomic_write_text`.
- `collector.py`: imports from `paths` / `procutil`; `Runner._terminate_process(...)` -> `procutil.terminate_tree(...)`; `APP_DIR` -> `paths.APP_DIR` (temp location changes in step F).
Check: goldens identical; `_sandbox.state_patch` still isolates `test_batch_resilience.py` (it patches `paths.STATE_DIR` once `paths` is imported).

## D. `Runner.__init__(self, emit, library=None)` (move-only)
Initialise here what `download()` created lazily: `self.library = library`, `self.archive = None`, `self.archive_folder = None`, `self.archive_errors = []`, `self.last_error = ''`, `self.job = procutil.JobObject()`. `download()` keeps resetting `archive`/`archive_errors` at its top and keeps `getattr(self, 'library', None) or Library()`.

## E. `_run_once`: child environment, job object, `finally` (robust-5, robust-10, arch-3 addition b)
Exactly these changes; the `while` loop is not touched:
1. Inside the existing `with self.lock:`, after the unchanged `if self.cancelled.is_set(): return -1, False, None` (so a cancelled call creates no folder) and before `Popen`: `run_tmp = None`; try `paths.tmp_dir().mkdir(parents=True, exist_ok=True)` and `run_tmp = tempfile.mkdtemp(prefix=f'run-{os.getpid()}-', dir=paths.tmp_dir())` (on `OSError` keep None). `environment = process_environment()`; if `run_tmp`: `environment.update(TEMP=run_tmp, TMP=run_tmp)`. `Popen(..., env=environment, cwd=str(paths.BIN), ...)` inside `try/except BaseException:` that does `shutil.rmtree(run_tmp, ignore_errors=True)` when `run_tmp` and re-raises (a failed process start must not leak the folder). Right after `Popen`, still inside the lock: `self.job.assign(process)`.
2. Replace the `finally` body with: `if process.poll() is None: procutil.terminate_tree(process, self.job)`; `reader.join(2)`; `process.stdout.close()` inside `try/except (OSError, ValueError)`; the existing `with self.lock: if self.process is process: self.process = None`; `if run_tmp: shutil.rmtree(run_tmp, ignore_errors=True)`.
3. `cancel()`: `procutil.terminate_tree(process, self.job)`.
A job kill still leaves yt-dlp's own unpack folder inside `run_tmp`; the `rmtree` removes it (measured by the skeptic: 23.9 MB, 0.05 s).

## F. Collector (robust-5, arch-3 item 2)
- `CollectionRunner.__init__`: `self.job = procutil.JobObject()`; assign after `Popen`; `cancel()` and the loop use `procutil.terminate_tree(process, self.job)`; in the loop terminate once (guard with a local flag) instead of on every 60 ms tick.
- Work folder: `temp = paths.collect_dir() / f'run-{os.getpid()}-{uuid.uuid4().hex[:8]}'` (created, removed in `finally`) replaces `APP_DIR/build/collection-temp`; `TEMP`/`TMP` of the worker point there. Worker environment: `procutil.process_environment(reset=False)`.
- `ca_bundle()`: first candidate `paths.cache_dir() / 'certificates'` when `str(paths.cache_dir()).isascii()`; then the existing parent walk unchanged.
- `run()`'s `finally`: wrap lines 350-355 (history bookkeeping) in `try/except Exception` that logs with `applog.get('collector').exception(...)`; `self.emit('collect_done', ...)` must always execute.
- `worker_main` unchanged.

## G. `applog.py` (arch-11, logging half)
```python
NAME = 'YtDlpDesktop'
def setup() -> logging.Logger   # idempotent; inside its try: paths.STATE_DIR.mkdir(parents=True, exist_ok=True), then
                                # RotatingFileHandler(paths.log_file(), maxBytes=3*1024*1024, backupCount=2, encoding='utf-8'),
                                # format '%(asctime)s %(levelname).1s %(name)s %(message)s'; if that fails use NullHandler; never raises
def get(child='') -> logging.Logger          # 'YtDlpDesktop' or 'YtDlpDesktop.<child>'
def shutdown() -> None                       # close and remove handlers (tests)
def install_hooks() -> None                  # sys.excepthook and threading.excepthook -> get().error(..., exc_info=...)
```
`setup()` must create the state folder itself: `paths` functions create nothing, `RotatingFileHandler` raises `FileNotFoundError` on a missing folder (every sandboxed run, a new Windows profile), and the 1.5.1 `studio.py:71-76` skips its own handler when one exists - a `NullHandler` attached here would silence `download.log` for the whole session. With the folder created, the old UI keeps logging to the same file. Engine: in `Runner.download`'s `except Exception as error:` add `applog.get('engine').exception('download queue aborted')` before the existing lines; in `EngineUpdater.run`'s `except` likewise. Message texts emitted to the UI stay identical.

## H. `updater.py` (robust-12, arch-10)
```python
class UpdateError(Exception)                              # str() is a Chinese message for the user
def engine_path() -> Path; def engine_version() -> str; def invalidate() -> None
def check_latest(timeout=25) -> dict                      # {'tag', 'url', 'size', 'sha256'}
def cleanup() -> None                                     # delete engines/yt-dlp.download*
def restore_bundled() -> None                             # delete engines/yt-dlp.exe and yt-dlp.json, invalidate()
class EngineUpdater:
    def __init__(self, emit, cancelled, probe_args=None, legacy_events=False)
    def run(self) -> None
```
- Cache: `engine_path()` keeps `(key, result)` where key = `(st_size, st_mtime_ns)` of the updated exe and of its receipt; the SHA256 is recomputed only when the key changes; any `OSError` -> bundled exe. `engine_version()` uses the same cache.
- `EngineUpdater.run`: first move `Runner.update` (engine.py:483-527) verbatim, then apply: `import urllib.request` inside `run`; the release request and parsing live in `check_latest` (`expected = (asset.get('digest') or '').removeprefix('sha256:')`); download to `engines/yt-dlp.download.<pid>.<n>.exe` (`n` from a module-level `itertools.count()`; still matched by the `yt-dlp.download*` cleanup). A fixed temp name is not safe: after Stop the UI is released at once while the old thread may still sit in `urlopen`, and a second update would share the file. The `finally` unlinks only this run's own temp file and ignores `OSError`. After size and SHA256 match, if `probe_args`: run `[temp, *probe_args]` (`capture_output`, timeout 60, `cwd=paths.BIN`, `env=procutil.process_environment()`, `creationflags=HIDDEN`) and raise `UpdateError` when the combined output contains `no such option` (both good and bad flag sets exit 2; only the text differs - verified). `self.cancelled.is_set()` is checked in the download loop, again after the probe and immediately before `os.replace`; when set, return without installing (the UI has already said `已取消更新。`). Then `os.replace` to `yt-dlp.exe`, write the receipt with `paths.atomic_write_text`, `invalidate()`.
- Events: with `legacy_events=True` exactly the 1.5.1 kinds (`title`, `progress` with `status='updating'`); otherwise `update_status` (str) and `update_progress` (`fraction, downloaded, total`). `updated`, `update_error`, `update_done` unchanged in both modes.
- Messages (`update_error` value): network/timeout -> `无法连接 GitHub，请检查网络后重试。（<short reason>）`; no asset -> `官方发布中没有找到 yt-dlp.exe。`; no digest -> `官方发布未提供 SHA256，已保留现有引擎。`; size or hash mismatch -> `更新文件校验失败，已保留原来的引擎。`; probe -> `新版引擎与本程序使用的参数不兼容，已保留现有引擎。`.
- `Runner.update()` becomes: `updater.EngineUpdater(self.emit, self.cancelled, build_command('https://example.invalid/probe', Options(Path(tempfile.gettempdir())))[1:-2], legacy_events=True).run()`. WP2 replaces the third argument by `engine.probe_args()` (every preset and option block), which the 1.6.0 UI also uses.
- Never contact any host other than `api.github.com` and an asset URL starting `https://github.com/yt-dlp/yt-dlp/releases/download/`.

## I. `settings.py` (robust-9) and `library.open_library` (robust-6)
`settings.py` exactly as SPEC §3.2 (it imports `paths` only, so it validates the enums it owns, `naming` and `appearance`; `mode` and `resolution` are only type-checked as str here and membership-checked by `StudioApp.__init__`, WP4 §B): `DEFAULTS` built by a function (`output` depends on `paths.default_output()`, `archive_root` on the loaded `output`), `load()`, `save(values)` (returns False and logs on `OSError`; drops keys starting with `_` except that `_extra` contents are merged back at top level). Not wired into the old UI in this WP.
`library.py`: `Library.__init__(self, path=None)` -> `Path(path or paths.catalog_path())`; new module function `open_library(path=None) -> (Library, str)`: try `Library(path)`; on `(OSError, sqlite3.Error)` try `Library(Path(tempfile.mkdtemp(prefix='ytdlp-history-')) / 'library.sqlite3')` and return it with the warning `历史记录本次不保存：<error>`; the second failure propagates. (The old UI still passes its explicit path; WP5 switches it.)

## J. `app.py`
GUI path only (not `--collect-worker`, `--self-test`, `--smoke-url`): `procutil.single_instance()`; when it returns None call `procutil.focus_window()` - skipped when env `YTDLP_DESKTOP_INSTANCE` is set, because a sandboxed launch must never raise the owner's real window - and `return 0`; keep the handle in a module global. Then for every GUI start: `applog.setup()`, `applog.install_hooks()`, `updater.cleanup()`, and `threading.Thread(target=paths.purge_stale, args=(procutil.alive,), daemon=True).start()`. The rest of `main()` is unchanged (`report_callback_exception` also calls `applog.get('ui').error(...)`). A running 1.5.1 instance holds no mutex; both can then run - accepted.

## K. `engine.diagnostics()` (additive)
The result gains `app_dir = str(paths.APP_DIR)`, `state_dir = str(paths.STATE_DIR)` and `catalog = str(paths.catalog_path())`; nothing else changes. WP6's gate and install rehearsal use them to observe where the onedir build resolves its data.

## Tests to add (default tier, unittest, `import _sandbox` first)
- `test_core_paths_settings.py`: env overrides; every path function; `catalog_path` (app-dir file writable / missing / read-only -> state dir); `purge_stale` (dead-pid folders removed, live kept, age rules); R7: settings file containing `[]`, `null`, BOM + valid object, wrong types, truncated JSON -> defaults or values as specified, `settings.bad.json` created only for unreadable files; unknown keys round-trip; no `.tmp` left; `open_library` falls back when the path is a directory; `applog.setup()` on a state dir that does not exist yet creates it and attaches a real file handler (`applog.shutdown()` afterwards).
- `test_core_procutil.py`: R14 (`single_instance(name)` twice in one process with an explicit unique name `Local\YtDlpDesktop.Test.<uuid>`: second is None; with env `YTDLP_DESKTOP_INSTANCE` set and no argument the env name is used; app level: hold the sandbox mutex, run `verification\sandbox_app.py` in a subprocess with the same environment, expect exit code 0 within 10 s and no window; `focus_window('<unique title>')` finds a throwaway `tk.Tk()` window with that title); R15 (a Python child that spawns a sleeping grandchild; `JobObject.assign` + `terminate` -> both pids dead within 3 s; `close()` alone also kills); `terminate_tree` without a job; `alive`.
- `test_core_updater.py` (no network; `urllib.request.urlopen` patched with fake release JSON and bytes): cache does not re-hash an unchanged file; digest `None`, bad hash, probe rejection each give the message above and leave the old engine; success writes exe + receipt; cancel during download returns and leaves no temp file; cancel set by the probe stub (i.e. after the download) leaves the old engine in place and emits no `updated`; two `EngineUpdater` runs use different temp names and the first one's `finally` does not delete the second one's file; `legacy_events` mapping; `restore_bundled`.
- `test_core_engine.py`: R12 (`_run_once` with an `emit` that raises on the first line: the child pid is dead afterwards and `paths.tmp_dir()` has no `run-*` folder); the child sees the per-run `TEMP`; `Runner(emit, library=x).library is x`; every 1.5.1 engine name is still importable (`APP_NAME, APP_VERSION, RESOURCE_ROOT, VENDOR, BIN, APP_DIR, STATE_DIR, MODES, RESOLUTIONS, HIDDEN, engine_path, engine_version, parse_urls, read_url_file, Options, check_cookie_file, store_failed_job, load_failed_job, build_command, number, parse_line, process_environment, Runner, diagnostics` and the pattern constants); `CollectionRunner.run` still emits `collect_done` when the history bookkeeping raises; a `_run_once` call with `cancelled` already set, and one whose `Popen` raises `OSError`, leave no `run-*` folder; `diagnostics()` keys `app_dir`, `state_dir`, `catalog` (with `subprocess.run` patched) point into the sandbox.

## Acceptance (verifier)
1. `run_tests.py` and `run_tests.py --all` exit 0; the four 1.5.1 golden files (`argv`, `events`, `runonce`, `ui`) and their test files are unmodified (compare by sha256 with `docs\1.6.0\handoff\snapshot-WP0`).
2. `src/studio.py`, `collection_ui.py`, `library_ui.py`, `progress.py` are byte-identical to the backup.
3. Launching `python -B verification\sandbox_app.py --base <tmp>` twice opens one window; the second process exits 0 without a window (bringing the first window forward is skipped in a sandbox and covered by the `focus_window` unit test). The owner's installed app, if running, is not affected.
4. After a download smoke (`sandbox_app.py --keep --self-test R --smoke-url <fixture> --smoke-output <tmp>`) the sandbox `STATE\tmp` contains no `run-*` folder and `STATE\download.log` exists after a GUI start.
5. No file under `src/` contains `taskkill` except `procutil.py`.

## Handoff note must include
Final signatures of the five new modules, the exact changed line ranges in `engine.py`, anything in §3.1/§3.2/§3.12 implemented differently, the status of each test above, and the snapshot folder with the sha256 list (SPEC §7).
