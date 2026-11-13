# -*- coding: utf-8 -*-
"""人话闸：判断一句话"像不像人说的"，给所有**说话路径**共用。

总纲（与本项目一致）：
    小焦不依赖任何具体模型；载体负责把"模型随口吐的东西"收拾成能发出去的话。

为什么单独抽一个模块：
    先在 B站 评论里写了 `_human_score`，但同类的病（旁白、markdown、提示词回显、
    "你："前缀）在**群聊 / 好友 / 朋友圈**同样会出现。规则抄三份必然漂移，
    所以集中在这里，谁要说话谁就过一遍。

两种用法：
    · `hard_flaws(t)` —— 只挑**硬伤**（结构性毛病）。给"宁可少说也不说错"的路径用，
      命中就别发（群聊/好友/朋友圈用这个，避免误杀正常长回复）。
    · `score(t)` —— 0~100 打分。给需要"重写直到像人"的路径用（B站评论用这个）。
"""
import re

# 结构性硬伤：出现即判"这不像人说的话"
_SCAFFOLD = ("视频里讲了什么", "他说：", "他说:", "这视频的标题", "标题：")
_NARRATION = ("说罢", "你随口", "顿了顿", "眼睛还", "语气轻快", "回了一句", "你补充")


def hard_flaws(t):
    """返回硬伤清单（空列表 = 没硬伤）。"""
    s = str(t or "").strip()
    out = []
    if not s:
        return ["空"]
    if "SKIP" in s:
        return ["SKIP"]
    for k in _SCAFFOLD:
        if k in s:
            out.append("提示词回显（%s）" % k)
    for k in _NARRATION:
        if k in s:
            out.append("旁白（%s）" % k)
    if re.search(r"\*\*|##|```", s):
        out.append("markdown 标记")
    if re.search(r"[（(][^）)]{4,}[）)]", s):
        out.append("括号自我说明")
    if re.match(r"^\s*(?:回复\s*[^：:]{1,12}|你|我|他|她)\s*[：:]\s*", s):
        out.append("说话人前缀")
    if s.count("\n") >= 3:
        out.append("多行(%d)" % s.count("\n"))
    return out


def score(t):
    """0~100 分。>=70 视为"像人"；<50 建议别发。"""
    s = str(t or "").strip()
    if not s:
        return 0, ["空"]
    if "SKIP" in s:
        return 0, ["SKIP"]
    sc, why = 100, []
    for bad, w in (("视频里讲了什么", 60), ("他说：", 60), ("他说:", 60),
                   ("回复 ", 50), ("标题：", 40), ("**", 45), ("##", 40),
                   ("说罢", 40), ("你随口", 40), ("顿了顿", 35), ("眼睛还", 35)):
        if bad in s:
            sc -= w
            why.append("含「%s」" % bad)
    if re.search(r"[（(][^）)]{2,}[）)]", s):
        sc -= 35
        why.append("括号自我说明")
    if s.count("\n") >= 2:
        sc -= 30
        why.append("多行")
    if len(s) > 90:
        sc -= 25
        why.append("太长(%d字)" % len(s))
    if len(s) < 6:
        sc -= 40
        why.append("太短")
    if s.count("。") + s.count("！") + s.count("？") > 3:
        sc -= 20
        why.append("句子太多")
    return max(0, sc), why


def is_human(t, floor=70):
    """够不够"像人"。"""
    return score(t)[0] >= floor
