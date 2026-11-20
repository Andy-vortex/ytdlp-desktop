"""Regression tests for the core review findings of track B (state), 1.6.0 core fix round.

review-robustness-1 / review-compat-2  the unfinished list is never replaced because it could not be
                                       read; compact, below SAFE_SIZE, overflow kept aside
review-compat-1                        a 1.6.0-only format in a history job does not break the 1.5.1 window
review-e2e-4                           the history job keeps option sets per (link, format)
review-privacy-security-3              the purges never enter or delete through links or junctions
review-state-2 / review-state-3        the updater retries os.replace for a moment; a file error while
                                       the new exe is written is a Chinese UpdateError
review-state-4                         a release-check stamp far in the future counts as due
review-state-7                         closing while minimised keeps the maximised state

Nothing here touches the network.  Junctions are made inside the test's own temp folder and
removed with ``cmd /c rmdir`` before the folder is deleted.  (2.0.0 S2 P2 / P3) The checks that ran
the 1.5.1 code against what this version writes are gone with the 1.5.1 layer; the unfinished list is
format 2 (a file that is not version 2 is set aside: test_unfinished.ForeignListTests).
"""
import _sandbox
from dataclasses import replace
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import engine
import formats
import library
import paths
import procutil
import settings
import unfinished
import updater
import urls
from library import Library

A, B, C = ('https://example.invalid/' + name for name in 'abc')
LOG = 'YtDlpDesktop.unfinished'
# Only a text inside the stored entries (as the owner's output folder looks); nothing is written there.
OWNER_OUTPUT = 'D:\\YT-DLP下载器\\下载文件'
HISTORY_KINDS = ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dead_pid(case):
    process = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=procutil.HIDDEN)
    process.wait(30)
    case.addCleanup(lambda: process)   # keeps the handle, so Windows cannot hand the pid out again
    assert not procutil.alive(process.pid)
    return process.pid


class TempCase(unittest.TestCase):
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

    def run_job(self, links, results, options=None, hook=None, emit=None):
        """One Runner.download with _run_once scripted: 0 / 1, or a function(runner, command)."""
        steps = list(results)

        def run_once(command, index, total, files):
            step = steps.pop(0)
            return step(runner, command) if callable(step) else (step, False, None)

        def record(kind, value):
            self.events.append((kind, value))
            if emit:
                emit(kind, value)

        runner = engine.Runner(record)
        with mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(links, options or self.options)
        self.assertEqual(steps, [])
        return self.events[-1][1]


# ------------------------------------------------------------------ the unfinished list


class UnfinishedListTests(TempCase):
    """review-robustness-1 (blocker) and review-compat-2 (major)."""

    def owner_entries(self, count, reason='unstarted', error='', title='', start=1_700_000_000.0, mode=None):
        """``count`` entries as a real queue of the owner leaves them (his output folder, MP3)."""
        options = engine._entry_options(engine.Options(Path(OWNER_OUTPUT), mode or formats.MODES[3]))
        result = []
        for number in range(count):
            item = unfinished.entry(f'https://www.youtube.com/watch?v=v{number:010d}', options, reason,
                                    error=error, title=title)
            item['time'] = start + number
            result.append(item)
        return result

    def write_like_wp5(self, entries):
        """The file as the save of the WP5 state wrote it (indent=2, every option, no size limit); (2.0.0)
        in format 2."""
        data = {'version': 2, 'entries': entries}
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return paths.failed_file().read_bytes()

    def set_aside_files(self, kind='bad'):
        return sorted(self.state.glob(f'failed-downloads.{kind}-*.json')) if self.state.is_dir() else []

    def test_an_oversized_list_survives_the_next_run(self):
        """The reproduction of the finding: > 5 MB on disk, one more failing link is run."""
        content = self.write_like_wp5(self.owner_entries(7000, reason='failed', error='ERROR: [youtube] x: ' + 'y' * 80))
        self.assertGreater(len(content), unfinished.MAX_SIZE, 'the WP5 save wrote a file that no loader reads')
        self.assertEqual(unfinished.load(), [], 'load() itself still gives [] for such a file')
        new = 'https://example.invalid/one-more'
        with self.assertLogs(LOG, 'WARNING') as logs:
            done = self.run_job([new], [1])
        kept = self.set_aside_files()
        self.assertEqual(len(kept), 1, 'the unreadable list was renamed, not overwritten')
        self.assertEqual(sha(kept[0].read_bytes()), sha(content), 'byte for byte the old list')
        self.assertEqual([item['url'] for item in unfinished.load()], [new])
        self.assertEqual(done['unfinished'], 1)
        self.assertIn(kept[0].name, logs.output[0])
        self.assertIn(_sandbox.zh_t('core.unfinished.problem.too_large', size='\0').split('\0')[0], logs.output[0])
        # The next run reads the new list normally and sets nothing aside.
        self.run_job([B], [1])
        self.assertEqual([item['url'] for item in unfinished.load()], [new, B])
        self.assertEqual(len(self.set_aside_files()), 1)

    def test_load_for_merge_sets_aside_every_list_it_cannot_read_completely(self):
        good = unfinished.entry(A, engine._entry_options(self.options), 'failed')
        cases = {
            # (2.0.0 S2 P3) the 1.5.1 layer is gone; (S2 Q2, PLAN 3.10) a file that is not version 2 is one
            # this merge cannot read completely: set aside like any other (test_unfinished.ForeignListTests)
            'cut off': '{"version": 2, "entries": [',
            'not an object': '[]',
            'another version': '{"version": 3, "entries": []}',
            'version 1': json.dumps({'version': 1, 'entries': [good]}),
            'the 1.5.1 shape': json.dumps({'urls': [A, B], 'options': {'output': str(self.out)}}),
            'entries not a list': '{"version": 2, "entries": "x"}',
            'no entries': '{"version": 2, "urls": ["https://example.invalid/a"]}',
            'one entry broken': json.dumps({'version': 2, 'entries': [good, {'key': 'k'}]}),
            'larger than 5 MB': json.dumps({'version': 2, 'entries': [good], 'padding': 'x' * (5 * 1024 * 1024)}),
            'a version-1 file larger than 5 MB': json.dumps({'version': 1, 'entries': [good],
                                                             'padding': 'x' * (5 * 1024 * 1024)}),
        }
        for name, text in cases.items():
            with self.subTest(name):
                for path in self.set_aside_files():
                    path.unlink()
                paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
                paths.failed_file().write_text(text, encoding='utf-8')
                lines = []
                with self.assertLogs(LOG, 'WARNING'):
                    entries = unfinished.load_for_merge(lines.append)
                self.assertFalse(paths.failed_file().exists())
                kept = self.set_aside_files()
                self.assertEqual(len(kept), 1)
                self.assertEqual(kept[0].read_text(encoding='utf-8'), text)
                self.assertEqual(len(lines), 1)
                self.assertIn('未完成清单', lines[0])
                self.assertIn(kept[0].name, lines[0])
                self.assertEqual(entries, [good] if name == 'one entry broken' else [])
        # A list that is fine and no list at all: nothing is set aside, nothing is said.
        for path in self.set_aside_files():
            path.unlink()
        unfinished.save([good])
        lines = []
        self.assertEqual(unfinished.load_for_merge(lines.append), [good])
        _sandbox.save_unfinished([A, C], self.options)
        self.assertEqual([item['url'] for item in unfinished.load_for_merge(lines.append)], [A, C])
        paths.failed_file().unlink()
        self.assertEqual(unfinished.load_for_merge(lines.append), [])
        self.assertEqual((lines, self.set_aside_files()), ([], []))

    def test_load_at_start_sets_aside_a_damaged_too_large_or_unreadable_list(self):
        """(2.0.0 S2 Q2, S2-AMEND.md 2.2) The window's start renames a list that gives no entry, as the
        merge does (the line is the same core.unfinished.kept_aside)."""
        good = unfinished.entry(A, engine._entry_options(self.options), 'failed')
        cases = {'damaged': ('{"version": 2, "entries": [', None),
                 'too_large': (json.dumps({'version': 2, 'entries': [good], 'padding': 'x' * (5 * 1024 * 1024)}), None),
                 'unreadable': ('{"version": 2, "entries": []}', OSError(errno.EIO, 'I/O error'))}
        for problem, (text, failure) in cases.items():
            with self.subTest(problem):
                for path in self.set_aside_files():
                    path.unlink()
                paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
                paths.failed_file().write_text(text, encoding='utf-8')
                lines = []
                reading = mock.patch.object(paths, 'read_text', side_effect=failure) if failure else mock.patch.object(
                    paths, 'read_text', wraps=paths.read_text)
                with reading, self.assertLogs(LOG, 'WARNING'):
                    self.assertEqual(unfinished.load_at_start(lines.append), [])
                self.assertFalse(paths.failed_file().exists())
                kept = self.set_aside_files()
                self.assertEqual([path.read_text(encoding='utf-8') for path in kept], [text])
                self.assertEqual([(line.key, line.params['problem'].key, line.params['target']) for line in lines],
                                 [('core.unfinished.kept_aside', f'core.unfinished.problem.{problem}', kept[0].name)])

    def test_the_merge_as_the_engine_will_call_it(self):
        """Runner.download's finally with the one sanctioned change: load_for_merge instead of load."""
        content = self.write_like_wp5(self.owner_entries(7000, reason='failed', error='e' * 100))
        lines = []
        results = [{'url': A, 'key': urls.link_key(A), 'format': 'audio_best', 'options': engine._entry_options(self.options),
                    'outcome': 'failed', 'error': 'x', 'title': '', 'referer': '', 'media_keys': []}]
        with unfinished.LOCK:
            entries = unfinished.merge(unfinished.load_for_merge(lines.append), results)
            unfinished.save(entries)
        self.assertEqual([item['url'] for item in unfinished.load()], [A])
        self.assertEqual(sha(self.set_aside_files()[0].read_bytes()), sha(content))
        self.assertEqual(len(lines), 1)

    def test_a_set_aside_name_that_is_taken_is_never_overwritten(self):
        stamp = '20261116-120000'
        self.state.mkdir(parents=True)
        taken = self.state / f'failed-downloads.bad-{stamp}.json'
        taken.write_text('an older set-aside list', encoding='utf-8')
        paths.failed_file().write_text('{broken', encoding='utf-8')
        with mock.patch.object(unfinished.time, 'strftime', return_value=stamp), self.assertLogs(LOG, 'WARNING'):
            unfinished.load_for_merge()
        self.assertEqual(taken.read_text(encoding='utf-8'), 'an older set-aside list')
        self.assertEqual((self.state / f'failed-downloads.bad-{stamp}-2.json').read_text(encoding='utf-8'), '{broken')

    def test_a_list_that_cannot_be_set_aside_is_left_untouched(self):
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_text('{broken', encoding='utf-8')
        with mock.patch.object(unfinished.os, 'rename', side_effect=PermissionError(13, '拒绝访问。')):
            with self.assertRaises(OSError):
                unfinished.load_for_merge()
            with self.assertRaises(OSError):
                unfinished.save([])
            done = self.run_job([A], [1])
        self.assertEqual(paths.failed_file().read_text(encoding='utf-8'), '{broken')
        self.assertTrue([line for kind, line in self.events
                         if kind == 'log' and getattr(line, 'key', None) == 'core.engine.unfinished_not_saved'])
        self.assertEqual((done['failed_path'], done['unfinished']), (None, 0))

    def test_clearing_the_list_never_deletes_a_list_it_could_not_read(self):
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_text('{broken', encoding='utf-8')
        with self.assertLogs(LOG, 'WARNING'):
            self.assertIsNone(engine.save_unfinished([]))     # what 清除未完成 calls
        self.assertFalse(paths.failed_file().exists())
        self.assertEqual([path.read_text(encoding='utf-8') for path in self.set_aside_files()], ['{broken'])

    def test_a_stopped_queue_of_8000_links_stays_readable(self):
        """The reproduction of review-compat-2: an 8000-link queue stopped during its first link."""
        links = [f'https://www.youtube.com/watch?v=v{number:010d}' for number in range(8000)]
        options = engine.Options(Path(OWNER_OUTPUT), formats.MODES[3])

        def stop(runner, _command):
            runner.cancel()
            return 1, False, None

        # The Runner creates the output folder before the first item: the run uses a folder of this
        # test; the owner's own folder name follows below with the same entries.
        done = self.run_job(links, [stop], options=replace(options, output=self.out / '下载文件'))
        self.assertEqual(done['unfinished'], 8000)
        size = paths.failed_file().stat().st_size
        self.assertLessEqual(size, unfinished.SAFE_SIZE)
        self.assertNotIn(b'\n', paths.failed_file().read_bytes(), 'compact JSON')
        listed = unfinished.load()
        # The temp folder of a test is much longer than the owner's output folder: here the oldest
        # entries may have gone to an overflow file.  Nothing is lost either way.
        moved = [item for path in self.set_aside_files('overflow')
                 for item in json.loads(path.read_text(encoding='utf-8'))['entries']]
        self.assertEqual(sorted(item['url'] for item in listed + moved), sorted(links))
        self.assertEqual({item['options']['mode'] for item in listed}, {formats.MODES[3]})
        for path in self.set_aside_files('overflow'):
            path.unlink()
        # The same 8000 entries with the owner's folder, as they would be written for him.
        owner = self.owner_entries(8000)
        unfinished.save(owner)
        self.assertLessEqual(paths.failed_file().stat().st_size, unfinished.SAFE_SIZE)
        self.assertEqual(self.set_aside_files('overflow'), [], '8000 stopped links fit without an overflow')
        self.assertEqual(len(unfinished.load()), 8000)
        # One more successful job: nothing is lost.
        self.run_job([A], [0])
        self.assertEqual(len(unfinished.load()), 8000)
        _sandbox.write_report('review-state-sizes.json', {
            'bytes_8000_stopped_owner_folder': paths.failed_file().stat().st_size,
            'bytes_8000_stopped_test_folder': size, 'moved_in_test_folder': len(moved),
            'safe_size': unfinished.SAFE_SIZE, 'max_size': unfinished.MAX_SIZE})

    def test_a_list_beyond_the_safe_size_moves_its_oldest_entries_aside(self):
        entries = self.owner_entries(14000, reason='failed', error='ERROR: [youtube] v: Sign in to confirm ' + 'z' * 60,
                                     title='一首相当长的中文歌曲标题 - 某位歌手【官方 MV】')
        lines = []
        with unfinished.LOCK, self.assertLogs(LOG, 'WARNING') as logs:
            unfinished.load_for_merge(lines.append)      # no file yet: nothing to say; the callback is kept
            unfinished.save(entries)
        text = paths.failed_file().read_bytes()
        self.assertLessEqual(len(text), unfinished.SAFE_SIZE)
        overflow = self.set_aside_files('overflow')
        self.assertEqual(len(overflow), 1)
        moved = json.loads(overflow[0].read_text(encoding='utf-8'))['entries']
        kept = unfinished.load()
        self.assertEqual(len(moved) + len(kept), len(entries), 'nothing is dropped')
        self.assertEqual(sorted(item['url'] for item in moved + kept), sorted(item['url'] for item in entries))
        self.assertLessEqual(max(item['time'] for item in moved), min(item['time'] for item in kept), 'the oldest move')
        self.assertIn(overflow[0].name, logs.output[-1])
        self.assertEqual(len(lines), 1)
        self.assertIn(f'最早的 {len(moved)} 条', lines[0])

    # -------------------------------------------------------------- fix round 1

    def test_the_defaults_are_the_options_defaults(self):
        defaults = engine._entry_options(engine.Options(Path(OWNER_OUTPUT)))
        defaults.pop('output')
        self.assertEqual(unfinished.OPTION_DEFAULTS, defaults)

    def test_entries_are_written_without_their_default_options(self):
        """core-rv-state-1-1: no repeated default options."""
        title = '一首相当长的中文歌曲标题 - 某位歌手【官方 MV】'
        error = 'ERROR: [youtube] v: Sign in to confirm ' + 'z' * 60
        entries = self.owner_entries(5000, reason='failed', error=error, title=title)
        unfinished.save(entries)
        raw = json.loads(paths.failed_file().read_text(encoding='utf-8'))
        self.assertEqual({tuple(sorted(item['options'])) for item in raw['entries']}, {('mode', 'output')})
        self.assertEqual(raw['entries'][0]['options'], {'output': OWNER_OUTPUT, 'mode': formats.MODES[3]})
        size = paths.failed_file().stat().st_size
        self.assertLessEqual(size, unfinished.SAFE_SIZE * 3 // 4, size)
        self.assertEqual(self.set_aside_files('overflow'), [])
        self.assertEqual(unfinished.load(), entries, 'the defaults come back when the list is read')
        loaded = engine.Options.from_dict(unfinished.load()[0]['options'])
        self.assertEqual(loaded, engine.Options(Path(OWNER_OUTPUT), formats.MODES[3]))
        # Values that differ from the defaults are written; an older file with every option still reads the same.
        special = engine.Options(self.out, formats.MODES[4], '1080p', playlist=True, batch_safe=False, naming='title_id',
                                 embed_cover=False, subtitles=True, skip_downloaded=True)
        item = unfinished.entry(A, engine._entry_options(special), 'stopped')
        unfinished.save([item])
        written = json.loads(paths.failed_file().read_text(encoding='utf-8'))['entries'][0]['options']
        self.assertEqual(written, {'output': str(self.out), 'mode': formats.MODES[4], 'resolution': '1080p', 'playlist': True,
                                   'batch_safe': False, 'naming': 'title_id', 'embed_cover': False, 'subtitles': True,
                                   'skip_downloaded': True})
        self.assertEqual(engine.Options.from_dict(unfinished.load()[0]['options']), replace(special, proxy='', cookies=''))
        full = self.owner_entries(2)
        self.write_like_wp5(full)
        self.assertEqual(unfinished.load(), full)
        # (2.0.0 S2 P4: unfinished.newest_group, used only by a 1.5.1 wrapper, is gone)
        both = unfinished.load() + self.owner_entries(1, start=1_800_000_000.0)
        self.assertEqual([item['options'] for item in both], [both[-1]['options']] * 3,
                         'entries of both forms ran with the same options')

    def hold(self, seconds):
        """Open the list with no sharing at all (as a scanner or a backup tool may) for ``seconds``."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateFileW(str(paths.failed_file()), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotIn(handle, (None, wintypes.HANDLE(-1).value), ctypes.get_last_error())
        timer = threading.Timer(seconds, kernel32.CloseHandle, (handle,))
        timer.start()
        self.addCleanup(timer.join)
        return timer

    def test_a_list_held_for_a_moment_by_another_program_is_read_not_set_aside(self):
        """core-rv-state-1-2: a sharing violation is waited out (READ_RETRY), not taken for an unreadable list."""
        for seconds in (0.1, 0.4):
            with self.subTest(seconds=seconds):
                for path in paths.failed_file().parent.glob('failed-downloads*'):
                    path.unlink()
                self.events = []
                unfinished.save(self.owner_entries(50, reason='failed'))
                new = f'https://example.invalid/held-{seconds}'

                def step(runner, _command):
                    self.hold(seconds)
                    return 1, False, None

                self.run_job([new], [step])
                time.sleep(seconds + 0.1)
                self.assertEqual(self.set_aside_files(), [])
                self.assertEqual(len(unfinished.load()), 51)
                self.assertEqual(unfinished.load()[-1]['url'], new)
                self.assertFalse([line for kind, line in self.events if kind == 'log' and '清单' in str(line)])

    def test_a_list_held_longer_is_kept_as_it_is(self):
        for path in paths.failed_file().parent.glob('failed-downloads*') if paths.failed_file().parent.is_dir() else []:
            path.unlink()
        unfinished.save(self.owner_entries(50, reason='failed'))
        before = paths.failed_file().read_bytes()

        def step(runner, _command):
            self.hold(2.5)
            return 1, False, None

        started = time.monotonic()
        done = self.run_job([A], [step])
        self.assertGreaterEqual(time.monotonic() - started, unfinished.READ_RETRY * 0.9, 'the read was retried')
        time.sleep(2.7)
        self.assertEqual(paths.failed_file().read_bytes(), before, 'never replaced by a shorter list')
        self.assertTrue([line for kind, line in self.events
                         if kind == 'log' and getattr(line, 'key', None) == 'core.engine.unfinished_not_saved'])
        self.assertEqual(done['failed_path'], None)


# ------------------------------------------------------------------ the history (library.sqlite3)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Library(self.root / 'library.sqlite3')
        self.out = self.root / 'out'
        engine.Runner.reset_pacing()
        pace = mock.patch.object(engine, 'PACE_SECONDS', (0, 0))
        pace.start()
        self.addCleanup(pace.stop)
        state = _sandbox.state_patch(self.root / 'state')
        state.start()
        self.addCleanup(state.stop)

    def launch(self, options, links, overrides=None):
        """The record ui_download.launch writes."""
        data = {**options.to_dict(), 'urls': list(links)}
        if overrides:
            data['overrides'] = {url: own.to_dict() for url, own in overrides.items()}
        return self.store.create_job('download', f'{len(links)}条 · {options.mode}', data)

    def raw(self, job):
        db = sqlite3.connect(self.store.path)
        try:
            return json.loads(db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()[0])
        finally:
            db.close()

    # review-compat-1

    def test_every_format_is_stored_as_itself(self):
        """(2.0.0 S2 P2) Format 2 has one 'mode' (S2-DESIGN.md 1.2): no mode_v2 for the formats 1.5.1 did
        not know (review-compat-1 kept the 1.5.1 window reading them; that window is not supported)."""
        cases = [(formats.MODES[5], 'best'), (formats.MODES[6], '1080p'), (formats.MODES[3], 'best')]
        for mode, resolution in cases:
            with self.subTest(mode=mode):
                options = engine.Options(self.out, mode, resolution)
                job = self.launch(options, [A])
                stored = self.raw(job)['options']
                self.assertEqual(stored['mode'], mode)
                self.assertNotIn('mode_v2', stored)
                record = self.store.job(job)
                self.assertEqual(record['data']['options']['mode'], mode)
                self.assertEqual(engine.Options.from_dict(record['data']['options']), options)
                row = next(row for row in self.store.history() if row['id'] == job)
                self.assertEqual(row['data']['options']['mode'], mode)
                # A write of the options through change_job keeps the stored form.
                self.store.change_job(job, options=record['data']['options'])
                self.assertEqual(self.raw(job)['options'], stored)
                self.assertEqual(self.store.job(job)['data']['options']['mode'], mode)
        # Collection jobs (no download format) are stored as before.
        collect = self.store.create_job('collect', '采集', {'urls': [A], 'dynamic': False})
        self.assertEqual(self.raw(collect)['options'], {'urls': [A], 'dynamic': False, 'proxy': ''})


    # review-e2e-4

    def history_emit(self, job):
        def emit(kind, value):
            if kind in HISTORY_KINDS:
                self.store.download_event(job, kind, value)
        return emit

    def run_with_history(self, job, links, options, steps):
        """Runner.download as the window runs it: every event also goes to download_event."""
        steps = list(steps)
        emit = self.history_emit(job)
        runner = engine.Runner(emit)

        def run_once(command, index, total, files):
            step = steps.pop(0)
            return step(runner) if callable(step) else (step, False, None)

        with mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(links, options)
        self.assertEqual(steps, [])

    def test_the_same_link_in_another_format_keeps_both_downloads(self):
        mp3 = engine.Options(self.out, formats.MODES[3])
        flac = engine.Options(self.root / '另一个 文件夹', formats.MODES[2])
        for outcome_mp3, outcome_flac in ((0, 0), (0, 1), (1, 0)):
            with self.subTest(mp3=outcome_mp3, flac=outcome_flac):
                job = self.launch(mp3, [A, B])
                accepted = []

                def first(runner):
                    accepted.append(runner.enqueue([A], flac))
                    return outcome_mp3, False, None

                self.run_with_history(job, [A, B], mp3, [first, 0, outcome_flac])
                self.assertEqual(accepted, [[A]])
                data = self.store.job(job)['data']
                options = data['options']
                self.assertEqual(options['urls'], [A, B])
                self.assertNotIn(A, options.get('overrides') or {}, 'the first download of A keeps the job options')
                self.assertEqual([(item['url'], item['format'], item['options']['mode'], item['options']['output'])
                                  for item in options['variants']],
                                 [(A, 'audio_flac', formats.MODES[2], str(flac.output))])
                plan = [(item['url'], item['format'], item['done']) for item in library.job_downloads(data)]
                self.assertEqual(plan, [(A, 'audio_mp3', outcome_mp3 == 0), (B, 'audio_mp3', True),
                                        (A, 'audio_flac', outcome_flac == 0)])
                for item in library.job_downloads(data):
                    expected = flac if item['format'] == 'audio_flac' else mp3
                    self.assertEqual(engine.Options.from_dict(item['options']), expected)
                # (2.0.0) one completion list: the (link, format) pairs that finished
                self.assertEqual(sorted(map(tuple, data['done'])),
                                 sorted([(A, 'audio_mp3')] * (outcome_mp3 == 0) + [(B, 'audio_mp3')]
                                        + [(A, 'audio_flac')] * (outcome_flac == 0)))

    def test_the_same_link_in_the_same_format_updates_its_options_as_before(self):
        mp3 = engine.Options(self.out, formats.MODES[3])
        job = self.launch(mp3, [A])
        other = replace(mp3, output=self.root / '别处')
        self.store.extend_job(job, [A], {A: other.to_dict()})
        options = self.store.job(job)['data']['options']
        self.assertEqual(options['overrides'][A]['output'], str(other.output))
        self.assertNotIn('variants', options)
        # A new link with its own options, and an override of a link that is not in the list (as before).
        self.store.extend_job(job, [B], {B: other.to_dict(), C: other.to_dict()})
        options = self.store.job(job)['data']['options']
        self.assertEqual(options['urls'], [A, B])
        self.assertEqual(set(options['overrides']), {A, B, C})
        # A variant added twice is one entry with the newest options.
        flac = engine.Options(self.out, formats.MODES[2])
        self.store.extend_job(job, [A], {A: flac.to_dict()})
        self.store.extend_job(job, [A], {A: replace(flac, output=self.root / '新').to_dict()})
        variants = self.store.job(job)['data']['options']['variants']
        self.assertEqual([(item['url'], item['format'], item['options']['output']) for item in variants],
                         [(A, 'audio_flac', str(self.root / '新'))])

    def test_completion_of_events_without_formats(self):
        """(2.0.0) An item_done without a format: the job's only format of that link is taken; a link the
        job has in two formats is remembered without one (no download of the plan is marked)."""
        mp3 = engine.Options(self.out, formats.MODES[3])
        job = self.launch(mp3, [A, B])
        self.store.download_event(job, 'item_done', dict(index=1, success=False, cancelled=False, files=0,
                                                         bot_check=False, url=A, skipped='downloaded'))
        self.store.extend_job(job, [B], {B: engine.Options(self.out, formats.MODES[2]).to_dict()})
        self.store.download_event(job, 'item_done', dict(index=2, success=False, cancelled=False, files=0,
                                                         bot_check=False, url=B, skipped='downloaded'))
        data = self.store.job(job)['data']
        self.assertEqual(data['done'], [[A, 'audio_mp3'], [B, None]], 'B has two formats: which one is unknown')
        self.assertEqual([item['done'] for item in library.job_downloads(data)], [True, False, False])
        self.store.download_event(job, 'item_done', dict(index=3, success=False, cancelled=False, files=0,
                                                         bot_check=False, url=B, skipped='downloaded', format='audio_flac'))
        self.assertEqual(self.store.job(job)['data']['done'], [[A, 'audio_mp3'], [B, None], [B, 'audio_flac']])


# ------------------------------------------------------------------ the purges and links


def make_junction(case, link, target):
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True,
                   creationflags=procutil.HIDDEN, timeout=30)
    case.addCleanup(remove_junction, link)


def remove_junction(link):
    if os.path.lexists(link) and paths.is_link(link):
        subprocess.run(['cmd', '/c', 'rmdir', str(link)], capture_output=True, creationflags=procutil.HIDDEN, timeout=30)


class LinkTests(unittest.TestCase):
    """review-privacy-security-3: real junctions inside the test's temp folder."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'profile' / 'YtDlpDesktop'     # the app's own name: _owned() is True
        patcher = _sandbox.state_patch(self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dead = dead_pid(self)
        self.old = time.time() - 3 * 86400

    def victim(self, name, files):
        folder = self.root / name
        for relative in files:
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'keep me')
            os.utime(path, (self.old, self.old))
        return folder

    def listing(self, folder):
        return sorted(str(path.relative_to(folder)) for path in folder.rglob('*'))

    def purge(self):
        paths.purge_stale(procutil.alive)
        paths.purge_engine_downloads(procutil.alive)
        updater.cleanup()

    def test_linked_work_folders_are_left_alone(self):
        self.state.mkdir(parents=True)
        victims = {
            'work': self.victim('victim-work', ['holiday.mkv', 'notes.txt', 'song.flac', 'old.part']),
            'tmp': self.victim('victim-tmp', [f'run-{self.dead}-abcd/keep.txt']),
            'collect': self.victim('victim-collect', [f'run-{self.dead}-ef01/keep.txt']),
            'engines': self.victim('victim-engines', ['yt-dlp.download', f'yt-dlp.download.{self.dead}.1.exe']),
        }
        before = {name: self.listing(folder) for name, folder in victims.items()}
        for name, folder in victims.items():
            make_junction(self, self.state / name, folder)
            self.assertTrue(paths.is_link(self.state / name))
        self.purge()
        self.assertEqual({name: self.listing(folder) for name, folder in victims.items()}, before)

    def test_links_inside_the_real_folders_are_never_entered(self):
        inner = self.victim('victim-inner', ['keep.txt'])
        part = self.victim('victim-part', ['keep.txt'])
        engines_target = self.victim('victim-engine-link', ['keep.txt'])
        run = paths.own_dir(paths.tmp_dir()) / f'run-{self.dead}-abcd'
        run.mkdir()
        (run / 'file.txt').write_bytes(b'x')
        make_junction(self, run / 'inner', inner)
        work = paths.own_dir(paths.work_dir())
        stale = work / 'stale.webm'
        stale.write_bytes(b'x')
        older = time.time() - 8 * 86400      # final-architecture-1: kept for paths.WORK_MAX_AGE
        os.utime(stale, (older, older))
        make_junction(self, work / 'old.part', part)
        make_junction(self, work / 'clip.webm', part)
        engines = paths.own_dir(paths.engines_dir())
        linked = f'yt-dlp.download.{self.dead}.1.exe'      # (2.0.0 S2 P4) a name the purge removes when it is a file
        make_junction(self, engines / linked, engines_target)
        symlink = work / 'linked.webm'
        try:
            os.symlink(part / 'keep.txt', symlink)
            self.addCleanup(lambda: os.path.lexists(symlink) and os.unlink(symlink))
            os.utime(symlink, (self.old, self.old), follow_symlinks=False)
        except (OSError, NotImplementedError):
            symlink = None      # creating symbolic links needs a privilege this account may not have
        self.purge()
        self.assertFalse(run.exists(), 'the dead run folder is removed ...')
        self.assertEqual(self.listing(inner), ['keep.txt'], '... without entering the junction inside it')
        self.assertFalse(stale.exists(), 'an old plain file in the work folder is still removed')
        self.assertTrue(paths.is_link(work / 'old.part') and paths.is_link(work / 'clip.webm'))
        self.assertTrue(paths.is_link(engines / linked))
        self.assertEqual(self.listing(part), ['keep.txt'])
        self.assertEqual(self.listing(engines_target), ['keep.txt'])
        if symlink is not None:
            self.assertTrue(os.path.lexists(symlink), 'a file link in the work folder stays')

    def test_a_linked_state_folder_is_not_cleaned(self):
        target = self.victim('victim-state', [f'tmp/run-{self.dead}-abcd/keep.txt', 'work/holiday.mkv',
                                              'engines/yt-dlp.download'])
        before = self.listing(target)
        self.state.parent.mkdir(parents=True)
        make_junction(self, self.state, target)
        self.assertTrue(paths.is_link(paths.STATE_DIR))
        self.assertFalse(paths._purge_allowed())
        self.purge()
        self.assertEqual(self.listing(target), before)

    def test_is_link(self):
        folder = self.root / 'plain'
        folder.mkdir()
        (folder / 'f').write_bytes(b'x')
        self.assertFalse(paths.is_link(folder))
        self.assertFalse(paths.is_link(folder / 'f'))
        self.assertFalse(paths.is_link(self.root / 'missing'))
        make_junction(self, self.root / 'j', folder)
        self.assertTrue(paths.is_link(self.root / 'j'))


# ------------------------------------------------------------------ the updater


TAG = '2099.01.02'
ASSET_URL = updater.ASSET_PREFIX + TAG + '/yt-dlp.exe'
PAYLOAD = b'MZ' + bytes(range(256)) * 1200
OLD_ENGINE = b'MZ old updated engine'
GOOD_PROBE = b'yt-dlp.exe: error: You must provide at least one URL.\r\n'


class FakeGitHub:
    def __call__(self, request, timeout=None):
        url = request.full_url
        if url == updater.RELEASE_URL:
            release = {'tag_name': TAG, 'assets': [{'name': 'yt-dlp.exe', 'browser_download_url': ASSET_URL,
                                                    'size': len(PAYLOAD), 'digest': 'sha256:' + sha(PAYLOAD)}]}
            return io.BytesIO(json.dumps(release).encode('utf-8'))
        if url == ASSET_URL:
            return io.BytesIO(PAYLOAD)
        raise AssertionError(f'unexpected address: {url}')


class Full:
    """A file whose write fails as on a full disk."""

    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.handle.close()
        return False

    def write(self, _data):
        raise OSError(errno.ENOSPC, 'No space left on device')


class UpdaterTests(unittest.TestCase):
    """review-state-2 and review-state-3."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = _sandbox.state_patch(self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        updater.invalidate()
        self.addCleanup(updater.invalidate)
        self.engines = paths.engines_dir()
        self.engines.mkdir(parents=True)
        (self.engines / 'yt-dlp.exe').write_bytes(OLD_ENGINE)
        (self.engines / 'yt-dlp.json').write_text(json.dumps({'version': '2001.01.01', 'sha256': sha(OLD_ENGINE)}),
                                                  encoding='utf-8')
        self.events = []
        self.cancelled = threading.Event()
        self.handles = []
        self.addCleanup(self.release_handles)

    def release_handles(self):
        for handle in self.handles:
            try:
                handle.close()
            except OSError:
                pass

    def run_update(self, probe=None):
        def default_probe(command, timeout):
            return GOOD_PROBE
        started = time.monotonic()
        # (1.7.0) The pretend exe is no program: its "--version" run is stood in for (c5-updater a).
        with mock.patch('urllib.request.urlopen', FakeGitHub()), \
                mock.patch.object(updater, '_version_output', return_value=(0, b'2099.01.02\r\n', b'')), \
                mock.patch.object(updater, '_probe_output', side_effect=probe or default_probe):
            updater.EngineUpdater(lambda kind, value: self.events.append((kind, value)), self.cancelled,
                                  ['--ignore-config']).run()
        self.assertEqual([kind for kind, _value in self.events][-1], 'update_done')
        return time.monotonic() - started

    def values(self, kind):
        return [value for name, value in self.events if name == kind]

    def holder(self, seconds):
        """A probe after which a scanner holds the new exe (read and write sharing, no delete sharing)."""
        def probe(command, timeout):
            handle = open(command[0], 'rb')       # Python's open() never shares delete access
            self.handles.append(handle)
            timer = threading.Timer(seconds, handle.close)
            timer.daemon = True
            timer.start()
            self.addCleanup(timer.cancel)
            return GOOD_PROBE
        return probe

    def test_a_scanner_that_holds_the_new_exe_for_a_second_does_not_stop_the_update(self):
        elapsed = self.run_update(self.holder(1.0))
        self.assertEqual(self.values('update_error'), [])
        self.assertEqual(self.values('updated'), [{'version': TAG, 'unchanged': False}])
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), PAYLOAD)
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertEqual(sorted(path.name for path in self.engines.iterdir()),
                         ['yt-dlp.exe', 'yt-dlp.json'])

    def test_a_handle_held_longer_gives_the_chinese_message_after_about_three_seconds(self):
        elapsed = self.run_update(self.holder(6.0))
        self.assertEqual(len(self.values('update_error')), 1)
        message = self.values('update_error')[0]
        self.assertEqual(getattr(message, 'key', None), 'core.update.write_failed', message)
        self.assertGreaterEqual(elapsed, updater.INSTALL_RETRY - 0.2)
        self.assertLess(elapsed, updater.INSTALL_RETRY + 2.5)
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), OLD_ENGINE)

    def test_stop_during_the_retries_installs_nothing(self):
        timer = threading.Timer(0.5, self.cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        elapsed = self.run_update(self.holder(6.0))
        self.assertEqual((self.values('update_error'), self.values('updated')), ([], []))
        self.assertEqual(self.values('update_done'), [{'cancelled': True}])
        self.assertLess(elapsed, 2.5)
        self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), OLD_ENGINE)

    def file_trouble(self, kind):
        real_open = Path.open

        def fake_open(path, mode='r', *args, **kwargs):
            if path.name.startswith('yt-dlp.download.') and 'w' in mode:
                if kind == 'open':
                    raise PermissionError(13, '拒绝访问。', str(path))
                return Full(real_open(path, mode, *args, **kwargs))
            return real_open(path, mode, *args, **kwargs)
        return mock.patch.object(Path, 'open', fake_open)

    def test_a_full_disk_or_no_permission_gives_a_chinese_message(self):
        for kind, reason in (('write', 'No space left on device'), ('open', '拒绝访问。')):
            with self.subTest(kind=kind):
                self.events.clear()
                with self.file_trouble(kind), self.assertLogs('YtDlpDesktop.updater', 'WARNING'):
                    self.run_update()
                self.assertEqual(self.values('update_error'), [_sandbox.zh_t('core.update.save_failed', reason=reason)])
                message = self.values('update_error')[0]
                for part in ('[Errno', 'yt-dlp', str(self.root), '\\'):
                    self.assertNotIn(part, message)
                self.assertEqual((self.engines / 'yt-dlp.exe').read_bytes(), OLD_ENGINE)
                self.assertEqual(sorted(path.name for path in self.engines.iterdir()), ['yt-dlp.exe', 'yt-dlp.json'])


# ------------------------------------------------------------------ the window


class WindowTests(unittest.TestCase):
    """review-state-4 and review-state-7 (one live StudioApp at a time)."""

    def setUp(self):
        import ui_settings
        from studio import StudioApp
        self.StudioApp = StudioApp
        self.calls = []

        def check_latest(timeout=None):
            self.calls.append(timeout)
            return {'tag': '2099.01.01', 'url': '', 'size': 0, 'sha256': ''}

        def no_clipboard(_self, *_args, **_kwargs):
            raise tk.TclError('no clipboard')

        for patcher in (mock.patch.object(updater, 'check_latest', check_latest),
                        mock.patch.object(StudioApp, 'clipboard_get', no_clipboard)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.DAY = ui_settings.DAY
        settings_file = _sandbox.state_dir() / 'settings.json'
        if settings_file.exists():
            settings_file.unlink()

    def make_app(self, **values):
        import customtkinter as ctk
        _sandbox.write_settings(**values)
        ctk.set_appearance_mode('light')
        app = self.StudioApp(_sandbox.app_args())
        self.addCleanup(self.close, app)
        app.attributes('-alpha', 0.0)
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    @staticmethod
    def pump(app, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.update()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def test_a_release_check_stamp_far_in_the_future_counts_as_due(self):
        app = self.make_app()
        app.withdraw()
        app.check_engine_updates.set(True)
        now = time.time()
        for stamp, due in ((now + 400 * self.DAY, True), (now + 2 * self.DAY, True), (now + 2 * 3600, False),
                           (now - 2 * 3600, False), (now - 2 * self.DAY, True), (0, True)):
            with self.subTest(ahead=stamp - now):
                self.calls.clear()
                app.prefs['last_engine_check'] = stamp
                app.daily_engine_check()
                end = time.monotonic() + (5 if due else 0.5)
                while time.monotonic() < end and not self.calls:
                    time.sleep(0.02)
                self.assertEqual(len(self.calls), 1 if due else 0)

    def close_minimised(self, zoom_during_session=False, **values):
        app = self.make_app(**values)
        if zoom_during_session:
            self.assertTrue(self.pump(app, lambda: app.state() == 'normal'))
            app.state('zoomed')
        if values.get('zoomed') or zoom_during_session:
            self.assertTrue(self.pump(app, lambda: app.state() == 'zoomed'))
        else:
            self.assertTrue(self.pump(app, lambda: app.state() == 'normal'))
        app.iconify()
        self.assertTrue(self.pump(app, lambda: app.state() == 'iconic'))
        app.on_close()       # not busy: settings are saved and the window is destroyed
        return settings.load()

    def test_closing_while_minimised_keeps_the_maximised_state(self):
        stored = '1150x820+40+50'
        values = self.close_minimised(geometry=stored, zoomed=True)
        self.assertTrue(values['zoomed'], 'maximised at start, minimised, closed from the taskbar')
        self.assertEqual(values['geometry'], stored)
        values = self.close_minimised(zoom_during_session=True, geometry=stored, zoomed=False)
        self.assertTrue(values['zoomed'], 'maximised during the session, then minimised and closed')
        values = self.close_minimised(geometry=stored, zoomed=False)
        self.assertFalse(values['zoomed'], 'a normal window that was minimised stays normal')


if __name__ == '__main__':
    unittest.main()
