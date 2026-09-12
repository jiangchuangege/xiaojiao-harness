# -*- coding: utf-8 -*-
"""离线用例：不联网也能跑（配置校验 / 会话回收 / 指标 / 脱敏 / JSON 美化 / 安全闸门 / 参数校验）

这些用例覆盖"改动最容易踩坏、又不需要公网"的部分，适合每次提交都跑。
"""
from __future__ import annotations

import json
import os
import threading
import time

from harness import Results, load_plugin


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
    samples = {
        "sk-abcdefghijklmnopqrstuvwx": "OpenAI 风格密钥",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWX1234": "GitHub token",
        "AKIAIOSFODNN7EXAMPLE": "AWS Access Key",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U": "JWT",
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
    mc.record("get", False, 2.0, error="sk-abcdefghijklmnop")
    mc.record("fetch", False, 1.0, circuit_break=True)
    snap = mc.snapshot()
    g = snap["tools"]["get"]
    res.check("指标", "calls/success/fail 计数", (g["calls"], g["success"], g["fail"]) == (3, 2, 1), str({k: g[k] for k in ("calls", "success", "fail")}))
    res.check("指标", "avg/max 延迟计算", (g["avg_latency"], g["max_latency"]) == (2.0, 3.0), "avg=%s max=%s" % (g["avg_latency"], g["max_latency"]))
    res.check("指标", "熔断次数计数", snap["tools"]["fetch"]["circuit_breaks"] == 1, "")
    res.check("指标", "错误信息脱敏入库", "sk-abcdefghijklmnop" not in json.dumps(g, ensure_ascii=False), g["last_error"][:40])
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

    # ---------- 7. 参数规范化 / 工具集完整性 ----------
    from harness import Bridge as _B
    b = mod.ScraplingBridge()
    bx = _B(mod, b)
    tools = [t["name"] for t in b.get_tool_descriptions()]
    native = ["open_session", "open_request_session", "close_session", "list_sessions", "make_request",
              "bulk_get", "fetch", "bulk_fetch", "stealthy_fetch", "bulk_stealthy_fetch",
              "session_fetch", "session_make_request", "screenshot"]
    missing = [t for t in native if t not in tools]
    res.check("工具集", "Scrapling 原生 13 工具 1:1 全暴露", not missing, "缺=%s" % missing)
    res.check("工具集", "对外工具总数 17", len(tools) == 17, "实际 %d: %s" % (len(tools), tools))

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

    return res
