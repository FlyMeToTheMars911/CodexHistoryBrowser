# 2026-10-29 桌面工具改造与历史读取报错排查

## 交付范围

增加 `CodexHistoryBrowser.exe` 独立窗口入口，默认查询本机 Codex 历史，保留原 Web 入口。桌面复用现有查询、详情、内容索引和 Markdown 展示，使用系统 WebView2；不额外捆绑 Chromium，不要求最终用户安装 Python。桌面缓存和日志持久保存在用户本地应用数据目录。

`desktop.py` 创建随机端口的 `127.0.0.1` 服务，关闭时停止它创建的服务及索引线程。Markdown 使用原生保存对话框；Web 下载方式保留。构建脚本使用隔离虚拟环境和固定版本依赖，生成单文件、无控制台 EXE。

## 排查结论与依据

本次重复错误由 **Codex 的 rollout 压缩格式迁移未适配** 触发，不是项目所用数据库表、字段不兼容，也不是正文丢失。

| 证据 | 结果 |
|---|---|
| `work/server.log` 第 1423–1862 行 | 2026-10-29 17:19:45 至 17:24:44，共 27 条 ERROR，全是同 3 个会话各重复 9 次的 `FileNotFoundError` |
| 对应 `threads.rollout_path` | 原 `.jsonl` 不存在，但原路径追加 `.zst` 后的压缩文件全部存在，文件头为标准 Zstandard 魔数 `28b52ffd` |
| 3 条会话的结构化历史 | `thread_items`、`thread_realtime_items` 均为 0 行，`thread_turns` 各 1 行；`history_mode=paginated`，`has_user_event=0`，只有投影位置与轮次元数据 |
| `state_5.sqlite` | 最新迁移为 62（`threads attached parent`，2026-10-29）；项目需要的 `threads` / `projects` 字段仍存在 |
| `thread_history_1.sqlite` | 最新迁移为 8（`thread turn root`，2026-10-10）；项目使用的正文表、投影表及字段仍存在 |
| 日志异常类别 | 没有 `no such table`、`no such column` |
| 进一步全量核对 | 303 个没有结构化正文及原 JSONL 的会话，303/303 均存在精确路径的 `.jsonl.zst`；其中 299 个子代理、1 个已归档用户会话及上述 3 个用户会话 |

数据库确实存在后续迁移，但所需表列保留。`history_mode`、`has_user_event` 和结构化正文行数不能单独证明正文是否存在；本机当前采用压缩 rollout 保留这部分正文。旧浏览器只识别 JSONL，且已有内容索引会跳过未变化的记录，因此当时日志只有 3 个会话报错，而全量重建会暴露更多未识别的压缩历史。

原代码调用链为 `HistoryStore.iter_items()` → `rollout_items()` → `sync_once()`：结构化正文为空时回退 JSONL，只检查数据库中记录的未压缩路径，没有尝试 `.zst`，因此抛出 `FileNotFoundError`，后台每约 30 秒打印同一堆栈。原始版本对应 `server.py` 第 148、169、237–245 行。修复后通过 `resolve_rollout_path()` 选择来源，`open_rollout()` 流式解压，复用原有逐行解析。

以上证据来自本机只读检查，未连接远程业务服务器或数据库。含真实路径、会话标识的详细 SQL 参数与原始结果仅保留在本地 `work/diagnosis.json`、`work/diagnosis_all_missing.json`，不提交公开仓库。

## 修复与兼容策略

- 优先读取原 JSONL，原文件不存在时读取同路径追加 `.zst` 的压缩文件；数据库路径本身以 `.zst` 结尾也可直接读取。
- Python 3.14+ 使用 `compression.zstd`，较早版本使用固定版本 `zstandard`；EXE 内置依赖。按 UTF-8 流式逐行解压，不生成解压临时文件，支持串联压缩帧。
- 增量索引指纹使用实际来源文件的路径、大小、修改时间，适配压缩迁移及仅压缩文件内容变化的场景。
- 缺少正文时保留元数据查询，显示正文不可用数及说明；详情返回可理解的 404，前端禁止空导出。
- 记录首次缺失警告；相同缺失状态不重复打印堆栈。后续同步继续检查，JSONL 或结构化正文恢复时自动重新索引。
- 清除失效的派生正文索引，避免搜索命中已经不可读取的旧正文。
- 指定根目录内自动识别最高数字版本的状态库和历史库，启动时核对必需表字段，不读取其他子目录下的遗留同名库。
- 状态库缺少必需字段时明确报错；结构化历史字段不兼容时回退 JSONL；投影字段不兼容时使用文件变化检测，并在页面提示。
- 单个原始文件不可读按会话记录错误，其他会话继续同步。无法访问整个状态库等错误仍明确报告。
- 所有 Codex 源数据库连接继续使用 `mode=ro` 和 `PRAGMA query_only=ON`；不修改表、数据、配置或原始会话文件。

这属于按实测内部结构进行的兼容读取，并非对所有未来格式的保证。官方文档说明了可配置的 [SQLite 状态目录](https://learn.chatgpt.com/docs/config-file/config-reference)，本工具通过 `--codex-home` 显式指定数据根目录，不自动解析其配置。

## 构建与验证

```powershell
.\build-desktop.ps1
python -m py_compile server.py
python -m py_compile desktop.py
python -m unittest discover -s tests -v
```

没有使用 Maven，已完成以下验证：

- Python 单文件语法检查通过；`start.ps1`、`build-desktop.ps1` 的 PowerShell 语法检查通过。
- 23 项临时数据库/桌面回归用例均已通过，覆盖筛选、分页、全文索引、只读限制、压缩迁移、增量更新、原文件优先、依赖缺失、压缩损坏、真正缺失时的恢复，以及桌面生命周期/导出。
- 前端单文件 JS 语法及 Playwright 检查通过：缺失提示按纯文本显示、Web/桌面两种导出、390px 页面布局，页面脚本错误为 0。
- 真实本机 1077 条会话完成索引，`error=null`、`unavailable=0`；内容查询样本命中 588 条。此前报错的 3 条会话均能读取，实际详情 HTTP 返回 200。
- 本机一次增量索引约 3.14 秒；此次对压缩历史补建索引约 186.84 秒，构建同时运行，耗时仅供参考。真实验证结果保留于本地 `work/compat-results.json`，不包含正文。
- 最终 EXE 为 14,839,301 字节（约 14.15 MiB）。实际启动打包后的 WebView2 窗口，用仅保留 `.jsonl.zst` 的合成夹具验证全文命中 1 条、详情 2 条、桌面桥接函数可用、页面脚本错误为 0；发送正常窗口关闭消息后退出码为 0，日志确认索引线程已退出。
- 原生保存/取消与来源校验已由桌面回归覆盖；未人工点选实际 EXE 的系统保存对话框。截图与结果位于本地忽略目录 `work/desktop-ui/`，使用的都是合成夹具。
- Web 的 `start.ps1 -NoBrowser` 实际启动通过，自动选中项目 `.venv-build`，压缩夹具正文查询命中 1 条、详情 2 条、索引无错误；`stop.ps1` 停止后测试端口及启动进程均已退出。

构建参考：[pywebview freezing](https://pywebview.flowrl.com/guide/freezing)、[pywebview API](https://pywebview.flowrl.com/api/)、[PyInstaller 使用说明](https://www.pyinstaller.org/en/stable/usage.html)。

## 本次改动审阅清单

| 类别 | 改动 |
|---|---|
| 数据表 | Codex 源表和结构无改动；沿用工具自己的 `documents` / `search_fts` 及触发器，更新派生内容和指纹，无新增业务表 |
| 业务参数 | 无业务系统参数改动；桌面新增 `--work-dir`，沿用 `--codex-home` / `CODEX_HOME`，端口自动分配 |
| 接口定义 | `/api/status` 新增 `unavailable`、`warnings`、`schema`；`/api/thread` 缺正文返回 404；新增桌面桥接 `export_markdown(filename, text)` |
| 数据字典 | 无业务数据字典改动 |
| 查询模板 | 无业务查询模板改动；增加 SQLite 版本和字段探测 |
| DAO 层 | 无 Java DAO / DAOImpl 或 MyBatis 改动；调整 Python `HistoryStore` 来源探测和异常处理 |
| 脚本 | 新增 `build-desktop.cmd` / `build-desktop.ps1`、PyInstaller spec 与构建依赖；原 Web 启停脚本保留 |
| 运行依赖 | 新增 `requirements.txt` 的 `zstandard`；Web 启动优先探测本项目虚拟环境，并检查解压能力 |
| 前端 | 独立缺失提示、桌面原生导出、加载失败时禁用空导出 |
| HEP | 无改动 |
| 提交边界 | 仅源码、脚本、依赖清单、测试、文档；不提交实际聊天正文、源数据库、索引、日志、测试导出或本地构建目录 |
