"""界面文字（中文 / 英文）：资料页：任务历史、订阅、统一归档。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
GROUPS：菜单的值 → 本文件的编号。TAGS：日志行的颜色标记。
"""

TEXTS = {
    'dialog.archive_failed': ('归档失败', 'Archiving failed'),
    'dialog.attach_failed': ('关联失败', 'Could not link'),
    'dialog.open_failed': ('打开失败', 'Could not open'),
    'dialog.resume.done.text': ('媒体都已下载。归档问题见日志。', 'All media is downloaded. Archive issues are in the Log.'),
    'dialog.resume.done.title': ('下载已完成', 'Download complete'),
    'dialog.resume.failed.text': ('这条任务的下载设置已不可用。', "This task's download settings are no longer usable."),
    'dialog.resume.failed.title': ('无法继续', 'Cannot continue'),
    'dialog.resume_collect_failed': ('无法继续采集', 'Cannot continue collecting'),
    'dialog.subscription.not_started': ('检查未开始', 'Check not started'),
    'dialog.subscription.save_failed': ('保存订阅失败', 'Could not save the subscription'),
    'dialog.transcript.title': ('关联转写稿 · 复制，不移动原文件', 'Link transcript · copied, not moved'),
    'library.archive.attach': ('关联转写稿…', 'Link transcript…'),
    'library.archive.count': ('共 {total} 条', 'Total: {total}'),
    'library.archive.empty': ('还没有归档', 'Nothing archived yet'),
    'library.archive.found': ('找到 {total} 条', 'Found: {total}'),
    'library.archive.go_settings': ('去设置', 'Go to Settings'),
    'library.archive.not_provided': ('—', '—'),
    'library.busy': ('资料库文件被其他程序占用，正在自动重试', 'Library file in use by another program; retrying'),
    'library.busy_click': ('资料库文件被其他程序占用，请稍后再试', 'Library file in use by another program; try again shortly'),
    'library.cap_note': (' · 已达 {limit} 条上限', ' · Limit reached: {limit}'),
    'library.history.count': ('共 {total} 条', 'Total: {total}'),
    'library.history.downloaded': ('已下载 {completed} 条', 'Downloaded: {completed}'),
    'library.history.empty': ('还没有任务', 'No tasks yet'),
    'library.history.found': ('找到 {total} 条', 'Found: {total}'),
    'library.history.last_error': (' · 最近错误：{error}', ' · Last error: {error}'),
    'library.history.more': ('加载更多', 'Load more'),
    'library.history.pages': ('已保存 {pages} 页 · {done}', 'Saved pages: {pages} · {done}'),
    'library.history.unreadable': ('这条任务暂时无法读取', "This task can't be read right now"),
    'library.interval.15min': ('每 15 分钟', 'Every 15 min'),
    'library.interval.daily': ('每天', 'Daily'),
    'library.interval.hourly': ('每小时', 'Hourly'),
    'library.interval.manual': ('仅手动', 'Manual only'),
    'library.kind.collect': ('采集', 'Collect'),
    'library.kind.download': ('下载', 'Download'),
    'library.never_checked': ('从未', 'Never'),
    'library.pending.count': ('新增 {count} 条', 'New: {count}'),
    'library.pending.empty': ('没有新增', 'Nothing new'),
    'library.pending.log_ignored': (
        '已忽略订阅“{name}”的 {count} 条新增内容。',
        'New items ignored: {count} · Subscription: {name}'),
    'library.resume': ('继续', 'Continue'),
    'library.state.checking': ('检查中', 'Checking'),
    'library.state.completed': ('已完成', 'Completed'),
    'library.state.due': ('待检查', 'Due'),
    'library.state.interrupted': ('已中断', 'Interrupted'),
    'library.state.partial': ('部分完成', 'Partly done'),
    'library.state.paused': ('已暂停', 'Paused'),
    'library.state.running': ('进行中', 'Running'),
    'library.subscription.check_now': ('立即检查', 'Check now'),
    'library.subscription.delete': ('删除订阅', 'Delete subscription'),
    'library.subscription.delete_busy': ('这个订阅正在检查，结束后再删除', 'This subscription is being checked; delete it afterwards'),
    'library.subscription.delete_task_busy': ('当前任务结束后才能删除', 'Delete it when the current task ends'),
    'library.subscription.delete_text': (
        '删除订阅“{name}”、它的 {pending} 条新增和已见记录？任务历史与已下载的文件会保留。删除后无法恢复。',
        'Delete “{name}”, its {pending} new items and its seen links? Task history and downloaded files are kept. This cannot be undone.'),
    'library.subscription.delete_title': ('删除订阅', 'Delete subscription'),
    'library.subscription.empty': ('还没有订阅', 'No subscriptions yet'),
    'library.subscription.go_collect': ('去采集', 'Go to Collect'),
    'library.subscription.gone': ('这个订阅已不存在', 'This subscription no longer exists'),
    'library.subscription.log_deleted': (
        '已删除订阅：{name}（待处理内容 {pending} 条）',
        'Subscription deleted: {name} · Pending items removed: {pending}'),
    'library.subscription.log_renamed': ('已将订阅改名为：{name}', 'Subscription renamed: {name}'),
    'library.subscription.pause': ('暂停', 'Pause'),
    'library.subscription.rename': ('改名…', 'Rename…'),
    'library.subscription.rename_title': ('改名', 'Rename'),
    'library.subscription.resume': ('恢复', 'Resume'),
    'library.tab.archive': ('归档', 'Archive'),
    'library.tab.history': ('历史', 'History'),
    'library.tab.subscriptions': ('订阅', 'Subscriptions'),
    'library.unreadable': ('历史无法读取：{error}', "Can't read the history: {error}"),
    'library.view_results': ('查看结果', 'View results'),
    'log.archive.library_busy': (
        '资料库正被其他程序占用：本次归档只记录原链接，不附采集来源和网页正文。',
        'Another program is using the library file: this archive records the original link only, without the collection source and page text.'),
    'log.archive.pages': ('已归档当前 {count} 个页面正文。', 'Page texts archived: {count}.'),
    'log.library.back': (
        '资料库已恢复，暂存的 {count} 条记录已写入。',
        'The library file is available again; held records written: {count}.'),
    'log.library.busy': (
        '资料库正被其他程序占用（{error}）：窗口不等待，稍后自动重试；新的记录会在可写入后补写。',
        'Another program is using the library file ({error}): the window does not wait and retries automatically; held records are written once the file is free again.'),
    'log.library.lost': (
        '资料库仍被其他程序占用，{count} 条记录没有写入。',
        'Another program is still using the library file; records not written: {count}.'),
    'log.resume.dropped': (
        '继续：同一链接的其他格式 {count} 条没有开始，可在“资料 → 历史”再次继续。',
        'Continue: other formats of the same links did not start ({count}); continue again in Library → History.'),
    'log.resume.later': ('继续：同一链接的其他格式 {count} 条已加入队列。', 'Continue: other formats of the same links queued: {count}.'),
    'log.subscription.deferred': (
        '订阅“{name}”会在资料库可写入后保存。',
        'Subscription “{name}” is saved once the library file can be written.'),
    'log.subscription.failed': ('订阅检查失败：{name} · {error}', 'Subscription check failed: {name} · {error}'),
    'log.subscription.saved': (
        '已保存订阅：{name}。检查范围沿用当前采集页数、CSS 和动态模式设置。',
        'Subscription saved: {name}. Checks use the current page limit, CSS and dynamic mode settings.'),
}

# 菜单的值 → 编号（i18n.label / choices 读合并后的 i18n.GROUPS）。只放编号；None 表示按原值显示。
GROUPS = {
    'interval': {
        900: 'library.interval.15min',
        3600: 'library.interval.hourly',
        86400: 'library.interval.daily',
        0: 'library.interval.manual',
    },
    'job_status': {
        'running': 'library.state.running',
        'completed': 'library.state.completed',
        'partial': 'library.state.partial',
        'interrupted': 'library.state.interrupted',
    },
    'job_kind': {
        'download': 'library.kind.download',
        'collect': 'library.kind.collect',
    },
    'library_tab': {
        'history': 'library.tab.history',
        'subscriptions': 'library.tab.subscriptions',
        'archive': 'library.tab.archive',
    },
}

# 日志行的颜色标记（ui_log.classify 经 i18n.log_tag 读合并后的 i18n.TAGS）：本文件的编号 → 'error' / 'ok'。
TAGS = {
    'log.subscription.failed': 'error',
}
