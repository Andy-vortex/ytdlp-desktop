# 架构

YT-DLP 下载器 2.0.2：Windows 上的 yt-dlp 图形界面（CustomTkinter），界面有中文和英文。本文讲每样东西在哪个文件、怎么连起来。
怎么跑程序和测试、怎么改文字见 `AGENTS.md`；数据格式的细节见 `docs\DATA.md`；用户能看到的规则见 `使用说明.txt`。
代码注释里形如 `MESSAGES.md 6`、`I18N.md`、`GLOSSARY.md`、`WP3` 的引用指 1.x 的设计文档，它们在标签 `v1.8.1` 里，不在主干。

## 1. 进程与线程

```
界面线程（Tk 主循环，StudioApp）
  ├─ 每 25 ms：poll ── 取 events 队列 ── handle(kind, value) ── on_<kind>
  ├─ 每 1 s：watchdog（工作线程死了却没发结束事件 → 补发一个）
  └─ 一次只有一个活动（busy）：下载 / 采集 / 更新
       │ 启动时 run_token + 1，emit 闭包记住它；token 变了的迟到事件直接丢弃
       ├─ 下载线程   engine.Runner.download ── yt-dlp.exe 子进程（作业对象）
       ├─ 采集线程   collector.CollectionRunner.run ── 本程序 --collect-worker 子进程（作业对象，可带 Chrome）
       └─ 更新线程   updater.EngineUpdater.run
后台小线程：HistoryWriter（资料库被锁时按顺序补写）、paths.purge_stale（启动清理）、每日版本检查
```

| 部分 | 在哪 | 要点 |
|---|---|---|
| 事件 | `emit(kind, value)` → `queue.Queue` | 只有工作线程发事件；下载事件先交资料库记录（`library.download_event`），失败只记日志，事件照样进队列 |
| poll | `studio.StudioApp.poll` | 每次最多 200 个、5 ms 预算；连续的 `progress` 只处理最后一个；单个处理函数出错只记录，poll 不停 |
| 分派 | `studio.HANDLED_KINDS` + `on_<kind>` | 按名字动态分派，搜不到调用者的 `on_*` 也是活的。`collect_*` 交给 `handle_collection`。**新事件要同时加进 `HANDLED_KINDS` 和写 `on_<kind>`** |
| 忙碌 | `studio.StudioApp.set_busy` | 三组控件（`controls`、`input_controls`、`update_buttons`）按忙闲切换，其余由各页钩子计算 |
| 停止 | `Runner.cancel` / `CollectionRunner.cancel` | 结束作业对象 = 结束整棵进程树，`taskkill /T /F` 兜底。关窗时先停止，8 秒后强制关闭 |
| 子进程环境 | `procutil.process_environment` | 每次运行私有 TEMP（`<状态目录>\tmp\run-<pid>-<随机>`、`collect\run-*`），结束删除；`PATH` 以内置 `vendor\bin` 开头 |
| 采集通讯 | 临时 `request.json` + `request.events`（JSONL） | 打包后 stdio 是 None，所以用文件；子进程直接写资料库，界面读事件行 |
| 单实例 | `procutil.single_instance` | 互斥量 `Local\YtDlpDesktop.Instance`；第二次启动只把已有窗口调到前面 |

## 2. 模块地图

`src\` 平铺。依赖方向：界面 → 核心 → 基础。核心模块（`engine`、`unfinished`、`library`、`collector`、`updater`、`urls`、`formats`、`naming`）不用任何 `i18n` 属性，唯一例外是 `library` 写文件时的 `i18n.t('filetext.*')`；它们用 `messages.msg` 造消息（`test_source_rules` 的 S2 钉住）。

| 模块 | 职责 | 主要入口 |
|---|---|---|
| **入口** | | |
| `app.py` | 参数、单实例、日志、语言、主循环；`--self-test`、`--collect-worker` 分支不加载 Tk | `main()` |
| **基础**（只用标准库） | | |
| `paths.py` | 所有位置的唯一来源；原子写入；改名保留；启动清理 | `APP_DIR`、`STATE_DIR`、`catalog_path`、`atomic_write_text`、`set_aside`、`purge_stale` |
| `procutil.py` | 子进程环境、作业对象、单实例、前台窗口、任务栏闪烁 | `JobObject`、`terminate_tree`、`single_instance` |
| `applog.py` | 滚动日志 `download.log`、异常钩子 | `setup`、`get` |
| `settings.py` | `settings.json` 逐项校验读写；坏文件改名、被占用时不覆盖 | `load`、`save`、`SCHEDULES` |
| `urls.py` | 链接提取、清理、身份键 | `extract_urls`、`identity`、`link_key`、`classify` |
| `formats.py` | 7 种格式预设 | `PRESETS`、`MODES`、`by_id`、`format_args` |
| `naming.py` | 3 种文件命名与改名 | `NAMING`、`finalize` |
| **核心** | | |
| `engine.py` | 下载：选项、命令行、输出解析、队列 | 见下方"engine.py 内部" |
| `unfinished.py` | 未完成清单的读、合并、写 | `load_at_start`、`load_for_merge`、`merge`、`save` |
| `library.py` | SQLite 资料库（任务、页、订阅、条目、归档、下载记录）与归档文件夹 | `open_library`、`Library`、`Archive` |
| `collector.py` | 网页采集（Scrapling；动态模式用独立临时 Chrome） | `CollectionRunner`、`worker_main`、`collect` |
| `updater.py` | 引擎路径与版本、检查新版、安全更新、恢复内置 | `engine_path`、`check_latest`、`EngineUpdater`、`restore_bundled` |
| `messages.py` | 核心消息（编号 + 参数）、显示、存储编解码、采集 IPC | `msg`、`raw`、`show`、`error_text`、`to_json` / `from_json`、`wire` / `unwire` |
| **文字** | | |
| `i18n.py` | 选语言、取文字；合并各文件的 `TEXTS` / `GROUPS` / `TAGS` | `init`、`t`、`label`、`choices` |
| `i18n_<页>.py` ×8 | 手写的中英文字（第 5 节）：`core`、`common`、`shell`、`download`、`collect`、`library`、`log`、`settings` | `TEXTS`；需要时还有 `GROUPS`、`TAGS` |
| **界面** | | |
| `ui_theme.py` | 设计令牌（浅色 / 深色）、DPI、控件工厂、共用组件 | `menu_button` / `popup` / `bind_menu`、`ask_text`、`NoticeBar`、`path_button`、`textbox`、`empty_state`、`segmented`、`Tabs`、`on_appearance` |
| `progress.py` | 平滑进度条 | `SmoothProgressBar` |
| `studio.py` | 外壳：窗口、侧栏、`page()`、状态、poll、`set_busy`、关闭 | `StudioApp` |
| `ui_download.py` | 下载页（链接框、选项、任务列表、YouTube 暂缓重试） | `DownloadUI`：`start`、`launch`、`download_emit`、`on_*` |
| `collection_ui.py` | 采集页 | `CollectionUI`：`launch_collection`、`handle_collection` |
| `library_ui.py` | 资料页：历史、订阅、归档；订阅定时；`HistoryWriter` | `LibraryUI`：`history_selection`、`check_subscription`、`subscription_tick` |
| `ui_log.py` | 日志页 | `LogUI` |
| `ui_settings.py` | 设置页（引擎、下载、新订阅、网络、界面、关于）、语言重启 | `SettingsUI`：`show_settings(group)`、`refresh_settings_controls` |

`class StudioApp(DownloadUI, SettingsUI, LogUI, LibraryUI, CollectionUI, ctk.CTk)`：每页是一个混入类，共用一个窗口对象。改某一页先看 `verification\test_page_<页>.py`（`download`、`collect`、`library`、`log`、`settings`）；改外壳先看 `test_busy_matrix`、`test_poll_order`、`test_theme`。

区域动作的写法统一：右键菜单和"⋯"按钮用同一个 `<区域>_menu_items()` 生成，按钮叫 `<区域>_menu`（`link`、`row`、`task`、`collect`、`history`、`subscription`、`archive`、`log`）。订阅的新增条目表没有"⋯"：它的右键菜单 `pending_menu_items` 是旁边两个按钮的镜像。

**engine.py 内部**（按文件顺序）：常量（`APP_VERSION`、429 冷却、`PACE_SECONDS`、`BOT_CHECK_PATTERN`）→ `explain_error` → `Options` → cookies 检查 → `build_command`、`probe_args` → `parse_line` → `QueueItem` → `_EngineOutput`（只认带本次随机标记的输出行）→ `_SafeArchive` → 已有文件与封面的辅助函数 → `Runner`（`cancel`、`_wait`、`_intercept`、`_run_once`、`enqueue`、`_cooled`、`download`）→ `diagnostics`。

## 3. 一次下载：从点击到完成

1. **点击"开始下载"**：`ui_download.start` → `read_form`（链接、选项、来源页）→ `launch`。
2. **开始前检查**：每组选项用一条链接试 `build_command`（过长的链接换下一条；全部过长才弹"检查输入"）；建好保存目录。
3. **记录与界面**：`library.create_job` 经 `HistoryWriter` 写任务记录（库被锁时先排队，下载照常开始）；`populate_queue` 建任务行；`set_busy(True)`。
4. **启动**：`download_emit` 把 `run_token` 加一并做出 emit 闭包；`Runner(emit, library).download` 在下载线程上运行。
5. **每条链接**（`Runner.download`）：
   - YouTube 被机器人验证拦住过 → 本条暂缓（`skipped='bot_check'`）；
   - 开了"跳过已下载过的内容"且资料库有同格式记录、文件仍在 → 跳过；
   - 稳健模式：距上一条成功下载 10–20 秒随机间隔（可取消）；
   - `build_command` → `_cooled`：遇 HTTP 429 冷却后重试，冷却 5 / 10 分钟（不超过 15 分钟；`Retry-After` 更长就按它）；重试 2 次后第 3 次遇到 429，本条记为未完成，还有待下载的条目时队列再冷却一次，然后继续下一条；
   - `_run_once`：yt-dlp 子进程放进作业对象；`_EngineOutput` 只认带本次随机标记的输出行，网页文字伪造不了；`parse_line` 产生 `progress`、`media`、`file`、`title` 事件；cookies 内容不进日志；
   - 完成后按命名方式改名（`naming.finalize`），统一归档时写归档（`library.Archive`）；发 `item_done`。
   - 运行中可经 `Runner.enqueue` 追加链接（"加入队列"）。
6. **结束**（`download` 的 `finally`）：`unfinished.merge` 把失败、停止、未开始、暂缓的条目按（链接键, 格式）写进未完成清单，已完成的删掉；发 `done`。
7. **界面收尾**：`on_done` → `set_busy(False)`、更新任务头和侧栏状态；有被暂缓的 YouTube 链接时按 `BOT_RETRY_DELAYS`（30 / 15 / 15 分钟）自动重试。

## 4. 数据

| 文件 | 位置 | 格式 / 版本标记 | 写它的模块 |
|---|---|---|---|
| `settings.json` | `<状态目录>` | 平铺 JSON，无版本标记，逐项校验 | `settings` |
| `failed-downloads.json`（未完成清单） | `<状态目录>` | `"version": 2` | `unfinished` |
| `library.sqlite3`（资料库） | `<程序目录>\data\`（存在且可写时），否则 `<状态目录>` | `PRAGMA user_version = 2` | `library` |
| `资料信息.json` | 每个归档文件夹里 | `"version": 2` | `library.Archive` |
| `download.log` | `<状态目录>` | 滚动文本日志 | `applog` |
| `engines\yt-dlp.exe` + `yt-dlp.json` | `<状态目录>` | 更新过的引擎和它的校验记录；没有记录就不用 | `updater` |
| `restart-state.json` | `<状态目录>` | 语言重启时暂存未保存的输入，120 秒后作废 | `ui_settings` |
| `tmp\`、`work\`、`collect\`、`cache\` | `<状态目录>` | 带标记文件 `.ytdlp-desktop`，启动时按年龄清理 | `paths` |

- `<状态目录>` = `%LOCALAPPDATA%\YtDlpDesktop`，可用 `YTDLP_DESKTOP_STATE` 改；`<程序目录>` = exe 所在目录，可用 `YTDLP_DESKTOP_APP_DIR` 改。默认保存目录 `<程序目录>\下载文件`。
- **格式 v2 的要点**：
  - 消息存编号加参数 `{"k": 编号, "p": {...}}`，原文（标题、用户输入、yt-dlp 原文）存字符串。只在登记过的字段（"槽"，`library.SLOTS`）上解释。
  - `verification\golden\stored-keys.json` 里的编号已写进用户数据：只能改文字，不能改名、删除或改占位符。
  - 枚举存代码值：格式存预设 id（`video_mkv` 等），分辨率 `best` 等，链接种类 `media` / `video_page` / `web`；显示文字由 `i18n.label` 给出。
  - 所有写入都是临时文件 + `os.replace`；被别的程序占用时不覆盖。
- **不是 v2 的文件**（旧版本写的、以后版本写的或损坏的）：不读、不转换、不删除，改名留在原处，程序从空白开始，日志页写一行。有版本标记的文件按版本标记判定：
  - 资料库、未完成清单：`paths.set_aside(path, 'bad')` → `library.bad-<时间>.sqlite3`、`failed-downloads.bad-<时间>.json`；
  - `资料信息.json`：`library.Archive` 改名为 `资料信息.bad.json`（被占用时 `资料信息.bad-<时间>.json`），下次写这个条目时重建；
  - `settings.json` 没有版本标记，只有读不出来（不是 JSON 对象）时才改名为 `settings.bad.json`（被占用时 `settings.bad-<时间>.json`）；被别的程序锁住时不改名、不覆盖，本次用默认值。
  
  细节和已知情形见 `docs\DATA.md` 第 6 节。
- 不保存：cookies 内容（只存文件路径）、代理（只在本次运行有效，日志里屏蔽）。唯一的后台联网是每日向 `api.github.com` 查版本号。

## 5. 界面文字

- 每条文字只写在一个地方：`src\i18n_<页>.py` 的 `TEXTS`，`'编号': ('中文', 'English')`，编号按字母序。改文字和加文字的步骤见 `AGENTS.md` 的"界面文字"。
- 编号前缀决定文件，**最长前缀优先**（`verification\test_i18n_catalog.py` 的 `PREFIX_FILES`，检查 C2）：

| 编号前缀 | 文件 |
|---|---|
| `value.link_kind.` | `i18n_collect` |
| `dialog.filetype.` | `i18n_common` |
| `dialog.restart_`、`log.restart_` | `i18n_settings` |
| `core.` `value.` `format.` `naming.` `app.` `unit.` | `i18n_core` |
| `common.` `table.` `error.` | `i18n_common` |
| `nav.` `shell.` `window.` | `i18n_shell` |
| `download.` `collect.` `library.` `settings.` | 同名页面文件 |
| 其余 `log.` `dialog.` `filetext.` | 显示它的那一页；两页以上用到的放 `i18n_common`（不检查） |

- 同一文件里还有 `GROUPS`（菜单的代码值 → 编号）和 `TAGS`（日志行标红 / 标绿：编号 → `'error'` / `'ok'`）。只放编号，不放文字。
- 界面代码用 `i18n.t(编号, 名字=值)`、`i18n.label(组, 代码值)`。核心模块按第 2 节的规则用 `messages.msg(编号, ...)` 造消息，界面显示时经 `messages.show` / `error_text` 按当前语言渲染；`i18n.t` 的参数是消息（或只带一条消息的异常）时，也按本次调用的语言显示（`Message.text_in`），f-string、拼接、`configure(text=…)` 不经过 `t()`，仍要 `show`。
- 语言：环境变量 `YTDLP_DESKTOP_LANG`（`zh` / `en`）> 设置 `language` > Windows 显示语言或区域格式（有一个是中文就用中文）。进程内固定，切换要重启。

## 6. 测试

入口 `verification\run_tests.py`，命令见 `AGENTS.md` 的"检查"。每个 `test_*.py` 在自己的子进程和临时沙箱里运行（`LOCALAPPDATA`、程序目录、TEMP、单实例名都指向沙箱），测试一律在中文下运行。运行期间 `verification\` 有任何文件变化、或真实的 `%LOCALAPPDATA%\YtDlpDesktop` 有变化，整次运行判失败。

| 层级（文件头 `# tier:`） | 内容 | 开关 |
|---|---|---|
| default | 单元测试和界面测试 | 默认 |
| all | 本地夹具服务器、真实 yt-dlp / FFmpeg、Chrome | `--all` |
| exe | 打包后的程序 | `--only-tier exe --exe <exe>` |
| net | 真实网站，从不自动运行 | `--net` |

- 标记（文件前五行注释）：`# quick` = 不建窗口、不起子进程的快文件，`--quick` 只跑它们；`# serial: 原因` = 计时、抢前台或截屏，`-j N` 并行时放到最后串行跑。
- 报告写到 `build\verify\report.json`；不带 `-k` / `--quick` 的串行运行另写 `report-source.json` / `report-exe.json` 给发布门禁用。
- 基准文件放 `verification\golden\`（根目录的 `verification\*.json` 被 git 忽略）。

**守卫保护什么**

| 测试 | 保护 |
|---|---|
| `test_busy_matrix` | `set_busy` 后每个控件在每种活动下可不可用；各页钩子每次调用一次；每个 `*_menu_items` 的启用状态 |
| `test_poll_order` | poll 的事件顺序、`progress` 合并、出错不停 |
| `test_engine_blocks`、`test_golden_runonce`、`test_golden_events`、`test_golden_argv` | `_run_once` 读取循环、`_cooled` 的 429 冷却、`finally` 里的清单合并；事件序列；命令行参数矩阵 |
| `test_identity_vectors` | `urls.identity`、`link_key` 的固定结果（它们是用户数据里的键） |
| `test_i18n_catalog` | 目录规则：编号排序与不重复、所在文件、占位符两边一致、英文无中文、每个编号有使用者、`stored-keys.json` 只增不减；`messages.msg` 的编号必须写成字面量（要选编号就写 `a if 条件 else b`） |
| `test_source_rules` | 读源码的规则：`src` 里目录文件以外的中文字面量都登记在 `verification\golden\text-allow.json`（逐条写理由，不能等于目录中文，总数钉住）；核心模块不用 `i18n`；不写 `from i18n import`；所有文本文件是 LF；仓库文件里没有本机用户名、用户目录、git 邮箱、`<盘符>:\Users\<真名>` 路径，邮箱只用保留测试域名（example.org 等）。报错写出 `<文件>:<行>`、事实和怎么改 |
| `test_messages_ui`、`test_messages_ui_download`、`test_messages_store` | G4：每个消息编号经每条通道（事件、存储、对话框、日志）在英文下显示正确 |
| `test_golden_ui` | 按固定顺序送入每种事件和忙闲切换后，外壳和各页控件的状态与 `verification\golden\ui.json` 一致 |
| `test_i18n_ui`、`test_i18n_snapshot` | 英文界面除用户数据外没有中文 |
| `test_formats_naming` | `src` 里没有旧的中文存储值 |
| `test_messages_store` | 资料库的表和列 |
| `test_release_tooling` | 发布门禁和安装脚本的每条拒绝规则 |

## 7. 构建、发布门禁、安装与回滚

| 命令 | 做什么 |
|---|---|
| `prepare_vendor.py` | 按 `vendor.lock.json` 下载 yt-dlp、Deno、FFmpeg，逐个核对 SHA-256 后填好 `vendor\`（`vendor\` 不进仓库）；`--update` 只改锁文件；`--record-local` 离线记录现有文件 |
| `build.py [--skip-tests]` | ① 按 `vendor\manifest.json` 核对 vendor；② 串行 `run_tests.py --all`；③ PyInstaller `app.spec`（文件夹版）；④ 暂存到 `build\package-<版本>\YT-DLP下载器` 并写 `build.json`，随后尽力删除 `build\dist`、`build\work`（删不掉只提示）；⑤ 对暂存的 exe 跑 exe 层，再跑发布门禁。从不联网下载 |
| `verification\verify_release.py [--stage 目录]` | 门禁：文件布局与 vendor 哈希；两次 `--self-test`；打包 exe 的夹具下载；界面冒烟；两份测试报告属于这份源码、这些测试（`tests_sha256`）和这个 exe；暂存目录在运行后没变。通过后写 `build\verify\release-<版本>.json`（含逐文件清单） |
| `install_release.py --stage <暂存目录> --target <程序目录> [--dry-run]` | 程序在运行时拒绝；按 `release-<版本>.json` 逐文件核对暂存目录；复制并再核对；上一版移进 `_previous\`；改名切换（写 `install-receipt.json` 是提交点）；在安装位置自检、下载冒烟、界面冒烟；任何一步失败自动回滚。`data\`、`下载文件\` 不碰 |
| `install_release.py --target <程序目录> --rollback` | 按 `install-receipt.json` 把当前版本移到 `_rolledback-<版本>\`，恢复上一版。中途断电留下的 `install-journal.json` 在下次运行时先被撤销或补完 |

版本号只写在 `src\engine.py` 的 `APP_VERSION`，`build.py` 和门禁从那里读。构建与安装只在所有者要求时运行。
