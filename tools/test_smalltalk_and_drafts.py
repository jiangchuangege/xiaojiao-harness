# -*- coding: utf-8 -*-
"""寒暄快速通道 / 草稿体 / 半截话 / 假工具调用 —— 离线自测（不联网、不调大脑）

【为什么要有这一组】2026-11-14 用户实测的三个症状，全是"载体把不该交的东西交出去了"：
  ① 说「你好」，它答一道 45°/135° 的选择题（深度模式第 2 遍裸 prompt 自己编的题）；
  ② 说「你好」，它答"你刚才说大概看半小时，然后去食堂"（记忆/历史被塞进寒暄轮）；
  ③ 问时间，它交解题草稿"1. 检查设备系统时间：当前时间是 [具体时间]…日期是 2023年10月12日"。
这一组把四条判据钉住：`_is_smalltalk`、`_looks_like_plan_draft`、`_looks_incomplete`、
`_strip_fake_tool_calls`。判据都是确定性的，改动它们必须让这里先亮红。

用法：python tools/test_smalltalk_and_drafts.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import xiaojiao_app as app  # noqa: E402

PASS, FAIL = [], []


def ck(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✅" if cond else "❌", name,
                         ("  ← " + str(info)[:160]) if info else ""))


INSTR = ("\n\n【回答格式，务必遵守】先用 <思考> 和 </思考> 包住你的推理过程"
         "（可以分点、可以自我怀疑、可以推翻自己），然后在 </思考> 之后写最终回答。不要复述题目。")

print("=" * 78)
print("【A】寒暄识别：这些话不该检索、不该带工具、不该带记忆")
SMALL = ["你好", "在吗？", "谢谢！", "晚安", "早上好呀", "哈喽~"]
for s in SMALL:
    ck("判为寒暄：%s" % s, app._is_smalltalk(s) is True, app._is_smalltalk(s))

BIG = ["帮我看下今天天气", "你好，帮我抓一下 http://example.com", "你是谁",
       "2的10次方是多少？", "今天几号？", "给我讲讲唐朝历史", "翻译一下 hello"]
for s in BIG:
    ck("不走寒暄通道：%s" % s, app._is_smalltalk(s) is False, app._is_smalltalk(s))

ck("带载体指令尾巴的「你好」仍判为寒暄（尾巴要先剥掉）",
   app._is_smalltalk("你好" + INSTR) is True, app._is_smalltalk("你好" + INSTR))

print("\n【B】解题草稿不许当答案（用户截图里那条）")
DRAFT = ("（先确认当前时区）\n"
         "1. 检查设备系统时间：当前时间是 [具体时间]。\n"
         "2. 确认时区：位于 [时区名称]。\n"
         "3. 计算本地时间：系统时间 + 时区偏移量 = [当前准确时间]。\n"
         "4. 查阅日历：目前准确时间 对应的日期是 2023年10月12日。")
ck("草稿体认出来了", app._looks_like_plan_draft(DRAFT) is True)
ck("正常回答不是草稿", app._looks_like_plan_draft("现在是 2026-11-14 16:40。") is False)
ck("正常的分步回答（菜谱）不算草稿",
   app._looks_like_plan_draft("1. 先烧水\n2. 再放茶叶\n这两步就够了。") is False)

print("\n【C】半截话不许进候选池")
for t, why in [("2的10次方可以这样计算：", "冒号收尾"),
               ("答案是（未完", "括号不闭合"),
               ("```py\nprint(1)", "代码围栏不闭合")]:
    ck("判为半截：%s" % why, app._looks_incomplete(t) is True, t)
for t in ["2的10次方是 1024。", "好，说完了。", "1+1=2"]:
    ck("完整句不算半截：%s" % t, app._looks_incomplete(t) is False, t)

print("\n【D】模型自演的假工具调用要砍掉")
FAKE = '哼，你好啊。有什么事？  \n（调用工具）  \nweb_search("harnees")'
ck("假调用被砍掉", "web_search" not in app._strip_fake_tool_calls(FAKE),
   app._strip_fake_tool_calls(FAKE))
ck("寒暄正文留下来", "有什么事" in app._strip_fake_tool_calls(FAKE))
CODE = "这段代码用 requests.get(url) 就能拿到页面。"
ck("正常提到函数名**不许**误伤", app._strip_fake_tool_calls(CODE) == CODE, CODE)

print("\n【E】回答开头那句「自我指令」要砍掉（用户：回答和思考掺到一块了我咋看不懂）")
SI = "嗯，回一声，别太正式，带点调侃。 我知道你忙完这一阵子肯定累，但你还是记得来打招呼。"
ck("自我指令被砍掉", app._strip_self_instruction(SI).startswith("我知道你忙完"),
   app._strip_self_instruction(SI))
ck("正常关心句**不许**误伤",
   app._strip_self_instruction("别太累了，早点休息。") == "别太累了，早点休息。")
ck("普通寒暄不许误伤",
   app._strip_self_instruction("嗯，你好呀！今天咋样？") == "嗯，你好呀！今天咋样？")
ck("两句自我指令都砍（语气/简短）",
   app._strip_self_instruction("语气温柔点，简短一点。 我在呢，你说。") == "我在呢，你说。",
   app._strip_self_instruction("语气温柔点，简短一点。 我在呢，你说。"))

print("\n" + "=" * 78)
print("  通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("  ❌ 未通过：%s" % "、".join(FAIL))
print("=" * 78)
sys.exit(0 if not FAIL else 1)
