"""Settings page and engine-updater UI of StudioApp (mixin SettingsUI; 1.6.0 WP4).

(1.7.0) Every shown text through i18n (I18N.md 8); the language row 语言 / Language in the card
提醒与界面 and the restart that applies it (I18N.md 6.2, 6.3).

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
from tkinter import filedialog, messagebox

import customtkinter as ctk
import engine
import i18n
import messages
import naming
import paths
import procutil
import ui_theme
import updater
from engine import APP_VERSION, check_cookie_file, engine_version
from library_ui import text_button_width

UPDATE_IDLE = '从官方稳定版更新；下载后校验 SHA256，并确认与本程序兼容。'
DAY = 86400
# The proxy entry and the cookies entry with its two buttons take the same width, so both
# rows of the network card line up on the left and on the right.  (1.7.0, I18N.md 10.1) Both grow
# by the same amount when the labels of the two buttons need more than COOKIE_BUTTON_W.
COOKIE_ROW_W = 376
COOKIE_BUTTON_W = 60
# (WP7) settings.json 'appearance' value -> label of the 外观 choice
APPEARANCE_LABELS = {'light': '浅色', 'dark': '深色', 'system': '跟随系统'}
# (1.7.0, I18N.md 6.3) The unsaved input a restart keeps: next to settings.json, read once.
RESTART_STATE = 'restart-state.json'
RESTART_STATE_VERSION = 1
RESTART_STATE_MAX_AGE = 120     # s: an older file is deleted and ignored
RESTART_STATE_KEYS = ('urls', 'collect_urls', 'subscription_name')


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


class SettingsUI:
    # ================================================================ page
    def build_settings_page(self):
        page = self.page('settings', i18n.t('nav.settings'), i18n.t('settings.subtitle'), max_width=ui_theme.PAGE_MAX)
        page.grid_rowconfigure(1, weight=1)
        self.settings_area = ui_theme.scroll_area(page)
        self.settings_area.grid(row=1, column=0, sticky='nsew')
        self.settings_area.grid_columnconfigure(0, weight=1)
        self.settings_cards = []

        # 1. engine
        body = self.settings_card(i18n.t('settings.card.engine'))
        engine_row = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0)
        engine_row.grid_columnconfigure(0, weight=1)
        self.version_label = ui_theme.label(engine_row, f'yt-dlp  {shown_engine_version()}', role='heading',
                                            text_color=ui_theme.ACCENT_TEXT, anchor='w')
        self.version_label.grid(row=0, column=0, sticky='ew')
        self.update_status = ui_theme.label(engine_row, i18n.t('settings.update.idle'), role='caption',
                                            text_color=ui_theme.MUTED, anchor='w', justify='left', wraplength=360)
        self.update_status.grid(row=1, column=0, sticky='ew', pady=(2, 0))
        ui_theme.wrap_to_width(self.update_status)
        actions = ctk.CTkFrame(engine_row, fg_color='transparent', corner_radius=0)
        actions.grid(row=0, column=1, rowspan=2, sticky='e', padx=(ui_theme.CARD_PAD, 0))
        restore_text = i18n.t('settings.engine.restore')
        self.restore_button = ui_theme.button(actions, restore_text, self.restore_engine, kind='ghost',
                                              width=ui_theme.fitted_width(actions, restore_text))
        self.restore_button.grid(row=0, column=0, padx=(0, 8))
        check_text = i18n.t('settings.engine.check')
        update = ui_theme.button(actions, check_text, self.update_engine, kind='primary',
                                 width=max(104, ui_theme.fitted_width(actions, check_text)))
        update.grid(row=0, column=1)
        self.update_buttons.append(update)
        # Shown only while an engine update runs: the page that started it can also stop it.
        cancel_text = i18n.t('common.cancel')
        self.cancel_update_button = ui_theme.button(actions, cancel_text, self.stop, kind='ghost',
                                                    width=max(72, ui_theme.fitted_width(actions, cancel_text)))
        self.cancel_update_button.grid(row=0, column=2, padx=(8, 0))
        self.cancel_update_button.grid_remove()
        self.engine_updated = False
        self.settings_rows(body, [engine_row, ui_theme.setting_row(
            body, i18n.t('settings.engine.daily.title'), i18n.t('settings.engine.daily.text'),
            lambda area: ui_theme.switch(area, variable=self.check_engine_updates))])

        # 2. files and naming
        body = self.settings_card(i18n.t('settings.card.files'))
        # naming_label keeps the zh label of self.naming (H4, test-visible); the menu shows the
        # label of the running language and stores the naming key in self.naming (I18N.md 7).
        self.naming_label = tk.StringVar(value=naming.LABELS.get(self.naming.get(), naming.LABELS['title']))
        self.naming.trace_add('write', lambda *_: self.naming_label.set(naming.LABELS.get(self.naming.get(), '')))
        naming_labels = [i18n.label('naming', key) for key in naming.LABELS]
        naming_row = ui_theme.setting_row(
            body, i18n.t('settings.naming.title'), i18n.t('settings.naming.text'),
            lambda area: ui_theme.option_menu(area, list(naming.LABELS), labels=naming_labels, variable=self.naming,
                                              command=self.on_naming,
                                              width=max(160, ui_theme.menu_width(area, naming_labels))))
        self.naming_menu = naming_row.control
        tags = ui_theme.setting_row(body, i18n.t('settings.tags.title'), i18n.t('settings.tags.text'),
                                    lambda area: ui_theme.switch(area, variable=self.embed_metadata))
        cover = ui_theme.setting_row(body, i18n.t('settings.cover.title'), i18n.t('settings.cover.text'),
                                     lambda area: ui_theme.switch(area, variable=self.embed_cover))
        skip = ui_theme.setting_row(body, i18n.t('settings.skip.title'), i18n.t('settings.skip.text'),
                                    lambda area: ui_theme.switch(area, variable=self.skip_downloaded))
        self.settings_rows(body, [naming_row, tags, cover, skip])
        self.controls += [self.naming_menu, tags.control, cover.control, skip.control]

        # 3. network
        body = self.settings_card(i18n.t('settings.card.network'))
        choose_text, clear_text = i18n.t('common.choose'), i18n.t('common.clear')
        button_w = max(COOKIE_BUTTON_W, text_button_width(body, choose_text), text_button_width(body, clear_text))
        row_w = COOKIE_ROW_W + 2 * (button_w - COOKIE_BUTTON_W)
        proxy = ui_theme.setting_row(body, i18n.t('settings.proxy.title'), i18n.t('settings.proxy.text'),
                                     lambda area: ui_theme.entry(area, textvariable=self.proxy,
                                                                 placeholder=i18n.t('settings.proxy.placeholder'),
                                                                 width=row_w))

        def cookie_controls(area):
            holder = ctk.CTkFrame(area, fg_color='transparent', corner_radius=0)
            self.cookies_entry = ui_theme.entry(holder, textvariable=self.cookies,
                                                placeholder=i18n.t('settings.cookies.placeholder'),
                                                width=row_w - 2 * (8 + button_w))
            self.cookies_entry.grid(row=0, column=0)
            self.choose_cookies_button = ui_theme.button(holder, choose_text, self.choose_cookies, width=button_w)
            self.choose_cookies_button.grid(row=0, column=1, padx=(8, 0))
            self.clear_cookies_button = ui_theme.button(holder, clear_text, self.clear_cookies, width=button_w)
            self.clear_cookies_button.grid(row=0, column=2, padx=(8, 0))
            return holder
        cookies = ui_theme.setting_row(body, i18n.t('settings.cookies.title'), i18n.t('settings.cookies.text'),
                                       cookie_controls)
        self.settings_rows(body, [proxy, cookies])
        self.cookies.trace_add('write', lambda *_: self.after_idle(self._show_cookie_tail))
        self.controls += [proxy.control, self.cookies_entry, self.choose_cookies_button, self.clear_cookies_button]

        # 4. batch downloads
        body = self.settings_card(i18n.t('settings.card.batch'))
        batch = ui_theme.setting_row(body, i18n.t('settings.steady.title'), i18n.t('settings.steady.text'),
                                     lambda area: ui_theme.switch(area, variable=self.batch_safe))
        self.batch_switch = batch.control
        self.settings_rows(body, [batch])
        self.controls.append(self.batch_switch)

        # 5. notifications and interface
        body = self.settings_card(i18n.t('settings.card.interface'))
        self.settings_rows(body, [
            ui_theme.setting_row(body, i18n.t('settings.notify.title'), i18n.t('settings.notify.text'),
                                 lambda area: ui_theme.switch(area, variable=self.notify_done)),
            ui_theme.setting_row(body, i18n.t('settings.clipboard.title'), i18n.t('settings.clipboard.text'),
                                 lambda area: ui_theme.switch(area, variable=self.clipboard_hint)),
            ui_theme.setting_row(body, i18n.t('settings.appearance.title'), i18n.t('settings.appearance.text'),
                                 self.appearance_control),
            # (1.7.0, I18N.md 6.2) after 外观; not in self.controls: the choice may change while busy.
            ui_theme.setting_row(body, i18n.t('settings.language.title'), i18n.t('settings.language.text'),
                                 self.language_control)])
        self.build_language_strip(body, row=4)

        # 6. app and bundled components
        body = self.settings_card(i18n.t('settings.card.app'))
        versions = component_versions()
        rows = []
        for name, value in ((i18n.t('settings.component.app'), f'v{APP_VERSION}'),
                            ('FFmpeg / ffprobe', versions['ffmpeg']), ('Deno', versions['deno'])):
            line = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0)
            line.grid_columnconfigure(0, weight=1)
            ui_theme.label(line, name, anchor='w').grid(row=0, column=0, sticky='w')
            ui_theme.label(line, value, text_color=ui_theme.MUTED, anchor='e').grid(row=0, column=1, sticky='e')
            rows.append(line)
        note = ui_theme.label(body, i18n.t('settings.component.note'), role='caption',
                              text_color=ui_theme.MUTED, anchor='w')
        rows.append(note)
        self.settings_rows(body, rows, gap=6)
        self.refresh_engine_labels()
        # (1.7.0, I18N.md 6.3 step 8) After every page exists: the input kept by a restart.
        if not self.smoke_mode:
            self.after_idle(self.restore_restart_state)

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
        self.appearance_choice = ui_theme.segmented(area, list(APPEARANCE_LABELS),
                                                    [i18n.label('appearance', mode) for mode in APPEARANCE_LABELS],
                                                    command=self.choose_appearance, height=32,
                                                    font=ui_theme.font('body'))
        self.appearance_choice.set(current)
        return self.appearance_choice

    def choose_appearance(self, choice):
        """Apply the chosen mode at once (no restart) and keep it in settings.json ('appearance').
        ``choice`` is the stored value ('light', 'dark', 'system'); the zh label of 1.6.0 is accepted too."""
        mode = choice if choice in APPEARANCE_LABELS else \
            next((key for key, text in APPEARANCE_LABELS.items() if text == choice), None)
        if mode is None:
            return
        if self.appearance_choice.get() != mode:
            self.appearance_choice.set(mode)
        ctk.set_appearance_mode(mode)
        if isinstance(self.prefs, dict) and self.prefs.get('appearance') != mode:
            self.prefs['appearance'] = mode
            self.schedule_save()

    def on_naming(self, choice):
        """The naming menu hands over the naming key (a zh label of 1.6.0 is accepted too)."""
        if choice in naming.LABELS:
            if self.naming.get() != choice:
                self.naming.set(choice)
            return
        for key, text in naming.LABELS.items():
            if text == choice:
                self.naming.set(key)

    def _show_cookie_tail(self):
        try:
            self.cookies_entry.xview_moveto(1.0)
        except (tk.TclError, AttributeError):
            pass

    # ================================================================ language (1.7.0, I18N.md 6.2)
    def language_control(self, area):
        """跟随系统 / 简体中文 / English: it shows the stored choice, not the resolved language."""
        self.language_saved = True
        self.language_strip_kind = None
        self.language_choice = ui_theme.segmented(area, list(i18n.CHOICES),
                                                  [i18n.label('language', value) for value in i18n.CHOICES],
                                                  command=self.choose_language)
        self.language_choice.set(i18n.stored_choice(self.prefs))
        return self.language_choice

    def build_language_strip(self, body, row):
        """The note under the language row and its 立即重启 button; hidden until choose_language."""
        strip = ctk.CTkFrame(body, fg_color='transparent', corner_radius=0)
        strip.grid(row=row, column=0, sticky='ew', pady=(8, 0))
        strip.grid_columnconfigure(0, weight=1)
        self.language_strip = strip
        self.language_note = ui_theme.label(strip, '', role='caption', text_color=ui_theme.ACCENT_TEXT, anchor='w',
                                            justify='left', wraplength=360)
        self.language_note.grid(row=0, column=0, sticky='ew')
        ui_theme.wrap_to_width(self.language_note)
        text = i18n.t('settings.language.restart')
        self.language_restart = ui_theme.button(strip, text, self.restart_app, kind='primary',
                                                width=ui_theme.fitted_width(strip, text))
        self.language_restart.grid(row=0, column=1, sticky='e', padx=(ui_theme.CARD_PAD, 0))
        self.language_restart.grid_remove()
        strip.grid_remove()

    def choose_language(self, value):
        """Store the choice at once (settings.json 'language', through prefs['_extra']); it applies
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
        立即重启; otherwise the strip is hidden."""
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
        self.language_strip_kind = kind
        self.show_language_strip()

    def show_language_strip(self):
        kind = getattr(self, 'language_strip_kind', None)
        strip = getattr(self, 'language_strip', None)
        if strip is None:
            return
        if kind is None:
            strip.grid_remove()
            return
        button = False
        if kind == 'restart':
            if self.busy:
                text = i18n.t('settings.language.restart_busy')
            else:
                text = i18n.t('settings.language.restart_note')
            tone = ui_theme.ACCENT_TEXT
            button = not self.smoke_mode       # smoke mode never saves, so it never restarts
        elif kind == 'held':
            text, tone = i18n.t('settings.language.held'), ui_theme.WARN
        else:
            text, tone = i18n.t('settings.language.env_fixed'), ui_theme.MUTED
        if self.language_note.cget('text') != text:
            self.language_note.configure(text=text, text_color=tone)
        if button:
            state = 'disabled' if self.busy or self.closing else 'normal'
            if self.language_restart.cget('state') != state:
                self.language_restart.configure(state=state)
            self.language_restart.grid()
        else:
            self.language_restart.grid_remove()
        strip.grid()

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
        """{urls, collect_urls, subscription_name}: the inputs save_settings does not store."""
        def box(widget):
            try:
                return widget.get('1.0', 'end-1c') if widget is not None else ''
            except tk.TclError:
                return ''
        try:
            name = self.subscription_name.get()
        except (AttributeError, tk.TclError):
            name = ''
        return {'urls': box(getattr(self, 'urls', None)), 'collect_urls': box(getattr(self, 'collect_urls', None)),
                'subscription_name': name}

    def restore_restart_state(self):
        """Read restart-state.json once (younger than RESTART_STATE_MAX_AGE, version 1), delete it in
        every case and fill the three inputs that are still empty."""
        if self.smoke_mode or self.closing:
            return
        if not all(hasattr(self, name) for name in ('urls', 'collect_urls', 'subscription_name')):
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
                self.sync_collect_placeholder()
                restored = True
            name = data.get('subscription_name')
            if isinstance(name, str) and name.strip() and not self.subscription_name.get().strip():
                self.subscription_name.set(name)
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
        self.version_label.configure(text=f'yt-dlp  {shown}')
        self.engine_badge.configure(text=f'yt-dlp {shown}')
        self.engine_updated = updated_engine_active()
        self.refresh_engine_controls()
        latest = self.prefs.get('latest_engine') if isinstance(self.prefs, dict) else ''
        self.show_app_badge(bool(latest and version_newer(latest, version)))

    def refresh_engine_controls(self):
        """恢复内置引擎 follows the busy lock like the other controls (it is enabled only while an updated
        engine is active); 取消 is shown only while an engine update runs.  Called from refresh_shell.
        (1.7.0) A visible restart strip of the language row follows the busy state too."""
        self.restore_button.configure(state='normal' if self.engine_updated and not self.busy else 'disabled')
        updating = self.busy and self.operation == 'update'
        if updating != bool(self.cancel_update_button.winfo_manager()):
            if updating:
                self.cancel_update_button.grid()
            else:
                self.cancel_update_button.grid_remove()
        if getattr(self, 'language_strip_kind', None) == 'restart':
            self.show_language_strip()

    def update_engine(self):
        if self.busy:
            return
        self.operation = 'update'
        self.set_busy(True)
        self.progress.set(0, immediate=True)
        self.animate(True)
        self.operation = 'update'
        self.restore_button.configure(state='disabled')
        self.update_status.configure(text=i18n.t('core.update.checking'), text_color=ui_theme.ACCENT_TEXT)
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
            # (1.7.0, request of fix-core) restore_bundled raises (updater.MSG_RESTORE) when the
            # updated engine is still in use: the labels and the still enabled button tell the truth.
            messagebox.showwarning(i18n.t('dialog.restore.failed'), messages.error_text(error), parent=self)
            self.refresh_engine_labels()
            return
        self.refresh_engine_labels()
        self.update_status.configure(text=i18n.t('settings.update.restored'), text_color=ui_theme.MUTED)
        self.append_log(i18n.t('log.engine_restored', version=shown_engine_version()))

    def on_update_status(self, value):
        if not (self.busy and self.operation == 'update'):
            return
        self.update_status.configure(text=messages.show(value), text_color=ui_theme.ACCENT_TEXT)

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

    def on_updated(self, value):
        version = value['version']
        shown = shown_engine_version(version)
        self.version_label.configure(text=f'yt-dlp  {shown}')
        self.engine_badge.configure(text=f'yt-dlp {shown}')
        self.update_status.configure(text=(i18n.t('settings.update.latest') if value['unchanged'] else
                                           i18n.t('settings.update.done')), text_color=ui_theme.OK)
        self.prefs['latest_engine'] = version
        self.append_log(i18n.t('log.engine_checked', version=shown))

    def on_update_error(self, value):
        error = messages.show(value)
        self.update_status.configure(text=i18n.t('settings.update.failed', error=error), text_color=ui_theme.WARN)
        self.append_log(i18n.t('log.update_failed', error=error))

    def on_update_done(self, value):
        try:
            self.animate(False)
        finally:
            self.set_busy(False)
        if value['cancelled']:
            self.update_status.configure(text=i18n.t('settings.update.cancelled'), text_color=ui_theme.MUTED)
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
            self.show_app_badge(True)
