# -*- coding: utf-8 -*-
"""
小焦 · 交互入口（修复版 + 思考层）

修复点：
1. 从 model_config.json 读取真实架构，不再把 16 头猜成 8 头。
2. 前向使用与训练一致的因果掩码（src_mask），训练/推理对齐。
3. 用「用户 + 输入 + 小焦」的提示格式生成，与语料格式一致。
4. 生成采用 温度采样 + 重复惩罚 + top-k，并正确截断回复。
5. 控制台强制 UTF-8，避免中文乱码。

思考层（让回答"对得上"而非瞎回）：
- 先做「语义检索」：从训练语料里找出与用户输入最相似的历史问题，直接返回它的回答
  （grounded / relevant，避免模型乱接龙）。
- 检索命中率低时才退回模型自由生成。
"""
import os, re, json, pickle, sys
import torch
import torch.nn as nn
import requests

MODEL_PATH = "mini_gpt_model.pth"
VOCAB_PATH = "vocab.pkl"
CONFIG_PATH = "model_config.json"
MEMORY_PATH = "xiaojiao_memory.txt"

# 检索用的历史问答库（优先清洗版，其次原始训练池）
POOL_PATH = "training_data_pool_clean.txt"
if not os.path.exists(POOL_PATH):
    POOL_PATH = "training_data_pool.txt"
RETRIEVE_MAX_PAIRS = 300000       # 索引的问答对上限（越大越准，启动/查询略慢）
RETRIEVE_THRESHOLD = 0.15         # 闲聊检索的相似度阈值（低于该值则退回生成）
QUESTION_THRESHOLD = 0.70         # 问题只有近乎“原话命中”才用语料；否则联网思考

MAX_NEW_TOKENS = 40
TEMPERATURE = 0.8
TOP_K = 50
REPETITION_PENALTY = 1.2
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# 控制台 UTF-8（Windows）
try:
    os.system("")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class MiniGPT(nn.Module):
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
        mask = torch.triu(torch.ones(seq_len, seq_len, device=x.device), diagonal=1).bool()
        for layer in self.layers:
            x = layer(x, src_mask=mask)
        return self.fc(x)


def load_model():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(VOCAB_PATH):
        print("❌ 缺少 mini_gpt_model.pth 或 vocab.pkl，请先运行 train_model.py 训练。")
        sys.exit(1)

    with open(VOCAB_PATH, "rb") as f:
        vocab = pickle.load(f)
    char2idx, idx2char = vocab["char2idx"], vocab["idx2char"]
    vocab_size = vocab.get("vocab_size", len(char2idx))

    state = torch.load(MODEL_PATH, map_location=DEVICE)

    # 优先读取训练时保存的真实架构
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        embed_size = cfg["embed_size"]
        num_heads = cfg["num_heads"]
        hidden_size = cfg["hidden_size"]
        num_layers = cfg["num_layers"]
    else:
        # 兜底：从权重推断（旧版无 config）
        embed_size = state["embedding.weight"].shape[1]
        num_layers = max(int(k.split(".")[1]) for k in state if k.startswith("layers.")) + 1
        hidden_size = state["layers.0.linear1.weight"].shape[0]
        ip = state["layers.0.self_attn.in_proj_weight"]
        head_dim = 64
        num_heads = (ip.shape[1] // head_dim)

    model = MiniGPT(vocab_size, embed_size, num_heads, hidden_size, num_layers).to(DEVICE)
    model.load_state_dict(state, strict=True)
    model.eval()
    print(f"📊 词表: {vocab_size} | 架构: embed={embed_size} heads={num_heads} layers={num_layers}")
    return model, char2idx, idx2char


@torch.no_grad()
def generate(model, idx, idx2char, max_new=MAX_NEW_TOKENS):
    """温度采样 + top-k + 重复惩罚 的自回归生成。

    在遇到换行或新一轮「用户/小焦」时停止，保证只返回小焦的一句话。
    """
    model.eval()
    gen_tokens = []
    gen_text = ""
    for _ in range(max_new):
        ic = idx if idx.size(1) <= 512 else idx[:, -512:]
        logits = model(ic)[0, -1, :] / TEMPERATURE

        for t in gen_tokens:                      # 重复惩罚
            logits[t] = logits[t] / REPETITION_PENALTY

        if logits.size(0) > TOP_K:                # Top-k 过滤
            kth = torch.topk(logits, TOP_K)[0][-1]
            logits[logits < kth] = -float("inf")

        probs = torch.softmax(logits, dim=-1)
        nxt = torch.multinomial(probs, 1).item()
        ch = idx2char.get(nxt, "")
        gen_tokens.append(nxt)
        gen_text += ch
        idx = torch.cat([idx, torch.tensor([[nxt]], dtype=torch.long, device=idx.device)], dim=1)

        # 停止条件：换行，或开始新一轮（模型学会了「用户…小焦…」的格式）
        if ch in ("\n", "\r") or gen_text.rstrip().endswith(("用户", "小焦")):
            break
        if len(gen_text) > 0 and gen_text[-1] in ("户", "焦") and gen_text.rstrip().endswith(("用户", "小焦")):
            break
    return gen_text


# ========== 思考层：语义检索（grounded answering） ==========
_PAIR_RE = re.compile(r"^用户(.+?)小焦(.+)$")


def _bigrams(s):
    """字符二元的集合（比单字符更能区分“相关”与“只是都含‘什么’这类常见词”）。"""
    return frozenset(s[i:i + 2] for i in range(len(s) - 1))


def load_reply_index(path=POOL_PATH, max_pairs=RETRIEVE_MAX_PAIRS):
    """从问答库中构建 (用户二字符集, 用户话, 小焦回答) 索引，用于语义检索。"""
    index = []
    if not os.path.exists(path):
        return index
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = _PAIR_RE.match(line.strip())
            if not m:
                continue
            user, reply = m.group(1).strip(), m.group(2).strip()
            if user and reply:
                index.append((_bigrams(user), user, reply))
            if len(index) >= max_pairs:
                break
    return index


def retrieve_reply(query, index, threshold=RETRIEVE_THRESHOLD):
    """① 先用【向量库】(self_learn/vstore.py，含反思+功能用法)语义检索，命中即复用；
       ② 否则回退字符二元组重叠检索。让"小脑"用上向量库、越用越强。"""
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "self_learn"))
        import vstore
        vr = vstore.search(query, k=1, threshold=0.13)
        if vr["hit"] and vr["best"][1]:
            return vr["best"][1], vr["best"][0]
    except Exception:
        pass
    q = _bigrams(query)
    best_reply, best_score = None, 0.0
    for user_bi, user, reply in index:
        inter = len(q & user_bi)
        if inter <= 0:
            continue
        score = 2 * inter / (len(q) + len(user_bi))
        if score > best_score:
            best_score, best_reply = score, reply
    if best_reply is not None and best_score >= threshold:
        return best_reply, best_score
    return None, 0.0


# ========== 联网搜索 ==========
def _clean_html(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&#\d+;|&[a-z]+;", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def web_search(query, num=6):
    """免密钥联网搜索（Bing 中文，失败回退 Sogou）。

    返回 [(标题, 内容)] 列表 —— 只给内容标题，不给网址/网站名。
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    engines = [("https://cn.bing.com/search?q=", r'<li class="b_algo"[^>]*>(.*?)</li>'),
               ("https://www.sogou.com/web?query=", r'<div class="vrwrap"[^>]*>(.*?)</div>')]
    out = []
    seen = set()
    for base, block_re in engines:
        try:
            r = requests.get(base + requests.utils.quote(query), headers=headers, timeout=12)
            if r.status_code != 200:
                continue
            for block in re.findall(block_re, r.text, re.S):
                # 标题：优先取 <h2><a>，去掉“站点 ›”面包屑
                h2 = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.S)
                if not h2:
                    h2 = re.search(r'<a[^>]*>(.*?)</a>', block, re.S)
                title = _clean_html(h2.group(1)) if h2 else ""
                if "›" in title:
                    title = title.split("›")[-1].strip()
                # 内容：取段落摘要
                p = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
                content = _clean_html(p.group(1)) if p else ""
                if not content:
                    content = title
                title = title or content[:24]
                if len(content) > 30 and content[:40] not in seen:
                    seen.add(content[:40])
                    out.append((title, content))
                if len(out) >= num:
                    break
        except Exception:
            continue
        if len(out) >= num:
            break
    return out[:num]


def _sentences(text):
    """把一段文字切成句子（以中文标点/换行断句），过滤过短碎片。"""
    parts = re.split(r"(?<=[。！？!?；;])|[\n\r]+", text)
    return [s.strip() for s in parts if len(s.strip()) >= 8]


def synthesize(evidence, query, max_out=4):
    """从多份检索内容中“想”出该返回什么：抽取与问题最相关的关键句，过滤噪音。

    返回 (标题, [关键句])。
    """
    title = evidence[0][0] if evidence else query
    qset = set(query)
    scored = []
    for _, content in evidence:
        for s in _sentences(content):
            overlap = len(qset & set(s))
            if overlap <= 0:
                continue
            # 相关度：查询字符重合越多、句子越完整越优先
            score = overlap / max(1.0, len(s) ** 0.5)
            scored.append((score, s))
    # 按相关度排序、去重，选最相关的几句
    seen = set()
    chosen = []
    for _, s in sorted(scored, key=lambda x: -x[0]):
        key = s[:24]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(s)
        if len(chosen) >= max_out:
            break
    return title, chosen


def llm_available():
    """本地大模型（教师，:8080）是否在线 —— 在线时可进行真正的“深度思考”。"""
    try:
        return requests.get("http://127.0.0.1:%s/health" % os.environ.get("LLAMA_PORT", "8080"), timeout=3).status_code == 200
    except Exception:
        return False


def llm_ask(prompt):
    """调用本地大模型进行综合/推理（OpenAI 兼容 /completion）。"""
    try:
        r = requests.post("http://127.0.0.1:%s/completion" % os.environ.get("LLAMA_PORT", "8080"),
                          json={"prompt": prompt, "n_predict": 512, "temperature": 0.6},
                          timeout=90)
        if r.status_code == 200:
            return r.json().get("content", "").strip()
    except Exception:
        pass
    return ""


def deep_think(query, index):
    """联网检索 → 思考该返还什么/不返还什么 → 组织成一条干净的答案。

    在线的大模型（老师）会做真正的推理综合；离线则退化为你问题最相关的关键句抽取。
    只输出内容与标题，不给网址/网站名。
    """
    evidence = web_search(query, num=6)
    if not evidence:
        return f"🔎 暂时没能搜到关于「{query}」的可靠信息（请检查网络后重试）。"

    # ① 本地大模型在线 → 真·推理综合
    context = "\n".join(f"{t}：{c}" for t, c in evidence[:4])
    if llm_available():
        prompt = (f"用户问题：{query}\n\n联网检索到的资料：\n{context[:1200]}\n\n"
                  f"请综合分析以上资料，用中文回答：先用一句话概括，再分点讲清要点。"
                  f"只保留与问题直接相关的关键信息，丢弃无关内容。这是给用户看的最终答案，不要提及网站或网址。")
        answer = llm_ask(prompt)
        if answer:
            return "🤔 综合联网信息，我的思考：\n" + answer.strip()

    # ② 离线 → 抽取式“思考”：从多份结果里选出最相关的关键句（该返还/不该返还）
    title, chosen = synthesize(evidence, query, max_out=4)
    lines = [f"🤖 {title}"]
    lines.extend("   · " + c for c in chosen)
    return "\n".join(lines)


_QUESTION_RE = re.compile(r"[?？]|是什么|为什么|怎么回事|怎么办|如何|多少|哪个|哪里|谁|吗$|呢$|嘛$|啥")


def _looks_like_question(q):
    """判断一句话是不是“在问问题”（信息型），用于决定要不要自动联网思考。"""
    return bool(_QUESTION_RE.search(q))


def main():
    model, char2idx, idx2char = load_model()

    # 载入检索索引（用于让回答对得上话）
    print("🔎 加载语义检索索引 ...")
    index = load_reply_index()
    print(f"   已索引问答对: {len(index)}")

    print("\n" + "═" * 46)
    print("  小焦 · 修复版 + 检索思考层 + 联网搜索")
    print("  /搜索 <问题>   联网搜索    /思考 <问题>   联网深度思考")
    print("  输入 exit 退出")
    print("═" * 46 + "\n")

    while True:
        user_input = input("你: ").strip()
        if user_input.lower() in ("exit", "quit", "退出"):
            print("👋 小焦: 再见啦~")
            break
        if not user_input:
            continue

        # ① 联网搜索命令
        if user_input.startswith("/搜索"):
            q = user_input[3:].strip() or user_input.strip()
            res = web_search(q, num=5)
            if not res:
                print("🤖 小焦: 暂时没能联网搜索到结果（请检查网络）。")
                continue
            print(f"🤖 小焦: 关于「{q}」我整理到这些 👇")
            for title, content in res:
                print(f"   · {title}：{content[:140]}")
            continue

        # ② 联网深度思考命令
        if user_input.startswith("/思考"):
            q = user_input[3:].strip() or user_input.strip()
            print("🤖 小焦: 让我联网想想 ...")
            print(deep_think(q, index))
            continue

        # ③ 普通对话：判断是“问问题”还是“闲聊”
        if _looks_like_question(user_input):
            # 问题 → 仅当语料有强相关时才直接用，否则联网思考
            resp, score = retrieve_reply(user_input, index, threshold=QUESTION_THRESHOLD)
            if resp is not None:
                print(f"🤖 小焦: {resp.strip()[:160]}")
                record = resp.strip()[:160]
            else:
                print("🤖 小焦: 让我联网想想 ...")
                answer = deep_think(user_input, index)
                print(answer)
                record = answer.replace("\n", " ")[:80]
        else:
            # 闲聊 → 语料检索或模型生成
            resp, score = retrieve_reply(user_input, index)
            if resp is not None:
                record = resp.strip()[:160]
            else:
                prompt = "用户" + user_input + "小焦"
                ids = [char2idx.get(c, 0) for c in prompt]
                idx = torch.tensor([ids], dtype=torch.long, device=DEVICE)
                record = generate(model, idx, idx2char).strip()
                for sep in ("\n", "用户", "小焦"):
                    if sep in record:
                        record = record.split(sep)[0]
                        break
                record = record.strip()
            print(f"🤖 小焦: {record}")

        # 记录到记忆
        if len(record) > 1:
            try:
                with open(MEMORY_PATH, "a", encoding="utf-8") as f:
                    f.write(f"用户 {user_input} 小焦 {record[:80]}\n")
            except Exception:
                pass


if __name__ == "__main__":
    main()
