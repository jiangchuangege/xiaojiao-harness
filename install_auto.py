# -*- coding: utf-8 -*-
"""🐳 小焦 · 一键安装/启动
用法：双击 `一键安装.bat`，或：python install_auto.py
它会：装依赖 → 检查/提示大模型位置 → 自动识别路径 → 启动小焦。
"""
import os, sys, subprocess, json

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(cmd, show=True):
    print("  >", cmd)
    subprocess.run(cmd, shell=True)


def check_model():
    """检查大模型服务/文件，给出新手指引。"""
    try:
        c = json.load(open(os.path.join(ROOT, "xiaojiao_control.json"), encoding="utf-8"))
    except Exception:
        c = {}
    ll = c.get("brain", {}).get("llama", {})
    server, gguf = ll.get("server", ""), ll.get("gguf", "")
    ok_s = os.path.exists(server)
    ok_g = os.path.exists(gguf)
    print("\n🔍 检查大模型：")
    print("   llama-server:", server, "→", "✅ 存在" if ok_s else "❌ 未找到")
    print("   模型文件:    ", gguf, "→", "✅ 存在" if ok_g else "❌ 未找到")
    if not ok_s:
        print("   💡 请安装 llama.cpp（llama-server.exe），或用环境变量 XIAOJIAO_LLAMA_SERVER 指定路径。")
    if not ok_g:
        print("   💡 请把模型 .gguf 放到 C:/llama、本项目目录或 Downloads，或用环境变量 XIAOJIAO_GGUF 指定。")
    return ok_s and ok_g


def main():
    print("=" * 52)
    print("🐳 小焦 XiaoJiao · 一键安装 / 启动")
    print("=" * 52)
    # 1) 依赖
    print("\n[1/3] 安装 Python 依赖 ...")
    run('"%s" -m pip install -r "%s"' % (sys.executable, os.path.join(ROOT, "requirements.txt")))
    # 2) 模型
    print("\n[2/3] 检查大模型 ...")
    check_model()
    # 3) 启动
    print("\n[3/3] 启动小焦（大模型 + Web + DSH 桥接）...")
    run('"%s" "%s"' % (sys.executable, os.path.join(ROOT, "start_xiaojiao.py")))
    print("\n✅ 完成。浏览器打开 http://127.0.0.1:5000")


if __name__ == "__main__":
    main()
