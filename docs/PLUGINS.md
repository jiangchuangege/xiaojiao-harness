# 插件开发指南

| 项 | 值 |
| --- | --- |
| 适用版本 | v1.0 |
| 最后更新 | 2026-09-14 |
| 维护者 | 小焦项目 |
| 文档状态 | 稳定 |

**摘要**：说明小焦的插件机制（如何被扫描、注册、开关、去重）以及怎么写一个插件。当前有哪些工具、每个工具做什么，见 [工具清单](tools.md)；其它扩展方式（改人设、加模型、接外部系统）见 [扩展开发](extend.md)。

## 目录

1. [机制](#1-机制)
2. [写一个 Python 插件](#2-写一个-python-插件)
3. [四种插件类型](#3-四种插件类型)
4. [生效与验证](#4-生效与验证)
5. [契约与边界](#5-契约与边界)
6. [可参考的现有插件](#6-可参考的现有插件)
7. [相关文档](#7-相关文档)

---

## 1. 机制

插件机制由 `xiaojiao_app.py` 的 `load_plugins()` 实现，思路是"往目录丢文件即生效，不为主程序改一行代码"：

1. 启动时扫描项目根目录下的 `plugins/`，按后缀识别插件类型；
2. 每个插件登记为一条记录：插件名（= 文件名去掉后缀）、类型、工具描述列表、开关状态、文件路径；
3. 全部记录汇总进全局 `PLUGINS`；
4. 依据 `xiaojiao_control.json` 的 `capabilities.plugins` 设置每个插件的 `on` 状态；
5. 立即重建系统提示词：`compose_system_prompt()` 会把"当前可用的工具清单"动态拼进去（`_plugin_list()`，最多列 90 条，超出部分标注"另有 N 个工具"）。

因此模型"知道现在有哪些工具"这件事是跟着 `PLUGINS` 走的：重启后新增插件自动出现在清单里，不需要人工改人设。

载体层另有一个能力登记处 `core/carrier/capability.py`（类 `CapabilityRegistry`），用于回答"现在有哪些工具、跟上次比多了少了什么、刚丢进来的文件算不算数"。它优先调用主程序的真实接口（`load_plugins()` + `all_tool_names()`），主程序不可用时退化为扫描目录并按正则粗估工具名。它的定位是**清点与留档**（能力清单会落盘到 `logs/carrier/`，该目录已被 `.gitignore` 忽略），不影响工具的实际执行路由。

### 1.1 工具的路由

内置工具与插件工具统一在 `_build_tools()` 里合并成给模型的功能列表，并建立"工具名 → 插件名"的路由表 `_TOOL2PLUGIN`；执行时由 `run_tool()` 统一进入，插件工具再转到 `run_plugin()`。

### 1.2 重名防呆

不同插件或内置工具使用同一个工具名时，后加载的会覆盖路由表，导致"模型以为调的是 A，实际执行的是 B"。当前的处理是：

- 内置工具名先占位（`check_env`、`run_command`、`read_file` 等 14 个）；
- 插件里与之同名的工具**直接跳过**，并写一条 `工具名冲突，已跳过` 告警日志；
- 插件之间重名同样按先到先得处理。

因此插件作者应保证工具名互不重复，看到该告警就去改名。

### 1.3 外部清单类插件的过滤

`.json` 清单插件里若只声明工具、没写 `url`，该工具实际无法执行（调用只会返回"需对应运行时或填写 url"）。这类工具不会下发给模型，判据由插件自身的 `has_url()` 决定。

## 2. 写一个 Python 插件

Python 插件是一个 `.py` 文件，文件里至少有一个类同时提供两个方法：

| 方法 | 作用 |
| --- | --- |
| `get_tool_descriptions()` | 返回该插件提供的工具列表，每项含 `name` / `description` / `parameters`（JSON Schema） |
| `execute(name, params)` | 按工具名执行，返回字符串 |

一个文件只注册**第一个**同时具备这两个方法的类（找到即停止），但这个类可以一次声明多个工具，在 `execute` 里按 `name` 分发。

### 2.1 最小示例：无参数工具

```python
# plugins/my_time.py
import datetime


class MyTimePlugin:
    """时间助手插件。"""

    def get_tool_descriptions(self):
        return [{
            "name": "get_time",
            "description": "获取当前日期和时间",
            "parameters": {"type": "object", "properties": {}},
        }]

    def execute(self, name, params):
        if name == "get_time":
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return None
```

### 2.2 带参数的工具

```python
# plugins/my_weather.py
import requests


class MyWeatherPlugin:
    def get_tool_descriptions(self):
        return [{
            "name": "get_weather",
            "description": "查询城市天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如 北京"}},
                "required": ["city"],
            },
        }]

    def execute(self, name, params):
        if name == "get_weather":
            city = params.get("city", "北京")
            r = requests.get(f"https://wttr.in/{city}?format=%C+%t", timeout=8)
            return f"{city}天气：{r.text.strip()}"
        return None
```

把文件放进 `plugins/` 后重启小焦即可被扫描到。

## 3. 四种插件类型

| 类型 | 识别方式 | 提供什么 | 依赖 |
| --- | --- | --- | --- |
| Python 插件 | 文件名以 `.py` 结尾且不以 `__` 开头 | 类里的 `get_tool_descriptions()` + `execute()` | 无额外依赖 |
| JS 插件 | 文件名以 `.js` 或 `.mjs` 结尾（`plugin_runner.js` 除外） | 导出对象提供 `getToolDescriptions()` 与 `execute(name, params)`，可选 `getSettings()`；执行时由 `plugins/plugin_runner.js` 起 node 子进程 | 需要 Node.js |
| JSON 插件 | 文件名以 `.json` 结尾 | 分三种：`type` 为 `skin` 的是皮肤；带 `tools` 数组的是工具清单（支持 OpenAI / Claude / DSH 三种写法，只有带 `url` 的才可执行）；否则按 API 插件处理，把 HTTP 接口声明成工具 | 无额外依赖 |
| 技能文档 | 文件名以 `.md` 结尾 | 内容拼进系统提示词（人设层），本身不提供可调用工具 | 无额外依赖 |

### 3.1 JS 插件示例

```javascript
// plugins/js-calc.js
module.exports = {
  getToolDescriptions() {
    return [{
      name: "js_calc",
      description: "计算数学表达式，如 (3+4)*2",
      parameters: {
        type: "object",
        properties: { expr: { type: "string", description: "数学表达式" } },
        required: ["expr"],
      },
    }];
  },
  execute(name, params) {
    if (name === "js_calc") {
      const expr = String(params.expr || "");
      if (!/^[\d\s+\-*/().%^]+$/.test(expr)) return "只支持纯数字运算表达式";
      return String(Function('"use strict";return (' + expr.replace(/\^/g, "**") + ')')());
    }
    return null;
  },
};
```

`execute` 允许返回 Promise，运行器会等待它 settle 后再取结果。

### 3.2 API 插件（JSON）示例

```json
{
  "name": "ip",
  "description": "查询本机公网 IP",
  "tools": [
    {
      "name": "get_ip",
      "description": "查本机公网 IP，只返回地址",
      "parameters": {"type": "object", "properties": {}},
      "method": "GET",
      "url": "https://api.ipify.org",
      "response": "text"
    },
    {
      "name": "get_ip_info",
      "description": "查某个 IP 的归属地",
      "parameters": {"type": "object", "properties": {"ip": {"type": "string"}}},
      "method": "GET",
      "url": "https://ipapi.co/{ip}/json",
      "response": "json"
    }
  ]
}
```

执行规则由 `_api_execute()` 实现：

- `{参数名}` 占位符会被入参替换；
- `GET` 把入参作为查询串；`POST` / `PUT` / `PATCH` 把入参（可用 `body_exclude` 排除若干字段）作为 JSON 请求体；
- `response` 为 `json` 时返回解析后的对象（可用 `field` 指定只取某个字段），否则返回文本（截断到 2000 字）；
- 单个工具可单独设置 `headers` 与 `timeout`（默认 30 秒）。

## 4. 生效与验证

1. 把文件放进 `plugins/`；
2. 重启小焦（`python start_xiaojiao.py`）；
3. 查看已注册的插件：

```powershell
python -c "import xiaojiao_app as x; print(list(x.PLUGINS.keys()))"
```

4. 也可以在「设置」页看到插件列表与开关状态（数据来自 `GET /api/settings` 的 `plugins` 字段）。
5. 想默认启用或停用某个插件，在 `xiaojiao_control.json` 里配置：

```json
{
  "capabilities": {
    "plugins": {
      "my_time": true,
      "my_weather": false
    }
  }
}
```

`capabilities.plugins` 必须是**对象**（插件名 → 布尔）。写成布尔值 `true` 会让插件加载阶段抛 `AttributeError`，导致小焦无法启动。

临时停用某个插件：把文件移进 `plugins/_disabled/` 子目录。载体扫描会跳过该目录，主程序也只扫描 `plugins/` 一层，因此其中的文件不会被加载。

## 5. 契约与边界

| 项 | 约定 |
| --- | --- |
| 插件名 | 等于文件名去掉后缀；重名文件会互相覆盖 |
| 工具名 | 不可与其它工具重复，也不要与 14 个内置工具重名（见 [工具清单](tools.md)） |
| 返回值 | 返回 `str` 会原样交给模型与用户；返回 `None` 表示"本次没做事"，模型继续 |
| 异常 | `run_plugin()` 会捕获插件抛出的异常并返回空串（不转成中文报错）。若希望用户看到可读信息，请在 `execute` 内部自行 `try/except` 并返回中文说明 |
| 耗时 | `execute` 是同步调用，长时间阻塞会拖慢对话；耗时任务建议改为后台执行并在后续轮次取结果 |
| 依赖 | 只使用已安装的库（`requests`、`json`、`datetime`、标准库等），不要在插件里临时装包 |
| 加载失败 | 任何插件导入失败都会被静默跳过，只影响该插件本身；排查时先用第 4 节的命令确认它是否出现在 `PLUGINS` 里 |
| 安全 | `execute` 能做的事等于你的代码能做的事。危险动作小焦侧另有删除红线与危险命令确认，但插件自身的网络与文件行为由作者负责 |

## 6. 可参考的现有插件

| 插件 | 提供工具数 | 可借鉴之处 |
| --- | --- | --- |
| `plugins/scrapling_bridge.py` | 18 | 多工具分发、内网地址闸门、会话回收、抓取指标 |
| `plugins/archify.py` | 15 | 把外部工具链封装成"读技能 → 读 schema → 校验 → 交付"的固定工作流 |
| `plugins/code_intelligence.py` | 7 | 大结果截断，避免把整份源码塞进上下文 |
| `plugins/db_helper.py` | 5 | 只读约束（只允许 SELECT）、连接状态保持在插件实例里 |
| `plugins/asset_intel.py` | 3 | 免费源与付费源混用、未配置 Key 时给出可操作的说明、异常一律转中文 |
| `plugins/workspace_search.py` | 3 | 限定工作区范围的文件检索 |
| `plugins/netdoctor.js` | 3 | JS 插件写法：网络诊断类工具 |
| `plugins/ip.json` | 2 | JSON API 插件写法：声明式接入第三方 HTTP 接口 |
| `plugins/causal_memory.py` | 6 | **常驻子进程 + MCP（JSON-RPC over stdio）**：把外部 MCP 服务当工具用；找不到二进制时一个工具都不注册 |
| `plugins/causal_memory_ext.py` | 10 | **同一个引擎的第二批工具**：复用第一批的常驻连接，把主程序一行不改地扩出 10 个能力；也是"新插件不该做什么"的反面教材注解（见 6.3） |
| `plugins/bili_duty.py` | 29 | **子进程 + 自带 venv 隔离**：B站 客户端的依赖不污染主程序；写操作（发评论/私信）默认拒绝、必须显式 `force`；插件崩了只影响这一条工具 |

`asset_intel` 的数据源接入说明见 [资产测绘插件](asset-intel.md)。
`bili_duty` 的完整说明（值班流程、29 个工具分组、红线、配置项、常见故障）见 [刷B站（bili_duty）](bili-duty.md)。

### 6.1 `causal_memory`：接一个外部 MCP 服务当工具（2026-10-15）

小焦自己已经有事实/画像记忆（`memory_vec`）和经验记录（`self_learn` 里几百条工具成败与反思），
所以这个插件**只接小焦缺的那部分**：类型化的因果教训。它把
[causal-memory](https://github.com/JingxuanC/causal-memory)（Rust 单二进制，Apache-2.0）
当常驻子进程拉起来，通过 MCP 的 JSON-RPC 说话，提供 6 个工具：

| 工具 | 对应用途 |
| --- | --- |
| `causal_warn` | 动手前预警（抑制侧）：这个动作以前造成过什么后果 |
| `causal_search` | 查以前的教训：同类事情上什么做法导致了什么结果 |
| `causal_record` | 记一条「决策 → 结果」（`relation` 取 caused / enabled / prevented / co_occurrence） |
| `causal_trace` | 故障多跳溯源：从一个坏结果往回追出因果链 |
| `causal_counterfactual` | 两个候选方案各自过去的结果分布 |
| `causal_report` | 预测账本：以前的判断押对了多少 |

配置写在 `xiaojiao_control.json`（环境变量 `CAUSAL_MEMORY_EXE` / `CAUSAL_MEMORY_DB` /
`CAUSAL_MEMORY_TASK_TAG` 可覆盖）：

```json
"causal_memory": {
  "exe": "C:/xiaojiao/causal-memory/causal-memory.exe",
  "db":  "C:/xiaojiao/causal-memory/xiaojiao.db",
  "task_tag": "小焦运维"
}
```

二进制从该项目的 Releases 下载 `windows-x86_64` 包解压即可（本机实测的是 0.9.3 版）；
**找不到二进制时 `get_tool_descriptions()` 返回空** —— 一个工具都不会注册，
宁可不出现，也不给模型一个点不动的工具；真去调它会给一句中文说明，告诉你缺什么、去哪下。

两条使用提醒：一是它记的是「决策 → 结果」，**不主动埋点就是空库**，要靠 `causal_record` 攒；
二是攒到一定量后跑一次 `causal-memory sleep`（离线巩固），它的预警档位才会更准。

自测：`tools/test_causal_memory_plugin.py`（离线段用假 MCP server 验客户端这条链，
真机段用真二进制 + 临时库跑「记一条 → 搜回来 → 动手前预警」）。

### 6.2 关键动作自动埋点（`core/causal_hooks.py`，2026-10-15）

光接上没用——它记的是「决策 → 结果」，**不埋点就是空库**。所以在三类最有价值的动作上自动埋：

| 类别 | 认什么 | 挂点 |
| --- | --- | --- |
| 配置类（config） | 写/改配置文件（`xiaojiao_control.json`、`llama-swap.y*ml`、`*.yaml|yml|toml|ini|conf|env`、`presets/*.json`）；命令里出现配置文件路径也算 | `run_tool()`（工具的唯一入口） |
| 文件类（file） | 写文件（`write_file`／`edit_file`／`append_file`／`save_to`）；命令里含删除/覆盖/移动（`Remove-Item`／`rm`／`del`／`Set-Content`／`>` 等） | 同上 |
| 显存类（vram） | 大脑切换（卸载/加载模型 = 显存里谁进谁出） | `brain_manager.switch_to()` |

只读类、搜索类一律不埋（埋点要少而准）。动作**之前**问一句、**之后**记一笔，两条硬规矩：

1. **绝不挡路**：之前的查询最多等 2 秒（等不到就不说），之后的记账丢后台线程——
   用户这一轮不因为"记一笔账"多等一毫秒；
2. **宁可不说，不许吓唬人**：只有对面明确给 `WARNING` / `DANGER` 才提示；
   `UNKNOWN`／空／超时不会说成危险，最多挂一句**如实标注的参考**
   （「有相关历史记录（引擎未给安全档位，仅供参考、不是结论）」）。

开关都写在 `causal_memory` 那一块里：

```json
"causal_memory": {
  "auto": true,                       // 总开关，false 就完全不埋点
  "auto_kinds": ["config", "file", "vram"],
  "auto_min_interval_s": 600,         // 同一个动作多久内只记一次（不刷库）
  "auto_max_per_hour": 30,            // 每小时上限
  "auto_show_related": true,          // 引擎没给档位时要不要挂那句"有相关记录"的参考
  "env": { "CAUSAL_MEMORY_LLM_API": "http://127.0.0.1:9292/v1" }
}
```

`env` 里可以给引擎自己的 LLM / 嵌入配置（`CAUSAL_MEMORY_LLM_*` 判极性用、
`CAUSAL_MEMORY_EMBED_*` 语义检索用；都不配就是纯 BM25）。

⚠️ **实测到的现状（写下来免得误会）**：直接用 `record_decision` 写进去的边，
引擎当前的极性判定给的是 `other`／`mixed`，所以 `intervention_query` 基本都回 `UNKNOWN` ——
也就是「高危预警」这一档现在很少亮，你会先看到"有相关记录"那句参考。
想让预警真正分档，得靠它的提炼链路（`extract`／`judge`／`distill`）或攒够样本后的 `sleep` 巩固。

自测：`tools/test_causal_hooks.py`（37 项：分类只认三类、UNKNOWN 不当危险、
查询最多等 2 秒就放弃、记账后台返回、同动作节流、每小时上限、`auto=false` 全静默、
插件没加载时整层安静）。

### 6.3 一个组合案例：同一件事分两个插件写（2026-10-15）

`causal_memory.py`（6 个工具）+ `causal_memory_ext.py`（10 个工具）是同一个外部引擎的两批工具，
**故意拆成两个文件**，因为它们的错法不一样、边界也不一样：

* **第一批**负责"连上、并保持连上"：常驻子进程、MCP 握手、进程死了自动重启、
  找不到二进制时一个工具都不注册。它的风险是**基础设施**（连不上）；
* **第二批**负责"把引擎的能力补齐"：它只做参数拼装与结果包装，
  连的还是第一批那个常驻连接。它的风险是**接线**（调错工具、调错方法）。

**这一批最值得写下来的教训（也是新插件的反面教材）**：
第二批第一版为了省事，直接调了第一批插件的 `execute()` —— 而那个方法**只认它自己的 6 个工具名**，
别的一律返回 `None`。于是十个工具**全部**返回"没有相关记录"，
看上去像"库是空的"，其实是调用被静默过滤掉了。
正确的做法是复用第一批实例的 `_call()`（直接对 MCP 说话）。
这条已经钉进 `tools/test_causal_memory_ext.py`：stub 里的 `execute()` 一被调用就抛异常。

两个插件的共同规矩（值得任何插件照抄）：

1. **能力不可用时一个工具都不注册**（`get_tool_descriptions()` 返回 `[]`）——
   宁可不出现，也不给模型一个点不动的工具；
2. **所有失败都转成中文说明**（小焦的 `run_plugin()` 会把异常吞成空串，不自己说用户就什么都看不到）；
3. **空结果要如实说明**，并写清"这不代表没事"；
4. **不可逆操作可回滚**：作废走引擎的软作废（`invalidate_*`），不是删除。

完整说明（结构图、装法、埋点、旁挂工具、实测数据与已知问题）见
[因果记忆集成](causal-memory.md)。

### 6.4 `rea`：把一个 229 工具的逆向工程引擎收成三个入口（2026-10-17）

[REA](https://github.com/morluto/rea)（Reverse Engineer Anything，npm 包 `rea-agents`）是一个
MCP stdio 服务，工具表里**229 个工具**：PE / ELF / Mach-O / .NET / APK / JS / WASM 的检查、
反编译、xref、字符串搜索、证据导出，外加 frida 动态注入、GDB、模型文件（GGUF / safetensors）检查。

**为什么不把 229 个都挂上去**：小焦的大脑是本机 4B 小模型，工具表按意图装载。
229 个名字塞进去，可预期的后果是它挑错工具、或者挑一个参数根本对不上的。
所以这个插件只暴露三个**粗粒度**入口：

| 工具 | 干什么 |
| --- | --- |
| `rea_status` | 服务在不在线、版本、可用工具数。出问题先看这个 |
| `rea_tools` | 用关键词搜它能做什么（`pe`、`apk`、`decompile`、`frida`、`gguf`），返回名字和**必填参数** |
| `rea_call` | 调指定工具：先 `open_binary`（给 `path`）打开目标，再调分析类工具 |

**接线用的东西**：`core/mcp_stdio.py`（一个六十行的 MCP 客户端：起进程、握手、列工具、调工具）。
插件本身**不做协议细节**，只做三件事：注册条件、参数/结果的翻译、错误话术。
（`plugins/causal_memory.py` 里有一份同思路的实现，暂时并存 —— 那份已在跑且有 25 项自测钉着，
这轮不动它。）

**真机实测（本机，2026-10-17）**

* 连上只要 **0.4 秒**，`rea_status` 报 `rea v6.8.0 ｜ 229 个工具`；
* `open_binary` 打开 `C:\Windows\System32\notepad.exe` → **1.2 秒**认出
  `format=pe`、`architecture=x86_64`、`sha256=468ffe12…`；
* `binary_overview` / `list_documents` 这类深层分析报 `provider_unavailable` ——
  REA 的反编译要本机装 Ghidra 或 Hopper，**这台机器没装**。所以"识别文件"能用、
  "反编译"现在用不了（这是环境，不是接线）。
* 走小焦那条路真的调通了：问它「用 rea_status 看一下逆向服务在不在」，
  4B 自己选中 `rea_status`、工具真的执行、把 229 这个数原样答出来。

**这块踩出来的三个真坑（都写进了自测）**

1. **用 `cmd.exe /c node …` 起服务会留下孤儿进程**：`terminate()` 只杀 cmd.exe，
   node 还活着并继续占着 stdout 管道，调用方以为没结束就干等 —— 现象是"卡十分钟、日志里一个字没有"。
   改成直接起 `node`，并在 `stop()` 里用 `taskkill /F /T` 杀**进程树**。
2. **`call()` 里先拿锁再 `start()`，而 `start()` 里的握手又要 `call()`** ——
   普通的不可重入锁就是**自己锁死自己**（表现同样是静默卡住）。锁换成 `RLock`。
3. **`cmd.exe /c` 里的 `/c` 被当成文件路径**：启动器校验报"文件不存在"，
   于是工具一个都不注册 —— 真机上整条接线静默失效。校验现在会跳过开关参数。

**已知边界（面试/文档都要这么说）**

* 4B 有时会挑错工具：问「用 rea 打开 notepad 看看格式」，它选了 `open_app`（真把记事本打开了）。
  工具描述已经把「打开」这类动词去掉、换成「逆向分析」类词，但仍然要靠 `rea_tools` 先搜；
* 深层分析需要外部引擎（Ghidra / Hopper），本机没有；
* 第一次调用要等 node 冷启动（几秒），之后常驻。

自测：`tools/test_rea_plugin.py` **39 项**（假 MCP 服务，不依赖 node / 网络 / 装包）。

### 6.5 让 4B 也能精准调 229 个工具：意图路由（2026-10-17 续）

6.4 那版只给了三个入口（status / tools / call），能用，但**逼着 4B 自己记住 229 个英文工具名** ——
实测它会挑错（问「用 rea 打开 notepad 看看格式」，它选了 `open_app`，真把记事本打开了）。
这一版把"选工具"和"拼参数"两件事**从模型手里拿回载体**：

| 入口 | 谁在干活 |
| --- | --- |
| `rea_do` | 模型只说一句中文意图（"反编译 main"、"看这个文件的哈希"），**载体**负责选工具、拼参数、必要时先 `open_binary` 打开目标、报错翻成人话 |
| `rea_help` | 给出某个工具的必填参数与可用参数（中文） |
| `rea_call` | 知道工具名时的精确调用 —— 229 个里任何一个都能调，**没有白名单** |
| `rea_tools` / `rea_status` | 目录检索与健康检查 |

**路由怎么算（确定性，不调模型）**

1. 中文意图 → 英文线索词：词表在 `plugins/rea_tools_zh.py`（纯数据，改词不用碰接线代码）；
2. 线索词命中工具名（+9）、意图里点到工具本名（+14）、命中说明（+2）；
3. **平台家族**矩阵：`.exe`/`.dll` 属 pe，`.apk` 属 android，`.ipa` 属 apple，`.gguf` 属 model……
   家族对上 +4，对不上 **-7**（这条是实测加的：给 .exe 找签名，一开始被派到了 `inspect_apk_signature`）；
4. 需要参数却凑不齐的工具扣分 —— 能直接跑起来的优先。

**参数怎么填**：载体把"目标文件"填进 `path`/`file`/`target`，把"函数名/地址/关键词"填进
`symbol`/`name`/`address`/`pattern`；**填不齐就列出来问**（"要调 X，还缺 selector"），绝不瞎编。
需要"已打开的目标"的工具，载体在调用前自动 `open_binary`（REA 那边目标是会话级的，后续工具不用再给路径）。

**实测准确率**：`tools/test_rea_router.py` 用 20 条真实意图当夹具，**19/19 命中**（第 20 条是"故意选不中"的
冷门需求，不计分）。夹具用的是 `tests/rea_tools_snapshot.json`（229 个工具的名字+说明+schema 快照，126KB），
所以这条自测**不需要装 node、不需要联网**。

**仍然会错的地方（如实记着）**：「看看证书签名」在 `.exe` 上会选通用的 `inspect_signature`，
而不是更专的 `inspect_pe_certificates` —— 能干活，但不是最优。这类"通用 vs 专用"的取舍，
现在的规则是"通用优先（更安全）"，要改就得给每个家族单独写偏好表。

**顺带修的一个真 bug**：`/api/chat` 在模型某一轮什么都没产出时，会把会话里的占位符
`⏳__pending__` 当成回答**原样返回给前端**（界面就一直转圈，而且日志里看不出来 —— 因为它不是空串，
原来那条 `if not answer_final` 的兜底没命中）。现在出口统一过一道 `_answer_sane()`：
空串/占位符/纯符号一律按"没答上"处理，走兜底话术并打一条警告日志。流式接口同样过了这道。

### 6.6 `ebook`：公开电子书采集，边界写死在代码里（2026-10-17）

**为什么值得单独写一节**：这是本仓库第一个把「**不做某些事**」写成代码、而不是写进提示词的插件。

用户实测「帮我爬一下电子书 <网址> 下载到 G:\dzs」，中断在"截图连错两次"。修完链子之后，
真问题浮出来：那家站的文件在登录+付费墙后面（内页 10023 字符正文里**文件直链 0 个**，
"登录/VIP/会员/付费"出现 32 次）。于是这个插件把三件事**写死在实现里**：

| 边界 | 代码里怎么落 | 为什么不靠提示词 |
| --- | --- | --- |
| 不碰付费墙 | `_paywall_hits()` 扫去标签后的正文，命中 `vip/会员/付费/购买/登录后/扫码/提取码…` → 该条目跳过 | 提示词只是"请求它自觉"，4B 完全可能照下不误 |
| 遵守 robots | `_robots_ok()` 用 `urllib.robotparser`，被 Disallow 就不抓 | 这是站点明确的意愿，不该由模型决定 |
| 只下真文件 | `_magic_ok()` 验文件头（PDF=`%PDF`，EPUB/ZIP=`PK`），不对就删掉 | 常见坑：点了下载，下来的是登录页 HTML，被当成书存进目录 |

另外：单文件上限 40MB、同域名限速 0.6 秒、`limit` 上限 30、http/https 且拒内网（SSRF）。

**怎么验**：`tests/fake_book_site.py` 本地假站把五种情况摆齐（公开条目 / 付费墙条目 /
robots 禁止目录 / 假 PDF / 目录页导航），`tools/test_ebook_plugin.py` **27 项**全绿，
全程不碰真网站 —— 别人的服务器不该被测试流量敲，测试也必须确定性。

完整说明见 [ebook.md](ebook.md)。

## 7. 相关文档

- [工具清单](tools.md)：内置工具与插件工具的实际清单
- [扩展开发](extend.md)：改人设、加模型、把已有系统包成工具
- [资产测绘插件](asset-intel.md)：一个完整插件的用法说明
- [公开电子书采集](ebook.md)：目录 → 条目 → 文件，含付费墙/robots/文件头三道闸

## 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-14 | v1.0 | 重写：对齐代码 + 统一文风 |
