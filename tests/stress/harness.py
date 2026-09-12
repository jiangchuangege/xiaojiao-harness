# -*- coding: utf-8 -*-
"""小焦抓取插件压力测试 · 公共骨架

设计目标：**同一个测试既能本地跑，也能在 CI 跑**，且结果可机读（JSON）以便设置通过率门槛。

分工：
  · harness.py —— 加载插件、调用封装、结果统计、报告输出
  · test_units.py —— 离线用例（不联网：配置校验/会话回收/指标/脱敏/JSON 美化/安全闸门）
  · test_network.py —— 联网用例（真实抓取、批量、对抗测试）
  · run_all.py —— 编排 + 通过率门槛 + results.json
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

# CI（GitHub Windows runner）控制台不是 UTF-8，打印 ✅/❌ 会 UnicodeEncodeError → 整个套件崩掉。
# 真实事故：CI 上 check_mermaid 就是这么失败的，导致压力测试从来没真正跑起来。这里统一兜住。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-ok — 老环境没有 reconfigure 也不该让测试挂掉
        pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_PATH = os.path.join(REPO_ROOT, "plugins", "scrapling_bridge.py")

# 测试用固定 URL（公开、稳定、允许抓取的示例站点）
URL_OK = "https://example.com"
URL_OK2 = "https://example.org"
URL_BAD_DOMAIN = "https://nonexistent-domain-9x7q2zz.example"
URL_INVALID = "这不是一个网址"
URL_SLOW = "https://httpbin.org/delay/10"
URL_REDIRECT_SSRF = "https://httpbin.org/redirect-to?url=http://127.0.0.1:5000/"
HARD_CAP = 90          # 单次调用硬上限（秒）


def load_plugin():
    """加载 plugins/scrapling_bridge.py（必须先登记 sys.modules，否则 @dataclass 会失败）。"""
    spec = importlib.util.spec_from_file_location("scrapling_bridge_under_test", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scrapling_bridge_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class Results:
    """用例结果收集器：记录通过/失败/跳过，最后算通过率并输出 JSON。"""

    def __init__(self, name: str = "stress") -> None:
        self.name = name
        self.rows: List[Dict[str, Any]] = []
        self.started = time.time()

    def add(self, group: str, case: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
        tag = "SKIP" if skipped else ("PASS" if ok else "FAIL")
        self.rows.append({"group": group, "case": case, "status": tag,
                          "detail": str(detail)[:300], "at": time.strftime("%H:%M:%S")})
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[tag]
        print("  %s [%s] %-40s %s" % (icon, group, case, str(detail)[:110]), flush=True)

    def check(self, group: str, case: str, cond: bool, detail: str = "") -> bool:
        self.add(group, case, bool(cond), detail)
        return bool(cond)

    def skip(self, group: str, case: str, why: str) -> None:
        self.add(group, case, True, why, skipped=True)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "FAIL")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "PASS")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "SKIP")

    @property
    def pass_rate(self) -> float:
        counted = self.passed + self.failed
        return 100.0 if counted == 0 else round(self.passed * 100.0 / counted, 2)

    def summary(self) -> Dict[str, Any]:
        return {"name": self.name, "total": self.total, "passed": self.passed,
                "failed": self.failed, "skipped": self.skipped, "pass_rate": self.pass_rate,
                "duration_s": round(time.time() - self.started, 1),
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "rows": self.rows}

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, ensure_ascii=False, indent=2)
        return path

    def print_summary(self) -> None:
        s = self.summary()
        print("\n" + "=" * 62)
        print("  通过 %d / 共 %d（失败 %d，跳过 %d）· 通过率 %.2f%% · 耗时 %.1fs"
              % (s["passed"], s["total"], s["failed"], s["skipped"], s["pass_rate"], s["duration_s"]))
        if self.failed:
            print("  失败用例：")
            for r in self.rows:
                if r["status"] == "FAIL":
                    print("    ❌ [%s] %s → %s" % (r["group"], r["case"], r["detail"][:120]))
        print("=" * 62, flush=True)


class Bridge:
    """插件调用封装：统一超时保护 + 结果解析。"""

    def __init__(self, mod, bridge=None) -> None:
        self.mod = mod
        self.impl = bridge or mod.ScraplingBridge()
        self.session_ids: Dict[str, str] = {}

    def call(self, tool: str, params: Optional[Dict[str, Any]] = None, cap: int = HARD_CAP
             ) -> Tuple[float, bool, str, Dict[str, Any]]:
        """返回 (耗时, 是否挂死, 错误文本, 原始 dict)。"""
        t0 = time.time()
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            out = pool.submit(self.impl.execute, tool, params or {}).result(timeout=cap)
            hung = False
        except Exception as e:
            out = "HARD_CAP_TIMEOUT" if type(e).__name__ == "TimeoutError" else "PYTHON_EXCEPTION: %s" % e
            hung = type(e).__name__ == "TimeoutError"
        finally:
            pool.shutdown(wait=False)
        el = round(time.time() - t0, 2)
        try:
            d = json.loads(out)
            err = d.get("error") or ""
        except Exception:
            d, err = {}, "非JSON返回: " + str(out)[:120]
        return el, hung, err, d

    def open_sessions(self) -> None:
        """准备 dynamic / static 两类会话（供会话类用例使用）。"""
        _, _, _, d = self.call("open_session", {"session_type": "dynamic"})
        try:
            self.session_ids["dynamic"] = json.loads(d.get("content") or "{}").get("session_id", "")
        except Exception:
            self.session_ids["dynamic"] = ""
        _, _, _, d2 = self.call("open_request_session", {})
        try:
            self.session_ids["static"] = json.loads(d2.get("content") or "{}").get("session_id", "")
        except Exception:
            self.session_ids["static"] = ""

    def close_all(self) -> None:
        for sid in list(self.session_ids.values()):
            if sid:
                self.call("close_session", {"session_id": sid}, cap=30)


def offline_only() -> bool:
    """环境变量 XJ_STRESS_OFFLINE=1 → 只跑离线用例（无网络场景）。"""
    return os.environ.get("XJ_STRESS_OFFLINE", "") == "1"
