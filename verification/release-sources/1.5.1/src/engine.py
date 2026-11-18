from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

APP_NAME = 'YT-DLP下载器'
APP_VERSION = '1.5.1'
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
VENDOR = RESOURCE_ROOT / 'vendor'
BIN = VENDOR / 'bin'
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'YtDlpDesktop'
MODES = ('音频 · 原始最佳', '音频 · WAV 32 位', '音频 · FLAC', '音频 · MP3 320K', '视频 · 最佳画质 MKV')
RESOLUTIONS = ('最佳', '2160p / 4K', '1440p', '1080p', '720p', '480p')
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
RATE_LIMIT_COOLDOWN = 300
MAX_RATE_LIMIT_COOLDOWN = 900
MAX_RATE_LIMIT_RETRIES = 2
RATE_LIMIT_PATTERN = re.compile(r'(?:HTTP(?:\s+Error|\s+status)?\s*[:=]?\s*429\b|(?:ERROR|WARNING):.*?\bToo Many Requests\b)', re.I)
SLEEP_PATTERN = re.compile(r'\bSleeping\s+([\d.]+)\s+seconds?\b', re.I)
RETRY_AFTER_PATTERN = re.compile(r'\bRetry-After\s*[:=]\s*(\d+)\b', re.I)
# YouTube's IP-level bot check; only logged-in cookies get past it.
BOT_CHECK_PATTERN = re.compile(r'confirm you.{0,3}re not a bot', re.I)
BOT_CHECK_HINT = ('YouTube 要求登录验证（Sign in to confirm you’re not a bot）。这是 YouTube 对当前网络出口 IP 的限制，'
                  '不是链接解析错误。请在“偏好设置 → 网络”选择已登录 YouTube 的 cookies.txt，然后点“重试失败”。')
BOT_CHECK_STALE_HINT = ('YouTube 仍要求登录验证：所选 cookies 可能已过期或导出时未登录，'
                        '请重新导出 cookies.txt 后点“重试失败”。')


def engine_path():
    updated = STATE_DIR / 'engines' / 'yt-dlp.exe'
    receipt = updated.with_suffix('.json')
    try:
        metadata = json.loads(receipt.read_text(encoding='utf-8'))
        if updated.is_file() and hashlib.sha256(updated.read_bytes()).hexdigest() == metadata['sha256']:
            return updated
    except (OSError, ValueError, KeyError):
        pass
    return VENDOR / 'yt-dlp.exe'


def engine_version():
    try:
        if engine_path() != VENDOR / 'yt-dlp.exe':
            return json.loads((STATE_DIR / 'engines' / 'yt-dlp.json').read_text(encoding='utf-8'))['version']
        return json.loads((VENDOR / 'manifest.json').read_text(encoding='utf-8'))[0]['version']
    except (OSError, ValueError, KeyError, IndexError):
        return '已内置'


def parse_urls(text):
    urls = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        url = raw.strip()
        if not url or url.startswith(('#', ';', ']')):
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or any(c.isspace() for c in url):
            raise ValueError(f'第 {line_number} 行不是有效的 HTTP / HTTPS 链接。请每行填写一个链接。')
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ValueError('请先粘贴至少一个视频或音频链接。')
    return urls


def read_url_file(path):
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('链接文件超过 5 MB，请分成多个较小的 TXT 文件导入。')
    data = path.read_bytes()
    encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            return parse_urls(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError('无法读取文本编码，请将链接文件另存为 UTF-8。')


@dataclass(frozen=True)
class Options:
    output: Path
    mode: str = MODES[0]
    resolution: str = RESOLUTIONS[0]
    playlist: bool = False
    proxy: str = ''
    batch_safe: bool = True
    referers: dict[str, str] = field(default_factory=dict)
    archive_root: str = ''
    archive_contexts: dict = field(default_factory=dict)
    cookies: str = ''


def check_cookie_file(path):
    """Validate a user-chosen Netscape cookies.txt; its contents are never stored by the app."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValueError(f'找不到 cookies 文件：{file}\n请在“偏好设置 → 网络”中重新选择。')
    if file.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('cookies 文件超过 10 MB，请确认选择的是导出的 cookies.txt。')
    with file.open('rb') as handle:
        head = handle.read(4096).lstrip(b'\xef\xbb\xbf \t\r\n')
    if head.startswith((b'{', b'[')):
        raise ValueError('这个 cookies 文件是 JSON 格式，yt-dlp 需要 Netscape 格式的 cookies.txt，请重新导出。')
    return file


def store_failed_job(urls, options):
    """Keep a recoverable failed queue independently of the source URL textbox."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / 'failed-downloads.json'
    settings = asdict(options)
    settings['output'] = str(options.output)
    # Proxy credentials remain session-only, as in the preferences UI.
    settings['proxy'] = ''
    # The cookies path lives in settings.json; retries always use the current preference.
    settings['cookies'] = ''
    data = {'version': 1, 'urls': list(dict.fromkeys(urls)), 'options': settings}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return path


def load_failed_job():
    path = STATE_DIR / 'failed-downloads.json'
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('urls'), list):
            return None
        if not data['urls'] or any(not isinstance(url, str) for url in data['urls']):
            return None
        urls = parse_urls('\n'.join(data['urls']))
        settings = data['options']
        if not isinstance(settings, dict) or not isinstance(settings.get('output'), str) or not settings['output'].strip():
            return None
        options = Options(output=Path(settings['output']), mode=settings.get('mode', MODES[0]),
                          resolution=settings.get('resolution', RESOLUTIONS[0]),
                          playlist=bool(settings.get('playlist', False)), proxy='',
                          batch_safe=bool(settings.get('batch_safe', True)),
                          referers={k: v for k, v in settings.get('referers', {}).items()
                                    if isinstance(k, str) and isinstance(v, str)}
                          if isinstance(settings.get('referers', {}), dict) else {},
                          archive_root=settings.get('archive_root', ''),
                          archive_contexts=settings.get('archive_contexts', {}))
        if options.mode not in MODES or options.resolution not in RESOLUTIONS:
            return None
        return {'urls': urls, 'options': options}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def build_command(url, options):
    if options.mode not in MODES or options.resolution not in RESOLUTIONS:
        raise ValueError('Unsupported output option')
    command = [str(engine_path()), '--ignore-config', '--no-plugin-dirs', '--encoding', 'utf-8',
               '--ffmpeg-location', str(BIN), '--js-runtimes', 'deno:' + str(BIN / 'deno.exe'),
               '--newline', '--no-color', '--no-quiet', '--progress', '--progress-delta', '0.15', '--no-simulate',
               '--windows-filenames', '--trim-filenames', '180', '--no-overwrites', '--continue',
               '--socket-timeout', '20', '--retries', '5', '--fragment-retries', '5',
               '--abort-on-unavailable-fragments',
               '--concurrent-fragments', '1' if options.batch_safe else '3', '-P', str(options.output),
               '-o', '%(title).140B [%(id)s].%(ext)s',
               '--progress-template', 'download:__GUI_PROGRESS__%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(progress.status)s',
               '--print', 'before_dl:__GUI_TITLE__%(title)j',
               '--print', 'after_move:__GUI_FILE__%(filepath)j']
    if options.archive_root:
        command += ['--print', 'after_move:__GUI_ARCHIVE__%(.{id,title,uploader,description,webpage_url,upload_date,filepath})j']
    command += ['--extractor-retries', '5',
                '--retry-sleep', 'http:exp=2:60', '--retry-sleep', 'fragment:exp=2:60',
                '--retry-sleep', 'extractor:exp=2:60', '--retry-sleep', 'file_access:exp=1:10']
    if options.batch_safe:
        command += ['--sleep-interval', '10', '--max-sleep-interval', '20', '--sleep-requests', '1']
    if options.playlist:
        command += ['--yes-playlist', '--lazy-playlist']
    else:
        command += ['--no-playlist', '--playlist-items', '1']
    if options.proxy.strip():
        parsed = urllib.parse.urlsplit(options.proxy.strip())
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname:
            raise ValueError('代理地址应以 http://、https:// 或 socks5:// 开头。')
        command += ['--proxy', options.proxy.strip()]
    if options.cookies.strip():
        command += ['--cookies', str(check_cookie_file(options.cookies.strip()))]
    referer = options.referers.get(url, '')
    if referer:
        parsed_referer = urllib.parse.urlsplit(referer)
        if parsed_referer.scheme not in ('http', 'https') or not parsed_referer.hostname or any(c in referer for c in '\r\n'):
            raise ValueError('采集来源地址无效，请重新采集后加入下载列表。')
        command += ['--referer', referer]
    if options.mode == MODES[4]:
        height = options.resolution.split('p')[0] if options.resolution != '最佳' else None
        limit = f'[height<={height}]' if height else ''
        command += ['-f', f'bv*{limit}+ba/b{limit}', '--merge-output-format', 'mkv', '--remux-video', 'mkv']
    else:
        command += ['-f', 'ba/b', '-x']
        if options.mode == MODES[0]:
            command += ['--audio-format', 'best']
        elif options.mode == MODES[1]:
            command += ['--audio-format', 'wav', '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-c:a pcm_f32le']
        elif options.mode == MODES[2]:
            command += ['--audio-format', 'flac', '--postprocessor-args', 'ExtractAudio+ffmpeg_o:-sample_fmt s32 -bits_per_raw_sample 24']
        else:
            command += ['--audio-format', 'mp3', '--audio-quality', '320K']
    return command + ['--', url]


def number(raw):
    try:
        value = float(raw)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def parse_line(line):
    if line.startswith('__GUI_PROGRESS__'):
        fields = line.removeprefix('__GUI_PROGRESS__').split('|')
        if len(fields) == 6:
            downloaded, total, estimate, speed, eta = map(number, fields[:5])
            size = total or estimate
            return 'progress', {'fraction': min((downloaded or 0) / size, 1) if size else None,
                                'downloaded': downloaded, 'total': size, 'speed': speed,
                                'eta': eta, 'status': fields[5]}
    for prefix, kind in [('__GUI_TITLE__', 'title'), ('__GUI_FILE__', 'file'), ('__GUI_ARCHIVE__', 'archive')]:
        if line.startswith(prefix):
            try:
                return kind, json.loads(line[len(prefix):])
            except ValueError:
                pass
    return 'log', line


def process_environment():
    environment = os.environ.copy()
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYINSTALLER_RESET_ENVIRONMENT='1')
    environment['PATH'] = str(BIN) + os.pathsep + environment.get('PATH', '')
    return environment


class Runner:
    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.bot_check = False

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            process = self.process
        self._terminate_process(process)

    @staticmethod
    def _terminate_process(process):
        if process and process.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, creationflags=HIDDEN, timeout=10)
                else:
                    process.terminate()
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def _wait(self, seconds, index, total, reason):
        deadline = time.monotonic() + seconds
        self.emit('log', f'{reason}，等待 {math.ceil(seconds)} 秒；可随时点击停止。')
        while not self.cancelled.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.emit('resuming', {'index': index, 'total': total})
                return True
            self.emit('waiting', {'index': index, 'total': total,
                                  'seconds': math.ceil(remaining), 'reason': reason})
            self.cancelled.wait(min(1, remaining))
        return False

    def _run_once(self, command, index, total, files):
        """Read output off-thread so UI countdowns and cancellation keep moving."""
        with self.lock:
            if self.cancelled.is_set():
                return -1, False, None
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL,
                                       env=process_environment(), cwd=str(BIN), creationflags=HIDDEN)
            self.process = process
        output = queue.Queue()

        def read_output():
            try:
                for raw in process.stdout:
                    output.put(raw)
            except (OSError, ValueError):
                pass
            finally:
                output.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        limited, retry_after = False, None
        sleep_deadline, last_seconds = None, None
        try:
            while not self.cancelled.is_set():
                if sleep_deadline is not None:
                    seconds = max(0, math.ceil(sleep_deadline - time.monotonic()))
                    if seconds == 0:
                        sleep_deadline = None
                        self.emit('resuming', {'index': index, 'total': total})
                    elif seconds != last_seconds:
                        last_seconds = seconds
                        self.emit('waiting', {'index': index, 'total': total, 'seconds': seconds,
                                              'reason': '下载间隔 / 重试等待'})
                try:
                    raw = output.get(timeout=0.2)
                except queue.Empty:
                    continue
                if raw is None:
                    break
                line = raw.rstrip('\r\n')
                if not line:
                    continue
                kind, value = parse_line(line)
                if kind == 'file' and isinstance(value, str) and value not in files:
                    files.append(value)
                if kind == 'archive' and getattr(self, 'archive', None):
                    try:
                        self.archive.media(self.archive_folder, value)
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        # Media success is retained, but the archive failure is surfaced separately.
                        self.emit('log', f'归档信息保存失败（媒体已保留）：{error}')
                        self.archive_errors.append(str(error))
                self.emit(kind, value)
                if kind == 'log':
                    if BOT_CHECK_PATTERN.search(line):
                        self.bot_check = True
                    match = RETRY_AFTER_PATTERN.search(line)
                    if match:
                        retry_after = max(retry_after or 0, int(match.group(1)))
                    if RATE_LIMIT_PATTERN.search(line):
                        limited = True
                        self._terminate_process(process)
                        break
                    match = SLEEP_PATTERN.search(line)
                    if match:
                        seconds = number(match.group(1))
                        if seconds:
                            sleep_deadline, last_seconds = time.monotonic() + seconds, None
                elif sleep_deadline is not None:
                    sleep_deadline = None
                    self.emit('resuming', {'index': index, 'total': total})
            if self.cancelled.is_set():
                self._terminate_process(process)
            result = process.wait()
            return result, limited, retry_after
        finally:
            process.stdout.close()
            with self.lock:
                if self.process is process:
                    self.process = None

    def download(self, urls, options):
        success, failed, files, failed_urls = 0, 0, [], []
        completed_urls, recovery_urls = set(), []
        bot_checks = 0
        previous_job = load_failed_job()
        index, abnormal = 0, False
        self.archive = None
        self.archive_errors = []
        try:
            options.output.mkdir(parents=True, exist_ok=True)
            if options.archive_root:
                from library import Archive, Library
                self.archive = Archive(options.archive_root, getattr(self, 'library', None) or Library())
            for index, url in enumerate(urls, 1):
                if self.cancelled.is_set():
                    break
                self.emit('item', {'index': index, 'total': len(urls), 'url': url})
                first_file = len(files)
                effective = options
                if self.archive:
                    context = dict(options.archive_contexts.get(url) or {})
                    if context.get('job_id'):
                        try:
                            record = self.archive.library.job(context['job_id'])
                            context['page'] = next((p for p in record['pages'] if p['url'] == context['page_url']), None)
                        except ValueError:
                            self.emit('log', '找不到原采集历史，本次归档保留来源链接，未附网页正文。')
                    self.archive_folder = self.archive.prepare(url, context)
                    effective = replace(options, output=self.archive_folder / '媒体')
                command = build_command(url, effective)
                rate_attempt = 0
                self.bot_check = False
                while not self.cancelled.is_set():
                    result, limited, retry_after = self._run_once(command, index, len(urls), files)
                    if not limited or self.cancelled.is_set():
                        break
                    cooldown = min(RATE_LIMIT_COOLDOWN * 2 ** rate_attempt, MAX_RATE_LIMIT_COOLDOWN)
                    # A visible server Retry-After may be longer than our normal cap.
                    cooldown = max(cooldown, retry_after or 0)
                    if rate_attempt >= MAX_RATE_LIMIT_RETRIES:
                        result = result or 1
                        self.emit('log', '此链接多次触发 HTTP 429，已记入失败清单。')
                        if index < len(urls):
                            self._wait(cooldown, index, len(urls), '网站限流，暂停整个下载队列')
                        break
                    rate_attempt += 1
                    if not self._wait(cooldown, index, len(urls),
                                      f'网站限流，冷却后重试此链接（{rate_attempt}/{MAX_RATE_LIMIT_RETRIES}）'):
                        break
                if self.cancelled.is_set():
                    self.emit('item_done', {'index': index, 'cancelled': True, 'success': False,
                                            'files': len(files) - first_file})
                    break
                if result == 0:
                    success += 1
                    completed_urls.add(url)
                else:
                    failed += 1
                    failed_urls.append(url)
                    if self.bot_check:
                        bot_checks += 1
                        self.emit('log', BOT_CHECK_STALE_HINT if options.cookies.strip() else BOT_CHECK_HINT)
                    self.emit('log', f'此链接未完成（退出码 {result}），继续处理队列中的下一项。')
                self.emit('item_done', {'index': index, 'cancelled': False, 'success': result == 0,
                                        'files': len(files) - first_file,
                                        'bot_check': result != 0 and self.bot_check})
        except Exception as error:
            abnormal = True
            failed += 1
            if index and urls[index - 1] not in completed_urls:
                failed_urls.append(urls[index - 1])
            self.emit('log', f'错误：{error}')
        finally:
            failed_urls = list(dict.fromkeys(failed_urls))
            recovery_urls = list(failed_urls)
            if self.cancelled.is_set() or abnormal:
                recovery_urls += [url for url in urls[max(0, index - 1):] if url not in completed_urls]
            # Retain older unfinished links for this same output/format job.
            same_options = False
            if previous_job:
                previous_options = asdict(previous_job['options'])
                current_options = asdict(options)
                for key in ('proxy', 'cookies'):
                    previous_options.pop(key, None)
                    current_options.pop(key, None)
                same_options = previous_options == current_options
                if same_options:
                    recovery_urls += [url for url in previous_job['urls'] if url not in completed_urls]
            recovery_urls = list(dict.fromkeys(recovery_urls))
            failed_path = None
            try:
                if recovery_urls or not previous_job or (same_options and any(url in previous_job['urls'] for url in urls)):
                    failed_path = str(store_failed_job(recovery_urls, options))
                elif previous_job:
                    failed_path = str(STATE_DIR / 'failed-downloads.json')
            except OSError as error:
                self.emit('log', f'失败清单未能保存：{error}')
            self.emit('done', {'success': success, 'failed': failed, 'files': files,
                               'cancelled': self.cancelled.is_set(), 'failed_urls': failed_urls,
                               'pending_urls': recovery_urls, 'failed_path': failed_path,
                               'archive_errors': self.archive_errors, 'bot_check': bot_checks > 0})

    def update(self):
        temporary = None
        try:
            self.emit('title', '正在检查官方更新…')
            request = urllib.request.Request('https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest',
                                             headers={'User-Agent': 'YtDlpDesktop/1.0', 'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(request, timeout=25) as response:
                release = json.load(response)
            version = release['tag_name']
            if version == engine_version():
                self.emit('updated', {'version': version, 'unchanged': True})
                return
            asset = next(a for a in release['assets'] if a['name'] == 'yt-dlp.exe')
            url = asset['browser_download_url']
            if not url.startswith('https://github.com/yt-dlp/yt-dlp/releases/download/'):
                raise ValueError('Unexpected update origin')
            expected = asset.get('digest', '').removeprefix('sha256:')
            if len(expected) != 64 or asset['size'] > 150 * 1024 * 1024:
                raise ValueError('Update checksum or size is unavailable')
            target = STATE_DIR / 'engines' / 'yt-dlp.exe'
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.download')
            checksum, received = hashlib.sha256(), 0
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'YtDlpDesktop/1.0'}), timeout=25) as response, temporary.open('wb') as handle:
                while block := response.read(256 * 1024):
                    if self.cancelled.is_set():
                        return
                    handle.write(block)
                    checksum.update(block)
                    received += len(block)
                    if received > 150 * 1024 * 1024:
                        raise ValueError('Update exceeds expected size')
                    self.emit('progress', {'fraction': received / asset['size'], 'downloaded': received,
                                           'total': asset['size'], 'speed': None, 'eta': None, 'status': 'updating'})
            if checksum.hexdigest() != expected or received != asset['size']:
                raise ValueError('更新文件校验失败，已保留原来的引擎。')
            temporary.replace(target)
            target.with_suffix('.json').write_text(json.dumps({'version': version, 'sha256': expected}), encoding='utf-8')
            self.emit('updated', {'version': version, 'unchanged': False})
        except Exception as error:
            self.emit('update_error', str(error))
        finally:
            if temporary and temporary.is_file():
                temporary.unlink()
            self.emit('update_done', {'cancelled': self.cancelled.is_set()})


def diagnostics():
    result = {'app_version': APP_VERSION, 'resource_root': str(RESOURCE_ROOT), 'components': {}}
    import scrapling
    from scrapling.fetchers import Fetcher, StealthyFetcher
    from collector import browser_available
    result['components']['scrapling'] = {'returncode': 0, 'version': [scrapling.__version__],
                                         'dynamic_Chrome_available': browser_available()}
    for name, command in [
        ('yt-dlp', [str(engine_path()), '--ignore-config', '--version']),
        ('ffmpeg', [str(BIN / 'ffmpeg.exe'), '-version']),
        ('ffprobe', [str(BIN / 'ffprobe.exe'), '-version']),
        ('deno', [str(BIN / 'deno.exe'), '--version'])
    ]:
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=90, cwd=BIN, env=process_environment(), creationflags=HIDDEN)
        result['components'][name] = {'returncode': process.returncode,
                                      'version': (process.stdout or process.stderr).splitlines()[:1]}
        if process.returncode:
            raise RuntimeError(f'{name}: {process.stderr}')
    return result
