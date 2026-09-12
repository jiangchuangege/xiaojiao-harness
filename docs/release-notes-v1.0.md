# 小焦 v1.0 · 四层修复：把"工具选择"做稳

> 一句话：**不再调错工具、不再在工具海里乱试、该调的一定调到。**

## 本轮修复项（按四层）

### 第 1 层 · 工具描述场景化

所有**可调用**工具（`.py` / `.js` / `.json` 插件 + 内置共 60+ 个）的描述统一改成
「**什么时候用** + 输入 + 输出」，同类工具写明区别：

- `get`（静态页/接口首选）→ `fetch`（需要 JS 渲染）→ `stealthy_fetch`（被 Cloudflare 拦才用，开销最大）
- `net_ip`（查**自己**的公网 IP/归属地）vs `get_ip_info`（查**指定**某个 IP）
- `search_files`（按**文件名**找）vs `grep_files`（按**内容**找）
- `collect_vulnerabilities`（漏洞清单唯一入口）vs `web_search`（普通资料检索）

**顺带修掉一个真缺陷**：`read_file` / `search_files` / `search_content` 在
**内置、code_intelligence、workspace_search** 三处**重名** —— 后加载的插件会覆盖工具路由表，
模型以为调的是 A、实际执行的是 B（这就是"调错工具"最典型的成因）。
现在插件侧改名为 `ci_*` / `ws_*` 区分，并在 `_build_tools()` 加了**冲突防呆**：
内置优先、重名跳过并写 WARNING 日志，插件作者一看日志就知道要改名。

### 第 2 层 · 工具选择决策树（写进 `_TOOL_RULES`）

```
【工具选择顺序】不确定就用这张表，别硬猜：
① 网址/抓网页 → get → fetch → stealthy_fetch
② 查资料/新闻/天气这类信息 → web_search
③ 画图（架构图/流程图/时序图/数据流/状态图）→ archify 工作流（一次搜索都不发）
④ 漏洞清单 → collect_vulnerabilities
⑤ 本机公网 IP/归属地 → net_ip
⑥ 纯聊天/寒暄/概念问答 → 不调任何工具
⑦ 执行命令/写文件/读文件 → run_command / write_file / edit_file / read_file
⑧ 都不沾边又要外部信息 → 优先 web_search，不要硬猜
```

### 第 3 层 · 入口关键词路由（规则先于模型，命中即跳过模型判断）

| 命中 | 动作 |
| --- | --- |
| 句子里有网址（`http(s)://` 或裸域名 `.com/.cn/...`） | 直接抓取，不再让模型决定 |
| 画图/架构图/流程图/时序图/数据流/状态图/archify | 锁定 archify 工具链，**跳过联网检索** |
| 漏洞/CVE/安全公告 | `collect_vulnerabilities` |
| IP/公网/归属地（且没给具体 IP） | `net_ip` 直答 |

### 第 4 层 · 调错后的修正

- **候选表** `_TOOL_FALLBACK`：`get → fetch → stealthy_fetch`、`search_files ↔ grep_files`、
  `archify_validate → archify_read_schema` …
- **抓取自动升级**：`_scrape_failed()` 判定 403/风控页/空正文 → 自动换下一个候选工具
  （实测 `抓 https://www.cloudflare.com`：get 被拦 → 自动 stealthy_fetch 成功）
- **连续 2 次调错即停止**：`_layer4_after_call()` 停止重试、工具轨迹标注「已停止：连续 N 次调错」、
  把最后一次报错原文交给用户，并给出建议候选（不再无限空转）
- **按意图收窄本轮工具**：画图轮只暴露 `archify_*`，网址轮只暴露抓取类 ——
  修掉"画架构图跑去 read_memory、最后把记忆内容当答案"这类乱试
- **多步链路时间预算**：画图轮 240 秒上限，超时停止并如实汇报（原来能跑到 400+ 秒）

## 8 条验收用例（真跑 `/api/chat`）

| # | 输入 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 抓一下 https://example.com | get | ✅ `get`，9 秒，返回正文 |
| 2 | 帮我看看 https://www.gutenberg.org/ 写了啥 | get | ✅ `get` + 真实解读（79,373 本电子书） |
| 3 | 抓 https://www.cloudflare.com | get 失败自动升级 | ✅ get 被拦 → **stealthy_fetch** 成功 |
| 4 | 画一张小焦架构图 | archify 工具链 | ✅ 全链 7 步 + 交付 HTML，**47 秒** |
| 5 | 最近漏洞 | collect_vulnerabilities | ✅ 返回 NVD 漏洞表 |
| 6 | 我的 IP 是多少 | net_ip | ✅ 1 秒返回真实公网 IP |
| 7 | 你好 | 不调工具 | ✅ 0 工具调用 |
| 8 | 今天天气怎么样 | 不硬猜 | ✅ 用 `get_weather`（比硬搜更准） |

## 测试数据

| 项目 | 结果 |
| --- | --- |
| 全量套件 | **248 / 249 · 100%**（1 项按当天 NVD 数据跳过，非失败） |
| 分套件 | 离线 92/92 ｜ 应用逻辑 104/104 ｜ 安全 18/18 ｜ 联网 34/35（1 跳过） |
| 实机 `live_check` | 36 / 36 |
| UI 样式 / 预设 | 25/25 ｜ 13/13 |
| 文档 / Mermaid / 原理 / ruff | 0 错误 ｜ 42 图 0 问题 ｜ 12/12 ｜ 全过 |

## 升级说明

1. **不用改配置**：旧的 `xiaojiao_control.json` 直接可用。
2. **插件作者注意**：工具名必须**全局唯一**；重名会被跳过并打 WARNING（内置优先）。
3. **行为变化**：说了网址就直接抓；说画图就直接走 archify 链（不再联网搜）；
   同一工具连续 2 次调错会**停下并报错**，这是有意设计（不再空转烧时间）。
4. 画图依赖 Archify 插件（`plugins/archify.py` + `plugins/archify.skill.md`）。
5. 改动前备份：`logs/backup_before_refactor/`（`xiaojiao_app.py` / `archify.py` / 控制文件等，均不入库）。


---

## 本版还包含（此前几轮修复，一并落地）

- **提示词分层重构**：`role` 只留人设；检索铁律 / 工具规则 / 插件清单全部由代码管理
  （`compose_system_prompt(role, plugins)` = role + `_SEARCH_RULES` + `_TOOL_RULES` + 动态插件清单），
  插件一变自动重建，**加插件不用改人设**。
- **校验熔断**：`archify_validate` 失败时一次列全所有报错 + 建议修法；同一工具连续失败即熔断，
  实测同一画图指令 **287 秒 → 39 秒**、validate 调用 **7 次 → 3 次**。
- **云端大脑健壮性**：退避重试 + 连续失败熔断 60 秒 + 自动兜本地大脑（并在回答里如实标注）；
  切模型时用 chat 实测；`tools/check_cloud_brain.py` 一键体检。
- **资产测绘插件** `plugins/asset_intel.py`：IP → 命中哪些 CVE（免费无 Key）；CVE → 受影响 IP（配 Key）。
- **公网 IP 直答**、抓取类指令规则直通、幻影工具不进工具表、日志落盘修复（主程序日志曾从未落盘）。
- **发布工具修复**：不再 `git reset --hard` 吞掉未提交代码；tag 改指向不再删引用（Release 不会变孤儿）。
