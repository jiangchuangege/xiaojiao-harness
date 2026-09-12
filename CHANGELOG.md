# 更新日志 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。所有重要改动都会记录在这。

## [v1.2.2] - 2026-09-12

**🔍 用户实测反馈的展示问题（真实截图投诉）：抓取接口后内容仍是一大坨原始 JSON。**

### Fixed

- 🔴 **大 JSON 抓取后展示混乱（根因：截断顺序错了）**：`stealthy_fetch` 抓 NVD 接口返回 **10023 字**，
  正好越过 `MAX_CONTENT_CHARS=10000` 的截断线 —— 旧流程是「**先截断、后美化**」，
  内容被截成**非法 JSON**，于是美化与代码块双双失效，用户看到的是一整屏压缩 JSON。
  现在改为「**先美化、后截断**」，并且由调用方决定是否截断（`clip=False` 保留全文供展示与落盘）。
  实测同一请求：**10023 字原始 JSON → 3538 字 / 115 行美化 JSON + 折叠提示**，前端渲染为可复制代码块。
  > 这个 bug 之前没被发现，是因为我的验证用例用的是 6828 字的响应（**没越过截断线**）—— 现在补了
  > 9 项**超长 JSON 回归用例**（含 16KB 样本），CI 每次都会跑。
- 🐞 **抓到的接口存成 `.json` 却解析不了**：落盘内容带着 Markdown 转义（`NVD\_CVE`），
  `json.load` 报 `Invalid \escape`。现在存 `.json` 时自动「去转义 → 解析 → 美化」成**合法完整 JSON**
  （实测 24404 字、5 条漏洞、可被程序直接解析）；非 `.json` 文件名不改写内容。
- 🐞 **`save_to` 之前会存下"折叠后的预览"**：现在落盘始终是**全文**（实测 13033 字 vs 展示 3538 字）。

### Verified

- 全量测试套件：**103/103 通过 · 通过率 100%**（新增 9 项大 JSON 回归用例）
- 端到端复测（用户原话）：模型路径 ✓ 直接产出 Markdown 表格；规则快通道 ✓ 产出可复制代码块 + 折叠提示

## [v1.2.1] - 2026-09-12

**🔍 实机验收发现的两处体验修复**（UI 层，无接口变更）。

### Fixed

- 🐞 **模型下拉误导性显示"未配置模型"**：`/api/models` 在未显式配置模型时返回空列表，
  界面于是显示"未配置模型" —— 但此时**自动模式下的本地大脑（:9292）其实是通的、对话也正常**，
  用户会以为没配好。现在改为显示「**自动（本地大脑 :9292）**」；只有既无模型又非自动模式才提示去设置页添加。
- 🐞 **浏览器控制台一直报 `favicon.ico 404`**：新增内联 SVG favicon 路由（不额外增加文件），控制台错误清零。

### Verified

- 实机验收 **23/23 通过**（对运行中的服务发真实请求：8 个端点、体检 11 项、抓取、JSON 展示与折叠、SSRF 拦截、指标累加、会话回收器、日志脱敏）
- **真浏览器（Playwright）打开 UI**：发送消息 → 抓取回答渲染正常（代码块/链接/标题/📖解读）→ 控制台 **0 错误**，已留截图证据
- 全量测试套件：**94/94 通过 · 100%**

## [v1.2.0] - 2026-09-12

**🛠️ 稳定化落地版**：可观测（指标 / 日志）、可回收（会话）、可并发（批量）、可验证（压力测试 + CI），
并把文档补成「能照着做」的形态。**语义化版本说明**：全部为兼容性新增与内部整改，**无破坏性变更**。

### Added

- 🛡️ **安全审计报告 `docs/security-audit.md`**（新增）：SSRF（直连 + 重定向 + **数值型绕过**）/ robots /
  限速 / 目录穿越 / 日志脱敏 / UA / 命令端点 / 无遥测 / 无明文密钥 的逐项结论与复现命令；
  附带安全控制点流程图。**本轮审计发现并修复 2 个真实问题**（见 Fixed）。
- 🧪 **测试与稳定性报告 `docs/testing-report.md`**（新增）：测试资产清单、执行结果、**覆盖矩阵（诚实版）**、
  4 项未覆盖清单、24 小时长跑方法与判定规则。
- ⏱️ **24 小时稳定性脚本 `tests/stress/stability_24h.py`**（新增）：每小时一轮抓取，记录成功率 / P50 / P95 /
  Python 堆增长；自动判定 PASS / SUSPECT / FAIL 并产出 `stability_report.json` + Markdown 摘要。
- 🔐 **安全用例套件 `tests/stress/test_security.py`**（新增，18 项，已接入 `run_all.py`）：SSRF 21 种写法矩阵、
  robots RFC 9309 三种分支、同域限速计时、日志脱敏**回读文件验证**、UA 合规、目录穿越、命令端点源码契约、
  无遥测埋点、已跟踪文件无明文密钥。
- 📐 **架构说明 `ARCHITECTURE.md`**（新增）：设计目标 → 系统总览图 → 进程与端口 → 模块职责表 →
  **一次对话的完整生命周期（时序图）** → 插件机制与契约 → 大脑/小脑协作 → 数据与状态 →
  质量与安全 → **扩展点（改哪里）** → 已知限制（诚实清单）；含 5 张 Mermaid 原理图。
- 🤝 **贡献指南 `CONTRIBUTING.md`**（新增）：五分钟上手、分支/提交规范（**中文 commit**）、
  提交前自检、**8 条硬性约束**（禁硬编码/禁明文密钥/错误必须中文/不许静默吞异常…）、插件模板、报 Bug 规范。
- 🧭 **图语法自检 `tools/check_mermaid.py`**（新增）：离线校验所有文档里的 Mermaid（围栏闭合、
  subgraph/end 配对、引号与括号配对、sequenceDiagram 关键字），已接入 CI 作为**文档质量闸门**；
  当前全仓 **39 个图 0 问题**。
- 🖼️ **前端渲染自检**（补进 `tests/stress/test_units.py`）：真测后端转换（Setext→ATX、JSON 归拢）
  + 对渲染模板做**契约检查**（`_fence_body`、`codebox lang-`、`renderTableBlock`、`mdh`、`srcbox`、`<br>`、`/metrics`）。
- 🪵 **统一日志模块 `xiaojiao_log.py`**：所有模块走同一套 logging（`logs/xiaojiao.log`，5MB×3 轮转），
  支持 `XIAOJIAO_LOG_LEVEL` / `XIAOJIAO_LOG_CONSOLE` 调节；**写日志前全链路脱敏**（`api_key=`、`sk-…`、`ghp_…`、`AKIA…`、JWT 一律打码），
  日志目录不可写时自动降级、绝不影响主流程。
- 🔧 **安全重构工具 `tools/fix_silent_except.py`**：用 AST 精确把「`except: pass`」改成「记日志 + 明确降级」，
  把裸 `except:` 改成 `except Exception:`；默认 dry-run、改写后自动语法校验、语法不过则放弃该文件。
- 🧪 **压力测试套件进仓库 + CI 定时跑**：新增 `tests/stress/`（`run_all.py` 编排 + `harness.py` 骨架 + 离线与联网两套用例，结果机读 `results.json`），以及 `.github/workflows/stress-test.yml`（每天 03:00 定时 / 手动触发 / 插件或测试变更触发；**通过率 < 95% 直接失败**；结果上传 artifact 并写入 Job Summary）。本地实测 **67/67 通过 · 通过率 100% · 74.3 秒**。用法见 [tests/stress/README.md](tests/stress/README.md)。
- ⚡ **批量并发可配置（BatchConfig）**：原来批量是串行的，3 个域名也要排队；现在拆成**跨域并发**（`batch.concurrency`，默认 3）与**同域闸门**（`batch.per_domain_limit`，默认 1，永不并发打同一个站），外加 `rate_limit` / `max_retries` / `backoff_base`。实测 3 个不同域名 **6.20s → 0.92s（提速 85%）**；同域实测最大并发仍为 1。非法配置（如 `concurrency=0`）**不静默忽略**，批量工具直接返回中文错误。复测 **18/18 通过**。
- 📊 **指标与观测（MetricsCollector）**：每次工具调用自动记录 `calls / success / fail / total_latency / avg_latency / max_latency / circuit_breaks / last_error`，三种取法：`GET /metrics`（Prometheus 文本，可直接抓取）、`GET /api/scrapling/metrics`（JSON，含活跃会话明细与熔断状态）、`logs/scrapling_metrics.json`（落盘）。安全拦截（SSRF/robots）不计失败；错误信息**脱敏后**入库。实测 **17/17 通过**，`/metrics` 线上返回 200。
- 🔒 **`sanitize()` 脱敏补强**：原来只认 `key=value` 形式，**裸凭据会原样泄露**（指标自测发现 `sk-xxx` 未被抹掉）。现在额外覆盖 `sk-…` / `ghp_…` / `AKIA…` / `xox…` / JWT / `password=`，日志与指标一律打码。
- 🧹 **会话自动回收（SessionManager）**：`open_session` 每开一次就真起一个浏览器，忘了 `close_session` 会一直占内存。现在三条规则任一命中即自动回收并真关闭：**TTL**（`session_ttl`，默认 30 分钟）/ **空闲**（`session_idle`，默认 5 分钟）/ **上限**（`max_sessions`，默认 20，超出踢最久未用 LRU）；后台线程每 60 秒巡检；配置非法（0/负数/非数字）回退默认并中文告警。实测 **10/10 通过**（LRU、TTL、空闲、真实会话回收、用户主动关闭从回收表移除）。

### Changed

- 🧹 **消除"静默吞异常"（阶段 2 代码质量）**：静态审计发现 `except: pass` 共 **103 处**、裸 `except:` 19 处 ——
  出错时无声无息，线上完全无法排障。用 AST 工具批量改为「`LOG.debug("忽略异常(文件:行): 异常")`」，
  并给裸 `except:` 加上 `Exception` 限定（不再吞掉 `KeyboardInterrupt`）。
  审计结果：**静默吞异常 103 → 6、裸 except 19 → 14、疑似密钥 0**（剩余均在本地未入库的自用插件里）。
- 🧹 **清理死代码**：插件加载器里重复的 `elif man.get("tools")` 分支（永远不可达）已删除。
- 🧹 **行尾一致性**：仓库里 CRLF/LF 混存曾导致"整文件被改"的巨型 diff（`xiaojiao_app.py` 7232 行噪音，真实改动仅 73 行）——
  已按各文件在 HEAD 中的原始约定对齐并强制重新入库，现在 diff 干净可评审。

### Fixed

- 🔴 **SSRF 数值型写法绕过（高危，安全自测当场发现）**：`http://2130706433/`（= `127.0.0.1` 的十进制写法）
  能绕过原有检查 —— 它既不以 `127.` 开头，本机 DNS 也解析不了（走进"解析失败→放行"分支），
  但 HTTP 客户端会把它当 IP 用，等于**直接打本机**。现在统一把十进制 / 十六进制 / 八进制 / 缩写点分
  （`2130706433`、`0x7f000001`、`017700000001`、`127.1`、`10.1`、`192.168.1`）还原成 IP 再判定，**6 种写法全部拦截**。
- 🟠 **命令执行服务默认暴露局域网（中高危，源码审计发现）**：`xiaojiao_tools.py` 的 `/api/run` 无鉴权、能执行任意
  PowerShell，却默认监听 `0.0.0.0:5003`，且 `force` 默认 `True`（跳过危险确认）。现在**默认只监听 127.0.0.1**
  （局域网使用需显式设 `XIAOJIAO_TOOLS_HOST`），且**只有本机客户端**才允许跳过确认（非本机请求降级 + 记警告）。

## [v1.1.0] - 2026-09-12

### Added

- 🕷️ **内置 Scrapling**（`plugins/scrapling_bridge.py` 桥接插件）：小焦从此**想抓啥抓啥**——网页 / 动态页 / 接口 JSON / 批量列表 / 登录态页面 / 下载任意文件。**Scrapling 原生 13 个工具 1:1 全部暴露（工具名与官方一致）**，另加 3 个小焦增强（`get` 友好别名 / `scrape_with_selector` 自适应选择器 / `download` 任意文件下载），并保留 `browser_session` 聚合入口 → 对外共 **17 个工具**：
  - 原生 13：`make_request` / `bulk_get` / `fetch` / `bulk_fetch` / `stealthy_fetch` / `bulk_stealthy_fetch` / `open_session` / `open_request_session` / `close_session` / `list_sessions` / `session_fetch` / `session_make_request` / `screenshot`
  - 小焦增强 3：`get`（`make_request` 的中文友好别名）/ `scrape_with_selector`（自适应选择器，防站点改版）/ 🆕 `download`（**下载任意文件** PDF/EPUB/ZIP/图片/音视频…，Scrapling 原生没有这个能力）
  - 兼容入口 1：`browser_session`（用 `action` 一个工具走完 open/fetch/screenshot/close 全流程）
  - 🆕 `save_to` 参数：抓取正文直接存文件到 `books/`（长文/连载章节不塞对话）
  - 会话类型用错时给**中文提示**（如 dynamic 会话不能发 HTTP 请求，会告诉你去用 `session_fetch` 或先 `open_request_session`）
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

- 🧪 **全工具极限压力自检修复批次**（17 个工具 × 正常/空参/非法/必失败/连续 + 8 项对抗 + 性能基准，共 4 轮**真实调用**）：
  - 🐞 **`download` 把 404 错误页当文件保存**：目标 404 时插件仍报"已下载（类型 text/html）"并把错误页存成 `.pdf`。现在只有 2xx 才落盘；404/410 直接报"文件不存在，未保存任何文件"；只有 401/403/406/429（WAF 拦 UA）才回退浏览器指纹通道；0 字节、扩展名与内容类型不符也会明确警告。
  - 🐞 **批量 `urls` 传字符串被逐字符拆开**：`"https://a.com"` 被当成 **14 个单字符"网址"**，静默返回 14 条无意义错误。新增 `_norm_urls()`：字符串当 1 个网址、空列表报"urls 为空"、其它类型明确报错。
  - 🐞 **批量"全部失败"却返回成功**：顶层 `error` 为空 + status 200 → 调用方与熔断器都当成功，坏源永不被熔断。现在全失败置顶层错误（保留 `items` 明细）。
  - 🐞 **非法 `session_type` 毒化会话表**：非法类型会让 Scrapling 注册一条脏会话，此后 `list_sessions` **每次**都抛 Pydantic 校验错误（会话列表永久不可用）。现在插件前置白名单校验（dynamic/stealthy/static），`list_sessions` 另加可读降级提示。
  - 🐞 **用户传的 `timeout` 被静默忽略**：`get`/`fetch`/`stealthy_fetch`/`scrape_with_selector`/批量只用了配置默认值 —— 实测"6 秒超时"等了 **22 秒**才回来（Scrapling 默认重试 3 次 × 6 秒）。现在显式 timeout 会带上参数、收敛 `retries=1`、并给客户端加硬上限（实测 get 7.2s / fetch 13.0s 内返回）。
  - 🐞 **`scrape_with_selector` 选择器没匹配到却"空内容 + 成功"**：现在返回结构化 `not_found`（含 `not_found: true` / `selector`）与中文建议，绝不假装抓到东西。
  - 🐞 **英文/裸库错误外泄**：`Session 'x' not found`、`validation error for SessionInfo`、`net::ERR_NAME_NOT_RESOLVED`、`curl: (28)`、`Redirect to internal IP ... rejected` 等一律转成可执行中文（新增 `_humanize_error()`）。
  - 🔒 **回归确认**：SSRF 直连（含 `file://` / `169.254.x` / `[::1]` / `0.0.0.0`）与**重定向型 SSRF**（公网 302 → `127.0.0.1`）100% 拦截；`download` 目录穿越（`../../`）不逃逸出项目目录并提示已改名。
  - 📊 **复测**：17/17 工具、**32/32 用例全部通过**；熔断第 4 次触发且 32 秒后自愈；连续 20 次调用 Python 堆净增 0.10MB。
- 🐞 **插件加载器**：`spec_from_file_location` 未注册 `sys.modules` → Python 3.13 下**任何使用 `@dataclass` 的插件都会静默加载失败**。已修复（加载失败时清理 `sys.modules`）。
- 🐞 **前端渲染**：`renderBlocks` 按空行分块后块间缺换行 → 段落粘连；`.mdh` 标题类无 CSS → 标题不显示样式；`inline()` 不识别 Markdown 链接。均已修复。
- 🐞 **插件参数适配**：`_adapt_args()` 对不支持 `timeout` 的工具（`open_session`/`close_session`/`list_sessions`）硬塞 timeout → 报错；改为按工具白名单过滤参数并区分超时单位（浏览器类=毫秒，HTTP 类=秒）。
- 🐞 **抓取内容**：`extraction_type="markdown"` 缺 `markdownify` 会整体失败 → 改为提前探测 + 优雅降级（html 提取 + 内置 HTML→Markdown）。
- 🐞 **下载被 WAF 拦（403）** → 下载改用标准浏览器头，失败自动退回 Scrapling 浏览器指纹下载。
- 🐞 **熔断误判**：安全拦截（SSRF/robots/参数缺失）曾被计入"工具失败"，连续拦截会导致工具被误熔断 → 安全拦截不再计入。
- 🐞 **批量工具参数错误**：批量内部曾把单个 `url` 传给 `bulk_*` 工具 → 改为逐 URL 调用单个工具（这样才能真正控制限速/退避/失败隔离）。
- 🐞 **视频任务残留报错**：前端对已结束的视频任务轮询报「任务状态丢失」并混进对话 → 改为静默清理 localStorage / 温和收尾。
- 🐞 **robots.txt 误拦整站（真实案例：NVD API 抓不了）**：`urllib.robotparser` 遇到 401/403 会设 `disallow_all=True`，于是 **WAF 把 robots.txt 请求 403 掉 → 整站被判"禁止抓取"**；而用浏览器 UA 去看那些站点往往**根本没有 robots.txt**（404）。改为插件自己拉取并按 **RFC 9309** 判定：只有真读到 `Disallow` 才拦，404/403/超时/5xx 一律放行；同时新增**单次放行开关** —— 对话里说「忽略 robots 抓一次」（`ignore_robots: true`）或配置 `scrapling.allow_robots_skip: true`，并给出可执行的中文提示。
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
