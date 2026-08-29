# 小焦 · 轻量向量库（Embedding + 余弦相似度，零外部依赖）
# 对标 Chroma/FAISS 的简化本地实现：把学过的"功能用法/反思"向量化存起来，检索按余弦取最像。
import json, os, hashlib

VS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_vec.json")
DIM = 256


def _h(s):
    """确定性哈希(跨进程稳定)。"""
    x = 0
    for c in s:
        x = (x * 31 + ord(c)) & 0xFFFF
    return x


def embed(text):
    """字符 2/3-gram 计数 → 归一化向量。"""
    t = (text or "").lower()
    v = [0.0] * DIM
    for n, w in ((2, 1.0), (3, 0.6)):
        for i in range(len(t) - n + 1):
            v[_h(t[i:i + n]) % DIM] += w
    norm = math_sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def math_sqrt(x):
    return x ** 0.5


def _load():
    try:
        return json.load(open(VS, encoding="utf-8"))
    except Exception:
        return {"entries": []}


def _save(d):
    os.makedirs(os.path.dirname(VS), exist_ok=True)
    json.dump(d, open(VS, "w", encoding="utf-8"), ensure_ascii=False)


def add(text, tag="", doc_id=None):
    d = _load()
    eid = doc_id or hashlib.md5(text.encode()).hexdigest()[:8]
    d["entries"] = [x for x in d["entries"] if x["id"] != eid]
    d["entries"].append({"id": eid, "text": text, "tag": tag, "v": embed(text)})
    _save(d)
    return eid


def search(query, k=3, threshold=0.13):
    d = _load()
    q = embed(query)
    hits = [(sum(x * y for x, y in zip(q, e["v"])), e["text"], e.get("tag", "")) for e in d["entries"]]
    hits.sort(key=lambda x: -x[0])
    top = hits[:k]
    best = top[0] if top else (0.0, "", "")
    return {"top": top, "best": best, "hit": bool(top and best[0] >= threshold)}


def count():
    return len(_load()["entries"])
