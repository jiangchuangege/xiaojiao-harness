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
        if j.get("state") in ("switching", "generating") and now - j.get("ts", 0) > 600:
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


def _kb():
    """小脑「电影设计提示词学习库」(向量库, self_learn/vstore)。"""
    try:
        import sys as _sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _sys.path.insert(0, os.path.join(root, "self_learn"))
        import vstore
        return vstore
    except Exception:
        return None


def _refine_prompt(raw, save=True):
    """用小焦大脑精炼出电影级提示词。①先查小脑学习库(向量库命中=用学过的) ②大脑精炼并学进库 ③模板兜底。绝不给用户看思考过程。"""
    raw = (raw or "").strip()
    if not raw: return raw
    V = _kb()
    if V:
        try:
            r = V.search("用户: " + raw, k=1, threshold=0.35)
            if r["hit"] and r["best"][1]:
                best = r["best"][1]
                if "-> 精炼: " in best:
                    best = best.split("-> 精炼: ", 1)[1]
                return best
        except Exception:
            pass
    try:
        import json as _j, requests as _r, re as _re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = _j.load(open(os.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
        base = (cfg.get("brain", {}).get("api", {}).get("base_url") or "http://127.0.0.1:8080/v1")
        sys_p = ("你是专业电影导演。把用户的视频描述**简洁**改写成一段英文电影提示词(主体/光线/镜头/风格)，不超过 80 字。只输出提示词，不要解释、不要引号、不要思考。")
        r = _r.post(base.rstrip("/") + "/chat/completions",
                    json={"model": "xiaojiao1.0-4B",
                          "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": raw}],
                          "max_tokens": 400, "temperature": 0.6, "chat_template_kwargs": {"enable_thinking": False}},
                    timeout=6)
        if r.status_code == 200:
            msg = r.json()["choices"][0].get("message", {})
            out = (msg.get("content") or "").strip()
            if not out:
                rc = (msg.get("reasoning_content") or "")
                drafts = _re.findall(r"(?:Draft|Final Prompt|最终提示词|结果|Final)[^\\n]*?[:：]\\s*([^\\n]{8,})", rc)
                if drafts:
                    out = drafts[-1].strip()
                else:
                    lines = [l.strip() for l in rc.split("\\n") if l.strip() and len(l.strip()) > 8]
                    out = lines[-1] if lines else ""
            if out:
                if len(out) > 300: out = out[-300:]
                if save:
                    V = _kb()
                    if V:
                        try:
                            V.add("用户: %s -> 精炼: %s" % (raw, out), tag="video_prompt")
                        except Exception:
                            pass
                return out
    except Exception:
        pass
    # 模板兜底(秒出): 加电影级修饰词
    return raw + ", cinematic, high detail, dramatic lighting, smooth motion, film quality"


def _worker(job_id, prompt):
    confirmed = _jobs[job_id].get("confirmed_refined") or ""
    _jobs[job_id].update(state="switching", message="精炼提示词…")
    _persist()
    refined = confirmed or _refine_prompt(prompt)
    _jobs[job_id]["prompt"] = prompt
    _jobs[job_id]["refined_prompt"] = refined
    _jobs[job_id].update(state="switching", message="卸载大脑，腾出显存…")
    _persist()
    try:
        ms.stop_brain()
        prompt = refined
        ms.start_comfy()
        _jobs[job_id].update(state="generating", message="正在生成视频…(Wan2.1 FP8)")
        ckpt = config.find_checkpoint() or "dit_fp8.safetensors"
        wf = _load_workflow(prompt, ckpt)
        pid = cc.submit_workflow(wf)
        def _prog(value, maxv):
            try:
                _jobs[job_id]["progress"] = {"value": value, "max": maxv}
            except Exception:
                pass
        fn, sub, ftype = cc.wait_output(pid, progress_cb=_prog)
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

@bp.route("/api/video/refine")
def api_video_refine():
    """只精炼提示词(不学习/不切换)——供前端"确认提示词"流程用。"""
    prompt = (request.args.get("prompt") or "").strip()[:80] or (request.get_json(silent=True) or {}).get("prompt", "")
    refined = _refine_prompt(prompt, save=False)
    # 中文大意(给用户看懂): 简单概括原始场景
    zh = prompt
    def _zh(_p):
        try:
            import json as _j, requests as _r
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cfg = _j.load(open(os.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
            base = (cfg.get("brain", {}).get("api", {}).get("base_url") or "http://127.0.0.1:8080/v1")
            r = _r.post(base.rstrip("/") + "/chat/completions", json={"model":"xiaojiao1.0-4B","messages":[{"role":"system","content":"把这段英文视频提示词翻译成一句简洁中文（只说中文，不要英文、不要解释）。"},{"role":"user","content":_p}],"max_tokens":60,"temperature":0.3,"chat_template_kwargs":{"enable_thinking":False}}, timeout=8)
            if r.status_code == 200:
                c = (r.json()["choices"][0].get("message",{}).get("content") or "").strip()
                return c or _p
        except Exception:
            pass
        return _p
    zh = _zh(refined) if len(refined) > 15 else prompt
    return jsonify({"ok": True, "refined": refined, "zh": zh})


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

@bp.route("/api/video/promptkb")
def api_video_promptkb():
    """小脑「电影设计提示词学习库」统计+最近学到的(给用户看学了什么)。"""
    V = _kb(); n = 0; recent = []
    if V:
        try:
            import os as _o, json as _j
            data = _j.load(open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "knowledge_vec.json"), encoding="utf-8"))
            for e in data.get("entries", []):
                if e.get("tag") == "video_prompt":
                    n += 1
                    recent.append(e.get("text", ""))
            recent = recent[-6:][::-1]
        except Exception:
            pass
    return jsonify({"count": n, "recent": recent})


@bp.route("/api/video/state")
def api_video_state():
    st = ms.get_state()
    busy = any(j.get("state") in ("queued", "switching", "generating") for j in _jobs.values())
    return jsonify(dict(st, busy=busy))

@bp.route("/videos/<path:name>")
def serve_video(name):
    from flask import send_from_directory
    return send_from_directory(config.OUT_DIR, name)


@bp.route("/media/<path:name>")
def serve_media(name):
    from flask import send_from_directory
    media_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")
    return send_from_directory(media_dir, name)
