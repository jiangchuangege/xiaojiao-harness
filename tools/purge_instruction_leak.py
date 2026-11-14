# -*- coding: utf-8 -*-
"""清理"载体指令冒充用户原话"的污染行（2026-11-14）

病根：网页端开着"想看到它在想什么"时，`/api/chat/stream` 把
`【回答格式，务必遵守】先用 <思考> 和 </思考> …` 拼在用户那句话后面交给 `agent_run()`，
而 `agent_run()` 又把这整串当成"用户说的话"写进了记忆库。于是库里出现：

    用户：你好 【回答格式，务必遵守】先用 <思考> … 小焦：【回答格式，务必遵守】…

它的 key 就是「你好」，向量相似度 1.00 —— 下次再说「你好」，检索把这条顶到第 1 位注入，
模型就照着自己上次那段"格式说明"再答一遍。用户看到的：说「你好」，它答数学题/格式说明。

本脚本做三件事（**只删带这段指令的行，别的一概不动**）：
  1. `logs/xiaojiao_memory_vec.jsonl`  删掉正文含这段指令的记忆行（检索源）
  2. `self_learn/knowledge_vec.json` / `self_learn/tool_skills.txt`  删掉同源污染
  3. `xiaojiao_sessions.json`          把会话里那条被污染的助手消息删掉

用法：
    python tools/purge_instruction_leak.py            # 预览（只报数，不改）
    python tools/purge_instruction_leak.py --apply    # 真删（先备份到 logs/backup_<时间>/）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "【回答格式，务必遵守】"
APPLY = "--apply" in sys.argv


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(path, bdir):
    if not os.path.exists(path):
        return
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, os.path.basename(path))
    shutil.copy2(path, dst)


def purge_jsonl(path, bdir):
    """按行过滤：正文含 MARK 的行丢掉。返回 (总行, 删除行)。"""
    if not os.path.exists(path):
        return 0, 0
    keep, drop = [], 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if MARK in ln:
                drop += 1
                continue
            keep.append(ln)
    if APPLY and drop:
        _backup(path, bdir)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    return len(keep) + drop, drop


def purge_txt(path, bdir):
    if not os.path.exists(path):
        return 0, 0
    lines = open(path, encoding="utf-8", errors="ignore").read().splitlines(True)
    keep = [ln for ln in lines if MARK not in ln]
    drop = len(lines) - len(keep)
    if APPLY and drop:
        _backup(path, bdir)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    return len(lines), drop


def purge_knowledge_vec(path, bdir):
    """self_learn/knowledge_vec.json：{"entries":[{text:...}]}"""
    if not os.path.exists(path):
        return 0, 0
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("  ! 读不了 %s：%s" % (path, e))
        return 0, 0
    ents = data.get("entries") or []
    keep = [e for e in ents if MARK not in str(e.get("text", ""))]
    drop = len(ents) - len(keep)
    if APPLY and drop:
        _backup(path, bdir)
        data["entries"] = keep
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return len(ents), drop


def purge_sessions(path, bdir):
    """会话文件：删掉正文含 MARK 的消息（保留该会话）。"""
    if not os.path.exists(path):
        return 0, 0
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("  ! 读不了 %s：%s" % (path, e))
        return 0, 0
    total = drop = 0
    for s in data.get("sessions", []) or []:
        msgs = s.get("messages") or []
        total += len(msgs)
        keep = [m for m in msgs if MARK not in str(m.get("content", ""))]
        d = len(msgs) - len(keep)
        if d:
            drop += d
            s["messages"] = keep
    if APPLY and drop:
        _backup(path, bdir)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return total, drop


def main():
    bdir = os.path.join(ROOT, "logs", "backup_purge_" + _stamp())
    jobs = [
        ("记忆库（检索源）", os.path.join(ROOT, "logs", "xiaojiao_memory_vec.jsonl"), purge_jsonl),
        ("知识向量", os.path.join(ROOT, "self_learn", "knowledge_vec.json"), purge_knowledge_vec),
        ("工具技能", os.path.join(ROOT, "self_learn", "tool_skills.txt"), purge_txt),
        ("会话记录", os.path.join(ROOT, "xiaojiao_sessions.json"), purge_sessions),
    ]
    print("模式：%s" % ("真删（会先备份）" if APPLY else "预览（不改任何文件）"))
    total_all = 0
    for label, path, fn in jobs:
        n, d = fn(path, bdir)
        total_all += d
        print("  %-14s %6d 行 → 删除 %d 行  %s" % (label, n, d, "" if n else "(文件不存在)"))
    print("合计删除 %d 行" % total_all)
    if APPLY and total_all:
        print("备份在：%s" % bdir)
    elif not APPLY and total_all:
        print("加 --apply 才会真删。")


if __name__ == "__main__":
    main()
