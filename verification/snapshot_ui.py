"""Screenshots and layout assertions of the StudioApp pages (1.6.0 WP4, SPEC 6).

    .venv\\Scripts\\python.exe -B verification\\snapshot_ui.py [--pages download,collect,library,logs,settings]
                                                         [--sizes 1000x740,1120x810,2560x1392]
                                                         [--state idle|active|bot|filled] [--out DIR] [--no-shots]
                                                         [--appearance light|dark|both] [--language zh|en|both]

run(pages=None, sizes=(...), shots=True) builds one smoke-mode StudioApp (light mode, invisible:
alpha 0 and no window frame), empties the link box, selects MODES[0], and for every size and
page (and every tab of the library page) captures the window with PrintWindow into
VERIFY_OUT/shots (or --out).  It returns the list of failed assertions (empty = all pass) and
destroys its app before it returns.  A process never holds two live StudioApp instances:
call run() only after the calling file has destroyed its own app.

(2.0.0 S4 stage C) (15) state 'idle' with a language: the collect page in dynamic mode, the download page with
only unfinished links (its solid button is 重试未完成 (N)) and the empty library views are captured and checked
like the pages ((9), (9b), (13) and at most one solid button); the library views of state 'filled' are the
subscription view with new items, the crowded download page is state 'active' (download-crowded).

(2.0.0 S4 A2) (14) on every checked page the header holds only the title: header.title, header.left and
header.right are its only children, and no other label sits in it (page-level controls go into header.left /
header.right, 00-system 2.4).

Assertions (WP4.md section H): (1) at 1000x740 no page asks for more height than the content
area has; (2) task list viewport >= 300 logical px at 1000x740, >= 380 at 1120x810; (3) a
60-character title and a 120-character metrics text change neither the composer card, the task
header nor the viewport (+-1 px); (4) the settings grid fits the scroll viewport horizontally, the
scrollbar is shown and the last About row can be scrolled fully into view; (5) every Treeview line is
at least 0.85 of the body text line; (6) no widget of a checked page keeps the stock colours
#3B8ED0 / #979DA2; (7) at 2560x1392 the composer card is at most 1081 logical px wide; (8) at
1000x740 the full bot-check sentence fits the metrics line.
WP5: assertions (1) and (6) cover all five pages (CHECKED); one more check: nothing in the creation
strip of the library's subscription tab ends right of the strip.  State 'filled' (filled_scene) puts
collected links, history jobs, subscriptions with new entries and archive rows on the collect and
library pages; it uses its own smoke output folder and history.

(1.7.0, I18N.md 11.5) run(..., language='zh'|'en') / --language zh|en|both runs the same captures in
that language (i18n through _sandbox.language; PNG names of English captures end in '-en') and adds
assertions (9) to (12).  Without ``language`` (the 1.6.0 callers) the process language is kept and
only (1) to (8) run, exactly as before.
  (9)  no text cut without an ellipsis: a label managed by ui_theme.fit shows its full text or a
       prefix of it ending in '…'; every line of a wrapped label fits the label; every other label,
       button, checkbox, switch, option menu and segment needs no more than its width (text plus the
       widget's own insets, as ui_theme.fitted_width counts them); every table heading fits its column
       (text + 16 px); table cells are shown in full or cut by AppTable.ellipsize with '…'.
  (9b) nothing ends outside its card: every mapped button, entry, option menu, checkbox and segmented
       bar ends left of the inner right edge of the nearest card around it (all library tabs).
  (10) state 'active': the sidebar texts fit the sidebar (navigation, status, the 'Update available' link,
       never cut) with a running job at item 123 of 456, the waiting status, the YouTube status and a newer
       engine; (2.0.0 S4 A2) the status is one word of 00-system 4.4 (Downloading, Waiting mm:ss, On hold) and
       the link is shown exactly while a newer engine is offered.
  (11) state 'active', 1000x740: (2.0.0 S4) the task title has a line of its own, as wide as the task
       header, and is shown whole when it holds no user data, with 4 unfinished rows and the Retry count
       shown, and again with three-digit counts (122 done, 1 active, 333 waiting, 123 unfinished).  (Final
       fix round 1, ENV-1) Also after a job that ended with the YouTube check (a skipped link, eight on hold),
       after 清除未完成, and while a web collection or an engine update runs after it; (9) / (9b) hold for the
       task header.
  (12) the new-items line of the subscription view and the settings grid fit (the WP5 / 1.6.0 checks above,
       run in the chosen language; 2.0.0 S4: the creation strip and the settings cards are gone).

(1.8.0 WP-6, MESSAGES.md 7.6) In English only:
  (13) no Chinese outside user data: every text of every mapped widget of every captured page, tab and
       state (labels, buttons, check boxes, switches, option menus and their values, segments, entries and
       their placeholders, table headings and cells, text boxes, the log page; the sidebar) holds no CJK
       character once the user-data strings of the snapshot fixtures (USER_DATA: the titles, names and
       file names the scenes below feed, each checked to occur in this file) and the deliberate CJK of the
       English catalog (the language names 简体中文 / Language / 语言, the brackets 【】『』 the naming hint
       quotes) are taken out.  Cut texts are read whole (ui_theme.full_text, AppTable.full_values).
"""
import _sandbox
import argparse
import ast
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import sys
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

import customtkinter as ctk
from PIL import Image

PAGES = ('download', 'collect', 'library', 'logs', 'settings')
CHECKED = ('download', 'settings', 'logs', 'collect', 'library')     # WP4 checked the first three; WP5 added the others
SIZES = ('1000x740', '1120x810', '2560x1392')
LIBRARY_TABS = ('history', 'subscriptions', 'archive')        # library_ui.TAB_* (the tab ids), in tab order
STOCK = ('#3b8ed0', '#979da2')
TITLE_60 = '周杰伦 - 晴天 (Official Music Video) 【高音质 · 动态歌词 Lyrics】「每日推荐」 #63 加长版'[:60]
METRICS_120 = ('网站限流，暂停整个下载队列 · 剩余 04:59 · 可随时停止 · ' * 4)[:120]
# (WP7) run(..., appearance='dark') / --appearance dark|both: the same captures and assertions in dark
# mode, PNG names with a '-dark' suffix, plus dark_failures(): no widget shows a light surface, the
# table styles, stripes, log tags and progress canvas carry the dark token values.
APPEARANCES = ('light', 'dark')
LIGHT_SURFACES = ('#ffffff', '#f4f7fc', '#f7f9fd')
# (1.7.0) run(..., language=...) / --language: the language of a run and of its PNG names.
LANGUAGES = ('zh', 'en')
TOLERANCE = 1           # physical px a measured width may exceed the widget (rounding of scaled sizes)
HEADING_PAD = 16        # logical px a table heading needs beside its text (I18N.md 11.5 (9))
SIDEBAR_JOB = (123, 456)    # (10), (11): a long job (the task header counts it; the sidebar says one word)
# (13) The user data the scenes feed (media, page and link titles, subscription and uploader names, file
# names): taken out of a text before it is checked for CJK.  Every entry is a part of a string literal of
# this file (checked by user_data()), never an app text.
USER_DATA = ('徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】', '徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」',
             '徐秉龙 - 双子 【Lyrics MV】', '李翊君 雨蝶 歌詞版', '周杰伦 - 晴天 (Official MV)', '白羊.m4a',
             '第 ', ' 期 · 城市夜谈：在凌晨两点仍然醒着的人', '城市夜谈 · 节目列表', '城市夜谈 · 每周更新', '城市夜谈', '关于本节目',
             '节目简介', '官方频道公告', '未完成的歌曲', '媒体.m4a', '转写稿/文字稿.txt', TITLE_60)
CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]')


def grab(window, path) -> bool:
    """Capture ``window`` with PrintWindow (works for an invisible window); False when Windows refuses."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for function, result, arguments in [
            (user32.GetParent, wintypes.HWND, [wintypes.HWND]), (user32.GetWindowDC, wintypes.HDC, [wintypes.HWND]),
            (gdi32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC]),
            (gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_int, ctypes.c_int]),
            (gdi32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]),
            (user32.PrintWindow, wintypes.BOOL, [wintypes.HWND, wintypes.HDC, wintypes.UINT]),
            (gdi32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ]), (gdi32.DeleteDC, wintypes.BOOL, [wintypes.HDC]),
            (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC])]:
        function.restype, function.argtypes = result, arguments
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    width, height = window.winfo_width(), window.winfo_height()
    hdc = user32.GetWindowDC(hwnd)
    memory = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, 3):
            return False

        class Header(ctypes.Structure):
            _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                        ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                        ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD)]
        header = Header()
        header.biSize, header.biWidth, header.biHeight = ctypes.sizeof(header), width, -height
        header.biPlanes, header.biBitCount = 1, 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.UINT]
        gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1).convert('RGB').save(path)
        return True
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, hdc)


def settle(app, rounds=3):
    for _ in range(rounds):
        app.update_idletasks()
        app.update()


def settle_tab(app, tab, timeout=1.5):
    """Pump until the selected library tab is the current, mapped tab frame (the tab frames are
    stacked and raised, final-fix-ui), then a little longer for it to draw."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        settle(app, 1)
        try:
            ready = app.library_tabs.get() == tab and app.library_tabs.tab(tab).winfo_ismapped()
        except (tk.TclError, ValueError, KeyError, AttributeError):
            break
        if ready:
            break
        time.sleep(0.02)
    pause(app, 0.15)


def pause(app, seconds):
    """Let timers run (CTk re-checks some layouts, such as text box scrollbars, every 200 ms)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)


def widgets_below(widget):
    stack = [widget]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.winfo_children())


def active_scene(app):
    """A download in progress: three links in the box, eight rows in every state, 42 % of the second."""
    import engine
    import i18n
    app.urls.delete('1.0', 'end')
    app.urls.insert('1.0', 'https://www.youtube.com/watch?v=xrooMGuRhgY&list=RDxrooMGuRhgY&start_radio=1\n'
                           'https://www.youtube.com/watch?v=OQMcYxMlwGA\nhttps://youtu.be/dQw4w9WgXcQ')
    app.links_changed()
    links = [f'https://www.youtube.com/watch?v=video{number:04d}' for number in range(8)]
    app.populate_queue(links)
    app.operation = 'download'
    app.set_busy(True)
    app.handle('item', {'index': 1, 'total': 8, 'url': links[0]})
    app.handle('title', '徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】')
    app.handle('item_done', {'index': 1, 'success': True, 'cancelled': False, 'files': 1, 'bot_check': False,
                             'path': str(_sandbox.out('白羊.m4a'))})
    app.handle('item', {'index': 4, 'total': 8, 'url': links[3]})
    app.handle('title', '李翊君 雨蝶 歌詞版')
    # (L2) the reason is the engine's own canonical text, so a language run shows it localized
    app.handle('item_done', {'index': 4, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                             'reason': engine.explain_error('ERROR: [youtube] video0003: Video unavailable')})
    app.handle('item_done', {'index': 5, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                             'skipped': 'downloaded', 'title': '周杰伦 - 晴天 (Official MV)'})
    app.handle('item_done', {'index': 6, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': True,
                             'skipped': 'bot_check'})
    app.handle('item', {'index': 2, 'total': 8, 'url': links[1]})
    app.handle('title', '徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」')
    app.handle('progress', {'fraction': .42, 'downloaded': 1.5 * 2 ** 20, 'total': 3.6 * 2 ** 20,
                            'speed': 507.8 * 1024, 'eta': 4, 'status': 'downloading'})
    app.progress.set(.42, immediate=True)
    for line in ('[youtube] Extracting URL: ' + links[1], '[youtube] video0001: Downloading webpage',
                 'WARNING: [youtube] video0003: Some formats may be missing', 'ERROR: [youtube] video0003: Video unavailable',
                 engine.msg('core.engine.item_unfinished', code=1), '[ExtractAudio] Destination: 白羊.m4a',
                 i18n.t('log.file_saved', path=str(_sandbox.out('白羊.m4a')))):
        app.append_log(line)
    app.flush_log()
    app.unfinished = [{'url': links[3]}]
    app.refresh_failed_button()
    return engine


def bot_scene(app):
    """A job that ended with the YouTube bot check: the full sentence in the metrics line."""
    links = ['https://www.youtube.com/watch?v=GGGGGGGGGGG', 'https://example.org/other.mp3']
    app.populate_queue(links)
    app.operation = 'download'
    app.set_busy(True)
    app.handle('item', {'index': 1, 'total': 2, 'url': links[0]})
    app.handle('item_done', {'index': 1, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': True})
    app.handle('item', {'index': 2, 'total': 2, 'url': links[1]})
    app.handle('item_done', {'index': 2, 'success': True, 'cancelled': False, 'files': 1, 'bot_check': False})
    app.handle('done', {'success': 1, 'failed': 1, 'files': [], 'cancelled': False, 'pending_urls': [links[0]],
                        'bot_check': True})


def filled_scene(app):
    """Collect and library pages with content (WP5): collected links with a selection, history jobs
    with an error, a subscription with pending entries, archive rows.  The download page is left alone."""
    import engine
    from engine import MODES
    source = 'https://www.example.org/podcast/list'
    links = [{'url': f'https://media.example.org/podcast/episode-{number:03d}.m4a',
              'title': f'第 {number} 期 · 城市夜谈：在凌晨两点仍然醒着的人', 'kind': 'media', 'source': source}
             for number in range(1, 15)]
    links += [{'url': 'https://www.youtube.com/watch?v=OQMcYxMlwGA', 'title': '徐秉龙 - 双子 【Lyrics MV】', 'kind': 'video_page',
               'source': source},
              {'url': 'https://www.example.org/podcast/about', 'title': '关于本节目', 'kind': 'web', 'source': source}]
    page = {'url': source, 'title': '城市夜谈 · 节目列表', 'text': '节目简介', 'markdown': '# 节目简介', 'links': links}
    app.collect_urls.insert('1.0', source + '\nhttps://www.example.org/podcast/list?page=2')
    app.handle('collect_page', page)
    app.collect_tree.selection_set(['1', '2', '3'])
    app.collection_selection_changed()
    store = app.library
    for title, kind, status, errors in [
            ('徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」 等 3 条', 'download', 'partial',
             ['ERROR: [youtube] OQMcYxMlwGA: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies '
              'for the authentication.']),
            ('城市夜谈', 'collect', 'completed', []),
            ('李翊君 雨蝶 歌詞版', 'download', 'completed', []),
            (source, 'collect', 'interrupted', [engine.msg('core.collect.http_status', status=503)]),
            ('周杰伦 - 晴天 (Official MV)', 'download', 'interrupted', [])]:
        multi = title.endswith(' 等 3 条')
        # (final fix round 2, CR2-2) The multi-link job as a run writes it: three links, no title, then the
        # first media title (library.download_event; 2.0.0: the title and title_links 3).  (2.0.0 S2 P2) The
        # subscription check's job is via='subscription' with the subscription's name.
        urls = ['https://www.youtube.com/watch?v=OQMcYxMlwGA', 'https://youtu.be/dQw4w9WgXcQ',
                'https://www.bilibili.com/video/BV1xx411c7mD'] if multi else [source]
        job = store.create_job(kind, '' if multi else title,
                               {'urls': urls, 'output': str(_sandbox.out('snapshot-output')), 'mode': MODES[0]},
                               via='subscription' if title == '城市夜谈' else None)
        if multi:
            store.download_event(job, 'title', title[:-len(' 等 3 条')])
        store.change_job(job, status=status, errors=[{'error': error, 'reason': None} for error in errors])
    subscription = store.subscription('城市夜谈 · 每周更新', {'urls': [source], 'dynamic': False, 'scrolls': 0,
                                                          'selector': '', 'max_pages': 1}, 3600, True, True)
    store.finish_subscription(subscription, [page], True)
    store.subscription('官方频道公告', {'urls': ['https://www.example.org/news'], 'dynamic': False, 'scrolls': 0,
                                    'selector': '', 'max_pages': 2}, 86400, True, False)
    for number, link in enumerate(links[:6]):
        store.archive_index(str(_sandbox.out('snapshot-archive', f'item-{number}')),
                            {'url': link['url'], 'source': source, 'title': link['title'],
                             'uploader': '城市夜谈' if number % 2 else '', 'files': ['媒体.m4a'] if number < 4 else [],
                             'transcripts': ['转写稿/文字稿.txt'] if number == 1 else []})
    app.refresh_library()
    # full_values: the cells on screen may be cut with '…' (AppTable.ellipsize).
    with_error = [iid for iid in app.history_tree.get_children() if '双子' in app.history_tree.full_values(iid)[1]]
    if with_error:
        app.history_tree.selection_set(with_error[0])
        app.history_selection()
    subscriptions = app.subscription_tree.get_children()
    if subscriptions:
        app.subscription_tree.selection_set(subscriptions[0])
        app.subscription_selection()
        app.pending_tree.selection_set(app.pending_tree.get_children()[:2])


def shot_name(name, appearance, language=None):
    """(WP7) 'download-1000x740.png' -> 'download-1000x740-dark.png' in dark mode; (1.7.0) English
    captures end in '-en' ('download-1000x740-dark-en.png'); Chinese ones keep the 1.6.0 names."""
    stem = name[:-len('.png')]
    if appearance != 'light':
        stem += f'-{appearance}'
    if language == 'en':
        stem += '-en'
    return stem + '.png'


# ---------------------------------------------------------------- (1.7.0) assertions (9) to (11)
def _scale_of(widget):
    import ui_theme
    return ui_theme._scaling(widget) or 1.0


def _font_measure(widget, text):
    """Physical px of ``text`` in the font a CTk widget draws with."""
    import ui_theme
    return ui_theme._measure_font(widget, widget.cget('font')).measure(text)


def _shown(widget):
    """(class name, text) of a CTk text widget, for the failure messages."""
    try:
        if isinstance(widget, ctk.CTkOptionMenu):
            text = widget._text_label.cget('text')
        elif isinstance(widget, ctk.CTkEntry):
            text = widget.get() or widget.cget('placeholder_text')
        else:
            text = widget.cget('text')
    except (ValueError, tk.TclError, AttributeError):
        text = ''
    return f'{type(widget).__name__} {str(text)!r}'


def needed_width(widget):
    """Physical px a label, button, checkbox, switch or option menu needs to show its text in full,
    counted like customtkinter lays it out (the insets ui_theme.fitted_width / menu_width count); None
    when the widget shows no text or is not one of these."""
    def scaled(value):
        return widget._apply_widget_scaling(value)
    try:
        if isinstance(widget, ctk.CTkButton):
            inner = getattr(widget, '_text_label', None)
            if inner is None or not str(widget.cget('text')).strip():
                return None
            column = max(widget._corner_radius, widget._border_width + 1, widget._border_spacing)
            need = 2 * scaled(column) + inner.winfo_reqwidth()
            image = getattr(widget, '_image_label', None)
            if image is not None and widget._compound in ('left', 'right'):
                need += image.winfo_reqwidth() + scaled(widget._image_label_spacing)
            return need
        if isinstance(widget, ctk.CTkOptionMenu):
            inner = widget._text_label
            if not str(inner.cget('text')).strip():
                return None
            left = max(scaled(widget._corner_radius), scaled(3))
            right = max(scaled(widget._current_height + 3), scaled(3))
            return inner.winfo_reqwidth() + left + right
        if isinstance(widget, (ctk.CTkCheckBox, ctk.CTkSwitch)):
            inner = widget._text_label
            if not str(widget.cget('text')).strip():
                return None
            return widget._canvas.winfo_reqwidth() + scaled(6) + inner.winfo_reqwidth()
        if isinstance(widget, ctk.CTkLabel):
            inner = widget._label
            if not str(inner.cget('text')).strip():
                return None
            inset = scaled(min(widget._corner_radius, round(widget._current_height / 2)))
            return inner.winfo_reqwidth() + 2 * inset
    except (AttributeError, tk.TclError):
        return None
    return None


def checked_widgets(app, page):
    """Every viewable widget below page ``page``; on the library page only the shown tab (the tab
    frames are stacked, final-fix-ui)."""
    skip = set()
    tabs = getattr(app, 'library_tabs', None)
    if page == 'library' and tabs is not None:
        current = tabs.get()
        skip = {str(frame) for name, frame in getattr(tabs, '_frames', {}).items() if name != current}
    stack = [app.pages[page]]
    while stack:
        widget = stack.pop()
        if str(widget) in skip:
            continue
        try:
            if not widget.winfo_viewable():
                continue
        except tk.TclError:
            continue
        yield widget
        stack.extend(widget.winfo_children())


def sidebar_widgets(app):
    side = app.side_status.master.master
    stack = [side]
    while stack:
        widget = stack.pop()
        try:
            if not widget.winfo_viewable():
                continue
        except tk.TclError:
            continue
        yield widget
        stack.extend(widget.winfo_children())


def text_failures(widgets, strict_fit=False):
    """(9) for ``widgets``: messages for every text shown cut without an ellipsis.  ``strict_fit``: a
    label managed by fit must show its full text too (the sidebar, I18N.md 10.4)."""
    import ui_theme
    failures = []
    for widget in widgets:
        if isinstance(widget, ttk.Treeview):
            failures += _table_failures(widget)
            continue
        if not isinstance(widget, (ctk.CTkLabel, ctk.CTkButton, ctk.CTkOptionMenu, ctk.CTkCheckBox, ctk.CTkSwitch)):
            continue
        try:
            width = widget.winfo_width()
        except tk.TclError:
            continue
        if width <= 1:
            continue
        if isinstance(widget, ctk.CTkLabel) and getattr(widget, '_fit_text', None) is not None:
            full = ui_theme.full_text(widget).replace('\n', ' ')
            shown = str(widget.cget('text'))
            if shown != full:
                if strict_fit:
                    failures.append(f'(9) {type(widget).__name__} cut to {shown!r} (full {full!r}), must not be cut')
                elif not (shown.endswith('…') and full.startswith(shown[:-1].rstrip())):
                    failures.append(f'(9) {type(widget).__name__} shows {shown!r}, not {full!r} or a prefix of it with …')
            continue
        if isinstance(widget, ctk.CTkLabel) and getattr(widget, '_wrap_bound', False):
            inner = widget._label
            inset = widget._apply_widget_scaling(min(widget._corner_radius, round(widget._current_height / 2)))
            for line in str(inner.cget('text')).split('\n'):
                if _font_measure(widget, line) + 2 * inset > width + TOLERANCE:
                    failures.append(f'(9) wrapped {type(widget).__name__}: line {line!r} is '
                                    f'{_font_measure(widget, line)} px, label {width} px')
            continue
        need = needed_width(widget)
        if need is not None and need > width + TOLERANCE:
            failures.append(f'(9) {_shown(widget)} needs {need:.0f} px, has {width} px')
    return failures


def _table_failures(table):
    """(9) the headings of a Treeview fit their columns; the cells of an AppTable are shown in full or
    cut with '…' (AppTable.ellipsize) and fit their column."""
    import ui_theme
    failures = []
    scale = _scale_of(table)
    style = table.cget('style') or 'Treeview'
    lookup = ttk.Style(table)
    spec = lookup.lookup(style + '.Heading', 'font') or lookup.lookup('Treeview.Heading', 'font') or 'TkHeadingFont'
    columns = list(table['columns'])
    widths = {}
    for column in columns:
        widths[column] = int(table.column(column, 'width'))
        title = str(table.heading(column, 'text'))
        if not title:
            continue
        need = int(table.tk.call('font', 'measure', spec, '-displayof', table, title)) + round(HEADING_PAD * scale)
        if need > widths[column] + TOLERANCE:
            failures.append(f'(9) table {table} heading {title!r} needs {need} px, column {widths[column]} px')
    full_values = getattr(table, 'full_values', None)
    if full_values is None or getattr(table, '_cut_full', None) is None:
        return failures
    measure = ui_theme._measure_font(table, (ui_theme.FAMILY, ui_theme.TABLE_FONT)).measure
    spare = 2 * max(0, round(10 * scale) - 4) + round(4 * scale)      # AppTable._cut_rooms
    for iid in table.get_children()[:80]:
        shown = tuple(table.item(iid, 'values'))
        full = full_values(iid)
        for position, column in enumerate(columns):
            if position >= len(shown):
                break
            text = str(shown[position])
            whole = str(full[position]).replace('\n', ' ') if position < len(full) else text
            if text != whole and '…' not in text:
                failures.append(f'(9) table {table} cell {whole!r} shown as {text!r} without …')
            elif measure(text) > widths[column] - spare + TOLERANCE:
                failures.append(f'(9) table {table} cell {text!r} is {measure(text)} px, column {column} '
                                f'has {widths[column] - spare} px')
    return failures


def _is_card(widget):
    import ui_theme
    if not isinstance(widget, ctk.CTkFrame) or isinstance(widget, ctk.CTkSegmentedButton):
        return False
    try:
        # (2.0.0 S4) no cards any more: a bordered, rounded frame (the link box) bounds what sits in it
        return widget.cget('border_width') >= 1 and widget.cget('corner_radius') >= ui_theme.RADIUS
    except (ValueError, tk.TclError):
        return False


def header_failures(app, page):
    """(14) The header of page ``page`` holds only its title (2.0.0 S4, 00-system 2.4): its children are
    header.title, header.left and header.right, and the title is the only label in it."""
    import ui_theme
    header = getattr(app.pages[page], 'header', None)
    if header is None:
        return [f'(14) {page}: the page has no header']
    failures = []
    children = [child for child in header.winfo_children() if isinstance(child, ctk.CTkBaseClass)]
    if set(children) != {header.title, header.left, header.right} or len(children) != 3:
        failures.append(f'(14) {page}: the header holds {[str(child) for child in children]}')
    labels = [widget for widget in widgets_below(header) if isinstance(widget, ctk.CTkLabel)]
    if labels != [header.title]:
        failures.append(f'(14) {page}: labels in the header: {[ui_theme.full_text(label) for label in labels]}')
    return failures


def user_data(app=None):
    """(13) USER_DATA, after checking that every entry is part of a string literal of this file (the
    scenes' fixture data) and is not a text of the zh catalog, a template's placeholders matching anything
    (2.0.0 S2 P2: in place of the legacy reader's G2 recognition); with ``app``
    also the fixture's own folders (its save folder and archive folder, the default 资料归档 of D10
    included)."""
    folders = []
    for name in ('output', 'archive_root'):
        try:
            folders.append(str(getattr(app, name).get()))
        except (AttributeError, tk.TclError):
            pass
    import i18n
    catalog = []
    for text in i18n.CATALOG['zh'].values():
        parts = re.split(r'\{[A-Za-z_][A-Za-z_0-9]*\}', text)
        if re.search(r'[一-鿿]', ''.join(parts)):          # a template with Chinese words of its own
            catalog.append(re.compile('.+'.join(re.escape(part) for part in parts), re.S))
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    literals += [part.value for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                 for part in node.values if isinstance(part, ast.Constant)]
    for entry in USER_DATA:
        if not any(entry in literal for literal in literals if literal is not USER_DATA):
            raise AssertionError(f'(13) USER_DATA entry {entry!r} is no fixture text of snapshot_ui')
        if any(pattern.fullmatch(entry) for pattern in catalog):
            raise AssertionError(f'(13) USER_DATA entry {entry!r} is an app text')
    return sorted([entry for entry in USER_DATA + tuple(folders) if entry], key=len, reverse=True)


def chinese_left(text, users=None):
    """(13) The CJK characters of ``text`` once the user data and the deliberate CJK of the English
    catalog (the language names, the 【】『』 the naming hint quotes) are taken out."""
    import i18n
    text = str(text)
    for entry in users if users is not None else user_data():
        text = text.replace(entry, '')
    for key in ('settings.language.zh', 'settings.language.title'):
        for allowed in i18n.all_values(key):
            text = text.replace(allowed, '')
    return ''.join(char for char in CJK.findall(text) if char not in '【】『』')


def shown_texts(widgets):
    """(13) [(widget class, text)] of every text the widgets show or offer, uncut."""
    import ui_theme
    found = []
    for widget in widgets:
        try:
            if isinstance(widget, ttk.Treeview):
                found += [('heading', widget.heading(column, 'text')) for column in widget['columns']]
                values = getattr(widget, 'full_values', None)
                for iid in widget.get_children():
                    cells = values(iid) if values else widget.item(iid, 'values')
                    found += [('cell', cell) for cell in cells]
                found.append(('empty', getattr(widget, '_empty', '') or ''))
            elif isinstance(widget, ctk.CTkLabel):
                found.append(('label', ui_theme.full_text(widget)))
            elif isinstance(widget, ui_theme.PathButton) and widget.path:
                continue                    # (2.0.0 S4) a folder or file path is user data
            elif isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkSwitch, ctk.CTkRadioButton)):
                found.append((type(widget).__name__, widget.cget('text')))
            elif isinstance(widget, ctk.CTkOptionMenu):
                found.append(('option menu', widget._text_label.cget('text')))
                found += [('option', value) for value in widget.cget('values') or ()]
            elif isinstance(widget, ctk.CTkSegmentedButton):
                found += [('segment', value) for value in widget.cget('values') or ()]
            elif isinstance(widget, ctk.CTkEntry):
                found += [('entry', widget.get()), ('placeholder', widget.cget('placeholder_text') or '')]
            elif isinstance(widget, tk.Text):
                found.append(('text box', widget.get('1.0', 'end-1c')))
        except (tk.TclError, ValueError, AttributeError, KeyError):
            continue
    return [(kind, str(text)) for kind, text in found if text]


def cjk_failures(widgets, app=None, where=''):
    """(13) A message for every shown line that keeps CJK outside user data (English runs only)."""
    users = user_data(app)
    failures, seen = [], set()
    for kind, text in shown_texts(widgets):
        for line in text.splitlines() or [text]:
            left = chinese_left(line, users)
            if left and (kind, line) not in seen:
                seen.add((kind, line))
                failures.append(f'(13) {where}{kind} shows Chinese outside user data: {line[:120]!r} ({left[:20]})')
    return failures


def card_failures(app, widgets, page):
    """(9b) every mapped button, entry, option menu, checkbox and segmented bar ends left of the inner
    right edge of the nearest card around it (the page frame when there is none)."""
    failures = []
    kinds = (ctk.CTkButton, ctk.CTkEntry, ctk.CTkOptionMenu, ctk.CTkCheckBox, ctk.CTkSegmentedButton)
    for widget in widgets:
        if not isinstance(widget, kinds):
            continue
        if isinstance(widget, ctk.CTkButton) and isinstance(widget.master, ctk.CTkSegmentedButton):
            continue        # the segments: their bar is checked
        card = widget.master
        while card is not None and card is not app.pages[page] and not _is_card(card):
            card = card.master
        if card is None:
            continue
        try:
            inset = card._apply_widget_scaling(card.cget('border_width')) if card is not app.pages[page] else 0
            edge = card.winfo_rootx() + card.winfo_width() - inset
            right = widget.winfo_rootx() + widget.winfo_width()
        except (tk.TclError, ValueError, AttributeError):
            continue
        if widget.winfo_width() > 1 and right > edge + TOLERANCE:
            failures.append(f'(9b) {_shown(widget)} ends {right - edge:.0f} px right of its card')
    return failures


def title_failures(app, label):
    """(11, 2.0.0 S4) The task title has the first line of the task header to itself: it is as wide as the header,
    and a title without user data (the item name beyond the row cap) is shown whole.  (S4 visual fix) Without a
    metrics line the task buttons share the title's line (place_task_actions): the title then fills the line up
    to the first shown button, less that button's own gap (at most PAD)."""
    import ui_theme
    found = []
    title, header = app.current_title, app.task_header
    buttons = [widget for widget in (app.retry_button, app.task_menu, app.stop_button) if widget.winfo_manager()]
    if header.winfo_manager() and getattr(app, 'actions_inline', False) and buttons:
        gap = min(widget.winfo_x() for widget in buttons) - (title.winfo_x() + title.winfo_width())
        if not 0 <= gap <= round(ui_theme.PAD * _scale_of(app)) + TOLERANCE:
            found.append(f'1000x740 (11) {label}: the task title ends {gap} px before the buttons on its line')
        if any(widget.grid_info().get('row') != 0 for widget in buttons):
            found.append(f'1000x740 (11) {label}: a task button is not on the title line')
    elif header.winfo_manager() and abs(title.winfo_width() - header.winfo_width()) > TOLERANCE:
        found.append(f'1000x740 (11) {label}: the task title is {title.winfo_width()} px in a '
                     f'{header.winfo_width()} px header')
    full = ui_theme.full_text(title)
    if ' · ' not in full and title.cget('text') != full:
        found.append(f'1000x740 (11) {label}: the task title {full!r} is cut')
    return found


def _unfinished_entries(count, offset=0):
    """Stored unfinished entries (the shape unfinished.py keeps) for the task list's tail."""
    entries = []
    for number in range(count):
        url = f'https://www.youtube.com/watch?v=left{offset + number:06d}'
        entries.append({'url': url, 'key': f'youtube:left{offset + number:06d}', 'format': 'audio_best',
                        'reason': 'failed', 'title': f'未完成的歌曲 {number + 1}', 'error': '', 'time': number,
                        'options': {}})
    return entries


def _job_scene(app, done, waiting, unfinished):
    """A running download: ``done`` rows done, one active, ``waiting`` waiting, and ``unfinished``
    stored unfinished entries shown below the job (the Retry button counts them)."""
    total = done + 1 + waiting
    links = [f'https://www.youtube.com/watch?v=job{number:06d}' for number in range(total)]
    app.unfinished = []
    app.populate_queue(links)
    app.operation = 'download'
    app.set_busy(True)
    for number in range(done):
        app.row_model[number]['state'] = 'done'
    app.handle('item', {'index': done + 1, 'total': total, 'url': links[done]})
    app.handle('title', '徐秉龙 - 双子 【Lyrics MV】「每日歌曲推荐 #63」')
    app.unfinished = _unfinished_entries(unfinished)
    app.show_unfinished_tail()
    app._rows_changed(0)
    app.refresh_failed_button()
    app.refresh_shell()


def scene_failures(app, appearance, language, shots, folder):
    """(10) and (11): the sidebar texts and the task title of the active scene, at 1000x740."""
    import engine
    import i18n
    import ui_theme
    from ui_download import time_text
    failures = []
    app.geometry('1000x740+0+0')
    app.show_page('download')
    settle(app)
    scale = _scale_of(app)

    def sidebar(label):
        settle(app)
        found = [f'1000x740 sidebar ({label}): {failure}' for failure in text_failures(sidebar_widgets(app), True)]
        if language == 'en':                # (1.8.0) (13)
            widgets = list(sidebar_widgets(app)) + list(checked_widgets(app, 'download'))
            found += [f'1000x740 sidebar ({label}): {failure}' for failure in cjk_failures(widgets, app)]
        return found

    def title_room(label):
        settle(app)
        pause(app, 0.1)
        return title_failures(app, label)

    # (11) 4 unfinished rows and "Retry unfinished (4)"
    _job_scene(app, 2, 5, 4)
    failures += title_room('4 unfinished')
    def status(label, key, **values):
        # (2.0.0 S4 A2) one word (00-system 4.4), the same colour as its dot
        text = app.side_status.cget('text')
        wanted = i18n.t(key, **values)
        found = [] if text == wanted else [f'1000x740 sidebar ({label}): status {text!r}, expected {wanted!r}']
        if app.side_dot.cget('text_color') != app.side_status.cget('text_color'):
            found.append(f'1000x740 sidebar ({label}): the dot and the word differ in colour')
        return found

    def notice(label, shown):
        if bool(app.engine_notice.winfo_manager()) != shown:
            return [f"1000x740 sidebar ({label}): the 'Update available' link is {'not ' if shown else ''}shown"]
        return []

    # (10) + (11) a long job: item 123 of 456, three-digit counts
    index, total = SIDEBAR_JOB
    _job_scene(app, index - 1, total - index, 123)
    failures += title_room('three-digit counts')
    failures += sidebar(f'item {index} / {total}')
    failures += status(f'item {index} / {total}', 'shell.status.downloading')
    failures += notice(f'item {index} / {total}', False)
    if shots:
        grab(app, folder / shot_name('download-crowded-1000x740.png', appearance, language))
    # (10) the waiting status
    app.handle('waiting', {'kind': 'interval', 'reason': engine.msg('core.wait.pace'), 'seconds': 299})
    failures += sidebar('waiting')
    failures += status('waiting', 'shell.status.waiting', time=time_text(app.wait_seconds))
    app.clear_wait()
    # (10) awaiting YouTube and a newer engine: (S4 visual fix) the job ends normally with the YouTube check, so
    # the countdown is armed the way it is in use, and the sidebar and the page's notice bar show it together
    _finished_bot_job(app, keep_retry=True)
    app.handle('engine_check', {'latest': '2099.12.31', 'current': '2026.10.21', 'newer': True})
    app.refresh_shell()
    failures += sidebar('YouTube status, newer engine')
    failures += status('YouTube status, newer engine', 'shell.status.bot_wait')
    failures += notice('YouTube status, newer engine', True)
    settle(app)
    if app.notice_kind != 'bot' or not app.notice_bar.shown:
        failures.append(f'1000x740 sidebar (YouTube status): the countdown notice is not shown '
                        f'(notice {app.notice_kind!r})')
    if shots:
        grab(app, folder / shot_name('sidebar-1000x740.png', appearance, language))
    app.cancel_bot_retry()
    failures += finished_scene_failures(app, appearance, language, shots, folder)
    return failures


def _finished_bot_job(app, keep_retry=False):
    """(final fix round 1, ENV-1) Twelve links that ended with the YouTube check: 3 done, 1 skipped
    (downloaded before), 8 on hold, the countdown cancelled (``keep_retry``: left running).  The finished job's
    stats are the long form '3 succeeded / 8 unfinished / 12 total / 1 skipped'."""
    links = [f'https://www.youtube.com/watch?v=bot{number:08d}' for number in range(12)]
    app.unfinished = []
    app.populate_queue(links)
    app.operation = 'download'
    app.job_ran = True          # (S4 visual fix) as launch() sets it: the progress bar keeps the job's value
    app.set_busy(True)
    for number, url in enumerate(links, 1):
        app.handle('item', {'index': number, 'total': 12, 'url': url})
        if number <= 3:
            app.handle('item_done', {'index': number, 'success': True, 'cancelled': False, 'files': 1,
                                     'bot_check': False})
        elif number == 4:
            app.handle('item_done', {'index': number, 'success': False, 'cancelled': False, 'files': 0,
                                     'bot_check': False, 'skipped': 'downloaded'})
        else:
            app.handle('item_done', {'index': number, 'success': False, 'cancelled': False, 'files': 0,
                                     'bot_check': True, 'skipped': 'bot_check'})
    app.handle('done', {'success': 3, 'failed': 0, 'files': [], 'cancelled': False, 'pending_urls': links[4:],
                        'bot_check': True, 'blocked': 'bot_check', 'archive_errors': [], 'skipped': 1})
    app.unfinished = [{'url': url, 'key': 'youtube:' + url[-11:], 'format': 'audio_best', 'reason': 'blocked',
                       'title': '', 'error': '', 'time': number, 'options': {}} for number, url in enumerate(links[4:])]
    app.show_unfinished_tail()
    app.refresh_failed_button()
    if not keep_retry:
        app.cancel_bot_retry()


def finished_scene_failures(app, appearance, language, shots, folder):
    """(11, final fix round 1 ENV-1) At 1000x740 the task title fills its line (title_failures), and nothing of
    the task header ends outside the page, after a job that ended with the YouTube check (a skipped link, eight on
    hold), after 清除未完成, and while a web collection and an engine update run after it."""
    from tkinter import messagebox
    failures = []
    scale = _scale_of(app)
    app.geometry('1000x740+0+0')
    app.show_page('download')

    def check(label, name=None):
        settle(app)
        pause(app, 0.1)
        settle(app)
        found = title_failures(app, label)
        widgets = list(checked_widgets(app, 'download'))
        found += [f'1000x740 (11) {label}: {failure}' for failure in card_failures(app, widgets, 'download')]
        found += [f'1000x740 (11) {label}: {failure}' for failure in text_failures(widgets)]
        if language == 'en':                # (1.8.0) (13)
            found += [f'1000x740 (13) {label}: {failure}' for failure in cjk_failures(widgets, app)]
        if shots and name:
            grab(app, folder / shot_name(f'{name}-1000x740.png', appearance, language))
        return found

    _finished_bot_job(app)
    failures += check('job ended with the YouTube check', 'download-finished-bot')
    asked = messagebox.askyesno
    messagebox.askyesno = lambda *_args, **_kwargs: True
    try:
        app.clear_unfinished()
    finally:
        messagebox.askyesno = asked
    failures += check('after Clear unfinished', 'download-finished-cleared')
    for operation in ('collect', 'update'):
        app.operation = operation
        app.set_busy(True)
        failures += check(f'{operation} after the finished job', f'download-finished-{operation}')
        app.set_busy(False)
    app.operation = None
    app.refresh_shell()
    return failures


def stage_scene_failures(app, size, appearance, language, shots, folder):
    """(15, 2.0.0 S4 stage C) The states a page has between empty and busy, in state 'idle': the collect page in
    dynamic mode (the option row with the scrolls menu and the privacy line), the download page with only
    unfinished links (重试未完成 (N) is the solid button), and the empty states of the three library views.
    Each is captured and checked like a page ((9), (9b), (13), one solid button per view)."""
    import ui_theme
    from collection_ui import MODE_DYNAMIC, MODE_STATIC
    failures = []

    def check(page, label, name):
        settle(app)
        pause(app, 0.1)
        widgets = list(checked_widgets(app, page))
        found = [f'{size} {label}: {failure}' for failure in text_failures(widgets)]
        found += [f'{size} {label}: {failure}' for failure in card_failures(app, widgets, page)]
        if language == 'en':
            found += [f'{size} {label}: {failure}' for failure in cjk_failures(widgets, app)]
        solid = [widget for widget in widgets if isinstance(widget, ui_theme.AppButton) and widget.kind == 'primary']
        if len(solid) > 1:
            found.append(f'{size} {label}: {len(solid)} solid buttons')
        if shots:
            grab(app, folder / shot_name(f'{name}-{size}.png', appearance, language))
        return found, solid

    app.show_page('collect')
    app.collect_mode.set(MODE_DYNAMIC)
    found, _solid = check('collect', 'collect (dynamic)', 'collect-dynamic')
    failures += found
    if not app.collect_scrolls_menu.winfo_ismapped() or not app.collect_note.winfo_ismapped():
        failures.append(f'{size} collect (dynamic): the scrolls menu and the privacy line are not shown')
    app.collect_mode.set(MODE_STATIC)
    app.show_page('download')
    saved = list(app.unfinished)
    app.unfinished = _unfinished_entries(3)
    app.show_unfinished_tail()
    app.refresh_failed_button()
    found, solid = check('download', 'download (unfinished only)', 'download-unfinished')
    failures += found
    if solid != [app.retry_button]:
        failures.append(f'{size} download (unfinished only): the solid button is not 重试未完成 (N)')
    app.unfinished = saved
    app.populate_queue([])
    app.refresh_failed_button()
    app.show_page('library')
    for number, tab in enumerate(LIBRARY_TABS, 1):
        app.library_tabs.set(tab)
        settle_tab(app, tab)
        found, _solid = check('library', f'library/{tab} (empty)', f'library-{number}-empty')
        failures += found
    # (S4 visual fix) The log page on Errors while the log holds no error or warn line: "No errors" in the middle
    # of the text, on the text's own fill (SURFACE_ALT), not on a lighter patch.
    app.show_page('logs')
    app.set_errors_only(True)
    found, _solid = check('logs', 'logs (errors only)', 'logs-errors-only')
    failures += found
    inner = app.logs._textbox
    if not (inner.tag_ranges('error') or inner.tag_ranges('warn')):
        if not app.log_empty.winfo_ismapped():
            failures.append(f'{size} logs (errors only): "No errors" is not shown')
        if app.log_empty.cget('fg_color') != ui_theme.SURFACE_ALT:
            failures.append(f'{size} logs (errors only): "No errors" is not on SURFACE_ALT')
    app.set_errors_only(False)
    return failures


def _luminance(color):
    value = str(color).lstrip('#')
    if len(value) != 6:
        return 0.0
    channels = []
    for index in (0, 2, 4):
        channel = int(value[index:index + 2], 16) / 255
        channels.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def dark_failures(app):
    """(WP7) Assertions of the dark mode, on the whole window (sidebar and every page and tab)."""
    import ui_theme
    failures = []

    def dark(value):
        return str(value[1] if isinstance(value, (list, tuple)) else value).lower()
    for widget in widgets_below(app):
        if isinstance(widget, (ctk.CTkBaseClass, ctk.CTkScrollableFrame)):
            for option in ('fg_color', 'bg_color'):
                try:
                    value = widget.cget(option)
                except (ValueError, tk.TclError, AttributeError, KeyError):
                    continue
                if isinstance(value, (list, tuple)) and tuple(value) == ui_theme.WHITE:
                    continue        # a text token, never a surface
                if dark(value) in LIGHT_SURFACES:
                    failures.append(f'dark: {widget} {option} = {value}')
        if not isinstance(widget, ctk.CTkBaseClass) and isinstance(
                widget, (tk.Canvas, tk.Frame, tk.Label, tk.Text, tk.Listbox, tk.Entry)):
            # plain Tk widgets (the inner canvases and text widgets of customtkinter included)
            try:
                value = str(widget.tk.call(widget._w, 'cget', '-background'))
                if value.startswith('#') and len(value) == 4:
                    value = '#' + ''.join(part * 2 for part in value[1:])
                elif not value.startswith('#'):
                    value = '#%02x%02x%02x' % tuple(part // 256 for part in widget.winfo_rgb(value))
            except tk.TclError:
                continue
            if value.lower() in LIGHT_SURFACES or _luminance(value) > 0.4:
                failures.append(f'dark: {widget} background = {value}')
    style = ttk.Style(app)
    for name in ('App.Treeview', 'Collect.Treeview'):
        for option, token in (('background', ui_theme.SURFACE), ('fieldbackground', ui_theme.SURFACE),
                              ('foreground', ui_theme.INK)):
            found = str(style.lookup(name, option)).upper()
            if found != token[1]:
                failures.append(f'dark: {name} {option} = {found}, expected {token[1]}')
        found = str(style.lookup(name + '.Heading', 'background')).upper()
        if found != ui_theme.SURFACE_ALT[1]:
            failures.append(f'dark: {name}.Heading background = {found}, expected {ui_theme.SURFACE_ALT[1]}')
    for widget in widgets_below(app):
        if isinstance(widget, ttk.Treeview) and hasattr(widget, 'restripe'):
            found = str(widget.tag_configure('odd', 'background')).upper()
            if found != ui_theme.STRIPE[1]:
                failures.append(f'dark: {widget} stripe = {found}, expected {ui_theme.STRIPE[1]}')
    found = str(app.progress.canvas.cget('background')).upper()
    if found != ui_theme.SURFACE[1]:
        failures.append(f'dark: progress canvas background = {found}, expected {ui_theme.SURFACE[1]}')
    for tag, token in (('error', ui_theme.DANGER), ('warn', ui_theme.WARN), ('ok', ui_theme.OK),
                       ('dim', ui_theme.MUTED), ('app', ui_theme.INK)):
        found = str(app.logs.tag_cget(tag, 'foreground')).upper()
        if found != token[1]:
            failures.append(f'dark: log tag {tag} = {found}, expected {token[1]}')
    return failures


def run(pages=None, sizes=SIZES, shots=True, out=None, state='idle', appearance='light', language=None):
    """Capture and check the pages; returns the failed assertions (empty when everything passes).

    (1.7.0) ``language`` 'zh' or 'en': the run happens in that language (_sandbox.language, also for
    the processes it starts), English PNG names end in '-en', and assertions (9) to (12) are added.
    None keeps the process language and the 1.6.0 assertions (1) to (8)."""
    if language is None:
        return _run(pages, sizes, shots, out, state, appearance, None)
    if language not in LANGUAGES:
        raise ValueError(f'language must be one of {LANGUAGES}, not {language!r}')
    with _sandbox.language(language):
        return _run(pages, sizes, shots, out, state, appearance, language)


def _run(pages, sizes, shots, out, state, appearance, language):
    import engine
    import formats
    import ui_theme
    from studio import StudioApp
    extended = language is not None
    pages = list(pages or PAGES)
    checked = [page for page in pages if page in CHECKED]
    folder = Path(out) if out else _sandbox.OUT / 'shots'
    failures = []
    ctk.set_appearance_mode(appearance)
    output = _sandbox.out('snapshot-output')
    if state == 'filled':
        # (WP5) the filled scene writes into the smoke history; it gets a folder (and history) of its own.
        output = _sandbox.out('snapshot-filled-output')
        for leftover in output.glob('test-library.sqlite3*'):
            leftover.unlink()
    arguments = _sandbox.app_args('https://example.invalid/snapshot', str(output))
    arguments.appearance = appearance       # (WP7) StudioApp.__init__ applies it, as app.py does
    app = StudioApp(arguments)
    try:
        app.attributes('-alpha', 0.0)
        app.overrideredirect(True)
        app.urls.delete('1.0', 'end')
        app.mode.set(formats.PRESETS[0].id)
        app.on_format()
        app.links_changed()
        scale = ctk.ScalingTracker.get_widget_scaling(app)
        if state == 'active':
            active_scene(app)
        elif state == 'bot':
            bot_scene(app)
        elif state == 'filled':
            filled_scene(app)

        def logical(pixels):
            return pixels / scale

        for size in sizes:
            app.geometry(size + '+0+0')
            settle(app)
            width, height = (int(part) for part in size.split('x'))
            for page in pages:
                app.show_page(page)
                settle(app)
                if shots:
                    # The link box decides on its scrollbar in a 200 ms loop of its own; a capture taken
                    # earlier shows the bar it had while the window was still being laid out.
                    pause(app, 0.25)
                tabs = LIBRARY_TABS if page == 'library' and hasattr(app, 'library_tabs') else (None,)
                for tab in tabs:
                    if tab is not None:
                        app.library_tabs.set(tab)
                        settle_tab(app, tab)
                    if shots:
                        name = f"{page}{'-' + str(LIBRARY_TABS.index(tab) + 1) if tab else ''}-{size}.png"
                        if not grab(app, folder / shot_name(name, appearance, language)):
                            print(f'capture skipped: {name}', flush=True)
                    # (1.7.0) (9) texts and (9b) card edges of the shown page / tab
                    if extended and page in checked:
                        where = f"{size} {page}{'/' + tab if tab else ''}"
                        widgets = list(checked_widgets(app, page))
                        failures += [f'{where}: {failure}' for failure in text_failures(widgets)]
                        failures += [f'{where}: {failure}' for failure in card_failures(app, widgets, page)]
                        if language == 'en':        # (1.8.0) (13) no Chinese outside user data
                            failures += [f'{where}: {failure}'
                                         for failure in cjk_failures(widgets + list(sidebar_widgets(app)), app)]
                    # (12, 2.0.0 S4) nothing of the new-items line (count, 加入下载, 忽略) is cut off at its right edge
                    if tab == LIBRARY_TABS[1]:
                        line = app.pending_bar
                        for child in line.winfo_children():
                            if child.winfo_ismapped() and child.winfo_x() + child.winfo_width() > line.winfo_width() + TOLERANCE:
                                failures.append(f'{size}: {child} ends {logical(child.winfo_x() + child.winfo_width() - line.winfo_width()):.0f} px '
                                                f'right of the new-items line')
                # (14) the header holds only the title
                if page in checked and tab in (None, LIBRARY_TABS[0]):
                    failures += [f'{size}: {failure}' for failure in header_failures(app, page)]
                # (1) the page fits the content area at the minimum size
                if (width, height) == (1000, 740) and page in checked:
                    request, room = app.pages[page].winfo_reqheight(), app.content.winfo_height()
                    if request > room + 1:
                        failures.append(f'{size} {page}: page asks for {logical(request):.0f} px, '
                                        f'content has {logical(room):.0f} px')
            if 'download' in pages:
                app.show_page('download')
                settle(app)
                listed = bool(app.row_model)
                viewport = logical(app.task_list.canvas.winfo_height())
                limit = {(1000, 740): 300, (1120, 810): 380}.get((width, height))
                # (2) the task list viewport; (2.0.0 S4) no rows: the empty state instead of the list and its header
                if not listed:
                    if not app.empty.winfo_manager() or app.task_header.winfo_manager():
                        failures.append(f'{size}: no rows, but the empty state is not shown in place of the list')
                elif limit is not None and viewport < limit and state != 'active':
                    failures.append(f'{size}: task list viewport {viewport:.0f} px < {limit}')
                # (3) long texts do not move anything (the list is compared when it was shown already)
                before = (app.composer.winfo_height(), app.task_header.winfo_height(),
                          app.task_list.canvas.winfo_height() if listed else 0)
                saved = (list(app.row_model), app.active_index, app.operation, ui_theme.full_text(app.metrics))
                app.append_rows(['https://example.invalid/long-title'])
                app.operation, app.active_index = 'download', len(app.row_model) - 1
                app.handle('title', TITLE_60)
                app.metrics.configure(text=METRICS_120)
                settle(app)
                after = (app.composer.winfo_height(), app.task_header.winfo_height(),
                         app.task_list.canvas.winfo_height() if listed else 0)
                if not listed:
                    before = (before[0],) + after[1:]       # the header and the list appear with the first row
                if any(abs(a - b) > scale for a, b in zip(before, after)):
                    failures.append(f'{size}: long title/metrics changed heights {before} -> {after}')
                app.populate_queue([model['url'] for model in saved[0]])
                for number, model in enumerate(saved[0]):
                    app.update_row(number, **{k: v for k, v in model.items() if k != 'index'})
                app.active_index, app.operation = saved[1], saved[2]
                app.refresh_task_view()
                app.metrics.configure(text=saved[3])
                # (7) the page cap
                if (width, height) == (2560, 1392):
                    composer = logical(app.composer.winfo_width())
                    if composer > 1081:
                        failures.append(f'{size}: composer card is {composer:.0f} px wide (cap 1080)')
                # (8) the bot-check sentence fits the metrics line at the minimum size
                if (width, height) == (1000, 740):
                    keep = (ui_theme.full_text(app.metrics), app.current_title.cget('text'))
                    # (1.7.0) the sentence of the running language
                    import i18n
                    bot_text = i18n.t('download.metrics.bot')
                    app.metrics.configure(text=bot_text)
                    settle(app)
                    if app.metrics.cget('text') != ui_theme.full_text(app.metrics):
                        failures.append(f'{size}: bot-check sentence cut to {app.metrics.cget("text")!r}')
                    app.metrics.configure(text=keep[0])
            # (15) the in-between states of the pages (the idle window has no history: the library views are empty)
            if extended and state == 'idle' and {'download', 'collect', 'library'} <= set(pages):
                failures += stage_scene_failures(app, size, appearance, language, shots, folder)
            if 'settings' in pages and (width, height) in ((1000, 740), (1120, 810)):
                app.show_page('settings')
                settle(app)
                area = app.settings_area
                room = area.canvas.winfo_width()
                if area.winfo_reqwidth() > room + 1:
                    failures.append(f'{size}: the settings grid asks {logical(area.winfo_reqwidth()):.0f} px, '
                                    f'viewport {logical(room):.0f} px')
                edge = area.canvas.winfo_rootx() + room
                for child in area.winfo_children():
                    if child.winfo_ismapped() and child.winfo_rootx() + child.winfo_width() > edge + TOLERANCE:
                        failures.append(f'{size}: settings row {_shown(child)} ends '
                                        f'{logical(child.winfo_rootx() + child.winfo_width() - edge):.0f} px right of the viewport')
                if not area.bar_visible:
                    failures.append(f'{size}: settings scrollbar is not shown')
                area.canvas.yview_moveto(1.0)
                settle(app)
                last = app.about_rows[-1][1]
                bottom = last.winfo_rooty() + last.winfo_height()
                view_bottom = area.canvas.winfo_rooty() + area.canvas.winfo_height()
                if bottom > view_bottom + 1:
                    failures.append(f'{size}: the last About row ends {logical(bottom - view_bottom):.0f} px below the viewport')
                if shots:
                    grab(app, folder / shot_name(f'settings-end-{size}.png', appearance, language))
                if extended:
                    # (9) the rows scrolled into view last (they are checked at the top as well)
                    widgets = list(checked_widgets(app, 'settings'))
                    failures += [f'{size} settings (end): {failure}' for failure in text_failures(widgets)]
                area.canvas.yview_moveto(0.0)
                settle(app)
        # (5) table text lines
        body = ui_theme._measure_font(app, ui_theme.font('body')).metrics('linespace')
        for widget in widgets_below(app):
            if isinstance(widget, ttk.Treeview):
                style = widget.cget('style') or 'Treeview'
                spec = ttk.Style(app).lookup(style, 'font')
                parts = app.tk.splitlist(spec) if spec else ()
                if len(parts) >= 2:
                    line = tkfont.Font(root=app, family=parts[0], size=int(parts[1])).metrics('linespace')
                    if line < 0.85 * body:
                        failures.append(f'{widget}: table line {line} px < 0.85 x body {body} px')
        # (6) stock colours
        for page in checked:
            for widget in widgets_below(app.pages[page]):
                for option in ('fg_color', 'border_color'):
                    try:
                        value = widget.cget(option)
                    except (ValueError, tk.TclError, AttributeError, KeyError):
                        continue
                    values = value if isinstance(value, (list, tuple)) else (value,)
                    if any(str(item).lower() in STOCK for item in values):
                        failures.append(f'{page}: {widget} {option} = {value}')
        # (WP7) dark mode: no light surface, ttk / plain-Tk colours in their dark values
        if appearance == 'dark':
            failures += dark_failures(app)
        # (1.7.0) (10) sidebar texts and (11) the task title, in the active scene (last: it changes the job)
        if extended and state == 'active' and 'download' in pages:
            failures += scene_failures(app, appearance, language, shots, folder)
    finally:
        app.destroy()
        if appearance != 'light':
            ctk.set_appearance_mode('light')     # (WP7) the next app of this process starts light again
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--pages', default=','.join(PAGES))
    parser.add_argument('--sizes', default=','.join(SIZES))
    parser.add_argument('--state', choices=('idle', 'active', 'bot', 'filled'), default='idle')
    parser.add_argument('--out')
    parser.add_argument('--no-shots', action='store_true')
    parser.add_argument('--appearance', choices=APPEARANCES + ('both',), default='light')     # (WP7)
    # (1.7.0) zh / en / both: assertions (9) to (12) and '-en' PNG names; left out: the process language
    # and the 1.6.0 assertions only.
    parser.add_argument('--language', choices=LANGUAGES + ('both',))
    options = parser.parse_args(argv)
    failures = []
    languages = LANGUAGES if options.language == 'both' else (options.language,)
    for language in languages:
        for appearance in (APPEARANCES if options.appearance == 'both' else (options.appearance,)):
            found = run([page for page in options.pages.split(',') if page], tuple(options.sizes.split(',')),
                        not options.no_shots, options.out, options.state, appearance, language)
            failures += [f'[{language}] {failure}' if language else failure for failure in found]
    for failure in failures:
        print('FAIL', failure, flush=True)
    print('snapshot_ui:', 'ok' if not failures else f'{len(failures)} failed', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
