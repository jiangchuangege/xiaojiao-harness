# podcast_service/podcast_gen.py —— 播客大脑 · 核心生成逻辑
# 用「剧本(LLM) → 卸载LLM → TTS逐句(Chatterbox) → pydub拼接 → SD1.5封面」产出一个完整播客包。
# 遵循小焦多脑机制: 用时一个大脑上显存, 用别的 LLM/视频就把它替换/卸载, 互不打架。
import os, json, sys, time, threading, datetime

# ===== 路径 =====
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # xiaojiao harness 根
_SVC = os.path.dirname(os.path.abspath(__file__))
_MEDIA = os.path.join(_ROOT, "media", "podcast")
os.makedirs(_MEDIA, exist_ok=True)
_SD_MODEL = os.environ.get("XIAOJIAO_SD_MODEL") or r"G:\moxing\v1-5-pruned-emaonly.safetensors"

# ===== 全局懒加载单例 =====
_tts = None       # ChatterboxTTS
_sd = None        # StableDiffusionPipeline
_llm = None

def _log(msg):
    print("[podcast] %s" % msg, flush=True)

# ============ ① 剧本生成(用聊天大脑) ============
def _llm_chat(messages, temperature=0.8, max_tokens=1200):
    """调小焦聊天大脑(llama-swap 9292) 生成文本。"""
    global _llm
    import requests
    cfg = _load_control()
    api = cfg.get("brain", {}).get("api", {})
    base = api.get("base_url") or "http://127.0.0.1:9292/v1"
    model = api.get("model") or "xiaojiao"
    url = base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    r = requests.post(url, json=payload, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _load_control():
    try:
        return json.load(open(os.path.join(_ROOT, "xiaojiao_control.json"), encoding="utf-8"))
    except Exception:
        return {}

def generate_script(topic, host_a="小李", host_b="小焦", rounds=4, style="轻松有趣"):
    """让 LLM 生成一段双人播客对话脚本, 返回 [(speaker, text), ...]。"""
    system = ("你是资深播客编剧。请围绕主题写一段%d轮(每轮两人各一句)的中文双人播客对话，"
              "主持人是「%s」和「%s」，风格%s。直接输出纯文本对话，格式每行："
              "[%s]: 内容  或  [%s]: 内容，不要多余说明。" % (rounds, host_a, host_b, style, host_a, host_b))
    user = "本期播客主题:%s" % topic
    content = _llm_chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    lines = []
    for ln in content.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # 解析 [host]: text
        for sp in (host_a, host_b):
            tag = "[%s]" % sp
            if ln.startswith(tag):
                lines.append((sp, ln[len(tag):].strip()))
                break
    # 兜底: 若没解析出来, 按行当作同一主持人
    if not lines and content.strip():
        lines = [(host_a, x) for x in content.splitlines() if x.strip()]
    return lines or [(host_a, "大家好,欢迎收听本期播客,我们来聊聊「%s」。" % topic)]

# ============ ② TTS 逐句合成 ============
def _get_tts():
    """懒加载 Chatterbox TTS(复用小焦 /api/tts 同一模型, 不二次占显存)。"""
    global _tts
    if _tts is not None:
        return _tts
    from pathlib import Path
    # 优先复用 xiaojiao_app 的 _tts_model
    try:
        sys.path.insert(0, _ROOT)
        import xiaojiao_app as _app
        if getattr(_app, "_tts_model", None) is not None:
            _tts = _app._tts_model
            return _tts
    except Exception:
        pass
    # 否则独立加载
    import perth
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker  # 水印占位, 跳过模型, 照常出音
    import os as _os
    _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from chatterbox import ChatterboxTTS
    mdir = _find_tts_dir()
    _tts = ChatterboxTTS.from_local(Path(mdir), device="cuda") if mdir else ChatterboxTTS.from_pretrained(device="cuda")
    return _tts

def _find_tts_dir():
    import glob
    env = os.environ.get("XIAOJIAO_TTS_MODEL")
    if env and os.path.isdir(env):
        return env
    ctl = _load_control().get("brain", {}).get("tts_model_dir")
    if ctl and os.path.isdir(ctl):
        return ctl
    # 扫描常见位置
    for base in [r"G:\模型文件\语音模型", r"G:\模型文件", r"C:\模型文件"]:
        for d in glob.glob(os.path.join(base, "*")):
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "tokenizer.json")):
                return d
    return None

def _tts_speak(text, out_path):
    """单句 TTS 合成到 wav。失败返回 None。"""
    try:
        import torch
        tts = _get_tts()
        if tts is None:
            return None
        wav = tts.generate(text)
        if hasattr(wav, "cpu"):
            wav = wav.cpu()
        import torchaudio
        torchaudio.save(out_path, wav, getattr(tts, "_tts_model", tts).sr if hasattr(getattr(tts, "_tts_model", tts), "sr") else 24000)
        return out_path
    except Exception as e:
        _log("TTS 单句失败: %s" % e)
        return None

# ============ ③ pydub 拼接 ============
def _merge_wavs(wav_paths, out_path, gap_ms=400):
    """按顺序拼多段 wav, 中间插入 gap, 输出 mp3/wav。"""
    if not wav_paths:
        return None
    from pydub import AudioSegment
    total = AudioSegment.silent(duration=300)
    for i, wp in enumerate(wav_paths):
        if wp and os.path.exists(wp):
            seg = AudioSegment.from_wav(wp)
            total = total + seg + AudioSegment.silent(duration=gap_ms)
    total.export(out_path, format="mp3", bitrate="128k")
    return out_path

# ============ ④ SD1.5 封面 ============
def _get_sd():
    global _sd
    if _sd is not None:
        return _sd
    if not os.path.exists(_SD_MODEL):
        _log("SD模型不存在: %s" % _SD_MODEL)
        return None
    try:
        import torch
        from diffusers import StableDiffusionPipeline
        _sd = StableDiffusionPipeline.from_single_file(
            _SD_MODEL, torch_dtype=torch.float16, safety_checker=None)
        _sd = _sd.to("cuda")
        return _sd
    except Exception as e:
        _log("SD 加载失败: %s" % e)
        return None

def gen_cover(topic, out_path, width=512, height=512, steps=25):
    """用 SD1.5 生成播客封面图。失败返回 None。"""
    try:
        import torch
        sd = _get_sd()
        if sd is None:
            return None
        prompt = ("podcast cover, %s, vivid, professional podcast artwork, "
                  "microphone, waves, gradient background, trending on artstation" % topic)
        neg = "blurry, low quality, watermark, text, ugly, deformed"
        img = sd([prompt], negative_prompt=[neg], width=width, height=height,
                 num_inference_steps=steps, guidance_scale=7.5).images[0]
        img.save(out_path)
        return out_path
    except Exception as e:
        _log("封面生成失败: %s" % e)
        return None

# ============ 任务管理 ============
_JOBS = {}
def _make_job_id():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S") + str(int(time.time() * 1000) % 1000)

def generate_podcast(topic, host_a="小李", host_b="小焦", rounds=4, style="轻松有趣", use_cover=True, build_cover=True):
    """主入口: 生成播客包, 返回 job_id(后台线程执行)。"""
    jid = _make_job_id()
    _JOBS[jid] = {"state": "queued", "progress": 0, "message": "排队中", "topic": topic, "audio": None, "cover": None, "error": None, "ts": time.time()}
    threading.Thread(target=_run_job, args=(jid, topic, host_a, host_b, rounds, style, use_cover, build_cover), daemon=True).start()
    return jid

def _run_job(jid, topic, host_a, host_b, rounds, style, use_cover, build_cover):
    job = _JOBS[jid]
    try:
        _log("开始生成播客: %s" % topic)
        # 1. 出剧本(用聊天大脑, 剧本生成后它仍在显存)
        job["state"], job["message"] = "script", "正在撰写播客剧本…"
        job["progress"] = 10
        lines = generate_script(topic, host_a, host_b, rounds, style)
        job["script"] = lines
        job["message"] = "剧本完成, 开始配音…"
        job["progress"] = 30

        # 2. TTS 逐句合成
        job["state"] = "tts"
        wavs = []
        for i, (sp, text) in enumerate(lines):
            wp = os.path.join(_MEDIA, "%s_%02d.wav" % (jid, i))
            r = _tts_speak(text, wp)
            wavs.append(wp if r else None)
            job["progress"] = 30 + int(50 * (i + 1) / max(1, len(lines)))
            job["message"] = "正在配音 %d/%d…" % (i + 1, len(lines))

        # 3. 拼接
        job["state"], job["message"] = "compose", "正在合成完整播客音频…"
        job["progress"] = 85
        audio_out = os.path.join(_MEDIA, "%s_podcast.mp3" % jid)
        audio_path = _merge_wavs(wavs, audio_out) if use_cover else _merge_wavs(wavs, audio_out)
        if audio_path and os.path.exists(audio_path):
            job["audio"] = "/media/podcast/%s" % os.path.basename(audio_path)
        # 4. 封面
        job["state"], job["message"] = "cover", "正在生成封面图…"
        job["progress"] = 92
        if build_cover:
            cov = os.path.join(_MEDIA, "%s_cover.png" % jid)
            if gen_cover(topic, cov):
                job["cover"] = "/media/podcast/%s" % os.path.basename(cov)

        job["progress"] = 100
        job["state"] = "done"
        job["message"] = "完成! 播客已生成。"
        # 清理中间 wav(保留成品)
        for wp in wavs:
            try:
                if wp and os.path.exists(wp): os.remove(wp)
            except Exception: pass
    except Exception as e:
        _log("播客生成失败: %s" % e)
        job["state"] = "error"
        job["error"] = str(e)
        job["message"] = "生成失败: %s" % e

def job_status(jid):
    j = _JOBS.get(jid)
    if not j:
        return {"state": "notfound"}
    return dict(j)

def ensure_media_route():
    """确保 Flask 能 serve /media/podcast(由 xiaojiao_app 的 /media/<path> 处理)。"""
    return _MEDIA
