"""
小焦 · 手动投喂脚本
功能：读取 ai_generated.txt 中的数据，批量写入训练池
用途：我（DeepSeek）生成数据 → 你保存到 ai_generated.txt → 运行此脚本投喂
"""

import os

AI_DATA_FILE = "ai_generated.txt"
TRAIN_POOL_FILE = "training_data_pool.txt"

def feed():
    if not os.path.exists(AI_DATA_FILE):
        print("❌ 没有找到 ai_generated.txt，请先把我生成的数据保存到这个文件。")
        return

    with open(AI_DATA_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    valid_lines = [l.strip() for l in lines if l.strip()]
    if not valid_lines:
        print("❌ ai_generated.txt 是空的，请先填入数据。")
        return

    with open(TRAIN_POOL_FILE, "a", encoding="utf-8") as f:
        for line in valid_lines:
            f.write(line + "\n")

    print(f"✅ 投喂完成！共写入 {len(valid_lines)} 条数据到训练池。")
    print(f"📊 当前训练池总数据量：请查看 training_data_pool.txt")

if __name__ == "__main__":
    feed()