# 依赖与模型检测逻辑 · Dependency Check

> **核心规则**：小焦对"大脑模型"的检测，是**按 OpenAI 兼容协议真发一次请求、通了才算通过**——不是只查配置里填没填字符串、文件在不在。这样能真正暴露"配置了但连不上/用不了"的问题。

---

## 1. 为什么要"按协议检测"

只检测"配置里有没有 `api_key` / `base_url`" 或"本地有没有 `.gguf` 文件"，有一个坑：**填了但地址错、key 失效、服务没启动**时，也会被当成"有模型"，结果用户实际用不了却不知道。

所以小焦把模型检测升级为**协议连通测试**：

- **本地大脑**：不只看 GGUF 文件在不在，还**探测大脑服务端口**（socket 连通），确认真在跑。
- **云端 API**：真发一个 OpenAI 兼容请求（`GET /models`，不支持则 fallback 到 `POST /chat/completions`），**返回 200 才算通过**。

---

## 2. 检测函数

`install_all.py` 里实现了两个核心函数：

### `test_cloud_api(base_url, api_key, model="")`
按 OpenAI 兼容协议探测一个端点，返回 `(ok, message)`：

1. 先 `GET {base_url}/models`，`200` → 通过。
2. 若 `/models` 返回 `405/404`（有的端点不开放即进列表），**fallback** 发一个最小 `POST {base_url}/chat/completions`（`{"model":..., "messages":[{"role":"user","content":"hi"}], "max_tokens":1}`），`200` → 通过。
3. 其它状态码（如 `401` 鉴权失败）或连接异常 → **不通**，如实返回原因。

> 为什么有 fallback：有部分端点（如 DeepSeek）不开放 `/models`，但 `/chat/completions` 可用。保留 fallback 才能准确判断"到底能不能聊"。

### `is_port_up(port, timeout=1.0)`
用 `socket` 探测本地端口是否在监听（判断本地大脑服务是否上线）。

---

## 3. 安装向导怎么用（`install_all.py` 第 3 步）

流程：

1. **先看本地**：从配置解析大脑端口（默认 `9292`，即 llama-swap），`is_port_up()` 探测——在线则对 `http://127.0.0.1:{port}/v1` 调 `test_cloud_api`。
2. **再看云端**：若配置了 `api_key` + `base_url`，对云端真发一次 `test_cloud_api`。
3. **结论**：`本地在线 或 云端通` 任一成立 → **协议连通，判通过**。
4. 若都不通 → **交互式引导**：询问是否现在配置云端 API，让你现场输入 `base_url / key / model`，**当场实测**。**通了才写入 `xiaojiao_control.json`（`brain.api`）并判通过**；不通就明确报"连接失败/鉴权失败/HTTP xxx"，绝不误判。

```text
[3/11] 大脑模型 (本地 GGUF 或 云端 OpenAI 兼容 key) —— 按协议连通检测 ...
   ✅ 本地大脑协议通: http://127.0.0.1:9292/v1 (GET /models 200 OK)
   ❌ 云端 API 不通: HTTP 401
   ...
```

---

## 4. `/api/env`（猫娘体检接口）怎么改

`xiaojiao_app.py` 的 `/api/env` 返回的环境体检里，"**对话/工具模型**"这一项同样按协议检测：

- `_local_online = port_up(本地端口)`：本地大脑服务是否在线。
- 云端：`requests.get(base_url + "/models", Authorization=Bearer key, timeout=3)`，`200` 即算通；否则报"不通(HTTP xxx)"或"连接失败"。
- 结论：`_local_online or _cloud_ok` 为真才算 `ok`，否则 `missing`（并给出怎么做）。

这样猫娘问你"装小焦体检"时，**模型有没有真的能用**会如实反映，而不是因为填了个 key 就显示✅。

---

## 5. 检测返回的几种情况（示例）

| 场景 | 检测结果 |
| --- | --- |
| 本地大脑 9292 未启动 | ⓘ 未在线 → 提示"先启动 start_xiaojiao" |
| 云端 API + 正确 key | ✅ `GET /models 200 OK` → 通过 |
| 云端 API + 错误 key | ❌ `HTTP 401` → 不通（端点在、鉴权失败） |
| 云端地址不存在 | ❌ `连接失败` → 不通 |
| 云端只开放 chat 不开放 models | ✅ fallback 到 `/chat/completions` → 通过 |
| `base_url` 为空 | ❌ `base_url 为空` → 不通 |

---

## 6. 相关文件

| 文件 | 作用 |
| --- | --- |
| `install_all.py` | 一键安装向导，第 3 步做本地+云端协议连通检测；`test_cloud_api` / `is_port_up` |
| `xiaojiao_app.py` | `/api/env` 体检接口，"对话/工具模型"按协议检测 |
| `xiaojiao_control.json` | `brain.api`（云端 base_url/api_key/model）、`brain.llama`（本地 GGUF） |

> 模型**可插拔、不写死型号**：任何 OpenAI 兼容端点 / 任意本地 GGUF，只要协议连通就可用。
