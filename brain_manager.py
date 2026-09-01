# brain_manager.py —— 小焦「多大脑 · 秒级切换」调度中心
# 原理：小焦/小脑作为调度中心，连接多个「大脑」(聊天/视频/图像/推理...)。
#      空闲大脑 → sleep(权重从显存卸载到内存, ~1-2s)；要用 → wake(内存→显存, ~1-2s)。
#      进程常驻不杀，只做权重 offload/onload → 秒级切换，省显存。
import os, json, threading, time, subprocess

# 大脑注册表：每个大脑 = 一个可连的"模型服务"(唯一端口/唯一指纹)
# state: RUN(权重在显存) / SLEEP(权重在内存, 进程在) / OFF(未加载)
# 新增大脑只需在 BRAINS 加一项 + 实现它的 _sleep/_wake(或默认 keep-alive)
BRAINS = {
    "chat": {   # 聊天大脑：llama.cpp (llama-server)
        "name": "聊天大脑 (Qwen 4B, llama-swap)", "port": 9292,
        "type": "llama", "vram_gb": 3.5, "state": "SLEEP", "proc": "/".join(["C:/llama/llama-server.exe"]),
    },
    "video": {  # 视频大脑：ComfyUI + Wan2.1
        "name": "视频大脑 (Wan2.1 + ComfyUI)", "port": 8188,
        "type": "comfy", "vram_gb": 5.0, "state": "OFF", "proc": "comfy",
    },
    "coder": {  # 编码大脑：Qwen3-Coder 7B (llama-swap 托管, 8G 按需切换)
        "name": "编码大脑 (Qwen3-8B 工具·代码)", "port": 9292,
        "type": "llama", "vram_gb": 5.2, "state": "OFF",
    },
    # 未来扩展(示例, 加进 BRAINS 即可被调度):
    # "image": {"name":"图像大脑(SD3)","port":8189,"type":"comfy","vram_gb":4.0,"state":"OFF"},
    # "reason": {"name":"推理大脑(DeepSeek)","port":8081,"type":"llama","vram_gb":6.0,"state":"OFF"},
}

_lock = threading.Lock()


def _port_alive(port):
    import socket
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port)); return True
    except Exception:
        return False
    finally:
        s.close()


def _pid_on_port(port):
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if ":%d " % port in line and ("LISTENING" in line or "LISTEN" in line):
            try:
                return int(line.strip().split()[-1])
            except Exception:
                pass
    return None


def is_running(brain_key):
    b = BRAINS[brain_key]
    return _port_alive(b["port"])


def state():
    with _lock:
        return {k: {"name": v["name"], "state": v["state"], "port": v["port"], "vram_gb": v["vram_gb"]} for k, v in BRAINS.items()}


def _start_llama(b):
    # 先卸载其它 llama 模型(腾全部显存给当前大脑——8G 上两个 llama 不能同时占显存)
    try:
        import model_switch as _ms
        mine = "coder" if str(b.get("name", "")).find("编码") >= 0 or b.get("port") != 9292 else "xiaojiao"
        other = "coder" if mine == "xiaojiao" else "xiaojiao"
        _ms._llama_swap_unload(other)
    except Exception:
        pass
    if _port_alive(b["port"]):
        return True
    try:
        server, gguf = _llama_cfg()
        if not os.path.exists(server):
            server = "llama-server"  # 走 PATH
        if not (gguf and os.path.exists(gguf)):
            return False  # 缺模型: 提示配置, 不死写
        cwd = os.path.dirname(gguf) if os.path.dirname(gguf) else "."
        subprocess.Popen([server, "-m", gguf, "--port", str(b["port"]), "-c", "32768"],
                         cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def _start_comfy(b):
    root = os.environ.get("XIAOJIAO_COMFY_DIR") or _comfy_dir()
    if not root or not os.path.exists(os.path.join(root, "main.py")):
        return False  # 未配置 ComfyUI, 不死写
    py = os.path.join(os.path.dirname(root), "python_embeded", "python.exe")
    try:
        args = [py, "main.py", "--port", str(b["port"])]
        if os.environ.get("XIAOJIAO_KEEP_COMFY") == "1":
            args.append("--lowvram")
        subprocess.Popen(args, cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def wake(brain_key, wait=10):
    """唤醒大脑到显存(RUN)，并确保其它大脑让出显存。复用 video_service.model_switch 的真实控制。"""
    import sys, os as _os
    root = _os.path.dirname(_os.path.abspath(__file__))
    if _os.path.join(root, "video_service") not in sys.path:
        sys.path.insert(0, _os.path.join(root, "video_service"))
    import model_switch as ms
    keep_comfy = (_os.environ.get("XIAOJIAO_KEEP_COMFY") == "1")
    b = BRAINS[brain_key]
    if brain_key == "video":
        # 视频上显存; llama 大脑由 llama.cpp 空闲自动卸到内存(温存, 单槽), 不主动杀
        ms.start_comfy()     # 起视频大脑(ComfyUI+Wan)
    elif brain_key == "chat":
        # 聊天大脑上显卡; 其它温存大脑留在内存(切回快)
        ms.start_brain()     # 聊天大脑 -> 显卡
    else:
        _start_llama(b) if b["type"] == "llama" else _start_comfy(b)
    b["state"] = "RUN"
    return is_running(brain_key)


def sleep(brain_key):
    """温存(WARM, 内存单槽驻留): 不卸载——llama进程/模型保留(llama.cpp空闲自动卸到内存),
    视频保留ComfyUI。切回 = 内存→显存, 秒级。被新温存顶掉时才彻底卸载(_full_stop)。"""
    b = BRAINS.get(brain_key)
    if b:
        b["state"] = "WARM"
    return True


def _full_stop(brain_key):
    """彻底卸载(OFF, 腾显存+内存): 新温存顶掉旧温存时调用——内存只允许一个。"""
    b = BRAINS.get(brain_key)
    if b:
        try:
            import model_switch as _ms
            if b.get("type") == "llama":
                _mid = "coder" if brain_key == "coder" else "xiaojiao"
                _ms._llama_swap_unload(_mid)
            else:
                _ms.stop_comfy()
        except Exception:
            pass
        b["state"] = "OFF"
    return True


def _evict_ram_brains(except_key):
    """切换到"第三个大脑"时, 把内存里温存的大脑清掉(腾内存)。chat/video 之间互不杀。"""
    import model_switch as _ms
    for k in list(BRAINS.keys()):
        if k == except_key or k in ("chat", "video"):
            continue  # 聊天/视频互相切换不清
        try:
            _ms.stop_comfy()  # 第三方大脑占用时, 视频大脑让位(或按类型扩展)
        except Exception:
            pass


def switch_to(target):
    """秒级切换(内存单槽温存): 顶掉旧温存 -> 当前活动去温存 -> 目标上显存。可切回。"""
    with _lock:
        # 1) 顶掉内存里旧的温存(内存只允许一个) —— 新温存进来, 旧的彻底卸载
        for k, v in BRAINS.items():
            if v["state"] == "WARM" and k != target:
                _full_stop(k)
        # 2) 当前活动大脑 -> 温存(留内存, 切回秒级)
        for k in [k for k, v in BRAINS.items() if v["state"] == "RUN" and k != target]:
            sleep(k)
        # 3) 目标 -> 活动(显存)
        ok = wake(target)
        return {"switched": target, "from": [k for k, v in BRAINS.items() if v["state"] == "RUN" and k != target], "ok": ok}


if __name__ == "__main__":
    print("小焦调度中心 —— 多大脑秒级切换")
    while True:
        cmd = input("> switch <brain> | state | quit > ").strip().lower()
        if cmd == "quit":
            break
        if cmd == "state":
            print(json.dumps(state(), ensure_ascii=False, indent=1))
        elif cmd.startswith("switch "):
            print(json.dumps(switch_to(cmd.split()[1]), ensure_ascii=False))
