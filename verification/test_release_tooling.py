"""Release tooling (1.6.0 prefix-release): prepare_vendor.py with vendor.lock.json, the manifest-driven vendor checks
of build.py and verify_release.py, and the per-file release list that install_release.py enforces.

No network: urllib.request.urlopen is replaced by an in-memory fake of GitHub (API, release downloads, raw files)
that fails the test on any other address.  Everything is written below this process's sandbox TEMP.
"""
import _sandbox  # noqa: F401  (first: the sandbox)
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pv = load('prepare_vendor_under_test', ROOT / 'prepare_vendor.py')
build = load('build_under_test', ROOT / 'build.py')
gate = load('verify_release_under_test', ROOT / 'verification' / 'verify_release.py')
installer = load('install_release_under_test', ROOT / 'install_release.py')

APP = installer.APP_NAME
EXE = installer.EXE_NAME
DLLS_63 = ('avcodec-63.dll', 'avdevice-63.dll', 'avfilter-12.dll', 'avformat-63.dll', 'avutil-61.dll',
           'swresample-7.dll', 'swscale-10.dll')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def ffmpeg_zip(build_name: str, dlls=DLLS_63) -> bytes:
    top = f'ffmpeg-{build_name}-win64-gpl-shared/'
    entries = {top + 'bin/' + name: b'dll ' + name.encode() + build_name.encode() for name in dlls}
    entries.update({top + 'bin/ffmpeg.exe': b'MZ ffmpeg ' + build_name.encode(),
                    top + 'bin/ffprobe.exe': b'MZ ffprobe ' + build_name.encode(),
                    top + 'bin/ffplay.exe': b'MZ ffplay (not bundled)',
                    top + 'LICENSE.txt': b'GPL v3 text', top + 'doc/ffmpeg.html': b'<html>',
                    top + 'include/libavcodec/avcodec.h': b'/* header */'})
    return make_zip(entries)


class FakeGitHub:
    """urlopen replacement: serves registered URLs, records every call, refuses anything else."""

    def __init__(self):
        self.data = {}
        self.calls = []

    def add(self, url, payload):
        self.data[url] = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, 'full_url') else str(request)
        self.calls.append(url)
        if url not in self.data:
            raise AssertionError('unexpected network access: ' + url)

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        return Response(self.data[url])


def tool_entry(name, tag, asset, payload, licences=()):
    repository = pv.REPOSITORIES[name]
    entry = {'name': name, 'repository': repository, 'tag': tag, 'asset': asset,
             'url': f'https://github.com/{repository}/releases/download/{tag}/{asset}', 'sha256': sha(payload),
             'licenses': [{'file': file, 'destination': destination,
                           'url': f'https://raw.githubusercontent.com/{repository}/{tag}/{file}',
                           'sha256': sha(text)} for file, destination, text in licences]}
    return entry


class Release:
    """One consistent set of fake upstream files and the lock that pins them."""

    def __init__(self, ytdlp_tag='2026.10.21', deno_tag='v2.9.8', ffmpeg_tag='autobuild-2026-10-31-18-18',
                 ffmpeg_build='N-128251-g6d1bfd7609', dlls=DLLS_63):
        self.ytdlp = b'MZ fake yt-dlp ' + ytdlp_tag.encode() * 50
        self.deno = make_zip({'deno.exe': b'MZ fake deno ' + deno_tag.encode()})
        self.ffmpeg = ffmpeg_zip(ffmpeg_build, dlls)
        self.ffmpeg_asset = f'ffmpeg-{ffmpeg_build}-win64-gpl-shared.zip'
        self.licences = {'yt-dlp': [('LICENSE', 'yt-dlp_LICENSE.txt', b'Unlicense'),
                                    ('THIRD_PARTY_LICENSES.txt', 'yt-dlp_THIRD_PARTY_LICENSES.txt', b'third')],
                         'deno': [('LICENSE.md', 'deno_LICENSE.txt', b'MIT deno')]}
        self.tools = [tool_entry('yt-dlp', ytdlp_tag, 'yt-dlp.exe', self.ytdlp, self.licences['yt-dlp']),
                      tool_entry('deno', deno_tag, 'deno-x86_64-pc-windows-msvc.zip', self.deno, self.licences['deno']),
                      tool_entry('ffmpeg', ffmpeg_tag, self.ffmpeg_asset, self.ffmpeg)]
        self.tools[2]['display_version'] = pv.display_version(ffmpeg_tag)
        self.tools[2]['build'] = ffmpeg_build

    def serve(self, fake: FakeGitHub):
        for tool, payload in zip(self.tools, (self.ytdlp, self.deno, self.ffmpeg)):
            fake.add(tool['url'], payload)
            for licence, (_file, _destination, text) in zip(tool['licenses'], self.licences.get(tool['name'], [])):
                fake.add(licence['url'], text)

    def write_lock(self, path: Path):
        path.write_text(json.dumps({'comment': 'test', 'updated': '2026-11-16', 'tools': self.tools}, indent=2),
                        encoding='utf-8')


class Base(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='release-tooling-')).resolve()
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.vendor = self.work / 'vendor'
        self.cache = self.work / 'build' / 'vendor-cache'
        self.lock = self.work / 'vendor.lock.json'
        self.requirements = self.work / 'requirements-lock.txt'
        self.requirements.write_text('customtkinter==6.0.0\n', encoding='utf-8')
        self.fake = FakeGitHub()
        patcher = patch('urllib.request.urlopen', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, release: Release):
        release.serve(self.fake)
        release.write_lock(self.lock)
        return pv.prepare(pv.load_lock(self.lock), self.vendor, self.cache, self.requirements)

    def main_prepare(self, *arguments):
        output = io.StringIO()
        with patch.object(pv, 'LOCK', self.lock), patch.object(pv, 'VENDOR', self.vendor), \
                patch.object(pv, 'CACHE', self.cache), patch.object(pv, 'REQUIREMENTS', self.requirements), \
                patch.object(pv, 'ROOT', self.work), contextlib.redirect_stdout(output):
            code = pv.main(list(arguments))
        return code, output.getvalue()

    def check_vendor(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build.check_vendor(self.vendor, self.lock)
        return output.getvalue()

    def leftovers(self):
        return [path for pattern in ('*.zip', '*.part') for path in self.vendor.rglob(pattern)]


# ---------------------------------------------------------------------------------------------- prepare_vendor

class PrepareFromLock(Base):
    def test_fresh_prepare_by_default_uses_the_lock_and_leaves_no_archive_in_vendor(self):
        release = Release()
        release.serve(self.fake)
        release.write_lock(self.lock)
        code, text = self.main_prepare()
        self.assertEqual(code, 0, text)
        self.assertFalse(any('api.github.com' in url for url in self.fake.calls), self.fake.calls)
        self.assertEqual(self.leftovers(), [])
        self.assertTrue((self.cache / 'ffmpeg' / 'autobuild-2026-10-31-18-18' / release.ffmpeg_asset).is_file())
        self.assertEqual(sorted(path.name for path in (self.vendor / 'bin').iterdir()),
                         sorted(DLLS_63 + ('deno.exe', 'ffmpeg.exe', 'ffprobe.exe')))     # no ffplay.exe
        self.assertEqual(sorted(path.name for path in (self.vendor / 'licenses').iterdir() if path.is_file()),
                         ['deno_LICENSE.txt', 'ffmpeg_LICENSE.txt', 'yt-dlp_LICENSE.txt',
                          'yt-dlp_THIRD_PARTY_LICENSES.txt'])
        manifest = json.loads((self.vendor / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual([item['repository'] for item in manifest],
                         ['yt-dlp/yt-dlp', 'denoland/deno', 'yt-dlp/FFmpeg-Builds'])
        self.assertEqual(manifest[0]['version'], '2026.10.21')          # updater.py reads entry 0
        self.assertEqual(manifest[2]['display_version'], '2026-10-31')  # ui_settings.py reads display_version
        self.assertNotEqual(manifest[2]['version'], 'latest')
        files = pv.manifest_files(manifest)
        self.assertEqual(files['yt-dlp.exe'], sha(release.ytdlp))
        self.assertEqual(files['bin/avcodec-63.dll'], pv.file_sha256(self.vendor / 'bin' / 'avcodec-63.dll'))
        packages = json.loads((self.vendor / 'licenses' / 'python-packages' / 'packages.json').read_text('utf-8'))
        self.assertEqual(packages[0]['package'], 'customtkinter')
        self.assertTrue(packages[0]['licenses'])
        text = self.check_vendor()
        self.assertIn('vendor ok', text)
        self.assertNotIn('note:', text)                 # vendor/ is exactly what the lock pins

    def test_a_second_run_reuses_the_cache_and_removes_old_leftovers(self):
        release = Release()
        self.prepare(release)
        (self.vendor / 'ffmpeg-master-latest-win64-gpl-shared.zip').write_bytes(b'old leftover')
        (self.vendor / 'deno.zip.part').write_bytes(b'half')
        calls = len(self.fake.calls)
        self.prepare(release)
        self.assertEqual(len(self.fake.calls), calls, 'cached archives were downloaded again')
        self.assertEqual(self.leftovers(), [])
        self.check_vendor()

    def test_a_wrong_sha256_is_refused_and_vendor_stays_as_it_was(self):
        self.prepare(Release())
        before = {path: path.read_bytes() for path in self.vendor.rglob('*') if path.is_file()}
        bad = Release(ffmpeg_tag='autobuild-2026-11-30-18-18', ffmpeg_build='N-130000-gaaaaaaaaaa')
        bad.serve(self.fake)
        bad.tools[2]['sha256'] = '0' * 64              # the pin does not match what upstream serves
        bad.write_lock(self.lock)
        code, text = self.main_prepare()
        self.assertEqual(code, 1, text)
        self.assertIn('SHA-256 mismatch', text)
        self.assertIn(bad.ffmpeg_asset, text)
        after = {path: path.read_bytes() for path in self.vendor.rglob('*') if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(list(self.cache.rglob('*.part')), [])
        self.assertFalse((self.cache / 'ffmpeg' / 'autobuild-2026-11-30-18-18' / bad.ffmpeg_asset).exists())
        self.assertEqual([path for path in self.cache.iterdir() if path.name.startswith('staging-')], [])

    def test_a_wrong_licence_sha256_is_refused(self):
        release = Release()
        release.serve(self.fake)
        release.tools[1]['licenses'][0]['sha256'] = '1' * 64
        release.write_lock(self.lock)
        code, text = self.main_prepare()
        self.assertEqual(code, 1, text)
        self.assertIn('SHA-256 mismatch for LICENSE.md', text)
        self.assertFalse((self.vendor / 'manifest.json').exists())

    def test_the_lock_is_validated(self):
        release = Release()
        release.tools[0]['url'] = 'https://example.invalid/yt-dlp.exe'
        release.write_lock(self.lock)
        with self.assertRaisesRegex(pv.PrepareError, 'yt-dlp: url'):
            pv.load_lock(self.lock)
        release = Release()
        release.tools[1]['licenses'][0]['url'] = 'https://raw.githubusercontent.com/denoland/deno/main/LICENSE.md'
        release.write_lock(self.lock)
        with self.assertRaisesRegex(pv.PrepareError, 'same tag'):
            pv.load_lock(self.lock)

    def test_the_committed_lock_pins_the_agreed_releases(self):
        tools = {tool['name']: tool for tool in pv.load_lock(ROOT / 'vendor.lock.json')}
        self.assertEqual((tools['yt-dlp']['tag'], tools['yt-dlp']['sha256']),
                         ('2026.10.21', '33121d192d706e62f684a06f80918e3f3a8ba52ac3a65213b6951c39c7d9f555'))
        self.assertEqual((tools['deno']['tag'], tools['deno']['sha256']),
                         ('v2.9.8', 'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf'))
        self.assertEqual((tools['ffmpeg']['tag'], tools['ffmpeg']['asset'], tools['ffmpeg']['sha256']),
                         ('autobuild-2026-10-31-18-18', 'ffmpeg-N-128251-g6d1bfd7609-win64-gpl-shared.zip',
                          'de0839788c3f9a649dfb49f6f800a58532c5055ad9af7a75c0aa02043b880591'))

    def test_an_ffmpeg_major_bump_builds_without_code_changes(self):
        self.prepare(Release())
        bumped = tuple(name.replace('avcodec-63', 'avcodec-64').replace('avformat-63', 'avformat-64')
                       for name in DLLS_63)
        self.prepare(Release(ffmpeg_tag='autobuild-2026-12-31-18-18', ffmpeg_build='N-140000-gbbbbbbbbbb',
                             dlls=bumped))
        names = sorted(path.name for path in (self.vendor / 'bin').iterdir())
        self.assertIn('avcodec-64.dll', names)
        self.assertNotIn('avcodec-63.dll', names)       # the old FFmpeg's DLLs do not stay behind
        self.assertIn('vendor ok', self.check_vendor())
        stage = make_stage(self.work / 'stage', self.vendor)
        layout = gate.check_layout(stage, source_manifest=self.vendor / 'manifest.json')
        self.assertIn('avcodec-64.dll', layout['vendor_bin'])


class RecordLocal(Base):
    """The owner's vendor/ was prepared by the old prepare_vendor.py: its manifest has no file list."""

    def old_vendor(self):
        (self.vendor / 'bin').mkdir(parents=True)
        (self.vendor / 'licenses').mkdir()
        engine = b'MZ old yt-dlp'
        (self.vendor / 'yt-dlp.exe').write_bytes(engine)
        for name in DLLS_63 + ('deno.exe', 'ffmpeg.exe', 'ffprobe.exe'):
            (self.vendor / 'bin' / name).write_bytes(b'old ' + name.encode())
        manifest = [{'repository': 'yt-dlp/yt-dlp', 'version': '2026.10.21', 'asset': 'yt-dlp.exe',
                     'sha256': sha(engine), 'url': 'https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.21/'
                                                   'yt-dlp.exe'},
                    {'repository': 'denoland/deno', 'version': 'v2.9.8', 'asset': 'deno-x86_64-pc-windows-msvc.zip',
                     'sha256': 'c' * 64, 'url': 'https://github.com/denoland/deno/releases/download/v2.9.8/x.zip'},
                    {'repository': 'yt-dlp/FFmpeg-Builds', 'version': 'latest',
                     'asset': 'ffmpeg-master-latest-win64-gpl-shared.zip', 'sha256': 'd' * 64,
                     'url': 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/x.zip',
                     'display_version': '2026-10-24'}]
        (self.vendor / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        Release().write_lock(self.lock)

    def test_build_refuses_an_old_manifest_with_the_offline_fix(self):
        self.old_vendor()
        with self.assertRaises(build.BuildError) as caught:
            self.check_vendor()
        self.assertIn('prepare_vendor.py --record-local', str(caught.exception))

    def test_record_local_is_offline_and_makes_the_build_check_pass(self):
        self.old_vendor()
        (self.vendor / 'ffmpeg-master-latest-win64-gpl-shared.zip').write_bytes(b'leftover of the old script')
        code, text = self.main_prepare('--record-local')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.fake.calls, [])            # no download, no API call
        self.assertEqual(self.leftovers(), [])
        manifest = json.loads((self.vendor / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest[0]['repository'], 'yt-dlp/yt-dlp')
        self.assertEqual(manifest[0]['version'], '2026.10.21')
        self.assertEqual(manifest[2]['display_version'], '2026-10-24')
        self.assertEqual(manifest[2]['sha256'], 'd' * 64)         # the recorded asset digest is kept
        files = pv.manifest_files(manifest)
        self.assertEqual(sorted(files), sorted(['yt-dlp.exe', 'bin/deno.exe', 'bin/ffmpeg.exe', 'bin/ffprobe.exe']
                                               + ['bin/' + name for name in DLLS_63]))
        self.assertIn('ffmpeg: vendor/ holds latest', text)       # the difference from the lock is shown
        output = self.check_vendor()
        self.assertIn('vendor ok', output)
        self.assertIn('note: ffmpeg', output)
        stage = make_stage(self.work / 'stage', self.vendor)
        gate.check_layout(stage, source_manifest=self.vendor / 'manifest.json')

    def test_record_local_refuses_what_it_cannot_attribute(self):
        self.old_vendor()
        (self.vendor / 'bin' / 'notes.txt').write_text('?', encoding='utf-8')
        with self.assertRaisesRegex(pv.PrepareError, 'notes.txt'):
            pv.record_local(self.vendor)
        (self.vendor / 'bin' / 'notes.txt').unlink()
        (self.vendor / 'yt-dlp.exe').write_bytes(b'MZ another engine')
        with self.assertRaisesRegex(pv.PrepareError, 'is not the file vendor/manifest.json names'):
            pv.record_local(self.vendor)

    def test_record_local_refuses_a_stale_dll_of_an_older_ffmpeg(self):
        self.old_vendor()
        manifest_before = (self.vendor / 'manifest.json').read_bytes()
        (self.vendor / 'bin' / 'avcodec-62.dll').write_bytes(b'stale avcodec of an older FFmpeg')
        code, text = self.main_prepare('--record-local')
        self.assertEqual(code, 1, text)
        self.assertIn('avcodec-62.dll, avcodec-63.dll', text)
        self.assertIn('more than one major version', text)
        self.assertEqual((self.vendor / 'manifest.json').read_bytes(), manifest_before)
        with self.assertRaises(build.BuildError):                    # the build still refuses it, too
            self.check_vendor()
        (self.vendor / 'bin' / 'avcodec-62.dll').unlink()
        code, text = self.main_prepare('--record-local')
        self.assertEqual(code, 0, text)
        self.assertIn('vendor ok', self.check_vendor())
        self.assertEqual(pv.mixed_dll_versions(['bin/avcodec-63.dll', 'bin/avformat-63.dll', 'bin/avutil-61.dll']),
                         [])
        self.assertEqual(pv.mixed_dll_versions(['bin/swscale-9.dll', 'bin/swscale-10.dll', 'bin/avutil-61.dll']),
                         [['swscale-9.dll', 'swscale-10.dll']])

    def test_build_check_notices_changed_extra_and_missing_files(self):
        self.old_vendor()
        pv.record_local(self.vendor)
        dll = self.vendor / 'bin' / 'avutil-61.dll'
        original = dll.read_bytes()
        dll.write_bytes(bytes(reversed(original)))                # same size, other content
        with self.assertRaisesRegex(build.BuildError, 'differ from the SHA-256'):
            self.check_vendor()
        dll.write_bytes(original)
        (self.vendor / 'bin' / 'avcodec-64.dll').write_bytes(b'stray')
        with self.assertRaisesRegex(build.BuildError, 'does not list'):
            self.check_vendor()
        (self.vendor / 'bin' / 'avcodec-64.dll').unlink()
        (self.vendor / 'bin' / 'swscale-10.dll').unlink()
        with self.assertRaisesRegex(build.BuildError, 'missing: swscale-10.dll'):
            self.check_vendor()


class UpdateLock(Base):
    def api(self, release: Release, ffmpeg_releases):
        for tool in release.tools[:2]:
            self.fake.add(f'https://api.github.com/repos/{tool["repository"]}/releases/latest', {
                'tag_name': tool['tag'], 'assets': [
                    {'name': 'other.txt', 'digest': 'sha256:' + '0' * 64},
                    {'name': tool['asset'], 'digest': 'sha256:' + tool['sha256'],
                     'browser_download_url': tool['url']}]})
        self.fake.add('https://api.github.com/repos/yt-dlp/FFmpeg-Builds/releases?per_page=100&page=1', ffmpeg_releases)
        self.fake.add('https://api.github.com/repos/yt-dlp/FFmpeg-Builds/releases?per_page=100&page=2', [])
        release.serve(self.fake)

    def test_update_picks_newest_releases_and_the_newest_month_end_ffmpeg(self):
        old = Release()
        old.write_lock(self.lock)
        new = Release(ytdlp_tag='2026.11.30', deno_tag='v2.10.0', ffmpeg_tag='autobuild-2026-10-31-18-18')

        def ffmpeg_release(tag, build_name, digest):
            return {'tag_name': tag, 'assets': [
                {'name': f'ffmpeg-{build_name}-win64-gpl-shared.zip', 'digest': 'sha256:' + digest},
                {'name': f'ffmpeg-{build_name}-win64-gpl.zip', 'digest': 'sha256:' + '9' * 64}]}

        listing = [{'tag_name': 'latest', 'assets': []},
                   ffmpeg_release('autobuild-2026-11-15-12-50', 'N-129000-gcccccccccc', '1' * 64),  # month not over
                   ffmpeg_release('autobuild-2026-10-31-18-18', 'N-128251-g6d1bfd7609', new.tools[2]['sha256']),
                   ffmpeg_release('autobuild-2026-10-30-12-00', 'N-128200-gdddddddddd', '2' * 64),
                   ffmpeg_release('autobuild-2026-09-30-18-10', 'N-127000-geeeeeeeeee', '3' * 64)]
        self.api(new, listing)
        tools = pv.update_lock(self.lock, self.cache, today=datetime.date(2026, 11, 16))
        named = {tool['name']: tool for tool in tools}
        self.assertEqual(named['yt-dlp']['tag'], '2026.11.30')
        self.assertEqual(named['deno']['tag'], 'v2.10.0')
        self.assertEqual(named['ffmpeg']['tag'], 'autobuild-2026-10-31-18-18')
        self.assertEqual(named['ffmpeg']['asset'], 'ffmpeg-N-128251-g6d1bfd7609-win64-gpl-shared.zip')
        self.assertEqual(named['ffmpeg']['display_version'], '2026-10-31')
        self.assertEqual(named['yt-dlp']['licenses'][0]['url'],
                         'https://raw.githubusercontent.com/yt-dlp/yt-dlp/2026.11.30/LICENSE')
        self.assertEqual(named['yt-dlp']['licenses'][0]['sha256'], sha(b'Unlicense'))
        self.assertEqual(pv.load_lock(self.lock), tools)             # the rewritten lock is valid
        pv.prepare(tools, self.vendor, self.cache, self.requirements)
        self.assertIn('vendor ok', self.check_vendor())

    def test_update_only_rewrites_the_lock_and_leaves_vendor_alone(self):
        old = Release()
        self.prepare(old)                                            # vendor/ holds the old pins
        before = {path: path.read_bytes() for path in self.vendor.rglob('*') if path.is_file()}
        new = Release(ytdlp_tag='2026.11.10', deno_tag='v2.9.9', ffmpeg_tag='autobuild-2026-09-30-18-10',
                      ffmpeg_build='N-127000-geeeeeeeeee')
        listing = [{'tag_name': 'autobuild-2026-09-30-18-10', 'assets': [
            {'name': new.ffmpeg_asset, 'digest': 'sha256:' + new.tools[2]['sha256']}]}]
        calls = len(self.fake.calls)
        self.api(new, listing)
        code, text = self.main_prepare('--update')
        self.assertEqual(code, 0, text)
        named = {tool['name']: tool for tool in pv.load_lock(self.lock)}
        self.assertEqual((named['yt-dlp']['tag'], named['deno']['tag'], named['ffmpeg']['tag']),
                         ('2026.11.10', 'v2.9.9', 'autobuild-2026-09-30-18-10'))
        after = {path: path.read_bytes() for path in self.vendor.rglob('*') if path.is_file()}
        self.assertEqual(after, before, '--update changed vendor/')
        self.assertFalse([url for url in self.fake.calls[calls:] if '/releases/download/' in url],
                         'the new release assets were downloaded')
        self.assertIn('vendor/ was not changed', text)
        self.assertIn('python prepare_vendor.py', text)
        self.assertNotIn('All vendor components ready', text)
        self.assertIn('note:', self.check_vendor())                 # vendor/ now differs from the new lock

    def test_update_refuses_an_asset_without_digest(self):
        Release().write_lock(self.lock)
        before = self.lock.read_bytes()
        new = Release()
        self.api(new, [])
        self.fake.add('https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest', {
            'tag_name': '2026.11.30', 'assets': [{'name': 'yt-dlp.exe'}]})
        with self.assertRaisesRegex(pv.PrepareError, 'no SHA-256 digest'):
            pv.update_lock(self.lock, self.cache, today=datetime.date(2026, 11, 16))
        self.assertEqual(self.lock.read_bytes(), before)


# ---------------------------------------------------------------------------------------------- the gate's layout

def make_stage(stage: Path, vendor: Path) -> Path:
    """A minimal staged folder: exe + _internal with the given vendor/ and the files check_layout requires."""
    internal = stage / '_internal'
    shutil.copytree(vendor, internal / 'vendor')
    (stage / EXE).write_bytes(b'MZ staged exe ' + os.urandom(32))
    (internal / 'patchright' / 'driver').mkdir(parents=True)
    (internal / 'patchright' / 'driver' / 'node.exe').write_bytes(b'MZ node')
    (internal / 'PIL').mkdir()
    (internal / 'PIL' / '_imagingft.cp311-win_amd64.pyd').write_bytes(b'freetype')
    (internal / 'python311.dll').write_bytes(b'python ' + os.urandom(64))
    return stage


class GateLayout(Base):
    def setUp(self):
        super().setUp()
        self.prepare(Release())
        self.stage = make_stage(self.work / 'stage', self.vendor)

    def test_layout_takes_the_bin_list_from_the_manifest(self):
        layout = gate.check_layout(self.stage, source_manifest=self.vendor / 'manifest.json')
        self.assertEqual(layout['vendor_bin'], sorted(DLLS_63 + ('deno.exe', 'ffmpeg.exe', 'ffprobe.exe')))

    def test_layout_refuses_an_extra_or_changed_bin_file(self):
        extra = self.stage / '_internal' / 'vendor' / 'bin' / 'avcodec-62.dll'
        extra.write_bytes(b'old dll')
        with self.assertRaisesRegex(gate.GateError, 'exactly the files the manifest lists'):
            gate.check_layout(self.stage, source_manifest=None)
        extra.unlink()
        dll = self.stage / '_internal' / 'vendor' / 'bin' / 'avformat-63.dll'
        dll.write_bytes(bytes(reversed(dll.read_bytes())))
        with self.assertRaisesRegex(gate.GateError, 'differ from the SHA-256'):
            gate.check_layout(self.stage, source_manifest=None)

    def test_layout_refuses_a_manifest_without_file_list(self):
        path = self.stage / '_internal' / 'vendor' / 'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        for item in manifest:
            item.pop('files')
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, 'record-local'):
            gate.check_layout(self.stage, source_manifest=None)

    def test_layout_requires_ffmpeg_ffprobe_and_deno_in_the_manifest(self):
        path = self.stage / '_internal' / 'vendor' / 'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        del manifest[2]['files']['bin/ffprobe.exe']
        path.write_text(json.dumps(manifest), encoding='utf-8')
        (self.stage / '_internal' / 'vendor' / 'bin' / 'ffprobe.exe').unlink()
        with self.assertRaisesRegex(gate.GateError, 'does not list bin/ffprobe.exe'):
            gate.check_layout(self.stage, source_manifest=None)

    def test_file_list_and_its_binding_hash_agree_between_gate_and_installer(self):
        files = gate.stage_file_list(self.stage)
        self.assertEqual(files, sorted(installer.file_entries(self.stage), key=lambda item: item['path']))
        self.assertEqual(gate.file_list_sha256(files), installer.list_sha256(files))
        self.assertIn(EXE, [item['path'] for item in files])
        self.assertIn('_internal/vendor/bin/ffmpeg.exe', [item['path'] for item in files])


# ---------------------------------------------------------------------------------------------- the installer

class InstallerFileList(Base):
    """A change to the stage after the gate (same size, added, deleted) refuses the install before anything moves."""

    def setUp(self):
        super().setUp()
        self.prepare(Release())
        self.stage = make_stage(self.work / 'src' / 'build' / 'package-1.6.0' / APP, self.vendor)
        files = gate.stage_file_list(self.stage)
        self.record = self.work / 'src' / 'build' / 'verify' / 'release-1.6.0.json'
        self.record.parent.mkdir(parents=True)
        self.release = {'status': 'passed', 'version': '1.6.0',
                        'exe': {'sha256': gate.file_sha256(self.stage / EXE)},
                        'file_list': {'count': len(files), 'size': sum(item['size'] for item in files),
                                      'sha256': gate.file_list_sha256(files), 'files': files}}
        self.record.write_text(json.dumps(self.release, ensure_ascii=False), encoding='utf-8')
        self.target = self.work / 'owner' / APP
        (self.target / 'data').mkdir(parents=True)
        (self.target / EXE).write_bytes(b'MZ dummy 1.5.1 ' + os.urandom(64))
        (self.target / f'{APP}-1.5.0备份.exe').write_bytes(b'MZ dummy 1.5.0')
        (self.target / 'data' / 'library.sqlite3').write_bytes(os.urandom(4096))

    def snapshot(self):
        return {path.relative_to(self.target).as_posix(): path.read_bytes() if path.is_file() else None
                for path in self.target.rglob('*')}

    def install(self):
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = installer.main(['--stage', str(self.stage), '--target', str(self.target),
                                   '--old-version', '1.5.1',
                                   '--instance-name', 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex[:12]])
        self.assertEqual(self.snapshot(), before, 'the refused install changed the target')
        return code, output.getvalue()

    def test_unchanged_stage_passes_and_its_copy_is_checked_file_by_file(self):
        info = installer.check_stage(self.stage)
        self.assertEqual(info['files'], self.release['file_list']['count'])
        with contextlib.redirect_stdout(io.StringIO()):
            installer.copy_new_program(self.stage, self.target, info)
        self.assertTrue((self.target / installer.NEW_INTERNAL / 'vendor' / 'bin' / 'ffmpeg.exe').is_file())
        installer.discard(self.target, [installer.NEW_INTERNAL, installer.NEW_EXE])

        real_copytree = shutil.copytree

        def tampering_copytree(source, destination, *args, **kwargs):
            result = real_copytree(source, destination, *args, **kwargs)   # recurses through this wrapper
            if Path(destination).name == installer.NEW_INTERNAL:
                dll = Path(destination) / 'vendor' / 'bin' / 'avutil-61.dll'
                dll.write_bytes(bytes(reversed(dll.read_bytes())))      # same size, other bytes
            return result

        with patch.object(installer.shutil, 'copytree', tampering_copytree), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(installer.InstallError) as caught:
                installer.copy_new_program(self.stage, self.target, info)
        self.assertIn('复制结果与发布验证记录的文件清单不符', str(caught.exception))
        self.assertIn('内容不同 1 个文件（_internal\\vendor\\bin\\avutil-61.dll）', str(caught.exception))
        self.assertFalse((self.target / installer.NEW_INTERNAL).exists())
        self.assertFalse((self.target / installer.NEW_EXE).exists())

    def test_a_same_size_change_after_the_gate_refuses_the_install(self):
        dll = self.stage / '_internal' / 'vendor' / 'bin' / 'avcodec-63.dll'
        dll.write_bytes(bytes(reversed(dll.read_bytes())))
        code, text = self.install()
        self.assertEqual(code, 1, text)
        self.assertIn('暂存目录与发布验证记录不符：内容不同 1 个文件（_internal\\vendor\\bin\\avcodec-63.dll）', text)
        self.assertIn('请重新运行 build.py。未做任何更改。', text)

    def test_an_added_file_after_the_gate_refuses_the_install(self):
        (self.stage / '_internal' / 'extra.dll').write_bytes(b'added later')
        code, text = self.install()
        self.assertEqual(code, 1, text)
        self.assertIn('多出 1 个文件（_internal\\extra.dll）', text)

    def test_a_deleted_file_after_the_gate_refuses_the_install(self):
        (self.stage / '_internal' / 'vendor' / 'bin' / 'swscale-10.dll').unlink()
        code, text = self.install()
        self.assertEqual(code, 1, text)
        self.assertIn('缺少 1 个文件（_internal\\vendor\\bin\\swscale-10.dll）', text)

    def test_dry_run_checks_the_list_as_well(self):
        (self.stage / '_internal' / 'extra.dll').write_bytes(b'added later')
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = installer.main(['--stage', str(self.stage), '--target', str(self.target), '--dry-run',
                                   '--instance-name', 'Local\\YtDlpDesktop.Test.' + uuid.uuid4().hex[:12]])
        self.assertEqual(code, 1, output.getvalue())
        self.assertIn('多出 1 个文件', output.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_a_record_without_or_with_an_altered_list_is_refused(self):
        del self.release['file_list']
        self.record.write_text(json.dumps(self.release), encoding='utf-8')
        code, text = self.install()
        self.assertEqual(code, 1, text)
        self.assertIn('没有逐个文件的清单', text)

        files = gate.stage_file_list(self.stage)
        tampered = [dict(item) for item in files]
        tampered[0]['sha256'] = 'f' * 64                    # list edited without its binding hash
        self.release['file_list'] = {'count': len(files), 'size': sum(item['size'] for item in files),
                                     'sha256': gate.file_list_sha256(files), 'files': tampered}
        self.record.write_text(json.dumps(self.release), encoding='utf-8')
        code, text = self.install()
        self.assertEqual(code, 1, text)
        self.assertIn('文件清单不完整或已被改动', text)


if __name__ == '__main__':
    unittest.main()
