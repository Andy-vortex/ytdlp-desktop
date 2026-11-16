"""Golden transcripts of the real engine.Runner._run_once (1.6.0 WP0).

The protected loop (sleep / rate-limit state machine) is NOT patched here: it reads
real child processes ``python -c <script>`` that print scripted yt-dlp lines.  This
golden must stay identical through the whole upgrade (after WP1 §E and WP2 §F).
To record on purpose, run this file directly (not through run_tests.py) with
GOLDEN_UPDATE=1.

Every scenario calls _run_once(command, 1, 2, files) on a fresh Runner, without
download(): whatever the loop reads from ``self`` must exist after Runner.__init__
(or be read with getattr, as 1.5.1 does for ``archive``).

The children write the way yt-dlp does: its ERROR and WARNING lines go to stderr
(say_err), everything else to stdout (say).  The loop only sees the stderr lines
because _run_once merges the two streams.

Each record also holds ``popen``: how the loop started the child (None when it did
not start one).  It is captured by wrapping subprocess.Popen for the duration of the
call, so the engine has to reach Popen through the subprocess module at call time,
as 1.5.1 does.  Recorded: the number of positional arguments, the stream arguments
by name, encoding, errors, creationflags, cwd, the names of any further keyword
arguments, and from ``env`` only the three variables and the first PATH entry that
process_environment() sets.  The rest of ``env`` is not recorded (WP1 adds a per-run
TEMP there).

Normalisation: ``seconds`` of a waiting payload -> "<S>", the exit code of a child the
loop had to kill -> "<KILLED>", the scenario's temp folder -> <TMP>, the vendor bin
folder -> <BIN>.
No scenario depends on timing: a child that must wait for the loop waits for a file
that the test creates when the expected event arrives.
"""
import _sandbox
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import engine

GOLDEN_NAME = 'runonce-1.5.1.json'
WATCHDOG_SECONDS = 60
PRELUDE = ('import json, os, sys, time\n'
           'sys.stdout.reconfigure(encoding="utf-8", newline="\\n")\n'
           'sys.stderr.reconfigure(encoding="utf-8", newline="\\n")\n'
           'def say(*lines):\n'
           '    sys.stdout.write("".join(line + "\\n" for line in lines))\n'
           '    sys.stdout.flush()\n'
           'def say_err(*lines):\n'
           '    sys.stderr.write("".join(line + "\\n" for line in lines))\n'
           '    sys.stderr.flush()\n'
           'def wait_for(path):\n'
           '    deadline = time.monotonic() + 30\n'
           '    while not os.path.exists(path) and time.monotonic() < deadline:\n'
           '        time.sleep(0.01)\n')
PROGRESS = '__GUI_PROGRESS__50|100|NA|10|5|downloading'
BOT_LINE = ('ERROR: [youtube] abc: Sign in to confirm you\\u2019re not a bot. '
            'Use --cookies-from-browser or --cookies for the authentication.')
STREAMS = {subprocess.PIPE: 'PIPE', subprocess.STDOUT: 'STDOUT', subprocess.DEVNULL: 'DEVNULL'}
RECORDED_KEYWORDS = ('stdin', 'stdout', 'stderr', 'encoding', 'errors', 'creationflags', 'cwd', 'env')
FIXED_ENVIRONMENT = ('PYTHONUTF8', 'PYTHONIOENCODING', 'PYINSTALLER_RESET_ENVIRONMENT')


def describe_start(arguments, keywords):
    """The part of one Popen call that the loop depends on, in a machine-independent form."""
    def stream(name):
        value = keywords.get(name)
        return STREAMS.get(value, value if value is None else repr(value))

    flags = keywords.get('creationflags')
    if flags is not None and flags == getattr(subprocess, 'CREATE_NO_WINDOW', object()):
        flags = 'CREATE_NO_WINDOW'
    environment = keywords.get('env')
    described = {'positional_arguments': len(arguments),
                 'stdin': stream('stdin'), 'stdout': stream('stdout'), 'stderr': stream('stderr'),
                 'encoding': keywords.get('encoding'), 'errors': keywords.get('errors'),
                 'creationflags': flags,
                 'cwd': None if keywords.get('cwd') is None else str(keywords['cwd']),
                 'other_keywords': sorted(set(keywords) - set(RECORDED_KEYWORDS)),
                 'env': None}
    if environment is not None:
        described['env'] = {name: environment.get(name) for name in FIXED_ENVIRONMENT}
        described['env']['PATH[0]'] = environment.get('PATH', '').split(os.pathsep)[0]
    return described


class FakeArchive:
    """Stands in for library.Archive: records media() calls and rejects metadata without a file."""

    def __init__(self):
        self.calls = []

    def media(self, folder, metadata):
        self.calls.append([str(folder), copy.deepcopy(metadata)])
        if 'filepath' not in metadata:
            raise ValueError('归档文件与本条资料目录不匹配。')


class GoldenRunOnceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()

    def scenario(self, body, killed=False, hook=None, prepare=None):
        """Run one scripted child through the real loop; returns the normalised record."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media, signal = root / '标题 [abc].wav', root / 'continue.flag'
            events, files = [], []

            def emit(kind, value):
                events.append([kind, copy.deepcopy(value)])
                if hook:
                    hook(runner, kind, value, signal)

            runner = engine.Runner(emit)
            archive = prepare(runner, root) if prepare else None
            command = [sys.executable, '-c', PRELUDE + body, str(media), str(signal)]
            real_popen, starts = subprocess.Popen, []

            def popen(*arguments, **keywords):
                # Only the engine's own start is recorded; taskkill (subprocess.run) passes through.
                target = arguments[0] if arguments else keywords.get('args')
                if target is command or target == command:
                    starts.append(describe_start(arguments, keywords))
                return real_popen(*arguments, **keywords)

            fired = []
            watchdog = threading.Timer(WATCHDOG_SECONDS, lambda: (fired.append(True), runner.cancel()))
            watchdog.daemon = True
            watchdog.start()
            try:
                with patch.object(subprocess, 'Popen', popen):
                    code, was_limited, retry_after = runner._run_once(command, 1, 2, files)
            finally:
                watchdog.cancel()
            self.assertIs(subprocess.Popen, real_popen)
            self.assertEqual(fired, [], f'the loop did not finish within {WATCHDOG_SECONDS} s')
            self.assertLessEqual(len(starts), 1, 'the loop started the child more than once')
            self.assertIsNone(runner.process, 'the finished process is still registered on the runner')
            for kind, value in events:
                if kind == 'waiting':
                    self.assertIsInstance(value['seconds'], int)
                    value['seconds'] = '<S>'
            if killed:
                self.assertNotEqual(code, 0, 'the child was expected to be killed by the loop')
                code = '<KILLED>'
            record = {'events': events, 'result': [code, was_limited, retry_after],
                      'bot_check': runner.bot_check, 'files': files, 'popen': starts[0] if starts else None}
            if archive:
                record['archive_calls'] = archive.calls
                record['archive_errors'] = list(runner.archive_errors)
            replacements = [(form, '<TMP>') for form in _sandbox.path_forms(root)]
            replacements += [(form, '<BIN>') for form in _sandbox.path_forms(engine.BIN)]
            return _sandbox.normalise(record, replacements)

    def test_run_once_transcripts_match_golden(self):
        def cancel_on_waiting(runner, kind, value, signal):
            if kind == 'waiting':
                runner.cancel()

        def release_on_resuming(runner, kind, value, signal):
            if kind == 'resuming':
                signal.write_text('go', encoding='utf-8')

        def already_cancelled(runner, root):
            runner.cancelled.set()

        def with_archive(runner, root):
            runner.archive = FakeArchive()
            runner.archive_folder = root / 'archive' / 'item'
            runner.archive_errors = []
            return runner.archive

        result = {
            # --- the five scenarios of WP0.md
            # The sleep line and the progress line arrive in one write, so the countdown is still
            # running when the progress line ends it.
            'normal': self.scenario(
                'say(\'__GUI_TITLE__"T"\')\n'
                f'say("[download] Sleeping 0.15 seconds ...", "{PROGRESS}")\n'
                'say("__GUI_FILE__" + json.dumps(sys.argv[1]))\n'),
            'rate_limit': self.scenario(
                'say_err("ERROR: HTTP Error 429: Too Many Requests")\n'
                'time.sleep(30)\n', killed=True),
            'retry_after': self.scenario(
                'say("[debug] response header Retry-After: 7")\n'
                'say("[debug] response header Retry-After: 3")\n'
                'say_err("ERROR: HTTP Error 429: Too Many Requests")\n'
                'time.sleep(30)\n', killed=True),
            'bot_check': self.scenario(f'say_err("{BOT_LINE}")\nsys.exit(1)\n'),
            'plain_failure': self.scenario('say_err("ERROR: boom")\nsys.exit(1)\n'),
            # --- further branches of the loop
            'cancelled_before_start': self.scenario('say("never printed")\n', prepare=already_cancelled),
            'odd_lines': self.scenario(
                'say("", "Some title includes number 429", "ERROR: HTTP Error 403: Forbidden")\n'
                'say("__GUI_TITLE__not-json", "__GUI_PROGRESS__1|2|3")\n'
                'say("__GUI_FILE__" + json.dumps(sys.argv[1]), "__GUI_FILE__" + json.dumps(sys.argv[1]))\n'
                'sys.exit(3)\n'),
            'archive_lines': self.scenario(
                'say("__GUI_ARCHIVE__" + json.dumps({"id": "x1", "title": "T", "filepath": sys.argv[1]}))\n'
                'say("__GUI_ARCHIVE__" + json.dumps({"id": "x2", "title": "no file"}))\n',
                prepare=with_archive),
            'sleep_expires': self.scenario(
                'say("[download] Sleeping 1 seconds ...")\n'
                'wait_for(sys.argv[2])\n'
                f'say("{PROGRESS}")\n', hook=release_on_resuming),
            'cancel_during_sleep': self.scenario(
                'say("[download] Sleeping 300 seconds ...")\n'
                'time.sleep(30)\n', killed=True, hook=cancel_on_waiting),
            # --- fix round 1
            # The second alternative of RATE_LIMIT_PATTERN: no status code, a WARNING on stderr.
            'too_many_requests_without_status': self.scenario(
                'say_err("WARNING: [youtube] abc: Too Many Requests")\n'
                'time.sleep(30)\n', killed=True),
            # The first alternative without the word "Error", and Retry-After written with "=".
            'http_status_429': self.scenario(
                'say("[debug] response header Retry-After=12")\n'
                'say_err("WARNING: [generic] Got HTTP status 429, giving up")\n'
                'time.sleep(30)\n', killed=True),
            # Lines that look similar and must not start a cooldown, a countdown or a Retry-After.
            'near_misses': self.scenario(
                'say("[download] Destination: Too Many Requests [abc].m4a")\n'
                'say_err("ERROR: HTTP Error 4290: not a rate limit")\n'
                'say("[debug] Retry-After header is missing")\n'
                'say("[download] Sleeping 0 seconds ...", "[download] Sleeping a while ...")\n'
                'sys.exit(1)\n'),
            # Every shape of a progress line: total known; only the estimate known; nothing known;
            # more downloaded than the size; a size of zero; negative, infinite and non-numeric fields.
            'progress_shapes': self.scenario(
                'say("__GUI_PROGRESS__50|100|NA|10|5|downloading")\n'
                'say("__GUI_PROGRESS__50|NA|200|10|5|downloading")\n'
                'say("__GUI_PROGRESS__NA|NA|NA|NA|NA|downloading")\n'
                'say("__GUI_PROGRESS__300|200|NA|NA|NA|downloading")\n'
                'say("__GUI_PROGRESS__10|0|0|1|1|finished")\n'
                'say("__GUI_PROGRESS__-5|inf|100|nan|1e3|downloading")\n'),
            # Bytes that are not UTF-8 are replaced; the reader survives and the next line arrives.
            'invalid_utf8': self.scenario(
                'sys.stdout.buffer.write(b"\\xff\\xfe broken bytes\\n")\n'
                'sys.stdout.buffer.flush()\n'
                'say(\'__GUI_TITLE__"T"\')\n'),
            # --- fix round 2: details of line parsing
            # A progress line that carries both sizes uses the total, not the estimate.  A __GUI_FILE__
            # value that is not a string is passed on as an event but never added to the file list.
            # The bot-check line is recognised with no character at all between "you" and "re".
            'parsing_details': self.scenario(
                'say("__GUI_PROGRESS__50|100|200|10|5|downloading")\n'
                'say("__GUI_FILE__null", "__GUI_FILE__17")\n'
                'say_err("ERROR: [youtube] abc: Sign in to confirm youre not a bot.")\n'
                'sys.exit(1)\n'),
            # Up to three characters may stand between "you" and "re" (the typographic apostrophe
            # read in the wrong encoding), in any letter case ...
            'bot_check_three_characters_any_case': self.scenario(
                'say_err("ERROR: [youtube] abc: SIGN IN TO CONFIRM YOU\\u00e2\\u20ac\\u2122RE NOT A BOT.")\n'
                'sys.exit(1)\n'),
            # ... but not four.
            'bot_check_four_characters_apart': self.scenario(
                'say_err("ERROR: [youtube] abc: Sign in to confirm you____re not a bot.")\n'
                'sys.exit(1)\n'),
            # Only the line end is removed: spaces around a line stay and a line of spaces is a log
            # line.  A progress line with seven fields is a log line as well.  The total also wins
            # when it is the larger of the two sizes.  An archive line on a runner that has no
            # archive is passed on and nothing else happens.
            'line_shapes': self.scenario(
                'say("   ", "  [download] indented  ")\n'
                'say("__GUI_PROGRESS__50|100|NA|10|5|downloading|extra")\n'
                'say("__GUI_PROGRESS__50|200|100|10|5|downloading")\n'
                'say("__GUI_ARCHIVE__" + json.dumps({"id": "x1", "title": "T"}))\n'),
        }
        _sandbox.check_golden(self, GOLDEN_NAME, result)


if __name__ == '__main__':
    unittest.main()
