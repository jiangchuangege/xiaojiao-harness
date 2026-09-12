# 架构与设计 · Architecture

> 本文档深入剖析「小焦」的技术选型、模块划分与关键设计决策。

---

## 1. 系统定位

「小焦」不是要复刻一个大模型，而是要回答一个问题：

> **如何用一台消费级设备，拥有一个会持续进化的中文对话伙伴？**

答案是——**蒸馏 + 小模型 + 记忆/插件**。系统把“大脑”拆成两层：

| 层 | 角色 | 负责 |
| --- | --- | --- |
| **大模型（老师）** | 本地 LLM（llama.cpp） | 按时生成高质量对话 / QA，充当教师信号 |
| **小模型（学生）** | 字符级 MiniGPT | 离线推理、低成本运行，承载可沉淀的人格与知识 |

“老师”负责“想得深”，“学生”负责“答得快、记得住”。训练数据由老师蒸馏而来，学生的能力随蒸馏不断成长。

---

## 2. 模块划分

```
xiaojiao-harness/
├── 数据层
│   └── convert.py / clean_data.py / validator.py   → 语料清洗
├── 蒸馏层
│   ├── massive_distill.py       → 主题批量蒸馏
│   ├── distill_and_train.py     → 知识库 QA 蒸馏
│   └── auto_distill_loop.py     → 无间循环驱动
├── 训练层
│   └── train_model.py           → 接力训练器
├── 推理层
│   └── xiaojiao_harness.py      → 交互入口 + MiniGPT + 记忆
├── 扩展层
│   └── plugins/                 → memory / search / weather
└── 观测层
    └── web_monitor.py           → Flask 实时看板
```

各层之间通过**文件**作为边界，松耦合、易替换：

| 边界文件 | 生产者 | 消费者 |
| --- | --- | --- |
| `training_data_pool.txt` | convert.py / 蒸馏器 | train_model.py |
| `vocab.pkl` | train_model.py | xiaojiao_harness.py |
| `mini_gpt_model.pth` | train_model.py | xiaojiao_harness.py |
| `xiaojiao_memory.txt` | 记忆插件 / 用户 | xiaojiao_harness.py |

---

## 3. 核心模型：MiniGPT

### 3.1 网络结构

一个**纯 `TransformerDecoder`** 的因果语言模型，即 GPT-1 时代的经典结构，去掉了 encoder 与 cross-attention，专攻自回归生成：

```text
input_ids ──► TokenEmbedding ──┐
                              ├──► X
pos_ids ───► PosEmbedding ────┘
                             │
                        ┌────▼─────┐
                        │  Decoder │ × N   (self-attention, norm_first)
                        └────┬─────┘
                             ▼
                          Linear → vocab logits
```

关键实现点在 [`xiaojiao_harness.py`](../xiaojiao_harness.py) 与 [`train_model.py`](../train_model.py) 中保持一致，二者共用一个 `MiniGPT` 定义。

### 3.2 为什么用「字符级」tokenize

- 中文没有天然词空格，BPE / wordpiece 需要额外的分词器与子词词表。
- 字符级直接用 `char2idx`，词表即“所有出现过的字”，常仅几百到几千个 token。
- 带来两个好处：**embedding 极小**、**推理生成可逐字中文输出**，对中文最友好。
- 代价是序列更长，但配合 `SEQ_LEN=64` 与自回归训练，对陪伴式短对话完全足够。

### 3.3 Pre-Norm（`norm_first=True`）

Transformer 中 LayerNorm 前置（Pre-LN）比后置（Post-LN）更稳定，尤其在小模型 + 较长序列时几乎成为标配。`nn.TransformerDecoderLayer` 直接暴露该选项，无需手写。

---

## 4. 架构自动推断：权重即 Config

一个小模型最怕“config 与权重对不上”。小焦的做法是**让权重自己描述自己**：

| 超参 | 推断来源 |
| --- | --- |
| `vocab_size` | `vocab["vocab_size"]`（词表文件） |
| `embed_size` | `state_dict["embedding.weight"].shape[1]` |
| `num_layers` | 出现过的最大 `layers.N.*` 层索引 `+1` |
| `hidden_size` | `state_dict["layers.0.linear1.weight"].shape[0]` |
| `num_heads` | `(in_proj.shape[0] // 3) // (embed_size // 8)` |

这样一份 `.pth` 即便换了超参也能被正确重建，`load_model()` 无需任何外部 JSON 配置文件，鲁棒且自包含。

---

## 5. 推理与记忆

### 5.1 自回归解码

```python
logits = model(tensor)[0, -1, :] / temperature
probs  = softmax(logits, -1)
next   = multinomial(probs, 1)
```

- `temperature=0.6`：在“聪明”与“发散”之间取平衡。
- 重复终止：若最近 3 个采样 token 完全一致，则提前结束，避免“复读机”。
- 取最近 32 个 token 作为上下文窗口（`input_ids[-32:]`），兼顾记忆与速度。

### 5.2 检索式记忆

`search_memory()` 用**字符集合交集大小**给每条历史记忆打分：

```python
score = len(set(query) & set(memory_line))
```

取最高分的一条拼进 prompt。这是朴素但有效的“相关度”近似：当没有向量检索基础设施时，用**字符集合重合度**做零依赖的相关性检索。

---

## 6. 插件系统

插件遵循统一的**工具描述 + 分发执行**接口：

```python
class XXPlugin:
    def get_tool_descriptions(self):  # 返回 [{name, description, parameters}]
    def execute(self, tool_name, params):  # 按 name 分发并返回结果
```

### 6.1 工具选择四层架构（防止"调错工具 / 乱试 / 漏调"）

模型"选工具不稳"是这套系统最容易翻车的地方，因此**从描述到路由到纠错**分四层兜：

| 层 | 位置 | 做什么 |
| --- | --- | --- |
| ① 描述场景化 | 各插件的 `get_tool_descriptions()` | 每条描述必须写「**什么时候用** + 输入 + 输出」；同类工具写明区别（`get` 静态首选 → `fetch` 要渲染 → `stealthy_fetch` 被风控才用；`net_ip` 查自己 vs `get_ip_info` 查指定 IP） |
| ② 决策树 | `xiaojiao_app._TOOL_RULES` | 【工具选择顺序】八条：网址→抓取链 ｜ 信息→web_search ｜ 画图→archify 工作流 ｜ 漏洞→collect_vulnerabilities ｜ IP→net_ip ｜ 纯聊天→不调工具 ｜ 命令文件 ｜ 兜底 web_search |
| ③ 关键词路由 | `agent_run` 入口（规则先于模型） | `_looks_like_url()` / `_asks_diagram()` / `detect_vulnerability_query()` / `_asks_net_ip()` 命中就**跳过模型判断**直连工具；画图任务按意图**收窄本轮工具**（只给 `archify_*`） |
| ④ 调错修正 | 工具循环 | 候选表 `_TOOL_FALLBACK`（`get`→`fetch`→`stealthy_fetch`…）+ `_scrape_failed()` 抓取自动升级 + 同一工具**连续 2 次调错即停止**并如实报错；多步链路有 240 秒时间预算 |

**重名防呆**：工具名必须全局唯一。`_build_tools()` 里内置优先、插件重名**跳过并告警** ——
重名会让 `_TOOL2PLUGIN` 路由表被覆盖，模型以为调 A 实际执行 B（"调错工具"的典型成因）。

这种形态与主流 Agent tool-calling 的 schema 一致，便于未来扩展为真正的“可调用工具”。当前内置：

- `memory.py` → `save_memory` / `read_memory`（持久化到 `xiaojiao_memory.txt`）
- `search.py` → `web_search`（打开百度搜索）
- `weather.py` → `get_weather`（wttr.in 天气）
- 🕷️ `scrapling_bridge.py` → **内置 Scrapling 抓取，18 个工具**（原生 13 个 1:1：make_request/bulk_get/fetch/bulk_fetch/stealthy_fetch/bulk_stealthy_fetch/open_session/open_request_session/close_session/list_sessions/session_fetch/session_make_request/screenshot ＋ 4 个增强：get/scrape_with_selector/download/collect_vulnerabilities ＋ 兼容入口 browser_session）→ 详见 [scrapling.md](scrapling.md)

### 6.1 🕷️ 抓取插件（scrapling_bridge）架构要点

```mermaid
%%{init: {"themeVariables": {"fontSize": "14px"}, "flowchart": {"htmlLabels": true, "wrappingWidth": 320, "nodeSpacing": 46, "rankSpacing": 64, "useMaxWidth": true}}}%%
flowchart LR
    REQ["用户：抓一下 xxx / 下载任意文件"] --> DI["抓取意图识别<br/>_detect_scrape_intent()"]
    DI --> SEC["SecurityGuard<br/>SSRF·robots·限速·UA"]
    SEC --> CB["CircuitBreaker<br/>3 次失败→暂停 30s"]
    CB --> BM["BatchManager<br/>去重·429退避·代理轮换·隔离"]
    BM --> AR["AsyncRunner<br/>专用事件循环线程"]
    AR --> MC["MCPClient<br/>连接池·健康检查·重连·超时"]
    MC --> IP["inproc 直连（默认）"]
    MC --> MP["MCP stdio / http"]
    IP --> OUT["正文 / books/*.md / downloads/*.epub / 截图"]
    MP --> OUT
    OUT --> EX["_explain_content() 📖 解读"]
    OUT --> LE["_learn_skill() 🧠 经验沉淀 → 向量库"]
    LE -. 检索复用 .-> DI

    classDef s fill:#eef2ff,stroke:#6366f1,color:#312e81;
    classDef o fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    class REQ,DI,SEC,CB,BM,AR,MC,IP,MP s;
    class OUT,EX,LE o;
```

**与插件接口的关系**：`ScraplingBridge` 仍遵循上表的 `get_tool_descriptions()/execute()` 约定，因此对小焦而言它只是"一个多了 18 个工具的普通插件"——复杂逻辑（异步桥接、安全闸门、熔断、批量、NVD 漏洞聚合、双通道）全部封装在插件内部，对外只暴露简单参数。

**两条关键设计**：
1. **意图直通**（在 `xiaojiao_app.py` 侧）：4B 模型 function-calling 不稳，小焦用规则识别「抓/爬/下载 + 网址」→ 直接构造工具调用（`_detect_scrape_intent`），失败才回落到模型自主调用。
2. **用户使用时学习**：每次使用后把「需求→工具→参数→结果」沉淀进 `self_learn/tool_skills.txt` + 向量库（成功记用法、失败记反思），下次 `_recall_skills()` 命中即注入上下文复用 —— 让小脑**越用越会**。

---

## 7. 真·文生视频（video_service，按需切换）

小焦还具备**本地 AI 文生视频**能力：网页点 🎬 → `video_service` **按需切换模型**（8G 显存互斥）：

```mermaid
%%{init: {"themeVariables": {"fontSize": "14px"}, "flowchart": {"htmlLabels": true, "wrappingWidth": 320, "nodeSpacing": 46, "rankSpacing": 64, "useMaxWidth": true}}}%%
flowchart LR
    subgraph XJ["🐱 小焦 Web (5000)"]
        direction TB
        A["agent_run (对话)"]
        V["🎬 生成视频 video_service"]
    end

    subgraph STEP["⚙️ 按需切换（8G 互斥）"]
        direction TB
        STOP["1 卸载大脑<br/>llama-swap 卸载模型(9292, 秒级)"]
        COMFY["2 启动 ComfyUI(8188)<br/>+ Wan2.1-1.3B-FP8"]
        OUT["3 生成 480p videos/*.mp4"]
        RESTORE["4 温存留内存 / 切第三脑或闲置超时清<br/>聊天上显卡, 视频留内存(9292 加载大脑)"]
    end

    V --> STOP
    V --> COMFY --> OUT
    V --> RESTORE
    RESTORE -->|5 恢复后继续对话| A

    classDef xj fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef stp fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    class A,V xj;
    class STOP,COMFY,OUT,RESTORE stp;
```

**关键**：大脑(LLM) 与 视频(扩散) 不同时占显存——`video_service/model_switch.py` 负责：卸大脑→起 ComfyUI→生成→**温存留内存**（聊天上显卡时不杀它）→切第三个大脑或闲置超15分钟才清。前端显示进度条（后端轮询 ComfyUI `/progress`），状态无锁读取、刷新/多标签不丢进度。

> 详见 [video.md](video.md)。

---

## 7c. 8G 显存管理规则（唯一正解）

```
显存(RUN) 只允许 1 个:  聊天4B / 编码8B / 视频Wan  任一时刻最多一个
内存(WARM) 只允许 1 个: 上次用的大脑(切回秒级), 被新的顶掉就彻底卸载
切换 X:  顶掉旧WARM(卸载) -> 当前RUN去WARM(温存) -> X上显存
同时用:  生成前自动卸载另一个llama(内存守卫), 当前大脑独占显存全速
```

- **谁在用谁全速**（独占显存）。
- **上次用的留内存**（切回快）。
- **防止双模型驻留 OOM**（睡觉真卸载）。

## 7b. Agent 预设 · 大脑监控 · DSH 功能型插件兼容（最新能力）

```mermaid
%%{init: {"themeVariables": {"fontSize": "14px"}, "flowchart": {"htmlLabels": true, "wrappingWidth": 320, "nodeSpacing": 46, "rankSpacing": 64, "useMaxWidth": true}}}%%
flowchart LR
    subgraph XJ["🐱 小焦 Web(5000)"]
        direction TB
        P["🎭 Agent 预设 presets/<br/>人格+大脑+工具开关, 保存即应用"]
        M["🧠 大脑仓库监控 /monitor<br/>状态/显存/切换/调优/添加"]
        COST["💰 成本看板 /cost<br/>今日调用/Token/花费/节省"]
    end

    subgraph OP["⚙️ 配置 / 调度 / 接入"]
        direction TB
        C["xiaojiao_control.json<br/>(合并配置, 热更新不重启)"]
        A["/api/monitor"]
        B["brain_manager.switch_to<br/>llama-swap(9292) + ComfyUI(8188)"]
        D["DSH 功能型插件兼容<br/>_make_tools_plugin<br/>(识别 DSH/OpenAI/Claude tools)"]
        J["_record_usage<br/>写入 cost_daily.json"]
    end

    P --> C
    M --> A
    M -->|操作| B
    XJ -->|/v1| D
    XJ -->|成本记录| J
    COST --> J

    classDef xj fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef op fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    class P,M,COST xj;
    class C,A,B,D,J op;
```

---

## 7d. 🐱 N.E.K.O. 猫娘桌面伙伴

> ⚠️ 猫娘**不是小焦自带的**——它是**你下载的开源 N.E.K.O. 项目**。小焦只是**集成**它：一键启动拉起它的服务、学习它的记忆、给它装"小焦"插件。

一键启动拉起 **N.E.K.O. 猫娘**（你下载的开源项目，Steam **桌面客户端 `N.E.K.O.exe`**）——成熟 Live2D 猫娘壳 + 小焦本地内核，二者互相学习：

```mermaid
%%{init: {"themeVariables": {"fontSize": "14px"}, "flowchart": {"htmlLabels": true, "wrappingWidth": 320, "nodeSpacing": 46, "rankSpacing": 64, "useMaxWidth": true}}}%%
flowchart LR
    ST["▶ start_xiaojiao.start_neko()"]

    subgraph NEKO_SRV["🐱 N.E.K.O. 猫娘（Steam 桌面客户端）"]
        direction TB
        APP["N.E.K.O.exe 桌面客户端<br/>(你看到的猫娘界面)"]
        MS["后端 main_server<br/>(48911) 猫娘后端"]
        MEM["memory_server<br/>(48912) 猫娘记忆"]
        APP --> MS & MEM
    end

    subgraph SRC["📚 猫娘记忆 %LOCALAPPDATA%\\N.E.K.O\\memory\\YUI"]
        direction TB
        FACTS["facts.json<br/>关于主人的事实"]
        PERSONA["persona.json<br/>说话风格"]
    end

    subgraph LEARN_FLOW["🎓 学习通道"]
        direction TB
        LEARN["learn_from_neko.py<br/>--daemon --interval 300"]
    end

    subgraph XJ_MEM["🧡 小焦记忆库"]
        direction TB
        KNOW["xiaojiao_knowledge_memory.json"]
        K2["学会:* · 猫娘说话风格"]
    end

    ST --> APP
    APP --> MS & MEM
    MS --> FACTS & PERSONA
    FACTS & PERSONA --> LEARN
    LEARN --> KNOW --> K2

    classDef st fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef neko fill:#fce7f3,stroke:#f472b6,color:#831843;
    classDef src fill:#f1f5f9,stroke:#94a3b8,color:#1e293b;
    classDef lrn fill:#fef9c3,stroke:#eab308,color:#713f12;
    classDef xj fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    class ST st;
    class APP,MS,MEM neko;
    class FACTS,PERSONA src;
    class LEARN lrn;
    class KNOW,K2 xj;
```

- **记忆来源**：读 N.E.K.O. 猫娘 `facts.json`（关于主人的事实）+ `persona.json`（说话风格）。
- **学进小焦**：写进 `xiaojiao_knowledge_memory.json`，键 `学会:*`（主人事实）与 `猫娘说话风格`。
- **N.E.K.O. 插件**：`%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\`，提供「装小焦」体检 + 安装指引。

> 详细见 [docs/neko.md](neko.md)。`python start_xiaojiao.py` 启动时**先问一句**（`[Y/n]`）再决定是否拉起猫娘；答 `n` 或非交互式环境**不拉也不影响小焦**（`XIAOJIAO_NEKO_AUTO=1` 可免询问）。

- **Agent 预设**：`presets/*.json`（人格+大脑+工具开关），设置页卡片管理，Web 编辑/增删，**保存即应用**（合并配置 + 热更新，不重启）。
- **大脑仓库监控**：`/monitor` 实时看所有大脑状态/显存/内存/任务，直接切换/调优/添加大脑。
- **DSH 功能型插件兼容**：小焦**独立兼容** DSH/OpenAI/Claude 的工具插件——内置"插件万能桥"`_make_tools_plugin` 直接识别它们的 tools 清单，转成小焦的 `py/js/json/skill` 插件，在 5000 端口就能用，**无需装 DSH**。DSH 的界面/皮肤插件则在 DSH 里原生跑（把小焦当模型接入 `/v1`）。

## 8. 观测层：Web 监控

[`web_monitor.py`](../web_monitor.py) 用 Flask 起一个轻量面板，提供：

- `/` —— 渲染看板（知识片段、记忆、训练池、模型、LLM 状态、最近日志）
- `/api/status` —— JSON 状态接口（供前端 `fetch` 轮询，30s 一次）
- `/api/distill` —— 一键触发 `massive_distill.py` 的异步子进程

它让整条“自进化流水线”可被**实时观测与手动干预**。

---

## 8. 设计权衡小结

| 决策 | 取舍 |
| --- | --- |
| 字符级 tokenize | 用更长序列换零依赖中文友好 |
| 纯 Decoder，无 encoder | 为自回归生成而裁剪，极简 |
| 文件作为层间边界 | 模块解耦、可替换，代价是磁盘 I/O |
| 权重推断配置 | 免去 config 同步问题，代价是推导逻辑需维护 |
| 蒸馏自本地 LLM | 免 API 费用、可控，代价是依赖本地算力 |
