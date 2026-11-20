"""Settings page and engine-updater UI of StudioApp (mixin SettingsUI; 1.6.0 WP4).

(2.0.0 S4, settings.md) One two-column grid in a ScrollArea, at most SETTINGS_MAX wide (the ScrollArea itself fills
the content area, so its scrollbar sits on the right edge like every other page's): a muted group
label above each group (engine, downloads, subscriptions, network, interface, about), a row label on
the left and its control on the right.  The engine row carries the version, 恢复内置引擎 (only while
an updated engine is in use) and 检查更新, which an update in progress swaps for 停止.  Every shown
text through i18n; the language row and the restart that applies it as in 1.7.0 (I18N.md 6.2, 6.3).

Never import studio here at module level: studio imports this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from functools import partial
from tkinter import filedialog, messagebox

import customtkinter as ctk
import engine
import i18n
import messages
import naming
import paths
import procutil
import settings
import ui_theme
import updater
from engine import APP_VERSION, check_cookie_file
from updater import engine_version

DAY = 86400
# (WP7) The settings.json 'appearance' values in the order of the 外观 choice (labels: i18n group 'appearance').
APPEARANCE = ('light', 'dark', 'system')
# The groups of the page, top to bottom (show_settings takes one of these ids).
GROUPS = ('engine', 'downloads', 'subscriptions', 'network', 'interface', 'about')
# (1.7.0, I18N.md 6.3) The unsaved input a restart keeps: next to settings.json, read once.
RESTART_STATE = 'restart-state.json'
RESTART_STATE_VERSION = 1
RESTART_STATE_MAX_AGE = 120     # s: an older file is deleted and ignored
RESTART_STATE_KEYS = ('urls', 'collect_urls')


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
    builtin = i18n.t('settings.component.builtin')
    found = {'ffmpeg': builtin, 'deno': builtin}
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


def restart_state_file() -> Path:
    """restart-state.json next to settings.json (I18N.md 6.3)."""
    return paths.settings_file().with_name(RESTART_STATE)


def shown_engine_version(version=None):
    """The engine version as shown: engine_version()'s '已内置' (updater.BUILTIN, a message) in the
    running language (MESSAGES.md 6.2)."""
    return messages.show(engine_version() if version is None else version)


def _reveal(status):
    """The engine status line takes no room while it is empty (a CTkLabel without text still takes its height)."""
    ui_theme.show(status, bool(status.cget('text')))


class SettingsUI:
    # ================================================================ page
    def build_settings_page(self):
        page = self.page('settings', i18n.t('nav.settings'))
        # (S4 visual review) The page is not capped: the ScrollArea fills the content area and its scrollbar sits on
        # the right edge as on every other page.  Only the grid inside is capped at SETTINGS_MAX (set_page_cap below).
        self.page_caps['settings'] = None
        page.grid_rowconfigure(1, weight=1)
        area = self.settings_area = ui_theme.scroll_area(page)
        # INLINE more than the header's own gap: a row scrolled half out of view stays clear of the title
        area.grid(row=1, column=0, sticky='nsew', pady=(ui_theme.INLINE, 0))
        area.grid_columnconfigure(0, weight=1)
        # The two drop-downs of the page share one width, so their left edges are one line too.
        self.settings_menu_w = max(ui_theme.menu_width(area, [i18n.label('naming', key) for key in naming.NAMING]),
                                   ui_theme.menu_width(area, [i18n.label('interval', seconds)
                                                              for seconds in settings.SCHEDULES]))
        self.engine_updated = False
        # the group labels, gridded by the loop below in this order
        self.settings_groups = {
            'engine': ui_theme.section_label(area, i18n.t('settings.group.engine')),
            'downloads': ui_theme.section_label(area, i18n.t('settings.group.downloads')),
            'subscriptions': ui_theme.section_label(area, i18n.t('settings.group.subscriptions')),
            'network': ui_theme.section_label(area, i18n.t('settings.group.network')),
            'interface': ui_theme.section_label(area, i18n.t('settings.group.interface')),
            'about': ui_theme.section_label(area, i18n.t('settings.group.about'))}

        def title(text_label):      # a row label: as high as the control, so label and control centre on one line
            text_label.configure(height=ui_theme.CONTROL_H)
            return text_label
        self.cookies_note = ui_theme.label(area, i18n.t('settings.cookies.text'), role='caption',
                                           text_color=ui_theme.MUTED, anchor='w')
        # (group, row label or None for a row across both columns, control factory, a line under the label or None)
        rows = (
            ('engine', None, self.engine_row, None),
            ('engine', title(ui_theme.label(area, i18n.t('settings.engine.daily.title'), anchor='w')),
             partial(self.switch_control, 'daily_switch', self.check_engine_updates, locked=False), None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.naming.title'), anchor='w')),
             self.naming_control, None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.archive.title'), anchor='w')),
             partial(self.switch_control, 'archive_switch', self.archive_enabled, command=self.save_settings), None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.archive.folder'), anchor='w')),
             self.archive_folder_control, None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.tags.title'), anchor='w')),
             partial(self.switch_control, 'tags_switch', self.embed_metadata), None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.cover.title'), anchor='w')),
             partial(self.switch_control, 'cover_switch', self.embed_cover), None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.skip.title'), anchor='w')),
             partial(self.switch_control, 'skip_switch', self.skip_downloaded), None),
            ('downloads', title(ui_theme.label(area, i18n.t('settings.steady.title'), anchor='w')),
             partial(self.switch_control, 'batch_switch', self.batch_safe), None),
            ('subscriptions', title(ui_theme.label(area, i18n.t('settings.subscription.media_only'), anchor='w')),
             partial(self.switch_control, 'subscription_media_switch', self.subscription_media_only), None),
            ('subscriptions', title(ui_theme.label(area, i18n.t('settings.subscription.first_new'), anchor='w')),
             partial(self.switch_control, 'subscription_first_switch', self.subscription_first_new), None),
            ('subscriptions', title(ui_theme.label(area, i18n.t('settings.subscription.schedule'), anchor='w')),
             self.subscription_interval_control, None),
            ('network', title(ui_theme.label(area, i18n.t('settings.proxy.title'), anchor='w')),
             self.proxy_control, None),
            ('network', title(ui_theme.label(area, i18n.t('settings.cookies.title'), anchor='w')),
             self.cookies_control, self.cookies_note),
            ('interface', title(ui_theme.label(area, i18n.t('settings.notify.title'), anchor='w')),
             partial(self.switch_control, 'notify_switch', self.notify_done, locked=False), None),
            ('interface', title(ui_theme.label(area, i18n.t('settings.clipboard.title'), anchor='w')),
             partial(self.switch_control, 'clipboard_switch', self.clipboard_hint, locked=False), None),
            ('interface', title(ui_theme.label(area, i18n.t('settings.appearance.title'), anchor='w')),
             self.appearance_control, None),
            # (1.7.0, I18N.md 6.2) after 外观; not in self.controls: the choice may change while busy.
            ('interface', title(ui_theme.label(area, i18n.t('settings.language.title'), anchor='w')),
             self.language_control, None),
        )
        right = (ui_theme.PAD, 0)       # every control ends on the grid's right edge
        line, group = 0, None
        for name, row_label, make, below in rows:
            first = name != group
            if first:
                group = name
                self.settings_groups[name].grid(row=line, column=0, columnspan=2, sticky='w',
                                                pady=(0 if line == 0 else ui_theme.SECTION, ui_theme.INLINE))
                line += 1
            top = 0 if first else ui_theme.INLINE
            if row_label is None:
                make(area).grid(row=line, column=0, columnspan=2, sticky='ew', pady=(top, 0))
            else:
                row_label.grid(row=line, column=0, sticky='w', pady=(top, 0))
                make(area).grid(row=line, column=1, sticky='e', padx=right, pady=(top, 0))
            line += 1
            if below is not None:
                below.grid(row=line, column=0, columnspan=2, sticky='w')
                line += 1
            if make == self.language_control:
                # (00-system 2.9) the notice that answers the language row sits right below it
                self.language_notice = ui_theme.notice_bar(area)
                self.language_notice.grid(row=line, column=0, columnspan=2, sticky='ew', pady=(ui_theme.INLINE, 0))
                self.language_notice.hide()
                line += 1

        # about: the versions, read-only (the program's version is shown only here, M17)
        self.settings_groups['about'].grid(row=line, column=0, columnspan=2, sticky='w', pady=(ui_theme.SECTION, ui_theme.INLINE))
        line += 1
        versions = component_versions()
        self.about_rows = []
        for name, value in ((i18n.t('settings.component.app'), f'v{APP_VERSION}'),
                            ('FFmpeg / ffprobe', versions['ffmpeg']), ('Deno', versions['deno'])):
            top = 0 if not self.about_rows else ui_theme.INLINE
            title = ui_theme.label(area, name, anchor='w')
            title.grid(row=line, column=0, sticky='w', pady=(top, 0))
            shown = ui_theme.label(area, value, text_color=ui_theme.MUTED, anchor='e')
            shown.grid(row=line, column=1, sticky='e', padx=right, pady=(top, 0))
            self.about_rows.append((title, shown))
            line += 1
        # the grid is at most SETTINGS_MAX wide: the area's spare column takes the rest of the viewport's width
        ui_theme.set_page_cap(self, area.canvas, {area: ui_theme.SETTINGS_MAX})
        self.refresh_engine_labels()
        # (1.7.0, I18N.md 6.3 step 8) After every page exists: the input kept by a restart.
        if not self.smoke_mode:
            self.after_idle(self.restore_restart_state)

    # ---------------------------------------------------------------- the rows' controls
    def engine_row(self, area):
        """yt-dlp {version} … (恢复内置引擎) [检查更新 | 停止]; the status line below, across the row, wrapped."""
        row = ctk.CTkFrame(area, fg_color='transparent', corner_radius=0)
        row.grid_columnconfigure(0, weight=1)
        self.version_label = ui_theme.label(row, f'yt-dlp {shown_engine_version()}', height=ui_theme.CONTROL_H,
                                            anchor='w')
        self.version_label.grid(row=0, column=0, sticky='w')
        restore_text = i18n.t('settings.engine.restore')
        self.restore_button = ui_theme.button(row, restore_text, self.restore_engine, kind='ghost',
                                              width=ui_theme.fitted_width(row, restore_text))
        self.restore_button.grid(row=0, column=1, sticky='e', padx=(ui_theme.PAD, ui_theme.INLINE))
        self.restore_button.grid_remove()
        # 检查更新 and 停止 share one cell and one width: the row does not move when they swap.
        check_text, stop_text = i18n.t('settings.engine.check'), i18n.t('common.stop')
        width = max(ui_theme.fitted_width(row, check_text), ui_theme.fitted_width(row, stop_text))
        self.check_update_button = ui_theme.button(row, check_text, self.update_engine, kind='primary', width=width)
        self.check_update_button.grid(row=0, column=2, sticky='e')
        self.update_buttons.append(self.check_update_button)
        self.stop_update_button = ui_theme.button(row, stop_text, self.stop, kind='danger', width=width)
        self.stop_update_button.grid(row=0, column=2, sticky='e')
        self.stop_update_button.grid_remove()
        self.update_status = ui_theme.label(row, '', role='caption', text_color=ui_theme.MUTED, anchor='w',
                                            justify='left', wraplength=360)
        self.update_status.grid(row=1, column=0, columnspan=3, sticky='ew')
        ui_theme.wrap_to_width(self.update_status)
        self.update_status.grid_remove()
        return row

    def switch_control(self, name, variable, area, locked=True, command=None):
        """A row's switch, kept as ``self.<name>``; ``locked`` ones join self.controls (grey while busy)."""
        widget = ui_theme.switch(area, variable=variable, command=command)
        setattr(self, name, widget)
        if locked:
            self.controls.append(widget)
        return widget

    def naming_control(self, area):
        # The menu shows the label of the running language and stores the naming key in self.naming (I18N.md 7).
        labels = [i18n.label('naming', key) for key in naming.NAMING]
        self.naming_menu = ui_theme.option_menu(area, list(naming.NAMING), labels=labels, variable=self.naming,
                                                command=self.on_naming, width=self.settings_menu_w)
        self.controls.append(self.naming_menu)
        return self.naming_menu

    def archive_folder_control(self, area):
        """The archive folder: changed only through the folder dialog, whenever the window is idle (the collect
        page's 归档正文 and 关联转写稿… write there too, whatever 统一归档 says)."""
        self.archive_folder_button = ui_theme.path_button(area, self.choose_archive)
        self.archive_folder_button.set_path(self.archive_root.get())
        self.archive_root.trace_add('write', lambda *_: self.archive_folder_button.set_path(self.archive_root.get()))
        self.controls.append(self.archive_folder_button)
        return self.archive_folder_button

    def subscription_interval_control(self, area):
        """The schedule of a new subscription: settings.SCHEDULES (seconds) in menu order."""
        labels = [i18n.label('interval', seconds) for seconds in settings.SCHEDULES]
        self.subscription_interval_menu = ui_theme.option_menu(area, list(settings.SCHEDULES), labels=labels,
                                                               variable=self.subscription_interval,
                                                               width=self.settings_menu_w)
        self.controls.append(self.subscription_interval_menu)
        return self.subscription_interval_menu

    def proxy_control(self, area):
        """本次代理: never stored (not in PERSISTED), the label says so."""
        self.proxy_entry = ui_theme.entry(area, textvariable=self.proxy, placeholder=i18n.t('settings.proxy.placeholder'),
                                          width=ui_theme.PATH_W)
        self.controls.append(self.proxy_entry)
        return self.proxy_entry

    def cookies_control(self, area):
        """The cookies file on a path button (chosen through the file dialog and checked) and 清除, together PATH_W
        wide; 清除 is grey while no file is chosen (refresh_settings_controls)."""
        holder = ctk.CTkFrame(area, fg_color='transparent', corner_radius=0, width=0, height=0)
        clear_text = i18n.t('common.clear')
        clear_w = ui_theme.fitted_width(holder, clear_text)
        self.cookies_button = ui_theme.path_button(holder, self.choose_cookies,
                                                   width=ui_theme.PATH_W - ui_theme.INLINE - clear_w)
        self.cookies_button.grid(row=0, column=0)
        # anchor 'e': the text of the ghost button ends on the right edge of the column, as the other controls do
        self.clear_cookies_button = ui_theme.button(holder, clear_text, self.clear_cookies, kind='ghost', width=clear_w,
                                                    anchor='e')
        self.clear_cookies_button.grid(row=0, column=1, padx=(ui_theme.INLINE, 0))
        self.cookies_button.set_path(self.cookies.get().strip())
        self.cookies.trace_add('write', lambda *_: self.cookies_changed())
        self.controls.append(self.cookies_button)
        return holder

    def cookies_changed(self):
        self.cookies_button.set_path(self.cookies.get().strip())
        self.refresh_settings_controls()

    # ================================================================ appearance (WP7)
    def appearance_control(self, area):
        """The 浅色 / 深色 / 跟随系统 choice; it shows the mode the window was started in."""
        if ctk.AppearanceModeTracker.appearance_mode_set_by == 'system':
            current = 'system'
        else:
            current = 'dark' if ctk.get_appearance_mode() == 'Dark' else 'light'
        self.appearance_choice = ui_theme.segmented(area, list(APPEARANCE),
                                                    [i18n.label('appearance', mode) for mode in APPEARANCE],
                                                    command=self.choose_appearance, height=32,
                                                    font=ui_theme.font('body'))
        self.appearance_choice.set(current)
        return self.appearance_choice

    def choose_appearance(self, choice):
        """Apply the chosen mode at once (no restart) and keep it in settings.json ('appearance').
        ``choice`` is the stored value ('light', 'dark', 'system'); anything else is ignored."""
        if choice not in APPEARANCE:
            return
        mode = choice
        if self.appearance_choice.get() != mode:
            self.appearance_choice.set(mode)
        ctk.set_appearance_mode(mode)
        if isinstance(self.prefs, dict) and self.prefs.get('appearance') != mode:
            self.prefs['appearance'] = mode
            self.schedule_save()

    def on_naming(self, choice):
        """The naming menu hands over the naming key; anything else is ignored."""
        if choice in naming.NAMING and self.naming.get() != choice:
            self.naming.set(choice)

    # ================================================================ language (1.7.0, I18N.md 6.2)
    def language_control(self, area):
        """跟随系统 / 简体中文 / English: it shows the stored choice, not the resolved language."""
        self.language_saved = True
        self.language_notice_kind = None
        self.language_notice_look = None
        self.language_choice = ui_theme.segmented(area, list(i18n.CHOICES),
                                                  [i18n.label('language', value) for value in i18n.CHOICES],
                                                  command=self.choose_language)
        self.language_choice.set(i18n.stored_choice(self.prefs))
        return self.language_choice

    def choose_language(self, value):
        """Store the choice at once (settings.json 'language', through prefs['language']); it applies
        at the next start.  Allowed while busy: only the restart waits."""
        if value not in i18n.CHOICES:
            return
        if self.language_choice.get() != value:
            self.language_choice.set(value)
        self.language_saved = i18n.store_choice(self.prefs, value)
        if self.language_saved:
            self.schedule_save()
        self.refresh_language_note(value)

    def refresh_language_note(self, value=None):
        """Held session -> settings.language.held; the variable set -> env_fixed (no button); a
        choice that resolves to another language than the running one -> the restart note and
        立即重启; otherwise the notice is hidden."""
        if value is None:
            value = self.language_choice.get()
        if not getattr(self, 'language_saved', True):
            kind = 'held'
        elif i18n.env_override() is not None:
            kind = 'env_fixed'
        elif i18n.resolve(value, environ={}) != i18n.language():
            kind = 'restart'
        else:
            kind = None
        self.language_notice_kind = kind
        self.show_language_notice()

    def show_language_notice(self):
        """The notice under the language row.  'restart': 重启后生效 with 立即重启 while idle (never in smoke mode,
        which never saves), 任务结束后可重启 without a button while busy (restart_app refuses then anyway)."""
        kind = getattr(self, 'language_notice_kind', None)
        notice = getattr(self, 'language_notice', None)
        if notice is None:
            return
        if kind is None:
            self.language_notice_look = None
            notice.hide()
            return
        actions = ()
        if kind == 'restart':
            if self.busy:
                text = i18n.t('settings.language.restart_busy')
            else:
                text = i18n.t('settings.language.restart_note')
                if not (self.smoke_mode or self.closing):
                    actions = ((i18n.t('settings.language.restart'), self.restart_app),)
            tone = 'idle'
        elif kind == 'held':
            text, tone = i18n.t('settings.language.held'), 'warn'
        else:
            text, tone = i18n.t('settings.language.env_fixed'), 'idle'
        look = (text, tone, bool(actions))
        if look != self.language_notice_look or not notice.shown:     # set_busy calls this: redraw on change only
            self.language_notice_look = look
            notice.show(text, actions, tone)

    # ================================================================ restart (1.7.0, I18N.md 6.3)
    def restart_launcher(self, command, env):
        """Start the new process; tests replace it (an instance attribute).  Under the test runner
        procutil.spawn_detached refuses with OSError."""
        return procutil.spawn_detached(command, env)

    def restart_app(self):
        """立即重启: keep the unsaved input, save the settings, start the program again and close."""
        if self.busy or self.closing or self.smoke_mode:
            return
        state_path = restart_state_file()
        written = False
        kept = self.restart_inputs()
        if any(value.strip() for value in kept.values()):
            record = {'version': RESTART_STATE_VERSION, 'created': time.time(), 'pid': os.getpid(), **kept}
            try:
                paths.atomic_write_text(state_path, json.dumps(record, ensure_ascii=False))
                written = True
            except OSError:
                if not messagebox.askyesno(i18n.t('dialog.restart_unsaved.title'), i18n.t('dialog.restart_unsaved.text'),
                                           parent=self):
                    return
        self.save_settings()
        try:
            self.restart_launcher(procutil.relaunch_command(), procutil.restart_environment())
        except OSError as error:
            if written:
                try:
                    state_path.unlink()
                except OSError:
                    pass
            messagebox.showerror(i18n.t('dialog.restart_failed.title'),
                                 i18n.t('dialog.restart_failed.text', error=messages.error_text(error)), parent=self)
            return
        self.finish_close()

    def restart_inputs(self):
        """{urls, collect_urls}: the two link boxes, which save_settings does not store."""
        def box(widget):
            try:
                return widget.get('1.0', 'end-1c') if widget is not None else ''
            except tk.TclError:
                return ''
        return {'urls': box(getattr(self, 'urls', None)), 'collect_urls': box(getattr(self, 'collect_urls', None))}

    def restore_restart_state(self):
        """Read restart-state.json once (younger than RESTART_STATE_MAX_AGE, version 1), delete it in
        every case and fill the two link boxes that are still empty."""
        if self.smoke_mode or self.closing:
            return
        if not all(hasattr(self, name) for name in RESTART_STATE_KEYS):
            try:
                self.after(50, self.restore_restart_state)     # not every page exists yet
            except tk.TclError:
                pass
            return
        path = restart_state_file()
        try:
            raw = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return
        except OSError:
            raw = None
        try:
            path.unlink()
        except OSError:
            pass
        try:
            data = json.loads(raw) if raw is not None else None
        except ValueError:
            data = None
        if not isinstance(data, dict) or data.get('version') != RESTART_STATE_VERSION:
            return
        created = data.get('created')
        if isinstance(created, bool) or not isinstance(created, (int, float)) \
                or not -5 <= time.time() - created < RESTART_STATE_MAX_AGE:
            return
        restored = False
        try:
            urls = data.get('urls')
            if isinstance(urls, str) and urls.strip() and not self.urls.get('1.0', 'end-1c').strip():
                self.urls.insert('1.0', urls)
                self.links_changed()
                restored = True
            pages = data.get('collect_urls')
            if isinstance(pages, str) and pages.strip() and not self.collect_urls.get('1.0', 'end-1c').strip():
                self.collect_urls.insert('1.0', pages)
                self.collect_urls.sync_placeholder()
                restored = True
        except tk.TclError:
            return
        if restored:
            self.append_log(i18n.t('log.restart_restored'))

    # ================================================================ cookies
    def choose_cookies(self):
        current = Path(self.cookies.get().strip()).expanduser() if self.cookies.get().strip() else None
        initial = str(current.parent) if current and current.parent.is_dir() else str(Path.home())
        path = filedialog.askopenfilename(parent=self, title=i18n.t('dialog.cookies.title'), initialdir=initial,
                                          filetypes=[(i18n.t('dialog.cookies.filetype'), '*.txt'),
                                                     (i18n.t('dialog.filetype.all'), '*.*')])
        if not path:
            return
        try:
            check_cookie_file(path)
        except (ValueError, OSError) as error:
            messagebox.showwarning(i18n.t('dialog.cookies.unusable'), messages.error_text(error), parent=self)
            return
        self.cookies.set(str(Path(path)))
        self.save_settings()
        self.append_log(i18n.t('log.cookies_set'))
        self.cancel_bot_retry()

    def clear_cookies(self):
        self.cookies.set('')
        self.save_settings()

    # ================================================================ engine updates
    def refresh_engine_labels(self):
        version = engine_version()
        shown = shown_engine_version(version)
        self.version_label.configure(text=f'yt-dlp {shown}')
        self.engine_updated = updated_engine_active()
        self.refresh_settings_controls()
        latest = self.prefs.get('latest_engine') if isinstance(self.prefs, dict) else ''
        newer = bool(latest and version_newer(latest, version))
        self.show_engine_notice(newer)
        if newer and not self.busy and not self.update_status.cget('text'):
            # a newer version known from an earlier check is said at start too, until something else is said
            shown_latest = messages.show(messages.raw(latest))
            self.update_status.configure(text=i18n.t('settings.update.available', latest=shown_latest),
                                         text_color=ui_theme.ACCENT_TEXT)
            _reveal(self.update_status)

    def show_settings(self, group):
        """Open the settings page with group ``group`` (one of GROUPS) at the top of the view: the sidebar's
        new-version link ('engine'), the download page's archive folder button ('downloads')."""
        self.show_page('settings')
        heading = getattr(self, 'settings_groups', {}).get(group)
        if heading is None:
            return
        area = self.settings_area
        try:
            self.update_idletasks()         # the page is shown first: a hidden page reports no height
            height = area.winfo_height()
            fraction = 0.0 if group == GROUPS[0] or height <= 1 else heading.winfo_y() / height
            area.canvas.yview_moveto(fraction)
        except tk.TclError:
            pass

    def refresh_settings_controls(self):
        """The controls of this page that depend on more than the busy lock (called from refresh_shell after every
        set_busy): 恢复内置引擎 is shown only while an updated engine is in use and usable when idle; 停止 takes
        the place of 检查更新 while an engine update runs; 清除 is usable when idle and a cookies file is chosen;
        a restart notice of the language row follows the busy state."""
        idle = not self.busy
        ui_theme.show(self.restore_button, self.engine_updated)
        ui_theme.set_enabled(self.restore_button, self.engine_updated and idle)
        updating = self.busy and self.operation == 'update'
        ui_theme.show(self.stop_update_button, updating)
        ui_theme.show(self.check_update_button, not updating)      # its state is set_busy's (update_buttons)
        ui_theme.set_enabled(self.clear_cookies_button, idle and bool(self.cookies.get().strip()))
        if getattr(self, 'language_notice_kind', None) == 'restart':
            self.show_language_notice()

    def update_engine(self):
        if self.busy:
            return
        self.operation = 'update'
        self.set_busy(True)
        self.progress.set(0, immediate=True)
        self.animate(True)
        self.operation = 'update'
        self.update_status.configure(text=i18n.t('core.update.checking'), text_color=ui_theme.ACCENT_TEXT)
        _reveal(self.update_status)
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
        if not messagebox.askyesno(i18n.t('settings.engine.restore'), i18n.t('dialog.restore.text'), parent=self):
            return
        try:
            updater.restore_bundled()
        except Exception as error:
            # (1.7.0, request of fix-core) restore_bundled raises (core.update.restore_failed) when the
            # updated engine is still in use: the labels and the still enabled button tell the truth.
            messagebox.showwarning(i18n.t('dialog.restore.failed'), messages.error_text(error), parent=self)
            self.refresh_engine_labels()
            return
        self.refresh_engine_labels()
        self.update_status.configure(text=i18n.t('settings.update.restored'), text_color=ui_theme.MUTED)
        _reveal(self.update_status)
        self.append_log(i18n.t('log.engine_restored', version=shown_engine_version()))

    def on_update_status(self, value):
        if not (self.busy and self.operation == 'update'):
            return
        self.update_status.configure(text=messages.show(value), text_color=ui_theme.ACCENT_TEXT)
        _reveal(self.update_status)

    def on_update_progress(self, value):
        if not (self.busy and self.operation == 'update'):
            return
        fraction = value.get('fraction') if isinstance(value, dict) else None
        if fraction is None:
            return
        self.animate(False)
        self.progress.set(fraction)
        self.update_status.configure(text=i18n.t('settings.update.downloading_pct', percent=f'{100 * fraction:.0f}%'),
                                     text_color=ui_theme.ACCENT_TEXT)
        _reveal(self.update_status)

    def on_updated(self, value):
        version = value['version']
        shown = shown_engine_version(version)
        self.version_label.configure(text=f'yt-dlp {shown}')
        self.update_status.configure(text=(i18n.t('settings.update.latest') if value['unchanged'] else
                                           i18n.t('settings.update.done')), text_color=ui_theme.OK)
        _reveal(self.update_status)
        self.prefs['latest_engine'] = version
        self.schedule_save()        # (1.8.1) as on_engine_check: the new version survives a restart
        self.append_log(i18n.t('log.engine_checked', version=shown))

    def on_update_error(self, value):
        error = messages.show(value)
        self.update_status.configure(text=i18n.t('settings.update.failed', error=error), text_color=ui_theme.WARN)
        _reveal(self.update_status)
        self.append_log(i18n.t('log.update_failed', error=error))

    def on_update_done(self, value):
        try:
            self.animate(False)
        finally:
            self.set_busy(False)
        if value['cancelled']:
            self.update_status.configure(text=i18n.t('settings.update.cancelled'), text_color=ui_theme.MUTED)
            _reveal(self.update_status)
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
                self.update_status.configure(text=i18n.t('settings.update.available', latest=value.get('latest')),
                                             text_color=ui_theme.ACCENT_TEXT)
                _reveal(self.update_status)
            self.show_engine_notice(True)
