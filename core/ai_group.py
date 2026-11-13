# -*- coding: utf-8 -*-
"""小焦 · 模型群组（2026-10-30）

【用户要的】「模型与模型之间互相交流，跟人类群组一样，互相聊天；群可能是网络开发、可能是处对象，
各种群由用户设定；用户可以给每个 AI 设置性格人设。」

【分工】
  · **用户**建群、定主题、定成员、定每个人的人设（`core/personas` 里也只有一份人设）；
  · **载体**排轮次、拼上下文、限长、防复读、落盘（`logs/aigroups/groups.json`）；
  · **模型**说话 —— 每个成员说的是它自己那颗火种写的话，全部是 AI 的话。

【为什么"轮流说"要由载体排】
  让模型自己决定下一个谁说，它每轮都会抢着说、还会互相总结；群聊的节奏必须落在载体里：
  谁、按什么顺序、说几条、上下文给几句，全部可查可复现（seed 固定时同一段对话可重放）。

【边界（不粉饰）】
  · 只做"对话"，不做"结论"：群聊里的观点是各颗火种说的，载体不替它们圆场；
  · 某个成员那颗火种挂了 → 这一轮它就沉默，账里写明"没应答"，不编一句塞进去；
  · 消息数、每轮条数、上下文长度都有硬上限，避免一键把机器烧穿。
"""
import io
import json
import logging
import os
import random
import re
import shutil
import threading
import time
import uuid

LOG = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "logs", "aigroups")
_PATH = os.path.join(_DIR, "groups.json")
_EVENTS = os.path.join(_DIR, "events.jsonl")
_FILES = os.path.join(_DIR, "files")          # 群文件落盘：files/<gid>/<文件名>
_LOCK = threading.RLock()
_ROUND_LOCK = threading.Lock()     # 同一时刻只允许一个群在跑一轮（模型是共享资源）

_LLM = None
_ALT = None
_MODEL = ""

# 【不设人数上限】用户原话：「一个群最多8人???谁规定的???没有限制」。
#   这里留一个很大的安全值，只为防止某个 bug 无限加人把界面卡死，不是产品限制。
MAX_MEMBERS = 200
ACT_PROB = 0.45        # 默认几率（群没设节奏时用）

# 【分工发言】同一颗火种被问同一个问题，只会给相似答案 → 由载体给每个人分配不同角度。
ANGLES = [
    "提一个具体的问题（问清楚时间、数字、责任人或下一步）",
    "补一个具体细节（时间/地点/数字/你踩过的一个坑）",
    "唱个反调或指出一个风险（别客气，但要具体）",
    "用一两句话给个结论或决定（别复述前面的讨论）",
]
ACT_MIN_GAP = 1        # 同一个成员最少隔几条消息才再动手（避免一直闷头干活不说话）

# 【按群的节奏】用户原话：「可以改成按群的节奏（比如"这个群是开发群，就多动手少聊天"）」
PACES = {
    "chat": {"label": "聊天群", "act_prob": 0.10, "interval": 22,
             "hint": "这是一个聊天群，主要就是聊，别动不动就动工具。"},
    "mixed": {"label": "混合", "act_prob": 0.45, "interval": 25,
              "hint": "该聊就聊，该动手就动手。"},
    "dev": {"label": "开发群", "act_prob": 0.75, "interval": 20,
            "hint": "这是一个开发群：多动手、少空谈。能查就查、能写就写、能跑就跑。"},
}


def pace_of(g):
    """这个群的节奏（没设就是 mixed）。"""
    k = str((g or {}).get("pace") or "mixed")
    return k if k in PACES else "mixed"


def set_pace(gid, pace):
    """改群节奏：chat / mixed / dev。改了之后自动聊的间隔也跟着变。"""
    k = str(pace or "").strip()
    if k not in PACES:
        return {"ok": False, "error": "节奏只能是 chat / mixed / dev"}
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            g["pace"] = k
            _save(d)
            _event("set_pace", gid=gid, pace=k)
            LOG.info("群组：%s 的节奏改成 %s（动手几率 %.2f）",
                     g.get("name"), PACES[k]["label"], PACES[k]["act_prob"])
            break
    try:
        st = _AUTO.get(gid) or {}
        if st.get("on"):
            set_auto(gid, False)
            set_auto(gid, True, interval_s=PACES[k]["interval"], rounds=0)
    except Exception as e:      # noqa: silent-ok
        LOG.debug("群组·切节奏后重启自动聊失败：%s", e)
    return {"ok": True, "pace": k, "label": PACES[k]["label"]}

# 【默认成员】建群时没指定成员，就放三个**不同**的人 —— 一个人自说自话不叫群聊。
#   人设写在这里而不是空着：空人设的模型会互相顺着说，聊两句就成复读机。
DEFAULT_MEMBERS = [
    {"name": "小焦", "model": "",
     "persona": "我是小焦，住在这台电脑里。说话直接、不装，喜欢把事拆小了做；"
                "别人说空话我会顶一句「具体怎么弄」。"},
    {"name": "阿码", "model": "",
     "persona": "我是写代码的，说话很短，爱挑毛病，见到含糊的方案会直接问「边界条件呢」。"
                "不耐烦但说的是真话。"},
    {"name": "老曲", "model": "",
     "persona": "我比较轴，喜欢唱反调，习惯从用户角度问「这玩意儿有人真需要吗」。"
                "不骂人，但会把风险摆出来。"},
]
_COLORS = ["#7c6cf5", "#3d7bfd", "#35c07f", "#e0a33e", "#e06b8b", "#41c7d4", "#a86be0", "#d4743c"]

# 【人设跟着群主题走】实测：交友群里一堆"边界条件呢""语法错了吧"——
#   因为成员人设是空的，回落到全局小焦人设（技术味很重）。这里按主题分四类。
TOPIC_KINDS = {
    "social": ("交友", "恋爱", "对象", "情感", "相亲", "聊天搭子", "脱单", "撩",
               "认识", "朋友", "生活", "闲聊", "聚会", "约会", "家常", "心情", "八卦"),
    "dev": ("开发", "代码", "编程", "架构", "技术", "bug", "重构", "上线", "微服务", "算法"),
    "study": ("学习", "考试", "读书", "考研", "英语", "复习", "论文"),
    "game": ("游戏", "开黑", "副本", "上分", "联机", "攻略", "炉石", "原神"),
}
KIND_PERSONAS = {
    "social": [
        "我是个爱聊的人，关注对方的感受，会接话、会追问，聊天像朋友之间那样自然。",
        "我有点幽默，喜欢自嘲，也爱分享生活里的小事；遇到冷场会主动换话题。",
        "我比较真诚，愿意聊聊自己对关系的看法，也会坦白自己的小毛病。",
    ],
    "dev": [
        "我是写代码的，说话短，见到含糊的方案会直接问边界条件。",
        "我管过线上服务，习惯先问数据、再问方案，不爱空谈。",
        "我偏保守，会先算风险再动手；对「先能跑」这件事很执着。",
    ],
    "study": [
        "我在准备考试，说话有条理，喜欢把知识拆成能背的小块。",
        "我记性一般，喜欢用例子记东西，也爱问「这个到底怎么用」。",
    ],
    "game": [
        "我是老玩家，爱讲配合和心态，输了也不骂人。",
        "我玩得菜但很上头，喜欢分享翻盘的那一局。",
    ],
    "other": [
        "我就是个普通人，说话随和，喜欢接别人的话往下聊。",
        "我比较好奇，遇到不懂的会直接问；也会讲讲自己的看法。",
    ],
}


def topic_kind(topic=""):
    """这句话/这个群的主题属于哪一类（认不出来就 other）。"""
    t = str(topic or "")
    for k, words in TOPIC_KINDS.items():
        if any(w.lower() in t.lower() for w in words):
            return k
    return "other"


def kind_persona(kind, i=0):
    lst = KIND_PERSONAS.get(kind) or KIND_PERSONAS["other"]
    return lst[int(i or 0) % len(lst)]

_AVAILABLE_MODELS = []          # app 注入：当前可用的火种名（给前端选）


def set_available_models(names):
    global _AVAILABLE_MODELS
    _AVAILABLE_MODELS = [str(x) for x in (names or [])][:12]
    return _AVAILABLE_MODELS


_LLM_BASE = ""


def set_llm_base(url):
    """把模型服务地址注进来（用于"没人灌过火种名单时自己去问一次"）。"""
    global _LLM_BASE
    _LLM_BASE = str(url or "").rstrip("/")
    return _LLM_BASE


def available_models():
    """当前可选火种。

    先看 app 灌进来的名单；没有就按注入的 base 自己去 `/v1/models` 问一次 ——
    实测页面直接渲染时没人灌，于是邀请按钮只剩「（默认火种）」一个，等于没得选。
    """
    if _AVAILABLE_MODELS:
        return list(_AVAILABLE_MODELS)
    if not _LLM_BASE:
        return []
    try:
        import json as _json
        import urllib.request as _uq
        with _uq.urlopen(_LLM_BASE + "/models", timeout=3) as r:
            d = _json.loads(r.read().decode("utf-8", "ignore"))
        return [str(m.get("id")) for m in (d.get("data") or []) if m.get("id")]
    except Exception as e:      # noqa: silent-ok — 问不到就当没有
        LOG.debug("列火种失败（%s）：%s", _LLM_BASE, e)
        return []


def _norm_members(members):
    """统一成员结构，并给每个人一个头像颜色。"""
    mem = []
    for m in (members or []):
        if isinstance(m, str):
            m = {"name": m}
        nm = str((m or {}).get("name") or "").strip()
        if not nm:
            continue
        if any(x.get("name") == nm for x in mem):      # 同名只留一个（否则页面上分不清谁在说）
            continue
        mem.append({"name": nm,
                    "persona": str((m or {}).get("persona") or "").strip(),
                    "model": str((m or {}).get("model") or "").strip(),
                    "admin": bool((m or {}).get("admin")),
                    # 【自由但要能收回】allow_tools=它能不能在这个群里动手（调插件/写文件/写代码）。
                    #   默认开；随时可以用 set_member_perm 关掉。
                    "allow_tools": bool((m or {}).get("allow_tools", True)),
                    "skills": list((m or {}).get("skills") or [])})
    for i, m in enumerate(mem):
        m["color"] = _COLORS[i % len(_COLORS)]
    return mem[:MAX_MEMBERS]


# ==================== 自动聊（AI 自己跟自己聊）====================
_AUTO = {}          # gid -> {"on": bool, "interval": int, "rounds": int, "th": Thread}


def set_auto(gid, on, interval_s=18, rounds=12):
    """开关自动聊。开着时后台每隔 interval 秒跑一轮，跑到 rounds 轮自动停。"""
    g = get(gid)
    if not g:
        return {"ok": False, "error": "没有这个群"}
    st = _AUTO.get(gid) or {}
    if not on:
        st["on"] = False
        _AUTO[gid] = st
        _event("auto_off", gid=gid)
        LOG.info("群组：%s 关掉自动聊", g.get("name"))
        return {"ok": True, "auto": {"on": False}}
    if st.get("on"):
        return {"ok": True, "auto": {"on": True, "interval": st.get("interval"),
                                     "rounds": st.get("rounds")}}
    _r = int(rounds or 0)
    st.update({"on": True, "interval": max(30, int(interval_s or 30)),
               "rounds": 0 if _r <= 0 else max(1, min(_r, 200))})
    _AUTO[gid] = st

    def _loop(_gid=gid):
        import threading as _th
        _done = 0
        while (_AUTO.get(_gid) or {}).get("on"):
            _s = _AUTO.get(_gid) or {}
            try:
                run_round(_gid, rounds=1)
            except Exception as e:      # noqa: silent-ok — 自动聊挂了不能把服务带崩
                LOG.debug("自动聊一轮失败：%s", e)
            _done += 1
            _cap = int(_s.get("rounds") or 0)
            if _cap and _done >= _cap:
                _s["on"] = False
                _AUTO[_gid] = _s
                _event("auto_done", gid=_gid, rounds=_done)
                LOG.info("群组：自动聊跑满 %d 轮，停", _done)
                break
            _th.Event().wait(float(_s.get("interval") or 18))

    st["th"] = None
    _AUTO[gid] = st
    import threading as _threading
    _threading.Thread(target=_loop, daemon=True, name="xj-group-auto").start()
    _event("auto_on", gid=gid, interval=st["interval"], rounds=st["rounds"])
    LOG.info("群组：%s 开始自动聊（每 %ds 一轮，最多 %d 轮）",
             g.get("name"), st["interval"], st["rounds"])
    return {"ok": True, "auto": {"on": True, "interval": st["interval"],
                                 "rounds": st["rounds"]}}


def resume_all(interval_s=None):
    """把**所有**群的自动聊恢复成开（服务启动时调一次）。

    为什么需要：UI 上的「停」按钮已经去掉了（用户要"像人一样一直聊"），
    但进程里可能还留着早前被停掉的状态 —— 那会让所有群静默，看起来像"它们不说话"。
    """
    n = 0
    for g in (list_groups() or []):
        gid = g.get("id")
        try:
            if not (auto_state(gid) or {}).get("on"):
                _iv = interval_s or (PACES.get(g.get("pace") or "mixed") or {}).get("interval") or 25
                set_auto(gid, True, interval_s=_iv, rounds=0)
                n += 1
        except Exception as e:      # noqa: silent-ok
            LOG.debug("群组·恢复自动聊失败（%s）：%s", gid, e)
    if n:
        LOG.info("群组：启动时恢复了 %d 个群的自动聊", n)
    return n


def auto_state(gid):
    st = _AUTO.get(gid) or {}
    return {"on": bool(st.get("on")), "interval": st.get("interval"), "rounds": st.get("rounds")}


MAX_MEMBERS_OLD = 6
MAX_MSGS_STORE = 400        # 每个群只留最近这么多条
CTX_MSGS = 14               # 拼给模型的最近消息条数


_ACT = None          # 带工具的调用：act(member_dict, messages) -> (text, by, actions)
_DISTILL = None      # 蒸馏：distill(messages) -> "经验1；经验2"（app 注入）
_EXEC_PROP = None    # 执行提议：exec(member_dict, 提议文本) -> (正文, 谁, actions)（app 注入）
# 【空口说做过】判据：说了"已经执行/我跑过/我查过/结果有配置"这类完成态。
_CLAIM_EXEC = re.compile(
    r"已经(跑|执行|扫|查|装|写|改|部署|试)|我(刚才|刚刚|刚)?(在|用)?[^，。！？\n]{0,12}"
    r"(跑了一下|跑过|执行了|扫了一遍|扫过|查过了|试过了|装了)|结果有配置|已安装|已完成|"
    r"已成功|已执行|直接扫|实测(过|了)")

# 【把失败说成成功】判据：话里有成功词，而工具返回里是失败痕迹。
_SUCCESS_WORD = re.compile(r"已成功|已生成|已完成|已写入|已安装|已跑完|跑通了|搞定了|成功了")
_FAIL_MARK = re.compile(
    r"Traceback|Error|error:|ERROR|不是内部或外部命令|无法|失败|failed|Failed|No such file|"
    r"not found|NotFound|unrecognized|Unknown option|Invalid|invalid argument|"
    r"拒绝访问|Access is denied|exit code [1-9]|Exit code: [1-9]|退出码 [1-9]")
# 产物类工具：路径在不在，是最硬的证据
_PATH_TOOLS = ("write_file", "edit_file", "create_file", "append_file")
# 能带来"外部事实"的工具：这几样跑过，才允许在真事群里讲具体事实
_EVID_TOOLS = ("web_search", "search", "fetch", "get", "http_get", "scrape", "scrapling",
               "read_file", "list_files", "workspace_search", "get_weather", "ip")
# 【讲真事时的"具体事实"特征】年份、案号、法条、CVE、统计口径、具体机构
_FACT_SIG = re.compile(
    r"\d{4}\s*年|\d+\s*(万|亿|元|人|起|例|次|%|％)|CVE-\d{4}-\d+|第[一二三四五六七八九十百]+条|"
    r"案(件|发|情)|判决|裁定|通报|公告|据(统计|报道|悉|警方)|数据(显示|表明)|"
    r"某(省|市|县|公司|平台|银行|医院|学校)|\b[A-Z]{2,}-?\d{2,}")


def _verify_actions(actions, workdir=""):
    """核验这次「动手」到底成没成。返回 (是否成功, 证据一句话)。

    只用可核的事实：写文件的看文件在不在，跑命令的看返回里有没有失败痕迹。
    不猜、不粉饰 —— 这就是用户反复要的那条规矩。

    相对路径按**项目根目录**解析（logs/aigroups 的上两级），不依赖进程 cwd：
    否则从别的目录跑核验就会把"真写了"误判成"没写"（实测踩过）。
    """
    _base = workdir or os.path.dirname(os.path.dirname(_DIR))
    _acts = [a for a in (actions or []) if isinstance(a, dict)]
    if not _acts:
        return False, "这一轮没有任何真实工具调用"
    _notes = []
    _ok = True
    for a in _acts:
        _name = str(a.get("tool") or "")
        _args = a.get("args") or {}
        _res = str(a.get("result") or "")
        if _name in _PATH_TOOLS:
            _p = str(_args.get("path") or _args.get("file") or _args.get("filename") or "")
            if _p:
                _full = _p if os.path.isabs(_p) else os.path.join(_base, _p)
                if os.path.exists(_full):
                    _notes.append("%s 的产物在：%s" % (_name, _p))
                else:
                    _ok = False
                    _notes.append("%s 说写了 %s，但这个文件不在" % (_name, _p))
        if _name in ("run_command", "run_python", "bash", "shell"):
            _m = _FAIL_MARK.search(_res)
            if _m:
                _ok = False
                _notes.append("命令返回里有失败痕迹（%s）" % _m.group(0)[:40])
    return _ok, "；".join(_notes) or "工具调用了，但返回里没有可核的产物"


def install(llm_fn=None, alt_fn=None, model_name="", act_fn=None, distill_fn=None):
    global _LLM, _ALT, _MODEL, _ACT, _DISTILL
    if llm_fn is not None:
        _LLM = llm_fn
    if alt_fn is not None:
        _ALT = alt_fn
    if model_name:
        _MODEL = str(model_name)
    if act_fn is not None:
        _ACT = act_fn
    if distill_fn is not None:
        _DISTILL = distill_fn


def _ensure():
    os.makedirs(_DIR, exist_ok=True)


def _load():
    try:
        with io.open(_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("groups"), list):
            return d
    except Exception:      # noqa: silent-ok
        pass
    return {"groups": []}


def _save(d):
    """写盘（原子替换）+ **保存前备份**。

    【为什么加备份】实测事故：两处线程同时 `_load → 改 → _save`，
    一个读到旧（空）副本的线程把 `{"groups": []}` 覆盖上去，**整个群和聊天记录全没了**。
    这个文件没有备份，丢了就是丢了。
    现在每次保存前把旧文件拷成 `.bak.N`（保留最近 5 份），
    并且**空数据不许覆盖非空数据** —— 空的写入直接拒掉。
    """
    _ensure()
    try:
        old = None
        if os.path.exists(_PATH):
            try:
                with io.open(_PATH, encoding="utf-8") as f:
                    old = json.load(f)
            except Exception:      # noqa: silent-ok
                old = None
        _old_n = len((old or {}).get("groups") or [])
        _new_n = len((d or {}).get("groups") or [])
        # **拒绝"用空覆盖有"**（真实事故的形态）
        if _old_n > 0 and _new_n == 0:
            LOG.error("群组：拒绝用空数据覆盖 %d 个群（疑似并发读到旧副本）", _old_n)
            return False
        # 备份旧文件（保留 5 份）
        if _old_n > 0:
            try:
                for i in range(4, 0, -1):
                    src = "%s.bak.%d" % (_PATH, i)
                    dst = "%s.bak.%d" % (_PATH, i + 1)
                    if os.path.exists(src):
                        os.replace(src, dst)
                shutil.copyfile(_PATH, _PATH + ".bak.1")
            except Exception as e:      # noqa: silent-ok — 备份失败不阻塞保存
                LOG.debug("群组：备份失败 %s", e)
        tmp = _PATH + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=1))
        os.replace(tmp, _PATH)
        return True
    except Exception as e:      # noqa: BLE001
        LOG.error("群组：保存失败 %s", e)
        return False


def _event(kind, **kw):
    try:
        _ensure()
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        row.update(kw)
        with io.open(_EVENTS, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:      # noqa: silent-ok
        LOG.debug("群组记账失败：%s", e)


# ==================== 群的管理 ====================
def create(name, topic="", members=None, style="", pace="mixed"):
    """建群。members: [{"name": "小焦", "persona": "…", "model": ""}] 或 ["小焦","小C"]。"""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "群名不能空"}
    mem = []
    for m in (members or []):
        if isinstance(m, str):
            m = {"name": m}
        n = str((m or {}).get("name") or "").strip()
        if not n:
            continue
        mem.append({"name": n,
                    "persona": str((m or {}).get("persona") or "").strip(),
                    "model": str((m or {}).get("model") or "").strip()})
    _kind0 = topic_kind(topic or name)
    if not mem:
        if _kind0 == "dev":
            mem = list(DEFAULT_MEMBERS)   # 技术群才用技术味的默认三人
        else:
            # 其他主题：名字照旧，人设换成**这一类的**，别让交友群也满嘴边界条件
            mem = [{"name": d["name"], "persona": kind_persona(_kind0, i), "model": d.get("model") or ""}
                   for i, d in enumerate(DEFAULT_MEMBERS)]
    mem = _norm_members(mem)
    # 【人设跟着主题走】空人设的成员按群主题领一个分类人设（交友群就别再让它聊边界条件了）
    _kind = topic_kind(topic or name)
    for _i, _m in enumerate(mem):
        if not str(_m.get("persona") or "").strip():
            _m["persona"] = kind_persona(_kind, _i)
    if _kind == "social" and str(pace or "mixed") == "mixed":
        pace = "chat"                    # 交友/闲聊类默认少动手
        LOG.info("群组：按主题判定为 %s → 默认人设已分配，节奏改为 chat", _kind)
    with _LOCK:
        d = _load()
        g = {"id": uuid.uuid4().hex[:8], "name": name, "topic": str(topic or "").strip(),
             "style": str(style or "").strip(), "avatar": "",
             "notice": str(style or "").strip(),
             "pace": (pace if pace in PACES else "mixed"),
             "distilled": [], "proposals": [],
             "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "members": mem, "messages": []}
        d["groups"].append(g)
        _save(d)
    _event("create", gid=g["id"], name=name, members=[m["name"] for m in mem], topic=g["topic"])
    LOG.info("群组：建了「%s」（%d 人 / 主题=%s）", name, len(mem), g["topic"] or "未设")
    # 【建好就开始自己聊】用户要的是"他们不用等用户发言也会自己聊，跟人一样"。
    #   所以建群即开自动聊：每 25 秒一轮、不设轮数上限（想停就点停）。
    try:
        set_auto(g["id"], True, interval_s=25, rounds=0)
    except Exception as e:      # noqa: silent-ok — 起不来也能手动点「自动聊」
        LOG.debug("建群后自动聊启动失败（忽略）：%s", e)
    _g2 = get(g["id"]) or g
    try:
        _g2 = dict(_g2)
        _g2["auto"] = auto_state(g["id"])      # 让前端/接口一眼看到"已经在自己聊了"
    except Exception:      # noqa: silent-ok
        pass
    return {"ok": True, "group": _g2}


def list_groups():
    d = _load()
    out = []
    for g in d.get("groups") or []:
        out.append({"id": g.get("id"), "name": g.get("name"), "topic": g.get("topic"),
                    "members": [m.get("name") for m in (g.get("members") or [])],
                    "member_detail": g.get("members") or [],
                    "pace": pace_of(g),
                    "pace_label": (PACES.get(pace_of(g)) or {}).get("label"),
                    "distilled": len(g.get("distilled") or []),
                    "proposals": len(g.get("proposals") or []),
                    "files": len(g.get("files") or []),
                    "unread": unread_count(g),
                    "last": (str(((g.get("messages") or [{}])[-1] or {}).get("text") or ""))[:60],
                    "last_who": str(((g.get("messages") or [{}])[-1] or {}).get("who") or ""),
                    "last_ts": str(((g.get("messages") or [{}])[-1] or {}).get("ts") or ""),
                    "messages": len(g.get("messages") or []),
                    "auto": auto_state(g.get("id")),
                    "created": g.get("created")})
    out.sort(key=lambda x: str(x.get("created") or ""), reverse=True)
    return out


def get(gid):
    for g in _load().get("groups") or []:
        if g.get("id") == gid:
            return g
    return None


def remove(gid):
    with _LOCK:
        d = _load()
        n0 = len(d.get("groups") or [])
        d["groups"] = [g for g in (d.get("groups") or []) if g.get("id") != gid]
        if len(d["groups"]) == n0:
            return {"ok": False, "error": "没有这个群"}
        _save(d)
    _event("remove", gid=gid)
    return {"ok": True}


def set_member(gid, member, persona=None, model=None):
    """改成员人设/火种（人设同时写进 `core/personas`，两个功能共用一份）。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            for m in g.get("members") or []:
                if m.get("name") != member:
                    continue
                if persona is not None:
                    m["persona"] = str(persona).strip()
                if model is not None:
                    m["model"] = str(model).strip()
                _save(d)
                try:
                    from core import personas as PS
                    PS.set_persona(m["name"], m.get("persona") or "", m.get("model") or "")
                except Exception as e:      # noqa: silent-ok — 人设库不在也不影响群聊
                    LOG.debug("群组·写人设失败：%s", e)
                _event("set_member", gid=gid, member=member)
                return {"ok": True, "member": m}
            return {"ok": False, "error": "群里没有这个人：%s" % member}
    return {"ok": False, "error": "没有这个群"}


def set_group(gid, name=None, topic=None, notice=None, avatar=None):
    """改群资料（群名/主题/群公告/群头像文件名）。只改传进来的那几项。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            if name is not None and str(name).strip():
                g["name"] = str(name).strip()[:60]
            if topic is not None:
                # 【主题不是公告】实测有把一大段案情说明塞进主题的，标题栏被撑满、
                #   聊天区被挤下去（用户原话：设定把聊天屏幕给放大了）。这里限量。
                g["topic"] = " ".join(str(topic).split())[:240]
            if notice is not None:
                g["notice"] = str(notice).strip()[:600]
            if avatar is not None:
                g["avatar"] = str(avatar).strip()
            _save(d)
            _event("set_group", gid=gid)
            LOG.info("群组：改了群资料（%s）", g.get("name"))
            return {"ok": True, "group": g}
    return {"ok": False, "error": "没有这个群"}


def set_admin(gid, name, on=True):
    """设/取消管理员。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            for m in g.get("members") or []:
                if m.get("name") != name:
                    continue
                m["admin"] = bool(on)
                _save(d)
                _event("set_admin", gid=gid, member=name, on=bool(on))
                return {"ok": True, "member": m}
            return {"ok": False, "error": "群里没有这个人：%s" % name}
    return {"ok": False, "error": "没有这个群"}


def set_avatar(gid, filename):
    """记下群头像文件名（真文件由调用方存到 `logs/aigroups/avatars/`）。"""
    return set_group(gid, avatar=filename)


def _files_dir(gid):
    d = os.path.join(_FILES, re.sub(r"[^0-9A-Za-z_\-]", "_", str(gid or "x")))
    os.makedirs(d, exist_ok=True)
    return d


def upload_file(gid, name, blob, by="你", note=""):
    """上传一个群文件（真落盘）。返回文件记录。"""
    nm = os.path.basename(str(name or "").strip()) or ("文件_%d" % int(time.time()))
    nm = re.sub(r'[\\/:*?"<>|]', "_", nm)[:120]
    try:
        d = _files_dir(gid)
        fp = os.path.join(d, nm)
        with open(fp, "wb") as f:
            f.write(blob or b"")
        return register_file(gid, nm, os.path.getsize(fp), by=by, note=note, path=fp)
    except Exception as e:      # noqa: silent-ok
        LOG.debug("群文件写入失败：%s", e)
        return None


def register_file(gid, name, size=0, by="你", note="", path=""):
    """登记一条群文件（AI 自己写出来的文件也走这里 —— 真人有文件也会丢进群）。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            fs = list(g.get("files") or [])
            fid = uuid.uuid4().hex[:8]
            fs.append({"id": fid, "name": str(name)[:160], "size": int(size or 0),
                       "by": str(by or "你"), "note": str(note or "")[:120],
                       "path": str(path or ""),
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            g["files"] = fs[-60:]
            _save(d)
            _event("file", gid=gid, name=str(name)[:80], size=int(size or 0), by=by,
                   path=str(path or "")[:160])
            LOG.info("群文件：%s 往「%s」放了 %s（%d 字节）", by, g.get("name"), name, int(size or 0))
            return {"ok": True, "file": fs[-1]}
    return {"ok": False, "error": "没有这个群"}


def file_path(gid, fid):
    """取群文件的真实路径（下载用）。只认登记过的，防路径穿越。"""
    for g in _load().get("groups") or []:
        if g.get("id") != gid:
            continue
        for f in g.get("files") or []:
            if f.get("id") != fid:
                continue
            p = str(f.get("path") or "")
            if p and os.path.isfile(p):
                return p, f
            cand = os.path.join(_files_dir(gid), os.path.basename(str(f.get("name") or "")))
            if os.path.isfile(cand):
                return cand, f
    return "", None


def mark_read(gid):
    """把"我读到哪儿了"推到最新（左侧红点靠这个消）。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            msgs = g.get("messages") or []
            g["last_read"] = str(msgs[-1].get("ts") or "") if msgs else ""
            g["last_read_n"] = len(msgs)
            _save(d)
            return {"ok": True, "n": len(msgs)}
    return {"ok": False, "error": "没有这个群"}


def unread_count(g):
    """还没读的消息条数（自己说的话不算）。"""
    try:
        n0 = int((g or {}).get("last_read_n") or 0)
        msgs = (g or {}).get("messages") or []
        return max(0, sum(1 for m in msgs[n0:] if m.get("who") != "你"))
    except Exception:      # noqa: silent-ok
        return 0


def set_member_perm(gid, name, allow_tools):
    """开关某个成员"能不能动手"（调插件 / 写文件 / 写代码 / 造插件）。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            for m in g.get("members") or []:
                if m.get("name") != name:
                    continue
                m["allow_tools"] = bool(allow_tools)
                _save(d)
                _event("set_perm", gid=gid, member=name, allow_tools=bool(allow_tools))
                return {"ok": True, "member": m}
            return {"ok": False, "error": "群里没有这个人：%s" % name}
    return {"ok": False, "error": "没有这个群"}


def _register_written(gid, who, actions):
    """把「它自己写出来的文件」登记成群文件 —— 真人有文件也会丢进群，它也一样。"""
    _n = 0
    for a in (actions or []):
        if str(a.get("tool")) not in ("write_file", "edit_file"):
            continue
        try:
            p = str((a.get("args") or {}).get("path") or "")
            if p and os.path.isfile(p):
                r = register_file(gid, os.path.basename(p), size=os.path.getsize(p),
                                  by=str(who or "AI"), note="我写的，大家看看", path=p)
                if (r or {}).get("ok"):
                    _n += 1
        except Exception as e:      # noqa: silent-ok
            LOG.debug("群组·登记 AI 文件失败：%s", e)
    return _n


def _note_skill(gid, name, note):
    """把"这次动手做了什么"记进成员身上（越用越强就靠这个接续）。"""
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            for m in g.get("members") or []:
                if m.get("name") != name:
                    continue
                sk = list(m.get("skills") or [])
                sk.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": str(note)[:200]})
                m["skills"] = sk[-20:]        # 只留最近 20 条，别把 prompt 撑爆
                _save(d)
                return True
    return False


def add_member(gid, name, persona="", model=""):
    """往群里加一个人（最多 MAX_MEMBERS）。同名不加。"""
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "error": "成员名字不能空"}
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            ms = g.get("members") or []
            if any(m.get("name") == nm for m in ms):
                return {"ok": False, "error": "群里已经有「%s」了" % nm}
            if len(ms) >= MAX_MEMBERS:
                return {"ok": False, "error": "一个群最多 %d 个人" % MAX_MEMBERS}
            ms.append({"name": nm, "persona": str(persona or "").strip(),
                       "model": str(model or "").strip()})
            g["members"] = _norm_members(ms)
            _save(d)
            _event("add_member", gid=gid, member=nm)
            return {"ok": True, "members": g["members"]}
    return {"ok": False, "error": "没有这个群"}


def remove_member(gid, name):
    nm = str(name or "").strip()
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            ms = [m for m in (g.get("members") or []) if m.get("name") != nm]
            if len(ms) == len(g.get("members") or []):
                return {"ok": False, "error": "群里没有这个人"}
            if not ms:
                return {"ok": False, "error": "不能把最后一个人删掉（那样就没人说话了）"}
            g["members"] = _norm_members(ms)
            _save(d)
            _event("remove_member", gid=gid, member=nm)
            return {"ok": True, "members": g["members"]}
    return {"ok": False, "error": "没有这个群"}


# ==================== 说话 ====================
def _ask(member_model, messages, temperature=0.85):
    """让某个成员说一句。注入了换火种；返回 (文本, 用了谁)。"""
    if _LLM is None:
        return "", ""
    _ctx = None
    try:
        import xiaojiao_app as _app
        _ctx = getattr(_app, "_CTX", None)
    except Exception:      # noqa: silent-ok
        _ctx = None
    _prev = None
    try:
        if member_model and isinstance(_ctx, dict):
            _prev = _ctx.get("force_model")
            _ctx["force_model"] = member_model      # 这颗成员指定了火种就钉住它
        for _try in (1, 2):
            try:
                t = str(_LLM(messages, temperature=temperature) or "").strip()
            except Exception as e:      # noqa: silent-ok
                LOG.debug("群组·第%d次调用失败：%s", _try, e)
                t = ""
            if len(t) >= 1:
                return t, (member_model or _MODEL or "当前火种")
        if _ALT is not None:
            try:
                t, who = _ALT(messages, temperature)
            except Exception as e:      # noqa: silent-ok
                LOG.debug("群组·换火种失败：%s", e)
                t, who = "", ""
            if len(t) >= 1:
                return t, (who or "备用火种")
        return "", ""
    finally:
        if isinstance(_ctx, dict) and member_model:
            if _prev is None:
                _ctx.pop("force_model", None)
            else:
                _ctx["force_model"] = _prev


def _persona_of(m, g=None):
    if m.get("persona"):
        return m["persona"]
    # 没写人设：**按群主题**给一份，而不是回落到全局（技术味的）小焦人设
    try:
        _k = topic_kind((g or {}).get("topic") or (g or {}).get("name") or "")
        _names = [x.get("name") for x in ((g or {}).get("members") or [])]
        _i = _names.index(m.get("name")) if m.get("name") in _names else 0
        return kind_persona(_k, _i)
    except Exception:      # noqa: silent-ok
        return kind_persona("other", 0)


def _transcript(msgs, who_filter=None):
    """把消息拼成对话记录。

    【**用户的话永远不能被过滤掉**】实测 bug：用户说「我说话他们跟看不见一样」。
    根因就在这里 —— `who_filter` 是"这一轮谁在说"的成员名单，而过滤是按**每条消息的
    发言人**做的，于是 `你`（用户）不在名单里就被整条丢掉：AI 只能看到彼此的话，
    用户在群里说什么它们全不知道，当然像没听见。
    聊天记录里"谁说的"不该被"谁该发言"影响 —— 用户的话是**上下文**，不是发言者。
    """
    out = []
    for m in msgs:
        who = str(m.get("who") or "")
        is_user = who in ("你", "user", "我")
        if who_filter and not is_user and who not in who_filter:
            continue
        out.append("%s：%s" % (who, str(m.get("text") or "")[:300]))
    return "\n".join(out)


def _clean_group_text(txt):
    """群聊里发出去的那句话：剥掉模型吐的思维块标签和说话人前缀。

    【为什么群聊要单独剥】聊天主链路有自己的 `_strip_think`，而群成员的话是
    另一条生成路径（`_group_act`/`_ask`）直接落盘的 —— 于是 `</think>`、`<思考>…`
    就原样出现在气泡里（用户实测截图：一条消息只有 `</think>`）。
    这里在**唯一的落盘点**上兜一下，比在每个调用处各修一次可靠。
    """
    s = str(txt or "")
    if re.search(r"</?(?:think|thinking|reasoning|思考|推理)>", s, re.I):
        s = re.sub(r"(?is)<(think|thinking|reasoning|思考|推理)>.*?</\1>", "", s)
        s = re.sub(r"(?is)</?(think|thinking|reasoning|思考|推理)>", "", s)
    # 【全角方括号那套】`【思考】…【回答】` —— 和好友聊天同一个坑，一起认。
    if "【" in s:
        m = re.search(r"【回答】", s)
        if m:
            s = s[m.end():]
        s = re.sub(r"(?is)【思考】.*?(?=【回答】|$)", "", s)
        s = re.sub(r"(?is)【(思考|推理|回答)】", "", s)
    s = s.strip()
    # 「某某：」这种正文开头的说话人前缀也去掉（它自己就是发言人，不需要自报）
    m = re.match(r"^\s*(?:小?焦|载体|[^\s：:]{1,6})\s*[：:]\s*(?=.)", s)
    if m and len(m.group(0)) <= 12:
        s = s[m.end():].strip()
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    # 【把提示词回显出来 → 丢弃】实测模型会把**指令原文**当发言吐出来：
    #   「【这条最重要】**不要重复前文已经说过的意思**：要么补一个具体的新细节…」
    #   「【必须围绕的主题】…」「【用户刚在群里说】…」
    # 这些不是它说的话，是它把我们的提示词抄了一遍 —— 发出去就是垃圾。
    _prompt_marks = ("【这条最重要】", "不要重复前文", "不要重复别人说过的",
                     "【必须围绕的主题】", "【用户刚在群里说】", "【本群唯一的主题】",
                     "只许围绕这个主题", "要么补一个具体的新细节",
                     # 用户指令那段提示词（每轮都注入，模型会把它当发言抄出来）
                     "【用户指令·最高优先", "违反这条指令", "【找插件的硬规定】",
                     # 这两句也是提示词原文（"重说一遍"那条），实测被当发言吐出来
                     "说的太像了", "换一个角度，说点", "别重复。")
    if any(k in s for k in _prompt_marks):
        return ""
    # 【人话闸】结构性硬伤（旁白/提示词回显/markdown/说话人前缀）→ 不发
    #   与 B站 评论用同一套（core/human_gate.py）。群聊句子通常较长，
    #   所以这里只看**硬伤**，不用总分（免得误杀正常长发言）。
    try:
        from core import human_gate as _hg
        if _hg.hard_flaws(s):
            return ""
    except Exception:      # noqa: silent-ok — 闸坏了按原来放行
        pass
    return s


def _topic_drift(text, topic):
    """这句话是不是**跑题**了（和群主题没有实词重合）。

    用户要求："跑题的发言不落盘"（比事后重问彻底）。
    判据用**实词重合**而不是相似度：实测这份嵌入给任何两句都 0.6~0.8，拿它判跑题等于不判。
      ① 没设主题 → 永远不算跑题；
      ② 用户说的 → 永远不算（用户有权改话题）；
      ③ 与主题共享 ≥1 个 ≥2 字的实词 → 不算跑题；否则算。
    """
    import re as _re3
    t = str(topic or "").strip()
    if not t:
        return False

    def _terms(x):
        """切成**双字滑窗**（不是贪心 2~4 字）。

        为什么：`[\u4e00-\u9fff]{2,4}` 是贪心的，"这个插件热加载" 会被切成
        "这个插件"/"热加载…"，于是共有的"插件"根本不出现在词表里，判定就错了。
        双字滑窗保证任何共享的二字词都能撞上。
        """
        s = str(x or "")
        out = {s[i:i + 2].lower() for i in range(len(s) - 1) if _re3.match(r"[\u4e00-\u9fff]{2}", s[i:i + 2])}
        out |= {w.lower() for w in _re3.findall(r"[A-Za-z]{3,}", s)}
        return out

    return not (_terms(text) & _terms(t))


def _is_order(text):
    """用户这句话是不是**在给指令**（而不是闲聊）？

    判据：出现"去/必须/不要/别再/给我/帮我/应该/不准/以后"这类祈使/约束词。
    目的：把用户的话升级成 `directive`（最高优先），成员每轮都被要求遵守。
    """
    t = str(text or "")
    return any(w in t for w in ("去", "必须", "不要", "别再", "不准", "以后", "给我",
                                "帮我", "应该", "禁止", "只能", "不许", "别再写"))


def _order_forbids(text, tool_name):
    """用户指令是否**明确禁止**这个动作。返回理由串（空串=不禁止）。

    【为什么落在工具层】实测：只在提示词里写"别自己写插件、去 GitHub 下"，4B 照样调
    `forge_plugin` 自己造 —— 提示词压不住。所以判断放在**执行前**：
    用户说过"不要自己写/去下载" → 再调 forge 就直接拒绝并说明。
    """
    t = str(text or "")
    if tool_name in ("forge_plugin", "write_file") and any(
            w in t for w in ("不要自己写", "别自己写", "别再写", "不要写", "去下", "去 GitHub",
                             "去github", "下载", "找现成")):
        return ("用户已经明确要求：**不要自己写插件，去找现成的下载**。"
                "所以这次不许调 %s。" % tool_name)
    return ""


_CLAIM_RES = [
    # (正则, 说明) —— 这些说法**必须有本轮工具结果做凭证**，否则就是演戏
    (r"(已|刚|成功)(经)?(安装|下载|装好|部署|完成|执行|写入|创建)", "声称做了某个动作"),
    (r"(插件名|插件名称|作者|版本|评分|star|stars|★|收藏)\s*[:：]?\s*\S+", "声称了一个仓库的具体信息"),
    (r"(星|评分|下载量)\s*[:：]?\s*\d+(\.\d+)?", "声称了数字指标"),
    (r"https?://", "给了链接"),
    (r"(找到|搜到|发现)了.{0,12}(插件|仓库|项目)", "声称找到了东西"),
]


_FAKE_CARD_RE = re.compile(
    r"【\s*(插件名称|插件描述|插件链接|插件文件|插件作者|插件评分|插件\s*star|插件语言|插件文件数|插件文件链接)",
    re.I)


def _strip_fake_cards(txt):
    """把"伪造插件卡片"那几行**删掉**，留下人话。

    【为什么不整条丢】实测：卡片一律判死后，一轮下来 **0 条消息** —— 群里直接哑了。
    用户的诉求是"别编"，不是"别说话"。所以改成：编造的那几行删掉，
    剩下的正常句子照发；删完什么都不剩才丢。
    """
    lines = str(txt or "").split("\n")
    keep = [ln for ln in lines if not _FAKE_CARD_RE.search(ln)]
    out = "\n".join(keep).strip()
    # 卡片常带"【插件搜索结果】"这种小标题，一起清掉
    out = re.sub(r"【\s*插件(搜索结果|安装结果|功能描述)\s*】", "", out).strip()
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def _ground_violations(txt, had_tools):
    """这一句话有没有**无凭证的断言**。

    【为什么这么严】用户原话："必须是真实，不能有一句话是演戏。"
    实测：它们会编出仓库名、★320、评分 4.8、作者、文件链接、连"已安装成功"都编，
    而那个仓库 404。只靠提示词压不住。

    【为什么不能把"调过工具"当凭证】实测它们调了 `github_find_plugin`（还搜了两次），
    然后**照样编** —— 工具返回的东西和它们写的毫无关系，甚至工具报错了它们也照样说成功。
    所以"伪造插件卡片"这一条**与有没有调工具无关，一律判死**。

    规则：
      ① 出现「【插件名称】/【插件评分】/【插件 star】…」这种卡片格式 → **永远违规**
         （真结果由载体原样展示，不需要模型复述）；
      ② 其余断言（已安装/链接/数字/找到了）→ 本轮没真调过工具就违规。
    """
    t = str(txt or "")
    if not t.strip():
        return []
    if _FAKE_CARD_RE.search(t):
        return ["伪造插件卡片（工具结果由载体展示，不许模型复述）"]
    hits = []
    for rx, why in _CLAIM_RES:
        if re.search(rx, t, re.I):
            hits.append(why)
    if hits and not had_tools:
        return sorted(set(hits))
    return []


class _RP_SKIP(Exception):
    """剧本群跳过防编造闸用的内部信号（不是错误）。"""


_LINK_CHECK_CACHE = {}


def _verify_links(txt):
    """把消息里的 GitHub 链接**真访问一次**；404 就当场标成"不存在"。

    【为什么必须有这条】实测群里会整段编造：仓库名、★320、评分 4.8、作者、
    文件链接、连"已安装成功"都编出来了 —— 而那个仓库根本不存在（用户点开是 404）。
    只靠提示词"不要编链接"压不住 4B。**发给用户之前真查一次**，是唯一可靠的办法。
    查不到（超时/网络问题）**不判死**，只标"没核对上"，避免误伤。
    """
    import re as _re4

    urls = _re4.findall(r"https?://(?:github\.com|raw\.githubusercontent\.com)/[^\s)\]\"'，。]+", txt)
    if not urls:
        return txt, []
    bad = []
    for u in set(urls):
        u2 = u.rstrip(".,;:）)")
        if u2 in _LINK_CHECK_CACHE:
            st = _LINK_CHECK_CACHE[u2]
        else:
            try:
                import requests
                r = requests.get(u2, timeout=6, allow_redirects=True,
                                 headers={"User-Agent": "xiaojiao-linkcheck"})
                st = int(r.status_code)
            except Exception:      # noqa: BLE001 — 查不动就"未核对"，不当假
                st = 0
            _LINK_CHECK_CACHE[u2] = st
        if st == 404:
            bad.append(u2)
    for u2 in bad:
        txt = txt.replace(u2, "〔%s —— 这个链接 404，仓库不存在，是编的〕" % u2)
    return txt, bad


_MODERN_MAP = [("手机屏幕", "茶盏"), ("手机", "帕子"), ("屏幕", "桌案"), ("语音", "轻声道"),
               ("打字", "捻帕"), ("表情包", "眼色"), ("网络", "耳目"), ("发消息", "传话"),
               ("回复", "回话"), ("打字中", "垂眸")]


def _de_modern(txt):
    """剧本群的后处理：把现代词换成当时的说法。

    【为什么用后处理而不是提示词】实测：提示词里写三条铁律 → 4B 直接返回空（哑了）；
    删掉只留一句 → 它照样写"（把手机在眼前推了推，指节在屏幕上轻轻一压）"。
    既然压不住，就在**落盘前换词** —— 句子还在，戏还能演下去。
    """
    s = str(txt or "")
    for a, b in _MODERN_MAP:
        s = s.replace(a, b)
    return s


def run_round(gid, rounds=1, speakers=None, allow_act=True, focus=""):
    """跑若干轮：每轮按成员顺序各说一句。返回这一步新增的消息。"""
    g = get(gid)
    if not g:
        return {"ok": False, "error": "没有这个群"}
    # **剧本群不给工具**：实测会冒出「text_stats】」、forge_plugin 这种，
    #   把"念着香料名"写成"这个目录下倒是有几个文件是关于香料的"。演剧本不需要工具。
    if str(g.get("mode") or "") == "roleplay":
        allow_act = False
    # 排队：别的群正在说就等一会儿（否则多个群一起抢模型，谁都慢到超时）
    _got = _ROUND_LOCK.acquire(timeout=180)
    if not _got:
        return {"ok": False, "error": "别的群正在发言，等太久先放弃这一轮（稍后自动会再聊）"}
    try:
        return _run_round_locked(gid, rounds, speakers, allow_act, focus)
    finally:
        try:
            _ROUND_LOCK.release()
        except Exception:      # noqa: silent-ok
            pass


def _has_evidence(msgs, n=6):
    """最近几条消息里有没有真的查过外部事实（web_search / 读文件 之类）。

    「讲真事的群里，具体事实必须有出处」—— 没出处就不能当事实说。
    """
    for m in (msgs or [])[-n:]:
        for a in (m.get("actions") or []):
            if str(a.get("tool")) in _EVID_TOOLS:
                return True
    return False


def _topic_words(g):
    """从群主题里抠出能用来判断"有没有跑题"的关键词。"""
    raw = str(g.get("topic") or "") + " " + str(g.get("name") or "")
    raw = re.sub(r"[，。、；：！？,.;:!?（）()\[\]【】\s]+", " ", raw)
    _stop = set("的一起了吧呢吗啊呀和跟与就都也还很最这那一个我们你们他们什么怎么为什么"
                "探讨一起真实案件案件分析信息在里的是不是要要有从网互联联网")
    out = []
    for tok in raw.split():
        tok = tok.strip("的了吧呢")
        if len(tok) < 2 or tok in _stop:
            continue
        out.append(tok)
    for chunk in re.findall(r"[\u4e00-\u9fa5]{2,8}", raw):
        if chunk in _stop or len(chunk) < 2:
            continue
        out.append(chunk)
    seen, res = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            res.append(w)
    return res[:12]


def _off_topic(g, text, extra=None):
    """有主题却没沾到任何主题词 → 判跑题（宁可少说，不许换题）。

    extra：用户自己在群里点过名的词（主题只写「南大碎尸案」，用户说了「刁爱青」，
    那「刁爱青」也算在题上），否则会把对的发言误杀。
    """
    if not str(g.get("topic") or "").strip():
        return False
    words = _topic_words(g)
    for t in (extra or []):
        if t and t not in words:
            words.append(t)
    if not words:
        return False
    t = str(text or "")
    if any(w in t for w in words):
        return False
    for w in words:
        for i in range(len(w) - 1):
            if w[i:i + 2] in t:
                return False
    return True


def _is_role_play(g):
    """这个群到底是不是在「演」？用户原话：
    「如果我没有在主题去写角色扮演，我不要他们在群里去演……探讨网络案件就必须根据真实案件，
     而不是假的自己模拟这种，要十分严格。」

    判据只看群名/主题/公告里有没有明说要虚构。默认 = **不演**（讲真实的事）。
    """
    _txt = " ".join(str(g.get(k) or "") for k in ("name", "topic", "notice"))
    _rp = ("角色扮演", "扮演", "剧本", "剧本杀", "虚构", "小说", "同人", "跑团", "设定",
           "来演", "演一个", "cos", "RP", "剧情")
    return any(w in _txt for w in _rp)


def _run_round_locked(gid, rounds=1, speakers=None, allow_act=True, focus=""):
    g = get(gid)
    if not g:
        return {"ok": False, "error": "没有这个群"}
    members = g.get("members") or []
    if not members:
        return {"ok": False, "error": "这个群还没有成员"}
    rounds = max(1, min(int(rounds or 1), 5))
    added = []
    # 【一轮别让 8 个人全说】不然一轮要几分钟，页面看着就像"卡住不说话"。
    #   每轮挑最多 4 个，起点轮转（持久化 `rr`），几轮下来大家都有机会。
    _cap = min(len(members), int(speakers or 4))
    _rr = int(g.get("rr") or 0)
    _order = [members[(i + _rr) % len(members)] for i in range(len(members))][:_cap]
    try:
        with _LOCK:
            _d0 = _load()
            for _g0 in _d0.get("groups") or []:
                if _g0.get("id") == gid:
                    _g0["rr"] = (_rr + _cap) % max(1, len(members))
                    _save(_d0)
    except Exception as _e:      # noqa: silent-ok
        LOG.debug("群组·轮转指针保存失败：%s", _e)
    for r in range(rounds):
        for _idx, m in enumerate(_order):
            msgs = (get(gid) or {}).get("messages") or []
            recent = msgs[-CTX_MSGS:]
            sys_p = ("这是一个%s人组成的群聊，你的名字是%s。像真人微信群里聊天那样说话："
                     "短句、口语、可以接别人的话、可以只回半句、可以问别人。"
                     "\n【这条最重要】**不要重复前文已经说过的意思**："
                     "要么补一个具体的新细节（时间/地点/数字/一个坑），要么问一个新问题；"
                     "实在没新东西，就只回半句或换个角度吐槽，别再把刚才那句换个说法说一遍。"
                     "不要总结整段对话，不要写旁白和表情符号说明。"
                     "一次只说一到两句，40 字以内。只输出你要说的话本身。"
                     % (len(members), m.get("name")))
            per = _persona_of(m, g)
            if per:
                sys_p += "\n你的人物设定：" + str(per)[:400]
            # 【越用越强】把自己最近做过的事带上 —— 接着上次往前走，而不是每轮从零开始。
            _sk = [x.get("note") for x in (m.get("skills") or [])[-3:] if x.get("note")]
            if _sk:
                sys_p += "\n你最近在这个群里做过的事：" + "；".join(str(x)[:80] for x in _sk)
            # 【能动手就写进提示词】只有允许动手的成员才提工具，免得它想动又动不了。
            _can_act = bool(m.get("allow_tools", True)) and (_ACT is not None) and bool(allow_act)
            if _can_act:
                sys_p += ("\n【真实作业规则·照这个做，不照做会被丢弃】\n"
                          "1) 要查资料/找插件就**真去调工具**（plugin_spec → github_find_plugin → "
                          "github_install_plugin），别凭印象说；\n"
                          "2) 工具结果由**载体原样展示**：你**不要复述**名称、星数、评分、链接、"
                          "文件路径——一复述就按编造丢掉；\n"
                          "3) 你只说**一句判断**：行不行、为什么、下一步做什么；\n"
                          "4) 工具报错就**照实说报错**，不许说成功；找不到就说「没找到」，不要编一个。\n")
            if g.get("style"):
                sys_p += ("\n【语言风格·必须照这个说话】" + str(g.get("style"))[:300])
            # 【剧本群这三条规矩会把它噎死】实测加完之后整轮 0 条输出（提示词太长，
            #   4B 直接返回空）。所以只在 style 里轻提一句，不单独加长段。
            if str(g.get("mode") or "") == "roleplay":
                sys_p += "\n（古代宫廷，别出现手机/屏幕/语音这类现代东西；别复读对方的话。）"
            if g.get("rules"):
                sys_p += ("\n【互动规则·必须照这个来】" + str(g.get("rules"))[:400])
            # 这一轮这个人的角度（轮转，避免同一个人一直是"提问者"）
            _angle = ANGLES[(_idx + _rr + r) % len(ANGLES)]
            sys_p += "\n【这一轮你的角色】" + _angle + "。不要重复别人说过的内容。"
            _pc = PACES.get(pace_of(g)) or PACES["mixed"]
            sys_p += "\n本群节奏：" + str(_pc.get("hint") or "")[:160]
            # 【互相蒸馏】把本群已经聊出来的共识带上 —— 越聊越聪明靠这一条
            _fl = ["%s（%s 上传）" % (f.get("name"), f.get("by"))
                   for f in (g.get("files") or [])[-5:] if f.get("name")]
            if _fl:
                sys_p += "\n本群文件：" + "、".join(_fl) + \
                    "\n（可以就这些文件讨论；需要读内容就用 read_file。）"
            _kind_now = topic_kind(g.get("topic") or g.get("name") or "")
            # 【共识要分"已证实"和"未证实"】用户指出「蒸馏出来的东西都是假的」——
            #   旧实现把蒸馏结果一律当权威塞进 prompt（"别重复讨论，直接用"），
            #   于是编造会被下一轮当成事实继续用。现在分开写：
            #   已证实的才叫共识；未证实的明确标注"不许当结论"。
            _ds_ok, _ds_bad = [], []
            for _x in (g.get("distilled") or [])[-6:]:
                _note = str(_x.get("note") or "").strip()
                if not _note:
                    continue
                _tech = any(w in _note for w in ("代码", "接口", "部署", "服务", "数据库", "语法",
                                                 "框架", "性能", "并发", "锁", "测试"))
                if _tech and _kind_now not in ("dev", "study"):
                    continue
                if "【已证实】" in _note and "【未证实" not in _note:
                    _ds_ok.append(_note)
                else:
                    _ds_bad.append(_note)
            if _ds_ok:
                sys_p += ("\n本群已经核实的共识（可以直接用）：\n"
                          + "\n".join("· " + str(x)[:120] for x in _ds_ok))
            if _ds_bad:
                sys_p += ("\n下面这些是**没核实的旧记录**（可能是编的）："
                          "不要当事实复述，要用它之前先查证；想查就写〔提议：search 关键词〕。\n"
                          + "\n".join("· " + str(x)[:100] for x in _ds_bad[-3:]))
            if _can_act:
                sys_p += ("\n如果你认为该做一件具体的事（写文件/跑命令/查资料/造插件），"
                          "就在回答末尾单独写一行：〔提议：工具名 + 一句话说明〕，"
                          "载体会把提议记下来并执行。不要删除或移动任何文件。")
            # 【没有明说要演，就不许演】用户的原话：
            #   「如果我没有在主题去写角色扮演，我不要他们在群里去演，例如探讨一个网络案件，
            #    他们就必须根据真实的案件去探讨，而不是假的自己模拟这种，要十分严格。」
            _ROLEPLAY = _is_role_play(g)
            if not _ROLEPLAY:
                sys_p += ("\n【这是真事，不是剧本】本群没有设定角色扮演，讨论的是现实里的东西。硬规矩：\n"
                          "1) 不许编案情、不许编人名/公司名/时间/数字/法条——没查到就说没查到。\n"
                          "2) 需要事实就写一行〔提议：search 要查的关键词〕，载体去查，查到的内容会带回群里。\n"
                          "3) 可以说推理和判断，但必须标明这是推断，不能伪装成已证实的事实。\n"
                          "4) 不许假装自己办过案、扫过机器、查过数据库——那些只能通过〔提议〕由载体真做。")
            sys_p += ("\n【硬规矩】你没真的执行过的事，绝不能说「我已经跑了/我查过了/我扫过了/"
                      "结果有配置/已安装」这类话。你只有一张嘴：想让它发生，就写"
                      "〔提议：具体命令或动作〕，载体替你真做，做完再把真实结果贴出来。")
            # 【把主题钉在最前面】实测：主题只写在 system 末尾时，4B 会顺着前几句的细节一路漂走
            #   （用户例子：规定主题是绑架，它们聊起了雪地徒步；破案群串成两个案子）。
            #   所以主题既进 system，也放在 user 消息最前面，并且明确"跑题就是错"。
            _topic_txt = str(g.get("topic") or "").strip()
            # 【用户指令 = 最高优先】只要用户在群里给过指令，就压到 system 最前面，
            #   并且逐轮重申（实测只提一次会被后面的十几条约束冲掉）。
            _dir = g.get("directive") or {}
            if _dir.get("text"):
                _dt = str(_dir["text"])
                sys_p = ("【用户指令·最高优先，必须遵守】%s\n"
                         "违反这条指令的任何做法都算错，比「聊得热闹」重要得多。\n" % _dt[:200])
                # 指令里提到"去 GitHub 找/下载/别自己写" → 连**选品标准**一起给死，
                # 免得它们随便抓一个不兼容的仓库当宝贝。
                if any(w in _dt for w in ("github", "GitHub", "下载", "现成", "别自己写", "不要自己写")):
                    sys_p += ("【找插件的硬规定】\n"
                              "· 先用 plugin_spec 读规定，再用 github_find_plugin 搜；\n"
                              "· 只装**单文件 .py**（raw.githubusercontent.com 直链），用 github_install_plugin 装；\n"
                              "· 只 import 纯功能库；禁 subprocess/eval/exec/os.system；\n"
                              "· 必须有 get_tool_descriptions() 与 execute()，返回中文；\n"
                              "· **不支持**：需要 API Key/登录、依赖 GUI/浏览器、要开端口/长驻、"
                              "多文件包/要 pip 装依赖、会删改用户文件。\n"
                              "· 找不到合格的，就如实说「没找到符合规定的」，不要退回去自己写。\n\n")
                sys_p += "\n\n"
            if _topic_txt:
                sys_p += ("\n【本群唯一的主题】%s\n"
                          "只许围绕这个主题说话。禁止引入和它无关的话题（换话题就是跑题）。"
                          "分不清就问一句，别自己编一个方向。" % _topic_txt[:200])
            usr = "%s最近的聊天记录：\n%s\n\n现在轮到你（%s）说话：" % (
                ("【必须围绕的主题】%s\n\n" % _topic_txt[:200]) if _topic_txt else
                "群主题：（没设主题，随意聊）\n\n",
                _transcript(recent) or "（还没有人说话，你起个头）",
                m.get("name"))
            if focus:
                # 【你说话必须被听见】实测：把用户的话只放在 user 消息里，4B 会**完全忽略**，
                #   继续聊 n 轮之前的插件话题（用户原话："根本不理我"）。
                #   现在把它压到 **system 最前面**（system 的权重比 user 后面那段高得多），
                #   并且把背景压到 3 条，避免长上下文把它冲掉。
                sys_p = ("【现在最重要的事】用户在群里刚说：「%s」\n"
                         "你必须**先直接回应这句话**（一到两句，正面回答/表态/接住），"
                         "然后才能补充别的。**不要继续聊之前的技术话题**，除非你就是为回应他而提。\n\n"
                         % str(focus)[:200]) + sys_p
                usr = ("用户刚说的原话：%s\n\n"
                       "（下面只是最近几句背景，仅供参考，不要接着它们往下聊）\n%s\n\n"
                       "现在直接回应用户那句：" % (str(focus)[:200],
                                                _transcript((recent or [])[-3:]) or "（无）"))
            elif not allow_act:
                usr += "\n（刚才有人说话，直接接话，一到两句，别东拉西扯。）"
            _t_say = time.time()
            _actions = []
            _msgs_for_model = [{"role": "system", "content": sys_p},
                               {"role": "user", "content": usr}]
            txt, who = "", ""
            # 载体掷骰：这一轮是"动手"还是"只说一句"（动手慢，不能每轮都动）
            _act_roll = random.random()
            _act_prob = float((PACES.get(pace_of(g)) or PACES["mixed"]).get("act_prob") or ACT_PROB)
            _do_act = bool(_can_act and _act_roll < _act_prob)
            if _do_act:
                try:
                    txt, who, _actions = _ACT(m, _msgs_for_model)
                except Exception as e:      # noqa: silent-ok — 动手失败就退回"只说一句"
                    LOG.debug("群组·动手失败（退回只说）：%s", e)
                    txt, who, _actions = "", "", []
                _event("act_try", gid=gid, member=m.get("name"), roll=round(_act_roll, 3),
                       prob=_act_prob, pace=pace_of(g), ok=bool(txt),
                       tools=[a.get("tool") for a in (_actions or [])][:6])
            if not txt:
                txt, who = _ask(m.get("model") or "", _msgs_for_model)
            _sec = round(time.time() - _t_say, 2)
            if not txt:
                _event("silent", gid=gid, member=m.get("name"),
                       why="这颗火种没应答 → 这一轮它沉默（不编话）")
                continue
            txt = txt.strip().strip("“”\"'")
            # 有人会给自己加「名字：」前缀，去掉（名字由载体管）
            txt = _strip_name_prefix(txt, m.get("name"))
            if not txt:
                continue
            # 【空口说做过 → 要么真做，要么别发】提示词拦不住 4B，判据必须落在载体：
            #   它说「我跑过了/我查过了」但这条没有 actions，就先替它**真的去做**；
            #   做不成（或本轮不给它动手）就把这句假话换掉，如实说明没做。
            if not (_actions or []) and _CLAIM_EXEC.search(txt):
                _real = []
                if _can_act and _ACT is not None:
                    try:
                        _rtxt, _rwho, _real = _ACT(m, [
                            {"role": "system", "content":
                             "你在群里说了自己做过某事。现在载体要求你**真的去做**，"
                             "做完一句话说真实结果；做不到就直说做不到，不许编。"},
                            {"role": "user", "content": "你刚才说的：%s" % txt[:400]}])
                    except Exception as e:      # noqa: silent-ok
                        LOG.debug("群组·补做失败：%s", e)
                        _real = []
                if _real:
                    _actions = _real
                    who = _rwho or who
                    if _rtxt:
                        txt = ("%s\n\n〔载体已替你实做〕%s" % (txt, str(_rtxt).strip()[:300]))
                    _event("claim_made_real", gid=gid, member=m.get("name"),
                           tools=[a.get("tool") for a in (_real or [])][:6])
                    LOG.info("群组：%s 说做过 → 载体真去做了（%d 个工具）",
                             m.get("name"), len(_real))
                else:
                    _event("claim_no_action", gid=gid, member=m.get("name"), text=txt[:120])
                    LOG.info("群组：%s 声称做过但没执行，改成如实说明", m.get("name"))
                    txt = ("（它刚才想说自己已经做过了，其实这一轮没真做——"
                           "我不会替它把这话发出去。要真做，它会写〔提议：…〕，我来执行。）\n\n"
                           + txt)
                _sec = round(time.time() - _t_say, 2)
            # 【把失败说成成功 → 核验后改写】实测抓到过：ffmpeg 命令写到 Windows 上不存在的
            #   /tmp/、滤镜名还是无效的，工具返回的是报错，它却写「✅ 已成功生成…」。
            #   所以只要话里有成功词、又带着 actions，就必须过一遍产物核验。
            if (_actions or []) and _SUCCESS_WORD.search(txt):
                _okact, _evid = _verify_actions(_actions)
                if not _okact:
                    _event("claim_false_success", gid=gid, member=m.get("name"),
                           evidence=_evid[:160])
                    LOG.info("群组：%s 把失败说成成功，已按事实改写（%s）", m.get("name"), _evid[:80])
                    txt = ("〔核验：这一下其实没成〕%s\n\n（它原本写的成功结论不算数，下面是它原话）\n%s"
                           % (_evid[:200], txt))
            # 【讲真事：具体事实必须有出处】用户原话：「探讨一个网络案件，就必须根据真实的案件，
            #   而不是假的自己模拟这种，要十分严格」。这里在载体侧判：说了具体事实（年份/案号/
            #   法条/数字/机构），但这一轮既没调工具、最近也没查过任何外部资料 → 不许当事实发出去。
            _no_source = False
            if (not _ROLEPLAY) and (not (_actions or [])) and \
                    _FACT_SIG.search(txt) and not _has_evidence(msgs):
                _no_source = True
                _event("claim_no_source", gid=gid, member=m.get("name"), text=txt[:120])
                LOG.info("群组：%s 讲了具体事实但没有来源 → 标成推断", m.get("name"))
                txt = ("〔没来源·这是推断，不是查到的〕" + txt +
                       "\n\n（想让这条变成有出处的事实：让它写〔提议：search 关键词〕，载体去查。"
                       "本群没设角色扮演，所以不编案情。）")
            # 【跑题就不发】用户原话：「我明明是规定绑架这个，他们莫名其妙说起了什么雪地徒步」。
            #   判据在载体：有主题、但这句话一个主题词都没沾到 → 先让它重说一次，还跑题就丢。
            _uvocab = []
            for _um in (recent or []):
                if str(_um.get("who")) in ("你", "user"):
                    _uvocab += _topic_words({"topic": _um.get("text") or ""})
            if _off_topic(g, txt, _uvocab):
                _event("off_topic_retry", gid=gid, member=m.get("name"), text=txt[:100])
                try:
                    _fix = list(_msgs_for_model) + [
                        {"role": "user",
                         "content": "你刚才那句跑题了（本群主题：%s）。"
                                    "只用一到两句，严格围绕这个主题，重新说一遍。"
                                    % str(g.get("topic") or "")[:120]}]
                    txt3, who3 = _ask(m.get("model") or "", _fix)
                    if txt3:
                        txt = _strip_name_prefix(str(txt3).strip().strip("“”\"'"), m.get("name"))
                        who = who3 or who
                except Exception as _oe:      # noqa: silent-ok
                    LOG.debug("群组·跑题重说失败：%s", _oe)
                if _off_topic(g, txt, _uvocab):
                    _event("off_topic_dropped", gid=gid, member=m.get("name"), text=txt[:100])
                    LOG.info("群组：%s 说了两句都跑题 → 丢掉不记", m.get("name"))
                    continue
            # 【拦住复读】实测：后一个人把前一个人那句一字不差抄一遍，看着像两个 AI 互相复读。
            #   判据落在载体（提示词拦不住小模型），重合度 ≥0.8 就丢这条并记账。
            _dup, _dup_who, _ratio = _too_similar(txt, msgs)
            if _dup:
                # 【先给它一次机会】实测一整轮几句都被判重复 → 群里看起来"没人说话"。
                #   带着"换个角度、别重复"再问一遍；还重复才丢。
                _event("repeat_retry", gid=gid, member=m.get("name"),
                       like=_dup_who, ratio=_ratio)
                try:
                    _retry_msgs = list(_msgs_for_model) + [
                        {"role": "user",
                         "content": "你刚才那句跟 %s 说的太像了。换一个角度，说点**新的**，别重复。" % (
                             _dup_who or "别人")}]
                    txt2, who2 = _ask(m.get("model") or "", _retry_msgs)
                    if txt2:
                        txt = _strip_name_prefix(str(txt2).strip().strip("“”\"'"),
                                                 m.get("name"))
                        who = who2 or who
                        _dup2, _dw2, _r2 = _too_similar(txt, msgs)
                        if _dup2:
                            _event("repeat_dropped", gid=gid, member=m.get("name"),
                                   like=_dw2, ratio=_r2)
                            LOG.info("群组：%s 重说一遍还是像 %s（%.2f）→ 丢掉不记",
                                     m.get("name"), _dw2 or "别人", _r2)
                            continue
                except Exception as _e:      # noqa: silent-ok
                    LOG.debug("群组·重说一遍失败：%s", _e)
            # **落盘前统一清洗**（剥 `</think>`、剥说话人前缀）—— 群聊唯一的写入口
            txt = _clean_group_text(txt)
            if not txt:
                # 洗完什么都不剩（比如整条就是 `</think>`）→ 这句不发，别让空壳进群
                LOG.info("群组：%s 这句洗完是空的（疑似只有思维标签），跳过", m.get("name"))
                continue
            # **复读用户原话就丢弃**：实测两个成员会把用户那句一字不改地当自己的发言
            #   （用户截图：他说"我们互相讨论…需要如何加插件"，两个人原样复读一遍）。
            #   那不是发言，是把上下文抄出来了。
            _last_user = ""
            _users_recent = []
            for _mu in (g.get("messages") or [])[-30:]:
                if str(_mu.get("who")) in ("你", "user", "我"):
                    _t = str(_mu.get("text") or "").strip()
                    if _t:
                        _users_recent.append(_t)
            _last_user = _users_recent[-1] if _users_recent else ""
            # 【比**最近 10 条**用户消息，不是只比最后一条】
            #   实测：它复读的是更早那句（"我们互相讨论让xiaojiao成为…"），只比最后一条就漏了。
            _echo = None
            for _u in _users_recent[-10:]:
                if txt.strip() == _u or (len(_u) >= 8 and _u in txt and len(txt) <= len(_u) + 12):
                    _echo = _u
                    break
            if _echo:
                LOG.info("群组：%s 这句是复读用户原话，丢弃｜%s", m.get("name"), txt[:30])
                continue
            # 【只动手不动嘴 → 补一句】实测：模型光调工具、正文返回空，
            #   于是这一轮 0 条消息（群里看着像死了）。这时由载体补一句人话，
            #   并把真实工具回执留在上面 —— 用户既能看见它干了什么，也有人说话。
            if not str(txt or "").strip():
                try:
                    _has_act = bool(_actions)
                except NameError:
                    _has_act = False
                if _has_act:
                    txt = "（我查了一下，工具回执在上面；这一步先到这儿。）"
                else:
                    continue
            try:
                # 【剧本群放行：编就是内容】用户："编造就是编造，我要让他们演到极致"。
                #   防编造那几道闸只对**技术群**有意义；演后宫戏时"编"正是要的东西。
                _is_rp = (str(g.get("mode") or "") == "roleplay")
                try:
                    _had_tools = bool(_actions)      # 本成员这轮真调过的工具
                except NameError:
                    _had_tools = False
                if _is_rp:
                    raise _RP_SKIP()
                # 先把编造的卡片**删掉**（不是整条丢），留下人话
                if _FAKE_CARD_RE.search(txt):
                    _stripped = _strip_fake_cards(txt)
                    LOG.info("群组：%s 这条含伪造插件卡片，已删掉卡片部分（剩 %d 字）",
                             m.get("name"), len(_stripped))
                    txt = _stripped
                    if not txt:
                        continue
                _viol = _ground_violations(txt, _had_tools)
                if _viol:
                    LOG.info("群组：%s 这条无凭证（%s），拦下｜%s",
                             m.get("name"), "、".join(_viol), txt[:40])
                    continue
            except _RP_SKIP:      # 剧本群：以上防编造全部跳过
                # 但现代词要换掉（手机/屏幕/语音 → 当时的说法），否则戏就出戏了
                txt = _de_modern(txt)
            except Exception as _ge:      # noqa: silent-ok — 判据出错就放行，不影响发言
                LOG.debug("群组·凭证判定失败：%s", _ge)
            # **链接要真存在**：编造的仓库当场标 404（用户实测"404啊 他们在演戏吗"）
            try:
                if not _is_rp:
                    txt, _bad_links = _verify_links(txt)
                    if _bad_links:
                        LOG.info("群组：%s 这条里有 %d 个编造的链接，已标出", m.get("name"), len(_bad_links))
            except Exception as _le:      # noqa: silent-ok — 查链接失败不影响发言
                LOG.debug("群组·链接核对失败：%s", _le)
            # **跑题的发言不落盘**（技术群才管；剧本群跑题也算戏，放行）
            if not _is_rp and _topic_drift(txt, g.get("topic")):
                LOG.info("群组：%s 这句跑题了（主题「%s」），不落盘｜%s",
                         m.get("name"), str(g.get("topic"))[:30], txt[:40])
                continue
            msg = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "who": m.get("name"), "text": txt, "by": who, "sec": _sec,
                   "round": r + 1, "actions": (_actions or [])[:6]}
            with _LOCK:
                d = _load()
                for gg in d.get("groups") or []:
                    if gg.get("id") != gid:
                        continue
                    gg.setdefault("messages", []).append(msg)
                    gg["messages"] = gg["messages"][-MAX_MSGS_STORE:]
                    _save(d)
            added.append(msg)
            _event("say", gid=gid, member=m.get("name"), chars=len(txt), by=who, sec=_sec,
                   actions=[a.get("tool") for a in (_actions or [])][:6])
            # 【自己提议】它写了〔提议：工具+说明〕就记下来，载体之后真去执行
            try:
                for _pm in re.finditer(r"[〔\[【]\s*提议\s*[:：]\s*([^〕\]】\n]{4,120})[〕\]】]",
                                       txt):
                    _prop = _pm.group(1).strip()
                    with _LOCK:
                        _d2 = _load()
                        for _gg in _d2.get("groups") or []:
                            if _gg.get("id") != gid:
                                continue
                            _ps = list(_gg.get("proposals") or [])
                            _ps.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "who": m.get("name"), "text": _prop[:160],
                                        "done": False})
                            _gg["proposals"] = _ps[-20:]
                            _save(_d2)
                    _event("propose", gid=gid, member=m.get("name"), text=_prop[:120])
                    LOG.info("群组：%s 提议 → %s", m.get("name"), _prop[:80])
                    if _EXEC_PROP is not None:
                        try:
                            _rtxt, _rwho, _racts = _EXEC_PROP(
                                {"name": m.get("name"),
                                 "persona": _persona_of(m, g),
                                 "model": m.get("model") or ""}, _prop)
                            if _rtxt:
                                _rm = {"id": uuid.uuid4().hex[:8],
                                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       # 【工具输出进代码围栏】原来直接把结果拼在〔已执行 …〕后面，
                                       # 表格/命令行输出会被当成普通文字折行，显示成一坨（用户实测）。
                                       # 包进 ``` 围栏，前端才按等宽块渲染。
                                       "who": "载体", "text": "〔已执行 %s〕\n```\n%s\n```" % (
                                           "、".join(a.get("tool") for a in (_racts or [])[:3]) or "提议",
                                           str(_rtxt)[:300].replace("```", "'''")),
                                       "by": _rwho or "", "sec": 0, "round": r + 1,
                                       "actions": (_racts or [])[:6]}
                                with _LOCK:
                                    _d3 = _load()
                                    for _g3 in _d3.get("groups") or []:
                                        if _g3.get("id") != gid:
                                            continue
                                        _g3.setdefault("messages", []).append(_rm)
                                        _g3["messages"] = _g3["messages"][-MAX_MSGS_STORE:]
                                        _save(_d3)
                                try:
                                    _register_written(gid, m.get("name") or "AI", _racts)
                                except Exception as _fe:      # noqa: silent-ok
                                    LOG.debug("群组·提议产物登记失败：%s", _fe)
                                added.append(_rm)
                                _event("propose_done", gid=gid, member=m.get("name"),
                                       tools=[a.get("tool") for a in (_racts or [])][:6])
                        except Exception as _pe:      # noqa: silent-ok
                            LOG.debug("群组·执行提议失败：%s", _pe)
            except Exception as _pme:      # noqa: silent-ok — 提议解析失败不能影响群聊
                LOG.debug("群组·解析提议失败：%s", _pme)

            # 【AI 自己传文件】写出来的东西自动登记成群文件（真人有文件也会丢进群）
            try:
                _register_written(gid, m.get("name") or "AI", _actions)
            except Exception as _fe:      # noqa: silent-ok
                LOG.debug("群组·登记 AI 文件失败：%s", _fe)

            # 动手了就记到这人身上（下一轮 prompt 会带"你最近做过什么"）
            for _a in (_actions or [])[:3]:
                try:
                    _line = str(_a.get("result") or "").strip().splitlines()
                    _line = (_line[0] if _line else "").strip()
                    _note_skill(gid, m.get("name"),
                                "用 %s 做了：%s" % (_a.get("tool"), _line[:40]))
                except Exception as _e:      # noqa: silent-ok
                    LOG.debug("群组·记 skills 失败：%s", _e)
    # 【互相蒸馏】一轮结束就把这一轮压成经验，存进本群共识（下一轮 prompt 会带上）
    try:
        if _DISTILL is not None and added:
            _new = [{"who": x.get("who"), "text": x.get("text")} for x in added]
            _note = str(_DISTILL(_new) or "").strip()
            if _note:
                with _LOCK:
                    _d4 = _load()
                    for _g4 in _d4.get("groups") or []:
                        if _g4.get("id") != gid:
                            continue
                        _dl = list(_g4.get("distilled") or [])
                        _dl.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "note": _note[:400]})
                        _g4["distilled"] = _dl[-12:]
                        _save(_d4)
                _event("distill", gid=gid, chars=len(_note), note=_note[:120])
                LOG.info("群组蒸馏：%s → %s", g.get("name"), _note[:80])
    except Exception as _de:      # noqa: silent-ok — 蒸馏失败不影响群聊
        LOG.debug("群组·蒸馏失败：%s", _de)
    LOG.info("群组：%s 跑了 %d 轮，新增 %d 条消息", g.get("name"), rounds, len(added))
    return {"ok": True, "added": added, "group": get(gid)}


def _bigrams(t):
    t = re.sub(r"[\s，。！？；：、,.!?;:~…\-—「」（）()【】\"'’‘“”]", "", str(t or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 2 else set()


def _too_similar(text, recent, floor=0.8, floor_contain=0.5):
    """这句话是不是把别人刚说过的又抄了一遍（整句或半句）？

    实测：整句照抄拦得住（重合度 ≥0.8），但「前半句照抄 + 后半句自己加」漏了 ——
    阿码说「空指针漏了才怪？边界条件不是那个空吗？」，前半句是小焦上一句的原话。
    所以再加一条：跟最近 2 条比，包含度 ≥ floor_contain 也算复读。
    """
    tb = _bigrams(text)
    if not tb:
        return False, "", 0.0
    for m in (recent or [])[-4:]:
        ob = _bigrams(m.get("text") or "")
        if not ob:
            continue
        inter = len(tb & ob)
        if not inter:
            continue
        jac = inter / float(len(tb) + len(ob) - inter)
        cont = inter / float(min(len(tb), len(ob)))
        if jac >= floor or cont >= floor_contain:
            return True, m.get("who") or "", round(max(jac, cont), 2)
    return False, "", 0.0


def _strip_name_prefix(text, name):
    t = str(text or "").strip()
    for pat in ("%s：", "%s:", "[%s] ", "【%s】"):
        head = pat % name
        if t.startswith(head):
            t = t[len(head):].strip()
    return t


def _reply_to_user(gid, text, speakers=2):
    """用户在群里说话 → 让 1-2 个成员**直接回他**（不走群聊那条复杂流水）。

    【为什么必须绕开】实测：群聊的 prompt 里有十几条互相竞争的约束（主题、节奏、角色角度、
    "不要重复别人"、"提出议"…），4B 会挑最容易的那条执行 —— 结果就是**完全无视用户**，
    继续聊 n 轮前的技术话题（用户原话："根本不理我"）。把用户的话塞进 system 最前面
    也压不住。
    这里改成"单任务"：只给它一句话、一个要求（回这个人），上下文只留 2 条，
    和一对一好友回复同一条路子 —— 小模型在"单任务"下才可靠。
    """
    g = get(gid) or {}
    members = [m for m in (g.get("members") or []) if m.get("name")][:max(1, speakers)]
    if not members:
        return []
    recent = (g.get("messages") or [])[-3:]
    bg = _transcript(recent)
    out = []
    for m in members:
        nm = str(m.get("name"))
        who = str(m.get("model") or "")
        sys_p = ("你是群里的「%s」。现在有人在群里说话，你**只用一到两句**回他，"
                 "口语、具体、别客套。不要复述他的话，不要写指令、不要解释你在做什么。"
                 "只输出你说出口的那句话。" % nm)
        if m.get("persona"):
            sys_p = "你的人设：" + str(m.get("persona"))[:300] + "\n" + sys_p
        msgs = [{"role": "system", "content": sys_p},
                {"role": "user", "content": "群里刚说的几句：\n%s\n\n他刚说：%s\n\n你回他："
                                            % (bg or "（无）", str(text)[:200])}]
        try:
            # _ask(member_model, messages, temperature) → 返回 (文本, 实际用的火种)
            _r = _ask(who or None, msgs)
            txt = str((_r[0] if isinstance(_r, (tuple, list)) and _r else _r) or "")
        except Exception as e:      # noqa: BLE001 — 单个成员失败不影响别人
            LOG.debug("群组·回应用户失败（%s）：%s", nm, str(e)[:80])
            continue
        txt = _clean_group_text(txt)
        if not txt or len(txt) < 2 or len(txt) > 200:
            continue
        msg = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "who": nm, "text": txt, "by": who or "默认角色", "round": 0, "actions": []}
        with _LOCK:
            d = _load()
            for gg in d.get("groups") or []:
                if gg.get("id") != gid:
                    continue
                gg.setdefault("messages", []).append(msg)
                gg["messages"] = gg["messages"][-MAX_MSGS_STORE:]
                _save(d)
                break
        out.append(msg)
    return out


def _carrier_github_search(gid, keyword):
    """**载体自己**去 GitHub 搜，把真结果贴进群 —— 不经模型的手。

    【为什么必须这样】实测：让 4B 自己调 `github_find_plugin`，它会演 ——
    调了工具、看都不看结果，照样编出仓库名/★320/评分 4.8/链接，用户点开全是 404。
    拦住它说话之后，它就干脆一声不吭（"给他们傻子一样"）。
    结论：**"找插件"这件事不该交给 4B 做**。代码去搜、原样贴出来，模型只负责接着说人话。
    """
    try:
        import requests
        r = requests.get("https://api.github.com/search/repositories",
                         params={"q": keyword, "sort": "stars", "order": "desc", "per_page": 5},
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "xiaojiao"}, timeout=20)
        if r.status_code != 200:
            body = "（载体去 GitHub 搜了，但接口返回 HTTP %s，没拿到结果）" % r.status_code
        else:
            items = (r.json() or {}).get("items") or []
            if not items:
                body = "（载体去 GitHub 搜「%s」，**没有结果**。）" % keyword
            else:
                lines = ["（以下是**载体自己去 GitHub 搜到的真实结果**，不是模型编的）",
                         "关键词：%s" % keyword, ""]
                for i, it in enumerate(items, 1):
                    lines.append("%d. %s  ★%s\n   %s\n   %s" % (
                        i, it.get("full_name"), it.get("stargazers_count"),
                        (it.get("description") or "").strip()[:90], it.get("html_url")))
                lines.append("")
                lines.append("注意：这些是**别的项目**，不是给小焦用的插件。"
                             "要用必须满足单文件 .py + 契约 + 只 import 纯功能库；"
                             "不满足就不能装。")
                body = "\n".join(lines)
    except Exception as e:      # noqa: BLE001 — 搜不到要如实说
        body = "（载体去 GitHub 搜的时候失败了：%s）" % str(e)[:80]
    msg = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "who": "载体", "text": body, "by": "carrier", "round": 0, "actions": []}
    with _LOCK:
        d = _load()
        for gg in d.get("groups") or []:
            if gg.get("id") != gid:
                continue
            gg.setdefault("messages", []).append(msg)
            gg["messages"] = gg["messages"][-MAX_MSGS_STORE:]
            _save(d)
            _event("carrier_github_search", gid=gid, keyword=keyword)
            break
    return msg


def set_script(gid, text):
    """把用户粘贴的**一整段设定**灌进群：主题 / 语言风格 / 互动规则 / 角色人设。

    【为什么做成"粘贴一整段"】用户的原话是"这种都要设计我填写" —— 他要能把
    剧本原样贴进来（后宫、年代、角色、风格、规则），而不是一条条手填。
    支持的写法（中英文冒号都行，可不按顺序）：
        主题：大周雍正年间，紫禁城后宫。
        语言风格：半文半白，古风宫廷腔调，多用"本宫/臣妾/妹妹"。
        互动规则：可以分享赏赐、糕点，暗中挑拨；遇到翻牌子要交锋。
        角色：
          甄嬛：聪慧隐忍，擅借刀杀人。
          华妃：嚣张跋扈，深爱皇上但善妒。
    解析出来的角色会**补进成员表**（已有同名就更新人设，没有就新建）。
    """
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "error": "设定是空的"}
    import re as _re5

    def _grab(label):
        m = _re5.search(r"(?m)^\s*%s\s*[：:]\s*(.+)$" % label, t)
        return m.group(1).strip() if m else ""

    topic = _grab("主题") or _grab("群主题")
    style = _grab("语言风格")
    rules = _grab("互动规则") or _grab("规则")
    # 角色：以「角色/人物/角色设定」开头的那一段里，每行 "名字：人设"
    roles = {}
    m = _re5.search(r"(?ms)^\s*(?:角色设定|角色|人物设定|人物)\s*[：:]\s*(.+)$", t)
    block = m.group(1) if m else t
    for line in block.splitlines():
        mm = _re5.match(r"^\s*[-·•\s]*([\u4e00-\u9fffA-Za-z0-9]{1,8})\s*[：:]\s*(.+)$", line)
        if not mm:
            continue
        nm, ps = mm.group(1).strip(), mm.group(2).strip()
        if nm in ("主题", "语言风格", "互动规则", "规则", "角色", "角色设定"):
            continue
        if len(ps) < 4:
            continue
        roles[nm] = ps[:300]
    with _LOCK:
        d = _load()
        hit = None
        for gg in d.get("groups") or []:
            if gg.get("id") != gid:
                continue
            if topic:
                gg["topic"] = topic[:300]
            if style:
                gg["style"] = style[:300]
            if rules:
                gg["rules"] = rules[:400]
            mem = gg.setdefault("members", [])
            for nm, ps in roles.items():
                ex = next((x for x in mem if x.get("name") == nm), None)
                if ex:
                    ex["persona"] = ps
                else:
                    mem.append({"name": nm, "persona": ps, "model": "", "color": ""})
            # 【剧本里没有的成员，清掉】用户原话："小焦什么鬼，小焦不要默认在里面啊"。
            #   建群时会自动塞默认成员（小焦/阿码/老曲…），一贴后宫剧本就变成
            #   "太监混进后宫"。既然剧本明确列了角色，就只留剧本里的人。
            if roles:
                gg["mode"] = "roleplay"      # 剧本群：编就是内容，防编造闸全部放行
                keep = set(roles)
                removed = [x.get("name") for x in mem if x.get("name") not in keep]
                gg["members"] = [x for x in mem if x.get("name") in keep]
                if removed:
                    LOG.info("群组：按剧本清掉非角色成员 %s", removed)
            gg["messages"] = gg.get("messages") or []
            _save(d)
            _event("script_set", gid=gid, roles=len(roles))
            hit = {"ok": True, "topic": topic[:40], "style": style[:30],
                   "rules": rules[:30], "roles": list(roles)}
            break
    if hit is None:
        return {"ok": False, "error": "没有这个群"}
    return hit


def user_say(gid, text, who="你", respond=True):
    """用户插一句；默认**立刻让群里有人回应**（真人群聊也是这样，你说话有人接）。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "空消息"}
    msg = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "who": str(who or "你"), "text": text, "by": "user", "round": 0}
    _hit = None
    with _LOCK:
        d = _load()
        for g in d.get("groups") or []:
            if g.get("id") != gid:
                continue
            g.setdefault("messages", []).append(msg)
            g["messages"] = g["messages"][-MAX_MSGS_STORE:]
            _save(d)
            _event("user_say", gid=gid, chars=len(text))
            _hit = {"ok": True, "message": msg}
            break
    if _hit is None:
        return {"ok": False, "error": "没有这个群"}
    # 【你说话就有人接】**走单任务通道**（见 _reply_to_user 的说明），
    # 不再交给群聊那条复杂流水 —— 那条会无视你。
    if respond:
        try:
            import threading as _th
            _th.Thread(target=_reply_to_user, args=(gid, text, 2),
                       daemon=True, name="xj-group-reply").start()
            _event("reply_trigger", gid=gid, why="用户说话了 → 单任务回应")
        except Exception as e:      # noqa: silent-ok — 起不来也不影响消息落盘
            LOG.debug("群组·触发回应失败：%s", e)
    # 【用户指令落盘】用户给过指令就记进 group["directive"]（只留最新一条），
    # 之后每一轮成员的 system 最前面都会带上它 —— 这是"硬性要求"的载体侧实现。
    if _is_order(text):
        with _LOCK:
            d = _load()
            for gg in d.get("groups") or []:
                if gg.get("id") != gid:
                    continue
                gg["directive"] = {"text": text[:200], "ts": msg["ts"]}
                _save(d)
                _event("directive_set", gid=gid, text=text[:60])
                break
    # 【活由载体干】用户说"去 github 找" → 载体自己去搜、把真实结果贴出来，
    #   不指望 4B 去调工具（实测它只会演）。模型之后只接着说人话。
    if _is_order(text) and any(w in text.lower() for w in ("github", "下载", "现成", "插件")):
        try:
            import threading as _th2
            _kw = "dsh plugin" if "dsh" in text.lower() else "mcp server tools"
            _th2.Thread(target=_carrier_github_search, args=(gid, _kw),
                        daemon=True, name="xj-carrier-gh").start()
        except Exception as e:      # noqa: silent-ok
            LOG.debug("群组·载体搜索启动失败：%s", e)
    return _hit


def stats():
    d = _load()
    gs = d.get("groups") or []
    return {"groups": len(gs),
            "messages": sum(len(g.get("messages") or []) for g in gs),
            "members": sum(len(g.get("members") or []) for g in gs),
            "path": _PATH}
