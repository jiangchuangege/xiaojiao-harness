#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小焦 ↔ bili-duty 桥：在**插件自己的 venv 进程**里调一个 bili-duty 工具，把结果以 JSON 回给调用方。

为什么要有这个桥（而不是在 xiaojiao 里直接 import mcp_server）：
  `mcp_server.py` 依赖 `bilibili_api` + `mcp`，这两个只装在插件的 `.venv` 里；
  小焦自己的解释器没有它们。直接 import 会 ImportError，装到小焦环境里又会污染它。
  所以：小焦 → 子进程（插件 venv 的 python）→ 这里 → 目标函数 → JSON 回传。
  这也是"插件自带环境"最省事、最不互相污染的做法。

约定：
  标准输入  {"tool": "bili_search", "args": {...}, "allow": "*" 或 "a,b"}
  标准输出  一行 JSON {"ok": true, "text": "..."} 或 {"ok": false, "error": "..."}
"""
import asyncio
import inspect
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _collect(v):
    """把工具返回值拆成 (文字, 图片列表)。

    为什么要把图片单独带回来：`bili_cover` / `bili_frames` 返回的是**图片内容块**，
    原来的实现只把它拼成一句"[图片 image/jpeg，12345 字节]"，于是界面拿不到图，
    "刷过的视频"就只能显示文字表 —— 而用户要看的是**封面**。
    这里把 base64 原样带回去，由界面决定怎么用（缩略图直接 data URI 也行）。
    """
    if isinstance(v, str):
        return v, []
    if isinstance(v, (list, tuple)):
        texts, images = [], []
        for it in v:
            txt = getattr(it, "text", None)
            if txt is not None:
                texts.append(str(txt))
                continue
            data = getattr(it, "data", None)
            mime = getattr(it, "mimeType", None) or getattr(it, "mime_type", None)
            if data is not None:
                d = data if isinstance(data, str) else base64.b64encode(bytes(data)).decode()
                images.append({"mime": mime or "image/jpeg", "data": d})
                continue
            texts.append(str(it))
        return "\n".join(texts), images
    return str(v), []


def _as_text(v):
    """兼容旧的调用点：只要文字。"""
    return _collect(v)[0]


def main():
    try:
        raw = sys.stdin.read()
        req = json.loads(raw or "{}")
    except Exception as e:
        _emit({"ok": False, "error": "请求不是合法 JSON：%s" % e})
        return 2

    tool = str(req.get("tool") or "")
    args = req.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    # 白名单必须在 import 之前设好：mcp_server 在**导入时**读 BILI_MCP_TOOLS
    allow = req.get("allow")
    if allow:
        os.environ["BILI_MCP_TOOLS"] = str(allow)

    os.chdir(HERE)                       # 插件大量用相对路径（凭证/配置/日志）
    if HERE not in sys.path:
        sys.path.insert(0, HERE)

    try:
        import mcp_server as M
    except Exception as e:
        _emit({"ok": False, "error": "导入 mcp_server 失败：%s: %s" % (type(e).__name__, e)})
        return 3

    fn = getattr(M, tool, None)
    if fn is None:
        _emit({"ok": False, "error": "没有这个工具：%s" % tool})
        return 4
    # FastMCP 的 @mcp.tool() 可能把函数包成工具对象 —— 取回原函数
    if not (inspect.iscoroutinefunction(fn) or inspect.isfunction(fn)):
        inner = getattr(fn, "fn", None) or getattr(fn, "func", None)
        if inner is not None:
            fn = inner

    try:
        if inspect.iscoroutinefunction(fn):
            out = asyncio.run(fn(**args))
        else:
            out = fn(**args)
    except TypeError as e:
        _emit({"ok": False, "error": "参数不对：%s" % e})
        return 5
    except Exception as e:
        _emit({"ok": False, "error": "调用出错：%s: %s" % (type(e).__name__, str(e)[:300]),
               "trace": traceback.format_exc()[-800:]})
        return 6

    text, images = _collect(out)
    payload = {"ok": True, "text": text}
    if images:
        payload["images"] = images
    _emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
