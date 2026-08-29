# video_service/comfy_client.py —— 与 ComfyUI 通信
import os, time, json, requests

import config


def submit_workflow(workflow):
    """提交工作流到 ComfyUI，返回 prompt_id。"""
    r = requests.post(config.COMFY_URL + "/prompt",
                      json={"prompt": workflow, "client_id": "xiaojiao-harness"}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_output(prompt_id, timeout=1800, progress_cb=None):
    """轮询 /history 直到出视频文件；返回 (filename, subfolder, type)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            h = requests.get(config.COMFY_URL + "/history/%s" % prompt_id, timeout=15).json()
        except Exception:
            h = {}
        if prompt_id in h and h[prompt_id].get("outputs"):
            for node, o in h[prompt_id]["outputs"].items():
                for key in ("gifs", "videos", "images"):
                    for f in o.get(key, []):
                        if key != "images" or f.get("type") == "output":
                            return f.get("filename"), f.get("subfolder", ""), f.get("type", "output")
            if h[prompt_id].get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI 执行出错: %s" % json.dumps(h[prompt_id].get("status"))[:200])
        if progress_cb:
            progress_cb()
        time.sleep(3)
    raise TimeoutError("视频生成超时(30分钟)")


def download_video(filename, subfolder, ftype, out_path):
    r = requests.get(config.COMFY_URL + "/view",
                     params={"filename": filename, "subfolder": subfolder, "type": ftype}, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path
