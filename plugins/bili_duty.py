# -*- coding: utf-8 -*-
"""B站插件 · 把小焦接上 B站（搜索/评论/字幕/弹幕/榜单/UP主/封面画面）

总纲（与本项目所有模块一致）：
    小焦系统本身不依赖任何具体模型。它是完整的载体（器官齐全），模型是火种（可替换）。
    接入任何模型 → 系统活；换任何模型 → 系统不变。
    这就是"模型平等"和"变形金刚"的工程基础。

它是怎么工作的（为什么不直接 import）：
    B站能力的实现放在 `bili_duty/`（原来是一个独立的 MCP 插件，自带 `.venv`，
    里面装着 `bilibili_api` + `mcp`）。小焦自己的解释器**没有**这些依赖，
    所以这里不 import 它，而是：小焦 → 子进程（插件自己的 venv python）→ `bili_duty/xj_bridge.py`
    → 目标函数 → JSON 回传。
    好处：① 不污染小焦的环境；② 插件目录整体搬走/换 venv 都不影响小焦；
          ③ 崩了也只是"这一条工具失败"，不会把小焦进程带下去。
    代价：每次调用多一次进程启动（约 0.3~1 秒）+ 插件自己的节流（0.6~1.6 秒），
          这对"搜一下/读一条"完全够用。

红线（与载体一致）：
    `bili_reply` 会**往 B站 写东西**（发评论）。默认**不**让它自己发：
    必须调用方显式传 `force=true`（也就是"本人当场让发"），否则直接拒绝、一个字都不发。
    另外插件侧还有自己的 `_guard("bili_reply")` 闸，两道闸都在才算开。
"""
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                      # 仓库根
_PLUGIN_DIR = os.path.join(_ROOT, "bili_duty")
_BRIDGE = os.path.join(_PLUGIN_DIR, "xj_bridge.py")
_CONF = os.path.join(_PLUGIN_DIR, "config.json")

# 每次调用的默认超时（秒）。抓取/转写类工具自己更慢，单独给。
_TIMEOUT = 120
_TIMEOUT_SLOW = {"bili_crawl": 600, "bili_transcribe_result": 600,
                 "bili_transcribe": 900, "bili_transcribe_start": 900,
                 "bili_login": 300}
_NEED_ANY = {"bili_search": ("keyword", "搜索词"),
             "bili_comments": ("bvid", "BV号"), "bili_subtitle": ("bvid", "BV号"),
             "bili_video_info": ("bvid", "BV号"), "bili_danmaku": ("bvid", "BV号"),
             "bili_crawl": ("keyword", "关键词"), "bili_cover": ("bvid", "BV号"),
             "bili_frames": ("bvid", "BV号"), "bili_user_info": ("uid", "UID"),
             "bili_user_videos": ("uid", "UID"),
             "bili_favorite_content": ("media_id", "收藏夹 id"),
             "bili_transcribe": ("bvid", "BV号"),
             "bili_transcribe_start": ("bvid", "BV号"),
             "bili_transcribe_result": ("job_id", "job_id")}


def _log(msg):
    """插件日志：走标准 logging（小焦会接管），失败也不影响功能。"""
    try:
        import logging
        logging.getLogger(__name__).info("[bili] %s", msg)
    except Exception:      # noqa: silent-ok — 日志写不进去不能挡住工具调用
        pass


class BilibiliPlugin:
    """B站插件：把 bili-duty 的能力登记成小焦的工具。"""

    # ---------------- 环境 ----------------
    @staticmethod
    def _python_path():
        """找到跑桥接的 python（插件自带 venv 优先）。

        顺序：config.json 里记的 pythonPath → 插件目录下的 .venv → 当前解释器。
        找不到时返回 None，由 execute 如实告诉用户"插件环境没装好"，
        **不要**假装能用（那会让模型编出"我查到了"）。
        """
        cand = []
        # **本地 .venv 优先**：插件整体搬到别处也还能用（不依赖 config 里记的绝对路径）；
        # config.json 的 pythonPath 作为兜底（万一有人特意指到别的解释器）。
        cand.append(os.path.join(_PLUGIN_DIR, ".venv", "Scripts", "python.exe"))
        cand.append(os.path.join(_PLUGIN_DIR, ".venv", "bin", "python"))
        try:
            with open(_CONF, encoding="utf-8") as f:
                p = (json.load(f) or {}).get("pythonPath")
            if p:
                cand.append(p)
        except Exception:      # noqa: silent-ok — 配置缺失就走上面的默认路径
            pass
        for p in cand:
            if p and os.path.isfile(p):
                return p
        return None

    def __init__(self):
        self._py = self._python_path()
        self._warned = False

    def _call(self, tool, args, timeout=None):
        """调一次桥接，返回文本。失败时返回**可读的失败原因**（绝不编造结果）。"""
        if not os.path.isfile(_BRIDGE):
            return "B站插件未接好：找不到 %s" % _BRIDGE
        py = self._py or sys.executable
        payload = json.dumps({"tool": tool, "args": args or {}}, ensure_ascii=False)
        try:
            r = subprocess.run(
                [py, _BRIDGE],
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=_PLUGIN_DIR,
                timeout=timeout or _TIMEOUT_SLOW.get(tool, _TIMEOUT),
            )
        except subprocess.TimeoutExpired:
            return "B站请求超时（>%ds）：%s。可能是网络慢或 B站 限流，稍后再试。" % (
                timeout or _TIMEOUT_SLOW.get(tool, _TIMEOUT), tool)
        except Exception as e:      # noqa: BLE001 — 起不了子进程要如实说
            return "B站插件调用失败：%s: %s" % (type(e).__name__, str(e)[:200])
        out = (r.stdout or b"").decode("utf-8", "ignore").strip()
        err = (r.stderr or b"").decode("utf-8", "ignore").strip()
        line = ""
        for ln in reversed(out.splitlines()):
            if ln.strip().startswith("{"):
                line = ln.strip()
                break
        if not line:
            _log("桥接无 JSON 输出 rc=%s stderr=%s" % (r.returncode, err[-400:]))
            return "B站插件没有返回结果（rc=%s）。%s" % (r.returncode, err[-300:] if err else "")
        try:
            d = json.loads(line)
        except Exception:
            return "B站插件返回了看不懂的内容：%s" % line[:200]
        if d.get("ok"):
            return str(d.get("text") or "")
        _log("桥接失败：%s" % str(d.get("error"))[:300])
        return "B站操作失败：%s" % (d.get("error") or "未知原因")

    # ---------------- 工具清单 ----------------
    def get_tool_descriptions(self):
        """登记给小焦的工具。

        【每条描述都必须写「什么时候用」】仓库的自测里有一条硬规则：
        插件工具的描述必须回答"什么场景该挑它"（`test_units` 的「工具选择」用例）。
        理由很实际：模型挑工具靠的就是这句话——只写"能干什么"不写"什么时候用"，
        该用 B站 的时候它会去 web_search，该 web_search 的时候它会去抓 B站。
        所以下面 14 条**每一条**都以「什么时候用」开头或含有该短语。
        """
        return [
            {"name": "bili_search",
             "description": "搜 B站 视频。什么时候用：用户问「B站 上有没有…」「搜一下B站的X」；"
                            "输入 keyword，可选 num/order（totalrank=综合, click=播放, pubdate=最新）；"
                            "输出 标题/作者/播放/时长/BV号。(泛泛的资料类问题请用 web_search)",
             "parameters": {"keyword": "搜索词（必填）", "num": "条数，默认10",
                            "order": "排序：totalrank/click/pubdate/dm"}},
            {"name": "bili_comments",
             "description": "取一个视频的热门评论（含点赞/回复数）。什么时候用：想了解一条视频的"
                            "风评、观众反响，或看评论区有没有人在吐槽/补充信息；输入 bvid。",
             "parameters": {"bvid": "BV号（必填）", "num": "条数，默认30"}},
            {"name": "bili_subtitle",
             "description": "取视频的 AI 字幕全文。什么时候用：你要**判断这条视频到底讲了什么**"
                            "（别看标题就下结论）；输入 bvid。",
             "parameters": {"bvid": "BV号（必填）"}},
            {"name": "bili_video_info",
             "description": "看视频基本信息（标题/UP主/播放/简介/分P等）。什么时候用：用户给了 BV号"
                            "但你需要先确认“这是不是他要的那条”；输入 bvid。",
             "parameters": {"bvid": "BV号（必填）"}},
            {"name": "bili_danmaku",
             "description": "取视频弹幕（观众实时反应）。什么时候用：想看某一刻观众在刷什么、"
                            "或者判断这条视频的“名场面”在哪；输入 bvid，可选 num。",
             "parameters": {"bvid": "BV号（必填）", "num": "条数，默认100"}},
            {"name": "bili_crawl",
             "description": "按关键词批量抓：搜视频→取评论→取字幕，一次回来。什么时候用：要"
                            "「整体了解一下 B站 上某话题」时用它；输入 keyword，可选 max_videos。",
             "parameters": {"keyword": "关键词（必填）", "max_videos": "抓几个视频，默认5",
                            "comments_per_video": "每个视频几条评论，默认20",
                            "get_subtitles": "是否抓字幕，默认true"}},
            {"name": "bili_rank",
             "description": "B站排行榜。什么时候用：用户问「最近什么火」「B站榜单」「哪个区最热」；"
                            "可选 category/day。",
             "parameters": {"category": "分区，默认all", "day": "近几天，默认3"}},
            {"name": "bili_hot_videos",
             "description": "B站热门视频。什么时候用：用户想看「现在 B站 上在热什么」，"
                            "但没给具体关键词；可选 pn 页码 / ps 每页条数。",
             "parameters": {"pn": "页码，默认1", "ps": "每页条数，默认20"}},
            {"name": "bili_user_info",
             "description": "看一个 UP 主的基本信息。什么时候用：用户提到某个 UP 主，你要先确认"
                            "TA 是谁、多少粉丝；输入 uid。",
             "parameters": {"uid": "UP主UID（必填，数字）"}},
            {"name": "bili_user_videos",
             "description": "看一个 UP 主投过的视频。什么时候用：用户问「某某 UP 最近发了什么」"
                            "「这个 UP 还做过什么」；输入 uid，可选 pn/ps/order/keyword。",
             "parameters": {"uid": "UP主UID（必填）", "pn": "页码默认1", "ps": "每页默认30",
                            "order": "pubdate/click", "keyword": "在TA投稿里搜"}},
            {"name": "bili_cover",
             "description": "看一个视频的封面（能看出题材/风格，但看不出视频里发生了什么）。"
                            "什么时候用：想快速判断一条视频的调性/是否少儿不宜；输入 bvid。",
             "parameters": {"bvid": "BV号（必填）"}},
            {"name": "bili_frames",
             "description": "看视频里的画面（按时长取几帧）。什么时候用：想知道"
                            "「这条视频到底拍了什么」——字幕告诉你说了什么，画面告诉你拍了什么。",
             "parameters": {"bvid": "BV号（必填）", "num": "帧数1-6，默认3",
                            "at": "指定时间点（秒）列表，如 [12,45]"}},
            {"name": "bili_login_check",
             "description": "看 B站 那边登录的是谁（凭证是否有效）。什么时候用：要发评论/私信之前"
                            "先查这个；或用户问「B站 登录了吗」。",
             "parameters": {}},
            {"name": "bili_check_credential",
             "description": "检查凭证文件本身是否完整/过期（比 login_check 更底层，不联网也能说清"
                            "缺哪个字段）。什么时候用：login_check 报未登录、要判断是「没登录」"
                            "还是「凭证文件坏了」。",
             "parameters": {}},
            {"name": "bili_login",
             "description": "开始扫码登录 B站（出二维码）。什么时候用：用户说「登录 B站」「扫码」，"
                            "或凭证失效需要重新登。二维码约 180 秒有效，要马上给用户扫。",
             "parameters": {}},
            {"name": "bili_video_zones",
             "description": "看 B站 有哪些分区（分区 id 与名字）。什么时候用：要按分区找视频、"
                            "或用户提到某个区（鬼畜/科技/生活）而你需要对应的 category。",
             "parameters": {}},
            {"name": "bili_hot_buzzwords",
             "description": "看 B站 热门搜索词/梗。什么时候用：想知道「现在大家都在搜什么」"
                            "「有什么新梗」，用来挑话题或判断热度。",
             "parameters": {"page_num": "页码，默认1", "page_size": "每页条数，默认20"}},
            {"name": "bili_weekly_hot",
             "description": "看 B站 每周必看（周榜）。什么时候用：用户问「这周有什么值得看的」；"
                            "输入 week（0=最近一周）。",
             "parameters": {"week": "第几周，0=最近"}},
            {"name": "bili_recommend",
             "description": "取 B站 给**当前登录账号**的首页推荐流。什么时候用：没有明确关键词、"
                            "要让「刷」这件事按用户自己的口味来，而不是按热门榜。",
             "parameters": {"ps": "条数，默认20"}},
            {"name": "bili_favorite_lists",
             "description": "看收藏夹列表（自己或别人的）。什么时候用：用户问「我收藏夹里有什么」"
                            "「有哪些收藏夹」；uid=0 表示自己。",
             "parameters": {"uid": "用户UID，0=自己"}},
            {"name": "bili_favorite_content",
             "description": "看某个收藏夹里的内容。什么时候用：用户指了某个收藏夹、要里面的视频清单；"
                            "输入 media_id（从 favorite_lists 拿），可选 keyword 过滤。",
             "parameters": {"media_id": "收藏夹id（必填）", "page": "页码默认1",
                            "keyword": "在收藏夹里搜"}},
            {"name": "bili_unread_messages",
             "description": "看未读私信。什么时候用：用户问「有人私信我吗」「有没有新消息」。",
             "parameters": {}},
            {"name": "bili_received_replies",
             "description": "看别人回复我的评论/收到的评论回应。什么时候用：想知道「我发的评论有人回吗」"
                            "「评论区有没有人接话」。",
             "parameters": {}},
            {"name": "bili_received_at_and_likes",
             "description": "看 @我的 与 收到的点赞。什么时候用：用户问「有人 @我吗」「我评论被赞了吗」。",
             "parameters": {}},
            {"name": "bili_send_message",
             "description": "给某个用户发私信。什么时候用：**本人当场明确让你发**才用——"
                            "这会往 B站 写东西，必须传 force=true；否则一律拒绝。",
             "parameters": {"receiver_uid": "对方UID（必填）", "text": "私信正文（必填）",
                            "force": "必须为true才允许发"}},
            {"name": "bili_transcribe",
             "description": "用本地 ASR 把**没有字幕**的视频转成文字（慢，几分钟）。什么时候用："
                            "字幕接口返回空、但你确实需要内容判断。注意它会等跑完，很慢。",
             "parameters": {"bvid": "BV号（必填）"}},
            {"name": "bili_transcribe_start",
             "description": "排一个转写任务并拿到 job_id（不阻塞）。什么时候用：想转写又不想卡住 ——"
                            "先 start，之后用 transcribe_result 取。",
             "parameters": {"bvid": "BV号（必填）"}},
            {"name": "bili_transcribe_result",
             "description": "取转写任务的结果。什么时候用：start 拿到 job_id 之后来这里取文字。",
             "parameters": {"job_id": "start 返回的 job_id（必填）"}},
            {"name": "bili_reply",
             "description": "在视频下发一条评论（或回复某条评论）。什么时候用：**本人当场明确让你发**"
                            "的时候才用——这会往 B站 写东西，必须传 force=true；否则一律拒绝"
                            "（自动评论默认关闭）。",
             "parameters": {"bvid": "BV号（必填）", "text": "评论正文（必填）",
                            "rpid": "要回复的评论id，0=发新评论", "force": "必须为true才允许发"}},
        ]

    # ---------------- 执行 ----------------
    def execute(self, tool_name, params):
        p = params or {}
        # 必填参数先查一遍：空着就直说，别把 B站 的 -400 原样甩给用户
        #（"请求错误"这种话对用户没有任何信息量，模型还可能据此编造）。
        # 用模块级 `_NEED_ANY`（和工具清单同一处维护），免得两处各写一份又漏项。
        if tool_name in _NEED_ANY:
            k, cn = _NEED_ANY[tool_name]
            if not str(p.get(k) or "").strip():
                return "这个操作需要一个%s：请告诉我具体是哪一个（参数名 %s）。" % (cn, k)
        try:
            if tool_name == "bili_search":
                return self._call("bili_search", {
                    "keyword": p.get("keyword") or p.get("q") or "",
                    "num": int(p.get("num") or 10),
                    "order": p.get("order") or "totalrank"})
            if tool_name == "bili_comments":
                return self._call("bili_comments", {"bvid": p.get("bvid") or "",
                                                    "num": int(p.get("num") or 30)})
            if tool_name == "bili_subtitle":
                return self._call("bili_subtitle", {"bvid": p.get("bvid") or ""})
            if tool_name == "bili_video_info":
                return self._call("bili_video_info", {"bvid": p.get("bvid") or ""})
            if tool_name == "bili_danmaku":
                return self._call("bili_danmaku", {"bvid": p.get("bvid") or "",
                                                   "num": int(p.get("num") or 100)})
            if tool_name == "bili_crawl":
                return self._call("bili_crawl", {
                    "keyword": p.get("keyword") or "",
                    "max_videos": int(p.get("max_videos") or 5),
                    "comments_per_video": int(p.get("comments_per_video") or 20),
                    "get_subtitles": bool(p.get("get_subtitles", True))})
            if tool_name == "bili_rank":
                return self._call("bili_rank", {"category": p.get("category") or "all",
                                                "day": int(p.get("day") or 3)})
            if tool_name == "bili_hot_videos":
                return self._call("bili_hot_videos", {"pn": int(p.get("pn") or 1),
                                                      "ps": int(p.get("ps") or 20)})
            if tool_name == "bili_user_info":
                return self._call("bili_user_info", {"uid": int(p.get("uid") or 0)})
            if tool_name == "bili_user_videos":
                return self._call("bili_user_videos", {
                    "uid": int(p.get("uid") or 0),
                    "pn": int(p.get("pn") or 1), "ps": int(p.get("ps") or 30),
                    "order": p.get("order") or "pubdate",
                    "keyword": p.get("keyword") or ""})
            if tool_name == "bili_cover":
                return self._call("bili_cover", {"bvid": p.get("bvid") or ""})
            if tool_name == "bili_frames":
                at = p.get("at")
                return self._call("bili_frames", {
                    "bvid": p.get("bvid") or "",
                    "num": int(p.get("num") or 3),
                    "at": [float(x) for x in at] if isinstance(at, (list, tuple)) and at else None})
            if tool_name == "bili_login_check":
                return self._call("bili_login_check", {})
            if tool_name == "bili_check_credential":
                return self._call("bili_check_credential", {})
            if tool_name == "bili_login":
                return self._call("bili_login", {}, timeout=_TIMEOUT_SLOW.get("bili_login", 300))
            if tool_name == "bili_video_zones":
                return self._call("bili_video_zones", {})
            if tool_name == "bili_hot_buzzwords":
                return self._call("bili_hot_buzzwords", {
                    "page_num": int(p.get("page_num") or 1),
                    "page_size": int(p.get("page_size") or 20)})
            if tool_name == "bili_weekly_hot":
                return self._call("bili_weekly_hot", {"week": int(p.get("week") or 0)})
            if tool_name == "bili_recommend":
                return self._call("bili_recommend", {"ps": int(p.get("ps") or 20)})
            if tool_name == "bili_favorite_lists":
                return self._call("bili_favorite_lists", {"uid": int(p.get("uid") or 0)})
            if tool_name == "bili_favorite_content":
                return self._call("bili_favorite_content", {
                    "media_id": int(p.get("media_id") or 0),
                    "page": int(p.get("page") or 1),
                    "keyword": str(p.get("keyword") or "")})
            if tool_name == "bili_unread_messages":
                return self._call("bili_unread_messages", {})
            if tool_name == "bili_received_replies":
                return self._call("bili_received_replies", {})
            if tool_name == "bili_received_at_and_likes":
                return self._call("bili_received_at_and_likes", {})
            if tool_name == "bili_send_message":
                # ---- 红线：私信也是"往 B站 写东西"，同样必须本人在场 ----
                if not (p.get("force") is True or str(p.get("force")).lower() == "true"):
                    return ("🔴 这条会**真的发私信**给 B站 用户。自动发消息是关的，所以我没发。"
                            "你确认要发，明确说一句「发吧」，我再动手。")
                text = str(p.get("text") or "").strip()
                uid = int(p.get("receiver_uid") or 0)
                if not text or not uid:
                    return "发私信需要 receiver_uid 和 text 两个参数。"
                return self._call("bili_send_message", {"receiver_uid": uid, "text": text})
            if tool_name == "bili_transcribe":
                return self._call("bili_transcribe", {"bvid": p.get("bvid") or ""})
            if tool_name == "bili_transcribe_start":
                return self._call("bili_transcribe_start", {"bvid": p.get("bvid") or ""})
            if tool_name == "bili_transcribe_result":
                return self._call("bili_transcribe_result", {"job_id": str(p.get("job_id") or "")})
            if tool_name == "bili_reply":
                # ---- 红线：往 B站 写东西，必须"本人在场" ----
                if not (p.get("force") is True or str(p.get("force")).lower() == "true"):
                    return ("🔴 这条会把评论**真的发到 B站**。自动评论默认是关的，"
                            "所以我没有发。如果你确认要发，明确说一句「发吧」，我再动手。")
                text = str(p.get("text") or "").strip()
                bvid = str(p.get("bvid") or "").strip()
                if not text or not bvid:
                    return "发评论需要 bvid 和 text 两个参数。"
                return self._call("bili_reply", {"bvid": bvid, "text": text,
                                                 "rpid": int(p.get("rpid") or 0)})
        except Exception as e:      # noqa: BLE001 — 参数错也要如实说，别让模型猜
            return "B站工具「%s」调用出错：%s: %s" % (tool_name, type(e).__name__, str(e)[:200])
        return "B站插件里没有「%s」这个工具。" % tool_name
