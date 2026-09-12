# -*- coding: utf-8 -*-
"""只留一个 v1.0：清掉 GitHub 上的旧 Release 与 tag，再建 v1.0

安全说明：只删 Release 与 tag 引用（可重建），**不动任何提交历史**。
用法：python tools/reset_releases.py --tag v1.0 [--dry-run]
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
    ap = argparse.ArgumentParser(description="清理旧 Release/tag，只保留指定版本")
    ap.add_argument("--tag", default="v1.0", help="要保留的版本号（默认 v1.0）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    h = {"Authorization": "Bearer %s" % token(), "Accept": "application/vnd.github+json",
         "User-Agent": "xiaojiao-release-reset"}

    def api(path, method="GET", payload=None, ok=(200, 201, 204, 404, 422)):
        req = urllib.request.Request(
            "https://api.github.com%s" % path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=dict(h, **{"Content-Type": "application/json"}), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            if e.code in ok:
                return {"_code": e.code, "_detail": detail[:200]}
            raise SystemExit("API %s %s → %s: %s" % (method, path, e.code, detail[:200]))

    keep = args.tag
    rels = api("/repos/%s/releases?per_page=100" % REPO)
    tags = api("/repos/%s/git/refs/tags" % REPO)
    tags = tags if isinstance(tags, list) else []

    print("现有 Release：%s" % ", ".join(r["tag_name"] for r in rels) or "（无）")
    print("现有 tag    ：%s" % ", ".join(t["ref"].split("/")[-1] for t in tags) or "（无）")
    print("要保留      ：%s" % keep)
    if args.dry_run:
        print("\n--dry-run：不执行删除。")
        return 0

    for r in rels:
        if r["tag_name"] == keep:
            continue
        res = api("/repos/%s/releases/%s" % (REPO, r["id"]), "DELETE")
        print("  %s Release %s → %s" % ("✓ 已删" if res.get("_code") == 204 else "✗",
                                        r["tag_name"], res.get("_code") or "ok"))
    for t in tags:
        name = t["ref"].split("/")[-1]
        if name == keep:
            continue
        res = api("/repos/%s/git/refs/tags/%s" % (REPO, urllib.parse.quote(name, safe="")), "DELETE")
        print("  %s tag %s → %s" % ("✓ 已删" if res.get("_code") == 204 else "（已无）",
                                    name, res.get("_code") or "ok"))

    # 建 v1.0：正文取 CHANGELOG 里对应小节
    body = ""
    try:
        text = io.open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        m = re.search(r"^##\s*\[%s\][^\n]*\n(.*?)(?=^##\s*\[|\Z)" % re.escape(keep), text, re.S | re.M)
        body = (m.group(1).strip() if m else "")
    except OSError:
        pass
    if not body:
        print("❌ CHANGELOG 里找不到 [%s] 小节，无法建 Release 正文" % keep)
        return 1

    main_sha = api("/repos/%s/git/ref/heads/main" % REPO)["object"]["sha"]
    title = "%s · 小焦的首个正式版" % keep
    if api("/repos/%s/git/ref/tags/%s" % (REPO, keep)).get("_code") == 404:
        tobj = api("/repos/%s/git/tags" % REPO, "POST",
                   {"tag": keep, "message": title, "object": main_sha, "type": "commit"})
        api("/repos/%s/git/refs" % REPO, "POST", {"ref": "refs/tags/%s" % keep, "sha": tobj["sha"]})
        print("✅ tag %s → %s" % (keep, tobj["sha"][:12]))
    else:
        print("· tag %s 已存在" % keep)

    rel = api("/repos/%s/releases" % REPO, "POST",
              {"tag_name": keep, "name": title, "body": body, "draft": False, "prerelease": False})
    if rel.get("_code") == 422:
        old = api("/repos/%s/releases/tags/%s" % (REPO, keep))
        up = api("/repos/%s/releases/%s" % (REPO, old["id"]), "PATCH", {"name": title, "body": body})
        print("✅ 已更新 Release %s：%s（正文 %d 字）" % (keep, up.get("html_url"), len(up.get("body") or "")))
    else:
        print("✅ 已创建 Release %s：%s（正文 %d 字）" % (keep, rel.get("html_url"), len(rel.get("body") or "")))

    rels2 = api("/repos/%s/releases?per_page=100" % REPO)
    tags2 = api("/repos/%s/git/refs/tags" % REPO)
    print("\n收尾核对：Release = %s ｜ tag = %s"
          % (", ".join(r["tag_name"] for r in rels2) or "无",
             ", ".join(t["ref"].split("/")[-1] for t in tags2) or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
