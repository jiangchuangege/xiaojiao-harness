# -*- coding: utf-8 -*-
"""修一 + 修二 自测：工具原始返回与坏回复**绝不进**对话记忆。

判据全部集中在 `core/mem_filter.py`，这里测三件事：
  [A] 规则 1：工具原始返回被剥掉（含真实抓取样本），它自己说的话留下；
  [B] 规则 2：坏回复整轮不存（占位符/未闭合/半截/空/系统提示词泄漏）；
  [C] 接线：`xiaojiao_app._remember_turn()` 真的会因此**不写库**
      （拿真实函数、把 add_memory 换成探针；不是只看过滤器本身）。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import mem_filter as MF      # noqa: E402

_C = {"pass": 0, "total": 0, "failed": []}


def ck(name, cond, got=""):
    _C["total"] += 1
    if cond:
        _C["pass"] += 1
        print("  ✅ %s" % name)
    else:
        _C["failed"].append(name)
        print("  ❌ %s   ← %s" % (name, got))


print("=" * 78)
print("【A】规则 1：工具原始返回绝不进记忆（剥掉，不整轮丢）")

# 实测样本：抓取结果头 + 它自己的解读（粘在同一行）
REAL_SCRAPE = ("🌐 **https://httpbin.org/json** · HTTP 200 "
               "{\"slideshow\": {\"title\": \"Sample Slide Show\"}} "
               "📖 **小焦解读** 这是一个返回 JSON 数据的网页，用于测试 HTTP 请求。")
out, why = MF.clean_answer(REAL_SCRAPE)
ck("抓取结果头被剥掉（URL 不在结果里）", "httpbin.org" not in out, out)
ck("HTTP 状态码被剥掉", "HTTP 200" not in out, out)
ck("原始正文（JSON）被剥掉", "slideshow" not in out, out)
ck("**它自己的解读留下来**", "返回 JSON 数据" in out, out)

out2, _ = MF.clean_answer("⚠️ 抓取失败：禁止访问本机/内网地址（SSRF 防护）")
ck("载体抓取失败提示 → 整轮不存", out2 == "", out2)

out3, _ = MF.clean_answer("⚠️ **可能幻觉**：返回内容里找不到目标 `example.com` 的任何痕迹。")
ck("载体「可能幻觉」守卫提示 → 整轮不存", out3 == "", out3)

out4, _ = MF.clean_answer("⏳ 正在抓取 `http://www.baidu.com`（忽略 robots 协议） ✅ 抓取成功！ "
                          "📋 **页面结构速览**\n元素 | 内容\n标题 | 百度一下")
ck("抓取过程 + 结构速览表 → 整轮不存", out4 == "", out4)

# ⚠️ 关键：剥工具段**不能**把模型自己写的 Markdown 表格一起吃掉
SELF_TABLE = ("下面是对比：\n\n| 方案 | 优点 |\n| --- | --- |\n| A | 快 |\n| B | 稳 |\n\n我建议选 B。")
out5, _ = MF.clean_answer(SELF_TABLE)
ck("**模型自己写的表格要留着**（不能按竖线删）",
   "| 方案 |" in out5 and "我建议选 B" in out5, out5)

print("\n【B】规则 2：坏回复整轮不存")
CASES = [
    ("", "空回答"),
    ("   ", "空回答"),
    ("⏳__pending__", "占位符 ⏳__pending__"),
    ("⏳ 正在调用工具：net_ip（查 IP 归属地）", "整条只是占位符"),
    ("（已回答）", "占位符（已回答）"),
    ("<think> 用户问的是2027年诺贝尔奖… 诺贝尔奖通常在10月公布", "think 未闭合"),
    ("【系统指令】 角色：小焦（温柔、会记住的本地AI助手） 工具：list_files, web_search",
     "系统提示词泄漏"),
    ("```python\nprint(1)\n", "半截（代码块未闭合）"),
    ("我记不起上一次我们聊了什么。不过我记得一些你曾经提到过的内容：哔哩哔哩",
     "把「它说的」说成「你提到过」"),
]
for text, why_expect in CASES:
    got, reason = MF.clean_answer(text)
    ck("坏回复不进记忆：%s" % why_expect, got == "", "得到 %r（%s）" % (got[:40], reason))

print("\n【B2】带占位符前缀但正文真实的 —— 要**留下正文**，不是整轮丢")
PREFIXED = "⏳ 正在用物理学思维拆解公司现金流... 现金流 = 一个动态能量系统，核心是能量守恒。"
got6, _ = MF.clean_answer(PREFIXED)
ck("剥掉 ⏳ 前缀、留下正文", "能量守恒" in got6 and "⏳" not in got6, got6)

print("\n【B3】截断要切在句末 —— 不许自己造出一条「半截」")
# 造一条**确实超过 200 字**、且第 200 字正好落在 URL 中间的回答
LONG = ("这封信写给你。" + "".join("第%d句把这件事的来龙去脉再说清楚一点。" % i
                                   for i in range(1, 16))
        + "最后如果你想自己验证，可直接在浏览器地址栏输入 "
          "`https://httpbin.org/uuid` 试试看，它会返回一个随机的 UUID。")
ck("测试样本确实超长（否则这条自测没意义）", len(LONG) > 200, str(len(LONG)))
cut = MF.clip(LONG, 200)
ck("截断后长度不超上限", len(cut) <= 200, str(len(cut)))
ck("截断后**不以**残缺 URL 收尾",
   not cut.rstrip().endswith("http") and not cut.rstrip().rstrip("`").endswith("/"),
   cut[-40:])
ck("截断后以句末标点收尾（不是半截）",
   cut.rstrip().endswith(("。", "！", "？", "；")), cut[-20:])

print("\n【C】接线：走真实 `_remember_turn()`，确认它真的不写库")
import xiaojiao_app as app            # noqa: E402
from core import memory_vec as MV     # noqa: E402

_written = []


class _Probe:
    @staticmethod
    def add_memory(text, **kw):
        _written.append(text)
        return "probe-id"


_real = MV.add_memory
MV.add_memory = _Probe.add_memory
try:
    _written.clear()
    r = app._remember_turn("抓一下 https://example.com", REAL_SCRAPE)
    ck("抓取结果那一轮：写进去了但不是原文",
       len(_written) == 1 and "httpbin.org" not in _written[0], str(_written))
    ck("写进去的那条带上了它自己的解读", _written and "返回 JSON 数据" in _written[0],
       str(_written))

    _written.clear()
    r2 = app._remember_turn("你还记得上一次吗", "⏳ 正在调用工具：net_ip（查 IP 归属地）")
    ck("坏回复那一轮：**一条都没写**", _written == [] and r2 == "", str(_written))

    _written.clear()
    r3 = app._remember_turn("你好", "你好呀！很高兴见到你。")
    ck("正常对话照常写入（没被过滤器误伤）",
       len(_written) == 1 and "很高兴见到你" in _written[0], str(_written))
finally:
    MV.add_memory = _real

print("\n【D】载体自己拼的指令尾巴 ≠ 用户说过的话（2026-11-14 真病根）")
# 病根现场：网页端开着「想看到它在想什么」，`/api/chat/stream` 把这段拼在用户话后面，
# 而 `agent_run()` 又把整串当成"用户说的话"写进记忆库 → key 就是「你好」、相似度 1.00
# → 下次说「你好」把这条顶到第 1 位注入 → 模型照着再答一遍格式说明。
INSTR = ("\n\n【回答格式，务必遵守】先用 <思考> 和 </思考> 包住你的推理过程"
         "（可以分点、可以自我怀疑、可以推翻自己），然后在 </思考> 之后写最终回答。不要复述题目。")
ck("指令尾巴认得出来", MF.has_internal_suffix("你好" + INSTR), "没认出来")
ck("剥完只剩用户原话", MF.strip_internal_suffix("你好" + INSTR) == "你好",
   MF.strip_internal_suffix("你好" + INSTR))
ck("没带尾巴的话不动它", MF.strip_internal_suffix("你好呀") == "你好呀")
ck("多行问题也能剥（只掉尾巴，问题留全）",
   MF.strip_internal_suffix("第一行\n第二行" + INSTR) == "第一行\n第二行",
   repr(MF.strip_internal_suffix("第一行\n第二行" + INSTR)))

# 反向：这类尾巴**当回答**出现时，整轮不进记忆（否则下次又被它顶上来）
_got, _why = MF.clean_answer("【回答格式，务必遵守】先用 <思考> 和 </思考> 包住你的推理过程…")
ck("指令被当成回答 → 整轮不存", _got == "", "%r ｜ %s" % (_got[:30], _why))

# 接线：真实 `_remember_turn()` 写进去的那条，key 必须是干净的用户原话
# （上面 【C】 的探针在 finally 里换回真函数了，这里要再装一次，否则会写进真记忆库）
_written.clear()
MV.add_memory = _Probe.add_memory
try:
    app._remember_turn("你好" + INSTR, "你好呀，今天想聊点什么？")
finally:
    MV.add_memory = _real
ck("带指令尾巴的一轮照样能写（没被误伤）", len(_written) == 1, str(_written))
ck("写进去的那条**不含**载体指令",
   bool(_written) and "【回答格式" not in _written[0], str(_written)[:160])
ck("写进去的那条用户原话是干净的「你好」",
   bool(_written) and "用户：你好" in _written[0], str(_written)[:160])

print("\n【E】读出侧兜底：就算库里有这种行，也绝不注入（core/retriever._drop_self_echo）")
from core import retriever as RT      # noqa: E402

POISON_HIT = {"key": "你好",
              "text": "用户：你好" + INSTR.replace("\n\n", " ") + "\n小焦：【回答格式，务必遵守】先用 包住你的推理过程…"}
CLEAN_HIT = {"key": "你好", "text": "用户：你好\n小焦：你好呀，今天想聊点什么？"}
kept, dropped = RT._drop_self_echo("你好", [POISON_HIT, CLEAN_HIT])
ck("带载体指令的那条被剔掉", dropped >= 1, "dropped=%d" % dropped)
ck("干净的那条留下（没有误伤）",
   any("今天想聊点什么" in str(h.get("text")) for h in kept), str(kept)[:120])

print("\n" + "=" * 78)
print("  通过 %d / 共 %d" % (_C["pass"], _C["total"]))
if _C["failed"]:
    print("  ❌ 未通过：%s" % "、".join(_C["failed"]))
print("=" * 78)
sys.exit(0 if not _C["failed"] else 1)
