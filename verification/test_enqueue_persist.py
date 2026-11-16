"""prefix-queue (before installing 1.6.0): links added with 加入队列 to a running job are written
into the job's history record at once (ui_download.DownloadUI.record_enqueued).

Before the fix they lived only in Runner.inbox until the next item started, so a hard kill, a
crash or a power cut during a long item or a 429 cooldown lost them from the history job (what
继续未完成 resumes) and from the unfinished list (Codex suggestion 1, verifier c1-enqueue-persist).

The hard-kill tests start a child process (this file with ``child <scenario> <result file>``):
a real, withdrawn StudioApp in the inherited sandbox starts a job through 开始下载 with the real
engine.Runner and its real _run_once loop.  engine.build_command gives a stand-in engine, a
``python -c`` child that copies verification/fixture/fixture.mp4 slowly into the output folder
and prints yt-dlp's progress lines, or that answers HTTP 429 (the Runner then sits in its real
300 s cooldown).  While the item runs, links are added with 加入队列 (the second time in another
format), and the child calls os._exit(1).  The parent reads the history afterwards.  The
'nofix' scenario turns the fix off and shows the loss the test guards against.
No website, no yt-dlp, no network.
"""
import _sandbox
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

import engine
import formats
import library
import ui_download
import ui_theme
import updater
from engine import MODES, Options, QueueItem
from library import job_downloads

FIXTURE = _sandbox.ROOT / 'verification' / 'fixture' / 'fixture.mp4'
FIRST_SLOW = 'https://example.invalid/slow-first'
FIRST_LIMITED = 'https://example.invalid/limited-first'
ADDED_SAME = 'https://example.invalid/added-a'
ADDED_OTHER = ['https://example.invalid/added-b', 'https://example.invalid/added-c']
OTHER_MODE = MODES[3]
CHILD_TIMEOUT = 120

# The stand-in engine: argv = [folder, '--', url].
STAND_IN = r'''
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
folder, url = sys.argv[1], sys.argv[-1]
if "limited" in url:
    print("ERROR: [generic] limited-first: Unable to download webpage: HTTP Error 429: Too Many Requests",
          file=sys.stderr, flush=True)
    sys.exit(1)
data = open(FIXTURE, "rb").read()
os.makedirs(folder, exist_ok=True)
part = os.path.join(folder, "slow [slow].mp4.part")
end = time.monotonic() + 60
with open(part, "wb") as handle:
    for offset in range(0, len(data), 1024):
        if time.monotonic() > end:
            break
        handle.write(data[offset:offset + 1024])
        handle.flush()
        try:
            print(f"__GUI_PROGRESS__{offset + 1024}|{len(data)}|NA|1024|{len(data) // 1024}|downloading", flush=True)
        except OSError:
            sys.exit(1)
        time.sleep(0.2)
sys.exit(1)
'''


# --- child process -------------------------------------------------------------------------

def child(scenario, result_path):
    from studio import StudioApp
    import customtkinter as ctk

    def no_network(*_args, **_kwargs):
        raise AssertionError('no update check in this test')

    def no_clipboard(self, *_args, **_kwargs):
        raise tk.TclError('CLIPBOARD selection doesn\'t exist')

    seen = set()

    class Watched(engine.Runner):
        def __init__(self, emit, library=None):
            def watched(kind, value):
                seen.add(kind)
                emit(kind, value)
            super().__init__(watched, library=library)

    def command(url, item_options):
        built = real_build(url, item_options)
        return [sys.executable, '-c', f'FIXTURE = {str(FIXTURE)!r}\n' + STAND_IN, built[built.index('-P') + 1], '--', url]

    real_build = engine.build_command
    result = {'scenario': scenario}
    mock.patch.object(updater, 'check_latest', no_network).start()
    mock.patch.object(StudioApp, 'clipboard_get', no_clipboard).start()
    mock.patch.object(engine, 'PACE_SECONDS', (0, 0)).start()
    mock.patch.object(engine, 'build_command', command).start()
    mock.patch.object(ui_download, 'Runner', Watched).start()
    if scenario == 'nofix':
        mock.patch.object(StudioApp, 'record_enqueued', lambda *_args: None).start()
    _sandbox.write_settings()
    ctk.set_appearance_mode('light')
    app = StudioApp(_sandbox.app_args())
    app.withdraw()
    app.update_idletasks()
    app.output.set(str(_sandbox.out('downloads')))
    app.mode.set(MODES[0])
    first = FIRST_LIMITED if scenario == 'limited' else FIRST_SLOW
    app.urls.insert('1.0', first)
    app.start()
    wanted = 'waiting' if scenario == 'limited' else 'progress'
    end = time.monotonic() + 60
    while wanted not in seen and time.monotonic() < end:
        app.update()
        time.sleep(0.02)
    result['seen'] = sorted(seen)
    result['busy'] = bool(app.busy)
    result['job'] = app.download_job
    # 加入队列: one link in the job's format, then two in another format.
    app.urls.insert('1.0', ADDED_SAME)
    first_count = app.enqueue_links()
    app.mode.set(OTHER_MODE)
    app.urls.insert('1.0', '\n'.join(ADDED_OTHER))
    second_count = app.enqueue_links()
    result['accepted'] = [first_count, second_count]
    result['inbox'] = [item.url for item in app.runner.inbox]
    result['queued_event'] = 'queued' in seen
    result['hint'] = ui_theme.full_text(app.link_hint)
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    sys.stdout.flush()
    os._exit(1)                     # the hard kill: no finally, no queued event, no unfinished list


# --- tests ---------------------------------------------------------------------------------

class HardKillTests(unittest.TestCase):
    """A hard kill while the current item runs keeps every link that 加入队列 accepted."""

    def run_child(self, scenario):
        result_path = _sandbox.out(f'enqueue-persist-{scenario}.json')
        result_path.unlink(missing_ok=True)
        process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()), 'child', scenario,
                                  str(result_path)], capture_output=True, text=True, encoding='utf-8',
                                 errors='replace', timeout=CHILD_TIMEOUT)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertTrue(result_path.is_file(), process.stdout + process.stderr)
        result = json.loads(result_path.read_text(encoding='utf-8'))
        time.sleep(1.0)            # the stand-in engine dies with its job object
        return result

    def job_of(self, job):
        lib = library.Library()
        rows = {row['id']: row for row in lib.history()}
        self.assertIn(job, rows)
        return rows[job]

    def check_child(self, result, first):
        wanted = 'waiting' if 'limited' in first else 'progress'
        self.assertIn(wanted, result['seen'], 'the item was running when links were added')
        self.assertTrue(result['busy'])
        self.assertTrue(result['job'])
        self.assertEqual(result['accepted'], [1, 2])
        self.assertEqual(result['inbox'], [ADDED_SAME, *ADDED_OTHER], 'the links were still only in the inbox')
        self.assertFalse(result['queued_event'], 'no queued event came before the kill')
        self.assertEqual(result['hint'], '已加入队列 2 条')

    def check_record(self, result, first):
        row = self.job_of(result['job'])
        self.assertEqual(row['status'], 'interrupted')
        downloads = job_downloads(row['data'])
        self.assertEqual([entry['url'] for entry in downloads], [first, ADDED_SAME, *ADDED_OTHER])
        self.assertFalse(any(entry['done'] for entry in downloads))
        by_url = {entry['url']: entry for entry in downloads}
        self.assertEqual(by_url[ADDED_SAME]['format'], by_url[first]['format'])
        self.assertEqual(by_url[ADDED_SAME]['options']['mode'], MODES[0])
        for url in ADDED_OTHER:
            self.assertEqual(by_url[url]['options']['mode'], OTHER_MODE, 'the added links keep their own format')
            self.assertNotEqual(by_url[url]['format'], by_url[first]['format'])
        overrides = row['data']['options'].get('overrides') or {}
        self.assertEqual(sorted(overrides), sorted(ADDED_OTHER), 'options only for links that differ from the job')
        return row

    def test_hard_kill_during_a_slow_download_keeps_the_added_links(self):
        result = self.run_child('slow')
        self.check_child(result, FIRST_SLOW)
        self.check_record(result, FIRST_SLOW)

    def test_hard_kill_during_a_429_cooldown_keeps_the_added_links(self):
        result = self.run_child('limited')
        self.check_child(result, FIRST_LIMITED)
        self.check_record(result, FIRST_LIMITED)

    def test_without_the_fix_the_hard_kill_loses_them(self):
        """Control: the same child with record_enqueued turned off reproduces the loss."""
        result = self.run_child('nofix')
        self.check_child(result, FIRST_SLOW)
        row = self.job_of(result['job'])
        self.assertEqual(row['status'], 'interrupted')
        self.assertEqual([entry['url'] for entry in job_downloads(row['data'])], [FIRST_SLOW])


class FakeRunner:
    """Stands in for engine.Runner (no base_options): download() waits until released."""
    instances = []

    def __init__(self, emit, library=None):
        self.emit, self.library = emit, library
        self.origins = {}
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.inbox = []
        self.enqueued = []
        FakeRunner.instances.append(self)

    def download(self, urls, options, overrides=None):
        self.release.wait(20)

    def enqueue(self, urls, options=None, overrides=None):
        self.enqueued.append((list(urls), options))
        return list(urls)

    def cancel(self):
        self.cancelled.set()
        self.release.set()


def pump(app, condition=None, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None


class WindowTests(unittest.TestCase):
    def setUp(self):
        from studio import StudioApp
        self.StudioApp = StudioApp
        for patcher in (mock.patch.object(updater, 'check_latest', lambda *a, **k: None),
                        mock.patch.object(StudioApp, 'clipboard_get', lambda *a, **k: ''),
                        mock.patch.object(engine, 'PACE_SECONDS', (0, 0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        engine.save_unfinished([])
        FakeRunner.instances = []
        self.addCleanup(lambda: [runner.release.set() for runner in FakeRunner.instances])

    def make_app(self):
        import customtkinter as ctk
        _sandbox.write_settings()
        ctk.set_appearance_mode('light')
        app = self.StudioApp(_sandbox.app_args())
        app.withdraw()
        self.addCleanup(self.close, app)
        app.update_idletasks()
        app.output.set(str(_sandbox.out('window-downloads')))
        app.mode.set(MODES[0])
        return app

    @staticmethod
    def close(app):
        try:
            if app.winfo_exists():
                app.destroy()
        except tk.TclError:
            pass

    def test_a_failing_history_never_breaks_enqueue(self):
        app = self.make_app()
        app.urls.insert('1.0', 'https://example.org/first')
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
            self.assertTrue(app.busy)
            self.assertTrue(app.download_job)
            app.urls.insert('1.0', 'https://example.org/late')
            with mock.patch.object(app.library, 'extend_job', side_effect=sqlite3.OperationalError('database is locked')) as extend, \
                    mock.patch.object(ui_download.applog.get('ui'), 'exception') as logged:
                self.assertEqual(app.enqueue_links(), 1)
            self.assertEqual(extend.call_count, 1)
            self.assertEqual(logged.call_count, 1)
            self.assertEqual(FakeRunner.instances[-1].enqueued[-1][0], ['https://example.org/late'])
            self.assertEqual([model['url'] for model in app.row_model],
                             ['https://example.org/first', 'https://example.org/late'])
            self.assertEqual(ui_theme.full_text(app.link_hint), '已加入队列 1 条')
            self.assertEqual(app.urls.get('1.0', 'end-1c'), '')
            self.assertTrue(app.busy)
            FakeRunner.instances[-1].release.set()
            app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [],
                                'pending_urls': [], 'failed_path': None})
        self.assertFalse(app.busy)

    def test_enqueue_records_the_links_in_the_running_job(self):
        """Without base_options on the runner the options of the job (job_options) are the base."""
        app = self.make_app()
        app.urls.insert('1.0', 'https://example.org/first')
        with mock.patch.object(ui_download, 'Runner', FakeRunner):
            app.start()
            job = app.download_job
            app.urls.insert('1.0', 'https://example.org/same')
            self.assertEqual(app.enqueue_links(), 1)
            app.mode.set(OTHER_MODE)
            app.urls.insert('1.0', 'https://example.org/other')
            self.assertEqual(app.enqueue_links(), 1)
            data = app.library.job(job)['data']
            self.assertEqual(data['options']['urls'],
                             ['https://example.org/first', 'https://example.org/same', 'https://example.org/other'])
            self.assertEqual(sorted(data['options'].get('overrides') or {}), ['https://example.org/other'])
            self.assertEqual(data['options']['overrides']['https://example.org/other']['mode'], OTHER_MODE)
            FakeRunner.instances[-1].release.set()
            app.handle('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [],
                                'pending_urls': [], 'failed_path': None})


class RecordTests(unittest.TestCase):
    """record_enqueued on its own, with the real Library and the real Runner._queued payload."""

    def setUp(self):
        self.lib = library.Library(_sandbox.out('record', f'{self.id().rsplit(".", 1)[-1]}.sqlite3'))
        self.options = Options(_sandbox.out('record-downloads'))
        self.job = self.lib.create_job('download', '1 条', {**self.options.to_dict(), 'urls': ['https://example.org/a']})
        self.runner = engine.Runner(lambda *_: None)
        self.runner.base_options = self.options
        self.window = SimpleNamespace(download_job=self.job, library=self.lib, job_options=(self.options, {}))

    def record(self, accepted, options):
        ui_download.DownloadUI.record_enqueued(self.window, self.runner, accepted, options)

    def data(self):
        return self.lib.job(self.job)['data']

    def test_the_later_queued_event_changes_nothing(self):
        other = Options(_sandbox.out('record-downloads'), OTHER_MODE)
        self.record(['https://example.org/b'], self.options)
        self.record(['https://example.org/c', 'https://example.org/a'], other)
        before = self.data()
        moved = [QueueItem(2, 'https://example.org/b', self.options), QueueItem(3, 'https://example.org/c', other),
                 QueueItem(4, 'https://example.org/a', other)]
        self.lib.download_event(self.job, 'queued', self.runner._queued(moved, 4))
        after = self.data()
        self.assertEqual(after['options'], before['options'])
        downloads = job_downloads(after)
        self.assertEqual([(entry['url'], entry['options']['mode']) for entry in downloads],
                         [('https://example.org/a', self.options.mode), ('https://example.org/b', self.options.mode),
                          ('https://example.org/c', OTHER_MODE), ('https://example.org/a', OTHER_MODE)])

    def test_no_job_no_write(self):
        self.window.download_job = None
        with mock.patch.object(self.lib, 'extend_job') as extend:
            self.record(['https://example.org/b'], self.options)
        extend.assert_not_called()

    def test_a_failing_library_is_only_logged(self):
        with mock.patch.object(self.lib, 'extend_job', side_effect=RuntimeError('broken')), \
                mock.patch.object(ui_download.applog.get('ui'), 'exception') as logged:
            self.record(['https://example.org/b'], self.options)        # raises nothing
        self.assertEqual(logged.call_count, 1)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'child':
        child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
