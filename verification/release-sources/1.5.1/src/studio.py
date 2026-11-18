from __future__ import annotations

import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import traceback
from dataclasses import asdict
from urllib.parse import urlsplit

import customtkinter as ctk
from progress import SmoothProgressBar
from collection_ui import CollectionUI
from library_ui import LibraryUI
from engine import (APP_DIR, APP_VERSION, MODES, RESOLUTIONS, RESOURCE_ROOT, STATE_DIR,
                    Options, Runner, build_command, check_cookie_file, engine_version, load_failed_job,
                    parse_urls, read_url_file)

BG, WHITE, INK, MUTED = '#F4F7FC', '#FFFFFF', '#18253B', '#7A879C'
BLUE, BLUE_LIGHT, LINE = '#3268EB', '#EDF3FF', '#E5EAF3'
SIDE, GREEN, ORANGE = '#111F35', '#16966D', '#C17A16'
FORMATS = ['原始音频 · 保留源编码', 'WAV · 32 位浮点', 'FLAC · 24 位', 'MP3 · 320 kbps', 'MKV · 保留源画质']


def bytes_text(value):
    if value is None:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}'
        value /= 1024


def time_text(value):
    if value is None:
        return '—'
    minutes, seconds = divmod(int(value), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class StudioApp(LibraryUI, CollectionUI, ctk.CTk):
    def __init__(self, arguments):
        super().__init__(fg_color=BG)
        self.title('YT-DLP 下载器')
        self.geometry('1120x810')
        self.minsize(1000, 740)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.events = queue.Queue()
        self.runner = None
        self.busy = self.closing = self.animating = False
        self.smoke_mode = bool(arguments.smoke_url)
        self.active_index = None
        self.operation = None
        self.wait_context = None
        self.wait_deadline = 0
        self.wait_seconds = None
        self.rows = []
        self.controls = []
        self.update_buttons = []
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('YtDlpDesktop')
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = RotatingFileHandler(STATE_DIR / 'download.log', maxBytes=3 * 1024 * 1024, backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.logger.addHandler(handler)
        try:
            settings = json.loads((STATE_DIR / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings = {}
        mode = settings.get('mode', MODES[0])
        if mode not in MODES:
            mode = MODES[0]
        resolution = settings.get('resolution', RESOLUTIONS[0])
        self.output = tk.StringVar(value=arguments.smoke_output or settings.get('output', str(APP_DIR / '下载文件')))
        self.mode = tk.StringVar(value=mode)
        self.format = tk.StringVar(value=FORMATS[MODES.index(mode)])
        self.resolution = tk.StringVar(value=resolution if resolution in RESOLUTIONS else RESOLUTIONS[0])
        self.proxy = tk.StringVar(value='')
        cookies = settings.get('cookies', '')
        self.cookies = tk.StringVar(value=cookies if isinstance(cookies, str) else '')
        self.playlist = tk.BooleanVar(value=False)
        self.batch_safe = tk.BooleanVar(value=settings.get('batch_safe', True) is not False)
        self.init_library(settings)
        self.failed_job = None if self.smoke_mode else load_failed_job()
        self.font = ctk.CTkFont(family='Microsoft YaHei UI', size=13)
        self.small = ctk.CTkFont(family='Microsoft YaHei UI', size=11)
        self.bold = ctk.CTkFont(family='Microsoft YaHei UI', size=15, weight='bold')
        self.build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color='transparent')
        self.content.grid(row=0, column=1, sticky='nsew', padx=28, pady=(21, 20))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self.build_download_page()
        self.build_log_page()
        self.build_settings_page()
        self.build_collection_page()
        self.build_library_page()
        self.archive_enabled.trace_add('write', lambda *_: self.refresh_destination())
        self.refresh_destination()
        self.refresh_failed_button()
        self.show_page('download')
        self.on_format()
        if arguments.smoke_url:
            self.urls.insert('1.0', arguments.smoke_url)
            self.format.set(FORMATS[1])
            self.on_format()
        self.links_changed()
        self.after(25, self.poll)
        self.append_log(f'就绪：yt-dlp {engine_version()}；FFmpeg、ffprobe 和 Deno 已内置。')
        try:
            self.iconbitmap(str(RESOURCE_ROOT / 'app.ico'))
        except tk.TclError:
            pass

    def label(self, parent, text='', **kwargs):
        kwargs.setdefault('font', self.font)
        kwargs.setdefault('text_color', INK)
        return ctk.CTkLabel(parent, text=text, **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        kwargs.setdefault('width', 94)
        return ctk.CTkButton(parent, text=text, command=command, font=self.font, height=35,
                            corner_radius=8, fg_color=BLUE if primary else BLUE_LIGHT,
                            hover_color='#2557D0' if primary else '#DFE9FF',
                            text_color=WHITE if primary else BLUE, **kwargs)

    def card(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=WHITE, corner_radius=14, border_width=1, border_color=LINE, **kwargs)

    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=184, fg_color=SIDE, corner_radius=0)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(7, weight=1)
        brand = ctk.CTkFrame(side, fg_color='transparent')
        brand.grid(row=0, column=0, sticky='ew', padx=19, pady=(30, 33))
        mark = ctk.CTkFrame(brand, width=38, height=38, corner_radius=11, fg_color=BLUE)
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        mark.grid_propagate(False)
        self.label(mark, '↓', font=ctk.CTkFont(size=28, weight='bold'), text_color=WHITE).place(relx=.5, rely=.48, anchor='center')
        self.label(brand, 'YT-DLP', text_color=WHITE, font=ctk.CTkFont(size=19, weight='bold'), anchor='w').grid(row=0, column=1, sticky='w')
        self.label(brand, '桌面下载器', text_color='#91A3BF', font=self.small, anchor='w').grid(row=1, column=1, sticky='w')
        self.nav_buttons = {}
        for row, key, title in [(1, 'download', '↓   下载中心'), (2, 'collect', '⌕   网页采集'), (3, 'library', '▤   资料管理'), (4, 'logs', '≡   任务日志'), (5, 'settings', '⚙   偏好设置')]:
            button = ctk.CTkButton(side, text=title, anchor='w', font=self.font, height=43,
                                  fg_color='transparent', hover_color='#203451', corner_radius=9,
                                  text_color='#A9B8CF', command=lambda selected=key: self.show_page(selected))
            button.grid(row=row, column=0, sticky='ew', padx=13, pady=4)
            self.nav_buttons[key] = button
        self.label(side, '本地文件', text_color='#637A9C', font=self.small, anchor='w').grid(row=6, column=0, sticky='ew', padx=24, pady=(28, 0))
        self.folder_button = ctk.CTkButton(side, text='↗   打开下载目录', anchor='w', height=39,
                                          font=self.font, fg_color='transparent', hover_color='#203451',
                                          text_color='#A9B8CF', command=self.open_folder)
        self.folder_button.grid(row=7, column=0, sticky='new', padx=13, pady=(9, 0))
        bottom = ctk.CTkFrame(side, fg_color='#192A44', corner_radius=11)
        bottom.grid(row=8, column=0, sticky='ew', padx=15, pady=(12, 19))
        self.label(bottom, '●  下载引擎已就绪', text_color='#65D8AE', font=self.small).pack(anchor='w', padx=12, pady=(10, 1))
        self.engine_badge = self.label(bottom, f'yt-dlp {engine_version()}', text_color='#92A6C6', font=self.small)
        self.engine_badge.pack(anchor='w', padx=12, pady=(0, 1))
        self.label(bottom, f'Desktop {APP_VERSION}', text_color='#657E9F', font=self.small).pack(anchor='w', padx=12, pady=(0, 10))

    def page(self, name, title, subtitle):
        page = ctk.CTkFrame(self.content, fg_color='transparent')
        page.grid(row=0, column=0, sticky='nsew')
        page.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(page, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 17))
        self.label(header, title, font=ctk.CTkFont(family='Microsoft YaHei UI', size=27, weight='bold'), anchor='w').pack(anchor='w')
        self.label(header, subtitle, text_color=MUTED, font=self.small, anchor='w').pack(anchor='w', pady=(2, 0))
        self.pages[name] = page
        return page

    def build_download_page(self):
        page = self.page('download', '下载中心', '添加链接，选择格式，剩下的交给下载器。')
        page.grid_rowconfigure(4, weight=1)
        links = self.card(page)
        links.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        links.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(links, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew', padx=17, pady=(12, 7))
        top.grid_columnconfigure(0, weight=1)
        self.label(top, '添加下载链接', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w')
        self.link_count = self.label(top, '0 条链接', text_color=BLUE, fg_color=BLUE_LIGHT, corner_radius=6, width=75, height=25, font=self.small)
        self.link_count.grid(row=0, column=1, sticky='e')
        self.urls = ctk.CTkTextbox(links, height=80, fg_color='#F7F9FD', border_width=1,
                                  border_color=LINE, corner_radius=8, text_color=INK,
                                  font=self.font, wrap='word')
        self.urls.grid(row=1, column=0, sticky='ew', padx=17)
        self.urls.bind('<KeyRelease>', lambda _event: self.links_changed())
        toolbar = ctk.CTkFrame(links, fg_color='transparent')
        toolbar.grid(row=2, column=0, sticky='ew', padx=17, pady=(8, 11))
        toolbar.grid_columnconfigure(2, weight=1)
        paste = self.button(toolbar, '粘贴链接', self.paste, width=91)
        paste.grid(row=0, column=0, padx=(0, 7))
        importer = self.button(toolbar, '导入 TXT', self.import_txt, width=91)
        importer.grid(row=0, column=1)
        self.label(toolbar, '每行一条 · 自动去重', text_color=MUTED, font=self.small).grid(row=0, column=2, sticky='e')
        self.controls += [paste, importer, self.urls]
        options = self.card(page)
        options.grid(row=2, column=0, sticky='ew', pady=(0, 12))
        options.grid_columnconfigure(0, weight=1)
        form = ctk.CTkFrame(options, fg_color='transparent')
        form.grid(row=0, column=0, sticky='ew', padx=17, pady=(12, 10))
        form.grid_columnconfigure(0, weight=3)
        form.grid_columnconfigure(1, weight=2)
        self.label(form, '输出格式', font=self.small, text_color=MUTED, anchor='w').grid(row=0, column=0, sticky='w')
        self.label(form, '视频画质', font=self.small, text_color=MUTED, anchor='w').grid(row=0, column=1, sticky='w', padx=(12, 0))
        self.format_menu = ctk.CTkOptionMenu(form, values=FORMATS, variable=self.format, command=self.on_format,
                                            font=self.font, dropdown_font=self.font, height=34, fg_color='#F1F5FC',
                                            button_color='#E5ECF8', button_hover_color='#D7E3F7', text_color=INK)
        self.format_menu.grid(row=1, column=0, sticky='ew')
        self.quality_menu = ctk.CTkOptionMenu(form, values=list(RESOLUTIONS), variable=self.resolution,
                                             font=self.font, dropdown_font=self.font, height=34, fg_color='#F1F5FC',
                                             button_color='#E5ECF8', button_hover_color='#D7E3F7', text_color=INK)
        self.quality_menu.grid(row=1, column=1, sticky='ew', padx=(12, 0))
        playlist = ctk.CTkCheckBox(form, text='整个播放列表', variable=self.playlist, checkbox_width=18,
                                   checkbox_height=18, corner_radius=5, font=self.small, text_color=INK,
                                   fg_color=BLUE, hover_color=BLUE, border_color='#AEBED6')
        playlist.grid(row=1, column=2, padx=(15, 0), sticky='e')
        destination = ctk.CTkFrame(options, fg_color='transparent')
        destination.grid(row=1, column=0, sticky='ew', padx=17, pady=(0, 13))
        destination.grid_columnconfigure(1, weight=1)
        self.destination_label = self.label(destination, '保存到', text_color=MUTED, font=self.small)
        self.destination_label.grid(row=0, column=0, padx=(0, 10))
        self.output_entry = ctk.CTkEntry(destination, textvariable=self.output, fg_color='#F7F9FD',
                                         border_color=LINE, text_color=INK, font=self.small, height=33)
        self.output_entry.grid(row=0, column=1, sticky='ew')
        browse = self.button(destination, '更改', self.browse, width=65)
        browse.grid(row=0, column=2, padx=(8, 0))
        self.controls += [self.format_menu, self.output_entry, playlist, browse]
        status = self.card(page)
        status.grid(row=3, column=0, sticky='ew', pady=(0, 12))
        status.grid_columnconfigure(0, weight=1)
        self.current_title = self.label(status, '准备就绪', font=self.bold, anchor='w', wraplength=400)
        self.current_title.grid(row=0, column=0, sticky='ew', padx=17, pady=(10, 0))
        self.metrics = self.label(status, '添加链接后，点击开始下载', text_color=MUTED, font=self.small, anchor='w', wraplength=400, justify='left')
        self.metrics.grid(row=1, column=0, sticky='ew', padx=17, pady=(0, 4))
        self.progress = SmoothProgressBar(status, height=6, progress_color=BLUE, fg_color='#E9EFF9')
        self.progress.grid(row=2, column=0, sticky='ew', padx=17, pady=(0, 13))
        self.progress.set(0, immediate=True)
        self.open_output_button = self.button(status, '打开文件夹', self.open_folder, width=94)
        self.open_output_button.grid(row=0, column=1, rowspan=3, padx=(3, 8))
        self.stop_button = self.button(status, '停止', self.stop, width=65, state='disabled')
        self.stop_button.grid(row=0, column=2, rowspan=3, padx=(0, 8))
        self.start_button = self.button(status, '↓  开始下载', self.start, primary=True, width=129)
        self.start_button.grid(row=0, column=3, rowspan=3, padx=(0, 17))
        self.controls.append(self.start_button)
        tasks = self.card(page)
        tasks.grid(row=4, column=0, sticky='nsew')
        tasks.grid_columnconfigure(0, weight=1)
        tasks.grid_rowconfigure(1, weight=1)
        heading = ctk.CTkFrame(tasks, fg_color='transparent')
        heading.grid(row=0, column=0, sticky='ew', padx=17, pady=(10, 3))
        heading.grid_columnconfigure(0, weight=1)
        self.label(heading, '任务列表', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w')
        self.queue_stats = self.label(heading, '暂无任务', text_color=MUTED, font=self.small)
        self.queue_stats.grid(row=0, column=1, sticky='e')
        self.retry_button = self.button(heading, '重试失败', self.retry_failed, width=103, state='disabled')
        self.retry_button.grid(row=0, column=2, padx=(12, 0))
        self.task_list = ctk.CTkScrollableFrame(tasks, fg_color=WHITE, corner_radius=0, height=110,
                                               scrollbar_button_color='#D7E0EE', scrollbar_button_hover_color='#C0CCE0')
        self.task_list.grid(row=1, column=0, sticky='nsew', padx=10, pady=(0, 10))
        self.task_list.grid_columnconfigure(0, weight=1)
        self.empty = ctk.CTkFrame(tasks, fg_color=WHITE, corner_radius=0)
        self.empty.grid(row=1, column=0, sticky='nsew', padx=16, pady=(0, 10))
        center = ctk.CTkFrame(self.empty, fg_color='transparent')
        center.place(relx=.5, rely=.5, anchor='center')
        self.label(center, '↓', font=ctk.CTkFont(size=30), text_color='#9DB8EC').pack()
        self.label(center, '你的下载任务会显示在这里', text_color=MUTED, font=self.small).pack(pady=(2, 0))

    def build_log_page(self):
        page = self.page('logs', '任务日志', '解析、下载、转换和错误信息，集中查看。')
        page.grid_rowconfigure(1, weight=1)
        card = self.card(page)
        card.grid(row=1, column=0, sticky='nsew')
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        self.label(card, '运行记录', font=self.bold, anchor='w').grid(row=0, column=0, sticky='ew', padx=20, pady=(15, 10))
        self.logs = ctk.CTkTextbox(card, fg_color='#F8FAFD', text_color='#4A5D77', font=self.small,
                                  wrap='word', state='disabled', corner_radius=8)
        self.logs.grid(row=1, column=0, sticky='nsew', padx=16, pady=(0, 16))

    def build_settings_page(self):
        page = self.page('settings', '偏好设置', '下载引擎独立更新，界面与个人设置保持不变。')
        page.grid_rowconfigure(1, weight=1)
        body = ctk.CTkScrollableFrame(page, fg_color='transparent', corner_radius=0,
                                       scrollbar_button_color='#D7E0EE', scrollbar_button_hover_color='#C0CCE0')
        body.grid(row=1, column=0, sticky='nsew')
        body.grid_columnconfigure(0, weight=1)
        card = self.card(body)
        card.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        card.grid_columnconfigure(0, weight=1)
        self.label(card, '下载引擎', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w', padx=21, pady=(17, 2))
        self.version_label = self.label(card, f'yt-dlp  {engine_version()}', text_color=BLUE, anchor='w')
        self.version_label.grid(row=1, column=0, sticky='w', padx=21)
        self.update_status = self.label(card, '从官方稳定版更新；下载完成后校验 SHA256。', text_color=MUTED, font=self.small, anchor='w')
        self.update_status.grid(row=2, column=0, sticky='w', padx=21, pady=(2, 18))
        update = self.button(card, '检查更新', self.update_engine, primary=True, width=116)
        update.grid(row=0, column=1, rowspan=3, padx=21)
        self.update_buttons.append(update)
        network = self.card(body)
        network.grid(row=1, column=0, sticky='ew', pady=(0, 16))
        network.grid_columnconfigure(0, weight=1)
        self.label(network, '网络', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w', padx=21, pady=(15, 4))
        self.label(network, '代理地址（可选）', text_color=MUTED, font=self.small, anchor='w').grid(row=1, column=0, sticky='w', padx=21)
        proxy = ctk.CTkEntry(network, textvariable=self.proxy, placeholder_text='留空使用当前网络，如 http://127.0.0.1:7890',
                              fg_color='#F7F9FD', border_color=LINE, text_color=INK, font=self.font, height=36)
        proxy.grid(row=2, column=0, sticky='ew', padx=21, pady=(5, 12))
        self.label(network, 'YouTube cookies 文件（可选）', text_color=MUTED, font=self.small, anchor='w').grid(row=3, column=0, sticky='w', padx=21)
        cookie_row = ctk.CTkFrame(network, fg_color='transparent')
        cookie_row.grid(row=4, column=0, sticky='ew', padx=21, pady=(5, 0))
        cookie_row.grid_columnconfigure(0, weight=1)
        self.cookies_entry = ctk.CTkEntry(cookie_row, textvariable=self.cookies,
                                          placeholder_text='留空不使用；遇到 YouTube 机器人验证时选择 cookies.txt',
                                          fg_color='#F7F9FD', border_color=LINE, text_color=INK, font=self.font, height=36)
        self.cookies_entry.grid(row=0, column=0, sticky='ew')
        choose_cookies = self.button(cookie_row, '选择', self.choose_cookies, width=65)
        choose_cookies.grid(row=0, column=1, padx=(8, 0))
        clear_cookies = self.button(cookie_row, '清除', self.clear_cookies, width=65)
        clear_cookies.grid(row=0, column=2, padx=(8, 0))
        self.label(network, '出现 “Sign in to confirm you’re not a bot” 时使用：导出已登录 YouTube（建议小号）的 Netscape 格式 cookies.txt。'
                            '该文件等同登录凭证，请勿外传。',
                   text_color=MUTED, font=self.small, anchor='w', justify='left', wraplength=640).grid(row=5, column=0, sticky='w', padx=21, pady=(6, 18))
        self.controls += [proxy, self.cookies_entry, choose_cookies, clear_cookies]
        batch = self.card(body)
        batch.grid(row=2, column=0, sticky='ew', pady=(0, 16))
        batch.grid_columnconfigure(0, weight=1)
        self.label(batch, '批量下载', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w', padx=21, pady=(15, 8))
        self.batch_switch = ctk.CTkSwitch(batch, text='稳健模式（推荐）', variable=self.batch_safe,
                                          command=self.save_settings, font=self.font, text_color=INK,
                                          progress_color=BLUE, button_color=WHITE, button_hover_color='#DFE9FF')
        self.batch_switch.grid(row=1, column=0, sticky='w', padx=21)
        self.label(batch, '每条下载前随机等待 10–20 秒；失败时逐步延长重试等待。',
                   text_color=MUTED, font=self.small, anchor='w').grid(row=2, column=0, sticky='w', padx=21, pady=(8, 2))
        self.label(batch, '遇到网站限流会暂停队列；失败链接会保存，下次打开仍可重试。',
                   text_color=MUTED, font=self.small, anchor='w').grid(row=3, column=0, sticky='w', padx=21, pady=(0, 15))
        self.controls.append(self.batch_switch)
        components = self.card(body)
        components.grid(row=3, column=0, sticky='ew')
        components.grid_columnconfigure(0, weight=1)
        self.label(components, '应用与内置组件', font=self.bold, anchor='w').grid(row=0, column=0, sticky='w', padx=21, pady=(15, 6))
        manifest = json.loads((RESOURCE_ROOT / 'vendor/manifest.json').read_text(encoding='utf-8'))
        for row, name, value in [(1, '桌面应用', APP_VERSION), (2, 'FFmpeg / ffprobe', manifest[2].get('display_version', '内置版本')),
                                 (3, 'Deno', manifest[1]['version'])]:
            self.label(components, name, text_color=MUTED, font=self.small, anchor='w').grid(row=row, column=0, sticky='w', padx=21)
            self.label(components, value, font=self.small).grid(row=row, column=1, sticky='e', padx=21)
        self.label(components, '这些组件随应用打包；“检查更新”仅更新 yt-dlp。', text_color=MUTED, font=self.small, anchor='w').grid(row=4, column=0, columnspan=2, sticky='w', padx=21, pady=(10, 17))

    def show_page(self, name):
        for key, button in self.nav_buttons.items():
            button.configure(fg_color='#253D63' if key == name else 'transparent',
                             text_color=WHITE if key == name else '#A9B8CF')
        self.pages[name].tkraise()

    def on_format(self, _value=None):
        self.mode.set(MODES[FORMATS.index(self.format.get())])
        self.quality_menu.configure(state='normal' if self.mode.get() == MODES[4] and not self.busy else 'disabled')

    def links_changed(self):
        lines = {s.strip() for s in self.urls.get('1.0', 'end-1c').splitlines() if s.strip() and not s.lstrip().startswith(('#', ';', ']'))}
        self.link_count.configure(text=f'{len(lines)} 条链接')

    def add_urls(self, text):
        if self.urls.get('1.0', 'end-1c').strip():
            self.urls.insert('end', '\n')
        self.urls.insert('end', text.strip())
        self.links_changed()

    def paste(self):
        try:
            self.add_urls(self.clipboard_get())
        except tk.TclError:
            pass

    def import_txt(self):
        path = filedialog.askopenfilename(parent=self, title='导入链接列表', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if path:
            try:
                self.add_urls('\n'.join(read_url_file(path)))
            except (ValueError, OSError) as error:
                messagebox.showwarning('导入未完成', str(error), parent=self)

    def browse(self):
        if self.archive_enabled.get():
            self.choose_archive()
            return
        initial = self.output.get() if Path(self.output.get()).is_dir() else str(APP_DIR)
        path = filedialog.askdirectory(parent=self, initialdir=initial)
        if path:
            self.output.set(path)

    def choose_cookies(self):
        current = Path(self.cookies.get().strip()).expanduser() if self.cookies.get().strip() else None
        initial = str(current.parent) if current and current.parent.is_dir() else str(Path.home())
        path = filedialog.askopenfilename(parent=self, title='选择 cookies.txt', initialdir=initial,
                                          filetypes=[('cookies 文件', '*.txt'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            check_cookie_file(path)
        except (ValueError, OSError) as error:
            messagebox.showwarning('cookies 文件不可用', str(error), parent=self)
            return
        self.cookies.set(str(Path(path)))
        self.save_settings()
        self.append_log('已设置 cookies 文件；之后的下载会带上登录信息。')

    def clear_cookies(self):
        self.cookies.set('')
        self.save_settings()

    def open_folder(self):
        try:
            path = Path(self.archive_root.get() if self.archive_enabled.get() else self.output.get()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as error:
            messagebox.showerror('无法打开目录', str(error), parent=self)

    def refresh_destination(self):
        enabled = self.archive_enabled.get()
        self.output_entry.configure(textvariable=self.archive_root if enabled else self.output)
        self.destination_label.configure(text='资料库' if enabled else '保存到')

    def save_settings(self):
        if not self.smoke_mode:
            try:
                (STATE_DIR / 'settings.json').write_text(json.dumps({'output': self.output.get(), 'mode': self.mode.get(),
                    'resolution': self.resolution.get(), 'batch_safe': self.batch_safe.get(),
                    'archive_enabled': self.archive_enabled.get(), 'archive_root': self.archive_root.get(),
                    'cookies': self.cookies.get().strip()}, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as error:
                self.append_log(f'设置未保存：{error}')

    def append_log(self, line):
        self.logger.info(str(line))
        self.logs.configure(state='normal')
        self.logs.insert('end', str(line) + '\n')
        if int(self.logs.index('end-1c').split('.')[0]) > 1500:
            self.logs.delete('1.0', '301.0')
        self.logs.see('end')
        self.logs.configure(state='disabled')

    def set_busy(self, busy):
        self.busy = busy
        for control in self.controls + self.update_buttons:
            control.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.refresh_failed_button()
        self.on_format()
        if hasattr(self, 'collect_stop'):
            self.collect_stop.configure(state='normal' if busy and self.operation == 'collect' else 'disabled')
            self.collection_selection_changed()
        if hasattr(self, 'history_tree'):
            self.history_selection()

    def refresh_failed_button(self):
        count = len(self.failed_job['urls']) if self.failed_job else 0
        self.retry_button.configure(text=f'重试失败 ({count})' if count else '重试失败',
                                    state='normal' if count and not self.busy else 'disabled')

    def retry_failed(self):
        if self.busy or not self.failed_job:
            return
        options = self.failed_job['options']
        self.output.set(str(options.output))
        self.mode.set(options.mode)
        self.format.set(FORMATS[MODES.index(options.mode)])
        self.resolution.set(options.resolution)
        self.playlist.set(options.playlist)
        self.batch_safe.set(options.batch_safe)
        self.download_referers.update(options.referers)
        self.archive_enabled.set(bool(options.archive_root))
        if options.archive_root:
            self.archive_root.set(options.archive_root)
        self.urls.delete('1.0', 'end')
        self.urls.insert('1.0', '\n'.join(self.failed_job['urls']))
        self.links_changed()
        self.show_page('download')
        self.start()

    def clear_wait(self):
        self.wait_context = None
        self.wait_seconds = None

    def render_wait(self):
        if self.wait_context is None:
            return
        seconds = max(0, math.ceil(self.wait_deadline - time.monotonic()))
        if seconds == self.wait_seconds:
            return
        self.wait_seconds = seconds
        reason = self.wait_context['reason']
        self.metrics.configure(text=f'{reason} · 剩余 {time_text(seconds)} · 可随时停止')
        self.mark_row(self.active_index, '等待中', ORANGE)
        if self.active_index is not None and 0 <= self.active_index < len(self.rows):
            self.rows[self.active_index]['detail'].configure(text=f'{self.active_index + 1:02d}  ·  {reason} · {time_text(seconds)}')

    def animate(self, enabled):
        if enabled != self.animating:
            self.animating = enabled
            if enabled:
                self.progress.configure(mode='indeterminate')
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.configure(mode='determinate')

    def populate_queue(self, urls):
        for row in self.rows:
            row['frame'].destroy()
        self.rows = []
        self.empty.grid_remove()
        for index, url in enumerate(urls):
            frame = ctk.CTkFrame(self.task_list, fg_color='#F8FAFE', corner_radius=9)
            frame.grid(row=index, column=0, sticky='ew', padx=3, pady=4)
            frame.grid_columnconfigure(1, weight=1)
            self.label(frame, '↓', width=32, text_color=BLUE, font=ctk.CTkFont(size=21)).grid(row=0, column=0, rowspan=2, padx=(8, 3))
            title = self.label(frame, urlsplit(url).hostname or url, anchor='w', font=self.font, wraplength=460)
            title.grid(row=0, column=1, sticky='ew', padx=5, pady=(7, 0))
            host = self.label(frame, f'{index + 1:02d}  ·  等待解析', font=self.small, text_color=MUTED, anchor='w')
            host.grid(row=1, column=1, sticky='ew', padx=5, pady=(0, 7))
            badge = self.label(frame, '等待中', width=85, text_color=MUTED, font=self.small)
            badge.grid(row=0, column=2, rowspan=2, padx=(5, 11))
            self.rows.append({'frame': frame, 'title': title, 'detail': host, 'badge': badge, 'state': 'waiting'})
        self.queue_stats.configure(text=f'{len(urls)} 条任务')

    def mark_row(self, index, text, color, state=None):
        if index is not None and 0 <= index < len(self.rows):
            row = self.rows[index]
            row['badge'].configure(text=text, text_color=color)
            if state:
                row['state'] = state

    def start(self):
        if self.busy:
            return
        try:
            urls = parse_urls(self.urls.get('1.0', 'end-1c'))
            if not self.output.get().strip():
                raise ValueError('请选择保存目录。')
            options = Options(Path(self.output.get()).expanduser().resolve(), self.mode.get(),
                              self.resolution.get(), self.playlist.get(), self.proxy.get(), self.batch_safe.get(),
                              referers={url: self.download_referers[url] for url in urls if url in self.download_referers},
                              archive_root=str(Path(self.archive_root.get()).expanduser().resolve()) if self.archive_enabled.get() else '',
                              archive_contexts=self.collection_contexts(urls) if self.archive_enabled.get() else {},
                              cookies=self.cookies.get().strip())
            if self.archive_enabled.get() and not self.archive_root.get().strip():
                raise ValueError('请选择资料库目录。')
            build_command(urls[0], options)
        except (ValueError, OSError) as error:
            messagebox.showwarning('检查输入', str(error), parent=self)
            return
        self.save_settings()
        self.populate_queue(urls)
        self.operation = 'download'
        self.set_busy(True)
        self.metrics.configure(text_color=MUTED)
        self.progress.set(0, immediate=True)
        self.animate(True)
        self.operation = 'download'
        self.active_index = None
        self.clear_wait()
        self.append_log(f'开始任务：{len(urls)} 条链接，{self.mode.get()}，保存到 {options.archive_root or options.output}')
        settings = asdict(options)
        settings.update(output=str(options.output), urls=urls)
        job_id = self.library.create_job('download', f'{len(urls)}条 · {options.mode}', settings)
        self.download_job = job_id
        def emit(kind, value):
            if kind in ('item', 'file', 'item_done', 'done'):
                self.library.download_event(job_id, kind, value)
            self.events.put((kind, value))
        self.runner = Runner(emit)
        self.runner.library = self.library
        threading.Thread(target=self.runner.download, args=(urls, options), daemon=True).start()

    def update_engine(self):
        if not self.busy:
            self.operation = 'update'
            self.set_busy(True)
            self.progress.set(0, immediate=True)
            self.animate(True)
            self.operation = 'update'
            self.update_status.configure(text='正在检查官方更新…', text_color=BLUE)
            self.runner = Runner(lambda kind, value: self.events.put((kind, value)))
            threading.Thread(target=self.runner.update, daemon=True).start()

    def stop(self):
        if self.busy and self.runner:
            self.clear_wait()
            self.current_title.configure(text='正在停止任务…')
            self.stop_button.configure(state='disabled')
            self.collect_stop.configure(state='disabled')
            threading.Thread(target=self.runner.cancel, daemon=True).start()

    def handle(self, kind, value):
        if kind.startswith('collect_'):
            self.handle_collection(kind, value)
            return
        if kind == 'log':
            self.append_log(value)
            if self.operation == 'download' and value.startswith(('[ExtractAudio]', '[Merger]', '[VideoRemuxer]')):
                self.clear_wait()
                self.mark_row(self.active_index, '转换中', BLUE)
                self.metrics.configure(text='音视频处理中，请稍候…')
                self.animate(True)
        elif kind == 'item':
            self.clear_wait()
            self.progress.set(0, immediate=True)
            self.active_index = value['index'] - 1
            self.mark_row(self.active_index, '解析中', BLUE, 'active')
            self.rows[self.active_index]['detail'].configure(text=f"{value['index']:02d}  ·  正在获取媒体信息")
            self.current_title.configure(text=f"正在解析链接 {value['index']} / {value['total']}")
            self.metrics.configure(text='正在连接网站…')
            self.animate(True)
            self.append_log(f"链接 {value['index']} / {value['total']}：{value['url']}")
        elif kind == 'title':
            if self.operation == 'download':
                self.progress.set(0, immediate=True)
                self.animate(True)
            title = str(value)[:56]
            self.current_title.configure(text=title)
            if self.operation == 'download' and self.active_index is not None:
                self.rows[self.active_index]['title'].configure(text=title[:50])
        elif kind == 'waiting':
            self.wait_context = value
            self.wait_deadline = time.monotonic() + value['seconds']
            self.wait_seconds = None
            self.animate(False)
            self.render_wait()
        elif kind == 'resuming':
            self.clear_wait()
            self.metrics.configure(text='等待结束，继续处理…')
            self.mark_row(self.active_index, '处理中', BLUE)
            self.animate(True)
        elif kind == 'progress':
            self.clear_wait()
            fraction = value.get('fraction')
            self.animate(fraction is None)
            if fraction is not None:
                self.progress.set(fraction)
            percent = f'{100 * fraction:.1f}%' if fraction is not None else '下载中'
            speed = bytes_text(value.get('speed')) + '/s' if value.get('speed') is not None else '—'
            text = f"{percent}   ·   {bytes_text(value.get('downloaded'))} / {bytes_text(value.get('total'))}   ·   {speed}   ·   剩余 {time_text(value.get('eta'))}"
            self.metrics.configure(text=text)
            if self.operation == 'download':
                self.mark_row(self.active_index, percent if value.get('status') == 'downloading' else '处理中', BLUE)
                self.rows[self.active_index]['detail'].configure(text=f'{self.active_index + 1:02d}  ·  {speed}')
            else:
                self.update_status.configure(text=f'正在下载新版引擎… {percent}')
        elif kind == 'file':
            self.append_log(f'文件已保存：{value}')
        elif kind == 'item_done':
            self.clear_wait()
            index = value['index'] - 1
            state = 'stopped' if value['cancelled'] else ('done' if value['success'] else 'failed')
            color = GREEN if state == 'done' else ORANGE
            self.mark_row(index, {'done': '已完成', 'failed': '失败', 'stopped': '已停止'}[state], color, state)
            if state == 'done':
                detail = f"{index + 1:02d}  ·  已保存 {value['files']} 个文件"
            elif value.get('bot_check'):
                detail = f'{index + 1:02d}  ·  YouTube 要求登录验证，需要 cookies'
            else:
                detail = f'{index + 1:02d}  ·  详情见任务日志'
            self.rows[index]['detail'].configure(text=detail)
        elif kind == 'done':
            self.clear_wait()
            self.animate(False)
            if 'failed_urls' in value:
                self.failed_job = load_failed_job()
            self.set_busy(False)
            for index, row in enumerate(self.rows):
                if row['state'] == 'active':
                    self.mark_row(index, '已停止' if value['cancelled'] else '失败', ORANGE, 'stopped' if value['cancelled'] else 'failed')
            if value['cancelled']:
                title = '任务已停止'
                self.metrics.configure(text='已下载的片段会保留，重新开始可尝试续传。')
            else:
                title = f"下载完成 · {value['success']} 条成功" if not value['failed'] else f"任务结束 · {value['success']} 条成功，{value['failed']} 条失败"
                self.metrics.configure(text='文件已保存，点击“打开文件夹”即可查看。' if not value['failed'] else '失败链接已保存，可点击任务列表中的“重试失败”。')
                if value.get('bot_check'):
                    hint = ('YouTube 仍要求登录验证：cookies 可能已过期，请重新导出后点“重试失败”。' if self.cookies.get().strip()
                            else 'YouTube 要求登录验证：请在“偏好设置 → 网络”选择 cookies.txt 后点“重试失败”。')
                    self.metrics.configure(text=hint, text_color=ORANGE)
                if not value['failed']:
                    self.progress.set(1)
            self.current_title.configure(text=title)
            self.queue_stats.configure(text=f"成功 {value['success']}  /  失败 {value['failed']}  /  共 {len(self.rows)}")
            self.append_log(title)
            if value.get('failed_path'):
                self.append_log(f"失败清单：{value['failed_path']}")
            if value.get('archive_errors'):
                self.metrics.configure(text='媒体已保存，但部分归档信息失败，请查看任务日志。', text_color=ORANGE)
            self.refresh_library()
        elif kind == 'updated':
            version = value['version']
            self.version_label.configure(text=f'yt-dlp  {version}')
            self.engine_badge.configure(text=f'yt-dlp {version}')
            self.update_status.configure(text=('当前已是官方最新版。' if value['unchanged'] else '更新完成；之后的新任务将使用新版引擎。'), text_color=GREEN)
            self.current_title.configure(text='下载引擎已就绪')
            self.append_log(f'引擎检查完成：{version}')
        elif kind == 'update_error':
            self.update_status.configure(text='更新未完成，现有引擎仍可使用；详情见任务日志。', text_color=ORANGE)
            self.append_log(f'更新失败：{value}')
        elif kind == 'update_done':
            self.animate(False)
            self.set_busy(False)
            if value['cancelled']:
                self.update_status.configure(text='已取消更新。', text_color=MUTED)

    def poll(self):
        pending_progress = None
        deadline = time.perf_counter() + 0.005
        try:
            for _ in range(200):
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    pending_progress = value
                else:
                    if pending_progress is not None:
                        self.handle('progress', pending_progress)
                        pending_progress = None
                    self.handle(kind, value)
                if time.perf_counter() >= deadline:
                    break
        except queue.Empty:
            pass
        except Exception:
            self.logger.exception('UI event processing failed')
        if pending_progress is not None:
            try:
                self.handle('progress', pending_progress)
            except Exception:
                self.logger.exception('UI progress processing failed')
        self.render_wait()
        if self.closing and not self.busy:
            self.destroy()
        else:
            self.after(25, self.poll)

    def on_close(self):
        if self.busy:
            if messagebox.askyesno('停止并关闭', '当前任务尚未完成。要停止任务并关闭吗？', parent=self):
                self.closing = True
                self.stop()
        else:
            self.save_settings()
            self.destroy()
