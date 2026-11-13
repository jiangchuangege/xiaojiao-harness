# -*- coding: utf-8 -*-
"""网页截图：把「它正在看的那个页面」拍成一张图给用户看。

总纲（与本项目所有模块一致）：
    小焦系统本身不依赖任何具体模型。它是完整的载体（器官齐全），模型是火种（可替换）。
    接入任何模型 → 系统活；换任何模型 → 系统不变。
    这就是"模型平等"和"变形金刚"的工程基础。

为什么需要它（用户原话："最好设计个真实的屏幕一样的，我能真的看到他们在看"）：
    模型说"我打开了 X 页面、上面写着…"——用户无法核实。
    这里在**同一个 URL** 上真开一次浏览器、截一张图，用户直接看画面。
    它不能证明模型真读了内容，但能证明**这个页面长什么样**，
    比模型复述可靠得多（复述可能是编的，截图不会）。

边界：GitHub 等站点禁止 iframe 嵌入，所以用"截图"而不是"嵌页面"。
"""
import hashlib
import logging
import os
import re
import time

LOG = logging.getLogger(__name__)

_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "weblog")
_BROWSER = "G:/xiaojiao harness/chrome-win64/chrome.exe"

_TOOLS = [
    {
        "name": "web_shot",
        "description": "把一个网页**拍成截图**给用户看（真开浏览器）。什么时候用：你刚用 get/fetch "
                       "打开了一个网页、或要向用户证明『这个页面长什么样』时；输入 url；"
                       "输出 截图路径（前端会直接显示图片）。",
        "parameters": {"type": "object",
                       "properties": {"url": {"type": "string", "description": "要截图的网址"}},
                       "required": ["url"]},
    },
]


def _shot(url):
    from playwright.sync_api import sync_playwright
    os.makedirs(_OUT, exist_ok=True)
    h = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
    path = os.path.join(_OUT, h + ".png")
    with sync_playwright() as p:
        exe = _BROWSER if os.path.exists(_BROWSER) else None
        b = p.chromium.launch(headless=True, executable_path=exe) if exe else p.chromium.launch(headless=True)
        try:
            pg = b.new_page(viewport={"width": 1280, "height": 800})
            pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(1800)          # 让首屏渲染完
            title = (pg.title() or "")[:80]
            pg.screenshot(path=path, full_page=False)
        finally:
            b.close()
    return path, title


class WebShotPlugin:
    def get_tool_descriptions(self):
        return list(_TOOLS)

    def execute(self, tool_name, params):
        p = params or {}
        if tool_name != "web_shot":
            return "这个插件只有 web_shot（给网页拍截图）。"
        url = str(p.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            return "请给以 http:// 或 https:// 开头的完整网址。"
        try:
            path, title = _shot(url)
        except Exception as e:      # noqa: BLE001 — 截不到要如实说
            LOG.debug("web_shot 失败：%s", e)
            return "截图失败：%s: %s" % (type(e).__name__, str(e)[:120])
        rel = os.path.relpath(path, os.path.dirname(_OUT)).replace("\\", "/")
        # 前端见到 `/weblog/xxx.png` 会直接渲染成图片
        return ("✅ 已截图：%s\n标题：%s\n\n![页面截图](/%s)"
                % (url, title or "（无标题）", rel))


_PLUGIN = WebShotPlugin()


def get_plugin():
    return _PLUGIN
