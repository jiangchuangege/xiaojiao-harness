# -*- coding: utf-8 -*-
"""活体验收（网页那条路）：走 /api/chat/stream + think=true（网页默认就是这个组合）。

这条路上 `agent_run` 收到的是「你的话 + 【回答格式，务必遵守】…」——
2026-11-14 的病根就是这串尾巴被当成"用户说的话"存进了记忆库。

用法：python tools/probe_stream.py ["你好"]
"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
msg = sys.argv[1] if len(sys.argv) > 1 else "你好"
quality = int(sys.argv[2]) if len(sys.argv) > 2 else 3      # 网页"深度模式"默认就是 3
body = json.dumps({"message": msg, "think": True, "quality": quality},
                  ensure_ascii=False).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:5000/api/chat/stream", data=body,
                             headers={"Content-Type": "application/json"}, method="POST")
text, err, meta = [], "", {}
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue                      # SSE 注释行（`: 排队中 第 N 位`）直接跳过
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue                      # 不是 JSON 的杂行：跳过，不当中断
            if obj.get("type") in ("chunk", "delta"):
                text.append(obj.get("text") or "")
            elif obj.get("type") == "meta":
                meta = obj
            elif obj.get("type") == "error":
                err = obj.get("error") or ""
            elif obj.get("type") == "done":
                # `done.answer` 是载体认定的最终回答（权威），有就用它
                if obj.get("answer"):
                    text = [obj["answer"]]
except Exception as e:
    print("请求失败：%s" % e)
    sys.exit(2)

ans = "".join(text).strip()
print("问：%s" % msg)
print("答：%s" % ans[:700])
print("\n判定：")
bad = ("【回答格式" in ans or "务必遵守" in ans or "3x + 4" in ans or "解答步骤" in ans
       or "包住你的推理过程" in ans)
print("  %s 不含载体指令/无关回声" % ("✅" if not bad else "❌"))
print("  %s 不是空回答" % ("✅" if len(ans) >= 2 else "❌"))
if err:
    print("  ⚠️ 服务端报错：%s" % err[:200])
sys.exit(0 if (not bad and len(ans) >= 2 and not err) else 1)
