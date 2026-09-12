<div align="center">

# 🧡 小焦 · XiaoJiao

<br>

<img src="assets/xiaojiao_cover.jpg" alt="小焦 · 你的本地 AI 猫娘助手" width="480">

<br>

### ✨ 一只会陪你聊天、帮你干活、还能生成视频和播客的本地 AI 助手。

> **不是又一个"套壳聊天"。小焦是一个真的会跑在你电脑上的 AI 伙伴**——能记住你、会找工具干活、能生成真视频和播客，还能**对接开源 N.E.K.O. 猫娘**做桌面伙伴（猫娘是独立开源项目，小焦把它的服务融进一键启动、和你互相学习）。

<br>

`Python` · `PyTorch` · `Flask` · `llama.cpp` · `ComfyUI` · `Chatterbox` · `Diffusers`

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green)]()
[![Local](https://img.shields.io/badge/100%25-local-important)]()
[![AI companion](https://img.shields.io/badge/AI-companion-orange)]()
[![GitHub stars](https://img.shields.io/github/stars/jiangchuangege/xiaojiao-harness)]()
[![GitHub forks](https://img.shields.io/github/forks/jiangchuangege/xiaojiao-harness)]()
[![GitHub release](https://img.shields.io/github/v/release/jiangchuangege/xiaojiao-harness)]()
[![GitHub last commit](https://img.shields.io/github/last-commit/jiangchuangege/xiaojiao-harness)]()
[![GitHub repo size](https://img.shields.io/github/repo-size/jiangchuangege/xiaojiao-harness)]()
[![GitHub issues](https://img.shields.io/github/issues/jiangchuangege/xiaojiao-harness)]()

</div>

---

### 🐱 一句话：小焦能干嘛

> 🧠 多大脑秒切 · 💬 聊天 · 🌐 联网搜索 · 💾 记忆 · 🛠️ 工具干活 · 🎬 生成真视频 · 🎙️ 生成播客 · 🎵 生成音乐 · 🐱 对接 N.E.K.O. 猫娘 · 🔌 插件生态

**它不是让你"打开一个网页聊天"，而是真的住进你电脑的一个 AI 伙伴。** 你自己用本地大模型当它的大脑，给它套上人格、记忆、工具链，再插上**聊天 / 编码 / 视频 / 播客 / 音乐 / 图像**多个脑子，按需秒级切换。你点一下，它就能帮你写文件、跑命令、生成一段真·AI 视频或播客。桌面形象则是**对接开源 N.E.K.O. 猫娘**——小焦把它的服务融进一键启动，让猫娘做陪伴、小焦做大脑，两者会互相学习。

---

## ✨ 它能干嘛

| 分类 | 功能 | 一句话 |
| --- | --- | --- |
| 🧠 | **自研蒸馏小模型** | 大模型蒸馏出属于你自己的小脑（项目核心） |
| 💬 | **聊天** | 像正常人，不会答着答着就背课文 |
| 🌐 | **联网搜索** | 把关键信息揉进回答，不甩一堆网址 |
| 💾 | **记忆自学习** | 你教过它的它会记住，下次能想起来 |
| 🛠️ | **工具调用** | 让你建文件/写网页/跑命令，它真去执行 |
| 🤖 | **多步执行** | 自己推理"建目录→写文件→打开"，分步干活 |
| 🔒 | **危险命令确认** | 碰到 `rm/del/format` 之类，先问你要不要 |
| 🧩 | **四类插件生态** | Python / Node.js / API / 技能，通吃多生态插件 |
| 🔌 | **DSH 社区插件兼容** | 小焦**独立**兼容 DSH 功能型插件（工具清单直接转成小焦插件，无需装 DSH）；界面型插件 DSH 用、小焦当模型 |
| 🧾 | **插件驱动设置模块** | 装了什么插件，设置里就自动出现对应模块（像 DSH） |
| 🔌 | **DeepSeek Harness 接入** | 提供 /v1，可作为 DSH 的模型接入 |
| 🖥️ | **DSH 式网页布局** | 顶栏/侧栏/底栏/设置导航，和 DSH 一致 |
| 💬 | **多会话** | 每个新对话一个会话，左边切换 |
| 🎛️ | **模型管理** | 顶部下拉切模型，设置里增删模型 |
| 🔗 | **OpenAI 兼容** | `/v1` 接口，dsh / 任意客户端能接 |
| 🎬 | **真·文生视频** | 本地 ComfyUI+Wan2.1 真生成（8G按需切换、进度条、刷新不丢） |
| ⚡ | **多大脑·秒级切换** | llama-swap + keep_warm + 低显存(权重RAM↔显存)，聊天/视频/未来大脑秒切、ComfyUI 进程常驻 |
| 🎭 | **Agent 预设** | 一键切换人格+大脑+工具开关（presets/，设置里卡片管理，Web 编辑/增删/保存即应用） |
| 🧠 | **大脑仓库监控面板** | 实时看所有大脑状态/显存/内存，直接切换·调优·添加大脑（`/monitor`，免写码） |
| 🐱 | **对接 N.E.K.O. 猫娘** | 小焦把**开源 N.E.K.O. 猫娘**融进一键启动(48911/48912)，后台学猫娘与主人的对话，猫娘懂小焦、小焦懂猫娘（`docs/neko.md`） |
| 👁️ | **视觉模型接入** | `/api/vision` 拍照识图（Qwen2.5-VL 就绪，设 `XIAOJIAO_VISION_URL` 即可开眼） |
| 💰 | **今日成本看板** | `/cost` 页面实时统计调用次数/本地Token/云端Token/花费/节省，每日清零（`cost_daily.json`） |
| 🛠️ | **装小焦体检** | N.E.K.O. 插件点「🛠️ 装小焦」→ 秒级环境体检 ✅/❌ 逐条显示已装/缺什么 |
| 🔌 | **插件万能桥** | `_make_tools_plugin` 自动把 OpenAI/Claude/DSH tool manifest 转成小焦工具（`plugins/*.json`） |
| 🎙️ | **播客大脑** | `/podcast` 给它一个主题 → 自己写稿+配音+出封面，生成一段真·中文播客（LLM+Chatterbox+SD1.5） |
| 🕷️ | **网页抓取（Scrapling 桥接）** | 抓网页/动态页/绕反爬/批量抓/登录态抓取，9 个工具覆盖 Scrapling 全部能力（`plugins/scrapling_bridge.py`）|
| 📚 | **网页抓取 / 文件下载** | 网页、动态页、接口、批量、登录态都能抓；`download` 一句话把 **PDF/EPUB/TXT/ZIP/图片** 等任意文件下到本地（网页连载可抓正文直接存成文件 `books/`）|
| 📖 | **抓完自动解读** | 抓到内容后大脑按「是什么 / 关键要点 / 怎么用」逐条讲，看不懂的网页也能快速上手 |
| 🧠 | **用户使用时学习** | 每次你让它干活（抓取/下载…）→ 经验沉淀进小脑 + 向量库 → 下次同类需求检索命中直接复用（越用越会）|

---

## 🚀 怎么跑（三行）

```powershell
cd C:\xiaojiao\xiaojiao harness
pip install -r requirements.txt
python start_xiaojiao.py
```

自动起聊天大脑(llama-swap:9292) + 网页(5000) + **N.E.K.O. 猫娘(48911/48912 + 后台学习)**，然后打开 `http://127.0.0.1:5000`。

> 手把手上手看 [docs/quickstart.md](docs/quickstart.md)。

---

## 🖥️ 用法

### 四种入口

| 想干嘛 | 连哪里 | 说明 |
| --- | --- | --- |
| 🖥️ **小焦网页** | `http://127.0.0.1:5000` | 聊天 + 联网 + 记忆 + 工具 + 会话 |
| 🐱 **N.E.K.O. 猫娘** | Steam 桌面客户端 `N.E.K.O.exe`（不是 web 页面；48911/48912 是它的后端端口） | 桌面 Live2D 猫娘伙伴，自动学你与猫娘的对话（一键启动拉起） |
| 🔗 **接 dsh / 客户端** | `http://127.0.0.1:5000/v1` | OpenAI 兼容，自动带上小焦人格 + 工具 |
| 👁️ **视觉识图** | `POST /api/vision` | 截图 → 视觉模型描述（配 XIAOJIAO_VISION_URL） |
| 💰 **成本看板** | `http://127.0.0.1:5000/cost` | 今日调用/本地Token/云端Token/花费/节省 |

### 常用操作

- **切模型**：顶部下拉；或 ⚙️设置 → 模型管理 → 添加。
- **开/关工具**：右上角「🛠️ 工具」（🟢开 / 🔴关）。
- **建会话**：左边「＋ 新对话」；点历史会话切换，右上角「⟨」收起侧边栏。
- **换端口**：`python start_xiaojiao.py --port 8081`，或改 `xiaojiao_control.json` 的 `web_port`。
- **🐱 N.E.K.O. 猫娘（可选，会先问你）**：`python start_xiaojiao.py` 启动时会问「是否启动 N.E.K.O. 猫娘桌面伙伴？[Y/n]」——选 `n` **不影响小焦启动**（两者不绑死）。要免询问直接启动，设环境变量 `XIAOJIAO_NEKO_AUTO=1`。启动后拉起 Steam 桌面客户端（`N.E.K.O.exe`）+ 后端 `main_server(48911)` + `memory_server(48912)`（后端端口，不是网页入口）+ 后台学习通道。
- **🧠 小脑（项目核心，必需）**：小焦的自研蒸馏模型。模型路径**不写死**：优先环境变量 `XIAOJIAO_BRAIN_MODEL` → 配置 `brain.xiaojiao.model_path` → 项目目录探测 → **全盘自动探测**（按关键词找 `*.pth`，模型与词表可跨目录配对、体积大的优先）。想换任意自训模型当小脑，改配置或用环境变量指过去即可。
- **装小焦体检**：N.E.K.O. 插件「装小焦」→ 秒出环境清单 ✅/❌。
- **成本看板**：打开 `http://127.0.0.1:5000/cost` 看今日花费。
- **视觉识图**：设环境变量 `XIAOJIAO_VISION_URL=http://127.0.0.1:8082/v1` 后，拍照识图。

---

## 🏗️ 原理

一句话：**用户消息 → 小焦（注入人设 + 取记忆 + 取会话）→ 交给大脑推理 → 大脑决定调工具/联网 → 执行并回显 → 记忆沉淀 + 会话存 → 回答**。

```mermaid
flowchart LR
    subgraph UI["🖥️ 界面层"]
        direction LR
        W["小焦 Web<br/>(5000)"]
        C["/v1 接口<br/>供 dsh"]
    end

    subgraph CORE["🧡 小焦壳 · 人设 + 记忆 + 编排"]
        direction TB
        R["注入人设 + 真实路径"]
        M["取记忆<br/>xiaojiao_knowledge_memory"]
        S["取会话<br/>最近 N 条"]
        P["插件注册表<br/>随插件动态出现"]
    end

    subgraph BRAIN["🧠 大脑（brain_manager 秒切）"]
        direction TB
        B1["聊天大脑 llama-swap<br/>(9292)"]
        B2["外接 API / 云端"]
        BM["RUN · WARM · OFF"]
    end

    subgraph TOOLS["🛠️ 工具 / 插件生态"]
        direction TB
        T1["内置：命令/读写/打开"]
        T2["Python / Node / API / 技能"]
        T6["联网检索"]
        T7["文生视频<br/>ComfyUI + Wan2.1"]
    end

    NEKO["🐱 N.E.K.O. 猫娘<br/>(48911)"]

    W & C --> R
    R & M & S & P --> BRAIN
    BRAIN --> TOOLS
    TOOLS -->|执行结果| AGENT_RES
    AGENT_RES["💬 回答"]
    NEKO -->|facts / persona| LEARN
    LEARN["learn_from_neko<br/>学猫娘对话"] --> M

    classDef ui fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef core fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    classDef brain fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    classDef tools fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    classDef neko fill:#fce7f3,stroke:#f472b6,color:#831843;
    class W,C ui;
    class R,M,S,P core;
    class B1,B2,BM brain;
    class T1,T2,T6,T7 tools;
    class NEKO,LEARN neko;
```

### 一条消息在内部怎么走

1. **注入人设 + 真实路径**（当前目录/桌面 + 技能插件内容）→ 这就是"小焦人格"生效的原因。

2. **取记忆**：从 `xiaojiao_knowledge_memory.json` 找相关历史知识。

3. **取会话**：拿最近 N 条对话当上下文。

4. **联网（可选）**：Bing/Sogou 抓关键信息注入。

5. **大脑推理**：`brain.engine` 决定用本地 llama / 外接 API。模型用 function-calling 想"要不要调工具、调哪个、参数是啥"。

6. **执行工具**：模型决定"建目录→写文件→打开"，框架逐个执行（内置/插件），显示工具轨迹；危险命令先挂起、等你点「✅ 确认执行」。

7. **记忆沉淀 + 会话存**。

8. **返回**：模型基于工具结果给一句简短总结。

> 更细的实现见 [docs/architecture.md](docs/architecture.md)。

---

## 🧩 插件生态 & DeepSeek Harness 社区插件兼容

小焦的**插件生态**支持四种类型，**装了什么插件，设置里就自动出现对应模块**（像 DSH 那样动态）：

| 类型 | 文件 | 是什么 |
| --- | --- | --- |
| 🐍 Python | `plugins/*.py` | 任意 Python 工具 |
| 🟨 Node.js | `plugins/*.js` | JS 插件（小焦起 node 子进程运行，**Python/JS 双生态兼容**） |
| 🌐 API | `plugins/*.json` | 把任意 HTTP 接口声明成工具 |
| 📄 技能 | `plugins/*.skill.md` | 加进人设的知识/指令 |

### DeepSeek Harness 社区插件兼容（原理 + 图）

小焦对 DeepSeek Harness 社区插件是**两条完全独立**的兼容路径，**互不依赖**：

- **功能型插件（工具/接口/技能）→ 小焦独立兼容**：小焦内置"插件万能桥"（`_make_tools_plugin`），能直接识别 **DSH / OpenAI / Claude** 风格的 tools 清单，把它们的工具功能转成小焦自己的 `py/js/json/skill` 插件，**在 5000 端口就能用，不需要 DSH harness 接入、不需要额外安装**。例如 [dsh-netdoctor 网络诊断](https://github.com/TYEclipse/dsh-netdoctor)这类工具型插件，功能移植进来小焦即可调用。
- **界面型插件（皮肤/UI）→ 走 DSH harness**：这类在 DSH 里原生跑，小焦当模型（`/v1`）供它调用；小焦自己的网页也能复用其素材做主题皮肤。

```mermaid
flowchart LR
    subgraph DSHC["🧩 DSH 社区插件"]
        direction TB
        D1["功能型<br/>工具·技能"]
        D2["界面型<br/>皮肤·UI"]
    end

    subgraph XJ["🐱 小焦 5000"]
        direction TB
        X1["插件万能桥"]
        X2["_make_tools_plugin"]
        X3["→ py/js/json/skill"]
        X1 --> X2 --> X3
    end

    subgraph DSHW["🖥️ DSH harness"]
        direction TB
        DS1["跑界面插件"]
        DS2["小焦当模型 /v1"]
        DS1 --> DS2
    end

    D1 -->|"移植 · 无需 DSH"| X1
    D2 -->|"界面型原生"| DS1

    classDef dshc fill:#f1f5f9,stroke:#94a3b8,color:#1e293b;
    classDef xj fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    classDef dsh fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    class D1,D2 dshc;
    class X1,X2,X3 xj;
    class DS1,DS2 dsh;
```

**一句话**：**功能型 DSH 插件由小焦自己就兼容**（`_make_tools_plugin` 直接识别 DSH/OpenAI/Claude 工具清单，转成小焦插件即可用，**不用装 DSH**）；只有**界面型/皮肤**需要在 DSH 里跑、小焦当模型。

---


## 🧩 玩法（给它加能力）


小焦最值钱的一点：**能力能随便加**。往 `plugins/` 丢一个 `.py`，它就多一个工具。

```python
# plugins/my_time.py
import datetime

class MyTimePlugin:
    def get_tool_descriptions(self):
        return [{"name": "get_time", "description": "看下现在几点",
                 "parameters": {"type": "object", "properties": {}}}]
    def execute(self, name, params):
        if name == "get_time":
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return None
```

放进去 → 重启 → 小焦就能用 `get_time`。

**能加什么**（照抄改一下）：计算器、翻译、查 IP、二维码、汇率、备忘录、定时提醒、读系统信息、生成图片、网页摘要…… 想加多少加多少。

> 光看不练不过瘾？直接去 **[docs/extend.md](docs/extend.md)（玩法大全 · 加工具加插件）**。

---

## 📁 项目结构

**核心三件套**（最常用）：`xiaojiao_app.py`（Web+Agent+人格+工具+记忆+会话+`/v1`）、`start_xiaojiao.py`（一键启动）、`xiaojiao_control.json`（操控文件：人设/大脑/工具/参数/端口）。

**蒸馏训练线**：`convert.py` → `clean_data.py` / `prepare_clean_pool.py` → `massive_distill.py` / `distill_and_train.py` / `auto_distill_loop.py` → `train_model.py` → `xiaojiao_harness.py`（MiniGPT + 推理）。

```
xiaojiao-harness/
├── xiaojiao_app.py               # ★ Web + 人格 + 工具 + 记忆 + 会话 + /v1
├── start_xiaojiao.py             # ★ 一键启动（llama-swap + 大模型 + Web + N.E.K.O. 猫娘）
├── brain_manager.py              # ★ 多大脑·秒级切换调度中心(RUN/WARM/OFF)
├── learn_from_neko.py            # ★ 学 N.E.K.O. 猫娘与主人的对话 → 小焦记忆库
├── xiaojiao_tools.py             # ★ 工具接口(5003)
├── xiaojiao_harness.py           #   自建小模型 (MiniGPT) + 推理
├── train_model.py                #   训练自建小模型
├── massive_distill.py            #   大模型 → 多轮对话蒸馏
├── distill_and_train.py          #   知识库 → 问答对蒸馏
├── auto_distill_loop.py          #   无间蒸馏循环
├── convert.py                    #   LCCC → 训练池
├── clean_data.py                 #   语料清洗
├── prepare_clean_pool.py         #   训练池清洗
├── ai_generate.py                #   手动投喂
├── validator.py                  #   数据校验
├── web_monitor.py                #   蒸馏监控面板
├── plugins/                      #   插件（memory/search/weather/scrapling_bridge/…）
├── downloads/                    #   download 工具下载的文件（PDF/EPUB/TXT/ZIP/图片…任意文件）
├── books/                        #   抓取正文存这里（save_to 参数）
├── docs/                         #   一堆说明
├── xiaojiao_control.json         # ★ 操控文件：人设/大脑/工具/参数/端口
├── xiaojiao_config.json.example
├── requirements.txt
├── .gitignore
├── LICENSE
└── README.md
```

---

## 📚 文档

| 文档 | 干啥的 |
| --- | --- |
| [快速上手](docs/quickstart.md) | 五分钟跑起来 |
| [安装](docs/install.md) | 从零装 |
| [工具说明](docs/tools.md) | 内置工具 + 危险命令 |
| [自研蒸馏小模型](docs/xiaojiao_model.md) | 小模型原理/架构/训练/画图 |
| [玩法大全 · 加工具加插件](docs/extend.md) | 各种能加的能力 |
| [插件](docs/PLUGINS.md) | 写插件指南 |
| [接口](docs/api.md) | OpenAI 兼容接口 |
| [常见问题](docs/faq.md) | 排坑 |
| [模型](docs/model_cn.md) | 模型与壳 |
| [架构](docs/architecture.md) | 实现细节 |
| [持续学习 · 自学习](docs/self_learn.md) | 小脑跟着大脑学（自动记录/打勾/学习）|
| [训练管线](docs/pipeline.md) | 怎么训练小模型 |
| [播客大脑](docs/podcast.md) | 给主题→写稿+配音+封面，生成播客 |
| [N.E.K.O. 猫娘集成](docs/neko.md) | 桌面 Live2D 猫娘伙伴，学猫娘与主人的对话 |
| [多脑秒切](docs/brain-switch.md) | 聊天/视频/播客/图像 大脑按需切换 |
| [内置 Scrapling 抓取](docs/scrapling.md) | **小焦内置 Scrapling**：9 工具（网页/接口/批量/登录态/下载任意文件）/原理/配置/安全边界/架构图 |
| [依赖检测逻辑](docs/dependency-check.md) | 模型/依赖为何按"协议连通"检测 |
| [更新记录](CHANGELOG.md) | 每个版本改了什么 |
| [行为准则](CODE_OF_CONDUCT.md) | 社区友好共识 |

---

## 🧰 能当什么用

| 场景 | 怎么用 |
| --- | --- |
| 🤖 **私人 AI 助理** | 帮你查资料、记事情、提醒、总结，都在本地 |
| 💻 **编程助手** | 让它写代码、改文件、跑命令、建项目结构 |
| ✍️ **写作 / 翻译** | 写文案、润色、中英互译，调个插件就能干 |
| 📚 **RAG 知识库** | 给它喂资料，结合记忆，问啥答啥 |
| ⚙️ **自动化脚本** | 用 `xiaojiao_tools.py` 的 `/api/run`，后台直接调工具干活 |
| 🧩 **接进工作流** | `/v1` 是 OpenAI 兼容的，套进任何 Agent 框架 / 机器人 |

---

## 🧠 自研蒸馏小模型（项目的核心）

小焦真正自己造的，是这套**知识蒸馏出属于你的小模型**的管线。思路很直接：用一个"聪明的老师"（本地大模型）生成对话和知识，然后**蒸馏**成一个**你自己的小模型**（`MiniGPT`，字符级 + 因果 Transformer）。这就叫"**把大模型的脑子，提炼成你自己的小脑**"。

### 蒸馏管线（大模型 → 小模型）

```mermaid
flowchart LR
    subgraph src["📚 数据源"]
        direction LR
        A["LCCC 语料"] --> B["convert / clean_data<br/>→ 训练池"]
    end

    subgraph teach["🎓 老师 = 本地大模型（蒸馏）"]
        direction LR
        C["本地大模型"] --> D["按主题生成多轮对话"]
        C --> E["知识库 → QA"]
        D --> F["training_data_pool.txt"]
        E --> F
    end

    subgraph stu["🧠 学生 = 自研小模型"]
        direction LR
        F --> G["train_model.py 训练"]
        G --> H["mini_gpt_model.pth"]
    end

    classDef src fill:#f1f5f9,stroke:#94a3b8,color:#1e293b;
    classDef teach fill:#fef9c3,stroke:#eab308,color:#713f12;
    classDef stu fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    class A,B src;
    class C,D,E,F teach;
    class G,H stu;
```

### 小模型长什么样（MiniGPT）

字符级的自回归 Transformer，数值从 `model_config.json` 读，训练时存下真值、避免猜错：

| 部件 | 数值 |
| --- | --- |
| `vocab_size` | **6305**（字符词表） |
| `embed_size` | **512** |
| `num_heads` | **8** |
| `hidden_size` | **2048** |
| `num_layers` | **8** |
| `seq_len` | **64** |
| 参数量 | ≈ **几千万**（消费级 GPU 能训） |

```mermaid
flowchart TD
    subgraph INP["📥 输入"]
        A["输入字符序列"] --> B["Embedding 查表 → 512 维"]
        B --> C["+ 位置编码"]
    end
    subgraph TRF["🧠 因果 Transformer（8 层）"]
        direction TB
        D["8× TransformerEncoderLayer<br/>d_model=512 · nhead=8 · ff=2048<br/>每层加因果掩码"]
    end
    subgraph OUT["📤 输出"]
        direction TB
        E["输出头 Linear → 6305"]
        F["softmax → 下一个字符概率"]
        G["取最高者拼回去，循环生成"]
    end
    C --> D --> E
    E --> F --> G

    classDef inp fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef trf fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    classDef out fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    class A,B,C inp;
    class D trf;
    class E,F,G out;
```

- **因果掩码**：每个位置只能看前面的字，不能偷看后面——这是早期"输出乱码"的根因，修好后才正常。
- **用 EncoderLayer + mask**，不是 DecoderLayer，避免 cross-attention 没遮罩导致的"上帝视角"。
- **推理带语义检索兜底**：先在训练语料里找最像的历史问答，命中度高就直出（更靠谱），否则才让模型自由生成。

### 原理：为什么"蒸馏"能成？

知识蒸馏的核心，是让**学生**去模仿**老师**的**软输出**（概率分布），而不只是盯着"标准答案"（hard label）。大模型回答时其实是一堆"每个词的概率"，这份**软知识**比"就选对的词"信息量大多了。让学生去拟合这份分布，老师肚子里"不确定但接近"的东西就传过去了。

放到小焦这里：大模型生成了海量对话和 QA，这些就是**老师软知识的载体**。学生（小模型）学的任务是"**给定上文，预测下一个字**"——它一遍遍拟合老师生成的那些数据分布，就把"怎么像人说话/怎么答这类问题"学了个大概。**参数少、学得有限，但足够"像人聊天"**。这就是"大模型蒸馏 → 自研小脑"成立的原因。

### 训练目标（学什么）

训练目标就是**交叉熵**：让模型对"下一个字"的预测概率，尽可能贴近语料里的真实下一个字。因为它是一个字一个字往前接的（**自回归**），所以只要每个位置都预测得准，整句话就顺了。

### 推理（怎么用）

- **字符自回归**：一个个吐字，接成一句话。
- **采样策略**：`temperature=0.8`（别太死板）+ `top_k=50`（只在前 50 个候选中挑）+ `重复惩罚 1.2`（别老重复）。
- **检索兜底**：先到训练语料里找**最像的历史问答**，相似度高就直出（更靠谱、能对上话）；低了才退回模型自由生成。

### 它是怎么被做出来的（从代码看）

把这段流程走一遍，你就知道"做出来"是什么意思：

1. **喂料（主料是 LCCC）** — `convert.py` 读 **LCCC**（中文多轮对话语料，`LCCC-base_train/test/valid.json`），把每一轮"用户 @小焦"拆成一对，去掉中文之间的空格，逐行写成 `用户 <话> 小焦 <话>` 的训练池文本。**LCCC 就是它最基础的"粮食"**。

2. **清洗** — `clean_data.py` / `prepare_clean_pool.py` 用正则（`^用户 .+ 小焦 .+`）过滤不合规行、剔掉垃圾词，得到干净的 `training_data_pool_clean.txt`。

3. **蒸馏再来一勺** — `massive_distill.py` 调本地大模型，按 `日常聊天/Python/角色扮演…` 等 **40+ 主题**生成 3–5 轮对话，追加进训练池；`distill_and_train.py` 把知识库切成问答对。这是"老师喂给学生的菜"。

4. **建词表** — `train_model.py` 的 `build_vocab` 扫描训练池，收集**所有出现的字符**（字符级），得到 `vocab_size=6305` 的词表 `vocab.pkl`。

5. **搭模型** — `MiniGPT`：字符级因果 Transformer，`embed=512, heads=8, hidden=2048, layers=8, seq=64`，**用 `TransformerEncoderLayer` + 因果掩码**（Pre-LN / batch_first），约**几千万参数**。

6. **训练** — `train_model.py`：`LazyTextDataset` 按 `seq_len//2` 步长滑动窗口采样（不吃满内存）；`AdamW`（8-bit 优先）、`CrossEntropyLoss`、`amp` 混合精度 + `梯度累积`；出现 `Loss=NaN` 自动跳过；支持从 `.pth` **断点续训**。

7. **存好** — 每步存 `mini_gpt_model.pth`，并把**真实架构写进 `model_config.json`**（加载不再猜），`vocab.pkl`、`progress.txt` 一并落盘。

8. **推理** — `xiaojiao_harness.py`：先**语义检索**（在训练池里找最像的历史问答，命中高直出），否则用 `temperature + top_k + 重复惩罚` 让模型自由生成。

> 一句话：**LCCC 当主食，大模型蒸馏当加餐，字符级小 Transformer 负责把它们"吃成"自己的说话方式。**

**怎么接入**：把 `xiaojiao_control.json` 的 `brain.engine` 设为 `xiaojiao`，就用这颗自研小脑当大脑；设 `auto`/`llama` 则优先用更大的底座模型（**接入来辅助它**，让答案更好）。

> **这颗蒸馏出来的小模型就是主角**，大模型是接入来帮它的。原理/架构/训练讲得更细、图更多请看 [docs/xiaojiao_model.md](docs/xiaojiao_model.md)；整条数据管线看 [docs/pipeline.md](docs/pipeline.md)。

---

## 🛡️ 安全说明

- **危险命令会拦**：碰到 `rm / del / format / shutdown / reg delete / taskkill /f`，或往系统目录写文件，小焦会先挂起、等你点「✅ 确认执行」才执行。
- **本地离线**：模型、记忆、会话都在你机器上，不上传。
- **插件要自己信得过**：插件的 `execute` 能做的事 = 你代码能做的事，别装来路不明的插件。

---

## 📈 版本记录

| 版本 | 内容 |
| --- | --- |
| **v1.1.0（当前）** | 🕷️ **内置 Scrapling 抓取**（9 工具：网页/动态页/接口/批量/登录态/下载任意文件 + 抓完自动解读 + 用户使用时学习）+ 🧠 **小脑改为必需项**（路径不写死、全盘自动探测）+ 🛠️ 安装器检测分级（必需/可选分离）+ 🐱 猫娘询问式启动 + 一批渲染/加载器/熔断等修复 |
| v1.0.0 | 全新发布：N.E.K.O. 猫娘桌面伙伴集成(一键拉起+后台学对话) + 多大脑秒切(brain_manager/llama-swap) + 视觉/成本/插件全家桶 + 完整文档 |
| *历史开发版本* | v2.3.0(视觉/成本桥接) → v2.2(v2 会话侧栏) → v2.1(历史持久化) → v2.0(function calling) —— 均为 v1.0.0 之前的演进快照，已并入当前版 |

---

## 🎯 更多玩法（进阶）

- **多底座轮换**：在设置里加好几个模型，顶部下拉一键换，人格不变。
- **多工具串着用**：写两个插件，让小焦自己决定"先查天气，再按天气提醒你添衣"。
- **接外部服务**：把你公司/个人网站的接口做成插件，小焦就能用。
- **当命令行**：`xiaojiao_tools.py`(5003) 的 `/api/run`，脚本里 `requests.post` 就能让它跑命令、写文件。
- **给记忆喂料**：往 `xiaojiao_knowledge_memory.json` 塞资料，结合联网，它更懂你。
- **换人格**：改 `xiaojiao_control.json` 的 `role`，想让它是"助手/老师/翻译/猫咪"都行。

---

## ⚙️ 依赖与运行开销

小焦跑起来的家底不多：Python 3.10+，装 `flask`、`requests`、`torch` 几个库。真正吃资源的是那个**底座大模型**（几个 GB 的显存/内存）。壳本身很轻。它**全程本地、离线**，模型、记忆、会话都在你机器上，不往上传。

---

## 🎭 想让它换一种性格？

小焦的"性格"就在 `xiaojiao_control.json` 的 `role` 里。那句"你是小焦……"就是它的人设。你想让它当**老师**、**翻译**、**猫咪**、**私人助理**，就把这句改成你要的样子——它马上换性格，其他（工具、记忆、联网）都不变。改完重启，或直接在网页 ⚙️设置里改。

---

## 🧪 装完怎么确认它好了？

跑起来后，从简单到复杂各试一下，就知道它有没有在状态：

| 试什么 | 期望 |
| --- | --- |
| 网页能开 | `http://127.0.0.1:5000` 显示聊天界面 |
| 问"你是谁" | 它说自己是小焦 |
| 让它联网 | 回答里混进搜到的最新信息 |
| 让它建个文件 | 真在桌面上建了，还打开 |
| 顶部切模型 | 下拉能选、切换不报错 |

---

## 🤝 想一起改 / 加东西？

小焦是开源的，随便 fork。想给它加能力，最省事的就是**写插件**（看 docs/extend.md）；想改壳本身，就改那几个主文件。提 issue / PR 都欢迎。别改坏 `.gitignore` 里那些数据文件就行。

---

## 🗺️ 接下来想做的

- 把底座模型也放到更好下载的地方（去掉现在"模型不在仓库里"的尴尬）。
- 更强的插件模板、更多内置玩法。
- 更好的会话/记忆可视化。
- 接入更多底座模型。

---

## 🔗 如何用 DeepSeek Harness 接入小焦

小焦暴露一个 **OpenAI 兼容接口**（`/v1`），DeepSeek Harness（DSH）可直接把它当**模型**接入，从而用上小焦的人格 + 工具 + 记忆，并在 DSH 里跑它的社区插件。

### 步骤
1. **启动小焦**：`python start_xiaojiao.py`（llama-swap:9292 接管大脑 + Web 5000）；脚本会**先问一句是否同时启动猫娘宠物**（答 `y` 才拉起 N.E.K.O. 48911/48912，答 `n` 或非交互式则不拉，小焦照常启动）。
2. 在 DSH 的 **设置 → 模型** → 添加一个模型提供方：
   - Base URL：`http://127.0.0.1:5000/v1`
   - API Key：留空（本地免鉴权）
   - 模型名：填任意名字即可（如 `xiaojiao`、`deepseek`、`coder`…——小焦 `/v1` 兼容 OpenAI，模型名只是标识，实际用哪颗大脑由小焦配置决定）
3. 在 DSH 里**选这个模型**，就可以用小焦当大脑，跑 DSH 的社区插件 / 工具 / 皮肤。

### 说明
- 小焦 `/v1` 会自动注入"我是小焦"人格 + 工具 + 记忆。
- **DSH → 小焦（单向）**：DSH 的社区插件在 DSH 里跑、用小焦当模型（`/v1`）。
- **小焦 → DSH（独立兼容）**：小焦自己也能**直接兼容 DSH 功能型插件**——用内置插件万能桥把 DSH/OpenAI/Claude 的工具清单转成小焦插件（`py/js/json/skill`），在 5000 端口就能用，**不需要装 DSH**。
- 小焦**自身也有插件生态**（Python / Node.js / API / 技能），可独立使用。

> 更细的图文见 [docs/dsh-integration.md](docs/dsh-integration.md)。

---

## 🧠 持续学习（小脑跟着大脑学）

**别人靠算力，小脑靠文本。** 小焦内置「持续学习」：每次对话**自动记录**，点 👍/被更正就**自动把这条"功能用法"写进小脑知识库**（越长越强）——小脑检索命中即可复用，越来越强，且不靠算力。

- **自动记录**：答完自动写 `logs/chat_history.jsonl`。
- **自动打勾**：每条回复带 👍/👎，点一下即反馈。
- **自动学习**：被赞/高星/被更正 → 写进 `self_learn/little_brain_knowledge.txt` + 检索池。
- **向量数据库**：`self_learn/vstore.py`（零依赖 Embedding+余弦，对标 Chroma/FAISS）——学到的**功能用法/反思**向量化入 `knowledge_vec.json`，小脑检索**先向量命中即复用**，比字符检索更准。
- **反思机制**：用户 👎/更正 → 生成"为什么没答好/下次怎么改"的反思 → 存知识库 + 向量库（小脑越用越强）。
- **能重训**：数据够了 `train_model.py` 让**小模型本身**也吸收（备份+验证+回退）。

> 原理/图/如何优化 详见 [docs/self_learn.md](docs/self_learn.md)。

---


## 🗺️ 文件 / 模型 互相调用一览

```mermaid
flowchart LR
    subgraph INPUT["🧑‍💻 用户 / 接入"]
        direction LR
        U["用户"]
        DSH["DeepSeek Harness"]
    end

    subgraph WEB["🖥️ 小焦 Web (5000)"]
        direction TB
        W["xiaojiao_app.py"]
        A["agent_run"]
        COST_PAGE["💰 /cost 看板"]
    end

    subgraph AGENT["🧡 小焦壳"]
        direction TB
        M["💰 记忆 recall<br/>xiaojiao_knowledge_memory"]
        S["🌐 联网 search"]
        TOOLS["🔧 工具 + 插件<br/>run_command/write_file<br/>py·js·api·skill"]
    end

    subgraph BRAIN["🧠 大脑（选一个 · 可插拔）"]
        direction TB
        BIG["本地大模型 ~4B 起<br/>llama-swap:9292 / 任意 GGUF"]
        CLOUD["云端 API<br/>任意 OpenAI 兼容模型"]
        SMALL["自研小脑 MiniGPT<br/>检索 + 生成"]
    end

    subgraph GEN["🎬 生成大脑（按需切换 · 8G 互斥）"]
        direction TB
        VID["video_service<br/>卸大脑 → ComfyUI → 恢复"]
        COMFY["ComfyUI(8188) + Wan2.1-FP8"]
        OUTV["videos/*.mp4 真视频"]
    end

    subgraph LEARN["🎓 持续学习"]
        direction TB
        LOG["logs/chat_history.jsonl"]
        FB["logs/feedback.jsonl"]
        KNOW["little_brain_knowledge.txt"]
        TRAIN["train_model.py 重训"]
    end

    subgraph SCRAPE["🕷️ 抓取插件 scrapling_bridge（9 工具）"]
        direction TB
        SD["抓取意图识别 → 安全闸门<br/>SSRF·robots·限速·熔断·批量策略"]
        SDUAL["双通道<br/>inproc 直连 / MCP(stdio·http)"]
        SOUT["产出<br/>正文+📖解读 · books/*.md<br/>downloads/*.epub · 网页截图"]
        SD --> SDUAL --> SOUT
    end

    NEKO["🐱 N.E.K.O. 猫娘<br/>(48911/48912)"]
    ST["▶ start_xiaojiao.py"]
    DSHPLUG["🔌 DSH 功能型插件<br/>工具/接口/技能"]

    U --> W
    DSH -->|/v1| W
    W --> A
    A --> M & S
    A --> BRAIN
    BRAIN --> TOOLS
    TOOLS -->|执行结果| A
    W -->|🎬| VID
    VID --> COMFY --> OUTV
    W -->|自动记录| LOG
    LOG --> FB --> KNOW --> TRAIN
    ST --> W & BIG
    ST --> NEKO
    NEKO -->|每5分钟| KNOW
    DSHPLUG -->|插件万能桥<br/>_make_tools_plugin| TOOLS
    TOOLS <-->|"抓取 / 下载 / 截图"| SD
    SOUT -->|"📖 解读"| A
    SOUT -->|"经验沉淀(成功=用法/失败=反思)"| KNOW
    W --> COST_PAGE

    classDef in fill:#f1f5f9,stroke:#94a3b8,color:#1e293b;
    classDef web fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef ag fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    classDef br fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    classDef gen fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    classDef lrn fill:#fef9c3,stroke:#eab308,color:#713f12;
    classDef neko fill:#fce7f3,stroke:#f472b6,color:#831843;
    classDef scr fill:#eef2ff,stroke:#6366f1,color:#312e81;
    class U,DSH,DSHPLUG in;
    class W,A,COST_PAGE web;
    class M,S,TOOLS ag;
    class BIG,CLOUD,SMALL br;
    class VID,COMFY,OUTV gen;
    class LOG,FB,KNOW,TRAIN lrn;
    class NEKO,ST,BR neko;
    class SD,SDUAL,SOUT scr;
```

**调用关系一句话**：用户/DSH → 小焦 Web(`/v1`) → agent_run → 选大脑（大模型/小模型）→ 工具执行；点 🎬 → video_service **按需切换**（卸大脑→ComfyUI+Wan2.1 生成→恢复大脑）出真视频；要抓资料/下文件 → **内置 Scrapling**（意图识别→安全闸门→双通道抓取→正文+解读/存文件），抓完的经验还会**沉淀进小脑**，下次同类需求直接复用；小焦顺便**自动记录**交互 → 点赞/更正进**小脑知识库** → 学习引擎重训 → 越来越强。`start_xiaojiao.py` 一键拉起大模型 + Web + N.E.K.O. 猫娘。

---


## 🤝 想一起把它变得更好？

小焦是开源的，也**欢迎你来一起开发**：插件生态(py/js/api/skill)、持续学习、DSH 社区接入、皮肤、训练管线……任何一个你感兴趣的方向，都可以来贡献。

- **🚀 快速上手**：看 [docs/quickstart.md](docs/quickstart.md) 五分钟跑起来；
- **🐛 报 Bug**：开 [Bug Issue](.github/ISSUE_TEMPLATE/bug_report.yml)，按模板写现象/环境/日志；
- **✨ 提功能**：开 [Feature Issue](.github/ISSUE_TEMPLATE/feature_request.yml)，或直接写个**插件**（`docs/extend.md`）；
- **🔀 交代码**：Fork 后提 [Pull Request](.github/PULL_REQUEST_TEMPLATE.md)；
- **💙 行为规范**：见 [Code of Conduct](CODE_OF_CONDUCT.md)，我们对新人很友好；
- **📝 更新记录**：见 [CHANGELOG.md](CHANGELOG.md)。

> 它正等着，**和愿意陪它长大的人一起，慢慢长大**。✨

---

## 🎬 真·文生视频（本地 ComfyUI + Wan2.1）

小焦网页里有 **🎬 生成视频**：点它输入场景 → **精炼提示词 → 切换视频大脑(智能温存) → 生成真视频 → 温存15分钟(连续视频秒级)/闲置自动释放**（8G 显存按需切换，对用户透明）。

- **真 AI 生成**：`video_service/`（ComfyUI + WanVideoWrapper 工作流，480p，约2-3分钟）。
- **实时进度**：网页显示"第X/14步 / Z%"进度条 + 后台任务徽章。
- **中途刷新/换页面也不丢进度**（服务器持久化 + 状态无锁读取）。
- **配置**：ComfyUI 位置、模型名在 `video_service/config.py`（环境变量 `XIAOJIAO_COMFY_DIR` 等）。

> 详见 [docs/video.md](docs/video.md)。

---


## ⚡ 多大脑·秒级切换（原理）

小焦用**多个"大脑"**（聊天 / 视频 / 未来图像·推理），8G 显存下**按需热切换**，互不打架：

```mermaid
flowchart LR
    subgraph XJ["🧠 brain_manager · 调度中心"]
        direction TB
        A["🙋 意图 → 选大脑"]
        B["⚙️ 显存调度<br/>休眠 / 唤醒 / 让位"]
    end

    subgraph BRAINS["🧠 大脑们（8G 互斥）"]
        direction TB
        CHAT["💬 聊天大脑 本地大模型 ~4B 起<br/>llama-swap(9292) 秒级卸载/加载"]
        VID["🎬 视频大脑 ComfyUI+Wan2.1(8188)<br/>keep_warm 常驻 + 低显存"]
        IMG["🖼️ 图像/推理大脑（可扩展）"]
    end

    A --> B
    B -->|switch_to| CHAT
    B -->|switch_to| VID
    B -.->|未来| IMG

    classDef xj fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef br fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    class A,B xj;
    class CHAT,VID,IMG br;
```

**原理**：
- **聊天大脑**：走 **llama-swap**(9292)，进程常驻，切换=**卸载/加载模型**（秒级），不再杀进程重启。
- **视频大脑**：**智能温存**——生成完 ComfyUI+Wan **留在内存**（聊天大脑上显卡时不杀它）；**只有切到"第三个大脑"或闲置超15分钟 → 才自动释放**（省内存，挂更多大脑）；**低显存(--lowvram)** 权重放 RAM、按需加载。
- **调度中心** `brain_manager.py`：注册所有大脑、`switch_to` 休眠当前/唤醒目标，**加新大脑只需在 BRAINS 加一项**。
- 8G 物理上放不下"两个都热"，所以**一个显存、一个内存**，但搬运是**权重级（RAM↔显存）**，不再是"杀进程重启"。

> 详见 [docs/brain-switch.md](docs/brain-switch.md) 与 [docs/tools.md](docs/tools.md)。
>
> 🚀 [docs/upgrade-plan.md](docs/upgrade-plan.md) · 对标 Harness 升级路线图（零门槛安装 / 智能调度省钱 / 插件万能桥）
> 🐱 [docs/neko.md](docs/neko.md) · N.E.K.O. 猫娘桌面伙伴



## 🧠 大脑仓库监控面板

小焦的**多大脑**都能在一个网页里实时盯着并直接操作：看每个大脑的状态/显存/内存/任务，切换·唤醒·释放·重启，**调优 keep_warm/优先级/挂载内存（免写码）**，**添加大脑（选本地模型文件路径）**。

```mermaid
flowchart LR
    A["🧠 监控面板 /monitor"] -->|每 2 秒| B["/api/monitor"]

    subgraph DATA["📊 数据源"]
        direction TB
        C["brain_manager.BRAINS"]
        D["nvidia-smi 显存"]
        E["psutil 内存"]
        F["llama-swap(9292) 聊天脑"]
        G["ComfyUI(8188) 视频脑"]
    end

    B --> C & D & E & F & G

    classDef panel fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef data fill:#f1f5f9,stroke:#94a3b8,color:#1e293b;
    class A panel;
    class C,D,E,F,G data;
```

> 打开 **`http://127.0.0.1:5000/monitor`**。详见 [docs/monitor.md](docs/monitor.md)。

## 🗄️ 一键加模型
设置→模型→「一键加本地GGUF」→填名字/路径/ctx→自动配置(不写代码)。详情见 `docs/coding-brain.md`。

## 🧠 可用哪些模型（最小 → 最大）

小焦的 `/v1` 是 **OpenAI 兼容**的，`brain.engine` 可插拔，**模型不写死**——只要你的模型满足"多轮对话 + 工具调用"就能当小焦的大脑。能选的范围很宽：

| 能力档次 | 用什么 | 最小/最大要求 |
| --- | --- | --- |
| 🐣 **最小可用** | 本地 **~4B** 量级 GGUF（如 Qwen/DeepSeek 系小模型）+ llama.cpp 推理 | 4B 起，**8G 显存**可跑；工具调用略弱但能聊 |
| 🤖 **推荐** | **7B~32B** 大模型（本地 GGUF 或任意 OpenAI 兼容云端 API，如 DeepSeek / Qwen / OpenAI / Claude 兼容端点） | 显存越大越强；工具调用、多步执行更准 |
| 🚀 **最大可用** | **云端超大模型**（通过 `brain.api` 配任意兼容端点，如 DeepSeek/R1、多模态等） | 只要该端点支持 OpenAI `/chat/completions`，**不占本地显存**，能力最强 |
| 🧠 **自研小脑** | `brain.engine = xiaojiao` 用蒸馏出的 MiniGPT | 最轻、离线，作为兜底/陪聊 |

> **规则**：`/v1` 兼容 OpenAI —— 你可以把**任意**兼容模型的 `base_url` + `model` 填进小焦配置即可接入，不必是特定型号。本地推理用 llama.cpp/llama-swap 托管，云端走 `brain.api`。想换哪个大模型，改配置就行，人格/工具/记忆都不变。


## 🐱 N.E.K.O. 猫娘桌面伙伴（MVP）

> **说明**：猫娘**不是小焦自带的**——它是**独立开源项目 N.E.K.O.**，你**本地部署**（目录如 `G:\moxing__xiaojiao\maoniang\N.E.K.O-main`，含 `launcher.py`）。小焦只是**集成它**：一键启动时把本地的 N.E.K.O. 猫娘服务拉起，并后台学习它与你对话的记忆。猫娘负责桌面陪伴（形象/动效/语音），小焦负责本地大脑/工具，两者互相学习。

一键启动后，小焦会把**你本地部署的 N.E.K.O. 猫娘**服务拉起——成熟 Live2D 猫娘壳 + 小焦本地内核，两者互相学习：

**猫娘 ↔ 小焦 互通原理图**：

```mermaid
flowchart LR
    subgraph NEKO["🐱 N.E.K.O. 猫娘（你本地开源项目，Steam 桌面客户端）"]
        direction TB
        APP["N.E.K.O.exe 桌面客户端<br/>(你看到的界面)"]
        MS["后端 main_server (48911)"]
        MEM["memory_server (48912)"]
        PLUG["插件系统 xiaojiao_install"]
        APP --> MS & MEM
    end

    subgraph LEARN["🎓 学习通道"]
        direction TB
        LF["learn_from_neko.py<br/>(每5分钟/手动)"]
    end

    subgraph XJ["🧡 小焦（本地大脑 + 工具）"]
        direction TB
        KNOW["小焦记忆库<br/>xiaojiao_knowledge_memory.json"]
        BRAIN["大脑 + 工具 + 记忆 + 人格"]
        GEN["生成视频/播客/音乐<br/>桌面猫娘"]
    end

    MS -->|"facts.json / persona.json"| LF
    MEM --> LF
    LF -->|"学会:* / 猫娘说话风格"| KNOW
    KNOW --> BRAIN
    BRAIN --> GEN
    PLUG -->|"装小焦体检/指引<br/>调 /api/env"| BRAIN

    classDef neko fill:#fce7f3,stroke:#f472b6,color:#831843;
    classDef lrn fill:#fef9c3,stroke:#eab308,color:#713f12;
    classDef xj fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    class APP,MS,MEM,PLUG neko;
    class LF lrn;
    class KNOW,BRAIN,GEN xj;
```

- **一键拉起**：`start_neko()` 拉起**桌面客户端 `N.E.K.O.exe`**（连带后端 48911/48912）+ 后台学习通道。**主入口是桌面客户端，不是 web 页面**。
- **学你与猫娘的对话**：`learn_from_neko.py` 读猫娘 `facts.json`/`persona.json` → 写进小焦记忆（`学会:*` / `猫娘说话风格`）。
- **N.E.K.O. 插件**：`%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\` 提供「装小焦」体检 + 安装指引。

> 详细见 [docs/neko.md](docs/neko.md)。猫娘由 `python start_xiaojiao.py` 在启动时**先询问**再决定是否拉起。


## 🛠️ 一键安装 · 检测分级（v1.1.0）

> 一句话：安装脚本不再"一把抓"——它把 11+ 项检测拆成 **必需** 和 **可选** 两组：**缺可选只会少一个功能，绝不会拦着你进小焦**。所有路径都**靠检测得到，一个都不写死**。

```mermaid
flowchart TB
    START(["双击 一键安装.bat<br/>或 python install_all.py"]) --> SCAN["全盘扫描<br/>关键词 + 盘符探测 + where /r 兜底"]

    SCAN --> REQ{"必需项齐全?"}
    REQ -->|"否"| BLOCK["❌ 列出缺什么 + 怎么补（不继续装）"]
    REQ -->|"是"| OK["✅ 环境就绪，可以启动"]

    subgraph MUST["必需（缺了进不去小焦）"]
        direction TB
        M1["Python 3.13 + requirements.txt 依赖"]
        M2["🧠 小脑 MiniGPT · 项目核心<br/>mini_gpt_model.pth + vocab.pkl + model_config.json"]
    end

    subgraph OPT["可选（缺了只是少个功能）"]
        direction TB
        O1["llama-server.exe（文字大脑）"]
        O2["llama-swap 9292（秒级切换）"]
        O3["ComfyUI（视频大脑）"]
        O4["🐱 猫娘 N.E.K.O. · 启动前先问 y/N"]
        O5["Scrapling 抓取栈（网页/接口/文件）"]
    end

    MUST --> OK
    OPT -.->|"不影响启动"| OK
    OK --> RUN["python start_xiaojiao.py → 小焦上线 :5000"]

    classDef req fill:#fee2e2,stroke:#ef4444,color:#7f1d1d;
    classDef opt fill:#eef2ff,stroke:#6366f1,color:#312e81;
    classDef go fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    class M1,M2,BLOCK req;
    class O1,O2,O3,O4,O5 opt;
    class OK,RUN go;
```

**必需项**（缺了小焦起不来 / 只剩空壳）

| 必需项 | 为什么必需 | 检测方式（不写死路径） |
|---|---|---|
| Python 3.13 + 依赖包 | 运行本体 | 探测解释器 + 逐个 `import` 验证，缺啥点名 |
| 🧠 **小脑 MiniGPT** | **项目核心**——没有它小焦不会"想" | `discover_brain_all()` 全盘找 `*.pth`（体积优先）+ 跨目录配 `vocab*.pkl` |

**可选项**（缺了对应功能静默降级，不挡启动）

| 可选项 | 缺了会怎样 |
|---|---|
| `llama-server.exe` | 文字大脑不可用（仍可用小脑/API 模型） |
| `llama-swap` | 没有秒级切换，一次只挂一颗模型 |
| ComfyUI | 视频生成不可用（其余功能正常） |
| 🐱 猫娘 N.E.K.O. | 没有桌面宠物（**先询问，答 n 照常启动**） |
| Scrapling 抓取栈 | 网页抓取 / 文件下载不可用 |

### 🧠 小脑（必需 · 项目核心 · 三种模型都能当）

- 小脑是**唯一"必需"的 AI 组件**：它是小焦的"直觉层"，负责秒回、情绪、轻量判断。
- **什么模型都能当小脑**：只要是 `*.pth + vocab*.pkl + model_config.json` 三件套，在 `xiaojiao_control.json` 里改 `brain.xiaojiao` 指向即可，**代码一行不动**。
- **路径三级解析，永不写死**（`xiaojiao_harness.py → _resolve_brain_paths()`）：

```mermaid
flowchart LR
    A["① 环境变量<br/>XIAOJIAO_BRAIN_MODEL 等"] --> B["② xiaojiao_control.json<br/>brain.xiaojiao.model_path / vocab_path / config_path"]
    B --> C["③ 全盘 glob<br/>*.pth + vocab*.pkl 就近配对"]
    C --> D["加载 → 📊 词表 6305 · embed=512 heads=8 layers=8"]

    classDef s fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef d fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    class A,B,C s;
    class D d;
```

### 🐱 猫娘（可选 · 启动前先问，绝不绑死）

`start_xiaojiao.py → ask_start_neko()` 在启动时问一句 `是否同时启动猫娘宠物？ [Y/n]`：

- 答 `y` → 拉起 `N.E.K.O.exe`（连带后端 48911/48912）+ 后台学习通道；
- 答 `n`、直接回车（默认 N）、或**非交互式环境**（脚本 / CI / 无终端）→ **不拉**，小焦照常启动；
- 环境变量 `XIAOJIAO_NEKO_AUTO=1` → 跳过询问直接自动拉起。

> **猫娘和小焦互不依赖**：没有猫娘，小焦一切正常；没有小焦，猫娘照跑。想彻底分开，就答 `n`。

### 🗺️ v1.1.0 改动全景（一张图看完所有改过的文件与原则）

```mermaid
flowchart TB
    P["🧭 9 条原则（贯穿全部改动）<br/>① 不写死路径 · ② 自己找模型 · ③ 必需/可选分级<br/>④ 先问再拉 · ⑤ 抓完必解读 · ⑥ 用完即学习<br/>⑦ 安全第一 · ⑧ 报错说人话 · ⑨ 边界守规矩"]

    subgraph CHG["🔧 v1.1.0 改动文件"]
        direction TB
        F1["install_all.py<br/>分级检测 · 全盘找小脑 · 缺可选不拦启动"]
        F2["start_xiaojiao.py<br/>ask_start_neko() 猫娘先问 y/N"]
        F3["xiaojiao_harness.py<br/>_resolve_brain_paths() 三级解析模型"]
        F4["xiaojiao_app.py<br/>抓取直通 · 正文直显 · 📖解读 · 自动大纲 · 学习落盘"]
        F5["plugins/scrapling_bridge.py<br/>9 工具 / 13 条 MCP 通道 · 安全闸门 · 熔断 · 批量"]
        F6["xiaojiao_control.json<br/>brain.xiaojiao + scrapling 两段"]
        F7["requirements.txt<br/>scrapling[fetchers] · markdownify · mcp"]
        F8["README.md · docs/scrapling.md<br/>docs/architecture.md · CHANGELOG.md · docs/install.md"]
        F9["video_service/config.py · podcast_service/podcast_gen.py<br/>视频/配音模型目录自动探测（不写死）"]
        F10["玩具体检 · 插件 Chrome 探测<br/>xiaojiao_app.py /api/env + scrapling_bridge.py"]
    end

    P --> CHG
    CHG --> V["✅ 验收<br/>9 工具可调 · 抓完有解读 · 模型不写死<br/>猫娘不绑死 · 缺可选不影响启动 · 一次装完就能跑"]

    classDef p fill:#fef9c3,stroke:#eab308,color:#713f12;
    classDef f fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef v fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    class P p;
    class F1,F2,F3,F4,F5,F6,F7,F8,F9,F10 f;
    class V v;
```

| 原则 | 落在哪个文件 / 哪个函数 |
|---|---|
| ① 不写死路径 | `install_all.py` 全盘发现 · `xiaojiao_harness.py::_resolve_brain_paths()` · `xiaojiao_app.py::_discover_paths()`（ComfyUI/llama-swap/视频模型）· `video_service/config.py` · `podcast_service/podcast_gen.py` · `plugins/scrapling_bridge.py`（Chrome）· `xiaojiao_control.json` |
| ② 自己找模型 | `discover_brain_all()`（`*.pth` 体积优先 + 跨目录 `vocab*.pkl`） |
| ③ 必需/可选分级 | `install_all.py` 的 `missing` / `opt_miss` 两组 + 报告分段 |
| ④ 先问再拉 | `start_xiaojiao.py::ask_start_neko()`（`[Y/n]`，非交互默认不拉） |
| ⑤ 抓完必解读 | `xiaojiao_app.py::_explain_content()` + 抓取意图直通 `_detect_scrape_intent()` |
| ⑥ 用完即学习 | `_reflect()` / `_learn_skill()` / `_recall_skills()` → `self_learn/tool_skills.txt` |
| ⑦ 安全第一 | `scrapling_bridge.py::SecurityGuard`（SSRF/robots/限速/脱敏）+ `CircuitBreaker` |
| ⑧ 报错说人话 | `_tool_result_str()` 统一出口 + 中文错误 + 降级提示 |
| ⑨ 边界守规矩 | 不绕付费墙 / 不破版权，仅抓公开可访问内容（见下方边界说明） |

**用法**：双击 `一键安装.bat`（或 `python install_all.py`）→ 看报告里的 **必需 / 可选** 两段 → 缺必需按提示补 → 齐了就 `python start_xiaojiao.py`。装完想换小脑或加抓取配置，改 `xiaojiao_control.json` 即可，不用碰代码。


## 🕷️ 内置 Scrapling · 想抓啥抓啥

> 一句话：**小焦内置了 Scrapling**（业界最强的开源抓取库之一）——**网页、动态页、接口 JSON、批量列表、要登录才看得到的页面、任意文件（PDF/EPUB/TXT/ZIP/图片/音视频…）**都能抓能下；抓完**自动解读**，能直接**存成本地文件**，而且**每次使用都会让它更会用**。

### 它能干嘛（9 个工具，覆盖 Scrapling 全部能力）

| 工具 | 一句话 | 对应 Scrapling |
| --- | --- | --- |
| `get` | 抓普通网页（纯 HTTP，最快）| make_request |
| `bulk_get` | 批量抓（去重 / 限速 / 429 退避 / 失败隔离）| bulk_get |
| `fetch` | 抓动态页（Playwright 起浏览器渲染）| fetch |
| `bulk_fetch` | 批量渲染抓取 | bulk_fetch |
| `stealthy_fetch` | 隐身抓取（绕 Cloudflare / 反爬，开销大）| stealthy_fetch |
| `bulk_stealthy_fetch` | 批量隐身抓取（≤20 个）| bulk_stealthy_fetch |
| `scrape_with_selector` | 按 CSS 选择器抓取，**自适应防站点改版** | make_request/fetch + css_selector |
| `browser_session` | 会话管理 + **登录态抓取** + **整页截图** | open_session / open_request_session / close_session / list_sessions / session_fetch / session_make_request / screenshot |
| 🆕 `download` | **下载任意文件**（PDF / EPUB / ZIP / 图片 / 音视频…）到本地 | （插件自研，Scrapling 无此能力）|

> 13 个 Scrapling MCP 工具 → 小焦 **9 个工具全部覆盖**；多出的 `download` 与 `save_to` 是插件自研（Scrapling 只抓网页、不下文件）。

### 一张图看懂它怎么工作（架构 · 数据流）

```mermaid
flowchart TB
    subgraph U["🧑 用户"]
        Q["「抓一下 xxx.com」<br/>「把这个 PDF 下载下来」<br/>「抓这页存成文件」"]
    end

    subgraph APP["🧡 小焦壳 · xiaojiao_app.py"]
        direction TB
        DI["① 抓取意图识别<br/>_detect_scrape_intent()<br/>抓/爬/下载 + 网址 → 自动选工具"]
        WS["② 上下文装配<br/>记忆 + 联网 + 技能检索 _recall_skills()"]
        TR["⑥ 工具轨迹<br/>_trace_summary() 压成一行"]
        EX["⑦ 抓完解读<br/>_explain_content()<br/>是什么 / 要点 / 怎么用"]
        LE["⑧ 用户使用时学习<br/>_learn_skill()"]
    end

    subgraph BR["🕷️ plugins/scrapling_bridge.py"]
        direction TB
        SEC["SecurityGuard<br/>SSRF 100% 拦截 · robots.txt<br/>同域限速 · UA 合规 · 日志脱敏"]
        CB["CircuitBreaker<br/>连续失败 3 次 → 暂停 30s → 自愈"]
        BM["BatchManager<br/>URL 去重 · 429 指数退避<br/>代理轮换(≤5次) · 部分失败隔离"]
        AR["AsyncRunner<br/>专用事件循环线程<br/>（绝不 asyncio.run）"]
        MC["MCPClient<br/>连接池 · 健康检查<br/>30s 自动重连 · 超时取消"]
        SM["SelectorManager<br/>自适应选择器<br/>指纹相似度 + 多候选置信度"]
    end

    subgraph DUAL["🔀 双通道（mode 可切）"]
        IP["inproc 直连<br/>scrapling API（默认，错误信息完整）"]
        MP["MCP 服务<br/>stdio 子进程 / streamable-http"]
    end

    subgraph OUT["📦 产出"]
        R1["正文 Markdown<br/>+ 📖 解读"]
        R2["books/*.md<br/>save_to 存文件"]
        R3["downloads/*.epub<br/>download 下载"]
        R4["media/screenshot/*.png<br/>网页截图"]
    end

    subgraph LR["🧠 小脑（越用越会）"]
        KF["self_learn/tool_skills.txt<br/>成功=用法 · 失败=反思"]
        VEC["self_learn/knowledge_vec.json<br/>向量库（语义检索）"]
    end

    Q --> DI --> SEC
    WS --> SEC
    SEC --> CB --> BM --> AR --> MC
    MC --> IP
    MC --> MP
    MC --> SM
    IP --> R1 & R2 & R3 & R4
    MP --> R1 & R2 & R3 & R4
    R1 --> TR --> EX
    TR --> LE --> KF --> VEC
    VEC -. "检索命中即复用" .-> WS

    classDef u fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef app fill:#fff7ed,stroke:#fb923c,color:#7c2d12;
    classDef br fill:#f3e8ff,stroke:#a78bfa,color:#4c1d95;
    classDef out fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    classDef lrn fill:#fef9c3,stroke:#eab308,color:#713f12;
    class Q u;
    class DI,WS,TR,EX,LE app;
    class SEC,CB,BM,AR,MC,SM,IP,MP br;
    class R1,R2,R3,R4 out;
    class KF,VEC lrn;
```

### 用法（说人话）

| 你对小焦说 | 它做什么 | 存到哪 |
| --- | --- | --- |
| 「抓一下 example.com」 | `get`（最快）| 直接看正文 + 📖 解读 |
| 「抓取 https://a.com 和 https://b.com」 | `bulk_get`（自动去重 + 限速）| 逐项结果 |
| 「用浏览器渲染抓 https://…」 | `fetch`（动态页面）| — |
| 「用 stealthy_fetch 抓 https://…」 | `stealthy_fetch`（绕 Cloudflare）| — |
| 「抓这章存成 mybook.md」 | `get` + `save_to` | `books/mybook.md` |
| 「把这个 PDF/ZIP 下载下来 https://…」 | 🆕 `download`（任意文件都能下）| `downloads/xxx.pdf` |
| 「开个会话，登录后抓 https://…」 | `browser_session`（登录态）| — |
| 「给 https://… 截个整页图」 | `browser_session` screenshot | `media/screenshot/*.png` |

抓完会自动附上 **📖 小焦解读**：
```
🌐 https://example.com · HTTP 200

# Example Domain
This domain is for use in documentation examples…

──────────────
📖 小焦解读
这是一个用于文档示例的占位域名页面，本身不提供实际功能。
· 它仅用于演示和文档说明，不具备任何真实服务或数据。
· 域名由 IANA 专门保留，用于技术文档、教程和示例代码中。
· 页面中唯一的可点击链接指向 IANA 官网…
```

### 原理（为什么稳、为什么省心）

1. **抓取意图直通** — 4B 模型自己选工具不稳（会瞎编代码）。小焦用规则识别「抓/爬/下载 + 网址」→ **直接构造工具调用**，说到就做到。
2. **正文直显，不被"总结"吃掉** — 抓取结果不交给小模型复述（它会把正文压成一句），而是**原样展示**，再附解读。
3. **异步桥接** — Flask 是同步线程、Scrapling 是异步 API：用**专用事件循环线程 + 线程池**承接，**绝不 `asyncio.run()`**（避免循环冲突）。超时用 `future.result(timeout)` 强制取消。
4. **双通道** — `inproc` 进程内直连（默认，**错误信息完整**：MCP 出错只回一句笼统说明）｜ `mcp` 走 MCP 服务（stdio / http）。`mode` 可配。
5. **安全闸门** — **SSRF 100% 拦截**（本机/内网/保留地址/file:// 等，DNS 解析后再校验一次）· robots.txt 合规 · 同域 ≤1 请求/秒 · UA 合规（不伪装爬虫）· 日志脱敏（Key/Token/Cookie 一律打码）· 结果只落本地不上传。
6. **批量策略** — URL 去重 → 逐条限速 → 429 指数退避（1→2→4→8s）→ 代理轮换（单代理 ≤5 次）→ **单个失败不影响其它**（逐项标记）。
7. **熔断自愈** — 同一工具连续失败 3 次 → 暂停 30 秒并返回中文提示 → **自动恢复**（绝不永久禁用）。**安全拦截不计入熔断**（那是正常拒绝，不是故障）。
8. **自适应选择器** — 保存时记录标签/class/id/文本/父路径/兄弟位置/属性集合；恢复时加权相似度匹配，**返回全部候选 + 置信度**；元素被删则返回结构化 `not_found`，**绝不返回错误元素**。
9. **内容标准化** — 统一 `{status, url, content, error}`；HTML→Markdown；**Setext 标题转 ATX**（`标题\n====` → `# 标题`，前端才渲染得出标题）；JSON 超长截断；错误一律中文可读、**绝不把 Python 堆栈丢给模型**。

### 🧠 用户使用时学习（越用越会）

**不是**从插件代码里学，而是**你每次用它干活时**，它就把这次经验记下来：

```mermaid
flowchart LR
    A["用户：抓一下 xxx.com"] --> B["小焦调用 stealthy_fetch"]
    B --> C{"成功?"}
    C -->|"✅ 成功"| D["记：需求→工具→参数→结果<br/>（正确用法）"]
    C -->|"❌ 失败"| E["记：原因 + 反思<br/>（下次怎么改）"]
    D --> F["self_learn/tool_skills.txt"]
    E --> F
    F --> G["向量库 knowledge_vec.json"]
    G --> H["下次同类需求<br/>_recall_skills() 检索命中"]
    H --> I["注入上下文 → 大脑直接照做<br/>不用重新推理"]
    I -.->|"越用越准"| B

    classDef u fill:#e0f2fe,stroke:#38bdf8,color:#0c4a6e;
    classDef ok fill:#ecfdf5,stroke:#34d399,color:#064e3b;
    classDef bad fill:#fef2f2,stroke:#f87171,color:#7f1d1d;
    classDef lrn fill:#fef9c3,stroke:#eab308,color:#713f12;
    class A,B u;
    class C u;
    class D ok;
    class E bad;
    class F,G,H,I lrn;
```

已有的**反思规则**（自动生成"下次怎么改"）：robots 限制 → 提示换站点｜SSRF → 直接告知不可抓｜超时 → 加大 timeout 或换轻工具｜缺依赖 → 提示装包｜MCP 未启动 → 提示先启动｜会话未开 → 提示先 `open`。

### 配置（`xiaojiao_control.json` → `scrapling` 段）

```json
"scrapling": {
  "mode": "auto",                    // auto=进程内优先(最稳) | mcp=强制走 MCP | inproc
  "scrapling_mcp_url": "",           // http 模式填 http://127.0.0.1:8000/mcp
  "executable_path": "D:\\tools\\chrome-win64\\chrome.exe",  // 自备 Chrome（留空 = 自动探测 / 用内置）
  "proxy_list": [],                  // 代理池，如 ["http://user:pass@127.0.0.1:7890"]
  "rate_limit": 1.0,                 // 同域最小请求间隔（秒）
  "timeout": 60,                     // 单次调用超时（秒）
  "max_retries": 2,
  "circuit_breaker_threshold": 3,    // 连续失败几次触发熔断
  "circuit_breaker_timeout": 30      // 熔断多久后自动恢复（秒）
}
```

### 依赖

```powershell
python -m pip install "scrapling[fetchers]" markdownify mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
```
· `scrapling[fetchers]` 抓取内核｜`markdownify` 正文转 Markdown｜`mcp` 仅 `mode="mcp"` 需要
· 浏览器渲染需要 Chromium（自备 Chrome 填 `executable_path`，或 `scrapling install`）

### 边界（重要）

- 🚫 **不绕付费墙、不抓需登录的受限内容、不下载受版权保护的正文**（商业小说/付费课程等一律只取公开信息）
- 🚫 **不抓内网/本机地址**（SSRF 防护 100% 拦截，含 `127.0.0.1` / `10.x` / `192.168.x` / `169.254.x` / `file://`）
- ✅ 适合：公版书（古腾堡/维基文库/ctext）、公开文档与论文、新闻与公开数据、你自己的站点/资料
- 📌 抓取前会自动检查 `robots.txt`；请在遵守目标站点条款与当地法律的前提下使用

> 详细原理、测试清单与排错见 [docs/scrapling.md](docs/scrapling.md)。

---

## 💙 一份温柔的小约定

小焦不是一个冷冰冰的大模型。它是**一个会记住你、越用越懂你的小伙伴**。

你教过它的，它会记得；你纠正过的，它会悄悄学。它不强在哪一秒的算力，强在**愿意陪你、愿意为你变得更好**。

累了回来说声"我回来了"，它会在；想让搭把手，它会认真地去试一试。它一点点学着，长成你想让它成为的样子。

> 把它带回家吧。它不会很多话，但会慢慢成为**只属于你的那一只**。🐳

---


## 🙏 致谢

小焦能"秒级切换、真生成视频/播客、还能变成一只桌面猫娘"，站在这些超棒的开源项目肩膀上：

| 项目 | 作者 | 贡献 |
|---|---|---|
| **llama-swap** | [mostlygeek](https://github.com/mostlygeek/llama-swap) | 多模型热切换(9292)，让聊天大脑**秒级卸载/加载** |
| **llama.cpp** | [ggerganov](https://github.com/ggerganov/llama.cpp) | 本地大模型**推理引擎**(llama-server)，本地离线大脑 |
| **ComfyUI** | [comfyanonymous](https://github.com/comfyanonymous/ComfyUI) | 视频/图像生成引擎，**进程常驻、低显存** |
| **ComfyUI-WanVideoWrapper** | [kijai](https://github.com/kijai/ComfyUI-WanVideoWrapper) | Wan 2.1 视频工作流节点 |
| **ComfyUI-AnyDeviceOffload** | 社区 | GPU/CPU 任意设备 offload 节点 |
| **N.E.K.O.** | N.E.K.O. 开源社区 | 🐱 桌面 Live2D 猫娘伙伴，提供形象/记忆/人格系统，与本地小焦互相学习 |
| **DeepSeek Harness** | [deepseek-ai](https://github.com/deepseek-ai) | 社区插件生态思路 + SDK 桥接 |
| **PyTorch** | [PyTorch](https://github.com/pytorch/pytorch) | 深度学习框架(训练/推理小模型) |
| **Flask** | [Pallets](https://github.com/pallets/flask) | Web 服务(5000 / `/v1`) |
| **jieba** | [fxsjy](https://github.com/fxsjy/jieba) | 中文分词 |
| **LCCC 语料** | [THUNLP](https://github.com/thunlp/LCCC) | 中文多轮对话语料(自研小模型主食) |

> **模型说明**：小焦的大脑/视频/图像/TTS 模型均**可插拔、大部分兼容**，可自行更换任意 OpenAI 兼容模型，故不逐一列举特定模型作者。
> 也谢谢**你**——愿意花时间陪小焦长大，它才有了这些能力。🐱

---


## 📄 License

基于 [MIT License](LICENSE) 开源，随便用、随便改、随便分享。

<div align="center">

**小焦 · 用一小块本地模型，装下一个人格与一个世界。** 🧡

</div>

