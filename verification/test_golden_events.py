"""Golden event transcripts of engine.Runner.download.

Each scenario runs the real download() with _run_once replaced by a script and _wait
replaced by a recorder, so the transcript shows exactly which events the queue logic
emits, in which order, and what it leaves in failed-downloads.json.

Two goldens:
* events-1.6.0.json (1.6.0 WP2): every scenario below on the current engine.  Written by
  this work package, so on its own it proves nothing about compatibility.  To record on
  purpose, run this file directly (not through run_tests.py) with GOLDEN_UPDATE=1.
* events-1.5.1.json (WP0, recorded from the 1.5.1 sources, never rewritten): the
  projection test runs the 1.5.1 scenarios on the current engine, drops every kind and
  key that SPEC section 3.8 marks as new, and requires the 1.5.1 transcript except for
  the differences enumerated in PERMITTED_DIFFERENCES.

Transcript entries are [kind, value]; a call of _wait appears in place as
["_wait", [seconds, index, total, reason]].
Determinism: random.uniform returns its first argument, time.monotonic is a scripted
clock (frozen unless a step moves it), and the entry time stamps are replaced.
Normalisation tokens: <OUT> output folder, <STATE> state folder, <ARCHIVE> archive
root, <COOKIES> cookies file, <TMP> the scenario's temp folder, <OSERROR> the text of
an operating-system error (it depends on the Windows language) after the app's own
prefix of the log line, <TIME> the time stamp of an unfinished entry.
"""
import _sandbox
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import urls

GOLDEN_NAME = 'events-1.6.0.json'
LEGACY_GOLDEN = 'events-1.5.1.json'
A, B, C = ('https://example.invalid/' + name for name in 'abc')

# The ten scenarios of WP0.md step 5, for which the projection is normative (WP2.md "Order").
NORMATIVE = ('two_success', 'fail_then_success', 'rate_limit_then_success', 'rate_limit_exhausted_then_next',
             'retry_after', 'cancel_during_first', 'bot_check_first_of_two', 'exception_on_second_of_three',
             'previous_job_same_options', 'previous_job_other_format')

# What the projection may differ in.  Anything else fails the test.
PERMITTED_DIFFERENCES = (
    'done.failed_path: None when nothing is stored (1.5.1 always named the file, also after writing an empty list)',
    'done.pending_urls in the scenarios of PENDING_THIS_RUN: the links of this run only (1.5.1 added the older list)',
    'failed_file: the entries key is added; the legacy urls and options are compared separately '
    '(no file where 1.5.1 wrote an empty list)',
    'log: the wording of BOT_CHECK_HINT and BOT_CHECK_STALE_HINT, which WP2.md section F replaces',
)
# done.pending_urls of 1.6.0 in the two scenarios with an older list (scenarios 9 and 10 of WP0.md).
PENDING_THIS_RUN = {'previous_job_same_options': [], 'previous_job_other_format': []}
# The same difference in a scenario outside the ten (an older list that this run did not touch).
PENDING_THIS_RUN_MORE = {'previous_job_same_options_despite_proxy_and_cookies': []}
# The two hint texts as 1.5.1 logged them (engine.py:36-39 of the backup).
OLD_BOT_CHECK_HINT = ('YouTube 要求登录验证（Sign in to confirm you’re not a bot）。这是 YouTube 对当前网络出口 IP 的限制，'
                      '不是链接解析错误。请在“偏好设置 → 网络”选择已登录 YouTube 的 cookies.txt，然后点“重试失败”。')
OLD_BOT_CHECK_STALE_HINT = ('YouTube 仍要求登录验证：所选 cookies 可能已过期或导出时未登录，'
                            '请重新导出 cookies.txt 后点“重试失败”。')
# Kinds and keys that SPEC section 3.8 marks with "+": dropped before the comparison with 1.5.1.
NEW_KINDS = ('media', 'queued')
NEW_KEYS = {'item': ('key', 'format', 'origin', 'multi'),
            'waiting': ('kind',),
            'item_done': ('url', 'origin', 'title', 'path', 'error', 'reason', 'skipped', 'stale_engine', 'format'),
            'done': ('total', 'skipped', 'blocked', 'unfinished', 'engine_error')}
# Scenarios of events-1.5.1.json outside the ten that pin behaviour 1.6.0 changes on purpose.
# The projection test requires that each of them really differs, so the list cannot go stale.
NOT_PROJECTED = {
    'output_folder_unusable':
        'robust-8: a folder that cannot be created fails each link on its own (item, 此链接无法开始, item_done) '
        'instead of aborting the queue before the first link',
    'emit_raises_after_success':
        'done.failed counts the items that failed; 1.5.1 added one for the aborted queue although the link had finished',
    'emit_raises_after_failure':
        'done.failed counts the items that failed (1); 1.5.1 counted the link and the aborted queue (2)',
    'previous_job_same_options_new_failure':
        'the list is kept per link and format: older entries keep their place and the new failure is appended '
        '(1.5.1 put this run first), and pending_urls names this run only',
    'previous_job_same_options_cancelled':
        'as above: older entries first, then this run; pending_urls names this run only',
    'previous_job_same_options_all_set':
        'as above; and archive contexts are no longer part of what makes two lists the same job',
    'previous_job_other_referers_failure':
        'robust-1: referers are per link and no longer decide whether an older list is replaced; it is kept',
    'previous_job_other_archive_contexts_failure':
        'robust-1: archive contexts no longer decide whether an older list is replaced; it is kept',
}


def report(command, files, name):
    """Report one file inside the folder the command was given, the way the real _run_once
    handles a __GUI_FILE__ line: a path that is already in the list is not added again."""
    path = str(Path(command[command.index('-P') + 1]) / name)
    if path not in files:
        files.append(path)


def ok(name):
    """A successful run that reports one file inside the folder the command was given."""
    def step(runner, command, index, total, files):
        report(command, files, name)
        return 0, False, None
    return step


def fail(runner, command, index, total, files):
    return 1, False, None


def fail_with(code):
    def step(runner, command, index, total, files):
        return code, False, None
    return step


def limited(retry_after=None, code=1):
    def step(runner, command, index, total, files):
        return code, True, retry_after
    return step


def limited_but_exit_zero(name):
    """The engine printed a 429 line, but the process had already finished its work: a file and
    exit code 0 (the loop reads the line after the process ended, so it has nothing left to stop)."""
    def step(runner, command, index, total, files):
        report(command, files, name)
        return 0, True, None
    return step


def cancel_and_fail(runner, command, index, total, files):
    runner.cancel()
    return 1, False, None


def cancel_and_limited(runner, command, index, total, files):
    """The stop arrives while a run that met a 429 line is ending."""
    runner.cancel()
    return 1, True, None


def cancel_but_ok(name):
    """The stop arrives when the process has just finished its work: a file and exit code 0."""
    def step(runner, command, index, total, files):
        runner.cancel()
        return ok(name)(runner, command, index, total, files)
    return step


def bot_check(runner, command, index, total, files):
    runner.bot_check = True
    return 1, False, None


def bot_line_but_ok(name):
    """The engine printed the bot-check line (one client was refused) and still finished the download."""
    def step(runner, command, index, total, files):
        runner.bot_check = True
        return ok(name)(runner, command, index, total, files)
    return step


def archive_error_but_ok(name):
    """The download finished, but the archive record of its file could not be written: the real
    _run_once then adds the error text to runner.archive_errors and carries on."""
    def step(runner, command, index, total, files):
        runner.archive_errors.append('归档文件与本条资料目录不匹配。')
        return ok(name)(runner, command, index, total, files)
    return step


def boom(runner, command, index, total, files):
    raise RuntimeError('boom')


class GoldenEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()

    def scenario(self, urls, steps, options=None, before=None, hook=None, wait_result=None, library=False,
                 blocked=(), os_errors=(), overrides=None, pace=None, clock=None):
        """Run one scripted download; returns the normalised {'events', 'failed_file'}.

        ``blocked`` names the folders ('out', 'state') that cannot be created because their
        parent is a file; ``os_errors`` lists the log prefixes after which this scenario
        expects the text of an operating-system error (replaced by <OSERROR>).
        ``overrides`` (a function of the places) gives per-link options; ``pace`` replaces
        engine.PACE_SECONDS; ``clock`` is the one-element list that time.monotonic reads
        (steps and hooks may move it; frozen otherwise).
        """
        clock = clock if clock is not None else [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, state, archive, cookies = root / 'out', root / 'state', root / 'archive', root / 'cookies.txt'
            for name in blocked:
                blocker = root / f'{name}-is-below-this-file.txt'
                blocker.write_text('a file, not a folder\n', encoding='utf-8')
                if name == 'out':
                    out = blocker / 'out'
                else:
                    self.assertEqual(name, 'state')
                    state = blocker / 'state'
            cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
            places = {'out': out, 'state': state, 'archive': archive, 'cookies': cookies}
            transcript, violations = [], []
            remaining = list(steps)

            def emit(kind, value):
                transcript.append([kind, copy.deepcopy(value)])
                if hook:
                    hook(runner, kind, value)

            def run_once(command, index, total, files):
                item = next((value for kind, value in reversed(transcript) if kind == 'item'), None)
                if not item or (command[-2:], index, total) != (['--', item['url']], item['index'], item['total']):
                    violations.append(f'_run_once({command[-1]}, {index}, {total}) does not match the last item event {item}')
                if not remaining:
                    violations.append(f'unexpected extra _run_once call for {command[-1]}')
                    return 1, False, None
                return remaining.pop(0)(runner, command, index, total, files)

            def wait(seconds, index, total, reason):
                transcript.append(['_wait', [seconds, index, total, reason]])
                return wait_result(runner) if wait_result else True

            engine.Runner.reset_pacing()
            self.addCleanup(engine.Runner.reset_pacing)
            with _sandbox.state_patch(state), patch('random.uniform', side_effect=lambda low, high: low), \
                    patch('time.monotonic', side_effect=lambda: clock[0]), \
                    patch.object(engine, 'PACE_SECONDS', pace if pace is not None else engine.PACE_SECONDS):
                runner = engine.Runner(emit)
                # What an earlier download would have left on the runner: download() starts clean.
                runner.archive, runner.archive_errors = object(), ['left over from an earlier download']
                if library:
                    from library import Library
                    runner.library = places['library'] = Library(root / 'library.sqlite3')
                run_options = options(places) if options else engine.Options(out)
                if before:
                    before(places, run_options)
                with patch.object(runner, '_run_once', side_effect=run_once), \
                        patch.object(runner, '_wait', side_effect=wait):
                    if overrides:
                        runner.download(urls, run_options, overrides(places))
                    else:
                        runner.download(urls, run_options)
                stored = state / 'failed-downloads.json'
                failed_file = json.loads(stored.read_text(encoding='utf-8')) if stored.is_file() else None
            for entry in (failed_file or {}).get('entries', []):
                self.assertIsInstance(entry['time'], float)
                self.assertGreater(entry['time'], 0)
                entry['time'] = '<TIME>'
            self.assertEqual(violations, [])
            self.assertEqual(remaining, [], 'the scripted _run_once results were not all used')
            self.assertEqual(transcript[-1][0], 'done', 'done must be the last event')
            replaced = 0
            for prefix in os_errors:
                for entry in transcript:
                    if entry[0] == 'log' and str(entry[1]).startswith(prefix) and len(entry[1]) > len(prefix):
                        entry[1] = prefix + '<OSERROR>'
                        replaced += 1
                    elif entry[0] == 'item_done' and entry[1].get('error') and prefix == os_errors[0]:
                        # The same operating-system text, as the item keeps it.
                        entry[1]['error'] = '<OSERROR>'
                        if entry[1].get('reason'):
                            entry[1]['reason'] = '<OSERROR>'
            if os_errors:
                self.assertGreaterEqual(replaced, 1, f'expected a log line starting with one of {os_errors!r}')
                for entry in (failed_file or {}).get('entries', []):
                    if entry.get('error'):
                        entry['error'] = '<OSERROR>'
            replacements = []
            for path, token in ((out, '<OUT>'), (state, '<STATE>'), (archive, '<ARCHIVE>'), (cookies, '<COOKIES>'),
                                (root, '<TMP>')):
                replacements += [(form, token) for form in _sandbox.path_forms(path)]
            return _sandbox.normalise({'events': transcript, 'failed_file': failed_file}, replacements)

    def legacy_scenarios(self):
        """The scenarios of events-1.5.1.json: urls, steps, options, hooks and what is stored
        beforehand are exactly as WP0 recorded them (only the log prefix after which an
        operating-system text is expected was extended for output_folder_unusable)."""
        def cancel_on_first_item_event(runner, kind, value):
            if kind == 'item' and value['index'] == 1:
                runner.cancel()

        def cancel_after_first_item(runner, kind, value):
            if kind == 'item_done' and value['index'] == 1:
                runner.cancel()

        def cancel_after_second_item(runner, kind, value):
            if kind == 'item_done' and value['index'] == 2:
                runner.cancel()

        def raise_on_item_done(runner, kind, value):
            # What a failing library.download_event does to the emit closure of the 1.5.1 UI.
            if kind == 'item_done':
                raise RuntimeError('library is locked')

        def cancel_in_wait(runner):
            runner.cancel()
            return False

        scenarios = {
            # --- the ten scenarios of WP0.md, in its order
            'two_success': dict(urls=[A, B], steps=[ok('a.wav'), ok('b.wav')]),
            'fail_then_success': dict(urls=[A, B], steps=[fail, ok('b.wav')]),
            'rate_limit_then_success': dict(urls=[A, B], steps=[limited(), ok('a.wav'), ok('b.wav')]),
            'rate_limit_exhausted_then_next': dict(urls=[A, B], steps=[limited(), limited(), limited(), ok('b.wav')]),
            'retry_after': dict(urls=[A], steps=[limited(650), ok('a.wav')]),
            'cancel_during_first': dict(urls=[A, B, C], steps=[cancel_and_fail]),
            'bot_check_first_of_two': dict(urls=[A, B], steps=[bot_check, ok('b.wav')],
                                           options=lambda p: engine.Options(p['out'], batch_safe=False)),
            'exception_on_second_of_three': dict(urls=[A, B, C], steps=[ok('a.wav'), boom]),
            'previous_job_same_options': dict(
                urls=[A], steps=[ok('a.wav')],
                before=lambda p, options: engine.store_failed_job([A, B], options)),
            'previous_job_other_format': dict(
                urls=[A], steps=[ok('a.mp3')],
                options=lambda p: engine.Options(p['out'], engine.MODES[3]),
                before=lambda p, options: engine.store_failed_job([A, B], engine.Options(p['out'], engine.MODES[4]))),
            # --- further branches of download() and of its finally block
            'previous_job_cleared_by_success': dict(
                urls=[A], steps=[ok('a.wav')],
                before=lambda p, options: engine.store_failed_job([A], options)),
            'previous_job_other_format_failure': dict(
                urls=[C], steps=[fail],
                options=lambda p: engine.Options(p['out'], engine.MODES[3]),
                before=lambda p, options: engine.store_failed_job([A, B], engine.Options(p['out'], engine.MODES[4]))),
            # The comparison with the previous job ignores proxy and cookies (both are session values).
            'previous_job_same_options_despite_proxy_and_cookies': dict(
                urls=[A], steps=[ok('a.wav')],
                options=lambda p: engine.Options(p['out'], proxy='http://127.0.0.1:7890', cookies=str(p['cookies'])),
                before=lambda p, options: engine.store_failed_job([A, B], engine.Options(p['out']))),
            'cancel_between_items': dict(urls=[A, B, C], steps=[ok('a.wav')], hook=cancel_after_first_item),
            'cancel_during_cooldown': dict(urls=[A, B], steps=[limited()], wait_result=cancel_in_wait),
            'rate_limit_exhausted_on_last': dict(urls=[A], steps=[limited(), limited(), limited()]),
            'bot_check_with_cookies': dict(
                urls=[A], steps=[bot_check],
                options=lambda p: engine.Options(p['out'], batch_safe=False, cookies=str(p['cookies']))),
            # The bot-check flag of one link must not leak into the next one.
            'bot_check_then_plain_failure': dict(urls=[A, B], steps=[bot_check, fail],
                                                 options=lambda p: engine.Options(p['out'], batch_safe=False)),
            'archive_two_items': dict(
                urls=[A, B], steps=[ok('a.mkv'), ok('b.mkv')], library=True,
                options=lambda p: engine.Options(
                    p['out'], engine.MODES[4], batch_safe=False, archive_root=str(p['archive']),
                    archive_contexts={A: {'title': '标题甲', 'source': 'https://example.invalid/page',
                                          'job_id': 'missing-job', 'page_url': 'https://example.invalid/page'}})),
            # --- fix round 1: conditions of the failed-list merge (the except and the finally block)
            # A stop right after the last link finished: nothing is left to retry.
            'cancel_after_last_item': dict(urls=[A, B], steps=[ok('a.wav'), ok('b.wav')],
                                           hook=cancel_after_second_item),
            # The queue aborts before the first link (index 0): every link is kept for a retry and
            # failed_urls stays empty (urls[index - 1] would be the last link).
            # (1.6.0 fails each link on its own, with the line 此链接无法开始.)
            'output_folder_unusable': dict(urls=[A, B, C], steps=[], blocked=('out',),
                                           os_errors=('此链接无法开始：', '错误：')),
            # emit raises after the link's result is known: a finished link is not stored again, and a
            # failed one is listed once although both the loop and the except block add it.
            'emit_raises_after_success': dict(urls=[A, B], steps=[ok('a.wav')], hook=raise_on_item_done),
            'emit_raises_after_failure': dict(urls=[A, B], steps=[fail], hook=raise_on_item_done),
            # The list cannot be written: one log line, failed_path stays None, done still arrives.
            'failed_list_cannot_be_saved': dict(urls=[A], steps=[fail], blocked=('state',),
                                                os_errors=('失败清单未能保存：',)),
            # --- fix round 1: details of the 429 loop and of the item_done payload
            # A Retry-After shorter than the computed cooldown does not shorten the wait.
            'retry_after_shorter_than_cooldown': dict(urls=[A], steps=[limited(7), ok('a.wav')]),
            # The attempt counter starts again for every link: two waits of 300 s, both "(1/2)".
            'rate_limit_on_two_links': dict(urls=[A, B], steps=[limited(), ok('a.wav'), limited(), ok('b.wav')]),
            # A bot-check line in the output of a run that succeeded is not reported as a bot check.
            'bot_check_line_but_success': dict(urls=[A, B], steps=[bot_line_but_ok('a.wav'), ok('b.wav')]),
            # A stop during the second link: its item_done counts only its own files (none).
            'cancel_during_second': dict(urls=[A, B], steps=[ok('a.wav'), cancel_and_fail]),
            # --- fix round 2: the merge itself, an older list with the same options plus links of this run
            # A new failure goes in front of the older links; neither the new link nor the older ones are lost.
            'previous_job_same_options_new_failure': dict(
                urls=[C], steps=[fail],
                before=lambda p, options: engine.store_failed_job([A, B], options)),
            # The older link is retried together with a new one and both finish: the list is emptied,
            # although one link of this run was never on it.
            'previous_job_retried_with_new_link': dict(
                urls=[A, C], steps=[ok('a.wav'), ok('c.wav')],
                before=lambda p, options: engine.store_failed_job([A], options)),
            # A stop with an older list: the unfinished links of this run first, then the older ones, each once.
            'previous_job_same_options_cancelled': dict(
                urls=[C, B], steps=[cancel_and_fail],
                before=lambda p, options: engine.store_failed_job([A, B], options)),
            # --- fix round 2: corner cases of the item loop
            # Exit code 0 does not end the 429 loop: the link is retried after the cooldown, and when the
            # retries are used up it counts as failed (exit code reported as 1) although a file was written.
            'limited_with_exit_code_zero_exhausted': dict(
                urls=[A], steps=[limited_but_exit_zero('a.wav')] * 3),
            'limited_with_exit_code_zero_then_success': dict(
                urls=[A], steps=[limited_but_exit_zero('a.wav'), ok('a.wav')]),
            # A stop while a rate-limited run ends: no cooldown is started any more.
            'cancel_while_limited': dict(urls=[A, B], steps=[cancel_and_limited]),
            # A stop while a run ends with exit code 0: the link still counts as stopped, not as finished,
            # and stays in the list together with the links behind it; its file is counted.
            'cancel_with_exit_code_zero': dict(urls=[A, B], steps=[cancel_but_ok('a.wav')]),
            # A stop between the item event and the first run: no process is started, and the item
            # still gets its item_done.
            'cancel_right_after_item_event': dict(urls=[A, B], steps=[], hook=cancel_on_first_item_event),
            # The exit code in the log line is the one the process returned, also when the retries
            # after HTTP 429 are used up.
            'failure_with_exit_code_2': dict(urls=[A], steps=[fail_with(2)]),
            'rate_limit_exhausted_keeps_exit_code': dict(urls=[A], steps=[limited(code=3)] * 3),
            # The output folder is created together with its missing parent folders.
            'output_folder_nested': dict(
                urls=[A], steps=[ok('a.wav')],
                options=lambda p: engine.Options(p['out'] / '新建文件夹' / '音乐')),
            # Archive errors collected while a link ran are handed on in the done event; the link
            # itself still counts as finished.
            'archive_error_reported': dict(
                urls=[A], steps=[archive_error_but_ok('a.mkv')], library=True,
                options=lambda p: engine.Options(p['out'], engine.MODES[4], batch_safe=False,
                                                 archive_root=str(p['archive']))),
            # --- fix round 2: what "the same job" means for the merge
            # Every stored option survives the round trip through failed-downloads.json: an older list
            # written with a value other than the default in each option is recognised as the same job.
            'previous_job_same_options_all_set': dict(
                urls=[C], steps=[fail], library=True,
                options=lambda p: engine.Options(
                    p['out'], engine.MODES[4], engine.RESOLUTIONS[3], playlist=True, batch_safe=False,
                    referers={C: 'https://example.org/page'}, archive_root=str(p['archive']),
                    archive_contexts={C: {'title': '标题丙', 'source': 'https://example.invalid/page'}}),
                before=lambda p, options: engine.store_failed_job([A, B], options)),
        }
        # An older list that differs in one single option other than proxy and cookies is another job:
        # it is not merged, and the new failure replaces it (as in previous_job_other_format_failure).
        other_values = {
            'output': lambda p: p['out'] / 'elsewhere',
            'resolution': lambda p: engine.RESOLUTIONS[3],
            'playlist': lambda p: True,
            'batch_safe': lambda p: False,
            'referers': lambda p: {A: 'https://example.org/page'},
            'archive_root': lambda p: str(p['archive']),
            'archive_contexts': lambda p: {A: {'title': '标题甲'}},
        }
        for key, other in other_values.items():
            scenarios[f'previous_job_other_{key}_failure'] = dict(
                urls=[C], steps=[fail],
                before=lambda p, options, key=key, other=other: engine.store_failed_job(
                    [A, B], replace(options, **{key: other(p)})))
        return scenarios

    def new_scenarios(self):
        """Scenarios for what 1.6.0 adds to the queue (the cases themselves are tested in test_queue.py)."""
        Y1, Y2 = 'https://www.youtube.com/watch?v=AAA', 'https://www.youtube.com/watch?v=BBB'
        search = 'https://www.youtube.com/results?search_query=AAA'
        clock = [1000.0]

        def takes(seconds, name):
            """A successful run during which the clock moves."""
            def step(runner, command, index, total, files):
                clock[0] += seconds
                return ok(name)(runner, command, index, total, files)
            return step

        def four_seconds_before_each_link(runner, kind, value):
            if kind == 'item':
                clock[0] += 4

        def enqueue_and_ok(name, links, mode=None):
            def step(runner, command, index, total, files):
                options = replace(runner.base_options, mode=mode) if mode else None
                runner.enqueue(links, options)
                return ok(name)(runner, command, index, total, files)
            return step

        def enqueue_and_cancel(links):
            def step(runner, command, index, total, files):
                runner.enqueue(links)
                runner.cancel()
                return 1, False, None
            return step

        def engine_refuses_an_option(runner, command, index, total, files):
            runner._note_line('yt-dlp.exe: error: no such option: --embed-everything')
            return 2, False, None

        def subtitles_refused(runner, command, index, total, files):
            runner._note_line("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
            return 1, False, None

        def unavailable(runner, command, index, total, files):
            runner._note_line('ERROR: [youtube] AAA: Video unavailable')
            return 1, False, None

        def reports_media(name, media_id):
            """A run whose engine output names the media it saved (as the real loop hands it on)."""
            def step(runner, command, index, total, files):
                path = str(Path(command[command.index('-P') + 1]) / name)
                Path(path).write_bytes(b'media')      # a real file: the Runner renames it
                for kind, value in (('title', '白羊'),
                                    ('media', {'id': media_id, 'extractor_key': 'Youtube', 'title': '白羊', 'uploader': '某人',
                                               'duration': 215, 'webpage_url': 'https://www.youtube.com/watch?v=' + media_id,
                                               'filepath': path}),
                                    ('file', path)):
                    kind, value = runner._intercept(kind, value)
                    if kind == 'file' and value not in files:
                        files.append(value)
                    runner.emit(kind, value)
                return 0, False, None
            return step

        def downloaded_before(p, options):
            song = p['out'] / '已下载的歌.opus'
            song.parent.mkdir(parents=True, exist_ok=True)
            song.write_bytes(b'media')
            p['library'].record_download([urls.link_key(A)], 'audio_best', A, '已下载的歌', str(song), 'earlier-job')
            p['library'].record_download([urls.link_key(B)], 'audio_best', B, '文件已删除的歌', str(p['out'] / '已删除.opus'),
                                         'earlier-job')

        return {
            # Spacing: no wait before the first link; afterwards the rest of the 10 s since the last
            # download ended (4 s have passed when the next link starts).
            'pacing_three_links': dict(urls=[A, B, C], steps=[takes(3, 'a.wav'), takes(3, 'b.wav'), takes(3, 'c.wav')],
                                       hook=four_seconds_before_each_link, clock=clock),
            # R11: the first bot check on YouTube keeps the other YouTube links of the run back.
            'youtube_bot_check_blocks_the_rest': dict(urls=[Y1, Y2, A], steps=[bot_check, ok('a.wav')]),
            # R10: a link that cannot start (a broken referer) fails alone.
            'link_that_cannot_start': dict(urls=[A, B], steps=[ok('b.wav')],
                                           options=lambda p: engine.Options(p['out'], referers={A: 'not a link'})),
            'enqueue_during_first_link': dict(urls=[A], steps=[enqueue_and_ok('a.wav', [B, C]), ok('b.wav'), ok('c.wav')]),
            'enqueue_another_format': dict(urls=[A], steps=[enqueue_and_ok('a.wav', [A, B], engine.MODES[3]), ok('a.mp3'), fail]),
            'enqueue_then_stop': dict(urls=[A, B], steps=[enqueue_and_cancel([C])]),
            'engine_refuses_an_option': dict(urls=[A, B], steps=[engine_refuses_an_option]),
            'subtitle_failure_reruns_without_subtitles': dict(
                urls=[A], steps=[subtitles_refused, ok('a.mkv')],
                options=lambda p: engine.Options(p['out'], engine.MODES[4], subtitles=True)),
            'duplicate_links_run_once': dict(urls=[Y1, 'https://youtu.be/AAA?si=x', B], steps=[ok('a.wav'), ok('b.wav')]),
            'overrides_two_formats_one_fails': dict(
                urls=[A, B], steps=[ok('a.wav'), unavailable],
                overrides=lambda p: {B: engine.Options(p['out'] / '视频', engine.MODES[6], '720p', batch_safe=False)}),
            'skip_what_is_already_downloaded': dict(
                urls=[A, B], steps=[ok('b.wav')], library=True, before=downloaded_before,
                options=lambda p: engine.Options(p['out'], skip_downloaded=True)),
            # The owner's real case: the song is on the list under its watch link and is then
            # downloaded through a search link.  The media id clears the entry.
            'media_id_clears_the_entry_of_another_link': dict(
                urls=[search], steps=[reports_media('白羊 [AAA].opus', 'AAA')],
                before=lambda p, options: engine.store_failed_job([Y1, B], options)),
            'failed_link_queued_again_succeeds': dict(urls=[A, B], steps=[fail, enqueue_and_ok('b.wav', [A]), ok('a.wav')]),
        }

    def test_download_transcripts_match_golden(self):
        scenarios = self.legacy_scenarios()
        added = self.new_scenarios()
        self.assertEqual(set(scenarios) & set(added), set())
        scenarios.update(added)
        result = {name: self.scenario(**arguments) for name, arguments in scenarios.items()}
        _sandbox.check_golden(self, GOLDEN_NAME, result)

    # --- the 1.5.1 scenarios on the current engine, compared with the 1.5.1 golden

    @staticmethod
    def project(record, legacy):
        """A transcript reduced to what 1.5.1 knew.  ``legacy`` says which side is being prepared:
        the wording of the two bot-check hints is replaced by a token on both."""
        hints = ({OLD_BOT_CHECK_HINT: '<BOT_CHECK_HINT>', OLD_BOT_CHECK_STALE_HINT: '<BOT_CHECK_STALE_HINT>'} if legacy
                 else {engine.BOT_CHECK_HINT: '<BOT_CHECK_HINT>', engine.BOT_CHECK_STALE_HINT: '<BOT_CHECK_STALE_HINT>'})
        events = []
        for kind, value in copy.deepcopy(record['events']):
            if kind in NEW_KINDS:
                continue
            if isinstance(value, dict):
                for key in NEW_KEYS.get(kind, ()):
                    value.pop(key, None)
            if kind == 'log':
                value = hints.get(value, value)
            events.append([kind, value])
        return events

    def compare_with_legacy(self, name, expected, actual):
        """Raise AssertionError unless ``actual`` (this engine) is ``expected`` (events-1.5.1.json)
        apart from PERMITTED_DIFFERENCES."""
        stored_path = '<STATE>' + chr(92) + 'failed-downloads.json'
        old_events, new_events = self.project(expected, True), self.project(actual, False)
        old_done, new_done = old_events[-1][1], new_events[-1][1]
        self.assertEqual((old_events[-1][0], new_events[-1][0]), ('done', 'done'), name)
        old_file, new_file = expected['failed_file'], actual['failed_file']
        # 1. failed_path names the file exactly when a list is stored.
        self.assertEqual(new_done.pop('failed_path'), stored_path if new_file else None, name)
        old_path = old_done.pop('failed_path')
        if new_file is None:
            self.assertFalse((old_file or {}).get('urls'), f'{name}: 1.5.1 stored links, 1.6.0 stored nothing')
        else:
            self.assertEqual(old_path, stored_path, name)
        # 2. pending_urls: this run only.
        this_run = {**PENDING_THIS_RUN, **PENDING_THIS_RUN_MORE}
        if name in this_run:
            self.assertEqual(new_done.pop('pending_urls'), this_run[name], name)
            old_done.pop('pending_urls')
        # 3. failed_file: the view 1.5.1 reads holds the links 1.5.1 stored, with the options it stored.
        old_links = (old_file or {}).get('urls', [])
        self.assertEqual((new_file or {}).get('urls', []), old_links, f'{name}: legacy urls')
        if new_file is not None:
            self.assertEqual(new_file['version'], 1, name)
            self.assertIsInstance(new_file['entries'], list, name)
            self.assertTrue(new_file['entries'], name)
        if old_links:
            self.assertEqual({key: new_file['options'].get(key) for key in old_file['options']}, old_file['options'],
                             f'{name}: legacy options')
        self.assertEqual(new_events, old_events, f'{name}: the transcript differs from events-1.5.1.json')

    def test_projection_onto_the_1_5_1_golden(self):
        legacy = json.loads((_sandbox.GOLDEN / LEGACY_GOLDEN).read_text(encoding='utf-8'))
        scenarios = self.legacy_scenarios()
        self.assertEqual(set(scenarios), set(legacy), 'the scenario table and events-1.5.1.json name the same scenarios')
        self.assertEqual(len(PERMITTED_DIFFERENCES), 4)
        self.assertEqual(set(NORMATIVE) & set(NOT_PROJECTED), set(), 'the ten scenarios of WP0.md are always projected')
        self.assertLessEqual(set(NORMATIVE) | set(NOT_PROJECTED) | set(PENDING_THIS_RUN) | set(PENDING_THIS_RUN_MORE),
                             set(legacy))
        self.assertEqual(len(NORMATIVE), 10)
        self.assertLessEqual(set(PENDING_THIS_RUN), set(NORMATIVE))
        self.assertEqual(set(PENDING_THIS_RUN_MORE) & (set(NORMATIVE) | set(NOT_PROJECTED)), set())
        self.assertEqual(len(legacy) - len(NOT_PROJECTED), 40, 'scenarios of 1.5.1 that the current engine still reproduces')
        problems = []
        for name, arguments in scenarios.items():
            # WP2.md: Runner.reset_pacing() (done by scenario()) and PACE_SECONDS = (0, 0).
            actual = self.scenario(**arguments, pace=(0, 0))
            try:
                self.compare_with_legacy(name, legacy[name], actual)
            except AssertionError as error:
                if name not in NOT_PROJECTED:
                    problems.append(f'{name}: {str(error)[:1500]}')
            else:
                if name in NOT_PROJECTED:
                    problems.append(f'{name}: listed in NOT_PROJECTED but equals the 1.5.1 transcript; remove it from the list')
        self.assertEqual(problems, [], chr(10) * 2 + (chr(10) * 2).join(problems))


if __name__ == '__main__':
    unittest.main()
