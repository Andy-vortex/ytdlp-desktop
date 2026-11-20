"""Install the staged program into the app folder, or roll an installation back (1.6.0 WP6 §E).

    python -B install_release.py --stage <staged folder> --target <app folder>
                                 [--dry-run] [--shortcut] [--rollback] [--old-version V] [--instance-name NAME]

Install (every step refuses with a message and changes nothing until step 4):
  1. refuse while the app runs: the single-instance mutex (--instance-name, default Local\\YtDlpDesktop.Instance)
     exists (1.6.0), or <target>\\YT-DLP下载器.exe cannot be opened for writing (a running 1.5.1);
  2. require build\\verify\\release-<version>.json beside the stage whose exe sha256 equals the staged exe and whose
     "file_list" (every staged file: path, size, SHA-256, bound by its own hash) matches the stage file by file: a
     file changed (also at the same size), added or deleted after the gate refuses the install; a record without
     the list (written by an older verify_release.py) is refused too;
  3. copy _internal to <target>\\_internal.new and the exe to <target>\\YT-DLP下载器.new.exe; compare count and size,
     then every copied file against the record's list;
  4. move the previous program aside by its layout:
       onedir  (<target>\\_internal exists): exe + _internal (+ its install-receipt.json) into <target>\\_previous\\;
               an older _previous is first renamed to _previous.old-<stamp> and deleted only after the new receipt
               is in place; no "...备份.exe" is made.  The receipt kept in _previous is chained past the discarded
               older _previous, so repeated rollbacks go: this install -> the previous install -> the one-file program;
       onefile (exe without _internal, i.e. 1.5.1): rename the exe to YT-DLP下载器-<version>备份.exe
               (<version> = --old-version, else the old program's own --self-test, else 旧版; a timestamp is
               appended when the name exists); the receipt records the backup's sha256;
       none    (no exe): nothing;
     an active install-receipt.json that cannot be kept is renamed to install-receipt.superseded-<stamp>.json,
     never overwritten;
  5. rename _internal.new -> _internal, the .new.exe -> YT-DLP下载器.exe and the receipt written beforehand
     (install-receipt.pending.json, "checked": false) -> install-receipt.json; that last rename is the commit point;
  6. post-install gate from the installed path (--self-test with app_dir = target, fixture download smoke, GUI
     smoke), then the receipt is rewritten with "checked": true.  From the commit until then the new program is
     active but unchecked: any failure or Ctrl+C in that window (also during the clean-up of step 5) runs the
     rollback automatically, with Ctrl+C held back until it is done.  A run that is killed in that window leaves
     "checked": false behind; the next install first rolls that unchecked install back, and --rollback says so;
  7. --shortcut: Desktop "YT-DLP 下载器.lnk" when none exists (off by default);
  8. print what remains for the owner.
Rollback (--rollback, also the automatic path) acts only on install-receipt.json, whose names must be exactly the
ones install writes: the current exe + _internal go together into <target>\\_rolledback-<installed version>\\, the
previous program comes back as the receipt says, and the receipt is renamed to install-receipt.rolledback.json (a
receipt that the install moved into _previous comes back, so a second rollback can follow).
Journal: steps 4-5 and the rollback are one sequence of renames listed in <target>\\install-journal.json before the
first one and recorded after each.  A failure undoes the renames done so far.  A run that stops half way (killed,
power loss) leaves the journal behind; the next start of install or rollback first undoes those renames (or, when
all of them were done, only finishes the clean-up) and then carries on.
Nothing else in the target is touched: not data\\, not 下载文件\\, not 源码\\, not another backup exe.
Standard library only.  The gate helpers come from verification\\verify_release.py beside this script.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
APP_NAME = 'YT-DLP下载器'
EXE_NAME = APP_NAME + '.exe'
INSTANCE_NAME = 'Local\\YtDlpDesktop.Instance'
INTERNAL = '_internal'
NEW_INTERNAL = '_internal.new'
NEW_EXE = APP_NAME + '.new.exe'
PREVIOUS = '_previous'
RECEIPT = 'install-receipt.json'
ROLLED_BACK_RECEIPT = 'install-receipt.rolledback.json'
PENDING_RECEIPT = 'install-receipt.pending.json'
KEEP_RECEIPT = 'install-receipt.keep.json'
JOURNAL = 'install-journal.json'
SHORTCUT_NAME = 'YT-DLP 下载器.lnk'
RUNNING_INSTALL = '请先关闭 YT-DLP 下载器再安装。'
RUNNING_ROLLBACK = '请先关闭 YT-DLP 下载器再回滚。'
OLD_VERSION_UNKNOWN = '旧版'
OLD_SELF_TEST_TIMEOUT = 300
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+')
BACKUP_PATTERN = re.compile(re.escape(APP_NAME) + r'-(?:[0-9A-Za-z.\-]{1,20}|旧版)备份(?:-\d{8}-\d{6})?\.exe')
# First part of every path a journal may name: only the entries this script itself creates or moves.
OWNED_PATTERN = re.compile('|'.join([
    re.escape(EXE_NAME), re.escape(NEW_EXE), r'_internal(?:\.new)?', r'_previous(?:\.old-[0-9-]{1,40})?',
    r'_rolledback-\d+\.\d+\.\d+(?:\.old-[0-9-]{1,40})?', r'install-receipt(?:\.[a-z]+(?:-[0-9-]{1,40})?)?\.json',
    BACKUP_PATTERN.pattern]))
# Trash names (set aside by a run and deleted after its commit) and temporary files of write_json_atomic; such
# leftovers of an earlier, interrupted run are deleted too.
STALE_PATTERN = re.compile(r'_previous\.old-[0-9-]{1,40}|_rolledback-\d+\.\d+\.\d+\.old-[0-9-]{1,40}'
                           r'|install-receipt\.replaced-[0-9-]{1,40}\.json'
                           r'|install-(?:journal|receipt(?:\.[a-z]+)?)\.json\.\d+\.tmp')


class InstallError(Exception):
    """Refusal or failure; str() is the message for the owner."""


def say(text: str) -> None:
    print(text, flush=True)


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def stamp() -> str:
    return f'{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}'


def short(error: BaseException) -> str:
    text = str(error) or type(error).__name__
    return text if len(text) <= 300 else text[:300] + '…'


class HeldInterrupt:
    """Ctrl+C is noted, not acted on, between start() and stop(): for steps that must not stop half way (the
    renames up to the commit, the clean-up after it, an automatic rollback).  stop() restores the previous
    handler; raise_if_pressed() then raises the KeyboardInterrupt that was held back.  Outside the main thread
    (no signal handler can be set) nothing is held."""

    def __init__(self):
        self.pressed = False
        self._previous = None
        self._active = False

    def _note(self, _signal, _frame):
        self.pressed = True

    def start(self) -> 'HeldInterrupt':
        if not self._active:
            try:
                self._previous = signal.signal(signal.SIGINT, self._note)
                self._active = True
            except (ValueError, OSError):
                pass
        return self

    def stop(self) -> None:
        if self._active:
            self._active = False
            try:
                signal.signal(signal.SIGINT, self._previous)
            except (ValueError, OSError, TypeError):
                pass

    def raise_if_pressed(self) -> None:
        if self.pressed:
            self.pressed = False
            raise KeyboardInterrupt

    def __enter__(self) -> 'HeldInterrupt':
        return self.start()

    def __exit__(self, *_exc) -> bool:
        self.stop()
        return False


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def folder_stats(folder) -> tuple:
    count = size = 0
    for current, _folders, files in os.walk(folder):
        for name in files:
            count += 1
            size += os.path.getsize(os.path.join(current, name))
    return count, size


def load_gate():
    """verification/verify_release.py beside this script: the self-test, download and GUI smoke helpers."""
    path = ROOT / 'verification' / 'verify_release.py'
    if not path.is_file():
        raise InstallError(f'缺少 {path}（安装后自检需要它）。')
    spec = importlib.util.spec_from_file_location('verify_release_for_install', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ running?

def mutex_exists(name: str) -> bool:
    """True when the named mutex exists (a 1.6.0 instance holds it)."""
    if os.name != 'nt':
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenMutexW(0x00100000, False, name)       # SYNCHRONIZE
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5                         # ERROR_ACCESS_DENIED: it exists


def exe_in_use(path: Path) -> bool:
    """True when the exe cannot be opened for writing (Windows locks the image of a running program)."""
    if not path.is_file():
        return False
    try:
        with open(path, 'r+b'):
            pass
    except PermissionError:
        return True
    return False


def refuse_if_running(target: Path, instance_name: str, message: str) -> None:
    if mutex_exists(instance_name) or exe_in_use(target / EXE_NAME):
        raise InstallError(message)


# ------------------------------------------------------------------ small file helpers

def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(os.lstat(path), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False


def remove_tree(path: Path) -> None:
    """Delete a folder this script owns (stale .new / _previous.old-* / _rolledback-*.old-*)."""
    if not os.path.lexists(path):
        return
    if is_link(path):
        raise InstallError(f'拒绝删除链接或联接：{path}')

    def writable(function, name, _info):
        try:
            os.chmod(name, 0o666)
            function(name)
        except OSError:
            pass

    for _ in range(5):
        shutil.rmtree(path, onerror=writable)
        if not path.exists():
            return
        time.sleep(0.5)
    raise InstallError(f'无法删除 {path}（是否有文件被占用？）')


def remove_path(path: Path) -> None:
    """Delete a file or folder this script owns; InstallError/OSError when it cannot."""
    if not os.path.lexists(path):
        return
    if path.is_dir() or is_link(path):
        remove_tree(path)
    else:
        try:
            os.unlink(path)
        except PermissionError:
            os.chmod(path, 0o666)
            os.unlink(path)


def discard(target: Path, names) -> list:
    """Delete entries this script owns; returns the names that could not be deleted (never raises OSError)."""
    left = []
    for relative in names:
        try:
            remove_path(target / relative)
        except (InstallError, OSError):
            left.append(relative)
    return left


def inside_target(target: Path, relative: str) -> Path:
    """A path named in a receipt or journal, which must lie inside the target."""
    path = (target / relative).resolve()
    if not path.is_relative_to(target.resolve()) or path == target.resolve():
        raise InstallError(f'{relative} 不在程序目录内（是否为链接或联接？）')
    return path


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        with open(temporary, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def read_json(path: Path):
    """The JSON value in the file, or None when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ receipts

def receipt_problem(receipt) -> str | None:
    """None when the receipt has exactly the shape install() writes; otherwise what is wrong with it."""
    if not isinstance(receipt, dict):
        return '不是 JSON 对象'
    installed, previous = receipt.get('installed'), receipt.get('previous')
    if not isinstance(installed, str) or not VERSION_PATTERN.fullmatch(installed):
        return f'installed 无效：{installed!r}'
    if not isinstance(previous, dict):
        return 'previous 不是对象'
    if 'checked' in receipt and not isinstance(receipt['checked'], bool):
        return f'checked 无效：{receipt["checked"]!r}'
    kind = previous.get('layout')
    exe, internal, kept = previous.get('exe'), previous.get('internal'), previous.get('receipt')
    if previous.get('version') is not None and not isinstance(previous.get('version'), str):
        return 'previous.version 无效'
    if kind == 'onefile':
        if not isinstance(exe, str) or not BACKUP_PATTERN.fullmatch(exe):
            return f'备份名无效：{exe!r}'
        if internal is not None or kept is not None:
            return '单文件旧程序不应有 internal / receipt'
        digest = previous.get('exe_sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return '缺少备份文件的 SHA256'
    elif kind == 'onedir':
        if (exe != f'{PREVIOUS}\\{EXE_NAME}' or internal != f'{PREVIOUS}\\{INTERNAL}'
                or kept not in (None, f'{PREVIOUS}\\{RECEIPT}')):
            return f'文件夹版旧程序的路径无效：{exe!r}、{internal!r}、{kept!r}'
    elif kind == 'none':
        if exe is not None or internal is not None or kept is not None:
            return '无旧程序时不应有文件名'
    else:
        return f'layout 无效：{kind!r}'
    return None


def backups_to_name(target: Path, receipt=None) -> str:
    """The one-file backup(s) a message may tell the owner to double-click, joined by '、'; '' when there is none.

    The backup the receipt chain names comes first (a one-file previous program, or for a folder install the one
    named by the receipt kept in _previous), but only when that file exists.  Otherwise every file in the target
    whose name has the backup form is named; the name is never assumed (it can be 旧版 or carry a timestamp)."""
    named = None
    if receipt_problem(receipt) is None:
        previous = receipt['previous']
        if previous['layout'] == 'onedir' and previous.get('receipt'):
            kept = read_json(target / previous['receipt'])
            previous = kept['previous'] if receipt_problem(kept) is None else {}
        if previous.get('layout') == 'onefile':
            named = previous['exe']
    try:
        if named and (target / named).is_file():
            return named
        found = sorted(entry.name for entry in os.scandir(target)
                       if BACKUP_PATTERN.fullmatch(entry.name) and entry.is_file())
    except OSError:
        return ''
    return '、'.join(found)


def chained_receipt(target: Path, current):
    """The current receipt as it must read once its program sits in _previous.

    Its own 'previous' may point into the older _previous that this install discards; then it takes over the
    'previous' of the receipt kept there (a one-file backup or nothing), so that the rollback chain stays
    intact.  Returns the receipt unchanged when nothing points into the older _previous, a new dict when it was
    chained, and None when it cannot be kept usefully (invalid, or the older receipt is unusable)."""
    if receipt_problem(current) is not None:
        return None
    if current['previous']['layout'] != 'onedir':
        return current
    older = read_json(target / PREVIOUS / RECEIPT)
    if receipt_problem(older) is not None or older['previous']['layout'] == 'onedir':
        return None
    return {**current, 'previous': older['previous']}


def unchecked_install(target: Path) -> str | None:
    """The version of the active install when its post-install gate never finished ("checked": false: the run
    was killed between the commit and the end of the gate), else None.  Receipts without the key (written
    before it existed, or kept in _previous after their own gate passed) count as checked."""
    receipt = read_json(target / RECEIPT)
    if receipt_problem(receipt) is None and receipt.get('checked') is False:
        return receipt['installed']
    return None


def mark_checked(target: Path, receipt: dict) -> None:
    """The gate passed: the active receipt says so from now on."""
    write_json_atomic(target / RECEIPT, {**receipt, 'checked': True})


# ------------------------------------------------------------------ journalled renames

def move_step(source: str, destination: str) -> dict:
    return {'op': 'move', 'from': source, 'to': destination}


def mkdir_step(path: str) -> dict:
    return {'op': 'mkdir', 'path': path}


def owned_path(target: Path, relative) -> Path:
    """A name from a journal: relative, inside the target, and its first part a name this script writes."""
    if not isinstance(relative, str) or not relative or '/' in relative:
        raise InstallError(f'安装日志中的路径无效：{relative!r}')
    parts = relative.split('\\')
    if not OWNED_PATTERN.fullmatch(parts[0]) or any(part in ('', '.', '..') or ':' in part for part in parts):
        raise InstallError(f'安装日志中的路径不是本脚本管理的文件：{relative!r}')
    return inside_target(target, relative)


def check_journal(target: Path, data) -> None:
    ok = (isinstance(data, dict) and data.get('format') == 1 and data.get('operation') in ('install', 'rollback')
          and data.get('direction') in ('forward', 'back') and isinstance(data.get('steps'), list)
          and isinstance(data.get('done'), int) and not isinstance(data.get('done'), bool)
          and 0 <= data['done'] <= len(data['steps'])
          and isinstance(data.get('temporaries'), list) and isinstance(data.get('trash'), list))
    if not ok:
        raise InstallError(f'{JOURNAL} 内容无效')
    for step in data['steps']:
        if not isinstance(step, dict) or step.get('op') not in ('move', 'mkdir'):
            raise InstallError(f'{JOURNAL} 中有无效步骤：{step!r}')
        for key in (('path',) if step['op'] == 'mkdir' else ('from', 'to')):
            owned_path(target, step.get(key))
    for relative in data['temporaries'] + data['trash']:
        owned_path(target, relative)


def step_state(target: Path, step: dict, direction: str) -> str:
    """'done' (the step is in effect), 'pending' (it is not) or 'unclear', judged by what exists now.

    A rename never overwrites: when both names exist, the rename (forward) or its undo (back) did not happen."""
    if step['op'] == 'mkdir':
        return 'done' if (target / step['path']).is_dir() else 'pending'
    source = os.path.lexists(target / step['from'])
    destination = os.path.lexists(target / step['to'])
    if direction == 'forward':
        if source:
            return 'pending'
        return 'done' if destination else 'unclear'
    if destination:
        return 'done'
    return 'pending' if source else 'unclear'


def apply_step(target: Path, step: dict) -> None:
    if step['op'] == 'mkdir':
        os.mkdir(target / step['path'])
        return
    source, destination = target / step['from'], target / step['to']
    if os.path.lexists(destination):
        raise InstallError(f'目标已存在，未覆盖：{destination}')
    os.rename(source, destination)


def undo_step(target: Path, step: dict) -> None:
    if step['op'] == 'mkdir':
        os.rmdir(target / step['path'])         # only an empty folder: anything else inside stops the undo
        return
    source, destination = target / step['from'], target / step['to']
    if os.path.lexists(source):
        raise InstallError(f'无法把 {step["to"]} 改回 {step["from"]}：后者已存在')
    os.rename(destination, source)


def save_journal(target: Path, data: dict) -> None:
    write_json_atomic(target / JOURNAL, data)


def reverse(target: Path, data: dict, recovering: bool) -> list:
    """Undo the renames in effect, last first, recording progress in the journal.  Returns the problems; when
    there are none, the temporaries and the journal are deleted as well.  The step at the journal's boundary may
    or may not have happened (a rename can finish just before an interrupt), so the files decide.  recovering:
    the journal comes from an earlier run; a boundary step whose names are both gone stops the recovery (in this
    run such a step simply failed)."""
    steps, done = data['steps'], data['done']
    if data['direction'] == 'forward' and done < len(steps):
        state = step_state(target, steps[done], 'forward')
        if state == 'unclear' and recovering:
            return [f'第 {done + 1} 步（{steps[done]}）的两个名字都不存在']
        done += state == 'done'
    elif data['direction'] == 'back' and done > 0:
        state = step_state(target, steps[done - 1], 'back')
        if state == 'unclear':
            return [f'第 {done} 步（{steps[done - 1]}）的两个名字都不存在']
        done -= state == 'pending'
    data['direction'], data['done'] = 'back', done
    try:
        save_journal(target, data)
        while data['done'] > 0:
            undo_step(target, steps[data['done'] - 1])
            data['done'] -= 1
            save_journal(target, data)
    except (OSError, InstallError) as error:
        return [short(error)]
    left = discard(target, data['temporaries'])
    if left:
        say('未能删除：' + '、'.join(left) + '（下次安装会重新处理）。')
    try:
        os.unlink(target / JOURNAL)
    except OSError as error:
        return [f'{JOURNAL} 无法删除：{short(error)}']
    return []


def finish(target: Path, data: dict) -> list:
    """All renames are in effect: delete the trash (older _previous etc., also such leftovers of an earlier run)
    and the journal.  Returns what could not be deleted (a held file, for example); the operation itself is
    complete either way."""
    try:
        stale = [entry for entry in os.listdir(target)
                 if STALE_PATTERN.fullmatch(entry) and entry not in data['trash']]
    except OSError:
        stale = []
    left = discard(target, data['trash'] + data['temporaries'] + stale)
    previous = target / PREVIOUS
    if previous.is_dir() and not is_link(previous):
        try:
            previous.rmdir()            # only when empty (after a rollback to a previous folder build)
        except OSError:
            pass
    try:
        os.unlink(target / JOURNAL)
    except OSError:
        pass            # harmless: the next start sees every step done and only repeats this clean-up
    return left


def run_steps(target: Path, operation: str, steps: list, files: dict | None = None, temporaries=(),
              trash=()) -> dict:
    """Do the renames under a journal.  On any failure (or Ctrl+C) the renames done so far are undone and
    InstallError is raised.  files: JSON files written after the journal and before the first rename (their
    names belong to the temporaries).  Returns the journal data once every rename is done (the journal file
    is still there; finish() deletes the trash, the temporaries and the journal)."""
    files = files or {}
    data = {'format': 1, 'operation': operation, 'time': now(), 'pid': os.getpid(), 'direction': 'forward',
            'done': 0, 'steps': steps, 'temporaries': list(temporaries) + list(files), 'trash': list(trash)}
    try:
        check_journal(target, data)    # the plan itself must pass the checks a later recovery applies
    except InstallError as error:
        raise InstallError(f'{error}；未做任何更改。') from None
    if os.path.lexists(target / JOURNAL):
        raise InstallError(f'{JOURNAL} 已存在；未做任何更改。')
    try:
        save_journal(target, data)
        for relative, content in files.items():
            write_json_atomic(target / relative, content)
        for index, step in enumerate(steps):
            apply_step(target, step)
            data['done'] = index + 1
            save_journal(target, data)
    except BaseException as error:
        if os.path.lexists(target / JOURNAL):
            problems = reverse(target, data, recovering=False)
        else:                               # the journal was never written: nothing was renamed
            discard(target, list(files))
            problems = []
        if isinstance(error, KeyboardInterrupt):
            reason = '被中断（Ctrl+C）'
        elif isinstance(error, InstallError):
            reason = str(error)
        elif isinstance(error, OSError):
            reason = f'移动文件失败：{short(error)}'
        else:
            reason = f'{type(error).__name__}: {short(error)}'
        if problems:
            detail = ('；恢复时出现问题：' + '；'.join(problems) + f'。{JOURNAL} 已保留，下次运行本脚本时会继续恢复，'
                      '在此之前请不要手动移动程序文件')
        else:
            detail = '，已恢复原状'
        raise InstallError(f'{reason}{detail}。') from None
    return data


def recover(target: Path, dry_run: bool) -> None:
    """An install or rollback that stopped half way left its journal: undo it, or finish its clean-up when every
    rename was done.  Refuses (InstallError) when the files do not match the journal."""
    if not os.path.lexists(target / JOURNAL):
        return
    data = read_json(target / JOURNAL)
    try:
        check_journal(target, data)
    except InstallError as error:
        raise InstallError(f'发现无法使用的安装日志 {JOURNAL}（{error}）；未做任何更改，请先人工检查程序目录。')
    what = '安装' if data['operation'] == 'install' else '回滚'
    steps, done = data['steps'], data['done']
    complete = data['direction'] == 'forward' and (
        done == len(steps) or (done == len(steps) - 1 and step_state(target, steps[-1], 'forward') == 'done'))
    if dry_run:
        say(f'发现上次中断的{what}（{JOURNAL}）；实际运行时会先'
            + ('完成它剩下的清理。' if complete else '撤销它已做的改动，使程序目录回到它开始之前的状态。'))
        return
    if complete:
        data['done'] = len(steps)
        left = finish(target, data)
        say(f'上次的{what}已做完全部改名，只剩清理；已清理。'
            + (f'未能删除：{"、".join(left)}，可以稍后手动删除。' if left else ''))
        return
    say(f'发现上次中断的{what}（{JOURNAL}），正在撤销它已做的改动…')
    problems = reverse(target, data, recovering=True)
    if problems:
        raise InstallError(f'无法自动恢复上次中断的{what}：' + '；'.join(problems) + f'。{JOURNAL} 已保留；'
                           '请先人工检查程序目录。')
    say(f'已撤销上次中断的{what}，程序目录回到了它开始之前的状态。')


# ------------------------------------------------------------------ install

def stage_version(stage: Path) -> str:
    match = re.fullmatch(r'package-(\d+\.\d+\.\d+)', stage.parent.name)
    if not match:
        raise InstallError(f'暂存目录应位于 build\\package-<版本>\\{APP_NAME}：{stage}')
    return match.group(1)


def file_entries(folder: Path, prefix: str = '') -> list:
    """[{path, size, sha256}] of every file below ``folder``; path is ``prefix`` + the '/'-separated relative path
    (the form of the release record's file list)."""
    found = []
    for current, _folders, files in os.walk(folder):
        relative = Path(current).relative_to(folder)
        for name in files:
            path = Path(current) / name
            found.append({'path': prefix + (relative / name).as_posix(), 'size': path.stat().st_size,
                          'sha256': file_sha256(path)})
    return found


def list_sha256(files: list) -> str:
    """Same definition as verify_release.file_list_sha256 (the hash that binds the list inside the record)."""
    text = json.dumps([[item['path'], item['size'], item['sha256']] for item in files], ensure_ascii=False,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def recorded_files(release: dict, release_path: Path) -> list:
    """The release record's list of every staged file, checked for consistency; InstallError otherwise."""
    entry = release.get('file_list')
    if not isinstance(entry, dict) or not isinstance(entry.get('files'), list):
        raise InstallError(f'发布验证记录 {release_path} 没有逐个文件的清单（它由旧版 verify_release.py 写入）；'
                           '请重新运行 build.py。未做任何更改。')
    files = entry['files']
    try:
        well_formed = all(isinstance(item['path'], str) and isinstance(item['size'], int)
                          and re.fullmatch(r'[0-9a-f]{64}', item['sha256']) for item in files)
        bound = (well_formed and entry.get('sha256') == list_sha256(files) and entry.get('count') == len(files)
                 and entry.get('size') == sum(item['size'] for item in files)
                 and len({item['path'] for item in files}) == len(files))
    except (KeyError, TypeError):
        bound = False
    if not bound or not files:
        raise InstallError(f'发布验证记录 {release_path} 中的文件清单不完整或已被改动；请重新运行 build.py。未做任何更改。')
    exe_entry = next((item for item in files if item['path'] == EXE_NAME), None)
    if exe_entry is None or exe_entry['sha256'] != (release.get('exe') or {}).get('sha256'):
        raise InstallError(f'发布验证记录 {release_path} 中的文件清单与其中的程序 SHA256 不一致；请重新运行 build.py。'
                           '未做任何更改。')
    return files


def list_differences(expected: list, actual: list) -> str:
    """'' when both lists are equal, else a short Chinese description of the added, missing and changed files."""
    before = {item['path']: (item['size'], item['sha256']) for item in expected}
    after = {item['path']: (item['size'], item['sha256']) for item in actual}
    parts = []
    for label, paths in (('多出', sorted(set(after) - set(before))), ('缺少', sorted(set(before) - set(after))),
                         ('内容不同', sorted(path for path in set(before) & set(after) if before[path] != after[path]))):
        if paths:
            shown = '、'.join(path.replace('/', '\\') for path in paths[:5]) + ('…' if len(paths) > 5 else '')
            parts.append(f'{label} {len(paths)} 个文件（{shown}）')
    return '；'.join(parts)


def check_stage(stage: Path) -> dict:
    exe = stage / EXE_NAME
    if not exe.is_file() or not (stage / INTERNAL).is_dir():
        raise InstallError(f'暂存目录不完整（需要 {EXE_NAME} 和 {INTERNAL}）：{stage}')
    version = stage_version(stage)
    release_path = stage.parents[1] / 'verify' / f'release-{version}.json'
    if not release_path.is_file():
        raise InstallError(f'缺少发布验证记录 {release_path}；请先运行 build.py 并通过验证。')
    try:
        release = json.loads(release_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise InstallError(f'发布验证记录无法读取：{release_path}（{error}）')
    exe_hash = file_sha256(exe)
    if release.get('status') != 'passed' or release.get('version') != version:
        raise InstallError(f'发布验证记录不是 {version} 的通过记录：{release_path}')
    if (release.get('exe') or {}).get('sha256') != exe_hash:
        raise InstallError('暂存的程序与发布验证记录不符（SHA256 不同）；请重新运行 build.py。')
    expected = recorded_files(release, release_path)
    say(f'核对暂存目录的全部 {len(expected)} 个文件（大小和 SHA256）…')
    try:
        actual = file_entries(stage)
    except OSError as error:
        raise InstallError(f'暂存目录无法完整读取（{short(error)}）；未做任何更改。') from None
    differences = list_differences(expected, actual)
    if differences:
        raise InstallError(f'暂存目录与发布验证记录不符：{differences}。发布验证之后暂存目录被改动过；'
                           '请重新运行 build.py。未做任何更改。')
    count, size = len(expected), sum(item['size'] for item in expected)
    return {'version': version, 'exe_sha256': exe_hash, 'files': count, 'size': size, 'release': str(release_path),
            'file_list': expected}


def layout_of(target: Path) -> str:
    exe, internal = target / EXE_NAME, target / INTERNAL
    if internal.exists():
        if not exe.is_file():
            raise InstallError(f'程序目录里有 {INTERNAL} 但没有 {EXE_NAME}，布局不完整；请先人工检查：{target}')
        return 'onedir'
    if exe.is_file():
        return 'onefile'
    return 'none'


def program_version(exe: Path, gate) -> str | None:
    """The app_version the program reports in its own --self-test (state and temp redirected), or None."""
    work = Path(tempfile.mkdtemp(prefix='ytdlp-old-version-'))
    try:
        report = work / 'old.json'
        environment = gate.sandbox_environment(work / 'run', True)
        process = subprocess.Popen([str(exe), '--self-test', str(report)], env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        try:
            process.wait(OLD_SELF_TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            gate.end_process_tree(process)
            return None
        data = json.loads(report.read_text(encoding='utf-8'))
        version = str(data.get('app_version') or '').strip()
        return version if re.fullmatch(r'[0-9A-Za-z.\-]{1,20}', version) else None
    except (OSError, ValueError):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def backup_name(target: Path, version: str) -> str:
    name = f'{APP_NAME}-{version}备份.exe'
    if os.path.lexists(target / name):
        name = f'{APP_NAME}-{version}备份-{datetime.datetime.now():%Y%m%d-%H%M%S}.exe'
    return name


def copy_new_program(stage: Path, target: Path, info: dict) -> None:
    """Step 3: _internal.new and the .new.exe beside the old program, compared with the stage."""
    new_internal, new_exe = target / NEW_INTERNAL, target / NEW_EXE
    for leftover in (new_internal, new_exe):
        if os.path.lexists(leftover):
            say(f'发现上次未完成安装的残留，将重新生成：{leftover.name}')
            remove_path(leftover)           # InstallError when a file in it is held
    say('复制新程序…')
    try:
        shutil.copytree(stage / INTERNAL, new_internal)
        shutil.copy2(stage / EXE_NAME, new_exe)
        count, size = folder_stats(new_internal)
        count, size = count + 1, size + new_exe.stat().st_size
        same = (count, size) == (info['files'], info['size']) and file_sha256(new_exe) == info['exe_sha256']
        differences = ''
        if same and info.get('file_list') is not None:
            say('核对复制结果（全部文件的 SHA256）…')
            copied = file_entries(new_internal, INTERNAL + '/')
            copied.append({'path': EXE_NAME, 'size': new_exe.stat().st_size, 'sha256': file_sha256(new_exe)})
            differences = list_differences(info['file_list'], copied)
    except (OSError, shutil.Error) as error:
        left = discard(target, [NEW_INTERNAL, NEW_EXE])
        raise InstallError(f'复制新程序失败（{short(error)}）；' + (f'副本未能完全删除（{"、".join(left)}），'
                           '下次安装会重新处理' if left else '已删除副本') + '，旧程序未改动。') from None
    except BaseException:                   # Ctrl+C while copying: no half copy is left behind
        discard(target, [NEW_INTERNAL, NEW_EXE])
        raise
    if not same:
        discard(target, [NEW_INTERNAL, NEW_EXE])
        raise InstallError(f'复制结果与暂存目录不一致（{count} 个文件 / {size} 字节，应为 {info["files"]} / '
                           f'{info["size"]}）；已删除副本，未做其他更改。')
    if differences:
        discard(target, [NEW_INTERNAL, NEW_EXE])
        raise InstallError(f'复制结果与发布验证记录的文件清单不符：{differences}；已删除副本，未做其他更改。')


def plan_install(target: Path, kind: str, info: dict, old_version: str | None, gate) -> dict:
    """Steps 4 and 5 as a list of renames, the receipt and the files to write first."""
    tag = stamp()
    steps, trash, files = [], [], {}
    exe, receipt_path = target / EXE_NAME, target / RECEIPT
    current = read_json(receipt_path) if receipt_path.is_file() else None

    def supersede():
        """An active receipt that cannot be kept in the chain is set aside, never overwritten."""
        if os.path.lexists(receipt_path):
            steps.append(move_step(RECEIPT, f'install-receipt.superseded-{tag}.json'))

    if kind == 'onedir':
        installed = current.get('installed') if isinstance(current, dict) else None
        if not isinstance(installed, str) or not VERSION_PATTERN.fullmatch(installed):
            installed = None
        previous_version = installed or program_version(exe, gate) or old_version or OLD_VERSION_UNKNOWN
        if os.path.lexists(target / PREVIOUS):
            discarded = f'{PREVIOUS}.old-{tag}'
            steps.append(move_step(PREVIOUS, discarded))
            trash.append(discarded)
        steps += [mkdir_step(PREVIOUS), move_step(EXE_NAME, f'{PREVIOUS}\\{EXE_NAME}'),
                  move_step(INTERNAL, f'{PREVIOUS}\\{INTERNAL}')]
        kept = None
        if os.path.lexists(receipt_path):
            chained = chained_receipt(target, current)
            if chained is current:
                steps.append(move_step(RECEIPT, f'{PREVIOUS}\\{RECEIPT}'))
                kept = f'{PREVIOUS}\\{RECEIPT}'
            elif chained is not None:
                files[KEEP_RECEIPT] = chained
                replaced = f'install-receipt.replaced-{tag}.json'
                steps += [move_step(RECEIPT, replaced), move_step(KEEP_RECEIPT, f'{PREVIOUS}\\{RECEIPT}')]
                trash.append(replaced)
                kept = f'{PREVIOUS}\\{RECEIPT}'
            else:
                supersede()
        previous = {'layout': 'onedir', 'version': previous_version, 'exe': f'{PREVIOUS}\\{EXE_NAME}',
                    'internal': f'{PREVIOUS}\\{INTERNAL}', 'receipt': kept}
    elif kind == 'onefile':
        previous_version = old_version or program_version(exe, gate) or OLD_VERSION_UNKNOWN
        name = backup_name(target, previous_version)
        previous = {'layout': 'onefile', 'version': previous_version, 'exe': name, 'internal': None,
                    'receipt': None, 'exe_sha256': file_sha256(exe)}
        steps.append(move_step(EXE_NAME, name))
        supersede()
    else:
        previous = {'layout': 'none', 'version': None, 'exe': None, 'internal': None, 'receipt': None}
        supersede()
    # "checked" turns true only once the post-install gate passed (mark_checked).
    receipt = {'installed': info['version'], 'time': now(), 'previous': previous,
               'exe_sha256': info['exe_sha256'], 'files': info['files'], 'size': info['size'], 'checked': False}
    files[PENDING_RECEIPT] = receipt
    steps += [move_step(NEW_INTERNAL, INTERNAL), move_step(NEW_EXE, EXE_NAME), move_step(PENDING_RECEIPT, RECEIPT)]
    return {'steps': steps, 'trash': trash, 'files': files, 'receipt': receipt}


def install(stage: Path, target: Path, old_version: str | None, instance_name: str, dry_run: bool,
            shortcut: bool, shortcut_folder: Path | None) -> int:
    say(f'安装：{stage}\n  ->  {target}')
    if not target.is_dir():
        raise InstallError(f'程序目录不存在：{target}')
    if stage.resolve() == target.resolve() or target.resolve().is_relative_to(stage.resolve()):
        raise InstallError('程序目录不能是暂存目录本身或位于其中。')
    refuse_if_running(target, instance_name, RUNNING_INSTALL)
    info = check_stage(stage)
    version = info['version']
    gate = load_gate()
    if dry_run and os.path.lexists(target / JOURNAL):
        recover(target, dry_run=True)
        say('试运行（--dry-run）：未做任何更改。')
        return 0
    recover(target, dry_run=False)
    unchecked = unchecked_install(target)
    if unchecked and dry_run:
        say(f'程序目录里的 {unchecked} 已经启用，但上次安装没有完成安装后自检（安装过程被中断）；'
            '实际运行时会先把它回滚，再重新安装并自检。')
        say('试运行（--dry-run）：未做任何更改。')
        return 0
    if unchecked:
        say(f'程序目录里的 {unchecked} 已经启用，但上次安装没有完成安装后自检（安装过程被中断）；'
            '先把它回滚，再重新安装并自检。')
        try:
            rollback(target, instance_name, dry_run=False, check_running=False, automatic=True)
        except InstallError as error:
            raise InstallError(f'无法回滚上次未经自检的安装（{error}）；未开始新的安装。') from None
        say('已回滚上次未经自检的安装。')
    kind = layout_of(target)
    say(f'当前布局：{kind}；将安装 {version}（{info["files"]} 个文件，{info["size"] / 1e6:.1f} MB）')
    if dry_run:
        plan = {'onedir': f'把 {EXE_NAME} 和 {INTERNAL} 移入 {PREVIOUS}\\',
                'onefile': f'把 {EXE_NAME} 改名为 {APP_NAME}-<版本>备份.exe',
                'none': '无需移动旧程序'}[kind]
        say(f'试运行（--dry-run）：将复制 {INTERNAL}.new 和 {NEW_EXE}，{plan}，再启用新程序并自检。未做任何更改。')
        return 0

    # 3. copy next to the old program (never into an existing _internal)
    copy_new_program(stage, target, info)

    # 4. + 5. move the previous program aside and activate the new one: one journalled sequence of renames
    refuse_if_running(target, instance_name, RUNNING_INSTALL)
    try:
        plan = plan_install(target, kind, info, old_version, gate)
    except BaseException:
        discard(target, [NEW_INTERNAL, NEW_EXE])
        raise
    held = HeldInterrupt().start()          # a Ctrl+C during the renames acts once they are all done
    try:
        journal = run_steps(target, 'install', plan['steps'], files=plan['files'],
                            temporaries=[NEW_INTERNAL, NEW_EXE], trash=plan['trash'])
    except BaseException:
        held.stop()
        if not os.path.lexists(target / JOURNAL):
            discard(target, [NEW_INTERNAL, NEW_EXE])
        raise
    receipt = plan['receipt']
    previous = receipt['previous']

    # Committed: the new program is active but not checked.  Until the gate has passed and the receipt says so,
    # every failure and every Ctrl+C (also one held back above, or one during the clean-up) rolls it back.
    try:
        held.stop()
        held.raise_if_pressed()
        left = finish(target, journal)
        say(f'已启用 {version}；旧程序：{previous["layout"]}' + (f'（{previous["exe"]}）' if previous['exe'] else ''))
        say('安装记录：' + json.dumps(receipt, ensure_ascii=False))
        if left:
            say('更早的备份未能完全删除（是否有文件被占用？）：' + '、'.join(left) + '。它已不再使用，可以稍后手动删除。')
        # 6. post-install gate from the installed path
        results = post_install_gate(target, version, gate)
        mark_checked(target, receipt)
    except BaseException as error:  # GateError, anything unexpected, Ctrl+C: never keep an unchecked install
        return abort_install(target, instance_name, version, journal, error)
    say(f'安装后自检通过：自检 {results["self_test_seconds"]} 秒，下载自检 {results["download"]["files"]}，'
        f'窗口 {results["time_to_window"]} 秒出现。')

    # 7. shortcut (only when asked)
    if shortcut:
        create_shortcut(target / EXE_NAME, shortcut_folder)

    # 8. what remains for the owner
    say('\n还需要您亲自完成：')
    say('  1. 用新程序下载一首真实的 YouTube 音频（这是 _internal\\vendor\\bin 中的 deno.exe 与 FFmpeg 在中文路径下'
        '对真实网站的唯一检验）。')
    if previous['layout'] == 'onedir':
        backups = backups_to_name(target, receipt)
        say(f'  2. 确认无误、也不再需要用脚本回滚时，可删除 {target / PREVIOUS}（删除后回滚命令无法再回到上一次安装'
            + (f'；{backups} 仍可直接双击使用' if backups else '') + '）。')
    elif previous['layout'] == 'onefile':
        say(f'  2. 旧版保留为 {target / previous["exe"]}，可随时双击运行；它与新版共用 data 文件夹和设置。')
    say('  3. %TEMP% 中 1.5.1 遗留的两个 _MEI* 文件夹（各约 640 MB）可以手动删除；本脚本不会删除它们。')
    say(f'  回滚命令："{sys.executable}" -B "{Path(__file__).resolve()}" --rollback --target "{target}"'
        '（每运行一次回到上一次安装前的程序）')
    return 0


def abort_install(target: Path, instance_name: str, version: str, journal: dict, error: BaseException) -> int:
    """The install was committed but its gate did not pass (failure, Ctrl+C, an error in the clean-up): roll it
    back at once, with Ctrl+C held back so that the rollback cannot stop half way.  Exit codes: 2 (gate failed),
    130 (interrupted), 3 (the rollback failed as well)."""
    interrupted = isinstance(error, KeyboardInterrupt)
    if interrupted:
        say(f'安装在自检完成之前被中断（Ctrl+C）；未经自检的 {version} 不会保留。')
    elif isinstance(error, Exception):
        say(f'安装后自检失败：{short(error)}')
    else:
        say(f'安装在自检完成之前被终止（{type(error).__name__}）。')
    say('正在自动回滚…（这一步不会被 Ctrl+C 打断）')
    with HeldInterrupt():
        try:
            if os.path.lexists(target / JOURNAL):
                finish(target, journal)         # every rename is done; only its clean-up was cut short
            rollback(target, instance_name, dry_run=False, check_running=False, automatic=True)
        except Exception as failure:
            say(f'自动回滚失败：{short(failure)}')
            say(f'{version} 仍是当前程序，但没有通过安装后自检。请关闭可能正在运行的 YT-DLP 下载器后，再运行一次'
                '安装命令（它会先回滚这次未经自检的安装）或回滚命令：')
            say(f'  "{sys.executable}" -B "{Path(__file__).resolve()}" --rollback --target "{target}"')
            return 3
    say('已自动回滚到安装前的状态。')
    if interrupted:
        return 130
    if not isinstance(error, Exception):
        raise error
    return 2


def post_install_gate(target: Path, version: str, gate) -> dict:
    """--self-test from the installed path (app_dir must be the target), fixture download smoke, GUI smoke."""
    work = Path(tempfile.mkdtemp(prefix='ytdlp-install-gate-')).resolve()
    exe = target / EXE_NAME
    server = None
    try:
        data = gate.self_test(exe, work, redirect_app_dir=False, name='installed-self-test')
        gate.check_self_test_report(data, target)
        gate.check(data.get('app_version') == version, f'app_version {data.get("app_version")!r}, expected {version}')
        gate.check(gate.same_path(data.get('app_dir', ''), target), f'app_dir is {data.get("app_dir")}, not {target}')
        gate.check(gate.inside(data.get('state_dir', ''), work), f'state_dir {data.get("state_dir")} not redirected')
        catalog = target / 'data' / 'library.sqlite3'
        if catalog.is_file():
            gate.check(gate.same_path(data.get('catalog', ''), catalog),
                       f'catalog is {data.get("catalog")}, expected {catalog}')
        base, server = gate.fixture_server()
        download = gate.download_smoke(exe, work, base)
        gui = gate.gui_smoke(exe, work, base + '/fixture.mp4')
        return {'self_test_seconds': data['_seconds'], 'download': download, 'time_to_window': gui['time_to_window']}
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        shutil.rmtree(work, ignore_errors=True)


def create_shortcut(exe: Path, folder: Path | None = None) -> bool:
    """Desktop shortcut to the exe (working directory = app folder), only when none exists."""
    environment = os.environ.copy()
    if folder is None:
        found = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                                "[Environment]::GetFolderPath('Desktop')"],
                               capture_output=True, text=True, encoding='utf-8', creationflags=NO_WINDOW, timeout=60)
        folder = Path(found.stdout.strip())
        if found.returncode or not folder.is_dir():
            say('未找到桌面文件夹，未创建快捷方式。')
            return False
    link = Path(folder) / SHORTCUT_NAME
    if link.exists():
        say(f'快捷方式已存在，未改动：{link}')
        return False
    environment.update(YTDLP_LINK=str(link), YTDLP_EXE=str(exe), YTDLP_DIR=str(exe.parent))
    made = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                           '$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:YTDLP_LINK);'
                           '$s.TargetPath=$env:YTDLP_EXE;$s.WorkingDirectory=$env:YTDLP_DIR;'
                           '$s.IconLocation=$env:YTDLP_EXE+",0";$s.Save()'],
                          capture_output=True, env=environment, creationflags=NO_WINDOW, timeout=60)
    if made.returncode or not link.exists():
        say(f'快捷方式创建失败：{made.stderr.decode(errors="replace").strip()}')
        return False
    say(f'已创建快捷方式：{link}')
    return True


# ------------------------------------------------------------------ rollback

def rollback(target: Path, instance_name: str, dry_run: bool, check_running: bool = True,
             automatic: bool = False) -> int:
    """automatic: called by install (a failed gate, or an unchecked install found at the start)."""
    say(f'回滚：{target}')
    if check_running:
        refuse_if_running(target, instance_name, RUNNING_ROLLBACK)
    if dry_run and os.path.lexists(target / JOURNAL):
        recover(target, dry_run=True)
        say('试运行（--dry-run）：未做任何更改。')
        return 0
    recover(target, dry_run=False)
    receipt_path = target / RECEIPT
    if not receipt_path.is_file():
        backups = backups_to_name(target)
        raise InstallError(f'没有安装记录 {RECEIPT}，无法回滚；未做任何更改。'
                           + (f'（也可以直接双击程序目录里的 {backups} 使用旧版。）' if backups else ''))
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise InstallError(f'安装记录无法读取（{error}）；未做任何更改。')
    problem = receipt_problem(receipt)
    if problem:
        raise InstallError(f'安装记录内容无效（{problem}）；未做任何更改。')
    installed, previous = receipt['installed'], receipt['previous']
    if receipt.get('checked') is False and not automatic:
        say(f'当前的 {installed} 没有完成安装后自检（安装过程被中断），回滚会把它撤下。')
    kind = previous['layout']
    names = {'onefile': [previous['exe']], 'onedir': [previous['exe'], previous['internal']], 'none': []}[kind]
    for name in names:
        try:
            inside_target(target, name)
        except InstallError as error:
            raise InstallError(f'{error}；未做任何更改。') from None
    missing =[] if (target / EXE_NAME).is_file() else [EXE_NAME]
    missing += [] if (target / INTERNAL).is_dir() and not is_link(target / INTERNAL) else [INTERNAL]
    missing += [name for name in names if not os.path.lexists(target / name)]
    if missing:
        backups = backups_to_name(target, receipt)
        raise InstallError('安装记录提到的文件不存在：' + '、'.join(missing) + '；未做任何更改。'
                           '（如果旧程序已被手动删除，脚本无法再回滚'
                           + (f'；{backups} 仍可直接双击使用' if backups else '') + '。）')
    try:
        if kind == 'onefile' and file_sha256(target / previous['exe']) != previous['exe_sha256']:
            raise InstallError(f'{previous["exe"]} 与安装记录中的 SHA256 不符；未做任何更改。')
    except OSError as error:
        raise InstallError(f'无法读取 {previous["exe"]}（{short(error)}）；未做任何更改。')
    rolled = f'_rolledback-{installed}'
    if dry_run:
        say(f'试运行（--dry-run）：将把 {EXE_NAME} 和 {INTERNAL} 移入 {rolled}\\，恢复 {kind} 旧程序'
            + (f'（{previous["exe"]}）' if previous.get('exe') else '') + '。未做任何更改。')
        return 0
    tag = stamp()
    steps, trash = [], []
    if os.path.lexists(target / rolled):
        older = f'{rolled}.old-{tag}'
        steps.append(move_step(rolled, older))
        trash.append(older)
    steps += [mkdir_step(rolled), move_step(EXE_NAME, f'{rolled}\\{EXE_NAME}'),
              move_step(INTERNAL, f'{rolled}\\{INTERNAL}')]
    if kind == 'onefile':
        steps.append(move_step(previous['exe'], EXE_NAME))
    elif kind == 'onedir':
        steps += [move_step(previous['exe'], EXE_NAME), move_step(previous['internal'], INTERNAL)]
    # An older rolled-back receipt goes (the folder it described was replaced as well).
    if os.path.lexists(target / ROLLED_BACK_RECEIPT):
        replaced = f'install-receipt.replaced-{tag}.json'
        steps.append(move_step(ROLLED_BACK_RECEIPT, replaced))
        trash.append(replaced)
    steps.append(move_step(RECEIPT, ROLLED_BACK_RECEIPT))
    kept = previous.get('receipt')
    if kind == 'onedir' and kept and (target / kept).is_file():
        steps.append(move_step(kept, RECEIPT))
    # Ctrl+C is held back from the first rename to the end of the clean-up: a rollback that has been committed
    # is reported as done, never as "interrupted" (a second run would roll back once more).
    with HeldInterrupt() as held:
        try:
            journal = run_steps(target, 'rollback', steps, trash=trash)
        except InstallError as error:
            raise InstallError(f'回滚失败：{error}') from None
        try:
            left, cut = finish(target, journal), False
        except KeyboardInterrupt:
            left, cut = [], True
        say(f'已回滚：{installed} 移入 {rolled}\\；当前程序：'
            + {'onefile': f'{previous.get("version")}（单文件）', 'onedir': f'{previous.get("version")}（文件夹版）',
               'none': '无'}[kind])
        if left:
            say('更早的回滚文件未能完全删除（是否有文件被占用？）：' + '、'.join(left) + '。可以稍后手动删除。')
        if receipt_path.is_file():
            say(f'上一次安装的记录已恢复为 {RECEIPT}，还可以再回滚一次。')
        if cut:
            say(f'清理被中断（Ctrl+C），回滚本身已经完成；{JOURNAL} 和待删除的旧文件留在程序目录里，'
                '下次运行安装或回滚时会先把清理做完。')
    if held.pressed and not automatic:
        say('回滚期间按下的 Ctrl+C 在回滚完成后才处理：回滚已经完成。')
    return 130 if cut and not automatic else 0


# ------------------------------------------------------------------ entry point

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Install the staged program, or roll back (receipt-driven).')
    parser.add_argument('--stage', help=f'staged folder, e.g. build\\package-1.6.0\\{APP_NAME}')
    parser.add_argument('--target', required=True, help='the app folder (where YT-DLP下载器.exe lives)')
    parser.add_argument('--dry-run', action='store_true', help='check and print the plan; change nothing')
    parser.add_argument('--shortcut', action='store_true', help='create a Desktop shortcut when none exists')
    parser.add_argument('--rollback', action='store_true', help='undo the installation named by install-receipt.json')
    parser.add_argument('--old-version', help='version of the old single-file program (names its backup)')
    parser.add_argument('--instance-name', default=INSTANCE_NAME, help='single-instance mutex of the app')
    parser.add_argument('--shortcut-dir', help=argparse.SUPPRESS)   # rehearsals: a folder instead of the Desktop
    options = parser.parse_args(argv)
    target = Path(options.target).resolve()
    try:
        if options.rollback:
            return rollback(target, options.instance_name, options.dry_run)
        if not options.stage:
            raise InstallError('请用 --stage 指定暂存目录。')
        if options.old_version is not None and not re.fullmatch(r'[0-9A-Za-z.\-]{1,20}', options.old_version):
            raise InstallError('--old-version 只能包含字母、数字、点和横线。')
        return install(Path(options.stage).resolve(), target, options.old_version, options.instance_name,
                       options.dry_run, options.shortcut,
                       Path(options.shortcut_dir) if options.shortcut_dir else None)
    except InstallError as error:
        say(f'未完成：{error}')
        return 1
    except KeyboardInterrupt:
        say(f'已中断（Ctrl+C）。如果程序目录里留有 {JOURNAL}，再次运行本脚本会先把它恢复。')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
