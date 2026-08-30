# -*- coding: utf-8 -*-
"""🧠 大脑仓库监控面板 —— app_monitor.py
后端(Flask 蓝图)：/monitor(页面) + /api/monitor(数据) + 操作接口。
集成到现有 5000 端口：在 xiaojiao_app.py 里 import 并 register_blueprint。
数据源：brain_manager.BRAINS + nvidia-smi + psutil + llama-swap(9292) + ComfyUI(8188)。
"""
import os, time, json, subprocess, threading
from flask import Blueprint, jsonify, request

import brain_manager as bm

bp = Blueprint("monitor", __name__)
_LOGS = []          # 操作日志
_OPS = {}           # 每脑操作记录
_ROOT = os.path.dirname(os.path.abspath(__file__))


def _log(action, target, note=""):
    _LOGS.insert(0, {"t": time.strftime("%H:%M:%S"), "action": action, "target": target, "note": note})
    if len(_LOGS) > 60:
        del _LOGS[60:]


def _nvidia_vram():
    """nvidia-smi 读显存(used/total MB)。"""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=6)
        if r.returncode == 0:
            used, total = r.stdout.strip().split(",")[:2]
            return int(used), int(total)
    except Exception:
        pass
    return 0, 0


def _sys_mem():
    """psutil 读内存(used/total MB)。"""
    try:
        import psutil
        v = psutil.virtual_memory()
        return int(v.used / 1048576), int(v.total / 1048576)
    except Exception:
        return 0, 0


def _llama_models():
    """llama-swap 已加载模型(聊天大脑)。"""
    try:
        import requests
        r = requests.get("http://127.0.0.1:9292/api/models", timeout=3).json()
        out = []
        for m in r.get("data", []):
            st = (m.get("status") or {})
            out.append({"id": m.get("id"), "state": "运行中" if "loaded" in str(st.get("value")) else "温存", "vram": st.get("vram")})
        return out
    except Exception:
        return []


def _comfy_status():
    """ComfyUI 状态(视频大脑)。"""
    try:
        import requests
        q = requests.get("http://127.0.0.1:8188/queue", timeout=3).json()
        run = len(q.get("queue_running", []) or [])
        return {"running": run, "state": "运行中" if run else "温存"}
    except Exception:
        return {"running": 0, "state": "空闲"}


def _brain_card(key, b):
    """把 BRAINS 注册表条目转成面板卡片数据。"""
    status = b.get("state", "空闲")
    if status == "RUN":
        status = "运行中"
    elif status == "SLEEP":
        status = "温存"
    else:
        status = "空闲"
    extra = ""
    if key == "chat":
        lm = _llama_models()
        extra = "llama-swap: " + (lm[0]["state"] if lm else "未加载")
        status = lm[0]["state"] if lm else status
    elif key == "video":
        cs = _comfy_status()
        status = cs["state"]
        extra = "ComfyUI 队列: %s" % cs["running"]
    gb = b.get("vram_gb", 0)
    return {"name": b.get("name", key), "key": key, "type": b.get("type", ""),
            "state": status, "vram_gb": gb, "task": extra, "port": b.get("port", "")}


@bp.route("/monitor")
def monitor_page():
    from flask import send_from_directory
    return send_from_directory(_ROOT, "monitor.html")


@bp.route("/api/monitor", methods=["GET"])
def api_monitor():
    """面板数据：大脑清单 + 全局统计。"""
    vru, vrt = _nvidia_vram()
    mu, mt = _sys_mem()
    brains = []
    for k, b in bm.BRAINS.items():
        brains.append(_brain_card(k, b))
    online = sum(1 for x in brains if x["state"] == "运行中")
    warm = sum(1 for x in brains if x["state"] == "温存")
    return jsonify({
        "brains": brains,
        "vram_used": vru, "vram_total": vrt,
        "mem_used": mu, "mem_total": mt,
        "total": len(brains), "online": online, "warm": warm,
        "logs": _LOGS[:30],
    })


@bp.route("/api/monitor/op", methods=["POST"])
def api_monitor_op():
    """操作：switch/wake/release/clearWarm/clearVram/restart/add。"""
    d = request.get_json(force=True, silent=True) or {}
    op = d.get("op"); tgt = d.get("target", "")
    note = ""
    try:
        if op == "switch":
            r = bm.switch_to(tgt)
            note = "→ %s" % r.get("switched", tgt)
        elif op == "wake":
            bm.wake(tgt); note = "唤醒 %s" % tgt
        elif op in ("release", "sleep"):
            bm.sleep(tgt); note = "释放 %s" % tgt
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
        elif op == "restart":
            bm.sleep(tgt); bm.wake(tgt); note = "重启 %s" % tgt
        elif op == "add":
            name = d.get("name", "unamed")
            bm.BRAINS.setdefault(name, {"name": name, "type": "comfy", "port": d.get("port", 8189), "vram_gb": d.get("vram", 4.0), "state": "OFF"})
            note = "新增大脑 %s" % name
        _log(op, tgt, note)
        return jsonify({"ok": True, "note": note})
    except Exception as e:
        _log(op, tgt, str(e))
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/api/monitor/report", methods=["POST"])
def api_monitor_report():
    """上报某大脑的显存/内存/时长(前端每2秒回写, 用于趋势)。"""
    d = request.get_json(force=True, silent=True) or {}
    key = d.get("key", "")
    _OPS[key] = {"vram": d.get("vram"), "mem": d.get("mem"), "t": time.time()}
    return jsonify({"ok": True})
