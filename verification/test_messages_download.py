# quick
"""Download core messages (MESSAGES.md 3.2, 3.4, 3.5, 5.2, 7.5; WP-2).

    python -X utf8 -B test_messages_download.py

engine.py, unfinished.py, naming.py, urls.py and formats.py produce messages: every app text they
emit, raise or store is msg(key, ...) whose value is byte for byte the 1.7.0 Chinese text (the
1.7.0 values are frozen literals below since 2.0.0 S2 P1; they were read from the 1.7.0 release
sources).  (2.0.0 S3 W4) An expected app text is now its key's zh / en text (zh_t / en_t): the tests pin
the key and the params, the wording lives only in the catalog.  Checked here:

* the five modules are converted (WP-6 removed the staging flag MESSAGES_READY) and APP_VERSION is the
  current version; (2.0.1) their Chinese literals and their use of i18n are checked by test_source_rules
  (S1, S2), which replaced the G1 checks;
* the core H4 constants keep their 1.7.0 values (ParityTests extended, 8.1), with their keys;
* every producer of 3.2 gives its key, the 1.7.0 zh text and an English text in en;
* explain_error and _masked keep the 1.7.0 values and the value's type (D-H-2, P8);
* the regression cases D-H-1 (cookies echo), D-H-2 (cut reasons), D-H-3 (English-only texts) and
  D-H-4 (a raw engine line that quotes app text) through a scripted Runner: key, stored bytes, en
  text via show();
* the unfinished list (3.4, D-F23, D-F30): problems and notices are messages, download.log keeps
  the zh WARNING, entries are read through messages.from_json and written through to_json (2.0.0
  format 2), keys and params of a later version are kept byte for byte by unfinished.save.
  (2.0.0 S2 P3) The failed-list rollback of 5.2 (1.5.1 / 1.6.0 / 1.7.0 reading what this version
  writes) is gone with the version-1 file.

No window, no network; _run_once is scripted.
"""
import _sandbox
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

import engine
import formats
import i18n
import messages
import naming
import paths
import unfinished
import urls
from messages import Message, Raw, msg

CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
# (2.0.0 S3 W4) The expected app texts are read by key in a fixed language (S3-DESIGN.md 4.3): the test pins
# which key and which params; the wording lives only in the catalog.
from _sandbox import en_t, zh_t
A, B, C, D, E = ('https://example.invalid/' + name for name in 'abcde')
MODULES = ('engine', 'unfinished', 'naming', 'urls', 'formats')
COOKIE_SECRET = 'cookie-secret-value-1234'
ECHO = f"ERROR: invalid Netscape format cookies file 'cookies.txt': '.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}'"
RAW_LINE = 'ERROR: [generic] x: 链接不存在（404）；网站拒绝访问（403）'      # engine output: input data (R5)
# (2.0.0 S2 P1) The values the 1.7.0 release computed, frozen as literals (they were read from
# the frozen 1.7.0 sources, which S2 deleted; the assertions that use them are unchanged).
# (S2 Q1) The stored enum values are codes from 2.0.0 on: the first resolution is 'best' (1.7.0: 最佳)
# and a preset is stored by its id (1.7.0: its Chinese mode name).  (S4 A1) A preset has no label any more.
# (S3 W4, R4) A message constant's value is its key's zh text (the key is pinned in the test); the
# engine's own strings and the stored resolutions stay literals.
OLD_CONSTANTS = {'BOT_CHECK_HINT': zh_t('core.engine.bot_check_hint'),
 'BOT_CHECK_REASON': zh_t('core.reason.bot_check'),
 'BOT_CHECK_STALE_HINT': zh_t('core.engine.bot_check_stale'),
 'COOKIE_LINE_FAILED': zh_t('core.cookies.line_failed'),
 'COOKIE_LINE_SKIPPED': zh_t('core.cookies.line_skipped'),
 'ENGINE_ERROR_REASON': zh_t('core.reason.engine_error'),
 'ENGINE_OPTION_ERROR': 'no such option',
 'NETWORK_REASON': zh_t('core.reason.network'),
 'RESOLUTIONS': ('best', '2160p / 4K', '1440p', '1080p', '720p', '480p'),
 'STALE_REASON': zh_t('core.reason.stale'),
 'SUBTITLE_FAILURE': 'Unable to download video subtitles'}
OLD_APP_VERSION = '1.7.0'
# (S3 W4, R4) The 1.7.0 patterns, in order, with the key of each reason (core.reason.<name>).
OLD_ERROR_REASONS = [('not (?:made this video )?available in your country|geo.?restrict', 'region'),
 ('Video unavailable|Private video|has been removed', 'unavailable'),
 ('Unable to download (?!video data)[^:]*: HTTP Error 403\\b', 'page_403'),
 ('Unable to download (?!video data)[^:]*: HTTP Error 404\\b', 'page_404'),
 ('Unable to download (?!video data)[^:]*: HTTP Error 410\\b', 'page_410'),
 ('timed out|getaddrinfo failed|Unable to download webpage|Connection (reset|refused)|10060',
  'network'),
 ('HTTP Error 403', 'http_403'),
 ('HTTP Error 404', 'http_404'),
 ('HTTP Error 410', 'http_410'),
 ('Requested format is not available', 'format'),
 ('Unsupported URL', 'unsupported')]
OLD_TRANSPORT_FAILURE = 'Unable to connect to proxy|ProxyError|Tunnel connection failed|timed out|getaddrinfo failed|Unable to download webpage|Connection (reset|refused)|10060'
# (2.0.0 S4 A1) Only the ids: the second name of a format (Preset.label) is gone, its one name is format.<id>.
OLD_PRESETS = ['audio_best', 'audio_wav', 'audio_flac', 'audio_mp3', 'video_mkv', 'audio_m4a', 'video_mp4']
EXPLAIN_SAMPLES = ['ERROR: [youtube] AAA: Video unavailable', 'HTTP Error 404: Not Found', 'timed out',
                   'Unable to download webpage: HTTP Error 403: Forbidden', 'Requested format is not available',
                   '[youtube] AAA: Something odd happened ' + 'z' * 90, '[WinError 5] 拒绝访问。', '', None,
                   '  spaced  ']
EXPLAIN_FLAGS = ({}, {'bot_check': True}, {'stale': True}, {'code': 7})
# 1.7.0 explain_error(sample, **flags) for each sample (rows) and flag set (columns); (S3 W4, R4) a reason is
# its key's zh text, an engine text that is passed through stays a literal (input data, R5)
_BOT, _STALE, _GONE = zh_t('core.reason.bot_check'), zh_t('core.reason.stale'), zh_t('core.reason.unavailable')
_404, _NET = zh_t('core.reason.http_404'), zh_t('core.reason.network')
_PAGE_403, _FORMAT = zh_t('core.reason.page_403'), zh_t('core.reason.format')
_EXIT_1, _EXIT_7 = zh_t('core.reason.exit_code', code=1), zh_t('core.reason.exit_code', code=7)
OLD_EXPLAINED = [(_GONE, _BOT, _GONE, _GONE),
 (_404, _BOT, _404, _404),
 (_NET, _BOT, _NET, _NET),
 (_PAGE_403, _BOT, _PAGE_403, _PAGE_403),
 (_FORMAT, _BOT, _FORMAT, _FORMAT),
 ('Something odd happened zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz',
  _BOT,
  _STALE,
  'Something odd happened zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'),
 ('[WinError 5] 拒绝访问。', _BOT, _STALE, '[WinError 5] 拒绝访问。'),
 (_EXIT_1, _BOT, _STALE, _EXIT_7),
 (_EXIT_1, _BOT, _STALE, _EXIT_7),
 ('spaced', _BOT, _STALE, 'spaced')]
# 1.7.0 _masked of the proxy test text, and of the zh text of the record_unreadable message around it
OLD_MASKED_TEXT = 'Unable to connect to proxy [代理] ( again)'       # messages.PROXY_TOKEN: the stored mask (R5)
OLD_MASKED_MESSAGE = zh_t('core.engine.record_unreadable', error=OLD_MASKED_TEXT)


def en(value):
    with _sandbox.language('en'):
        return messages.show(value)


def zh(value):
    with _sandbox.language('zh'):
        return messages.show(value)


def raised(call, *arguments, **keywords):
    try:
        call(*arguments, **keywords)
    except Exception as error:      # noqa: BLE001 - the test inspects whatever was raised
        return error
    raise AssertionError(f'{call!r} raised nothing')


class Checks(unittest.TestCase):
    def assertMessage(self, value, key, text=None):
        self.assertIsInstance(value, Message, repr(value))
        self.assertEqual(value.key, key)
        if text is not None:
            self.assertEqual(messages.zh(value), text)

    def assertEnglish(self, value, *raw_params):
        """en shows the key's English text: no CJK left outside the given raw parameter texts."""
        shown = en(value)
        for part in raw_params:
            shown = shown.replace(part, '')
        self.assertIsNone(CJK.search(shown), shown)
        return en(value)


class Case(Checks):
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
        paths.failed_file().write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def run_job(self, links, results, options=None):
        """One download(); ``results`` are the scripted engine runs, in order: 0 / 1, or a function."""
        steps = list(results)

        def run_once(command, index, total, files):
            step = steps.pop(0)
            return step(runner, command, index, total, files) if callable(step) else (step, False, None)

        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', return_value=True):
            runner.download(links, options or self.options)
        self.assertEqual(steps, [])
        return self.events[-1][1]

    def item_done(self, url):
        return [value for kind, value in self.events if kind == 'item_done' and value['url'] == url][-1]

    def logs(self):
        return [value for kind, value in self.events if kind == 'log']


# ============================================================================ modules and constants

class ModuleTests(Checks):
    def test_the_five_modules_are_ready_and_the_version_is_1_8_0(self):
        for name in MODULES:
            module = sys.modules[name]
            self.assertFalse(hasattr(module, 'MESSAGES_READY'), f'{name}: WP-6 removed the staging flag (7.8)')
        self.assertEqual(engine.APP_VERSION, '2.0.2')     # (2.0.2) the version bump; the test name keeps 1_8_0 (-k)

    def test_the_core_constants_keep_their_1_7_0_values(self):
        """8.1 / P3: the H4 constants are messages with the 1.7.0 values."""
        keys = {'BOT_CHECK_HINT': 'core.engine.bot_check_hint', 'BOT_CHECK_STALE_HINT': 'core.engine.bot_check_stale',
                'NETWORK_REASON': 'core.reason.network', 'BOT_CHECK_REASON': 'core.reason.bot_check',
                'STALE_REASON': 'core.reason.stale', 'ENGINE_ERROR_REASON': 'core.reason.engine_error',
                'COOKIE_LINE_SKIPPED': 'core.cookies.line_skipped', 'COOKIE_LINE_FAILED': 'core.cookies.line_failed'}
        old = OLD_CONSTANTS
        self.assertEqual(set(old), set(keys) | {'RESOLUTIONS', 'SUBTITLE_FAILURE', 'ENGINE_OPTION_ERROR'})
        for name, key in keys.items():
            self.assertMessage(getattr(engine, name), key, old[name])
            self.assertEqual(getattr(engine, name), old[name], 'compares equal to the 1.7.0 text')
            self.assertEnglish(getattr(engine, name))
        self.assertEqual(engine.RESOLUTIONS, old['RESOLUTIONS'])
        self.assertNotIsInstance(engine.RESOLUTIONS[0], Message, 'a stored enum value stays a literal (P7)')
        # D-H-1: the item's error for a cookies echo is the text 1.7.0 stored, now with a key.
        self.assertMessage(engine.COOKIE_LINE_ITEM, 'core.cookies.line_failed_item',
                           zh_t('core.cookies.line_failed_item'))
        # S1: the waiting reason of _run_once.
        self.assertMessage(engine.WAIT_PACE_OR_RETRY, 'core.wait.pace_or_retry', zh_t('core.wait.pace_or_retry'))
        # ERROR_REASONS: the same patterns, in the same order, with the 1.7.0 reasons (their keys).
        pairs = OLD_ERROR_REASONS
        self.assertEqual(len(engine.ERROR_REASONS), len(pairs))
        for (pattern, reason), (old_pattern, key) in zip(engine.ERROR_REASONS, pairs, strict=True):
            self.assertEqual(pattern.pattern, old_pattern)
            self.assertMessage(reason, f'core.reason.{key}', zh_t(f'core.reason.{key}'))
        self.assertEqual(engine._TRANSPORT_FAILURE.pattern, OLD_TRANSPORT_FAILURE)

    def test_formats_and_naming_keep_their_enum_values(self):
        """P7: stored enum values stay literals (S3: the naming labels are the catalog's, group 'naming')."""
        presets = OLD_PRESETS
        self.assertEqual([preset.id for preset in formats.PRESETS], presets)
        for preset in formats.PRESETS:
            self.assertNotIsInstance(preset.id, Message)
            self.assertFalse(hasattr(preset, 'label'))
        self.assertEqual(formats.BEST, 'best')


# ============================================================================ producers (3.2)

class ProducerTests(Case):
    def check(self, error, key, text, *raw_params, kind=None):
        self.assertIsInstance(error, kind or Exception)
        message = messages.message_of(error)
        self.assertMessage(message, key, text)
        self.assertEqual(str(error), text, 'str(exception) is the 1.7.0 text')
        self.assertIs(str(error), message, 'and keeps the key')
        self.assertEnglish(message, *raw_params)
        return message

    def test_link_parsing_and_files(self):
        """D-F34, D-F37."""
        self.check(raised(engine.parse_urls, 'https://a.example/x\nnot a link'), 'core.links.bad_line',
                   zh_t('core.links.bad_line', line=2), kind=ValueError)
        self.check(raised(engine.parse_urls, '# only a comment'), 'core.links.none', zh_t('core.links.none'))
        self.check(raised(urls.parse_download_text, 'https://a.example/x\nwords only'), 'core.links.no_link_on_line',
                   zh_t('core.links.no_link_on_line', line=2))
        self.check(raised(urls.parse_download_text, '\n# x\n'), 'core.links.none', zh_t('core.links.none'))
        big = self.root / 'big.txt'
        big.write_bytes(b'#' * (5 * 1024 * 1024 + 1))
        self.check(raised(engine.read_url_file, big), 'core.links.file_too_large', zh_t('core.links.file_too_large'))
        broken = self.root / 'broken.txt'
        broken.write_bytes(b'\xff\xfe\x00\xd8')
        self.check(raised(engine.read_url_file, broken), 'core.links.encoding', zh_t('core.links.encoding'))

    def test_cookie_files(self):
        """D-F36 (no cookie content in any message: H6)."""
        missing = self.root / 'gone' / 'cookies.txt'
        error = self.check(raised(engine.check_cookie_file, missing), 'core.cookies.missing',
                           zh_t('core.cookies.missing', file=str(missing)), str(missing))
        self.assertEqual(dict(error.params), {'file': str(missing)})
        large = self.root / 'large.txt'
        large.write_bytes(b'# Netscape HTTP Cookie File\n' + b'#' * (10 * 1024 * 1024))
        self.check(raised(engine.check_cookie_file, large), 'core.cookies.too_large',
                   zh_t('core.cookies.too_large'))
        exported = self.root / 'cookies.json'
        exported.write_text('[{"name": "SID", "value": "%s"}]' % COOKIE_SECRET, encoding='utf-8')
        message = self.check(raised(engine.check_cookie_file, exported), 'core.cookies.json',
                             zh_t('core.cookies.json'))
        self.assertEqual(dict(message.params), {})
        plain = self.root / 'plain.txt'
        plain.write_text(f'.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}\n', encoding='utf-8')
        message = self.check(raised(engine.check_cookie_file, plain), 'core.cookies.not_netscape',
                             zh_t('core.cookies.not_netscape'))
        self.assertNotIn(COOKIE_SECRET, json.dumps(messages.wire(message), ensure_ascii=False))

    def test_options_and_formats_english_texts_are_keyed(self):
        """D-H-3 / D-F38: the English-only texts get keys whose zh text is today's English."""
        # (2.0.0 S3 W4, R4) each case: the raised error, its key and its params (the text is the key's)
        cases = [
            (raised(engine.Options.from_dict, []), 'core.options.not_object', {}),
            (raised(engine.Options.from_dict, {'output': ' '}), 'core.options.no_output', {}),
            (raised(engine.Options.from_dict, {'output': 'D:/x', 'mode': 'mp5'}), 'core.formats.bad_mode',
             {'mode': "'mp5'"}),
            (raised(engine.Options.from_dict, {'output': 'D:/x', 'resolution': 99}), 'core.options.bad_resolution',
             {'resolution': 99}),
            (raised(engine.build_command, A, engine.Options(self.out, mode='mp5')), 'core.options.unsupported', {}),
            (raised(formats.by_id, '音频'), 'core.formats.bad_id', {'id': "'音频'"}),
            (raised(formats.by_id, 'x'), 'core.formats.bad_id', {'id': "'x'"}),
            (raised(formats.by_key, 'video_x@1080p'), 'core.formats.bad_id', {'id': "'video_x'"}),
        ]
        for error, key, params in cases:
            text = zh_t(key, **params)
            message = self.check(error, key, text, "'音频'", kind=ValueError)
            self.assertEqual(zh(message), text)
            self.assertEqual(en(message), en_t(key, **params))
            self.assertEqual(zh(message), en(message), 'zh shows the 1.7.0 English text (D13)')

    def test_build_command_errors(self):
        """D-F16 nested producers."""
        self.check(raised(engine.build_command, A, engine.Options(self.out, proxy='ftp://proxy.example')),
                   'core.proxy.scheme', zh_t('core.proxy.scheme'))
        self.check(raised(engine.build_command, A, engine.Options(self.out, referers={A: 'not a link'})),
                   'core.collect.bad_source', zh_t('core.collect.bad_source'))
        long_url = 'https://example.invalid/' + 'x' * 33000
        error = self.check(raised(engine.build_command, long_url, self.options), 'core.links.too_long',
                           zh_t('core.links.too_long', length=len(long_url), url=urls.display(long_url)),
                           urls.display(long_url), kind=engine.LinkTooLong)
        self.assertEqual(dict(error.params), {'length': len(long_url), 'url': urls.display(long_url)})

    def test_naming_notices(self):
        """D-F06: the four rename notices."""
        source = self.root / 'Song [abc].mp3'
        source.write_bytes(b'x')
        cases = [
            (PermissionError(13, 'in use'), 'core.rename.locked', zh_t('core.rename.locked', name=source.name)),
            (OSError(5, 'Access is denied'), 'core.rename.failed_named',
             zh_t('core.rename.failed_named', reason='Access is denied', name=source.name)),
            (FileExistsError(17, 'exists'), 'core.rename.too_many', zh_t('core.rename.too_many', name=source.name)),
        ]
        for failure, key, text in cases:
            lines = []
            with mock.patch.object(naming.os, 'rename', side_effect=failure), mock.patch.object(naming, 'LOCK_PAUSE', 0):
                self.assertEqual(naming.finalize(source, 'abc', 'title', log=lines.append), str(source))
            self.assertEqual(len(lines), 1)
            self.assertMessage(lines[0], key, text)
            self.assertEnglish(lines[0], source.name, 'Access is denied')
        lines = []
        with mock.patch.object(naming, 'target_stem', side_effect=TypeError('broken')):
            naming.finalize(source, 'abc', 'title', log=lines.append)
        self.assertMessage(lines[0], 'core.rename.failed', zh_t('core.rename.failed', reason='TypeError'))
        self.assertEnglish(lines[0])

    def test_explain_error_keeps_1_7_0_values_and_types(self):
        """D-F28: the same values as 1.7.0's explain_error; a Message fallback keeps its key (D-H-2)."""
        self.assertEqual(len(OLD_EXPLAINED), len(EXPLAIN_SAMPLES))
        for text, row in zip(EXPLAIN_SAMPLES, OLD_EXPLAINED):
            for flags, old in zip(EXPLAIN_FLAGS, row, strict=True):
                new = engine.explain_error(text, **flags)
                self.assertEqual(messages.zh(new), old, (text, flags))
        self.assertMessage(engine.explain_error('', 7), 'core.reason.exit_code', zh_t('core.reason.exit_code', code=7))
        self.assertEqual(dict(engine.explain_error('', 7).params), {'code': 7})
        self.assertIs(engine.explain_error('x', bot_check=True), engine.BOT_CHECK_REASON)
        self.assertIs(engine.explain_error('HTTP Error 410: Gone'), engine.ERROR_REASONS[8][1])
        line = messages.raw('[youtube] AAA: Something odd happened ' + 'z' * 90)
        self.assertIsInstance(engine.explain_error(line), Raw)
        self.assertIs(type(engine.explain_error('[youtube] AAA: plain text')), str)
        missing = msg('core.cookies.missing', file='D:/' + 'p' * 120 + '/cookies.txt')
        reason = engine.explain_error(missing)
        self.assertMessage(reason, 'core.cookies.missing', messages.zh(missing)[:70])
        self.assertEqual(en(reason), en(missing), 'en shows the whole message (D12)')

    def test_masked_keeps_the_type_and_masks_raw_params(self):
        """P8: the 1.7.0 replacements, on the text and on the raw params of a message."""
        proxy = 'http://user:secret@127.0.0.1:9'
        options = engine.Options(self.out, proxy=proxy)
        text = f'Unable to connect to proxy {proxy} (user:secret@ again)'
        self.assertEqual(engine._masked(text, options), OLD_MASKED_TEXT)
        self.assertIs(type(engine._masked(text, options)), str)
        line = messages.raw(text)
        self.assertIsInstance(engine._masked(line, options), Raw)
        self.assertEqual(engine._masked(line, options), OLD_MASKED_TEXT)
        message = msg('core.engine.record_unreadable', error=text)
        masked = engine._masked(message, options)
        self.assertMessage(masked, 'core.engine.record_unreadable', OLD_MASKED_MESSAGE)
        self.assertNotIn('secret', json.dumps(messages.wire(masked), ensure_ascii=False))
        self.assertIn(messages.PROXY_TOKEN, masked.params['error'])
        self.assertNotIn('secret', en(masked))
        self.assertIn(en_t('core.proxy_mask'), en(masked))
        self.assertIs(engine._masked(message, engine.Options(self.out)), message, 'nothing to mask: the same object')

    def test_wait_and_unfinished_notices(self):
        """D-F01 / D-F25: the waiting line nests the caller's reason."""
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        runner.cancelled.set()
        runner._wait(2.5, 1, 1, msg('core.wait.rate_retry', attempt=1, limit=2))
        line = self.logs()[-1]
        self.assertMessage(line, 'core.engine.waiting', zh_t('core.engine.waiting', seconds=3, reason=zh_t(
            'core.wait.rate_retry', attempt=1, limit=2)))
        self.assertEqual(line.params['seconds'], 3)
        self.assertMessage(line.params['reason'], 'core.wait.rate_retry')
        self.assertEnglish(line)


# ============================================================================ regression cases (7.5)

class RegressionTests(Case):
    def test_d_h_1_the_cookies_echo_error_is_keyed(self):
        cookies = self.root / 'cookies.txt'
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        options = engine.Options(self.out, cookies=str(cookies))

        def echo(runner, command, index, total, files):
            kind, value = runner._intercept('log', ECHO)
            runner.emit(kind, value)
            runner._note_line(ECHO)
            return 1, False, None

        self.run_job([A], [echo], options)
        self.assertIn(engine.COOKIE_LINE_FAILED, self.logs())
        line = next(value for value in self.logs() if value == engine.COOKIE_LINE_FAILED)
        self.assertMessage(line, 'core.cookies.line_failed')
        done = self.item_done(A)
        fragment = zh_t('core.cookies.line_failed_item')      # what 1.7.0 stored (its key's zh text)
        self.assertMessage(done['error'], 'core.cookies.line_failed_item', fragment)
        self.assertMessage(done['reason'], 'core.cookies.line_failed_item', fragment)
        english = en_t('core.cookies.line_failed_item')
        self.assertEqual(en(done['error']), english)
        self.assertEqual(en(done['reason']), english)
        # (2.0.0) Stored: the key; read back: the message with the 1.7.0 zh text.
        stored = self.file()['entries'][0]
        self.assertEqual(stored['error'], {'k': 'core.cookies.line_failed_item'})
        loaded = unfinished.load()[0]['error']
        self.assertEqual(zh(loaded), fragment)
        self.assertEqual(loaded.key, 'core.cookies.line_failed_item')
        self.assertEqual(en(loaded), english)
        # H6: the cookie line itself is nowhere.
        self.assertNotIn(COOKIE_SECRET, json.dumps([[kind, messages.wire(value)] for kind, value in self.events],
                                                   ensure_ascii=False, default=str))
        self.assertNotIn(COOKIE_SECRET, paths.failed_file().read_text(encoding='utf-8'))

    def test_d_h_2_reasons_cut_at_70_keep_their_key(self):
        bad = self.root / 'bad-cookies.txt'
        bad.write_text(f'.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}\n', encoding='utf-8')
        folder = self.root / ('long-folder-' + 'p' * 150)
        missing = folder / 'cookies.txt'
        self.assertGreaterEqual(len(str(missing)), 200 - 40)
        self.run_job([A, B], [], engine.Options(self.out, cookies=str(bad)))
        text = zh_t('core.cookies.not_netscape')
        done = self.item_done(A)
        self.assertMessage(done['error'], 'core.cookies.not_netscape', text)
        self.assertMessage(done['reason'], 'core.cookies.not_netscape', text[:70])
        self.assertEqual(len(messages.zh(done['reason'])), 70, 'the stored reason is the 1.7.0 70-character cut')
        self.assertEqual(en(done['reason']), en(done['error']), 'en shows the whole text (D12)')
        self.assertEnglish(done['reason'])
        self.assertMessage(self.logs()[0], 'core.engine.cannot_start_link', zh_t('core.engine.cannot_start_link', error=text))
        self.assertEnglish(self.logs()[0])
        # A missing cookies file with a long path: the error is cut at 200 and the reason at 70.
        self.events.clear()
        self.run_job([C], [], engine.Options(self.out, cookies=str(missing)))
        full = zh_t('core.cookies.missing', file=str(missing))
        self.assertGreater(len(full), 200)
        done = self.item_done(C)
        self.assertMessage(done['error'], 'core.cookies.missing', full[:200])
        self.assertMessage(done['reason'], 'core.cookies.missing', full[:70])
        whole = en(msg('core.cookies.missing', file=str(missing)))
        self.assertEqual(en(done['error']), whole)
        self.assertEqual(en(done['reason']), whole)
        self.assertIn(str(missing), whole)
        self.assertEnglish(done['reason'], str(missing))
        # (2.0.0, S2-DESIGN.md D7) The stored entry is the key and the whole path; read back, it renders
        # the whole text in either language (the 200-character cut is only the running value).
        stored = next(item for item in self.file()['entries'] if item['url'] == C)
        self.assertEqual(stored['error'], {'k': 'core.cookies.missing', 'p': {'file': str(missing)}})
        self.assertNotIn('msgs', stored)
        loaded = next(item for item in unfinished.load() if item['url'] == C)['error']
        self.assertMessage(loaded, 'core.cookies.missing', full)
        self.assertEqual(en(loaded), whole)

    def test_d_h_3_english_only_texts_through_the_runner(self):
        self.run_job([A], [], engine.Options(self.out, mode='mp5'))
        done = self.item_done(A)
        self.assertMessage(done['error'], 'core.options.unsupported', zh_t('core.options.unsupported'))
        self.assertEqual(zh(done['error']), zh_t('core.options.unsupported'))
        self.assertEqual(en(self.logs()[0]), en_t('core.engine.cannot_start_link', error=en_t('core.options.unsupported')))
        self.assertEqual(zh(self.logs()[0]), zh_t('core.engine.cannot_start_link', error=zh_t('core.options.unsupported')))

    def test_d_h_4_a_raw_engine_line_that_quotes_app_text_passes_unchanged(self):
        def line(runner, command, index, total, files):
            kind, value = runner._intercept(*engine.parse_line(RAW_LINE))
            runner.emit(kind, value)
            runner._note_line(RAW_LINE)
            return 1, False, None

        self.run_job([A], [line])
        logged = [value for value in self.logs() if value == RAW_LINE]
        self.assertEqual(len(logged), 1)
        self.assertIs(type(logged[0]), str, 'raw engine output stays a plain str (D-F24)')
        self.assertEqual(en(logged[0]), RAW_LINE, 'never rewritten in en')
        done = self.item_done(A)
        self.assertIsInstance(done['error'], Raw)
        self.assertEqual(done['error'], RAW_LINE[6:].strip())
        self.assertIsInstance(done['reason'], Raw)
        self.assertEqual(done['reason'], '链接不存在（404）；网站拒绝访问（403）')
        self.assertEqual(en(done['reason']), '链接不存在（404）；网站拒绝访问（403）')
        loaded = unfinished.load()[0]['error']
        self.assertEqual(loaded, RAW_LINE[6:].strip())
        self.assertEqual(en(loaded), RAW_LINE[6:].strip())

    def test_every_app_line_of_a_run_is_a_message(self):
        """Duplicates, a run that cannot start, a failure, the 429 run-out: no plain app text is emitted."""
        def limited(runner, command, index, total, files):
            return 1, True, None

        with mock.patch.object(engine, 'MAX_RATE_LIMIT_RETRIES', 0):
            self.run_job([A, A, B, C], [1, limited], engine.Options(self.out, referers={C: 'not a link'}))
        lines = self.logs()
        keys = [line.key for line in lines]
        self.assertTrue(all(isinstance(line, Message) for line in lines), lines)
        for key in ('core.engine.duplicate_merged', 'core.engine.item_unfinished', 'core.engine.rate_limited_out',
                    'core.engine.cannot_start_link'):
            self.assertIn(key, keys)
        for line in lines:
            self.assertEnglish(line, A, C)
        self.assertMessage(self.item_done(A)['reason'], 'core.reason.exit_code', zh_t('core.reason.exit_code', code=1))


# ============================================================================ the unfinished list (3.4)

class UnfinishedTests(Case):
    def entry(self, url, error='', reason='failed'):
        return unfinished.entry(url, engine._entry_options(self.options), reason, error=error)

    def test_problems_and_notices_are_messages_and_download_log_stays_zh(self):
        """D-F23; test_review_state.py:163 reads the zh WARNING (H-10)."""
        paths.failed_file().parent.mkdir(parents=True, exist_ok=True)
        paths.failed_file().write_bytes(b'x' * (unfinished.MAX_SIZE + 1))
        entries, problem, _stamp = unfinished._read(paths.failed_file())
        size = f'{(unfinished.MAX_SIZE + 1) / 1024 / 1024:.1f}'
        self.assertMessage(problem, 'core.unfinished.problem.too_large', zh_t('core.unfinished.problem.too_large', size=size))
        self.assertEqual(problem.params['size'], size)
        lines = []
        with self.assertLogs('YtDlpDesktop.unfinished', 'WARNING') as logs:
            unfinished.load_for_merge(lines.append)
        self.assertEqual(len(lines), 1)
        target = next(self.state.glob('failed-downloads.bad-*.json'))
        text = zh_t('core.unfinished.kept_aside', problem=zh_t('core.unfinished.problem.too_large', size=size),
                    target=target.name)
        self.assertMessage(lines[0], 'core.unfinished.kept_aside', text)
        self.assertMessage(lines[0].params['problem'], 'core.unfinished.problem.too_large')
        self.assertEqual(logs.output, [f'WARNING:YtDlpDesktop.unfinished:{text}'])
        self.assertEnglish(lines[0], target.name)
        UNKNOWN = zh_t('core.unfinished.problem.unknown_format')
        # (2.0.0 S2 P3) bad_entries (a 1.5.1 list) is gone with the 1.5.1 layer; (S2 Q2) a file that is not
        # version 2 cannot be recognised (and is set aside, test_unfinished.ForeignListTests).
        cases = {'{"version": 1, "entries": [': ('core.unfinished.problem.damaged', zh_t('core.unfinished.problem.damaged')),
                 '{"version": 2}': ('core.unfinished.problem.unknown_format', UNKNOWN),
                 '{"version": 3, "entries": []}': ('core.unfinished.problem.unknown_format', UNKNOWN),
                 '{"version": 2, "entries": [{"key": "k"}]}': ('core.unfinished.problem.dropped',
                                                              zh_t('core.unfinished.problem.dropped', dropped=1)),
                 '{"version": 1, "urls": ["not a link"], "options": {"output": "D:/x"}}':
                     ('core.unfinished.problem.unknown_format', UNKNOWN),
                 '{"version": 1, "entries": []}': ('core.unfinished.problem.unknown_format', UNKNOWN)}
        for content, (key, text) in cases.items():
            paths.failed_file().write_text(content, encoding='utf-8')
            self.assertMessage(unfinished._read(paths.failed_file())[1], key, text)

    def test_set_aside_and_overflow_errors_are_messages(self):
        path = paths.failed_file()
        with mock.patch.object(unfinished.os, 'rename', side_effect=FileExistsError(17, 'exists')), \
                mock.patch.object(unfinished.os.path, 'lexists', return_value=False):
            error = raised(unfinished._keep_unreadable, path, msg('core.unfinished.problem.damaged'), None)
        self.assertIsInstance(error, OSError)
        self.assertMessage(messages.message_of(error), 'core.unfinished.cannot_set_aside',
                           zh_t('core.unfinished.cannot_set_aside', path=path))
        self.assertEqual(str(error), zh_t('core.unfinished.cannot_set_aside', path=path))
        with mock.patch.object(unfinished.os, 'rename', side_effect=FileExistsError(17, 'exists')):
            error = raised(unfinished._write_overflow, path, [self.entry(A)], None)
        self.assertMessage(messages.message_of(error), 'core.unfinished.no_overflow_name',
                           zh_t('core.unfinished.no_overflow_name', path=path))
        lines = []
        with mock.patch.object(unfinished, 'SAFE_SIZE', 3000),                 self.assertLogs('YtDlpDesktop.unfinished', 'WARNING') as logs:
            unfinished.save([self.entry(f'https://example.invalid/{number}', error='x' * 150) for number in range(12)],
                            log=lines.append)
        self.assertEqual(logs.output, [f'WARNING:YtDlpDesktop.unfinished:{messages.zh(lines[0])}'])
        self.assertEqual(len(lines), 1)
        target = next(self.state.glob('failed-downloads.overflow-*.json'))
        self.assertMessage(lines[0], 'core.unfinished.overflow')
        self.assertEqual(messages.zh(lines[0]),
                         zh_t('core.unfinished.overflow', count=lines[0].params['count'], target=target.name))
        self.assertEnglish(lines[0], target.name)

    def test_entries_are_read_and_written_in_format_2(self):
        """3.4 (2.0.0 S2 P3, S2-DESIGN.md 1.3): entry() and merge() keep the type (cut at 200 while running),
        the writer stores a message as {"k", "p"} and any other text as itself, _clean reads them back."""
        long_path = 'D:/' + 'q' * 220
        cut = msg('core.engine.cannot_start_link', error=msg('core.cookies.missing', file=long_path))
        echoed = messages.raw('不支持这个链接')       # an engine line equal to an app text
        entries = [self.entry(A, msg('core.proxy.scheme')), self.entry(B, cut), self.entry(C, echoed),
                   self.entry(D, 'ERROR: plain engine text'), self.entry(E)]
        self.assertMessage(entries[1]['error'], 'core.engine.cannot_start_link', messages.zh(cut)[:200])
        self.assertIsInstance(entries[2]['error'], Raw)
        unfinished.save(entries)
        stored = {item['url']: item for item in self.file()['entries']}
        self.assertEqual([stored[url]['error'] for url in (A, B, C, D, E)],
                         [{'k': 'core.proxy.scheme'},
                          {'k': 'core.engine.cannot_start_link',
                           'p': {'error': {'k': 'core.cookies.missing', 'p': {'file': long_path}}}},
                          '不支持这个链接', 'ERROR: plain engine text', ''])
        self.assertNotIn('msgs', json.dumps(self.file(), ensure_ascii=False))
        loaded = {item['url']: item for item in unfinished.load()}
        self.assertMessage(loaded[A]['error'], 'core.proxy.scheme')
        self.assertMessage(loaded[B]['error'], 'core.engine.cannot_start_link', messages.zh(cut))
        self.assertEqual(en(loaded[B]['error']), en(cut), 'the whole path in en')
        self.assertIsInstance(loaded[C]['error'], Raw)
        self.assertEqual(en(loaded[C]['error']), '不支持这个链接', 'never translated')
        self.assertIsInstance(loaded[D]['error'], Raw)
        self.assertEqual(loaded[E]['error'], '')
        # A round trip writes the same bytes.
        before = paths.failed_file().read_bytes()
        with mock.patch.object(unfinished.time, 'time', return_value=0):
            unfinished.save(unfinished.load())
        self.assertEqual(paths.failed_file().read_bytes(), before)

    def test_keys_and_params_of_a_later_version_survive_unfinished_save(self):
        """I11 (2.0.0 form): an unknown key, another placeholder, an unknown nested key are read as messages
        (never raising) and written back byte for byte."""
        later = [{'k': 'core.future.text', 'p': {'n': 1}},
                 {'k': 'core.engine.cannot_start_link', 'p': {'error': 'Qq', 'extra': 2}},
                 {'k': 'core.engine.error', 'p': {'error': {'k': 'core.future.nested'}}}]
        written = [dict(unfinished._written(self.entry(f'https://example.invalid/f{number}')), error=form)
                   for number, form in enumerate(later)]
        written.append(unfinished._written(self.entry(A, 'ERROR: plain')))
        self.write({'version': 2, 'entries': written})
        loaded = unfinished.load()
        for item, form in zip(loaded, later):
            self.assertIsInstance(item['error'], Message)
            self.assertEqual(item['error'].key, form['k'])
            en(item['error'])                               # never raises
            zh(item['error'])
        unfinished.save(loaded)
        again = self.file()['entries']
        self.assertEqual([item['error'] for item in again], later + ['ERROR: plain'], 'kept byte for byte')


def unfinished_keys():
    return {'key', 'format', 'url', 'title', 'reason', 'error', 'time', 'referer', 'options'}


if __name__ == '__main__':
    unittest.main()
