"""Bounded page collection using Scrapling; runs in a cancellable child process."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from contextlib import redirect_stdout, redirect_stderr
import json
import hashlib
from functools import lru_cache
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import tempfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from engine import APP_DIR, HIDDEN, RESOURCE_ROOT, Runner, parse_urls, process_environment

MAX_LINKS = 2000
MAX_TEXT = 400_000
MEDIA_EXT = re.compile(r'\.(?:mp4|webm|mov|mkv|m4v|mp3|m4a|aac|flac|wav|ogg|opus|m3u8|mpd)$', re.I)
VIDEO_PAGE = re.compile(r'(?:youtube\.com/(?:watch|shorts/|live/)|youtu\.be/|bilibili\.com/video/|b23\.tv/|douyin\.com/(?:video/|note/)|v\.douyin\.com/|tiktok\.com/.*/video/|vimeo\.com/\d)', re.I)


@lru_cache(maxsize=1)
def ca_bundle():
    """libcurl's native CA loader needs an ASCII path on some Chinese Windows installs."""
    import certifi
    original = Path(certifi.where())
    if os.name != 'nt' or str(original).isascii():
        return str(original)
    content = original.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    for parent in (APP_DIR, *APP_DIR.parents):
        if not str(parent).isascii():
            continue
        try:
            folder = parent / '.yt-dlp-cache' / 'certificates'
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f'certifi-{digest}.pem'
            if not target.exists() or target.read_bytes() != content:
                temporary = target.with_suffix(f'.{os.getpid()}.tmp')
                temporary.write_bytes(content)
                temporary.replace(target)
            return str(target)
        except OSError:
            continue
    raise RuntimeError('无法建立HTTPS证书缓存，请将程序放到可写目录后重试。')


def web_url(value, base=''):
    value = urljoin(base, str(value).strip())
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return None
        if any(c.isspace() for c in value):
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
    except ValueError:
        return None


def link_kind(url, media=False):
    if media or MEDIA_EXT.search(urlsplit(url).path):
        return '媒体地址'
    return '视频页面' if VIDEO_PAGE.search(url) else '网页链接'


def browser_available():
    # Use an installed browser with a separate temporary profile, never the user's login profile.
    return any((Path(os.environ.get(key, '')) / suffix).is_file() for key, suffix in (
        ('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe'),
        ('PROGRAMFILES(X86)', 'Google/Chrome/Application/chrome.exe'),
        ('LOCALAPPDATA', 'Google/Chrome/Application/chrome.exe')))


@dataclass
class CollectOptions:
    urls: list[str]
    dynamic: bool = False
    scrolls: int = 0
    selector: str = ''
    max_pages: int = 1
    proxy: str = ''
    resume: dict | None = None
    job_id: str = ''
    history_path: str = ''

    def validate(self):
        self.urls = parse_urls('\n'.join(self.urls))
        if len(self.urls) > 20:
            raise ValueError('一次最多采集20个起始网页。')
        if not 0 <= self.scrolls <= 5 or not 1 <= self.max_pages <= 5:
            raise ValueError('滚动次数需为0–5，单个起始页的翻页上限需为1–5。')
        if any(not web_url(url) for url in self.urls):
            raise ValueError('采集地址须为不含用户名或密码的HTTP/HTTPS链接。')
        if self.proxy and not re.match(r'^(https?|socks5h?)://[^\s]+$', self.proxy):
            raise ValueError('请在偏好设置中填写有效代理地址。')
        if self.dynamic and not browser_available():
            raise ValueError('动态采集需要安装Google Chrome；普通网页模式无需浏览器。')
        if self.resume:
            if any(not web_url(task['url']) or not 0 <= task['depth'] < self.max_pages for task in self.resume['todo']):
                raise ValueError('历史续采位置无效。')
        return self


def parse_page(response, requested_url, selector='', captured=()):
    """Extract from rendered HTML or HTTP response using the same Scrapling selectors."""
    from scrapling import Selector
    from markdownify import markdownify

    base = str(response.url or requested_url)
    title = ' '.join(response.css('title::text').getall()).strip() or urlsplit(base).hostname or '网页'
    title = title[:300]
    base_tag = response.css('base::attr(href)').get()
    document_base = web_url(base_tag, base) if base_tag else base
    document_base = document_base or base
    roots = response.css(selector) if selector else [response]
    if selector and not roots:
        raise ValueError('该页没有匹配的内容，请检查CSS范围或尝试动态模式。')
    links = {}

    def add(href, label='', media=False):
        if not href or len(links) >= MAX_LINKS:
            return
        url = web_url(href, document_base)
        if not url:
            return
        kind = link_kind(url, media)
        item = {'url': url, 'title': re.sub(r'\s+', ' ', label).strip()[:200] or title,
                'kind': kind, 'source': base}
        # Prefer the media classification when a URL appears as both link and source.
        if url not in links or kind == '媒体地址':
            links[url] = item

    for node in roots:
        for a in node.css('a[href]'):
            add(a.attrib.get('href'), a.get_all_text(separator=' ', strip=True))
        # A CSS range can itself be a link/media element.
        if getattr(node, 'tag', '') == 'a':
            add(node.attrib.get('href'), node.get_all_text(separator=' ', strip=True))
        for el in node.css('video[src], audio[src], source[src], iframe[src]'):
            add(el.attrib.get('src'), el.attrib.get('title', ''), el.tag != 'iframe')
    for meta in response.css('meta[property="og:video"], meta[property="og:video:url"], meta[property="og:video:secure_url"], meta[property="og:audio"]'):
        add(meta.attrib.get('content'), media=True)
    for url in captured:
        add(url, '动态页面媒体请求', True)

    if selector:
        text_roots = roots
    else:
        text_roots = []
        for css in ('article', 'main', '[role="main"]', 'body'):
            text_roots = response.css(css)
            if text_roots:
                break
    fragments = []
    for node in text_roots:
        # Work on a copy: removing boilerplate must never mutate link extraction or shared responses.
        clean = Selector(node.html_content)
        for unwanted in clean.css('script, style, noscript, nav, footer, header, form, svg, template'):
            parent = unwanted._root.getparent()
            if parent is not None:
                parent.remove(unwanted._root)
        fragments.append(clean.html_content)
    md = markdownify('\n'.join(fragments), heading_style='ATX', strip=['img']).strip()
    plain = '\n'.join(line.strip() for line in Selector('\n'.join(fragments)).get_all_text(separator='\n', strip=True).splitlines() if line.strip())
    truncated = len(md) > MAX_TEXT or len(plain) > MAX_TEXT
    nxt = response.css('a[rel~="next"]::attr(href)').get()
    next_url = web_url(nxt, base) if nxt else None
    # Do not crawl off-site pagination, login pages, or all links recursively.
    if next_url and urlsplit(next_url).netloc.lower() != urlsplit(base).netloc.lower():
        next_url = None
    return {'url': base, 'title': title, 'text': plain[:MAX_TEXT], 'markdown': md[:MAX_TEXT], 'text_truncated': truncated,
            'links': list(links.values()), 'next': next_url}


def collect(options, emit):
    from scrapling.fetchers import Fetcher, StealthyFetcher

    options.validate()
    visited = set((options.resume or {}).get('visited', []))
    todo = list((options.resume or {}).get('todo', [dict(url=url, depth=0) for url in options.urls]))
    deferred = []
    while todo:
            task = todo.pop(0)
            url, page_number = task['url'], task['depth']
            if not url or url in visited:
                continue
            emit('collect_status', f'正在采集第{len(visited) + 1}页…')
            try:
                if MEDIA_EXT.search(urlsplit(url).path):
                    visited.add(url)
                    emit('collect_page', {'url': url, 'title': '媒体直链', 'text': '', 'markdown': '',
                        'links': [{'url': url, 'title': '媒体直链', 'kind': '媒体地址', 'source': url}], 'next': None,
                        '_checkpoint': dict(visited=sorted(visited), todo=todo + deferred)})
                    continue
                captured = set()

                def setup(page):
                    def on_response(response):
                        content_type = response.headers.get('content-type', '').lower()
                        path = urlsplit(response.url).path.lower()
                        if len(captured) < 500 and (MEDIA_EXT.search(path) or content_type.startswith(('audio/', 'video/')) or 'mpegurl' in content_type or 'dash+xml' in content_type):
                            if not path.endswith(('.ts', '.m4s')) and 'mp2t' not in content_type:
                                captured.add(response.url)
                    page.on('response', on_response)

                def actions(page):
                    for _ in range(options.scrolls):
                        page.evaluate('window.scrollBy(0, window.innerHeight)')
                        page.wait_for_timeout(600)
                    for src in page.locator('video, audio').evaluate_all('(els) => els.map(el => el.currentSrc).filter(Boolean)'):
                        captured.add(src)

                if options.dynamic:
                    response = StealthyFetcher.fetch(url, real_chrome=True, headless=True,
                        google_search=False, timeout=30000, wait=800, page_setup=setup,
                        page_action=actions, proxy=options.proxy or None)
                else:
                    response = Fetcher.get(url, timeout=25, retries=1, impersonate='chrome',
                                           proxy=options.proxy or None, follow_redirects=True,
                                           verify=ca_bundle())
                if response.status >= 400:
                    raise ValueError(f'网站返回HTTP {response.status}，可尝试动态模式或稍后重试。')
                if len(response.body) > 8 * 1024 * 1024:
                    raise ValueError('页面超过8MB，请缩小采集范围。')
                result = parse_page(response, url, options.selector, captured)
                visited.update((url, result['url']))
                if result['next'] and result['next'] not in visited and page_number + 1 < options.max_pages:
                    todo.insert(0, dict(url=result['next'], depth=page_number + 1))
                result['_checkpoint'] = dict(visited=sorted(visited), todo=todo + deferred)
                emit('collect_page', result)
                if todo:
                    time.sleep(1)
            except Exception as error:
                # Credentials in a proxy URL must not appear in logs or IPC errors.
                message = str(error).replace(options.proxy, '[代理]') if options.proxy else str(error)
                deferred.append(task)
                emit('collect_error', message[:900])
                emit('collect_checkpoint', dict(visited=sorted(visited), todo=todo + deferred))
    emit('collect_checkpoint', dict(visited=sorted(visited), todo=deferred))


def worker_main(job):
    # File IPC also works in the packaged Windows windowed executable, whose stdio is None.
    job = Path(job)
    with job.with_suffix('.events').open('a', encoding='utf-8', newline='\n') as output:
        store = None
        def emit(kind, value):
            if store and kind in ('collect_page', 'collect_checkpoint', 'collect_error'):
                store.collection_event(options.job_id, kind, value)
            output.write(json.dumps([kind, value], ensure_ascii=False) + '\n')
            output.flush()
        options = None
        try:
            options = CollectOptions(**json.loads(job.read_text(encoding='utf-8')))
            if options.job_id and options.history_path:
                from library import Library
                store = Library(options.history_path)
                store.change_job(options.job_id, worker=os.getpid())
            with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
                collect(options, emit)
            if store:
                record = store.job(options.job_id)
                pending = (record['data'].get('checkpoint') or {}).get('todo', [])
                store.change_job(options.job_id, status='partial' if pending or record['data']['errors'] else 'completed')
            return 0
        except Exception as error:
            message = str(error)
            if options and options.proxy:
                message = message.replace(options.proxy, '[代理]')
            emit('collect_error', message[:900])
            if store:
                store.change_job(options.job_id, status='partial')
            return 1


class CollectionRunner:
    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.process = None
        self.lock = threading.Lock()

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            process = self.process
        Runner._terminate_process(process)

    def run(self, options):
        process = None
        try:
            options.validate()
            command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(RESOURCE_ROOT / 'src/app.py')]
            environment = process_environment()
            temp = APP_DIR / 'build' / 'collection-temp'
            temp.mkdir(parents=True, exist_ok=True)
            environment.update(TEMP=str(temp), TMP=str(temp))
            with tempfile.TemporaryDirectory(prefix='job-', dir=temp) as directory:
                job = Path(directory) / 'request.json'
                job.write_text(json.dumps(asdict(options), ensure_ascii=False), encoding='utf-8')
                events = job.with_suffix('.events')
                events.touch()
                with self.lock:
                    if self.cancelled.is_set():
                        return
                    process = subprocess.Popen(command + ['--collect-worker', str(job)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=HIDDEN, env=environment)
                    self.process = process
                pending = b''
                with events.open('rb') as stream:
                    while True:
                        if self.cancelled.is_set():
                            Runner._terminate_process(process)
                        finished = process.poll() is not None
                        pending += stream.read()
                        lines = pending.split(b'\n')
                        pending = lines.pop()
                        for line in lines:
                            try:
                                kind, value = json.loads(line)
                                if kind in ('collect_status', 'collect_page', 'collect_error', 'collect_checkpoint'):
                                    self.emit(kind, value)
                            except (ValueError, TypeError):
                                continue
                        if finished:
                            break
                        self.cancelled.wait(.06)
                Runner._terminate_process(process)
                code = process.wait()
            if code and not self.cancelled.is_set():
                self.emit('collect_error', f'采集进程退出（{code}），请检查页面或动态模式所需的Chrome。')
        except Exception as error:
            if not self.cancelled.is_set():
                self.emit('collect_error', str(error)[:900])
        finally:
            Runner._terminate_process(process)
            if process:
                process.wait()
                if process.stdout:
                    process.stdout.close()
            with self.lock:
                self.process = None
            if options.job_id and options.history_path:
                from library import Library
                store = Library(options.history_path)
                record = store.job(options.job_id)
                if self.cancelled.is_set() or record['status'] == 'running':
                    store.change_job(options.job_id, status='interrupted')
            self.emit('collect_done', {'cancelled': self.cancelled.is_set()})
