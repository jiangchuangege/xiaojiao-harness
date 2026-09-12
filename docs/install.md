# 安装

从零把小焦装起来。

## 环境

- Windows / Linux
- Python 3.10+
- 一个能跑的 GGUF 大模型（或任一 OpenAI 兼容接口）

## 步骤

1. **拉代码**
   ```powershell
   git clone https://github.com/jiangchuangege/xiaojiao-harness
   cd xiaojiao-harness
   ```

2. **装依赖**
   ```powershell
   pip install -r requirements.txt
   ```
   `requirements.txt` 主要是 `flask`、`requests`、`torch`。

3. **配模型**：编辑 `xiaojiao_control.json`
   - `brain.engine`: `auto`（默认）
   - `brain.llama.gguf`: 指向你的 `.gguf`
   - 或 `brain.api`: 填外接接口的 base_url/api_key/model

   > **模型不写死，最小→最大都能用**：`.gguf` 任意通用（最小 ~4B 起，8G 显存可跑）；云端的 `brain.api` 填**任一 OpenAI 兼容模型**（DeepSeek / Qwen / OpenAI 端点等，不占本地显存，能力最强）。小焦 `/v1` 兼容 OpenAI，换模型只改配置，人格/工具/记忆不变。

4. **启动**
   ```powershell
   python start_xiaojiao.py
   ```

5. **打开** `http://127.0.0.1:5000`

> **模型检测按"协议连通"**：安装向导（`python install_all.py`）与「装小焦体检」都不只看"配置里填没填"，而是**真发一次 OpenAI 兼容请求**——本地大脑看端口、云端看 `/models`（不行则 `chat/completions`），**通了才算通过**。若填了 API 但连不上（地址错/key 错/服务没起），会如实报"不通"，不会误判成可用。逻辑详见 [dependency-check.md](dependency-check.md)。

## 🛠️ 一键安装（推荐：`python install_all.py` 或双击 `一键安装.bat`）

检测项分**两组**，报告里分开列：

| 分组 | 包含 | 缺了会怎样 |
| --- | --- | --- |
| **必需** | Python 3.13 + `requirements.txt` 依赖；🧠 **小脑 MiniGPT**（`*.pth` + `vocab*.pkl` + `model_config.json`，**项目核心**，也是唯一必需的 AI 组件） | 进不去小焦（脚本会点名缺什么、怎么补） |
| **可选** | `llama-server.exe`（文字大脑）、`llama-swap`（秒级切换）、ComfyUI（视频大脑）、🐱 猫娘 N.E.K.O.、Scrapling 抓取栈 | **只少一个功能，不拦启动** |

- **路径全靠检测，不写死**：小脑用全盘 `*.pth`（体积优先）+ 跨目录 `vocab*.pkl` 配对；其余组件同样按关键词/盘符探测。
- **什么模型都能当小脑**：换任自训三件套后，改 `xiaojiao_control.json → brain.xiaojiao`（`model_path` / `vocab_path` / `config_path`）即可，代码不动。
- Wan 视频模型（2.5GB）与猫娘都**先询问**再决定是否下载/拉起。

> 小脑路径解析顺序：**环境变量 `XIAOJIAO_BRAIN_MODEL` / `XIAOJIAO_BRAIN_VOCAB` / `XIAOJIAO_BRAIN_CONFIG` → `xiaojiao_control.json → brain.xiaojiao` → 项目目录 + 全盘 glob**。完整说明见仓库 README 的 **🛠️ 一键安装 · 检测分级** 章节（含分级图、路径解析图、改动全景图）。

## 常见安装问题

- **没网/连不上**：先确认 9292、5000 端口空闲。
- **CUDA OOM**：把 `brain.llama.ctx` 改小（32768 → 16384）。
- **想用 API 不用本地模型**：`brain.engine` 设 `api`，填好 `brain.api`。


## 🔀 换电脑 / 迁移（路径不写死，3 选 1）

**代码里没有必须改的绝对路径**，换电脑按下面任一方式即可：

| 方式 | 做法 |
| --- | --- |
| **① 自动查找（啥都不用做）** | 把 `llama-server.exe` 和模型 `.gguf` 放到 `C:/llama`、项目目录、用户目录或 `Downloads`，`start_xiaojiao.py` 会自动找到 |
| **② 环境变量** | `XIAOJIAO_LLAMA_SERVER`=llama-server 路径；`XIAOJIAO_LLAMA_GGUF`=模型路径；`XIAOJIAO_LLAMA_SWAP`=llama-swap 路径；`XIAOJIAO_BRAIN_MODEL`/`XIAOJIAO_BRAIN_VOCAB`/`XIAOJIAO_BRAIN_CONFIG`=小脑三件套；`XIAOJIAO_NEKO_AUTO=1`=启动时免询问直接拉起猫娘；`LLAMA_API`=蒸馏用接口；`LLM_BASE_URL`=OpenAI兼容接口 |
| **③ 控制文件** | 改 `xiaojiao_control.json` 的 `brain.llama.server / gguf / port`、`brain.xiaojiao.*`、`scrapling.*`（配置文件的用途就是给人改） |

> 优先级：**控制文件(存在才用) → 环境变量 → 自动查找**。其余数据文件（记忆/会话/知识/插件）都跟随项目目录，天然可移植，复制整个文件夹即可。
