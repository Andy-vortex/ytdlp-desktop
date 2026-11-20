"""界面文字（中文 / 英文）：设置页（含语言一行的重启流程）。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
GROUPS：菜单的值 → 本文件的编号。TAGS：日志行的颜色标记。
"""

TEXTS = {
    'dialog.cookies.filetype': ('cookies 文件', 'Cookies file'),
    'dialog.cookies.title': ('选择 cookies.txt', 'Choose cookies.txt'),
    'dialog.cookies.unusable': ('cookies 文件不可用', 'Cookies file not usable'),
    'dialog.restart_failed.text': (
        '无法自动重启，请关闭后再打开。\n\n{error}',
        "Couldn't restart. Close the app and open it again.\n\n{error}"),
    'dialog.restart_failed.title': ('无法重启', 'Cannot restart'),
    'dialog.restart_unsaved.text': (
        '链接框和采集网页里的内容无法保留，重启后会清空。仍然重启？',
        "The links and pages you typed can't be kept and will be cleared. Restart anyway?"),
    'dialog.restart_unsaved.title': ('重启', 'Restart'),
    'dialog.restore.failed': ('恢复未完成', 'Restore not finished'),
    'dialog.restore.text': (
        '将删除已更新的 yt-dlp，改用内置版本。继续？',
        'Deletes the updated yt-dlp and uses the built-in one. Continue?'),
    'log.cookies_set': ('已设置 cookies 文件；之后的下载会带上登录信息。', 'Cookies file set; later downloads use its sign-in.'),
    'log.engine_checked': ('引擎检查完成：{version}', 'Engine check finished: {version}'),
    'log.engine_restored': ('已恢复内置引擎：yt-dlp {version}', 'Built-in engine restored: yt-dlp {version}'),
    'log.restart_restored': ('已恢复重启前的输入内容。', 'The input from before the restart was restored.'),
    'log.update_failed': ('更新失败：{error}', 'Update failed: {error}'),
    'settings.appearance.dark': ('深色', 'Dark'),
    'settings.appearance.light': ('浅色', 'Light'),
    'settings.appearance.system': ('跟随系统', 'System'),
    'settings.appearance.title': ('外观', 'Appearance'),
    'settings.archive.folder': ('归档文件夹', 'Archive folder'),
    'settings.archive.title': ('统一归档', 'Archive downloads'),
    'settings.clipboard.title': ('识别剪贴板', 'Watch clipboard'),
    'settings.component.app': ('桌面应用', 'Desktop app'),
    'settings.component.builtin': ('内置', 'Built-in'),
    'settings.cookies.text': ('等同登录凭证，勿外传', 'Works like a sign-in; never share it'),
    'settings.cookies.title': ('YouTube cookies', 'YouTube cookies'),
    'settings.cover.title': ('嵌入封面', 'Embed cover'),
    'settings.engine.check': ('检查更新', 'Check for updates'),
    'settings.engine.daily.title': ('每日检查', 'Daily check'),
    'settings.engine.restore': ('恢复内置引擎', 'Restore built-in engine'),
    'settings.group.about': ('关于', 'About'),
    'settings.group.downloads': ('下载', 'Downloads'),
    'settings.group.engine': ('引擎', 'Engine'),
    'settings.group.interface': ('界面', 'Interface'),
    'settings.group.network': ('网络', 'Network'),
    'settings.group.subscriptions': ('新订阅', 'New subscriptions'),
    'settings.language.en': ('English', 'English'),
    'settings.language.env_fixed': (
        'YTDLP_DESKTOP_LANG 固定了本次语言；选择已保存，去掉它后生效',
        "YTDLP_DESKTOP_LANG sets this session's language; your choice applies without it"),
    'settings.language.held': (
        '设置文件无法读取，本次语言更改不会保存',
        "Settings file unreadable; the language change won't be saved"),
    'settings.language.restart': ('立即重启', 'Restart now'),
    'settings.language.restart_busy': ('任务结束后可重启', 'Restart when the task ends'),
    'settings.language.restart_note': ('重启后生效', 'Applies after restart'),
    'settings.language.system': ('跟随系统', 'System'),
    'settings.language.title': ('语言 / Language', 'Language / 语言'),
    'settings.language.zh': ('简体中文', '简体中文'),
    'settings.naming.title': ('文件命名', 'File naming'),
    'settings.notify.title': ('完成提醒', 'Notify when done'),
    'settings.proxy.placeholder': ('如 http://127.0.0.1:7890', 'e.g. http://127.0.0.1:7890'),
    'settings.proxy.title': ('本次代理', 'Proxy for this session'),
    'settings.skip.title': ('跳过已下载过', 'Skip downloaded before'),
    'settings.steady.title': ('稳健模式', 'Steady mode'),
    'settings.subscription.first_new': ('包含已有内容', 'Include existing items'),
    'settings.subscription.media_only': ('仅媒体链接', 'Media links only'),
    'settings.subscription.schedule': ('计划', 'Schedule'),
    'settings.tags.title': ('写入标签', 'Write tags'),
    'settings.update.available': ('有新版 {latest}', '{latest} available'),
    'settings.update.cancelled': ('更新已停止', 'Update stopped'),
    'settings.update.done': ('已更新', 'Updated'),
    'settings.update.downloading_pct': ('下载中 {percent}', 'Downloading {percent}'),
    'settings.update.failed': (
        '更新失败，现有引擎仍可用：{error}',
        'Update failed; the current engine still works: {error}'),
    'settings.update.latest': ('已是最新版', 'Up to date'),
    'settings.update.restored': ('已恢复内置引擎', 'Built-in engine restored'),
}

# 菜单的值 → 编号（i18n.label / choices 读合并后的 i18n.GROUPS）。只放编号；None 表示按原值显示。
GROUPS = {
    'appearance': {
        'light': 'settings.appearance.light',
        'dark': 'settings.appearance.dark',
        'system': 'settings.appearance.system',
    },
    'language': {
        'system': 'settings.language.system',
        'zh': 'settings.language.zh',
        'en': 'settings.language.en',
    },
}

# 日志行的颜色标记（ui_log.classify 经 i18n.log_tag 读合并后的 i18n.TAGS）：本文件的编号 → 'error' / 'ok'。
TAGS = {
    'log.update_failed': 'error',
}
