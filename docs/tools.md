# 🧰 小焦依赖 & 工具清单（需要什么 / 放哪 / 干什么）

> 环境检查：网页设置里「🛠️ 环境检查」可一键检测下列每一项。

## 1. Python 依赖（`pip install -r requirements.txt`）
| 包 | 用途 |
|---|---|
| `torch>=2.0` | 自研 MiniGPT 训练/推理（小脑） |
| `flask>=2.0` | Web 服务（端口 5000） |
| `requests>=2.28` | HTTP / 调 llama-server / 联网搜索 |
| `numpy>=1.24` | 通用数组 |
| `jieba>=0.42` | 分词（记忆/检索/自学习） |

## 2. 本地大模型（聊天大脑，GGUF）
| 项 | 位置（可配置/环境变量） | 说明 |
|---|---|---|
| **llama-server.exe**（llama.cpp） | `C:/llama/llama-server.exe`，或 `XIAOJIAO_LLAMA_SERVER` / 控制文件 `brain.llama.server` | 推理引擎 |
| **xiaojiao1.0-4B.gguf**（4B 模型） | `C:/llama/xiaojiao1.0-4B.gguf`，或 `XIAOJIAO_LLAMA_GGUF` | 小焦本尊大脑 |

## 3. llama-swap（多大脑秒级切换）
| 项 | 位置 | 说明 |
|---|---|---|
| **llama-swap.exe** | `G:\模型文件\大脑秒计切换\llama-swap_251_windows_amd64\`，或 `XIAOJIAO_LLAMA_SWAP` | 热切换管理器（9292），启动时自动拉起 |
| 配置 `llama-swap.yaml` | 项目根 | 定义 xiaojiao 模型路由 |

## 4. 视频大脑（ComfyUI + Wan2.1）
| 项 | 位置 | 说明 |
|---|---|---|
| **ComfyUI 便携版** | `G:\模型文件\视频模型\ComfyUI_windows_portable_nvidia_cu126\...`，或 `XIAOJIAO_COMFY_DIR` | 视频生成引擎（8188），keep_warm 常驻 |
| **dit_fp8.safetensors**（Wan2.1-1.3B-FP8） | ComfyUI `models/checkpoints/` | 生成本体 |
| **umt5_fp8.safetensors**（文本编码器） | ComfyUI `models/text_encoders/` | 理解提示词 |
| **vae_fp8.safetensors**（VAE） | ComfyUI `models/vae/` | 解码画面 |

## 5. 其它
| 项 | 说明 |
|---|---|
| **Node.js** | 跑 `.js` 插件（`plugins/plugin_runner.js`） |
| **NVIDIA 显卡** | 建议 8GB+ 显存（4060 可跑 4B 大脑 + Wan 480p 视频） |
| **python_embeded**（ComfyUI 自带） | 视频模型专用 Python（与主环境隔离） |

## 6. 环境变量总表（可覆盖硬路径，推荐配置化）
| 变量 | 作用 |
|---|---|
| `XIAOJIAO_LLAMA_SERVER` | llama-server.exe 路径 |
| `XIAOJIAO_LLAMA_GGUF` | 4B 模型路径 |
| `XIAOJIAO_LLAMA_SWAP` | llama-swap.exe 路径 |
| `XIAOJIAO_COMFY_DIR` | ComfyUI 目录 |
| `XIAOJIAO_VIDEO_ROOT` | 视频模型总目录 |
| `XIAOJIAO_KEEP_COMFY` | `1`=视频模型常驻(不重启) |
| `XIAOJIAO_WAN_MODEL` | Wan 模型名 |

> 全部路径**都可配置/环境变量覆盖**，代码里不死写死路径（找不到会提示，不会假装存在）。
