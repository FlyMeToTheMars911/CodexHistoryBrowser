# Codex History Browser

一个轻量的本机 Codex 会话查询工具，支持 Windows EXE 独立窗口和 Web 两种入口。两种入口共用只读查询后端及原生 HTML、CSS、JavaScript 页面，无 npm、无外部 CDN。

## EXE 桌面工具（推荐）

双击打包后的 `dist/CodexHistoryBrowser.exe`，直接在独立窗口查询本机历史，无需启动命令行或另开浏览器，也无需安装 Python。默认读取 `CODEX_HOME`，未设置时读取当前用户的 `.codex`。关闭窗口会停止本窗口启动的本机服务。

桌面窗口复用系统 Microsoft Edge WebView2 Runtime，不捆绑 Chromium。需要 Windows 和可用的 WebView2 Runtime；缺少运行环境或数据库时会弹窗提示，并记录日志。Markdown 导出使用系统保存对话框。

日志和派生索引保存在 `%LOCALAPPDATA%\CodexHistoryBrowser`，其中日志为 `desktop.log`，不写入 EXE 所在目录或临时解包目录。首次使用会在后台建立内容索引，期间可以先查询标题和项目。不同数据目录使用独立索引；关闭后再次启动会复用索引。

可选参数示例：

```powershell
.\dist\CodexHistoryBrowser.exe --codex-home 'D:\backup\.codex'
.\dist\CodexHistoryBrowser.exe --work-dir 'D:\HistoryBrowserCache'
```

从源码构建请运行项目根目录的 `build-desktop.ps1`（或 `build-desktop.cmd`）。脚本在独立 `.venv-build` 中安装固定版本的桌面和打包依赖，然后生成单文件 EXE；首次构建需要联网下载依赖。构建产物、虚拟环境和缓存不提交到 Git。源码桌面入口为 `desktop.py`，依赖见 `requirements-desktop.txt`。

## Web 启动（保留）

Web 入口不要求安装桌面或打包依赖。读取新版本 Codex 的 `.jsonl.zst` 压缩历史时，Python 3.10–3.13 需安装 `requirements.txt` 中的 Zstandard 依赖；Python 3.14+ 可直接使用标准库 `compression.zstd`。

源码环境首次配置示例（使用 Python 3.10+）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Windows 双击项目根目录的 `start.cmd`。启动成功后自动打开：

```text
http://127.0.0.1:8765
```

保持启动窗口运行，按 `Ctrl+C` 停止服务；也可双击 `stop.cmd` 停止默认端口上的本项目服务（会先核对进程命令行，不停止其他程序）。自定义端口使用 `.\stop.ps1 -Port 8877`。启动脚本依次检查项目 `.venv` / `.venv-build`、Codex 自带 Python 和系统 Python 3，选择具有 SQLite FTS5 trigram 与 Zstandard 支持的 Python 3.10+。不会安装或升级系统组件。

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
- “显示工具与过程”同时控制详情和 Markdown 导出；未勾选时只保留用户消息和正式回复，过滤工具调用、工具输出、思考和过程说明。未记录阶段的旧版助手回复仍会保留。
- 查询条件、页码及打开的会话保存在浏览器地址中，可刷新恢复或收藏。
- 内容查询覆盖消息、代码、命令及工具输出，不限于最终回复。

此项目使用精简 Markdown 渲染，保留文本与代码，支持常见标题、列表、链接、表格、引用。复杂 Markdown 扩展、公式、Mermaid 不专门渲染。图片/音频等非文本条目保留原始引用信息，不自动加载外部资源。只浏览本地 Codex 任务，不下载 ChatGPT 云端会话。

## 数据来源与索引

默认根目录为环境变量 `CODEX_HOME`，未设置时为用户目录下的 `.codex`。可通过 `--codex-home` 显式覆盖。

| 数据 | 来源 | 访问方式 |
|---|---|---|
| 任务、标题、归档、项目 | `state_N.sqlite` 的 `threads` / `projects` | SQLite 只读连接 |
| 消息和工具条目 | `thread_history_N.sqlite` 的 `thread_items` | SQLite 只读连接 |
| 历史变化位置 | `thread_history_N.sqlite` 的 `thread_history_projection_state` | SQLite 只读连接 |
| 缺少结构化历史的任务 | `threads.rollout_path` 指向的 JSONL，或同路径追加 `.zst` | 原文件逐行读取；压缩文件流式解压，不落盘 |
| 内容查询索引 | Web 的 `work/search-*.sqlite`；EXE 的本地应用数据目录 | 本工具自行生成，可重建 |
| 服务日志 | Web 的 `work/server.log`；EXE 的 `desktop.log` | 标准 logging |

原始 Codex 数据库连接使用 `mode=ro` 和 `PRAGMA query_only=ON`。程序不修改会话、归档状态、Codex 配置、源数据库结构或 WAL。

首次启动后台建立内容索引。此时标题/项目等查询可立即使用，内容查询可能尚未覆盖全部记录，页面会显示索引进度。启动后每 30 秒检查任务时间及历史投影位置，只重新索引发生变化的会话。新消息可能有约 30 秒同步延迟；“刷新记录”重新查询最新元数据和当前内容索引。

3 个字符及以上的内容关键词使用 SQLite FTS5 trigram 索引；仅存储文档命中关系，不存储每个词的位置，再进行字面匹配校验以缩小索引。1–2 字符关键词在派生文本库中匹配，数据量大时建议使用更长关键词。查询只返回截取后的摘要，避免传输整段历史。索引磁盘占用取决于原始历史规模；索引位于当前项目而非 Codex 目录。源文件丢失等索引失败会显示提示并记入日志。

项目不以 `history.jsonl` 作为完整聊天正文，也不读取 `.codex/sqlite/` 下可能遗留的同名旧库。启动时仅扫描指定数据根目录，选择数字版本最大的 `state_N.sqlite` / `thread_history_N.sqlite`，并检查必需字段；状态库缺少必需字段时明确提示需要适配，不静默切换到旧库。新增字段不影响读取；结构化历史表缺少必需字段时回退 JSONL，投影表不可用时改用文件变化检测。数据库版本在启动时选择，Codex 运行中升级后需重启本工具。

已适配 Codex 的历史文件压缩迁移：数据库可能仍记录 `.jsonl` 路径，而磁盘仅保留 `.jsonl.zst`。原 JSONL 存在时优先读取，否则自动读取压缩文件；增量索引检测实际来源文件的大小和修改时间。EXE 内置解压依赖，源文件保持只读。

若会话在结构化历史中没有正文，且 JSONL 和压缩文件均不存在，保留标题和项目查询，并显示“本地正文不可用”。这类会话不再每轮打印异常堆栈，也不保留已失效的正文搜索索引；后续同步仍检查正文是否恢复。打开详情会收到明确的 404 提示。本工具无法从空记录恢复已缺失的正文。

数据格式已按 2026-10-29 本机实际结构复核，未来内部结构变化仍可能需要适配。若 Codex 使用自定义 SQLite 数据目录，可用 `--codex-home` 指定实际包含状态库的目录，本工具不解析 Codex 的 `config.toml`。

## 文件结构

```text
CodexHistoryBrowser/
  server.py              标准库后端、只读数据访问、增量搜索索引
  desktop.py             WebView2 独立窗口、本机服务生命周期、原生导出
  CodexHistoryBrowser.spec  单文件 EXE 打包定义
  build-desktop.*        隔离构建环境及 Windows 打包入口
  requirements-*.txt     仅桌面/构建使用的固定版本依赖
  start.cmd              Windows 双击启动入口
  start.ps1              Python 探测与启动参数
  stop.cmd / stop.ps1     停止本项目服务
  static/
    index.html           查询和详情页面
    style.css            响应式样式
    app.js               查询、分页、详情、Markdown 导出
  tests/
    test_server.py       使用临时数据库的功能测试
    test_desktop.py      桌面服务生命周期和导出测试
  docs/                  桌面改造、报错排查及改动审阅记录
  work/                  运行日志、派生索引、检查产物（忽略提交）
```

服务只监听 `127.0.0.1`，仅允许 localhost/127.0.0.1 Host。页面及请求不依赖互联网，历史中的 HTML 按文本处理。

## 接口

| GET 接口 | 用途 |
|---|---|
| `/api/status` | 索引进度、最近同步时间、错误提示、数据根目录、缺失正文数、兼容性提示及选中数据库 |
| `/api/projects` | 项目选项 |
| `/api/search` | 分页查询 |
| `/api/thread?id=...&after=-1&limit=60` | 按事件序号分批读取详情，返回 `has_more` / `next_cursor` |

`/api/search` 参数：`project`（选项返回的 key）、`title`、`content`、`time_field`（`updated_at` / `created_at`）、`start` / `end`（YYYY-MM-DD）、`archived`（空 / 0 / 1）、`include_agents`（0 / 1）、`page`、`page_size`。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时构造的数据库，不改本机 Codex 数据。最初 Web 验证见 `VALIDATION.md`；本次桌面改造及日志原因见 [2026-10-29 改造与排查记录](docs/20261029-desktop-and-history-compatibility.md)。

Markdown 导出筛选的改动及本机记录验证见 [2026-11-18 导出筛选验证](docs/20261118-markdown-export-filter.md)。

## 改动审阅清单

- 数据表：只在本项目派生索引中新增 `documents`、`search_fts` 及 FTS 内部表/同步触发器；原始 Codex 表无改动。
- 配置参数：Web 使用 `--codex-home`、`--port`、`--no-browser`；桌面使用 `--codex-home`、`--work-dir`，服务端口自动分配；共同使用已有 `CODEX_HOME` 作为默认来源。
- 接口定义：保留上述 4 个本地只读 GET 接口；状态新增 `unavailable`、`warnings`、`schema`，详情缺失正文返回 404；桌面增加原生保存接口 `export_markdown(filename, text)`。
- 数据字典：无源数据字典改动；前端对条目类型提供中文展示名。
- 查询模板、DAO 层：无业务查询模板及 Java DAO；后端使用标准库参数化 SQLite 查询。
- 脚本：保留 Web 启停脚本；桌面增加 `build-desktop.cmd`、`build-desktop.ps1` 及 PyInstaller spec，本项目没有历史版本脚本目录。
- HEP：无改动。
- Git：仅发布源码、测试、文档和启停脚本；`work/` 中的聊天索引、日志、截图和导出文件不纳入版本管理。
