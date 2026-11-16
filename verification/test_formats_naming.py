"""formats.py and naming.py (1.6.0 WP2 steps A and C): the preset table, the naming rule
and the rename of a finished file.  No process is started and nothing leaves the temp folder."""
import _sandbox
import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import engine
import formats
import naming

MODES_OF_1_5_1 = ('音频 · 原始最佳', '音频 · WAV 32 位', '音频 · FLAC', '音频 · MP3 320K', '视频 · 最佳画质 MKV')


class FormatsTests(unittest.TestCase):
    def test_the_first_five_modes_are_the_literal_strings_of_1_5_1(self):
        self.assertEqual(formats.MODES[:5], MODES_OF_1_5_1)
        self.assertEqual(formats.LEGACY_MODES, MODES_OF_1_5_1)
        self.assertEqual(engine.MODES, formats.MODES)
        self.assertEqual(engine.MODES[:5], MODES_OF_1_5_1)
        self.assertEqual(formats.MODES[5:], ('音频 · M4A 原生', '视频 · MP4 兼容'))
        self.assertEqual(engine.RESOLUTIONS, ('最佳', '2160p / 4K', '1440p', '1080p', '720p', '480p'))

    def test_table_ids_labels_kinds_and_menu_order(self):
        self.assertEqual([preset.id for preset in formats.PRESETS],
                         ['audio_best', 'audio_wav', 'audio_flac', 'audio_mp3', 'video_mkv', 'audio_m4a', 'video_mp4'])
        self.assertEqual([preset.label for preset in formats.PRESETS],
                         ['原始音频 · 保留源编码', 'WAV · 32 位浮点', 'FLAC · 24 位', 'MP3 · 320 kbps',
                          'MKV · 保留源画质', 'M4A · AAC 原生（免转码）', 'MP4 · 兼容优先（H.264）'])
        self.assertEqual([preset.kind for preset in formats.PRESETS],
                         ['audio', 'audio', 'audio', 'audio', 'video', 'audio', 'video'])
        self.assertEqual([preset.cover for preset in formats.PRESETS], [True, False, True, True, True, True, True])
        self.assertEqual(formats.MENU_ORDER,
                         ('audio_best', 'audio_m4a', 'audio_mp3', 'audio_flac', 'audio_wav', 'video_mp4', 'video_mkv'))
        self.assertEqual(sorted(formats.MENU_ORDER), sorted(preset.id for preset in formats.PRESETS))
        for name in ('id', 'mode', 'label'):
            values = [getattr(preset, name) for preset in formats.PRESETS]
            self.assertEqual(len(set(values)), len(values), name)
        for preset in formats.PRESETS:
            self.assertIsInstance(preset.args, tuple)
            self.assertTrue(all(isinstance(word, str) for word in preset.args))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                preset.mode = 'x'

    def test_lookups(self):
        for preset in formats.PRESETS:
            self.assertIs(formats.by_mode(preset.mode), preset)
            self.assertIs(formats.by_id(preset.id), preset)
            self.assertIs(formats.by_label(preset.label), preset)
        for lookup in (formats.by_mode, formats.by_id, formats.by_label):
            for bad in ('', 'x', None, formats.PRESETS[0].label + ' '):
                with self.assertRaises(ValueError):
                    lookup(bad)
        # A label is not a mode and the other way round.
        with self.assertRaises(ValueError):
            formats.by_mode(formats.PRESETS[0].label)
        with self.assertRaises(ValueError):
            formats.by_label(formats.PRESETS[0].mode)

    def test_format_key_names_the_resolution_of_a_video_preset(self):
        """WP2 fix round 1 (bughunt-9): one video in 720p and in 1080p are two downloads."""
        self.assertEqual(formats.BEST, engine.RESOLUTIONS[0])
        for preset in formats.PRESETS:
            for resolution in engine.RESOLUTIONS:
                key = formats.format_key(preset, resolution)
                if preset.kind == 'audio' or resolution == engine.RESOLUTIONS[0]:
                    self.assertEqual(key, preset.id, (preset.id, resolution))
                else:
                    self.assertEqual(key, f'{preset.id}@{resolution}')
                self.assertIs(formats.by_key(key), preset)
        self.assertEqual(formats.format_key(formats.by_id('video_mp4'), '1080p'), 'video_mp4@1080p')
        self.assertEqual(formats.format_key(formats.by_id('audio_mp3'), '1080p'), 'audio_mp3')
        self.assertEqual(len({formats.format_key(formats.by_id('video_mkv'), r) for r in engine.RESOLUTIONS}),
                         len(engine.RESOLUTIONS))
        with self.assertRaises(ValueError):
            formats.by_key('nothing@1080p')

    def test_format_args(self):
        table = {
            'audio_best': ['-f', 'ba/b', '-x', '--audio-format', 'best'],
            'audio_wav': ['-f', 'ba/b', '-x', '--audio-format', 'wav', '--postprocessor-args',
                          'ExtractAudio+ffmpeg_o:-c:a pcm_f32le'],
            'audio_flac': ['-f', 'ba/b', '-x', '--audio-format', 'flac', '--postprocessor-args',
                           'ExtractAudio+ffmpeg_o:-sample_fmt s32 -bits_per_raw_sample 24'],
            'audio_mp3': ['-f', 'ba/b', '-x', '--audio-format', 'mp3', '--audio-quality', '320K'],
            'video_mkv': ['-f', 'bv*+ba/b', '--merge-output-format', 'mkv', '--remux-video', 'mkv'],
            'audio_m4a': ['-f', 'ba[ext=m4a]/ba[acodec^=mp4a]/ba/b', '-x', '--audio-format', 'm4a'],
            'video_mp4': ['-f', 'bv*+ba/b', '-S', 'vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac',
                          '--merge-output-format', 'mp4', '--remux-video', 'mp4'],
        }
        for preset in formats.PRESETS:
            self.assertEqual(formats.format_args(preset, '最佳'), table[preset.id], preset.id)
            if preset.kind == 'audio':
                self.assertEqual(formats.format_args(preset, '1080p'), table[preset.id], preset.id)
        mkv, mp4 = formats.by_id('video_mkv'), formats.by_id('video_mp4')
        self.assertEqual(formats.format_args(mkv, '1080p')[:2], ['-f', 'bv*[height<=1080]+ba/b[height<=1080]'])
        self.assertEqual(formats.format_args(mkv, '2160p / 4K')[:2], ['-f', 'bv*[height<=2160]+ba/b[height<=2160]'])
        self.assertEqual(formats.format_args(mp4, '720p')[:2], ['-f', 'bv*[height<=720]+ba/b[height<=720]'])
        self.assertEqual(formats.format_args(mp4, '480p')[2:], table['video_mp4'][2:])
        self.assertIsInstance(formats.format_args(mkv, '1080p'), list)
        # The table itself is never changed by a call.
        self.assertEqual(mkv.args[1], 'bv*{L}+ba/b{L}')

    def test_build_command_reads_the_table(self):
        out = Path(tempfile.gettempdir()) / 'out'
        for preset in formats.PRESETS:
            for resolution in engine.RESOLUTIONS:
                command = engine.build_command('https://example.invalid/a', engine.Options(out, preset.mode, resolution))
                words = formats.format_args(preset, resolution)
                start = command.index('-f')
                self.assertEqual(command[start:start + len(words)], words, (preset.id, resolution))
        with self.assertRaisesRegex(ValueError, 'Unsupported output option'):
            engine.build_command('https://example.invalid/a', engine.Options(out, 'x'))
        with self.assertRaisesRegex(ValueError, 'Unsupported output option'):
            engine.build_command('https://example.invalid/a', engine.Options(out, formats.MODES[0], '999p'))


class NamingRuleTests(unittest.TestCase):
    def stem(self, title, style, audio=True, media_id='xrooMGuRhgY'):
        return naming.target_stem(f'{title} [{media_id}]', media_id, style, audio)

    def test_constants(self):
        self.assertEqual(naming.NAMING, ('title', 'clean', 'title_id'))
        self.assertEqual(naming.LABELS, {'title': '标题（推荐）', 'clean': '精简标题', 'title_id': '标题 [视频 ID]'})
        self.assertEqual(naming.OUTPUT_TEMPLATE, '%(title).140B [%(id)s].%(ext)s')
        self.assertIn(naming.OUTPUT_TEMPLATE, engine.build_command('https://example.invalid/a',
                                                                    engine.Options(Path(tempfile.gettempdir()))))

    def test_clean_cuts_the_decorations_of_the_titles_in_the_owners_log(self):
        cut = {
            '徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」': '徐秉龙 - 双子',
            '微醺卡带 - 雨蝶（R&B微醺版）『我向你飞 雨温柔的坠 像你的拥抱把我包围。』『动态歌词MV』#翻唱 #改编#':
                '微醺卡带 - 雨蝶（R&B微醺版）',
            '徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】': '徐秉龍、沈以誠 - 白羊',
        }
        for title, expected in cut.items():
            self.assertEqual(self.stem(title, 'clean'), expected)
            self.assertEqual(self.stem(title, 'title'), title, 'the default style only drops the id')
            self.assertIsNone(self.stem(title, 'title_id'))

    def test_clean_leaves_titles_alone_that_are_not_artist_dash_song_plus_decoration(self):
        unchanged = ['李翊君 雨蝶 歌詞版', 'YOASOBI「アイドル」 Official Music Video',
                     'Taylor Swift - Anti-Hero (Official Music Video)',
                     # the counter-examples of ux-2: the song name is the bracketed part
                     '周杰倫 Jay Chou【晴天 Sunny Day】-Official Music Video', 'Ado「うっせぇわ」']
        for title in unchanged:
            self.assertEqual(self.stem(title, 'clean'), title)
            self.assertEqual(self.stem(title, 'title'), title)

    def test_clean_applies_to_audio_presets_only(self):
        title = 'C# Tutorial - Part 1 [Beginner]'
        self.assertEqual(self.stem(title, 'clean', audio=True), 'C# Tutorial - Part 1')
        self.assertEqual(self.stem(title, 'clean', audio=False), title, 'a video preset with clean behaves like title')
        self.assertEqual(self.stem(title, 'title', audio=False), title)

    def test_a_cut_that_leaves_less_than_two_characters_falls_back_to_the_title(self):
        # "x - " + decoration would leave a result that is too short only in constructed cases.
        with mock.patch.object(naming, 'CLEAN_RULE', naming.re.compile(r'^(.).*$')):
            self.assertEqual(self.stem('徐秉龙 - 双子 【MV】', 'clean'), '徐秉龙 - 双子 【MV】')
        with mock.patch.object(naming, 'CLEAN_RULE', naming.re.compile(r'^(..).*$')):
            self.assertEqual(self.stem('徐秉龙 - 双子 【MV】', 'clean'), '徐秉')

    def test_a_stem_without_the_exact_id_suffix_is_left_alone(self):
        for stem, media_id in (('标题 [abc]', 'abd'), ('标题 [abc] ', 'abc'), ('标题[abc]', 'abc'), ('标题 (abc)', 'abc'),
                               ('标题 [ABC]', 'abc'), ('标题', 'abc'), ('标题 [abc]', ''), ('标题 [abc]', None),
                               (' [abc]', 'abc'), ('CON [abc]', 'abc'), ('nul [abc]', 'abc')):
            for style in naming.NAMING:
                self.assertIsNone(naming.target_stem(stem, media_id, style), (stem, media_id, style))
        self.assertEqual(naming.target_stem('标题  [abc]', 'abc', 'title'), '标题')
        self.assertEqual(naming.target_stem('17 [17]', 17, 'title'), '17', 'a numeric id is compared as text')
        self.assertEqual(naming.target_stem('console [abc]', 'abc', 'title'), 'console')
        # An unknown style behaves like the default one.
        self.assertEqual(naming.target_stem('标题 [abc]', 'abc', 'something'), '标题')

    def test_renames_truth_table(self):
        self.assertTrue(naming.renames('title'))
        self.assertTrue(naming.renames('clean'))
        self.assertFalse(naming.renames('title_id'))
        for style in ('title', 'clean', 'title_id'):
            self.assertFalse(naming.renames(style, playlist=True))
            self.assertFalse(naming.renames(style, archive_root='D:\\资料'))
            self.assertFalse(naming.renames(style, True, 'D:\\资料'))
        self.assertTrue(naming.renames('title', False, ''))
        self.assertTrue(naming.renames('clean', playlist=False, archive_root=''))


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.lines = []

    def work(self, title='徐秉龙 - 双子 【Lyrics MV】', media_id='abc', suffix='.opus', content=b'media'):
        path = self.root / f'{title} [{media_id}]{suffix}'
        path.write_bytes(content)
        return path

    def names(self):
        return sorted(path.name for path in self.root.iterdir())

    def test_title_drops_the_id_and_returns_the_new_path(self):
        path = self.work()
        result = naming.finalize(str(path), 'abc', 'title', log=self.lines.append)
        self.assertIsInstance(result, str)
        self.assertEqual(result, str(self.root / '徐秉龙 - 双子 【Lyrics MV】.opus'))
        self.assertEqual(self.names(), ['徐秉龙 - 双子 【Lyrics MV】.opus'])
        self.assertEqual(Path(result).read_bytes(), b'media')
        self.assertEqual(self.lines, [])

    def test_clean_and_the_video_exception(self):
        self.assertEqual(Path(naming.finalize(self.work(), 'abc', 'clean')).name, '徐秉龙 - 双子.opus')
        video = self.work(suffix='.mkv')
        self.assertEqual(Path(naming.finalize(video, 'abc', 'clean', audio=False)).name, '徐秉龙 - 双子 【Lyrics MV】.mkv')

    def test_title_id_and_a_foreign_suffix_keep_the_file(self):
        path = self.work()
        self.assertEqual(naming.finalize(str(path), 'abc', 'title_id'), str(path))
        self.assertEqual(naming.finalize(str(path), 'other', 'title'), str(path))
        self.assertEqual(naming.finalize(str(path), '', 'title'), str(path))
        self.assertEqual(self.names(), [path.name])

    def test_a_collision_gives_2_then_3_and_never_overwrites(self):
        results = []
        for content in (b'first', b'second', b'third'):
            results.append(naming.finalize(self.work(title='歌', content=content), 'abc', 'title', log=self.lines.append))
        self.assertEqual([Path(result).name for result in results], ['歌.opus', '歌 (2).opus', '歌 (3).opus'])
        self.assertEqual([Path(result).read_bytes() for result in results], [b'first', b'second', b'third'])
        self.assertEqual(self.names(), ['歌 (2).opus', '歌 (3).opus', '歌.opus'])
        self.assertEqual(self.lines, [])
        # A folder of that name is in the way just like a file.
        (self.root / '曲.opus').mkdir()
        self.assertEqual(Path(naming.finalize(self.work(title='曲'), 'abc', 'title')).name, '曲 (2).opus')

    def test_a_file_can_be_fetched_from_another_folder(self):
        work = self.root / 'work'
        work.mkdir()
        waiting = work / '歌 [abc].opus'
        waiting.write_bytes(b'new')
        (self.root / '歌 [abc].opus').write_bytes(b'old, with the work name')
        (self.root / '歌.opus').write_bytes(b'old, with the final name')
        result = naming.finalize(str(waiting), 'abc', 'title', log=self.lines.append, folder=self.root)
        self.assertEqual(result, str(self.root / '歌 (2).opus'))
        self.assertEqual(Path(result).read_bytes(), b'new')
        self.assertEqual(self.names(), ['work', '歌 (2).opus', '歌 [abc].opus', '歌.opus'])
        self.assertEqual((self.root / '歌 [abc].opus').read_bytes(), b'old, with the work name')
        self.assertEqual((self.root / '歌.opus').read_bytes(), b'old, with the final name')
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(self.lines, [])
        # A folder that does not exist: reported, and the file stays where it is.
        waiting.write_bytes(b'again')
        self.assertEqual(naming.finalize(str(waiting), 'abc', 'title', log=self.lines.append, folder=self.root / 'missing'),
                         str(waiting))
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(waiting.is_file())

    def test_a_title_with_dots_keeps_its_extension(self):
        result = naming.finalize(self.work(title='A.B. feat. C', suffix='.m4a'), 'abc', 'title')
        self.assertEqual(Path(result).name, 'A.B. feat. C.m4a')

    def test_a_locked_file_is_reported_not_raised(self):
        path = self.work()
        calls = []

        def locked(source, target):
            calls.append((Path(source).name, Path(target).name))
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process')

        with mock.patch.object(naming.os, 'rename', locked), mock.patch.object(naming.time, 'sleep') as sleep:
            result = naming.finalize(str(path), 'abc', 'title', log=self.lines.append)
        self.assertEqual(result, str(path), 'the file keeps its work name')
        self.assertEqual(calls, [(path.name, '徐秉龙 - 双子 【Lyrics MV】.opus')] * 3, 'three tries on the same name')
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.3,), (0.3,)])
        self.assertEqual(len(self.lines), 1)
        self.assertIn(path.name, self.lines[0])
        self.assertTrue(path.is_file())

    def test_a_lock_that_goes_away_is_survived(self):
        path = self.work()
        real, failures = os.rename, [PermissionError(13, 'locked')]

        def flaky(source, target):
            if failures:
                raise failures.pop()
            return real(source, target)

        with mock.patch.object(naming.os, 'rename', flaky), mock.patch.object(naming.time, 'sleep'):
            result = naming.finalize(str(path), 'abc', 'title', log=self.lines.append)
        self.assertEqual(Path(result).name, '徐秉龙 - 双子 【Lyrics MV】.opus')
        self.assertTrue(Path(result).is_file())
        self.assertEqual(self.lines, [])

    def test_a_real_lock_on_the_file(self):
        path = self.work()
        with open(path, 'rb'), mock.patch.object(naming.time, 'sleep'):   # an open handle without delete sharing
            result = naming.finalize(str(path), 'abc', 'title', log=self.lines.append)
        self.assertEqual(result, str(path))
        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.names(), [path.name])

    def test_other_errors_and_a_missing_file_are_reported_not_raised(self):
        missing = self.root / '没有 [abc].opus'
        self.assertEqual(naming.finalize(str(missing), 'abc', 'title', log=self.lines.append), str(missing))
        self.assertEqual(len(self.lines), 1)
        path = self.work()
        with mock.patch.object(naming.os, 'rename', side_effect=OSError(22, 'Invalid argument')):
            self.assertEqual(naming.finalize(str(path), 'abc', 'title', log=self.lines.append), str(path))
        self.assertEqual(len(self.lines), 2)
        # No log callback, a callback that raises, and a path that is not text at all.
        with mock.patch.object(naming.os, 'rename', side_effect=OSError(22, 'Invalid argument')):
            self.assertEqual(naming.finalize(str(path), 'abc', 'title'), str(path))
            self.assertEqual(naming.finalize(str(path), 'abc', 'title', log=lambda text: 1 / 0), str(path))
        self.assertEqual(naming.finalize(17, 'abc', 'title'), '17')
        self.assertEqual(naming.finalize(None, 'abc', 'title', log=self.lines.append), 'None')

    def test_ninety_nine_copies_are_the_limit(self):
        path = self.work(title='歌')
        with mock.patch.object(naming.os, 'rename', side_effect=FileExistsError(17, 'exists')) as rename:
            result = naming.finalize(str(path), 'abc', 'title', log=self.lines.append)
        self.assertEqual(result, str(path))
        self.assertEqual(rename.call_count, 99)
        self.assertEqual(Path(rename.call_args_list[-1].args[1]).name, '歌 (99).opus')
        self.assertEqual(len(self.lines), 1)


if __name__ == '__main__':
    unittest.main()
