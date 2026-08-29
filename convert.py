# -*- coding: utf-8 -*-
"""
把 LCCC 多轮语料 JSON 转换为训练池文本（修正版）。

修复点：
1. 统一用 UTF-8 读取，避免在中文 Windows 上被误按 GBK 读取导致乱码。
2. LCCC 的词之间带空格（如「你 去 那儿」），直接学会让模型学着吐空格；
   这里按「中文/全角标点」之间的空格去掉，得到自然连续的中文。
3. 正确按「偶数下标=用户、奇数下标=小焦」配对，跳过空的一句话。
输出格式：每行 `用户 <用户的话> 小焦 <小焦的回答>`，UTF-8。
"""
import json, os, re

FILES = ["LCCC-base_train.json", "LCCC-base_test.json", "LCCC-base_valid.json"]
OUTPUT = "training_data_pool.txt"

# 中文汉字 + 全角标点
CJK = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
NO_CJK_SPACE = re.compile(r"(?<=[" + CJK + r"])\s+(?=[" + CJK + r"])")


def norm(text):
    """去掉中文/全角标点之间的空格，得到连续中文。"""
    return NO_CJK_SPACE.sub("", text)


def main():
    total = 0
    with open(OUTPUT, "w", encoding="utf-8") as out:
        for path in FILES:
            if not os.path.exists(path):
                print(f"⚠️ 跳过不存在: {path}")
                continue
            print(f"📂 处理: {path}")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except UnicodeDecodeError:
                with open(path, "r", encoding="gbk") as f:
                    data = json.load(f)

            count = 0
            for conv in data:
                if not isinstance(conv, list):
                    continue
                # LCCC 的 turn 交替出现：偶数下标是用户，奇数下标是小焦
                for i in range(0, len(conv) - 1, 2):
                    user = norm(conv[i].strip())
                    reply = norm(conv[i + 1].strip())
                    if user and reply:
                        out.write(f"用户 {user} 小焦 {reply}\n")
                        count += 1
            print(f"   ✅ 写入 {count} 条")
            total += count
    print(f"✅ 转换完成，共 {total} 条，已写入 {OUTPUT}")


if __name__ == "__main__":
    main()
