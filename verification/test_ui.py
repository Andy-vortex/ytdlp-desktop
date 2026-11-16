import _sandbox
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

# Every output goes to the sandbox (_sandbox.out); nothing is written into verification/.

import customtkinter as ctk
from studio import StudioApp
import engine
from engine import Options, MODES

ctk.set_appearance_mode('light')
app = StudioApp(_sandbox.app_args('http://127.0.0.1/fixture.mp4', str(_sandbox.out('ui-test'))))
app.withdraw()
try:
    app.update_idletasks()
    assert set(app.pages) == {'download', 'collect', 'library', 'logs', 'settings'}
    app.handle('collect_page', {'url': 'https://example.org/list', 'title': '测试文章',
        'text': '完整正文', 'markdown': '# 完整正文', 'links': [
            {'url': 'https://example.org/film.mp4', 'title': '视频甲', 'kind': '媒体地址', 'source': 'https://example.org/list'},
            {'url': 'https://example.org/article', 'title': '文章', 'kind': '网页链接', 'source': 'https://example.org/list'}]})
    assert len(app.collect_tree.get_children()) == 1
    app.collect_filter.set('全部链接')
    assert len(app.collect_tree.get_children()) == 2
    app.collect_search.set('视频甲')
    assert len(app.collect_tree.get_children()) == 1
    app.collect_tree.selection_set(app.collect_tree.get_children())
    app.collection_selection_changed()
    app.add_collected()
    app.add_collected()
    assert app.urls.get('1.0', 'end-1c').count('https://example.org/film.mp4') == 1
    assert app.download_referers['https://example.org/film.mp4'] == 'https://example.org/list'
    with patch('collection_ui.filedialog.asksaveasfilename', return_value=str(_sandbox.out('collection-ui-links.txt'))):
        app.export_collected_links()
    assert _sandbox.out('collection-ui-links.txt').read_text(encoding='utf-8-sig').strip() == 'https://example.org/film.mp4'
    with patch('collection_ui.filedialog.asksaveasfilename', return_value=str(_sandbox.out('collection-ui-text.md'))):
        app.export_collected_text()
    assert '# 完整正文' in _sandbox.out('collection-ui-text.md').read_text(encoding='utf-8-sig')
    app.operation = 'collect'
    app.set_busy(True)
    assert app.collect_start.cget('state') == 'disabled' and app.collect_stop.cget('state') == 'normal'
    app.handle('collect_done', {'cancelled': True})
    assert not app.busy and app.collect_stop.cget('state') == 'disabled'
    app.urls.delete('1.0', 'end')
    app.urls.insert('1.0', 'http://127.0.0.1/fixture.mp4')
    app.set_busy(True)
    assert app.batch_switch.cget('state') == 'disabled'
    assert app.retry_button.cget('state') == 'disabled'
    with patch('studio.os.startfile') as open_folder:
        app.open_output_button.invoke()
        open_folder.assert_called_once_with(_sandbox.out('ui-test'))
    app.set_busy(False)
    app.animate(True)
    animation = app.progress._frame_id
    assert animation is not None
    app.animate(True)
    assert app.progress._frame_id == animation
    app.animate(False)
    assert app.progress.cget('mode') == 'determinate'
    app.populate_queue(['https://example.org/one', 'https://example.org/two'])
    app.operation = 'download'
    app.handle('item', {'index': 1, 'total': 2, 'url': 'https://example.org/one'})
    app.handle('title', '第一条测试媒体')
    app.handle('waiting', {'index': 1, 'total': 2, 'seconds': 300, 'reason': '网站限流，暂停队列'})
    assert '05:00' in app.metrics.cget('text')
    assert app.rows[0]['badge'].cget('text') == '等待中'
    assert not app.animating
    app.wait_deadline = time.monotonic() + 59
    app.render_wait()
    assert '00:59' in app.metrics.cget('text')
    app.handle('resuming', {'index': 1, 'total': 2})
    assert app.wait_context is None and app.animating
    app.handle('progress', {'fraction': .5, 'downloaded': 1024, 'total': 2048, 'speed': 512, 'eta': 2, 'status': 'downloading'})
    assert app.rows[0]['badge'].cget('text') == '50.0%'
    assert app.progress.target == .5
    app.handle('item_done', {'index': 1, 'success': True, 'cancelled': False, 'files': 1})
    app.handle('item', {'index': 2, 'total': 2, 'url': 'https://example.org/two'})
    app.handle('item_done', {'index': 2, 'success': False, 'cancelled': False, 'files': 0})
    app.handle('done', {'success': 1, 'failed': 1, 'cancelled': False, 'files': []})
    assert app.rows[0]['state'] == 'done' and app.rows[1]['state'] == 'failed'
    assert not app.busy
    app.progress.set(.4, immediate=True)
    app.handle('done', {'success': 0, 'failed': 0, 'cancelled': True, 'files': []})
    assert app.progress.target == .4
    # WP4: retry launches the stored entry with its own options; form and link box stay as they are (R13).
    engine.store_failed_job(['https://example.org/two'],
                            Options(_sandbox.out('original-output'), MODES[2], '720p', True, '', True))
    app.handle('done', {'success': 1, 'failed': 1, 'cancelled': False, 'files': [],
                        'failed_urls': ['https://example.org/two'], 'failed_path': 'test-failures.json'})
    assert app.retry_button.cget('state') == 'normal'
    app.urls.delete('1.0', 'end')
    app.urls.insert('1.0', 'https://example.org/typed')
    output_before, mode_before = app.output.get(), app.mode.get()
    with patch.object(app, 'launch') as launch:
        app.retry_button.invoke()
        launch.assert_called_once()
        assert launch.call_args.args[0] == ['https://example.org/two']
        base = launch.call_args.args[1]
        assert base.mode == MODES[2] and base.resolution == '720p' and base.playlist is True
    assert app.urls.get('1.0', 'end-1c') == 'https://example.org/typed'
    assert app.output.get() == output_before and app.mode.get() == mode_before
    engine.save_unfinished([])
    app.set_busy(True)
    assert app.retry_button.cget('state') == 'disabled'
    assert app.cookies_entry.cget('state') == 'disabled'
    app.set_busy(False)
    cookie_file = _sandbox.out('ui-test-cookies.txt')
    cookie_file.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    with patch('studio.filedialog.askopenfilename', return_value=str(cookie_file)):
        app.choose_cookies()
    assert app.cookies.get() == str(cookie_file)
    with patch('studio.filedialog.askopenfilename', return_value=str(_sandbox.out('missing-cookies.txt'))), \
         patch('studio.messagebox.showwarning') as warning:
        app.choose_cookies()
        warning.assert_called_once()
    assert app.cookies.get() == str(cookie_file)
    app.clear_cookies()
    assert app.cookies.get() == ''
    cookie_file.unlink()
    app.populate_queue(['https://www.youtube.com/watch?v=blocked'])
    app.operation = 'download'
    app.handle('item', {'index': 1, 'total': 1, 'url': 'https://www.youtube.com/watch?v=blocked'})
    app.handle('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': True})
    assert 'cookies' in app.rows[0]['detail'].cget('text')
    app.handle('done', {'success': 0, 'failed': 1, 'cancelled': False, 'files': [], 'bot_check': True})
    assert '偏好设置' in app.metrics.cget('text')
    app.show_page('settings')
    app.show_page('logs')
    app.show_page('download')
    result = {'status': 'passed', 'pages': list(app.pages), 'queue_updates': 'passed',
              'collection_filter_selection_dedup_referer_exports_and_stop_controls': 'passed',
              'progress': 'passed', 'mixed_success_and_failure': 'passed',
              'open_folder_while_downloading': 'passed', 'animation_mode_switch': 'passed',
              'cancel_does_not_fill_bar': 'passed',
              'waiting_countdown_and_resuming': 'passed', 'retry_restores_failed_urls_and_options': 'passed',
              'safe_mode_controls_disabled_during_download': 'passed',
              'cookies_choose_validate_clear_and_bot_check_hint': 'passed',
              'requested_width': app.winfo_reqwidth(), 'requested_height': app.winfo_reqheight()}
    _sandbox.write_report('ui-report.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
finally:
    app.destroy()
