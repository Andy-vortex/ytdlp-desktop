"""Task recovery, incremental subscriptions, and source archive UI (mixin LibraryUI; 1.6.0 WP5).

The library page is one ui_theme.tabs card with three tabs (WP5.md section C).  Every colour,
font and widget comes from ui_theme.  Never import studio here at module level.

(1.7.0) Every shown text through i18n (I18N.md 8); stored texts (job titles, subscription errors,
reasons) keep their canonical Chinese form and are shown through i18n.localize (I18N.md 9.3); the
tab ids, the interval labels of the Tk variable and the job statuses keep their 1.6.0 values.
"""
from collections import deque
from dataclasses import asdict
from pathlib import Path
import os
import re
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import applog
import i18n
import ui_theme
from collector import MAX_LINKS, CollectOptions
from engine import APP_DIR, BOT_CHECK_PATTERN, STALE_PATTERN, Options, explain_error
from library import HISTORY_PAGE, JOB_OPTION_KEYS, Archive, is_busy, job_downloads, open_library, set_ui_thread, ui_wait
from urls import display

# The labels are looked up from the stored seconds only; the label itself is never stored.
INTERVALS = {'仅手动': 0, '每 15 分钟': 900, '每小时': 3600, '每天': 86400}
STATES = {'running': '进行中', 'completed': '已完成', 'partial': '部分完成', 'interrupted': '已中断'}
# A download job that ended without a single successful download is stored as 'partial' (1.5.1 reads
# that string); the list says what the download page said (polish ui-daily-flow-6).
NOTHING_DONE = '未完成'
# The name a download job gets before the first title arrives ('3条 · 音频 · MP3 320K', also from 1.5.1).
PLACEHOLDER_TITLE = re.compile(r'\d+\s*条 · .+')
RESUME_POLL = 100     # ms between the checks that hand the other formats of resumed links to the run
RESUME_LATER_LOG = '继续未完成：同一链接的其他格式 {count} 条已加入队列'
RESUME_DROPPED_LOG = '继续未完成：同一链接的其他格式 {count} 条没有开始，可在资料管理中再次“继续未完成”'
HISTORY_NOTE = '已完成页不会重采；失败页和中断页可以继续。下载任务恢复未成功的链接。'
SUBSCRIPTION_NOTE = '新增内容会保留，只有下载成功才从待处理列表消失；不会自动下载。'
HISTORY_UNREADABLE = '这条记录暂时无法读取，请稍后点“刷新记录”。'
LIBRARY_UNREADABLE = '历史记录暂时无法读取：{error}'
PICK_SUBSCRIPTION = '请先在上方列表中选择一个订阅。'
NO_SUBSCRIPTION = '还没有订阅：在网页采集页设置好起始网页后点“保存为订阅”。'
PICK_PENDING = '请先在下方选择需要下载的新增条目。'
NO_PENDING = '这个订阅目前没有待处理的新增内容。'
PICK_ARCHIVE = '请先在下方列表中选择一条资料。'
RETRY_BACKOFF = 900     # s: an automatic check that could not start waits at least this long (or its interval)
TAB_PAD = 10        # inside a tab frame (the Tabs card adds 6: a 16 px margin like every card)
TAB_TOP = 2         # below the tab bar, which keeps 10 px under itself
# (1.7.0 U1) Another program holds library.sqlite3: the window never waits for it (library.py
# gives up after about 0.15 s on the window's thread); it says so calmly, writes what must not be
# lost later (HistoryWriter) and looks again every LIBRARY_RETRY_MS.
LIBRARY_BUSY = '资料库正被其他程序占用，稍后自动重试'
LIBRARY_BUSY_LOG = '资料库正被其他程序占用（{error}）：窗口不等待，稍后自动重试；新的记录会在可写入后补写。'
LIBRARY_BUSY_CLICK = '资料库正被其他程序占用，请稍后再点一次。'
LIBRARY_RETRY_MS = 2000
# (1.7.0 U2) The history list: the newest HISTORY_PAGE jobs, more on request, a search the database runs.
HISTORY_SEARCH_MS = 250         # typing pause before the search runs
HISTORY_EMPTY = '下载和采集任务会记录在这里'
HISTORY_NONE_FOUND = '没有标题或链接包含“{term}”的任务'
# (1.7.0) Stored and shown texts of the user's own data: only these app-made forms are localized,
# so a media title or a subscription name that happens to equal a core text stays as it is (I18N.md 9.2).
JOB_TITLE_KEYS = ('value.job_title.', 'value.subscription.untitled')
NAME_KEYS = ('value.subscription.untitled',)
# (final fix round 2, CR2-2) The app-made forms per kind of stored job title: a download job titled by
# its media (data['titled']) has only '{title} 等 {count} 条' (several links), an untitled one only the
# placeholder '{count} 条 · {mode}', a collect job only '订阅：{name}' (else its first start link).
TITLED_JOB_KEYS = ('value.job_title.multi',)
UNTITLED_JOB_KEYS = ('value.job_title.placeholder',)
COLLECT_JOB_KEYS = ('value.job_title.subscription',)
# (final fix round 1, CR1-1; I18N.md 9.3) A stored page or link title equal to the collector's fallback
# page title (网页) is shown in the running language: archive titles and the new-items table, as the
# collect table does (collection_ui.PAGE_TITLE_KEYS).  (final fix round 2, CR2-1) So is the collector's
# title of a start page that is itself a media file (媒体直链).  (final fix round 3, CR3-1) So is the title
# of a media request a dynamic collection captured (动态页面媒体请求).
PAGE_TITLE_KEYS = ('value.page_title.', 'value.link_kind.direct', 'value.link_kind.dynamic_request')


def stored_text(key, **values):
    """(1.7.0) The canonical (Chinese) text of ``key``: what is stored or compared in every
    language (H2), e.g. a subscription job's title '订阅：…' or a library tab id."""
    text = i18n.all_values(key)[0]
    return text.format(**values) if values else text.replace('{{', '{').replace('}}', '}')


# (1.7.0) The library tab ids (stored by snapshot_ui and compared by studio.focus_page): the 1.6.0 names.
TAB_HISTORY = stored_text('library.tab.history')
TAB_SUBSCRIPTIONS = stored_text('library.tab.subscriptions')
TAB_ARCHIVE = stored_text('library.tab.archive')


def text_button_width(parent, text, size='md'):
    """(1.7.0, I18N.md 10.1) The logical width an AppButton needs to show ``text`` in full: the text,
    the label's 1 px border on each side and the corner columns (ui_theme.fitted_width's measure
    without its 72 px floor, so a 1.6.0 width below 72 stays as it was in Chinese)."""
    scale = ui_theme._scaling(parent) or 1.0
    measure = ui_theme._measure_font(parent, ui_theme.font('caption' if size == 'sm' else 'body'))
    needed = measure.measure(text) + 2 + 2 * round(ui_theme.RADIUS * scale)
    return int(-(-needed // scale))


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
    """The name of a job in the history list.  A download job that never got a title keeps the
    placeholder '<n>条 · <format>' in the store (1.5.1 shows it); the list names it after its first
    link instead, like the task rows (polish ui-daily-flow-6).

    (1.7.0) The stored title keeps its form; an app-made one ('3 条 · 音频 · …', '订阅：…',
    '未命名订阅') is shown in the running language (I18N.md 9.3).

    (final fix round 2, CR2-2) Only the forms the app makes for that kind of job: a title the engine
    took from the media itself stays as it is, even when it reads like '订阅：…' or '3 条 · …'."""
    title = str(row.get('title') or '')
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    options = data.get('options') if isinstance(data.get('options'), dict) else {}
    urls = [url for url in options.get('urls') or [] if isinstance(url, str) and url]
    if row.get('kind') != 'download':
        return i18n.localize(title, keys=COLLECT_JOB_KEYS)
    if data.get('titled'):
        # library.py: the first media title, or '{title} 等 {count} 条' when the job has several links.
        return title if len(urls) == 1 else i18n.localize(title, keys=TITLED_JOB_KEYS)
    if not PLACEHOLDER_TITLE.fullmatch(title) or not urls:
        return i18n.localize(title, keys=UNTITLED_JOB_KEYS)
    if len(urls) > 1:
        return i18n.t('value.job_title.multi', title=display(urls[0]), count=len(urls))
    return display(urls[0])


def history_status(row):
    """The status word of a job in the history list (the stored status string is never changed)."""
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    if row.get('kind') == 'download' and row.get('status') == 'partial' and not data.get('completed'):
        return i18n.t('common.unfinished')
    status = row.get('status')
    return i18n.label('job_status', status) if status in STATES else status


def history_row(row):
    """(1.7.0) The four cells of a job in the history list, in the running language."""
    kind = i18n.label('job_kind', 'collect' if row['kind'] == 'collect' else 'download')
    return kind, history_title(row), history_status(row), clock(row['updated'])


def subscription_row(row):
    """(1.7.0) The four cells of a subscription: its name (the app's default name localized), the
    schedule (the label of the stored seconds) or 已暂停, the last check and the pending count."""
    schedule = i18n.label('interval', row['interval']) if row['enabled'] else i18n.t('library.state.paused')
    return (i18n.localize(row['name'], keys=NAME_KEYS), schedule, clock(row['data']['last_check']), row['pending'])


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
        self.archive_enabled = tk.BooleanVar(value=False if self.smoke_mode else settings.get('archive_enabled', False))
        self.archive_root = tk.StringVar(value=str(Path(self.output.get()) / '资料归档') if self.smoke_mode else settings.get('archive_root', str(Path(self.output.get()) / '资料归档')))
        # The zh label of the stored seconds (INTERVALS keeps the zh dict, I18N.md 7); the menu shows it localized.
        self.subscription_interval = tk.StringVar(value=next(name for name, seconds in INTERVALS.items() if seconds == 3600))
        self.subscription_media = tk.BooleanVar(value=True)
        self.subscription_first = tk.BooleanVar(value=True)
        self.subscription_name = tk.StringVar()
        self.archive_search = tk.StringVar()

    def build_library_page(self):
        page = self.page('library', i18n.t('nav.library'), i18n.t('library.subtitle'))
        page.grid_rowconfigure(2, weight=1)
        # robust-6: a history that could not be opened is replaced for this session; say so here.
        self.library_warning_bar = ctk.CTkFrame(page, fg_color=ui_theme.WARN_SOFT, corner_radius=ui_theme.RADIUS)
        self.library_warning_bar.grid_columnconfigure(0, weight=1)
        self.library_warning_label = ui_theme.label(self.library_warning_bar, '', role='caption',
                                                    text_color=ui_theme.WARN, anchor='w')
        self.library_warning_label.grid(row=0, column=0, sticky='ew', padx=12, pady=5)
        self.library_warning_bar.grid(row=1, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        ui_theme.fit(self.library_warning_label, i18n.localize(self.library_warning))
        if not self.library_warning:
            self.library_warning_bar.grid_remove()
        # A keyboard focus left in a tab that is switched away from moves to the new tab's table
        # (final-fix-ui, polish-function-3); the hidden tabs take no Tab focus (ui_theme.Tabs).
        # (1.7.0) The tab ids stay the 1.6.0 names; the bar shows the labels (I18N.md 7, library_tab).
        tables = {TAB_HISTORY: 'history_tree', TAB_SUBSCRIPTIONS: 'subscription_tree', TAB_ARCHIVE: 'archive_tree'}
        ids = [TAB_HISTORY, TAB_SUBSCRIPTIONS, TAB_ARCHIVE]
        self.library_tabs = ui_theme.tabs(page, ids, focus=lambda name: getattr(self, tables.get(name, ''), None),
                                          labels={name: i18n.label('library_tab', name) for name in ids})
        self.library_tabs.grid(row=2, column=0, sticky='nsew')
        history = self.library_tabs.add(TAB_HISTORY)
        subscriptions = self.library_tabs.add(TAB_SUBSCRIPTIONS)
        archive = self.library_tabs.add(TAB_ARCHIVE)
        self.build_history_tab(history)
        self.build_subscription_tab(subscriptions)
        self.build_archive_tab(archive)
        self.refresh_library()
        self.after(1500, self.subscription_tick)

    # Inside a tab frame: 10 px at the sides and the bottom (the Tabs card insets its frames by 6,
    # which makes the 16 px card margin), 2 px at the top (the tab bar keeps 10 below itself), 12 between blocks.
    def build_history_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        bar = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        bar.grid(row=0, column=0, sticky='ew', padx=TAB_PAD, pady=(TAB_TOP, 0))
        bar.grid_columnconfigure(3, weight=1)
        ui_theme.button(bar, i18n.t('library.refresh_list'), self.refresh_library).grid(row=0, column=0, sticky='w')
        self.history_view = ui_theme.button(bar, i18n.t('library.view_results'), self.view_history)
        self.history_view.grid(row=0, column=1, sticky='w', padx=(8, 0))
        resume = i18n.t('library.resume')
        self.history_resume = ui_theme.button(bar, resume, self.resume_history, kind='primary',
                                              width=max(112, text_button_width(bar, resume)))
        self.history_resume.grid(row=0, column=2, sticky='w', padx=(8, 0))
        self.controls += [self.history_view, self.history_resume]
        # (1.7.0 U2) The search runs in the database (title or link), like the archive tab's field.
        self.history_search_entry = ui_theme.entry(bar, textvariable=self.history_search,
                                                   placeholder=i18n.t('library.history.search'), width=230)
        self.history_search_entry.grid(row=0, column=4, sticky='e', padx=(12, 0))
        self.history_search.trace_add('write', lambda *_: self.history_search_changed())
        # One line (fit, the whole text in ui_theme.full_text): a wrapped note made the table jump
        # whenever a job with a long error was selected.
        self.history_note = ui_theme.label(tab, '', role='caption', text_color=ui_theme.MUTED,
                                           anchor='w', justify='left')
        self.history_note.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, ui_theme.GAP))
        ui_theme.fit(self.history_note, i18n.t('library.history.note'))
        self.history_tree = ui_theme.table(tab, columns(tab, [
            ('kind', i18n.t('table.type'), 80, False), ('title', i18n.t('table.task'), 360, True),
            ('status', i18n.t('table.status'), 96, False), ('time', i18n.t('table.updated'), 120, False)]),
                                           empty=i18n.t('library.history.empty'))
        self.history_tree.container.grid(row=2, column=0, sticky='nsew', padx=TAB_PAD, pady=(0, 8))
        pin_rows(self.history_tree, 4)
        self.history_tree.ellipsize()
        self.history_tree.bind('<<TreeviewSelect>>', lambda _: self.history_selection())
        # (1.7.0 U2) Under the table: how many jobs are shown of how many, and 加载更多 while more exist.
        footer = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        footer.grid(row=3, column=0, sticky='ew', padx=TAB_PAD, pady=(0, TAB_PAD))
        footer.grid_columnconfigure(0, weight=1)
        self.history_count = ui_theme.label(footer, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.history_count.grid(row=0, column=0, sticky='ew')
        self.history_more = ui_theme.button(footer, i18n.t('library.history.more'), self.more_history, kind='ghost',
                                            size='sm')
        self.history_more.grid(row=0, column=1, sticky='e')
        footer.grid_rowconfigure(0, minsize=self.history_more.winfo_reqheight())
        self.history_more.grid_remove()

    def build_subscription_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=2)
        tab.grid_rowconfigure(3, weight=3)
        schedule = ui_theme.label(tab, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        schedule.grid(row=0, column=0, sticky='ew', padx=TAB_PAD, pady=(TAB_TOP, 0))
        ui_theme.fit(schedule, i18n.t('library.subscription.schedule_note'))     # (1.7.0) one line, cut with '…'
        # Creation strip, two rows: on one row it needs about 890 px and has 712 at 1000x740.
        strip = ctk.CTkFrame(tab, fg_color=ui_theme.SURFACE_ALT, corner_radius=ui_theme.RADIUS)
        strip.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(8, 0))
        strip.grid_columnconfigure(0, weight=1)
        self.subscription_strip = strip
        name = ui_theme.entry(strip, textvariable=self.subscription_name,
                              placeholder=i18n.t('library.subscription.name_placeholder'),
                              width=280, fg_color=ui_theme.SURFACE)
        name.grid(row=0, column=0, sticky='ew', padx=(12, 0), pady=(12, 0))
        self.subscription_name_entry = name
        # (1.7.0) The variable keeps the zh label (INTERVALS), the menu shows the label of the seconds.
        interval_labels = [i18n.label('interval', seconds) for seconds in INTERVALS.values()]
        interval = ui_theme.option_menu(strip, list(INTERVALS), labels=interval_labels,
                                        variable=self.subscription_interval,
                                        width=max(110, ui_theme.menu_width(strip, interval_labels)))
        interval.grid(row=0, column=1, padx=(8, 0), pady=(12, 0))
        create = ui_theme.button(strip, i18n.t('library.subscription.create'), self.save_subscription, kind='primary')
        create.grid(row=0, column=2, padx=(8, 12), pady=(12, 0))
        self.subscription_create = create
        flags = ctk.CTkFrame(strip, fg_color='transparent', corner_radius=0)
        flags.grid(row=1, column=0, columnspan=3, sticky='w', padx=12, pady=(10, 12))
        media = ui_theme.checkbox(flags, i18n.t('library.subscription.media_only'), variable=self.subscription_media)
        media.grid(row=0, column=0, sticky='w')
        first = ui_theme.checkbox(flags, i18n.t('library.subscription.first_new'), variable=self.subscription_first)
        first.grid(row=0, column=1, sticky='w', padx=(24, 0))
        self.controls += [name, interval, create, media, first]
        # Two stacked blocks (side by side the four columns would be clipped at the default width).
        block = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        block.grid(row=2, column=0, sticky='nsew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        block.grid_columnconfigure(0, weight=1)
        block.grid_rowconfigure(1, weight=1)
        actions = ctk.CTkFrame(block, fg_color='transparent', corner_radius=0)
        actions.grid(row=0, column=0, sticky='ew')
        buttons = [ui_theme.button(actions, i18n.t('library.subscription.check_now'), self.check_subscription,
                                   kind='primary'),
                   ui_theme.button(actions, i18n.t('library.subscription.toggle'), self.toggle_subscription),
                   ui_theme.button(actions, i18n.t('library.subscription.apply_interval'), self.change_interval)]
        for column, button in enumerate(buttons):
            button.grid(row=0, column=column, sticky='w', padx=(0 if column == 0 else 8, 0))
        # Two bounded lists leave room for the new-content queue at minimum window size.
        # (final fix round 1, ENV-4) The Schedule column shows an interval label or 已暂停: as wide as the
        # widest of them needs (I18N.md 10.1 group-label rule; Chinese keeps its 110 px).
        schedule_width = cell_width(block, [i18n.label('interval', seconds) for seconds in INTERVALS.values()]
                                    + [i18n.t('library.state.paused')], 110)
        self.subscription_tree = ui_theme.table(block, columns(block, [
            ('name', i18n.t('table.subscription'), 210, True), ('state', i18n.t('table.schedule'), schedule_width, False),
            ('last', i18n.t('table.last_success'), 115, False), ('pending', i18n.t('table.pending'), 70, False)]),
                                                empty=i18n.t('library.subscription.empty'), height=4)
        self.subscription_tree.container.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        pin_rows(self.subscription_tree, 4)
        self.subscription_tree.ellipsize()
        self.subscription_tree.bind('<<TreeviewSelect>>', lambda _: self.subscription_selection())
        pending = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        pending.grid(row=3, column=0, sticky='nsew', padx=TAB_PAD, pady=(ui_theme.GAP, TAB_PAD))
        pending.grid_columnconfigure(0, weight=1)
        pending.grid_rowconfigure(1, weight=1)
        self.subscription_note = ui_theme.label(pending, '', role='caption', text_color=ui_theme.MUTED, anchor='w')
        self.subscription_note.grid(row=0, column=0, sticky='ew', padx=(0, 12))
        ui_theme.fit(self.subscription_note, i18n.t('library.subscription.note'))
        queue = ui_theme.button(pending, i18n.t('collect.add'), self.queue_subscription)
        queue.grid(row=0, column=1, sticky='e')
        buttons.append(queue)
        self.controls += buttons
        self.pending_tree = ui_theme.table(pending, columns(pending, [('title', i18n.t('table.new_items'), 290, True),
                                                                      ('url', i18n.t('table.link'), 420, True)]),
                                           selectmode='extended', height=4,
                                           empty=i18n.t('library.pending.empty'))
        self.pending_tree.container.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(8, 0))
        pin_rows(self.pending_tree, 4)
        self.pending_tree.ellipsize(middle=('url',))

    def build_archive_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)
        switch_row = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        switch_row.grid(row=0, column=0, sticky='ew', padx=TAB_PAD, pady=(TAB_TOP, 0))
        enabled = ui_theme.checkbox(switch_row, i18n.t('library.archive.enabled'), variable=self.archive_enabled,
                                    command=self.save_settings)
        enabled.grid(row=0, column=0, sticky='w')
        ui_theme.label(switch_row, i18n.t('library.archive.off_note'), role='caption',
                       text_color=ui_theme.MUTED).grid(row=0, column=1, sticky='w', padx=(16, 0))
        paths = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        paths.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        paths.grid_columnconfigure(0, weight=1)
        root = ui_theme.entry(paths, textvariable=self.archive_root)
        root.grid(row=0, column=0, sticky='ew')
        choose = ui_theme.button(paths, i18n.t('library.archive.choose'), self.choose_archive)
        choose.grid(row=0, column=1, padx=(8, 0))
        self.controls += [enabled, root, choose]
        layout = ui_theme.label(tab, i18n.t('library.archive.layout'),
                                role='caption', text_color=ui_theme.MUTED, anchor='w', justify='left')
        layout.grid(row=2, column=0, sticky='ew', padx=TAB_PAD, pady=(8, 0))
        ui_theme.wrap_to_width(layout)
        tools = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        tools.grid(row=3, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        tools.grid_columnconfigure(1, weight=1)
        # (1.7.0, I18N.md 10.1) A safety net for long labels: when the row is short, the search entry
        # (sticky 'w', it does not grow) gives way down to 160 px before any button is clipped.
        tools.grid_columnconfigure(0, weight=1, minsize=160)
        self.archive_search_entry = ui_theme.entry(tools, textvariable=self.archive_search,
                                                   placeholder=i18n.t('library.archive.search'), width=280)
        self.archive_search_entry.grid(row=0, column=0, sticky='w')
        for column, (title, callback) in enumerate([(i18n.t('library.archive.archive_pages'), self.archive_pages),
                                                    (i18n.t('library.archive.open'), self.open_archive),
                                                    (i18n.t('library.archive.attach'), self.attach_transcript),
                                                    (i18n.t('common.refresh'), self.refresh_library)], 2):
            button = ui_theme.button(tools, title, callback)
            button.grid(row=0, column=column, padx=(8, 0))
            self.controls.append(button)
        self.archive_tree = ui_theme.table(tab, columns(tab, [
            ('title', i18n.t('table.title'), 300, True), ('author', i18n.t('table.author'), 100, False),
            ('files', i18n.t('table.media_transcripts'), 95, False), ('source', i18n.t('table.source'), 210, True)]),
                                           empty=i18n.t('library.archive.empty'))
        self.archive_tree.container.grid(row=4, column=0, sticky='nsew', padx=TAB_PAD, pady=(ui_theme.GAP, TAB_PAD))
        pin_rows(self.archive_tree, 4)
        self.archive_tree.ellipsize(middle=('source',))
        self.archive_search.trace_add('write', lambda *_: self.refresh_archives())

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
            for tree, rows, render in [
                (self.history_tree, jobs, history_row),
                (self.subscription_tree, self.library.subscriptions(), subscription_row)]:
                # iids are the record ids; fill() keeps the selection of a row that still exists.
                tree.fill((row['id'], render(row)) for row in rows)
            self.show_history_count(len(self.history_tree.get_children()), total, search)
            self.history_selection()
            self.subscription_selection()
            self.refresh_archives()
        except Exception as error:
            if is_busy(error):
                self.library_busy(error)
                return
            applog.get('ui').exception('refresh_library failed')
            self.show_library_problem(i18n.t('library.unreadable', error=error))
        else:
            if self.library_waiting and self.library_busy_count == busy_before and not self.history_writer.waiting():
                self.library_settled()
            self.show_library_problem('')

    def show_library_problem(self, text):
        """The warning strip above the tabs: ``text`` (a read error), else the calm busy line while
        the library is locked (1.7.0 U1), else the warning of open_library."""
        if text and text != self.library_problem:
            self.append_log(text)       # once per new error, not at every refresh
        self.library_problem = text
        calm = not text and getattr(self, 'library_waiting', False)
        shown = text or (i18n.t('library.busy') if calm else '') or i18n.localize(self.library_warning)
        try:
            self.library_warning_bar.configure(fg_color=ui_theme.ACCENT_SOFT if calm else ui_theme.WARN_SOFT)
            self.library_warning_label.configure(text_color=ui_theme.ACCENT_TEXT if calm else ui_theme.WARN)
            ui_theme.fit(self.library_warning_label, shown)
            if shown:
                self.library_warning_bar.grid()
            else:
                self.library_warning_bar.grid_remove()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ (1.7.0 U1) a locked library
    def library_busy(self, error=None):
        """A history read or write met a lock held by another program: one log line, the calm
        notice on the download page and the strip of the library page, and a look again in
        LIBRARY_RETRY_MS (library_watch).  Never waits and never opens a dialog."""
        self.library_waiting = True
        self.library_busy_count += 1
        if not self.library_busy_logged:
            self.library_busy_logged = True
            self.append_log(i18n.t('log.library.busy', error=error or 'database is locked'))
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
            self.show_library_problem(i18n.t('library.unreadable', error=error))

    def show_history_count(self, shown, total, search=''):
        if search:
            text = i18n.t('library.history.found', total=total) + \
                (i18n.t('library.history.found_shown', shown=shown) if shown < total else '')
        elif shown < total:
            text = i18n.t('library.history.count_shown', total=total, shown=shown)
        else:
            text = i18n.t('library.history.count', total=total) if total else ''
        try:
            self.history_count.configure(text=text)
            empty = i18n.t('library.history.none_found', term=search[:30]) if search else i18n.t('library.history.empty')
            if self.history_tree._empty != empty:
                self.history_tree.set_empty(empty)
            if shown < total:
                self.history_more.configure(text=i18n.t('library.history.more_n', count=min(HISTORY_PAGE, total - shown)))
                self.history_more.grid()
            else:
                self.history_more.grid_remove()
        except (AttributeError, tk.TclError):
            pass

    def history_selection(self):
        """Note and button states for the selected job; never raises (set_busy calls it)."""
        selected = self.history_tree.selection()
        record = None
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
        else:
            note = i18n.t('library.history.note')
        try:
            self.history_note.configure(text=note)
            usable = record is not None and not self.busy
            self.history_view.configure(state='normal' if usable else 'disabled')
            self.history_resume.configure(state='normal' if usable and record['status'] not in ('running', 'completed') else 'disabled')
        except tk.TclError:
            pass

    @staticmethod
    def history_note_text(record):
        """One line about a job: saved pages (collect jobs), successful downloads, the last error.

        A download job's error is the engine's English line; the note shows the short Chinese
        reason the task row showed (SPEC 3.11; stored next to the line since polish
        ui-wording-consistency-5), else the reason explain_error finds for it, never the raw line.
        A download counts once per (link, format) (review-e2e-4).

        (1.7.0) The stored error and reason stay canonical Chinese in the record; the note shows
        them in the running language (I18N.md 9.3), the raw engine line never.
        """
        data = record['data']
        completed = len(data.get('completed') or [])
        if record['kind'] != 'collect':
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
            error = str(errors[-1])
            if record['kind'] != 'collect':
                last = data.get('last_reason')
                if isinstance(last, dict) and last.get('error') == error and isinstance(last.get('reason'), str) \
                        and last['reason']:
                    error = last['reason']
                else:
                    error = explain_error(error, bot_check=bool(BOT_CHECK_PATTERN.search(error)),
                                          stale=bool(STALE_PATTERN.search(error)))
            error = i18n.localize(error)        # a collector text, a stored reason or explain_error's result
            text += i18n.t('library.history.last_error', error=error[:130])
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
        self.collect_text.configure(state='normal' if any(p['text'] for p in record['pages']) else 'disabled')
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
                messagebox.showwarning(i18n.t('dialog.resume_collect_failed'), i18n.localize(str(error)), parent=self)
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
        if self.busy:
            return
        try:
            options = self.collection_options()
            name = self.subscription_name.get().strip() or (options.urls[0][:65])
            values = (name, asdict(options), INTERVALS[self.subscription_interval.get()], self.subscription_media.get(),
                      self.subscription_first.get())
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
            self.library_tabs.set(TAB_SUBSCRIPTIONS)
            self.show_page('library')
            self.append_log(i18n.t('log.subscription.saved', name=name))
        except (OSError, ValueError) as error:
            messagebox.showwarning(i18n.t('dialog.subscription.save_failed'), i18n.localize(str(error)), parent=self)

    def selected_subscription(self):
        selected = self.subscription_tree.selection()
        if not selected:
            return None
        return next((r for r in self.library.subscriptions() if r['id'] == selected[0]), None)

    def subscription_action(self, action):
        """Run a subscription button's action; a locked library (1.7.0 U1) does nothing but the
        calm notice (the owner clicks again later)."""
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
        # or 刷新) does not throw a multi-selection away.
        previous = getattr(self, 'visible_pending', [])
        picked = {previous[int(n)]['url'] for n in self.pending_tree.selection()
                  if n.isdigit() and int(n) < len(previous)}
        self.visible_pending = self.library.pending(row['id']) if row else []
        self.pending_tree.selection_remove(self.pending_tree.selection())
        self.pending_tree.fill((str(n), (i18n.localize(item['title'], keys=PAGE_TITLE_KEYS), item['url']))
                               for n, item in enumerate(self.visible_pending))
        again = [str(n) for n, item in enumerate(self.visible_pending) if item['url'] in picked]
        if again:
            self.pending_tree.selection_set(again)
        if row:
            data = row['data']
            # The stored last_error (also '；'-joined collector errors) in the running language (I18N.md 9.3).
            text = i18n.t('library.subscription.status', new=data['last_new'], pending=row['pending']) + \
                (i18n.localize(data['last_error']) if data['last_error'] else i18n.t('library.subscription.status_note'))
        else:
            text = i18n.t('library.subscription.note')
        self.subscription_note.configure(text=text, text_color=ui_theme.MUTED)

    def subscription_hint(self, text):
        """Say on the subscription tab why an action did nothing (the note line, until the selection changes)."""
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
            if not automatic:
                self.subscription_hint(self.pick_subscription_text())
            return
        try:
            options = CollectOptions(**row['data']['options'])
            options.proxy = self.proxy.get().strip()
            options.validate()
            self.active_subscription = row['id']
            # The job title is stored in its canonical form (H2); history_title shows it localized.
            self.launch_collection(options, title=stored_text('value.job_title.subscription', name=row['name']),
                                   background=automatic)
            if automatic:
                # robust-13: the download page says why it cannot start right now, the collect page
                # why its controls are disabled.
                name = i18n.localize(row['name'], keys=NAME_KEYS)
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
                    self.library.finish_subscription(row['id'], [], False, str(error))
                except Exception:
                    applog.get('ui').exception('finish_subscription failed')
            self.append_log(i18n.t('log.subscription.failed', name=i18n.localize(row['name'], keys=NAME_KEYS),
                                   error=i18n.localize(str(error))))
            if not automatic:
                messagebox.showwarning(i18n.t('dialog.subscription.not_started'), i18n.localize(str(error)), parent=self)

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
                try:
                    self.after(15000, self.subscription_tick)
                except tk.TclError:
                    pass        # the window is gone

    def toggle_subscription(self):
        self.subscription_action(self._toggle_subscription)

    def change_interval(self):
        self.subscription_action(self._change_interval)

    def _toggle_subscription(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], not row['enabled'], row['interval'])
            self.refresh_library()
        elif not row and not self.busy:
            self.subscription_hint(self.pick_subscription_text())

    def pick_subscription_text(self):
        """Why a subscription action did nothing: no row picked, or no subscription at all."""
        if self.subscription_tree.get_children():
            return i18n.t('library.pick_subscription')
        return i18n.t('library.no_subscription')

    def _change_interval(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], row['enabled'], INTERVALS[self.subscription_interval.get()])
            self.refresh_library()
        elif not row and not self.busy:
            self.subscription_hint(self.pick_subscription_text())

    def queue_subscription(self):
        if self.busy:
            return
        items = [self.visible_pending[int(n)] for n in self.pending_tree.selection()]
        if not items:
            # The same line as the other actions of this tab (no dialog for a missing selection).
            if self.pending_tree.get_children():
                self.subscription_hint(i18n.t('library.pick_pending'))
            elif self.subscription_tree.selection():
                self.subscription_hint(i18n.t('library.no_pending'))
            else:
                self.subscription_hint(self.pick_subscription_text())
            return
        self.queue_collected_items(items)

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
            archive = Archive(self.archive_root.get(), index)
            for page in self.collected_pages:
                archive.prepare(page['url'], dict(title=page['title'], source=page['url'], page=page))
            self.append_log(i18n.t('log.archive.pages', count=len(self.collected_pages)))
            if index.deferred:
                self.library_busy()
            self.refresh_archives()
        except (ValueError, OSError) as error:
            messagebox.showerror(i18n.t('dialog.archive_failed'), i18n.localize(str(error)), parent=self)

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
            # (final fix round 1, CR1-1) the shown title is searched too when it differs (English fallback title)
            shown = i18n.localize(r['title'], keys=PAGE_TITLE_KEYS)
            title = r['title'] if shown == r['title'] else r['title'] + ' ' + shown
            return (title + ' ' + str(r.get('uploader', '')) + ' ' + r['source']).casefold()
        self.visible_archives = [r for r in stored if not term or term in searched(r)]
        self.archive_tree.selection_remove(selected)        # positional iids; re-selected by folder below
        self.archive_tree.fill((str(n), (i18n.localize(row['title'], keys=PAGE_TITLE_KEYS),
                                         row.get('uploader') or i18n.t('library.archive.not_provided'),
                                         f"{len(row['files'])} / {len(row['transcripts'])}", row['source']))
                               for n, row in enumerate(self.visible_archives))
        for n, row in enumerate(self.visible_archives):
            if row['folder'] == selected_folder:
                self.archive_tree.selection_set(str(n))

    def selected_archive(self):
        selected = self.archive_tree.selection()
        return self.visible_archives[int(selected[0])] if selected else None

    def open_archive(self):
        row = self.selected_archive()
        if row:
            try:
                os.startfile(row['folder'])
            except OSError as error:
                messagebox.showerror(i18n.t('dialog.open_failed'), str(error), parent=self)
        else:
            self.archive_hint()

    def archive_hint(self):
        """The two actions that need a selected row say so instead of doing nothing."""
        empty = not self.archive_tree.get_children()
        messagebox.showinfo(i18n.t('dialog.pick_item'),
                            i18n.t('library.archive.none') if empty else i18n.t('library.pick_archive'), parent=self)

    def attach_transcript(self):
        row = self.selected_archive()
        if not row:
            self.archive_hint()
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
                messagebox.showerror(i18n.t('dialog.attach_failed'), i18n.localize(str(error)), parent=self)
