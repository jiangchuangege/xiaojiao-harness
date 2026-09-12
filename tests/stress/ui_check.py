# -*- coding: utf-8 -*-
"""UI 渲染真实检查：用 Playwright 真开浏览器打开小焦，发消息、等回答、截图

用途：补上"Markdown/代码块/链接/标题 只在浏览器里渲染，Python 测不到"这个缺口。
做法是**真的打开页面、真的发消息、真的渲染**，并把控制台错误与截图一起留证。

前置：① 小焦正在运行；② 装了 playwright（`pip install playwright`；
      无自带内核时可用自备 Chrome，脚本会优先找常见 Chrome 路径）

用法：
    python tests/stress/ui_check.py
    python tests/stress/ui_check.py --base http://127.0.0.1:5000 --out ui_chat.png
退出码：0 = UI 可用且回答已渲染；1 = 有异常（截图仍会保存，便于排查）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHROME_CANDIDATES = [
    os.path.join(REPO_ROOT, "chrome-win64", "chrome.exe"),
    os.path.expanduser(r"~\chrome-win64\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="小焦 UI 渲染真实检查")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--out", default="ui_chat.png")
    ap.add_argument("--message", default="抓一下 https://example.com")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 未安装 playwright：python -m pip install playwright")
        return 1

    out_path = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)
    with sync_playwright() as p:
        launch_kw = {"headless": True}
        chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), "")
        if chrome:
            launch_kw["executable_path"] = chrome
            print("使用自备 Chrome: %s" % chrome)
        try:
            browser = p.chromium.launch(**launch_kw)
        except Exception as e:
            print("自备 Chrome 启动失败（%s），改用 Playwright 自带内核…" % str(e)[:120])
            browser = p.chromium.launch(headless=True)

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console.error: %s" % m.text) if m.type == "error" else None)

        print("打开 %s …" % args.base)
        page.goto(args.base, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        print("  标题: %s" % page.title())

        sel = None
        for cand in ("#inp", "textarea#inp", "textarea", "input[type=text]"):
            if page.query_selector(cand):
                sel = cand
                break
        if not sel:
            page.screenshot(path=out_path, full_page=True)
            print("❌ 找不到输入框；已保存截图 %s" % out_path)
            browser.close()
            return 1

        page.fill(sel, args.message)
        page.keyboard.press("Enter")
        print("  已发送：%s" % args.message)

        text = ""
        deadline = time.time() + 150
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
            if any(k in text for k in ("Example Domain", "小焦解读", "抓取失败", "HTTP 200")):
                break

        pre_cnt = len(page.query_selector_all("pre.code"))
        a_cnt = len(page.query_selector_all(".b a"))
        h_cnt = len(page.query_selector_all(".b .mdh"))
        print("  渲染检查：pre.code=%d · 正文链接=%d · 标题块=%d" % (pre_cnt, a_cnt, h_cnt))
        print("  控制台错误：%d 条 %s" % (len(errors), errors[:3]))
        page.screenshot(path=out_path)
        print("  截图：%s" % out_path)
        browser.close()

    rendered = any(k in text for k in ("Example Domain", "小焦解读", "HTTP 200"))
    print("\n结论：%s" % ("✅ UI 可用、回答已渲染" if rendered else "⚠️ 未检测到回答，请看截图"))
    if errors:
        print("提示：控制台有 %d 条错误，建议排查（截图已保存）" % len(errors))
    return 0 if rendered else 1


if __name__ == "__main__":
    sys.exit(main())
