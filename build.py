"""Build, stage and gate the release (1.6.0 WP6).

    python -B build.py [--skip-tests]

One entry point that never downloads anything:
  1. check vendor/ against vendor/manifest.json: yt-dlp.exe and exactly the vendor/bin files the manifest lists,
     each with its SHA-256 (ffmpeg.exe, ffprobe.exe, deno.exe always required); no *.zip / *.part leftovers; a
     manifest without a file list (older prepare_vendor.py) is refused with the offline fix
     (prepare_vendor.py --record-local); a difference from vendor.lock.json is only noted;
  2. unless --skip-tests: verification/run_tests.py --all must exit 0 (serial; it writes build/verify/report-source.json,
     which the gate needs: with --skip-tests that file must already hold a green serial --all run of these sources
     and of these tests: its tests_sha256 must be that of verification/ and vendor/manifest.json now);
  3. PyInstaller app.spec (onedir) into build/dist, work files in build/work;
  1b. (2.0.5) vendor/licenses/notices holds exactly the "notices" of vendor.lock.json, and each was written for the
     Python and package versions this build bundles;
  4. stage build/dist/YT-DLP下载器 as build/package-<version>/YT-DLP下载器 (an older stage is replaced, never merged;
     an older zip of this version is deleted); (2.0.5) complete the licence texts in _internal/vendor/licenses (the
     Python runtime's LICENSE.txt, the licence files of every package of requirements-lock.txt) and write
     THIRD-PARTY.txt beside the exe; write build/package-<version>/build.json {source_sha256, exe_sha256, built};
     then build/dist and build/work are removed (best effort: a folder that cannot be removed is only noted);
  5. (2.0.0) verification/run_tests.py --only-tier exe --exe <staged exe> (build/verify/report-exe.json; until 1.8.1
     this ran every tier again, though only the tier exe reads the exe), then verification/verify_release.py, which
     takes the two reports together;
  6. (2.0.5) build/ytdlp-desktop-<version>-win64.zip (the folder YT-DLP下载器 with exactly the files of the release
     record, UTF-8 names, sorted, one timestamp) and its .sha256; refused when a file holds this machine's user name,
     user folder or git e-mail.
Everything this script writes stays under build/.  Child processes (tests, PyInstaller, the gate) are started with
the interpreter that runs this script (sys.executable), so the script works in the source tree (where .venv sits
beside it) and in a copy of the tree without .venv alike.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time
import zipfile

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build'
REQUIREMENTS = ROOT / 'requirements-lock.txt'
APP_NAME = 'YT-DLP下载器'
PROJECT = 'https://github.com/Andy-vortex/ytdlp-desktop'
THIRD_PARTY = 'THIRD-PARTY.txt'                   # (2.0.5) beside the exe
LICENCES = ('_internal', 'vendor', 'licenses')    # (2.0.5) every licence text of the package lies below this folder
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
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


def source_tests_command() -> list:
    """Step 2: a serial run of the tiers default and all; run_tests.py writes it to build/verify/report-source.json."""
    return [sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'run_tests.py', '--all']


def exe_tests_command(exe) -> list:
    """Step 5 (2.0.0): only the tier exe against the staged exe (no other file reads VERIFY_EXE, so the other tiers
    would run a second time on the same sources); run_tests.py writes it to build/verify/report-exe.json."""
    return [sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'run_tests.py', '--only-tier', 'exe',
            '--exe', exe]


def check_interpreter() -> None:
    if importlib.util.find_spec('PyInstaller') is None:
        raise BuildError(f'this Python cannot import PyInstaller: {sys.executable}\n'
                         'Run build.py with the project interpreter, for example\n'
                         r'  .venv\Scripts\python.exe -X utf8 -B build.py   (from the repository root)')


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
    check_notices(tools, vendor, lock)
    try:
        notes = tools.lock_differences(manifest, tools.load_lock(lock))
    except tools.PrepareError as error:
        notes = [f'vendor.lock.json not compared ({error})']
    for note in notes:
        say(f'note: {note}; the build bundles what vendor/manifest.json records')
    say(f'vendor ok: yt-dlp {entry.get("version", "?")} sha256 {actual[:16]}..., {len(listed_bin)} files in vendor/bin '
        f'as vendor/manifest.json lists them ({", ".join(sorted(listed_bin))})')


def installed_version(name: str) -> str | None:
    """The version of ``python`` (this interpreter) or of an installed distribution, None when it is not installed."""
    if name.lower() == 'python':
        return platform.python_version()
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def check_notices(tools, vendor: Path, lock: Path) -> list:
    """(2.0.5) vendor/licenses/notices holds exactly the texts the lock's "notices" pin (each with its SHA-256), and
    every notice was written for what this build bundles: its "with" (curl_cffi==0.17.0, python==3.11.17) must be the
    installed version.  A difference would put wrong facts into THIRD-PARTY.txt, so it stops the build."""
    try:
        notices = tools.load_notices(lock)
    except tools.PrepareError as error:
        raise BuildError(str(error))
    folder = vendor / 'licenses' / tools.NOTICES
    present = sorted(item.name for item in folder.iterdir()) if folder.is_dir() else []
    wanted = sorted(notice['destination'] for notice in notices)
    changed = [notice['destination'] for notice in notices if not (folder / notice['destination']).is_file()
               or file_sha256(folder / notice['destination']) != notice['sha256']]
    if present != wanted or changed:
        raise BuildError('vendor/licenses/notices does not hold exactly the licence texts vendor.lock.json pins ('
                         + ', '.join(sorted(set(wanted) ^ set(present)) + changed) + '); prepare vendor/ again: '
                         'python prepare_vendor.py')
    stale = []
    for notice in notices:
        name, version = notice['with'].split('==', 1)
        actual = installed_version(name)
        if actual != version:
            stale.append(f'{notice["name"]} (written for {notice["with"]}, this build has {name} {actual})')
    if stale:
        raise BuildError('vendor.lock.json "notices" describe other versions than this build bundles: '
                         + '; '.join(stale) + '. Check the libraries these versions contain, correct the notices '
                         '(name, version, license, source, url, sha256, with) and run python prepare_vendor.py')
    return notices


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


def remove_leftovers() -> None:
    """(2.0.1) After the stage is checked, PyInstaller's output and work files are not needed any more (about
    0.5 GB).  Best effort: a folder that cannot be removed is noted and the build goes on."""
    for folder in (BUILD / 'dist', BUILD / 'work'):
        try:
            remove_folder(folder)
        except (BuildError, OSError) as error:
            say(f'note: {error}; left in place, the build goes on')


def tree_size(folder: Path) -> tuple:
    count = size = 0
    for item in folder.rglob('*'):
        if item.is_file():
            count += 1
            size += item.stat().st_size
    return count, size


# ------------------------------------------------------------------ (2.0.5) licence texts and THIRD-PARTY.txt

LICENCE_NAME = re.compile(r'(?i)^(licen[cs]e|copying|notice|authors)')
TEXT = '  License text: '                          # verify_release.py reads these lines


def package_path(*parts) -> str:
    """A path inside the package as THIRD-PARTY.txt writes it: relative to the package folder, with backslashes."""
    return '\\'.join(str(part) for part in parts)


def requirement_names(requirements: Path) -> list:
    names = []
    for line in Path(requirements).read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'\s*([A-Za-z0-9_.\-]+)==\S+\s*', line)
        if match:
            names.append(match.group(1))
    return names


# Where a distribution's metadata is vague: pyinstaller declares only the classifier "GPLv2" (its COPYING.txt is
# GPL-2.0-or-later with the bootloader exception); customtkinter's URLs name its website, not its source.
PACKAGE_FACTS = {'pyinstaller': {'license': 'GPL-2.0-or-later WITH Bootloader-exception'},
                 'customtkinter': {'source': 'https://github.com/TomSchimansky/CustomTkinter'}}


def package_licence(metadata) -> str:
    """The licence a distribution declares: License-Expression, else its licence classifiers, else a one-line
    License field (customtkinter 6.0.0 says MIT in its classifier and its LICENSE file, CC0 in the free-text field)."""
    expression = (metadata.get('License-Expression') or '').strip()
    if expression:
        return expression
    classifiers = [item.split('::')[-1].strip() for item in metadata.get_all('Classifier') or []
                   if item.startswith('License ::')]
    if classifiers:
        return ', '.join(classifiers)
    text = (metadata.get('License') or '').strip()
    return text if text and '\n' not in text and len(text) <= 60 else 'see the license text'


def package_source(metadata, name: str, version: str) -> str:
    """The source address a distribution declares (Project-URL source / repository / homepage, then Home-page),
    else its page on PyPI."""
    urls = {}
    for item in metadata.get_all('Project-URL') or []:
        label, _, url = item.partition(',')
        urls[re.sub(r'[\s_-]+', ' ', label.strip().lower())] = url.strip()
    for label in ('source', 'source code', 'repository', 'code', 'homepage', 'home'):
        if urls.get(label, '').startswith('https://'):
            return urls[label]
    home = (metadata.get('Home-page') or '').strip()
    return home if home.startswith('https://') else f'https://pypi.org/project/{name}/{version}/'


def collect_python_packages(folder: Path, requirements: Path = REQUIREMENTS) -> list:
    """The licence files of every package of requirements-lock.txt (what the program is built from), copied from this
    Python's installed distributions into folder/<package>/ at every build: each file below <dist-info>/licenses/
    (PEP 639) and each LICENSE / COPYING / NOTICE / AUTHORS file directly in the .dist-info folder.  The libraries a
    package carries inside itself with their own .dist-info below a _vendor folder (setuptools) go to
    folder/<package>/_vendor/<library>/ and are named in "vendored".  Writes folder/packages.json and returns
    [{package, version, license, source, files, vendored}] (files relative to ``folder``)."""
    folder = Path(folder)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    packages, missing = [], []
    for name in requirement_names(requirements):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
            continue
        copied, vendored = [], set()
        for item in distribution.files or []:
            parts = item.parts
            at = next((index for index, part in enumerate(parts) if part.endswith('.dist-info')), None)
            if at is None or len(parts) < at + 2:
                continue
            if at == 0:
                prefix = []
            elif at >= 2 and parts[at - 1] == '_vendor':
                library = parts[at][:-len('.dist-info')]
                vendored.add(' '.join(library.rsplit('-', 1)))          # 'jaraco.text-4.0.0' -> 'jaraco.text 4.0.0'
                prefix = ['_vendor', library]
            else:
                continue
            inside = list(parts[at + 1:])
            if inside[0] == 'licenses' and len(inside) > 1:
                inside = inside[1:]
            elif len(inside) != 1 or not LICENCE_NAME.match(inside[0]):
                continue
            target = '/'.join(prefix + inside)
            if target in copied:
                target = '/'.join(prefix + ['dist-info_' + '_'.join(inside)])
            (folder / name / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.locate(), folder / name / target)
            copied.append(target)
        metadata = distribution.metadata
        facts = PACKAGE_FACTS.get(name.lower(), {})
        packages.append({'package': name, 'version': distribution.version,
                         'license': facts.get('license') or package_licence(metadata),
                         'source': facts.get('source') or package_source(metadata, name, distribution.version),
                         'files': sorted(f'{name}/{target}' for target in copied),
                         'vendored': sorted(vendored, key=str.lower)})
    if missing:
        raise BuildError('not installed in this Python, so their licences cannot be shipped: ' + ', '.join(missing)
                         + '; run build.py with the project .venv after pip install -r requirements-lock.txt')
    (folder / 'packages.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2) + '\n',
                                          encoding='utf-8', newline='\n')
    return packages


def python_runtime() -> dict:
    """The Python runtime PyInstaller bundles: this interpreter's base installation (its LICENSE.txt, and for a
    python-build-standalone build the release in its BUILD file) and the Tcl/Tk it carries."""
    base = Path(sys.base_prefix)
    licence = base / 'LICENSE.txt'
    if not licence.is_file():
        raise BuildError(f'{licence} is missing, so the Python licence cannot be shipped')
    build_file = base / 'BUILD'
    build = build_file.read_text(encoding='utf-8', errors='replace').strip() if build_file.is_file() else ''
    import tkinter
    return {'version': platform.python_version(), 'build': build if re.fullmatch(r'\d{8}', build) else '',
            'license_file': licence, 'tcl_tk': tkinter.Tcl().eval('info patchlevel')}


PLAYWRIGHT_TEXTS = ('LICENSE', 'NOTICE')


def playwright_texts() -> Path:
    """The folder with Playwright's LICENSE and NOTICE (Apache-2.0): patchright's driver is Playwright's driver,
    patched, and does not carry them, so they are taken from the playwright distribution of requirements-lock.txt."""
    try:
        folder = Path(importlib.metadata.distribution('playwright').locate_file('playwright/driver/package'))
    except importlib.metadata.PackageNotFoundError:
        raise BuildError('playwright is not installed in this Python, so Playwright\'s NOTICE cannot be shipped; '
                         'run build.py with the project .venv after pip install -r requirements-lock.txt')
    absent = [name for name in PLAYWRIGHT_TEXTS if not (folder / name).is_file()]
    if absent:
        raise BuildError(f'{folder} lacks ' + ', '.join(absent))
    return folder


def read_node_version(exe: Path) -> str:
    """``node.exe --version`` of the Node.js inside patchright's driver ('24.22.1')."""
    try:
        output = subprocess.run([str(exe), '--version'], capture_output=True, text=True, timeout=60,
                                creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise BuildError(f'{exe} --version failed ({error})')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', output):
        raise BuildError(f'{exe} --version printed {output!r}')
    return output[1:]


def third_party_text(app_version: str, manifest: list, licence_files: list, runtime: dict, node: dict,
                     notices: list, packages: list) -> str:
    """THIRD-PARTY.txt: every bundled program and library with version, licence, source and licence text."""
    tools = load_prepare_vendor()
    texts = lambda *parts: TEXT + package_path(*LICENCES, *parts)         # noqa: E731
    ytdlp, deno, ffmpeg = (tools.entry_for(manifest, name) for name in ('yt-dlp', 'deno', 'ffmpeg'))
    tag = lambda entry: entry.get('tag') or entry['version']               # noqa: E731
    build = ffmpeg.get('build') or tools.ffmpeg_build(ffmpeg.get('asset', ''))
    commit = re.fullmatch(r'N-\d+-g([0-9a-f]{7,40})', build or '')
    if not commit:
        raise BuildError(f'the FFmpeg in vendor/ has no known source commit ({ffmpeg.get("asset")}); prepare vendor/ '
                         'from vendor.lock.json (python prepare_vendor.py) before a release')
    commit = commit.group(1)
    short = f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    lines = [
        f'Third-party software in {APP_NAME} {app_version}',
        f'{APP_NAME} {app_version} 中的第三方软件',
        '',
        f'{APP_NAME} itself is under the MIT License: {PROJECT}',
        'It ships the programs and libraries below; each keeps its own license. "License text" names the file that',
        'holds it, relative to this folder. The last section lists every Python package the program is built from;',
        'a few of them are build tools only and are not in this package.',
        '本程序（MIT 许可证）随附下列第三方程序和库，各自遵循自己的许可证。"License text" 是许可文本所在的文件，路径相对本文件夹。',
        '最后一节列出构建本程序所用的全部 Python 包，其中少数只是构建工具，不在本包里。',
        '',
        'FFmpeg in this package is a GPL build (GPL-3.0-or-later). Its entry says where its source and build scripts are.',
        '本包里的 FFmpeg 是 GPL 构建（GPL-3.0-or-later），源码和构建脚本的位置见 FFmpeg 一条。',
        '',
        '== Programs ==',
        '',
        f'yt-dlp {tag(ytdlp)}',
        '  License: Unlicense; the libraries built into yt-dlp.exe are listed in yt-dlp_THIRD_PARTY_LICENSES.txt',
        f'  Source: https://github.com/yt-dlp/yt-dlp/tree/{tag(ytdlp)}',
        f'  Download: {ytdlp.get("url")} (SHA-256 {ytdlp.get("sha256")})',
        '  Files: ' + package_path('_internal', 'vendor', 'yt-dlp.exe'),
        *[texts(name) for name in licence_files if name.startswith('yt-dlp_')],
        '',
        f'FFmpeg {build}',
        '  License: GPL-3.0-or-later. This build is configured with --enable-gpl --enable-version3 and links GPL',
        '    libraries (x264, x265 and others), so it is distributed under the GNU General Public License version 3.',
        f'  Build: {tag(ffmpeg)} of yt-dlp/FFmpeg-Builds, file {ffmpeg.get("asset")} (SHA-256 {ffmpeg.get("sha256")})',
        f'  Download: {ffmpeg.get("url")}',
        f'  FFmpeg source: commit {commit} of https://git.ffmpeg.org/ffmpeg.git',
        f'    (mirror: https://github.com/FFmpeg/FFmpeg/commit/{commit})',
        f'  Build scripts: https://github.com/yt-dlp/FFmpeg-Builds/tree/{tag(ffmpeg)}',
        '    (they pin the source of every library linked into this build)',
        '  Files: ' + package_path('_internal', 'vendor', 'bin') + ' (ffmpeg.exe, ffprobe.exe and the DLLs beside them)',
        *[texts(name) for name in licence_files if name.startswith('ffmpeg_')],
        '',
        f'Deno {tag(deno)}',
        '  License: MIT',
        f'  Source: https://github.com/denoland/deno/tree/{tag(deno)}',
        f'  Download: {deno.get("url")} (SHA-256 {deno.get("sha256")})',
        '  Files: ' + package_path('_internal', 'vendor', 'bin', 'deno.exe'),
        *[texts(name) for name in licence_files if name.startswith('deno_')],
        '',
        f'Node.js {node["version"]}',
        '  License: MIT; the licenses of the libraries built into node.exe are in the same file',
        f'  Source: https://github.com/nodejs/node/tree/v{node["version"]}',
        f'  Part of: patchright {node["patchright"]} (the browser driver of dynamic collection)',
        '  Files: ' + package_path('_internal', 'patchright', 'driver', 'node.exe'),
        TEXT + package_path('_internal', 'patchright', 'driver', 'LICENSE'),
        '',
        f'Playwright driver {node["driver"]}',
        '  License: Apache-2.0; the npm packages inlined into its scripts are listed in the *.LICENSE files below',
        '  Source: https://github.com/microsoft/playwright, patched by https://github.com/Kaliiiiiiiiii-Vinyzu/patchright',
        f'  Part of: patchright {node["patchright"]} (patchright-core {node["driver"]})',
        '  Files: ' + package_path('_internal', 'patchright', 'driver', 'package'),
        *[texts('playwright-driver', name) for name in PLAYWRIGHT_TEXTS],
        *[TEXT + package_path('_internal', 'patchright', 'driver', *Path(path).parts) for path in node['texts']],
        '',
        '== Python runtime ==',
        '',
        f'Python {runtime["version"]}',
        '  License: PSF-2.0; the same file holds the terms of bzip2, Tcl/Tk and Tix and of the Microsoft Visual C++',
        '    runtime files that come with Python',
        f'  Source: https://github.com/python/cpython/tree/v{runtime["version"]}',
        *([f'  Build: python-build-standalone {runtime["build"]} '
           f'(https://github.com/astral-sh/python-build-standalone/tree/{runtime["build"]})'] if runtime['build'] else []),
        '  Files: ' + package_path('_internal', short) + ' and the other files of _internal that no other entry names',
        texts('python', 'LICENSE.txt'),
        '',
        f'Tcl/Tk {runtime["tcl_tk"]}',
        '  License: TCL (BSD-style)',
        '  Source: https://www.tcl-lang.org/',
        '  Files: ' + package_path('_internal', 'tcl86t.dll') + ', tk86t.dll, _tcl_data, _tk_data, tcl8',
        TEXT + package_path('_internal', '_tk_data', 'license.terms'),
        '',
        '== Libraries built into the Python runtime and the Python packages ==',
        '']
    for notice in notices:
        lines += [f'{notice["name"]} {notice["version"]}', f'  License: {notice["license"]}',
                  f'  Part of: {notice["part_of"]}', f'  Source: {notice["source"]}',
                  texts('notices', notice['destination']), '']
    lines += ['== Python packages the program is built from (requirements-lock.txt) ==', '']
    for package in packages:
        vendored = package.get('vendored') or []
        lines += [f'{package["package"]} {package["version"]} (Python package)',
                  f'  License: {package["license"]}' + ('' if package['files'] else
                                                        ' (the package carries no license file; see Source)'),
                  f'  Source: {package["source"]}',
                  *(textwrap.wrap('Includes: ' + ', '.join(vendored) + ' (their license texts are below)', width=116,
                                  initial_indent='  ', subsequent_indent='    ', break_on_hyphens=False)
                    if vendored else []),
                  *[texts('python-packages', *path.split('/')) for path in package['files']], '']
    return '\r\n'.join(lines).rstrip('\r\n') + '\r\n'


def write_third_party(stage: Path, app_version: str, lock: Path | None = None, requirements: Path = REQUIREMENTS,
                      runtime: dict | None = None, node_version=read_node_version) -> dict:
    """Step 4 (2.0.5): complete the licence texts of the staged package and write THIRD-PARTY.txt beside the exe.

    Into _internal/vendor/licenses (which already holds the tools' licences and vendor/licenses/notices): python/
    (the runtime's LICENSE.txt), python-packages/ (collected now from the installed distributions) and
    playwright-driver/ (Playwright's LICENSE and NOTICE).  The texts PyInstaller and patchright already put into the
    package (Tk's license.terms, Node's LICENSE and the driver's notices) are named where they are."""
    stage = Path(stage)
    lock = Path(lock) if lock is not None else ROOT / 'vendor.lock.json'
    internal = stage / '_internal'
    licences = stage.joinpath(*LICENCES)
    if not licences.is_dir():
        raise BuildError(f'{licences} is missing')
    tools = load_prepare_vendor()
    try:
        manifest = tools.read_manifest(internal / 'vendor')
        notices = tools.load_notices(lock)
    except tools.PrepareError as error:
        raise BuildError(str(error))
    absent = [notice['destination'] for notice in notices
              if not (licences / tools.NOTICES / notice['destination']).is_file()]
    if absent:
        raise BuildError('the package lacks the notices ' + ', '.join(absent) + ' (python prepare_vendor.py)')
    runtime = runtime or python_runtime()
    (licences / 'python').mkdir(exist_ok=True)
    shutil.copyfile(runtime['license_file'], licences / 'python' / 'LICENSE.txt')
    packages = collect_python_packages(licences / 'python-packages', requirements)
    driver = internal / 'patchright' / 'driver'
    required = [internal / '_tk_data' / 'license.terms', driver / 'node.exe', driver / 'LICENSE',
                driver / 'package' / 'ThirdPartyNotices.txt', driver / 'package' / 'package.json']
    lacking = [str(path.relative_to(stage)) for path in required if not path.is_file()]
    if lacking:
        raise BuildError('the package lacks ' + ', '.join(lacking))
    try:
        driver_version = str(json.loads((driver / 'package' / 'package.json').read_text(encoding='utf-8'))['version'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BuildError(f'the version of patchright\'s driver cannot be read ({error})')
    upstream = playwright_texts()
    (licences / 'playwright-driver').mkdir(exist_ok=True)
    for name in PLAYWRIGHT_TEXTS:
        shutil.copyfile(upstream / name, licences / 'playwright-driver' / name)
    node = {'version': node_version(driver / 'node.exe'), 'patchright': installed_version('patchright') or '?',
            'driver': driver_version,
            'texts': ['package/ThirdPartyNotices.txt']
            + sorted(path.relative_to(driver).as_posix() for path in (driver / 'package').rglob('*.LICENSE'))}
    licence_files = sorted(item.name for item in licences.iterdir() if item.is_file())
    text = third_party_text(app_version, manifest, licence_files, runtime, node, notices, packages)
    (stage / THIRD_PARTY).write_bytes(text.encode('utf-8'))
    return {'packages': len(packages), 'notices': len(notices)}


# ------------------------------------------------------------------ (2.0.5) the zip for a GitHub release

def zip_path(version: str) -> Path:
    return BUILD / f'ytdlp-desktop-{version}-win64.zip'


def zip_stamp(built: str) -> tuple:
    """The one timestamp of every zip entry: build.json's "built" (zip keeps even seconds)."""
    moment = datetime.datetime.fromisoformat(built)
    return (max(moment.year, 1980), moment.month, moment.day, moment.hour, moment.minute, moment.second // 2 * 2)


def short_path(path: str) -> str:
    """The 8.3 form of an existing ``path`` ('' when Windows gives none)."""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    except (AttributeError, OSError):
        return ''
    return buffer.value if 0 < length < len(buffer) else ''


def privacy_needles() -> list:
    """This machine's user name, user folder (also in its 8.3 form, as TEMP often is) and git e-mail, lower case, as
    UTF-8 and UTF-16 bytes."""
    found = set()
    name = os.environ.get('USERNAME', '').strip()
    if len(name) >= 3:
        found.add(name)
    home = os.environ.get('USERPROFILE', '').strip()
    short = short_path(home) if home else ''
    for folder in {home, short} - {''}:
        found |= {folder, folder.replace('\\', '/'), folder.replace('\\', '\\\\')}
    short_name = short.rstrip('\\/').rpartition('\\')[2]
    if len(short_name) >= 3 and '~' in short_name:
        found.add(short_name)
    try:
        mail = subprocess.run(['git', 'config', 'user.email'], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=30, creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        mail = ''
    if mail:
        found.add(mail)
    return sorted({text.lower().encode(encoding) for text in found for encoding in ('utf-8', 'utf-16-le')})


def check_zip(path: Path, files: list) -> None:
    """The zip holds exactly ``files`` (the release record's list) below APP_NAME/, in that order, each with its
    size and SHA-256, every non-ASCII name flagged as UTF-8."""
    expected = sorted(files, key=lambda item: item['path'])
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != [f'{APP_NAME}/{item["path"]}' for item in expected]:
            raise BuildError(f'{path.name} does not hold the files of the release record')
        for info, item in zip(infos, expected):
            if not info.filename.isascii() and not info.flag_bits & 0x800:
                raise BuildError(f'{path.name}: {info.filename} is not stored as UTF-8')
            if info.file_size != item['size'] or hashlib.sha256(archive.read(info)).hexdigest() != item['sha256']:
                raise BuildError(f'{path.name}: {info.filename} differs from the release record')


def packed_contents(path: Path, relative: str):
    """(label, bytes) of what a file of the package holds compressed, for the privacy check: each member of a .zip
    (base_library.zip, data zips), and for the program exe each entry of its PyInstaller archive and each module of
    the PYZ inside it.  A file that cannot be opened as what its name says stops the build."""
    try:
        if relative.lower().endswith('.zip'):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    yield info.filename, archive.read(info)
        elif relative == f'{APP_NAME}.exe':
            from PyInstaller.archive.readers import CArchiveReader
            package = CArchiveReader(str(path))
            for name, entry in package.toc.items():
                if entry[-1] == 'z':                                     # the PYZ of the program's modules
                    modules = package.open_embedded_archive(name)
                    for module in modules.toc:
                        yield f'{name}:{module}', modules.extract(module, raw=True) or b''   # None: namespace
                else:
                    yield name, package.extract(name)
    except Exception as error:              # PyInstaller's readers raise their own types; any of them stops the build
        raise BuildError(f'{relative} cannot be opened for the privacy check ({error})')


def write_zip(stage: Path, files: list, destination: Path, stamp: tuple, needles=()) -> str:
    """Step 6 (2.0.5): ``destination`` holds the folder APP_NAME itself with exactly the files of the release record
    (``files``), sorted by path, one timestamp, UTF-8 names (zipfile flags them), so the same stage gives the same
    bytes.  A file that differs from the record, a file the record lacks, or one that holds a privacy needle (this
    machine's user name, user folder, git e-mail), also inside a zip of the package or the exe's PYZ, stops it.  Writes <zip>.sha256 beside it; returns the SHA-256."""
    stage, destination = Path(stage), Path(destination)
    expected = sorted(files, key=lambda item: item['path'])
    present = sorted(path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file())
    if present != [item['path'] for item in expected]:
        raise BuildError(f'{stage} no longer holds exactly the files of the release record; build again')
    temporary = destination.with_name(destination.name + '.part')
    hits = []
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for item in expected:
                data = (stage / item['path']).read_bytes()
                if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise BuildError(f'{item["path"]} differs from the release record; build again')
                lowered = data.lower()
                if any(needle in lowered for needle in needles):
                    hits.append(item['path'])
                elif needles:
                    hits += [f'{item["path"]} ({label})' for label, inner in packed_contents(stage / item['path'],
                                                                                            item['path'])
                             if any(needle in inner.lower() for needle in needles)][:1]
                info = zipfile.ZipInfo(f'{APP_NAME}/{item["path"]}', date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0                  # Windows
                info.external_attr = 0x20               # FILE_ATTRIBUTE_ARCHIVE
                archive.writestr(info, data)
        if hits:
            raise BuildError('the package holds this machine\'s user name, user folder or git e-mail, so it must not be '
                             'published: ' + ', '.join(hits[:10]))
        check_zip(temporary, expected)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    digest = file_sha256(destination)
    destination.with_name(destination.name + '.sha256').write_bytes(f'{digest}  {destination.name}\n'.encode('utf-8'))
    return digest


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Build, stage and gate the release; never downloads anything.')
    parser.add_argument('--skip-tests', action='store_true',
                        help='skip step 2 (run_tests.py --all before building); the gate of step 5 always runs and '
                             'then needs build/verify/report-source.json of a green serial --all run of these sources '
                             'and of these tests (src, verification/ and vendor/manifest.json unchanged since)')
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
            run(source_tests_command(), TEST_TIMEOUT, 'run_tests.py --all')
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
        for old in (zip_path(version), zip_path(version).with_name(zip_path(version).name + '.sha256')):
            if old.exists():
                old.unlink()                # a zip is only ever the one of the last passed gate
        shutil.copytree(dist, stage)
        if tree_size(stage) != tree_size(dist):
            raise BuildError('the staged folder differs from build/dist in file count or size')
        notes = write_third_party(stage, version)
        say(f'{THIRD_PARTY} written ({notes["packages"]} Python packages, {notes["notices"]} notices)')
        exe = stage / f'{APP_NAME}.exe'
        record = {'version': version, 'source_sha256': sources, 'exe_sha256': file_sha256(exe),
                  'built': datetime.datetime.now().astimezone().isoformat(timespec='seconds')}
        temporary = stale_record.with_name(f'build.json.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, stale_record)
        count, size = tree_size(stage)
        say(f'staged {stage} ({count} files, {size / 1e6:.1f} MB); build.json written')
        remove_leftovers()

        # 5. the tier exe against the staged exe, then the release gate
        clock = time.monotonic()
        run(exe_tests_command(exe), TEST_TIMEOUT, 'run_tests.py --only-tier exe --exe')
        timings['tests_exe'] = round(time.monotonic() - clock, 1)
        clock = time.monotonic()
        run([sys.executable, '-X', 'utf8', '-B', ROOT / 'verification' / 'verify_release.py', '--stage', stage],
            GATE_TIMEOUT, 'verify_release.py')
        timings['gate'] = round(time.monotonic() - clock, 1)

        # 6. (2.0.5) the zip of exactly the files the gate recorded
        clock = time.monotonic()
        try:
            release = json.loads((BUILD / 'verify' / f'release-{version}.json').read_text(encoding='utf-8'))
            files = release['file_list']['files']
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BuildError(f'the release record of {version} cannot be read ({error})')
        archive = zip_path(version)
        digest = write_zip(stage, files, archive, zip_stamp(record['built']), privacy_needles())
        timings['zip'] = round(time.monotonic() - clock, 1)
        say(f'zip {archive} ({archive.stat().st_size / 1e6:.1f} MB, {len(files)} files), sha256 {digest}')
    except BuildError as error:
        say(f'FAILED: {error}')
        return 1
    except KeyboardInterrupt:
        say('interrupted')
        return 130
    timings['total'] = round(time.monotonic() - started, 1)
    say('timings (s): ' + ', '.join(f'{name} {value}' for name, value in timings.items()))
    say(f'done: {BUILD / "verify" / f"release-{version}.json"}, {zip_path(version)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
