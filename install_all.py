# -*- coding: utf-8 -*-
"""🐳 小焦 · 全功能一键安装器
用法：双击 `一键安装.bat`，或：python install_all.py
它会：检测 11 项工具 → 自动下载缺失的(小件全自动, 大件给选项) → 写配置 → 报告哪些可用。
装完(或补完缺失) → 全部功能就能用。
"""
import os, sys, json, shutil, subprocess, urllib.request, zipfile, time, socket

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
    server = ll.get("server") or "llama-server"
    ok_s = os.path.exists(server) or shutil.which("llama-server") is not None
    if not ok_s:
        print("   ❌ 未找到 llama-server.exe（必需！）")
        print("   自动下载 llama.cpp 便携版(必需)...")
        dl = os.path.join(ROOT, "llama.cpp-b.zip")
        ok = download("https://github.com/ggml-org/llama.cpp/releases/download/b4107/llama-b4107-bin-win-cuda-cu12.2-x64.zip", dl, "llama.cpp")
        if ok and unzip(dl, os.path.join(ROOT, "llama.cpp")):
            exe = os.path.join(ROOT, "llama.cpp", "llama-server.exe")
            if os.path.exists(exe):
                ll["server"] = exe.replace("/", "\\")
        try:
            os.remove(dl)
        except Exception:
            pass
        missing.append("llama-server.exe")
    else:
        print("   ✅", server)

    # 3) 大脑模型(不写死: 本地 GGUF 或 云端兼容 API; 统一按"协议连通"检测——真发起请求, 通了才算通过)
    print("\n[3/11] 大脑模型 (本地 GGUF 或 云端 OpenAI 兼容 key) —— 按协议连通检测 ...")
    api = c.setdefault("brain", {}).setdefault("api", {})
    api_key = (api.get("api_key") or "").strip()
    api_url = (api.get("base_url") or "").strip()
    api_model = (api.get("model") or "").strip()

    # 3a) 先看本地: 是否已有大脑服务在线(llama-swap / llama-server 端口)
    local_port = None
    try:
        from urllib.parse import urlparse
        local_port = urlparse(api_url or "http://127.0.0.1:9292/v1").port or 9292
    except Exception:
        local_port = 9292
    local_online = is_port_up(local_port)
    gf = ll.get("gguf") or ""
    ok_g_file = bool(gf) and os.path.exists(gf)
    if not ok_g_file:
        for d in (r"C:/llama", ROOT, os.path.expanduser("~/Downloads"), os.path.expanduser("~")):
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if fn.lower().endswith(".gguf") and os.path.isfile(os.path.join(d, fn)):
                        gf = os.path.join(d, fn); break
                if gf: break
        if gf:
            ok_g_file = True
    # 3b) 本地真连通检测: 对着在线的大脑服务发一次 OpenAI 兼容请求
    local_ok = False; local_msg = ""
    if local_online:
        try:
            url = "http://127.0.0.1:%d/v1" % local_port
            ok, msg = test_cloud_api(url, api_key, api_model)
            local_ok, local_msg = ok, msg
        except Exception as e:
            local_msg = "探测异常: %s" % str(e)[:60]
    if not local_ok:
        local_msg = ("大脑服务未在线(端口 %d). 文件%s; 启动 start_xiaojiao 后会自动拉起大脑." % (local_port, ("已找到 " + gf if ok_g_file else "未找到 GGUF")))
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
        print("   ✅ 本地大脑协议通: %s (%s)" % (("http://127.0.0.1:%d/v1" % local_port), local_msg))
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

    # 4) llama-swap
    print("\n[4/11] llama-swap (秒级切换) ...")
    sw = os.environ.get("XIAOJIAO_LLAMA_SWAP") or ""
    ok_sw = bool(sw) and os.path.exists(sw)
    if not ok_sw:
        found = []
        for d in (r"G:\模型文件\大脑秒计切换", os.path.join(ROOT, "llama-swap")):
            if os.path.isdir(d):
                for root, _, fs in os.walk(d):
                    for f in fs:
                        if f == "llama-swap.exe":
                            found.append(os.path.join(root, f))
        if found:
            sw = found[0]
            c.setdefault("brain", {})["llama_swap_port"] = 9292
            print("   ✅ 自动找到:", sw)
        else:
            print("   ❌ 未找到 llama-swap.exe（必需！秒级切换核心）")
            print("   自动下载 llama-swap(必需)...")
            dl = os.path.join(ROOT, "llama-swap.zip")
            if download("https://github.com/mostlygeek/llama-swap/releases/download/v0.251/llama-swap_0.251_windows_amd64.zip", dl, "llama-swap"):
                if unzip(dl, os.path.join(ROOT, "llama-swap")):
                    for root, _, fs in os.walk(os.path.join(ROOT, "llama-swap")):
                        for f in fs:
                            if f == "llama-swap.exe":
                                sw = os.path.join(root, f)
            try:
                os.remove(dl)
            except Exception:
                pass
            if not sw:
                missing.append("llama-swap.exe")
    if sw:
        c.setdefault("brain", {}).setdefault("api", {})["base_url"] = "http://127.0.0.1:9292/v1"
        c["brain"]["api"]["model"] = "xiaojiao"
        c["brain"]["llama_swap_port"] = 9292
        print("   ✅ llama-swap:", sw)

    # 5) ComfyUI
    print("\n[5/11] ComfyUI (视频大脑) ...")
    comfy = os.environ.get("XIAOJIAO_COMFY_DIR") or ""
    ok_comfy = bool(comfy) and os.path.exists(os.path.join(comfy, "main.py"))
    if not ok_comfy:
        print("   ❌ 未找到 ComfyUI（较大 ~2GB，脚本不自动下）")
        p = input("   请粘贴 ComfyUI 目录(main.py 所在, 回车跳过): ").strip().strip('"')
        if p and os.path.exists(os.path.join(p, "main.py")):
            comfy = p
        else:
            missing.append("ComfyUI")
    if comfy:
        print("   ✅ ComfyUI:", comfy)

    # 5b) 视频秒级切换必需节点: AnyDeviceOffload + WanVideoWrapper(装进 ComfyUI custom_nodes)
    print("\n[5b/11] 视频秒级切换节点(必需) ...")
    cn_dir = os.path.join(comfy, "custom_nodes") if comfy else ""
    node_missing = []
    for node in ("ComfyUI-AnyDeviceOffload", "ComfyUI-WanVideoWrapper"):
        ok_node = os.path.isdir(os.path.join(cn_dir, node)) if cn_dir else False
        if ok_node:
            print("   ✅", node, "已在 custom_nodes")
        else:
            print("   ❌", node, "未安装(必需)")
            # 尝试从 大脑秒计切换 目录/zip 复制
            src_dir = os.path.join(r"G:\模型文件\大脑秒计切换", node) if node == "ComfyUI-AnyDeviceOffload" else ""
            got = False
            if cn_dir and src_dir and os.path.isdir(src_dir):
                try:
                    import shutil as _sh
                    _sh.copytree(src_dir, os.path.join(cn_dir, node), dirs_exist_ok=True)
                    got = os.path.isdir(os.path.join(cn_dir, node))
                    print("   ✅ 已从 大脑秒计切换 复制:", node)
                except Exception as e:
                    print("   ✗ 复制失败:", e)
            if not got:
                node_missing.append(node)
    if node_missing:
        missing.append("视频节点:" + ",".join(node_missing))

    # 6) Wan 视频模型三件套
    print("\n[6/11] Wan2.1 视频模型三件套 ...")
    vroot = os.environ.get("XIAOJIAO_VIDEO_ROOT") or r"G:\模型文件\视频模型"
    ck = os.path.join(vroot, "dit_fp8.safetensors")
    tc = os.path.join(vroot, "umt5_fp8.safetensors")
    va = os.path.join(vroot, "vae_fp8.safetensors")
    ok3 = all(os.path.exists(x) for x in (ck, tc, va))
    if not ok3:
        print("   ❌ 缺视频模型(必需！):", [os.path.basename(x) for x in (ck, tc, va) if not os.path.exists(x)])
        print("   自动从 hf-mirror 下载 Wan2.1-1.3B 三件套(必需, 共~2.5GB)...")
        os.makedirs(vroot, exist_ok=True)
        base = HF_MIRROR + "/Wan-AI/Wan2.1-T2V-1.3B-Diffusers/resolve/main/"
        for f, d in [("dit_fp8.safetensors", ck), ("umt5_fp8.safetensors", tc), ("vae_fp8.safetensors", va)]:
            if not os.path.exists(d):
                download(base + f, d, f)
        if not all(os.path.exists(x) for x in (ck, tc, va)):
            missing.append("Wan 视频模型")
    else:
        print("   ✅ 三件套齐全")

    # 7) Node.js
    print("\n[7/11] Node.js (js 插件) ...")
    if shutil.which("node"):
        print("   ✅ node:", shutil.which("node"))
    else:
        print("   ❌ 未装 Node.js → 到 https://nodejs.org 装 LTS(默认一路下一步)")
        missing.append("Node.js")

    # 8) NVIDIA GPU
    print("\n[8/11] NVIDIA GPU ...")
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=8)
        print("   ✅", r.stdout.strip() if r.returncode == 0 else "未检测到")
    except Exception:
        print("   ❌ 未检测到 N 卡(视频/加速需要)")

    # 8b) 可选功能依赖（不是硬性必需, 缺哪个=哪个功能用不了, 这里全部补齐告知）
    print("\n[8b/11] 可选功能依赖 (缺哪个=哪个功能用不了) ...")
    # N.E.K.O. 猫娘(桌面伙伴, 你下载的开源项目)
    neko_root = ""
    for cand in [os.environ.get("XIAOJIAO_NEKO_DIR", ""), r"G:\moxing__xiaojiao\maoniang\N.E.K.O-main", r"G:\模型文件\猫娘\N.E.K.O-main", r"C:\NEKO\N.E.K.O-main"]:
        if cand and os.path.exists(os.path.join(cand, "launcher.py")):
            neko_root = cand; break
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

    # 10) 报告
    print("\n[10/11] 结果报告")
    if not missing:
        print(G("   🎉 全部必需项就绪！运行 `python start_xiaojiao.py` 即可进入小焦（秒级切换可用）。"))
        print("\n[11/11] 启动小焦 ...")
        if ask("   现在启动? [Y/n] "):
            subprocess.run([sys.executable, os.path.join(ROOT, "start_xiaojiao.py")])
    else:
        print(R("   ⛔ 缺少必需项，无法进入小焦：%s" % ", ".join(sorted(set(missing)))))
        print(R("   秒级切换所需工具(llama-swap/llama.cpp/Wan模型等)为硬性必需，缺一不可。"))
        print("   补上后再运行本脚本，或按上方提示配置。")
    print("完成。")


if __name__ == "__main__":
    main()
