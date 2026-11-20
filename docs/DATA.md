# 数据格式（v2）

2.0.0 只读写这里写的格式。1.8.1 及更早版本写的文件不读、不转换：判定为"不是 v2"后改名留在原处，程序从空白开始，日志页写一行（第 6 节）。
每个文件在哪、由哪个模块写，见 `ARCHITECTURE.md` 第 4 节。

## 1. 消息（全项目一种形状）

```
M := 原文字符串 | {"k": <编号>, "p": {<名>: P, ...}}      # "p" 为空时省略
P := 整数 | 浮点数 | 原文字符串 | M
     （只有 "k" == "common.clauses"）"p" == {"parts": [M, M, ...]}
```

- 只在已知的文字位置（"槽"）上解释：dict 是消息，字符串是原文，`''` 表示没有。槽以外的 dict 原样不动。
- 读（`messages.from_json`）永不抛异常：未知编号照样建消息、显示编号本身、参数原样写回；其他形状（包括参数不合上面 P 语法的 `{"k", "p"}`）原样返回、原样写回。
- 写（`messages.to_json`）遇到无法存储的参数不抛异常：整条退化为中文原文，记一次 WARNING。
- 可存储编号是持久契约：`verification/golden/stored-keys.json` 列出的编号（137 个）以后不得改名、删除或改占位符，只许增加（`test_i18n_catalog.test_stored_keys_are_a_contract`）。

## 2. 枚举值存代码

| 组 | 代码值 |
|---|---|
| 格式（`mode`） | 预设 id：`audio_best`、`audio_wav`、`audio_flac`、`audio_mp3`、`video_mkv`、`audio_m4a`、`video_mp4` |
| 分辨率（`resolution`） | `best`；其余 `2160p / 4K`、`1440p`、`1080p`、`720p`、`480p` 原样 |
| 链接种类（`kind`） | `media`、`video_page`、`web`（显示用的 `direct`、`dynamic_request` 只作标题编号） |

显示文字由 `i18n.label(<组>, <代码>)` 给出；格式键（如 `video_mkv@2160p / 4K`）不变。`test_formats_naming.StoredEnumTests` 保证 `src` 里没有旧中文存储值。

## 3. 资料库 `library.sqlite3`

- `PRAGMA user_version = 2`；表和列与 1.7.0 相同（`test_messages_store` 的 `SCHEMA_OF_1_7_0`）。新库在一个事务里建表并写版本。
- 槽（`library.SLOTS`）：

| 记录 | 槽（`*` = 列表各项 / dict 各值） |
|---|---|
| `jobs.data` | `errors.*.error`、`errors.*.reason`、`options.archive_contexts.*.title` |
| `subscriptions.data` | `last_error` |
| `pages.data` | `title`、`links.*.title` |
| `entries.data` | `title`、`page_title` |
| `archives.data` | `title`、`page_title` |

- `jobs.title` 只存原文（媒体标题、订阅名、采集任务的起始链接），没有就 `''`；显示规则是 `library.title_parts`。
- `jobs.data`：`options`（下载任务 `{urls, overrides?, variants?, <Options 字段>}`，`mode` 只在这里；采集任务是 `CollectOptions`，不加新键）、`subscription`、`via`（`'subscription'`，可缺）、`title_links`（≥ 2，可缺）、`checkpoint`、`errors`（`[{"error": M, "reason": M 或 null}]`，下载任务最多 20 条）、`done`（`[[url, format 或 null], ...]`，唯一的"完成"记录）、`files`、`current*`、`owner`、`worker`、`result`。没有 `msgs`、`mode_v2`、`completed`、`completed_variants`、`last_reason`、`titled`。
- 所有 JSON 用 `json.dumps(..., ensure_ascii=False)`（`Library.contexts` 的预筛选依赖它）。

## 4. 未完成清单 `failed-downloads.json`

```json
{"version": 2, "entries": [{"key": "…", "format": "…", "url": "…", "title": "<原文>",
  "reason": "failed|stopped|unstarted|blocked", "error": M, "time": 0.0, "referer": "<原文>",
  "options": {"output": "…", "mode": "<预设 id>", "…": "省略默认值"}}]}
```

紧凑 JSON、原子写入；槽是每条的 `error`。5 MiB 读取上限、4 MiB 写入上限与溢出文件 `failed-downloads.overflow-<时间>.json`、被占用时不覆盖、读不全先改名保全，都与 1.8.1 相同。

## 5. 归档 `资料信息.json` 与设置 `settings.json`

- `资料信息.json`：`{"version": 2, url, source, title: M, page_title?: M, created, files, transcripts, id?, uploader?, description?, webpage_url?, upload_date?, downloaded?}`，`indent=2`。`version` 只在文件里，`archives.data` 不带（资料库已有 `user_version`）。`阅读说明.txt`、`网页正文.*` 按当前语言写。
- `settings.json`：平铺对象，没有版本标记；`language`（`system` / `zh` / `en`）是正式键；`mode` / `resolution` 存代码值；不认识的键不读、不写回。读取按现有规则逐项校验，不合规的值回到默认值（1.8.1 的中文 `mode`、`最佳` 因此回到默认格式与 `best`，`_extra.language` 不读）。
- `settings.json` 里新订阅的默认值（2.0.0 S4，设置 → 新订阅）：`subscription_media_only`（布尔，默认 `true`，仅媒体链接）、`subscription_first_new`（布尔，默认 `true`，包含已有内容）、`subscription_interval`（秒，取值只能是 `settings.SCHEDULES` = `900` / `3600` / `86400` / `0`，默认 `3600`；`3600.0` 读作 `3600`，`true`、`7200`、`NaN` 和其他类型回到默认）。文件里没有这三个键时取默认值，不需要转换。订阅计划的取值只有 `settings.SCHEDULES` 一处来源，`library.py` 的检查也引用它。

## 6. 不是 v2 的文件：改名保留，从空白开始

判定只看版本标记。改名用 `paths.set_aside(path, 'bad')`：`<名>.bad-<时间><后缀>`，名字被占时加 `-2` … `-99`，从不覆盖；改名失败时不写原文件，本次用已有的退路。

| 文件 | 何时 | 判定 | 结果 |
|---|---|---|---|
| `library.sqlite3` | `open_library` 打开时 | 有表且 `user_version != 2`；或 SQLite 报 `NOTADB`（26）/ `CORRUPT`（11） | 改名为 `library.bad-<时间>.sqlite3`，在原位置建新的空库；资料页提示条与日志页一行 `core.library.kept_aside`。旁边有 `-wal`、文件被占用（包括另一个连接正在事务中）、改名失败：本次用临时库（`core.library.temporary`），原文件不动，下次启动再处理。`-journal` 不拦改名（见下面已知情形） |
| `failed-downloads.json` | 窗口启动时（`unfinished.load_at_start`） | `version` 不是 2、`entries` 不是列表、损坏、过大、读不了，且没有一条可用条目 | 改名为 `failed-downloads.bad-<时间>.json`，日志页一行 `core.unfinished.kept_aside`。有可用条目、只坏了几条的 v2 文件留给第一次合并处理（不丢链接）；被占用时启动时跳过，由合并时的现有路径处理 |
| `资料信息.json` | `Archive.prepare` 时（下载归档、资料页"归档采集页面"） | 不是对象、`version` 不是 2、`files` / `transcripts` 不是列表、读不出 | 改名为 `资料信息.bad.json`（被占时 `资料信息.bad-<时间>.json`），本条重新建立，日志一行 `core.archive.kept_aside`；媒体与转写稿不动。`media` / `attach` 遇到时报 `core.archive.not_v2`，不改名、不写回 |
| `settings.json` | 读取时 | 没有版本标记 | 不改名，逐项校验（第 5 节）；损坏时照旧改名为 `settings.bad.json` |

已知情形：

- 旧库旁边若有上次崩溃留下的热 journal，SQLite 第一次读取时会先回滚并写回原文件，之后才改名；"改名后的文件与原文件字节相同"只对没有 journal 的文件成立。
- SQLite 不认为是热的遗留 journal（例如头部全零）不含原文件的内容，不拦改名，留在原处，由新库下次写入时覆盖。
- 界面线程上遇到被别的程序锁住的旧库：本次会话资料页显示历史错误、不改名，下次启动按上表处理。
- `user_version` 是 3（以后版本写的）的库同样改名：2.0.0 不读不认识的格式。

## 7. 采集子进程（IPC）

每行 `json.dumps([kind, messages.wire(value)], ensure_ascii=False)`；`wire(Message)` = `{"$m": {"k": …, "p": {…}}}`，dict / list / tuple 递归；形状不对的标记在读取时抛 `ValueError`。
