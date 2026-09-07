# learn_from_neko.py —— 学习通道: 从 N.E.K.O. 猫娘对话中学"你的需求", 存进小焦知识库
# 原理: N.E.K.O. memory_server(:48912) 存了你与猫娘的对话 → 这里拉取最近对话 →
#       提炼"你主动说了什么/问了什么/喜欢什么/在意什么" → 写入 xiaojiao_knowledge_memory.json
#       (小焦对话时会 recall 这些, 于是"更懂你")。
# 用法:  python learn_from_neko.py            # 拉一次并学习
#        python learn_from_neko.py --daemon   # 后台每 N 分钟自动学(和小焦同跑)
# 也可作为小焦的一个工具被调: 详见最后 __main__。
import os, sys, json, time, re, argparse, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(ROOT, "xiaojiao_knowledge_memory.json")
NEKO_MEM = os.environ.get("XIAOJIAO_NEKO_MEM", "http://127.0.0.1:48912")
_STATE = os.path.join(ROOT, ".neko_learn_state.json")   # 记上次学到哪

# 角色名: 小焦在 N.E.K.O. 里叫什么(日志显示 YUI)
LANLAN = os.environ.get("XIAOJIAO_NEKO_CHAR", "YUI")


def _load_mem():
    if os.path.exists(MEMORY_FILE):
        try:
            return json.load(open(MEMORY_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_mem(mem):
    try:
        json.dump(mem, open(MEMORY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def _state_load():
    if os.path.exists(_STATE):
        try:
            return json.load(open(_STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _state_save(st):
    try:
        json.dump(st, open(_STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _neko_alive():
    try:
        import requests
        r = requests.get(NEKO_MEM.rstrip("/") + "/get_settings/" + LANLAN, timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def _fetch_recent():
    """从 N.E.K.O. memory_server 拉最近历史对话。可能返回整段文本备注或结构化列表, 统一转成 [{role,content}]。"""
    import requests
    try:
        r = requests.get(NEKO_MEM.rstrip("/") + "/get_recent_history/" + LANLAN, timeout=8)
        if r.status_code == 200:
            d = r.content.decode("utf-8", "ignore")
            # 优先 JSON 解析(可能是列表)
            try:
                jd = json.loads(d)
            except Exception:
                jd = d
            if isinstance(jd, dict):
                for k in ("messages", "history", "conversations", "items", "data", "recent"):
                    if isinstance(jd.get(k), list):
                        return jd[k]
                if isinstance(jd.get("memo"), str):
                    return [{"role": "system", "content": jd["memo"]}]
            if isinstance(jd, list):
                return jd
            # 整段文本: 按"用户行"拆出来
            txt = jd if isinstance(jd, str) else str(jd)
            return _split_text_history(txt)
    except Exception:
        pass
    return []


def _split_text_history(txt):
    """把整段对话文本拆成 [{role:user,content:...}] — 只保留"用户(碳基生物/主人)+说的话"。"""
    out = []
    for line in txt.splitlines():
        line = line.strip()
        # "碳基生物 | ..." / "主人 | ..." 这类是用户说的话
        for marker in ("碳基生物 |", "主人 |", "Master |", "我 |", "用户 |"):
            if line.startswith(marker):
                rest = line[len(marker):].strip()
                # 去掉时间/括号动作: [20260907 ...]（小声...）
                rest = re.sub(r"\[[^\]]*\]", "", rest)
                rest = re.sub(r"（[^）]*）", "", rest)
                if rest:
                    out.append({"role": "user", "content": rest})
                break
    # 也把整段备忘录里"其称/其让/其询问/其表示"这类用户行为描述抽出来
    return out if out else ({}, ) and [{"role": "user", "content": txt[:2000]}] if txt else []


def _extract_user_needs(history):
    """把对话历史里【用户说的话】挑出来, 提炼成需求/偏好条目。"""
    user_lines = []
    for it in history:
        if isinstance(it, dict):
            role = (it.get("role") or it.get("who") or it.get("from") or "").lower()
            text = it.get("content") or it.get("text") or it.get("message") or ""
            if role in ("user", "master", "主人", "me", "我"):
                user_lines.append(str(text))
        elif isinstance(it, str):
            user_lines.append(it)
    return [t.strip() for t in user_lines if t.strip()]


def _refine_needs(user_lines):
    """(可选)用 DeepSeek/本地 提炼每条需求的关键。没大脑就用原文。"""
    out = []
    for t in user_lines[-40:]:
        out.append(t)
    return out


def learn_once(verbose=True):
    if not _neko_alive():
        if verbose:
            print("[learn_neko] N.E.K.O. memory_server 不在线(%s), 跳过" % NEKO_MEM)
        return 0
    history = _fetch_recent()
    if not history:
        if verbose:
            print("[learn_neko] 暂无对话记录")
        return 0
    needs = _extract_user_needs(history)
    if not needs:
        return 0
    mem = _load_mem()
    added = 0
    for nd in needs:
        if len(nd) < 4:
            continue
        k = "".join(re.findall(r"[\u4e00-\u9fff]{2,}", nd)[:2]) or nd[:4]
        entry = mem.setdefault("neko需求:" + k, {"q": nd, "know": [], "ts": datetime.datetime.now().isoformat()})
        note = "（来自猫娘对话学习：%s）" % nd[:60]
        if note not in entry["know"]:
            entry["know"].append(note)
        entry["know"] = entry["know"][-8:]
        added += 1
    _save_mem(mem)
    if verbose:
        print("[learn_neko] ✅ 学 %d 条用户需求 → %s" % (added, MEMORY_FILE))
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true", help="后台循环学习")
    ap.add_argument("--interval", type=int, default=300, help="daemon 间隔秒")
    a = ap.parse_args()
    if a.daemon:
        print("[learn_neko] 后台学习模式, 每 %ds 一次 (Ctrl+C 停)" % a.interval)
        while True:
            try:
                learn_once(verbose=True)
            except Exception as e:
                print("[learn_neko] err:", e)
            time.sleep(a.interval)
    else:
        learn_once(verbose=True)


if __name__ == "__main__":
    main()
