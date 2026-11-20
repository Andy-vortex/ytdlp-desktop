"""界面文字（中文 / 英文）：核心模块的消息（messages.msg）、存储值的显示名、格式与命名、程序标题、归档文件里的文字。手写，是这些文字唯一的来源。

规则（verification/test_i18n_catalog.py 检查）：TEXTS 的编号按字母序，每条 '编号': ('中文', 'English')；
占位符 {name} 两边相同；英文里不出现中文。改文字只改这里，中英文一起看。
GROUPS：菜单的值 → 本文件的编号。TAGS：日志行的颜色标记。
"""

TEXTS = {
    'app.title': ('YT-DLP 下载器', 'YT-DLP Downloader'),
    'core.archive.cannot_set_aside': (
        '无法保留损坏的资料信息文件：{record}',
        'Could not set aside the damaged item info file: {record}'),
    'core.archive.kept_aside': (
        '资料信息文件读不出或不是本版本的格式，已原样保留为 {target}，本条重新建立：{folder}',
        "The item info file could not be read or is not in this version's format; it was kept unchanged as {target} and made again: {folder}"),
    'core.archive.media_mismatch': ('归档文件与本条资料目录不匹配。', "The archive file does not belong to this item's folder."),
    'core.archive.not_v2': (
        '资料信息文件读不出或不是本版本的格式，请重新归档这一条。',
        "The item info file could not be read or is not in this version's format; archive this item again."),
    'core.archive.outside_root': ('归档目录超出资料库范围。', 'The item folder lies outside the archive folder.'),
    'core.archive.transcript_outside': ('转写稿目录超出本条资料范围。', "The transcript folder lies outside this item's folder."),
    'core.archive.transcript_type': ('请选择 TXT、SRT、MD 或 VTT 转写稿。', 'Choose a TXT, SRT, MD or VTT transcript.'),
    'core.archive.unwritable': ('资料库无法写入：{error}', 'Cannot write to the archive folder: {error}'),
    'core.collect.bad_address': (
        '采集地址须为不含用户名或密码的 HTTP / HTTPS 链接。',
        'Web addresses to collect must be HTTP / HTTPS links without a user name or password.'),
    'core.collect.bad_proxy': ('请在设置中填写有效代理地址。', 'Enter a valid proxy address in Settings.'),
    'core.collect.bad_resume': ('历史续采位置无效。', 'The collection checkpoint in the history is not valid.'),
    'core.collect.bad_source': (
        '采集来源地址无效，请重新采集后加入下载列表。',
        'The source web address of these links is not valid. Collect again, then add them to the download list.'),
    'core.collect.ca_cache': (
        '无法建立 HTTPS 证书缓存，请将程序放到可写目录后重试。',
        'Could not create the HTTPS certificate cache. Move the program to a writable folder and try again.'),
    'core.collect.http_status': (
        '网站返回 HTTP {status}，可尝试动态模式或稍后重试。',
        'The site returned HTTP {status}. Try dynamic mode or try again later.'),
    'core.collect.limits': ('滚动次数需为 0–5，单个起始页的翻页上限需为 1–5。', 'Scrolls must be 0–5 and pages per start page 1–5.'),
    'core.collect.needs_chrome': (
        '动态采集需要安装 Google Chrome；普通网页模式无需浏览器。',
        'Dynamic collection needs Google Chrome; static page mode works without a browser.'),
    'core.collect.no_match': (
        '该页没有匹配的内容，请检查 CSS 范围或尝试动态模式。',
        'Nothing on this page matches. Check the CSS scope or try dynamic mode.'),
    'core.collect.page_n': ('正在采集第 {page} 页…', 'Collecting page {page}…'),
    'core.collect.too_large': ('页面超过 8 MB，请缩小采集范围。', 'The page is larger than 8 MB. Narrow the collection scope.'),
    'core.collect.too_many_pages': ('一次最多采集 20 个起始网页。', 'At most 20 start pages per collection.'),
    'core.collect.worker_exit': (
        '采集进程退出（{code}），请检查页面或动态模式所需的 Chrome。',
        'The collector process ended ({code}). Check the page, or Chrome for dynamic mode.'),
    'core.cookies.json': (
        '这个 cookies 文件是 JSON 格式，yt-dlp 需要 Netscape 格式的 cookies.txt，请重新导出。',
        'This cookies file is in JSON format; yt-dlp needs a Netscape-format cookies.txt. Export it again.'),
    'core.cookies.line_failed': (
        'ERROR: cookies 文件中有一行格式不对，无法读取（内容不记录）',
        'ERROR: a line of the cookies file has a wrong format and cannot be read (content not logged)'),
    'core.cookies.line_failed_item': (
        'cookies 文件中有一行格式不对，无法读取（内容不记录）',
        'A line of the cookies file has a wrong format and cannot be read (content not logged)'),
    'core.cookies.line_skipped': (
        'WARNING: cookies 文件中有一行格式不对，已跳过（内容不记录）',
        'WARNING: a line of the cookies file has a wrong format and was skipped (content not logged)'),
    'core.cookies.missing': (
        '找不到 cookies 文件：{file}\n请在“设置 → 网络”中重新选择。',
        'Cookies file not found: {file}\nChoose it again in Settings → Network.'),
    'core.cookies.not_netscape': (
        '这个文件不是 Netscape 格式的 cookies.txt（第一行应为 “# Netscape HTTP Cookie File”），请重新导出。',
        'This file is not a Netscape-format cookies.txt (its first line should be “# Netscape HTTP Cookie File”). Export it again.'),
    'core.cookies.too_large': (
        'cookies 文件超过 10 MB，请确认选择的是导出的 cookies.txt。',
        'The cookies file is larger than 10 MB. Make sure you chose the exported cookies.txt.'),
    'core.engine.archive_info_failed': (
        '归档信息保存失败（媒体已保留）：{error}',
        'Archive details could not be saved (the media is kept): {error}'),
    'core.engine.bot_check_hint': (
        'YouTube 暂时拒绝了当前网络出口（Sign in to confirm you’re not a bot）。这通常在 30 分钟左右自行解除，链接已保留在“未完成”列表，稍后重试即可；也可以在“设置 → 网络”选择已登录 YouTube 的 cookies.txt 后重试。',
        'YouTube is temporarily refusing your current network (Sign in to confirm you’re not a bot). This usually clears in about 30 minutes. The links are kept in the Unfinished list, so try again later; you can also choose a cookies.txt from a signed-in YouTube account in Settings → Network and retry.'),
    'core.engine.bot_check_stale': (
        'YouTube 仍要求验证：所选 cookies 可能已过期或导出时未登录。可以稍后重试，或重新导出 cookies.txt。',
        'YouTube still asks for a check: the selected cookies may have expired or were exported while signed out. Try again later, or export cookies.txt again.'),
    'core.engine.cannot_start_engine': (
        '无法启动下载引擎：{error}。可在“设置”恢复内置引擎。',
        'Cannot start the download engine: {error}. You can restore the built-in engine in Settings.'),
    'core.engine.cannot_start_link': ('此链接无法开始：{error}', 'This link could not start: {error}'),
    'core.engine.duplicate_merged': ('重复链接已合并：{url}', 'Duplicate link merged: {url}'),
    'core.engine.error': ('错误：{error}', 'Error: {error}'),
    'core.engine.incompatible': (
        '当前 yt-dlp 引擎与本程序不兼容，请在“设置”恢复内置引擎。',
        'The current yt-dlp engine does not work with this program. Restore the built-in engine in Settings.'),
    'core.engine.item_unfinished': (
        '此链接未完成（退出码 {code}），继续处理队列中的下一项。',
        'This link did not finish (exit code {code}); continuing with the next item in the queue.'),
    'core.engine.kept_existing': (
        '保存位置已有这个文件（之前下载的），保留原文件名：{name}',
        'The save folder already has this file (downloaded earlier); its name is kept: {name}'),
    'core.engine.no_collect_history': (
        '找不到原采集历史，本次归档保留来源链接，未附网页正文。',
        'The original collection history was not found; this archive keeps the source link without the page text.'),
    'core.engine.other_format_source': (
        '保存位置已有这个链接的另一种格式（{name}，之前下载的）；本条用它转换成所选格式，原文件保留不动。',
        'The save folder already has this link in another format ({name}, downloaded earlier); it is converted to the chosen format and the original file stays untouched.'),
    'core.engine.picture_exists': (
        '保存位置已有与本条同名的图片（{name}），本条不嵌入封面，图片保留不动。',
        "The save folder already has an image with this item's name ({name}); no cover is embedded and the image stays untouched."),
    'core.engine.present_skipped': (
        '保存位置已有这一条（{name}，之前下载的），已跳过，原文件保留不动。',
        'The save folder already has this item ({name}, downloaded earlier); it is skipped and the original file stays untouched.'),
    'core.engine.present_untagged': (
        '保存位置已有本条的 {count} 个文件；为了不改动它们，本次新下载的文件不写入标签、封面和字幕。',
        'The save folder already has files of this item ({count}); to leave them unchanged, the new files get no tags, cover or subtitles.'),
    'core.engine.rate_limited_out': (
        '此链接多次触发 HTTP 429，已记入失败清单。',
        'This link hit HTTP 429 several times; it is kept in the Unfinished list.'),
    'core.engine.record_unreadable': (
        '下载记录无法读取，照常下载：{error}',
        'The download record cannot be read; downloading anyway: {error}'),
    'core.engine.resume_partial': (
        '保存位置有这个链接之前没下载完的部分文件，本条直接在保存位置继续下载。',
        'The save folder has an unfinished partial file of this link; downloading continues there.'),
    'core.engine.subtitles_dropped': (
        '字幕下载失败，已改为不带字幕重新下载此条。',
        'Subtitle download failed; downloading this item again without subtitles.'),
    'core.engine.unfinished_not_saved': ('失败清单未能保存：{error}', 'The Unfinished list could not be saved: {error}'),
    'core.engine.waiting': (
        '{reason}，等待 {seconds} 秒；可随时点击停止。',
        '{reason}: waiting {seconds} s; you can stop at any time.'),
    'core.formats.bad_id': ('Unsupported output format id: {id}', 'Unsupported output format id: {id}'),
    'core.formats.bad_mode': ('Unsupported output format: {mode}', 'Unsupported output format: {mode}'),
    'core.library.bad_interval': ('不支持的订阅检查间隔。', 'Unsupported subscription check interval.'),
    'core.library.cannot_set_aside': ('无法保留旧的历史记录文件：{path}', 'Could not set aside the old history file: {path}'),
    'core.library.kept_aside': (
        '历史记录（library.sqlite3）{problem}，已原样保留为 {target}（没有删除），本次从新的历史记录开始。',
        'The history (library.sqlite3) {problem}; it was kept unchanged as {target} (not deleted), and a new history starts.'),
    'core.library.no_job': ('找不到任务记录。', 'Task record not found.'),
    'core.library.not_v2': ('不是本版本的格式', "is not in this version's format"),
    'core.library.temporary': ('历史记录本次不保存：{error}', 'History is not saved in this session: {error}'),
    'core.links.bad_line': (
        '第 {line} 行不是有效的 HTTP / HTTPS 链接。请每行填写一个链接。',
        'Line {line} is not a valid HTTP / HTTPS link. Put one link per line.'),
    'core.links.encoding': (
        '无法读取文本编码，请将链接文件另存为 UTF-8。',
        'Could not read the text encoding. Save the link file as UTF-8.'),
    'core.links.file_too_large': (
        '链接文件超过 5 MB，请分成多个较小的 TXT 文件导入。',
        'The link file is larger than 5 MB. Split it into smaller TXT files.'),
    'core.links.no_link_on_line': (
        '第 {line} 行没有找到链接。请每行粘贴一个链接。',
        'No link found on line {line}. Paste one link per line.'),
    'core.links.none': ('请先粘贴至少一个视频或音频链接。', 'Paste at least one video or audio link first.'),
    'core.links.too_long': (
        '链接过长（{length} 个字符），无法交给下载引擎：{url}',
        'Link too long for the download engine ({length} characters): {url}'),
    'core.options.bad_resolution': ('Unsupported resolution: {resolution}', 'Unsupported resolution: {resolution}'),
    'core.options.no_output': ('Options data has no usable output folder', 'Options data has no usable output folder'),
    'core.options.not_object': ('Options data must be an object', 'Options data must be an object'),
    'core.options.unsupported': ('Unsupported output option', 'Unsupported output option'),
    'core.proxy.scheme': (
        '代理地址应以 http://、https:// 或 socks5:// 开头。',
        'The proxy address must start with http://, https:// or socks5://.'),
    'core.proxy_mask': ('[代理]', '[proxy]'),
    'core.reason.bot_check': (
        'YouTube 验证失败，稍后重试即可，也可在设置中选择 cookies',
        'YouTube check failed; try again later, or choose cookies in Settings'),
    'core.reason.engine_error': ('下载引擎无法运行', 'The download engine cannot run'),
    'core.reason.exit_code': ('下载未完成（退出码 {code}）', 'Download not finished (exit code {code})'),
    'core.reason.format': ('没有可用的所选格式或画质', 'The chosen format or quality is not available'),
    'core.reason.http_403': ('网站拒绝访问（403）', 'The site refused access (403)'),
    'core.reason.http_404': ('链接不存在（404）', 'Link not found (404)'),
    'core.reason.http_410': ('链接已失效（410）', 'Link has expired (410)'),
    'core.reason.network': ('连不上网站，请检查网络或 VPN', "Can't reach the site; check your network or VPN"),
    'core.reason.page_403': ('页面不存在或无权访问（403）', 'Page not found or access denied (403)'),
    'core.reason.page_404': ('页面不存在或无权访问（404）', 'Page not found or access denied (404)'),
    'core.reason.page_410': ('页面不存在或无权访问（410）', 'Page not found or access denied (410)'),
    'core.reason.region': ('该地区不可观看，可更换网络节点', 'Not available in this region; try another network location'),
    'core.reason.stale': ('解析失败，下载引擎可能已过期', 'Could not parse the page; the download engine may be outdated'),
    'core.reason.unavailable': ('视频不存在或已设为私享', 'Video unavailable or private'),
    'core.reason.unsupported': ('不支持这个链接', 'This link is not supported'),
    'core.rename.failed': ('文件未能改名（{reason}），已保留原文件名。', 'The file was not renamed ({reason}); it keeps its name.'),
    'core.rename.failed_named': (
        '文件未能改名（{reason}），已保留原文件名：{name}',
        'The file was not renamed ({reason}); it keeps its name: {name}'),
    'core.rename.locked': (
        '文件正被其他程序占用，未能改名，已保留原文件名：{name}',
        'The file is in use by another program and was not renamed; it keeps its name: {name}'),
    'core.rename.too_many': (
        '同名文件过多，未能改名，已保留原文件名：{name}',
        'Too many files with the same name; it was not renamed and keeps its name: {name}'),
    'core.unfinished.cannot_set_aside': (
        '无法保留读不出的未完成清单：{path}',
        'Could not set aside the unreadable Unfinished list: {path}'),
    'core.unfinished.kept_aside': (
        '未完成清单（failed-downloads.json）{problem}，已原样保留为 {target}（没有删除），本次结果写入新的清单。',
        "The Unfinished list (failed-downloads.json) {problem}; it was kept unchanged as {target} (not deleted), and this run's results go to a new list."),
    'core.unfinished.no_overflow_name': (
        '找不到可用的文件名保存未完成清单的较早部分：{path}',
        'No free file name to keep the older part of the Unfinished list: {path}'),
    'core.unfinished.overflow': (
        '未完成清单过长，已把最早的 {count} 条移到 {target}（没有删除）。',
        'The Unfinished list was too long; the oldest entries ({count}) were moved to {target} (not deleted).'),
    'core.unfinished.problem.damaged': ('内容已损坏', 'is damaged'),
    'core.unfinished.problem.dropped': ('有 {dropped} 条记录无法识别', 'has unreadable records ({dropped})'),
    'core.unfinished.problem.too_large': ('超过 5 MB（{size} MB）', 'is larger than 5 MB ({size} MB)'),
    'core.unfinished.problem.unknown_format': ('格式无法识别', 'has an unknown format'),
    'core.unfinished.problem.unreadable': ('无法读取', 'cannot be read'),
    'core.update.bad_hash': (
        '更新文件校验失败，已保留原来的引擎。',
        'The update file failed its checksum check; the previous engine is kept.'),
    'core.update.bad_host': (
        '更新文件不在官方发布地址，已保留现有引擎。',
        'The update file is not at the official release address; the current engine is kept.'),
    'core.update.bad_release': (
        '官方发布信息无法识别，已保留现有引擎。',
        'The official release information is not readable; the current engine is kept.'),
    'core.update.bad_size': (
        '官方发布的文件大小异常，已保留现有引擎。',
        'The official release file has an unexpected size; the current engine is kept.'),
    'core.update.builtin': ('已内置', 'built-in'),
    'core.update.checking': ('正在检查官方更新…', 'Checking for official updates…'),
    'core.update.downloading': ('正在下载新版引擎…', 'Downloading the new engine…'),
    'core.update.incompatible': (
        '新版引擎与本程序使用的参数不兼容，已保留现有引擎。',
        'The new engine does not accept the options this program uses; the current engine is kept.'),
    'core.update.no_exe': ('官方发布中没有找到 yt-dlp.exe。', 'The official release has no yt-dlp.exe.'),
    'core.update.no_github': (
        '无法连接 GitHub，请检查网络后重试。（{reason}）',
        'Cannot reach GitHub. Check your network and try again. ({reason})'),
    'core.update.no_sha': (
        '官方发布未提供 SHA256，已保留现有引擎。',
        'The official release has no SHA256; the current engine is kept.'),
    'core.update.record_failed': (
        '新版引擎的校验记录无法写入（{reason}），已改用内置引擎，请稍后重试更新。',
        "The new engine's checksum record could not be written ({reason}); the built-in engine is used. Try the update again later."),
    'core.update.restore_failed': (
        '无法删除已更新的引擎（文件可能正被其他程序使用或为只读），目前仍在使用已更新的引擎。请稍后再试。',
        'The updated engine cannot be deleted (another program may be using it, or it is read-only); the updated engine is still in use. Try again later.'),
    'core.update.save_failed': (
        '新版引擎无法保存到磁盘（{reason}），已保留现有引擎。',
        'The new engine could not be saved to disk ({reason}); the current engine is kept.'),
    'core.update.start_cannot': ('无法启动', 'it cannot be started'),
    'core.update.start_exit': ('退出码 {code}', 'exit code {code}'),
    'core.update.start_failed': (
        '新版引擎无法正常运行（{reason}），已保留现有引擎。',
        'The new engine does not run properly ({reason}); the current engine is kept.'),
    'core.update.start_no_version': ('没有给出版本号', 'no version number reported'),
    'core.update.start_timeout': ('{timeout} 秒内没有结束', 'no answer within {timeout} s'),
    'core.update.testing': ('正在检查新版引擎与本程序的兼容性…', 'Checking that the new engine works with this program…'),
    'core.update.timeout': ('连接超时', 'connection timed out'),
    'core.update.write_failed': (
        '新版引擎无法写入（{reason}），已保留现有引擎。',
        'The new engine could not be written ({reason}); the current engine is kept.'),
    'core.wait.pace': ('下载间隔', 'Download pause'),
    'core.wait.pace_or_retry': ('下载间隔 / 重试等待', 'Download pause / retry wait'),
    'core.wait.rate_queue': ('网站限流，暂停整个下载队列', 'The site is rate-limiting; pausing the whole queue'),
    'core.wait.rate_retry': (
        '网站限流，冷却后重试此链接（{attempt}/{limit}）',
        'The site is rate-limiting; retrying this link after a cool-down ({attempt}/{limit})'),
    'filetext.author_unknown': ('来源未提供', 'not provided by the source'),
    'filetext.page_text': ('{title}\n来源：{url}{note}\n\n{body}\n', '{title}\nSource: {url}{note}\n\n{body}\n'),
    'filetext.readme_head': (
        '{title}\n原链接：{url}\n采集来源：{source}\n作者：{author}\n\n',
        '{title}\nOriginal link: {url}\nCollected from: {source}\nAuthor: {author}\n\n'),
    'filetext.readme_media': ('\n\n媒体文件：\n', '\n\nMedia files:\n'),
    'filetext.readme_transcripts': ('\n\n关联转写稿：\n', '\n\nLinked transcripts:\n'),
    'filetext.truncated_source': (
        '\n说明：源正文超过长度上限，以下内容已截断。',
        '\nNote: the source text exceeded the length limit; the text below is cut off.'),
    'format.audio_best': ('原始音频 · 保留源编码', 'Original audio · source codec'),
    'format.audio_flac': ('FLAC · 24 位', 'FLAC · 24-bit'),
    'format.audio_m4a': ('M4A · AAC 原生', 'M4A · native AAC'),
    'format.audio_mp3': ('MP3 · 320 kbps', 'MP3 · 320 kbps'),
    'format.audio_wav': ('WAV · 32 位浮点', 'WAV · 32-bit float'),
    'format.video_mkv': ('MKV · 保留源画质', 'MKV · source quality'),
    'format.video_mp4': ('MP4 · H.264', 'MP4 · H.264'),
    'naming.clean': ('精简标题', 'Clean title'),
    'naming.title': ('标题', 'Title'),
    'naming.title_id': ('标题 [视频 ID]', 'Title [video ID]'),
    'value.job_title.multi': ('{title} 等 {count} 条', '{title} · {count} links'),
    'value.job_title.subscription': ('订阅：{name}', 'Subscription: {name}'),
    'value.page_title.fallback': ('网页', 'Web page'),
    'value.resolution.best': ('最佳', 'Best'),
    'value.resolution.heights': ('2160p / 4K, 1440p, 1080p, 720p, 480p', '2160p / 4K, 1440p, 1080p, 720p, 480p'),
    'value.subscription.interrupted': (
        '检查中断；下次重试，不更新已见记录', 'Check interrupted; retried next time, seen items not updated'),
    'value.subscription.stopped': ('采集已停止', 'Collection stopped'),
    'value.subscription.unfinished': ('检查未完成', 'Check not finished'),
    'value.subscription.untitled': ('未命名订阅', 'Untitled subscription'),
}

# 菜单的值 → 编号（i18n.label / choices 读合并后的 i18n.GROUPS）。只放编号；None 表示按原值显示。
GROUPS = {
    'resolution': {
        'best': 'value.resolution.best',
        '2160p / 4K': None,
        '1440p': None,
        '1080p': None,
        '720p': None,
        '480p': None,
    },
    'naming': {
        'title': 'naming.title',
        'clean': 'naming.clean',
        'title_id': 'naming.title_id',
    },
}

# 日志行的颜色标记（ui_log.classify 经 i18n.log_tag 读合并后的 i18n.TAGS）：本文件的编号 → 'error' / 'ok'。
TAGS = {
    'core.archive.cannot_set_aside': 'error',     # (2.0.0 S4) a file kept aside, or not: shown under Errors too
    'core.archive.kept_aside': 'error',
    'core.engine.cannot_start_engine': 'error',
    'core.engine.cannot_start_link': 'error',
    'core.engine.error': 'error',
    'core.engine.item_unfinished': 'error',
    'core.library.cannot_set_aside': 'error',
    'core.library.kept_aside': 'error',
    'core.unfinished.cannot_set_aside': 'error',
    'core.unfinished.kept_aside': 'error',
}
