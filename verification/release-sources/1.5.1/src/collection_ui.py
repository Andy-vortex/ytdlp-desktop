"""Page collection workspace for the existing desktop downloader."""
from pathlib import Path
from dataclasses import asdict
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk
from collector import CollectOptions, CollectionRunner, MAX_LINKS, browser_available
from engine import parse_urls


class CollectionUI:
    def build_collection_page(self):
        page = self.page('collect', '网页采集', '从网页发现音视频链接，筛选后加入下载队列；也可保存网页正文。')
        page.grid_rowconfigure(3, weight=1)
        self.collected_links = {}
        self.collected_pages = []
        self.collection_errors = 0
        self.download_referers = {}
        self.collect_mode = tk.StringVar(value='普通网页')
        self.collect_scrolls = tk.StringVar(value='0')
        self.collect_pages_limit = tk.StringVar(value='1')
        self.collect_filter = tk.StringVar(value='媒体候选')
        self.collect_search = tk.StringVar()
        self.collect_selector = tk.StringVar()

        inputs = self.card(page)
        inputs.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        inputs.grid_columnconfigure(0, weight=1)
        self.label(inputs, '起始网页（每行一个，最多20个）', font=self.bold).grid(row=0, column=0, sticky='w', padx=17, pady=(12, 5))
        self.collect_urls = ctk.CTkTextbox(inputs, height=58, font=self.font, wrap='word', fg_color='#F7F9FD', border_width=1, border_color='#E5EAF3')
        self.collect_urls.grid(row=1, column=0, sticky='ew', padx=17)
        settings = ctk.CTkFrame(inputs, fg_color='transparent')
        settings.grid(row=2, column=0, sticky='ew', padx=17, pady=(8, 5))
        self.label(settings, '方式', font=self.small).pack(side='left')
        mode = ctk.CTkOptionMenu(settings, variable=self.collect_mode, values=['普通网页', '动态网页（Chrome）'], width=175, font=self.small)
        mode.pack(side='left', padx=(8, 16))
        self.label(settings, '滚动次数', font=self.small).pack(side='left')
        scroll = ctk.CTkOptionMenu(settings, variable=self.collect_scrolls, values=[str(n) for n in range(6)], width=58)
        scroll.pack(side='left', padx=(6, 16))
        self.label(settings, '每站最多页数', font=self.small).pack(side='left')
        limit = ctk.CTkOptionMenu(settings, variable=self.collect_pages_limit, values=[str(n) for n in range(1, 6)], width=58)
        limit.pack(side='left', padx=6)
        advanced = ctk.CTkFrame(inputs, fg_color='transparent')
        advanced.grid(row=3, column=0, sticky='ew', padx=17, pady=(2, 8))
        advanced.grid_columnconfigure(1, weight=1)
        self.label(advanced, '内容范围（可选CSS）', font=self.small).grid(row=0, column=0, padx=(0, 8))
        scope = ctk.CTkEntry(advanced, textvariable=self.collect_selector, placeholder_text='留空提取整页链接；正文优先 article / main', font=self.small)
        scope.grid(row=0, column=1, sticky='ew')
        self.label(inputs, '翻页仅跟随同站 rel=next；动态模式使用独立临时Chrome，不读取现有浏览器登录状态。', text_color='#7A879C', font=self.small).grid(row=4, column=0, sticky='w', padx=17, pady=(0, 10))
        self.controls += [self.collect_urls, mode, scroll, limit, scope]
        bar = ctk.CTkFrame(page, fg_color='transparent')
        bar.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        self.collect_start = self.button(bar, '开始采集', self.start_collection, primary=True)
        self.collect_start.pack(side='left')
        self.collect_stop = self.button(bar, '停止', self.stop, state='disabled', width=65)
        self.collect_stop.pack(side='left', padx=8)
        subscribe = self.button(bar, '保存为订阅', self.save_subscription, width=100)
        subscribe.pack(side='left')
        self.controls.append(subscribe)
        self.collect_status = self.label(bar, '支持普通页面和动态页面；采集结果供你选择，不会自动下载。', font=self.small, text_color='#7A879C', wraplength=400, anchor='w', justify='left')
        self.collect_status.pack(side='left', padx=8)
        self.controls.append(self.collect_start)
        result = self.card(page)
        result.grid(row=3, column=0, sticky='nsew')
        result.grid_rowconfigure(1, weight=1)
        result.grid_columnconfigure(0, weight=1)
        toolbar = ctk.CTkFrame(result, fg_color='transparent')
        toolbar.grid(row=0, column=0, sticky='ew', padx=14, pady=10)
        self.label(toolbar, '发现的链接', font=self.bold).pack(side='left', padx=(0, 12))
        ctk.CTkOptionMenu(toolbar, variable=self.collect_filter, values=['媒体候选', '全部链接'], width=112, font=self.small).pack(side='left')
        self.label(toolbar, '搜索', font=self.small).pack(side='left', padx=(10, 0))
        ctk.CTkEntry(toolbar, textvariable=self.collect_search, placeholder_text='筛选标题或地址', width=190, font=self.small).pack(side='left', padx=8)
        self.collect_count = self.label(toolbar, '0 条', font=self.small, text_color='#3268EB')
        self.collect_count.pack(side='right')
        frame = tk.Frame(result, background='white')
        frame.grid(row=1, column=0, sticky='nsew', padx=14)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure('Collect.Treeview', rowheight=29, font=('Microsoft YaHei UI', 10), background='#FFFFFF', fieldbackground='#FFFFFF')
        style.configure('Collect.Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'))
        self.collect_tree = ttk.Treeview(frame, columns=('kind', 'title', 'url'), show='headings', selectmode='extended', style='Collect.Treeview')
        for key, title, width in [('kind', '类型', 85), ('title', '标题', 230), ('url', '链接', 350)]:
            self.collect_tree.heading(key, text=title)
            self.collect_tree.column(key, width=width, minwidth=60, stretch=key!='kind')
        self.collect_tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.collect_tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.collect_tree.configure(yscrollcommand=scrollbar.set)
        self.collect_tree.bind('<<TreeviewSelect>>', lambda _: self.collection_selection_changed())
        footer = ctk.CTkFrame(result, fg_color='transparent')
        footer.grid(row=2, column=0, sticky='ew', padx=14, pady=10)
        self.button(footer, '全选当前', lambda: self.collect_tree.selection_set(self.collect_tree.get_children()), width=85).pack(side='left')
        self.collect_add = self.button(footer, '加入下载队列', self.add_collected, primary=True, width=120, state='disabled')
        self.collect_add.pack(side='left', padx=7)
        self.collect_export = self.button(footer, '导出链接TXT', self.export_collected_links, width=110, state='disabled')
        self.collect_export.pack(side='left')
        self.collect_text = self.button(footer, '保存网页正文', self.export_collected_text, width=120, state='disabled')
        self.collect_text.pack(side='right')
        self.controls.append(self.collect_add)
        self.collect_filter.trace_add('write', lambda *_: self.render_collection_links())
        self.collect_search.trace_add('write', lambda *_: self.render_collection_links())
        self.render_collection_links()

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
            self.collect_mode.get() != '普通网页', int(self.collect_scrolls.get()),
            self.collect_selector.get().strip(), int(self.collect_pages_limit.get()), self.proxy.get().strip()).validate()

    def launch_collection(self, options, job_id=None, title=None):
        if not job_id:
            self.collected_links.clear()
            self.collected_pages.clear()
            job_id = self.library.create_job('collect', title or options.urls[0], asdict(options), self.active_subscription)
        else:
            self.library.change_job(job_id, status='running', errors=[], owner=__import__('os').getpid(), worker=None)
        self.collection_job = job_id
        options.job_id = job_id
        options.history_path = str(self.library.path)
        self.collection_errors = 0
        self.render_collection_links()
        self.collect_text.configure(state='disabled')
        self.operation = 'collect'
        self.set_busy(True)
        self.collect_stop.configure(state='normal')
        self.collect_status.configure(text='正在准备采集…', text_color='#3268EB')
        self.runner = CollectionRunner(lambda kind, value: self.events.put((kind, value)))
        threading.Thread(target=self.runner.run, args=(options,), daemon=True).start()

    def handle_collection(self, kind, value):
        if kind == 'collect_status':
            self.collect_status.configure(text=value)
        elif kind == 'collect_page':
            value = {k:v for k,v in value.items() if k != '_checkpoint'}
            self.collected_pages.append(value)
            for link in value['links']:
                if len(self.collected_links) < MAX_LINKS:
                    self.collected_links.setdefault(link['url'], link)
            self.render_collection_links()
            self.collect_status.configure(text=f'已采集 {len(self.collected_pages)} 页，发现 {len(self.collected_links)} 条链接')
            self.append_log(f'网页采集：{value["title"]}，{len(value["links"])} 条链接')
            if value.get('text_truncated'):
                self.append_log('本页正文超过40万字符，已截断；导出文件会注明。')
        elif kind == 'collect_error':
            self.collection_errors += 1
            self.append_log(f'网页采集未完成：{value}')
            self.collect_status.configure(text=f'部分页面未完成：{value[:85]}', text_color='#C17A16')
        elif kind == 'collect_done':
            if self.collection_job:
                record = self.library.job(self.collection_job)
                self.load_collection_record(record)
                if self.active_subscription:
                    new = self.library.finish_subscription(self.active_subscription, record['pages'],
                        not value['cancelled'] and record['status'] == 'completed',
                        '；'.join(record['data']['errors'][-2:]) or ('采集已停止' if value['cancelled'] else '检查未完成'))
                    self.append_log(f'订阅本轮新增{new}条；未下载的新增内容保留在资料管理中。')
                self.collection_job = None
                self.active_subscription = None
            self.set_busy(False)
            self.collect_stop.configure(state='disabled')
            self.collect_text.configure(state='normal' if any(p['text'] for p in self.collected_pages) else 'disabled')
            self.collection_selection_changed()
            status = '已停止，已采集结果保留' if value['cancelled'] else '采集结束'
            suffix = f'；{self.collection_errors}个错误，详情见任务日志' if self.collection_errors else ''
            if len(self.collected_links) >= MAX_LINKS:
                suffix += f'；已达{MAX_LINKS}条上限，请分批采集'
            self.collect_status.configure(text=f'{status}：{len(self.collected_pages)}页 / {len(self.collected_links)}条链接{suffix}', text_color='#C17A16' if self.collection_errors else '#16966D')
            self.refresh_library()

    def render_collection_links(self):
        self.collect_tree.delete(*self.collect_tree.get_children())
        term = self.collect_search.get().casefold()
        shown = []
        for item in self.collected_links.values():
            if self.collect_filter.get() == '媒体候选' and item['kind'] == '网页链接':
                continue
            if term and term not in (item['url']+' '+item['title']).casefold():
                continue
            shown.append(item)
        self.visible_collection_links = shown
        for index, item in enumerate(shown):
            self.collect_tree.insert('', 'end', iid=str(index), values=(item['kind'], item['title'], item['url']))
        self.collection_selection_changed()

    def collection_selection_changed(self):
        count = len(self.collect_tree.selection())
        total = len(self.collect_tree.get_children())
        self.collect_count.configure(text=f'显示 {total} / 共 {len(self.collected_links)} · 已选 {count}')
        self.collect_add.configure(state='normal' if count and not self.busy else 'disabled')
        self.collect_export.configure(state='normal' if count else 'disabled')

    def selected_collection_links(self):
        return [self.visible_collection_links[int(i)] for i in self.collect_tree.selection()]

    def add_collected(self):
        if self.busy:
            return
        items = self.selected_collection_links()
        self.queue_collected_items(items)

    def queue_collected_items(self, items):
        if self.busy:
            return
        if not items:
            return
        current = self.urls.get('1.0', 'end-1c').strip()
        try:
            existing = parse_urls(current) if current else []
        except ValueError as error:
            messagebox.showwarning('请先修正下载列表', str(error), parent=self)
            return
        new = [item['url'] for item in items if item['url'] not in existing]
        if new:
            self.add_urls('\n'.join(new))
        self.download_referers.update({i['url']: i['source'] for i in items if i['url'] != i['source']})
        self.show_page('download')
        self.append_log(f'采集结果已加入下载列表：新增{len(new)}条，跳过重复{len(items)-len(new)}条。')

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
                    note = '\n说明：该页正文超出40万字符，以下为截断内容。' if p.get('text_truncated') else ''
                    parts.append(f'{"# " if markdown else ""}{p["title"]}\n来源：{p["url"]}{note}\n\n{p["markdown"] if markdown else p["text"]}')
                Path(path).write_text('\n\n---\n\n'.join(parts)+'\n', encoding='utf-8-sig')
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=self)
