"""界面文字（中文 / 英文）：窗口外壳：侧栏、状态、窗口标题、启动与退出。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
TAGS：日志行的颜色标记。
"""

TEXTS = {
    'dialog.close_busy.text': ('当前任务尚未完成。要停止任务并关闭吗？', 'A task is still running. Stop it and close?'),
    'dialog.close_busy.title': ('停止并关闭', 'Stop and close'),
    'dialog.open_folder_failed': ('无法打开目录', 'Cannot open the folder'),
    'log.ready': (
        '就绪：yt-dlp {version}；FFmpeg、ffprobe 和 Deno 已内置。',
        'Ready: yt-dlp {version}; FFmpeg, ffprobe and Deno are built in.'),
    'log.settings_held': (
        '设置文件暂时无法读取（可能被其他程序占用），本次先用默认设置；原文件不会被覆盖。',
        'The settings file cannot be read right now (another program may be using it). Defaults are used this time; the file is not overwritten.'),
    'log.settings_not_saved': (
        '设置未保存：无法写入 settings.json，详情见日志文件。',
        'Settings not saved: settings.json cannot be written. See the log file for details.'),
    'log.ui_error': ('界面错误：{kind}: {value}', 'Interface error: {kind}: {value}'),
    'log.watchdog': ('后台任务意外结束，已恢复界面。', 'A background task ended unexpectedly; the window has been restored.'),
    'nav.collect': ('采集', 'Collect'),
    'nav.download': ('下载', 'Downloads'),
    'nav.library': ('资料', 'Library'),
    'nav.logs': ('日志', 'Log'),
    'nav.settings': ('设置', 'Settings'),
    'shell.engine_update': ('有新版', 'Update available'),
    'shell.notice.settings_held': (
        '设置文件暂不可读，本次用默认设置；原文件不会被覆盖',
        'Settings file unreadable; using defaults, the file is not overwritten'),
    'shell.status.bot_wait': ('已暂缓', 'On hold'),
    'shell.status.collecting': ('采集中', 'Collecting'),
    'shell.status.downloading': ('下载中', 'Downloading'),
    'shell.status.ready': ('就绪', 'Ready'),
    'shell.status.updating': ('更新中', 'Updating'),
    'shell.status.waiting': ('等待 {time}', 'Waiting {time}'),
    'window.title.done': ('✓ 已完成 {success} 条', '✓ Done: {success}'),
    'window.title.unfinished': ('✗ {unfinished} 条未完成', '✗ Unfinished: {unfinished}'),
}

# 日志行的颜色标记（ui_log.classify 经 i18n.log_tag 读合并后的 i18n.TAGS）：本文件的编号 → 'error' / 'ok'。
TAGS = {
    'log.settings_not_saved': 'error',
    'log.ui_error': 'error',
}
