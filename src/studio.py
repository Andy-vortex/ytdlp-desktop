"""StudioApp: the window shell of YT-DLP下载器 (1.6.0 WP4).

The shell owns the window, the sidebar, the shared state, the event loop (poll, handle,
set_busy, watchdog) and closing; the pages are mixins (DownloadUI, SettingsUI, LogUI,
LibraryUI, CollectionUI).  FORMATS, bytes_text, time_text and the 1.5.1 colour names are
re-exported from ui_download for older callers (library_ui: from studio import FORMATS).
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import applog
import collection_ui
import engine
import formats
import i18n
import messages
import naming
import paths
import settings
import ui_theme
from collection_ui import CollectionUI
from ui_download import (DownloadUI, FORMATS, bytes_text, time_text, BG, WHITE, INK, MUTED,  # noqa: F401 (re-exports)
                         BLUE, BLUE_LIGHT, LINE, SIDE, GREEN, ORANGE)
from ui_settings import SettingsUI
from ui_log import LogUI
from library_ui import LibraryUI
from engine import APP_VERSION, MODES, RESOLUTIONS, RESOURCE_ROOT, engine_version

TITLE = 'YT-DLP 下载器'
DEFAULT_SIZE = (1120, 810)
MIN_SIZE = (1000, 740)
# Events handled through self.handlers (kind -> bound method on_<kind>); collect_* go to handle_collection.
HANDLED_KINDS = ('log', 'item', 'title', 'waiting', 'resuming', 'progress', 'media', 'file', 'queued', 'item_done',
                 'done', 'updated', 'update_error', 'update_done', 'update_status', 'update_progress', 'engine_check',
                 'library_busy')     # (1.7.0 U1) a worker's history write waits for a locked library
# Tk variables written to settings.json 400 ms after a change (SPEC 3.2).
PERSISTED = ('output', 'mode', 'resolution', 'batch_safe', 'archive_enabled', 'archive_root', 'cookies', 'naming',
             'embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded', 'notify_done', 'clipboard_hint',
             'check_engine_updates')
_GEOMETRY = re.compile(r'^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$')
# (1.7.0 U4) settings.json could not be read at start (held or locked): said once on the download page.
SETTINGS_HELD_NOTICE = '设置文件暂时无法读取，本次先用默认设置；原文件不会被覆盖。'


def virtual_screen():
    """(x, y, width, height) of the virtual desktop in physical pixels, or None when unknown."""
    try:
        metric = ctypes.windll.user32.GetSystemMetrics
        x, y, width, height = metric(76), metric(77), metric(78), metric(79)
        return (x, y, width, height) if width > 0 and height > 0 else None
    except (AttributeError, OSError):
        return None


def valid_geometry(text, virtual):
    """``text`` ('WxH+X+Y') when a window can be restored there, else None.  Pure.

    W and H are logical pixels and must reach the minimum size; X and Y are physical pixels
    and the top-left corner must lie on the virtual screen ``virtual`` = (x, y, w, h), at least
    100 px inside its right and bottom edges (a window snapped to the left edge has an X a few
    pixels below the screen's left edge, so up to 50 px are allowed there).
    """
    match = _GEOMETRY.match(str(text or '').strip())
    if not match:
        return None
    width, height, x, y = (int(part) for part in match.groups())
    if width < MIN_SIZE[0] or height < MIN_SIZE[1]:
        return None
    if virtual is not None:
        left, top, span_x, span_y = virtual
        if not (left - 50 <= x <= left + span_x - 100 and top <= y <= top + span_y - 100):
            return None
    return f'{width}x{height}+{x}+{y}'


class _WindowPlacement(ctypes.Structure):
    _fields_ = [('length', ctypes.c_uint), ('flags', ctypes.c_uint), ('showCmd', ctypes.c_uint),
                ('ptMinPosition', ctypes.c_long * 2), ('ptMaxPosition', ctypes.c_long * 2),
                ('rcNormalPosition', ctypes.c_long * 4)]


def _restores_maximised(window):
    """For a minimised window: will it come back maximised?  (Windows keeps that in the
    window placement, flag WPF_RESTORETOMAXIMIZED.)  None when it cannot be asked."""
    try:
        frame = int(window.wm_frame(), 16)
        placement = _WindowPlacement()
        placement.length = ctypes.sizeof(placement)
        if not ctypes.windll.user32.GetWindowPlacement(ctypes.c_void_p(frame), ctypes.byref(placement)):
            return None
        return bool(placement.flags & 0x0002)
    except (AttributeError, OSError, ValueError, TypeError, tk.TclError):
        return None


def _saved_zoomed(window, stored):
    """The 'zoomed' value save_settings writes.  A minimised (or hidden) window keeps the state
    it had before, so closing it from the taskbar does not lose the maximised state (core fix
    review-state-7; a deviation from the literal WP4.md expression ``state() == 'zoomed'``):
    when minimised, Windows is asked whether it comes back maximised; without an answer,
    and for a hidden window, the stored value stays."""
    state = window.state()
    if state not in ('iconic', 'withdrawn'):
        return state == 'zoomed'
    if state == 'iconic':
        restores = _restores_maximised(window)
        if restores is not None:
            return restores
    return bool(stored)


class StudioApp(DownloadUI, SettingsUI, LogUI, LibraryUI, CollectionUI, ctk.CTk):
    def __init__(self, arguments):
        # 1. theme: DPI awareness and the patched theme before the first Tk root
        ui_theme.enable_dpi_awareness()
        ctk.set_appearance_mode(getattr(arguments, 'appearance', None) or 'light')
        ui_theme.apply_theme()
        super().__init__(fg_color=ui_theme.BG)
        applog.setup()
        self.report_callback_exception = self.report_error
        self.logger = applog.get()
        # 2. window basics
        self.title(i18n.t('app.title'))       # TITLE in zh (I18N.md 8.5)
        self.geometry(f'{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}')
        self.minsize(*MIN_SIZE)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        # 3. state containers (every attribute a page builder or handler may touch)
        self.events = queue.Queue()
        self.runner = None
        self.worker = None
        self.worker_operation = None
        self.run_token = 0
        self.job_options = None
        self.row_queue = []
        self.row_generation = 0
        self.row_after = None
        self.view_restore = None        # (row generation, px) the task list returns to once its rows are built
        self.busy = self.closing = self.animating = False
        self.smoke_mode = bool(arguments.smoke_url)
        self.operation = None
        self.active_index = None
        self.wait_context = None
        self.wait_deadline = 0
        self.wait_seconds = None
        self.rows = []
        self.row_model = []
        self.selected_row = None
        self.controls = []
        self.input_controls = []
        self.update_buttons = []
        self.pages = {}
        self.page_caps = {}
        self.nav_buttons = {}
        self.nav_icons = {}
        self.current_page = 'download'
        self.handlers = {}
        self.log_buffer = []
        self.last_submitted = ''
        self.last_saved_path = ''
        self.download_job = None
        self.bot_retry = None
        self.title_stamp = 0.0
        self.save_after = None
        self.watch_strikes = 0
        # 4. preferences: smoke mode uses the defaults and never saves
        self.prefs = {} if self.smoke_mode else settings.load()
        defaults = settings.defaults()
        prefs = {**defaults, **self.prefs}
        mode = prefs['mode'] if prefs['mode'] in MODES else MODES[0]
        resolution = prefs['resolution'] if prefs['resolution'] in RESOLUTIONS else RESOLUTIONS[0]
        # 5. Tk variables
        self.output = tk.StringVar(value=arguments.smoke_output or prefs['output'] or str(paths.default_output()))
        self.mode = tk.StringVar(value=mode)
        self.format = tk.StringVar(value=formats.by_mode(mode).label)
        self.resolution = tk.StringVar(value=resolution)
        self.proxy = tk.StringVar(value='')
        self.cookies = tk.StringVar(value=prefs['cookies'] if isinstance(prefs['cookies'], str) else '')
        self.playlist = tk.BooleanVar(value=False)
        self.batch_safe = tk.BooleanVar(value=prefs['batch_safe'] is not False)
        self.naming = tk.StringVar(value=prefs['naming'] if prefs['naming'] in naming.NAMING else 'title')
        for name in ('embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded', 'notify_done', 'clipboard_hint',
                     'check_engine_updates'):
            value = prefs[name]
            setattr(self, name, tk.BooleanVar(value=value if isinstance(value, bool) else defaults[name]))
        # 6. history, collection state, unfinished list
        self.init_library(self.prefs)
        self.init_collection()
        self.unfinished = [] if self.smoke_mode else engine.load_unfinished()
        for kind in HANDLED_KINDS:
            handler = getattr(self, f'on_{kind}', None)
            if handler is not None:
                self.handlers[kind] = handler
        # 7. geometry: the remembered normal size first, then the <Configure> tracker
        self.normal_geometry = ''
        if not self.smoke_mode:
            stored = valid_geometry(self.prefs.get('geometry', ''), virtual_screen())
            if stored:
                self.geometry(stored)
                self.normal_geometry = stored
        self.bind('<Configure>', self.on_configure, add='+')
        # 8. sidebar and pages
        self.font = ui_theme.font('body')
        self.small = ui_theme.font('caption')
        self.bold = ui_theme.font('heading')
        self.build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        self.content.grid(row=0, column=1, sticky='nsew', padx=ui_theme.PAGE_PAD, pady=16)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.build_download_page()
        self.build_log_page()
        self.build_settings_page()
        self.build_collection_page()
        self.build_library_page()
        # The not yet rewritten builders configure Collect.Treeview with a 10 pt font; this re-applies the scaled style.
        ui_theme.apply_ttk_styles(self)
        ui_theme.set_page_cap(self, self.content, {self.pages[name]: self.page_caps.get(name) for name in self.pages})
        # 9. traces
        self.archive_enabled.trace_add('write', lambda *_: self.refresh_destination())
        if not self.smoke_mode:
            for name in PERSISTED:
                getattr(self, name).trace_add('write', self.schedule_save)
        # 10. first refreshes
        self.refresh_destination()
        self.refresh_failed_button()
        self.show_page('download')
        self.on_format()
        if arguments.smoke_url:
            self.urls.insert('1.0', arguments.smoke_url)
            self.format.set(FORMATS[1])
            self.on_format()
        self.links_changed()
        self.append_log(i18n.t('log.ready', version=messages.show(engine_version())))
        if settings.HELD in self.prefs:
            # (final-merge, core request 1) settings.load() could not read the file: say why the
            # defaults are shown; save() never writes them over the stored file.
            self.append_log(i18n.t('log.settings_held'))
        try:
            self.iconbitmap(str(RESOURCE_ROOT / 'app.ico'))
        except tk.TclError:
            pass
        # 11. timers and start-up hooks
        self.after(25, self.poll)
        self.after(1000, self.watchdog)
        self.download_startup()
        self.settings_startup()
        if settings.HELD in self.prefs:
            # (1.7.0 U4) Once, on the download page as well: the owner sees the defaults and is told
            # why, without a dialog.  The notice stays until 知道了 or another notice takes its place.
            self.show_notice('settings', i18n.t('shell.notice.settings_held'),
                             [(i18n.t('common.got_it'), lambda: self.hide_notice('settings'))])
        if not self.smoke_mode and self.prefs.get('zoomed'):
            self.after(0, lambda: self.state('zoomed'))

    def init_collection(self):
        """The collection page's shared state (moved out of build_collection_page; that builder sets it again)."""
        self.collected_links = {}
        self.collected_pages = []
        self.collection_errors = 0
        self.download_referers = {}
        # The stored (compared) values of 1.6.0, shown through i18n.label by the collect page (I18N.md 7).
        self.collect_mode = tk.StringVar(value=collection_ui.MODE_STATIC)
        self.collect_scrolls = tk.StringVar(value='0')
        self.collect_pages_limit = tk.StringVar(value='1')
        self.collect_filter = tk.StringVar(value=collection_ui.FILTERS[0])
        self.collect_search = tk.StringVar()
        self.collect_selector = tk.StringVar()

    def on_configure(self, event):
        """Remember the last size and position of the window in its normal (not maximised) state."""
        if event.widget is self:
            try:
                if self.state() == 'normal':
                    self.normal_geometry = self.geometry()
            except tk.TclError:
                pass

    def schedule_save(self, *_):
        if self.smoke_mode:
            return
        if self.save_after is not None:
            try:
                self.after_cancel(self.save_after)
            except (tk.TclError, ValueError):
                pass
        self.save_after = self.after(400, self.save_settings)

    # ------------------------------------------------------------ shell factories (compatibility surface)
    def label(self, parent, text='', **kwargs):
        kwargs.setdefault('font', self.font)
        return ui_theme.label(parent, text, **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        return ui_theme.button(parent, text, command, kind='primary' if primary else kwargs.pop('kind', 'soft'), **kwargs)

    def card(self, parent, **kwargs):
        return ui_theme.card(parent, **kwargs)

    def report_error(self, kind, value, trace):
        """Tk callback errors: the traceback goes to the log file, one line to the log page."""
        try:
            applog.get('ui').error('界面错误', exc_info=(kind, value, trace))
            self.append_log(i18n.t('log.ui_error', kind=kind.__name__, value=messages.error_text(value)))
        except Exception:
            pass

    def build_sidebar(self):
        side = ctk.CTkFrame(self, width=ui_theme.SIDEBAR_W, fg_color=ui_theme.SIDE, corner_radius=0)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(6, weight=1)
        brand = ctk.CTkFrame(side, fg_color='transparent', corner_radius=0)
        brand.grid(row=0, column=0, sticky='ew', padx=16, pady=(24, 24))
        mark = ctk.CTkLabel(brand, text='', image=ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white', 18), width=36,
                            height=36, corner_radius=10, fg_color=ui_theme.ACCENT)
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        ui_theme.label(brand, 'YT-DLP', role='brand', text_color=ui_theme.WHITE, anchor='w').grid(row=0, column=1, sticky='w')
        caption, wrapped = self._brand_caption(brand)
        ui_theme.label(brand, caption, role='caption', text_color=ui_theme.SIDE_MUTED, anchor='w',
                       **({'justify': 'left'} if wrapped else {})).grid(row=1, column=1, sticky='w')
        self.nav_icons = {}
        for row, key, code, title in [(1, 'download', ui_theme.ICON_DOWNLOAD, i18n.t('nav.download')),
                                      (2, 'collect', ui_theme.ICON_SEARCH, i18n.t('nav.collect')),
                                      (3, 'library', ui_theme.ICON_LIBRARY, i18n.t('nav.library')),
                                      (4, 'logs', ui_theme.ICON_LOG, i18n.t('nav.logs')),
                                      (5, 'settings', ui_theme.ICON_SETTINGS, i18n.t('nav.settings'))]:
            self.nav_icons[key] = (ui_theme.icon(code, 'side'), ui_theme.icon(code, 'white'))
            button = ctk.CTkButton(side, text=title, image=self.nav_icons[key][0], compound='left', anchor='w',
                                   border_spacing=10, height=40, corner_radius=8, font=self.font,
                                   fg_color='transparent', hover_color=ui_theme.SIDE_HOVER,
                                   text_color=ui_theme.SIDE_TEXT,
                                   command=lambda selected=key: self.show_page(selected))
            button.grid(row=row, column=0, sticky='ew', padx=12, pady=2)
            self.nav_buttons[key] = button
        status = ctk.CTkFrame(side, fg_color=ui_theme.SIDE_CARD, corner_radius=10)
        status.grid(row=7, column=0, sticky='ew', padx=12, pady=(12, 16))
        status.grid_columnconfigure(1, weight=1)
        self.side_dot = ui_theme.label(status, '●', role='caption', text_color=ui_theme.SIDE_OK, width=10)
        self.side_dot.grid(row=0, column=0, sticky='w', padx=(12, 6), pady=(10, 0))
        self.side_status = ui_theme.label(status, '', role='caption', text_color=ui_theme.SIDE_OK, anchor='w')
        self.side_status.grid(row=0, column=1, sticky='ew', padx=(0, 12), pady=(10, 0))
        # (1.7.0, I18N.md 10.4) A safety net: a status longer than its 120 px is cut with '…'.
        ui_theme.fit(self.side_status, i18n.t('shell.status.ready'))
        # The engine version, or the shown 已内置 (updater.BUILTIN) when the built-in version is not known.
        self.engine_badge = ui_theme.label(status, f'yt-dlp {messages.show(engine_version())}', role='caption',
                                           text_color=ui_theme.SIDE_MUTED, anchor='w')
        self.engine_badge.grid(row=1, column=0, columnspan=2, sticky='ew', padx=12)
        self.app_badge = ui_theme.label(status, '', role='caption', text_color=ui_theme.SIDE_MUTED, anchor='w')
        self.app_badge.grid(row=2, column=0, columnspan=2, sticky='ew', padx=12, pady=(0, 10))
        self.show_app_badge(False)

    @staticmethod
    def _brand_caption(brand):
        """(1.7.0, I18N.md 10.4) The caption under the brand, on two lines when one line is wider than
        its column (en 'Desktop Downloader'; the zh text fits and stays one line): (text, wrapped)."""
        text = i18n.t('shell.brand_caption')
        try:
            scale = ui_theme._scaling(brand) or 1.0
            measure = ui_theme._measure_font(brand, ui_theme.font('caption')).measure
            room = (ui_theme.SIDEBAR_W - 2 * 16 - 36 - 10) * scale      # sidebar, brand padding, mark and its gap
            if measure(text) <= room or ' ' not in text.strip():
                return text, False
            words = text.split(' ')
            splits = [(' '.join(words[:cut]), ' '.join(words[cut:])) for cut in range(1, len(words))]
            first, rest = min(splits, key=lambda pair: max(measure(pair[0]), measure(pair[1])))
            return f'{first}\n{rest}', True
        except (tk.TclError, AttributeError, ValueError):
            return text, False

    def show_app_badge(self, engine_new):
        """(1.7.0) The app badge of the sidebar: the version, and ' · 引擎有新版' (en ' · new engine')
        while a newer yt-dlp is offered.  The texts are the shell's (catalog i18n_shell)."""
        if engine_new:
            self.app_badge.configure(text=i18n.t('shell.badge.engine_new', version=APP_VERSION),
                                     text_color=ui_theme.SIDE_WARN)
        else:
            self.app_badge.configure(text=f'v{APP_VERSION}', text_color=ui_theme.SIDE_MUTED)

    def page(self, name, title, subtitle, max_width=None):
        page = ctk.CTkFrame(self.content, fg_color='transparent', corner_radius=0)
        page.grid(row=0, column=0, sticky='nsew')
        page.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        header.grid(row=0, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        header.grid_columnconfigure(1, weight=1)
        ui_theme.label(header, title, role='title', anchor='w').grid(row=0, column=0, sticky='w')
        subtitle_label = ui_theme.label(header, subtitle, role='caption', text_color=ui_theme.MUTED, anchor='w')
        subtitle_label.grid(row=0, column=1, sticky='ew', padx=(12, 0), pady=(8, 0))
        ui_theme.fit(subtitle_label, subtitle)
        self.pages[name] = page
        if max_width is None and name in ('download', 'settings'):
            max_width = ui_theme.PAGE_MAX
        self.page_caps[name] = max_width
        return page

    def show_page(self, name):
        for key, button in self.nav_buttons.items():
            active = key == name
            images = self.nav_icons.get(key, (None, None))
            options = {'fg_color': ui_theme.SIDE_ACTIVE if active else 'transparent',
                       'text_color': ui_theme.WHITE if active else ui_theme.SIDE_TEXT}
            if images[active] is not None:
                options['image'] = images[active]
            button.configure(**options)
        page = self.pages[name]
        # (final-fix-ui, polish-visual-1) The pages stay gridded on top of each other and the
        # current one is raised, as in 1.5.1 and INT: un-gridding a page (the polish round's
        # grid_forget) unmapped every window in it, and mapping the 50 task rows again cost
        # 250 to 300 ms per switch.  What the polish round wanted from it stays
        # (ui-wording-consistency-1): the covered pages take no Tab focus (ui_theme.hide_from_focus)
        # and focus_page moves the keyboard focus into the shown page, so Ctrl+V, Enter and
        # typing never reach a hidden field.
        if not page.winfo_manager():
            page.grid(row=0, column=0, sticky='nsew')
        page.tkraise()
        ui_theme.hide_from_focus(self, 'pages', [other for other in self.pages.values() if other is not page])
        self.current_page = name
        if name == 'collect' and hasattr(self, '_dim_collect_box'):
            self._dim_collect_box(bool(self.busy))      # (1.7.0 U6) the look waits for its page
        self.focus_page(name)

    def focus_page(self, name):
        """(polish, ui-wording-consistency-1) Move the keyboard focus to page ``name``'s natural field
        unless it already is inside that page, so that Ctrl+V, Enter and typing reach the page
        that is shown and never a hidden one."""
        page = self.pages.get(name)
        if page is None:
            return
        try:
            focused = str(self.tk.call('focus', '-lastfor', self._w))
        except tk.TclError:
            focused = ''
        if focused.startswith(str(page) + '.'):
            return
        target = page
        if name == 'download':
            target = getattr(self, 'urls', page)
        elif name == 'collect':
            target = getattr(self, 'collect_urls', page)
        elif name == 'logs':
            target = getattr(self, 'logs', page)
        elif name == 'library':
            # The tab ids are the 1.6.0 Chinese names (group library_tab, I18N.md 7), in tab order.
            tab_ids = [value for value, _label in i18n.choices('library_tab')]
            tables = dict(zip(tab_ids, ('history_tree', 'subscription_tree', 'archive_tree')))
            try:
                target = getattr(self, tables.get(self.library_tabs.get(), ''), page)
            except AttributeError:
                target = page
        try:
            target.focus_set()
        except tk.TclError:
            pass

    def refresh_shell(self):
        """Side status line and the start button's label, derived from the current state."""
        try:
            text, tone = i18n.t('shell.status.ready'), ui_theme.SIDE_OK
            if self.busy and self.operation == 'download':
                if self.wait_context is not None and self.wait_seconds is not None:
                    text, tone = i18n.t('shell.status.waiting', time=time_text(self.wait_seconds)), ui_theme.SIDE_WARN
                elif self.active_index is not None:
                    total = max(self.job_row_count(), self.active_index + 1)
                    text, tone = (i18n.t('shell.status.downloading_n', index=self.active_index + 1, total=total),
                                  ui_theme.SIDE_INFO)
                else:
                    text, tone = i18n.t('shell.status.downloading'), ui_theme.SIDE_INFO
            elif self.busy and self.operation == 'collect':
                text, tone = i18n.t('shell.status.collecting'), ui_theme.SIDE_INFO
            elif self.busy and self.operation == 'update':
                text, tone = i18n.t('shell.status.updating'), ui_theme.SIDE_INFO
            elif getattr(self, 'bot_retry', None) is not None:
                text, tone = i18n.t('shell.status.bot_wait'), ui_theme.SIDE_DANGER
            # The status is fitted (cut with '…' when it is too wide): compare the full text.
            if ui_theme.full_text(self.side_status) != text:
                self.side_status.configure(text=text, text_color=tone)
                self.side_dot.configure(text_color=tone)
            # (I18N.md 8.7) compared with the texts of the running language, not with Chinese literals
            queued = self.busy and self.operation == 'download'
            start = i18n.t('download.start.queue') if queued else i18n.t('download.start')
            if self.start_button.cget('text').strip() != start:
                self.start_button.configure(text=start)
        except (AttributeError, tk.TclError):
            pass     # called while the window is still being built or already gone
        for refresh in ('refresh_foreign_activity', 'refresh_engine_controls'):
            try:
                getattr(self, refresh)()
            except (AttributeError, tk.TclError):
                pass     # the page is not built yet, or the window is gone

    def open_folder(self):
        try:
            path = Path(self.archive_root.get() if self.archive_enabled.get() else self.output.get()).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as error:
            messagebox.showerror(i18n.t('dialog.open_folder_failed'), messages.error_text(error), parent=self)

    def save_settings(self):
        """Write settings.json from the variables and the kept values; never in smoke mode."""
        if self.smoke_mode:
            return
        if self.save_after is not None:
            try:
                self.after_cancel(self.save_after)
            except (tk.TclError, ValueError):
                pass
            self.save_after = None
        values = dict(self.prefs)
        try:
            for name in PERSISTED:
                values[name] = getattr(self, name).get()
            values['output'] = values['output'].strip()
            values['cookies'] = values['cookies'].strip()
            values['geometry'] = self.normal_geometry or self.prefs.get('geometry', '')
            values['zoomed'] = _saved_zoomed(self, self.prefs.get('zoomed'))
        except tk.TclError:
            pass        # the window is going away; what was read so far is saved
        self.prefs.update({key: value for key, value in values.items() if key in ('geometry', 'zoomed')})
        if not settings.save(values):
            self.append_log(i18n.t('log.settings_not_saved'))

    def set_busy(self, busy):
        self.busy = busy
        if not busy:
            self.worker = None
        for control in self.controls + self.update_buttons:
            control.configure(state='disabled' if busy else 'normal')
        for control in self.input_controls:
            control.configure(state='disabled' if busy and self.operation != 'download' else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.refresh_failed_button()
        self.on_format()
        if hasattr(self, 'collect_stop'):
            self.collect_stop.configure(state='normal' if busy and self.operation == 'collect' else 'disabled')
            self.collection_selection_changed()
        if hasattr(self, 'history_tree'):
            self.history_selection()
        self.refresh_shell()

    def stop(self):
        if self.busy and self.runner:
            if self.operation == 'update':
                # The UI is released at once; the old worker can queue nothing more after the token bump.
                self.runner.cancel()
                self.run_token += 1
                self.drain_events()
                if self.busy:
                    self.handle('update_done', {'cancelled': True})
                return
            self.clear_wait()
            self.current_title.configure(text=i18n.t('download.title.stopping'))
            self.stop_button.configure(state='disabled')
            self.collect_stop.configure(state='disabled')
            threading.Thread(target=self.runner.cancel, daemon=True).start()

    def handle(self, kind, value):
        if kind.startswith('collect_'):
            self.handle_collection(kind, value)
            return
        handler = self.handlers.get(kind)
        if handler is not None:
            handler(value)

    def drain_events(self):
        """Handle every queued event now (poll is not involved); a failing handler is logged."""
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self.handle(kind, value)
            except Exception:
                self.logger.exception('UI event processing failed')

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
        try:
            self.flush_log()
            self.render_wait()
        except Exception:
            self.logger.exception('UI tick failed')
        finally:
            if self.closing and not self.busy:
                self.finish_close()
            else:
                self.after(25, self.poll)

    def watchdog(self):
        """Every second: a registered worker of the running activity that died without its terminal
        event (two quiet ticks in a row) gets one synthetic terminal event."""
        try:
            worker = self.worker
            if (self.busy and worker is not None and self.worker_operation == self.operation
                    and not worker.is_alive() and self.events.empty()):
                self.watch_strikes += 1
                if self.watch_strikes >= 2:
                    self.watch_strikes = 0
                    terminal = {'download': ('done', {'success': 0, 'failed': 0, 'files': [], 'cancelled': True,
                                                      'failed_urls': [], 'pending_urls': []}),
                                'update': ('update_done', {'cancelled': True}),
                                'collect': ('collect_done', {'cancelled': True})}.get(self.operation)
                    self.append_log(i18n.t('log.watchdog'))
                    self.run_token += 1
                    self.worker = None
                    if self.operation == 'download':
                        # The synthetic done goes through handle(), not the emit closure: the history
                        # job would stay 'running' without this.
                        self.close_history_job(self.download_job)
                    if terminal is not None:
                        self.handle(*terminal)
            else:
                self.watch_strikes = 0
        except Exception:
            self.logger.exception('watchdog failed')
        finally:
            try:
                self.after(1000, self.watchdog)
            except tk.TclError:
                pass

    def on_close(self):
        if self.closing:
            self.force_close()      # second click on X while stopping
            return
        if self.busy:
            if messagebox.askyesno(i18n.t('dialog.close_busy.title'), i18n.t('dialog.close_busy.text'), parent=self):
                self.closing = True
                self.stop()
                self.after(8000, self.force_close)
        else:
            self.finish_close()

    def force_close(self):
        """Close now, whatever the worker does: it is cancelled on a thread and can no longer emit."""
        runner = self.runner
        if runner is not None:
            threading.Thread(target=runner.cancel, daemon=True).start()
        self.run_token += 1
        self.worker = None
        self.finish_close()

    def finish_close(self):
        if getattr(self, '_finished', False):
            return
        self._finished = True
        self.closing = True
        try:
            self.save_settings()
        except Exception:
            self.logger.exception('settings were not saved on close')
        for handler in self.logger.handlers:
            try:
                handler.flush()
            except Exception:
                pass
        self.destroy()

    def destroy(self):
        # (1.7.0 U1) History writes that still wait for a locked library are given up (logged).
        try:
            self.library_closing()
        except Exception:
            pass
        # Pending after() scripts would fire on a destroyed interpreter (or print
        # "invalid command name" under the next root of a test process).
        try:
            for job in self.tk.splitlist(self.tk.call('after', 'info')):
                # Plain Tcl cancel: Misc.after_cancel would also delete the callback's Tcl command,
                # which the widget that scheduled it deletes again when it is destroyed.
                self.tk.call('after', 'cancel', job)
        except tk.TclError:
            pass
        super().destroy()
