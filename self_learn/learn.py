"""
小焦 · 持续学习引擎（小脑跟着大脑学）

核心思路：小脑不强在算力，强在一本「跟着大脑学来的、越来越厚的文本知识」。
每次好的交互 → 把 (用户问题 -> 大脑最终回答) 记进一个不断增长的文本文件；
小脑的检索池同源成长 → 小脑越来越强；必要时可重训小模型。这就是「持续学习 / 自学习」。

不依赖算力，全靠一个文本文件越长越强。

用法（在 self_learn 目录）：
  python learn.py log <用户问题> <大脑回答> [工具结果]    # 记录一次交互
  python learn.py feedback <log_id> good|bad|4星|5星 [用户更正后的回答]
  python learn.py build        # 把「高质量」交互 -> little_brain_knowledge.txt(小脑知识库,越长越强)
                              #   并同步进小脑检索池 training_data_pool_clean.txt
  python learn.py train        # (可选) 用积累的知识重训小模型
"""

import json, os, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(BASE)                                  # 项目根
LOG_DIR = os.path.join(PROJ, "logs")
LOG_FILE = os.path.join(LOG_DIR, "chat_history.jsonl")        # ① 记录层
FEED_FILE = os.path.join(LOG_DIR, "feedback.jsonl")           # ③ 反馈层
KNOW_FILE = os.path.join(BASE, "little_brain_knowledge.txt")  # ★ 小脑知识库(核心:越长越强)
RETRIEVE_POOL = os.path.join(PROJ, "training_data_pool_clean.txt")  # 小脑检索池


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def cmd_log(args):
    """记录一次交互(② 记录层)。args = [用户问题, 大脑/最终回答, 工具轨迹?]
    工具轨迹(功能用法)是可选的 JSON 字符串，如 '[{"tool":"write_file","args":{...}}]'。"""
    user = args[0] if len(args) > 0 else ""
    ans = args[1] if len(args) > 1 else ""
    trace = args[2] if len(args) > 2 else ""   # 功能用法：大脑调用了哪些工具
    if not user:
        print("缺少用户问题"); return
    log_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S%f")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": _now(), "log_id": log_id, "user": user,
                            "brain_output": "", "tool_trace": trace,   # ★ 功能能力点
                            "tool_result": "", "final_reply": ans, "user_feedback": None}, ensure_ascii=False) + "\n")
    print("已记录交互 log_id=", log_id)


def cmd_feedback(args):
    """记录用户反馈(③ 反馈层)。args = [log_id, good|bad|4星|5星, 更正后的回答?]"""
    lid = args[0] if len(args) > 0 else ""
    fb = args[1] if len(args) > 1 else ""
    corr = args[2] if len(args) > 2 else ""
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(FEED_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": _now(), "log_id": lid, "feedback": fb,
                            "corrected_reply": corr}, ensure_ascii=False) + "\n")
    print("已记录反馈:", fb)


def _good(fb, corrected):
    """判定是否「值得学」：用户修正过 / 点赞 / 4星5星。"""
    if corrected:
        return True
    if fb in ("good", "👍"):
        return True
    try:
        if int(str(fb).strip("星")) >= 4:
            return True
    except Exception:
        pass
    return False


def cmd_build(args):
    """④ 数据积累：把高质量交互追加进小脑知识库(越长越强)，并同步检索池。"""
    if not os.path.exists(LOG_FILE):
        print("暂无交互日志"); return
    good = set()
    if os.path.exists(FEED_FILE):
        for ln in open(FEED_FILE, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if _good(r.get("feedback"), r.get("corrected_reply")):
                good.add(r.get("log_id"))
    added = 0
    with open(KNOW_FILE, "a", encoding="utf-8") as out:
        for ln in open(LOG_FILE, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            u = (r.get("user") or "").strip()
            a = ((r.get("corrected_reply") or r.get("final_reply") or "")).strip()
            trace = (r.get("tool_trace") or "").strip()
            if not u or (not a and not trace):
                continue
            if r.get("log_id") in good:
                # 功能用法优先：把「用户要什么 + 大脑怎么用工具解决」记进知识库
                lesson = ("用户 %s 小焦 %s" % (u, a)) if a else ("用户 %s" % u)
                if trace:
                    lesson += " 用工具:%s" % trace
                out.write(lesson + "\n")
                added += 1
    if added:
        # 同步进小脑检索池(让它检索时用得上这些学到的功能用法)
        with open(RETRIEVE_POOL, "a", encoding="utf-8") as pool:
            pool.write(open(KNOW_FILE, encoding="utf-8").read())
    print("本次新增学习样本:", added, "| 小脑知识库累计:", _count(KNOW_FILE), "条")


def _count(fp):
    try:
        return sum(1 for _ in open(fp, encoding="utf-8"))
    except Exception:
        return 0


def cmd_train(args):
    print("⑤ 训练层(可选): 用积累的知识重训小模型 -> 运行 train_model.py 即可(会读训练池)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    args = sys.argv[2:]
    {"log": cmd_log, "feedback": cmd_feedback, "build": cmd_build,
     "train": cmd_train}.get(cmd, lambda a: print("未知命令(参考顶部用法)"))(args)


if __name__ == "__main__":
    main()
