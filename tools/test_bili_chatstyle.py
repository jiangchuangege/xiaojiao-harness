# -*- coding: utf-8 -*-
"""私信/评论的"旁白压成聊天话"自测（离线，不连 B站、不起 MCP 服务）

【为什么要有这一条】2026-11-14 用户截图：B站 私信里账号回的是
  「我低头看手机，把那边的私信拉到最上头，手指点点那条信息，眼睛瞪得圆圆的……」
—— 旁白，不是聊天。收信人只会觉得它在自言自语讲故事。
判据在 `bili_duty/mcp_server.py` 的 `_chat_style()`，这里把它单独取出来跑样例。

用法：python tools/test_bili_chatstyle.py
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = open(os.path.join(ROOT, "bili_duty", "mcp_server.py"), encoding="utf-8").read()

# 只取 "_NARRATION_SIGNS = (" 到 "Tool 21" 之间那段（不 import 整个 MCP 服务）
_start = SRC.index("_NARRATION_SIGNS = (")
_end = SRC.index("# ========== Tool 21: 发私信 ==========")
_ns = {"re": re}
exec(SRC[_start:_end], _ns)
chat_style = _ns["_chat_style"]

PASS, FAIL = [], []


def ck(name, cond, info=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("✅" if cond else "❌", name,
                         ("  ← " + str(info)[:160]) if info else ""))


print("=" * 78)
print("【A】旁白 → 聊天话（用户截图原句）")
NARR1 = ("我低头看手机，把那边的私信拉到最上头，手指点点那条信息，眼睛瞪得圆圆的。"
         "嗯，我嘟囔了一句，声音不大，像是在对自己说，又像是在试探你，嘴角带着一点点无奈的笑。"
         "行吧，既然不藏，那本小姐就把话说明白，别在这里耍小聪明。")
out1 = chat_style(NARR1)
ck("旁白被删掉", "我低头" not in out1 and "手指点点" not in out1, out1)
ck("它真正想说的那句留下了", "别在这里耍小聪明" in out1, out1)
ck("压完明显变短", len(out1) < len(NARR1) * 0.6, "%d → %d" % (len(NARR1), len(out1)))

print("\n【B】引号里的台词优先提取")
NARR2 = ("我停了两秒，屏幕那边那盏灯忽明忽暗地闪了两下。你这问得突然，打字的手指停在了半空。"
         "我这边倒是先憋出一句：“本小姐是跟你说话，不是自言自语。”"
         "说完自己又补了一句，“不过重来也行，反正本小姐也不爱看。”")
out2 = chat_style(NARR2)
ck("只留引号里的台词", "本小姐是跟你说话" in out2 and "我停了两秒" not in out2, out2)

print("\n【C】正常聊天**一个字都不许动**")
for t in ["跟你说话啊。你一直只发一个问号一个额，本小姐还能跟谁聊，跟空气吗",
          "今天天气不错，要不要出去走走？",
          "本小姐正忙着，晚点再说。",
          "这个视频的字幕我看完了，讲的是三件事：一、二、三。"]:
    ck("原样返回：%s" % t[:16], chat_style(t) == t, chat_style(t))

print("\n【D】压不动就原样发（绝不吞消息）")
ONLY_NARRATION = "我低头看手机，手指点点屏幕，嘴角带着笑。"
out4 = chat_style(ONLY_NARRATION)
ck("全是旁白时也不会变成空串", bool(out4.strip()), repr(out4))

print("\n【E】strong 模式（评论场景）门槛更低")
MILD = "我点了点屏幕，这个视频讲得挺清楚。"
ck("私信模式（门槛高）不动它", chat_style(MILD) == MILD, chat_style(MILD))
ck("评论模式（strong）会压掉旁白", "我点了点屏幕" not in chat_style(MILD, strong=True),
   chat_style(MILD, strong=True))

print("\n" + "=" * 78)
print("  通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("  ❌ 未通过：%s" % "、".join(FAIL))
print("=" * 78)
sys.exit(0 if not FAIL else 1)
