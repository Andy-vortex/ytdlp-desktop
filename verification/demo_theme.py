"""Visual demo of ui_theme: every factory in every state on two pages.

    python -X utf8 -B demo_theme.py                             open the window
    python -X utf8 -B demo_theme.py --shots DIR [--size WxH]    render invisibly, save two PNGs and
                                                                measurements-WxH.json into DIR

DIR must lie outside the source tree. The demo imports ui_theme only, never an app module.
"""
import argparse
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

sys.dont_write_bytecode = True         # never leave a __pycache__ in src/
TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / 'src'))

import customtkinter as ctk  # noqa: E402
from PIL import Image  # noqa: E402

import ui_theme as t  # noqa: E402

TITLE = '徐秉龍、沈以誠 - 白羊『青春一記荒唐 亦然學著瘋狂。』【動態歌詞Lyrics】「每日歌曲推薦 #63」 Official Lyric Video [xrooMGuRhgY]'
NAV = [('下载中心', t.ICON_DOWNLOAD, 'widgets'), ('网页采集', t.ICON_SEARCH, None), ('资料管理', t.ICON_LIBRARY, 'tables'),
       ('任务日志', t.ICON_LOG, None), ('偏好设置', t.ICON_SETTINGS, None)]
ICONS = [t.ICON_DOWNLOAD, t.ICON_SEARCH, t.ICON_LIBRARY, t.ICON_LOG, t.ICON_SETTINGS, t.ICON_DONE, t.ICON_CLOCK,
         t.ICON_ERROR, t.ICON_PAUSE, t.ICON_FOLDER, t.ICON_SKIP]


class Demo(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=t.BG)
        self.title('ui_theme 演示')
        self.minsize(1000, 740)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.probe = {}             # widgets the measurements read
        self.nav = {}
        self.build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        self.content.grid(row=0, column=1, sticky='nsew', padx=t.PAGE_PAD, pady=16)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {'widgets': self.build_widgets_page(), 'tables': self.build_tables_page()}
        t.set_page_cap(self, self.content, {self.pages['widgets']: t.PAGE_MAX, self.pages['tables']: None})
        self.show('widgets')

    # ---- shell
    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=t.SIDEBAR_W, fg_color=t.SIDE, corner_radius=0)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(8, weight=1)
        brand = ctk.CTkFrame(side, fg_color='transparent', corner_radius=0, width=0, height=0)
        brand.grid(row=0, column=0, sticky='ew', padx=16, pady=(24, 24))
        mark = ctk.CTkLabel(brand, text='', image=t.icon(t.ICON_DOWNLOAD, 'white', 18), width=36, height=36,
                            corner_radius=10, fg_color=t.ACCENT)
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        t.label(brand, 'YT-DLP', role='brand', text_color=t.WHITE, anchor='w').grid(row=0, column=1, sticky='w')
        for row, (title, code, page) in enumerate(NAV, 1):
            item = ctk.CTkButton(side, text=title, image=t.icon(code, 'side'), compound='left', anchor='w',
                                 font=t.font('body'), height=40, corner_radius=t.RADIUS, border_spacing=10,
                                 fg_color='transparent', hover_color=t.SIDE_HOVER, text_color=t.SIDE_TEXT,
                                 command=(lambda name=page: self.show(name)) if page else None)
            item.grid(row=row, column=0, sticky='ew', padx=12, pady=2)
            self.nav[title] = (item, code, page)
        t.label(side, '●  下载中', role='caption', text_color=t.SIDE_INFO, anchor='w').grid(
            row=9, column=0, sticky='ew', padx=24, pady=(12, 16))

    def show(self, name):
        self.pages[name].tkraise()
        for item, code, page in self.nav.values():
            active = page == name
            item.configure(fg_color=t.SIDE_ACTIVE if active else 'transparent',
                           text_color=t.WHITE if active else t.SIDE_TEXT,
                           image=t.icon(code, 'white' if active else 'side'))

    def page(self, title):
        page = ctk.CTkFrame(self.content, fg_color='transparent', corner_radius=0)
        page.grid(row=0, column=0, sticky='nsew')
        page.grid_columnconfigure(0, weight=1)
        t.label(page, title, role='title', anchor='w').grid(row=0, column=0, sticky='w', pady=(0, t.SECTION))
        return page

    def section(self, page, row, title, weight=0):
        """A group: its small name, then its body; white space separates groups (no card, 00-system 2.5)."""
        group = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0, width=0, height=0)
        group.grid(row=row, column=0, sticky='nsew' if weight else 'ew', pady=(0, t.SECTION))
        group.grid_columnconfigure(0, weight=1)
        if weight:
            page.grid_rowconfigure(row, weight=weight)
        t.section_label(group, title).grid(row=0, column=0, sticky='w', pady=(0, t.INLINE))
        body = ctk.CTkFrame(group, fg_color='transparent', corner_radius=0, width=0, height=0)
        body.grid(row=1, column=0, sticky='nsew')
        if weight:
            group.grid_rowconfigure(1, weight=1)
        return body

    # ---- page 1: buttons, chips, icons, inputs
    def build_widgets_page(self):
        page = self.page('控件')
        body = self.section(page, 1, '按钮')
        for column, kind in enumerate(('primary', 'soft', 'ghost', 'danger'), 1):
            t.label(body, kind, role='caption', text_color=t.MUTED).grid(row=0, column=column, padx=(0, 8), sticky='w')
        rows = [('md', 'normal', '默认 36'), ('md', 'disabled', '禁用 36'), ('sm', 'normal', '小号 28'), ('sm', 'disabled', '禁用 28')]
        texts = {'primary': '开始下载', 'soft': '打开文件夹', 'ghost': '导入 TXT', 'danger': '停止'}
        for row, (size, state, caption) in enumerate(rows, 1):
            t.label(body, caption, role='caption', text_color=t.MUTED, anchor='w', width=64).grid(row=row, column=0, sticky='w', pady=3)
            for column, kind in enumerate(('primary', 'soft', 'ghost', 'danger'), 1):
                item = t.button(body, texts[kind], None, kind=kind, size=size, state=state)
                item.grid(row=row, column=column, padx=(0, 8), pady=3, sticky='w')
                self.probe[f'button_{kind}_{size}_{state}'] = item
        extra = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0, width=0, height=0)
        extra.grid(row=1, column=5, rowspan=4, sticky='ne', padx=(16, 0))
        body.grid_columnconfigure(5, weight=1)
        with_icon = t.button(extra, '开始下载', None, kind='primary', width=132, image=t.icon(t.ICON_DOWNLOAD, 'white'), compound='left')
        with_icon.grid(row=0, column=0, pady=3, sticky='e')
        later = t.button(extra, '加入队列', None, kind='primary', width=132)
        later.grid(row=1, column=0, pady=3, sticky='e')
        later.configure(state='disabled')       # disabled after creation, unlike the grid on the left
        folder = t.button(extra, '打开文件夹', None, width=132, image=t.icon(t.ICON_FOLDER, 'accent'), compound='left')
        folder.grid(row=2, column=0, pady=3, sticky='e')

        body = self.section(page, 2, '状态、图标与输入')
        body.grid_columnconfigure(0, weight=1)
        chips = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0, width=0, height=0)
        chips.grid(row=0, column=0, sticky='w')
        for column, (text, tone) in enumerate([('等待中', 'idle'), ('42%', 'info'), ('已完成', 'ok'), ('限流等待', 'warn'), ('失败', 'danger')]):
            item = t.chip(chips, text, tone)
            item.grid(row=0, column=column, padx=(0, 8))
            self.probe[f'chip_{tone}'] = item
        icons = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0, width=0, height=0)
        icons.grid(row=1, column=0, sticky='w', pady=(10, 0))
        tones = ['ink', 'muted', 'faint', 'accent', 'ok', 'warn', 'danger']
        for column, code in enumerate(ICONS):
            ctk.CTkLabel(icons, text='', image=t.icon(code, tones[column % len(tones)]), width=28, height=24).grid(row=0, column=column)
        dark = ctk.CTkFrame(icons, fg_color=t.SIDE, corner_radius=t.RADIUS, width=0, height=0)
        dark.grid(row=0, column=len(ICONS), padx=(12, 0))
        for column, code in enumerate(ICONS[:5]):
            ctk.CTkLabel(dark, text='', image=t.icon(code, 'white' if column == 0 else 'side'), width=28, height=24).grid(row=0, column=column, padx=(4 if column == 0 else 0, 4 if column == 4 else 0), pady=2)

        fields = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0, width=0, height=0)
        fields.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        fields.grid_columnconfigure(1, weight=1)
        self.path = ctk.StringVar(self, 'D:/下载文件')
        self.empty_text = ctk.StringVar(self, '')
        self.locked = ctk.StringVar(self, '下载时不可改')
        filled = t.entry(fields, textvariable=self.path, width=150)
        filled.grid(row=0, column=0, sticky='w')
        hinted = t.entry(fields, placeholder='如 http://127.0.0.1:7890', textvariable=self.empty_text)
        hinted.grid(row=0, column=1, sticky='ew', padx=8)
        t.entry(fields, textvariable=self.locked, width=130, state='disabled').grid(row=0, column=2)
        menu = t.option_menu(fields, ['音频 · 原始最佳', '音频 · FLAC', '视频 · MP4 兼容'], width=160)
        menu.grid(row=0, column=3, padx=8)
        t.option_menu(fields, ['1080p'], width=90, state='disabled').grid(row=0, column=4)
        toggles = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0, width=0, height=0)
        toggles.grid(row=3, column=0, sticky='w', pady=(10, 0))
        self.flags = [ctk.BooleanVar(self, value) for value in (False, True, True, False, True, False, True)]
        box_off = t.checkbox(toggles, '整个播放列表', variable=self.flags[0])
        box_on = t.checkbox(toggles, '内嵌字幕', variable=self.flags[1])
        box_disabled = t.checkbox(toggles, '不可用', variable=self.flags[2], state='disabled')
        switch_off = t.switch(toggles, '关', variable=self.flags[3])
        switch_on = t.switch(toggles, '开', variable=self.flags[4])
        bare_off = t.switch(toggles)
        bare_on = t.switch(toggles)
        bare_on.select()
        locked_off = t.switch(toggles, '禁用', variable=self.flags[5], state='disabled')
        locked_on = t.switch(toggles, '禁用', variable=self.flags[6], state='disabled')
        for column, item in enumerate((box_off, box_on, box_disabled, switch_off, switch_on, bare_off, bare_on, locked_off, locked_on)):
            item.grid(row=0, column=column, padx=(0, 16))
        self.probe.update(entry=filled, entry_hint=hinted, option_menu=menu, checkbox=box_on, switch=switch_on,
                          switch_bare=bare_on, switch_off=switch_off)

        body = self.section(page, 3, '文本框、截断与空状态', weight=1)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(1, weight=1)
        box = t.textbox(body, height=72, wrap='none')
        box.insert('1.0', 'https://www.youtube.com/watch?v=xrooMGuRhgY\nhttps://www.youtube.com/watch?v=OQMcYxMlwGA')
        box.grid(row=0, column=0, sticky='ew', padx=(0, t.GAP))
        narrow = ctk.CTkFrame(body, fg_color=t.ROW, corner_radius=t.RADIUS, height=t.ROW_H)
        narrow.grid(row=1, column=0, sticky='new', padx=(0, t.GAP), pady=(t.GAP, 0))
        narrow.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(narrow, text='', image=t.icon(t.ICON_DONE, 'ok'), width=32).grid(row=0, column=0, rowspan=2, padx=(8, 2))
        fitted = t.label(narrow, anchor='w')
        fitted.grid(row=0, column=1, sticky='ew', pady=(6, 0))
        t.fit(fitted, TITLE)
        detail = t.label(narrow, '01  ·  已保存 1 个文件 · 3.6 MB · opus', role='caption', text_color=t.MUTED, anchor='w')
        detail.grid(row=1, column=1, sticky='ew', pady=(0, 6))
        t.chip(narrow, '已完成', 'ok').grid(row=0, column=2, rowspan=2, padx=(8, 12))
        empty = t.empty_state(body, '你的下载任务会显示在这里', t.ICON_DOWNLOAD)
        empty.grid(row=0, column=1, rowspan=2, sticky='nsew')
        self.probe.update(fit_label=fitted, textbox=box, task_row=narrow, empty_state=empty)
        return page

    # ---- page 2: tabs, tables, setting rows in a scroll area
    def build_tables_page(self):
        page = self.page('表格与设置')
        page.grid_rowconfigure(1, weight=1)
        tabs = t.tabs(page, ['任务历史与续采', '增量订阅', '统一资料归档'])
        tabs.grid(row=1, column=0, sticky='nsew', pady=(0, t.GAP))
        history = tabs.add('任务历史与续采')
        history.grid_columnconfigure(0, weight=1)
        history.grid_rowconfigure(1, weight=3)
        history.grid_rowconfigure(2, weight=2)
        bar = ctk.CTkFrame(history, fg_color='transparent', corner_radius=0, width=0, height=0)
        bar.grid(row=0, column=0, sticky='ew', padx=10, pady=(0, t.GAP))
        t.button(bar, '刷新记录', None).pack(side='left')
        t.button(bar, '查看结果', None, state='disabled').pack(side='left', padx=8)
        t.button(bar, '继续未完成', None, kind='primary', state='disabled').pack(side='left')
        t.label(bar, '已完成页不会重采；失败页和中断页可以继续。', role='caption', text_color=t.MUTED).pack(side='left', padx=12)
        filled = t.table(history, [('kind', '类型', 80, False), ('title', '任务', 360), ('status', '状态', 96, False),
                                   ('time', '更新时间', 120, False)], empty='还没有任务记录')
        filled.container.grid(row=1, column=0, sticky='nsew', padx=10, pady=(0, t.GAP))
        records = [('下载', '徐秉龙 - 双子 等 3 条 · 音频 · 原始最佳', '已完成', '11-15 02:20'),
                   ('采集', 'https://example.org/list', '已中断', '11-14 23:02'),
                   ('下载', '2 条 · 视频 · MP4 兼容', '部分完成', '11-13 04:11')]
        filled.fill((number, records[number % 3]) for number in range(1, 9))
        filled.selection_set('2')
        hollow = t.table(history, [('title', '新增内容（可多选）', 290), ('url', '链接', 420)], selectmode='extended',
                         empty='还没有新增内容 · 订阅检查到的新链接会出现在这里')
        hollow.container.grid(row=2, column=0, sticky='nsew', padx=10, pady=(0, 10))
        hollow.fill([])
        for name in ('增量订阅', '统一资料归档'):
            t.label(tabs.add(name), name, text_color=t.MUTED).grid(row=0, column=0, padx=10, pady=10)

        settings = self.section(page, 2, '下载')
        settings.grid_columnconfigure(0, weight=1)
        area = t.scroll_area(settings, height=150)
        area.grid(row=0, column=0, sticky='ew')
        area.grid_columnconfigure(0, weight=1)
        self.safe = ctk.BooleanVar(self, True)
        self.proxy = ctk.StringVar(self, '')
        # the settings page's grid: a short label on the left, its control on the right (settings.md 2)
        rows = (('稳健模式', lambda: t.switch(area, variable=self.safe)),
                ('本次代理', lambda: t.entry(area, textvariable=self.proxy, placeholder='http://127.0.0.1:7890', width=260)),
                ('yt-dlp 2026.10.21', lambda: t.button(area, '检查更新', None, kind='primary')),
                ('完成提醒', lambda: t.switch(area)))
        made = []
        for row, (title, control) in enumerate(rows):
            t.label(area, title, anchor='w', height=t.CONTROL_H).grid(row=row, column=0, sticky='w',
                                                                      pady=(0 if row == 0 else t.INLINE, 0))
            made.append(control())
            made[-1].grid(row=row, column=1, sticky='e', padx=(t.PAD, t.INLINE), pady=(0 if row == 0 else t.INLINE, 0))
        self.probe.update(table=filled, table_empty=hollow, tabs=tabs, scroll_area=area,
                          setting_entry=made[1], setting_switch=made[0])
        return page


# ---------------------------------------------------------------- capture and measurements
def grab(window, path):
    """Save the client area of ``window`` with PrintWindow (works while the window is fully transparent)."""
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
    hwnd = user32.GetParent(window.winfo_id())
    width, height = window.winfo_width(), window.winfo_height()
    hdc = user32.GetWindowDC(hwnd)
    memory = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memory, bitmap)
    printed = user32.PrintWindow(hwnd, memory, 3)

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                    ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG),
                    ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]
    header = BitmapInfoHeader()
    header.biSize, header.biWidth, header.biHeight, header.biPlanes, header.biBitCount = ctypes.sizeof(header), width, -height, 1, 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    picture = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1).convert('RGB')
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(hwnd, hdc)
    if not printed:
        return None
    picture.save(path)
    return picture


def text_start(picture, box, background):
    """x of the first pixel in ``box`` (left, top, right, bottom) that is clearly not ``background``."""
    red, green, blue = (int(background[index:index + 2], 16) for index in (1, 3, 5))
    pixels = picture.load()
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            pixel = pixels[x, y]
            if abs(pixel[0] - red) + abs(pixel[1] - green) + abs(pixel[2] - blue) > 90:
                return x
    return None


def measure(app, picture=None):
    """Sizes in logical px (physical / scaling) of what the demo shows, for the handoff note."""
    scale = ctk.ScalingTracker.get_widget_scaling(app)
    probe = app.probe

    def height(widget):
        return round(widget.winfo_height() / scale, 1)

    def line(description):
        # Font(font=description) would round the size to whole points; the options keep the pixels.
        parts = app.tk.splitlist(description) if isinstance(description, str) else tuple(description)
        styles = ' '.join(str(part) for part in parts[2:]).split()
        exact = tkfont.Font(root=app, family=parts[0], size=int(parts[1]), weight='bold' if 'bold' in styles else 'normal')
        return exact.metrics('linespace')
    tree = probe['table']
    style = ttk.Style(app)
    body_line = line((t.FAMILY, -round(t.SIZES['body'] * scale)))
    tree_line = line(style.lookup('App.Treeview', 'font'))
    result = {
        'scaling': scale, 'tk_scaling': round(float(app.tk.call('tk', 'scaling')), 3),
        'button_md': height(probe['button_primary_md_normal']), 'button_sm': height(probe['button_primary_sm_normal']),
        'entry': height(probe['entry']), 'option_menu': height(probe['option_menu']), 'checkbox': height(probe['checkbox']),
        'switch': height(probe['switch']), 'switch_bare_width': round(probe['switch_bare'].winfo_width() / scale, 1),
        'chip': height(probe['chip_ok']), 'chip_width': round(probe['chip_ok'].winfo_width() / scale, 1),
        'textbox': height(probe['textbox']), 'task_row': height(probe['task_row']),
        'table_rowheight': round(int(style.lookup('App.Treeview', 'rowheight')) / scale, 1),
        'table_line_px': tree_line, 'body_line_px': body_line, 'table_line_ratio': round(tree_line / body_line, 3),
        'setting_switch': height(probe['setting_switch']), 'setting_entry': height(probe['setting_entry']),
        'placeholder_mapped': bool(probe['entry_hint'].placeholder_label.winfo_ismapped()),
        'fit_text': probe['fit_label'].cget('text'), 'fit_is_cut': probe['fit_label'].cget('text') != t.full_text(probe['fit_label']),
        'scrollbar_shown': probe['scroll_area'].bar_visible,
    }
    for role in t.SIZES:
        sample = t.label(app, '样本 Ag', role=role)
        result[f'label_{role}'] = sample.cget('height')
        result[f'line_{role}'] = round(line(t.font(role).create_scaled_tuple(scale)) / scale, 1)
        sample.destroy()
    first = tree.get_children()[0]
    cell = tree.bbox(first, 'title')
    if picture is not None and cell:
        left = tree.winfo_rootx() - app.winfo_rootx() + cell[0]
        top = tree.winfo_rooty() - app.winfo_rooty()
        result['heading_text_x'] = text_start(picture, (left, top + 2, left + cell[2], top + cell[1] - 2), t.resolve(t.SURFACE_ALT))
        result['cell_text_x'] = text_start(picture, (left, top + cell[1] + 2, left + cell[2], top + cell[1] + cell[3] - 2), t.resolve(t.SURFACE))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--shots', metavar='DIR', help='save PNGs into DIR (outside the source tree) and exit')
    parser.add_argument('--size', default='1120x810', help='window size in logical px, WxH')
    arguments = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):       # a console that cannot show a character must not stop the demo
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    out = None
    if arguments.shots:
        out = Path(arguments.shots).resolve()
        if out == TREE or TREE in out.parents:
            parser.error('--shots DIR must be outside the source tree')
        out.mkdir(parents=True, exist_ok=True)
    t.enable_dpi_awareness()
    ctk.set_appearance_mode('light')
    t.apply_theme()
    app = Demo()
    if out is None:
        app.geometry(arguments.size)
        app.mainloop()
        return 0
    try:
        app.attributes('-alpha', 0.0)
        app.geometry(arguments.size + '+0+0')
        results = {}
        for name in app.pages:
            app.show(name)
            for _ in range(4):          # layout settles over a few idle rounds (wrap, fit, scrollbars)
                app.update()
            picture = grab(app, out / f'demo-{name}-{arguments.size}.png')
            if picture is None:
                print('capture skipped:', name)
            if name == 'tables':
                results = measure(app, picture)
        (out / f'measurements-{arguments.size}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        for key, value in results.items():
            print(f'{key}: {value}')
    finally:
        app.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
