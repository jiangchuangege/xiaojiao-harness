"""装小焦指引插件 (XiaojiaoInstallPlugin)

让猫娘帮主人安装/熟悉「小焦 XiaoJiao」开源项目：
- env_check: 读小焦后端 http://127.0.0.1:5000/api/env 的真实环境体检(装了哪些/缺哪些+怎么修)
- install_guide: 给小焦分步安装/启动指引
"""
from __future__ import annotations

import json
import os
import urllib.request

from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    Ok,
    Err,
    SdkError,
)

# 小焦后端地址(可用环境变量改; 5000是小焦web, /api/env返回环境体检)
XIAOJIAO_BASE = os.environ.get("XIAOJIAO_XIAOJIAO_BASE", "http://127.0.0.1:5000")

_INSTALL_STEPS = [
    ("1. 装依赖", "到小焦项目目录，运行 `pip install -r requirements.txt`。"),
    ("2. 摆好大脑", "小焦 /v1 兼容 OpenAI，模型**不写死**：a) 放任意 GGUF 到 C:/llama 或项目目录(本地离线)；或 b) 配置里填任意 OpenAI 兼容 API(base_url+key+model，如 DeepSeek/Qwen)。两者任一即可。"),
    ("3. 一键启动", "运行 `python start_xiaojiao.py`，它会自动起 多大脑(llama-swap:9292) + 网页(5000) + 你下载的 N.E.K.O. 猫娘(48911/48912)。"),
    ("4. 打开用", "浏览器开 `http://127.0.0.1:5000` 就能聊天；点 🎬 生成视频、🎙️ 播客、🎵 音乐、🐱 猫娘。"),
    ("5. 体检", "想确认环境：跟我说「装小焦体检」，我帮你调环境检查看缺什么、缺的会影响哪个功能。"),
]


@neko_plugin
class XiaojiaoInstallPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger.info("装小焦指引插件已加载")

    @plugin_entry(
        id="env_check",
        name="装小焦·环境体检",
        description="读取小焦后端的环境检查结果，告诉主人哪些装好了、哪些缺(如 Python/llama/网络/显存)。返回体检清单。",
        input_schema={"type": "object", "properties": {}},
    )
    async def env_check(self, **_):
        items = self._fetch_env()
        if items is None:
            return Ok({
                "ok": False,
                "msg": "小焦后端(5000)暂未响应。要我先按『装小焦指引』讲讲安装步骤吗？",
                "install_steps": _INSTALL_STEPS,
            })
        ok_names, miss_names, miss_detail = [], [], []
        for it in items:
            name = it.get("name", "")
            if it.get("ok"):
                ok_names.append(name)
            else:
                miss_names.append(name)
                if it.get("need"):
                    miss_detail.append("%s → %s" % (name, str(it["need"])[:60]))
        self.logger.info("小焦体检: 已装 %d, 缺 %d", len(ok_names), len(miss_names))
        # 把"缺的每项 + 怎么修"完整写进日志(猫娘读日志就能看到, 不再只说"缺4")
        detail = "\n".join("・%s：%s" % (it.get("name"), str(it.get("need") or "查看docs")[:80]) for it in items if not it.get("ok"))
        self.logger.info("装小焦体检完整结果：已装：%s | 缺：%s | 缺的怎么补：\n%s", ("、".join(ok_names)) or "无", ("、".join(miss_names)) or "无", detail or "无")
        # 把"缺的每一项 + 怎么修"写成猫娘能直接读的清晰文本(避免只报"缺4"说不清)
        miss_text = []
        for it in items:
            if it.get("ok"):
                continue
            name = it.get("name", "")
            fix = it.get("need") or it.get("fix") or "请查看小焦文档/docs"
            miss_text.append("・%s：%s" % (name, str(fix)[:80]))
        summary = "✅ 已装: %s\n❌ 还缺这些(小焦帮你查了怎么补):\n%s" % (
            "、".join(ok_names) or "无",
            "\n".join(miss_text) if miss_text else "无",
        )
        return Ok({
            "ok": True,
            "installed": ok_names,
            "missing": miss_names,
            "missing_how_to_fix": miss_detail,
            "summary": summary,
        })

    @plugin_entry(
        id="install_guide",
        name="装小焦·安装指引",
        description="告诉主人安装/启动「小焦 XiaoJiao」的完整分步指引(装依赖→摆大脑→启动→使用)。",
        input_schema={"type": "object", "properties": {}},
    )
    async def install_guide(self, **_):
        text = "想装小焦吗？跟着我一步步来：\n" + "\n".join("・%s：%s" % (s, d) for s, d in _INSTALL_STEPS)
        self.logger.info("装小焦安装指引：\n%s", text)   # 写进日志, 猫娘读日志就能看到
        return Ok({"steps": _INSTALL_STEPS, "guide": text})

    def _fetch_env(self):
        """读小焦 /api/env, 返回 [{name,ok,need}...] 或 None(拉不到)。"""
        try:
            req = urllib.request.Request(XIAOJIAO_BASE + "/api/env")
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            items = d.get("items")
            return items if isinstance(items, list) else None
        except Exception:
            print("[xiaojiao_install] 读小焦 /api/env 失败(小焦后端没起?)", flush=True)
            return None
