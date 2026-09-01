# 快速上手

拿到小焦，五分钟跑起来。

## 1. 装依赖

```powershell
cd C:\xiaojiao\xiaojiao harness
pip install -r requirements.txt
```

## 2. 放好模型

小焦要一个 "大脑"。默认读 `xiaojiao_control.json` 里的 `brain.llama.gguf`。

你有 GGUF 就把它路径填进去；没有就从 Hugging Face 下个 Qwen 系 GGUF，或看看 Release 上有没有分片。

**重要**：`xiaojiao_control.json` 里的 `brain.engine` 保持 `auto`（或 `llama`），别选 `xiaojiao`，不然会用那个自研的小模型（能力弱，默认已停用）。

## 3. 启动

```powershell
python start_xiaojiao.py
```

它自动：
1. 起聊天大脑（llama-swap:9292，start_xiaojiao 自动）
2. 起网页（5000）
3. 开浏览器

> 看到 `✅ 大脑 … 已就绪` 就成功了。

## 4. 用

- **聊天**：直接在输入框问。
- **让它干活**：右上角「🛠️ 工具」保持绿色，说"在桌面建个 xxx 文件夹写个 a.html"，它会自己分步做。
- **切模型**：顶部下拉；或设置里 → 模型管理 → 添加。
- **建新会话**：左边「＋ 新对话」。
- **🐳 桌面宠物**：自动拉起 Electron 透明窗口，点全息核心弹菜单[语音通话/聊天/装小焦/收起]。
- **💰 成本看板**：浏览器打开 `http://127.0.0.1:5000/cost`。
- **📷 视觉识图**：设 `XIAOJIAO_VISION_URL=http://127.0.0.1:8082/v1`，宠物点📷拍照即识图。

## 装小焦体检

宠物点「🛠️ 装小焦」→ 秒出环境清单 ✅/❌，告诉你缺什么、怎么装。


## 换端口

```powershell
python start_xiaojiao.py --port 8081      # 或改 xiaojiao_control.json 的 web_port
```

## 换个宿主

小焦的接口是 OpenAI 兼容的。把 dsh 或其他客户端的 base_url 填成 `http://127.0.0.1:5000/v1` 就能接入小焦。
