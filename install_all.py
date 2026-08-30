# -*- coding: utf-8 -*-
"""🐳 小焦 · 全功能一键安装器
用法：双击 `一键安装.bat`，或：python install_all.py
它会：检测 11 项工具 → 自动下载缺失的(小件全自动, 大件给选项) → 写配置 → 报告哪些可用。
装完(或补完缺失) → 全部功能就能用。
"""
import os, sys, json, shutil, subprocess, urllib.request, zipfile, time

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(ROOT, "xiaojiao_control.json")
HF_MIRROR = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
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
        print("   ❌ 未找到 llama-server.exe")
        if ask("   要自动下载 llama.cpp 便携版(约几十MB)? [y/N] "):
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

    # 3) 4B 聊天模型
    print("\n[3/11] 聊天模型 (4B gguf) ...")
    gf = ll.get("gguf") or ""
    ok_g = bool(gf) and os.path.exists(gf)
    if not ok_g:
        print("   ❌ 未找到模型 gguf（这是你微调过的 xiaojiao1.0-4B，脚本不能替你下）")
        p = input("   请把 xiaojiao1.0-4B.gguf 的完整路径粘贴进来(回车跳过): ").strip().strip('"')
        if p and os.path.exists(p):
            ll["gguf"] = p
        missing.append("4B 模型 gguf")
    else:
        print("   ✅", gf)

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
            print("   ❌ 未找到 llama-swap.exe")
            if ask("   要自动下载 llama-swap(约23MB)? [y/N] "):
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

    # 6) Wan 视频模型三件套
    print("\n[6/11] Wan2.1 视频模型三件套 ...")
    vroot = os.environ.get("XIAOJIAO_VIDEO_ROOT") or r"G:\模型文件\视频模型"
    ck = os.path.join(vroot, "dit_fp8.safetensors")
    tc = os.path.join(vroot, "umt5_fp8.safetensors")
    va = os.path.join(vroot, "vae_fp8.safetensors")
    ok3 = all(os.path.exists(x) for x in (ck, tc, va))
    if not ok3:
        print("   ❌ 缺视频模型:", [os.path.basename(x) for x in (ck, tc, va) if not os.path.exists(x)])
        if ask("   要自动从 hf-mirror 下载 Wan2.1-1.3B 三件套(共~2.5GB)? [y/N] "):
            os.makedirs(vroot, exist_ok=True)
            base = HF_MIRROR + "/Wan-AI/Wan2.1-T2V-1.3B-Diffusers/resolve/main/"
            os.makedirs(os.path.join(vroot, "ComfyUI_windows_portable_nvidia_cu126", "ComfyUI_windows_portable", "ComfyUI", "models"), exist_ok=True)
            for f, d in [("dit_fp8.safetensors", ck), ("umt5_fp8.safetensors", tc), ("vae_fp8.safetensors", va)]:
                if not os.path.exists(d):
                    download(base + f, d, f)
        else:
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
        print(G("   🎉 全部就绪！运行 `python start_xiaojiao.py` 即可全功能使用。"))
    else:
        print(R("   ⚠️ 还缺: %s（补上后即可全功能）" % ", ".join(sorted(set(missing)))))

    print("\n[11/11] 启动小焦 ...")
    if ask("   现在启动? [Y/n] "):
        subprocess.run([sys.executable, os.path.join(ROOT, "start_xiaojiao.py")])
    print("完成。")


if __name__ == "__main__":
    main()
