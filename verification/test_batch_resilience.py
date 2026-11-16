"""Deterministic queue tests. No external websites or media are requested."""
from __future__ import annotations

import _sandbox
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


import engine


class BatchResilienceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state_patch = _sandbox.state_patch(self.root / 'state')
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        # 1.6.0: these tests are not about the spacing between downloads (test_queue.py is).
        engine.Runner.reset_pacing()
        pace = patch.object(engine, 'PACE_SECONDS', (0, 0))
        pace.start()
        self.addCleanup(pace.stop)
        self.urls = ['https://example.invalid/first', 'https://example.invalid/second']
        self.options = engine.Options(self.root / 'downloads')
        self.events = []
        self.runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))

    def done(self):
        return next(value for kind, value in reversed(self.events) if kind == 'done')

    @staticmethod
    def flag_values(command, flag):
        return [command[i + 1] for i, word in enumerate(command[:-1]) if word == flag]

    def test_safe_mode_has_random_download_interval_and_bounded_backoff(self):
        command, listed = (engine.build_command(self.urls[0], replace(self.options, playlist=on)) for on in (False, True))
        pacing = ('--sleep-interval', '--max-sleep-interval', '--sleep-requests')
        self.assertEqual([self.flag_values(command, flag) for flag in pacing], [[], [], ['1']])
        self.assertEqual([self.flag_values(listed, flag) for flag in pacing], [['10'], ['20'], ['1']])
        self.assertEqual(self.flag_values(command, '--retries'), ['5'])
        self.assertEqual(self.flag_values(command, '--fragment-retries'), ['5'])
        # --print otherwise implies quiet mode and hides the real engine sleep log.
        self.assertIn('--no-quiet', command)
        retry_sleeps = self.flag_values(command, '--retry-sleep')
        for category in ('http', 'fragment', 'extractor'):
            expression = next(value.split(':', 1)[1] for value in retry_sleeps
                              if value.startswith(category + ':'))
            self.assertTrue(expression.startswith(('exp=', 'linear=')), expression)
            self.assertLessEqual(float(expression.split(':')[1]), 60)
        self.assertEqual(command[-2:], ['--', self.urls[0]])

    def test_safe_mode_can_be_disabled_without_losing_network_retry(self):
        options = engine.Options(self.root / 'downloads', batch_safe=False)
        command = engine.build_command(self.urls[0], options)
        for flag in ('--sleep-interval', '--max-sleep-interval', '--sleep-requests'):
            self.assertNotIn(flag, command)
        self.assertEqual(self.flag_values(command, '--retries'), ['5'])

    def test_failure_continues_to_next_link_and_saves_only_failed_links(self):
        visited = []

        def run(command, index, total, files):
            visited.append(command[-1])
            if index == 1:
                return 1, False, None
            files.append(str(self.options.output / 'completed.wav'))
            return 0, False, None

        with patch.object(self.runner, '_run_once', side_effect=run):
            self.runner.download(self.urls, self.options)
        self.assertEqual(visited, self.urls)
        result = self.done()
        self.assertEqual((result['success'], result['failed']), (1, 1))
        self.assertEqual(result['failed_urls'], self.urls[:1])
        job = engine.load_failed_job()
        self.assertEqual(job['urls'], self.urls[:1])
        self.assertEqual(job['options'], self.options)

    def test_rate_limit_holds_queue_and_retries_same_link_before_next(self):
        trace = []
        attempts = {url: 0 for url in self.urls}

        def run(command, index, total, files):
            url = command[-1]
            trace.append(('run', url))
            attempts[url] += 1
            return (1, True, None) if url == self.urls[0] else (0, False, None)

        def wait(seconds, index, total, reason):
            trace.append(('wait', seconds))
            return True

        with patch.object(self.runner, '_run_once', side_effect=run), \
                patch.object(self.runner, '_wait', side_effect=wait):
            self.runner.download(self.urls, self.options)
        self.assertEqual(attempts[self.urls[0]], engine.MAX_RATE_LIMIT_RETRIES + 1)
        self.assertEqual(attempts[self.urls[1]], 1)
        for i, entry in enumerate(trace[:-1]):
            if entry == ('run', self.urls[0]):
                self.assertEqual(trace[i + 1][0], 'wait', trace)
        waits = [entry[1] for entry in trace if entry[0] == 'wait']
        self.assertGreaterEqual(waits[0], engine.RATE_LIMIT_COOLDOWN)
        self.assertEqual(waits, sorted(waits))
        self.assertLessEqual(max(waits), engine.MAX_RATE_LIMIT_COOLDOWN)
        self.assertEqual(self.done()['failed_urls'], self.urls[:1])

    def test_rate_limit_then_success_is_counted_once(self):
        results = iter([(1, True, None), (0, False, None)])
        with patch.object(self.runner, '_run_once', side_effect=lambda *args: next(results)), \
                patch.object(self.runner, '_wait', return_value=True) as wait:
            self.runner.download(self.urls[:1], self.options)
        self.assertEqual(wait.call_count, 1)
        self.assertEqual((self.done()['success'], self.done()['failed']), (1, 0))
        self.assertEqual(self.done()['failed_urls'], [])

    def test_stop_during_rate_cooldown_does_not_start_another_attempt(self):
        def stop_wait(*args):
            self.runner.cancel()
            return False

        with patch.object(self.runner, '_run_once', return_value=(1, True, None)) as run, \
                patch.object(self.runner, '_wait', side_effect=stop_wait):
            self.runner.download(self.urls, self.options)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.done()['cancelled'])
        self.assertEqual(self.done()['success'], 0)
        self.assertEqual(self.done()['pending_urls'], self.urls)
        self.assertEqual(engine.load_failed_job()['urls'], self.urls)

    def test_retry_after_header_is_respected(self):
        results = iter([(1, True, 650), (0, False, None)])
        with patch.object(self.runner, '_run_once', side_effect=lambda *args: next(results)), \
                patch.object(self.runner, '_wait', return_value=True) as wait:
            self.runner.download(self.urls[:1], self.options)
        self.assertGreaterEqual(wait.call_args.args[0], 650)
        self.assertEqual(self.done()['failed'], 0)

    def test_retry_clears_successful_urls_but_retains_unstarted_links(self):
        engine.store_failed_job(self.urls, self.options)
        with patch.object(self.runner, '_run_once', return_value=(0, False, None)):
            self.runner.download(self.urls[:1], self.options)
        self.assertEqual(engine.load_failed_job()['urls'], self.urls[1:])
        next_runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with patch.object(next_runner, '_run_once', return_value=(0, False, None)):
            next_runner.download(self.urls[1:], self.options)
        self.assertIsNone(engine.load_failed_job())

    def test_success_in_different_format_does_not_clear_prior_failed_job(self):
        video = engine.Options(self.root / 'original-video', engine.MODES[4], '2160p / 4K', True)
        engine.store_failed_job(self.urls[:1], video)
        audio = engine.Options(self.root / 'new-audio', engine.MODES[3])
        with patch.object(self.runner, '_run_once', return_value=(0, False, None)):
            self.runner.download(self.urls[:1], audio)
        self.assertEqual(self.done()['success'], 1)
        prior = engine.load_failed_job()
        self.assertIsNotNone(prior, 'Different-format success discarded an unfinished video job')
        self.assertEqual(prior['urls'], self.urls[:1])
        self.assertEqual(prior['options'], video)

    def test_wait_is_cancellable_without_waiting_for_cooldown(self):
        waiting = threading.Event()
        result = []

        def emit(kind, value):
            self.events.append((kind, value))
            if kind == 'waiting':
                waiting.set()

        self.runner.emit = emit
        thread = threading.Thread(target=lambda: result.append(
            self.runner._wait(300, 1, 2, '限流暂停')))
        thread.start()
        self.assertTrue(waiting.wait(2), 'Countdown was not emitted')
        started = time.monotonic()
        self.runner.cancel()
        thread.join(1)
        self.assertFalse(thread.is_alive(), 'Stop did not interrupt cooldown promptly')
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result, [False])
        self.assertFalse(any(kind == 'resuming' for kind, _ in self.events))

    def test_countdown_reports_remaining_time_then_resumes(self):
        self.assertTrue(self.runner._wait(0.03, 2, 3, '测试等待'))
        waiting = [value for kind, value in self.events if kind == 'waiting']
        self.assertTrue(waiting)
        self.assertEqual(waiting[0]['index'], 2)
        self.assertEqual(waiting[0]['total'], 3)
        self.assertGreaterEqual(waiting[0]['seconds'], 1)
        self.assertEqual(self.events[-1][0], 'resuming')

    def test_failed_job_roundtrip_retains_original_options(self):
        options = engine.Options(self.root / '原始目录', engine.MODES[4], '1080p', True,
                                 'http://127.0.0.1:7890', False)
        path = engine.store_failed_job(self.urls, options)
        self.assertTrue(Path(path).is_file())
        job = engine.load_failed_job()
        self.assertEqual(job['urls'], self.urls)
        self.assertEqual(job['options'], replace(options, proxy=''))
        # Proxy credentials are session-only; retry UI preserves the currently entered proxy.
        self.assertEqual(json.loads(Path(path).read_text(encoding='utf-8'))['options']['proxy'], '')
        engine.store_failed_job([], options)
        self.assertIsNone(engine.load_failed_job())

    def test_corrupt_failed_job_does_not_prevent_application_start(self):
        path = engine.store_failed_job(self.urls, self.options)
        Path(path).write_text('{truncated', encoding='utf-8')
        self.assertIsNone(engine.load_failed_job())

    def test_local_subprocess_429_detection_and_non_rate_error(self):
        for message, expected in [('ERROR: HTTP Error 429: Too Many Requests', True),
                                  ('ERROR: HTTP Error 403: Forbidden', False),
                                  ('Some title includes number 429', False)]:
            with self.subTest(message=message):
                command = [sys.executable, '-c',
                           'import sys; print(' + repr(message) + ', flush=True); sys.exit(1)']
                result, limited, retry_after = self.runner._run_once(command, 1, 1, [])
                self.assertNotEqual(result, 0)
                self.assertEqual(limited, expected)

    def test_youtube_bot_check_is_detected_from_engine_output(self):
        message = ('ERROR: [youtube] abc: Sign in to confirm you’re not a bot. '
                   'Use --cookies-from-browser or --cookies for the authentication.')
        command = [sys.executable, '-c',
                   'import sys; sys.stdout.reconfigure(encoding="utf-8"); print(' + repr(message) + ', flush=True); sys.exit(1)']
        result, limited, _ = self.runner._run_once(command, 1, 1, [])
        self.assertNotEqual(result, 0)
        self.assertFalse(limited)
        self.assertTrue(self.runner.bot_check)

    def test_bot_check_failure_reports_cookie_hint_per_item_and_job(self):
        outcomes = iter([(1, True), (0, False)])

        def run(*_args):
            failed, bot = next(outcomes)
            self.runner.bot_check = bot
            return failed, False, None

        with patch.object(self.runner, '_run_once', side_effect=run):
            self.runner.download(self.urls, replace(self.options, batch_safe=False))
        items = [value for kind, value in self.events if kind == 'item_done']
        self.assertEqual([item['bot_check'] for item in items], [True, False])
        self.assertTrue(self.done()['bot_check'])
        logs = [value for kind, value in self.events if kind == 'log']
        self.assertIn(engine.BOT_CHECK_HINT, logs)

    def test_failed_job_does_not_persist_cookies_path(self):
        cookies = self.root / 'cookies.txt'
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        path = engine.store_failed_job(self.urls, replace(self.options, cookies=str(cookies)))
        self.assertEqual(json.loads(Path(path).read_text(encoding='utf-8'))['options']['cookies'], '')
        self.assertEqual(engine.load_failed_job()['options'].cookies, '')

    def test_native_sleep_log_emits_countdown_and_resumes_on_progress(self):
        script = (
            'import time; '
            'print("[download] Sleeping 0.15 seconds ...", flush=True); '
            'time.sleep(0.15); '
            'print("__GUI_PROGRESS__100|100|NA|10|0|downloading", flush=True)'
        )
        result, limited, _ = self.runner._run_once([sys.executable, '-c', script], 1, 1, [])
        self.assertEqual(result, 0)
        self.assertFalse(limited)
        kinds = [kind for kind, _ in self.events]
        self.assertIn('waiting', kinds)
        self.assertIn('resuming', kinds)
        self.assertLess(kinds.index('waiting'), kinds.index('resuming'))
        self.assertIn('progress', kinds)

    def test_stop_terminates_native_sleep_child_promptly(self):
        waiting = threading.Event()
        result = []

        def emit(kind, value):
            self.events.append((kind, value))
            if kind == 'waiting':
                waiting.set()

        self.runner.emit = emit
        script = 'import time; print("[download] Sleeping 300 seconds ...", flush=True); time.sleep(300)'
        thread = threading.Thread(target=lambda: result.append(self.runner._run_once(
            [sys.executable, '-c', script], 1, 1, [])))
        thread.start()
        try:
            self.assertTrue(waiting.wait(3), 'Native sleep countdown was not detected')
            started = time.monotonic()
            self.runner.cancel()
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'Stop did not terminate native sleep')
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(self.runner.cancelled.is_set())
            self.assertNotEqual(result[0][0], 0)
        finally:
            if thread.is_alive():
                self.runner.cancel()
                thread.join(10)


if __name__ == '__main__':
    unittest.main()
