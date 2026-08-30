# -*- coding: utf-8 -*-
"""🧠 大脑仓库监控面板 —— app_monitor.py
后端(Flask 蓝图)：/monitor(页面) + /api/monitor(数据) + 操作 + 调优 + 添加。
数据源：brain_manager.BRAINS + nvidia-smi + psutil + llama-swap(9292) + ComfyUI(8188)。
"""
import os, time, json, subprocess
from flask import Blueprint, jsonify, request
import brain_manager as bm

bp = Blueprint("monitor", __name__)
_LOGS = []
_ROOT = os.path.dirname(os.path.abspath(__file__))

# 可添加的"大脑模型库"(Web 上点选即加载)
MODEL_LIB = [
    {"id": "xiaojiao-chat", "type": "llama", "name": "聊天大脑 (Qwen 4B)", "vram": 4.0, "port": 8080},
    {"id": "wan-video", "type": "comfy", "name": "视频大脑 (Wan2.1)", "vram": 5.0, "port": 8188},
]
# 新大脑补全默认配置
BRAIN_CONF = {"keep_warm": False, "priority": 5, "mem_pinned": False}


def _log(action, target, note=""):
    _LOGS.insert(0, {"t": time.strftime("%H:%M:%S"), "action": action, "target": target, "note": note})
    if len(_LOGS) > 60:
        del _LOGS[60:]


def _nvidia():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=6)
        if r.returncode == 0:
            u, t = r.stdout.strip().split(",")[:2]
            return int(u), int(t)
    except Exception:
        pass
    return 0, 0


def _mem():
    """内存(used/total MB)。psutil 优先, 否则 Windows ctypes。"""
    try:
        import psutil
        v = psutil.virtual_memory()
        return int(v.used / 1048576), int(v.total / 1048576)
    except Exception:
        pass
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int((m.ullTotalPhys - m.ullAvailPhys) / 1048576), int(m.ullTotalPhys / 1048576)
    except Exception:
        return 0, 0


def _llama():
    """聊天大脑: 端口9292在=在线; 模型加载看 /api/models。"""
    try:
        import requests
        d = requests.get("http://127.0.0.1:9292/api/models", timeout=3).json()
        out = [m.get("id") for m in d.get("data", [])]
        loaded = bool(d.get("data"))
        st = "运行中" if loaded else "空闲"
        return {"models": out, "state": st, "loaded": loaded}
    except Exception:
        # 端口在但接口没数据 -> 视为在线
        try:
            import socket
            socket.create_connection(("127.0.0.1", 9292), 0.8).close()
            return {"models": [], "state": "运行中", "loaded": True}
        except Exception:
            return {"models": [], "state": "空闲", "loaded": False}


def _comfy():
    try:
        import requests
        q = requests.get("http://127.0.0.1:8188/queue", timeout=3).json()
        n = len(q.get("queue_running", []) or [])
        return {"running": n, "state": "运行中" if n else "温存"}
    except Exception:
        return {"running": 0, "state": "空闲"}


def _card(key, b):
    conf = dict(BRAIN_CONF)
    conf.update(b.get("conf", {}))
    status, extra, task = b.get("state", "空闲"), "", ""
    if key == "chat":
        L = _llama(); status = L["state"]; task = "llama-swap " + L["state"]; extra = "已挂内存" if L["loaded"] else "可加载"
    elif key == "video":
        C = _comfy(); status = C["state"]; task = "ComfyUI 队列 %s" % C["running"]; extra = "已挂内存" if status == "温存" else "未加载"
    if status == "RUN":
        status = "运行中"
    return {"key": key, "name": b.get("name", key), "type": b.get("type", ""), "port": b.get("port", ""),
            "state": status, "task": task, "extra": extra, "vram_gb": b.get("vram_gb", 0),
            "conf": conf, "log": _LOGS[:8]}


@bp.route("/monitor")
def monitor_page():
    from flask import send_from_directory
    return send_from_directory(_ROOT, "monitor.html")


@bp.route("/api/monitor")
def api_monitor():
    vru, vrt = _nvidia(); mu, mt = _mem()
    brains = [_card(k, b) for k, b in bm.BRAINS.items()]
    online = sum(1 for x in brains if x["state"] == "运行中")
    warm = sum(1 for x in brains if x["state"] == "温存")
    return jsonify({"brains": brains, "vram_used": vru, "vram_total": vrt, "mem_used": mu, "mem_total": mt,
                    "total": len(brains), "online": online, "warm": warm, "logs": _LOGS[:30], "lib": MODEL_LIB})


@bp.route("/api/monitor/op", methods=["POST"])
def api_monitor_op():
    d = request.get_json(force=True, silent=True) or {}
    op, tgt = d.get("op", ""), d.get("target", "")
    note = ""
    try:
        if op == "switch":
            r = bm.switch_to(tgt); note = "→ %s" % r.get("switched", tgt)
        elif op == "wake":
            bm.wake(tgt); note = "唤醒 %s" % tgt
        elif op == "release":
            bm.sleep(tgt); note = "释放 %s" % tgt
        elif op == "restart":
            bm.sleep(tgt); bm.wake(tgt); note = "重启 %s" % tgt
        elif op == "clearWarm":
            for k in list(bm.BRAINS.keys()):
                if bm.BRAINS[k].get("state") == "SLEEP":
                    bm.sleep(k)
            note = "已清理温存模型"
        elif op == "clearVram":
            try:
                import requests
                requests.post("http://127.0.0.1:8188/free", json={"unload_models": True, "free_memory": True}, timeout=8)
                requests.post("http://127.0.0.1:9292/api/models/unload/xiaojiao", timeout=8)
            except Exception:
                pass
            note = "已紧急清空显存"
        elif op == "add":
            name = d.get("name") or tgt
            btype = d.get("type") or "comfy"
            ppath = d.get("path") or ""
            pid = d.get("id") or name
            if pid in bm.BRAINS:
                note = "大脑已存在: %s" % pid
            elif btype == "comfy":
                bm.BRAINS.setdefault(pid, {"name": name, "type": "comfy", "port": d.get("port", 8188), "vram_gb": d.get("vram", 5.0), "state": "OFF", "conf": dict(BRAIN_CONF), "path": ppath})
                bm.wake(pid); note = "已添加并加载 %s" % name
            else:
                note = "仅支持添加视频/ComfyUI 类大脑(路径:%s)" % (ppath or "-")
        elif op == "tune":
            # 调优: 改 keep_warm / priority / mem_pinned
            conf = d.get("conf", {})
            b = bm.BRAINS.get(tgt)
            if b:
                b.setdefault("conf", {}).update(conf)
                note = "调优 %s → %s" % (tgt, json.dumps(conf, ensure_ascii=False))
                import json as _j
                _save_conf(tgt, conf)
        _log(op, tgt, note)
        return jsonify({"ok": True, "note": note})
    except Exception as e:
        _log(op, tgt, str(e)); return jsonify({"ok": False, "error": str(e)}), 400


def _save_conf(key, conf):
    """把调优配置写回 xiaojiao_control.json(brain.<key>.conf)。"""
    try:
        cf = os.path.join(_ROOT, "xiaojiao_control.json")
        d = json.load(open(cf, encoding="utf-8"))
        d.setdefault("brain", {}).setdefault(key, {}).setdefault("conf", {}).update(conf)
        json.dump(d, open(cf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass
