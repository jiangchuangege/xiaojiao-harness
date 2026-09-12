# -*- coding: utf-8 -*-
"""小焦 · 资产测绘插件（IP ↔ CVE 对应表）

**为什么有这个插件**：NVD 只发布「CVE → 受影响软件/版本(CPE)」，**不发布任何公网 IP**。
所以"网络上所有含这些漏洞的 IP 地址并列表对应上"这类**资产测绘**诉求，光靠 NVD 永远答不了。
这个插件把"资产测绘数据源"接进来，两个方向都覆盖：

  ① **给 IP → 看它命中哪些 CVE**：走 Shodan 的免费 InternetDB 接口（https://internetdb.shodan.io）
     **完全不需要 Key**，开箱即用。工具：`asset_intel_lookup`
  ② **给 CVE/关键词 → 查哪些 IP 受影响**：这类"反向测绘"各家都要求 Key（Shodan / ZoomEye / Fofa），
     需要你填 Key 才能用。工具：`asset_intel_search`（没配 Key 时会明确告诉你差什么、去哪拿）

Key 怎么填（三种任选，都不填也能用方向①）：
  · 环境变量：`SHODAN_API_KEY` / `ZOOMEYE_API_KEY` / `FOFA_EMAIL` + `FOFA_KEY`
  · 或在本目录放 `asset_intel_keys.json`：{"shodan": "xxx", "zoomeye": "xxx", "fofa_email": "a@b.c", "fofa_key": "xxx"}
  · 改完重启小焦即生效（设置页 → 插件里也能看到本插件是否加载）

设计纪律（跟 scrapling_bridge 保持一致）：
  · 任何异常都转成**中文可读**说明返回，绝不把堆栈抛给用户
  · 只查公网 IP：内网/本机地址直接拒绝（否则叫"资产测绘"却扫自己家网段，没意义也不安全）
  · 不写死 Key、不把 Key 打进日志/结果里
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

import requests

_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_intel_keys.json")
_INTERNETDB = "https://internetdb.shodan.io/%s"        # 免费、无需 Key
_SHODAN_SEARCH = "https://api.shodan.io/shodan/host/search"
_ZOOMEYE_SEARCH = "https://api.zoomeye.org/host/search"
_FOFA_SEARCH = "https://fofa.info/api/v1/search/all"

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
_PRIVATE_RE = re.compile(r"^(?:10\.|127\.|0\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)")


def _keys() -> Dict[str, str]:
    """取各家 Key：先环境变量，再本地 key 文件（文件存在才读，格式错了当没配）。"""
    env = {"shodan": os.environ.get("SHODAN_API_KEY", ""),
           "zoomeye": os.environ.get("ZOOMEYE_API_KEY", ""),
           "fofa_email": os.environ.get("FOFA_EMAIL", ""),
           "fofa_key": os.environ.get("FOFA_KEY", "")}
    try:
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in env:
                    if d.get(k):
                        env[k] = str(d[k])
    except Exception:  # noqa: silent-ok — Key 文件坏掉不该让插件起不来
        pass
    return env


def _extract_ips(text) -> List[str]:
    """从字符串/列表里抠出公网 IP（去重、保序、跳过内网）。"""
    raw: List[str] = []
    if isinstance(text, (list, tuple)):
        for x in text:
            raw.extend(_IP_RE.findall(str(x)))
    else:
        raw.extend(_IP_RE.findall(str(text or "")))
    out = []
    for ip in raw:
        if ip in out:
            continue
        if _PRIVATE_RE.match(ip) or ip.startswith("255."):
            continue
        out.append(ip)
    return out


def _md_table(header: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(lines)


class AssetIntelPlugin:
    """资产测绘：IP ↔ CVE。方向①免费无 Key，方向②需要数据源 Key。"""

    def get_tool_descriptions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "asset_intel_lookup",
                "description": "给 IP 查它命中了哪些漏洞(免费，无需 Key)。什么时候用：用户给了具体 IP 想知道有没有已知漏洞；输入 ips(一个或多个公网 IP，逗号/空格分隔)；输出 IP→命中 CVE 表格(含端口/主机名)",
                "parameters": {"type": "object",
                               "properties": {"ips": {"type": "string", "description": "一个或多个公网 IP，逗号/空格分隔"}},
                               "required": ["ips"]},
            },
            {
                "name": "asset_intel_search",
                "description": "反过来查：某个 CVE/产品在**全网有哪些 IP** 受影响(需数据源 Key)。什么时候用：用户要「含这些漏洞的 IP」；输入 query(如 vuln:CVE-2024-1234)、limit；输出 IP 列表(未配 Key 时给配置指引)",
                "parameters": {"type": "object",
                               "properties": {"query": {"type": "string", "description": "如 vuln:CVE-2024-1234 或 apache 2.4.49"},
                                              "limit": {"type": "integer", "description": "返回条数，默认 10"}},
                               "required": ["query"]},
            },
            {
                "name": "asset_intel_status",
                "description": "看资产测绘数据源能不能用。什么时候用：反查失败或用户问「能查 IP 吗」；无参数；输出 各数据源状态 + 还缺哪个 Key + 去哪拿",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        ]

    # ---------------- 方向①：IP → CVE（免费） ----------------
    def lookup(self, ips, cves="") -> str:
        ip_list = _extract_ips(ips)
        if not ip_list:
            return ("⚠️ 没识别到公网 IP（内网/本机地址不查）。给我形如 `8.8.8.8` 的地址，"
                    "多个用逗号或空格隔开，例如：`asset_intel_lookup 1.1.1.1, 8.8.8.8`")
        want_cves = {c.upper() for c in _CVE_RE.findall(str(cves or ""))}
        rows, notes, hits = [], [], 0
        for ip in ip_list[:20]:
            try:
                r = requests.get(_INTERNETDB % ip, timeout=12)
                if r.status_code == 404:
                    rows.append([ip, "—", "—", "（Shodan 没有该地址的数据）"])
                    continue
                if r.status_code != 200:
                    notes.append("%s：HTTP %s，已跳过" % (ip, r.status_code))
                    continue
                d = r.json()
                ports = ", ".join(str(p) for p in (d.get("ports") or [])[:12]) or "—"
                hosts = ", ".join((d.get("hostnames") or [])[:3]) or "—"
                vulns = [str(v).upper() for v in (d.get("vulns") or [])]
                shown = [v for v in vulns if not want_cves or v in want_cves]
                if want_cves:
                    shown = shown or ["（你关心的 CVE 一个都没命中）"]
                hits += 1 if (vulns and (not want_cves or any(v in want_cves for v in vulns))) else 0
                rows.append([ip, hosts, ports, ", ".join(shown[:12]) or "无"])
            except Exception as e:  # 单个 IP 失败不影响其它
                notes.append("%s：%s" % (ip, str(e)[:60]))
        head = ("🛰️ **资产测绘 · IP → 漏洞**（数据源：Shodan InternetDB，免费无需 Key）\n\n"
                + _md_table(["IP", "主机名", "开放端口", "命中的 CVE"], rows))
        tail = "\n\n- 命中你关心的 CVE 的地址：**%d / %d**" % (hits, len(rows)) if want_cves else ""
        if want_cves:
            head = head.replace("命中的 CVE", "命中的 CVE（已按你给的清单过滤）")
        if notes:
            head += "\n\n⚠️ 部分地址没查到：" + "；".join(notes[:5])
        return head + tail

    # ---------------- 方向②：CVE → IP（要 Key） ----------------
    def search(self, query, limit=10) -> str:
        q = str(query or "").strip()
        if not q:
            return "⚠️ 请给查询词：CVE 编号（如 `vuln:CVE-2024-1234`）或关键词（如 `apache 2.4.49`）"
        limit = max(1, min(int(limit or 10), 50))
        k = _keys()
        if k["zoomeye"]:
            return self._search_zoomeye(q, limit, k["zoomeye"])
        if k["shodan"]:
            return self._search_shodan(q, limit, k["shodan"])
        if k["fofa_key"] and k["fofa_email"]:
            return self._search_fofa(q, limit, k["fofa_email"], k["fofa_key"])
        return ("⚠️ **「CVE → 受影响 IP」需要资产测绘数据源 Key**，现在一个都没配。\n\n"
                "为什么必须 Key：全网扫描数据只有 Shodan / ZoomEye / Fofa / Censys 这几家有，都要账号。\n\n"
                "怎么配（任选一家，配完重启小焦）：\n"
                "1. 拿 Key：Shodan https://account.shodan.io/ ｜ ZoomEye https://www.zoomeye.org/profile ｜ "
                "Fofa https://fofa.info/personalData\n"
                "2. 填：环境变量 `SHODAN_API_KEY` 或 `ZOOMEYE_API_KEY`，"
                "或写 `plugins/asset_intel_keys.json`：`{\"shodan\": \"你的Key\"}`\n"
                "3. 验证：对我说「资产测绘状态」，能用的数据源会列出来。\n\n"
                "👉 不想买 Key 也有免费的一半：**给我 IP，我立刻列出它命中了哪些 CVE**"
                "（`asset_intel_lookup`，走 Shodan InternetDB，不需要 Key）。")

    def _search_shodan(self, q, limit, key) -> str:
        try:
            r = requests.get(_SHODAN_SEARCH, params={"key": key, "query": q, "minify": "true"},
                             timeout=20)
            if r.status_code != 200:
                return ("⚠️ Shodan 返回 HTTP %s：%s\n（免费账号的 `vuln:` 过滤器可能不可用，"
                        "需要会员；可换成关键词查询）" % (r.status_code, r.text[:120]))
            d = r.json()
            rows = [[m.get("ip_str", "—"), (m.get("org") or "—")[:24],
                     (m.get("location") or {}).get("country_name", "—"),
                     ", ".join(str(p) for p in (m.get("ports") or [])[:8]) or "—",
                     ", ".join((m.get("vulns") or [])[:4]) or "—"]
                    for m in (d.get("matches") or [])[:limit]]
            return ("🛰️ **资产测绘 · CVE/关键词 → IP**（数据源：Shodan，共 %s 条）\n\n" % d.get("total", "?")) \
                + _md_table(["IP", "组织", "国家", "开放端口", "命中的 CVE"], rows or [["—", "—", "—", "—", "没有结果"]])
        except Exception as e:
            return "⚠️ Shodan 查询失败：%s" % str(e)[:100]

    def _search_zoomeye(self, q, limit, key) -> str:
        try:
            r = requests.get(_ZOOMEYE_SEARCH, params={"query": q, "page": 1},
                             headers={"API-KEY": key}, timeout=20)
            if r.status_code != 200:
                return "⚠️ ZoomEye 返回 HTTP %s：%s" % (r.status_code, r.text[:120])
            d = r.json()
            rows = []
            for m in (d.get("matches") or [])[:limit]:
                rows.append([m.get("ip", "—"), (m.get("geoinfo") or {}).get("country", {}).get("names", {}).get("zh-CN", "—"),
                             ", ".join(str(p.get("port")) for p in (m.get("portinfo") or {}).get("port", []) if p.get("port")) or "—"
                             if isinstance(m.get("portinfo"), dict) else "—",
                             (m.get("portinfo") or {}).get("app", "—") if isinstance(m.get("portinfo"), dict) else "—",
                             ", ".join((m.get("vulns") or [])[:4]) if m.get("vulns") else "—"])
            return ("🛰️ **资产测绘 · CVE/关键词 → IP**（数据源：ZoomEye，共 %s 条）\n\n" % d.get("total", "?")) \
                + _md_table(["IP", "国家", "端口", "应用", "命中的 CVE"], rows or [["—", "—", "—", "—", "没有结果"]])
        except Exception as e:
            return "⚠️ ZoomEye 查询失败：%s" % str(e)[:100]

    def _search_fofa(self, q, limit, email, key) -> str:
        try:
            import base64
            r = requests.get(_FOFA_SEARCH, params={"email": email, "key": key,
                                                  "qbase64": base64.b64encode(q.encode()).decode(),
                                                  "size": limit, "fields": "host,ip,port,country,title"},
                             timeout=20)
            if r.status_code != 200:
                return "⚠️ Fofa 返回 HTTP %s：%s" % (r.status_code, r.text[:120])
            d = r.json()
            if d.get("error"):
                return "⚠️ Fofa 报错：%s" % str(d.get("errmsg") or d.get("error"))[:120]
            rows = [[x[1] if len(x) > 1 else "—", x[3] if len(x) > 3 else "—",
                     x[2] if len(x) > 2 else "—", (x[4] if len(x) > 4 else "—")[:24], "—"]
                    for x in (d.get("results") or [])[:limit]]
            return ("🛰️ **资产测绘 · CVE/关键词 → IP**（数据源：Fofa，共 %s 条）\n\n" % d.get("size", "?")) \
                + _md_table(["IP", "国家", "端口", "标题", "命中的 CVE"], rows or [["—", "—", "—", "—", "没有结果"]])
        except Exception as e:
            return "⚠️ Fofa 查询失败：%s" % str(e)[:100]

    # ---------------- 状态 ----------------
    def status(self) -> str:
        k = _keys()
        rows = [
            ["Shodan InternetDB", "✅ 可用（免费，无需 Key）", "IP → 该地址命中的 CVE / 开放端口"],
            ["ZoomEye", "✅ 已配 Key" if k["zoomeye"] else "❌ 未配 Key（ZOOMEYE_API_KEY）", "CVE/关键词 → 受影响 IP"],
            ["Shodan 搜索", "✅ 已配 Key" if k["shodan"] else "❌ 未配 Key（SHODAN_API_KEY）", "CVE/关键词 → 受影响 IP"],
            ["Fofa", "✅ 已配 Key" if (k["fofa_key"] and k["fofa_email"]) else "❌ 未配 Key（FOFA_EMAIL + FOFA_KEY）",
             "CVE/关键词 → 受影响 IP"],
        ]
        return ("🛰️ **资产测绘数据源状态**\n\n"
                + _md_table(["数据源", "状态", "能干什么"], rows)
                + "\n\n- Key 写法：环境变量，或 `plugins/asset_intel_keys.json`"
                  "（`{\"shodan\": \"xxx\", \"zoomeye\": \"xxx\", \"fofa_email\": \"a@b.c\", \"fofa_key\": \"xxx\"}`）"
                  "\n- 改完**重启小焦**生效；`asset_intel_lookup` 无需任何 Key，随时能用。")

    # ---------------- 分发 ----------------
    def execute(self, tool_name: str, params: Dict[str, Any]) -> str:
        params = params or {}
        try:
            if tool_name == "asset_intel_lookup":
                return self.lookup(params.get("ips") or params.get("ip") or params.get("url") or "",
                                   params.get("cves") or params.get("cve") or "")
            if tool_name == "asset_intel_search":
                return self.search(params.get("query") or params.get("q") or params.get("cve") or "",
                                   params.get("limit", 10))
            if tool_name == "asset_intel_status":
                return self.status()
            return "未知工具：%s" % tool_name
        except Exception as e:  # 兜到底：插件绝不把异常抛给调用方
            return "⚠️ 资产测绘插件出错：%s: %s" % (type(e).__name__, str(e)[:120])
