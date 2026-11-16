"""Settings page and engine-updater UI of StudioApp (mixin SettingsUI; 1.6.0 WP4).

Never import studio here at module level: studio imports this module.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import engine
import naming
import paths
import ui_theme
import updater
from engine import APP_VERSION, check_cookie_file, engine_version

UPDATE_IDLE = '从官方稳定版更新；下载后校验 SHA256，并确认与本程序兼容。'
DAY = 86400
# The proxy entry and the cookies entry with its two buttons take the same width, so both
# rows of the network card line up on the left and on the right.
COOKIE_ROW_W = 376
COOKIE_BUTTON_W = 60
# (WP7) settings.json 'appearance' value -> label of the 外观 choice
APPEARANCE_LABELS = {'light': '浅色', 'dark': '深色', 'system': '跟随系统'}


class UpdateRun:
    """What self.runner holds during an engine update: stop() calls cancel()."""

    def __init__(self):
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()


def version_newer(latest, current):
    """True when ``latest`` (a yt-dlp tag such as 2026.11.02) is newer than ``current``."""
    def numbers(text):
        return tuple(int(part) for part in re.findall(r'\d+', str(text or '')))
    first, second = numbers(latest), numbers(current)
    if first and second:
        return first > second
    return bool(latest) and str(latest) != str(current)


def component_versions():
    """{'ffmpeg': ..., 'deno': ...} from vendor/manifest.json, looked up by repository."""
    found = {'ffmpeg': '内置版本', 'deno': '内置版本'}
    try:
        manifest = json.loads((paths.VENDOR / 'manifest.json').read_text(encoding='utf-8'))
        for item in manifest if isinstance(manifest, list) else []:
            repository = str(item.get('repository', '')).lower()
            if repository.endswith('ffmpeg-builds'):
                found['ffmpeg'] = str(item.get('display_version') or item.get('version') or found['ffmpeg'])
            elif repository == 'denoland/deno':
                found['deno'] = str(item.get('version') or found['deno'])
    except (OSError, ValueError, AttributeError):
        pass
    return found


def updated_engine_active():
    try:
        return updater.engine_path() != paths.VENDOR / 'yt-dlp.exe'
    except Exception:
        return False


class SettingsUI:
    # ================================================================ page
    def build_settings_page(self):
        page = self.page('settings', '偏好设置', '下载引擎独立更新，界面与个人设置保持不变。', max_width=ui_theme.PAGE_MAX)
        page.grid_rowconfigure(1, weight=1)
        self.settings_area = ui_theme.scroll_area(page)
        self.settings_area.grid(row=1, column=0, sticky='nsew')
        self.settings_area.grid_columnconfigure(0, weight=1)
        self.settings_cards = []

        # 1. engine
        body = self.settings_card('下载引擎')
        engine_row = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0)
        engine_row.grid_columnconfigure(0, weight=1)
        self.version_label = ui_theme.label(engine_row, f'yt-dlp  {engine_version()}', role='heading',
                                            text_color=ui_theme.ACCENT_TEXT, anchor='w')
        self.version_label.grid(row=0, column=0, sticky='ew')
        self.update_status = ui_theme.label(engine_row, UPDATE_IDLE, role='caption', text_color=ui_theme.MUTED,
                                            anchor='w', justify='left', wraplength=360)
        self.update_status.grid(row=1, column=0, sticky='ew', pady=(2, 0))
        ui_theme.wrap_to_width(self.update_status)
        actions = ctk.CTkFrame(engine_row, fg_color='transparent', corner_radius=0)
        actions.grid(row=0, column=1, rowspan=2, sticky='e', padx=(ui_theme.CARD_PAD, 0))
        self.restore_button = ui_theme.button(actions, '恢复内置引擎', self.restore_engine, kind='ghost',
                                              width=ui_theme.fitted_width(actions, '恢复内置引擎'))
        self.restore_button.grid(row=0, column=0, padx=(0, 8))
        update = ui_theme.button(actions, '检查更新', self.update_engine, kind='primary', width=104)
        update.grid(row=0, column=1)
        self.update_buttons.append(update)
        # Shown only while an engine update runs: the page that started it can also stop it.
        self.cancel_update_button = ui_theme.button(actions, '取消', self.stop, kind='ghost', width=72)
        self.cancel_update_button.grid(row=0, column=2, padx=(8, 0))
        self.cancel_update_button.grid_remove()
        self.engine_updated = False
        self.settings_rows(body, [engine_row, ui_theme.setting_row(
            body, '每天自动检查一次新版本', '只访问 api.github.com 查询版本号，不会自动安装。',
            lambda area: ui_theme.switch(area, variable=self.check_engine_updates))])

        # 2. files and naming
        body = self.settings_card('文件与命名')
        self.naming_label = tk.StringVar(value=naming.LABELS.get(self.naming.get(), naming.LABELS['title']))
        self.naming.trace_add('write', lambda *_: self.naming_label.set(naming.LABELS.get(self.naming.get(), '')))
        naming_row = ui_theme.setting_row(
            body, '文件命名',
            '精简标题会去掉“歌手 - 歌名”后面的【】『』等装饰，只用于音频。下载整个播放列表或启用统一归档时，'
            '文件名始终保留 [视频 ID]，以便再次运行时跳过已完成的条目。',
            lambda area: ui_theme.option_menu(area, list(naming.LABELS.values()), variable=self.naming_label,
                                              command=self.on_naming, width=160))
        self.naming_menu = naming_row.control
        tags = ui_theme.setting_row(body, '写入标题、歌手等标签', '同时把来源链接写入文件信息。',
                                    lambda area: ui_theme.switch(area, variable=self.embed_metadata))
        cover = ui_theme.setting_row(body, '嵌入封面图', 'WAV 不支持封面。',
                                     lambda area: ui_theme.switch(area, variable=self.embed_cover))
        skip = ui_theme.setting_row(body, '跳过已下载过的内容',
                                    '按视频和格式记忆；文件被删除或移走后会重新下载。右键任务可“仍然下载”。',
                                    lambda area: ui_theme.switch(area, variable=self.skip_downloaded))
        self.settings_rows(body, [naming_row, tags, cover, skip])
        self.controls += [self.naming_menu, tags.control, cover.control, skip.control]

        # 3. network
        body = self.settings_card('网络')
        proxy = ui_theme.setting_row(body, '代理地址（可选）', '留空使用当前网络；只在本次运行有效，不会保存。',
                                     lambda area: ui_theme.entry(area, textvariable=self.proxy,
                                                                 placeholder='如 http://127.0.0.1:7890',
                                                                 width=COOKIE_ROW_W))

        def cookie_controls(area):
            holder = ctk.CTkFrame(area, fg_color='transparent', corner_radius=0)
            self.cookies_entry = ui_theme.entry(holder, textvariable=self.cookies, placeholder='未选择',
                                                width=COOKIE_ROW_W - 2 * (8 + COOKIE_BUTTON_W))
            self.cookies_entry.grid(row=0, column=0)
            self.choose_cookies_button = ui_theme.button(holder, '选择', self.choose_cookies, width=COOKIE_BUTTON_W)
            self.choose_cookies_button.grid(row=0, column=1, padx=(8, 0))
            self.clear_cookies_button = ui_theme.button(holder, '清除', self.clear_cookies, width=COOKIE_BUTTON_W)
            self.clear_cookies_button.grid(row=0, column=2, padx=(8, 0))
            return holder
        cookies = ui_theme.setting_row(
            body, 'YouTube cookies 文件（可选）',
            '多数情况下不需要：YouTube 的验证通常约 30 分钟后自行解除。想立即继续时，可选择已登录 YouTube（建议小号）的 '
            'Netscape 格式 cookies.txt。该文件等同登录凭证，请勿外传；程序只保存它的路径。', cookie_controls)
        self.settings_rows(body, [proxy, cookies])
        self.cookies.trace_add('write', lambda *_: self.after_idle(self._show_cookie_tail))
        self.controls += [proxy.control, self.cookies_entry, self.choose_cookies_button, self.clear_cookies_button]

        # 4. batch downloads
        body = self.settings_card('批量下载')
        batch = ui_theme.setting_row(body, '稳健模式（推荐）',
                                     '多条链接之间随机间隔 10–20 秒，只下载一条时不等待；遇到网站限流会暂停队列并自动重试。',
                                     lambda area: ui_theme.switch(area, variable=self.batch_safe))
        self.batch_switch = batch.control
        self.settings_rows(body, [batch])
        self.controls.append(self.batch_switch)

        # 5. notifications and interface
        body = self.settings_card('提醒与界面')
        self.settings_rows(body, [
            ui_theme.setting_row(body, '完成时提醒', '窗口在后台时闪烁任务栏按钮，并在标题栏显示结果。',
                                 lambda area: ui_theme.switch(area, variable=self.notify_done)),
            ui_theme.setting_row(body, '识别剪贴板中的链接', '回到窗口时提示可以添加的链接；不会自动开始下载，也不记录剪贴板内容。',
                                 lambda area: ui_theme.switch(area, variable=self.clipboard_hint)),
            ui_theme.setting_row(body, '外观', '深色模式适合夜间使用；“跟随系统”会随 Windows 设置切换。',
                                 self.appearance_control)])

        # 6. app and bundled components
        body = self.settings_card('应用与内置组件')
        versions = component_versions()
        rows = []
        for name, value in (('桌面应用', f'v{APP_VERSION}'), ('FFmpeg / ffprobe', versions['ffmpeg']),
                            ('Deno', versions['deno'])):
            line = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0)
            line.grid_columnconfigure(0, weight=1)
            ui_theme.label(line, name, anchor='w').grid(row=0, column=0, sticky='w')
            ui_theme.label(line, value, text_color=ui_theme.MUTED, anchor='e').grid(row=0, column=1, sticky='e')
            rows.append(line)
        note = ui_theme.label(body, '这些组件随应用打包；“检查更新”只更新 yt-dlp。', role='caption',
                              text_color=ui_theme.MUTED, anchor='w')
        rows.append(note)
        self.settings_rows(body, rows, gap=6)
        self.refresh_engine_labels()

    def settings_card(self, title):
        card = self.card(self.settings_area)
        card.grid(row=len(self.settings_cards), column=0, sticky='ew', pady=(0, ui_theme.GAP))
        card.grid_columnconfigure(0, weight=1)
        self.settings_cards.append(card)
        ui_theme.label(card, title, role='heading', anchor='w').grid(row=0, column=0, sticky='ew',
                                                                     padx=ui_theme.CARD_PAD, pady=(14, 0))
        body = ctk.CTkFrame(card, fg_color='transparent', corner_radius=0)
        body.grid(row=1, column=0, sticky='ew', padx=ui_theme.CARD_PAD, pady=(10, ui_theme.CARD_PAD))
        body.grid_columnconfigure(0, weight=1)
        return body

    def settings_rows(self, body, rows, gap=14):
        for number, row in enumerate(rows):
            row.grid(row=number, column=0, sticky='ew', pady=(0 if number == 0 else gap, 0))

    # ================================================================ appearance (WP7)
    def appearance_control(self, area):
        """The 浅色 / 深色 / 跟随系统 choice; it shows the mode the window was started in."""
        if ctk.AppearanceModeTracker.appearance_mode_set_by == 'system':
            current = 'system'
        else:
            current = 'dark' if ctk.get_appearance_mode() == 'Dark' else 'light'
        self.appearance_choice = ctk.CTkSegmentedButton(area, values=list(APPEARANCE_LABELS.values()), height=32,
                                                        font=ui_theme.font('body'), command=self.choose_appearance)
        self.appearance_choice.set(APPEARANCE_LABELS[current])
        return self.appearance_choice

    def choose_appearance(self, label):
        """Apply the chosen mode at once (no restart) and keep it in settings.json ('appearance')."""
        mode = next((key for key, text in APPEARANCE_LABELS.items() if text == label), None)
        if mode is None:
            return
        if self.appearance_choice.get() != label:
            self.appearance_choice.set(label)
        ctk.set_appearance_mode(mode)
        if isinstance(self.prefs, dict) and self.prefs.get('appearance') != mode:
            self.prefs['appearance'] = mode
            self.schedule_save()

    def on_naming(self, label):
        for key, text in naming.LABELS.items():
            if text == label:
                self.naming.set(key)

    def _show_cookie_tail(self):
        try:
            self.cookies_entry.xview_moveto(1.0)
        except (tk.TclError, AttributeError):
            pass

    # ================================================================ cookies
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
        self.cancel_bot_retry()

    def clear_cookies(self):
        self.cookies.set('')
        self.save_settings()

    # ================================================================ engine updates
    def refresh_engine_labels(self):
        version = engine_version()
        self.version_label.configure(text=f'yt-dlp  {version}')
        self.engine_badge.configure(text=f'yt-dlp {version}')
        self.engine_updated = updated_engine_active()
        self.refresh_engine_controls()
        latest = self.prefs.get('latest_engine') if isinstance(self.prefs, dict) else ''
        if latest and version_newer(latest, version):
            self.app_badge.configure(text=f'v{APP_VERSION} · 引擎有新版', text_color=ui_theme.SIDE_WARN)
        else:
            self.app_badge.configure(text=f'v{APP_VERSION}', text_color=ui_theme.SIDE_MUTED)

    def refresh_engine_controls(self):
        """恢复内置引擎 follows the busy lock like the other controls (it is enabled only while an updated
        engine is active); 取消 is shown only while an engine update runs.  Called from refresh_shell."""
        self.restore_button.configure(state='normal' if self.engine_updated and not self.busy else 'disabled')
        updating = self.busy and self.operation == 'update'
        if updating != bool(self.cancel_update_button.winfo_manager()):
            if updating:
                self.cancel_update_button.grid()
            else:
                self.cancel_update_button.grid_remove()

    def update_engine(self):
        if self.busy:
            return
        self.operation = 'update'
        self.set_busy(True)
        self.progress.set(0, immediate=True)
        self.animate(True)
        self.operation = 'update'
        self.restore_button.configure(state='disabled')
        self.update_status.configure(text='正在检查官方更新…', text_color=ui_theme.ACCENT_TEXT)
        self.run_token += 1
        token = self.run_token

        def emit(kind, value):
            if token == self.run_token:
                self.events.put((kind, value))
        run = UpdateRun()
        self.runner = run
        job = updater.EngineUpdater(emit, run.cancelled, engine.probe_args())
        self.worker = threading.Thread(target=job.run, daemon=True)
        self.worker_operation = 'update'
        self.worker.start()

    def restore_engine(self):
        if self.busy:
            return
        if not messagebox.askyesno('恢复内置引擎', '将删除已更新的 yt-dlp，改用随程序打包的版本。继续吗？', parent=self):
            return
        try:
            updater.restore_bundled()
        except Exception as error:
            messagebox.showwarning('恢复未完成', str(error), parent=self)
        self.refresh_engine_labels()
        self.update_status.configure(text='已改用内置引擎。', text_color=ui_theme.MUTED)
        self.append_log(f'已恢复内置引擎：yt-dlp {engine_version()}')

    def on_update_status(self, value):
        if not (self.busy and self.operation == 'update'):
            return
        self.update_status.configure(text=str(value), text_color=ui_theme.ACCENT_TEXT)

    def on_update_progress(self, value):
        if not (self.busy and self.operation == 'update'):
            return
        fraction = value.get('fraction') if isinstance(value, dict) else None
        if fraction is None:
            return
        self.animate(False)
        self.progress.set(fraction)
        self.update_status.configure(text=f'正在下载新版引擎… {100 * fraction:.0f}%', text_color=ui_theme.ACCENT_TEXT)

    def on_updated(self, value):
        version = value['version']
        self.version_label.configure(text=f'yt-dlp  {version}')
        self.engine_badge.configure(text=f'yt-dlp {version}')
        self.update_status.configure(text=('当前已是官方最新版。' if value['unchanged'] else '更新完成；之后的新任务将使用新版引擎。'),
                                     text_color=ui_theme.OK)
        self.prefs['latest_engine'] = version
        self.append_log(f'引擎检查完成：{version}')

    def on_update_error(self, value):
        self.update_status.configure(text=f'更新未完成，现有引擎仍可使用：{value}', text_color=ui_theme.WARN)
        self.append_log(f'更新失败：{value}')

    def on_update_done(self, value):
        try:
            self.animate(False)
        finally:
            self.set_busy(False)
        if value['cancelled']:
            self.update_status.configure(text='已取消更新。', text_color=ui_theme.MUTED)
        self.refresh_engine_labels()

    # ================================================================ daily release check (func-10)
    def settings_startup(self):
        if not self.smoke_mode:
            self.after(3000, self.daily_engine_check)

    def daily_engine_check(self):
        """At most once a day: ask GitHub for the newest yt-dlp tag (never installs anything)."""
        if self.smoke_mode or not self.check_engine_updates.get():
            return
        # A stamp more than a day in the future (the clock was once set ahead) counts as due;
        # otherwise the check would stay off until that day (core fix review-state-4).
        if -DAY <= time.time() - float(self.prefs.get('last_engine_check') or 0) < DAY:
            return
        current = engine_version()
        events = self.events

        def work():
            try:
                latest = updater.check_latest(timeout=15)['tag']
                events.put(('engine_check', {'latest': latest, 'current': current,
                                             'newer': version_newer(latest, current)}))
            except Exception:
                pass        # every failure is silent; the next start tries again
        threading.Thread(target=work, daemon=True).start()

    def on_engine_check(self, value):
        self.prefs['last_engine_check'] = time.time()
        self.prefs['latest_engine'] = str(value.get('latest') or '')
        self.schedule_save()
        if value.get('newer'):
            if not self.busy:
                self.update_status.configure(text=f"有新版 yt-dlp {value.get('latest')}，点“检查更新”安装。",
                                             text_color=ui_theme.ACCENT_TEXT)
            self.app_badge.configure(text=f'v{APP_VERSION} · 引擎有新版', text_color=ui_theme.SIDE_WARN)
