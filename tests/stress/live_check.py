# -*- coding: utf-8 -*-
"""实机验收：对**正在运行**的小焦发真实请求（不是跑单元测试）

用途：发布后/改完代码后的"最后一公里"自检 —— 真启动、真 HTTP、真抓取、真拦截。

前置：小焦正在运行（`python start_xiaojiao.py`），默认地址 http://127.0.0.1:5000

用法：
    python tests/stress/live_check.py
    python tests/stress/live_check.py --base http://127.0.0.1:5000
退出码：0 = 全部通过；1 = 有失败项
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser(description="小焦实机验收")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    passed, failed = [], []

    def check(name: str, cond: bool, detail: str = "") -> None:
        (passed if cond else failed).append(name)
        print("  %s %-46s %s" % ("✅" if cond else "❌", name, str(detail)[:100]), flush=True)

    def get(path: str, timeout: int = 25):
        try:
            r = requests.get(base + path, timeout=timeout)
            return r.status_code, r
        except Exception as e:
            return 0, str(e)

    def post(path: str, payload: dict, timeout: int = 240):
        try:
            r = requests.post(base + path, json=payload, timeout=timeout)
            return r.status_code, r
        except Exception as e:
            return 0, str(e)

    print("=" * 68)
    print("  小焦实机验收（服务：%s）" % base)
    print("=" * 68)

    print("\n[1] 基础端点")
    for path in ("/", "/api/env", "/api/models", "/api/sessions", "/api/history",
                 "/metrics", "/api/scrapling/metrics", "/api/tools_toggle", "/favicon.ico"):
        code, _ = get(path)
        check("GET %s" % path, code == 200, "HTTP %s" % code)

    print("\n[2] 玩具体检 /api/env")
    code, r = get("/api/env")
    if code == 200:
        d = r.json()
        items = d.get("items", [])
        ok_n = sum(1 for i in items if i.get("ok"))
        check("体检项全部通过（%d/%d）" % (ok_n, len(items)),
              bool(d.get("ok")) and ok_n == len(items), "缺失=%s" % (d.get("missing") or "无"))
    else:
        check("体检接口可用", False, "HTTP %s" % code)

    print("\n[3] 指标与观测")
    code, r = get("/metrics")
    body = r.text if code == 200 else ""
    check("/metrics 为 Prometheus 文本", "# TYPE xiaojiao_scrapling_calls_total counter" in body,
          "%d 行" % len(body.splitlines()))
    check("/metrics 含会话数与运行时长",
          "xiaojiao_scrapling_sessions_active" in body and "uptime_seconds" in body, "")
    code, r = get("/api/scrapling/metrics")
    if code == 200:
        j = r.json()
        check("JSON 指标含 tools/sessions/breaker",
              all(k in j for k in ("tools", "sessions", "breaker")),
              "total_calls=%s active=%s" % (j.get("total_calls"), (j.get("sessions") or {}).get("active")))

    print("\n[4] 真实抓取（走对话直通）")
    t0 = time.time()
    code, r = post("/api/chat", {"message": "抓一下 https://example.com"})
    el = time.time() - t0
    if code == 200:
        a = r.json().get("answer", "")
        check("抓取 example.com 成功",
              ("Example Domain" in a) or ("HTTP 200" in a), "%.1fs · %d 字" % (el, len(a)))
    else:
        check("抓取 example.com 成功", False, "HTTP %s" % code)

    print("\n[5] JSON 展示（美化 + 代码块 + 折叠）")
    code, r = post("/api/chat", {"message": "用 stealthy_fetch 抓这个接口：https://httpbin.org/json"})
    if code == 200:
        a = r.json().get("answer", "")
        check("JSON 被包成代码块（前端可渲染）", "```json" in a, "")
        check("JSON 已缩进美化", ('\n  "' in a) or ('\n    "' in a), "")

    print("\n[6] SSRF 防护（实机）")
    code, r = post("/api/chat", {"message": "抓一下 http://127.0.0.1:5000/"})
    if code == 200:
        a = r.json().get("answer", "")
        check("内网地址被拦截", any(k in a for k in ("SSRF", "禁止", "内网")),
              a[:80].replace("\n", " "))

    print("\n[7] 指标累加 + 会话回收器")
    code, r = get("/api/scrapling/metrics")
    if code == 200:
        j = r.json()
        check("刚才的调用已记入指标", j.get("total_calls", 0) >= 2,
              "total_calls=%s 工具=%s" % (j.get("total_calls"), list((j.get("tools") or {}))[:4]))
        s = j.get("sessions") or {}
        check("会话回收器生效且有限额", bool(s.get("max_sessions")) and bool(s.get("ttl_seconds")),
              "max=%s ttl=%ss idle=%ss" % (s.get("max_sessions"), s.get("ttl_seconds"), s.get("idle_seconds")))

    print("\n[8] 日志与脱敏")
    logp = os.path.join(REPO_ROOT, "logs", "xiaojiao.log")
    check("统一日志文件存在且在写", os.path.exists(logp) and os.path.getsize(logp) > 0,
          "%.1f KB" % (os.path.getsize(logp) / 1024 if os.path.exists(logp) else 0))
    if os.path.exists(logp):
        txt = open(logp, encoding="utf-8", errors="ignore").read()
        check("日志中无明文密钥", "sk-" not in txt, "")

    print("\n" + "=" * 68)
    print("  实机验收：通过 %d / %d" % (len(passed), len(passed) + len(failed)))
    for f in failed:
        print("    ❌ %s" % f)
    print("=" * 68)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
