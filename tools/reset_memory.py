# -*- coding: utf-8 -*-
"""把"她认识的你"全部清空，从零重新认识（默认预览，--apply 才写）

【为什么要有这个脚本】用户要「清空之前的记忆，重新认识他」。手删很容易漏 ——
记忆分散在十几个文件里：对话记忆、画像、学到的知识、工具技能、会话历史、自我模型…
漏一个，"旧印象"就还会从别的门进来。

本脚本按**层级**清，每一层都先整份备份到 `logs/backup_reset_<时间>/`：

  L1  关于你的记忆        logs/xiaojiao_memory_vec.jsonl、logs/psyche/user_profile.jsonl
  L2  学到的知识与技能    self_learn/knowledge_vec.json、self_learn/tool_skills.txt
                          xiaojiao_knowledge_memory.json
  L3  会话历史            xiaojiao_sessions.json（重置成一个空白会话）
  L4  自我侧的记忆流      logs/self_model.jsonl、logs/memory_upkeep.jsonl、
                          logs/psyche/preference.jsonl（她自己的印象、保养账、偏好）

**默认做 L1+L2+L3**（= 她完全不记得你，也不再背着你以前的对话）；
`--level=4` 连她自己的自我模型一起清；`--only=1` / `--only=12` 只清指定层。

⚠️ 不动的东西：`plugins/`、`core/`、各 config、`logs/identity.json`（那是你给她的**设定**，
不是她的记忆）、`logs/backup_*` 备份本身。

用法：
    python tools/reset_memory.py               # 预览：会说清每层会清掉多少
    python tools/reset_memory.py --apply       # 真清（先备份）
    python tools/reset_memory.py --apply --only=1     # 只清"关于你的记忆"
"""
import io
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
APPLY = "--apply" in sys.argv
ONLY = ""
for a in sys.argv:
    if a.startswith("--only="):
        ONLY = a.split("=", 1)[1]

LEVELS = {
    1: [("对话记忆", "logs/xiaojiao_memory_vec.jsonl"),
        ("画像（关于你的印象）", "logs/psyche/user_profile.jsonl")],
    # L2 只动 knowledge_vec 里"关于你"的那部分；工具技能（tool_skills.txt）与
    # 知识记忆（xiaojiao_knowledge_memory.json）是"干活的技能"，不属于"对你的记忆"，保留。
    2: [("知识向量（只清「关于你」的部分）", "self_learn/knowledge_vec.json")],
    3: [("会话历史", "xiaojiao_sessions.json")],
    4: [("自我模型", "logs/self_model.jsonl"),
        ("记忆保养账", "logs/memory_upkeep.jsonl"),
        ("偏好", "logs/psyche/preference.jsonl")],
}


def picked():
    want = set()
    if ONLY:
        want = {int(c) for c in ONLY}
    else:
        want = {1, 2, 3}
    return {lv: items for lv, items in LEVELS.items() if lv in want}


def count_of(path):
    if not os.path.exists(path):
        return 0
    try:
        if path.endswith(".jsonl"):
            return sum(1 for ln in open(path, encoding="utf-8", errors="replace") if ln.strip())
        if path.endswith(".json"):
            d = json.load(open(path, encoding="utf-8"))
            if isinstance(d, dict) and "entries" in d:
                return len(d.get("entries") or [])
            if isinstance(d, dict) and "sessions" in d:
                return len(d.get("sessions") or [])
            return 1
        return sum(1 for ln in open(path, encoding="utf-8", errors="replace") if ln.strip())
    except Exception:
        return -1


def empty_like(path):
    """生成"清空后"的内容（保留结构，不破坏读取方）。"""
    if path.endswith("knowledge_vec.json"):
        # 【只清"关于你/你问过什么"的那部分，留着"怎么用工具"的经验】
        #   实测这条库里混着两类：①「反思: 用户问X，小焦答Y…用户更正为…」②「小焦用「fetch」成功…」
        #   ①属于"对你的记忆"（要清），②属于"干活的技能"（留着，否则她连工具都不会用了）。
        try:
            ents = json.load(open(path, encoding="utf-8")).get("entries") or []
        except Exception:      # noqa: silent-ok — 读不了就整份清空（宁缺勿脏）
            ents = []
        keep = [e for e in ents
                if not re.match(r"^(反思\s*[:：]?\s*)?用户", str(e.get("text", "")).strip())]
        return json.dumps({"entries": keep}, ensure_ascii=False)
    if path.endswith("sessions.json"):
        sid = uuid.uuid4().hex[:10]
        return json.dumps({"current": sid,
                           "sessions": [{"id": sid, "title": "新对话", "messages": []}]},
                          ensure_ascii=False)
    if path.endswith("knowledge_memory.json"):
        return json.dumps({}, ensure_ascii=False)
    if path.endswith(".json") and not path.endswith(".jsonl"):
        return json.dumps({}, ensure_ascii=False)
    return ""


def main():
    bdir = os.path.join("logs", "backup_reset_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    plan = picked()
    print("模式：%s" % ("真清（先整份备份）" if APPLY else "预览（不改任何文件）"))
    print("清的层级：%s" % "、".join("L%d" % k for k in sorted(plan)))
    total = 0
    for lv in sorted(plan):
        print("\nL%d:" % lv)
        for label, path in plan[lv]:
            n = count_of(path)
            has = os.path.exists(path)
            total += max(0, n)
            print("   %-22s %-40s %s" % (label, path, ("%d 条" % n) if has else "(不存在，跳过)"))
            if APPLY and has:
                os.makedirs(bdir, exist_ok=True)
                shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(empty_like(path))
    print("\n合计清空约 %d 条记录" % total)
    if APPLY:
        print("备份在：%s" % bdir)
        print("\n清完之后她会：不记得你说过的任何事、不记得聊过什么、不会拿旧对话当上下文；")
        print("设定还在（logs/identity.json、config 均未动）。想找回：把备份里的文件拷回去即可。")
    else:
        print("\n加 --apply 才会真清。")


if __name__ == "__main__":
    main()
