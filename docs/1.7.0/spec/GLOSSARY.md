# YT-DLP下载器 1.7.0 — English glossary (normative)

Every English text of the app (the `i18n_*.py` catalogs, the README, message boxes, log lines) uses
these terms and no synonyms. A change of a term is made here first and then in every catalog value
that uses it. The "Avoid" columns guide writers; they are not a test, because several avoided
words are correct terms in another row ("web address", "check interval", "Download anyway").
The test is the curated, case-sensitive list of section 9 ("Always wrong"), checked by
`verification/test_i18n_catalog.py` item 4 (I18N.md 11.2) and by `build_inventory.py`.
The Chinese texts are not changed by the language adaptation.

Conventions: UI names inside a sentence are in curly quotes, as the Chinese texts do (“Retry
unfinished”); menu paths use the arrow (Settings → Network); the five page names and the library
tab names are proper names in title case (Downloads, Web Collector, Library, Task Log, Settings,
History & Resume); everything else is in sentence case (only the first word and names are
capitalised), buttons included; “Choose” is for files and folders, “Select” for rows of a list;
no exclamation marks; counts are written plural-free as "Label: n", parts joined by " · "
(`Task finished · Succeeded: 3 · Unfinished: 1`); the compact task-header stats are the one
exception and read "n word" (`12 done · 1 active · 37 waiting`) (I18N.md 8.3).

## 1. App, pages and areas

| 中文 | English | Use | Avoid |
|---|---|---|---|
| YT-DLP 下载器 / YT-DLP下载器 | YT-DLP Downloader | window title, README; the exe and folder names keep the Chinese name | YT-DLP Desktop |
| 桌面下载器 | Desktop Downloader | sidebar caption under the brand | |
| 下载中心 | Downloads | sidebar, page title | Download Center, Download Hub |
| 网页采集 | Web Collector (page); collect, collection (verb, noun) | sidebar, page title; “collect links from web pages” | scrape, crawl, harvest |
| 资料管理 | Library | sidebar, page title | Materials, Data management |
| 任务日志 | Task Log | sidebar, page title, “see the Task Log” | Logs, Journal |
| 偏好设置 | Settings | sidebar, page title, “in Settings → Network” | Preferences, Options |
| 运行记录 | Activity | heading of the log card | |
| 任务历史与续采 | History & Resume | library tab | Task history and continue collection |
| 增量订阅 | Subscriptions | library tab; one is a subscription | Incremental subscription, Feed |
| 统一资料归档 | Archive (tab); the unified archive (feature) | library tab; “Archive downloads in one place” | Unified material archive |

## 2. Downloading

| 中文 | English | Use | Avoid |
|---|---|---|---|
| 链接 | link | everything the owner pastes or collects | URL (except in technical errors), address |
| 网页地址 / 起始网页 | web address / start page | collector input | URL |
| 开始下载 | Start download | primary button | Download, Go |
| 加入队列 | Add to queue | start button while a job runs | Enqueue |
| 队列 | queue | | |
| 任务 | task (one job of links); item (one link of it) | “Task finished”, “Retry this one” | job (only in code) |
| 未完成 | Unfinished | the kept list of links that did not finish, chip, status | Failed list, Incomplete |
| 未完成列表 / 失败清单 / 未完成清单 | the Unfinished list | always with a capital U, also mid-sentence (“Kept in the Unfinished list”) | failed list, unfinished list |
| 重试未完成 | Retry unfinished | button; with count “Retry unfinished (3)” | Retry failed |
| 清除未完成 | Clear unfinished | button and dialog title | Delete failed |
| 重试此条 | Retry this one | row menu | |
| 仍然下载 | Download anyway | row menu, quoted in hints | Force download |
| 已下载过 | Downloaded before | chip of a skipped link | Already downloaded |
| 已暂缓 | On hold | chip of a link held back by the YouTube check | Deferred, Postponed |
| 已移出 | Removed | chip after 清除未完成 | Cleared |
| 未开始 | Not started | chip | |
| 已停止 | Stopped | chip | |
| 等待中 / 解析中 / 下载中 / 处理中 / 转换中 | Waiting / Parsing / Downloading / Processing / Converting | chips | |
| 正在下载 {n} / {m} | Item {n} / {m} (sidebar status, 120 px); Downloading {n} / {m} (task title) | | |
| 完成 / 进行中 / 等待 / 未完成（任务头统计） | {n} done · {n} active · {n} waiting · {n} unfinished; {n} succeeded / {n} unfinished / {n} total | compact task-header stats only | |
| 停止 | Stop | button | Cancel (Cancel is 取消) |
| 取消 | Cancel | button | |
| 整个播放列表 | Whole playlist | checkbox, quoted in hints | Entire playlist, Full list |
| 合辑（RD…） | mix | “Auto-generated mixes (RD…)” | radio |
| 内嵌字幕 | Embed subtitles | checkbox | |
| 格式 / 画质 | format / quality | | codec, resolution (resolution only for the 1080p list) |
| 最佳 | Best | resolution choice | Max |
| 保存到 / 保存位置 | Save to / save folder | label / sentences | output directory, destination |
| 打开文件夹 / 在文件夹中显示 | Open folder / Show in folder | | Explorer |
| 稳健模式 | Steady mode | Settings → Batch downloads | Safe mode, Robust mode, Polite mode |
| 限流 | rate-limiting (HTTP 429) | “The site is rate-limiting” | throttling |
| 冷却 | cool-down | | |
| 下载间隔 | download pause | waiting reason | interval (interval is 检查间隔) |

## 3. YouTube check, cookies, network

| 中文 | English | Use | Avoid |
|---|---|---|---|
| YouTube 验证 | YouTube check | chip reason, notices; the full sentence names the engine message “Sign in to confirm you’re not a bot” | bot detection, captcha, verification |
| YouTube 暂时拒绝了当前网络 | YouTube is temporarily refusing your network | task title | blocked, banned |
| 等待 YouTube 验证 | Awaiting YouTube | sidebar status (short) | |
| cookies / cookies 文件 / cookies.txt | cookies / cookies file / cookies.txt | always lowercase “cookies”; “a Netscape-format cookies.txt” | Cookie, cookie jar |
| 登录凭证 | login | “The file works like a login” | credentials |
| 代理 / 代理地址 | proxy / proxy address | | |
| [代理] (mask in logs) | [proxy] | | |
| 网络节点 | network location | region reason | node, VPN server |

## 4. Engine and updates

| 中文 | English | Use | Avoid |
|---|---|---|---|
| 下载引擎 / 引擎 | download engine / engine | yt-dlp seen as the app's engine; name it “yt-dlp” where the program is meant | downloader core, backend |
| 内置引擎 / 恢复内置引擎 | built-in engine / Restore built-in engine | | bundled engine, default engine |
| 检查更新 | Check for updates | button, quoted in texts | Update |
| 官方稳定版 / 官方发布 | official stable release / official release | | upstream |
| 新版引擎 | the new engine | | |
| 引擎有新版 | new engine | sidebar badge `v1.7.0 · new engine` (must fit 136 px) | engine update available (too wide for the badge) |
| 已内置（引擎版本未知） | built-in | engine badge `yt-dlp built-in` | |
| 内置组件 / 内置版本 | built-in components / built-in version | | |
| 退出码 | exit code | | return code |

## 5. Web Collector

| 中文 | English | Use | Avoid |
|---|---|---|---|
| 采集 / 开始采集 / 停止采集 | collect / Start collecting / Stop collecting | | scrape |
| 普通网页 / 动态网页（Chrome） | Static page / Dynamic page (Chrome) | mode menu | Normal page |
| 媒体候选 / 全部链接 | Media links / All links | filter menu; “Track media links only” | media candidates |
| 发现的链接 | Links found | heading | |
| 媒体地址 / 媒体直链 / 视频页面 / 网页链接 / 动态页面媒体请求 | Media / Direct link / Video page / Web link / Page request | link kinds in the collect table's Type column (widest en label 77 px + 16 = 93 px column, I18N.md 10.1) | Media address, Direct media link, Dynamic page media request |
| 网页（无标题页面） | Web page | title of a collected page without a title | |
| 滚动次数 / 每站页数 | Scrolls / Pages per site | | |
| 内容范围（CSS） | content scope (CSS) | | selector (only “CSS selector” in errors) |
| 网页正文 | page text | “Save page text”, archive files | body, article text |
| 添加到下载列表 | Add to download list | button (collector and subscriptions) | Send to downloads |
| 续采 / 无法续采 | resume collecting / Cannot resume collecting | | continue collection |
| 采集断点 | collection checkpoint | | breakpoint |

## 6. Library

| 中文 | English | Use | Avoid |
|---|---|---|---|
| 历史记录 | history | “The history cannot be written” | records database |
| 继续未完成 | Resume unfinished | history button | Continue unfinished |
| 刷新记录 / 刷新 | Refresh list / Refresh | | Reload |
| 查看结果 | View results | | |
| 订阅 / 保存为订阅 / 立即检查 / 暂停 / 恢复 | subscription / Save as subscription / Check now / Pause / resume | | feed, watch |
| 新增 / 新增内容 | new / new items | | increments |
| 待处理 | Pending | column, counts | Todo |
| 基线 | baseline | “off: baseline only” | |
| 检查间隔 / 仅手动 / 每 15 分钟 / 每小时 / 每天 | interval / Manual only / Every 15 minutes / Hourly / Daily | | |
| 进行中 / 已完成 / 部分完成 / 已中断 / 已暂停 | Running / Completed / Partly done / Interrupted / Paused | history and subscription status | Partial |
| 资料 / 资料目录 | item / item folder | an archived entry; the archive tab's button 打开资料目录 is “Open folder” (it opens the selected item's folder; the row must fit 1000x740) | material, asset |
| 归档当前采集正文 | Archive page text | archive tab button | Archive collected page text (too wide) |
| 资料库（目录） | archive folder | the root folder of the unified archive | library folder, database |
| 资料库（被占用的 data\library.sqlite3） | the library file | the lock notices of the 1.7.0 fixes: “Another program is using the library file; retrying automatically”, “The library file is available again” (added by WP-L2, finding L0-V1) | database, the library (the page is the Library), archive folder |
| 归档 | archive (verb and noun) | | file away |
| 转写稿 / 关联转写稿 | transcript / Link transcript | | subtitle file |
| 来源 / 作者 | source / author | | uploader (only in technical texts) |

## 7. Settings

| 中文 | English | Use | Avoid |
|---|---|---|---|
| 文件与命名 / 文件命名 / 命名方式 | Files and naming / File naming / naming style | | |
| 标题（推荐） / 精简标题 / 标题 [视频 ID] | Title (recommended) / Clean title / Title [video ID] | naming choices | Short title |
| 嵌入封面图 / 写入标签 | Embed cover art / Write tags | | thumbnail |
| 跳过已下载过的内容 | Skip items downloaded before | | |
| 批量下载 | Batch downloads | card | |
| 提醒与界面 | Notifications and interface | card | |
| 完成时提醒 | Notify when done | | |
| 识别剪贴板中的链接 | Detect links in the clipboard | | clipboard monitoring |
| 外观 / 浅色 / 深色 / 跟随系统 | Appearance / Light / Dark / System | | Theme, Follow system |
| 语言 / Language / 跟随系统 / 简体中文 / English | Language / 语言 (row title) / System / 简体中文 / English | the language row (new in 1.7.0): the title is bilingual with the running language first (zh `语言 / Language`, en `Language / 语言`); the two language names are written in their own language in both catalogs | Chinese, Simplified Chinese |
| 立即重启 | Restart now | | Relaunch |
| 应用与内置组件 / 桌面应用 | App and built-in components / Desktop app | | |

## 8. Common words

| 中文 | English | Avoid |
|---|---|---|
| 就绪 / 准备就绪 | Ready | Idle |
| 选择 / 清除 / 清空 / 更改 / 添加 / 忽略 / 粘贴 / 导入 TXT | Choose / Clear / Clear / Change / Add / Ignore / Paste / Import TXT | Select (Select is 选中 in “Select all”, “Select an item”), Browse |
| 失败 / 无法… / 未能… | Failed / Cannot … / Could not … | Unable to |
| 知道了 | Got it | OK |
| 详情见任务日志 | See the Task Log for details | |
| 可随时停止 | you can stop at any time | |
| 条（量词） | not translated: counts use “Label: n” (I18N.md 8.3) | link(s), item(s) |

## 9. Always wrong (tested)

These phrases never appear in an English value; the check is case-sensitive and runs in
`build_inventory.py` and `test_i18n_catalog.py` item 4. The list is short on purpose: only phrases
with no correct use in this app. A new entry is added here and in both checks.

`Download Center`, `Download Hub`, `Failed list`, `failed list`, `Retry failed`, `Unable to`,
`Follow system`, `Short title`, `Enqueue`, `bot detection`, `captcha`, `Cookie jar`, `cookie jar`,
`Media address`, `unfinished list` (lowercase), `Continue unfinished`, `Partial`,
`Already downloaded`, `Deferred`, `Preferences`, `YT-DLP Desktop`, `Unified material archive`,
`Incremental subscription`.

Not on the list although they are in an "Avoid" column, because they are right elsewhere:
`address` (web address, proxy address), `interval` (check interval), `Update` (the yt-dlp update
texts), `Select` (Select all), `Download` (Download anyway, Download complete), `Cookie` (the quoted
first line `# Netscape HTTP Cookie File`).
