# -*- coding: utf-8 -*-
"""
小焦 · 一键启动（把她融合成一套：大模型大脑 + 小焦壳 + Web + N.E.K.O. 猫娘）

用法：  python start_xiaojiao.py
它做五件事：
  1. 读取「操控文件」xiaojiao_control.json
  2. 若配置了本地大模型(GGUF) → 自动用 llama.cpp 跑起来
  3. 启动小焦的 Web 界面
  4. 自动打开浏览器
  5. 拉起 N.E.K.O. 猫娘(48911/48912) + 后台学习
"""
import os
import shutil, sys, time, threading, webbrowser, subprocess
import requests
import xiaojiao_app as app

CONTROL = app.CONTROL
MODEL_NAME = app.MODEL_NAME
BRAIN = CONTROL.get("brain", {})
ENGINE = BRAIN.get("engine", "auto")


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

def start_neko():
    """启动 N.E.K.O. 猫娘(融合进小焦一键启动): 起 memory_server + main_server, 后台学习你的需求, 打开猫娘页面。
    支持两种形态(自动探测, 找不到就跳过, 不阻塞小焦):
      - Steam 版:  G:\\SteamLibrary\\steamapps\\common\\n.e.k.o  (N.E.K.O.exe 拉起 48911/48912)
      - 源码克隆版: 任意 N.E.K.O-main/launcher.py  + .venv
    路径可改(用户下载位置不同): XIAOJIAO_NEKO_DIR 环境变量优先。"""
    import subprocess as _sp
    # 1) 定位 N.E.K.O. 项目根: 优先环境变量 -> Steam 版 -> 源码克隆版候选
    roots = []
    env_neko = os.environ.get("XIAOJIAO_NEKO_DIR", "")
    if env_neko:
        roots.append(env_neko)
    roots += [
        r"G:\SteamLibrary\steamapps\common\n.e.k.o",        # Steam 版(你实际的)
        r"C:\Program Files (x86)\Steam\steamapps\common\n.e.k.o",
        r"C:\Program Files\Steam\steamapps\common\n.e.k.o",
        r"G:\moxing__xiaojiao\maoniang\N.E.K.O-main",       # 源码克隆版
        r"G:\模型文件\猫娘\N.E.K.O-main", r"C:\NEKO\N.E.K.O-main",
    ]
    # 先找含 N.E.K.O.exe(Steam版) 或 launcher.py(源码版) 的根
    root = None
    for r_ in roots:
        if os.path.exists(os.path.join(r_, "N.E.K.O.exe")):
            root = r_; break
    if not root:
        root = next((r_ for r_ in roots if os.path.exists(os.path.join(r_, "launcher.py"))), None)
    if not root:
        print("⚠️ 未找到 N.E.K.O. 猫娘(设 XIAOJIAO_NEKO_DIR 指向其目录, 或装 Steam 版于 n.e.k.o)")
        return None

    steam_exe = os.path.join(root, "N.E.K.O.exe")            # Steam 版入口
    launcher = os.path.join(root, "launcher.py")             # 源码版入口
    py = os.path.join(root, ".venv", "Scripts", "python.exe")

    def _alive(port):
        try:
            import socket as _s
            s = _s.socket(); s.settimeout(2); s.connect(("127.0.0.1", port)); s.close(); return True
        except Exception:
            return False

    # 2) 确保 48911/48912 在线; 都不在线才尝试拉起
    if not (_alive(48911) or _alive(48912)):
        if os.path.exists(steam_exe):
            _sp.Popen([steam_exe], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
            print("🐱 [N.E.K.O] Steam 版已启动(N.E.K.O.exe, 起 48911/48912)")
        elif os.path.exists(launcher) and os.path.exists(py):
            _sp.Popen([py, "-m", "app.memory_server"], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
            _sp.Popen([py, "-m", "app.main_server"], cwd=root, creationflags=subprocess.CREATE_NO_WINDOW,
                      env={**os.environ, "PYTHONUTF8": "1"})
            print("🐱 [N.E.K.O] 源码版已启动(memory_server:48912 + main_server:48911)")
        else:
            print("⚠️ [N.E.K.O] 在 %s 但既无 N.E.K.O.exe 也无可用 launcher.py/.venv, 跳过自动拉起" % root)
            return root
    else:
        print("🐱 [N.E.K.O] 已在运行(48911/48912 在线)")

    # 3) 后台学习通道(每 5 分钟学一次你与猫娘的对话)
    try:
        learn = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learn_from_neko.py")
        if os.path.exists(learn):
            _sp.Popen([sys.executable, learn, "--daemon", "--interval", "300"],
                      cwd=os.path.dirname(os.path.abspath(__file__)), creationflags=subprocess.CREATE_NO_WINDOW)
            print("🎓 [N.E.K.O] 后台学习通道已启动(每5分钟学你与猫娘的对话)")
    except Exception:
        pass
    return root


def start_llama_swap():
    """自动启动 llama-swap(多大脑热切换管理器)。独立端口9292, 不冲突直接大脑8080。
    路径多候选自动检测(不写死, 兼容移动位置): 环境变量/常见位置。"""
    env_exe = os.environ.get("XIAOJIAO_LLAMA_SWAP", "")
    cands = [env_exe] if env_exe else []
    cands += [
        r"G:\moxing__xiaojiao\大脑秒计切换\llama-swap_251_windows_amd64\llama-swap.exe",
        r"G:\模型文件\大脑秒计切换\llama-swap_251_windows_amd64\llama-swap.exe",
        r"G:\模型文件\大脑秒计切换\llama-swap.exe",
        r"G:\moxing__xiaojiao\大脑秒计切换\llama-swap.exe",
    ]
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llama-swap.yaml")
    exe = next((c for c in cands if c and os.path.exists(c)), "")
    if not (exe and os.path.exists(cfg)):
        print("  [llama-swap] 未找到(exe或配置)，跳过 —— 聊天大脑不会跟起，请设 XIAOJIAO_LLAMA_SWAP 或检查 llama-swap.exe")
        return None
    print("  [llama-swap] exe: %s" % exe)
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

    # 3c. 融合 N.E.K.O. 猫娘: 起它的服务 + 后台学习你的需求 + 打开猫娘页
    neko_root = start_neko()

    # 4. 打开浏览器
    print(f"🌐 启动小焦 Web: http://127.0.0.1:{port}")
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    # 打开 N.E.K.O. 猫娘页
    if neko_root:
        threading.Timer(3.5, lambda: webbrowser.open("http://127.0.0.1:48911")).start()
        print("🐱 猫娘已上线: http://127.0.0.1:48911")

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
        print("🛑 所有服务已关闭。")

if __name__ == "__main__":
    main()