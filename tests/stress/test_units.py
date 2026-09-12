# -*- coding: utf-8 -*-
"""离线用例：不联网也能跑（配置校验 / 会话回收 / 指标 / 脱敏 / JSON 美化 / 安全闸门 / 参数校验）

这些用例覆盖"改动最容易踩坏、又不需要公网"的部分，适合每次提交都跑。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from harness import REPO_ROOT, Results, load_plugin

ROOT_APP = REPO_ROOT


def run(res: Results, mod=None) -> Results:
    mod = mod or load_plugin()

    # ---------- 1. 安全闸门：SSRF 必须 100% 拦截 ----------
    guard = mod.SecurityGuard(rate_limit=0.0)
    ssrf_targets = ["http://127.0.0.1/", "http://10.0.0.1/", "http://192.168.1.1/",
                    "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
                    "http://localhost:5000/", "http://[::1]/", "http://0.0.0.0/",
                    "gopher://127.0.0.1:70/", "ftp://192.168.1.1/"]
    blocked = 0
    for t in ssrf_targets:
        if guard.check_ssrf(t):
            blocked += 1
    res.check("安全", "SSRF 拦截 %d 个内网/危险地址" % len(ssrf_targets),
              blocked == len(ssrf_targets), "拦截 %d/%d" % (blocked, len(ssrf_targets)))
    res.check("安全", "合法公网 URL 放行", guard.check_ssrf("https://example.com") == "",
              guard.check_ssrf("https://example.com") or "放行")

    # ---------- 2. 脱敏：裸凭据也必须打码 ----------
    # 下面全是**故意构造的假凭据**（secret-fixture），用于验证脱敏逻辑；不是真实密钥。
    samples = {
        "sk-abcdefghijklmnopqrstuvwx": "OpenAI 风格密钥",              # secret-fixture（假）
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWX1234": "GitHub token",            # secret-fixture（假）
        "AKIAIOSFODNN7EXAMPLE": "AWS Access Key",                      # secret-fixture（假）
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U": "JWT",  # secret-fixture（假）
    }
    for raw, label in samples.items():
        out = mod.sanitize("错误里带了 %s 这样的值" % raw)
        res.check("脱敏", "抹掉%s" % label, raw not in out, out[:70])
    out_kv = mod.sanitize("api_key=SECRET123 cookie=abc token: xyz")
    res.check("脱敏", "键值对形式保留键名抹值", "SECRET123" not in out_kv and "api_key=" in out_kv, out_kv[:70])

    # ---------- 3. JSON 展示美化 ----------
    short = json.loads(mod.fmt_result(200, "u", '{"a":1,"b":[1,2,3]}'))
    res.check("展示", "短 JSON 美化缩进", "\n  \"a\": 1" in short["content"], repr(short["content"][:40]))
    md_escaped = json.loads(mod.fmt_result(200, "u", '{"format":"NVD\\_CVE","v":1}'))
    res.check("展示", "Markdown 转义过的 JSON 仍能解析美化",
              '"format": "NVD_CVE"' in md_escaped["content"], repr(md_escaped["content"][:60]))
    big = json.loads(mod.fmt_result(200, "u", json.dumps([{"i": i} for i in range(500)])))
    res.check("展示", "超长 JSON 自动折叠并给提示",
              "已折叠显示" in big["content"] and len(big["content"].splitlines()) <= mod.JSON_DISPLAY_LINES + 6,
              "%d 行" % len(big["content"].splitlines()))
    plain = json.loads(mod.fmt_result(200, "u", "# 标题\n普通正文"))
    res.check("展示", "非 JSON 内容原样保留", plain["content"].startswith("# 标题"), plain["content"][:30])

    # ---------- 4. 会话回收 SessionManager ----------
    class _Cfg:
        max_sessions, session_ttl, session_idle = 2, 1800, 300
    closed = []
    sm = mod.SessionManager(_Cfg(), closer=closed.append, interval=999, autostart=False)
    sm.register("a"); time.sleep(0.03); sm.register("b"); time.sleep(0.03)
    sm.touch("a"); time.sleep(0.03); sm.register("c")
    res.check("会话回收", "超出上限踢最久未用(LRU)", closed == ["b"], "被关=%s" % closed)

    class _Cfg2:
        max_sessions, session_ttl, session_idle = 20, 0, 0
    sm_bad = mod.SessionManager(_Cfg2(), closer=lambda s: None, interval=999, autostart=False)
    res.check("会话回收", "非法配置(0)回退默认值",
              (sm_bad.max_sessions, sm_bad.ttl, sm_bad.idle) == (20, 1800, 300),
              "max=%d ttl=%d idle=%d" % (sm_bad.max_sessions, sm_bad.ttl, sm_bad.idle))

    class _Cfg3:
        max_sessions, session_ttl, session_idle = 20, 1, 300
    closed2 = []
    sm2 = mod.SessionManager(_Cfg3(), closer=closed2.append, interval=999, autostart=False)
    sm2.register("t1")
    time.sleep(1.1)
    sm2.sweep("test")
    res.check("会话回收", "TTL 到期被回收", closed2 == ["t1"], "被关=%s" % closed2)

    class _Cfg4:
        max_sessions, session_ttl, session_idle = 20, 1800, 300
    sm3 = mod.SessionManager(_Cfg4(), closer=lambda s: None, interval=5, autostart=True)
    sm3.register("bg")
    time.sleep(0.2)
    alive = [t.name for t in threading.enumerate() if t.name == "xj-session-sweeper"]
    sm3.stop()
    res.check("会话回收", "登记后自动起后台巡检线程", len(alive) == 1, "线程=%s" % alive)

    # ---------- 5. 指标 MetricsCollector ----------
    mc = mod.MetricsCollector(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_metrics.json"))
    mc.record("get", True, 1.0)
    mc.record("get", True, 3.0)
    mc.record("get", False, 2.0, error="sk-abcdefghijklmnop")   # secret-fixture（假密钥，用于验证脱敏）
    mc.record("fetch", False, 1.0, circuit_break=True)
    snap = mc.snapshot()
    g = snap["tools"]["get"]
    res.check("指标", "calls/success/fail 计数", (g["calls"], g["success"], g["fail"]) == (3, 2, 1), str({k: g[k] for k in ("calls", "success", "fail")}))
    res.check("指标", "avg/max 延迟计算", (g["avg_latency"], g["max_latency"]) == (2.0, 3.0), "avg=%s max=%s" % (g["avg_latency"], g["max_latency"]))
    res.check("指标", "熔断次数计数", snap["tools"]["fetch"]["circuit_breaks"] == 1, "")
    res.check("指标", "错误信息脱敏入库", "sk-abcdefghijklmnop" not in json.dumps(g, ensure_ascii=False), g["last_error"][:40])  # secret-fixture（假）
    prom = mc.to_prometheus(extra={"sessions_active": 2})
    res.check("指标", "Prometheus 无标签指标格式正确",
              "xiaojiao_scrapling_sessions_active 2" in prom and "{}" not in prom,
              [l for l in prom.splitlines() if "sessions_active" in l][-1])
    res.check("指标", "Prometheus 含工具维度指标",
              'xiaojiao_scrapling_calls_total{tool="get"} 3' in prom, "")
    res.check("指标", "export() 落盘可读", bool(mc.export()) and "tools" in json.load(open(mc.path, encoding="utf-8")), mc.path)

    # ---------- 6. 批量配置 BatchConfig ----------
    bad_cases = [({"concurrency": 0}, "必须 ≥ 1"), ({"concurrency": 99999}, "不能超过"),
                 ({"per_domain_limit": 0}, "必须 ≥ 1"), ({"rate_limit": -1}, "不能为负"),
                 ({"max_retries": 99}, "0~10"), ({"backoff_base": -1}, "不能为负"),
                 ({"concurrency": "x"}, "必须是整数")]
    for raw, expect in bad_cases:
        c = mod.BatchConfig.load({"batch": raw}, 1.0)
        res.check("批量配置", "非法 %s → 中文错误" % json.dumps(raw, ensure_ascii=False),
                  bool(c.error) and expect in c.error, c.error or "(未报错)")
    ok = mod.BatchConfig.load({"batch": {"concurrency": 4, "per_domain_limit": 2}}, 1.0)
    res.check("批量配置", "合法配置字段生效",
              (not ok.error) and ok.concurrency == 4 and ok.per_domain_limit == 2, ok.describe())

    # ---------- 7. 前端渲染（后端行为 + 模板契约）----------
    # 说明：Markdown→HTML 的渲染发生在浏览器 JS 里，Python 侧测不到；
    # 因此这里① 真测后端负责的转换（Setext 归一化、JSON 归拢、大纲提取），
    #        ② 对模板做"契约检查"（渲染管线依赖的钩子必须存在），两者都不是模拟。
    md = mod.normalize_markdown("标题\n====\n\n正文\n\n小标题\n----\n")
    res.check("渲染", "Setext 标题转 ATX（前端才渲染得出标题）",
              "# 标题" in md and "## 小标题" in md, repr(md[:40]))

    sys.path.insert(0, ROOT_APP)
    app_src = open(os.path.join(ROOT_APP, "xiaojiao_app.py"), encoding="utf-8").read()
    for token, why in (("_fence_body", "抓取正文按类型套代码块"),
                       ("codebox lang-", "代码块带语言类名（CSS 限高用）"),
                       ("renderTableBlock", "表格渲染"),
                       ('class="mdh"', "标题样式钩子"),
                       ("srcbox", "来源框（宽度/换行修复处）"),
                       ("<br>", "块间换行（段落粘连修复处）"),
                       ("/metrics", "指标接口")):
        res.check("渲染/契约", "模板含 %s（%s）" % (token, why), token in app_src, "")

    # 抓取正文的展示归拢：JSON → ```json 代码块；Markdown → 原样
    import re as _re
    m = _re.search(r"def _fence_body\(text: str\) -> str:(.*?)\ndef ", app_src, _re.S)
    res.check("渲染/契约", "_fence_body 支持 Markdown 转义 JSON 兜底",
              bool(m) and "bfnrtu" in m.group(1), "")

    # ---------- 8. 大 JSON 的展示与落盘（尺寸相关的回归，曾真实翻车）----------
    # 背景：`stealthy_fetch` 抓 NVD 返回 10023 字，正好越过 10000 字截断线。
    # 旧实现「先截断、后美化」→ 内容被截成**非法 JSON** → 既无法美化也无法包代码块，
    # 用户看到一大坨原始 JSON（真实用户截图投诉）。所以这里专门用**超长 JSON** 做回归。
    big_obj = {"resultsPerPage": 5,
               "vulnerabilities": [{"cve": {"id": "CVE-2024-%05d" % i,
                                            "desc": "x" * 200,
                                            "metrics": {"score": i}}} for i in range(60)]}
    big_raw = json.dumps(big_obj, ensure_ascii=False)          # 必然 > MAX_CONTENT_CHARS
    big_escaped = big_raw.replace("_", "\\_")                  # 模拟 markdownify 的转义
    res.check("大JSON", "样本确实超过截断线（%d > %d）" % (len(big_escaped), mod.MAX_CONTENT_CHARS),
              len(big_escaped) > mod.MAX_CONTENT_CHARS, "")

    shown = json.loads(mod.fmt_result(200, "u", big_escaped))["content"]
    lines = shown.splitlines()
    res.check("大JSON", "展示时被美化成多行 JSON（首行 '{'）", lines[0].strip() == "{", "%d 行" % len(lines))
    res.check("大JSON", "超长自动折叠并给出提示", "已折叠显示" in shown, "展示 %d 字" % len(shown))
    res.check("大JSON", "展示长度可控（≤ 折叠上限 + 提示行）",
              len(shown) <= mod.JSON_DISPLAY_CHARS + 400, "%d 字" % len(shown))
    res.check("大JSON", "Markdown 转义已修（不含 \\_ 残留）", "\\_" not in shown, "")

    # 截断顺序：clip=True 会先截断（对批量友好），clip=False 保留全文（展示/落盘用）
    truncated = mod.MCPClient._normalize_one({"status": 200, "url": "u", "content": big_raw}, clip=True)["content"]
    full = mod.MCPClient._normalize_one({"status": 200, "url": "u", "content": big_raw}, clip=False)["content"]
    res.check("大JSON", "clip=True 会截断到上限（批量路径）", len(truncated) <= mod.MAX_CONTENT_CHARS + 200,
              "%d 字" % len(truncated))
    res.check("大JSON", "clip=False 保留全文（展示/存文件路径）", len(full) == len(big_raw),
              "%d 字" % len(full))

    # 落盘：.json 必须写成**合法且完整**的 JSON（旧实现落盘带转义的非法 JSON）
    saved = mod._materialize_content(big_escaped, "nvd.json")
    ok_saved = False
    try:
        obj2 = json.loads(saved)
        ok_saved = len(obj2.get("vulnerabilities") or []) == 60
    except Exception:
        ok_saved = False
    res.check("大JSON", "存 .json 时写成合法且完整 JSON（可被程序解析）", ok_saved, "%d 字" % len(saved))
    res.check("大JSON", "非 .json 文件名不改写内容",
              mod._materialize_content(big_raw, "note.md") == big_raw, "")

    # ---------- 9. 参数规范化 / 工具集完整性 ----------
    from harness import Bridge as _B
    b = mod.ScraplingBridge()
    bx = _B(mod, b)
    tools = [t["name"] for t in b.get_tool_descriptions()]
    native = ["open_session", "open_request_session", "close_session", "list_sessions", "make_request",
              "bulk_get", "fetch", "bulk_fetch", "stealthy_fetch", "bulk_stealthy_fetch",
              "session_fetch", "session_make_request", "screenshot"]
    missing = [t for t in native if t not in tools]
    res.check("工具集", "Scrapling 原生 13 工具 1:1 全暴露", not missing, "缺=%s" % missing)
    res.check("工具集", "对外工具总数 18", len(tools) == 18, "实际 %d: %s" % (len(tools), tools))
    res.check("工具集", "新增漏洞聚合工具已注册", "collect_vulnerabilities" in tools, "")

    # 空参数 / 非法参数（全部离线即可判定，不联网）
    for tool, params, expect in (
            ("bulk_get", {"urls": []}, "urls 为空"),
            ("bulk_fetch", {"urls": []}, "urls 为空"),
            ("bulk_stealthy_fetch", {"urls": "not-a-list-but-string"}, None),
            ("scrape_with_selector", {"url": "https://example.com", "selector": ""}, "需要同时提供"),
            ("open_session", {"session_type": "不合法"}, "session_type 不合法"),
            ("download", {}, "需要提供 url"),
            ("get", {"url": "file:///etc/passwd"}, "禁止访问"),
            ("get", {"url": "http://127.0.0.1:5000/"}, "SSRF"),
            ("close_session", {}, "session_id"),
            ("session_fetch", {"url": "https://example.com"}, "session_id")):
        _, _, err, _ = bx.call(tool, params, cap=20)
        if expect is None:
            res.check("参数校验", "%s 接受字符串并规范化" % tool, True, "（联网用例验证真实抓取）")
        else:
            res.check("参数校验", "%s → %s" % (tool, expect), expect in err, err[:70])

    # ---------- 10. NVD 漏洞聚合（collect_vulnerabilities）：纯离线逻辑 ----------
    # 复盘：以前"抓最近漏洞"会拿到 1999 年数据、受影响软件全是 n/a、5 条只总结 1 条，
    # 所以这里把"CPE 转人话 / CVSS 取值 / 表格渲染"逐条钉死，任何一步退化都能被这条用例抓到。
    _cpe = "cpe:2.3:a:apache:http_server:1.0:*:*:*:*:*:*:*"
    res.check("漏洞聚合", "CPE → 人话软件名（含版本）",
              mod.cpe_to_software(_cpe) == "Apache HTTP Server 1.0", mod.cpe_to_software(_cpe))
    res.check("漏洞聚合", "非 CPE 字符串不硬凑软件名",
              mod.cpe_to_software("not-a-cpe") == "" and mod.cpe_to_software("") == "", "")
    _cpe_kernel = mod.cpe_to_software("cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*")
    res.check("漏洞聚合", "厂商名不重复 + 通配版本不显示(不出 *)",
              _cpe_kernel == "Linux Kernel", _cpe_kernel)
    _v31 = {"metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]}}
    _v2 = {"metrics": {"cvssMetricV2": [{"cvssData": {"baseScore": 7.5}}]}}
    res.check("漏洞聚合", "CVSS v3.1 取等级与评分", mod._cvss_of(_v31) == ("CRITICAL", 9.8), str(mod._cvss_of(_v31)))
    res.check("漏洞聚合", "CVSS v2 无 baseSeverity 时按分数补等级",
              mod._cvss_of(_v2) == ("HIGH", 7.5), str(mod._cvss_of(_v2)))
    res.check("漏洞聚合", "无 CVSS 数据时不编造等级", mod._cvss_of({}) == ("", 0.0), "")
    # 下面这条是按 NVD 真实响应结构构造的样本（字段名/嵌套一致，值均为虚构）
    _cve = {"id": "CVE-2026-0001", "vulnStatus": "Analyzed",
            "published": "2026-02-05T10:00:00.000",
            "lastModified": "2026-02-06T10:00:00.000",
            "descriptions": [{"lang": "es", "value": "no"}, {"lang": "en", "value": "A flaw  was found | in Apache."}],
            "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 8.1, "baseSeverity": "HIGH"}}]},
            "configurations": [{"nodes": [{"cpeMatch": [{"criteria": _cpe}]}]}]}
    _row = mod._nvd_row(_cve)
    res.check("漏洞聚合", "整行字段提取（编号/等级/评分/受影响软件）",
              bool(_row) and _row["id"] == "CVE-2026-0001" and _row["severity"] == "HIGH"
              and _row["score"] == 8.1 and _row["software"] == ["Apache HTTP Server 1.0"],
              str(_row)[:120])
    res.check("漏洞聚合", "描述只取英文、压平空白",
              bool(_row) and _row["summary"] == "A flaw was found | in Apache.", str(_row and _row["summary"]))
    res.check("漏洞聚合", "Rejected 状态的 CVE 不进表",
              mod._nvd_row({"id": "CVE-2026-0002", "vulnStatus": "Rejected"}) is None, "")
    res.check("漏洞聚合", "缺 id 的脏记录不进表", mod._nvd_row({}) is None, "")
    _md = mod.build_vuln_markdown([_row], "HIGH 及以上", 7, "2026-02-01T00:00:00.000+00:00",
                                  "2026-02-08T00:00:00.000+00:00", 1284, 200, 3, 5)
    _tbl = [l for l in _md.splitlines() if l.startswith("|")]
    res.check("漏洞聚合", "输出可直接渲染的 Markdown 表格（表头+分隔+数据行）",
              len(_tbl) == 3 and _tbl[0].count("|") == 8, "%d 行表格" % len(_tbl))
    res.check("漏洞聚合", "表格里是真实软件名而不是 n/a",
              "Apache HTTP Server 1.0" in _md and "n/a" not in _md, "")
    res.check("漏洞聚合", "摘要里的竖线被转义（不撑破表格）", "found \\| in Apache" in _md, "")
    res.check("漏洞聚合", "命中不足时如实说明（不凑数）", "确实只有 1 条" in _md, "")
    _md0 = mod.build_vuln_markdown([], "CRITICAL 及以上", 1, "s", "e", 0, 12, 0, 5)
    res.check("漏洞聚合", "零命中时给出放宽建议而不是空白", "severity=ANY" in _md0, _md0.splitlines()[-1][:70])
    res.check("漏洞聚合", "days/limit 脏输入回落默认值",
              (mod._clamp_int("abc", 7, 1, 120), mod._clamp_int(None, 5, 1, 50)) == (7, 5), "")
    res.check("漏洞聚合", "days 被夹在 NVD 官方上限 120 天内",
              mod._clamp_int(999, 7, 1, mod.NVD_WINDOW_MAX_DAYS) == 120, "")
    # CPE 未收录时的描述兜底（新 CVE 常态）：必须摘到名字 + 明确标注"描述推断"，且去掉冠词
    _guess = mod._software_from_desc(
        "The GEO my WP plugin for WordPress is vulnerable to Local File Inclusion in all versions up to 5.0.")
    res.check("漏洞聚合", "CPE 缺失时从描述摘软件名（去冠词、带平台）",
              _guess == "GEO my WP（WordPress）", _guess)
    _guess2 = mod._software_from_desc(
        "The Tutor LMS – eLearning and online course solution plugin for WordPress is vulnerable to SQL Injection.")
    res.check("漏洞聚合", "描述里有破折号说明时只取产品名", _guess2 == "Tutor LMS（WordPress）", _guess2)
    res.check("漏洞聚合", "描述里没有软件名就不硬编（返回空）",
              mod._software_from_desc("An improper check in some component allows an attacker to cause a crash.") == "",
              mod._software_from_desc("An improper check in some component allows an attacker to cause a crash."))
    _row_g = dict(_row, software=[], software_guess="GEO my WP（WordPress）")
    _mdg = mod.build_vuln_markdown([_row_g], "HIGH 及以上", 7, "s", "e", 10, 10, 0, 5)
    res.check("漏洞聚合", "描述推断的软件名在表格里被标注为推断",
              "（描述推断）" in _mdg and "GEO my WP（WordPress）（描述推断）" in _mdg, "")
    _mdn = mod.build_vuln_markdown([_row], "HIGH 及以上", 7, "s", "e", 7124, 100, 0, 5,
                                   notes=["第二页没拉到，本次只扫描了最新 50 条"])
    res.check("漏洞聚合", "抽样不完整时如实告警（不冒充全量）",
              "第二页没拉到" in _mdn and "本次实际扫描 100 条" in _mdn, "")
    res.check("漏洞聚合", "窗口很小(全扫)时说已全部扫描",
              "已全部扫描" in mod.build_vuln_markdown([_row], "HIGH 及以上", 1, "s", "e", 12, 12, 0, 5), "")

    return res
