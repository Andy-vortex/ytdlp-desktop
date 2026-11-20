"""StudioApp: the window shell of YT-DLP下载器 (1.6.0 WP4).

The shell owns the window, the sidebar, the shared state, the event loop (poll, handle,
set_busy, watchdog) and closing; the pages are mixins (DownloadUI, SettingsUI, LogUI,
LibraryUI, CollectionUI).
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
from ui_download import DownloadUI, time_text
from ui_settings import SettingsUI
from ui_log import LogUI
from library_ui import LibraryUI
from engine import MODES, RESOLUTIONS
from paths import RESOURCE_ROOT
from updater import engine_version

DEFAULT_SIZE = (1120, 810)
MIN_SIZE = (1000, 740)
# Events handled through self.handlers (kind -> bound method on_<kind>); collect_* go to handle_collection.
HANDLED_KINDS = ('log', 'item', 'title', 'waiting', 'resuming', 'progress', 'media', 'file', 'queued', 'item_done',
                 'done', 'updated', 'update_error', 'update_done', 'update_status', 'update_progress', 'engine_check',
                 'library_busy')     # (1.7.0 U1) a worker's history write waits for a locked library
# Tk variables written to settings.json 400 ms after a change (SPEC 3.2).
PERSISTED = ('output', 'mode', 'resolution', 'batch_safe', 'archive_enabled', 'archive_root', 'cookies', 'naming',
             'embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded', 'notify_done', 'clipboard_hint',
             'check_engine_updates', 'subscription_media_only', 'subscription_first_new', 'subscription_interval')
_GEOMETRY = re.compile(r'^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$')


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
        super().__init__(fg_color=ui_theme.SURFACE)      # (2.0.0 S4) one sheet: the pages sit on SURFACE
        applog.setup()
        self.report_callback_exception = self.report_error
        self.logger = applog.get()
        # 2. window basics
        self.title(i18n.t('app.title'))
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
        self.mode = tk.StringVar(value=mode)        # a preset id; the format menu shows i18n.t('format.' + id)
        self.resolution = tk.StringVar(value=resolution)
        self.proxy = tk.StringVar(value='')
        self.cookies = tk.StringVar(value=prefs['cookies'] if isinstance(prefs['cookies'], str) else '')
        self.playlist = tk.BooleanVar(value=False)
        self.batch_safe = tk.BooleanVar(value=prefs['batch_safe'] is not False)
        self.naming = tk.StringVar(value=prefs['naming'] if prefs['naming'] in naming.NAMING else 'title')
        for name in ('embed_metadata', 'embed_cover', 'subtitles', 'skip_downloaded', 'notify_done', 'clipboard_hint',
                     'check_engine_updates', 'subscription_media_only', 'subscription_first_new'):
            value = prefs[name]
            setattr(self, name, tk.BooleanVar(value=value if isinstance(value, bool) else defaults[name]))
        # (2.0.0 S4) The schedule of a new subscription (settings.SCHEDULES, seconds) and the archive switch and
        # folder: every persisted variable is made here.  The default archive folder follows the output folder.
        interval = prefs['subscription_interval']
        self.subscription_interval = tk.IntVar(
            value=interval if interval in settings.SCHEDULES else defaults['subscription_interval'])
        self.archive_enabled = tk.BooleanVar(value=self.prefs.get('archive_enabled', False) is True)
        self.archive_root = tk.StringVar(
            value=self.prefs.get('archive_root', settings.defaults(self.output.get())['archive_root']))
        # 6. history, collection state, unfinished list
        self.init_library(self.prefs)
        self.init_collection()
        self.unfinished = [] if self.smoke_mode else engine.load_unfinished(self.append_log)
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
        # (2.0.0 S4) the words the shared pieces of ui_theme show themselves (ask_text, path_button, empty_state)
        ui_theme.WORDS.update(cancel=i18n.t('common.cancel'), save=i18n.t('common.save'),
                              choose=i18n.t('common.choose_ellipsis'))
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
            self.mode.set(formats.PRESETS[1].id)
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
        # Codes (compared, never stored), shown through i18n.label by the collect page.
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

    def report_error(self, kind, value, trace):
        """Tk callback errors: the traceback goes to the log file, one line to the log page."""
        try:
            applog.get('ui').error('界面错误', exc_info=(kind, value, trace))
            self.append_log(i18n.t('log.ui_error', kind=kind.__name__, value=messages.error_text(value)))
        except Exception:
            pass

    def build_sidebar(self):
        """(2.0.0 S4, 00-system 4.1-4.4) The mark and 'YT-DLP', five one-word pages, and at the bottom the
        status (a dot and one word) with the 'Update available' link above it while a newer engine is offered."""
        side = self.sidebar = ctk.CTkFrame(self, width=ui_theme.SIDEBAR_W, fg_color=ui_theme.SIDE, corner_radius=0)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(6, weight=1)
        brand = ctk.CTkFrame(side, fg_color='transparent', corner_radius=0)
        brand.grid(row=0, column=0, sticky='ew', padx=16, pady=(20, 20))
        mark = ctk.CTkLabel(brand, text='', image=ui_theme.icon(ui_theme.ICON_DOWNLOAD, 'white', 16), width=32,
                            height=32, corner_radius=ui_theme.RADIUS, fg_color=ui_theme.ACCENT)
        mark.grid(row=0, column=0, padx=(0, 10))
        ui_theme.label(brand, 'YT-DLP', role='brand', text_color=ui_theme.WHITE, anchor='w').grid(row=0, column=1, sticky='w')
        # One icon column and one text column for the whole sidebar (00-system 4.4): a nav button sits `edge` from
        # the side, draws its 16 px icon `inset` further in (a `cell` 18 wide with Tk's label pad, at 200 %), then
        # CTk's `gap`; the update arrow and the status dot take the same cell, so every text starts at one x.
        edge, inset, cell, gap = 12, 10, 18, 6
        self.nav_icons = {}
        for row, key, code, title in [(1, 'download', ui_theme.ICON_DOWNLOAD, i18n.t('nav.download')),
                                      (2, 'collect', ui_theme.ICON_SEARCH, i18n.t('nav.collect')),
                                      (3, 'library', ui_theme.ICON_LIBRARY, i18n.t('nav.library')),
                                      (4, 'logs', ui_theme.ICON_LOG, i18n.t('nav.logs')),
                                      (5, 'settings', ui_theme.ICON_SETTINGS, i18n.t('nav.settings'))]:
            self.nav_icons[key] = (ui_theme.icon(code, 'side'), ui_theme.icon(code, 'white'))
            button = ctk.CTkButton(side, text=title, image=self.nav_icons[key][0], compound='left',
                                   anchor='w', border_spacing=inset, height=ui_theme.CONTROL_H, corner_radius=ui_theme.RADIUS,
                                   font=self.font, fg_color='transparent', hover_color=ui_theme.SIDE_HOVER,
                                   text_color=ui_theme.SIDE_TEXT, command=lambda selected=key: self.show_page(selected))
            button.grid(row=row, column=0, sticky='ew', padx=edge, pady=2)
            self.nav_buttons[key] = button
        # Shown only while a newer yt-dlp is offered (show_engine_notice); it opens the engine group of Settings.
        # It is 28 high, so its border spacing is 2 and its corner radius sets the inner margin instead of `inset`;
        # the outer margin makes up the difference.
        self.engine_notice = ctk.CTkButton(side, text=i18n.t('shell.engine_update'), anchor='w', height=ui_theme.CONTROL_H_SM,
                                           image=ui_theme.icon(ui_theme.ICON_UPDATE, 'side_warn', 16), compound='left',
                                           corner_radius=ui_theme.RADIUS, border_spacing=2, font=self.small,
                                           fg_color='transparent', hover_color=ui_theme.SIDE_HOVER,
                                           text_color=ui_theme.SIDE_WARN, command=lambda: self.show_settings('engine'))
        self.engine_notice.grid(row=7, column=0, sticky='ew', padx=(edge + inset - ui_theme.RADIUS, edge), pady=(0, 4))
        self.engine_notice.grid_remove()
        status = ctk.CTkFrame(side, fg_color='transparent', corner_radius=0)
        status.grid(row=8, column=0, sticky='ew', padx=(edge + inset, edge), pady=(0, 16))
        self.side_dot = ui_theme.label(status, '●', role='caption', text_color=ui_theme.SIDE_OK, width=cell)
        self.side_dot.grid(row=0, column=0, sticky='w', padx=(0, gap))
        self.side_status = ui_theme.label(status, i18n.t('shell.status.ready'), role='caption',
                                          text_color=ui_theme.SIDE_OK, anchor='w')
        self.side_status.grid(row=0, column=1, sticky='w')

    def show_engine_notice(self, shown):
        """The sidebar's 'Update available' link: shown while a newer yt-dlp than the running one is offered."""
        ui_theme.show(self.engine_notice, bool(shown))

    def page(self, name, title, max_width=None):
        """A page with its header: the title, then ``header.left`` (page-level controls packed from the left,
        SECTION after the title) and ``header.right`` (packed from the right edge).  The content starts in row 1."""
        page = ctk.CTkFrame(self.content, fg_color='transparent', corner_radius=0)
        page.grid(row=0, column=0, sticky='nsew')
        page.grid_columnconfigure(0, weight=1)
        header = page.header = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        header.grid(row=0, column=0, sticky='ew', pady=(0, ui_theme.PAD))
        header.grid_rowconfigure(0, minsize=40)
        header.grid_columnconfigure(2, weight=1)
        header.title = ui_theme.label(header, title, role='title', anchor='w')
        header.title.grid(row=0, column=0, sticky='w')
        header.left = ctk.CTkFrame(header, fg_color='transparent', corner_radius=0, width=0, height=0)
        header.left.grid(row=0, column=1, sticky='w', padx=(ui_theme.SECTION, 0))
        header.right = ctk.CTkFrame(header, fg_color='transparent', corner_radius=0, width=0, height=0)
        header.right.grid(row=0, column=3, sticky='e')
        self.pages[name] = page
        if max_width is None and name == 'download':
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
        if name == 'collect' and hasattr(self, 'collect_urls'):
            self.collect_urls.set_dimmed(bool(self.busy))   # (1.7.0 U6) the look waits for its page
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
            # The tab ids are codes (group library_tab), in tab order.
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
        """The sidebar's status (a dot and one word, 00-system 4.4), then the hooks of the pages that follow
        the shell state: refresh_foreign_activity, refresh_start and refresh_settings_controls."""
        try:
            if self.busy and self.operation == 'download':
                if self.wait_context is not None and self.wait_seconds is not None:
                    text, tone = i18n.t('shell.status.waiting', time=time_text(self.wait_seconds)), ui_theme.SIDE_WARN
                else:
                    text, tone = i18n.t('shell.status.downloading'), ui_theme.SIDE_INFO
            elif self.busy and self.operation == 'collect':
                text, tone = i18n.t('shell.status.collecting'), ui_theme.SIDE_INFO
            elif self.busy and self.operation == 'update':
                text, tone = i18n.t('shell.status.updating'), ui_theme.SIDE_INFO
            elif getattr(self, 'bot_retry', None) is not None:
                text, tone = i18n.t('shell.status.bot_wait'), ui_theme.SIDE_WARN
            else:
                text, tone = i18n.t('shell.status.ready'), ui_theme.SIDE_OK
            if self.side_status.cget('text') != text or self.side_status.cget('text_color') != tone:
                self.side_status.configure(text=text, text_color=tone)
                self.side_dot.configure(text_color=tone)
        except (AttributeError, tk.TclError):
            pass     # called while the window is still being built or already gone
        for refresh in ('refresh_foreign_activity', 'refresh_start', 'refresh_settings_controls'):
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
        # (2.0.0 S4, 00-system 4.7) The final shape: every control that also depends on a selection, a content or
        # a mode is set by the hook of its view, which reads self.busy itself.
        self.refresh_failed_button()           # download, tasks: stop, retry unfinished, clear unfinished
        self.on_format()                       # download, input: resolution, subtitles
        self.refresh_collect_actions()         # collect: stop, save page text
        self.collection_selection_changed()    # collect: add to downloads, export
        self.history_selection()               # library: archive_selection and subscription_tools first
        self.refresh_shell()                   # shell: refresh_foreign_activity, refresh_start, refresh_settings_controls

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
