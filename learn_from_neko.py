import os, sys, json, datetime, re
sys.stdout.reconfigure(encoding="utf-8")
try:
    from xiaojiao_log import get_logger
    log = get_logger(__name__)
except Exception:                      # 独立运行时退化为标准 logging
    import logging
    log = logging.getLogger("xiaojiao.learn_from_neko")
ROOT = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(ROOT, "xiaojiao_knowledge_memory.json")
# Steam 版 N.E.K.O 的 YUI 记忆目录(用户偏好/猫娘风格)
STEAM_MEM = os.path.join(os.environ.get("LOCALAPPDATA", ""), "N.E.K.O", "memory", "YUI")

def _load_mem():
    if os.path.exists(MEMORY_FILE):
        try:
            return json.load(open(MEMORY_FILE, encoding="utf-8"))
        except Exception as e:          # 记忆文件损坏/被占用时用空记忆继续，不让整条链路挂掉
            log.warning("读取记忆文件失败（按空记忆继续）: %s", e)
            return {}
    return {}

def _save_mem(m):
    json.dump(m, open(MEMORY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def learn_once(verbose=True):
    mem = _load_mem()
    added = 0
    # 1. facts.json: 用户偏好/需求事实
    facts = os.path.join(STEAM_MEM, "facts.json")
    if os.path.exists(facts):
        try:
            d = json.load(open(facts, encoding="utf-8"))
            items = d if isinstance(d, list) else (d.get("facts") or [])
            for it in items if isinstance(items, list) else []:
                if not isinstance(it, dict): continue
                txt = (it.get("text") or "").strip()
                if not txt: continue
                entity = str(it.get("entity", "master")).lower()
                if "碳基生物" in txt or "主人" in txt or entity in ("master", "user", "self"):
                    k = "学会:" + ("".join(re.findall(r"[\u4e00-\u9fff]{2,}", txt)[:2]) or txt[:4])
                    e = mem.setdefault(k, {"q": txt, "know": [], "ts": datetime.datetime.now().isoformat()})
                    note = "（从猫娘与主人对话学到）%s" % txt
                    if note not in e["know"]:
                        e["know"].append(note); added += 1
                    e["know"] = e["know"][-20:]
        except Exception as ex:
            if verbose: print("facts 读错:", ex)
    # 2. persona.json: 猫娘说话风格
    persona = os.path.join(STEAM_MEM, "persona.json")
    if os.path.exists(persona):
        try:
            p = json.load(open(persona, encoding="utf-8"))
            st = json.dumps(p, ensure_ascii=False)[:260]
            e = mem.setdefault("猫娘说话风格", {"q": "小焦怎么像猫娘那样说话", "know": [], "ts": datetime.datetime.now().isoformat()})
            note = "参考猫娘说话风格: " + st
            if note not in e["know"]:
                e["know"].append(note); added += 1
        except Exception as ex:
            if verbose: print("persona 读错:", ex)
    _save_mem(mem)
    if verbose:
        print("[learn_from_neko] ✅ 学到 %d 条" % added)
        for k in list(mem.keys()):
            if k.startswith("学会:"):
                print("  ·", mem[k]["q"][:55])
    return added

if __name__ == "__main__":
    learn_once()
