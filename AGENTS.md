# ytdlp-desktop

Windows 上的 yt-dlp 图形界面（Python 3.11 + CustomTkinter，中英文界面）。进仓库先读这一页；模块、事件流和数据文件的细节见 `docs/ARCHITECTURE.md`。

## 跑起来

- 解释器只用仓库根目录的 `.venv\Scripts\python.exe -X utf8 -B`（`-B`：不在仓库里留 `__pycache__`；`-X utf8`：中文路径和输出）。没有 `.venv`：按 README“构建与测试”（Build and test）的第一段命令建。
- 从源码启动：`.venv\Scripts\python.exe -X utf8 -B verification\sandbox_app.py`（`--keep` 退出后保留沙箱，`--base <目录>` 指定沙箱）。
- **绝不直接运行 `src\app.py`**：它把仓库根当程序目录，建 `data\` 和 `下载文件\`，还读写真实的 `%LOCALAPPDATA%\YtDlpDesktop`。
- 截图：`.venv\Scripts\python.exe -X utf8 -B verification\snapshot_ui.py --language both --appearance both --state idle --out <目录>`（`--state` 还可以是 `active`、`bot`、`filled`；`--no-shots` 只做布局断言）。

## 代码在哪

| 页面（文件都在 `src\`） | 代码 | 文字 |
|---|---|---|
| 外壳、侧栏、`set_busy`、`poll` | `studio.py` | `i18n_shell.py` |
| 下载 / 采集 / 资料（历史、订阅、归档） | `ui_download.py` / `collection_ui.py` / `library_ui.py` | `i18n_download.py` / `i18n_collect.py` / `i18n_library.py` |
| 日志 / 设置 | `ui_log.py` / `ui_settings.py` | `i18n_log.py` / `i18n_settings.py` |
| 共用组件、颜色、字号；核心模块 | `ui_theme.py` | `i18n_common.py`；`i18n_core.py` |

核心：`engine.py`（下载队列 `Runner`、限流、YouTube 验证、`APP_VERSION`）、`unfinished.py`（未完成清单）、`library.py`（资料库）、`collector.py`（采集子进程）、`updater.py`（引擎更新）、`settings.py`、`paths.py`、`messages.py`。入口 `app.py`。
- 事件：后台 `emit(kind, value)` → `studio.poll` → `on_<kind>`；新事件还要加进 `studio.HANDLED_KINDS`。高风险区：`engine.py` 的 `Runner._run_once`、`Runner._cooled`、`Runner.download`。改了必须跑 `--all`。

## 检查

| 时机 | 命令（前面都是 `.venv\Scripts\python.exe -X utf8 -B`） | 约 |
|---|---|---|
| 改完代码 | `verification\run_tests.py --quick -j 4` | 20 秒 |
| 只跑几个文件 | `verification\run_tests.py -k urls,unfinished`（文件名含其中一段就跑） | — |
| 交付前 | `verification\run_tests.py -j 4` | 10 分钟 |
| 正式门禁（发布前，串行） | `verification\run_tests.py --all` | 20 分钟 |

- 每个测试文件在自己的沙箱进程里跑，界面语言是中文。`--all` 需要 `vendor\` 和 Chrome。运行期间不改 `verification\`、`src\*.py`、`app.spec`，同一时间只跑一个 `run_tests.py` 或 `build.py`。发布门禁只认串行、不带 `-k` 和 `--quick` 的报告。
- 新测试文件：快、不开窗口的，在前五行加 `# quick`；按时钟计时、抢前台窗口或截屏的，加 `# serial: <原因>`，并把文件名加进 `verification\test_sandbox.py` 里 `assertEqual(serial, [...])` 那张清单。

## 界面文字

- 文字只写在 `src\i18n_<页>.py` 的 `TEXTS` 里：`'编号': ('中文', 'English')`，编号按字母序。编号前缀决定文件（`verification\test_i18n_catalog.py` 的 `PREFIX_FILES`）。
- 改一条：`git grep -n "旧文字" -- src/i18n_*.py`，在那一行同时改中文和英文（`{占位符}` 两边一致）。然后跑 `--quick`，再跑 `-k test_golden_ui,test_i18n_ui,test_text_guard`。
- 加一条：在对应文件里按字母序加 `'页.区域.名字': ('中文', 'English')`；代码里写 `i18n.t('页.区域.名字', 名字=值)`，核心模块写 `messages.msg`。菜单的值 → 编号写在同一文件的 `GROUPS`；日志里要标红或标绿的新行，自己在同一文件的 `TAGS` 里写 `'error'` 或 `'ok'`（不写就是普通颜色，没有测试会提醒）。
- 换了调用处用的编号（`t('a')` 改成 `t('b')`）：目录检查测不出用错，跑那一页的页面测试（`-k test_page_download` 等）和 `-k test_golden_ui`。
- 编号没人用了就删掉那一条。几个人并行改不同页面、要停用公共文件（`i18n_core / common / shell`）的编号时，不删公共文件：在自己那一页的文件里写 `RETIRED = ('common.x',)`，由合并的人删编号和这一行。
- 编号说作用，不说措辞；只改文字时不改编号。测试里要用界面文字时按编号取（`_sandbox.zh_t` / `en_t` / `zh_head` 或 `i18n.t`），不抄文字；测机制的测试用自己注入的假编号。
- 改字演练（"改一条文字只动一处"的证据，在临时副本里改，不碰仓库）：`verification\i18n_drill.py --set 'download.chip.done=完成|Done!' -k test_golden -j 4`；不带 `--set` 改全部文字，后面接 `run_tests.py` 的参数。有测试因此失败，就是那个测试抄了文字：改成按编号取。

## 改界面

- 共用组件都在 `src\ui_theme.py`，直接用，不在页面里另做一套：菜单 `MenuItem` / `SEPARATOR` / `popup` / `menu_button` / `bind_menu`；`ask_text`（一行输入框）、`empty_state`、`notice_bar`（`NoticeBar`）、`path_button`、`textbox`、`segmented`、`tabs`（`Tabs`）、`table`、`section_label`、`show`（只在变化时显隐）、`AppButton.set_kind`。`ui_theme` 不导入任何应用模块（`test_theme` 钉住），它要显示的几个词在 `ui_theme.WORDS`，由 `studio` 填。
- 页头只放标题。每个视图只有一个实心按钮。每个动作都要有看得见的入口：右键菜单和"⋯"用同一个 `<区域>_menu_items`（`link_` / `row_` / `task_` / `collect_` / `history_` / `subscription_` / `pending_` / `archive_` / `log_menu_items`）。不可用的控件置灰，不弹"请先选择"。不加说明句，要说明的内容写进 `使用说明.txt`。改完用 `snapshot_ui.py` 截图，看中文、英文、浅色、深色四种组合。
- 跟着忙碌状态变化的控件：只看忙不忙的，放进 `self.controls`、`input_controls` 或 `update_buttons`；还要看选中项或内容的，由本视图在 `set_busy` 里调用的刷新函数计算。
- 增删、移动这类控件或菜单项时，同步改 `verification\test_busy_matrix.py`：清单 `CONTROLS` / `INPUT_CONTROLS` / `UPDATE_BUTTONS` / `HOOKED` / `ALWAYS`（不受忙碌控制的导航或只读控件进 `NAVIGATION`，写理由；不进任何清单的可操作控件会让 `test_no_operable_control_is_left_out_*` 失败），数量断言（`(23, 2, 1, 17, 16)` 和 `len(wanted) == 43`），`expected` / `expected_shown` / `expected_menus` 里的期望；`set_busy` 新加刷新函数时加进 `REFRESHES`。然后跑 `-k test_busy_matrix`。

## 守卫报错怎么修

报错写明 `<文件>:<行>`、事实和怎么改。常见的几种：
- `test_text_guard` / `test_i18n_catalog` 的 G1a：`src` 里有未登记的中文字面量。界面文字改成 `i18n.t`（核心模块用 `msg`）。只写进日志文件、磁盘文件名或存储标记、从不显示的，登记进 `verification\golden\text-allow\<模块>.json` 的 `literals`，写 `category` 和 `reason`。`test_text_guard` 还要求所有文本文件用 LF 换行，并且不含本机用户名、用户目录、git 邮箱或真实邮箱：路径改成相对仓库根或占位符（`C:\Users\<you>`），邮箱用 `example.org`。
- `test_i18n_catalog`：目录规则（排序、重复、占位符、英文里没有中文、每个编号都有人用、存储编号契约）。
- `isolation` / `sources`：运行期间改了 `verification\` 或 `src`，或者已安装的程序在运行、写了真实状态目录。关掉程序，重跑。
- golden 不一致：先确认行为确实应该变，再直接运行那个测试文件重录，例如 `$env:GOLDEN_UPDATE='1'; .venv\Scripts\python.exe -X utf8 -B verification\test_golden_ui.py; Remove-Item Env:GOLDEN_UPDATE`（不删的话，这个会话里之后直接运行的 golden 测试都会被重录）。在 `run_tests.py` 下重录会被拒绝。golden 里的界面文字记成编号，只改措辞不需要重录。`ui.json` 各页共用：并行改页面时只由合并的人在合并后重录。
- 门禁说报告 "produced with other tests"：测试、golden 或 vendor 清单在报告生成后改过。重跑串行 `--all`。

## 数据格式

- 只读写 v2：`library.sqlite3` 的 `PRAGMA user_version = 2`；`failed-downloads.json` 和归档里的 `资料信息.json` 带 `"version": 2`。`settings.json` 没有版本号，读取时逐项校验，不认识的值回到默认值。
- 不是 v2 或读不出的文件，改名留在原处，从空白开始，并记一行日志：`library.sqlite3`、`failed-downloads.json` 用 `paths.set_aside(path, 'bad')`（日志页）；`settings.json`、`资料信息.json` 用各自的 `_set_aside`（先用 `settings.bad.json` / `资料信息.bad.json`；设置只记进日志文件）。**不读、不转换旧版数据，不加转换代码。**
- 消息存成 `{"k": 编号, "p": {参数}}`；枚举存代码值（例如预设 id `video_mp4`），不存显示文字。
- `verification\golden\stored-keys.json` 里的编号已经存在用户数据里，是契约：只能新增，不能改名、删除或改占位符。表和列由 `test_messages_store` 钉住，要改先问所有者。基准文件放在 `verification\golden\`（`.gitignore` 忽略 `verification\*.json`）。

## 不要做

- 不运行 `build.py` 或 `install_release.py`，不 commit、不打 tag、不 push，除非所有者要求：这些操作会替换他正在用的程序，或改动公开仓库。
- 不碰已安装的程序目录、它旁边的 `data\` 和 `%LOCALAPPDATA%\YtDlpDesktop`：那里是所有者的真实数据。
- 被 git 跟踪的文件里不写本机用户名、用户目录路径或邮箱：仓库是公开的。路径写成相对仓库根的形式，或用占位符。
- 不为了让测试通过而放宽断言。`test_busy_matrix`、`test_poll_order`、`test_engine_blocks`、`test_identity_vectors` 只能加强：它们守着控件状态、进度合并、下载队列和链接识别。
- 不弄坏这些行为：下载选项、断点续传、跳过已下载过；稳健模式的间隔，HTTP 429 冷却 5 / 10 / 15 分钟；YouTube 验证时只暂缓 YouTube 链接，30 / 15 / 15 分钟后重试；停止时结束整棵进程树；未完成清单不丢链接；采集和订阅；引擎更新只取官方发布、核对 SHA-256；安装时逐文件核对、失败回滚；cookies 内容不保存、不进日志，代理只在本次运行有效，唯一的后台联网是 api.github.com；英文界面里不出现中文。

## 发布（只在所有者要求时）

1. 把 `src\engine.py` 的 `APP_VERSION` 改成新版本号；`vendor\` 不全时先运行 `.venv\Scripts\python.exe -X utf8 -B prepare_vendor.py`（联网下载，按 `vendor.lock.json` 核对 SHA-256）。`build.py` 本身不下载。
2. `.venv\Scripts\python.exe -X utf8 -B build.py`（约 40 分钟，依次是串行 `--all`、PyInstaller、exe 层测试、`verify_release.py`），产出 `build\package-<版本>\YT-DLP下载器` 和 `build\verify\release-<版本>.json`。
3. 关掉正在运行的程序，先运行 `.venv\Scripts\python.exe -X utf8 -B install_release.py --stage build\package-<版本>\YT-DLP下载器 --target "<程序目录>" --dry-run`，确认无误后去掉 `--dry-run` 再运行一次。安装后自检失败会自动回滚。
4. 隐私检查：`git grep -n -i -F $env:USERNAME`、`git grep -n -i -F $env:USERPROFILE`、`git grep -n -i -F (git config user.email)` 都没有结果；`git status` 里没有 `docs\*\handoff\`、cookies 文件或 `data\`。
5. `git commit`，然后 `git tag v<版本>`，最后 `git push origin main v<版本>`。
