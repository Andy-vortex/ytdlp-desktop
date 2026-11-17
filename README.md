# YT-DLP 下载器（ytdlp-desktop）

一个基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的 Windows 桌面下载器：粘贴链接、选格式、点开始，剩下的交给它。另带网页采集和资料管理。界面有简体中文和英文两种。

*A Windows desktop front end for yt-dlp in Simplified Chinese and English: paste links, pick a format, download. Includes a Web Collector and a Library. The English guide is in the section [English](#english) below.*

> 本项目与 yt-dlp 官方项目无关。请只下载你有权下载的内容，并遵守各网站的使用条款和版权法律。

![下载中心](docs/screenshots/download.png)

## 功能

**下载**
- 直接粘贴分享文本也可以，程序会自动提取链接、合并重复链接；电台和播放列表参数默认忽略，勾选“整个播放列表”才下载整个列表。
- 七种格式：原始音频、WAV、FLAC、MP3、M4A、MKV、MP4，视频可选分辨率；自动嵌入标签和封面（WAV 除外）。
- 文件名默认不带视频 ID，可选三种命名方式；已有文件从不覆盖，重名时自动加 “ (2)”。
- 下载中可以继续添加链接；可停止、重试单条、一键重试全部未完成；未完成列表在重启后保留。
- 稳健模式：条目之间间隔 10–20 秒，遇到 HTTP 429 自动冷却。
- YouTube 要求验证时，其余 YouTube 链接自动暂缓，并按 30、15、15 分钟倒计时自动重试；其他网站的下载不受影响。
- 可选 cookies.txt（Netscape 格式）和代理。

**网页采集与资料管理**
- 从网页发现音视频链接（普通网页、需要滚动的动态网页、翻页），筛选后加入下载列表，也可以保存网页正文。
- 资料管理：任务历史与续采、增量订阅、统一资料归档。

**界面与其他**
- 简体中文和英文两种界面：默认跟随 Windows（显示语言或区域格式是中文时用中文，否则用英文），也可以在“偏好设置 → 提醒与界面 → 语言”中选择，重启后生效（“立即重启”会保留链接框里的链接）。文件夹和文件名（“下载文件”“资料归档”等）在两种语言下都保持中文。
- 浅色、深色、跟随系统三种外观；适配 200% 高分屏。
- 引擎更新：可在设置里更新 yt-dlp，更新前会做兼容性检查，随时可恢复内置版本。
- 任务日志页，可只看错误、复制、打开日志文件。

| 网页采集 | 资料管理 | 深色模式 |
|---|---|---|
| ![网页采集](docs/screenshots/collect.png) | ![资料管理](docs/screenshots/library.png) | ![深色模式](docs/screenshots/download-dark.png) |

## 隐私

- 不收集、不上传任何使用数据。
- 唯一的后台联网是每天最多一次向 `api.github.com` 查询 yt-dlp 的最新版本号（可在设置里关闭），从不自动安装。
- cookies 文件只记录它的路径，内容不会被复制、保存或写进日志。

## 从源码运行

需要 Windows 10/11（64 位）和 Python 3.11。

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe prepare_vendor.py      # 按 vendor.lock.json 下载 yt-dlp、FFmpeg、Deno 并校验 SHA-256
.venv\Scripts\python.exe -B src\app.py
```

动态网页采集需要本机安装 Google Chrome。

### 第三方程序（vendor\）

`vendor\` 不在仓库里，由 `prepare_vendor.py` 生成：

- `vendor.lock.json`（随仓库提交）固定了每个程序的仓库、发布标签、文件名、下载地址和 SHA-256：yt-dlp 2026.10.21、Deno v2.9.8、FFmpeg 月末构建 autobuild-2026-10-31-18-18（FFmpeg-Builds 长期保留每月最后一个构建，每日构建约两周后删除）。许可证文件取自同一个标签。
- `prepare_vendor.py` 默认只下载锁定的版本，逐个核对 SHA-256，不符即停止且不改动 `vendor\`。压缩包缓存在 `build\vendor-cache`，`vendor\` 里不会留下 `*.zip` / `*.part`。`vendor\bin` 整体替换，旧版 FFmpeg 的 DLL 不会残留。
- `prepare_vendor.py --update` 查询最新的 yt-dlp、Deno 和最新一个月末 FFmpeg 构建，只改写 `vendor.lock.json`，不改动 `vendor\`。检查改动后再运行 `prepare_vendor.py` 按新锁准备 `vendor\`，然后提交新锁。
- `prepare_vendor.py --record-local` 不联网：把 `vendor\` 里现有的 yt-dlp.exe 和 `vendor\bin` 各文件的 SHA-256 记入 `vendor\manifest.json`。用于旧版 `prepare_vendor.py` 准备的 `vendor\`（它的清单没有文件列表，`build.py` 会拒绝并提示这一步）。同一个 FFmpeg 库有两个大版本号（例如 `avcodec-62.dll` 和 `avcodec-64.dll`）时，它拒绝记录：多出的是旧版 FFmpeg 残留的 DLL，删掉后再运行。
- `vendor\manifest.json` 记录 `vendor\` 里实际有什么：每个程序的版本（FFmpeg 另有 `display_version`）和文件列表及 SHA-256。`build.py` 和发布检查按这份列表核对 `vendor\bin`，不写死 FFmpeg 的 DLL 版本号；与 `vendor.lock.json` 不一致时只提示。

## 测试与打包

```powershell
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py          # 默认测试
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py --all    # 含真实引擎和采集的完整测试
.venv\Scripts\python.exe -B build.py                                   # 测试 + 打包 + 发布检查
```

测试在隔离的临时环境里运行，不会碰本机的设置和数据。`build.py` 从不下载，先按 `vendor\manifest.json` 核对 `vendor\`；结果在 `build\package-1.7.0\YT-DLP下载器`（程序文件夹，`YT-DLP下载器.exe` 和 `_internal` 必须放在一起）。发布检查通过后写 `build\verify\release-1.7.0.json`，其中列出程序文件夹里每个文件的路径、大小和 SHA-256。

`install_release.py` 用来把打包好的程序安全地装到已有的程序目录：先按发布记录逐个核对暂存的文件（发布检查之后改过、多出或缺少任何文件都拒绝安装），复制后再核对一次，然后备份旧版本，安装后自检，失败自动回退，并支持 `--rollback` 手动回退。

## 文档

- [使用说明.txt](使用说明.txt)：完整的用户说明。
- [架构说明.txt](架构说明.txt)：模块、事件流、数据文件和打包流程。
- [docs/1.6.0/spec](docs/1.6.0/spec)：1.6.0 版的设计规范。
- [docs/1.7.0/spec](docs/1.7.0/spec)：中英文界面的设计（I18N.md）和英文用词表（GLOSSARY.md）。

## 许可证

本项目代码使用 [MIT 许可证](LICENSE)。yt-dlp、FFmpeg、Deno、customtkinter、Scrapling 等第三方组件各自遵循它们自己的许可证；`prepare_vendor.py` 会把第三方许可证文件一并下载到 `vendor\licenses`（Python 依赖的许可证从 `.venv` 中已安装的包复制到 `vendor\licenses\python-packages`），打包时随程序一起分发。

---

## English

YT-DLP Downloader is a Windows desktop front end for [yt-dlp](https://github.com/yt-dlp/yt-dlp): paste links, pick a format, press “Start download”. It also collects media links from web pages (Web Collector) and keeps a history, subscriptions and an archive of what you downloaded (Library). The interface is available in Simplified Chinese and English.

> This project is not affiliated with the yt-dlp project. Download only what you have the right to download, and follow each site's terms of use and copyright law.

![Downloads](docs/screenshots/download-en.png)

### Install, update and roll back

- The program is a folder: `YT-DLP下载器.exe` and the `_internal` folder next to it must stay together (the exe alone does not start). Windows 10/11, 64-bit; no Python needed.
- Double-click `YT-DLP下载器.exe`; starting it again while it runs brings the open window to the front. The window remembers its size and position.
- To install a new build over an existing program folder, use `install_release.py` (developer tool, Chinese messages): it checks every file against the release record, keeps the previous version, runs a self-test from the new place and rolls back by itself when that fails. `--rollback` undoes one installation at a time; the 1.5.1 program is kept as `YT-DLP下载器-1.5.1备份.exe` and shares the settings, the Unfinished list and the library with this version.
- The bundled yt-dlp can be updated in Settings → Download engine (“Check for updates”); the new engine is checked (size, SHA256, it must start and accept every option this program uses) before it replaces the current one, and “Restore built-in engine” goes back.

### First download

1. Paste links into the link box on the Downloads page (Ctrl+V, “Paste” or “Import TXT”). Shared text from apps works too: the links are found in it and duplicates are merged.
2. Choose a format. Seven are offered: original audio (source codec), M4A (native AAC, no re-encoding), MP3 320 kbps, FLAC 24-bit, WAV 32-bit float, MP4 (most compatible, H.264) and MKV (source quality). Quality and “Embed subtitles” apply to the two video formats.
3. Press “Start download” (Ctrl+Enter). While a task runs the button reads “Add to queue”: new links join the running task.
4. Playlist and radio parameters in YouTube links are ignored unless “Whole playlist” is checked; auto-generated mixes (RD…) are never downloaded whole.
5. Files are named after the title (three naming styles in Settings → Files and naming); an existing file is never overwritten, a new one gets “ (2)”. Tags, the source link and cover art are written by default (WAV has no cover).
6. With “Skip items downloaded before” on (the default), a link downloaded before in the same format whose file is still there is skipped and shown as “Downloaded before”; right-click it and choose “Download anyway” to download it again.

### Unfinished links and retries

- Every link that did not finish is kept in the Unfinished list: failed, stopped, not started and links put on hold by the YouTube check. The list survives a restart and is shown below the task.
- “Retry unfinished (n)” (F5) retries them all with their own format, quality and save folder; right-click a row for “Retry this one”, “Show reason” or “Remove from list”; “Clear unfinished” empties the list after a confirmation (downloaded files stay).
- Steady mode (Settings → Batch downloads, on by default) waits 10–20 seconds between links. When a site answers HTTP 429 (rate-limiting), the whole queue pauses for a cool-down (5, then 10, at most 15 minutes, or the time the site asks for).

### The YouTube check and cookies

- YouTube sometimes asks the network you use to confirm that you are not a bot (“Sign in to confirm you're not a bot”). The other YouTube links of the task are put on hold (kept in the Unfinished list) while links of other sites go on. This usually clears in about 30 minutes; without cookies the program retries by itself after 30, 15 and 15 minutes.
- To go on at once, choose a Netscape-format `cookies.txt` of a signed-in YouTube account in Settings → Network. The program stores only the path of that file, never its content, and never writes its lines to the log. The file works like a login: keep it to yourself.

### Web Collector

- Enter up to 20 start pages (one per line) and press “Start collecting”. “Static page” mode reads the page directly; “Dynamic page (Chrome)” uses a separate temporary Chrome (it must be installed) and can scroll the page. Pages per site follow `rel=next` links on the same site only.
- The table lists media links (or all links); select some and press “Add to download list”: they go into the link box of the Downloads page and are never downloaded by themselves. “Export links (TXT)” and “Save page text” save the selection or the collected page text.
- “Save as subscription” turns the current settings into a subscription (see Library).

### Library

- **History & Resume**: every download and collection task, newest first (300 at a time, “Load more” for older ones, a search field for titles and links). “View results” loads a task again; “Resume unfinished” downloads the links of a task that did not succeed, or collects the pages that are left.
- **Subscriptions**: a subscription checks its start pages by hand or every 15 minutes, hourly or daily while the program is open, and lists only what is new. New items stay pending until they are downloaded successfully.
- **Archive**: with “Archive downloads in one place” on, downloads go into an archive folder you choose, one folder per item with its record, a readme, the page text and linked transcripts.
- When another program holds the library file (`library.sqlite3`), the window does not wait: it says so, keeps what it shows and writes the records once the file is free again.

### Settings

Download engine; Files and naming (naming style, tags, cover art, skipping items downloaded before); Network (proxy address, cookies); Batch downloads (Steady mode); Notifications and interface (notify when done, links in the clipboard, Appearance: Light / Dark / System, and Language); App and built-in components. Changes are saved at once.

**Language / 语言**: “System” uses Chinese when the Windows display language or the regional format is Chinese, and English otherwise; “简体中文” and “English” fix the language. A change applies after a restart: “Restart now” restarts the program and keeps the links in the box, the start pages of the Web Collector and a subscription name being typed (it waits while a task runs). The variable `YTDLP_DESKTOP_LANG=zh|en` overrides the choice for one session. The 1.5.1 fallback does not know this setting; after it saved the settings the choice is “System” again.

### Where your data lives

- `%LOCALAPPDATA%\YtDlpDesktop`: `settings.json`, `failed-downloads.json` (the Unfinished list), `download.log`, an updated engine and temporary files.
- `data\library.sqlite3` next to the program: history, download records, subscriptions and the archive index.
- Back up these, the `data` folder and your download and archive folders. Folder and file names stay Chinese in both languages: the default save folder `下载文件`, the archive folder `资料归档`, and in each archived item `资料信息.json`, `阅读说明.txt`, `网页正文.txt/.md`, `媒体` and `转写稿`. Their content (readme, page-text header) is written in the language the program runs in.

### Privacy

No usage data is collected or sent. The only background network request is at most one check a day for the newest yt-dlp version on `api.github.com` (it can be turned off and never installs anything). Of a cookies file only its path is stored.

| Web Collector | Library | Dark mode |
|---|---|---|
| ![Web Collector](docs/screenshots/collect-en.png) | ![Library](docs/screenshots/library-en.png) | ![Dark mode](docs/screenshots/download-dark-en.png) |

### From source, tests and builds

See the Chinese sections above for the commands (`prepare_vendor.py`, `src\app.py`, `verification\run_tests.py`, `build.py`); the manual `使用说明.txt` is in Chinese. The design of the two languages is in [docs/1.7.0/spec](docs/1.7.0/spec).
