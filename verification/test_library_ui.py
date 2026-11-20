import _sandbox
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid

# Every output goes to the sandbox (_sandbox.out); nothing is written into verification/.

from collector import CollectOptions
from engine import Options
from library import job_downloads
from studio import StudioApp
import ui_theme
import customtkinter as ctk

OUT = _sandbox.out('library-ui')
ctk.set_appearance_mode('light')
app = StudioApp(_sandbox.app_args('https://example.org/video.mp4', str(OUT)))
app.withdraw()
try:
    url = 'https://example.org/list'
    app.collect_urls.insert('1.0', url)
    app.collect_pages_limit.set('2')
    options = app.collection_options()
    job = app.library.create_job('collect', '历史列表', asdict(options))
    page = dict(url=url, title='历史完整正文', text='归档正文', markdown='# 归档正文', links=[dict(url='https://example.org/video.mp4', title='视频甲', source=url, kind='media')], next=None)
    checkpoint = dict(visited=[url], todo=[dict(url=url+'/next', depth=1)])
    app.library.collection_event(job, 'collect_page', dict(page, _checkpoint=checkpoint))
    app.library.change_job(job, status='interrupted')
    app.refresh_library()
    app.history_tree.selection_set(job)
    app.view_history()
    assert app.collected_pages[0]['text'] == '归档正文'
    assert app.collect_urls.get('1.0','end-1c') == url
    assert app.collect_pages_limit.get() == '2'
    with patch.object(app, 'launch_collection') as launch:
        app.resume_history()
        launch.assert_called_once()
        assert launch.call_args.args[0].resume == checkpoint
    app.subscription_interval.set(0)        # 0 s: manual only (the default of a new subscription, settings page)
    app.save_subscription()
    subscription = app.library.subscriptions()[0]
    assert subscription['interval'] == 0 and subscription['data']['options']['max_pages'] == 2
    assert subscription['name'] == url, 'named after its first page'
    app.library.finish_subscription(subscription['id'], [page], True)
    app.refresh_library()
    app.subscription_tree.selection_set(subscription['id'])
    app.subscription_selection()
    app.pending_tree.selection_set(app.pending_tree.get_children())
    app.queue_subscription()
    assert app.urls.get('1.0','end-1c').count('https://example.org/video.mp4') == 1
    assert len(app.library.pending(subscription['id'])) == 1
    app.set_schedule(86400)                 # ⋯ → Schedule ▸ daily
    assert app.library.subscriptions()[0]['interval'] == 86400
    app.toggle_subscription()
    assert not app.library.subscriptions()[0]['enabled']
    app.toggle_subscription()
    app.library.configure_subscription(subscription['id'], True, 3600)
    with app.library.db() as db:
        db.execute('UPDATE subscriptions SET next_check=0')
    app.smoke_mode = False
    app.busy = True
    with patch.object(app, 'check_subscription') as check:
        app.subscription_tick()
        check.assert_not_called()
        app.busy = False
        app.subscription_tick()
        check.assert_called_once()
        assert check.call_args.kwargs['automatic']
    app.smoke_mode = True
    app.archive_pages()
    row = app.library.archives()[0]
    assert (Path(row['folder'])/'网页正文.txt').is_file()
    app.archive_tree.selection_set('0')
    transcript = OUT/'精校稿.txt'
    transcript.write_text('正文', encoding='utf-8')
    with patch('library_ui.filedialog.askopenfilename', return_value=str(transcript)):
        app.attach_transcript()
    assert len(app.library.archives()[0]['transcripts']) == 1
    assert transcript.read_text(encoding='utf-8') == '正文'
    app.archive_search.set('不存在的标题')
    assert not app.archive_tree.get_children()
    app.archive_search.set('历史完整')
    assert len(app.archive_tree.get_children()) == 1
    app.archive_enabled.set(True)
    assert app.output_button.path == app.archive_root.get()
    assert app.output_button._icon_code == ui_theme.ICON_ARCHIVE
    with patch('studio.os.startfile') as opened:
        app.open_folder()
        opened.assert_called_once_with(Path(app.archive_root.get()))
    app.archive_enabled.set(False)
    assert app.output_button.path == app.output.get()
    assert app.output_button._icon_code == ui_theme.ICON_FOLDER
    raw = asdict(Options(OUT))
    raw.update(output=str(OUT), urls=['https://example.org/one', 'https://example.org/two'])
    download = app.library.create_job('download', '恢复下载', raw)
    # (2.0.0 S2 P2) a finished download is a (link, format) pair in 'done'
    first = job_downloads(app.library.job(download)['data'])[0]
    app.library.change_job(download, status='interrupted', done=[[first['url'], first['format']]])
    app.refresh_library()
    app.history_tree.selection_set(download)
    box = app.urls.get('1.0', 'end-1c')
    with patch.object(app, 'launch') as launch:
        app.resume_history()
        launch.assert_called_once()
        assert launch.call_args.args[0] == ['https://example.org/two']
        assert app.urls.get('1.0', 'end-1c') == box
    for tab in ('history', 'subscriptions', 'archive'):        # library_ui.TAB_*
        app.show_page('library')
        app.library_tabs.set(tab)
        app.update_idletasks()
    report = dict(status='passed', history_resume='passed', persistent_subscription_queue='passed',
        scheduled_due_and_busy_serialization='passed', archive_and_transcript='passed', download_remaining_only='passed')
    _sandbox.write_report('library-ui-report.json', report)
    print(json.dumps(report))
finally:
    app.destroy()
