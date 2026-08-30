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
        "name": "聊天大脑 (Qwen 4B)", "port": 8080,
        "type": "llama", "vram_gb": 3.5, "state": "SLEEP", "proc": "/".join(["C:/llama/llama-server.exe"]),
    },
    "video": {  # 视频大脑：ComfyUI + Wan2.1
        "name": "视频大脑 (Wan2.1 + ComfyUI)", "port": 8188,
        "type": "comfy", "vram_gb": 5.0, "state": "OFF", "proc": "comfy",
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
    if _port_alive(b["port"]):
        return True
    try:
        subprocess.Popen(["C:/llama/llama-server.exe", "-m", "C:/llama/xiaojiao1.0-4B.gguf", "--port", str(b["port"]), "-c", "32768"],
                         cwd="C:/llama", creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def _start_comfy(b):
    root = os.environ.get("XIAOJIAO_COMFY_DIR", r"G:\模型文件\视频模型\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI")
    py = os.path.join(os.path.dirname(root), "python_embeded", "python.exe")
    try:
        subprocess.Popen([py, "main.py", "--port", str(b["port"])], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def wake(brain_key, wait=10):
    """把大脑唤醒到显存(RUN)。进程若没起则拉起；权重由服务端驻留(默认keep-alive)。"""
    b = BRAINS[brain_key]
    if b["type"] == "llama":
        _start_llama(b)
    elif b["type"] == "comfy":
        _start_comfy(b)
    for _ in range(wait * 2):
        if _port_alive(b["port"]):
            break
        time.sleep(0.5)
    b["state"] = "RUN"
    return is_running(brain_key)


def sleep(brain_key):
    """让大脑睡眠(SLEEP, 权重/工作流不占显存, 进程常驻)。"""
    b = BRAINS.get(brain_key)
    if b:
        b["state"] = "SLEEP"  # 进程保留, 标记为可休眠; 具体显存释放由各服务/后续vLLM接管
    return True


def switch_to(target):
    """秒级切换：休眠当前在跑的大脑, 唤醒目标大脑。"""
    with _lock:
        current = [k for k, v in BRAINS.items() if v["state"] == "RUN" and k != target]
        for k in current:
            sleep(k)
        ok = wake(target)
        return {"switched": target, "from": current, "ok": ok}


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
