# -*- coding: utf-8 -*-
"""预设（Agent 预设）真实生效验证：切预设 → 配置真的变了 → 行为真的跟着变

为什么单独测：用户反馈"设置了预设跟没生效一模一样"。光看接口返回 ok 不算数，
必须验证**行为真的变了**：`闲聊陪伴` 预设会关掉联网（capabilities.web_search=false），
所以切到它之后问"最近 AI 新闻"应该是 **0 条搜索来源**；再切回带联网的预设，
同样的问题就应该有来源 —— 行为对不上就是没生效。

安全：测试前后会**完整备份/还原**用户的 xiaojiao_control.json（逐字节），不留副作用。

用法：
    python tests/stress/preset_check.py
    python tests/stress/preset_check.py --base http://127.0.0.1:5000
退出码：0 = 全部通过；1 = 有失败
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CTL = os.path.join(REPO_ROOT, "xiaojiao_control.json")


def main() -> int:
    ap = argparse.ArgumentParser(description="预设真实生效验证")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print("  %s %-46s %s" % ("✅" if cond else "❌", name, str(detail)[:100]), flush=True)

    def get(p):
        return requests.get(base + p, timeout=30).json()

    def post(p, payload, timeout=300):
        return requests.post(base + p, json=payload, timeout=timeout).json()

    print("=" * 70)
    print("  预设真实生效验证（服务：%s）" % base)
    print("=" * 70)

    backup = ""
    if os.path.exists(CTL):
        backup = os.path.join(tempfile.mkdtemp(prefix="xj_ctl_"), "xiaojiao_control.json")
        shutil.copy2(CTL, backup)
        print("  已备份操控文件 → %s" % backup)

    try:
        print("\n[1] 预设列表")
        d = get("/api/presets")
        files = [p["file"] for p in d.get("presets", [])]
        names = [p["name"] for p in d.get("presets", [])]
        check("能列出预设（≥2 个）", len(files) >= 2, "%s" % names[:6])
        check("当前预设字段存在（首次使用可为空）", isinstance(d.get("current"), str),
              "current=%r" % d.get("current", ""))

        if "闲聊陪伴.json" in files and "编程助手.json" in files:
            print("\n[2] 切到「闲聊陪伴」（预设里关掉了联网）")
            r = post("/api/presets/load", {"file": "闲聊陪伴.json"})
            check("接口返回 ok", r.get("ok") is True, str(r.get("preset")))
            check("接口回传的 capabilities 与预设一致（web_search=false）",
                  (r.get("capabilities") or {}).get("web_search") is False,
                  str(r.get("capabilities")))
            check("当前预设名已更新", r.get("preset") == "闲聊陪伴", str(r.get("preset")))
            s = get("/api/settings")
            _role = ((s.get("control") or {}).get("role") or "")      # 注意：/api/settings 是嵌套结构
            check("设置接口读到的新人格与预设一致",
                  "闲聊" in _role or "幽默" in _role, _role[:50])

            print("\n[3] 行为验证：关掉联网后，问同一个问题应该 0 条来源")
            a1 = post("/api/chat", {"message": "最近 AI 新闻"})
            n1 = len(a1.get("sources") or [])
            check("闲聊陪伴预设 → 不联网检索（来源 0 条）", n1 == 0, "来源=%d" % n1)

            print("\n[4] 切回「编程助手」（预设里开着联网）")
            r2 = post("/api/presets/load", {"file": "编程助手.json"})
            check("接口返回 ok", r2.get("ok") is True, str(r2.get("preset")))
            check("capabilities.web_search 回到 true",
                  (r2.get("capabilities") or {}).get("web_search") is True,
                  str(r2.get("capabilities")))
            check("temperature 跟着预设变成 0.2", abs(float(r2.get("temperature") or 0) - 0.2) < 1e-6,
                  str(r2.get("temperature")))
            a2 = post("/api/chat", {"message": "最近 AI 新闻"})
            n2 = len(a2.get("sources") or [])
            check("编程助手预设 → 恢复联网检索（来源 >0）", n2 > 0, "来源=%d" % n2)
            check("两次行为确实不同（预设真的生效了）", n1 != n2, "%d vs %d" % (n1, n2))
        else:
            check("内置预设齐全（闲聊陪伴 + 编程助手）", False, str(files))
    finally:
        if backup:
            shutil.copy2(backup, CTL)
            time.sleep(1.2)                     # 让 app 的 mtime 热重载生效
            # 以**文件内容**为准判断是否还原成功（内存里的旧值可能还没被热重载覆盖）
            import json
            try:
                with open(CTL, encoding="utf-8") as fh:
                    cur = json.load(fh)
                check("操控文件已按原样还原（人格/工具开关/模型都没丢）",
                      cur.get("preset") in (None, "")
                      and cur.get("models") == json.load(open(backup, encoding="utf-8")).get("models"),
                      "preset=%r models=%d" % (cur.get("preset"), len(cur.get("models") or [])))
            except Exception as e:
                check("操控文件已还原", False, str(e)[:80])

    print("\n" + "=" * 70)
    print("  预设验证：通过 %d / %d" % (len(passed), len(passed) + len(failed)))
    for f in failed:
        print("    ❌ %s" % f)
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
