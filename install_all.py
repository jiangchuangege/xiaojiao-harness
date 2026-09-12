# -*- coding: utf-8 -*-
"""🐳 小焦 · 全功能一键安装器
用法：双击 `一键安装.bat`，或：python install_all.py
它会：检测 11 项工具 → 自动下载缺失的(小件全自动, 大件给选项) → 写配置 → 报告哪些可用。
装完(或补完缺失) → 全部功能就能用。
"""
import os, sys, json, shutil, subprocess, urllib.request, zipfile, time, socket
import logging  # noqa: F401  （由 tools/fix_silent_except.py 注入）
try:
    from xiaojiao_log import get_logger
except Exception:  # 独立运行时退化为标准 logging
    def get_logger(name=None):
        return logging.getLogger(name or 'xiaojiao')
LOG = get_logger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(ROOT, "xiaojiao_control.json")
HF_MIRROR = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")


def test_cloud_api(base_url, api_key, model=""):
    """按 OpenAI 兼容协议真实探测一次, 确认这个 API 通不通、能不能用。
    返回 (ok: bool, message: str)。"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return False, "base_url 为空"
    import requests
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    # 1) 先试 GET /v1/models(大多数 OpenAI 兼容端点都有)
    try:
        r = requests.get(base_url + "/models", headers=headers, timeout=15)
        if r.status_code == 200:
            return True, "GET /models 200 OK"
        # 2) 若 models 405/404, 回退发一个最小 chat/completions 试探
        body = {"model": model or "test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
        r2 = requests.post(base_url + "/chat/completions", headers=headers, json=body, timeout=20)
        if r2.status_code == 200:
            return True, "POST /chat/completions 200 OK"
        return False, "HTTP %s (models=%s, chat=%s)" % (r2.status_code, r.status_code, r2.status_code)
    except Exception as e:
        return False, "连接失败: %s" % str(e)[:80]


def is_port_up(port, timeout=1.0):
    try:
        s = socket.socket(); s.settimeout(timeout)
        s.connect(("127.0.0.1", port)); s.close(); return True
    except Exception:
        return False


# ========== 全自动探测：不写死任何路径 ==========
# 顺序：环境变量 > PATH > 各盘符启发式(一级目录名命中通用关键词才深入，避免全盘慢扫)
#       > where /r 全盘兜底(启发式漏了也能找到)。目录名/盘符怎么起都行，换机器都能自己找到。
# 关键词只用"通用词"，不掺任何某台机器的个人目录名。
DISCOVER_KEYWORDS = ("llama", "comfy", "模型", "大脑", "xiaojiao",
                     "video", "视频", "ai", "wan", "portable", "下载", "tool", "工具")
_SKIP_DIRS = {"windows", "system volume information", "$recycle.bin", "programdata",
              "node_modules", "program files (x86)", "program files", "python", "$windows.~bt"}


def _drives():
    """返回当前存在的盘符根列表（如 C 盘、D 盘）。"""
    import string
    out = []
    for letter in string.ascii_uppercase:
        r = letter + ":\\"
        try:
            if os.path.isdir(r):
                out.append(r)
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 66, e)
    return out


def _top_dirs(drv):
    """盘符根下的一级目录名列表（跳过隐藏/系统）。"""
    try:
        return [d for d in os.listdir(drv)
                if os.path.isdir(os.path.join(drv, d)) and not d.startswith(("$", "."))]
    except Exception:
        return []


def _hit_keyword(name, keywords):
    n = (name or "").lower()
    return any((k or "").lower() in n for k in keywords)


def _walk_limit(root, maxdepth):
    """有限深度 os.walk，自动剪掉超大系统目录，产出 (dirpath, dirnames)。"""
    root = root.rstrip("\\/")
    base = root.count(os.sep)
    for dp, dns, _fns in os.walk(root):
        dns[:] = [d for d in dns if d.lower() not in _SKIP_DIRS and not d.startswith(("$", "."))]
        if dp.count(os.sep) - base >= maxdepth:
            dns[:] = []
            yield dp, dns
            continue
        yield dp, dns


def _where_search(drv, name, timeout=25):
    """终极兜底：用系统 where /r 在整盘搜某个特征文件(不管目录叫啥都能找到)。返回完整路径或 None。"""
    try:
        r = subprocess.run(["where", "/r", drv, name], capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.splitlines():
                p = line.strip()
                if p.lower().endswith(name.lower()):
                    return p
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 107, e)
    return None


def discover_exe(name, extra_keywords=()):
    """找可执行文件：PATH → 关键词命中的盘符目录里有限深度扫 → where /r 整盘兜底。返回完整路径或 None。"""
    w = shutil.which(name)
    if w:
        return w
    kws = tuple(extra_keywords) or (name.split("-")[0].split(".")[0],)
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or _hit_keyword(t, kws)):
                continue
            base = os.path.join(drv, t)
            if os.path.exists(os.path.join(base, name)):
                return os.path.join(base, name)
            for dp, _dns in _walk_limit(base, 3):
                if os.path.exists(os.path.join(dp, name)):
                    return os.path.join(dp, name)
    # 启发式漏了 → 全盘找(不依赖任何目录名)
    for drv in _drives():
        p = _where_search(drv, name)
        if p:
            return p
    return None


def discover_gguf():
    """自动找一个 GGUF 模型文件（优先名字含 xiaojiao 的）。返回路径或 None。"""
    best = None
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or t.lower() in ("downloads", "下载")):
                continue
            base = os.path.join(drv, t)
            for dp, _dns in _walk_limit(base, 3):
                try:
                    for f in os.listdir(dp):
                        if f.lower().endswith(".gguf"):
                            if "xiaojiao" in f.lower():
                                return os.path.join(dp, f)   # 官方同名优先
                            best = best or os.path.join(dp, f)
                except Exception as e:
                    LOG.debug("忽略异常(%s:%d): %s", __file__, 151, e)
    return best


def discover_comfy():
    """自动找 ComfyUI 根目录（含 main.py 且路径含 ComfyUI 字样）。返回路径或 None。"""
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or "comfy" in t.lower()):
                continue
            base = os.path.join(drv, t)
            for dp, _dns in _walk_limit(base, 4):
                if os.path.basename(dp).lower().find("comfy") >= 0 and os.path.exists(os.path.join(dp, "main.py")):
                    return dp
    return None


def discover_video_root():
    """自动找 Wan 视频模型根目录（含 dit_fp8.safetensors 等）。返回路径或 None。"""
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or t.lower() in ("downloads", "下载")):
                continue
            base = os.path.join(drv, t)
            if os.path.exists(os.path.join(base, "dit_fp8.safetensors")):
                return base
            for dp, _dns in _walk_limit(base, 3):
                if os.path.exists(os.path.join(dp, "dit_fp8.safetensors")) and os.path.exists(os.path.join(dp, "vae_fp8.safetensors")):
                    return dp
    return None


def discover_node_dir(prefix):
    """在盘符里找一个目录名以 prefix 开头的目录（如 ComfyUI-AnyDeviceOffload-1.0.3）。返回路径或 None。"""
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or _hit_keyword(t, (prefix,))):
                continue
            base = os.path.join(drv, t)
            for dp, dns in _walk_limit(base, 3):
                for d in dns:
                    if d.lower().startswith(prefix.lower()):
                        return os.path.join(dp, d)
                if os.path.basename(dp).lower().startswith(prefix.lower()):
                    return dp
    return None


def discover_brain_all():
    """全盘自动探测小脑模型候选（*.pth）与词表(vocab*.pkl) —— 不写死任何路径。
    模型与词表可跨目录，自动配对（同目录优先，否则用探测到的词表）。
    排序：体积大优先（更像训练好的完整模型）。返回 [(model, vocab, size_bytes), ...]。"""
    kws = ("xiaonao", "小脑", "brain", "xiaojiao", "model", "模型")
    models, vocabs = [], []
    for drv in _drives():
        for t in _top_dirs(drv):
            if not (_hit_keyword(t, DISCOVER_KEYWORDS) or _hit_keyword(t, kws)):
                continue
            base = os.path.join(drv, t)
            for dp, dns in _walk_limit(base, 3):
                try:
                    names = os.listdir(dp)
                except Exception:
                    continue
                v_here = [f for f in names if f.lower().startswith("vocab") and f.lower().endswith(".pkl")]
                for f in v_here:
                    vocabs.append(os.path.join(dp, f))
                for f in sorted(names):
                    if not f.lower().endswith(".pth"):
                        continue
                    p = os.path.join(dp, f)
                    try:
                        sz = os.path.getsize(p)
                    except Exception:
                        continue
                    if sz < 1_000_000:      # 跳过占位/无关小文件
                        continue
                    models.append((p, os.path.join(dp, v_here[0]) if v_here else "", sz))
    out = []
    for p, v, sz in models:
        out.append((p, v or (vocabs[0] if vocabs else ""), sz))
    out.sort(key=lambda x: -x[2])        # 体积大优先
    return out


def discover_brain():
    """全盘自动探测小脑模型（取最佳候选）。返回 (model_path, vocab_path)。"""
    r = discover_brain_all()
    return (r[0][0], r[0][1]) if r else (None, None)


def discover_neko():
    """自动找 N.E.K.O. 猫娘根目录。不猜目录名、不写死路径，识别两种形态:
      - Steam 版桌面客户端: 目录含 N.E.K.O.exe
      - 源码克隆版: 目录名含 neko/猫娘 且含 launcher.py
    启发式找不到时用 where /r 整盘兜底，装在哪都能找到。返回路径或 None。"""
    for drv in _drives():
        for t in _top_dirs(drv):
            base = os.path.join(drv, t)
            # ① 一级目录直接命中
            if os.path.exists(os.path.join(base, "N.E.K.O.exe")):
                return base
            if _hit_keyword(t, ("neko", "猫娘")) and os.path.exists(os.path.join(base, "launcher.py")):
                return base
            # ② 一级目录名命中通用词(steam/neko/模型/xiaojiao 等) → 深入有限层找
            if _hit_keyword(t, DISCOVER_KEYWORDS) or _hit_keyword(t, ("neko", "steam", "猫娘")):
                for dp, _dns in _walk_limit(base, 4):
                    if os.path.exists(os.path.join(dp, "N.E.K.O.exe")):
                        return dp
                    if os.path.exists(os.path.join(dp, "launcher.py")) and _hit_keyword(os.path.basename(dp), ("neko", "猫娘")):
                        return dp
    # ③ 兜底: 整盘找 N.E.K.O.exe(Steam 版特征, 独一无二)
    for drv in _drives():
        p = _where_search(drv, "N.E.K.O.exe")
        if p:
            return os.path.dirname(p)
    return None


G = lambda x: "\033[92m" + x + "\033[0m" if os.name != "nt" else x
R = lambda x: "\033[91m" + x + "\033[0m" if os.name != "nt" else x


def load_cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(c):
    json.dump(c, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def download(url, dest, label=""):
    print("   ⬇ 下载 %s → %s ..." % (label or url.split("/")[-1], dest))
    try:
        urllib.request.urlretrieve(url, dest)
        return os.path.exists(dest)
    except Exception as e:
        print("   ✗ 下载失败:", e)
        return False


def unzip(src, dst):
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(dst)
        return True
    except Exception as e:
        print("   ✗ 解压失败:", e)
        return False


def ask(msg):
    try:
        return input(msg).strip().lower() in ("y", "yes", "是", "1", "")
    except Exception:
        return True


def main():
    print("=" * 56)
    print("🐳 小焦 XiaoJiao · 全功能一键安装")
    print("检测 11 项工具 → 自动装缺失 → 配置 → 全部功能可用")
    print("=" * 56)
    c = load_cfg()
    missing = []
    opt_miss = []

    # 1) Python 依赖
    print("\n[1/11] Python 依赖 ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")])
    print("   ✅ pip 依赖装完(或已装)")

    # 2) llama.cpp (llama-server.exe)
    print("\n[2/11] llama.cpp (聊天大脑引擎) ...")
    ll = c.setdefault("brain", {}).setdefault("llama", {})
    server = ll.get("server") or ""
    # 检测: ①配置文件已有 ②全自动探测(PATH + 各盘符按关键词找)，不用写死路径
    ok_s = False
    if server and os.path.exists(server):
        ok_s = True
    else:
        found = discover_exe("llama-server.exe")
        if found:
            ok_s = True
            server = found
            print("   🔎 自动探测到:", found)
    if ok_s:
        ll["server"] = server.replace("/", "\\")
        print("   ✅", server)
    else:
        print("   ❌ 未找到 llama-server.exe（必需！）")
        print("   自动下载 llama.cpp 便携版(必需)...")
        dl = os.path.join(ROOT, "llama.cpp-b.zip")
        ok = False
        # 动态获取最新版(nightly-tag.txt), 资产名格式随版本变化, 逐个试探(cuda 优先, CPU 兜底)
        try:
            import urllib.request as _ur
            with _ur.urlopen("https://github.com/ggml-org/llama.cpp/releases/latest/download/nightly-tag.txt", timeout=20) as _f:
                _tag = _f.read().decode().strip()
            print("   ℹ️ llama.cpp 最新版: %s（自动下载最新，不再写死旧版本号）" % _tag)
            _base = "https://github.com/ggml-org/llama.cpp/releases/download/%s/" % _tag
            for _n in ("llama-%s-bin-win-cuda-12.4-x64.zip" % _tag,
                       "llama-%s-bin-win-cuda-cu12.2-x64.zip" % _tag,
                       "llama-%s-bin-win-cpu-x64.zip" % _tag):
                ok = download(_base + _n, dl, "llama.cpp")
                if ok:
                    break
        except Exception as e:
            print("   ✗ 获取版本失败:", str(e)[:100])
        if ok and unzip(dl, os.path.join(ROOT, "llama.cpp")):
            exe = os.path.join(ROOT, "llama.cpp", "llama-server.exe")
            if os.path.exists(exe):
                ll["server"] = exe.replace("/", "\\")
        try:
            os.remove(dl)
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 370, e)
        if not (ll.get("server") and os.path.exists(ll["server"])):
            missing.append("llama-server.exe")

    # 3) 大脑模型(不写死: 本地 GGUF 或 云端兼容 API; 真发起请求探测, 通了才算通过)
    print("\n[3/11] 大脑模型 (本地 GGUF 或 云端 OpenAI 兼容 key) ...")
    api = c.setdefault("brain", {}).setdefault("api", {})
    api_key = (api.get("api_key") or "").strip()
    api_url = (api.get("base_url") or "").strip()
    api_model = (api.get("model") or "").strip()

    # 3a) 本地 GGUF: 配置文件 -> 全自动探测(盘符按关键词找, 优先 xiaojiao 名)
    local_port = None
    try:
        from urllib.parse import urlparse
        local_port = urlparse(api_url or "http://127.0.0.1:9292/v1").port or 9292
    except Exception:
        local_port = 9292
    gf = ll.get("gguf") or ""
    ok_g_file = bool(gf) and os.path.exists(gf)
    if not ok_g_file:
        auto_gf = discover_gguf()
        if auto_gf:
            gf, ok_g_file = auto_gf, True
            ll["gguf"] = gf.replace("/", "\\")
            print("   🔎 自动探测到模型:", gf)
    # 3b) 服务在线探测 + 判定:
    #     大脑就绪 = 服务在线(协议通) OR (有 llama-server + 有 GGUF)——文件齐了, 启动 start_xiaojiao 会自动拉起
    local_online = is_port_up(local_port)
    local_ok = False; local_msg = ""
    if local_online:
        try:
            url = "http://127.0.0.1:%d/v1" % local_port
            ok, msg = test_cloud_api(url, api_key, api_model)
            local_ok, local_msg = ok, msg
        except Exception as e:
            local_msg = "探测异常: %s" % str(e)[:60]
    server_ok = bool(ll.get("server")) and os.path.exists(ll["server"])
    if not local_ok and ok_g_file and server_ok:
        local_ok = True
        local_msg = "文件就绪(llama-server + %s)，启动 start_xiaojiao 后自动拉起大脑" % os.path.basename(gf)
    if not local_ok:
        local_msg = ("大脑服务未在线(端口 %d)%s" % (local_port,
                     ("；模型文件缺失" if not ok_g_file else "")))
    # 3c) 云端真连通检测: 若配置里有云端 key+url, 实测一次
    cloud_ok = False; cloud_msg = "未配置云端 API"
    if api_key and api_url:
        try:
            ok, msg = test_cloud_api(api_url, api_key, api_model)
            cloud_ok, cloud_msg = ok, msg
        except Exception as e:
            cloud_msg = "探测异常: %s" % str(e)[:60]
    model_ok = local_ok or cloud_ok

    # 报告
    if local_ok:
        print("   ✅ 本地大脑可用: %s (%s)" % (("http://127.0.0.1:%d/v1" % local_port), local_msg))
    else:
        print("   ⓘ 本地大脑: %s" % local_msg)
    if cloud_ok:
        print("   ✅ 云端 API 协议通: %s (%s)" % (api_url, cloud_msg))
    elif api_key and api_url:
        print("   ❌ 云端 API 不通: %s" % cloud_msg)
    if model_ok:
        print("   ℹ️ 已用协议连通确认大脑可用; 想换模型: 改 xiaojiao_control.json 的 brain.engine(llama/auto/api) 或 models, 型号不写死。")
    else:
        print("   ⚠️ 当前没有协议连通的大脑。")
        # 交互式让用户填云端 API 并实测连通(通了即通过并写配置)
        try:
            ans = input("   想现在配置一个云端 OpenAI 兼容 API(base_url + key + model)? [Y/n]: ").strip().lower()
        except Exception:
            ans = "y"
        if ans in ("", "y", "yes", "是", "1"):
            try:
                print("   · 输入 base_url(如 https://api.deepseek.com/v1):")
                bu = input("     > ").strip().rstrip("/")
                print("   · 输入 api_key:")
                bk = input("     > ").strip()
                print("   · 输入模型名(如 deepseek-chat, 可回车用默认):")
                bm = input("     > ").strip() or "deepseek-chat"
                print("   🔍 正在按 OpenAI 兼容协议实测连通...")
                ok, msg = test_cloud_api(bu, bk, bm)
                if ok:
                    api["base_url"] = bu
                    api["api_key"] = bk
                    api["model"] = bm
                    c.setdefault("brain", {})["engine"] = "api"
                    save_cfg(c)
                    print("   ✅ 协议连通通过 (%s) —— 已写入 xiaojiao_control.json(brain.api), 用过即通过。" % msg)
                else:
                    print("   ❌ 检测不通: %s" % msg)
                    print("   不通视为未通过(避免配置了却不能用)。请检查 base_url/key/model 后重试, 或先启动本地大脑。")
                    missing.append("大脑模型(云端API — 协议连通)")
            except Exception as e:
                print("   ✗ 输入/检测异常: %s" % str(e)[:60])
                missing.append("大脑模型(云端API — 协议连通)")
        else:
            print("   好，跳过。先启动本地大脑(start_xiaojiao.py)或稍后再配 API。")
            missing.append("大脑模型(本地大脑服务或云端API — 需协议连通)")

    # 3b) 小脑（自研蒸馏模型 · 必需 · 项目核心）—— 路径不写死：配置/环境变量/自动探测
    print("\n[3b/11] 小脑 (自研蒸馏模型 · 必需 · 项目核心) ...")
    import glob as _g
    _xjc = (c.get("brain") or {}).get("xiaojiao") or {}
    _bm = os.environ.get("XIAOJIAO_BRAIN_MODEL") or _xjc.get("model_path") or ""
    _bv = os.environ.get("XIAOJIAO_BRAIN_VOCAB") or _xjc.get("vocab_path") or ""
    _bc = os.environ.get("XIAOJIAO_BRAIN_CONFIG") or _xjc.get("config_path") or ""
    if not (_bm and os.path.exists(_bm)):                 # 自动探测(不写死文件名)
        _c = sorted(_g.glob(os.path.join(ROOT, "*.pth")))
        _bm = _c[0] if _c else ""
    if not (_bv and os.path.exists(_bv)):
        _c = sorted(_g.glob(os.path.join(ROOT, "vocab*.pkl")))
        _bv = _c[0] if _c else ""
    if not (_bc and os.path.exists(_bc)):
        _c = sorted(_g.glob(os.path.join(ROOT, "model_config*.json")))
        _bc = _c[0] if _c else ""
    # 项目目录没有 → 全盘自动探测（不写死任何路径）
    if not (_bm and os.path.exists(_bm)):
        print("   🔎 项目目录没有小脑，正在全盘自动探测(找 *.pth + vocab*.pkl)...")
        _cands = discover_brain_all()
        if _cands:
            if len(_cands) > 1:
                print("   找到 %d 个候选（选中的是第 1 个；想换改 brain.xiaojiao.model_path）:" % len(_cands))
                for _p, _v, _s in _cands[:5]:
                    print("      · %s (%.0f MB)%s" % (_p, _s / 1e6, " +词表" if _v else ""))
            _bm = _cands[0][0]
            if _cands[0][1] and not (_bv and os.path.exists(_bv)):
                _bv = _cands[0][1]
            print("   🔎 自动选用小脑:", _bm)
    # 找到就写进配置（运行时按配置加载；你也可以随时自己改）
    if _bm and os.path.exists(_bm) and _bv and os.path.exists(_bv):
        c.setdefault("brain", {}).setdefault("xiaojiao", {})
        c["brain"]["xiaojiao"]["model_path"] = _bm
        c["brain"]["xiaojiao"]["vocab_path"] = _bv
        if _bc and os.path.exists(_bc):
            c["brain"]["xiaojiao"]["config_path"] = _bc
        print("   ✅ 已写入 xiaojiao_control.json → brain.xiaojiao（想换模型改这里即可）")
    _has_harness = os.path.exists(os.path.join(ROOT, "xiaojiao_harness.py"))
    if _bm and _bv:
        print("   ✅ 小脑就绪: %s + %s (%.0f MB)" % (os.path.basename(_bm), os.path.basename(_bv),
                                                     os.path.getsize(_bm) / 1e6))
        print("      (想换模型当小脑：改 xiaojiao_control.json 的 brain.xiaojiao.model_path，或设 XIAOJIAO_BRAIN_MODEL)")
        if not _has_harness:
            print("   ⚠️ 缺 xiaojiao_harness.py（小脑推理代码），请补上")
            missing.append("xiaojiao_harness.py(小脑推理)")
    else:
        print("   ❌ 未找到小脑模型（必需 · 项目核心）")
        print("      需要：模型(*.pth) + 词表(vocab*.pkl)。可自行指定：")
        print("        · 改 xiaojiao_control.json → brain.xiaojiao.model_path / vocab_path")
        print("        · 或设环境变量 XIAOJIAO_BRAIN_MODEL / XIAOJIAO_BRAIN_VOCAB")
        print("        · 或训练一个：python train_model.py（先 massive_distill.py 蒸馏语料）")
        missing.append("小脑模型(核心 · *.pth + vocab*.pkl)")

    # 4) llama-swap
    print("\n[4/11] llama-swap (秒级切换 · 必需) ...")
    sw = os.environ.get("XIAOJIAO_LLAMA_SWAP") or ""
    ok_sw = bool(sw) and os.path.exists(sw)
    if not ok_sw:
        sw = discover_exe("llama-swap.exe")
        ok_sw = bool(sw)
        if ok_sw:
            print("   🔎 自动探测到:", sw)
    if not ok_sw:
            print("   ❌ 未找到 llama-swap.exe（必需！秒级切换核心）")
            print("   自动下载 llama-swap(必需)...")
            dl = os.path.join(ROOT, "llama-swap.zip")
            ok = False
            # 动态获取最新版(tag 与资产名格式已变: v255 -> llama-swap_255_windows_amd64.zip)
            try:
                import urllib.request as _ur
                with _ur.urlopen("https://api.github.com/repos/mostlygeek/llama-swap/releases/latest", timeout=20) as _f:
                    import json as _json
                    _ver = _json.loads(_f.read().decode()).get("tag_name", "").lstrip("v")
                print("   ℹ️ llama-swap 最新版: v%s（自动下载最新，不再写死旧版本号）" % _ver)
                ok = download("https://github.com/mostlygeek/llama-swap/releases/download/v%s/llama-swap_%s_windows_amd64.zip" % (_ver, _ver), dl, "llama-swap")
            except Exception as e:
                print("   ✗ 获取版本失败:", str(e)[:100])
            if ok and unzip(dl, os.path.join(ROOT, "llama-swap")):
                for root, _, fs in os.walk(os.path.join(ROOT, "llama-swap")):
                    for f in fs:
                        if f == "llama-swap.exe":
                            sw = os.path.join(root, f)
            try:
                os.remove(dl)
            except Exception as e:
                LOG.debug("忽略异常(%s:%d): %s", __file__, 555, e)
            if not sw:
                missing.append("llama-swap.exe")
    if sw:
        c.setdefault("brain", {}).setdefault("api", {})["base_url"] = "http://127.0.0.1:9292/v1"
        c["brain"]["api"]["model"] = "xiaojiao"
        c["brain"]["llama_swap_port"] = 9292
        print("   ✅ llama-swap:", sw)

    # 5) ComfyUI —— 可选(视频大脑)
    print("\n[5/11] ComfyUI (可选 · 视频大脑) ...")
    comfy = os.environ.get("XIAOJIAO_COMFY_DIR") or ""
    ok_comfy = bool(comfy) and os.path.exists(os.path.join(comfy, "main.py"))
    if not ok_comfy:
        auto = discover_comfy()
        if auto:
            comfy = auto
            ok_comfy = True
            print("   🔎 自动探测到:", comfy)
    elif comfy:
        print("   ✅ ComfyUI:", comfy)
    if not ok_comfy:
        print("   ⓘ 未找到 ComfyUI（可选；缺了只是不能生成视频，不影响聊天）")
        p = input("   请粘贴 ComfyUI 目录(main.py 所在, 回车跳过): ").strip().strip('"')
        if p and os.path.exists(os.path.join(p, "main.py")):
            comfy = p
            ok_comfy = True
        else:
            opt_miss.append("ComfyUI → 🎬 真·文生视频")
    if comfy:
        print("   ✅ ComfyUI:", comfy)

    # 5b) 视频节点 —— 可选(仅视频功能用)
    print("\n[5b/11] 视频节点 (可选 · 仅视频用) ...")
    cn_dir = os.path.join(comfy, "custom_nodes") if comfy else ""
    node_missing = []
    for node in ("ComfyUI-AnyDeviceOffload", "ComfyUI-WanVideoWrapper"):
        ok_node = os.path.isdir(os.path.join(cn_dir, node)) if cn_dir else False
        if ok_node:
            print("   ✅", node, "已在 custom_nodes")
        else:
            print("   ❌", node, "未装进 ComfyUI（正在自动寻找本地源码并安装...）")
            # 全盘自动找该节点源码目录(目录名可能带版本号, 如 ComfyUI-AnyDeviceOffload-1.0.3)
            src_dir = discover_node_dir(node)
            got = False
            if cn_dir and src_dir:
                try:
                    import shutil as _sh
                    _sh.copytree(src_dir, os.path.join(cn_dir, node), dirs_exist_ok=True)
                    got = os.path.isdir(os.path.join(cn_dir, node))
                    print("   ✅ 已自动找到并装进 ComfyUI:", src_dir)
                except Exception as e:
                    print("   ✗ 复制失败:", e)
            if not got:
                node_missing.append(node)
    if node_missing:
        opt_miss.append("视频节点:" + ",".join(node_missing) + " → 🎬 文生视频")

    # 6) Wan 视频模型三件套 —— 可选(仅视频功能用)
    print("\n[6/11] Wan2.1 视频模型 (可选 · 仅视频用) ...")
    # 视频模型根目录: 环境变量优先, 否则自动探测(找含 dit_fp8.safetensors 的目录)
    vroot = os.environ.get("XIAOJIAO_VIDEO_ROOT") or ""
    if not vroot or not os.path.isdir(vroot):
        auto_vr = discover_video_root()
        if auto_vr:
            vroot = auto_vr
            print("   🔎 自动探测到视频模型目录:", vroot)
    if not vroot:
        # 没探测到已有目录时, 下载到脚本目录下(不写死任何盘符路径)
        vroot = os.path.join(ROOT, "video_models")
        print("   ℹ️ 未找到现有视频模型目录, 将下载到:", vroot)
    os.makedirs(vroot, exist_ok=True)
    ck = os.path.join(vroot, "dit_fp8.safetensors")
    tc = os.path.join(vroot, "umt5_fp8.safetensors")
    va = os.path.join(vroot, "vae_fp8.safetensors")
    ok3 = all(os.path.exists(x) for x in (ck, tc, va))
    if not ok3:
        print("   ⓘ 缺视频模型(可选，不影响聊天):", [os.path.basename(x) for x in (ck, tc, va) if not os.path.exists(x)])
        ans = "n"
        try:
            ans = input("   是否现在自动下载 Wan2.1 三件套(约2.5GB)? [y/N]: ").strip().lower()
        except Exception:
            ans = "n"
        if ans in ("y", "yes", "是", "1"):
            print("   自动从 hf-mirror 下载 Wan2.1-1.3B 三件套...")
            os.makedirs(vroot, exist_ok=True)
            base = HF_MIRROR + "/Wan-AI/Wan2.1-T2V-1.3B-Diffusers/resolve/main/"
            for f, d in [("dit_fp8.safetensors", ck), ("umt5_fp8.safetensors", tc), ("vae_fp8.safetensors", va)]:
                if not os.path.exists(d):
                    download(base + f, d, f)
        if not all(os.path.exists(x) for x in (ck, tc, va)):
            opt_miss.append("Wan 视频模型 → 🎬 文生视频")
    else:
        print("   ✅ 三件套齐全")

    # 7) Node.js —— 可选(js 插件用)
    print("\n[7/11] Node.js (可选 · JS 插件) ...")
    if shutil.which("node"):
        print("   ✅ node:", shutil.which("node"))
    else:
        print("   ⓘ 未装 Node.js（可选；缺了只是 .js 插件不可用）→ https://nodejs.org 装 LTS")
        opt_miss.append("Node.js → 🟨 JS 插件")

    # 8) NVIDIA GPU
    print("\n[8/11] NVIDIA GPU ...")
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=8)
        print("   ✅", r.stdout.strip() if r.returncode == 0 else "未检测到")
    except Exception:
        print("   ❌ 未检测到 N 卡(视频/加速需要)")

    # 8b) 可选功能依赖（不是硬性必需, 缺哪个=哪个功能用不了, 这里全部补齐告知）
    print("\n[8b/11] 可选功能依赖 (缺哪个=哪个功能用不了) ...")
    # N.E.K.O. 猫娘(桌面伙伴, 可选) —— 环境变量优先, 否则全盘自动探测; 支持 Steam 版(N.E.K.O.exe)与源码版(launcher.py)
    neko_root = os.environ.get("XIAOJIAO_NEKO_DIR") or ""
    if not (neko_root and (os.path.exists(os.path.join(neko_root, "launcher.py"))
                           or os.path.exists(os.path.join(neko_root, "N.E.K.O.exe")))):
        neko_root = discover_neko() or ""
    if neko_root:
        print("   ✅ N.E.K.O. 猫娘:", neko_root)
    else:
        print("   → 未找到 N.E.K.O. 猫娘(可跳过): 桌面猫娘伙伴不可用。下载 N.E.K.O. 开源项目(含 launcher.py)后设 XIAOJIAO_NEKO_DIR=其目录。")
        opt_miss.append("N.E.K.O. 猫娘 → 桌面猫娘伙伴")
    # Chatterbox(播客配音 TTS) —— 能 import 即已装
    try:
        import chatterbox  # noqa
        print("   ✅ Chatterbox(播客配音) 已装")
        tts_ok = True
    except Exception:
        try:
            import chattts  # noqa
            print("   ✅ ChatTTS(播客配音) 已装")
            tts_ok = True
        except Exception:
            print("   → 未装 Chatterbox: 播客配音(TTS)不可用。`pip install chatterbox-tts`(或 chattts)。")
            opt_miss.append("Chatterbox → 🎙️ 播客配音")
            tts_ok = False
    # Diffusers + SD1.5(封面) —— 播客封面/图
    try:
        import diffusers  # noqa
        print("   ✅ Diffusers(SD1.5 封面) 已装")
        sd_ok = True
    except Exception:
        print("   → 未装 diffusers: 播客封面/图像生成不可用。`pip install diffusers transformers accelerate`。")
        opt_miss.append("Diffusers/SD1.5 → 🎙️ 播客封面")
        sd_ok = False
    # ACE-Step(音乐) —— music_service 调它自带 API server :8001
    ace_dir = os.path.join(ROOT, "music_service")
    ace_free = os.path.exists(ace_dir) and any(
        os.path.isfile(os.path.join(ace_dir, f)) for f in os.listdir(ace_dir) if f.lower().endswith((".py", ".json", ".yaml"))
    )
    if ace_free:
        print("   ✅ music_service(ACE-Step 音乐) 脚本就绪")
    else:
        print("   → music_service 目录缺失/空: 🎵 音乐生成不可用(需 ACE-Step 的 API server :8001)。")
        opt_miss.append("ACE-Step 音乐 → 🎵 音乐生成")
    # jieba(分词), requests, torch 等 pip 已在 1) 装
    # 简短汇总
    if opt_miss:
        print("   ⚠️ 以下功能将不可用(其余全部可用):")
        for m in opt_miss:
            print("      -", m)
    else:
        print("   🎉 所有可选功能依赖就绪(猫娘/配音/封面/音乐)!")

    # 9) 写配置
    print("\n[9/11] 写配置 ...")
    if comfy:
        c.setdefault("brain", {})["comfy_dir"] = comfy
        os.environ["XIAOJIAO_COMFY_DIR"] = comfy
    c.setdefault("brain", {})["keep_warm"] = True
    save_cfg(c)
    print("   ✅ xiaojiao_control.json 已配置(keep_warm/llama-swap/路径)")

    # 10) 报告（分级：必需 / 可选）
    print("\n[10/11] 结果报告")
    print("   ── 必需项（缺了无法启动小焦）──")
    if missing:
        for m in sorted(set(missing)):
            print(R("     ❌ " + m))
    else:
        print(G("     ✅ 全部就绪"))
    print("   ── 可选功能（缺了只是对应功能不可用，不影响聊天）──")
    if opt_miss:
        for m in sorted(set(opt_miss)):
            print("     ⓘ " + m)
    else:
        print(G("     ✅ 全部就绪"))

    if not missing:
        print(G("\n   🎉 必需项就绪！运行 `python start_xiaojiao.py` 即可进入小焦（聊天 + 秒级切换可用）。"))
        if opt_miss:
            print("   ℹ️ 上面的可选项不影响启动，想要对应功能再按提示补。")
        print("\n[11/11] 启动小焦 ...")
        if ask("   现在启动? [Y/n] "):
            subprocess.run([sys.executable, os.path.join(ROOT, "start_xiaojiao.py")])
    else:
        print(R("\n   ⛔ 缺必需项，无法进入小焦：%s" % ", ".join(sorted(set(missing)))))
        print("   必需项只有：Python依赖 / llama.cpp(聊天大脑) / 大脑模型 / llama-swap(秒级切换)。")
        print("   （ComfyUI、视频模型、Node.js、猫娘等都属于可选，缺了不影响聊天。）")
        print("   补上必需项后再运行本脚本。")
    print("完成。")


if __name__ == "__main__":
    main()
