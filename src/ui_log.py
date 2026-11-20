"""Log page of StudioApp (mixin LogUI; 1.6.0 WP4).

append_log() only buffers (and writes the log file); poll() calls flush_log(), which writes at
most 200 lines per tick into the tagged text box.  Never import studio here at module level.
"""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox

import i18n
import messages
import paths
import ui_theme

MAX_LINES = 1500
TRIM_LINES = 300
FLUSH_LIMIT = 200
TAGS = {'error': ui_theme.DANGER, 'warn': ui_theme.WARN, 'ok': ui_theme.OK, 'dim': ui_theme.MUTED, 'app': ui_theme.INK}
_ENGINE_PREFIX = re.compile(r'^\[[\w:]+\]')
_SLEEPING = re.compile(r'^\[[\w:]+\] Sleeping [\d.]+ seconds')


def classify(line):
    """Tag of one log line: error, warn, dim, ok or app (WP4.md section G, in this order).  The engine's own
    lines by their start; the app's lines by the key whose text they are (i18n.TAGS, matched in every language by
    i18n.log_tag: 2.0.0 S4 names no key and holds no text here)."""
    if line.startswith('ERROR'):
        return 'error'
    if line.startswith('WARNING'):
        return 'warn'
    if _ENGINE_PREFIX.match(line) or 'Deleting original file' in line:
        return 'dim'
    return i18n.log_tag(line) or 'app'


class LogUI:
    def build_log_page(self):
        """Page header: [All | Errors]  Copy  ⋯ on the right (log.md 2); below it the log text fills the page."""
        page = self.page('logs', i18n.t('nav.logs'))
        page.grid_rowconfigure(1, weight=1)
        right = page.header.right
        self.errors_only = False
        self.log_menu = ui_theme.menu_button(right, self.log_menu_items, size='sm')
        self.log_menu.pack(side='right')
        copy = i18n.t('log.copy_all')
        self.log_copy_button = ui_theme.button(right, copy, self.copy_log, kind='ghost', size='sm',
                                               width=ui_theme.fitted_width(right, copy, 'sm', ghost=True,
                                                                           inset=ui_theme.INLINE))
        # (final polish) the gaps a reader sees run from ink to ink: Copy carries INLINE on each side of its word
        # and the 16 px icon of '⋯' sits 6 inside its 28 px box, so Copy touches '⋯' (8 + 6 = 14 between the
        # word and the icon) and the filter keeps SECTION (24 + 8 = 32): "look" and "do" read as two groups
        self.log_copy_button.pack(side='right')
        self.log_filter = ui_theme.segmented(right, ('all', 'errors'),
                                             {'all': i18n.t('log.filter.all'), 'errors': i18n.t('log.filter.errors')},
                                             command=lambda value: self.set_errors_only(value == 'errors'),
                                             height=ui_theme.CONTROL_H_SM)
        self.log_filter.set('all')
        self.log_filter.pack(side='right', padx=(0, ui_theme.SECTION))
        self.logs = ui_theme.textbox(page, state='disabled', wrap='word', font=ui_theme.font('caption'))
        self.logs.grid(row=1, column=0, sticky='nsew')
        self.log_empty = ui_theme.label(self.logs, i18n.t('log.no_errors'), role='caption',
                                        text_color=ui_theme.MUTED, fg_color=ui_theme.SURFACE_ALT)
        for widget in (self.logs, self.log_empty):        # right click: the mirror of ⋯
            ui_theme.bind_menu(widget, self.log_menu_items)
        self.refresh_log_colors()
        ui_theme.on_appearance(self.refresh_log_colors)
        # (final-fix-ui, final-e2e-ui-1) Whether the view follows new lines is the owner's choice,
        # made by scrolling: a wheel turn, a drag of the scrollbar, a key or a selection drag in the
        # box.  It is not read back from the view after every flush: a box that got shorter (the
        # window was restored from maximised) keeps its first line, so its end leaves the view
        # although nobody scrolled.
        self.log_follow = True
        inner = getattr(self.logs, '_textbox', self.logs)
        for sequence in ('<MouseWheel>', '<KeyRelease>', '<B1-Motion>', '<ButtonRelease-1>'):
            inner.bind(sequence, self._log_scrolled, add='+')
        bar = getattr(self.logs, '_y_scrollbar', None)
        for widget in (bar, getattr(bar, '_canvas', None)):
            if widget is not None:
                for sequence in ('<B1-Motion>', '<ButtonRelease-1>', '<MouseWheel>'):
                    tk.Misc.bind(widget, sequence, self._log_scrolled, add='+')
        tk.Misc.bind(inner, '<Configure>', self._log_resized, add='+')

    def _log_scrolled(self, _event=None):
        """After the owner's own scrolling (once Tk has moved the view): follow again only at the end."""
        try:
            self.after_idle(self._log_view_settled)
        except tk.TclError:
            pass

    def _log_view_settled(self):
        try:
            self.log_follow = self.logs.yview()[1] >= 0.999
        except tk.TclError:
            pass

    def _log_resized(self, _event=None):
        """A box that follows stays at the end when its size changes."""
        if self.log_follow:
            try:
                self.logs.see('end')
            except tk.TclError:
                pass

    def refresh_log_colors(self):
        """CTkTextbox.tag_config takes one colour, not a (light, dark) pair: resolved here, again on every mode change."""
        for name, token in TAGS.items():
            self.logs.tag_config(name, foreground=ui_theme.resolve(token))

    def append_log(self, line):
        # (1.8.0, MESSAGES.md 6.2 D-F47) Core lines arrive as messages and are shown (and written to
        # download.log) in the running language; UI lines are already shown text and raw engine
        # output is plain: both pass unchanged.  zh: the line itself.
        line = messages.show(line)
        proxy = self.proxy.get().strip() if hasattr(self, 'proxy') else ''
        if proxy:
            line = line.replace(proxy, i18n.t('core.proxy_mask'))
        self.logger.info(line)
        self.log_buffer.append(line)

    def flush_log(self):
        """Write buffered lines into the page (at most 200 per call); keeps the view at the end only
        when it already was there."""
        if not self.log_buffer:
            return
        lines, self.log_buffer[:] = self.log_buffer[:FLUSH_LIMIT], self.log_buffer[FLUSH_LIMIT:]
        follow = getattr(self, 'log_follow', True)
        self.logs.configure(state='normal')
        try:
            for line in lines:
                if _SLEEPING.match(line):
                    continue        # the engine's own sleep notices stay in the file only
                self.logs.insert('end', line + '\n', classify(line))
            if int(self.logs.index('end-1c').split('.')[0]) > MAX_LINES:
                self.logs.delete('1.0', f'{TRIM_LINES + 1}.0')
        finally:
            self.logs.configure(state='disabled')
        if follow:
            self.logs.see('end')
        if self.errors_only:
            self.refresh_log_empty()

    def set_errors_only(self, on):
        """Errors (True: only error and warn lines) or All (False); the same call twice changes nothing."""
        follow = getattr(self, 'log_follow', True)      # a reader at the end stays at the end
        self.errors_only = bool(on)
        for name in ('dim', 'app', 'ok'):
            self.logs.tag_config(name, elide=self.errors_only)
        self.log_filter.set('errors' if self.errors_only else 'all')     # set() runs no command
        self.refresh_log_empty()
        if follow:
            self.logs.see('end')

    def refresh_log_empty(self):
        """'No errors' in the middle of the text while Errors is chosen and there is no error or warn line."""
        inner = self.logs._textbox
        empty = self.errors_only and not (inner.tag_ranges('error') or inner.tag_ranges('warn'))
        if empty:
            self.log_empty.place(relx=.5, rely=.5, anchor='center')
        else:
            self.log_empty.place_forget()

    def log_menu_items(self):
        """The '⋯' of the page header and the right-click menu of the text (00-system 2.10)."""
        return [ui_theme.MenuItem(i18n.t('log.copy_all'), self.copy_log),
                ui_theme.MenuItem(i18n.t('log.open_file'), self.open_log_file),
                ui_theme.SEPARATOR,
                ui_theme.MenuItem(i18n.t('log.clear_view'), self.clear_log_view,
                                  enabled=bool(self.logs.get('1.0', 'end-1c')))]

    def copy_log(self):
        """All the lines, also the ones Errors hides."""
        self.clipboard_clear()
        self.clipboard_append(self.logs.get('1.0', 'end-1c'))

    def open_log_file(self):
        try:
            os.startfile(paths.log_file())
        except OSError as error:
            messagebox.showwarning(i18n.t('dialog.open_log_failed'), messages.error_text(error), parent=self)

    def clear_log_view(self):
        self.logs.configure(state='normal')
        self.logs.delete('1.0', 'end')
        self.logs.configure(state='disabled')
        self.log_follow = True
        self.refresh_log_empty()
