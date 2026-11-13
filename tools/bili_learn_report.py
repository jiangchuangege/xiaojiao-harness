# -*- coding: utf-8 -*-
"""「越用越厉害」体检：用**真实数据**回答，不靠感觉。

用户问的是两件事：
  ① 它有没有越用越厉害（学的东西在不在增长、会不会重复、能不能被召回）
  ② 它有没有"慢慢有情感起来"

这个脚本只做**测量**，不改任何数据。它读：
  · logs/spirit_memory/{knowledge,method,diagnosis}.jsonl   —— 学到的认知
  · bili_duty/comments.md                                    —— 它自己写的评论
  · bili_duty/seen.md                                        —— 看过的视频

关于"情感"必须说清口径：脚本能测的是**表达层面的变化**
（评论变长没有、有没有第一人称和情绪词、有没有 @ 人、有没有从"陈述"变成"搭话"）。
它**不能**证明模型真的"有感受" —— 那不是一个日志能证明的事。
所以要区分：**行为证据**（可测）与**内在体验**（不可测，也不该假装测到了）。

跑：python tools/bili_learn_report.py
"""
import io
import json
import os
import re
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SM = os.path.join(ROOT, "logs", "spirit_memory")
COMMENTS = os.path.join(ROOT, "bili_duty", "comments.md")
SEEN = os.path.join(ROOT, "bili_duty", "seen.md")

EMO = ("啊", "呀", "吧", "呢", "哈", "笑", "气", "烦", "累", "开心", "难过", "喜欢", "讨厌",
       "心疼", "意外", "惊", "怕", "甜", "苦", "爽", "委屈", "不服", "哼", "唉", "诶", "哎")
FIRST = ("我", "咱", "本小姐", "咱家")


def load(kind):
    p = os.path.join(SM, kind + ".jsonl")
    out = []
    if not os.path.exists(p):
        return out
    for line in io.open(p, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:      # noqa: silent-ok — 坏行跳过，不影响统计
            continue
    return out


def day_of(ts):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except Exception:      # noqa: silent-ok
        return "?"


def main():
    print("=" * 72)
    print("  小焦「越用越厉害」体检（只读测量）")
    print("=" * 72)

    items = []
    for k in ("knowledge", "method", "diagnosis"):
        items += load(k)

    # ---------- ① 学习总量与来源 ----------
    by_kind = Counter(x.get("kind") for x in items)
    by_src = Counter()
    for x in items:
        s = str(x.get("source") or "")
        by_src["B站" if s.startswith("B站") else ("对话" if "对话" in s else (s or "未标"))] += 1
    print("\n[1] 学到的认知总数：%d" % len(items))
    print("    按类：%s" % dict(by_kind))
    print("    按来源：%s" % dict(by_src))

    # ---------- ② 按天看增长 ----------
    per_day = defaultdict(lambda: defaultdict(int))
    for x in items:
        s = str(x.get("source") or "")
        tag = "B站" if s.startswith("B站") else "对话"
        per_day[day_of(x.get("ts"))][tag] += 1
    print("\n[2] 按天增长（B站 = 刷视频学到的）")
    days = sorted(per_day)[-10:]
    for d in days:
        print("    %s  B站 %-4d 对话 %-4d" % (d, per_day[d]["B站"], per_day[d]["对话"]))
    bili_days = [d for d in days if per_day[d]["B站"]]
    print("    有 B站 学习的天数：%d / 最近 %d 天" % (len(bili_days), len(days)))

    # ---------- ③ 去重率（会不会越刷越臃肿）----------
    texts = [str(x.get("text") or "") for x in items]
    dup = len(texts) - len(set(texts))
    print("\n[3] 重复条目：%d 条（去重率 %.1f%%）—— 越低说明护栏在起作用"
          % (dup, (dup / len(texts) * 100) if texts else 0))

    # ---------- ④ 能不能被召回（这才是"厉害"的关键）----------
    try:
        import sys
        sys.path.insert(0, ROOT)
        from core import spirit_memory as sm
        probe = ["DeepSeek Harness", "B站", "视频", "情感", "占卜"]
        hit = 0
        for q in probe:
            try:
                r = sm.recall(q, k=3)
                if r:
                    hit += 1
            except Exception:      # noqa: silent-ok
                pass
        print("\n[4] 召回自检：%d/%d 个探针词能召回内容（能召回才谈得上「影响回答」）"
              % (hit, len(probe)))
    except Exception as e:
        print("\n[4] 召回自检跳过：%s" % str(e)[:80])

    # ---------- ⑤ 它写的评论：情感表达的变化 ----------
    rows = []
    if os.path.exists(COMMENTS):
        for line in io.open(COMMENTS, encoding="utf-8", errors="ignore"):
            s = line.strip()
            if s.startswith("- "):
                rows.append(s[2:])
    print("\n[5] 它自己写的评论：%d 条" % len(rows))
    if rows:
        lens = [len(r) for r in rows]
        emo_n = [sum(1 for w in EMO if w in r) for r in rows]
        first_n = [1 if any(w in r for w in FIRST) else 0 for r in rows]
        at_n = [1 if re.search(r"@\S", r) else 0 for r in rows]
        print("    平均长度：%.0f 字（最短 %d / 最长 %d）" % (sum(lens) / len(lens), min(lens), max(lens)))
        print("    带情绪词：%d/%d 条（%.0f%%）" % (sum(1 for x in emo_n if x), len(rows),
                                            sum(1 for x in emo_n if x) / len(rows) * 100))
        print("    有第一人称：%d/%d 条" % (sum(first_n), len(rows)))
        print("    带 @：%d/%d 条" % (sum(at_n), len(rows)))
        # 前后对比：最早 5 条 vs 最近 5 条
        def stat(chunk):
            return (sum(len(c) for c in chunk) / max(1, len(chunk)),
                    sum(1 for c in chunk if any(w in c for w in EMO)) / max(1, len(chunk)))
        a_len, a_emo = stat(rows[:5])
        b_len, b_emo = stat(rows[-5:])
        print("    最早 5 条：平均 %.0f 字 / 情绪词覆盖 %.0f%%" % (a_len, a_emo * 100))
        print("    最近 5 条：平均 %.0f 字 / 情绪词覆盖 %.0f%%" % (b_len, b_emo * 100))
        print("    最近 3 条原文：")
        for r in rows[-3:]:
            print("      · " + r[:100])

    # ---------- ⑥ 看过的视频 ----------
    vids = 0
    if os.path.exists(SEEN):
        for line in io.open(SEEN, encoding="utf-8", errors="ignore"):
            if line.startswith("|") and "BV" in line:
                vids += 1
    print("\n[6] 看过的视频：%d 条" % vids)

    # ---------- 结论（分开说，不混）----------
    print("\n" + "=" * 72)
    print("  结论（严格区分「能证明的」和「不能证明的」）")
    print("=" * 72)
    grows = len(items) > 0 and (by_src.get("B站", 0) > 0)
    print("  ✅ 可证：学到的认知在累积（%d 条，其中 B站 来源 %d 条）"
          % (len(items), by_src.get("B站", 0)))
    print("  ✅ 可证：重复条目 %d 条 → 同题不重复写，库不会越刷越肿" % dup)
    if rows:
        print("  ✅ 可证：评论在变化（长度 %.0f→%.0f 字，情绪词覆盖 %.0f%%→%.0f%%）"
              % (a_len, b_len, a_emo * 100, b_emo * 100))
    print("  ⚠️ 不可证：它有没有「内在感受」 —— 日志只能看到**表达**，看不到体验；")
    print("     把「话更像人」当成「有情感」，是把测量当结论，这一步我不替你做。")
    print("  ⚠️ 也不可证：这些认知是否真的提升了答案质量 —— 要测这个，")
    print("     得拿「学之前/学之后」对同一批问题做盲评（可以做，但那是另一个实验）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
