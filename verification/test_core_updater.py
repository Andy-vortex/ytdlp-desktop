"""Engine path/version cache and the engine updater of updater.py (1.6.0 WP1).

No network: urllib.request.urlopen is replaced by FakeGitHub, which serves a release
document and the bytes of a pretend yt-dlp.exe and fails the test on any other URL.
The option probe is replaced by a stub, except in one test that runs the bundled
yt-dlp.exe without a URL (offline; it only parses its options) and in ProbeOutputTests,
which start ``python -c`` children.

Fix round 1: the probe ends the whole process tree when its time is up; the clean-up
spares the download of an update that is still running and only touches an engines folder
that is the app's own; a receipt that cannot be written leaves the bundled engine and says
so; messages carry no file paths.
"""
import _sandbox
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

import engine
import paths
import procutil
import updater

TAG = '2099.01.02'
ASSET_URL = updater.ASSET_PREFIX + TAG + '/yt-dlp.exe'
PAYLOAD = b'MZ' + bytes(range(256)) * 2600          # 665 602 bytes: three blocks of 256 KiB
OLD_ENGINE = b'MZ old updated engine'
GOOD_PROBE = (b'\r\nUsage: yt-dlp.exe [OPTIONS] URL [URL...]\r\n\r\n'
              b'yt-dlp.exe: error: You must provide at least one URL.\r\n')
BAD_PROBE = (b'\r\nUsage: yt-dlp.exe [OPTIONS] URL [URL...]\r\n\r\n'
             b'yt-dlp.exe: error: no such option: --embed-thumbnail\r\n')
PROBE_ARGS = ['--ignore-config', '--no-playlist']
VALID = object()   # install_old: write the receipt that belongs to the exe
TEMP_NAME = re.compile(r'^yt-dlp\.download\.(\d+)\.(\d+)\.exe$')
# A stand-in for a onefile engine that hangs: the first process starts a second one that
# shares its output pipes, reports both pids into the file named by its argument, and sleeps.
HANGING = ('import json, os, subprocess, sys, time\n'
           'second = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], '
           'stdout=sys.stdout, stderr=sys.stderr)\n'
           'open(sys.argv[1], "w").write(json.dumps({"pid": os.getpid(), "second": second.pid, '
           '"temp": os.environ["TEMP"]}))\n'
           'print("started", flush=True)\n'
           'time.sleep(120)\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Stream:
    """The response body of the asset download; ``on_block(index)`` runs before each block."""

    def __init__(self, data, on_block=None):
        self.data, self.position, self.index, self.on_block = data, 0, 0, on_block

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, size=-1):
        if self.position >= len(self.data):
            return b''
        if self.on_block:
            self.on_block(self.index)
        self.index += 1
        block = self.data[self.position:self.position + size] if size and size > 0 else self.data[self.position:]
        self.position += len(block)
        return block


class FakeGitHub:
    """Stands in for urllib.request.urlopen."""

    def __init__(self, payload=PAYLOAD, tag=TAG, digest='auto', size=None, url=ASSET_URL, assets=None,
                 release=None, raw=None, error=None, on_block=None):
        self.payload, self.url, self.raw, self.error, self.on_block = payload, url, raw, error, on_block
        if release is None:
            asset = {'name': 'yt-dlp.exe', 'browser_download_url': url,
                     'size': len(payload) if size is None else size}
            if digest == 'auto':
                asset['digest'] = 'sha256:' + sha(payload)
            elif digest != 'absent':
                asset['digest'] = digest
            release = {'tag_name': tag,
                       'assets': [{'name': 'yt-dlp', 'browser_download_url': url + '.bin', 'size': 1, 'digest': 'x'},
                                  asset] if assets is None else assets}
        self.release = release
        self.requests = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, timeout, {key.lower(): value for key, value in request.header_items()}))
        if url == updater.RELEASE_URL:
            if self.error is not None:
                raise self.error
            return io.BytesIO(self.raw if self.raw is not None else json.dumps(self.release).encode('utf-8'))
        if url == self.url and url.startswith(updater.ASSET_PREFIX):
            return Stream(self.payload, self.on_block)
        raise AssertionError(f'the updater contacted an unexpected address: {url}')

    @property
    def urls(self):
        return [url for url, _timeout, _headers in self.requests]


class UpdaterCase(unittest.TestCase):
    # 1.7.0 (c5-updater a): the updater first runs "<new exe> --version".  The pretend exe of these
    # tests is no program, so that run is stood in for (a version line, exit code 0) unless a test
    # class sets this; test_v170_core.EngineStartCheckTests runs it for real.
    real_version_check = False

    def setUp(self):
        if not self.real_version_check:
            patcher = mock.patch.object(updater, '_version_output', return_value=(0, TAG.encode() + b'\r\n', b''))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = _sandbox.state_patch(self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        updater.invalidate()
        self.addCleanup(updater.invalidate)
        self.engines = paths.engines_dir()
        self.bundled = paths.VENDOR / 'yt-dlp.exe'
        self.bundled_version = json.loads((paths.VENDOR / 'manifest.json').read_text(encoding='utf-8'))[0]['version']
        self.events = []
        self.cancelled = threading.Event()
        self.probes = []

    def emit(self, kind, value):
        self.events.append((kind, value))

    def kinds(self):
        return [kind for kind, _value in self.events]

    def values(self, kind):
        return [value for name, value in self.events if name == kind]

    def install_old(self, data=OLD_ENGINE, version='2001.01.01', receipt=VALID):
        self.engines.mkdir(parents=True, exist_ok=True)
        (self.engines / 'yt-dlp.exe').write_bytes(data)
        (self.engines / 'yt-dlp.json').write_text(
            json.dumps({'version': version, 'sha256': sha(data)} if receipt is VALID else receipt), encoding='utf-8')
        updater.invalidate()

    def assert_old_engine_kept(self):
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), OLD_ENGINE)
        self.assertEqual(json.loads((self.engines / 'yt-dlp.json').read_text(encoding='utf-8')),
                         {'version': '2001.01.01', 'sha256': sha(OLD_ENGINE)})
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
        self.assertEqual(updater.engine_version(), '2001.01.01')

    def leftovers(self):
        return sorted(path.name for path in self.engines.iterdir() if path.name.startswith('yt-dlp.download')) \
            if self.engines.is_dir() else []

    def probe(self, output=GOOD_PROBE, hook=None):
        """Replace the run of the downloaded file: records (command, timeout, file existed)."""
        def run(command, timeout):
            self.probes.append((list(command), timeout, Path(command[0]).is_file()))
            if hook:
                hook(command)
            if isinstance(output, BaseException):
                raise output
            return output
        return mock.patch.object(updater, '_probe_output', side_effect=run)

    def dead_pid(self):
        process = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
        process.wait(30)
        self.addCleanup(lambda: process)  # keeps the handle, so Windows cannot hand the pid out again
        self.assertFalse(procutil.alive(process.pid))
        return process.pid

    def still_alive(self, pids, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(procutil.alive(pid) for pid in pids):
            time.sleep(0.05)
        return [pid for pid in pids if procutil.alive(pid)]

    def kill_leftovers(self, pids):
        for pid in pids:
            if procutil.alive(pid):
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                               creationflags=procutil.HIDDEN, timeout=30)

    def run_update(self, github, probe_args=PROBE_ARGS, legacy=False, probe=None):
        with mock.patch('urllib.request.urlopen', github), (probe or self.probe()):
            updater.EngineUpdater(self.emit, self.cancelled, probe_args, legacy_events=legacy).run()
        self.assertEqual(self.kinds()[-1], 'update_done', 'update_done must be the last event')
        self.assertEqual(self.kinds().count('update_done'), 1)
        for url in github.urls:
            self.assertTrue(url == updater.RELEASE_URL or url.startswith(updater.ASSET_PREFIX), url)


class EnginePathTests(UpdaterCase):
    def test_without_an_update_the_bundled_engine_is_used(self):
        self.assertEqual(updater.engine_path(), self.bundled)
        self.assertEqual(updater.engine_version(), self.bundled_version)
        self.assertIs(engine.engine_path, updater.engine_path)
        self.assertIs(engine.engine_version, updater.engine_version)
        self.assertEqual(engine.build_command('https://example.invalid/a', engine.Options(self.root))[0],
                         str(self.bundled))
        self.assertFalse(paths.STATE_DIR.exists(), 'asking for the engine creates nothing')

    def test_cache_does_not_hash_an_unchanged_file_again(self):
        self.install_old()
        exe, receipt = self.engines / 'yt-dlp.exe', self.engines / 'yt-dlp.json'
        tampered = OLD_ENGINE + b' tampered'
        tampered_receipt = json.dumps({'version': '2002.02.02', 'sha256': sha(tampered)})
        # Counted from here on: every hashlib.sha256() the updater module starts.
        with mock.patch.object(updater.hashlib, 'sha256', wraps=hashlib.sha256) as hashing:
            for _ in range(3):
                self.assertEqual(updater.engine_path(), exe)
            self.assertEqual(updater.engine_version(), '2001.01.01')
            self.assertEqual(engine.build_command('https://example.invalid/a', engine.Options(self.root))[0], str(exe))
            self.assertEqual(hashing.call_count, 1, 'the exe was hashed again although nothing changed')
            # The exe changes: hashed once more, and the mismatch is remembered too.
            exe.write_bytes(tampered)
            self.assertEqual(updater.engine_path(), self.bundled)
            self.assertEqual(updater.engine_path(), self.bundled)
            self.assertEqual(updater.engine_version(), self.bundled_version)
            self.assertEqual(hashing.call_count, 2)
            # The receipt changes: hashed again, now it matches.
            receipt.write_text(tampered_receipt, encoding='utf-8')
            self.assertEqual(updater.engine_path(), exe)
            self.assertEqual(updater.engine_version(), '2002.02.02')
            self.assertEqual(hashing.call_count, 3)
            # Same size, other modification time.
            status = exe.stat()
            os.utime(exe, ns=(status.st_atime_ns, status.st_mtime_ns + 2_000_000_000))
            self.assertEqual(updater.engine_path(), exe)
            self.assertEqual(hashing.call_count, 4)
            updater.invalidate()
            self.assertEqual(updater.engine_path(), exe)
            self.assertEqual(hashing.call_count, 5)
            receipt.unlink()
            self.assertEqual(updater.engine_path(), self.bundled)
            self.assertEqual(hashing.call_count, 5, 'without a receipt nothing is hashed')

    def test_the_receipt_is_read_in_turn_with_its_writer(self):
        """bughunt-7: the window asks for the engine version while the updater thread replaces the receipt."""
        self.install_old()
        receipt = self.engines / 'yt-dlp.json'
        with mock.patch.object(paths, 'read_text', wraps=paths.read_text) as read_text:
            self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
            self.assertEqual(updater.engine_version(), '2001.01.01')
        self.assertEqual([Path(call.args[0]) for call in read_text.call_args_list], [receipt, receipt])

    def test_cache_belongs_to_one_state_folder(self):
        self.install_old()
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
        with _sandbox.state_patch(self.root / 'other-state'):
            self.assertEqual(updater.engine_path(), self.bundled)
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')

    def test_damaged_receipts_fall_back_to_the_bundled_engine(self):
        for receipt in ([], 'text', None, 7, {'version': '1'}, {'version': '1', 'sha256': None},
                        {'version': '1', 'sha256': 'not the hash'}):
            with self.subTest(receipt=receipt):
                self.install_old(receipt=receipt)
                self.assertEqual(updater.engine_path(), self.bundled)
                self.assertEqual(updater.engine_version(), self.bundled_version)
        self.install_old()
        (self.engines / 'yt-dlp.json').write_text('{"version": "1", "sha2', encoding='utf-8')
        self.assertEqual(updater.engine_path(), self.bundled)
        # A valid engine whose receipt has no version: the engine is used, the version is unknown.
        self.install_old(receipt={'sha256': sha(OLD_ENGINE)})
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
        self.assertEqual(updater.engine_version(), '已内置')
        # A folder in place of the exe.
        (self.engines / 'yt-dlp.exe').unlink()
        (self.engines / 'yt-dlp.exe').mkdir()
        updater.invalidate()
        self.assertEqual(updater.engine_path(), self.bundled)


class CheckLatestTests(UpdaterCase):
    def test_result_and_request(self):
        github = FakeGitHub()
        with mock.patch('urllib.request.urlopen', github):
            self.assertEqual(updater.check_latest(),
                             {'tag': TAG, 'url': ASSET_URL, 'size': len(PAYLOAD), 'sha256': sha(PAYLOAD)})
            updater.check_latest(timeout=15)
        self.assertEqual(github.urls, [updater.RELEASE_URL] * 2, 'only api.github.com is asked')
        self.assertEqual([timeout for _url, timeout, _headers in github.requests], [25, 15])
        self.assertEqual(github.requests[0][2]['user-agent'], 'YtDlpDesktop/1.0')
        self.assertEqual(github.requests[0][2]['accept'], 'application/vnd.github+json')
        self.assertEqual(updater.RELEASE_URL, 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest')

    def test_missing_parts_are_reported_as_empty_values(self):
        cases = [(FakeGitHub(digest=None), {'sha256': ''}),
                 (FakeGitHub(digest='absent'), {'sha256': ''}),
                 (FakeGitHub(digest='SHA256:ABC'), {'sha256': 'sha256:abc'}),
                 (FakeGitHub(digest='sha256:' + sha(PAYLOAD).upper()), {'sha256': sha(PAYLOAD)}),
                 (FakeGitHub(assets=[]), {'url': '', 'size': 0, 'sha256': ''}),
                 (FakeGitHub(assets=[{'name': 'yt-dlp_x86.exe', 'browser_download_url': ASSET_URL, 'size': 5}]),
                  {'url': '', 'size': 0, 'sha256': ''}),
                 (FakeGitHub(release={'tag_name': TAG}), {'url': '', 'size': 0, 'sha256': ''}),
                 (FakeGitHub(size='big'), {'size': 0})]
        for github, expected in cases:
            with self.subTest(expected=expected, release=github.release), mock.patch('urllib.request.urlopen', github):
                result = updater.check_latest()
                self.assertEqual(result['tag'], TAG)
                self.assertEqual({key: result[key] for key in expected}, expected)
                self.assertEqual(set(result), {'tag', 'url', 'size', 'sha256'})

    def test_network_failures_give_the_network_message(self):
        failures = [(urllib.error.URLError(TimeoutError('timed out')), '连接超时'),
                    (TimeoutError('The read operation timed out'), '连接超时'),
                    (urllib.error.URLError(OSError(11001, 'getaddrinfo failed')), 'getaddrinfo failed'),
                    (urllib.error.HTTPError(updater.RELEASE_URL, 403, 'rate limit exceeded', {}, None), 'HTTP 403'),
                    (ConnectionResetError(10054, 'connection reset'), 'connection reset')]
        for failure, reason in failures:
            with self.subTest(failure=failure), mock.patch('urllib.request.urlopen', FakeGitHub(error=failure)):
                with self.assertRaises(updater.UpdateError) as caught:
                    updater.check_latest()
                message = str(caught.exception)
                self.assertTrue(message.startswith('无法连接 GitHub，请检查网络后重试。（'), message)
                self.assertTrue(message.endswith('）'), message)
                self.assertIn(reason, message)
                self.assertNotIn('\n', message)

    def test_an_answer_that_is_not_a_release_is_refused(self):
        for raw in (b'<html>captive portal</html>', b'[]', b'{"message": "Not Found"}', b'{"tag_name": ""}',
                    b'{"tag_name": 5}', b'\xff\xfe'):
            with self.subTest(raw=raw), mock.patch('urllib.request.urlopen', FakeGitHub(raw=raw)):
                with self.assertRaises(updater.UpdateError) as caught:
                    updater.check_latest()
                self.assertEqual(str(caught.exception), '官方发布信息无法识别，已保留现有引擎。')


class EngineUpdaterTests(UpdaterCase):
    def test_success_writes_exe_and_receipt(self):
        self.install_old()
        github = FakeGitHub()
        self.run_update(github)
        self.assertEqual(github.urls, [updater.RELEASE_URL, ASSET_URL])
        self.assertEqual(self.kinds(), ['update_status', 'update_status', 'update_progress', 'update_progress',
                                        'update_progress', 'update_status', 'updated', 'update_done'])
        self.assertEqual(self.values('update_status')[0], '正在检查官方更新…')
        self.assertTrue(all(isinstance(text, str) and text for text in self.values('update_status')))
        progress = self.values('update_progress')
        self.assertEqual([set(value) for value in progress], [{'fraction', 'downloaded', 'total'}] * 3)
        self.assertEqual([value['downloaded'] for value in progress], [262144, 524288, len(PAYLOAD)])
        self.assertEqual({value['total'] for value in progress}, {len(PAYLOAD)})
        self.assertEqual(progress[-1]['fraction'], 1.0)
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual(self.values('update_done'), [{'cancelled': False}])
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), PAYLOAD)
        self.assertEqual(json.loads((self.engines / 'yt-dlp.json').read_text(encoding='utf-8')),
                         {'version': TAG, 'sha256': sha(PAYLOAD)})
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()), ['yt-dlp.exe', 'yt-dlp.json'])
        # The cache was invalidated: the new engine is used at once.
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
        self.assertEqual(updater.engine_version(), TAG)
        # The probe ran the downloaded file with the app's options (how: ProbeOutputTests).
        self.assertEqual(len(self.probes), 1)
        command, timeout, existed = self.probes[0]
        self.assertTrue(existed, 'the probe must run the downloaded file')
        self.assertEqual(Path(command[0]).parent, self.engines)
        match = TEMP_NAME.match(Path(command[0]).name)
        self.assertTrue(match, command[0])
        self.assertEqual(int(match.group(1)), os.getpid())
        self.assertEqual(command[1:], PROBE_ARGS)
        self.assertEqual((timeout, updater.PROBE_TIMEOUT), (60, 60))

    def test_update_forgets_the_cached_answer_even_when_the_file_stamps_do_not_change(self):
        # Coarse file times can leave size and time of the exe and of the receipt unchanged.
        self.install_old(receipt={'version': '2001.01.01', 'sha256': 'not the hash of the exe'})
        with mock.patch.object(updater, '_stamp', return_value=(1, 1)):
            self.assertEqual(updater.engine_path(), self.bundled)   # remembered: this engine is not valid
            self.run_update(FakeGitHub())
            self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')
            self.assertEqual(updater.engine_version(), TAG)

    def test_no_probe_without_probe_arguments(self):
        self.run_update(FakeGitHub(), probe_args=None)
        self.assertEqual(self.probes, [])
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual(updater.engine_version(), TAG)

    def test_latest_version_already_in_use_downloads_nothing(self):
        github = FakeGitHub(tag=self.bundled_version)
        self.run_update(github)
        self.assertEqual(github.urls, [updater.RELEASE_URL])
        self.assertEqual(self.values('updated'), [{'version': self.bundled_version, 'unchanged': True}])
        self.assertFalse(self.engines.exists())
        self.assertEqual(self.probes, [])

    def refused(self, github, message, probe=None, asset_requested=False):
        self.install_old()
        with self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(github, probe=probe)
        self.assertEqual(self.values('update_error'), [message])
        self.assertEqual(self.values('updated'), [])
        self.assertEqual(self.values('update_done'), [{'cancelled': False}])
        self.assertEqual(github.urls, [updater.RELEASE_URL] + ([github.url] if asset_requested else []))
        self.assertEqual(self.leftovers(), [])
        self.assert_old_engine_kept()

    def test_digest_none_is_refused_with_a_readable_message(self):
        self.refused(FakeGitHub(digest=None), '官方发布未提供 SHA256，已保留现有引擎。')

    def test_digest_absent_or_short_is_refused(self):
        for digest in ('absent', 'sha256:abc', ''):
            with self.subTest(digest=digest):
                self.events.clear()
                self.refused(FakeGitHub(digest=digest), '官方发布未提供 SHA256，已保留现有引擎。')

    def test_bad_hash_is_refused(self):
        self.refused(FakeGitHub(digest='sha256:' + sha(b'another file')), '更新文件校验失败，已保留原来的引擎。',
                     asset_requested=True)
        self.assertEqual(self.probes, [], 'a file that failed verification is never started')

    def test_size_mismatch_is_refused(self):
        for size in (len(PAYLOAD) + 1, len(PAYLOAD) - 1, 300_000):
            with self.subTest(size=size):
                self.events.clear()
                self.refused(FakeGitHub(size=size), '更新文件校验失败，已保留原来的引擎。', asset_requested=True)
        self.assertEqual(self.probes, [])

    def test_probe_rejection_is_refused(self):
        self.refused(FakeGitHub(), '新版引擎与本程序使用的参数不兼容，已保留现有引擎。',
                     probe=self.probe(BAD_PROBE), asset_requested=True)
        self.assertEqual(len(self.probes), 1)

    def test_an_engine_that_does_not_start_is_refused(self):
        for failure in (OSError(193, '%1 is not a valid Win32 application'),
                        subprocess.TimeoutExpired(['yt-dlp.exe'], 60)):
            with self.subTest(failure=failure):
                self.events.clear()
                self.refused(FakeGitHub(), '新版引擎与本程序使用的参数不兼容，已保留现有引擎。',
                             probe=self.probe(failure), asset_requested=True)

    def test_release_without_the_exe_is_refused(self):
        self.refused(FakeGitHub(assets=[]), '官方发布中没有找到 yt-dlp.exe。')

    def test_download_address_outside_the_official_release_is_never_contacted(self):
        for url in ('https://example.invalid/yt-dlp.exe', 'http://github.com/yt-dlp/yt-dlp/releases/download/x/yt-dlp.exe',
                    'https://github.com/someone-else/yt-dlp/releases/download/x/yt-dlp.exe'):
            with self.subTest(url=url):
                self.events.clear()
                self.refused(FakeGitHub(url=url), '更新文件不在官方发布地址，已保留现有引擎。')

    def test_implausible_size_is_refused_before_the_download(self):
        for size in (0, -1, 150 * 1024 * 1024 + 1, 'big'):
            with self.subTest(size=size):
                self.events.clear()
                self.refused(FakeGitHub(size=size), '官方发布的文件大小异常，已保留现有引擎。')

    def test_network_failure_gives_the_network_message(self):
        self.refused(FakeGitHub(error=urllib.error.URLError(TimeoutError('timed out'))),
                     '无法连接 GitHub，请检查网络后重试。（连接超时）')

    def test_connection_lost_during_the_download(self):
        def drop(index):
            if index == 1:
                raise ConnectionResetError(10054, 'connection reset by peer')

        self.refused(FakeGitHub(on_block=drop), '无法连接 GitHub，请检查网络后重试。（[Errno 10054] connection reset by peer）',
                     asset_requested=True)

    def test_cancel_during_the_download_installs_nothing_and_leaves_no_file(self):
        self.install_old()
        seen = []

        def cancel(index):
            seen.append(self.leftovers())
            if index == 1:
                self.cancelled.set()

        self.run_update(FakeGitHub(on_block=cancel))
        self.assertEqual(len(seen), 2, 'the download went on after the stop')
        self.assertEqual(len(seen[1]), 1, 'the temp file exists while the download runs')
        self.assertTrue(TEMP_NAME.match(seen[1][0]), seen[1])
        self.assertEqual(self.kinds(), ['update_status', 'update_status', 'update_progress', 'update_done'])
        self.assertEqual(self.values('update_done'), [{'cancelled': True}])
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.probes, [])
        self.assert_old_engine_kept()

    def test_cancel_during_the_release_check_downloads_nothing(self):
        github = FakeGitHub()
        real = github.__call__

        def cancel_then_answer(request, timeout=None):
            self.cancelled.set()
            return real(request, timeout)

        with mock.patch('urllib.request.urlopen', cancel_then_answer):
            updater.EngineUpdater(self.emit, self.cancelled, PROBE_ARGS).run()
        self.assertEqual(github.urls, [updater.RELEASE_URL])
        self.assertEqual(self.kinds(), ['update_status', 'update_done'])
        self.assertEqual(self.values('update_done'), [{'cancelled': True}])
        self.assertFalse(self.engines.exists())

    def test_cancel_set_during_the_probe_leaves_the_old_engine(self):
        self.install_old()
        self.run_update(FakeGitHub(), probe=self.probe(hook=lambda _command: self.cancelled.set()))
        self.assertEqual(len(self.probes), 1)
        self.assertNotIn('updated', self.kinds())
        self.assertNotIn('update_error', self.kinds())
        self.assertEqual(self.values('update_done'), [{'cancelled': True}])
        self.assertEqual(self.leftovers(), [])
        self.assert_old_engine_kept()

    def test_two_runs_use_their_own_temp_files(self):
        paused, release = threading.Event(), threading.Event()
        first_cancelled, first_events, names = threading.Event(), [], {}

        def on_block(index):
            if threading.current_thread() is first and index == 1:
                names['first'] = self.leftovers()
                paused.set()
                release.wait(30)

        def finish_first(command):
            # The second run has its verified file; now the abandoned first run ends.
            names['second'] = Path(command[0]).name
            names['both'] = self.leftovers()
            release.set()
            first.join(30)
            names['after_first'] = self.leftovers()

        github = FakeGitHub(on_block=on_block)
        first = threading.Thread(target=updater.EngineUpdater(
            lambda kind, value: first_events.append((kind, value)), first_cancelled, PROBE_ARGS).run)
        with mock.patch('urllib.request.urlopen', github), self.probe(hook=finish_first):
            first.start()
            self.assertTrue(paused.wait(30))
            first_cancelled.set()   # Stop: the window is free again while this run still waits
            updater.EngineUpdater(self.emit, self.cancelled, PROBE_ARGS).run()
        self.assertFalse(first.is_alive())
        self.assertEqual(len(names['first']), 1)
        self.assertEqual(len(names['both']), 2, 'the two runs shared one temp file')
        self.assertNotEqual(names['first'][0], names['second'])
        self.assertEqual(names['after_first'], [names['second']],
                         "the first run's clean-up removed the second run's file (or kept its own)")
        self.assertEqual(first_events[-1], ('update_done', {'cancelled': True}))
        self.assertNotIn('updated', [kind for kind, _value in first_events])
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), PAYLOAD)
        self.assertEqual(self.leftovers(), [])

    def test_legacy_events_are_the_kinds_of_1_5_1(self):
        self.run_update(FakeGitHub(), legacy=True)
        self.assertEqual(self.kinds(), ['title', 'progress', 'progress', 'progress', 'updated', 'update_done'])
        self.assertEqual(self.values('title'), ['正在检查官方更新…'])
        self.assertEqual(self.values('progress')[-1],
                         {'fraction': 1.0, 'downloaded': len(PAYLOAD), 'total': len(PAYLOAD), 'speed': None,
                          'eta': None, 'status': 'updating'})
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual(self.values('update_done'), [{'cancelled': False}])

    def test_legacy_error_and_unchanged(self):
        self.run_update(FakeGitHub(tag=self.bundled_version), legacy=True)
        self.assertEqual(self.kinds(), ['title', 'updated', 'update_done'])
        self.events.clear()
        with self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(FakeGitHub(digest=None), legacy=True)
        self.assertEqual(self.kinds(), ['title', 'update_error', 'update_done'])

    def test_runner_update_is_the_legacy_delegate(self):
        runner = engine.Runner(self.emit)
        with mock.patch('urllib.request.urlopen', FakeGitHub()), self.probe():
            runner.update()
        self.assertEqual(self.kinds(), ['title', 'progress', 'progress', 'progress', 'updated', 'update_done'])
        # WP1: the options of one preset.  From WP2 on: engine.probe_args(), every preset and option block.
        expected = (engine.probe_args() if hasattr(engine, 'probe_args') else
                    engine.build_command('https://example.invalid/probe',
                                         engine.Options(Path(tempfile.gettempdir())))[1:-2])
        self.assertEqual(self.probes[0][0][1:], expected)
        self.assertIn('--ignore-config', expected)
        self.assertNotIn('https://example.invalid/probe', self.probes[0][0])
        self.assertNotIn('--', self.probes[0][0])
        self.assertEqual(updater.engine_version(), TAG)
        # Stop is the runner's own event.
        self.events.clear()
        updater.restore_bundled()
        stopped = engine.Runner(self.emit)
        stopped.cancel()
        with mock.patch('urllib.request.urlopen', FakeGitHub()), self.probe():
            stopped.update()
        self.assertEqual(self.kinds(), ['title', 'update_done'])
        self.assertEqual(self.values('update_done'), [{'cancelled': True}])

    def test_unexpected_failure_is_logged_with_its_traceback(self):
        with mock.patch.object(updater, 'check_latest', side_effect=RuntimeError('boom')), \
                self.assertLogs('YtDlpDesktop.updater', 'ERROR') as logs:
            updater.EngineUpdater(self.emit, self.cancelled).run()
        self.assertEqual(self.kinds(), ['update_status', 'update_error', 'update_done'])
        self.assertEqual(self.values('update_error'), ['boom'])
        self.assertTrue(logs.records[0].exc_info)

    def test_install_failure_keeps_a_readable_message(self):
        self.install_old()
        with mock.patch.object(updater.os, 'replace', side_effect=PermissionError(13, '拒绝访问。')), \
                self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(FakeGitHub())
        self.assertEqual(self.values('update_error'), ['新版引擎无法写入（拒绝访问。），已保留现有引擎。'])
        self.assertEqual(self.leftovers(), [])
        self.assert_old_engine_kept()

    def test_install_failure_message_has_no_file_path(self):
        """bughunt-6: str(error) of a failed os.replace carries both paths, cut somewhere in the middle."""
        self.install_old()
        with open(self.engines / 'yt-dlp.exe', 'rb'), self.assertLogs('YtDlpDesktop.updater', 'WARNING') as logs:
            self.run_update(FakeGitHub())   # the engine in use cannot be replaced
        message = self.values('update_error')[0]
        self.assertTrue(message.startswith('新版引擎无法写入（') and message.endswith('），已保留现有引擎。'), message)
        for part in ('yt-dlp', 'engines', str(self.root), self.root.name, '\\', '[WinError', "'"):
            self.assertNotIn(part, message)
        self.assertGreater(len(message), len('新版引擎无法写入（），已保留现有引擎。'), 'the reason is missing')
        self.assertTrue(logs.records[0].exc_info, 'the log keeps the full error')
        self.assertEqual(self.values('updated'), [])
        self.assertEqual(self.leftovers(), [])
        self.assert_old_engine_kept()
        self.assertEqual(updater._file_reason(OSError()), 'OSError')
        self.assertEqual(updater._file_reason(PermissionError(13, 'Access is\ndenied.', 'C:\\a\\b.exe')),
                         'Access is denied.')

    def failing_receipt(self):
        real = paths.atomic_write_text

        def write(path, text, encoding='utf-8'):
            if Path(path).name == 'yt-dlp.json':
                raise PermissionError(13, '拒绝访问。', str(path))
            return real(path, text, encoding)

        return mock.patch.object(paths, 'atomic_write_text', write)

    def test_receipt_failure_leaves_the_bundled_engine_and_says_so(self):
        """bughunt-6: the new exe is already in place when its receipt cannot be written."""
        self.install_old()
        self.assertEqual(updater.engine_version(), '2001.01.01')
        with self.failing_receipt(), self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(FakeGitHub())
        self.assertEqual(self.values('update_error'),
                         ['新版引擎的校验记录无法写入（拒绝访问。），已改用内置引擎，请稍后重试更新。'])
        self.assertEqual(self.values('updated'), [])
        self.assertEqual(self.values('update_done'), [{'cancelled': False}])
        # No exe that nothing vouches for, and no receipt of an engine that is gone.
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()), [])
        self.assertEqual(updater.engine_path(), self.bundled)
        self.assertEqual(updater.engine_version(), self.bundled_version)
        # The next attempt starts from a clean state and succeeds.
        self.events.clear()
        self.run_update(FakeGitHub())
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual(updater.engine_version(), TAG)

    def test_receipt_failure_without_an_earlier_update(self):
        with self.failing_receipt(), self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(FakeGitHub())
        self.assertEqual(len(self.values('update_error')), 1)
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()), ['.ytdlp-desktop'])
        self.assertEqual(updater.engine_path(), self.bundled)

    def test_the_start_up_purge_spares_the_download_of_a_running_update(self):
        """bughunt-4: the purge thread of app.py reaches the engines folder while an update sits
        between its download and its installation (the file is closed then)."""
        seen = []

        def purge(_command):
            seen.append(self.leftovers())
            paths.purge_stale(procutil.alive)
            updater.cleanup()
            seen.append(self.leftovers())

        self.run_update(FakeGitHub(), probe=self.probe(hook=purge))
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(seen[1], seen[0], 'the clean-up deleted the file of the update that is running')
        self.assertEqual(self.values('update_error'), [])
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), PAYLOAD)
        self.assertEqual(updater.engine_version(), TAG)
        self.assertEqual(self.leftovers(), [])
        self.assertTrue((self.engines / paths.OWNED).is_file(), 'the updater creates the engines folder as the app\'s own')

    def test_the_bundled_engine_passes_the_real_probe_and_a_wrong_option_fails_it(self):
        """Offline: without a URL the engine only parses its options and prints the usage error."""
        arguments = engine.build_command('https://example.invalid/probe',
                                         engine.Options(Path(tempfile.gettempdir())))[1:-2]
        updater.EngineUpdater(self.emit, self.cancelled, arguments)._probe(self.bundled)
        with self.assertLogs('YtDlpDesktop.updater', 'WARNING'), self.assertRaises(updater.UpdateError) as caught:
            updater.EngineUpdater(self.emit, self.cancelled, arguments + ['--no-such-option-in-any-yt-dlp'])._probe(
                self.bundled)
        self.assertEqual(str(caught.exception), '新版引擎与本程序使用的参数不兼容，已保留现有引擎。')
        self.assertEqual(self.events, [])


class ProbeOutputTests(UpdaterCase):
    """updater._probe_output with real child processes (python -c)."""

    def run_folders(self):
        folder = paths.tmp_dir()
        return sorted(path.name for path in folder.iterdir() if path.name != paths.OWNED) if folder.is_dir() else []

    def test_output_and_how_the_engine_is_started(self):
        code = ('import json, os, sys\n'
                'print(json.dumps({"temp": os.environ["TEMP"], "tmp": os.environ["TMP"], "cwd": os.getcwd(), '
                '"arguments": sys.argv[1:], "stdin": sys.stdin.read(), "exists": os.path.isdir(os.environ["TEMP"])}))\n'
                'sys.stderr.write("to stderr\\n")\n'
                'sys.exit(2)\n')
        with mock.patch.object(subprocess, 'Popen', wraps=subprocess.Popen) as popen:
            output = updater._probe_output([sys.executable, '-c', code, '--no-playlist'], 60)
        first, _, rest = output.partition(b'\n')
        report = json.loads(first)
        self.assertIn(b'to stderr', rest, 'both streams are returned')
        self.assertEqual(report['arguments'], ['--no-playlist'])
        self.assertEqual(report['stdin'], '', 'the engine must not wait for input')
        self.assertEqual(Path(report['cwd']), paths.BIN)
        # A private TEMP, as for a download: the engine unpacks itself there.
        temp = Path(report['temp'])
        self.assertEqual((report['tmp'], report['exists']), (report['temp'], True))
        self.assertTrue(temp.is_absolute())
        self.assertEqual(temp.parent, paths.tmp_dir())
        self.assertTrue(temp.name.startswith(f'run-{os.getpid()}-'), temp.name)
        self.assertEqual(self.run_folders(), [], 'the private temp folder was not removed')
        keywords = popen.call_args.kwargs
        self.assertEqual((keywords['creationflags'], keywords['stdin'], keywords['cwd']),
                         (procutil.HIDDEN, subprocess.DEVNULL, str(paths.BIN)))
        self.assertEqual(keywords['env']['PATH'].split(os.pathsep)[0], str(paths.BIN))
        self.assertEqual(keywords['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_a_file_that_does_not_start_raises_oserror_and_leaves_nothing(self):
        broken = self.root / 'broken.exe'
        broken.write_bytes(b'MZ this is not a program')
        with self.assertRaises(OSError):
            updater._probe_output([str(broken), '--version'], 60)
        self.assertEqual(self.run_folders(), [])

    def hanging(self, timeout=4):
        """Run the hanging stand-in through EngineUpdater._probe; returns (seconds, its report)."""
        report = self.root / f'pids-{len(list(self.root.glob("pids-*")))}.json'
        probe = updater.EngineUpdater(self.emit, self.cancelled, ['-c', HANGING, str(report)])
        started = time.monotonic()
        try:
            with mock.patch.object(updater, 'PROBE_TIMEOUT', timeout), mock.patch.object(updater, 'PROBE_GRACE', 5), \
                    self.assertLogs('YtDlpDesktop.updater', 'WARNING') as logs, \
                    self.assertRaises(updater.UpdateError) as caught:
                probe._probe(sys.executable)
        finally:
            data = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {}
            self.addCleanup(self.kill_leftovers, [data.get('pid'), data.get('second')])
        elapsed = time.monotonic() - started
        self.assertEqual(str(caught.exception), '新版引擎与本程序使用的参数不兼容，已保留现有引擎。')
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        self.assertIn('did not finish', logs.records[0].getMessage())
        self.assertTrue(data, 'the stand-in did not start in time')
        return elapsed, data

    def test_a_hanging_engine_is_ended_with_everything_it_started(self):
        """bughunt-5: subprocess.run(timeout=...) killed only the first process and then waited
        for the second one, which holds the same pipes, for as long as it liked (here 120 s)."""
        elapsed, data = self.hanging()
        self.assertLess(elapsed, 30, 'the timeout did not bound the probe')
        self.assertGreaterEqual(elapsed, 4)
        self.assertEqual(self.still_alive([data['pid'], data['second']]), [], 'the probe left processes behind')
        self.assertEqual(Path(data['temp']).parent, paths.tmp_dir())
        self.assertEqual(self.run_folders(), [])
        self.assertEqual(self.events, [])

    def test_a_hanging_engine_is_ended_without_a_job_object_too(self):
        with mock.patch.object(procutil.JobObject, '_create', staticmethod(lambda: None)):
            elapsed, data = self.hanging()
        self.assertLess(elapsed, 30)
        self.assertEqual(self.still_alive([data['pid'], data['second']]), [])

    def test_an_update_with_a_hanging_engine_ends_and_keeps_the_old_engine(self):
        self.install_old()
        timed_out = subprocess.TimeoutExpired(['yt-dlp.exe'], 60)
        with self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            self.run_update(FakeGitHub(), probe=self.probe(timed_out))
        self.assertEqual(self.values('update_error'), ['新版引擎与本程序使用的参数不兼容，已保留现有引擎。'])
        self.assertEqual(self.values('update_done'), [{'cancelled': False}])
        self.assertEqual(self.leftovers(), [])
        self.assert_old_engine_kept()


class HousekeepingTests(UpdaterCase):
    def test_restore_bundled(self):
        self.run_update(FakeGitHub())
        self.assertEqual(updater.engine_version(), TAG)
        updater.restore_bundled()
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()), ['.ytdlp-desktop'])
        self.assertEqual(updater.engine_path(), self.bundled)
        self.assertEqual(updater.engine_version(), self.bundled_version)
        updater.restore_bundled()  # nothing to delete: no error
        # An exe that cannot be deleted right now is still out of use once its receipt is gone.
        self.install_old()
        real_unlink = Path.unlink

        def unlink(path, *arguments, **keywords):
            if path.name == 'yt-dlp.exe':
                raise PermissionError(13, 'in use')
            return real_unlink(path, *arguments, **keywords)

        with mock.patch.object(Path, 'unlink', unlink), self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
            updater.restore_bundled()
        self.assertEqual(updater.engine_path(), self.bundled)

    def test_cleanup_removes_only_download_leftovers(self):
        updater.cleanup()  # no engines folder: no error, nothing created
        self.assertFalse(self.engines.exists())
        paths.own_dir(self.engines)   # as the updater creates it
        self.install_old()
        dead, own = self.dead_pid(), os.getpid()
        running = f'yt-dlp.download.{own}.2.exe'   # an update of this very process, between download and install
        for name in ('yt-dlp.download', f'yt-dlp.download.{dead}.1.exe', f'yt-dlp.download.{dead}.7.exe', running,
                     'notes.txt', 'yt-dlp.download-notes.txt', 'yt-dlp.download.old'):
            (self.engines / name).write_bytes(b'x')
        (self.engines / 'yt-dlp.download-folder').mkdir()
        updater.cleanup()
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()),
                         sorted(['.ytdlp-desktop', 'notes.txt', 'yt-dlp.download-folder', 'yt-dlp.download-notes.txt',
                                 'yt-dlp.download.old', running, 'yt-dlp.exe', 'yt-dlp.json']))
        self.assert_old_engine_kept()

    def test_cleanup_leaves_an_engines_folder_alone_that_is_not_the_apps_own(self):
        """bughunt-1: a state folder on a shared place that has an engines folder of its own."""
        with _sandbox.state_patch(self.root / 'shared'):
            foreign = paths.engines_dir()
            foreign.mkdir(parents=True)
            names = ['yt-dlp.download', f'yt-dlp.download.{self.dead_pid()}.1.exe', 'yt-dlp.download-notes.txt']
            for name in names:
                (foreign / name).write_bytes(b'x')
            updater.cleanup()
            self.assertEqual(sorted(path.name for path in foreign.iterdir()), sorted(names))
        # The default location is the app's by its name, whoever created the folder (1.5.1 did).
        with _sandbox.state_patch(self.root / 'profile' / 'YtDlpDesktop'):
            default = paths.engines_dir()
            default.mkdir(parents=True)
            for name in names:
                (default / name).write_bytes(b'x')
            updater.cleanup()
            self.assertEqual(sorted(path.name for path in default.iterdir()), ['yt-dlp.download-notes.txt'])

    def test_update_error_is_an_exception_with_a_user_message(self):
        self.assertTrue(issubclass(updater.UpdateError, Exception))
        self.assertEqual(str(updater.UpdateError('更新文件校验失败，已保留原来的引擎。')), '更新文件校验失败，已保留原来的引擎。')

    def test_updater_does_not_import_the_engine_or_the_network_at_import(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import updater; '
                'print(sorted(name for name in ("engine", "urllib.request", "http.client", "library") '
                'if name in sys.modules))')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(_sandbox.SRC)], capture_output=True,
                                text=True, timeout=60, creationflags=procutil.HIDDEN)
        self.assertEqual(result.stdout.strip(), '[]', result.stderr)


if __name__ == '__main__':
    unittest.main()
