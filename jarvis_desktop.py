# jarvis_desktop.py —— 桌面贾维斯悬浮窗(MVP): 透明+置顶+可拖动, 加载小焦 /pet 全息核心
# 用法: python jarvis_desktop.py  (需已启动小焦后端 5000)
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
try:
    import webview
except Exception as e:
    print("需要 pywebview: pip install pywebview |", e)
    sys.exit(1)


def main():
    url = "http://127.0.0.1:5000/pet"
    try:
        import requests
        requests.get(url, timeout=3)
    except Exception:
        print("⚠️ 小焦后端(5000)未启动，请先运行 start_xiaojiao.py")
        sys.exit(1)
    w = webview.create_window(
        "J.A.R.V.I.S.",
        url,
        width=340, height=300,
        frameless=True,          # 无边框
        on_top=True,             # 置顶
        transparent=True,        # 透明背景
        easy_drag=True,          # 可拖动
        resizable=False,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
