# 🎬 真·文生视频（小焦 × ComfyUI × Wan2.1）

小焦网页内置「**真·AI 文生视频**」：输入一句场景，本地 **ComfyUI + Wan2.1-FP8** 真实生成视频。8G 显存按需切换模型，全程自动、用户无感。

---

## 🏆 效果演示（本地真生成的视频）

这是**小焦在你机器上本地 AI 生成**的真实视频（Wan2.1-1.3B-FP8，480p，8G 显存）：

<video src="/media/wan_video_demo_1.mp4" controls width="100%"></video>
<video src="/media/wan_video_demo_2.mp4" controls width="100%"></video>

> **有多强**：完全本地、不联网、不上传（隐私）；真扩散模型生成（不是贴图/假动画）；8G 显卡就能跑；3 分钟出片。**这是"小焦自己长出视频能力"的证明。**

---

## 原理 / 按需切换（8G 互斥）

```
点 🎬 → llama-swap 卸载大脑(9292, 秒级) → 启动 ComfyUI(8188) → Wan2.1-FP8 生成(480p)
     → 下载视频到本地 videos/ → 停止 ComfyUI → 恢复大脑 → 网页播放
```

**核心**：大脑(LLM) 和 视频(扩散) 不同时占显存——先卸一个再起另一个，全程自动。

---

## 📦 要下载什么软件 / 模型

### 1. ComfyUI（软件）
- **ComfyUI 便携版**（带独立 python，免装环境）：推荐 `ComfyUI_windows_portable_nvidia_cu126`（含 CUDA/cuDNN 运行时）。
- 解压到任意目录（例如 `G:\模型文件\视频模型`）。

### 2. Wan 视频模型（三个都要）
Wan2.1 文生视频 = **1 个主模型 + 1 个文本编码器 + 1 个 VAE**：

| 文件 | 作用 | 放哪 |
|---|---|---|
| **模型 `dit_fp8.safetensors`（Wan2.1-1.3B-FP8, ~1.3GB）** | 生成本体 | `ComfyUI\models\checkpoints\` |
| **文本编码器 `umt5_fp8.safetensors`** | 理解你的提示词 | `ComfyUI\models\text_encoders\` |
| **VAE `vae_fp8.safetensors`** | 把潜变量变图像 | `ComfyUI\models\vae\` |

> 来源：Hugging Face：`Wan-AI/Wan2.1-T2V-1.3B-Diffusers`。**国内加速**：设 `HF_ENDPOINT=https://hf-mirror.com`，或去 hf-mirror.com 手动下。

---

## 🧩 兼容哪些视频模型

| 模型 | 大小 | 8G 可否 | 说明 |
|---|---|---|---|
| **Wan2.1-T2V-1.3B-FP8** | ~1.3GB | ✅ 推荐 | 480p，质量中上，小焦默认 |
| **LTX-Video 2B** | ~9GB | ⚠️ 勉强 | 画面更活，8G 吃力 |
| Wan2.1-14B | 大 | ❌ | 需 16G+ |
| Wan2.5/2.6 | 大 | ⚠️ | 需 720p，8G 易爆 |

> 小焦工作流（`video_service/workflow_wan.json`）用的是 **WanVideoWrapper** 节点，能加载**任意 Wan 权重**（改 `config.py` 的 checkpoint 名即可换模型）。

---

## 🗂️ 目录结构（模型放哪，全貌）

```
G:\模型文件\视频模型\
├── ComfyUI_windows_portable_nvidia_cu126\   ← ComfyUI 便携版
│   └── ComfyUI\
│       ├── main.py
│       └── models\
│           ├── checkpoints\   ← 放 dit_fp8.safetensors (Wan2.1 模型)
│           ├── text_encoders\ ← 放 umt5_fp8.safetensors
│           └── vae\           ← 放 vae_fp8.safetensors
├── dit_fp8.safetensors        (放外包层也可以，config 会自动找)
└── ...
```

小焦侧（`video_service/config.py`，也可用环境变量）：
| 变量 | 说明 | 默认 |
|---|---|---|
| `XIAOJIAO_VIDEO_ROOT` | 视频模型总目录 | `G:\模型文件\视频模型` |
| `XIAOJIAO_COMFY_DIR` | ComfyUI 启动目录 | 自动探测含 `main.py` 的目录 |
| `XIAOJIAO_COMFY_PORT` | ComfyUI 端口 | 8188 |

---

## 🚀 怎么用

1. **启动小焦**：`python start_xiaojiao.py`（大脑 + 网页 5000）。
2. **下载/放好**：ComfyUI + 三个模型（按上面目录放）。
3. 网页打开 `http://127.0.0.1:5000` → 点 **🎬 生成视频** → 输入场景。
4. 系统**自动**：卸大脑→起 ComfyUI→生成(约2-3分钟)→停 ComfyUI→恢复大脑。
5. 网页显示"第X/14步 / Z%"进度条 + 完成后**直接播放视频**。

> 首次需等 ComfyUI 加载模型（1-3 分钟）；生成中刷新/换页面进度不丢。

---

## 接口 / 排查
- `GET /api/video/current`、`/api/video/status?job=` → 任务状态/进度。
- **生成失败**：看 job 的 `error` 字段；历史根因=BlockSwap 节点（已去除）。
- **ComfyUI(8188) 关**：生成完按设计停止（释放显存）；生成进行中才在。
- **进度条不显示**：ComfyUI `/progress` 空返回；可换 ws 监听拿真实步数（进阶）。
