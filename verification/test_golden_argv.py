"""Golden argv matrix of engine.build_command.

Two goldens:
* argv-1.6.0.json (1.6.0 WP2): the matrix below on the current engine.  Any changed,
  added, removed or reordered yt-dlp argument fails this test with a unified diff.  To
  record on purpose, run this file directly (not through run_tests.py) with GOLDEN_UPDATE=1.
* argv-1.5.1.json (WP0, recorded from the 1.5.1 sources, never rewritten): every case of
  it exists in the new matrix under the same name, built with the options 1.5.1 had
  (embed_metadata=False, embed_cover=False).  A second test derives the 1.6.0 command of
  each such case from the 1.5.1 command by the three permitted changes and requires
  equality, so nothing else can have changed for the five old formats.

Case names: mode<N>.<variant>, N = position in engine.MODES.  The variants that also
exist in argv-1.5.1.json are listed in LEGACY_VARIANTS; the others use 1.6.0 options.
Normalisation tokens: <ENGINE> engine exe, <BIN> vendor\\bin, <OUT> output folder,
<COOKIES> cookies file, <ARCHIVE> archive root, <STATE> state folder, <OTHER> an output
folder on another drive than the state folder.
"""
import _sandbox
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import engine
import formats

GOLDEN_NAME = 'argv-1.6.0.json'
LEGACY_GOLDEN = 'argv-1.5.1.json'
URL = 'https://example.com/watch?v=abc&list=xyz'
PROXY = 'http://127.0.0.1:7890'
REFERER = 'https://example.org/page'
OTHER_URL = 'https://example.org/other'  # another link of the same queue
OTHER_REFERER = 'https://example.net/elsewhere'
LEGACY_MODE_COUNT = 5  # the five formats of 1.5.1, engine.MODES[0..4]
LEGACY_VARIANTS = ('default', 'unsafe', 'playlist', 'proxy', 'cookies', 'referer', 'referer_other_url',
                   'referer_among_others', 'archive', 'combined', '1080p', '2160p')
# Variants of the old matrix in which the finished file is not renamed: no temp path (SPEC 3.6).
NOT_RENAMED = ('playlist', 'archive', 'combined')
MEDIA_PRINT = 'after_move:__GUI_MEDIA__%(.{id,extractor_key,title,uploader,duration,webpage_url,filepath})j'
FILE_PRINT = 'after_move:__GUI_FILE__%(filepath)j'
INHERITED_HIDDEN = ('--no-color', '--referer')


def matrix(out, cookies, archive, other):
    """{case name: Options}."""
    cases = {}
    for index, mode in enumerate(engine.MODES):
        video = formats.by_mode(mode).kind == 'video'

        def old(**changes):
            # The option set of 1.5.1: no tags, no cover.
            return engine.Options(out, mode, embed_metadata=False, embed_cover=False, **changes)

        variants = {
            'default': old(),
            'unsafe': old(batch_safe=False),
            'playlist': old(playlist=True),
            'proxy': old(proxy=PROXY),
            'cookies': old(cookies=str(cookies)),
            'referer': old(referers={URL: REFERER}),
            # A referer belongs to its own link (mixed queues: collected links have one,
            # pasted links do not): another link's referer is never used ...
            'referer_other_url': old(referers={OTHER_URL: OTHER_REFERER}),
            # ... and among several entries the link's own one is taken.
            'referer_among_others': old(referers={OTHER_URL: OTHER_REFERER, URL: REFERER}),
            'archive': old(archive_root=str(archive)),
            # Every optional block of 1.5.1 at once pins the order of the blocks among themselves.
            'combined': old(playlist=True, proxy=PROXY, cookies=str(cookies), referers={URL: REFERER},
                            archive_root=str(archive)),
            # --- 1.6.0 options
            'tags': engine.Options(out, mode),                       # the defaults: tags and cover
            'tags_only': engine.Options(out, mode, embed_cover=False),
            'cover_only': engine.Options(out, mode, embed_metadata=False),
            'playlist_unsafe': old(playlist=True, batch_safe=False),
            'subtitles': old(subtitles=True),                        # flags for video presets only
            'title_id': old(naming='title_id'),                      # the file keeps its name: no temp path
            # Every optional block of 1.6.0 at once.
            'everything': engine.Options(out, mode, playlist=True, proxy=PROXY, cookies=str(cookies),
                                         referers={URL: REFERER}, archive_root=str(archive), subtitles=True),
        }
        if video:
            variants['1080p'] = engine.Options(out, mode, '1080p', embed_metadata=False, embed_cover=False)
            variants['2160p'] = engine.Options(out, mode, '2160p / 4K', embed_metadata=False, embed_cover=False)
            variants['subtitles_unsafe'] = old(subtitles=True, batch_safe=False)
            variants['subtitles_tags'] = engine.Options(out, mode, subtitles=True)
        if index == 0:
            variants['clean'] = old(naming='clean')                  # naming does not change the command ...
            variants['other_drive'] = engine.Options(other, mode, embed_metadata=False, embed_cover=False)   # ... the drive does
        for name, options in variants.items():
            cases[f'mode{index}.{name}'] = options
    return cases


class GoldenArgvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()

    def commands(self):
        """The normalised {case: argv} of the matrix."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, state, archive = root / 'out', root / 'state', root / 'archive'
            cookies = root / 'cookies.txt'
            cookies.write_text('# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tname\tvalue\n',
                               encoding='utf-8')
            other = Path(('Q:' if state.resolve().drive.lower() != 'q:' else 'R:') + '\\elsewhere')
            with _sandbox.state_patch(state):
                replacements = [(str(engine.engine_path()), '<ENGINE>'), (str(engine.BIN), '<BIN>'), (str(other), '<OTHER>')]
                for path, token in ((out, '<OUT>'), (cookies, '<COOKIES>'), (archive, '<ARCHIVE>'), (state, '<STATE>')):
                    replacements += [(form, token) for form in _sandbox.path_forms(path)]
                cases = {name: engine.build_command(URL, options) for name, options in matrix(out, cookies, archive, other).items()}
            self.assertFalse(state.exists(), 'building commands creates nothing in the state folder')
        for name, command in cases.items():
            self.assertTrue(all(isinstance(word, str) for word in command), name)
            self.assertEqual(command[-2:], ['--', URL], name)
            self.assertNotIn(OTHER_REFERER, command, name)
        return _sandbox.normalise(cases, replacements)

    def test_build_command_matrix_matches_golden(self):
        _sandbox.check_golden(self, GOLDEN_NAME, self.commands())

    def test_old_formats_differ_from_1_5_1_only_in_the_three_permitted_ways(self):
        """Media print added before the file print; -P temp: after -P <OUT> where the file is
        renamed; no --sleep-interval / --max-sleep-interval without the playlist option."""
        legacy = json.loads((_sandbox.GOLDEN / LEGACY_GOLDEN).read_text(encoding='utf-8'))
        current = self.commands()
        self.assertEqual(len(legacy), 52)
        for name, old in legacy.items():
            index, variant = name.split('.', 1)
            self.assertLess(int(index[4:]), LEGACY_MODE_COUNT, name)
            self.assertIn(variant, LEGACY_VARIANTS, name)
            self.assertIn(name, current, f'{name} of argv-1.5.1.json is missing in the new matrix')
            expected = list(old)
            # 1. the media print, directly before the file print
            self.assertEqual(expected.count(FILE_PRINT), 1, name)
            position = expected.index(FILE_PRINT)
            self.assertEqual(expected[position - 1], '--print', name)
            expected[position - 1:position - 1] = ['--print', MEDIA_PRINT]
            # 2. the temp path, directly after -P <OUT>, only where the finished file is renamed
            self.assertEqual(expected.count('-P'), 1, name)
            if variant not in NOT_RENAMED:
                position = expected.index('-P')
                self.assertEqual(expected[position + 1], '<OUT>', name)
                expected[position + 2:position + 2] = ['-P', 'temp:<STATE>\\work']
            # 3. the engine's own download interval only between the entries of a playlist
            if '--yes-playlist' not in expected and '--sleep-interval' in expected:
                position = expected.index('--sleep-interval')
                self.assertEqual(expected[position:position + 4], ['--sleep-interval', '10', '--max-sleep-interval', '20'], name)
                del expected[position:position + 4]
            self.assertEqual(current[name], expected, name)
        # The cases above cover every old format and nothing was skipped.
        self.assertEqual({name.split('.', 1)[0] for name in legacy}, {f'mode{index}' for index in range(LEGACY_MODE_COUNT)})

    def test_every_flag_exists_in_the_bundled_engine(self):
        """Hard rule 1: no option the bundled yt-dlp does not know (read from its own --help, offline)."""
        result = subprocess.run([str(engine.engine_path()), '--ignore-config', '--help'], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=120, stdin=subprocess.DEVNULL,
                                env=engine.process_environment(), creationflags=engine.HIDDEN)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        help_text = result.stdout
        self.assertIn('--ignore-config', help_text)
        flags = set()
        for name, command in self.commands().items():
            words = command[1:command.index('--')]
            flags |= {word for word in words if re.fullmatch(r'-[A-Za-z]|--[a-z][a-z0-9-]+', word)}
        self.assertGreater(len(flags), 40)
        for expected in ('--embed-metadata', '--parse-metadata', '--embed-thumbnail', '--convert-thumbnails', '--embed-subs',
                         '--sub-langs', '--sleep-subtitles', '--sleep-requests', '--sleep-interval', '-P', '-S', '-f', '-x'):
            self.assertIn(expected, flags)
        missing = [flag for flag in sorted(flags)
                   if not re.search(r'(?m)(?:^|[\s,])' + re.escape(flag) + r'(?=[\s,]|$)', help_text)]
        # Two flags that 1.5.1 already passed are accepted by the engine but no longer listed in its
        # help (deprecated spellings).  They are part of the protected head of the command.
        self.assertEqual(missing, sorted(INHERITED_HIDDEN), 'flags the bundled engine does not list in --help')
        legacy_words = {word for command in json.loads((_sandbox.GOLDEN / LEGACY_GOLDEN).read_text(encoding='utf-8')).values()
                        for word in command}
        self.assertLessEqual(set(INHERITED_HIDDEN), legacy_words, 'only flags of 1.5.1 may be missing from the help')
        # What counts in the end: the engine's own option parser accepts all of it (no link: it only
        # parses and prints its usage error).
        probe = subprocess.run([str(engine.engine_path()), *engine.probe_args()], capture_output=True,
                               encoding='utf-8', errors='replace', timeout=120, stdin=subprocess.DEVNULL,
                               env=engine.process_environment(), creationflags=engine.HIDDEN)
        output = probe.stdout + probe.stderr
        self.assertNotIn('no such option', output, output[-800:])
        self.assertIn('You must provide at least one URL', output, output[-800:])
        self.assertEqual(flags - set(engine.probe_args()), {'--cookies'},
                         'probe_args() covers every flag of the matrix except the one that needs a file')


if __name__ == '__main__':
    unittest.main()
