"""
小焦 · 多轮对话蒸馏版（适配训练数据格式）
生成的数据格式与 ai_generated.txt 一致：
用户 <说的话> 小焦 <说的话>
"""

import os
import time
import json
import re
import requests
import pickle
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
import random

LLAMA_API = os.environ.get("LLAMA_API", "http://127.0.0.1:9292/completion")
TRAIN_POOL_FILE = "training_data_pool.txt"
MODEL_FILE = "mini_gpt_model.pth"
VOCAB_FILE = "vocab.pkl"
LOG_FILE = "massive_distill.log"

SEED_TOPICS = [
    "日常聊天", "打招呼", "自我介绍", "闲聊", "心情", "生活",
    "编程入门", "Python基础", "数据结构", "算法", "计算机网络",
    "前端开发", "后端开发", "数据库", "Linux", "Git",
    "人工智能基础", "机器学习", "深度学习", "神经网络", "自然语言处理",
    "角色扮演", "剧本创作", "对话设计", "性格塑造", "场景演绎",
    "故事推进", "情感表达", "风格模仿", "叙事节奏", "冲突设计",
    "科幻角色", "奇幻角色", "侦探角色", "侠客角色",
]

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def generate_conversation(topic):
    """
    生成多轮对话，格式为：
    用户 你好 小焦 你好呀！今天想聊点什么呢？
    用户 你会什么 小焦 我会聊天、回答问题、陪你角色扮演
    """
    prompt = f"""请围绕「{topic}」这个主题，生成一段3-5轮的自然对话。

对话格式要求：
- 每行格式为：用户 [用户说的话] 小焦 [小焦的回答]
- 对话要自然、口语化、真实
- 话题要连续，像真实聊天一样有来有回
- 每行用空格分隔「用户」「用户说的话」「小焦」「小焦的回答」

示例：
用户 你好 小焦 你好呀！今天想聊点什么呢？
用户 你会什么 小焦 我会聊天、回答问题、陪你角色扮演，你问什么我都能试试
用户 那你会写代码吗 小焦 会一点Python，你想写什么功能？

请直接输出对话内容，不要输出其他任何内容。"""

    try:
        resp = requests.post(LLAMA_API, json={
            "prompt": prompt,
            "n_predict": 2000,
            "temperature": 0.7,
            "stop": ["\n\n\n"]
        }, timeout=120)
        if resp.status_code != 200:
            return []
        content = resp.json().get("content", "")

        lines = content.split("\n")
        conversation = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 匹配格式：用户 xxx 小焦 xxx
            if "用户" in line and "小焦" in line:
                # 用空格分割，取用户部分和小焦部分
                parts = line.split("小焦")
                if len(parts) == 2:
                    user_part = parts[0].replace("用户", "").strip()
                    xiaojiao_part = parts[1].strip()
                    if user_part and xiaojiao_part:
                        conversation.append(f"用户 {user_part} 小焦 {xiaojiao_part}")

        if len(conversation) < 3:
            return []
        return conversation

    except Exception as e:
        log(f"生成对话失败：{e}")
        return []

class MiniGPT(nn.Module):
    def __init__(self, vocab_size=104, embed_size=128, num_heads=4, hidden_size=256, num_layers=4):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_size)
        self.pos_embedding = nn.Embedding(1024, embed_size)
        self.layers = nn.ModuleList([
            nn.TransformerDecoderLayer(d_model=embed_size, nhead=num_heads,
                                       dim_feedforward=hidden_size, batch_first=True)
            for _ in range(num_layers)
        ])
        self.fc = nn.Linear(embed_size, vocab_size)
        self.vocab_size = vocab_size

    def forward(self, x):
        seq_len = x.size(1)
        positions = torch.arange(seq_len, device=x.device).unsqueeze(0).expand(x.size(0), -1)
        x = self.embedding(x) + self.pos_embedding(positions)
        for layer in self.layers:
            x = layer(x, x)
        return self.fc(x)

def train_model():
    if not os.path.exists(VOCAB_FILE) or not os.path.exists(TRAIN_POOL_FILE):
        log("跳过训练：词表或训练池不存在")
        return

    with open(VOCAB_FILE, "rb") as f:
        vocab = pickle.load(f)

    with open(TRAIN_POOL_FILE, "r", encoding="utf-8") as f:
        raw = f.read()

    if len(raw) < 100:
        log("跳过训练：训练数据不足")
        return

    char2idx = vocab["char2idx"]
    vocab_size = vocab["vocab_size"]
    seq_len = 32
    data = [char2idx.get(c, 0) for c in raw if c in char2idx]
    if len(data) < seq_len:
        log("跳过训练：数据太短")
        return

    class TextDataset(Dataset):
        def __init__(self, data, seq_len):
            self.inputs = []
            self.targets = []
            for i in range(0, len(data) - seq_len, 1):
                self.inputs.append(data[i:i+seq_len])
                self.targets.append(data[i+1:i+seq_len+1])
            self.inputs = torch.tensor(self.inputs, dtype=torch.long)
            self.targets = torch.tensor(self.targets, dtype=torch.long)
        def __len__(self):
            return len(self.inputs)
        def __getitem__(self, idx):
            return self.inputs[idx], self.targets[idx]

    dataset = TextDataset(data, seq_len)
    dataloader = DataLoader(dataset, batch_size=32, shuffle=True)
    model = MiniGPT(vocab_size=vocab_size)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)

    log("🧠 开始训练小模型...")
    for epoch in range(3):
        total_loss = 0
        for x, y in dataloader:
            optimizer.zero_grad()
            out = model(x)
            loss = criterion(out.reshape(-1, vocab_size), y.reshape(-1))
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        log(f"  Epoch {epoch+1}, Loss: {total_loss/len(dataloader):.4f}")

    torch.save(model.state_dict(), MODEL_FILE)
    log(f"✅ 模型已保存到 {MODEL_FILE}")

    try:
        test_model = MiniGPT(vocab_size=vocab_size)
        test_model.load_state_dict(torch.load(MODEL_FILE, map_location="cpu"))
        log("✅ 模型文件校验通过")
    except Exception as e:
        log(f"❌ 模型文件校验失败：{e}")

    with open(TRAIN_POOL_FILE, "w", encoding="utf-8") as f:
        f.write("")
    log("🗑️ 训练池已清空")

def main():
    print("""
╔═══════════════════════════════════════════╗
║   小焦 · 多轮对话蒸馏版                  ║
║   生成格式：用户 xxx 小焦 xxx             ║
║   按 Ctrl+C 停止运行                     ║
╚═══════════════════════════════════════════╝
    """)
    log("启动多轮对话蒸馏模式")
    total_conv = 0
    total_rounds = 0
    topic_pool = SEED_TOPICS.copy()
    random.shuffle(topic_pool)

    while True:
        try:
            if not topic_pool:
                topic_pool = SEED_TOPICS.copy()
                random.shuffle(topic_pool)

            topic = topic_pool.pop(0)
            log(f"📚 当前主题：{topic}")

            conversation = generate_conversation(topic)
            if conversation:
                with open(TRAIN_POOL_FILE, "a", encoding="utf-8") as f:
                    for line in conversation:
                        f.write(line + "\n")
                total_conv += len(conversation)
                total_rounds += 1
                log(f"  ✅ 生成 {len(conversation)} 条对话 (累计: {total_conv} 条, {total_rounds} 轮)")

                if total_rounds % 10 == 0:
                    train_model()
            else:
                log(f"  ❌ 未能生成对话，跳过此主题")

            time.sleep(2)

        except KeyboardInterrupt:
            log("\n用户中断，退出")
            break
        except Exception as e:
            log(f"主循环异常: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()