# video_service/model_switch.py —— 显存按需切换协调器
# 状态机：idle → stop_brain → start_comfy → generating → stop_comfy → start_brain → idle
# 关键：大脑与视频模型互斥（8G 显存），先杀一个再起另一个；任何异常都尽力恢复大脑。
import os, sys, time, json, subprocess, threading

import config

_state = {"phase": "idle", "message": "", "progress": 0, "error": None, "job": None}
_lock = threading.Lock()
_comfy_proc = None


def get_state():
    # 无锁快照：直接读 dict 副本，绝不等模型切换锁(避免状态读取被长阻塞操作堵住)
    return dict(_state)


def _set(phase, message="", progress=None, error=None):
    with _lock:
        _state["phase"] = phase
        if message:
            _state["message"] = message
        if progress is not None:
            _state["progress"] = progress
        if error is not None:
            _state["error"] = error


def _pid_on_port(port):
    """用 netstat 找到占用端口的 PID（Win）。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            if ":%d" % port in line and "LISTENING" in line:
                parts = line.split()
                return int(parts[-1])
    except Exception:
        pass
    return None


def _kill_pid(pid):
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
    except Exception:
        pass


def stop_brain():
    """卸载大脑(8080 llama-server)，释放显存。"""
    _set("stop_brain", "正在卸载大脑，腾出显存…")
    pid = _pid_on_port(config.BRAIN_PORT)
    if pid:
        _kill_pid(pid)
    # 兜底：再等 3 秒确认端口释放
    for _ in range(6):
        if _pid_on_port(config.BRAIN_PORT) is None:
            break
        time.sleep(0.5)


def start_brain():
    """重新加载大脑(llama-server)，等就绪。失败不阻断（会给出提示）。"""
    _set("start_brain", "正在恢复大脑…")
    try:
        server, gguf, port, ctx = config.brain_llama()
        if not (server and gguf and os.path.exists(server) and os.path.exists(gguf)):
            _set("idle", "大脑恢复：模型路径不存在，请检查配置", error="brain_path")
            return
        subprocess.Popen([server, "-m", gguf, "--port", str(port), "-c", str(ctx)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            if _pid_on_port(port):
                break
            time.sleep(1)
        _set("idle", "大脑已恢复")
    except Exception as e:
        _set("idle", "大脑恢复失败: %s" % e, error=str(e))


def start_comfy():
    """启动 ComfyUI（视频模型），等 8188 就绪。便携版用自带 python_embeded。"""
    global _comfy_proc
    _set("start_comfy", "正在加载视频模型(ComfyUI + Wan2.1 FP8)…")
    if _pid_on_port(config.COMFY_PORT):
        return  # 已有人在跑
    main_py = os.path.join(config.COMFY_DIR, "main.py")
    if not os.path.exists(main_py):
        raise RuntimeError("找不到 ComfyUI: %s（请设 XIAOJIAO_COMFY_DIR）" % main_py)
    # 便携版优先用自己的 python_embeded（否则系统 python 缺包）
    py = sys.executable
    for cand in (os.path.join(config.COMFY_DIR, "..", "..", "python_embeded", "python.exe"),
                 os.path.join(config.COMFY_DIR, "..", "python_embeded", "python.exe"),
                 os.path.join(config.COMFY_DIR, "python_embeded", "python.exe")):
        cp = os.path.normpath(cand)
        if os.path.exists(cp):
            py = cp
            break
    _comfy_proc = subprocess.Popen(
        [py, main_py],
        cwd=config.COMFY_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):  # 最多等 4 分钟加载
        if _pid_on_port(config.COMFY_PORT):
            _set("start_comfy", "视频模型已就绪")
            return
        time.sleep(1)
    raise RuntimeError("ComfyUI 启动超时(4分钟)")


def stop_comfy():
    """卸载视频模型(杀掉 8188)，释放显存。"""
    global _comfy_proc
    _set("stop_comfy", "正在卸载视频模型…")
    pid = _pid_on_port(config.COMFY_PORT)
    _kill_pid(pid)
    if _comfy_proc:
        try:
            _comfy_proc.kill()
        except Exception:
            pass
        _comfy_proc = None
    for _ in range(10):
        if _pid_on_port(config.COMFY_PORT) is None:
            break
        time.sleep(0.5)
