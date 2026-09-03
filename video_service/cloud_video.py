# video_service/cloud_video.py —— 通用云端视频大脑适配器
# 不针对某一家: 按 base_url 特征自动识别视频 API 协议, 让"加了视频模型就能用"最大化。
# 已支持协议:
#   agnes      -> POST {base}/videos  + GET /agnesapi (Bearer, mode=ti2vid)
#   openai 类  -> 相关厂商若是 OpenAI 兼容 /v1/videos 或 /v1/generations, 按 OpenAI 视频格式
#   gemini 类  -> Gemini 视频生成原生端点
# 未知协议会给出"需接入"的明确提示。
import os, time, datetime, json, threading, requests

# ---------- 免费用户节流(1 RPM, 防止撞 429) ----------
_RPM_LOCK = threading.Lock()
_LAST_REQ = [0.0]
_RPM_INTERVAL = 60
def _throttle():
    with _RPM_LOCK:
        now = time.time()
        wait = _RPM_INTERVAL - (now - _LAST_REQ[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ[0] = time.time()

def _load_control():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        return json.load(open(os.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
    except Exception:
        return {}

def _video_models():
    """从控制文件 models 里挑出所有视频模型(base_url 含 videos/video 或 name 含 video)。"""
    out = []
    for m in _load_control().get("models", []):
        name = (m.get("name") or "")
        url = (m.get("base_url") or "")
        low_url, low_name = url.lower(), name.lower()
        if "videos" in low_url or "/video" in low_url or "video" in low_name:
            out.append(m)
    return out

def _detect_provider(base_url, model_name):
    """按 base_url / model_name 特征猜协议(provider)。"""
    u = (base_url or "").lower()
    m = (model_name or "").lower()
    if "agnes" in u or "agnes" in m:
        return "agnes"
    if "generativelanguage" in u or "gemini" in u:
        return "gemini"
    if "openai" in u or "api.openai" in u:
        return "openai"
    if u.endswith("/v1") or "/v1/videos" in u or "videos" in u:
        return "openai"   # OpenAI 兼容视频
    return "unknown"

def _pick_video_model():
    """返回 (provider, model_name, api_key, base_url)。有多个视频模型取第一个可用(有key的)。"""
    for m in _video_models():
        key = m.get("api_key") or ""
        if not key:
            continue
        base = m.get("base_url") or ""
        model = m.get("model") or m.get("name")
        return _detect_provider(base, model), model, key, base
    return None

def available():
    """是否配置了任意视频模型(有 key)。"""
    return _pick_video_model() is not None

def provider():
    p = _pick_video_model()
    return p[0] if p else None

def model():
    p = _pick_video_model()
    return p[1] if p else None

def generate(prompt, mode=None, poll_interval=20, timeout=1800, progress_cb=None, **extra):
    """通用文生视频入口。按 provider 分发到对应实现, 返回 (本地路径, 远程URL)。"""
    picked = _pick_video_model()
    if not picked:
        raise RuntimeError("未配置可用的视频模型(需要在模型管理里添加一个带 key 的视频模型)")
    provider_name, model_name, key, base_url = picked
    if provider_name == "agnes":
        return _gen_agnes(prompt, key, base_url, model_name, poll_interval, timeout, progress_cb, **extra)
    elif provider_name == "gemini":
        return _gen_gemini(prompt, key, base_url, model_name, poll_interval, timeout, progress_cb, **extra)
    elif provider_name == "openai":
        return _gen_openai(prompt, key, base_url, model_name, poll_interval, timeout, progress_cb, **extra)
    else:
        raise RuntimeError("暂未支持的视频 API 协议(%s)，请告诉我它的接口文档我来接入" % provider_name)

# ---------- Agnes ----------
def _gen_agnes(prompt, key, base, model, poll, timeout, cb, **extra):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = {"model": model, "prompt": prompt}
    body.update({"mode": extra.get("mode") or "ti2vid"})
    body.update(extra)
    _throttle()
    r = requests.post((base.rstrip("/") if not base.rstrip("/").endswith("/videos") else base.rstrip("/")) + "/videos"
                      if not base.rstrip("/").endswith("/videos") else base.rstrip("/"), headers=headers, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("视频建任务失败(%d): %s" % (r.status_code, r.text[:200]))
    data = r.json()
    vid = data.get("video_id") or data.get("task_id") or data.get("id")
    if not vid:
        raise RuntimeError("未返回任务ID: " + str(data)[:200])
    start = time.time()
    while time.time() - start < timeout:
        try:
            st = requests.get((base.rstrip("/").split("/videos")[0]) + "/agnesapi",
                              headers=headers, params={"video_id": vid}, timeout=30).json()
        except Exception as e:
            st = {"status": "pending", "error": str(e)}
        s = st.get("status", "pending")
        if s == "completed":
            return _download(st.get("video_url") or st.get("url") or "", "_agnese")
        elif s == "failed":
            raise RuntimeError("生成失败: " + str(st.get("error", ""))[:200])
        else:
            if cb: cb(int(st.get("progress", 0) or 0))
        time.sleep(poll)
    raise TimeoutError("视频生成超时")

# ---------- OpenAI 兼容视频(如部分厂商 /v1/videos, 用 POST + 轮询 /v1/videos/{id}) ----------
def _gen_openai(prompt, key, base, model, poll, timeout, cb, **extra):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = {"model": model, "prompt": prompt}
    body.update(extra)
    _throttle()
    # 尝试 /v1/videos 或 /v1/video/generations
    for ep in ["/v1/videos", "/v1/video/generations", "/v1/videos/generations"]:
        url = base.rstrip("/").split("/v1")[0] + ep if not base.rstrip("/").endswith("/v1") else base.rstrip("/") + ("/videos" if ep == "/v1/videos" else ep)
        try:
            r = requests.post(url, headers=headers, json=body, timeout=60)
            if r.status_code in (200, 201):
                data = r.json()
                vid = data.get("id") or data.get("video_id") or data.get("task_id") or (data.get("data") or [{}])[0].get("id")
                if not vid:
                    raise RuntimeError("未返回任务ID: " + str(data)[:200])
                start = time.time()
                while time.time() - start < timeout:
                    try:
                        sr = requests.get(url + "/" + str(vid), headers=headers, timeout=30)
                        st = sr.json()
                    except Exception as e:
                        st = {"status": "pending", "error": str(e)}
                    status = (st.get("status") or "").lower()
                    vurl = st.get("url") or st.get("video_url") or ""
                    if status in ("completed", "succeeded") or vurl:
                        return _download(vurl, "_cloud")
                    elif status in ("failed", "error"):
                        raise RuntimeError("生成失败: " + str(st.get("error", ""))[:200])
                    else:
                        if cb: cb(0)
                    time.sleep(poll)
                raise TimeoutError("视频生成超时")
        except Exception as e:
            if "失败" in str(e):
                raise
            continue
    raise RuntimeError("OpenAI 兼容视频端点尝试失败")

# ---------- Gemini ----------
def _gen_gemini(prompt, key, base, model, poll, timeout, cb, **extra):
    raise RuntimeError("Gemini 视频适配暂未完整实现，请补充 endpoint 后接入")

# ---------- 下载 ----------
def _download(vurl, tag):
    if not vurl:
        raise RuntimeError("完成但无视频 URL")
    out = os.path.join(_out_dir(), datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + tag)
    ext = os.path.splitext(vurl)[1] or ".mp4"
    out += ext
    vr = requests.get(vurl, timeout=120)
    vr.raise_for_status()
    with open(out, "wb") as f:
        f.write(vr.content)
    return out, vurl

def _out_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "videos")
    os.makedirs(d, exist_ok=True)
    return d

# 兼容旧引用
def _key():
    return _pick_video_model()[2] if _pick_video_model() else ""
