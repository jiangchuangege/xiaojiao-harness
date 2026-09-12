# -*- coding: utf-8 -*-
"""静态代码质量审计（标准库 ast 实现，不需要联网、不需要装 ruff/flake8）

为什么需要：CI/本地都可能没有 lint 工具（例如 pip 不可达时），但坏味道不能不看。
本脚本只依赖标准库，任何环境都能跑。

检查项：
  · 静默吞异常 `except: pass`（可写 `# noqa: silent-ok` 显式豁免）
  · 裸 `except:`
  · 硬编码绝对路径（Windows 盘符 / Unix 家目录）
  · 硬编码私有 IP（排除本机与 0.0.0.0 监听、排除 SSRF 黑名单）
  · 疑似明文密钥（可用 `secret-fixture` 标记测试假数据）
  · 超长函数（>80 行）
  · 库/插件模块里的 print
  · TODO / FIXME / XXX / HACK

用法：
    python tools/audit_static.py                 # 扫描仓库
    python tools/audit_static.py --json out.json # 机读结果
退出码：0（只报告，不阻断；需要阻断可在 CI 里自行判断阈值）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter

# CI（GitHub Windows runner）控制台不是 UTF-8，打印中文会 UnicodeEncodeError —— 入口先切成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-ok — 老环境没有 reconfigure 也不该让工具挂掉
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "~", "logs", "books",
             "downloads", "media", "videos", "dsh_home", "dsh_sessions", "assets", "presets",
             "static", "LCCC-large", "LCCC-base-split"}

PATH_RE = re.compile(r'["\']([A-Za-z]:\\\\[^"\']{3,}|/(?:home|Users|mnt|opt)/[^"\']{3,})["\']')
IP_RE = re.compile(r'["\']((?:\d{1,3}\.){3}\d{1,3})["\']')
SECRET_RE = re.compile(r'(sk-[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})')
TODO_RE = re.compile(r'#\s*(TODO|FIXME|XXX|HACK)\b', re.I)

LIB_PREFIXES = ("plugins/", "video_service/", "podcast_service/", "music_service/")


def scan_file(path: str, rel: str, rep: dict) -> None:
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return
    rep["files_scanned"] += 1
    lines = src.splitlines()

    for i, line in enumerate(lines, 1):
        if PATH_RE.search(line) and "sqlite" not in line.lower():
            rep["hardcoded_path"].append({"file": rel, "line": i, "code": line.strip()[:110]})
        m = IP_RE.search(line)
        if m and m.group(1) not in ("127.0.0.1", "0.0.0.0") and "PRIVATE_HOST_HINTS" not in line \
                and "blocked" not in line.lower():
            rep["hardcoded_ip"].append({"file": rel, "line": i, "code": line.strip()[:110]})
        if SECRET_RE.search(line) and "secret-fixture" not in line and "SECRET_RE" not in line:
            rep["secret"].append({"file": rel, "line": i, "code": line.strip()[:60]})
        if TODO_RE.search(line):
            rep["todo"].append({"file": rel, "line": i, "code": line.strip()[:100]})

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        rep.setdefault("syntax_error", []).append({"file": rel, "error": str(e)})
        return

    has_main = any(isinstance(n, ast.If) for n in tree.body)
    is_lib = rel.startswith(LIB_PREFIXES) or rel in ("xiaojiao_app.py", "xiaojiao_harness.py",
                                                     "brain_manager.py", "xiaojiao_log.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            hline = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            if "silent-ok" in hline:
                continue
            if node.type is None:
                rep["bare_except"].append({"file": rel, "line": node.lineno})
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                rep["silent_except"].append({"file": rel, "line": node.lineno})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > 80:
                rep["long_function"].append({"file": rel, "line": node.lineno,
                                             "name": node.name, "lines": length})
        if is_lib and not has_main and isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "print":
                rep["print_in_module"].append({"file": rel, "line": node.lineno})


def main() -> int:
    ap = argparse.ArgumentParser(description="静态代码质量审计（标准库实现）")
    ap.add_argument("--json", default="", help="把结果写入 JSON 文件")
    args = ap.parse_args()

    rep = {"files_scanned": 0, "bare_except": [], "silent_except": [], "hardcoded_path": [],
           "hardcoded_ip": [], "secret": [], "long_function": [], "print_in_module": [],
           "todo": [], "syntax_error": []}

    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                scan_file(p, os.path.relpath(p, ROOT), rep)

    print("扫描 Python 文件：%d 个\n" % rep["files_scanned"])
    rows = [("静默吞异常 except: pass", "silent_except"), ("裸 except", "bare_except"),
            ("硬编码绝对路径", "hardcoded_path"), ("硬编码私有 IP", "hardcoded_ip"),
            ("疑似明文密钥", "secret"), ("超长函数（>80 行）", "long_function"),
            ("库/模块里的 print", "print_in_module"), ("TODO/FIXME", "todo")]
    for label, key in rows:
        items = rep.get(key) or []
        print("%-24s %d" % (label, len(items)))
        for it in items[:5]:
            extra = it.get("code") or it.get("name") or ""
            print("    %-44s :%-5s %s" % (it.get("file", "?"), it.get("line", "?"), str(extra)[:64]))
        if len(items) > 5:
            print("    … 其余 %d 条见 JSON" % (len(items) - 5))

    by_file = Counter()
    for key in ("bare_except", "silent_except", "hardcoded_path", "hardcoded_ip", "secret",
                "long_function", "print_in_module"):
        for it in rep.get(key) or []:
            by_file[it.get("file", "?")] += 1
    if by_file:
        print("\n问题最多的文件：")
        for f, n in by_file.most_common(8):
            print("    %-46s %d 项" % (f, n))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        print("\n结果已写入：%s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
