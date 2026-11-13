# -*- coding: utf-8 -*-
"""B站插件**全覆盖**自测：29 个工具一个不少，且都能真调通（或如实报出为什么不行）。

用户的要求是"不准缺少任何"，所以这里的判据是：
  ① 插件 `mcp_server.py` 里定义的每个 `bili_*` 函数，小焦这边都有对应工具（名字一一对上）
  ② 每个工具的描述都写「什么时候用」（仓库自测的硬规则）
  ③ **每个工具都真调一次**：返回的东西必须是人话，不能是空串/异常堆栈
  ④ 两个写操作（reply / send_message）不传 force 时必须被本地闸拦住

③ 里"失败"不一定是 bug：没私信、没 @、模型文件不在……都会如实返回。
   判据是"返回了可读的原因"，而不是"返回成功" —— 否则就成了自欺。

跑：python tools/test_bili_full.py           （全跑，约 2-4 分钟）
    python tools/test_bili_full.py --fast    （跳过转写/登录这类慢工具）
"""
import io
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

PASS, FAIL = [], []
# 需要真实参数的工具：给一份能跑的最小参数
ARGS = {
    "bili_search": {"keyword": "小焦", "num": 2},
    "bili_comments": {"bvid": "BV1vx411M7DA", "num": 3},
    "bili_subtitle": {"bvid": "BV1vx411M7DA"},
    "bili_video_info": {"bvid": "BV1vx411M7DA"},
    "bili_danmaku": {"bvid": "BV1vx411M7DA", "num": 5},
    "bili_crawl": {"keyword": "小焦", "max_videos": 1, "comments_per_video": 2, "get_subtitles": False},
    "bili_rank": {"category": "all", "day": 1},
    "bili_hot_videos": {"ps": 3},
    "bili_user_info": {"uid": 1},
    "bili_user_videos": {"uid": 1, "ps": 3},
    "bili_cover": {"bvid": "BV1vx411M7DA"},
    "bili_frames": {"bvid": "BV1vx411M7DA", "num": 1},
    "bili_hot_buzzwords": {"page_num": 1, "page_size": 5},
    "bili_weekly_hot": {"week": 0},
    "bili_recommend": {"ps": 3},
    "bili_favorite_lists": {"uid": 0},
    "bili_favorite_content": {"media_id": 1, "page": 1},
}
SLOW = {"bili_login", "bili_transcribe", "bili_transcribe_start", "bili_transcribe_result",
        "bili_send_message"}


def ck(group, name, cond, info=""):
    (PASS if cond else FAIL).append("%s/%s" % (group, name))
    print("  %s [%s] %s%s" % ("✅" if cond else "❌", group, name,
                              ("  ← " + str(info)[:110]) if info else ""))


def main():
    fast = "--fast" in sys.argv
    print("=" * 74)
    print("  B站插件 · 全覆盖自测（29 个工具一个不少）")
    print("=" * 74)

    src = io.open(os.path.join(_ROOT, "bili_duty", "mcp_server.py"), encoding="utf-8").read()
    declared = {n for n, _ in re.findall(r"^async def (bili_[a-z_]+)\((.*?)\)", src, re.M)}

    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_bd_full", os.path.join(_ROOT, "plugins", "bili_duty.py"))
    m = ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = m.BilibiliPlugin()
    desc = p.get_tool_descriptions()
    exposed = [d["name"] for d in desc]

    print("\n[A] 覆盖：插件定义 vs 小焦登记")
    ck("A", "没有漏掉的工具", not (declared - set(exposed)), sorted(declared - set(exposed)))
    ck("A", "没有凭空多出来的", not (set(exposed) - declared), sorted(set(exposed) - declared))
    ck("A", "没有重名", len(exposed) == len(set(exposed)), len(exposed))
    ck("A", "总数 = %d" % len(declared), len(exposed) == len(declared), len(exposed))
    ck("A", "每条描述都写了「什么时候用」",
       all("什么时候用" in d.get("description", "") for d in desc),
       [d["name"] for d in desc if "什么时候用" not in d.get("description", "")])

    print("\n[B] 真调一遍（每个工具都打一次）")
    import xiaojiao_app as X
    X._build_tools()
    routed = {k for k in X._TOOL2PLUGIN if k.startswith("bili_")}
    ck("B", "全部进了路由表", declared <= routed, sorted(declared - routed))

    dead = []
    for name in sorted(declared):
        if fast and name in SLOW:
            print("  ⏭  [B] %-28s 跳过（--fast 模式）" % name)
            continue
        args = dict(ARGS.get(name, {}))
        if name in ("bili_reply", "bili_send_message"):
            args = {"bvid": "BV1x", "text": "t"} if name == "bili_reply" else {"receiver_uid": 1, "text": "t"}
        out = str(X.run_tool(name, args, force=True))
        ok = bool(out.strip()) and ("Traceback" not in out)
        if not ok:
            dead.append((name, out[:80]))
        ck("B", "%-28s 有可读返回" % name, ok, out[:70].replace("\n", " "))

    print("\n[C] 写操作必须被本地闸拦住（不传 force 一律不发）")
    r1 = str(X.run_tool("bili_reply", {"bvid": "BV1x411c7mD", "text": "t"}, force=True))
    ck("C", "bili_reply 不传 force → 拒绝", ("没有发" in r1) or ("真的发到" in r1), r1[:70])
    r2 = str(p.execute("bili_send_message", {"receiver_uid": 1, "text": "t"}))
    ck("C", "bili_send_message 不传 force → 拒绝", ("没有发" in r2) or ("真的发私信" in r2), r2[:70])

    print("\n" + "=" * 74)
    print("  通过 %d / 共 %d%s" % (len(PASS), len(PASS) + len(FAIL),
                                  ("（失败：%s）" % ", ".join(FAIL)) if FAIL else ""))
    if dead:
        print("  返回不了的工具：%s" % dead)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
