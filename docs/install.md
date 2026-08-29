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
