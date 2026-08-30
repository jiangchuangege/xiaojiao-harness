# 🎭 Agent 预设切换（小焦）

一键切换**人格 + 大脑 + 工具开关**，不用重启、不用改代码。放在 `xiaojiao_control.json` 同目录的 `presets/` 文件夹里。

## 怎么用
- 网页顶部「🎭 预设」下拉，选一个预设 → 自动加载对应配置（人格/大脑/工具开关），**立即生效，不用重启**。
- 预设文件放 `presets/`.json。

## 预设 JSON 格式
```json
{
  "name": "编程助手",
  "role": "你是小焦·编程助手。回答要简洁专业，代码一律用```代码块```，中文注释。",
  "brain": {"engine": "llama", "llama": {"ctx": 20000}},
  "capabilities": {"web_search": true, "memory": true, "run_tools": true, "context_len": 30},
  "behavior": {"temperature": 0.2, "max_tokens": 2048}
}
```
| 字段 | 含义 |
|---|---|
| `name` | 预设名(下拉显示) |
| `role` | **人格/人设**（小焦成什么类型） |
| `brain` | **大脑**（engine=auto/llama/api/xiaojiao；llama.ctx=上下文） |
| `capabilities` | **工具开关**（web_search 联网、memory 记忆、run_tools 工具/代码执行、context_len 上下文轮数） |
| `behavior` | 温度、max_tokens |

## 内置示例（presets/）
- `default.json`（默认·小焦）
- `编程助手.json`（简洁专业、代码块、工具全开）
- `闲聊陪伴.json`（幽默、少联网、少工具）

## 接口
| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/presets` | GET | 列出所有预设 + 当前 |
| `/api/presets/load` | POST | 加载预设（合并配置 + 热更新，不重启） |
| `/api/presets/current` | GET | 当前预设状态 |

## 原理（合并配置，不重启）
```
选预设 → /api/presets/load → 读 presets/*.json
  → _deep_merge(合并进 CONTROL, 不覆盖未提到的键)
  → 写回 xiaojiao_control.json
  → reload_control() 热更新内存(SYSTEM_PROMPT/BRAIN/CAP) → ✓ 立即生效
```

## 新增一个预设
在 `presets/` 加一个 `.json`（按上面格式），网页下拉自动出现。
