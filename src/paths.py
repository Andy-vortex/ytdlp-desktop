"""Where everything lives (1.6.0). Standard library only; imports no app module.

RESOURCE_ROOT, VENDOR, BIN, APP_DIR and STATE_DIR are plain module attributes, so a
test (or a portable setup) can replace them.  Every function below reads them when it
is called.  The path functions leave nothing behind (catalog_path tries a write with a
file that deletes itself); the ones that write say so: own_dir, run_dir and
atomic_write_text create folders, set_aside renames, the purge functions delete.
Other modules therefore use ``paths.X`` / ``paths.x()`` at call time and never
``from paths import X``.

Environment overrides, read once at import:
  YTDLP_DESKTOP_APP_DIR   the application folder (data\\, 下载文件\\)
  YTDLP_DESKTOP_STATE     the state folder (settings, log, unfinished list, engines, temp)
An empty value counts as not set.  Both folders are made absolute here: child processes
start in another folder, where a relative path would point somewhere else.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time


def _env(name) -> str:
    """An environment value; an empty or blank one counts as not set."""
    return (os.environ.get(name) or '').strip()


RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
VENDOR = RESOURCE_ROOT / 'vendor'
BIN = VENDOR / 'bin'
STATE_NAME = 'YtDlpDesktop'
APP_DIR = (Path(os.path.abspath(_env('YTDLP_DESKTOP_APP_DIR'))) if _env('YTDLP_DESKTOP_APP_DIR')
           else Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1])
STATE_DIR = Path(os.path.abspath(_env('YTDLP_DESKTOP_STATE')
                                 or os.path.join(_env('LOCALAPPDATA') or os.path.expanduser('~'), STATE_NAME)))

RUN_FOLDER = re.compile(r'^run-(\d+)-')
# What an engine update downloads into: <pid>.<serial> (1.6.0 on; 2.0.0 S2 P4 dropped the fixed name of 1.5.1).
ENGINE_DOWNLOAD = re.compile(r'^yt-dlp\.download\.(\d+)\.\d+\.exe$')
# The one age rule of both work folders (STATE\work and APP_DIR\data\work, see clean_work):
# a resumable partial download, a finished format part of a stopped merge, the original before
# an audio extraction and every other file stay WORK_MAX_AGE, as long as a stopped entry is
# worth retrying; only true strays (ffmpeg's temporaries, cover pictures) go after WORK_STRAY_AGE
# (a day, so that a second instance never loses the cover of a long download it is running).
WORK_MAX_AGE = 7 * 24 * 3600
WORK_STRAY_AGE = 24 * 3600
WORK_STRAYS = ('*.temp', '*.temp.*', '*.tmp', '*.jpg', '*.jpeg', '*.webp', '*.png')
# What a save that was killed leaves in the state folder (atomic_write_text, the overflow file
# of unfinished.save) and in the engines folder (the receipt of an engine update): '<name>.<pid>.tmp'.
STATE_TEMP = re.compile(r'^(?:settings\.json|failed-downloads\.json)\.(\d+)(?:\.overflow)?\.tmp$', re.I)
ENGINE_TEMP = re.compile(r'^yt-dlp\.json\.(\d+)\.tmp$', re.I)
OWNED = '.ytdlp-desktop'       # left by own_dir in a folder the app created itself
REPLACE_ATTEMPTS = 6           # os.replace is repeated while another program has the target open
REPLACE_PAUSE = 0.05
_write_lock = threading.Lock()


def settings_file() -> Path:
    return STATE_DIR / 'settings.json'


def failed_file() -> Path:
    return STATE_DIR / 'failed-downloads.json'


def log_file() -> Path:
    return STATE_DIR / 'download.log'


def engines_dir() -> Path:
    return STATE_DIR / 'engines'


def tmp_dir() -> Path:
    return STATE_DIR / 'tmp'


def work_dir() -> Path:
    return STATE_DIR / 'work'


def collect_dir() -> Path:
    return STATE_DIR / 'collect'


def cache_dir() -> Path:
    return STATE_DIR / 'cache'


def data_dir() -> Path:
    return APP_DIR / 'data'


def default_output() -> Path:
    return APP_DIR / '下载文件'


def catalog_path() -> Path:
    """The history database: beside the app when that file exists and can be written, else in the state folder."""
    path = data_dir() / 'library.sqlite3'
    if path.is_file() and _writable(path):
        return path
    return STATE_DIR / 'library.sqlite3'


def _writable(path) -> bool:
    """Whether a database file can be changed: it opens for writing and its folder takes a
    new file (SQLite keeps its journal beside the database).

    Windows is asked by doing it.  os.access only knows the read-only attribute and says
    yes for every folder, also for one whose access rights refuse writing (a protected
    install folder).  The probe file deletes itself when it is closed; nothing stays behind.
    """
    try:
        with open(path, 'r+b'):
            pass
        probe = Path(path).with_name(f'{Path(path).name}.{os.getpid()}.{os.urandom(4).hex()}.probe')
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_TEMPORARY', 0))
        os.close(descriptor)
        if not hasattr(os, 'O_TEMPORARY'):
            os.unlink(probe)
        return True
    except OSError:
        return False


def own_dir(folder) -> Path:
    """Create one of the app's work folders (tmp, collect, work, engines) and mark it as its own.

    Only a folder that this call created gets the marker file; a folder that was already
    there is used as it is.  purge_stale cleans a work folder only when it carries the
    marker (or lies in a state folder with the app's own name), so a folder of that name
    which the app merely found, with somebody else's files, is never cleaned.
    Raises OSError when the folder cannot be created.
    """
    folder = Path(folder)
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        if not folder.is_dir():
            raise
        return folder
    try:
        (folder / OWNED).touch()
    except OSError:
        pass   # without the marker the folder is merely never cleaned
    return folder


def run_dir(parent) -> Path:
    """A new folder <parent>\\run-<pid>-<random>, the private TEMP of one child process.

    The parent is created with own_dir.  The result is absolute.  One attempt, OSError on
    failure: tempfile.mkdtemp tries name after name, practically without end, in a folder
    whose access rights refuse new entries.
    """
    folder = Path(os.path.abspath(own_dir(parent))) / f'run-{os.getpid()}-{os.urandom(4).hex()}'
    folder.mkdir()
    return folder


def read_text(path, encoding='utf-8') -> str:
    """Read a file that atomic_write_text maintains (settings, unfinished list, receipts).

    Readers and writers of this process take turns: Windows refuses to replace a file
    while any handle has it open, so a reader would make a concurrent save fail.
    """
    with _write_lock:
        return Path(path).read_text(encoding=encoding)


def read_bytes(path) -> bytes:
    """As read_text, undecoded."""
    with _write_lock:
        return Path(path).read_bytes()


def atomic_write_text(path, text, encoding='utf-8') -> None:
    """Write through a temp file in the same folder: flush, fsync, os.replace."""
    path = Path(path)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    # The temp name is per process, so writers of this process take turns.
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(temporary, 'w', encoding=encoding) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            _replace(temporary, path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass


def _replace(source, target) -> None:
    """os.replace, repeated for a moment while the target is open elsewhere (another
    version of the app reading it, a virus scanner): Windows answers that with
    PermissionError, and such a handle is usually gone a few milliseconds later."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_PAUSE)


def free_names(path, kind) -> list:
    """The names a file is set aside under, in order: <stem>.<kind>-<time><suffix>, then -2 … -99
    (failed-downloads.bad-<time>.json, library.bad-<time>.sqlite3)."""
    path = Path(path)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    stem, suffix = path.name.split('.', 1)[0], path.suffix
    return [path.with_name(f'{stem}.{kind}-{stamp}{suffix}')] + [
        path.with_name(f'{stem}.{kind}-{stamp}-{number}{suffix}') for number in range(2, 100)]


def set_aside(path, kind):
    """(2.0.0) Rename ``path`` to the first free name of free_names (never replaces a file: os.rename
    on Windows).  PermissionError (somebody is reading it this very moment) is retried REPLACE_ATTEMPTS
    times, then raised.  Returns the new path; None when every name is taken."""
    for target in free_names(path, kind):
        if os.path.lexists(target):
            continue
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.rename(path, target)
                return target
            except FileExistsError:
                break
            except PermissionError:
                if attempt + 1 >= REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_PAUSE)
    return None


def is_link(path) -> bool:
    """Is ``path`` a symbolic link, a junction or any other reparse point?  Never follows it.
    A path whose own entry cannot be read counts as one: the cleanups leave it alone."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(status.st_mode) or bool(getattr(status, 'st_file_attributes', 0)
                                                 & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _plain_file(path) -> bool:
    """A regular file that is no link or reparse point (lstat: the link itself is looked at)."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(status.st_mode) and not (getattr(status, 'st_file_attributes', 0)
                                                 & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _plain_dir(path) -> bool:
    """A real folder that is no link, junction or other reparse point."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(status.st_mode) and not (getattr(status, 'st_file_attributes', 0)
                                                 & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def purge_stale(alive) -> None:
    """Remove what earlier runs left behind; never raises.

    ``alive(pid) -> bool`` is procutil.alive.  Only folders named run-<pid>-* whose
    process is gone are removed from the temp and collect folders, so a second running
    instance never loses its own.  Nothing is touched in a work folder that is not the
    app's own (see own_dir), nor below a state folder that is relative or a drive root.
    Links, junctions and other reparse points are skipped at every level (the state
    folder, the four work folders, their entries and everything below a run folder):
    nothing is ever entered or deleted through one (core fix review-privacy-security-3).
    The work folder is cleaned by clean_work (one age rule with the work folder beside the
    program).  In the state folder itself only the temp files of saves that were killed are
    removed ('settings.json.<pid>.tmp', 'failed-downloads.json.<pid>.tmp' and
    '.<pid>.overflow.tmp'), plain files only and only when that process is gone.
    """
    try:
        if not _purge_allowed():
            return
        for folder in (tmp_dir(), collect_dir()):
            if not _owned(folder) or not _plain_dir(folder):
                continue
            for entry in _entries(folder):
                match = RUN_FOLDER.match(entry.name)
                try:
                    if match and _plain_dir(entry) and not alive(int(match.group(1))):
                        # A real folder only.  shutil.rmtree (Python 3.8 and later) removes a link or
                        # junction found inside as the link itself and never enters it.
                        shutil.rmtree(entry, ignore_errors=True)
                except Exception:
                    continue
        work = work_dir()
        if _owned(work):
            clean_work(work)
        _purge_temp_files(STATE_DIR, STATE_TEMP, alive)
        purge_engine_downloads(alive)
    except Exception:
        pass


def _purge_temp_files(folder, pattern, alive) -> None:
    """Delete the plain files directly in ``folder`` whose name matches ``pattern`` (group 1 is
    the pid of the process that wrote it) when that process is gone.  Never through a link,
    never a folder; a file of a running process (this one, a second instance) stays."""
    for entry in _entries(folder):
        try:
            match = pattern.match(entry.name)
            if match and _plain_file(entry) and not alive(int(match.group(1))):
                entry.unlink()
        except Exception:
            continue


def clean_work(folder, now=None) -> int:
    """Remove what stopped downloads left in a work folder of the app; never raises.

    The one age rule of both work folders (final-architecture-1): true strays (WORK_STRAYS:
    ffmpeg's temporaries, cover pictures) older than WORK_STRAY_AGE, every other plain file
    (a resumable *.part / *.ytdl / *.part-FragN, a finished format part 'Title [id].f137.mp4',
    the original before an audio extraction) older than WORK_MAX_AGE.  Only files directly in
    the folder (no recursion), never the marker file, never through a link or junction, never
    in a folder that is itself a link.  Whether the folder is the app's own is the caller's
    question (purge_stale: _owned; engine.clean_app_work: the marker).  Returns the number
    of files removed.
    """
    removed = 0
    try:
        if not _plain_dir(folder):
            return 0
        now = time.time() if now is None else now
        for entry in _entries(folder):
            try:
                name = entry.name.lower()
                if name == OWNED.lower() or not _plain_file(entry):
                    continue
                stray = any(fnmatch.fnmatchcase(name, pattern) for pattern in WORK_STRAYS)
                if now - os.lstat(entry).st_mtime <= (WORK_STRAY_AGE if stray else WORK_MAX_AGE):
                    continue
                entry.unlink()
                removed += 1
            except OSError:
                continue
    except Exception:
        pass
    return removed


def purge_engine_downloads(alive) -> None:
    """Delete what interrupted engine updates left in the engines folder; never raises.

    Only the names the updater itself uses: 'yt-dlp.download.<pid>.<n>.exe', only when that
    process is gone.  The file
    of an update that is running right now, here or in a second instance, is closed
    between its download and its installation and must not disappear then.  Likewise the
    temp file of the update receipt, 'yt-dlp.json.<pid>.tmp', when that process is gone
    (final-data-safety-5).  A linked engines folder or a linked entry is left alone.
    """
    try:
        if not _purge_allowed() or not _owned(engines_dir()) or not _plain_dir(engines_dir()):
            return
        for entry in _entries(engines_dir()):
            try:
                match = ENGINE_DOWNLOAD.match(entry.name) or ENGINE_TEMP.match(entry.name)
                if not match or not _plain_file(entry):
                    continue
                if alive(int(match.group(1))):
                    continue
                entry.unlink()
            except Exception:
                continue
    except Exception:
        pass


def _purge_allowed() -> bool:
    """Nothing is ever cleaned below a relative state folder, directly below a drive or share
    root, or below a state folder that is itself a link or junction."""
    state = Path(STATE_DIR)
    return state.is_absolute() and state.parent != state and _plain_dir(state)


def _owned(folder) -> bool:
    """May this work folder be cleaned?  Yes when the app created it (own_dir left its
    marker) or when the state folder has the app's own name; a tmp, work, collect or
    engines folder that was simply found below some other state folder may be anybody's."""
    return Path(STATE_DIR).name.casefold() == STATE_NAME.casefold() or (Path(folder) / OWNED).is_file()


def _entries(folder) -> list:
    try:
        return list(Path(folder).iterdir())
    except OSError:
        return []
