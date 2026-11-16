"""Screenshots and layout assertions of the StudioApp pages (1.6.0 WP4, SPEC 6).

    .venv\\Scripts\\python.exe -B verification\\snapshot_ui.py [--pages download,collect,library,logs,settings]
                                                         [--sizes 1000x740,1120x810,2560x1392]
                                                         [--state idle|active|bot|filled] [--out DIR] [--no-shots]
                                                         [--appearance light|dark|both]

run(pages=None, sizes=(...), shots=True) builds one smoke-mode StudioApp (light mode, invisible:
alpha 0 and no window frame), empties the link box, selects MODES[0], and for every size and
page (and every tab of the library page) captures the window with PrintWindow into
VERIFY_OUT/shots (or --out).  It returns the list of failed assertions (empty = all pass) and
destroys its app before it returns.  A process never holds two live StudioApp instances:
call run() only after the calling file has destroyed its own app.

Assertions (WP4.md section H): (1) at 1000x740 no page asks for more height than the content
area has; (2) task list viewport >= 300 logical px at 1000x740, >= 380 at 1120x810; (3) a
60-character title and a 120-character metrics text change neither the composer card, the task
header nor the viewport (+-1 px); (4) settings cards fit the scroll viewport horizontally, the
scrollbar is shown and the last card can be scrolled fully into view; (5) every Treeview line is
at least 0.85 of the body text line; (6) no widget of a checked page keeps the stock colours
#3B8ED0 / #979DA2; (7) at 2560x1392 the composer card is at most 1081 logical px wide; (8) at
1000x740 the full bot-check sentence fits the metrics line.
WP5: assertions (1) and (6) cover all five pages (CHECKED); one more check: nothing in the creation
strip of the library's subscription tab ends right of the strip.  State 'filled' (filled_scene) puts
collected links, history jobs, subscriptions with new entries and archive rows on the collect and
library pages; it uses its own smoke output folder and history.
"""
import _sandbox
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

import customtkinter as ctk
from PIL import Image

PAGES = ('download', 'collect', 'library', 'logs', 'settings')
CHECKED = ('download', 'settings', 'logs', 'collect', 'library')     # WP4 checked the first three; WP5 added the others
SIZES = ('1000x740', '1120x810', '2560x1392')
LIBRARY_TABS = ('任务历史与续采', '增量订阅', '统一资料归档')
STOCK = ('#3b8ed0', '#979da2')
TITLE_60 = '周杰伦 - 晴天 (Official Music Video) 【高音质 · 动态歌词 Lyrics】「每日推荐」 #63 加长版'[:60]
METRICS_120 = ('网站限流，暂停整个下载队列 · 剩余 04:59 · 可随时停止 · ' * 4)[:120]
# (WP7) run(..., appearance='dark') / --appearance dark|both: the same captures and assertions in dark
# mode, PNG names with a '-dark' suffix, plus dark_failures(): no widget shows a light surface, the
# table styles, stripes, log tags and progress canvas carry the dark token values.
APPEARANCES = ('light', 'dark')
LIGHT_SURFACES = ('#ffffff', '#f4f7fc', '#f7f9fd')


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
    app.handle('item_done', {'index': 4, 'success': False, 'cancelled': False, 'files': 0, 'bot_check': False,
                             'reason': '视频不可用（已删除或设为私享）'})
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
                 '此链接未完成（退出码 1），继续处理队列中的下一项。', '[ExtractAudio] Destination: 白羊.m4a',
                 '文件已保存：' + str(_sandbox.out('白羊.m4a'))):
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
    from engine import MODES
    source = 'https://www.example.org/podcast/list'
    links = [{'url': f'https://media.example.org/podcast/episode-{number:03d}.m4a',
              'title': f'第 {number} 期 · 城市夜谈：在凌晨两点仍然醒着的人', 'kind': '媒体地址', 'source': source}
             for number in range(1, 15)]
    links += [{'url': 'https://www.youtube.com/watch?v=OQMcYxMlwGA', 'title': '徐秉龙 - 双子 【Lyrics MV】', 'kind': '视频页面',
               'source': source},
              {'url': 'https://www.example.org/podcast/about', 'title': '关于本节目', 'kind': '网页链接', 'source': source}]
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
            ('订阅：城市夜谈', 'collect', 'completed', []),
            ('李翊君 雨蝶 歌詞版', 'download', 'completed', []),
            (source, 'collect', 'interrupted', ['网站返回HTTP 503，可尝试动态模式或稍后重试。']),
            ('周杰伦 - 晴天 (Official MV)', 'download', 'interrupted', [])]:
        job = store.create_job(kind, title, {'urls': [source], 'output': str(_sandbox.out('snapshot-output')),
                                             'mode': MODES[0]})
        store.change_job(job, status=status, errors=errors)
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


def shot_name(name, appearance):
    """(WP7) 'download-1000x740.png' -> 'download-1000x740-dark.png' in dark mode."""
    return name if appearance == 'light' else name[:-len('.png')] + f'-{appearance}.png'


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


def run(pages=None, sizes=SIZES, shots=True, out=None, state='idle', appearance='light'):
    """Capture and check the pages; returns the failed assertions (empty when everything passes)."""
    import engine
    import ui_download
    import ui_theme
    from studio import StudioApp
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
        app.format.set(ui_download.FORMATS[0])
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
                        if not grab(app, folder / shot_name(name, appearance)):
                            print(f'capture skipped: {name}', flush=True)
                    # (WP5) nothing in the subscription tab's creation strip is cut off at its right edge
                    if tab == LIBRARY_TABS[1] and hasattr(app, 'subscription_strip'):
                        strip = app.subscription_strip
                        for child in strip.winfo_children():
                            if child.winfo_ismapped() and child.winfo_x() + child.winfo_width() > strip.winfo_width():
                                failures.append(f'{size}: {child} ends {logical(child.winfo_x() + child.winfo_width() - strip.winfo_width()):.0f} px '
                                                f'right of the subscription strip')
                # (1) the page fits the content area at the minimum size
                if (width, height) == (1000, 740) and page in checked:
                    request, room = app.pages[page].winfo_reqheight(), app.content.winfo_height()
                    if request > room + 1:
                        failures.append(f'{size} {page}: page asks for {logical(request):.0f} px, '
                                        f'content has {logical(room):.0f} px')
            if 'download' in pages:
                app.show_page('download')
                settle(app)
                viewport = logical(app.task_list.canvas.winfo_height())
                limit = {(1000, 740): 300, (1120, 810): 380}.get((width, height))
                # (2) the task list viewport
                if limit is not None and viewport < limit and state != 'active':
                    failures.append(f'{size}: task list viewport {viewport:.0f} px < {limit}')
                # (3) long texts do not move anything
                before = (app.composer.winfo_height(), app.task_header.winfo_height(),
                          app.task_list.canvas.winfo_height())
                saved = (list(app.row_model), app.active_index, app.operation, ui_theme.full_text(app.metrics))
                app.append_rows(['https://example.invalid/long-title'])
                app.operation, app.active_index = 'download', len(app.row_model) - 1
                app.handle('title', TITLE_60)
                app.metrics.configure(text=METRICS_120)
                settle(app)
                after = (app.composer.winfo_height(), app.task_header.winfo_height(),
                         app.task_list.canvas.winfo_height())
                if any(abs(a - b) > scale for a, b in zip(before, after)):
                    failures.append(f'{size}: long title/metrics changed heights {before} -> {after}')
                app.populate_queue([model['url'] for model in saved[0]])
                for number, model in enumerate(saved[0]):
                    app.update_row(number, **{k: v for k, v in model.items() if k != 'index'})
                app.active_index, app.operation = saved[1], saved[2]
                app.refresh_stats()
                app.metrics.configure(text=saved[3])
                # (7) the page cap
                if (width, height) == (2560, 1392):
                    composer = logical(app.composer.winfo_width())
                    if composer > 1081:
                        failures.append(f'{size}: composer card is {composer:.0f} px wide (cap 1080)')
                # (8) the bot-check sentence fits the metrics line at the minimum size
                if (width, height) == (1000, 740):
                    keep = (ui_theme.full_text(app.metrics), app.current_title.cget('text'))
                    app.metrics.configure(text=ui_download.BOT_TEXT)
                    settle(app)
                    if app.metrics.cget('text') != ui_theme.full_text(app.metrics):
                        failures.append(f'{size}: bot-check sentence cut to {app.metrics.cget("text")!r}')
                    app.metrics.configure(text=keep[0])
            if 'settings' in pages and (width, height) in ((1000, 740), (1120, 810)):
                app.show_page('settings')
                settle(app)
                area = app.settings_area
                room = area.canvas.winfo_width()
                for card in app.settings_cards:
                    if card.winfo_reqwidth() > room + 1:
                        failures.append(f'{size}: settings card asks {logical(card.winfo_reqwidth()):.0f} px, '
                                        f'viewport {logical(room):.0f} px')
                if not area.bar_visible:
                    failures.append(f'{size}: settings scrollbar is not shown')
                area.canvas.yview_moveto(1.0)
                settle(app)
                last = app.settings_cards[-1]
                bottom = last.winfo_rooty() + last.winfo_height()
                view_bottom = area.canvas.winfo_rooty() + area.canvas.winfo_height()
                if bottom > view_bottom + 1:
                    failures.append(f'{size}: last settings card ends {logical(bottom - view_bottom):.0f} px below the viewport')
                if shots:
                    grab(app, folder / shot_name(f'settings-end-{size}.png', appearance))
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
    options = parser.parse_args(argv)
    failures = []
    for appearance in (APPEARANCES if options.appearance == 'both' else (options.appearance,)):
        failures += run([page for page in options.pages.split(',') if page], tuple(options.sizes.split(',')),
                        not options.no_shots, options.out, options.state, appearance)
    for failure in failures:
        print('FAIL', failure, flush=True)
    print('snapshot_ui:', 'ok' if not failures else f'{len(failures)} failed', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
