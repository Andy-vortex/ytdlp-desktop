"""Page collection workspace for the existing desktop downloader (mixin CollectionUI; 1.6.0 WP5).

Layout (WP5.md section B): page header, input card (start pages, the three options and the
CSS scope, actions in the card header), results card (count, filter and search in its header,
a one-line status, the table, the footer with the primary action at the far right).
Every colour, font and widget comes from ui_theme.  Never import studio here at module level.

(1.7.0) Every shown text through i18n (I18N.md 8); the Tk variables keep their 1.6.0 values
(collect mode and filter), the link kinds and the stored subscription errors stay canonical Chinese.

(1.8.0, MESSAGES.md 6) The collector's texts and the stored titles arrive as messages (or raw
user data) and are shown with messages.show / messages.error_text; no key-prefix list is left.
"""
from pathlib import Path
from dataclasses import asdict
import os
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from urllib.parse import quote, urlsplit, urlunsplit

import customtkinter as ctk
import applog
import i18n
import messages
import ui_theme
import urls as links
from collector import CollectOptions, CollectionRunner, MAX_LINKS, browser_available
from engine import parse_urls
from library import is_busy, ui_wait
from library_ui import LIBRARY_RETRY_MS, cell_width, columns, pin_rows, text_button_width

# (1.7.0 U1) A collection start that met a library locked by another program waits for it.
COLLECT_WAIT = 120          # s: then the start is given up (status line and log)
COLLECT_WAITING = '资料库正被其他程序占用，可以写入后自动开始采集…'
COLLECT_GAVE_UP = '采集未开始：资料库仍被其他程序占用，请稍后再试。'
COLLECT_DROPPED = '采集未开始：等待资料库期间开始了其他任务。'


def subscription_result(record, cancelled):
    """(pages, successful, error) of a subscription check for Library.finish_subscription, from
    the collection job's record."""
    successful = not cancelled and record['status'] == 'completed'
    # (1.8.0, C-F39) The last two errors as one clauses message (stored: the 1.7.0 '；' join of the
    # zh texts), else why the check ended; the library page shows it with messages.show.
    error = messages.clauses(record['data']['errors'][-2:]) or \
        messages.msg('value.subscription.stopped' if cancelled else 'value.subscription.unfinished')
    return record['pages'], successful, error

MODE_STATIC = '普通网页'
MODE_DYNAMIC = '动态网页（Chrome）'
FILTERS = ('媒体候选', '全部链接')
STATUS_IDLE = '支持普通页面和动态页面；采集结果供你选择，不会自动下载。'
NOTE_PAGES = '翻页只跟随同站的 rel=next 链接。'
NOTE_DYNAMIC = '动态模式使用独立的临时 Chrome，不读取现有浏览器的登录状态。'
# (1.7.0) The stored link kind the media filter leaves out (collector.py; library.sqlite3); a
# compared stored value, not a message (MESSAGES.md 6.3).
WEB_LINK = i18n.all_values('value.link_kind.web')[0]
# Polish ui-daily-flow-5: the links go into the download page's link box (nothing starts or joins
# a running queue), so the button says so; 加入队列 is the download page's word for a running job.
ADD_TEXT = '添加到下载列表'
SUBTITLE = '从网页发现音视频链接，筛选后添加到下载列表；也可保存网页正文。'
NARROW_OPTIONS = 700        # logical px: below this the CSS entry gets a row of its own
BACKGROUND_NOTE = '正在后台检查订阅“{name}”，结果会保存到资料管理 → 增量订阅；完成后可继续采集。'
# Characters percent-encoding may leave as they are, tried from the gentlest set to the strictest:
# a collected link must reach the download box whole (see queueable_url).  The last set also
# encodes ':' (final-fix-ui, final-privacy-security-1): a second link inside the path, query or
# fragment ('?a=1|https://…', '%7Chttps://…', '/go;https://…') becomes 'https%3A//…', which the
# box's parser no longer splits off.
_URL_SAFE = (":/?#@!$&*+,;=%~-._", "/?#@&=%~-._:", "/?#@&=%~-._")


def queueable_url(url):
    """``url`` written so that the link parser of the download box (urls.extract_urls) takes it whole.

    The parser stops at full-width brackets, quotes and spaces and trims closing punctuation,
    so a collected link such as ``.../第1集（高清）.mp4`` or ``.../Song_(live)`` would be queued cut
    short. Such a link gets its path, query and fragment percent-encoded (the same address for
    the site); a link the parser already takes whole is returned unchanged.
    """
    if links.extract_urls(url) == [url]:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    for safe in _URL_SAFE:
        candidate = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe=safe),
                                quote(parts.query, safe=safe), quote(parts.fragment, safe=safe)))
        if candidate and candidate[-1] in links.TRAILING:
            candidate = candidate[:-1] + '%{:02X}'.format(ord(candidate[-1]))
        if links.extract_urls(candidate) == [candidate]:
            return candidate
    return url


class CollectionUI:
    def build_collection_page(self):
        page = self.page('collect', i18n.t('nav.collect'), i18n.t('collect.subtitle'))
        page.grid_rowconfigure(2, weight=1)
        # The page's state containers are created by StudioApp.init_collection() (studio.py).
        pad = ui_theme.CARD_PAD
        muted = ui_theme.MUTED

        # ------------------------------------------------ input card
        inputs = ui_theme.card(page)
        inputs.grid(row=1, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        inputs.grid_columnconfigure(0, weight=1)
        self.collect_card = inputs
        top = ctk.CTkFrame(inputs, fg_color='transparent', corner_radius=0)
        top.grid(row=0, column=0, sticky='ew', padx=pad, pady=(12, 8))
        top.grid_columnconfigure(2, weight=1)
        ui_theme.label(top, i18n.t('collect.start_pages'), role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        ui_theme.label(top, i18n.t('collect.start_pages.hint'), role='caption', text_color=muted,
                       anchor='w').grid(row=0, column=1, sticky='w', padx=(10, 0), pady=(3, 0))
        subscribe_text = i18n.t('collect.save_subscription')
        subscribe = ui_theme.button(top, subscribe_text, self.save_subscription,
                                    width=max(72, text_button_width(top, subscribe_text)))
        subscribe.grid(row=0, column=3, padx=(8, 0))
        stop_text = i18n.t('common.stop')
        self.collect_stop = ui_theme.button(top, stop_text, self.stop, kind='danger',
                                            width=max(64, text_button_width(top, stop_text)), state='disabled')
        self.collect_stop.grid(row=0, column=4, padx=(8, 0))
        # (I18N.md 10.1) the text, the icon (16) and customtkinter's 6 px between them
        start_text = i18n.t('collect.start')
        self.collect_start = ui_theme.button(top, start_text, self.start_collection, kind='primary',
                                             width=max(112, text_button_width(top, start_text) + 22),
                                             image=ui_theme.icon(ui_theme.ICON_SEARCH, 'white'), compound='left')
        self.collect_start.grid(row=0, column=5, padx=(8, 0))

        self.collect_urls = ui_theme.textbox(inputs, height=56, wrap='word')
        self.collect_urls.grid(row=1, column=0, sticky='ew', padx=pad)
        self._collect_hint_shown = False
        self.collect_placeholder = ui_theme.label(self.collect_urls, i18n.t('collect.placeholder'), text_color=ui_theme.FAINT,
                                                  fg_color=ui_theme.SURFACE_ALT, anchor='w', cursor='xterm')
        self.collect_placeholder.bind('<Button-1>', self._collect_placeholder_clicked)
        self.collect_urls.bind('<KeyRelease>', lambda _event: self.sync_collect_placeholder())
        self.collect_urls.bind('<<Modified>>', self._collect_urls_modified)
        self.collect_urls.bind('<Control-Return>', self._collect_from_box)     # like the download link box
        self._collect_box_dimmed = False
        self.sync_collect_placeholder()

        options = ctk.CTkFrame(inputs, fg_color='transparent', corner_radius=0)
        options.grid(row=2, column=0, sticky='ew', padx=pad, pady=(10, 0))
        options.grid_columnconfigure(6, weight=1)
        self.collect_options_row = options
        ui_theme.label(options, i18n.t('collect.option.mode'), role='caption', text_color=muted).grid(row=0, column=0, sticky='w')
        # (1.7.0) The variable keeps MODE_STATIC / MODE_DYNAMIC (compared); the menu shows the labels.
        mode_labels = [i18n.label('collect_mode', value) for value in (MODE_STATIC, MODE_DYNAMIC)]
        mode = ui_theme.option_menu(options, (MODE_STATIC, MODE_DYNAMIC), labels=mode_labels, variable=self.collect_mode,
                                    width=max(170, ui_theme.menu_width(options, mode_labels)))
        mode.grid(row=0, column=1, sticky='w', padx=(8, 16))
        ui_theme.label(options, i18n.t('collect.option.scrolls'), role='caption', text_color=muted).grid(row=0, column=2, sticky='w')
        scroll = ui_theme.option_menu(options, [str(n) for n in range(6)], variable=self.collect_scrolls, width=64)
        scroll.grid(row=0, column=3, sticky='w', padx=(8, 16))
        ui_theme.label(options, i18n.t('collect.option.pages'), role='caption', text_color=muted).grid(row=0, column=4, sticky='w')
        limit = ui_theme.option_menu(options, [str(n) for n in range(1, 6)], variable=self.collect_pages_limit,
                                     width=64)
        limit.grid(row=0, column=5, sticky='w', padx=(8, 0))
        scope = ui_theme.entry(options, textvariable=self.collect_selector, placeholder=i18n.t('collect.option.selector'))
        self.collect_scope = scope
        self._collect_narrow = None
        self._collect_layout_options(False)
        # On the outer frame: customtkinter's bind() would go to the inner canvas (see ui_theme.fit).
        tk.Misc.bind(options, '<Configure>', lambda _event: self._collect_layout_options(), add='+')

        self.collect_note = ui_theme.label(inputs, '', role='caption', text_color=muted, anchor='w')
        self.collect_note.grid(row=3, column=0, sticky='ew', padx=pad, pady=(8, 14))
        self.refresh_collect_note()
        self.collect_mode.trace_add('write', lambda *_: self.refresh_collect_note())
        self.controls += [self.collect_urls, mode, scroll, limit, scope]
        self.controls.append(subscribe)
        self.controls.append(self.collect_start)

        # ------------------------------------------------ results card
        result = ui_theme.card(page)
        result.grid(row=2, column=0, sticky='nsew')
        result.grid_columnconfigure(0, weight=1)
        result.grid_rowconfigure(2, weight=1)
        self.collect_results = result
        head = ctk.CTkFrame(result, fg_color='transparent', corner_radius=0)
        head.grid(row=0, column=0, sticky='ew', padx=pad, pady=(12, 0))
        head.grid_columnconfigure(2, weight=1)
        ui_theme.label(head, i18n.t('collect.found'), role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        self.collect_count = ui_theme.chip(head, i18n.t('collect.count', shown=0, total=0, selected=0), 'idle')
        self.collect_count.grid(row=0, column=1, sticky='w', padx=(10, 0))
        filter_labels = [i18n.label('collect_filter', value) for value in FILTERS]
        ui_theme.option_menu(head, FILTERS, labels=filter_labels, variable=self.collect_filter,
                             width=max(120, ui_theme.menu_width(head, filter_labels))).grid(row=0, column=3, padx=(12, 0))
        self.collect_search_entry = ui_theme.entry(head, textvariable=self.collect_search,
                                                   placeholder=i18n.t('collect.filter.placeholder'), width=220)
        self.collect_search_entry.grid(row=0, column=4, padx=(8, 0))
        self.collect_status = ui_theme.label(result, '', role='caption', text_color=muted, anchor='w')
        self.collect_status.grid(row=1, column=0, sticky='ew', padx=pad, pady=(6, 8))
        ui_theme.fit(self.collect_status, i18n.t('collect.status.idle'))
        # (1.7.0, I18N.md 10.1) The Type column shows the link-kind labels: as wide as the widest needs.
        # (final fix round 1, ENV-3) Measured with the room AppTable's cut keeps beside a cell (20 px
        # rather than 16), so 动态页面媒体请求 / Page request are shown in full.
        kind_width = cell_width(result, [text for _value, text in i18n.choices('link_kind')], 88)
        self.collect_tree = ui_theme.table(result, columns(result, [
            ('kind', i18n.t('table.type'), kind_width, False), ('title', i18n.t('table.title'), 260, True),
            ('url', i18n.t('table.link'), 360, True)]),
                                           selectmode='extended', empty=i18n.t('collect.empty'))
        self.collect_tree.container.grid(row=2, column=0, sticky='nsew', padx=pad)
        pin_rows(self.collect_tree, 4)     # the page's request stays the same once the scrollbar appears
        self.collect_tree.ellipsize(middle=('url',))     # cut with '…'; links keep their distinguishing end
        self.collect_tree.bind('<<TreeviewSelect>>', lambda _: self.collection_selection_changed())
        footer = ctk.CTkFrame(result, fg_color='transparent', corner_radius=0)
        footer.grid(row=3, column=0, sticky='ew', padx=pad, pady=(10, 14))
        footer.grid_columnconfigure(3, weight=1)
        ui_theme.button(footer, i18n.t('collect.select_shown'),
                        lambda: self.collect_tree.selection_set(self.collect_tree.get_children()),
                        kind='ghost').grid(row=0, column=0, sticky='w')
        self.collect_export = ui_theme.button(footer, i18n.t('collect.export_links'), self.export_collected_links,
                                              state='disabled')
        self.collect_export.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.collect_text = ui_theme.button(footer, i18n.t('collect.save_text'), self.export_collected_text,
                                            state='disabled')
        self.collect_text.grid(row=0, column=2, sticky='w', padx=(8, 0))
        # (I18N.md 10.1) wide enough for the longest count suffix, so the button never changes width
        self.collect_add = ui_theme.button(footer, i18n.t('collect.add'), self.add_collected, kind='primary',
                                           width=max(132, text_button_width(footer, self.add_text(9999))),
                                           state='disabled')
        self.collect_add.grid(row=0, column=4, sticky='e')
        self.controls.append(self.collect_add)
        self.collect_filter.trace_add('write', lambda *_: self.render_collection_links())
        self.collect_search.trace_add('write', lambda *_: self.render_collection_links())
        self.render_collection_links()

    # ------------------------------------------------------------ input card helpers
    def _collect_placeholder_clicked(self, _event=None):
        self.collect_urls.focus_set()
        return 'break'

    def _collect_from_box(self, _event=None):
        """Ctrl+Enter in the start-URL box starts the collection instead of adding a line."""
        self.start_collection()
        return 'break'

    def _dim_collect_box(self, dimmed):
        """While a task runs the start-URL box is disabled; it also looks so (as the download link box)."""
        if dimmed == self._collect_box_dimmed:
            return
        self._collect_box_dimmed = dimmed
        fill = ui_theme.SURFACE if dimmed else ui_theme.SURFACE_ALT
        self.collect_urls.configure(text_color=ui_theme.FAINT if dimmed else ui_theme.INK, fg_color=fill)
        self.collect_placeholder.configure(fg_color=fill)

    def _collect_urls_modified(self, _event=None):
        try:
            self.collect_urls.edit_modified(False)
        except tk.TclError:
            pass
        self.sync_collect_placeholder()

    def sync_collect_placeholder(self):
        """The faint example stays while the start-URL box is empty, focused or not."""
        try:
            empty = not self.collect_urls.get('1.0', 'end-1c')
            if empty and not self._collect_hint_shown:
                self.collect_placeholder.place(x=10, y=6)
            elif not empty and self._collect_hint_shown:
                self.collect_placeholder.place_forget()
            self._collect_hint_shown = empty
        except tk.TclError:
            pass

    def _collect_layout_options(self, narrow=None):
        """The CSS entry stretches beside the three menus; on a row narrower than 700 px it moves below them."""
        row = self.collect_options_row
        if narrow is None:
            try:
                width = row.winfo_width()
            except tk.TclError:
                return
            if width <= 1:
                return
            narrow = width / ctk.ScalingTracker.get_widget_scaling(row) < NARROW_OPTIONS
        if narrow == self._collect_narrow:
            return
        self._collect_narrow = narrow
        if narrow:
            self.collect_scope.grid(row=1, column=0, columnspan=7, sticky='ew', padx=0, pady=(8, 0))
        else:
            self.collect_scope.grid(row=0, column=6, columnspan=1, sticky='ew', padx=(16, 0), pady=0)

    def refresh_collect_note(self, background=None):
        """The caption under the options: the paging rule, or (``background`` = a subscription name)
        why the controls are disabled while an automatic subscription check runs (robust-13)."""
        if background is not None:
            self.collect_note.configure(text_color=ui_theme.ACCENT_TEXT)
            ui_theme.fit(self.collect_note, i18n.t('collect.background', name=background))
            return
        text = i18n.t('collect.note.pages')
        if self.collect_mode.get() == MODE_DYNAMIC:
            # Two sentences of one line (I18N.md 8.2): zh joins them directly, en with a space.
            text += i18n.t('common.sentence_join') + i18n.t('collect.note.dynamic')
        self.collect_note.configure(text_color=ui_theme.MUTED)
        ui_theme.fit(self.collect_note, text)

    def set_collect_status(self, text, tone=None):
        """The one-line status of the results card; tone idle (MUTED), running, warn or ok."""
        colors = {'idle': ui_theme.MUTED, 'running': ui_theme.ACCENT_TEXT, 'warn': ui_theme.WARN, 'ok': ui_theme.OK}
        if tone is None:
            self.collect_status.configure(text=text)
        else:
            self.collect_status.configure(text=text, text_color=colors[tone])

    # ------------------------------------------------------------ collection
    def start_collection(self):
        if self.busy:
            return
        try:
            options = self.collection_options()
        except ValueError as error:
            messagebox.showwarning(i18n.t('dialog.collect.check_settings'), messages.error_text(error), parent=self)
            return
        self.active_subscription = None
        self.launch_collection(options)

    def collection_options(self):
        return CollectOptions(parse_urls(self.collect_urls.get('1.0', 'end-1c')),
            self.collect_mode.get() != MODE_STATIC, int(self.collect_scrolls.get()),
            self.collect_selector.get().strip(), int(self.collect_pages_limit.get()), self.proxy.get().strip()).validate()

    def launch_collection(self, options, job_id=None, title=None, background=False):
        """Start a collection.  ``background`` (automatic subscription checks) leaves the results,
        the status line, the start-URL box and the text-export button of the collect page alone.

        Returns False when the history record could not be written (final-fix-ui,
        final-architecture-2): nothing starts, the results shown stay, and the owner gets a dialog
        and a status line.  A background check re-raises instead: check_subscription logs it and
        backs off."""
        fresh = not job_id
        self.collect_pending = None
        try:
            if fresh:
                # (1.8.0) Untitled: the first start link, user data (MESSAGES.md 3.3).
                job_id = self.library.create_job('collect', title or messages.raw(options.urls[0]), asdict(options),
                                                 self.active_subscription)
            else:
                self.library.change_job(job_id, status='running', errors=[], owner=os.getpid(), worker=None)
        except (sqlite3.Error, OSError, ValueError) as error:
            if background:
                raise
            if is_busy(error):
                # (1.7.0 U1) Another program holds the library: no dialog and no wait.  The collector
                # needs the record before it starts, so the start is tried again by itself
                # (retry_collection) until the record can be written or COLLECT_WAIT has passed.
                self.collect_pending = (options, None if fresh else job_id, title, self.active_subscription,
                                        time.monotonic() + COLLECT_WAIT)
                self.active_subscription = None
                self.set_collect_status(i18n.t('collect.status.waiting_library'), 'running')
                self.library_busy(error)
                if self.collect_retry_after is not None:
                    try:
                        self.after_cancel(self.collect_retry_after)
                    except (tk.TclError, ValueError):
                        pass
                try:
                    self.collect_retry_after = self.after(LIBRARY_RETRY_MS, self.retry_collection)
                except tk.TclError:
                    self.collect_retry_after = None
                return False
            applog.get('ui').exception('collection history could not be written')
            self.active_subscription = None
            # (final fix round 2, CR2-3; I18N.md 8.4) A core exception's text in the running language.
            self.append_log(i18n.t('log.collect.not_started', error=messages.error_text(error)))
            self.set_collect_status(i18n.t('collect.status.not_started'), 'warn')
            messagebox.showwarning(i18n.t('dialog.collect.not_started.title'),
                                   i18n.t('dialog.collect.not_started.text', error=messages.error_text(error)),
                                   parent=self)
            return False
        if fresh and not background:
            # Only now that the job exists: a start that failed keeps the results on screen.
            self.collected_links.clear()
            self.collected_pages.clear()
        self.collection_job = job_id
        self.collection_background = bool(background)
        options.job_id = job_id
        options.history_path = str(self.library.path)
        self.collection_errors = 0
        if not background:
            self.render_collection_links()
            self.collect_text.configure(state='disabled')
        self.operation = 'collect'
        try:
            # Inside the try: if set_busy raised, the except below still releases the window.
            self.set_busy(True)
            self.collect_stop.configure(state='normal')
            if not background:
                self.set_collect_status(i18n.t('collect.status.preparing'), 'running')
            # Activity start (WP4 section C): a token, an emit closure that drops the events of an
            # older activity, and the registered worker thread the watchdog may judge.
            self.run_token += 1
            token = self.run_token
            self.runner = CollectionRunner(lambda kind, value: token == self.run_token and self.events.put((kind, value)))
            thread = threading.Thread(target=self.runner.run, args=(options,), daemon=True)
            self.worker = thread
            self.worker_operation = 'collect'
            thread.start()
            return True
        except Exception:
            applog.get('ui').exception('collection could not start')
            self.run_token += 1
            self.collection_job = None
            self.collection_background = False
            self.set_busy(False)
            self.collect_stop.configure(state='disabled')
            raise

    def retry_collection(self):
        """(1.7.0 U1) Try a collection start again that waited for a locked library."""
        self.collect_retry_after = None
        pending, self.collect_pending = self.collect_pending, None
        if pending is None or self.closing:
            return
        options, job_id, title, subscription, deadline = pending
        if self.busy:
            self.append_log(i18n.t('collect.status.library_dropped'))
            self.set_collect_status(i18n.t('collect.status.library_dropped'), 'warn')
            return
        if time.monotonic() >= deadline:
            self.append_log(i18n.t('collect.status.library_gave_up'))
            self.set_collect_status(i18n.t('collect.status.library_gave_up'), 'warn')
            return
        self.active_subscription = subscription
        self.launch_collection(options, job_id, title)

    def _subscription_task(self, job, subscription, cancelled):
        """The bookkeeping of a subscription check, for the HistoryWriter (1.7.0 U1)."""
        library, events = self.library, self.events

        def task():
            record = library.job(job)
            new = library.finish_subscription(subscription, *subscription_result(record, cancelled))
            events.put(('log', i18n.t('log.subscription.new', new=new)))
        return task

    def handle_collection(self, kind, value):
        background = getattr(self, 'collection_background', False)
        if kind == 'collect_status':
            if not background:
                self.set_collect_status(messages.show(value))       # a collector message (MESSAGES.md 6.2)
        elif kind == 'collect_page' and background:
            # An automatic check: the worker has stored the page; the collect page keeps what it shows.
            self.append_log(i18n.t('log.collect.page', title=messages.show(value['title']),
                                   count=len(value['links'])))
            if value.get('text_truncated'):
                self.append_log(i18n.t('collect.truncated'))
        elif kind == 'collect_page':
            value = {k:v for k,v in value.items() if k != '_checkpoint'}
            self.collected_pages.append(value)
            for link in value['links']:
                if len(self.collected_links) < MAX_LINKS:
                    self.collected_links.setdefault(link['url'], link)
            self.render_collection_links()
            self.set_collect_status(i18n.t('collect.status.progress', pages=len(self.collected_pages),
                                           links=len(self.collected_links)))
            self.append_log(i18n.t('log.collect.page', title=messages.show(value['title']),
                                   count=len(value['links'])))
            if value.get('text_truncated'):
                self.append_log(i18n.t('collect.truncated'))
        elif kind == 'collect_error':
            self.collection_errors += 1
            shown = messages.show(value)        # a collector message (MESSAGES.md 6.2)
            self.append_log(i18n.t('log.collect.unfinished', error=shown))
            if not background:
                # One line, cut by pixels; ui_theme.full_text(collect_status) keeps the whole message.
                self.set_collect_status(i18n.t('collect.status.partial', error=shown), 'warn')
        elif kind == 'collect_done':
            # The bookkeeping may fail (a locked or broken history): the window is released anyway (arch-3 a).
            record = None
            job, subscription = self.collection_job, self.active_subscription
            try:
                if job:
                    # (1.7.0 fix round 1) One try: a busy result is handled calmly below.
                    with ui_wait(0):
                        record = self.library.job(job)
                    if subscription:
                        new = self.library.finish_subscription(subscription, *subscription_result(record, value['cancelled']))
                        self.append_log(i18n.t('log.subscription.new', new=new))
            except Exception as error:
                if is_busy(error):
                    # (1.7.0 U1) Another program holds the library: the pages are stored already (the
                    # collector wrote them); the subscription's bookkeeping is done by the
                    # HistoryWriter once the lock is gone, and the results on screen stay.
                    if job and subscription:
                        self.history_writer.defer(self._subscription_task(job, subscription, value['cancelled']))
                    self.library_busy(error)
                else:
                    applog.get('ui').exception('collection bookkeeping failed')
                    self.append_log(i18n.t('log.collect.record_failed', error=messages.error_text(error)))
            finally:
                self.collection_job = None
                self.active_subscription = None
                self.collection_background = False
                self.set_busy(False)
                self.collect_stop.configure(state='disabled')
            if background:
                self.set_hint('')
                self.refresh_collect_note()
                self.refresh_library()
                return
            if record is not None:
                # After set_busy(False): a disabled start-URL box ignores delete/insert, and a manual
                # subscription check must show its own start pages next to its own settings.
                try:
                    self.load_collection_record(record)
                except Exception as error:
                    applog.get('ui').exception('loading the collection record failed')
                    self.append_log(i18n.t('log.collect.load_failed', error=messages.error_text(error)))
            self.collect_text.configure(state='normal' if any(p['text'] for p in self.collected_pages) else 'disabled')
            self.collection_selection_changed()
            status = i18n.t('collect.status.stopped') if value['cancelled'] else i18n.t('collect.status.finished')
            # One style for numbers (polish ui-wording-consistency-4): '3 页', the running status's spacing.
            suffix = i18n.t('collect.status.errors_suffix', errors=self.collection_errors) if self.collection_errors else ''
            if len(self.collected_links) >= MAX_LINKS:
                suffix += i18n.t('collect.status.limit_suffix', limit=MAX_LINKS)
            tone = 'warn' if self.collection_errors else ('idle' if value['cancelled'] else 'ok')
            self.set_collect_status(i18n.t('collect.status.summary', status=status, pages=len(self.collected_pages),
                                           links=len(self.collected_links), suffix=suffix), tone)
            self.refresh_library()

    def render_collection_links(self):
        term = self.collect_search.get().casefold()
        shown = []
        for item in self.collected_links.values():
            if self.collect_filter.get() == FILTERS[0] and item['kind'] == WEB_LINK:
                continue
            # (final fix round 2, CR2-1) The search also finds the title as shown (zh: the same text);
            # (1.8.0, MESSAGES.md 6.1) the stored text through messages.zh, the shown one through show.
            shown_title = messages.show(item['title'])
            if term and term not in (item['url']+' '+messages.zh(item['title'])+' '+shown_title).casefold():
                continue
            shown.append(item)
        self.visible_collection_links = shown
        # The iids are positions in the new list: an old selection would point at other links.
        self.collect_tree.selection_remove(self.collect_tree.selection())
        # (1.7.0) The stored link kind and the title (a message or user data) in the running language.
        self.collect_tree.fill((str(index), (i18n.label('link_kind', item['kind']),
                                             messages.show(item['title']), item['url']))
                               for index, item in enumerate(shown))
        self.collection_selection_changed()

    def collection_selection_changed(self):
        count = len(self.collect_tree.selection())
        total = len(self.collect_tree.get_children())
        self.collect_count.set(i18n.t('collect.count', shown=total, total=len(self.collected_links), selected=count))
        # (1.7.0 U6) set_busy calls this method on every busy switch: a control is configured only
        # when its text or state changes (each customtkinter redraw can flush the window's layout).
        text = self.add_text(count)
        state = 'normal' if count and self.can_add_collected() else 'disabled'
        if self.collect_add.cget('text') != text or self.collect_add.cget('state') != state:
            self.collect_add.configure(text=text, state=state)
        state = 'normal' if count else 'disabled'
        if self.collect_export.cget('state') != state:
            self.collect_export.configure(state=state)
        # The start-URL box's disabled look: at once on the collect page or for a collection; for a
        # download started on another page it waits until the collect page is shown (show_page),
        # because the text box's redraw lays out the whole window again (about 0.2 s right after
        # 开始下载 with 300 links).
        if getattr(self, 'current_page', 'collect') == 'collect' or self.operation == 'collect':
            self._dim_collect_box(bool(self.busy))

    @staticmethod
    def add_text(count):
        """添加到下载列表, with the number of selected links when there are any."""
        return f"{i18n.t('collect.add')} ({count})" if count else i18n.t('collect.add')

    def selected_collection_links(self):
        return [self.visible_collection_links[int(i)] for i in self.collect_tree.selection()]

    def can_add_collected(self):
        """Links may go into the download box when the window is idle, and also while a download
        runs (the download page accepts links then: its button hands them to the running job).
        Not while a collection or an engine update runs (polish ui-daily-flow-5)."""
        return not self.busy or self.operation == 'download'

    def add_collected(self):
        if not self.can_add_collected():
            return
        items = self.selected_collection_links()
        self.queue_collected_items(items)

    def queue_collected_items(self, items):
        if not self.can_add_collected():
            return
        if not items:
            return
        # A link the box's parser would cut short goes in percent-encoded (queueable_url).  One that
        # still would not reach the box whole is left out with a log line, never queued in pieces.
        kept, queued = [], []
        for item in items:
            url = queueable_url(item['url'])
            if links.extract_urls(url) == [url]:
                kept.append(item)
                queued.append(url)
            else:
                self.append_log(i18n.t('log.collect.link_skipped', url=item['url']))
        items = kept
        if not items:
            return
        added = self.add_links('\n'.join(queued), source='collect')
        # Keys are the links as queued; read_form() also finds them through the origin of a cleaned link.
        self.download_referers.update({url: i['source'] for url, i in zip(queued, items) if i['url'] != i['source']})
        self.show_page('download')
        self.append_log(i18n.t('log.collect.queued', added=added, skipped=len(items) - added))

    def export_collected_links(self):
        items = self.selected_collection_links()
        if not items:
            return
        path = filedialog.asksaveasfilename(parent=self, title=i18n.t('dialog.export_links.title'), defaultextension='.txt',
                                            filetypes=[(i18n.t('dialog.filetype.link_list'), '*.txt')],
                                            initialfile=i18n.t('dialog.export_links.file'))
        if path:
            try:
                Path(path).write_text('\n'.join(i['url'] for i in items)+'\n', encoding='utf-8-sig')
            except OSError as error:
                messagebox.showerror(i18n.t('dialog.save_failed'), messages.error_text(error), parent=self)

    def export_collected_text(self):
        pages = [p for p in self.collected_pages if p['text']]
        if not pages:
            return
        path = filedialog.asksaveasfilename(parent=self, title=i18n.t('dialog.export_text.title'), defaultextension='.txt',
                                            filetypes=[(i18n.t('dialog.filetype.text'), '*.txt'), ('Markdown', '*.md')],
                                            initialfile=i18n.t('dialog.export_text.file'))
        if path:
            try:
                markdown = Path(path).suffix.lower() == '.md'
                parts = []
                for p in pages:
                    # (I18N.md 8.8) the labels of the file in the running language; zh: the 1.6.0 bytes
                    note = i18n.t('filetext.truncated_export') if p.get('text_truncated') else ''
                    parts.append(i18n.t('filetext.export_page', heading='# ' if markdown else '',
                                        title=messages.show(p['title']), url=p['url'], note=note,
                                        body=p['markdown'] if markdown else p['text']))
                Path(path).write_text('\n\n---\n\n'.join(parts)+'\n', encoding='utf-8-sig')
            except OSError as error:
                messagebox.showerror(i18n.t('dialog.save_failed'), messages.error_text(error), parent=self)
