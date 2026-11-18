"""Task recovery, incremental subscriptions, and source archive UI (mixin LibraryUI; 1.6.0 WP5).

The library page is one ui_theme.tabs card with three tabs (WP5.md section C).  Every colour,
font and widget comes from ui_theme.  Never import studio here at module level.
"""
from dataclasses import asdict
from pathlib import Path
import os
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import applog
import ui_theme
from collector import CollectOptions
from engine import APP_DIR, BOT_CHECK_PATTERN, STALE_PATTERN, Options, explain_error
from library import JOB_OPTION_KEYS, Archive, job_downloads, open_library
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


def pin_rows(table, rows):
    """Make a table ask for ``rows`` rows plus its heading, whether or not its scrollbar is shown.

    The table's scrollbar (a CTkScrollbar) asks for 200 px of its own; once it appears a 4-row table
    asks for about 208 px instead of 166 and the subscription tab no longer fits at 1000x740.  The
    container keeps the asked size fixed; its grid cell still stretches it.
    """
    table.container.configure(height=ui_theme.TABLE_ROW_H * (rows + 1) + 8)
    table.container.grid_propagate(False)


def clock(stamp):
    return time.strftime('%m-%d %H:%M', time.localtime(stamp)) if stamp else '尚未检查'


def history_title(row):
    """The name of a job in the history list.  A download job that never got a title keeps the
    placeholder '<n>条 · <format>' in the store (1.5.1 shows it); the list names it after its first
    link instead, like the task rows (polish ui-daily-flow-6)."""
    title = str(row.get('title') or '')
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    if row.get('kind') != 'download' or data.get('titled') or not PLACEHOLDER_TITLE.fullmatch(title):
        return title
    options = data.get('options') if isinstance(data.get('options'), dict) else {}
    urls = [url for url in options.get('urls') or [] if isinstance(url, str) and url]
    if not urls:
        return title
    return f'{display(urls[0])} 等 {len(urls)} 条' if len(urls) > 1 else display(urls[0])


def history_status(row):
    """The status word of a job in the history list (the stored status string is never changed)."""
    data = row.get('data') if isinstance(row.get('data'), dict) else {}
    if row.get('kind') == 'download' and row.get('status') == 'partial' and not data.get('completed'):
        return NOTHING_DONE
    return STATES.get(row.get('status'), row.get('status'))


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
        self.subscription_interval = tk.StringVar(value='每小时')
        self.subscription_media = tk.BooleanVar(value=True)
        self.subscription_first = tk.BooleanVar(value=True)
        self.subscription_name = tk.StringVar()
        self.archive_search = tk.StringVar()

    def build_library_page(self):
        page = self.page('library', '资料管理', '采集断点自动保存；订阅只收新增；媒体、正文与转写稿集中归档。')
        page.grid_rowconfigure(2, weight=1)
        # robust-6: a history that could not be opened is replaced for this session; say so here.
        self.library_warning_bar = ctk.CTkFrame(page, fg_color=ui_theme.WARN_SOFT, corner_radius=ui_theme.RADIUS)
        self.library_warning_bar.grid_columnconfigure(0, weight=1)
        self.library_warning_label = ui_theme.label(self.library_warning_bar, '', role='caption',
                                                    text_color=ui_theme.WARN, anchor='w')
        self.library_warning_label.grid(row=0, column=0, sticky='ew', padx=12, pady=5)
        self.library_warning_bar.grid(row=1, column=0, sticky='ew', pady=(0, ui_theme.GAP))
        ui_theme.fit(self.library_warning_label, self.library_warning)
        if not self.library_warning:
            self.library_warning_bar.grid_remove()
        # A keyboard focus left in a tab that is switched away from moves to the new tab's table
        # (final-fix-ui, polish-function-3); the hidden tabs take no Tab focus (ui_theme.Tabs).
        tables = {'任务历史与续采': 'history_tree', '增量订阅': 'subscription_tree', '统一资料归档': 'archive_tree'}
        self.library_tabs = ui_theme.tabs(page, ['任务历史与续采', '增量订阅', '统一资料归档'],
                                          focus=lambda name: getattr(self, tables.get(name, ''), None))
        self.library_tabs.grid(row=2, column=0, sticky='nsew')
        history = self.library_tabs.add('任务历史与续采')
        subscriptions = self.library_tabs.add('增量订阅')
        archive = self.library_tabs.add('统一资料归档')
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
        ui_theme.button(bar, '刷新记录', self.refresh_library).grid(row=0, column=0, sticky='w')
        self.history_view = ui_theme.button(bar, '查看结果', self.view_history)
        self.history_view.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.history_resume = ui_theme.button(bar, '继续未完成', self.resume_history, kind='primary', width=112)
        self.history_resume.grid(row=0, column=2, sticky='w', padx=(8, 0))
        self.controls += [self.history_view, self.history_resume]
        # One line (fit, the whole text in ui_theme.full_text): a wrapped note made the table jump
        # whenever a job with a long error was selected.
        self.history_note = ui_theme.label(tab, '', role='caption', text_color=ui_theme.MUTED,
                                           anchor='w', justify='left')
        self.history_note.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, ui_theme.GAP))
        ui_theme.fit(self.history_note, HISTORY_NOTE)
        self.history_tree = ui_theme.table(tab, [('kind', '类型', 80, False), ('title', '任务', 360, True),
                                                 ('status', '状态', 96, False), ('time', '更新时间', 120, False)],
                                           empty='下载和采集任务会记录在这里')
        self.history_tree.container.grid(row=2, column=0, sticky='nsew', padx=TAB_PAD, pady=(0, TAB_PAD))
        pin_rows(self.history_tree, 4)
        self.history_tree.ellipsize()
        self.history_tree.bind('<<TreeviewSelect>>', lambda _: self.history_selection())

    def build_subscription_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=2)
        tab.grid_rowconfigure(3, weight=3)
        ui_theme.label(tab, '定时检查仅在下载器打开时运行；忙碌时顺延，重新打开会补查到期订阅。', role='caption',
                       text_color=ui_theme.MUTED, anchor='w').grid(row=0, column=0, sticky='ew', padx=TAB_PAD,
                                                                   pady=(TAB_TOP, 0))
        # Creation strip, two rows: on one row it needs about 890 px and has 712 at 1000x740.
        strip = ctk.CTkFrame(tab, fg_color=ui_theme.SURFACE_ALT, corner_radius=ui_theme.RADIUS)
        strip.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(8, 0))
        strip.grid_columnconfigure(0, weight=1)
        self.subscription_strip = strip
        name = ui_theme.entry(strip, textvariable=self.subscription_name, placeholder='订阅名称（使用网页采集页的当前设置）',
                              width=280, fg_color=ui_theme.SURFACE)
        name.grid(row=0, column=0, sticky='ew', padx=(12, 0), pady=(12, 0))
        self.subscription_name_entry = name
        interval = ui_theme.option_menu(strip, list(INTERVALS), variable=self.subscription_interval, width=110)
        interval.grid(row=0, column=1, padx=(8, 0), pady=(12, 0))
        create = ui_theme.button(strip, '订阅当前采集设置', self.save_subscription, kind='primary')
        create.grid(row=0, column=2, padx=(8, 12), pady=(12, 0))
        self.subscription_create = create
        flags = ctk.CTkFrame(strip, fg_color='transparent', corner_radius=0)
        flags.grid(row=1, column=0, columnspan=3, sticky='w', padx=12, pady=(10, 12))
        media = ui_theme.checkbox(flags, '只跟踪媒体候选', variable=self.subscription_media)
        media.grid(row=0, column=0, sticky='w')
        first = ui_theme.checkbox(flags, '首轮也列为新增（关闭则只建基线）', variable=self.subscription_first)
        first.grid(row=0, column=1, sticky='w', padx=(24, 0))
        self.controls += [name, interval, create, media, first]
        # Two stacked blocks (side by side the four columns would be clipped at the default width).
        block = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        block.grid(row=2, column=0, sticky='nsew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        block.grid_columnconfigure(0, weight=1)
        block.grid_rowconfigure(1, weight=1)
        actions = ctk.CTkFrame(block, fg_color='transparent', corner_radius=0)
        actions.grid(row=0, column=0, sticky='ew')
        buttons = [ui_theme.button(actions, '立即检查', self.check_subscription, kind='primary'),
                   ui_theme.button(actions, '暂停 / 恢复', self.toggle_subscription),
                   ui_theme.button(actions, '应用检查间隔', self.change_interval)]
        for column, button in enumerate(buttons):
            button.grid(row=0, column=column, sticky='w', padx=(0 if column == 0 else 8, 0))
        # Two bounded lists leave room for the new-content queue at minimum window size.
        self.subscription_tree = ui_theme.table(block, [('name', '订阅', 210, True), ('state', '计划', 110, False),
                                                        ('last', '上次成功', 115, False), ('pending', '待处理', 70, False)],
                                                empty='还没有订阅 · 在网页采集页设置好后点“保存为订阅”', height=4)
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
        ui_theme.fit(self.subscription_note, SUBSCRIPTION_NOTE)
        queue = ui_theme.button(pending, '添加到下载列表', self.queue_subscription)
        queue.grid(row=0, column=1, sticky='e')
        buttons.append(queue)
        self.controls += buttons
        self.pending_tree = ui_theme.table(pending, [('title', '新增内容（可多选）', 290, True), ('url', '链接', 420, True)],
                                           selectmode='extended', height=4,
                                           empty='还没有新增内容 · 订阅检查到的新链接会出现在这里')
        self.pending_tree.container.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(8, 0))
        pin_rows(self.pending_tree, 4)
        self.pending_tree.ellipsize(middle=('url',))

    def build_archive_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)
        switch_row = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        switch_row.grid(row=0, column=0, sticky='ew', padx=TAB_PAD, pady=(TAB_TOP, 0))
        enabled = ui_theme.checkbox(switch_row, '下载时统一归档', variable=self.archive_enabled, command=self.save_settings)
        enabled.grid(row=0, column=0, sticky='w')
        ui_theme.label(switch_row, '关闭时保留原下载目录和文件命名。', role='caption',
                       text_color=ui_theme.MUTED).grid(row=0, column=1, sticky='w', padx=(16, 0))
        paths = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        paths.grid(row=1, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        paths.grid_columnconfigure(0, weight=1)
        root = ui_theme.entry(paths, textvariable=self.archive_root)
        root.grid(row=0, column=0, sticky='ew')
        choose = ui_theme.button(paths, '选择资料库', self.choose_archive)
        choose.grid(row=0, column=1, padx=(8, 0))
        self.controls += [enabled, root, choose]
        layout = ui_theme.label(tab, '按来源站点与稳定 ID 分目录：媒体 / 网页正文 TXT 与 MD / 资料信息 / 转写稿。旧文件不会被移动。',
                                role='caption', text_color=ui_theme.MUTED, anchor='w', justify='left')
        layout.grid(row=2, column=0, sticky='ew', padx=TAB_PAD, pady=(8, 0))
        ui_theme.wrap_to_width(layout)
        tools = ctk.CTkFrame(tab, fg_color='transparent', corner_radius=0)
        tools.grid(row=3, column=0, sticky='ew', padx=TAB_PAD, pady=(ui_theme.GAP, 0))
        tools.grid_columnconfigure(1, weight=1)
        self.archive_search_entry = ui_theme.entry(tools, textvariable=self.archive_search,
                                                   placeholder='搜索标题、作者或来源', width=280)
        self.archive_search_entry.grid(row=0, column=0, sticky='w')
        for column, (title, callback) in enumerate([('归档当前采集正文', self.archive_pages),
                                                    ('打开资料目录', self.open_archive),
                                                    ('关联转写稿', self.attach_transcript),
                                                    ('刷新', self.refresh_library)], 2):
            button = ui_theme.button(tools, title, callback)
            button.grid(row=0, column=column, padx=(8, 0))
            self.controls.append(button)
        self.archive_tree = ui_theme.table(tab, [('title', '标题', 300, True), ('author', '作者', 100, False),
                                                 ('files', '媒体 / 转写', 95, False), ('source', '来源', 210, True)],
                                           empty='还没有归档资料')
        self.archive_tree.container.grid(row=4, column=0, sticky='nsew', padx=TAB_PAD, pady=(ui_theme.GAP, TAB_PAD))
        pin_rows(self.archive_tree, 4)
        self.archive_tree.ellipsize(middle=('source',))
        self.archive_search.trace_add('write', lambda *_: self.refresh_archives())

    def refresh_library(self):
        # robust-6: a locked or broken history must not stop the window (at start-up neither): the
        # error goes to the log and into the warning strip, and the next refresh tries again.
        try:
            for tree, rows, render in [
                (self.history_tree, self.library.history(), lambda r: ('采集' if r['kind'] == 'collect' else '下载', history_title(r), history_status(r), clock(r['updated']))),
                (self.subscription_tree, self.library.subscriptions(), lambda r: (r['name'], (next(k for k,v in INTERVALS.items() if v == r['interval']) if r['enabled'] else '已暂停'), clock(r['data']['last_check']), r['pending']))]:
                # iids are the record ids; fill() keeps the selection of a row that still exists.
                tree.fill((row['id'], render(row)) for row in rows)
            self.history_selection()
            self.subscription_selection()
            self.refresh_archives()
        except Exception as error:
            applog.get('ui').exception('refresh_library failed')
            self.show_library_problem(LIBRARY_UNREADABLE.format(error=error))
        else:
            self.show_library_problem('')

    def show_library_problem(self, text):
        """The warning strip above the tabs: ``text`` (a read error), else the warning of open_library."""
        if text and text != self.library_problem:
            self.append_log(text)       # once per new error, not at every refresh
        self.library_problem = text
        shown = text or self.library_warning
        try:
            ui_theme.fit(self.library_warning_label, shown)
            if shown:
                self.library_warning_bar.grid()
            else:
                self.library_warning_bar.grid_remove()
        except tk.TclError:
            pass

    def history_selection(self):
        """Note and button states for the selected job; never raises (set_busy calls it)."""
        selected = self.history_tree.selection()
        record = None
        if selected:
            try:
                record = self.library.job(selected[0])
                note = self.history_note_text(record)
            except Exception:
                applog.get('ui').exception('history record could not be read')
                record, note = None, HISTORY_UNREADABLE
        else:
            note = HISTORY_NOTE
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
        """
        data = record['data']
        completed = len(data.get('completed') or [])
        if record['kind'] != 'collect':
            try:
                completed = max(completed, sum(1 for entry in job_downloads(data) if entry['done']))
            except Exception:
                pass
        done = f'{completed} 条下载成功'
        if record['kind'] == 'collect':
            text = f"{len(record['pages'])} 个已保存页面 · {done}"
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
            text += f' · 最近错误：{error[:130]}'
        return text

    def load_collection_record(self, record):
        raw = record['data']['options']
        self.collect_urls.delete('1.0', 'end')
        self.collect_urls.insert('1.0', '\n'.join(raw['urls']))
        self.collect_mode.set('动态网页（Chrome）' if raw.get('dynamic') else '普通网页')
        self.collect_scrolls.set(str(raw.get('scrolls', 0)))
        self.collect_pages_limit.set(str(raw.get('max_pages', 1)))
        self.collect_selector.set(raw.get('selector', ''))
        self.collected_pages = record['pages']
        self.collected_links = {i['url']: i for p in record['pages'] for i in p['links']}
        self.render_collection_links()
        self.collect_text.configure(state='normal' if any(p['text'] for p in record['pages']) else 'disabled')
        self.set_collect_status(f"历史结果：{len(record['pages'])} 页，{len(self.collected_links)} 条链接", 'idle')

    def view_history(self):
        if self.busy or not self.history_tree.selection():
            return
        record = self.library.job(self.history_tree.selection()[0])
        if record['kind'] == 'collect':
            self.load_collection_record(record)
            self.show_page('collect')
        else:
            # The links go into the box; the format, folder and other choices stay as they are.
            raw = record['data']['options']
            self.add_links('\n'.join(raw.get('urls') or []))
            self.download_referers.update(raw.get('referers') or {})
            self.show_page('download')
            self.set_hint('已载入这条记录的链接；格式沿用当前设置')

    def resume_history(self):
        if self.busy or not self.history_tree.selection():
            return
        record = self.library.job(self.history_tree.selection()[0])
        if record['status'] in ('running', 'completed'):
            return
        if record['kind'] == 'collect':
            raw = dict(record['data']['options'])
            raw.update(proxy=self.proxy.get().strip(), resume=record['data']['checkpoint'])
            try:
                options = CollectOptions(**raw).validate()
            except ValueError as error:
                messagebox.showwarning('无法续采', str(error), parent=self)
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
                messagebox.showinfo('下载已完成', '媒体均已下载；如有归档错误，可查看任务日志和资料目录。', parent=self)
                return
            # The job's own options (and those of links added in another format or folder), with the
            # current network and batch preferences.  The link box and the form are not touched; songs
            # downloaded meanwhile are skipped by the engine as already downloaded.
            try:
                urls, options, overrides = self.resume_launch(raw, first)
                later = [(entry['url'], self.resume_options(entry['options'], [entry['url']])) for entry in rest]
            except ValueError:
                messagebox.showwarning('无法恢复', '这条记录的输出设置在当前版本中不可用。', parent=self)
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
            for url, options in later:
                try:
                    self.library.extend_job(job, [url], {url: options.to_dict()})
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
            self.append_log(RESUME_DROPPED_LOG.format(count=len(later)))
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
                self.append_log(RESUME_LATER_LOG.format(count=added))
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
            key = self.library.subscription(name, asdict(options), INTERVALS[self.subscription_interval.get()], self.subscription_media.get(), self.subscription_first.get())
            self.refresh_library()
            self.subscription_tree.selection_set(key)
            self.library_tabs.set('增量订阅')
            self.show_page('library')
            self.append_log(f'已保存订阅：{name}。检查范围沿用当前采集页数、CSS 和动态模式设置。')
        except (OSError, ValueError) as error:
            messagebox.showwarning('保存订阅失败', str(error), parent=self)

    def selected_subscription(self):
        selected = self.subscription_tree.selection()
        return next((r for r in self.library.subscriptions() if selected and r['id'] == selected[0]), None)

    def subscription_selection(self):
        row = self.selected_subscription()
        # Positional iids: an old selection would point at other entries.  The entries the owner had
        # picked are picked again by their link, so a refresh (after a background check, a download
        # or 刷新) does not throw a multi-selection away.
        previous = getattr(self, 'visible_pending', [])
        picked = {previous[int(n)]['url'] for n in self.pending_tree.selection()
                  if n.isdigit() and int(n) < len(previous)}
        self.visible_pending = self.library.pending(row['id']) if row else []
        self.pending_tree.selection_remove(self.pending_tree.selection())
        self.pending_tree.fill((str(n), (item['title'], item['url'])) for n, item in enumerate(self.visible_pending))
        again = [str(n) for n, item in enumerate(self.visible_pending) if item['url'] in picked]
        if again:
            self.pending_tree.selection_set(again)
        if row:
            data = row['data']
            text = f"最近检查新增 {data['last_new']} 条 · 待处理 {row['pending']} 条 · " + (data['last_error'] or '仅下载成功后移出；可多选新增内容添加到下载列表。')
        else:
            text = SUBSCRIPTION_NOTE
        self.subscription_note.configure(text=text, text_color=ui_theme.MUTED)

    def subscription_hint(self, text):
        """Say on the subscription tab why an action did nothing (the note line, until the selection changes)."""
        self.subscription_note.configure(text=text, text_color=ui_theme.WARN)

    def check_subscription(self, row=None, automatic=False):
        if self.busy or self.closing:
            return
        row = row or self.selected_subscription()
        if not row:
            if not automatic:
                self.subscription_hint(PICK_SUBSCRIPTION if self.subscription_tree.get_children() else NO_SUBSCRIPTION)
            return
        try:
            options = CollectOptions(**row['data']['options'])
            options.proxy = self.proxy.get().strip()
            options.validate()
            self.active_subscription = row['id']
            self.launch_collection(options, title='订阅：' + row['name'], background=automatic)
            if automatic:
                # robust-13: the download page says why it cannot start right now, the collect page
                # why its controls are disabled.
                ui_theme.fit(self.link_hint, f'正在检查订阅“{row["name"]}”，完成后可以开始下载')
                self.refresh_collect_note(background=row['name'])
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
            self.append_log(f"订阅检查失败：{row['name']} · {error}")
            if not automatic:
                messagebox.showwarning('订阅未开始', str(error), parent=self)

    def subscription_tick(self):
        try:
            if not self.busy and not self.closing and not self.smoke_mode:
                for row in sorted(self.library.subscriptions(), key=lambda r: r['next_check']):
                    if row['enabled'] and row['interval'] and row['next_check'] <= time.time() \
                            and self.subscription_backoff.get(row['id'], 0) <= time.time():
                        self.check_subscription(row, automatic=True)
                        break
        except Exception:
            # One failing tick (a locked history, say) must not end the scheduled checks.
            applog.get('ui').exception('subscription tick failed')
        finally:
            if not self.closing:
                try:
                    self.after(15000, self.subscription_tick)
                except tk.TclError:
                    pass        # the window is gone

    def toggle_subscription(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], not row['enabled'], row['interval'])
            self.refresh_library()
        elif not row and not self.busy:
            self.subscription_hint(PICK_SUBSCRIPTION if self.subscription_tree.get_children() else NO_SUBSCRIPTION)

    def change_interval(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], row['enabled'], INTERVALS[self.subscription_interval.get()])
            self.refresh_library()
        elif not row and not self.busy:
            self.subscription_hint(PICK_SUBSCRIPTION if self.subscription_tree.get_children() else NO_SUBSCRIPTION)

    def queue_subscription(self):
        if self.busy:
            return
        items = [self.visible_pending[int(n)] for n in self.pending_tree.selection()]
        if not items:
            # The same line as the other actions of this tab (no dialog for a missing selection).
            if self.pending_tree.get_children():
                self.subscription_hint(PICK_PENDING)
            elif self.subscription_tree.selection():
                self.subscription_hint(NO_PENDING)
            else:
                self.subscription_hint(PICK_SUBSCRIPTION if self.subscription_tree.get_children() else NO_SUBSCRIPTION)
            return
        self.queue_collected_items(items)

    def collection_contexts(self, urls):
        # Provenance stays available after restarting without embedding body copies per URL.
        return self.library.contexts(urls)

    def choose_archive(self):
        path = filedialog.askdirectory(parent=self, initialdir=self.archive_root.get() if Path(self.archive_root.get()).is_dir() else str(APP_DIR))
        if path:
            self.archive_root.set(path)
            self.save_settings()

    def archive_pages(self):
        try:
            if not self.archive_root.get().strip():
                raise ValueError('请先选择资料库目录。')
            archive = Archive(self.archive_root.get(), self.library)
            for page in self.collected_pages:
                archive.prepare(page['url'], dict(title=page['title'], source=page['url'], page=page))
            self.append_log(f'已归档当前 {len(self.collected_pages)} 个页面正文。')
            self.refresh_archives()
        except (ValueError, OSError) as error:
            messagebox.showerror('归档失败', str(error), parent=self)

    def refresh_archives(self):
        selected = self.archive_tree.selection()
        selected_folder = self.visible_archives[int(selected[0])]['folder'] if selected and hasattr(self, 'visible_archives') else None
        term = self.archive_search.get().casefold()
        self.visible_archives = [r for r in self.library.archives() if not term or term in (r['title'] + ' ' + str(r.get('uploader', '')) + ' ' + r['source']).casefold()]
        self.archive_tree.selection_remove(selected)        # positional iids; re-selected by folder below
        self.archive_tree.fill((str(n), (row['title'], row.get('uploader') or '未提供', f"{len(row['files'])} / {len(row['transcripts'])}", row['source']))
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
                messagebox.showerror('打开失败', str(error), parent=self)
        else:
            self.archive_hint()

    def archive_hint(self):
        """The two actions that need a selected row say so instead of doing nothing."""
        empty = not self.archive_tree.get_children()
        messagebox.showinfo('选择资料', '还没有归档资料。' if empty else PICK_ARCHIVE, parent=self)

    def attach_transcript(self):
        row = self.selected_archive()
        if not row:
            self.archive_hint()
            return
        source = filedialog.askopenfilename(parent=self, title='关联现有转写稿（复制，保留原文件）', filetypes=[('转写稿', '*.txt *.srt *.md *.vtt')])
        if source:
            try:
                folder = Path(row['folder'])
                Archive(folder.parent.parent, self.library).attach(folder, source)
                self.refresh_archives()
            except (ValueError, OSError) as error:
                messagebox.showerror('关联失败', str(error), parent=self)
