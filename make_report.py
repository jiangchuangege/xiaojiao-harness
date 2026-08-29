# -*- coding: utf-8 -*-
"""🐳 小焦 · 《小脑成长报告》生成器
用法：python make_report.py  → 生成 成长报告.html 并打开（可分享/截图发朋友圈）
"""
import os, json, datetime, webbrowser, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "logs", "chat_history.jsonl")
FB = os.path.join(ROOT, "logs", "feedback.jsonl")
KNOW = os.path.join(ROOT, "self_learn", "little_brain_knowledge.txt")


def count(fp):
    try:
        return sum(1 for _ in open(fp, encoding="utf-8"))
    except Exception:
        return 0


def read_lines(fp):
    try:
        return [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]
    except Exception:
        return []


def main():
    logs = read_lines(LOG)
    fbs = read_lines(FB)
    good = sum(1 for x in fbs if x.get("feedback") in ("good", "👍") or str(x.get("feedback", "")).strip("星") in ("4", "5"))
    bad = sum(1 for x in fbs if x.get("feedback") in ("bad", "👎"))
    corr = sum(1 for x in fbs if x.get("corrected_reply"))
    know = count(KNOW)
    today = datetime.date.today().isoformat()
    # 本周新增知识
    week_ago = datetime.datetime.now() - datetime.timedelta(days=7)
    wk = 0
    if os.path.exists(KNOW):
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(KNOW))
            wk = know if mtime >= week_ago else 0
        except Exception:
            wk = know
    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>小焦成长报告</title>
<style>
 body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:linear-gradient(160deg,#0e1116,#141a2e);color:#e8ebf3;display:flex;justify-content:center;padding:40px 16px}
 .card{max-width:520px;width:100%;background:#151a26;border:1px solid #2a3140;border-radius:20px;padding:28px;box-shadow:0 30px 80px #0009}
 h1{font-size:22px;margin:0 0 4px}.sub{color:#8b93a3;font-size:13px;margin-bottom:22px}
 .big{font-size:44px;font-weight:800;background:linear-gradient(135deg,#5b5ff5,#7c5cf0);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
 .row{display:flex;gap:12px;margin-top:18px}
 .cell{flex:1;background:#0e1116;border:1px solid #232a3e;border-radius:12px;padding:14px;text-align:center}
 .cell .n{font-size:24px;font-weight:700}.cell .t{font-size:12px;color:#8b93a3;margin-top:4px}
 .foot{margin-top:26px;color:#6e7681;font-size:12px;text-align:center;line-height:1.7}
 .bar{height:10px;background:#0e1116;border-radius:6px;overflow:hidden;margin-top:8px}
 .bar i{display:block;height:100%;background:linear-gradient(90deg,#5b5ff5,#7c5cf0);border-radius:6px}
</style></head><body><div class="card">
<h1>🐳 小焦 · 小脑成长报告</h1>
<div class="sub">%s · 小脑跟着大脑学 · 别人靠算力，小脑靠文本</div>
<div class="big">%s</div>
<div style="color:#8b93a3;font-size:13px">小脑知识库累计（条）—— 越长越强</div>
<div class="bar"><i style="width:%s%%"></i></div>
<div class="row">
 <div class="cell"><div class="n">%s</div><div class="t">本周新增</div></div>
 <div class="cell"><div class="n">%s</div><div class="t">交互记录</div></div>
 <div class="cell"><div class="n">%s</div><div class="t">👍 点赞</div></div>
 <div class="cell"><div class="n">%s</div><div class="t">被更正</div></div>
</div>
<div class="foot">它不会很多话，但会慢慢成为只属于你的那一只 🐳<br>xiaojiao-harness · 持续学习 · DSH 插件生态</div>
</div></body></html>""" % (today, know, min(100, int(know / 5)), wk, len(logs), good, corr)
    out = os.path.join(ROOT, "成长报告.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ 已生成:", out)
    webbrowser.open("file://" + out.replace("\\", "/"))


if __name__ == "__main__":
    main()
