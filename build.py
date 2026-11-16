"""Build, stage and gate the release (1.6.0 WP6).

    python -B build.py [--skip-tests]

One entry point that never downloads anything:
  1. check vendor/ against vendor/manifest.json: yt-dlp.exe and exactly the vendor/bin files the manifest lists,
     each with its SHA-256 (ffmpeg.exe, ffprobe.exe, deno.exe always required); no *.zip / *.part leftovers; a
     manifest without a file list (older prepare_vendor.py) is refused with the offline fix
     (prepare_vendor.py --record-local); a difference from vendor.lock.json is only noted;
  2. unless --skip-tests: verification/run_tests.py --all must exit 0;
  3. PyInstaller app.spec (onedir) into build/dist, work files in build/work;
  4. stage build/dist/YT-DLP下载器 as build/package-<version>/YT-DLP下载器 (an older stage is replaced, never merged)
     and write build/package-<version>/build.json {source_sha256, exe_sha256, built};
  5. verification/run_tests.py --all --exe <staged exe>, then verification/verify_release.py.
Everything this script writes stays under build/.  Child processes (tests, PyInstaller, the gate) are started with
the interpreter that runs this script (sys.executable), so the script works in the source tree (where .venv sits
beside it) and in a copy of the tree without .venv alike.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
APP_NAME = 'YT-DLP下载器'
TEST_TIMEOUT = 3600          # seconds for one run_tests.py call
PYINSTALLER_TIMEOUT = 1800
GATE_TIMEOUT = 900


class BuildError(Exception):
    """A step failed; str() is the message for the person running the build."""


def say(text: str) -> None:
    print(f'[build] {text}', flush=True)


def load_run_tests():
    """verification/run_tests.py as a module: its source_sha256() is the one definition of the source hash."""
    spec = importlib.util.spec_from_file_location('run_tests', ROOT / 'verification' / 'run_tests.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def child_environment() -> dict:
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    # PyInstaller's cache (bincache) stays inside build/ as well.
    environment['PYINSTALLER_CONFIG_DIR'] = str(BUILD / 'pyinstaller-config')
    return environment


def run(command: list, timeout: int, what: str) -> None:
    say('$ ' + subprocess.list2cmdline([str(part) for part in command]))
    started = time.monotonic()
    process = subprocess.Popen([str(part) for part in command], cwd=str(ROOT), env=child_environment(),
                               stdin=subprocess.DEVNULL)
    try:
        code = process.wait(timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        process.wait(30)
        raise BuildError(f'{what}: no result after {timeout} s; the process tree was ended')
    except BaseException:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        raise
    say(f'{what}: exit code {code} after {time.monotonic() - started:.1f} s')
    if code != 0:
        raise BuildError(f'{what} failed (exit code {code})')


def check_interpreter() -> None:
    if importlib.util.find_spec('PyInstaller') is None:
        raise BuildError(f'this Python cannot import PyInstaller: {sys.executable}\n'
                         'Run build.py with the project interpreter, for example\n'
                         r'  D:\YT-DLP下载器\源码\.venv\Scripts\python.exe -B build.py')


def load_prepare_vendor():
    """prepare_vendor.py beside this script as a module: the one definition of the manifest's file list."""
    spec = importlib.util.spec_from_file_location('prepare_vendor_for_build', ROOT / 'prepare_vendor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_vendor(vendor: Path | None = None, lock: Path | None = None) -> None:
    """vendor/ holds exactly what vendor/manifest.json records: yt-dlp.exe and the files of vendor/bin (the list is
    taken from the manifest, not hard-coded, so a newer FFmpeg with other DLL version numbers builds), each with its
    SHA-256; ffmpeg.exe, ffprobe.exe and deno.exe are always required."""
    vendor = Path(vendor) if vendor is not None else ROOT / 'vendor'
    lock = Path(lock) if lock is not None else ROOT / 'vendor.lock.json'
    tools = load_prepare_vendor()
    try:
        manifest = tools.read_manifest(vendor)
    except tools.PrepareError as error:
        raise BuildError(str(error))
    entry = tools.entry_for(manifest, 'yt-dlp')
    if not entry or not entry.get('sha256'):
        raise BuildError('vendor/manifest.json has no yt-dlp/yt-dlp entry with a sha256')
    engine = vendor / 'yt-dlp.exe'
    if not engine.is_file():
        raise BuildError('vendor/yt-dlp.exe is missing')
    actual = file_sha256(engine)
    if actual != str(entry['sha256']).lower():
        raise BuildError(f'vendor/yt-dlp.exe sha256 {actual} differs from vendor/manifest.json ({entry["sha256"]})')
    expected = tools.manifest_files(manifest)
    if expected is None:
        raise BuildError('vendor/manifest.json has no file list (it was written by an older prepare_vendor.py). '
                         'Record the files now in vendor/ once, offline: python -B prepare_vendor.py --record-local '
                         '(or prepare vendor/ again from vendor.lock.json: python prepare_vendor.py)')
    required = ['yt-dlp.exe'] + ['bin/' + name for name in tools.REQUIRED_BIN]
    absent = [path for path in required if path not in expected]
    if absent:
        raise BuildError('vendor/manifest.json does not list ' + ', '.join(absent))
    listed_bin = {path[4:] for path in expected if path.startswith('bin/')}
    if not (vendor / 'bin').is_dir():
        raise BuildError('vendor/bin is missing')
    present_bin = {item.name for item in (vendor / 'bin').iterdir()}
    missing = sorted(listed_bin - present_bin)
    extra = sorted(present_bin - listed_bin)
    if missing:
        raise BuildError('vendor/bin is incomplete, missing: ' + ', '.join(missing))
    if extra:
        raise BuildError('vendor/bin holds files vendor/manifest.json does not list (they would be packaged): '
                         + ', '.join(extra) + '; prepare vendor/ again (python prepare_vendor.py)')
    changed = [path for path, digest in sorted(expected.items())
               if not (vendor / path).is_file() or file_sha256(vendor / path) != digest]
    if changed:
        raise BuildError('vendor/ files differ from the SHA-256 in vendor/manifest.json: ' + ', '.join(changed))
    leftovers = [str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
                 for pattern in ('*.zip', '*.part') for path in vendor.rglob(pattern)]
    if leftovers:
        # app.spec keeps *.zip and *.part out of the program; a leftover means vendor/ was not prepared cleanly.
        raise BuildError('vendor/ holds download leftovers of an interrupted or older prepare_vendor.py: '
                         + ', '.join(leftovers) + '. app.spec would leave them out of the program, but delete them '
                         '(prepare_vendor.py --record-local or a new prepare_vendor.py run removes them)')
    if not (vendor / 'licenses').is_dir():
        raise BuildError('vendor/licenses is missing')
    try:
        notes = tools.lock_differences(manifest, tools.load_lock(lock))
    except tools.PrepareError as error:
        notes = [f'vendor.lock.json not compared ({error})']
    for note in notes:
        say(f'note: {note}; the build bundles what vendor/manifest.json records')
    say(f'vendor ok: yt-dlp {entry.get("version", "?")} sha256 {actual[:16]}..., {len(listed_bin)} files in vendor/bin '
        f'as vendor/manifest.json lists them ({", ".join(sorted(listed_bin))})')


def _writable(function, path, _info):
    # A read-only file (for example a copied licence file) would stop rmtree.
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


def remove_folder(path: Path) -> None:
    """Remove a folder this script owns (below build/); refuses anything else and links."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    resolved = path.resolve()
    if not resolved.is_relative_to(BUILD.resolve()) or resolved == BUILD.resolve():
        raise BuildError(f'refusing to delete a folder outside build/: {path}')
    attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
    if path.is_symlink() or attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise BuildError(f'refusing to delete a link or junction: {path}')
    for attempt in range(5):
        shutil.rmtree(path, onerror=_writable)
        if not path.exists():
            return
        time.sleep(0.5)
    raise BuildError(f'could not remove {path} (is a file in it open?)')


def tree_size(folder: Path) -> tuple:
    count = size = 0
    for item in folder.rglob('*'):
        if item.is_file():
            count += 1
            size += item.stat().st_size
    return count, size


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Build, stage and gate the release; never downloads anything.')
    parser.add_argument('--skip-tests', action='store_true',
                        help='skip step 2 (run_tests.py --all before building); the gate of step 5 always runs')
    options = parser.parse_args(argv)
    started = time.monotonic()
    timings = {}
    try:
        check_interpreter()
        run_tests = load_run_tests()
        version = run_tests.app_version()
        if not version:
            raise BuildError('APP_VERSION cannot be read from src/engine.py')
        say(f'version {version}; interpreter {sys.executable}')

        # 1. vendor
        check_vendor()

        # 2. tests on the sources
        if options.skip_tests:
            say('step 2 skipped (--skip-tests)')
        else:
            clock = time.monotonic()
            run([sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'run_tests.py', '--all'],
                TEST_TIMEOUT, 'run_tests.py --all')
            timings['tests_source'] = round(time.monotonic() - clock, 1)

        # 3. PyInstaller (the source hash is taken before the build and must not change during it)
        sources = run_tests.source_sha256()
        clock = time.monotonic()
        remove_folder(BUILD / 'dist' / APP_NAME)
        run([sys.executable, '-B', '-m', 'PyInstaller', ROOT / 'app.spec', '--noconfirm',
             '--distpath', BUILD / 'dist', '--workpath', BUILD / 'work'], PYINSTALLER_TIMEOUT, 'PyInstaller')
        timings['pyinstaller'] = round(time.monotonic() - clock, 1)
        if run_tests.source_sha256() != sources:
            raise BuildError('src/*.py or app.spec changed during the build; build again')
        dist = BUILD / 'dist' / APP_NAME
        if not (dist / f'{APP_NAME}.exe').is_file() or not (dist / '_internal').is_dir():
            raise BuildError(f'PyInstaller did not produce {dist}\\{APP_NAME}.exe with _internal\\')

        # 4. stage
        package = BUILD / f'package-{version}'
        stage = package / APP_NAME
        remove_folder(stage)
        stale_record = package / 'build.json'
        if stale_record.exists():
            stale_record.unlink()
        package.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dist, stage)
        if tree_size(stage) != tree_size(dist):
            raise BuildError('the staged folder differs from build/dist in file count or size')
        exe = stage / f'{APP_NAME}.exe'
        record = {'version': version, 'source_sha256': sources, 'exe_sha256': file_sha256(exe),
                  'built': datetime.datetime.now().astimezone().isoformat(timespec='seconds')}
        temporary = stale_record.with_name(f'build.json.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, stale_record)
        count, size = tree_size(stage)
        say(f'staged {stage} ({count} files, {size / 1e6:.1f} MB); build.json written')

        # 5. tests against the staged exe, then the release gate
        clock = time.monotonic()
        run([sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'run_tests.py', '--all', '--exe', exe],
            TEST_TIMEOUT, 'run_tests.py --all --exe')
        timings['tests_exe'] = round(time.monotonic() - clock, 1)
        clock = time.monotonic()
        run([sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'verify_release.py', '--stage', stage],
            GATE_TIMEOUT, 'verify_release.py')
        timings['gate'] = round(time.monotonic() - clock, 1)
    except BuildError as error:
        say(f'FAILED: {error}')
        return 1
    except KeyboardInterrupt:
        say('interrupted')
        return 130
    timings['total'] = round(time.monotonic() - started, 1)
    say('timings (s): ' + ', '.join(f'{name} {value}' for name, value in timings.items()))
    say(f'done: {BUILD / "verify" / f"release-{version}.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
