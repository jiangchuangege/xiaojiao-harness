# -*- coding: utf-8 -*-
"""B站插件接入自测：确认"接上了、而且规矩还在"。

为什么要有它：插件是**自动扫描加载**的（plugins/*.py），一旦某个文件写坏，
小焦启动时就会整体少一批能力，而人往往只看到"工具用不了"却不知道是哪一步断了。
这里把"能装、能路由、能真跑、红线还在、失败不编造"五件事钉住。

离线可跑（不需要网络的部分会在没有网络时自动跳过并如实标注）。

跑：python tools/test_bili_plugin.py
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

PASS, FAIL = [], []


def ck(group, name, cond, info=""):
    (PASS if cond else FAIL).append("%s/%s" % (group, name))
    print("  %s [%s] %s%s" % ("✅" if cond else "❌", group, name,
                              ("  ← " + str(info)[:120]) if info else ""))


def main():
    print("=" * 70)
    print("  B站插件接入自测")
    print("=" * 70)

    import xiaojiao_app as X

    # ---------- A 装载 ----------
    print("\n[A] 能被插件加载器认出来")
    P = X.load_plugins()
    ck("A", "bili_duty 出现在插件表里", "bili_duty" in P, sorted(P)[:3])
    ent = P.get("bili_duty") or {}
    ck("A", "是 py 类型且默认开启", ent.get("type") == "py" and bool(ent.get("on")), ent.get("type"))
    desc = ent.get("desc") or []
    ck("A", "登记了 14 个工具", len(desc) == 14, len(desc))
    ck("A", "每个工具都有 name+description+parameters",
       all(d.get("name") and d.get("description") and isinstance(d.get("parameters"), dict)
           for d in desc))
    ck("A", "工具名都带 bili_ 前缀（不会和内置工具撞名）",
       all(str(d.get("name")).startswith("bili_") for d in desc))

    # ---------- B 路由 ----------
    print("\n[B] 小焦自己的调度能路由到它")
    X._build_tools()
    routed = sorted(k for k in X._TOOL2PLUGIN if k.startswith("bili_"))
    ck("B", "路由表里有全部 14 个", len(routed) == 14, routed[:4])
    ck("B", "路由指向 bili_duty", all(X._TOOL2PLUGIN[k] == "bili_duty" for k in routed))

    # ---------- C 真跑 ----------
    print("\n[C] 真跑一次（真 B站 接口；断网时如实跳过）")
    r = str(X.run_tool("bili_login_check", {}, force=True))
    if "已登录" in r or "already_logged_in" in r:
        ck("C", "登录状态可查（凭证已复用）", True, r[:60])
        real_run = True
    elif "失败" in r or "超时" in r or "没有返回" in r:
        ck("C", "断网/限流时如实报错、不编结果", True, r[:80])
        real_run = False
    else:
        ck("C", "登录状态可查", False, r[:80])
        real_run = False

    ck("C", "空参数给的是可读提示，不是 B站 的 -400",
       "请告诉我具体是哪一个" in str(X.run_tool("bili_search", {}, force=True)))

    # ---------- D 红线 ----------
    print("\n[D] 红线：往 B站 写东西必须本人在场")
    rp = str(X.run_tool("bili_reply", {"bvid": "BV1xx411c7mD", "text": "测试"}, force=True))
    ck("D", "不传 force 时拒绝发送", "没有发" in rp or "真的发到" in rp, rp[:60])
    ck("D", "拒绝时给了明确口令（说'发吧'）", "发吧" in rp, rp[:80])
    # 插件实现层面再确认一次（不走小焦调度，防止"绕过调度就绕过了红线"）
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("_bd", os.path.join(_ROOT, "plugins", "bili_duty.py"))
    m = ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    inst = m.BilibiliPlugin()
    ck("D", "插件自身也有闸（不是只靠外层）",
       "没有发" in inst.execute("bili_reply", {"bvid": "BV1x", "text": "t"}))
    # 缺参数时**必须**在本地就拦住（不能靠 B站 的报错兜底 —— 万一参数恰好合法，
    # 那就成了"一次误发"。所以这里只传 bvid、故意不传 text）。
    ck("D", "缺 text 时本地就拦住，不会去碰 B站",
       "需要 bvid 和 text" in inst.execute("bili_reply", {"bvid": "BV1x", "force": True}))

    # ---------- E 环境 ----------
    print("\n[E] 插件环境（自带 venv，不污染小焦）")
    ck("E", "找到插件自己的 python", bool(inst._python_path()), inst._python_path())
    py = inst._python_path() or ""
    ck("E", "解析的是接入后的新位置（不是旧 .dsh 目录）",
       os.path.normcase(py).startswith(os.path.normcase(os.path.join(_ROOT, "bili_duty"))), py)
    ck("E", "桥接脚本存在", os.path.isfile(os.path.join(_ROOT, "bili_duty", "xj_bridge.py")))
    cfg = os.path.join(_ROOT, "bili_duty", "config.json")
    try:
        d = json.load(open(cfg, encoding="utf-8"))
        ck("E", "config.json 的 pythonPath 已指向新位置",
           os.path.normcase(str(d.get("pythonPath", ""))).startswith(
               os.path.normcase(os.path.join(_ROOT, "bili_duty"))), d.get("pythonPath"))
    except Exception as e:
        ck("E", "config.json 可读", False, e)

    print("\n" + "=" * 70)
    print("  通过 %d / 共 %d%s" % (len(PASS), len(PASS) + len(FAIL),
                                  ("（失败：%s）" % ", ".join(FAIL)) if FAIL else ""))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
