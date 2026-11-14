# -*- coding: utf-8 -*-
"""把 agent_run 真正发给模型的那几条消息原样打出来（寒暄轮专用）。

用法：python tools/dump_prompt.py 你好
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xiaojiao_app as app  # noqa: E402

q = sys.argv[1] if len(sys.argv) > 1 else "你好"
real = app.llm_chat


def spy(messages, *a, **kw):
    print("\n===== 发给模型的 %d 条消息 =====" % len(messages))
    for i, m in enumerate(messages):
        c = str(m.get("content") or "")
        print("--- [%d] role=%s len=%d ---" % (i, m.get("role"), len(c)))
        print(c[:700])
    print("===== 结束 =====\n")
    return real(messages, *a, **kw)


app.llm_chat = spy
_real_tools = app.llm_chat_tools


def spy_tools(messages, *a, **kw):
    return spy(messages, *a, **kw)


app.llm_chat_tools = spy_tools
try:
    r = app.agent_run(q)
    print("答案:", (r[0] if isinstance(r, tuple) else r))
finally:
    app.llm_chat = real
    app.llm_chat_tools = _real_tools
