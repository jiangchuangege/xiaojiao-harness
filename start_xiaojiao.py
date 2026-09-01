# -*- coding: utf-8 -*-
"""
小焦 · 一键启动（把她融合成一套：大模型大脑 + 小焦壳 + Web + DSH插件桥接）

用法：  python start_xiaojiao.py
它做五件事：
  1. 读取「操控文件」xiaojiao_control.json
  2. 若配置了本地大模型(GGUF) → 自动用 llama.cpp 跑起来
  3. 启动小焦的 Web 界面
  4. 自动打开浏览器
  5. 启动 DSH 桥接服务（用于调用 DeepSeek Harness 社区插件）
"""
import os
import shutil, sys, time, threading, webbrowser, subprocess
import requests
import xiaojiao_app as app

CONTROL = app.CONTROL
MODEL_NAME = app.MODEL_NAME
BRAIN = CONTROL.get("brain", {})
ENGINE = BRAIN.get("engine", "auto")

# DSH 配置
DSH_ENABLED = CONTROL.get("dsh", {}).get("enabled", False)


def resolve_llama_paths():
    """解析大模型路径：控制文件(存在才用) -> 环境变量(XIAOJIAO_LLAMA_SERVER/XIAOJIAO_GGUF) -> 自动查找。换电脑不用改代码。"""
    server = BRAIN.get("llama", {}).get("server", "")
    gguf = BRAIN.get("llama", {}).get("gguf", "")
    if not (os.path.exists(server) and os.path.exists(gguf)):
        server = os.environ.get("XIAOJIAO_LLAMA_SERVER", server) or ""
        gguf = os.environ.get("XIAOJIAO_GGUF", gguf) or ""
    if not os.path.exists(server):
        server = shutil.which("llama-server") or ""
        if not server:
            for d in ("C:/llama", ".", "..", os.path.expanduser("~")):
                c = os.path.join(d, "llama-server.exe")
                if os.path.exists(c):
                    server = c; break
    if not (gguf and os.path.exists(gguf)):
        gguf = ""
        for d in ("C:/llama", ".", "..", os.path.expanduser("~/Downloads"), os.path.expanduser("~")):
            if not os.path.isdir(d): continue
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(".gguf"):
                    gguf = os.path.join(d, fn); break
            if gguf: break
    return server, gguf

def start_llama_brain():
    """启动本地大模型。小焦脑优先走 llama-swap(9292)；若已在线则跳过冗余8080直连，避免冲突/占显存/卡住。"""
    try:
        api = BRAIN.get("api", {}); burl = (api.get("base_url") or "").lower()
        if "9292" in burl and requests.get("http://127.0.0.1:9292/v1/models", timeout=3).status_code == 200:
            print("✅ 大脑已由 llama-swap(9292) 管理，跳过冗余直连(8080)。")
            return None
    except Exception:
        pass
    server, gguf = resolve_llama_paths()
    port = int(BRAIN.get("llama", {}).get("port", 8080))
    if not (server and gguf and os.path.exists(server) and os.path.exists(gguf)):
        print(f"⚠️ 没找到大模型文件/服务，跳过自动启动（小焦将用自建模型兜底）。")
        return None
    ctx = BRAIN.get("llama", {}).get("ctx", 32768)
    print(f"🧠 正在启动大脑 {MODEL_NAME} (~{os.path.getsize(gguf)/1e9:.1f}GB) ...")
    proc = subprocess.Popen(
        [server, "-m", gguf, "--port", str(port), "-c", str(ctx)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 等待模型就绪
    for _ in range(120):
        try:
            if requests.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                print(f"✅ 大脑 {MODEL_NAME} 已就绪 (port {port})")
                return proc
        except Exception:
            pass
        time.sleep(2)
    print(f"⚠️ 大脑启动超时（可能在加载模型），小焦仍会尝试连接。")
    return proc

def start_pet():
    """自动启动桌面宠物(Electron 透明窗)。若 npm start 失败则用 pywebview 版。"""
    try:
        import subprocess as _sp
        root = os.path.dirname(os.path.abspath(__file__))
        desk = os.path.join(root, "desktop")
        exe = os.path.join(desk, "node_modules", "electron", "dist", "electron.exe")
        if os.path.exists(exe):
            # 直接用 electron.exe 启动(比 npm start 可靠), 加载 /pet
            _sp.Popen([exe, desk], cwd=desk, creationflags=subprocess.CREATE_NO_WINDOW)
            print("🐳 桌面宠物已启动 (Electron 透明窗)")
            return True
        # 兜底: pywebview
        _sp.Popen([sys.executable, os.path.join(root, "jarvis_desktop.py")], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
        print("🐳 桌面宠物已启动 (pywebview)")
        return True
    except Exception as e:
        print("⚠️ 宠物启动失败:", str(e)[:50])
        return False


def start_dsh_bridge():
    """启动 DSH 桥接 HTTP 服务（端口 5001）"""
    if not DSH_ENABLED:
        print("ℹ️ DSH 桥接未启用（dsh.enabled = false），跳过。")
        return None

    bridge_path = os.path.join(os.path.dirname(__file__), "dsh_bridge", "main.py")
    if not os.path.exists(bridge_path):
        print(f"⚠️ 未找到 DSH 桥接服务文件：{bridge_path}")
        return None

    print("🌉 正在启动 DSH 桥接服务（端口 5001）...")
    proc = subprocess.Popen(
        [sys.executable, bridge_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(__file__)
    )
    # 等待服务就绪（最多 10 秒；一起就绪最理想，没就绪也不报错，稍后会自动可用）
    for _ in range(25):
        try:
            if requests.get("http://127.0.0.1:5001/health", timeout=1).status_code == 200:
                print("✅ DSH 桥接服务已就绪")
                return proc
        except Exception:
            pass
        time.sleep(1)
    print("✅ DSH 桥接已在后台启动（端口5001）；首次需加载 deepseek_harness，稍等几秒就绪。")
    return proc

def start_llama_swap():
    """自动启动 llama-swap(多大脑热切换管理器)。独立端口9292, 不冲突直接大脑8080。"""
    exe = os.environ.get("XIAOJIAO_LLAMA_SWAP") or r"G:\模型文件\大脑秒计切换\llama-swap_251_windows_amd64\llama-swap.exe"
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llama-swap.yaml")
    if not (os.path.exists(exe) and os.path.exists(cfg)):
        print("  [llama-swap] 未找到(exe或配置)，跳过")
        return None
    try:
        import socket
        s = socket.socket(); s.settimeout(0.8)
        try:
            s.connect(("127.0.0.1", 9292)); s.close()
            print("  [llama-swap] 已在运行(9292)"); return None
        except Exception:
            pass
        finally:
            s.close()
        proc = subprocess.Popen([exe, "--config", cfg, "--listen", "127.0.0.1:9292"],
                                cwd=os.path.dirname(exe), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("  [llama-swap] 已启动(9292) —— 多大脑秒级切换管理")
        return proc
    except Exception as e:
        print("  [llama-swap] 启动失败: %s" % e); return None


def main():
    print("=" * 50)
    print("  小焦 · XiaoJiao (含 DSH 插件生态)")
    print(f"  模型名: {MODEL_NAME}")
    print(f"  大脑:   {ENGINE}")
    print("=" * 50)

    # 1. 先启动 DSH 桥接（在大模型加载前，避免资源竞争导致它起不来）
    dsh_proc = start_dsh_bridge()

    # 2b. 先拉起 llama-swap(9292), 让大脑由它管理(8080直连会检测到9292后自动跳过)
    llama_swap_proc = start_llama_swap()

    # 2. 启动大模型大脑(若9292在线则跳过冗余8080, 不再卡)
    llama_proc = None
    if ENGINE in ("auto", "llama"):
        llama_proc = start_llama_brain()

    # 3. 确定 Web 端口
    port = 5000
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except:
            port = 5000
    else:
        port = int(CONTROL.get("web_port", os.environ.get("PORT", 5000)))
    os.environ["PORT"] = str(port)

    # 3b. 自动启动桌面宠物(你重启 start_xiaojiao 就带起宠物)
    start_pet()

    # 4. 打开浏览器
    print(f"🌐 启动小焦 Web: http://127.0.0.1:{port}")
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    # 5. 启动 Web 服务（阻塞在这里）
    try:
        app.app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        # 清理所有子进程
        if llama_proc:
            try:
                llama_proc.kill()
            except:
                pass
        if dsh_proc:
            try:
                dsh_proc.kill()
            except:
                pass
        print("🛑 所有服务已关闭。")

if __name__ == "__main__":
    main()