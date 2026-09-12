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
    # 用**规则直通**的问法（"抓一下 <url>"）：走插件 get 并原样给出正文，
    # 不依赖"模型这次愿不愿意把 JSON 贴出来"（实测用"用 stealthy_fetch 抓…"这种说法，
    # 模型会自己调 fetch_url 五六次、最后还不给代码块 → 假失败）。
    # 另：抓取插件的熔断是 30 秒 —— 刚跑完压力套件时 get 可能正在熔断窗口里，
    #     所以第一次没走通就等一会儿重试一次（这是环境时序，不是产品缺陷）。
    code, r = post("/api/chat", {"message": "抓一下 https://httpbin.org/json"})
    if code == 200:
        a = r.json().get("answer", "")
        _tr = [t.get("tool") for t in (r.json().get("tool_trace") or [])]
        if "get" not in _tr and "```" not in a:
            print("  · 抓取工具可能在熔断窗口内，等 31 秒重试一次…")
            time.sleep(31)
            code, r = post("/api/chat", {"message": "抓一下 https://httpbin.org/json"})
            a = (r.json().get("answer", "") if code == 200 else "")
            _tr = [t.get("tool") for t in ((r.json().get("tool_trace") or []) if code == 200 else [])]
        # 只要确实是**抓取工具**干的活就算过（get / make_request / fetch / stealthy_fetch …）：
        # 判据是"真抓了且不靠模型转述"，不必钉死某一个工具名（模型偶尔会自己挑 fetch，
        # 结果一样正确 —— 实测钉死 get 会假失败）。
        _fetch_tools = {"get", "make_request", "fetch", "stealthy_fetch", "scrape_with_selector",
                        "bulk_get", "bulk_fetch"}
        check("走抓取工具真抓（不靠模型转述）",
              bool(_fetch_tools & set(_tr)) and len(_tr) <= 3, str(_tr)[:60])
        check("JSON 被包成代码块（前端可渲染）", "```json" in a or "```\n{" in a, "")
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

    print("\n[9] 用户实测缺陷 1：用搜索工具找漏洞（不许把「用」当关键词）")
    code, r = post("/api/chat", {"message": "用搜索工具找漏洞"})
    if code == 200:
        a = r.json().get("answer", "")
        bad = [k for k in ("汉语汉字", "部首", "拼音是", "笔画", "释义：用") if k in a]
        check("没有把功能字「用」当主题回答", not bad, "命中=%s" % (bad or "无"))
        check("给出了真实漏洞/已清洗的检索关键词",
              ("CVE-" in a) or ("NVD" in a) or ("已把" in a), a[:90].replace("\n", " "))
    else:
        check("用例 1 可执行", False, "HTTP %s" % code)

    print("\n[10] 用户实测缺陷 2：抓取最近 7 天的高危漏洞（时间窗 + 软件名 + 完整行数）")
    code, r = post("/api/chat", {"message": "抓取最近 7 天的高危漏洞"}, timeout=300)
    if code == 200:
        a = r.json().get("answer", "")
        rows = [ln for ln in a.splitlines() if ln.startswith("|")]
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
        check("走的是 NVD 结构化数据（不是新闻搜索）", "NVD" in a and "lastModStartDate" in a,
              a.splitlines()[0][:60] if a else "")
        check("时间窗 = 最近 7 天（杜绝历史数据）", today in a, "窗口起点应为 %s" % today)
        check("表格完整：表头+分隔+5 行数据", len(rows) == 7, "表格行=%d" % len(rows))
        cells = [c.strip() for c in (rows[2].split("|")[1:-1])] if len(rows) > 2 else []
        check("每行字段齐全（序号/CVE/等级/评分/软件/时间/摘要）", len(cells) == 7 and "CVE-" in cells[1],
              str(cells[:4]))
        check("等级为 HIGH 或 CRITICAL",
              all(("| HIGH |" in ln) or ("| CRITICAL |" in ln) for ln in rows[2:]), "")
        # 判"每行都有交代"而不是"必须有 3 个真名"：NVD 偶尔确实没收录产品配置（无 CPE），
        # 那时页面写「(NVD 未收录产品配置)」是**正确行为**；按真名数量判会随当天 CVE 组成飘。
        softs = []
        for ln in rows[2:]:
            cs = [x.strip() for x in ln.split("|")[1:-1]]
            if len(cs) >= 7:
                softs.append(cs[4])
        real = [s for s in softs if s and "未收录" not in s]
        check("受影响软件每行都有交代（真名或「未收录」标注），不出现裸 n/a",
              len(softs) == 5 and all(s and s != "n/a" for s in softs), str(softs)[:70])
        # 这一条**不能当硬门槛**：NVD 当天新发布的 CVE 常常整批都没收录 CPE
        # （实测出现过 5/5 全是「(NVD 未收录产品配置)」）。有名字就顺带验证
        # "CPE 被翻成人话"，没有就如实说明是数据侧的事，不算失败。
        if real:
            check("有 CPE 的行被翻成可读软件名（不是裸 cpe:2.3…）",
                  not any(s.startswith("cpe:") for s in real), str(real)[:70])
        else:
            print("  ℹ️ 本次 5 行的 NVD 记录都没收录 CPE（数据侧）→ 跳过「软件名可读性」"
                  "（该逻辑在离线用例另有覆盖：CPE→人话）")
    else:
        check("用例 2 可执行", False, "HTTP %s" % code)

    print("\n[11] 会话删除（侧栏 ✕ 用的接口）")
    code, r = post("/api/session/new", {})
    sid = (r.json().get("id") if code == 200 else "") or ""
    check("能新建会话（拿到 id）", bool(sid), "id=%s" % sid)
    if sid:
        code, r = post("/api/session/delete", {"id": sid})
        body = r.json() if code == 200 else {}
        check("能删除会话", code == 200 and body.get("ok") is True, str(body)[:80])
        _cur, _ = post("/api/session/new", {})
        if _cur == 200:
            pass
        code, r = get("/api/sessions")
        ids = [s.get("id") for s in (r.json().get("sessions") or [])] if code == 200 else []
        check("删掉的会话不再出现在列表里", sid not in ids, "剩余 %d 个会话" % len(ids))
    code, r = post("/api/session/delete", {"id": "不存在的会话id"})
    check("删不存在的会话给中文错误", code == 404 and "会话" in (r.json().get("error") or ""),
          "HTTP %s" % code)
    code, r = post("/api/session/delete", {})
    check("不传 id 时给中文错误", code == 400, "HTTP %s" % code)

    print("\n[12] 时间类提问必须用真实时间（真实缺陷：模型自己编日期）")
    _sid2 = ""
    code, r = post("/api/session/new", {})
    if code == 200:
        _sid2 = (r.json().get("id") or "")
    code, r = post("/api/chat", {"message": "现在几点了"})
    ans = (r.json().get("answer") or "") if code == 200 else ""
    _now = time.strftime("%Y")
    check("问时间时给出的是真实年份（不是模型猜的）", _now in ans,
          "答案里应有 %s：%s" % (_now, ans[:60].replace("\n", " ")))
    if _sid2:
        post("/api/session/delete", {"id": _sid2})

    print("\n" + "=" * 68)
    print("  实机验收：通过 %d / %d" % (len(passed), len(passed) + len(failed)))
    for f in failed:
        print("    ❌ %s" % f)
    print("=" * 68)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
