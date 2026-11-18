"""Log page of StudioApp (mixin LogUI; 1.6.0 WP4).

append_log() only buffers (and writes the log file); poll() calls flush_log(), which writes at
most 200 lines per tick into the tagged text box.  Never import studio here at module level.
"""
from __future__ import annotations

import functools
import os
import re
import string
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import i18n
import paths
import ui_theme

MAX_LINES = 1500
TRIM_LINES = 300
FLUSH_LIMIT = 200
TAGS = {'error': ui_theme.DANGER, 'warn': ui_theme.WARN, 'ok': ui_theme.OK, 'dim': ui_theme.MUTED, 'app': ui_theme.INK}
_ENGINE_PREFIX = re.compile(r'^\[[\w:]+\]')
_SLEEPING = re.compile(r'^\[[\w:]+\] Sleeping [\d.]+ seconds')
_ERROR_STARTS = ('此链接未完成', '此链接无法开始', '更新失败', '网页采集未完成', '订阅检查失败', '界面错误', '设置未保存',
                 '错误：', '无法启动下载引擎')
_OK_STARTS = ('文件已保存', '下载完成')
# (1.7.0, I18N.md 8.6) The header of a job that left links unfinished shares its start with the
# header after 清除未完成 ('任务结束 · 1 条成功'): zh keeps the 1.6.0 rule of classify, the other
# languages look for the template's longest literal part after its start (en 'Unfinished:').
_FINISHED_UNFINISHED = 'download.title.finished_unfinished'


def _literals(text):
    try:
        return [literal for literal, _field, _spec, _conversion in string.Formatter().parse(text)]
    except ValueError:
        return [text]


@functools.lru_cache(maxsize=1)
def _starts():
    """(error starts, ok starts, ((start, part), ...) of the unfinished header in the other
    languages): the 1.6.0 Chinese starts extended with i18n.prefixes() of the tagged keys."""
    headers = i18n.all_values(_FINISHED_UNFINISHED)
    shared = {_literals(text)[0] for text in headers}
    errors = _ERROR_STARTS + tuple(start for start in i18n.prefixes('error') if start not in shared)
    oks = _OK_STARTS + tuple(i18n.prefixes('ok'))
    special = []
    for text in headers[1:]:
        parts = [part for part in _literals(text) if part]
        if len(parts) > 1:
            special.append((parts[0], max(parts[1:], key=len).strip()))
    return errors, oks, tuple(special)


def classify(line):
    """Tag of one log line: error, warn, dim, ok or app (WP4.md section G, in this order)."""
    if line.startswith('ERROR'):
        return 'error'
    if line.startswith('WARNING'):
        return 'warn'
    if _ENGINE_PREFIX.match(line) or 'Deleting original file' in line:
        return 'dim'
    errors, oks, special = _starts()
    if line.startswith(errors) or (line.startswith('任务结束') and '未完成' in line):
        return 'error'
    if any(line.startswith(start) and part in line[len(start):] for start, part in special):
        return 'error'
    if line.startswith(oks):
        return 'ok'
    return 'app'


class LogUI:
    def build_log_page(self):
        page = self.page('logs', i18n.t('nav.logs'), i18n.t('log.subtitle'))
        page.grid_rowconfigure(1, weight=1)
        card = self.card(page)
        card.grid(row=1, column=0, sticky='nsew')
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(card, fg_color='transparent', corner_radius=0)
        header.grid(row=0, column=0, sticky='ew', padx=ui_theme.CARD_PAD, pady=(12, 8))
        header.grid_columnconfigure(0, weight=1)
        ui_theme.label(header, i18n.t('log.heading'), role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        self.errors_only = False
        # (1.7.0, I18N.md 10.1, 10.4) A button is as wide as its label needs (never below the 72 px
        # of 1.6.0); the errors button fits both of its labels, so it keeps its width when it toggles.
        errors_width = max(ui_theme.fitted_width(header, i18n.t('log.errors_only'), 'sm'),
                           ui_theme.fitted_width(header, i18n.t('log.show_all'), 'sm'))
        self.errors_button = ui_theme.button(header, i18n.t('log.errors_only'), self.toggle_errors, kind='ghost',
                                             size='sm', width=errors_width)
        self.errors_button.grid(row=0, column=1, padx=(4, 0))
        for column, (text, command) in enumerate(((i18n.t('log.copy_all'), self.copy_log),
                                                  (i18n.t('log.open_file'), self.open_log_file),
                                                  (i18n.t('log.clear_view'), self.clear_log_view)), 2):
            ui_theme.button(header, text, command, kind='ghost', size='sm',
                            width=ui_theme.fitted_width(header, text, 'sm')).grid(row=0, column=column, padx=(4, 0))
        self.logs = ui_theme.textbox(card, state='disabled', wrap='word', font=ui_theme.font('caption'))
        self.logs.grid(row=1, column=0, sticky='nsew', padx=ui_theme.CARD_PAD, pady=(0, ui_theme.CARD_PAD))
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
        # (1.7.0, I18N.md 8.6) Core lines arrive in canonical Chinese and are shown (and written to
        # download.log) in the running language; UI lines are already localized and raw engine
        # output is unknown to localize: both pass unchanged.  zh: the line itself.
        line = i18n.localize(str(line))
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

    def toggle_errors(self):
        follow = getattr(self, 'log_follow', True)      # a reader at the end stays at the end
        self.errors_only = not self.errors_only
        for name in ('dim', 'app', 'ok'):
            self.logs.tag_config(name, elide=self.errors_only)
        self.errors_button.configure(text=i18n.t('log.show_all') if self.errors_only else i18n.t('log.errors_only'))
        if follow:
            self.logs.see('end')

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.logs.get('1.0', 'end-1c'))

    def open_log_file(self):
        try:
            os.startfile(paths.log_file())
        except OSError as error:
            messagebox.showwarning(i18n.t('dialog.open_log_failed'), str(error), parent=self)

    def clear_log_view(self):
        self.logs.configure(state='normal')
        self.logs.delete('1.0', 'end')
        self.logs.configure(state='disabled')
        self.log_follow = True
