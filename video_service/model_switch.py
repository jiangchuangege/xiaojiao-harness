# video_service/model_switch.py —— 显存按需切换协调器
# 状态机：idle → stop_brain → start_comfy → generating → stop_comfy → start_brain → idle
# 关键：大脑与视频模型互斥（8G 显存），先杀一个再起另一个；任何异常都尽力恢复大脑。
import os, sys, time, json, subprocess, threading

import config

_state = {"phase": "idle", "message": "", "progress": 0, "error": None, "job": None}
_lock = threading.Lock()
_comfy_proc = None


def _keep_warm():
    """读操控文件 brain.keep_warm：是否常驻视频模型/不重启ComfyUI。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json as _j
        d = _j.load(open(os.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
        return bool(d.get("brain", {}).get("keep_warm", False))
    except Exception:
        return False


def _llama_swap_url():
    """llama-swap 管理接口地址(多大脑秒级切换)。"""
    import os as _o
    try:
        root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
        import json as _j
        d = _j.load(open(_o.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
        p_ = d.get("brain", {}).get("llama_swap_port", 9292)
        return "http://127.0.0.1:%d" % p_
    except Exception:
        return "http://127.0.0.1:9292"


def _pid_on_port(port):
    try:
        import subprocess as _sp
        out = _sp.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if ":%d " % port in line and "LISTENING" in line:
                try:
                    return int(line.strip().split()[-1])
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _llama_swap_unload(model_id="xiaojiao"):
    """让 llama-swap 卸载一个模型(释放显存,进程常驻)。"""
    import requests as _r
    try:
        r = _r.post(_llama_swap_url() + "/api/models/unload/" + model_id, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


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
    """卸载大脑(腾显存)。优先走 llama-swap unload(秒级,进程常驻)；否则兜底杀进程。"""
    _set("stop_brain", "正在卸载大脑，腾出显存…")
    # ① 用 llama-swap 卸载(常驻进程,秒级)
    if _llama_swap_unload("xiaojiao"):
        # 等上游端口释放
        for _ in range(8):
            time.sleep(0.5)
        _set("idle", "大脑已卸载(llama-swap)")
        return
    # ② 兜底：直接杀 8080 llama-server
    pid = _pid_on_port(config.BRAIN_PORT)
    if pid:
        _kill_pid(pid)
    for _ in range(6):
        if _pid_on_port(config.BRAIN_PORT) is None:
            break
        time.sleep(0.5)


def start_brain():
    """恢复聊天大脑。走 llama-swap(常驻,自动加载)；llama-swap 不在则直接起 llama-server 兜底。"""
    _set("start_brain", "正在恢复大脑…")
    import requests as _r
    swap = _llama_swap_url()
    try:
        if _r.get(swap + "/health", timeout=5).status_code == 200:
            # llama-swap 在 -> 触发一次请求让 xiaojiao 模型自动加载(首个请求会加载)
            server, gguf, port, ctx = config.brain_llama()
            _r.get(swap + "/api/profiles", timeout=10)
            _set("idle", "大脑已恢复(llama-swap)")
            return
    except Exception:
        pass
    # 兜底：直接起 llama-server
    try:
        server, gguf, port, ctx = config.brain_llama()
        if not (server and gguf and os.path.exists(server) and os.path.exists(gguf)):
            _set("idle", "大脑恢复：模型路径不存在", error="brain_path")
            return
        subprocess.Popen([server, "-m", gguf, "--port", str(port), "-c", str(ctx)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            if _pid_on_port(port):
                break
            time.sleep(1)
        _set("idle", "大脑已恢复(直接启动)")
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
    # keep_warm/lowvram: 让 ComfyUI 自动把模型权重放内存, 聊天时自动睡眠(Wan), ComfyUI 进程常驻
    lowvram = ["--lowvram"] if (_keep_warm() or os.environ.get("XIAOJIAO_KEEP_COMFY")=="1") else []
    _comfy_proc = subprocess.Popen(
        [py, main_py, "--port", str(config.COMFY_PORT)] + lowvram,
        cwd=config.COMFY_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):  # 最多等 4 分钟加载
        if _pid_on_port(config.COMFY_PORT):
            _set("start_comfy", "视频模型已就绪")
            return
        time.sleep(1)
    raise RuntimeError("ComfyUI 启动超时(4分钟)")


def stop_comfy():
    """卸载视频模型(杀掉 8188)，释放显存。keep_warm 时保留ComfyUI进程/Wan模型常驻,不杀(视频秒级)。"""
    global _comfy_proc
    if _keep_warm() and _pid_on_port(config.COMFY_PORT):
        _set("idle", "视频模型常驻(keep_warm), 不上传…")
        if _comfy_proc:
            _comfy_proc = None
        return
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
