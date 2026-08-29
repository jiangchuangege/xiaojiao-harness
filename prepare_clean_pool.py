# -*- coding: utf-8 -*-
"""把训练数据池清洗为干净、规模可控的 UTF-8 语料文件。"""
import re, random, os
SRC = "training_data_pool.txt"; DST = "training_data_pool_clean.txt"; MAX_LINES = 1_000_000
JUNK_KW = ["**Format**", "Xiao Jiao", "Final Polish", "Line 1:", "Line 2:", "Line 3:",
           "Line 4:", "Line 5:", "User's Message", "Xiao Jiao's Response", "Yes.",
           "Okay, so", "the format is", "```", "【", "】"]
LINE_RE = re.compile(r"^用户 .+ 小焦 .+")
CJK = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
NO_CJK_SPACE = re.compile(r"(?<=[" + CJK + r"])\s+(?=[" + CJK + r"])")
def is_junk(s):
    if not s.strip(): return True
    if any(k.lower() in s.lower() for k in JUNK_KW): return True
    if "用户" not in s or "小焦" not in s: return True
    return False
def main():
    kept=[]
    with open(SRC,"rb") as f:
        for raw in f:
            try: s=raw.rstrip(b"\r\n").decode("utf-8").strip()
            except Exception: continue
            if not LINE_RE.match(s): continue
            if is_junk(s): continue
            kept.append(NO_CJK_SPACE.sub("", s))
            if len(kept)>=MAX_LINES: break
    random.seed(42); random.shuffle(kept)
    with open(DST,"w",encoding="utf-8") as f:
        for s in kept: f.write(s+"\n")
    print("有效对话行:",len(kept),"已写入",DST,"{:.1f} MB".format(os.path.getsize(DST)/1e6))
if __name__=="__main__": main()
