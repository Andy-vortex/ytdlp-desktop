"""Download page of StudioApp (mixin DownloadUI; 1.6.0 WP4).

Composer card (links, format, destination, start) over a task card (state, metrics, notices,
progress, task rows).  Never import studio here at module level: studio imports this module.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from urllib.parse import unquote

import customtkinter as ctk
import applog
import engine
import formats
import i18n
import messages
import procutil
import ui_theme
import unfinished as unfinished_list
import urls as links
from progress import SmoothProgressBar
from engine import APP_DIR, RESOLUTIONS, LinkTooLong, Options, Runner, build_command
from library_ui import require_job

# 1.5.1 colour names, now ui_theme tokens ((light, dark) pairs); studio re-exports them.
BG, WHITE, INK, MUTED = ui_theme.BG, ui_theme.SURFACE, ui_theme.INK, ui_theme.MUTED
BLUE, BLUE_LIGHT, LINE = ui_theme.ACCENT, ui_theme.ACCENT_SOFT, ui_theme.LINE
SIDE, GREEN, ORANGE = ui_theme.SIDE, ui_theme.OK, ui_theme.WARN
# Event kinds the download history records (library.download_event).
HISTORY_KINDS = ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done')
# The seven preset labels in MODES order (index-based callers such as library_ui keep working).
FORMATS = [preset.label for preset in formats.PRESETS]
TITLE = 'YT-DLP 下载器'

ROW_PITCH = 56                  # logical px per task row (52 + 2 x 2)
ROW_CHUNK = 10                  # at most this many rows built per step of the build queue
ROW_FIRST = 4                   # rows built at once by populate_queue / append_rows (the top of the list)
# (polish, ui-performance-1) A step also ends once it has spent ROW_SLICE s, the layout of its
# rows included: a step must not freeze the window.
ROW_SLICE = 0.1
ROW_STEP_MS = 5                 # pause between two build steps (clicks and scrolls get through)
ROW_LATE = 0.05                 # a step this late found the window busy: it lets the window breathe first
# Rows that get widgets.  Every row costs 15 to 40 ms of Tk work at 200 % scaling, so a long
# job shows its first ROW_LIMIT rows and the footer counts the rest (they download as usual).
ROW_LIMIT = 50
LIST_HANDS_OFF = 10             # s after a hand scroll of the task list in which it does not follow the active row
BOT_RETRY_DELAYS = (1800, 900, 900)
CONVERTING = ('[ExtractAudio]', '[Merger]', '[VideoRemuxer]', '[Metadata]', '[ThumbnailsConvertor]',
              '[EmbedThumbnail]', '[EmbedSubtitle]', '[MoveFiles]')
# state -> (icon, tone, chip text)
STATE_LOOK = {
    'waiting': (ui_theme.ICON_CLOCK, 'idle', '等待中'),
    'active': (ui_theme.ICON_DOWNLOAD, 'info', '解析中'),
    'done': (ui_theme.ICON_DONE, 'ok', '已完成'),
    'failed': (ui_theme.ICON_ERROR, 'danger', '失败'),
    'stopped': (ui_theme.ICON_STOP, 'idle', '已停止'),
    'unstarted': (ui_theme.ICON_PENDING, 'idle', '未开始'),
    'blocked': (ui_theme.ICON_CLOCK, 'warn', '已暂缓'),
    'skipped': (ui_theme.ICON_SKIP, 'idle', '已下载过'),
    'unfinished': (ui_theme.ICON_ERROR, 'warn', '未完成'),
}
RETRYABLE = ('failed', 'stopped', 'unstarted', 'blocked', 'unfinished')
REASON_STATES = ('failed', 'blocked', 'unfinished')      # rows whose menu offers 查看原因
# (final-fix-ui, polish-function-2) A row of the last job whose entry 清除未完成 removed keeps its
# state (its menu still offers 查看原因 and 重试此条) and gets the flag 'cleared', this chip and
# this detail; it no longer counts as unfinished.
CLEARED_CHIP = '已移出'
CLEARED_DETAIL = '已从未完成列表移除'
FINISHED_UNFINISHED = '未完成的链接已保留，可点“重试未完成”，或右键单条重试。'
CLEARED_METRICS = '未完成记录已清除；已下载的文件不受影响，右键单条仍可重试。'
# The header of a job that left links unfinished ('任务结束 · 1 条成功，1 条未完成'), and what
# 清除未完成 puts in place of its unfinished part (1.6.0: '，未完成记录已清除'; 1.7.0: nothing).
FINISHED_PREFIX = '任务结束 · '
# (1.7.0 U5) Nothing: the header becomes '任务结束 · 1 条成功', which fits at 1000x740; the metrics
# line below it says 未完成记录已清除 in full (CLEARED_METRICS).
CLEARED_TITLE_SUFFIX = ''
REASON_WORDS = {'failed': '上次失败', 'stopped': '已停止', 'unstarted': '未开始', 'blocked': 'YouTube 验证'}
HINT_DEFAULT = 'Ctrl+V 粘贴 · Ctrl+Enter 开始'
PLACEHOLDER = '在这里粘贴视频或音频链接，每行一条'
METRICS_IDLE = '添加链接后，点击开始下载'
# The title above it already says "YouTube 暂时拒绝了当前网络"; the metrics line does not repeat it.
BOT_TEXT = '通常约 30 分钟后自行恢复，链接已保留；也可在“偏好设置 → 网络”选择 cookies。'
BLOCKED_DETAIL = '等 YouTube 验证解除后重试'
# While another activity holds the window: (task title, metrics line, stop button) on the download page.
# and the placeholder of the dimmed link box.
FOREIGN_TEXT = {'update': ('正在更新下载引擎…', '更新结束后即可添加链接、开始下载；进度见“偏好设置 → 下载引擎”。', '停止更新',
                           '下载引擎更新中，结束后可在这里粘贴链接'),
                'collect': ('正在网页采集…', '采集结束后即可添加链接、开始下载；进度见“网页采集”。', '停止采集',
                            '网页采集中，结束后可在这里粘贴链接')}
BOT_TEXT_COOKIES = 'YouTube 仍要求验证：cookies 可能已过期。可稍后重试，或在“偏好设置 → 网络”重新选择 cookies。'
# (polish, ui-daily-flow-2) A network failure of a job that went through a proxy.  (1.8.0, D-F46) A
# message: the history stores it with its key; its value is the 1.7.0 text.
PROXY_REASON = messages.msg('download.reason.proxy')
# (polish, ui-daily-flow-4) A job whose links were all downloaded before.
SKIPPED_TEXT = '这些链接以前下载过，文件仍在；右键可“仍然下载”。'
# (1.7.0, I18N.md 8.1) The constants above keep their Chinese values (tests and other modules read
# them); what the page shows comes from the catalogs.  The keys of REASON_WORDS and FOREIGN_TEXT:
REASON_KEYS = {'failed': 'download.reason.failed', 'stopped': 'download.chip.stopped',
               'unstarted': 'download.chip.unstarted', 'blocked': 'download.reason.blocked'}
FOREIGN_KEYS = {'update': ('download.foreign.update.title', 'download.foreign.update.metrics',
                           'download.foreign.update.stop', 'download.foreign.update.placeholder'),
                'collect': ('download.foreign.collect.title', 'download.foreign.collect.metrics',
                            'download.foreign.collect.stop', 'download.foreign.collect.placeholder')}
# (I18N.md 10.5) The title of the task header keeps at least this many logical px; the stats line
# beside it drops its unfinished part when it would leave less.  STATS_GAPS: the padding of the
# stats label and the three buttons right of it.
TITLE_MIN = 160
STATS_GAPS = 40
# The start button's icon (ui_theme.icon's default size) and the spacing CTkButton puts between
# an image and its text (logical px).
START_ICON = 16
IMAGE_SPACING = 6


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


def decode_link_file(path):
    """Text of a link file, decoded like engine.read_url_file (UTF-16 with BOM, UTF-8, GB18030; 5 MB)."""
    path = Path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError(messages.msg('core.links.file_too_large'))
    data = path.read_bytes()
    encodings = ('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(messages.msg('core.links.encoding'))


def link_title(url):
    """A link as a row title (final-fix-ui, final-e2e-ui-3): percent-encoded text is decoded for
    display only (a collected link with a Chinese name stays readable), control characters
    become spaces, then urls.display shortens it.  The stored link never changes."""
    text = str(url or '')
    if '%' in text:
        text = unquote(text, errors='replace')
        text = ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in text)
    return links.display(text)


def history_value(kind, value, proxied):
    """The event value the history stores: as sent, except that a network failure of a job that
    runs through a proxy keeps the proxy wording the task row shows (on_item_done), so the
    history note says the same (final-fix-ui, polish-function-5).  A copy; the window still
    gets the engine's own value."""
    if (proxied and kind == 'item_done' and isinstance(value, dict)
            and value.get('reason') == engine.NETWORK_REASON):
        return {**value, 'reason': PROXY_REASON}
    return value


def text_button_width(parent, text, size='md'):
    """The logical width an AppButton needs to show ``text`` in full (ui_theme.fitted_width's
    measure: the text, the label's 1 px border on each side and the corner columns), without
    fitted_width's 72 px floor."""
    scale = ui_theme._scaling(parent) or 1.0
    measure = ui_theme._measure_font(parent, ui_theme.font('caption' if size == 'sm' else 'body'))
    needed = measure.measure(text) + 2 + 2 * round(ui_theme.RADIUS * scale)
    return int(-(-needed // scale))


def synthetic_done():
    """The terminal event the UI makes up for a download worker that ended without one (WP4.md section C)."""
    return {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [], 'pending_urls': []}


def _cancel(widget, job):
    if job is not None:
        try:
            widget.after_cancel(job)
        except (tk.TclError, ValueError):
            pass


class DownloadUI:
    # ================================================================ page
    def build_download_page(self):
        page = self.page('download', i18n.t('nav.download'), i18n.t('download.subtitle'), max_width=ui_theme.PAGE_MAX)
        page.grid_rowconfigure(2, weight=1)
        pad = ui_theme.CARD_PAD
        self.row_pool = []
        self.row_menu = None
        self.link_after = self.hint_after = self.clip_after = self.bot_after = self.notice_after = None
        self.notice_kind = None
        self.undo_count = 0                 # links the undo notice can put back
        self.list_scrolled_at = 0.0        # when the owner last scrolled the task list by hand
        self.follow_after = None
        self.foreign_activity = None        # 'update' / 'collect' while that activity holds the download page
        self.foreign_saved = None
        self.job_ended = False              # the task rows are those of a job that has ended (footer text)
        self.metrics_base = ''              # metrics text of the running item before the active title is added
        self.job_summary = None             # (success, unfinished) of the job that ended last (I18N.md 8.7)
        # The stats line: (full text, its shorter form or forms): the running stats without the unfinished
        # part, or the shorter forms of a finished job's stats (final fix round 1, ENV-1); None: none.
        self.stats_texts = ('', None)
        # (final fix round 3, ENV3-1) A finished job's stats form is chosen by the title (English only).
        self.stats_title_first = False
        self.stats_after = None
        self.clip_text = self.clip_digest = ''
        self._placeholder_shown = False
        # ------------------------------------------------ composer card
        composer = self.card(page)
        composer.grid(row=1, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        composer.grid_columnconfigure(0, weight=1)
        self.composer = composer
        top = ctk.CTkFrame(composer, fg_color='transparent', corner_radius=0)
        top.grid(row=0, column=0, sticky='ew', padx=pad, pady=(12, 8))
        top.grid_columnconfigure(2, weight=1)
        ui_theme.label(top, i18n.t('download.heading'), role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        self.link_count = ui_theme.chip(top, i18n.t('download.link_count', count=0), 'info')
        self.link_count.grid(row=0, column=1, sticky='w', padx=(10, 0))
        self.link_hint = ui_theme.label(top, '', role='caption', text_color=ui_theme.MUTED, anchor='e')
        self.link_hint.grid(row=0, column=2, sticky='ew', padx=(12, 4))
        ui_theme.fit(self.link_hint, i18n.t('download.hint.default'))
        # (1.7.0, I18N.md 10.1) Fixed widths grow to fit their label: max(<1.6.0 width>, fitted width).
        paste = ui_theme.button(top, i18n.t('download.paste'), self.paste, kind='ghost', size='sm',
                                width=self._fitted(top, 60, 'download.paste', size='sm'))
        paste.grid(row=0, column=3, padx=(4, 0))
        importer = ui_theme.button(top, i18n.t('download.import'), self.import_txt, kind='ghost', size='sm',
                                   width=self._fitted(top, 60, 'download.import', size='sm'))
        importer.grid(row=0, column=4, padx=(4, 0))
        clear = ui_theme.button(top, i18n.t('download.clear_box'), self.clear_links, kind='ghost', size='sm',
                                width=self._fitted(top, 60, 'download.clear_box', size='sm'))
        clear.grid(row=0, column=5, padx=(4, 0))
        self.urls = ui_theme.textbox(composer, height=72, wrap='none')
        self.urls.grid(row=1, column=0, sticky='ew', padx=pad)
        self.link_placeholder = ui_theme.label(self.urls, i18n.t('download.placeholder'), text_color=ui_theme.FAINT,
                                               fg_color=ui_theme.SURFACE_ALT, anchor='w', cursor='xterm')
        self.link_placeholder.bind('<Button-1>', self._placeholder_clicked)
        self.urls.bind('<KeyRelease>', self._links_typed)
        self.urls.bind('<<Modified>>', self._links_modified)
        self.urls.bind('<<Paste>>', self.on_box_paste)
        self.urls.bind('<Control-Return>', self._start_from_box)
        self.urls.bind('<Button-3>', self._box_menu)
        self.link_note = ui_theme.label(composer, '', role='caption', text_color=ui_theme.ACCENT_TEXT, anchor='w')
        self.link_note.grid(row=2, column=0, sticky='ew', padx=pad, pady=(6, 0))
        self.link_note.grid_remove()
        self.clip_bar = ctk.CTkFrame(composer, fg_color=ui_theme.SURFACE_ALT, corner_radius=8)
        self.clip_bar.grid(row=3, column=0, sticky='ew', padx=pad, pady=(6, 0))
        self.clip_bar.grid_columnconfigure(0, weight=1)
        self.clip_label = ui_theme.label(self.clip_bar, '', role='caption', anchor='w')
        self.clip_label.grid(row=0, column=0, sticky='ew', padx=(12, 8), pady=4)
        ui_theme.button(self.clip_bar, i18n.t('download.clip.add'), self.accept_clipboard, kind='ghost', size='sm',
                        width=self._fitted(self.clip_bar, 60, 'download.clip.add', size='sm')).grid(row=0, column=1,
                                                                                                    pady=4)
        ui_theme.button(self.clip_bar, i18n.t('download.clip.ignore'), self.ignore_clipboard, kind='ghost', size='sm',
                        width=self._fitted(self.clip_bar, 60, 'download.clip.ignore', size='sm')).grid(
                            row=0, column=2, padx=(4, 8), pady=4)
        self.clip_bar.grid_remove()
        choices = ctk.CTkFrame(composer, fg_color='transparent', corner_radius=0)
        choices.grid(row=4, column=0, sticky='ew', padx=pad, pady=(10, 0))
        choices.grid_columnconfigure(4, weight=1)
        # (1.7.0, I18N.md 7) The menus show labels and keep the stored values: self.format holds the zh
        # Preset.label (formats.by_label reads it), self.resolution the stored resolution.
        values = [formats.by_id(i).label for i in formats.MENU_ORDER]
        labels = [i18n.label('format', value) for value in values]
        self.format_menu = ui_theme.option_menu(choices, values, labels=labels, variable=self.format,
                                                command=self.on_format,
                                                width=max(250, ui_theme.menu_width(choices, labels)))
        self.format_menu.grid(row=0, column=0, sticky='w')
        labels = [i18n.label('resolution', value) for value in RESOLUTIONS]
        self.quality_menu = ui_theme.option_menu(choices, list(RESOLUTIONS), labels=labels, variable=self.resolution,
                                                 width=max(120, ui_theme.menu_width(choices, labels)))
        self.quality_menu.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.subtitle_check = ui_theme.checkbox(choices, i18n.t('download.subtitles'), variable=self.subtitles)
        self.subtitle_check.grid(row=0, column=2, sticky='w', padx=(16, 0))
        playlist = ui_theme.checkbox(choices, i18n.t('download.playlist'), variable=self.playlist)
        playlist.grid(row=0, column=3, sticky='w', padx=(16, 0))
        destination = ctk.CTkFrame(composer, fg_color='transparent', corner_radius=0)
        destination.grid(row=5, column=0, sticky='ew', padx=pad, pady=(8, 14))
        destination.grid_columnconfigure(1, weight=1)
        self.destination_label = ui_theme.label(destination, i18n.t('download.save_to'), role='caption',
                                                text_color=ui_theme.MUTED)
        self.destination_label.grid(row=0, column=0, padx=(0, 8))
        self.output_entry = ui_theme.entry(destination, textvariable=self.output)
        self.output_entry.grid(row=0, column=1, sticky='ew')
        browse = ui_theme.button(destination, i18n.t('download.change'), self.browse,
                                 width=self._fitted(destination, 60, 'download.change'))
        browse.grid(row=0, column=2, padx=(8, 0))
        self.open_output_button = ui_theme.button(destination, i18n.t('download.open_folder'), self.open_folder,
                                                  width=self._fitted(destination, 92, 'download.open_folder'))
        self.open_output_button.grid(row=0, column=3, padx=(8, 0))
        # The start button fits the wider of its two labels plus its icon: it never changes width
        # when a job starts (I18N.md 10.1).
        icon = ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white')
        extra = START_ICON + IMAGE_SPACING if icon is not None else 0
        self.start_button = ui_theme.button(destination, i18n.t('download.start'), self.start, kind='primary',
                                            width=self._fitted(destination, 132, 'download.start', 'download.start.queue',
                                                               extra=extra),
                                            image=icon, compound='left')
        self.start_button.grid(row=0, column=4, padx=(16, 0))
        self.controls += [self.format_menu, self.subtitle_check, playlist, self.output_entry, browse]
        self.input_controls += [self.urls, paste, importer, clear, self.start_button]
        # ------------------------------------------------ task card
        tasks = self.card(page)
        tasks.grid(row=2, column=0, sticky='nsew')
        tasks.grid_columnconfigure(0, weight=1)
        tasks.grid_rowconfigure(3, weight=1)
        self.task_card = tasks
        header = ctk.CTkFrame(tasks, fg_color='transparent', corner_radius=0)
        header.grid(row=0, column=0, sticky='ew', padx=pad, pady=(12, 0))
        header.grid_columnconfigure(0, weight=1)
        self.task_header = header
        self.current_title = ui_theme.label(header, '', role='heading', anchor='w')
        self.current_title.grid(row=0, column=0, sticky='ew')
        ui_theme.fit(self.current_title, i18n.t('download.title.ready'))
        # (final fix round 3, ENV3-1) While a finished job's stats form depends on the title (English),
        # a new title text measures the room again (ui_theme.fit's configure, wrapped once more).
        fitted_configure = self.current_title.configure

        def configure_title(require_redraw=False, **kwargs):
            fitted_configure(require_redraw=require_redraw, **kwargs)
            if 'text' in kwargs and getattr(self, 'stats_title_first', False):
                self._stats_resized()
        self.current_title.configure = configure_title
        self.queue_stats =ui_theme.label(header, i18n.t('download.stats.none'), role='caption',
                                          text_color=ui_theme.MUTED)
        self.queue_stats.grid(row=0, column=1, padx=(12, 0))
        self.retry_button = ui_theme.button(header, i18n.t('download.retry_unfinished'), lambda: self.retry_failed(),
                                            state='disabled')
        self.retry_button.grid(row=0, column=2, padx=(12, 0))
        self.clear_unfinished_button = ui_theme.button(header, i18n.t('download.clear_unfinished'),
                                                       self.clear_unfinished, kind='ghost', size='sm', state='disabled')
        self.clear_unfinished_button.grid(row=0, column=3, padx=(8, 0))
        self.stop_button = ui_theme.button(header, i18n.t('common.stop'), self.stop, kind='danger',
                                           width=self._fitted(header, 64, 'common.stop'), state='disabled')
        self.stop_button.grid(row=0, column=4, padx=(8, 0))
        self.metrics = ui_theme.label(header, '', role='caption', text_color=ui_theme.MUTED, anchor='w',
                                      height=ui_theme.LABEL_H['caption'])
        self.metrics.grid(row=1, column=0, columnspan=5, sticky='ew')
        ui_theme.fit(self.metrics, i18n.t('download.metrics.idle'))
        # (I18N.md 10.5) The room of the stats line is measured again when the header or one of the
        # buttons beside it (重试未完成 (N), the stop button's label) changes width.
        for widget in (header, self.retry_button, self.clear_unfinished_button, self.stop_button):
            tk.Misc.bind(widget, '<Configure>', self._stats_resized, add='+')
        self.notice_bar = ctk.CTkFrame(tasks, fg_color=ui_theme.SURFACE_ALT, corner_radius=8)
        self.notice_bar.grid(row=1, column=0, sticky='ew', padx=pad, pady=(8, 0))
        self.notice_bar.grid_columnconfigure(0, weight=1)
        self.notice_text = ui_theme.label(self.notice_bar, '', role='caption', anchor='w')
        self.notice_text.grid(row=0, column=0, sticky='ew', padx=(12, 8), pady=4)
        self.notice_buttons = [ui_theme.button(self.notice_bar, '', None, kind='ghost', size='sm') for _ in range(2)]
        for column, button in enumerate(self.notice_buttons, 1):
            button.grid(row=0, column=column, padx=(0, 8 if column == 2 else 4), pady=4)
        self.notice_bar.grid_remove()
        self.progress = SmoothProgressBar(tasks, height=6)
        self.progress.grid(row=2, column=0, sticky='ew', padx=pad, pady=8)
        self.progress.set(0, immediate=True)
        self.task_list = ui_theme.scroll_area(tasks, height=100)
        self.task_list.grid(row=3, column=0, sticky='nsew', padx=8, pady=(0, 8))
        self.task_list.grid_columnconfigure(0, weight=1)
        self.empty = ui_theme.empty_state(tasks, i18n.t('download.empty'), ui_theme.ICON_DOWNLOAD)
        self.empty.grid(row=3, column=0, sticky='nsew', padx=8, pady=(0, 8))
        self.row_footer = ui_theme.label(self.task_list, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        for sequence in ('<MouseWheel>', '<B1-Motion>'):
            self.bind(sequence, self._list_touched, add='+')
        self.bind_download_keys()
        self.playlist.trace_add('write', lambda *_: self.links_changed())

    @staticmethod
    def _fitted(parent, width, *keys, size='md', extra=0):
        """(1.7.0, I18N.md 10.1) max(``width``, the width the widest of the ``keys``' texts needs
        on an AppButton, plus ``extra`` logical px for an image).  Unlike ui_theme.fitted_width
        there is no 72 px floor: a 1.6.0 width below 72 whose label fits stays as it was."""
        return max([width] + [text_button_width(parent, i18n.t(key), size) + extra for key in keys])

    # ================================================================ composer
    def on_format(self, _value=None):
        preset = formats.by_label(self.format.get())
        if self.mode.get() != preset.mode:
            self.mode.set(preset.mode)
        video = preset.kind == 'video'
        state = 'normal' if video and not self.busy else 'disabled'
        # (final fix round 1, polish-function-1) set_busy calls this on every busy switch: a widget is
        # gridded, hidden or reconfigured only when that changes anything.  A CTk option menu redraws
        # itself on every configure() and flushes the pending layout of the whole window
        # (update_idletasks), which made one click on 开始下载 stall the window.
        for widget in (self.quality_menu, self.subtitle_check):
            try:
                shown = bool(widget.winfo_manager())
            except tk.TclError:
                shown = not video
            if video and not shown:
                widget.grid()
            elif not video and shown:
                widget.grid_remove()
            try:
                current = str(widget.cget('state'))
            except (ValueError, tk.TclError):
                current = None
            if current != state:
                widget.configure(state=state)

    def box_text(self):
        return self.urls.get('1.0', 'end-1c')

    def _placeholder_clicked(self, _event=None):
        self.urls.focus_set()
        return 'break'

    def sync_placeholder(self):
        """The placeholder stays while the box is empty, focused or not (the box gets the focus at launch)."""
        try:
            empty = not self.box_text()
            if empty and not self._placeholder_shown:
                self.link_placeholder.place(x=10, y=6)
            elif not empty and self._placeholder_shown:
                self.link_placeholder.place_forget()
            self._placeholder_shown = empty
        except tk.TclError:
            pass

    def _links_modified(self, _event=None):
        try:
            self.urls.edit_modified(False)
        except tk.TclError:
            pass
        self.sync_placeholder()

    def _links_typed(self, _event=None):
        self.sync_placeholder()
        _cancel(self, self.link_after)
        self.link_after = self.after(150, self._links_debounced)

    def _links_debounced(self):
        self.link_after = None
        self.links_changed()

    def links_changed(self):
        """Count the links in the box and say what happens to playlists, mixes and duplicates.  Never raises."""
        try:
            self.sync_placeholder()
            playlist = self.playlist.get()
            result = links.normalize(links.extract_urls(self.box_text()), playlist)
            self.link_count.set(i18n.t('download.link_count', count=len(result.urls)))
            kinds = result.kinds
            note = ''
            if result.dropped_lists and not playlist:
                note = i18n.t('download.note.lists_dropped', count=result.dropped_lists)
            elif playlist and kinds.get('mix'):
                note = i18n.t('download.note.mix')
            elif kinds.get('playlist') and not playlist:
                note = i18n.t('download.note.playlist')
            elif kinds.get('search'):
                note = i18n.t('download.note.search')
            elif kinds.get('channel'):
                note = i18n.t('download.note.channel')
            elif result.duplicates:
                note = i18n.t('download.note.duplicates', count=result.duplicates)
            ui_theme.fit(self.link_note, note)
            if note:
                self.link_note.grid()
            else:
                self.link_note.grid_remove()
        except Exception:
            applog.get('ui').exception('links_changed failed')

    def set_hint(self, text):
        default = '' if getattr(self, 'foreign_activity', None) else i18n.t('download.hint.default')
        ui_theme.fit(self.link_hint, text or default)
        _cancel(self, self.hint_after)
        self.hint_after = None
        if text and text != default:
            self.hint_after = self.after(8000, lambda: self.set_hint(''))

    def box_keys(self):
        playlist = self.playlist.get()
        return {links.link_key(links.clean_url(url, playlist), playlist) for url in links.extract_urls(self.box_text())}

    def add_links(self, text, source='paste'):
        """Append the links found in ``text`` that are not in the box yet; returns how many were added."""
        if self.busy and self.operation != 'download':
            self.set_hint(i18n.t('download.hint.busy'))
            return 0
        found = links.extract_urls(text or '')
        playlist = self.playlist.get()
        known = self.box_keys()
        added, skipped = [], 0
        for url in found:
            key = links.link_key(links.clean_url(url, playlist), playlist)
            if key in known:
                skipped += 1
                continue
            known.add(key)
            added.append(url)
        if added:
            current = self.box_text()
            prefix = '\n' if current and not current.endswith('\n') else ''
            self.urls.insert('end', prefix + '\n'.join(added))
            self.urls.see('end')
        if not found:
            hint = i18n.t('download.hint.no_links_file') if source == 'import' else i18n.t('download.hint.no_links_clip')
        elif skipped:
            hint = i18n.t('download.hint.added_skipped', added=len(added), skipped=skipped)
        else:
            hint = i18n.t('download.hint.added', added=len(added))
        if source == 'import' and found:
            empty = sum(1 for line in (text or '').splitlines()
                        if line.strip() and not line.lstrip().startswith(links.COMMENT) and not links.extract_urls(line))
            if empty:
                hint += i18n.t('download.hint.empty_lines', empty=empty)
        self.set_hint(hint)
        self.links_changed()
        return len(added)

    def add_urls(self, text):
        if self.urls.get('1.0', 'end-1c').strip():
            self.urls.insert('end', '\n')
        self.urls.insert('end', text.strip())
        self.links_changed()

    def paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.set_hint(i18n.t('download.hint.no_links_clip'))
            return 0
        return self.add_links(text, 'paste')

    def on_box_paste(self, _event=None):
        """<<Paste>> in the link box: links are appended by add_links; any other text pastes normally."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return None
        if not links.extract_urls(text):
            return None
        self.add_links(text, 'paste')
        return 'break'

    def clear_links(self):
        self.urls.delete('1.0', 'end')
        self.set_hint('')
        self.links_changed()

    def import_txt(self):
        path = filedialog.askopenfilename(parent=self, title=i18n.t('dialog.import.title'),
                                          filetypes=[(i18n.t('dialog.filetype.text_files'), '*.txt'),
                                                     (i18n.t('dialog.filetype.all'), '*.*')])
        if not path:
            return
        try:
            text = decode_link_file(path)
        except (ValueError, OSError) as error:
            messagebox.showwarning(i18n.t('dialog.import.failed'), messages.error_text(error), parent=self)
            return
        self.add_links(text, 'import')

    def _box_menu(self, event):
        menu = self._menu()
        usable = 'disabled' if self.busy and self.operation != 'download' else 'normal'
        menu.add_command(label=i18n.t('download.paste'), command=self.paste, state=usable)
        menu.add_command(label=i18n.t('download.menu.select_all'),
                         command=lambda: self.urls.tag_add('sel', '1.0', 'end-1c'))
        menu.add_command(label=i18n.t('download.clear_box'), command=self.clear_links, state=usable)
        self._popup(menu, event)
        return 'break'

    def _menu(self):
        if self.row_menu is not None:
            try:
                self.row_menu.destroy()
            except tk.TclError:
                pass
        self.row_menu = tk.Menu(self, tearoff=0, font=(ui_theme.FAMILY, 10),
                                background=ui_theme.resolve(ui_theme.SURFACE), foreground=ui_theme.resolve(ui_theme.INK),
                                activebackground=ui_theme.resolve(ui_theme.ACCENT_SOFT),
                                activeforeground=ui_theme.resolve(ui_theme.INK),
                                disabledforeground=ui_theme.resolve(ui_theme.FAINT), relief='flat', borderwidth=1)
        return self.row_menu

    def _popup(self, menu, event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def browse(self):
        if self.archive_enabled.get():
            self.choose_archive()
            return
        initial = self.output.get() if Path(self.output.get()).is_dir() else str(APP_DIR)
        path = filedialog.askdirectory(parent=self, initialdir=initial)
        if path:
            self.output.set(path)

    def refresh_destination(self):
        enabled = self.archive_enabled.get()
        self.output_entry.configure(textvariable=self.archive_root if enabled else self.output)
        self.destination_label.configure(text=i18n.t('download.archive_to') if enabled else i18n.t('download.save_to'))

    # ================================================================ clipboard chip (ux-3, safe form)
    def check_clipboard(self):
        """Offer links found in the clipboard; never fills the box, never starts anything, never logs the text."""
        self.clip_after = None
        try:
            if (not self.clipboard_hint.get() or self.smoke_mode or self.current_page != 'download'
                    or (self.busy and self.operation != 'download')):
                return self.hide_clipboard()
            try:
                text = self.clipboard_get()
            except tk.TclError:
                return self.hide_clipboard()
            if not isinstance(text, str) or not text.strip():
                return self.hide_clipboard()
            digest = hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()
            if digest == self.prefs.get('last_clip'):
                return self.hide_clipboard()
            playlist = self.playlist.get()
            known = self.box_keys() | {row.get('key') for row in self.row_model}
            fresh = [url for url in links.extract_urls(text)
                     if links.link_key(links.clean_url(url, playlist), playlist) not in known]
            if not fresh:
                return self.hide_clipboard()
            self.clip_text, self.clip_digest = text, digest
            self.clip_bar.grid()
            ui_theme.fit(self.clip_label, i18n.t('download.clip.found', count=len(fresh), title=link_title(fresh[0])))
        except Exception:
            applog.get('ui').exception('clipboard check failed')

    def schedule_clipboard_check(self, _event=None):
        _cancel(self, self.clip_after)
        self.clip_after = self.after(300, self.check_clipboard)

    def hide_clipboard(self):
        self.clip_bar.grid_remove()
        self.clip_text = ''

    def _remember_clip(self):
        if self.clip_digest:
            self.prefs['last_clip'] = self.clip_digest
            self.schedule_save()

    def accept_clipboard(self):
        text = self.clip_text
        self._remember_clip()
        self.hide_clipboard()
        if text:
            self.add_links(text, 'paste')

    def ignore_clipboard(self):
        self._remember_clip()
        self.hide_clipboard()

    # ================================================================ keys and feedback (ux-9, ux-10)
    def bind_download_keys(self):
        self.bind('<Return>', lambda event: self._key(self.start, outside_text=True), add='+')
        for sequence in ('<Control-v>', '<Control-V>'):
            self.bind(sequence, lambda event: self._key(self.paste, outside_text=True), add='+')
        # Both cases (final-fix-ui, final-acceptance-ui-1): with Caps Lock on Tk sends the
        # uppercase keysym, as for Ctrl+V above.
        for sequence in ('<Control-l>', '<Control-L>'):
            self.bind(sequence, lambda event: self._key(self.urls.focus_set), add='+')
        for sequence in ('<Control-e>', '<Control-E>'):
            self.bind(sequence, lambda event: self._key(self.reveal_last_file), add='+')
        self.bind('<F4>', lambda event: self._key(self.open_folder), add='+')
        self.bind('<F5>', lambda event: self._key(self.retry_failed), add='+')
        for number, page in enumerate(('download', 'collect', 'library', 'logs', 'settings'), 1):
            self.bind(f'<Control-Key-{number}>',
                      lambda event, name=page: self._key(lambda: self.show_page(name), any_page=True), add='+')
        self.bind('<FocusIn>', self._window_focus, add='+')

    def _key(self, action, outside_text=False, any_page=False):
        try:
            widget = self.focus_get()
        except (KeyError, tk.TclError):
            widget = None
        if not any_page and self.current_page != 'download':
            return None
        if outside_text and isinstance(widget, (tk.Text, tk.Entry)):
            return None
        action()
        return None

    def _start_from_box(self, _event=None):
        self.start()
        return 'break'

    def reveal_last_file(self):
        if self.last_saved_path:
            procutil.reveal_in_folder(self.last_saved_path)

    def _window_focus(self, event):
        if event.widget is self:
            self.set_window_title()
            self.schedule_clipboard_check()

    def set_window_title(self, prefix=''):
        title = i18n.t('app.title')         # TITLE in zh (I18N.md 8.5)
        try:
            self.title(f'{prefix} — {title}' if prefix else title)
        except tk.TclError:
            pass

    def notify_result(self, success, unfinished):
        """Title and taskbar flash when a job ends while the window is in the background."""
        self.set_window_title()
        try:
            if not self.notify_done.get() or self.smoke_mode or self.focus_displayof() is not None:
                return
        except (KeyError, tk.TclError):
            return
        self.set_window_title(i18n.t('window.title.done', success=success) if not unfinished
                              else i18n.t('window.title.unfinished', unfinished=unfinished))
        try:
            procutil.flash_window(int(self.frame(), 16))
        except (ValueError, tk.TclError):
            pass

    # ================================================================ another activity holds the window
    def refresh_foreign_activity(self):
        """While an engine update or a web collection runs, the download page says so: its task header,
        the stop button's label, the hint and a dimmed link box.  The download header is put back when
        the activity ends.  Called from refresh_shell (so at the end of every set_busy)."""
        foreign = self.operation if self.busy and self.operation in FOREIGN_TEXT else None
        if foreign == self.foreign_activity:
            return
        if foreign is not None and self.foreign_activity is None:
            self.foreign_saved = (ui_theme.full_text(self.current_title), ui_theme.full_text(self.metrics),
                                  self.metrics.cget('text_color'), self.progress.target)
        self.foreign_activity = foreign
        if foreign is not None:
            title, metrics, stop, placeholder = (i18n.t(key) for key in FOREIGN_KEYS[foreign])
            self.current_title.configure(text=title)
            self.metrics.configure(text=metrics, text_color=ui_theme.MUTED)
            self.stop_button.configure(text=stop)
            _cancel(self, self.hint_after)
            self.hint_after = None
            ui_theme.fit(self.link_hint, '')      # no "Ctrl+V 粘贴" while nothing can be pasted
            self.urls.configure(text_color=ui_theme.FAINT, fg_color=ui_theme.SURFACE)
            self.link_placeholder.configure(text=placeholder, fg_color=ui_theme.SURFACE)
            self.hide_clipboard()
        else:
            saved, self.foreign_saved = self.foreign_saved, None
            if saved is not None:
                self.current_title.configure(text=saved[0])
                self.metrics.configure(text=saved[1], text_color=saved[2])
                self.progress.set(saved[3], immediate=True)
            self.stop_button.configure(text=i18n.t('common.stop'))
            ui_theme.fit(self.link_hint, i18n.t('download.hint.default'))
            self.urls.configure(text_color=ui_theme.INK, fg_color=ui_theme.SURFACE_ALT)
            self.link_placeholder.configure(text=i18n.t('download.placeholder'), fg_color=ui_theme.SURFACE_ALT)

    # ================================================================ notices
    def show_notice(self, kind, text, actions=(), timeout=None):
        self.notice_kind = kind
        ui_theme.fit(self.notice_text, text)
        actions = list(actions)[:2]
        for number, button in enumerate(self.notice_buttons):
            if number < len(actions):
                label, command = actions[number]
                button.configure(text=label, command=command)
                button.grid()
            else:
                button.grid_remove()
        self.notice_bar.grid()
        _cancel(self, self.notice_after)
        self.notice_after = None
        if timeout:
            self.notice_after = self.after(timeout, lambda: self.hide_notice(kind))

    def hide_notice(self, kind=None):
        if kind is not None and self.notice_kind != kind:
            return
        self.notice_bar.grid_remove()
        self.notice_kind = None
        if kind != 'bot' and self.bot_retry is not None and self.bot_retry.get('deadline') is not None:
            self.render_bot_notice()
        if kind != 'library' and self.notice_kind is None and getattr(self, 'library_waiting', False):
            self.show_library_notice()      # (1.7.0 U1) the library is still locked

    # Hints other pages put on this page; their texts are the download page's (catalog i18n_download).
    def hint_history_loaded(self):
        """The links of a history record were put into the box (library page, 继续未完成 / 载入)."""
        self.set_hint(i18n.t('download.hint.history_loaded'))

    def hint_subscription_check(self, name):
        """A subscription check holds the window; ``name`` is the subscription's shown name."""
        ui_theme.fit(self.link_hint, i18n.t('download.hint.subscription_check', name=name))

    def undo_launch(self):
        self.hide_notice('undo')
        if not self.last_submitted:
            return
        current = self.box_text()
        self.urls.delete('1.0', 'end')
        self.urls.insert('1.0', self.last_submitted + ('\n' + current if current.strip() else ''))
        self.links_changed()

    # ================================================================ rows (arch-14 reduced)
    def populate_queue(self, urls, options=None, overrides=None):
        """Replace the task list by one waiting row per link.  Built rows are kept for reuse, not destroyed."""
        self.row_generation += 1
        _cancel(self, self.row_after)
        self.row_after = None
        self.row_queue = []
        for row in self.rows:
            row['frame'].grid_remove()
            row['index'] = None
        self.rows = []
        self.row_model = []
        self.selected_row = None
        self.job_ended = False          # set by on_done: the rows are those of a job that has ended
        self.row_footer.grid_remove()
        # Back to the top: a list scrolled down would keep its old scroll region, and the few new
        # rows would sit above the viewport (the canvas never redisplays an item it cannot see).
        try:
            self.task_list.canvas.yview_moveto(0)
        except tk.TclError:
            pass
        self.list_scrolled_at = 0.0
        self.append_rows(urls, options, overrides)

    def append_rows(self, urls, options=None, overrides=None):
        """Waiting rows for ``urls`` (indices continue the job's rows; the unfinished rows below
        them move down); the first rows are built now, the rest in short steps."""
        overrides = overrides or {}
        start = self._tail_start()
        tail = [model['entry'] for model in self.row_model[start:]]
        del self.row_model[start:]
        for url in urls:
            own = overrides.get(url, options)
            playlist = own.playlist if own is not None else False
            self.row_model.append({'index': len(self.row_model), 'url': url, 'key': links.link_key(url, playlist),
                                   'options': own, 'state': 'waiting', 'title': link_title(url),
                                   'detail': i18n.t('download.detail.waiting'), 'chip': None, 'tone': None,
                                   'icon': None, 'path': '', 'error': '', 'entry': None})
        self._append_tail(tail)
        self._rows_changed(start)
        self.refresh_task_view()

    def _unfinished_model(self, entry):
        """The row model of a stored unfinished entry (state 'unfinished', below the job's rows)."""
        # (1.7.0, I18N.md 9.3) The reason word and the format in the running language; the entry
        # keeps its stored values (format key, canonical mode string).
        reason = i18n.t(REASON_KEYS.get(entry.get('reason'), 'download.reason.failed'))
        try:
            label = i18n.label('format', formats.by_key(entry.get('format', '')).label)
        except ValueError:
            label = i18n.label('mode', str((entry.get('options') or {}).get('mode', '')))
        url = entry.get('url', '')
        return {'index': len(self.row_model), 'url': url, 'key': entry.get('key', ''),
                'options': self._entry_options(entry, log=False), 'state': 'unfinished',
                'title': entry.get('title') or link_title(url),
                'detail': i18n.t('download.detail.unfinished', reason=reason, format=label),
                'chip': None, 'tone': None, 'icon': None, 'path': '',
                'error': entry.get('error', ''), 'entry': entry, 'tail': True}

    def show_unfinished_rows(self):
        """Start-up: the stored unfinished entries as rows in state 'unfinished'."""
        self.populate_queue([])
        self.show_unfinished_tail()

    # (polish, ui-daily-flow-1; a deviation from WP4 section E, which shows the unfinished rows at
    # start-up only.)  The task list and 重试未完成 (N) always describe the same links: every stored
    # unfinished entry that no row of the job stands for is shown as an 'unfinished' row below the
    # job's rows ("the tail").  The job's rows stay a prefix of row_model, so the engine's item
    # numbers keep addressing them (row index = item index - 1).
    def _tail_start(self):
        """Index of the first unfinished row below the job's rows (len(row_model) when there is none)."""
        start = len(self.row_model)
        while start and self.row_model[start - 1].get('tail'):
            start -= 1
        return start

    def job_row_count(self):
        """Rows of the current (or last) job, without the unfinished rows below them."""
        return self._tail_start()

    def _row_signature(self, model):
        """(link key, format key) of a row: what the unfinished list stores for it; None when unknown."""
        if model.get('entry') is not None:
            return model['entry'].get('key'), model['entry'].get('format')
        options = model.get('options')
        if options is None and self.job_options:
            options = self.job_options[0]
        if options is None:
            return None
        try:
            preset = formats.by_mode(options.mode)
            return model['key'], formats.format_key(preset, options.resolution)
        except (ValueError, AttributeError):
            return None

    def _append_tail(self, entries):
        """Append ``entries`` that no row stands for as unfinished rows (row_model only)."""
        if not entries:
            return
        signatures = [(model, self._row_signature(model)) for model in self.row_model]
        taken = {signature for _, signature in signatures if signature is not None}
        # A row whose format is unknown (no options anywhere) stands for its link in any format.
        loose = {model['key'] for model, signature in signatures if signature is None}
        for entry in entries:
            signature = (entry.get('key'), entry.get('format'))
            if signature in taken or entry.get('key') in loose:
                continue
            taken.add(signature)
            self.row_model.append(self._unfinished_model(entry))

    def show_unfinished_tail(self, entries=None):
        """Replace the unfinished rows below the job's rows by ``entries`` (default: the stored list)."""
        start = self._tail_start()
        del self.row_model[start:]
        self._append_tail(list(self.unfinished if entries is None else entries))
        self._rows_changed(start)
        self.refresh_task_view()

    def _rows_changed(self, start):
        """row_model changed from index ``start`` on: repaint, drop or build the rows from there."""
        keep = min(len(self.rows), len(self.row_model), self.row_cap())
        for row in self.rows[keep:]:
            row['frame'].grid_remove()
            row['index'] = None
        del self.rows[keep:]
        if self.selected_row is not None and self.selected_row >= start:
            self.selected_row = None
        for index in range(start, keep):
            self.rows[index]['index'] = index
            self._paint_row(self.rows[index], self.row_model[index])
        self.row_queue = []
        self._schedule_rows()

    def row_cap(self):
        """Rows that get widgets: at most ROW_LIMIT, and a Tk window cannot be taller than 32,767 physical px."""
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        return max(1, min(ROW_LIMIT, int(30000 / (ROW_PITCH * scale))))

    def _schedule_rows(self):
        wanted = min(len(self.row_model), self.row_cap())
        start = len(self.rows) + len(self.row_queue)
        self.row_queue.extend(range(start, wanted))
        if self.row_after is None and self.row_queue:
            self.build_rows(self.row_generation, scheduled=False)
        self._update_footer()

    def build_rows(self, generation, scheduled=True):
        if generation != self.row_generation:
            return
        if scheduled:
            self.row_after = None
            # (final fix round 1, polish-function-1) A step whose timer comes much later than asked
            # found the window busy (the click that started a 300-link job: launch and set_busy).
            # Run as the next timer, it would add its whole slice to that stall before the window
            # could repaint or take a click; it waits one more short step instead (once in a row).
            late = time.perf_counter() - getattr(self, 'row_due', 0.0) > ROW_LATE
            if late and not getattr(self, 'row_yielded', False):
                self.row_yielded = True
                self._post_rows(generation)
                return
            self.row_yielded = False
        began = time.perf_counter()
        # The first step runs inside the caller (populate_queue, append_rows): a fixed number of rows,
        # so that a short list is complete when the call returns.  The later steps are timed.
        for _ in range(ROW_CHUNK if scheduled else ROW_FIRST):
            if not self.row_queue:
                break
            index = self.row_queue.pop(0)
            if index == len(self.rows) and index < len(self.row_model):
                self.rows.append(self._make_row(index))
                if not scheduled:
                    continue
                # The layout of the new row now, inside this step: left to the idle loop it piles up
                # behind the next steps and the scrollbar's own update_idletasks pays for all of it
                # at once (stalls of 0.5 to 5 s measured at 200 %, ui-performance-1).
                try:
                    self.update_idletasks()
                except tk.TclError:
                    return
            if scheduled and time.perf_counter() - began >= ROW_SLICE:
                break
        if self.row_queue and self.row_after is None:
            self._post_rows(generation)
        self._update_footer()
        if not self.row_queue and self.view_restore is not None:
            self._restore_offset(*self.view_restore)

    def _post_rows(self, generation):
        """The next build step in ROW_STEP_MS (and when it is due, for build_rows' lateness check)."""
        self.row_due = time.perf_counter() + ROW_STEP_MS / 1000
        self.row_after = self.after(ROW_STEP_MS, self.build_rows, generation)

    def _make_row(self, index):
        if index < len(self.row_pool):
            row = self.row_pool[index]
        else:
            frame = ctk.CTkFrame(self.task_list, fg_color=ui_theme.ROW, corner_radius=8, height=ui_theme.ROW_H,
                                 border_width=0, border_color=ui_theme.ACCENT)
            frame.grid_columnconfigure(1, weight=1)
            icon = ctk.CTkLabel(frame, text='', width=32, height=32)
            icon.grid(row=0, column=0, rowspan=2, padx=(8, 2))
            title = ui_theme.label(frame, '', anchor='w')
            title.grid(row=0, column=1, sticky='ew', pady=(6, 0))
            detail = ui_theme.label(frame, '', role='caption', text_color=ui_theme.MUTED, anchor='w',
                                    height=ui_theme.LABEL_H['caption'])
            detail.grid(row=1, column=1, sticky='ew', pady=(0, 6))
            badge = ui_theme.chip(frame, '', 'idle')
            badge.grid(row=0, column=2, rowspan=2, padx=(8, 12))
            row = {'frame': frame, 'icon': icon, 'title': title, 'detail': detail, 'badge': badge, 'state': None,
                   'index': index, 'url': '', 'key': '', 'path': '', 'error': '', 'look': None, 'selected': False}
            for widget in (frame, icon, title, detail, badge):
                widget.bind('<Button-1>', lambda event, r=row: self.select_row(r['index']), add='+')
                widget.bind('<Double-Button-1>', lambda event, r=row: self.open_row(r['index']), add='+')
                widget.bind('<Button-3>', lambda event, r=row: self.row_context(event, r['index']), add='+')
            self.row_pool.append(row)
        row['index'] = index
        row['frame'].grid(row=index, column=0, sticky='ew', padx=4, pady=2)
        self._paint_row(row, self.row_model[index])
        return row

    def _paint_row(self, row, model):
        state = model['state']
        icon_code, tone, _chip = STATE_LOOK.get(state, STATE_LOOK['waiting'])
        chip_text = i18n.label('row_state', state if state in STATE_LOOK else 'waiting')
        tone = model.get('tone') or tone
        icon_code = model.get('icon') or icon_code
        chip_text = model.get('chip') or chip_text
        detail = model.get('detail') or ''
        detail_text = detail if state == 'unfinished' else f"{model['index'] + 1:02d}  ·  {detail}"
        row.update(state=state, url=model['url'], key=model['key'], path=model.get('path', ''),
                   error=model.get('error', ''))
        look = (icon_code, tone)
        if row.get('look') != look:
            row['look'] = look
            row['icon'].configure(image=ui_theme.icon(icon_code, tone, 16))
        if ui_theme.full_text(row['title']) != model['title'] or not getattr(row['title'], '_fit_bound', False):
            ui_theme.fit(row['title'], model['title'])
        if ui_theme.full_text(row['detail']) != detail_text or not getattr(row['detail'], '_fit_bound', False):
            ui_theme.fit(row['detail'], detail_text)
        row['badge'].set(chip_text, tone)
        selected = self.selected_row == model['index']
        if row.get('selected') != selected:
            row['selected'] = selected
            row['frame'].configure(border_width=1 if selected else 0)

    def update_row(self, index, **fields):
        """Change the model of row ``index`` and repaint it when it is built.  Unknown indices are ignored."""
        if index is None or not 0 <= index < len(self.row_model):
            return
        model = self.row_model[index]
        if 'state' in fields:
            for key in ('chip', 'tone', 'icon'):
                fields.setdefault(key, None)
            fields.setdefault('cleared', False)
        model.update(fields)
        if index < len(self.rows):
            self._paint_row(self.rows[index], model)

    def _update_footer(self):
        if len(self.row_model) > self.row_cap() and not self.row_queue:
            start = self._tail_start()
            hidden_job = max(0, start - len(self.rows))
            hidden_tail = len(self.row_model) - max(start, len(self.rows))
            parts = []
            if hidden_job:
                # (final-fix-ui, polish-function-4) "会照常下载" only until the job has ended; then
                # the hidden rows that did not finish point at 重试未完成.
                if not self.job_ended:
                    parts.append(i18n.t('download.footer.hidden_running', hidden=hidden_job))
                elif self.unfinished and any(model['state'] in RETRYABLE and not model.get('cleared')
                                             for model in self.row_model[len(self.rows):start]):
                    parts.append(i18n.t('download.footer.hidden_retry', hidden=hidden_job))
                else:
                    parts.append(i18n.t('download.footer.hidden', hidden=hidden_job))
            if hidden_tail:
                parts.append(i18n.t('download.footer.tail_other', hidden=hidden_tail) if hidden_job
                             else i18n.t('download.footer.tail', hidden=hidden_tail))
            ui_theme.fit(self.row_footer, i18n.sep('clause_sep').join(parts))
            self.row_footer.grid(row=len(self.rows), column=0, sticky='ew', padx=12, pady=(4, 8))
        else:
            self.row_footer.grid_remove()

    def _list_touched(self, event):
        """A wheel turn over the task list or a drag of its scrollbar: the owner is reading the list."""
        try:
            path = str(event.widget)
            area = str(self.task_list.canvas.master)
            inner = str(self.task_list)
        except (AttributeError, tk.TclError):
            return
        if not path.startswith(area + '.'):
            return
        # tkinter.EventType is a StrEnum: str(event.type) is the number '38', not 'MouseWheel'.
        wheel = event.type == tk.EventType.MouseWheel or str(event.type) == 'MouseWheel'
        if wheel or not (path == inner or path.startswith(inner + '.')):
            self.list_scrolled_at = time.monotonic()

    def follow_row(self, index):
        """Scroll the task list so that row ``index`` is in view, unless the owner scrolled it by hand
        during the last LIST_HANDS_OFF seconds."""
        if index is None or time.monotonic() - self.list_scrolled_at < LIST_HANDS_OFF:
            return
        _cancel(self, self.follow_after)
        self.follow_after = self.after(60, self._follow_now, index)

    def _follow_now(self, index):
        self.follow_after = None
        try:
            if index is None or not 0 <= index < len(self.row_model):
                return
            canvas = self.task_list.canvas
            box = canvas.bbox('all')
            view = canvas.winfo_height()
            if not box or view <= 1:
                return
            content = box[3] - box[1]
            if content <= view:
                return
            pitch = ROW_PITCH * ctk.ScalingTracker.get_widget_scaling(self)
            if index < len(self.rows) and self.rows[index]['frame'].winfo_height() > 1:
                frame = self.rows[index]['frame']
                top, bottom = frame.winfo_y(), frame.winfo_y() + frame.winfo_height()
            else:
                top = min(index, len(self.rows)) * pitch
                bottom = top + pitch
            first, last = canvas.yview()
            if top >= first * content and bottom <= last * content:
                return          # already in view
            # One row of context above the active row, so the finished one before it stays visible.
            canvas.yview_moveto(max(0.0, top - pitch) / content)
        except tk.TclError:
            pass

    def refresh_task_view(self):
        if self.row_model:
            self.empty.grid_remove()
        else:
            self.empty.grid()
        self.refresh_stats()

    def refresh_stats(self, final=None):
        if final is not None:
            # (final fix round 1, ENV-1) The finished job's stats follow the room rule of 10.5 too.
            forms = self.final_stats_forms()
            self._show_stats(final, short=forms[1:] if final == forms[0] else None, title_first=self._title_first())
            return
        if not self.row_model:
            self._show_stats(i18n.t('download.stats.none'))
            return
        states = [row['state'] for row in self.row_model]
        if all(state == 'unfinished' for state in states):
            self._show_stats(i18n.t('download.stats.unfinished', count=len(states)))
            return
        if not self.busy and not states.count('waiting') and not states.count('active'):
            forms = self.final_stats_forms()
            self._show_stats(forms[0], short=forms[1:], title_first=self._title_first())
            return
        text = i18n.t('download.stats.running', done=states.count('done'), active=states.count('active'),
                      waiting=states.count('waiting'))
        if states.count('unfinished'):
            # (I18N.md 10.5) Without its unfinished part when the title would get less than TITLE_MIN:
            # 重试未完成 (N) beside it shows the same count.
            self._show_stats(text + i18n.t('download.stats.running_unfinished', unfinished=states.count('unfinished')),
                             short=text)
            return
        self._show_stats(text)

    def _show_stats(self, text, short=None, title_first=False):
        """Show the stats line; ``short`` is its form without the unfinished part (10.5), or a
        sequence of ever shorter forms (a finished job's stats, final fix round 1 ENV-1): the first
        of ``text`` and the shorter forms that leaves the title its room is shown, else the last.

        (final fix round 3, ENV3-1) ``title_first`` (a finished job's stats in English, _title_first):
        the first form that leaves the task title room for its whole text is shown, else the shortest
        one, so a longer stats form never cuts a title that a shorter one would show in full, and a
        wider window never gives the title less room than a narrower one."""
        if short is not None and not isinstance(short, str):
            short = tuple(form for form in short if form != text) or None
        self.stats_texts = (text, short)
        self.stats_title_first = bool(title_first and short is not None)
        if short is not None:
            forms = (short,) if isinstance(short, str) else short
            if self.stats_title_first:
                need = self._title_need()
                text = next((form for form in (text,) + tuple(forms[:-1]) if self._stats_fit(form, need)), forms[-1])
            elif not self._stats_fit(text):
                text = next((form for form in forms[:-1] if self._stats_fit(form)), forms[-1])
        if self.queue_stats.cget('text') != text:
            self.queue_stats.configure(text=text)

    def _stats_fit(self, text, title=0):
        """Whether ``text`` leaves the task title at least TITLE_MIN logical px of the header, and at
        least ``title`` physical px when given (always True while the header has no width yet: a
        window that is not shown)."""
        try:
            width = self.task_header.winfo_width()
            if width <= 1:
                return True
            scale = ui_theme._scaling(self.task_header) or 1.0
            buttons = sum(button.winfo_reqwidth() for button in (self.retry_button, self.clear_unfinished_button,
                                                                  self.stop_button) if button.winfo_manager())
            room = width - buttons - round(STATS_GAPS * scale) - max(round(TITLE_MIN * scale), title)
            measure = ui_theme._measure_font(self.queue_stats, self.queue_stats.cget('font'))
            return measure.measure(text) + 2 <= room
        except (tk.TclError, AttributeError):
            return True

    @staticmethod
    def _title_first():
        """(final fix round 3, ENV3-1) A finished job's stats form is chosen by the title in English
        only: the Chinese header keeps the room rule of round 1 (its 1.6.0 look, pixel for pixel)."""
        return i18n.language() != 'zh'

    def _title_alternatives(self, title, forms=()):
        """(final fix round 3, ENV3-1) English: the shorter forms (longest first) a finished job's
        ``title`` shows whole when it would be cut; the counts they leave out are in the stats line
        and on 重试未完成 (N) beside it.  Chinese: none (its header looks as before)."""
        forms = [form for form in forms if form != title] if self._title_first() else []
        ui_theme.fit_alternatives(self.current_title, title, forms)

    def _title_need(self):
        """Physical px the task title needs to show its whole text (ui_theme.fit's room of the label)."""
        try:
            label = self.current_title
            scale = ui_theme._scaling(label) or 1.0
            inset = min(label.cget('corner_radius'), round(label.cget('height') / 2))
            measure = ui_theme._measure_font(label, label.cget('font'))
            return measure.measure(ui_theme.full_text(label).replace('\n', ' ')) + 2 * round(inset * scale) + 2
        except (tk.TclError, AttributeError):
            return 0

    def _stats_resized(self, _event=None):
        """The header changed width, or a button beside the stats did: measure the room again
        once the layout is done (at most one pending check)."""
        if getattr(self, 'stats_after', None) is None:
            try:
                self.stats_after = self.after_idle(self._stats_refit)
            except tk.TclError:
                pass

    def _stats_refit(self):
        self.stats_after = None
        text, short = self.stats_texts
        if short is not None:
            self._show_stats(text, short, title_first=self.stats_title_first)

    def final_stats(self):
        """The stats line of a finished job, counted from the rows (polish, ui-daily-flow-1): its
        未完成 counts every row that 重试未完成 would retry, the unfinished rows below the job included."""
        return self.final_stats_forms()[0]

    def final_stats_forms(self):
        """(final fix round 1, ENV-1) The finished job's stats line and its shorter forms, longest
        first: the full line; without the 跳过 / 已移出 parts; only 成功 and 共 (the unfinished count
        is on 重试未完成 (N) beside it, the skipped and removed rows keep their chips).  The room rule
        of I18N.md 10.5 shows the first one that leaves the task title TITLE_MIN px."""
        states = [row['state'] for row in self.row_model]
        cleared = sum(1 for row in self.row_model if row.get('cleared'))
        # (a row still waiting or active when the job has ended did not finish either)
        unfinished = sum((row['state'] in RETRYABLE or row['state'] in ('waiting', 'active')) and not row.get('cleared')
                         for row in self.row_model)
        base = i18n.t('download.stats.final', done=states.count('done'), unfinished=unfinished, total=len(states))
        text = base
        if states.count('skipped'):
            text += i18n.t('download.stats.final_skipped', skipped=states.count('skipped'))
        if cleared:
            text += i18n.t('download.stats.final_cleared', cleared=cleared)
        forms = [text]
        for form in (base, i18n.t('download.stats.final_short', done=states.count('done'), total=len(states))):
            if form not in forms:
                forms.append(form)
        return forms

    def select_row(self, index):
        previous, self.selected_row = self.selected_row, index
        for number in {previous, index}:
            if number is not None and 0 <= number < len(self.rows):
                self._paint_row(self.rows[number], self.row_model[number])

    def open_row(self, index):
        if index is None or not 0 <= index < len(self.row_model):
            return
        model = self.row_model[index]
        path = model.get('path')
        if model['state'] not in ('done', 'skipped') or not path:
            return
        try:
            if Path(path).is_file():
                os.startfile(path)
            else:
                procutil.reveal_in_folder(path)
        except OSError as error:
            messagebox.showerror(i18n.t('dialog.open_file_failed'), messages.error_text(error), parent=self)

    def row_context(self, event, index):
        if index is None or not 0 <= index < len(self.row_model):
            return
        self.select_row(index)
        model = self.row_model[index]
        state, path = model['state'], model.get('path')
        other_busy = self.busy and self.operation != 'download'
        menu = self._menu()

        def enabled(flag):
            return 'normal' if flag else 'disabled'
        menu.add_command(label=i18n.t('download.menu.play'), command=lambda: self.open_row(index),
                         state=enabled(state in ('done', 'skipped') and bool(path)))
        menu.add_command(label=i18n.t('download.menu.show'), command=lambda: procutil.reveal_in_folder(path),
                         state=enabled(bool(path)))
        menu.add_command(label=i18n.t('download.menu.copy_link'), command=lambda: self.copy_text(model['url']))
        menu.add_command(label=i18n.t('download.menu.reason'), command=lambda: self.show_row_reason(index),
                         state=enabled(state in REASON_STATES))
        menu.add_separator()
        menu.add_command(label=i18n.t('download.menu.retry'), command=lambda: self.retry_row(index),
                         state=enabled(state in RETRYABLE and not other_busy))
        menu.add_command(label=i18n.t('download.menu.anyway'), command=lambda: self.retry_row(index),
                         state=enabled(state == 'skipped' and not other_busy))
        menu.add_command(label=i18n.t('download.menu.remove'), command=lambda: self.remove_row(index),
                         state=enabled(not self.busy))
        self._popup(menu, event)

    def row_reason(self, index):
        """The whole reason of an unfinished row (the row itself may show it cut) and the engine's error."""
        model = self.row_model[index]
        reason = str(model.get('detail') or '')
        # (final fix round 3, SWEEP-1) The error of a link that could not start is a core message (a cookies,
        # proxy, source-address or link-length check of build_command; also stored in failed-downloads.json):
        # shown in the running language.  A raw yt-dlp line is plain or Raw and stays as it is.
        error = messages.show(model.get('error') or '')
        return reason + (i18n.t('dialog.reason.engine_error', error=error) if error and error not in reason else '')

    def show_row_reason(self, index):
        if index is None or not 0 <= index < len(self.row_model):
            return
        messagebox.showinfo(i18n.t('dialog.reason.title'), f"{self.row_model[index]['title']}\n\n{self.row_reason(index)}",
                            parent=self)

    def copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _entry_options(self, entry, log=True):
        """Options to retry an unfinished entry with: its own, plus the current proxy, cookies and pacing."""
        url = entry.get('url', '')
        try:
            options = Options.from_dict(entry.get('options') or {}, proxy=self.proxy.get().strip(),
                                        cookies=self.cookies.get().strip(), batch_safe=self.batch_safe.get(),
                                        skip_downloaded=False,
                                        referers={url: entry['referer']} if entry.get('referer') else {})
        except (ValueError, TypeError, KeyError) as error:
            if log:
                self.append_log(i18n.t('log.retry_failed', url=url, error=messages.error_text(error)))
            return None
        if options.archive_root:
            options = dataclasses.replace(options, archive_contexts=self.collection_contexts([url]))
        return options

    def retry_row(self, index):
        """重试此条 / 仍然下载: the row's own options, never skipped; queued while a download runs."""
        if not 0 <= index < len(self.row_model):
            return
        model = self.row_model[index]
        options = model.get('options')
        if model.get('entry') is not None:
            options = self._entry_options(model['entry'])
        if options is None:
            return
        options = dataclasses.replace(options, skip_downloaded=False, proxy=self.proxy.get().strip(),
                                      cookies=self.cookies.get().strip())
        url = model['url']
        # A manual retry of a blocked link replaces the automatic one (WP4.md section E), queued or not.
        blocked = model['state'] == 'blocked' or (model.get('entry') or {}).get('reason') == 'blocked'
        if self.busy and self.operation == 'download' and self.runner is not None:
            accepted = self.runner.enqueue([url], options)
            if accepted:
                if blocked:
                    self.cancel_bot_retry()
                self.append_rows(accepted, options)
                self.totals_changed()
                self.set_hint(i18n.t('download.hint.queued', count=len(accepted)))
            else:
                self.set_hint(i18n.t('download.hint.already_queued'))
            return
        if self.busy:
            return
        if blocked:
            self.cancel_bot_retry()
        self.launch([url], options)

    def remove_row(self, index):
        """从列表移除 (idle only): the row and the matching unfinished entry."""
        if self.busy or not 0 <= index < len(self.row_model):
            return
        model = self.row_model[index]
        signature = self._row_signature(model)
        if signature is not None and any((e.get('key'), e.get('format')) == signature for e in self.unfinished):
            try:
                with unfinished_list.LOCK:
                    entries = unfinished_list.remove(engine.load_unfinished(), {signature})
                    engine.save_unfinished(entries)
            except OSError as error:
                # (final fix round 3, SWEEP-2) unfinished.save's own OSErrors carry core messages.
                messagebox.showwarning(i18n.t('dialog.remove_failed'), messages.error_text(error), parent=self)
                return
            self.unfinished = entries
            self.refresh_failed_button()
        models = [m for m in self.row_model if m is not model]
        offset = self._list_offset()
        self.populate_queue([])
        for number, item in enumerate(models):
            item['index'] = number
            self.row_model.append(item)
        self._schedule_rows()
        self.refresh_task_view()
        # The owner keeps their place: the rebuilt list returns to where it was scrolled.
        self._restore_offset(self.row_generation, offset)

    def _list_offset(self):
        """How far (in canvas pixels) the task list is scrolled down."""
        try:
            canvas = self.task_list.canvas
            box = canvas.bbox('all')
            return canvas.yview()[0] * (box[3] - box[1]) if box else 0.0
        except tk.TclError:
            return 0.0

    def _restore_offset(self, generation, offset):
        """Scroll the task list back to ``offset`` px once the rows of ``generation`` are all built
        (build_rows calls again when its queue runs empty), clamped to the new content height."""
        self.view_restore = None
        if generation != self.row_generation or offset <= 0:
            return
        if self.row_queue:
            self.view_restore = (generation, offset)
            return
        try:
            self.update_idletasks()
            canvas = self.task_list.canvas
            box = canvas.bbox('all')
            view = canvas.winfo_height()
            if not box or view <= 1:
                return
            content = box[3] - box[1]
            canvas.configure(scrollregion=box)
            canvas.yview_moveto(max(0.0, min(offset, content - view)) / content if content > view else 0.0)
        except tk.TclError:
            pass

    # ================================================================ start, launch, enqueue
    def read_form(self):
        """(urls, options, origins) from the form; raises ValueError / OSError with a message for the owner."""
        found = links.parse_download_text(self.box_text())
        playlist = self.playlist.get()
        result = links.normalize(found, playlist)
        output = self.output.get().strip().strip('"').strip()
        if not output:
            raise ValueError(i18n.t('error.output_missing'))
        archive = self.archive_enabled.get()
        root = self.archive_root.get().strip().strip('"').strip()
        if archive and not root:
            raise ValueError(i18n.t('download.error.archive_root_missing'))
        referers = {}
        for url in result.urls:
            referer = self.download_referers.get(url) or self.download_referers.get(result.origin.get(url, url))
            if referer:
                referers[url] = referer
        contexts = {}
        if archive:
            wanted = list(dict.fromkeys(result.urls + [result.origin.get(url, url) for url in result.urls]))
            known = self.collection_contexts(wanted)
            for url in result.urls:
                context = known.get(url) or known.get(result.origin.get(url, url))
                if context:
                    contexts[url] = context
        preset = formats.by_mode(self.mode.get())
        options = Options(Path(output).expanduser().resolve(), self.mode.get(), self.resolution.get(), playlist,
                          self.proxy.get().strip(), self.batch_safe.get(), referers=referers,
                          archive_root=str(Path(root).expanduser().resolve()) if archive else '',
                          archive_contexts=contexts, cookies=self.cookies.get().strip(), naming=self.naming.get(),
                          embed_metadata=self.embed_metadata.get(), embed_cover=self.embed_cover.get(),
                          subtitles=self.subtitles.get() and preset.kind == 'video',
                          skip_downloaded=self.skip_downloaded.get())
        try:
            build_command(result.urls[0], options)
        except LinkTooLong:
            # (final-merge) an over-long link is failed alone by the Runner; launch decides whether
            # anything can start, and 加入队列 hands it on like any other link.
            pass
        return result.urls, options, dict(result.origin)

    def start(self):
        if self.busy and self.operation == 'download':
            self.enqueue_links()
            return
        if self.busy:
            return
        try:
            urls, options, origins = self.read_form()
        except (ValueError, OSError) as error:
            # A core ValueError (urls.parse_download_text) carries a message.
            messagebox.showwarning(i18n.t('dialog.check_input'), messages.error_text(error), parent=self)
            return
        submitted = self.box_text()
        if self.launch(urls, options, origins=origins):
            self.last_submitted = submitted
            self.urls.delete('1.0', 'end')
            self.links_changed()
            self.set_hint('')
            self.undo_count = len(urls)
            self.show_notice('undo', i18n.t('download.notice.started', count=len(urls)),
                             [(i18n.t('download.notice.restore_links'), self.undo_launch)], timeout=10000)

    def launch(self, urls, options, overrides=None, origins=None, auto=False):
        """Start a download job; False (nothing changed) when it cannot start."""
        if self.busy:
            return False
        urls = list(urls)
        overrides = dict(overrides or {})
        if not auto and self.bot_retry is not None and any(links.is_youtube(url) for url in urls):
            self.cancel_bot_retry()
        try:
            if not urls:
                raise ValueError(messages.msg('core.links.none'))
            folders = {}
            # (final-fix-ui, polish-function-1) Each option set is checked once (with the first link
            # that uses it, and again for each distinct referer, the only per-link part that
            # build_command checks): 300 calls cost 0.17 s of file-system probes on the UI thread.
            # The command line of every link is built by the Runner as before.
            # (final-merge, core request 2) An over-long link does not refuse the job: the Runner
            # fails it alone, and its option set is checked again with the next link.  Only when
            # every link is over-long does nothing start.
            checked = set()
            too_long = []
            for url in urls:
                own = overrides.get(url, options)
                signature = (id(own), own.referers.get(url, ''))
                if signature in checked:
                    continue
                try:
                    build_command(url, own)
                except LinkTooLong as error:
                    too_long.append(error)
                    continue
                checked.add(signature)
                folder = Path(own.archive_root) if own.archive_root else Path(own.output)
                folders[str(folder)] = folder
            if len(too_long) == len(urls):
                raise too_long[0]
            for folder in folders.values():
                folder.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as error:
            messagebox.showwarning(i18n.t('dialog.check_input'), messages.error_text(error), parent=self)
            return False
        self.save_settings()
        job_id = None
        try:
            data = {**options.to_dict(), 'urls': urls}
            if overrides:
                data['overrides'] = {url: own.to_dict() for url, own in overrides.items()}
            # (1.8.0, D-F45) A message: stored as its zh text in every language (H2) with its key.
            title = messages.msg('value.job_title.placeholder', count=len(urls),
                                 mode=messages.value('mode', options.mode))
            library = self.library
            # (1.7.0 U1) A library locked by another program: the download starts at once and its
            # record (a PendingJob until then) is written as soon as the lock is gone.
            job_id = self.history_create(lambda: library.create_job('download', title, data))
        except Exception as error:
            applog.get('ui').exception('create_job failed')
            self.append_log(i18n.t('log.history_write_failed', error=messages.error_text(error)))
        self.download_job = job_id
        try:
            self.job_options = (options, overrides)
            self.populate_queue(urls, options, overrides)
            self.show_unfinished_tail()             # the leftovers of earlier jobs stay in view
            self.operation = 'download'
            self.set_busy(True)
            self.metrics.configure(text_color=ui_theme.MUTED)
            self.progress.set(0, immediate=True)
            self.animate(True)
            self.active_index = None
            self.clear_wait()
            self.current_title.configure(text=i18n.t('shell.status.downloading'))
            self.metrics.configure(text=i18n.t('download.metrics.connecting'))
            self.set_window_title()
            label = i18n.label('format', formats.by_mode(options.mode).label)
            self.append_log(i18n.t('log.task_started', count=len(urls), format=label,
                                   folder=options.archive_root or options.output))
            proxied = any(str(own.proxy or '').strip() for own in [options, *overrides.values()])
            emit = self.download_emit(job_id, proxied=proxied)
            self.runner = Runner(emit, library=self.library)
            self.runner.origins = dict(origins or {})
            self.worker = threading.Thread(target=self.runner.download, args=(urls, options, overrides or None),
                                           daemon=True)
            self.worker_operation = 'download'
            self.worker.start()
            self.refresh_shell()
            return True
        except Exception as error:
            applog.get('ui').exception('download could not start')
            self.run_token += 1
            self.set_busy(False)
            self.animate(False)
            # Nothing runs: no waiting rows (the stored unfinished list comes back) and no running job.
            try:
                self.active_index = None
                if self.unfinished and not self.smoke_mode:
                    self.show_unfinished_rows()
                else:
                    self.populate_queue([])
            except Exception:
                applog.get('ui').exception('task list reset failed')
            self.close_history_job(job_id)
            self.current_title.configure(text=i18n.t('download.title.ready'))
            self.metrics.configure(text=i18n.t('download.metrics.idle'))
            messagebox.showerror(i18n.t('dialog.start_failed'), messages.error_text(error), parent=self)
            return False

    def close_history_job(self, job_id):
        """Record a synthetic cancelled end for a history job whose worker never ran or died silently."""
        if not job_id:
            return
        library = self.library
        try:
            # (1.7.0 U1) Behind the job's waiting writes when the library is locked.
            self.history_write(lambda: library.download_event(require_job(job_id), 'done', synthetic_done()))
        except Exception:
            applog.get('ui').exception('history job %s could not be closed', job_id)

    def download_emit(self, job_id, proxied=False):
        """The emit closure of one download job (an activity start: it bumps run_token).

        Events of an older job (token changed) are dropped.  The history is written first, for
        the kinds it records; a failing history never keeps an event from the window.
        ``proxied``: the job runs through a proxy; the history then stores the proxy wording the
        task row shows for a network failure (final-fix-ui, polish-function-5).
        """
        self.run_token += 1
        token = self.run_token
        library = self.library
        writer = self.history_writer
        warned, deferred = [], []

        def emit(kind, value):
            if token != self.run_token:
                return
            try:
                if job_id and kind in HISTORY_KINDS:
                    stored = history_value(kind, value, proxied)
                    # (1.7.0 U1) Written now on this worker thread (long busy timeout), or behind
                    # the job's waiting writes (its record waits for a locked library): the event
                    # reaches the window at once either way, and the history gets it in order.
                    if not writer.run(lambda: library.download_event(require_job(job_id), kind, stored)) and not deferred:
                        deferred.append(True)
                        self.events.put(('library_busy', None))     # the window shows its calm notice
            except Exception:
                applog.get('ui').exception('history write failed (%s)', kind)
                if not warned:
                    warned.append(True)
                    self.events.put(('log', i18n.t('log.history_write_failed_short')))
            finally:
                self.events.put((kind, value))
        return emit

    def enqueue_links(self):
        """加入队列: hand the box's links to the running job; messages go to the hint, never to a dialog."""
        try:
            urls, options, origins = self.read_form()
        except (ValueError, OSError) as error:
            self.set_hint(messages.error_text(error))
            return 0
        runner = self.runner
        if runner is None or not hasattr(runner, 'enqueue'):
            self.set_hint(i18n.t('download.hint.busy'))
            return 0
        runner.origins.update(origins)
        accepted = runner.enqueue(urls, options)
        if accepted:
            self.record_enqueued(runner, accepted, options)
            self.append_rows(accepted, options)
            self.totals_changed()
            self.urls.delete('1.0', 'end')
            self.links_changed()
            self.set_hint(i18n.t('download.hint.queued', count=len(accepted)))
            self.append_log(i18n.t('log.queued', count=len(accepted)))
        elif runner.closed or runner.cancelled.is_set():
            self.set_hint(i18n.t('download.hint.queue_ended'))
        else:
            self.set_hint(i18n.t('download.hint.already_queued'))
        return len(accepted)

    def record_enqueued(self, runner, accepted, options):
        """Write links that 加入队列 handed to the running job into its history record at once.

        Runner.enqueue keeps them in its inbox until the next item starts; a crash, a killed
        process or a power cut during a long item or a 429 cooldown would otherwise lose them
        from the record that 继续未完成 resumes (prefix-queue).  The options of a link go to
        'overrides' only when they differ from the run's, as Runner._queued sends them, so the
        later queued event changes nothing (Library._extend).  A failing history never blocks
        加入队列: it is only logged, as library_ui.resume_later does.
        """
        job = self.download_job
        if not job:
            return
        try:
            base = getattr(runner, 'base_options', None)
            if base is None:
                base = (getattr(self, 'job_options', None) or (None,))[0]
            own = options.to_dict()
            different = base is None or own != base.to_dict()
            library, links = self.library, list(accepted)
            overrides = {url: own for url in accepted} if different else None
            # (1.7.0 U1) A locked library: written later by the HistoryWriter, in click order.  (A
            # caller without the window's writer, such as a test's stand-in window, writes at once.)
            write = getattr(self, 'history_write', None)
            task = lambda: library.extend_job(require_job(job), links, overrides)      # noqa: E731
            if write is None:
                task()
            else:
                write(task)
        except Exception:
            applog.get('ui').exception('加入队列: the added links could not be recorded in the history job')

    def totals_changed(self):
        """Links joined the running job: the task header, the window title and the sidebar count
        them at once, not only when the next item starts (final-fix-ui, final-e2e-ui-2)."""
        if not (self.busy and self.operation == 'download') or self.active_index is None:
            self.refresh_stats()
            return
        index = self.active_index + 1
        total = max(self.job_row_count(), index)
        if not self.foreign_activity:
            self.current_title.configure(text=self._active_title(index, total))
        self.set_window_title(f'{index}/{total}')
        self.refresh_stats()
        self.refresh_shell()

    # ================================================================ unfinished list (ux-7)
    def refresh_failed_button(self):
        count = len(self.unfinished)
        text = i18n.t('download.retry_unfinished_n', count=count) if count else i18n.t('download.retry_unfinished')
        resized = self.retry_button.cget('text') != text
        self.retry_button.configure(text=text, state='normal' if count and not self.busy else 'disabled')
        self.clear_unfinished_button.configure(state='normal' if count and not self.busy else 'disabled')
        if resized and getattr(self, 'stats_texts', ('', None))[1] is not None:
            self._stats_resized()       # the Retry button changed width: the stats line's room did too

    def retry_failed(self, entries=None, auto=False):
        """Retry unfinished entries with their own options; the form, the box and the settings stay as they are."""
        if self.busy:
            return False
        entries = list(self.unfinished if entries is None else entries)
        if not entries:
            return False
        if not auto and any(entry.get('reason') == 'blocked' for entry in entries):
            self.cancel_bot_retry()
        newest = {}
        for entry in entries:
            url = entry.get('url')
            if url and (url not in newest or entry.get('time', 0) > newest[url].get('time', 0)):
                newest[url] = entry
        chosen = {}
        for entry in entries:
            url = entry.get('url')
            if not url:
                continue
            if newest.get(url) is not entry:
                self.append_log(i18n.t('log.retry.other_format_later', url=url))
                continue
            options = self._entry_options(entry)
            if options is not None:
                chosen[url] = options
        if not chosen:
            return False
        urls = list(chosen)
        first = chosen[urls[0]]
        overrides = {url: options for url, options in chosen.items() if options != first}
        return self.launch(urls, first, overrides=overrides, auto=auto)

    def clear_unfinished(self):
        count = len(self.unfinished)
        if self.busy or not count:
            return
        if not messagebox.askyesno(i18n.t('download.clear_unfinished'), i18n.t('dialog.clear_unfinished.text', count=count),
                                   parent=self):
            return
        try:
            engine.save_unfinished([])
        except OSError as error:
            # (final fix round 3, SWEEP-2) unfinished.save's own OSErrors carry core messages.
            messagebox.showwarning(i18n.t('dialog.clear_failed'), messages.error_text(error), parent=self)
            return
        self.unfinished = []
        if self.row_model and all(row['state'] == 'unfinished' for row in self.row_model):
            self.populate_queue([])
        else:
            # (final-fix-ui, polish-function-2) The last job's rows that did not finish are no longer
            # counted as unfinished: 重试未完成 has nothing left to retry.  They keep their reason
            # (查看原因) and 重试此条 in their menu.
            for index, model in enumerate(self.row_model):
                if model['state'] in RETRYABLE and not model.get('tail') and not model.get('cleared'):
                    # A failure keeps its reason; 已保留到未完成列表 and the YouTube wait no longer hold.
                    reason = str(model.get('detail') or '') if model['state'] == 'failed' else ''
                    cleared = i18n.t('download.detail.cleared')
                    self.update_row(index, cleared=True, chip=i18n.t('download.chip.cleared'), tone='idle',
                                    icon=ui_theme.ICON_PENDING, detail=f'{cleared} · {reason}' if reason else cleared)
            self.show_unfinished_tail()
            if ui_theme.full_text(self.metrics) in (i18n.t('download.metrics.finished_unfinished'),
                                                    i18n.t('download.metrics.bot'), i18n.t('download.metrics.bot_cookies')):
                self.metrics.configure(text=i18n.t('download.metrics.cleared'), text_color=ui_theme.MUTED)
            self.refresh_stats(final=self.final_stats())
        # (final fix round 1, rv-findings-1-1) The header of the finished job no longer names
        # unfinished links that the stats line has stopped counting.  (1.7.0, I18N.md 8.7) It is
        # rendered from the job's numbers, not cut out of the shown text.
        success = self._finished_success(ui_theme.full_text(self.current_title))
        if success is not None:
            title = i18n.t('download.title.finished_cleared', success=success)
            self._title_alternatives(title, (i18n.t('download.title.finished'),))
            self.current_title.configure(text=title)
        self.refresh_failed_button()
        self.cancel_bot_retry()

    def _finished_success(self, title):
        """The success count when ``title`` is the header of a job that left links unfinished
        (download.title.finished_unfinished in the running language), else None.  The numbers of
        the job that ended last (on_done) come first; a header written otherwise is read with
        the template, numbers only."""
        summary = getattr(self, 'job_summary', None)
        if summary is not None:
            success, unfinished = summary
            if title == i18n.t('download.title.finished_unfinished', success=success, unfinished=unfinished):
                return success
        shape = i18n.t('download.title.finished_unfinished', success='\x00', unfinished='\x01')
        pattern = ''.join({'\x00': r'(?P<success>\d+)', '\x01': r'\d+'}.get(part, re.escape(part))
                          for part in re.split('([\x00\x01])', shape))
        match = re.fullmatch(pattern, title)
        return int(match.group('success')) if match else None

    # ================================================================ bot-check retry (SPEC 3.11)
    def arm_bot_retry(self, round_number):
        self.bot_retry = {'round': round_number,
                          'deadline': time.monotonic() + BOT_RETRY_DELAYS[round_number - 1]}
        self.render_bot_notice()
        self._schedule_bot_tick()
        self.refresh_shell()

    def cancel_bot_retry(self):
        if self.bot_retry is None:
            return
        self.bot_retry = None
        _cancel(self, self.bot_after)
        self.bot_after = None
        self.hide_notice('bot')
        self.refresh_shell()

    def _schedule_bot_tick(self):
        _cancel(self, self.bot_after)
        self.bot_after = self.after(1000, self.bot_retry_tick)

    def render_bot_notice(self):
        state = self.bot_retry
        # (polish, ui-daily-flow-3) The undo of a job that is still running keeps its place; once the
        # job has ended the countdown and its buttons replace it.
        if state is None or state.get('deadline') is None or (self.notice_kind == 'undo' and self.busy):
            return
        remaining = max(0, math.ceil(state['deadline'] - time.monotonic()))
        self.show_notice('bot', i18n.t('download.notice.bot_retry', time=time_text(remaining), round=state['round'],
                                       rounds=len(BOT_RETRY_DELAYS)),
                         [(i18n.t('download.notice.retry_now'), self.retry_blocked_now),
                          (i18n.t('common.cancel'), self.cancel_bot_retry)])

    def bot_retry_tick(self):
        """Every second while armed: countdown; when due and idle, retry the blocked entries."""
        self.bot_after = None
        state = self.bot_retry
        if state is None or state.get('deadline') is None:
            return
        try:
            if time.monotonic() < state['deadline'] or self.busy:
                self.render_bot_notice()
                self._schedule_bot_tick()
                return
            blocked = [entry for entry in self.unfinished if entry.get('reason') == 'blocked']
            if not blocked:
                self.cancel_bot_retry()
                return
            self.bot_retry = {'round': state['round'], 'deadline': None}      # retrying
            self.hide_notice('bot')
            if not self.retry_failed(blocked, auto=True):
                self.bot_retry = None
                self.refresh_shell()
        except Exception:
            applog.get('ui').exception('bot retry tick failed')

    def retry_blocked_now(self):
        blocked = [entry for entry in self.unfinished if entry.get('reason') == 'blocked']
        self.cancel_bot_retry()
        if blocked:
            self.retry_failed(blocked)

    def _after_bot_job(self, value):
        """on_done: arm, advance or end the automatic retry."""
        state = self.bot_retry
        if value.get('blocked') == 'bot_check':
            self.prefs['bot_check_at'] = time.time()
            self.schedule_save()
        blocked = value.get('blocked') == 'bot_check' and not value.get('cancelled')
        retrying = state is not None and state.get('deadline') is None
        if not blocked or self.cookies.get().strip():
            if retrying:
                self.bot_retry = None
            return
        if retrying:
            next_round = state['round'] + 1
            if next_round > len(BOT_RETRY_DELAYS):
                self.bot_retry = None
                self.show_notice('bot_end', i18n.t('download.notice.bot_gave_up'),
                                 [(i18n.t('common.got_it'), lambda: self.hide_notice('bot_end'))])
                return
            self.arm_bot_retry(next_round)
        elif state is None:
            self.arm_bot_retry(1)

    # ================================================================ progress display
    def clear_wait(self):
        self.wait_context = None
        self.wait_seconds = None

    def render_wait(self):
        if self.wait_context is None:
            return
        # 1 ms of slack: deadline - now can come out as 300.00000000002 in floating point.
        seconds = max(0, math.ceil(self.wait_deadline - time.monotonic() - 0.001))
        if seconds == self.wait_seconds:
            return
        self.wait_seconds = seconds
        reason = messages.show(self.wait_context['reason'])        # a core message (core.wait.*)
        self.live_metrics(i18n.t('download.metrics.wait', reason=reason, time=time_text(seconds)))
        self.update_row(self.active_index, chip=i18n.t('download.chip.waiting'), tone='warn', icon=ui_theme.ICON_CLOCK,
                        detail=f'{reason} · {time_text(seconds)}')
        self.refresh_shell()

    def animate(self, enabled):
        if enabled != self.animating:
            self.animating = enabled
            if enabled:
                self.progress.configure(mode='indeterminate')
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.configure(mode='determinate')

    # ================================================================ event handlers
    def live_metrics(self, text):
        """The metrics line while an item runs.  An item whose row has no widgets (beyond the row
        cap) is also named here, where a narrow window still has room for its title; the task
        title can keep only a few of its characters (final-fix-ui, polish-visual-3)."""
        self.metrics_base = text
        row = self.active_index
        if self.operation == 'download' and row is not None and len(self.rows) <= row < len(self.row_model):
            text = i18n.t('download.metrics.current', text=text, title=self.row_model[row]['title'])
        self.metrics.configure(text=text)

    def on_log(self, value):
        self.append_log(value)
        if self.operation == 'download' and str(value).startswith(CONVERTING):
            self.clear_wait()
            self.update_row(self.active_index, chip=i18n.t('download.chip.converting'), tone='info', icon=None)
            self.live_metrics(i18n.t('download.metrics.converting'))
            self.animate(True)

    def on_item(self, value):
        self.clear_wait()
        self.progress.set(0, immediate=True)
        index, total = value['index'], value['total']
        self.active_index = index - 1
        if self.active_index >= self._tail_start():
            self.append_rows([value.get('url', '')])
        self.update_row(self.active_index, state='active', detail=i18n.t('download.detail.fetching'))
        self.current_title.configure(text=self._active_title(index, total))
        self.metrics.configure(text_color=ui_theme.MUTED)
        self.live_metrics(i18n.t('download.metrics.connecting'))
        self.animate(True)
        self.append_log(i18n.t('log.link_n', index=index, total=total, url=value['url']))
        self.set_window_title(f'{index}/{total}')
        self.title_stamp = time.monotonic()
        self.refresh_stats()
        self.refresh_shell()
        self.follow_row(self.active_index)

    def _active_title(self, index, total):
        """The task title while item ``index`` of ``total`` runs.  An item whose row has no widgets
        (beyond the row cap, ui-performance-1) is named in the title, so that it stays in view."""
        text = (i18n.t('download.title.downloading_n', index=index, total=total) if total != 1
                else i18n.t('shell.status.downloading'))
        row = index - 1
        if row >= len(self.rows) and 0 <= row < len(self.row_model):
            text += f" · {self.row_model[row]['title']}"
        return text

    def on_title(self, value):
        if self.operation == 'download' and self.active_index is not None:
            self.update_row(self.active_index, title=messages.show(value))
            if self.active_index >= len(self.rows):
                total = max(self.job_row_count(), self.active_index + 1)
                self.current_title.configure(text=self._active_title(self.active_index + 1, total))
                if self.busy and not self.foreign_activity and self.metrics_base:
                    self.live_metrics(self.metrics_base)

    def on_waiting(self, value):
        # The engine repeats 'waiting' every second of a wait: follow the active row only when the wait
        # starts, so that a list the owner scrolled stays where it is for the rest of a long pause.
        starting = self.wait_context is None
        self.wait_context = value
        self.wait_deadline = time.monotonic() + value['seconds']
        self.wait_seconds = None
        self.animate(False)
        self.render_wait()
        if starting:
            self.follow_row(self.active_index)

    def on_resuming(self, value):
        self.clear_wait()
        self.live_metrics(i18n.t('download.metrics.resuming'))
        self.update_row(self.active_index, chip=i18n.t('download.chip.processing'), tone='info', icon=None)
        self.animate(True)
        self.refresh_shell()

    def on_progress(self, value):
        self.clear_wait()
        fraction = value.get('fraction')
        self.animate(fraction is None)
        if fraction is not None:
            self.progress.set(fraction)
        percent = f'{100 * fraction:.1f}%' if fraction is not None else i18n.t('download.chip.downloading')
        speed = bytes_text(value.get('speed')) + '/s' if value.get('speed') is not None else '—'
        text = i18n.t('download.metrics.progress', percent=percent, done=bytes_text(value.get('downloaded')),
                      total=bytes_text(value.get('total')), speed=speed, eta=time_text(value.get('eta')))
        if not self.foreign_activity:       # an update's legacy progress event keeps the update header
            self.live_metrics(text)
        if self.operation == 'download':
            self.update_row(self.active_index,
                            chip=percent if value.get('status') == 'downloading' else i18n.t('download.chip.processing'),
                            tone='info', icon=None, detail=speed)
            now = time.monotonic()
            if self.active_index is not None and now - self.title_stamp >= 1:
                self.title_stamp = now
                self.set_window_title(f'{self.active_index + 1}/{self.job_row_count()} · {percent}')
        else:
            self.update_status.configure(text=i18n.t('settings.update.downloading_pct', percent=percent))

    def on_media(self, value):
        if not isinstance(value, dict):
            return
        path = value.get('filepath')
        if isinstance(path, str) and path:
            self.last_saved_path = path
        index = value.get('index')
        if isinstance(index, int):
            fields = {}
            if isinstance(path, str) and path:
                fields['path'] = path
            if isinstance(value.get('title'), str) and value['title']:
                fields['title'] = value['title']
            self.update_row(index - 1, **fields)

    def on_file(self, value):
        self.append_log(i18n.t('log.file_saved', path=value))
        if isinstance(value, str) and value:
            self.last_saved_path = value
            index = self.active_index
            if index is not None and 0 <= index < len(self.row_model) and not self.row_model[index].get('path'):
                self.row_model[index]['path'] = value

    def on_queued(self, value):
        try:
            urls = list(value.get('urls') or [])
            first = int(value.get('first_index') or 0) - 1
        except (TypeError, ValueError, AttributeError):
            return
        missing = first + len(urls) - self._tail_start()
        if first >= 0 and missing > 0:
            base = self.job_options[0] if self.job_options else None
            entries = value.get('items')
            if isinstance(entries, list) and len(entries) == len(urls):
                # (1.7.0, request of fix-core) The additive 'items' carry each moved item's own
                # options, so a link queued in two formats gets a row per format.  Consecutive
                # entries with the same options are appended together.
                runs = []
                for entry, url in zip(entries[len(entries) - missing:], urls[len(urls) - missing:]):
                    own = self._queued_options(entry, base)
                    if runs and runs[-1][1] == own:
                        runs[-1][0].append(url)
                    else:
                        runs.append(([url], own))
                for run_urls, own in runs:
                    self.append_rows(run_urls, own)
            else:
                self.append_rows(urls[len(urls) - missing:], base)
        self.totals_changed()

    def _queued_options(self, entry, base):
        """The options of one entry of a queued event's 'items' (the job's base options when it
        names none or they cannot be used); the session's proxy and cookies come from the base."""
        own = entry.get('options') if isinstance(entry, dict) else None
        if not isinstance(own, dict):
            return base
        try:
            if base is not None:
                return Options.from_dict(own, proxy=base.proxy, cookies=base.cookies)
            return Options.from_dict(own)
        except (ValueError, TypeError):
            return base

    def on_item_done(self, value):
        self.clear_wait()
        index = value['index'] - 1
        if index >= self._tail_start():
            self.append_rows([value.get('url', '')])
        model = self.row_model[index] if 0 <= index < len(self.row_model) else {}
        fields = {}
        if isinstance(value.get('title'), str) and value['title']:
            fields['title'] = value['title']
        path = value.get('path') if isinstance(value.get('path'), str) and value.get('path') else model.get('path', '')
        if path:
            fields['path'] = path
        skipped = value.get('skipped') or ''
        if skipped == 'downloaded':
            # The title is on the line above; the earlier file (when known) says where it is.
            earlier = Path(path).name if path else i18n.t('download.detail.skipped')
            self.update_row(index, state='skipped', detail=i18n.t('download.detail.skipped_anyway', earlier=earlier),
                            **fields)
        elif skipped == 'bot_check':
            self.update_row(index, state='blocked', detail=i18n.t('download.detail.blocked'), **fields)
        elif value['cancelled']:
            self.update_row(index, state='stopped', detail=i18n.t('download.detail.kept'), **fields)
        elif value['success']:
            detail = i18n.t('download.detail.no_new_file')
            if path:
                try:
                    detail = f'{Path(path).name} · {bytes_text(os.path.getsize(path))}'
                except OSError:
                    detail = Path(path).name
            elif value.get('files'):
                detail = i18n.t('download.detail.files_saved', files=value['files'])
            self.update_row(index, state='done', detail=detail, **fields)
        else:
            canonical = value.get('reason') or (engine.explain_error('', bot_check=True) if value.get('bot_check')
                                                else None)
            # (1.8.0) The engine's reason is a message: compared as it is (its value is the 1.7.0 text),
            # shown in the running language; the raw engine error (the row's 'error') stays raw.
            reason = messages.show(canonical) if canonical else i18n.t('download.detail.see_log')
            # (polish, ui-daily-flow-2) A transport failure is never an outdated engine; through a
            # proxy it names the proxy (a deviation from WP2/WP4, which add the prefix on every stale flag).
            network = canonical == engine.NETWORK_REASON
            if network and self._row_proxy(model):
                reason = i18n.t('download.reason.proxy')
            if value.get('stale_engine') and not network:
                # The way out comes first: the end of a long reason is what the row cuts.
                reason = (i18n.t('download.detail.stale_update', reason=reason) if canonical == engine.STALE_REASON
                          else i18n.t('download.detail.stale_maybe', reason=reason))
            self.update_row(index, state='failed', detail=reason, error=value.get('error') or '', **fields)
        self.refresh_stats()

    def _row_proxy(self, model):
        """The proxy the row's link was downloaded through ('' for none)."""
        options = model.get('options') or (self.job_options[0] if self.job_options else None)
        proxy = getattr(options, 'proxy', None) if options is not None else self.proxy.get()
        return str(proxy or '').strip()

    def on_done(self, value):
        try:
            self.clear_wait()
            self.animate(False)
            if 'failed_urls' in value:
                self.unfinished = engine.load_unfinished()
            self.job_ended = True
        finally:
            self.set_busy(False)
        cancelled = bool(value['cancelled'])
        engine_error = bool(value.get('engine_error'))
        for index, model in enumerate(self.row_model):
            if model['state'] == 'active':
                self.update_row(index, state='stopped' if cancelled else 'failed',
                                detail=i18n.t('download.detail.kept') if cancelled else i18n.t('download.detail.see_log'))
            elif model['state'] == 'waiting' and (cancelled or engine_error):
                self.update_row(index, state='unstarted', detail=i18n.t('download.detail.kept'))
        self.active_index = None
        success = value['success']
        unfinished = max(len(value['pending_urls']) if 'pending_urls' in value else 0, value['failed'])
        skipped = value.get('skipped') or 0
        self.job_summary = (success, unfinished)
        color = ui_theme.MUTED
        # (polish, ui-daily-flow-1) The leftovers of earlier jobs come back below this job's rows.
        self.show_unfinished_tail()
        shorter = ()        # (final fix round 3, ENV3-1) shorter forms of the title (_title_alternatives)
        if cancelled:
            title, metrics = i18n.t('download.title.stopped'), i18n.t('download.metrics.stopped')
        elif engine_error:
            title, metrics, color = (i18n.t('download.title.engine_error'), i18n.t('download.metrics.engine_error'),
                                     ui_theme.WARN)
        elif value.get('bot_check'):
            title = i18n.t('download.title.bot')
            metrics = i18n.t('download.metrics.bot_cookies') if self.cookies.get().strip() else i18n.t('download.metrics.bot')
            color = ui_theme.WARN
        elif unfinished:
            title = i18n.t('download.title.finished_unfinished', success=success, unfinished=unfinished)
            metrics = i18n.t('download.metrics.finished_unfinished')
            shorter = (i18n.t('download.title.finished_unfinished_short', unfinished=unfinished),
                       i18n.t('download.title.finished'))
        elif skipped and not success:
            # (polish, ui-daily-flow-4; WP4 has no text for a job that only skipped links)
            title, metrics = i18n.t('download.title.all_skipped', skipped=skipped), i18n.t('download.metrics.skipped')
        elif skipped:
            title = i18n.t('download.title.done_skipped', success=success, skipped=skipped)
            metrics = i18n.t('download.metrics.files_saved')
            shorter = (i18n.t('download.title.done_skipped_short', skipped=skipped), i18n.t('download.title.done_short'))
        else:
            title = i18n.t('download.title.done', success=success)
            metrics = i18n.t('download.metrics.files_saved')
            shorter = (i18n.t('download.title.done_short'),)
        self._title_alternatives(title, shorter)
        if value.get('archive_errors'):
            metrics, color = i18n.t('download.metrics.archive_errors'), ui_theme.WARN
        if not cancelled and not value['failed'] and not unfinished:
            self.progress.set(1)
        elif not cancelled and unfinished:
            # (polish, ui-daily-flow-7) Not the 100 % of the last item: the share that succeeded.
            self.progress.set(success / (success + unfinished))
        self.current_title.configure(text=title)
        self.metrics.configure(text=metrics, text_color=color)
        self.refresh_stats(final=self.final_stats())
        self.append_log(title)
        if value.get('failed_path') and (value.get('unfinished') or unfinished):
            # (polish, ui-wording-consistency-8; WP4.md:77 says 失败清单) the list is 未完成 everywhere else.
            self.append_log(i18n.t('log.unfinished_list', path=value['failed_path']))
        if self.notice_kind == 'undo':
            # (polish, ui-daily-flow-3) The job has ended: the undo no longer reads as a start.
            ui_theme.fit(self.notice_text, i18n.t('download.notice.undo', count=self.undo_count))
        try:
            self.notify_result(success, 0 if cancelled else unfinished)
        except Exception:
            applog.get('ui').exception('notify failed')
        self._after_bot_job(value)
        self.refresh_shell()
        self.refresh_library()

    # ================================================================ start-up
    def download_startup(self):
        """Start-up hooks: focus on the link box; outside smoke mode the unfinished rows, the relaunch
        hint and the clipboard check."""
        self.after(100, self.urls.focus_set)
        if self.smoke_mode:
            return
        if self.unfinished:
            self.show_unfinished_rows()
        since = time.time() - float(self.prefs.get('bot_check_at') or 0)
        if 0 <= since < 1800:
            self.metrics.configure(text=i18n.t('download.metrics.bot_recent', minutes=max(1, int(since // 60))))
        self.after(300, self.check_clipboard)
