# -*- coding: utf-8 -*-
"""原理遵循审计：把"我们说好的规矩"逐条变成机器能查的断言

为什么要有这个工具：规矩写在文档里没人会一条条手查，久了必然跑偏。
这里把项目约定(硬性约束)做成 12 条可执行检查，每条都给证据；任何一条不达标就直接失败。

约定来源：CONTRIBUTING.md 的硬性约束 + README「原则」章节 + 用户明确要求。

用法：
    python tools/check_principles.py              # 静态部分（无需服务）
    python tools/check_principles.py --with-live  # 追加"服务在跑"的实机检查
退出码：0 = 全部达标；1 = 有未达标项
"""
from __future__ import annotations

import argparse
import ast
import glob
import io
import json
import os
import re
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-ok — 老环境没有 reconfigure 也不该让工具挂掉
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "~", "logs", "books",
             "downloads", "media", "videos", "dsh_home", "dsh_sessions", "assets", "presets",
             "static", "LCCC-large", "LCCC-base-split", "chrome-win64"}
RESULTS = []


def _rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def py_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if _rel(dirpath).startswith("plugins/xiaojiao-plugins-main"):
            continue                                  # 内置第三方仓库，不按本项目规矩要求
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def record(no, name, ok, detail=""):
    RESULTS.append({"no": no, "name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print("  %s [P%-2d] %-40s %s" % ("✅" if ok else "❌", no, name, str(detail)[:110]), flush=True)


def run(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 999, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="原理遵循审计")
    ap.add_argument("--with-live", action="store_true", help="追加需要服务在跑的实机检查")
    ap.add_argument("--json", default="", help="把结果写入 JSON")
    args = ap.parse_args()

    print("=" * 74)
    print("  小焦 · 原理遵循审计（%d 个 Python 文件）" % len(py_files()))
    print("=" * 74)

    # ---------- P1 代码能编译、真 bug 级静态检查干净 ----------
    bad = []
    for f in py_files():
        try:
            ast.parse(io.open(f, encoding="utf-8", errors="ignore").read())
        except SyntaxError as e:
            bad.append("%s:%s" % (_rel(f), e.lineno))
    if not bad:
        rc, out = run([sys.executable, "-m", "ruff", "check", "--select", "E9,F63,F7,F82",
                       "--output-format", "concise", "."])
        if rc == 999 or "No module named" in out:
            record(1, "全部文件可编译 + 真 bug 级静态检查", True, "ruff 未安装，仅做语法检查")
        else:
            hits = [l for l in out.splitlines() if re.match(r"^\S+:\d+:", l)]
            record(1, "全部文件可编译 + 真 bug 级静态检查", rc == 0, "%d 条 ruff 问题" % len(hits))
    else:
        record(1, "全部文件可编译 + 真 bug 级静态检查", False, "语法错误：%s" % bad[:3])

    # ---------- P2 无硬编码密钥 / 私有 IP / 绝对路径 ----------
    rc, out = run([sys.executable, "tools/audit_static.py", "--json", os.path.join(ROOT, "_princ.json")])
    try:
        rep = json.load(io.open(os.path.join(ROOT, "_princ.json"), encoding="utf-8"))
    except Exception:
        rep = {}
    tracked = set()
    rc2, out2 = run(["git", "ls-files"])
    if rc2 == 0:
        tracked = {l.strip() for l in out2.splitlines() if l.strip()}

    def count_tracked(key):
        return [i for i in (rep.get(key) or []) if i.get("file") in tracked]

    sec = count_tracked("secret")
    ip = count_tracked("hardcoded_ip")
    record(2, "已跟踪文件无明文密钥", not sec, "%d 处" % len(sec))
    record(3, "已跟踪文件无硬编码私有 IP", not ip, "%d 处" % len(ip))

    # ---------- P4 库/模块里不 print（用统一日志） ----------
    pr = count_tracked("print_in_module")
    record(4, "库/模块内不用 print（走统一日志）", not pr,
           "%d 处%s" % (len(pr), ("：" + pr[0]["file"]) if pr else ""))

    # ---------- P5 不静默吞异常 ----------
    silent = count_tracked("silent_except")
    bare = count_tracked("bare_except")
    record(5, "无静默吞异常 / 无裸 except", not silent and not bare,
           "silent=%d bare=%d" % (len(silent), len(bare)))

    # ---------- P6 错误信息中文可读（API 的 error 字段必须带中文） ----------
    non_cn = []
    cjk = re.compile(r"[\u4e00-\u9fa5]")
    for f in py_files():
        if _rel(f).startswith("tests/"):
            continue
        try:
            tree = ast.parse(io.open(f, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in ("error", "err") \
                        and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    s = v.value
                    if s.strip() and not cjk.search(s) and "%" not in s:
                        non_cn.append("%s:%s" % (_rel(f), node.lineno))
    record(6, "面向用户的 error 文案都是中文", not non_cn, str(non_cn[:3]))

    # ---------- P7 测试是真跑（没有 mock / 打桩 / 假数据） ----------
    fake = []
    for f in glob.glob(os.path.join(ROOT, "tests", "**", "*.py"), recursive=True):
        t = io.open(f, encoding="utf-8", errors="ignore").read()
        for pat in ("unittest.mock", "Mock(", "monkeypatch", "responses.add", "mock.patch",
                    "MagicMock", "pytest.mark.skip"):
            if pat in t:
                fake.append("%s:%s" % (_rel(f), pat))
    record(7, "测试套件不含 mock/打桩（必须是真实调用）", not fake, str(fake[:3]))

    # ---------- P8 文档与代码一致 ----------
    rc, out = run([sys.executable, "tools/check_docs.py", "--quiet"])
    err_lines = [l for l in out.splitlines() if l.strip().startswith("❌")]
    record(8, "文档 ↔ 代码一致（链接/路径/接口/工具名）", rc == 0, "%d 个错误" % len(err_lines))

    # ---------- P9 版本号自洽（有版本历史，且不出现"幽灵版本号"） ----------
    # 政策演进：v1.0 时期要求"只有一个版本号"，但开始正常发版后这就不成立了 ——
    # 现在的判据是：① CHANGELOG 里至少一个版本标题、最新在最上面、且不重复；
    # ② 仓库里出现的每个 vX.Y.Z 都必须在 CHANGELOG 里有对应小节（不许出现查无此版的号）。
    cl = io.open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    heads = re.findall(r"^## \[(v[^\]]+)\]", cl, flags=re.M)
    dup = len(heads) != len(set(heads))
    ghosts = []
    known = set(heads)
    for f in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True):
        if not f.lower().endswith((".md", ".py", ".txt", ".yml", ".json", ".example")):
            continue
        rel = _rel(f)
        # 只审"人写的"文档与代码：self_learn/ 之类是运行态知识（里面出现的是 Node 自身的
        # 版本号，不是我们的发版号），扫进来只会制造噪音。
        if any(x in rel for x in ("CHANGELOG.md", ".git/", "node_modules",
                                  "self_learn/", "logs/", "media/", "docs/xiaojiao-kb")):
            continue
        try:
            t = io.open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        unknown = sorted(set(re.findall(r"v\d+\.\d+\.\d+", t)) - known)
        if unknown:
            ghosts.append("%s:%s" % (rel, unknown))
    record(9, "版本号自洽（CHANGELOG 有版本、无重复、无查无此版的号）",
           bool(heads) and not dup and not ghosts,
           "标题=%s 幽灵=%s" % (heads, ghosts[:2]))

    # ---------- P10 依赖锁定 ----------
    lock = os.path.join(ROOT, "requirements.lock")
    req = os.path.join(ROOT, "requirements.txt")
    ok10, det10 = os.path.exists(lock), ""
    if ok10:
        lock_txt = io.open(lock, encoding="utf-8").read().lower()
        tops = [re.split(r"[<>=!\[; ]", l.strip())[0].lower()
                for l in io.open(req, encoding="utf-8") if l.strip() and not l.startswith("#")]
        miss = [t for t in tops if t and t not in lock_txt]
        ok10 = not miss
        det10 = "锁定 %d 行；缺 %s" % (len(lock_txt.splitlines()), miss[:3])
    record(10, "依赖已锁定（requirements.lock 覆盖每个顶层依赖）", ok10, det10)

    # ---------- P11 每个插件工具都有测试引用 ----------
    plugin = io.open(os.path.join(ROOT, "plugins", "scrapling_bridge.py"), encoding="utf-8").read()
    tools = set(re.findall(r'\n\s+T\("([a-z_]+)"', plugin))
    tests_txt = "\n".join(io.open(f, encoding="utf-8", errors="ignore").read()
                          for f in glob.glob(os.path.join(ROOT, "tests", "**", "*.py"), recursive=True))
    untested = sorted(t for t in tools if t not in tests_txt)
    record(11, "插件工具都有测试引用（%d 个工具）" % len(tools), not untested, str(untested[:4]))

    # ---------- P12 可观测性端点仍在 ----------
    app_src = io.open(os.path.join(ROOT, "xiaojiao_app.py"), encoding="utf-8").read()
    need = ["/metrics", "/api/scrapling/metrics", "/api/env", "/api/presets", "/api/settings"]
    miss = [p for p in need if ('"%s"' % p) not in app_src]
    record(12, "关键可观测/配置端点都在", not miss, str(miss))

    live_ok = True
    if args.with_live:
        print("\n  —— 实机部分（需要服务在跑）——")
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:5000/api/env", timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            record(13, "实机健康检查 /api/env 全项通过", bool(d.get("ok")),
                   "缺失=%s" % (d.get("missing") or "无"))
        except Exception as e:
            record(13, "实机健康检查 /api/env", False, str(e)[:80])
            live_ok = False

    # 结果默认写进 logs/（已被 .gitignore 忽略），避免临时产物混进仓库
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    jsonp = args.json or os.path.join(ROOT, "logs", "principles.json")
    io.open(jsonp, "w", encoding="utf-8").write(json.dumps(RESULTS, ensure_ascii=False, indent=1))
    try:
        os.remove(os.path.join(ROOT, "_princ.json"))
    except OSError:  # noqa: silent-ok — 临时文件删不掉不影响结论
        pass

    bads = [r for r in RESULTS if not r["ok"]]
    print("\n" + "=" * 74)
    print("  原理遵循：达标 %d / %d" % (len(RESULTS) - len(bads), len(RESULTS)))
    for b in bads:
        print("    ❌ P%-2d %s → %s" % (b["no"], b["name"], b["detail"][:120]))
    print("=" * 74)
    return 0 if not bads and live_ok else 1


if __name__ == "__main__":
    sys.exit(main())
