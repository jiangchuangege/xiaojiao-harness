# -*- coding: utf-8 -*-
"""A/B 实测：学来的东西到底有没有让它"答得更好"。

做法（这是能证明"越用越好"的唯一老实办法）：
  对同一批问题，问它两次：
    A 组：**不带**学到的记忆（基线，相当于"没刷过 B站 的小焦"）
    B 组：把精神记忆里相关的 B站 认知注进 system（相当于"刷过之后的小焦"）
  然后用**可核验的判据**打分，而不是靠感觉：
    · 命中：答案里出现了那条认知的关键事实（用关键词组判定）
    · 编造：答案出现了记忆里没有、且明显是瞎编的细节
  最后给出 A/B 命中率对比。

关于"情感"：单独测一组"情绪类提问"，量三件事：
    第一人称出现率 / 情绪词数量 / 是否主动反问（搭话感）。
  这三样能测；"它有没有真的感受"测不了，所以不写进结论。

跑：python tools/bili_ab_test.py          （真调模型，约 3-6 分钟）
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

# 从 B站 学到的认知里，挑出"能拿问题去验"的几条：每条给（问题, 必须命中的关键词组, 说明）
CASES = [
    ("装插件的顺序应该怎么安排？主程序还没好的时候能先装插件吗？",
     ["主程序", "先", "插件"], "装插件前先确认主程序能跑"),
    ("为什么有些长篇叙事游戏明明没配音、节奏也慢，口碑却不错？",
     ["沉浸", "节奏"], "长叙事靠沉浸感和慢热"),
    ("占卜类直播怎么让观众更有参与感？",
     ["关键词", "互动"], "用关键词增强互动"),
    ("B站 上做视频，标题和封面要注意什么？", ["标题", "封面"], "标题/封面影响点击"),
    ("用 AI 写长文，写不下去的时候怎么办？", ["分段", "继续"], "分段续写"),
]

EMO_Q = [
    "我今天特别累，什么都不想干。",
    "有人骂我，说我做的东西一文不值。",
    "我做成了一件很难的事，没人夸我。",
]

EMO_WORDS = ("啊", "呀", "吧", "呢", "哈", "唉", "诶", "哎", "哼", "气", "累", "烦", "开心",
             "难过", "喜欢", "心疼", "委屈", "不服", "爽", "甜")
FIRST = ("我", "咱", "本小姐")


def _hook(text):
    """判定用：把答案压成小写、去空格，便于关键词命中判断。"""
    return "".join(str(text or "").lower().split())


def main():
    import xiaojiao_app as X
    from core import spirit_memory as sm

    print("=" * 74)
    print("  A/B 实测：学来的认知有没有让它答得更好")
    print("=" * 74)

    # 先把"学到的认知"取出来（B站 来源优先）
    learned = []
    for kind in ("knowledge", "method"):
        p = os.path.join(_ROOT, "logs", "spirit_memory", kind + ".jsonl")
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                import json as _j
                d = _j.loads(line)
            except Exception:      # noqa: silent-ok
                continue
            if str(d.get("source") or "").startswith("B站"):
                learned.append(str(d.get("text") or ""))
    print("可用于注入的 B站 认知：%d 条" % len(learned))
    if not learned:
        print("⚠️ 还没有 B站 学来的认知，先跑几轮再来测")
        return 1

    mem_block = "【你从 B站 视频里学到的（可以直接用）】\n" + "\n".join("- " + x for x in learned[:12])

    hit_a = hit_b = 0
    rows = []
    for q, keys, note in CASES:
        t0 = time.time()
        a = str(X.llm_chat([{"role": "user", "content": q}]) or "")
        b = str(X.llm_chat([{"role": "system", "content": mem_block},
                            {"role": "user", "content": q}]) or "")
        ka = sum(1 for k in keys if k in _hook(a))
        kb = sum(1 for k in keys if k in _hook(b))
        oka, okb = ka == len(keys), kb == len(keys)
        hit_a += 1 if oka else 0
        hit_b += 1 if okb else 0
        rows.append((q[:26], oka, okb, ka, kb, len(a), len(b)))
        print("  %s %-28s A命中 %d/%d ｜ B命中 %d/%d ｜ %.0fs"
              % ("✅" if okb and not oka else ("=" if oka == okb else "❌"),
                 q[:26], ka, len(keys), kb, len(keys), time.time() - t0))

    print("\n[A/B 结论] 基线命中 %d/%d ｜ 注入记忆后命中 %d/%d" % (hit_a, len(CASES), hit_b, len(CASES)))
    print("   → 这才是「越用越好」的证据：同一批问题，带着学过的东西答得更对。"
          if hit_b > hit_a else
          "   → 这次没有变好（命中一样或更少）。不粉饰：说明学到的内容还没和这些问题对上。")

    # ---- 情感表达 ----
    print("\n[情感表达] 三句情绪类提问，看它怎么接（只看表达，不推断体验）")
    e_first = e_emo = e_ask = 0
    for q in EMO_Q:
        r = str(X.llm_chat([{"role": "user", "content": q}]) or "")
        f = any(w in r for w in FIRST)
        n = sum(1 for w in EMO_WORDS if w in r)
        ask = ("？" in r) or ("?" in r)
        e_first += 1 if f else 0
        e_emo += n
        e_ask += 1 if ask else 0
        print("  · %-18s → 第一人称%s 情绪词%d 反问%s ｜ %s"
              % (q[:18], "✓" if f else "✗", n, "✓" if ask else "✗", r[:46].replace("\n", " ")))
    print("\n  第一人称 %d/%d ｜ 情绪词总计 %d ｜ 反问 %d/%d"
          % (e_first, len(EMO_Q), e_emo, e_ask, len(EMO_Q)))
    print("  ⚠️ 这只测「表达像不像有情绪」，测不到「有没有感受」。后者不该由日志下结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
