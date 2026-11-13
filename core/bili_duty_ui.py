# -*- coding: utf-8 -*-
"""刷B站 · 设置模块的**后端**（给小焦的「刷B站」设置页用）。

总纲（与本项目所有模块一致）：
    小焦系统本身不依赖任何具体模型。它是完整的载体（器官齐全），模型是火种（可替换）。
    接入任何模型 → 系统活；换任何模型 → 系统不变。
    这就是"模型平等"和"变形金刚"的工程基础。

它在系统里的位置：
    `bili_duty/` 是 B站 插件的运行体（原先是 DSH 的插件，自带调度与配置）。
    小焦这边通过 `plugins/bili_duty.py` 拿到**能力**（14 个工具）；
    这个模块提供**界面要的数据与动作** —— 读配置、存配置、跑一轮、看刷过什么。
    两边共用同一份 `bili_duty/config.json` / `state.json` / `seen.md`，
    所以在哪边改都能生效，不会出现"小焦里改了、DSH 那边还是旧值"。

分寸（为什么"跑一轮"默认只读）：
    DSH 那边的调度会**真的去发评论**（它有自己的 `autoComment` 与人工确认）。
    小焦这边我**不擅自发**：一轮默认只做"搜 → 挑 → 拉评论 → 记账"，
    要发评论必须调用方显式传 `allow_comment=true`，而且仍受插件的 `_guard` 与
    `bili_reply` 的 `force` 双闸约束。发出去的话收不回来，这一层不能含糊。
"""
import json
import os
import subprocess
import time

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bili_duty")
_CFG = os.path.join(_DIR, "config.json")
_STATE = os.path.join(_DIR, "state.json")
_SEEN = os.path.join(_DIR, "seen.md")
_COMMENTS = os.path.join(_DIR, "comments.md")
_REPORTS = os.path.join(_DIR, "reports")
_COVER_CACHE = os.path.join(_DIR, ".cache", "covers")

# 允许从界面改的键（白名单）。**别的键一律不碰** —— 免得界面把 pythonPath 之类改坏。
_WRITABLE = ("perDay", "peakValley", "noon", "night", "autoComment",
             "keywords", "transcribe", "dmWatch", "fetchPerRound",
             "searchSort", "searchPages", "commentPerRound",
             # 偏好
             "workspace", "excludeWords", "personaPrompt",
             # 互动
             "replyToComments", "replyPerRound", "mentionName", "commentLength",
             "dmMode", "dmUid", "dmReplyPerDay",
             # 高级
             "manualMax", "manualCooldownMin", "manualCooldownSec", "runTimeoutMinutes", "reportLevel",
             "asrModel", "modelProvider", "modelName")


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else default
    except Exception:      # noqa: silent-ok — 文件坏了就按默认值走，不让界面 500
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # 原子写：写到一半崩了也不会留下半截配置


def cfg():
    return _load(_CFG, {})


def state():
    return _load(_STATE, {})


def _quota():
    """今天还剩多少额度（自动跑的口径）。"""
    st, c = state(), cfg()
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        return {"used": 0, "limit": int(c.get("perDay") or 0), "manual": 0}
    return {"used": int(st.get("count") or 0),
            "limit": int(c.get("perDay") or 0),
            "manual": int(st.get("manualExtra") or 0)}


def _rows_after(path, header_prefix):
    """从 md 里取某个小节下面的表格行（`| a | b |` 形式）。"""
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            inside = False
            for line in f:
                s = line.strip()
                if s.startswith("## "):
                    inside = s.startswith("## " + header_prefix) or header_prefix in s
                    continue
                if inside and s.startswith("|") and "---" not in s:
                    cells = [x.strip() for x in s.strip("|").split("|")]
                    if cells and cells[0] and cells[0] != "日期":
                        out.append(cells)
    except Exception:      # noqa: silent-ok — 没有记录就是空列表，界面显示"还没刷过"
        pass
    return out


def seen(limit=40):
    """看过的视频（seen.md 的「看过的视频」表，最新在后 → 取末尾 limit 条再倒序）。"""
    rows = _rows_after(_SEEN, "看过的视频")
    out = []
    for c in rows[-limit:]:
        out.append({"date": c[0] if len(c) > 0 else "",
                    "bvid": c[1] if len(c) > 1 else "",
                    "title": c[2] if len(c) > 2 else "",
                    "up": c[3] if len(c) > 3 else "",
                    "kind": c[4] if len(c) > 4 else ""})
    out.reverse()
    return out


def my_comments(limit=30):
    """自己发过的评论（comments.md 每行一条）。"""
    out = []
    try:
        with open(_COMMENTS, encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s.startswith("- "):
                    continue
                out.append(s[2:][:300])
    except Exception:      # noqa: silent-ok — 同上
        pass
    out.reverse()
    return out[:limit]


def _parse_report(path):
    """解析 `reports/BVxxxx-日期.md` → 卡片数据（标题/UP/档位/为什么/我的一句话/已评论）。

    为什么从报告里读而不是重算：这些判断是**当时那轮**做的（哪个档位、为什么值得），
    重算会得出"现在的看法"，跟用户看到的记录不一致。记录就该是记录。
    """
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:      # noqa: silent-ok — 读不了就跳过这张卡
        return None
    import re
    d = {"bvid": "", "title": "", "up": "", "play": "", "verdict": "",
         "why": "", "mine": "", "commented": False, "date": "", "file": os.path.basename(path)}
    m = re.search(r"#\s*(BV[0-9A-Za-z]+)", text)
    if m:
        d["bvid"] = m.group(1)
    m = re.search(r"标题：(.+)", text)
    if m:
        # 有的报告把 UP/链接 写在同一行（"标题：xxx｜UP：yyy｜链接：zzz"），
        # 直接取整行会把它们塞进标题里 —— 在第一个分隔符处截断。
        t = re.split(r"\s*[｜|]\s*(?:UP|链接|播放)| / UP：", m.group(1))[0]
        d["title"] = t.strip()
    m = re.search(r"UP：(.+?)\s*｜\s*播放：(.+?)\s*｜", text)
    if m:
        d["up"], d["play"] = m.group(1).strip(), m.group(2).strip()
    if not d["up"]:
        # 有些报告把 UP 写在标题那一行（"标题：…｜UP：yyy｜链接：…"），
        # 上面按分隔符截断标题时会把 UP 一起切掉 —— 这里单独捞回来。
        m = re.search(r"UP：([^｜|\n]+)", text)
        if m:
            d["up"] = m.group(1).strip()
    m = re.search(r"\*\*档位\*\*：(.+)", text)
    if m:
        d["verdict"] = m.group(1).strip()
    m = re.search(r"为什么值得[：:](.+)", text)
    if m:
        d["why"] = m.group(1).strip()
    m = re.search(r"我的一句话[：:](.+)", text)
    if m:
        d["mine"] = m.group(1).strip()
    d["commented"] = "已评论" in text
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    if m:
        d["date"] = m.group(1)
    return d if d["bvid"] else None


def gallery(filter_by="", limit=60):
    """刷过的视频（`reports/*.md` 的档位判断 + `seen.md` 的全部观看记录）。

    为什么必须**两边合并**（用户实测反馈："看了的视频没有实时记录同步"）：
      · `reports/` 只有"当时写了笔记的那几条"（14 条），是 DSH 侧挑出来细看的；
      · 真正记账的是 `seen.md`（219 条），我们每一轮新看的都追加在那里。
      只读 reports 就会让用户觉得"我刚看的根本没进画廊"。
    合并规则：以 bvid 为键，seen.md 提供**全部**记录，reports 提供**档位/理由**；
    没有报告的记 `未判定`（如实标注"没细看"，不编档位）。
    """
    # ① 先把 reports 的判定读进来
    by_bv = {}
    try:
        names = [f for f in os.listdir(_REPORTS) if f.endswith(".md")]
    except Exception:      # noqa: silent-ok — 没有报告目录就是"还没细看过"
        names = []
    for fn in names:
        c = _parse_report(os.path.join(_REPORTS, fn))
        if c:
            by_bv[c["bvid"]] = c

    # ② 再把 seen.md 的全部观看记录读进来
    # 去重：同一个 BV 可能被不同轮次都记过（判重表是追加的），以**最后一条**为准；
    # 顺序用"文件里越靠后=看得越晚"，再整体倒序 —— 这样最新看的排最前，
    # 而不是按 BV 字符串排（那看着像乱序，用户会以为没同步）。
    merged = {}
    order = []
    for row in _rows_after(_SEEN, "看过的视频"):
        bv = row[1] if len(row) > 1 else ""
        if not bv.startswith("BV"):
            continue
        base = by_bv.get(bv) or {}
        if bv not in merged:
            order.append(bv)
        merged[bv] = {
            "bvid": bv,
            "date": row[0] if row else "",
            "title": base.get("title") or (row[2] if len(row) > 2 else bv),
            "up": base.get("up") or (row[3] if len(row) > 3 else ""),
            "verdict": base.get("verdict") or "",
            "why": base.get("why") or "",
            "mine": base.get("mine") or "",
            "commented": bool(base.get("commented")),
            "kind": row[4] if len(row) > 4 else "",
        }
    cards = [merged[bv] for bv in reversed(order)]
    # reports 里有、seen.md 里没有的（DSH 侧记过但没进判重表的）也补上
    for bv, c in by_bv.items():
        if bv not in merged:
            cards.append(c)

    cards.sort(key=lambda x: (x.get("date") or "", x.get("bvid") or ""), reverse=True)
    if filter_by and filter_by not in ("全部", "all"):
        if filter_by == "发给你了":
            cards = [c for c in cards if c.get("commented")]
        elif filter_by == "挑了":
            cards = [c for c in cards if c.get("verdict")]
        elif filter_by == "跳过":
            cards = [c for c in cards if not c.get("verdict")]
        elif filter_by == "未判定":
            cards = [c for c in cards if not c.get("verdict")]
        else:
            cards = [c for c in cards if c.get("verdict") == filter_by]
    return cards[:limit]


def cover_bytes(bvid):
    """取封面图字节（磁盘缓存优先）。返回 (bytes, mime) 或 (None, 原因)。

    为什么一定要缓存：每取一次都要起一个插件子进程 + 下载图片，1~3 秒。
    画廊一次要十几张，不缓存就是十几秒白屏。
    """
    import base64
    import json as _json
    import os as _os
    import re
    if not re.fullmatch(r"BV[0-9A-Za-z]{8,12}", bvid or ""):
        return None, "BV号格式不对"
    try:
        _os.makedirs(_COVER_CACHE, exist_ok=True)
    except Exception:      # noqa: silent-ok — 建不了缓存目录就退化成不缓存
        pass
    for ext in ("jpg", "png", "webp"):
        p = _os.path.join(_COVER_CACHE, bvid + "." + ext)
        if _os.path.isfile(p):
            try:
                return open(p, "rb").read(), "image/" + ("jpeg" if ext == "jpg" else ext)
            except Exception:      # noqa: silent-ok — 缓存坏了就重新取
                pass
    # 通过桥接直接调 bili_cover（**不走 run_tool**：要拿图片 base64，不是文字）
    py = None
    try:
        c = cfg()
        py = c.get("pythonPath")
    except Exception:      # noqa: silent-ok
        pass
    if not py or not _os.path.isfile(py):
        py = _os.path.join(_DIR, ".venv", "Scripts", "python.exe")
    bridge = _os.path.join(_DIR, "xj_bridge.py")
    try:
        r = subprocess.run([py, bridge], input=json.dumps(
            {"tool": "bili_cover", "args": {"bvid": bvid}}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_DIR, timeout=120)
        out = (r.stdout or b"").decode("utf-8", "ignore").strip().splitlines()
        d = _json.loads(out[-1]) if out else {}
    except Exception as e:      # noqa: BLE001 — 取不到封面要如实说，不能让界面猜
        return None, "取封面失败：%s" % str(e)[:100]
    if not d.get("ok"):
        return None, str(d.get("error") or "取封面失败")[:120]
    imgs = d.get("images") or []
    if not imgs:
        return None, "这个视频没有封面"
    raw = base64.b64decode(imgs[0]["data"])
    mime = imgs[0].get("mime") or "image/jpeg"
    ext = "png" if "png" in mime else ("webp" if "webp" in mime else "jpg")
    try:
        with open(_os.path.join(_COVER_CACHE, bvid + "." + ext), "wb") as f:
            f.write(raw)
    except Exception:      # noqa: silent-ok — 缓存失败不影响这次返回
        pass
    return raw, mime


def _tools():
    """拿小焦的工具调度（真调用插件，不另写一套 B站 客户端）。"""
    import xiaojiao_app as X
    X._build_tools()                 # 确保路由表已建（否则 run_tool 会说"未知工具"）
    return X


def _bvid_of(row):
    return (row or {}).get("bvid") or ""


def _cooldown_sec(c):
    """手动跑的冷却时长（秒）。**秒级优先，分钟级兜底**。

    为什么要多一个 `manualCooldownSec`：原来的 `manualCooldownMin` 是"分钟"，
    最小 1 分钟 —— 调脚本、调界面时等 1 分钟太久了，而把它设成 0 又等于"关掉冷却"，
    测不了"冷却到底生效没有"。所以加一个秒级字段：
      · 设了 `manualCooldownSec` → 用它（可以是 10 秒这种短值）
      · 没设 → 退回 `manualCooldownMin × 60`（**DSH 那边照旧读分钟，不受影响**）
    两边共用一份 config，但各读各的精度，谁也不用迁就谁。
    """
    try:
        sec = int(float(c.get("manualCooldownSec") or 0))
    except Exception:      # noqa: silent-ok — 值写坏了就退回分钟
        sec = 0
    if sec > 0:
        return sec
    try:
        return int(float(c.get("manualCooldownMin") or 0) * 60)
    except Exception:      # noqa: silent-ok
        return 0


def _compose_comment(v, c, sub=None):
    """写一条要发出去的评论：`@某人 + 正文`。

    【必须基于**真的看过内容**】用户原话："他是否真正把视频看完然后再去发信息，
    要不他直接发都与视频无关了"。
    实测（修前）：这里只把**标题**给了模型（`视频标题：%s`），字幕一个字没喂 ——
    所以评论必然跟内容无关。
    现在：有字幕就喂字幕（前 1200 字）并要求"必须提到视频里的具体内容"；
    **没有字幕/字幕太短 → 返回空串，这一条不评论**（宁可不说，也不发无关的话）。

    为什么要自己拼 @：`bili_reply` 只负责发，不会替你 @ 人；
    而插件配置里 `mentionName` 就是"@ 谁"。所以发出去的正文必须自带 `@名字 `。
    """
    name = str(c.get("mentionName") or "").strip()
    prefix = ("@" + name + " ") if name else ""
    length = str(c.get("commentLength") or "medium")
    want = {"short": "一句话，15 字以内", "long": "三四句，80 字左右"}.get(length, "两句话，40 字以内")
    body = ""
    try:
        X = _tools()
        persona = str(c.get("personaPrompt") or "").strip()[:1200]
        title = str(v.get("title") or "")[:80]
        # 【没内容就不评论】视频没看完/没字幕 → 不发与内容无关的话
        _sub = str(sub or "").strip()
        if len(_sub) < 200:
            LOG.info("写评论跳过：没有可用字幕（%d 字），不发与内容无关的评论", len(_sub))
            return ""
        sys_p = (persona or "你是个说话口语、短句、带点小脾气的观众。")
        sys_p += ("\n\n现在给这条 B站 视频写一条评论。要求：%s；像人随口说的，"
                  "不要 markdown、不要引号、不要解释你在做什么。只输出评论本身。\n"
                  "**必须落在视频讲的具体内容上**（提到他说的事/观点/例子），"
                  "不许写成放之四海皆准的空话。" % want)
        out = X.llm_chat([{"role": "system", "content": sys_p},
                          {"role": "user", "content":
                           "视频标题：%s\n\n视频里实际说了什么（字幕节选）：\n%s"
                           % (title, _sub[:1200])}])
        body = str(out or "").strip().strip('"“”')
        body = body.split("\n")[0][:120]
    except Exception as e:      # noqa: silent-ok — 模型不可用也不能编一句无关的话发出去
        LOG.info("[刷B站] 写评论失败：%s", str(e)[:80])
    if not body:
        # 【不许兜底成空话】"看完了，学到了。" 这类跟内容无关的句子正是用户抱怨的
        #   "直接发都与视频无关"。写不出来就**不发**，让 _run 那边记 skip。
        return ""
    return (prefix + body).strip()


def _bili_learn(v, subtitle, c):
    """把一条视频**学成一条认知**，存进小焦的精神记忆库。返回 (存了?, 说明)。

    用户的原话是"让他通过这一层去 B站 学习，越用越厉害" ——
    所以光"看过并记账"不够：看过的内容必须变成**以后能回想起来、能影响回答**的东西。
    这里复用载体已有的学习闭环（`core/spirit_memory.remember`），**不另造一套**：
      · 用模型把字幕提炼成一句陈述（knowledge/method 二选一），不是把字幕整段塞进去；
      · 由 `remember()` 的护栏把关（超长、问答对、带答案字段一律拒收），
        再加 `_looks_like_self_claim` 这一道（"小焦自己会不会"的断言不学）；
      · 同题会判 `duplicate` 而**不重复写** —— 越刷越干净，不会把库刷肿。
    """
    if not subtitle or len(str(subtitle)) < 200:
        return False, "没有字幕（跳过学习）"
    try:
        import sys as _sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        import xiaojiao_app as X
        from core import spirit_memory as _sm
        title = str(v.get("title") or "")[:80]
        prompt = (
            "下面是一条 B站 视频的标题和字幕节选。请提炼**一条可复用的认知**。\n"
            "只输出两行：第一行类别（knowledge / method 二选一），第二行一句话（≤60 字，陈述句）。\n"
            "knowledge = 关于某个工具/技术/事实的可靠认知\n"
            "method = 做某类事的具体做法或判据\n"
            "要求：只写**字幕里真的支持的**内容；不要写'这个视频讲了…'；不要写标题党结论；\n"
            "字幕信息不足就只输出 none。\n\n"
            "标题：%s\n字幕：%s\n" % (title, str(subtitle)[:1800]))
        out = str(X._selfrate_llm(prompt) or "").strip()
        if not out or out.lower().startswith("none"):
            return False, "模型判断没有可学的东西"
        lines = [x.strip() for x in out.split("\n") if x.strip()]
        kind = lines[0].lower().strip(":： ") if lines else ""
        text = lines[1] if len(lines) > 1 else ""
        if kind not in ("knowledge", "method") or not text:
            return False, "提炼结果不合格式"
        if X._looks_like_self_claim(text):
            return False, "这条在讲小焦自己，按规矩不学"
        r = _sm.remember(kind, text, source="B站 %s" % (v.get("bvid") or ""),
                         llm_judge=X._spirit_judge)
        return (r.get("action") == "added"), "%s → %s" % (text[:40], r.get("action"))
    except Exception as e:      # noqa: BLE001 — 学不会不影响这一轮刷视频
        return False, "学习失败：%s" % str(e)[:80]


def _transcribe(bv, c, wait_sec=240):
    """没字幕时用插件自带的本地 ASR 转一份。返回 (文本, 说明)。

    为什么**直接跑 `asr.py`**，而不是走插件的 `bili_transcribe_start`：
      实测插件那个 MCP 工具会**阻塞 20 分钟还不返回**（它内部还要串一遍别的东西），
      整条后台队列会被一条视频卡死。而 `asr.py` 自己单跑，
      实测 1 分 13 秒的视频 **75 秒出结果**、逻辑清晰、失败也说得明白。
      能力是同一个（同一份 faster-whisper + 同一个模型缓存），只是换了更短的调用路径。
    """
    if not c.get("transcribe"):
        return "", "设置里没开本地转写"
    import glob
    import tempfile
    py = os.path.join(_DIR, ".venv-asr", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = os.path.join(_DIR, ".venv-asr", "bin", "python")
    script = os.path.join(_DIR, "asr.py")
    if not os.path.isfile(py) or not os.path.isfile(script):
        return "", "找不到 asr 环境或 asr.py"
    out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="xj_asr_")
    os.close(out_fd)
    try:
        r = subprocess.run([py, script, bv, "-o", out_path,
                            "--model", str(c.get("asrModel") or "small")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=_DIR, timeout=wait_sec)
        txt = ""
        if os.path.isfile(out_path):
            txt = open(out_path, encoding="utf-8", errors="ignore").read().strip()
        if not txt:
            err = (r.stderr or b"").decode("utf-8", "ignore")[-160:]
            return "", "转写没有产出（rc=%s）%s" % (r.returncode, err)
        # 只剩署名/水印（如"字幕by索兰娅"）说明这条没语音，不算学到东西
        if len(txt) < 60:
            return "", "转写只拿到 %d 字（疑似没有语音）：%s" % (len(txt), txt[:40])
        return txt, "转写完成（%d 字）" % len(txt)
    except subprocess.TimeoutExpired:
        return "", "转写超过 %d 秒没完成" % wait_sec
    except Exception as e:      # noqa: BLE001 — 转写出错只影响这一条
        return "", "转写异常：%s" % str(e)[:100]
    finally:
        try:
            os.remove(out_path)
        except Exception:      # noqa: silent-ok — 临时文件删不掉不影响结果
            pass


_TQ = os.path.join(_DIR, ".cache", "transcribe_queue.json")


def _tq_load():
    try:
        return json.load(open(_TQ, encoding="utf-8")) or []
    except Exception:      # noqa: silent-ok — 队列文件没有/坏了就是空队列
        return []


def _tq_save(q):
    try:
        os.makedirs(os.path.dirname(_TQ), exist_ok=True)
        with open(_TQ, "w", encoding="utf-8") as f:
            json.dump(q[-200:], f, ensure_ascii=False)
    except Exception:      # noqa: silent-ok — 存不下就丢这一批，不影响主流程
        pass


def enqueue_transcribe(bvid, title=""):
    """把"这条没字幕、需要本地转写"排进后台队列（**不阻塞这一轮**）。

    为什么必须异步（实测）：`bili_transcribe_start` 自己要**下载视频 + 抽音轨**，
    实测 180 秒还没返回。把它放进"跑一轮"里同步等，整轮就会被一条视频卡死 ——
    而用户要的是"开开转写"，不是"每条都等五分钟"。所以拆成：
      这一轮只负责**登记**；后台一个线程慢慢转，转完再学。
    """
    q = _tq_load()
    if any(x.get("bvid") == bvid for x in q):
        return False
    q.append({"bvid": bvid, "title": str(title)[:60], "at": time.time()})
    _tq_save(q)
    return True


_worker = None


def start_transcribe_worker(interval=30):
    """后台转写线程：一次一条，转完 → 提炼 → 存记忆。"""
    global _worker
    if _worker is not None and _worker.is_alive():
        return False
    import threading

    def _loop():
        import time as _t
        while True:
            try:
                q = _tq_load()
                if q:
                    item = q[0]
                    # 把这条先摘出来（避免重复处理），失败也不再重排（转写成本高）
                    _tq_save(q[1:])
                    bv = item.get("bvid") or ""
                    c = cfg()
                    tx, why = _transcribe(bv, c, wait_sec=420)
                    if tx:
                        _bili_learn({"bvid": bv, "title": item.get("title") or ""}, tx, c)
                        try:
                            import logging
                            logging.getLogger(__name__).info(
                                "[刷B站] 转写完成并学习：%s（%d 字）", bv, len(tx))
                        except Exception:      # noqa: silent-ok
                            pass
                    else:
                        try:
                            import logging
                            logging.getLogger(__name__).info(
                                "[刷B站] 转写没成：%s → %s", bv, why)
                        except Exception:      # noqa: silent-ok
                            pass
            except Exception:      # noqa: silent-ok — 后台线程绝不能死
                pass
            _t.sleep(int(interval))

    _worker = threading.Thread(target=_loop, name="bili-asr", daemon=True)
    _worker.start()
    return True


def run_round(allow_comment=None, budget=None, manual=True):
    """跑一轮：搜 → 挑 → 拉评论 →（按配置）发评论 → 记账。

    **发不发评论由 `autoComment` 决定**（`allow_comment=None` 时读配置）；
    显式传 True/False 则覆盖本次。
        · 配置里"自动发评论"开着 → 手动跑和自动跑都会发
        · 关着 → 默认只读；只有本次显式勾了"强制发评论"才发
    这一点以前做错了：`autoComment` 只显示在界面上，**执行路径根本没读它**，
    于是用户勾着"自动发评论"却永远只读到"只读（不发评论）"。
    发出去的评论必须 @ 到 `mentionName`（否则用户收不到通知，等于白发）。
    """
    c = cfg()
    if allow_comment is None:
        allow_comment = bool(c.get("autoComment"))
    st = state()
    if manual:
        cool = _cooldown_sec(c)
        last_manual = int(st.get("lastManualAt") or 0)
        if cool and last_manual:
            left = cool - int((time.time() * 1000 - last_manual) / 1000)
            if left > 0:
                return {"ok": False,
                        "error": "刚跑过，冷却中：还要等 %d 秒（设置里的「手动跑冷却」= %d 秒）"
                                 % (left, cool)}

    X = _tools()
    n_fetch = int(budget or c.get("fetchPerRound") or 8)
    kws = [k for k in (c.get("keywords") or []) if str(k).strip()]
    picked = []

    if kws:
        for kw in kws[:3]:
            raw = str(X.run_tool("bili_search", {"keyword": kw, "num": max(3, n_fetch),
                                                 "order": c.get("searchSort") or "totalrank"},
                                 force=True))
            picked += _parse_search(raw)
    else:
        # 【为什么不用"热门"兜底】实测：没有关键词时拉 `bili_hot_videos`，
        # 会混进大量情感/擦边内容（用户当场就看到了）。排行榜是编辑口径，干净得多。
        raw = str(X.run_tool("bili_rank", {"category": "all", "day": 3}, force=True))
        picked += _parse_search(raw)

    # ---- 排除词：标题/UP 里出现就整条跳过（插件 config 里本来就有这个字段，之前没接）----
    ex = [str(w).strip().lower() for w in (c.get("excludeWords") or []) if str(w).strip()]
    if ex:
        before = len(picked)
        kept = []
        for p in picked:
            hay = (str(p.get("title") or "") + " " + str(p.get("author") or p.get("up") or "")).lower()
            if any(w in hay for w in ex):
                continue
            kept.append(p)
        picked = kept
        _dropped_ex = before - len(picked)
    else:
        _dropped_ex = 0

    seen_bv = {r.get("bvid") for r in seen(200)}
    picked = [p for p in picked if _bvid_of(p) and _bvid_of(p) not in seen_bv][:n_fetch]

    # 【判重表会把老视频全挡掉 → 再按"最新"捞一次】
    # 实测：关键词搜回来的前几条永远是那几条，判重之后一条不剩，于是"跑了但没东西"。
    # 所以第一轮全被挡住时，换 `pubdate`（最新）再搜一遍 —— 那才是"新刷出来的"。
    if not picked and kws:
        for kw in kws[:2]:
            raw = str(X.run_tool("bili_search", {"keyword": kw, "num": max(10, n_fetch * 3),
                                                 "order": "pubdate"}, force=True))
            cand = _parse_search(raw)
            fresh = [p for p in cand if _bvid_of(p) and _bvid_of(p) not in seen_bv]
            if fresh:
                picked = fresh[:n_fetch]
                break

    if not picked:
        return {"ok": False, "error": "这一轮没挑到新视频（可能都被刷过了，或搜索没返回结果）"}

    today = time.strftime("%Y-%m-%d")
    lines, comments = [], []
    posted = 0
    learned_n = 0
    for v in picked:
        bv = _bvid_of(v)
        title = (v.get("title") or "")[:60].replace("|", "／")
        up = (v.get("author") or v.get("up") or "")[:20].replace("|", "／")
        cm = str(X.run_tool("bili_comments", {"bvid": bv, "num": 20}, force=True))
        got = _count_comments(cm)
        lines.append("| %s | %s | %s | %s | 评论 |" % (today, bv, title, up))
        item = {"bvid": bv, "title": title, "n": got}
        # ---- 学习：字幕 → 一条认知 → 精神记忆库（"越刷越厉害"靠的就是这一步）----
        try:
            sub = str(X.run_tool("bili_subtitle", {"bvid": bv}, force=True))
        except Exception:      # noqa: silent-ok — 没字幕就不学，不影响这一轮
            sub = ""
        lazy = (not sub) or len(sub) < 200 or ("没有" in sub[:40] and len(sub) < 400)
        if lazy:
            # 没字幕/字幕太短 → **只登记到后台队列**，不在这一轮里等转写
            if enqueue_transcribe(bv, title):
                item["queued_asr"] = True
            else:
                item["queued_asr"] = "已在队列里"
        learned, why_learn = _bili_learn({"bvid": bv, "title": title}, sub, c)
        item["learned"] = bool(learned)
        item["learn_why"] = why_learn
        if learned:
            learned_n += 1
        if allow_comment:
            # 只有**明确要求**时才写；force 闸仍在插件里守着（双闸）
            text = _compose_comment(v, c, sub)
            if not text:
                # 没看过内容 → 不评论（用户要求：不许发与视频无关的话）
                item["skipped_comment"] = "没有可用字幕，未评论"
                continue
            r = str(X.run_tool("bili_reply", {"bvid": bv, "text": text, "force": True}, force=True))
            ok = ("rpid" in r) or ("成功" in r) or ("ok" in r.lower())
            item["commented"] = bool(ok)
            item["comment"] = text
            item["reply_res"] = r[:160]
            if ok:
                posted += 1
                try:
                    with open(_COMMENTS, "a", encoding="utf-8") as f:
                        f.write("- %s %s %s\n" % (time.strftime("%Y-%m-%d %H:%M"), bv, text[:120]))
                except Exception:      # noqa: silent-ok — 记不上不影响已经发出去的评论
                    pass
        comments.append(item)

    # 追加到 seen.md（**只追加，不改已有内容**）
    try:
        with open(_SEEN, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(lines) + "\n")
    except Exception as e:      # noqa: BLE001 — 记不上账要如实说，不能假装刷过
        return {"ok": False, "error": "记录写到 seen.md 失败：%s" % str(e)[:120]}

    # 更新 state.json
    if st.get("day") != today:
        st = {"day": today, "count": 0, "manualExtra": 0, "windows": {}, "windowTries": {}}
    if manual:
        st["manualExtra"] = int(st.get("manualExtra") or 0) + len(picked)
    st["picked"] = int(st.get("picked") or 0) + len(picked)
    st["lastRunAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if manual:
        st["lastManualAt"] = int(time.time() * 1000)
    st["lastWhy"] = "manual" if manual else "auto"
    _save(_STATE, st)

    mname = str(c.get("mentionName") or "").strip()
    if allow_comment:
        note = "发了 %d 条评论%s" % (posted, ("（已 @%s）" % mname) if mname else "")
    else:
        note = "只读（没发评论）：设置里「自动发评论」是关的"
    note += "；学了 %d 条" % learned_n
    # 【把这一轮看的写进 reports/】不写的话画廊永远显示「未判定」
    try:
        for _it in (comments or [])[:8]:
            _v = next((x for x in picked if x.get("bvid") == _it.get("bvid")), {})
            _write_report({"bvid": _it.get("bvid"), "title": _it.get("title"),
                           "author": _v.get("author") or _v.get("up"),
                           "play": _v.get("play")},
                          verdict=("可以看" if _it.get("learned") else "未判定"),
                          why=(_it.get("learn_why") or "—"),
                          mine="", commented=bool(_it.get("n")))
    except Exception as _we:      # noqa: silent-ok
        LOG.info("[刷B站] 批量写报告失败：%s", str(_we)[:80])
    # 【看完要给你报告】用户原话："他看完视频也不私信向我报告"。
    #   实测：小焦这边**从来没有**发私信的代码（原版 DSH 插件的值班循环没搬过来），
    #   而且 dmUid 是 0 —— 没人可发。这里补上：配了 dmUid 就发一条汇总私信。
    try:
        _dm = _dm_report(comments, c)
        if _dm:
            note += "；" + _dm
    except Exception as _de:      # noqa: silent-ok — 私信失败不影响这一轮
        LOG.info("[刷B站] 私信报告失败：%s", str(_de)[:80])
    return {"ok": True, "picked": len(picked), "commented": posted, "learned": learned_n,
            "items": comments,
            "filtered": _dropped_ex, "note": note
                    + ("；排除词挡掉 %d 条" % _dropped_ex if _dropped_ex else "")}


def _parse_search(raw):
    """把 bili_search 的 JSON 文本解析成 [{bvid,title,author,play}]。解析不了就空。"""
    try:
        d = json.loads(raw)
        return d.get("videos") or d.get("items") or []
    except Exception:
        # 插件有时会返回带前缀的失败文本 —— 用一个宽松的正则兜一下 BV 号
        import re
        out = []
        for m in re.finditer(r"(BV[0-9A-Za-z]{10})", raw or ""):
            out.append({"bvid": m.group(1)})
        return out


def _parse_hot(raw):
    try:
        d = json.loads(raw)
        return d.get("videos") or d.get("items") or []
    except Exception:
        return _parse_search(raw)


def _count_comments(raw):
    try:
        d = json.loads(raw)
        for k in ("comments", "data", "replies", "items"):
            v = d.get(k)
            if isinstance(v, list):
                return len(v)
        return 0
    except Exception:
        return 0


def _control():
    """小焦的主控制文件（models[] / 当前 brain.api.model 都在这）。"""
    p = os.path.join(os.path.dirname(_DIR), "xiaojiao_control.json")
    return _load(p, {})


def choices():
    """界面下拉用的清单：**只列小焦这边真能调用的**。

    为什么必须有这个（用户实测反馈）：`modelProvider` / `modelName` 原来是两个自由输入框，
    得手打模型名 —— 打错一个字就是"这一轮跑了但模型不对"，而且用户根本不知道有哪些可填。
    下拉的意义不是好看，是**让可选范围等于真实范围**。
    数据来源：`xiaojiao_control.json` 的 `models[]`（和 设置→模型 那一页同源），
    所以那边加了模型，这里立刻就有。
    """
    ctl = _control()
    models, seen = [], set()
    for m in (ctl.get("models") or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("model") or m.get("name") or "").strip()
        name = str(m.get("name") or mid).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append({"id": mid, "name": name, "engine": str(m.get("engine") or "")})
    here = str(((ctl.get("brain") or {}).get("api") or {}).get("model") or "")
    return {
        "models": models,
        "current": here,
        # provider 下拉：空 = 跟随；其余按 models[] 的模型名（它们就是小焦的"火种"）
        "providers": models,
    }


def status():
    """界面用的完整状态。**每一项都有真实来源**，不编。"""
    c, st = cfg(), state()
    q = _quota()
    # 登录状态：真调一次（有缓存则很快）
    login = {"ok": False, "text": "未检查"}
    try:
        X = _tools()
        r = str(X.run_tool("bili_login_check", {}, force=True))
        login = {"ok": "已登录" in r or "already_logged_in" in r, "text": r[:120]}
    except Exception as e:      # noqa: silent-ok — 查不到就如实说查不到
        login = {"ok": False, "text": "检查失败：%s" % str(e)[:80]}
    last = st.get("lastRunAt") or ""
    return {
        "ok": True,
        "login": login,
        "quota": q,
        "last_run": last,
        "last_why": st.get("lastWhy") or "",
        "today": time.strftime("%Y-%m-%d"),
        "cfg": {k: c.get(k) for k in _WRITABLE},
        "choices": choices(),
        "auto": auto_state(),
        "dict": {
            "perDay": "每天最多刷几条（0 = 不自动刷，仍可手动跑）",
            "peakValley": "只在谷时刷（12:00-14:00 与 18:00 之后）",
            "noon": "中午谷时 12:00-14:00",
            "night": "晚上/夜间谷时 18:00-09:00",
            "autoComment": "刷的时候自动发评论（DSH 侧执行；小焦手动跑默认不发）",
        },
        "duty_running": _running(),
    }


def _running():
    """DSH 那边的调度有没有在跑（看 run-lock.json）。"""
    p = os.path.join(_DIR, "run-lock.json")
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
        ts = float(d.get("at") or d.get("ts") or 0)
        if ts > 1e12:
            ts = ts / 1000.0
        return bool(ts) and (time.time() - ts) < 3600
    except Exception:      # noqa: silent-ok — 锁文件坏了就当没在跑
        return False


def save(patch):
    """保存界面改的配置（只认白名单键）。返回 (ok, 说明)。"""
    c = cfg()
    changed = []
    for k, v in (patch or {}).items():
        if k not in _WRITABLE:
            continue
        if k in ("perDay", "fetchPerRound", "searchPages", "commentPerRound"):
            try:
                v = int(v)
            except Exception:
                continue
            v = max(0, min(v, 200))
        if k in ("peakValley", "noon", "night", "autoComment", "transcribe", "dmWatch"):
            if isinstance(v, str):
                v = v.lower() in ("1", "true", "on", "yes")
            v = bool(v)
        if k == "keywords":
            if isinstance(v, str):
                v = [x.strip() for x in v.replace("，", ",").split(",") if x.strip()]
            elif not isinstance(v, list):
                continue
        c[k] = v
        changed.append(k)
    if not changed:
        return False, "没有可保存的字段"
    try:
        _save(_CFG, c)
    except Exception as e:      # noqa: BLE001 — 存不下去要说清，不能假报成功
        return False, "保存失败：%s" % str(e)[:120]
    return True, "已保存：" + "、".join(changed)


def _dm_report(items, c):
    """看完这一轮 → 给**你**发一条私信汇总。没配 uid 就如实说没发。

    【为什么单独写】`bili_send_message` 工具一直有，但**没有任何地方调它** ——
    用户看到的"看完不报告"不是它懒得发，是**这条路径根本不存在**。
    """
    mode = str(c.get("dmMode") or "off")
    uid = int(c.get("dmUid") or c.get("dmOwnerUid") or 0)
    if mode == "off":
        return ""
    if uid <= 0:
        return "私信报告没发：dmUid 是 0（在设置里填你的 B站 uid）"
    if not items:
        return "私信报告没发：这一轮没看到新视频"
    lines = ["这一轮看了 %d 条：" % len(items)]
    for it in items[:8]:
        lines.append("· %s" % str(it.get("title") or it.get("bvid") or "")[:40])
    try:
        X = _tools()
        r = str(X.run_tool("bili_send_message",
                           {"uid": uid, "text": "\n".join(lines)[:900], "force": True},
                           force=True))
        ok = ("已发" in r) or ("ok" in r.lower())
        return "私信报告已发（uid %s）" % uid if ok else ("私信没发成：%s" % r[:60])
    except Exception as e:      # noqa: BLE001
        return "私信发送出错：%s" % str(e)[:60]


def _write_report(v, verdict, why="", mine="", commented=False):
    """小焦这边也写一份报告 → 画廊才有档位，不再是清一色「未判定」。

    【为什么必须有】画廊的档位只从 `reports/*.md` 读（`_parse_report`），
    而那些报告一直是**原版 DSH 侧**写的；小焦每轮看完只往 `seen.md` 记账，
    于是用户看到 60 条全是「未判定」（实测截图）。
    这里按 `_parse_report` 认的格式落一份，字段一个不多一个不少。
    """
    bv = str(v.get("bvid") or "").strip()
    if not bv.startswith("BV"):
        return ""
    try:
        os.makedirs(_REPORTS, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        path = os.path.join(_REPORTS, "%s-%s.md" % (bv, day))
        title = str(v.get("title") or "")[:100]
        up = str(v.get("author") or v.get("up") or "")[:40]
        play = str(v.get("play") or "")
        body = ["# %s" % bv,
                "",
                "标题：%s｜UP：%s｜播放：%s" % (title, up, play or "未知"),
                "",
                "**档位**：%s" % (verdict or "未判定"),
                "",
                "为什么值得：%s" % (why or "—"),
                "",
                "我的一句话：%s" % (mine or "—"),
                "",
                ("已评论" if commented else "未评论"),
                "",
                "_（由小焦本轮刷B站自动记录）_"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        return path
    except Exception as e:      # noqa: BLE001 — 写不了报告不影响这一轮
        LOG.info("[刷B站] 写报告失败：%s", str(e)[:80])
        return ""


def _in_window(now, c):
    """现在在不在"该自动跑"的时段里。返回 (可以跑?, 时段名)。

    口径与插件一致：
      · `peakValley` 关 → 任何时间都算（那就没有"谷时"这回事了）
      · 开 → 只在 中午 12:00-14:00 / 夜间 18:00-09:00 两个谷时里跑，
        并且 `noon` / `night` 各自还能单独关
    """
    if not c.get("peakValley"):
        return True, "any"
    h = now.hour
    if c.get("noon", True) and 12 <= h < 14:
        return True, "noon"
    if c.get("night", True) and (h >= 18 or h < 9):
        return True, "night"
    return False, ""


def auto_state(now=None):
    """只判断**不执行**：自动轮现在是什么状态、下一班什么时候。

    为什么单独有这个：界面上必须能回答"它到底会不会自己跑"。
    如果只在真跑的时候才有反馈，用户永远不知道自己是"配好了在等"还是"根本不会跑"。
    """
    import datetime as _dt
    now = now or _dt.datetime.now()
    c = cfg()
    st = state()
    today = now.strftime("%Y-%m-%d")
    n = int(c.get("perDay") or 0)
    used = int(st.get("count") or 0) if st.get("day") == today else 0
    if n <= 0:
        return {"enabled": False, "reason": "perDay=0（不自动刷）", "used": used, "limit": n}
    if used >= n:
        return {"enabled": False, "reason": "今天已跑满 %d 条" % n, "used": used, "limit": n}
    ok, win = _in_window(now, c)
    if ok and not (st.get("windows") or {}).get("%s:%s" % (today, win)):
        return {"enabled": True, "reason": "现在就在谷时窗口（%s），等下就会跑" % win,
                "used": used, "limit": n, "window": win}
    # 算下一班：从当前时刻往后找 24 小时里第一个符合条件的整点
    for k in range(1, 25):
        t = now + _dt.timedelta(hours=k)
        ok2, win2 = _in_window(t, c)
        if ok2 and not (st.get("windows") or {}).get("%s:%s" % (t.strftime("%Y-%m-%d"), win2)):
            # 跨天要说清是"明天几点"，只写 "00:00 之后" 会让人以为是今天半夜（实测就被误解过）
            same_day = t.strftime("%Y-%m-%d") == now.strftime("%Y-%m-%d")
            when = t.strftime("%H:00") if same_day else t.strftime("明天 %H:00")
            return {"enabled": True, "reason": "%s 之后（%s）" % (when, win2),
                    "used": used, "limit": n, "next": t.strftime("%Y-%m-%d %H:%M")}
    return {"enabled": True, "reason": "等待谷时窗口", "used": used, "limit": n}


def tick(now=None, force=False):
    """**自动轮的心跳**：每两分钟被调一次，判断"该不该自己刷一轮"。

    为什么要有它（用户实测反馈："他没有自己去刷视频"）：
      DSH 那边有调度器，而小焦这边我一开始只做了个手动按钮 —— 于是界面上写着
      "峰谷时段自动刷"，实际**永远不会自己跑**。那是最坏的一种错：看起来配好了。
    判断顺序（任何一条不满足就安静地跳过，不打扰用户）：
      ① `perDay > 0`（0 = 明确不自动刷）
      ② 今天自动跑的条数还没到 `perDay`
      ③ 现在在谷时窗口里（`peakValley` 关 → 任何时间都算，但要隔 30 分钟才跑下一轮）
      ④ **这个窗口今天还没跑过**（`state.windows["日期:窗口"]`，与插件的记账口径一致）
    force=True 只给"测试用"：跳过 ③④ 立刻跑一轮（仍然不动手动计数）。
    自动轮**不发评论**（只读 + 记账）：小焦这轮没有 DSH 那套人设写评论的流水线，
    自动往 B站 发通用话术等于刷屏 —— 宁可少做，也不做错。
    """
    import datetime as _dt
    now = now or _dt.datetime.now()
    c = cfg()
    n = int(c.get("perDay") or 0)
    if n <= 0 and not force:
        return {"ran": False, "why": "perDay=0（不自动刷）"}
    st = state()
    today = now.strftime("%Y-%m-%d")
    used = int(st.get("count") or 0) if st.get("day") == today else 0
    if used >= n and not force:
        return {"ran": False, "why": "今天已跑满 %d 条" % n}
    ok, win = _in_window(now, c)
    key = "%s:%s" % (today, win)
    if not force:
        if not ok:
            return {"ran": False, "why": "现在不在谷时窗口"}
        if win == "any":
            # 【为什么 any 模式不能走 windows 闸】`peakValley` 关掉时"任何时间都算窗口"，
            # 如果还按"一个窗口一天只跑一次"，那就变成**一天只跑一轮**，
            # 而 `perDay` 明明写着 20 条 —— 用户会觉得"额度形同虚设"。
            # 所以 any 模式改用"距上次至少 30 分钟"的间隔控制，条数由 perDay 管。
            last = st.get("lastRunAt") or ""
            if last:
                try:
                    import calendar
                    lt = calendar.timegm(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))
                    if time.time() - lt < 1800:
                        return {"ran": False, "why": "距上一轮不到 30 分钟（any 模式按间隔跑）"}
                except Exception:      # noqa: silent-ok — 时间解析不了就当作没跑过
                    pass
        elif (st.get("windows") or {}).get(key):
            return {"ran": False, "why": "这个窗口今天已经跑过了（%s）" % key}
        last_fail = int(st.get("lastFailAt") or 0)
        if last_fail and (time.time() * 1000 - last_fail) < 1800 * 1000:
            return {"ran": False, "why": "上一轮没挑到新的，再等一会儿（30 分钟后重试）"}
    r = run_round(allow_comment=None, manual=False)   # None = 按 autoComment 决定
    if not force:
        st = state()
        w = st.setdefault("windowTries", {})
        w[key] = int(w.get(key) or 0) + 1
        if r.get("ok"):
            # **只有真跑成了才把这一班标记成"跑过"**
            # （实测踩到：没挑到新视频也把窗口标了 → 那一班就永远不重试了，
            #   用户看到的是"它又没动"）。
            st.setdefault("windows", {})[key] = True
            st["count"] = int(st.get("count") or 0) + 1
        else:
            # 没跑成（多半是"暂时没有新视频"）→ 不消耗窗口，但要退避，
            # 免得每两分钟就去 B站 问一遍（那是打扰，不是勤奋）。
            st["lastFailAt"] = int(time.time() * 1000)
        _save(_STATE, st)
    return {"ran": bool(r.get("ok")), "why": r.get("error") or ("挑了 %s 个" % r.get("picked")),
            "window": (None if force else key), "result": r}


_sched_thread = None


def start_scheduler(interval=120):
    """起一个守护线程做自动轮（`interval` 秒检查一次）。

    为什么每 120 秒一次而不是精确到点：谷时窗口都是小时级（12-14、18-09），
    两分钟粒度足够，且不会因为机器休眠错过就整天空转。
    """
    global _sched_thread
    if _sched_thread is not None and _sched_thread.is_alive():
        return False
    import threading

    def _loop():
        import time as _t
        _t.sleep(20)                     # 启动后先让服务站稳，别一上来就抢网络
        while True:
            try:
                r = tick()
                if r.get("ran"):
                    try:
                        import logging
                        logging.getLogger(__name__).warning("[刷B站] 自动轮跑了：%s", r.get("why"))
                    except Exception:      # noqa: silent-ok
                        pass
            except Exception:      # noqa: silent-ok — 自动轮出错绝不能把线程搞死
                pass
            _t.sleep(int(interval))

    _sched_thread = threading.Thread(target=_loop, name="bili-duty-auto", daemon=True)
    _sched_thread.start()
    return True


def register(app):
    """把接口挂到 Flask 上。**只挂路由，不碰别的**。"""
    from flask import jsonify, request

    @app.route("/api/bili_duty", methods=["GET", "POST"])
    def api_bili_duty():
        if request.method == "POST":
            ok, msg = save(request.get_json(silent=True) or {})
            out = status()
            out["saved"] = ok
            out["msg"] = msg
            return jsonify(out)
        return jsonify(status())

    @app.route("/api/bili_duty/run", methods=["POST"])
    def api_bili_duty_run():
        body = request.get_json(silent=True) or {}
        # allow_comment 不传（或 null）→ **按设置里的「自动发评论」决定**；
        # 传 true 才是"这一次强制发"（界面上那个勾选框）。
        ac = body.get("allow_comment")
        if ac is not None:
            ac = bool(ac)
        try:
            r = run_round(allow_comment=ac, budget=body.get("budget"))
        except Exception as e:      # noqa: BLE001 — 跑挂了要如实回，界面不能白屏
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
        return jsonify(r)

    @app.route("/api/bili_duty/seen", methods=["GET"])
    def api_bili_duty_seen():
        return jsonify({"ok": True, "videos": seen(60), "comments": my_comments(30)})

    @app.route("/api/bili_duty/gallery", methods=["GET"])
    def api_bili_duty_gallery():
        f = (request.args.get("filter") or "").strip()
        cards = gallery(f)
        # 档位分布（给界面上的筛选条显示条数）
        cnt = {}
        for c in gallery(""):
            v = c.get("verdict") or "未判定"
            cnt[v] = cnt.get(v, 0) + 1
        cnt["全部"] = len(gallery(""))
        return jsonify({"ok": True, "cards": cards, "counts": cnt})

    @app.route("/api/bili_duty/cover/<bvid>", methods=["GET"])
    def api_bili_duty_cover(bvid):
        from flask import Response
        data, mime = cover_bytes(bvid)
        if not data:
            return jsonify({"ok": False, "error": mime}), 404
        r = Response(data, mimetype=mime)
        r.headers["Cache-Control"] = "public, max-age=86400"   # 封面不会变，一年缓存一天够用
        return r

    @app.route("/api/bili_duty/tick", methods=["POST"])
    def api_bili_duty_tick():
        """触发一次"该不该自动跑"的判断；`{"force":true}` 则立刻跑一轮（测试用）。"""
        from flask import request as _rq
        force = bool((_rq.get_json(silent=True) or {}).get("force"))
        try:
            r = tick(force=force)
        except Exception as e:      # noqa: BLE001 — 自检失败要说清
            r = {"ran": False, "why": "%s: %s" % (type(e).__name__, str(e)[:120])}
        return jsonify(r)
