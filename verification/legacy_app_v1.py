from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import traceback

import customtkinter as ctk

from engine import (APP_DIR, APP_NAME, APP_VERSION, MODES, RESOLUTIONS, RESOURCE_ROOT,
                    STATE_DIR, Options, Runner, build_command, diagnostics, engine_version, parse_urls)

BG = '#0b1220'
CARD = '#131e30'
FIELD = '#0d1727'
TEXT = '#e7eef9'
MUTED = '#a0aec3'
ACCENT = '#4285f4'
GREEN = '#58d7ad'


def human_bytes(value):
    if value is None:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}'
        value /= 1024


def human_time(seconds):
    if seconds is None:
        return '—'
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class DownloadApp(ctk.CTk):
    def __init__(self, arguments):
        super().__init__(fg_color=BG)
        self.title('YT-DLP 下载器')
        self.geometry('1020x810')
        self.minsize(880, 700)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self.events = queue.Queue()
        self.runner = None
        self.busy = False
        self.closing = False
        self.indeterminate = False
        self.current_files = []
        self.smoke_mode = bool(arguments.smoke_url)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger('YtDlpDesktop')
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = RotatingFileHandler(STATE_DIR / 'download.log', maxBytes=3 * 1024 * 1024,
                                           backupCount=2, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.logger.addHandler(handler)
        settings = self.read_settings()
        self.output = tk.StringVar(value=arguments.smoke_output or settings.get('output', str(APP_DIR / '下载文件')))
        self.mode = tk.StringVar(value=settings.get('mode', MODES[0]) if settings.get('mode') in MODES else MODES[0])
        self.resolution = tk.StringVar(value=settings.get('resolution', RESOLUTIONS[0]) if settings.get('resolution') in RESOLUTIONS else RESOLUTIONS[0])
        self.playlist = tk.BooleanVar(value=False)
        self.proxy = tk.StringVar(value='')
        self.body_font = ctk.CTkFont(family='Microsoft YaHei UI', size=13)
        self.small_font = ctk.CTkFont(family='Microsoft YaHei UI', size=11)
        self.heading_font = ctk.CTkFont(family='Microsoft YaHei UI', size=15, weight='bold')
        self.create_widgets()
        if arguments.smoke_url:
            self.urls.insert('1.0', arguments.smoke_url)
            self.mode.set(MODES[1])
            self.on_mode_change()
        self.after(80, self.poll_events)
        self.append_log(f'就绪。yt-dlp {engine_version()}，FFmpeg / ffprobe / Deno 已内置。')
        try:
            self.iconbitmap(str(RESOURCE_ROOT / 'app.ico'))
        except tk.TclError:
            pass

    def label(self, parent, text='', **kwargs):
        kwargs.setdefault('font', self.body_font)
        kwargs.setdefault('text_color', TEXT)
        return ctk.CTkLabel(parent, text=text, **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, font=self.body_font,
                            height=35, corner_radius=8,
                            fg_color=ACCENT if primary else '#23334b',
                            hover_color='#296bd3' if primary else '#304562', **kwargs)

    def create_widgets(self):
        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=22, pady=(18, 12))
        header.grid_columnconfigure(1, weight=1)
        badge = ctk.CTkFrame(header, width=48, height=48, corner_radius=13, fg_color=ACCENT)
        badge.grid(row=0, column=0, rowspan=2, padx=(0, 13))
        badge.grid_propagate(False)
        self.label(badge, '↓', font=ctk.CTkFont(size=33, weight='bold')).place(relx=.5, rely=.47, anchor='center')
        self.label(header, '视频与音频下载', font=ctk.CTkFont(family='Microsoft YaHei UI', size=25, weight='bold'), anchor='w').grid(row=0, column=1, sticky='ew')
        self.label(header, '粘贴链接，选择格式，保存到你的电脑。', text_color=MUTED, font=self.small_font, anchor='w').grid(row=1, column=1, sticky='ew')
        self.button(header, '更新引擎', self.update_engine, width=95).grid(row=0, column=2, rowspan=2, padx=(12, 0))
        self.update_button = header.winfo_children()[-1]
        top = ctk.CTkFrame(self, fg_color='transparent')
        top.grid(row=1, column=0, sticky='ew', padx=22, pady=(0, 12))
        top.grid_columnconfigure(0, weight=3)
        top.grid_columnconfigure(1, weight=2)
        links = ctk.CTkFrame(top, fg_color=CARD, corner_radius=13)
        links.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        links.grid_columnconfigure(0, weight=1)
        self.label(links, '01  添加链接', font=self.heading_font, anchor='w').grid(row=0, column=0, sticky='ew', padx=16, pady=(12, 1))
        self.label(links, '每行一个链接；支持视频、音频和播放列表。', text_color=MUTED, font=self.small_font, anchor='w').grid(row=1, column=0, sticky='ew', padx=16)
        self.urls = ctk.CTkTextbox(links, height=122, fg_color=FIELD, text_color=TEXT,
                                  border_width=1, border_color='#2a3a52', corner_radius=8,
                                  font=self.body_font, wrap='word')
        self.urls.grid(row=2, column=0, sticky='ew', padx=16, pady=(7, 9))
        self.paste_button = self.button(links, '粘贴链接', self.paste, width=105)
        self.paste_button.grid(row=3, column=0, sticky='w', padx=16, pady=(0, 13))
        options = ctk.CTkFrame(top, fg_color=CARD, corner_radius=13)
        options.grid(row=0, column=1, sticky='nsew')
        options.grid_columnconfigure(0, weight=1)
        self.label(options, '02  下载设置', font=self.heading_font, anchor='w').grid(row=0, column=0, sticky='ew', padx=16, pady=(12, 7))
        self.mode_menu = ctk.CTkOptionMenu(options, values=list(MODES), variable=self.mode,
                                          command=self.on_mode_change, font=self.body_font,
                                          dropdown_font=self.body_font, fg_color=FIELD,
                                          button_color='#273b58', height=32)
        self.mode_menu.grid(row=1, column=0, sticky='ew', padx=16)
        quality = ctk.CTkFrame(options, fg_color='transparent')
        quality.grid(row=2, column=0, sticky='ew', padx=16, pady=(7, 4))
        quality.grid_columnconfigure(1, weight=1)
        self.label(quality, '视频画质', width=64, anchor='w', font=self.small_font, text_color=MUTED).grid(row=0, column=0)
        self.res_menu = ctk.CTkOptionMenu(quality, values=list(RESOLUTIONS), variable=self.resolution,
                                         font=self.body_font, dropdown_font=self.body_font,
                                         fg_color=FIELD, button_color='#273b58', height=29)
        self.res_menu.grid(row=0, column=1, sticky='ew')
        self.play_switch = ctk.CTkSwitch(options, text='包含整个播放列表', variable=self.playlist,
                                         font=self.body_font, progress_color=ACCENT, switch_width=36, switch_height=19)
        self.play_switch.grid(row=3, column=0, sticky='w', padx=16, pady=(4, 0))
        self.label(options, '关闭时只取单条；纯播放列表只取第一项。', text_color=MUTED, font=self.small_font, anchor='w').grid(row=4, column=0, sticky='ew', padx=16)
        self.proxy_entry = ctk.CTkEntry(options, textvariable=self.proxy, placeholder_text='代理（可选），如 http://127.0.0.1:7890',
                                        fg_color=FIELD, font=self.small_font, height=30)
        self.proxy_entry.grid(row=5, column=0, sticky='ew', padx=16, pady=(6, 12))
        destination = ctk.CTkFrame(self, fg_color=CARD, corner_radius=13)
        destination.grid(row=2, column=0, sticky='ew', padx=22, pady=(0, 12))
        destination.grid_columnconfigure(0, weight=1)
        self.label(destination, '03  保存位置', font=self.heading_font, anchor='w').grid(row=0, column=0, sticky='ew', padx=16, pady=(9, 4))
        self.output_entry = ctk.CTkEntry(destination, textvariable=self.output, font=self.body_font,
                                         fg_color=FIELD, height=33)
        self.output_entry.grid(row=1, column=0, sticky='ew', padx=(16, 8), pady=(0, 12))
        self.browse_button = self.button(destination, '选择目录', self.browse, width=92)
        self.browse_button.grid(row=1, column=1, padx=(0, 8), pady=(0, 12))
        self.button(destination, '打开文件夹', self.open_folder, width=100).grid(row=1, column=2, padx=(0, 16), pady=(0, 12))
        progress = ctk.CTkFrame(self, fg_color=CARD, corner_radius=13)
        progress.grid(row=3, column=0, sticky='ew', padx=22, pady=(0, 12))
        progress.grid_columnconfigure(0, weight=1)
        self.title_label = self.label(progress, '准备就绪', font=self.heading_font, anchor='w', wraplength=580)
        self.title_label.grid(row=0, column=0, columnspan=2, sticky='ew', padx=16, pady=(11, 3))
        self.status_label = self.label(progress, '等待开始', text_color=GREEN, font=self.small_font)
        self.status_label.grid(row=0, column=2, sticky='e', padx=16, pady=(11, 3))
        self.progress = ctk.CTkProgressBar(progress, progress_color=ACCENT, fg_color='#26344b', height=7)
        self.progress.grid(row=1, column=0, columnspan=3, sticky='ew', padx=16, pady=5)
        self.progress.set(0)
        metrics = ctk.CTkFrame(progress, fg_color='transparent')
        metrics.grid(row=2, column=0, sticky='ew', padx=16, pady=(0, 10))
        self.percent_label = self.label(metrics, '0%', font=self.small_font, text_color=MUTED)
        self.percent_label.grid(row=0, column=0, sticky='w')
        self.speed_label = self.label(metrics, '速度  —', font=self.small_font, text_color=MUTED)
        self.speed_label.grid(row=1, column=0, sticky='w')
        self.eta_label = self.label(metrics, '剩余  —', font=self.small_font, text_color=MUTED)
        self.eta_label.grid(row=1, column=1, sticky='w', padx=(18, 0))
        self.stop_button = self.button(progress, '停止', self.stop, width=80, state='disabled')
        self.stop_button.grid(row=2, column=1, padx=(8, 10), pady=(0, 10))
        self.start_button = self.button(progress, '开始下载', self.start_download, primary=True, width=145)
        self.start_button.grid(row=2, column=2, padx=(0, 16), pady=(0, 10))
        logs = ctk.CTkFrame(self, fg_color=CARD, corner_radius=13)
        logs.grid(row=4, column=0, sticky='nsew', padx=22, pady=(0, 9))
        logs.grid_columnconfigure(0, weight=1)
        logs.grid_rowconfigure(1, weight=1)
        self.label(logs, '任务日志', font=self.small_font, text_color=MUTED, anchor='w').grid(row=0, column=0, sticky='ew', padx=16, pady=(8, 3))
        self.log_box = ctk.CTkTextbox(logs, height=95, fg_color=FIELD, text_color='#b8c8de',
                                     font=ctk.CTkFont(family='Microsoft YaHei UI', size=11), wrap='word', state='disabled')
        self.log_box.grid(row=1, column=0, sticky='nsew', padx=12, pady=(0, 10))
        footer = ctk.CTkFrame(self, fg_color='transparent')
        footer.grid(row=5, column=0, sticky='ew', padx=22, pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)
        self.version_label = self.label(footer, f'YT-DLP {engine_version()}  ·  独立版 {APP_VERSION}', font=self.small_font, text_color=MUTED, anchor='w')
        self.version_label.grid(row=0, column=0, sticky='w')
        self.about_button = ctk.CTkButton(footer, text='关于', width=45, height=22, fg_color='transparent',
                                         hover_color='#23334b', font=self.small_font, command=self.about)
        self.about_button.grid(row=0, column=1, sticky='e')
        self.on_mode_change()

    def read_settings(self):
        try:
            return json.loads((STATE_DIR / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        # Links and proxy credentials are deliberately not retained between sessions.
        if self.smoke_mode:
            return
        try:
            (STATE_DIR / 'settings.json').write_text(json.dumps({'output': self.output.get(),
                'mode': self.mode.get(), 'resolution': self.resolution.get()}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as error:
            self.append_log(f'设置未保存：{error}')

    def append_log(self, line):
        line = str(line)
        self.logger.info(line)
        self.log_box.configure(state='normal')
        self.log_box.insert('end', line + '\n')
        if int(self.log_box.index('end-1c').split('.')[0]) > 1500:
            self.log_box.delete('1.0', '301.0')
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

    def paste(self):
        try:
            value = self.clipboard_get()
        except tk.TclError:
            return
        if self.urls.get('1.0', 'end-1c').strip():
            self.urls.insert('end', '\n')
        self.urls.insert('end', value.strip())

    def browse(self):
        destination = filedialog.askdirectory(parent=self, initialdir=self.output.get() if Path(self.output.get()).is_dir() else str(APP_DIR))
        if destination:
            self.output.set(destination)

    def open_folder(self):
        try:
            path = Path(self.output.get()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as error:
            messagebox.showerror('无法打开目录', str(error), parent=self)

    def on_mode_change(self, _value=None):
        self.res_menu.configure(state='normal' if self.mode.get() == MODES[4] and not self.busy else 'disabled')

    def set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        for widget in (self.start_button, self.update_button, self.paste_button, self.browse_button,
                       self.output_entry, self.mode_menu, self.play_switch, self.proxy_entry):
            widget.configure(state=state)
        self.urls.configure(state=state)
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.on_mode_change()

    def animate(self, active):
        if active == self.indeterminate:
            return
        self.indeterminate = active
        if active:
            self.progress.configure(mode='indeterminate')
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate')

    def start_download(self):
        if self.busy:
            return
        try:
            urls = parse_urls(self.urls.get('1.0', 'end-1c'))
            if not self.output.get().strip():
                raise ValueError('请选择保存目录。')
            options = Options(Path(self.output.get()).expanduser().resolve(), self.mode.get(),
                              self.resolution.get(), self.playlist.get(), self.proxy.get())
            build_command(urls[0], options)
        except (ValueError, OSError) as error:
            messagebox.showwarning('检查输入', str(error), parent=self)
            return
        self.save_settings()
        self.current_files.clear()
        self.set_busy(True)
        self.animate(True)
        self.status_label.configure(text=f'队列 {len(urls)} 条链接', text_color=GREEN)
        self.append_log(f'开始任务：{len(urls)} 条链接；{options.mode}；保存到 {options.output}')
        self.runner = Runner(lambda kind, value: self.events.put((kind, value)))
        threading.Thread(target=self.runner.download, args=(urls, options), daemon=True).start()

    def update_engine(self):
        if self.busy:
            return
        self.set_busy(True)
        self.animate(True)
        self.status_label.configure(text='检查更新', text_color=GREEN)
        self.runner = Runner(lambda kind, value: self.events.put((kind, value)))
        threading.Thread(target=self.runner.update, daemon=True).start()

    def stop(self):
        if self.runner and self.busy:
            self.status_label.configure(text='正在停止…', text_color='#fbbf6a')
            self.stop_button.configure(state='disabled')
            threading.Thread(target=self.runner.cancel, daemon=True).start()

    def poll_events(self):
        try:
            for _ in range(100):
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.append_log(value)
                    if value.startswith(('[ExtractAudio]', '[Merger]', '[VideoRemuxer]')):
                        self.status_label.configure(text='转换 / 合并中')
                        self.animate(True)
                elif kind == 'item':
                    self.title_label.configure(text=f"正在解析链接 {value['index']} / {value['total']}…")
                    self.status_label.configure(text=f"链接 {value['index']} / {value['total']}", text_color=GREEN)
                    self.append_log(f"处理链接 {value['index']} / {value['total']}：{value['url']}")
                    self.percent_label.configure(text='正在连接…')
                    self.speed_label.configure(text='速度  —')
                    self.eta_label.configure(text='剩余  —')
                    self.animate(True)
                elif kind == 'title':
                    self.title_label.configure(text=str(value)[:66])
                elif kind == 'progress':
                    fraction = value.get('fraction')
                    self.animate(fraction is None)
                    if fraction is not None:
                        self.progress.set(fraction)
                        self.percent_label.configure(text=f"{100 * fraction:.1f}%  ·  {human_bytes(value.get('downloaded'))} / {human_bytes(value.get('total'))}")
                    self.speed_label.configure(text='速度  ' + (human_bytes(value['speed']) + '/s' if value.get('speed') is not None else '—'))
                    self.eta_label.configure(text='剩余  ' + human_time(value.get('eta')))
                    if value.get('status') == 'finished':
                        self.status_label.configure(text='下载完成，正在处理…')
                elif kind == 'file':
                    self.current_files.append(value)
                    self.append_log(f'文件已保存：{value}')
                elif kind == 'done':
                    self.animate(False)
                    self.set_busy(False)
                    if value['cancelled']:
                        self.title_label.configure(text='任务已停止')
                        self.status_label.configure(text='已停止，可重新开始续传', text_color='#fbbf6a')
                    elif value['failed']:
                        self.title_label.configure(text=f"任务结束：成功 {value['success']}，失败 {value['failed']}")
                        self.status_label.configure(text='详情见下方日志', text_color='#fbbf6a')
                    else:
                        self.title_label.configure(text=f"下载完成 · {value['success']} 条链接")
                        self.status_label.configure(text='已完成', text_color=GREEN)
                        self.progress.set(1)
                        self.percent_label.configure(text='100%')
                    self.append_log(self.title_label.cget('text'))
                elif kind == 'updated':
                    self.version_label.configure(text=f"YT-DLP {value['version']}  ·  独立版 {APP_VERSION}")
                    text = '当前已是官方最新版' if value['unchanged'] else '引擎更新完成'
                    self.title_label.configure(text=f"{text} · {value['version']}")
                    self.status_label.configure(text='就绪', text_color=GREEN)
                    self.append_log(self.title_label.cget('text'))
                elif kind == 'update_error':
                    self.title_label.configure(text='更新未完成，现有引擎仍可使用')
                    self.status_label.configure(text='请查看日志', text_color='#fbbf6a')
                    self.append_log(f'更新失败：{value}')
                elif kind == 'update_done':
                    self.animate(False)
                    self.set_busy(False)
                    if value['cancelled']:
                        self.title_label.configure(text='已取消更新')
                        self.status_label.configure(text='就绪', text_color=GREEN)
        except queue.Empty:
            pass
        except Exception:
            self.logger.exception('UI event processing failed')
        if self.closing and not self.busy:
            self.destroy()
        else:
            self.after(80, self.poll_events)

    def about(self):
        messagebox.showinfo('关于 YT-DLP 下载器',
            f'中文可视化下载器 {APP_VERSION}\nyt-dlp {engine_version()}\n\n'
            '内置 yt-dlp、FFmpeg、ffprobe、Deno。\nWAV 以 32 位浮点保存；FLAC 为 24 位。\n'
            '格式转换无法恢复源音频已损失的细节。\n\n'
            '项目：https://github.com/yt-dlp/yt-dlp\n'
            f'日志与设置：{STATE_DIR}', parent=self)

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno('停止任务并关闭', '当前任务尚未完成。要停止并关闭下载器吗？\n已下载的临时片段会保留以便续传。', parent=self):
                return
            self.closing = True
            self.stop()
            return
        self.save_settings()
        self.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', metavar='REPORT')
    parser.add_argument('--smoke-url')
    parser.add_argument('--smoke-output')
    arguments = parser.parse_args()
    if arguments.self_test:
        report = Path(arguments.self_test)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = diagnostics()
            result['status'] = 'passed'
            code = 0
        except Exception:
            result = {'status': 'failed', 'traceback': traceback.format_exc()}
            code = 1
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return code
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    app = DownloadApp(arguments)
    app.report_callback_exception = lambda kind, value, trace: app.append_log('界面错误：' + ''.join(traceback.format_exception(kind, value, trace)))
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
