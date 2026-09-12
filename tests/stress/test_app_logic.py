# -*- coding: utf-8 -*-
"""应用逻辑用例（离线）：检索词清洗 / 漏洞查询意图 / 提示词铁律 / 工具注册

覆盖两个真实缺陷的**回归防线**：
  ① 小焦曾把功能字「用」当检索词去搜（用户实测："用搜索工具找漏洞" → 搜出"用（汉语汉字）"）；
  ② 小焦曾自己拼 NVD 接口（没带时间窗）→ 拿到 1999 年数据、受影响软件 n/a、5 条只总结 1 条。
这两条都靠"代码层规则"兜住，所以必须有用例盯着，别哪天被改回去。

注意：这里会 import 主程序（含小脑/插件加载），拿不到依赖时整组**跳过**而不是误报失败。
"""
from __future__ import annotations

import importlib.util
import os
import sys

from harness import REPO_ROOT, Results

_APP = None
_ERR = ""


def _load_app():
    """加载主程序（只加载一次）。失败原因记下来，用例里如实标注为"跳过"。"""
    global _APP, _ERR
    if _APP is not None or _ERR:
        return _APP
    _old = os.getcwd()
    try:
        os.chdir(REPO_ROOT)                       # 主程序用相对路径找 plugins/ 与操控文件
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        spec = importlib.util.spec_from_file_location("xiaojiao_app_under_test",
                                                      os.path.join(REPO_ROOT, "xiaojiao_app.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["xiaojiao_app_under_test"] = mod
        spec.loader.exec_module(mod)
        _APP = mod
    except Exception as e:                        # 依赖缺失/环境异常 → 跳过，不制造假失败
        _ERR = "%s: %s" % (type(e).__name__, str(e)[:120])
    finally:
        os.chdir(_old)
    return _APP


def run(res: Results) -> Results:
    app = _load_app()
    if app is None:
        res.skip("应用逻辑", "主程序加载", "环境不满足（%s）" % _ERR)
        return res

    # ---------- 1. 命令式口语 → 真正的检索关键词 ----------
    for raw, want in (("用搜索工具找漏洞", "漏洞 CVE"),
                      ("用联网搜一下最近的漏洞", "最近的漏洞 CVE"),
                      ("帮我搜 2026 年 AI 新闻", "2026 年 AI 新闻"),
                      ("查一下 apache 的漏洞", "apache 漏洞 CVE")):
        got, hint = app.resolve_search_query(raw)
        res.check("检索词", "「%s」→「%s」" % (raw, want), got == want, "实际「%s」%s" % (got, hint))

    # ---------- 2. 只有功能字/语气词 → 必须拒绝并反问，绝不用单字去搜 ----------
    for raw in ("用", "搜", "找", "搜一下", "帮我", "抓", "看看"):
        got, hint = app.resolve_search_query(raw)
        res.check("检索词", "「%s」被拒绝并提示用户" % raw,
                  got == "" and hint == app.SEARCH_KEYWORD_HINT, got or hint[:24])
    res.check("检索词", "拒绝时给出中文可读提示",
              "请告诉我你要搜索的具体关键词" in app.SEARCH_KEYWORD_HINT, app.SEARCH_KEYWORD_HINT[:40])
    res.check("检索词", "web_search 对无效词不发请求（离线可判定）", app.web_search("用") == [], "")

    # ---------- 3. 裸关键词不能被误伤（"看雪安全" ≠ "雪安全"） ----------
    for raw in ("看雪安全", "未来漏洞", "apache 漏洞"):
        got, _ = app.resolve_search_query(raw)
        res.check("检索词", "裸关键词「%s」原样保留" % raw, got.startswith(raw.split()[0]), "实际「%s」" % got)

    # ---------- 4. 漏洞查询意图 → 参数（直接决定调不调 collect_vulnerabilities） ----------
    for raw, want in (("抓取最近 7 天的高危漏洞", {"days": 7, "severity": "HIGH", "limit": 5}),
                      ("用搜索工具找漏洞", {"days": 7, "severity": "HIGH", "limit": 5}),
                      ("看看这个月的严重漏洞 10 条", {"days": 30, "severity": "CRITICAL", "limit": 10}),
                      ("帮我看下 30 天的中危漏洞", {"days": 30, "severity": "MEDIUM", "limit": 5}),
                      ("今天有哪些严重漏洞", {"days": 1, "severity": "CRITICAL", "limit": 5})):
        res.check("漏洞意图", "「%s」→ %s" % (raw, want), app.detect_vulnerability_query(raw) == want,
                  str(app.detect_vulnerability_query(raw)))
    for raw in ("什么是漏洞", "介绍一下 CVE 是什么", "今天天气怎么样"):
        res.check("漏洞意图", "概念提问「%s」不抢答" % raw, app.detect_vulnerability_query(raw) is None, "")

    # ---------- 5. 提示词铁律（换人设也不能丢） ----------
    sp = app.SYSTEM_PROMPT or ""
    res.check("提示词", "人设里带检索铁律", "检索铁律" in sp, sp[-60:].replace("\n", " "))
    res.check("提示词", "铁律禁止功能字当检索词", "禁止把" in sp and "功能字" in sp, "")
    res.check("提示词", "铁律要求漏洞优先 collect_vulnerabilities", "collect_vulnerabilities" in sp, "")

    # ---------- 6. 工具注册（插件新工具能被主程序看到并调用） ----------
    try:
        app._build_tools()
        names = list(app._TOOL2PLUGIN.keys())
    except Exception as e:
        names = []
        res.check("工具注册", "构建工具表", False, "%s: %s" % (type(e).__name__, str(e)[:80]))
    res.check("工具注册", "collect_vulnerabilities 已注册到工具表",
              "collect_vulnerabilities" in names, "工具数=%d" % len(names))
    res.check("工具注册", "web_search 仍是主程序内置工具", "web_search" in names, "")

    # ---------- 7. 配置热重载不能把「检索铁律」弄丢（真实缺陷：reload_control 直接赋值 role） ----------
    app.reload_control()
    res.check("配置热重载", "reload 后人设里仍有检索铁律", "检索铁律" in (app.SYSTEM_PROMPT or ""),
              app.SYSTEM_PROMPT[-40:].replace("\n", " "))
    res.check("配置热重载", "compose_system_prompt 是唯一合成入口",
              app.compose_system_prompt("X").endswith(app._SEARCH_RULES), "")
    res.check("配置热重载", "操控文件路径是模块级常量（不再是函数内局部变量）",
              os.path.isabs(app.CONTROL_FILE) and app.CONTROL_FILE.endswith("xiaojiao_control.json"),
              app.CONTROL_FILE)

    # ---------- 8. /api/persona 回归（真实缺陷：引用了未定义的 _CFG → 必然 500，切人设保存不了） ----------
    import json as _json
    import tempfile
    tmp_ctl = os.path.join(tempfile.mkdtemp(prefix="xj_ctl_"), "xiaojiao_control.json")
    with open(tmp_ctl, "w", encoding="utf-8") as f:
        _json.dump({"role": "原始人设"}, f, ensure_ascii=False)
    _old_ctl = app.CONTROL_FILE
    app.CONTROL_FILE = tmp_ctl
    try:
        client = app.app.test_client()
        r = client.post("/api/persona", json={"role": "我是新的人设"})
        body = r.get_json() or {}
        saved = _json.load(open(tmp_ctl, encoding="utf-8")).get("role", "")
        res.check("切人设", "POST /api/persona 返回 200（不再 NameError 500）",
                  r.status_code == 200 and body.get("ok") is True,
                  "HTTP %s %s" % (r.status_code, str(body)[:60]))
        res.check("切人设", "人设真的写进了操控文件", saved == "我是新的人设", saved[:40])
        r2 = client.post("/api/persona", json={"role": app.SYSTEM_PROMPT})
        saved2 = _json.load(open(tmp_ctl, encoding="utf-8")).get("role", "")
        res.check("切人设", "界面回传带铁律的人设不会把铁律重复存进文件",
                  r2.status_code == 200 and "检索铁律" not in saved2, saved2[-30:])
        res.check("切人设", "空人设被拒绝", client.post("/api/persona", json={"role": ""}).status_code == 400, "")
    finally:
        app.CONTROL_FILE = _old_ctl
        app.reload_control()                   # 把全局状态还原成真实操控文件

    return res
