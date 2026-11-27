# quick
"""The three engine blocks that test_protected_blocks.py pinned as ASTs, by their behaviour (2.0.0 S0).

    python -X utf8 -B test_engine_blocks.py

test_protected_blocks.py (removed in 2.0.0 S2) compared three blocks of engine.py with the frozen 1.7.0 source:
Runner._run_once, the try in Runner.download's finally that calls unfinished.merge, and the while
loop of Runner._cooled.  Most of what they do has behaviour tests (test_golden_runonce,
test_batch_resilience, test_queue, test_golden_events, test_review_state, test_unfinished,
test_v170_core; see docs/2.0.0/handoff/S0.md); this file adds what was only pinned, with the message
keys rather than the Chinese texts:

  _run_once   the waiting events of a yt-dlp sleep carry the reason core.wait.pace_or_retry (S1), a
              countdown per changed second and resuming when other output arrives; an archive
              record that cannot be saved logs core.engine.archive_info_failed (S2), is counted in
              archive_errors and the archive event still reaches the window; Stop before the start
              runs nothing; (S0 review fix) a file reported twice is listed once, the countdown
              rounds up (no resume before the sleep is over), the largest Retry-After wins, empty
              lines are skipped -- these four were pinned only by test_golden_runonce; (2.0.0 S2 P1)
              a private TEMP that cannot be made leaves the engine the process TEMP, a sealed
              command reads its output through _EngineOutput (the sealed marker is the engine's,
              a plain one a log line), a reader that does not end within join(2) does not hold
              the call up, and a stdout.close() that raises OSError is ignored
  merge       an unfinished list that cannot be saved (OSError or any other error) logs
              core.engine.unfinished_not_saved (S3) and done is still the last event; (S0 review
              fix) the notices of the merge reach the download log: a list that cannot be read
              completely is set aside as failed-downloads.bad-<time>.json and says so
              (core.unfinished.kept_aside), a merged list over the size limit moves its oldest
              entries to failed-downloads.overflow-<time>.json and says so (core.unfinished.overflow)
  _cooled     an engine that cannot start: core.engine.cannot_start_engine (S4), engine_error, no
              retry; 429: cooldowns 300 s, 600 s (capped at 900 s, raised to Retry-After) with
              core.wait.rate_retry and its attempt (S7), then core.engine.rate_limited_out (S5) and,
              when anything still waits (a pending item or a link enqueue() took meanwhile), one
              pause of the whole queue with core.wait.rate_queue (S6); Stop during a cooldown ends
              the loop; the attempt count is shared by the reruns of one item
  queued      (2.0.0 S2 P1) a queued event at the end of a download that cannot be sent is only
              logged (applog 'queued event failed'); done is still the last event
"""
import _sandbox
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import engine
import i18n
import messages
import paths
import time
import unfinished
from messages import Message

_sandbox.private_bin()   # (2.0.5) the children start in paths.BIN; this test needs no vendor folder

PRELUDE = ('import json, os, sys, time\n'
           'sys.stdout.reconfigure(encoding="utf-8", newline="\\n")\n'
           'def say(*lines):\n'
           '    sys.stdout.write("".join(line + "\\n" for line in lines))\n'
           '    sys.stdout.flush()\n'
           'def wait_for(path):\n'
           '    deadline = time.monotonic() + 30\n'
           '    while not os.path.exists(path) and time.monotonic() < deadline:\n'
           '        time.sleep(0.01)\n')


def key_of(value):
    return value.key if isinstance(value, Message) else None


class RunOnceTests(unittest.TestCase):
    def run_child(self, body, hook=None, prepare=None, files=None):
        """(events, result) of Runner._run_once on a child that prints ``body``'s lines."""
        with tempfile.TemporaryDirectory() as directory:
            signal = Path(directory) / 'continue.flag'
            events = []

            def emit(kind, value):
                events.append((kind, value))
                if hook:
                    hook(kind, value, signal)

            runner = engine.Runner(emit)
            if prepare:
                prepare(runner, Path(directory))
            command = [sys.executable, '-c', PRELUDE + body, str(signal)]
            watchdog = threading.Timer(60, runner.cancel)
            watchdog.daemon = True
            watchdog.start()
            try:
                result = runner._run_once(command, 2, 5, [] if files is None else files)
            finally:
                watchdog.cancel()
            return events, result, runner

    def test_a_sleep_waits_with_the_pace_or_retry_reason(self):
        def hook(kind, value, signal):
            if kind == 'waiting':
                signal.touch()

        events, result, _runner = self.run_child(
            'say("[download] Sleeping 3.0 seconds as required by the site...")\n'
            'wait_for(sys.argv[1])\n'
            'say("__GUI_PROGRESS__50|100|NA|10|5|downloading")\n', hook)
        self.assertEqual(result, (0, False, None))
        waiting = [value for kind, value in events if kind == 'waiting']
        self.assertGreaterEqual(len(waiting), 1)
        for value in waiting:
            self.assertEqual(key_of(value['reason']), 'core.wait.pace_or_retry')
            self.assertIs(value['reason'], engine.WAIT_PACE_OR_RETRY)
            self.assertEqual((value['index'], value['total']), (2, 5))
            self.assertIn(value['seconds'], (1, 2, 3))
        self.assertEqual(len({value['seconds'] for value in waiting}), len(waiting), 'one event per changed second')
        kinds = [kind for kind, _value in events]
        self.assertEqual(kinds[0], 'log')
        self.assertIn('resuming', kinds)
        self.assertLess(kinds.index('waiting'), kinds.index('resuming'))
        self.assertEqual(kinds[kinds.index('resuming') - 1:kinds.index('resuming') + 1], ['progress', 'resuming'],
                         'other output ends the wait at once')
        self.assertEqual(dict(events)['resuming'], {'index': 2, 'total': 5})
        with _sandbox.language('en'):
            self.assertEqual(messages.show(waiting[0]['reason']), i18n.t('core.wait.pace_or_retry'))

    def test_an_archive_record_that_cannot_be_saved(self):
        class Archive:
            calls = []

            def media(self, folder, metadata):
                self.calls.append(metadata)
                raise ValueError('not saved')

        def prepare(runner, directory):
            runner.archive, runner.archive_folder = Archive(), directory

        events, result, runner = self.run_child(
            'say("__GUI_ARCHIVE__" + json.dumps({"id": "x1", "title": "T"}))\n', prepare=prepare)
        self.assertEqual(result, (0, False, None))
        logged = [value for kind, value in events if kind == 'log']
        self.assertEqual([key_of(value) for value in logged], ['core.engine.archive_info_failed'])
        self.assertEqual(runner.archive_errors, ['not saved'])
        kinds = [kind for kind, _value in events]
        self.assertEqual(kinds, ['log', 'archive'], 'the failure is logged, the record still reaches the window')
        self.assertEqual(Archive.calls, [{'id': 'x1', 'title': 'T'}])

    def test_a_file_reported_twice_is_listed_once(self):
        files = ['C:/media/old.mp4']
        events, result, _runner = self.run_child(
            'say("__GUI_FILE__" + json.dumps("C:/media/a.mp4"), "__GUI_FILE__" + json.dumps("C:/media/a.mp4"),\n'
            '    "__GUI_FILE__" + json.dumps("C:/media/old.mp4"))\n', files=files)
        self.assertEqual(result, (0, False, None))
        self.assertEqual(files, ['C:/media/old.mp4', 'C:/media/a.mp4'])
        self.assertEqual([kind for kind, _value in events], ['file', 'file', 'file'], 'every line is still an event')

    def test_the_countdown_rounds_up_and_resumes_only_when_the_sleep_is_over(self):
        stamps = []

        def hook(kind, value, signal):
            stamps.append((kind, time.monotonic()))
            if kind == 'resuming':
                signal.touch()

        events, result, _runner = self.run_child(
            'say("[download] Sleeping 1.5 seconds as required by the site...")\n'
            'wait_for(sys.argv[1])\n', hook)
        self.assertEqual(result, (0, False, None))
        waiting = [value['seconds'] for kind, value in events if kind == 'waiting']
        self.assertEqual(waiting[0], 2, '1.5 s left is shown as 2 (rounded up)')
        self.assertEqual(waiting, sorted(waiting, reverse=True))
        self.assertNotIn(0, waiting)
        kinds = [kind for kind, _value in events]
        self.assertEqual(kinds.count('resuming'), 1)
        started = next(at for kind, at in stamps if kind == 'log')
        resumed = next(at for kind, at in stamps if kind == 'resuming')
        self.assertGreaterEqual(resumed - started, 1.3, 'no resume before the 1.5 s sleep is over')

    def test_the_largest_retry_after_wins(self):
        _events, result, _runner = self.run_child(
            'say("WARNING: HTTP Error 503, Retry-After: 120", "WARNING: Retry-After: 60", "WARNING: retry-after=90")\n')
        self.assertEqual(result, (0, False, 120))

    def test_empty_lines_are_skipped(self):
        events, result, _runner = self.run_child('say("first", "", "second")\n'
                                                 'sys.stdout.write("\\r\\n\\n"); sys.stdout.flush()\n')
        self.assertEqual(result, (0, False, None))
        self.assertEqual(events, [('log', 'first'), ('log', 'second')])

    def test_a_private_temp_that_cannot_be_made_leaves_the_process_temp(self):
        """(2.0.0 S2 P1) paths.run_dir raising OSError: the engine runs with the TEMP of the process."""
        with mock.patch.object(engine.paths, 'run_dir', side_effect=OSError(28, 'no space')) as run_dir:
            events, result, _runner = self.run_child('say(os.environ.get("TEMP", ""), os.environ.get("TMP", ""))\n')
        run_dir.assert_called_once()
        self.assertEqual(result, (0, False, None))
        self.assertEqual(events, [('log', os.environ['TEMP']), ('log', os.environ['TMP'])])

    def test_a_sealed_command_reads_through_the_engine_output_wrapper(self):
        """(2.0.0 S2 P1) A command with a private marker in --print is sealed: the line with this run's
        sealed marker is the engine's own (a file event), one with the plain marker only a log line."""
        body = ('template = sys.argv[sys.argv.index("--print") + 1]\n'
                'seal = template[:template.index("FILE__")]\n'
                'say(seal + "FILE__" + json.dumps("C:/media/sealed.mp4"),\n'
                '    "__GUI_FILE__" + json.dumps("C:/media/forged.mp4"))\n')
        with tempfile.TemporaryDirectory() as directory:
            events = []
            runner = engine.Runner(lambda kind, value: events.append((kind, value)))
            files = []
            command = [sys.executable, '-c', PRELUDE + body, str(Path(directory) / 'flag'),
                       '--print', engine.MARKER + 'FILE__%(filepath)j']
            sealed, changed = runner._seal_command(command)
            self.assertTrue(changed)
            self.assertNotIn(engine.MARKER + 'FILE__', sealed[-1])
            wrapped = []
            real = engine._EngineOutput

            def wrapper(stream, seal):
                wrapped.append(seal)
                return real(stream, seal)
            with mock.patch.object(engine, '_EngineOutput', side_effect=wrapper):
                result = runner._run_once(command, 1, 1, files)
        self.assertEqual(result, (0, False, None))
        self.assertEqual(wrapped, [runner._seal])
        self.assertEqual(files, ['C:/media/sealed.mp4'])
        self.assertEqual([kind for kind, _value in events], ['file', 'log'])
        self.assertEqual(events[0][1], 'C:/media/sealed.mp4')
        self.assertIn('C:/media/forged.mp4', events[1][1])

    @staticmethod
    def fake_process(stream):
        return types.SimpleNamespace(stdout=stream, wait=lambda: 0, poll=lambda: 0)

    def test_a_reader_that_does_not_end_does_not_hold_the_call(self):
        """(2.0.0 S2 P1) reader.join(2) times out (the output never ends by itself): after Stop the
        call returns about 2 s later; closing stdout then ends the reader; the process is cleared."""
        class Stream:
            def __init__(self):
                self.closed, self.ended, self.closes = threading.Event(), threading.Event(), 0

            def __iter__(self):
                self.closed.wait(30)
                self.ended.set()
                raise ValueError('I/O operation on closed file')

            def close(self):
                self.closes += 1
                self.closed.set()
        stream = Stream()
        runner = engine.Runner(lambda kind, value: None)
        runner.job = mock.Mock()
        runner._terminate_process = lambda *args: None
        timer = threading.Timer(0.3, runner.cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with mock.patch.object(engine.subprocess, 'Popen', return_value=self.fake_process(stream)):
            result = runner._run_once(['engine'], 1, 1, [])
        elapsed = time.monotonic() - started
        self.assertEqual(result, (0, False, None))
        self.assertGreaterEqual(elapsed, 2.0, 'join(2) waited its 2 s')
        self.assertLess(elapsed, 20)
        self.assertEqual(stream.closes, 1)
        self.assertTrue(stream.ended.wait(10), 'closing stdout ended the reader')
        self.assertIsNone(runner.process)

    def test_a_stdout_close_that_raises_is_ignored(self):
        """(2.0.0 S2 P1) stdout.close() raising OSError: the result stands, the process is cleared and
        the private TEMP folder is removed."""
        class Stream:
            def __iter__(self):
                yield 'a line\n'

            def close(self):
                raise OSError(5, 'denied')
        made = []
        real = engine.paths.run_dir

        def run_dir(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]
        events = []
        runner = engine.Runner(lambda kind, value: events.append((kind, value)))
        runner.job = mock.Mock()
        with mock.patch.object(engine.subprocess, 'Popen', return_value=self.fake_process(Stream())), \
                mock.patch.object(engine.paths, 'run_dir', side_effect=run_dir):
            result = runner._run_once(['engine'], 1, 1, [])
        self.assertEqual(result, (0, False, None))
        self.assertEqual(events, [('log', 'a line')])
        self.assertIsNone(runner.process)
        self.assertEqual(len(made), 1)
        self.assertFalse(Path(made[0]).exists(), 'the private TEMP folder is removed')

    def test_stop_before_the_start_runs_nothing(self):
        runner = engine.Runner(lambda kind, value: None)
        runner.cancelled.set()
        with mock.patch.object(engine.subprocess, 'Popen') as popen:
            self.assertEqual(runner._run_once([sys.executable, '-c', 'pass'], 1, 1, []), (-1, False, None))
        popen.assert_not_called()


class CooledTests(unittest.TestCase):
    """Runner._cooled with _run_once and _wait replaced (no process, no waiting)."""

    def runner(self, runs, waits=None, pending=True, inbox=()):
        events = []
        runner = engine.Runner(lambda kind, value: events.append((kind, value)))
        runner.items = [types.SimpleNamespace(state='active')] + \
            ([types.SimpleNamespace(state='pending')] if pending else [types.SimpleNamespace(state='done')])
        runner.inbox = list(inbox)
        results = list(runs)
        waited = []

        def run_once(command, index, total, files):
            outcome = results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def wait(seconds, index, total, reason):
            waited.append((seconds, reason.key, dict(reason.params)))
            return True if waits is None else waits.pop(0)

        runner._run_once = run_once
        runner._wait = wait
        return runner, events, waited, results

    @staticmethod
    def logged(events):
        return [key_of(value) for kind, value in events if kind == 'log']

    def test_the_cooldown_ladder_then_the_queue_pause(self):
        runner, events, waited, left = self.runner([(1, True, None)] * 3)
        self.assertEqual(runner._cooled(['cmd'], 1, []), (1, False))
        self.assertEqual(left, [])
        self.assertEqual(waited, [(300, 'core.wait.rate_retry', {'attempt': 1, 'limit': 2}),
                                  (600, 'core.wait.rate_retry', {'attempt': 2, 'limit': 2}),
                                  (900, 'core.wait.rate_queue', {})])
        self.assertEqual(self.logged(events), ['core.engine.rate_limited_out'])
        self.assertEqual((engine.RATE_LIMIT_COOLDOWN, engine.MAX_RATE_LIMIT_COOLDOWN, engine.MAX_RATE_LIMIT_RETRIES),
                         (300, 900, 2))

    def test_retry_after_raises_the_cooldown(self):
        runner, _events, waited, _left = self.runner([(1, True, 1200), (1, True, 10), (1, True, 1200)])
        runner._cooled(['cmd'], 1, [])
        self.assertEqual([seconds for seconds, _key, _params in waited], [1200, 600, 1200])

    def test_nothing_waits_no_queue_pause(self):
        runner, events, waited, _left = self.runner([(1, True, None)] * 3, pending=False)
        self.assertEqual(runner._cooled(['cmd'], 1, []), (1, False))
        self.assertEqual([key for _seconds, key, _params in waited], ['core.wait.rate_retry'] * 2)
        self.assertEqual(self.logged(events), ['core.engine.rate_limited_out'])

    def test_a_link_enqueued_meanwhile_counts_as_waiting(self):
        runner, _events, waited, _left = self.runner([(1, True, None)] * 3, pending=False, inbox=['queued'])
        runner._cooled(['cmd'], 1, [])
        self.assertEqual(waited[-1][1], 'core.wait.rate_queue')

    def test_a_zero_result_of_the_last_run_still_fails_the_item(self):
        runner, _events, _waited, _left = self.runner([(1, True, None), (1, True, None), (0, True, None)])
        self.assertEqual(runner._cooled(['cmd'], 1, []), (1, False))

    def test_stop_during_a_cooldown_ends_the_loop(self):
        runner, events, waited, left = self.runner([(1, True, None)] * 3, waits=[False])
        self.assertEqual(runner._cooled(['cmd'], 1, []), (1, False))
        self.assertEqual(len(waited), 1)
        self.assertEqual(len(left), 2, 'no further run')
        self.assertEqual(self.logged(events), [])

    def test_stop_during_a_run_ends_the_loop(self):
        runner, events, waited, left = self.runner([(1, True, None)] * 3)
        original = runner._run_once

        def run_and_stop(*args):
            outcome = original(*args)
            runner.cancelled.set()
            return outcome
        runner._run_once = run_and_stop
        self.assertEqual(runner._cooled(['cmd'], 1, []), (1, False))
        self.assertEqual((waited, len(left)), ([], 2))

    def test_stop_before_the_loop_runs_nothing(self):
        runner, _events, _waited, left = self.runner([(0, False, None)])
        runner.cancelled.set()
        self.assertEqual(runner._cooled(['cmd'], 1, []), (None, False))
        self.assertEqual(len(left), 1)

    def test_no_rate_limit_one_run(self):
        runner, events, waited, left = self.runner([(0, False, None), (0, False, None)])
        self.assertEqual(runner._cooled(['cmd'], 1, []), (0, False))
        self.assertEqual((waited, len(left), events), ([], 1, []))

    def test_the_attempts_are_shared_by_the_reruns_of_an_item(self):
        runner, events, waited, _left = self.runner([(1, True, None)] * 2)
        runner._rate_attempt = 1
        runner._cooled(['cmd'], 1, [])
        self.assertEqual([(seconds, params.get('attempt')) for seconds, _key, params in waited],
                         [(600, 2), (900, None)])
        self.assertEqual(self.logged(events), ['core.engine.rate_limited_out'])

    def test_an_engine_that_cannot_start(self):
        runner, events, waited, left = self.runner([OSError(2, 'missing'), (0, False, None)])
        self.assertEqual(runner._cooled(['cmd'], 1, []), (None, True))
        self.assertTrue(runner.engine_error)
        self.assertEqual(self.logged(events), ['core.engine.cannot_start_engine'])
        self.assertEqual((waited, len(left)), ([], 1))


class MergeTests(unittest.TestCase):
    """The unfinished list at the end of Runner.download (unfinished.merge / save)."""

    def setUp(self):
        self.clean()
        self.addCleanup(self.clean)

    @staticmethod
    def clean():
        for path in paths.failed_file().parent.glob('failed-downloads*'):
            path.unlink()

    @staticmethod
    def leftovers():
        return sorted(path.name for path in paths.failed_file().parent.glob('failed-downloads.*-*.json'))

    def download(self, failure=None, url='https://media.example.invalid/merge'):
        events = []
        runner = engine.Runner(lambda kind, value: events.append((kind, value)))
        engine.Runner.reset_pacing()
        output = _sandbox.out('engine-blocks')
        saving = mock.patch.object(unfinished, 'save', side_effect=failure) if failure else contextlib.nullcontext()
        with mock.patch.object(engine, 'PACE_SECONDS', (0, 0)), \
                mock.patch.object(runner, '_run_once', return_value=(1, False, None)), \
                mock.patch.object(runner, '_wait', return_value=True), saving:
            runner.download([url], engine.Options(output, batch_safe=False))
        return events

    def test_a_list_that_cannot_be_read_is_kept_aside_and_the_log_says_so(self):
        damaged = '{"version": 1, "entries": ['
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_text(damaged, encoding='utf-8')
        events = self.download()
        self.assertEqual(events[-1][0], 'done')
        logged = [value for kind, value in events if kind == 'log']
        kept = [value for value in logged if key_of(value) == 'core.unfinished.kept_aside']
        self.assertEqual(len(kept), 1, 'the download log says where the unreadable list went')
        bad = [name for name in self.leftovers() if name.startswith('failed-downloads.bad-')]
        self.assertEqual(len(bad), 1)
        self.assertEqual(kept[0].params['target'], bad[0])
        self.assertEqual(paths.failed_file().parent.joinpath(bad[0]).read_text(encoding='utf-8'), damaged,
                         'kept as it was')
        stored = json.loads(paths.failed_file().read_text(encoding='utf-8'))
        self.assertEqual([item['url'] for item in stored['entries']], ['https://media.example.invalid/merge'])
        self.assertEqual(events[-1][1]['failed_path'], str(paths.failed_file()))

    def test_a_merged_list_over_the_limit_moves_its_oldest_entries_and_the_log_says_so(self):
        options = engine.Options(_sandbox.out('engine-blocks')).to_dict()
        old = [dict(unfinished.entry(f'https://media.example.invalid/old-{n}', options, 'failed', error='x' * 150),
                    time=1000.0 + n) for n in range(40)]
        unfinished.save(old)
        with mock.patch.object(unfinished, 'SAFE_SIZE', 4000):
            events = self.download()
        self.assertEqual(events[-1][0], 'done')
        overflow = [value for kind, value in events if kind == 'log' and key_of(value) == 'core.unfinished.overflow']
        self.assertEqual(len(overflow), 1, 'the download log says where the oldest entries went')
        moved = [name for name in self.leftovers() if name.startswith('failed-downloads.overflow-')]
        self.assertEqual(len(moved), 1)
        self.assertEqual(overflow[0].params['target'], moved[0])
        kept = json.loads(paths.failed_file().read_text(encoding='utf-8'))['entries']
        aside = json.loads(paths.failed_file().parent.joinpath(moved[0]).read_text(encoding='utf-8'))['entries']
        self.assertEqual(overflow[0].params['count'], len(aside))
        self.assertEqual(sorted(item['url'] for item in kept + aside),
                         sorted([item['url'] for item in old] + ['https://media.example.invalid/merge']),
                         'no link is lost')
        self.assertIn('https://media.example.invalid/merge', [item['url'] for item in kept], 'the newest stays')

    def test_a_queued_event_that_cannot_be_sent_is_only_logged(self):
        """(2.0.0 S2 P1) A link enqueue() took while the queue ended abnormally is moved in the finally
        block; its queued event raises: applog records 'queued event failed', done is still last."""
        events = []
        late = 'https://media.example.invalid/late'

        def emit(kind, value):
            if kind == 'queued':
                raise RuntimeError('window gone')
            events.append((kind, value))
        runner = engine.Runner(emit)
        engine.Runner.reset_pacing()

        def run_once(*_args):
            self.assertEqual(runner.enqueue([late]), [late])
            raise RuntimeError('engine broke')
        logger = mock.Mock()
        with mock.patch.object(engine, 'PACE_SECONDS', (0, 0)), \
                mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True), \
                mock.patch.object(engine.applog, 'get', return_value=logger):
            runner.download(['https://media.example.invalid/first'],
                            engine.Options(_sandbox.out('engine-blocks'), batch_safe=False))
        self.assertEqual(events[-1][0], 'done', 'done is the last event')
        logged = [call.args[0] for call in logger.exception.call_args_list]
        self.assertIn('queued event failed', logged)
        self.assertNotIn('queued', [kind for kind, _value in events])

    def test_a_list_that_cannot_be_saved(self):
        for failure in (OSError(13, 'denied'), RuntimeError('broken')):
            with self.subTest(failure=type(failure).__name__):
                events = self.download(failure)
                self.assertEqual(events[-1][0], 'done', 'done is the last event')
                logged = [key_of(value) for kind, value in events if kind == 'log']
                self.assertIn('core.engine.unfinished_not_saved', logged)
                self.assertIsNone(events[-1][1]['failed_path'])
                self.assertEqual(events[-1][1]['failed'], 1)


if __name__ == '__main__':
    unittest.main()
