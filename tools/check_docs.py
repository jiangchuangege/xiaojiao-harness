# -*- coding: utf-8 -*-
"""文档一致性检查：代码 ↔ 文档，别让文档悄悄过期

为什么要有这个工具：
  文档最容易烂在**没人会手点的地方** —— 相对链接指向已删除的文件、命令里写的脚本不存在、
  文档说"有 /api/xxx 这个接口"但代码里没有、工具名写错了。这里全部用机器核对。

检查四类：
  ① 相对链接（Markdown 语法）指向的文件是否存在；
  ② 正文里 `反引号包起来的仓库路径`（如 `tools/audit_static.py`）是否存在；
  ③ 文档里提到的 `/api/...` 端点是否真在代码里注册（`@app.route`、`add_url_rule`）；
  ④ 工具表里写的工具名是否真在插件/主程序里（只在"这一块表格里出现过真实工具名"时才较真）。

分级：**错误**（链接/路径不存在）会让退出码为 1；**警告**（端点/工具名疑似过期）只提示，
因为文档里出现外部服务接口（llama-swap 的 /v1、ComfyUI 的 /progress）是正常的。

用法：
    python tools/check_docs.py            # 全量检查
    python tools/check_docs.py --quiet    # 只打印问题
退出码：0 = 无错误（警告不拦）；1 = 有链接/路径不存在
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

# CI（GitHub Windows runner）控制台不是 UTF-8，打印中文会 UnicodeEncodeError —— 入口先切成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-ok — 老环境没有 reconfigure 也不该让工具挂掉
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "logs", "books", "downloads", "media", "~", "node_modules", "__pycache__",
             ".github"}
# 这些是"外部依赖 / 运行时产物 / 示例占位"，不要求在仓库里存在
ALLOW_MISSING = (
    "chrome-win64", "ComfyUI", "llama-server", "llama-swap", "xiaojiao_control.json",
    "xiaojiao1.0-4B.gguf", "mini_gpt_model.pth", "vocab.pkl", "model_config.json",
    "ui_chat.png", "results.json", "_metrics.json", "stability_report.json",
    "tool_skills.txt", "knowledge_vec.json", "facts.json", "persona.json",
    "xiaojiao_history.json", "xiaojiao_knowledge_memory.json", "xiaojiao_sessions.json",
    "mybook.md", "valid.json", "xxx.py", "your_plugin.py",
    # 外部项目/外部服务的文件（N.E.K.O. 猫娘、ComfyUI 等）与运行时产物
    "launcher.py", "__init__.py", "robots.txt", "main.py", "plugin.json", "ch1.md",
)
# 表格里第一格是反引号、但其实是参数/字段而不是工具的（白名单，避免误报）
ALLOW_TOOLISH = {
    "save_to", "ignore_robots", "wait_selector", "full_page", "session_type", "session_id",
    "max_sessions", "session_ttl", "session_idle", "rate_limit", "max_retries", "backoff_base",
    "per_domain_limit", "main_content_only", "extraction_type", "css_selector", "timeout",
    "filename", "adaptive", "selector", "action", "url", "urls", "headless", "stealth",
    "proxy", "cookies", "headers", "method", "params", "data", "json", "days", "severity",
    "limit", "query", "num", "path", "content", "name", "text", "items", "force",
    # browser_session 的 action 取值 / 大脑引擎名（它们出现在工具表里但不是工具）
    "open", "open_http", "close", "list", "request", "llama", "auto", "api", "xiaojiao",
}
# 历史记录类文档：里面提到"已删除的文件/旧路径"是正常的，不做存在性较真
HISTORY_DOCS = ("CHANGELOG.md", "docs/landing-report.md", "docs/release-and-rollback.md")


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def md_files() -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if rel.startswith("plugins/xiaojiao-plugins-main"):     # 内置的第三方插件仓库，不是本项目文档
            continue
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _basenames() -> dict:
    idx = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            idx.setdefault(fn, []).append(os.path.join(dirpath, fn))
    return idx


_BASE = None


def _rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _exists(rel_path: str, from_file: str) -> bool:
    """把文档里的相对路径解析成真实路径并判断存在性。

    三级解析：① 相对当前文档 ② 相对仓库根 ③ 按**文件名**全仓唯一匹配
    （文档里常写 `run_all.py` 而实际在 tests/stress/ 下，这不算文档错）。
    """
    global _BASE
    p = rel_path.strip().strip("`").split("#")[0].split("?")[0].strip()
    if not p or p.startswith(("http://", "https://", "mailto:", "#")):
        return True
    if any(a in p for a in ALLOW_MISSING):
        return True
    if p.startswith("/"):                       # 站点绝对路径（如 /media/tts/）不是仓库路径
        return True
    if p.lower().endswith((".md", ".py", ".js", ".json", ".txt")) is False:
        return True
    if _rel(from_file) in HISTORY_DOCS:
        return True
    cands = [os.path.join(os.path.dirname(from_file), p), os.path.join(ROOT, p)]
    if any(os.path.exists(c) for c in cands):
        return True
    if _BASE is None:
        _BASE = _basenames()
    base = os.path.basename(p)
    hits = _BASE.get(base, [])
    if len(hits) == 1:
        return True
    if hits and "/" not in p:                   # 只写文件名（不带目录）时，仓库里能对上就算
        return True
    if hits and "/" in p:                       # 同名多个：目录尾段能对上就算
        tail = p.split("/")[0]
        return any(tail in h.replace("\\", "/") for h in hits)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="文档一致性检查（链接/路径/端点/工具名）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    app_src = io.open(os.path.join(ROOT, "xiaojiao_app.py"), encoding="utf-8", errors="ignore").read()
    plugins = []
    pdir = os.path.join(ROOT, "plugins")
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if fn.endswith(".py"):
                plugins.append(io.open(os.path.join(pdir, fn), encoding="utf-8", errors="ignore").read())
    plugin_src = "\n".join(plugins)
    other_routes = ""
    for sub in ("video_service", "podcast_service", "music_service"):
        d = os.path.join(ROOT, sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if fn.endswith(".py"):
                    other_routes += io.open(os.path.join(d, fn), encoding="utf-8",
                                            errors="ignore").read()

    # 真实端点集合
    route_paths = set(re.findall(r'@app\.route\(\s*["\']([^"\']+)["\']', app_src))
    route_paths |= set(re.findall(r'@\w+\.route\(\s*["\']([^"\']+)["\']', other_routes))
    # 附加服务（命令端点、监控、视频/播客/音乐）与 OpenAI 兼容端点的注册方式也可能是 add_url_rule
    extra_src = ""
    for fn in ("xiaojiao_tools.py", "app_monitor.py", "music_service/music_api.py"):
        fp = os.path.join(ROOT, fn)
        if os.path.exists(fp):
            extra_src += io.open(fp, encoding="utf-8", errors="ignore").read()
    route_paths |= set(re.findall(r'@\w+\.route\(\s*["\']([^"\']+)["\']', extra_src))
    route_paths |= set(re.findall(r'add_url_rule\(\s*["\']([^"\']+)["\']', app_src + extra_src))
    # 工具名集合：插件 T("name") + 主程序 run_tool 的 if name == "xxx"
    tool_names = set(re.findall(r'\bT\(\s*"([a-z_0-9]+)"', plugin_src))
    tool_names |= set(re.findall(r'if name == "([a-z_0-9]+)"', app_src))
    tool_names |= set(re.findall(r'"name":\s*"([a-z_0-9]+)"', plugin_src))

    errors, warns, checked = [], [], 0
    link_re = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
    path_re = re.compile(r"`([A-Za-z0-9_\-./]+\.(?:py|md|json|txt|yml|yaml|js|bat|ps1|sh|lock))`")
    ep_re = re.compile(r"`?(/[a-z][a-z0-9_/]*(?:/[a-z0-9_\-{}]+)*)`?")
    TOOL_RE = re.compile(r"`([a-z][a-z_0-9]{3,})`")

    for f in md_files():
        text = io.open(f, encoding="utf-8", errors="ignore").read()
        rel_f = _rel(f)
        for m in link_re.finditer(text):
            checked += 1
            if not _exists(m.group(1), f):
                errors.append("%s: 链接指向不存在的文件 → %s" % (rel_f, m.group(1)))
        for m in path_re.finditer(text):
            checked += 1
            if not _exists(m.group(1), f):
                errors.append("%s: 提到的路径不存在 → %s" % (rel_f, m.group(1)))
        # 端点：只对**本仓库自己的 /api/... 命名空间**较真，外部服务（/v1、/progress、/models）
        # 出现在文档里是正常的，不当问题
        for m in re.finditer(r"`(/api/[A-Za-z0-9_/]+)`", text):
            p = m.group(1).rstrip("/")
            if not p:
                continue
            checked += 1
            if p in route_paths or any(r == p or r.startswith(p + "/") for r in route_paths):
                continue
            warns.append("%s: 提到的接口 %s 未在本仓库路由表找到（可能是外部服务）" % (rel_f, p))
        # 工具表校验：先把相邻的表格行攒成块，**只有当这一块里出现过真实工具名**时才较真
        # （否则那些写模型参数的表 —— vocab_size/base_url 之类 —— 会被误当成工具）
        block = []

        def _flush_block(rows):
            joined = "\n".join(rows)
            if not any(re.search(r"`%s`" % re.escape(t), joined) for t in tool_names):
                return
            for line in rows:
                mt = re.match(r"\|\s*(?:🆕\s*)?`([a-z][a-z_0-9]{3,})`", line.strip())
                if not mt:
                    continue
                checked_local[0] += 1
                nm = mt.group(1)
                if nm not in tool_names and nm not in ALLOW_TOOLISH:
                    warns.append("%s: 工具表里写了 `%s`，但代码里没有这个工具" % (rel_f, nm))

        checked_local = [0]
        for line in text.splitlines():
            if line.strip().startswith("|"):
                block.append(line)
                continue
            if block:
                _flush_block(block)
                block = []
        if block:
            _flush_block(block)

    if not args.quiet:
        print("检查 %d 个文档、%d 项断言（链接/路径/端点/工具名）" % (len(md_files()), checked))
        print("代码实况：@app.route %d 条、插件工具 %d 个" % (len(route_paths), len(tool_names)))
    for e in errors:
        print("  ❌ %s" % e)
    for w in warns[:15]:
        print("  ⚠️ %s" % w)
    if len(warns) > 15:
        print("  … 另有 %d 条警告" % (len(warns) - 15))
    print("结论：错误 %d · 警告 %d" % (len(errors), len(warns)))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
