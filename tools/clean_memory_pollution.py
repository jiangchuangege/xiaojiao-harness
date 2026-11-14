# -*- coding: utf-8 -*-
"""清污染：记忆库 / 画像库 / 学习库里的脏行（默认只预览，`--apply` 才写盘）

【为什么要清】用户原话「我的AI最近我发现越来越难用」。体检结果（`tools/audit_memory_health.py`）：

  · 画像库里存着 **"用户叫小焦"**（小焦是它自己的名字）、**"用户称呼自己为'你'"**、
    **"用户称呼为'你'"**、**"用户称呼其为'小焦'"** —— 这些每轮都会被注入 system，
    于是它真的开始管用户叫"小焦"，还反复纠结"你是谁、我是谁"。
  · 记忆库 551 条里有 **88 条是自我回灌**（问句 + 它自己那次的长篇回答）、
    **6 条"我没有关于你…"**（"不知道"被当成知识存下来）、**4 条工具原始返回**、
    **6 条网页残留**。
  · 学习库 `knowledge_vec` 18 条、`tool_skills.txt` 10 行带着工具原始返回/载体指令。

判据全是确定性的（字符串规则），不做"像不像"的模糊判断。删之前先整份备份到
`logs/backup_clean_<时间>/`，任何一条都能捞回来。

用法：
    python tools/clean_memory_pollution.py            # 预览：只说会删多少
    python tools/clean_memory_pollution.py --apply    # 真删（先备份）
"""
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
APPLY = "--apply" in sys.argv

# ── 判据 ────────────────────────────────────────────────────────────
TOOL_MARKS = ("🌐 **", "📖 **小焦解读**", "📋 **页面结构速览**", "⏳ 正在抓取",
              "✅ 抓取成功", "⚠️ 抓取失败", "⚠️ **可能幻觉**")
INSTR_MARKS = ("【回答格式，务必遵守】", "【系统指令】", "⏳__pending__", "（已回答）")
NO_ANSWER_MARKS = ("没有关于你", "没有关于“你", "我记不起", "记忆库里没有关于",
                   "我没有关于你", "没查到关于你")
TRACE_MARKS = ("已执行 ", "这是载体真实做的事", "▾")
# 被误当成"名字"的代词/它自己的名字
NAME_JUNK = ("你", "我", "他", "她", "它", "自己", "小焦", "对方", "用户", "本小姐", "主人")

# 【测试遗留：不是用户说过的话】2026-11-14 我为了验证身份插件，用「以后叫我老板」跑过几轮，
# 画像判断那一环把"用户称呼我为老板"当事实记了下来。那不是用户说的，必须清掉。
TEST_LEFTOVERS = ("用户称呼我为老板", "用户称呼我为「老板」")


def _ai_name():
    try:
        d = json.load(open("logs/identity.json", encoding="utf-8"))
        n = str(d.get("self_name") or "").strip()
        if n:
            return n
    except Exception:      # noqa: silent-ok
        pass
    return "小焦"


# 归一化时要去掉的「语气词/框架词」（它们不改变"说的是哪件事"）
_FILLER = ("用户", "经常", "常常", "平时", "喜欢", "习惯", "在", "的", "了", "会",
           "观看", "看", "查询", "查看", "关注", "使用", "要", "想", "有", "是", "都")
_NEG = ("不", "没", "别", "无", "非", "讨厌", "不想")


def _fact_key(s):
    """把一句话压成「这件事」的键：小写 + 去标点 + 去框架词。

    实测要收的这几条，压完是同一个键：
      经常看b站视频 / 喜欢看B站视频 / 经常在B站观看视频  -> b站视频
      用户关注高危漏洞查询 / 关注高危漏洞                -> 高危漏洞
    正反话压完不同，并且下面还有一条**否定保护**：一边有否定词、另一边没有 => 绝不合并。
    """
    t = str(s or "").lower()
    t = re.sub(r"[\s，。！？、；：,.!?;:()（）【】「」\"'’‘—-]", "", t)
    for w in _FILLER:
        t = t.replace(w, "")
    return t


def collapse_same_fact(rows, threshold=None):
    """同一类里「同一件事的多个说法」收成一条（留最早那条）。返回 (保留, 合并条数)。"""
    kept, merged = [], 0
    for r in rows:
        c = str(r.get("content") or "")
        k = str(r.get("kind") or "")
        key = _fact_key(c)
        neg = any(w in c for w in _NEG)
        dup = False
        for k2 in kept:
            if str(k2.get("kind") or "") != k:
                continue
            c2 = str(k2.get("content") or "")
            key2 = _fact_key(c2)
            if not key or not key2:
                continue
            if neg != any(w in c2 for w in _NEG):
                continue                      # 一个肯定一个否定 => 不是同一件事
            if key == key2 or key in key2 or key2 in key:
                dup = True
                break
        if dup:
            merged += 1
        else:
            kept.append(r)
    return kept, merged


def profile_is_junk(content):
    c = str(content or "").strip()
    if not c:
        return True
    if c in TEST_LEFTOVERS:
        return True
    if any(m in c for m in TRACE_MARKS + INSTR_MARKS + TOOL_MARKS):
        return True
    if len(c) > 120:
        return True
    ai = _ai_name()
    m = re.match(r"^用户(?:的名字是|叫|名叫|称呼为|称呼其为|称呼自己为|称呼我为|称呼)"
                 r"[「\"'‘]?([^」\"'’，。]{1,8})", c)
    if m:
        val = m.group(1)
        if any(j in val for j in NAME_JUNK) or val == ai:
            return True
    if re.match(r"^用户称呼[其为自]?[己我]?为?[「\"'‘]?([你我他她它])[」\"'’]?$", c):
        return True
    # 「用户要求用一句话介绍自己」这类是**请求**，不是关于用户的事实（注入进画像纯噪音）
    if re.match(r"^用户(要求|让|叫|问|请|希望)", c):
        return True
    # 明显是错别字/残句（实测有「泥是它朋友」这种 —— "你是它朋友"被听错后写进画像）
    if "泥是" in c:
        return True
    return False


def memory_is_junk(rec):
    """记忆库一行要不要删。返回 (删?, 原因)。"""
    txt = str(rec.get("text") or "")
    if any(m in txt for m in TOOL_MARKS):
        return True, "工具原始返回"
    if any(m in txt for m in INSTR_MARKS):
        return True, "载体指令泄漏"
    if any(m in txt for m in NO_ANSWER_MARKS):
        return True, "「我不知道」被当成知识"
    if len(txt) > 400:
        return True, "过长（网页残留/整段抓取）"
    # 自我回灌：问句 + 它自己那次的长回答（>200 字）—— 检索出来只会让它复读自己
    m = re.search(r"用户：(.{1,80})", txt)
    if m and any(q in m.group(1) for q in ("吗", "?", "？")) and "小焦：" in txt:
        if len(txt) > 200:
            return True, "自我回灌（问句 + 长篇自答）"
    return False, ""


def _backup(path, bdir):
    if os.path.exists(path):
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))


def clean_memory(bdir):
    p = "logs/xiaojiao_memory_vec.jsonl"
    rows, keep, reasons, seen = [], [], {}, {}
    dup = 0
    for ln in open(p, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        rows.append((ln, r))

    # 先按内容判，再按 key 去重（同一个 key 只留最后一条 = 最新的）
    keep_keys = {}
    for ln, r in rows:
        bad, why = memory_is_junk(r)
        if bad:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        key = str(r.get("key") or "")
        keep_keys[key] = (ln, r)
    # 保持原顺序输出
    good = set(id(v[1]) for v in keep_keys.values())
    for ln, r in rows:
        bad, _ = memory_is_junk(r)
        if bad:
            continue
        if id(r) in good and not seen.get(id(r)):
            seen[id(r)] = True
            keep.append(ln + "\n")
        else:
            dup += 1
    print("【记忆库】原 %d 条 → 保留 %d 条 ｜ 删 %d 条 ｜ 同 key 去重 %d 条"
          % (len(rows), len(keep), sum(reasons.values()), dup))
    for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("     - %-28s %d" % (k, v))
    if APPLY:
        _backup(p, bdir)
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(keep)


def clean_profile(bdir):
    p = "logs/psyche/user_profile.jsonl"
    rows, keep, dropped, seen = [], [], [], set()
    for ln in open(p, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        rows.append((ln, r))
    out = []
    for ln, r in rows:
        c = str(r.get("content") or "").strip()
        if profile_is_junk(c) or c in seen:
            dropped.append(c[:60])
            continue
        seen.add(c)
        out.append(ln + "\n")
    print("\n【画像库】原 %d 条 → 保留 %d 条 ｜ 删 %d 条" % (len(rows), len(out), len(dropped)))
    for d in dropped:
        print("     - 删：%s" % d)
    # 再把"同一件事的多个说法"收成一条（比如 B站 那条被记了三遍、高危漏洞那条被记了四遍）
    kept_rows = [json.loads(x) for x in out]
    kept_rows, merged = collapse_same_fact(kept_rows)
    if merged:
        print("     - 同一件事换个说法（合并掉）%d 条 → 剩 %d 条" % (merged, len(kept_rows)))
        out = [json.dumps(r, ensure_ascii=False) + "\n" for r in kept_rows]
    if APPLY:
        _backup(p, bdir)
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(out)


def clean_knowledge(bdir):
    p = "self_learn/knowledge_vec.json"
    data = json.load(open(p, encoding="utf-8"))
    ents = data.get("entries") or []
    keep = [e for e in ents if not any(m in str(e.get("text", "")) for m in TOOL_MARKS + INSTR_MARKS)]
    print("\n【知识向量】原 %d 条 → 保留 %d 条 ｜ 删 %d 条" % (len(ents), len(keep), len(ents) - len(keep)))
    if APPLY and len(keep) != len(ents):
        _backup(p, bdir)
        data["entries"] = keep
        json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)

    p2 = "self_learn/tool_skills.txt"
    lines = open(p2, encoding="utf-8", errors="replace").read().splitlines(True)
    keep2 = [l for l in lines if not any(m in l for m in TOOL_MARKS + INSTR_MARKS)]
    print("【工具技能】原 %d 行 → 保留 %d 行 ｜ 删 %d 行" % (len(lines), len(keep2), len(lines) - len(keep2)))
    if APPLY and len(keep2) != len(lines):
        _backup(p2, bdir)
        open(p2, "w", encoding="utf-8").writelines(keep2)


def main():
    bdir = os.path.join("logs", "backup_clean_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    print("模式：%s" % ("真删（先整份备份）" if APPLY else "预览（不改任何文件）"))
    clean_memory(bdir)
    clean_profile(bdir)
    clean_knowledge(bdir)
    if APPLY:
        print("\n备份在：%s" % bdir)
    else:
        print("\n加 --apply 才会真删。")


if __name__ == "__main__":
    main()
