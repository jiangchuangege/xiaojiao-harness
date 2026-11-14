# -*- coding: utf-8 -*-
"""清会话：删掉垃圾回复 / 卡住的占位符 / 我测试时造出来的探测会话（默认预览，--apply 才写）

【为什么清】`xiaojiao_sessions.json` 里 537 个会话、2228 条消息，实测含：
  · 「大脑没有应答，这一问没答上…」47 条 —— 那几轮根本没答，留着只会在翻历史时制造错觉
  · `⏳__pending__` 10 条 —— **孤儿占位符**，界面会把它渲染成"正在回答…"卡在那儿
  · 【元认知】/【回答格式，务必遵守】各 1 条 —— 思考漏出 / 载体指令复读
  · 一批「你好」「在吗」「以后叫我老板」这种**探测会话** —— 是我这两天调试验证时跑出来的，
    不是用户真的聊天记录，堆在会话列表最上面只会更难用

规则（确定性）：
  1. 助手消息含坏标记 → 删（用户那条保留：他自己说的话不能丢）
  2. 助手消息是纯旁白（"我低头看手机…"这种小说笔法）→ 删
  3. 一个会话里**用户说的话全在探测清单里** → 整条会话删（保留 `current` 当前会话）
  4. 删完一条消息都不剩的会话 → 删；只有用户没有回复的会话 → 保留（那是他没等到回答）
删之前整份备份到 `logs/backup_sessions_<时间>/`。

用法：
    python tools/clean_sessions.py            # 预览
    python tools/clean_sessions.py --apply    # 真删
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
PATH = "xiaojiao_sessions.json"

BAD_MARKS = ("大脑没有应答", "⏳__pending__", "（已回答）", "【回答格式，务必遵守】",
             "【系统指令】", "🤖 大脑没有应答", "模型配置正确、端口可达")
NARRATION = ("我低头", "我抬头", "我停了两秒", "手指点点", "眼睛瞪", "屏幕那边",
             "我嘟囔", "屏幕那头", "我点了点", "嘴角带")
# 我这两天验证时问过的话（**不是**用户的真实对话）→ 整条会话算"探测会话"
PROBE_LINES = {
    "你好", "在吗", "在吗？", "谢谢", "谢谢！", "晚安", "早上好", "早上好呀", "哈喽", "哈喽~",
    "你是谁", "1+1等于几？", "1+1等于几", "2的10次方是多少？", "今天几号？", "现在几点了？",
    "帮我看看现在菏泽的天气", "今天时间你说的不对吧", "以后叫我老板", "我叫王强",
    "你叫我什么？", "我喜欢什么？", "以前叫我老板",
}


def is_bad_reply(text):
    t = str(text or "").strip()
    if not t:
        return True, "空"
    for m in BAD_MARKS:
        if m in t:
            return True, "坏标记（%s）" % m
    hits = sum(1 for w in NARRATION if w in t)
    if hits >= 2:
        return True, "纯旁白（小说笔法）"
    return False, ""


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    cur = str(data.get("current") or "")
    sessions = data.get("sessions") or []
    bdir = os.path.join("logs", "backup_sessions_" + datetime.now().strftime("%Y%m%d_%H%M%S"))

    msg_total = sum(len(s.get("messages") or []) for s in sessions)
    out_sessions, out_msgs = [], 0
    dropped_msgs = 0
    dropped_sess = 0
    reasons = {}

    for s in sessions:
        msgs = s.get("messages") or []
        sid = str(s.get("id") or "")
        users = [str(m.get("content") or "").strip() for m in msgs if m.get("role") == "用户"]
        # ③ 探测会话：他说过的每一句都在我的探测清单里
        if sid != cur and users and all(u in PROBE_LINES for u in users):
            dropped_sess += 1
            reasons["探测会话（我调试时造的）"] = reasons.get("探测会话（我调试时造的）", 0) + 1
            dropped_msgs += len(msgs)
            continue
        kept = []
        for m in msgs:
            if m.get("role") == "小焦":
                bad, why = is_bad_reply(m.get("content"))
                if bad:
                    dropped_msgs += 1
                    reasons[why] = reasons.get(why, 0) + 1
                    continue
            kept.append(m)
        # ④ 删完空了 → 整条会话删（除了当前会话）
        if not kept and sid != cur:
            dropped_sess += 1
            reasons["删完没有内容"] = reasons.get("删完没有内容", 0) + 1
            continue
        s = dict(s)
        s["messages"] = kept
        out_sessions.append(s)
        out_msgs += len(kept)

    print("模式：%s" % ("真删（先整份备份）" if APPLY else "预览（不改任何文件）"))
    print("会话：%d → %d（删 %d 条）" % (len(sessions), len(out_sessions), dropped_sess))
    print("消息：%d → %d（删 %d 条）" % (msg_total, out_msgs, dropped_msgs))
    if reasons:
        print("删除原因：")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print("   - %-26s %d" % (k, v))
    print("当前会话（%s）保持不动：%s" % (cur, "是" if cur else "无"))

    if APPLY:
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(PATH, os.path.join(bdir, os.path.basename(PATH)))
        data["sessions"] = out_sessions
        tmp = PATH + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, PATH)
        print("备份在：%s" % bdir)
    else:
        print("\n加 --apply 才会真删。")


if __name__ == "__main__":
    main()
