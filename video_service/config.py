# video_service/config.py —— 视频生成服务配置（自动探测，全部可改）
import os
import logging  # noqa: F401  （由 tools/fix_silent_except.py 注入）
try:
    from xiaojiao_log import get_logger
except Exception:  # 独立运行时退化为标准 logging
    def get_logger(name=None):
        return logging.getLogger(name or 'xiaojiao')
LOG = get_logger(__name__)

# 视频模型总目录（可改/可设环境变量；默认自动探测，不写死）
def _find_video_root_near(comfy_dir):
    """从 ComfyUI 目录往上找几层，看哪一层放着 dit_fp8.safetensors（模型常在便携包外层）。"""
    d = (comfy_dir or "").rstrip("\\/")
    for _ in range(5):
        if not d:
            break
        if os.path.exists(os.path.join(d, "dit_fp8.safetensors")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return ""


def _default_video_root():
    """优先级：ComfyUI 目录附近 → 全盘探测含 dit_fp8 的目录 → 空。绝不写死路径。"""
    try:
        import json
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        c = json.load(open(os.path.join(_root, "xiaojiao_control.json"), encoding="utf-8"))
        cd = (c.get("brain", {}) or {}).get("comfy_dir") or ""
        if cd:
            near = _find_video_root_near(cd)
            if near:
                return near
            return os.path.dirname(cd.rstrip("\\/"))
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 32, e)
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import install_all as _ia
        return _ia.discover_video_root() or ""
    except Exception:
        return ""


VIDEO_ROOT = os.environ.get("XIAOJIAO_VIDEO_ROOT") or _default_video_root()
COMFY_PORT = int(os.environ.get("XIAOJIAO_COMFY_PORT", "8188"))
COMFY_URL = "http://127.0.0.1:%d" % COMFY_PORT
BRAIN_PORT = int(os.environ.get("LLAMA_PORT", "8080"))


def find_comfy_dir():
    """在 VIDEO_ROOT 下递归找含 main.py 的 ComfyUI 目录（便携版嵌套）。"""
    cand = os.environ.get("XIAOJIAO_COMFY_DIR", "")
    if not cand:
        try:
            import json
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            c = json.load(open(os.path.join(_root, "xiaojiao_control.json"), encoding="utf-8"))
            cand = (c.get("brain", {}) or {}).get("comfy_dir") or ""
        except Exception:
            cand = ""
    for d in (cand, VIDEO_ROOT):
        if d and os.path.exists(os.path.join(d, "main.py")):
            return d
    if not VIDEO_ROOT:
        # 没配置也没探测到 → 让 install_all 全盘找一次（不写死）
        try:
            import install_all as _ia
            _c = _ia.discover_comfy()
            if _c:
                return _c
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 70, e)
        return cand or ""
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
