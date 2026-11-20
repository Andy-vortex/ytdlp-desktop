# tier: all
"""Real Scrapling/static/browser/queue integration against a deterministic local site."""
import _sandbox
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
import engine
import unfinished
from collector import CollectOptions, CollectionRunner, parse_page, web_url
from scrapling import Selector

OUT = _sandbox.out('collection-test')
OUT.mkdir(parents=True, exist_ok=True)
_sandbox.set_state_dir(OUT / 'isolated-state')


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/slow':
            time.sleep(15)
        if self.path.split('?')[0] in ('/guarded.mp4', '/fixture.mp4'):
            if self.path.startswith('/guarded') and self.headers.get('Referer') != BASE+'/dynamic':
                self.send_error(403)
                return
            data = (ROOT / 'verification/fixture/fixture.mp4').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
        else:
            pages = {
                '/list': '<title>采集测试</title><nav>导航垃圾</nav><article><h1>第一篇</h1><p>正文甲乙丙。</p><a href="/guarded.mp4">媒体文件</a><a href="/guarded.mp4#same">重复</a><a href="https://www.youtube.com/watch?v=localtest">YouTube 视频</a><a href="javascript:alert(1)">不安全链接</a><script>正文不应出现脚本</script></article><a rel="next" href="/page2">下一页</a>',
                '/page2': '<title>第二页</title><main><p>正文第二页</p><a href="/fixture.mp4">另一个视频</a></main><a rel="next" href="/list">回环</a>',
                '/dynamic': '<title>动态采集</title><main id="main"><h1>动态测试</h1></main><script>setTimeout(()=>{document.querySelector("main").innerHTML+=\'<p>动态正文已加载</p><a href="/fixture.mp4">延迟视频</a>\';fetch("/guarded.mp4");},150)</script>',
                '/empty': '<title>空页</title><body>没有链接</body>',
                '/external': '<a rel="next" href="https://example.invalid/page">不要离站</a>',
                '/slow': '<title>慢页</title><p>等待停止</p>',
            }
            if self.path not in pages:
                self.send_error(404)
                return
            data = pages[self.path].encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
BASE = f'http://127.0.0.1:{server.server_port}'
threading.Thread(target=server.serve_forever, daemon=True).start()


def run(options):
    events = []
    runner = CollectionRunner(lambda k, v: events.append((k, v)))
    runner.run(options)
    errors = [v for k, v in events if k == 'collect_error']
    assert not errors, errors
    assert events[-1][0] == 'collect_done'
    return [v for k, v in events if k == 'collect_page']


report = {}
try:
    assert web_url('javascript:bad', BASE) is None
    assert web_url('//example.org/a#x', BASE) == 'http://example.org/a'
    pages = run(CollectOptions([BASE+'/list'], max_pages=5))
    assert len(pages) == 2, pages
    assert len([i for i in pages[0]['links'] if i['url'] == BASE+'/guarded.mp4']) == 1
    assert all(not i['url'].startswith('javascript:') for p in pages for i in p['links'])
    assert '正文甲乙丙' in pages[0]['text'] and '脚本' not in pages[0]['text'] and '导航垃圾' not in pages[0]['text']
    assert '# 第一篇' in pages[0]['markdown']
    assert len(run(CollectOptions([BASE+'/external'], max_pages=5))) == 1
    report['static_real_fetch_pagination_loop_dedup_and_text'] = 'passed'
    scope = run(CollectOptions([BASE+'/list'], selector='article'))
    assert not any(i['url'].endswith('/page2') for i in scope[0]['links'])
    report['CSS_scope'] = 'passed'
    empty = run(CollectOptions([BASE+'/empty']))
    assert not empty[0]['links']
    direct = run(CollectOptions([BASE+'/fixture.mp4']))
    assert direct[0]['links'][0]['kind'] == 'media'
    report['empty_and_direct_media'] = 'passed'
    dynamic = run(CollectOptions([BASE+'/dynamic'], dynamic=True, scrolls=1))
    assert '动态正文已加载' in dynamic[0]['text'], dynamic
    urls = {i['url'] for i in dynamic[0]['links']}
    assert BASE+'/guarded.mp4' in urls and BASE+'/fixture.mp4' in urls, dynamic
    report['real_Chrome_JS_and_network_media_capture'] = 'passed'
    events = []
    options = engine.Options(OUT/'download', engine.MODES[4], batch_safe=False,
                             referers={BASE+'/guarded.mp4': BASE+'/dynamic'})
    engine.Runner(lambda k, v: events.append((k, v))).download([BASE+'/guarded.mp4'], options)
    done = next(v for k, v in reversed(events) if k == 'done')
    assert done['success'] == 1 and done['files'], events
    # (2.0.0 S2 P4) engine.load_failed_job (a 1.5.1 wrapper) is gone: the entries' own referers
    stored = unfinished.load()
    assert not stored or {item['url']: item['referer'] for item in stored if item['referer']} == options.referers, stored
    report['collected_media_to_ytdlp_with_required_Referer'] = 'passed'
    for dynamic_mode in (False, True):
        events = []
        runner = CollectionRunner(lambda k, v: events.append((k, v)))
        thread = threading.Thread(target=runner.run, args=(CollectOptions([BASE+'/slow'], dynamic=dynamic_mode),))
        thread.start()
        time.sleep(2)
        started = time.monotonic()
        runner.cancel()
        thread.join(8)
        assert not thread.is_alive() and runner.process is None
        assert events[-1] == ('collect_done', {'cancelled': True}), events
        report['stop_browser' if dynamic_mode else 'stop_HTTP'] = round(time.monotonic()-started, 3)
    report['status'] = 'passed'
except Exception:
    import traceback
    report.update(status='failed', traceback=traceback.format_exc())
    raise
finally:
    server.shutdown()
    (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
