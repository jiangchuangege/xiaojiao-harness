# -*- coding: utf-8 -*-
"""活体验收：直接问「你好」，看它答的是不是人话（不再输出格式说明/数学题）。

用法：python tools/probe_hello.py ["你好"]
"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
msg = sys.argv[1] if len(sys.argv) > 1 else "你好"
req = urllib.request.Request(
    "http://127.0.0.1:5000/api/chat",
    data=json.dumps({"message": msg}, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
except Exception as e:
    print("请求失败：%s" % e)
    sys.exit(2)

ans = str(d.get("answer") or d.get("reply") or d)
print("问：%s" % msg)
print("答：%s" % ans[:600])
print("\n判定：")
bad = ("【回答格式" in ans or "务必遵守" in ans or "3x + 4" in ans
       or "解答步骤" in ans or "包住你的推理过程" in ans)
print("  %s 不含载体指令/无关题目回声" % ("✅" if not bad else "❌"))
print("  %s 有实体内容（≥2 字）" % ("✅" if len(ans.strip()) >= 2 else "❌"))
sys.exit(0 if not bad else 1)
