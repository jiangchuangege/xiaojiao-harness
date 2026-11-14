# -*- coding: utf-8 -*-
"""身份设定自测：`plugins/identity.py` + 载体层的身份指令识别（离线，不调大脑）

【要钉住的三件事】
  ① 用户说"以后叫我老板""我叫王强""我在杭州"→ 载体**直接**记下（不指望模型调工具）；
  ② 不像身份指令的话（"你是谁""我在想办法""你叫我什么"）**不许**乱记；
  ③ 记完之后注入给模型的那一句必须带上称呼，且**没设置时**要明确"别瞎猜名字"
     （用户实测：一句「你好」，它答"哟，这不是我熟悉的老张嘛"）。

用法：python tools/test_identity.py
"""
import io
import json
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def ck(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✅" if cond else "❌", name,
                         ("  ← " + str(info)[:150]) if info else ""))


print("=" * 78)
print("【A】身份指令识别（载体直接记，不问模型）")
import importlib.util as ilu

_spec = ilu.spec_from_file_location("identity", os.path.join(ROOT, "plugins", "identity.py"))
ID = ilu.module_from_spec(_spec)
_spec.loader.exec_module(ID)

import xiaojiao_app as app  # noqa: E402

CASES = [
    ("以后叫我老板", "call_as", "老板"),
    ("叫我老李就行", "call_as", "老李"),
    ("我叫王强", "user_name", "王强"),
    ("我在杭州", "city", "杭州"),
]
for text, field, val in CASES:
    got = app._identity_command(text)
    ck("认出来：%s" % text, bool(got) and got[0] == field and got[1] == val, got)

NOT = ["你是谁", "你叫什么名字", "你叫我什么", "我在想办法", "我在写代码",
       "帮我查下天气", "今天几号？", "你好"]
for text in NOT:
    ck("不乱认：%s" % text, app._identity_command(text) is None, app._identity_command(text))

print("\n【B】插件存储与提示")
_tmp = tempfile.mkdtemp()
_old = ID._ID_FILE
ID._ID_FILE = os.path.join(_tmp, "identity.json")
try:
    p = ID.IdentityPlugin()
    names = [d["name"] for d in p.get_tool_descriptions()]
    ck("三个工具都在", names == ["identity_get", "identity_set", "identity_clear"], names)

    empty = ID.hint()
    ck("没设置时提示里写明「别瞎猜名字」", "不要瞎猜" in empty, empty)
    ck("没设置时提示不包含任何具体称呼", "老板" not in empty and "老张" not in empty, empty)

    p.execute("identity_set", {"field": "call_as", "value": "老板"})
    ck("设置写进了文件", json.load(open(ID._ID_FILE, encoding="utf-8"))["call_as"] == "老板")
    hint = ID.hint()
    ck("设置后提示带上称呼", "老板" in hint, hint)
    ck("设置后提示要求「照这个称呼」", "照这个称呼" in hint, hint)

    bad = p.execute("identity_set", {"field": "不存在的字段", "value": "x"})
    ck("非法字段被拒", "没有" in bad and "可选的" in bad, bad)

    p.execute("identity_clear", {"field": "call_as"})
    ck("清掉后回到未设置", json.load(open(ID._ID_FILE, encoding="utf-8"))["call_as"] == "")
    ck("清掉后提示又能提醒「别瞎猜」", "不要瞎猜" in ID.hint())
finally:
    ID._ID_FILE = _old

print("\n【C】接线：载体注入用的就是插件那一句")
ck("app._identity_hint() 拿到的就是插件 hint()",
   app._identity_hint() == ID.hint() or "称呼" in app._identity_hint(),
   app._identity_hint()[:90])

print("\n" + "=" * 78)
print("  通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("  ❌ 未通过：%s" % "、".join(FAIL))
print("=" * 78)
sys.exit(0 if not FAIL else 1)
