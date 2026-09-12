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

        # ---------- 0. 布局：居中输入区 / 欢迎卡 / 预设入口 ----------
        print("\n[0] 布局（居中 composer / 欢迎卡 / 预设选择器）")
        page.wait_for_timeout(1200)
        # 先开一个新会话：空状态才应该出现欢迎卡（有历史记录时不该硬塞欢迎卡）
        try:
            page.click("text=➕ 新会话")
            page.wait_for_timeout(1200)
        except Exception:
            pass
        lay = page.evaluate("""() => {
            const c=document.querySelector('.composer');
            const r=c?c.getBoundingClientRect():null;
            const inp=document.getElementById('inp');
            const send=document.querySelector('.cmp-send');
            const sel=document.getElementById('presetSel');
            const chips=document.querySelectorAll('#feed .welcome .chip');
            const w=document.querySelector('#feed .welcome');
            const feed=document.getElementById('feed');
            // 关键：输入区应该和"对话内容列"共用同一根轴线（不是相对整个窗口居中，
            // 因为左边还有侧栏；和消息列对齐才是视觉上正确的居中）
            const firstMsg=document.querySelector('#feed .m .b') || w;
            const fr=firstMsg?firstMsg.getBoundingClientRect():null;
            return {
              hasComposer:!!c, cw:r?Math.round(r.width):0, l:r?r.left:0, rr:r?r.right:0,
              cc:r?(r.left+r.right)/2:0,
              tag:inp?inp.tagName:'', inComposer:!!(c&&inp&&c.contains(inp)),
              sendRadius:send?getComputedStyle(send).borderRadius:'',
              presetOptions:sel?sel.options.length:0,
              welcome:!!w, chips:chips.length,
              feedMid: feed?Math.round(feed.getBoundingClientRect().width/2+feed.getBoundingClientRect().left):0
            };
        }""")
        check("输入区与对话列共用同一轴线（视觉居中）",
              lay["hasComposer"] and lay["feedMid"] and abs(lay["cc"] - lay["feedMid"]) <= 80,
              "输入区中心=%.0f 对话列中心=%d" % (lay["cc"], lay["feedMid"]))
        check("输入区宽度收窄到 820px 量级（不再拉满全屏）", 600 <= lay["cw"] <= 860, "%dpx" % lay["cw"])
        check("输入框是 textarea 且位于输入卡片内",
              lay["tag"] == "TEXTAREA" and lay["inComposer"], lay["tag"])
        check("发送按钮是圆形", lay["sendRadius"].startswith(("50%", "9999px", "20px")), lay["sendRadius"])
        check("预设选择器已出现在输入区（选项 ≥3）", lay["presetOptions"] >= 3, "%d 项" % lay["presetOptions"])
        check("新会话时显示欢迎卡（含示例 chips ≥3）",
              lay["welcome"] and lay["chips"] >= 3, "chips=%d" % lay["chips"])
        # 点一下示例 chip：应该自动填进输入框
        if lay["chips"]:
            page.click("#feed .welcome .chip")
            filled = page.input_value("#inp")
            check("点示例 chip 会把问题填进输入框", bool(filled.strip()), "填入：%s" % filled[:40])
            page.fill("#inp", "")

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
            # 高亮是否生效，改成**喂固定样例**给页面自己的高亮函数 —— 不依赖"这次模型
            # 恰好写了一段够长的代码"（实测偶发只出 1 个片段就假失败）。仍然测的是真函数。
            _hl = page.evaluate("""() => {
                const src = 'def add(a, b):\\n    # 求和\\n    return a + b  # 42\\nprint("hi")';
                const html = (typeof codeBlock === 'function') ? codeBlock(src, 'python') : '';
                const box = document.createElement('div');
                box.innerHTML = html;
                return {toks: box.querySelectorAll('span[class^=tk-]').length,
                        kw: box.querySelectorAll('.tk-kw,.tk-str,.tk-num,.tk-key').length,
                        hasKw: box.querySelectorAll('.tk-kw').length > 0};
            }""")
            check("语法高亮生效（固定 python 样例≥3 个着色片段）", _hl["toks"] >= 3,
                  "片段=%d 关键词/字符串=%d" % (_hl["toks"], _hl["kw"]))
            check("关键字被单独着色（def/return 这类）", _hl["hasKw"], str(_hl)[:70])
            if _hl["kw"] >= 2:
                # 同样用固定样例取色：现场答案里可能只有一种成分，会假失败
                colors = page.evaluate("""() => {
                    const src = 'def add(a, b):\\n    # 求和\\n    return a + b  # 42\\nprint("hi")';
                    const box = document.createElement('div');
                    box.innerHTML = (typeof codeBlock === 'function') ? codeBlock(src, 'python') : '';
                    document.body.appendChild(box);
                    const g = s => { const el = box.querySelector(s); return el ? getComputedStyle(el).color : ''; };
                    const out = [g('.tk-kw'), g('.tk-str'), g('.tk-num'), g('.tk-key')].filter(Boolean);
                    box.remove();
                    return out;
                }""")
                check("不同语法成分颜色确实不同", len(set(colors)) >= 2, str(colors))
        else:
            check("页面上出现代码块", False, "没等到 ``` 围栏内容")

        # ---------- 2. 表格：留白 + 不折断 + 容器变宽 ----------
        print("\n[2] 表格（留白/不折字/容器变宽）")
        got = ask("抓取最近 7 天的高危漏洞", "NVD 漏洞速览", timeout=200)
        # 打字机播完才会把表格挂上 DOM 并给气泡加 .wide → 先等它稳定，再断言/量宽度
        for _ in range(24):
            if page.query_selector(".b.wide .tblwrap table"):
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(400)
        check("漏洞表格已渲染", bool(got) and page.query_selector(".b.wide .tblwrap table") is not None, "")
        if page.query_selector("table"):
            def _measure():
                return page.evaluate("""() => {
                const wrap=document.querySelector('.tblwrap');
                const th=document.querySelector('.tblwrap th');
                const td=document.querySelector('.tblwrap td');
                const b=document.querySelector('.b.wide');
                const feed=document.getElementById('feed');
                const cs=el=>el?getComputedStyle(el):null;
                const wide=[...document.querySelectorAll('.b.wide')].map(x=>x.clientWidth);
                return {hasWrap:!!wrap, wrapScroll:wrap?cs(wrap).overflowX:'',
                        thPad:th?cs(th).paddingLeft:'', tdPad:td?cs(td).paddingLeft:'',
                        tdNowrap:td?cs(td).whiteSpace:'', thNowrap:th?cs(th).whiteSpace:'',
                        bWide:!!b, bWidth:wide.length?Math.max(...wide):0, feedWidth:feed?feed.clientWidth:0,
                        shadow:td?cs(td).textShadow:''};
            }""")
            # 量之前等布局稳定：气泡宽度会随"欢迎卡收起 / 侧栏 / 流式渲染"的收尾动画变化，
            # 实测同一套代码偶尔量到 545px（动画中间态）→ 假失败。最多重试 3 次。
            t = _measure()
            for _ in range(3):
                if t.get("bWidth", 0) >= 1000:
                    break
                page.wait_for_timeout(1000)
                t = _measure()
            check("表格外面有横向滚动容器", t["hasWrap"] and t["wrapScroll"] == "auto", t["wrapScroll"])
            check("表头/单元格留白足够（≥10px）",
                  t["thPad"].startswith(("1", "2")) and t["tdPad"].startswith(("1", "2")),
                  "th=%s td=%s" % (t["thPad"], t["tdPad"]))
            check("短列不折字（nowrap），只有摘要列允许折行",
                  t["thNowrap"] == "nowrap" and t["tdNowrap"] == "nowrap",
                  "th=%s td=%s" % (t["thNowrap"], t["tdNowrap"]))
            # 判据用**绝对宽度**而不是"占容器百分之几"：容器宽度会随侧栏是否展开、
            # 窗口宽度变化（实测同一套代码 1190 与 1400 都出现过），按比例判会偶发误报。
            # 真正的不变量是：带表格的气泡走的是"宽气泡"样式（1140px），比普通 820px 宽。
            check("带表格的消息占了更宽的容器", t["bWide"] and t["bWidth"] >= 1000,
                  "气泡=%dpx 容器=%dpx 宽气泡=%s" % (t["bWidth"], t["feedWidth"], t["bWide"]))
            check("表格文字无阴影", t["shadow"] in ("none", ""), t["shadow"])

        # ---------- 3. <think> 标签不外泄 ----------
        print("\n[3] <think> 标签处理")
        body = page.inner_text("body")
        check("页面上看不到 <think> 标签", "<think>" not in body and "</think>" not in body, "")

        # ---------- 4. 预设下拉 / 提示条位置（真实缺陷：选好的预设名不见了、提示盖住输入区） ----------
        print("\n[4] 预设下拉与轻提示位置")
        # 先问后端"当前到底套用了哪个预设"：没套用就只验下拉本身可用，
        # 不要因为"当前没有预设"就把用例判失败（那是用户状态，不是缺陷）。
        _cur = page.evaluate("""async () => {
          try { const r = await fetch('/api/presets'); const d = await r.json();
                return {file: d.current || '', name: d.current_name || '', n: (d.presets||[]).length}; }
          catch (e) { return {file:'', name:'', n:-1}; }
        }""")
        _ps = page.evaluate("""() => {
          const el = document.getElementById('presetSel');
          if (!el) return {missing:true};
          const r = el.getBoundingClientRect();
          return {value: el.value, text: (el.selectedOptions[0]||{}).text || '',
                  options: [...el.options].length, y: Math.round(r.top), h: Math.round(r.height)};
        }""")
        check("页面上有「预设」下拉且有可选项", (not _ps.get("missing")) and _ps.get("options", 0) >= 2,
              str(_ps)[:90])
        if _cur.get("file"):
            # 有当前预设时，下拉必须把它回填出来（回填不上就会掉回占位项「🎭 预设」）
            check("预设下拉能回填当前预设（不是只剩占位项）",
                  _ps.get("value") == _cur["file"] and _ps.get("text", "").strip() not in ("", "🎭 预设"),
                  "当前=%s 下拉=%s" % (_cur["file"], _ps.get("text")))
        else:
            check("未套用预设时下拉停在占位项（不假报当前预设）",
                  _ps.get("value") == "" and _ps.get("text", "").strip() == "🎭 预设",
                  "下拉=%s" % _ps.get("text"))
        # 触发一次 toast，确认它出现在**顶部**、不会压在输入区那排胶囊上
        _t = page.evaluate("""() => {
          if (typeof toast === 'function') toast('样式自检：提示条位置',400);
          const el = document.getElementById('toast');
          if (!el) return {missing:true};
          const r = el.getBoundingClientRect();
          const cmp = document.querySelector('.composer');
          const c = cmp ? cmp.getBoundingClientRect() : null;
          return {top: Math.round(r.top), bottom: Math.round(r.bottom),
                  cmpTop: c ? Math.round(c.top) : -1, vh: window.innerHeight,
                  overlap: c ? (r.bottom > c.top && r.top < c.bottom) : false};
        }""")
        check("轻提示出现在页面上方（不与输入区重叠）",
              (not _t.get("missing")) and _t.get("top", 999) < _t.get("vh", 0) * 0.5
              and not _t.get("overlap"), str(_t)[:110])

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
