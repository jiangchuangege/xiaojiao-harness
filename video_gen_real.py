# -*- coding: utf-8 -*-
"""🐳 小焦 · 真·AI 文生视频（本地扩散模型，8G 显存可跑）
支持两个开源模型（二选一，按已下载的自动识别）：
  - Wan2.1-T2V-1.3B   (阿里, Apache-2.0)  下载 ~5GB   → Wan-AI/Wan2.1-T2V-1.3B-Diffusers
  - LTX-Video 2B      (Lightricks, Apache-2.0) 下载 ~9GB → Lightricks/LTX-Video
8G 方案：float16 + enable_model_cpu_offload() + vae/attention slicing。
速度：一段 2-4 秒(57帧) 在 8G 上约 5~15 分钟；首跑自动下载模型。
用法：python video_gen_real.py "樱花飘落的春天" [--model wan|ltx]
环境：pip install diffusers transformers accelerate safetensors imageio[ffmpeg]
"""
import os, sys, datetime

PROMPT = sys.argv[1] if len(sys.argv) > 1 else "a whale swimming in the deep blue ocean"
MODEL_CHOICE = None
for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        MODEL_CHOICE = sys.argv[i + 1]
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")
os.makedirs(OUT_DIR, exist_ok=True)

# 本地已下载的模型目录（放这里自动识别；或设 HF 端点在缓存里）
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
WAN_DIR = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
LTX_ID = "Lightricks/LTX-Video"


def detect_model():
    if MODEL_CHOICE == "wan":
        return "wan"
    if MODEL_CHOICE == "ltx":
        return "ltx"
    # 自动识别：看本地缓存里下了哪个
    try:
        for d in os.listdir(CACHE):
            if "wan2.1-t2v-1.3b" in d.lower():
                return "wan"
            if "ltx-video" in d.lower():
                return "ltx"
    except Exception:
        pass
    return "ltx"  # 默认 LTX


def ensure_deps():
    try:
        import torch, diffusers, transformers, accelerate, safetensors, imageio
        return True
    except Exception as e:
        print("❌ 缺少依赖:", e)
        print('  请先安装：pip install diffusers transformers accelerate safetensors imageio[ffmpeg]')
        return False


def generate(prompt=PROMPT, width=512, height=512, frames=57, steps=40):
    import torch
    from diffusers import LTXPipeline, WanPipeline
    model = detect_model()
    print("🎯 使用模型:", "Wan2.1-T2V-1.3B" if model == "wan" else "LTX-Video 2B")
    print("⏳ 加载模型（首次会自动下载 ~%sGB，可用 HF_ENDPOINT=https://hf-mirror.com 加速）..." % ("5" if model == "wan" else "9"))
    if model == "wan":
        pipe = WanPipeline.from_pretrained(WAN_DIR, torch_dtype=torch.float16, variant="bf16")
    else:
        pipe = LTXPipeline.from_pretrained(LTX_ID, torch_dtype=torch.float16, variant="fp16")
    # —— 8G 显存三件套 ——
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    pipe.enable_attention_slicing()
    print("🧠 真视频生成中（8G 上约 5~15 分钟，别关窗口）...")
    kw = dict(prompt=prompt, negative_prompt="low quality, worst quality, blurry, jittery",
              width=width, height=height, num_frames=frames, num_inference_steps=steps)
    out = (pipe(**kw, guidance_scale=5.0 if model == "wan" else 3.0)).frames[0]
    name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_real"
    import imageio.v2 as iio
    mp4 = os.path.join(OUT_DIR, name + ".mp4")
    iio.mimsave(mp4, out, fps=24, quality=8)
    print("✅ 真视频已生成:", mp4)
    return mp4


if __name__ == "__main__":
    if ensure_deps():
        generate()
