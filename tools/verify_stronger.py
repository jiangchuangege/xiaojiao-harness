# -*- coding: utf-8 -*-
"""② + ① 合体实测：**有没有变强**，用可核判据，不靠感觉。

两块：
  A) 工具是否让答案变对（4 题，必须用 B站 数据才能答对）
       A 组：只问模型（它只能猜）
       B 组：先真取数据（字幕/评论），把数据喂给同一个模型
     判据：答案里是否出现**只有看过数据才知道**的关键点（人工从数据里挑的关键词组）
  B) 学到的东西是否让答案变对（10 题，来自 B站 学来的认知）
       A 组：无记忆；B 组：注入相关认知
     判据：关键事实命中率

跑：python tools/verify_stronger.py            （全跑，十几分钟）
    python tools/verify_stronger.py --quick    （只跑 A 块）
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

# A 块：问题 + 用哪条视频 + 判据关键词（这些词只有在读过数据后才会出现）
TOOL_CASES = [
    ("BV1EoxK6UEnL", "这条视频里，作者说他自己搭的这套系统一个月花了多少钱？",
     ["4200", "四千二"]),
    ("BV1EoxK6UEnL", "这条视频提到的'三个坑'里，第一个坑是什么？",
     ["插件", "主程序"]),
    ("BV1vx411M7DA", "这条视频大概是什么内容？",
     ["舞蹈", "尬舞", "翻跳", "c哩"]),
    ("BV1EoxK6UEnL", "这条视频里作者提到他的系统跑了多少次请求？",
     ["15万", "150000", "十五万"]),
]

# B 块：从学到的认知里挑，问题必须能用那条认知答
LEARN_CASES = [
    ("装插件之前应该先确认什么？", ["主程序"], "装插件前先查主程序"),
    ("为什么有些没配音的长篇叙事游戏口碑不错？", ["沉浸", "节奏"], "长叙事靠沉浸与慢热"),
    ("占卜直播怎么提高观众参与度？", ["关键词", "互动"], "用关键词增强互动"),
    ("B站 视频的标题和封面有什么用？", ["标题", "封面"], "标题封面影响点击"),
    ("下班回家想恢复精力，先做什么？", ["放松", "洗"], "先物理放松"),
]


def _hook(t):
    return "".join(str(t or "").lower().split())


def _hit(ans, keys):
    h = _hook(ans)
    return sum(1 for k in keys if k in h), len(keys)


def main():
    quick = "--quick" in sys.argv
    import xiaojiao_app as X
    X._build_tools()

    print("=" * 72)
    print("  实测：到底有没有变强")
    print("=" * 72)

    # ---------- A 块：工具 ----------
    print("\n[A] 工具让答案变对吗（同一模型，有数据 vs 没数据）")
    a_worse = a_better = a_same = 0
    for bv, q, keys in TOOL_CASES:
        # A：只问模型
        plain = str(X.llm_chat([{"role": "user", "content": q}]) or "")
        # B：先取真数据，再问
        try:
            sub = str(X.run_tool("bili_subtitle", {"bvid": bv}, force=True))[:2500]
        except Exception as e:      # noqa: BLE001
            sub = "取字幕失败：%s" % str(e)[:60]
        cm = ""
        try:
            cm = str(X.run_tool("bili_comments", {"bvid": bv, "num": 10}, force=True))[:1200]
        except Exception:      # noqa: silent-ok
            pass
        fed = str(X.llm_chat([
            {"role": "system", "content": "只根据下面提供的资料回答；资料里没有就说不知道，不要编。"},
            {"role": "user", "content": "资料：\n%s\n\n评论：\n%s\n\n问题：%s" % (sub, cm, q)}]) or "")
        ka, na = _hit(plain, keys)
        kb, nb = _hit(fed, keys)
        tag = "✅变好" if kb > ka else ("❌变差" if kb < ka else "＝持平")
        if kb > ka:
            a_better += 1
        elif kb < ka:
            a_worse += 1
        else:
            a_same += 1
        print("  %s  %-30s 无数据 %d/%d ｜ 有数据 %d/%d" % (tag, q[:30], ka, na, kb, nb))
        print("      无数据答：%s" % plain.replace("\n", " ")[:60])
        print("      有数据答：%s" % fed.replace("\n", " ")[:60])

    print("\n[A 结论] 工具：变好 %d ｜ 持平 %d ｜ 变差 %d（共 %d 题）"
          % (a_better, a_same, a_worse, len(TOOL_CASES)))

    if quick:
        return 0

    # ---------- B 块：学到的认知 ----------
    print("\n[B] 学到的认知让答案变对吗")
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
                d = json.loads(line)
            except Exception:      # noqa: silent-ok
                continue
            learned.append(str(d.get("text") or ""))
    mem = "【你学到的（可以直接用）】\n" + "\n".join("- " + x for x in learned[:15])
    b_better = b_worse = b_same = 0
    for q, keys, note in LEARN_CASES:
        plain = str(X.llm_chat([{"role": "user", "content": q}]) or "")
        fed = str(X.llm_chat([{"role": "system", "content": mem},
                              {"role": "user", "content": q}]) or "")
        ka, na = _hit(plain, keys)
        kb, nb = _hit(fed, keys)
        tag = "✅变好" if kb > ka else ("❌变差" if kb < ka else "＝持平")
        if kb > ka:
            b_better += 1
        elif kb < ka:
            b_worse += 1
        else:
            b_same += 1
        print("  %s  %-26s 无记忆 %d/%d ｜ 有记忆 %d/%d（%s）" % (tag, q[:26], ka, na, kb, nb, note))

    print("\n[B 结论] 学习：变好 %d ｜ 持平 %d ｜ 变差 %d（共 %d 题）"
          % (b_better, b_same, b_worse, len(LEARN_CASES)))

    print("\n" + "=" * 72)
    print("  总判：")
    print("   工具  → 变好 %d / 持平 %d / 变差 %d" % (a_better, a_same, a_worse))
    print("   学习  → 变好 %d / 持平 %d / 变差 %d" % (b_better, b_same, b_worse))
    print("  样本很小（%d + %d 题），只能说明方向，不能当显著结论。"
          % (len(TOOL_CASES), len(LEARN_CASES)))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
