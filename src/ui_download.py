"""Download page of StudioApp (mixin DownloadUI; 1.6.0 WP4, 2.0.0 S4 layout).

One sheet, no cards: the link box (count chip and '⋯' in its corner), the link note, the clipboard
notice, the option row and the folder / start row; below them the task area (notice, a two-line task
header, the progress bar, the task rows, or the empty state).  Every action has a visible control:
link_menu, task_menu and the selected row's row_menu; right clicks mirror them (*_menu_items).
Never import studio here at module level: studio imports this module.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import os
from pathlib import Path
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
from engine import RESOLUTIONS, LinkTooLong, Options, Runner, build_command
from paths import APP_DIR
from library_ui import require_job

# Event kinds the download history records (library.download_event).
HISTORY_KINDS = ('item', 'title', 'media', 'file', 'queued', 'item_done', 'done')

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
# state -> (icon, tone); the chip text is i18n.label('row_state', state)
STATE_LOOK = {
    'waiting': (ui_theme.ICON_CLOCK, 'idle'),
    'active': (ui_theme.ICON_DOWNLOAD, 'info'),
    'done': (ui_theme.ICON_DONE, 'ok'),
    'failed': (ui_theme.ICON_ERROR, 'danger'),
    'stopped': (ui_theme.ICON_STOP, 'idle'),
    'unstarted': (ui_theme.ICON_PENDING, 'idle'),
    'blocked': (ui_theme.ICON_CLOCK, 'warn'),
    'skipped': (ui_theme.ICON_SKIP, 'idle'),
    'unfinished': (ui_theme.ICON_ERROR, 'warn'),
}
RETRYABLE = ('failed', 'stopped', 'unstarted', 'blocked', 'unfinished')
REASON_STATES = ('failed', 'blocked', 'unfinished')      # rows whose menu offers 查看原因
# (polish, ui-daily-flow-2) A network failure of a job that went through a proxy.  (1.8.0, D-F46) A
# message: the history stores it with its key; its value is the 1.7.0 text.
PROXY_REASON = messages.msg('download.reason.proxy')
# The keys of the reason a row shows (download.reason.* or a chip) and of the task title while another
# activity holds the window (the link box is dimmed, the title says who runs).
REASON_KEYS = {'failed': 'download.reason.failed', 'stopped': 'download.chip.stopped',
               'unstarted': 'download.chip.unstarted', 'blocked': 'download.reason.blocked'}
FOREIGN_KEYS = {'update': 'download.foreign.update.title', 'collect': 'download.foreign.collect.title'}
# Notices of the task area shown in the warning tone (the others: the idle tone).
WARN_NOTICES = ('bot', 'bot_end', 'settings', 'bot_recent')
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


def synthetic_done():
    """The terminal event the UI makes up for a download worker that ended without one (WP4.md section C)."""
    return {'success': 0, 'failed': 0, 'files': [], 'cancelled': True, 'failed_urls': [], 'pending_urls': []}


class DownloadUI:
    # ================================================================ page
    def build_download_page(self):
        page = self.page('download', i18n.t('nav.download'), max_width=ui_theme.PAGE_MAX)
        page.grid_rowconfigure(2, weight=1)
        self.row_pool = []
        self.link_after = self.hint_after = self.clip_after = self.bot_after = self.notice_after = None
        self.notice_kind = None
        self.list_scrolled_at = 0.0        # when the owner last scrolled the task list by hand
        self.follow_after = None
        self.foreign_activity = None        # 'update' / 'collect' while that activity holds the download page
        self.foreign_saved = None
        self.job_ran = False                # a download ran in this session (the progress bar has a value)
        self.link_total = 0                 # links in the box (links_changed)
        self.note_text = ('', 'warn')       # the lasting note of the box's links (links_changed)
        self.hint_note = None               # (text, tone) of a one-off feedback (set_hint), shown first
        self.retry_count = None             # the unfinished count refresh_failed_button showed last
        self.row_menu_at = None             # the row frame the row '⋯' sits in
        self.clip_text = self.clip_digest = ''
        self.clip_seen = ''                 # digest of the text last added or ignored; memory only, never saved
        # ------------------------------------------------ input: links, how, where and start
        composer = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        composer.grid(row=1, column=0, sticky='ew')
        composer.grid_columnconfigure(0, weight=1)
        self.composer = composer
        box = ctk.CTkFrame(composer, fg_color=ui_theme.SURFACE_ALT, border_width=1, border_color=ui_theme.LINE,
                           corner_radius=ui_theme.RADIUS)
        box.grid(row=0, column=0, sticky='ew')
        box.grid_columnconfigure(0, weight=1)
        self.link_box = box
        self.urls = ui_theme.textbox(box, placeholder=i18n.t('download.placeholder'), undo=True, height=72,
                                     wrap='none', fg_color=ui_theme.SURFACE_ALT, border_width=0, corner_radius=6)
        self.urls.grid(row=0, column=0, sticky='nsew', padx=(2, 0), pady=2)
        tools = ctk.CTkFrame(box, fg_color='transparent', corner_radius=0)
        tools.grid(row=0, column=1, sticky='ne', padx=(0, ui_theme.INLINE), pady=ui_theme.INLINE)
        self.link_tools = tools
        self.link_count = ui_theme.chip(tools, '0', 'info', width=28)
        self.link_count.grid(row=0, column=0, padx=(0, 4))
        self.link_count.grid_remove()
        self.link_menu = ui_theme.menu_button(tools, self.link_menu_items, size='sm')
        self.link_menu.grid(row=0, column=1)
        self.urls.bind('<KeyRelease>', self._links_typed)
        self.urls.bind('<<Paste>>', self.on_box_paste)
        self.urls.bind('<Control-Return>', self._start_from_box)
        # Tk's Text binds Ctrl+O to "open a line": the box takes it first for 导入 TXT….
        for sequence in ('<Control-o>', '<Control-O>'):
            self.urls.bind(sequence, self._import_from_box)
        for widget in (self.urls, box, tools):
            ui_theme.bind_menu(widget, self.link_menu_items)
        self.link_note = ui_theme.label(composer, '', role='caption', text_color=ui_theme.ACCENT_TEXT, anchor='w',
                                        justify='left')
        self.link_note.grid(row=1, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        ui_theme.wrap_to_width(self.link_note)
        self.link_note.grid_remove()
        self.clip_bar = ui_theme.notice_bar(composer)
        self.clip_bar.grid(row=2, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        self.clip_bar.hide()
        options = ctk.CTkFrame(composer, fg_color='transparent', corner_radius=0)
        options.grid(row=3, column=0, sticky='ew', pady=(ui_theme.GAP, 0))
        options.grid_columnconfigure(4, weight=1)
        # The menus show labels and keep the values: self.mode holds the preset id, self.resolution the
        # stored resolution.
        labels = [i18n.t('format.' + i) for i in formats.MENU_ORDER]
        self.format_menu = ui_theme.option_menu(options, list(formats.MENU_ORDER), labels=labels, variable=self.mode,
                                                command=self.on_format, width=ui_theme.menu_width(options, labels))
        self.format_menu.grid(row=0, column=0, sticky='w')
        labels = [i18n.label('resolution', value) for value in RESOLUTIONS]
        self.quality_menu = ui_theme.option_menu(options, list(RESOLUTIONS), labels=labels, variable=self.resolution,
                                                 width=ui_theme.menu_width(options, labels))
        self.quality_menu.grid(row=0, column=1, sticky='w', padx=(ui_theme.INLINE, 0))
        self.subtitle_check = ui_theme.checkbox(options, i18n.t('download.subtitles'), variable=self.subtitles)
        self.subtitle_check.grid(row=0, column=2, sticky='w', padx=(ui_theme.PAD, 0))
        playlist = ui_theme.checkbox(options, i18n.t('download.playlist'), variable=self.playlist)
        playlist.grid(row=0, column=3, sticky='w', padx=(ui_theme.PAD, 0))
        actions = ctk.CTkFrame(composer, fg_color='transparent', corner_radius=0)
        actions.grid(row=4, column=0, sticky='ew', pady=(ui_theme.GAP, 0))
        actions.grid_columnconfigure(2, weight=1)
        # The folder button is the way to change the folder (the system dialog); with archived downloads it
        # shows the archive folder and leads to Settings, where that folder is chosen.
        self.output_button = ui_theme.path_button(actions, self.browse)
        self.output_button.grid(row=0, column=0, sticky='w')
        text = i18n.t('common.open_folder')
        self.open_output_button = ui_theme.button(actions, text, self.open_folder,
                                                  width=ui_theme.fitted_width(actions, text))
        self.open_output_button.grid(row=0, column=1, sticky='w', padx=(ui_theme.INLINE, 0))
        # The start button fits the wider of its two labels plus its icon: it never changes width
        # when a job starts (I18N.md 10.1).  refresh_start sets its label, state and kind.
        icon = ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white')
        extra = START_ICON + IMAGE_SPACING if icon is not None else 0
        width = max(ui_theme.fitted_width(actions, i18n.t(key)) for key in ('download.start', 'download.start.queue'))
        self.start_button = ui_theme.button(actions, i18n.t('download.start'), self.start, kind='primary',
                                            width=width + extra, image=icon, compound='left', state='disabled')
        self.start_button.grid(row=0, column=3, sticky='e')
        self.controls += [self.format_menu, self.subtitle_check, playlist, self.output_button]
        # ------------------------------------------------ task area
        area = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        area.grid(row=2, column=0, sticky='nsew', pady=(ui_theme.SECTION, 0))
        area.grid_columnconfigure(0, weight=1)
        area.grid_rowconfigure(3, weight=1)
        self.task_area = area
        self.notice_bar = ui_theme.notice_bar(area)
        self.notice_bar.grid(row=0, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        self.notice_bar.hide()
        header = ctk.CTkFrame(area, fg_color='transparent', corner_radius=0)
        header.grid(row=1, column=0, sticky='ew')
        header.grid_columnconfigure(0, weight=1)
        self.task_header = header
        # Line 1: what is going on (the whole width); line 2: how far / why, and what to do next.  Without a
        # metrics line the buttons sit at the right end of line 1 (place_task_actions).
        self.current_title = ui_theme.label(header, '', role='heading', anchor='w')
        self.current_title.grid(row=0, column=0, columnspan=4, sticky='ew')
        ui_theme.fit(self.current_title, '')
        self.metrics = ui_theme.label(header, '', role='caption', text_color=ui_theme.MUTED, anchor='w',
                                      height=ui_theme.LABEL_H['caption'])
        self.metrics.grid(row=1, column=0, sticky='ew')
        ui_theme.fit(self.metrics, '')
        self.actions_inline = None          # the buttons are on the title's line (place_task_actions)
        self.retry_button = ui_theme.button(header, i18n.t('download.retry_unfinished_n', count=0),
                                            lambda: self.retry_failed(), state='disabled')
        self.retry_button.grid(row=1, column=1, padx=(ui_theme.INLINE, 0))
        self.retry_button.grid_remove()
        self.task_menu = ui_theme.menu_button(header, self.task_menu_items)
        self.task_menu.grid(row=1, column=2, padx=(ui_theme.INLINE, 0))
        self.task_menu.grid_remove()
        text = i18n.t('common.stop')
        self.stop_button = ui_theme.button(header, text, self.stop, kind='danger',
                                           width=ui_theme.fitted_width(header, text), state='disabled')
        self.stop_button.grid(row=1, column=3, padx=(ui_theme.PAD, 0))
        self.stop_button.grid_remove()
        self.progress = SmoothProgressBar(area, height=6, surface=ui_theme.SURFACE)
        self.progress.grid(row=2, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        self.progress.set(0, immediate=True)
        self.task_list = ui_theme.scroll_area(area, height=100)
        self.task_list.grid(row=3, column=0, sticky='nsew', pady=(ui_theme.GAP, 0))
        self.task_list.grid_columnconfigure(0, weight=1)
        # Empty: one short line and the next step (its button is in input_controls: grey while another
        # activity holds the window).
        self.empty = ui_theme.empty_state(area, i18n.t('download.empty'), ui_theme.ICON_DOWNLOAD,
                                          action=(i18n.t('download.paste'), self.paste))
        self.empty.grid(row=1, column=0, rowspan=3, sticky='nsew')
        self.input_controls += [self.urls, self.empty.action_button]
        self.row_footer = ui_theme.label(self.task_list, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        # The '⋯' of the selected row (one for the whole list, laid into that row; row_menu_items).
        self.row_menu = ui_theme.menu_button(self.task_list, lambda: self.row_menu_items(self.selected_row), size='sm')
        self.row_menu.configure(fg_color=ui_theme.ROW, bg_color=ui_theme.ROW)
        for widget in (self.task_header, self.progress, self.task_list):
            widget.grid_remove()
        for sequence in ('<MouseWheel>', '<B1-Motion>'):
            self.bind(sequence, self._list_touched, add='+')
        self.place_task_actions()
        self.bind_download_keys()
        self.playlist.trace_add('write', lambda *_: self.links_changed())
        for variable in (self.output, self.archive_root):
            variable.trace_add('write', lambda *_: self.refresh_destination())

    # ================================================================ composer
    def on_format(self, _value=None):
        preset = formats.by_id(self.mode.get())
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

    def links_usable(self):
        """Links can be added: idle, or a download runs (not while a collection or an engine update holds it)."""
        return not (self.busy and self.operation != 'download')

    def _undo_mark(self):
        """One undo step per change the program makes to the box (Ctrl+Z puts back one paste or one 清空)."""
        try:
            self.urls.edit_separator()
        except tk.TclError:
            pass

    def _links_typed(self, _event=None):
        ui_theme.cancel_after(self, self.link_after)
        self.link_after = self.after(150, self._links_debounced)

    def _links_debounced(self):
        self.link_after = None
        self.links_changed()

    def box_links(self):
        """The links the box holds now (as 开始下载 would take them)."""
        return links.normalize(links.extract_urls(self.box_text()), self.playlist.get()).urls

    def links_changed(self):
        """Count the links in the box and say what happens to playlists, mixes and duplicates.  Never raises."""
        try:
            self.urls.sync_placeholder()
            playlist = self.playlist.get()
            result = links.normalize(links.extract_urls(self.box_text()), playlist)
            self.link_total = len(result.urls)
            self.link_count.set(str(self.link_total))
            ui_theme.show(self.link_count, self.link_total > 0)
            self.show_paste_action()
            kinds = result.kinds
            note, tone = '', 'warn'
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
                note, tone = i18n.t('download.note.duplicates', count=result.duplicates), 'info'
            self.note_text = (note, tone)
            self._show_note()
            self.refresh_start()
        except Exception:
            applog.get('ui').exception('links_changed failed')

    def show_paste_action(self):
        """(final polish) 粘贴 under the empty state only while the link box holds no link: with links in it the
        next step is 开始下载, and the empty state keeps only its icon and sentence."""
        button = getattr(getattr(self, 'empty', None), 'action_button', None)
        if button is None:          # the box is built before the task area
            return
        wanted = not self.link_total
        if wanted and not button.winfo_manager():
            button.pack(pady=(ui_theme.GAP, 0))
        elif not wanted and button.winfo_manager():
            button.pack_forget()

    def _show_note(self):
        """The line under the box: a one-off feedback while it lasts, else the note of the links (not while
        another activity holds the dimmed box), else nothing."""
        text, tone = self.hint_note or (('', 'warn') if self.foreign_activity else self.note_text)
        color = ui_theme.WARN if tone == 'warn' else ui_theme.ACCENT_TEXT
        if text and (self.link_note.cget('text') != text or self.link_note.cget('text_color') != color):
            self.link_note.configure(text=text, text_color=color)
        ui_theme.show(self.link_note, bool(text))

    def set_hint(self, text, tone='info', timeout=8000):
        """A one-off feedback under the box (``tone`` 'info' or 'warn'), gone after ``timeout`` ms (None:
        until the next one); '' clears it.  A reason is wrapped, never cut."""
        ui_theme.cancel_after(self, self.hint_after)
        self.hint_after = None
        self.hint_note = (text, tone) if text else None
        if text and timeout:
            self.hint_after = self.after(timeout, lambda: self.set_hint(''))
        self._show_note()

    def box_keys(self):
        playlist = self.playlist.get()
        return {links.link_key(links.clean_url(url, playlist), playlist) for url in links.extract_urls(self.box_text())}

    def add_links(self, text, source='paste'):
        """Append the links found in ``text`` that are not in the box yet; returns how many were added."""
        if not self.links_usable():
            self.set_hint(i18n.t('download.hint.busy'), 'warn')
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
            self._undo_mark()
            self.urls.insert('end', prefix + '\n'.join(added))
            self._undo_mark()
            self.urls.see('end')
        tone = 'info'
        if not found:
            hint = i18n.t('download.hint.no_links_file') if source == 'import' else i18n.t('download.hint.no_links_clip')
            tone = 'warn'
        elif skipped:
            hint = i18n.t('download.hint.added_skipped', added=len(added), skipped=skipped)
        else:
            hint = i18n.t('download.hint.added', added=len(added))
        if source == 'import' and found:
            empty = sum(1 for line in (text or '').splitlines()
                        if line.strip() and not line.lstrip().startswith(links.COMMENT) and not links.extract_urls(line))
            if empty:
                hint += i18n.t('download.hint.empty_lines', empty=empty)
        self.set_hint(hint, tone)
        self.links_changed()
        return len(added)

    def paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.set_hint(i18n.t('download.hint.no_links_clip'), 'warn')
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
        """清空 (red, last in the box's menu): no confirmation, Ctrl+Z in the box brings the links back."""
        self._undo_mark()
        self.urls.delete('1.0', 'end')
        self._undo_mark()
        self.set_hint('')
        self.links_changed()

    def select_all_links(self):
        self.urls.tag_add('sel', '1.0', 'end-1c')

    def import_txt(self):
        if not self.links_usable():
            self.set_hint(i18n.t('download.hint.busy'), 'warn')
            return
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

    def _import_from_box(self, _event=None):
        self.import_txt()
        return 'break'

    def link_menu_items(self):
        """The link box's '⋯' and its right click: 粘贴, 导入 TXT…, 全选, 清空 (enabled as of now)."""
        usable, text = self.links_usable(), bool(self.box_text())
        return [ui_theme.MenuItem(i18n.t('download.paste'), self.paste, enabled=usable),
                ui_theme.MenuItem(i18n.t('download.import'), self.import_txt, enabled=usable),
                ui_theme.SEPARATOR,
                ui_theme.MenuItem(i18n.t('common.select_all'), self.select_all_links, enabled=text),
                ui_theme.MenuItem(i18n.t('download.clear_box'), self.clear_links, enabled=text and usable,
                                  danger=True)]

    def browse(self):
        if self.archive_enabled.get():
            self.show_settings('downloads')     # the archive folder is chosen in Settings only
            return
        initial = self.output.get() if Path(self.output.get()).is_dir() else str(APP_DIR)
        path = filedialog.askdirectory(parent=self, initialdir=initial)
        if path:
            self.output.set(path)

    def refresh_destination(self):
        """The folder button shows where the files go: the archive folder (its icon) or the download folder."""
        if self.archive_enabled.get():
            self.output_button.set_path(self.archive_root.get(), ui_theme.ICON_ARCHIVE)
        else:
            self.output_button.set_path(self.output.get(), ui_theme.ICON_FOLDER)

    # ================================================================ clipboard notice (ux-3, safe form)
    def check_clipboard(self):
        """Offer links found in the clipboard; never fills the box, never starts anything, never logs the text."""
        self.clip_after = None
        try:
            if (not self.clipboard_hint.get() or self.smoke_mode or self.current_page != 'download'
                    or not self.links_usable()):
                return self.hide_clipboard()
            try:
                text = self.clipboard_get()
            except tk.TclError:
                return self.hide_clipboard()
            if not isinstance(text, str) or not text.strip():
                return self.hide_clipboard()
            digest = hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()
            if digest == self.clip_seen:
                return self.hide_clipboard()
            playlist = self.playlist.get()
            known = self.box_keys() | {row.get('key') for row in self.row_model}
            fresh = [url for url in links.extract_urls(text)
                     if links.link_key(links.clean_url(url, playlist), playlist) not in known]
            if not fresh:
                return self.hide_clipboard()
            self.clip_text, self.clip_digest = text, digest
            self.clip_bar.show(i18n.t('download.clip.found', count=len(fresh), title=link_title(fresh[0])),
                               [(i18n.t('download.clip.add'), self.accept_clipboard),
                                (i18n.t('common.ignore'), self.ignore_clipboard)])
        except Exception:
            applog.get('ui').exception('clipboard check failed')

    def schedule_clipboard_check(self, _event=None):
        ui_theme.cancel_after(self, self.clip_after)
        self.clip_after = self.after(300, self.check_clipboard)

    def hide_clipboard(self):
        self.clip_bar.hide()
        self.clip_text = ''

    def _remember_clip(self):
        if self.clip_digest:
            self.clip_seen = self.clip_digest

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
        for sequence in ('<Control-o>', '<Control-O>'):
            self.bind(sequence, lambda event: self._key(self.import_txt), add='+')
        # The selected task row's menu (the link box and the tables take these keys first, for their own menu).
        for sequence in ('<Shift-F10>', '<App>'):
            try:
                self.bind(sequence, lambda event: self._key(self.open_row_menu, outside_text=True), add='+')
            except tk.TclError:
                pass        # a Tk without the menu key's keysym
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
        title = i18n.t('app.title')
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
        """While an engine update or a web collection runs, the download page says so: the task title names
        it and the link box is dimmed.  The download header is put back when the activity ends.  Called
        from refresh_shell (so at the end of every set_busy)."""
        foreign = self.operation if self.busy and self.operation in FOREIGN_KEYS else None
        if foreign == self.foreign_activity:
            return
        if foreign is not None and self.foreign_activity is None:
            self.foreign_saved = (ui_theme.full_text(self.current_title), ui_theme.full_text(self.metrics),
                                  self.metrics.cget('text_color'), self.progress.target)
        self.foreign_activity = foreign
        if foreign is not None:
            self.current_title.configure(text=i18n.t(FOREIGN_KEYS[foreign]))
            self.metrics.configure(text='', text_color=ui_theme.MUTED)
            self.set_hint('')
            self.hide_clipboard()
        else:
            saved, self.foreign_saved = self.foreign_saved, None
            if saved is not None:
                self.current_title.configure(text=saved[0])
                self.metrics.configure(text=saved[1], text_color=saved[2])
                self.progress.set(saved[3], immediate=True)
        self.urls.set_dimmed(foreign is not None)
        self.link_box.configure(fg_color=ui_theme.SURFACE if foreign is not None else ui_theme.SURFACE_ALT)
        self._show_note()
        self.refresh_task_view()

    # ================================================================ notices
    def show_notice(self, kind, text, actions=(), timeout=None):
        """The notice above the task area (one at a time: a new one takes the place of the last)."""
        self.notice_kind = kind
        self.notice_bar.show(text, list(actions)[:2], tone='warn' if kind in WARN_NOTICES else 'idle')
        ui_theme.cancel_after(self, self.notice_after)
        self.notice_after = None
        if timeout:
            self.notice_after = self.after(timeout, lambda: self.hide_notice(kind))

    def hide_notice(self, kind=None):
        if kind is not None and self.notice_kind != kind:
            return
        self.notice_bar.hide()
        self.notice_kind = None
        if kind != 'bot' and self.bot_retry is not None and self.bot_retry.get('deadline') is not None:
            self.render_bot_notice()
        if kind != 'library' and self.notice_kind is None and getattr(self, 'library_waiting', False):
            self.show_library_notice()      # (1.7.0 U1) the library is still locked

    # Hints other pages put on this page; their texts are the download page's (catalog i18n_download).
    def hint_history_loaded(self):
        """The links of a history record were put into the box (library page, 继续 / 载入)."""
        self.set_hint(i18n.t('download.hint.history_loaded'))

    def hint_subscription_check(self, name):
        """A subscription check holds the window; ``name`` is the subscription's shown name.  It stays until
        the check ends (collection_ui clears it with set_hint(''))."""
        self.set_hint(i18n.t('download.hint.subscription_check', name=name), 'warn', timeout=None)

    def undo_launch(self):
        self.hide_notice('undo')
        if not self.last_submitted:
            return
        current = self.box_text()
        self._undo_mark()
        self.urls.delete('1.0', 'end')
        self.urls.insert('1.0', self.last_submitted + ('\n' + current if current.strip() else ''))
        self._undo_mark()
        self.links_changed()

    # ================================================================ rows (arch-14 reduced)
    def populate_queue(self, urls, options=None, overrides=None):
        """Replace the task list by one waiting row per link.  Built rows are kept for reuse, not destroyed."""
        self.row_generation += 1
        ui_theme.cancel_after(self, self.row_after)
        self.row_after = None
        self.row_queue = []
        for row in self.rows:
            row['frame'].grid_remove()
            row['index'] = None
        self.rows = []
        self.row_model = []
        self.selected_row = None
        self.place_row_menu()
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
                                   'detail': '', 'chip': None, 'tone': None,
                                   'icon': None, 'path': '', 'error': '', 'entry': None})
        self._append_tail(tail)
        self._rows_changed(start)
        self.refresh_task_view()

    def _unfinished_model(self, entry):
        """The row model of a stored unfinished entry (state 'unfinished', below the job's rows)."""
        # (1.7.0, I18N.md 9.3) The reason word and the format in the running language; the entry
        # keeps its stored values (format key, mode: a preset id).
        reason = i18n.t(REASON_KEYS.get(entry.get('reason'), 'download.reason.failed'))
        try:
            label = i18n.t('format.' + formats.by_key(entry.get('format', '')).id)
        except ValueError:
            mode = str((entry.get('options') or {}).get('mode', ''))
            # an unknown id shows itself
            label = i18n.t('format.' + mode) if mode in formats.MODES else messages.show(mode)
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
            preset = formats.by_id(options.mode)
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
        self.place_row_menu()
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
            badge.grid(row=0, column=3, rowspan=2, padx=(ui_theme.INLINE, 12))     # column 2: the row '⋯'
            row = {'frame': frame, 'icon': icon, 'title': title, 'detail': detail, 'badge': badge, 'state': None,
                   'index': index, 'url': '', 'key': '', 'path': '', 'error': '', 'look': None, 'selected': False}
            for widget in (frame, icon, title, detail, badge):
                widget.bind('<Button-1>', lambda event, r=row: self.select_row(r['index']), add='+')
                widget.bind('<Double-Button-1>', lambda event, r=row: self.open_row(r['index']), add='+')
                widget.bind('<Button-3>', lambda event, r=row: self.row_context(event, r['index']), add='+')
            self.row_pool.append(row)
        row['index'] = index
        row['frame'].grid(row=index, column=0, sticky='ew', padx=0, pady=2)
        self._paint_row(row, self.row_model[index])
        return row

    def _paint_row(self, row, model):
        state = model['state']
        icon_code, tone = STATE_LOOK.get(state, STATE_LOOK['waiting'])
        chip_text = i18n.label('row_state', state if state in STATE_LOOK else 'waiting')
        tone = model.get('tone') or tone
        icon_code = model.get('icon') or icon_code
        chip_text = model.get('chip') or chip_text
        detail = model.get('detail') or ''
        number = f"{model['index'] + 1:02d}"
        detail_text = detail if state == 'unfinished' else f'{number} · {detail}' if detail else number
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
        """"还有 N 条" below the rows when the list holds more than the rows that get widgets (they download as usual)."""
        hidden = len(self.row_model) - len(self.rows)
        if len(self.row_model) > self.row_cap() and not self.row_queue and hidden > 0:
            self.row_footer.configure(text=i18n.t('download.footer.hidden', hidden=hidden))
            self.row_footer.grid(row=len(self.rows), column=0, sticky='ew', padx=12, pady=ui_theme.INLINE)
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
        ui_theme.cancel_after(self, self.follow_after)
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
        """What the task area shows: the task header while there are rows or the window is busy (any
        activity), else the empty state; the progress bar once a download ran; the rows when there are
        some.  Rows that are only the unfinished leftovers of earlier jobs are titled 未完成.  Cheap: it
        only grids or hides on a change (it runs on every row change and every set_busy)."""
        rows = bool(self.row_model)
        header = rows or self.busy
        ui_theme.show(self.empty, not header)
        ui_theme.show(self.task_header, header)
        # another activity's title is not over the last download's bar (its value is kept for the end)
        ui_theme.show(self.progress, header and (self.busy or self.job_ran) and not self.foreign_activity)
        # a ScrollArea grids its outer frame (CTkScrollableFrame); the inner frame always reports a manager
        ui_theme.show(self.task_list, rows)
        if rows and not self.busy and all(model['state'] == 'unfinished' for model in self.row_model):
            title = i18n.t('common.unfinished')
            if ui_theme.full_text(self.current_title) != title:
                self.current_title.configure(text=title)
                self.metrics.configure(text='', text_color=ui_theme.MUTED)
        self.place_task_actions()

    def place_task_actions(self):
        """With a metrics line the task buttons sit at its right end; without one (only unfinished, another
        activity, a done job) the line goes and the buttons sit at the right end of the title's line.  Only
        grids on a change."""
        inline = not ui_theme.full_text(self.metrics)
        if inline == self.actions_inline:
            return
        self.actions_inline = inline
        row = 0 if inline else 1
        self.current_title.grid_configure(columnspan=1 if inline else 4)
        # (final polish) the metrics line keeps the height of a button line even without buttons (the YouTube
        # check ending), its sentence centred in it as beside the buttons; grid minsize is in device pixels
        line = round(ui_theme.CONTROL_H * (ui_theme._scaling(self.task_header) or 1.0))
        self.task_header.grid_rowconfigure(1, minsize=0 if inline else line)
        ui_theme.show(self.metrics, not inline)
        for widget in (self.retry_button, self.task_menu, self.stop_button):
            if widget.winfo_manager():
                widget.grid_configure(row=row)
            else:               # a hidden widget keeps its place: grid it there and hide it again
                widget.grid(row=row)
                widget.grid_remove()

    def select_row(self, index):
        previous, self.selected_row = self.selected_row, index
        for number in {previous, index}:
            if number is not None and 0 <= number < len(self.rows):
                self._paint_row(self.rows[number], self.row_model[number])
        self.place_row_menu()

    def place_row_menu(self):
        """The row '⋯' sits in the selected row (when it has widgets) and nowhere else."""
        index = self.selected_row
        menu = self.row_menu
        if index is None or not 0 <= index < len(self.rows):
            if menu.winfo_manager():
                menu.grid_remove()
            self.row_menu_at = None
            return
        frame = self.rows[index]['frame']
        if self.row_menu_at is not frame or not menu.winfo_manager():
            menu.grid(in_=frame, row=0, column=2, rowspan=2, padx=(ui_theme.INLINE, 0))
            menu.lift(frame)        # a pooled row frame made later would cover it
            self.row_menu_at = frame

    def open_row_menu(self):
        """Shift+F10 / the menu key: the selected row's menu, at its bottom-left corner."""
        index = self.selected_row
        if index is None or not 0 <= index < len(self.rows):
            return
        frame = self.rows[index]['frame']
        ui_theme.popup(frame, lambda: self.row_menu_items(index), frame.winfo_rootx() + 8,
                       frame.winfo_rooty() + frame.winfo_height())

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

    def row_menu_items(self, index):
        """The menu of one task row (its '⋯', its right click, Shift+F10): 播放, 在文件夹中显示, 复制链接, 查看原因,
        then 重试 (仍然下载 for a row skipped as downloaded before) and 移除 (red)."""
        if index is None or not 0 <= index < len(self.row_model):
            return []
        model = self.row_model[index]
        state, path = model['state'], model.get('path')
        other_busy = not self.links_usable()
        item = ui_theme.MenuItem
        items = [item(i18n.t('download.menu.play'), lambda: self.open_row(index),
                      enabled=state in ('done', 'skipped') and bool(path)),
                 item(i18n.t('download.menu.show'), lambda: procutil.reveal_in_folder(path), enabled=bool(path)),
                 item(i18n.t('download.menu.copy_link'), lambda: self.copy_text(model['url'])),
                 item(i18n.t('download.menu.reason'), lambda: self.show_row_reason(index),
                      enabled=state in REASON_STATES),
                 ui_theme.SEPARATOR]
        if state == 'skipped':
            items.append(item(i18n.t('download.menu.anyway'), lambda: self.retry_row(index), enabled=not other_busy))
        else:
            items.append(item(i18n.t('download.menu.retry'), lambda: self.retry_row(index),
                              enabled=state in RETRYABLE and not other_busy))
        items.append(item(i18n.t('download.menu.remove'), lambda: self.remove_row(index), enabled=not self.busy,
                          danger=True))
        return items

    def row_context(self, event, index):
        """A right click on a task row: select it, then its menu at the mouse."""
        if index is None or not 0 <= index < len(self.row_model):
            return
        self.select_row(index)
        ui_theme.popup(self, lambda: self.row_menu_items(index), event.x_root, event.y_root)

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
                self.set_hint(i18n.t('download.hint.already_queued'), 'warn')
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
        preset = formats.by_id(self.mode.get())
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
        """开始下载 / 加入队列 (button, Enter, Ctrl+Enter).  Nothing happens without a link in the box: the button
        is grey then (refresh_start); the count is taken now, a typing pause may not have counted yet."""
        if not self.box_links():
            return
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
            library = self.library
            # (1.7.0 U1) A library locked by another program: the download starts at once and its
            # record (a PendingJob until then) is written as soon as the lock is gone.  (2.0.0) No
            # title yet: the history names the job after its links until the first media title.
            job_id = self.history_create(lambda: library.create_job('download', '', data))
        except Exception as error:
            applog.get('ui').exception('create_job failed')
            self.append_log(i18n.t('log.history_write_failed', error=messages.error_text(error)))
        self.download_job = job_id
        try:
            self.job_ran = True
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
            self.place_task_actions()
            self.set_window_title()
            label = i18n.t('format.' + formats.by_id(options.mode).id)
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
            self.metrics.configure(text='')
            self.refresh_task_view()
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
            self.set_hint(messages.error_text(error), 'warn')       # the whole reason, wrapped
            return 0
        runner = self.runner
        if runner is None or not hasattr(runner, 'enqueue'):
            self.set_hint(i18n.t('download.hint.busy'), 'warn')
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
            self.set_hint(i18n.t('download.hint.queue_ended'), 'warn')
        else:
            self.set_hint(i18n.t('download.hint.already_queued'), 'warn')
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
            return
        index = self.active_index + 1
        total = max(self.job_row_count(), index)
        if not self.foreign_activity:
            self.current_title.configure(text=self._active_title(index, total))
        self.set_window_title(f'{index}/{total}')
        self.refresh_shell()

    # ================================================================ unfinished list (ux-7)
    def refresh_start(self):
        """The start button: 加入队列 during a download, else 开始下载; usable with a link in the box while links can
        be added; and the one solid button of the page moves with the stage (00-system 2.6): an empty box, idle,
        with unfinished links -> 重试未完成 (N) is solid and the start button soft.  Called from refresh_shell (so
        at the end of every set_busy), links_changed and refresh_failed_button (when the unfinished count changes)."""
        # (I18N.md 8.7) compared with the texts of the running language, not with Chinese literals
        text = i18n.t('download.start.queue') if self.busy and self.operation == 'download' else i18n.t('download.start')
        state = 'normal' if self.link_total and self.links_usable() else 'disabled'
        changes = {}
        if self.start_button.cget('text').strip() != text:
            changes['text'] = text
        if str(self.start_button.cget('state')) != state:
            changes['state'] = state
        if changes:
            self.start_button.configure(**changes)
        retry_first = bool(self.unfinished) and not self.link_total and not self.busy
        self.start_button.set_kind('soft' if retry_first else 'primary')
        self.retry_button.set_kind('primary' if retry_first else 'soft')

    def refresh_failed_button(self):
        """The task header's buttons: 停止 only while busy (any activity); 重试未完成 (N) and the task '⋯' only while
        there are unfinished links, 重试 usable when idle.  Then the task area's parts (refresh_task_view)."""
        # (2.0.0 S4 A2, moved here from set_busy) every other caller of this method runs while idle (remove_row,
        # clear_unfinished, start-up), where the stop button is hidden.
        ui_theme.show(self.stop_button, self.busy)
        state = 'normal' if self.busy else 'disabled'
        if str(self.stop_button.cget('state')) != state:
            self.stop_button.configure(state=state)
        count = len(self.unfinished)
        if count:
            text = i18n.t('download.retry_unfinished_n', count=count)
            if self.retry_button.cget('text') != text:
                # the width follows the text, or a redraw could cut the label (00-system 2.3)
                self.retry_button.configure(text=text, width=ui_theme.fitted_width(self.retry_button.master, text))
        state = 'normal' if count and not self.busy else 'disabled'
        if str(self.retry_button.cget('state')) != state:
            self.retry_button.configure(state=state)
        ui_theme.show(self.retry_button, bool(count))
        ui_theme.show(self.task_menu, bool(count))
        self.refresh_task_view()
        if count != self.retry_count:
            self.retry_count = count
            self.refresh_start()

    def task_menu_items(self):
        """The task header's '⋯': 清除未完成 (red, with its confirmation), kept behind '⋯' so it is not clicked by
        mistake.  (final polish) No 重试未完成 (N) here: the button beside it is shown exactly when this menu is,
        never turns into another action, and the header has no right-click menu, so the copy only repeated it."""
        usable = bool(self.unfinished) and not self.busy
        return [ui_theme.MenuItem(i18n.t('download.clear_unfinished'), self.clear_unfinished, enabled=usable,
                                  danger=True)]

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
        """清除未完成 (task '⋯', after a confirmation).  The title stays: it never counts the unfinished links."""
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
            # (查看原因) and 重试 in their menu.
            for index, model in enumerate(self.row_model):
                if model['state'] in RETRYABLE and not model.get('tail') and not model.get('cleared'):
                    # A failure keeps its reason; 已保留在未完成 and the YouTube wait no longer hold.
                    reason = str(model.get('detail') or '') if model['state'] == 'failed' else ''
                    cleared = i18n.t('download.detail.cleared')
                    self.update_row(index, cleared=True, chip=i18n.t('download.chip.cleared'), tone='idle',
                                    icon=ui_theme.ICON_PENDING, detail=f'{cleared} · {reason}' if reason else cleared)
            self.show_unfinished_tail()
            # "the links are kept" no longer holds
            if ui_theme.full_text(self.metrics) in (i18n.t('download.metrics.finished_unfinished'),
                                                    i18n.t('download.metrics.bot'), i18n.t('download.metrics.bot_cookies')):
                self.metrics.configure(text='', text_color=ui_theme.MUTED)
        self.refresh_failed_button()
        self.cancel_bot_retry()

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
        ui_theme.cancel_after(self, self.bot_after)
        self.bot_after = None
        self.hide_notice('bot')
        self.refresh_shell()

    def _schedule_bot_tick(self):
        ui_theme.cancel_after(self, self.bot_after)
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
        # the time first: a narrow window cuts the end of the reason, never the countdown
        self.live_metrics(i18n.t('download.metrics.wait', reason=reason, time=time_text(seconds)), ui_theme.WARN)
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
    def live_metrics(self, text, color=ui_theme.MUTED):
        """The metrics line while an item runs (WARN while it waits)."""
        if self.metrics.cget('text_color') != color:
            self.metrics.configure(text=text, text_color=color)
        else:
            self.metrics.configure(text=text)
        self.place_task_actions()

    def on_log(self, value):
        self.append_log(value)
        if self.operation == 'download' and str(value).startswith(CONVERTING):
            self.clear_wait()
            self.update_row(self.active_index, chip=i18n.t('download.chip.converting'), tone='info', icon=None)
            self.live_metrics(i18n.t('download.chip.processing'))
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
            if self.active_index >= len(self.rows) and not self.foreign_activity:
                total = max(self.job_row_count(), self.active_index + 1)
                self.current_title.configure(text=self._active_title(self.active_index + 1, total))

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
        self.live_metrics(i18n.t('download.chip.processing'))
        self.update_row(self.active_index, chip=i18n.t('download.chip.processing'), tone='info', icon=None)
        self.animate(True)
        self.refresh_shell()

    def on_progress(self, value):
        self.clear_wait()
        fraction = value.get('fraction')
        self.animate(fraction is None)
        if fraction is not None:
            self.progress.set(fraction)
        # whole percent, rounded down: 100% only when it is all there
        percent = f'{math.floor(100 * fraction)}%' if fraction is not None else i18n.t('download.chip.downloading')
        speed = bytes_text(value.get('speed')) + '/s' if value.get('speed') is not None else '—'
        text = i18n.t('download.metrics.progress', percent=percent, done=bytes_text(value.get('downloaded')),
                      total=bytes_text(value.get('total')), speed=speed, eta=time_text(value.get('eta')))
        if not self.foreign_activity:       # another activity (collect / update) keeps its header
            self.live_metrics(text)
        if self.operation == 'download':
            self.update_row(self.active_index,
                            chip=percent if value.get('status') == 'downloading' else i18n.t('download.chip.processing'),
                            tone='info', icon=None, detail=speed)
            now = time.monotonic()
            if self.active_index is not None and now - self.title_stamp >= 1:
                self.title_stamp = now
                self.set_window_title(f'{self.active_index + 1}/{self.job_row_count()} · {percent}')

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
            self.update_row(index, state='skipped', detail=earlier, **fields)
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
        color = ui_theme.MUTED
        # (polish, ui-daily-flow-1) The leftovers of earlier jobs come back below this job's rows.
        self.show_unfinished_tail()
        # The title: the first that holds of stopped, engine error, YouTube check, unfinished, all downloaded
        # before, done.  No summary line: the success count is in the title, the unfinished count on
        # 重试未完成 (N), the rest on the row chips.
        if cancelled:
            title, metrics = i18n.t('download.title.stopped'), i18n.t('download.metrics.stopped')
        elif engine_error:
            # The way out where it exists: 恢复内置引擎 is in Settings only while an updated engine is in use.
            title, color = i18n.t('download.title.engine_error'), ui_theme.WARN
            metrics = (i18n.t('download.metrics.engine_error') if getattr(self, 'engine_updated', False)
                       else i18n.t('download.detail.see_log'))
        elif value.get('bot_check'):
            title = i18n.t('download.title.bot')
            metrics = i18n.t('download.metrics.bot_cookies') if self.cookies.get().strip() else i18n.t('download.metrics.bot')
            color = ui_theme.WARN
        elif unfinished:
            title = i18n.t('download.title.finished_n', success=success)
            metrics = i18n.t('download.metrics.finished_unfinished')
        elif skipped and not success:
            # (polish, ui-daily-flow-4; WP4 has no text for a job that only skipped links)
            title, metrics = i18n.t('download.title.all_skipped', skipped=skipped), ''
        else:
            title, metrics = i18n.t('download.title.done', success=success), ''
        if value.get('archive_errors'):
            metrics, color = i18n.t('download.metrics.archive_errors'), ui_theme.WARN
        if not cancelled and not value['failed'] and not unfinished:
            self.progress.set(1)
        elif not cancelled and unfinished:
            # (polish, ui-daily-flow-7) Not the 100 % of the last item: the share that succeeded.
            self.progress.set(success / (success + unfinished))
        self.current_title.configure(text=title)
        self.metrics.configure(text=metrics, text_color=color)
        self.place_task_actions()
        self.append_log(title)
        if value.get('failed_path') and (value.get('unfinished') or unfinished):
            # (polish, ui-wording-consistency-8; WP4.md:77 says 失败清单) the list is 未完成 everywhere else.
            self.append_log(i18n.t('log.unfinished_list', path=value['failed_path']))
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
            # a notice: the task header may be hidden at start-up; 知道了 puts it away
            self.show_notice('bot_recent', i18n.t('download.notice.bot_recent', minutes=max(1, int(since // 60))),
                             [(i18n.t('common.got_it'), lambda: self.hide_notice('bot_recent'))])
        self.after(300, self.check_clipboard)
