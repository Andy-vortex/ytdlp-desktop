"""Local durable jobs, subscriptions, and a portable source archive."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit
import uuid

from engine import APP_DIR

CATALOG = APP_DIR / 'data' / 'library.sqlite3'


def identity(url):
    """Known video IDs survive tracking changes; unknown query strings stay intact."""
    p = urlsplit(url)
    host = (p.hostname or '').lower()
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        video = p.path.strip('/').split('/')[-1] if host == 'youtu.be' or p.path.startswith(('/shorts/', '/live/')) else parse_qs(p.query).get('v', [''])[0]
        if video and not parse_qs(p.query).get('list'):
            return 'youtube:' + video
    for domain, pattern, prefix in [('bilibili.com', r'/video/(BV[\w]+|av\d+)', 'bili:'),
                                      ('douyin.com', r'/(?:video|note)/(\d+)', 'douyin:')]:
        match = re.search(pattern, p.path)
        if (host == domain or host.endswith('.' + domain)) and match:
            # Bilibili's p parameter identifies a separate part.
            part = parse_qs(p.query).get('p', ['1'])[0] if prefix == 'bili:' else ''
            return prefix + match[1] + (':' + part if part else '')
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def safe_name(value, limit=60):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:limit].rstrip(' .') or '未命名'
    if re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Library:
    def __init__(self, path=CATALOG):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, title TEXT,
                    status TEXT, updated REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS pages(job TEXT, url TEXT, data TEXT, PRIMARY KEY(job,url));
                CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, name TEXT,
                    enabled INTEGER, interval INTEGER, next_check REAL, data TEXT);
                CREATE TABLE IF NOT EXISTS entries(subscription TEXT, key TEXT, pending INTEGER,
                    data TEXT, PRIMARY KEY(subscription,key));
                CREATE TABLE IF NOT EXISTS archives(folder TEXT PRIMARY KEY, updated REAL, data TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_job(self, kind, title, options, subscription=None):
        options = dict(options)
        options['proxy'] = ''  # Login/proxy secrets never enter persisted settings.
        options.pop('resume', None)
        options.pop('job_id', None)
        options.pop('history_path', None)
        job = uuid.uuid4().hex
        data = dict(options=options, subscription=subscription, checkpoint=None, errors=[],
                    owner=os.getpid(), worker=None, completed=[], files=[])
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job, kind, title, 'running', time.time(), json.dumps(data, ensure_ascii=False)))
        return job

    def job(self, job):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
            if not row:
                raise ValueError('找不到任务记录。')
            result = dict(row)
            result['data'] = json.loads(row['data'])
            result['pages'] = [json.loads(r[0]) for r in db.execute('SELECT data FROM pages WHERE job=? ORDER BY rowid', (job,))]
            return result

    def change_job(self, job, status=None, **updates):
        with self.db() as db:
            row = db.execute('SELECT data,status FROM jobs WHERE id=?', (job,)).fetchone()
            data = json.loads(row[0])
            data.update(updates)
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), status or row[1], time.time(), job))

    def collection_event(self, job, kind, value):
        # Page and its frontier commit together, inside the worker BEFORE GUI notification.
        with self.db() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job,)).fetchone()
            data = json.loads(row[0])
            if kind == 'collect_page':
                page = dict(value)
                data['checkpoint'] = page.pop('_checkpoint')
                db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)', (job, page['url'], json.dumps(page, ensure_ascii=False)))
            elif kind == 'collect_checkpoint':
                data['checkpoint'] = value
            elif kind == 'collect_error':
                data['errors'].append(value)
            db.execute('UPDATE jobs SET data=?,updated=? WHERE id=?', (json.dumps(data, ensure_ascii=False), time.time(), job))

    def history(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY updated DESC')]
        for row in rows:
            row['data'] = json.loads(row['data'])
            if row['status'] == 'running' and not alive(row['data'].get('worker') or row['data'].get('owner')):
                self.change_job(row['id'], status='interrupted')
                row['status'] = 'interrupted'
        return rows

    def download_event(self, job, kind, value):
        with self.db() as db:
            row = db.execute('SELECT data,status FROM jobs WHERE id=?', (job,)).fetchone()
            data, status = json.loads(row[0]), row[1]
            if kind == 'item':
                data['current'] = value['url']
            elif kind == 'file':
                if value not in data['files']:
                    data['files'].append(value)
            elif kind == 'item_done' and value['success'] and data.get('current'):
                url = data['current']
                if url not in data['completed']:
                    data['completed'].append(url)
                db.execute('UPDATE entries SET pending=0 WHERE key=?', (identity(url),))
            elif kind == 'done':
                data['result'] = value
                status = 'interrupted' if value['cancelled'] else ('partial' if value['failed'] or value.get('archive_errors') else 'completed')
            db.execute('UPDATE jobs SET data=?,status=?,updated=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), status, time.time(), job))

    def subscription(self, name, options, interval=3600, media_only=True, first_new=True):
        if interval not in (0, 900, 3600, 86400):
            raise ValueError('不支持的订阅检查间隔。')
        options = dict(options)
        options['proxy'] = ''
        key = uuid.uuid4().hex
        data = dict(options=options, media_only=media_only, first_new=first_new,
                    initialized=False, last_check=None, last_error='', last_new=0)
        with self.db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?)',
                       (key, name.strip()[:100] or '未命名订阅', 1, interval, time.time(), json.dumps(data, ensure_ascii=False)))
        return key

    def subscriptions(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM subscriptions ORDER BY rowid')]
            for row in rows:
                row['data'] = json.loads(row['data'])
                row['pending'] = db.execute('SELECT count(*) FROM entries WHERE subscription=? AND pending=1', (row['id'],)).fetchone()[0]
            return rows

    def configure_subscription(self, key, enabled, interval):
        if interval not in (0, 900, 3600, 86400):
            raise ValueError('不支持的订阅检查间隔。')
        with self.db() as db:
            db.execute('UPDATE subscriptions SET enabled=?,interval=?,next_check=? WHERE id=?', (int(enabled), interval, time.time() + interval, key))

    def finish_subscription(self, key, pages, successful, error=''):
        with self.db() as db:
            row = db.execute('SELECT * FROM subscriptions WHERE id=?', (key,)).fetchone()
            data = json.loads(row['data'])
            new = 0
            if successful:
                for page in pages:
                    for item in page['links']:
                        if data['media_only'] and item['kind'] == '网页链接':
                            continue
                        item_key = identity(item['url'])
                        old = db.execute('SELECT pending FROM entries WHERE subscription=? AND key=?', (key, item_key)).fetchone()
                        pending = old[0] if old else int(data['initialized'] or data['first_new'])
                        new += int(old is None and pending)
                        payload = dict(item, page_url=page['url'], page_title=page['title'])
                        db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?)', (key, item_key, pending, json.dumps(payload, ensure_ascii=False)))
                data.update(initialized=True, last_check=time.time(), last_error='', last_new=new)
            else:
                # A stopped/failed scan never establishes a baseline or consumes new items.
                data['last_error'] = error or '检查中断；下次重试，不更新已见记录'
            db.execute('UPDATE subscriptions SET data=?,next_check=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), time.time() + max(900, row['interval']), key))
            return new

    def pending(self, key):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM entries WHERE subscription=? AND pending=1 ORDER BY rowid', (key,))]

    def downloaded(self, url):
        with self.db() as db:
            db.execute('UPDATE entries SET pending=0 WHERE key=?', (identity(url),))

    def archive_index(self, folder, data):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO archives VALUES(?,?,?)', (str(folder), time.time(), json.dumps(data, ensure_ascii=False)))

    def archives(self):
        with self.db() as db:
            return [dict(folder=r['folder'], **json.loads(r['data'])) for r in db.execute('SELECT * FROM archives ORDER BY updated DESC')]

    def contexts(self, urls):
        remaining, result = set(urls), {}
        with self.db() as db:
            for row in db.execute('SELECT job,url,data FROM pages ORDER BY rowid DESC'):
                if not remaining:
                    break
                page = json.loads(row['data'])
                for item in page['links']:
                    if item['url'] in remaining:
                        result[item['url']] = dict(title=item['title'], source=item['source'], job_id=row['job'], page_url=page['url'])
                        remaining.remove(item['url'])
        return result


class Archive:
    def __init__(self, root, library=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = library

    def prepare(self, url, context=None):
        context = context or {}
        source = context.get('source') or url
        host = urlsplit(source).hostname or '来源'
        source_key = hashlib.sha256(identity(source).encode()).hexdigest()[:8]
        item_key = hashlib.sha256(identity(url).encode()).hexdigest()[:16]
        # Stable ID paths survive title changes and retries, and avoid duplicate media.
        folder = self.root / (safe_name(host, 45) + '_' + source_key) / item_key
        if not folder.resolve().is_relative_to(self.root):
            raise ValueError('归档目录超出资料库范围。')
        folder.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            data = dict(url=url, source=source, title=context.get('title') or url,
                        created=time.time(), files=[], transcripts=[])
        # Never let an old result erase existing asset records.
        data['url'] = url
        if context.get('title'):
            data['title'] = context['title']
        if context.get('page'):
            page = context['page']
            note = '\n说明：源正文超过长度上限，以下内容已截断。' if page.get('text_truncated') else ''
            for filename, body in [('网页正文.txt', page.get('text', '')), ('网页正文.md', page.get('markdown', ''))]:
                (folder / filename).write_text(f"{page['title']}\n来源：{page['url']}{note}\n\n{body}\n", encoding='utf-8-sig')
            data['page_title'] = page['title']
        self.save(folder, data)
        return folder

    def save(self, folder, data):
        folder = Path(folder).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError('归档目录超出资料库范围。')
        write_json(folder / '资料信息.json', data)
        (folder / '阅读说明.txt').write_text(
            f"{data['title']}\n原链接：{data['url']}\n采集来源：{data['source']}\n作者：{data.get('uploader') or '来源未提供'}\n\n"
            + data.get('description', '') + '\n\n媒体文件：\n' + '\n'.join(data['files'])
            + '\n\n关联转写稿：\n' + '\n'.join(data['transcripts']) + '\n', encoding='utf-8-sig')
        if self.library:
            self.library.archive_index(folder, data)

    def media(self, folder, metadata):
        folder = Path(folder).resolve()
        path = Path(metadata['filepath']).resolve()
        if not path.is_relative_to(folder) or not folder.is_relative_to(self.root) or not path.is_file():
            raise ValueError('归档文件与本条资料目录不匹配。')
        data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
        data.update({key: metadata[key] for key in ('id', 'title', 'uploader', 'description', 'webpage_url', 'upload_date') if metadata.get(key) is not None})
        relative = str(path.relative_to(folder))
        if relative not in data['files']:
            data['files'].append(relative)
        data['downloaded'] = time.time()
        self.save(folder, data)

    def attach(self, folder, source):
        folder, source = Path(folder).resolve(), Path(source).resolve()
        if not folder.is_relative_to(self.root) or source.suffix.lower() not in ('.txt', '.srt', '.md', '.vtt'):
            raise ValueError('请选择TXT、SRT、MD或VTT转写稿。')
        data = json.loads((folder / '资料信息.json').read_text(encoding='utf-8'))
        target = folder / '转写稿' / safe_name(source.stem, 75)
        target = target.with_name(target.name + '_' + hashlib.sha256(source.read_bytes()).hexdigest()[:10] + source.suffix.lower())
        target.parent.mkdir(exist_ok=True)
        if not target.resolve().is_relative_to(folder):
            raise ValueError('转写稿目录超出本条资料范围。')
        if not target.exists():
            shutil.copy2(source, target)
        relative = str(target.relative_to(folder))
        if relative not in data['transcripts']:
            data['transcripts'].append(relative)
        self.save(folder, data)
        return target
