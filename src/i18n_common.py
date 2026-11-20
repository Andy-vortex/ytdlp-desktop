"""界面文字（中文 / 英文）：几页共用的文字（按钮、表头、分隔符、文件对话框）。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
"""

TEXTS = {
    'common.cancel': ('取消', 'Cancel'),
    'common.choose_ellipsis': ('选择…', 'Choose…'),
    'common.clear': ('清除', 'Clear'),
    'common.got_it': ('知道了', 'Got it'),
    'common.ignore': ('忽略', 'Ignore'),
    'common.no_match': ('没有匹配项', 'No matches'),
    'common.open_folder': ('打开文件夹', 'Open folder'),
    'common.refresh': ('刷新', 'Refresh'),
    'common.save': ('保存', 'Save'),
    'common.search': ('搜索', 'Search'),
    'common.select_all': ('全选', 'Select all'),
    'common.sentence_join': ('', ' '),
    'common.stop': ('停止', 'Stop'),
    'common.unfinished': ('未完成', 'Unfinished'),
    'dialog.filetype.all': ('所有文件', 'All files'),
    'dialog.filetype.link_list': ('链接列表', 'Link list'),
    'dialog.filetype.text': ('纯文本', 'Plain text'),
    'dialog.filetype.text_files': ('文本文件', 'Text files'),
    'dialog.filetype.transcript': ('转写稿', 'Transcripts'),
    'error.archive_root_missing': ('请先选择资料库目录。', 'Choose an archive folder first.'),
    'error.output_missing': ('请选择文件夹。', 'Choose a folder.'),
    'table.author': ('作者', 'Author'),
    'table.last_success': ('上次成功', 'Last success'),
    'table.link': ('链接', 'Link'),
    'table.media_transcripts': ('媒体 / 转写稿', 'Media / Transcripts'),
    'table.pending': ('新增', 'New'),
    'table.schedule': ('计划', 'Schedule'),
    'table.source': ('来源', 'Source'),
    'table.status': ('状态', 'Status'),
    'table.subscription': ('订阅', 'Subscription'),
    'table.task': ('任务', 'Task'),
    'table.title': ('标题', 'Title'),
    'table.type': ('类型', 'Type'),
    'table.updated': ('更新时间', 'Updated'),
}
