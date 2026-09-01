# 接口说明

小焦提供一套 OpenAI 兼容接口，随便什么客户端填 base_url 就能接上它。

## 地址

```
http://127.0.0.1:5000/v1
```

## 支持的端

- `GET /v1/models` — 列出模型
- `POST /v1/chat/completions` — 对话（流式/非流式）
- 也支持 `tools`（function calling），小焦会注入人设

## 例子

```bash
curl http://127.0.0.1:5000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "xiaojiao1.0-4B",
    "messages": [{"role": "user", "content": "你是谁"}]
  }'
```

## 就能接到

- **dsh（DeepSeek Harness）**：把模型提供方的 base_url 填 `http://127.0.0.1:5000/v1`
- **任何 OpenAI 客户端 / open-webui / 脚本**

## 注意

小焦人格是**人设层注入**的，所以走 `/v1` 才有人格（"我是小焦"）。你要是直接连裸模型（llama-swap 上游的 llama），那就是模型自己的出厂回答，是人设注入的对象，不是小焦。就这么回事。

## 小焦自己的接口

- `POST /api/chat` — 网页用的对话
- `GET /api/history` / `/api/sessions` — 历史 / 会话
- `POST /api/session/new`、`GET /api/session/<id>` — 建会话 / 切换
- `POST /api/tools_toggle` — 工具开关
- `POST /api/model/select` — 切模型
- `POST /api/confirm` — 确认执行危险命令

## 工具接口（5003）

```
POST http://127.0.0.1:5003/api/run   {"name":"run_command","command":"..."}
GET  http://127.0.0.1:5003/api/tools
```
