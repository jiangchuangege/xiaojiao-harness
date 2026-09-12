# -*- coding: utf-8 -*-
"""24 小时稳定性长跑（每小时一轮，记录成功率 / P95 延迟 / 堆增长 / 异常）

用途：验证"长时间运行不崩、不泄漏、延迟不衰减"。
本项目在常规开发会话里跑不了 24 小时，所以把脚本留在这里 —— 需要时你自己跑：

    python tests/stress/stability_24h.py --hours 24 --per-round 10 --interval 3600
    python tests/stress/stability_24h.py --hours 1  --per-round 5  --interval 60   # 快速试跑

产出：
  · stability_report.json —— 每轮明细 + 汇总结论（机读）
  · stability_summary.md  —— 人类可读摘要（可直接贴进 issue/PR）

判定规则（写进结论）：
  · 崩溃 / 未捕获异常 → 直接标记 FAIL
  · 堆增长：首轮 vs 末轮对比，若持续单调增长且总增幅 > 50MB → 标记 SUSPECT（疑似泄漏）
  · 延迟衰减：末 1/3 轮的 P95 相对首 1/3 轮涨幅 > 100% → 标记 SUSPECT
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Bridge, load_plugin  # noqa: E402

SAMPLE_URLS = [
    "https://example.com",
    "https://example.org",
    "https://www.iana.org/help/example-domains",
    "https://httpbin.org/html",
]


def heap_mb() -> float:
    return round(tracemalloc.get_traced_memory()[0] / 1048576.0, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取插件 24 小时稳定性长跑")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--per-round", type=int, default=10, help="每轮抓取多少个 URL")
    ap.add_argument("--interval", type=float, default=3600.0, help="每轮间隔秒数（默认 1 小时）")
    ap.add_argument("--out", default="stability_report.json")
    args = ap.parse_args()

    mod = load_plugin()
    b = Bridge(mod)
    rounds = max(1, int(args.hours * 3600 / max(1.0, args.interval)))

    tracemalloc.start()
    print("=" * 64)
    print("  稳定性长跑：%d 轮 × 每轮 %d 个 URL，间隔 %.0f 秒（合计约 %.1f 小时）"
          % (rounds, args.per_round, args.interval, rounds * args.interval / 3600))
    print("=" * 64, flush=True)

    history = []
    for i in range(rounds):
        t0 = time.time()
        lat, ok, errs = [], 0, []
        for j in range(args.per_round):
            url = SAMPLE_URLS[j % len(SAMPLE_URLS)]
            el, hung, err, d = b.call("get", {"url": url, "timeout": 25}, cap=60)
            lat.append(el)
            if err or hung:
                errs.append("%s → %s" % (url, (err or "挂死")[:60]))
            else:
                ok += 1
        rec = {"round": i + 1, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "ok": ok, "total": args.per_round,
               "p50": round(statistics.median(lat), 2) if lat else 0,
               "p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 2) if lat else 0,
               "heap_mb": heap_mb(), "errors": errs[:3], "round_s": round(time.time() - t0, 1)}
        history.append(rec)
        print("  第 %2d/%d 轮：成功 %d/%d · P50 %.2fs · P95 %.2fs · 堆 %.1fMB %s"
              % (rec["round"], rounds, ok, args.per_round, rec["p50"], rec["p95"], rec["heap_mb"],
                 ("⚠️ " + errs[0][:50]) if errs else ""), flush=True)

        # 每轮后导出一次指标，便于事后分析
        try:
            b.impl.metrics_export()
        except Exception:  # noqa: silent-ok — 指标导出失败不影响长跑主流程
            pass

        if i < rounds - 1:
            time.sleep(args.interval)

    tracemalloc.stop()
    b.close_all()

    # ---------- 判定 ----------
    first, last = history[0], history[-1]
    heap_growth = round(last["heap_mb"] - first["heap_mb"], 2)
    n3 = max(1, len(history) // 3)
    p95_head = statistics.median([h["p95"] for h in history[:n3]]) or 0.01
    p95_tail = statistics.median([h["p95"] for h in history[-n3:]])
    lat_degrade = round((p95_tail - p95_head) / p95_head * 100, 1)
    total_ok = sum(h["ok"] for h in history)
    total_req = sum(h["total"] for h in history)
    success_rate = round(total_ok * 100.0 / max(1, total_req), 2)

    verdict, notes = "PASS", []
    if success_rate < 95:
        verdict = "FAIL"
        notes.append("成功率 %.2f%% < 95%%" % success_rate)
    if heap_growth > 50:
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        notes.append("堆增长 %.1fMB（>50MB，疑似泄漏）" % heap_growth)
    if lat_degrade > 100:
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        notes.append("P95 延迟较前期上涨 %.1f%%（疑似衰减）" % lat_degrade)

    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "rounds": rounds, "per_round": args.per_round, "interval_s": args.interval,
              "success_rate": success_rate, "heap_growth_mb": heap_growth,
              "p95_change_pct": lat_degrade, "verdict": verdict, "notes": notes,
              "history": history}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = ["# 稳定性长跑报告", "",
          "- 轮次：%d（每轮 %d 个 URL，间隔 %.0f 秒）" % (rounds, args.per_round, args.interval),
          "- 成功率：**%.2f%%**（%d/%d）" % (success_rate, total_ok, total_req),
          "- 堆增长：**%.2f MB**" % heap_growth,
          "- P95 变化：**%+.1f%%**" % lat_degrade,
          "- 结论：**%s**" % verdict, ""]
    if notes:
        md += ["## 需要关注", ""] + ["- %s" % n for n in notes] + [""]
    md += ["## 每轮明细", "", "| 轮次 | 时间 | 成功 | P50 | P95 | 堆(MB) |", "|---|---|---|---|---|---|"]
    for h in history:
        md.append("| %d | %s | %d/%d | %.2fs | %.2fs | %.1f |"
                  % (h["round"], h["at"], h["ok"], h["total"], h["p50"], h["p95"], h["heap_mb"]))
    with open("stability_summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("\n结论：%s%s" % (verdict, ("（" + "；".join(notes) + "）") if notes else ""))
    print("报告：%s · 摘要：stability_summary.md" % args.out)
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
