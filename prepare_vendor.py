"""Prepare vendor/: the third-party programs bundled with the app (yt-dlp, Deno, FFmpeg).

    python prepare_vendor.py                 download exactly what vendor.lock.json pins, verify every SHA-256 and
                                             fill vendor/ (the archives stay in build/vendor-cache, never in vendor/)
    python prepare_vendor.py --update        look up the newest yt-dlp and Deno releases and the newest month-end
                                             FFmpeg build and rewrite vendor.lock.json only; vendor/ is not
                                             changed (review the new lock, then run python prepare_vendor.py)
    python prepare_vendor.py --record-local  offline: record the files now in vendor/ (yt-dlp.exe and vendor/bin,
                                             with their SHA-256) in vendor/manifest.json, without downloading

vendor/manifest.json is a list, yt-dlp first (the app reads entry 0 as the bundled engine version), one entry per
tool: repository, version, tag, asset, sha256 (of the downloaded asset), url, for FFmpeg display_version and build,
source, and files = {path inside vendor/: sha256} of what is really in vendor/.  build.py and the release gate take
the expected vendor/bin file list from it, so a newer FFmpeg with other DLL version numbers builds unchanged.
Licence files come from the same pinned tags (FFmpeg's from inside its archive).  (2.0.5) The "notices" of
vendor.lock.json, the licence texts of libraries built into the Python runtime and the Python packages that ship no
licence file of their own (OpenSSL, libffi, curl_cffi's curl stack, lxml's libxml2, ...), are downloaded by URL and
SHA-256 into vendor/licenses/notices; --update keeps them as they are.  The licence files of the Python packages
themselves are collected by build.py from the installed distributions, at every build.
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor'
LOCK = ROOT / 'vendor.lock.json'
CACHE = ROOT / 'build' / 'vendor-cache'
HEADERS = {'User-Agent': 'YtDlpDesktop-Build/1.0', 'Accept': 'application/vnd.github+json'}
TOOL_ORDER = ('yt-dlp', 'deno', 'ffmpeg')          # yt-dlp must stay first in the manifest
REPOSITORIES = {'yt-dlp': 'yt-dlp/yt-dlp', 'deno': 'denoland/deno', 'ffmpeg': 'yt-dlp/FFmpeg-Builds'}
ASSETS = {'yt-dlp': re.compile(r'yt-dlp\.exe'), 'deno': re.compile(r'deno-x86_64-pc-windows-msvc\.zip'),
          'ffmpeg': re.compile(r'ffmpeg-N-\d+-g[0-9a-f]+-win64-gpl-shared\.zip')}
LICENSES = {'yt-dlp': (('LICENSE', 'yt-dlp_LICENSE.txt'), ('THIRD_PARTY_LICENSES.txt', 'yt-dlp_THIRD_PARTY_LICENSES.txt')),
            'deno': (('LICENSE.md', 'deno_LICENSE.txt'),), 'ffmpeg': ()}
LICENSE_PREFIXES = ('yt-dlp_', 'deno_', 'ffmpeg_')
REQUIRED_BIN = ('deno.exe', 'ffmpeg.exe', 'ffprobe.exe')
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+\-]{0,120}')
SHA256 = re.compile(r'[0-9a-f]{64}')
AUTOBUILD = re.compile(r'autobuild-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})')
LEFTOVERS = ('*.zip', '*.part')
NOTICES = 'notices'                                 # vendor/licenses/notices
NOTICE_KEYS = ('name', 'version', 'license', 'part_of', 'with', 'source', 'url', 'sha256', 'destination')
NOTICE_WITH = re.compile(r'[A-Za-z0-9_.\-]+==[A-Za-z0-9_.+\-]+')
LOCK_COMMENT = ('Pinned third-party programs bundled in vendor/ and the licence texts of libraries built into the Python '
                'runtime and packages ("notices"). prepare_vendor.py downloads exactly these and verifies every '
                'SHA-256; prepare_vendor.py --update looks up newer releases of the tools and rewrites this file, '
                'keeping "notices" as they are (each names the package or Python version it was written for in '
                '"with"; build.py refuses when that differs from what it bundles).')


class PrepareError(Exception):
    """str() is the message for the person running the script."""


def say(text: str) -> None:
    print(text, flush=True)


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, data) -> None:
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    # LF on every system: vendor.lock.json is a tracked file and the repository keeps text files LF.
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    os.replace(temporary, path)


# ------------------------------------------------------------------ manifest helpers (also used by build.py)

def read_manifest(vendor: Path) -> list:
    try:
        manifest = json.loads((vendor / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise PrepareError(f'vendor/manifest.json cannot be read: {error}')
    if not isinstance(manifest, list) or not all(isinstance(item, dict) for item in manifest):
        raise PrepareError('vendor/manifest.json is not a list of tool entries')
    return manifest


def entry_for(manifest: list, name: str):
    repository = REPOSITORIES[name].lower()
    return next((item for item in manifest if str(item.get('repository', '')).lower() == repository), None)


def manifest_files(manifest: list):
    """{path inside vendor/ ('yt-dlp.exe', 'bin/x.dll'): sha256} of all tools, or None when an entry has no file
    list (a manifest written by the prepare_vendor.py of 1.6.0 and earlier)."""
    found = {}
    for name in TOOL_ORDER:
        entry = entry_for(manifest, name)
        files = entry.get('files') if entry else None
        if not isinstance(files, dict) or not files:
            return None
        for path, digest in files.items():
            found[str(path)] = str(digest).lower()
    return found


def lock_differences(manifest: list, lock_tools: list) -> list:
    """Tools whose asset in vendor/ is not the one vendor.lock.json pins (a note, never an error)."""
    notes = []
    for tool in lock_tools:
        entry = entry_for(manifest, tool['name']) or {}
        if str(entry.get('sha256', '')).lower() != tool['sha256']:
            notes.append(f'{tool["name"]}: vendor/ holds {entry.get("version", "?")} ({entry.get("asset", "?")}), '
                         f'vendor.lock.json pins {tool["tag"]} ({tool["asset"]})')
    return notes


# ------------------------------------------------------------------ the lock

def load_lock(path: Path = LOCK) -> list:
    """The three pinned tools in TOOL_ORDER, validated; PrepareError names the first problem."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        tools = {item['name']: item for item in data['tools']}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PrepareError(f'{Path(path).name} cannot be read: {error}')
    result = []
    for name in TOOL_ORDER:
        tool = tools.get(name)
        if not isinstance(tool, dict):
            raise PrepareError(f'{Path(path).name} has no entry for {name}')
        repository, tag, asset = tool.get('repository'), str(tool.get('tag', '')), str(tool.get('asset', ''))
        problem = None
        if repository != REPOSITORIES[name]:
            problem = f'repository must be {REPOSITORIES[name]}'
        elif not SAFE_NAME.fullmatch(tag):
            problem = f'tag {tag!r} is not a plain release tag'
        elif not ASSETS[name].fullmatch(asset):
            problem = f'asset {asset!r} is not a {name} asset'
        elif tool.get('url') != f'https://github.com/{repository}/releases/download/{tag}/{asset}':
            problem = 'url is not the release download address of that tag and asset'
        elif not SHA256.fullmatch(str(tool.get('sha256', ''))):
            problem = 'sha256 must be 64 lower-case hex digits'
        for licence in tool.get('licenses') or []:
            if problem:
                break
            if not SAFE_NAME.fullmatch(str(licence.get('file', ''))) or \
                    not SAFE_NAME.fullmatch(str(licence.get('destination', ''))) or \
                    not str(licence['destination']).startswith(LICENSE_PREFIXES):
                problem = f'licence entry {licence} has an unsafe name'
            elif licence.get('url') != f'https://raw.githubusercontent.com/{repository}/{tag}/{licence["file"]}':
                problem = f'licence {licence["file"]} must come from the same tag {tag}'
            elif not SHA256.fullmatch(str(licence.get('sha256', ''))):
                problem = f'licence {licence["file"]} has no valid sha256'
        if problem:
            raise PrepareError(f'{Path(path).name}, {name}: {problem}')
        result.append(tool)
    return result


def load_notices(path: Path = LOCK) -> list:
    """(2.0.5) The "notices" of the lock, validated: licence texts of libraries built into the Python runtime and the
    Python packages.  Each names the library, its version, licence, where it sits in the program ("part_of"), the
    package or Python version it was written for ("with", for example curl_cffi==0.17.0), its official source, and
    the pinned text (raw.githubusercontent.com URL, SHA-256, file name in vendor/licenses/notices).  A lock without
    the key has none."""
    try:
        notices = json.loads(Path(path).read_text(encoding='utf-8')).get('notices', [])
    except (OSError, ValueError, AttributeError) as error:
        raise PrepareError(f'{Path(path).name} cannot be read: {error}')
    if not isinstance(notices, list):
        raise PrepareError(f'{Path(path).name}: "notices" is not a list')
    seen = set()
    for notice in notices:
        if not isinstance(notice, dict) or sorted(notice) != sorted(NOTICE_KEYS) or \
                not all(isinstance(notice[key], str) and notice[key].strip() for key in NOTICE_KEYS):
            raise PrepareError(f'{Path(path).name}: notice {notice!r} needs exactly the text fields '
                               + ', '.join(NOTICE_KEYS))
        problem = None
        if not SAFE_NAME.fullmatch(notice['destination']) or notice['destination'] in seen:
            problem = f'destination {notice["destination"]!r} is not a plain, unique file name'
        elif not notice['url'].startswith('https://raw.githubusercontent.com/'):
            problem = 'url must be a raw.githubusercontent.com address'
        elif not notice['source'].startswith('https://'):
            problem = 'source must be an https address'
        elif not SHA256.fullmatch(notice['sha256']):
            problem = 'sha256 must be 64 lower-case hex digits'
        elif not NOTICE_WITH.fullmatch(notice['with']):
            problem = '"with" must read <package or python>==<version>'
        if problem:
            raise PrepareError(f'{Path(path).name}, notice {notice["name"]}: {problem}')
        seen.add(notice['destination'])
    return notices


# ------------------------------------------------------------------ downloads

def fetch_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        return json.load(response)


def download(url: str, destination: Path, expected: str | None) -> Path:
    """``url`` into ``destination`` (a cache file), streamed through a .part file and checked against ``expected``.

    A cached file with the right SHA-256 is reused.  ``expected`` None (only for --update) accepts any content.
    A mismatch deletes the download and raises PrepareError; nothing is left behind.
    """
    if not url.startswith(('https://github.com/', 'https://raw.githubusercontent.com/')):
        raise PrepareError(f'refusing to download from {url}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected and destination.is_file() and file_sha256(destination) == expected:
        say(f'  cached   {destination.name}')
        return destination
    temporary = destination.with_name(destination.name + '.part')
    digest, received, step = hashlib.sha256(), 0, 0
    try:
        request = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as handle:
            while block := response.read(1024 * 1024):
                handle.write(block)
                digest.update(block)
                received += len(block)
                if received // (20 * 1024 * 1024) != step:
                    step = received // (20 * 1024 * 1024)
                    say(f'  {destination.name}: {received / 1e6:.0f} MB')
        actual = digest.hexdigest()
        if expected and actual != expected:
            raise PrepareError(f'SHA-256 mismatch for {destination.name} ({url}): expected {expected}, received '
                               f'{actual}. The download was discarded and vendor/ was not changed. If the pinned '
                               'release was replaced upstream, run prepare_vendor.py --update and review the new lock.')
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    say(f'  verified {destination.name} ({received / 1e6:.1f} MB, sha256 {digest.hexdigest()[:16]}...)')
    return destination


def cache_folder(cache: Path, tool: dict) -> Path:
    return cache / tool['name'] / tool['tag']


def copy_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, destination: Path) -> None:
    with archive.open(member) as source, destination.open('wb') as target:
        shutil.copyfileobj(source, target)


def assemble(tools: list, cache: Path, staging: Path) -> list:
    """Download (or reuse) every pinned file and build the complete new vendor content in ``staging``."""
    if staging.exists():
        shutil.rmtree(staging)
    (staging / 'bin').mkdir(parents=True)
    (staging / 'licenses').mkdir()
    manifest = []
    for tool in tools:
        name = tool['name']
        say(f'{name} {tool["tag"]}: {tool["asset"]}')
        folder = cache_folder(cache, tool)
        archive_path = download(tool['url'], folder / tool['asset'], tool['sha256'])
        files = []
        if name == 'yt-dlp':
            shutil.copyfile(archive_path, staging / 'yt-dlp.exe')
            files.append('yt-dlp.exe')
        else:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    parts = member.filename.replace('\\', '/').split('/')
                    base = parts[-1]
                    destination = None
                    if name == 'deno' and base == 'deno.exe':
                        destination = 'bin/deno.exe'
                    elif name == 'ffmpeg' and len(parts) >= 2 and parts[-2] == 'bin' and \
                            (base.lower().endswith('.dll') or base in ('ffmpeg.exe', 'ffprobe.exe')):
                        destination = 'bin/' + base
                    elif name == 'ffmpeg' and ('license' in base.lower() or base.lower() in ('readme.txt', 'readme.md')):
                        destination = 'licenses/ffmpeg_' + base
                    if destination is None:
                        continue
                    if not SAFE_NAME.fullmatch(base):
                        raise PrepareError(f'{tool["asset"]} holds a file with an unexpected name: {member.filename}')
                    copy_member(archive, member, staging / destination)
                    if destination.startswith('bin/'):
                        files.append(destination)
        for licence in tool.get('licenses') or []:
            source = download(licence['url'], folder / 'licenses' / licence['file'], licence['sha256'])
            shutil.copyfile(source, staging / 'licenses' / licence['destination'])
        entry = {'repository': tool['repository'], 'version': tool['tag'], 'tag': tool['tag'], 'asset': tool['asset'],
                 'sha256': tool['sha256'], 'url': tool['url']}
        if name == 'ffmpeg':
            entry['display_version'] = tool.get('display_version') or display_version(tool['tag'])
            entry['build'] = tool.get('build') or ffmpeg_build(tool['asset'])
        entry['source'] = 'vendor.lock.json'
        entry['files'] = {path: file_sha256(staging / path) for path in sorted(set(files))}
        manifest.append(entry)
    missing = [name for name in REQUIRED_BIN if not (staging / 'bin' / name).is_file()]
    if missing:
        raise PrepareError('the pinned archives lack ' + ', '.join(missing))
    return manifest


def remove_leftovers(vendor: Path) -> list:
    removed = []
    for pattern in LEFTOVERS:
        for path in vendor.rglob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(path.relative_to(vendor).as_posix())
    return removed


def install_vendor(staging: Path, vendor: Path, manifest: list) -> None:
    """Replace yt-dlp.exe, the whole of bin/ and the tool licences in vendor/; the manifest is written last.

    Anything else in vendor/ stays (vendor/licenses/notices is replaced by install_notices).  An old bin/ is replaced
    as a whole, so DLLs
    of an older FFmpeg cannot remain beside the new ones.
    """
    vendor.mkdir(exist_ok=True)
    (vendor / 'licenses').mkdir(exist_ok=True)
    old_bin = vendor / f'bin.old-{os.getpid()}'
    if (vendor / 'bin').exists():
        try:
            os.replace(vendor / 'bin', old_bin)
        except OSError as error:
            raise PrepareError(f'vendor/bin cannot be replaced ({error}); close every program that uses it and run '
                               'prepare_vendor.py again. vendor/ was not changed.')
    manifest_path = vendor / 'manifest.json'
    previous_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
    if manifest_path.exists():
        manifest_path.unlink()              # an interrupted run must not look complete
    try:
        shutil.move(str(staging / 'bin'), str(vendor / 'bin'))
    except OSError as error:
        if old_bin.exists() and not (vendor / 'bin').exists():
            os.replace(old_bin, vendor / 'bin')
            if previous_manifest is not None:
                manifest_path.write_bytes(previous_manifest)
        raise PrepareError(f'the new vendor/bin could not be moved into place ({error}); vendor/ was left as it was.')
    # From here on build.py's hash check notices any half-finished state (the manifest is written last).
    shutil.copyfile(staging / 'yt-dlp.exe', vendor / 'yt-dlp.exe')
    for path in (vendor / 'licenses').iterdir():
        if path.is_file() and path.name.startswith(LICENSE_PREFIXES):
            path.unlink()
    for path in (staging / 'licenses').iterdir():
        shutil.copyfile(path, vendor / 'licenses' / path.name)
    for name in remove_leftovers(vendor):
        say(f'removed download leftover vendor/{name}')
    if old_bin.exists():
        shutil.rmtree(old_bin, ignore_errors=True)
        if old_bin.exists():
            say(f'note: could not delete {old_bin.name} completely; it is not used, delete it later')
    write_json_atomic(manifest_path, manifest)


def install_notices(notices: list, vendor: Path, cache: Path) -> None:
    """(2.0.5) vendor/licenses/notices holds exactly the pinned notice texts (downloaded or reused from the cache,
    each checked against its SHA-256); the folder is rebuilt beside it and then swapped in."""
    staging = cache / f'notices-{os.getpid()}'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        if notices:
            say(f'notices: {len(notices)} licence texts')
        for notice in notices:
            source = download(notice['url'], cache / NOTICES / notice['destination'], notice['sha256'])
            shutil.copyfile(source, staging / notice['destination'])
        folder = vendor / 'licenses' / NOTICES
        (vendor / 'licenses').mkdir(parents=True, exist_ok=True)
        if folder.exists():
            shutil.rmtree(folder)
        shutil.move(str(staging), str(folder))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def prepare(tools: list, vendor: Path = VENDOR, cache: Path = CACHE, notices=()) -> list:
    staging = cache / f'staging-{os.getpid()}'
    try:
        manifest = assemble(tools, cache, staging)
        install_vendor(staging, vendor, manifest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    install_notices(list(notices), vendor, cache)
    return manifest


# ------------------------------------------------------------------ --record-local

def display_version(tag: str) -> str:
    match = AUTOBUILD.fullmatch(tag)
    return f'{match.group(1)}-{match.group(2)}-{match.group(3)}' if match else tag


def ffmpeg_build(asset: str) -> str:
    match = re.match(r'ffmpeg-(N-\d+-g[0-9a-f]+)-', asset)
    return match.group(1) if match else ''


def ffmpeg_reported_version(exe: Path):
    """``ffmpeg -version`` of the local build ('2026-10-24' for 'N-...-20261024'), or None."""
    try:
        output = subprocess.run([str(exe), '-hide_banner', '-version'], capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(rb'ffmpeg version (\S+)', output)
    if not match:
        return None
    version = match.group(1).decode('ascii', 'replace')
    dated = re.search(r'-(\d{4})(\d{2})(\d{2})$', version)
    return f'{dated.group(1)}-{dated.group(2)}-{dated.group(3)}' if dated else version


VERSIONED_DLL = re.compile(r'(?i)([a-z][a-z0-9_]*)-(\d+)\.dll')


def mixed_dll_versions(files) -> list:
    """Groups of DLL names that share a library name with different major versions (avcodec-62.dll beside
    avcodec-64.dll): a stale DLL of an older FFmpeg.  ``files`` holds paths or names; each group is sorted."""
    by_library = {}
    for path in files:
        name = str(path).replace('\\', '/').rsplit('/', 1)[-1]
        match = VERSIONED_DLL.fullmatch(name)
        if match:
            by_library.setdefault(match.group(1).lower(), set()).add(name)
    return [sorted(names, key=lambda item: (len(item), item.lower()))
            for _library, names in sorted(by_library.items()) if len(names) > 1]


def record_local(vendor: Path = VENDOR) -> list:
    """Record the files now in vendor/ into vendor/manifest.json; never downloads.

    For a vendor/ prepared by an older prepare_vendor.py (its manifest has no file list).  yt-dlp.exe must still be
    the file the manifest names; deno.exe belongs to Deno, ffmpeg.exe, ffprobe.exe and every DLL to FFmpeg; any other
    file in vendor/bin is refused, and so are two major versions of one FFmpeg library (avcodec-62.dll beside
    avcodec-64.dll: a stale DLL of an older FFmpeg).  Download leftovers (*.zip, *.part) in vendor/ are deleted.
    """
    manifest = read_manifest(vendor)
    entries = {}
    for name in TOOL_ORDER:
        entries[name] = entry_for(manifest, name)
        if entries[name] is None:
            raise PrepareError(f'vendor/manifest.json has no {REPOSITORIES[name]} entry; prepare vendor/ again: '
                               'python prepare_vendor.py')
    engine = vendor / 'yt-dlp.exe'
    if not engine.is_file():
        raise PrepareError('vendor/yt-dlp.exe is missing; prepare vendor/ again: python prepare_vendor.py')
    engine_hash = file_sha256(engine)
    if engine_hash != str(entries['yt-dlp'].get('sha256', '')).lower():
        raise PrepareError(f'vendor/yt-dlp.exe (sha256 {engine_hash}) is not the file vendor/manifest.json names '
                           f'({entries["yt-dlp"].get("sha256")}); prepare vendor/ again: python prepare_vendor.py')
    bin_folder = vendor / 'bin'
    if not bin_folder.is_dir():
        raise PrepareError('vendor/bin is missing; prepare vendor/ again: python prepare_vendor.py')
    files = {'yt-dlp': {'yt-dlp.exe': engine_hash}, 'deno': {}, 'ffmpeg': {}}
    unknown = []
    for path in sorted(bin_folder.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            unknown.append(path.name + '\\')
        elif path.name == 'deno.exe':
            files['deno']['bin/deno.exe'] = file_sha256(path)
        elif path.name in ('ffmpeg.exe', 'ffprobe.exe') or path.name.lower().endswith('.dll'):
            files['ffmpeg']['bin/' + path.name] = file_sha256(path)
        else:
            unknown.append(path.name)
    if unknown:
        raise PrepareError('vendor/bin holds files that belong to no bundled tool: ' + ', '.join(unknown)
                           + '; remove them or prepare vendor/ again: python prepare_vendor.py')
    stale = mixed_dll_versions(files['ffmpeg'])
    if stale:
        raise PrepareError('vendor/bin holds more than one major version of the same FFmpeg library: '
                           + '; '.join(', '.join(group) for group in stale)
                           + '. One of each group was left behind by an older FFmpeg (usually the lower number). '
                           'Delete the stale DLLs and run --record-local again, or prepare vendor/ again: '
                           'python prepare_vendor.py. vendor/manifest.json was not changed.')
    missing = [name for name in REQUIRED_BIN if not (bin_folder / name).is_file()]
    if missing:
        raise PrepareError('vendor/bin lacks ' + ', '.join(missing) + '; prepare vendor/ again: python prepare_vendor.py')
    ffmpeg = entries['ffmpeg']
    if not ffmpeg.get('display_version') or ffmpeg.get('display_version') == 'latest':
        reported = ffmpeg_reported_version(bin_folder / 'ffmpeg.exe')
        if reported:
            ffmpeg['display_version'] = reported
    recorded = datetime.date.today().isoformat()
    for name in TOOL_ORDER:
        entries[name]['source'] = f'recorded from vendor/ by prepare_vendor.py --record-local on {recorded}'
        entries[name]['files'] = files[name]
    ordered = [entries[name] for name in TOOL_ORDER] + [item for item in manifest
                                                         if not any(item is entry for entry in entries.values())]
    for name in remove_leftovers(vendor):
        say(f'removed download leftover vendor/{name}')
    write_json_atomic(vendor / 'manifest.json', ordered)
    return ordered


# ------------------------------------------------------------------ --update

def latest_release(repository: str, pattern) -> dict:
    release = fetch_json(f'https://api.github.com/repos/{repository}/releases/latest')
    return release_asset(repository, release, pattern)


def release_asset(repository: str, release: dict, pattern) -> dict:
    tag = str(release.get('tag_name', ''))
    asset = next((item for item in release.get('assets') or [] if pattern.fullmatch(str(item.get('name', '')))), None)
    if not SAFE_NAME.fullmatch(tag) or asset is None:
        raise PrepareError(f'{repository} release {tag!r} has no asset matching {pattern.pattern}')
    digest = str(asset.get('digest') or '')
    if not digest.startswith('sha256:') or not SHA256.fullmatch(digest[7:].lower()):
        raise PrepareError(f'{repository} {tag}: GitHub gives no SHA-256 digest for {asset["name"]}')
    return {'repository': repository, 'tag': tag, 'asset': asset['name'], 'sha256': digest[7:].lower(),
            'url': f'https://github.com/{repository}/releases/download/{tag}/{asset["name"]}'}


def month_end_ffmpeg(today: datetime.date) -> dict:
    """The newest month-end autobuild of yt-dlp/FFmpeg-Builds: the last build of the newest finished month.

    FFmpeg-Builds keeps month-end builds long-term and prunes daily builds after about two weeks, so a pin to a
    month-end build stays downloadable.
    """
    repository = REPOSITORIES['ffmpeg']
    best = None
    for page in (1, 2, 3):
        releases = fetch_json(f'https://api.github.com/repos/{repository}/releases?per_page=100&page={page}')
        if not releases:
            break
        for release in releases:
            match = AUTOBUILD.fullmatch(str(release.get('tag_name', '')))
            if not match or release.get('draft') or release.get('prerelease'):
                continue
            stamp = tuple(int(part) for part in match.groups())
            if stamp[:2] >= (today.year, today.month):
                continue                    # this month is not over: its last build is not known yet
            if best is None or stamp > best[0]:
                best = (stamp, release)
    if best is None:
        raise PrepareError(f'{repository} lists no autobuild of a finished month')
    return release_asset(repository, best[1], ASSETS['ffmpeg'])


def update_lock(lock: Path = LOCK, cache: Path = CACHE, today: datetime.date | None = None) -> list:
    today = today or datetime.date.today()
    found = {'yt-dlp': latest_release(REPOSITORIES['yt-dlp'], ASSETS['yt-dlp']),
             'deno': latest_release(REPOSITORIES['deno'], ASSETS['deno']),
             'ffmpeg': month_end_ffmpeg(today)}
    try:
        old = {tool['name']: tool for tool in load_lock(lock)}
    except PrepareError:
        old = {}
    notices = load_notices(lock) if Path(lock).is_file() else []   # kept as they are (a broken list stops here)
    tools = []
    for name in TOOL_ORDER:
        tool = {'name': name, **found[name], 'licenses': []}
        tool = {key: tool[key] for key in ('name', 'repository', 'tag', 'asset', 'url', 'sha256', 'licenses')}
        if name == 'ffmpeg':
            tool['display_version'] = display_version(tool['tag'])
            tool['build'] = ffmpeg_build(tool['asset'])
        for file, destination in LICENSES[name]:
            url = f'https://raw.githubusercontent.com/{tool["repository"]}/{tool["tag"]}/{file}'
            path = download(url, cache_folder(cache, tool) / 'licenses' / file, None)
            tool['licenses'].append({'file': file, 'destination': destination, 'url': url,
                                     'sha256': file_sha256(path)})
        before = old.get(name)
        say(f'{name}: ' + (f'{before["tag"]} -> {tool["tag"]}' if before and before['tag'] != tool['tag']
                           else f'{tool["tag"]} (unchanged)' if before else tool['tag']))
        tools.append(tool)
    data = {'comment': LOCK_COMMENT, 'updated': today.isoformat(), 'tools': tools, 'notices': notices}
    write_json_atomic(lock, data)
    say(f'wrote {lock.name}')
    return load_lock(lock)


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='backslashreplace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Prepare vendor/ (yt-dlp, Deno, FFmpeg) from vendor.lock.json.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--update', action='store_true',
                       help='look up the newest releases (FFmpeg: newest month-end build) and rewrite '
                            'vendor.lock.json only; vendor/ is not changed (run prepare_vendor.py afterwards)')
    group.add_argument('--record-local', action='store_true',
                       help='offline: record the files now in vendor/ in vendor/manifest.json (no download)')
    options = parser.parse_args(argv)
    started = time.monotonic()
    try:
        if options.record_local:
            manifest = record_local(VENDOR)
            count = len(manifest_files(manifest) or {})
            say(f'vendor/manifest.json now records {count} files (yt-dlp.exe and vendor/bin) with their SHA-256.')
            try:
                notes = lock_differences(manifest, load_lock(LOCK))
            except PrepareError:
                notes = []
            for note in notes:
                say(f'note: {note}')
            if notes:
                say('note: the build uses what vendor/ holds; run prepare_vendor.py (online) to switch to the lock.')
            return 0
        if options.update:
            update_lock(LOCK, CACHE)
            say(f'vendor/ was not changed. Review {LOCK.name} (for example with git diff), then run '
                'python prepare_vendor.py to download and verify the new pins into vendor/.')
            return 0
        prepare(load_lock(LOCK), VENDOR, CACHE, load_notices(LOCK))
    except PrepareError as error:
        say(f'prepare_vendor: FAILED: {error}')
        return 1
    say(f'All vendor components ready ({time.monotonic() - started:.0f} s); archives are cached in '
        f'{CACHE.relative_to(ROOT)}, not in vendor/.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
