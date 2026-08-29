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

4. **启动**
   ```powershell
   python start_xiaojiao.py
   ```

5. **打开** `http://127.0.0.1:5000`

## 常见安装问题

- **没网/连不上**：先确认 8080、5000 端口空闲。
- **CUDA OOM**：把 `brain.llama.ctx` 改小（32768 → 16384）。
- **想用 API 不用本地模型**：`brain.engine` 设 `api`，填好 `brain.api`。


## 🔀 换电脑 / 迁移（路径不写死，3 选 1）

**代码里没有必须改的绝对路径**，换电脑按下面任一方式即可：

| 方式 | 做法 |
| --- | --- |
| **① 自动查找（啥都不用做）** | 把 `llama-server.exe` 和模型 `.gguf` 放到 `C:/llama`、项目目录、用户目录或 `Downloads`，`start_xiaojiao.py` 会自动找到 |
| **② 环境变量** | `XIAOJIAO_LLAMA_SERVER`=llama-server 路径；`XIAOJIAO_GGUF`=模型路径；`LLAMA_PORT`=大模型端口(默认8080)；`LLAMA_API`=蒸馏用接口；`LLM_BASE_URL`=OpenAI兼容接口 |
| **③ 控制文件** | 改 `xiaojiao_control.json` 的 `brain.llama.server / gguf / port`（配置文件的用途就是给人改） |

> 优先级：**控制文件(存在才用) → 环境变量 → 自动查找**。其余数据文件（记忆/会话/知识/插件）都跟随项目目录，天然可移植，复制整个文件夹即可。
