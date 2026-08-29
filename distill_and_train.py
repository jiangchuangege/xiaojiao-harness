"""
小焦 · 蒸馏训练闭环
功能：从知识库提取内容 → 调用本地大模型生成问答 → 训练小模型
"""

import requests
import json
import re
import os
import pickle
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

# ========== 配置 ==========
LLAMA_API = os.environ.get("LLAMA_API", "http://127.0.0.1:8080/completion")
KNOWLEDGE_FILE = "xiaojiao_knowledge.txt"
MEMORY_FILE = "xiaojiao_memory.txt"
TRAIN_POOL_FILE = "training_data_pool.txt"
MODEL_FILE = "mini_gpt_model.pth"
VOCAB_FILE = "vocab.pkl"

# ========== 1. 读取已有知识 ==========
def load_knowledge():
    text = ""
    if os.path.exists(KNOWLEDGE_FILE):
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            text += f.read()
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            text += f.read()
    chunks = [c.strip() for c in re.split(r'-{10,}', text) if len(c.strip()) > 50]
    return chunks

# ========== 2. 生成问答对（自动处理思考过程） ==========
def generate_qa(chunk):
    prompt = f"""根据以下内容生成5个问答对（问题+答案），格式为JSON数组：
内容：{chunk[:600]}
输出格式：[{{"q": "问题1", "a": "答案1"}}, ...]
只输出JSON数组，不要其他内容。"""
    
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "prompt": prompt,
            "n_predict": 1500,
            "temperature": 0.1
        }
        resp = requests.post(LLAMA_API, json=payload, headers=headers, timeout=90)
        if resp.status_code != 200:
            return []
        data = resp.json()
        content = data.get("content", "")
        
        # ----- 核心：提取最后一个完整的 JSON 数组 -----
        all_json = []
        start = 0
        while True:
            start = content.find('[', start)
            if start == -1:
                break
            depth = 0
            end = start
            for i in range(start, len(content)):
                if content[i] == '[':
                    depth += 1
                elif content[i] == ']':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if depth != 0:
                start += 1
                continue
            json_str = content[start:end]
            try:
                qa_list = json.loads(json_str)
                if isinstance(qa_list, list) and len(qa_list) > 0:
                    all_json.append(qa_list)
            except:
                pass
            start = end
        
        if all_json:
            return all_json[-1]
        
        # 备用正则提取
        pattern = r'\[\s*\{[^{}]*"q"\s*:\s*"[^"]*"\s*,\s*"a"\s*:\s*"[^"]*"\s*\}\s*\]'
        matches = re.findall(pattern, content, re.DOTALL)
        if matches:
            try:
                return json.loads(matches[-1])
            except:
                pass
        
        print("⚠️ 未找到有效 JSON 数组")
        return []
        
    except Exception as e:
        print(f"生成失败：{e}")
        return []

# ========== 3. 小模型定义（与训练时一致） ==========
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

# ========== 4. 训练小模型 ==========
def train_model():
    if not os.path.exists(VOCAB_FILE) or not os.path.exists(TRAIN_POOL_FILE):
        print("训练数据或词表缺失，跳过训练")
        return
    with open(VOCAB_FILE, "rb") as f:
        vocab = pickle.load(f)
    with open(TRAIN_POOL_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    if len(raw) < 100:
        print("训练数据不足，跳过训练")
        return
    char2idx = vocab["char2idx"]
    idx2char = vocab["idx2char"]
    vocab_size = vocab["vocab_size"]
    text = raw
    seq_len = 32
    data = [char2idx.get(c, 0) for c in text if c in char2idx]
    if len(data) < seq_len:
        print("数据太短，跳过训练")
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
    for epoch in range(2):
        total_loss = 0
        for x, y in dataloader:
            optimizer.zero_grad()
            out = model(x)
            loss = criterion(out.reshape(-1, vocab_size), y.reshape(-1))
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"Epoch {epoch+1}, Loss: {total_loss/len(dataloader):.4f}")
    torch.save(model.state_dict(), MODEL_FILE)
    print("✅ 模型训练完成，已保存为", MODEL_FILE)

# ========== 5. 主流程 ==========
def main():
    print("🧠 开始蒸馏流程...")
    chunks = load_knowledge()
    if not chunks:
        print("没有足够的知识源，请先积累记忆或知识")
        return
    print(f"📚 找到 {len(chunks)} 个知识片段")
    all_qa = []
    for i, chunk in enumerate(chunks[:5]):
        print(f"  处理片段 {i+1}/{min(5, len(chunks))}...")
        qa = generate_qa(chunk)
        if qa:
            all_qa.extend(qa)
            print(f"    ✅ 生成 {len(qa)} 条")
    if all_qa:
        with open(TRAIN_POOL_FILE, "a", encoding="utf-8") as f:
            for item in all_qa:
                f.write(f"{item['q']} {item['a']}\n")
        print(f"✅ 共生成 {len(all_qa)} 条问答，已存入训练池")
        train_model()
    else:
        print("没有生成新的训练数据")

if __name__ == "__main__":
    main()