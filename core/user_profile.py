# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
"""小焦 · 载体层 · **用户画像库**（关于用户是谁、在乎什么）

【它是什么 —— 第四个库，跟前面三个都不一样】
  · `core/memory_vec.py`    对话记忆：**发生过什么**（全存，原始记录）
  · `core/spirit_memory.py` 精神记忆：**想明白了什么**
  · `core/preference.py`    偏好：**它自己反复起的心**
  · **本模块**             用户画像：**关于用户的特征**（有选择地存）

  前三个都是"它的"，这一个**是关于用户的**。所以必须分开存：
  检索时的分工也不同 —— 问事实走对话记忆，问"我上次说的那个"走画像 + 对话记忆，
  问新问题则把画像当**背景**注入。

【最重要的一条：谁来判断"该记什么"】
  **判据由模型给，存储由载体执行。**
  载体**绝不许**自己去判断"用户喜欢体育" —— 那就是死模板（代码写"关键词含篮球/NBA/湖人
  就记体育"）。为什么不行：
    · 用户随口问一次 ≠ 长期关注
    · 用户帮别人问 ≠ 他自己关注
    · 关键词匹配一定会误判
  而模型能区分"随口问"和"真的关注"、能结合上下文、判断错了还能修正。
  **载体要做的是给它"判断的机会"**（每轮问一句"这一轮有没有关于用户、值得长期记住的"），
  而不是替它判断。

【记录格式（规格给定）】
    {
      "id": "…",
      "ts": 1789…,                       # 载体填
      "kind": "兴趣" | "事实" | "偏好" | "关系" | 它自己起的短类别名（见 `KINDS` 处的实测教训）
      "content": "用户关注 NBA 篮球赛事",   # 模型给的一句话
      "source": "模型自己判断",             # **如实标：这不是载体推的**
      "evidence": "用户问了湖人 vs 勇士",    # 模型给的依据（载体照抄，不改写）
      "said_count": 1,                    # 用户说过几次（第一次就是 1；同一句话再说一次 +1）
      "hit_count": 0                      # 被召回几次（命中一次 +1：用得越多的兴趣越稳）
    }
  ⚠️ 这两个计数**故意分开**（2026-09-17 用户定的）：
    · `said_count` = **用户**说的次数（`add()` 去重命中时 +1）
    · `hit_count`  = **载体**召回它的次数（`hit()` 命中时 +1）
    拆之前两者共用 `hit_count`：连说三次「我 25 岁」库里只有 1 条、但计数是 6（3 次去重 + 3 次召回），
    **一个数字两种含义**，"用户强调过"和"这条好用"分不出来。现在各记各的。
    ⚠️ 拆分之前的老记录 `said_count` 是 **0**（那时候没人记过这个数），**不代表用户没说过** ——
    如实标着缺口，不拿 hit_count 反推、也不假装补过。

【载体在这一层做的全部事情】（不多做一件）
  1. 追加写盘（原子、只追加）
  2. 说过的次数记 `said_count`、被召回的次数记 `hit_count`（**两个计数语义不许混**）
  3. 把画像作为**事实**渲染给模型（`render()`）—— 渲染时**不加任何结论句**
  4. **不判断、不推断、不补全**：模型说记什么就记什么；模型说不记就一个字都不写
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

__all__ = ["KINDS", "KIND_MAX", "add", "all_records", "recent", "hit", "render", "stats",
           "count", "forget_all", "path", "parse_verdict", "clean_kind", "contradiction"]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "logs", "psyche")
_PATH = os.path.join(_DIR, "user_profile.jsonl")
_LOCK = threading.RLock()

# 规格给定的四个类别：**推荐**（提示词里让它优先用这四个）。
# ⚠️ 但它们**不是只许这四个** —— 实测教训（`tools/test_closed_loop.py` 案例 3）：
#   模型判「我是做后端的，平时都用 Python」值得记，给的类别是 `职业/技术栈`，
#   而载体当时**硬性拒收** → 一条真实、明确的用户事实被载体丢掉了。
#   那正是"载体替它做决定"：它说要记，载体因为标签不在白名单里就说不行。
#   现在的判据：四类**优先**；四类都不贴切时，它**可以自己起一个短类别名**（≤ `KIND_MAX` 字），
#   载体照收、照存、照原样标（`source` 已写明这是它自己给的类别）。
#   载体只挡**明显不成标签**的东西（空、带 JSON 符号、带换行、过长）—— 那是解析垃圾，不是类别。
KINDS = ("兴趣", "事实", "偏好", "关系")
KIND_MAX = 10

# 单条内容长度上限：内容太长就不是"一句话的画像"了，那是对话记忆该干的事。
MAX_LEN = 200
# 条数上限：画像要有选择，不能变成第二个对话库
MAX_ROWS = 500


def path():
    return _PATH


def _all_raw():
    out = []
    if not os.path.exists(_PATH):
        return out
    with open(_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:      # noqa: silent-ok — 坏行跳过（不因为一行坏了丢掉整个库）
                continue
            if isinstance(d, dict):
                # 老记录（拆分之前写的）没有这两个字段 —— **读的时候就补齐默认值**，
                # 免得每个读的人自己写 `.get(k, 0)` 各写各的、写歪一个就整条链错。
                # 注意：`said_count` 补 0 是"那时候没人记过这个数"，不是"用户没说过"（不拿 hit_count 反推）。
                d.setdefault("said_count", 0)
                d.setdefault("hit_count", 0)
                out.append(d)
    return out


def all_records():
    with _LOCK:
        return _all_raw()


def recent(n=5):
    rows = all_records()
    rows.sort(key=lambda r: float(r.get("ts") or 0))
    return rows[-max(1, int(n)):]


def count():
    return len(all_records())


def clean_kind(kind):
    """把模型给的类别收拾成一个**能当标签用**的短词；不成标签的返回 ""。

    **只管格式，不管内容**：四类优先，但它自己起的短类别名一样收 ——
    载体没资格判断「职业/技术栈」算不算一个类别（那是替它做决定）。
    """
    k = str(kind or "").strip().strip("「」\"'")
    if not k or len(k) > KIND_MAX:
        return ""
    if any(ch in k for ch in "{}[]\n\r\t\"'`"):      # JSON 残留 / 换行 → 解析垃圾，不是类别
        return ""
    return k


def _rewrite(rows):
    """整库重写（原子替换）。**只有写盘的几个地方**用，逻辑一处不能多。"""
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, _PATH)


# ================== 写入前的"内容闸门"（2026-11-14）==================
# 【为什么必须有 —— 用户原话「我的AI最近我发现越来越难用」】体检发现画像库里存着：
#   「用户叫小焦」（小焦是**它自己**的名字）、「用户称呼自己为'你'」、「用户称呼为'你'」、
#   「用户称呼其为'小焦'」、「…已执行 98 步 ▾ 这是载体真实做的事…」（工具轨迹）。
# 这些每轮都被注入 system，于是它真的开始管用户叫"小焦"，还反复纠结"你是谁、我是谁"。
# 判据是确定性的（格式 + 代词/它自己的名字）；**不判断"这件事该不该记"** —— 那仍是模型的决定。
_PROFILE_JUNK_MARKS = ("已执行 ", "这是载体真实做的事", "▾", "🌐 **", "⏳", "【回答格式",
                       "【系统指令】", "📋 **页面结构速览**")
_PRONOUN_NAMES = ("你", "我", "他", "她", "它", "自己", "对方", "用户", "本小姐", "主人")
_NAME_RE = re.compile(r"^用户(?:的名字是|叫|名叫|称呼为|称呼其为|称呼自己为|称呼我为|称呼)"
                      r"[「\"'‘]?([^」\"'’，。]{1,8})")


def _ai_self_name():
    """它自己叫什么（`logs/identity.json` 的 self_name；读不到就按默认）。"""
    try:
        import json as _j
        _p = os.path.join(_ROOT, "logs", "identity.json")
        with open(_p, encoding="utf-8") as f:
            n = str((_j.load(f) or {}).get("self_name") or "").strip()
        if n:
            return n
    except Exception:      # noqa: silent-ok — 没有设定文件就用默认名
        pass
    return "小焦"


def content_ok(content):
    """这条内容能不能当"关于用户的事实"存下来。返回 (ok, 原因)。"""
    c = str(content or "").strip()
    if not c:
        return False, "空"
    if any(m in c for m in _PROFILE_JUNK_MARKS):
        return False, "工具轨迹/载体指令残留，不是用户说的事"
    if len(c) > 120:
        return False, "太长（>120 字，多半是整段抓取或长文）"
    m = _NAME_RE.match(c)
    if m:
        val = m.group(1)
        if any(j in val for j in _PRONOUN_NAMES) or val == _ai_self_name():
            return False, "把代词/它自己的名字当成了用户的名字（%s）" % val
    return True, ""


def add(kind, content, why="", evidence="", source="模型自己判断"):
    """**只做存储**：模型说要记什么就记什么；载体不判断内容对不对、该不该记。

    返回记录 id；`content` 为空、或 `kind` 不成标签（见 `clean_kind()`）→ 返回 ""（**不写盘**）。

    【去重（用户报的 bug：同一个事实写了三遍）】
      `content` **完全相同**（strip 后逐字相等，不做模糊匹配）→ **不追加新行**，
      把旧记录的 `said_count` +1，返回**旧记录的 id**。
      为什么按"完全相等"：模糊匹配会把"我爱吃香菜"和"我不吃香菜"并成一条 ——
      那是**载体替它判断**（本项目最忌讳的事）。宁可留两条，也不许并错。
      ⚠️ 这里 +1 的是 `said_count`（**用户**说过几次），**不动 `hit_count`** ——
      `hit_count` 只由 `hit()` 记，表示**召回**过几次。两个计数不许互相加。
    """
    k = clean_kind(kind)
    c = str(content or "").strip()
    if not c or not k:
        return ""
    _ok, _why = content_ok(c)
    if not _ok:
        # 记一笔"为什么没存" —— 否则事后分不清"它没说要记"和"载体拦了"
        try:
            import logging as _lg
            _lg.getLogger("xiaojiao.user_profile").info(
                "画像：**没写进去**（%s）｜%s", _why, c[:40])
        except Exception:      # noqa: silent-ok
            pass
        return ""
    if len(c) > MAX_LEN:
        c = c[:MAX_LEN]
    with _LOCK:
        os.makedirs(_DIR, exist_ok=True)
        rows = _all_raw()
        for r in rows:
            if str(r.get("content") or "").strip() == c:
                r["said_count"] = int(r.get("said_count") or 0) + 1
                _rewrite(rows)
                return str(r.get("id") or "")
        rec = {
            "id": "%d-%s" % (int(time.time() * 1000), os.urandom(3).hex()),
            "ts": time.time(),
            "kind": k,
            "content": c,
            # `why` 与 `evidence` 都是**模型给的原文，载体照抄不改写** —— 改了就成了载体在替它解释
            "why": str(why or "")[:200],
            "evidence": str(evidence or "")[:200],
            "source": str(source or "模型自己判断"),
            # 第一次说就是 1（"说过几次"从这一次算起）—— 起始值 0 会让"连说三次"只数到 2
            "said_count": 1,
            "hit_count": 0,
        }
        # 上限保护：超了就丢掉最老的（画像要精，不是要全）
        if len(rows) >= MAX_ROWS:
            rows = rows[-MAX_ROWS + 1:]
        rows.append(rec)
        _rewrite(rows)
    return rec["id"]


def hit(rec_id=None, kind=None, keyword=None, ids=None):
    """命中一次 → `hit_count` +1（用得越多的兴趣越稳）。

    三种调用方式都收（历史上被三种写法调过）：
      · `hit("2026…-ab12")`   —— 单个 id（血管那条链就是这么记的）
      · `hit(ids=["a","b"])`  —— 一批 id
      · `hit(keyword="NBA")`  —— 按内容片段（只给自测/排查用）

    `id` 不存在时**静默返回**，不崩 —— 调用方（召回链）不该因为一条记录被删掉就整轮失败。
    """
    if isinstance(rec_id, (set, list, tuple)):
        ids = list(rec_id)
    elif isinstance(rec_id, str) and rec_id.strip():
        ids = [rec_id.strip()]
    ids = set(ids or [])
    n = 0
    with _LOCK:
        rows = _all_raw()
        for r in rows:
            ok = False
            if ids and r.get("id") in ids:
                ok = True
            elif keyword and keyword in str(r.get("content") or ""):
                ok = (kind is None or r.get("kind") == kind)
            if ok:
                r["hit_count"] = int(r.get("hit_count") or 0) + 1
                n += 1
        if n:
            _rewrite(rows)
    return n


def render(n=8):
    """把画像渲染成**事实**清单，给模型看。

    ⚠️ **这里一个字都不许加结论**：只列"它自己被记下来的那句话"，
       不写"所以用户喜欢体育"之类的推断 —— 那是模型该做的，不是载体。
    """
    rows = recent(n)
    if not rows:
        return ""
    lines = ["（下面这些是**之前它自己判断值得记住的、关于用户的事**，原样列着，不作推断）"]
    for r in rows:
        lines.append("- [%s] %s（依据：%s）"
                     % (r.get("kind"), r.get("content"), r.get("evidence") or "未给"))
    return "\n".join(lines)


def _json_objects(s):
    """按**括号配对**扫出文本里所有候选 JSON 对象。

    为什么不用正则：`re.search(r"\\{[\\s\\S]*\\}")` 是**贪婪**的 —— 模型先在正文里举一次
    格式例子、再给结论时（`按格式 {"remember":…} 我的判断是 {"remember":false}`），
    它会从第一个 `{` 一口气吃到最后一个 `}`，`json.loads` 直接失败 → **一条本该记下的东西被判成"不记"**。
    这就是"载体侧把它的判断丢了"，必须堵死。扫描时跳过字符串内部（`"` 与转义），
    不然 `content` 里带个花括号就会配错。
    """
    out = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append(s[start:i + 1])
                    start = -1
    return out


def _pick(text):
    """从一段输出里挑出**它那句判断**的对象（挑不到返回 None）。"""
    cands = []
    for blob in _json_objects(str(text or "")):
        try:
            d = json.loads(blob)
        except Exception:      # noqa: silent-ok — 这个候选不是合法 JSON，换下一个
            continue
        if isinstance(d, dict):
            cands.append(d)
    if not cands:
        return None
    # 取**最后一个**带 remember 字段的对象：模型常把格式例子先写一遍、结论写在后面，
    # 而例子里的 `remember` 也在（它就是照格式抄的）—— 所以"第一个"会取到例子。
    # 自测 `tools/test_user_profile.py` 第四节专门钉这一条。
    with_rem = [c for c in cands if "remember" in c]
    return with_rem[-1] if with_rem else cands[0]


def contradiction(text):
    """它的输出**自己跟自己不一致**时，返回一句可核对的原因；一致返回 ""。

    实测抓到的真原文（闭环实测案例 1 的写入轮）：
        {"remember": false,
         "kind": "兴趣", "content": "用户平时最爱看NBA，湖人球几乎一场不落",
         "why": "这是用户主动交代的个人兴趣偏好，属于值得长期记住的信息"}
    标志位写 false，可它把 kind / content 全填满了、why 还写着"值得长期记住"。

    载体的处理（三样都不许做：不改它的 flag、不静默丢掉、不替它下结论）：
      · 只把这个**事实**（它的两处对不上）原样告诉它，让它自己再说一次。
    判据是**结构**的，不是关键词猜的：真原文里两条"不记"（案例 4、6）填的是
    `"kind": null, "content": null`，而这一条两个字段都是满的 —— 差得很清楚。
    """
    d = _pick(text)
    if not isinstance(d, dict) or d.get("remember") is not False:
        return ""
    has_kind = bool(clean_kind(d.get("kind")))
    has_content = bool(str(d.get("content") or "").strip())
    if has_kind and has_content:
        return ("remember 写的是 false，但 kind=%r、content=%r 都填满了 —— 两处对不上"
                % (d.get("kind"), str(d.get("content"))[:40]))
    return ""


def parse_verdict(text):
    """解析模型那句判断。返回 `dict`（解析不出来返回 `{"remember": False}`）。

    【判据必须宽容、取值必须严】
      · 模型可能包着 ```json、先举例再给结论、带一堆解释 → **宽容**地逐个抠 JSON 对象
      · 但 `remember` 必须是真 true、`content` 非空 → **严**，宁可漏记不污染
    """
    s = str(text or "")
    if not s.strip():
        return {"remember": False, "why": "空"}
    d = _pick(s)
    if d is None:
        return {"remember": False, "why": "没有 JSON"}
    if d.get("remember") is not True:
        return {"remember": False, "why": str(d.get("why") or "模型说不记")}
    kind = clean_kind(d.get("kind"))
    content = str(d.get("content") or "").strip()
    if not kind or not content:
        # ⚠️ 模型说"要记"但类别不成标签 / 内容为空 → **不记**，并把原因带出去（可如实记账）
        return {"remember": False, "why": "类别或内容不合格（kind=%r）" % d.get("kind")}
    return {"remember": True, "kind": kind, "content": content,
            "why": str(d.get("why") or ""), "evidence": str(d.get("evidence") or "")}


def stats():
    rows = all_records()
    by = {}
    for r in rows:
        by[r.get("kind")] = by.get(r.get("kind"), 0) + 1
    return {"count": len(rows), "by_kind": by, "path": _PATH,
            "max_rows": MAX_ROWS}


def forget_all():
    """清空（只给测试/用户显式重置用，日常没人会调它）。"""
    with _LOCK:
        if os.path.exists(_PATH):
            os.remove(_PATH)


if __name__ == "__main__":       # 自带的冒烟自测（写临时库，不动真库）
    import tempfile
    _real = _PATH
    _PATH = os.path.join(tempfile.gettempdir(), "_up_smoke.jsonl")
    if os.path.exists(_PATH):
        os.remove(_PATH)
    try:
        assert add("", "x") == "", "空类别必须拒收"
        assert add("x" * 30, "x") == "", "过长类别必须拒收"
        assert add("{\"kind\": 1}", "x") == "", "JSON 残留不是类别，必须拒收"
        assert add("兴趣", "") == "", "空内容必须拒收"
        # 实测抓到的**真原文**（4B 就是这么答的）—— 它说要记，载体必须接住
        _real = ('```json\n{\n  "remember": true,\n  "kind": "职业/技术栈",\n'
                 '  "content": "用户是后端开发者，日常使用 Python 进行开发。",\n'
                 '  "why": "职业身份和技术栈是用户的核心身份标签",\n'
                 '  "evidence": "我是做后端的，平时工作里都用 Python"\n}\n```')
        _v = parse_verdict(_real)
        assert _v["remember"] is True and _v["kind"] == "职业/技术栈", _v
        assert add(_v["kind"], _v["content"]) != "", "自定类别也必须能落盘"
        i = add("兴趣", "用户关注 NBA 篮球赛事", why="主动问了湖人比赛",
                evidence="用户问了湖人 vs 勇士")
        assert i, "正常记录应当写入"
        assert count() == 2, count()
        assert "NBA" in render(), render()
        assert hit(keyword="NBA") == 1
        # 两个计数分开看：这条 **add 过一次（said=1）**、**hit 过一次（hit=1）**
        rows = {r["content"]: {"said": r.get("said_count", 0), "hit": r.get("hit_count", 0)}
                for r in all_records()}
        assert rows.get("用户关注 NBA 篮球赛事") == {"said": 1, "hit": 1}, rows
        v = parse_verdict('```json\n{"remember": true, "kind": "兴趣", '
                          '"content": "用户关注 NBA", "why": "问了湖人"}\n```')
        assert v["remember"] is True and v["kind"] == "兴趣", v
        assert parse_verdict('{"remember": false}')["remember"] is False
        assert parse_verdict('{"remember": true, "kind": "", "content": "x"}'
                             )["remember"] is False, "空类别必须拒收"
        assert parse_verdict("随便一句话")["remember"] is False
        print("✅ 用户画像库 冒烟自测通过；stats=%s" % stats())
    finally:
        if os.path.exists(_PATH):
            os.remove(_PATH)
        _PATH = _real
