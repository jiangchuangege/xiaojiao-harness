# -*- coding: utf-8 -*-
"""网络受限时的发布兜底：用 GitHub REST API 把本地提交发布到远端分支

背景：`github.com` 的 git-over-HTTPS 有时会被网络重置（`Recv failure: Connection was reset`），
      但 `api.github.com` 仍然可用。此时用本脚本即可继续发布，**不需要改任何代码**。

原理：按顺序把每个本地提交重建为远端对象 —— 改动文件 → blob → tree(base_tree=父提交 tree) → commit，
      最后把分支 ref 指到最新提交（`force=false`，只允许快进）。

用法：
    python tools/publish_via_api.py                      # 发布 origin/<当前分支> 与本地 HEAD 之间的提交
    python tools/publish_via_api.py --branch main        # 指定分支
    python tools/publish_via_api.py --dry-run            # 只打印将要发布什么

发布后本地与远端**内容完全一致**，但提交 SHA 可能不同（提交对象的 committer 元数据由 API 侧写入）。
网络恢复后，用下面两条命令对齐引用即可（内容一致，安全）：

    git fetch origin
    git reset --hard origin/<branch>
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_REPO = "jiangchuangege/xiaojiao-harness"


def git(*args: str) -> str:
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git %s 失败: %s" % (" ".join(args), r.stderr.strip()[:200]))
    return r.stdout


def get_token(repo: str) -> str:
    c = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                       input="protocol=https\nhost=github.com\npath=%s.git\n\n" % repo)
    for line in c.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("取不到 GitHub token（先确认 git 凭据里有可用 token）")


class Api:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.h = {"Authorization": "Bearer %s" % token,
                  "Accept": "application/vnd.github+json", "User-Agent": "xiaojiao-publish"}

    def __call__(self, path: str, method: str = "GET", payload=None):
        req = urllib.request.Request(
            "https://api.github.com%s" % path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=dict(self.h, **{"Content-Type": "application/json"}), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError("API %s %s → %s: %s"
                               % (method, path, e.code, e.read().decode("utf-8", "ignore")[:300]))


def main() -> int:
    ap = argparse.ArgumentParser(description="用 GitHub API 发布本地提交（git push 被网络拦截时的兜底）")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--branch", default=git("branch", "--show-current").strip())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-ref", action="store_true",
                    help="允许非快进更新分支引用（仅在需要清理远端重复提交等特殊情况使用）")
    args = ap.parse_args()

    api = Api(args.repo, get_token(args.repo))
    remote_head = api("/repos/%s/branches/%s" % (args.repo, urllib.parse.quote(args.branch)))["commit"]["sha"]
    local_head = git("rev-parse", "HEAD").strip()
    if remote_head == local_head:
        print("远端与本地一致，无需发布")
        return 0

    # 求"待发布提交"：注意 API 发布的提交对象本地可能没有（SHA 不同），
    # 所以不能用 `git rev-list 远端..本地`，改为「远端已有哪些 SHA」求差集。
    remote_shas = set()
    remote_tree_to_sha = {}
    page = 1
    while page <= 5:                      # 最多回溯 500 个提交，足够用
        chunk = api("/repos/%s/commits?sha=%s&per_page=100&page=%d" % (args.repo, args.branch, page))
        if not chunk:
            break
        for c in chunk:
            remote_shas.add(c["sha"])
            remote_tree_to_sha.setdefault(c["commit"]["tree"]["sha"], c["sha"])
        if len(chunk) < 100:
            break
        page += 1
    # 找共同基点：先按 SHA 精确匹配；匹配不到再按 **内容（tree）** 匹配 ——
    # 因为 API 发布过的提交本轮本地并没有该对象（SHA 不同但内容相同）。
    base, base_remote = "", ""
    for sha in git("rev-list", "HEAD").split():
        if sha in remote_shas:
            base = base_remote = sha
            break
        tree = git("rev-parse", "%s^{tree}" % sha).strip()
        if tree in remote_tree_to_sha:
            base, base_remote = sha, remote_tree_to_sha[tree]
            print("（本地 %s 与远端 %s 内容一致，按内容匹配为共同基点）"
                  % (sha[:8], base_remote[:8]))
            break
    if not base:
        print("❌ 找不到本地与远端的共同提交（历史可能已分叉），请人工核对后再发布")
        return 1
    pending = git("rev-list", "--reverse", "%s..%s" % (base, local_head)).split()
    if not pending:
        print("本地没有领先远端的新提交（远端可能领先，请先 fetch）")
        return 1

    print("分支 %s：共同基点 %s → 本地 %s，待发布 %d 个提交"
          % (args.branch, base_remote[:12], local_head[:12], len(pending)))
    for sha in pending:
        print("  · %s %s" % (sha[:8], git("log", "-1", "--format=%s", sha).strip()[:70]))
    if args.dry_run:
        print("\n（--dry-run：未真正发布）")
        return 0

    parent = base_remote                  # ★ 链的起点必须是"远端那侧的基点提交"
    # （既不能用 remote_head，也不能用本地 SHA —— 否则会重复发布或产生分叉历史）
    for sha in pending:
        subject = git("log", "-1", "--format=%s", sha).strip()
        body = git("log", "-1", "--format=%B", sha).strip() or subject
        a = git("log", "-1", "--format=%an|%ae|%aI", sha).strip().split("|")
        c = git("log", "-1", "--format=%cn|%ce|%cI", sha).strip().split("|")
        files = [f for f in git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines() if f.strip()]

        entries = []
        for path in files:
            blob = subprocess.run(["git", "show", "%s:%s" % (sha, path)],
                                  capture_output=True).stdout
            b = api("/repos/%s/git/blobs" % args.repo, "POST",
                    {"content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"})
            entries.append({"path": path.replace("\\", "/"), "mode": "100644",
                            "type": "blob", "sha": b["sha"]})
        parent_tree = api("/repos/%s/git/commits/%s" % (args.repo, parent))["tree"]["sha"]
        tree = api("/repos/%s/git/trees" % args.repo, "POST", {"base_tree": parent_tree, "tree": entries})
        commit = api("/repos/%s/git/commits" % args.repo, "POST",
                     {"message": body, "tree": tree["sha"], "parents": [parent],
                      "author": {"name": a[0], "email": a[1], "date": a[2]},
                      "committer": {"name": c[0], "email": c[1], "date": c[2]}})
        print("  %s → 远端 %s（%d 个文件）" % (sha[:8], commit["sha"][:12], len(files)))
        parent = commit["sha"]

    api("/repos/%s/git/refs/heads/%s" % (args.repo, args.branch), "PATCH",
        {"sha": parent, "force": bool(args.force_ref)})
    print("\n✅ 已发布：%s 现在指向 %s%s"
          % (args.branch, parent[:12], "（force 更新）" if args.force_ref else ""))

    local_tree = git("rev-parse", "%s^{tree}" % local_head).strip()
    remote_tree = api("/repos/%s/git/commits/%s" % (args.repo, parent))["tree"]["sha"]
    print("内容一致性：%s（本地 tree %s / 远端 tree %s）"
          % ("✅ 一致" if local_tree == remote_tree else "❌ 不一致，请人工核对",
             local_tree[:12], remote_tree[:12]))
    if local_tree == remote_tree:
        print("网络恢复后对齐引用：git fetch origin && git reset --hard origin/%s" % args.branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
