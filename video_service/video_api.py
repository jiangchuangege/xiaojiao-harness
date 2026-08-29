# video_service/video_api.py —— Web 扩展（Blueprint）：真·文生视频（ComfyUI + Wan2.1）
# 模型按需切换：卸载大脑 → 起 ComfyUI → 生成 → 卸载 ComfyUI → 恢复大脑（对用户透明）
import os, json, threading, datetime

from flask import Blueprint, request, jsonify

import config
import model_switch as ms
import comfy_client as cc

bp = Blueprint("video_service", __name__)
_jobs = {}  # job_id -> state
_jobs_lock = threading.Lock()
_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_jobs.json")


def _persist():
    try:
        with _jobs_lock:
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
    def upd(**kw):
        with _jobs_lock:
            _jobs[job_id].update(kw)
    upd(state="switching", message="卸载大脑，腾出显存…")
    _persist()
    try:
        # a. 卸载大脑(释放显存)
        ms.stop_brain()
        # b. 加载视频模型(ComfyUI)
        ms.start_comfy()
        upd(state="generating", message="正在生成视频…(Wan2.1 FP8)")
        # 提交工作流 + 轮询
        ckpt = config.find_checkpoint() or "wan2.1-t2v-1.3b-fp8.safetensors"
        wf = _load_workflow(prompt, ckpt)
        pid = cc.submit_workflow(wf)
        fn, sub, ftype = cc.wait_output(pid, progress_cb=lambda: upd(progress=0))
        # 下载到本地 videos/
        name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_wan"
        ext = os.path.splitext(fn)[1] or ".mp4"
        out = os.path.join(config.OUT_DIR, name + ext)
        cc.download_video(fn, sub, ftype, out)
        # d. 卸载视频模型
        upd(state="switching", message="卸载视频模型…")
        ms.stop_comfy()
        # e. 恢复大脑
        ms.start_brain()
        upd(state="done", message="完成", url="/videos/" + os.path.basename(out))
        _persist()
    except Exception as e:
        # 异常兜底：无论如何尽量恢复大脑
        try:
            ms.stop_comfy()
        except Exception:
            pass
        try:
            ms.start_brain()
        except Exception:
            pass
        upd(state="error", error=str(e), message="生成失败，已尽力恢复大脑")
        _persist()


@bp.route("/api/video", methods=["POST"])
def api_video():
    d = request.get_json(force=True, silent=True) or {}
    prompt = (d.get("prompt") or "").strip()[:80]
    if not prompt:
        return jsonify({"ok": False, "error": "请输入场景"}), 400
    # 忙则拒绝（防同时生成/切换）
    with _jobs_lock:
        for j in _jobs.values():
            if j.get("state") in ("switching", "generating"):
                return jsonify({"ok": False, "busy": True, "error": "正在生成/切换模型中，请稍候"}), 200
        job_id = datetime.datetime.now().strftime("%H%M%S%f")
        _jobs[job_id] = {"state": "queued", "prompt": prompt, "message": "排队中"}
        _persist()
    threading.Thread(target=_worker, args=(job_id, prompt), daemon=True).start()
    return jsonify({"ok": True, "job": job_id})


@bp.route("/api/video/status")
def api_video_status():
    job = request.args.get("job", "")
    with _jobs_lock:
        j = _jobs.get(job)
        if j is None:
            return jsonify({"state": "unknown"})
        return jsonify(j)



@bp.route("/api/video/current")
def api_video_current():
    """返回当前活跃任务(服务器全局，跨浏览器/刷新/重启都能拿到)。"""
    with _jobs_lock:
        active = None
        for j in list(_jobs.values()):
            if j.get("state") in ("queued", "switching", "generating"):
                active = j; break
        if active is None:
            # 返回最近一个(可能是done/error,便于展示上个结果)
            active = list(_jobs.values())[-1] if _jobs else None
    if active is None:
        return jsonify({"none": True})
    st = ms.get_state()
    return jsonify({"job": active, "phase": st.get("phase"), "busy": st.get("busy")})

@bp.route("/api/video/state")
def api_video_state():
    """整体切换状态（前端可显示"正在切换模型"）。"""
    st = ms.get_state()
    st["busy"] = any(j.get("state") in ("switching", "generating") for j in _jobs.values())
    return jsonify(st)


@bp.route("/videos/<path:name>")
def serve_video(name):
    from flask import send_from_directory
    return send_from_directory(config.OUT_DIR, name)
