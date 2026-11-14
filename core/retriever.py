# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
# 这就是"模型平等"和"变形金刚"的工程基础。
"""小焦 · 载体层 · 记忆检索（无限 1：记忆无限 —— 策略层）

memory_vec 负责"存 + 算余弦"，这里负责**策略**：时间衰减、相似度阈值、top-K、
注入 token 上限、以及把每次检索如实写进 `logs/memory_retrieval.log`。

┌─ 一条关键设计（载体优先的取舍，和 spec 字面略有不同，理由在下面）────────────┐
│ spec 写的是"余弦相似度 + 时间衰减，阈值 0.6"。如果**把衰减乘进阈值判定**，会出事：│
│ 一条 0.85 分的老记忆，衰减 0.4 后只剩 0.34 < 0.6 → 被丢掉。                  │
│ 那"三年前说的那个事"就永远检索不到了 —— 正好打死无限 1 的招牌场景。            │
│                                                                          │
│ 所以这里拆开用：                                                          │
│   · **阈值只判原始余弦**（判"这条到底相不相关"）—— 相关就是相关，跟多久以前无关；│
│   · **时间衰减只参与排序**（同样相关时，优先想起最近的）—— 这才是我们真正想要的 │
│     人类式记忆：老的能想起来，但新的更靠前。                                 │
└──────────────────────────────────────────────────────────────────────────┘

衰减分档照 spec：7 天内 ×1.0 / 30 天内 ×0.7 / 更早 ×0.4。
"""
import json
import os
import re
import threading
import time

from . import embedder, memory_vec

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_ROOT, "logs", "memory_retrieval.log")
_LOG_LOCK = threading.Lock()

# ===== 策略参数（都可被操控文件覆盖，见 xiaojiao_app 里的 _memory_cfg）=====
TOP_K = 5                 # 默认取最像的 5 条
THRESHOLD = 0.6           # 相似度阈值（判在原始余弦上）
MAX_TOKENS = 2000         # 注入进 system 的记忆文本上限
DECAY_7D = 1.0
DECAY_30D = 0.7
DECAY_OLD = 0.4
_D7 = 7 * 86400.0
_D30 = 30 * 86400.0

# ===== 载体二次判断（小脑召回 → 大脑精排）=====
# 【为什么必须有这一层 —— 小脑的已知边界，不是它的 bug】
#   小脑是**字级**模型，均值池化出来的向量在"主题级"够用，在"精细语义"上不行。实测：
#     · "我非常喜欢这个方案" vs "我非常讨厌这个方案" 余弦 **0.904**（近义对才 0.814）
#       —— 两者共享 10/11 个字，字级表示看到的是"同一串字"，不是"相反的意思"。
#     · "今天天气真好" vs "数据库索引用 B+ 树" 余弦 0.570 —— 无关项也偏高（各向异性）。
#   而 `THRESHOLD = 0.6` 只能挡住一部分。剩下的**反义/无关**如果直接注进 system，
#   模型就会拿"A 的反面"去回答"A 的问题" —— 这比"没记住"更糟。
# 【怎么补】小脑负责**召回**（宁可多召回），大脑负责**精排**（它读得懂语义）。
#   一次额外的小模型调用，只在"有多条候选"时才发生。
#   ⚠️【2026-09-18 改】原来这里写的是"失败一律放行（绝不因为判官挂了就丢记忆）"——
#   现在**分两种失败、两种处置**（见 `rerank` 里的说明）：
#     · **判官不可用**（大脑没起/报错/返回空）→ **不再全部放行**：按 `THRESHOLD` 卡一道，
#       但仍至少留 top1（不空手而归）；
#     · **承载它的那段代码抛异常** → 仍**全部放行**（这是 `rerank` 最外层的兜底，没改）。
RERANK = True                 # 总开关（操控文件可覆盖）
RERANK_MIN_HITS = 2           # 只有 1 条候选时不值得多跑一次模型
RERANK_MAX = 5                # 最多让大脑看几条（再多它也会挑花眼）
# 【为什么要"只在排名含糊时才精排" —— 这是实测逼出来的闸门，不是省事】
#   精排要调一次大脑，实测给检索加了 ~400ms（4ms → 409ms）。
#   而「无限 1 · 记忆无限」的验收线是**检索延迟 < 100ms** —— 无条件精排会直接把它判红。
#   想清楚"什么时候才值得花这 400ms"就清楚了：
#     · top1 明显领先（分差 ≥ 0.10）→ 向量排序本身就是可信的，再问大脑纯属白花时间；
#     · top1 与 top2 咬得很近（分差 < 0.10）→ **这正是反义/无关混进来的典型形状**
#       （实测「我喜欢蓝色」0.90 vs「我讨厌蓝色」0.88，分差只有 0.02），必须让大脑裁一下。
#   也就是说：这个闸门按"精排能不能改变结果"来开门，而不是按"跑测试方不方便"。
RERANK_GAP = 0.50             # top1 与 top2 的分差低于它，才认定"排名含糊"、需要精排
# 【2026-09-18 改：0.10 → 0.50，实测被逼出来的】
#   上面那套推理没错，但 0.10 这条线**太容易过**：真实查询里 top1 领先 0.2 是家常便饭，
#   于是绝大多数查询都走"排名不含糊 → 跳过精排"，**精排实际上很少跑**。
#   实测日志（用户给的两条）：
#     「检索精排：3 条候选 → 判官不可用，全部保留」
#     「检索精排：3 条候选 → top1 领先 0.236，排名不含糊 → 跳过精排」
#   两条路都不跑精排 → 噪音（"用户最近在学游泳"配"服务器安全"）直接进 system。
#   0.50 的含义变成"**只有 top1 真的碾压**才跳过"：机制保留（极端清晰时仍省一次模型调用），
#   但不再动不动就跳过。代价是精排调用变多（每次 ~400ms，见上面那段说明）——
#   这是**明知代价、选择精度**，不是忘了那条 100ms 验收线。
#   ⚠️ 还改了第二处：判官不可用时不再"全部保留"，改成按 THRESHOLD 卡一道（见 `rerank`）。
# 【为什么召回要单独给一道更低的底线 —— 实测抓出来的真 bug】
#   原来召回直接用 THRESHOLD = 0.6。于是：一条**确实相关但排名靠后**的长记忆，
#   分只有 0.548，**在精排之前就被滤掉了** —— 大脑连看都没看到它，精排显示「候选不足，跳过」。
#   实测：问「我上个月参加了什么会」，长记忆排第 5（0.548）被 0.6 拦掉，
#   最终注入的是毫不相干的短记忆（0.613）。
#   召回与判定是**两件事**：召回该宽（宁可多捞几条给大脑看），判定才该严。
#   所以召回用这条更低的底线，收口交给大脑精排；判官没真正筛选时退回原阈值（见下）。
RERANK_RECALL_THRESHOLD = 0.35  # 宽松召回底线（只用于给大脑看的候选集）
_RERANK_PROMPT = (
    "下面是一句用户提问和若干条历史记忆。请挑出**能用来回答这个问题**的记忆。\n"
    "判断标准（很重要，请逐条看）：\n"
    "  1. 记忆内容与提问说的是**同一件事、同一个方向** → 相关\n"
    "  2. 记忆与提问**方向相反**（问「喜欢什么」而记忆说「讨厌某物」；"
    "问「住在哪」而记忆说「不想说」）→ **不相关**，方向相反的记忆拿来回答会答反\n"
    "  3. 只是碰巧出现同一个词、但说的不是一回事 → 不相关\n"
    "只输出相关记忆的**编号**，用逗号分隔（例：1,3）；一条都不相关就只输出「无」。"
    "不要输出任何解释、不要复述记忆内容。\n\n"
    "用户提问：%s\n\n候选记忆：\n%s\n")


def decay(age_seconds):
    """时间衰减系数：7 天内 1.0 / 30 天内 0.7 / 更早 0.4。"""
    if age_seconds is None:
        return DECAY_7D
    if age_seconds <= _D7:
        return DECAY_7D
    if age_seconds <= _D30:
        return DECAY_30D
    return DECAY_OLD


def estimate_tokens(text):
    """与 xiaojiao_app._estimate_tokens 同一口径（cjk×1.5 + other/3）。

    优先借 app 的实现，保证"注入预算"和"上下文预算"是同一把尺子；借不到就用本地等价式。
    """
    try:
        import sys
        app = sys.modules.get("xiaojiao_app")
        if app is not None and hasattr(app, "_estimate_tokens"):
            return int(app._estimate_tokens(text))
    except Exception:      # noqa: silent-ok — 借不到就本地算，结果一致
        pass
    t = str(text or "")
    if not t:
        return 0
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 1.5 + (len(t) - cjk) / 3) + 1


# ================== 修三：注入必须**标明来源**（谁说的）==================
# 【实测症状 —— 这条比"检索不准"严重】
#   用户问「你还记得上一次吗」，它答：
#   「我记不起上一次我们聊了什么 —— 不过，我确实记得一些**你曾经提到过的内容**：…哔哩哔哩…」
#   那些东西**根本不是用户说的**（是抓取结果的残留），却被冠上了"你曾经提到过"。
#   根因就在这里的老实现：**只要正文不是「用户：…」的形状，就一律贴"用户曾说过"** ——
#   等于把「它说的」「工具给的」「它学到的」统统说成了「用户说的」。
# 【现在怎么标】按正文里**真实的分段**标；标不出"用户说的"，就不许说是用户说的：
#   「用户：X」+「小焦：Y」   → 【你说过的】X ／【小焦说过的】Y
#   只有「小焦：Y」           → 【小焦说过的】Y
#   只有「用户：X」           → 【你说过的】X
#   其它（学到的知识／工具结论等）→ 【它自己记下的一条】…
#     ⚠️ 最后这一类**绝不写"你说过的"** —— 宁可标得保守，也不许把来源说错。
_SRC_USER = "【你说过的】"
_SRC_SELF = "【小焦说过的】"
_SRC_OTHER = "【它自己记下的一条】"


def format_memory_line(text):
    """把一条记忆正文格式化成**带来源标记**的一行（修三的唯一出口）。

    【为什么要跟踪"当前说话人"】对话记忆是「用户：…\\n小焦：…」两段，
    但一段可能折成多行。逐行判前缀之后，**没带前缀的续行应该算上一个说话人的**，
    而**从头到尾都没有说话人前缀的**（学到的知识、工具结论）**一个都不算"你说的"**。
    实测踩到过：把 `【http】HTTP 是一个协议…` 这种知识行当成"小焦说的" ——
    那同样是把来源标错了，只是方向反过来。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    user_part, self_part, other_part = "", "", ""
    cur = None
    for seg in raw.split("\n"):
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith("用户："):
            cur = "user"
            user_part = (user_part + " " + seg[len("用户："):].strip()).strip()
        elif seg.startswith("小焦："):
            cur = "self"
            self_part = (self_part + " " + seg[len("小焦："):].strip()).strip()
        elif cur == "user":
            user_part = (user_part + " " + seg).strip()
        elif cur == "self":
            self_part = (self_part + " " + seg).strip()
        else:
            other_part = (other_part + " " + seg).strip()
    bits = []
    if user_part:
        bits.append("%s%s" % (_SRC_USER, user_part))
    if self_part:
        bits.append("%s%s" % (_SRC_SELF, self_part))
    if other_part:
        bits.append("%s%s" % (_SRC_OTHER, other_part))
    if not bits:
        return "- %s%s" % (_SRC_OTHER, raw.replace("\n", " "))
    return "- " + " ｜ ".join(bits)


def clip_line(line, limit):
    """把注入行截到 `limit` —— 尽量切在分隔符/句末，别把来源标记截成半截。"""
    if len(line) <= limit:
        return line
    head = line[:limit]
    cut = max(head.rfind(" ｜ "), head.rfind("。"), head.rfind("！"), head.rfind("？"))
    if cut >= limit // 2:
        return head[:cut]
    return head + "…"


_Q_MARKS = ("什么", "谁", "哪", "怎么", "多少", "为什么", "是否", "能不能", "有没有", "是不是",
            "多久", "几点", "几号", "几时", "吗", "呢")


def _looks_like_question(text):
    """是不是**在提问**（判据要窄：只认明确问句；宁可少判，也不误伤事实陈述）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t.endswith(("？", "?", "吗", "呢")):
        return True
    return any(w in t for w in _Q_MARKS)


def _bigram_overlap(a, b):
    """共有 2-gram ÷ 短句的 2-gram 数（0~1）。用它判"是不是同一句"，不调模型、不依赖向量。"""
    def _bg(s):
        s = "".join(ch for ch in str(s or "") if ch.strip())
        return set(s[i:i + 2] for i in range(max(0, len(s) - 1)))
    A, B = _bg(a), _bg(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(min(len(A), len(B)))


def _drop_self_echo(query, hits, line=0.9):
    """剔掉"同一个问题 + 它自己上次的回答"那些行。返回 `(保留的, 剔掉几条)`。

    【为什么必须有】实测（2026-09-18）：用户说了四件事、再问「我住在哪」，
    它答「我没有关于你住哪的信息」—— 因为 top1 那条的 key 就是这句问题本身，
    内容是它上一轮的同款回答（相似度 1.0000，而真正的事实那条只有 0.89）。
    **它拿自己上次的"我不知道"当事实**。剔掉这一行，事实那行就会顶上来。
    【为什么只剔问句】`key` 相同的**陈述句**（「我叫张三」）恰恰是记忆的正主，
    那是"用户亲口说的"，一个字都不能动 —— 所以这里再要求"用户那半句是问句"。
    """
    out, dropped = [], 0
    for h in hits or []:
        k = str((h or {}).get("key") or "")
        txt = str((h or {}).get("text") or "")
        # ⑤ 【2026-11-14】载体自己拼进"用户那句话"的指令尾巴被存进来的行 → 永远不注入。
        #   实测形状：`用户：你好 【回答格式，务必遵守】先用 <思考> … 小焦：…`。
        #   key 就是「你好」、相似度 1.00，注入进去模型就照着自己上次那段"格式说明"再答一遍
        #   （用户看到的：说「你好」，它答数学题/格式说明）。写入侧已经堵住源头，
        #   这里再堵一次读出侧 —— 老备份被恢复、别处写进来的，也不会漏过去。
        if "【回答格式，务必遵守】" in txt or "【系统指令】" in txt:
            dropped += 1
            continue
        # ④ 这一条是"它自己说我不知道"那一类 → 永远不注入（存量里还有一批这种行）
        if any(w in txt for w in ("没有关于你", "没有关于你住", "没有记录", "没查到", "没有关于“你",
                                  "我刚才查了一下我的记忆", "我目前没有关于", "没有关于你生日")):
            dropped += 1
            continue
        u = ""
        for seg in txt.split("用户：")[1:]:
            u = seg.split("\n")[0].strip()
            break
        if k and _bigram_overlap(query, k) >= line and _looks_like_question(u or k):
            dropped += 1
            continue
        out.append(h)
    return out, dropped


def retrieve(query, top_k=None, threshold=None, max_tokens=None,
             log=True, kind=None, now=None):
    """检索相关记忆并拼成可注入的文本。

    返回 dict：
      hits        全部命中（含 score / decayed / ts / kind）
      used        真正被注入的（受 max_tokens 限制）
      text        注入用文本（空串表示没有可用记忆）
      tokens      注入文本的 token 数
      latency_ms  本次检索总耗时（向量 + 载体二次判断）
      vector_ms   **纯向量检索**那一段（无限 1 的"检索延迟 < 100ms"判的是它）
      rerank_ms   载体二次判断那一段（要调一次大脑，没触发时为 0）
      rid         本次检索 id（给 record_usage 回填"模型是否用了"）

    【为什么把延迟拆成两段报】
      加"载体二次判断"之前，检索纯是向量运算（实测 3~5ms），所以"检索延迟 < 100ms"
      是对**整个检索**说的。加了精排之后，"整个检索"里多了一次大脑调用（实测 +400ms）——
      这时候还报一个数，等于把"向量快不快"和"精排跑没跑"混成一个指标：
      要么用 400ms 冤枉向量层，要么用 5ms 掩盖精排的开销。拆开报，两个问题各自看得见。

    【为什么这么设计】模型不需要"记住"任何东西 —— 记忆在模型外面（向量库），
    要用的时候按查询检索 top-K 注入 system。这样：
    ① 模型 ctx 不随对话变长而爆（无限 6）；
    ② 换任何模型都自带全部历史（模型平等）；
    ③ "检索"这一步是可测的（命中率/使用率/延迟都有数字，见 test_memory_recall）。
    时间衰减**只影响排序、不参与阈值判断**：否则"三年前说的那件事"会被衰减掉、
    直接检索不到 —— 那等于系统自己把用户的经历弄丢了。

    【去掉它会怎样】要么把所有历史塞进 ctx（本地 20224 装不下 100 轮对话），
    要么模型只能记住最近几轮 —— 用户说"我叫张三"之后问"我叫什么"，它会答不上来。
    """
    t0 = time.time()
    top_k = int(TOP_K if top_k is None else top_k)
    threshold = float(THRESHOLD if threshold is None else threshold)
    max_tokens = int(MAX_TOKENS if max_tokens is None else max_tokens)
    now = time.time() if now is None else float(now)

    # 召回用宽松底线（见 RERANK_RECALL_THRESHOLD 的说明）：先把候选捞全，再让大脑收口。
    _recall_th = min(float(threshold), float(RERANK_RECALL_THRESHOLD)) if RERANK else threshold
    raw = memory_vec.search_memory(query, top_k=max(top_k * 3, top_k),
                                   threshold=_recall_th, dedup_text=True)
    vector_ms = (time.time() - t0) * 1000.0      # 纯向量检索这一段（判据：< 100ms）
    # ★ **剔掉"自我回灌"行（2026-09-18 补，用户实测抓到的真病根）** ★
    # 【症状】用户先说了四件事，再问「我住在哪」→ 它答「我没有关于你住哪的信息」，五问四错。
    #   查库发现：四件事**都在库里**（相似度 0.89 能被检索到），可每次 top1 都是**1.0000**
    #   的另一条 —— 那条的 key 就是**这句问题本身**，内容是**它上一轮的回答**（"我没有记录"）。
    #   于是：它拿自己上次"我不知道"当事实，一字不改地再说一遍 —— **错答自我强化**。
    #   这是"自我回灌"最凶的那个形状：不是分值虚高，是把它自己的回答当成了知识。
    # 【判据（确定性、不调模型）】一行同时满足两条就剔：
    #   ① `key` 与本次提问**几乎同一句**（2-gram 重合 ≥ 0.9）；
    #   ② 用户那半句是**问句**（不是陈述）——陈述句的 key 相同反而是宝贝（「我叫张三」）。
    raw, _dropped = _drop_self_echo(query, raw)
    if _dropped:
        try:
            import logging
            logging.getLogger("xiaojiao.retriever").info(
                "剔掉 %d 条自我回灌行（同一句问题 + 它自己上次的回答）：%s",
                _dropped, str(raw[0].get("text"))[:40].replace("\n", " ") if raw else "剔完没别的了")
        except Exception:      # noqa: silent-ok — 日志失败不影响检索
            pass
    if kind:
        raw = [h for h in raw if h.get("kind") == kind]

    hits = []
    for h in raw:
        age = max(0.0, now - float(h.get("ts") or now))
        d = decay(age)
        hits.append({"id": h["id"], "text": h["text"], "kind": h.get("kind", "dialogue"),
                     "entities": h.get("entities") or [], "ts": h.get("ts"),
                     "score": h["score"], "decay": d,
                     "decayed": round(h["score"] * d, 4),
                     "age_days": round(age / 86400.0, 2)})
    # 排序：衰减后的分数（同样相关时，最近的更靠前）
    hits.sort(key=lambda h: -h["decayed"])
    hits = hits[:top_k]

    # ---- 载体二次判断：小脑召回 → 大脑精排（见 `rerank` 的说明）----
    # 放在"排好序、还没装进 token 预算"这一刻：先让大脑把反义/无关剔掉，
    # 省下来的 token 预算就留给真正相关的那些。
    before_n = len(hits)
    _t_rr = time.time()
    hits, rerank_note = rerank(query, hits)
    rerank_ms = (time.time() - _t_rr) * 1000.0
    # 判官**真的筛过**才算数（说明文本以「保留」开头）；跳过 / 不可用 / 保守放行时，
    # 把宽松召回带进来的低分候选按原阈值滤掉 —— 否则一关精排，无关记忆全被注入。
    if not str(rerank_note).startswith("保留"):
        _strict = [h for h in hits if float(h.get("score") or 0) >= float(threshold)]
        hits = _strict or hits[:1]
    try:
        if before_n >= RERANK_MIN_HITS:
            import logging
            logging.getLogger("xiaojiao.retriever").info(
                "检索精排（载体二次判断）：%d 条候选 → %s", before_n, rerank_note)
    except Exception:      # noqa: silent-ok — 日志失败绝不影响检索
        pass
    for i, h in enumerate(hits, 1):        # 补回 rank（memory_vec 那边的 rank 是按原始余弦排的，
        h["rank"] = i                      # 这里重排过，必须重编号，否则日志/自检会对不上）

    # 按 token 预算装（至少装 1 条：命中了就一定要给模型看到）
    used, lines, tok = [], [], 0
    for h in hits:
        line = format_memory_line(h["text"])
        if len(line) > 240:
            line = clip_line(line, 240)
        add = estimate_tokens(line) + 1
        if used and tok + add > max_tokens:
            break
        used.append(h)
        lines.append(line)
        tok += add

    text = "\n".join(lines)
    latency = (time.time() - t0) * 1000.0
    rid = "%d" % int(time.time() * 1000)
    if log:
        log_retrieval(rid, query, hits, used, tok, latency)
    return {"hits": hits, "used": used, "text": text, "tokens": tok,
            "latency_ms": round(latency, 2), "rid": rid,
            "vector_ms": round(vector_ms, 2), "rerank_ms": round(rerank_ms, 2),
            "rerank_note": rerank_note,
            "backend": embedder.backend()}


def _ask_brain(prompt):
    """问一次大脑（软依赖 xiaojiao_app）—— 拿不到就返回 None。

    为什么用软依赖而不是 `import xiaojiao_app`：`xiaojiao_app` 反过来 import 本模块，
    直接 import 会成环（启动即炸）。取不到就返回 None，让调用方**如实降级**，不是静默乱猜。

    【为什么先试带 max_tokens、再退回不带 —— 这一步是实测补上的】
      `xiaojiao_app.llm_chat(messages)` **不接受 `max_tokens` 参数**。
      第一版直接写 `fn(msgs, max_tokens=32)`，于是每次都抛 TypeError、被 except 吞掉、
      精排永远走"判官不可用，全部保留"这条降级路 —— **功能表面开着、实际一次都没生效**，
      而且没有任何报错。这类"静默失效"只能靠**真的调一次**才发现（自测里专门留了一条）。
    """
    try:
        import sys
        app = sys.modules.get("xiaojiao_app")
        fn = getattr(app, "llm_chat", None) if app is not None else None
        if fn is None:
            return None
        msgs = [{"role": "user", "content": prompt}]
        try:
            out = fn(msgs, max_tokens=32)
        except TypeError:
            out = fn(msgs)              # 老签名/别的实现：不接受 max_tokens
        return out if isinstance(out, str) else None
    except Exception:      # noqa: silent-ok — 判官挂了就放行全部候选，绝不因此丢记忆
        return None


# ===== 载体确定性闸门：**同一话题、不同地点**（实测抓到的真缺陷）=====
# 【实测症状】用户问「山东菏泽这周会下雨吗？」，库里存的是「江淮地区这两天会下雨吗？」。
#   小脑是字级模型，"地区 + 天气"这种**同类话题**实测余弦 0.954，
#   而真正跑题的「我上周去黄山玩了」只有 0.725 —— top1 领先 0.230 ≥ RERANK_GAP，
#   精排因此被判成"排名不含糊"直接跳过，"江淮降雨"被逐字注入 system：
#   模型等于拿**别的地方**的天气去回答菏泽的问题（用户原话："扯到江淮降雨"）。
# 【为什么这一层必须由载体做】"提问里的地点和记忆里的地点是不是同一个"是**确定性判断**，
#   字符串层面就能判；交给概率模型判反而引入不确定性、还要多花一次模型调用。
#   规则粗糙的代价是**漏检**（保守放行），不是误杀 —— 与 rerank 的失败路径同一条自律。
# 【为什么只做地点这一层】地点最容易"同类不同物"地混进来（江淮 ≠ 菏泽，但都属"某地"）；
#   反义（喜欢/讨厌）语义复杂、规则判不了，仍然交给大脑精排。
_PLACE_PROVINCES = frozenset((
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
))
_PLACE_REGIONS = frozenset((
    "江淮", "江南", "江浙", "华北", "华南", "华东", "华中", "华西", "东北", "西南",
    "西北", "中原", "黄淮", "长江", "黄河", "珠江", "淮河", "汉江", "长三角",
    "珠三角", "京津冀", "粤港澳", "川渝", "云贵", "岭南", "塞北", "关中", "胶东",
))
_PLACE_LEXICON = _PLACE_PROVINCES | _PLACE_REGIONS
# 行政后缀式：靠后缀回扫取词，抓"菏泽市/历下区"这类字面上没有词典条目的地名。
_PLACE_SUFFIXES = ("省", "市", "县", "区", "州", "盟", "旗", "地区", "流域", "一带",
                   "平原", "高原", "盆地", "半岛", "山脉", "群岛", "沙漠")
# 回扫取词时不许落在词首/词中的虚指字（"这个地区""哪个城市"里的"个/哪"不是地名）。
_PLACE_STOP_LEAD = set("这那哪某个的了是我你他她它们在到去来住从向和与及之其")


def _place_tokens(text):
    """载体自己认「地域词」：词典（省 / 大区 / 流域）+ 行政后缀式。纯规则，**不调模型**。

    只回答一件事："这段话里提到了哪个地方"。判不出来就返回**空集合** ——
    空集合表示"这次不做地域判断"（保守放行），绝不猜。
    """
    t = str(text or "")
    if not t:
        return set()
    out = {w for w in _PLACE_LEXICON if w in t}
    for sfx in _PLACE_SUFFIXES:
        for m in re.finditer(re.escape(sfx), t):
            for ln in (3, 2, 1):          # 往前取 1~3 个汉字当候选地名
                s = m.start() - ln
                if s < 0:
                    continue
                chunk = t[s:m.start()]
                if not chunk or not all("\u4e00" <= c <= "\u9fff" for c in chunk):
                    continue
                if any(c in _PLACE_STOP_LEAD for c in chunk):
                    continue
                out.add(chunk + sfx)
                break
    return out


# ===== 五类领域闸门（在地域闸门之后补的四类）=====
# 【为什么要把地域那套推广开】地域闸门治的是"问菏泽、答江淮"这一类病：
#   **同类话题、不同主体**。同样的病在另外四类上一样会犯：
#     · 人物：问"张三最近怎么样"，记忆里是"李四升职了" → 拿别人的事答
#     · 时间：问"今天天气"，记忆里是"去年今天下大雪" → 拿旧事当现状
#     · 单位：问"多少公里"，记忆里是"多少英里" → 数量级直接错
#     · 产品：问"iPhone 怎么设"，记忆里是"Android 怎么设" → 平台操作不同
#     · 事件：问"A 项目进度"，记忆里是"B 项目验收" → 张冠李戴
# 【判据不能非黑即白 —— 这是这套闸门唯一的设计难点】
#   用户明确要求的两条对照：
#     「张三最近怎么样」vs 记忆「李四升职了」        → 否决（只提到别人）
#     「张三最近怎么样」vs 记忆「张三和李四一起吃饭」 → 保留（提到了张三）
#   所以判据不是"有没有提到别的名字"，而是**"提问点的主体，这条记忆里有没有"**。
#   这与地域闸门里"记忆完全没提地名时不判冲突"是同一条自律：**宁可漏检，不可误杀**。
_TIME_TOKENS = ("今天", "今日", "昨天", "昨日", "明天", "明日", "后天", "前天",
                "本周", "这周", "上周", "上星期", "本周内", "本月", "这个月", "上个月",
                "今年", "去年", "前年", "明年", "去年今天", "去年同期", "刚才", "刚刚")
_UNIT_TOKENS = ("公里", "千米", "英里", "mile", "km", "米", "英尺", "foot", "feet",
                "厘米", "英寸", "inch", "斤", "公斤", "千克", "磅", "pound", "克",
                "摄氏度", "华氏度", "°c", "°f", "升", "毫升", "加仑", "字节", "kb", "mb", "gb", "tb")
_PRODUCT_TOKENS = ("iphone", "ipad", "mac", "macos", "ios", "android", "安卓", "windows",
                   "linux", "ubuntu", "华为", "鸿蒙", "harmonyos", "小米", "redmi", "三星",
                   "chrome", "edge", "firefox", "safari", "python", "node", "java", "微信", "qq")
# 人物：只认**带明确身份标记**的名字（低召回、高精度）。
# 不认识的裸名字一律不认 —— 中文人名与普通词的字面界限太模糊，
# 硬抽会把"项目/方案/会议"当成名字，那会比不过滤更糟。
_PERSON_MARK = ("先生", "女士", "老师", "同事", "朋友", "老板", "经理", "医生", "同学", "老婆", "老公", "儿子", "女儿")
_EVENT_QUOTED = re.compile(r"「([^」]{2,20})」|《([^》]{2,20})》|“([^”]{2,20})”")
_EVENT_NAMED = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12}?)(?:项目|计划|系统|平台|方案|工程)")


def _tokens_from(text, vocab):
    """闭词表命中（时间/单位/产品），并做**最长匹配优先**归一化。

    【为什么要归一化】"去年今天" 同时含 "去年今天" 与 "今天" 两个词条，
    不处理的话它在字面上就"包含今天"，于是「今天天气」和「去年今天下了大雪」
    会被判成同一个时间 —— 而这恰恰是要挡的那一类（拿旧事当现状）。
    归一化规则：**某个短词如果是某个已命中长词的子串，就把短词去掉**。
    """
    t = str(text or "").lower()
    hit = {w for w in vocab if w in t}
    drop = set()
    for a in hit:
        for b in hit:
            if a != b and a in b:
                drop.add(a)
    return hit - drop


def _person_tokens(text):
    """人物词。两条路：**带身份标记的**（张三先生）与**姓氏开头的两字名**（张三）。

    【为什么要走姓氏这条路】用户给的对照例子就是"张三 vs 李四"这种**裸名字**，
    只认"张三先生"的话那条主线判据根本不生效。
    【为什么要一份排除表】很多常用词就是姓氏开头："周末/白天/方向/方案/许多/任务"。
    不排除就会把普通词当人名，那比不过滤更糟（会把对的记忆误杀）。
    所以这一条是**低召回、高精度**：认得出常见姓名的两字写法，认不出的就不认
    —— 认不出只导致漏检（保守放行），认错会导致误杀。
    """
    t = str(text or "")
    out = set()
    for mk in _PERSON_MARK:
        for m in re.finditer(re.escape(mk), t):
            for ln in (3, 2, 1):
                s = m.start() - ln
                if s < 0:
                    continue
                chunk = t[s:m.start()]
                if chunk and all("\u4e00" <= c <= "\u9fff" for c in chunk):
                    out.add(chunk + mk)
                    break
    for i, ch in enumerate(t):
        if ch in _CN_SURNAMES and i + 1 < len(t):
            nxt = t[i + 1]
            if "\u4e00" <= nxt <= "\u9fff":
                name = ch + nxt
                if name not in _SURNAME_STOP:
                    out.add(name)
    return out


# 常见姓氏（百家姓节选）。只用于"姓氏 + 一个字"的两字名识别。
_CN_SURNAMES = set("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
                   "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗高林"
                   "郭何梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段漕钱汤尹黎易常武乔贺赖龚文")
# 姓氏开头但**不是人名**的常用词。命中即不作为人物词。
_SURNAME_STOP = {
    "周末", "白天", "方向", "方案", "方法", "方案", "许多", "许可", "任务", "任何",
    "金子", "金钱", "苏州", "马路", "马上", "毛病", "毛笔", "谢了", "谢谢", "哪里",
    "何时", "何等", "那么", "这个", "那个", "于是", "常常", "方式", "方才", "程度",
    "江水", "河水", "海外", "王者", "孔明", "孔子", "山西", "江西", "江苏", "广西",
    "何时", "汪洋", "白云", "石头", "夏天", "秋冬", "田地", "任何", "史书", "顾客",
    "侯爵", "孟子", "龙头", "万一", "段子", "钱币", "汤药", "易主", "常识", "武力",
    "乔迁", "贺电", "赖以", "龚断", "文化", "叶某", "阎王", "余下", "杜鹃",
}


def _event_tokens(text):
    """事件/项目名：引号里的名字，或"XX项目/计划/系统/平台"里的 XX。"""
    t = str(text or "")
    out = set()
    for m in _EVENT_QUOTED.finditer(t):
        g = m.group(1) or m.group(2) or m.group(3)
        if g:
            out.add(g.strip())
    for m in _EVENT_NAMED.finditer(t):
        out.add(m.group(0))
    return out


# 五类闸门的注册表：类别 → (抽词函数, 是否用"子串也算同一个"来比对)。
# · 地域式比对（子串算同一个）：人物（"张三"⊂"张三先生"）、事件（"星火"⊂"星火项目"）
# · **精确比对**：时间 / 单位 / 产品 —— 这几类是闭词表，"今天"绝不等于"去年今天"、
#   "公里"绝不等于"英里"，用子串比对会把要挡的东西放过去。
# 加一类新闸门只需在这里加一行 —— 判据复用同一套"层级 + 单向否决"逻辑。
_DOMAIN_GATES = (
    ("人物", _person_tokens, True),
    ("时间", lambda t: _tokens_from(t, _TIME_TOKENS), False),
    ("单位", lambda t: _tokens_from(t, _UNIT_TOKENS), False),
    ("产品", lambda t: _tokens_from(t, _PRODUCT_TOKENS), False),
    ("事件", _event_tokens, True),
)


def _domain_conflict(query, text):
    """五类领域闸门：提问点的主体，这条记忆里**一个都没有**、而它只提到了别的主体 → 冲突。

    返回冲突原因（空串 = 不冲突）。**只有在"提问侧与记忆侧都抽到了同类词、且完全不重合"
    时才判冲突** —— 任一侧抽不到就放行。这条自律贯穿五类：
    抽不到说明**载体判不了**，判不了就不能替用户扔掉记忆。

    与地域闸门的分工：地域单独有一条（`_place_conflict`，带层级判据），
    这里管另外四类 + 人物。两类都命中任一条即算冲突。
    """
    q = str(query or "")
    t = str(text or "")
    if not q or not t:
        return ""
    for name, extract, fuzzy in _DOMAIN_GATES:
        qt = extract(q)
        if not qt:
            continue                      # 提问侧没点这类主体 → 这类不参与判断
        mt = extract(t)
        if not mt:
            continue                      # 记忆侧没提这类 → 不判冲突（可能仍然有用）
        if fuzzy:
            if any(_same_place(a, b) for a in qt for b in mt):
                continue
        else:
            if qt & mt:
                continue
        return "%s闸门：提问点的是「%s」，而这条记忆只在说「%s」" % (
            name, "、".join(sorted(qt)), "、".join(sorted(mt)))
    return ""


def _same_place(a, b):
    """两个地名是不是"同一个地方"：完全相同，或一个是另一个的子串（山东 vs 山东菏泽）。"""
    return a == b or a in b or b in a


def _domain_reason(query, text):
    """统一闸门：地域 + 五类领域，任一命中即返回原因（空串 = 放行）。

    调用方（`rerank` 与载体侧的优率计算）都走这一个入口 ——
    两处各写一套判据早晚会不一致，而"检索判据两套"正是最难查的那种 bug。
    """
    try:
        qp = _place_tokens(query)
        if qp and _place_conflict(qp, text):
            return "地域闸门：提问点名的地方与这条记忆对不上"
    except Exception:      # noqa: silent-ok — 地域判不了就继续看领域那几类
        pass
    return _domain_conflict(query, text)


def _place_overlap(q_places, text):
    """这条记忆里有没有提到提问点名的地方。"""
    if not q_places:
        return False
    m = _place_tokens(text)
    return any(_same_place(a, b) for a in q_places for b in m)


# 市级及以下的行政后缀。判"层级"用：省级/大区级对不上才能断定"确实是别处"。
_PLACE_SUB_SUFFIXES = ("市", "县", "区", "州", "盟", "旗")


def _place_level(tok):
    """这个地名词属于哪一级：`省`（省级行政区）/ `区`（大区、流域）/ `市`（市级及以下）。

    **为什么要分层级**：一级地名对不上，不代表两个地方不同。
    `菏泽市` 属于 `山东`，但字面上既不相等也不互相包含 —— 没有地理库就判不出这层隶属关系。
    所以只有**两边都是省级或大区级**且对不上时，才能断定"问的是一处、说的是另一处"。
    """
    if tok in _PLACE_PROVINCES:
        return "省"
    if tok in _PLACE_REGIONS:
        return "区"
    for sfx in _PLACE_SUB_SUFFIXES:
        if tok.endswith(sfx):
            return "市"
    return "市"          # 后缀式抓到的词一律按市级及以下处理（保守）


def _place_conflict(q_places, text):
    """提问点了地名，这条记忆**只在说另一处地方** → 冲突，必须剔掉。

    两条保守规则，都是为了"宁可漏检，不可误杀"：

    ① 记忆里**完全没提地名**时不判冲突："我不吃辣"这种记忆对"北京哪家川菜好"仍然有用，
       不能因为它没提地名就丢掉。

    ② 记忆里提的是**市级及以下**地名时不判冲突。实测踩到过这个坑：问「山东菏泽这周会下雨吗？」
       时，`_place_tokens` 从提问里只认出 `山东`（"菏泽"没有行政后缀），
       而记忆「菏泽市明天有中雨」认出的是 `菏泽市` —— 两者字面对不上，
       第一版据此把这条**完全正确的记忆剔掉了**，反倒留下了认不出地名的
       「我上周去黄山玩了」。**丢掉对的、留下无关的**，比不过滤更坏。
       判不出隶属关系时就不剔，把"谁更相关"交回给大脑精排。
    """
    if not q_places:
        return False
    m = _place_tokens(text)
    if not m:
        return False
    if any(_same_place(a, b) for a in q_places for b in m):
        return False
    return any(_place_level(b) in ("省", "区") for b in m)


def rerank(query, hits, judge=None):
    """**载体二次判断**：让大脑从 top-K 里挑出真相关的，滤掉反义 / 无关。

    返回 `(保留的 hits, 说明)`。
    **失败分两种（2026-09-18 起不再笼统说"一律放行"）**：
      · **判官不可用**（`out` 为空：大脑没起 / 报错 / 返回空）→ **不全部放行**，
        改按 `THRESHOLD` 卡一道，但至少留 top1（见下 ⑤）；
      · **判官可用、只是判为「无」** → 仍**保守保留**（见下 ④）；
      · **本函数自己抛异常** → 最外层 `except` **全部放行**（这一条没改，如实标注）。

    ① 为什么要它：小脑是字级模型，"喜欢/讨厌"余弦 0.904（比近义还高），
      0.6 的阈值挡不住这种反义；把反义记忆注进 system，模型会拿反面去回答问题 ——
      比"没记住"更糟。小脑负责召回（宁可多召回），大脑负责精排（它读得懂语义）。
    ② 去掉会怎样：反义/无关记忆直接进 system。实测症状就是"答非所问，而且答得理直气壮"。
    ③ 为什么最多只问 `RERANK_MAX` 条：候选太多时小模型会挑花眼，且 prompt 变长拖慢每轮。
    ④ 为什么"返回无"也放行：小模型偶尔会矫枉过正把**全部**候选判成无关。
      真按它说的清空，用户会突然感觉"它什么都不记得了" —— 那种退化的观感比多注入一条更差。
    ⑤ 判官不可用时为什么改成"卡一道"（2026-09-18）：原来那条路是全部放行，
      只要大脑这条线不通（没装 / 没起 / 报错），噪音就全进 system ——
      实测日志「检索精排：3 条候选 → 判官不可用，全部保留」，游泳 / 周杰伦照样进去。
      **这不是载体替模型判断**：判官在的时候照常问大脑（它在判断，载体一个字不改）；
      判官不在时，载体手里只剩**已经算出来的分数**，按分数卡一道不叫替它判断，
      叫"没有判断可问的时候，不许把噪音当事实"。全部低于阈值也至少留 top1。
    """
    try:
        if not RERANK:
            return hits, "精排开关关闭，全部保留"
        n0 = len(hits)
        # ---- ① 载体确定性剔除：地域 + 五类领域（零成本，不调模型）----
        # 实测：这一层挡下的正是"问菏泽、答江淮"那条链（见上面 _place_tokens 的说明）；
        # 现在同一条链还覆盖 人物 / 时间 / 单位 / 产品 / 事件 五类（见 `_domain_conflict`）。
        q_places = _place_tokens(query)
        _rej = {}
        kept = []
        for h in hits:
            _why = _domain_reason(query, h.get("text"))
            if _why:
                _rej[id(h)] = _why
                continue
            kept.append(h)
        if len(kept) != len(hits):
            hits = kept
            if hits:
                import logging
                logging.getLogger("xiaojiao.retriever").info(
                    "载体确定性闸门：剔除 %d 条｜例：%s", len(_rej), list(_rej.values())[0][:80])
            if not hits:
                return hits, ("保留 0/%d 条（候选记忆提到的地点/主体与「%s」全都对不上 → "
                              "这次不注入：宁可说不知道，也不拿别处的事回答）"
                              % (n0, "、".join(sorted(q_places)) or query[:20]))
        # 提问点了地名时**不许跳过精排** —— "同类话题、不同主体"恰恰是 top1 领先的典型形状
        # （实测那条就是领先 0.230 仍被跳过）。所以地名词在时，多花一次判官调用是值得的。
        if len(hits) < RERANK_MIN_HITS and not (q_places and hits):
            return hits, "候选不足，跳过"
        # 排名不含糊就不精排（见 RERANK_GAP 的说明：这一条同时守住了"精度"和"检索延迟 <100ms"）
        # 注意：只在**候选 ≥ 2 条**时才算分差 —— 地域闸门可能只留下 1 条候选，
        # 那时 hits[1] 不存在（第一版就是在这里抛 IndexError，被下面的 except 吞成
        # "精排异常，全部保留" —— 又是"静默失效"，所以这里显式写清条数判据）。
        if len(hits) >= 2:
            gap = float(hits[0].get("decayed") or 0) - float(hits[1].get("decayed") or 0)
            if gap >= RERANK_GAP and not q_places:
                return hits, "top1 领先 %.3f，排名不含糊 → 跳过精排" % gap
        cand = hits[:RERANK_MAX]
        listing = "\n".join("%d. %s" % (i + 1, (h.get("text") or "").replace("\n", " ")[:120])
                            for i, h in enumerate(cand))
        prompt = _RERANK_PROMPT % ((query or "").replace("\n", " ")[:200], listing)
        out = judge(prompt) if judge is not None else _ask_brain(prompt)
        if not out:
            # 【2026-09-18 改】判官不可用时**不再全部放行**，改成按 THRESHOLD 卡一道。
            #   原来这里写 `return hits, "判官不可用，全部保留"` —— 只要大脑这条线不通
            #   （没装/没起/报错），噪音就全进 system。实测日志就是那句
            #   「检索精排：3 条候选 → 判官不可用，全部保留」，游泳/周杰伦照样进去。
            # ⚠️ 这是**降级策略，不是"载体替模型判断"**：
            #   判官在的时候照常问大脑（那是它在判断，载体一个字不改）；
            #   判官不在时，载体手里只剩**已经算出来的分数** —— 按分数卡一道不叫替它判断，
            #   叫"没有判断可问的时候，不许把噪音当事实"。
            # ⚠️ 全部低于阈值也**至少留 top1**：不能空手而归（那会让用户突然觉得
            #   "它什么都不记得了"，比多注入一条更糟 —— 和上面"返回无也放行"同一个顾虑）。
            if not hits:
                return hits, "判官不可用，且无候选"
            thr = float(THRESHOLD)

            def _s(h):
                # ⚠️ 生产链上的 hits 可能只有 `decayed`（时间衰减后的分）而没有 `score`，
                #    自测里给的是 `score`。两个都认，缺哪个都不许抛异常（抛了就整段降级）。
                _v = h.get("score")
                if _v is None:
                    _v = h.get("decayed")
                try:
                    return float(_v or 0)
                except Exception:      # noqa: silent-ok — 分数字段坏了当 0 处理
                    return 0.0

            _hi = [h for h in hits if _s(h) >= thr]
            if _hi:
                return _hi, ("判官不可用 → 按阈值 %.2f 卡一道：保留 %d/%d 条，"
                             "卡掉 %d 条（分数不到 %.2f 的不进 system）"
                             % (thr, len(_hi), len(hits), len(hits) - len(_hi), thr))
            return hits[:1], ("判官不可用 → 按阈值 %.2f 卡：%d 条**全部低于阈值**，"
                              "但保留 top1（%.2f）不空手而归；其余 %d 条卡掉"
                              % (thr, len(hits), _s(hits[0]), len(hits) - 1))
        picked = [int(x) for x in re.findall(r"\d+", out)]
        keep = [cand[i - 1] for i in picked if 1 <= i <= len(cand)]
        if not keep:
            # 提问点了地名、候选里**一条都没提到那个地方**时，判官的"全不相关"是对的 ——
            # 这时"保守保留"等于把别处的事硬塞给用户，所以尊重判官、如实不注入。
            # 其余情况仍保守保留（判官偶尔矫枉过正，不能让它把记忆清空）。
            if q_places and not any(_place_overlap(q_places, h.get("text")) for h in hits):
                return [], ("保留 0/%d 条（判官判为全不相关，且候选里没有一条提到「%s」→ "
                            "不注入）" % (len(hits), "、".join(sorted(q_places))))
            return hits, "判官判为全不相关 → 保守保留（不轻易清空记忆）"
        # 尾部没让判官看的那些（超过 RERANK_MAX 的）按原顺序接在后面
        keep = keep + [h for h in hits[len(cand):]]
        note = "保留 %d/%d 条" % (len(keep), len(hits))
        if len(hits) != n0:
            note += "（另 %d 条因地点对不上被剔掉）" % (n0 - len(hits))
        return keep, note
    except Exception:      # noqa: silent-ok — 精排是加分项，出错必须放行全部
        return hits, "精排异常，全部保留"



# ------------------------------------------------------------------ 日志
def log_retrieval(rid, query, hits, used, tokens, latency_ms, verdict=None):
    """如实记录一次检索：query / 检索条数 / 每条相似度 / 注入 token / （回填）模型是否使用。"""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        sims = ", ".join("%.3f" % h["score"] for h in hits) or "无"
        ids = ",".join(h["id"].split("-")[0] for h in used) or "无"
        line = ("[%s] rid=%s 后端=%s 检索=%d条 注入=%d条 相似度=[%s] 注入token=%d "
                "耗时=%.1fms 模型使用=%s ｜ query=%s | 注入id=%s"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), rid, embedder.backend(),
                   len(hits), len(used), sims, tokens, latency_ms,
                   "待定" if verdict is None else ("是" if verdict else "否"),
                   (query or "").replace("\n", " ")[:80], ids))
        with _LOG_LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:      # noqa: silent-ok — 记日志失败绝不能影响对话
        pass


def record_usage(rid, used, note=""):
    """对话结束后回填"模型到底有没有用上检索到的记忆"（写同一份日志，便于统计使用率）。"""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        line = ("[%s] rid=%s 回填 模型使用=%s %s"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), rid, "是" if used else "否", note[:60]))
        with _LOG_LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:      # noqa: silent-ok — 同上
        pass


def usage_rate(path=None):
    """从日志统计"使用率"：回填为"是"的比例。给自检和面板用。"""
    p = path or _LOG_PATH
    if not os.path.exists(p):
        return {"total": 0, "used": 0, "rate": 0.0}
    total = used = 0
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "回填 模型使用=" not in line:
                continue
            total += 1
            if "回填 模型使用=是" in line:
                used += 1
    return {"total": total, "used": used, "rate": (used / total) if total else 0.0}


def config(**kw):
    """覆盖策略参数（供操控文件热改）。返回生效后的参数。"""
    global TOP_K, THRESHOLD, MAX_TOKENS
    if kw.get("top_k"):
        TOP_K = max(1, int(kw["top_k"]))
    if kw.get("threshold") is not None:
        THRESHOLD = float(kw["threshold"])
    if kw.get("max_tokens"):
        MAX_TOKENS = max(100, int(kw["max_tokens"]))
    return {"top_k": TOP_K, "threshold": THRESHOLD, "max_tokens": MAX_TOKENS}
