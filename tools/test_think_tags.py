# -*- coding: utf-8 -*-
"""自测：`<思考>` 标签必须在**所有出口**都被剥掉。

用户实测的三个现场：
  ① 好友消息里：`<思考> 我需要先理解… </思考> 你好，我是小焦…` —— 两段粘在一起
  ② 思考流水里：「感知层：说「…」→ 意味着「<思考> 我需要先理解…」」 —— 推理被当成"感知到的事"
  ③ 心/记忆里：`（我心里在想：<思考> …）`
根因：`_strip_think` 只认英文 think/thinking/reasoning，不认中文 `<思考>`。
"""
import sys

sys.path.insert(0, ".")

import xiaojiao_app as X


def main():
    cases = [
        ("英文成对", "<think>abc</think>正文", "正文"),
        ("中文成对", "<思考>推理</思考>\n你好呀", "你好呀"),
        ("中文闭标签残留", "答案</思考>", "答案"),
        ("中文未闭合", "<思考>推理没关\n正文照旧", "正文照旧"),
        ("全角尖括号", "＜思考＞推理＜/思考＞正文", None),   # 不要求处理，仅记录
        ("正常文本不动", "今天天气不错。", "今天天气不错。"),
        ("空块", "<思考></思考>", ""),
    ]
    bad = 0
    for name, src, want in cases:
        got = X._strip_think(src)
        if want is None:
            print("  · [%s] 结果=%r（不强制）" % (name, got))
            continue
        ok = (got == want) or (want and want in got and "<思考>" not in got)
        print("  %s [%s] %r → %r" % ("✅" if ok else "❌", name, src[:30], got[:40]))
        bad += 0 if ok else 1

    # 关键：中文标签**不能**再出现在结果里
    for name, src, _ in cases:
        got = X._strip_think(src)
        if "思考>" in got:
            print("  ❌ [%s] 结果里还有标签：%r" % (name, got))
            bad += 1

    # 社会消息/记忆这类出口都要过一遍（源码级确认：调用点存在）
    src_all = open("xiaojiao_app.py", encoding="utf-8").read()
    print("\n源码里 _strip_think 的调用点：%d 处" % src_all.count("_strip_think("))

    print("\n结果：%s" % ("✅ 全过" if bad == 0 else "❌ %d 条失败" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
