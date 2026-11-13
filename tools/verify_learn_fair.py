# -*- coding: utf-8 -*-
"""公平复测：按**真实注入方式**（recall 检索 top-3）测学习有没有用。

上一次 `verify_stronger.py` 一次塞 15 条记忆，把简单题带偏了 —— 那是测试方法不公平，
不是产品行为。真实代码是「按问题召回 3 条」。这里照真实方式重测。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

CASES = [
    ("装插件之前应该先确认什么？", ["主程序"]),
    ("为什么有些没配音的长篇叙事游戏口碑不错？", ["沉浸", "节奏"]),
    ("占卜直播怎么提高观众参与度？", ["关键词", "互动"]),
    ("B站 视频的标题和封面有什么用？", ["标题", "封面"]),
    ("下班回家想恢复精力，先做什么？", ["放松", "洗"]),
]


def hook(t):
    return "".join(str(t or "").lower().split())


def main():
    import xiaojiao_app as X
    from core import spirit_memory as sm

    print("=" * 70)
    print("  学习有效性 · 公平复测（recall top-3，与真实注入一致）")
    print("=" * 70)
    better = same = worse = 0
    for q, keys in CASES:
        plain = str(X.llm_chat([{"role": "user", "content": q}]) or "")
        # 真实方式：按问题召回，最多 3 条
        try:
            hits = sm.recall(q, k=3) or []
        except Exception:
            hits = []
        texts = []
        for h in hits:
            if isinstance(h, dict):
                texts.append(str(h.get("text") or h.get("know") or ""))
            else:
                texts.append(str(h))
        texts = [t for t in texts if t][:3]
        mem = ("【你以前学到的，可能相关】\n" + "\n".join("- " + t[:120] for t in texts)) if texts else ""
        fed = str(X.llm_chat(([{"role": "system", "content": mem}] if mem else [])
                             + [{"role": "user", "content": q}]) or "")
        ka = sum(1 for k in keys if k in hook(plain))
        kb = sum(1 for k in keys if k in hook(fed))
        tag = "✅变好" if kb > ka else ("❌变差" if kb < ka else "＝持平")
        better += 1 if kb > ka else 0
        worse += 1 if kb < ka else 0
        same += 1 if kb == ka else 0
        print("  %s %-26s 无记忆 %d/%d ｜ 有记忆 %d/%d（召回 %d 条）"
              % (tag, q[:26], ka, len(keys), kb, len(keys), len(texts)))
        if texts:
            print("      召回：%s" % texts[0][:60])
    print("\n结论：变好 %d ｜ 持平 %d ｜ 变差 %d（共 %d 题）"
          % (better, same, worse, len(CASES)))
    print("样本仍小（5 题），只能看方向。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
