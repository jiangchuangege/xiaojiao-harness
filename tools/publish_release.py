# -*- coding: utf-8 -*-
"""发布 v1.2.0：① 发布分支最新提交 ② 快进合并 main ③ 打 tag ④ 创建 Release

github.com 的 git 通道被重置时使用（fetch/push 均失败）；api.github.com 正常。
所有动作都可核对：合并为**快进**（不改写历史）、tag/Release 走 REST API 并打印返回结果。
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = "jiangchuangege/xiaojiao-harness"
BRANCH = "release/stabilize-20260912"
TAG = "v1.2.0"
TITLE = "v1.2.0 · 稳定化落地版（可观测 / 可回收 / 可并发 / 可验证）"
NOTES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "release-notes-v1.2.0.md")


def token() -> str:
    c = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                       input="protocol=https\nhost=github.com\npath=%s.git\n\n" % REPO)
    for line in c.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("取不到 GitHub token")


H = {"Authorization": "Bearer %s" % token(), "Accept": "application/vnd.github+json",
     "User-Agent": "xiaojiao-release"}


def api(path: str, method: str = "GET", payload=None, ok_codes=(200, 201, 422)):
    req = urllib.request.Request(
        "https://api.github.com%s" % path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=dict(H, **{"Content-Type": "application/json"}), method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        if e.code in ok_codes:
            return {"_code": e.code, "_detail": detail}
        raise SystemExit("API %s %s → %s: %s" % (method, path, e.code, detail[:300]))


def main() -> int:
    head = api("/repos/%s/git/ref/heads/%s" % (REPO, urllib.parse.quote(BRANCH)))["object"]["sha"]
    main_ref = api("/repos/%s/git/ref/heads/main" % REPO)["object"]["sha"]
    print("分支 %s → %s" % (BRANCH, head[:12]))
    print("main    → %s" % main_ref[:12])

    # ① 合并到 main：仅当 main 是分支祖先（快进）—— 不改写历史
    cmp_ = api("/repos/%s/compare/%s...%s" % (REPO, main_ref, head))
    if cmp_.get("status") not in ("ahead", "identical"):
        print("❌ main 与分支不是快进关系（status=%s），已中止，请人工合并" % cmp_.get("status"))
        return 1
    if main_ref == head:
        print("· main 已在分支头，跳过合并")
    else:
        r = api("/repos/%s/git/refs/heads/main" % REPO, "PATCH", {"sha": head, "force": False})
        new_main = (r.get("object") or {}).get("sha", head)
        print("✅ main 已快进到 %s（%s → %s）" % (new_main[:12], main_ref[:12], head[:12]))

    # ② 打 tag（annotated tag 对象 + ref）
    tag_obj = api("/repos/%s/git/tags" % REPO, "POST",
                  {"tag": TAG, "message": "小焦 %s · 稳定化落地版（可观测/可回收/可并发/可验证）" % TAG,
                   "object": head, "type": "commit"})
    if tag_obj.get("_code") == 422:
        print("· tag %s 已存在，跳过创建" % TAG)
        tag_sha = None
    else:
        tag_sha = tag_obj["sha"]
        ref = api("/repos/%s/git/refs" % REPO, "POST", {"ref": "refs/tags/%s" % TAG, "sha": tag_sha})
        print("✅ tag %s → %s（%s）" % (TAG, tag_sha[:12], "已创建" if ref.get("ref") else ref))

    # ③ 创建 Release
    body = open(NOTES, encoding="utf-8").read()
    rel = api("/repos/%s/releases" % REPO, "POST",
              {"tag_name": TAG, "name": TITLE, "body": body, "draft": False, "prerelease": False})
    if rel.get("_code") == 422:
        print("· Release %s 已存在：%s" % (TAG, rel.get("_detail", "")[:160]))
        existing = api("/repos/%s/releases/tags/%s" % (REPO, TAG))
        print("  现有 Release: %s" % existing.get("html_url"))
    else:
        print("✅ Release 已创建：%s" % rel.get("html_url"))
        print("   正文 %d 字" % len(rel.get("body") or ""))

    # ④ 本地引用同步（github.com 不通时无法 fetch，直接按 API 结果更新）
    for ref, sha in (("refs/heads/main", head), ("refs/remotes/origin/main", head),
                     ("refs/remotes/origin/%s" % BRANCH, head)):
        try:
            subprocess.run(["git", "update-ref", ref, sha], check=True, capture_output=True)
        except Exception as e:
            print("· 本地引用 %s 更新跳过：%s" % (ref, str(e)[:80]))
    print("\n完成。核对：")
    print("  main  =", api("/repos/%s/git/ref/heads/main" % REPO)["object"]["sha"][:12])
    print("  分支  =", api("/repos/%s/git/ref/heads/%s" % (REPO, urllib.parse.quote(BRANCH)))["object"]["sha"][:12])
    rels = api("/repos/%s/releases" % REPO)
    for r_ in rels[:3]:
        print("  Release: %s | %s" % (r_["tag_name"], r_["html_url"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
