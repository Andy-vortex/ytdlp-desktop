"""界面文字（中文 / 英文）：采集页（含链接种类的显示名）。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
GROUPS：菜单的值 → 本文件的编号。TAGS：日志行的颜色标记。
"""

TEXTS = {
    'collect.add': ('加入下载', 'Add to downloads'),
    'collect.archive_text': ('归档正文', 'Archive page text'),
    'collect.background': ('正在检查订阅“{name}”', 'Checking subscription “{name}”'),
    'collect.count': ('{shown} / {total} 条', '{shown} / {total}'),
    'collect.empty': ('还没有结果', 'No results yet'),
    'collect.export_links': ('导出链接…', 'Export links…'),
    'collect.filter.all': ('全部链接', 'All links'),
    'collect.filter.media': ('媒体链接', 'Media links'),
    'collect.mode.dynamic': ('动态网页', 'Dynamic page'),
    'collect.mode.static': ('普通网页', 'Static page'),
    'collect.note.dynamic': ('使用独立的临时 Chrome，不读取浏览器登录', 'Uses a separate temporary Chrome; never reads your sign-ins'),
    'collect.option.selector': ('CSS 范围', 'CSS scope'),
    'collect.placeholder': ('粘贴网页链接，每行一条', 'Paste page links, one per line'),
    'collect.save_subscription': ('订阅', 'Subscribe'),
    'collect.save_text': ('保存正文…', 'Save page text…'),
    'collect.start': ('开始采集', 'Start collecting'),
    'collect.status.errors_suffix': (' · {errors} 个错误，见日志', ' · Errors: {errors}, see Log'),
    'collect.status.finished': ('已完成', 'Done'),
    'collect.status.from_history': ('历史结果 · {pages} 页 · {links} 条链接', 'From history · Pages: {pages} · Links: {links}'),
    'collect.status.library_dropped': ('未开始：等待期间开始了其他任务', 'Not started: another task began while waiting'),
    'collect.status.library_gave_up': ('未开始：资料库文件仍被占用，请稍后再试', 'Not started: library file still in use, try again later'),
    'collect.status.limit_suffix': (' · 已达 {limit} 条上限，请分批采集', ' · Limit reached: {limit}; collect in batches'),
    'collect.status.not_started': (
        '未开始：历史记录暂时无法写入，请稍后再试',
        "Not started: the history can't be written, try again later"),
    'collect.status.partial': ('部分网页未完成：{error}', 'Some pages did not finish: {error}'),
    'collect.status.preparing': ('正在准备采集…', 'Preparing to collect…'),
    'collect.status.progress': ('已采集 {pages} 页 · {links} 条链接', 'Pages: {pages} · Links: {links}'),
    'collect.status.stopped': ('已停止，结果已保留', 'Stopped; results kept'),
    'collect.status.summary': ('{status} · {pages} 页 · {links} 条链接{suffix}', '{status} · Pages: {pages} · Links: {links}{suffix}'),
    'collect.status.waiting_library': (
        '资料库文件被其他程序占用，可写入后自动开始…',
        'Library file in use by another program; starts when free…'),
    'collect.truncated': ('本页正文超过 40 万字，已截断；导出文件会注明。', 'Page text over 400,000 characters was cut; exports say so.'),
    'collect.value.pages': ('{count} 页', '{count} pages'),
    'collect.value.pages_one': ('1 页', '1 page'),
    'collect.value.scrolls': ('滚动 {count} 次', '{count} scrolls'),
    'collect.value.scrolls_one': ('滚动 1 次', '1 scroll'),
    'dialog.collect.check_settings': ('检查设置', 'Check settings'),
    'dialog.collect.not_started.text': (
        '历史记录暂时无法写入，采集未开始。请稍后再试。\n\n{error}',
        "The history can't be written, so collecting did not start. Try again later.\n\n{error}"),
    'dialog.collect.not_started.title': ('采集未开始', 'Not started'),
    'dialog.export_links.file': ('采集链接.txt', 'collected-links.txt'),
    'dialog.export_links.title': ('保存所选链接', 'Save selected links'),
    'dialog.export_text.file': ('网页正文.txt', 'page-text.txt'),
    'dialog.export_text.title': ('保存网页正文', 'Save page text'),
    'dialog.save_failed': ('保存失败', 'Save failed'),
    'filetext.export_page': (
        '{heading}{title}\n来源：{url}{note}\n\n{body}',
        '{heading}{title}\nSource: {url}{note}\n\n{body}'),
    'filetext.truncated_export': (
        '\n说明：该页正文超出 40 万字符，以下为截断内容。',
        "\nNote: this page's text exceeds 400,000 characters; the text below is cut off."),
    'log.collect.link_skipped': (
        '这条采集链接无法完整放入下载列表，已跳过：{url}',
        'This collected link cannot be added to the download list in full and was skipped: {url}'),
    'log.collect.load_failed': ('采集结果无法载入：{error}', 'The collection results could not be loaded: {error}'),
    'log.collect.not_started': (
        '网页采集未开始：历史记录无法写入（{error}）',
        'Web collection did not start: the history cannot be written ({error})'),
    'log.collect.page': ('网页采集：{title}，{count} 条链接', 'Page collected: {title} · Links: {count}'),
    'log.collect.queued': (
        '采集结果已加入下载：新增 {added} 条，跳过重复 {skipped} 条。',
        'Collected links added to downloads · Added: {added} · Duplicates skipped: {skipped}'),
    'log.collect.record_failed': ('采集记录保存失败：{error}', 'The collection record could not be saved: {error}'),
    'log.collect.unfinished': ('网页采集未完成：{error}', 'Web collection not finished: {error}'),
    'log.subscription.new': (
        '订阅本轮新增 {new} 条；未下载的新增内容保留在资料页。',
        'New items in this check: {new}; those not downloaded stay in the Library.'),
    'value.link_kind.direct': ('直链', 'Direct'),
    'value.link_kind.dynamic_request': ('页面请求', 'Page request'),
    'value.link_kind.media': ('媒体', 'Media'),
    'value.link_kind.video_page': ('视频页', 'Video page'),
    'value.link_kind.web': ('网页', 'Page'),
}

# 菜单的值 → 编号（i18n.label / choices 读合并后的 i18n.GROUPS）。只放编号；None 表示按原值显示。
GROUPS = {
    'link_kind': {
        'media': 'value.link_kind.media',
        'video_page': 'value.link_kind.video_page',
        'web': 'value.link_kind.web',
        'direct': 'value.link_kind.direct',
        'dynamic_request': 'value.link_kind.dynamic_request',
    },
    'collect_mode': {
        'static': 'collect.mode.static',
        'dynamic': 'collect.mode.dynamic',
    },
    'collect_filter': {
        'media': 'collect.filter.media',
        'all': 'collect.filter.all',
    },
}

# 日志行的颜色标记（ui_log.classify 经 i18n.log_tag 读合并后的 i18n.TAGS）：本文件的编号 → 'error' / 'ok'。
TAGS = {
    'log.collect.unfinished': 'error',
}
