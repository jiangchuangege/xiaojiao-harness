# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
# 这就是"模型平等"和"变形金刚"的工程基础。
"""小焦 · 健康系统 · 急诊守卫（让"停机"这件事有证据、有期限、能恢复）

【要修的是什么 —— 一次真实事故，不是理论推演】

    实测日志（`logs/xiaojiao.log`）里反复出现这一组，约每两小时一次：

        [22:09:35] 健康急诊：已保留现场
        [22:09:35] 健康通知（3 级）：本轮判为 HEAVY：载体资源吃紧（显存告警）
        [22:09:35] 健康急诊：已停止服务（拒绝继续生成），原因：本轮判为 LIGHT：
                  火种（模型）在当前上下文长度下开始不稳定；依据：无明显症状。
        [22:09:35] 健康通知（4 级）：必须马上停下来休息。原因：本轮判为 LIGHT…
                  依据：无明显症状。建议：继续回答即可。

    三件事同时成立，本身就是自相矛盾的：
      · 等级 = 4 级（"必须马上停下来休息"）
      · 依据 = **无明显症状**
      · 建议 = **继续回答即可**
    而系统取了最狠的那一项去执行：真的停了。

【比误报更严重：停了就起不来】
    `_HEALTH_EMERGENCY` 在 `xiaojiao_app.py` 里被置 True（第 11007 行），
    但通篇 19,000 行**没有任何一处把它复位为 False**。于是：

        误报一次 → 标志永久为真 → agent_run 对**每一个**请求都返回急诊文本
                → 小焦从此一句话都不答 → 用户只能重启进程

    日志里那些 20~30 秒一次的进程重启，就是这个的痕迹（不是崩溃，是"答不了话"）。
    一个"保护性"机制把自己变成了**单点永久故障**，这是它最该被修掉的地方。

【修法的三条原则（不是把急诊关掉）】
    ① **有证据才停**：安全类症状 / 致命组合 / 连续三轮乱码或拒答 —— 这些是真证据。
       "无明显症状"不是证据。建议写"继续回答"就更不该停。
    ② **有期限**：就算真停了，也要在冷却后**自己站起来**再评一次。
       真有病会再触发（那就再停）；误报则只影响这一小段，不会变成永久。
    ③ **能自证**：每一次"拦下误报"和"到点恢复"都落盘，事后能查清"它到底停过几次、
       有几次是误报"。不许出现"停了但没人知道为什么"。

【刻意不做的三件事】
    · 不删除急诊机制：真出安全类症状时必须停，这条不能松；
    · 不改治疗层的分级：四级治疗本身的设计是对的，错的是"谁有资格进四级"；
    · 不靠重启恢复：恢复必须在进程内完成，否则用户还得手动救它。
"""
from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.RLock()

# ---- 默认旋钮（都可被 xiaojiao_control.json → health.* 覆盖）----
DEFAULT_TTL_S = 180.0        # 急诊停机多久后自动站起来重评一次
DEFAULT_MAX_TRIPS = 3        # 一个冷却窗口内最多因"无证据"拦下几次（超过就说明监测层本身失准）
DEFAULT_WINDOW_S = 3600.0    # 上述计数的时间窗口

# 真证据：这些**确实**说明不能再生成了（与安全层、资源层的口径保持一致）
HARD_CODES = ("harmful_content", "dangerous_instruction", "self_harm",
              "illegal_advice", "safety", "unsafe")
FATAL_PAIRS = (("vram_alert", "timeout"),)

_STATE = {
    "on": False,
    "reason": "",
    "at": 0.0,
    "ttl": DEFAULT_TTL_S,
    "trips": 0,              # 被本守卫拦下的"无证据停机"次数
    "tripped_at": [],        # 每次拦下的时间戳（用于窗口计数）
    "last_clear": 0.0,
    "last_clear_why": "",
    "history": [],           # 最近若干次决策（只留摘要，供复盘）
}

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "logs", "health")
_LOG_PATH = os.path.join(_LOG_DIR, "emergency_guard.jsonl")


# ---------------------------------------------------------------- 配置
def configure(cfg=None):
    """从 health.* 读旋钮。缺配置就用默认值 —— 绝不因为读不到配置而失效。"""
    d = cfg if isinstance(cfg, dict) else {}
    try:
        _STATE["ttl"] = float(d.get("emergency_ttl_s", DEFAULT_TTL_S) or DEFAULT_TTL_S)
    except Exception:        # noqa: silent-ok
        _STATE["ttl"] = DEFAULT_TTL_S
    return dict(ttl=_STATE["ttl"])


# ---------------------------------------------------------------- 证据判定
def _codes_of(symptoms):
    """把症状输入归一成 code 列表（兼容对象 / dict / str 三种形态）。"""
    out = []
    for s in (symptoms or []):
        c = None
        if isinstance(s, str):
            c = s
        elif isinstance(s, dict):
            c = s.get("code") or s.get("name")
        else:
            c = getattr(s, "code", None)
        if c:
            out.append(str(c))
    return out


def has_hard_evidence(diag=None, symptoms=None):
    """有没有**真证据**支持"必须停机"。返回 (bool, 说明)。

    这里宁严不宽：只有下面三类算证据 —— 它们对应的是"继续生成会造成实质伤害
    或机器扛不住"，而不是"文本看起来有点怪"。
    """
    d = diag if isinstance(diag, dict) else {}
    codes = _codes_of(symptoms)
    cset = set(codes)

    # ① 安全类：来自别的安全层，一旦出现就是急诊
    hit = cset & set(HARD_CODES)
    if hit:
        return True, "安全类症状：%s" % "、".join(sorted(hit))

    # ② 无法恢复的组合：显存告警 + 超时同时出现（机器扛不住，不是说话说错）
    for a, b in FATAL_PAIRS:
        if a in cset and b in cset:
            return True, "致命组合：%s + %s" % (a, b)

    # ③ 连续 ≥3 轮的乱码 / 拒答（说明这颗火种在这个上下文里确实废了）
    streaks = d.get("streak") if isinstance(d.get("streak"), dict) else {}
    for c in ("garbled", "refusal"):
        if c in cset:
            try:
                if int(streaks.get(c, 1)) >= 3:
                    return True, "%s 连续 %s 轮" % (c, streaks.get(c))
            except Exception:      # noqa: silent-ok — streak 读不出来就不算这条证据
                pass

    # ④ 诊断自己说"没症状 / 可以继续" → 明确不算证据
    reason = str(d.get("reason") or "")
    advice = str(d.get("advice") or "")
    if ("无明显症状" in reason) or ("继续回答" in advice):
        return False, "诊断自述无症状（依据：无明显症状 / 建议：继续回答）"

    if not codes:
        return False, "这一轮没有检出任何症状"

    return False, "症状 %s 未达到急诊证据线" % ("、".join(sorted(cset)) or "（空）")


def should_stop(diag=None, symptoms=None, severity=None):
    """该不该真的停。返回 (bool, 说明)。**永不抛异常。**"""
    try:
        ok, why = has_hard_evidence(diag, symptoms)
        if ok:
            return True, why
        # 没有硬证据 → 拦下。这里要记一笔：这是"它本来要停、被守卫拦了"
        _note_blocked(why, severity)
        return False, "无急诊证据，已拦下（%s）" % why
    except Exception as e:      # noqa: silent-ok — 守卫自己出错时必须放行（宁可停，不可失控）
        return True, "守卫内部异常，按原判执行：%r" % (e,)


# ---------------------------------------------------------------- 状态
def trip(reason="", ttl=None):
    """真的进入急诊停机。带 TTL，到点自己站起来。"""
    with _LOCK:
        _STATE["on"] = True
        _STATE["reason"] = str(reason)[:300]
        _STATE["at"] = time.time()
        if ttl is not None:
            try:
                _STATE["ttl"] = float(ttl)
            except Exception:      # noqa: silent-ok
                pass
        _STATE["history"].append({"ts": _STATE["at"], "act": "trip",
                                  "reason": _STATE["reason"][:120]})
        _STATE["history"] = _STATE["history"][-50:]
    _log("trip", {"reason": _STATE["reason"], "ttl": _STATE["ttl"]})
    return True


def is_on():
    """急诊是否还生效。**到点自动恢复** —— 这是"停了能起来"的那一半。"""
    with _LOCK:
        if not _STATE["on"]:
            return False
        elapsed = time.time() - float(_STATE["at"] or 0)
        if elapsed >= float(_STATE["ttl"] or DEFAULT_TTL_S):
            _STATE["on"] = False
            _STATE["last_clear"] = time.time()
            _STATE["last_clear_why"] = "冷却到期自动恢复（停了 %.0f 秒）" % elapsed
            _STATE["history"].append({"ts": _STATE["last_clear"], "act": "auto_recover",
                                      "reason": _STATE["last_clear_why"]})
            _STATE["history"] = _STATE["history"][-50:]
            _log("auto_recover", {"elapsed_s": round(elapsed, 1)})
            return False
        return True


def clear(why="手动/外部复位"):
    """显式复位（比如体检通过、用户点了恢复）。"""
    with _LOCK:
        was = bool(_STATE["on"])
        _STATE["on"] = False
        _STATE["last_clear"] = time.time()
        _STATE["last_clear_why"] = str(why)[:200]
        if was:
            _STATE["history"].append({"ts": _STATE["last_clear"], "act": "clear",
                                      "reason": _STATE["last_clear_why"]})
            _STATE["history"] = _STATE["history"][-50:]
    if was:
        _log("clear", {"why": _STATE["last_clear_why"]})
    return was


def status():
    """给界面 / 日志看的当前状态。"""
    with _LOCK:
        st = dict(_STATE)
        st["history"] = list(st["history"])[-10:]
        if st["on"]:
            st["remaining_s"] = max(0.0, float(st["ttl"] or 0) - (time.time() - float(st["at"] or 0)))
        else:
            st["remaining_s"] = 0.0
        return st


# ---------------------------------------------------------------- 内部
def _note_blocked(why, severity=None):
    with _LOCK:
        _STATE["trips"] += 1
        _STATE["tripped_at"].append(time.time())
        # 只保留窗口内的记录
        win = float(_STATE.get("window_s", DEFAULT_WINDOW_S) or DEFAULT_WINDOW_S)
        _STATE["tripped_at"] = [t for t in _STATE["tripped_at"] if time.time() - t <= win]
        _STATE["history"].append({"ts": time.time(), "act": "blocked",
                                  "reason": str(why)[:120], "severity": str(severity or "")})
        _STATE["history"] = _STATE["history"][-50:]
        n = len(_STATE["tripped_at"])
    _log("blocked", {"why": str(why)[:160], "severity": str(severity or ""),
                     "blocked_in_window": n})
    if n >= DEFAULT_MAX_TRIPS:
        # 一个窗口内被拦这么多次，说明**监测层判级本身失准** —— 那是该修的 bug，
        # 不是该由用户承担的"保护"。这里如实告警，但不擅自改监测层的阈值。
        _log("monitor_suspect", {"blocked_in_window": n,
                                 "hint": "同一窗口内多次无证据停机，建议复查 monitor 的症状阈值与 streak 统计"})


def _log(act, payload):
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        row = {"ts": time.time(), "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
               "act": act}
        row.update(payload or {})
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:      # noqa: silent-ok — 落盘失败绝不影响守护本身
        pass


__all__ = ["configure", "has_hard_evidence", "should_stop", "trip", "is_on",
           "clear", "status", "HARD_CODES", "FATAL_PAIRS"]
