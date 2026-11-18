"""The history store of 1.6.0 (WP2 step H): the downloads table, BEGIN IMMEDIATE for
read-modify-write, the new job helpers, and download_event for the events the engine
gained.  Every database is a throw-away file in the temp folder; no process is started.
"""
import _sandbox
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import engine
import library
import urls
from library import Library

A, B, C = ('https://example.invalid/' + name for name in 'abc')
Y1 = 'https://www.youtube.com/watch?v=AAA'
BACKUP = _sandbox.release_src('1.5.1')
# The schema as library.py of 1.5.1 creates it (lines 39-49 of the backup).
SCHEMA_OF_1_5_1 = '''
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, title TEXT,
        status TEXT, updated REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS pages(job TEXT, url TEXT, data TEXT, PRIMARY KEY(job,url));
    CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, name TEXT,
        enabled INTEGER, interval INTEGER, next_check REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS entries(subscription TEXT, key TEXT, pending INTEGER,
        data TEXT, PRIMARY KEY(subscription,key));
    CREATE TABLE IF NOT EXISTS archives(folder TEXT PRIMARY KEY, updated REAL, data TEXT);
'''


def old_library_module():
    """library.py of 1.5.1 under another name (its ``from engine import APP_DIR`` finds the current engine)."""
    name = 'library_1_5_1'
    spec = importlib.util.spec_from_file_location(name, BACKUP / 'library.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Library(self.root / 'library.sqlite3')

    def job(self, links=(A,), **options):
        return self.store.create_job('download', f'{len(links)}条 · 音频 · 原始最佳', dict(urls=list(links), **options))

    def data(self, job):
        return self.store.job(job)['data']

    def media_file(self, name='歌.opus'):
        path = self.root / name
        path.write_bytes(b'media')
        return path

    def subscription_with(self, *links):
        """A subscription whose listed links are all pending."""
        sub = self.store.subscription('订阅', dict(urls=['https://example.org/list']))
        page = dict(url='https://example.org/list', title='页', links=[dict(url=link, title='t', kind='媒体地址') for link in links])
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

    def test_a_database_of_1_5_1_opens_and_gains_the_table(self):
        path = self.root / 'old.sqlite3'
        db = sqlite3.connect(path)
        db.executescript(SCHEMA_OF_1_5_1)
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', ('j1', 'download', '1条 · 音频 · 原始最佳', 'partial', 1700000000.0,
                                                           json.dumps({'options': {'urls': [A], 'output': 'D:\\x', 'mode': '音频 · 原始最佳'},
                                                                       'subscription': None, 'checkpoint': None, 'errors': [], 'owner': 0,
                                                                       'worker': None, 'completed': [], 'files': []}, ensure_ascii=False)))
        db.commit()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        self.assertNotIn('downloads', tables)
        store = Library(path)
        with store.db() as db:
            self.assertIn('downloads', {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual([row[1] for row in db.execute('PRAGMA table_info(downloads)')],
                             ['key', 'format', 'url', 'title', 'path', 'finished', 'job'])
        self.assertEqual([row['title'] for row in store.history()], ['1条 · 音频 · 原始最佳'])
        self.assertIsNone(store.find_download(['youtube:AAA'], 'audio_best'))
        # The old job takes the new events.
        store.download_event('j1', 'item', dict(index=1, total=1, url=A, key=urls.link_key(A), format='audio_best', origin=A, multi=False))
        store.download_event('j1', 'media', dict(index=1, id='abc', extractor_key='Youtube', title='歌', filepath=str(self.media_file())))
        self.assertTrue(store.find_download(['youtube:abc'], 'audio_best')['exists'])

    def test_1_5_1_reads_a_database_that_1_6_0_has_written(self):
        """Rollback: the old Library opens the file, lists the jobs and resumes its own kind of job."""
        job = self.job([A, B])
        self.store.download_event(job, 'item', dict(index=1, total=2, url=A, key=urls.link_key(A), format='audio_best', origin=A, multi=False))
        self.store.download_event(job, 'title', '歌')
        self.store.download_event(job, 'media', dict(index=1, id='abc', extractor_key='Youtube', title='歌', filepath=str(self.media_file())))
        self.store.download_event(job, 'queued', dict(urls=[C], first_index=3, total=3, overrides={C: {'mode': '视频 · MP4 兼容'}}))
        self.store.download_event(job, 'item_done', dict(index=1, success=True, cancelled=False, files=1, bot_check=False, url=A, error=''))
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, bot_check=False, url=B,
                                                         error='[x] b: HTTP Error 403'))
        self.store.download_event(job, 'done', dict(success=1, failed=1, cancelled=False, files=[], blocked='', total=3))
        old = old_library_module().Library(self.store.path)
        rows = old.history()
        self.assertEqual([(row['id'], row['status'], row['title']) for row in rows], [(job, 'partial', '歌 等 2 条')])
        record = old.job(job)
        self.assertEqual(record['data']['completed'], [A])
        self.assertEqual(record['data']['options']['urls'], [A, B, C])
        self.assertEqual(record['data']['errors'][-1][:130], '[x] b: HTTP Error 403', 'what its history note shows')
        # What resume_history of 1.5.1 computes, and its own events on the same row.
        self.assertEqual([url for url in record['data']['options']['urls'] if url not in record['data']['completed']], [B, C])
        old.download_event(job, 'item', dict(url=B))
        old.download_event(job, 'item_done', dict(success=True))
        self.assertEqual(self.store.job(job)['data']['completed'], [A, B])
        self.assertEqual(old.subscriptions(), [])
        self.assertEqual(old.archives(), [])


class JobHelperTests(StoreCase):
    def test_extend_job(self):
        job = self.job([A])
        self.store.extend_job(job, [B, A, C, B])
        self.assertEqual(self.data(job)['options']['urls'], [A, B, C])
        self.assertNotIn('overrides', self.data(job)['options'])
        mp3 = {'output': 'D:\\音乐', 'mode': '音频 · MP3 320K'}
        self.store.extend_job(job, ['https://example.invalid/d'], {'https://example.invalid/d': mp3})
        self.store.extend_job(job, [], {B: {'mode': '视频 · MP4 兼容'}})
        options = self.data(job)['options']
        self.assertEqual(options['urls'], [A, B, C, 'https://example.invalid/d'])
        self.assertEqual(options['overrides'], {'https://example.invalid/d': mp3, B: {'mode': '视频 · MP4 兼容'}})
        self.store.extend_job(job, [], None)
        self.store.extend_job(job, [], {})
        self.assertEqual(self.data(job)['options']['overrides'], options['overrides'])
        # A job record without a list of links (a collect job of an old version) gets one.
        bare = self.store.create_job('download', 'x', {})
        self.store.extend_job(bare, [A])
        self.assertEqual(self.data(bare)['options']['urls'], [A])

    def test_rename_job(self):
        job = self.job([A])
        before = self.store.job(job)
        self.store.rename_job(job, '新标题')
        after = self.store.job(job)
        self.assertEqual(after['title'], '新标题')
        self.assertEqual((after['status'], after['data']), (before['status'], before['data']))

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
            'rename_job': lambda: self.store.rename_job(job, 't'),
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
        self.assertEqual(record['data']['completed'], [A])
        self.assertEqual(record['data']['files'], ['D:\\a.opus'])
        self.assertEqual(record['data']['current'], A)
        self.assertEqual(record['data']['errors'], [])
        self.assertEqual(record['title'], '2条 · 音频 · 原始最佳', 'no title event, no new title')
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
                            ('queued', dict(urls=[B], first_index=2, total=2, overrides={B: {'mode': '音频 · MP3 320K'}})),
                            ('title', '歌')):
            started = time.monotonic()
            self.store.download_event(job, kind, value)
            self.assertLess(time.monotonic() - started, 1, kind)
        for key in ('youtube:abc', urls.link_key(A)):
            row = self.store.find_download([key], 'audio_best')
            self.assertEqual((row['url'], row['title'], row['path'], row['job'], row['exists']), (A, '歌', str(path), job, True), key)
        data = self.data(job)
        self.assertEqual(data['options']['urls'], [A, B])
        self.assertEqual(data['options']['overrides'], {B: {'mode': '音频 · MP3 320K'}})
        self.assertEqual(self.store.job(job)['title'], '歌 等 2 条')
        self.assertIs(data['titled'], True)

    def test_title_names_the_job_once(self):
        single = self.job([A])
        self.store.download_event(single, 'title', '第一首')
        self.store.download_event(single, 'title', '第二首')
        self.assertEqual(self.store.job(single)['title'], '第一首')
        several = self.job([A, B, C])
        for bad in ('', '   ', None, 17, {'title': 'x'}):
            self.store.download_event(several, 'title', bad)
        self.assertEqual(self.store.job(several)['title'], '3条 · 音频 · 原始最佳', 'an empty title names nothing')
        self.store.download_event(several, 'title', '甲')
        self.assertEqual(self.store.job(several)['title'], '甲 等 3 条')
        # The count is the job's at that moment: links that joined before the first title count.
        grown = self.job([A])
        self.store.download_event(grown, 'queued', dict(urls=[B], first_index=2, total=2))
        self.store.download_event(grown, 'title', '乙')
        self.assertEqual(self.store.job(grown)['title'], '乙 等 2 条')

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
        self.assertEqual(data['completed'], [Y1])
        self.assertEqual(self.pending(sub), [B], 'the link that is already on disk leaves the pending list')
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, skipped='downloaded',
                                                         url=Y1, origin=origin))
        self.assertEqual(self.data(job)['completed'], [Y1], 'once')
        # Blocked by a bot check: nothing is completed.
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0, bot_check=True,
                                                         skipped='bot_check', url=B, origin=B))
        self.assertEqual(self.data(job)['completed'], [Y1])
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
        self.assertEqual(self.data(job)['completed'], [Y1])

    def test_errors_are_kept_for_the_history_note(self):
        job = self.job([A])
        self.store.download_event(job, 'item', dict(url=A))
        self.store.download_event(job, 'item_done', dict(success=False, error='[x] a: HTTP Error 403: Forbidden'))
        self.store.download_event(job, 'item_done', dict(success=False, error=''))
        self.store.download_event(job, 'item_done', dict(success=False))
        self.assertEqual(self.data(job)['errors'], ['[x] a: HTTP Error 403: Forbidden'])
        for number in range(30):
            self.store.download_event(job, 'item_done', dict(success=False, error=f'error {number}'))
        errors = self.data(job)['errors']
        self.assertEqual(len(errors), 20, 'the last 20')
        self.assertEqual((errors[0], errors[-1]), ('error 10', 'error 29'))
        self.assertTrue(all(isinstance(error, str) for error in errors), '1.5.1 slices the last one')

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
        self.assertEqual(record['title'], '甲 等 2 条')
        self.assertEqual(record['data']['completed'], [A, C])
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
        self.assertEqual(self.store.job(second)['data']['completed'], [A])


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
        self.assertEqual((record['status'], record['data']['completed'], record['data']['result']['engine_error']),
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
        self.assertEqual(record['title'], 'bad ? title 等 2 条')
        self.assertEqual(self.store.find_download(['youtube:x?'], 'audio_best')['title'], 'bad ? title')
        self.assertEqual(record['data']['errors'], ['e ?'])

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


if __name__ == '__main__':
    unittest.main()
