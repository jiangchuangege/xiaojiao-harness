# -*- coding: utf-8 -*-
"""安全用例：SSRF / robots / 限速 / 脱敏 / UA / 目录穿越 / 命令端点加固 / 无遥测 / 无明文密钥

**全部离线可跑**（被拦截的请求根本不会发出去；需要联网的项放在 test_network.py）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

from harness import REPO_ROOT, Results, load_plugin

SSRF_TARGETS = [
    "http://127.0.0.1/", "http://127.0.0.1:5000/", "http://10.0.0.1/", "http://10.255.255.1/",
    "http://192.168.1.1/", "http://172.16.0.1/", "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd", "file:///C:/Windows/win.ini", "gopher://127.0.0.1:70/",
    "ftp://192.168.1.1/", "http://localhost:5000/", "http://[::1]/", "http://0.0.0.0/",
    "http://metadata.google.internal/",
    # 数值型/缩写型写法的绕过尝试（安全自测发现十进制写法曾绕过）
    "http://2130706433/", "http://0x7f000001/", "http://127.1/", "http://10.1/",
    "http://017700000001/", "http://192.168.1/",
]


def run(res: Results, mod=None) -> Results:
    mod = mod or load_plugin()

    # ---------- 1. SSRF 拦截矩阵 ----------
    guard = mod.SecurityGuard(rate_limit=0.0)
    leaked = [t for t in SSRF_TARGETS if not guard.check_ssrf(t)]
    res.check("SSRF", "内网/本机/保留地址 100%% 拦截（%d 个）" % len(SSRF_TARGETS),
              not leaked, "漏网：%s" % leaked if leaked else "全部拦截")
    res.check("SSRF", "正常公网地址放行",
              guard.check_ssrf("https://example.com") == "" and guard.check_ssrf("http://example.org/a?b=1") == "",
              "")

    # 经工具入口再验一遍（确保闸门真的挂在调用路径上，而不是只有独立函数管用）
    from harness import Bridge
    b = Bridge(mod)
    tools_blocked = 0
    probe = [("get", {"url": u}) for u in ("http://127.0.0.1/", "file:///etc/passwd", "http://10.0.0.1/")]
    probe += [("download", {"url": "http://169.254.169.254/"}), ("bulk_get", {"urls": ["http://127.0.0.1/"]})]
    for tool, params in probe:
        _, _, err, _ = b.call(tool, params, cap=20)
        if ("SSRF" in err) or ("禁止访问" in err) or ("内网" in err) or ("本机" in err):
            tools_blocked += 1
    res.check("SSRF", "工具入口同样拦截（%d 个探针）" % len(probe), tools_blocked == len(probe),
              "拦截 %d/%d" % (tools_blocked, len(probe)))

    # ---------- 2. robots.txt 按 RFC 9309 判定 ----------
    g2 = mod.SecurityGuard(rate_limit=0.0)
    g2._fetch_robots = lambda root: (["User-agent: *", "Disallow: /private"], "")
    ok_pub, _ = g2.robots_allowed("https://x.example.com/public", mod.USER_AGENT)
    g2._robots.clear()
    ok_priv, why_priv = g2.robots_allowed("https://x.example.com/private/x", mod.USER_AGENT)
    g2._robots.clear()
    ok_ign, _ = g2.robots_allowed("https://x.example.com/private/x", mod.USER_AGENT, True)
    res.check("robots", "允许路径放行 / 禁止路径拦截 / 显式忽略可放行",
              ok_pub and (not ok_priv) and ok_ign, "pub=%s priv=%s ignore=%s" % (ok_pub, ok_priv, ok_ign))

    g3 = mod.SecurityGuard(rate_limit=0.0)
    g3._fetch_robots = lambda root: (None, "该站没有 robots.txt(404) → 放行")
    ok_404, why404 = g3.robots_allowed("https://y.example.com/anything", mod.USER_AGENT)
    res.check("robots", "没有 robots.txt → 放行（不误拦整站）", ok_404, why404)

    g4 = mod.SecurityGuard(rate_limit=0.0)
    g4._fetch_robots = lambda root: (None, "robots.txt 被 WAF 拦(403) → 放行")
    ok_403, why403 = g4.robots_allowed("https://z.example.com/api", mod.USER_AGENT)
    res.check("robots", "robots 被 WAF 403 → 放行（RFC 9309）", ok_403, why403)

    # ---------- 3. 同域限速（真实计时） ----------
    g5 = mod.SecurityGuard(rate_limit=1.0)
    t0 = time.time()
    g5.wait_rate_limit("https://rate.example.com/a")
    g5.wait_rate_limit("https://rate.example.com/b")
    gap = time.time() - t0
    res.check("限速", "同域两次请求间隔 ≥ 1s（实测 %.2fs）" % gap, gap >= 0.9, "gap=%.2fs" % gap)
    t0 = time.time()
    g5.wait_rate_limit("https://other-domain.example.com/a")
    gap2 = time.time() - t0
    res.check("限速", "不同域不互相阻塞（实测 %.2fs）" % gap2, gap2 < 0.3, "gap=%.2fs" % gap2)

    # ---------- 4. 日志脱敏（真写日志再回读文件） ----------
    try:
        from xiaojiao_log import get_logger, scrub
        log = get_logger("stress.security")
        fake = "sk-" + "A" * 24                       # secret-fixture（假）
        log.info("安全自测：密钥 %s 不应出现在日志里", fake)
        for h in __import__("logging").getLogger("xiaojiao").handlers:
            try:
                h.flush()
            except Exception:
                pass
        logfile = os.path.join(REPO_ROOT, "logs", "xiaojiao.log")
        content = open(logfile, encoding="utf-8", errors="ignore").read() if os.path.exists(logfile) else ""
        res.check("脱敏", "写入日志的裸密钥被打码（回读日志文件验证）", fake not in content,
                  "命中" if fake in content else "未命中（已打码）")
        res.check("脱敏", "scrub() 覆盖键值对 + 裸凭据",
                  scrub("api_key=SECRET token:ABC Bearer x.y") .count("***") >= 3, "")
    except ImportError as e:
        res.skip("脱敏", "日志模块", "无法导入 xiaojiao_log: %s" % e)

    # ---------- 5. User-Agent 合规（不伪装爬虫） ----------
    ua = mod.USER_AGENT
    bad_ua = [w for w in ("bot", "spider", "crawler", "slurp", "bingbot", "googlebot") if w in ua.lower()]
    res.check("UA", "UA 不伪装搜索引擎/爬虫", not bad_ua, "UA=%s…" % ua[:60])

    # ---------- 6. 下载：目录穿越与文件名净化 ----------
    gp = mod._guess_filename("https://x.example.com/../../etc/passwd")
    res.check("文件安全", "非法文件名被净化（无 .. 与路径分隔）",
              (".." not in gp) and ("/" not in gp) and ("\\" not in gp), "→ %s" % gp)
    gp2 = mod._guess_filename("https://x.example.com/a%2Fb%2F..%2Fc.txt")
    res.check("文件安全", "URL 编码的穿越也被净化",
              (".." not in gp2) and ("/" not in gp2), "→ %s" % gp2)

    # ---------- 7. 命令类端点加固（源码契约） ----------
    tools_src = open(os.path.join(REPO_ROOT, "xiaojiao_tools.py"), encoding="utf-8").read()
    res.check("命令端点", "默认只监听本机（不暴露到局域网）",
              'os.environ.get("XIAOJIAO_TOOLS_HOST", "127.0.0.1")' in tools_src, "")
    res.check("命令端点", "仅本机客户端可跳过危险确认（force 降级）",
              "and _loopback" in tools_src and "拒绝非本机客户端的 force 请求" in tools_src, "")
    res.check("命令端点", "工具异常不把堆栈抛给调用方（记日志 + 中文）",
              'log.warning("工具 %s 执行异常' in tools_src, "")

    # ---------- 8. 无遥测/外传迹象 ----------
    pat = re.compile(r"telemetry|analytics|sentry|mixpanel|google-analytics|umami|report_usage", re.I)
    suspects = []
    _self = os.path.abspath(__file__)          # 本文件自身含这些关键词（就是用来扫描的），需排除
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "~", "logs", "node_modules"}]
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                if os.path.abspath(p) == _self:
                    continue
                try:
                    if pat.search(open(p, encoding="utf-8", errors="ignore").read()):
                        suspects.append(os.path.relpath(p, REPO_ROOT))
                except OSError:
                    pass
    res.check("数据不外传", "代码中无遥测/上报埋点", not suspects, str(suspects[:3]))

    # ---------- 9. 仓库内无明文密钥（扫描已跟踪文件） ----------
    secret_re = re.compile(r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True,
                                 text=True, encoding="utf-8").stdout.splitlines()
    except Exception:
        tracked = []
    hits = []
    for rel in tracked:
        p = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(p) or rel.endswith((".png", ".jpg", ".pth", ".pkl", ".zip")):
            continue
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for line in text.splitlines():
            if secret_re.search(line) and "secret-fixture" not in line and "SECRET_PATTERNS" not in line:
                hits.append("%s: %s" % (rel, line.strip()[:60]))
    res.check("密钥", "已跟踪文件中无明文密钥（%d 个文件）" % len(tracked), not hits, str(hits[:2]))

    return res
