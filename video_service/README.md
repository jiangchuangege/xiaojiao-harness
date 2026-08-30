# 🎬 真·文生视频扩展（video_service）

在 `xiaojiao-harness` Web(5000) 里加"生成视频"入口：点 🎬 → **自动卸载大脑(Qwen4B/8080) → 加载 ComfyUI+Wan2.1 → 生成 → **温存留内存**（聊天上显卡时不杀）→ 切第三个大脑/闲置超时自动释放**，全程对用户透明（8G 显存互斥，不同时占两个模型）。

## 1. 架构设计（按需切换）

```
┌────────────┐  🎬点击   ┌─────────────────────────────────────────┐
│  用户/Web  │ ────────▶ │  video_api.py (Blueprint @5000)         │
└────────────┘           │  POST /api/video → 后台线程 _worker      │
                         └────────────────┬────────────────────────┘
                                          ▼
                        ┌────────────────────────────────────────┐
                        │        model_switch.py (协调器)        │
                        │  状态机: idle→stop_brain→start_comfy→  │
                        │         generating→stop_comfy→         │
                        │         start_brain→idle               │
                        └───┬──────────────┬─────────────┬───────┘
                            ▼              ▼             ▼
                    大脑(8080)        ComfyUI(8188)   前端轮询
                    llama-server     + Wan2.1 FP8   /api/video/status
                    杀/起进程         杀/起进程        显示进度
```

- **互斥保证**：先 `stop_brain()`（杀 8080 进程）→ 才 `start_comfy()`；生成完先 `stop_comfy()` → 才 `start_brain()`。任何时刻只有一个模型占显存。
- **线程安全**：全局 `_lock` + 任务状态表，忙时拒绝新请求（返回 `busy`）。

## 2. 关键代码

**切换逻辑** `model_switch.py`：
```python
stop_brain()   # netstat 找 8080 PID → taskkill → 等端口释放(腾显存)
start_comfy()  # Popen(ComfyUI/main.py) → 轮询 8188 就绪(≤3分钟)
stop_comfy()   # 杀 8188 PID → 等释放
start_brain()  # 重起 llama-server(复用控制文件路径) → 等 8080 就绪
```
> 找 PID 用 `netstat -ano | findstr :端口` + `taskkill /F /PID`，不误杀其他 python。

**API** `video_api.py`（Blueprint，挂载在 5000）：
- `POST /api/video` {prompt} → 后台线程跑 `_worker`，返回 job_id
- `GET /api/video/status?job=` → {state: switching|generating|done|error, message, url}
- `GET /api/video/state` → 当前切换状态（前端可显示"正在切换模型"）

## 3. 与 ComfyUI 通信

```python
# comfy_client.py
submit_workflow(wf)   # POST {COMFY_URL}/prompt  → prompt_id
wait_output(pid)      # 轮询 GET {COMFY_URL}/history/{id} 直到 outputs 出现视频文件
download_video(...)   # GET {COMFY_URL}/view?filename=...  → 存到本地 videos/
```
工作流模板 `workflow_wan.json`：CheckpointLoaderSimple → CLIPTextEncode×2 → WanImageToVideo → VAEDecode → SaveVideo。**节点名可能随你 ComfyUI 版本不同**，若报错请照你的 ComfyUI 实际节点调整。

## 4. 异常处理（兜底）

| 场景 | 策略 |
|---|---|
| ComfyUI 路径/启动失败 | `_worker` catch → `stop_comfy()` + `start_brain()`（尽力恢复大脑）→ state=error 带原因 |
| 生成超时/ComfyUI 报错 | `wait_output` 超时(30分钟) → 同样兜底恢复 |
| 大脑恢复失败 | `start_brain` 不抛错，只记录 error 提示（不影响网页） |
| 忙时重复点击 | 返回 `busy`，前端提示"正在生成/切换中" |
| 模型文件缺失 | `find_checkpoint()` 返回 None → 明确提示"把 Wan 模型放进 checkpoints" |

## 5. 首次配置（一次）

1. **ComfyUI**：已在 `G:\模型文件\视频模型\ComfyUI_windows_portable_nvidia_cu126\`（自动探测）。
2. **模型放对位置**：
   - 把 `G:\模型文件\视频模型\dit_fp8.safetensors` 复制到 `ComfyUI_windows_portable_nvidia_cu126\ComfyUI\models\checkpoints\`。
   - Wan2.1 还需要 **文本编码器 umt5-xxl** 和 **Wan VAE**（`models/text_encoders`、`models/vae`），没有的话 ComfyUI 会报缺模型——下载放进去即可。
3. 网页点 🎬 输入场景 → 自动切换生成。

> 可改：环境变量 `XIAOJIAO_VIDEO_ROOT` / `XIAOJIAO_COMFY_DIR` / `XIAOJIAO_COMFY_PORT`。
