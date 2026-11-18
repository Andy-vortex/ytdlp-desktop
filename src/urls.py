"""Links (1.6.0): lenient extraction from pasted text, YouTube clean-up, identity keys.

Standard library and messages only (1.8.0: the errors are messages).  Three different things
live here and must not be mixed up:

* ``identity(url)`` is the function of 1.5.1's library.py, moved here unchanged.  Its
  results are stored (subscription entries, archive folder names), so it never changes.
* ``link_key(url, playlist)`` says which links are the same download.  It is used for
  comparing only: the queue, the unfinished list, the downloads table.
* ``clean_url(url, playlist)`` is what the engine is handed.  Only YouTube links change,
  and only by losing tracking parameters and a list that would not be used anyway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import messages

YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be',
                 'www.youtube-nocookie.com'}
TRACKING = ('si', 'pp', 'feature', 'start_radio', 'index', 'ab_channel')
# Characters that never belong to a pasted link: white space, angle brackets and quotes, and
# full-width punctuation, brackets and curly quotes (a browser percent-encodes them), and
# zero-width characters.  CJK letters are allowed: some sites have them in their paths.
_STOP = '\\s<>"\'，。；：！？、（）【】《》「」『』〈〉〔〕［］｛｝“”‘’…\u200b\u200c\u200d\u2060\ufeff'
URL_RE = re.compile(r'https?://[^' + _STOP + r']+', re.I)
BARE_RE = re.compile(r'(?<![\w/.])(?:www\.|youtu\.be/|music\.youtube\.com/)[^' + _STOP + r']+', re.I)
COMMENT = ('#', ';', ']')
# Stripped from the end of a link: ASCII and full-width punctuation, closing brackets and quotes.
TRAILING = '.,;:!?)]}>\'"，。；：！？、）】》」』〉〕］｝”’…～·\u200b\u200c\u200d\u2060\ufeff'
# Where one match holds two links: a separator (, ; |) followed by the start of a link, or a
# scheme glued to the end of the first link.  A scheme after / = ? & # % : ( . @ is part of the
# first link (https://web.archive.org/web/2020/https://..., ?url=https://...).
_LINK_START = r'(?:https?://|www\.|youtu\.be/|music\.youtube\.com/)'
_GLUE = re.compile(r'[,;|]\s*(?=' + _LINK_START + r')|(?<=[^\s/=?&#%:(.@])(?=https?://)', re.I)
_ID = re.compile(r'[A-Za-z0-9_-]+')     # YouTube video ids are ASCII
_SCHEME = re.compile(r'^[a-z][a-z0-9+.-]*://', re.I)


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


def _split(url):
    """urlsplit that answers None for text it cannot take apart."""
    try:
        parts = urlsplit(str(url))
        parts.hostname   # raises for a broken bracketed host
        return parts
    except ValueError:
        return None


def _host(parts) -> str:
    return (parts.hostname or '').lower() if parts is not None else ''


def _list_id(parts) -> str:
    return parse_qs(parts.query).get('list', [''])[0]


def _route(fragment) -> bool:
    """Whether a fragment selects the page of a hash-routed site (#/song?id=1, #!/video/2,
    #id=3) rather than a place on the page (#comments)."""
    return bool(fragment) and (fragment[0] in '/!' or '?' in fragment or '=' in fragment)


def extract_urls(text) -> list[str]:
    """Every link in pasted text, in reading order, each once.

    Lenient: words around a link, Chinese punctuation after it and a missing scheme in
    front of www. / youtu.be/ / music.youtube.com/ are all accepted.  Comment lines
    (starting with # ; or ]) are skipped as in engine.parse_urls.
    """
    found = []
    for line in str(text).splitlines():
        if line.strip().startswith(COMMENT):
            continue
        spans = [(match.start(), match.end(), match.group(0)) for match in URL_RE.finditer(line)]
        full = list(spans)
        for match in BARE_RE.finditer(line):
            if not any(start <= match.start() < end for start, end, _text in full):
                spans.append((match.start(), match.end(), 'https://' + match.group(0)))
        for _start, _end, text in sorted(spans):
            for candidate in _pieces(text):
                candidate = _trim_youtube(candidate.rstrip(TRAILING))
                parts = _split(candidate)
                if parts is None or parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
                    continue
                if candidate not in found:
                    found.append(candidate)
    return found


def _pieces(text) -> list[str]:
    """One match split where two links were glued together; each piece with a scheme."""
    result, start = [], 0
    for match in _GLUE.finditer(text):
        if match.start() > start:
            result.append(text[start:match.start()])
        start = match.end()
    result.append(text[start:])
    return [piece if _SCHEME.match(piece) else 'https://' + piece for piece in result if piece]


def _trim_youtube(candidate) -> str:
    """A YouTube video link with words typed straight after it ("…/AAA太好听了") loses those words.

    Only when what comes before the first non-ASCII character is a video link: a channel
    handle or a search may be written in any script and is left as it is."""
    if _host(_split(candidate)) not in YOUTUBE_HOSTS:
        return candidate
    for position, character in enumerate(candidate):
        if ord(character) > 127:
            head = candidate[:position].rstrip(TRAILING)
            return head if video_id(head) else candidate
    return candidate


def parse_download_text(text) -> list[str]:
    """extract_urls for the link box: every line that is not empty or a comment must hold a link."""
    found = []
    for number, raw in enumerate(str(text).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(COMMENT):
            continue
        links = extract_urls(line)
        if not links:
            raise ValueError(messages.msg('core.links.no_link_on_line', line=number))
        for link in links:
            if link not in found:
                found.append(link)
    if not found:
        raise ValueError(messages.msg('core.links.none'))
    return found


def is_youtube(url) -> bool:
    return _host(_split(url)) in YOUTUBE_HOSTS


def video_id(url):
    """The video id of a YouTube-family link, or None (also for other sites)."""
    parts = _split(url)
    if _host(parts) not in YOUTUBE_HOSTS:
        return None
    path = parts.path
    if _host(parts) == 'youtu.be':
        candidate = path.strip('/').split('/')[0]
    elif path.startswith(('/shorts/', '/live/', '/embed/')):
        candidate = path.split('/')[2]
        if candidate == 'videoseries':   # /embed/videoseries?list=... is a playlist player
            return None
    elif path.rstrip('/') == '/watch':
        candidate = parse_qs(parts.query).get('v', [''])[0]
    else:
        return None
    return candidate if _ID.fullmatch(candidate) else None


def classify(url) -> str:
    """video | mix | video_in_list | playlist | search | channel | other (everything that is not YouTube)."""
    parts = _split(url)
    if _host(parts) not in YOUTUBE_HOSTS:
        return 'other'
    if video_id(url):
        listing = _list_id(parts)
        if listing.startswith('RD'):
            return 'mix'
        return 'video_in_list' if listing else 'video'
    path = parts.path
    if path.rstrip('/') == '/playlist':
        return 'playlist'
    if path.rstrip('/') == '/results':
        return 'search'
    if path.startswith(('/@', '/channel/', '/c/', '/user/')):
        return 'channel'
    return 'other'


def multi(url, playlist=False) -> bool:
    """Whether the link can stand for more than one media file.

    Then its link key must not be treated as one downloaded file: a playlist is not
    "already downloaded" because its first entry is.
    """
    return bool(playlist) or classify(url) in ('playlist', 'search', 'channel')


def clean_url(url, playlist=False) -> str:
    """The link as it is handed to the engine.

    YouTube family only; host, path and the order of the remaining parameters are kept.
    Tracking parameters go; on a link with a video id the list goes when the playlist
    option is off or the list is an automatic mix (RD...).  When nothing is removed the
    input comes back unchanged, and every other site's link is byte-identical.
    """
    parts = _split(url)
    if _host(parts) not in YOUTUBE_HOSTS or not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    has_video = video_id(url) is not None
    kept = [(name, value) for name, value in pairs
            if name not in TRACKING
            and not (name == 'list' and has_video and (not playlist or value.startswith('RD')))]
    if len(kept) == len(pairs):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept, safe=':/'), parts.fragment))


def link_key(url, playlist=False) -> str:
    """Which download a link is.  For comparing only; never handed to the engine.

    youtube:<id>          a video link whose list is absent, an automatic mix, or unused (playlist off)
    youtube:list:<list>   /playlist?list=..., and a video link with a real list when playlist is on
    bili:... / douyin:... as identity()
    otherwise             lower-cased scheme and host + path + query (YouTube: without tracking parameters);
                          on other sites followed by '#' + the fragment when the fragment is a route
                          (https://music.163.com/#/song?id=1 and ?id=2 are two songs), never for a
                          plain anchor such as #comments
    """
    parts = _split(url)
    if parts is None:
        return str(url)
    if _host(parts) not in YOUTUBE_HOSTS:
        key = identity(str(url))
        if _route(parts.fragment) and not key.startswith(('bili:', 'douyin:')):
            key += '#' + parts.fragment
        return key
    video, listing = video_id(url), _list_id(parts)
    if video:
        if listing and playlist and not listing.startswith('RD'):
            return 'youtube:list:' + listing
        return 'youtube:' + video
    if parts.path.rstrip('/') == '/playlist' and listing:
        return 'youtube:list:' + listing
    parts = _split(clean_url(url, playlist)) or parts
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))


@dataclass
class Normalized:
    urls: list = field(default_factory=list)      # cleaned links, one per link key, in input order
    origin: dict = field(default_factory=dict)    # cleaned link -> the spelling it was given in
    dropped_lists: int = 0                        # inputs whose list parameter was removed
    duplicates: int = 0                           # inputs merged into an earlier one
    kinds: dict = field(default_factory=dict)     # classify() of the kept links -> count


def normalize(urls, playlist=False) -> Normalized:
    """Clean the links and keep the first spelling of each download."""
    result, seen = Normalized(), set()
    for original in urls:
        cleaned = clean_url(original, playlist)
        key = link_key(cleaned, playlist)
        kind = classify(original)
        if kind in ('mix', 'video_in_list') and not _list_id(_split(cleaned)):
            result.dropped_lists += 1
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)
        result.urls.append(cleaned)
        result.origin[cleaned] = original
        result.kinds[kind] = result.kinds.get(kind, 0) + 1
    return result


def display(url, limit=60) -> str:
    """A link short enough for a row: no scheme, no leading www., cut with an ellipsis."""
    text = _SCHEME.sub('', str(url))
    if text.lower().startswith('www.'):
        text = text[4:]
    return text if len(text) <= limit else text[:max(limit - 1, 0)] + '…'
