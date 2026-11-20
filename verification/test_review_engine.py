# tier: all
"""Regression tests for the confirmed engine-track findings of the 1.6.0 core review
(docs/1.6.0/handoff/review-core-findings.json, track A-engine; core-fix-engine.md).

Tier "all": most findings are about what the bundled yt-dlp does (its wording, its timing, its
files), so they are checked with the real engine against a local site; the unit checks of the
same fixes run in the same file.  No network: every site is 127.0.0.1.
"""
import _sandbox
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import applog
import engine
import formats
import i18n
import library
import paths
import procutil
import unfinished
from engine import MODES, Options, Runner


# (2.0.0 S3 W4) the zh text of a catalog key, whatever the current language (S3-DESIGN 4.3)
from _sandbox import zh_t


def head(key, name):
    """The zh text of a template before its placeholder ``name``: how a log line of that key starts."""
    return zh_t(key, **{name: '\0'}).split('\0')[0]


KEPT_EXISTING = head('core.engine.kept_existing', 'name')
OTHER_FORMAT = head('core.engine.other_format_source', 'name')
PICTURE_EXISTS = head('core.engine.picture_exists', 'name')
CANNOT_START_ENGINE = head('core.engine.cannot_start_engine', 'error')
# every "the save folder already has ..." notice of the engine
PRESENT_NOTICES = (KEPT_EXISTING, OTHER_FORMAT, PICTURE_EXISTS, head('core.engine.present_skipped', 'name'),
                   head('core.engine.present_untagged', 'count'))

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'verification' / 'fixture' / 'fixture.mp4'
SONG_TITLE = 'Artist - Song'


# --------------------------------------------------------------------------- local site

class _Handler(SimpleHTTPRequestHandler):
    """Files of the site folder (with Range, so a partial download can resume), /status/<code>
    pages, and /inject (a reason phrase that carries CRs and fake markers)."""
    inject = b''

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith('/status/'):
            self.send_error(int(self.path.split('/')[2]))
            return
        if self.path.startswith('/inject'):
            self.close_connection = True
            self.wfile.write(b'HTTP/1.1 404 ' + self.inject + b'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
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


def start_site(folder, inject=b''):
    handler = type('Handler', (_Handler,), {'inject': inject})
    server = _Server(('127.0.0.1', 0), partial(handler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}', server


def make_site(folder):
    """fixture.mp4, a cover picture and song.html (a page with that video and an og:image)."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE, folder / 'fixture.mp4')
    if not (folder / 'cover.jpg').exists():
        subprocess.run([str(paths.BIN / 'ffmpeg.exe'), '-y', '-v', 'error', '-i', str(folder / 'fixture.mp4'),
                        '-frames:v', '1', '-vf', 'scale=320:180', str(folder / 'cover.jpg')],
                       check=True, creationflags=procutil.HIDDEN, env=procutil.process_environment(), timeout=60)
    (folder / 'song.html').write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<title>{SONG_TITLE}</title><meta property="og:title" content="{SONG_TITLE}">'
        '<meta property="og:image" content="cover.jpg"></head>'
        '<body><video src="fixture.mp4" poster="cover.jpg"></video></body></html>', encoding='utf-8')


def area(name):
    folder = _sandbox.OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(folder):
    return {path.name: sha256(path) for path in Path(folder).iterdir() if path.is_file()}


def media_files(folder):
    folder = Path(folder)
    return sorted(path.name for path in folder.iterdir() if path.name != paths.OWNED) if folder.is_dir() else []


def search(root, needles, skip=()):
    """{needle: [files below root that contain it]} (UTF-8 and UTF-16 bytes)."""
    hits = {needle: [] for needle in needles}
    skip = [Path(entry).resolve() for entry in skip]
    for folder, _folders, names in os.walk(root):
        for name in names:
            path = Path(folder) / name
            if any(path.resolve().is_relative_to(entry) for entry in skip):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            for needle in needles:
                if needle.encode('utf-8') in data or needle.encode('utf-16-le') in data:
                    hits[needle].append(str(path))
    return {needle: found for needle, found in hits.items() if found}


class EngineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()
        cls.site = area('site')
        make_site(cls.site)
        cls.base, cls.server = start_site(cls.site)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Runner.reset_pacing()
        patcher = mock.patch.object(engine, 'PACE_SECONDS', (0, 0))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(tempfile.mkdtemp(dir=area('runs')))
        self.events = []
        with unfinished.LOCK:
            unfinished.save([])

    def emit(self, kind, value):
        self.events.append((kind, value))

    def run_links(self, links, options, runner=None, overrides=None):
        runner = runner or Runner(self.emit)
        runner.download(links, options, overrides)
        return runner

    def of(self, kind):
        return [value for name, value in self.events if name == kind]

    def done(self):
        values = self.of('done')
        self.assertEqual(len(values), 1, 'exactly one done event')
        self.assertEqual(self.events[-1][0], 'done', 'done is the last event')
        return values[0]


# --------------------------------------------------------------------------- review-privacy-security-1

class CookieLineTests(EngineCase):
    """No byte of a cookie value from the chosen cookies file reaches the log, an item's error, the
    unfinished list or the history (H6)."""

    MARKERS = {'six_fields': '.youtube.com\tTRUE\t/\tTRUE\tSID\t{m}\n',
               'float_expiry': '.youtube.com\tTRUE\t/\tTRUE\t1893456000.5\tSID\t{m}\n',
               'eight_fields': '.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{m}\textra\n',
               'spaces': '.youtube.com TRUE / TRUE 0 SID {m}\n',
               'negative_expiry': '.youtube.com\tTRUE\t/\tTRUE\t-1\tSID\t{m}\n',
               'httponly_six': '#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\tSID\t{m}\n',
               'superscript_expiry': '.youtube.com\tTRUE\t/\tTRUE\t\u00b2\tSID\t{m}\n',
               'carriage_return': '.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tX{m}\rTAIL{m}\n'}

    def cookies_file(self, folder, good):
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / 'cookies.txt'
        text = '# Netscape HTTP Cookie File\n' + f'127.0.0.1\tFALSE\t/\tFALSE\t0\tgood\t{good}\n'
        markers = {}
        for name, line in self.MARKERS.items():
            markers[name] = f'MKSECRET{name.upper()}Z'
            text += line.format(m=markers[name])
        file.write_text(text, encoding='utf-8')
        return file, markers

    def test_malformed_lines_of_a_real_cookies_file_never_reach_any_output(self):
        good = 'MKGOODVALUEZ'
        cookie_folder = self.root / 'cookiehome'
        file, markers = self.cookies_file(cookie_folder, good)
        applog.setup()
        self.addCleanup(applog.shutdown)
        logger = applog.get('ui')
        history = library.Library(self.root / 'history.sqlite3')
        links = [self.base + '/fixture.mp4', self.base + '/status/404']
        job = history.create_job('download', 'cookies', {'urls': links})

        def emit(kind, value):
            # What the window does: every log line goes to download.log; the history kinds to the library.
            if kind == 'log':
                logger.info(str(value))
            if kind in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done'):
                history.download_event(job, kind, value)
            self.emit(kind, value)

        options = Options(self.root / 'out', MODES[0], batch_safe=False, cookies=str(file))
        Runner(emit).download(links, options)
        done = self.done()
        self.assertEqual((done['success'], done['failed']), (1, 1))
        for handler in applog.get().handlers:
            handler.flush()
        dump = _sandbox.out('events', f'{self.id()}.json')
        dump.write_text(json.dumps(self.events, ensure_ascii=False, default=str), encoding='utf-8')
        logs = [value for value in self.of('log') if isinstance(value, str)]
        self.assertIn(engine.COOKIE_LINE_SKIPPED, logs, 'the skipped lines are reported in Chinese')
        self.assertFalse([line for line in logs if 'cookie file entry' in line])
        # The unfinished list holds the failed link (with its error) and the history the job.
        self.assertEqual([entry['reason'] for entry in unfinished.load()], ['failed'])
        self.assertTrue(paths.log_file().is_file())
        needles = [good, *markers.values(), 'TAILMK']
        self.assertEqual(search(_sandbox.BASE, needles, skip=[cookie_folder]), {},
                         'a cookie value was found outside the cookies file')

    def test_a_file_that_is_not_a_cookies_file_is_refused_before_the_engine_reads_it(self):
        notes = self.root / '密码备忘.txt'
        notes.write_text('我的笔记\nPRIVATE_NOTE_99 secret\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Netscape'):
            engine.check_cookie_file(notes)
        self.run_links([self.base + '/fixture.mp4'], Options(self.root / 'out', MODES[0], cookies=str(notes)))
        done = self.done()
        self.assertEqual(done['failed'], 1)
        item_done = self.of('item_done')[0]
        self.assertIn('Netscape', item_done['error'])
        self.assertNotIn('PRIVATE_NOTE_99', json.dumps(self.events, ensure_ascii=False))
        self.assertEqual(search(_sandbox.BASE, ['PRIVATE_NOTE_99'], skip=[notes]), {})

    def test_check_cookie_file_mirrors_what_the_engine_accepts(self):
        def write(name, text, encoding='utf-8'):
            path = self.root / name
            path.write_text(text, encoding=encoding)
            return path

        header = '# Netscape HTTP Cookie File\n'
        entry = '.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=1\n'
        for name, text in (('plain.txt', header + entry), ('short.txt', '# HTTP Cookie File\n' + entry),
                           ('only_header.txt', header), ('bad_first.txt', 'junk line\n' + header + entry)):
            self.assertEqual(engine.check_cookie_file(write(name, text)), self.root / name, name)
        self.assertTrue(engine.check_cookie_file(write('bom.txt', header + entry, 'utf-8-sig')).is_file())
        for name, text in (('no_header.txt', entry), ('notes.txt', 'hello\nworld\n'), ('blank_first.txt', '\n' + header),
                           ('httponly_first.txt', '#HttpOnly_' + entry), ('empty.txt', '')):
            with self.assertRaisesRegex(ValueError, 'Netscape', msg=name):
                engine.check_cookie_file(write(name, text))
        with self.assertRaisesRegex(ValueError, 'JSON'):
            engine.check_cookie_file(write('cookies.json', '[{"domain": ".youtube.com"}]'))
        # A relative path comes back absolute (the engine's working folder is the program's bin folder).
        # (Change into the folder first: os.path.relpath fails when the sandbox and the working
        # folder are on different drives, as in the owner's layout with the sources on D:.)
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            checked = engine.check_cookie_file('plain.txt')
        finally:
            os.chdir(previous)
        self.assertTrue(checked.is_absolute())
        self.assertEqual(checked, self.root / 'plain.txt')

    def test_every_wording_that_repeats_a_cookies_line_is_replaced(self):
        runner = Runner(self.emit)
        runner._current = engine.QueueItem(1, 'https://www.youtube.com/watch?v=abc',
                                           Options(self.root, cookies=str(self.root / 'c.txt')))
        echoes = ["WARNING: skipping cookie file entry due to invalid length 6: '.youtube.com\\tTRUE\\t/\\tTRUE\\tSID\\tMK1\\n'",
                  "WARNING: skipping cookie file entry due to invalid expires at -1: '.x\\tTRUE\\t/\\tTRUE\\t-1\\tSID\\tMK1\\n'",
                  "ERROR: invalid Netscape format cookies file 'C:\\\\c.txt': '.youtube.com\\tTRUE\\t/\\tTRUE\\t\u00b2\\tSID\\tMK1\\n'",
                  'WARNING: something new about line .youtube.com\tTRUE\t/\tTRUE\t0\tSID\tMK1']
        for line in echoes:
            kind, value = runner._intercept('log', line)
            self.assertEqual(kind, 'log')
            self.assertNotIn('MK1', value)
            self.assertIn(value, (engine.COOKIE_LINE_SKIPPED, engine.COOKIE_LINE_FAILED))
            runner.last_error = ''
            runner._note_line(line)
            self.assertNotIn('MK1', runner.last_error)
        self.assertEqual(runner._intercept('log', echoes[2])[1], engine.COOKIE_LINE_FAILED)
        # Lines that only name the file, and ordinary lines, stay as they are.
        for line in ("ERROR: 'C:\\c.txt' does not look like a Netscape format cookies file",
                     'ERROR: Cookies file must be Netscape formatted, not JSON. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ',
                     '[download] Destination: C:\\out\\a [b].mp4',
                     'ERROR: [generic] x: Unable to download webpage: HTTP Error 404: Not Found'):
            self.assertEqual(runner._intercept('log', line), ('log', line))
        # Without cookies, a line with tabs is an ordinary line.
        runner._current = engine.QueueItem(1, 'https://example.com/a', Options(self.root))
        tabbed = 'note\ta\tb\tc\td'
        self.assertEqual(runner._intercept('log', tabbed), ('log', tabbed))


# --------------------------------------------------------------------------- review-e2e-1

class StopAfterTheFileTests(EngineCase):
    def test_stop_after_the_file_was_delivered_reports_the_item_done(self):
        out = self.root / 'out'
        runner = Runner(self.emit)

        def emit(kind, value):
            self.emit(kind, value)
            if kind == 'media':
                runner.cancel()     # the file is moved and renamed; the engine is still running

        runner.emit = emit
        runner.download([self.base + '/song.html'], Options(out, formats.by_id('audio_mp3').id))
        done = self.done()
        item_done = self.of('item_done')[0]
        final = self.of('media')[0]['filepath']
        self.assertTrue(Path(final).is_file())
        self.assertEqual((item_done['success'], item_done['cancelled'], item_done['path']), (True, False, final))
        self.assertEqual((done['success'], done['cancelled'], done['files'], done['pending_urls']), (1, True, [final], []))
        self.assertEqual(self.of('file'), [final])
        self.assertEqual(unfinished.load(), [], 'a finished item is not put into the unfinished list')
        self.assertEqual(media_files(out), [Path(final).name])

    def test_a_link_for_several_files_stays_stopped(self):
        out = self.root / 'out'
        script = ('import json, os, sys, time\n'
                  'folder = sys.argv[1]\n'
                  'os.makedirs(folder, exist_ok=True)\n'
                  'path = os.path.join(folder, "一 [p1].opus")\n'
                  'open(path, "wb").write(b"media")\n'
                  'print("__GUI_MEDIA__" + json.dumps(dict(id="p1", extractor_key="Youtube", title="一", filepath=path)), flush=True)\n'
                  'print("__GUI_FILE__" + json.dumps(path), flush=True)\n'
                  'time.sleep(30)\n')
        runner = Runner(self.emit)

        def emit(kind, value):
            self.emit(kind, value)
            if kind == 'file':
                runner.cancel()

        runner.emit = emit
        real = engine.build_command

        def command(url, options):
            built = real(url, options)
            return [sys.executable, '-c', script, built[built.index('-P') + 1], '--', url]

        with mock.patch.object(engine, 'build_command', command):
            runner.download(['https://www.youtube.com/playlist?list=PLx'], Options(out, playlist=True))
        item_done = self.of('item_done')[0]
        self.assertEqual((item_done['success'], item_done['cancelled']), (False, True))
        self.assertEqual([entry['reason'] for entry in unfinished.load()], ['stopped'])


# --------------------------------------------------------------------------- WP2-q1

class ExistingFileTests(EngineCase):
    def test_a_file_that_was_there_before_the_item_is_not_changed(self):
        for preset_id in ('audio_mp3', 'audio_flac'):
            for style in ('title', 'title_id'):
                with self.subTest(preset=preset_id, style=style):
                    out = self.root / f'{preset_id}-{style}'
                    mode = formats.by_id(preset_id).id
                    self.events = []
                    Runner.reset_pacing()
                    self.run_links([self.base + '/song.html'], Options(out, mode, naming='title_id'))
                    before = snapshot(out)
                    self.assertEqual(len(before), 1, before)
                    old = str(out / next(iter(before)))
                    work_before = media_files(paths.work_dir())
                    self.events = []
                    Runner.reset_pacing()
                    self.run_links([self.base + '/song.html'], Options(out, mode, naming=style))
                    self.assertEqual(snapshot(out), before, 'the old file is unchanged and nothing was added')
                    done = self.done()
                    item_done = self.of('item_done')[0]
                    self.assertEqual((done['success'], done['files']), (1, [old]))
                    self.assertEqual((item_done['success'], item_done['path']), (True, old))
                    self.assertEqual([value['filepath'] for value in self.of('media')], [old])
                    self.assertEqual(self.of('file'), [old])
                    self.assertTrue(any(str(line).startswith(KEPT_EXISTING) for line in self.of('log')))
                    self.assertEqual(media_files(paths.work_dir()), work_before, 'no cover picture is left in the work folder')
                    self.assertEqual(unfinished.load(), [])


# --------------------------------------------------------------------------- fix round 1: core-rv-engine-1-1

class OtherFormatTests(EngineCase):
    """An older file of the same link in another format (the source of a conversion) is never
    reported as the requested file and never deleted: the item is converted from it with -k."""

    def history(self):
        store = library.Library(self.root / 'history.sqlite3')
        job = store.create_job('download', 'other format', {'urls': [self.base + '/song.html']})

        def emit(kind, value):
            store.download_event(job, kind, value)
            self.emit(kind, value)
        return store, emit

    def test_an_older_mp4_is_converted_and_kept(self):
        link = self.base + '/song.html'
        for preset_id, extension in (('audio_mp3', '.mp3'), ('video_mkv', '.mkv')):
            with self.subTest(preset=preset_id):
                out = self.root / f'from-mp4-{preset_id}'
                self.events = []
                Runner.reset_pacing()
                self.run_links([link], Options(out, formats.by_id('video_mp4').id, naming='title_id'))
                before = snapshot(out)
                self.assertEqual([Path(name).suffix for name in before], ['.mp4'], before)
                with unfinished.LOCK:
                    unfinished.save([])
                store, emit = self.history()
                work_before = media_files(paths.work_dir())
                self.events = []
                Runner.reset_pacing()
                Runner(emit, library=store).download([link], Options(out, formats.by_id(preset_id).id, naming='title'))
                after = snapshot(out)
                done = self.done()
                item_done = self.of('item_done')[0]
                for name, digest in before.items():
                    self.assertEqual(after.get(name), digest, 'the older MP4 is unchanged')
                added = sorted(set(after) - set(before))
                self.assertEqual([Path(name).suffix for name in added], [extension], after)
                final = str(out / added[0])
                self.assertEqual((done['success'], done['failed'], done['files']), (1, 0, [final]))
                self.assertEqual((item_done['success'], item_done['path'], item_done['format']), (True, final, preset_id))
                self.assertEqual([value['filepath'] for value in self.of('media')], [final])
                self.assertEqual(unfinished.load(), [])
                row = store.find_download([engine.QueueItem(1, link, Options(out)).key], preset_id)
                self.assertEqual((row or {}).get('path'), final, 'the history holds the requested file')
                logs = [str(line) for line in self.of('log')]
                self.assertTrue(any(line.startswith(OTHER_FORMAT) for line in logs), logs)
                self.assertFalse(any(line.startswith(KEPT_EXISTING) for line in logs), logs)
                self.assertEqual(media_files(paths.work_dir()), work_before, 'nothing is left in the work folder')

    def test_stop_during_audio_extraction_then_retry_gives_the_requested_format(self):
        link = self.base + '/song.html'
        out = self.root / 'stop-extract'
        runner = Runner(self.emit)
        stopped = []

        def emit(kind, value):
            self.emit(kind, value)
            if kind == 'log' and isinstance(value, str) and value.startswith('[ExtractAudio]') and not stopped:
                stopped.append(value)
                runner.cancel()

        runner.emit = emit
        runner.download([link], Options(out, formats.by_id('audio_mp3').id, naming='title_id'))
        self.assertTrue(stopped, 'the run reached the audio extraction')
        entries = unfinished.load()
        self.assertEqual([entry['reason'] for entry in entries], ['stopped'])
        left = snapshot(out)
        sources = {name: digest for name, digest in left.items() if name.endswith('.mp4')}
        self.assertEqual(len(sources), 1, left)
        # The retry from the unfinished list, as the window does it.
        self.events = []
        Runner.reset_pacing()
        retry = Options.from_dict(entries[0]['options'], proxy='', cookies='', batch_safe=True, skip_downloaded=False)
        self.run_links([entries[0]['url']], retry)
        done = self.done()
        item_done = self.of('item_done')[0]
        self.assertEqual((done['success'], item_done['success']), (1, True))
        self.assertTrue(item_done['path'].endswith('.mp3') and Path(item_done['path']).is_file(), item_done['path'])
        after = snapshot(out)
        for name, digest in sources.items():
            self.assertEqual(after.get(name), digest, 'the MP4 that was left is not deleted or changed')
        self.assertEqual(unfinished.load(), [])
        probe = subprocess.run([str(paths.BIN / 'ffprobe.exe'), '-v', 'error', '-show_entries', 'stream=codec_name',
                                '-of', 'csv=p=0', item_done['path']], capture_output=True, text=True,
                               creationflags=procutil.HIDDEN, env=procutil.process_environment(), timeout=60)
        self.assertIn('mp3', probe.stdout)

    def test_which_reported_files_are_the_requested_file(self):
        by_id = formats.by_id
        table = [('X [a].mp3', 'audio_mp3', True), ('X [a].mp4', 'audio_mp3', False), ('X [a].MP3', 'audio_mp3', True),
                 ('X [a].flac', 'audio_flac', True), ('X [a].m4a', 'audio_flac', False), ('X [a].wav', 'audio_wav', True),
                 ('X [a].m4a', 'audio_m4a', True), ('X [a].mp4', 'audio_m4a', False), ('X [a].mkv', 'video_mkv', True),
                 ('X [a].mp4', 'video_mkv', False), ('X [a].mp4', 'video_mp4', True), ('X [a].webm', 'video_mp4', False),
                 ('X [a].opus', 'audio_best', True), ('X [a].m4a', 'audio_best', True), ('X [a].mp4', 'audio_best', False),
                 ('X [a].webm', 'audio_best', False), ('X [a].f137.mp4', 'video_mp4', False),
                 ('X [a].f251.webm', 'audio_best', False), ('X [a].fhls-720p.mp4', 'video_mp4', False),
                 ('Song feat. B [a].mp4', 'video_mp4', True)]
        for name, preset_id, expected in table:
            self.assertEqual(engine._is_final_file('C:\\out\\' + name, by_id(preset_id)), expected, (name, preset_id))
        command = ['yt-dlp.exe', '--no-overwrites', '-x', '--', 'https://e/-k']
        self.assertEqual(engine._keeping_source(command), ['yt-dlp.exe', '--no-overwrites', '-x', '-k', '--', 'https://e/-k'])
        self.assertEqual(engine._keeping_source(engine._keeping_source(command)).count('-k'), 1)
        self.assertEqual(command[-2:], ['--', 'https://e/-k'], 'the original command is not changed')

    def test_a_format_part_or_a_line_before_the_title_never_stops_the_engine(self):
        out = self.root / 'parts'
        out.mkdir(parents=True)
        part = out / 'X [a].f137.mp4'
        final = out / 'X [a].mp4'
        for path in (part, final):
            path.write_bytes(b'data')
        runner = Runner(self.emit)
        runner._current = engine.QueueItem(1, 'https://example.com/a', Options(out, formats.by_id('video_mp4').id))
        runner._existing = {engine._normal(part), engine._normal(final)}
        stops = []
        runner._stop_engine = lambda: stops.append(1)
        line = '[download] {} has already been downloaded'
        # Before the item's sealed title line: never.
        self.assertEqual(runner._intercept('log', line.format(final)), ('log', line.format(final)))
        self.assertEqual((runner._kept, runner._source, stops), (None, None, []))
        runner._intercept('title', 'X')
        # A format part is merged and removed by the engine as usual.
        runner._intercept('log', line.format(part))
        self.assertEqual((runner._kept, runner._source, stops), (None, None, []))
        runner._intercept('log', line.format(final))
        self.assertEqual((runner._kept, stops), (str(final), [1]))


# --------------------------------------------------------------------------- final fix round 1: rv-regression-gate-1-1

class KeepFromTheStartTests(EngineCase):
    """An older file of the link in another format is seen before the item starts, and the item
    runs with -k from the start: the engine converts the file at once after its "already
    downloaded" line, often before it could be stopped (the race of the earlier fix)."""

    def test_which_files_count_as_another_format_of_the_link(self):
        out = 'C:\\out\\'
        listing = [out + name for name in (
            'Artist - Song [song].mp4', 'Artist - Song [song].MKV', 'Other [song-2].webm', 'Song [song].f137.mp4',
            'Song [song].mp4.part', 'Song [song].jpg', 'Song [song].mp3', 'Song [songs].mp4', 'Song [other].mp4',
            'Song [song].vtt', 'Song.mp4')]
        mp3 = formats.by_id('audio_mp3')
        self.assertEqual(engine._other_formats(listing, 'http://127.0.0.1/song.html', mp3),
                         sorted(out + name for name in ('Artist - Song [song].mp4', 'Artist - Song [song].MKV',
                                                        'Other [song-2].webm')))
        self.assertEqual(engine._other_formats(listing, 'http://127.0.0.1/song.html', formats.by_id('video_mkv')),
                         sorted(out + name for name in ('Artist - Song [song].mp4', 'Other [song-2].webm')))
        best = engine._other_formats(listing, 'http://127.0.0.1/song.html', formats.by_id('audio_best'))
        self.assertNotIn(out + 'Song [song].mp3', best, 'a finished audio file is not a source for audio_best')
        self.assertEqual(engine._other_formats(listing, 'http://127.0.0.1/', mp3), [], 'no id, no guess')
        self.assertEqual(engine._other_formats(listing, 'http://127.0.0.1/song.html', None), [])

    def test_the_item_starts_with_keep_and_runs_once(self):
        for existing, keep in (('Artist - Song [song].mp4', True), ('Artist - Song [song].mp3', False),
                               ('Artist - Song [other].mp4', False), (None, False)):
            with self.subTest(existing=existing):
                out = self.root / f'keep-{keep}-{existing}'
                out.mkdir(parents=True)
                if existing:
                    (out / existing).write_bytes(b'owner')
                commands = []

                def run_once(runner, command, *_args):
                    commands.append(list(command))
                    return 0, False, None

                self.events = []
                with mock.patch.object(Runner, '_run_once', run_once):
                    runner = self.run_links([self.base + '/song.html'], Options(out, formats.by_id('audio_mp3').id))
                self.assertEqual(len(commands), 1, 'one run, no stop and rerun')
                self.assertEqual(commands[0].count('-k'), 1 if keep else 0)
                self.assertEqual(commands[0][-2:], ['--', self.base + '/song.html'], 'the link stays last')
                self.assertEqual(runner._keep_early, keep)

    def test_the_already_downloaded_line_does_not_stop_a_run_with_keep(self):
        out = self.root / 'line'
        out.mkdir(parents=True)
        source = out / 'Artist - Song [song].mp4'
        source.write_bytes(b'owner')
        line = f'[download] {source} has already been downloaded'
        for early, stopped in ((True, []), (False, [1])):
            with self.subTest(early=early):
                runner = Runner(self.emit)
                runner._current = engine.QueueItem(1, self.base + '/song.html', Options(out, formats.by_id('audio_mp3').id))
                runner._existing = {engine._normal(source)}
                runner._keep_source = runner._keep_early = early
                stops = []
                runner._stop_engine = lambda: stops.append(1)
                self.events = []
                runner._intercept('title', 'Artist - Song')
                self.assertEqual(runner._intercept('log', line), ('log', line))
                self.assertEqual((runner._source, runner._kept, stops), (str(source), None, stopped))
                said = [value for value in self.of('log') if str(value).startswith(OTHER_FORMAT)]
                self.assertEqual(len(said), 1 if early else 0, said)

    def test_what_keep_kept_of_a_new_download_is_removed(self):
        out = self.root / 'kept'
        out.mkdir(parents=True)
        old = ['Old Title [song].mp4', 'Mine [song].webm', 'Other [x].mp4']
        new_removed = ['Artist - Song [song].mp4', 'Artist - Song [song].f137.mp4', 'Artist - Song [song].f251.webm',
                       'Artist - Song [song].f140.mp3']
        new_kept = ['Artist - Song.mp3', 'Artist - Song [song].mp3', 'Artist - Song [song].jpg', 'Artist - Song [song].vtt',
                    'Artist - Song [other].mp4', 'notes [song].txt']
        for name in old:
            (out / name).write_bytes(b'owner')
        runner = Runner(self.emit)
        runner._existing = engine._listing(out)
        for name in new_removed + new_kept:
            (out / name).write_bytes(b'new')
        item = engine.QueueItem(1, self.base + '/song.html', Options(out, formats.by_id('audio_mp3').id))
        item.path = str(out / 'Artist - Song.mp3')
        runner._discard_kept(item, out, [item.path])
        self.assertEqual(media_files(out), sorted(old + new_kept))

    def test_a_new_download_beside_an_older_file_leaves_no_extra_copy(self):
        # The older file has the link's id but another title: the engine downloads afresh; -k kept
        # that download's MP4, which is removed; the older file is untouched.
        link = self.base + '/song.html'
        out = self.root / 'renamed'
        self.run_links([link], Options(out, formats.by_id('video_mp4').id, naming='title_id'))
        first = snapshot(out)
        self.assertEqual(len(first), 1, first)
        older = out / 'Old Title [song].mp4'
        (out / next(iter(first))).rename(older)
        before = snapshot(out)
        work_before = media_files(paths.work_dir())
        self.events = []
        Runner.reset_pacing()
        commands = []
        real = Runner._run_once

        def run_once(runner, command, *args):
            commands.append(list(command))
            return real(runner, command, *args)

        with mock.patch.object(Runner, '_run_once', run_once):
            self.run_links([link], Options(out, formats.by_id('audio_mp3').id, naming='title'))
        self.assertEqual([command.count('-k') for command in commands], [1], 'one run, with -k from the start')
        done = self.done()
        after = snapshot(out)
        self.assertEqual(after.get(older.name), before[older.name], 'the older file is unchanged')
        added = sorted(set(after) - set(before))
        self.assertEqual([Path(name).suffix for name in added], ['.mp3'], after)
        self.assertEqual((done['success'], done['files']), (1, [str(out / added[0])]))
        self.assertEqual(media_files(paths.work_dir()), work_before, 'nothing is left in the work folder')


# --------------------------------------------------------------------------- fix round 1: core-rv-engine-1-2

class ForgedAlreadyLineTests(EngineCase):
    def test_a_reason_phrase_cannot_report_an_owner_file_as_downloaded(self):
        out = self.root / 'out-inject'
        out.mkdir(parents=True)
        victim = out / 'Owner holiday video [abc].mp4'
        victim.write_bytes(b'owner data')
        forged = ('Not Found\r[download] ' + str(victim) + ' has already been downloaded\rB').encode('utf-8')
        base, server = start_site(self.site, inject=forged)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        history = library.Library(self.root / 'history.sqlite3')
        job = history.create_job('download', 'inject', {'urls': [base + '/inject']})

        def emit(kind, value):
            history.download_event(job, kind, value)
            self.emit(kind, value)

        Runner(emit, library=history).download([base + '/inject'], Options(out, MODES[0], batch_safe=False))
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['files']), (0, 1, []))
        self.assertEqual((self.of('media'), self.of('file')), ([], []))
        self.assertEqual(self.of('item_done')[0]['reason'], zh_t('core.reason.page_404'))
        self.assertIsNone(history.find_download([engine.QueueItem(1, base + '/inject', Options(out)).key], 'audio_best'))
        self.assertEqual([entry['reason'] for entry in unfinished.load()], ['failed'])
        self.assertEqual(victim.read_bytes(), b'owner data')
        self.assertFalse(any(str(line).startswith(PRESENT_NOTICES) for line in self.of('log')))

    def test_lines_are_split_as_before_and_text_after_a_bare_cr_is_marked(self):
        import io
        cases = [b'', b'a', b'a\n', b'a\r\n', b'a\r', b'a\rb', b'a\rb\n', b'a\r\r\n', b'\r\n', b'\r', b'\n\n',
                 b'a\r\nb\rc\n', b'x\xe4\xb8\rz\n', b'\xe5\xbe\x90\r\n\xe7\xa7\x89', b'frame=1\rframe=2\r[download] y\r\n']
        for data in cases:
            expected = list(io.TextIOWrapper(io.BytesIO(data), encoding='utf-8', errors='replace'))
            self.assertEqual([line for line, _ in engine._EngineOutput._lines(io.BytesIO(data))], expected, data)
        self.assertEqual([continued for _, continued in engine._EngineOutput._lines(io.BytesIO(b'a\rb\r\nc\n'))],
                         [False, True, False])
        seal = '__GUIabc_'
        data = ('ERROR: x\r[download] C:\\o\\a [b].mp3 has already been downloaded\r\n'
                '[download] C:\\o\\a [b].mp3 has already been downloaded\r\n'
                'frame=1\r' + seal + 'FILE__"C:\\\\o\\\\a.mp3"\r\n').encode('utf-8')
        stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8', errors='replace')
        lines = [line.rstrip('\r\n') for line in engine._EngineOutput(stream, seal)]
        self.assertEqual(lines, ['ERROR: x', ' [download] C:\\o\\a [b].mp3 has already been downloaded',
                                 '[download] C:\\o\\a [b].mp3 has already been downloaded', 'frame=1',
                                 '__GUI_FILE__"C:\\\\o\\\\a.mp3"'])


# --------------------------------------------------------------------------- WP2-q2

class CleanWorkTests(unittest.TestCase):
    def test_old_intermediate_files_are_removed_and_nothing_else(self):
        root = Path(tempfile.mkdtemp(dir=area('clean')))
        folder = paths.own_dir(root / 'data' / 'work')
        now = time.time()
        day = 24 * 3600

        def file(name, age_days, where=folder):
            path = where / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x')
            os.utime(path, (now - age_days * day, now - age_days * day))
            return path

        removed = [file('A [x].f137.mp4', 10), file('A [x].webm', 10), file('B [y].m4a', 8), file('C [z].part', 8),
                   file('C [z].jpg', 2)]
        # final-architecture-1: a resumable partial of two days stays (paths.WORK_MAX_AGE), as in STATE\work.
        kept = [file('D [w].f140.m4a', 2), file('E [v].part', 0.01), file('sub/old.mp4', 30), file('F [u].part', 2),
                file('F [u].ytdl', 2), file('G [t].jpg', 0.5)]
        marker = folder / paths.OWNED
        os.utime(marker, (now - 30 * day, now - 30 * day))
        target = file('outside.mp4', 30, root / 'target')
        junction = folder / 'link'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(root / 'target')],
                              capture_output=True, creationflags=procutil.HIDDEN).returncode == 0
        try:
            self.assertEqual(engine.clean_app_work(folder, now=now), len(removed))
            for path in removed:
                self.assertFalse(path.exists(), path.name)
            for path in kept + [marker, target]:
                self.assertTrue(path.exists(), path.name)
            if made:
                self.assertTrue(junction.exists(), 'a junction is never removed or followed')
        finally:
            if made:
                subprocess.run(['cmd', '/c', 'rmdir', str(junction)], capture_output=True, creationflags=procutil.HIDDEN)
        # A folder without the app's marker is never cleaned.
        foreign = root / 'foreign'
        stray = file('A [x].f137.mp4', 30, foreign)
        self.assertEqual(engine.clean_app_work(foreign, now=now), 0)
        self.assertTrue(stray.exists())

    def test_both_work_folders_follow_one_age_rule(self):
        """final-architecture-1: STATE\\work (paths.purge_stale) and APP_DIR\\data\\work
        (engine.clean_app_work) keep and remove the same files."""
        root = Path(tempfile.mkdtemp(dir=area('clean-both')))
        hour = 3600
        ages = {'Song [abc].f137.mp4': 2 * hour, 'Song [abc].f251.webm.part': 2 * hour, 'Song [abc].webm': 2 * hour,
                'Long [xyz].mp4.part': 48 * hour, 'Long [xyz].mp4.ytdl': 48 * hour, 'Long [xyz].mp4.part-Frag7': 48 * hour,
                'Cover [c].jpg': 30 * hour, 'Young [y].webp': 2 * hour, 'ffmpeg.temp.mp3': 30 * hour,
                'Old [o].mp4.part': 8 * 24 * hour, 'Old [o].f140.m4a': 8 * 24 * hour}
        expected = sorted(['Song [abc].f137.mp4', 'Song [abc].f251.webm.part', 'Song [abc].webm', 'Long [xyz].mp4.part',
                           'Long [xyz].mp4.ytdl', 'Long [xyz].mp4.part-Frag7', 'Young [y].webp'])
        with _sandbox.state_patch(root / 'state' / 'YtDlpDesktop'), mock.patch.object(paths, 'APP_DIR', root / 'app'):
            folders = [paths.own_dir(paths.work_dir()), paths.own_dir(engine.app_work_dir())]
            now = time.time()
            for folder in folders:
                for name, seconds in ages.items():
                    (folder / name).write_bytes(b'x')
                    os.utime(folder / name, (now - seconds, now - seconds))
            paths.purge_stale(procutil.alive)
            engine.clean_app_work(engine.app_work_dir())
            for folder in folders:
                self.assertEqual(media_files(folder), expected, folder)


# --------------------------------------------------------------------------- review-e2e-2

class OldPartialTests(EngineCase):
    def test_a_partial_download_of_1_5_1_in_the_output_folder_is_resumed_there(self):
        out = self.root / 'out'
        out.mkdir(parents=True)
        partial = out / 'fixture [fixture].mp4.part'
        partial.write_bytes(FIXTURE.read_bytes()[:30000])
        self.run_links([self.base + '/fixture.mp4'], Options(out, formats.by_id('audio_mp3').id))
        done = self.done()
        logs = [str(line) for line in self.of('log')]
        self.assertEqual(done['success'], 1)
        self.assertIn(zh_t('core.engine.resume_partial'), logs)
        self.assertTrue(any('Resuming download at byte 30000' in line for line in logs), logs)
        self.assertEqual(media_files(out), ['fixture.mp3'], 'no partial file is left behind')
        self.assertEqual(media_files(paths.work_dir()), [])
        self.assertEqual(unfinished.load(), [])

    def test_which_partials_belong_to_a_link(self):
        listing = {engine._normal(Path('C:/out') / name) for name in (
            'Song [AAAAAAAAAAA].webm.part', 'Song [AAAAAAAAAAA].f137.mp4.ytdl', 'Other [BBBBBBBBBBB].webm.part',
            '徐秉龙 - 双子 (1) [song-1].mp4.part', 'Finished [AAAAAAAAAAA].mp3', 'video [BV1xx411c7mD_p1].mp4.part-Frag3')}
        self.assertEqual([Path(path).name for path in engine._old_partials(listing, 'https://youtu.be/AAAAAAAAAAA?si=x')],
                         ['song [aaaaaaaaaaa].f137.mp4.ytdl', 'song [aaaaaaaaaaa].webm.part'])
        self.assertEqual(len(engine._old_partials(listing, 'http://127.0.0.1:1/slow/song.html')), 1)
        self.assertEqual(len(engine._old_partials(listing, 'https://www.bilibili.com/video/BV1xx411c7mD/')), 1)
        self.assertEqual(engine._old_partials(listing, 'https://www.youtube.com/watch?v=CCCCCCCCCCC'), [])
        self.assertEqual(engine._old_partials(listing, 'https://example.com/'), [])


# --------------------------------------------------------------------------- review-e2e-3

class PageErrorTests(EngineCase):
    def test_reasons(self):
        table = {
            '[generic] song: Unable to download webpage: HTTP Error 404: Not Found (caused by <HTTPError 404>)':
                zh_t('core.reason.page_404'),
            '[generic] page: Unable to download webpage: HTTP Error 403: Forbidden': zh_t('core.reason.page_403'),
            '[generic] page: Unable to download webpage: HTTP Error 410: Gone': zh_t('core.reason.page_410'),
            '[youtube] AAA: Unable to download API JSON: HTTP Error 404: Not Found': zh_t('core.reason.page_404'),
            'unable to download video data: HTTP Error 404: Not Found': zh_t('core.reason.http_404'),
            'unable to download video data: HTTP Error 403: Forbidden': zh_t('core.reason.http_403'),
            '[generic] page: Unable to download webpage: <urlopen error timed out>': zh_t('core.reason.network'),
            '[generic] page: Unable to download webpage: [WinError 10061] No connection could be made':
                zh_t('core.reason.network'),
            'Unable to download webpage: Connection refused': zh_t('core.reason.network'),
        }
        for text, reason in table.items():
            self.assertEqual(engine.explain_error(text), reason, text)

    def test_a_dead_or_forbidden_page_is_not_called_a_network_problem(self):
        links = [self.base + '/status/404', self.base + '/status/403', self.base + '/status/410']
        self.run_links(links, Options(self.root / 'out', MODES[0], batch_safe=False))
        reasons = [value['reason'] for value in self.of('item_done')]
        self.assertEqual(reasons, [zh_t('core.reason.page_404'), zh_t('core.reason.page_403'), zh_t('core.reason.page_410')])
        self.assertEqual(sorted(entry['reason'] for entry in unfinished.load()), ['failed'] * 3)


# --------------------------------------------------------------------------- review-privacy-security-2

class MarkerTests(EngineCase):
    def test_a_reason_phrase_with_carriage_returns_cannot_fake_a_marker(self):
        owner = self.root / 'owner-files'
        owner.mkdir(parents=True)
        victim = owner / 'tax-return [abc].pdf'
        victim.write_bytes(b'%PDF owner data')
        media = json.dumps({'id': 'abc', 'extractor_key': 'Youtube', 'title': 'injected', 'filepath': str(victim)})
        forged = ('A\r__GUI_MEDIA__' + media + '\r__GUI_FILE__' + json.dumps(r'C:\Windows\System32\calc.exe')
                  + '\r__GUI_TITLE__"forged"\rB').encode('latin-1')
        base, server = start_site(self.site, inject=forged)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        history = library.Library(self.root / 'history.sqlite3')
        job = history.create_job('download', 'inject', {'urls': [base + '/inject']})

        def emit(kind, value):
            history.download_event(job, kind, value)
            self.emit(kind, value)

        Runner(emit, library=history).download([base + '/inject'], Options(self.root / 'out', MODES[0], batch_safe=False))
        done = self.done()
        self.assertEqual((self.of('media'), self.of('file'), self.of('title'), done['files']), ([], [], [], []))
        self.assertEqual(sorted(os.listdir(owner)), ['tax-return [abc].pdf'])
        self.assertIsNone(history.find_download(['youtube:abc'], 'audio_best'))
        self.assertEqual(done['failed'], 1)

    def test_only_sealed_lines_of_the_engine_are_markers(self):
        out = self.root / 'out'
        out.mkdir(parents=True)
        real = out / 'real [r1].opus'
        real.write_bytes(b'media')
        script = ('import json, sys\n'
                  'template = sys.argv[sys.argv.index("--print") + 1]\n'
                  'marker = template.split(":", 1)[1].split("%", 1)[0]\n'
                  'out = sys.stdout\n'
                  'out.write(marker + json.dumps(sys.argv[-1]) + "\\n")\n'
                  'out.write("__GUI_FILE__" + json.dumps("C:/forged/one.exe") + "\\n")\n'
                  'out.write("ERROR: 404 A\\r__GUI_FILE__" + json.dumps("C:/forged/two.exe") + "\\rB\\n")\n'
                  'out.flush()\n')
        command = [sys.executable, '-c', script, '--print', 'after_move:__GUI_FILE__%(filepath)j', '--', str(real)]
        runner = Runner(self.emit)
        files = []
        result = runner._run_once(command, 1, 1, files)
        self.assertEqual(result[0], 0)
        self.assertEqual(files, [str(real)])
        self.assertEqual(self.of('file'), [str(real)])
        self.assertTrue(any(str(line).startswith(' __GUI_FILE__') for line in self.of('log')))
        # The seal is in the --print argument only, never in the link, and differs per runner.
        sealed, changed = runner._seal_command(['x', '--print', 'a:__GUI_FILE__%(f)j', '--progress-template',
                                                'download:__GUI_PROGRESS__%(p)s', '--', 'https://e/__GUI_FILE__'])
        self.assertTrue(changed)
        self.assertNotIn('__GUI_FILE__', sealed[2])
        self.assertNotIn('__GUI_PROGRESS__', sealed[4])
        self.assertEqual(sealed[-1], 'https://e/__GUI_FILE__')
        self.assertNotEqual(Runner(self.emit)._seal, runner._seal)
        plain = ['python', '-c', 'print(1)']
        self.assertIs(runner._seal_command(plain)[0], plain)


# --------------------------------------------------------------------------- review-robustness-2

class LeftoverChildTests(EngineCase):
    def test_stop_ends_a_child_that_outlived_the_engine(self):
        script = ('import subprocess, sys\n'
                  'helper = subprocess.Popen([sys.executable, "-c", '
                  '"import os, time; print(\'helper-pid\', os.getpid(), flush=True); time.sleep(600)"])\n'
                  'print("launcher-pid", helper.pid, flush=True)\n'
                  'print("engine exits now", flush=True)\n')
        real = engine.build_command

        def command(url, options):
            real(url, options)
            return [sys.executable, '-c', script, '--', url]

        pids = []
        seen = threading.Event()

        def emit(kind, value):
            self.emit(kind, value)
            if kind == 'log' and isinstance(value, str) and value.split()[:1] in (['helper-pid'], ['launcher-pid']):
                pids.append(int(value.split()[1]))
                if len(pids) == 2:
                    seen.set()

        runner = Runner(emit)
        try:
            with mock.patch.object(engine, 'build_command', command):
                worker = threading.Thread(target=runner.download, args=(['https://example.com/a'], Options(self.root / 'out')),
                                          daemon=True)
                worker.start()
                self.assertTrue(seen.wait(30), 'the stand-in engine did not report its helper')
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    with runner.lock:
                        process = runner.process
                    if process is not None and process.poll() is not None:
                        break
                    time.sleep(0.05)
                time.sleep(0.5)
                self.assertTrue(worker.is_alive(), 'the helper holds the output pipe, so the item is still running')
                runner.cancel()
                worker.join(20)
                self.assertFalse(worker.is_alive(), 'Stop frees the worker')
            self.assertEqual(len(self.of('done')), 1)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and any(procutil.alive(pid) for pid in pids):
                time.sleep(0.1)
            self.assertEqual([pid for pid in pids if procutil.alive(pid)], [], 'nothing the engine started survives')
        finally:
            for pid in pids:
                if procutil.alive(pid):
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                                   creationflags=procutil.HIDDEN, timeout=30)
            runner.cancel()


# --------------------------------------------------------------------------- review-robustness-3

class ArchiveDatabaseErrorTests(EngineCase):
    def test_a_database_error_while_archiving_fails_the_archive_record_only(self):
        store = library.Library(self.root / 'history.sqlite3')
        real_index = library.Library.archive_index

        def archive_index(this, folder, data):
            if 'downloaded' in data:      # the write after the media file arrived
                raise sqlite3.OperationalError('database or disk is full')
            return real_index(this, folder, data)

        script = ('import json, os, sys\n'
                  'folder = sys.argv[1]\n'
                  'os.makedirs(folder, exist_ok=True)\n'
                  'name = "T [" + sys.argv[-1][-1] + "].mp4"\n'
                  'path = os.path.join(folder, name)\n'
                  'open(path, "wb").write(b"media")\n'
                  'print("__GUI_FILE__" + json.dumps(path), flush=True)\n'
                  'print("__GUI_ARCHIVE__" + json.dumps(dict(id=name, title="T", filepath=path)), flush=True)\n')
        real = engine.build_command

        def command(url, options):
            built = real(url, options)
            return [sys.executable, '-c', script, built[built.index('-P') + 1], '--', url]

        options = Options(self.root / 'out', archive_root=str(self.root / '资料库'))
        with mock.patch.object(engine, 'build_command', command), \
                mock.patch.object(library.Library, 'archive_index', archive_index):
            self.run_links(['https://example.com/a', 'https://example.com/b'], options, runner=Runner(self.emit, library=store))
        done = self.done()
        self.assertEqual((done['success'], done['failed'], len(done['archive_errors'])), (2, 0, 2))
        self.assertEqual([value['success'] for value in self.of('item_done')], [True, True])
        self.assertTrue(any(head('core.engine.archive_info_failed', 'error') in str(line) for line in self.of('log')))
        self.assertEqual(unfinished.load(), [])


# --------------------------------------------------------------------------- review-state-1

class StateWorkFolderTests(EngineCase):
    def test_a_state_folder_that_cannot_be_created_is_not_used_as_the_work_folder(self):
        blocker = self.root / 'blocker'
        blocker.write_text('a file where the state folder should be', encoding='utf-8')
        out = _sandbox.out('state-1', 'out')
        with mock.patch.object(paths, 'STATE_DIR', blocker / 'YtDlpDesktop'):
            self.assertEqual(engine.temp_path(Options(out)), engine.app_work_dir())
            with mock.patch.object(paths, 'APP_DIR', blocker / 'app'):
                self.assertIsNone(engine.temp_path(Options(out)))
                command = engine.build_command(self.base + '/fixture.mp4', Options(out))
                self.assertFalse(any(word.startswith('temp:') for word in command))
                self.run_links([self.base + '/fixture.mp4'], Options(out, MODES[0], batch_safe=False))
        done = self.done()
        self.assertEqual((done['success'], done['failed']), (1, 0), self.of('log'))
        self.assertEqual(media_files(out), ['fixture.m4a'])


# --------------------------------------------------------------------------- final-data-safety-2

def streams(path):
    probe = subprocess.run([str(paths.BIN / 'ffprobe.exe'), '-v', 'error', '-show_entries', 'stream=codec_name',
                            '-of', 'csv=p=0', str(path)], capture_output=True, text=True,
                           creationflags=procutil.HIDDEN, env=procutil.process_environment(), timeout=60)
    return probe.stdout.split()


class OwnerPictureTests(EngineCase):
    """A picture of the owner's named '<work name>.jpg' (or .png / .webp) in the output folder is
    never taken as the cover, embedded and deleted, nor overwritten by a converted cover."""

    def work_name(self):
        out = self.root / 'probe-name'
        self.run_links([self.base + '/song.html'], Options(out, formats.by_id('audio_mp3').id, naming='title_id',
                                                           batch_safe=False))
        names = media_files(out)
        self.assertEqual(len(names), 1, names)
        self.events = []
        Runner.reset_pacing()
        with unfinished.LOCK:
            unfinished.save([])
        return Path(names[0]).stem

    def test_an_owner_picture_with_the_work_name_survives(self):
        stem = self.work_name()
        cover = (self.site / 'cover.jpg').read_bytes()
        for style in ('title', 'title_id'):
            with self.subTest(style=style):
                out = self.root / f'owner-{style}'
                out.mkdir(parents=True)
                for extension in ('.jpg', '.png', '.webp'):
                    (out / (stem + extension)).write_bytes(cover + extension.encode())
                before = snapshot(out)
                work_before = media_files(paths.work_dir())
                self.events = []
                Runner.reset_pacing()
                self.run_links([self.base + '/song.html'], Options(out, formats.by_id('audio_mp3').id, naming=style,
                                                                   batch_safe=False))
                done = self.done()
                self.assertEqual((done['success'], done['failed']), (1, 0), self.of('log'))
                after = snapshot(out)
                for name, digest in before.items():
                    self.assertEqual(after.get(name), digest, f'{name} is unchanged')
                added = sorted(set(after) - set(before))
                self.assertEqual([Path(name).suffix for name in added], ['.mp3'], after)
                logs = [str(line) for line in self.of('log')]
                self.assertFalse(any('thumbnail is already present' in line for line in logs), logs)
                self.assertEqual(media_files(paths.work_dir()), work_before, 'no picture is left in the work folder')
                notice = any(line.startswith(PICTURE_EXISTS) for line in logs)
                if style == 'title':
                    # With the work folder the cover is fetched there and still embedded.
                    self.assertFalse(notice, logs)
                    self.assertIn('mjpeg', streams(out / added[0]), 'the cover is embedded')
                else:
                    # Without a work folder this item does without its cover.
                    self.assertTrue(notice, logs)
                    self.assertNotIn('mjpeg', streams(out / added[0]))
                self.assertEqual(unfinished.load(), [])

    def test_a_stopped_item_leaves_no_cover_and_the_retry_embeds_it(self):
        out = self.root / 'stop-cover'
        runner = Runner(self.emit)
        stopped = []

        def emit(kind, value):
            self.emit(kind, value)
            if kind == 'log' and isinstance(value, str) and value.startswith('[download] Destination') and not stopped:
                stopped.append(value)
                runner.cancel()

        runner.emit = emit
        options = Options(out, formats.by_id('audio_mp3').id, naming='title_id', batch_safe=False)
        runner.download([self.base + '/song.html'], options)
        self.assertTrue(stopped, 'the run reached the download')
        self.assertTrue(any(str(line).startswith('[info] Writing video thumbnail') for line in self.of('log')))
        self.assertEqual([name for name in media_files(out) if Path(name).suffix in engine.PICTURE_EXTENSIONS], [],
                         'the cover the engine wrote for the stopped item is removed')
        self.events = []
        Runner.reset_pacing()
        self.run_links([self.base + '/song.html'], options)
        done = self.done()
        self.assertEqual(done['success'], 1)
        logs = [str(line) for line in self.of('log')]
        self.assertFalse(any(line.startswith(PICTURE_EXISTS) for line in logs), logs)
        finished = [name for name in media_files(out) if name.endswith('.mp3')]
        self.assertEqual(len(finished), 1, media_files(out))
        self.assertIn('mjpeg', streams(out / finished[0]))
        self.assertEqual([name for name in media_files(out) if Path(name).suffix in engine.PICTURE_EXTENSIONS], [])

    def test_which_pictures_belong_to_a_link_and_the_thumbnail_path(self):
        listing = {engine._normal(Path('C:/out') / name) for name in (
            'Song [AAAAAAAAAAA].jpg', 'Song [AAAAAAAAAAA].mp3', 'Other [BBBBBBBBBBB].webp', 'Artist - Song (1) [song-1].PNG',
            'holiday.jpg', 'cover [x].gif')}
        names = lambda found: [Path(path).name for path in found]
        self.assertEqual(names(engine._owner_pictures(listing, 'https://youtu.be/AAAAAAAAAAA')), ['song [aaaaaaaaaaa].jpg'])
        self.assertEqual(names(engine._owner_pictures(listing, 'http://127.0.0.1:1/song.html')),
                         ['artist - song (1) [song-1].png'])
        self.assertEqual(engine._owner_pictures(listing, 'https://www.youtube.com/watch?v=CCCCCCCCCCC'), [])
        # A link for several files, or one whose id cannot be guessed: every picture with a work name.
        self.assertEqual(len(engine._owner_pictures(listing, 'https://www.youtube.com/playlist?list=PLx', multi=True)), 3)
        self.assertEqual(len(engine._owner_pictures(listing, 'https://example.com/')), 3)
        # The thumbnail path goes with the work folder and the cover, never alone.
        out = _sandbox.out('pictures', 'out')
        mp3 = formats.by_id('audio_mp3').id
        with_work = engine.build_command(self.base + '/song.html', Options(out, mp3))
        work = engine.temp_path(Options(out, mp3))
        self.assertIsNotNone(work)
        self.assertIn('thumbnail:' + str(work), with_work)
        self.assertEqual(with_work[with_work.index('thumbnail:' + str(work)) - 1], '-P')
        for command in (engine.build_command(self.base + '/song.html', Options(out, mp3), temp=False),
                        engine.build_command(self.base + '/song.html', Options(out, mp3, embed_cover=False)),
                        engine.build_command(self.base + '/song.html', Options(out, mp3, naming='title_id')),
                        engine.build_command(self.base + '/song.html', Options(out, formats.by_id('audio_wav').id))):
            self.assertFalse(any(word.startswith('thumbnail:') for word in command), command)

    def test_a_forged_thumbnail_line_after_a_bare_cr_is_marked(self):
        import io
        data = 'Not Found\r[info] Writing video thumbnail 0 to: C:\\o\\x.jpg\r\n'.encode('utf-8')
        stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8', errors='replace')
        self.assertEqual([line.rstrip('\r\n') for line in engine._EngineOutput(stream, '__GUIabc_')],
                         ['Not Found', ' [info] Writing video thumbnail 0 to: C:\\o\\x.jpg'])


# --------------------------------------------------------------------------- final-privacy-security-2

class LongLinkTests(EngineCase):
    def test_an_over_long_link_fails_alone_and_the_queue_goes_on(self):
        out = self.root / 'long'
        long_link = self.base + '/fixture.mp4?pad=' + 'x' * 33000
        with self.assertRaisesRegex(ValueError, '^' + re.escape(head('core.links.too_long', 'length')) + '33'):
            engine.build_command(long_link, Options(out))
        # A link just inside the limit is still accepted.
        fitting = self.base + '/fixture.mp4?pad=' + 'x' * 20000
        self.assertLessEqual(len(subprocess.list2cmdline(engine.build_command(fitting, Options(out)))),
                             engine.COMMAND_LINE_LIMIT)
        runner = self.run_links([long_link, self.base + '/fixture.mp4'], Options(out, MODES[0], batch_safe=False))
        done = self.done()
        items = self.of('item_done')
        self.assertEqual([value['success'] for value in items], [False, True])
        self.assertFalse(runner.engine_error)
        self.assertFalse(done.get('engine_error'))
        self.assertNotEqual(items[0]['reason'], engine.ENGINE_ERROR_REASON)
        logs = [str(line) for line in self.of('log')]
        too_long = zh_t('core.engine.cannot_start_link', error=head('core.links.too_long', 'length'))
        self.assertTrue(any(line.startswith(too_long) for line in logs), logs)
        self.assertFalse(any(line.startswith(CANNOT_START_ENGINE) for line in logs), logs)
        self.assertEqual(media_files(out), ['fixture.m4a'])
        self.assertEqual([entry['reason'] for entry in unfinished.load()], ['failed'])

    def test_an_engine_start_failure_has_one_full_stop(self):
        def refuse(*_args, **_kwargs):
            raise FileNotFoundError(2, '文件名或扩展名太长。', None, 206)

        out = self.root / 'start-failure'
        with mock.patch.object(Runner, '_run_once', refuse):
            self.run_links([self.base + '/fixture.mp4'], Options(out, MODES[0], batch_safe=False))
        logs = [str(line) for line in self.of('log') if str(line).startswith(CANNOT_START_ENGINE)]
        self.assertEqual(len(logs), 1, logs)
        self.assertNotIn('。。', logs[0])
        self.assertEqual(engine._sentence(OSError('x. ')), 'x')
        self.assertEqual(engine._sentence(OSError('文件名太长。')), '文件名太长')


if __name__ == '__main__':
    unittest.main(verbosity=2)
