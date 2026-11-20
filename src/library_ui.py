"""Task recovery, incremental subscriptions, and source archive UI (mixin LibraryUI; 1.6.0 WP5).

(2.0.0 S4, library.md / subscriptions.md) The library page: the title and the view switch (History |
Subscriptions | Archive) in the page header, one notice bar for the library file, and one view at a time;
each view is a toolbar (search or nothing on the left, its actions and its '⋯' on the right) above its
table.  Every action of a view has a visible control (a button or the view's '⋯'); the right click of a
table mirrors the same menu (``*_menu_items``).  Every colour, font and widget comes from ui_theme.  Never
import studio here at module level.

(1.7.0) Every shown text through i18n (I18N.md 8); stored texts (job titles, subscription errors,
reasons) keep their canonical Chinese form (1.7.0 showed them via i18n.localize, I18N.md 9.3);
the tab ids, the interval labels of the Tk variable and the job statuses keep their 1.6.0 values.

(1.8.0, MESSAGES.md 6) The library returns decoded records: app texts are messages, user data is
raw; the page shows them with messages.show / messages.error_text, so no key-prefix list is left.
"""
from collections import deque
from dataclasses import asdict
from pathlib import Path
import os
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import applog
import i18n
import messages
import settings
import ui_theme
from collector import MAX_LINKS, CollectOptions
from engine import BOT_CHECK_PATTERN, STALE_PATTERN, Options, explain_error
from paths import APP_DIR
from library import (HISTORY_PAGE, JOB_OPTION_KEYS, Archive, is_busy, job_downloads, open_library, set_ui_thread,
                     title_parts, ui_wait)
import urls as urltools

STATES = ('running', 'completed', 'partial', 'interrupted')      # the stored job statuses
RESUME_POLL = 100     # ms between the checks that hand the other formats of resumed links to the run
RETRY_BACKOFF = 900     # s: an automatic check that could not start waits at least this long (or its interval)
# (1.7.0 U1) Another program holds library.sqlite3: the window never waits for it (library.py
# gives up after about 0.15 s on the window's thread); it says so calmly, writes what must not be
# lost later (HistoryWriter) and looks again every LIBRARY_RETRY_MS.
LIBRARY_RETRY_MS = 2000
# (1.7.0 U2) The history list: the newest HISTORY_PAGE jobs, more on request, a search the database runs.
HISTORY_SEARCH_MS = 250         # typing pause before the search runs

# The library tab ids (used by snapshot_ui and compared by studio.focus_page): codes, never shown;
# the tab bar shows i18n.label('library_tab', id).
TAB_HISTORY = 'history'
TAB_SUBSCRIPTIONS = 'subscriptions'
TAB_ARCHIVE = 'archive'
VIEW_TABLES = {TAB_HISTORY: 'history_tree', TAB_SUBSCRIPTIONS: 'subscription_tree', TAB_ARCHIVE: 'archive_tree'}


def text_width(parent, text, role='caption'):
    """(1.7.0) Logical px of ``text`` in the font of ``role`` (tables use the caption size)."""
    scale = ui_theme._scaling(parent) or 1.0
    return int(-(-ui_theme._measure_font(parent, ui_theme.font(role)).measure(str(text)) // scale))


def cell_width(parent, texts, width=0):
    """(final fix round 1, ENV-3 / ENV-4; I18N.md 10.1) The logical width of a fixed column whose cells
    show one of ``texts`` (the labels of a group): max(``width``, the widest text as AppTable's cut
    measures it + the room a cell keeps beside its text (ui_theme.cell_spare) + 1 px for the rounding
    of scaled widths), so no label of the group is cut with '…'."""
    scale = ui_theme._scaling(parent) or 1.0
    width_of = ui_theme._width_lookup(ui_theme._measure_font(parent, (ui_theme.FAMILY, ui_theme.TABLE_FONT)))
    widest = max((sum(width_of(char) for char in str(text)) for text in texts), default=0)
    return max(width, int(-(-(widest + ui_theme.cell_spare(scale)) // scale)) + 1)


def columns(parent, specs):
    """(1.7.0, I18N.md 10.1) Table columns (key, heading, width, stretch) whose width is at least
    the heading text + 24 px, so a heading is never cut (the 1.6.0 width when that is wider)."""
    return [(key, heading, max(width, text_width(parent, heading) + 24), stretch)
            for key, heading, width, stretch in specs]


class PendingJob:
    """(1.7.0 U1) A history job whose record waits in the HistoryWriter (the library was locked).
    ``id`` is the record id once it is written; ``lost`` when it could not be written at all."""
    __slots__ = ('id', 'lost')

    def __init__(self):
        self.id = None
        self.lost = False

    def __repr__(self):
        return f'PendingJob({self.id!r})'


def job_id_of(job):
    """The record id of a job (an id, a PendingJob or None); None while it is not written."""
    return job.id if isinstance(job, PendingJob) else job


def require_job(job):
    """The record id of ``job`` for a write that follows its creation; LookupError when the
    record could not be written (the write is then dropped and logged)."""
    found = job_id_of(job)
    if not found:
        raise LookupError('the history job was never written')
    return found


# (1.7.0 fix round 1) How long the window's thread repeats a history write that met a lock before
# the HistoryWriter takes it over (library.ui_wait): one try against another program's lock costs
# about 30 ms, so this is one try; a quick collision with this program's own short transaction
# returns at once and gets one repeat after library.UI_PAUSE.
UI_WRITE_WAIT = 0.03


class HistoryWriter:
    """(1.7.0 U1) Writes the history must not lose (a new job record, links added with 加入队列,
    the events of a job whose record waits, subscription bookkeeping) when the library is locked.

    ``run(task)`` calls ``task()`` at once on the calling thread, unless earlier writes still wait
    (then it waits behind them, so a job's writes keep their order).  A task that meets a lock
    ("database is locked", library.is_busy) waits in a queue that one daemon thread works through
    in order: with the worker busy timeout (library.WORKER_TIMEOUT), repeated every RETRY seconds
    until it is written or the window closes (close()).  Any other error drops that task with a
    log line, as a failing history write always did.  Never blocks the window's thread.
    """
    RETRY = 1.0

    def __init__(self, logger=None):
        self.lock = threading.Lock()
        self.tasks = deque()
        self.thread = None
        self.closed = False
        self.written = 0        # tasks written by the thread (they had waited)
        self.dropped = 0
        self.logger = logger or applog.get('ui')

    def waiting(self):
        """How many writes still wait (0 when everything is written)."""
        with self.lock:
            return len(self.tasks)

    def run(self, task):
        """True when ``task`` ran now; False when it waits in the queue.  A non-lock error of a
        task run now is raised to the caller, as before."""
        with self.lock:
            queued = bool(self.tasks)
            if queued:
                self._append(task)
        if queued:
            return False
        try:
            # (1.7.0 fix round 1) A busy result is deferred anyway: on the window's thread the
            # write is not repeated for the full UI_WAIT (one try plus at most one quick repeat,
            # enough for this program's own short transactions).
            with ui_wait(UI_WRITE_WAIT):
                task()
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.defer(task)
            return False
        return True

    def defer(self, task):
        with self.lock:
            self._append(task)

    def _append(self, task):
        self.tasks.append(task)
        if not self.closed and (self.thread is None or not self.thread.is_alive()):
            self.thread = threading.Thread(target=self._work, name='history-writer', daemon=True)
            self.thread.start()

    def _work(self):
        while True:
            with self.lock:
                if self.closed or not self.tasks:
                    self.thread = None
                    return
                task = self.tasks[0]
            try:
                task()
            except sqlite3.OperationalError as error:
                if is_busy(error):
                    time.sleep(self.RETRY)
                    continue
                self.logger.exception('a waiting history write failed')
                self.dropped += 1
            except Exception:
                self.logger.exception('a waiting history write failed')
                self.dropped += 1
            else:
                self.written += 1
            with self.lock:
                if self.tasks and self.tasks[0] is task:
                    self.tasks.popleft()

    def close(self):
        """The window closes: the thread stops after its current try; returns what was not written."""
        with self.lock:
            self.closed = True
            return len(self.tasks)

    def idle(self, timeout):
        """Wait up to ``timeout`` s for every waiting write (tests and close); True when none waits."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.waiting():
                return True
            time.sleep(0.02)
        return not self.waiting()


class _QueuedIndex:
    """The library as Archive sees it on the window's thread: archive_index goes through the
    HistoryWriter, so a locked library never loses an archive entry (1.7.0 U1)."""

    def __init__(self, library, writer):
        self._library, self._writer = library, writer
        self.deferred = False

    def archive_index(self, folder, data):
        library = self._library
        if not self._writer.run(lambda: library.archive_index(folder, data)):
            self.deferred = True

    def __getattr__(self, name):
        return getattr(self._library, name)


def _cancel_after(widget, job):
    if job is not None:
        try:
            widget.after_cancel(job)
        except (tk.TclError, ValueError):
            pass


def pin_rows(table, rows):
    """Make a table ask for ``rows`` rows plus its heading, whether or not its scrollbar is shown.

    The table's scrollbar (a CTkScrollbar) asks for 200 px of its own; once it appears a 4-row table
    asks for about 208 px instead of 166 and the subscription tab no longer fits at 1000x740.  The
    container keeps the asked size fixed; its grid cell still stretches it.
    """
    table.container.configure(height=ui_theme.TABLE_ROW_H * (rows + 1) + 8)
    table.container.grid_propagate(False)


def capped_links(pages):
    """(1.7.0 U3) The links of stored pages as the collect page keeps them during a run: the first
    MAX_LINKS distinct links in page order (collection_ui's collect_page handler); the record in
    the history keeps every page whole."""
    links = {}
    for page in pages:
        for item in page.get('links') or []:
            if len(links) >= MAX_LINKS:
                return links
            links.setdefault(item['url'], item)
    return links


def clock(stamp):
    return time.strftime('%m-%d %H:%M', time.localtime(stamp)) if stamp else i18n.t('library.never_checked')


def history_title(row):
    """The name of a job in the history list (2.0.0, S2-DESIGN.md 1.2): library.title_parts in the
    running language.  The title column holds the media's or the owner's own text, shown as it is; a
    download job without one is named after its first link, like the task rows (polish
    ui-daily-flow-6); a subscription's job is '订阅：<name>'."""
    parts = title_parts(row)
    form, text = parts[0], parts[1]
    if form == 'subscription':
        return i18n.t('value.job_title.subscription',
                      name=messages.show(messages.raw(text) or messages.msg('value.subscription.untitled')))
    if form == 'multi':
        return i18n.t('value.job_title.multi', title=messages.show(messages.raw(text)), count=int(parts[2]))
    if form == 'link':
        return urltools.display(text)
    if form == 'multi_link':
        return i18n.t('value.job_title.multi', title=urltools.display(text), count=int(parts[2]))
    return messages.show(messages.raw(text))


def history_status(row):
    """The status word of a job in the history list (the stored status string is never changed).
    A download job that ended without a single successful download is stored as 'partial'; the list
    says what the download page said (polish ui-daily-flow-6)."""
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    if row.get('kind') == 'download' and row.get('status') == 'partial' and not data.get('done'):
        return i18n.t('common.unfinished')
    status = row.get('status')
    return i18n.label('job_status', status) if status in STATES else status


def history_row(row):
    """(1.7.0) The four cells of a job in the history list, in the running language."""
    kind = i18n.label('job_kind', 'collect' if row['kind'] == 'collect' else 'download')
    return kind, history_title(row), history_status(row), clock(row['updated'])


def subscription_row(row, checking=False, now=None):
    """(2.0.0 S4, subscriptions.md 6.3) The four cells of a subscription: its name (the app's default name in
    the running language), the schedule word, the last success and the new count.  The schedule word says
    why a subscription is not checked yet: Checking (its check runs or waits for the library), Paused, Due
    (its time has come; the window is busy or the check backs off), else the label of the stored seconds."""
    if checking:
        state = i18n.t('library.state.checking')
    elif not row['enabled']:
        state = i18n.t('library.state.paused')
    elif row['interval'] and row['next_check'] <= (time.time() if now is None else now):
        state = i18n.t('library.state.due')
    else:
        state = i18n.label('interval', row['interval'])
    return (messages.show(row['name']), state, clock(row['data']['last_check']), row['pending'])


def resume_plan(entries):
    """Split the downloads of a job (library.job_downloads) that are not done into the first one of
    each link, in job order (they start as one job), and the rest: the same links in other formats,
    which join that job's queue once it runs (review-e2e-4)."""
    first, rest, seen = [], [], set()
    for entry in entries:
        if entry.get('done'):
            continue
        if entry['url'] in seen:
            rest.append(entry)
        else:
            seen.add(entry['url'])
            first.append(entry)
    return first, rest


class LibraryUI:
    def init_library(self, settings):
        # robust-6: the history is optional.  open_library() falls back to a throw-away database and
        # says so; the warning is logged here and shown above the tabs of the library page.
        path = Path(self.output.get()) / 'test-library.sqlite3' if self.smoke_mode else None
        set_ui_thread()         # (1.7.0 U1) this thread never waits long for a lock (library.py)
        self.history_writer = HistoryWriter()
        self.library_waiting = False            # (U1) a read or write met a lock; the notice is up
        self.library_busy_logged = False
        self.library_busy_count = 0
        self.library_watch_after = None
        self.collect_pending = None             # (U1) a collection start that waits for the library
        self.collect_retry_after = None
        self.history_search = tk.StringVar()    # (U2)
        self.history_limit = HISTORY_PAGE
        self.history_search_after = None
        self.library, self.library_warning = open_library(path)
        if self.library_warning:
            self.append_log(self.library_warning)       # log file and log page
        self.collection_job = None
        self.collection_background = False      # True while an automatic subscription check runs
        self.subscription_backoff = {}          # subscription id -> time before which the timer skips it
        self.library_problem = ''               # the history error shown in the warning strip ('' = none)
        self.download_job = None
        self.active_subscription = None
        # (2.0.0 S4) archive_enabled, archive_root and the defaults of a new subscription (subscription_interval,
        # subscription_media_only, subscription_first_new) are studio's persisted variables (the settings page).
        self.archive_search = tk.StringVar()
        self.library_warning_dismissed = False  # the open_library warning was closed with Got it
        self._history_record = (None, None)     # (iid, record) that history_selection read last

    def build_library_page(self):
        page = self.page('library', i18n.t('nav.library'))
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        # robust-6, PLAN 3-10: a library file that could not be read, is locked or was set aside is said on
        # one notice bar above the views (show_library_problem).
        self.library_notice = ui_theme.notice_bar(page, tone='warn')
        self.library_notice.grid(row=1, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        self.library_notice.hide()
        # The view switch stands in the page header, right of the title.  A keyboard focus left in a view
        # that is switched away from moves to the new view's table (final-fix-ui, polish-function-3); the
        # hidden views take no Tab focus (ui_theme.Tabs).  The tab ids are codes; the bar shows the labels.
        ids = [TAB_HISTORY, TAB_SUBSCRIPTIONS, TAB_ARCHIVE]
        self.library_tabs = ui_theme.tabs(page, ids, focus=lambda name: getattr(self, VIEW_TABLES.get(name, ''), None),
                                          labels=[i18n.label('library_tab', name) for name in ids],
                                          bar_parent=page.header.left)
        self.library_tabs.grid(row=2, column=0, sticky='nsew')
        self.build_history_tab(self.library_tabs.tab(TAB_HISTORY))
        self.build_subscription_tab(self.library_tabs.tab(TAB_SUBSCRIPTIONS))
        self.build_archive_tab(self.library_tabs.tab(TAB_ARCHIVE))
        # F5 refreshes the library while it is shown (the download page's F5 retries its unfinished links).
        self.bind('<F5>', self.library_key_refresh, add='+')
        self.show_library_problem('')
        self.refresh_library()
        self.after(1500, self.subscription_tick)

    def library_key_refresh(self, _event=None):
        """F5: refresh the library page while it is shown."""
        if getattr(self, 'current_page', None) == 'library':
            self.refresh_library()

    @staticmethod
    def table_actions(tree, action):
        """Double click on a row (not on the heading: that resizes columns) and Enter on the table run
        ``action``, the row's most common "open" action (00-system 2.8)."""
        def double(event):
            if tree.identify_region(event.x, event.y) == 'cell':
                action()

        def enter(_event):
            action()
            return 'break'      # not on to the window's Enter (start a download)
        tree.bind('<Double-Button-1>', double, add='+')
        tree.bind('<Return>', enter, add='+')
        tree.on_double_click, tree.on_enter = double, enter      # tests and agents call them (Tk cannot fake a double click)

    @staticmethod
    def toolbar(tab):
        """The row above a view's table: column 1 is the elastic space (what to look at left, actions right)."""
        bar = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        bar.grid(row=0, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        bar.grid_columnconfigure(1, weight=1)
        return bar

    def build_history_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        bar = self.history_bar = self.toolbar(tab)
        # (1.7.0 U2) The search runs in the database (title or link).
        self.history_search_entry = ui_theme.entry(bar, textvariable=self.history_search,
                                                   placeholder=i18n.t('common.search'), width=280)
        self.history_search_entry.grid(row=0, column=0, sticky='w')
        self.history_search.trace_add('write', lambda *_: self.history_search_changed())
        # The count beside the search it answers, as on the collect page (Total: N, or Found: N).
        self.history_count = ui_theme.label(bar, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.history_count.grid(row=0, column=1, sticky='w', padx=(ui_theme.PAD, 0))
        resume = i18n.t('library.resume')
        self.history_resume = ui_theme.button(bar, resume, self.resume_history, kind='primary',
                                              width=ui_theme.fitted_width(bar, resume), state='disabled')
        self.history_resume.grid(row=0, column=2, sticky='e')
        self.history_menu = ui_theme.menu_button(bar, self.history_menu_items)
        self.history_menu.grid(row=0, column=3, sticky='e', padx=(ui_theme.INLINE, 0))
        self.history_tree = ui_theme.table(tab, columns(tab, [
            ('kind', i18n.t('table.type'), 80, False), ('title', i18n.t('table.task'), 360, True),
            ('status', i18n.t('table.status'), 96, False), ('time', i18n.t('table.updated'), 120, False)]),
                                           empty=i18n.t('common.no_match'))
        self.history_tree.container.grid(row=1, column=0, sticky='nsew')
        pin_rows(self.history_tree, 4)
        self.history_tree.ellipsize()
        self.history_tree.bind('<<TreeviewSelect>>', lambda _: self.history_selection())
        ui_theme.bind_menu(self.history_tree, self.history_menu_items)
        self.table_actions(self.history_tree, self.view_history)
        # Under the table: the selected job's details (one line, fit: the whole text in ui_theme.full_text; a
        # wrapped line made the table jump) and Load more while more exist (1.7.0 U2).
        footer = self.history_footer = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        footer.grid(row=2, column=0, sticky='ew', pady=(ui_theme.INLINE, 0))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_rowconfigure(0, minsize=ui_theme.CONTROL_H_SM)
        self.history_note = ui_theme.label(footer, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.history_note.grid(row=0, column=0, sticky='ew')
        ui_theme.fit(self.history_note, '')
        more = i18n.t('library.history.more')
        self.history_more = ui_theme.button(footer, more, self.more_history, kind='ghost', size='sm',
                                            width=ui_theme.fitted_width(footer, more, 'sm'))
        self.history_more.grid(row=0, column=1, sticky='e', padx=(ui_theme.PAD, 0))
        self.history_more.grid_remove()
        # The whole view is empty (no job, no search): one short line instead of a toolbar and an empty table.
        self.history_empty = ui_theme.empty_state(tab, i18n.t('library.history.empty'), ui_theme.ICON_LIBRARY)
        self.history_empty.grid(row=0, column=0, rowspan=3, sticky='nsew')
        self.history_empty.grid_remove()

    def build_subscription_tab(self, tab):
        """(2.0.0 S4, subscriptions.md 2.3) Check now and ⋯ above the subscriptions; the new count, the reason
        of a failed check and Add to downloads / Ignore above the new items.  New subscriptions are made on the
        collect page (Subscribe), their defaults on the settings page."""
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=2)
        tab.grid_rowconfigure(3, weight=3)
        bar = self.subscription_bar = self.toolbar(tab)
        check = i18n.t('library.subscription.check_now')
        self.subscription_check = ui_theme.button(bar, check, self.check_subscription, kind='primary',
                                                  width=ui_theme.fitted_width(bar, check), state='disabled')
        self.subscription_check.grid(row=0, column=2, sticky='e')
        self.subscription_menu = ui_theme.menu_button(bar, self.subscription_menu_items)
        self.subscription_menu.grid(row=0, column=3, sticky='e', padx=(ui_theme.INLINE, 0))
        # (final fix round 1, ENV-4) The Schedule column shows one schedule word: as wide as the widest of
        # them needs (I18N.md 10.1 group-label rule).
        words = [i18n.label('interval', seconds) for seconds in settings.SCHEDULES] + [
            i18n.t('library.state.paused'), i18n.t('library.state.checking'), i18n.t('library.state.due')]
        self.subscription_tree = ui_theme.table(tab, columns(tab, [
            ('name', i18n.t('table.subscription'), 210, True),
            ('state', i18n.t('table.schedule'), cell_width(tab, words, 96), False),
            ('last', i18n.t('table.last_success'), 115, False), ('pending', i18n.t('table.pending'), 0, False)]),
                                                empty=i18n.t('library.subscription.empty'), height=4)
        self.subscription_tree.container.grid(row=1, column=0, sticky='nsew')
        pin_rows(self.subscription_tree, 4)
        self.subscription_tree.ellipsize()
        self.subscription_tree.bind('<<TreeviewSelect>>', lambda _: self.subscription_selection())
        ui_theme.bind_menu(self.subscription_tree, self.subscription_menu_items)
        pending = self.pending_bar = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        pending.grid(row=2, column=0, sticky='ew', pady=(ui_theme.SECTION, ui_theme.GAP))
        pending.grid_columnconfigure(1, weight=1)
        pending.grid_rowconfigure(0, minsize=ui_theme.CONTROL_H)
        self.pending_count = ui_theme.label(pending, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.pending_count.grid(row=0, column=0, sticky='w')
        # The reason of the last failed check (DANGER), or why an action did nothing (WARN); one line, fit.
        self.subscription_note = ui_theme.label(pending, '', role='caption', text_color=ui_theme.DANGER, anchor='w')
        self.subscription_note.grid(row=0, column=1, sticky='ew', padx=(ui_theme.INLINE, ui_theme.PAD))
        ui_theme.fit(self.subscription_note, '')
        # 00-system 2.6: the secondary Ignore, then Add to downloads (N) on the right edge.  The count in that
        # label changes; the width is that of a three-digit count, so Ignore never moves.
        ignore = i18n.t('common.ignore')
        self.pending_ignore = ui_theme.button(pending, ignore, self.ignore_pending, kind='ghost', state='disabled',
                                              width=ui_theme.fitted_width(pending, ignore))
        self.pending_ignore.grid(row=0, column=2, sticky='e')
        self.pending_queue = ui_theme.button(pending, self.add_text(0), self.queue_subscription, state='disabled',
                                             width=ui_theme.fitted_width(pending, self.add_text(999)))
        # (final polish) its right edge is that of Check now above it: the '⋯' beside Check now sticks out alone
        self.pending_queue.grid(row=0, column=3, sticky='e', padx=(ui_theme.INLINE, ui_theme.INLINE + ui_theme.CONTROL_H))
        self.pending_tree = ui_theme.table(tab, columns(tab, [('title', i18n.t('table.title'), 290, True),
                                                              ('url', i18n.t('table.link'), 420, True)]),
                                           selectmode='extended', height=4, empty=i18n.t('library.pending.empty'))
        self.pending_tree.container.grid(row=3, column=0, sticky='nsew')
        pin_rows(self.pending_tree, 4)
        self.pending_tree.ellipsize(middle=('url',))
        self.pending_tree.bind('<<TreeviewSelect>>', lambda _: self.subscription_tools())
        ui_theme.bind_menu(self.pending_tree, self.pending_menu_items)
        for sequence in ('<Control-a>', '<Control-A>'):      # both: Caps Lock sends the uppercase keysym
            self.pending_tree.bind(sequence, self.select_all_pending, add='+')
        # No subscription at all: one short line and the way to make one (the collect page's Subscribe).
        self.subscription_empty = ui_theme.empty_state(
            tab, i18n.t('library.subscription.empty'), ui_theme.ICON_LIBRARY,
            action=(i18n.t('library.subscription.go_collect'), lambda: self.show_page('collect')))
        self.subscription_empty.grid(row=0, column=0, rowspan=4, sticky='nsew')
        self.subscription_empty.grid_remove()

    def build_archive_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        bar = self.archive_bar = self.toolbar(tab)
        self.archive_search_entry = ui_theme.entry(bar, textvariable=self.archive_search,
                                                   placeholder=i18n.t('common.search'), width=280)
        self.archive_search_entry.grid(row=0, column=0, sticky='w')
        self.archive_count = ui_theme.label(bar, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.archive_count.grid(row=0, column=1, sticky='w', padx=(ui_theme.PAD, 0))
        opened = i18n.t('common.open_folder')
        self.archive_open = ui_theme.button(bar, opened, self.open_archive, width=ui_theme.fitted_width(bar, opened),
                                            state='disabled')
        self.archive_open.grid(row=0, column=2, sticky='e')
        self.archive_menu = ui_theme.menu_button(bar, self.archive_menu_items)
        self.archive_menu.grid(row=0, column=3, sticky='e', padx=(ui_theme.INLINE, 0))
        self.archive_tree = ui_theme.table(tab, columns(tab, [
            ('title', i18n.t('table.title'), 260, True), ('author', i18n.t('table.author'), 100, False),
            ('files', i18n.t('table.media_transcripts'), 95, False), ('source', i18n.t('table.source'), 260, True)]),
                                           empty=i18n.t('common.no_match'))
        self.archive_tree.container.grid(row=1, column=0, sticky='nsew')
        pin_rows(self.archive_tree, 4)
        self.archive_tree.ellipsize(middle=('source',))
        self.archive_tree.bind('<<TreeviewSelect>>', lambda _: self.archive_selection())
        ui_theme.bind_menu(self.archive_tree, self.archive_menu_items)
        self.table_actions(self.archive_tree, self.open_archive)
        self.archive_search.trace_add('write', lambda *_: self.refresh_archives())
        # Nothing archived: one short line; while archiving is off, the way to its switch (settings page).
        self.archive_empty = ui_theme.empty_state(
            tab, i18n.t('library.archive.empty'), ui_theme.ICON_LIBRARY,
            action=(i18n.t('library.archive.go_settings'), lambda: self.show_settings('downloads')))
        self.archive_empty.grid(row=0, column=0, rowspan=2, sticky='nsew')
        self.archive_empty.grid_remove()
        self.archive_enabled.trace_add('write', lambda *_: self.show_archive_action())
        self.show_archive_action()

    def show_archive_action(self):
        """Go to Settings under the empty archive only while archiving is off (then nothing gets archived)."""
        button = getattr(getattr(self, 'archive_empty', None), 'action_button', None)
        if button is None:
            return
        try:
            wanted = not self.archive_enabled.get()
            if wanted and not button.winfo_manager():
                button.pack(pady=(ui_theme.GAP, 0))
            elif not wanted and button.winfo_manager():
                button.pack_forget()
        except tk.TclError:
            pass

    def refresh_library(self):
        # robust-6: a locked or broken history must not stop the window (at start-up neither): the
        # error goes to the log and into the warning strip, and the next refresh tries again.
        # (1.7.0 U1) A lock held by another program is no error: the lists stay as they are, the
        # strip says calmly that it is retried, and library_watch tries again by itself.
        busy_before = self.library_busy_count
        try:
            search = self.history_search.get().strip()
            jobs = self.library.history(self.history_limit, search=search)
            total = self.library.history_count(search) if len(jobs) >= self.history_limit else len(jobs)
            # iids are the record ids; fill() keeps the selection of a row that still exists.
            self.history_tree.fill((row['id'], history_row(row)) for row in jobs)
            self.fill_subscriptions(self.library.subscriptions())
            self.show_history_count(len(self.history_tree.get_children()), total, search)
            self.history_selection()
            self.subscription_selection()
            self.refresh_archives()
        except Exception as error:
            if is_busy(error):
                self.library_busy(error)
                return
            applog.get('ui').exception('refresh_library failed')
            self.show_library_problem(i18n.t('library.unreadable', error=messages.error_text(error)))
        else:
            if self.library_waiting and self.library_busy_count == busy_before and not self.history_writer.waiting():
                self.library_settled()
            self.show_library_problem('')

    def show_library_problem(self, text):
        """The notice bar above the views: ``text`` (a read error, warn) with Refresh; else the calm busy line
        while the library is locked (1.7.0 U1; idle tone, no button: it goes by itself); else the warning of
        open_library (a temporary library, a file set aside, PLAN 3-10) with Got it, until it is closed."""
        if text and text != self.library_problem:
            self.append_log(text)       # once per new error, not at every refresh
        self.library_problem = text
        notice = getattr(self, 'library_notice', None)
        if notice is None:
            return
        warning = '' if self.library_warning_dismissed else messages.show(self.library_warning)
        try:
            if text:
                notice.show(text, [(i18n.t('common.refresh'), self.refresh_library)], tone='warn')
            elif getattr(self, 'library_waiting', False):
                notice.show(i18n.t('library.busy'), tone='idle')
            elif warning:
                notice.show(warning, [(i18n.t('common.got_it'), self.dismiss_library_warning)], tone='warn')
            else:
                notice.hide()
        except tk.TclError:
            pass

    def dismiss_library_warning(self):
        """Got it on the open_library warning: the bar goes for this session (a refresh cannot change it)."""
        self.library_warning_dismissed = True
        self.show_library_problem(self.library_problem)

    # ------------------------------------------------------------ (1.7.0 U1) a locked library
    def library_busy(self, error=None):
        """A history read or write met a lock held by another program: one log line, the calm
        notice on the download page and the strip of the library page, and a look again in
        LIBRARY_RETRY_MS (library_watch).  Never waits and never opens a dialog."""
        self.library_waiting = True
        self.library_busy_count += 1
        if not self.library_busy_logged:
            self.library_busy_logged = True
            self.append_log(i18n.t('log.library.busy', error=messages.error_text(error) if error else 'database is locked'))
        self.show_library_problem(self.library_problem)
        self.show_library_notice()
        if self.library_watch_after is None and not self.closing:
            try:
                self.library_watch_after = self.after(LIBRARY_RETRY_MS, self.library_watch)
            except tk.TclError:
                pass        # the window is gone

    def on_library_busy(self, _value=None):
        """The event a worker thread sends when one of its history writes had to wait."""
        self.library_busy()

    def show_library_notice(self):
        """The download page's notice line says that the library is retried, unless another
        notice (the undo of 开始下载, the YouTube wait) holds it; hide_notice brings it back."""
        if not self.library_waiting or self.notice_kind not in (None, 'library'):
            return
        try:
            self.show_notice('library', i18n.t('library.busy'))
        except (AttributeError, tk.TclError):
            pass        # the download page is not built yet

    def library_watch(self):
        """Every LIBRARY_RETRY_MS while the library is locked: once nothing waits to be written,
        read the lists again; when that works too, the notice and the strip go."""
        self.library_watch_after = None
        if self.closing or not self.library_waiting:
            return
        if self.history_writer.waiting():
            self.library_watch_after = self.after(LIBRARY_RETRY_MS, self.library_watch)
            return
        self.refresh_library()          # settles, or (a lock again) library_busy() re-arms this watch

    def library_settled(self):
        """The lock is gone and nothing waits to be written: notice and strip go, one log line."""
        self.library_waiting = False
        self.library_busy_logged = False
        _cancel_after(self, self.library_watch_after)
        self.library_watch_after = None
        written = self.history_writer.written
        self.history_writer.written = 0
        if written:
            self.append_log(i18n.t('log.library.back', count=written))
        self.show_library_problem(self.library_problem)
        if self.notice_kind == 'library':
            self.hide_notice('library')

    def library_closing(self):
        """The window closes: writes that still wait for the lock are given up (logged)."""
        _cancel_after(self, self.library_watch_after)
        self.library_watch_after = None
        left = self.history_writer.close()
        if left:
            applog.get('ui').warning(i18n.t('log.library.lost', count=left))

    def history_write(self, task):
        """Run a history write that must not be lost: now, or (library locked) later in the
        HistoryWriter, with the calm notice.  Other errors are raised as before."""
        if self.history_writer.run(task):
            return True
        self.library_busy()
        return False

    def history_create(self, create):
        """A new job record: the id from ``create()`` now, or a PendingJob whose id the HistoryWriter
        fills in once the library can be written (every later write of the job waits behind it)."""
        pending = PendingJob()

        def task():
            try:
                pending.id = create()
            except sqlite3.OperationalError as error:
                if not is_busy(error):
                    pending.lost = True
                raise
            except Exception:
                pending.lost = True
                raise
        if self.history_write(task):
            return pending.id
        return pending

    # ------------------------------------------------------------ (1.7.0 U2) search and paging
    def history_search_changed(self):
        _cancel_after(self, self.history_search_after)
        try:
            self.history_search_after = self.after(HISTORY_SEARCH_MS, self.run_history_search)
        except tk.TclError:
            self.history_search_after = None

    def run_history_search(self):
        self.history_search_after = None
        self.history_limit = HISTORY_PAGE       # a new search starts with the newest page again
        self.refresh_history()

    def more_history(self):
        self.history_limit += HISTORY_PAGE
        self.refresh_history()

    def refresh_history(self):
        """Only the history list (search, 加载更多); a lock is handled like refresh_library."""
        try:
            search = self.history_search.get().strip()
            rows = self.library.history(self.history_limit, search=search)
            total = self.library.history_count(search) if len(rows) >= self.history_limit else len(rows)
            self.history_tree.fill((row['id'], history_row(row)) for row in rows)
            self.show_history_count(len(rows), total, search)
            self.history_selection()
        except Exception as error:
            if is_busy(error):
                self.library_busy(error)
                return
            applog.get('ui').exception('refresh_history failed')
            self.show_library_problem(i18n.t('library.unreadable', error=messages.error_text(error)))

    def show_history_count(self, shown, total, search=''):
        """The toolbar's count (Total: N, or Found: N for a search) and Load more while more exist; the whole
        view is the empty state when there is no job and no search."""
        if search:
            text = i18n.t('library.history.found', total=total)
        else:
            text = i18n.t('library.history.count', total=total) if total else ''
        try:
            self.history_count.configure(text=text)
            ui_theme.show(self.history_more, shown < total)
            empty = not total and not search
            ui_theme.show(self.history_empty, empty)
            for widget in (self.history_bar, self.history_footer):
                ui_theme.show(widget, not empty)
        except (AttributeError, tk.TclError):
            pass

    def archive_selection(self):
        """(2.0.0 S4) Open folder follows the selection of the archive table, never the busy state (it only opens
        Explorer, as the download page's Open folder).  Called first by history_selection (so on every set_busy)
        and on every selection change of the table; never raises."""
        try:
            ui_theme.set_enabled(self.archive_open, bool(self.archive_tree.selection()))
        except (AttributeError, tk.TclError):
            pass

    def history_selection(self):
        """Details and Continue for the selected job; never raises (set_busy calls it).

        (1.8.1) First of all it sets the busy state of the subscription tools (subscription_tools), so no error
        of the history widgets below can skip it.  (2.0.0 S4) Before that the archive view's own hook,
        archive_selection.  The record read here is kept for history_menu_items (opening the menu reads nothing)."""
        self.archive_selection()
        self.subscription_tools()
        selected = self.history_tree.selection()
        record, note = None, ''
        if selected:
            try:
                record = self.library.job(selected[0])
                note = self.history_note_text(record)
            except Exception as error:
                if is_busy(error):
                    self.library_busy(error)        # (1.7.0 U1) read again by library_watch
                else:
                    applog.get('ui').exception('history record could not be read')
                record, note = None, i18n.t('library.history.unreadable')
        self._history_record = (selected[0] if selected else None, record)
        try:
            self.history_note.configure(text=note)
            ui_theme.set_enabled(self.history_resume, self.resumable(record) and not self.busy)
        except tk.TclError:
            pass

    @staticmethod
    def resumable(record):
        """A job that Continue can take up: read, and neither running nor completed."""
        return record is not None and record['status'] not in ('running', 'completed')

    def history_record(self):
        """The record of the selected job as history_selection read it; None when nothing (readable) is selected."""
        selected = self.history_tree.selection()
        iid, record = self._history_record
        return record if selected and selected[0] == iid else None

    def history_menu_items(self):
        """The history view's ⋯ and the right click of its table (00-system 2.10)."""
        record = self.history_record()
        usable = record is not None and not self.busy
        return [ui_theme.MenuItem(i18n.t('library.view_results'), self.view_history, enabled=usable),
                ui_theme.MenuItem(i18n.t('library.resume'), self.resume_history,
                                  enabled=usable and self.resumable(record)),
                ui_theme.SEPARATOR,
                ui_theme.MenuItem(i18n.t('common.refresh'), self.refresh_library)]

    @staticmethod
    def history_note_text(record):
        """One line about a job: saved pages (collect jobs), successful downloads, the last error.

        A download job's error is the engine's English line; the note shows the short reason the
        task row showed (SPEC 3.11; stored with the line, errors[-1]['reason']), else the reason
        explain_error finds for it, never the raw line.  A download counts once per (link, format)
        (review-e2e-4); a link counts once however many formats of it finished.

        (2.0.0) The stored error and reason are messages or raw text (S2-DESIGN.md 1.2), shown in the
        running language with messages.show.
        """
        data = record['data']
        pairs = [pair for pair in data.get('done') or [] if isinstance(pair, list) and len(pair) == 2]
        completed = len(pairs)
        if record['kind'] != 'collect':
            completed = len({str(pair[0]) for pair in pairs})
            try:
                completed = max(completed, sum(1 for entry in job_downloads(data) if entry['done']))
            except Exception:
                pass
        done = i18n.t('library.history.downloaded', completed=completed)
        if record['kind'] == 'collect':
            text = i18n.t('library.history.pages', pages=len(record['pages']), done=done)
        else:
            text = done
        errors = data.get('errors') or []
        if errors:
            last = errors[-1]
            error, reason = (last.get('error'), last.get('reason')) if isinstance(last, dict) else (last, None)
            error = error if isinstance(error, str) else str(error)
            if record['kind'] != 'collect':
                if isinstance(reason, str) and messages.zh(reason):
                    error = reason
                else:
                    plain = messages.zh(error)
                    error = explain_error(error, bot_check=bool(BOT_CHECK_PATTERN.search(plain)),
                                          stale=bool(STALE_PATTERN.search(plain)))
            # a collector message, a stored reason or explain_error's result; the 1.7.0 cut of the shown text
            text += i18n.t('library.history.last_error', error=messages.show(error)[:130])
        return text

    def load_collection_record(self, record):
        raw = record['data']['options']
        self.collect_urls.delete('1.0', 'end')
        self.collect_urls.insert('1.0', '\n'.join(raw['urls']))
        import collection_ui        # not at module level: collection_ui imports this module
        self.collect_mode.set(collection_ui.MODE_DYNAMIC if raw.get('dynamic') else collection_ui.MODE_STATIC)
        self.collect_scrolls.set(str(raw.get('scrolls', 0)))
        self.collect_pages_limit.set(str(raw.get('max_pages', 1)))
        self.collect_selector.set(raw.get('selector', ''))
        self.collected_pages = record['pages']
        self.collected_links = capped_links(record['pages'])
        self.render_collection_links()
        self.refresh_collect_actions()
        self.set_collect_status(i18n.t('collect.status.from_history', pages=len(record['pages']),
                                       links=len(self.collected_links))
                                + (i18n.t('library.cap_note', limit=MAX_LINKS) if len(self.collected_links) >= MAX_LINKS
                                   else ''), 'idle')

    def library_job(self, job):
        """The record of a job the owner clicked; None (after a hint) when another program holds
        the library (1.7.0 U1): the click is not repeated by itself."""
        try:
            return self.library.job(job)
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.library_busy(error)
            try:
                self.history_note.configure(text=i18n.t('library.busy_click'))
            except tk.TclError:
                pass
            return None

    def view_history(self):
        if self.busy or not self.history_tree.selection():
            return
        record = self.library_job(self.history_tree.selection()[0])
        if record is None:
            return
        if record['kind'] == 'collect':
            self.load_collection_record(record)
            self.show_page('collect')
        else:
            # The links go into the box; the format, folder and other choices stay as they are.
            raw = record['data']['options']
            self.add_links('\n'.join(raw.get('urls') or []))
            self.download_referers.update(raw.get('referers') or {})
            self.show_page('download')
            self.hint_history_loaded()

    def resume_history(self):
        if self.busy or not self.history_tree.selection():
            return
        record = self.library_job(self.history_tree.selection()[0])
        if record is None or record['status'] in ('running', 'completed'):
            return
        if record['kind'] == 'collect':
            raw = dict(record['data']['options'])
            raw.update(proxy=self.proxy.get().strip(), resume=record['data']['checkpoint'])
            try:
                options = CollectOptions(**raw).validate()
            except ValueError as error:
                messagebox.showwarning(i18n.t('dialog.resume_collect_failed'), messages.error_text(error), parent=self)
                return
            self.load_collection_record(record)
            self.active_subscription = record['data'].get('subscription')
            self.launch_collection(options, record['id'])
            self.show_page('collect')
        else:
            raw = record['data']['options']
            # Every download of the job, one per (link, format) (review-e2e-4): a link that was
            # added again in another format during the job is resumed in both formats.
            first, rest = resume_plan(job_downloads(record['data']))
            if not first:
                messagebox.showinfo(i18n.t('dialog.resume.done.title'), i18n.t('dialog.resume.done.text'), parent=self)
                return
            # The job's own options (and those of links added in another format or folder), with the
            # current network and batch preferences.  The link box and the form are not touched; songs
            # downloaded meanwhile are skipped by the engine as already downloaded.
            try:
                urls, options, overrides = self.resume_launch(raw, first)
                later = [(entry['url'], self.resume_options(entry['options'], [entry['url']])) for entry in rest]
            except ValueError:
                messagebox.showwarning(i18n.t('dialog.resume.failed.title'), i18n.t('dialog.resume.failed.text'),
                                       parent=self)
                return
            if self.launch(urls, options, overrides) and later:
                self.resume_later(later)
            self.show_page('download')

    def resume_launch(self, raw, entries):
        """(urls, options, overrides) that start ``entries`` (one per link) with the job's options
        ``raw``; a link whose stored options differ from the job's gets them as its override."""
        base = {key: value for key, value in raw.items() if key not in JOB_OPTION_KEYS}
        urls = [entry['url'] for entry in entries]
        options = self.resume_options(raw, urls)
        overrides = {entry['url']: self.resume_options(entry['options'], [entry['url']])
                     for entry in entries if entry['options'] != base}
        return urls, options, overrides

    def resume_later(self, later):
        """The same links in other formats, [(url, Options)], join the resumed job's queue as soon
        as it is open (Runner.enqueue takes nothing before the worker has built its items).  They are
        written into the new job's record at once, so a stop before they ran leaves them to a later
        继续未完成.  If the run ended before they could join (not stopped), they start as the next job."""
        runner = self.runner
        if runner is None or not hasattr(runner, 'enqueue'):
            return
        job = self.download_job
        if job:
            library = self.library
            for url, options in later:
                # (1.7.0 U1) A locked library: written later, in this order (HistoryWriter).
                own = {url: options.to_dict()}
                try:
                    self.history_write(lambda url=url, own=own: library.extend_job(require_job(job), [url], own))
                except Exception:
                    applog.get('ui').exception('resume: the other formats could not be recorded')
                    break
        self._resume_later_step(runner, list(later))

    def _resume_later_step(self, runner, later):
        if self.closing or not later:
            return
        same_run = self.busy and self.operation == 'download' and self.runner is runner
        if runner.cancelled.is_set() or (not same_run and (self.busy or self.runner is not runner)):
            # Stopped, or another task took the window meanwhile: the record keeps them for later.
            self.append_log(i18n.t('log.resume.dropped', count=len(later)))
            return
        if same_run and runner.opened and not runner.closed:
            left, added = [], 0
            for url, options in later:
                accepted = runner.enqueue([url], options)
                if accepted:
                    self.append_rows(accepted, options)
                    added += len(accepted)
                elif runner.closed or runner.cancelled.is_set():
                    left.append((url, options))
                # else: this (link, format) is already part of the run
            if added:
                self.append_log(i18n.t('log.resume.later', count=added))
                self.totals_changed()       # header, window title and sidebar count them (final-e2e-ui-2)
            later = left
            if not later:
                return
        if not same_run:
            # The run ended before they could join: they start as the next job, the same way.
            entries = [{'url': url, 'options': options} for url, options in later]
            first, rest = resume_plan(entries)
            base = first[0]['options']
            launch = self.launch([entry['url'] for entry in first], base,
                                 {entry['url']: entry['options'] for entry in first[1:] if entry['options'] != base})
            if launch and rest:
                self.resume_later([(entry['url'], entry['options']) for entry in rest])
            return
        try:
            self.after(RESUME_POLL, self._resume_later_step, runner, later)
        except tk.TclError:
            pass        # the window is gone

    def resume_options(self, raw, urls):
        """Options of a stored download job (or one of its overrides) for a resume; ValueError when unusable."""
        if not isinstance(raw, dict):
            raise ValueError('no options')
        return Options.from_dict(raw, proxy=self.proxy.get().strip(), cookies=self.cookies.get().strip(),
                                 batch_safe=self.batch_safe.get(), skip_downloaded=self.skip_downloaded.get(),
                                 archive_contexts=self.collection_contexts(urls) if raw.get('archive_root') else {})

    def save_subscription(self):
        """The collect page's Subscribe (its only entry, M6): a subscription of the current collect settings,
        named after its first page (M5; ⋯ → Rename… names it), with the defaults of the settings page (M4)."""
        if self.busy:
            return
        try:
            options = self.collection_options()
            name = options.urls[0][:65]
            values = (name, asdict(options), self.subscription_interval.get(), self.subscription_media_only.get(),
                      self.subscription_first_new.get())
            try:
                key = self.library.subscription(*values)
            except sqlite3.OperationalError as error:
                if not is_busy(error):
                    raise
                # (1.7.0 U1) Saved by the HistoryWriter once the library can be written; listed then.
                library = self.library
                self.history_writer.defer(lambda: library.subscription(*values))
                self.library_busy(error)
                self.append_log(i18n.t('log.subscription.deferred', name=name))
                return
            self.refresh_library()
            self.subscription_tree.selection_set(key)
            self.subscription_tree.see(key)
            self.subscription_selection()
            self.library_tabs.set(TAB_SUBSCRIPTIONS)
            self.show_page('library')
            self.append_log(i18n.t('log.subscription.saved', name=name))
        except (OSError, ValueError) as error:
            messagebox.showwarning(i18n.t('dialog.subscription.save_failed'), messages.error_text(error), parent=self)

    def selected_subscription(self):
        selected = self.subscription_tree.selection()
        if not selected:
            return None
        return next((r for r in self.library.subscriptions() if r['id'] == selected[0]), None)

    def fill_subscriptions(self, rows):
        """Fill the subscription table with ``rows`` (Library.subscriptions()); select the first row when none is
        selected (the view always has a current subscription, subscriptions.md 6.1); with no subscription at all
        the view is its empty state."""
        tree = self.subscription_tree
        tree.fill((row['id'], subscription_row(row, checking=self.subscription_checking(row['id']))) for row in rows)
        children = tree.get_children()
        if children and not tree.selection():
            tree.selection_set(children[0])
            tree.see(children[0])
        self.show_subscription_empty(not children)

    def show_subscription_empty(self, empty):
        """No subscription: only the empty state; else the toolbar, both tables and the new-items row.  The empty state
        lies over the (empty) subscription table, which stays gridded: studio.focus_page and the view switch give the
        focus to the table, and a table that is not mapped takes none (it would stay on a hidden page)."""
        try:
            ui_theme.show(self.subscription_empty, empty)
            for widget in (self.subscription_bar, self.pending_bar, self.pending_tree.container):
                ui_theme.show(widget, not empty)
        except (AttributeError, tk.TclError):
            pass

    def refresh_subscription_rows(self):
        """The schedule words again (Checking, Due) without touching the new items: only when a row changed,
        so the selection and the new-items table stay as they are.  Never raises."""
        try:
            rows = self.library.subscriptions()
            tree = self.subscription_tree
            wanted = [(row['id'], tuple(str(cell) for cell in subscription_row(row, self.subscription_checking(row['id']))))
                      for row in rows]
            shown = [(iid, tuple(str(cell) for cell in tree.full_values(iid))) for iid in tree.get_children()]
            if wanted != shown:
                self.fill_subscriptions(rows)
                self.subscription_selection()
        except Exception as error:
            if not is_busy(error):
                applog.get('ui').exception('the subscription rows could not be refreshed')

    def subscription_action(self, action):
        """Run a subscription action; a locked library (1.7.0 U1) does nothing but the calm notice and the
        one-line reason (the owner clicks again later)."""
        try:
            action()
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.library_busy(error)
            self.subscription_hint(i18n.t('library.busy_click'))

    def subscription_selection(self):
        try:
            self._subscription_selection()
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.library_busy(error)

    def _subscription_selection(self):
        row = self.selected_subscription()
        # Positional iids: an old selection would point at other entries.  The entries the owner had
        # picked are picked again by their link, so a refresh (after a background check, a download
        # or a refresh) does not throw a multi-selection away.
        previous = getattr(self, 'visible_pending', [])
        picked = {previous[int(n)]['url'] for n in self.pending_tree.selection()
                  if n.isdigit() and int(n) < len(previous)}
        self.visible_pending = self.library.pending(row['id']) if row else []
        self.pending_tree.selection_remove(self.pending_tree.selection())
        # "Nothing new" speaks of a subscription: no hint while none is selected (subscriptions.md 6.2).
        self.pending_tree.set_empty(i18n.t('library.pending.empty') if row else '')
        self.pending_tree.fill((str(n), (messages.show(item['title']), item['url']))
                               for n, item in enumerate(self.visible_pending))
        again = [str(n) for n, item in enumerate(self.visible_pending) if item['url'] in picked]
        if again:
            self.pending_tree.selection_set(again)
        # The new count beside the table it counts; the stored last_error (a message, also the clauses of
        # collector errors) in the running language: why the new items may be incomplete.
        count = i18n.t('library.pending.count', count=len(self.visible_pending)) if row else ''
        error = row['data']['last_error'] if row else None
        self.pending_count.configure(text=count)
        self.subscription_note.configure(text=messages.show(error) if error else '', text_color=ui_theme.DANGER)
        self.subscription_tools()

    def subscription_hint(self, text):
        """Say on the subscription view why an action did nothing (the note line, until the selection changes)."""
        self.subscription_note.configure(text=text, text_color=ui_theme.WARN)

    def check_subscription(self, row=None, automatic=False):
        if self.busy or self.closing:
            return
        if row is None:
            try:
                row = self.selected_subscription()
            except sqlite3.OperationalError as error:
                if not is_busy(error):
                    raise
                self.library_busy(error)        # (1.7.0 U1)
                self.subscription_hint(i18n.t('library.busy_click'))
                return
        if not row:
            return          # nothing selected: the button and the menu item are grey
        try:
            options = CollectOptions(**row['data']['options'])
            options.proxy = self.proxy.get().strip()
            options.validate()
            self.active_subscription = row['id']
            # (2.0.0) The job is marked as the subscription's; its title is the subscription's own
            # name ('' for the untitled one); history_title shows '订阅：<name>'.
            self.launch_collection(options, title=row['name_text'], background=automatic, via='subscription')
            if automatic:
                # robust-13: the download page says why it cannot start right now, the collect page
                # why its controls are disabled.
                name = messages.show(row['name'])
                self.hint_subscription_check(name)
                self.refresh_collect_note(background=name)
            else:
                self.show_page('collect')
        except Exception as error:
            # Also a locked history (sqlite3.Error from create_job): the timer must not retry this
            # subscription every 15 s, each try blocking the window for the database timeout.
            settings_error = isinstance(error, (OSError, ValueError))
            if not settings_error:
                applog.get('ui').exception('subscription check could not start')
            self.active_subscription = None
            self.subscription_backoff[row['id']] = time.time() + max(RETRY_BACKOFF, row.get('interval') or 0)
            if settings_error:
                # Recorded on the subscription as in 1.5.1.  Not after a history error: writing the
                # record would wait for the same lock once more.
                try:
                    self.library.finish_subscription(row['id'], [], False, messages.message_of(error))
                except Exception:
                    applog.get('ui').exception('finish_subscription failed')
            self.append_log(i18n.t('log.subscription.failed', name=messages.show(row['name']),
                                   error=messages.error_text(error)))
            if not automatic:
                messagebox.showwarning(i18n.t('dialog.subscription.not_started'), messages.error_text(error), parent=self)
        self.refresh_subscription_rows()        # Checking on the started one (or its stored error)

    def subscription_tick(self):
        try:
            if not self.busy and not self.closing and not self.smoke_mode:
                for row in sorted(self.library.subscriptions(), key=lambda r: r['next_check']):
                    if row['enabled'] and row['interval'] and row['next_check'] <= time.time() \
                            and self.subscription_backoff.get(row['id'], 0) <= time.time():
                        self.check_subscription(row, automatic=True)
                        break
        except Exception as error:
            # One failing tick (a locked history, say) must not end the scheduled checks.  (1.7.0
            # U1) A lock held by another program is not logged every 15 s: the next tick looks again.
            if not is_busy(error):
                applog.get('ui').exception('subscription tick failed')
        finally:
            if not self.closing:
                self.refresh_subscription_rows()        # Due and Checking follow the clock
                try:
                    self.after(15000, self.subscription_tick)
                except tk.TclError:
                    pass        # the window is gone

    def subscription_menu_items(self):
        """The subscription view's ⋯ and the right click of its table (subscriptions.md 4.2).  A locked library
        greys the row actions; Refresh stays."""
        try:
            row = self.selected_subscription()
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.library_busy(error)
            row = None
        usable = row is not None and not self.busy
        paused = row is not None and not row['enabled']
        schedules = tuple(ui_theme.MenuItem(i18n.label('interval', seconds),
                                            lambda seconds=seconds: self.set_schedule(seconds), enabled=usable,
                                            checked=row is not None and row['interval'] == seconds)
                          for seconds in settings.SCHEDULES)
        toggle = i18n.t('library.subscription.resume') if paused else i18n.t('library.subscription.pause')
        return [ui_theme.MenuItem(i18n.t('library.subscription.check_now'), self.check_subscription,
                                  enabled=usable and not self.closing),
                ui_theme.MenuItem(toggle, self.toggle_subscription, enabled=usable),
                ui_theme.MenuItem(i18n.t('table.schedule'), enabled=usable, children=schedules),
                ui_theme.MenuItem(i18n.t('library.subscription.rename'), self.rename_subscription, enabled=usable),
                ui_theme.MenuItem(i18n.t('common.refresh'), self.refresh_library),
                ui_theme.SEPARATOR,
                ui_theme.MenuItem(i18n.t('library.subscription.delete'), self.delete_subscription, danger=True,
                                  enabled=usable and not self.subscription_checking(row['id']))]

    def pending_menu_items(self):
        """The right click of the new-items table: the mirror of its two buttons (select all is Ctrl+A)."""
        picked = len(self.pending_tree.selection())
        return [ui_theme.MenuItem(self.add_text(picked), self.queue_subscription,
                                  enabled=bool(picked) and self.can_add_collected()),
                ui_theme.MenuItem(i18n.t('common.ignore'), self.ignore_pending,
                                  enabled=bool(picked) and not self.busy)]

    def select_all_pending(self, _event=None):
        """Ctrl+A on the new-items table."""
        self.pending_tree.selection_set(self.pending_tree.get_children())
        return 'break'

    def toggle_subscription(self):
        self.subscription_action(self._toggle_subscription)

    def _toggle_subscription(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], not row['enabled'], row['interval'])
            self.refresh_library()

    def set_schedule(self, seconds):
        """⋯ → Schedule ▸ ``seconds`` (one of settings.SCHEDULES) for the selected subscription; its current
        schedule writes nothing (configure_subscription would set its next check anew)."""
        self.subscription_action(lambda: self._set_schedule(seconds))

    def _set_schedule(self, seconds):
        row = self.selected_subscription()
        if row and not self.busy and row['interval'] != seconds:
            self.library.configure_subscription(row['id'], row['enabled'], seconds)
            self.refresh_library()

    def queue_subscription(self):
        """Add to downloads: the picked new items go to the download page, also while a download runs
        (can_add_collected, as the collect page's Add to downloads)."""
        if not self.can_add_collected():
            return
        items = [self.visible_pending[int(n)] for n in self.pending_tree.selection()
                 if n.isdigit() and int(n) < len(self.visible_pending)]
        if items:
            self.queue_collected_items(items)

    def subscription_tools(self):
        """(2.0.0 S4, subscriptions.md 7.3) Check now, Add to downloads and Ignore, computed here only from the
        busy state and the selections; the one solid button follows the stage: Add to downloads while the
        selected subscription has new items, else Check now (with none selected it stays as it was).  set_busy -> history_selection calls this first,
        so do both tables' selection changes.  Never raises."""
        try:
            idle = not self.busy
            picked = len(self.pending_tree.selection())
            has_row = bool(self.subscription_tree.selection())
            has_new = has_row and bool(self.pending_tree.get_children())
            ui_theme.set_enabled(self.subscription_check, has_row and idle)
            ui_theme.set_enabled(self.pending_queue, bool(picked) and self.can_add_collected())
            ui_theme.set_enabled(self.pending_ignore, bool(picked) and idle)
            text = self.add_text(picked)
            if self.pending_queue.cget('text') != text:
                self.pending_queue.configure(text=text)
            if has_row:     # (final polish) no subscription picked: the solid one stays where it was
                self.subscription_check.set_kind('soft' if has_new else 'primary')
                self.pending_queue.set_kind('primary' if has_new else 'soft')
        except (AttributeError, tk.TclError):
            pass        # the page is not built yet, or the window is gone

    def ignore_pending(self):
        self.subscription_action(self._ignore_pending)

    def _ignore_pending(self):
        """(1.8.1) Ignore: the picked new items leave this subscription's pending list (they stay seen, so a
        later check keeps them out).  One click, no dialog, as Add to downloads."""
        if self.busy:
            return
        selected = self.subscription_tree.selection()
        items = [self.visible_pending[int(n)] for n in self.pending_tree.selection()
                 if n.isdigit() and int(n) < len(self.visible_pending)]
        if not items:
            return
        row = self.selected_subscription()
        if not selected or row is None:
            self.refresh_library()
            self.subscription_hint(i18n.t('library.subscription.gone'))
            return
        count = self.library.ignore_entries(row['id'], [item['url'] for item in items])
        self.refresh_library()
        self.append_log(i18n.t('library.pending.log_ignored', count=int(count), name=messages.show(row['name'])))

    def delete_subscription(self):
        self.subscription_action(self._delete_subscription)

    def subscription_checking(self, key):
        """(1.8.1) A check of this subscription runs now or waits for the library (collect_pending)."""
        pending = self.collect_pending
        return self.active_subscription == key or bool(pending and pending[3] == key)

    def _delete_subscription(self):
        """(1.8.1) Delete subscription, after a confirmation whose default answer is no.  A subscription whose own
        check runs (or waits for the library) is never offered for deletion (delete_busy).  The dialog
        runs the event loop, so its check and the busy state are looked at again after it: delete_busy
        only when this subscription is being checked, delete_task_busy when another task (a download,
        another subscription's check) made the window busy."""
        row = self.selected_subscription()
        if row and self.subscription_checking(row['id']):
            self.subscription_hint(i18n.t('library.subscription.delete_busy'))
            return
        if self.busy or not row:
            return
        if not messagebox.askyesno(i18n.t('library.subscription.delete_title'),
                                   i18n.t('library.subscription.delete_text', name=messages.show(row['name']),
                                          pending=int(row['pending'])),
                                   icon='warning', default='no', parent=self):
            return
        # The dialog runs the event loop: the subscription timer may have started a check meanwhile.
        if self.subscription_checking(row['id']):
            self.subscription_hint(i18n.t('library.subscription.delete_busy'))
            return
        if self.busy:
            self.subscription_hint(i18n.t('library.subscription.delete_task_busy'))
            return
        found = self.library.delete_subscription(row['id'])
        self.subscription_backoff.pop(row['id'], None)
        self.refresh_library()
        if found:
            self.append_log(i18n.t('library.subscription.log_deleted', name=messages.show(row['name']),
                                   pending=int(row['pending'])))
        else:
            self.subscription_hint(i18n.t('library.subscription.gone'))

    def rename_subscription(self):
        self.subscription_action(self._rename_subscription)

    def _rename_subscription(self):
        """⋯ → Rename…: a one-line dialog (ui_theme.ask_text) with the current name.  Cancel or an empty entry
        writes nothing.  The dialog runs the event loop: a check that started meanwhile does not drop the typed
        name (rename writes only the name column); a subscription deleted meanwhile says gone."""
        if self.busy:
            return
        row = self.selected_subscription()
        if not row:
            return
        text = ui_theme.ask_text(self, i18n.t('library.subscription.rename_title'),
                                 row['name_text'] or messages.show(row['name']))
        if text is None:
            return
        found = self.library.rename_subscription(row['id'], text)
        self.refresh_library()
        if not found:
            self.subscription_hint(i18n.t('library.subscription.gone'))
            return
        renamed = self.selected_subscription()
        if renamed is not None and renamed['id'] == row['id']:
            self.append_log(i18n.t('library.subscription.log_renamed', name=messages.show(renamed['name'])))

    def collection_contexts(self, urls):
        # Provenance stays available after restarting without embedding body copies per URL.
        try:
            return self.library.contexts(urls)
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            # (1.7.0 U1) A locked library never stops a download: the archive keeps the link as
            # its source, as for a link that was not collected.
            self.library_busy(error)
            self.append_log(i18n.t('log.archive.library_busy'))
            return {}

    def choose_archive(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.archive_root.get() if Path(self.archive_root.get()).is_dir() else str(APP_DIR))
        if path:
            self.archive_root.set(path)
            self.save_settings()

    def archive_pages(self):
        try:
            if not self.archive_root.get().strip():
                raise ValueError(i18n.t('error.archive_root_missing'))
            index = _QueuedIndex(self.library, self.history_writer)       # (1.7.0 U1)
            archive = Archive(self.archive_root.get(), index, log=self.append_log)
            for page in self.collected_pages:
                archive.prepare(page['url'], dict(title=page['title'], source=page['url'], page=page))
            self.append_log(i18n.t('log.archive.pages', count=len(self.collected_pages)))
            if index.deferred:
                self.library_busy()
            self.refresh_archives()
        except (ValueError, OSError) as error:
            messagebox.showerror(i18n.t('dialog.archive_failed'), messages.error_text(error), parent=self)

    def refresh_archives(self):
        selected = self.archive_tree.selection()
        selected_folder = self.visible_archives[int(selected[0])]['folder'] if selected and hasattr(self, 'visible_archives') else None
        term = self.archive_search.get().casefold()
        try:
            stored = self.library.archives()
        except sqlite3.OperationalError as error:
            if not is_busy(error):
                raise
            self.library_busy(error)        # (1.7.0 U1) the list stays; library_watch reads it again
            return
        def searched(r):
            # (final fix round 1, CR1-1) the shown title is searched too when it differs (English fallback title);
            # (1.8.0, MESSAGES.md 6.2) the stored text through messages.zh, the shown one through show.
            stored, shown = messages.zh(r['title']), messages.show(r['title'])
            title = stored if shown == stored else stored + ' ' + shown
            return (title + ' ' + str(r.get('uploader', '')) + ' ' + r['source']).casefold()
        self.visible_archives = [r for r in stored if not term or term in searched(r)]
        self.archive_tree.selection_remove(selected)        # positional iids; re-selected by folder below
        self.archive_tree.fill((str(n), (messages.show(row['title']),
                                         row.get('uploader') or i18n.t('library.archive.not_provided'),
                                         f"{len(row['files'])} / {len(row['transcripts'])}", row['source']))
                               for n, row in enumerate(self.visible_archives))
        for n, row in enumerate(self.visible_archives):
            if row['folder'] == selected_folder:
                self.archive_tree.selection_set(str(n))
        count = len(self.visible_archives)
        self.archive_count.configure(text=i18n.t('library.archive.found', total=count) if term
                                     else i18n.t('library.archive.count', total=count))
        self.show_archive_empty(not stored and not term)
        self.archive_selection()

    def show_archive_empty(self, empty):
        """Nothing archived (and no search): only the empty state, over the empty table (it stays gridded for the
        keyboard focus, as in show_subscription_empty); else the toolbar and the table."""
        try:
            ui_theme.show(self.archive_empty, empty)
            ui_theme.show(self.archive_bar, not empty)
        except (AttributeError, tk.TclError):
            pass

    def selected_archive(self):
        selected = self.archive_tree.selection()
        return self.visible_archives[int(selected[0])] if selected else None

    def open_archive(self):
        """Open folder (the button, a double click or Enter on a row, ⋯): Explorer at the selected item; usable
        whenever an item is selected, also while a task runs."""
        row = self.selected_archive()
        if row:
            try:
                os.startfile(row['folder'])
            except OSError as error:
                messagebox.showerror(i18n.t('dialog.open_failed'), messages.error_text(error), parent=self)

    def archive_menu_items(self):
        """The archive view's ⋯ and the right click of its table (00-system 2.10)."""
        picked = bool(self.archive_tree.selection())
        return [ui_theme.MenuItem(i18n.t('common.open_folder'), self.open_archive, enabled=picked),
                ui_theme.MenuItem(i18n.t('library.archive.attach'), self.attach_transcript,
                                  enabled=picked and not self.busy),
                ui_theme.SEPARATOR,
                ui_theme.MenuItem(i18n.t('common.refresh'), self.refresh_library)]

    def attach_transcript(self):
        """⋯ → Link transcript…: a transcript file is copied into the selected item's folder (the original stays)."""
        row = self.selected_archive()
        if not row or self.busy:
            return
        source = filedialog.askopenfilename(parent=self, title=i18n.t('dialog.transcript.title'),
                                            filetypes=[(i18n.t('dialog.filetype.transcript'), '*.txt *.srt *.md *.vtt')])
        if source:
            try:
                folder = Path(row['folder'])
                index = _QueuedIndex(self.library, self.history_writer)       # (1.7.0 U1)
                Archive(folder.parent.parent, index).attach(folder, source)
                if index.deferred:
                    self.library_busy()
                self.refresh_archives()
            except (ValueError, OSError) as error:
                messagebox.showerror(i18n.t('dialog.attach_failed'), messages.error_text(error), parent=self)
