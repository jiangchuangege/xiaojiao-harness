# -*- coding: utf-8 -*-
"""一键发布：**优先走 API**（秒级），git 通道只做一次短超时尝试

为什么这么做：github.com 的 git-over-HTTPS 时常被重置，`git push` 默认要卡好几分钟才报错，
而 `api.github.com` 一直是通的、发布只要十几秒（真实教训：一次发布在 git 上白等了 5 分钟）。

流程：
  ① `git push` 快速尝试一次（--git-timeout，默认 20 秒，超时立刻放弃）
  ② 不通就自动改走 REST API 发布（tools/publish_via_api.py 的逻辑）
  ③ 发布完立刻把本地引用拉到与远端一致（能 fetch 就 fetch + reset --hard）
  ④ 最后对齐 tag / Release（tools/sync_release.py）

用法：
    python tools/publish_all.py                    # 发布当前分支 + 对齐 tag
    python tools/publish_all.py --tag v1.0         # 指定要同步的 Release 版本
    python tools/publish_all.py --api-only         # 直接走 API，不试 git
    python tools/publish_all.py --dry-run          # 只看会做什么
退出码：0 = 发布成功且本地/远端内容一致
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: silent-ok — 老环境没有 reconfigure 也不该让工具挂掉
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args, timeout=120):
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "超时（%ss）" % timeout
    except Exception as e:
        return 125, str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description="一键发布（API 优先）")
    ap.add_argument("--branch", default="")
    ap.add_argument("--tag", default="v1.0", help="要同步的 Release 版本；留空则跳过对齐")
    ap.add_argument("--git-timeout", type=int, default=20, help="git push 快速尝试的超时秒数")
    ap.add_argument("--api-only", action="store_true", help="跳过 git push，直接走 API")
    ap.add_argument("--skip-release", action="store_true", help="不处理 tag/Release")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rc, out = run(["git", "branch", "--show-current"])
    branch = args.branch or out.strip() or "main"
    rc, local = run(["git", "rev-parse", "--short", "HEAD"])
    local = local.strip()
    rc, dirty = run(["git", "status", "--porcelain"])
    if dirty.strip():
        print("⚠️ 还有未提交的改动：\n%s" % dirty.strip()[:300])
        print("   （发布只推已提交内容；未提交的不会上去）")
    print("分支 %s ｜ 本地 HEAD %s" % (branch, local))
    if args.dry_run:
        print("--dry-run：只做检查，不发布")
        return 0

    pushed = False
    remote_sha = ""
    if not args.api_only:
        print("\n① git push（超时 %ds，不通就转 API）…" % args.git_timeout)
        rc, out = run(["git", "push", "origin", branch], timeout=args.git_timeout)
        if rc == 0:
            pushed = True
            print("   ✅ git 通道可用：%s" % out.strip().splitlines()[-1][:100])
        else:
            print("   · git 通道不可用（%ss，%s）→ 转 API"
                  % (args.git_timeout, out.strip().splitlines()[-1][:80] if out.strip() else "无输出"))

    if not pushed:
        print("\n② 走 REST API 发布（秒级）…")
        rc, out = run([sys.executable, "tools/publish_via_api.py", "--branch", branch], timeout=180)
        print("   " + "\n   ".join(out.strip().splitlines()[-4:]))
        if rc != 0:
            print("❌ API 发布也失败，请检查网络/token")
            return 1
        if "不一致" in out:
            print("❌ 远端与本地内容不一致，需人工核对（不要继续对齐 tag）")
            return 1
        import re as _re
        m = _re.search(r"指向\s+([0-9a-f]{7,40})", out)
        remote_sha = m.group(1) if m else ""      # fetch 不通时也能给出真实的远端提交

    print("\n③ 本地引用对齐远端…")
    # **真实缺陷复盘**：这里原来无条件 `git reset --hard origin/main`。只要发布时工作区里
    # 还有**没提交**的改动（正常开发中很常见），这一步就会把它们**静默删掉** —— 我这次就被
    # 吃掉了刚写的补丁（文件回退到上次提交的样子，自己还没察觉）。现在：工作区不干净就不重置。
    rc_st, dirty = run(["git", "status", "--porcelain"], timeout=30)
    if rc_st == 0 and dirty.strip():
        print("   ⚠️ 工作区有 %d 处未提交改动 → **跳过 reset --hard**（绝不吞掉你的代码）；"
              "要自动对齐请先 commit" % len(dirty.strip().splitlines()))
    else:
        rc, out = run(["git", "fetch", "origin"], timeout=60)
        if rc == 0:
            rc2, t1 = run(["git", "rev-parse", "%s^{tree}" % branch])
            rc3, t2 = run(["git", "rev-parse", "origin/%s^{tree}" % branch])
            if t1.strip() == t2.strip():
                run(["git", "reset", "--hard", "origin/%s" % branch])
                rc4, now = run(["git", "rev-parse", "--short", "HEAD"])
                print("   ✅ 已对齐到 origin/%s（HEAD %s）" % (branch, now.strip()))
            else:
                print("   ⚠️ 本地/远端 tree 不一致，未重置（请核对）")
        else:
            print("   · fetch 不通（不影响发布结果，稍后再对齐）")

    if args.tag and not args.skip_release:
        print("\n④ 对齐 tag / Release（%s）…" % args.tag)
        rc, out = run([sys.executable, "tools/sync_release.py", "--tag", args.tag], timeout=180)
        print("   " + "\n   ".join(l for l in out.strip().splitlines()[-4:]))
        if rc != 0:
            print("   ⚠️ tag/Release 未对齐（可稍后重跑 tools/sync_release.py）")

    rc, fetched = run(["git", "rev-parse", "--short", "origin/%s" % branch])
    _r = remote_sha[:12] if remote_sha else (fetched.strip() if rc == 0 else "（未知，fetch 不通）")
    print("\n完成。核对：本地 %s ｜ 远端 %s%s"
          % (local, _r, "" if remote_sha else "（本地引用可能未刷新）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
