# -*- coding: utf-8 -*-
"""UI 样式与体验检查（真浏览器）：代码块配色/干净底色、表格留白、<think> 剥离

为什么单独一个脚本：这几件事**只能在真浏览器里量**——CSS 计算值、生成的 span 数、
表格列宽是否折行，Python 侧测不出来。改动前端后跑一遍，避免"看着改了其实没生效"。

用法：
    python tests/stress/ui_style_check.py
    python tests/stress/ui_style_check.py --base http://127.0.0.1:5000
退出码：0 = 全部通过；1 = 有失败（截图仍会保存，便于排查）
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
    ap = argparse.ArgumentParser(description="小焦 UI 样式检查")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--out", default="ui_style.png")
    args = ap.parse_args()
    out_path = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 未安装 playwright：python -m pip install playwright")
        return 1

    ok, bad = [], []

    def check(name, cond, detail=""):
        (ok if cond else bad).append(name)
        print("  %s %-42s %s" % ("✅" if cond else "❌", name, str(detail)[:100]), flush=True)

    with sync_playwright() as p:
        launch = {"headless": True}
        chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), "")
        if chrome:
            launch["executable_path"] = chrome
        try:
            browser = p.chromium.launch(**launch)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(args.base, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        sel = None
        for cand in ("#inp", "textarea#inp", "textarea"):
            if page.query_selector(cand):
                sel = cand
                break
        if not sel:
            print("❌ 找不到输入框")
            browser.close()
            return 1

        def ask(msg, wait_for, timeout=200):
            page.fill(sel, msg)
            page.keyboard.press("Enter")
            deadline = time.time() + timeout
            while time.time() < deadline:
                page.wait_for_timeout(1500)
                if wait_for in page.inner_text("body"):
                    page.wait_for_timeout(1200)
                    return True
            return False

        # ---------- 1. 代码块：底色干净 + 有语法配色 ----------
        print("\n[1] 代码块（配色 + 无阴影/无多余底色）")
        got = ask("用 python 写一个计算斐波那契数列的脚本", "```", timeout=120)
        if not got:
            got = ask("抓一下 https://httpbin.org/json", "HTTP 200", timeout=120)
        page.wait_for_timeout(1500)
        if page.query_selector(".codebox"):
            style = page.evaluate("""() => {
                const code=document.querySelector('.codebox pre.code code');
                const pre=document.querySelector('.codebox pre.code');
                const cs=getComputedStyle(code), ps=getComputedStyle(pre);
                return {codeBg:cs.backgroundColor, codeShadow:cs.textShadow, codePad:cs.paddingLeft,
                        preBg:ps.backgroundColor, preShadow:ps.textShadow,
                        toks:code.querySelectorAll('span[class^=tk-]').length,
                        kw:code.querySelectorAll('.tk-kw,.tk-str,.tk-num,.tk-key').length};
            }""")
            check("代码块底色是纯深色（无浅灰方块）",
                  style["preBg"] in ("rgb(13, 17, 23)", "rgba(0, 0, 0, 0)"), style["preBg"])
            check("代码元素自身不再带背景（白色印记根因）",
                  style["codeBg"] in ("rgba(0, 0, 0, 0)", "transparent"), style["codeBg"])
            check("代码块内文字无 text-shadow",
                  style["codeShadow"] in ("none", ""), style["codeShadow"])
            check("代码元素无多余内边距", style["codePad"] in ("0px", "0"), style["codePad"])
            check("语法高亮生效（≥3 个着色片段）", style["toks"] >= 3,
                  "片段=%d 关键词/字符串=%d" % (style["toks"], style["kw"]))
            if style["kw"] >= 2:
                colors = page.evaluate("""() => {
                    const g=s=>{const el=document.querySelector(s);return el?getComputedStyle(el).color:'';};
                    return [g('.tk-kw'),g('.tk-str'),g('.tk-num'),g('.tk-key')].filter(Boolean);
                }""")
                check("不同语法成分颜色确实不同", len(set(colors)) >= 2, str(colors))
        else:
            check("页面上出现代码块", False, "没等到 ``` 围栏内容")

        # ---------- 2. 表格：留白 + 不折断 + 容器变宽 ----------
        print("\n[2] 表格（留白/不折字/容器变宽）")
        got = ask("抓取最近 7 天的高危漏洞", "NVD 漏洞速览", timeout=200)
        check("漏洞表格已渲染", bool(got) and page.query_selector("table") is not None, "")
        if page.query_selector("table"):
            t = page.evaluate("""() => {
                const wrap=document.querySelector('.tblwrap');
                const th=document.querySelector('.tblwrap th');
                const td=document.querySelector('.tblwrap td');
                const b=document.querySelector('.b.wide');
                const feed=document.getElementById('feed');
                const cs=el=>el?getComputedStyle(el):null;
                return {hasWrap:!!wrap, wrapScroll:wrap?cs(wrap).overflowX:'',
                        thPad:th?cs(th).paddingLeft:'', tdPad:td?cs(td).paddingLeft:'',
                        tdNowrap:td?cs(td).whiteSpace:'', thNowrap:th?cs(th).whiteSpace:'',
                        bWide:!!b, bWidth:b?b.clientWidth:0, feedWidth:feed?feed.clientWidth:0,
                        shadow:td?cs(td).textShadow:''};
            }""")
            check("表格外面有横向滚动容器", t["hasWrap"] and t["wrapScroll"] == "auto", t["wrapScroll"])
            check("表头/单元格留白足够（≥10px）",
                  t["thPad"].startswith(("1", "2")) and t["tdPad"].startswith(("1", "2")),
                  "th=%s td=%s" % (t["thPad"], t["tdPad"]))
            check("短列不折字（nowrap），只有摘要列允许折行",
                  t["thNowrap"] == "nowrap" and t["tdNowrap"] == "nowrap",
                  "th=%s td=%s" % (t["thNowrap"], t["tdNowrap"]))
            check("带表格的消息占了更宽的容器", t["bWide"] and t["bWidth"] >= 0.85 * t["feedWidth"],
                  "气泡=%dpx 容器=%dpx" % (t["bWidth"], t["feedWidth"]))
            check("表格文字无阴影", t["shadow"] in ("none", ""), t["shadow"])

        # ---------- 3. <think> 标签不外泄 ----------
        print("\n[3] <think> 标签处理")
        body = page.inner_text("body")
        check("页面上看不到 <think> 标签", "<think>" not in body and "</think>" not in body, "")
        page.screenshot(path=out_path)
        print("\n  截图：%s" % out_path)
        check("控制台无错误", not errors, str(errors[:2]))
        browser.close()

    print("\n" + "=" * 62)
    print("  UI 样式检查：通过 %d / %d" % (len(ok), len(ok) + len(bad)))
    for b in bad:
        print("    ❌ %s" % b)
    print("=" * 62)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
