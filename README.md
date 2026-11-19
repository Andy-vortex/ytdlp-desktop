# YT-DLP Downloader / YT-DLP 下载器

[English](#english) · [中文](#中文)

## English

A Windows desktop app for [yt-dlp](https://github.com/yt-dlp/yt-dlp): paste links, pick a format, download. Interface in English and Simplified Chinese.

![Downloads](docs/screenshots/download-en.png)

### Why use it

- **Waits out YouTube's bot check.** When YouTube asks to confirm you are not a bot, only the YouTube links are put on hold and retried automatically after 30, 15 and 15 minutes; other sites keep downloading. Steady mode spaces items 10–20 s apart and pauses the queue on HTTP 429 (5, then 10, at most 15 minutes).
- **Nothing gets lost.** Unfinished links survive a restart and are retried with one click (F5), each with its own format and folder. An existing file is kept; the new one is saved as “name (2)”.
- **Collects links from web pages.** Static mode reads the page; dynamic mode uses a separate headless Chrome and can scroll. Up to 20 start pages, paging along same-site `rel=next` links. Chosen links go to the download list.
- **Library and subscriptions.** Task history with resume; subscriptions check their pages by hand, every 15 minutes, hourly or daily while the app is open, and list only new items. Since 1.8.1 a subscription can be renamed or deleted and pending items can be ignored. Optional archive: one folder per item with its record, page text and transcripts.
- **Safe engine updates.** A new yt-dlp comes only from its official GitHub release, is checked for size and SHA-256, and must start and accept the options this app passes before it replaces the current one; “Restore built-in engine” goes back.
- **Private.** No usage data is collected. The proxy address lives for the current session only and is shown as `[proxy]` in logs and errors. Of a cookies file only its path is saved; lines the engine echoes from it are kept out of the log. The only background request is a version check on `api.github.com`, at most once a day, and it can be turned off.
- **Ready to run.** yt-dlp, FFmpeg and Deno are bundled (versions and SHA-256 pinned in `vendor.lock.json`); no Python needed.
- **Two languages.** Follows the Windows language, or choose English / 简体中文 in Settings.

| Web Collector | Subscriptions |
|---|---|
| ![Web Collector](docs/screenshots/collect-en.png) | ![Subscriptions](docs/screenshots/library-en.png) |

### Use

There are no prebuilt downloads; build the program as below. The result is a folder: keep `YT-DLP下载器.exe` and `_internal` together. Windows 10/11, 64-bit. Dynamic collection needs Google Chrome.

| Data | Location |
|---|---|
| Settings, Unfinished list, log | `%LOCALAPPDATA%\YtDlpDesktop` |
| History, subscriptions, archive index | `data\library.sqlite3` next to the program |

The full manual is [使用说明.txt](使用说明.txt) (Chinese).

### Build and test

Python 3.11, from the repository root:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe prepare_vendor.py
.venv\Scripts\python.exe -B src\app.py
```

```powershell
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py --all
.venv\Scripts\python.exe -X utf8 -B build.py
.venv\Scripts\python.exe -X utf8 -B install_release.py --stage build\package-1.8.1\YT-DLP下载器 --target "<program folder>" --dry-run
```

- `prepare_vendor.py` downloads the pinned yt-dlp, FFmpeg and Deno into `vendor\` and stops on any SHA-256 mismatch.
- `run_tests.py` runs in a sandbox and leaves your settings and data alone; `--all` adds the real engine, a local fixture server and Chrome.
- `build.py` runs the tests, packages `build\package-1.8.1\YT-DLP下载器` and writes `build\verify\release-1.8.1.json` with every file's size and SHA-256.
- `install_release.py` checks every file against that record, keeps the previous version, self-tests the new one and restores the previous one if the test fails. Drop `--dry-run` to install.

| Path | Contents |
|---|---|
| `src\` | the app (`app.py` is the entry point) |
| `verification\` | tests and `run_tests.py` |
| `docs\` | design specs per version, screenshots |
| `架构说明.txt` | modules, events, data files, packaging (Chinese) |

### License

[MIT](LICENSE). yt-dlp, FFmpeg, Deno, customtkinter, Scrapling and the other components keep their own licenses; `prepare_vendor.py` collects them into `vendor\licenses`, and the build ships them with the program. Not affiliated with the yt-dlp project; download only what you have the right to.

---

## 中文

基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 的 Windows 桌面下载器：粘贴链接，选格式，下载。界面有简体中文和英文。

![下载中心](docs/screenshots/download.png)

### 优势

- **自动等过 YouTube 验证。** YouTube 要求“确认不是机器人”时，只暂缓 YouTube 链接，并在 30、15、15 分钟后自动重试；其他网站照常下载。稳健模式让条目间隔 10–20 秒，遇到 HTTP 429 整个队列冷却（5 分钟、10 分钟，最长 15 分钟）。
- **任务不丢。** 未完成的链接重启后还在，一键重试（F5），各自沿用自己的格式和目录。重名时保留已有文件，新文件存为“名称 (2)”。
- **网页采集。** 普通模式直接读取网页；动态模式用一个独立的无头 Chrome，可以滚动。最多 20 个起始网页，翻页只跟随同站的 `rel=next`。选中的链接加入下载列表。
- **资料管理与订阅。** 任务历史可续采；订阅可手动、每 15 分钟、每小时或每天检查（程序打开时），只列出新增。1.8.1 起订阅可以改名、删除，待处理条目可以忽略。可选统一归档：每个条目一个文件夹，含记录、网页正文和转写稿。
- **引擎更新稳妥。** 新版 yt-dlp 只从官方 GitHub 发布页获取，核对大小和 SHA-256，并且要能正常启动、认得本程序传给它的各项选项，才会替换当前版本；“恢复内置引擎”可退回。
- **隐私。** 不收集使用数据。代理地址只在本次运行有效，日志和错误里显示为“[代理]”。cookies 文件只保存路径，引擎回显的文件行不写入日志。唯一的后台联网是每天最多一次向 `api.github.com` 查询版本号，可以关闭。
- **开箱即用。** 内置 yt-dlp、FFmpeg、Deno（版本和 SHA-256 锁定在 `vendor.lock.json`），不需要安装 Python。
- **中英文界面。** 默认跟随 Windows，也可在设置中选择简体中文或 English。

| 网页采集 | 增量订阅 |
|---|---|
| ![网页采集](docs/screenshots/collect.png) | ![增量订阅](docs/screenshots/library.png) |

### 使用

没有预编译下载，请按下文构建。得到的是一个文件夹：`YT-DLP下载器.exe` 和 `_internal` 要放在一起。Windows 10/11 64 位。动态采集需要安装 Google Chrome。

| 数据 | 位置 |
|---|---|
| 设置、未完成清单、日志 | `%LOCALAPPDATA%\YtDlpDesktop` |
| 历史、订阅、归档索引 | 程序旁的 `data\library.sqlite3` |

完整说明见 [使用说明.txt](使用说明.txt)。

### 构建与测试

Python 3.11，在仓库根目录：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe prepare_vendor.py
.venv\Scripts\python.exe -B src\app.py
```

```powershell
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py
.venv\Scripts\python.exe -X utf8 -B verification\run_tests.py --all
.venv\Scripts\python.exe -X utf8 -B build.py
.venv\Scripts\python.exe -X utf8 -B install_release.py --stage build\package-1.8.1\YT-DLP下载器 --target "<程序目录>" --dry-run
```

- `prepare_vendor.py` 按锁定版本把 yt-dlp、FFmpeg、Deno 下载到 `vendor\`，SHA-256 不符即停止。
- `run_tests.py` 在沙箱中运行，不碰本机设置和数据；`--all` 另含真实引擎、本地测试服务器和 Chrome。
- `build.py` 先跑测试，再打包到 `build\package-1.8.1\YT-DLP下载器`，并写出记录每个文件大小和 SHA-256 的 `build\verify\release-1.8.1.json`。
- `install_release.py` 按这份记录逐个核对文件，保留旧版本，安装后自检，失败时恢复旧版本。去掉 `--dry-run` 即正式安装。

| 路径 | 内容 |
|---|---|
| `src\` | 程序（入口 `app.py`） |
| `verification\` | 测试和 `run_tests.py` |
| `docs\` | 各版本设计规范、截图 |
| `架构说明.txt` | 模块、事件流、数据文件、打包 |

### 许可证

[MIT](LICENSE)。yt-dlp、FFmpeg、Deno、customtkinter、Scrapling 等组件遵循各自的许可证；`prepare_vendor.py` 把它们收集到 `vendor\licenses`，打包时随程序分发。本项目与 yt-dlp 官方无关；请只下载你有权下载的内容。
