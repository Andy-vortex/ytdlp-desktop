"""Page collection workspace for the existing desktop downloader (mixin CollectionUI; 1.6.0 WP5).

Layout (WP5.md section B): page header, input card (start pages, the three options and the
CSS scope, actions in the card header), results card (count, filter and search in its header,
a one-line status, the table, the footer with the primary action at the far right).
Every colour, font and widget comes from ui_theme.  Never import studio here at module level.
"""
from pathlib import Path
from dataclasses import asdict
import os
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from urllib.parse import quote, urlsplit, urlunsplit

import customtkinter as ctk
import applog
import ui_theme
import urls as links
from collector import CollectOptions, CollectionRunner, MAX_LINKS, browser_available
from engine import parse_urls
from library_ui import pin_rows

MODE_STATIC = '普通网页'
MODE_DYNAMIC = '动态网页（Chrome）'
FILTERS = ('媒体候选', '全部链接')
STATUS_IDLE = '支持普通页面和动态页面；采集结果供你选择，不会自动下载。'
NOTE_PAGES = '翻页只跟随同站的 rel=next 链接。'
NOTE_DYNAMIC = '动态模式使用独立的临时 Chrome，不读取现有浏览器的登录状态。'
URLS_PLACEHOLDER = '每行一个网页地址，例如 https://example.com/videos · Ctrl+Enter 开始'
TABLE_EMPTY = '输入网页地址并开始采集，发现的音视频链接会列在这里'
# Polish ui-daily-flow-5: the links go into the download page's link box (nothing starts or joins
# a running queue), so the button says so; 加入队列 is the download page's word for a running job.
ADD_TEXT = '添加到下载列表'
SUBTITLE = '从网页发现音视频链接，筛选后添加到下载列表；也可保存网页正文。'
TRUNCATED_LOG = '本页正文超过 40 万字符，已截断；导出文件会注明。'
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
        page = self.page('collect', '网页采集', SUBTITLE)
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
        ui_theme.label(top, '起始网页', role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        ui_theme.label(top, '每行一个，最多 20 个', role='caption', text_color=muted,
                       anchor='w').grid(row=0, column=1, sticky='w', padx=(10, 0), pady=(3, 0))
        subscribe = ui_theme.button(top, '保存为订阅', self.save_subscription)
        subscribe.grid(row=0, column=3, padx=(8, 0))
        self.collect_stop = ui_theme.button(top, '停止', self.stop, kind='danger', width=64, state='disabled')
        self.collect_stop.grid(row=0, column=4, padx=(8, 0))
        self.collect_start = ui_theme.button(top, '开始采集', self.start_collection, kind='primary', width=112,
                                             image=ui_theme.icon(ui_theme.ICON_SEARCH, 'white'), compound='left')
        self.collect_start.grid(row=0, column=5, padx=(8, 0))

        self.collect_urls = ui_theme.textbox(inputs, height=56, wrap='word')
        self.collect_urls.grid(row=1, column=0, sticky='ew', padx=pad)
        self._collect_hint_shown = False
        self.collect_placeholder = ui_theme.label(self.collect_urls, URLS_PLACEHOLDER, text_color=ui_theme.FAINT,
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
        ui_theme.label(options, '方式', role='caption', text_color=muted).grid(row=0, column=0, sticky='w')
        mode = ui_theme.option_menu(options, (MODE_STATIC, MODE_DYNAMIC), variable=self.collect_mode, width=170)
        mode.grid(row=0, column=1, sticky='w', padx=(8, 16))
        ui_theme.label(options, '滚动次数', role='caption', text_color=muted).grid(row=0, column=2, sticky='w')
        scroll = ui_theme.option_menu(options, [str(n) for n in range(6)], variable=self.collect_scrolls, width=64)
        scroll.grid(row=0, column=3, sticky='w', padx=(8, 16))
        ui_theme.label(options, '每站页数', role='caption', text_color=muted).grid(row=0, column=4, sticky='w')
        limit = ui_theme.option_menu(options, [str(n) for n in range(1, 6)], variable=self.collect_pages_limit,
                                     width=64)
        limit.grid(row=0, column=5, sticky='w', padx=(8, 0))
        scope = ui_theme.entry(options, textvariable=self.collect_selector, placeholder='内容范围：可选 CSS，如 article')
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
        ui_theme.label(head, '发现的链接', role='heading', anchor='w').grid(row=0, column=0, sticky='w')
        self.collect_count = ui_theme.chip(head, '显示 0 / 共 0 · 已选 0', 'idle')
        self.collect_count.grid(row=0, column=1, sticky='w', padx=(10, 0))
        ui_theme.option_menu(head, FILTERS, variable=self.collect_filter, width=120).grid(row=0, column=3, padx=(12, 0))
        self.collect_search_entry = ui_theme.entry(head, textvariable=self.collect_search,
                                                   placeholder='筛选标题或地址', width=220)
        self.collect_search_entry.grid(row=0, column=4, padx=(8, 0))
        self.collect_status = ui_theme.label(result, '', role='caption', text_color=muted, anchor='w')
        self.collect_status.grid(row=1, column=0, sticky='ew', padx=pad, pady=(6, 8))
        ui_theme.fit(self.collect_status, STATUS_IDLE)
        self.collect_tree = ui_theme.table(result, [('kind', '类型', 88, False), ('title', '标题', 260, True),
                                                    ('url', '链接', 360, True)],
                                           selectmode='extended', empty=TABLE_EMPTY)
        self.collect_tree.container.grid(row=2, column=0, sticky='nsew', padx=pad)
        pin_rows(self.collect_tree, 4)     # the page's request stays the same once the scrollbar appears
        self.collect_tree.ellipsize(middle=('url',))     # cut with '…'; links keep their distinguishing end
        self.collect_tree.bind('<<TreeviewSelect>>', lambda _: self.collection_selection_changed())
        footer = ctk.CTkFrame(result, fg_color='transparent', corner_radius=0)
        footer.grid(row=3, column=0, sticky='ew', padx=pad, pady=(10, 14))
        footer.grid_columnconfigure(3, weight=1)
        ui_theme.button(footer, '全选当前', lambda: self.collect_tree.selection_set(self.collect_tree.get_children()),
                        kind='ghost').grid(row=0, column=0, sticky='w')
        self.collect_export = ui_theme.button(footer, '导出链接 TXT', self.export_collected_links, state='disabled')
        self.collect_export.grid(row=0, column=1, sticky='w', padx=(8, 0))
        self.collect_text = ui_theme.button(footer, '保存网页正文', self.export_collected_text, state='disabled')
        self.collect_text.grid(row=0, column=2, sticky='w', padx=(8, 0))
        self.collect_add = ui_theme.button(footer, ADD_TEXT, self.add_collected, kind='primary', width=132,
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
            ui_theme.fit(self.collect_note, BACKGROUND_NOTE.format(name=background))
            return
        text = NOTE_PAGES
        if self.collect_mode.get() == MODE_DYNAMIC:
            text += NOTE_DYNAMIC
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
            messagebox.showwarning('检查采集设置', str(error), parent=self)
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
        try:
            if fresh:
                job_id = self.library.create_job('collect', title or options.urls[0], asdict(options),
                                                 self.active_subscription)
            else:
                self.library.change_job(job_id, status='running', errors=[], owner=os.getpid(), worker=None)
        except (sqlite3.Error, OSError, ValueError) as error:
            if background:
                raise
            applog.get('ui').exception('collection history could not be written')
            self.active_subscription = None
            self.append_log(f'网页采集未开始：历史记录无法写入（{error}）')
            self.set_collect_status('采集未开始：历史记录暂时无法写入，请稍后再试。', 'warn')
            messagebox.showwarning('采集未开始', f'历史记录暂时无法写入，采集没有开始。请稍后再试。\n\n{error}', parent=self)
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
                self.set_collect_status('正在准备采集…', 'running')
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

    def handle_collection(self, kind, value):
        background = getattr(self, 'collection_background', False)
        if kind == 'collect_status':
            if not background:
                self.set_collect_status(value)
        elif kind == 'collect_page' and background:
            # An automatic check: the worker has stored the page; the collect page keeps what it shows.
            self.append_log(f'网页采集：{value["title"]}，{len(value["links"])} 条链接')
            if value.get('text_truncated'):
                self.append_log(TRUNCATED_LOG)
        elif kind == 'collect_page':
            value = {k:v for k,v in value.items() if k != '_checkpoint'}
            self.collected_pages.append(value)
            for link in value['links']:
                if len(self.collected_links) < MAX_LINKS:
                    self.collected_links.setdefault(link['url'], link)
            self.render_collection_links()
            self.set_collect_status(f'已采集 {len(self.collected_pages)} 页，发现 {len(self.collected_links)} 条链接')
            self.append_log(f'网页采集：{value["title"]}，{len(value["links"])} 条链接')
            if value.get('text_truncated'):
                self.append_log(TRUNCATED_LOG)
        elif kind == 'collect_error':
            self.collection_errors += 1
            self.append_log(f'网页采集未完成：{value}')
            if not background:
                # One line, cut by pixels; ui_theme.full_text(collect_status) keeps the whole message.
                self.set_collect_status(f'部分页面未完成：{value}', 'warn')
        elif kind == 'collect_done':
            # The bookkeeping may fail (a locked or broken history): the window is released anyway (arch-3 a).
            record = None
            try:
                if self.collection_job:
                    record = self.library.job(self.collection_job)
                    if self.active_subscription:
                        new = self.library.finish_subscription(self.active_subscription, record['pages'],
                            not value['cancelled'] and record['status'] == 'completed',
                            '；'.join(record['data']['errors'][-2:]) or ('采集已停止' if value['cancelled'] else '检查未完成'))
                        self.append_log(f'订阅本轮新增 {new} 条；未下载的新增内容保留在资料管理中。')
            except Exception as error:
                applog.get('ui').exception('collection bookkeeping failed')
                self.append_log(f'采集记录保存失败：{error}')
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
                    self.append_log(f'采集结果无法载入：{error}')
            self.collect_text.configure(state='normal' if any(p['text'] for p in self.collected_pages) else 'disabled')
            self.collection_selection_changed()
            status = '已停止，已采集结果保留' if value['cancelled'] else '采集结束'
            # One style for numbers (polish ui-wording-consistency-4): '3 页', the running status's spacing.
            suffix = f'；{self.collection_errors} 个错误，详情见任务日志' if self.collection_errors else ''
            if len(self.collected_links) >= MAX_LINKS:
                suffix += f'；已达 {MAX_LINKS} 条上限，请分批采集'
            tone = 'warn' if self.collection_errors else ('idle' if value['cancelled'] else 'ok')
            self.set_collect_status(f'{status}：{len(self.collected_pages)} 页 / {len(self.collected_links)} 条链接{suffix}', tone)
            self.refresh_library()

    def render_collection_links(self):
        term = self.collect_search.get().casefold()
        shown = []
        for item in self.collected_links.values():
            if self.collect_filter.get() == '媒体候选' and item['kind'] == '网页链接':
                continue
            if term and term not in (item['url']+' '+item['title']).casefold():
                continue
            shown.append(item)
        self.visible_collection_links = shown
        # The iids are positions in the new list: an old selection would point at other links.
        self.collect_tree.selection_remove(self.collect_tree.selection())
        self.collect_tree.fill((str(index), (item['kind'], item['title'], item['url'])) for index, item in enumerate(shown))
        self.collection_selection_changed()

    def collection_selection_changed(self):
        count = len(self.collect_tree.selection())
        total = len(self.collect_tree.get_children())
        self.collect_count.set(f'显示 {total} / 共 {len(self.collected_links)} · 已选 {count}')
        self.collect_add.configure(text=f'{ADD_TEXT} ({count})' if count else ADD_TEXT,
                                   state='normal' if count and self.can_add_collected() else 'disabled')
        self.collect_export.configure(state='normal' if count else 'disabled')
        self._dim_collect_box(bool(self.busy))      # set_busy calls this method

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
                self.append_log(f'这条采集链接无法完整放入下载列表，已跳过：{item["url"]}')
        items = kept
        if not items:
            return
        added = self.add_links('\n'.join(queued), source='collect')
        # Keys are the links as queued; read_form() also finds them through the origin of a cleaned link.
        self.download_referers.update({url: i['source'] for url, i in zip(queued, items) if i['url'] != i['source']})
        self.show_page('download')
        self.append_log(f'采集结果已加入下载列表：新增 {added} 条，跳过重复 {len(items) - added} 条。')

    def export_collected_links(self):
        items = self.selected_collection_links()
        if not items:
            return
        path = filedialog.asksaveasfilename(parent=self, title='保存所选链接', defaultextension='.txt', filetypes=[('链接列表', '*.txt')], initialfile='采集链接.txt')
        if path:
            try:
                Path(path).write_text('\n'.join(i['url'] for i in items)+'\n', encoding='utf-8-sig')
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=self)

    def export_collected_text(self):
        pages = [p for p in self.collected_pages if p['text']]
        if not pages:
            return
        path = filedialog.asksaveasfilename(parent=self, title='保存采集到的网页正文', defaultextension='.txt', filetypes=[('纯文本', '*.txt'), ('Markdown', '*.md')], initialfile='网页正文.txt')
        if path:
            try:
                markdown = Path(path).suffix.lower() == '.md'
                parts = []
                for p in pages:
                    note = '\n说明：该页正文超出 40 万字符，以下为截断内容。' if p.get('text_truncated') else ''
                    parts.append(f'{"# " if markdown else ""}{p["title"]}\n来源：{p["url"]}{note}\n\n{p["markdown"] if markdown else p["text"]}')
                Path(path).write_text('\n\n---\n\n'.join(parts)+'\n', encoding='utf-8-sig')
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=self)
