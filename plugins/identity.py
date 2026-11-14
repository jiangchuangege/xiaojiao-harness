"""
身份插件 —— 让对方（和它自己）的身份成为**载体里的设定**，不靠模型猜。

【为什么要这个插件（用户实测逼出来的）】一句「你好」，它答「哟，这不是我熟悉的老张嘛」——
"老张"是它编的；又说「小焦好呀」，把**它自己**的名字安到了用户头上。
根因不在提示词：本地这颗 4B 是从本项目日志里蒸馏出来的，日志里满是"小焦/老张"这类称呼，
于是它把训练语料里的称呼当成了"眼前这个人"。**提示词压不住权重，设定可以**：
把"对方叫什么、该怎么称呼、我在哪个城市"写进一个由**载体**掌管的文件，
每轮作为事实注入 system —— 这才叫"载体优先"。

【存哪】`logs/identity.json`（logs 不入库，属于用户私有数据）。
【谁能改】模型通过 `identity_set` 工具改（用户说"以后叫我老板"它就记下来），
        也可以直接手改这个 json —— 载体每轮现读，改完下一句就生效。

工具：
  · identity_get   读当前身份
  · identity_set   设一个字段（call_as / user_name / city / note / self_name）
  · identity_clear 清掉一个字段
字段：
  · call_as   对方希望你**怎么称呼他**（最常用；"老板""学姐""老李"）
  · user_name 对方的名字（真名/网名，可空）
  · city      对方所在城市（可空）
  · note      一句备注（可空）
  · self_name **它自己**叫什么（默认"小焦"）
"""

from __future__ import annotations

import json
import os
import threading

_ID_FILE = os.path.join("logs", "identity.json")
_LOCK = threading.Lock()
FIELDS = ("call_as", "user_name", "city", "note", "self_name")
_DEFAULTS = {"call_as": "", "user_name": "", "city": "", "note": "", "self_name": "小焦"}
_LABEL = {"call_as": "对方希望你称呼他", "user_name": "对方的名字",
          "city": "对方所在的城市", "note": "关于对方的备注", "self_name": "它自己的名字"}


def _load() -> dict:
    try:
        with open(_ID_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:      # noqa: silent-ok — 没有就是空设定
        d = {}
    out = dict(_DEFAULTS)
    if isinstance(d, dict):
        for k in FIELDS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
    return out


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(_ID_FILE), exist_ok=True)
    tmp = _ID_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: d.get(k, "") for k in FIELDS}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _ID_FILE)          # 原子替换：写到一半断电也不会留下半个 json


def hint() -> str:
    """给 system 用的一句事实（没有设定也给一句 —— 那句"别瞎猜称呼"正是用户要的）。"""
    d = _load()
    parts = []
    if d["call_as"]:
        parts.append("对方希望你称呼他「%s」。" % d["call_as"])
    elif d["user_name"]:
        parts.append("对方叫「%s」，可以直接这么称呼他。" % d["user_name"])
    if d["city"]:
        parts.append("他在%s。" % d["city"])
    if d["note"]:
        parts.append("备注：%s。" % d["note"])
    me = d["self_name"] or "小焦"
    head = "你是%s。" % me
    if not parts:
        # 【注意】这里**不许举例名字**（曾经写过"别叫「老张」「老板」这种"）——
        #   实测举例等于给模型递词：它会把例子里的名字直接叫出来。只说"别编称呼"就够。
        return (head + "还不知道对方怎么称呼 —— **不要瞎猜名字，也不要自己编一个称呼**；"
                "就直接说话。对方一旦告诉你怎么叫，就用 identity_set 记下来。")
    return head + "".join(parts) + "**照这个称呼他，不要自己编别的名字。**"


class IdentityPlugin:
    """身份设定：把"对方是谁、该怎么称呼"交给载体掌着。"""

    def get_tool_descriptions(self):
        return [
            {
                "name": "identity_get",
                "description": "读当前身份设定（对方怎么称呼、名字、城市、它自己的名字）。"
                               "什么时候用：想知道该怎么称呼对方、或用户问「你叫我什么」时。无参数。",
                "parameters": {},
            },
            {
                "name": "identity_set",
                "description": "记住对方的身份设定。什么时候用：用户说「以后叫我老板」「我叫XX」"
                               "「我在杭州」「你以后叫自己XX」时**立刻**调用，不要只在嘴上答应。"
                               "输入 field（call_as/user_name/city/note/self_name）和 value。",
                "parameters": {"field": "字段名", "value": "要记的值"},
            },
            {
                "name": "identity_clear",
                "description": "清掉某一项身份设定（用户说「别这么叫我了」「忘掉我的城市」时用）。"
                               "输入 field。",
                "parameters": {"field": "字段名"},
            },
        ]

    def execute(self, tool_name, params):
        params = params or {}
        if tool_name == "identity_get":
            d = _load()
            shown = {_LABEL[k]: (d[k] or "（未设置）") for k in FIELDS}
            return "当前身份设定：\n" + "\n".join("- %s：%s" % (k, v) for k, v in shown.items())

        if tool_name == "identity_set":
            field = str(params.get("field") or "").strip()
            value = str(params.get("value") or "").strip()
            if field not in FIELDS:
                return "没有「%s」这一项。可选的：%s" % (field, " / ".join(FIELDS))
            if not value:
                return "值是空的，没有改。要清掉就用 identity_clear。"
            with _LOCK:
                d = _load()
                d[field] = value[:60]
                _save(d)
            return "记下了：%s = 「%s」。下一句起就按这个来。" % (_LABEL[field], value[:60])

        if tool_name == "identity_clear":
            field = str(params.get("field") or "").strip()
            if field not in FIELDS:
                return "没有「%s」这一项。可选的：%s" % (field, " / ".join(FIELDS))
            with _LOCK:
                d = _load()
                d[field] = _DEFAULTS[field]
                _save(d)
            return "已清掉：%s。" % _LABEL[field]

        return "身份插件里没有「%s」这个工具。" % tool_name
