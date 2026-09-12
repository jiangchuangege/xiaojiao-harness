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
import time

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
    except Exception as e:                        # 依赖缺失/环境异常 → 跳过（不误报失败）
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

    # ---------- 5b. 铁律只能有一份（真实缺陷：切工具开关会把人设越存越脏） ----------
    dirty = "你是小焦。" + app._SEARCH_RULES * 5           # 模拟老版本攒出来的脏人设
    res.check("提示词", "脏人设能被剥成纯人设",
              app.strip_search_rules(dirty) == "你是小焦。", app.strip_search_rules(dirty)[:40])
    res.check("提示词", "合成后的人设铁律只有一份",
              app.compose_system_prompt(dirty).count("[检索铁律]") == 1,
              "份数=%d" % app.compose_system_prompt(dirty).count("[检索铁律]"))
    res.check("提示词", "剥铁律是幂等的（反复调用不再累加）",
              app.strip_search_rules(app.strip_search_rules(dirty)) == "你是小焦。", "")

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

    # ---------- 9. 检索质量：多变体 / 主题词 / 相关度 / <think> ----------
    # 用户实测反馈："最近 AI 新闻"搜出歌曲《最近》、"帮我搜索一下 你好"搜出"好（汉语文字）"。
    # 下面几条就是这件事的回归防线。
    res.check("检索质量", "「帮我搜索一下 你好」不会把「你好」拆成「好」",
              app.extract_search_keywords("帮我搜索一下 你好") == "你好",
              app.extract_search_keywords("帮我搜索一下 你好"))
    res.check("检索质量", "寒暄词不做检索（整句闸门返回空）",
              app.resolve_search_query("你好")[0] == "" and app.resolve_search_query("用")[0] == "", "")
    res.check("检索质量", "主题词提取：去掉时间词/助词/疑问尾巴",
              (app._query_tokens("最近AI新闻") == ["AI", "新闻"]
               and app._query_tokens("最近的漏洞 CVE") == ["漏洞", "CVE"]
               and app._query_tokens("最近的AI新闻有哪些") == ["AI", "新闻"]),
              str(app._query_tokens("最近的AI新闻有哪些")))
    res.check("检索质量", "多变体：原词之后紧跟「只用主题词」的写法",
              app._query_variants("最近 AI 新闻")[:2] == ["最近 AI 新闻", "AI 新闻"],
              str(app._query_variants("最近 AI 新闻")))
    res.check("检索质量", "多变体：2026年开头的查询也会把年份剥掉",
              "AI新闻" in app._query_variants("2026年AI新闻"), str(app._query_variants("2026年AI新闻")))
    _junk = app._search_relevance("最近的漏洞 CVE", "最近（李圣杰2006年演唱的歌曲）", "收录于专辑《关于你的歌》")[1]
    _good = app._search_relevance("最近的漏洞 CVE", "腾讯漏洞情报", "漏洞 CVE 最新情报与修复建议")[1]
    res.check("检索质量", "跑题结果覆盖率低、相关结果覆盖率高（据此换写法）",
              _junk < 0.6 <= _good, "跑题=%.0f%% 相关=%.0f%%" % (_junk * 100, _good * 100))
    res.check("检索质量", "模型只给碎片时改用整句关键词",
              app._better_search_query("最近", "最近 AI 新闻") == "最近 AI 新闻", "")
    res.check("检索质量", "碎片不属于用户原话时不乱改",
              app._better_search_query("python 教程", "今天天气怎么样") == "python 教程", "")
    res.check("思维标签", "<think> 整块与残留标签都被剥掉",
              app._strip_think("<think>想一下</think>你好") == "你好"
              and app._strip_think("</think>你好") == "你好"
              and app._strip_think("<thinking>\n\n</thinking>\n\n正常回答") == "正常回答", "")

    # ---------- 10. 大脑失败必须给出**真实原因** ----------    # 真实缺陷：非 200 直接 return None，401/403 全被静默吞掉，用户只看到
    # 「模型调用出错（可能是连接超时/限流）」——查都没法查。用户实测就撞上了这个。
    app._LAST_LLM_ERROR = ""
    res.check("大脑错误", "没失败过时不乱加提示", app.llm_error_suffix() == "", app.llm_error_suffix())
    app._note_llm_error("chat", 401, '{"error":{"message":"Invalid token (request id: 20260912141428abc)","type":"AgnesAI_error"}}')
    _suf = app.llm_error_suffix()
    res.check("大脑错误", "401 带出状态码 + 中文解释 + 服务端原话",
              "401" in _suf and "API Key 无效" in _suf and "Invalid token" in _suf, _suf[:70])
    res.check("大脑错误", "原始 JSON 被收拾成人话（不把 {\"error\":…} 糊给用户）",
              "{" not in _suf and "request id" not in _suf, _suf[:70])
    res.check("大脑错误", "顺带告诉用户该怎么办", "怎么" in _suf, "")
    app._note_llm_error("chat", None, "ConnectTimeout: 连接超时")
    res.check("大脑错误", "网络异常也如实带出异常类型", "ConnectTimeout" in app.llm_error_suffix(), "")
    # 假密钥**运行时拼**出来：别在仓库里留一个形似真密钥的字符串（会被自己的密钥扫描器拦下）
    _fake_key = "sk-" + "a" * 24
    app._note_llm_error("chat", 401, "Authorization: Bearer " + _fake_key)
    res.check("大脑错误", "错误信息里的密钥一律打码",
              _fake_key not in app.llm_error_suffix() and "***" in app.llm_error_suffix(), "")
    res.check("大脑错误", "给用户的提示里带「真实原因」这段",
              "真实原因" in app.llm_error_suffix(), "")
    # 偶发 vs 持续失败要说清楚（实测 Agnes 网关忽好忽坏：同一个 Key 会 [401,200,401,…]）
    app._LAST_LLM_ERROR = "HTTP 401 · API Key 无效或已过期"
    app._LLM_STAT["recent"] = [0, 0, 0, 0, 0, 0]
    res.check("大脑错误", "连续被拒时说清「更像服务商侧问题」，别让用户以为自己填错",
              "全部被拒" in app.llm_error_suffix() and "控制台" in app.llm_error_suffix(),
              app.llm_error_suffix()[:80])
    app._LLM_STAT["recent"] = [1, 0, 1, 0, 0, 1]
    res.check("大脑错误", "忽好忽坏时说「网关偶发拒签、已重试」",
              "偶发拒签" in app.llm_error_suffix(), "")
    app._LAST_LLM_ERROR = ""
    app._LLM_STAT["recent"] = []

    # ---------- 10b. 云端熔断：连续被拒先歇 60 秒改用本地，别硬打（越打越全是 401） ----------
    app._CLOUD_BREAK.update({"fails": 0, "until": 0.0})
    for _ in range(3):
        app._cloud_break_note(False, False)           # 模拟云端连续失败 3 次
    res.check("云端熔断", "连续 3 次失败后进入冷却（改用本地大脑）",
              app._CLOUD_BREAK["until"] > time.time(), str(app._CLOUD_BREAK))
    app._cloud_break_note(True, True)                 # 本地成功 → 计数归零
    res.check("云端熔断", "本地成功后熔断计数归零（下次仍会试云端）",
              app._CLOUD_BREAK["fails"] == 0, str(app._CLOUD_BREAK))
    app._CLOUD_BREAK.update({"fails": 0, "until": 0.0})

    # ---------- 11. 选"本地模型"必须真能用（真实缺陷：条目里 engine/model 配错，选了照样不通） ----------
    res.check("本地大脑", "本机地址被识别为本地（不需要 Key）",
              app._is_local_base("http://127.0.0.1:9292/v1") and app._is_local_base("http://localhost:8080/v1")
              and not app._is_local_base("https://apihub.agnes-ai.com/v1"), "")
    _m, _ids = app._local_served_model("http://127.0.0.1:1/v1", "agnes-2.5-flash")
    res.check("本地大脑", "本地服务问不到时按原样返回，不误改配置", _m == "agnes-2.5-flash" and _ids == [], "")

    # ---------- 12. 问"含漏洞的 IP/资产"必须正面回答，不能只会复读同一张 NVD 表 ----------
    # 真实缺陷：只判"漏洞意图"、不判"要的是资产清单"，于是不管怎么问都是同一张表，
    # 用户看到的就是"他一直发这个模板，一点没变"。
    res.check("资产问答", "「含这些漏洞的 IP 地址并列表」被识别为资产测绘诉求",
              app._asks_asset_list("帮我抓取现在网络上的所有包含这几个漏洞的IP地址并列表对应上"), "")
    res.check("资产问答", "「这些漏洞影响哪些机器」也算资产诉求",
              app._asks_asset_list("这些漏洞影响哪些机器"), "")
    res.check("资产问答", "普通漏洞问句不误判",
              not app._asks_asset_list("抓取最近 7 天的高危漏洞"), "")
    res.check("资产问答", "zip/clip 这类词不会被当成 ip",
              not app._asks_asset_list("帮我把这个 zip 包解压一下"), "")
    res.check("资产问答", "直接甩 IP 地址也算资产诉求",
              app._asks_asset_list("帮我查 1.1.1.1 和 8.8.8.8 命中了哪些漏洞"), "")

    # ---------- 13. 资产测绘插件（IP ↔ CVE 对应表）契约 ----------
    # 真实缺陷：问"含这些漏洞的 IP"永远只回同一张 NVD 表 —— 因为根本没有资产数据源。
    # 现在有了 plugins/asset_intel.py：方向①免费无 Key；方向②没配 Key 要说清怎么配。
    import importlib.util as _ilu
    _p = os.path.join(REPO_ROOT, "plugins", "asset_intel.py")
    _spec = _ilu.spec_from_file_location("asset_intel_under_test", _p)
    _ai = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_ai)
    _inst = _ai.AssetIntelPlugin()
    _tools = [t["name"] for t in _inst.get_tool_descriptions()]
    res.check("资产插件", "三个工具都在（查IP/反查/状态）",
              {"asset_intel_lookup", "asset_intel_search", "asset_intel_status"} <= set(_tools), str(_tools))
    _st = _inst.status()
    res.check("资产插件", "状态里说明免费那一半可用（无需 Key）",
              "InternetDB" in _st and "无需 Key" in _st, _st[:50].replace("\n", " "))
    _nokey = _inst.search("vuln:CVE-2024-1234")
    res.check("资产插件", "没配 Key 时给出可操作说明（去哪拿、填哪里、怎么验证）",
              all(k in _nokey for k in ("account.shodan.io", "SHODAN_API_KEY", "状态")), _nokey[:50])
    res.check("资产插件", "内网/本机地址被拒（不假装查了）",
              "没识别到公网 IP" in _inst.lookup("192.168.1.1, 127.0.0.1"), "")
    res.check("资产插件", "未知工具给中文提示而不是异常",
              "未知工具" in _inst.execute("nope", {}), "")
    res.check("资产插件", "插件绝不把异常抛给调用方",
              isinstance(_inst.execute("asset_intel_lookup", {"ips": None}), str), "")

    return res
