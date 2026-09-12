# 更新日志 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。所有重要改动都会记录在这。

## [v1.1.0] - 当前

**🕷️ 网页抓取能力 + 🧠 小脑成为必需项 + 🛠️ 安装器分级与体验修复。**

### Added
- 🕷️ **内置 Scrapling**（`plugins/scrapling_bridge.py` 桥接插件）：小焦从此**想抓啥抓啥**——网页 / 动态页 / 接口 JSON / 批量列表 / 登录态页面 / 下载任意文件。共 9 个工具，覆盖 Scrapling 全部 13 个 MCP 工具：
  - `get` / `bulk_get` / `fetch` / `bulk_fetch` / `stealthy_fetch` / `bulk_stealthy_fetch` / `scrape_with_selector`
  - `browser_session`：会话管理 + **登录态抓取** + **整页截图**（覆盖 open/close/list/session_fetch/session_make_request/screenshot）
  - 🆕 `download`：**下载任意文件**（PDF/EPUB/TXT/ZIP/图片/音视频…）到 `downloads/`（插件自研，Scrapling 无此能力）
  - 🆕 `save_to` 参数：抓取正文直接存文件到 `books/`（长文/连载章节不塞对话）
- 📖 **抓完自动解读**：`_explain_content()` 让大脑按「是什么 / 关键要点 / 怎么用」逐条讲；模型不可用时退回规则提纲。
- 🧠 **用户使用时学习**：`_learn_skill()` 每次用户使用工具后沉淀经验（成功=正确用法、失败=原因+反思）→ `self_learn/tool_skills.txt` + 向量库；`_recall_skills()` 下次检索命中即注入上下文复用（**越用越会**）。
- 🎯 **抓取意图直通**：`_detect_scrape_intent()` 用规则识别「抓/爬/下载 + 网址」→ 直接构造并执行工具调用（不指望 4B 模型自己选工具/避免它编造代码）。
- ⚙️ 依赖与配置：`requirements.txt` 增加 `scrapling[fetchers]` / `markdownify` / `mcp`；`xiaojiao_control.json` 增加 `scrapling` 配置段（mode/proxy_list/rate_limit/timeout/circuit_breaker…）。
- 📚 文档：新增 `docs/scrapling.md`（工具详解/原理/配置/测试清单/排错/合规边界）；README 增加 **🕷️ 内置 Scrapling · 想抓啥抓啥** 章节（含架构数据流图 + 学习闭环图，位于致谢之前）。
- 📚 文档：README 增加 **🛠️ 一键安装 · 检测分级** 章节（含三张图：**安装必需/可选分级图**、**小脑路径三级解析图**、**v1.1.0 改动全景图**＋原则落地对照表），同样位于**致谢之前**；`docs/architecture.md` 增加插件小节。

### Changed
- 🧠 **小脑改为必需项**（项目核心）：一键安装会检测小脑并明确提示；**路径不写死**（环境变量 → 配置 `brain.xiaojiao.model_path` → 项目目录探测 → **全盘自动探测**，模型与词表可跨目录配对、体积优先），支持换任意自训模型当小脑。
- 🛠️ **一键安装检测分级**：必需（Python 依赖 / 聊天大脑 llama.cpp / 大脑模型 / llama-swap 秒级切换 / 小脑）与可选（ComfyUI 视频大脑 / 视频节点 / Wan 视频模型 / Node.js / 猫娘 / 配音 / 封面 / 音乐）分开报告。
  - ComfyUI、视频节点、Wan 视频模型、Node.js 由「必需」降级为「可选」，不再阻塞启动。
  - Wan 视频模型 2.5GB 下载改为**先询问**（可选）。
- 🐱 **猫娘改为询问式**：启动时问「是否启动 N.E.K.O. 猫娘」(`[Y/n]`)，**不启动不影响小焦**；非交互环境默认不启动；`XIAOJIAO_NEKO_AUTO=1` 可免询问。
- 🖥️ **抓取结果展示优化**：正文直显（不再被小模型"总结"吃掉）；工具轨迹压成一行摘要（不再刷原始 JSON）；Setext 标题自动转 ATX。

### Fixed
- 🐞 **插件加载器**：`spec_from_file_location` 未注册 `sys.modules` → Python 3.13 下**任何使用 `@dataclass` 的插件都会静默加载失败**。已修复（加载失败时清理 `sys.modules`）。
- 🐞 **前端渲染**：`renderBlocks` 按空行分块后块间缺换行 → 段落粘连；`.mdh` 标题类无 CSS → 标题不显示样式；`inline()` 不识别 Markdown 链接。均已修复。
- 🐞 **插件参数适配**：`_adapt_args()` 对不支持 `timeout` 的工具（`open_session`/`close_session`/`list_sessions`）硬塞 timeout → 报错；改为按工具白名单过滤参数并区分超时单位（浏览器类=毫秒，HTTP 类=秒）。
- 🐞 **抓取内容**：`extraction_type="markdown"` 缺 `markdownify` 会整体失败 → 改为提前探测 + 优雅降级（html 提取 + 内置 HTML→Markdown）。
- 🐞 **下载被 WAF 拦（403）** → 下载改用标准浏览器头，失败自动退回 Scrapling 浏览器指纹下载。
- 🐞 **熔断误判**：安全拦截（SSRF/robots/参数缺失）曾被计入"工具失败"，连续拦截会导致工具被误熔断 → 安全拦截不再计入。
- 🐞 **批量工具参数错误**：批量内部曾把单个 `url` 传给 `bulk_*` 工具 → 改为逐 URL 调用单个工具（这样才能真正控制限速/退避/失败隔离）。
- 🐞 **视频任务残留报错**：前端对已结束的视频任务轮询报「任务状态丢失」并混进对话 → 改为静默清理 localStorage / 温和收尾。
- 🐞 **多进程占用 5000**：旧 python 进程未退出时与新进程同时监听，导致"重启后改动不生效"（旧进程抢答）→ 文档补充排错步骤。
- 🐞 **残留硬编码路径全部清除（"不准写死路径"）**：
  - 玩具体检 `/api/env`：ComfyUI / 视频模型 / llama-swap 改为 `_discover_paths()`（配置 → 环境变量 → 全盘自动探测），并**后台线程预热**（体检首屏 ~1 秒，不再卡盘）。
  - `video_service/config.py`：视频模型根目录改为从 `brain.comfy_dir` 逐级上溯找 `dit_fp8.safetensors` + 全盘探测（原来写死的旧目录已失效 → 现在无论装在哪都能找对）。
  - `podcast_service/podcast_gen.py`：配音模型目录改为项目目录/家目录/下载 + 盘符关键词探测。
  - `plugins/scrapling_bridge.py`：自备 Chrome 改为项目内 + 家目录 + 各盘关键词目录探测。
  - `start_xiaojiao.py`：猫娘（Steam 版按盘扫 `steamapps\common\n.e.k.o` + `discover_neko()` 兜底）、llama-swap 全部改为自动探测。
  - `.gitignore`：补 `books/`、`downloads/`、`test.db` 与个人本地插件，避免抓取产物/自用插件误入库。

---

## [v1.0.0] - 首次正式发布

**🚀 首次正式发布**：N.E.K.O. 猫娘桌面伙伴 + 多大脑秒切 + 全新封面 + 社区规范。

### Added
- 🐱 **N.E.K.O. 猫娘桌面伙伴**：一键启动自动拉起你下载的猫娘服务（main_server:48911 / memory_server:48912），并后台学习你与猫娘的对话。
- 🧠 **多大脑·秒级切换**：`brain_manager.py`（RUN/WARM/OFF）+ llama-swap(9292)。
- 🎨 **全新 README 封面**：用户猫娘壁纸（`assets/xiaojiao_cover.jpg`）。
- 🎬 **真·文生视频**、🎙️ **播客大脑**、🎵 **音乐**、👁️ **视觉识图**、💰 **成本看板**。
- 📚 社区文件：`CODE_OF_CONDUCT.md`、`.github/ISSUE_TEMPLATE`、`.github/PULL_REQUEST_TEMPLATE.md`。

### Changed
- 模型表述改为**可插拔**（兼容任意 OpenAI 大模型），去掉写死的型号。
- 安装向导 `install_all.py`：模型检测改为**本地任意 GGUF 或 云端兼容 key 任一通过**，并补齐可选功能依赖检测（N.E.K.O./配音/封面/音乐）。

### Removed
- 旧 Electron 桌面宠物（`desktop/`、`jarvis_desktop.py`、`docs/jarvis-desktop.md`），桌面形象统一改用 N.E.K.O. 猫娘。

### Fixed
- 安装向导不再死锁 `xiaojiao1.0-4B` 型号。
- `/api/env` 模型检测改为模型无关（本地 GGUF 或云端 API 任一即算有）。
- 网页顶栏 "J.A.R.V.I.S." 改为 "🐱 猫娘"，`/pet` 重定向到 N.E.K.O. 页。

---

## v1.0.0 之前的演进快照（已并入当前版）

- v2.3.0：桌面宠物 / 视觉接口 / 成本看板 / 插件万能桥
- v2.2.0：会话侧边栏 / 会话持久化
- v2.1.0：聊天历史持久化 / 插件接入工具系统
- v2.0.0：原生 function calling、多步任务自动执行
- v1.0.0（初版）：完整壳 + 工具 + 记忆 + 会话 + 联网 + 插件

> 这些历史版本已统一收敛到 **v1.0.0**（当前发布）。
