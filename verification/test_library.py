# tier: all
"""Real worker restart/resume, incremental checks, and real yt-dlp archive integration."""
import _sandbox
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
import engine
import urls
from collector import CollectOptions, CollectionRunner
from library import Archive, Library, identity, safe_name

OUT = _sandbox.out('library-test')
OUT.mkdir(parents=True)
_sandbox.set_state_dir(OUT / 'state')
db = Library(OUT / 'data/library.sqlite3')
hits = []
fail_second = False
extra = False


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        hits.append(self.path)
        if self.path == '/two' and fail_second:
            self.send_error(503)
            return
        if self.path == '/fixture.mp4':
            data = (ROOT / 'verification/fixture/fixture.mp4').read_bytes()
            content_type = 'video/mp4'
        else:
            if self.path == '/two':
                time.sleep(2)
            data = ('<title>中文资料测试</title><main><h1>完整正文</h1><p>用于验证归档和续采。</p>'
                    '<a href="/fixture.mp4">测试媒体</a>' + ('<a href="/new.mp4">新增媒体</a>' if extra else '')
                    + '</main>' + ('<a rel="next" href="/two">下一页</a>' if self.path == '/one' else '')).encode()
            content_type = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
BASE = f'http://127.0.0.1:{server.server_port}'
threading.Thread(target=server.serve_forever, daemon=True).start()


def run(options, job=None, stop_first=False):
    job = job or db.create_job('collect', '续采测试', asdict(options))
    options.job_id, options.history_path = job, str(db.path)
    events = []
    saved = threading.Event()
    def emit(k, v):
        events.append((k, v))
        if k == 'collect_page':
            saved.set()
    runner = CollectionRunner(emit)
    thread = threading.Thread(target=runner.run, args=(options,))
    thread.start()
    if stop_first:
        assert saved.wait(30), events
        # The worker's database transaction precedes even this notification.
        assert len(Library(db.path).job(job)['pages']) == 1
        runner.cancel()
    thread.join(90)
    assert not thread.is_alive(), events
    return job, events


report = {}
try:
    job, events = run(CollectOptions([BASE + '/one'], max_pages=2), stop_first=True)
    record = Library(db.path).job(job)
    assert record['status'] == 'interrupted' and len(record['pages']) == 1
    checkpoint = record['data']['checkpoint']
    assert checkpoint['todo'][0]['url'] == BASE + '/two'
    first_hits = hits.count('/one')
    db.change_job(job, status='running', errors=[])
    job, events = run(CollectOptions([BASE + '/one'], max_pages=2, resume=checkpoint), job)
    record = db.job(job)
    assert record['status'] == 'completed' and len(record['pages']) == 2, record
    assert hits.count('/one') == first_hits, hits
    report['stop_reopen_resume_without_refetching_completed_page'] = 'passed'

    fail_second = True
    failed, events = run(CollectOptions([BASE + '/one'], max_pages=2))
    record = db.job(failed)
    assert record['status'] == 'partial' and len(record['pages']) == 1 and record['data']['checkpoint']['todo']
    fail_second = False
    db.change_job(failed, status='running', errors=[])
    run(CollectOptions([BASE + '/one'], max_pages=2, resume=record['data']['checkpoint']), failed)
    assert db.job(failed)['status'] == 'completed'
    report['failed_page_retry'] = 'passed'

    options = asdict(CollectOptions([BASE + '/one']))
    options['proxy'] = 'http://secret:password@127.0.0.1:8123'
    sub = db.subscription('更新订阅', options, first_new=False)
    assert 'password' not in json.dumps(db.subscriptions())
    pages = db.job(job)['pages']
    assert db.finish_subscription(sub, pages, False, 'failed') == 0
    assert not db.subscriptions()[0]['data']['initialized']
    assert db.finish_subscription(sub, pages, True) == 0
    assert db.pending(sub) == []
    extra = True
    changed, _ = run(CollectOptions([BASE + '/one']))
    changed_pages = db.job(changed)['pages']
    assert db.finish_subscription(sub, changed_pages, True) == 1
    assert db.finish_subscription(sub, changed_pages, True) == 0
    assert len(Library(db.path).pending(sub)) == 1
    # Merely queuing or failing a download must not consume the new item.
    download_job = db.create_job('download', '下载恢复', dict(urls=[BASE+'/new.mp4'], proxy='secret'))
    db.download_event(download_job, 'item', dict(url=BASE+'/new.mp4'))
    db.download_event(download_job, 'item_done', dict(success=False))
    assert len(db.pending(sub)) == 1
    db.download_event(download_job, 'item_done', dict(success=True))
    assert db.pending(sub) == []
    assert identity('https://youtu.be/abc') == identity('https://www.youtube.com/watch?v=abc&utm_source=test')
    assert identity('https://a.test/v.mp4?token=1') != identity('https://a.test/v.mp4?token=2')
    assert identity('https://www.bilibili.com/video/BV123?p=1') != identity('https://www.bilibili.com/video/BV123?p=2')
    report['subscription_baseline_new_dedup_failure_and_success_semantics'] = 'passed'

    # Simulate a dead owner and reload the catalog, without relying on graceful exit.
    orphan = db.create_job('collect', '崩溃恢复', dict(urls=[BASE+'/one']))
    db.change_job(orphan, owner=0, worker=None)
    assert next(j for j in Library(db.path).history() if j['id'] == orphan)['status'] == 'interrupted'
    report['crash_history_recovery'] = 'passed'

    output = OUT / '原下载目录'
    archive_root = OUT / '统一资料库'
    url = BASE + '/fixture.mp4'
    events = []
    # 1.6.0: the window hands these events to the history before it shows them.
    history_job = db.create_job('download', '1条 · 视频 · 最佳画质 MKV', dict(urls=[url]))
    def emit(k, v):
        if k in ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done'):
            db.download_event(history_job, k, v)
        events.append((k, v))
    runner = engine.Runner(emit)
    runner.library = db
    runner.download([url], engine.Options(output, engine.MODES[4], batch_safe=False,
        archive_root=str(archive_root), archive_contexts={url: dict(title='测试媒体', source=BASE+'/one', job_id=job, page_url=BASE+'/one')}))
    done = next(v for k,v in reversed(events) if k == 'done')
    assert done['success'] == 1 and not done['archive_errors'], events
    assert not list(output.rglob('*.mkv'))
    recorded = db.job(history_job)
    assert recorded['status'] == 'completed' and recorded['data']['completed'] == [url], recorded
    assert recorded['title'] == 'fixture', recorded['title']
    saved = db.find_download([urls.link_key(url)], 'video_mkv')
    assert saved and saved['exists'] and saved['job'] == history_job and saved['path'] == done['files'][0], saved
    assert Path(saved['path']).name == 'fixture [fixture].mkv', saved   # archived files keep the engine's name
    assert db.find_download([urls.link_key(url)], 'audio_best') is None
    report['download_history_of_a_real_run'] = 'passed'
    archived = db.archives()
    assert len(archived) == 1, archived
    entry = archived[0]
    folder = Path(entry['folder'])
    metadata = json.loads((folder/'资料信息.json').read_text(encoding='utf-8'))
    assert metadata['id'] == 'fixture' and metadata['files'] and metadata['source'] == BASE+'/one', metadata
    media = folder / metadata['files'][0]
    assert media.is_file() and media.suffix == '.mkv'
    assert '完整正文' in (folder/'网页正文.txt').read_text(encoding='utf-8-sig')
    assert '来源：' in (folder/'网页正文.md').read_text(encoding='utf-8-sig')
    archive = Archive(archive_root, db)
    assert archive.prepare(url, dict(source=BASE+'/one', title='更新标题')) == folder
    assert json.loads((folder/'资料信息.json').read_text(encoding='utf-8'))['files'] == metadata['files']
    transcript = OUT / '已精校正文.txt'
    transcript.write_text('精校正文不应被修改', encoding='utf-8')
    attached = archive.attach(folder, transcript)
    assert attached.read_bytes() == transcript.read_bytes()
    assert archive.attach(folder, transcript) == attached
    transcript.write_text('另一版正文', encoding='utf-8')
    assert archive.attach(folder, transcript) != attached
    assert attached.read_text(encoding='utf-8') == '精校正文不应被修改'
    try:
        archive.media(folder, dict(filepath=str(transcript)))
        raise AssertionError('accepted an unrelated file')
    except ValueError:
        pass
    assert '/' not in safe_name('../../恶意标题/CON') and safe_name('CON') == '_CON'
    report['real_media_metadata_body_archive_and_non_destructive_transcript_linking'] = 'passed'
    report.update(status='passed', output=str(OUT))
    _sandbox.write_report('library-report.json', report)
    print(json.dumps(report, ensure_ascii=False))
finally:
    server.shutdown()
