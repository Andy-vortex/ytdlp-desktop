# quick
"""The unfinished list (1.6.0 WP2, R9): unfinished.py, the Options serialisers, and the list as
Runner.download leaves it.

No process is started: _run_once is scripted.  (2.0.0 S2 P3) The file is format 2 (version 2, no
1.5.1 view); (S2 Q2, PLAN 3.10) a file that is not version 2 is read as empty and set aside as
failed-downloads.bad-<time>.json, at the window's start or before the next save (ForeignListTests).
"""
import _sandbox
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import engine
import formats
import i18n
import paths
import unfinished
import urls

A, B, C = ('https://example.invalid/' + name for name in 'abc')
ENTRY_KEYS = {'key', 'format', 'url', 'title', 'reason', 'error', 'time', 'referer', 'options'}


class Case(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'state'
        patcher = _sandbox.state_patch(self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        engine.Runner.reset_pacing()
        pace = mock.patch.object(engine, 'PACE_SECONDS', (0, 0))
        pace.start()
        self.addCleanup(pace.stop)
        self.out = self.root / 'out'
        self.options = engine.Options(self.out)
        self.events = []

    def file(self):
        return json.loads(paths.failed_file().read_text(encoding='utf-8'))

    def write(self, data):
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False),
                                       encoding='utf-8')

    def entry(self, url, options=None, reason='failed', **more):
        options = options or self.options
        return unfinished.entry(url, engine._entry_options(options), reason, playlist=options.playlist, **more)


class StoreTests(Case):
    def test_entry_shape(self):
        options = engine.Options(self.out, formats.MODES[4], '1080p', proxy='http://user:secret@127.0.0.1:1',
                                 cookies='D:\\cookies.txt', referers={A: 'https://example.org/page'},
                                 archive_contexts={A: {'title': '甲'}})
        before = time.time()
        item = unfinished.entry(A, options.to_dict(private=True), 'stopped', error='x' * 300, title='标题',
                                referer='https://example.org/page')
        self.assertEqual(set(item), ENTRY_KEYS)
        self.assertEqual((item['key'], item['format'], item['url'], item['title'], item['reason'], item['referer']),
                         (urls.link_key(A), 'video_mkv@1080p', A, '标题', 'stopped', 'https://example.org/page'))
        self.assertEqual(len(item['error']), 200)
        self.assertGreaterEqual(item['time'], before)
        self.assertEqual(item['options']['proxy'], '', 'the proxy is never part of an entry')
        self.assertEqual(item['options']['cookies'], '', 'the cookies path is never part of an entry')
        self.assertNotIn('referers', item['options'])
        self.assertNotIn('archive_contexts', item['options'])
        self.assertEqual(item['options']['output'], str(self.out))
        self.assertEqual(unfinished.signature(item), (item['key'], 'video_mkv@1080p'))
        # The key follows the playlist option like the queue does.
        listed = 'https://www.youtube.com/watch?v=AAA&list=PLx'
        self.assertEqual(unfinished.entry(listed, self.options.to_dict(), 'failed')['key'], 'youtube:AAA')
        self.assertEqual(unfinished.entry(listed, self.options.to_dict(), 'failed', playlist=True)['key'], 'youtube:list:PLx')
        self.assertEqual(unfinished.entry(A, self.options.to_dict(), 'nonsense')['reason'], 'failed')
        with self.assertRaises(ValueError):
            unfinished.entry(A, {'output': 'D:\\x', 'mode': 'no such format'}, 'failed')
        with self.assertRaises(ValueError):
            unfinished.entry(A, {'output': 'D:\\x'}, 'failed')

    def test_save_and_load_round_trip(self):
        entries = [self.entry(A, title='甲'), self.entry(B, reason='unstarted', referer='https://example.org/p')]
        path = unfinished.save(entries)
        self.assertEqual(path, paths.failed_file())
        self.assertEqual(unfinished.load(), entries)
        data = self.file()
        self.assertEqual(list(data), ['version', 'entries'], '(2.0.0) no 1.5.1 view')
        self.assertEqual(data['version'], 2)
        # Fix round 1 (core-rv-state-1-1): an entry's options are written without their default values.
        self.assertEqual(data['entries'], [dict(item, options=unfinished._written_options(item['options'])) for item in entries])
        self.assertEqual(data['entries'][0]['options'], {'output': str(self.out), 'mode': formats.MODES[0]})
        self.assertEqual([name for name in os.listdir(self.state)], ['failed-downloads.json'], 'no temp file is left')
        self.assertEqual(engine.load_unfinished(), entries)
        self.assertEqual(engine.save_unfinished(entries[:1]), paths.failed_file())
        self.assertEqual(unfinished.load(), entries[:1])

    def test_an_empty_list_removes_the_file_and_writes_nothing(self):
        self.assertIsNone(unfinished.save([]))
        self.assertFalse(self.state.exists(), 'nothing to store and no file: nothing is created')
        unfinished.save([self.entry(A)])
        self.assertTrue(paths.failed_file().is_file())
        self.assertIsNone(unfinished.save([]))
        self.assertFalse(paths.failed_file().exists())
        self.assertEqual(unfinished.load(), [])
        self.assertIsNone(engine.save_unfinished([]))

    def test_corrupt_oversized_and_foreign_files_give_an_empty_list(self):
        self.assertEqual(unfinished.load(), [], 'no file')
        for content in ('{truncated', '', '[]', 'null', '"text"', '{"version": 2, "urls": ["https://a.test/1"]}',
                        '{"version": 1}', '{"version": 1, "urls": "https://a.test/1"}',
                        '{"version": 1, "urls": [], "options": {"output": "D:\\\\x"}}',
                        '{"version": 1, "urls": ["https://a.test/1"], "options": []}',
                        '{"version": 1, "urls": ["https://a.test/1"], "options": {"output": " "}}',
                        '{"version": 1, "urls": ["https://a.test/1", 7], "options": {"output": "D:\\\\x"}}',
                        '{"version": 1, "urls": ["not a link"], "options": {"output": "D:\\\\x"}}',
                        '{"version": 1, "urls": ["https://a.test/1"], "options": {"output": "D:\\\\x", "mode": "x"}}',
                        '{"version": 1, "urls": ["https://a.test/1"], "options": {"output": "D:\\\\x"}, "entries": {}}',
                        '{"version": 1, "entries": "x"}'):
            self.write(content)
            self.assertEqual(unfinished.load(), [], content)
        paths.failed_file().write_bytes(b'\xff\xfe\x00broken')
        self.assertEqual(unfinished.load(), [])
        self.write({'version': 1, 'entries': [self.entry(A)], 'padding': 'x' * (5 * 1024 * 1024)})
        self.assertGreater(paths.failed_file().stat().st_size, 5 * 1024 * 1024)
        self.assertEqual(unfinished.load(), [], 'larger than 5 MB')

    def test_entries_are_authoritative_and_broken_ones_are_dropped(self):
        good = self.entry(A)
        self.write({'version': 2, 'urls': [B, C], 'options': {'output': str(self.out)},
                    'entries': [good, 'text', {'key': 'k'}, dict(good, options='x'), dict(good, url=7),
                                dict(self.entry(B), title=None, error=5, referer=[], reason='odd', time='yesterday')]})
        loaded = unfinished.load()
        self.assertEqual([item['url'] for item in loaded], [A, B], 'keys other than entries are not read')
        self.assertEqual(loaded[0], good)
        self.assertEqual((loaded[1]['title'], loaded[1]['error'], loaded[1]['referer'], loaded[1]['reason'], loaded[1]['time']),
                         ('', '', '', 'failed', 0.0))
        self.write({'version': 2, 'urls': [B], 'options': {'output': str(self.out)}, 'entries': []})
        self.assertEqual(unfinished.load(), [], 'an empty entries list is an empty list')
        self.assertEqual(unfinished._read(paths.failed_file())[1], '', 'and the file is read completely')

    def test_options_survive_the_round_trip_through_an_entry(self):
        options = engine.Options(self.out, formats.MODES[6], '1440p', True, '', False, {}, str(self.root / '资料'), {}, '',
                                 naming='title_id', embed_metadata=False, embed_cover=False, subtitles=True,
                                 skip_downloaded=True)
        unfinished.save([self.entry(A, options)])
        stored = unfinished.load()[0]
        self.assertEqual(engine.Options.from_dict(stored['options']), options)
        self.assertEqual(stored['format'], 'video_mp4@1440p', 'a video format with a height limit names it')
        default = engine.Options(self.out)
        unfinished.save([self.entry(A, default)])
        self.assertEqual(engine.Options.from_dict(unfinished.load()[0]['options']), default)
        # The session values come from the caller when the entry is retried.
        retried = engine.Options.from_dict(stored['options'], batch_safe=True, proxy='http://127.0.0.1:1', cookies='D:\\c.txt')
        self.assertEqual((retried.batch_safe, retried.proxy, retried.cookies), (True, 'http://127.0.0.1:1', 'D:\\c.txt'))

    def test_proxy_and_cookies_are_never_written(self):
        cookies = self.root / 'my-cookies.txt'
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        options = engine.Options(self.out, proxy='http://user:topsecret@127.0.0.1:7890', cookies=str(cookies))
        _sandbox.save_unfinished([A], options)
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with mock.patch.object(runner, '_run_once', return_value=(1, False, None)):
            runner.download([B], options)
        for path in (path for path in self.state.rglob('*') if path.is_file()):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('topsecret', text, path.name)
            self.assertNotIn('my-cookies', text, path.name)
        for item in unfinished.load():
            self.assertEqual((item['options']['proxy'], item['options']['cookies']), ('', ''))

    def test_remove(self):
        entries = [self.entry(A), self.entry(B), self.entry(A, engine.Options(self.out, formats.MODES[3]))]
        kept = unfinished.remove(entries, [unfinished.signature(entries[0])])
        self.assertEqual(kept, entries[1:])
        self.assertEqual(unfinished.remove(entries, []), entries)
        self.assertEqual(unfinished.remove(entries, [list(unfinished.signature(item)) for item in entries]), [])
        self.assertEqual(len(entries), 3, 'the given list is not changed')

class OptionsTests(Case):
    def test_new_fields_and_positional_order(self):
        options = engine.Options(self.out)
        self.assertEqual((options.naming, options.embed_metadata, options.embed_cover, options.subtitles,
                          options.skip_downloaded), ('title', True, True, False, False))
        positional = engine.Options(self.out, formats.MODES[4], '1080p', True, 'http://127.0.0.1:1', False, {'a': 'b'},
                                    'D:/archive', {'a': {}}, 'D:/cookies.txt', 'clean', False, False, True, True)
        self.assertEqual((positional.cookies, positional.naming, positional.embed_metadata, positional.embed_cover,
                          positional.subtitles, positional.skip_downloaded), ('D:/cookies.txt', 'clean', False, False, True, True))

    def test_to_dict(self):
        options = engine.Options(self.out, formats.MODES[4], '1080p', True, 'http://user:pw@127.0.0.1:1', False,
                                 {A: 'https://example.org/p'}, 'D:\\资料', {A: {'title': '甲'}}, 'D:\\c.txt', 'clean',
                                 False, True, True, True)
        data = options.to_dict()
        self.assertEqual(data, {'output': str(self.out), 'mode': formats.MODES[4], 'resolution': '1080p', 'playlist': True,
                                'proxy': '', 'batch_safe': False, 'referers': {A: 'https://example.org/p'},
                                'archive_root': 'D:\\资料', 'archive_contexts': {A: {'title': '甲'}}, 'cookies': '',
                                'naming': 'clean', 'embed_metadata': False, 'embed_cover': True, 'subtitles': True,
                                'skip_downloaded': True})
        private = options.to_dict(private=True)
        self.assertEqual((private['proxy'], private['cookies']), ('http://user:pw@127.0.0.1:1', 'D:\\c.txt'))
        self.assertEqual({key: value for key, value in private.items() if key not in ('proxy', 'cookies')},
                         {key: value for key, value in data.items() if key not in ('proxy', 'cookies')})
        json.dumps(data)
        data['referers'][B] = 'changed'
        self.assertNotIn(B, options.referers, 'to_dict hands out copies')

    def test_from_dict(self):
        options = engine.Options(self.out, formats.MODES[5], 'best', True, 'socks5://127.0.0.1:1', False,
                                 {A: 'https://example.org/p'}, 'D:\\资料', {A: {'title': '甲'}}, 'D:\\c.txt', 'title_id',
                                 False, False, True, True)
        self.assertEqual(engine.Options.from_dict(options.to_dict(private=True)), options)
        self.assertEqual(engine.Options.from_dict(options.to_dict()), engine.Options(
            self.out, formats.MODES[5], 'best', True, '', False, {A: 'https://example.org/p'}, 'D:\\资料',
            {A: {'title': '甲'}}, '', 'title_id', False, False, True, True))
        # Missing keys get their defaults, unknown keys are ignored, a Path is accepted as output.
        self.assertEqual(engine.Options.from_dict({'output': str(self.out)}), engine.Options(self.out))
        self.assertEqual(engine.Options.from_dict({'output': self.out, 'urls': [A], 'future': 1, 'overrides': {}}),
                         engine.Options(self.out))
        # Overrides win over the data.
        self.assertEqual(engine.Options.from_dict({'output': 'D:\\x', 'mode': formats.MODES[1]}, mode=formats.MODES[2],
                                                  output=str(self.out)), engine.Options(self.out, formats.MODES[2]))
        # Flags are coerced, wrong types of the text and map fields fall back.
        loose = engine.Options.from_dict({'output': str(self.out), 'playlist': 1, 'batch_safe': 0, 'embed_metadata': None,
                                          'embed_cover': '', 'subtitles': 'yes', 'skip_downloaded': [1], 'proxy': 5,
                                          'cookies': None, 'archive_root': ['x'], 'referers': {A: 'ok', B: 7, 3: 'x'},
                                          'archive_contexts': 'x', 'naming': 'nonsense'})
        self.assertEqual(loose, engine.Options(self.out, playlist=True, batch_safe=False, referers={A: 'ok'},
                                               embed_metadata=False, embed_cover=False, subtitles=True, skip_downloaded=True))
        self.assertIs(loose.playlist, True)
        self.assertEqual(engine.Options.from_dict({'output': str(self.out), 'naming': 7}).naming, 'title')
        for style in ('title', 'clean', 'title_id'):
            self.assertEqual(engine.Options.from_dict({'output': str(self.out), 'naming': style}).naming, style)

    def test_from_dict_refuses_what_cannot_run(self):
        good = {'output': str(self.out), 'mode': formats.MODES[0], 'resolution': 'best'}
        for bad in ({'output': ''}, {'output': '   '}, {'output': None}, {'output': 7}, {'mode': 'x'}, {'mode': None},
                    {'mode': i18n.CATALOG['zh']['format.' + formats.MODES[0]]}, {'resolution': '999p'}, {'resolution': None}, {'resolution': 1080}):
            with self.assertRaises(ValueError, msg=bad):
                engine.Options.from_dict({**good, **bad})
        for not_a_dict in (None, [], 'x', 5):
            with self.assertRaises(ValueError):
                engine.Options.from_dict(not_a_dict)
        with self.assertRaises(ValueError):
            engine.Options.from_dict({})
        with self.assertRaises(ValueError):
            engine.Options.from_dict(good, mode='x')
        for mode in formats.MODES:
            self.assertEqual(engine.Options.from_dict({**good, 'mode': mode}).mode, mode)


class MergeTests(Case):
    def result(self, url, outcome, options=None, **more):
        options = options or self.options
        item = engine.QueueItem(1, url, options)
        data = {'url': url, 'key': item.key, 'format': item.format, 'options': engine._entry_options(options), 'outcome': outcome,
                'error': '', 'title': '', 'referer': options.referers.get(url, ''), 'media_keys': []}
        data.update(more)
        return data

    def test_finished_downloads_leave_and_unfinished_ones_are_stored(self):
        entries = [dict(self.entry(A), time=1.0), dict(self.entry(B), time=2.0), dict(self.entry(C), time=3.0)]
        merged = unfinished.merge(entries, [self.result(B, 'done'), self.result(C, 'skipped'),
                                            self.result('https://example.invalid/d', 'failed', error='x' * 300, title='丁'),
                                            self.result('https://example.invalid/e', 'stopped'),
                                            self.result('https://example.invalid/f', 'unstarted'),
                                            self.result('https://example.invalid/g', 'blocked'),
                                            self.result('https://example.invalid/h', 'done')])
        self.assertEqual([(item['url'][-1], item['reason']) for item in merged],
                         [('a', 'failed'), ('d', 'failed'), ('e', 'stopped'), ('f', 'unstarted'), ('g', 'blocked')])
        self.assertEqual(merged[0], entries[0], 'what the run did not touch stays as it is')
        self.assertEqual((merged[1]['title'], len(merged[1]['error'])), ('丁', 200))
        self.assertEqual(set(merged[1]), ENTRY_KEYS)
        self.assertEqual(len(entries), 3, 'the given list is not changed')
        self.assertEqual(unfinished.merge(entries, []), entries)
        self.assertEqual(unfinished.merge([], []), [])

    def test_an_older_entry_is_replaced_in_its_place_and_keeps_its_title(self):
        entries = [dict(self.entry(A, title='甲的标题'), time=1.0, error='old'), dict(self.entry(B, title='乙'), time=2.0)]
        merged = unfinished.merge(entries, [self.result(A, 'stopped', error='new')])
        self.assertEqual([item['url'] for item in merged], [A, B], 'same position')
        self.assertEqual((merged[0]['reason'], merged[0]['error'], merged[0]['title']), ('stopped', 'new', '甲的标题'))
        self.assertGreater(merged[0]['time'], 1.0)
        merged = unfinished.merge(entries, [self.result(A, 'failed', title='新标题')])
        self.assertEqual(merged[0]['title'], '新标题')

    def test_format_is_part_of_the_identity(self):
        mp3 = engine.Options(self.out, formats.MODES[3])
        entries = [self.entry(A), self.entry(A, mp3)]
        self.assertEqual([item['url'] for item in unfinished.merge(entries, [self.result(A, 'done')])], [A])
        self.assertEqual(unfinished.merge(entries, [self.result(A, 'done')])[0]['format'], 'audio_mp3')
        merged = unfinished.merge(entries[:1], [self.result(A, 'failed', mp3)])
        self.assertEqual([item['format'] for item in merged], ['audio_best', 'audio_mp3'])

    def test_media_keys_clear_entries_of_other_links(self):
        watch, search = 'https://www.youtube.com/watch?v=AAA', 'https://www.youtube.com/results?search_query=x'
        entries = [self.entry(watch), self.entry(B)]
        merged = unfinished.merge(entries, [self.result(search, 'done', media_keys=['youtube:AAA'])])
        self.assertEqual([item['url'] for item in merged], [B])
        # The generic extractor's ids are file names: they never clear anything.
        generic = dict(self.entry(B), key='generic:index')
        self.assertEqual(len(unfinished.merge([generic], [self.result(A, 'done', media_keys=['generic:index'])])), 1)
        # Only finished items clear by media key.
        self.assertEqual(len(unfinished.merge(entries, [self.result(search, 'failed', media_keys=['youtube:AAA'])])), 3)

    def test_a_download_that_also_finished_in_the_same_run_is_not_stored(self):
        merged = unfinished.merge([], [self.result(A, 'failed'), self.result(B, 'done'), self.result(A, 'done')])
        self.assertEqual(merged, [])
        watch, search = 'https://www.youtube.com/watch?v=AAA', 'https://www.youtube.com/results?search_query=x'
        merged = unfinished.merge([], [self.result(search, 'done', media_keys=['youtube:AAA']), self.result(watch, 'blocked')])
        self.assertEqual(merged, [], 'the file is on disk through the other link')

    def test_results_without_a_format_are_not_stored(self):
        self.assertEqual(unfinished.merge([], [self.result(A, 'failed', format='')]), [])
        self.assertEqual(unfinished.merge([], [self.result(A, 'nonsense')]), [])


class RunTests(Case):
    """R9: the list as Runner.download leaves it."""

    def run_job(self, links, results, options=None, overrides=None, hook=None):
        """One download(); ``results`` are the scripted engine runs, in order: 0 / 1, or a function."""
        steps = list(results)

        def run_once(command, index, total, files):
            step = steps.pop(0)
            return step(runner, command, index, total, files) if callable(step) else (step, False, None)

        def emit(kind, value):
            self.events.append((kind, value))
            if hook:
                hook(runner, kind, value)

        runner = engine.Runner(emit)
        with mock.patch.object(runner, '_run_once', side_effect=run_once), mock.patch.object(runner, '_wait', return_value=True):
            if overrides is None:
                runner.download(links, options or self.options)
            else:
                runner.download(links, options or self.options, overrides)
        self.assertEqual(steps, [])
        return self.events[-1][1]

    def listed(self):
        return [(item['url'], item['format'], item['reason']) for item in unfinished.load()]

    def test_a_fully_successful_first_job_writes_no_file(self):
        done = self.run_job([A, B], [0, 0])
        self.assertIsNone(done['failed_path'])
        self.assertEqual((done['unfinished'], done['pending_urls']), (0, []))
        self.assertFalse(paths.failed_file().exists())
        self.assertEqual([name for name in os.listdir(self.state) if name != 'work'], [], 'nothing but the work folder')

    def test_retrying_a_subset_with_other_referers_clears_the_entry(self):
        """robust-1 (a): A fails, B succeeds with a referer; a retry of A alone, without B's referer."""
        first = replace(self.options, referers={B: 'https://example.org/page'})
        self.run_job([A, B], [1, 0], options=first)
        self.assertEqual(self.listed(), [(A, 'audio_best', 'failed')])
        done = self.run_job([A], [0], options=self.options)
        self.assertEqual(self.listed(), [])
        self.assertIsNone(done['failed_path'])
        self.assertFalse(paths.failed_file().exists())

    def test_a_retried_entry_in_a_video_resolution_leaves_the_list(self):
        """Fix round 1 (bughunt-9): the stored entry and the retry have the same format key.  (2.0.0 S2 Q1)
        The list is written in format 2 (before: a 1.5.1 file migrated at start-up); the
        entries are the same: a video preset in a resolution other than best."""
        _sandbox.save_unfinished([A, B], engine.Options(self.out, formats.MODES[4], '1080p'))
        job = {'options': engine.Options.from_dict(unfinished.load()[0]['options'])}
        self.assertEqual(self.listed(), [(A, 'video_mkv@1080p', 'failed'), (B, 'video_mkv@1080p', 'failed')])
        self.run_job([A], [0], options=job['options'])
        self.assertEqual(self.listed(), [(B, 'video_mkv@1080p', 'failed')])
        # The same video in its best quality is another download: it leaves the 1080p entry alone.
        self.run_job([B], [0], options=replace(job['options'], resolution=engine.RESOLUTIONS[0]))
        self.assertEqual(self.listed(), [(B, 'video_mkv@1080p', 'failed')])

    def test_another_spelling_of_the_link_clears_the_entry(self):
        """robust-1 (c)."""
        self.run_job(['https://www.youtube.com/watch?v=AAA&list=RDMMAAA&start_radio=1'], [1],
                     options=replace(self.options, batch_safe=False))
        self.assertEqual([item['key'] for item in unfinished.load()], ['youtube:AAA'])
        self.run_job(['https://youtu.be/AAA?si=x'], [0])
        self.assertEqual(self.listed(), [])

    def test_a_media_event_from_a_search_link_clears_the_watch_link(self):
        """The owner's case: the song was saved through a search link; only the media id can tell."""
        watch = 'https://www.youtube.com/watch?v=AAA'
        self.run_job([watch, B], [1, 1])
        self.assertEqual([item['url'] for item in unfinished.load()], [watch, B])

        def found(runner, command, index, total, files):
            kind, value = runner._intercept('media', {'id': 'AAA', 'extractor_key': 'Youtube', 'title': '白羊',
                                                      'filepath': str(self.out / '白羊 [AAA].opus')})
            runner.emit(kind, value)
            return 0, False, None

        with mock.patch.object(engine.naming, 'finalize', side_effect=lambda path, *_a, **_k: path):
            done = self.run_job(['https://www.youtube.com/results?search_query=x'], [found])
        self.assertEqual(self.listed(), [(B, 'audio_best', 'failed')])
        self.assertEqual((done['unfinished'], done['failed_path']), (1, str(paths.failed_file())))

    def test_a_failure_in_another_format_does_not_replace_older_entries(self):
        """robust-1 (b)."""
        video = engine.Options(self.root / 'video', formats.MODES[4], '1080p')
        self.run_job([A, B], [1, 1], options=video)
        self.run_job([C], [1], options=engine.Options(self.out, formats.MODES[3]))
        self.assertEqual(self.listed(), [(A, 'video_mkv@1080p', 'failed'), (B, 'video_mkv@1080p', 'failed'), (C, 'audio_mp3', 'failed')])
        self.assertEqual(engine.Options.from_dict(unfinished.load()[0]['options']), video, 'the older entries keep their options')

    def test_success_in_another_format_does_not_clear(self):
        video = engine.Options(self.root / 'video', formats.MODES[4])
        self.run_job([A], [1], options=video)
        done = self.run_job([A], [0], options=engine.Options(self.out, formats.MODES[3]))
        self.assertEqual(self.listed(), [(A, 'video_mkv', 'failed')])
        self.assertEqual((done['pending_urls'], done['unfinished'], done['failed_path']), ([], 1, str(paths.failed_file())))
        done = self.run_job([A], [0], options=video)
        self.assertEqual(self.listed(), [])

    def test_stop_marks_the_running_link_stopped_and_the_rest_unstarted(self):
        def stop(runner, command, index, total, files):
            runner.cancel()
            return 1, False, None

        done = self.run_job([A, B, C], [0, stop])
        self.assertEqual(self.listed(), [(B, 'audio_best', 'stopped'), (C, 'audio_best', 'unstarted')])
        self.assertEqual((done['failed'], done['failed_urls'], done['pending_urls'], done['cancelled']), (0, [], [B, C], True))
        self.assertNotIn('failed', [reason for _url, _format, reason in self.listed()], 'stopped is not failed')

    def test_each_entry_keeps_the_options_of_its_own_link(self):
        video = engine.Options(self.root / 'video', formats.MODES[6], '720p', batch_safe=False,
                               referers={B: 'https://example.org/page'}, proxy='http://user:pw@127.0.0.1:1')
        self.run_job([A, B], [1, 1], overrides={B: video})
        first, second = unfinished.load()
        self.assertEqual(engine.Options.from_dict(first['options']), self.options)
        self.assertEqual(engine.Options.from_dict(second['options']), replace(video, referers={}, proxy=''))
        self.assertEqual((second['referer'], second['format']), ('https://example.org/page', 'video_mp4@720p'))
        self.assertEqual(first['referer'], '')
        self.assertNotIn('pw@', paths.failed_file().read_text(encoding='utf-8'))

    def test_a_list_that_cannot_be_saved_is_reported_and_the_job_still_ends(self):
        blocker = self.root / 'a-file'
        blocker.write_text('x', encoding='utf-8')
        patcher = _sandbox.state_patch(blocker / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        done = self.run_job([A], [1])
        self.assertEqual((done['failed'], done['failed_path'], done['unfinished'], done['pending_urls']), (1, None, 0, [A]))
        self.assertTrue([line for kind, line in self.events
                         if kind == 'log' and getattr(line, 'key', None) == 'core.engine.unfinished_not_saved'])



LOG = 'YtDlpDesktop.unfinished'


class ForeignListTests(Case):
    """(2.0.0 S2 Q2, S2-AMEND.md 2.2, 2.5 items 11-13) A file that is not version 2 (1.6.0-1.8.1 wrote
    version 1, 1.5.1 had no version, a later version may write 3): no entry is read from it, nothing is
    written into it; it is renamed to failed-downloads.bad-<time>.json (byte for byte) and one line says so."""

    def shapes(self):
        entry = dict(self.entry(A), options={'output': str(self.out), 'mode': formats.MODES[0]})
        return [{'version': 1, 'entries': [entry]}, {'urls': [A, B], 'options': {'output': str(self.out)}},
                {'version': 3, 'entries': []}, {'version': 2, 'entries': {}}]

    def kept(self):
        return sorted(self.state.glob('failed-downloads.bad-*.json'))

    def clear(self):
        for path in self.kept():
            path.unlink()

    def assert_kept(self, before, lines):
        """The list file was renamed (its bytes), reported once as kept aside (unknown_format)."""
        kept = self.kept()
        self.assertEqual([path.read_bytes() for path in kept], [before])
        self.assertEqual([(line.key, line.params['problem'].key, line.params['target']) for line in lines],
                         [('core.unfinished.kept_aside', 'core.unfinished.problem.unknown_format', kept[0].name)])

    def test_11_a_file_that_is_not_version_2_is_set_aside_at_the_start(self):
        for data in self.shapes():
            with self.subTest(data=data):
                self.clear()
                self.write(data)
                before = paths.failed_file().read_bytes()
                self.assertEqual(unfinished.load(), [])
                entries, problem, stamp = unfinished._read(paths.failed_file())
                self.assertEqual((entries, problem.key, stamp), ([], 'core.unfinished.problem.unknown_format', None))
                self.assertEqual(paths.failed_file().read_bytes(), before, 'load() changes nothing')
                self.assertEqual(sorted(os.listdir(self.state)), ['failed-downloads.json'])
                lines = []
                with self.assertLogs(LOG, 'WARNING') as logs:
                    self.assertEqual(unfinished.load_at_start(lines.append), [])
                self.assertFalse(paths.failed_file().exists())
                self.assert_kept(before, lines)
                self.assertEqual(len(logs.output), 1, 'download.log gets the same one line')
                again = []
                with self.assertNoLogs(LOG):
                    self.assertEqual(unfinished.load_at_start(again.append), [])
                self.assertEqual((again, len(self.kept())), ([], 1), 'nothing more the second time')

    def test_11_the_merge_and_save_still_set_such_a_file_aside_first(self):
        """A file that turns up after the start (or could not be renamed then): the 1.8.1 path."""
        for data in self.shapes():
            for name, call in (('load_for_merge', lambda lines: unfinished.load_for_merge(lines.append)),
                               ('save', lambda lines: unfinished.save([self.entry(C)], log=lines.append)),
                               ('save []', lambda lines: unfinished.save([], log=lines.append))):
                with self.subTest(data=data, call=name):
                    self.clear()
                    self.write(data)
                    before = paths.failed_file().read_bytes()
                    lines = []
                    with self.assertLogs(LOG, 'WARNING'):
                        result = call(lines)
                    self.assert_kept(before, lines)
                    if name == 'save':
                        self.assertEqual((self.file()['version'], [item['url'] for item in unfinished.load()]), (2, [C]))
                    else:
                        self.assertFalse(paths.failed_file().exists())
                        self.assertEqual(result, [] if name == 'load_for_merge' else None)
                    if paths.failed_file().exists():
                        paths.failed_file().unlink()

    def test_11b_load_at_start_leaves_no_file_a_good_file_and_a_partly_usable_file_alone(self):
        good = self.entry(A)
        with self.assertNoLogs(LOG):
            self.assertEqual(unfinished.load_at_start(self.fail), [])
        self.assertFalse(self.state.exists() and os.listdir(self.state))
        unfinished.save([good])
        before = paths.failed_file().read_bytes()
        with self.assertNoLogs(LOG):
            self.assertEqual(unfinished.load_at_start(self.fail), [good])
        self.assertEqual(paths.failed_file().read_bytes(), before)
        # dropped: the usable entries are kept and the file is left to the first merge (1.8.1), so that
        # no link is only in memory while the file is already renamed (PLAN 3.3)
        self.write({'version': 2, 'entries': [unfinished._written(good), {'key': 'k'}]})
        before = paths.failed_file().read_bytes()
        with self.assertNoLogs(LOG):
            self.assertEqual(unfinished.load_at_start(self.fail), [good])
        self.assertEqual(paths.failed_file().read_bytes(), before)
        self.assertEqual(os.listdir(self.state), ['failed-downloads.json'])

    def test_12_a_run_sets_aside_a_file_that_is_not_version_2_and_writes_version_2(self):
        self.write({'version': 1, 'urls': [A], 'options': {'output': str(self.out)}})
        before = paths.failed_file().read_bytes()
        done = self.run_job([B], [1])
        self.assertEqual((done['failed'], done['unfinished']), (1, 1))
        lines = [value for kind, value in self.events if kind == 'log' and getattr(value, 'key', None) == 'core.unfinished.kept_aside']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].params['problem'].key, 'core.unfinished.problem.unknown_format')
        self.assertEqual([path.read_bytes() for path in self.kept()], [before])
        self.assertEqual(lines[0].params['target'], self.kept()[0].name)
        self.assertEqual((self.file()['version'], [item['url'] for item in unfinished.load()]), (2, [B]))

    def test_13_a_file_in_use_is_not_touched(self):
        self.write({'version': 1, 'entries': []})
        before = paths.failed_file().read_bytes()
        with open(paths.failed_file(), 'rb'):      # Windows gives no delete sharing: the rename fails
            with self.assertNoLogs(LOG):
                self.assertEqual(unfinished.load_at_start(self.fail), [])
            self.assertEqual(paths.failed_file().read_bytes(), before)
            with self.assertRaises(OSError):
                unfinished.load_for_merge()
            done = self.run_job([B], [1])
        self.assertEqual((done['failed'], done['failed_path'], done['unfinished']), (1, None, 0))
        lines = [value for kind, value in self.events
                 if kind == 'log' and getattr(value, 'key', None) == 'core.engine.unfinished_not_saved']
        self.assertEqual(len(lines), 1)
        self.assertEqual(paths.failed_file().read_bytes(), before)
        self.assertEqual(self.kept(), [])
        # The same without depending on the platform: set_aside raises PermissionError.
        with mock.patch.object(paths, 'set_aside', side_effect=PermissionError(13, 'in use')), self.assertNoLogs(LOG):
            self.assertEqual(unfinished.load_at_start(self.fail), [])
        self.assertEqual(paths.failed_file().read_bytes(), before)

    run_job = RunTests.run_job


if __name__ == '__main__':
    unittest.main()
