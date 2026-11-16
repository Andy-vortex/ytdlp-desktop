"""The download queue of 1.6.0 (WP2 step G): per-item options, enqueue while running,
spacing, the bot-check block, already-downloaded skips, engine errors, renaming, and the
event payloads (R10, R11).

_run_once is scripted and _wait is recorded unless a test says otherwise; the tests that
use the real loop start ``python -c`` children.  No website and no yt-dlp process.
"""
import _sandbox
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import engine
import formats
import paths
import unfinished
import urls as urltools

A, B, C, D, E = ('https://example.invalid/' + name for name in 'abcde')
Y1, Y2, Y3 = ('https://www.youtube.com/watch?v=' + name for name in ('AAA', 'BBB', 'CCC'))
OLD_ITEM_DONE = {'index', 'cancelled', 'success', 'files', 'bot_check'}
NEW_ITEM_DONE = {'url', 'origin', 'title', 'path', 'error', 'reason', 'skipped', 'stale_engine', 'format'}
DONE_KEYS = {'success', 'failed', 'files', 'cancelled', 'failed_urls', 'pending_urls', 'failed_path', 'archive_errors',
             'bot_check', 'total', 'skipped', 'blocked', 'unfinished', 'engine_error'}


def ok(runner, command, index, total, files):
    return 0, False, None


def fail(runner, command, index, total, files):
    return 1, False, None


def bot(runner, command, index, total, files):
    runner.bot_check = True
    runner.last_error = '[youtube] AAA: Sign in to confirm you’re not a bot.'
    return 1, False, None


def boom(runner, command, index, total, files):
    raise RuntimeError('boom')


def saves(name):
    """A run that reports one file in the folder the command was given."""
    def step(runner, command, index, total, files):
        files.append(str(Path(command[command.index('-P') + 1]) / name))
        return 0, False, None
    return step


class FakeLibrary:
    """Only what the queue asks of the history: find_download."""

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or {}, error, []

    def find_download(self, keys, format):
        self.calls.append((list(keys), format))
        if self.error:
            raise self.error
        return self.rows.get((keys[0], format))


class QueueCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patcher = _sandbox.state_patch(self.root / 'state')
        patcher.start()
        self.addCleanup(patcher.stop)
        engine.Runner.reset_pacing()
        self.addCleanup(engine.Runner.reset_pacing)
        self.pace((0, 0))
        self.out = self.root / 'out'
        self.options = engine.Options(self.out)
        self.events, self.waits, self.commands = [], [], []

    def pace(self, seconds):
        patcher = mock.patch.object(engine, 'PACE_SECONDS', seconds)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_queue(self, links, steps, options=None, overrides=None, hook=None, library=None, wait=None, origins=None,
                  runner=None):
        """One scripted download(); returns the runner.  ``steps`` are used in order; when they
        run out the test fails.  ``hook(runner, kind, value)`` sees every event as it is emitted."""
        remaining, before = list(steps), len(self.events)

        def emit(kind, value):
            self.events.append((kind, value))
            if hook:
                hook(runner, kind, value)

        def run_once(command, index, total, files):
            self.commands.append(command)
            if not remaining:
                raise AssertionError(f'unexpected engine start for {command[-1]}')
            return remaining.pop(0)(runner, command, index, total, files)

        def waiting(seconds, index, total, reason):
            self.waits.append((seconds, index, total, reason, runner.wait_kind))
            return wait(runner) if wait else True

        runner = runner or engine.Runner(emit, library=library)
        runner.emit = emit
        if origins:
            runner.origins = origins
        with mock.patch.object(runner, '_run_once', side_effect=run_once), \
                mock.patch.object(runner, '_wait', side_effect=waiting):
            if overrides is None:
                runner.download(links, options or self.options)
            else:
                runner.download(links, options or self.options, overrides)
        self.assertEqual(remaining, [], 'scripted engine runs that were never used')
        kinds = [kind for kind, _value in self.events[before:]]
        self.assertEqual((kinds[-1], kinds.count('done')), ('done', 1), 'exactly one done event, and it is the last')
        return runner

    def of(self, kind):
        return [value for name, value in self.events if name == kind]

    def done(self):
        return self.of('done')[-1]

    def logs(self):
        return self.of('log')

    def stored(self):
        return {(entry['url'], entry['format']): entry['reason'] for entry in unfinished.load()}

    def started(self):
        """The links the engine was started for, in order."""
        return [command[-1] for command in self.commands]


class ItemTests(QueueCase):
    def test_payloads_of_a_plain_run(self):
        self.run_queue([A, B], [saves('a.opus'), fail], origins={A: A + '?utm=1'})
        self.assertEqual(self.of('item'), [
            {'index': 1, 'total': 2, 'url': A, 'key': urltools.link_key(A), 'format': 'audio_best', 'origin': A + '?utm=1',
             'multi': False},
            {'index': 2, 'total': 2, 'url': B, 'key': urltools.link_key(B), 'format': 'audio_best', 'origin': B,
             'multi': False}])
        first, second = self.of('item_done')
        self.assertEqual(set(first), OLD_ITEM_DONE | NEW_ITEM_DONE)
        self.assertEqual(first, {'index': 1, 'cancelled': False, 'success': True, 'files': 1, 'bot_check': False, 'url': A,
                                 'origin': A + '?utm=1', 'title': '', 'path': '', 'error': '', 'reason': '', 'skipped': '',
                                 'stale_engine': False, 'format': 'audio_best'})
        self.assertEqual(second, {'index': 2, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False, 'url': B,
                                  'origin': B, 'title': '', 'path': '', 'error': '', 'reason': '下载未完成（退出码 1）',
                                  'skipped': '', 'stale_engine': False, 'format': 'audio_best'})
        done = self.done()
        self.assertEqual(set(done), DONE_KEYS)
        self.assertEqual(done, {'success': 1, 'failed': 1, 'files': [str(self.out / 'a.opus')], 'cancelled': False,
                                'failed_urls': [B], 'pending_urls': [B], 'failed_path': str(paths.failed_file()),
                                'archive_errors': [], 'bot_check': False, 'total': 2, 'skipped': 0, 'blocked': '',
                                'unfinished': 1, 'engine_error': False})
        self.assertEqual(self.stored(), {(B, 'audio_best'): 'failed'})
        self.assertEqual(self.waits, [])
        self.assertEqual(self.of('queued'), [])

    def test_exit_code_zero_without_a_file_is_success(self):
        self.run_queue([A], [ok])
        self.assertEqual((self.done()['success'], self.done()['failed'], self.done()['files']), (1, 0, []))
        self.assertTrue(self.of('item_done')[0]['success'])
        self.assertEqual(self.of('item_done')[0]['files'], 0)
        self.assertIsNone(self.done()['failed_path'])
        self.assertFalse(paths.failed_file().exists())

    def test_error_text_and_reason_of_a_failed_item(self):
        def unavailable(runner, command, index, total, files):
            runner.last_error = '[youtube] abc: Video unavailable. This video has been removed'
            return 1, False, None

        def stale(runner, command, index, total, files):
            runner.last_error = '[youtube] abc: Failed to extract any player response'
            runner.stale = True
            return 1, False, None

        self.run_queue([A, B, C], [unavailable, stale, fail])
        first, second, third = self.of('item_done')
        self.assertEqual((first['error'], first['reason'], first['stale_engine']),
                         ('[youtube] abc: Video unavailable. This video has been removed', '视频不存在或已设为私享', False))
        self.assertEqual((second['reason'], second['stale_engine']), ('解析失败，下载引擎可能已过期', True))
        self.assertEqual((third['error'], third['reason'], third['stale_engine']), ('', '下载未完成（退出码 1）', False),
                         'what one link reported does not stick to the next')
        self.assertEqual([entry['error'] for entry in unfinished.load()], [first['error'], second['error'], ''])

    def test_the_proxy_address_is_not_kept_with_the_error(self):
        proxy = 'http://user:topsecret@127.0.0.1:7890'

        def refused(runner, command, index, total, files):
            runner.last_error = f'Unable to connect to proxy {proxy}: connection refused (user:topsecret@127.0.0.1:7890)'
            return 1, False, None

        self.run_queue([A], [refused], options=replace(self.options, proxy=proxy))
        error = self.of('item_done')[0]['error']
        self.assertNotIn('topsecret', error)
        self.assertIn('[代理]', error)
        self.assertNotIn('topsecret', paths.failed_file().read_text(encoding='utf-8'))

    def test_r10_a_link_that_cannot_start_fails_alone(self):
        options = replace(self.options, referers={A: 'not a link'})
        self.run_queue([A, B], [saves('b.opus')], options=options)
        self.assertEqual(self.started(), [B], 'the bad link never reaches the engine, the next one runs')
        self.assertEqual([kind for kind, _ in self.events],
                         ['item', 'log', 'item_done', 'item', 'item_done', 'done'])
        self.assertEqual(self.logs(), ['此链接无法开始：采集来源地址无效，请重新采集后加入下载列表。'])
        bad, good = self.of('item_done')
        self.assertEqual((bad['index'], bad['success'], bad['cancelled'], bad['files'], bad['bot_check']), (1, False, False, 0, False))
        self.assertEqual(bad['error'], '采集来源地址无效，请重新采集后加入下载列表。')
        self.assertEqual(bad['reason'], '采集来源地址无效，请重新采集后加入下载列表。')
        self.assertTrue(good['success'])
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['total'], done['failed_urls'], done['pending_urls']),
                         (1, 1, 2, [A], [A]))
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'failed'})

    def test_r10_other_things_that_fail_one_link_only(self):
        blocker = self.root / 'a-file'
        blocker.write_text('x', encoding='utf-8')
        cookies = self.root / 'missing-cookies.txt'
        overrides = {A: engine.Options(blocker / 'below-a-file'),                    # the folder cannot be created
                     B: replace(self.options, cookies=str(cookies)),                 # the cookies file is gone
                     C: replace(self.options, proxy='ftp://127.0.0.1'),              # not a proxy address
                     D: replace(self.options, mode='no such format')}                # options from a newer version
        self.run_queue([A, B, C, D, E], [saves('e.opus')], overrides=overrides)
        self.assertEqual(self.started(), [E])
        self.assertEqual([value['success'] for value in self.of('item_done')], [False, False, False, False, True])
        self.assertEqual(len([line for line in self.logs() if line.startswith('此链接无法开始：')]), 4)
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['total'], done['engine_error']), (1, 4, 5, False))
        self.assertEqual(done['failed_urls'], [A, B, C, D])
        # The item with a format this version does not know cannot be stored for a retry.
        self.assertEqual(sorted(url for url, _format in self.stored()), [A, B, C])
        self.assertEqual(self.of('item')[3]['format'], '')

    def test_every_item_event_gets_exactly_one_item_done(self):
        def cancel(runner, command, index, total, files):
            runner.cancel()
            return 1, False, None

        self.run_queue([A, B, C, D], [ok, fail, cancel], options=replace(self.options, referers={A: 'x'}))
        items = [value['index'] for value in self.of('item')]
        finished = [value['index'] for value in self.of('item_done')]
        self.assertEqual(items, [1, 2, 3, 4][:len(items)])
        self.assertEqual(finished, items)
        self.assertEqual(self.of('item_done')[-1]['cancelled'], True)

    def test_an_exception_in_the_engine_loop_ends_the_queue_without_item_done(self):
        with self.assertLogs('YtDlpDesktop.engine', 'ERROR'):
            runner = self.run_queue([A, B, C], [ok, boom])
        self.assertEqual([value['index'] for value in self.of('item')], [1, 2])
        self.assertEqual([value['index'] for value in self.of('item_done')], [1], 'the 1.5.1 exception path: no item_done')
        self.assertIn('错误：boom', self.logs())
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['failed_urls'], done['pending_urls'], done['total']),
                         (1, 1, [B], [B, C], 3))
        self.assertEqual([item.state for item in runner.items], ['done', 'failed', 'unstarted'])
        self.assertEqual(runner.items[1].error, 'boom')
        self.assertEqual(self.stored(), {(B, 'audio_best'): 'failed', (C, 'audio_best'): 'unstarted'})

    def test_duplicate_links_in_the_first_list_run_once(self):
        spellings = [Y1, 'https://youtu.be/AAA?si=x', Y1 + '&list=RDMMAAA&start_radio=1', B, B]
        self.run_queue(spellings, [ok, ok])
        self.assertEqual(self.started(), [Y1, B])
        self.assertEqual([(value['index'], value['total']) for value in self.of('item')], [(1, 2), (2, 2)])
        self.assertEqual(self.logs(), ['重复链接已合并：https://youtu.be/AAA?si=x',
                                       '重复链接已合并：' + Y1 + '&list=RDMMAAA&start_radio=1', '重复链接已合并：' + B])
        self.assertEqual(self.done()['total'], 2)
        # The same link in another format is another download.
        self.events.clear(), self.commands.clear()
        self.run_queue([A, B], [ok, ok], overrides={B: replace(self.options, mode=formats.MODES[3])})
        self.assertEqual(self.started(), [A, B])

    def test_overrides_give_each_link_its_own_command(self):
        video = engine.Options(self.root / 'video', formats.MODES[6], '720p', subtitles=True)
        unsafe = replace(self.options, batch_safe=False, referers={C: 'https://example.org/page'})
        self.run_queue([A, B, C], [ok, ok, ok], overrides={B: video, C: unsafe})
        first, second, third = self.commands
        self.assertEqual(first, engine.build_command(A, self.options))
        self.assertEqual(second, engine.build_command(B, video))
        self.assertEqual(third, engine.build_command(C, unsafe))
        self.assertIn('--embed-subs', second)
        self.assertEqual(second[second.index('-P') + 1], str(self.root / 'video'))
        self.assertEqual(third[third.index('--referer') + 1], 'https://example.org/page')
        self.assertEqual([value['format'] for value in self.of('item')], ['audio_best', 'video_mp4@720p', 'audio_best'])
        self.assertTrue((self.root / 'video').is_dir() and self.out.is_dir(), 'each destination is created for its item')

    def test_runner_state_before_any_download(self):
        runner = engine.Runner(lambda kind, value: None)
        self.assertEqual((runner.items, runner.inbox, runner.opened, runner.closed, runner.blocked, runner.origins),
                         ([], [], False, False, False, {}))
        self.assertIsNone(runner.base_options)
        self.assertIsNot(runner.queue_lock, runner.lock)
        self.assertIsNone(engine.Runner.last_media_end)

    def test_queue_item_fields(self):
        item = engine.QueueItem(3, Y1 + '&list=PLx', replace(self.options, playlist=True), origin='https://youtu.be/AAA')
        self.assertEqual((item.index, item.url, item.origin, item.key, item.format, item.multi, item.state, item.ran,
                          item.error, item.reason, item.title, item.path, item.media_keys, item.files),
                         (3, Y1 + '&list=PLx', 'https://youtu.be/AAA', 'youtube:list:PLx', 'audio_best', True, 'pending',
                          False, '', '', '', '', [], 0))
        self.assertIs(item.preset, formats.by_id('audio_best'))
        plain = engine.QueueItem(1, Y1, self.options)
        self.assertEqual((plain.origin, plain.key, plain.multi, plain.signature()), (Y1, 'youtube:AAA', False, ('youtube:AAA', 'audio_best')))
        self.assertTrue(engine.QueueItem(1, 'https://www.youtube.com/playlist?list=PLx', self.options).multi)


class BotBlockTests(QueueCase):
    def test_r11_first_bot_check_blocks_the_other_youtube_links_of_the_run(self):
        runner = self.run_queue([Y1, Y2, A], [bot, saves('a.opus')])
        self.assertEqual(self.started(), [Y1, A], 'the second YouTube link is not sent to the refused address')
        self.assertEqual([value['index'] for value in self.of('item')], [1, 3], 'no item event for the blocked link')
        first, second, third = self.of('item_done')
        self.assertEqual((first['index'], first['success'], first['bot_check'], first['skipped']), (1, False, True, ''))
        self.assertEqual(first['reason'], 'YouTube 暂时要求验证，稍后重试即可（也可在偏好设置选择 cookies）')
        self.assertEqual(second, {'index': 2, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': True,
                                  'skipped': 'bot_check', 'url': Y2, 'origin': Y2, 'title': '', 'path': '', 'error': '',
                                  'reason': 'YouTube 暂时要求验证，稍后重试即可（也可在偏好设置选择 cookies）',
                                  'stale_engine': False, 'format': 'audio_best'})
        self.assertTrue(third['success'])
        self.assertIn(engine.BOT_CHECK_HINT, self.logs())
        self.assertIn('此链接未完成（退出码 1），继续处理队列中的下一项。', self.logs())
        done = self.done()
        self.assertEqual((done['blocked'], done['bot_check'], done['success'], done['failed'], done['skipped'], done['total']),
                         ('bot_check', True, 1, 1, 0, 3))
        self.assertEqual(done['failed_urls'], [Y1], 'only the link that met the check counts as failed')
        self.assertEqual(done['pending_urls'], [Y1, Y2])
        self.assertEqual(done['unfinished'], 2)
        self.assertEqual(self.stored(), {(Y1, 'audio_best'): 'blocked', (Y2, 'audio_best'): 'blocked'})
        self.assertEqual([(item.state, item.ran) for item in runner.items], [('blocked', True), ('blocked', False), ('done', True)])
        self.assertTrue(runner.blocked)

    def test_every_youtube_host_is_blocked_and_other_sites_are_not(self):
        family = ['https://youtu.be/BBB', 'https://music.youtube.com/watch?v=CCC', 'https://www.youtube.com/shorts/DDD',
                  'https://www.youtube.com/playlist?list=PLx', 'https://www.youtube-nocookie.com/embed/EEE']
        self.run_queue([Y1] + family + [A, 'https://www.bilibili.com/video/BV1xx'], [bot, ok, ok])
        self.assertEqual(self.started(), [Y1, A, 'https://www.bilibili.com/video/BV1xx'])
        self.assertEqual([value['skipped'] for value in self.of('item_done')], ['', 'bot_check', 'bot_check', 'bot_check',
                                                                                'bot_check', 'bot_check', '', ''])

    def test_a_bot_check_on_another_site_blocks_nothing(self):
        runner = self.run_queue([A, Y1, B], [bot, ok, ok])
        self.assertEqual(self.started(), [A, Y1, B])
        self.assertEqual((self.done()['blocked'], self.done()['bot_check'], self.done()['failed']), ('', True, 1))
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'failed'})
        self.assertFalse(runner.blocked)
        self.assertEqual([value['bot_check'] for value in self.of('item_done')], [True, False, False])

    def test_the_stale_cookies_hint_when_a_cookies_file_was_used(self):
        cookies = self.root / 'cookies.txt'
        cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        self.run_queue([Y1], [bot], options=replace(self.options, cookies=str(cookies)))
        self.assertIn(engine.BOT_CHECK_STALE_HINT, self.logs())
        self.assertNotIn(engine.BOT_CHECK_HINT, self.logs())

    def test_a_new_run_is_not_blocked(self):
        runner = self.run_queue([Y1, Y2], [bot])
        self.assertTrue(runner.blocked)
        self.events.clear(), self.commands.clear()
        self.run_queue([Y2], [ok], runner=engine.Runner(lambda kind, value: None))
        self.assertEqual(self.started(), [Y2])
        self.assertEqual(self.done()['blocked'], '')
        self.assertEqual(self.stored(), {(Y1, 'audio_best'): 'blocked'}, 'the link that went through left the list')


class EnqueueTests(QueueCase):
    def test_enqueue_while_the_first_item_runs(self):
        accepted = []

        def first(runner, command, index, total, files):
            accepted.append(runner.enqueue([B, C]))
            return 0, False, None

        runner = self.run_queue([A], [first, ok, ok])
        self.assertEqual(accepted, [[B, C]])
        self.assertEqual(self.started(), [A, B, C])
        self.assertEqual(self.of('queued'), [{'urls': [B, C], 'first_index': 2, 'total': 3}])
        kinds = [kind for kind, _ in self.events]
        self.assertEqual(kinds, ['item', 'item_done', 'queued', 'item', 'item_done', 'item', 'item_done', 'done'])
        self.assertEqual([(value['index'], value['total']) for value in self.of('item')], [(1, 1), (2, 3), (3, 3)])
        self.assertEqual((self.done()['success'], self.done()['total']), (3, 3))
        self.assertEqual(runner.enqueue([D]), [], 'after done the queue is closed')
        self.assertTrue(runner.closed)

    def test_enqueue_before_the_run_is_open_and_after_it_ended(self):
        runner = engine.Runner(lambda kind, value: None)
        self.assertEqual(runner.enqueue([A], self.options), [], 'download() has not built its items yet')
        self.assertEqual(runner.inbox, [])
        self.run_queue([A], [ok], runner=runner)
        self.assertEqual(runner.enqueue([B]), [])
        self.assertEqual(runner.enqueue([B], self.options), [])
        self.assertEqual([item.url for item in runner.items], [A])

    def test_enqueue_of_a_link_that_is_waiting_running_or_done_is_refused(self):
        answers = {}

        def second(runner, command, index, total, files):
            answers['done'] = runner.enqueue([A])                            # finished in this run
            answers['active'] = runner.enqueue([B, 'https://example.invalid/b'])   # running right now
            answers['pending'] = runner.enqueue([C])                         # still waiting
            answers['spelling'] = runner.enqueue(['https://youtu.be/AAA?si=x'])    # another spelling of a waiting link
            answers['new'] = runner.enqueue([D, D, E])                       # new, once each
            answers['inbox'] = runner.enqueue([D])                           # accepted a moment ago
            answers['other format'] = runner.enqueue([A], replace(self.options, mode=formats.MODES[3]))
            return 0, False, None

        self.run_queue([A, B, C, Y1], [ok, second, ok, ok, ok, ok, ok])
        self.assertEqual(answers, {'done': [], 'active': [], 'pending': [], 'spelling': [], 'new': [D, E], 'inbox': [],
                                   'other format': [A]})
        self.assertEqual(self.started(), [A, B, C, Y1, D, E, A])
        queued = self.of('queued')
        self.assertEqual(len(queued), 1)
        self.assertEqual((queued[0]['urls'], queued[0]['first_index'], queued[0]['total']), ([D, E, A], 5, 7))
        self.assertEqual(set(queued[0]['overrides']), {A})
        self.assertEqual(queued[0]['overrides'][A]['mode'], formats.MODES[3])

    def test_a_link_that_failed_can_be_queued_again_and_gets_a_new_index(self):
        answers = []

        def second(runner, command, index, total, files):
            answers.append(runner.enqueue([A]))
            return 0, False, None

        runner = self.run_queue([A, B], [fail, second, saves('a.opus')])
        self.assertEqual(answers, [[A]])
        self.assertEqual(self.started(), [A, B, A])
        self.assertEqual([value['index'] for value in self.of('item')], [1, 2, 3])
        self.assertEqual([(item.url, item.state) for item in runner.items], [(A, 'failed'), (B, 'done'), (A, 'done')])
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['total']), (2, 1, 3))
        self.assertEqual(done['pending_urls'], [], 'the link finished in the end')
        self.assertIsNone(done['failed_path'])
        self.assertEqual(unfinished.load(), [])

    def test_blocked_stopped_and_skipped_links_can_be_queued_again(self):
        library = FakeLibrary({(urltools.link_key(A), 'audio_best'): {'exists': True, 'title': '甲', 'path': 'D:\\甲.opus'}})
        answers = []

        def last(runner, command, index, total, files):
            answers.append(runner.enqueue([A, Y2], replace(self.options, skip_downloaded=False)))
            return 0, False, None

        self.run_queue([A, Y1, Y2, B], [bot, last, saves('a.opus')],
                       options=replace(self.options, skip_downloaded=True), library=library)
        self.assertEqual(answers, [[A, Y2]], 'skipped as downloaded and blocked: both may be queued again')
        self.assertEqual(self.started(), [Y1, B, A], 'the re-queued YouTube link is still blocked in this run')
        self.assertEqual([value['skipped'] for value in self.of('item_done')], ['downloaded', '', 'bot_check', '', '', 'bot_check'])

    def test_overrides_of_queued_links_are_reported_only_where_they_differ(self):
        mp3 = replace(self.options, mode=formats.MODES[3])
        with_referer = replace(self.options, referers={D: 'https://example.org/page'})

        def first(runner, command, index, total, files):
            runner.enqueue([B])                         # the options of the run
            runner.enqueue([C], mp3)                    # another format
            runner.enqueue([D, E], self.options, {D: with_referer})   # one link with its own options
            return 0, False, None

        self.run_queue([A], [first, ok, ok, ok, ok], options=replace(self.options, proxy='http://user:pw@127.0.0.1:1'))
        queued = self.of('queued')[0]
        self.assertEqual(queued['urls'], [B, C, D, E])
        # B runs with the base options (session values aside); E was given the same options as the base.
        self.assertEqual(set(queued['overrides']), {C, D})
        self.assertEqual(queued['overrides'][C], mp3.to_dict())
        self.assertEqual(queued['overrides'][D]['referers'], {D: 'https://example.org/page'})
        self.assertNotIn('pw@', json.dumps(queued), 'no proxy in an event that is written to the history')
        self.assertEqual(self.commands[2], engine.build_command(C, mp3))
        self.assertEqual(self.commands[3], engine.build_command(D, with_referer))
        # Nothing differs: the key is absent.
        self.events.clear(), self.commands.clear()

        def again(runner, command, index, total, files):
            runner.enqueue([B])
            return 0, False, None

        self.run_queue([A], [again, ok])
        self.assertEqual(self.of('queued'), [{'urls': [B], 'first_index': 2, 'total': 2}])

    def test_stop_keeps_queued_links_as_unstarted(self):
        def first(runner, command, index, total, files):
            runner.enqueue([C, D])
            runner.cancel()
            self.assertEqual(runner.enqueue([E]), [], 'after Stop nothing is accepted')
            return 1, False, None

        runner = self.run_queue([A, B], [first])
        self.assertEqual([kind for kind, _ in self.events], ['item', 'item_done', 'queued', 'done'])
        self.assertEqual(self.of('queued'), [{'urls': [C, D], 'first_index': 3, 'total': 4}])
        done = self.done()
        self.assertEqual((done['cancelled'], done['success'], done['failed'], done['total']), (True, 0, 0, 4))
        self.assertEqual(done['pending_urls'], [A, B, C, D])
        self.assertEqual(done['failed_urls'], [])
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'stopped', (B, 'audio_best'): 'unstarted',
                                         (C, 'audio_best'): 'unstarted', (D, 'audio_best'): 'unstarted'})
        self.assertEqual([item.state for item in runner.items], ['stopped', 'unstarted', 'unstarted', 'unstarted'])
        self.assertEqual(runner.enqueue([E]), [])

    def test_engine_error_keeps_queued_links_as_unstarted(self):
        def first(runner, command, index, total, files):
            runner.enqueue([C])
            runner._note_line('yt-dlp.exe: error: no such option: --embed-everything')
            return 2, False, None

        runner = self.run_queue([A, B], [first])
        self.assertEqual([kind for kind, _ in self.events], ['item', 'log', 'item_done', 'queued', 'done'])
        self.assertEqual(self.logs(), ['当前 yt-dlp 引擎与本程序不兼容，请在“偏好设置”恢复内置引擎。'])
        item_done = self.of('item_done')[0]
        self.assertEqual((item_done['success'], item_done['cancelled'], item_done['reason']), (False, False, '下载引擎无法运行'))
        done = self.done()
        self.assertEqual((done['engine_error'], done['success'], done['failed'], done['total'], done['cancelled']),
                         (True, 0, 0, 3, False))
        self.assertEqual(done['pending_urls'], [A, B, C])
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'unstarted', (B, 'audio_best'): 'unstarted',
                                         (C, 'audio_best'): 'unstarted'})
        self.assertEqual(runner.enqueue([D]), [])
        self.assertEqual(self.started(), [A], 'the queue stops: every other link would fail the same way')

    def test_an_engine_that_cannot_start_stops_the_queue(self):
        def missing(runner, command, index, total, files):
            raise FileNotFoundError(2, 'The system cannot find the file specified')

        self.run_queue([A, B], [missing])
        self.assertEqual(len(self.logs()), 1)
        self.assertTrue(self.logs()[0].startswith('无法启动下载引擎：'))
        self.assertTrue(self.logs()[0].endswith('可在“偏好设置”恢复内置引擎。'))
        self.assertEqual((self.done()['engine_error'], self.done()['failed'], self.done()['pending_urls']), (True, 0, [A, B]))
        self.assertEqual(self.of('item_done')[0]['reason'], '下载引擎无法运行')
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'unstarted', (B, 'audio_best'): 'unstarted'})

    def test_an_exception_keeps_queued_links_as_unstarted(self):
        def first(runner, command, index, total, files):
            runner.enqueue([C])
            raise RuntimeError('boom')

        with self.assertLogs('YtDlpDesktop.engine', 'ERROR'):
            runner = self.run_queue([A, B], [first])
        self.assertEqual([kind for kind, _ in self.events], ['item', 'log', 'queued', 'done'])
        self.assertEqual(self.of('queued'), [{'urls': [C], 'first_index': 3, 'total': 3}])
        done = self.done()
        self.assertEqual((done['failed'], done['failed_urls'], done['pending_urls'], done['total']), (1, [A], [A, B, C], 3))
        self.assertEqual(self.stored(), {(A, 'audio_best'): 'failed', (B, 'audio_best'): 'unstarted',
                                         (C, 'audio_best'): 'unstarted'})
        self.assertEqual(runner.enqueue([D]), [])

    def test_enqueue_from_another_thread_never_waits_for_a_process_start(self):
        """enqueue takes queue_lock only; Runner.lock is held while a process starts."""
        runner = engine.Runner(lambda kind, value: None)
        answers = []

        def first(runner_, command, index, total, files):
            with runner_.lock:      # what _run_once holds while the engine starts
                thread = threading.Thread(target=lambda: answers.append(runner_.enqueue([B])))
                thread.start()
                thread.join(5)
                answers.append(thread.is_alive())
            return 0, False, None

        self.run_queue([A], [first, ok], runner=runner)
        self.assertEqual(answers, [[B], False])
        self.assertEqual(self.started(), [A, B])


class CountersTests(QueueCase):
    def test_counters_of_a_mixed_run(self):
        """done, failed, skipped as downloaded, bot-check failure, blocked without running."""
        library = FakeLibrary({(urltools.link_key(C), 'audio_best'): {'exists': True, 'title': '丙', 'path': 'D:\\丙.opus'}})
        self.run_queue([A, B, C, Y1, Y2], [saves('a.opus'), fail, bot],
                       options=replace(self.options, skip_downloaded=True), library=library)
        done = self.done()
        self.assertEqual((done['success'], done['failed'], done['skipped'], done['total']), (1, 2, 1, 5))
        self.assertEqual(done['failed_urls'], [B, Y1])
        self.assertEqual(done['pending_urls'], [B, Y1, Y2])
        self.assertEqual((done['blocked'], done['bot_check'], done['unfinished'], done['cancelled']), ('bot_check', True, 3, False))
        self.assertEqual(self.stored(), {(B, 'audio_best'): 'failed', (Y1, 'audio_best'): 'blocked', (Y2, 'audio_best'): 'blocked'})
        self.assertEqual([value['skipped'] for value in self.of('item_done')], ['', '', 'downloaded', '', 'bot_check'])
        self.assertEqual([value['index'] for value in self.of('item')], [1, 2, 4])


class PacingTests(QueueCase):
    def setUp(self):
        super().setUp()
        self.pace((10, 20))
        self.clock = [5000.0]
        for target, replacement in (('time.monotonic', lambda: self.clock[0]), ('random.uniform', lambda low, high: low)):
            patcher = mock.patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def takes(self, seconds, result=0):
        def step(runner, command, index, total, files):
            self.clock[0] += seconds
            return result, False, None
        return step

    def test_cold_first_item_does_not_wait_and_the_second_waits_for_the_rest_of_the_interval(self):
        self.run_queue([A, B, C], [self.takes(3), self.takes(3), self.takes(3)],
                       hook=lambda runner, kind, value: kind == 'item' and self.clock.__setitem__(0, self.clock[0] + 4))
        # 4 s pass between the end of a download and the next start: 10 - 4 = 6 s are left to wait.
        self.assertEqual(self.waits, [(6.0, 2, 3, '下载间隔', 'interval'), (6.0, 3, 3, '下载间隔', 'interval')])
        self.assertEqual(self.done()['success'], 3)
        kinds = [kind for kind, _ in self.events]
        self.assertEqual(kinds, ['item', 'item_done'] * 3 + ['done'], 'the wait comes after the item event of its link')

    def test_no_wait_when_the_interval_has_already_passed(self):
        self.run_queue([A, B], [self.takes(1), self.takes(1)],
                       hook=lambda runner, kind, value: kind == 'item' and self.clock.__setitem__(0, self.clock[0] + 10))
        self.assertEqual(self.waits, [])

    def test_only_a_successful_download_starts_the_interval(self):
        self.run_queue([A, B, C], [self.takes(3, result=1), self.takes(3), self.takes(3)])
        self.assertEqual(self.waits, [(10.0, 3, 3, '下载间隔', 'interval')], 'no wait after the failed first link')

    def test_playlist_items_and_unsafe_mode_do_not_wait(self):
        self.run_queue([A, B], [ok, ok], options=replace(self.options, playlist=True))
        self.assertEqual(self.waits, [])
        self.assertIsNotNone(engine.Runner.last_media_end)
        self.run_queue([C, D], [ok, ok], options=replace(self.options, batch_safe=False))
        self.assertEqual(self.waits, [])
        # Per item: the plain link after a playlist link waits, the playlist link itself does not.
        self.run_queue([A, B], [ok, ok], overrides={A: replace(self.options, playlist=True)})
        self.assertEqual(self.waits, [(10.0, 2, 2, '下载间隔', 'interval')])

    def test_a_second_runner_started_right_after_waits_too(self):
        self.run_queue([A], [self.takes(2)])
        self.assertEqual(self.waits, [])
        self.clock[0] += 3
        self.run_queue([B], [ok], runner=engine.Runner(lambda kind, value: None))
        self.assertEqual(self.waits, [(7.0, 1, 1, '下载间隔', 'interval')], 'spacing is measured across jobs')
        engine.Runner.reset_pacing()
        self.waits.clear()
        self.run_queue([C], [ok], runner=engine.Runner(lambda kind, value: None))
        self.assertEqual(self.waits, [], 'reset_pacing forgets the last download')

    def test_the_wait_kind_is_reset_after_the_interval(self):
        seen = []

        def limited_once(runner, command, index, total, files):
            seen.append(runner.wait_kind)
            return (1, True, None) if len(seen) == 1 else (0, False, None)

        self.run_queue([A, B], [ok, limited_once, limited_once])
        self.assertEqual([wait[4] for wait in self.waits], ['interval', 'rate_limit'])
        self.assertEqual(seen, ['rate_limit', 'rate_limit'])

    def test_stop_during_the_interval_ends_the_item_as_stopped(self):
        def stop(runner):
            runner.cancel()
            return False

        runner = self.run_queue([A, B, C], [ok], wait=stop)
        self.assertEqual(self.started(), [A], 'the engine is not started for the link whose wait was stopped')
        self.assertEqual([kind for kind, _ in self.events], ['item', 'item_done', 'item', 'item_done', 'done'])
        stopped = self.of('item_done')[1]
        self.assertEqual((stopped['index'], stopped['cancelled'], stopped['success'], stopped['files']), (2, True, False, 0))
        self.assertNotIn('bot_check', stopped, 'a stopped item_done has the keys of 1.5.1')
        self.assertEqual([item.state for item in runner.items], ['done', 'stopped', 'unstarted'])
        self.assertEqual(self.stored(), {(B, 'audio_best'): 'stopped', (C, 'audio_best'): 'unstarted'})
        self.assertEqual((self.done()['cancelled'], self.done()['pending_urls']), (True, [B, C]))
        self.assertEqual(runner.wait_kind, 'rate_limit')


class RealWaitTests(QueueCase):
    def test_the_real_wait_emits_interval_events(self):
        self.pace((0.25, 0.25))
        events = []
        runner = engine.Runner(lambda kind, value: events.append((kind, value)))
        with mock.patch.object(runner, '_run_once', return_value=(0, False, None)):
            runner.download([A, B], self.options)
        waiting = [value for kind, value in events if kind == 'waiting']
        self.assertTrue(waiting)
        self.assertEqual({value['kind'] for value in waiting}, {'interval'})
        self.assertEqual({(value['index'], value['total'], value['reason']) for value in waiting}, {(2, 2, '下载间隔')})
        kinds = [kind for kind, _ in events]
        self.assertLess(kinds.index('waiting'), kinds.index('resuming'))
        self.assertLess(kinds.index('resuming'), len(kinds) - 2)
        self.assertEqual(events[-1][1]['success'], 2)
        self.assertEqual(runner.wait_kind, 'rate_limit')

    def test_stop_during_the_real_interval_wait_is_prompt(self):
        self.pace((300, 300))
        events, waiting = [], threading.Event()

        def emit(kind, value):
            events.append((kind, value))
            if kind == 'waiting':
                waiting.set()

        runner = engine.Runner(emit)
        with mock.patch.object(runner, '_run_once', return_value=(0, False, None)) as run:
            thread = threading.Thread(target=runner.download, args=([A, B], self.options))
            thread.start()
            try:
                self.assertTrue(waiting.wait(5), 'the spacing wait was not shown')
                started = time.monotonic()
                runner.cancel()
                thread.join(3)
                self.assertFalse(thread.is_alive(), 'Stop did not end the spacing wait')
                self.assertLess(time.monotonic() - started, 2)
            finally:
                runner.cancel()
                thread.join(10)
        self.assertEqual(run.call_count, 1)
        done = events[-1][1]
        self.assertEqual((done['cancelled'], done['success'], done['pending_urls']), (True, 1, [B]))
        self.assertEqual([entry['reason'] for entry in unfinished.load()], ['stopped'])


class SkipDownloadedTests(QueueCase):
    def rows(self, **found):
        return {(urltools.link_key(url), 'audio_best'): row for url, row in found.items()}

    def test_a_link_whose_file_is_there_is_skipped(self):
        library = FakeLibrary({(urltools.link_key(A), 'audio_best'): {'exists': True, 'title': '歌', 'path': 'D:\\歌.opus'},
                               (urltools.link_key(B), 'audio_best'): {'exists': False, 'title': '曲', 'path': 'D:\\曲.opus'}})
        self.run_queue([A, B, C], [ok, ok], options=replace(self.options, skip_downloaded=True), library=library)
        self.assertEqual(self.started(), [B, C], 'a missing file and an unknown link are downloaded')
        self.assertEqual(library.calls, [([urltools.link_key(A)], 'audio_best'), ([urltools.link_key(B)], 'audio_best'),
                                         ([urltools.link_key(C)], 'audio_best')])
        skipped = self.of('item_done')[0]
        self.assertEqual(skipped, {'index': 1, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False,
                                   'skipped': 'downloaded', 'url': A, 'origin': A, 'title': '歌', 'path': 'D:\\歌.opus',
                                   'error': '', 'reason': '', 'stale_engine': False, 'format': 'audio_best'})
        self.assertEqual([value['index'] for value in self.of('item')], [2, 3], 'no item event for the skipped link')
        done = self.done()
        self.assertEqual((done['success'], done['skipped'], done['failed'], done['total'], done['pending_urls']), (2, 1, 0, 3, []))
        self.assertIsNone(done['failed_path'])

    def test_links_that_stand_for_several_files_are_never_looked_up(self):
        library = FakeLibrary()
        listed = replace(self.options, skip_downloaded=True, playlist=True)
        plain = replace(self.options, skip_downloaded=True)
        links = [A, 'https://www.youtube.com/playlist?list=PLx', 'https://www.youtube.com/results?search_query=x',
                 'https://www.youtube.com/@name', B]
        self.run_queue(links, [ok] * 5, options=plain, overrides={A: listed}, library=library)
        self.assertEqual(library.calls, [([urltools.link_key(B)], 'audio_best')])
        self.assertEqual([value['multi'] for value in self.of('item')], [True, True, True, True, False])

    def test_off_by_default_without_a_library_and_for_archived_items(self):
        library = FakeLibrary({(urltools.link_key(A), 'audio_best'): {'exists': True, 'title': '', 'path': ''}})
        self.run_queue([A], [ok], library=library)
        self.assertEqual(library.calls, [], 'the option is off unless the caller turns it on')
        self.run_queue([A], [ok], options=replace(self.options, skip_downloaded=True))
        self.assertEqual(self.started(), [A, A], 'no library, no skip')

    def test_a_history_that_cannot_be_read_does_not_stop_the_download(self):
        import sqlite3
        library = FakeLibrary(error=sqlite3.OperationalError('database is locked'))
        self.run_queue([A], [ok], options=replace(self.options, skip_downloaded=True), library=library)
        self.assertEqual(self.started(), [A])
        self.assertEqual(self.logs(), ['下载记录无法读取，照常下载：database is locked'])
        self.assertEqual(self.done()['success'], 1)

    def test_a_skipped_download_leaves_the_unfinished_list(self):
        unfinished.save([unfinished.entry(A, engine._entry_options(self.options), 'failed')])
        library = FakeLibrary({(urltools.link_key(A), 'audio_best'): {'exists': True, 'title': '歌', 'path': 'D:\\歌.opus'}})
        self.run_queue([A], [], options=replace(self.options, skip_downloaded=True), library=library)
        self.assertEqual(unfinished.load(), [])
        self.assertFalse(paths.failed_file().exists())


class SubtitleTests(QueueCase):
    def test_a_subtitle_failure_reruns_the_link_once_without_subtitles(self):
        def subtitles_refused(runner, command, index, total, files):
            runner._note_line("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
            return 1, False, None

        video = engine.Options(self.out, formats.MODES[4], subtitles=True)
        self.run_queue([A, B], [subtitles_refused, saves('a.mkv'), ok], options=video)
        self.assertEqual(self.started(), [A, A, B])
        self.assertIn('--embed-subs', self.commands[0])
        for flag in ('--embed-subs', '--sub-langs', '--sleep-subtitles'):
            self.assertNotIn(flag, self.commands[1])
        self.assertEqual(self.commands[1], engine.build_command(A, replace(video, subtitles=False)))
        self.assertIn('--embed-subs', self.commands[2], 'the next link tries subtitles again')
        self.assertEqual(self.waits, [], 'no cooldown of the queue')
        self.assertEqual(self.logs(), ['字幕下载失败，已改为不带字幕重新下载此条。'])
        first = self.of('item_done')[0]
        self.assertEqual((first['success'], first['files'], first['error'], first['reason']), (True, 1, '', ''))
        self.assertEqual((self.done()['success'], self.done()['failed']), (2, 0))

    def test_the_rerun_happens_once_and_only_for_a_subtitle_failure(self):
        def subtitles_refused(runner, command, index, total, files):
            runner._note_line("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
            return 1, False, None

        video = engine.Options(self.out, formats.MODES[6], subtitles=True)
        self.run_queue([A], [subtitles_refused, subtitles_refused], options=video)
        self.assertEqual(len(self.commands), 2)
        self.assertEqual((self.done()['success'], self.done()['failed']), (0, 1))
        self.events.clear(), self.commands.clear()
        self.run_queue([A], [fail], options=video)
        self.assertEqual(len(self.commands), 1, 'an ordinary failure is not repeated')
        self.events.clear(), self.commands.clear()
        self.run_queue([A], [subtitles_refused], options=replace(video, subtitles=False))
        self.assertEqual(len(self.commands), 1, 'without the option there is nothing to drop')

    def test_the_real_loop_does_not_take_a_subtitle_429_for_a_rate_limit(self):
        lines = ["WARNING: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests",
                 "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"]
        script = ('import sys\nsys.stderr.reconfigure(encoding="utf-8")\n'
                  + ''.join(f'print({line!r}, file=sys.stderr, flush=True)\n' for line in lines) + 'sys.exit(1)\n')
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        result, limited, retry_after = runner._run_once([sys.executable, '-c', script], 1, 1, [])
        self.assertEqual((result, limited, retry_after), (1, False, None))
        self.assertTrue(runner.subs_failed)
        self.assertEqual(self.logs(), lines)


class NamingTests(QueueCase):
    """The two hook lines of _run_once: a finished file is renamed and every event carries the final path."""

    def child(self, media, extra=''):
        """A stand-in engine: writes the files the command's -P folder would get and prints what yt-dlp prints."""
        return ('import json, os, sys\n'
                'sys.stdout.reconfigure(encoding="utf-8")\n'
                'folder = sys.argv[1]\n'
                'os.makedirs(folder, exist_ok=True)\n'
                f'for item in {media!r}:\n'
                '    path = os.path.join(folder, item["name"])\n'
                '    open(path, "wb").write(b"media")\n'
                '    print("__GUI_TITLE__" + json.dumps(item["title"]), flush=True)\n'
                '    data = dict(id=item["id"], extractor_key=item.get("extractor", "Youtube"), title=item["title"], uploader="某人",\n'
                '                duration=3, webpage_url="https://www.youtube.com/watch?v=" + item["id"], filepath=path)\n'
                '    print("__GUI_MEDIA__" + json.dumps(data), flush=True)\n'
                '    print("__GUI_FILE__" + json.dumps(path), flush=True)\n'
                + extra)

    def run_real(self, links, media, options=None, overrides=None, library=None, extra=''):
        """download() with the real _run_once; build_command gives the stand-in engine."""
        real = engine.build_command

        def command(url, item_options):
            built = real(url, item_options)        # still validates, and shows what yt-dlp would get
            self.commands.append(built)
            return [sys.executable, '-c', self.child(media, extra), built[built.index('-P') + 1], '--', url]

        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)), library=library)
        with mock.patch.object(engine, 'build_command', command):
            if overrides is None:
                runner.download(links, options or self.options)
            else:
                runner.download(links, options or self.options, overrides)
        return runner

    def names(self, folder=None):
        return sorted(path.name for path in (folder or self.out).iterdir())

    def test_media_and_file_events_carry_the_renamed_path(self):
        media = [{'name': '徐秉龙 - 双子 【Lyrics MV】 [abc].opus', 'id': 'abc', 'title': '徐秉龙 - 双子 【Lyrics MV】'}]
        runner = self.run_real([Y1], media)
        final = str(self.out / '徐秉龙 - 双子 【Lyrics MV】.opus')
        self.assertEqual(self.names(), ['徐秉龙 - 双子 【Lyrics MV】.opus'])
        self.assertEqual(self.of('title'), ['徐秉龙 - 双子 【Lyrics MV】'])
        self.assertEqual(self.of('media'), [{'id': 'abc', 'extractor_key': 'Youtube', 'title': '徐秉龙 - 双子 【Lyrics MV】',
                                             'uploader': '某人', 'duration': 3,
                                             'webpage_url': 'https://www.youtube.com/watch?v=abc', 'filepath': final,
                                             'index': 1}])
        self.assertEqual(self.of('file'), [final])
        kinds = [kind for kind, _ in self.events]
        self.assertLess(kinds.index('media'), kinds.index('file'), 'media comes before file')
        item_done = self.of('item_done')[0]
        self.assertEqual((item_done['success'], item_done['files'], item_done['title'], item_done['path']),
                         (True, 1, '徐秉龙 - 双子 【Lyrics MV】', final))
        self.assertEqual(self.done()['files'], [final])
        self.assertEqual(runner.items[0].media_keys, ['youtube:abc'])

    def test_clean_style_and_collision(self):
        media = [{'name': '徐秉龙 - 双子 【Lyrics MV】 [abc].opus', 'id': 'abc', 'title': '徐秉龙 - 双子 【Lyrics MV】'}]
        clean = replace(self.options, naming='clean')
        self.run_real([Y1], media, options=clean)
        self.run_real([Y1], media, options=clean)
        self.assertEqual(self.names(), ['徐秉龙 - 双子 (2).opus', '徐秉龙 - 双子.opus'])
        self.assertEqual(self.of('file'), [str(self.out / '徐秉龙 - 双子.opus'), str(self.out / '徐秉龙 - 双子 (2).opus')])
        # A video preset with the clean style keeps the full title.
        video = [{'name': 'C# Tutorial - Part 1 [Beginner] [v1].mkv', 'id': 'v1', 'title': 'C# Tutorial - Part 1 [Beginner]'}]
        self.run_real([Y2], video, options=engine.Options(self.out, formats.MODES[4], naming='clean'))
        self.assertIn('C# Tutorial - Part 1 [Beginner].mkv', self.names())

    def test_title_id_playlist_and_archive_keep_the_work_name(self):
        media = [{'name': '歌 [abc].opus', 'id': 'abc', 'title': '歌'}]
        self.run_real([Y1], media, options=replace(self.options, naming='title_id'))
        self.assertEqual(self.names(), ['歌 [abc].opus'])
        self.assertEqual(self.of('file'), [str(self.out / '歌 [abc].opus')])
        self.assertEqual(self.of('media')[0]['filepath'], str(self.out / '歌 [abc].opus'))
        self.assertFalse(any(word.startswith('temp:') for word in self.commands[-1]))

    def test_a_playlist_item_is_not_renamed_and_has_no_temp_path(self):
        media = [{'name': '一 [p1].opus', 'id': 'p1', 'title': '一'}, {'name': '二 [p2].opus', 'id': 'p2', 'title': '二'}]
        listed = replace(self.options, playlist=True)
        runner = self.run_real(['https://www.youtube.com/playlist?list=PLx'], media, options=listed)
        self.assertEqual(self.names(), ['一 [p1].opus', '二 [p2].opus'])
        self.assertFalse(any(word.startswith('temp:') for word in self.commands[-1]))
        self.assertEqual([value['index'] for value in self.of('media')], [1, 1])
        self.assertEqual(self.of('file'), [str(self.out / '一 [p1].opus'), str(self.out / '二 [p2].opus')])
        self.assertEqual(runner.items[0].media_keys, ['youtube:p1', 'youtube:p2'])
        self.assertEqual(self.of('item_done')[0]['files'], 2)
        # The same link with the option off is renamed and gets the temp path (same drive).
        self.events.clear()
        self.run_real([Y1], media[:1], options=engine.Options(self.root / 'single'))
        self.assertEqual(self.names(self.root / 'single'), ['一.opus'])
        self.assertIn('temp:' + str(paths.work_dir()), self.commands[-1])
        self.assertTrue((paths.work_dir() / paths.OWNED).is_file(), 'the work folder is created as the app\'s own')

    def test_an_archived_item_keeps_its_name_and_only_it_is_archived(self):
        from library import Library
        store = Library(self.root / 'history.sqlite3')
        media = [{'name': '歌 [abc].opus', 'id': 'abc', 'title': '歌'}]
        archive_root = self.root / '资料库'
        archived = replace(self.options, archive_root=str(archive_root))
        extra = ('print("__GUI_ARCHIVE__" + json.dumps(dict(id="abc", title="歌", uploader="某人", description="", '
                 'webpage_url="https://www.youtube.com/watch?v=abc", upload_date="20260101", filepath=path)), flush=True)\n')
        runner = self.run_real([A, B], media, overrides={A: archived}, library=store, extra=extra)
        self.assertEqual(self.done()['success'], 2)
        self.assertEqual(self.done()['archive_errors'], [])
        entries = store.archives()
        self.assertEqual(len(entries), 1, 'only the link with an archive root is archived')
        folder = Path(entries[0]['folder'])
        self.assertTrue(folder.is_relative_to(archive_root.resolve()))
        self.assertEqual(entries[0]['url'], A)
        self.assertEqual(entries[0]['files'], [str(Path('媒体') / '歌 [abc].opus')])
        self.assertTrue((folder / '媒体' / '歌 [abc].opus').is_file(), 'the archived file keeps the engine\'s name')
        self.assertEqual(self.names(), ['歌.opus'], 'the plain link is renamed as usual')
        self.assertEqual(list(runner._archives), [str(archive_root)])
        self.assertIsNone(runner.archive, 'the last item had no archive')
        self.assertFalse(any(word.startswith('temp:') for word in self.commands[0]))
        self.assertTrue(any(word.startswith('temp:') for word in self.commands[1]))

    def test_each_archive_root_gets_its_own_archive_and_a_bad_output_fails_only_the_plain_link(self):
        from library import Library
        store = Library(self.root / 'history.sqlite3')
        blocker = self.root / 'a-file'
        blocker.write_text('x', encoding='utf-8')
        base = engine.Options(blocker / 'below-a-file')       # cannot be created; an archived item never needs it
        first, second = self.root / '库一', self.root / '库二'
        overrides = {A: replace(base, archive_root=str(first)), C: replace(base, archive_root=str(second))}
        runner = self.run_queue([A, B, C], [ok, ok], options=base, overrides=overrides, library=store)
        self.assertEqual(self.started(), [A, C])
        self.assertEqual([value['success'] for value in self.of('item_done')], [True, False, True])
        self.assertEqual(sorted(runner._archives), sorted([str(first), str(second)]))
        self.assertEqual(len(store.archives()), 2)
        self.assertEqual({Path(row['folder']).parents[1] for row in store.archives()}, {first.resolve(), second.resolve()})
        for command, root in zip(self.commands, (first, second)):
            self.assertTrue(Path(command[command.index('-P') + 1]).is_relative_to(root.resolve()))
        self.assertEqual(self.stored(), {(B, 'audio_best'): 'failed'})

    def test_a_link_with_its_own_options_is_not_archived_because_the_job_is(self):
        from library import Library
        store = Library(self.root / 'history.sqlite3')
        archived = replace(self.options, archive_root=str(self.root / '库'))
        plain = engine.Options(self.root / 'plain')
        runner = self.run_queue([A, B], [ok, saves('b.opus')], options=archived, overrides={B: plain}, library=store)
        self.assertEqual([row['url'] for row in store.archives()], [A])
        first, second = self.commands
        self.assertTrue(any(word.startswith('after_move:__GUI_ARCHIVE__') for word in first))
        self.assertFalse(any(word.startswith('after_move:__GUI_ARCHIVE__') for word in second))
        self.assertEqual(second[second.index('-P') + 1], str(self.root / 'plain'))
        self.assertEqual(second, engine.build_command(B, plain))
        self.assertEqual(list(runner._archives), [str(self.root / '库')])
        self.assertIsNone(runner.archive)
        self.assertEqual(self.done()['files'], [str(self.root / 'plain' / 'b.opus')])

    def test_an_older_file_with_the_work_name_is_left_alone(self):
        """A download of 1.5.1 with the same name is in the output folder: the engine cannot move
        the new file out of the work folder and reports the old one (measured with the bundled
        engine).  The old file keeps its name and content; the new file is fetched and renamed."""
        self.out.mkdir(parents=True)
        old = self.out / '歌 [abc].opus'
        old.write_bytes(b'downloaded by 1.5.1')
        script = '\n'.join([
            'import json, os, sys',
            'sys.stdout.reconfigure(encoding="utf-8")',
            'out, work = sys.argv[1], sys.argv[2]',
            'open(os.path.join(work, "歌 [abc].opus"), "wb").write(b"new download")',
            'existing = os.path.join(out, "歌 [abc].opus")',
            'print("WARNING: Cannot move file out of temporary directory since it already exists. ", flush=True)',
            'print("__GUI_MEDIA__" + json.dumps(dict(id="abc", extractor_key="Youtube", title="歌", filepath=existing)), flush=True)',
            'print("__GUI_FILE__" + json.dumps(existing), flush=True)',
        ])
        real = engine.build_command

        def command(url, item_options):
            built = real(url, item_options)
            self.commands.append(built)
            return [sys.executable, '-c', script, str(item_options.output), str(paths.work_dir()), '--', url]

        for expected in ('歌.opus', '歌 (2).opus'):
            runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
            with mock.patch.object(engine, 'build_command', command):
                runner.download([Y1], self.options)
            self.assertTrue(any(word.startswith('temp:') for word in self.commands[-1]))
            final = str(self.out / expected)
            self.assertEqual(self.of('file')[-1], final)
            self.assertEqual(self.of('media')[-1]['filepath'], final)
            self.assertEqual(Path(final).read_bytes(), b'new download')
            self.assertEqual(old.read_bytes(), b'downloaded by 1.5.1', 'the older file is untouched')
            self.assertEqual([path.name for path in paths.work_dir().iterdir()], [paths.OWNED], 'nothing is left in the work folder')
            self.assertEqual(self.done()['files'], [final])
        self.assertEqual(self.names(), ['歌 (2).opus', '歌 [abc].opus', '歌.opus'])

    def test_a_generic_extractor_gives_no_media_key_and_odd_lines_pass_through(self):
        media = [{'name': 'index [index].mp4', 'id': 'index', 'title': 'index', 'extractor': 'Generic'}]
        extra = ('print("__GUI_MEDIA__" + json.dumps(dict(id="", filepath=path)), flush=True)\n'
                 'print("__GUI_MEDIA__" + json.dumps(dict(id="x")), flush=True)\n'
                 'print("__GUI_FILE__17", flush=True)\n'
                 'print("__GUI_FILE__" + json.dumps(os.path.join(folder, "never-announced.bin")), flush=True)\n')
        runner = self.run_real([A], media, extra=extra)
        self.assertEqual(runner.items[0].media_keys, [])
        self.assertEqual(self.names(), ['index.mp4'])
        self.assertEqual(self.of('media')[1:], [{'id': '', 'filepath': str(self.out / 'index [index].mp4')}, {'id': 'x'}],
                         'media lines without an id or a path are handed on as they are')
        self.assertEqual(self.of('file'), [str(self.out / 'index.mp4'), 17, str(self.out / 'never-announced.bin')])
        self.assertEqual(self.done()['success'], 1)

    def test_intercept_never_raises(self):
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        self.assertEqual(runner._intercept('log', 'text'), ('log', 'text'))
        self.assertEqual(runner._intercept('file', None), ('file', None))
        self.assertEqual(runner._intercept('file', 17), ('file', 17))
        self.assertEqual(runner._intercept('title', '题'), ('title', '题'))
        self.assertEqual(runner._intercept('archive', {'filepath': 'x'}), ('archive', {'filepath': 'x'}))
        # Without a running item the media line is handed on with its path unchanged.
        self.assertEqual(runner._intercept('media', {'id': 'a', 'filepath': 'D:\\x [a].opus'}),
                         ('media', {'id': 'a', 'filepath': 'D:\\x [a].opus', 'index': None}))
        self.assertEqual(runner._intercept('file', 'D:\\x [a].opus'), ('file', 'D:\\x [a].opus'))
        # Something breaks inside: the line passes through, one record in the log file, nothing emitted.
        runner._current = object()
        with self.assertLogs('YtDlpDesktop.engine', 'ERROR') as logs:
            self.assertEqual(runner._intercept('media', {'id': 'a', 'filepath': 'D:\\x [a].opus'}),
                             ('media', {'id': 'a', 'filepath': 'D:\\x [a].opus'}))
            self.assertEqual(runner._intercept('title', '题'), ('title', '题'))
        self.assertEqual(len(logs.records), 1, 'logged once')
        self.assertEqual(self.events, [])

    def test_the_title_event_names_an_item_that_fails_later(self):
        extra = 'print("ERROR: [youtube] abc: Requested format is not available", file=sys.stderr, flush=True)\nsys.exit(1)\n'
        real = engine.build_command

        def command(url, item_options):
            built = real(url, item_options)
            script = ('import json, sys\nsys.stdout.reconfigure(encoding="utf-8")\n'
                      'print("__GUI_TITLE__" + json.dumps("只有标题的失败"), flush=True)\n' + extra)
            return [sys.executable, '-c', script, '--', url]

        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with mock.patch.object(engine, 'build_command', command):
            runner.download([Y1], self.options)
        item_done = self.of('item_done')[0]
        self.assertEqual((item_done['success'], item_done['title'], item_done['error'], item_done['reason']),
                         (False, '只有标题的失败', '[youtube] abc: Requested format is not available', '没有可用的所选格式或画质'))
        self.assertEqual(unfinished.load()[0]['title'], '只有标题的失败')

    def old_file_engine(self, existing_name, work_copy=None):
        """A stand-in engine that, like yt-dlp, reports a file of an earlier download that is
        already in the output folder; with ``work_copy`` it also leaves a new copy with these
        bytes in the work folder (what yt-dlp does when it cannot move its result)."""
        lines = ['import json, os, sys', 'sys.stdout.reconfigure(encoding="utf-8")',
                 'out, work = sys.argv[1], sys.argv[2]', f'existing = os.path.join(out, {existing_name!r})']
        if work_copy is not None:
            lines.append(f'open(os.path.join(work, {existing_name!r}), "wb").write({work_copy!r})')
        else:
            lines.append('print("[download] " + existing + " has already been downloaded", flush=True)')
        lines += ['print("__GUI_MEDIA__" + json.dumps(dict(id="abc", extractor_key="Youtube", title="歌", filepath=existing)), flush=True)',
                  'print("__GUI_FILE__" + json.dumps(existing), flush=True)']
        real = engine.build_command

        def command(url, item_options):
            built = real(url, item_options)
            self.commands.append(built)
            paths.own_dir(paths.work_dir())
            return [sys.executable, '-c', '\n'.join(lines), str(item_options.output), str(paths.work_dir()), '--', url]
        return command

    def test_a_file_that_was_there_before_the_item_is_never_renamed(self):
        """Fix round 1 (conformance-2, bughunt-7): the engine reports an earlier download that has
        the work name; it keeps its name and the item is done with that path."""
        self.out.mkdir(parents=True)
        old = self.out / '歌 [abc].opus'
        old.write_bytes(b'downloaded earlier')
        for style in ('title', 'clean'):
            self.events.clear()
            runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
            with mock.patch.object(engine, 'build_command', self.old_file_engine(old.name)):
                runner.download([Y1], replace(self.options, naming=style))
            self.assertEqual(self.names(), ['歌 [abc].opus'], style)
            self.assertEqual(old.read_bytes(), b'downloaded earlier')
            self.assertEqual(self.of('file'), [str(old)])
            self.assertEqual(self.of('media')[0]['filepath'], str(old))
            item_done = self.of('item_done')[0]
            self.assertEqual((item_done['success'], item_done['path'], item_done['files']), (True, str(old), 1))
            self.assertIn('保存位置已有这个文件（之前下载的），保留原文件名：歌 [abc].opus', self.logs())
            self.assertEqual(self.done()['files'], [str(old)])
        # A file that this item itself produced is renamed as usual.
        self.events.clear()
        media = [{'name': '新歌 [new].opus', 'id': 'new', 'title': '新歌'}]
        self.run_real([Y2], media)
        self.assertIn('新歌.opus', self.names())

    def test_an_identical_copy_in_the_work_folder_is_discarded_and_a_different_one_kept_beside(self):
        self.out.mkdir(parents=True)
        old = self.out / '歌 [abc].opus'
        old.write_bytes(b'same bytes')
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with mock.patch.object(engine, 'build_command', self.old_file_engine(old.name, b'same bytes')):
            runner.download([Y1], self.options)
        self.assertEqual(self.names(), ['歌 [abc].opus'])
        self.assertEqual([path.name for path in paths.work_dir().iterdir()], [paths.OWNED], 'the copy is gone')
        self.assertEqual((self.of('file'), self.of('item_done')[0]['path']), ([str(old)], str(old)))
        self.events.clear()
        runner = engine.Runner(lambda kind, value: self.events.append((kind, value)))
        with mock.patch.object(engine, 'build_command', self.old_file_engine(old.name, b'other bytes')):
            runner.download([Y1], self.options)
        self.assertEqual(self.names(), ['歌 [abc].opus', '歌.opus'])
        self.assertEqual((old.read_bytes(), (self.out / '歌.opus').read_bytes()), (b'same bytes', b'other bytes'))
        self.assertEqual(self.of('file'), [str(self.out / '歌.opus')])


class FixRoundOneQueueTests(QueueCase):
    """WP2 fix round 1: the queue-wide cooldown (conformance-1), hash-routed links (bughunt-1),
    done always last (bughunt-5), OS error reasons (bughunt-6), the work folder beside the
    program (bughunt-8) and the resolution in the format key (bughunt-9)."""

    def test_a_rate_limited_last_link_pauses_the_queue_for_a_link_that_was_enqueued_meanwhile(self):
        def limited_and_enqueue(runner, command, index, total, files):
            self.assertEqual(runner.enqueue([B]), [B])
            return 1, True, None

        def limited(runner, command, index, total, files):
            return 1, True, None

        self.run_queue([A], [limited_and_enqueue, limited, limited, ok])
        self.assertEqual(self.started(), [A, A, A, B])
        self.assertEqual([(wait[0], wait[3]) for wait in self.waits],
                         [(300, '网站限流，冷却后重试此链接（1/2）'), (600, '网站限流，冷却后重试此链接（2/2）'),
                          (900, '网站限流，暂停整个下载队列')])
        kinds = [(kind, value.get('index') if isinstance(value, dict) else None) for kind, value in self.events]
        self.assertLess(kinds.index(('item_done', 1)), kinds.index(('item', 2)))
        # Nothing waiting: no pause of the queue.
        self.events.clear(), self.commands.clear(), self.waits.clear()
        self.run_queue([A], [limited, limited, limited])
        self.assertEqual([wait[3] for wait in self.waits], ['网站限流，冷却后重试此链接（1/2）', '网站限流，冷却后重试此链接（2/2）'])

    def test_hash_routed_links_are_two_downloads(self):
        song1, song2 = 'https://music.163.com/#/song?id=186016', 'https://music.163.com/#/song?id=347230'

        def first(runner, command, index, total, files):
            self.assertEqual(runner.enqueue([song2, song1]), [song2], 'the other song is taken, the running one is not')
            return 0, False, None

        self.run_queue([song1], [first, ok])
        self.assertEqual(self.started(), [song1, song2])
        self.events.clear(), self.commands.clear()
        self.run_queue([song1, song2, 'https://music.163.com/#/song?id=186016'], [ok, ok])
        self.assertEqual(self.started(), [song1, song2])
        self.assertEqual(self.logs(), ['重复链接已合并：https://music.163.com/#/song?id=186016'])
        library = FakeLibrary({(urltools.link_key(song1), 'audio_best'): {'exists': True, 'title': '一', 'path': 'D:\\一.m4a'}})
        self.events.clear(), self.commands.clear()
        self.run_queue([song1, song2], [ok], options=replace(self.options, skip_downloaded=True), library=library)
        self.assertEqual(self.started(), [song2], 'the second song is not reported as already downloaded')

    def test_two_resolutions_of_one_video_are_two_downloads(self):
        hd = engine.Options(self.out, formats.MODES[6], '720p')
        full = replace(hd, resolution='1080p')
        short = 'https://youtu.be/AAA'

        def first(runner, command, index, total, files):
            self.assertEqual(runner.enqueue([Y1]), [], '720p of this video is running')
            self.assertEqual(runner.enqueue([Y1], full), [Y1], 'the 1080p file is another download')
            return 0, False, None

        self.run_queue([short], [first, ok], options=hd)
        self.assertEqual(self.started(), [short, Y1])
        self.assertEqual([value['format'] for value in self.of('item')], ['video_mp4@720p', 'video_mp4@1080p'])
        self.events.clear(), self.commands.clear()
        self.run_queue([short, Y1], [ok, ok], options=hd, overrides={Y1: full})
        self.assertEqual(self.started(), [short, Y1])
        # skip_downloaded: the 720p file does not stand for the 1080p one; the best quality is the plain id.
        library = FakeLibrary({('youtube:AAA', 'video_mp4@720p'): {'exists': True, 'title': '视频', 'path': 'D:\\视频.mp4'}})
        self.events.clear(), self.commands.clear()
        self.run_queue([short, Y1, Y2], [ok, ok], options=replace(hd, skip_downloaded=True),
                       overrides={Y1: replace(full, skip_downloaded=True),
                                  Y2: engine.Options(self.out, formats.MODES[6], skip_downloaded=True)}, library=library)
        self.assertEqual(self.started(), [Y1, Y2])
        self.assertEqual([call[1] for call in library.calls], ['video_mp4@720p', 'video_mp4@1080p', 'video_mp4'])
        # The unfinished list keeps the two apart as well.
        self.events.clear(), self.commands.clear()
        self.run_queue([short, Y1], [fail, fail], options=hd, overrides={Y1: full})
        self.assertEqual(sorted(entry['format'] for entry in unfinished.load()), ['video_mp4@1080p', 'video_mp4@720p'])

    def test_done_is_the_last_event_whatever_breaks_while_the_list_is_saved(self):
        with mock.patch.object(engine.unfinished, 'merge', side_effect=ValueError('broken list')), \
                self.assertLogs('YtDlpDesktop.engine', 'ERROR'):
            self.run_queue([A, B], [fail, ok])
        self.assertIn('失败清单未能保存：broken list', self.logs())
        self.assertEqual((self.done()['unfinished'], self.done()['failed_path'], self.done()['failed']), (0, None, 1))

    def test_a_title_with_a_lone_surrogate_is_stored(self):
        def broken_title(runner, command, index, total, files):
            runner._intercept('title', 'bad \ud83d title')
            return 1, False, None

        self.run_queue([A], [broken_title])
        self.assertEqual(self.of('item_done')[0]['title'], 'bad ? title')
        self.assertEqual(unfinished.load()[0]['title'], 'bad ? title')
        self.assertEqual(self.done()['unfinished'], 1)

    def test_a_link_that_cannot_start_keeps_the_cause_of_an_os_error(self):
        blocker = self.root / 'a-file'
        blocker.write_text('x', encoding='utf-8')
        self.run_queue([A], [], options=engine.Options(blocker / 'below'))
        item_done = self.of('item_done')[0]
        self.assertTrue(item_done['reason'].startswith('[WinError'), item_done['reason'])
        self.assertEqual(item_done['reason'], item_done['error'][:70])

    def test_the_work_folder_beside_the_program_is_used_and_cleaned_once(self):
        app = self.root / 'app'
        work = app / 'data' / 'work'
        work.mkdir(parents=True)
        (work / paths.OWNED).touch()
        # final-architecture-1: a resumable partial stays paths.WORK_MAX_AGE (7 days), a cover a day.
        for name, hours in (('old [x].mp4.part', 8 * 24), ('old [x].jpg', 30), ('young [y].mp4.part', 1), ('kept [z].mp3', 30),
                            ('resumable [w].mp4.part', 30)):
            (work / name).write_bytes(b'x')
            stamp = time.time() - hours * 3600
            os.utime(work / name, (stamp, stamp))
        letter = 'Q:' if self.root.drive.lower() != 'q:' else 'R:'
        with mock.patch.object(paths, 'work_dir', return_value=Path(letter + '\\state\\work')), \
                mock.patch.object(paths, 'APP_DIR', app), \
                mock.patch.object(engine, 'clean_app_work', wraps=engine.clean_app_work) as cleaning:
            self.run_queue([A, B], [ok, ok])
        for command in self.commands:
            self.assertIn('temp:' + str(work), command)
        self.assertEqual(cleaning.call_count, 1, 'cleaned once per run')
        self.assertEqual(sorted(path.name for path in work.iterdir()),
                         [paths.OWNED, 'kept [z].mp3', 'resumable [w].mp4.part', 'young [y].mp4.part'])
        # Created on demand, with the marker.
        fresh = self.root / 'fresh'
        with mock.patch.object(paths, 'work_dir', return_value=Path(letter + '\\state\\work')), \
                mock.patch.object(paths, 'APP_DIR', fresh):
            self.run_queue([C], [ok])
        self.assertTrue((fresh / 'data' / 'work' / paths.OWNED).is_file())


if __name__ == '__main__':
    unittest.main()
