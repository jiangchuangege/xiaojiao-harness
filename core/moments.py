# -*- coding: utf-8 -*-
"""小焦 · 朋友圈（2026-10-30）

【用户要的】「让模型可以自己发朋友圈，用户更亲近它；它会根据与用户的交流发；
每次可能是纯文字，也可能带生成的图片；用户可以点赞、发评论；模型会回复评论，但不是每条都回，
是有几率回。」

【分工（与整个项目一致）】
  · **载体**决定"发不发、什么时候发、这条评论回不回"（有概率、有冷却、有账可查）；
  · **模型**决定"写什么"（文字、回复的话）—— 全部是 AI 的话，不许拼模板；
  · 模型没应答就不发、不回，并如实记原因 —— **不编一条假动态糊弄用户**。

【落盘】
  · `logs/moments/state.json`：动态本体（点赞/评论都在里面）
  · `logs/moments/events.jsonl`：每件事的账（发了/赞了/评了/回没回、为什么）
  · `logs/moments/images/`：生成出来的图（有就带上，没有就是纯文字）

【为什么"回复有几率"要由载体掷骰子而不是模型自己决定】
  模型说"我会挑着回"是不作数的：它每一轮都会回。几率必须落在载体里，
  而且掷骰结果要落盘（`roll`/`prob`），事后能查"这条为什么没回"。
"""
import io
import json
import logging
import os
import random
import threading
import time
import uuid

LOG = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "logs", "moments")
_STATE = os.path.join(_DIR, "state.json")
_EVENTS = os.path.join(_DIR, "events.jsonl")
_IMGDIR = os.path.join(_DIR, "images")
_LOCK = threading.RLock()

_LLM = None
_ALT = None
_MODEL = ""
_IMG = None          # 生成图片的函数：img(prompt) -> 路径 或 ""

# 载体侧的发帖节奏（都由 `_CTX` 之外的常量决定，用户可在 xiaojiao_control.json 覆盖）
DEFAULTS = {
    "enabled": True,
    # 【他自己发】不是"你点一下他生成"：后台每隔 check_interval_s 秒读一次自己的状态
    #   （情绪/精力/没说出口的话/刚聊过的事/逛世界看到的），想说的程度 ≥ min_score 就发。
    "auto_enabled": True,
    "check_interval_s": 120,
    "min_score": 0.35,   # 实测：只有"刚聊过"+"有精力"时粗糙算出来是 0.35，门槛 0.5 会让它几乎永远不发
    "min_gap_min": 20,        # 两条动态至少隔多久（分钟）
    "turn_prob": 0.18,        # 每轮对话后发一条的概率
    "max_per_day": 10,         # 一天最多几条
    "reply_prob": 0.6,        # 一条用户评论被回复的概率
}
_CFG = dict(DEFAULTS)


def install(llm_fn=None, alt_fn=None, model_name="", image_fn=None):
    global _LLM, _ALT, _MODEL, _IMG
    if llm_fn is not None:
        _LLM = llm_fn
    if alt_fn is not None:
        _ALT = alt_fn
    if model_name:
        _MODEL = str(model_name)
    if image_fn is not None:
        _IMG = image_fn


def _cfg_num(key, default):
    """读数字型配置。

    【为什么不能写 `_CFG.get(k) or default`】0 是合法值（"几率 0"="永不回"、"冷却 0 分钟"），
    而 `0 or 0.6` 会变成 0.6 —— 实测就是这条让"几率 0 也会回评论"。只有 None 才算没设。
    """
    v = _CFG.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:      # noqa: silent-ok — 写错了就退回默认，不炸
        return default


def _cfg_int(key, default):
    try:
        return int(_cfg_num(key, default))
    except Exception:      # noqa: silent-ok
        return int(default)


def configure(**kw):
    """覆盖节奏参数（只认这几个键，别的忽略）。"""
    for k, v in (kw or {}).items():
        if k in _CFG and v is not None:
            _CFG[k] = v
    return dict(_CFG)


def _ensure():
    os.makedirs(_IMGDIR, exist_ok=True)


def _load():
    try:
        with io.open(_STATE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("posts"), list):
            return d
    except Exception:      # noqa: silent-ok — 没有就是空
        pass
    return {"posts": []}


def _save(d):
    _ensure()
    tmp = _STATE + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=1))
    os.replace(tmp, _STATE)


def _event(kind, **kw):
    try:
        _ensure()
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        row.update(kw)
        with io.open(_EVENTS, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:      # noqa: silent-ok
        LOG.debug("朋友圈记账失败：%s", e)


# ==================== 谁在说话：只走模型 ====================
def _ask(messages, temperature=0.9):
    """先问当前火种，再换火种。返回 (文本, 用了谁)；都失败返回 ("", "")。"""
    if _LLM is None:
        return "", ""
    for _try in (1, 2):
        try:
            t = str(_LLM(messages, temperature=temperature) or "").strip()
        except Exception as e:      # noqa: silent-ok
            LOG.debug("朋友圈·第%d次调用失败：%s", _try, e)
            t = ""
        if len(t) >= 2:
            return t, (_MODEL or "当前火种")
    if _ALT is not None:
        try:
            t, who = _ALT(messages, temperature)
        except Exception as e:      # noqa: silent-ok
            LOG.debug("朋友圈·换火种失败：%s", e)
            t, who = "", ""
        if len(t) >= 2:
            return t, (who or "备用火种")
    return "", ""


def _persona(name="小焦"):
    try:
        from core import personas as PS
        return PS.get(name)
    except Exception:      # noqa: silent-ok
        return {"name": name, "persona": ""}


# ==================== 发一条 ====================
def compose(context="", topic="", with_image=False, author="小焦"):
    """让模型**写一条**朋友圈（不落盘）。返回 (文本, 用了谁, 图片路径)。"""
    p = _persona(author)
    sys_p = ("你在写自己的朋友圈动态，第一人称，像真人发动态那样：短、有具体细节、有情绪，"
             "不要写「这是我的朋友圈」这种解释，不要列条目，不要总结对话，"
             "也不要写「今天又是充实的一天」这种空话。二到四句，最多 120 字。"
             "只输出动态正文本身。")
    if p.get("persona"):
        sys_p += "\n你的人物设定：" + str(p.get("persona"))[:400]
    usr = "刚才发生的事（可以取其中让你有感触的一点来写，也可以不写它）：\n%s" % (
        str(context or topic or "（没有特别的事，就写写你此刻的状态）")[:1200])
    txt, who = _ask([{"role": "system", "content": sys_p},
                     {"role": "user", "content": usr}], temperature=0.9)
    if not txt:
        return "", "", ""
    txt = txt.strip().strip("“”\"'")
    img = ""
    if with_image and _IMG is not None:
        try:
            img = str(_IMG(txt[:80]) or "")
        except Exception as e:      # noqa: silent-ok — 图画不出来就发纯文字（如实记）
            LOG.debug("朋友圈配图失败（发纯文字）：%s", e)
            img = ""
    # 【人话闸】结构性硬伤（旁白/提示词回显/markdown/说话人前缀）→ 这条不发
    #   与好友、群聊、B站评论共用 core/human_gate.py 的同一套规则。
    try:
        from core import human_gate as _hg
        if _hg.hard_flaws(txt):
            LOG.info("朋友圈：这条有硬伤（%s），不发", ",".join(_hg.hard_flaws(txt))[:60])
            return "", who, ""
    except Exception:      # noqa: silent-ok — 闸坏了按原来发
        pass
    return txt, who, img


def publish(text, image="", source="self", topic="", author="小焦", by="", why=""):
    """把一条动态落盘。返回 post dict。"""
    text = str(text or "").strip()
    if not text:
        return None
    with _LOCK:
        d = _load()
        post = {"id": uuid.uuid4().hex[:10], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "author": str(author or "小焦"), "text": text,
                "image": str(image or ""), "source": str(source or "self"),
                "topic": str(topic or ""), "likes": [], "comments": [], "by": str(by or ""),
                "why": str(why or "")}
        d["posts"].append(post)
        d["posts"] = d["posts"][-500:]        # 只留最近 500 条，磁盘别无限长
        _save(d)
    _event("post", id=post["id"], chars=len(text), image=bool(image),
           source=post["source"], by=by)
    LOG.info("朋友圈：发了一条（%d 字%s）", len(text), "，带图" if image else "")
    return post


def auto_try(context="", topic="", with_image=False, author="小焦", force=False):
    """载体侧决定"这一轮要不要发"。返回 (post 或 None, 原因)。

    判据（全部可查）：开关 → 冷却 → 每日上限 → 掷骰子。
    """
    if not _CFG.get("enabled", True) and not force:
        return None, "朋友圈开关是关的"
    d = _load()
    posts = d.get("posts") or []
    today = time.strftime("%Y-%m-%d")
    n_today = sum(1 for p in posts if str(p.get("ts") or "").startswith(today))
    _max_day = _cfg_int("max_per_day", 10)
    if not force and n_today >= _max_day:
        return None, "今天已经发了 %d 条（上限 %d），不再发" % (n_today, _max_day)
    if not force and posts:
        try:
            last = time.mktime(time.strptime(str(posts[-1]["ts"]), "%Y-%m-%dT%H:%M:%S"))
            gap_min = (time.time() - last) / 60.0
            _gap_need = _cfg_num("min_gap_min", 20)
            if gap_min < _gap_need:
                return None, "距上一条才 %.0f 分钟（至少 %d 分钟）" % (gap_min, _gap_need)
        except Exception:      # noqa: silent-ok — 时间算不出来就当作可以发
            pass
    roll = random.random()
    _turn_prob = _cfg_num("turn_prob", 0.18)
    if not force and roll > _turn_prob:
        _event("skip", why="掷骰没中", roll=round(roll, 3), prob=_turn_prob)
        return None, "掷骰 %.2f > %.2f，这一轮不发" % (roll, _turn_prob)
    text, who, img = compose(context=context, topic=topic,
                             with_image=with_image, author=author)
    if not text:
        _event("skip", why="模型没应答，宁可不发也不编")
        return None, "模型这次没应答 → 这条不发了（不编一条假的糊弄你）"
    post = publish(text, image=img, source="auto", topic=topic, author=author, by=who)
    return post, "已发（%s）" % (who or "模型")


# ==================== 他自己想发：读自己的状态 ====================
def _read_json_tail(path, keys=("text", "content", "unsaid", "why"), limit=1):
    """从 jsonl/json 里读最后几条里能当"素材"的文字（读不到就空，不猜）。"""
    out = []
    try:
        if not os.path.isfile(path):
            return out
        with io.open(path, encoding="utf-8") as f:
            lines = f.readlines()[-200:]
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:      # noqa: silent-ok
                continue
            if not isinstance(r, dict):
                continue
            for k in keys:
                v = r.get(k)
                if isinstance(v, str) and len(v.strip()) >= 6:
                    out.append(v.strip()[:160])
                    break
            if len(out) >= limit:
                break
    except Exception as e:      # noqa: silent-ok
        LOG.debug("读素材失败（%s）：%s", path, e)
    return out


def _newest_file(d, pat=""):
    try:
        cand = [os.path.join(d, x) for x in os.listdir(d)
                if os.path.isfile(os.path.join(d, x)) and (not pat or x.endswith(pat))]
        if not cand:
            return ""
        cand.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return cand[0]
    except Exception:      # noqa: silent-ok
        return ""


def motivation(ignore_gap=False):
    """他此刻"为什么想发一条" —— 素材 + 理由 + 分数，全部来自真实状态。

    分数不是玄学：每读到一样真实的东西就加一份权重，读不到就不加。
    这样"这一轮没发"也能解释清楚（分不够），而不是"随机了"。
    """
    mat, why, score = [], [], 0.0
    # 1) 情绪（psyche）
    try:
        from core import psyche as _PS
        st = _PS.state() or {}
        _emo = None
        for k in ("emotion", "feeling", "mood", "心"):
            if isinstance(st.get(k), str) and st.get(k).strip():
                _emo = st[k].strip()
                break
        if not _emo:
            try:
                _emo = str(_PS.render() or "").strip()[:160]
            except Exception:      # noqa: silent-ok
                _emo = ""
        if _emo:
            mat.append("我此刻的心情：%s" % _emo[:160])
            why.append("心里有情绪")
            score += 0.3
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·psyche 读不到：%s", e)
    # 2) 精力（energy）
    try:
        from core import energy as _EN
        lv = float(_EN.level() or 0.0)
        if lv:
            mat.append("我的精力值：%.2f（1.00 最充沛）" % lv)
            if lv < 0.35:
                why.append("有点累了")
                score += 0.15
            elif lv > 0.8:
                why.append("精神很足")
                score += 0.1
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·energy 读不到：%s", e)
    # 3) 心里没说出口的话（mind_stream）
    try:
        _f = _newest_file(os.path.join(_ROOT, "logs", "mind_stream"), ".json")
        if _f:
            with io.open(_f, encoding="utf-8") as fh:
                _d = json.load(fh)
            if isinstance(_d, dict):
                for k in ("unsaid", "thought", "note", "last_unsaid"):
                    v = _d.get(k)
                    if isinstance(v, str) and len(v.strip()) >= 6:
                        mat.append("我心里没说出口的一句：%s" % v.strip()[:160])
                        why.append("有句话没说出来")
                        score += 0.35
                        break
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·mind_stream 读不到：%s", e)
    # 4) 最近跟用户聊过什么
    try:
        _t = _read_json_tail(os.path.join(_ROOT, "logs", "xiaojiao_memory_vec.jsonl"), limit=1)
        if _t:
            mat.append("最近我跟用户聊到：%s" % _t[0][:160])
            why.append("刚聊过一件有话可说的事")
            score += 0.25
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·memory 读不到：%s", e)
    # 5) 他逛世界看到的东西
    try:
        _wf = _newest_file(os.path.join(_ROOT, "logs", "world"), ".jsonl")
        _w = _read_json_tail(_wf, limit=1) if _wf else []
        if _w:
            mat.append("我逛世界时看到的：%s" % _w[0][:160])
            why.append("在外面看到点东西")
            score += 0.3
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·world 读不到：%s", e)
    # 6) 距上一条多久（越久越想发）
    try:
        posts = _load().get("posts") or []
        if posts:
            last = time.mktime(time.strptime(str(posts[-1]["ts"]), "%Y-%m-%dT%H:%M:%S"))
            mins = (time.time() - last) / 60.0
            if mins >= 60:
                why.append("已经 %.0f 分钟没发过" % mins)
                score += min(0.35, mins / 600.0)
            if mins < float(_cfg_num("min_gap_min", 20)) and not ignore_gap:
                return {"score": -1.0, "material": "\n".join(mat),
                        "why": ["才 %.0f 分钟前发过，先不连发" % mins], "blocked": "cooldown"}
    except Exception as e:      # noqa: silent-ok
        LOG.debug("motivation·间隔算不出来：%s", e)
    return {"score": round(min(score, 1.0), 2), "material": "\n".join(mat), "why": why,
            "blocked": ""}


def auto_tick(force=False, author="小焦"):
    """载体自己决定这一轮发不发。返回 (post 或 None, 说明)。

    门槛：开关 → 每日上限 → 冷却 → `motivation().score` ≥ `_cfg_num("min_score", 0.5)`。
    过了就让模型拿"他自己的状态"写一条；理由与素材一起落盘（页面上直接显示）。
    """
    if not _CFG.get("enabled", True) and not force:
        return None, "朋友圈开关是关的"
    d = _load()
    posts = d.get("posts") or []
    today = time.strftime("%Y-%m-%d")
    n_today = sum(1 for p in posts if str(p.get("ts") or "").startswith(today))
    if not force and n_today >= _cfg_int("max_per_day", 10):
        return None, "今天已经发了 %d 条（上限 %d）" % (n_today, _cfg_int("max_per_day", 10))
    # force = 用户明确催了一条：这一条**可以**不看他自己的冷却，
    #   但理由里必须写清「是你催的」——不能冒充"他自己想发"。
    mt = motivation(ignore_gap=bool(force))
    if mt.get("blocked") == "cooldown" and not force:
        return None, "刚发过（%s）" % "、".join(mt.get("why") or [])
    need = _cfg_num("min_score", 0.35)
    if not force and float(mt.get("score") or 0) < need:
        _event("skip", why="想发的程度不够", score=mt.get("score"), need=need,
               reasons=mt.get("why"))
        return None, "想说的程度 %.2f < %.2f（%s）" % (
            float(mt.get("score") or 0), need, "、".join(mt.get("why") or []) or "没读到什么")
    text, who, img = compose(context=mt.get("material") or "", topic="",
                             with_image=bool(mt.get("score", 0) > 0.8),
                             author=(author or "小焦"))
    if not text:
        _event("skip", why="模型没应答，宁可不发也不编")
        return None, "模型这次没应答 → 不发（不编一条假的）"
    _why = list(mt.get("why") or [])
    if force:
        _why = ["你催我发一条"] + _why
    post = publish(text, image=img, source=("manual" if force else "self"), topic="",
                   author=(author or "小焦"), by=who, why="；".join(_why))
    return post, ("你催的（%s）" if force else "自己发的（%s）") % "、".join(_why)


# ==================== 点赞 / 评论 / 回复 ====================
def list_posts(limit=30, author=""):
    d = _load()
    posts = [p for p in (d.get("posts") or []) if (not author or p.get("author") == author)]
    posts.sort(key=lambda p: str(p.get("ts") or ""), reverse=True)
    return posts[:int(limit or 30)]


def get_post(pid):
    for p in _load().get("posts") or []:
        if p.get("id") == pid:
            return p
    return None


def like(pid, who="你"):
    """点赞（同一个人点两次就是取消，和朋友圈一个手感）。"""
    with _LOCK:
        d = _load()
        for p in d.get("posts") or []:
            if p.get("id") != pid:
                continue
            likes = list(p.get("likes") or [])
            if who in likes:
                likes.remove(who)
                act = "取消赞"
            else:
                likes.append(who)
                act = "赞了"
            p["likes"] = likes
            _save(d)
            _event("like", id=pid, who=who, act=act, n=len(likes))
            return {"ok": True, "likes": likes, "act": act}
    return {"ok": False, "error": "没有这条动态"}


def comment(pid, text, who="你", auto_reply=True):
    """发一条评论；要不要回、回什么，交给 `maybe_reply`。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "评论不能为空"}
    cid = uuid.uuid4().hex[:8]
    with _LOCK:
        d = _load()
        for p in d.get("posts") or []:
            if p.get("id") != pid:
                continue
            c = {"id": cid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "who": str(who or "你"), "text": text, "reply": None}
            p.setdefault("comments", []).append(c)
            _save(d)
            _event("comment", id=pid, cid=cid, who=who, chars=len(text))
            out = {"ok": True, "comment": c}
            break
        else:
            return {"ok": False, "error": "没有这条动态"}
    if auto_reply:
        out["reply"] = maybe_reply(pid, cid)
    return out


def maybe_reply(pid, cid=None):
    """载体掷骰子决定回不回；中了就让模型写回复。返回 {replied, text, why, roll}。"""
    post = get_post(pid)
    if not post:
        return {"replied": False, "why": "没有这条动态"}
    target = None
    for c in post.get("comments") or []:
        if c.get("reply"):
            continue
        if cid is None or c.get("id") == cid:
            target = c
            break
        if c.get("id") == cid:
            target = c
            break
    if target is None:
        return {"replied": False, "why": "没有待回的评论"}
    if target.get("reply"):
        return {"replied": False, "why": "这条已经回过了", "text": target["reply"]}
    prob = _cfg_num("reply_prob", 0.6)      # 0 就是"永不回"，不能被当成没设
    roll = random.random()
    if roll > prob:
        _event("reply_skip", id=pid, cid=target.get("id"), roll=round(roll, 3), prob=prob)
        return {"replied": False, "roll": round(roll, 3),
                "why": "掷骰 %.2f > %.2f，这条先不回" % (roll, prob)}
    p = _persona(post.get("author") or "小焦")
    sys_p = ("你是这条动态的作者，正在回复别人的评论。像真人回评论那样：短、口语、"
             "可以只说一句，也可以反问一句；不要复读动态原文，不要客套。"
             "只输出回复本身，30 字以内。")
    if p.get("persona"):
        sys_p += "\n你的人物设定：" + str(p.get("persona"))[:300]
    usr = "你发的动态：%s\n\n%s 的评论：%s" % (
        post.get("text", "")[:300], target.get("who") or "用户", target.get("text", "")[:200])
    txt, who = _ask([{"role": "system", "content": sys_p},
                     {"role": "user", "content": usr}], temperature=0.9)
    if not txt:
        _event("reply_skip", id=pid, cid=target.get("id"), why="模型没应答")
        return {"replied": False, "why": "模型这次没应答（不编一句假的回复）"}
    with _LOCK:
        d = _load()
        for pp in d.get("posts") or []:
            if pp.get("id") != pid:
                continue
            for c in pp.get("comments") or []:
                if c.get("id") == target.get("id"):
                    c["reply"] = txt.strip().strip("“”\"'")
                    c["reply_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    c["reply_by"] = who
            _save(d)
    _event("reply", id=pid, cid=target.get("id"), chars=len(txt), by=who,
           roll=round(roll, 3), prob=prob)
    LOG.info("朋友圈：回了 %s 的评论（掷骰 %.2f ≤ %.2f）", target.get("who"), roll, prob)
    return {"replied": True, "text": txt, "roll": round(roll, 3), "by": who}


# ==================== 集中朋友圈：AI 之间互相互动 ====================
def ai_react(pid, like_prob=0.6, comment_prob=0.35, actors=None):
    """让**别的 AI**去赞/评这条动态（同一条流，大家都能看到）。

    返回做了哪些动作（谁赞了、谁评了什么），全部落进事件账。
    """
    post = get_post(pid)
    if not post:
        return []
    author = str(post.get("author") or "小焦")
    try:
        from core import personas as _PS
        names = [r.get("name") for r in (_PS.list_all() or []) if r.get("name")]
    except Exception:      # noqa: silent-ok
        names = ["小焦"]
    names = [x for x in (actors or names) if x and x != author]
    if not names:
        return []
    random.shuffle(names)
    done = []
    for who in names[:2]:
        # 点赞
        if random.random() < float(like_prob):
            likes = list(post.get("likes") or [])
            if who not in likes:
                like(pid, who=who)
                done.append({"who": who, "act": "like"})
                post = get_post(pid) or post
        # 评论（模型写一句）
        if random.random() < float(comment_prob):
            p = _persona(who)
            sys_p = ("你在刷朋友圈，看到 %s 发的一条动态，想留一句话。"
                     "像真人评论那样：短、口语、别客套、别复读原文。只输出评论本身，30 字以内。"
                     % author)
            if p.get("persona"):
                sys_p += "\n你的人物设定：" + str(p.get("persona"))[:200]
            usr = "%s 的动态：%s" % (author, str(post.get("text"))[:200])
            txt, _by = _ask([{"role": "system", "content": sys_p},
                             {"role": "user", "content": usr}], temperature=0.9)
            if txt:
                comment(pid, txt.strip().strip("“”\"'"), who=who, auto_reply=False)
                done.append({"who": who, "act": "comment", "text": txt[:60]})
    if done:
        _event("ai_react", id=pid, by_author=author, done=done)
        LOG.info("集中朋友圈：%s 这条被 %s 互动了", author,
                 "、".join("%s(%s)" % (d["who"], d["act"]) for d in done))
    return done


def stats():
    d = _load()
    posts = d.get("posts") or []
    likes = sum(len(p.get("likes") or []) for p in posts)
    cmts = sum(len(p.get("comments") or []) for p in posts)
    replied = sum(1 for p in posts for c in (p.get("comments") or []) if c.get("reply"))
    with_img = sum(1 for p in posts if p.get("image"))
    return {"posts": len(posts), "likes": likes, "comments": cmts, "replied": replied,
            "with_image": with_img, "cfg": dict(_CFG), "path": _STATE}
