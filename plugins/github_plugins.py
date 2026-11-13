# -*- coding: utf-8 -*-
"""GitHub 插件来源：去真实社区**找现成的**，下载、验过、装上。

总纲（与本项目所有模块一致）：
    小焦系统本身不依赖任何具体模型。它是完整的载体（器官齐全），模型是火种（可替换）。
    接入任何模型 → 系统活；换任何模型 → 系统不变。
    这就是"模型平等"和"变形金刚"的工程基础。

为什么要有它（用户原话）：
    "一直给我写垃圾插件，我让他们去 github 找好插件下载，他们就是不听。"
    —— 光靠提示词压不住 4B。所以把"找现成"变成**一条能真跑的路**：
    搜 GitHub → 拿 raw 文件 → 过 `plugin_forge` 的静态检查 + 沙箱试跑 → 通过才落盘。

【硬规定·必须满足】（不满足一律不收）
    ① 单文件 `.py`，定义 `class XxxPlugin` 且含 `get_tool_descriptions()` / `execute()`
    ② 文件末尾有 `def get_plugin(): return <类>()`
    ③ 每个工具的说明里必须有「什么时候用」，参数是标准 JSON Schema
    ④ `execute()` 返回**中文**字符串，给了真参数能跑通（沙箱会实测三组）
    ⑤ import 只用白名单里的纯功能库（禁 subprocess / eval / exec / os.system）
    ⑥ 文件名必须以 `gen_` 开头 —— 这样小焦会**自动给它生成独立 UI 模块**

【硬规定·不支持】（遇到直接拒，不要浪费时间）
    · 需要 API Key / 账号登录才能用的
    · 依赖 GUI、浏览器自动化、桌面窗口的
    · 要长驻进程 / 开端口 / 装系统服务的
    · 多文件包、需要 pip 装额外依赖的
    · 会删/改用户文件的
"""
import json
import logging
import os
import re
import time

LOG = logging.getLogger(__name__)

# 这段会被注入群聊/forger 的提示词 —— 让它们照着这个标准去找，而不是瞎写。
SPEC_MUST = [
    "单文件 .py；class XxxPlugin 里有 get_tool_descriptions() 和 execute()",
    "文件末尾 def get_plugin(): return 类实例()",
    "每个工具说明里必须写「什么时候用」；parameters 是标准 JSON Schema",
    "execute() 返回中文字符串；给真参数能跑通（沙箱会实测）",
    "只 import 纯功能库；禁 subprocess / eval / exec / os.system / 删文件",
    "文件名必须以 gen_ 开头（小焦会自动给它生成 UI 模块）",
]
SPEC_NO = [
    "需要 API Key / 登录才能用",
    "依赖 GUI / 浏览器 / 桌面窗口",
    "要长驻进程 / 开端口 / 装系统服务",
    "多文件包 / 需要额外 pip 依赖",
    "会删改用户文件",
]

_TOOLS = [
    {
        "name": "github_find_plugin",
        "description": "去 GitHub 搜**现成的**插件/工具库（不要自己写）。什么时候用：群里/用户要求"
                       "『去找个现成的 XX 插件』、或你想加能力但不想从零写；输入 keyword；"
                       "输出 仓库列表（名字/星数/说明/链接）。",
        "parameters": {"type": "object",
                       "properties": {"keyword": {"type": "string", "description": "关键词，如 dsh plugin / bilibili mcp"}},
                       "required": ["keyword"]},
    },
    {
        "name": "github_install_plugin",
        "description": "把一个 GitHub 上的 .py 插件下载下来，**过沙箱验过才装**。什么时候用：已经"
                       "用 github_find_plugin 找到候选、要真装上时；输入 raw_url；输出 装没装成 + 原因。",
        "parameters": {"type": "object",
                       "properties": {"raw_url": {"type": "string", "description": "raw.githubusercontent.com 的 .py 直链"},
                                      "name": {"type": "string", "description": "可选：装成什么名字（会自动加 gen_ 前缀）"}},
                       "required": ["raw_url"]},
    },
    {
        "name": "plugin_spec",
        "description": "看**插件硬规定**（什么样的能用、什么样的不支持）。什么时候用：要写或要找插件之前"
                       "先读这个，别凭感觉；无参数；输出 必须满足的 6 条 + 不支持的 5 类。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


def _gh_search(keyword, per_page=8):
    import requests
    url = "https://api.github.com/search/repositories"
    r = requests.get(url, params={"q": keyword, "sort": "stars", "order": "desc",
                                  "per_page": per_page},
                     headers={"Accept": "application/vnd.github+json",
                              "User-Agent": "xiaojiao"},
                     timeout=25)
    if r.status_code != 200:
        return [], "GitHub 返回 HTTP %s" % r.status_code
    items = (r.json() or {}).get("items") or []
    out = []
    for it in items[:per_page]:
        out.append({"full_name": it.get("full_name"), "stars": it.get("stargazers_count"),
                    "desc": (it.get("description") or "")[:120], "url": it.get("html_url")})
    return out, ""


def _install(raw_url, name=""):
    """下载 → 静态检查 → 沙箱试跑 → 通过才写进 plugins/。"""
    import requests
    from core import plugin_forge as PF

    if "raw.githubusercontent.com" not in raw_url and "github.com" not in raw_url:
        return False, "只接受 GitHub 的直链（raw.githubusercontent.com）"
    try:
        r = requests.get(raw_url, timeout=30, headers={"User-Agent": "xiaojiao"})
    except Exception as e:      # noqa: BLE001
        return False, "下载失败：%s" % str(e)[:100]
    if r.status_code != 200 or ".py" not in raw_url:
        return False, "不是可用的 .py（HTTP %s）" % r.status_code
    code = r.text
    if len(code) > 200000:
        return False, "文件太大（%d 字节），拒绝" % len(code)
    # 硬规定里的"不支持"先筛一遍
    low = code.lower()
    for bad, why in (("api_key", "需要 API Key"), ("tkinter", "依赖 GUI"),
                     ("pyqt", "依赖 GUI"), ("selenium", "依赖浏览器"),
                     ("playwright", "依赖浏览器"), ("http.server", "要开端口"),
                     ("subprocess", "会起子进程"), ("os.system", "会起子进程")):
        if bad in low:
            return False, "不符合规定：%s" % why
    ok, why, tools = PF.static_check(code), "", []
    if not ok:
        return False, "静态检查没过：%s" % str(why)[:120]
    # 落临时文件过沙箱
    safe = re.sub(r"[^A-Za-z0-9_]", "_", (name or os.path.basename(raw_url)))[:40]
    fname = "gen_" + (safe if safe.startswith("gen_") else "gh_" + safe) + ".py"
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "plugins", fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    ok2, why2, tools2 = PF.sandbox_run(path)
    if not ok2:
        try:
            os.remove(path)          # 验不过就撤掉（**只删自己刚写的那份**）
        except OSError:
            pass
        return False, "沙箱没过（已撤下）：%s" % str(why2)[:140]
    return True, "已装：%s ｜ 工具：%s ｜ 重启小焦后可用，且会自动出独立 UI 模块" % (
        fname, "、".join(tools2 or []))


def _spec_text():
    return ("【插件硬规定·必须满足】\n- " + "\n- ".join(SPEC_MUST) +
            "\n\n【不支持（遇到直接拒）】\n- " + "\n- ".join(SPEC_NO))


class GithubPluginsPlugin:
    def get_tool_descriptions(self):
        return list(_TOOLS)

    def execute(self, tool_name, params):
        p = params or {}
        try:
            if tool_name == "plugin_spec":
                return _spec_text()
            if tool_name == "github_find_plugin":
                kw = str(p.get("keyword") or "").strip()
                if not kw:
                    return "要搜什么？给个关键词（例如 dsh plugin / bilibili mcp）"
                items, err = _gh_search(kw)
                if err:
                    return "搜索失败：%s" % err
                if not items:
                    return "没搜到。换个关键词试试（英文更容易命中）。"
                lines = ["GitHub 上搜「%s」的前 %d 个：" % (kw, len(items))]
                for i, it in enumerate(items, 1):
                    lines.append("%d. %s（★%s）%s\n   %s" % (
                        i, it["full_name"], it["stars"], it["desc"], it["url"]))
                lines.append("\n注意：只能用 `github_install_plugin` 装**单个 .py 直链**，"
                             "且必须过沙箱。多文件包/需要 API Key 的不要装。")
                return "\n".join(lines)
            if tool_name == "github_install_plugin":
                url = str(p.get("raw_url") or "").strip()
                if not url:
                    return "要装哪个文件？给 raw.githubusercontent.com 的 .py 直链。"
                ok, why = _install(url, str(p.get("name") or ""))
                return ("✅ " if ok else "❌ ") + why
            return "这个插件只有 plugin_spec / github_find_plugin / github_install_plugin。"
        except Exception as e:      # noqa: BLE001 — 插件异常一律转成中文
            LOG.debug("github 插件异常：%s", e)
            return "操作失败：%s: %s" % (type(e).__name__, str(e)[:120])


_PLUGIN = GithubPluginsPlugin()


def get_plugin():
    return _PLUGIN
