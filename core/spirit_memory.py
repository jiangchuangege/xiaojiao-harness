# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
# 这就是"模型平等"和"变形金刚"的工程基础。
"""小焦 · 载体层 · 精神记忆库

【这段为什么这么设计】
    普通记忆（`core/memory_vec.py`）记的是**发生过什么**：谁说了什么、当时什么情况。
    那是"经历"。经历会越堆越多，但它不让人变强 —— 同一类问题第二次出现，
    依然要从头再走一遍。

    精神记忆记的是**学到了什么**：一条知识、一个方法、一次诊断经验。
    它是"认知"。第三次遇到同类问题时，载体可以直接把上次想明白的东西拿出来用，
    不必重新推一遍 —— 这才是"越用越强"里"强"的那部分。

    三个库各有分工（都落在 `logs/spirit_memory/`，一行一条、只追加）：
      · knowledge.jsonl   知识 —— 关于用户、世界、领域的**事实性认知**
      · method.jsonl      方法 —— 怎么做事：流程、判据、套路
      · diagnosis.jsonl   诊断经验 —— 出了什么错 → 怎么定位 → 怎么修好的

【为什么颗粒度必须是"一句话"】
    答案原文是**概率性的、一次性的**：同一次提问换个说法，生成的答案就不一样，
    把上一轮的答案原文存下来复用，等于把随机输出当成事实 —— 那是记忆污染，
    不是学习。所以本模块的粒度是**提炼过的一句话认知**，不是问答对。

    这条约束用护栏实现（不是靠注释自觉）：
      · `remember()` 拒收带 `answer` / `reply` / `output` 这类字段的记录；
      · 拒收超长文本（答案原文通常长，一句话认知通常短）；
      · 拒收"问：…答：…"结构的文本。
    被拒的记录返回 `ok=False` 与原因，**绝不静默存下去**。

【相似度判据为什么是 0.6】
    小脑是字级模型，精细语义弱：实测「我非常喜欢这个方案」与「我非常讨厌这个方案」
    余弦高达 0.904（比近义对的 0.814 还高）。所以**只靠相似度判定"是不是同一件事"
    不可靠**。
      · 相似度 < 0.6  →  明确不同题，直接分开存，不打扰模型；
      · 相似度 ≥ 0.6  →  可能同题也可能反义，**交给大脑（4B）判断**。
    没有判官时保守当作"同题"，因为此时漏合并会让同一件事存很多遍，
    而精神记忆的价值恰恰在于"同类的只留一条"。

【去掉会怎样】
    小焦会一直"经历"却从不"长本事"：每次都是第一次，用户看不到任何累积。
"""
import json
import os
import re
import threading
import time

__all__ = ["dir_path", "path_of", "remember", "recall", "same_topic",
           "should_merge", "stats", "looks_like_answer", "KINDS"]

KINDS = ("knowledge", "method", "diagnosis")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "logs", "spirit_memory")
_LOCK = threading.Lock()

# 同题判据的阈值：低于它明确不是同一件事，高于它需要大脑判断（见模块头说明）
SAME_TOPIC_THRESHOLD = 0.92
# 一句话认知的长度上限。超过它多半是被误传进来的答案原文。
_MAX_TEXT = 400
# 答案原文的特征字段：出现即拒收
_ANSWER_KEYS = ("answer", "reply", "output", "final", "response")
# "问：…答：…"这类问答对结构
_QA_RE = re.compile(r"[问Q][:：].{0,80}[答A][:：]")
_NORM = re.compile(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\[\]【】]")


def dir_path():
    return _DIR


def path_of(kind):
    """某个库的落盘路径。kind 不在三类里时返回 None（不猜、不兜底）。"""
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return None
    return os.path.join(_DIR, k + ".jsonl")


def _norm(text):
    return _NORM.sub("", str(text or "").lower())


def looks_like_answer(text):
    """这条文本像不像"答案原文"。

    判据三条，命中任一条即判为像：带问答对结构、超长、或本身就是一整段多句回答。
    **宁可少记，绝不把答案存进精神记忆** —— 存错了会让后面每一轮都被污染。
    """
    t = str(text or "").strip()
    if not t:
        return True
    if _QA_RE.search(t):
        return True
    if len(t) > _MAX_TEXT:
        return True
    # 含三个以上句末标点且长度过半，判为一整段回答（一句话认知通常只有一两个小句）
    stops = sum(t.count(c) for c in "。！？!?")
    if stops >= 3 and len(t) > _MAX_TEXT // 2:
        return True
    return False


def _vec(text):
    """取向量。向量服务不可用时返回 None —— 此时记忆照存，只是暂时不能比相似度。"""
    try:
        from core import embedder as E
        v = E.embed(text)
        if not v:
            return None
        # 压成 4 位小数存盘：512 维浮点原样落盘会让文件膨胀好几倍，精度损失可忽略
        return [round(float(x), 4) for x in v]
    except Exception:      # noqa: silent-ok — 小脑不可用不该阻断记忆写入
        return None


def same_topic(text_a, text_b):
    """两条文本的余弦相似度。算不出来时返回 None（调用方据此走保守分支）。"""
    va, vb = _vec(text_a), _vec(text_b)
    if not va or not vb or len(va) != len(vb):
        return None
    num = sum(x * y for x, y in zip(va, vb))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(y * y for y in vb) ** 0.5
    if not na or not nb:
        return None
    return num / (na * nb)


def _shared_terms(a, b):
    """两条文本共享多少个"实词"（中文取双字滑窗，英文取 3 字以上单词）。

    【为什么要它】实测：这份嵌入给**互不相关的句子**也算出 0.75~0.85 ——
    只看相似度的话，几乎所有新认知都会被判"同题"而拒收，记忆库长期不增长
    （这也是"学习有效性 1 好 3 平 1 差"的真因：新东西压根没存进去）。
    所以同题必须**同时**满足：相似度高 + 真的有共同实词。
    """
    import re as _re
    def bigrams(s):
        s = str(s or "")
        out = set()
        for i in range(len(s) - 1):
            w = s[i:i + 2]
            if _re.match(r"[\u4e00-\u9fff]{2}", w):
                out.add(w)
        out |= {w.lower() for w in _re.findall(r"[A-Za-z]{3,}", s)}
        return out
    return len(bigrams(a) & bigrams(b))


def should_merge(sim, llm_judge=None, text_a="", text_b=""):
    """按相似度（**加上实词重合**）决定"要不要并成同一条"。

    返回 (是否同题, 判据说明)。说明是要给日志与用户看的，必须如实写是谁判的：
    `规则`（相似度低于阈值直接分开）、`大脑`（>= 阈值且判官给了答案）、
    `保守`（>= 阈值但没有判官）。
    """
    if sim is None:
        return False, "保守：算不出相似度，当作不同题分开存"
    if sim < SAME_TOPIC_THRESHOLD:
        return False, "规则：相似度 %.3f < %.2f，明确不同题" % (sim, SAME_TOPIC_THRESHOLD)
    # 【高相似但没共同实词 → 判不同题】"后台挂了三天…" 和 "遇到报错先读堆栈…"
    #   实测相似度 0.79，但它们讲的根本不是一件事。
    _n = _shared_terms(text_a, text_b)
    if _n < 3:
        return False, "规则：相似度 %.3f 但只共享 %d 个实词，不是同一件事" % (sim, _n)
    if callable(llm_judge):
        try:
            verdict = llm_judge(text_a, text_b)
            same = bool(verdict)
            return same, "大脑：相似度 %.3f 达阈值，交由 4B 判断 → %s" % (sim, "同题" if same else "不同题")
        except Exception as e:      # noqa: silent-ok — 判官挂了就退回保守分支
            return True, "保守：相似度 %.3f 达阈值，但判官不可用（%s）" % (sim, type(e).__name__)
    return True, "保守：相似度 %.3f 达阈值，无判官，按同题处理" % sim


def _rows(kind):
    p = path_of(kind)
    out = []
    if not p or not os.path.exists(p):
        return out
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:      # noqa: silent-ok — 坏行跳过，不影响其余记忆
                    continue
    except Exception:      # noqa: silent-ok — 读不到就当这个库还空着
        return []
    return out


def _append(kind, rec):
    p = path_of(kind)
    os.makedirs(_DIR, exist_ok=True)
    with _LOCK:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def remember(kind, text, tags=None, source="", llm_judge=None, **kw):
    """存一条精神记忆。返回 `{"ok": bool, "action": str, "why": str, "id": str}`。

    action 三种：
      · `added`      新存了一条
      · `duplicate`  已有同题，**不重复存**（这才是精神记忆该有的样子）
      · `rejected`   被判为答案原文，拒收

    注意本函数**从不删除、也从不覆盖**任何既有记录：只追加。判为同题时是"不写"，
    不是"改写旧的"。历史一旦被改写，回滚与追责都没了依据。
    """
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"ok": False, "action": "rejected", "why": "kind 必须是 %s 之一" % " / ".join(KINDS), "id": ""}

    for bad in _ANSWER_KEYS:
        if bad in kw:
            return {"ok": False, "action": "rejected",
                    "why": "带 `%s` 字段：精神记忆只存一句话认知，不存答案原文" % bad, "id": ""}

    t = str(text or "").strip()
    if looks_like_answer(t):
        return {"ok": False, "action": "rejected",
                "why": "像答案原文（问答对结构 / 超长 / 整段回答），只收提炼过的一句话认知",
                "id": ""}

# 【写入前自校验：借灵枢那种"白箱管线"的最小版】
    #   问题（实测）：`remember` 收的是"模型说的一句话"，**没有任何核对**，
    #   于是模型编的东西也会被当成认知存下来，下一轮还被当权威召回。
    #   这里加一道最小校验，只做三件能真判的事：
    #     ① 有没有来源（source）—— 没有就标 basis=self；
    #     ② 句子里有**具体数字/年份/版本号**这类可证伪事实，却又是 basis=self → **拒收**；
    #     ③ 其余情况收下，但把 `basis` / `verified` 一起写进记录，**以后能追责**。
    _src = str(source or "").strip()
    _basis = "self"
    if _src and not re.search(r"(模型|自述|推测|猜测|guess|self)", _src, re.I):
        _basis = "external"          # 有真实来源（工具/用户/网页）
    elif _src:
        _basis = "self"
    _has_fact = bool(re.search(r"\d{4}\s*年|\d+(?:\.\d+)?\s*(?:%|倍|条|个|次|元|天|小时)"
                               r"|v?\d+\.\d+\.\d+|第[一二三四五六七八九十]+[条章步]", t))
    if _basis == "self" and _has_fact:
        return {"ok": False, "action": "rejected",
                "why": "这条带具体数字/年份，但没有外部来源（basis=self）—— 编的东西不许当认知存",
                "id": ""}

        # 同题判定：拿新文本与同类已有记录比
    rows = _rows(k)
    best, best_row = None, None
    for r in rows:
        prev = r.get("text") or ""
        if not prev:
            continue
        sim = same_topic(t, prev)
        if sim is not None and (best is None or sim > best):
            best, best_row = sim, r
    if best_row is not None:
        merge, why = should_merge(best, llm_judge=llm_judge, text_a=t, text_b=best_row.get("text") or "")
        if merge:
            return {"ok": True, "action": "duplicate", "id": best_row.get("id", ""),
                    "why": "已有一条同题记忆（%s），不重复存" % why}

    rec = {"id": "%s-%d" % (k[:4], int(time.time() * 1000)), "ts": time.time(),
           "kind": k, "text": t, "tags": list(tags or []), "source": _src,
           "basis": _basis, "verified": (_basis == "external")}
    rec.update({kk: vv for kk, vv in kw.items() if kk not in _ANSWER_KEYS})
    v = _vec(t)
    if v:
        rec["vec"] = v
    try:
        _append(k, rec)
    except Exception as e:      # noqa: silent-ok — 落盘失败只影响"越用越强"，不影响这一轮回答
        return {"ok": False, "action": "rejected", "why": "落盘失败：%s" % type(e).__name__, "id": ""}
    return {"ok": True, "action": "added", "id": rec["id"], "why": "新存一条"}


def recall(query, k=3, kind=None, min_sim=None):
    """按相似度召回精神记忆。

    返回列表，每项 `{"id", "kind", "text", "tags", "sim"}`，按相似度降序。
    **给的是"素材"不是"答案"** —— 调用方要把这些交给模型去推理和组织措辞，
    不能原文回吐给用户。
    """
    q = str(query or "").strip()
    if not q:
        return []
    kinds = [kind] if kind else list(KINDS)
    qv = _vec(q)
    out = []
    for kd in kinds:
        for r in _rows(kd):
            t = r.get("text") or ""
            if not t:
                continue
            sim = None
            rv = r.get("vec")
            if qv and rv and len(qv) == len(rv):
                num = sum(x * y for x, y in zip(qv, rv))
                na = sum(x * x for x in qv) ** 0.5
                nb = sum(y * y for y in rv) ** 0.5
                if na and nb:
                    sim = num / (na * nb)
            if sim is None:
                # 没有向量就退回字面重合：至少能被精确复述的查询命中
                if _norm(t) and (_norm(t) in _norm(q) or _norm(q) in _norm(t)):
                    sim = SAME_TOPIC_THRESHOLD
                else:
                    continue
            if min_sim is not None and sim < min_sim:
                continue
            out.append({"id": r.get("id", ""), "kind": kd, "text": t,
                        "tags": r.get("tags") or [], "sim": round(float(sim), 4)})
    out.sort(key=lambda x: -x["sim"])
    return out[:max(1, int(k))]


def stats():
    """三个库各自的条数与文件大小。读不到文件时如实计 0，不假装有数据。"""
    out = {"dir": _DIR, "kinds": {}}
    total = 0
    for kd in KINDS:
        p = path_of(kd)
        rows = _rows(kd)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        out["kinds"][kd] = {"count": len(rows), "bytes": size, "path": p}
        total += len(rows)
    out["total"] = total
    return out
