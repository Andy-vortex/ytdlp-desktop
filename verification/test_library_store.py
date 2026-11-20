# quick
"""The history store of 1.6.0 (WP2 step H): the downloads table, BEGIN IMMEDIATE for
read-modify-write, the new job helpers, and download_event for the events the engine
gained.  Every database is a throw-away file in the temp folder; no process is started.

(2.0.0 S2 P2) The store is format 2 (S2-DESIGN.md 1.2): a new download job has no title until the
first media title ('done' pairs instead of completed / completed_variants, errors as
{'error', 'reason'}); the two 1.5.1 database tests are gone (2.0.0 does not read an older library).
(S2 Q2, PLAN 3.10) A library or an archive record that is not version 2 is set aside and a new one
starts (ForeignLibraryTests, ForeignArchiveTests).
(S2 Q3) What the deleted converter tests and the old-data fixture reached at run time, on rows this tree
writes: the options of 继续未完成 (ResumeOptionsTests), rows a hand edit can break (BrokenRowTests), every
title structure in both languages (TitlePartsTests).
"""
import _sandbox
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import engine
import library
import messages
import paths
import urls
from library import Archive, Library
from messages import Message
from _sandbox import en_t, zh_t

A, B, C = ('https://example.invalid/' + name for name in 'abc')
Y1 = 'https://www.youtube.com/watch?v=AAA'


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Library(self.root / 'library.sqlite3')

    def job(self, links=(A,), **options):
        return self.store.create_job('download', '', dict(urls=list(links), **options))

    def data(self, job):
        return self.store.job(job)['data']

    def title(self, job):
        """library.title_parts of the job: what the history list names it after (S2-DESIGN.md 1.2)."""
        return library.title_parts(self.store.job(job))

    def media_file(self, name='歌.opus'):
        path = self.root / name
        path.write_bytes(b'media')
        return path

    def subscription_with(self, *links):
        """A subscription whose listed links are all pending."""
        sub = self.store.subscription('订阅', dict(urls=['https://example.org/list']))
        page = dict(url='https://example.org/list', title='页', links=[dict(url=link, title='t', kind='media') for link in links])
        self.assertEqual(self.store.finish_subscription(sub, [page], True), len(links))
        return sub

    def pending(self, sub):
        return [entry['url'] for entry in self.store.pending(sub)]


class DownloadsTableTests(StoreCase):
    def test_round_trip_and_existence_flag(self):
        path = self.media_file()
        before = time.time()
        self.store.record_download(['youtube:AAA', 'youtube:AAA', '', None, urls.link_key(A)], 'audio_best', Y1, '歌', str(path), 'job-1')
        row = self.store.find_download(['youtube:AAA'], 'audio_best')
        self.assertEqual({key: row[key] for key in ('key', 'format', 'url', 'title', 'path', 'job')},
                         {'key': 'youtube:AAA', 'format': 'audio_best', 'url': Y1, 'title': '歌', 'path': str(path), 'job': 'job-1'})
        self.assertGreaterEqual(row['finished'], before)
        self.assertIs(row['exists'], True)
        self.assertEqual(set(row), {'key', 'format', 'url', 'title', 'path', 'finished', 'job', 'exists'})
        self.assertEqual(self.store.find_download([urls.link_key(A)], 'audio_best')['path'], str(path), 'one row per key')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM downloads').fetchone()[0], 2, 'duplicate and empty keys are dropped')
        # Another format, an unknown key and no key at all.
        self.assertIsNone(self.store.find_download(['youtube:AAA'], 'audio_mp3'))
        self.assertIsNone(self.store.find_download(['youtube:BBB'], 'audio_best'))
        self.assertIsNone(self.store.find_download([], 'audio_best'))
        self.assertIsNone(self.store.find_download(['', None], 'audio_best'))
        # The file is gone (moved, renamed, deleted): the row stays, the flag says so.
        path.unlink()
        self.assertIs(self.store.find_download(['youtube:AAA'], 'audio_best')['exists'], False)
        self.store.record_download(['k'], 'audio_best', A, '', '', None)
        self.assertIs(self.store.find_download(['k'], 'audio_best')['exists'], False)
        self.store.record_download(['d'], 'audio_best', A, '', str(self.root), None)
        self.assertIs(self.store.find_download(['d'], 'audio_best')['exists'], False, 'a folder is not a downloaded file')

    def test_a_newer_download_replaces_the_row_and_the_newest_of_several_keys_wins(self):
        old, new = self.media_file('旧.opus'), self.media_file('新.opus')
        self.store.record_download(['youtube:AAA'], 'audio_best', Y1, '旧', str(old), 'job-1')
        self.store.record_download(['youtube:AAA'], 'audio_best', Y1, '新', str(new), 'job-2')
        row = self.store.find_download(['youtube:AAA'], 'audio_best')
        self.assertEqual((row['title'], row['path'], row['job']), ('新', str(new), 'job-2'))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM downloads').fetchone()[0], 1)
        with mock.patch('library.time.time', return_value=time.time() + 100):
            self.store.record_download(['other'], 'audio_best', A, '另', str(old), 'job-3')
        self.assertEqual(self.store.find_download(['youtube:AAA', 'other'], 'audio_best')['title'], '另')
        self.assertEqual(self.store.find_download(['other', 'youtube:AAA'], 'audio_best')['title'], '另')
        # The same key in two formats are two rows.
        self.store.record_download(['youtube:AAA'], 'audio_mp3', Y1, 'mp3', str(new), 'job-4')
        self.assertEqual(self.store.find_download(['youtube:AAA'], 'audio_mp3')['title'], 'mp3')
        self.assertEqual(self.store.find_download(['youtube:AAA'], 'audio_best')['title'], '新')

    def test_a_new_library_is_format_2(self):
        """(2.0.0) A file without tables gets them and user_version 2 (S2-DESIGN.md 2.6)."""
        with self.store.db() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual([row[1] for row in db.execute('PRAGMA table_info(downloads)')],
                             ['key', 'format', 'url', 'title', 'path', 'finished', 'job'])


class JobHelperTests(StoreCase):
    def test_extend_job(self):
        job = self.job([A])
        self.store.extend_job(job, [B, A, C, B])
        self.assertEqual(self.data(job)['options']['urls'], [A, B, C])
        self.assertNotIn('overrides', self.data(job)['options'])
        mp3 = {'output': 'D:\\音乐', 'mode': 'audio_mp3'}
        self.store.extend_job(job, ['https://example.invalid/d'], {'https://example.invalid/d': mp3})
        self.store.extend_job(job, [], {B: {'mode': 'video_mp4'}})
        options = self.data(job)['options']
        self.assertEqual(options['urls'], [A, B, C, 'https://example.invalid/d'])
        self.assertEqual(options['overrides'], {'https://example.invalid/d': mp3, B: {'mode': 'video_mp4'}})
        self.store.extend_job(job, [], None)
        self.store.extend_job(job, [], {})
        self.assertEqual(self.data(job)['options']['overrides'], options['overrides'])
        # A job record without a list of links (a collect job of an old version) gets one.
        bare = self.store.create_job('download', 'x', {})
        self.store.extend_job(bare, [A])
        self.assertEqual(self.data(bare)['options']['urls'], [A])

    def test_history_limit_and_order(self):
        jobs = []
        for number in range(5):
            with mock.patch('library.time.time', return_value=1000.0 + number):
                jobs.append(self.store.create_job('download', f'任务{number}', dict(urls=[A])))
        self.assertEqual([row['title'] for row in self.store.history()], ['任务4', '任务3', '任务2', '任务1', '任务0'])
        self.assertEqual([row['title'] for row in self.store.history(limit=2)], ['任务4', '任务3'])
        self.assertEqual([row['title'] for row in self.store.history(2)], ['任务4', '任务3'])
        self.assertEqual(len(self.store.history(limit=300)), 5)
        self.assertEqual(self.store.history(limit=0), [])
        import inspect
        self.assertEqual(inspect.signature(Library.history).parameters['limit'].default, 300)
        # A running job whose owner is gone is shown as interrupted, as before.
        self.store.change_job(jobs[0], owner=0, worker=None)
        self.assertEqual(self.store.history(limit=1)[0]['status'], 'interrupted')
        self.assertEqual(self.store.job(jobs[0])['status'], 'interrupted')

    def test_two_threads_changing_one_job_lose_nothing(self):
        """Read-modify-write on one row from two threads (the worker's events and a second writer)."""
        job = self.job([A])
        problems = []

        def files():
            try:
                for number in range(300):
                    self.store.download_event(job, 'file', f'D:\\file-{number}.opus')
            except Exception as error:
                problems.append(repr(error))

        def keys():
            try:
                for number in range(300):
                    self.store.change_job(job, **{f'key{number}': number})
            except Exception as error:
                problems.append(repr(error))

        threads = [threading.Thread(target=files), threading.Thread(target=keys)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(problems, [])
        data = self.data(job)
        self.assertEqual(len(data['files']), 300)
        self.assertEqual(sum(1 for key in data if key.startswith('key')), 300)

    def test_the_other_read_modify_write_methods_take_the_write_lock_first(self):
        statements = []
        real_connect = sqlite3.connect

        def connect(*arguments, **keywords):
            connection = real_connect(*arguments, **keywords)
            connection.set_trace_callback(statements.append)
            return connection

        job = self.job([A])
        sub = self.subscription_with(A)
        collect = self.store.create_job('collect', '采集', dict(urls=[A]))
        calls = {
            'change_job': lambda: self.store.change_job(job, note=1),
            'collection_event': lambda: self.store.collection_event(collect, 'collect_error', 'x'),
            'download_event': lambda: self.store.download_event(job, 'file', 'D:\\x'),
            'finish_subscription': lambda: self.store.finish_subscription(sub, [], False, 'x'),
            'extend_job': lambda: self.store.extend_job(job, [B]),
            'record_download': lambda: self.store.record_download(['k'], 'audio_best', A, 't', 'p', job),
        }
        with mock.patch('library.sqlite3.connect', connect):
            for name, call in calls.items():
                statements.clear()
                call()
                self.assertEqual(statements[0], 'BEGIN IMMEDIATE', name)
                self.assertEqual(statements[-1], 'COMMIT', name)
                self.assertEqual(statements.count('BEGIN IMMEDIATE'), 1, f'{name}: one transaction, one connection')


class DownloadEventTests(StoreCase):
    def test_events_of_1_5_1_without_any_new_key(self):
        """What the 1.5.1 window forwards (and test_library.py:125-129): item, item_done, file, done."""
        sub = self.subscription_with(A, B)
        job = self.job([A, B], proxy='secret')
        self.store.download_event(job, 'item', dict(url=A))
        self.store.download_event(job, 'item_done', dict(success=False))
        self.assertEqual(self.pending(sub), [A, B], 'a failed download does not consume the subscription entry')
        self.store.download_event(job, 'item_done', dict(success=True))
        self.assertEqual(self.pending(sub), [B])
        self.store.download_event(job, 'file', 'D:\\a.opus')
        self.store.download_event(job, 'file', 'D:\\a.opus')
        self.store.download_event(job, 'done', dict(success=1, failed=0, cancelled=False, files=['D:\\a.opus']))
        record = self.store.job(job)
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['data']['done'], [[A, None]], 'no format in the events and none in the job')
        self.assertEqual(record['data']['files'], ['D:\\a.opus'])
        self.assertEqual(record['data']['current'], A)
        self.assertEqual(record['data']['errors'], [])
        self.assertEqual(record['title'], '', 'no title event, no title')
        self.assertEqual(self.title(job), ('multi_link', A, 2))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM downloads').fetchone()[0], 0)
        for payload, status in ((dict(success=0, failed=1, cancelled=False, files=[]), 'partial'),
                                (dict(success=1, failed=0, cancelled=True, files=[]), 'interrupted'),
                                (dict(success=1, failed=0, cancelled=False, files=[], archive_errors=['x']), 'partial'),
                                (dict(success=1, failed=0, cancelled=False, files=[], blocked='bot_check'), 'partial'),
                                (dict(success=1, failed=0, cancelled=False, files=[], blocked=''), 'completed')):
            self.store.download_event(job, 'done', payload)
            self.assertEqual(self.store.job(job)['status'], status, payload)

    def test_unknown_kinds_are_ignored(self):
        job = self.job([A])
        before = self.store.job(job)
        for kind, value in (('log', 'text'), ('progress', {'fraction': 0.5}), ('waiting', {'seconds': 3}), ('archive', {}),
                            ('resuming', {}), ('something_new', None)):
            self.store.download_event(job, kind, value)
        self.assertEqual(self.store.job(job), before)

    def test_media_queued_and_title_return_at_once(self):
        """A nested transaction would wait 20 s for the method's own write lock and then fail."""
        job = self.job([A])
        path = self.media_file()
        self.store.download_event(job, 'item', dict(index=1, total=1, url=A, key=urls.link_key(A), format='audio_best',
                                                    origin=A, multi=False))
        for kind, value in (('media', dict(index=1, id='abc', extractor_key='Youtube', title='歌', uploader='某人', duration=3,
                                           webpage_url='https://www.youtube.com/watch?v=abc', filepath=str(path))),
                            ('queued', dict(urls=[B], first_index=2, total=2, overrides={B: {'mode': 'audio_mp3'}})),
                            ('title', '歌')):
            started = time.monotonic()
            self.store.download_event(job, kind, value)
            self.assertLess(time.monotonic() - started, 1, kind)
        for key in ('youtube:abc', urls.link_key(A)):
            row = self.store.find_download([key], 'audio_best')
            self.assertEqual((row['url'], row['title'], row['path'], row['job'], row['exists']), (A, '歌', str(path), job, True), key)
        data = self.data(job)
        self.assertEqual(data['options']['urls'], [A, B])
        self.assertEqual(data['options']['overrides'], {B: {'mode': 'audio_mp3'}})
        self.assertEqual(self.store.job(job)['title'], '歌')
        self.assertEqual(self.title(job), ('multi', '歌', 2))
        self.assertNotIn('titled', data)

    def test_title_names_the_job_once(self):
        single = self.job([A])
        self.store.download_event(single, 'title', '第一首')
        self.store.download_event(single, 'title', '第二首')
        self.assertEqual(self.store.job(single)['title'], '第一首')
        several = self.job([A, B, C])
        for bad in ('', '   ', None, 17, {'title': 'x'}):
            self.store.download_event(several, 'title', bad)
        self.assertEqual(self.store.job(several)['title'], '', 'an empty title names nothing')
        self.assertNotIn('title_links', self.data(several))
        self.store.download_event(several, 'title', '甲')
        self.assertEqual((self.store.job(several)['title'], self.data(several)['title_links']), ('甲', 3))
        self.assertEqual(self.title(several), ('multi', '甲', 3))
        self.assertEqual(self.title(single), ('plain', '第一首'))
        # The count is the job's at that moment: links that joined before the first title count, links
        # that join after it do not change it.
        grown = self.job([A])
        self.store.download_event(grown, 'queued', dict(urls=[B], first_index=2, total=2))
        self.store.download_event(grown, 'title', '乙')
        self.store.download_event(grown, 'queued', dict(urls=[C], first_index=3, total=3))
        self.assertEqual(self.title(grown), ('multi', '乙', 2))

    def test_a_playlist_item_records_its_media_keys_and_never_its_link_key(self):
        link = 'https://www.youtube.com/playlist?list=PLx'
        key = urls.link_key(link)
        job = self.job([link], playlist=True)
        self.store.download_event(job, 'item', dict(index=1, total=1, url=link, key=key, format='audio_best', origin=link, multi=True))
        for name in ('p1', 'p2'):
            self.store.download_event(job, 'media', dict(index=1, id=name, extractor_key='Youtube', title=name,
                                                         filepath=str(self.media_file(f'{name} [{name}].opus'))))
        self.assertEqual(self.store.find_download(['youtube:p1'], 'audio_best')['title'], 'p1')
        self.assertEqual(self.store.find_download(['youtube:p2'], 'audio_best')['title'], 'p2')
        self.assertIsNone(self.store.find_download([key], 'audio_best'), 'the list is not downloaded because two entries are')
        with self.store.db() as db:
            self.assertEqual(sorted(row[0] for row in db.execute('SELECT key FROM downloads')), ['youtube:p1', 'youtube:p2'])

    def test_media_without_usable_keys_records_nothing(self):
        job = self.job([A])
        path = str(self.media_file())
        # No item event yet: no format to record under.
        self.store.download_event(job, 'media', dict(id='abc', extractor_key='Youtube', title='歌', filepath=path))
        self.store.download_event(job, 'item', dict(index=1, total=1, url=A, key=urls.link_key(A), format='audio_best', multi=True))
        for value in (dict(id='index', extractor_key='Generic', title='x', filepath=path),    # generic ids are file names
                      dict(id='', extractor_key='Youtube', title='x', filepath=path),
                      dict(id='abc', title='x', filepath=path),                               # no extractor
                      dict(id='abc', extractor_key='Youtube', title='x'),                     # no path
                      dict(id='abc', extractor_key='Youtube', title='x', filepath=17),
                      'text', None, 17):
            self.store.download_event(job, 'media', value)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM downloads').fetchone()[0], 0)
        # A generic extractor on a single link: the link key alone is recorded.
        self.store.download_event(job, 'item', dict(index=1, total=1, url=A, key=urls.link_key(A), format='audio_best', multi=False))
        self.store.download_event(job, 'media', dict(id='index', extractor_key='Generic', title=None, filepath=path))
        with self.store.db() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT key, title FROM downloads')], [(urls.link_key(A), '')])

    def test_skipped_as_downloaded_completes_the_link_and_clears_the_subscription_entry(self):
        origin = 'https://youtu.be/AAA?si=x'
        sub = self.subscription_with(origin, B)
        job = self.job([Y1, B])
        # No item event precedes it; an earlier link is still "current".
        self.store.download_event(job, 'item', dict(index=1, total=2, url=B, key=urls.link_key(B), format='audio_best', origin=B, multi=False))
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, bot_check=False,
                                                         skipped='downloaded', url=Y1, origin=origin, title='歌', path='D:\\歌.opus'))
        data = self.data(job)
        self.assertEqual(data['done'], [[Y1, None]], 'the job has no format: none is known')
        self.assertEqual(self.pending(sub), [B], 'the link that is already on disk leaves the pending list')
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, skipped='downloaded',
                                                         url=Y1, origin=origin))
        self.assertEqual(self.data(job)['done'], [[Y1, None]], 'once')
        # Blocked by a bot check: nothing is completed.
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, bot_check=True,
                                                         skipped='bot_check', url=B, origin=B))
        self.assertEqual(self.data(job)['done'], [[Y1, None]])
        # With the format of the event (or the job's only one) the pair carries it.
        mp3 = self.job([A], mode='audio_mp3')
        self.store.download_event(mp3, 'item_done', dict(success=False, skipped='downloaded', url=A))
        self.store.download_event(mp3, 'item_done', dict(success=False, skipped='downloaded', url=C, format='video_mp4@1080p'))
        self.assertEqual(self.data(mp3)['done'], [[A, 'audio_mp3'], [C, 'video_mp4@1080p']])
        self.assertEqual(self.pending(sub), [B])

    def test_success_also_clears_the_entry_of_the_original_spelling(self):
        origin = Y1 + '&list=RDMMAAA&start_radio=1'      # identity() of 1.5.1 keeps the list: its own key
        sub = self.subscription_with(origin)
        job = self.job([Y1])
        self.store.download_event(job, 'item', dict(index=1, total=1, url=Y1, key='youtube:AAA', format='audio_best',
                                                    origin=origin, multi=False))
        self.assertEqual(self.pending(sub), [origin])
        self.store.download_event(job, 'item_done', dict(index=1, success=True, cancelled=False, files=1, bot_check=False, url=Y1,
                                                         origin=origin))
        self.assertEqual(self.pending(sub), [])
        self.assertEqual(self.data(job)['done'], [[Y1, 'audio_best']])

    def test_errors_are_kept_for_the_history_note(self):
        job = self.job([A])
        self.store.download_event(job, 'item', dict(url=A))
        self.store.download_event(job, 'item_done', dict(success=False, error='[x] a: HTTP Error 403: Forbidden'))
        self.store.download_event(job, 'item_done', dict(success=False, error=''))
        self.store.download_event(job, 'item_done', dict(success=False))
        self.assertEqual(self.data(job)['errors'], [{'error': '[x] a: HTTP Error 403: Forbidden', 'reason': None}])
        for number in range(30):
            self.store.download_event(job, 'item_done', dict(success=False, error=f'error {number}',
                                                             reason=f' reason {number} ' if number % 2 else '  '))
        errors = self.data(job)['errors']
        self.assertEqual(len(errors), 20, 'the last 20')
        self.assertEqual((errors[0], errors[-1]), ({'error': 'error 10', 'reason': None},
                                                   {'error': 'error 29', 'reason': 'reason 29'}))
        self.assertTrue(all(isinstance(item['error'], str) for item in errors))

    def test_item_keeps_what_the_engine_said_about_the_link(self):
        job = self.job([Y1])
        self.store.download_event(job, 'item', dict(index=1, total=1, url=Y1, key='youtube:AAA', format='video_mp4',
                                                    origin='https://youtu.be/AAA', multi=False))
        data = self.data(job)
        self.assertEqual((data['current'], data['current_key'], data['current_format'], data['current_origin'], data['current_multi']),
                         (Y1, 'youtube:AAA', 'video_mp4', 'https://youtu.be/AAA', False))
        self.store.download_event(job, 'item', dict(url=A))
        data = self.data(job)
        self.assertEqual((data['current'], data['current_key'], data['current_format'], data['current_origin'], data['current_multi']),
                         (A, None, None, None, False), 'an event of 1.5.1 resets what it does not carry')

    def test_a_whole_run_through_the_engine(self):
        """Runner.download with the emit closure of the window: every event goes to the store first."""
        job = self.job([A, B])
        events = []

        def emit(kind, value):
            if kind in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done'):
                self.store.download_event(job, kind, value)
            events.append((kind, value))

        path = self.media_file('甲 [abc].opus')

        def first(command, index, total, files):
            runner.enqueue([C])
            for kind, value in (('title', '甲'), ('media', dict(id='abc', extractor_key='Youtube', title='甲', filepath=str(path)))):
                kind, value = runner._intercept(kind, value)
                runner.emit(kind, value)
            return 0, False, None

        steps = iter([first, lambda *_: (1, False, None), lambda *_: (0, False, None)])
        patcher = _sandbox.state_patch(self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        engine.Runner.reset_pacing()
        self.addCleanup(engine.Runner.reset_pacing)
        runner = engine.Runner(emit, library=self.store)
        with mock.patch.object(engine, 'PACE_SECONDS', (0, 0)), \
                mock.patch.object(runner, '_run_once', side_effect=lambda *arguments: next(steps)(*arguments)):
            runner.download([A, B], engine.Options(self.root / 'out', skip_downloaded=True))
        record = self.store.job(job)
        self.assertEqual(record['status'], 'partial')
        self.assertEqual(library.title_parts(record), ('multi', '甲', 2))
        self.assertEqual(record['data']['done'], [[A, 'audio_best'], [C, 'audio_best']])
        self.assertEqual(record['data']['options']['urls'], [A, B, C])
        final = str(self.root / '甲.opus')
        self.assertEqual(self.store.find_download([urls.link_key(A)], 'audio_best')['path'], final, 'the renamed file is recorded')
        self.assertEqual(self.store.find_download(['youtube:abc'], 'audio_best')['path'], final)
        # A second job with the same link is skipped: the file is there.
        second = self.job([A])
        runner = engine.Runner(lambda kind, value: (self.store.download_event(second, kind, value)
                                                    if kind in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done') else None,
                                                    events.append((kind, value))), library=self.store)
        with mock.patch.object(runner, '_run_once', side_effect=AssertionError('the engine was started')):
            runner.download([A], engine.Options(self.root / 'out', skip_downloaded=True))
        done = events[-1][1]
        self.assertEqual((done['skipped'], done['success'], done['failed']), (1, 0, 0))
        self.assertEqual(self.store.job(second)['status'], 'completed')
        self.assertEqual(self.store.job(second)['data']['done'], [[A, 'audio_best']])


class FixRoundOneTests(StoreCase):
    """WP2 fix round 1: a job whose engine could not run (bughunt-4), titles that cannot be
    written as UTF-8 (bughunt-5), an unreadable archive record (conformance-6)."""

    def test_a_job_whose_engine_could_not_run_is_not_completed(self):
        job = self.job([A, B])
        for payload, status in ((dict(success=0, failed=0, cancelled=False, files=[], engine_error=True, pending_urls=[A, B]), 'partial'),
                                (dict(success=0, failed=0, cancelled=False, files=[], engine_error=True), 'partial'),
                                (dict(success=1, failed=0, cancelled=False, files=[], pending_urls=[B]), 'partial'),
                                (dict(success=2, failed=0, cancelled=False, files=[], engine_error=False, pending_urls=[]), 'completed')):
            self.store.download_event(job, 'done', payload)
            self.assertEqual(self.store.job(job)['status'], status, payload)
        # Through the real queue: the engine cannot start.
        job = self.job([A, B])
        runner = engine.Runner(lambda kind, value: self.store.download_event(job, kind, value)
                               if kind in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done') else None)
        with mock.patch.object(runner, '_run_once', side_effect=FileNotFoundError(2, 'missing engine')):
            runner.download([A, B], engine.Options(self.root / 'out'))
        record = self.store.job(job)
        self.assertEqual((record['status'], record['data']['done'], record['data']['result']['engine_error']),
                         ('partial', [], True))

    def test_a_title_with_a_lone_surrogate_is_stored(self):
        job = self.job([A, B])
        self.store.download_event(job, 'item', dict(url=A, key=A, format='audio_best', origin=A, multi=False))
        self.store.download_event(job, 'title', 'bad \ud83d title')
        path = self.media_file()
        self.store.download_event(job, 'media', dict(id='x\ud83d', extractor_key='Youtube', title='bad \ud83d title',
                                                     filepath=str(path)))
        self.store.download_event(job, 'item_done', dict(success=False, error='e \udc80'))
        record = self.store.job(job)
        self.assertEqual(library.title_parts(record), ('multi', 'bad ? title', 2))
        self.assertEqual(self.store.find_download(['youtube:x?'], 'audio_best')['title'], 'bad ? title')
        self.assertEqual(record['data']['errors'], [{'error': 'e ?', 'reason': None}])

    def test_an_unreadable_archive_record_is_set_aside_and_rebuilt(self):
        archive = library.Archive(self.root / '资料库', self.store)
        folder = archive.prepare(A, dict(title='甲'))
        record = folder / '资料信息.json'
        for broken in ('{ broken', '[1, 2]', '{"url": "x"}', '￾\x00garbage'):
            record.write_text(broken, encoding='utf-8')
            self.assertEqual(archive.prepare(A, dict(title='甲')), folder, broken)
            data = json.loads(record.read_text(encoding='utf-8'))
            self.assertEqual((data['url'], data['title'], data['files'], data['transcripts']), (A, '甲', [], []))
        aside = sorted(path.name for path in folder.iterdir() if path.name.startswith('资料信息.bad'))
        self.assertEqual(len(aside), 4, aside)
        self.assertIn('资料信息.bad.json', aside)
        self.assertEqual((folder / '资料信息.bad.json').read_text(encoding='utf-8'), '{ broken', 'the first one keeps the plain name')
        # A readable record is kept as it is.
        data = json.loads(record.read_text(encoding='utf-8'))
        data['files'] = ['媒体\\歌.opus']
        record.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        archive.prepare(A, dict(title='甲'))
        self.assertEqual(json.loads(record.read_text(encoding='utf-8'))['files'], ['媒体\\歌.opus'])
        self.assertEqual(len([path for path in folder.iterdir() if path.name.startswith('资料信息.bad')]), 4)


class MediaOnlyTests(StoreCase):
    """(2.0.0 S2-AMEND 4.3, Q3-6) The stored link kind is a code: a media-only subscription leaves the 'web'
    links out of its pending items and keeps the media and video-page links; one that follows every link
    keeps all three."""

    def check_page(self, media_only):
        sub = self.store.subscription('订阅', dict(urls=['https://example.org/list']), media_only=media_only)
        links = [dict(url=A, title='a', kind='media'), dict(url=B, title='b', kind='video_page'),
                 dict(url=C, title='c', kind='web')]
        page = dict(url='https://example.org/list', title='页', links=links)
        return sub, self.store.finish_subscription(sub, [page], True)

    def test_a_media_only_check_skips_the_web_links(self):
        sub, new = self.check_page(True)
        self.assertEqual((new, self.pending(sub)), (2, [A, B]))
        self.assertEqual([entry['kind'] for entry in self.store.pending(sub)], ['media', 'video_page'])

    def test_a_check_of_every_link_keeps_the_web_links(self):
        sub, new = self.check_page(False)
        self.assertEqual((new, self.pending(sub)), (3, [A, B, C]))

    def test_link_kind_gives_the_stored_codes(self):
        """(S2 review honesty-2) What the producer computes, not only literals: the static scan of
        test_formats_naming cannot see a Chinese kind made at run time."""
        import collector
        import i18n
        cases = [(('https://x.invalid/a.mp4',), 'media'), ((Y1,), 'video_page'),
                 (('https://example.org/about',), 'web'), (('https://example.org/about', True), 'media')]
        for arguments, kind in cases:
            self.assertEqual(collector.link_kind(*arguments), kind, arguments)
            self.assertIn(kind, i18n.GROUPS['link_kind'])

    def test_a_parsed_page_through_a_media_only_subscription(self):
        """(S2 review honesty-2) collector.parse_page (the real Scrapling selector) -> finish_subscription with
        media_only: the web link is not pending, and every kind stored in entries and pages is a code."""
        import collector
        import i18n
        from scrapling import Selector
        page_url = 'https://example.org/list'
        html = ('<html><head><title>List</title></head><body><a href="/about">About</a>'
                '<a href="/v/a.mp4">Clip</a><a href="' + Y1 + '">Video</a><video src="/m/b.webm"></video></body></html>')
        page = collector.parse_page(Selector(html, url=page_url), page_url)
        media, webm, about = 'https://example.org/v/a.mp4', 'https://example.org/m/b.webm', 'https://example.org/about'
        self.assertEqual({link['url'] for link in page['links']}, {about, media, Y1, webm})
        sub = self.store.subscription('订阅', dict(urls=[page_url]), media_only=True)
        self.assertEqual(self.store.finish_subscription(sub, [page], True), 3)
        self.assertEqual(sorted(self.pending(sub)), sorted([media, Y1, webm]))
        collect = self.store.create_job('collect', '', {'urls': [page_url]})
        self.store.collection_event(collect, 'collect_page', dict(page, _checkpoint=None))
        with self.store.db() as db:
            kinds = [json.loads(row[0])['kind'] for row in db.execute('SELECT data FROM entries')]
            kinds += [link['kind'] for row in db.execute('SELECT data FROM pages')
                      for link in json.loads(row[0])['links']]
        self.assertEqual(len(kinds), 7)
        self.assertTrue(set(kinds) <= set(i18n.GROUPS['link_kind']), kinds)
        self.assertEqual(sorted(set(kinds)), ['media', 'video_page', 'web'])


class CollectionFilterTests(unittest.TestCase):
    """(2.0.0 S2-AMEND 4.3, Q3-6) The collect page's first filter (media candidates) hides the links of kind
    'web' and shows the others; the second (every link) shows all.  CollectionUI.render_collection_links on a
    stand-in for the window (no Tk)."""

    def shown(self, choice):
        import collection_ui
        links = {url: dict(url=url, title='t', kind=kind)
                 for url, kind in ((A, 'media'), (B, 'web'), (C, 'video_page'), (Y1, 'dynamic_request'))}
        window = types.SimpleNamespace(collect_search=types.SimpleNamespace(get=lambda: ''),
                                       collect_filter=types.SimpleNamespace(get=lambda: choice),
                                       collected_links=links, collect_tree=mock.MagicMock(),
                                       collection_selection_changed=lambda: None)
        collection_ui.CollectionUI.render_collection_links(window)
        return [item['url'] for item in window.visible_collection_links]

    def test_the_media_filter_hides_the_web_links(self):
        import collection_ui
        self.assertEqual(self.shown(collection_ui.FILTERS[0]), [A, C, Y1])
        self.assertEqual(self.shown(collection_ui.FILTERS[1]), [A, B, C, Y1])


# ------------------------------------------------------------------ (2.0.0 S2 Q3) v2 runtime behaviour that
# the deleted converter tests and the old-data fixture used to reach (S2-AMEND 3.4); every record is written by
# this tree's own write paths (create_job, extend_job, download_event, subscription).

def resume_window():
    """LibraryUI.resume_launch / resume_options on a stand-in for the window (no Tk): the network and batch
    preferences of a fresh start (proxy '', cookies '', 稳健模式 on, 跳过已下载 off); a job without an archive
    folder never asks for archive contexts."""
    import library_ui

    class Window(library_ui.LibraryUI):
        pass

    window = Window.__new__(Window)
    window.proxy, window.cookies = types.SimpleNamespace(get=lambda: ''), types.SimpleNamespace(get=lambda: '')
    window.batch_safe, window.skip_downloaded = types.SimpleNamespace(get=lambda: True), \
        types.SimpleNamespace(get=lambda: False)
    window.collection_contexts = mock.Mock(side_effect=AssertionError('no archive folder: no contexts'))
    return window


class ResumeOptionsTests(StoreCase):
    """(S2-AMEND 3.4 Q3-1) 继续未完成 of a download job, every preset in three resolutions: job_downloads,
    resume_plan and the Options resume_launch / resume_options start it with, equal to the Options of the
    stored values; a subscription of the dynamic mode with a page limit is checked with its stored options."""

    def expected(self, values):
        options = engine.Options(output=Path(values['output']), mode=values['mode'], resolution=values['resolution'],
                                 playlist=values.get('playlist', False), naming=values.get('naming', 'title'),
                                 embed_cover=values.get('embed_cover', True), subtitles=values.get('subtitles', False),
                                 proxy='', cookies='', batch_safe=True, skip_downloaded=False, archive_contexts={})
        return options.to_dict(private=True)

    def test_every_preset_and_resolution_resumes_with_its_own_options(self):
        import formats
        import library_ui
        window = resume_window()
        presets = formats.MODES
        self.assertEqual(len(presets), 7)
        checked = 0
        for index, mode in enumerate(presets):
            for resolution in (formats.BEST, '2160p / 4K', '1080p'):
                with self.subTest(mode=mode, resolution=resolution):
                    base = dict(output=str(self.root / 'out'), mode=mode, resolution=resolution, playlist=True,
                                naming='clean', embed_cover=False)
                    other = dict(output=str(self.root / 'other'), mode=presets[(index + 1) % 7], resolution='1080p',
                                 subtitles=True)
                    own = dict(other, output=str(self.root / 'own'), naming='title_id')
                    job = self.job((A, B), **base)
                    self.store.extend_job(job, [C], {C: own})            # a new link with options of its own
                    self.store.extend_job(job, [A], {A: other})          # the same link in another format
                    base_key = formats.format_key(formats.by_id(mode), resolution)
                    self.store.download_event(job, 'item', {'url': B, 'format': base_key})
                    self.store.download_event(job, 'item_done', {'success': True, 'url': B})
                    stored = dict(base, proxy='')
                    other_key = formats.format_key(formats.by_id(other['mode']), '1080p')
                    self.assertNotEqual(other_key, base_key)
                    entries = library.job_downloads(self.data(job))
                    self.assertEqual(entries, [
                        {'url': A, 'format': base_key, 'options': stored, 'done': False},
                        {'url': B, 'format': base_key, 'options': stored, 'done': True},
                        {'url': C, 'format': other_key, 'options': own, 'done': False},
                        {'url': A, 'format': other_key, 'options': other, 'done': False}])
                    first, rest = library_ui.resume_plan(entries)
                    self.assertEqual([(entry['url'], entry['format']) for entry in first], [(A, base_key), (C, other_key)])
                    self.assertEqual([(entry['url'], entry['format']) for entry in rest], [(A, other_key)])
                    urls, options, overrides = window.resume_launch(self.data(job)['options'], first)
                    self.assertEqual(urls, [A, C])
                    self.assertEqual(options.to_dict(private=True), self.expected(stored))
                    self.assertEqual({url: value.to_dict(private=True) for url, value in overrides.items()},
                                     {C: self.expected(own)})
                    later = [window.resume_options(entry['options'], [entry['url']]).to_dict(private=True)
                             for entry in rest]
                    self.assertEqual(later, [self.expected(other)])
                    checked += 1
        self.assertEqual(checked, 21)
        window.collection_contexts.assert_not_called()

    def test_a_dynamic_subscription_with_a_page_limit_is_checked_with_its_options(self):
        """check_subscription: CollectOptions(**stored options), the proxy of this session, validate()."""
        import collector
        from dataclasses import asdict
        with mock.patch.object(collector, 'browser_available', lambda: True):
            saved = collector.CollectOptions(urls=['https://example.org/list'], dynamic=True, scrolls=2, selector='main a',
                                             max_pages=3).validate()
            key = self.store.subscription('Night', asdict(saved), media_only=False)
            row = next(row for row in self.store.subscriptions() if row['id'] == key)
            options = collector.CollectOptions(**row['data']['options'])
            options.proxy = ''
            checked = asdict(options.validate())
        self.assertEqual(checked, {'urls': ['https://example.org/list'], 'dynamic': True, 'scrolls': 2,
                                   'selector': 'main a', 'max_pages': 3, 'proxy': '', 'resume': None, 'job_id': '',
                                   'history_path': ''})
        self.assertEqual(row['data']['media_only'], False)


class BrokenRowTests(StoreCase):
    """(S2-AMEND 3.4 Q3-2) Rows this program never writes but a hand edit or a disk fault can leave in a v2
    library: what each reader does today, pinned (no exception, or the one it raises today).  Each row is made
    by the write path, then one value is replaced with UPDATE."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(library, 'alive', lambda pid: False)     # no job process is alive
        patcher.start()
        self.addCleanup(patcher.stop)

    def broken_job(self, change, links=(A,)):
        job = self.job(links, output=str(self.root), mode='audio_best')
        with self.store.db() as db:
            data = json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
            text = change(data) if callable(change) else change
            db.execute('UPDATE jobs SET data=? WHERE id=?', (text if isinstance(text, str) else json.dumps(text), job))
        return job

    def listed(self, job):
        return next(row for row in self.store.history() if row['id'] == job)

    def note(self, record):
        import library_ui
        return library_ui.LibraryUI.history_note_text(record)

    def test_job_records(self):
        import i18n
        import library_ui
        done = lambda n: i18n.t('library.history.downloaded', completed=n)      # noqa: E731
        kinds = i18n.label('job_kind', 'download')
        # data that is not JSON: listed by its title with empty data, its status as stored; job() raises
        job = self.broken_job('not json {')
        row = self.listed(job)
        self.assertEqual((row['data'], row['status']), ({}, 'running'))
        self.assertEqual(library_ui.history_row(row)[:3], (kinds, '', i18n.label('job_status', 'running')))
        self.assertEqual(self.note(dict(row, pages=[])), done(0))
        self.assertEqual(library.job_downloads(row['data']), [])
        self.assertRaises(json.JSONDecodeError, self.store.job, job)
        # data that is a JSON list: the same in the list; job() returns the list, the note raises
        job = self.broken_job([1, 2])
        row = self.listed(job)
        self.assertEqual((row['data'], row['status']), ({}, 'running'))
        self.assertEqual(self.store.job(job)['data'], [1, 2])
        self.assertEqual(library.job_downloads(self.store.job(job)['data']), [])
        self.assertRaises(AttributeError, self.note, self.store.job(job))
        # options that are not an object: no downloads, the note counts none, the title is empty
        job = self.broken_job(lambda data: dict(data, options='x'))
        row = self.listed(job)
        self.assertEqual(row['status'], 'interrupted')
        self.assertEqual(library_ui.history_row(row)[:3], (kinds, '', i18n.label('job_status', 'interrupted')))
        self.assertEqual(library.job_downloads(self.data(job)), [])
        self.assertEqual(self.note(self.store.job(job)), done(0))
        # urls that are not a list: the title ignores them; job_downloads walks the text (one entry a character)
        job = self.broken_job(lambda data: dict(data, options=dict(data['options'], urls='ab')))
        self.assertEqual(library_ui.history_row(self.listed(job))[1], '')
        self.assertEqual([(entry['url'], entry['format']) for entry in library.job_downloads(self.data(job))],
                         [('a', 'audio_best'), ('b', 'audio_best')])
        self.assertEqual(self.note(self.store.job(job)), done(0))
        # an error that is not an object: the note explains the last one as a line of the engine
        for errors, last in (([{'error': 'x', 'reason': None}, 7], '7'), ([7, 'boom'], 'boom')):
            job = self.broken_job(lambda data: dict(data, errors=errors))
            shown = messages.show(engine.explain_error(last, bot_check=False, stale=False))
            self.assertEqual(self.note(self.store.job(job)), done(0) + i18n.t('library.history.last_error', error=shown))
            self.assertEqual(self.note(dict(self.listed(job), pages=[])), self.note(self.store.job(job)))
        # 'done' items that are not pairs are skipped; a pair of two lists counts as one finished link
        job = self.broken_job(lambda data: dict(data, done=[['x'], 'y', [A, 'audio_best', 'z'], [[1], [2]]]))
        self.assertEqual([entry['done'] for entry in library.job_downloads(self.data(job))], [False])
        self.assertEqual(self.note(self.store.job(job)), done(1))
        self.assertEqual(library_ui.history_row(self.listed(job))[1], urls.display(A))

    def test_subscriptions_pages_entries_and_archives(self):
        import library_ui
        sub = self.subscription_with(A + '.mp4')
        self.assertEqual(library_ui.subscription_row(self.store.subscriptions()[0])[3], 1)
        # a pending item that is not JSON: the count is right, the list raises
        with self.store.db() as db:
            db.execute('UPDATE entries SET data=?', ('not json',))
        self.assertEqual([row['pending'] for row in self.store.subscriptions()], [1])
        self.assertRaises(json.JSONDecodeError, self.store.pending, sub)
        # a subscription record that is not JSON: the list of subscriptions raises
        with self.store.db() as db:
            db.execute('UPDATE subscriptions SET data=?', ('not json',))
        self.assertRaises(json.JSONDecodeError, self.store.subscriptions)
        # a page whose links are not a list: job() returns it as stored; contexts raises where it reads the
        # row (the row holds the link: the instr way), skips it on the row-by-row way, never reads it on a miss
        media = A + '.mp4'
        collect = self.store.create_job('collect', '', {'urls': [A]})
        self.store.collection_event(collect, 'collect_page', {'url': A, 'title': 't', 'text': '', 'markdown': '',
                                                              'next': None, 'links': [], '_checkpoint': None})
        with self.store.db() as db:
            db.execute('UPDATE pages SET data=?', (json.dumps({'url': A, 'title': 't', 'links': {'u': media}}),))
        self.assertEqual(self.store.job(collect)['pages'], [{'url': A, 'title': 't', 'links': {'u': media}}])
        self.assertRaises(TypeError, self.store.contexts, [media])
        self.assertEqual(self.store.contexts([media, B + '1', B + '2', B + '3', B + '4']), {})
        self.assertEqual(self.store.contexts([C]), {})
        with self.store.db() as db:
            db.execute('UPDATE pages SET data=?', (json.dumps({'url': A, 'title': 't', 'links': None, 'u': media}),))
        self.assertRaises(TypeError, self.store.contexts, [media, B + '1', B + '2', B + '3', B + '4'])
        # an archive row whose data is not JSON: the archive list raises
        self.store.archive_index(str(self.root / 'folder'), {'url': A, 'source': A, 'title': 't', 'uploader': '',
                                                              'files': [], 'transcripts': []})
        self.assertEqual(len(self.store.archives()), 1)
        with self.store.db() as db:
            db.execute('UPDATE archives SET data=?', ('not json',))
        self.assertRaises(json.JSONDecodeError, self.store.archives)


class TitlePartsTests(StoreCase):
    """(S2-AMEND 3.4 Q3-3) Every structure of library.title_parts as the write paths make it, and
    library_ui.history_title of each in Chinese and in English, text for text."""

    def test_every_title_structure_in_both_languages(self):
        import library_ui
        opts = {'urls': ['https://example.org/list']}
        named = self.store.subscription('Night', opts)
        untitled = self.store.subscription('', opts)
        jobs = {
            'subscription': self.store.create_job('collect', 'Night', opts, named, via='subscription'),
            'untitled': self.store.create_job('collect', '', opts, untitled, via='subscription'),
            'multi': self.job((A, B, C)),
            'plain': self.job((A,)),
            'link': self.job((A,)),
            'multi_link': self.job((A, B)),
            'collect': self.store.create_job('collect', 'Mine', opts),
            'empty': self.job(()),
        }
        self.store.download_event(jobs['multi'], 'title', '夜曲')
        self.store.download_event(jobs['multi'], 'title', 'second')          # only the first names the job
        self.store.download_event(jobs['plain'], 'title', '晴天')
        parts = {name: self.title(job) for name, job in jobs.items()}
        self.assertEqual(parts, {
            'subscription': ('subscription', 'Night'), 'untitled': ('subscription', ''),
            'multi': ('multi', '夜曲', 3), 'plain': ('plain', '晴天'), 'link': ('link', A),
            'multi_link': ('multi_link', A, 2), 'collect': ('plain', 'Mine'), 'empty': ('plain', '')})
        shown = {lang: {'subscription': text('value.job_title.subscription', name='Night'),
                        'untitled': text('value.job_title.subscription', name=text('value.subscription.untitled')),
                        'multi': text('value.job_title.multi', title='夜曲', count=3),
                        'multi_link': text('value.job_title.multi', title='example.invalid/a', count=2)}
                 for lang, text in (('zh', zh_t), ('en', en_t))}
        same = {'plain': '晴天', 'link': 'example.invalid/a', 'collect': 'Mine', 'empty': ''}
        for lang in ('zh', 'en'):
            with _sandbox.language(lang):
                titles = {name: library_ui.history_title(self.store.job(job)) for name, job in jobs.items()}
                listed = {row['id']: library_ui.history_title(row) for row in self.store.history()}
            self.assertEqual(titles, dict(shown[lang], **same), lang)
            self.assertEqual({name: listed[job] for name, job in jobs.items()}, titles, lang)


# ------------------------------------------------------------------ (2.0.0 S2 Q2) files that are not version 2

CJK = re.compile('[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]')
BAD_NAME = re.compile(r'^library\.bad-\d{8}-\d{6}(-\d+)?\.sqlite3$')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def en(value):
    with _sandbox.language('en'):
        return messages.show(value)


def old_library(path, version=0, without_downloads=False):
    """A library as another version left it: the tables (1.5.1: without downloads), one job, user_version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(library.SCHEMA)
    if without_downloads:
        db.execute('DROP TABLE downloads')
    db.execute("INSERT INTO jobs VALUES ('old', 'download', '旧任务', 'done', 1.0, '{\"urls\": []}')")
    db.execute(f'PRAGMA user_version = {version}')
    db.commit()
    db.close()
    return path


def user_version(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('PRAGMA user_version').fetchone()[0], [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    finally:
        db.close()


class ForeignLibraryTests(unittest.TestCase):
    """S2-AMEND.md 2.1 and 2.5 items 1-10 (PLAN 3.10): a library.sqlite3 that is not version 2 (another
    version wrote it, it is damaged) is renamed to library.bad-<time>.sqlite3 in its folder, byte for
    byte, and a new empty version-2 library starts in its place; the warning says so.  What only stops
    this session (a lock, a file in use, a transaction of another connection) never renames the file."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'library.sqlite3'
        pause = mock.patch.object(paths, 'REPLACE_PAUSE', 0.001)
        pause.start()
        self.addCleanup(pause.stop)

    def kept(self, folder=None):
        return sorted((folder or self.root).glob('library.bad-*.sqlite3'))

    def assert_new(self, store, path=None):
        self.assertEqual(store.path, (path or self.path).resolve())
        self.assertEqual(store.history(), [])
        self.assertEqual(user_version(store.path), (2, ['archives', 'downloads', 'entries', 'jobs', 'pages', 'subscriptions']))

    def assert_kept_aside(self, warning, before, problem='core.library.not_v2', folder=None):
        """warning is core.library.kept_aside naming the renamed file, whose bytes are ``before``."""
        kept = self.kept(folder)
        self.assertEqual(len(kept), 1)
        self.assertRegex(kept[0].name, BAD_NAME)
        self.assertEqual(sha(kept[0]), before)
        self.assertIsInstance(warning, Message)
        self.assertEqual((warning.key, warning.params['target']), ('core.library.kept_aside', kept[0].name))
        if problem is None:
            self.assertNotIsInstance(warning.params['problem'], Message)
            return warning.params['problem']
        self.assertEqual(warning.params['problem'].key, problem)
        target = kept[0].name
        self.assertEqual(messages.zh(warning), zh_t('core.library.kept_aside', problem=zh_t(problem), target=target))
        self.assertEqual(en(warning), en_t('core.library.kept_aside', problem=en_t(problem), target=target))
        self.assertEqual(CJK.findall(en(warning).replace(target, '')), [])

    def assert_temporary(self, store, warning):
        self.assertEqual(warning.key, 'core.library.temporary')
        self.assertTrue(store.path.parent.name.startswith('ytdlp-history-'))
        self.assertNotEqual(store.path, self.path.resolve())

    def test_1_a_library_of_an_older_version_is_kept_aside_and_a_new_one_starts(self):
        before = sha(old_library(self.path))
        store, warning = library.open_library(self.path)
        self.assert_new(store)
        self.assert_kept_aside(warning, before)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), sorted(['library.sqlite3', self.kept()[0].name]))
        # The next start opens the new library as it is.
        store.create_job('download', '新任务', {'urls': []})
        again, warning = library.open_library(self.path)
        self.assertEqual((again.path, warning, len(again.history()), len(self.kept())), (store.path, '', 1, 1))

    def test_1b_the_library_beside_the_program_stays_beside_the_program(self):
        """The owner's library is data\\library.sqlite3 (paths.catalog_path); the new one is made there too,
        not in the state folder (S2-AMEND.md 9, item 1)."""
        app, state = self.root / 'app', self.root / 'state'
        for patcher in (mock.patch.object(paths, 'APP_DIR', app), _sandbox.state_patch(state)):
            patcher.start()
            self.addCleanup(patcher.stop)
        beside = old_library(paths.data_dir() / 'library.sqlite3')
        before = sha(beside)
        other = Library(state / 'library.sqlite3')
        other.create_job('download', '状态目录的任务', {'urls': []})
        other_bytes = sha(other.path)
        store, warning = library.open_library()
        self.assert_new(store, beside)
        self.assert_kept_aside(warning, before, folder=paths.data_dir())
        self.assertEqual(sha(other.path), other_bytes, 'the library of the state folder is not touched')
        again, warning = library.open_library()
        self.assertEqual((again.path, warning), (beside.resolve(), ''))

    def test_2_a_library_of_1_5_1_and_one_of_a_later_version_are_kept_aside(self):
        for name, make in (('1.5.1', lambda: old_library(self.path, without_downloads=True)),
                           ('version 3', lambda: old_library(self.path, version=3))):
            with self.subTest(name):
                for path in self.root.iterdir():
                    path.unlink()
                before = sha(make())
                store, warning = library.open_library(self.path)
                self.assert_new(store)
                self.assert_kept_aside(warning, before)

    def test_3_a_damaged_file_is_kept_aside(self):
        real = old_library(self.root / 'real.sqlite3', version=2).read_bytes()
        for name, content, text in (('random bytes', os.urandom(8192), 'file is not a database'),
                                    ('cut to 100 bytes', real[:100], 'database disk image is malformed')):
            with self.subTest(name):
                for path in self.kept():
                    path.unlink()
                self.path.write_bytes(content)
                store, warning = library.open_library(self.path)
                self.assert_new(store)
                self.assertEqual(self.assert_kept_aside(warning, hashlib.sha256(content).hexdigest(), problem=None), text)
                self.assertEqual(messages.zh(warning),
                                 zh_t('core.library.kept_aside', problem=text, target=warning.params['target']))
                self.path.unlink()

    def test_4_an_empty_file_and_a_library_without_tables_are_new_libraries(self):
        def without_tables():
            db = sqlite3.connect(self.path)
            db.executescript('CREATE TABLE t(x); DROP TABLE t;')
            db.close()
        for name, make in (('0 bytes', lambda: self.path.write_bytes(b'')), ('no table', without_tables)):
            with self.subTest(name):
                if self.path.exists():
                    self.path.unlink()
                make()
                self.assertEqual(user_version(self.path), (0, []))
                store, warning = library.open_library(self.path)
                self.assertEqual(warning, '')
                self.assert_new(store)
                self.assertEqual(self.kept(), [])

    def test_4b_the_tables_and_the_version_are_made_in_one_transaction(self):
        """An exit half-way through making a new library leaves no table behind, so that the next start
        does not take the file for a library of another version (S2-AMEND.md 9, item 7)."""
        with mock.patch.object(library, 'SCHEMA', library.SCHEMA + 'CREATE TABLE jobs(x);'):
            with self.assertRaises(sqlite3.Error):
                Library(self.path)
        self.assertEqual(user_version(self.path), (0, []))
        store, warning = library.open_library(self.path)
        self.assertEqual((warning, self.kept()), ('', []))
        self.assert_new(store)

    def test_5_a_version_2_library_is_opened_as_it_is(self):
        store = Library(self.path)
        job = store.create_job('download', '任务', {'urls': [A]})
        before = sha(self.path)
        opened, warning = library.open_library(self.path)
        self.assertEqual((warning, self.kept(), [row['id'] for row in opened.history()]), ('', [], [job]))
        self.assertEqual(sha(self.path), before)

    def test_6_a_file_in_use_is_left_as_it_is_for_this_session(self):
        before = sha(old_library(self.path))
        with open(self.path, 'rb'):         # Windows gives no delete sharing: the rename fails
            store, warning = library.open_library(self.path)
        self.assert_temporary(store, warning)
        self.assertEqual((sha(self.path), [path.name for path in self.root.iterdir()]), (before, ['library.sqlite3']))
        with mock.patch.object(paths, 'set_aside', side_effect=PermissionError(13, 'in use')):
            store, warning = library.open_library(self.path)
        self.assert_temporary(store, warning)
        self.assertEqual((sha(self.path), [path.name for path in self.root.iterdir()]), (before, ['library.sqlite3']))
        store, warning = library.open_library(self.path)     # the handle is closed: the next start
        self.assert_new(store)
        self.assert_kept_aside(warning, before)

    def test_7_journals_and_a_connection_in_a_transaction(self):
        """(S2 review F4, DATA.md section 6) A journal SQLite does not take for hot (here 512 zero bytes) holds
        nothing of the file: the library is renamed as it is, not left for a temporary one at every start.
        A hot journal of a crashed writer is rolled back by SQLite first: the kept file is the committed
        state.  Another connection in a transaction: Windows refuses the rename, a temporary library serves."""
        before = sha(old_library(self.path))
        journal = self.root / 'library.sqlite3-journal'
        journal.write_bytes(bytes(512))
        store, warning = library.open_library(self.path)
        self.assert_new(store)
        self.assert_kept_aside(warning, before)
        store.create_job('download', 'x', {'urls': [A]})
        self.assertEqual(len(store.history()), 1, 'the new library works beside the old journal')
        for path in self.kept():
            path.unlink()
        self.path.unlink()
        journal.unlink(missing_ok=True)
        before = sha(old_library(self.path))
        crash = ("import os, sqlite3, sys\n"
                 "db = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
                 "db.execute('PRAGMA cache_size=1')\n"
                 "db.execute('BEGIN IMMEDIATE')\n"
                 "for n in range(200):\n"
                 "    db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)', ('c%d' % n, 'download', 'x' * 500, 'done', 1.0, '{}'))\n"
                 "os._exit(1)\n")
        subprocess.run([sys.executable, '-I', '-c', crash, str(self.path)], timeout=60,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertTrue(journal.exists(), 'the crashed writer left a hot journal')
        self.assertNotEqual(sha(self.path), before, 'the crashed writer changed the file')
        store, warning = library.open_library(self.path)
        self.assert_new(store)
        self.assert_kept_aside(warning, before)
        self.assertFalse(journal.exists())
        for path in self.kept():
            path.unlink()
        self.path.unlink()
        before = sha(old_library(self.path))
        other = sqlite3.connect(self.path, isolation_level=None)
        try:
            other.execute('BEGIN IMMEDIATE')
            other.execute("INSERT INTO jobs VALUES ('more', 'download', 'x', 'done', 2.0, '{}')")
            self.assertTrue(journal.exists())
            store, warning = library.open_library(self.path)
            self.assert_temporary(store, warning)
            self.assertEqual(self.kept(), [])
        finally:
            other.execute('ROLLBACK')
            other.close()
        self.assertEqual(sha(self.path), before)

    def test_8_a_taken_name_is_never_replaced(self):
        stamp = '20261119-120000'
        taken = self.root / f'library.bad-{stamp}.sqlite3'
        taken.write_bytes(b'an older one')
        before = sha(old_library(self.path))
        with mock.patch.object(paths.time, 'strftime', return_value=stamp):
            store, warning = library.open_library(self.path)
        self.assertEqual(taken.read_bytes(), b'an older one')
        self.assertEqual(warning.params['target'], f'library.bad-{stamp}-2.sqlite3')
        self.assertEqual(sha(self.root / f'library.bad-{stamp}-2.sqlite3'), before)
        self.assert_new(store)

    def test_9_a_lock_of_another_program_never_renames(self):
        before = sha(old_library(self.path))
        lock = sqlite3.connect(self.path, isolation_level=None)
        lock.execute('BEGIN EXCLUSIVE')
        try:
            with library.worker_wait(0.1):
                store, warning = library.open_library(self.path)
            self.assert_temporary(store, warning)
            saved = library._ui_thread
            library.set_ui_thread()
            try:
                store, warning = library.open_library(self.path)
            finally:
                library._ui_thread = saved
                library._ui_quiet_until = 0.0
            self.assertEqual((store.path, warning), (self.path.resolve(), ''), '1.7.0 U1: a lock at the start is used')
        finally:
            lock.execute('ROLLBACK')
            lock.close()
        with self.assertRaises(library.NotV2) as caught:
            store.history()
        self.assertIsInstance(caught.exception, sqlite3.OperationalError)
        self.assertFalse(library.is_busy(caught.exception))
        self.assertEqual(messages.message_of(caught.exception).key, 'core.library.not_v2')
        self.assertEqual((sha(self.path), self.kept()), (before, []))

    def test_10_a_new_library_that_cannot_be_made_after_the_rename(self):
        before = sha(old_library(self.path))
        real, calls = library._opened, []

        def opened(path):
            calls.append(path)
            if len(calls) == 2:
                raise sqlite3.OperationalError('disk I/O error')
            return real(path)

        with mock.patch.object(library, '_opened', side_effect=opened):
            store, warning = library.open_library(self.path)
        self.assertEqual(calls, [self.path, self.path.resolve()])
        self.assert_temporary(store, warning)
        kept = warning.params['error']
        self.assertEqual((kept.key, kept.params['target']), ('core.library.kept_aside', self.kept()[0].name))
        self.assertEqual(sha(self.kept()[0]), before)
        self.assertFalse(self.path.exists())


def old_record(folder, **more):
    """资料信息.json as 1.8.1 wrote it: no version, a msgs side table, the Chinese title of a web link."""
    data = dict(url=A, source=A, title='网页', created=1.0, files=['a.mp4'], transcripts=[],
                msgs={'title': {'k': 'value.link_kind.web'}}, **more)
    (folder / '资料信息.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return (folder / '资料信息.json').read_bytes()


class ForeignArchiveTests(unittest.TestCase):
    """S2-AMEND.md 2.3 and 2.5 items 14-17: 资料信息.json carries "version": 2; prepare sets aside a record
    that is not version 2 (or cannot be read) as 资料信息.bad.json and says so; media / attach refuse it
    and write nothing."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'archive'
        self.lines = []
        self.archive = Archive(self.root, log=self.lines.append)
        self.folder = Archive(self.root).prepare(A, dict(title='第一次', source=A))
        (self.folder / 'a.mp4').write_bytes(b'media')

    def record(self):
        return json.loads((self.folder / '资料信息.json').read_text(encoding='utf-8'))

    def test_14_a_record_of_an_older_version_is_kept_aside_and_made_again(self):
        before = old_record(self.folder)
        with _sandbox.language('en'):
            self.assertEqual(self.archive.prepare(A, dict(title='This time', source=A)), self.folder)
        self.assertEqual((self.folder / '资料信息.bad.json').read_bytes(), before)
        record = self.record()
        self.assertEqual((record['version'], record['title'], record['files']), (2, 'This time', []))
        self.assertNotIn('msgs', record)
        self.assertEqual((self.folder / 'a.mp4').read_bytes(), b'media', 'the media stay')
        self.assertEqual([(line.key, line.params['target'], line.params['folder']) for line in self.lines],
                         [('core.archive.kept_aside', '资料信息.bad.json', str(self.folder))])
        readme = (self.folder / '阅读说明.txt').read_text(encoding='utf-8-sig')
        self.assertEqual(CJK.findall(readme), [], readme)
        self.assertEqual(CJK.findall(en(self.lines[0]).replace('资料信息.bad.json', '')), [])

    def test_15_a_damaged_record_is_kept_aside_with_the_same_line(self):
        (self.folder / '资料信息.json').write_text('{"url": ', encoding='utf-8')
        self.archive.prepare(A, dict(title='再次', source=A))
        self.assertEqual((self.folder / '资料信息.bad.json').read_text(encoding='utf-8'), '{"url": ')
        self.assertEqual((self.record()['version'], self.record()['title']), (2, '再次'))
        self.assertEqual([line.key for line in self.lines], ['core.archive.kept_aside'])

    def test_16_a_version_2_record_is_read_and_written_with_its_version(self):
        self.assertEqual(self.record()['version'], 2)
        media = self.folder / 'a.mp4'
        self.archive.media(self.folder, {'filepath': str(media), 'title': '媒体'})
        self.archive.prepare(A, dict(title='再次', source=A))
        record = self.record()
        self.assertEqual((record['version'], record['files'], record['title']), (2, ['a.mp4'], '再次'))
        self.assertEqual((self.lines, list(self.folder.glob('资料信息.bad*'))), ([], []))
        stored = list(json.loads(row['data']) for row in self.index_rows(record))
        self.assertTrue(all('version' not in data for data in stored))

    def index_rows(self, record):
        store = Library(Path(self.directory.name) / 'library.sqlite3')
        Archive(self.root, store).save(self.folder, library._archive_record(self.record()))
        with store.db() as db:
            return db.execute('SELECT data FROM archives').fetchall()

    def test_16b_media_and_attach_refuse_a_record_that_is_not_version_2(self):
        before = old_record(self.folder)
        source = Path(self.directory.name) / '稿.txt'
        source.write_text('稿', encoding='utf-8')
        for call in (lambda: self.archive.media(self.folder, {'filepath': str(self.folder / 'a.mp4')}),
                     lambda: self.archive.attach(self.folder, source)):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(messages.message_of(caught.exception).key, 'core.archive.not_v2')
            self.assertEqual((self.folder / '资料信息.json').read_bytes(), before)
            self.assertEqual(list(self.folder.glob('资料信息.bad*')), [])
        self.assertFalse((self.folder / '转写稿').exists())
        self.assertEqual(self.lines, [])

    def test_17_a_record_that_cannot_be_renamed_fails_this_link_only(self):
        before = old_record(self.folder)
        with mock.patch.object(library.os, 'rename', side_effect=PermissionError(13, 'in use')):
            with self.assertRaises(OSError):
                self.archive.prepare(A, dict(title='再次', source=A))
        self.assertEqual((self.folder / '资料信息.json').read_bytes(), before)
        self.assertEqual((list(self.folder.glob('资料信息.bad*')), self.lines), ([], []))


if __name__ == '__main__':
    unittest.main()
