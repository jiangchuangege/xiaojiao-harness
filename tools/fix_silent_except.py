# -*- coding: utf-8 -*-
"""安全重构工具：把"静默吞异常"改成"记日志 + 明确降级"

背景（阶段 2 静态审计发现）：
    `except: pass` 有 100+ 处、裸 `except:` 19 处。出错时**无声无息**，
    线上排障时完全看不到线索 —— 这是最典型的"看起来稳定、实际不可维护"。

本工具用 AST 精确定位「except 子句体内只有 pass」的位置，改写成：
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, <行号>, e)

安全保证：
    · 只改「body 恰好是 [Pass]」的 handler（有实际逻辑的一律不碰）
    · 保留原缩进；裸 `except:` 一并改为 `except Exception as e:`
    · 文件里没有 LOG 符号时自动注入 `LOG = get_logger(__name__)`（并有 import 兜底）
    · 默认 --dry-run，只打印将要修改的行；确认无误再 --apply
    · 改完自动 py_compile 语法校验，语法不过则回滚该文件

用法：
    python tools/fix_silent_except.py xiaojiao_app.py plugins/scrapling_bridge.py --dry-run
    python tools/fix_silent_except.py xiaojiao_app.py --apply
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import py_compile
import re
import shutil
import sys
import tempfile

MSG = 'LOG.debug("忽略异常(%s:%d): %s", __file__, %d, e)'
LOG_IMPORT = ("import logging  # noqa: F401  （由 tools/fix_silent_except.py 注入）\n"
              "try:\n"
              "    from xiaojiao_log import get_logger\n"
              "except Exception:  # 独立运行时退化为标准 logging\n"
              "    def get_logger(name=None):\n"
              "        return logging.getLogger(name or 'xiaojiao')\n"
              "LOG = get_logger(__name__)\n")


def find_silent_handlers(tree: ast.AST):
    """返回 body 只有 pass 的 ExceptHandler 列表，以及裸 except 列表。"""
    silent, bare = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                bare.append(node)
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                silent.append(node)
    return silent, bare


def fix_file(path: str, apply: bool) -> int:
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print("  ❌ 语法错误，跳过: %s (%s)" % (path, e))
        return 0

    silent, bare = find_silent_handlers(tree)
    if not silent:
        print("  · %s：无静默吞异常" % path)
        return 0

    changes = []
    for h in silent:
        pass_node = h.body[0]
        idx = pass_node.lineno - 1                      # 0-based
        indent = re.match(r"\s*", lines[idx]).group(0)
        new_line = indent + (MSG % ("%s", h.lineno, "%s", h.lineno))
        changes.append((idx, new_line, h))
        # 裸 except 需要同时改 except 行
        if h.type is None:
            eidx = h.lineno - 1
            eindent = re.match(r"\s*", lines[eidx]).group(0)
            changes.append((eidx, eindent + "except Exception as e:", h))
        elif h.name is None:
            # except Exception: → 需要绑定 e
            eidx = h.lineno - 1
            eindent = re.match(r"\s*", lines[eidx]).group(0)
            old = lines[eidx].strip()
            new = old if old.endswith("as e:") else re.sub(r":\s*$", " as e:", old)
            changes.append((eidx, eindent + new, h))

    # 按行号倒序替换，避免行号漂移
    out = list(lines)
    for idx, text, _h in sorted(changes, key=lambda c: -c[0]):
        out[idx] = text

    # 需要 LOG 符号吗？
    if changes and "get_logger" not in src:
        # 插到首个 import 之后
        insert_at = 0
        for i, l in enumerate(out[:80]):
            if l.startswith(("import ", "from ")):
                insert_at = i + 1
        out[insert_at:insert_at] = LOG_IMPORT.rstrip("\n").split("\n")

    print("  · %s：静默吞异常 %d 处（其中裸 except %d 处）" % (path, len(silent), len(bare)))
    for idx, text, _ in sorted(changes, key=lambda c: c[0])[:6]:
        print("      L%-5d %s" % (idx + 1, text.strip()[:96]))
    if len(changes) > 6:
        print("      … 其余 %d 行同理" % (len(changes) - 6))

    if not apply:
        return len(silent)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        print("      ❌ 改写后语法不过，已放弃该文件：%s" % str(e)[:160])
        os.remove(tmp)
        return 0
    shutil.move(tmp, path)
    print("      ✅ 已写入（语法校验通过）")
    return len(silent)


def fix_bare_except(path: str, apply: bool) -> int:
    """把裸 `except:` 改成 `except Exception:`（不绑定变量，保持原有逻辑不动）。

    为什么：裸 except 会连 KeyboardInterrupt / SystemExit 一起吞掉，
    导致 Ctrl+C 杀不掉、测试框架提前退出被当成业务异常。
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return 0
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            idx = node.lineno - 1
            if idx < len(lines) and "silent-ok" not in lines[idx]:
                indent = re.match(r"\s*", lines[idx]).group(0)
                targets.append((idx, indent + "except Exception:"))
    if not targets:
        print("  · %s：无裸 except" % path)
        return 0
    print("  · %s：裸 except %d 处" % (path, len(targets)))
    for idx, text in targets[:6]:
        print("      L%-5d %s" % (idx + 1, text.strip()))
    if not apply:
        return len(targets)
    out = list(lines)
    for idx, text in sorted(targets, key=lambda t: -t[0]):
        out[idx] = text
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        print("      ❌ 语法不过，放弃：%s" % str(e)[:140])
        os.remove(tmp)
        return 0
    shutil.move(tmp, path)
    print("      ✅ 已写入（语法校验通过）")
    return len(targets)


def main() -> int:
    ap = argparse.ArgumentParser(description="把静默吞异常改成记日志（默认 dry-run）")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    ap.add_argument("--bare", action="store_true", help="只处理裸 except: → except Exception:")
    args = ap.parse_args()

    total = 0
    for p in args.files:
        if not os.path.exists(p):
            print("  ⚠️ 文件不存在: %s" % p)
            continue
        total += fix_bare_except(p, args.apply) if args.bare else fix_file(p, args.apply)
    print("\n%s：%d 处%s" % ("已修复" if args.apply else "待修复（--dry-run）", total,
                           "裸 except" if args.bare else "静默吞异常"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
