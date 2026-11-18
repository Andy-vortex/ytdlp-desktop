"""Task recovery, incremental subscriptions, and source archive UI."""
from dataclasses import asdict
from pathlib import Path
import os
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk
from collector import CollectOptions
from engine import APP_DIR, MODES, Options
from library import Archive, Library

INTERVALS = {'仅手动': 0, '每15分钟': 900, '每小时': 3600, '每天': 86400}
STATES = {'running': '进行中', 'completed': '已完成', 'partial': '部分完成', 'interrupted': '已中断'}


def clock(stamp):
    return time.strftime('%m-%d %H:%M', time.localtime(stamp)) if stamp else '尚未检查'


class LibraryUI:
    def init_library(self, settings):
        path = (Path(self.output.get()) / 'test-library.sqlite3') if self.smoke_mode else APP_DIR / 'data/library.sqlite3'
        self.library = Library(path)
        self.collection_job = None
        self.download_job = None
        self.active_subscription = None
        self.archive_enabled = tk.BooleanVar(value=False if self.smoke_mode else settings.get('archive_enabled', False))
        self.archive_root = tk.StringVar(value=str(Path(self.output.get()) / '资料归档') if self.smoke_mode else settings.get('archive_root', str(Path(self.output.get()) / '资料归档')))
        self.subscription_interval = tk.StringVar(value='每小时')
        self.subscription_media = tk.BooleanVar(value=True)
        self.subscription_first = tk.BooleanVar(value=True)
        self.subscription_name = tk.StringVar()
        self.archive_search = tk.StringVar()

    def table(self, parent, columns):
        frame = tk.Frame(parent, background='white')
        frame.pack(fill='both', expand=True, padx=8, pady=8)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', style='Collect.Treeview')
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=60)
        tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        scroll.pack(side='right', fill='y')
        tree.configure(yscrollcommand=scroll.set)
        return tree

    def build_library_page(self):
        page = self.page('library', '资料管理', '采集断点自动保存；订阅只收新增；媒体、正文与转写稿集中归档。')
        page.grid_rowconfigure(1, weight=1)
        self.library_tabs = ctk.CTkTabview(page, fg_color='white')
        self.library_tabs.grid(row=1, column=0, sticky='nsew')
        history = self.library_tabs.add('任务历史与续采')
        subscriptions = self.library_tabs.add('增量订阅')
        archive = self.library_tabs.add('统一资料归档')
        bar = ctk.CTkFrame(history, fg_color='transparent')
        bar.pack(fill='x', pady=8, padx=8)
        self.button(bar, '刷新记录', self.refresh_library).pack(side='left')
        self.history_view = self.button(bar, '查看结果', self.view_history)
        self.history_view.pack(side='left', padx=8)
        self.history_resume = self.button(bar, '继续未完成', self.resume_history, primary=True, width=130)
        self.history_resume.pack(side='left')
        self.controls += [self.history_view, self.history_resume]
        self.history_note = self.label(history, '已完成页不会重采；失败页和中断页可以继续。下载任务恢复未成功链接。', font=self.small, wraplength=780)
        self.history_note.pack(fill='x', padx=8)
        self.history_tree = self.table(history, [('kind', '类型', 70), ('title', '任务', 330), ('status', '状态', 90), ('time', '更新时间', 115)])
        self.history_tree.bind('<<TreeviewSelect>>', lambda _: self.history_selection())

        self.label(subscriptions, '定时检查仅在下载器打开时运行；忙碌时顺延，重新打开会补查到期订阅。', font=self.small, text_color='#7A879C').pack(anchor='w', padx=8, pady=6)
        form = ctk.CTkFrame(subscriptions, fg_color='transparent')
        form.pack(fill='x', padx=8)
        self.label(form, '名称', font=self.small).pack(side='left', padx=(0, 8))
        name = ctk.CTkEntry(form, textvariable=self.subscription_name, placeholder_text='订阅名称（使用网页采集页的当前设置）', width=295)
        name.pack(side='left')
        interval = ctk.CTkOptionMenu(form, variable=self.subscription_interval, values=list(INTERVALS), width=110)
        interval.pack(side='left', padx=8)
        create = self.button(form, '订阅当前采集设置', self.save_subscription, primary=True, width=155)
        create.pack(side='left')
        flags = ctk.CTkFrame(subscriptions, fg_color='transparent')
        flags.pack(fill='x', padx=8, pady=8)
        media = ctk.CTkCheckBox(flags, text='只跟踪媒体候选', variable=self.subscription_media, font=self.small)
        media.pack(side='left')
        first = ctk.CTkCheckBox(flags, text='首轮也列为新增（关闭则只建基线）', variable=self.subscription_first, font=self.small)
        first.pack(side='left', padx=20)
        self.controls += [name, interval, create, media, first]
        # Two bounded lists leave room for the new-content queue at minimum window size.
        self.subscription_tree = self.table(subscriptions, [('name', '订阅', 210), ('state', '计划', 110), ('last', '上次成功', 115), ('pending', '待处理', 70)])
        self.subscription_tree.configure(height=4)
        self.subscription_tree.bind('<<TreeviewSelect>>', lambda _: self.subscription_selection())
        actions = ctk.CTkFrame(subscriptions, fg_color='transparent')
        actions.pack(fill='x', padx=8)
        buttons = [self.button(actions, '立即检查', self.check_subscription, primary=True),
                   self.button(actions, '暂停 / 恢复', self.toggle_subscription, width=105),
                   self.button(actions, '应用检查间隔', self.change_interval, width=115),
                   self.button(actions, '新增加入下载队列', self.queue_subscription, width=145)]
        for button in buttons:
            button.pack(side='left', padx=(0, 8))
        self.controls += buttons
        self.subscription_note = self.label(subscriptions, '新增内容会保留，只有下载成功才从待处理列表消失；不会自动下载。', font=self.small, wraplength=780)
        self.subscription_note.pack(anchor='w', padx=8, pady=6)
        self.pending_tree = self.table(subscriptions, [('title', '新增内容（可多选）', 290), ('url', '链接', 420)])
        self.pending_tree.configure(selectmode='extended', height=4)

        row = ctk.CTkFrame(archive, fg_color='transparent')
        row.pack(fill='x', padx=8, pady=8)
        enabled = ctk.CTkCheckBox(row, text='下载时统一归档', variable=self.archive_enabled, command=self.save_settings, font=self.font)
        enabled.pack(side='left')
        self.label(row, '关闭时保留原下载目录和文件命名。', font=self.small, text_color='#7A879C').pack(side='left', padx=20)
        paths = ctk.CTkFrame(archive, fg_color='transparent')
        paths.pack(fill='x', padx=8)
        root = ctk.CTkEntry(paths, textvariable=self.archive_root)
        root.pack(side='left', fill='x', expand=True)
        choose = self.button(paths, '选择资料库', self.choose_archive)
        choose.pack(side='left', padx=(8, 0))
        self.controls += [enabled, root, choose]
        self.label(archive, '按来源站点与稳定ID分目录：媒体 / 网页正文TXT与MD / 资料信息 / 转写稿。旧文件不会被移动。', font=self.small, text_color='#7A879C', wraplength=780).pack(anchor='w', padx=8, pady=10)
        actions = ctk.CTkFrame(archive, fg_color='transparent')
        actions.pack(fill='x', padx=8)
        for title, callback in [('归档当前采集正文', self.archive_pages), ('打开资料目录', self.open_archive), ('关联转写稿', self.attach_transcript), ('刷新', self.refresh_library)]:
            button = self.button(actions, title, callback, width=130 if title.startswith('归档') else 105)
            button.pack(side='left', padx=(0, 8))
            self.controls.append(button)
        search = ctk.CTkFrame(archive, fg_color='transparent')
        search.pack(fill='x', padx=8, pady=(12, 2))
        self.label(search, '搜索标题、作者或来源', font=self.small).pack(side='left', padx=(0, 8))
        ctk.CTkEntry(search, textvariable=self.archive_search).pack(side='left', fill='x', expand=True)
        self.archive_tree = self.table(archive, [('title', '标题', 300), ('author', '作者', 100), ('files', '媒体 / 转写', 95), ('source', '来源', 210)])
        self.archive_search.trace_add('write', lambda *_: self.refresh_archives())
        self.refresh_library()
        self.after(1500, self.subscription_tick)

    def refresh_library(self):
        for tree, rows, render in [
            (self.history_tree, self.library.history(), lambda r: ('采集' if r['kind'] == 'collect' else '下载', r['title'], STATES.get(r['status'], r['status']), clock(r['updated']))),
            (self.subscription_tree, self.library.subscriptions(), lambda r: (r['name'], (next(k for k,v in INTERVALS.items() if v == r['interval']) if r['enabled'] else '已暂停'), clock(r['data']['last_check']), r['pending']))]:
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for row in rows:
                tree.insert('', 'end', iid=row['id'], values=render(row))
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
        self.history_selection()
        self.subscription_selection()
        self.refresh_archives()

    def history_selection(self):
        selected = self.history_tree.selection()
        if selected:
            record = self.library.job(selected[0])
            errors = record['data'].get('errors', [])
            self.history_note.configure(text=f"{len(record['pages'])}个已保存页面 · {len(record['data'].get('completed', []))}条下载成功" + (f' · 最近错误：{errors[-1][:130]}' if errors else ''))
        usable = bool(selected) and not self.busy
        self.history_view.configure(state='normal' if usable else 'disabled')
        self.history_resume.configure(state='normal' if usable and record['status'] not in ('running', 'completed') else 'disabled')

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
        self.collect_status.configure(text=f"历史结果：{len(record['pages'])}页，{len(self.collected_links)}条链接")

    def view_history(self):
        if self.busy or not self.history_tree.selection():
            return
        record = self.library.job(self.history_tree.selection()[0])
        if record['kind'] == 'collect':
            self.load_collection_record(record)
            self.show_page('collect')
        else:
            raw = record['data']['options']
            self.restore_download_options(raw)
            self.urls.delete('1.0', 'end')
            self.urls.insert('1.0', '\n'.join(raw['urls']))
            self.links_changed()
            self.show_page('download')

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
            pending = [url for url in raw['urls'] if url not in record['data']['completed']]
            if not pending:
                messagebox.showinfo('下载已完成', '媒体均已下载；如有归档错误，可查看任务日志和资料目录。', parent=self)
                return
            self.restore_download_options(raw)
            self.urls.delete('1.0', 'end')
            self.urls.insert('1.0', '\n'.join(pending))
            self.links_changed()
            self.show_page('download')
            self.start()

    def restore_download_options(self, raw):
        self.output.set(raw['output'])
        self.mode.set(raw['mode'])
        from studio import FORMATS
        self.format.set(FORMATS[MODES.index(raw['mode'])])
        self.resolution.set(raw['resolution'])
        self.playlist.set(raw['playlist'])
        self.batch_safe.set(raw['batch_safe'])
        self.archive_enabled.set(bool(raw.get('archive_root')))
        if raw.get('archive_root'):
            self.archive_root.set(raw['archive_root'])
        self.download_referers.update(raw.get('referers', {}))
        self.on_format()

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
            self.append_log(f'已保存订阅：{name}。检查范围沿用当前采集页数、CSS和动态模式设置。')
        except (OSError, ValueError) as error:
            messagebox.showwarning('保存订阅失败', str(error), parent=self)

    def selected_subscription(self):
        selected = self.subscription_tree.selection()
        return next((r for r in self.library.subscriptions() if selected and r['id'] == selected[0]), None)

    def subscription_selection(self):
        row = self.selected_subscription()
        self.pending_tree.delete(*self.pending_tree.get_children())
        self.visible_pending = self.library.pending(row['id']) if row else []
        for n, item in enumerate(self.visible_pending):
            self.pending_tree.insert('', 'end', iid=str(n), values=(item['title'], item['url']))
        if row:
            data = row['data']
            self.subscription_note.configure(text=(f"最近检查新增{data['last_new']}条 · 待处理{row['pending']}条 · " + (data['last_error'] or '仅下载成功后移出；可多选新增内容加入队列。')))

    def check_subscription(self, row=None, automatic=False):
        if self.busy or self.closing:
            return
        row = row or self.selected_subscription()
        if not row:
            return
        try:
            options = CollectOptions(**row['data']['options'])
            options.proxy = self.proxy.get().strip()
            options.validate()
            self.active_subscription = row['id']
            self.launch_collection(options, title='订阅：' + row['name'])
            if not automatic:
                self.show_page('collect')
        except (OSError, ValueError) as error:
            self.active_subscription = None
            self.library.finish_subscription(row['id'], [], False, str(error))
            self.append_log(f"订阅检查失败：{row['name']} · {error}")
            if not automatic:
                messagebox.showwarning('订阅未开始', str(error), parent=self)

    def subscription_tick(self):
        if not self.busy and not self.closing and not self.smoke_mode:
            for row in sorted(self.library.subscriptions(), key=lambda r: r['next_check']):
                if row['enabled'] and row['interval'] and row['next_check'] <= time.time():
                    self.check_subscription(row, automatic=True)
                    break
        if not self.closing:
            self.after(15000, self.subscription_tick)

    def toggle_subscription(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], not row['enabled'], row['interval'])
            self.refresh_library()

    def change_interval(self):
        row = self.selected_subscription()
        if row and not self.busy:
            self.library.configure_subscription(row['id'], row['enabled'], INTERVALS[self.subscription_interval.get()])
            self.refresh_library()

    def queue_subscription(self):
        if self.busy:
            return
        items = [self.visible_pending[int(n)] for n in self.pending_tree.selection()]
        if not items:
            messagebox.showinfo('选择新增内容', '请先在下方选择需要下载的新增条目。', parent=self)
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
            self.append_log(f'已归档当前{len(self.collected_pages)}个页面正文。')
            self.refresh_archives()
        except (ValueError, OSError) as error:
            messagebox.showerror('归档失败', str(error), parent=self)

    def refresh_archives(self):
        selected = self.archive_tree.selection()
        selected_folder = self.visible_archives[int(selected[0])]['folder'] if selected and hasattr(self, 'visible_archives') else None
        term = self.archive_search.get().casefold()
        self.visible_archives = [r for r in self.library.archives() if not term or term in (r['title'] + ' ' + str(r.get('uploader', '')) + ' ' + r['source']).casefold()]
        self.archive_tree.delete(*self.archive_tree.get_children())
        for n, row in enumerate(self.visible_archives):
            self.archive_tree.insert('', 'end', iid=str(n), values=(row['title'], row.get('uploader') or '未提供', f"{len(row['files'])} / {len(row['transcripts'])}", row['source']))
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

    def attach_transcript(self):
        row = self.selected_archive()
        if not row:
            return
        source = filedialog.askopenfilename(parent=self, title='关联现有转写稿（复制，保留原文件）', filetypes=[('转写稿', '*.txt *.srt *.md *.vtt')])
        if source:
            try:
                folder = Path(row['folder'])
                Archive(folder.parent.parent, self.library).attach(folder, source)
                self.refresh_archives()
            except (ValueError, OSError) as error:
                messagebox.showerror('关联失败', str(error), parent=self)
