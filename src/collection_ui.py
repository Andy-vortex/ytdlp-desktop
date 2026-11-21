"""Page collection workspace for the existing desktop downloader (mixin CollectionUI; 1.6.0 WP5).

Layout (2.0.0 S4, docs/2.0.0/handoff/S4/collect.md): no cards.  The link box, one row of options whose
values say what they are, Subscribe and Start (Stop in the same cell while collecting); the results
toolbar (search, filter, count, Add to downloads, '⋯') above the table, or the empty state; one status
line below.  Every colour, font and widget comes from ui_theme.  Never import studio here at module level.

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
from ui_theme import SEPARATOR, MenuItem
import urls as links
from collector import CollectOptions, CollectionRunner, MAX_LINKS
from engine import parse_urls
from library import is_busy, ui_wait
from library_ui import LIBRARY_RETRY_MS, cell_width, columns, pin_rows

# (1.7.0 U1) A collection start that met a library locked by another program waits for it.
COLLECT_WAIT = 120          # s: then the start is given up (status line and log)


def subscription_result(record, cancelled):
    """(pages, successful, error) of a subscription check for Library.finish_subscription, from
    the collection job's record."""
    successful = not cancelled and record['status'] == 'completed'
    # (1.8.0, C-F39) The last two errors as one clauses message (stored: the 1.7.0 '；' join of the
    # zh texts), else why the check ended; the library page shows it with messages.show.  (2.0.0) A
    # stored error is {'error', 'reason'} (S2-DESIGN.md 1.2).
    errors = [item.get('error') if isinstance(item, dict) else item for item in record['data']['errors'][-2:]]
    error = messages.clauses(errors) or \
        messages.msg('value.subscription.stopped' if cancelled else 'value.subscription.unfinished')
    return record['pages'], successful, error


def scrolls_label(count):
    """The scrolls menu's label of ``count`` (the value carries its unit, so the menu needs no label)."""
    return i18n.t('collect.value.scrolls_one') if count == 1 else i18n.t('collect.value.scrolls', count=count)


def pages_label(count):
    """The pages menu's label of ``count``."""
    return i18n.t('collect.value.pages_one') if count == 1 else i18n.t('collect.value.pages', count=count)


# Codes, never shown: the menus show i18n.label('collect_mode' / 'collect_filter', code).
MODE_STATIC = 'static'
MODE_DYNAMIC = 'dynamic'
FILTERS = ('media', 'all')
# The stored link kind (a code since 2.0.0: collector.link_kind; library.sqlite3) the media filter
# leaves out; shown through i18n.label('link_kind', ...).
WEB_LINK = 'web'
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
        """(2.0.0 S4, collect.md 2.5) No cards: the link box, the options row (mode, scrolls, pages, CSS scope,
        Subscribe, Start / Stop in one cell), the privacy line, the results toolbar (search, filter, count,
        Add to downloads, '⋯'), the table or the empty state in one cell, the status line."""
        page = self.page('collect', i18n.t('nav.collect'))
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(5, weight=1)
        # The page's state containers are created by StudioApp.init_collection() (studio.py).

        self.collect_urls = ui_theme.textbox(page, placeholder=i18n.t('collect.placeholder'), height=56, wrap='word')
        self.collect_urls.grid(row=1, column=0, sticky='ew')
        self.collect_urls.bind('<Control-Return>', self._collect_from_box)     # like the download link box

        # ------------------------------------------------ options: no labels, the values say what they are
        options = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        options.grid(row=2, column=0, sticky='ew', pady=(ui_theme.GAP, 0))
        options.grid_columnconfigure(3, weight=1, minsize=round(96 * ctk.ScalingTracker.get_widget_scaling(options)))
        self.collect_options = options
        # The variables keep the codes ('static' / 'dynamic', '0'…'5', '1'…'5'); the menus show the labels.
        labels = [i18n.label('collect_mode', value) for value in (MODE_STATIC, MODE_DYNAMIC)]
        self.collect_mode_menu = ui_theme.option_menu(options, (MODE_STATIC, MODE_DYNAMIC), labels=labels,
                                                      variable=self.collect_mode,
                                                      width=ui_theme.menu_width(options, labels))
        self.collect_mode_menu.grid(row=0, column=0, sticky='w')
        values = [str(n) for n in range(6)]
        labels = [scrolls_label(n) for n in range(6)]
        self.collect_scrolls_menu = ui_theme.option_menu(options, values, labels=labels, variable=self.collect_scrolls,
                                                         width=ui_theme.menu_width(options, labels))
        self.collect_scrolls_menu.grid(row=0, column=1, sticky='w', padx=(ui_theme.INLINE, 0))
        values = [str(n) for n in range(1, 6)]
        labels = [pages_label(n) for n in range(1, 6)]
        self.collect_pages_menu = ui_theme.option_menu(options, values, labels=labels, variable=self.collect_pages_limit,
                                                       width=ui_theme.menu_width(options, labels))
        self.collect_pages_menu.grid(row=0, column=2, sticky='w', padx=(ui_theme.INLINE, 0))
        self.collect_scope = ui_theme.entry(options, textvariable=self.collect_selector, width=96,
                                            placeholder=i18n.t('collect.option.selector'))
        self.collect_scope.grid(row=0, column=3, sticky='ew', padx=(ui_theme.PAD, 0))
        text = i18n.t('collect.save_subscription')
        self.collect_subscribe = ui_theme.button(options, text, self.save_subscription, kind='ghost',
                                                 width=ui_theme.fitted_width(options, text))
        # INLINE on both sides: the ghost button's own padding then sits evenly between the CSS box and Start.
        self.collect_subscribe.grid(row=0, column=4, padx=(ui_theme.INLINE, 0))
        # Start and Stop share one cell and one width, so Subscribe never moves when they swap: the text, the
        # 16 px icon, customtkinter's 6 px spacing and the image label's own border and padding (6 physical px).
        start_text, stop_text = i18n.t('collect.start'), i18n.t('common.stop')
        width = max(ui_theme.fitted_width(options, start_text) + 30, ui_theme.fitted_width(options, stop_text))
        self.collect_start = ui_theme.button(options, start_text, self.start_collection, kind='primary', width=width,
                                             image=ui_theme.icon(ui_theme.ICON_SEARCH, 'white'), compound='left')
        self.collect_start.grid(row=0, column=5, padx=(ui_theme.INLINE, 0))
        self.collect_stop = ui_theme.button(options, stop_text, self.stop, kind='danger', width=width)
        self.collect_stop.grid(row=0, column=5, padx=(ui_theme.INLINE, 0))
        self.collect_stop.grid_remove()

        # Dynamic mode only: the privacy line; during an automatic subscription check, why the controls are grey.
        self.collect_note = ui_theme.label(page, '', role='caption', text_color=ui_theme.MUTED, anchor='w', width=1)
        self.collect_note.grid(row=3, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        self.collect_mode.trace_add('write', lambda *_: self._collect_mode_changed())
        self._collect_mode_changed()
        self.controls += [self.collect_urls, self.collect_mode_menu, self.collect_scrolls_menu, self.collect_pages_menu,
                          self.collect_scope, self.collect_subscribe, self.collect_start]

        # ------------------------------------------------ results: toolbar (only with results), table or empty state
        bar = ctk.CTkFrame(page, fg_color='transparent', corner_radius=0)
        bar.grid(row=4, column=0, sticky='ew', pady=(ui_theme.SECTION, 0))
        bar.grid_columnconfigure(3, weight=1)
        self.collect_toolbar = bar
        self.collect_search_entry = ui_theme.entry(bar, textvariable=self.collect_search, width=200,
                                                   placeholder=i18n.t('common.search'))
        self.collect_search_entry.grid(row=0, column=0, sticky='w')
        labels = [i18n.label('collect_filter', value) for value in FILTERS]
        self.collect_filter_menu = ui_theme.option_menu(bar, FILTERS, labels=labels, variable=self.collect_filter,
                                                        width=ui_theme.menu_width(bar, labels))
        self.collect_filter_menu.grid(row=0, column=1, sticky='w', padx=(ui_theme.INLINE, 0))
        self.collect_count = ui_theme.label(bar, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.collect_count.grid(row=0, column=2, sticky='w', padx=(ui_theme.PAD, 0))
        # As wide as the longest count, so the button never changes width.
        self.collect_add = ui_theme.button(bar, i18n.t('collect.add'), self.add_collected, kind='primary',
                                           width=ui_theme.fitted_width(bar, self.add_text(9999)), state='disabled')
        self.collect_add.grid(row=0, column=4, sticky='e')
        self.collect_menu = ui_theme.menu_button(bar, self.collect_menu_items)
        self.collect_menu.grid(row=0, column=5, padx=(ui_theme.INLINE, 0))
        bar.grid_remove()

        # (1.7.0, I18N.md 10.1) The Type column shows the link-kind labels: as wide as the widest needs.
        kind_width = cell_width(page, [text for _value, text in i18n.choices('link_kind')], 72)
        self.collect_tree = ui_theme.table(page, columns(page, [
            ('kind', i18n.t('table.type'), kind_width, False), ('title', i18n.t('table.title'), 260, True),
            ('url', i18n.t('table.link'), 360, True)]), selectmode='extended', empty=i18n.t('common.no_match'))
        self.collect_tree.container.grid(row=5, column=0, sticky='nsew', pady=(ui_theme.GAP, 0))
        self.collect_tree.container.grid_remove()
        pin_rows(self.collect_tree, 4)     # the page's request stays the same once the scrollbar appears
        self.collect_tree.ellipsize(middle=('url',))     # cut with '…'; links keep their distinguishing end
        self.collect_tree.bind('<<TreeviewSelect>>', lambda _: self.collection_selection_changed())
        for sequence in ('<Control-a>', '<Control-A>'):      # Ctrl+A on the table only: in the entries it selects text
            self.collect_tree.bind(sequence, self._collect_select_all)
        ui_theme.bind_menu(self.collect_tree, self.collect_menu_items)
        self.collect_empty = ui_theme.empty_state(page, i18n.t('collect.empty'), ui_theme.ICON_SEARCH)
        self.collect_empty.grid(row=5, column=0, sticky='nsew', pady=(ui_theme.GAP, 0))

        self.collect_status = ui_theme.label(page, '', role='caption', text_color=ui_theme.MUTED, anchor='w', width=1)
        self.collect_status.grid(row=6, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        ui_theme.fit(self.collect_status, '')
        self.collect_filter.trace_add('write', lambda *_: self.render_collection_links())
        self.collect_search.trace_add('write', lambda *_: self.render_collection_links())
        self.render_collection_links()
        self.refresh_collect_actions()

    # ------------------------------------------------------------ input helpers
    def _collect_from_box(self, _event=None):
        """Ctrl+Enter in the start-URL box starts the collection instead of adding a line."""
        self.start_collection()
        return 'break'

    def _collect_mode_changed(self):
        """The scrolls menu only means something for dynamic pages; the privacy line explains that mode."""
        ui_theme.show(self.collect_scrolls_menu, self.collect_mode.get() == MODE_DYNAMIC)
        self.refresh_collect_note()

    def refresh_collect_note(self, background=None):
        """The line under the options: ``background`` (a subscription name) says why the controls are grey while
        an automatic subscription check runs (robust-13); otherwise the privacy line of dynamic mode, or nothing."""
        if background is not None:
            text, color = i18n.t('collect.background', name=background), ui_theme.ACCENT_TEXT
        elif self.collect_mode.get() == MODE_DYNAMIC:
            text, color = i18n.t('collect.note.dynamic'), ui_theme.MUTED
        else:
            text, color = '', ui_theme.MUTED
        self.collect_note.configure(text_color=color)
        ui_theme.fit(self.collect_note, text)      # a long subscription name is cut, the full text is kept
        ui_theme.show(self.collect_note, bool(text))

    def set_collect_status(self, text, tone=None):
        """The one-line status under the table; tone idle (MUTED), running, warn or ok."""
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

    def launch_collection(self, options, job_id=None, title=None, background=False, via=None):
        """Start a collection.  ``background`` (automatic subscription checks) leaves the results,
        the status line, the start-URL box and the text-export button of the collect page alone.
        ``title`` / ``via``: the new job's title (default: the first start link) and how it was made
        (library.create_job; 'subscription' for a subscription check, whose title may be '').

        Returns False when the history record could not be written (final-fix-ui,
        final-architecture-2): nothing starts, the results shown stay, and the owner gets a dialog
        and a status line.  A background check re-raises instead: check_subscription logs it and
        backs off."""
        fresh = not job_id
        self.collect_pending = None
        try:
            if fresh:
                # Untitled: the first start link (user data); a subscription's job keeps its name.
                job_id = self.library.create_job('collect', title if title or via else options.urls[0],
                                                 asdict(options), self.active_subscription, via=via)
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
                self.collect_pending_via = via          # (2.0.0) a subscription check's job keeps its 'via'
                self.active_subscription = None
                self.set_collect_status(i18n.t('collect.status.waiting_library'), 'running')
                self.library_busy(error)
                ui_theme.cancel_after(self, self.collect_retry_after)
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
        self.operation = 'collect'
        try:
            # Inside the try: if set_busy raised, the except below still releases the window.
            self.set_busy(True)         # refresh_collect_actions: Stop takes Start's place
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
            raise

    def retry_collection(self):
        """(1.7.0 U1) Try a collection start again that waited for a locked library."""
        self.collect_retry_after = None
        pending, self.collect_pending = self.collect_pending, None
        if pending is None or self.closing:
            return
        options, job_id, title, subscription, deadline = pending
        via, self.collect_pending_via = getattr(self, 'collect_pending_via', None), None
        if self.busy:
            self.append_log(i18n.t('collect.status.library_dropped'))
            self.set_collect_status(i18n.t('collect.status.library_dropped'), 'warn')
            return
        if time.monotonic() >= deadline:
            self.append_log(i18n.t('collect.status.library_gave_up'))
            self.set_collect_status(i18n.t('collect.status.library_gave_up'), 'warn')
            return
        self.active_subscription = subscription
        self.launch_collection(options, job_id, title, via=via)

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
            self.refresh_collect_actions()      # the first page brings the toolbar and the table
            self.set_collect_status(i18n.t('collect.status.progress', pages=len(self.collected_pages),
                                           links=len(self.collected_links)), 'running')
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

    def refresh_collect_actions(self):
        """(2.0.0 S4, collect.md 2.6) What follows the busy state and the results: Stop takes Start's place
        while a collection runs (also an automatic subscription check); with results the toolbar and the table
        replace the empty state, and Add to downloads is the solid button instead of Start (soft).  Called by
        set_busy, a collected page (handle_collection) and load_collection_record; only a change touches a widget (1.7.0 U6)."""
        collecting = bool(self.busy) and self.operation == 'collect'
        rich = bool(self.collected_links or self.collected_pages)
        before = getattr(self, '_collect_stage', (None, None))
        if before == (collecting, rich):
            return
        self._collect_stage = (collecting, rich)
        if collecting != before[0]:
            # usable while shown (studio.stop greys it until the run ends), grey while hidden
            self.collect_stop.configure(state='normal' if collecting else 'disabled')
            ui_theme.show(self.collect_start, not collecting)
            ui_theme.show(self.collect_stop, collecting)
        if rich != before[1]:
            self.collect_start.set_kind('soft' if rich else 'primary',
                                        image=ui_theme.icon(ui_theme.ICON_SEARCH, 'accent' if rich else 'white'))
            ui_theme.show(self.collect_toolbar, rich)
            ui_theme.show(self.collect_tree.container, rich)
            ui_theme.show(self.collect_empty, not rich)

    def collection_selection_changed(self):
        count = len(self.collect_tree.selection())
        text = i18n.t('collect.count', shown=len(self.collect_tree.get_children()), total=len(self.collected_links))
        if self.collect_count.cget('text') != text:
            self.collect_count.configure(text=text)
        # (1.7.0 U6) set_busy calls this method on every busy switch: a control is configured only
        # when its text or state changes (each customtkinter redraw can flush the window's layout).
        text = self.add_text(count)
        state = 'normal' if count and self.can_add_collected() else 'disabled'
        if self.collect_add.cget('text') != text or self.collect_add.cget('state') != state:
            self.collect_add.configure(text=text, state=state)
        # The start-URL box's disabled look: at once on the collect page or for a collection; for a
        # download started on another page it waits until the collect page is shown (show_page),
        # because the text box's redraw lays out the whole window again (about 0.2 s right after
        # 开始下载 with 300 links).
        if getattr(self, 'current_page', 'collect') == 'collect' or self.operation == 'collect':
            self.collect_urls.set_dimmed(bool(self.busy))

    @staticmethod
    def add_text(count):
        """加入下载, with the number of selected links when there are any."""
        return f"{i18n.t('collect.add')} ({count})" if count else i18n.t('collect.add')

    # ------------------------------------------------------------ the results' '⋯' and its right-click mirror
    def collect_menu_items(self):
        """(collect.md 4.2) Built when the menu opens, so every entry is enabled by the state of that moment."""
        tree = self.collect_tree
        picked = len(tree.selection())
        collecting_here = self.busy and self.operation == 'collect' and not getattr(self, 'collection_background', False)
        return [MenuItem(self.add_text(picked), self.add_collected, enabled=bool(picked) and self.can_add_collected()),
                MenuItem(i18n.t('common.select_all'), self.select_collected, enabled=bool(tree.get_children())),
                SEPARATOR,
                MenuItem(i18n.t('collect.export_links'), self.export_collected_links, enabled=bool(picked)),
                MenuItem(i18n.t('collect.save_text'), self.export_collected_text,
                         enabled=any(p['text'] for p in self.collected_pages) and not collecting_here),
                MenuItem(i18n.t('collect.archive_text'), self.archive_pages,
                         enabled=bool(self.collected_pages) and not self.busy)]

    def select_collected(self):
        """Select every row the table shows (the filter and the search decide which)."""
        rows = self.collect_tree.get_children()
        if rows:
            self.collect_tree.selection_set(rows)

    def _collect_select_all(self, _event=None):
        self.select_collected()
        return 'break'

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
