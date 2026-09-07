# music_service/ace_music.py —— ACE-Step 音乐大脑(开源顶级文字→音乐)
# 通过 ACE-Step 自带 FastAPI server(http://127.0.0.1:8001)调用, 不占小焦进程内存。
# 端点: POST /release_task(建任务) -> POST /query_result(查询) -> GET /v1/audio(下载)
import os, time, json, requests

ACE_STEP_URL = os.environ.get("XIAOJIAO_ACESTEP_URL", "http://127.0.0.1:8001")
ACE_STEP_API_KEY = os.environ.get("XIAOJIAO_ACESTEP_KEY", "")
_DEFAULT_DUR = 10   # 秒

def _health():
    try:
        r = requests.get(ACE_STEP_URL + "/health", timeout=4)
        return r.status_code == 200
    except Exception:
        return False

def available():
    return _health()

def _headers():
    h = {"Content-Type": "application/json"}
    if ACE_STEP_API_KEY:
        h["Authorization"] = "Bearer " + ACE_STEP_API_KEY
    return h

def _auth_body():
    return {"ai_token": ACE_STEP_API_KEY} if ACE_STEP_API_KEY else {}

def generate(prompt, lyrics="", duration=_DEFAULT_DUR, model=None, audio_format="mp3",
             poll_interval=3, timeout=1200, progress_cb=None, **extra):
    """用 ACE-Step 生成音乐, 返回 (本地音频路径, 时长秒)。
    prompt=音乐描述(如'轻快的钢琴曲'); lyrics=歌词(可选, 留空则纯音乐); duration=秒。"""
    if not _health():
        raise RuntimeError("ACE-Step 服务未启动(需先跑 start_api_server.bat 或 python -m uvicorn acestep.api_server:app --port 8001)")
    # 1. 建任务
    body = {
        "prompt": prompt,
        "lyrics": lyrics,
        "audio_duration": float(max(1, min(int(duration), 60))),
        "audio_format": audio_format,
        "inference_steps": 8,
        "use_random_seed": True,
    }
    if model:
        body["model"] = model
    body.update(_auth_body())
    body.update(extra)
    r = requests.post(ACE_STEP_URL + "/release_task", headers=_headers(), json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("ACE-Step 建任务失败(%d): %s" % (r.status_code, r.text[:220]))
    data = r.json()
    task_id = data.get("task_id") or data.get("job_id")
    if not task_id:
        raise RuntimeError("ACE-Step 未返回 task_id: " + str(data)[:200])
    print("[ace_music] 任务已建: %s" % task_id, flush=True)
    # 2. 轮询
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(poll_interval)
        try:
            q = requests.post(ACE_STEP_URL + "/query_result",
                              headers=_headers(),
                              json={"task_id_list": [task_id], "ai_token": ACE_STEP_API_KEY} if ACE_STEP_API_KEY
                                   else {"task_id_list": [task_id]}, timeout=30).json()
        except Exception as e:
            q = {"status": "error", "error": str(e)}
        # 兼容不同返回结构
        data_list = q.get("data") or q.get("results") or (q if isinstance(q, list) else [q])
        item = data_list[0] if data_list else {}
        status = str(item.get("status", "")).lower() if item else ""
        if status in ("succeeded", "completed", "done", "finished") or item.get("audio_paths") or item.get("first_audio_path"):
            return _download(item, prompt, audio_format), item.get("duration", duration)
        elif status in ("failed", "error", "cancelled"):
            raise RuntimeError("ACE-Step 生成失败: %s" % item.get("error") or item.get("status_message") or ("%s" % item)[:200])
        else:
            if progress_cb:
                progress_cb(int(item.get("progress", 0) or 0))
    raise TimeoutError("ACE-Step 生成超时")

def _download(item, prompt, audio_format="mp3"):
    """从 /v1/audio 下载第一个音频到本地 media/music/, 返回路径。"""
    paths = item.get("audio_paths") or []
    if not paths and item.get("first_audio_path"):
        paths = [item["first_audio_path"]]
    if not paths:
        raise RuntimeError("ACE-Step 完成但无音频路径: " + str(item)[:200])
    apath = paths[0]
    # 请求下载(依赖 path 参数)
    try:
        r = requests.get(ACE_STEP_URL + "/v1/audio", headers=_headers(), params={"path": apath}, timeout=120)
        r.raise_for_status()
    except Exception as e:
        # 兜底: 若路径是本地绝对路径直接用
        if os.path.exists(apath):
            r = None
        else:
            raise RuntimeError("下载失败: %s" % e)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "media", "music")
    os.makedirs(out_dir, exist_ok=True)
    name = _sanitize(prompt)[:30] + "_" + time.strftime("%Y%m%d_%H%M%S") + "." + audio_format
    out = os.path.join(out_dir, name)
    if r is not None:
        with open(out, "wb") as f:
            f.write(r.content)
    else:
        import shutil; shutil.copy(apath, out)
    return out

def _sanitize(s):
    import re
    return re.sub(r'[\\/:*?"<>|]', "_", s)
