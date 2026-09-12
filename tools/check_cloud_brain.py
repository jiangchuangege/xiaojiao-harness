# -*- coding: utf-8 -*-
"""体检云端大脑：到底是"你 Key 填错了"还是"服务商那边的问题"。

用法：
    python tools/check_cloud_brain.py                      # 用控制文件里当前的大脑配置
    python tools/check_cloud_brain.py --key sk-xxxx        # 临时测另一把 Key（不写配置）
    python tools/check_cloud_brain.py --n 10               # 每组打多少次（默认 6）

**判据只有一条：`POST /v1/chat/completions` 能不能通。**
`GET /v1/models` 在这里**不能当证据**：实测连「空 Key / 乱写的 Key」都能拿到 200
（网关/缓存时不时不校验令牌），所以"models 能通"≠"Key 是对的"。
（这条曾经把结论带偏过：之前据此告诉用户"你的 Key 是对的"，是错的。）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_brain():
    """读控制文件里的大脑配置（读不到就退回环境变量）。"""
    cf = os.path.join(ROOT, "xiaojiao_control.json")
    try:
        c = json.load(open(cf, encoding="utf-8"))
        api = (c.get("brain") or {}).get("api") or {}
        return {"engine": (c.get("brain") or {}).get("engine", "?"),
                "base_url": api.get("base_url", ""), "api_key": api.get("api_key", ""),
                "model": api.get("model", "")}
    except Exception as e:
        print("⚠️ 读不到控制文件（%s），改用环境变量" % str(e)[:60])
        return {"engine": "api", "base_url": os.environ.get("LLM_BASE", ""),
                "api_key": os.environ.get("LLM_API_KEY", ""), "model": os.environ.get("LLM_MODEL", "")}


def probe(base, key, model, n, timeout=60):
    """打 n 次 chat（+顺带打 n 次 models 作参考）。

    返回 (chat 成功数, models 成功数, 首错, 末次 request id, 401/403 次数, 超时次数)。
    **区分"被拒"和"太慢"**：实测这家慢起来一次要 100 秒（agnes-2.0-flash 冷启动到 103s），
    把超时也算成"Key 被拒"会给出完全错误的结论。
    """
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    ok_c = ok_m = denied = timed_out = 0
    first_err, last_rid = "", ""
    for _ in range(n):
        try:
            r2 = requests.post(base + "/chat/completions", headers=hdr,
                               json={"model": model, "messages": [{"role": "user", "content": "只回两个字：在的"}],
                                     "max_tokens": 16}, timeout=timeout)
            if r2.status_code == 200:
                ok_c += 1
            else:
                if r2.status_code in (401, 403):
                    denied += 1
                if not first_err:
                    first_err = "POST /chat/completions -> HTTP %s %s" % (r2.status_code, r2.text[:140])
                _m = r2.text
                import re as _re
                _rid = _re.search(r"request id:\s*([A-Za-z0-9]+)", _m)
                if _rid:
                    last_rid = _rid.group(1)
        except requests.exceptions.Timeout:
            timed_out += 1
            if not first_err:
                first_err = "POST /chat/completions -> 超时（%ds 内没返回）" % timeout
        except Exception as e:
            if not first_err:
                first_err = "POST /chat/completions -> %s: %s" % (type(e).__name__, str(e)[:100])
        try:
            ok_m += requests.get(base + "/models", headers=hdr, timeout=20).status_code == 200
        except Exception:
            pass
        time.sleep(0.5)
    return ok_c, ok_m, first_err, last_rid, denied, timed_out


def main():
    ap = argparse.ArgumentParser(description="云端大脑体检")
    ap.add_argument("--n", type=int, default=6, help="打多少次（默认 6）")
    ap.add_argument("--key", default="", help="临时用这把 Key 测（不写任何配置）")
    ap.add_argument("--base", default="", help="临时用这个地址测")
    ap.add_argument("--model", default="", help="临时用这个模型名测")
    args = ap.parse_args()
    b = load_brain()
    base = (args.base or b["base_url"] or "").rstrip("/")
    key = args.key or b["api_key"]
    model = args.model or b["model"]
    print("=" * 70)
    print("  云端大脑体检（判据：chat 能不能通）")
    print("=" * 70)
    print("  引擎    :", b["engine"], "" if not args.key else "（本次用 --key 指定的 Key）")
    print("  地址    :", base or "（空！没配 base_url）")
    print("  模型    :", model or "（空！没配模型名）")
    print("  Key     :", ("%s…%s（%d 字符）" % (key[:8], key[-4:], len(key))) if key else "（空！）")
    if not base or not key or not model:
        print("\n❌ 配置不完整：地址 / Key / 模型名，三个都要有。")
        return 2
    if key != key.strip():
        print("\n❌ Key 前后有空白字符（复制时带进来的），请删掉。")
        return 2
    n = max(1, min(args.n, 30))
    print("\n  各打 %d 次（单次最长等 60 秒；这家慢起来一次要 100 秒以上）…" % n)
    ok_c, ok_m, first_err, rid, denied, timed_out = probe(base, key, model, n)
    print("\n  结果：")
    print("    POST /chat/completions  %d/%d 成功   ← 这条才算数" % (ok_c, n))
    print("    GET  /models            %d/%d 成功   ← 仅参考（空 Key 有时也能 200，别拿它下结论）" % (ok_m, n))
    if denied or timed_out:
        print("    其中：被拒 %d 次 ｜ 超时 %d 次" % (denied, timed_out))
    if first_err:
        print("    首个错误：", first_err.replace("\n", " ")[:170])
    if rid:
        print("    末次 request id：", rid, "（找服务商支持时报这个）")
    print("\n  结论：")
    if ok_c == n:
        print("    ✅ 云端大脑可用（chat 全通）。")
    elif ok_c > 0:
        print("    ⚠️ 时通时不通（%d/%d）：网关偶发拒签/偶发变慢。小焦已内置退避重试 + 本地大脑兜底。"
              % (ok_c, n))
    elif timed_out and not denied:
        print("    ⚠️ chat **全超时**（%d/%d，没有一次被拒）——**Key 是好的**，是对方响应太慢"
              "（实测 agnes-2.0-flash 冷启动能到 103 秒）。换个更快的模型（agnes-2.5-flash）"
              "或稍后再试；期间小焦会自动用本地大脑顶着。" % (timed_out, n))
    else:
        print("    ❌ chat **一次都没通** —— 这**不是**偶发，是这把 Key 在服务商那边没被接受。")
        print("       常见原因，按顺序排：")
        print("         1. 这把 Key ≠ 你其它客户端里能用的那把（最可能：复制到旧 Key / 少了几位）")
        print("         2. 该 Key 已失效、被停用，或免费额度/额度已用完")
        print("         3. 账号需要在服务商控制台激活该模型/子账号权限")
        print("         4. 对方网关故障（拿上面的 request id 找支持确认）")
        print("       ✅ 最快验证：把你**在别的客户端里能用的那把 Key** 拿来测：")
        print("          python tools/check_cloud_brain.py --key <那把Key>")
        print("       期间小焦会自动用本地大脑照常回答，不影响你干活。")
    print("=" * 70)
    return 0 if ok_c == n else 1


if __name__ == "__main__":
    sys.exit(main())
