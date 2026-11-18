"""Download core messages (MESSAGES.md 3.2, 3.4, 3.5, 5.2, 7.5; WP-2).

    python -X utf8 -B test_messages_download.py

engine.py, unfinished.py, naming.py, urls.py and formats.py produce messages: every app text they
emit, raise or store is msg(key, ...) whose value is byte for byte the 1.7.0 Chinese text (read
from verification/release-sources/1.7.0 where the 1.7.0 code can be run alone).  Checked here:

* the five modules are converted and clean under G1 (G1a-G1f), APP_VERSION 1.8.0 (WP-6 removed the
  staging flag MESSAGES_READY, MESSAGES.md 7.8: G1 applies to them unconditionally);
* the core H4 constants keep their 1.7.0 values (ParityTests extended, 8.1), with their keys;
* every producer of 3.2 gives its key, the 1.7.0 zh text and an English text in en;
* explain_error and _masked keep the 1.7.0 values and the value's type (D-H-2, P8);
* the regression cases D-H-1 (cookies echo), D-H-2 (cut reasons), D-H-3 (English-only texts) and
  D-H-4 (a raw engine line that quotes app text) through a scripted Runner: key, stored bytes, en
  text via show();
* the unfinished list (3.4, D-F23, D-F30): problems and notices are messages, download.log keeps
  the zh WARNING, entries are decoded on read and encoded on write, foreign sidecar entries are kept
  byte for byte by unfinished.save;
* the failed-list rollback (5.2): the 1.6.0 and 1.7.0 unfinished modules (frozen sources, under
  other names, state redirected) load, merge and save a list 1.8.0 wrote; 1.5.1's
  engine.load_failed_job reads it and its store_failed_job output is read back by 1.8.0.

No window, no network; _run_once is scripted.
"""
import _sandbox
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

import engine
import formats
import i18n_tool
import messages
import naming
import paths
import unfinished
import urls
from messages import Message, Raw, msg

OLD = _sandbox.release_src('1.7.0')
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')
A, B, C, D, E = ('https://example.invalid/' + name for name in 'abcde')
MODULES = ('engine', 'unfinished', 'naming', 'urls', 'formats')
COOKIE_SECRET = 'cookie-secret-value-1234'
ECHO = f"ERROR: invalid Netscape format cookies file 'cookies.txt': '.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}'"
RAW_LINE = 'ERROR: [generic] x: 链接不存在（404）；网站拒绝访问（403）'


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


def old_source(name='engine.py'):
    return (OLD / name).read_text(encoding='utf-8')


def old_constants(names, name='engine.py') -> dict:
    """{NAME: value} of module-level literal assignments of a 1.7.0 source (by AST)."""
    found = {}
    for node in ast.parse(old_source(name)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names:
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def frozen_1_7_0(names, extra=None) -> types.SimpleNamespace:
    """The module-level statements of 1.7.0's engine.py named ``names`` (assignments and functions),
    executed alone with ``re`` and ``urllib.parse``: the 1.7.0 code of a pure helper."""
    import urllib.parse
    tree = ast.parse(old_source())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            body.append(node)
    namespace = {'re': re, 'urllib': urllib, **(extra or {})}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(OLD / 'engine.py'), 'exec'), namespace)
    return types.SimpleNamespace(**{name: namespace[name] for name in names})


def old_unfinished(version):
    """The unfinished.py of an older release (it imports the live formats / paths / urls, whose
    behaviour is that of its own release), loaded under another name."""
    name = 'unfinished_' + version.replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, _sandbox.release_src(version) / 'unfinished.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_engine_1_5_1(state):
    """The engine.py of 1.5.1 (the existing old_engine(state) pattern), its state folder redirected."""
    name = 'engine_1_5_1_messages'
    spec = importlib.util.spec_from_file_location(name, _sandbox.release_src('1.5.1') / 'engine.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # dataclasses looks the module up while the class is built
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    assert module.APP_VERSION == '1.5.1'
    module.STATE_DIR = Path(state)
    return module


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
            self.assertIn(name, i18n_tool.g1_modules(), name)
        self.assertEqual(engine.APP_VERSION, '1.8.0')
        self.assertEqual(old_constants({'APP_VERSION'})['APP_VERSION'], '1.7.0')

    def test_g1_family_is_clean_on_the_five_modules(self):
        context = i18n_tool.GuardContext()
        findings = []
        for name in MODULES:
            findings += [f'{f["rule"]} {f["file"]}:{f["line"]} {f["text"][:60]!r} ({f["detail"]})'
                         for f in i18n_tool.g1_module(context, name)]
            self.assertEqual(i18n_tool.allow_stale(context, name), [], name)
            self.assertEqual(context.allow(name)['pending_keys'], {}, name)
        self.assertEqual(findings, [])
        self.assertIn('engine', i18n_tool.g1_modules())

    def test_the_core_constants_keep_their_1_7_0_values(self):
        """8.1 / P3: the H4 constants are messages with the 1.7.0 values."""
        keys = {'BOT_CHECK_HINT': 'core.engine.bot_check_hint', 'BOT_CHECK_STALE_HINT': 'core.engine.bot_check_stale',
                'NETWORK_REASON': 'core.reason.network', 'BOT_CHECK_REASON': 'core.reason.bot_check',
                'STALE_REASON': 'core.reason.stale', 'ENGINE_ERROR_REASON': 'core.reason.engine_error',
                'COOKIE_LINE_SKIPPED': 'core.cookies.line_skipped', 'COOKIE_LINE_FAILED': 'core.cookies.line_failed'}
        old = old_constants(set(keys) | {'RESOLUTIONS', 'SUBTITLE_FAILURE', 'ENGINE_OPTION_ERROR'})
        self.assertEqual(set(old), set(keys) | {'RESOLUTIONS', 'SUBTITLE_FAILURE', 'ENGINE_OPTION_ERROR'})
        for name, key in keys.items():
            self.assertMessage(getattr(engine, name), key, old[name])
            self.assertEqual(getattr(engine, name), old[name], 'compares equal to the 1.7.0 text')
            self.assertEnglish(getattr(engine, name))
        self.assertEqual(engine.RESOLUTIONS, old['RESOLUTIONS'])
        self.assertNotIsInstance(engine.RESOLUTIONS[0], Message, 'a stored enum value stays a literal (P7)')
        # D-H-1: the item's error for a cookies echo is the text 1.7.0 stored, now with a key.
        self.assertMessage(engine.COOKIE_LINE_ITEM, 'core.cookies.line_failed_item',
                           old['COOKIE_LINE_FAILED'][6:].strip()[:200])
        # S1: the waiting reason of _run_once.
        self.assertMessage(engine.WAIT_PACE_OR_RETRY, 'core.wait.pace_or_retry', '下载间隔 / 重试等待')
        # ERROR_REASONS: the same patterns, in the same order, with the 1.7.0 reasons.
        tree = ast.parse(old_source())
        node = next(item.value for item in tree.body if isinstance(item, ast.Assign)
                    and getattr(item.targets[0], 'id', '') == 'ERROR_REASONS')
        pairs = [(ast.literal_eval(pair.elts[0].args[0]), ast.literal_eval(pair.elts[1])) for pair in node.elts]
        reason_keys = ['region', 'unavailable', 'page_403', 'page_404', 'page_410', 'network', 'http_403', 'http_404',
                       'http_410', 'format', 'unsupported']
        self.assertEqual(len(engine.ERROR_REASONS), len(pairs))
        for (pattern, reason), (old_pattern, old_reason), key in zip(engine.ERROR_REASONS, pairs, reason_keys):
            self.assertEqual(pattern.pattern, old_pattern)
            self.assertMessage(reason, f'core.reason.{key}', old_reason)
        self.assertEqual(engine._TRANSPORT_FAILURE.pattern,
                         frozen_1_7_0(('ERROR_REASONS', 'NETWORK_REASON', '_TRANSPORT_FAILURE'))._TRANSPORT_FAILURE.pattern)

    def test_formats_and_naming_keep_their_enum_values(self):
        """P7: stored enum values and the H4 naming labels stay literals."""
        presets = [(ast.literal_eval(node.args[1]), ast.literal_eval(node.args[2]))
                   for node in ast.walk(ast.parse(old_source('formats.py')))
                   if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'Preset']
        self.assertEqual([(preset.mode, preset.label) for preset in formats.PRESETS], presets)
        for preset in formats.PRESETS:
            self.assertNotIsInstance(preset.mode, Message)
            self.assertNotIsInstance(preset.label, Message)
        self.assertEqual(naming.LABELS, old_constants({'LABELS'}, 'naming.py')['LABELS'])
        self.assertEqual(formats.BEST, '最佳')


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
                   '第 2 行不是有效的 HTTP / HTTPS 链接。请每行填写一个链接。', kind=ValueError)
        self.check(raised(engine.parse_urls, '# only a comment'), 'core.links.none', '请先粘贴至少一个视频或音频链接。')
        self.check(raised(urls.parse_download_text, 'https://a.example/x\nwords only'), 'core.links.no_link_on_line',
                   '第 2 行没有找到链接。请每行粘贴一个链接。')
        self.check(raised(urls.parse_download_text, '\n# x\n'), 'core.links.none', '请先粘贴至少一个视频或音频链接。')
        big = self.root / 'big.txt'
        big.write_bytes(b'#' * (5 * 1024 * 1024 + 1))
        self.check(raised(engine.read_url_file, big), 'core.links.file_too_large',
                   '链接文件超过 5 MB，请分成多个较小的 TXT 文件导入。')
        broken = self.root / 'broken.txt'
        broken.write_bytes(b'\xff\xfe\x00\xd8')
        self.check(raised(engine.read_url_file, broken), 'core.links.encoding', '无法读取文本编码，请将链接文件另存为 UTF-8。')

    def test_cookie_files(self):
        """D-F36 (no cookie content in any message: H6)."""
        missing = self.root / 'gone' / 'cookies.txt'
        error = self.check(raised(engine.check_cookie_file, missing), 'core.cookies.missing',
                           f'找不到 cookies 文件：{missing}\n请在“偏好设置 → 网络”中重新选择。', str(missing))
        self.assertEqual(dict(error.params), {'file': str(missing)})
        large = self.root / 'large.txt'
        large.write_bytes(b'# Netscape HTTP Cookie File\n' + b'#' * (10 * 1024 * 1024))
        self.check(raised(engine.check_cookie_file, large), 'core.cookies.too_large',
                   'cookies 文件超过 10 MB，请确认选择的是导出的 cookies.txt。')
        exported = self.root / 'cookies.json'
        exported.write_text('[{"name": "SID", "value": "%s"}]' % COOKIE_SECRET, encoding='utf-8')
        message = self.check(raised(engine.check_cookie_file, exported), 'core.cookies.json',
                             '这个 cookies 文件是 JSON 格式，yt-dlp 需要 Netscape 格式的 cookies.txt，请重新导出。')
        self.assertEqual(dict(message.params), {})
        plain = self.root / 'plain.txt'
        plain.write_text(f'.example.com\tTRUE\t/\tFALSE\t0\tSID\t{COOKIE_SECRET}\n', encoding='utf-8')
        message = self.check(raised(engine.check_cookie_file, plain), 'core.cookies.not_netscape',
                             '这个文件不是 Netscape 格式的 cookies.txt（第一行应为 “# Netscape HTTP Cookie File”），请重新导出。')
        self.assertNotIn(COOKIE_SECRET, json.dumps(messages.wire(message), ensure_ascii=False))

    def test_options_and_formats_english_texts_are_keyed(self):
        """D-H-3 / D-F38: the English-only texts get keys whose zh text is today's English."""
        cases = [
            (raised(engine.Options.from_dict, []), 'core.options.not_object', 'Options data must be an object'),
            (raised(engine.Options.from_dict, {'output': ' '}), 'core.options.no_output',
             'Options data has no usable output folder'),
            (raised(engine.Options.from_dict, {'output': 'D:/x', 'mode': 'mp5'}), 'core.formats.bad_mode',
             "Unsupported output format: 'mp5'"),
            (raised(engine.Options.from_dict, {'output': 'D:/x', 'resolution': 99}), 'core.options.bad_resolution',
             'Unsupported resolution: 99'),
            (raised(engine.build_command, A, engine.Options(self.out, mode='mp5')), 'core.options.unsupported',
             'Unsupported output option'),
            (raised(formats.by_mode, '音频'), 'core.formats.bad_mode', "Unsupported output format: '音频'"),
            (raised(formats.by_id, 'x'), 'core.formats.bad_id', "Unsupported output format id: 'x'"),
            (raised(formats.by_label, None), 'core.formats.bad_label', 'Unsupported output format label: None'),
            (raised(formats.by_key, 'video_x@1080p'), 'core.formats.bad_id', "Unsupported output format id: 'video_x'"),
        ]
        for error, key, text in cases:
            message = self.check(error, key, text, "'音频'", kind=ValueError)
            self.assertEqual(zh(message), text, 'zh shows the 1.7.0 English text (D13)')
            self.assertEqual(en(message), text)

    def test_build_command_errors(self):
        """D-F16 nested producers."""
        self.check(raised(engine.build_command, A, engine.Options(self.out, proxy='ftp://proxy.example')),
                   'core.proxy.scheme', '代理地址应以 http://、https:// 或 socks5:// 开头。')
        self.check(raised(engine.build_command, A, engine.Options(self.out, referers={A: 'not a link'})),
                   'core.collect.bad_source', '采集来源地址无效，请重新采集后加入下载列表。')
        long_url = 'https://example.invalid/' + 'x' * 33000
        error = self.check(raised(engine.build_command, long_url, self.options), 'core.links.too_long',
                           f'链接过长（{len(long_url)} 个字符），无法交给下载引擎：{urls.display(long_url)}',
                           urls.display(long_url), kind=engine.LinkTooLong)
        self.assertEqual(dict(error.params), {'length': len(long_url), 'url': urls.display(long_url)})

    def test_naming_notices(self):
        """D-F06: the four rename notices."""
        source = self.root / 'Song [abc].mp3'
        source.write_bytes(b'x')
        cases = [
            (PermissionError(13, 'in use'), 'core.rename.locked', f'文件正被其他程序占用，未能改名，已保留原文件名：{source.name}'),
            (OSError(5, 'Access is denied'), 'core.rename.failed_named',
             f'文件未能改名（Access is denied），已保留原文件名：{source.name}'),
            (FileExistsError(17, 'exists'), 'core.rename.too_many', f'同名文件过多，未能改名，已保留原文件名：{source.name}'),
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
        self.assertMessage(lines[0], 'core.rename.failed', '文件未能改名（TypeError），已保留原文件名。')
        self.assertEnglish(lines[0])

    def test_explain_error_keeps_1_7_0_values_and_types(self):
        """D-F28: the same values as 1.7.0's explain_error; a Message fallback keeps its key (D-H-2)."""
        old = frozen_1_7_0(('ERROR_REASONS', 'NETWORK_REASON', 'BOT_CHECK_REASON', 'STALE_REASON',
                            '_EXTRACTOR_PREFIX', 'explain_error')).explain_error
        samples = ['ERROR: [youtube] AAA: Video unavailable', 'HTTP Error 404: Not Found', 'timed out',
                   'Unable to download webpage: HTTP Error 403: Forbidden', 'Requested format is not available',
                   '[youtube] AAA: Something odd happened ' + 'z' * 90, '[WinError 5] 拒绝访问。', '', None,
                   '  spaced  ']
        for text in samples:
            for flags in ({}, {'bot_check': True}, {'stale': True}, {'code': 7}):
                new = engine.explain_error(text, **flags)
                self.assertEqual(messages.zh(new), old(text, **flags), (text, flags))
        self.assertMessage(engine.explain_error('', 7), 'core.reason.exit_code', '下载未完成（退出码 7）')
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
        old = frozen_1_7_0(('_masked',)).__dict__['_masked']
        text = f'Unable to connect to proxy {proxy} (user:secret@ again)'
        self.assertEqual(engine._masked(text, options), old(text, options))
        self.assertIs(type(engine._masked(text, options)), str)
        line = messages.raw(text)
        self.assertIsInstance(engine._masked(line, options), Raw)
        self.assertEqual(engine._masked(line, options), old(text, options))
        message = msg('core.engine.record_unreadable', error=text)
        masked = engine._masked(message, options)
        self.assertMessage(masked, 'core.engine.record_unreadable', old(messages.zh(message), options))
        self.assertNotIn('secret', json.dumps(messages.wire(masked), ensure_ascii=False))
        self.assertIn(messages.PROXY_TOKEN, masked.params['error'])
        self.assertNotIn('secret', en(masked))
        self.assertIn('[proxy]', en(masked))
        self.assertIs(engine._masked(message, engine.Options(self.out)), message, 'nothing to mask: the same object')

    def test_wait_and_unfinished_notices(self):
        """D-F01 / D-F25: the waiting line nests the caller's reason."""
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        runner.cancelled.set()
        runner._wait(2.5, 1, 1, msg('core.wait.rate_retry', attempt=1, limit=2))
        line = self.logs()[-1]
        self.assertMessage(line, 'core.engine.waiting', '网站限流，冷却后重试此链接（1/2），等待 3 秒；可随时点击停止。')
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
        fragment = 'cookies 文件中有一行格式不对，无法读取（内容不记录）'      # what 1.7.0 stored
        self.assertMessage(done['error'], 'core.cookies.line_failed_item', fragment)
        self.assertMessage(done['reason'], 'core.cookies.line_failed_item', fragment)
        english = 'A line of the cookies file has a wrong format and cannot be read (content not logged)'
        self.assertEqual(en(done['error']), english)
        self.assertEqual(en(done['reason']), english)
        # Stored: the 1.7.0 bytes; read back: the key (the frozen legacy text lifts it, so no sidecar is needed).
        stored = self.file()['entries'][0]
        self.assertEqual(stored['error'], fragment)
        loaded = unfinished.load()[0]['error']
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
        text = '这个文件不是 Netscape 格式的 cookies.txt（第一行应为 “# Netscape HTTP Cookie File”），请重新导出。'
        done = self.item_done(A)
        self.assertMessage(done['error'], 'core.cookies.not_netscape', text)
        self.assertMessage(done['reason'], 'core.cookies.not_netscape', text[:70])
        self.assertEqual(len(messages.zh(done['reason'])), 70, 'the stored reason is the 1.7.0 70-character cut')
        self.assertEqual(en(done['reason']), en(done['error']), 'en shows the whole text (D12)')
        self.assertEnglish(done['reason'])
        self.assertMessage(self.logs()[0], 'core.engine.cannot_start_link', f'此链接无法开始：{text}')
        self.assertEnglish(self.logs()[0])
        # A missing cookies file with a long path: the error is cut at 200 and the reason at 70.
        self.events.clear()
        self.run_job([C], [], engine.Options(self.out, cookies=str(missing)))
        full = f'找不到 cookies 文件：{missing}\n请在“偏好设置 → 网络”中重新选择。'
        self.assertGreater(len(full), 200)
        done = self.item_done(C)
        self.assertMessage(done['error'], 'core.cookies.missing', full[:200])
        self.assertMessage(done['reason'], 'core.cookies.missing', full[:70])
        whole = en(msg('core.cookies.missing', file=str(missing)))
        self.assertEqual(en(done['error']), whole)
        self.assertEqual(en(done['reason']), whole)
        self.assertIn(str(missing), whole)
        self.assertEnglish(done['reason'], str(missing))
        # The stored entry keeps the 1.7.0 bytes and, because the cut text alone no longer gives the
        # whole path back, a sidecar entry with the key and the whole path.
        stored = next(item for item in self.file()['entries'] if item['url'] == C)
        self.assertEqual(stored['error'], full[:200])
        self.assertEqual(stored['msgs'], {full[:200]: {'k': 'core.cookies.missing', 'p': {'file': str(missing)}}})
        loaded = next(item for item in unfinished.load() if item['url'] == C)['error']
        self.assertMessage(loaded, 'core.cookies.missing', full[:200])
        self.assertEqual(loaded.origin, 'stored')
        self.assertEqual(en(loaded), whole)

    def test_d_h_3_english_only_texts_through_the_runner(self):
        self.run_job([A], [], engine.Options(self.out, mode='mp5'))
        done = self.item_done(A)
        self.assertMessage(done['error'], 'core.options.unsupported', 'Unsupported output option')
        self.assertEqual(zh(done['error']), 'Unsupported output option')
        self.assertEqual(en(self.logs()[0]), 'This link could not start: Unsupported output option')
        self.assertEqual(zh(self.logs()[0]), '此链接无法开始：Unsupported output option')

    def test_d_h_4_a_raw_engine_line_that_quotes_app_text_passes_unchanged(self):
        def line(runner, command, index, total, files):
            kind, value = runner._intercept(*engine.parse_line(RAW_LINE))
            runner.emit(kind, value)
            runner._note_line(RAW_LINE)
            return 1, False, None

        with _sandbox.fixture_texts('D-H-4: engine output that quotes app text is recognisable by design'):
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
        with _sandbox.fixture_texts('D-H-4: the stored raw line'):
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
        self.assertMessage(self.item_done(A)['reason'], 'core.reason.exit_code', '下载未完成（退出码 1）')


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
        self.assertMessage(problem, 'core.unfinished.problem.too_large', f'超过 5 MB（{size} MB）')
        self.assertEqual(problem.params['size'], size)
        lines = []
        with self.assertLogs('YtDlpDesktop.unfinished', 'WARNING') as logs:
            unfinished.load_for_merge(lines.append)
        self.assertEqual(len(lines), 1)
        target = next(self.state.glob('failed-downloads.bad-*.json'))
        text = f'未完成清单（failed-downloads.json）超过 5 MB（{size} MB），已原样保留为 {target.name}（没有删除），本次结果写入新的清单。'
        self.assertMessage(lines[0], 'core.unfinished.kept_aside', text)
        self.assertMessage(lines[0].params['problem'], 'core.unfinished.problem.too_large')
        self.assertEqual(logs.output, [f'WARNING:YtDlpDesktop.unfinished:{text}'])
        self.assertEnglish(lines[0], target.name)
        cases = {'{"version": 1, "entries": [': ('core.unfinished.problem.damaged', '内容已损坏'),
                 '{"version": 2}': ('core.unfinished.problem.unknown_format', '格式无法识别'),
                 '{"version": 1, "entries": [{"key": "k"}]}': ('core.unfinished.problem.dropped', '有 1 条记录无法识别'),
                 '{"version": 1, "urls": ["not a link"], "options": {"output": "D:/x"}}':
                     ('core.unfinished.problem.bad_entries', '链接或设置无法识别')}
        for content, (key, text) in cases.items():
            paths.failed_file().write_text(content, encoding='utf-8')
            self.assertMessage(unfinished._read(paths.failed_file())[1], key, text)

    def test_set_aside_and_overflow_errors_are_messages(self):
        path = paths.failed_file()
        with mock.patch.object(unfinished.os, 'rename', side_effect=FileExistsError(17, 'exists')), \
                mock.patch.object(unfinished.os.path, 'lexists', return_value=False):
            error = raised(unfinished._set_aside, path)
        self.assertIsInstance(error, OSError)
        self.assertMessage(messages.message_of(error), 'core.unfinished.cannot_set_aside', f'无法保留读不出的未完成清单：{path}')
        self.assertEqual(str(error), f'无法保留读不出的未完成清单：{path}')
        with mock.patch.object(unfinished.os, 'rename', side_effect=FileExistsError(17, 'exists')):
            error = raised(unfinished._write_overflow, path, [self.entry(A)], None)
        self.assertMessage(messages.message_of(error), 'core.unfinished.no_overflow_name',
                           f'找不到可用的文件名保存未完成清单的较早部分：{path}')
        lines = []
        with mock.patch.object(unfinished, 'SAFE_SIZE', 3000),                 self.assertLogs('YtDlpDesktop.unfinished', 'WARNING') as logs:
            unfinished.save([self.entry(f'https://example.invalid/{number}', error='x' * 150) for number in range(12)],
                            log=lines.append)
        self.assertEqual(logs.output, [f'WARNING:YtDlpDesktop.unfinished:{messages.zh(lines[0])}'])
        self.assertEqual(len(lines), 1)
        target = next(self.state.glob('failed-downloads.overflow-*.json'))
        self.assertMessage(lines[0], 'core.unfinished.overflow')
        self.assertEqual(messages.zh(lines[0]),
                         f'未完成清单过长，已把最早的 {lines[0].params["count"]} 条移到 {target.name}（没有删除）。')
        self.assertEnglish(lines[0], target.name)

    def test_entries_are_decoded_on_read_and_encoded_on_write(self):
        """3.4: entry() and merge() keep the type (cut at 200), _clean decodes, the writer encodes; a
        sidecar entry is written only where the stored text alone would not give the message back."""
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
                         [messages.zh(entry['error']) for entry in entries])
        self.assertNotIn('msgs', stored[A], 'the stored text alone gives core.proxy.scheme back')
        self.assertEqual(stored[B]['msgs'], {messages.zh(cut)[:200]: {
            'k': 'core.engine.cannot_start_link', 'p': {'error': {'k': 'core.cookies.missing', 'p': {'file': long_path}}}}})
        self.assertEqual(stored[C]['msgs'], {'不支持这个链接': None}, 'E2: a raw text the lift would translate')
        self.assertNotIn('msgs', stored[D])
        self.assertNotIn('msgs', stored[E])
        loaded = {item['url']: item for item in unfinished.load()}
        self.assertMessage(loaded[A]['error'], 'core.proxy.scheme')
        self.assertMessage(loaded[B]['error'], 'core.engine.cannot_start_link', messages.zh(cut)[:200])
        self.assertEqual(loaded[B]['error'].origin, 'stored')
        self.assertEqual(en(loaded[B]['error']), en(cut), 'the whole path in en')
        self.assertIsInstance(loaded[C]['error'], Raw)
        self.assertEqual(en(loaded[C]['error']), '不支持这个链接', 'never translated')
        self.assertIs(type(loaded[D]['error']), str)
        self.assertNotIn('msgs', loaded[B])
        # A round trip writes the same bytes.
        before = paths.failed_file().read_bytes()
        with mock.patch.object(unfinished.time, 'time', return_value=0):
            unfinished.save(unfinished.load())
        self.assertEqual(paths.failed_file().read_bytes(), before)

    def test_foreign_sidecar_entries_survive_unfinished_save(self):
        """I11 / E1b: entries of a later version (an unknown key, other placeholders, an unknown nested key)."""
        foreign = {'未来的文字一': {'k': 'core.future.text', 'p': {'n': 1}},
                   '此链接无法开始：Qq': {'k': 'core.engine.cannot_start_link', 'p': {'error': 'Qq', 'extra': 2}},
                   '错误：x': {'k': 'core.engine.error', 'p': {'error': {'k': 'core.future.nested'}}}}
        written = []
        for number, (text, entry) in enumerate(foreign.items()):
            item = unfinished._written(self.entry(f'https://example.invalid/f{number}', text))
            item['msgs'] = {text: entry}
            written.append(item)
        written.append(unfinished._written(self.entry(A, 'ERROR: plain')))
        self.write({'version': 1, 'urls': [A], 'options': engine._entry_options(self.options), 'entries': written})
        with _sandbox.fixture_texts('foreign sidecar entries of a later version (I11)'):
            loaded = unfinished.load()
            for item, (text, entry) in zip(loaded, foreign.items()):
                self.assertIsInstance(item['error'], Message)
                self.assertEqual(item['error'].origin, 'foreign')
                self.assertEqual(json.loads(item['error'].entry), entry)
                self.assertEqual(zh(item['error']), text)
                en(item['error'])                               # never raises
            unfinished.save(loaded)
        again = self.file()['entries']
        for item, (text, entry) in zip(again, foreign.items()):
            self.assertEqual(item['error'], text)
            self.assertEqual(item['msgs'], {text: entry}, 'kept byte for byte')
        self.assertNotIn('msgs', again[-1])


# ============================================================================ failed-list rollback (5.2)

class RollbackTests(Case):
    def write_1_8_0_list(self):
        """A list as 1.8.0 writes it: messages, Raw errors, sidecars, a foreign entry."""
        cookies = self.root / ('folder-' + 'r' * 200) / 'cookies.txt'

        def echo(runner, command, index, total, files):
            runner._note_line(ECHO)
            return 1, False, None

        def engine_line(runner, command, index, total, files):
            runner._note_line('ERROR: [youtube] B: Video unavailable')
            return 1, False, None

        def echoed(runner, command, index, total, files):
            runner._note_line('ERROR: 不支持这个链接')
            return 1, False, None

        with _sandbox.fixture_texts('an engine line equal to an app text (E2: stored as Raw with a null entry)'):
            self.run_job([A, B, C], [echo, engine_line, echoed])
            # Another format (MP3, one 1.5.1 knows): the newest group of the legacy view is this link
            # alone.  (This run writes the Raw entry of C again.)
            self.run_job([D], [], engine.Options(self.out, formats.MODES[3], cookies=str(cookies)))
        data = self.file()
        foreign = {'k': 'core.future.text', 'p': {'n': 1}}
        data['entries'][0]['msgs'] = {data['entries'][0]['error']: foreign}
        self.write(data)
        return data, foreign

    def test_the_1_8_0_list_has_messages_raw_errors_and_sidecars(self):
        data, _foreign = self.write_1_8_0_list()
        self.assertEqual([item['url'] for item in data['entries']], [A, B, C, D])
        self.assertEqual(data['urls'], [D], 'the legacy view 1.5.1 reads')
        self.assertEqual(set(data), {'version', 'urls', 'options', 'entries'}, 'no new top-level key (I2)')
        self.assertEqual(data['entries'][2]['msgs'], {'不支持这个链接': None})
        self.assertEqual(list(data['entries'][3]['msgs'].values())[0]['k'], 'core.cookies.missing')
        for item in data['entries']:
            self.assertTrue(set(item) <= unfinished_keys() | {'msgs'}, item.keys())

    def test_1_6_0_and_1_7_0_load_merge_and_save_a_1_8_0_list(self):
        for version in ('1.6.0', '1.7.0'):
            with self.subTest(version=version):
                if paths.failed_file().exists():
                    paths.failed_file().unlink()
                data, foreign = self.write_1_8_0_list()
                old = old_unfinished(version)
                entries = old.load()
                self.assertEqual([item['url'] for item in entries], [A, B, C, D])
                self.assertEqual([item['error'] for item in entries], [item['error'] for item in data['entries']])
                # B finished, C failed again (rebuilt), A and D untouched.
                results = [{'url': B, 'key': entries[1]['key'], 'format': entries[1]['format'], 'outcome': 'done'},
                           {'url': C, 'key': entries[2]['key'], 'format': entries[2]['format'], 'outcome': 'failed',
                            'error': 'ERROR: again', 'title': '', 'referer': '', 'options': entries[2]['options']}]
                with old.LOCK:
                    merged = old.merge(old.load_for_merge(), results)
                    old.save(merged)
                written = self.file()['entries']
                self.assertEqual([item['url'] for item in written], [A, C, D])
                self.assertEqual(written[0]['msgs'], {data['entries'][0]['error']: foreign}, 'kept on an untouched entry')
                self.assertEqual(written[0]['error'], data['entries'][0]['error'])
                self.assertEqual(written[2]['msgs'], data['entries'][3]['msgs'])
                self.assertEqual(written[2]['error'], data['entries'][3]['error'])
                self.assertNotIn('msgs', written[1], 'absent on the rebuilt entry')
                self.assertEqual(written[1]['error'], 'ERROR: again')
                # 1.8.0 decodes all of it.
                with _sandbox.fixture_texts('a foreign entry of a later version (I11)'):
                    loaded = unfinished.load()
                self.assertEqual(loaded[0]['error'].origin, 'foreign')
                self.assertEqual(json.loads(loaded[0]['error'].entry), foreign)
                self.assertIs(type(loaded[1]['error']), str)
                self.assertMessage(loaded[2]['error'], 'core.cookies.missing', data['entries'][3]['error'])
                self.assertEqual(loaded[2]['error'].origin, 'stored')
                self.assertEqual(en(loaded[2]['error']).count('r' * 200), 1, 'the whole path in en')

    def test_1_5_1_reads_the_legacy_view_and_1_8_0_reads_what_1_5_1_stores(self):
        self.write_1_8_0_list()
        legacy = old_engine_1_5_1(self.state)
        job = legacy.load_failed_job()
        self.assertEqual(job['urls'], [D])
        self.assertEqual((job['options'].mode, Path(job['options'].output)), (formats.MODES[3], self.out))
        legacy.store_failed_job([A, B], legacy.Options(self.out))
        written = self.file()
        self.assertNotIn('entries', written, 'the file is now what 1.5.1 writes')
        loaded = unfinished.load()
        self.assertEqual([(item['url'], item['error']) for item in loaded], [(A, ''), (B, '')])
        self.assertEqual(engine.load_failed_job()['urls'], [A, B])


def unfinished_keys():
    return {'key', 'format', 'url', 'title', 'reason', 'error', 'time', 'referer', 'options'}


if __name__ == '__main__':
    unittest.main()
