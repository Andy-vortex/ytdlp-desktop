"""界面文字（中文 / 英文）：日志页。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
"""

TEXTS = {
    'dialog.open_log_failed': ('无法打开日志文件', 'Cannot open the log file'),
    'log.clear_view': ('清空显示', 'Clear view'),
    'log.copy_all': ('复制', 'Copy'),
    'log.filter.all': ('全部', 'All'),
    'log.filter.errors': ('错误', 'Errors'),
    'log.no_errors': ('没有错误', 'No errors'),
    'log.open_file': ('打开日志文件', 'Open log file'),
}
