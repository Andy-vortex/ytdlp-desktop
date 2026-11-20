# tier: all
"""Regression tests of the 1.7.0 core fixes (docs/1.7.0/handoff/fix-core.md).

C1 c3-retry-ratelimit: the reruns of an item (with -k, without subtitles) go through the 429 cooldown.
C2 c5-updater: the new engine must start normally; Stop wins in the install pause; 恢复内置引擎
   reports success only when the bundled engine is the one in use.
C3 rv-findings-2-2: an item with -k from the start leaves no subtitle file behind.
C4 q-minor-2: one queued event with the same link in two formats keeps both in the history record.
C5 final-data-safety-1: a playlist or archive run never post-processes a file that was there before.
Fix round 1 (docs/1.7.0/handoff/fix-merge.md): a time in a title ('10:30' -> '10_30') is matched; a
   large folder's filters go through a config file instead of the fallback without tags; the option
   check covers the Runner's words; 恢复内置引擎 does not wait for a running or held exe.
Fix round 2: 原始最佳 (audio_best) is not kept from its own file by another preset's MP3/FLAC/WAV/M4A;
   a title that changes only in letter case is still skipped; a -k item stopped after the subtitle
   file was written leaves no subtitle file.
1.7.0 (i18n-L1D, v-core-3-c3-stop-leaves-temp-mkv): a -k item stopped or failed while ffmpeg changes
   the converted file leaves neither ffmpeg's '<work name>.temp.mkv' nor the half-made MKV.

Tier "all": the real bundled yt-dlp and ffmpeg against a local site (127.0.0.1 only, no network);
the unit checks of the same fixes run in the same file.
"""
import _sandbox
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.parse

import engine
import procutil
import formats
import i18n
import library
import paths
import unfinished
import updater
from engine import Options, Runner
from test_core_updater import FakeGitHub, GOOD_PROBE, OLD_ENGINE, PAYLOAD, TAG, UpdaterCase, sha


# (2.0.0 S3 W4) the zh text of a catalog key, whatever the current language (S3-DESIGN 4.3)
from _sandbox import zh_t


def head(key, name):
    """The zh text of a template before its placeholder ``name``: how a log line of that key starts."""
    return zh_t(key, **{name: '\0'}).split('\0')[0]


SKIPPED = head('core.engine.present_skipped', 'name')        # 保存位置已有这一条（…
UNTAGGED = head('core.engine.present_untagged', 'count')      # 保存位置已有本条的 …
OTHER_FORMAT = head('core.engine.other_format_source', 'name')

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'verification' / 'fixture' / 'fixture.mp4'
COOLDOWN = 2


# --------------------------------------------------------------------------- local site

class _Handler(SimpleHTTPRequestHandler):
    """Files of the site folder with Range; ``rules`` answers the n-th request of a path with the
    n-th entry of its list (the last one repeats): 200 serves the file, '302:<path>' redirects,
    any other number is that status.  Every request is logged with its time (``hits``)."""
    rules = {}
    hits = []

    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.unquote(self.path.split('?')[0])
        count = sum(1 for _time, seen in self.hits if seen == path) + 1
        self.hits.append((time.monotonic(), path))
        rule = self.rules.get(path)
        if rule:
            code = rule[min(count, len(rule)) - 1]
            if isinstance(code, str) and code.startswith('302:'):
                self.send_response(302)
                self.send_header('Location', code[4:])
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if code != 200:
                body = b'Too Many Requests'
                self.send_response(code)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)
                return
        target = Path(self.translate_path(self.path))
        if target.suffix == '.mp4' and target.is_file():
            data = target.read_bytes()
            start = 0
            ranged = self.headers.get('Range') or ''
            if ranged.startswith('bytes='):
                start = int(ranged[6:].split('-')[0] or 0)
            body = data[start:]
            self.send_response(206 if start else 200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(len(body)))
            if start:
                self.send_header('Content-Range', f'bytes {start}-{len(data) - 1}/{len(data)}')
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
            return
        super().do_GET()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def page(title, body):
    escaped = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    return ('<!doctype html><html><head><meta charset="utf-8">'
            f'<title>{escaped}</title><meta property="og:title" content="{escaped}"></head>'
            f'<body>{body}</body></html>')


def make_site(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('fixture.mp4', 'a.mp4', 'b.mp4', 'c.mp4'):
        shutil.copyfile(FIXTURE, folder / name)
    if not (folder / 'cover.jpg').exists():
        subprocess.run([str(paths.BIN / 'ffmpeg.exe'), '-y', '-v', 'error', '-i', str(FIXTURE), '-frames:v', '1',
                        '-vf', 'scale=320:180', str(folder / 'cover.jpg')], check=True,
                       creationflags=procutil.HIDDEN, env=procutil.process_environment(), timeout=60)
    (folder / 'sub.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n', encoding='utf-8')
    track = '<track kind="subtitles" src="sub.vtt" srclang="en" label="English">'
    (folder / 'subs.html').write_text(page('Artist - Song', f'<video src="fixture.mp4" poster="cover.jpg">{track}</video>'),
                                      encoding='utf-8')
    (folder / 'as.html').write_text(page('Clip as', f'<video src="a.mp4" poster="cover.jpg">{track}</video>'), encoding='utf-8')
    for name in 'abc':
        (folder / f'{name}.html').write_text(page(f'Clip {name}', f'<video src="{name}.mp4" poster="cover.jpg"></video>'),
                                             encoding='utf-8')
    (folder / 'song.html').write_text(page('Artist - Song', '<video src="fixture.mp4" poster="cover.jpg"></video>'),
                                      encoding='utf-8')
    list_page(folder, 'list.html', 2)


def list_page(folder, name, count, title='My List'):
    videos = ''.join(f'<video src="{letter}.mp4" poster="cover.jpg"></video>' for letter in 'abc'[:count])
    (folder / name).write_text(page(title, videos), encoding='utf-8')


def start_site(folder, rules=None):
    handler = type('Handler', (_Handler,), {'rules': dict(rules or {}), 'hits': []})
    server = _Server(('127.0.0.1', 0), partial(handler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}', server, handler


def area(name):
    folder = _sandbox.OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(folder):
    folder = Path(folder)
    return {path.name: digest(path) for path in folder.iterdir() if path.is_file()} if folder.is_dir() else {}


def media_files(folder):
    folder = Path(folder)
    return sorted(path.name for path in folder.iterdir() if path.name != paths.OWNED) if folder.is_dir() else []


def ffprobe(path, *entries):
    run = subprocess.run([str(paths.BIN / 'ffprobe.exe'), '-v', 'error', *entries, '-of', 'json', str(path)],
                         capture_output=True, text=True, encoding='utf-8', creationflags=procutil.HIDDEN,
                         env=procutil.process_environment(), timeout=60)
    return json.loads(run.stdout or '{}')


def tags(path):
    return ffprobe(path, '-show_entries', 'format_tags').get('format', {}).get('tags', {})


def edit_tags(path):
    """Change the title and comment as an owner's tag editor would (a new file, then a replace)."""
    path = Path(path)
    temporary = path.with_name('edited.tmp' + path.suffix)
    subprocess.run([str(paths.BIN / 'ffmpeg.exe'), '-y', '-v', 'error', '-i', str(path), '-map', '0', '-c', 'copy',
                    '-metadata', 'title=Owner Edited Title', '-metadata', 'comment=my notes', str(temporary)],
                   check=True, creationflags=procutil.HIDDEN, env=procutil.process_environment(), timeout=60)
    os.replace(temporary, path)


class EngineCase(unittest.TestCase):
    rules = {}

    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()
        cls.site = area('site-' + cls.__name__)
        make_site(cls.site)

    def setUp(self):
        self.base, self.server, self.handler = start_site(self.site, self.rules)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        Runner.reset_pacing()
        for name, value in (('PACE_SECONDS', (0, 0)), ('RATE_LIMIT_COOLDOWN', COOLDOWN),
                            ('MAX_RATE_LIMIT_COOLDOWN', COOLDOWN)):
            patcher = mock.patch.object(engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = Path(tempfile.mkdtemp(dir=area('runs')))
        self.events = []
        with unfinished.LOCK:
            unfinished.save([])

    def emit(self, kind, value):
        self.events.append((time.monotonic(), kind, value))

    def of(self, kind):
        return [value for _time, name, value in self.events if name == kind]

    def logs(self):
        return [str(value) for value in self.of('log')]

    def done(self):
        values = self.of('done')
        self.assertEqual(len(values), 1, 'exactly one done event')
        self.assertEqual(self.events[-1][1], 'done', 'done is the last event')
        return values[0]

    def runner(self):
        """A Runner whose _wait calls are recorded as (start, reason, seconds, waited)."""
        runner = Runner(self.emit)
        self.waits = []
        real = runner._wait

        def wait(seconds, index, total, reason):
            started = time.monotonic()
            waited = real(seconds, index, total, reason)
            self.waits.append((started, reason, seconds, waited))
            return waited
        runner._wait = wait
        return runner

    def run_links(self, links, options, runner=None):
        runner = runner or self.runner()
        runner.download(links, options)
        return runner

    def first_request(self, path):
        return next((moment for moment, seen in self.handler.hits if seen == path), None)

    def requests(self, path):
        return sum(1 for _moment, seen in self.handler.hits if seen == path)


# --------------------------------------------------------------------------- C1 c3-retry-ratelimit

class RerunRateLimitTests(EngineCase):
    """A 429 in the rerun without subtitles or in the -k rerun cools down and retries as on the main
    path; when the retries are used up the whole queue pauses; the next item starts after it."""

    SUBS = {'/sub.vtt': [429], '/a.mp4': [429]}
    COUNTED = [zh_t('core.wait.rate_retry', attempt=1, limit=2), zh_t('core.wait.rate_retry', attempt=2, limit=2),
               zh_t('core.wait.rate_queue')]

    def check_cooled(self, first_path):
        reasons = [reason for _started, reason, _seconds, _waited in self.waits]
        self.assertEqual(reasons, self.COUNTED, self.logs())
        self.assertTrue(all(waited for *_rest, waited in self.waits))
        self.assertEqual(self.requests(first_path), 3, 'the rerun ran three times: once and after each cooldown')
        pause_end = self.waits[-1][0] + COOLDOWN
        self.assertGreaterEqual(self.first_request('/b.html'), pause_end - 0.05,
                                'the next item started before the queue pause ended')
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['cancelled']), (1, 1, False))
        first, second = self.of('item_done')
        self.assertEqual((first['success'], second['success']), (False, True))
        self.assertIn(zh_t('core.engine.rate_limited_out'), self.logs())

    def test_a_429_in_the_rerun_without_subtitles_cools_down(self):
        out = self.root / 'subs'
        self.handler.rules = self.SUBS
        self.run_links([self.base + '/as.html', self.base + '/b.html'],
                       Options(out, formats.by_id('video_mp4').id, subtitles=True, batch_safe=False))
        logs = self.logs()
        self.assertIn(zh_t('core.engine.subtitles_dropped'), logs)
        # The subtitle 429 itself is no rate limit of the queue: the first wait comes after the media 429.
        self.assertEqual(self.requests('/sub.vtt'), 1)
        self.assertGreater(self.waits[0][0], self.first_request('/a.mp4'))
        self.check_cooled('/a.mp4')

    def test_a_429_in_the_keep_source_rerun_cools_down(self):
        out = self.root / 'keep'
        Runner(lambda *_: None).download([self.base + '/a.html'],
                                         Options(out, formats.by_id('video_mp4').id, naming='title_id', batch_safe=False))
        older = snapshot(out)
        self.assertEqual([Path(name).suffix for name in older], ['.mp4'], older)
        self.handler.hits.clear()
        self.handler.rules = {'/x.html': ['302:/a.html', 429]}
        Runner.reset_pacing()
        # x.html redirects to a.html: the link's id does not predict the older file, so the item is
        # stopped at its "already downloaded" line and run again with -k; that rerun meets the 429.
        self.run_links([self.base + '/x.html', self.base + '/b.html'], Options(out, formats.by_id('audio_mp3').id,
                                                                              batch_safe=False))
        self.assertTrue(any(line.startswith(OTHER_FORMAT) for line in self.logs()), self.logs())
        reasons = [reason for _started, reason, _seconds, _waited in self.waits]
        self.assertEqual(reasons, self.COUNTED, self.logs())
        self.assertEqual(self.requests('/x.html'), 4, 'the first run, the -k rerun and its two retries')
        self.assertGreaterEqual(self.first_request('/b.html'), self.waits[-1][0] + COOLDOWN - 0.05)
        for name, value in older.items():
            self.assertEqual(snapshot(out).get(name), value, 'the older MP4 is unchanged')
        done = self.done()
        self.assertEqual((done['success'], done['failed']), (1, 1))

    def test_stop_during_the_rerun_cooldown_ends_the_queue(self):
        out = self.root / 'stop'
        self.handler.rules = self.SUBS
        runner = self.runner()
        emit = runner.emit

        def stop_on_wait(kind, value):
            emit(kind, value)
            if kind == 'waiting' and not runner.cancelled.is_set():
                runner.cancel()
        runner.emit = stop_on_wait
        started = time.monotonic()
        self.run_links([self.base + '/as.html', self.base + '/b.html'],
                       Options(out, formats.by_id('video_mp4').id, subtitles=True, batch_safe=False), runner=runner)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual([(reason, waited) for _started, reason, _seconds, waited in self.waits],
                         [(zh_t('core.wait.rate_retry', attempt=1, limit=2), False)])
        self.assertEqual(self.requests('/a.mp4'), 1, 'nothing ran after Stop')
        self.assertIsNone(self.first_request('/b.html'), 'the next item never started')
        done = self.done()
        self.assertTrue(done['cancelled'])
        self.assertEqual([value['cancelled'] for value in self.of('item_done')], [True])
        self.assertEqual(sorted(entry['reason'] for entry in unfinished.load()), ['stopped', 'unstarted'])

    def test_the_retries_are_counted_once_per_item(self):
        """Scripted: a 429 on the main run, then a subtitle failure, then 429s in the rerun without
        subtitles: one count for the item (1/2, 2/2, then the queue pause), Retry-After respected."""
        out = self.root / 'scripted'
        script = [(1, True, None, False), (1, False, None, True), (1, True, 7, False), (1, True, None, False)]
        commands = []

        def run_once(runner, command, index, total, files):
            commands.append(list(command))
            if index != 1:
                return 0, False, None
            code, limited, retry_after, subtitles = script.pop(0)
            runner.subs_failed = runner.subs_failed or subtitles
            return code, limited, retry_after

        runner = self.runner()
        with mock.patch.object(Runner, '_run_once', run_once), mock.patch.object(engine, 'RATE_LIMIT_COOLDOWN', 0.1), \
                mock.patch.object(engine, 'MAX_RATE_LIMIT_COOLDOWN', 0.1):
            with mock.patch.object(runner, '_wait', lambda seconds, index, total, reason:
                                   self.waits.append((0, reason, seconds, True)) or True):
                runner.download(['https://e.test/a', 'https://e.test/b'],
                                Options(out, formats.by_id('video_mp4').id, subtitles=True, batch_safe=False))
        self.assertEqual([(reason, seconds) for _s, reason, seconds, _w in self.waits],
                         [(zh_t('core.wait.rate_retry', attempt=1, limit=2), 0.1),
                          (zh_t('core.wait.rate_retry', attempt=2, limit=2), 7), (zh_t('core.wait.rate_queue'), 0.1)])
        self.assertEqual(script, [])
        self.assertEqual(['--embed-subs' in command for command in commands], [True, True, False, False, True])
        self.assertEqual([value['success'] for value in self.of('item_done')], [False, True])


# --------------------------------------------------------------------------- C3 rv-findings-2-2

class KeepLeavesNoSubtitleTests(EngineCase):
    def test_an_item_with_keep_from_the_start_adds_only_the_requested_file(self):
        link = self.base + '/subs.html'
        for style in ('title', 'title_id'):
            with self.subTest(style=style):
                out = self.root / f'older-{style}'
                Runner(lambda *_: None).download([link], Options(out, formats.by_id('video_mp4').id, naming='title_id',
                                                                 batch_safe=False))
                before = snapshot(out)
                self.assertEqual([Path(name).suffix for name in before], ['.mp4'], before)
                work_before = media_files(paths.work_dir())
                self.events = []
                Runner.reset_pacing()
                runner = self.run_links([link], Options(out, formats.by_id('video_mkv').id, naming=style,
                                                        subtitles=True, batch_safe=False))
                self.assertTrue(runner._keep_early, 'the item ran with -k from the start')
                after = snapshot(out)
                for name, value in before.items():
                    self.assertEqual(after.get(name), value, 'the older MP4 is unchanged')
                added = sorted(set(after) - set(before))
                self.assertEqual([Path(name).suffix for name in added], ['.mkv'], after)
                streams = ffprobe(out / added[0], '-show_entries', 'stream=codec_type').get('streams', [])
                self.assertIn('subtitle', [stream.get('codec_type') for stream in streams], 'the subtitles are embedded')
                done = self.done()
                self.assertEqual((done['success'], done['files']), (1, [str(out / added[0])]))
                self.assertEqual(media_files(paths.work_dir()), work_before, 'nothing is left in the work folder')

    def test_a_stopped_item_leaves_no_subtitle_file_for_its_retry(self):
        """(fix round 2, v-core-2-c3-stop-after-delivery-leaves-vtt) Stop after the file was delivered
        (the item still succeeds) and Stop before it (the item is stopped): no '.vtt' stays, neither
        after the stopped run nor after the retry; the older MP4 is unchanged."""
        link = self.base + '/subs.html'
        for moment in ('file', '[EmbedSubtitle]'):
            with self.subTest(stop_at=moment):
                out = self.root / ('stop-' + moment.strip('[]'))
                Runner(lambda *_: None).download([link], Options(out, formats.by_id('video_mp4').id, naming='title_id',
                                                                 batch_safe=False))
                before = snapshot(out)
                options = Options(out, formats.by_id('video_mkv').id, naming='title_id', subtitles=True, batch_safe=False)
                self.events = []
                Runner.reset_pacing()
                runner = self.runner()
                emit = runner.emit

                def stop(kind, value, emit=emit, runner=runner, moment=moment):
                    emit(kind, value)
                    if not runner.cancelled.is_set() and (kind == moment or
                                                          (kind == 'log' and str(value).startswith(moment))):
                        runner.cancel()
                runner.emit = stop
                self.run_links([link], options, runner=runner)
                self.assertTrue(runner._keep_early)
                self.assertTrue(self.done()['cancelled'])
                item_done = self.of('item_done')[0]
                if moment == 'file':
                    self.assertEqual((item_done['success'], item_done['files']), (True, 1), item_done)
                self.assertFalse([name for name in os.listdir(out) if engine._WORK_SUBTITLE.search(name)],
                                 os.listdir(out))
                self.events = []
                Runner.reset_pacing()
                self.run_links([link], options)
                self.assertEqual(self.done()['success'], 1)
                self.assertFalse([name for name in os.listdir(out) if engine._WORK_SUBTITLE.search(name)],
                                 os.listdir(out))
                after = snapshot(out)
                self.assertEqual({name: after.get(name) for name in before}, before, 'the older MP4 is unchanged')

    def test_which_subtitle_files_are_removed(self):
        out = self.root / 'rule'
        out.mkdir(parents=True)
        owner = ['Mine [subs-1].en.vtt', 'Mine [subs-1].mp4', 'notes.vtt']
        removed = ['Artist - Song [subs-1].en.vtt', 'Artist - Song [subs-1].zh-Hans.srt', 'Clip [a-1].en.ass',
                   'Old [older].en.vtt']
        kept = ['Artist - Song [other].en.vtt', 'Artist - Song [subs-1].mkv', 'Artist - Song.mkv', 'X [subs-1].txt']
        for name in owner:
            (out / name).write_bytes(b'owner')
        runner = Runner(self.emit)
        runner._existing = engine._listing(out)
        for name in removed + kept:
            (out / name).write_bytes(b'new')
        runner._media_ids = ['a-1']
        runner._source = str(out / 'Older [older].mp4')
        item = engine.QueueItem(1, 'http://127.0.0.1/subs.html', Options(out, formats.by_id('video_mkv').id, subtitles=True))
        runner._discard_subtitles(item, out)
        self.assertEqual(media_files(out), sorted(owner + kept))


class KeepLeavesNoIntermediateTests(EngineCase):
    """(1.7.0, v-core-3-c3-stop-leaves-temp-mkv) An item with -k from the start converts the older file
    in the output folder, so ffmpeg's steps write '<work name>.temp.mkv' there.  The test holds the first
    such file open as soon as it appears (Windows then refuses the engine's rename, so the file is
    certainly there when the item ends) and either stops the item ('stop') or lets the engine fail on the
    rename ('fail').  Afterwards the folder holds exactly what it held before; a normal run then adds the
    MKV with its subtitles and leaves no intermediate file either."""

    def hold_first_temporary(self, out, runner, stop):
        """Wrap runner._run_once: a watcher opens the first '.temp.' file in ``out`` (and stops the item
        when ``stop``); the file is let go once the engine has ended, before the item's clean-up."""
        state = {'held': None, 'name': None, 'done': threading.Event()}
        real = runner._run_once

        def watch():
            deadline = time.monotonic() + 60
            while not state['done'].is_set() and time.monotonic() < deadline:
                try:
                    names = [name for name in os.listdir(out) if '.temp.' in name.lower()]
                except OSError:
                    names = []
                for name in names:
                    try:
                        state['held'] = open(out / name, 'rb')
                        state['name'] = name
                    except OSError:
                        continue
                    if stop:
                        runner.cancel()
                    return
                time.sleep(0.001)

        def run_once(command, index, total, files):
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                return real(command, index, total, files)
            finally:
                state['done'].set()
                watcher.join(10)
                if state['held'] is not None:
                    state['held'].close()
        runner._run_once = run_once
        return state

    def test_a_stopped_or_failed_item_leaves_no_temporary_file(self):
        link = self.base + '/subs.html'
        for outcome in ('stop', 'fail'):
            with self.subTest(outcome=outcome):
                out = self.root / ('temp-' + outcome)
                Runner(lambda *_: None).download([link], Options(out, formats.by_id('video_mp4').id, naming='title_id',
                                                                 batch_safe=False))
                before = snapshot(out)
                self.assertEqual([Path(name).suffix for name in before], ['.mp4'], before)
                options = Options(out, formats.by_id('video_mkv').id, naming='title_id', subtitles=True, batch_safe=False)
                self.events = []
                Runner.reset_pacing()
                runner = self.runner()
                state = self.hold_first_temporary(out, runner, stop=outcome == 'stop')
                self.run_links([link], options, runner=runner)
                self.assertTrue(runner._keep_early, 'the item ran with -k from the start')
                self.assertIsNotNone(state['name'], f'no ffmpeg temporary file appeared: {self.logs()}')
                self.assertTrue(state['name'].lower().endswith('.temp.mkv'), state['name'])
                done = self.done()
                item_done = self.of('item_done')[0]
                if outcome == 'stop':
                    self.assertTrue(done['cancelled'])
                    self.assertTrue(item_done['cancelled'], item_done)
                else:
                    self.assertFalse(done['cancelled'])
                    self.assertEqual((done['success'], done['failed']), (0, 1), self.logs())
                self.assertEqual(snapshot(out), before, 'the folder holds exactly what it held before the item')
                self.events = []
                Runner.reset_pacing()
                self.run_links([link], options)
                done = self.done()
                self.assertEqual(done['success'], 1, self.logs())
                after = snapshot(out)
                self.assertEqual({name: after.get(name) for name in before}, before, 'the older MP4 is unchanged')
                added = sorted(set(after) - set(before))
                self.assertEqual([Path(name).suffix for name in added], ['.mkv'], after)
                streams = ffprobe(out / added[0], '-show_entries', 'stream=codec_type').get('streams', [])
                self.assertIn('subtitle', [stream.get('codec_type') for stream in streams], 'the subtitles are embedded')
                self.assertEqual(done['files'], [str(out / added[0])])

    def test_which_intermediate_files_are_removed(self):
        out = self.root / 'rule'
        out.mkdir(parents=True)
        owner = ['Mine [subs-1].temp.mkv', 'Mine [subs-1].mkv', 'notes.temp.mkv', 'Older [older].mp4']
        removed = ['Artist - Song [subs-1].temp.mkv', 'Artist - Song [subs-1].temp.mp4', 'Artist - Song [subs-1].temp',
                   'Artist - Song [subs-1].meta', 'Clip [a-1].temp.m4a', 'Older [older].temp.mkv']
        kept = ['Artist - Song [other].temp.mkv', 'Artist - Song [subs-1].mkv', 'Artist - Song.temp.mkv',
                'Artist - Song [subs-1].en.vtt', 'X [subs-1].temp.mkv.part', 'Older [older].temporary.mkv']
        for name in owner:
            (out / name).write_bytes(b'owner')
        runner = Runner(self.emit)
        runner._existing = engine._listing(out)
        for name in removed + kept:
            (out / name).write_bytes(b'new')
        runner._media_ids = ['a-1']
        runner._source = str(out / 'Older [older].mp4')
        item = engine.QueueItem(1, 'http://127.0.0.1/subs.html', Options(out, formats.by_id('video_mkv').id, subtitles=True))
        runner._discard_intermediates(item, out)
        self.assertEqual(media_files(out), sorted(owner + kept))

    def test_which_unfinished_files_are_removed(self):
        out = self.root / 'unfinished'
        out.mkdir(parents=True)
        (out / 'Older [older].mp4').write_bytes(b'owner')
        (out / 'Owner [older].mkv').write_bytes(b'owner')
        runner = Runner(self.emit)
        runner._existing = engine._listing(out)
        runner._source = str(out / 'Older [older].mp4')
        item = engine.QueueItem(1, 'http://127.0.0.1/subs.html', Options(out, formats.by_id('video_mkv').id, subtitles=True))
        for name in ('Older [older].mkv', 'Older [older].mp3', 'Other [older].mkv'):
            (out / name).write_bytes(b'new')
        runner._discard_unfinished(item, [])
        self.assertEqual(media_files(out), ['Older [older].mp3', 'Older [older].mp4', 'Other [older].mkv',
                                            'Owner [older].mkv'])
        # A file the item reported is its delivered file, never removed; one that was there before neither.
        (out / 'Older [older].mkv').write_bytes(b'new')
        runner._discard_unfinished(item, [str(out / 'Older [older].mkv')])
        self.assertIn('Older [older].mkv', media_files(out))
        runner._existing = engine._listing(out)
        runner._discard_unfinished(item, [])
        self.assertIn('Older [older].mkv', media_files(out))
        audio = engine.QueueItem(1, 'http://127.0.0.1/subs.html', Options(out, formats.by_id('audio_mp3').id))
        runner._existing = engine._listing(out) - {engine._normal(out / 'Older [older].mp3')}
        runner._discard_unfinished(audio, [])
        self.assertNotIn('Older [older].mp3', media_files(out))
        self.assertIn('Older [older].mp4', media_files(out))


# --------------------------------------------------------------------------- C4 q-minor-2

class QueuedFormatsTests(unittest.TestCase):
    A = 'https://e.test/first'
    FAST = 'https://e.test/fast-a'

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=area('queued')))
        self.store = library.Library(self.root / 'history.sqlite3')
        self.first = Options(self.root / 'base', formats.by_id('audio_mp3').id)
        self.job = self.store.create_job('download', 'q', dict(self.first.to_dict(), urls=[self.A]))
        self.events = []
        with unfinished.LOCK:
            unfinished.save([])

    def run_queue(self, record=True):
        def emit(kind, value):
            self.events.append((kind, value))
            if record:
                self.store.download_event(self.job, kind, value)

        def run_once(runner, command, index, total, files):
            if index == 1:
                # 加入队列 twice while the first item runs: the same link in two formats and folders.
                runner.enqueue([self.FAST], Options(self.root / 'A', formats.by_id('audio_best').id))
                runner.enqueue([self.FAST], Options(self.root / 'B', formats.by_id('audio_mp3').id))
            return 1, False, None

        with mock.patch.object(Runner, '_run_once', run_once):
            Runner(emit).download([self.A], self.first)
        return [value for kind, value in self.events if kind == 'queued']

    def downloads(self):
        return [(item['url'], item['format'], Path(item['options']['output']).name)
                for item in library.job_downloads(self.store.job(self.job)['data'])]

    def test_one_queued_event_keeps_both_formats_of_a_link(self):
        queued = self.run_queue()
        self.assertEqual(len(queued), 1)
        payload = queued[0]
        # What 1.6.0 sent stays as it was (H5): the links, and the options keyed by link (the last one).
        self.assertEqual(payload['urls'], [self.FAST, self.FAST])
        self.assertEqual(set(payload['overrides']), {self.FAST})
        self.assertEqual(Path(payload['overrides'][self.FAST]['output']).name, 'B')
        # Added: one entry per item, in queue order.
        self.assertEqual([(entry['url'], entry['format'], Path(entry['options']['output']).name)
                          for entry in payload['items']],
                         [(self.FAST, 'audio_best', 'A'), (self.FAST, 'audio_mp3', 'B')])
        self.assertEqual(self.downloads(), [(self.A, 'audio_mp3', 'base'), (self.FAST, 'audio_best', 'A'),
                                            (self.FAST, 'audio_mp3', 'B')])
        # The record keeps the shape the 1.5.1 window and 1.6.0 read: links, overrides by link, variants.
        options = self.store.job(self.job)['data']['options']
        self.assertEqual(options['urls'], [self.A, self.FAST])
        self.assertEqual(Path(options['overrides'][self.FAST]['output']).name, 'A')
        self.assertEqual([(variant['url'], variant['format']) for variant in options['variants']], [(self.FAST, 'audio_mp3')])
        with self.store.db() as db:
            raw = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (self.job,)).fetchone()[0])['options']
        self.assertIn(raw['mode'], formats.MODES)       # (2.0.0 S2 P5) no 1.5.1 subset any more
        self.assertIn(raw['overrides'][self.FAST]['mode'], formats.MODES)

    def test_items_without_options_and_a_payload_of_1_6_0(self):
        # An entry without options is the run's own format; a 1.6.0 payload (no 'items') is read as before.
        self.store.download_event(self.job, 'queued', {'urls': ['https://e.test/b'], 'first_index': 2, 'total': 2,
                                                       'items': [{'url': 'https://e.test/b', 'format': 'audio_mp3'}]})
        old = {'urls': ['https://e.test/c'], 'first_index': 3, 'total': 3,
               'overrides': {'https://e.test/c': Options(self.root / 'C', 'audio_flac').to_dict()}}
        self.store.download_event(self.job, 'queued', old)
        self.store.download_event(self.job, 'queued', {'urls': [], 'items': [None, {'url': 5}], 'first_index': 4, 'total': 3})
        self.assertEqual(self.downloads(), [(self.A, 'audio_mp3', 'base'), ('https://e.test/b', 'audio_mp3', 'base'),
                                            ('https://e.test/c', 'audio_flac', 'C')])


# --------------------------------------------------------------------------- C2 c5-updater

class EngineStartCheckTests(UpdaterCase):
    real_version_check = True     # UpdaterCase stands in for "--version" otherwise
    def test_the_bundled_engine_runs_normally(self):
        updater.EngineUpdater(self.emit, self.cancelled, ['--ignore-config'])._check_start(self.bundled)
        code, output, _errors = updater._version_output(self.bundled, 60)
        self.assertEqual(code, 0)
        self.assertRegex(output.decode().strip(), updater.VERSION_LINE)

    def test_an_engine_that_starts_and_fails_is_refused_before_the_option_check(self):
        # A copy of this venv's python.exe starts and ends at once with "No pyvenv.cfg file" (exit
        # code 106): like a onefile engine whose unpacking failed.  Its SHA256 matches the release.
        broken = Path(sys.executable).read_bytes()
        self.install_old()
        probes = []
        real = updater._probe_output
        with mock.patch.object(updater, '_probe_output', side_effect=lambda *a: probes.append(a) or real(*a)), \
                mock.patch('urllib.request.urlopen', FakeGitHub(payload=broken)), \
                self.assertLogs('YtDlpDesktop.updater', 'WARNING') as logs:
            updater.EngineUpdater(self.emit, self.cancelled, ['--ignore-config']).run()
        self.assertEqual(self.values('update_error'), [zh_t('core.update.start_failed', reason=zh_t('core.update.start_exit', code=106))])
        self.assertEqual(self.values('updated'), [])
        self.assertEqual(probes, [], 'the option check never ran')
        self.assertTrue(any('--version' in record.getMessage() for record in logs.records))
        self.assert_old_engine_kept()
        self.assertEqual(self.leftovers(), [])

    def test_no_version_line_a_timeout_or_no_start_is_refused(self):
        exe = self.root / 'yt-dlp.exe'
        for answer, reason in (((0, b'Usage: yt-dlp [OPTIONS] URL\r\n', b''), zh_t('core.update.start_no_version')),
                               ((0, b'', b''), zh_t('core.update.start_no_version')),
                               ((2, b'2026.10.21\n', b''), zh_t('core.update.start_exit', code=2)),
                               (subprocess.TimeoutExpired(['x'], 60), zh_t('core.update.start_timeout', timeout=60)),
                               (OSError(193, 'bad exe'), zh_t('core.update.start_cannot'))):
            with self.subTest(reason=reason), \
                    mock.patch.object(updater, '_version_output', side_effect=[answer]), \
                    self.assertLogs('YtDlpDesktop.updater', 'WARNING'), self.assertRaises(updater.UpdateError) as caught:
                updater.EngineUpdater(self.emit, self.cancelled, ['--ignore-config'])._probe(exe)
            self.assertEqual(str(caught.exception), zh_t('core.update.start_failed', reason=reason))
        for line in (b'2026.10.21\n', b'2026.10.21.123456\r\n', b'2027.01.05\n'):
            with mock.patch.object(updater, '_version_output', return_value=(0, line, b'')), \
                    mock.patch.object(updater, '_probe_output', return_value=GOOD_PROBE):
                updater.EngineUpdater(self.emit, self.cancelled, ['--ignore-config'])._probe(exe)


class InstallStopTests(UpdaterCase):
    def test_stop_in_the_install_pause_wins_when_the_file_is_let_go(self):
        """Stop lands 30 ms into the 100 ms pause; the scanner-like handle on the new exe closes 30 ms
        later, before the next attempt (c5-updater b: in 1.6.0 that attempt installed the engine)."""
        for run in range(3):
            with self.subTest(run=run):
                self.events, self.cancelled = [], threading.Event()
                attempts, holder = [], []
                real_replace = updater.os.replace

                def stop_then_release():
                    self.cancelled.set()
                    time.sleep(0.03)
                    holder[0].close()

                def replace(source, target):
                    attempts.append(time.monotonic())
                    try:
                        return real_replace(source, target)
                    except PermissionError:
                        if len(attempts) == 3:
                            threading.Timer(0.03, stop_then_release).start()
                        raise

                def probe(command, timeout):
                    holder.append(open(command[0], 'rb'))     # no delete sharing: os.replace fails
                    return GOOD_PROBE
                self.install_old()
                with mock.patch.object(updater.os, 'replace', side_effect=replace):
                    self.run_update(FakeGitHub(), probe=mock.patch.object(updater, '_probe_output', side_effect=probe))
                time.sleep(0.1)
                self.assertEqual(len(attempts), 3, 'no attempt after Stop')
                self.assertEqual((self.values('updated'), self.values('update_error')), ([], []))
                self.assertEqual(self.values('update_done'), [{'cancelled': True}])
                self.assert_old_engine_kept()
                self.assertEqual(self.leftovers(), [])


class RestoreTests(UpdaterCase):
    def install_new(self):
        self.engines.mkdir(parents=True, exist_ok=True)
        (self.engines / 'yt-dlp.exe').write_bytes(PAYLOAD)
        (self.engines / 'yt-dlp.json').write_text(json.dumps({'version': TAG, 'sha256': sha(PAYLOAD)}), encoding='utf-8')
        updater.invalidate()
        self.assertEqual(updater.engine_path(), self.engines / 'yt-dlp.exe')

    def test_success_only_when_the_bundled_engine_is_in_use_afterwards(self):
        for label, held, read_only, restored, left in (
                ('nothing held', (), False, True, []),
                ('exe held', ('yt-dlp.exe',), False, True, ['yt-dlp.exe']),
                ('receipt held', ('yt-dlp.json',), False, True, ['yt-dlp.json']),
                ('both read-only', ('yt-dlp.json', 'yt-dlp.exe'), True, True, []),
                ('both held', ('yt-dlp.json', 'yt-dlp.exe'), False, False, ['yt-dlp.exe', 'yt-dlp.json'])):
            with self.subTest(label):
                self.install_new()
                handles = []
                for name in held:
                    if read_only:
                        os.chmod(self.engines / name, stat.S_IREAD)
                    else:
                        handles.append(open(self.engines / name, 'rb'))   # no delete sharing
                try:
                    if restored:
                        self.assertIs(updater.restore_bundled(), True)
                    else:
                        with self.assertLogs('YtDlpDesktop.updater', 'WARNING'), \
                                self.assertRaises(updater.UpdateError) as caught:
                            updater.restore_bundled()
                        self.assertEqual(str(caught.exception), zh_t('core.update.restore_failed'))
                finally:
                    for handle in handles:
                        handle.close()
                updater.invalidate()
                in_use = updater.engine_path()
                self.assertEqual(in_use == self.bundled, restored)
                self.assertEqual(sorted(path.name for path in self.engines.iterdir() if path.name.startswith('yt-dlp.')), left)
                for name in left:
                    os.chmod(self.engines / name, stat.S_IWRITE)
                    (self.engines / name).unlink()

    def test_a_running_or_held_exe_is_not_waited_for(self):
        """(fix round 1, v-core-1-restore-ui-freeze) restore_bundled runs on the window's thread: once
        the receipt is gone the updated exe is out of use, so a running exe (a download on it) or a
        held one is not retried.  A program stands in for the updated engine and runs meanwhile."""
        program = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'PING.EXE'
        for label in ('running', 'held'):
            with self.subTest(label):
                self.engines.mkdir(parents=True, exist_ok=True)
                exe = self.engines / 'yt-dlp.exe'
                shutil.copyfile(program, exe)
                (self.engines / 'yt-dlp.json').write_text(json.dumps({'version': TAG, 'sha256': digest(exe)}),
                                                          encoding='utf-8')
                updater.invalidate()
                self.assertEqual(updater.engine_path(), exe)
                if label == 'running':
                    holder = subprocess.Popen([str(exe), '-n', '30', '127.0.0.1'], stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, creationflags=procutil.HIDDEN)
                    release = lambda: (holder.kill(), holder.wait(10))    # noqa: E731
                    time.sleep(0.3)
                else:
                    holder = open(exe, 'rb')
                    release = holder.close
                try:
                    started = time.monotonic()
                    with self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
                        self.assertIs(updater.restore_bundled(), True)
                    blocked = time.monotonic() - started
                finally:
                    release()
                self.assertLess(blocked, 0.3, f'the window was blocked {blocked:.2f} s')
                updater.invalidate()
                self.assertEqual(updater.engine_path(), self.bundled)
                self.assertEqual(sorted(path.name for path in self.engines.iterdir() if path.name.startswith('yt-dlp.')),
                                 ['yt-dlp.exe'], 'the exe without its receipt stays, out of use')
                for _ in range(50):
                    try:
                        exe.unlink()
                        break
                    except PermissionError:
                        time.sleep(0.1)


# --------------------------------------------------------------------------- C5 final-data-safety-1

class PresentFileTests(EngineCase):
    """A playlist or archive run takes no file that was in the output folder before it as "already
    downloaded" and post-processes it: the engine is kept away from it with match filters."""

    def setUp(self):
        super().setUp()
        list_page(self.site, 'list.html', 2)
        self.addCleanup(list_page, self.site, 'list.html', 2)

    def options(self, out, **values):
        return Options(out, formats.by_id('audio_mp3').id, batch_safe=False, **values)

    def first_run(self, links, options):
        Runner(lambda *_: None).download(links, options)
        Runner.reset_pacing()

    def test_a_playlist_run_again_leaves_the_files_that_are_there_alone(self):
        out = self.root / 'playlist'
        self.first_run([self.base + '/list.html'], self.options(out, playlist=True))
        old = sorted(out.glob('*.mp3'))
        self.assertEqual([path.name for path in old], ['My List (1) [list-1].mp3', 'My List (2) [list-2].mp3'])
        for path in old:
            edit_tags(path)
        before = snapshot(out)
        list_page(self.site, 'list.html', 3)          # the playlist got one more entry
        self.run_links([self.base + '/list.html'], self.options(out, playlist=True))
        after = snapshot(out)
        self.assertEqual({name: after.get(name) for name in before}, before, 'the files that were there are unchanged')
        self.assertEqual(sorted(set(after) - set(before)), ['My List (3) [list-3].mp3'])
        for path in old:
            self.assertEqual((tags(path).get('title'), tags(path).get('comment')), ('Owner Edited Title', 'my notes'))
        new = tags(out / 'My List (3) [list-3].mp3')
        self.assertEqual(new.get('title'), 'My List (3)', 'the new entry is tagged as before')
        logs = self.logs()
        self.assertEqual([line for line in logs if line.startswith(SKIPPED)],
                         [zh_t('core.engine.present_skipped', name='My List (1)'),
                          zh_t('core.engine.present_skipped', name='My List (2)')])
        self.assertFalse([line for line in logs if 'does not pass filter' in line or 'has already been downloaded' in line])
        self.assertFalse([line for line in logs if line.startswith('[Metadata]') and '[list-3]' not in line])
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['files']), (1, 0, [str(out / 'My List (3) [list-3].mp3')]))
        fetched = self.requests('/a.mp4') + self.requests('/b.mp4')
        # The same playlist once more: nothing is downloaded, nothing changes, the item succeeds.
        self.events = []
        Runner.reset_pacing()
        settled = snapshot(out)
        self.run_links([self.base + '/list.html'], self.options(out, playlist=True))
        self.assertEqual(snapshot(out), settled)
        self.assertEqual((self.done()['success'], self.done()['files']), (1, []))
        self.assertEqual(self.requests('/a.mp4') + self.requests('/b.mp4'), fetched, 'what is there is never fetched again')
        self.assertEqual(fetched, 2, 'the first run only')

    def test_an_archived_link_run_again_keeps_its_file_and_its_record(self):
        archive = self.root / '资料库'
        options = self.options(self.root / 'unused', archive_root=str(archive))
        self.first_run([self.base + '/song.html'], options)
        found = sorted(archive.rglob('*.mp3'))
        self.assertEqual(len(found), 1, found)
        media = found[0]
        record = media.parent.parent / '资料信息.json'
        edit_tags(media)
        before = digest(media)
        # The record was rebuilt meanwhile (a damaged record is set aside and made anew without files).
        data = json.loads(record.read_text(encoding='utf-8'))
        data['files'] = []
        record.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.run_links([self.base + '/song.html'], options)
        self.assertEqual(digest(media), before, 'the archived file is unchanged')
        self.assertEqual(sorted(path.name for path in media.parent.iterdir()), [media.name])
        done = self.done()
        self.assertEqual((done['success'], done['files'], done['archive_errors']), (1, [str(media)], []))
        self.assertEqual(self.of('item_done')[0]['path'], str(media))
        self.assertEqual([value['filepath'] for value in self.of('media')], [str(media)])
        self.assertIn(zh_t('core.engine.kept_existing', name=media.name), self.logs())
        files = json.loads(record.read_text(encoding='utf-8'))['files']
        self.assertEqual(files, [str(Path('媒体') / media.name)], 'the record lists the file again')

    def test_odd_names_match_and_a_new_title_is_downloaded(self):
        title = 'Tom\'s "Best" & <Song> 中文 🎵 #1 ?:*|/\\ end'
        name = '我的 \'list\' & co.html'
        list_page(self.site, name, 2, title=title)
        link = self.base + '/' + urllib.parse.quote(name)
        out = self.root / 'odd'
        self.first_run([link], self.options(out, playlist=True))
        before = snapshot(out)
        self.assertEqual(len(before), 2, before)
        self.assertTrue(all("[我的 'list' & co-" in name for name in before), before)
        self.run_links([link], self.options(out, playlist=True))
        self.assertEqual(snapshot(out), before)
        self.assertEqual(len([line for line in self.logs() if line.startswith(SKIPPED)]), 2, self.logs())
        # The site renamed the entries: the same ids under another title are new files (as in 1.6.0).
        list_page(self.site, name, 2, title='Another ' + title)
        self.events = []
        Runner.reset_pacing()
        self.run_links([link], self.options(out, playlist=True))
        after = snapshot(out)
        self.assertEqual({key: after.get(key) for key in before}, before)
        self.assertEqual(len(set(after) - set(before)), 2, after)

    def test_too_many_files_and_no_config_file_leave_them_alone_too(self):
        """The fallback when the filters fit neither on the command line nor into a config file."""
        out = self.root / 'many'
        self.first_run([self.base + '/list.html'], self.options(out, playlist=True))
        for path in out.glob('*.mp3'):
            edit_tags(path)
        before = snapshot(out)
        list_page(self.site, 'list.html', 3)
        with mock.patch.object(engine, '_present_filters', return_value=['--match-filters', 'x' * 40000]), \
                mock.patch.object(engine, '_filter_config', return_value=None):
            self.run_links([self.base + '/list.html'], self.options(out, playlist=True))
        after = snapshot(out)
        self.assertEqual({name: after.get(name) for name in before}, before)
        self.assertEqual(sorted(set(after) - set(before)), ['My List (3) [list-3].mp3'])
        self.assertIn(zh_t('core.engine.present_untagged', count=2), self.logs())
        self.assertNotIn('purl', tags(out / 'My List (3) [list-3].mp3'))
        self.assertEqual(self.done()['success'], 1)

    def test_a_time_in_the_title_is_matched_by_its_underscore(self):
        """(fix round 1, v-core-1-c5-timestamp-title) The engine writes the site's '10:30' as '10_30'
        in the file name; the entries are still skipped, nothing is re-tagged."""
        list_page(self.site, 'time.html', 2, title='Live 10:30 Set')
        link = self.base + '/time.html'
        out = self.root / 'time'
        self.first_run([link], self.options(out, playlist=True))
        old = sorted(out.glob('*.mp3'))
        self.assertEqual([path.name for path in old], ['Live 10_30 Set (1) [time-1].mp3', 'Live 10_30 Set (2) [time-2].mp3'])
        for path in old:
            edit_tags(path)
        before = snapshot(out)
        self.run_links([link], self.options(out, playlist=True))
        self.assertEqual(snapshot(out), before, 'the files that were there are unchanged')
        for path in old:
            self.assertEqual((tags(path).get('title'), tags(path).get('comment')), ('Owner Edited Title', 'my notes'))
        logs = self.logs()
        self.assertEqual([line for line in logs if line.startswith(SKIPPED)],
                         [zh_t('core.engine.present_skipped', name='Live 10:30 Set (1)'),
                          zh_t('core.engine.present_skipped', name='Live 10:30 Set (2)')])
        self.assertFalse([line for line in logs if line.startswith('[Metadata]') or 'has already been downloaded' in line])
        self.assertEqual((self.done()['success'], self.done()['files']), (1, []))

    def test_many_files_go_through_a_config_file_and_keep_the_tags(self):
        """(fix round 1, v-core-1-c5-cmdline-fallback) A large folder whose filters do not fit on the
        command line: the engine reads them from a config file; the new entry is still tagged."""
        out = self.root / 'big'
        link = self.base + '/list.html'
        self.first_run([link], self.options(out, playlist=True))
        old = sorted(out.glob('*.mp3'))
        for path in old:
            edit_tags(path)
        for n in range(400):
            (out / f'Artist Name - Some Song Title {n} [zz{n:04d}].mp3').write_bytes(b'owner')
        before = snapshot(out)
        present = engine._present_media(out, formats.by_id('audio_mp3'))
        self.assertEqual(len(present), 402)
        words = engine._present_filters(present)
        command = engine.build_command(link, self.options(out, playlist=True))
        self.assertGreater(len(subprocess.list2cmdline(engine._with_filters(command, words))), engine.COMMAND_LINE_LIMIT,
                           'the filters do not fit on the command line')
        list_page(self.site, 'list.html', 3)
        commands, configs = [], []
        real = Runner._run_once

        def spy(runner, command, *args, **kwargs):
            commands.append(list(command))
            if '--config-locations' in command:
                path = Path(command[command.index('--config-locations') + 1])
                configs.append((path, path.read_text(encoding='utf-8')))
            return real(runner, command, *args, **kwargs)
        with mock.patch.object(Runner, '_run_once', spy):
            self.run_links([link], self.options(out, playlist=True))
        after = snapshot(out)
        self.assertEqual({name: after.get(name) for name in before}, before, 'the files that were there are unchanged')
        self.assertEqual(sorted(set(after) - set(before)), ['My List (3) [list-3].mp3'])
        for path in old:
            self.assertEqual((tags(path).get('title'), tags(path).get('comment')), ('Owner Edited Title', 'my notes'))
        new = tags(out / 'My List (3) [list-3].mp3')
        self.assertEqual(new.get('title'), 'My List (3)', 'the new entry is tagged')
        self.assertIn('purl', new)
        logs = self.logs()
        self.assertEqual(len([line for line in logs if line.startswith(SKIPPED)]), 2, logs)
        self.assertFalse([line for line in logs if line.startswith(UNTAGGED)], 'no fallback without tags')
        self.assertFalse([line for line in logs if line.startswith('[Metadata]') and '[list-3]' not in line])
        self.assertEqual(len(configs), 1, commands)
        path, text = configs[0]
        self.assertEqual(shlex.split(text, comments=True), words, 'the engine reads exactly the filters')
        self.assertFalse(any('--match-filters' in words_ for words_ in commands), 'nothing of it on the command line')
        self.assertFalse(path.parent.exists(), 'the config file and its run folder are removed after the item')
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['files']), (1, 0, [str(out / 'My List (3) [list-3].mp3')]))

    def audio_page(self, kind):
        """A playlist page 'Fmt List' of two entries: the MP4 videos, or audio files of ``kind``
        ('mp3', or 'webm' with Opus) made from the fixture."""
        if kind == 'mp4':
            body = ''.join(f'<video src="{letter}.mp4" poster="cover.jpg"></video>' for letter in 'ab')
        else:
            codec = {'mp3': ['-c:a', 'libmp3lame', '-b:a', '128k'], 'webm': ['-c:a', 'libopus', '-b:a', '64k']}[kind]
            for letter in 'ab':
                target = self.site / f'{letter}.{kind}'
                if not target.exists():
                    subprocess.run([str(paths.BIN / 'ffmpeg.exe'), '-y', '-v', 'error', '-i', str(FIXTURE), '-vn', *codec,
                                    str(target)], check=True, creationflags=procutil.HIDDEN, env=procutil.process_environment(),
                                   timeout=60)
            body = ''.join(f'<audio src="{letter}.{kind}"></audio>' for letter in 'ab')
        (self.site / f'fmt-{kind}.html').write_text(page('Fmt List', body), encoding='utf-8')
        return self.base + f'/fmt-{kind}.html'

    def run_formats(self, kind, first, second, out):
        """The page of ``kind`` as preset ``first``, the owner edits the tags, then as ``second`` into
        the same folder; returns (before, after) snapshots."""
        link = self.audio_page(kind)
        Runner(lambda *_: None).download([link], Options(out, formats.by_id(first).id, batch_safe=False, playlist=True))
        Runner.reset_pacing()
        for path in sorted(out.iterdir()):
            if path.suffix in ('.mp3', '.m4a', '.opus'):
                edit_tags(path)
        before = snapshot(out)
        self.events = []
        self.run_links([link], Options(out, formats.by_id(second).id, batch_safe=False, playlist=True))
        return before, snapshot(out)

    def skipped(self):
        return [line for line in self.logs() if line.startswith(SKIPPED)]

    def test_original_audio_after_another_preset_is_downloaded(self):
        """(fix round 2, v-core-2-c5-audio-best-other-format) A playlist as MP3, then as 原始最佳 into
        the same folder: the original audio (AAC as '.m4a') is downloaded beside the MP3s, which stay
        unchanged (as in 1.6.0).  The same 原始最佳 run again skips its own files."""
        out = self.root / 'best-after-mp3'
        before, after = self.run_formats('mp4', 'audio_mp3', 'audio_best', out)
        self.assertEqual(sorted(before), ['Fmt List (1) [fmt-mp4-1].mp3', 'Fmt List (2) [fmt-mp4-2].mp3'])
        self.assertEqual({name: after.get(name) for name in before}, before, 'the MP3s are unchanged')
        added = sorted(set(after) - set(before))
        self.assertEqual(added, ['Fmt List (1) [fmt-mp4-1].m4a', 'Fmt List (2) [fmt-mp4-2].m4a'])
        self.assertEqual(self.skipped(), [])
        self.assertEqual(tags(out / added[0]).get('title'), 'Fmt List (1)', 'the new files are tagged')
        done = self.done()
        self.assertEqual((done['success'], done['files']), (1, [str(out / name) for name in added]))
        for name in added:
            edit_tags(out / name)
        settled = snapshot(out)
        self.events = []
        Runner.reset_pacing()
        self.run_links([self.base + '/fmt-mp4.html'],
                       Options(out, formats.by_id('audio_best').id, batch_safe=False, playlist=True))
        self.assertEqual(snapshot(out), settled, 'its own M4As are left alone')
        self.assertEqual(len(self.skipped()), 2, self.logs())
        self.assertEqual((self.done()['success'], self.done()['files']), (1, []))

    def test_original_audio_skips_only_the_files_it_would_make(self):
        """原始最佳 counts an MP3 or M4A of the folder only when the entry's own format would make
        that file: an MP3 site is skipped over the MP3s of 原始最佳 and of the MP3 preset; an Opus
        (WebM) site is downloaded beside the M4As of the M4A preset."""
        for kind, first, added_kind, skipped in (('mp3', 'audio_best', None, 2), ('mp3', 'audio_mp3', None, 2),
                                                 ('webm', 'audio_m4a', '.opus', 0), ('webm', 'audio_best', None, 2)):
            with self.subTest(kind=kind, first=first):
                out = self.root / f'best-{kind}-{first}'
                before, after = self.run_formats(kind, first, 'audio_best', out)
                self.assertEqual(len(before), 2, before)
                self.assertEqual({name: after.get(name) for name in before}, before, 'the files there are unchanged')
                added = sorted(set(after) - set(before))
                self.assertEqual([Path(name).suffix for name in added], [added_kind] * 2 if added_kind else [], after)
                self.assertEqual(len(self.skipped()), skipped, self.logs())
                logs = self.logs()
                self.assertFalse([line for line in logs if 'has already been downloaded' in line], logs)
                self.assertFalse([line for line in logs if line.startswith('[Metadata]')
                                  and not any(name in line for name in added)], logs)
                self.assertEqual(self.done()['success'], 1)

    def test_a_title_that_changes_only_in_letter_case_is_skipped(self):
        """(fix round 2, v-core-2-c5-case-only-title-change) Windows names ignore letter case: the
        engine would find 'my song (1) [id].mp3' under 'My Song (1)' and re-tag it."""
        list_page(self.site, 'case.html', 2, title='my song')
        link = self.base + '/case.html'
        out = self.root / 'case'
        self.first_run([link], self.options(out, playlist=True))
        old = sorted(out.glob('*.mp3'))
        self.assertEqual([path.name for path in old], ['my song (1) [case-1].mp3', 'my song (2) [case-2].mp3'])
        for path in old:
            edit_tags(path)
        before = snapshot(out)
        list_page(self.site, 'case.html', 2, title='My Song')
        self.run_links([link], self.options(out, playlist=True))
        self.assertEqual(snapshot(out), before, 'the files that were there are unchanged')
        self.assertEqual(sorted(os.listdir(out)), [path.name for path in old])
        for path in old:
            self.assertEqual(tags(path).get('title'), 'Owner Edited Title')
        self.assertEqual(self.skipped(), [zh_t('core.engine.present_skipped', name='My Song (1)'),
                                          zh_t('core.engine.present_skipped', name='My Song (2)')])
        self.assertFalse([line for line in self.logs() if line.startswith('[Metadata]') or 'has already been downloaded' in line])
        self.assertEqual((self.done()['success'], self.done()['files']), (1, []))

    def test_the_filter_config_file(self):
        folder = self.root / 'conf'
        folder.mkdir(parents=True)
        words = engine._present_filters({"我的 'list' & co-1": ['Tom\'s "Best" & <Song> 中文 🎵 #1 ?:*|/\\ end'],
                                         'b2': ['B $HOME %TEMP% ~x']})
        loaded = engine._filter_config(words, folder)
        self.assertEqual(loaded, ['--config-locations', os.path.abspath(folder / engine.FILTER_CONFIG)])
        text = (folder / engine.FILTER_CONFIG).read_text(encoding='utf-8')
        self.assertTrue(text.isascii())
        self.assertEqual(shlex.split(text, comments=True), words)
        self.assertIsNone(engine._filter_config(words, self.root / 'missing'), 'a folder that is not there')
        odd = self.root / '%TEMP%'
        odd.mkdir()
        self.assertIsNone(engine._filter_config(words, odd), 'a path the engine would expand')
        self.assertFalse((odd / engine.FILTER_CONFIG).exists())
        unknown = self.root / '$NO_SUCH_NAME_V170'
        unknown.mkdir()
        self.assertIsNone(engine._filter_config(words, unknown), 'a name the engine might know')

    def test_the_option_check_covers_the_words_the_runner_adds(self):
        """(fix round 1, v-core-1-probe-match-filters) A new engine must accept --match-filters,
        --config-locations and -k too."""
        arguments = engine.probe_args()
        for word in ('--match-filters', '--config-locations', engine.KEEP_SOURCE):
            self.assertIn(word, arguments)
        run = subprocess.run([str(paths.VENDOR / 'yt-dlp.exe'), *arguments], capture_output=True, encoding='utf-8',
                             errors='replace', timeout=120, stdin=subprocess.DEVNULL, env=procutil.process_environment(),
                             cwd=str(paths.BIN), creationflags=procutil.HIDDEN)
        output = run.stdout + run.stderr
        self.assertNotIn('no such option', output.lower(), output[-800:])
        self.assertIn('You must provide at least one URL', output, output[-800:])

    def test_which_files_are_present_and_the_filter_words(self):
        out = self.root / 'rule'
        out.mkdir(parents=True)
        for name in ('A [x1].mp3', 'A [x1].MP3.part', 'B [x2].f251.mp3', 'C [x3].mp4', 'D [X4].mp3', 'E (2) [x1].mp3',
                     'plain.mp3', 'F [x5].jpg'):
            (out / name).write_bytes(b'owner')
        (out / 'G [x6].mp3').mkdir()
        self.assertEqual(engine._present_media(out, formats.by_id('audio_mp3')), {'x1': ['A', 'E (2)'], 'X4': ['D']})
        self.assertEqual(engine._present_media(out, formats.by_id('video_mp4')), {'x3': ['C']})
        self.assertEqual(engine._present_media(out / 'missing', formats.by_id('audio_mp3')), {})
        self.assertEqual(engine._filter_text("a-b'c&d 中_9"), "a\\u002db\\u0027c\\u0026d\\u0020\\u4e2d_9")
        self.assertEqual(engine._filter_text('🎵'), '\\U0001f3b5')
        gap = '[\\W_]*'
        self.assertEqual(engine.SKELETON_GAP, gap)
        self.assertEqual(engine._skeleton('My List (1)'), gap.join('MyList1'))
        self.assertEqual(engine._skeleton('Twelve characters plus more'), gap.join('Twelvecharac'))
        # (fix round 1) Letters and digits only: an underscore of the file name ('10:30' -> '10_30')
        # is a gap, so the site's '10:30' matches; an underscore of the site's title too.
        self.assertEqual(engine._skeleton('Live 10_30 Set'), gap.join('Live1030Set'))
        self.assertEqual(engine._skeleton('__init__ Talk'), gap.join('initTalk'))
        words = engine._present_filters({'x1': ['A', 'E (2)'], 'X4': ['D']})
        # (fix round 2) The title test ignores letter case, as Windows file names do.
        self.assertEqual(words, ['--match-filters', "id!~='^(?:X4|x1)$'",
                                 '--match-filters', f"id~='^X4$'&title!~='(?i)^{gap}(?:D)'",
                                 '--match-filters', f"id~='^x1$'&title!~='(?i)^{gap}(?:A|E{gap}2)'"])
        for site_title, file_title, same in (('Live 10:30 Set (1)', 'Live 10_30 Set (1)', True),
                                             ('__init__ Talk (2)', '__init__ Talk (2)', True),
                                             ('Tom\'s "Best" Mix?', "Tom's 'Best' Mix", True),
                                             ('My Song (1)', 'my song (1)', True),
                                             ('ÄRGER (1)', 'ärger (1)', True),
                                             ('Live 10:31 Set (1)', 'Live 10_30 Set (1)', False)):
            pattern = '(?i)^' + gap + '(?:' + engine._skeleton(file_title) + ')'
            self.assertEqual(bool(re.search(pattern, site_title)), same, (site_title, file_title))
        # (fix round 2) 原始最佳: files whose extension another preset's conversion gives too count only
        # for an entry whose chosen format would make them.
        for name in ('M [m1].m4a', 'P [p1].mp3', 'O [o1].opus', 'F [f1].flac', 'F [f1].wav', 'Q [q1].mp3', 'Q (2) [q1].ogg',
                     'R [r1].mp3', 'R [r1].m4a'):
            (out / name).write_bytes(b'owner')
        shared = {}
        best = engine._present_media(out, formats.by_id('audio_best'), shared)
        self.assertEqual(best['o1'], ['O'])
        self.assertEqual(shared, {('x1', 'A'): {'.mp3'}, ('X4', 'D'): {'.mp3'}, ('x1', 'E (2)'): {'.mp3'},
                                  ('m1', 'M'): {'.m4a'}, ('p1', 'P'): {'.mp3'}, ('f1', 'F'): {'.flac', '.wav'},
                                  ('q1', 'Q'): {'.mp3'}, ('r1', 'R'): {'.mp3', '.m4a'}})
        other = {}
        engine._present_media(out, formats.by_id('audio_mp3'), other)
        self.assertEqual(other, {}, 'only 原始最佳 has such files')
        words = engine._present_filters(best, shared)
        tests = set(words[1::2])
        skeleton = engine._skeleton
        self.assertIn(f"id~='^m1$'&ext~='^(?:webm|opus|ogg|oga|mp3|flac|wav)$'", tests)
        self.assertIn(f"id~='^p1$'&ext!~='^(?:mp3)$'", tests)
        self.assertIn(f"id~='^f1$'&ext!~='^(?:flac|wav)$'", tests)
        self.assertIn(f"id~='^q1$'&title!~='(?i)^{gap}(?:{skeleton('Q (2)')})'&ext!~='^(?:mp3)$'", tests)
        self.assertIn(f"id~='^r1$'&ext~='^(?:webm|opus|ogg|oga|flac|wav)$'", tests)
        self.assertFalse([word for word in tests if word.startswith("id~='^o1$'&ext")], 'Opus counts for every format')
        self.assertEqual(engine._format_test({'.m4a', '.mp3', '.flac', '.wav'}), "ext~='^(?:webm|opus|ogg|oga)$'")
        self.assertEqual(engine._present_filters(best), engine._present_filters(best, {}),
                         'without shared files the words are those of the other presets')
        for word in words[1::2]:
            self.assertNotIn(' ', word)
            self.assertEqual(word.count('&'), word.count("'&") , 'an & only between two conditions')
        command = engine._with_filters(['yt-dlp.exe', '-x', '--', 'https://e/x'], ['--match-filters', 'id!~=a'])
        self.assertEqual(command, ['yt-dlp.exe', '-x', '--match-filters', 'id!~=a', '--', 'https://e/x'])

    def test_a_single_link_is_not_filtered(self):
        out = self.root / 'single'
        out.mkdir(parents=True)
        (out / 'Artist - Song (1) [song-1].mp3').write_bytes(b'owner')
        commands = []

        def run_once(runner, command, *_args):
            commands.append(list(command))
            return 0, False, None

        for options in (self.options(out), self.options(out, naming='title_id')):
            with mock.patch.object(Runner, '_run_once', run_once):
                self.run_links([self.base + '/song.html'], options)
        self.assertEqual(len(commands), 2)
        self.assertFalse(any('--match-filters' in command for command in commands))

    def test_the_bundled_engine_knows_every_option_the_fixes_use(self):
        run = subprocess.run([str(paths.VENDOR / 'yt-dlp.exe'), '--ignore-config', '--help'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', env=procutil.process_environment(), cwd=str(paths.BIN),
                             creationflags=procutil.HIDDEN, timeout=120)
        for option in ('--match-filters', '--ignore-config', '--version', '-k, --keep-video', '--config-locations'):
            self.assertIn(option, run.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
