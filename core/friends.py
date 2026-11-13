# -*- coding: utf-8 -*-
"""小焦 · 好友（2026-10-30）

【用户要的东西，原话】
  「不是这样，是聊天单独的聊天好友，聊天好友页面，用户定义角色，然后定义后他就是一个好友，
   这个好友跟活着一样有他自己的朋友圈等等，也可以和用户聊天培养感情，不要只等用户去发给他信息，
   他有事情也会主动找用户，去定义每个人而不是朋友圈，还有这个朋友圈是他自己发不是我们点击才发，
   把这个点击才发的模块去掉」

所以这个模块的模型是：**每个 AI 是一个好友**。
  · 用户先定义这个好友（名字 / 性格 / 关系 / 头像 / 火种）——定义一次，他就是一个"人"；
  · 每个好友有**自己的**：一对一聊天记录、朋友圈（他自己的动态）；
  · 他会**主动**找用户说话（`proactive_tick` 由载体判断该不该说，模型负责说什么），
    不需要用户先开口；每次主动都记账（为什么发、多久没聊、说没说得成）；
  · 聊天记录、未读数、最近一次互动都落盘 `logs/friends/<名字>.json`。

【边界】
  · 主动说话是**载体掷骰 + 冷却 + 每日上限**共同决定，不是"每轮必发"，也不会刷屏；
  · 模型没应答就不发（不编一句"在吗"糊弄），事件账里写明；
  · 好友 ≠ 模型：好友是"人设"，火种是"脑子"，同一个好友可以换火种，记忆与关系不变。
"""
import io
import json
import logging
import os
import random
import re
import threading
import time

LOG = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "logs", "friends")
_LOCK = threading.RLock()

_LLM = None
_ALT = None
_MODEL = ""

DEFAULTS = {
    "enabled": True,
    "check_interval_s": 180,     # 多久看一次"有没有话想说"
    "min_gap_min": 45,           # 同一个好友两条主动消息至少隔多久
    "prob": 0.35,                # 每次检查有多少几率想说话
    "max_per_day": 6,            # 每个好友每天最多主动几条
    "max_msgs": 300,             # 每个好友保留多少条聊天
}
_CFG = dict(DEFAULTS)


def _cfg_num(k, d):
    v = _CFG.get(k)
    if v is None or v == "":
        return float(d)
    try:
        return float(v)
    except Exception:      # noqa: silent-ok
        return float(d)


def install(llm_fn=None, alt_fn=None, model_name=""):
    global _LLM, _ALT, _MODEL
    if llm_fn is not None:
        _LLM = llm_fn
    if alt_fn is not None:
        _ALT = alt_fn
    if model_name:
        _MODEL = str(model_name)


def configure(**kw):
    for k, v in (kw or {}).items():
        if k in _CFG and v is not None:
            _CFG[k] = v
    return dict(_CFG)


def _ensure():
    os.makedirs(_DIR, exist_ok=True)


def _safe(name):
    return re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fa5]", "_", str(name or "好友"))[:40] or "friend"


def _path(name):
    return os.path.join(_DIR, _safe(name) + ".json")


def _load(name):
    try:
        with io.open(_path(name), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("messages", [])
            return d
    except Exception:      # noqa: silent-ok — 没有就是新的
        pass
    return {"name": str(name), "messages": [], "last_read_n": 0,
            "last_proactive_ts": "", "created": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _save(name, d):
    _ensure()
    with _LOCK:
        tmp = _path(name) + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=1))
        os.replace(tmp, _path(name))


def _event(kind, **kw):
    try:
        _ensure()
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        row.update(kw)
        with io.open(os.path.join(_DIR, "events.jsonl"), "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:      # noqa: silent-ok
        LOG.debug("好友记账失败：%s", e)


def _persona(name):
    try:
        from core import personas as PS
        p = PS.get(name)
        if (not p.get("persona")) and (not p.get("relation")):
            return None
        return p
    except Exception:      # noqa: silent-ok
        return None


def _personas_all():
    try:
        from core import personas as PS
        return [x for x in (PS.list_all() or []) if x.get("name")]
    except Exception:      # noqa: silent-ok
        return []


def _ask(messages, temperature=0.9):
    if _LLM is None:
        return "", ""
    for _t in (1, 2):
        try:
            txt = str(_LLM(messages, temperature=temperature) or "").strip()
        except Exception as e:      # noqa: silent-ok
            LOG.debug("好友·第%d次调用失败：%s", _t, e)
            txt = ""
        if len(txt) >= 2:
            return txt, (_MODEL or "当前火种")
    if _ALT is not None:
        try:
            txt, who = _ALT(messages, temperature)
            if len(str(txt or "")) >= 2:
                return txt, (who or "备用火种")
        except Exception as e:      # noqa: silent-ok
            LOG.debug("好友·换火种失败：%s", e)
    return "", ""


# ==================== 好友列表 / 一对一聊天 ====================
def list_friends():
    """所有人设都是"好友"；带上未读数、最近一条、朋友圈条数。"""
    out = []
    for p in _personas_all():
        nm = p.get("name")
        d = _load(nm)
        msgs = d.get("messages") or []
        last = msgs[-1] if msgs else {}
        n0 = int(d.get("last_read_n") or 0)
        unread = sum(1 for m in msgs[n0:] if m.get("from") == "friend")
        try:
            from core import moments as MO
            n_moments = len(MO.list_posts(limit=200, author=nm) or [])
        except Exception:      # noqa: silent-ok
            n_moments = 0
        out.append({"name": nm, "relation": p.get("relation") or "",
                    "persona": (p.get("persona") or "")[:80], "avatar": p.get("avatar") or "",
                    "model": p.get("model") or "", "unread": unread,
                    "moments": n_moments, "messages": len(msgs),
                    "last": str(last.get("text") or "")[:60],
                    "last_from": last.get("from") or "",
                    "last_ts": last.get("ts") or ""})
    out.sort(key=lambda x: str(x.get("last_ts") or ""), reverse=True)
    return out


def history(name, limit=100):
    d = _load(name)
    return (d.get("messages") or [])[-int(limit or 100):]


def mark_read(name):
    d = _load(name)
    d["last_read_n"] = len(d.get("messages") or [])
    _save(name, d)
    return {"ok": True, "n": d["last_read_n"]}


def _strip_think_local(text):
    """剥掉模型吐的思维块标签（`<think>` / `<思考>` 等）。

    【为什么这里也要有一份】好友消息是**模型直接生成、直接落盘**的出口，
    不经过聊天那条 `_strip_think` 流水线。实测用户看到好友气泡里是
    「<思考> 我需要先理解… </思考> 你好，我是小焦…」—— 推理和正文粘成一段。
    好友/群聊这些"社交出口"都必须自己剥一次；放在 `_append` 这个**唯一落盘点**最省事。
    """
    import re as _re
    s = str(text or "")
    # 尖括号那套 + **全角方括号那套**（`【思考】…【回答】`）都要认。
    #   实测：只认 `<思考>` 时，模型换成 `【思考】` 写，整段推理就被当成消息发出来了
    #   （用户截图：好友回的全是"我意识到刚才那串解释有点抽象…"）。
    if _re.search(r"</?(?:think|thinking|reasoning|思考|推理)>", s, _re.I):
        s = _re.sub(r"(?is)<(think|thinking|reasoning|思考|推理)>.*?</\1>", "", s)
        s = _re.sub(r"(?is)</?(think|thinking|reasoning|思考|推理)>", "", s)
    if "【" in s:
        m = _re.search(r"【回答】", s)
        if m:
            s = s[m.end():]
        s = _re.sub(r"(?is)【思考】.*?(?=【回答】|$)", "", s)
        s = _re.sub(r"(?is)【(思考|推理|回答)】", "", s)
    return _re.sub(r"\n{3,}", "\n\n", s).strip()


def _append(name, from_, text, by="", why=""):
    d = _load(name)
    msg = {"id": "%d%03d" % (int(time.time() * 1000), random.randint(0, 999)),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "from": from_,
           # **落盘前先剥标签**：这里是好友消息唯一的写入口，挡住就没别的口子漏
           "text": _strip_think_local(text)[:1500], "by": by, "why": why}
    d.setdefault("messages", []).append(msg)
    d["messages"] = d["messages"][-int(_cfg_num("max_msgs", 300)):]
    if from_ == "friend":
        d["last_proactive_ts"] = msg["ts"]
    _save(name, d)
    return msg


def _ctx_msgs(name, limit=10):
    """把最近几句拼成模型能看懂的上下文。"""
    who = _persona(name) or {"name": name}
    me = who.get("name") or name
    rel = who.get("relation") or ""
    lines = []
    for m in history(name, limit):
        lines.append("%s：%s" % ("你" if m.get("from") == "friend" else "他", m.get("text")))
    head = "你是%s" % me
    if rel:
        head += "（他给你的关系/身份：%s）" % rel
    head += "。"
    if who.get("persona"):
        head += "你的人物设定：" + str(who.get("persona"))[:400]
    return head, "\n".join(lines)


_SPEAKER_RE = None


def _clean_reply(txt, last_user=""):
    """把模型吐出来的东西整理成"一句能发出去的话"，并挡住几种明显的胡说。

    实测三种坏输出（用户截图里的原样）：
      ① **抄对话记录**：「你：嗯。你那边也没啥事，就睡琢磨，琢磨啥呢？说说。」——
         模型把「最近的对话」当剧本往下写，而不是回话；
      ② **带说话人前缀**：「你：」「他：」直接被当成消息发出去；
      ③ **复述对方原话**：把"嗯"再回一遍（等于没回）。
    这里：剥前缀 → 拦复述 → 拦空话。拦下来就返回 ""，由上层**如实说"这次没应答"**，
    绝不把垃圾当成"它说的话"落盘。
    """
    global _SPEAKER_RE
    if _SPEAKER_RE is None:
        import re as _re
        _SPEAKER_RE = _re.compile(r"^\s*(?:你|他|她|我|对方|用户|小焦|[^\s：:]{1,8})\s*[：:]\s*")
    s = str(txt or "").strip()
    s = s.strip('"“”')
    # 只剥**开头**那一层前缀（"你：xxx" → "xxx"），剥完可能还有一层（"你：他：xxx"）
    for _ in range(2):
        s2 = _SPEAKER_RE.sub("", s, count=1)
        if s2 == s:
            break
        s = s2.strip()
    s = s.split("\n")[0].strip()          # 只要第一句（模型爱往下续写）
    if not s:
        return ""
    lu = str(last_user or "").strip()
    if lu and (s == lu or lu in s and len(s) <= len(lu) + 6):
        return ""                          # 复述对方原话 → 当没回
    # 【"拿对方那句开头、然后接着往下写"= 在续写剧本，不是在回话】
    # 实测原样：「你：嗯。你那边也没啥事，就睡琢磨，琢磨啥呢？说说。」
    # 区分关键在**标点**：`嗯。你那边…` 是把"嗯"当台词引用完又续写；
    # 而 `嗯，我也刚下班…` 是自然接话（后接逗号）。只拦前者。
    # 【复述问题 ≠ 回答】实测它会把用户那句原样抄到开头再往下编：
    #   「在干嘛呢（把手机搁腿上，抬眼看你两下，叹气）问了六次了…」
    # 原来只拦"抄完紧跟句号"的情况；这里放开给 ≥3 字的整句复述一律拦。
    # （1~2 字的语气词如"嗯"仍允许后面用逗号自然接话。）
    if lu and len(lu) >= 3 and s.startswith(lu):
        return ""
    if (lu and s.startswith(lu) and len(s) > len(lu) + 8
            and s[len(lu)] in "。！？!?…：:\n"):
        return ""
    # 句子里还带着"某某："这种说话人标记 → 那是对话记录，不是一句话
    if "：" in s[1:] or ":" in s[1:]:
        return ""
    # 整句只是语气词（"嗯""好""哦"）→ 也不算回话（用户实测："嗯。"这种回了等于没回）
    if len(s) <= 2 and s in ("嗯", "哦", "好", "啊", "哈", "对", "是", "行"):
        return ""
    return s[:200]


def _recent_friend_texts(name, k=3):
    """最近 k 条**它自己**说过的话（用来判"是不是又说了同一句"）。"""
    d = _load(name)
    out = [str(m.get("text") or "") for m in (d.get("messages") or [])
           if m.get("from") == "friend"][-k:]
    return out


def _too_similar(a, b):
    """两句是不是基本一样（判"复读"）。用字符集重叠，够用且不引第三方库。"""
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / float(len(sa | sb)) >= 0.8


def say(name, text):
    """用户对某个好友说一句 → 他回（走快速一对一，不折腾工具）。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "空消息"}
    if _persona(name) is None:
        return {"ok": False, "error": "没有这个好友（先在好友页定义角色）"}
    _append(name, "user", text)
    _event("user_say", friend=name, chars=len(text))
    head, ctx = _ctx_msgs(name)
    # 【察觉"你在重复问"】用户连发同一句时，模型每轮都当新问题答，
    #   于是它对同一个问题给出越来越飘的答案（实测：刷手机→等你回话→刷视频→打游戏→追你）。
    #   把"已经问过几次"直接告诉模型，它才知道该点破，而不是继续编。
    _recent_user = [str(m.get("text") or "") for m in (_load(name).get("messages") or [])
                    if m.get("from") == "user"][-6:-1]
    _repeat = sum(1 for x in _recent_user if x.strip() == text)
    _rep_hint = ""
    if _repeat >= 1:
        _rep_hint = ("\n\n【重要】他这句话已经问过 %d 次了。**别再当新问题回答，"
                     "也别装作刚听到**：直接点出来（比如「你刚问过了」），然后说点别的。"
                     % (_repeat + 1))
    sys_p = (head + " 你们在微信一样的一对一聊天。**对方的名字你并不知道，就叫他「你」；"
             "绝对不要叫他「小焦」（那是助手自己的名字）**。像真人那样回他：短、口语、有情绪，"
             "可以反问一句。不要复述他说的话，不要客套。只输出你要说的话，40 字以内。"
             # 【必须明说】模型会把上面的「最近的对话」当成剧本往下续写，
             # 于是输出「你：…」这种**说话人前缀**；这两句就是堵它的。
             "**只写你说出口的那句话本身，不要写「你：」「他：」这类前缀，"
             "不要接着抄前面的对话。**"
             # 模型会编"你这消息怎么回得这么慢"这种**它无从知道的事**（它看不见时间/网络），
             # 这类"评论对方发消息的速度/系统/网络"的话一律不许说。
             "**不要评论对方回消息快慢、不要提网络/延迟/系统，你不知道这些。**")
    txt, by = _ask([{"role": "system", "content": sys_p},
                    {"role": "user", "content": "最近的对话：\n%s\n\n他刚说：%s%s" % (
                        ctx[-600:], text, _rep_hint)}])
    txt = _clean_reply(txt, text)
    # 【察觉"它在重复答"】同一句话说第二遍时，用户会以为它卡住了（实测截图）。
    #   先要求换一个说法重来一次；还是重复就**不落盘**，如实说"这句刚说过"。
    _prev = _recent_friend_texts(name, 3)
    if txt and any(_too_similar(txt, p) for p in _prev):
        txt2, by2 = _ask([{"role": "system", "content":
                           head + " 你刚才说过「%s」。现在**换一个完全不同的说法**，"
                                  "说点新的具体内容（别说同一件事），一到两句，只输出这句话。"
                                  % (_prev[-1][:40])},
                          {"role": "user", "content": text}])
        txt = _clean_reply(txt2, text)
        by = by2 or by
        if txt and any(_too_similar(txt, p) for p in _prev):
            _event("reply_skip", friend=name, why="和刚才那句一样（不重复发）")
            return {"ok": True, "reply": None, "why": "这句话刚说过，我让它换个说法也没换出来"}
    if not txt:
        # 一次重试：换更硬的约束（模型小，第一次容易顺着剧本写）
        txt2, by2 = _ask([{"role": "system", "content":
                           head + " 现在只回一句话：对他刚说的那句，说一到两句具体的话，"
                                  "不要复述他的话，不要以「你：」开头。只输出这句话。"},
                          {"role": "user", "content": text}])
        txt = _clean_reply(txt2, text)
        by = by2 or by
    if not txt:
        _event("reply_skip", friend=name, why="模型没给出可用的话（不编一句假的）")
        return {"ok": True, "reply": None, "why": "模型这次没给出能发的话"}
    msg = _append(name, "friend", txt, by=by, why="回你")
    _event("reply", friend=name, chars=len(txt), by=by)
    return {"ok": True, "reply": msg}


def proactive(name, force=False):
    """这个好友**自己**想起一件事，主动找你（不需要你先说话）。"""
    if _persona(name) is None:
        return None, "不是好友（先定义角色）"
    if not _CFG.get("enabled", True) and not force:
        return None, "好友主动说话开关是关的"
    d = _load(name)
    msgs = d.get("messages") or []
    today = time.strftime("%Y-%m-%d")
    n_today = sum(1 for m in msgs
                  if m.get("from") == "friend" and str(m.get("ts") or "").startswith(today)
                  and (m.get("why") or "") != "回你")
    if not force and n_today >= int(_cfg_num("max_per_day", 6)):
        return None, "今天他已经主动找过你 %d 次了" % n_today
    if not force and msgs:
        try:
            last = time.mktime(time.strptime(str(msgs[-1]["ts"]), "%Y-%m-%dT%H:%M:%S"))
            gap = (time.time() - last) / 60.0
            need = _cfg_num("min_gap_min", 45)
            if gap < need:
                return None, "才 %.0f 分钟没说话（至少 %d 分钟）" % (gap, need)
        except Exception:      # noqa: silent-ok
            pass
    roll = random.random()
    prob = _cfg_num("prob", 0.35)
    if not force and roll > prob:
        _event("skip", friend=name, why="掷骰没中", roll=round(roll, 3), prob=prob)
        return None, "掷骰 %.2f > %.2f，这次不想说" % (roll, prob)
    head, ctx = _ctx_msgs(name)
    # 他最近发的朋友圈也能当话题
    own_moments = ""
    try:
        from core import moments as MO
        ps = MO.list_posts(limit=3, author=name) or []
        if ps:
            own_moments = "；".join(str(p.get("text"))[:60] for p in ps)
    except Exception:      # noqa: silent-ok
        own_moments = ""
    sys_p = (head + " **对方的名字你不知道，就叫他「你」，不要叫「小焦」。**"
             "现在没人跟你说话。你自己忽然想起一件事（或想问他一句），"
             "**主动**给他发条消息：一两句，像真人随手发的那种，不要客套开场，"
             "不要说「在吗」这种废话。只输出这条消息。")
    usr = "你们最近的对话：\n%s" % (ctx[-500:] or "（还没聊过）")
    if own_moments:
        usr += "\n你最近发过的动态：%s" % own_moments
    txt, by = _ask([{"role": "system", "content": sys_p},
                    {"role": "user", "content": usr}], temperature=0.95)
    if not txt:
        _event("skip", friend=name, why="模型没应答（宁可不发也不编）")
        return None, "模型这次没应答"
    why = "自己想起来找你说" if msgs else "第一次主动打招呼"
    msg = _append(name, "friend", txt, by=by, why=why)
    _event("proactive", friend=name, chars=len(txt), by=by, why=why)
    LOG.info("好友主动：%s → %s", name, str(txt)[:60])
    # 【他有自己的生活】顺手发一条自己的朋友圈（35% 几率），内容也是他自己写的
    try:
        if random.random() < 0.35:
            from core import moments as _MO
            _mtxt, _mby, _ = _MO.compose(
                context="我最近在想：%s\n我刚跟朋友说：%s" % (ctx[-200:] or "（没什么）", txt),
                author=name)
            if _mtxt:
                _MO.publish(_mtxt, author=name, by=_mby, source="self",
                            why="自己想起来发的")
                _event("moment", friend=name, chars=len(_mtxt))
                LOG.info("好友动态：%s 自己发了一条（%d 字）", name, len(_mtxt))
    except Exception as _e:      # noqa: silent-ok — 发动态失败不影响这次主动说话
        LOG.debug("好友发动态失败（忽略）：%s", _e)
    return msg, why


def proactive_tick():
    """载体循环调它：轮着看每个好友"想不想说话"。"""
    if not _CFG.get("enabled", True):
        return []
    out = []
    names = [f["name"] for f in list_friends()]
    random.shuffle(names)
    for nm in names:
        try:
            msg, why = proactive(nm)
        except Exception as e:      # noqa: silent-ok — 一个人挂了不影响别人
            LOG.debug("好友主动失败（%s）：%s", nm, e)
            msg, why = None, str(e)[:60]
        if msg:
            out.append({"friend": nm, "text": msg.get("text"), "why": why})
    return out


def stats():
    fs = list_friends()
    return {"friends": len(fs), "unread": sum(f["unread"] for f in fs),
            "messages": sum(f["messages"] for f in fs),
            "moments": sum(f["moments"] for f in fs), "cfg": dict(_CFG), "path": _DIR}
