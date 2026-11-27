# quick
"""Release tooling (1.6.0 prefix-release): prepare_vendor.py with vendor.lock.json, the manifest-driven vendor checks
of build.py and verify_release.py, and the per-file release list that install_release.py enforces.

No network: urllib.request.urlopen is replaced by an in-memory fake of GitHub (API, release downloads, raw files)
that fails the test on any other address.  Everything is written below this process's sandbox TEMP.
"""
import _sandbox  # noqa: F401  (first: the sandbox)
import contextlib
import datetime
import hashlib
import importlib.metadata
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
        # (2.0.5) one notice, written for the customtkinter this Python has installed (build.py checks "with")
        self.notice_text = b'zlib licence text'
        self.notices = [{'name': 'zlib', 'version': '1.3.1', 'license': 'Zlib', 'part_of': 'Python runtime: zlib1.dll',
                         'with': f'customtkinter=={importlib.metadata.version("customtkinter")}',
                         'source': 'https://github.com/madler/zlib',
                         'url': 'https://raw.githubusercontent.com/madler/zlib/v1.3.1/LICENSE',
                         'sha256': sha(self.notice_text), 'destination': 'zlib_LICENSE.txt'}]

    def serve(self, fake: FakeGitHub):
        for tool, payload in zip(self.tools, (self.ytdlp, self.deno, self.ffmpeg)):
            fake.add(tool['url'], payload)
            for licence, (_file, _destination, text) in zip(tool['licenses'], self.licences.get(tool['name'], [])):
                fake.add(licence['url'], text)
        fake.add(self.notices[0]['url'], self.notice_text)

    def write_lock(self, path: Path):
        path.write_text(json.dumps({'comment': 'test', 'updated': '2026-11-16', 'tools': self.tools,
                                    'notices': self.notices}, indent=2), encoding='utf-8')


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
        return pv.prepare(pv.load_lock(self.lock), self.vendor, self.cache, pv.load_notices(self.lock))

    def main_prepare(self, *arguments):
        output = io.StringIO()
        with patch.object(pv, 'LOCK', self.lock), patch.object(pv, 'VENDOR', self.vendor), \
                patch.object(pv, 'CACHE', self.cache), \
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
        # (2.0.5) the pinned notices, and no python-packages any more: build.py collects those at every build
        self.assertEqual(sorted(path.name for path in (self.vendor / 'licenses').iterdir() if path.is_dir()),
                         ['notices'])
        self.assertEqual((self.vendor / 'licenses' / 'notices' / 'zlib_LICENSE.txt').read_bytes(),
                         release.notice_text)
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
                         ('2026.11.23', '622ed48023cac1daa52a3bf3a6ac38f03514de214b5f0fa069389fd0f6d17e5c'))
        self.assertEqual((tools['deno']['tag'], tools['deno']['sha256']),
                         ('v2.9.8', 'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf'))
        self.assertEqual((tools['ffmpeg']['tag'], tools['ffmpeg']['asset'], tools['ffmpeg']['sha256']),
                         ('autobuild-2026-10-31-18-18', 'ffmpeg-N-128251-g6d1bfd7609-win64-gpl-shared.zip',
                          'de0839788c3f9a649dfb49f6f800a58532c5055ad9af7a75c0aa02043b880591'))
        # (2.0.5) the notices: the libraries inside the Python runtime, curl_cffi and lxml that ship no licence file
        notices = {notice['name']: notice for notice in pv.load_notices(ROOT / 'vendor.lock.json')}
        for name in ('OpenSSL', 'libffi', 'bzip2', 'XZ Utils (liblzma)', 'Expat', 'mpdecimal (libmpdec)', 'SQLite',
                     'zlib', 'curl', 'curl-impersonate', 'BoringSSL', 'Brotli', 'Zstandard', 'c-ares', 'nghttp2',
                     'ngtcp2', 'nghttp3', 'libxml2', 'libxslt', 'GNU libiconv'):
            self.assertIn(name, notices)
        self.assertEqual(len(notices), 20)
        self.assertEqual((notices['OpenSSL']['version'], notices['OpenSSL']['with']), ('3.5.9', 'python==3.11.17'))
        self.assertEqual((notices['curl']['version'], notices['curl']['with']), ('8.23.0', 'curl_cffi==0.17.0'))
        self.assertEqual((notices['libxml2']['version'], notices['libxml2']['with']), ('2.11.9', 'lxml==6.1.3'))
        requirements = (ROOT / 'requirements-lock.txt').read_text(encoding='utf-8').splitlines()
        for notice in notices.values():           # each was written for a package version the lock pins
            if not notice['with'].startswith('python=='):
                self.assertIn(notice['with'], requirements, notice['name'])

    def test_notices_are_validated(self):
        release = Release()
        for change, pattern in (({'url': 'https://example.invalid/LICENSE'}, 'raw.githubusercontent.com'),
                                ({'destination': '../x.txt'}, 'plain, unique file name'),
                                ({'with': 'customtkinter 6'}, '"with" must read'),
                                ({'sha256': 'x'}, 'sha256'),
                                ({'license': ''}, 'exactly the text fields')):
            with self.subTest(change=change):
                release.notices = [dict(Release().notices[0], **change)]
                release.write_lock(self.lock)
                with self.assertRaisesRegex(pv.PrepareError, pattern):
                    pv.load_notices(self.lock)
        release.notices = [Release().notices[0]] * 2
        release.write_lock(self.lock)
        with self.assertRaisesRegex(pv.PrepareError, 'unique'):
            pv.load_notices(self.lock)

    def test_the_build_refuses_missing_changed_or_stale_notices(self):
        release = Release()
        self.prepare(release)
        self.assertIn('vendor ok', self.check_vendor())
        notice = self.vendor / 'licenses' / 'notices' / 'zlib_LICENSE.txt'
        notice.write_bytes(b'other text')
        with self.assertRaisesRegex(build.BuildError, 'notices does not hold exactly.*zlib_LICENSE.txt'):
            self.check_vendor()
        notice.unlink()
        with self.assertRaisesRegex(build.BuildError, 'prepare_vendor.py'):
            self.check_vendor()
        notice.write_bytes(release.notice_text)
        (notice.parent / 'extra.txt').write_bytes(b'?')
        with self.assertRaisesRegex(build.BuildError, 'extra.txt'):
            self.check_vendor()
        (notice.parent / 'extra.txt').unlink()
        release.notices[0]['with'] = 'customtkinter==0.0.1'        # written for another version than installed
        release.write_lock(self.lock)
        with self.assertRaisesRegex(build.BuildError, 'describe other versions.*customtkinter==0.0.1'):
            self.check_vendor()

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
        stage = make_stage(self.work / 'stage', self.vendor, self.lock, self.requirements)
        layout = gate.check_layout(stage, source_manifest=self.vendor / 'manifest.json')
        self.assertIn('avcodec-64.dll', layout['vendor_bin'])
        self.assertIn('FFmpeg N-140000-gbbbbbbbbbb', (stage / build.THIRD_PARTY).read_text(encoding='utf-8'))


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
        release = Release()
        release.write_lock(self.lock)
        (self.vendor / 'licenses' / 'notices').mkdir()          # (2.0.5) as prepare_vendor.py leaves them
        (self.vendor / 'licenses' / 'notices' / 'zlib_LICENSE.txt').write_bytes(release.notice_text)

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
        # (2.0.5) such a vendor/ builds for the owner, but its FFmpeg ("latest") has no known source commit, so no
        # THIRD-PARTY.txt can be written for it: a package for others needs vendor/ prepared from the lock
        with self.assertRaisesRegex(build.BuildError, 'no known source commit.*prepare_vendor.py'):
            make_stage(self.work / 'stage', self.vendor, self.lock, self.requirements)

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
        self.assertNotIn(b'\r\n', self.lock.read_bytes())            # and LF, like every tracked text file
        self.assertEqual(pv.load_notices(self.lock), old.notices)    # (2.0.5) --update keeps the notices
        pv.prepare(tools, self.vendor, self.cache, pv.load_notices(self.lock))
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

def fake_runtime(folder: Path) -> dict:
    """What build.python_runtime() reports, with a licence file of its own (the tests never depend on how this
    machine's Python is installed)."""
    licence = folder / 'python-LICENSE.txt'
    licence.write_text('PSF licence text\n', encoding='utf-8')
    return {'version': '3.11.17', 'build': '20261021', 'license_file': licence, 'tcl_tk': '8.6.15'}


def make_stage(stage: Path, vendor: Path, lock: Path, requirements: Path) -> Path:
    """A minimal staged folder: exe + _internal with the given vendor/ and the files check_layout requires, completed
    by build.write_third_party as build.py step 4 does (licence texts, THIRD-PARTY.txt)."""
    internal = stage / '_internal'
    shutil.copytree(vendor, internal / 'vendor', ignore=shutil.ignore_patterns('python-packages'))   # as app.spec
    (stage / EXE).write_bytes(b'MZ staged exe ' + os.urandom(32))
    driver = internal / 'patchright' / 'driver'
    (driver / 'package' / 'lib').mkdir(parents=True)
    (driver / 'node.exe').write_bytes(b'MZ node')
    (driver / 'LICENSE').write_text('Node.js is licensed for use as follows: MIT\n', encoding='utf-8')
    (driver / 'package' / 'ThirdPartyNotices.txt').write_text('notices\n', encoding='utf-8')
    (driver / 'package' / 'package.json').write_text('{"name": "patchright-core", "version": "1.64.2"}\n',
                                                     encoding='utf-8')
    (driver / 'package' / 'lib' / 'utilsBundle.js.LICENSE').write_text('bundled npm packages\n', encoding='utf-8')
    (internal / '_tk_data').mkdir()
    (internal / '_tk_data' / 'license.terms').write_text('Tcl/Tk terms\n', encoding='utf-8')
    (internal / 'PIL').mkdir()
    (internal / 'PIL' / '_imagingft.cp311-win_amd64.pyd').write_bytes(b'freetype')
    (internal / 'python311.dll').write_bytes(b'python ' + os.urandom(64))
    with contextlib.redirect_stdout(io.StringIO()):
        build.write_third_party(stage, '2.0.5', lock=lock, requirements=requirements,
                                runtime=fake_runtime(stage.parent), node_version=lambda _exe: '24.22.1')
    return stage


class GateLayout(Base):
    def setUp(self):
        super().setUp()
        self.prepare(Release())
        self.stage = make_stage(self.work / 'stage', self.vendor, self.lock, self.requirements)

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
        self.assertIn('THIRD-PARTY.txt', [item['path'] for item in files])


class ThirdParty(Base):
    """(2.0.5) build.write_third_party completes the licence texts and writes THIRD-PARTY.txt; the gate's layout
    step refuses a package whose notice is missing, incomplete or names texts that are not there."""

    def setUp(self):
        super().setUp()
        self.release = Release()
        self.prepare(self.release)
        self.stage = make_stage(self.work / 'stage', self.vendor, self.lock, self.requirements)
        self.text_path = self.stage / build.THIRD_PARTY
        self.licences = self.stage.joinpath(*build.LICENCES)

    def refused(self, pattern):
        with self.assertRaisesRegex(gate.GateError, pattern):
            gate.check_layout(self.stage, source_manifest=None)

    def test_the_notice_names_every_component_with_version_licence_source_and_text(self):
        raw = self.text_path.read_bytes()
        self.assertIn(b'\r\n', raw)                                      # CRLF: a text file for Windows users
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        text = raw.decode('utf-8')
        entries = gate.third_party_entries(text)
        ffmpeg = '\n'.join(entries['FFmpeg N-128251-g6d1bfd7609'])
        for needed in ('GPL-3.0-or-later', '--enable-gpl --enable-version3', 'autobuild-2026-10-31-18-18',
                       'commit 6d1bfd7609 of https://git.ffmpeg.org/ffmpeg.git',
                       'https://github.com/yt-dlp/FFmpeg-Builds/tree/autobuild-2026-10-31-18-18',
                       'ffmpeg_LICENSE.txt'):
            self.assertIn(needed, ffmpeg)
        self.assertIn('  License: Unlicense', '\n'.join(entries['yt-dlp 2026.10.21']))
        self.assertIn('  License: MIT', entries['Deno v2.9.8'])
        self.assertIn('  Source: https://github.com/nodejs/node/tree/v24.22.1', entries['Node.js 24.22.1'])
        self.assertIn('  License text: _internal\\patchright\\driver\\LICENSE', entries['Node.js 24.22.1'])
        # patchright's driver is Playwright's (Apache-2.0): its own entry with Playwright's LICENSE and NOTICE
        playwright = entries['Playwright driver 1.64.2']
        self.assertIn('  License: Apache-2.0; the npm packages inlined into its scripts are listed in the *.LICENSE '
                      'files below', playwright)
        for text in ('vendor\\licenses\\playwright-driver\\LICENSE', 'vendor\\licenses\\playwright-driver\\NOTICE',
                     'patchright\\driver\\package\\ThirdPartyNotices.txt',
                     'patchright\\driver\\package\\lib\\utilsBundle.js.LICENSE'):
            self.assertIn(f'  License text: _internal\\{text}', playwright)
        self.assertIn('Microsoft', (self.licences / 'playwright-driver' / 'NOTICE').read_text(encoding='utf-8'))
        self.assertIn('  Build: python-build-standalone 20261021 '
                      '(https://github.com/astral-sh/python-build-standalone/tree/20261021)', entries['Python 3.11.17'])
        self.assertIn('  License text: _internal\\_tk_data\\license.terms', entries['Tcl/Tk 8.6.15'])
        self.assertEqual(entries['zlib 1.3.1'], ['  License: Zlib', '  Part of: Python runtime: zlib1.dll',
                                                 '  Source: https://github.com/madler/zlib',
                                                 '  License text: _internal\\vendor\\licenses\\notices\\zlib_LICENSE.txt'])
        version = importlib.metadata.version('customtkinter')
        package = entries[f'customtkinter {version} (Python package)']
        self.assertTrue(any(line.startswith('  License text: _internal\\vendor\\licenses\\python-packages\\'
                                            'customtkinter\\') for line in package), package)
        self.assertIn('  Source: https://github.com/TomSchimansky/CustomTkinter', package)   # not its website
        # the texts are really there: the runtime's licence copied, the package's licence files collected now
        self.assertEqual((self.licences / 'python' / 'LICENSE.txt').read_text(encoding='utf-8'), 'PSF licence text\n')
        packages = json.loads((self.licences / 'python-packages' / 'packages.json').read_text(encoding='utf-8'))
        self.assertEqual([item['package'] for item in packages], ['customtkinter'])
        self.assertTrue(packages[0]['files'])
        layout = gate.check_layout(self.stage, source_manifest=None)       # the gate takes what the build wrote
        self.assertEqual(layout['third_party'], len(entries))

    def test_a_missing_notice_or_licence_text_refuses(self):
        self.text_path.rename(self.text_path.with_name('kept.txt'))
        self.refused('missing .*THIRD-PARTY.txt')
        self.text_path.with_name('kept.txt').rename(self.text_path)
        (self.licences / 'python' / 'LICENSE.txt').unlink()
        self.refused(r'Python 3.11.17: the license text _internal\\vendor\\licenses\\python\\LICENSE.txt does not exist')

    def test_a_licence_text_outside_the_package_refuses(self):
        (self.work / 'outside.txt').write_text('not in the package\n', encoding='utf-8')
        original = self.text_path.read_bytes().decode('utf-8')
        for written in ('_internal\\..\\..\\outside.txt', '_internal/../../outside.txt'):
            with self.subTest(written=written):
                self.text_path.write_bytes(original.replace('_internal\\_tk_data\\license.terms', written, 1)
                                           .encode('utf-8'))
                self.refused(r'Tcl/Tk 8.6.15: the license text .*outside.txt does not exist in the package')

    def test_a_licence_text_no_entry_names_refuses(self):
        (self.licences / 'notices' / 'stray_LICENSE.txt').write_text('?', encoding='utf-8')
        self.refused(r'names no entry for these license texts: _internal\\vendor\\licenses\\notices\\stray')

    def test_each_required_entry_and_the_bundled_versions_are_checked(self):
        original = self.text_path.read_bytes().decode('utf-8')
        for old, new, pattern in (('Tcl/Tk 8.6.15', 'Tk 8.6.15', r'no entry "Tcl/Tk \.\.\."'),
                                  ('Node.js 24.22.1', 'Node 24.22.1', r'no entry "Node.js \.\.\."'),
                                  ('yt-dlp 2026.10.21', 'yt-dlp 2026.1.1', 'no entry "yt-dlp 2026.10.21"'),
                                  ('Deno v2.9.8\r\n', 'Deno v2.9.7\r\n', 'no entry "Deno v2.9.8"'),
                                  ('FFmpeg N-128251-g6d1bfd7609', 'FFmpeg N-1-g0000000',
                                   'no entry "FFmpeg N-128251-g6d1bfd7609"'),
                                  ('is distributed under the GNU General Public License version 3', 'is free',
                                   None),
                                  ('customtkinter ', 'custom-tkinter ', r'no entry "customtkinter .* \(Python package\)"')):
            with self.subTest(old=old):
                self.assertIn(old, original)
                self.text_path.write_bytes(original.replace(old, new, 1).encode('utf-8'))
                if pattern is None:                 # "GPL-3.0-or-later" is still in the licence line
                    gate.check_layout(self.stage, source_manifest=None)
                else:
                    self.refused(pattern)
        self.text_path.write_bytes(original.replace('GPL', 'free').encode('utf-8'))
        self.refused('the FFmpeg entry does not name GPL')
        no_commit = original.replace('6d1bfd7609', '0000000').replace('FFmpeg N-128251-g0000000',
                                                                      'FFmpeg N-128251-g6d1bfd7609')
        self.text_path.write_bytes(no_commit.encode('utf-8'))
        self.refused('the FFmpeg entry does not name 6d1bfd7609')
        self.text_path.write_bytes(original.encode('utf-8'))
        gate.check_layout(self.stage, source_manifest=None)

    def test_the_build_refuses_a_package_that_lacks_what_the_notice_names(self):
        (self.stage / '_internal' / '_tk_data' / 'license.terms').unlink()
        with self.assertRaisesRegex(build.BuildError, r'lacks _internal\\_tk_data\\license.terms'):
            build.write_third_party(self.stage, '2.0.5', lock=self.lock, requirements=self.requirements,
                                    runtime=fake_runtime(self.work), node_version=lambda _exe: '24.22.1')
        self.requirements.write_text('customtkinter==6.0.0\nnot-installed-package==1.0\n', encoding='utf-8')
        with self.assertRaisesRegex(build.BuildError, 'not installed in this Python.*not-installed-package'):
            build.collect_python_packages(self.work / 'collected', self.requirements)

    def test_the_libraries_inside_a_package_and_the_fixed_facts_are_named(self):
        # setuptools carries backports.tarfile, jaraco.*, more_itertools, tomli, wheel ... inside itself, each with its
        # own .dist-info below setuptools/_vendor: their licence texts ship and the setuptools entry names them
        self.requirements.write_text('setuptools==0\npyinstaller==0\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            build.write_third_party(self.stage, '2.0.5', lock=self.lock, requirements=self.requirements,
                                    runtime=fake_runtime(self.work), node_version=lambda _exe: '24.22.1')
        setuptools = importlib.metadata.distribution('setuptools')
        inside = sorted({item.parts[2][:-len('.dist-info')] for item in setuptools.files or []
                         if len(item.parts) > 3 and item.parts[:2] == ('setuptools', '_vendor')
                         and item.parts[2].endswith('.dist-info')})
        names = {library.rsplit('-', 1)[0] for library in inside}
        self.assertLessEqual({'backports.tarfile', 'jaraco_context', 'jaraco_functools', 'jaraco.text',
                              'more_itertools', 'tomli', 'wheel'}, names)
        text = self.text_path.read_text(encoding='utf-8')
        self.assertIn('== Python packages the program is built from (requirements-lock.txt) ==', text)
        self.assertIn('a few of them are build tools only and are not in this package.', text)
        entries = gate.third_party_entries(text)
        entry = entries[f'setuptools {setuptools.version} (Python package)']
        start = next(index for index, line in enumerate(entry) if line.startswith('  Includes: '))
        end = next(index for index, line in enumerate(entry) if line.startswith('  License text: '))
        includes = ' '.join(line.strip() for line in entry[start:end])      # wrapped over a few lines
        self.assertTrue(all(len(line) <= 116 for line in entry[start:end]), entry[start:end])
        for library in inside:
            with self.subTest(library=library):
                self.assertIn(' '.join(library.rsplit('-', 1)), includes)
                self.assertTrue(any(line.startswith('  License text: _internal\\vendor\\licenses\\python-packages\\'
                                                    f'setuptools\\_vendor\\{library}\\') for line in entry), entry)
        self.assertIn('  License: GPL-2.0-or-later WITH Bootloader-exception',
                      entries[f'pyinstaller {importlib.metadata.version("pyinstaller")} (Python package)'])
        gate.check_layout(self.stage, source_manifest=None)       # every collected text is named by an entry

    def test_main_writes_the_notice_once_while_staging(self):
        calls = main_calls('write_third_party')
        self.assertEqual(len(calls), 1)


def main_calls(name: str) -> list:
    """The calls of ``name`` inside build.main (read from the source, as test_main_removes_the_leftovers does)."""
    import ast
    tree = ast.parse((ROOT / 'build.py').read_text(encoding='utf-8'))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    return [node for node in ast.walk(main) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == name]


def pyinstaller_exe(path: Path, module_text: str = 'x = 1', data: bytes = b'data') -> None:
    """A small file in the format of a PyInstaller program: 'MZ', then a CArchive with one data entry and a PYZ that
    holds one module compiled from ``module_text``."""
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
    folder = path.parent / (path.name + '.parts')
    folder.mkdir(parents=True)
    (folder / 'probe.py').write_text(module_text, encoding='utf-8')
    (folder / 'data.bin').write_bytes(data)
    ZlibArchiveWriter(str(folder / 'PYZ.pyz'), [('probe', str(folder / 'probe.py'), 'PYMODULE'),
                                                ('namespace', '-', 'PYMODULE')],        # no data, like real ones
                      code_dict={'probe': compile(module_text, 'probe.py', 'exec')})
    CArchiveWriter(str(folder / 'pkg'), [('PYZ.pyz', str(folder / 'PYZ.pyz'), False, 'z'),
                                         ('data.bin', str(folder / 'data.bin'), True, 'x')], 'python311.dll')
    path.write_bytes(b'MZ exe ' + (folder / 'pkg').read_bytes())
    shutil.rmtree(folder)


class ReleaseZip(unittest.TestCase):
    """(2.0.5) build.py step 6: the zip of the release record's files, the folder YT-DLP下载器 itself."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='release-zip-')).resolve()
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.stage = self.work / 'package-2.0.5' / APP
        (self.stage / '_internal').mkdir(parents=True)
        pyinstaller_exe(self.stage / EXE)
        with zipfile.ZipFile(self.stage / '_internal' / 'base_library.zip', 'w') as archive:
            archive.writestr('os.pyc', b'compiled')
        for path, data in (('THIRD-PARTY.txt', 'notice 声明\r\n'.encode()),
                           ('_internal/python311.dll', os.urandom(4096)),
                           ('_internal/资料/说明.txt', '中文内容'.encode()),
                           ('_internal/vendor/bin/ffmpeg.exe', b'MZ ffmpeg' * 1000)):
            (self.stage / path).parent.mkdir(parents=True, exist_ok=True)
            (self.stage / path).write_bytes(data)
        self.files = gate.stage_file_list(self.stage)
        self.stamp = build.zip_stamp('2026-11-27T21:30:45-08:00')

    def make(self, name='out.zip', needles=()):
        destination = self.work / name
        digest = build.write_zip(self.stage, self.files, destination, self.stamp, needles)
        return destination, digest

    def test_the_zip_is_the_folder_with_the_recorded_files_in_utf8_and_is_reproducible(self):
        first, digest = self.make('a.zip')
        second, _ = self.make('b.zip')
        self.assertEqual(first.read_bytes(), second.read_bytes())        # same stage, same bytes
        self.assertEqual(digest, sha(first.read_bytes()))
        self.assertEqual((self.work / 'a.zip.sha256').read_bytes(), f'{digest}  a.zip\n'.encode())
        with zipfile.ZipFile(first) as archive:
            infos = archive.infolist()
            self.assertEqual([info.filename for info in infos],
                             [f'{APP}/{item["path"]}' for item in sorted(self.files, key=lambda item: item['path'])])
            for info, item in zip(infos, sorted(self.files, key=lambda item: item['path'])):
                self.assertEqual(sha(archive.read(info)), item['sha256'])
                self.assertEqual(info.date_time, (2026, 11, 27, 21, 30, 44))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertTrue(info.flag_bits & 0x800, info.filename)   # every name has 下载器: UTF-8 flag
        self.assertIn(f'{APP}/_internal/资料/说明.txt', [info.filename for info in infos])
        self.assertFalse(list(self.work.glob('*.part')))
        build.check_zip(first, self.files)

    def test_a_stage_that_differs_from_the_record_refuses(self):
        (self.stage / '_internal' / 'extra.dll').write_bytes(b'added after the gate')
        with self.assertRaisesRegex(build.BuildError, 'no longer holds exactly the files'):
            self.make()
        (self.stage / '_internal' / 'extra.dll').unlink()
        dll = self.stage / '_internal' / 'python311.dll'
        dll.write_bytes(bytes(reversed(dll.read_bytes())))
        with self.assertRaisesRegex(build.BuildError, 'python311.dll differs from the release record'):
            self.make()
        self.assertFalse(list(self.work.glob('*.zip*')))

    def test_a_file_with_this_machines_user_name_refuses(self):
        (self.stage / '_internal' / '资料' / '说明.txt').write_bytes('C:\\Users\\zz-probe-user\\x'.encode('utf-16-le'))
        self.files = gate.stage_file_list(self.stage)
        needles = sorted({'c:\\users\\zz-probe-user'.encode(encoding) for encoding in ('utf-8', 'utf-16-le')})
        with self.assertRaisesRegex(build.BuildError, 'user name, user folder or git e-mail.*说明.txt'):
            self.make(needles=needles)
        self.assertFalse(list(self.work.glob('*.zip*')))
        self.make(needles=[b'nobody-here'])                                  # other needles: fine

    def test_a_user_folder_inside_a_zip_or_the_exes_pyz_refuses(self):
        # compressed, so a byte search of the file itself does not see it
        needles = sorted({'c:\\users\\zz-probe-user'.encode(encoding) for encoding in ('utf-8', 'utf-16-le')})
        self.make('clean.zip', needles=needles)                              # the clean stage passes
        library = self.stage / '_internal' / 'base_library.zip'
        with zipfile.ZipFile(library, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('site.pyc', 'C:\\Users\\zz-probe-user\\x'.encode('utf-16-le') * 20)
        self.assertNotIn(needles[0], library.read_bytes().lower())
        self.assertNotIn(needles[1], library.read_bytes().lower())
        self.files = gate.stage_file_list(self.stage)
        with self.assertRaisesRegex(build.BuildError, r'user folder or git e-mail.*base_library.zip \(site.pyc\)'):
            self.make(needles=needles)
        with zipfile.ZipFile(library, 'w') as archive:
            archive.writestr('os.pyc', b'compiled')
        (self.stage / EXE).unlink()
        pyinstaller_exe(self.stage / EXE, module_text="where = 'C:\\\\Users\\\\zz-probe-user\\\\work'")
        self.files = gate.stage_file_list(self.stage)
        with self.assertRaisesRegex(build.BuildError, rf'user folder or git e-mail.*{EXE} \(PYZ.pyz:probe\)'):
            self.make(needles=needles)
        (self.stage / EXE).write_bytes(b'MZ not a PyInstaller program')     # an exe it cannot look into stops it
        self.files = gate.stage_file_list(self.stage)
        with self.assertRaisesRegex(build.BuildError, 'cannot be opened for the privacy check'):
            self.make(needles=needles)
        self.assertEqual([path.name for path in self.work.glob('*.zip*')], ['clean.zip', 'clean.zip.sha256'])

    def test_a_zip_with_other_content_fails_the_check(self):
        destination, _ = self.make()
        changed = [dict(item) for item in self.files]
        changed[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(build.BuildError, 'differs from the release record'):
            build.check_zip(destination, changed)
        with self.assertRaisesRegex(build.BuildError, 'does not hold the files'):
            build.check_zip(destination, self.files[1:])

    def test_the_needles_are_this_machines_names(self):
        with patch.dict(os.environ, {'USERNAME': 'zz-probe-user', 'USERPROFILE': 'C:\\Users\\zz-probe-user'}):
            needles = build.privacy_needles()
        for text in ('zz-probe-user', 'c:\\users\\zz-probe-user', 'c:/users/zz-probe-user'):
            self.assertIn(text.encode('utf-8'), needles)
            self.assertIn(text.encode('utf-16-le'), needles)
        # the 8.3 form of the user folder (TEMP is often written so) and its short user name
        with patch.dict(os.environ, {'USERNAME': 'zz-probe-user', 'USERPROFILE': 'C:\\Users\\zz-probe-user'}), \
                patch.object(build, 'short_path', lambda path: 'C:\\HOMES~1\\ZZ-PRO~1'):     # made-up 8.3 form
            needles = build.privacy_needles()
        for text in ('zz-pro~1', 'c:\\homes~1\\zz-pro~1', 'c:/homes~1/zz-pro~1', 'c:\\\\homes~1\\\\zz-pro~1'):
            self.assertIn(text.encode('utf-8'), needles)
            self.assertIn(text.encode('utf-16-le'), needles)
        if os.environ.get('USERPROFILE'):                       # the real call gives this machine's 8.3 form
            short = build.short_path(os.environ['USERPROFILE'])
            self.assertTrue(short and os.path.samefile(short, os.environ['USERPROFILE']), short)

    def test_main_zips_once_after_the_gate(self):
        import ast
        tree = ast.parse((ROOT / 'build.py').read_text(encoding='utf-8'))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        source = ast.get_source_segment((ROOT / 'build.py').read_text(encoding='utf-8'), main)
        self.assertEqual(len(main_calls('write_zip')), 1)
        self.assertLess(source.index("'verify_release.py'"), source.index('write_zip('))
        self.assertEqual(build.zip_path('2.0.5'), build.BUILD / 'ytdlp-desktop-2.0.5-win64.zip')


class GateTestEvidence(unittest.TestCase):
    """(2.0.0 S1) Step 5 takes the pair "source report + exe report".  The pair is made here by run_tests.main with
    the very arguments build.py passes, over a throwaway test folder (one file per tier), then altered one way at a
    time: each way the old single report.json was refused has its counterpart."""

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix='gate-evidence-', dir=os.environ['TEMP']))
        cls.runner = load('run_tests_for_gate_evidence', ROOT / 'verification' / 'run_tests.py')
        tests = cls.work / 'verification'
        tests.mkdir()
        (tests / 'test_a.py').write_text('print("default")\n', encoding='utf-8')
        (tests / 'test_b.py').write_text('# tier: all\nprint("all")\n', encoding='utf-8')
        (tests / 'test_p.py').write_text('# tier: exe\nimport os\nassert os.path.isfile(os.environ["VERIFY_EXE"])\n',
                                         encoding='utf-8')
        (tests / 'test_n.py').write_text('# tier: net\nprint("SKIPPED: never part of a release run")\n',
                                         encoding='utf-8')
        (tests / 'golden').mkdir()
        (tests / 'golden' / 'g.json').write_text('{"golden": 1}\n', encoding='utf-8')
        (cls.work / 'manifest.json').write_text('[{"repository": "yt-dlp/yt-dlp"}]\n', encoding='utf-8')
        (cls.work / 'real-state').mkdir()
        cls.stage = cls.work / 'stage'
        cls.stage.mkdir()
        cls.exe = cls.stage / gate.EXE_NAME
        cls.exe.write_bytes(b'MZ staged exe')
        cls.patchers = [patch.object(cls.runner, name, value) for name, value in (
            ('VERIFICATION', tests), ('REPORT', cls.work / 'verify' / 'report.json'), ('LEAK_SIGNATURES', ()),
            ('VENDOR_MANIFEST', cls.work / 'manifest.json'),
            ('real_state_dir', lambda: cls.work / 'real-state'))]
        for patcher in cls.patchers:
            patcher.start()
        cls.source_command = build.source_tests_command()
        cls.exe_command = build.exe_tests_command(cls.exe)
        for command in (cls.source_command, cls.exe_command):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cls.runner.main([str(part) for part in command[5:]])     # after python -X utf8 -B run_tests.py
            assert code == 0, output.getvalue()
        cls.good = {name: (cls.work / 'verify' / name).read_text(encoding='utf-8')
                    for name in ('report-source.json', 'report-exe.json')}
        cls.record = cls.work / 'build.json'
        cls.record.write_text(json.dumps({'source_sha256': cls.runner.source_sha256(),
                                          'exe_sha256': sha(cls.exe.read_bytes())}), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        for patcher in cls.patchers:
            patcher.stop()
        shutil.rmtree(cls.work, True)

    def reports(self, source=None, exe=None):
        """Copies of the good pair with the changes given as functions of the parsed report (or 'missing')."""
        folder = Path(tempfile.mkdtemp(prefix='pair-', dir=self.work))
        paths = {}
        for name, change in (('report-source.json', source), ('report-exe.json', exe)):
            paths[name] = folder / name
            if change == 'missing':
                continue
            data = json.loads(self.good[name])
            if change:
                change(data)
            paths[name].write_text(json.dumps(data), encoding='utf-8')
        return paths['report-source.json'], paths['report-exe.json']

    def gate(self, source=None, exe=None, record=None):
        source_report, exe_report = self.reports(source, exe)
        return gate.check_evidence(self.stage, self.runner, source_report, exe_report, record or self.record)

    def test_build_makes_the_pair_and_the_gate_takes_it(self):
        self.assertEqual([str(part) for part in self.exe_command[4:]],
                         [str(ROOT / 'verification' / 'run_tests.py'), '--only-tier', 'exe', '--exe', str(self.exe)])
        self.assertEqual([str(part) for part in self.source_command[4:]], [str(ROOT / "verification" / "run_tests.py"), "--all"])
        evidence = self.gate()
        self.assertEqual([item['file'] for item in evidence['report']['results']], ['test_a.py', 'test_b.py'])
        self.assertEqual([(item['file'], item['status']) for item in evidence['exe_report']['results']],
                         [('test_p.py', 'passed')])
        self.assertEqual(evidence['exe_report']['tiers'], ['exe'])
        self.assertEqual((evidence['sources'], evidence['exe_hash']),
                         (self.runner.source_sha256(), sha(self.exe.read_bytes())))
        self.assertFalse((self.work / 'verify' / 'report.json').read_text(encoding='utf-8') == self.good['report-source.json'],
                         'report.json is the last run (the exe pass); the gate does not read it')
        # (2.0.0 S2 Q4) the frozen released sources are gone; (2.0.1) so is the message-key evidence of 1.8.0
        # (msg_guard, pending_keys): the reports carry no msg_guard and the gate asks for none
        self.assertFalse(hasattr(gate, 'release_sources'))
        self.assertFalse(hasattr(gate, 'check_message_evidence') or hasattr(gate, 'pending_keys'))
        self.assertNotIn('msg_guard', evidence['report'])

    def test_a_missing_report_refuses(self):
        with self.assertRaisesRegex(gate.GateError, r'missing .*report-source\.json; run build\.py'):
            self.gate(source='missing')
        with self.assertRaisesRegex(gate.GateError, r'missing .*report-exe\.json; run build\.py'):
            self.gate(exe='missing')

    def test_reports_of_other_sources_or_another_exe_refuse(self):
        def other_sources(data):
            data['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(gate.GateError, 'report-source.json was produced from other sources'):
            self.gate(source=other_sources)
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json was produced from other sources'):
            self.gate(exe=other_sources)
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json was produced with another exe'):
            self.gate(exe=lambda data: data['exe'].update(sha256='0' * 64))
        with self.assertRaisesRegex(gate.GateError, 'app_version'):
            self.gate(exe=lambda data: data.update(app_version='0.0.1'))
        record = self.work / 'other-build.json'
        record.write_text(json.dumps({'source_sha256': self.runner.source_sha256(), 'exe_sha256': '0' * 64}),
                          encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, 'build.json: the staged exe is not the one'):
            self.gate(record=record)

    def test_an_incomplete_exe_report_refuses(self):
        def without_files(data):
            data['results'] = [item for item in data['results'] if item['file'] != 'test_p.py']
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json (has no results|lacks test files)'):
            self.gate(exe=without_files)
        with self.assertRaisesRegex(gate.GateError, 'tier exe was not really exercised: test_p.py'):
            self.gate(exe=lambda data: data['results'][0].update(status='skipped'))
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json has results that did not pass'):
            self.gate(exe=lambda data: data['results'][0].update(status='failed'))
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json tiers are'):
            self.gate(exe=lambda data: data.update(tiers=['default']))
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json is a partial run'):
            self.gate(exe=lambda data: data.update(filter='packaged'))

    def test_an_incomplete_or_parallel_source_report_refuses(self):
        def without(name):
            return lambda data: data.update(results=[item for item in data['results'] if item['file'] != name])
        with self.assertRaisesRegex(gate.GateError, 'report-source.json lacks test files that exist now: test_b.py'):
            self.gate(source=without('test_b.py'))
        with self.assertRaisesRegex(gate.GateError, 'report-source.json tiers are'):
            self.gate(source=lambda data: data.update(tiers=['default']))
        for change, pattern in ((lambda data: data.update(filter='a'), 'partial run'),
                                (lambda data: data.update(quick=True), '--quick run'),
                                (lambda data: data.update(jobs=4), 'parallel run'),
                                (lambda data: data.update(jobs=None), 'parallel run'),
                                (lambda data: data.update(exe={'path': 'x', 'sha256': 'y'}), 'run of the sources'),
                                (lambda data: data.update(only_tier='all'), 'run of the sources'),
                                (lambda data: data['results'][1].update(status='failed'), 'did not pass: test_b.py'),
                                (lambda data: data['results'].append({'file': 'isolation', 'tier': 'default',
                                                                      'status': 'failed'}), 'did not pass: isolation')):
            with self.subTest(pattern=pattern), self.assertRaisesRegex(gate.GateError, pattern):
                self.gate(source=change)

    def test_a_new_test_file_needs_a_new_run(self):
        """A test file added after the reports were written: the report of its tier no longer covers the tree.
        (S1 review fix) A new file also changes tests_sha256 (refused for that first); the file-list check is
        exercised on its own with tests_sha256 held at the reports' value."""
        digest = json.loads(self.good['report-source.json'])['tests_sha256']
        for name, tier, report in (('test_c.py', '', 'report-source.json'), ('test_q.py', '# tier: exe\n', 'report-exe.json')):
            path = self.runner.VERIFICATION / name
            path.write_text(tier + 'print(1)\n', encoding='utf-8')
            try:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(gate.GateError, 'report-source.json was produced with other tests'):
                        self.gate()
                    with patch.object(self.runner, 'tests_sha256', lambda: digest), \
                            self.assertRaisesRegex(gate.GateError, f'{report} lacks test files.*{name}'):
                        self.gate()
            finally:
                path.unlink()

    def test_changed_tests_or_vendor_need_a_new_run(self):
        """(S1 review fix) The sources are the same, but a test file, a golden file or the vendor manifest changed
        after the reports were written: a green report of the old tests is not taken (build.py --skip-tests runs
        no default / all test itself)."""
        for path, report in ((self.runner.VERIFICATION / 'test_a.py', 'report-source.json'),
                             (self.runner.VERIFICATION / 'test_p.py', 'report-source.json'),
                             (self.runner.VERIFICATION / 'golden' / 'g.json', 'report-source.json'),
                             (self.work / 'manifest.json', 'report-source.json')):
            original = path.read_bytes()
            path.write_bytes(b'raise SystemExit("now failing")\n')
            try:
                with self.subTest(file=path.name), self.assertRaisesRegex(
                        gate.GateError, f'{report} was produced with other tests or vendor files'):
                    self.gate()
            finally:
                path.write_bytes(original)
        self.gate()     # restored: taken again
        with self.assertRaisesRegex(gate.GateError, 'report-exe.json was produced with other tests'):
            self.gate(exe=lambda data: data.update(tests_sha256='0' * 64))
        with self.assertRaisesRegex(gate.GateError, 'report-source.json was produced with other tests'):
            self.gate(source=lambda data: data.pop('tests_sha256'))
        self.assertEqual(json.loads(self.good['report-source.json'])['tests_sha256'], self.runner.tests_sha256())

    def test_the_report_roles_match_what_the_gate_takes(self):
        source, exe = (json.loads(self.good[name]) for name in ('report-source.json', 'report-exe.json'))
        self.assertEqual((self.runner.report_roles(source), self.runner.report_roles(exe)), (['source'], ['exe']))
        self.assertEqual(self.runner.report_roles(dict(source, jobs=4)), [])
        self.assertEqual(self.runner.report_roles(dict(exe, filter='p')), [])


# ---------------------------------------------------------------------------------------------- the installer

class InstallerFileList(Base):
    """A change to the stage after the gate (same size, added, deleted) refuses the install before anything moves."""

    def setUp(self):
        super().setUp()
        self.prepare(Release())
        self.stage = make_stage(self.work / 'src' / 'build' / 'package-1.6.0' / APP, self.vendor, self.lock,
                                self.requirements)
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
        # (2.0.5) THIRD-PARTY.txt is in the record (the zip carries it) but the install takes the exe and _internal
        self.assertIn(build.THIRD_PARTY, [item['path'] for item in self.release['file_list']['files']])
        self.assertFalse((self.target / build.THIRD_PARTY).exists())
        # ... and the receipt counts what was installed, not the whole record
        receipt = installer.plan_install(self.target, 'none', info, None, None)['receipt']
        installed = [item for item in self.release['file_list']['files'] if item['path'] != build.THIRD_PARTY]
        self.assertEqual((receipt['files'], receipt['size']),
                         (len(installed), sum(item['size'] for item in installed)))
        self.assertEqual(receipt['files'], info['files'] - 1)
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


class Leftovers(unittest.TestCase):
    def test_leftovers_that_cannot_be_removed_only_note_and_the_build_goes_on(self):
        # (2.0.1 F7) build/dist and build/work after the checked stage are removed best effort
        tried, said = [], []
        def refuse(path):
            tried.append(Path(path).name)
            raise build.BuildError(f'could not remove {path}')
        with patch.object(build, 'remove_folder', refuse), patch.object(build, 'say', said.append):
            build.remove_leftovers()                         # no exception
        self.assertEqual(tried, ['dist', 'work'])
        self.assertEqual(len(said), 2)
        self.assertTrue(all('the build goes on' in line for line in said), said)

    def test_leftovers_are_removed_without_a_note(self):
        # (2.0.2) the success path: build/dist and build/work (with files in them) are gone, nothing is said
        base = Path(tempfile.mkdtemp(prefix='leftovers-')).resolve()
        try:
            for name in ('dist', 'work'):
                (base / name / 'sub').mkdir(parents=True)
                (base / name / 'sub' / 'file.bin').write_bytes(b'x' * 10)
            (base / 'keep.txt').write_text('stays', encoding='utf-8')
            said = []
            with patch.object(build, 'BUILD', base), patch.object(build, 'say', said.append):
                build.remove_leftovers()
            self.assertFalse((base / 'dist').exists())
            self.assertFalse((base / 'work').exists())
            self.assertTrue((base / 'keep.txt').exists())            # only the two folders
            self.assertEqual(said, [])
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_main_removes_the_leftovers(self):
        # (2.0.2) build.main really calls remove_leftovers() (once, without arguments)
        import ast
        tree = ast.parse((ROOT / 'build.py').read_text(encoding='utf-8'))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'remove_leftovers']
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].args, calls[0].keywords), ([], []))


if __name__ == '__main__':
    unittest.main()
