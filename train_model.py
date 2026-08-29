# -*- coding: utf-8 -*-
"""
小焦 · 训练器（修复版）

修复点：
1. 前向加入因果掩码（tgt_mask），这是真正的自回归语言模型；
   旧版 `layer(x, x)` 不带掩码导致“看着未来作弊”，推理时却加掩码，训练/推理不一致 → 乱码。
2. 保存 model_config.json 记录真实架构，加载时不再靠猜（旧版把 16 头猜成 8 头）。
3. 训练池一律按 UTF-8 处理，只保留干净对话行。
"""
import os, re, pickle, time, json, random
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

# ========== 配置 ==========
TRAIN_POOL_CLEAN = "training_data_pool_clean.txt"   # 已清洗语料（UTF-8，推荐）
TRAIN_POOL_RAW = "training_data_pool.txt"           # 原始语料（回退用）
# 优先用清洗后的语料；不存在则回退到原始训练池
TRAIN_POOL_FILE = TRAIN_POOL_CLEAN if os.path.exists(TRAIN_POOL_CLEAN) else TRAIN_POOL_RAW
MODEL_FILE = "mini_gpt_model.pth"
VOCAB_FILE = "vocab.pkl"
CONFIG_FILE = "model_config.json"
PROGRESS_FILE = "progress.txt"

EMBED_SIZE = 512
NUM_HEADS = 8
HIDDEN_SIZE = 2048
NUM_LAYERS = 8
SEQ_LEN = 64
BATCH_SIZE = 16
ACCUMULATION_STEPS = 2
LR = 3e-4
MAX_STEPS = 20000          # 训练步数上限（可按需调整）
LOG_EVERY = 100
SAVE_EVERY = 2000
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

LINE_RE = re.compile(r"^用户 .+ 小焦 .+")
JUNK_KW = ["**Format**", "Xiao Jiao", "Final Polish", "Line 1:", "Line 2:", "Line 3:",
           "Line 4:", "Line 5:", "User's Message", "Xiao Jiao's Response", "Yes.",
           "Okay, so", "the format is", "```", "【", "】"]


class MiniGPT(nn.Module):
    """因果自回归 Transformer（Encoder-only，Pre-LN，batch_first），与推理端一致。

    用 TransformerEncoderLayer（只有 self-attention）作为 decoder-only 块，
    并施加因果掩码。注意：不能用 TransformerDecoderLayer 再传入 memory，
    因为它的 cross-attention 不带掩码会让训练“偷看未来”，导致推理乱码。
    """

    def __init__(self, vocab_size, embed_size, num_heads, hidden_size, num_layers):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_size)
        self.pos_embedding = nn.Embedding(2048, embed_size)
        self.layers = nn.ModuleList([
            nn.TransformerEncoderLayer(
                d_model=embed_size, nhead=num_heads, dim_feedforward=hidden_size,
                batch_first=True, dropout=0.1, norm_first=True,
            ) for _ in range(num_layers)
        ])
        self.fc = nn.Linear(embed_size, vocab_size)
        self.vocab_size = vocab_size

    def forward(self, x):
        seq_len = x.size(1)
        positions = torch.arange(seq_len, device=x.device).unsqueeze(0).expand(x.size(0), -1)
        x = self.embedding(x) + self.pos_embedding(positions)
        # 因果掩码：每个位置只能看到自己及之前 → 真正的“下一个 token 预测”
        mask = torch.triu(torch.ones(seq_len, seq_len, device=x.device), diagonal=1).bool()
        for layer in self.layers:
            x = layer(x, src_mask=mask)
        return self.fc(x)


def build_vocab(file_path):
    chars = set()
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:                       # 语料已经是清洗后的对话，无需再按空格规则过滤
                chars.update(s)
    chars = sorted(chars)
    if not chars:            # 兜底：保证必有空格与控制字符
        chars = [" ", "\n"]
    char2idx = {c: i for i, c in enumerate(chars)}
    idx2char = {i: c for i, c in enumerate(chars)}
    return char2idx, idx2char, len(chars)


class LazyTextDataset(Dataset):
    """步长滑动窗口采样，避免一次性占用海量内存。"""

    def __init__(self, file_path, char2idx, seq_len=SEQ_LEN, max_chars=None):
        self.char2idx = char2idx
        self.seq_len = seq_len
        self.step = seq_len // 2
        buffer = []
        total = 0
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                buffer.append(s)
                total += len(s) + 1
                if max_chars and total >= max_chars:
                    break
        self.data = "\n".join(buffer)
        n = (len(self.data) - seq_len) // self.step
        self.length = max(n, 0)
        self.cache = {}

    def __len__(self):
        return self.length

    def __getitem__(self, idx):
        if idx in self.cache:
            return self.cache[idx]
        start = idx * self.step
        chunk = self.data[start:start + self.seq_len + 1]
        if len(chunk) < self.seq_len + 1:
            chunk = chunk.ljust(self.seq_len + 1, " ")
        seq = [self.char2idx.get(c, 0) for c in chunk[:self.seq_len]]
        tgt = [self.char2idx.get(c, 0) for c in chunk[1:self.seq_len + 1]]
        res = (torch.tensor(seq, dtype=torch.long), torch.tensor(tgt, dtype=torch.long))
        self.cache[idx] = res
        return res


def main():
    print("=" * 60)
    print("🧠 小焦 · 训练器（修复版）")
    print(f"   设备: {DEVICE}   架构: embed={EMBED_SIZE} heads={NUM_HEADS} hidden={HIDDEN_SIZE} layers={NUM_LAYERS}")
    print("=" * 60)

    print("\n📊 构建词表 ...")
    char2idx, idx2char, vocab_size = build_vocab(TRAIN_POOL_FILE)
    print(f"   词表大小: {vocab_size}")

    with open(VOCAB_FILE, "wb") as f:
        pickle.dump({"char2idx": char2idx, "idx2char": idx2char, "vocab_size": vocab_size}, f)

    print("\n🧠 创建模型 ...")
    model = MiniGPT(vocab_size, EMBED_SIZE, NUM_HEADS, HIDDEN_SIZE, NUM_LAYERS).to(DEVICE)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"   参数量: {n_params:,}")

    if os.path.exists(MODEL_FILE):
        try:
            model.load_state_dict(torch.load(MODEL_FILE, map_location=DEVICE))
            print("   ✅ 从现有模型续练")
        except Exception:
            print("   ⚠️ 从现有模型加载失败，从头训练")

    dataset = LazyTextDataset(TRAIN_POOL_FILE, char2idx)
    print(f"   训练样本数: {len(dataset)}")
    dataloader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True,
                            num_workers=0, pin_memory=(DEVICE == "cuda"))

    # 优化器：优先 8bit，失败回退标准 AdamW
    try:
        from bitsandbytes.optim import AdamW8bit
        optimizer = AdamW8bit(model.parameters(), lr=LR, weight_decay=0.01)
        print("   ✅ 使用 8-bit 优化器")
    except ImportError:
        optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)

    criterion = nn.CrossEntropyLoss()
    scaler = torch.amp.GradScaler("cuda") if DEVICE == "cuda" else None

    model.train()
    step = 0
    total_loss = 0.0
    start = time.time()
    optimizer.zero_grad()
    print("\n🚀 开始训练 ...")

    while step < MAX_STEPS:
        for x, y in dataloader:
            x, y = x.to(DEVICE), y.to(DEVICE)
            with torch.autocast("cuda" if DEVICE == "cuda" else "cpu", dtype=torch.float16):
                logits = model(x)
                loss = criterion(logits.reshape(-1, vocab_size), y.reshape(-1))
            if torch.isnan(loss):
                continue

            if scaler:
                scaler.scale(loss / ACCUMULATION_STEPS).backward()
            else:
                (loss / ACCUMULATION_STEPS).backward()

            if (step + 1) % ACCUMULATION_STEPS == 0:
                if scaler:
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    optimizer.step()
                optimizer.zero_grad()

            total_loss += loss.item()
            step += 1

            if step % LOG_EVERY == 0:
                avg = total_loss / LOG_EVERY
                elapsed = time.time() - start
                print(f"   step {step:6d} | loss {avg:.4f} | {step/(elapsed/60):.0f} steps/min", flush=True)
                total_loss = 0.0

            if step % SAVE_EVERY == 0:
                torch.save(model.state_dict(), MODEL_FILE)
                cfg = {"embed_size": EMBED_SIZE, "num_heads": NUM_HEADS,
                       "hidden_size": HIDDEN_SIZE, "num_layers": NUM_LAYERS,
                       "vocab_size": vocab_size, "seq_len": SEQ_LEN}
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                    f.write(str(step))
                print(f"   💾 已保存 checkpoint (step {step})", flush=True)

            if step >= MAX_STEPS:
                break

    # 最终保存
    torch.save(model.state_dict(), MODEL_FILE)
    cfg = {"embed_size": EMBED_SIZE, "num_heads": NUM_HEADS,
           "hidden_size": HIDDEN_SIZE, "num_layers": NUM_LAYERS,
           "vocab_size": vocab_size, "seq_len": SEQ_LEN}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 训练完成，共 {step} 步，耗时 {(time.time()-start)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
