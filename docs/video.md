# 🎬 真·文生视频（小焦 × ComfyUI × Wan2.1）

小焦网页内置「**真·AI 文生视频**」：输入一句场景，本地 **ComfyUI + Wan2.1-FP8** 真实生成视频。8G 显存按需切换模型。

## 原理 / 按需切换（8G 互斥）

```
点 🎬 → 卸载大脑(8080) → 启动 ComfyUI(8188) → Wan2.1-FP8 生成(480p)
     → 下载视频到本地 videos/ → 停止 ComfyUI → 恢复大脑 → 网页播放
```

**核心**：大脑(LLM) 和 视频(扩散) 不同时占显存——先卸一个再起另一个，全程自动、用户无感。

## 目录
```
video_service/
├── config.py          # ComfyUI 位置/端口/模型(环境变量可配, 自动探测)
├── model_switch.py    # 显存切换协调器(卸大脑/起ComfyUI/停/恢复)
├── comfy_client.py    # 与 ComfyUI 通信(提交/轮询/下载+实时进度)
├── video_api.py       # Web 接口(/api/video + /status + /current + 进度)
├── workflow_wan.json  # Wan 2.1 T2V 工作流(Wrapper节点, 480p, 无BlockSwap)
└── README.md          # 本文件
```

## 用
- 网页点 **🎬** → 输场景 → 自动切换 → 进度条/步数显示 → 完成出视频。
- 首次：ComfyUI 需放好模型（`dit_fp8.safetensors`→checkpoints、`umt5_fp8`→text_encoders、`vae_fp8`→vae）。

## 配置（`video_service/config.py` / 环境变量）
| 变量 | 说明 | 默认 |
|---|---|---|
| `XIAOJIAO_VIDEO_ROOT` | 视频模型总目录 | `G:\模型文件\视频模型` |
| `XIAOJIAO_COMFY_DIR` | ComfyUI 启动目录 | 自动探测 |
| `XIAOJIAO_COMFY_PORT` | ComfyUI 端口 | 8188 |
| `XIAOJIAO_WAN_MODEL` | Wan 模型 | 自动(用 dit_fp8) |

## 进度/状态
- `GET /api/video/current` → 当前任务(跨浏览器/刷新/重启)。
- `GET /api/video/status?job=` → 任务状态(state/message/progress 步数)。
- 任务超时(10分钟)自动判失败；状态读取无锁(不堵接口)。

## 常见问题
- **生成失败**：历史根因=BlockSwap 节点致 `None` 错误，已去掉；若失败看 job 的 error 字段。
- **ComfyUI 8188 关**：生成完按设计停止(释放显存)；生成进行中才在。
- **进度条不显示**：ComfyUI `/progress` 空返回；换 ws 监听可拿真实步数（进阶）。
