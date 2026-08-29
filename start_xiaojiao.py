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
    server, gguf = resolve_llama_paths()
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
    """启动本地大模型（与原函数保持一致）"""
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
    for _ in range(10):
        try:
            if requests.get("http://127.0.0.1:5001/health", timeout=1).status_code == 200:
                print("✅ DSH 桥接服务已就绪")
                return proc
        except Exception:
            pass
        time.sleep(1)
    print("ℹ️ DSH 桥接已在后台启动（若稍后未就绪，检查 deepseek-harness-sdk 是否安装）。")
    return proc

def main():
    print("=" * 50)
    print("  小焦 · XiaoJiao (含 DSH 插件生态)")
    print(f"  模型名: {MODEL_NAME}")
    print(f"  大脑:   {ENGINE}")
    print("=" * 50)

    # 1. 先启动 DSH 桥接（在大模型加载前，避免资源竞争导致它起不来）
    dsh_proc = start_dsh_bridge()

    # 2. 启动大模型大脑
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