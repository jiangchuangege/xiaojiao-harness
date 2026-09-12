# -*- coding: utf-8 -*-
"""发布一个版本：打 tag + 建 Release（git 通道不通时也能用，走 REST API）

设计要点：
  · **不再写死版本号**（以前这个脚本把 TAG/TITLE/正文文件全硬编码成某个固定版本，发下一版就得改代码）。
  · Release 正文默认从 `CHANGELOG.md` 里**按 tag 抽出对应小节**（单一事实来源，不会出现文档与 Release 不一致），
    也可以用 `--notes-file` 指定现成文件。
  · 所有动作都可核对：tag/Release 走 REST API 并打印返回结果；已存在的 tag/Release 只提示、不覆盖。
  · **绝不改写历史**：本脚本只打 tag + 建 Release，不碰 main 的任何提交。

用法：
    python tools/publish_release.py --tag v1.0
    python tools/publish_release.py --tag v1.0 --title "v1.0 · 标题" --dry-run
    python tools/publish_release.py --tag v1.0 --notes-file CHANGELOG.md
    python tools/publish_release.py --tag v1.0 --ref main

退出码：0 = 成功（或 dry-run 展示完毕）；1 = 失败/参数不合法
"""
from __future__ import annotations

import argparse
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
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def token() -> str:
    """取 GitHub token：优先环境变量，其次 git credential（不落盘、不打印）。"""
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if env.strip():
        return env.strip()
    c = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                       input="protocol=https\nhost=github.com\npath=%s.git\n\n" % REPO)
    for line in c.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("取不到 GitHub token：设 GITHUB_TOKEN 或先 git 登录一次")


def changelog_section(tag: str) -> str:
    """从 CHANGELOG.md 抽出 `## [tag]` 小节正文（找不到就返回空串）。"""
    try:
        text = open(CHANGELOG, encoding="utf-8").read()
    except OSError:
        return ""
    pat = re.compile(r"^##\s*\[%s\][^\n]*\n(.*?)(?=^##\s*\[|\Z)" % re.escape(tag),
                     re.S | re.M)
    m = pat.search(text)
    return (m.group(1).strip() if m else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="发布小焦版本（tag + GitHub Release）")
    ap.add_argument("--tag", required=True, help="版本号，如 v1.0")
    ap.add_argument("--title", default="", help="Release 标题；默认用 CHANGELOG 小节的加粗摘要")
    ap.add_argument("--target", default="main", help="tag 指向的分支/提交（默认 main）")
    ap.add_argument("--notes-file", default="", help="Release 正文文件；默认从 CHANGELOG 抽")
    ap.add_argument("--update-body", action="store_true",
                    help="Release 已存在时，把正文同步成当前 CHANGELOG 小节（不改 tag、不改历史）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不发请求")
    args = ap.parse_args()

    h = {"Authorization": "Bearer %s" % token(), "Accept": "application/vnd.github+json",
         "User-Agent": "xiaojiao-release"}

    def api(path: str, method: str = "GET", payload=None, ok_codes=(200, 201, 422)):
        req = urllib.request.Request(
            "https://api.github.com%s" % path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=dict(h, **{"Content-Type": "application/json"}), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            if e.code in ok_codes:
                return {"_code": e.code, "_detail": detail}
            raise SystemExit("API %s %s → %s: %s" % (method, path, e.code, detail[:300]))

    body = (open(args.notes_file, encoding="utf-8").read() if args.notes_file
            else changelog_section(args.tag))
    if not body:
        print("❌ 没有 Release 正文：CHANGELOG 里找不到 [%s] 小节，也没给 --notes-file" % args.tag)
        return 1
    title = args.title
    if not title:
        # 自动标题：取正文第一行，去掉 Markdown 强调符，截到第一个"：。+，、"之前，避免标题又长又乱
        first = body.splitlines()[0].strip().strip("*").strip()
        first = re.sub(r"[`*]+", "", first)
        first = re.split(r"[：。+，、（(]", first, maxsplit=1)[0].strip()
        title = "%s · %s" % (args.tag, first[:48]) if first else args.tag

    print("仓库      : %s" % REPO)
    print("版本      : %s" % args.tag)
    print("标题      : %s" % title)
    print("指向      : %s" % args.target)
    print("正文      : %d 字（%s）" % (len(body), args.notes_file or "CHANGELOG.md"))
    if args.dry_run:
        print("\n--dry-run：不发任何请求。正文预览：\n")
        print(body[:800])
        return 0

    ref = api("/repos/%s/git/ref/heads/%s" % (REPO, urllib.parse.quote(args.target, safe="")))
    sha = (ref.get("object") or {}).get("sha", "")
    if not sha:
        print("❌ 拿不到 %s 的提交：%s" % (args.target, str(ref)[:200]))
        return 1
    print("提交      : %s" % sha[:12])

    # 先看 tag ref 在不在：已存在就别再建一个游离的 tag 对象（反复发布会攒垃圾对象）
    ref_now = api("/repos/%s/git/ref/tags/%s" % (REPO, args.tag), ok_codes=(200, 404))
    if isinstance(ref_now, dict) and ref_now.get("ref"):
        print("· tag %s 已存在（%s），跳过创建"
              % (args.tag, str((ref_now.get("object") or {}).get("sha", ""))[:12]))
    else:
        tag_obj = api("/repos/%s/git/tags" % REPO, "POST",
                      {"tag": args.tag, "message": "%s" % title, "object": sha, "type": "commit"})
        if tag_obj.get("_code") == 422:
            print("· tag %s 创建被拒（可能已存在），跳过" % args.tag)
        else:
            tsha = tag_obj.get("sha", "")
            r = api("/repos/%s/git/refs" % REPO, "POST",
                    {"ref": "refs/tags/%s" % args.tag, "sha": tsha})
            print("✅ tag %s → %s（%s）" % (args.tag, tsha[:12], "已创建" if r.get("ref") else r))

    rel = api("/repos/%s/releases" % REPO, "POST",
              {"tag_name": args.tag, "name": title, "body": body,
               "draft": False, "prerelease": False})
    if rel.get("_code") == 422:
        existing = api("/repos/%s/releases/tags/%s" % (REPO, args.tag))
        print("· Release %s 已存在：%s" % (args.tag, existing.get("html_url")))
        if args.update_body and existing.get("id"):
            # 正文与 CHANGELOG 可能后来补过内容 → 允许显式同步（只改正文，不动 tag、不动历史）
            up = api("/repos/%s/releases/%s" % (REPO, existing["id"]), "PATCH",
                     {"name": title, "body": body})
            print("  ✅ 正文已同步为最新 CHANGELOG 小节（%d 字）" % len(up.get("body") or ""))
        else:
            print("  （如需同步正文：加 --update-body）")
    else:
        print("✅ Release 已创建：%s（正文 %d 字）" % (rel.get("html_url"), len(rel.get("body") or "")))

    rels = api("/repos/%s/releases" % REPO)
    print("\n最近发布：")
    for r_ in rels[:5]:
        print("  %-8s %s" % (r_.get("tag_name"), r_.get("html_url")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
