# -*- coding: utf-8 -*-
"""把 tag 与 GitHub Release 对齐到当前 main（幂等，可反复跑）

为什么需要：每次补提交后 tag 就落在旧提交上；而手工"删 tag 重建"还会把已发布的 Release
变成草稿、甚至留下 untagged 的孤儿 Release（都真实踩过）。这个工具一把做完并核对：

  ① tag <TAG> → 指向远端 main 的最新提交（不在最新就重建，注解标签）
  ② 恰好一个**已发布**的同名 Release，正文 = CHANGELOG 里 `## [TAG]` 小节
  ③ 清掉孤儿/草稿 Release（同一 tag 名字的）
  ④ 收尾打印 tag 列表 / Release 列表 / tag 目标是否等于 main

用法：
    python tools/sync_release.py --tag v1.0
    python tools/sync_release.py --tag v1.0 --dry-run
退出码：0 = 已对齐
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = "jiangchuangege/xiaojiao-harness"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def token() -> str:
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if env.strip():
        return env.strip()
    c = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                       input="protocol=https\nhost=github.com\npath=%s.git\n\n" % REPO)
    for line in c.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("取不到 GitHub token")


def main() -> int:
    ap = argparse.ArgumentParser(description="tag + Release 对齐到当前 main")
    ap.add_argument("--tag", default="v1.0")
    ap.add_argument("--title", default="", help="Release 标题；默认自动生成")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", default="main",
                    help="tag 要对齐的分支/引用（默认 main；从 release 分支发版时传该分支）")
    args = ap.parse_args()

    h = {"Authorization": "Bearer %s" % token(), "Accept": "application/vnd.github+json",
         "User-Agent": "xiaojiao-sync", "Content-Type": "application/json"}

    def api(path, method="GET", payload=None, ok=(200, 201, 204, 404, 422)):
        req = urllib.request.Request(
            "https://api.github.com%s" % path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body.strip() else {"_code": r.status}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            if e.code in ok:
                return {"_code": e.code, "_detail": detail[:160]}
            raise SystemExit("API %s %s → %s: %s" % (method, path, e.code, detail[:200]))

    # tag 要对齐的引用：默认 main；从 release 分支发版时传 --target release/xxx
    # （为什么需要：发布流程允许"提交先落在 release 分支"，这时把 tag 钉在 main 上会指向旧代码）
    target_ref = args.target or "main"
    main_sha = api("/repos/%s/git/ref/heads/%s" % (REPO, target_ref))["object"]["sha"]
    print("远端 %s = %s" % (target_ref, main_sha[:12]))

    # ① tag 是否已在最新提交上
    ref = api("/repos/%s/git/ref/tags/%s" % (REPO, urllib.parse.quote(args.tag, safe="")))
    cur = ""
    if ref.get("object"):
        if ref["object"].get("type") == "tag":
            cur = (api("/repos/%s/git/tags/%s" % (REPO, ref["object"]["sha"])).get("object") or {}).get("sha", "")
        else:
            cur = ref["object"]["sha"]
    need = cur != main_sha
    print("tag %s 当前 → %s%s" % (args.tag, (cur[:12] or "（不存在）"), "（需要重建）" if need else "（已是最新）"))
    if need and not args.dry_run:
        # **真实缺陷**：原来 tag 要重建时先 `DELETE refs/tags/<tag>`。GitHub 上
        # **删掉 tag 会把它名下的 Release 一并弄成孤儿/草稿**，于是发布页在发布过程中
        # 短暂"消失"（本次实测就是这样：发布完 Release 列表变成空的）。
        # 现在：已存在的 tag 用 PATCH **直接改指向**，不删引用；只有本来就没有才创建。
        tobj = api("/repos/%s/git/tags" % REPO, "POST",
                   {"tag": args.tag, "message": "小焦 %s · 正式版" % args.tag,
                    "object": main_sha, "type": "commit"})
        if ref.get("object"):
            api("/repos/%s/git/refs/tags/%s" % (REPO, urllib.parse.quote(args.tag, safe="")),
                "PATCH", {"sha": tobj["sha"], "force": True})
            print("✅ tag 已改指向 → %s（未删引用，Release 不会变孤儿）" % main_sha[:12])
        else:
            api("/repos/%s/git/refs" % REPO,
                "POST", {"ref": "refs/tags/%s" % args.tag, "sha": tobj["sha"]})
            print("✅ tag 已创建 → %s" % main_sha[:12])

    # ② 正文：优先用专门写好的发布说明 docs/release-notes-<tag>.md（排版更适合发布页），
    #    没有才退回 CHANGELOG 的对应小节（那种 2 万字长文堆在 Release 页上很难看）。
    notes = os.path.join(ROOT, "docs", "release-notes-%s.md" % args.tag)
    if os.path.exists(notes):
        body = io.open(notes, encoding="utf-8").read().strip()
        src = os.path.relpath(notes, ROOT)
    else:
        text = io.open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        m = re.search(r"^##\s*\[%s\][^\n]*\n(.*?)(?=^##\s*\[|\Z)" % re.escape(args.tag), text, re.S | re.M)
        body = (m.group(1).strip() if m else "")
        src = "CHANGELOG.md"
    if not body:
        print("❌ 找不到 %s 的发布说明（docs/release-notes-%s.md 或 CHANGELOG 小节）" % (args.tag, args.tag))
        return 1
    print("正文来源 = %s（%d 字）" % (src, len(body)))
    title = args.title or "%s · 小焦的首个正式版" % args.tag

    # ③ 清掉同名草稿/孤儿，再确保有一个已发布 Release
    #    **只清草稿**：已发布的别的 Release 不归本工具管，删了就是"发布页凭空消失"。
    rels = api("/repos/%s/releases?per_page=100" % REPO)
    published = [r for r in rels if r["tag_name"] == args.tag and not r["draft"]]
    strays = [r for r in rels if r["draft"]]
    for r in strays:
        if args.dry_run:
            print("（dry-run）会删除 Release %s id=%s draft=%s" % (r["tag_name"], r["id"], r["draft"]))
            continue
        api("/repos/%s/releases/%s" % (REPO, r["id"]), "DELETE")
        print("· 已清理 Release %s id=%s（%s）" % (r["tag_name"], r["id"], "草稿" if r["draft"] else "非本版本"))
    if args.dry_run:
        print("（dry-run：不提交任何改动）")
        return 0
    if published:
        up = api("/repos/%s/releases/%s" % (REPO, published[0]["id"]), "PATCH",
                 {"name": title, "body": body, "draft": False, "prerelease": False})
        if not up.get("id"):
            print("❌ Release 更新失败：%s" % str(up)[:160])
            return 1
        print("✅ Release 已更新：%s（正文 %d 字）" % (up.get("html_url"), len(up.get("body") or "")))
    else:
        rel = api("/repos/%s/releases" % REPO, "POST",
                  {"tag_name": args.tag, "name": title, "body": body, "draft": False, "prerelease": False},
                  ok=(200, 201))
        # 原来 api() 把 404/422 也算成功 → 创建失败会**静默通过**，发布页就没了。
        # 这里必须核对返回值里真的有 Release id。
        if not rel.get("id"):
            print("❌ Release 创建失败：%s" % str(rel)[:160])
            return 1
        print("✅ Release 已创建：%s（正文 %d 字）" % (rel.get("html_url"), len(rel.get("body") or "")))

    # ④ 收尾核对
    tags = api("/repos/%s/git/refs/tags" % REPO)
    rels2 = api("/repos/%s/releases?per_page=100" % REPO)
    tag_target = ""
    for t in tags:
        if t["ref"].endswith("/" + args.tag):
            tag_target = ((api("/repos/%s/git/tags/%s" % (REPO, t["object"]["sha"])).get("object") or {})
                          .get("sha", "") if t["object"]["type"] == "tag" else t["object"]["sha"])
    print("\n收尾核对：")
    print("  tag 列表     : %s" % ([t["ref"].split("/")[-1] for t in tags]))
    print("  Release 列表 : %s" % ([(r["tag_name"], "草稿" if r["draft"] else "已发布") for r in rels2]))
    print("  %s 指向      : %s ｜ %s: %s ｜ %s"
          % (args.tag, tag_target[:12] or "?", target_ref, main_sha[:12],
             "✅ 一致" if tag_target == main_sha else "❌ 不一致"))
    # 判成功：本 tag 指向目标引用，**且它自己有一个已发布的 Release**。
    # （旧判据要求"全仓库只有一个 Release"，开始多版本发版后就不成立了 —— 那是 v1.0 时代的写法。）
    mine_ok = any((r["tag_name"] == args.tag and not r["draft"]) for r in rels2)
    return 0 if (tag_target == main_sha and mine_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
