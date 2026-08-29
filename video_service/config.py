# video_service/config.py —— 视频生成服务配置（自动探测，全部可改）
import os

# 视频模型总目录（可改/可设环境变量）
VIDEO_ROOT = os.environ.get("XIAOJIAO_VIDEO_ROOT", r"G:\模型文件\视频模型")
COMFY_PORT = int(os.environ.get("XIAOJIAO_COMFY_PORT", "8188"))
COMFY_URL = "http://127.0.0.1:%d" % COMFY_PORT
BRAIN_PORT = int(os.environ.get("LLAMA_PORT", "8080"))


def find_comfy_dir():
    """在 VIDEO_ROOT 下递归找含 main.py 的 ComfyUI 目录（便携版嵌套）。"""
    cand = os.environ.get("XIAOJIAO_COMFY_DIR", "")
    for d in (cand, VIDEO_ROOT):
        if d and os.path.exists(os.path.join(d, "main.py")):
            return d
    # 递归找 main.py（最多 4 层）
    for root, dirs, files in os.walk(VIDEO_ROOT):
        depth = root[len(VIDEO_ROOT):].count(os.sep)
        if depth > 4:
            dirs[:] = []
            continue
        if "main.py" in files:
            return root
        if "node_modules" in dirs:
            dirs.remove("node_modules")
    return os.path.join(VIDEO_ROOT, "ComfyUI")


COMFY_DIR = find_comfy_dir()

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "videos")
os.makedirs(OUT_DIR, exist_ok=True)


def find_checkpoint():
    """找 Wan FP8 模型：先看 checkpoints 目录，再看 VIDEO_ROOT 外层。"""
    base = os.path.join(COMFY_DIR, "models", "checkpoints")
    for folder in (base, VIDEO_ROOT):
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".safetensors", ".ckpt")) and any(k in f.lower() for k in ("wan", "fp8", "dit", "1.3b")):
                    return f
        except Exception:
            continue
    return None


def brain_llama():
    import json
    try:
        c = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xiaojiao_control.json"), encoding="utf-8"))
        ll = c.get("brain", {}).get("llama", {})
        return ll.get("server", ""), ll.get("gguf", ""), int(ll.get("port", 8080)), int(ll.get("ctx", 32768))
    except Exception:
        return "", "", BRAIN_PORT, 32768
