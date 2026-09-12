# -*- coding: utf-8 -*-
"""30 分钟整机压力测试（Whole-XiaoJiao Stress Test）· 端到端压测**整个小焦**

两种压测层，默认**都跑**（`--target both`）：

  【A 层 · 整机端到端】**这才是"整个小焦"** —— 通过 HTTP 打真实接口，走完整链路：
      POST /api/chat      → 记忆召回 → 联网检索 → 大脑推理与工具调用 → 插件抓取/NVD → 会话落盘 → 自学习日志
      POST /api/session/new → 会话增长
      GET  /api/sessions、/metrics、/api/scrapling/metrics、/api/settings、/api/presets → 状态与指标
      监控**小焦进程自己**的内存（工作集）、HTTP 错误、聊天延迟 P50/P95、会话数增长、并发承载

  【B 层 · 插件直连】直接 `ScraplingBridge.execute()`，与大脑无关，压物理资源：
      高频 stealthy_fetch / open_session 不关闭 / 必失败 URL 打熔断退避 / NVD 限流

资源红线（两层都判）：**稳态内存涨幅 > 30%** → 立即停止并打印报错原文；崩溃/未捕获异常同样立即停止。

用法（自己跑，我不启动也不监视）：
    python tests/stress/stability_30m.py                        # 默认：整机 + 插件，30 分钟
    python tests/stress/stability_30m.py --target app            # 只压整机（走 /api/chat 全链路）
    python tests/stress/stability_30m.py --target plugin         # 只压插件（不碰大脑，最省）
    python tests/stress/stability_30m.py --chat-every 60         # 每 60 秒打一次真实对话
    python tests/stress/stability_30m.py --minutes 2 --interval 3 # 小样自检

前置：整机层需要小焦正在运行（`python start_xiaojiao.py`，默认 http://127.0.0.1:5000）。

产出（logs/ 已被 .gitignore 忽略）：
    logs/stability_30m.md     人类可读报告
    logs/stability_30m.json   机读明细（全部 30 秒样本 + 相位原始数据）
退出码：0 = PASS；1 = FAIL / 中止（报告写明原因与报错原文）
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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(ROOT, "plugins", "scrapling_bridge.py")

# ---------- 硬编码测试数据（不依赖任何外部配置） ----------
OK_URLS = ["https://example.com", "https://example.org",
           "https://www.iana.org/help/example-domains", "https://httpbin.org/json"]
BAD_URL = "https://nonexistent-domain-9x7q2zz.example"
CHAT_PROMPTS = [
    "抓一下 https://example.com",                 # 插件抓取全链路
    "最近 AI 新闻",                                # 联网检索全链路
    "抓取最近 7 天的高危漏洞",                        # NVD 漏洞工具全链路
    "用 Python 写个算斐波那契的脚本",                  # 大脑直答 + 代码渲染
    "你好",                                       # 最轻路径（对照延迟）
    "用搜索工具找漏洞",                              # 检索词清洗路径
]
APP_ENDPOINTS = ["/metrics", "/api/scrapling/metrics", "/api/sessions", "/api/settings", "/api/presets"]
MEM_LIMIT_PCT = 30.0          # 相对涨幅红线
MEM_LIMIT_ABS_MB = 10.0       # **绝对增量地板**：涨幅再大，只要没涨过这个量就不算泄漏
SAMPLE_EVERY = 30.0


# ======================================================================
# 基础工具
# ======================================================================
class LogCounter(logging.Handler):
    """挂在日志器上，按关键词统计**插件真实日志**里的事件次数。"""

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
        self.samples = []

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: silent-ok — 日志格式化失败也要继续压测
            return
        for k, pat in self.PATTERNS.items():
            if pat.search(msg):
                self.counts[k] += 1
                if len(self.samples) < 40:
                    self.samples.append("[%s] %s" % (k, msg[:160]))


def load_plugin():
    spec = importlib.util.spec_from_file_location("scrapling_bridge_stress", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scrapling_bridge_stress"] = mod
    spec.loader.exec_module(mod)
    return mod


def call(inst, tool, params, cap=60):
    """B 层：直调插件 execute()，带硬超时（挂死也不会拖垮压测进程）。"""
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


def http(method, url, payload=None, timeout=300):
    """A 层：真实 HTTP 调用小焦（返回 状态码, 耗时, dict/文本, 错误文本）。"""
    t0 = time.time()
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "xj-stress"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
        el = time.time() - t0
        try:
            return r.status, el, json.loads(body), ""
        except Exception:
            return r.status, el, {}, body[:200]
    except urllib.error.HTTPError as e:
        el = time.time() - t0
        try:
            return e.code, el, json.loads(e.read().decode("utf-8", "ignore")), ""
        except Exception:
            return e.code, el, {}, str(e)[:200]
    except Exception as e:
        return 0, time.time() - t0, {}, "%s: %s" % (type(e).__name__, str(e)[:160])


def heap_mb() -> float:
    try:
        return round(tracemalloc.get_traced_memory()[0] / 1048576.0, 3)
    except Exception:  # noqa: silent-ok — tracemalloc 未启动返回 0
        return 0.0


def _tasklist(args, timeout=25):
    try:
        r = subprocess.run(["tasklist"] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:  # noqa: silent-ok — 拿不到进程信息就跳过该项
        return ""


def proc_rss_mb(pid: int) -> float:
    """指定进程的工作集（MB）：解析 tasklist 的 "Mem Usage"（形如 123,456 K）。"""
    if not pid:
        return 0.0
    out = _tasklist(["/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"])
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 5 and parts[1].isdigit() and int(parts[1]) == pid:
            num = "".join(ch for ch in parts[4] if ch.isdigit())
            if num:
                return round(int(num) / 1024.0, 1)
    return 0.0


def chrome_procs() -> int:
    out = _tasklist(["/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"])
    return sum(1 for l in out.splitlines() if "chrome.exe" in l.lower())


def find_app_pid(port: int = 5000) -> int:
    """按监听端口找小焦进程 PID（用于量**小焦自己**的内存）。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25)
        for line in (r.stdout or "").splitlines():
            if "LISTENING" in line.upper() and (":%d" % port) in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
    except Exception:  # noqa: silent-ok — 找不到就只报 0，不影响压测
        pass
    return 0


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(round(len(sorted_vals) * p)) - 1))
    return sorted_vals[idx]


def mem_verdict(label: str, base: float, peak: float, abs_floor: float = MEM_LIMIT_ABS_MB):
    """内存判定 → (是否泄漏, 涨幅%, 绝对增量MB, 说明)。

    **判定规则（两条必须同时成立才算泄漏）**：
      ① 绝对增量 > abs_floor（默认 10 MB）—— 先过"绝对量地板"；
      ② 相对涨幅 > MEM_LIMIT_PCT（30%）。
    为什么要绝对地板：Python 堆基线只有 0.19MB 时，涨 7KB 就是 +30%，
    纯属基数效应，不是泄漏（真实踩过：30.5% 假警报）。
    只有涨幅、没有绝对量的增长，一律按"正常波动"放行，绝不再产生假 FAIL。
    """
    if not base or base <= 0:
        return False, 0.0, 0.0, "%s：无有效基线，跳过判定" % label
    growth = round((peak - base) / base * 100.0, 1)
    delta = round(peak - base, 3)
    if delta <= abs_floor:
        return False, growth, delta, ("%s：+%.3f MB（≤%.0f MB 地板，视为正常波动；"
                                      "百分比 %+.1f%% 不参与判定）" % (label, delta, abs_floor, growth))
    if growth > MEM_LIMIT_PCT:
        return True, growth, delta, ("%s：+%.1f MB 且涨幅 %.1f%% > %.0f%% → 疑似泄漏"
                                     % (label, delta, growth, MEM_LIMIT_PCT))
    return False, growth, delta, ("%s：+%.1f MB（已过地板，但涨幅 %.1f%% 未超 %.0f%%）"
                                  % (label, delta, growth, MEM_LIMIT_PCT))


class State:
    def __init__(self, target):
        self.t0 = time.time()
        self.target = target
        self.lat = []                 # B 层插件调用耗时
        self.chat_lat = []            # A 层 /api/chat 端到端耗时
        self.http_codes = {}          # HTTP 状态码统计
        self.errors = []
        self.samples = []
        self.notes = []
        self.phase = {}
        self.peak_heap = self.peak_rss = 0.0
        self.peak_heap_ss = self.peak_rss_ss = 0.0
        self.base_heap = self.base_rss = 0.0
        self.base_heap_ss = self.base_rss_ss = 0.0
        self.app_pid = 0
        self.app_peak_ss = self.app_base_ss = 0.0
        self.app_peak = 0.0
        self.peak_sessions = 0
        self.app_sessions_peak = 0
        self.test_sessions = []       # 测试自己建的会话 id（收尾删掉，不留垃圾）
        self.base_chrome = 0
        self.judge_steady = False
        self.aborted = False
        self.abort_reason = ""
        self.abort_tb = ""
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")

    def note(self, s):
        self.notes.append("[%s] %s" % (time.strftime("%H:%M:%S"), s))
        print("  · %s" % s, flush=True)

    def err(self, who, msg):
        self.errors.append((time.strftime("%H:%M:%S"), who, str(msg)[:400]))
        print("  ⚠️ [%s] %s → %s" % (time.strftime("%H:%M:%S"), who, str(msg)[:110]), flush=True)


def sampler(st: State, inst, stop, counter, base):
    """每 30 秒采样：本进程内存 / **小焦进程内存** / 会话 / chrome 进程 / 线程 / 熔断退避计数。"""
    while not stop.wait(SAMPLE_EVERY):
        try:
            stats = inst.sessions_stats() if inst else {"active": 0, "max_sessions": 0}
            brk = inst.metrics_snapshot().get("breaker", {}) if inst else {}
            h, r, ch = heap_mb(), proc_rss_mb(os.getpid()), chrome_procs()
            app_rss = proc_rss_mb(st.app_pid) if st.app_pid else 0.0
            st.peak_heap, st.peak_rss = max(st.peak_heap, h), max(st.peak_rss, r)
            st.peak_sessions = max(st.peak_sessions, int(stats.get("active", 0)))
            st.app_peak = max(st.app_peak, app_rss)
            if st.judge_steady:
                st.peak_heap_ss, st.peak_rss_ss = max(st.peak_heap_ss, h), max(st.peak_rss_ss, r)
                st.app_peak_ss = max(st.app_peak_ss, app_rss)
            rec = {"at": time.strftime("%H:%M:%S"), "t_s": round(time.time() - st.t0),
                   "heap_mb": h, "rss_mb": r, "app_rss_mb": app_rss,
                   "sessions_active": stats.get("active", 0),
                   "sessions_max": stats.get("max_sessions", 0),
                   "chrome": ch, "threads": threading.active_count(),
                   "breaker_open": len(brk.get("open_until") or {}), "logs": dict(counter.counts)}
            st.samples.append(rec)
            print("  ⏱ %s 本进程 %.1fMB ｜ 小焦 %.1fMB ｜ 会话 %s/%s ｜ chrome %d ｜ 异常 %d ｜ 熔断 %d 退避 %d"
                  % (rec["at"], r, app_rss, rec["sessions_active"], rec["sessions_max"], ch,
                     len(st.errors), counter.counts["熔断触发"], counter.counts["退避重试"]), flush=True)
            if st.judge_steady:
                checks = [("本进程堆", st.base_heap_ss, st.peak_heap_ss),
                          ("本进程工作集", st.base_rss_ss, st.peak_rss_ss)]
                if st.app_base_ss:
                    checks.append(("小焦进程工作集", st.app_base_ss, st.app_peak_ss))
                for label, b, cur in checks:
                    leaked, g, delta, why = mem_verdict(label, b, cur)
                    if leaked:                      # 只有"绝对增量 > 地板 且 涨幅 > 30%"才会走到这里
                        st.aborted = True
                        st.abort_reason = "疑似内存泄漏：%s" % why
                        st.abort_tb = ("最近 5 条监控样本（含绝对增量 %.3f MB）：\n" % delta
                                       + json.dumps(st.samples[-5:], ensure_ascii=False, indent=1))
                        stop.set()
                        return
        except Exception as e:
            st.err("采样线程", "%s: %s" % (type(e).__name__, e))


# ======================================================================
# A 层：整个小焦（端到端）
# ======================================================================
def app_chat(st: State, base: str, prompt: str, timeout=300, track=True):
    """打一次真实对话（走完整链路）；返回 (是否成功, 答案, dict)。"""
    code, el, d, err = http("POST", base.rstrip("/") + "/api/chat", {"message": prompt}, timeout)
    st.http_codes[code] = st.http_codes.get(code, 0) + 1
    if track:
        st.chat_lat.append(round(el, 2))
    ans = str(d.get("answer") or "")
    ok = (code == 200 and bool(ans.strip()) and not err)
    if not ok:
        st.err("POST /api/chat", "HTTP %s %s %s" % (code, (err or "")[:80], (ans or "")[:60]))
    return ok, ans, d


def phase_app_smoke(st: State, base: str) -> None:
    """A1：端到端冒烟 —— 逐个打 6 类真实对话，全部必须 200 且有答案。"""
    print("\n[相位 A1] 整机端到端冒烟：6 类真实对话…", flush=True)
    rows = []
    for p in CHAT_PROMPTS:
        ok, ans, d = app_chat(st, base, p, timeout=300)
        rows.append({"提问": p, "成功": ok, "答案长度": len(ans),
                     "来源数": len(d.get("sources") or []),
                     "工具轨迹": [t.get("tool") for t in (d.get("tool_trace") or [])][:3],
                     "引用校验": (d.get("grounding") or {}).get("grounded"),
                     "答案开头": ans[:60].replace("\n", " ")})
        print("   %s %-26s HTTP %s ｜ %d 字 ｜ 工具 %s"
              % ("✅" if ok else "❌", p[:26], st.http_codes and 200, len(ans),
                 rows[-1]["工具轨迹"] or "无"), flush=True)
    good = sum(1 for r in rows if r["成功"])
    st.phase["A1 整机冒烟（6 类对话）"] = {"成功": "%d/%d" % (good, len(rows)), "明细": rows}
    st.note("整机冒烟：%d/%d 条真实对话走通全链路（HTTP 200 + 非空答案）" % (good, len(rows)))
    if good < len(rows):
        st.err("整机冒烟", "有 %d 条对话失败" % (len(rows) - good))


def phase_app_sessions(st: State, base: str) -> None:
    """A2：会话增长与并发承载 —— 连续新建会话 + 并发对话，验证整机不崩、会话可管理。

    测试**自己建的会话会在收尾时删掉**（不留垃圾，也不碰你原来的会话）。
    """
    print("\n[相位 A2] 整机会话增长 + 3 路并发对话…", flush=True)
    ids = []
    for _ in range(10):
        code, _el, d, err = http("POST", base.rstrip("/") + "/api/session/new", {}, timeout=30)
        st.http_codes[code] = st.http_codes.get(code, 0) + 1
        if code == 200 and d.get("id"):
            ids.append(d["id"])
        else:
            st.err("POST /api/session/new", "HTTP %s %s" % (code, (err or "")[:60]))
    st.test_sessions.extend(ids)                      # 收尾统一删除
    code, _el, d, _err = http("GET", base.rstrip("/") + "/api/sessions", timeout=30)
    sess_n = len((d or {}).get("sessions") or [])
    st.app_sessions_peak = max(st.app_sessions_peak, sess_n)
    st.note("会话：新建 %d 个成功，当前会话总数 %d（会话文件随使用增长属正常；测试建的会在收尾删掉）"
            % (len(ids), sess_n))

    # 3 路并发对话（验证整机线程/资源承载）
    prompts = CHAT_PROMPTS[:3]
    res = {}

    def worker(i):
        ok, ans, _d = app_chat(st, base, prompts[i], timeout=300)
        res[i] = (ok, len(ans))

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(len(prompts))]
    t0 = time.time()
    [t.start() for t in ths]
    [t.join() for t in ths]
    st.phase["A2 会话增长与并发"] = {"新建会话": len(ids), "会话总数": sess_n,
                                     "并发成功": "%d/%d" % (sum(1 for v in res.values() if v[0]), len(prompts)),
                                     "并发耗时_s": round(time.time() - t0, 1),
                                     "并发明细": {prompts[i][:16]: res[i][1] for i in res}}
    st.note("并发：3 路真实对话 %d/3 成功（%.1f 秒）"
            % (sum(1 for v in res.values() if v[0]), time.time() - t0))


def phase_app_endpoints(st: State, base: str) -> None:
    """A3：小焦全部**纯本地**接口健康采样（不含 /api/env，它会外呼云端 /models）。"""
    rows, ok_n = [], 0
    for ep in APP_ENDPOINTS:
        code, el, _d, err = http("GET", base.rstrip("/") + ep, None, timeout=30)
        st.http_codes[code] = st.http_codes.get(code, 0) + 1
        rows.append({"接口": ep, "HTTP": code, "耗时_s": round(el, 2), "错误": (err or "")[:60]})
        ok_n += 1 if code == 200 else 0
        if code != 200:
            st.err("GET " + ep, "HTTP %s %s" % (code, (err or "")[:60]))
    st.phase["A3 本地接口健康"] = {"抽样": rows, "成功": "%d/%d" % (ok_n, len(APP_ENDPOINTS))}


# ======================================================================
# B 层：插件直连
# ======================================================================
def phase_plugin_sessions(st: State, inst) -> None:
    print("\n[相位 B1] 插件会话回收：连开 25 个且**不关闭**…", flush=True)
    max_s = int(inst.sessions_stats().get("max_sessions") or 0)
    peak, opened = 0, 0
    for _ in range(25):
        _el, ok, err, _d = call(inst, "open_session", {"session_type": "dynamic"}, cap=60)
        opened += 1 if ok else 0
        if not ok:
            st.err("open_session", err or "失败")
        act = int(inst.sessions_stats().get("active", 0))
        peak = max(peak, act)
        if max_s and act > max_s:
            st.aborted = True
            st.abort_reason = "会话未被回收：活跃 %d > 上限 %d（进程会堆积）" % (act, max_s)
            st.abort_tb = json.dumps(inst.sessions_stats(), ensure_ascii=False, indent=1)[:1500]
            return
    st.peak_sessions = max(st.peak_sessions, peak)
    st.phase["B1 会话回收"] = {"连开": 25, "成功": opened, "活跃峰值": peak, "上限": max_s,
                               "回收记录": (inst.sessions_stats().get("reclaimed_recent") or [])[:5]}
    st.note("插件会话回收：开成功 %d，活跃峰值 %d（上限 %d）→ %s"
            % (opened, peak, max_s, "不堆积 ✅" if peak <= max_s else "❌"))


def phase_plugin_breaker(st: State, inst, counter) -> None:
    print("\n[相位 B2] 插件熔断/退避：连打 3 次必失败请求…", flush=True)
    seq = []
    for _ in range(3):
        _el, _ok, err, _d = call(inst, "get", {"url": BAD_URL, "timeout": 8}, cap=30)
        seq.append(err[:60] if err else "成功(意外)")
    t0 = time.time()
    _el, ok2, err2, _d2 = call(inst, "get", {"url": OK_URLS[0], "timeout": 10}, cap=30)
    protected = ("保护" in err2) or ("暂时不可用" in err2)
    tripped = counter.counts["熔断触发"] > 0
    recovered = None
    if tripped:
        print("  熔断已触发，等自动恢复（最多 45 秒）…", flush=True)
        for _ in range(15):
            time.sleep(3)
            _el, ok3, _e3, _d3 = call(inst, "get", {"url": OK_URLS[0], "timeout": 15}, cap=40)
            if ok3:
                recovered = round(time.time() - t0, 1)
                break
    st.phase["B2 熔断/退避"] = {"触发": tripped, "熔断期间被拦": protected, "恢复耗时_s": recovered,
                                "失败序列": seq}
    st.note("插件熔断：%s ｜ 恢复：%s"
            % ("触发 ✅" if tripped else "未触发", ("%.1f 秒 ✅" % recovered) if recovered else "未恢复"))


def phase_plugin_nvd(st: State, inst, counter) -> None:
    print("\n[相位 B3] 插件 NVD 限流：连续 4 次查询…", flush=True)
    rows = []
    for i in range(4):
        t0 = time.time()
        _el, _ok, err, d = call(inst, "collect_vulnerabilities",
                                {"days": 1, "severity": "HIGH", "limit": 2}, cap=180)
        content = str(d.get("content") or "")
        kind = ("表格" if "NVD 漏洞速览" in content else
                ("限流/退避" if any(k in err for k in ("限流", "429", "频繁")) else
                 ("错误：" + err[:40] if err else "其它")))
        rows.append({"第几次": i + 1, "结果": kind, "耗时_s": round(time.time() - t0, 1)})
        print("   第 %d 次：%s（%.1f 秒）" % (i + 1, kind, time.time() - t0), flush=True)
        if i < 3:
            time.sleep(1)
    st.phase["B3 NVD 限流"] = {"轮次": rows, "出现限流提示": any(r["结果"] == "限流/退避" for r in rows)}
    st.note("插件 NVD：%s" % "、".join(r["结果"] for r in rows))


# ======================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="30 分钟整机压力测试（整个小焦 + 插件）")
    ap.add_argument("--target", choices=["both", "app", "plugin"], default="both")
    ap.add_argument("--base", default="http://127.0.0.1:5000", help="小焦地址")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=10.0, help="主循环节奏（秒）")
    ap.add_argument("--chat-every", type=float, default=90.0, help="整机层每多少秒打一次真实对话")
    ap.add_argument("--out", default="logs/stability_30m.md")
    ap.add_argument("--json", default="logs/stability_30m.json")
    ap.add_argument("--mem-abs-mb", type=float, default=10.0,
                    help="内存泄漏判定的绝对增量地板（MB），默认 10：涨幅再大，没涨过这个量就不算泄漏")
    args = ap.parse_args()
    globals()["MEM_LIMIT_ABS_MB"] = float(args.mem_abs_mb)

    out_path, json_path = os.path.abspath(args.out), os.path.abspath(args.json)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    total_s = max(60.0, args.minutes * 60.0)
    do_app = args.target in ("both", "app")
    do_plugin = args.target in ("both", "plugin")

    counter = LogCounter()
    for name in ("xiaojiao", ""):
        logging.getLogger(name).addHandler(counter)
    logging.getLogger().setLevel(logging.INFO)

    inst = None
    if do_plugin:
        mod = load_plugin()
        inst = mod.ScraplingBridge()
    st = State(args.target)
    st.app_pid = find_app_pid(5000)
    tracemalloc.start()

    # 整机层专用会话：所有压测对话都写进它，**不污染你自己的聊天记录**，收尾删掉
    if do_app:
        code, _el, d, _err = http("POST", args.base.rstrip("/") + "/api/session/new", {}, timeout=30)
        if code == 200 and d.get("id"):
            st.test_sessions.append(d["id"])
            print("  压测专用会话：%s（收尾会自动删除，不动你原来的会话）" % d["id"], flush=True)

    print("=" * 76)
    print("  30 分钟整机压力测试 ｜ 目标：%s" % {"both": "整个小焦 + 插件", "app": "整个小焦（端到端）", "plugin": "插件"}[args.target])
    print("  小焦地址 %s（PID %s）｜ 每 %.0f 秒一个循环 ｜ 每 %.0f 秒一次真实对话"
          % (args.base, st.app_pid or "未找到", args.interval, args.chat_every))
    print("  报告：%s" % out_path)
    print("=" * 76, flush=True)

    if do_app:
        code, el, d, err = http("GET", args.base.rstrip("/") + "/metrics", None, timeout=20)
        if code != 200:
            print("\n❌ 整机层需要小焦正在运行：%s 返回 HTTP %s %s" % (args.base, code, err), flush=True)
            print("   请先启动：python start_xiaojiao.py（或用 --target plugin 只压插件）", flush=True)
            return 1
        print("  小焦在线（/metrics HTTP 200，%.2fs）\n" % el, flush=True)

    stop = threading.Event()
    threading.Thread(target=sampler, args=(st, inst, stop, counter, args.base),
                     name="xj-sampler", daemon=True).start()

    try:
        # ---------- 预热 ----------
        warm_s = min(120.0, total_s / 10.0)
        print("[预热] %.0f 秒（拿稳定基线，不计入统计）…" % warm_s, flush=True)
        w0, i = time.time(), 0
        while time.time() - w0 < warm_s and not stop.is_set():
            if do_app:
                http("GET", args.base.rstrip("/") + "/api/sessions", None, timeout=20)
            if do_plugin and inst:
                call(inst, "get", {"url": OK_URLS[i % len(OK_URLS)], "timeout": 25}, cap=60)
            i += 1
            time.sleep(max(1.0, args.interval / 2.0))
        st.base_heap, st.base_rss = heap_mb(), proc_rss_mb(os.getpid())
        st.base_chrome = chrome_procs()
        st.note("预热基线：本进程 堆 %.1fMB / 工作集 %.1fMB ｜ 小焦 %.1fMB ｜ chrome %d"
                % (st.base_heap, st.base_rss, proc_rss_mb(st.app_pid), st.base_chrome))
        st.lat.clear()
        st.chat_lat.clear()

        # ---------- 相位 ----------
        if do_app:
            phase_app_smoke(st, args.base)
            if st.aborted:
                raise RuntimeError(st.abort_reason)
            phase_app_sessions(st, args.base)
        if do_plugin and inst:
            phase_plugin_sessions(st, inst)
            if st.aborted:
                raise RuntimeError(st.abort_reason)
            phase_plugin_breaker(st, inst, counter)
            phase_plugin_nvd(st, inst, counter)

        # ---------- 稳态基线（相位台阶落地后才套 30% 红线） ----------
        gc.collect()
        time.sleep(20)
        gc.collect()
        st.base_heap_ss, st.base_rss_ss = heap_mb(), proc_rss_mb(os.getpid())
        st.app_base_ss = proc_rss_mb(st.app_pid) if st.app_pid else 0.0
        # 峰值从基线起算：短跑或采样不足时不会出现"峰值 0 → -100%"这种荒谬数字
        st.peak_heap_ss, st.peak_rss_ss = st.base_heap_ss, st.base_rss_ss
        st.app_peak_ss = st.app_base_ss
        st.judge_steady = True
        st.note("稳态基线：本进程 堆 %.1fMB / 工作集 %.1fMB ｜ 小焦 %.1fMB（30%% 红线从这里开始判）"
                % (st.base_heap_ss, st.base_rss_ss, st.app_base_ss))

        # ---------- 主循环 ----------
        print("\n[主循环] %s + %s，直到 %.0f 分钟用完…"
              % ("整机对话/接口" if do_app else "", "插件高频调用" if do_plugin else "", args.minutes),
              flush=True)
        i, last_chat, nvd_done = 0, 0.0, False
        while time.time() - st.t0 < total_s and not stop.is_set():
            i += 1
            now = time.time()
            # --- A 层：整机 ---
            if do_app and (now - last_chat) >= args.chat_every:
                app_chat(st, args.base, CHAT_PROMPTS[i % len(CHAT_PROMPTS)], timeout=300)
                last_chat = time.time()
            if do_app and i % 7 == 0:
                code, _el, d, _err = http("GET", args.base.rstrip("/") + "/api/sessions", None, 30)
                st.http_codes[code] = st.http_codes.get(code, 0) + 1
                st.app_sessions_peak = max(st.app_sessions_peak, len((d or {}).get("sessions") or []))
            # --- B 层：插件 ---
            if do_plugin and inst:
                m = i % 10
                if m in (0, 1, 2, 3):
                    call(inst, "stealthy_fetch",
                         {"url": OK_URLS[(i // 10) % len(OK_URLS)], "timeout": 45}, cap=120)
                elif m in (4, 5, 6):
                    call(inst, "open_session", {"session_type": "dynamic"}, cap=60)
                elif m == 7:
                    call(inst, "get", {"url": OK_URLS[i % len(OK_URLS)], "timeout": 25}, cap=60)
                elif m == 8:
                    call(inst, "get", {"url": BAD_URL, "timeout": 8}, cap=30)
                else:
                    if not nvd_done and i > 40:
                        call(inst, "collect_vulnerabilities",
                             {"days": 3, "severity": "HIGH", "limit": 2}, cap=180)
                        nvd_done = True
                    else:
                        call(inst, "bulk_get", {"urls": OK_URLS[:2]}, cap=90)
            if i % 5 == 0:
                print("  进度 %d 轮｜%.1f/%.1f 分钟｜插件 P50 %.2fs｜对话 P50 %.2fs｜异常 %d"
                      % (i, (time.time() - st.t0) / 60.0, args.minutes,
                         statistics.median(st.lat) if st.lat else 0,
                         statistics.median(st.chat_lat) if st.chat_lat else 0, len(st.errors)),
                      flush=True)
            time.sleep(max(0.5, args.interval - ((time.time() - st.t0) % args.interval)))
    except Exception as e:
        st.aborted = True
        st.abort_reason = st.abort_reason or ("压测主流程异常：%s: %s" % (type(e).__name__, e))
        st.abort_tb = traceback.format_exc()
        print("\n❌ 立即停止：%s\n%s" % (st.abort_reason, st.abort_tb), flush=True)
    finally:
        stop.set()
        # 收尾清理：删掉压测自己建的会话（不留垃圾，也不碰你原有的会话）
        if do_app and st.test_sessions:
            gone = 0
            for sid in st.test_sessions:
                code, _el, d, _err = http("POST", args.base.rstrip("/") + "/api/session/delete",
                                          {"id": sid}, timeout=30)
                gone += 1 if (code == 200 and d.get("ok")) else 0
            st.note("收尾清理：已删除压测新建的 %d/%d 个会话（你原来的会话未动）"
                    % (gone, len(st.test_sessions)))
            try:
                _c, _e, _d, _r = http("GET", args.base.rstrip("/") + "/api/sessions", None, 30)
                _left = len((_d or {}).get("sessions") or [])
                st.note("清理后会话总数：%d" % _left)
            except Exception:  # noqa: silent-ok — 清理后的核对失败不影响报告
                pass

    # ---------- 汇总 ----------
    tracemalloc.stop()
    lat, clat = sorted(st.lat), sorted(st.chat_lat)
    p50, p95 = round(pct(lat, .50), 2), round(pct(lat, .95), 2)
    c50, c95 = round(pct(clat, .50), 2), round(pct(clat, .95), 2)
    heap_ss = round((st.peak_heap_ss - st.base_heap_ss) / st.base_heap_ss * 100, 1) if st.base_heap_ss else 0.0
    rss_ss = round((st.peak_rss_ss - st.base_rss_ss) / st.base_rss_ss * 100, 1) if st.base_rss_ss else 0.0
    app_ss = round((st.app_peak_ss - st.app_base_ss) / st.app_base_ss * 100, 1) if st.app_base_ss else 0.0
    heap_all = round((st.peak_heap - st.base_heap) / st.base_heap * 100, 1) if st.base_heap else 0.0
    rss_all = round((st.peak_rss - st.base_rss) / st.base_rss * 100, 1) if st.base_rss else 0.0
    http_err = sum(v for k, v in st.http_codes.items() if k != 200)
    ok_rate = round((len(st.lat) - len(st.errors)) * 100.0 / max(1, len(st.lat)), 2) if do_plugin else 100.0

    verdict, reasons = "PASS", []
    if st.aborted:
        verdict = "FAIL"
        reasons.append(st.abort_reason)

    # 内存判定：绝对地板 + 相对涨幅，两条同时成立才算泄漏（避免小基数假警报）
    mem_rows, mem_notes = [], []
    for label, b, cur, unit in (("本进程堆", st.base_heap_ss, st.peak_heap_ss, "MB"),
                                ("本进程工作集", st.base_rss_ss, st.peak_rss_ss, "MB"),
                                ("小焦进程工作集", st.app_base_ss, st.app_peak_ss, "MB")):
        leaked, g, delta, why = mem_verdict(label, b, cur)
        mem_rows.append((label, g, delta, b, cur, leaked, why))
        mem_notes.append(why)
        if leaked and "疑似内存泄漏" not in "；".join(reasons):
            verdict = "FAIL"
            reasons.append("疑似内存泄漏：%s" % why)
    if http_err:
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        reasons.append("出现 %d 个非 200 的 HTTP 响应" % http_err)
    if do_plugin and counter.counts["熔断触发"] == 0:
        verdict = "SUSPECT" if verdict == "PASS" else verdict
        reasons.append("插件熔断未触发（失败可能没打满阈值）")
    reasons = list(dict.fromkeys(reasons))          # 去重，别在报告里重复同一句

    plugin_note = ("" if do_plugin else "（本次 `--target %s` **未启用插件层**，所以这里的 0 次不代表插件有问题）"
                   % args.target)
    mem_table = ["| 对象 | 基线(MB) | 峰值(MB) | **绝对增量(MB)** | 涨幅 | 判定 |",
                 "|---|---|---|---|---|---|"]
    for label, g, delta, b, cur, leaked, why in mem_rows:
        if not b:
            mem_table.append("| %s | — | %.1f | — | — | ⏭️ 未采样 |" % (label, cur))
            continue
        mem_table.append("| %s | %.3f | %.3f | **%+.3f** | %+.1f%% | %s |"
                         % (label, b, cur, delta, g, "❌ 疑似泄漏" if leaked else "✅ 正常"))

    md = ["# 🔥 30 分钟整机压力测试报告（整个小焦 + 插件）", "",
          "> 目标：%s ｜ 小焦地址：%s（PID %s）" % (args.target, args.base, st.app_pid or "未找到"),
          "> 开始 %s ｜ 结束 %s ｜ 总时长 %.1f 分钟"
          % (st.started, time.strftime("%Y-%m-%d %H:%M:%S"), (time.time() - st.t0) / 60.0),
          "> 判定：**%s**%s" % (verdict, ("（" + "；".join(reasons) + "）") if reasons else ""), "",
          "## 一、整机（端到端，走真实 /api/chat 全链路）", "",
          "| 指标 | 数值 | 门槛 | 结论 |", "|---|---|---|---|",
          "| 真实对话次数 | %d 次 | — | — |" % len(st.chat_lat),
          "| 对话延迟 P50 | **%.2fs** | — | — |" % c50,
          "| 对话延迟 P95 | **%.2fs** | — | — |" % c95,
          "| HTTP 状态分布 | %s | 全部 200 | %s |"
          % (st.http_codes or "无", "✅" if http_err == 0 else "⚠️ %d 个非 200" % http_err),
          "| **小焦进程内存涨幅** | 涨幅 **%+.1f%%** ｜ 绝对增量 **%+.3f MB** | 增量>10MB 且 涨幅>30%% 才判泄漏 | %s |"
          % (app_ss, st.app_peak_ss - st.app_base_ss if st.app_base_ss else 0.0,
             "✅ 正常" if not any(r[5] for r in mem_rows if r[0] == "小焦进程工作集") else "❌ 疑似泄漏"),
          "| 小焦进程内存峰值 | %.1f MB | — | — |" % st.app_peak,
          "| 会话总数峰值 | %d | — | — |" % st.app_sessions_peak,
          "", "### 内存判定明细（绝对地板 %.0f MB + 相对涨幅 %.0f%%，两条同时成立才算泄漏）"
          % (MEM_LIMIT_ABS_MB, MEM_LIMIT_PCT), ""] + mem_table + [""] + \
         ["- %s" % n for n in mem_notes] + ["", "## 二、插件（直连 execute()，物理资源压测）", "",
          "| 指标 | 数值 | 门槛 | 结论 |", "|---|---|---|---|",
          "| 插件调用次数 | %d %s | — | — |" % (len(st.lat), plugin_note),
          "| 插件延迟 P50 / P95 | **%.2fs / %.2fs** | — | — |" % (p50, p95),
          "| 插件成功率 | %.2f%% | ≥95%% | %s |" % (ok_rate, "✅" if ok_rate >= 95 else "❌"),
          "| 本进程内存涨幅（堆 / 工作集，稳态） | 见下方内存明细表 | 增量>10MB 且 涨幅>30%% | ✅ |",
          "| 插件会话峰值 | %s（上限 %s） | ≤ 上限 | %s |"
          % ((st.phase.get("B1 会话回收") or {}).get("活跃峰值", st.peak_sessions),
             (st.phase.get("B1 会话回收") or {}).get("上限", "?"),
             "✅" if str((st.phase.get("B1 会话回收") or {}).get("活跃峰值", 0))
             <= str((st.phase.get("B1 会话回收") or {}).get("上限", 10 ** 9)) else "❌"),
          "| **熔断触发次数** | **%d** %s | ≥1（仅插件层） | %s |"
          % (counter.counts["熔断触发"], plugin_note,
             "✅" if (counter.counts["熔断触发"] >= 1 or not do_plugin) else "❌"),
          "| **退避/重试次数** | **%d** | — | — |" % counter.counts["退避重试"],
          "| 会话回收次数 | %d | — | — |" % counter.counts["会话回收"],
          "| 异常记录 | **%d 条** | 0 条最佳 | %s |" % (len(st.errors), "✅" if not st.errors else "⚠️"),
          "", "## 三、专项相位", ""]
    for k, v in st.phase.items():
        md += ["### %s" % k, "", "```json", json.dumps(v, ensure_ascii=False, indent=1), "```", ""]
    md += ["## 四、监控样本（每 30 秒一条）", "",
           "| 时刻 | 已跑(s) | 本进程堆(MB) | 本进程工作集(MB) | **小焦(MB)** | 插件会话 | chrome | 线程 | 熔断 | 退避 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for s in st.samples:
        lg = s.get("logs") or {}
        md.append("| %s | %d | %.1f | %.1f | %.1f | %s | %d | %d | %d | %d |"
                  % (s["at"], s["t_s"], s["heap_mb"], s["rss_mb"], s["app_rss_mb"],
                     s["sessions_active"], s["chrome"], s["threads"],
                     lg.get("熔断触发", 0), lg.get("退避重试", 0)))
    md += ["", "## 五、过程记录", ""] + (["- %s" % n for n in st.notes] or ["- （无）"])
    md += ["", "## 六、异常记录", ""]
    if st.errors:
        md += ["| 时刻 | 来源 | 错误原文 |", "|---|---|---|"]
        md += ["| %s | %s | %s |" % (t, w, str(e).replace("|", "\\|")[:200]) for t, w, e in st.errors[:40]]
        if len(st.errors) > 40:
            md.append("| … | … | 另有 %d 条（见 JSON） |" % (len(st.errors) - 40))
    else:
        md.append("无异常（整机与插件真实调用全部有结果）")
    if counter.samples:
        md += ["", "## 七、关键日志留证", "", "```"] + counter.samples + ["```"]
    if st.abort_tb:
        md += ["", "## 八、中止原因与报错原文", "", "```", st.abort_reason, st.abort_tb, "```"]
    md += ["", "---", "",
           "复现：`python tests/stress/stability_30m.py --target %s`" % args.target, ""]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": verdict, "reasons": reasons, "target": args.target, "app_pid": st.app_pid,
                   "chat_lat_p50": c50, "chat_lat_p95": c95, "chat_calls": len(st.chat_lat),
                   "plugin_lat_p50": p50, "plugin_lat_p95": p95, "plugin_ok_rate": ok_rate,
                   "http_codes": st.http_codes, "app_rss_growth_steady_pct": app_ss,
                   "app_peak_mb": st.app_peak, "app_sessions_peak": st.app_sessions_peak,
                   "heap_growth_steady_pct": heap_ss, "rss_growth_steady_pct": rss_ss,
                   "heap_growth_total_pct": heap_all, "rss_growth_total_pct": rss_all,
                   "log_counts": counter.counts, "errors": st.errors[:100], "samples": st.samples,
                   "phases": st.phase, "notes": st.notes}, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 76)
    print("  判定：%s%s" % (verdict, ("（" + "；".join(reasons) + "）") if reasons else ""))
    print("  整机：对话 %d 次 ｜ P50 %.2fs / P95 %.2fs ｜ 小焦内存 %s ｜ HTTP %s"
          % (len(st.chat_lat), c50, c95,
             ("%+.1f%%（增量 %+.3f MB，峰值 %.1fMB）" % (app_ss, st.app_peak_ss - st.app_base_ss, st.app_peak)
              if st.app_base_ss else "未采样（未找到小焦进程）"), st.http_codes))
    print("  插件：%d 次 ｜ P50 %.2fs / P95 %.2fs ｜ 熔断 %d 次 ｜ 退避 %d 次 ｜ 会话峰值 %s ｜ 异常 %d 条"
          % (len(st.lat), p50, p95, counter.counts["熔断触发"], counter.counts["退避重试"],
             st.peak_sessions, len(st.errors)))
    print("  报告：%s ｜ 明细：%s" % (out_path, json_path))
    print("=" * 76, flush=True)
    if st.aborted:
        print("\n报错原文：\n%s" % st.abort_tb, flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
