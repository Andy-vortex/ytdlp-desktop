from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import math
import os
from pathlib import Path
import queue
import random
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse

import applog
import formats
import messages
from messages import cut, message_of, msg
import naming
import paths
import procutil
import unfinished
import updater
# (2.0.0 S2 P4) Used unqualified here (Runner._run_once, diagnostics); the 1.5.1 copies of the paths
# names (RESOURCE_ROOT, VENDOR, BIN, APP_DIR, STATE_DIR, engine_version) are gone: import them from paths
# / updater.
from procutil import HIDDEN, process_environment
from updater import engine_path
import urls as urltools   # under another name: Runner.download keeps its parameter ``urls``

APP_NAME = 'YT-DLP下载器'
APP_VERSION = '2.0.1'
# The persisted format values (the preset ids, formats.MODES) and resolutions ('best': no limit).
MODES = formats.MODES
RESOLUTIONS = (formats.BEST, '2160p / 4K', '1440p', '1080p', '720p', '480p')
RATE_LIMIT_COOLDOWN = 300
MAX_RATE_LIMIT_COOLDOWN = 900
MAX_RATE_LIMIT_RETRIES = 2
# Seconds between the end of one download and the start of the next (a random value in this range).
PACE_SECONDS = (10, 20)
# A subtitle request answered with 429 fails that item only; it must not cool the whole queue down.
RATE_LIMIT_PATTERN = re.compile(r'^(?!.*Unable to download video subtitles).*?(?:HTTP(?:\s+Error|\s+status)?\s*[:=]?\s*429\b|(?:ERROR|WARNING):.*?\bToo Many Requests\b)', re.I)
SLEEP_PATTERN = re.compile(r'\bSleeping\s+([\d.]+)\s+seconds?\b', re.I)
RETRY_AFTER_PATTERN = re.compile(r'\bRetry-After\s*[:=]\s*(\d+)\b', re.I)
# YouTube's IP-level bot check; only logged-in cookies get past it.
BOT_CHECK_PATTERN = re.compile(r'confirm you.{0,3}re not a bot', re.I)
BOT_CHECK_HINT = msg('core.engine.bot_check_hint')
BOT_CHECK_STALE_HINT = msg('core.engine.bot_check_stale')
# Engine output that usually means the bundled or updated yt-dlp is too old for the site.
STALE_PATTERN = re.compile(r'Confirm you are on the latest version|Signature solving failed|n challenge solving failed|older than 90 days')
SUBTITLE_FAILURE = 'Unable to download video subtitles'
ENGINE_OPTION_ERROR = 'no such option'
# Short reasons for a row; the first pattern that matches the engine's error line wins.
# (The region rule stands first: YouTube words a region block as "Video unavailable. The uploader
# has not made this video available in your country".)
ERROR_REASONS = (
    (re.compile(r'not (?:made this video )?available in your country|geo.?restrict', re.I), msg('core.reason.region')),
    (re.compile(r'Video unavailable|Private video|has been removed', re.I), msg('core.reason.unavailable')),
    # The site answered, but refused the page or has no such page ("Unable to download webpage:
    # HTTP Error 404: Not Found"): not a network problem.  Before the network rule, which also
    # matches "Unable to download webpage".  Media data refused later keeps the two rules below.
    (re.compile(r'Unable to download (?!video data)[^:]*: HTTP Error 403\b', re.I), msg('core.reason.page_403')),
    (re.compile(r'Unable to download (?!video data)[^:]*: HTTP Error 404\b', re.I), msg('core.reason.page_404')),
    (re.compile(r'Unable to download (?!video data)[^:]*: HTTP Error 410\b', re.I), msg('core.reason.page_410')),
    (re.compile(r'timed out|getaddrinfo failed|Unable to download webpage|Connection (reset|refused)|10060', re.I),
     msg('core.reason.network')),
    (re.compile(r'HTTP Error 403', re.I), msg('core.reason.http_403')),
    (re.compile(r'HTTP Error 404', re.I), msg('core.reason.http_404')),
    (re.compile(r'HTTP Error 410', re.I), msg('core.reason.http_410')),
    (re.compile(r'Requested format is not available', re.I), msg('core.reason.format')),
    (re.compile(r'Unsupported URL', re.I), msg('core.reason.unsupported')),
)
NETWORK_REASON = msg('core.reason.network')
# (polish, ui-daily-flow-2; a deviation from WP2 section _note_line) yt-dlp appends its bug-report
# text, which ends in "Confirm you are on the latest version", to some transport errors (a proxy
# that is down).  STALE_PATTERN stays as H8 has it; a line that is a transport or proxy failure
# never marks the engine as outdated.
_TRANSPORT_FAILURE = re.compile(r'Unable to connect to proxy|ProxyError|Tunnel connection failed|'
                                + next(pattern.pattern for pattern, reason in ERROR_REASONS if reason == NETWORK_REASON),
                                re.I)
BOT_CHECK_REASON = msg('core.reason.bot_check')
STALE_REASON = msg('core.reason.stale')
ENGINE_ERROR_REASON = msg('core.reason.engine_error')
# (1.8.0, S1) The reason of the waiting events of _run_once's own sleep / retry countdown.
WAIT_PACE_OR_RETRY = msg('core.wait.pace_or_retry')
# "[youtube] AAA: " in front of an engine error; never "[WinError 5] ...: " or "[Errno 2] ...: ",
# whose text is the cause itself.
_EXTRACTOR_PREFIX = re.compile(r'^\[(?!(?:WinError|Errno)\b)[A-Za-z][^\]]*\]\s+\S+:\s+')


def explain_error(text, code=1, bot_check=False, stale=False) -> str:
    """A short Chinese reason for a download that did not finish.  Pure; never raises.

    (1.8.0) A message.  The patterns run on the zh text, as in 1.7.0.  The fallback keeps the
    type of ``text``: a Message keeps its key (en shows it whole) and its zh text is the 1.7.0
    expression (stripped, the extractor prefix removed, cut to 70 characters), a Raw text stays
    Raw, a plain text gives the 1.7.0 expression."""
    plain = messages.zh(text or '').strip()
    if bot_check:
        return BOT_CHECK_REASON
    for pattern, reason in ERROR_REASONS:
        if pattern.search(plain):
            return reason
    if stale:
        return STALE_REASON
    if not plain:
        return msg('core.reason.exit_code', code=code)
    fallback = _EXTRACTOR_PREFIX.sub('', plain, count=1)[:70]
    if isinstance(text, messages.Message):
        if fallback == messages.zh(text)[:70]:
            return cut(text, 70)
        # (final review 1, ZH-1) edge whitespace or an extractor prefix: the 1.7.0 zh text, key kept
        # (rebuilt the way cut() rebuilds a Message)
        return messages.Message(fallback, text.key, text.params)
    return messages.raw(fallback) if isinstance(text, messages.Raw) else fallback


def parse_urls(text):
    urls = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        url = raw.strip()
        if not url or url.startswith(('#', ';', ']')):
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or any(c.isspace() for c in url):
            raise ValueError(msg('core.links.bad_line', line=line_number))
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ValueError(msg('core.links.none'))
    return urls


def read_url_file(path):
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError(msg('core.links.file_too_large'))
    data = path.read_bytes()
    encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            return parse_urls(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError(msg('core.links.encoding'))


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
    naming: str = 'title'
    embed_metadata: bool = True
    embed_cover: bool = True
    subtitles: bool = False
    skip_downloaded: bool = False

    def to_dict(self, private=False) -> dict:
        """Plain data for JSON.  The proxy (it may carry a password) and the cookies path
        are blank unless ``private``: they are session values and never stored."""
        data = asdict(self)
        data['output'] = str(self.output)
        if not private:
            data['proxy'] = ''
            data['cookies'] = ''
        return data

    @classmethod
    def from_dict(cls, data, **overrides) -> 'Options':
        """The reverse of to_dict.  Unknown keys are ignored, missing ones get their default,
        flags are coerced; ValueError for an output, format or resolution that cannot be used."""
        if not isinstance(data, dict):
            raise ValueError(msg('core.options.not_object'))
        values = {**data, **overrides}
        output = values.get('output')
        if isinstance(output, Path):
            output = str(output)
        if not isinstance(output, str) or not output.strip():
            raise ValueError(msg('core.options.no_output'))
        mode = values.get('mode', MODES[0])
        resolution = values.get('resolution', RESOLUTIONS[0])
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError(msg('core.formats.bad_mode', mode=repr(mode)))
        if not isinstance(resolution, str) or resolution not in RESOLUTIONS:
            raise ValueError(msg('core.options.bad_resolution', resolution=repr(resolution)))

        def text(key):
            return values[key] if isinstance(values.get(key), str) else ''

        referers = values.get('referers')
        contexts = values.get('archive_contexts')
        naming_style = values.get('naming', 'title')
        return cls(output=Path(output), mode=mode, resolution=resolution,
                   playlist=bool(values.get('playlist', False)), proxy=text('proxy'),
                   batch_safe=bool(values.get('batch_safe', True)),
                   referers={k: v for k, v in referers.items() if isinstance(k, str) and isinstance(v, str)}
                   if isinstance(referers, dict) else {},
                   archive_root=text('archive_root'),
                   archive_contexts=dict(contexts) if isinstance(contexts, dict) else {},
                   cookies=text('cookies'),
                   naming=naming_style if isinstance(naming_style, str) and naming_style in naming.NAMING else 'title',
                   embed_metadata=bool(values.get('embed_metadata', True)),
                   embed_cover=bool(values.get('embed_cover', True)),
                   subtitles=bool(values.get('subtitles', False)),
                   skip_downloaded=bool(values.get('skip_downloaded', False)))


# The first line of a cookies file that the engine keeps must be this header, or it refuses the
# whole file (http.cookiejar.MozillaCookieJar.magic_re) after it has printed every line it skipped.
COOKIE_HEADER = re.compile(r'#( Netscape)? HTTP Cookie File')
COOKIE_HTTPONLY = '#HttpOnly_'


def _first_kept_cookie_line(text):
    """The first line the engine keeps when it reads a cookies file (it skips entries that do not
    have seven tab-separated fields or whose expiry is not a number), or None."""
    for line in text.splitlines():
        if line.startswith(COOKIE_HTTPONLY):
            line = line[len(COOKIE_HTTPONLY):]
        if line.startswith('#') or not line.strip():
            return line
        fields = line.split('\t')
        if len(fields) == 7 and (not fields[4] or fields[4].isdigit()):
            return line
    return None


def check_cookie_file(path):
    """Validate a user-chosen Netscape cookies.txt; its contents are never stored by the app.

    A file the engine would refuse anyway (no "# Netscape HTTP Cookie File" header as the first
    line it keeps) is refused here, before the engine reads it: the engine prints every line it
    skips, cookie values included.  Returns the absolute path (a relative one would make the
    engine write its cookie jar into the program folder, its working folder).
    """
    file = Path(os.path.abspath(Path(path).expanduser()))
    if not file.is_file():
        raise ValueError(msg('core.cookies.missing', file=os.fspath(file)))
    if file.stat().st_size > 10 * 1024 * 1024:
        raise ValueError(msg('core.cookies.too_large'))
    with file.open('rb') as handle:
        head = handle.read(64 * 1024)
    stripped = head.lstrip(b'\xef\xbb\xbf \t\r\n')
    if stripped.startswith((b'{', b'[')):
        raise ValueError(msg('core.cookies.json'))
    text = head.decode('utf-8', 'replace').removeprefix('\ufeff')
    first = _first_kept_cookie_line(text)
    if first is None or not COOKIE_HEADER.search(first):
        raise ValueError(msg('core.cookies.not_netscape'))
    return file


# Engine lines that repeat a line of the cookies file (cookie values included): yt-dlp prints
# "WARNING: skipping cookie file entry due to invalid length 6: '<line>'" for every entry it
# cannot use, and http.cookiejar words a broken entry "invalid Netscape format cookies file
# '<file>': '<line>'".  Such a line never reaches the log, the item's error or any file.
COOKIE_ECHO_PATTERN = re.compile(r'cookie file entry|cookies? file\b.*?:\s*[\'"]', re.I)
# A line with four or more tabs (raw or escaped as \t) is a cookies-file line when cookies are in use.
_TABS = re.compile(r'\\t|\t')
COOKIE_LINE_SKIPPED = msg('core.cookies.line_skipped')
COOKIE_LINE_FAILED = msg('core.cookies.line_failed')
# (1.8.0, D-H-1) The item's error for such a line: the text 1.7.0 stored (COOKIE_LINE_FAILED without its
# 'ERROR: '), now with a key of its own.
COOKIE_LINE_ITEM = msg('core.cookies.line_failed_item')


def _cookie_echo(line, cookies_in_use=True) -> bool:
    """Whether an engine output line may repeat a line of the cookies file."""
    if not isinstance(line, str):
        return False
    return bool(COOKIE_ECHO_PATTERN.search(line)) or (cookies_in_use and len(_TABS.findall(line)) >= 4)


def _entry_options(options) -> dict:
    """What an unfinished entry keeps of the options: no per-link maps (the entry has its own referer)."""
    data = options.to_dict()
    data.pop('referers', None)
    data.pop('archive_contexts', None)
    return data


def load_unfinished(log=None) -> list[dict]:
    """The entries of the unfinished list (see unfinished.py).  With ``log`` (the window's start, 2.0.0
    PLAN 3.10) a list file that gives no entry is set aside now and reported to ``log``."""
    return unfinished.load_at_start(log) if log is not None else unfinished.load()


def save_unfinished(entries):
    """Store the entries; an empty list removes the file.  Returns the path or None."""
    return unfinished.save(entries)


# One JSON line per finished media file, printed before the plain path line: the id, what the
# site calls the video, and where the file is.  The Runner renames the file when this arrives.
MEDIA_PRINT = 'after_move:__GUI_MEDIA__%(.{id,extractor_key,title,uploader,duration,webpage_url,filepath})j'
# "Artist - Song" in the title fills the artist and track tags when the site named no artist.
ARTIST_TRACK_RULE = r'%(artist|)s<>%(title)s:^<>(?P<artist>.+?)\s+-\s+(?P<track>.+)$'
SUBTITLE_LANGUAGES = 'zh.*,en.*,-live_chat'


# Windows starts no process whose command line is longer than 32767 characters; the Runner
# adds its seals (about 100 characters) and perhaps -k to what build_command returns.
COMMAND_LINE_LIMIT = 32000


class LinkTooLong(ValueError):
    """build_command: the link's command line would exceed COMMAND_LINE_LIMIT.  A ValueError like
    every other refusal, so the Runner fails this item alone; the download page lets such a link
    go to the Runner instead of refusing the whole job (final-merge, core request 2)."""
# The extensions of a cover picture the engine writes or converts beside the work name.
PICTURE_EXTENSIONS = ('.jpg', '.jpeg', '.webp', '.png')
# 'Title [id].jpg': a picture with the engine's work name (naming.OUTPUT_TEMPLATE).
_WORK_PICTURE = re.compile(r' \[([^\[\]]+)\]\.(?:jpe?g|webp|png)$', re.I)
# "[info] Writing video thumbnail 0 to: <path>" (also "playlist thumbnail").
_THUMBNAIL_WRITTEN = re.compile(r'^\[info\] Writing (?:video|playlist) thumbnail(?: \S+)? to: (.+?)\s*$')


def app_work_dir() -> Path:
    """The work folder beside the program, for an output folder on another drive than the state folder."""
    return Path(paths.APP_DIR) / 'data' / 'work'


def _is_link(path) -> bool:
    """A symbolic link or another reparse point (a junction): never used, never cleaned through."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(status.st_mode) or bool(getattr(status, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _usable_work(folder) -> bool:
    """Whether a work folder can be used: it is a real folder (or can be created below one)
    and a file can be written there.  Leaves nothing behind (the probe deletes itself);
    the folder itself is created when a download starts (Runner.download)."""
    try:
        folder = Path(folder)
        if not folder.is_absolute():
            return False
        existing = folder
        while not os.path.lexists(existing):
            if existing.parent == existing:
                return False
            existing = existing.parent
        if not existing.is_dir() or any(_is_link(part) for part in (folder, folder.parent) if os.path.lexists(part)):
            return False
        probe = existing / f'.ytdlp-probe-{os.getpid()}-{os.urandom(4).hex()}'
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_TEMPORARY', 0))
        os.close(descriptor)
        if not hasattr(os, 'O_TEMPORARY'):
            os.unlink(probe)
        return True
    except (OSError, ValueError):
        return False


def temp_path(options):
    """The work folder the engine downloads into before it moves the finished file, or None.

    Only for files that are renamed afterwards (naming.renames) and only for a work folder
    on the output folder's drive: then the move is a rename, and a half-finished file (and
    its cover picture) never shows up in the output folder.  The state folder's work folder
    when it is on that drive and can be created and written; else the one beside the program
    (APP_DIR\\data\\work) when it is on that drive and can be written; else none.
    """
    if not naming.renames(options.naming, options.playlist, options.archive_root):
        return None
    drive = Path(options.output).drive.lower()
    work = paths.work_dir()
    if drive == work.drive.lower() and _usable_work(work):
        return work
    work = app_work_dir()
    if drive and drive == work.drive.lower() and _usable_work(work):
        return work
    return None


def clean_app_work(folder=None, now=None) -> int:
    """Remove what stopped downloads left in the work folder beside the program; never raises.

    The same age rule as the state folder's work folder (paths.clean_work, final-architecture-1):
    a resumable partial download (*.part, *.ytdl, *.part-FragN), a finished format part
    ('Title [id].f137.mp4'), the original before audio extraction and any other plain file stay
    paths.WORK_MAX_AGE (7 days), so a stopped download that is retried within that time
    resumes; only ffmpeg's temporaries and cover pictures (paths.WORK_STRAYS) go after
    paths.WORK_STRAY_AGE.  Files directly inside the folder only, never the marker, never
    through a link, and only in a folder the app created itself (it carries paths.OWNED).
    The folder stays.  Returns the number of files removed.
    """
    try:
        folder = Path(folder or app_work_dir())
        if _is_link(folder) or not folder.is_dir() or not (folder / paths.OWNED).is_file():
            return 0
        return paths.clean_work(folder, now)
    except Exception:
        return 0


def build_command(url, options, temp=True):
    """The engine's argv for one link.  ``temp=False`` leaves out -P temp: (the Runner does that
    for a link whose unfinished download of 1.5.1 lies in the output folder, to resume it there)."""
    if options.mode not in MODES or options.resolution not in RESOLUTIONS:
        raise ValueError(msg('core.options.unsupported'))
    preset = formats.by_id(options.mode)
    command = [str(engine_path()), '--ignore-config', '--no-plugin-dirs', '--encoding', 'utf-8',
               '--ffmpeg-location', str(paths.BIN), '--js-runtimes', 'deno:' + str(paths.BIN / 'deno.exe'),
               '--newline', '--no-color', '--no-quiet', '--progress', '--progress-delta', '0.15', '--no-simulate',
               '--windows-filenames', '--trim-filenames', '180', '--no-overwrites', '--continue',
               '--socket-timeout', '20', '--retries', '5', '--fragment-retries', '5',
               '--abort-on-unavailable-fragments',
               '--concurrent-fragments', '1' if options.batch_safe else '3', '-P', str(options.output)]
    work = temp_path(options) if temp else None
    if work is not None:
        command += ['-P', 'temp:' + str(work)]
        if options.embed_cover and preset.cover:
            # The cover picture's final place is the work folder too: the engine then never takes
            # a picture of the owner's named '<work name>.jpg' in the output folder as the
            # cover, embeds it and deletes it (final-data-safety-2).
            command += ['-P', 'thumbnail:' + str(work)]
    command += ['-o', naming.OUTPUT_TEMPLATE,
                '--progress-template', 'download:__GUI_PROGRESS__%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(progress.status)s',
                '--print', 'before_dl:__GUI_TITLE__%(title)j',
                '--print', MEDIA_PRINT,
                '--print', 'after_move:__GUI_FILE__%(filepath)j']
    if options.archive_root:
        command += ['--print', 'after_move:__GUI_ARCHIVE__%(.{id,title,uploader,description,webpage_url,upload_date,filepath})j']
    command += ['--extractor-retries', '5',
                '--retry-sleep', 'http:exp=2:60', '--retry-sleep', 'fragment:exp=2:60',
                '--retry-sleep', 'extractor:exp=2:60', '--retry-sleep', 'file_access:exp=1:10']
    if options.batch_safe:
        # Between the entries of one playlist the engine itself waits; between links the Runner does.
        if options.playlist:
            command += ['--sleep-interval', '10', '--max-sleep-interval', '20']
        command += ['--sleep-requests', '1']
    if options.playlist:
        command += ['--yes-playlist', '--lazy-playlist']
    else:
        command += ['--no-playlist', '--playlist-items', '1']
    if options.proxy.strip():
        parsed = urllib.parse.urlsplit(options.proxy.strip())
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname:
            raise ValueError(msg('core.proxy.scheme'))
        command += ['--proxy', options.proxy.strip()]
    if options.cookies.strip():
        command += ['--cookies', str(check_cookie_file(options.cookies.strip()))]
    referer = options.referers.get(url, '')
    if referer:
        parsed_referer = urllib.parse.urlsplit(referer)
        if parsed_referer.scheme not in ('http', 'https') or not parsed_referer.hostname or any(c in referer for c in '\r\n'):
            raise ValueError(msg('core.collect.bad_source'))
        command += ['--referer', referer]
    command += formats.format_args(preset, options.resolution)
    if options.embed_metadata:
        # The two empty-field rules keep the long description out of the tags.
        command += ['--embed-metadata', '--parse-metadata', ':(?P<meta_synopsis>)',
                    '--parse-metadata', ':(?P<meta_description>)']
        if preset.kind == 'audio':
            command += ['--parse-metadata', ARTIST_TRACK_RULE]
    if options.embed_cover and preset.cover:
        command += ['--embed-thumbnail', '--convert-thumbnails', 'jpg']
    if options.subtitles and preset.kind == 'video':
        command += ['--embed-subs', '--sub-langs', SUBTITLE_LANGUAGES]
        if options.batch_safe:
            command += ['--sleep-subtitles', '1']
    command += ['--', url]
    if len(subprocess.list2cmdline(command)) > COMMAND_LINE_LIMIT:
        # Windows would refuse to start the engine (WinError 206), which looks like a broken
        # engine and stops the queue: refused here, this link fails alone (final-privacy-security-2).
        raise LinkTooLong(msg('core.links.too_long', length=len(url), url=urltools.display(url)))
    return command


def probe_args() -> list[str]:
    """Every option the app can hand to the engine, for the check of a freshly downloaded
    engine: all presets with every optional block switched on, then one plain single-link
    command (the flags that replace the playlist ones).  Repeated flags are harmless; only
    --cookies is missing, because it needs a file."""
    link, arguments = 'https://example.invalid/probe', []
    for preset in formats.PRESETS:
        arguments += build_command(link, Options(Path(tempfile.gettempdir()), preset.id, playlist=True, batch_safe=True,
                                                 subtitles=True, proxy='http://127.0.0.1:9',
                                                 referers={link: 'https://example.invalid/'}))[1:-2]
    arguments += build_command(link, Options(Path(tempfile.gettempdir())))[1:-2]
    # (1.7.0) The words the Runner adds for a playlist or archive run (_present_filters, a config
    # file for many of them: '-' reads the probe's empty stdin) and for an older file in another
    # format (_keeping_source).
    arguments += ['--match-filters', "id!~='^x$'", '--config-locations', '-', KEEP_SOURCE]
    return arguments


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
    if line.startswith('__GUI_MEDIA__'):
        try:
            value = json.loads(line[len('__GUI_MEDIA__'):])
            if isinstance(value, dict):
                return 'media', value
        except ValueError:
            pass
    return 'log', line


class QueueItem:
    """One link of a run.  ``state``: pending -> active -> done | failed | blocked | stopped |
    unstarted, or pending -> skipped | blocked without running; what is still pending when the
    run ends becomes unstarted.  ``ran`` is True once the engine was started for the item."""

    def __init__(self, index, url, options, origin=None):
        self.index = index
        self.url = url
        self.origin = origin or url
        self.options = options
        try:
            self.preset = formats.by_id(options.mode)
        except ValueError:
            self.preset = None      # build_command refuses the item when it is its turn
        # formats.format_key: the preset id, with '@<resolution>' for a video preset with a height limit.
        self.format = formats.format_key(self.preset, options.resolution) if self.preset else ''
        self.key = urltools.link_key(url, options.playlist)
        self.multi = urltools.multi(url, options.playlist)
        self.state = 'pending'
        self.ran = False
        self.error = ''
        self.reason = ''
        self.title = ''
        self.path = ''
        self.media_keys = []
        self.files = 0

    def signature(self):
        return self.key, self.format


def _masked(text, options) -> str:
    """Engine text that is kept (item_done.error, the unfinished list, the history) without the
    proxy address, which may carry a password.  (1.8.0, P8) messages.masked: the same replacements
    as 1.7.0, on the text and on the raw params of a message, whose type and key are kept."""
    text = str(text or '')
    proxy = options.proxy.strip() if options is not None else ''
    if proxy:
        pairs = [(proxy, messages.PROXY_TOKEN)]
        credentials = urllib.parse.urlsplit(proxy).netloc.rpartition('@')[0]
        if credentials:
            pairs.append((credentials + '@', ''))
        text = messages.masked(text, pairs)
    return text


def _safe_text(text):
    """A title as it can be stored: a lone UTF-16 surrogate (a title cut inside an emoji,
    which yt-dlp prints escaped) cannot be written as UTF-8 and becomes '?'."""
    return text.encode('utf-8', 'replace').decode('utf-8') if isinstance(text, str) else text


def _normal(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _same_content(first, second) -> bool:
    """Whether two files hold the same bytes (False when either cannot be read)."""
    try:
        if os.path.getsize(first) != os.path.getsize(second):
            return False
        with open(first, 'rb') as one, open(second, 'rb') as other:
            while True:
                block = one.read(1 << 20)
                if block != other.read(1 << 20):
                    return False
                if not block:
                    return True
    except OSError:
        return False


def _listing(folder) -> set:
    """The entries directly in a folder (normalised full paths); empty when it cannot be read."""
    try:
        with os.scandir(folder) as entries:
            return {_normal(entry.path) for entry in entries}
    except (OSError, ValueError):
        return set()


# The private markers of the engine's --print / --progress-template lines.  A run's own markers
# carry a random seal (see Runner._seal_command); a line that starts with an unsealed marker came
# from text the engine merely repeated (an HTTP reason phrase, a site's message) and is a log line.
MARKER = '__GUI_'


class _EngineOutput:
    """The engine's stdout as the reader thread iterates it: a line that starts with this run's
    sealed marker gets the plain marker parse_line knows; one that starts with a plain marker
    does not come from the engine's own --print and gets a leading space (a log line)."""

    def __init__(self, stream, seal):
        self._stream = stream
        self._seal = seal

    def __iter__(self):
        seal, length = self._seal, len(self._seal)
        buffer = getattr(self._stream, 'buffer', None)
        lines = self._lines(buffer) if buffer is not None else ((line, False) for line in self._stream)
        for line, continued in lines:
            if line.startswith(seal):
                yield MARKER + line[length:]
            elif line.startswith(MARKER):
                yield ' ' + line
            elif continued and (ALREADY_PATTERN.match(line.rstrip('\r\n')) or _THUMBNAIL_WRITTEN.match(line.rstrip('\r\n'))):
                # Text after a bare CR is not the start of an engine line (a server's reason
                # phrase can carry CRs): it never counts as the engine's "already downloaded".
                yield ' ' + line
            else:
                yield line

    @staticmethod
    def _lines(buffer):
        """The lines the text stream would give (LF, CR LF and a bare CR each end a line, as
        in universal-newline mode), each with whether it began after a bare CR inside one
        physical line.  Read from the binary stream, split at LF only, decoded per line (a
        UTF-8 sequence never contains an LF byte)."""
        for raw in iter(buffer.readline, b''):
            text = raw.decode('utf-8', 'replace')
            ended = text.endswith('\n')
            body = text[:-1] if ended else text
            if ended and body.endswith('\r'):
                body = body[:-1]
            pieces = body.split('\r')
            last = len(pieces) - 1
            for position, piece in enumerate(pieces):
                if position == last:
                    if ended:
                        yield piece + '\n', position > 0
                    elif piece:
                        yield piece, position > 0
                else:
                    yield piece + '\n', position > 0

    def close(self):
        self._stream.close()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _SafeArchive:
    """An Archive whose media() reports a database error like the archive's other errors (as
    OSError), so that a full disk or a locked history fails the archive record only, never the
    item or the queue.  Everything else is the archive's own."""

    def __init__(self, archive):
        self._archive = archive

    def media(self, *arguments, **keywords):
        try:
            return self._archive.media(*arguments, **keywords)
        except sqlite3.Error as error:
            raise OSError(msg('core.archive.unwritable', error=message_of(error))) from error

    def __getattr__(self, name):
        return getattr(self._archive, name)


# "[download] <path> has already been downloaded": the engine found the finished file and goes on
# to post-process it (tags, cover), which would change a file that was there before the item.
ALREADY_PATTERN = re.compile(r'^\[download\] (.+?) has already been downloaded(?: and merged)?\s*$')
# A partial download in the output folder, written without -P temp: (1.5.1, the title_id style):
# 'Title [id].ext.part', 'Title [id].f137.mp4.part', its .ytdl state file, fragments.
_PARTIAL_PATTERN = re.compile(r' \[([^\[\]]+)\]\.[^\\/]*?(?:\.part|\.ytdl|\.part-frag\d+)$', re.I)
# A format part of a merged download ('Title [id].f137.mp4'): an intermediate file of the engine,
# which merges it and removes it as usual.
_FORMAT_PART = re.compile(r'\]\.f[^.\\/\[\]]+\.[^.\\/]+$', re.I)
# What '--audio-format best' (audio_best) can leave as its finished file.
BEST_AUDIO_EXTENSIONS = ('.m4a', '.mp3', '.opus', '.ogg', '.oga', '.aac', '.flac', '.wav', '.alac', '.mka')
# --keep-video: the engine converts a file and leaves the original in place.
KEEP_SOURCE = '-k'
# A media file under the engine's work name ('Title [id].mp4') or one of its format parts
# ('Title [id].f137.mp4'); group 1 is the id, group 2 the extension.  Partial downloads
# ('….mp4.part') and pictures do not match the media extensions below.
_WORK_MEDIA = re.compile(r' \[([^\[\]]+)\]\.(?:f[^.\\/\[\]]+\.)?([^.\\/\[\]]+)$', re.I)
# What the engine can have downloaded or produced for a link (the sources of a conversion).
VIDEO_EXTENSIONS = ('.mp4', '.webm', '.mkv', '.mov', '.flv', '.avi', '.m4v', '.3gp', '.ts')
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + BEST_AUDIO_EXTENSIONS


def _final_extensions(preset) -> tuple:
    """The extensions a preset's finished file can have: '.mp3' for audio_mp3, '.mkv' for
    video_mkv, every audio extension for audio_best ('--audio-format' / '--remux-video')."""
    arguments = list(getattr(preset, 'args', ()) or ())
    for option in ('--audio-format', '--remux-video'):
        if option in arguments[:-1]:
            value = arguments[arguments.index(option) + 1]
            return BEST_AUDIO_EXTENSIONS if value == 'best' else ('.' + value.lower(),)
    return ()


def _is_final_file(path, preset) -> bool:
    """Whether a file the engine reports as already downloaded is the finished file the item
    asks for (the preset's extension, not a format part), not the source of a conversion."""
    name = os.path.basename(str(path))
    return not _FORMAT_PART.search(name) and os.path.splitext(name)[1].lower() in _final_extensions(preset)


def _keeping_source(command):
    """The command with -k before its '--' (the link stays last)."""
    command = list(command)
    if KEEP_SOURCE in command:
        return command
    position = command.index('--') if '--' in command else len(command)
    return command[:position] + [KEEP_SOURCE] + command[position:]


def _link_id(url) -> str:
    """The media id the engine will most likely use for a link: the YouTube video id, else the
    last part of the path without its extension (Bilibili BV id, Douyin number, the generic
    extractor's id).  '' when there is none."""
    video = urltools.video_id(url)
    if video:
        return video
    try:
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1])
    except (ValueError, AttributeError):
        return ''
    return os.path.splitext(name)[0].strip()


def _owner_pictures(listing, url, multi=False) -> list:
    """Pictures in the output folder (paths from _listing) that the engine could take as this
    link's cover when it writes the cover beside the media ('<work name>.jpg' and the other
    picture extensions): those whose [id] belongs to the link (as in _old_partials), and for a
    link that stands for several files or whose id cannot be guessed, every picture with a
    work name.  With --no-overwrites the engine would embed such a picture and then delete it,
    or overwrite it with a converted cover (final-data-safety-2)."""
    link_id = '' if multi else _link_id(url).lower()
    found = []
    for path in listing:
        match = _WORK_PICTURE.search(os.path.basename(path))
        if not match:
            continue
        tag = match.group(1).lower()
        if not link_id or tag == link_id or tag.startswith((link_id + '-', link_id + '_')):
            found.append(path)
    return sorted(found)


def _belongs_to(tag, link_id) -> bool:
    """Whether an [id] in a file name belongs to a link (the rule of _old_partials)."""
    tag = tag.lower()
    return bool(link_id) and (tag == link_id or tag.startswith((link_id + '-', link_id + '_')))


def _other_formats(listing, url, preset) -> list:
    """Files in the output folder (paths from _listing) that hold this link in another media
    format under the engine's work name, e.g. 'Title [id].mp4' from an earlier download when
    an MP3 is asked for now (final-regression-gate-1).  The engine takes such a file as already
    downloaded and converts it, and without -k it deletes it right after the conversion, often
    before the "has already been downloaded" line has been read and the engine could be
    stopped.  An item with such a file therefore runs with -k from the start.  The id rule is
    that of _old_partials; format parts, partial downloads and the preset's own extensions
    are not counted, nor audio files for a video preset (the engine never takes one as its
    download).  Empty when the id is unknown or the preset has no fixed extension."""
    finals = _final_extensions(preset)
    link_id = _link_id(url).lower()
    if not finals or not link_id:
        return []
    sources = VIDEO_EXTENSIONS if getattr(preset, 'kind', '') == 'video' else MEDIA_EXTENSIONS
    found = []
    for path in listing:
        name = os.path.basename(path)
        match = _WORK_MEDIA.search(name)
        if not match or _FORMAT_PART.search(name) or not _belongs_to(match.group(1), link_id):
            continue
        extension = '.' + match.group(2).lower()
        if extension in sources and extension not in finals:
            found.append(path)
    return sorted(found)


# A subtitle file the engine writes under the work name before it embeds it ('Title [id].en.vtt').
# Without -k the engine deletes it after embedding; with -k it stays (rv-findings-2-2).
_WORK_SUBTITLE = re.compile(r' \[([^\[\]]+)\]\.[^.\\/\[\]]+\.(?:vtt|srt|ass|ssa|ttml|srv[123]|json3|sbv|lrc|dfxp)$', re.I)
# (1.7.0, v-core-3-c3-stop-leaves-temp-mkv) What the engine's ffmpeg steps write beside a file they change
# and then rename onto it ('Title [id].temp.mkv' of the subtitle, tag and cover steps; 'Title [id].temp'
# for a file without an extension; the chapter list 'Title [id].meta').  A Stop or a failure while such a
# step runs leaves it.  Group 1 is the id.
_WORK_INTERMEDIATE = re.compile(r' \[([^\[\]]+)\]\.(?:temp(?:\.[^.\\/\[\]]+)?|meta)$', re.I)
# How many word characters of a file's title the match filter of a playlist or archive run compares
# (_present_filters): enough to tell two titles apart, short enough for many files.
SKELETON_CHARS = 12
# "[download] <title> does not pass filter (<filter>), skipping .." (the engine's line for an entry
# that _present_filters keeps from being processed again).
_FILTERED = re.compile(r'^\[download\] (.*?) does not pass filter \(.*\), skipping \.\.\s*$', re.S)


# (1.7.0 fix round 2, v-core-2-c5-audio-best-other-format) audio_best ('--audio-format best') keeps
# the site's audio as it is, so its finished file can have an extension that another preset's fixed
# conversion gives as well: '.mp3' (audio_mp3), '.flac' (audio_flac), '.wav' (audio_wav) and '.m4a'
# (audio_m4a).  Such a file is the one audio_best would take as already downloaded (or convert onto)
# only when the entry's chosen format is of that kind; with any other format the engine makes
# another file and never touches it (an MP3 of audio_mp3 when audio_best now gets the original
# AAC as '.m4a', an M4A of audio_m4a when audio_best gets Opus from a WebM).
BEST_SHARED_EXTENSIONS = ('.mp3', '.flac', '.wav', '.m4a')
# The chosen formats ('ext' of the entry's format, as the engine's match filter sees it before the
# download) that never hold AAC audio, so that audio_best never makes an '.m4a' from them.
_BEST_NO_AAC = ('webm', 'opus', 'ogg', 'oga', 'mp3', 'flac', 'wav')


def _keeps_source_audio(preset) -> bool:
    """Whether the preset is audio_best's kind: '--audio-format best'."""
    arguments = list(getattr(preset, 'args', ()) or ())
    return '--audio-format' in arguments[:-1] and arguments[arguments.index('--audio-format') + 1] == 'best'


def _present_media(folder, preset, shared=None) -> dict:
    """{id: [title, ...]} of the files directly in ``folder`` that are already this preset's finished
    file under the engine's work name ('Title [id].mp3' for audio_mp3; never a format part, a partial
    download or a link), with the names as the folder spells them.  {} when the folder cannot be
    read or the preset has no fixed extension.

    For audio_best, ``shared`` (a dict, when given) receives {(id, title): {extension, ...}} for the
    titles whose files there all have an extension of BEST_SHARED_EXTENSIONS: those count only for
    an entry whose chosen format could make them (_present_filters)."""
    finals = _final_extensions(preset)
    found = {}
    if not finals:
        return found
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for entry in entries if entry.is_file(follow_symlinks=False)]
    except (OSError, ValueError):
        return found
    extensions = {}
    for name in sorted(names):
        match = _WORK_MEDIA.search(name)
        extension = '.' + match.group(2).lower() if match else ''
        if not match or _FORMAT_PART.search(name) or extension not in finals:
            continue
        title = name[:match.start()]
        found.setdefault(match.group(1), [])
        if title not in found[match.group(1)]:
            found[match.group(1)].append(title)
        extensions.setdefault((match.group(1), title), set()).add(extension)
    if shared is not None and _keeps_source_audio(preset):
        for key, kinds in extensions.items():
            if kinds <= set(BEST_SHARED_EXTENSIONS):
                shared[key] = kinds
    return found


def _filter_text(text) -> str:
    """``text`` as a literal in a regular expression of a yt-dlp match filter: letters, digits and _
    as they are, everything else as a \\uXXXX escape, so that the filter holds no quote, no & and
    no space (the characters the engine's filter syntax gives a meaning)."""
    return ''.join(character if character.isascii() and (character.isalnum() or character == '_')
                   else ('\\u%04x' % ord(character) if ord(character) < 0x10000 else '\\U%08x' % ord(character))
                   for character in str(text))


# What may stand between two characters of a skeleton: anything that is not a letter or a digit.
# The underscore is part of it because the engine writes one for characters it replaces in a file
# name ('10:30' becomes '10_30'), so an underscore of the file name may stand for a colon of the
# site's title (1.7.0 fix round 1).
SKELETON_GAP = '[\\W_]*'


def _skeleton(title) -> str:
    """The pattern of a file's title for the match filter: its first SKELETON_CHARS letters and
    digits with anything else allowed between them (SKELETON_GAP).  The engine's file name keeps
    every letter and digit of the site's title in order (it only replaces or drops the others, a
    colon of a time by '_', and cuts the end), so the site's title of the same media always starts
    with this pattern."""
    characters = [character for character in str(title)
                  if character != '_' and re.match(r'\w', character)][:SKELETON_CHARS]
    return SKELETON_GAP.join(_filter_text(character) for character in characters)


def _title_test(titles) -> str:
    """The match-filter test "the title is none of these files' titles" (_skeleton).  It ignores
    letter case, as Windows file names do: the engine finds 'my song [id].mp3' under the site's new
    spelling 'My Song' (1.7.0 fix round 2, v-core-2-c5-case-only-title-change).  Python wants a
    global inline flag at the very start of the pattern."""
    return f"title!~='(?i)^{SKELETON_GAP}(?:" + '|'.join(sorted({_skeleton(title) for title in titles})) + ")'"


def _format_test(extensions) -> str:
    """The match-filter test on the entry's chosen format ('ext') under which audio_best leaves
    files of ``extensions`` (BEST_SHARED_EXTENSIONS) alone: for '.mp3', '.flac' and '.wav' any
    format but that one; with '.m4a' only a format that never holds AAC (_BEST_NO_AAC) and is none
    of the others.  '' when no format is safe."""
    if '.m4a' in extensions:
        allowed = [kind for kind in _BEST_NO_AAC if '.' + kind not in extensions]
        return "ext~='^(?:" + '|'.join(allowed) + ")$'" if allowed else ''
    return "ext!~='^(?:" + '|'.join(sorted(extension[1:] for extension in extensions)) + ")$'"


def _present_filters(present, shared=None) -> list:
    """--match-filters arguments that keep the engine away from the media of ``present``
    (_present_media) in a playlist or archive run (final-data-safety-1): an entry whose id is not
    there passes the first filter; an entry with such an id passes only when its title is another
    one (then the engine would not find the file either and downloads it under the new name).  An
    entry that passes no filter is skipped before the engine touches anything of it: no download, no
    tags, no cover, no subtitles.

    ``shared`` (from _present_media, audio_best only): an entry whose title matches only such files
    also passes when its chosen format is one with which audio_best makes another file (_format_test);
    the engine checks that after it has chosen the format and before anything is downloaded or
    written.  Where an id has both kinds, the format test applies to any title but those of the
    other files, which errs towards leaving a file alone."""
    shared = shared or {}
    ids = sorted(present)
    words = ['--match-filters', "id!~='^(?:" + '|'.join(_filter_text(media_id) for media_id in ids) + ")$'"]
    for media_id in ids:
        same_id = f"id~='^{_filter_text(media_id)}$'"
        words += ['--match-filters', f"{same_id}&{_title_test(present[media_id])}"]
        loose = [title for title in present[media_id] if (media_id, title) in shared]
        if not loose:
            continue
        test = _format_test(set().union(*(shared[(media_id, title)] for title in loose)))
        firm = [title for title in present[media_id] if title not in loose]
        if test:
            words += ['--match-filters', same_id + (f"&{_title_test(firm)}" if firm else '') + '&' + test]
    return words


# The config file that carries the filters of a playlist or archive run when they do not fit on the
# command line (1.7.0 fix round 1): the engine reads it through --config-locations, which
# --ignore-config still honours.
FILTER_CONFIG = 'filters.conf'


def _filter_config(words, folder):
    """Write ``words`` (_present_filters) as a yt-dlp config file in ``folder``; returns the engine
    arguments that load it, or None when the file cannot be written or its path would be changed by
    the engine (it expands '~', %NAME% and $NAME in a config location).  One option per line, each
    word quoted for the engine's shlex reading; the words are ASCII (_filter_text)."""
    path = os.path.abspath(os.path.join(str(folder), FILTER_CONFIG))
    if '%' in path or '$' in path or os.path.expandvars(os.path.expanduser(path)) != path:
        return None     # (the engine's own environment may define other names than this process)
    lines = ['# coding: utf-8']
    for position in range(0, len(words), 2):
        lines.append(' '.join(shlex.quote(word) for word in words[position:position + 2]))
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('\n'.join(lines) + '\n')
    except (OSError, ValueError):
        return None
    return ['--config-locations', path]


def _with_filters(command, words):
    """The command with ``words`` before its '--' (the link stays last)."""
    command = list(command)
    position = command.index('--') if '--' in command else len(command)
    return command[:position] + list(words) + command[position:]


def _source_line(name) -> str:
    """The log line for an older file of the link in another format that the item converts."""
    return msg('core.engine.other_format_source', name=name)


def _sentence(error) -> str:
    """An error's text without its own closing full stop (Windows' messages end in '。'),
    for a log line that adds its own.

    (1.8.0 WP-6, MESSAGES.md 15 A11.5) An error that carries a Message keeps it: its text is cut
    before the closing stop with cut(), which keeps the key (en shows the whole message), instead of
    a str method that would flatten it.  The zh text is the 1.7.0 one either way."""
    text = message_of(error)
    if isinstance(text, messages.Message):
        return cut(text, len(messages.zh(text).rstrip().rstrip('。.．').rstrip()))
    return str(error).rstrip().rstrip('。.．').rstrip()


def _shown_name(normalised) -> str:
    """The name of a file as the folder spells it (_listing gives lower-cased paths)."""
    try:
        with os.scandir(os.path.dirname(normalised)) as entries:
            for entry in entries:
                if _normal(entry.path) == normalised:
                    return entry.name
    except (OSError, ValueError):
        pass
    return os.path.basename(normalised)


def _old_partials(listing, url) -> list:
    """Partial downloads of this link in the output folder (paths from _listing)."""
    link_id = _link_id(url).lower()
    if not link_id:
        return []
    found = []
    for path in listing:
        match = _PARTIAL_PATTERN.search(os.path.basename(path))
        if not match:
            continue
        tag = match.group(1).lower()
        if tag == link_id or tag.startswith((link_id + '-', link_id + '_')):
            found.append(path)
    return sorted(found)


class Runner:
    # time.monotonic() when the last download of this program run ended successfully; shared by
    # all runners, so jobs started one right after the other are spaced like the items of one job.
    last_media_end = None

    @staticmethod
    def reset_pacing():
        """Forget the last download: the next item starts without a spacing wait."""
        Runner.last_media_end = None

    def __init__(self, emit, library=None):
        self.emit = emit
        self.cancelled = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.bot_check = False
        self.library = library
        self.archive = None
        self.archive_folder = None
        self.archive_errors = []
        self.last_error = ''
        self.job = procutil.JobObject()
        # What the output of the running item told (reset per item by download()).
        self.stale = False
        self.engine_error = False
        self.subs_failed = False
        self.wait_kind = 'rate_limit'
        self._renamed = {}
        self._current = None
        self._intercept_logged = False
        # Per item: the engine's work folder (temp_path) and what was in the output folder
        # before the item started (a file found there is never renamed).
        self._work = None
        self._existing = set()
        self._cleaned = set()
        # Per item: the cover pictures the engine wrote for it (its "Writing video thumbnail ... to:"
        # lines) in the output or work folder; what is left of them when the item ends is removed.
        self._covers = []
        self._cover_folders = set()
        # A file that was already in the output folder and that the engine reported as already
        # downloaded (the engine is stopped before it changes it).
        self._kept = None
        # A file of another format of the link that was already in the output folder (the
        # source of a conversion, e.g. an older MP4 before audio extraction): the engine is
        # stopped before it can convert and delete it, and the item runs again with -k.
        self._source = None
        self._keep_source = False
        # Whether the item runs with -k from the start because such a file was in the output
        # folder before it began (_other_formats): the engine is then not stopped for it.
        self._keep_early = False
        # Whether the engine's own sealed before_dl line (the title) came for the running item:
        # its "already downloaded" line comes after it, never before.
        self._at_download = False
        # Per item (1.7.0): the HTTP 429 retries used so far, shared by the item's main run and its
        # reruns (_cooled); the media ids the engine reported (media lines), for the clean-up of
        # what -k kept (_discard_subtitles); the files that were the preset's finished files in the
        # output folder before a playlist or archive item and that the engine was kept away from
        # (_present_media, _present_filters).
        self._rate_attempt = 0
        self._media_ids = []
        self._present = {}
        self._filters = []
        # The run folder of the item's filter config file when its filters did not fit on the
        # command line (1.7.0 fix round 1, _skip_present / _drop_filter_config).
        self._filter_dir = None
        # This runner's seal for the private output markers (see _seal_command).
        self._seal = '__GUI' + os.urandom(8).hex() + '_'
        # The queue.  queue_lock guards items, inbox, opened and closed, nothing else; nothing is
        # emitted while it is held.  (self.lock guards self.process only: it is held while a
        # process starts, and enqueue() is called from the window thread.)
        self.queue_lock = threading.Lock()
        self.items = []
        self.inbox = []
        self.opened = False
        self.closed = False
        self.base_options = None
        self.blocked = False     # a YouTube bot check was met: the other YouTube links of this run wait
        self.origins = {}        # cleaned link -> the spelling the user gave; set by the caller
        self._archives = {}

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            process = self.process
        procutil.terminate_tree(process, self.job)
        if process is not None and process.poll() is not None:
            # The engine itself has ended, but something it started may still hold its output
            # pipe open (the reader would wait for it forever): the job ends all of them.
            self.job.terminate()

    _terminate_process = staticmethod(procutil.terminate_tree)

    def _seal_command(self, command):
        """The command with this runner's seal in its private markers (the arguments of --print
        and --progress-template only), and whether there were any.  Text that the engine
        merely repeats (a server's reason phrase with a CR in it, a site's error message with a
        line break) can then never pass for one of its own marker lines."""
        sealed, changed = list(command), False
        for position in range(1, len(sealed)):
            if sealed[position - 1] == '--':
                break
            if sealed[position - 1] in ('--print', '--progress-template') and isinstance(sealed[position], str) \
                    and MARKER in sealed[position]:
                sealed[position] = sealed[position].replace(MARKER, self._seal)
                changed = True
        return (sealed, True) if changed else (command, False)

    def _stop_engine(self):
        """End the running engine and what it started (from the loop's own thread)."""
        with self.lock:
            process = self.process
        procutil.terminate_tree(process, self.job)

    def _wait(self, seconds, index, total, reason):
        deadline = time.monotonic() + seconds
        self.emit('log', msg('core.engine.waiting', reason=reason, seconds=math.ceil(seconds)))
        while not self.cancelled.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.emit('resuming', {'index': index, 'total': total})
                return True
            self.emit('waiting', {'index': index, 'total': total,
                                  'seconds': math.ceil(remaining), 'reason': reason,
                                  'kind': getattr(self, 'wait_kind', 'rate_limit')})
            self.cancelled.wait(min(1, remaining))
        return False

    def _intercept(self, kind, value):
        """Called for every parsed output line before the loop of _run_once uses it.

        media: the finished file gets its final name (naming.finalize) and the event carries
        that path and the item's index.  file: the path of the same file as renamed.
        Everything else passes through unchanged.  Never raises.

        A file that was in the output folder before the item started is never renamed: the
        engine reports such a file ("has already been downloaded") when an earlier download
        (1.5.1, a playlist run, the title_id style) left it under the work name.  For a single
        link such a line counts only after the item's sealed title line and only when it is at
        the start of an engine line (see _EngineOutput): when it names the item's own finished
        file, the engine is stopped and the file is reported as it is (_kept); when it names
        another format of the link (the source of a conversion), the item already runs with -k
        when download() saw that file beforehand (_other_formats, _keep_early) and the engine
        goes on; otherwise (an id that differs from the link's) the engine is stopped before it
        can convert and delete it, and download() runs the item again with -k (_source).
        When the engine downloaded anyway and left its new file in the work folder, an
        identical copy is discarded and a different one is placed beside the old file under
        the collision rule.
        """
        def say(text):
            try:
                self.emit('log', text)
            except Exception:
                pass

        try:
            item = self._current
            if kind == 'log' and isinstance(value, str):
                if _cookie_echo(value, self._uses_cookies()):
                    # Never the line itself: it repeats a line of the cookies file.
                    return 'log', COOKIE_LINE_FAILED if value.startswith('ERROR:') else COOKIE_LINE_SKIPPED
                filtered = _FILTERED.match(value)
                if filtered and self._present:
                    # An entry whose finished file was there before the item (_present_filters): the
                    # engine's line repeats the whole filter; one short line instead.
                    return 'log', msg('core.engine.present_skipped', name=filtered.group(1))
                written = _THUMBNAIL_WRITTEN.match(value)
                if written and _normal(Path(written.group(1)).parent) in self._cover_folders:
                    self._covers.append(written.group(1))     # removed when the item ends (_discard_covers)
                match = ALREADY_PATTERN.match(value)
                if match and item is not None and not item.multi and not item.options.archive_root \
                        and self._kept is None and self._at_download \
                        and _normal(match.group(1)) in self._existing and os.path.isfile(match.group(1)):
                    found = match.group(1)
                    if _is_final_file(found, item.preset):
                        # The item's own finished file was there before this item: the engine
                        # would now add tags and the cover to it in place.  It is stopped first;
                        # the file stays as it was.
                        self._kept = found
                        self._stop_engine()
                        say(msg('core.engine.kept_existing', name=Path(found).name))
                    elif self._source is None and not _FORMAT_PART.search(Path(found).name) \
                            and (self._keep_early or not self._keep_source):
                        # Another format of this link (an older MP4 before audio extraction or
                        # remuxing): the engine would convert it and delete it.
                        self._source = found
                        if self._keep_early:
                            # Already running with -k: it converts the file and leaves it.
                            say(_source_line(Path(found).name))
                        else:
                            # Stopped first; download() runs the item once more with -k.
                            self._stop_engine()
                return kind, value
            if kind == 'media' and isinstance(value, dict) and isinstance(value.get('filepath'), str) \
                    and value.get('id') not in (None, ''):
                filepath = final = value['filepath']
                if str(value['id']) not in self._media_ids:
                    self._media_ids.append(str(value['id']))
                options = item.options if item is not None else None
                if options is not None and naming.renames(options.naming, options.playlist, options.archive_root):
                    source, folder = filepath, None
                    existed = _normal(filepath) in self._existing
                    work = self._work
                    if work is not None:
                        # A file with the work name was already in the output folder and the engine
                        # downloaded anyway (a format it converts after the download): it then leaves
                        # the new file in the work folder and reports the old one.  The old file is
                        # not ours to rename; the new one is fetched and placed beside it.
                        waiting = Path(work) / Path(filepath).name
                        if waiting != Path(filepath) and waiting.is_file():
                            source, folder = str(waiting), Path(filepath).parent
                    if existed and folder is not None and _same_content(source, filepath):
                        # The new copy is byte for byte the file that is already there: not a second copy.
                        try:
                            os.remove(source)
                            folder = None
                        except OSError:
                            pass
                    if existed and folder is None:
                        say(msg('core.engine.kept_existing', name=Path(filepath).name))
                    else:
                        final = naming.finalize(source, value['id'], options.naming,
                                                audio=item.preset is None or item.preset.kind == 'audio',
                                                log=say, folder=folder)
                        if final == source and folder is not None:
                            final = filepath     # it could not be fetched: report what the engine reported
                self._renamed[filepath] = final
                if item is not None:
                    if isinstance(value.get('title'), str) and value['title']:
                        item.title = _safe_text(value['title'])
                    item.path = final
                    extractor = value.get('extractor_key')
                    if isinstance(extractor, str) and extractor and extractor.lower() != 'generic':
                        key = f'{extractor.lower()}:{value["id"]}'
                        if key not in item.media_keys:
                            item.media_keys.append(key)
                return 'media', dict(value, filepath=final, index=item.index if item is not None else None)
            if kind == 'file' and isinstance(value, str):
                return 'file', self._renamed.pop(value, value)
            if kind == 'title' and isinstance(value, str) and item is not None:
                self._at_download = True
                if not item.title:
                    item.title = _safe_text(value)
        except Exception:
            if not self._intercept_logged:
                self._intercept_logged = True
                applog.get('engine').exception('output line could not be processed')
        return kind, value

    def _uses_cookies(self) -> bool:
        item = self._current
        return bool(item is not None and getattr(item.options, 'cookies', '').strip())

    def _note_line(self, line):
        """Called for every plain output line of the engine (after the bot-check test)."""
        if line.startswith('ERROR:'):
            if _cookie_echo(line, self._uses_cookies()):
                line = COOKIE_LINE_FAILED       # the item's error never repeats a cookies-file line
                self.last_error = COOKIE_LINE_ITEM      # (1.8.0, D-H-1) the 1.7.0 text, with its key
            else:
                self.last_error = messages.raw(line[6:].strip()[:200])     # (1.8.0) raw engine text
        if STALE_PATTERN.search(line) and not _TRANSPORT_FAILURE.search(line):
            self.stale = True
        if ENGINE_OPTION_ERROR in line:
            self.engine_error = True
        if SUBTITLE_FAILURE in line:
            self.subs_failed = True

    def _run_once(self, command, index, total, files):
        """Read output off-thread so UI countdowns and cancellation keep moving."""
        command, sealed = self._seal_command(command)
        with self.lock:
            if self.cancelled.is_set():
                return -1, False, None
            # The engine unpacks itself into TEMP; a private folder per run can be removed
            # afterwards even when the process tree had to be killed.
            run_tmp = None
            try:
                run_tmp = str(paths.run_dir(paths.tmp_dir()))
            except OSError:
                run_tmp = None
            environment = process_environment()
            if run_tmp:
                environment.update(TEMP=run_tmp, TMP=run_tmp)
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL,
                                           env=environment, cwd=str(paths.BIN), creationflags=HIDDEN)
            except BaseException:
                if run_tmp:
                    shutil.rmtree(run_tmp, ignore_errors=True)
                raise
            self.job.assign(process)
            if sealed:
                process.stdout = _EngineOutput(process.stdout, self._seal)
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
                                              'reason': WAIT_PACE_OR_RETRY})
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
                kind, value = self._intercept(kind, value)
                if kind == 'file' and isinstance(value, str) and value not in files:
                    files.append(value)
                if kind == 'archive' and getattr(self, 'archive', None):
                    try:
                        self.archive.media(self.archive_folder, value)
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        # Media success is retained, but the archive failure is surfaced separately.
                        self.emit('log', msg('core.engine.archive_info_failed', error=message_of(error)))
                        self.archive_errors.append(str(error))
                self.emit(kind, value)
                if kind == 'log':
                    if BOT_CHECK_PATTERN.search(line):
                        self.bot_check = True
                    self._note_line(line)
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
            # Also reached when emit raises: the child must not outlive this call.
            if process.poll() is None:
                procutil.terminate_tree(process, self.job)
            reader.join(2)
            try:
                process.stdout.close()
            except (OSError, ValueError):
                pass
            with self.lock:
                if self.process is process:
                    self.process = None
            if run_tmp:
                shutil.rmtree(run_tmp, ignore_errors=True)

    def enqueue(self, urls, options=None, overrides=None):
        """Add links to the running queue; returns the accepted ones, in order.

        Callable from any thread; never emits.  [] unless the run is open (its items are built,
        the queue is not closed and Stop was not pressed): the caller then starts a new job.  A
        link whose (link key, format) is waiting, running or finished in this run is not taken
        again; one that failed, was stopped, blocked or skipped may be queued once more.
        """
        accepted = []
        with self.queue_lock:
            if not self.opened or self.closed or self.cancelled.is_set():
                return []
            base = options or self.base_options
            taken = {item.signature() for item in self.items + self.inbox if item.state in ('pending', 'active', 'done')}
            for url in urls:
                item = QueueItem(len(self.items) + len(self.inbox) + 1, url, (overrides or {}).get(url, base),
                                 self.origins.get(url))
                if item.signature() in taken:
                    continue
                taken.add(item.signature())
                self.inbox.append(item)
                accepted.append(url)
        return accepted

    def _queued(self, moved, total):
        """The payload of a queued event for items that left the inbox.

        'urls' and 'overrides' (keyed by link) as in 1.6.0; 'items' (1.7.0, additive, q-minor-2)
        holds one entry per moved item in queue order, {'url', 'format'} plus 'options' when they
        differ from the run's, so that the same link in two formats keeps both in the history
        record (Library.download_event).  'overrides' alone keeps only the last format of a link."""
        payload = {'urls': [item.url for item in moved], 'first_index': moved[0].index, 'total': total}
        base = self.base_options.to_dict()
        different = {item.url: item.options.to_dict() for item in moved if item.options.to_dict() != base}
        if different:
            payload['overrides'] = different
        entries = []
        for item in moved:
            entry = {'url': item.url, 'format': item.format}
            own = item.options.to_dict()
            if own != base:
                entry['options'] = own
            entries.append(entry)
        payload['items'] = entries
        return payload

    def _next(self):
        """The next item to run (marked active), or None when the queue is finished or stopped
        (then it is closed).  Links accepted by enqueue() join the queue here."""
        with self.queue_lock:
            moved = self.inbox[:]
            self.items += moved
            self.inbox.clear()
            item = None
            if not self.cancelled.is_set():
                item = next((entry for entry in self.items if entry.state == 'pending'), None)
            if item is None:
                self.closed = True
            else:
                item.state = 'active'
            total = len(self.items)
        if moved:
            self.emit('queued', self._queued(moved, total))
        return item

    def _discard_cover(self, kept):
        """Remove the cover picture the engine fetched for a file it was then stopped from
        changing ('<work name>.jpg' and the like): in the work folder, and in the output folder
        only when it was not there before the item.  Never raises."""
        stem = Path(kept).stem
        for folder in (Path(kept).parent, self._work):
            if folder is None:
                continue
            for extension in ('.jpg', '.jpeg', '.webp', '.png'):
                picture = Path(folder) / (stem + extension)
                try:
                    if picture.is_file() and not _is_link(picture) and (
                            folder == self._work or _normal(picture) not in self._existing):
                        os.remove(picture)
                except OSError:
                    pass

    def _discard_covers(self):
        """Remove what is left of the cover pictures the engine wrote for the item that ended
        (a stopped or failed download, a run with -k): '<work name>.jpg' and its converted
        forms beside the written one, in the output or work folder only, and only pictures that
        were not there before the item (self._existing).  1.5.1 never left a picture behind,
        and a picture left in the output folder would keep the next run of the link from
        embedding its cover (_owner_pictures).  Never raises."""
        covers, self._covers = self._covers, []
        for written in covers:
            try:
                written = Path(written)
                if _normal(written.parent) not in self._cover_folders:
                    continue
                for extension in PICTURE_EXTENSIONS:
                    picture = written.parent / (written.stem + extension)
                    if _normal(picture) not in self._existing and picture.is_file() and not _is_link(picture):
                        os.remove(picture)
            except (OSError, ValueError):
                continue

    def _discard_kept(self, item, folder, files):
        """Remove what -k kept of a new download for an item that ran with -k from the start
        (_keep_early) but for which the engine downloaded afresh instead of converting the older
        file: the downloaded source and its format parts in the output folder, which the engine
        itself deletes without -k.  Only files that were not there before the item
        (self._existing), that carry this link's id, that are not the item's reported file and
        not one of the preset's own formats.  Never raises."""
        link_id = _link_id(item.url).lower()
        finals = _final_extensions(item.preset)
        reported = {_normal(path) for path in files}
        if item.path:
            reported.add(_normal(item.path))
        try:
            entries = sorted(_listing(folder))
        except (OSError, ValueError):
            return
        for path in entries:
            try:
                name = os.path.basename(path)
                match = _WORK_MEDIA.search(name)
                if not match or path in self._existing or path in reported or not _belongs_to(match.group(1), link_id):
                    continue
                extension = '.' + match.group(2).lower()
                if extension not in MEDIA_EXTENSIONS or (extension in finals and not _FORMAT_PART.search(name)):
                    continue
                if os.path.isfile(path) and not _is_link(path):
                    os.remove(path)
            except (OSError, ValueError):
                continue

    def _discard_subtitles(self, item, folder):
        """Remove the subtitle files an item that ran with -k left after embedding them
        (rv-findings-2-2): '<work name>.<language>.vtt' and the other subtitle formats in the
        output folder and the work folder, which the engine itself deletes without -k.  Only files
        that were not in the output folder before the item (self._existing) and whose [id] is
        this link's (_link_id), the id of the older file the item converted (self._source) or an
        id the engine reported for the item (self._media_ids).  Never through a link; never raises."""
        ids, link_id = self._own_ids(item)
        for place in dict.fromkeys(str(entry) for entry in (folder, self._work) if entry is not None):
            try:
                entries = sorted(_listing(place))
            except (OSError, ValueError):
                continue
            for path in entries:
                try:
                    match = _WORK_SUBTITLE.search(os.path.basename(path))
                    if not match or path in self._existing:
                        continue
                    tag = match.group(1).lower()
                    if tag not in ids and not _belongs_to(tag, link_id):
                        continue
                    if os.path.isfile(path) and not _is_link(path):
                        os.remove(path)
                except (OSError, ValueError):
                    continue

    def _own_ids(self, item):
        """The ids that mark the files of the running item (the rule of _discard_subtitles): the set
        of the ids the engine reported for it (self._media_ids) and the id of the older file it
        converted (self._source), all lower case, and the link's own id (_link_id, for _belongs_to)."""
        ids = {media_id.lower() for media_id in self._media_ids if media_id}
        if self._source:
            match = _WORK_MEDIA.search(os.path.basename(self._source))
            if match:
                ids.add(match.group(1).lower())
        return ids, _link_id(item.url).lower()

    def _discard_intermediates(self, item, folder):
        """(1.7.0, v-core-3-c3-stop-leaves-temp-mkv) Remove the intermediate files of the engine's
        ffmpeg steps that the item left in the output folder (_WORK_INTERMEDIATE: '<work name>.temp.mkv'
        and the like).  An item with -k converts the older file of its link in place and an item
        without a work folder post-processes beside its file, so those steps run in the owner's folder,
        and a Stop or a failure while one of them runs leaves its file there for good.  Called whatever
        the outcome.  Only files that were not in the output folder before the item (self._existing) and
        whose [id] is the item's (_own_ids, the rule of _discard_subtitles); the work folder is the
        app's own (clean_app_work).  Never through a link; never raises."""
        try:
            ids, link_id = self._own_ids(item)
            entries = sorted(_listing(folder))
        except (OSError, ValueError, TypeError):
            return
        for path in entries:
            try:
                match = _WORK_INTERMEDIATE.search(os.path.basename(path))
                if not match or path in self._existing:
                    continue
                tag = match.group(1).lower()
                if tag not in ids and not _belongs_to(tag, link_id):
                    continue
                if os.path.isfile(path) and not _is_link(path):
                    os.remove(path)
            except (OSError, ValueError):
                continue

    def _discard_unfinished(self, item, files):
        """(1.7.0, v-core-3-c3-stop-leaves-temp-mkv) For an item with -k that converted the older file
        of its link in place (self._source) and then did not deliver (a Stop or a failure): remove the
        preset's file the conversion had begun beside the older file, e.g. 'Title [id].mkv' beside
        'Title [id].mp4', remuxed but without the subtitles, tags or cover still to come, or cut short.
        Left there, the next run of the link would take it as the finished file and keep it as it is
        (_kept).  Only the names the conversion gives (the older file's name with the preset's
        extensions), only files that were not in the output folder before the item (self._existing)
        and that the item did not report.  Never through a link; never raises."""
        try:
            source = Path(self._source)
            reported = {_normal(path) for path in files}
            if item.path:
                reported.add(_normal(item.path))
            for extension in _final_extensions(item.preset):
                made = source.with_suffix(extension)
                key = _normal(made)
                if key == _normal(source) or key in self._existing or key in reported:
                    continue
                try:
                    if made.is_file() and not _is_link(made):
                        os.remove(made)
                except OSError:
                    continue
        except (OSError, ValueError, TypeError):
            return

    def _cooled(self, command, index, files):
        """_run_once(command) with the queue's HTTP 429 handling: the attempt loop of 1.6.0's main
        path, moved here unchanged (c3-retry-ratelimit) so that the item's reruns (with -k, without
        subtitles) go through it too.  A 429 cools down and runs the command again, at most
        MAX_RATE_LIMIT_RETRIES times per item (self._rate_attempt, shared by the main run and the
        reruns, reset per item); when they are used up the item fails and the whole queue pauses
        for the cooldown if anything still waits, so the next item never starts before it ends.
        Stop ends a wait at once.  A subtitle 429 is no rate limit of the queue (RATE_LIMIT_PATTERN).
        Returns (result, start_failed); result is None when nothing ran (Stop came first, or the
        engine could not be started)."""
        result, start_failed = None, False
        while not self.cancelled.is_set():
            try:
                result, limited, retry_after = self._run_once(command, index, len(self.items), files)
            except OSError as error:
                self.engine_error = start_failed = True
                self.emit('log', msg('core.engine.cannot_start_engine', error=_sentence(error)))
                break
            if not limited or self.cancelled.is_set():
                break
            cooldown = min(RATE_LIMIT_COOLDOWN * 2 ** self._rate_attempt, MAX_RATE_LIMIT_COOLDOWN)
            # A visible server Retry-After may be longer than our normal cap.
            cooldown = max(cooldown, retry_after or 0)
            if self._rate_attempt >= MAX_RATE_LIMIT_RETRIES:
                result = result or 1
                self.emit('log', msg('core.engine.rate_limited_out'))
                # The whole queue pauses when anything still waits: a later item, or a
                # link that enqueue() accepted meanwhile (still in the inbox).
                with self.queue_lock:
                    more = bool(self.inbox) or any(entry.state == 'pending' for entry in self.items)
                if more:
                    self._wait(cooldown, index, len(self.items), msg('core.wait.rate_queue'))
                break
            self._rate_attempt += 1
            if not self._wait(cooldown, index, len(self.items),
                              msg('core.wait.rate_retry', attempt=self._rate_attempt, limit=MAX_RATE_LIMIT_RETRIES)):
                break
        return result, start_failed

    def _skip_present(self, item, effective, command):
        """For a playlist or archive item: keep the engine away from what was already in the output
        folder as this preset's finished file (final-data-safety-1).  Such a run of 1.6.0 took each
        of those files as "already downloaded" and wrote tags, the cover and subtitles into it in
        place.  Returns (effective, command): the command with _present_filters (on the command
        line, or, when they do not fit there, from a config file, _filter_config), or, only when
        that file cannot be written either, the item without tags, cover and subtitles (the engine
        then leaves a file that is there as it is, as 1.5.1 did)."""
        shared = {}
        self._present = _present_media(effective.output, item.preset, shared)
        if not self._present:
            return effective, command
        words = _present_filters(self._present, shared)
        filtered = _with_filters(command, words)
        if len(subprocess.list2cmdline(filtered)) <= COMMAND_LINE_LIMIT:
            self._filters = words
            return effective, filtered
        # (1.7.0 fix round 1) Too many files for the command line (a large music folder): the same
        # filters from a config file in a private run folder, removed after the item
        # (_drop_filter_config; a folder left by a killed run goes with paths.purge_stale).
        try:
            self._filter_dir = paths.run_dir(paths.tmp_dir())
        except OSError:
            self._filter_dir = None
        loaded = _filter_config(words, self._filter_dir) if self._filter_dir is not None else None
        if loaded:
            filtered = _with_filters(command, loaded)
            if len(subprocess.list2cmdline(filtered)) <= COMMAND_LINE_LIMIT:
                self._filters = loaded
                return effective, filtered
        self._drop_filter_config()
        plain = replace(effective, embed_metadata=False, embed_cover=False, subtitles=False)
        command = build_command(item.url, plain) if self._work == temp_path(plain) \
            else build_command(item.url, plain, temp=False)
        self.emit('log', msg('core.engine.present_untagged', count=sum(len(titles) for titles in self._present.values())))
        return plain, command

    def _drop_filter_config(self):
        """Remove the run folder of _skip_present's config file, if the item had one; never raises."""
        folder, self._filter_dir = getattr(self, '_filter_dir', None), None
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)

    def _present_done(self, item, effective, files, first_file):
        """After a playlist or archive item that ended well: the files _skip_present kept the engine
        away from stay in its archive record (Archive.media adds a file that is missing there, as
        the engine's own line did in 1.6.0), and an archived single link that delivered nothing new
        reports its file as it is, like a single link's file that was there before (_kept)."""
        if not self._filters or not item.options.archive_root or not self.archive or self.archive_folder is None:
            return
        folder = Path(effective.output)
        kept = []
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            match = _WORK_MEDIA.search(path.name)
            if match and match.group(1) in self._present and path.name[:match.start()] in self._present[match.group(1)] \
                    and path.is_file() and not _is_link(path) and _normal(path) in self._existing:
                kept.append(os.fspath(path))
        for path in kept:
            try:
                self.archive.media(self.archive_folder, {'filepath': path})
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.emit('log', msg('core.engine.archive_info_failed', error=message_of(error)))
                self.archive_errors.append(str(error))
        if kept and not item.multi and len(files) == first_file:
            found = kept[0]
            item.path = found
            files.append(found)
            stem = Path(found).stem
            media_id = stem[stem.rfind(' [') + 2:-1] if stem.endswith(']') and ' [' in stem else ''
            self.emit('log', msg('core.engine.kept_existing', name=Path(found).name))
            self.emit('media', {'id': media_id, 'extractor_key': '', 'title': item.title or stem,
                                'webpage_url': item.url, 'filepath': found, 'index': item.index})
            self.emit('file', found)

    @staticmethod
    def _finished_here(item, files, first_file) -> bool:
        """Whether a link for one file delivered its final file in this run (the media event or
        the file event came, and the file is on disk).  A playlist, search or channel link may
        stand for more files than it delivered: never."""
        if item.multi:
            return False
        if item.path and os.path.isfile(item.path):
            return True
        return len(files) > first_file and os.path.isfile(files[-1])

    def _item_done(self, item, files=0, **values):
        """Emit item_done for an item whose state is final.  The 1.5.1 keys come from the caller."""
        failed = not values.get('success') and not values.get('cancelled') and not values.get('skipped')
        item.files = files
        if failed and not item.reason:
            item.reason = explain_error(item.error, bot_check=bool(values.get('bot_check')))
        payload = {'index': item.index, **values, 'files': files, 'url': item.url, 'origin': item.origin,
                   'title': item.title, 'path': item.path, 'error': item.error, 'reason': item.reason,
                   'stale_engine': bool(failed and self.stale and not values.get('bot_check')),
                   'format': item.format}
        payload.setdefault('skipped', '')
        self.emit('item_done', payload)

    def download(self, urls, options, overrides=None):
        """Run the queue: ``urls`` with ``options``, or with ``overrides[url]`` where given.

        One worker thread; the only place that emits.  Always ends with a done event.
        """
        files = []
        bot_checks = 0
        self.archive = None
        self.archive_errors = []
        self._archives = {}
        self.engine_error = False
        self._current = None
        self._cleaned = set()
        try:
            merged = []
            with self.queue_lock:
                self.opened = False
                self.items, self.inbox = [], []
                self.closed = False
                self.blocked = False
                self.base_options = options
                seen = set()
                for url in urls:
                    item = QueueItem(len(self.items) + 1, url, (overrides or {}).get(url, options), self.origins.get(url))
                    if item.signature() in seen:
                        merged.append(url)
                        continue
                    seen.add(item.signature())
                    self.items.append(item)
                self.opened = True
            for url in merged:
                self.emit('log', msg('core.engine.duplicate_merged', url=url))
            while (item := self._next()) is not None:
                index = item.index
                self.last_error, self.stale, self.subs_failed, self._renamed = '', False, False, {}
                self._work, self._existing = None, set()
                self._covers, self._cover_folders = [], set()
                self._kept = None
                self._source, self._keep_source, self._at_download = None, False, False
                self._keep_early = False
                self._drop_filter_config()      # (an earlier item that ended before its own clean-up)
                self._rate_attempt, self._media_ids, self._present, self._filters = 0, [], {}, []
                # A YouTube bot check earlier in this run: the other YouTube links are not sent
                # to the refused address; they stay in the unfinished list.
                if self.blocked and urltools.is_youtube(item.url):
                    item.state = 'blocked'
                    item.reason = explain_error('', bot_check=True)
                    self._item_done(item, cancelled=False, success=False, bot_check=True, skipped='bot_check')
                    continue
                # Already on disk in this format (never for a link that stands for several files).
                if item.options.skip_downloaded and not item.multi and self.library and not item.options.archive_root:
                    row = None
                    try:
                        # (1.7.0 fix round 1) Another program's lock does not hold the start for the
                        # worker timeout: after FIND_TIMEOUT the item is downloaded as usual.
                        from library import FIND_TIMEOUT, worker_wait
                        with worker_wait(FIND_TIMEOUT):
                            row = self.library.find_download([item.key], item.format)
                    except Exception as error:
                        self.emit('log', msg('core.engine.record_unreadable', error=message_of(error)))
                    if row and row.get('exists'):
                        item.state = 'skipped'
                        item.title, item.path = str(row.get('title') or ''), str(row.get('path') or '')
                        self._item_done(item, cancelled=False, success=False, bot_check=False, skipped='downloaded')
                        continue
                self.emit('item', {'index': index, 'total': len(self.items), 'url': item.url, 'key': item.key,
                                   'format': item.format, 'origin': item.origin, 'multi': item.multi})
                first_file = len(files)
                # Spacing between downloads, measured from the end of the last successful one (also
                # that of an earlier job).  Inside one playlist the engine itself waits.
                if item.options.batch_safe and not item.options.playlist and Runner.last_media_end is not None:
                    gap = random.uniform(*PACE_SECONDS) - (time.monotonic() - Runner.last_media_end)
                    if gap > 0:
                        self.wait_kind = 'interval'
                        try:
                            waited = self._wait(gap, index, len(self.items), msg('core.wait.pace'))
                        finally:
                            self.wait_kind = 'rate_limit'
                        if not waited:
                            item.state = 'stopped'
                            self._item_done(item, cancelled=True, success=False)
                            break
                # Destination, archive and command.  What goes wrong here fails this link only.
                try:
                    archive = None
                    if item.options.archive_root:
                        archive = self._archives.get(item.options.archive_root)
                        if archive is None:
                            from library import Archive, Library
                            archive = self._archives[item.options.archive_root] = _SafeArchive(Archive(
                                item.options.archive_root, self.library or Library(),
                                log=lambda text: self.emit('log', text)))
                    self.archive, self.archive_folder = archive, None
                    effective = item.options
                    if archive:
                        context = dict(item.options.archive_contexts.get(item.url) or {})
                        if context.get('job_id'):
                            try:
                                record = self.archive.library.job(context['job_id'])
                                context['page'] = next((p for p in record['pages'] if p['url'] == context['page_url']), None)
                            except ValueError:
                                self.emit('log', msg('core.engine.no_collect_history'))
                        self.archive_folder = self.archive.prepare(item.url, context)
                        effective = replace(item.options, output=self.archive_folder / '媒体')
                    else:
                        item.options.output.mkdir(parents=True, exist_ok=True)
                    # What is in the output folder before the engine starts: a file found there is
                    # never renamed or changed (_intercept).
                    existing = _listing(effective.output)
                    work = planned = temp_path(effective)
                    if work is not None and not item.multi and _old_partials(existing, item.url):
                        # An unfinished download of this link that was written straight into the
                        # output folder (1.5.1, the title_id style): without -P temp: the engine
                        # resumes it there instead of starting again and leaving it behind.
                        work = None
                        self.emit('log', msg('core.engine.resume_partial'))
                    elif work is not None:
                        try:
                            paths.own_dir(work)               # created here, so that it carries the app's marker
                        except OSError:
                            work = None                       # cannot be created: download without it
                    if work is None and effective.embed_cover and getattr(item.preset, 'cover', False):
                        # Without a work folder the engine writes the cover beside the media, where it
                        # would take, embed and delete a picture of the owner's with the work name.
                        pictures = _owner_pictures(existing, item.url, item.multi)
                        if pictures:
                            effective = replace(effective, embed_cover=False)
                            self.emit('log', msg('core.engine.picture_exists', name=_shown_name(pictures[0])))
                    # (temp=False only when the planned work folder is not used: callers that stand
                    # in for build_command keep their two-argument form.)
                    command = build_command(item.url, effective) if work == planned \
                        else build_command(item.url, effective, temp=False)
                    if work is not None and _normal(work) != _normal(paths.work_dir()) and _normal(work) not in self._cleaned:
                        # The state folder's work folder is cleaned at program start (paths.purge_stale).
                        self._cleaned.add(_normal(work))
                        clean_app_work(work)
                    self._work = work
                    if item.multi or item.options.archive_root:
                        # (final-data-safety-1) What is already there is skipped, never post-processed again.
                        effective, command = self._skip_present(item, effective, command)
                except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
                    self.emit('log', msg('core.engine.cannot_start_link', error=message_of(error)))
                    item.state = 'failed'
                    item.error = cut(_masked(message_of(error), item.options), 200)
                    self._item_done(item, cancelled=False, success=False, bot_check=False)
                    continue
                item.ran = True
                self._existing = existing
                if not item.multi and not item.options.archive_root and _other_formats(existing, item.url, item.preset):
                    # (final-regression-gate-1) An older file of this link in another format is in
                    # the output folder: the engine would convert it and delete it at once, faster
                    # than it can be stopped after its "already downloaded" line.  -k from the start.
                    command = _keeping_source(command)
                    self._keep_source = self._keep_early = True
                self._cover_folders = {_normal(folder) for folder in (effective.output, work) if folder is not None}
                self._current = item
                self.bot_check = False
                # The 429 loop (moved verbatim into _cooled in 1.7.0, so that the reruns below use it too).
                result, start_failed = self._cooled(command, index, files)
                if self._source is not None and self._kept is None and not self._keep_early \
                        and not start_failed and not self.cancelled.is_set():
                    # The engine reported another format of this link that was there before the
                    # item (the source of a conversion) and was stopped before it could convert
                    # and delete it: once more with -k, so that it converts and leaves that file.
                    self.emit('log', _source_line(Path(self._source).name))
                    self._keep_source, self._at_download = True, False
                    self.last_error, self.stale, self.subs_failed = '', False, False
                    # (c3-retry-ratelimit) Through the same 429 cooldown as the main run.
                    again, start_failed = self._cooled(_keeping_source(command), index, files)
                    if again is not None or not start_failed:
                        result = again
                if self._kept is not None:
                    # The engine reported a file that was there before this item and was stopped
                    # before it could change it: the item is done with that file, as it is.
                    result = 0
                    kept = self._kept
                    item.path = kept
                    self._discard_cover(kept)
                    if kept not in files:
                        files.append(kept)
                        stem = Path(kept).stem
                        media_id = stem[stem.rfind(' [') + 2:-1] if stem.endswith(']') and ' [' in stem else ''
                        self.emit('media', {'id': media_id, 'extractor_key': '', 'title': item.title or stem,
                                            'webpage_url': item.url, 'filepath': kept, 'index': index})
                        self.emit('file', kept)
                # A subtitle that cannot be fetched must not cost the video: once more without it.
                if (result not in (None, 0) and self.subs_failed and item.options.subtitles
                        and not self.engine_error and not self.cancelled.is_set()):
                    self.emit('log', msg('core.engine.subtitles_dropped'))
                    self.last_error, self.subs_failed = '', False
                    try:
                        rerun = replace(effective, subtitles=False)
                        command = build_command(item.url, rerun) if self._work == temp_path(rerun) \
                            else build_command(item.url, rerun, temp=False)
                        if self._keep_source:
                            command = _keeping_source(command)     # the older file still stays
                        if self._filters:
                            command = _with_filters(command, self._filters)   # what is there stays skipped
                    except (ValueError, OSError) as error:
                        self.emit('log', msg('core.engine.cannot_start_link', error=message_of(error)))
                        self.last_error = message_of(error)
                    else:
                        # (c3-retry-ratelimit) Through the same 429 cooldown as the main run.
                        again, start_failed = self._cooled(command, index, files)
                        if again is not None or not start_failed:
                            result = again
                if self._keep_early and self._source is None and result == 0:
                    # The engine downloaded afresh instead of converting the older file (its title
                    # changed, or the id only looked alike): -k kept the new download's source.
                    self._discard_kept(item, effective.output, files)
                if self._keep_source and item.options.subtitles:
                    # (rv-findings-2-2) -k also kept the subtitle file the engine embedded.  (1.7.0 fix
                    # round 2, v-core-2-c3-stop-after-delivery-leaves-vtt) Whatever the outcome: after
                    # a Stop or a failure the file would be there before the retry (self._existing),
                    # which with -k would never remove it; the retry fetches the subtitles again.
                    self._discard_subtitles(item, effective.output)
                if self._keep_source and self._source is not None and (result != 0 or self.cancelled.is_set()) \
                        and not self._finished_here(item, files, first_file):
                    # (1.7.0, v-core-3-c3-stop-leaves-temp-mkv) Stopped or failed while converting the
                    # older file in place: the half-made file would pass for finished on the next run.
                    self._discard_unfinished(item, files)
                # (same finding) Whatever the outcome: ffmpeg's '<work name>.temp.mkv' and the like.
                self._discard_intermediates(item, effective.output)
                if result == 0 and not self.cancelled.is_set():
                    self._present_done(item, effective, files, first_file)
                self._current = None
                self._discard_covers()
                self._drop_filter_config()
                if result == 0:
                    Runner.last_media_end = time.monotonic()
                item.error = cut(_masked(self.last_error, item.options), 200) if result != 0 else ''
                if self.cancelled.is_set():
                    if self._finished_here(item, files, first_file):
                        # Stop came after the engine had delivered the file (moved, renamed and
                        # reported): the item is done; only the rest of the queue stops.
                        if result != 0:
                            Runner.last_media_end = time.monotonic()
                        if item.path and os.path.isfile(item.path) and item.path not in files:
                            # Stopped between the media line and the file line: the file line is
                            # not read any more, but the file is there.
                            files.append(item.path)
                            self.emit('file', item.path)
                        item.state, item.error, item.reason = 'done', '', ''
                        self._item_done(item, len(files) - first_file, cancelled=False, success=True, bot_check=False)
                    else:
                        item.state = 'stopped'
                        self._item_done(item, len(files) - first_file, cancelled=True, success=False)
                    break
                if self.engine_error:
                    if not start_failed:
                        self.emit('log', msg('core.engine.incompatible'))
                    item.state = 'unstarted'
                    item.reason = ENGINE_ERROR_REASON
                    self._item_done(item, len(files) - first_file, cancelled=False, success=False, bot_check=False)
                    break
                if result == 0:
                    item.state = 'done'
                else:
                    item.state = 'failed'
                    if self.bot_check:
                        bot_checks += 1
                        if urltools.is_youtube(item.url):
                            self.blocked = True
                            item.state = 'blocked'
                        self.emit('log', BOT_CHECK_STALE_HINT if item.options.cookies.strip() else BOT_CHECK_HINT)
                    item.reason = explain_error(item.error, result, bot_check=self.bot_check, stale=self.stale)
                    self.emit('log', msg('core.engine.item_unfinished', code=result))
                self._item_done(item, len(files) - first_file, cancelled=False, success=result == 0,
                                bot_check=result != 0 and self.bot_check)
        except Exception as error:
            applog.get('engine').exception('download queue aborted')
            # As in 1.5.1: the link that was running counts as failed and gets no item_done.
            for item in self.items:
                if item.state == 'active':
                    item.state = 'failed'
                    item.error = cut(_masked(message_of(error), item.options), 200)
                    item.reason = explain_error(item.error)
            self.emit('log', msg('core.engine.error', error=message_of(error)))
        finally:
            self._current = None
            # Close the queue (also on the paths that do not end in _next()) and take in what
            # enqueue() accepted meanwhile: those links must appear in an event and in the list.
            with self.queue_lock:
                self.closed = True
                moved = self.inbox[:]
                self.items += moved
                self.inbox.clear()
                total = len(self.items)
            if moved:
                try:
                    self.emit('queued', self._queued(moved, total))
                except Exception:
                    applog.get('engine').exception('queued event failed')
            cancelled = self.cancelled.is_set()
            for item in self.items:
                if item.state == 'active':
                    item.state = 'stopped' if cancelled else 'unstarted' if self.engine_error else 'failed'
                elif item.state == 'pending':
                    item.state = 'unstarted'
            # The unfinished list: what finished leaves it, what did not is stored (unfinished.merge).
            results = [{'url': item.url, 'key': item.key, 'format': item.format, 'options': _entry_options(item.options),
                        'outcome': item.state, 'error': item.error, 'title': item.title,
                        'referer': item.options.referers.get(item.url, ''), 'media_keys': list(item.media_keys)}
                       for item in self.items]
            stored, failed_path = 0, None
            try:
                with unfinished.LOCK:
                    entries = unfinished.merge(unfinished.load_for_merge(lambda text: self.emit('log', text)), results)
                    path = unfinished.save(entries)
                stored = len(entries)
                failed_path = str(path) if entries else None
            except Exception as error:   # whatever goes wrong here, done must still be the last event
                if not isinstance(error, OSError):
                    applog.get('engine').exception('the unfinished list could not be saved')
                self.emit('log', msg('core.engine.unfinished_not_saved', error=message_of(error)))
            failed_items = [item for item in self.items if item.state == 'failed' or (item.state == 'blocked' and item.ran)]
            finished = {item.signature() for item in self.items if item.state in ('done', 'skipped')}
            self.emit('done', {'success': sum(item.state == 'done' for item in self.items),
                               'failed': len(failed_items), 'files': files,
                               'cancelled': cancelled,
                               'failed_urls': list(dict.fromkeys(item.url for item in failed_items)),
                               'pending_urls': list(dict.fromkeys(item.url for item in self.items
                                                                  if item.signature() not in finished)),
                               'failed_path': failed_path,
                               'archive_errors': self.archive_errors, 'bot_check': bot_checks > 0,
                               'total': len(self.items),
                               'skipped': sum(item.state == 'skipped' for item in self.items),
                               'blocked': 'bot_check' if self.blocked else '',
                               'unfinished': stored, 'engine_error': self.engine_error})


def diagnostics():
    # 'catalog': the history file the window would look at first; not probed for writing (the self-test
    # writes nothing beside the owner's data), so a read-only file there is still named.
    catalog = paths.data_dir() / 'library.sqlite3'
    result = {'app_version': APP_VERSION, 'resource_root': str(paths.RESOURCE_ROOT),
              'app_dir': str(paths.APP_DIR), 'state_dir': str(paths.STATE_DIR),
              'catalog': str(catalog if catalog.is_file() else paths.STATE_DIR / 'library.sqlite3'),
              'components': {}}
    import scrapling
    from scrapling.fetchers import Fetcher, StealthyFetcher
    from collector import browser_available
    result['components']['scrapling'] = {'returncode': 0, 'version': [scrapling.__version__],
                                         'dynamic_Chrome_available': browser_available()}
    for name, command in [
        ('yt-dlp', [str(engine_path()), '--ignore-config', '--version']),
        ('ffmpeg', [str(paths.BIN / 'ffmpeg.exe'), '-version']),
        ('ffprobe', [str(paths.BIN / 'ffprobe.exe'), '-version']),
        ('deno', [str(paths.BIN / 'deno.exe'), '--version'])
    ]:
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=90, cwd=paths.BIN, env=process_environment(), creationflags=HIDDEN)
        result['components'][name] = {'returncode': process.returncode,
                                      'version': (process.stdout or process.stderr).splitlines()[:1]}
        if process.returncode:
            raise RuntimeError(f'{name}: {process.stderr}')
    return result
