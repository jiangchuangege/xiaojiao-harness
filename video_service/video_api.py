# video_service/video_api.py —— Web 扩展（Blueprint）：真·文生视频（ComfyUI + Wan2.1）
# 模型按需切换：卸载大脑 → 起 ComfyUI → 生成 → 卸载 ComfyUI → 恢复大脑（对用户透明）
# 任务状态用普通 dict 读写（GIL 安全），不持锁 —— 避免模型切换/任务线程把状态接口堵死。
import os, json, threading, datetime, time
from flask import Blueprint, request, jsonify
import config, model_switch as ms, comfy_client as cc

bp = Blueprint("video_service", __name__)
_jobs = {}
_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_jobs.json")

def _sweep():
    """清理卡死/超时(30分钟)的 switching/generating 任务，避免'请稍候'死锁。"""
    now = time.time()
    for jid, j in list(_jobs.items()):
        if j.get("state") in ("switching", "generating") and now - j.get("ts", 0) > 1800:
            j["state"] = "error"
            j["message"] = "生成超时(30分钟)，请重新生成"


def _persist():
    try:
        json.dump(_jobs, open(_JOBS_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

try:
    _jobs.update(json.load(open(_JOBS_FILE, encoding="utf-8")))
except Exception:
    pass

def _load_workflow(prompt, ckpt):
    wf = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_wan.json"), encoding="utf-8"))
    model_opt = os.environ.get("XIAOJIAO_WAN_MODEL", "wan2.6-t2v")
    for node in wf.values():
        for k, v in node.get("inputs", {}).items():
            if isinstance(v, str):
                v = v.replace("__CKPT__", ckpt).replace("__POS__", prompt).replace("__MODEL__", model_opt)
                node["inputs"][k] = v
    return wf

def _worker(job_id, prompt):
    _jobs[job_id].update(state="switching", message="卸载大脑，腾出显存…")
    _persist()
    try:
        ms.stop_brain()
        ms.start_comfy()
        _jobs[job_id].update(state="generating", message="正在生成视频…(Wan2.1 FP8)")
        ckpt = config.find_checkpoint() or "dit_fp8.safetensors"
        wf = _load_workflow(prompt, ckpt)
        pid = cc.submit_workflow(wf)
        fn, sub, ftype = cc.wait_output(pid)
        name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_wan"
        out = os.path.join(config.OUT_DIR, name + (os.path.splitext(fn)[1] or ".mp4"))
        cc.download_video(fn, sub, ftype, out)
        _jobs[job_id].update(state="switching", message="卸载视频模型…")
        ms.stop_comfy()
        ms.start_brain()
        _jobs[job_id].update(state="done", message="完成", url="/videos/" + os.path.basename(out))
    except Exception as e:
        try:
            ms.stop_comfy()
        except Exception:
            pass
        try:
            ms.start_brain()
        except Exception:
            pass
        _jobs[job_id].update(state="error", error=str(e), message="生成失败，已尽力恢复大脑")
    _persist()

@bp.route("/api/video", methods=["POST"])
def api_video():
    _sweep()
    d = request.get_json(force=True, silent=True) or {}
    prompt = (d.get("prompt") or "").strip()[:80]
    if not prompt:
        return jsonify({"ok": False, "error": "请输入场景"}), 400
    for j in list(_jobs.values()):
        if j.get("state") in ("switching", "generating"):
            return jsonify({"ok": False, "busy": True, "error": "正在生成/切换模型中，请稍候"}), 200
    job_id = datetime.datetime.now().strftime("%H%M%S%f")
    _jobs[job_id] = {"state": "queued", "prompt": prompt, "message": "排队中", "ts": time.time()}
    _persist()
    threading.Thread(target=_worker, args=(job_id, prompt), daemon=True).start()
    return jsonify({"ok": True, "job": job_id})

@bp.route("/api/video/status")
def api_video_status():
    job = request.args.get("job", "")
    j = _jobs.get(job)
    if j is None:
        return jsonify({"state": "unknown"})
    return jsonify(j)

@bp.route("/api/video/current")
def api_video_current():
    active = None
    for j in list(_jobs.values()):
        if j.get("state") in ("queued", "switching", "generating"):
            active = j; break
    if active is None:
        active = list(_jobs.values())[-1] if _jobs else None
    if active is None:
        return jsonify({"none": True})
    st = ms.get_state()
    return jsonify({"job": active, "phase": st.get("phase"), "busy": st.get("busy")})

@bp.route("/api/video/state")
def api_video_state():
    st = ms.get_state()
    busy = any(j.get("state") in ("queued", "switching", "generating") for j in _jobs.values())
    return jsonify(dict(st, busy=busy))

@bp.route("/videos/<path:name>")
def serve_video(name):
    from flask import send_from_directory
    return send_from_directory(config.OUT_DIR, name)
