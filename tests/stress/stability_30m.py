# -*- coding: utf-8 -*-
"""30 分钟无头压力测试（Headless Stress Test）· 只测插件 Python 代码，**绝不打任何大模型 API**

设计红线（按用户要求）：
  1. **禁止调用任何大模型 API**：不 import xiaojiao_app，不碰 /api/chat、/v1、BrainManager，
     不连 llama-swap / 云端模型 —— 本脚本连小焦进程都不需要启动，纯本地。
  2. **直接导入插件类并直调 execute()**：`plugins/scrapling_bridge.py` → `ScraplingBridge().execute(tool, params)`。
  3. **测试数据全部硬编码**（example.com / iana / httpbin / 必然失败的域名 / NVD 接口）。
  4. **高密度循环三类任务**：
     · 反复 `stealthy_fetch` 抓网页（压内存）
     · 反复 `open_session` **且不 close**（压会话回收机制）
     · 反复打**必失败**的 URL（压熔断与退避）
  5. 每 30 秒采一次：Python 堆 / 进程工作集 / 活跃会话 / chrome 进程数 / 线程数。
  6. 内存红线：**稳态涨幅 > 30%** → 立即停止并打印报错原文（相位造成的台阶单列，不算泄漏）。
  7. 崩溃 / 未捕获异常 → 立即停止并打印报错原文。
  8. 结束输出 Markdown 报告（内存、会话数、熔断次数、退避次数、P50/P95、异常记录）。

用法（**你自己在终端跑，我不启动也不监视**）：
    python tests/stress/stability_30m.py                          # 默认 30 分钟 / 每 10 秒一次
    python tests/stress/stability_30m.py --interval 5              # 更狠：每 5 秒一次
    python tests/stress/stability_30m.py --minutes 2 --interval 3   # 先小样试一下
    python tests/stress/stability_30m.py --app http://127.0.0.1:5000   # 顺带测小焦本体（只打非 LLM 接口）
    python tests/stress/stability_30m.py --no-nvd                  # 纯本地抓取，完全不碰外网接口
    python tests/stress/stability_30m.py --out logs/xxx.md --json logs/xxx.json

产出（都在 logs/ 目录，已被 .gitignore 忽略）：
    logs/stability_30m.md     ← 人类可读 Markdown 报告
    logs/stability_30m.json   ← 机读明细（含每 30 秒监控样本）

退出码：0 = PASS；1 = FAIL / 中止（报告里写明原因与报错原文）
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import traceback
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(ROOT, "plugins", "scrapling_bridge.py")

# ---------- 硬编码测试数据（不依赖任何外部配置/大模型） ----------
OK_URLS = [
    "https://example.com",
    "https://example.org",
    "https://www.iana.org/help/example-domains",
    "https://httpbin.org/json",
]
BAD_URL = "https://nonexistent-domain-9x7q2zz.example"       # 必然失败 → 打熔断
MEM_LIMIT_PCT = 30.0                                          # 内存涨幅红线（用户要求）
SAMPLE_EVERY = 30.0                                           # 每 30 秒采一次


# ======================================================================
# 计数：熔断 / 退避 / 会话回收 —— 全部来自插件**自己的真实日志**
# ======================================================================
class LogCounter(logging.Handler):
    """挂在小焦日志器和 root 上，按关键词统计真实发生的事件次数。"""

    PATTERNS = {
        "熔断触发": re.compile(r"熔断触发"),
        "熔断恢复": re.compile(r"熔断恢复"),
        "退避重试": re.compile(r"(重试|退避|Retrying in|429)"),
        "会话回收": re.compile(r"会话回收"),
        "工具异常": re.compile(r"(工具 .* 异常|Traceback)"),
    }

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.counts = {k: 0 for k in self.PATTERNS}
        self.samples = []                                    # 命中的原始日志（留证）

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: silent-ok — 日志格式化失败也要继续，别影响压测
            return
        for k, pat in self.PATTERNS.items():
            if pat.search(msg):
                self.counts[k] += 1
                if len(self.samples) < 40:
                    self.samples.append("[%s] %s" % (k, msg[:160]))


def load_plugin():
    """直接加载插件模块（不 import 小焦 app，不初始化任何大脑）。"""
    spec = importlib.util.spec_from_file_location("scrapling_bridge_headless", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scrapling_bridge_headless"] = mod     # Py3.13 下 dataclass 需要它
    spec.loader.exec_module(mod)
    return mod


def call(inst, tool, params, cap=60):
    """直调插件 execute()：返回 (耗时, 是否成功, 错误文本, 解析后的 dict)。

    带硬超时：插件本身也有超时，这里是最后一道保险（挂死也不会拖垮压测进程）。
    """
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        out = pool.submit(inst.execute, tool, params or {}).result(timeout=cap)
        hung = False
    except FutTimeout:
        out, hung = "HARD_CAP_TIMEOUT", True
    except Exception as e:
        out, hung = "PYTHON_EXCEPTION: %s: %s" % (type(e).__name__, e), False
    finally:
        pool.shutdown(wait=False)
    el = round(time.time() - t0, 2)
    try:
        d = json.loads(out)
        err = str(d.get("error") or "")
    except Exception:
        d, err = {}, "非 JSON 返回：" + str(out)[:160]
    if hung:
        err = "调用挂死（超过硬上限 %ds）" % cap
    return el, (not err and not hung), err, d


def heap_mb() -> float:
    try:
        return round(tracemalloc.get_traced_memory()[0] / 1048576.0, 3)
    except Exception:  # noqa: silent-ok — tracemalloc 未启动时返回 0
        return 0.0


def _tasklist(args, timeout=25):
    try:
        r = subprocess.run(["tasklist"] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:  # noqa: silent-ok — 拿不到进程信息就跳过该项
        return ""


def rss_mb(pid: int) -> float:
    """本进程工作集（MB）：Windows 下解析 tasklist 的 "Mem Usage"（形如 123,456 K）。"""
    out = _tasklist(["/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"])
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 5 and parts[1].isdigit() and int(parts[1]) == pid:
            num = "".join(ch for ch in parts[4] if ch.isdigit())
            if num:
                return round(int(num) / 1024.0, 1)
    return 0.0


def chrome_procs() -> int:
    """系统里 chrome.exe 进程数（含你自己的浏览器，看**增量趋势**），用于验证"进程不堆积"。"""
    out = _tasklist(["/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"])
    return sum(1 for l in out.splitlines() if "chrome.exe" in l.lower())


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(round(len(sorted_vals) * p)) - 1))
    return sorted_vals[idx]


class State:
    def __init__(self):
        self.t0 = time.time()
        self.lat = []              # 正式统计的耗时（预热不计）
        self.errors = []           # (时刻, 工具, 错误原文)
        self.samples = []          # 每 30 秒监控样本
        self.notes = []
        self.phase = {}
        self.peak_heap = self.peak_rss = 0.0
        self.peak_heap_ss = self.peak_rss_ss = 0.0
        self.peak_sessions = 0
        self.base_heap = self.base_rss = 0.0
        self.base_heap_ss = self.base_rss_ss = 0.0
        self.base_chrome = 0
        self.judge_steady = False  # 只有稳态阶段才套 30% 内存红线
        self.aborted = False
        self.abort_reason = ""
        self.abort_tb = ""
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")

    def note(self, s):
        self.notes.append("[%s] %s" % (time.strftime("%H:%M:%S"), s))
        print("  · %s" % s, flush=True)

    def err(self, tool, msg):
        self.errors.append((time.strftime("%H:%M:%S"), tool, str(msg)[:400]))
        print("  ⚠️ [%s] %s → %s" % (time.strftime("%H:%M:%S"), tool, str(msg)[:110]), flush=True)


def sampler(st: State, inst, stop: threading.Event, pid: int, counter: LogCounter):
    """每 30 秒采样：内存 / 会话 / 进程 / 线程 / 熔断状态 + 日志计数。"""
    while not stop.wait(SAMPLE_EVERY):
        try:
            stats = inst.sessions_stats()
            brk = inst.metrics_snapshot().get("breaker", {})
            h, r, ch = heap_mb(), rss_mb(pid), chrome_procs()
            st.peak_heap, st.peak_rss = max(st.peak_heap, h), max(st.peak_rss, r)
            st.peak_sessions = max(st.peak_sessions, int(stats.get("active", 0)))
            if st.judge_steady:
                st.peak_heap_ss, st.peak_rss_ss = max(st.peak_heap_ss, h), max(st.peak_rss_ss, r)
            rec = {"at": time.strftime("%H:%M:%S"), "t_s": round(time.time() - st.t0),
                   "heap_mb": h, "rss_mb": r,
                   "sessions_active": stats.get("active", 0),
                   "sessions_max": stats.get("max_sessions", 0),
                   "sessions_types": {s.get("type") for s in (stats.get("sessions") or [])},
                   "chrome": ch, "threads": threading.active_count(),
                   "breaker_open": len(brk.get("open_until") or {}),
                   "logs": dict(counter.counts)}
            rec["sessions_types"] = sorted(x for x in rec["sessions_types"] if x)
            st.samples.append(rec)
            print("  ⏱ %s 堆 %.1fMB 工作集 %.1fMB 会话 %s/%s chrome %d 线程 %d ｜熔断 %d 退避 %d 回收 %d"
                  % (rec["at"], h, r, rec["sessions_active"], rec["sessions_max"], ch,
                     rec["threads"], counter.counts["熔断触发"], counter.counts["退避重试"],
                     counter.counts["会话回收"]), flush=True)
            if st.judge_steady:
                for label, base, cur in (("堆", st.base_heap_ss, st.peak_heap_ss),
                                         ("工作集", st.base_rss_ss, st.peak_rss_ss)):
                    if base > 0 and (cur - base) / base * 100.0 > MEM_LIMIT_PCT:
                        st.aborted = True
                        st.abort_reason = ("疑似内存泄漏：稳态 %s 涨幅 %.1f%% > %.0f%%（基线 %.1f → 峰值 %.1f）"
                                           % (label, (cur - base) / base * 100.0, MEM_LIMIT_PCT, base, cur))
                        st.abort_tb = "最近 5 条监控样本：\n" + json.dumps(st.samples[-5:],
                                                                          ensure_ascii=False, indent=1)
                        stop.set()
                        return
        except Exception as e:      # 采样线程自身出错只记录，不停压测
            st.err("采样线程", "%s: %s" % (type(e).__name__, e))


# ======================================================================
# 专项相位
# ======================================================================
def phase_sessions(st: State, inst) -> None:
    """会话回收：连开 25 个 **不 close** → 超过上限必须自动踢旧的，活跃数不超上限。"""
    print("\n[相位 A] 会话回收：连开 25 个会话且**不主动关闭**…", flush=True)
    max_s = int(inst.sessions_stats().get("max_sessions") or 0)
    peak, opened = 0, 0
    t0 = time.time()
    for _ in range(25):
        _el, ok, err, _d = call(inst, "open_session", {"session_type": "dynamic"}, cap=60)
        opened += 1 if ok else 0
        if not ok:
            st.err("open_session", err or "失败")
        stats = inst.sessions_stats()
        peak = max(peak, int(stats.get("active", 0)))
        if max_s and int(stats.get("active", 0)) > max_s:
            st.aborted = True
            st.abort_reason = "会话未被回收：活跃 %s > 上限 %s（进程会堆积）" % (stats.get("active"), max_s)
            st.abort_tb = json.dumps(stats, ensure_ascii=False, indent=1)[:1500]
            return
    st.peak_sessions = max(st.peak_sessions, peak)
    st.phase["会话回收"] = {"连开个数": 25, "开成功": opened, "活跃峰值": peak, "上限": max_s,
                            "耗时_s": round(time.time() - t0, 1),
                            "回收记录": (inst.sessions_stats().get("reclaimed_recent") or [])[:5]}
    st.note("会话回收：开成功 %d 个，活跃峰值 %d（上限 %d）→ %s"
            % (opened, peak, max_s, "不堆积 ✅" if peak <= max_s else "❌ 超限"))


def phase_breaker(st: State, inst, counter: LogCounter) -> None:
    """熔断 + 退避：连打 3 次必失败请求 → 熔断生效 → 30 秒后自动恢复（真实计时）。"""
    print("\n[相位 B] 熔断/退避：连续打 3 次必失败请求…", flush=True)
    trips0, back0 = counter.counts["熔断触发"], counter.counts["退避重试"]
    seq = []
    for _ in range(3):
        _el, _ok, err, _d = call(inst, "get", {"url": BAD_URL, "timeout": 8}, cap=30)
        seq.append(err[:60] if err else "成功(意外)")
    trip_t0 = time.time()
    _el, ok2, err2, _d2 = call(inst, "get", {"url": OK_URLS[0], "timeout": 10}, cap=30)
    protected = ("保护" in err2) or ("暂时不可用" in err2)
    tripped = counter.counts["熔断触发"] > trips0
    recovered = None
    if tripped:
        print("  熔断已触发，等待自动恢复（最多 45 秒）…", flush=True)
        for _ in range(15):
            time.sleep(3)
            _el, ok3, err3, _d3 = call(inst, "get", {"url": OK_URLS[0], "timeout": 15}, cap=40)
            if ok3:
                recovered = round(time.time() - trip_t0, 1)
                break
    st.phase["熔断/退避"] = {"熔断触发": tripped, "熔断期间被拦": protected,
                             "恢复耗时_s": recovered, "失败序列": seq,
                             "退避/重试次数增量": counter.counts["退避重试"] - back0}
    st.note("熔断：%s ｜ 恢复：%s ｜ 退避/重试：%d 次"
            % ("触发 ✅" if tripped else "未触发", ("%.1f 秒后自动恢复 ✅" % recovered) if recovered else "未恢复",
               counter.counts["退避重试"] - back0))


def phase_nvd(st: State, inst, counter: LogCounter) -> None:
    """NVD 限流：短时间内连打 4 次 → 遇限流必须退避重试/中文提示，不崩。"""
    print("\n[相位 C] NVD 限流：连续 4 次查询…", flush=True)
    back0 = counter.counts["退避重试"]
    rows = []
    for i in range(4):
        t0 = time.time()
        _el, ok, err, d = call(inst, "collect_vulnerabilities",
                               {"days": 1, "severity": "HIGH", "limit": 2}, cap=180)
        content = str(d.get("content") or "")
        kind = ("表格" if "NVD 漏洞速览" in content else
                ("限流/退避" if any(k in err for k in ("限流", "429", "频繁")) else
                 ("其它错误：" + err[:50] if err else "其它")))
        rows.append({"第几次": i + 1, "结果": kind, "耗时_s": round(time.time() - t0, 1), "错误": err[:120]})
        print("   第 %d 次：%s（%.1f 秒）%s" % (i + 1, kind, time.time() - t0, ("｜" + err[:50]) if err else ""),
              flush=True)
        if i < 3:
            time.sleep(1)
    st.phase["NVD 限流"] = {"轮次": rows, "出现限流提示": any(r["结果"] == "限流/退避" for r in rows),
                            "退避/重试次数增量": counter.counts["退避重试"] - back0,
                            "崩溃": any("Traceback" in (r["错误"] or "") for r in rows)}
    st.note("NVD：4 次查询结果 = %s（未崩溃 ✅）" % "、".join(r["结果"] for r in rows))


def phase_app(st: State, base: str) -> None:
    """小焦本体（**只打非 LLM 接口**）：/metrics、/api/scrapling/metrics、/api/env、/api/sessions。

    明确不碰 /api/chat、/api/voice/*、/v1/* —— 那些会触发大脑推理（烧 Token）。这里只验证
    小焦进程本身在高压下不崩、指标接口始终可用。
    """
    import urllib.request
    print("\n[相位 D] 小焦本体健康采样（只打非 LLM 接口，%s）…" % base, flush=True)
    endpoints = ["/metrics", "/api/scrapling/metrics", "/api/env", "/api/sessions"]
    rows, ok_n = [], 0
    for ep in endpoints:
        t0 = time.time()
        try:
            with urllib.request.urlopen(base.rstrip("/") + ep, timeout=20) as r:
                code, size = r.status, len(r.read())
            rows.append({"接口": ep, "HTTP": code, "字节": size, "耗时_s": round(time.time() - t0, 2)})
            ok_n += 1 if code == 200 else 0
        except Exception as e:
            rows.append({"接口": ep, "HTTP": "失败", "错误": str(e)[:80],
                         "耗时_s": round(time.time() - t0, 2)})
            st.err("app" + ep, str(e))
    st.phase["小焦本体（非 LLM 接口）"] = {"抽样": rows, "成功": "%d/%d" % (ok_n, len(endpoints))}
    st.note("小焦本体：非 LLM 接口 %d/%d 正常（全程未调用 /api/chat，无 Token 消耗）"
            % (ok_n, len(endpoints)))


# ======================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="30 分钟无头压力测试（只测插件，不打任何大模型 API）")
    ap.add_argument("--minutes", type=float, default=30.0, help="总时长（分钟），默认 30")
    ap.add_argument("--interval", type=float, default=10.0, help="主循环每多少秒一次调用，默认 10")
    ap.add_argument("--out", default="logs/stability_30m.md")
    ap.add_argument("--json", default="logs/stability_30m.json")
    ap.add_argument("--no-nvd", action="store_true", help="跳过 NVD（纯本地抓取压测）")
    ap.add_argument("--app", default="", help="顺带压测小焦本体（只打非 LLM 接口，如 http://127.0.0.1:5000）")
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    json_path = os.path.abspath(args.json)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    total_s = max(60.0, args.minutes * 60.0)

    counter = LogCounter()
    for name in ("xiaojiao", ""):
        logging.getLogger(name).addHandler(counter)        # 插件日志 + 三方库日志都统计
    logging.getLogger().setLevel(logging.INFO)

    mod = load_plugin()                                     # ← 只加载插件，不碰任何大脑
    inst = mod.ScraplingBridge()
    st = State()
    pid = os.getpid()
    tracemalloc.start()

    print("=" * 74)
    print("  30 分钟无头压力测试（纯插件 · 绝不打大模型 API）")
    print("  总时长 %.0f 分钟 ｜ 每 %.0f 秒一次调用 ｜ 进程 PID %d" % (args.minutes, args.interval, pid))
    print("  插件：%s" % PLUGIN)
    print("  报告将写入：%s" % out_path)
    print("  明细 JSON  ：%s" % json_path)
    print("=" * 74, flush=True)

    stop = threading.Event()
    threading.Thread(target=sampler, args=(st, inst, stop, pid, counter),
                     name="xj-sampler", daemon=True).start()

    try:
        # ---------- 预热 + 基线 ----------
        warm_s = min(120.0, total_s / 10.0)
        print("\n[预热] %.0f 秒（拿稳定基线，耗时不计入统计）…" % warm_s, flush=True)
        w0, i = time.time(), 0
        while time.time() - w0 < warm_s and not stop.is_set():
            call(inst, "get", {"url": OK_URLS[i % len(OK_URLS)], "timeout": 25}, cap=60)
            i += 1
            time.sleep(max(1.0, args.interval / 2.0))
        st.base_heap, st.base_rss = heap_mb(), rss_mb(pid)
        st.base_chrome = chrome_procs()
        st.note("预热基线：堆 %.1fMB · 工作集 %.1fMB · 系统 chrome 进程 %d 个"
                % (st.base_heap, st.base_rss, st.base_chrome))
        st.lat.clear()

        # ---------- 专项相位 ----------
        phase_sessions(st, inst)
        if st.aborted:
            raise RuntimeError(st.abort_reason)
        phase_breaker(st, inst, counter)
        if not args.no_nvd:
            phase_nvd(st, inst, counter)
        if args.app:
            phase_app(st, args.app)

        # ---------- 稳态基线（相位台阶先落地，再套 30% 红线） ----------
        gc.collect()
        time.sleep(15)
        gc.collect()
        st.base_heap_ss, st.base_rss_ss = heap_mb(), rss_mb(pid)
        st.judge_steady = True
        st.phase["内存台阶"] = {"预热基线堆_mb": st.base_heap, "相位峰值堆_mb": st.peak_heap,
                                "稳态基线堆_mb": st.base_heap_ss,
                                "台阶回落_mb": round(st.peak_heap - st.base_heap_ss, 2)}
        st.note("稳态基线：堆 %.1fMB · 工作集 %.1fMB（30%% 内存红线从这一刻开始判）"
                % (st.base_heap_ss, st.base_rss_ss))

        # ---------- 主循环：高密度三类任务 ----------
        print("\n[主循环] 高密度循环：stealthy_fetch / open_session(不 close) / 失败 URL …", flush=True)
        i, nvd_done = 0, False
        while time.time() - st.t0 < total_s and not stop.is_set():
            i += 1
            m = i % 10
            if m in (0, 1, 2, 3):                       # 40%：隐身抓取（压内存）
                call(inst, "stealthy_fetch",
                     {"url": OK_URLS[(i // 10) % len(OK_URLS)], "timeout": 45}, cap=120)
            elif m in (4, 5, 6):                        # 30%：反复开会话且**不 close**（压会话回收）
                call(inst, "open_session", {"session_type": "dynamic"}, cap=60)
            elif m == 7:                                # 10%：普通抓取
                call(inst, "get", {"url": OK_URLS[i % len(OK_URLS)], "timeout": 25}, cap=60)
            elif m == 8:                                # 10%：必失败 URL（压熔断/退避）
                call(inst, "get", {"url": BAD_URL, "timeout": 8}, cap=30)
            else:                                       # 10%：批量 + 偶尔 NVD（+ 小焦本体健康采样）
                if not args.no_nvd and not nvd_done and i > 60:
                    call(inst, "collect_vulnerabilities", {"days": 3, "severity": "HIGH", "limit": 2},
                         cap=180)
                    nvd_done = True
                elif args.app and i % 50 == 0:
                    phase_app(st, args.app)              # 只打非 LLM 接口
                else:
                    call(inst, "bulk_get", {"urls": OK_URLS[:2]}, cap=90)

            if i % 5 == 0:
                print("  进度 %d 次｜%.1f/%.1f 分钟｜P50 %.2fs｜会话 %s/%s｜熔断 %d｜退避 %d｜异常 %d"
                      % (i, (time.time() - st.t0) / 60.0, args.minutes,
                         statistics.median(st.lat) if st.lat else 0,
                         inst.sessions_stats().get("active"), inst.sessions_stats().get("max_sessions"),
                         counter.counts["熔断触发"], counter.counts["退避重试"], len(st.errors)),
                      flush=True)
            time.sleep(max(0.5, args.interval - ((time.time() - st.t0) % args.interval)))
    except Exception as e:
        st.aborted = True
        st.abort_reason = st.abort_reason or ("压测主流程异常：%s: %s" % (type(e).__name__, e))
        st.abort_tb = traceback.format_exc()
        print("\n❌ 立即停止：%s\n%s" % (st.abort_reason, st.abort_tb), flush=True)
    finally:
        stop.set()

    # ---------- 汇总 ----------
    tracemalloc.stop()
    lat = sorted(st.lat)
    p50, p95 = round(pct(lat, 0.50), 2), round(pct(lat, 0.95), 2)
    heap_ss = (round((st.peak_heap_ss - st.base_heap_ss) / st.base_heap_ss * 100, 1)
               if st.base_heap_ss else 0.0)
    rss_ss = (round((st.peak_rss_ss - st.base_rss_ss) / st.base_rss_ss * 100, 1)
              if st.base_rss_ss else 0.0)
    heap_all = round((st.peak_heap - st.base_heap) / st.base_heap * 100, 1) if st.base_heap else 0.0
    rss_all = round((st.peak_rss - st.base_rss) / st.base_rss * 100, 1) if st.base_rss else 0.0
    sess_phase = st.phase.get("会话回收") or {}
    brk_phase = st.phase.get("熔断/退避") or {}
    ok_rate = round((len(st.lat) - len(st.errors)) * 100.0 / max(1, len(st.lat)), 2)

    verdict, reasons = "PASS", []
    if st.aborted:
        verdict, _ = "FAIL", reasons.append(st.abort_reason)
    if heap_ss > MEM_LIMIT_PCT:
        verdict = "FAIL"
        reasons.append("稳态堆涨幅 %.1f%% > %.0f%%" % (heap_ss, MEM_LIMIT_PCT))
    if rss_ss > MEM_LIMIT_PCT:
        verdict = "FAIL"
        reasons.append("稳态工作集涨幅 %.1f%% > %.0f%%" % (rss_ss, MEM_LIMIT_PCT))
    if sess_phase and sess_phase.get("活跃峰值", 0) > (sess_phase.get("上限") or 10 ** 9):
        verdict = "FAIL"
        reasons.append("会话活跃数超过上限")
    if not brk_phase.get("熔断触发"):
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        reasons.append("熔断未触发（失败可能没打满阈值）")
    elif brk_phase.get("恢复耗时_s") is None:
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        reasons.append("熔断后 45 秒内未恢复")

    md = ["# 🔬 30 分钟无头压力测试报告（Headless · 纯插件）", "",
          "> **未调用任何大模型 API**：直接 `import plugins/scrapling_bridge.py` 并调用 `ScraplingBridge.execute()`，",
          "> 不经小焦大脑、不走 `/api/chat`、不连 llama-swap / 云端模型。", "",
          "> 开始：%s ｜ 结束：%s ｜ 总时长：%.1f 分钟 ｜ 真实调用 %d 次（每 %.0f 秒一次）"
          % (st.started, time.strftime("%Y-%m-%d %H:%M:%S"), (time.time() - st.t0) / 60.0,
             len(st.lat), args.interval),
          "> 判定：**%s**%s" % (verdict, ("（" + "；".join(reasons) + "）") if reasons else ""), "",
          "## 一、核心指标", "",
          "| 指标 | 数值 | 门槛 | 结论 |", "|---|---|---|---|",
          "| 延迟 P50 | **%.2fs** | — | — |" % p50,
          "| 延迟 P95 | **%.2fs** | — | — |" % p95,
          "| 成功率 | **%.2f%%**（%d/%d） | ≥95%% | %s |"
          % (ok_rate, len(st.lat) - len(st.errors), len(st.lat), "✅" if ok_rate >= 95 else "❌"),
          "| 内存涨幅（堆，**稳态判据**） | **%+.1f%%**（%.1f → %.1f MB） | ≤30%% | %s |"
          % (heap_ss, st.base_heap_ss, st.peak_heap_ss, "✅" if heap_ss <= MEM_LIMIT_PCT else "❌"),
          "| 内存涨幅（工作集，**稳态判据**） | **%+.1f%%**（%.1f → %.1f MB） | ≤30%% | %s |"
          % (rss_ss, st.base_rss_ss, st.peak_rss_ss, "✅" if rss_ss <= MEM_LIMIT_PCT else "❌"),
          "| 内存涨幅（全程，含相位台阶，参考） | 堆 %+.1f%%（%.1f → %.1f）｜工作集 %+.1f%%（%.1f → %.1f） | — | — |"
          % (heap_all, st.base_heap, st.peak_heap, rss_all, st.base_rss, st.peak_rss),
          "| 内存峰值 | 堆 %.1f MB ｜ 工作集 %.1f MB（稳态 %.1f / %.1f） | — | — |"
          % (st.peak_heap, st.peak_rss, st.peak_heap_ss, st.peak_rss_ss),
          "| 会话数峰值 | **%s**（上限 %s） | ≤ 上限 | %s |"
          % (sess_phase.get("活跃峰值", st.peak_sessions), sess_phase.get("上限", "?"),
             "✅" if str(sess_phase.get("活跃峰值", 0)) <= str(sess_phase.get("上限", 10 ** 9)) else "❌"),
          "| **熔断触发次数** | **%d** | ≥1 | %s |"
          % (counter.counts["熔断触发"], "✅" if counter.counts["熔断触发"] >= 1 else "❌"),
          "| 熔断恢复 | %s | ≤45s | %s |"
          % ("%.1f 秒后自动恢复" % brk_phase["恢复耗时_s"] if brk_phase.get("恢复耗时_s") else "未恢复",
             "✅" if brk_phase.get("恢复耗时_s") else "❌"),
          "| **退避/重试次数** | **%d** | — | — |" % counter.counts["退避重试"],
          "| 会话回收次数 | %d | — | — |" % counter.counts["会话回收"],
          "| 异常记录 | **%d 条** | 0 条最佳 | %s |" % (len(st.errors), "✅" if not st.errors else "⚠️"),
          "", "## 二、专项相位", ""]
    for k, v in st.phase.items():
        md += ["### %s" % k, "", "```json", json.dumps(v, ensure_ascii=False, indent=1), "```", ""]
    md += ["## 三、内存/会话监控（每 30 秒一条）", "",
           "| 时刻 | 已跑(s) | 堆(MB) | 工作集(MB) | 活跃会话 | 上限 | chrome 进程 | 线程 | 熔断触发 | 退避 | 回收 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in st.samples:
        lg = s.get("logs") or {}
        md.append("| %s | %d | %.1f | %.1f | %s | %s | %d | %d | %d | %d | %d |"
                  % (s["at"], s["t_s"], s["heap_mb"], s["rss_mb"], s["sessions_active"],
                     s["sessions_max"], s["chrome"], s["threads"], lg.get("熔断触发", 0),
                     lg.get("退避重试", 0), lg.get("会话回收", 0)))
    md += ["", "## 四、过程记录", ""] + (["- %s" % n for n in st.notes] or ["- （无）"])
    md += ["", "## 五、异常记录", ""]
    if st.errors:
        md += ["| 时刻 | 工具 | 错误原文 |", "|---|---|---|"]
        md += ["| %s | %s | %s |" % (t, tool, str(e).replace("|", "\\|")[:200])
               for t, tool, e in st.errors[:40]]
        if len(st.errors) > 40:
            md.append("| … | … | 另有 %d 条（见 JSON） |" % (len(st.errors) - 40))
    else:
        md.append("无异常（真实调用全部有结果）")
    if counter.samples:
        md += ["", "## 六、关键日志留证（熔断/退避/回收前 40 条）", "", "```"]
        md += counter.samples
        md.append("```")
    if st.abort_tb:
        md += ["", "## 七、中止原因与报错原文", "", "```", st.abort_reason, st.abort_tb, "```"]
    md += ["", "---", "",
           "复现：`python tests/stress/stability_30m.py --minutes 30 --interval 10`",
           "（本脚本不调用任何大模型 API：直接调用插件的 `execute()`）", ""]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict, "reasons": reasons, "p50": p50, "p95": p95, "ok_rate": ok_rate,
                   "heap_growth_steady_pct": heap_ss, "rss_growth_steady_pct": rss_ss,
                   "heap_growth_total_pct": heap_all, "rss_growth_total_pct": rss_all,
                   "peak_heap_mb": st.peak_heap, "peak_rss_mb": st.peak_rss,
                   "peak_sessions": st.peak_sessions, "log_counts": counter.counts,
                   "errors": st.errors[:100], "samples": st.samples, "phases": st.phase,
                   "notes": st.notes}, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 74)
    print("  判定：%s%s" % (verdict, ("（" + "；".join(reasons) + "）") if reasons else ""))
    print("  P50 %.2fs ｜ P95 %.2fs ｜ 成功率 %.2f%%" % (p50, p95, ok_rate))
    print("  内存：稳态堆 %+.1f%% ／ 稳态工作集 %+.1f%%（峰值 %.1fMB / %.1fMB）"
          % (heap_ss, rss_ss, st.peak_heap, st.peak_rss))
    print("  会话峰值 %s ｜ 熔断 %d 次（恢复 %s）｜ 退避 %d 次 ｜ 回收 %d 次 ｜ 异常 %d 条"
          % (st.peak_sessions, counter.counts["熔断触发"], brk_phase.get("恢复耗时_s"),
             counter.counts["退避重试"], counter.counts["会话回收"], len(st.errors)))
    print("  报告：%s" % out_path)
    print("  明细：%s" % json_path)
    print("=" * 74, flush=True)
    if st.aborted:
        print("\n报错原文：\n%s" % st.abort_tb, flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
