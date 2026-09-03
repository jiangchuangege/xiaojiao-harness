# video_service/agenes.py —— Agnes 云端文生视频大脑(免费 API, 不占本地显存)
# 用法: XIAOJIAO_AGNES_KEY=<platform.agnes-ai.com 领取的 key>, 自动识别 agnes-video-2.5-flash
# 对接: 无 key 则不启用, 视频生成回落到本地 ComfyUI(Wan2.1)。
import os, time, datetime, requests, threading

BASE = "https://apihub.agnes-ai.com"
_DEFAULT_MODEL = "agnes-video-2.5-flash"

# 免费用户视频 = 1 RPM(每分钟1次请求)。用一个进程内锁 + 时间戳节流, 避免连续调用撞429。
_RPM_LOCK = threading.Lock()
_LAST_REQ = [0.0]   # 上次发请求的时间
_RPM_INTERVAL = 60  # 秒, 对应 1 RPM
# 建任务前的"准备"动作(可用于等待上一任务完成, 避免同 key 并行撞 RPM)
_pre_tasks = []

def _throttle():
    """确保两次 POST 之间至少间隔 RPM_INTERVAL 秒(1 RPM), 防止 429。"""
    with _RPM_LOCK:
        now = time.time()
        wait = _RPM_INTERVAL - (now - _LAST_REQ[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ[0] = time.time()

def _key():
    # 环境变量 XIAOJIAO_AGNES_KEY 优先; 其次从 control 的 models 里找 agnes 系列读 api_key
    k = os.environ.get("XIAOJIAO_AGNES_KEY", "")
    if k:
        return k
    try:
        import json, os as _o
        root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
        d = json.load(open(_o.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
        # 1) brain.agnese.key
        v = (d.get("brain", {}).get("agnese", {}).get("key")
             or d.get("brain", {}).get("video_agnese", {}).get("key"))
        if v:
            return v
        # 2) 遍历 models: 优先 base_url 含 /videos 的(真视频模型), 其次 name 含 video 的
        for m in (d.get("models") or []):
            url = (m.get("base_url") or "").lower()
            if "videos" in url:
                return m.get("api_key", "")
        for m in (d.get("models") or []):
            name = (m.get("name") or "").lower()
            if "agnes" in name and "video" in name:
                return m.get("api_key", "")
        return ""
    except Exception:
        return ""

def available():
    """是否配置了 Agnes key。"""
    return bool(_key())

def model():
    try:
        import json, os as _o
        root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
        d = json.load(open(_o.path.join(root, "xiaojiao_control.json"), encoding="utf-8"))
        # brain.agnese.model 优先
        v = (d.get("brain", {}).get("agnese", {}).get("model")
             or d.get("brain", {}).get("video_agnese", {}).get("model"))
        if v:
            return v
        # 从 models 找 agnes video 系列读 model 名
        for m in (d.get("models") or []):
            name = (m.get("name") or "").lower()
            url = (m.get("base_url") or "").lower()
            if "agnes" in name and ("video" in name or "videos" in url):
                return m.get("model") or _DEFAULT_MODEL
        return _DEFAULT_MODEL
    except Exception:
        return _DEFAULT_MODEL

def generate(prompt, mode="t2v", poll_interval=20, timeout=1800, progress_cb=None, **extra):
    """提交 Agnes 文生视频任务并轮询到完成, 返回 (本地视频路径, 远程URL)。
    agnes-video-2.5-flash 接受 model+prompt(+mode), 命令字段会随模型变化, 这里用**extra 透传。
    免费用户 1 RPM: 自动 _throttle。失败抛 RuntimeError。progress_cb(percent) 回调进度。"""
    key = _key()
    if not key:
        raise RuntimeError("未配置 Agnes key(设 XIAOJIAO_AGNES_KEY 或 xiaojiao_control.json 的 brain.agnese.key)")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    # 最小 body: 只带 model+prompt(+mode); 其它(时长/分辨率等)按模型通过 extra 可选传
    body = {"model": model(), "prompt": prompt}
    body.update({"mode": mode})
    body.update(extra)
    _throttle()   # 1 RPM 节流(≤1次/分钟)
    # 1. 创建任务
    r = requests.post(BASE + "/v1/videos", headers=headers, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("Agnes 建任务失败(%d): %s" % (r.status_code, r.text[:220]))
    data = r.json()
    vid = data.get("video_id") or data.get("task_id") or data.get("id")
    if not vid:
        raise RuntimeError("Agnes 未返回任务ID: " + str(data)[:200])
    # 2. 轮询
    start = time.time()
    while time.time() - start < timeout:
        try:
            st = requests.get(BASE + "/agnesapi", headers=headers, params={"video_id": vid}, timeout=30).json()
        except Exception as e:
            st = {"status": "pending", "error": str(e)}
        s = st.get("status", "pending")
        if s == "completed":
            # 下载成片到本地
            out = os.path.join(_out_dir(), datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_agnese")
            vurl = st.get("video_url") or st.get("url") or ""
            if not vurl:
                raise RuntimeError("Agnes 完成但无 video_url")
            ext = os.path.splitext(vurl)[1] or ".mp4"
            out += ext
            vr = requests.get(vurl, timeout=120)
            vr.raise_for_status()
            with open(out, "wb") as f:
                f.write(vr.content)
            return out, vurl
        elif s == "failed":
            raise RuntimeError("Agnes 生成失败: " + str(st.get("error", ""))[:200])
        else:
            if progress_cb:
                progress_cb(int(st.get("progress", 0) or 0))
        time.sleep(poll_interval)
    raise TimeoutError("Agnes 生成超时")

def _out_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "videos")
    os.makedirs(d, exist_ok=True)
    return d
