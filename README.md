# Codex History Browser

一个轻量的本机 Codex 会话查询页面。Python 标准库提供 HTTP 服务与 SQLite 查询，前端使用原生 HTML、CSS、JavaScript，无 npm、无 pip 第三方依赖、无外部 CDN。

## 启动

Windows 双击项目根目录的 `start.cmd`。启动成功后自动打开：

```text
http://127.0.0.1:8765
```

保持启动窗口运行，按 `Ctrl+C` 停止服务；也可双击 `stop.cmd` 停止默认端口上的本项目服务（会先核对进程命令行，不停止其他程序）。自定义端口使用 `.\stop.ps1 -Port 8877`。启动脚本优先使用本机 Codex 已安装的 Python 运行时，然后尝试系统 Python 3；要求 Python 3.10+，SQLite 支持 FTS5 trigram。不会安装或升级系统组件。

也可从 PowerShell 启动：

```powershell
.\start.ps1
.\start.ps1 -Port 8877 -NoBrowser
.\start.ps1 -CodexHome 'D:\backup\.codex'
```

若已有 Python 环境：

```powershell
python server.py --port 8765 --no-browser
```

端口被占用时会明确报错，可指定其他端口；不终止占用端口的其他程序。

## 查询与详情

- 条件：所属项目、标题关键词、聊天内容关键词、创建/更新时间、日期范围、归档状态。
- 条件之间是 AND；标题及内容按连续关键词匹配，不区分英文大小写。引号、百分号等按字面文本处理。
- 起止日期包含当天，依据运行服务的电脑本地时区。
- 默认包含归档与未归档任务，默认隐藏子代理任务；可勾选显示。
- 分页支持 10 / 20 / 50 / 100 条；按所选时间字段倒序。
- 项目优先读取 Codex 的真实项目关联，旧记录缺少关联时按工作目录归组。
- 标题优先使用 Codex 的 `name`（显示名称），缺失时回退到 `title`；列表显示两行，详情保留完整标题。
- 详情展示用户消息、助手回复、代码、表格和工具过程。工具输出可展开，每条记录可查看原始 JSON。
- 长会话每次加载 60 条，支持继续加载或加载全部；导出 Markdown 会先取完全部条目。
- 查询条件、页码及打开的会话保存在浏览器地址中，可刷新恢复或收藏。
- 内容查询覆盖消息、代码、命令及工具输出，不限于最终回复。

此项目使用精简 Markdown 渲染，保留文本与代码，支持常见标题、列表、链接、表格、引用。复杂 Markdown 扩展、公式、Mermaid 不专门渲染。图片/音频等非文本条目保留原始引用信息，不自动加载外部资源。只浏览本地 Codex 任务，不下载 ChatGPT 云端会话。

## 数据来源与索引

默认根目录为环境变量 `CODEX_HOME`，未设置时为用户目录下的 `.codex`。可通过 `--codex-home` 显式覆盖。

| 数据 | 来源 | 访问方式 |
|---|---|---|
| 任务、标题、归档、项目 | `state_5.sqlite` 的 `threads` / `projects` | SQLite 只读连接 |
| 消息和工具条目 | `thread_history_1.sqlite` 的 `thread_items` | SQLite 只读连接 |
| 历史变化位置 | `thread_history_1.sqlite` 的 `thread_history_projection_state` | SQLite 只读连接 |
| 缺少结构化历史的任务 | `threads.rollout_path` 指向的 JSONL | 逐行读取 |
| 内容查询索引 | 本项目 `work/search-*.sqlite` | 本项目自行生成，可重建 |
| 服务日志 | 本项目 `work/server.log` | 标准 logging |

原始 Codex 数据库连接使用 `mode=ro` 和 `PRAGMA query_only=ON`。程序不修改会话、归档状态、Codex 配置、源数据库结构或 WAL。

首次启动后台建立内容索引。此时标题/项目等查询可立即使用，内容查询可能尚未覆盖全部记录，页面会显示索引进度。启动后每 30 秒检查任务时间及历史投影位置，只重新索引发生变化的会话。新消息可能有约 30 秒同步延迟；“刷新记录”重新查询最新元数据和当前内容索引。

3 个字符及以上的内容关键词使用 SQLite FTS5 trigram 索引；仅存储文档命中关系，不存储每个词的位置，再进行字面匹配校验以缩小索引。1–2 字符关键词在派生文本库中匹配，数据量大时建议使用更长关键词。查询只返回截取后的摘要，避免传输整段历史。索引磁盘占用取决于原始历史规模；索引位于当前项目而非 Codex 目录。源文件丢失等索引失败会显示提示并记入日志。

项目不以 `history.jsonl` 作为完整聊天正文，也不读取 `.codex/sqlite/state_5.sqlite` 这类可能遗留的同名旧库。数据格式依据 2026-09 本机实测；将来 Codex 调整数据库结构时可能需要适配。

## 文件结构

```text
CodexHistoryBrowser/
  server.py              标准库后端、只读数据访问、增量搜索索引
  start.cmd              Windows 双击启动入口
  start.ps1              Python 探测与启动参数
  stop.cmd / stop.ps1     停止本项目服务
  static/
    index.html           查询和详情页面
    style.css            响应式样式
    app.js               查询、分页、详情、Markdown 导出
  tests/
    test_server.py       使用临时数据库的功能测试
  work/                  运行日志、派生索引、检查产物（忽略提交）
```

服务只监听 `127.0.0.1`，仅允许 localhost/127.0.0.1 Host。页面及请求不依赖互联网，历史中的 HTML 按文本处理。

## 接口

| GET 接口 | 用途 |
|---|---|
| `/api/status` | 索引进度、最近同步时间、错误提示、数据根目录 |
| `/api/projects` | 项目选项 |
| `/api/search` | 分页查询 |
| `/api/thread?id=...&after=-1&limit=60` | 按事件序号分批读取详情，返回 `has_more` / `next_cursor` |

`/api/search` 参数：`project`（选项返回的 key）、`title`、`content`、`time_field`（`updated_at` / `created_at`）、`start` / `end`（YYYY-MM-DD）、`archived`（空 / 0 / 1）、`include_agents`（0 / 1）、`page`、`page_size`。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时构造的数据库，不改本机 Codex 数据。现场真实数据验证结果见 `VALIDATION.md`。

## 改动审阅清单

- 数据表：只在本项目派生索引中新增 `documents`、`search_fts` 及 FTS 内部表/同步触发器；原始 Codex 表无改动。
- 配置参数：新增 `--codex-home`、`--port`、`--no-browser`，使用已有 `CODEX_HOME` 作为默认来源。
- 接口定义：新增上述 4 个本地只读 GET 接口。
- 数据字典：无源数据字典改动；前端对条目类型提供中文展示名。
- 查询模板、DAO 层：无业务查询模板及 Java DAO；后端使用标准库参数化 SQLite 查询。
- 脚本：新增 `start.cmd`、`start.ps1`、`stop.cmd`、`stop.ps1`，本项目没有历史版本脚本目录。
- Git：仅发布源码、测试、文档和启停脚本；`work/` 中的聊天索引、日志、截图和导出文件不纳入版本管理。
