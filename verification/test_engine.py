# quick
import _sandbox
import json
import re
from pathlib import Path
import unittest
import tempfile

from unittest import mock

import engine
import procutil
import updater
import formats
import i18n
import messages
import paths
from messages import Message, msg
from engine import MODES, Options, build_command, parse_line, parse_urls, read_url_file

URL = 'https://example.com/video'
CJK = re.compile(r'[㐀-䶿一-鿿　-〿！-～]')


# (2.0.0 S3 W4) the zh text of a catalog key, whatever the current language (S3-DESIGN 4.3)
from _sandbox import zh_t


def values(command, flag):
    """The value after every occurrence of a flag."""
    return [command[i + 1] for i, word in enumerate(command[:-1]) if word == flag]


def same_drive_folder(name='out'):
    """A folder on the drive of the state folder (never created)."""
    return Path(paths.work_dir().anchor) / 'ytdlp-test-nowhere' / name


def other_drive_folder():
    letter = 'Q:' if paths.work_dir().drive.lower() != 'q:' else 'R:'
    return Path(letter + '\\out')


class EngineTests(unittest.TestCase):
    def test_batch_text_encodings_comments_and_duplicates(self):
        data = '# comment\nhttps://example.com/one\n; ignored\n] ignored\nhttps://example.com/two\nhttps://example.com/one'
        with tempfile.TemporaryDirectory() as directory:
            for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
                path = Path(directory) / 'links.txt'
                path.write_text(data, encoding=encoding)
                self.assertEqual(read_url_file(path), ['https://example.com/one', 'https://example.com/two'])

    def test_multiple_urls_keep_order_and_do_not_execute_shell_syntax(self):
        url = 'https://example.com/watch?v=abc&list=xyz'
        self.assertEqual(parse_urls(f'{url}\n\nhttps://example.org/video\n{url}'),
                         [url, 'https://example.org/video'])
        command = build_command(url, Options(Path('D:/output with spaces')))
        self.assertEqual(command[-2:], ['--', url])
        self.assertIn('--ignore-config', command)
        self.assertIn('--no-plugin-dirs', command)

    def test_invalid_url_is_rejected_with_line_number(self):
        with self.assertRaisesRegex(ValueError, re.escape(zh_t('core.links.bad_line', line=2))):
            parse_urls('https://example.com/video\n--exec cmd.exe')

    def test_audio_and_video_commands(self):
        for mode in MODES[:4]:
            command = build_command('https://example.com/video', Options(Path('D:/test'), mode))
            self.assertIn('-x', command)
            self.assertIn('--no-playlist', command)
            self.assertIn('--playlist-items', command)
        wav = build_command('https://example.com/video', Options(Path('D:/test'), MODES[1]))
        self.assertIn('ExtractAudio+ffmpeg_o:-c:a pcm_f32le', wav)
        video = build_command('https://example.com/video', Options(Path('D:/test'), MODES[4], '1080p', True))
        self.assertIn('bv*[height<=1080]+ba/b[height<=1080]', video)
        self.assertIn('--yes-playlist', video)
        self.assertNotIn('-x', video)

    def test_cookies_file_is_passed_only_when_chosen_and_valid(self):
        url = 'https://www.youtube.com/watch?v=abc'
        self.assertNotIn('--cookies', build_command(url, Options(Path('D:/test'))))
        with tempfile.TemporaryDirectory() as directory:
            cookies = Path(directory) / '我的 cookies.txt'
            cookies.write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=1\n', encoding='utf-8')
            command = build_command(url, Options(Path('D:/test'), cookies=str(cookies)))
            self.assertEqual(command[command.index('--cookies') + 1], str(cookies))
            self.assertEqual(command[-2:], ['--', url])
            missing = str(Path(directory) / 'missing.txt')
            with self.assertRaisesRegex(ValueError, re.escape(zh_t('core.cookies.missing', file=missing))):
                build_command(url, Options(Path('D:/test'), cookies=missing))
            exported_json = Path(directory) / 'cookies.json'
            exported_json.write_text('[{"domain": ".youtube.com"}]', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Netscape'):
                build_command(url, Options(Path('D:/test'), cookies=str(exported_json)))

    def test_progress_with_missing_values_and_estimated_size(self):
        kind, value = parse_line('__GUI_PROGRESS__100|NA|200|50|2|downloading')
        self.assertEqual(kind, 'progress')
        self.assertEqual(value['fraction'], .5)
        self.assertEqual(value['speed'], 50)
        kind, value = parse_line('__GUI_PROGRESS__0|NA|NA|NA|NA|downloading')
        self.assertIsNone(value['fraction'])
        self.assertIsNone(value['eta'])

    def test_titles_and_unicode_paths_are_json_decoded(self):
        title = '测试中文 "title"'
        self.assertEqual(parse_line('__GUI_TITLE__' + json.dumps(title)), ('title', title))
        path = 'D:\\下载文件\\test.wav'
        self.assertEqual(parse_line('__GUI_FILE__' + json.dumps(path)), ('file', path))

    # --- 1.6.0 WP2 step E: build_command

    def test_media_line_is_parsed_and_printed_before_the_file_line(self):
        media = {'id': 'abc', 'extractor_key': 'Youtube', 'title': '测试 "题"', 'uploader': '某人', 'duration': 3,
                 'webpage_url': 'https://www.youtube.com/watch?v=abc', 'filepath': 'D:\\下载\\测试 [abc].opus'}
        self.assertEqual(parse_line('__GUI_MEDIA__' + json.dumps(media, ensure_ascii=False)), ('media', media))
        # Anything that is not a JSON object stays an ordinary output line.
        for line in ('__GUI_MEDIA__', '__GUI_MEDIA__{broken', '__GUI_MEDIA__"text"', '__GUI_MEDIA__[1]', '__GUI_MEDIA__null'):
            self.assertEqual(parse_line(line), ('log', line))
        for options in (Options(same_drive_folder()), Options(same_drive_folder(), MODES[4], playlist=True),
                        Options(same_drive_folder(), archive_root='D:\\资料')):
            command = build_command(URL, options)
            prints = values(command, '--print')
            self.assertEqual(prints[0], 'before_dl:__GUI_TITLE__%(title)j')
            self.assertEqual(prints[1], 'after_move:__GUI_MEDIA__%(.{id,extractor_key,title,uploader,duration,webpage_url,filepath})j')
            self.assertEqual(prints[1], engine.MEDIA_PRINT)
            self.assertEqual(prints[2], 'after_move:__GUI_FILE__%(filepath)j')
            self.assertEqual(len(prints), 4 if options.archive_root else 3)
            if options.archive_root:
                self.assertTrue(prints[3].startswith('after_move:__GUI_ARCHIVE__'))

    def test_temp_path_only_for_files_that_are_renamed_on_the_same_drive(self):
        out = same_drive_folder()
        expected = 'temp:' + str(paths.work_dir())
        for options in (Options(out), Options(out, naming='clean'), Options(out, MODES[4], '1080p'),
                        Options(out, batch_safe=False, proxy='http://127.0.0.1:1')):
            command = build_command(URL, options)
            # final-data-safety-2: the cover's final place is the work folder too.
            self.assertEqual(values(command, '-P'), [str(out), expected, 'thumbnail:' + str(paths.work_dir())])
            self.assertEqual(command.index(expected), command.index(str(out)) + 2, 'directly after -P <output>')
            self.assertEqual(command[command.index(expected) + 1:command.index(expected) + 4],
                             ['-P', 'thumbnail:' + str(paths.work_dir()), '-o'])
            self.assertEqual(engine.temp_path(options), paths.work_dir())
        for options in (Options(out, playlist=True), Options(out, naming='title_id'), Options(out, archive_root='D:\\资料'),
                        Options(out, naming='clean', playlist=True), Options(other_drive_folder()),
                        Options(Path('relative') / 'folder')):
            command = build_command(URL, options)
            self.assertEqual(values(command, '-P'), [str(options.output)], options)
            self.assertFalse(any(word.startswith('temp:') for word in command), options)
            self.assertIsNone(engine.temp_path(options))
        # The drive letter is compared without regard to case, and the folder is read when the command is built.
        swapped = Path(str(out).swapcase()) if str(out)[0].isalpha() else out
        self.assertIn(expected, build_command(URL, Options(swapped)))
        # (A state folder that can be created: review-state-1 refuses one that cannot.)
        elsewhere = Path(tempfile.mkdtemp()) / 'elsewhere'
        with mock.patch.object(paths, 'STATE_DIR', elsewhere):
            self.assertIn('temp:' + str(elsewhere / 'work'), build_command(URL, Options(out)))
        self.assertFalse(paths.work_dir().exists(), 'building a command creates nothing')

    def test_pacing_flags(self):
        out = same_drive_folder()
        single = build_command(URL, Options(out))
        self.assertNotIn('--sleep-interval', single)
        self.assertNotIn('--max-sleep-interval', single)
        self.assertEqual(values(single, '--sleep-requests'), ['1'])
        listed = build_command(URL, Options(out, playlist=True))
        start = listed.index('--sleep-interval')
        self.assertEqual(listed[start:start + 6], ['--sleep-interval', '10', '--max-sleep-interval', '20', '--sleep-requests', '1'])
        self.assertEqual(listed[start + 6:start + 8], ['--yes-playlist', '--lazy-playlist'])
        for options in (Options(out, batch_safe=False), Options(out, playlist=True, batch_safe=False)):
            command = build_command(URL, options)
            for flag in ('--sleep-interval', '--max-sleep-interval', '--sleep-requests', '--sleep-subtitles'):
                self.assertNotIn(flag, command)

    def test_metadata_block(self):
        out = same_drive_folder()
        base = ['--embed-metadata', '--parse-metadata', ':(?P<meta_synopsis>)', '--parse-metadata', ':(?P<meta_description>)']
        artist = ['--parse-metadata', '%(artist|)s<>%(title)s:^<>(?P<artist>.+?)\\s+-\\s+(?P<track>.+)$']
        for preset in formats.PRESETS:
            command = build_command(URL, Options(out, preset.id, embed_cover=False))
            words = formats.format_args(preset, 'best')
            after = command[command.index('-f') + len(words):-2]
            self.assertEqual(after, base + (artist if preset.kind == 'audio' else []), preset.id)
            off = build_command(URL, Options(out, preset.id, embed_metadata=False, embed_cover=False))
            self.assertEqual(off[off.index('-f') + len(words):-2], [], preset.id)
            self.assertNotIn('--embed-metadata', off)
            self.assertNotIn('--parse-metadata', off)

    def test_cover_block(self):
        out = same_drive_folder()
        for preset in formats.PRESETS:
            command = build_command(URL, Options(out, preset.id, embed_metadata=False))
            tail = command[-5:-2]
            if preset.cover:
                self.assertEqual(tail, ['--embed-thumbnail', '--convert-thumbnails', 'jpg'], preset.id)
            else:
                self.assertEqual(preset.id, 'audio_wav')
                self.assertNotIn('--embed-thumbnail', command)
                self.assertNotIn('--convert-thumbnails', command)
            self.assertNotIn('--embed-thumbnail', build_command(URL, Options(out, preset.id, embed_cover=False)))
        both = build_command(URL, Options(out, MODES[0]))
        self.assertLess(both.index('--embed-metadata'), both.index('--embed-thumbnail'))

    def test_subtitle_block(self):
        out = same_drive_folder()
        for preset in formats.PRESETS:
            command = build_command(URL, Options(out, preset.id, subtitles=True))
            if preset.kind == 'video':
                self.assertEqual(command[-7:-2], ['--embed-subs', '--sub-langs', 'zh.*,en.*,-live_chat', '--sleep-subtitles', '1'])
                unsafe = build_command(URL, Options(out, preset.id, subtitles=True, batch_safe=False))
                self.assertEqual(unsafe[-5:-2], ['--embed-subs', '--sub-langs', 'zh.*,en.*,-live_chat'])
                self.assertNotIn('--sleep-subtitles', unsafe)
            else:
                for flag in ('--embed-subs', '--sub-langs', '--sleep-subtitles'):
                    self.assertNotIn(flag, command, preset.id)
            for flag in ('--embed-subs', '--sub-langs', '--sleep-subtitles', '--write-subs', '--write-auto-subs'):
                self.assertNotIn(flag, build_command(URL, Options(out, preset.id)), preset.id)

    def test_block_order_of_a_full_command(self):
        out = same_drive_folder()
        with tempfile.TemporaryDirectory() as directory:
            cookies = Path(directory) / 'cookies.txt'
            cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
            command = build_command(URL, Options(out, MODES[6], '720p', proxy='http://127.0.0.1:1', cookies=str(cookies),
                                                 referers={URL: 'https://example.org/page'}, subtitles=True))
        order = ['-P', '-o', '--progress-template', '--print', '--extractor-retries', '--retry-sleep', '--sleep-requests',
                 '--no-playlist', '--proxy', '--cookies', '--referer', '-f', '-S', '--merge-output-format', '--remux-video',
                 '--embed-metadata', '--parse-metadata', '--embed-thumbnail', '--convert-thumbnails', '--embed-subs',
                 '--sub-langs', '--sleep-subtitles', '--']
        positions = [command.index(flag) for flag in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(command[-2:], ['--', URL])
        self.assertEqual(command[0], str(updater.engine_path()))

    # --- 1.6.0 WP2 step F: what the Runner reads from the engine's output

    def test_explain_error_table(self):
        table = [
            (dict(text='[youtube] abc: Sign in to confirm you’re not a bot.', bot_check=True),
             zh_t('core.reason.bot_check')),
            (dict(text='', bot_check=True), zh_t('core.reason.bot_check')),
            (dict(text='[youtube] abc: Video unavailable'), zh_t('core.reason.unavailable')),
            (dict(text='[youtube] abc: Private video. Sign in if you\'ve been granted access'), zh_t('core.reason.unavailable')),
            (dict(text='[youtube] abc: This video has been removed by the uploader'), zh_t('core.reason.unavailable')),
            (dict(text='[youtube] abc: Video unavailable. The uploader has not made this video available in your country'),
             zh_t('core.reason.region')),
            (dict(text='[x] 1: This video is not available in your country'), zh_t('core.reason.region')),
            (dict(text='[x] 1: This video is geo-restricted'), zh_t('core.reason.region')),
            (dict(text='[x] 1: content is georestricted'), zh_t('core.reason.region')),
            (dict(text='[generic] Unable to download webpage: <urlopen error timed out>'), zh_t('core.reason.network')),
            (dict(text='<urlopen error [Errno 11001] getaddrinfo failed>'), zh_t('core.reason.network')),
            (dict(text='[youtube] abc: Unable to download webpage: HTTP Error 500'), zh_t('core.reason.network')),
            (dict(text='Connection reset by peer'), zh_t('core.reason.network')),
            (dict(text='[WinError 10061] Connection refused'), zh_t('core.reason.network')),
            (dict(text='[WinError 10060] A connection attempt failed'), zh_t('core.reason.network')),
            (dict(text='unable to download video data: HTTP Error 403: Forbidden'), zh_t('core.reason.http_403')),
            (dict(text='[generic] abc: HTTP Error 404: Not Found'), zh_t('core.reason.http_404')),
            (dict(text='[youtube] abc: Requested format is not available. Use --list-formats'), zh_t('core.reason.format')),
            (dict(text='Unsupported URL: https://example.org/'), zh_t('core.reason.unsupported')),
            (dict(text='[youtube] abc: Some new failure', stale=True), zh_t('core.reason.stale')),
            (dict(text='', stale=True), zh_t('core.reason.stale')),
            # A known reason wins over the stale flag; the bot check wins over everything.
            (dict(text='[youtube] abc: Video unavailable', stale=True), zh_t('core.reason.unavailable')),
            (dict(text='[youtube] abc: Video unavailable', bot_check=True, stale=True),
             zh_t('core.reason.bot_check')),
            (dict(text='[youtube] abc: Something nobody has a rule for'), 'Something nobody has a rule for'),
            (dict(text='plain text without a prefix'), 'plain text without a prefix'),
            (dict(text=zh_t('core.proxy.scheme')), zh_t('core.proxy.scheme')),
            (dict(text=''), zh_t('core.reason.exit_code', code=1)),
            (dict(text='', code=2), zh_t('core.reason.exit_code', code=2)),
            (dict(text=None, code=-1), zh_t('core.reason.exit_code', code=-1)),
            (dict(text='   '), zh_t('core.reason.exit_code', code=1)),
        ]
        for arguments, expected in table:
            self.assertEqual(engine.explain_error(**arguments), expected, arguments)
        long = engine.explain_error('[youtube] abc: ' + 'x' * 300)
        self.assertEqual(long, 'x' * 70)
        self.assertEqual(engine.explain_error('[a] b: [c] d: rest'), '[c] d: rest', 'only one leading part is removed')

    def test_probe_args_cover_every_option_block(self):
        arguments = engine.probe_args()
        for flag in ('--sleep-interval', '--embed-subs', '--embed-thumbnail', '--audio-quality', '--postprocessor-args',
                     '--proxy', '--referer', '--yes-playlist', '--embed-metadata', '--parse-metadata', '--sleep-subtitles',
                     '--sleep-requests', '--merge-output-format', '--remux-video', '-S', '-x', '--print', '--ignore-config'):
            self.assertIn(flag, arguments)
        self.assertNotIn('--', arguments)
        self.assertNotIn('--cookies', arguments, 'no cookies file is needed for the probe')
        self.assertFalse(any('example.invalid/probe' == word for word in arguments))
        self.assertNotIn(str(updater.engine_path()), arguments)
        for preset in formats.PRESETS:
            words = formats.format_args(preset, 'best')
            self.assertTrue(any(arguments[i:i + len(words)] == words for i in range(len(arguments))), preset.id)
        self.assertEqual(arguments, engine.probe_args(), 'the same list every time')

    def test_a_subtitle_429_is_not_a_rate_limit_of_the_queue(self):
        limited = ['ERROR: HTTP Error 429: Too Many Requests', 'WARNING: [youtube] abc: Too Many Requests',
                   'ERROR: unable to download video data: HTTP Error 429: Too Many Requests', 'HTTP status 429',
                   '[download] Got error: HTTP Error 429: Too Many Requests']
        for line in limited:
            self.assertTrue(engine.RATE_LIMIT_PATTERN.search(line), line)
        subtitle = ["ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests",
                    "WARNING: Unable to download video subtitles for 'zh-Hans': HTTP Error 429: Too Many Requests",
                    'ERROR: [youtube] abc: Unable to download video subtitles: Too Many Requests',
                    'ERROR: HTTP Error 429 (unable to download video subtitles)']
        for line in subtitle:
            self.assertIsNone(engine.RATE_LIMIT_PATTERN.search(line), line)
        for line in ('ERROR: HTTP Error 403: Forbidden', 'Some title includes number 429', '[download] 42.9% of 3MiB'):
            self.assertIsNone(engine.RATE_LIMIT_PATTERN.search(line), line)

    def test_note_line_flags(self):
        runner = engine.Runner(lambda kind, value: None)
        self.assertEqual((runner.last_error, runner.stale, runner.engine_error, runner.subs_failed), ('', False, False, False))
        runner._note_line('[youtube] Extracting URL')
        runner._note_line('WARNING: something harmless')
        self.assertEqual((runner.last_error, runner.stale, runner.engine_error, runner.subs_failed), ('', False, False, False))
        runner._note_line('ERROR:   [youtube] abc: Video unavailable  ')
        self.assertEqual(runner.last_error, '[youtube] abc: Video unavailable')
        runner._note_line('ERROR: ' + 'y' * 500)
        self.assertEqual(runner.last_error, 'y' * 200)
        for line in ('WARNING: [youtube] abc: Signature solving failed: Some formats may be missing',
                     'WARNING: [youtube] abc: n challenge solving failed: Some formats may be missing',
                     'ERROR: [youtube] abc: Failed to extract any player response; please report this issue. '
                     'Confirm you are on the latest version using  yt-dlp -U',
                     'WARNING: Your yt-dlp version (2025.01.01) is older than 90 days!'):
            fresh = engine.Runner(lambda kind, value: None)
            fresh._note_line(line)
            self.assertTrue(fresh.stale, line)
            self.assertFalse(fresh.engine_error or fresh.subs_failed, line)
        fresh = engine.Runner(lambda kind, value: None)
        fresh._note_line('yt-dlp.exe: error: no such option: --embed-everything')
        self.assertEqual((fresh.engine_error, fresh.stale, fresh.subs_failed, fresh.last_error), (True, False, False, ''))
        fresh = engine.Runner(lambda kind, value: None)
        fresh._note_line("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
        self.assertEqual((fresh.subs_failed, fresh.engine_error, fresh.stale), (True, False, False))
        self.assertTrue(fresh.last_error.startswith('Unable to download video subtitles'))

    def test_wait_events_say_what_kind_of_wait_it_is(self):
        events = []
        runner = engine.Runner(lambda kind, value: events.append((kind, value)))
        self.assertTrue(runner._wait(0.02, 1, 2, '限流'))
        runner.wait_kind = 'interval'
        self.assertTrue(runner._wait(0.02, 2, 2, engine.msg('core.wait.pace')))    # (1.8.0) the reason download() passes
        waits = [value for kind, value in events if kind == 'waiting']
        self.assertEqual([value['kind'] for value in waits[:1] + waits[-1:]], ['rate_limit', 'interval'])
        self.assertEqual(set(waits[0]), {'index', 'total', 'seconds', 'reason', 'kind'})
        self.assertEqual([value for kind, value in events if kind == 'resuming'],
                         [{'index': 1, 'total': 2}, {'index': 2, 'total': 2}])

    def test_wording_constants(self):
        self.assertEqual(engine.BOT_CHECK_HINT, zh_t('core.engine.bot_check_hint'))
        self.assertEqual(engine.BOT_CHECK_STALE_HINT, zh_t('core.engine.bot_check_stale'))
        self.assertEqual(engine.PACE_SECONDS, (10, 20))


def make_junction(link, target) -> bool:
    """A directory junction (no privilege needed); False when this system cannot make one."""
    import subprocess
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True,
                            stdin=subprocess.DEVNULL, creationflags=procutil.HIDDEN)
    return result.returncode == 0 and Path(link).is_dir()


def age(path, hours):
    stamp = __import__('time').time() - hours * 3600
    __import__('os').utime(path, (stamp, stamp))


class FixRoundOneTests(unittest.TestCase):
    """WP2 fix round 1: OS error texts (bughunt-6) and the work folder beside the program (bughunt-8)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_explain_error_keeps_the_text_of_an_os_error(self):
        for text in ("[WinError 183] 当文件已存在时，无法创建该文件。: 'C:\\\\Users\\\\x'", "[WinError 5] 拒绝访问。: 'D:\\\\Music\\\\下载'",
                     "[Errno 2] No such file or directory: 'x'", "[Errno 13] Permission denied: 'y'", '[1] a: b'):
            self.assertEqual(engine.explain_error(text), text[:70], text)
        self.assertEqual(engine.explain_error('[netease:song] 186016: Some failure'), 'Some failure')
        self.assertEqual(engine.explain_error('[youtube:tab] PLx: Other failure'), 'Other failure')

    def other_state(self):
        """The state folder on another drive than the test folders."""
        letter = 'Q:' if self.root.drive.lower() != 'q:' else 'R:'
        return mock.patch.object(paths, 'work_dir', return_value=Path(letter + '\\state\\work'))

    def test_temp_path_beside_the_program_for_an_output_on_another_drive(self):
        app, out = self.root / 'app', self.root / 'out'
        work = app / 'data' / 'work'
        with self.other_state(), mock.patch.object(paths, 'APP_DIR', app):
            self.assertEqual(engine.app_work_dir(), work)
            self.assertEqual(engine.temp_path(Options(out)), work)
            command = build_command(URL, Options(out))
            self.assertEqual(values(command, '-P'), [str(out), 'temp:' + str(work), 'thumbnail:' + str(work)])
            self.assertFalse(app.exists(), 'deciding creates nothing (the folder is made when a download starts)')
            for options in (Options(out, playlist=True), Options(out, naming='title_id'), Options(out, archive_root=str(self.root))):
                self.assertIsNone(engine.temp_path(options))
            # A folder that cannot be written is not used: no -P temp: at all.
            with mock.patch.object(engine.os, 'open', side_effect=PermissionError(13, 'Access is denied')):
                self.assertIsNone(engine.temp_path(Options(out)))
                self.assertFalse(any(word.startswith('temp:') for word in build_command(URL, Options(out))))
            app.mkdir()
            (app / 'data').write_text('a file where the folder would be', encoding='utf-8')
            self.assertIsNone(engine.temp_path(Options(out)))
            (app / 'data').unlink()
            # A link is never used as the work folder.
            (app / 'data').mkdir()
            elsewhere = self.root / 'elsewhere'
            elsewhere.mkdir()
            if make_junction(work, elsewhere):
                self.assertIsNone(engine.temp_path(Options(out)))
                os_module = __import__('os')
                os_module.rmdir(work)
            self.assertEqual(engine.temp_path(Options(out)), work)
        # The program on another drive than the output folder: no temp path.
        letter = 'R:' if self.root.drive.lower() != 'r:' else 'S:'
        with self.other_state(), mock.patch.object(paths, 'APP_DIR', Path(letter + '\\app')):
            self.assertIsNone(engine.temp_path(Options(out)))
        # The state folder on the output's drive still wins.
        with mock.patch.object(paths, 'APP_DIR', app):
            self.assertEqual(engine.temp_path(Options(same_drive_folder())), paths.work_dir())

    def test_cleaning_the_work_folder_beside_the_program(self):
        work = self.root / 'work'
        work.mkdir()
        # final-architecture-1: one age rule with STATE\work (paths.clean_work).  Strays (ffmpeg's
        # temporaries, covers) go after a day; a resumable partial, a format part and any other
        # file stay 7 days, so a download stopped two days ago still resumes.
        old = ['a [x].jpg', 'a [x].webp', 'a [x].PNG', 'a [x].temp.mp3', 'b.temp', 'c.tmp',
               'o [x].mp4.part', 'o [x].mp4.ytdl', 'o [x].f137.mp4.part-Frag3', 'o [x].f137.mp4']
        kept_old = ['finished [x].mp3', 'notes.txt', 'a [x].mp4.part', 'a [x].f137.mp4.part-Frag3', 'a [x].mp4.ytdl',
                    'a [x].f137.mp4']
        for name in old + kept_old + ['young [y].mp4.part', 'young [y].jpg']:
            (work / name).write_bytes(b'x')
        for name in old[:6] + kept_old:
            age(work / name, 25)
        for name in kept_old[2:]:
            age(work / name, 48)
        for name in old[6:]:
            age(work / name, 8 * 24)
        (work / 'sub').mkdir()
        (work / 'sub' / 'deep.part').write_bytes(b'x')
        age(work / 'sub' / 'deep.part', 48)
        self.assertEqual(engine.clean_app_work(work), 0, 'a folder the app did not create is never cleaned')
        (work / paths.OWNED).touch()
        age(work / paths.OWNED, 48)
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'theirs.part').write_bytes(b'x')
        age(outside / 'theirs.part', 48)
        linked = make_junction(work / 'link', outside)
        self.assertEqual(engine.clean_app_work(work), len(old))
        remaining = sorted(path.name for path in work.iterdir())
        self.assertEqual(remaining, sorted(kept_old + ['young [y].mp4.part', 'young [y].jpg', 'sub', paths.OWNED]
                                           + (['link'] if linked else [])))
        self.assertTrue((work / 'sub' / 'deep.part').is_file(), 'no recursion')
        self.assertTrue((outside / 'theirs.part').is_file(), 'no link is followed')
        self.assertTrue(work.is_dir() and self.root.is_dir(), 'the folder and its parent stay')
        if linked:
            # The folder itself being a link: nothing at all.
            self.assertEqual(engine.clean_app_work(work / 'link'), 0)
            self.assertTrue((outside / 'theirs.part').is_file())
            __import__('os').rmdir(work / 'link')
        self.assertEqual(engine.clean_app_work(self.root / 'missing'), 0)


class SentenceTests(unittest.TestCase):
    """(moved from test_english_replay in 2.0.0 S2 P1; MESSAGES.md 15 A11.5) engine._sentence keeps
    the Message an OSError carries: its key, the 1.7.0 zh text, the whole English text."""

    def test_an_os_error_with_a_message(self):
        error = OSError(msg('core.unfinished.cannot_set_aside', path='x.json'))
        shown = engine._sentence(error)
        line = msg('core.engine.cannot_start_engine', error=shown)
        self.assertIsInstance(shown, Message)
        self.assertEqual(shown.key, 'core.unfinished.cannot_set_aside')
        zh = messages.zh(msg('core.unfinished.cannot_set_aside', path='x.json'))
        self.assertEqual(messages.zh(shown), zh.rstrip().rstrip('。.．').rstrip(), 'the 1.7.0 zh text')
        with _sandbox.language('en'):
            self.assertEqual(messages.show(shown), i18n.t('core.unfinished.cannot_set_aside', path='x.json'))
            self.assertIsNone(CJK.search(messages.show(line)))
        plain = OSError(2, '系统找不到指定的文件。')
        self.assertEqual(engine._sentence(plain), str(plain).rstrip().rstrip('。.．').rstrip())
        self.assertIs(type(engine._sentence(plain)), str)


class ExplainErrorTests(unittest.TestCase):
    """(moved from test_english_replay in 2.0.0 S2 P1; final review 1, ZH-1) explain_error's fallback
    for a Message: the zh text is the 1.7.0 one (stripped, the extractor prefix removed, 70
    characters), the key is kept (unless the engine is stale) and English shows the message whole.
    The same text as plain or raw text gives the same zh text.  (2.0.0 S3 W4) The texts are the
    catalog's zh texts (zh_t), so a reworded catalog text changes them too; the expected results are the
    1.7.0 rule applied to them (computed by the 1.7.0 explain_error of the release sources for the
    1.7.0 texts): trailing spaces stripped, the one leading "[extractor] id: " part removed, the
    stale-engine reason when the engine is stale."""

    STALE = zh_t('core.reason.stale')
    PREFIX = '[youtube] abcdefghijk: '
    FAILED = 'Something failed here'
    # (zh text, the message that has it, explain_error(text, code, False, stale) for
    #  (1, False), (2, False), (1, True))
    CASES = (
        (zh_t('core.archive.unwritable', error='disk I/O error  '), ('core.archive.unwritable', {'error': 'disk I/O error  '}),
         (zh_t('core.archive.unwritable', error='disk I/O error'), zh_t('core.archive.unwritable', error='disk I/O error'),
          STALE)),
        (zh_t('core.engine.waiting', reason=PREFIX + FAILED, seconds='3'),
         ('core.engine.waiting', {'reason': PREFIX + FAILED, 'seconds': '3'}),
         (zh_t('core.engine.waiting', reason=FAILED, seconds='3'), zh_t('core.engine.waiting', reason=FAILED, seconds='3'),
          STALE)),
        (zh_t('core.engine.waiting', reason='  ' + PREFIX + FAILED, seconds='3'),
         ('core.engine.waiting', {'reason': '  ' + PREFIX + FAILED, 'seconds': '3'}),
         (zh_t('core.engine.waiting', reason=FAILED, seconds='3'), zh_t('core.engine.waiting', reason=FAILED, seconds='3'),
          STALE)),
        (zh_t('core.update.start_timeout', timeout=PREFIX + FAILED),
         ('core.update.start_timeout', {'timeout': PREFIX + FAILED}),
         (zh_t('core.update.start_timeout', timeout=FAILED), zh_t('core.update.start_timeout', timeout=FAILED), STALE)),
        (zh_t('core.update.start_timeout', timeout='  ' + PREFIX + FAILED),
         ('core.update.start_timeout', {'timeout': '  ' + PREFIX + FAILED}),
         (zh_t('core.update.start_timeout', timeout=FAILED), zh_t('core.update.start_timeout', timeout=FAILED), STALE)),
        (zh_t('value.job_title.multi', title=PREFIX + FAILED, count='3'),
         ('value.job_title.multi', {'title': PREFIX + FAILED, 'count': '3'}),
         (zh_t('value.job_title.multi', title=FAILED, count='3'), zh_t('value.job_title.multi', title=FAILED, count='3'),
          STALE)),
        (zh_t('value.job_title.multi', title='  ' + PREFIX + FAILED, count='3'),
         ('value.job_title.multi', {'title': '  ' + PREFIX + FAILED, 'count': '3'}),
         (zh_t('value.job_title.multi', title=FAILED, count='3'), zh_t('value.job_title.multi', title=FAILED, count='3'),
          STALE)),
        (zh_t('core.archive.unwritable', error='disk I/O error'), ('core.archive.unwritable', {'error': 'disk I/O error'}),
         (zh_t('core.archive.unwritable', error='disk I/O error'), zh_t('core.archive.unwritable', error='disk I/O error'),
          STALE)),
        (PREFIX + FAILED, None, (FAILED, FAILED, STALE)),
    )
    FLAGS = ((1, False), (2, False), (1, True))

    def test_messages_keep_their_key_and_the_1_7_0_zh_text(self):
        for text, made, old in self.CASES:
            values = [text, messages.raw(text)]
            if made is not None:
                message = msg(made[0], **made[1])
                self.assertEqual(messages.zh(message), text, 'the message has this very zh text')
                values.append(message)
            for value in values:
                for (code, stale), expected in zip(self.FLAGS, old, strict=True):
                    with self.subTest(text=text, value=type(value).__name__, code=code, stale=stale):
                        reason = engine.explain_error(value, code, False, stale)
                        self.assertEqual(messages.zh(reason), expected)
                        if isinstance(value, Message) and not stale:
                            self.assertIsInstance(reason, Message)
                            self.assertEqual(reason.key, value.key)
                            with _sandbox.language('en'):
                                self.assertEqual(messages.show(reason), messages.show(value), 'en shows it whole')


if __name__ == '__main__':
    unittest.main()
