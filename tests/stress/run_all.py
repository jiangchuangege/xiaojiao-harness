# -*- coding: utf-8 -*-
"""小焦抓取插件压力测试 · 总入口

用法：
    python tests/stress/run_all.py                 # 离线 + 联网（默认）
    python tests/stress/run_all.py --quick         # 跳过耗时项（并发/熔断自愈）
    XJ_STRESS_OFFLINE=1 python tests/stress/run_all.py   # 只跑离线用例（无网环境）
    python tests/stress/run_all.py --json results.json --min-pass-rate 95

四个套件：单元/渲染契约 → 应用逻辑（检索词/漏洞意图）→ 安全 → 联网。
退出码：0 = 通过率达标；1 = 低于门槛（CI 据此判定失败）
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Results, load_plugin, offline_only  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="小焦抓取插件压力测试")
    ap.add_argument("--json", default="results.json", help="结果 JSON 输出路径")
    ap.add_argument("--min-pass-rate", type=float, default=95.0, help="通过率门槛（%%），低于则退出码 1")
    ap.add_argument("--quick", action="store_true", help="跳过耗时用例（并发/熔断自愈）")
    ap.add_argument("--offline", action="store_true", help="只跑离线用例")
    args = ap.parse_args()

    res = Results("xiaojiao-scrapling-stress")
    mod = load_plugin()          # 只加载一次，两个套件共用同一个插件实例

    print("=" * 62)
    print("  小焦抓取插件 · 压力测试（真实调用，禁止模拟）")
    print("=" * 62)

    print("\n[1/4] 离线用例（配置/安全闸门/会话/指标/参数校验/渲染契约/漏洞聚合）")
    import test_units
    test_units.run(res, mod=mod)

    print("\n[2/4] 应用逻辑用例（检索词清洗/漏洞查询意图/提示词铁律/工具注册）")
    import test_app_logic
    test_app_logic.run(res)

    print("\n[3/4] 安全用例（SSRF/robots/限速/脱敏/UA/穿越/命令端点/无遥测/无明文密钥）")
    import test_security
    test_security.run(res, mod=mod)

    if args.offline or offline_only():
        print("\n[4/4] 联网用例 —— 已跳过（--offline 或 XJ_STRESS_OFFLINE=1）")
        res.skip("联网", "全部联网用例", "离线模式")
    else:
        print("\n[4/4] 联网用例（真实抓取/批量/会话/对抗/NVD 漏洞聚合）")
        import test_network
        test_network.run(res, mod=mod, quick=args.quick)

    res.print_summary()
    path = res.save(args.json)
    print("结果已写入: %s" % path)

    if res.pass_rate < args.min_pass_rate:
        print("\n❌ 通过率 %.2f%% 低于门槛 %.2f%%" % (res.pass_rate, args.min_pass_rate))
        return 1
    print("\n✅ 通过率 %.2f%% ≥ 门槛 %.2f%%" % (res.pass_rate, args.min_pass_rate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
