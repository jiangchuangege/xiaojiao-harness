# -*- coding: utf-8 -*-
"""体检：记忆库 / 画像库 / 学习库里到底混进了什么（只读，不改任何文件）

按"能不能直接拿来喂上下文"分类计数，并给出每类的前几条样例：
  A 工具原始返回残留      B 坏回复（占位符/半截/未闭合）
  C 自我回灌（它自己说的被当成事实）  D 载体指令泄漏
  E 过长（>400 字，冲淡事实）        F 重复（同一句问过很多次）
  G 空壳/极短                        H 画像库里的解析垃圾

用法：python tools/audit_memory_health.py
"""
import io
import json
import os
import re
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def load_jsonl(p):
    out = []
    if not os.path.exists(p):
        return out
    for ln in open(p, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"_bad": ln[:120]})
    return out


def show(title, rows, n=3, width=100):
    print("\n---- %s：%d 条 ----" % (title, len(rows)))
    for r in rows[:n]:
        s = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        print("   · " + s.replace("\n", " ")[:width])


# ================== A. 记忆库 ==================
MEM = load_jsonl("logs/xiaojiao_memory_vec.jsonl")
print("=" * 78)
print("【记忆库】logs/xiaojiao_memory_vec.jsonl ：%d 条" % len(MEM))

TOOL_MARKS = ("🌐 **", "HTTP 200", "📖 **小焦解读**", "⚠️ 抓取失败", "📋 **页面结构速览**",
              "⏳ 正在抓取", "✅ 抓取成功")
BAD_MARKS = ("⏳__pending__", "（已回答）", "【系统指令】", "【回答格式，务必遵守】",
             "没有关于你", "没查到", "没有记录", "我记不起")
SELF_MARKS = ("小焦：", "【小焦说过的】")

cat = Counter()
examples = {k: [] for k in ("tool", "bad", "self_echo", "long", "empty", "dup")}
key_count = Counter()

for r in MEM:
    txt = str(r.get("text") or "")
    key = str(r.get("key") or "")
    key_count[key] += 1
    if any(m in txt for m in TOOL_MARKS):
        cat["A 工具原始返回"] += 1
        examples["tool"].append(txt)
    if any(m in txt for m in BAD_MARKS):
        cat["B 坏回复/占位符"] += 1
        examples["bad"].append(txt)
    # 自我回灌：'用户：X  小焦：…' 里 X 是问句、且答案只有它自己那句话
    m = re.search(r"用户：(.{1,80})", txt)
    if m and any(q in m.group(1) for q in ("吗", "?", "？")) and "小焦：" in txt:
        cat["C 自我回灌（问答对）"] += 1
        examples["self_echo"].append(txt)
    if len(txt) > 400:
        cat["E 过长（>400 字）"] += 1
        examples["long"].append(txt)
    if len(re.sub(r"\W", "", txt)) < 4:
        cat["G 空壳/极短"] += 1
        examples["empty"].append(txt)

dups = {k: c for k, c in key_count.items() if k and c > 1}
cat["F 同一句问过多次"] = sum(c - 1 for c in dups.values())
examples["dup"] = ["%s  ×%d" % (k[:60], c) for k, c in list(dups.items())[:5]]

for k in sorted(cat):
    print("   %-22s %5d" % (k, cat[k]))

show("A 工具原始返回残留", examples["tool"])
show("B 坏回复/占位符", examples["bad"])
show("C 自我回灌（问句 + 它自己的答）", examples["self_echo"])
show("E 过长", examples["long"])
show("F 重复问句", examples["dup"])
show("G 空壳/极短", examples["empty"])

# ================== B. 画像库 ==================
PROF = load_jsonl("logs/psyche/user_profile.jsonl")
print("\n" + "=" * 78)
print("【画像库】logs/psyche/user_profile.jsonl ：%d 条" % len(PROF))
kinds = Counter(str(p.get("kind") or "?") for p in PROF)
print("   类别分布：" + ", ".join("%s=%d" % (k, v) for k, v in kinds.most_common(10)))
junk, seen, dup = [], Counter(), 0
for p in PROF:
    c = str(p.get("content") or "")
    if not c.strip() or any(ch in c for ch in "{}[]\n\r\t") or len(c) > 120:
        junk.append(c)
    seen[c] += 1
dup = sum(v - 1 for v in seen.values() if v > 1)
print("   解析垃圾/超长：%d 条 ｜ 完全相同重复：%d 条" % (len(junk), dup))
show("画像垃圾", junk)
show("画像样例（最近的 6 条）", [str(p.get("content") or "") for p in PROF[-6:]], n=6)

# ================== C. 学习库 ==================
print("\n" + "=" * 78)
print("【知识向量】self_learn/knowledge_vec.json")
try:
    kv = json.load(open("self_learn/knowledge_vec.json", encoding="utf-8"))
    ents = kv.get("entries") or []
    print("   条目：%d" % len(ents))
    bad = [e for e in ents if any(m in str(e.get("text", "")) for m in TOOL_MARKS + BAD_MARKS)]
    print("   含工具残留/坏回复：%d" % len(bad))
    show("知识向量里的脏数据", [str(e.get("text", "")) for e in bad])
except Exception as e:
    print("   读不了：%s" % e)

print("\n【工具技能】self_learn/tool_skills.txt")
try:
    lines = [l.strip() for l in open("self_learn/tool_skills.txt", encoding="utf-8", errors="replace") if l.strip()]
    bad = [l for l in lines if any(m in l for m in ("【回答格式", "⏳__pending__", "🌐 **"))]
    print("   行数：%d ｜ 脏行：%d" % (len(lines), len(bad)))
    show("脏行", bad)
except Exception as e:
    print("   读不了：%s" % e)

print("\n" + "=" * 78)
print("体检结束（只读，未改动任何文件）")
