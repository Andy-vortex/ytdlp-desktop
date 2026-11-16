"""Golden widget states of StudioApp after a scripted handle()/set_busy() sequence (1.6.0 WP0).

It guards the riskiest move of the upgrade: the split of studio.py and the dispatch
table that replaces the if/elif chain of StudioApp.handle (WP4 steps 1-2).  One
withdrawn smoke app receives every event kind through app.handle(kind, value) and
every busy transition through app.set_busy(); after each step the visible state is
recorded.  Recorded from the 1.5.1 sources.  To record on purpose, run this file
directly (not through run_tests.py) with GOLDEN_UPDATE=1.

Recorded after every step: the texts of current_title, metrics, queue_stats,
update_status, version_label, engine_badge; [text, state] of start_button,
stop_button, retry_button, open_output_button, collect_start, collect_stop; every
row as [title, detail, badge, state]; progress target and mode; busy, animating,
operation; the mode variable; the state of every widget in app.controls and in
app.update_buttons as one letter each (N normal, D disabled, in list order); the
lines the step passed to app.append_log; the state of quality_menu (set_busy leaves
it to on_format, and the menu is not in app.controls); how often the step called
app.refresh_library, and the rows of history_tree as [type, title, status] (the
effect of that call).
Only names of the 1.6.0 spec's test-visible list are read (WP0.md step 5, WP4.md §I,
SPEC §3.14; quality_menu is named in WP4.md §D), so the recorder itself keeps working
when the pages are rebuilt.
Normalisation tokens: <VER> engine version, <OUT> smoke output folder, <STATE> state folder.
The clock (time.monotonic) is frozen, so countdown texts do not depend on timing.
"""
import _sandbox
import unittest
from unittest.mock import patch

import customtkinter as ctk
import engine
from studio import FORMATS, StudioApp

GOLDEN_NAME = 'ui-1.6.0.json'
U1 = 'https://example.invalid/a'
U2 = 'https://media.example.invalid/b'
U3 = 'https://www.youtube.com/watch?v=golden'
TITLE = '第一条测试媒体 · A Media Title That Is Clearly Longer Than Fifty-Six Characters In Total'
TEXT_WIDGETS = ('current_title', 'metrics', 'queue_stats', 'update_status', 'version_label', 'engine_badge')
BUTTONS = ('start_button', 'stop_button', 'retry_button', 'open_output_button', 'collect_start', 'collect_stop')


def letters(widgets):
    result = ''
    for widget in widgets:
        try:
            state = str(widget.cget('state'))
        except Exception:
            state = '?'
        result += {'normal': 'N', 'disabled': 'D'}.get(state, '?')
    return result


def snapshot(app, log, refreshes):
    state = {name: getattr(app, name).cget('text') for name in TEXT_WIDGETS}
    for name in BUTTONS:
        button = getattr(app, name)
        state[name] = [button.cget('text'), button.cget('state')]
    state['rows'] = [[row['title'].cget('text'), row['detail'].cget('text'), row['badge'].cget('text'), row['state']]
                     for row in app.rows]
    state['progress'] = {'target': round(app.progress.target, 3), 'mode': app.progress.cget('mode')}
    state['busy'], state['animating'], state['operation'] = bool(app.busy), bool(app.animating), app.operation
    state['mode'] = app.mode.get()
    state['controls'] = letters(app.controls)
    state['update_buttons'] = letters(app.update_buttons)
    state['log'] = list(log)
    del log[:]
    state['quality_menu'] = app.quality_menu.cget('state')
    state['refresh_library'] = len(refreshes)
    del refreshes[:]
    state['history'] = [list(app.history_tree.item(row, 'values'))[:3] for row in app.history_tree.get_children()]
    return state


def script(app, clock, out, failed_path):
    """The steps as (label, action); every branch of StudioApp.handle and set_busy is reached."""
    options = engine.Options(out)

    def handle(kind, value):
        return lambda: app.handle(kind, value)

    def busy(operation, value=True):
        def action():
            app.operation = operation
            app.set_busy(value)
        return action

    def item(index, total, url):
        return handle('item', {'index': index, 'total': total, 'url': url})

    def item_done(index, success=False, cancelled=False, files=0, bot_check=False):
        return handle('item_done', {'index': index, 'cancelled': cancelled, 'success': success,
                                    'files': files, 'bot_check': bot_check})

    def done(stored=None, **changes):
        payload = {'success': 0, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                   'pending_urls': [], 'failed_path': failed_path, 'archive_errors': [], 'bot_check': False}
        payload.update(changes)

        def action():
            if stored is not None:
                # What the engine leaves in failed-downloads.json before it emits done.
                engine.store_failed_job(stored, options)
            app.handle('done', payload)
        return action

    def progress(fraction, downloaded, total, speed, eta, status):
        return handle('progress', {'fraction': fraction, 'downloaded': downloaded, 'total': total,
                                   'speed': speed, 'eta': eta, 'status': status})

    def later(seconds):
        def action():
            clock[0] += seconds
            app.render_wait()
        return action

    def cookies(value):
        return lambda: app.cookies.set(value)

    def legacy_done():
        # Three links are on disk now, but a done event without failed_urls must not reload the list.
        engine.store_failed_job([U1, U2, U3], options)
        app.handle('done', {'success': 1, 'failed': 0, 'cancelled': False, 'files': []})

    history_job = []

    def create_history_job():
        # start() records the job in the library before the worker runs; the history list is not refreshed yet.
        # (Only one job: the list is ordered by a wall-clock time stamp.)
        history_job.append(app.library.create_job('download', f'1条 · {options.mode}',
                                                  {'urls': [U1], 'output': str(out), 'mode': options.mode}))

    def done_with_history(**changes):
        payload = {'success': 1, 'failed': 0, 'files': [], 'cancelled': False, 'failed_urls': [],
                   'pending_urls': [], 'failed_path': failed_path, 'archive_errors': [], 'bot_check': False}
        payload.update(changes)

        def action():
            engine.store_failed_job([], options)
            # The emit closure of start() hands the event to the library first, then to the UI.
            app.library.download_event(history_job[-1], 'done', payload)
            app.handle('done', payload)
        return action

    a_wav, c_wav = str(out / 'a.wav'), str(out / 'c.wav')
    return [
        ('initial', lambda: None),
        ('populate_queue: three links', lambda: app.populate_queue([U1, U2, U3])),

        # --- operation download, job 1: one failure among three
        ('download job 1: set_busy(True)', busy('download')),
        ('item 1/3', item(1, 3, U1)),
        ('title longer than 56 characters', handle('title', TITLE)),
        ('log: plain engine line', handle('log', '[generic] a: Downloading webpage')),
        ('waiting 300 s', handle('waiting', {'index': 1, 'total': 3, 'seconds': 300, 'reason': '下载间隔 / 重试等待'})),
        ('render_wait 241 s later', later(241)),
        ('resuming', handle('resuming', {'index': 1, 'total': 3})),
        ('progress: 50 % downloading', progress(.5, 1024, 2048, 512, 2, 'downloading')),
        ('progress: fraction None', progress(None, 4096, None, None, None, 'downloading')),
        ('progress: status finished', progress(1.0, 2048, 2048, None, None, 'finished')),
        ('log: [ExtractAudio] line', handle('log', '[ExtractAudio] Destination: a.wav')),
        ('file', handle('file', a_wav)),
        ('item_done: success with one file', item_done(1, success=True, files=1)),
        ('item 2/3', item(2, 3, U2)),
        ('log: [Merger] line', handle('log', '[Merger] Merging formats into "b.mkv"')),
        ('item_done: failed', item_done(2)),
        ('item 3/3', item(3, 3, U3)),
        ('log: [VideoRemuxer] line', handle('log', '[VideoRemuxer] Remuxing video from mp4 to mkv')),
        ('item_done: success with two files', item_done(3, success=True, files=2)),
        ('done: failures with failed_urls and failed_path',
         done(stored=[U2], success=2, failed=1, files=[a_wav, c_wav], failed_urls=[U2], pending_urls=[U2])),
        ('unknown event kind is ignored', handle('golden_unknown_kind', {'anything': 1})),

        # --- job 2: YouTube bot check, without and with a cookies file chosen
        ('populate_queue: two links', lambda: app.populate_queue([U3, U1])),
        ('download job 2: set_busy(True)', busy('download')),
        ('job 2 item 1/2', item(1, 2, U3)),
        ('item_done: failed with bot_check', item_done(1, bot_check=True)),
        ('job 2 item 2/2', item(2, 2, U1)),
        ('job 2 item_done: success', item_done(2, success=True, files=1)),
        ('done: bot_check without cookies',
         done(stored=[U3], success=1, failed=1, files=[a_wav], failed_urls=[U3], pending_urls=[U3], bot_check=True)),
        ('cookies file chosen', cookies('C:\\cookies\\youtube.txt')),
        ('download job 2b: set_busy(True)', busy('download')),
        ('done: bot_check with cookies',
         done(stored=[U3], success=0, failed=1, failed_urls=[U3], pending_urls=[U3], bot_check=True)),
        ('cookies cleared', cookies('')),

        # --- job 3: stopped by the user during a wait
        ('populate_queue: three links again', lambda: app.populate_queue([U1, U2, U3])),
        ('download job 3: set_busy(True)', busy('download')),
        ('job 3 item 1/3', item(1, 3, U1)),
        ('job 3 waiting 900 s (rate limit)',
         handle('waiting', {'index': 1, 'total': 3, 'seconds': 900, 'reason': '网站限流，暂停整个下载队列'})),
        ('item_done: cancelled', item_done(1, cancelled=True)),
        ('done: cancelled', done(stored=[U1, U2, U3], cancelled=True, pending_urls=[U1, U2, U3])),

        # --- job 4: everything succeeds
        ('populate_queue: two links again', lambda: app.populate_queue([U1, U2])),
        ('download job 4: set_busy(True)', busy('download')),
        ('job 4 item 1/2', item(1, 2, U1)),
        ('job 4 item_done 1: success', item_done(1, success=True, files=1)),
        ('job 4 item 2/2', item(2, 2, U2)),
        ('job 4 item_done 2: success', item_done(2, success=True, files=1)),
        ('done: all ok', done(stored=[], success=2, files=[a_wav, c_wav])),

        # --- job 5: media saved, archive bookkeeping failed
        ('populate_queue: one link', lambda: app.populate_queue([U1])),
        ('download job 5: set_busy(True)', busy('download')),
        ('job 5 item 1/1', item(1, 1, U1)),
        ('job 5 item_done: success', item_done(1, success=True, files=1)),
        ('done: archive_errors', done(stored=[], success=1, files=[a_wav], archive_errors=['归档文件与本条资料目录不匹配。'])),

        # --- jobs 6 and 7: done arrives while a row is still active (no item_done)
        ('populate_queue: two links for job 6', lambda: app.populate_queue([U1, U2])),
        ('download job 6: set_busy(True)', busy('download')),
        ('job 6 item 1/2', item(1, 2, U1)),
        ('done: failed while a row is active', done(stored=[U1, U2], failed=1, failed_urls=[U1], pending_urls=[U1, U2])),
        ('populate_queue: one link for job 7', lambda: app.populate_queue([U2])),
        ('download job 7: set_busy(True)', busy('download')),
        ('job 7 item 1/1', item(1, 1, U2)),
        ('done: cancelled while a row is active', done(stored=[U2], cancelled=True, pending_urls=[U2])),
        ('download job 8: set_busy(True)', busy('download')),
        ('done: legacy payload without failed_urls', legacy_done),

        # --- operation update
        ('update 1: set_busy(True)', busy('update')),
        ('update 1: animate(True)', lambda: app.animate(True)),
        ('update: title', handle('title', '正在检查官方更新…')),
        ('update: progress with status updating', progress(.25, 1000, 4000, None, None, 'updating')),
        ('updated: new version', handle('updated', {'version': '2099.01.01', 'unchanged': False})),
        ('update_done', handle('update_done', {'cancelled': False})),
        ('update 2: set_busy(True)', busy('update')),
        ('update 2: animate(True)', lambda: app.animate(True)),
        ('updated: unchanged', handle('updated', {'version': '2099.02.02', 'unchanged': True})),
        ('update 2: update_done', handle('update_done', {'cancelled': False})),
        ('update 3: set_busy(True)', busy('update')),
        ('update_error', handle('update_error', 'Update checksum or size is unavailable')),
        ('update_done: cancelled', handle('update_done', {'cancelled': True})),

        # --- operation collect (handle forwards collect_* kinds to handle_collection)
        ('collect: set_busy(True)', busy('collect')),
        ('collect_page', handle('collect_page', {
            'url': 'https://example.org/list', 'title': '测试文章', 'text': '完整正文', 'markdown': '# 完整正文', 'links': [
                {'url': 'https://example.org/film.mp4', 'title': '视频甲', 'kind': '媒体地址', 'source': 'https://example.org/list'},
                {'url': 'https://example.org/article', 'title': '文章', 'kind': '网页链接', 'source': 'https://example.org/list'}]})),
        ('collect_error', handle('collect_error', '网站返回HTTP 503，可尝试动态模式或稍后重试。')),
        ('collect_done', handle('collect_done', {'cancelled': False})),

        # --- set_busy(True) / set_busy(False) for each operation
        # set_busy() calls on_format(), which copies the format menu's choice into the mode variable.
        ('format variable set to MKV, on_format not called', lambda: app.format.set(FORMATS[4])),
        ('set_busy(True) with operation download', busy('download', True)),
        ('set_busy(False) with operation download', busy('download', False)),
        ('set_busy(True) with operation update', busy('update', True)),
        ('set_busy(False) with operation update', busy('update', False)),
        ('set_busy(True) with operation collect', busy('collect', True)),
        ('set_busy(False) with operation collect', busy('collect', False)),

        # --- fix round 1: the done branch ends with refresh_library(); the history list shows the job
        ('history: a download job is recorded, the list is not refreshed yet', create_history_job),
        ('download job 9: set_busy(True)', busy('download')),
        ('done: the history list shows the finished job', done_with_history(files=[a_wav])),

        # --- WP4: the event kinds added in 1.6.0 (media, queued, update_status, update_progress, engine_check)
        ('wp4 populate_queue: one link', lambda: app.populate_queue([U1])),
        ('wp4 download job: set_busy(True)', busy('download')),
        ('wp4 item 1/1', item(1, 1, U1)),
        ('wp4 media', handle('media', {'index': 1, 'id': 'a', 'extractor_key': 'Generic', 'title': '媒体标题',
                                       'uploader': '', 'duration': 3, 'webpage_url': U1, 'filepath': a_wav})),
        ('wp4 queued: one more link', lambda: (app.append_rows([U2]),
                                               app.handle('queued', {'urls': [U2], 'first_index': 2, 'total': 2}))),
        ('wp4 item_done: skipped as downloaded', handle('item_done', {
            'index': 2, 'cancelled': False, 'success': False, 'files': 0, 'bot_check': False, 'url': U2,
            'title': '已有的歌', 'path': c_wav, 'skipped': 'downloaded', 'reason': '', 'error': ''})),
        ('wp4 item_done: success with path', handle('item_done', {
            'index': 1, 'cancelled': False, 'success': True, 'files': 1, 'bot_check': False, 'url': U1,
            'title': '媒体标题', 'path': a_wav, 'skipped': '', 'reason': '', 'error': ''})),
        ('wp4 done: one skipped', done(stored=[], success=1, files=[a_wav], skipped=1, total=2, unfinished=0)),
        ('wp4 update: set_busy(True)', busy('update')),
        ('wp4 update_status', handle('update_status', '正在下载 yt-dlp 2099.03.03…')),
        ('wp4 update_progress', handle('update_progress', {'fraction': .4, 'downloaded': 4, 'total': 10})),
        ('wp4 update_done', handle('update_done', {'cancelled': False})),
        ('wp4 update_progress after the end is ignored', handle('update_progress', {'fraction': .9, 'downloaded': 9,
                                                                                    'total': 10})),
        ('wp4 engine_check: newer release', handle('engine_check', {'latest': '2099.04.04', 'current': '2026.10.21',
                                                                    'newer': True})),
    ]


def record():
    """Build one smoke app, play the script, destroy the app; returns the normalised steps."""
    out = _sandbox.out('golden-ui')
    state_dir = _sandbox.state_dir()
    failed_path = str(state_dir / 'failed-downloads.json')
    version = engine.engine_version()
    ctk.set_appearance_mode('light')
    app = StudioApp(_sandbox.app_args('http://127.0.0.1/fixture.mp4', str(out)))
    steps = []
    try:
        app.withdraw()
        log = []
        append_log = app.append_log

        def capture(line):
            log.append(str(line))
            append_log(line)

        app.append_log = capture
        refreshes = []
        refresh_library = app.refresh_library

        def count_refresh():
            refreshes.append(True)
            refresh_library()

        app.refresh_library = count_refresh
        clock = [1000.0]
        with patch('time.monotonic', new=lambda: clock[0]):
            for label, action in script(app, clock, out, failed_path):
                action()
                steps.append({'step': label, 'state': snapshot(app, log, refreshes)})
    finally:
        app.destroy()
    replacements = [(version, '<VER>')]
    for path, token in ((out, '<OUT>'), (state_dir, '<STATE>')):
        replacements += [(form, token) for form in _sandbox.path_forms(path)]
    return _sandbox.normalise(steps, replacements)


class GoldenUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _sandbox.assert_isolated()

    def test_scripted_sequence_matches_golden(self):
        steps = record()
        labels = [entry['step'] for entry in steps]
        self.assertEqual(len(labels), len(set(labels)), 'step labels must be unique')
        _sandbox.check_golden(self, GOLDEN_NAME, steps)


if __name__ == '__main__':
    unittest.main()
