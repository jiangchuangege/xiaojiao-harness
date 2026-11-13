# -*- coding: utf-8 -*-
"""接线审计：core/ 下每个模块，有没有被别的文件导入（= 有没有真接进系统）。

判据：在 xiaojiao_app.py / core/*.py / plugins/*.py 里出现
      `import <mod>` 或 `from core import <mod>` 或 `core.<mod>`
孤立模块 = 从来没有被导入 → 等于没接上。
"""
import io
import os
import re

ROOT = "."
core_files = sorted(f[:-3] for f in os.listdir("core") if f.endswith(".py"))
sources = []
for p in ["xiaojiao_app.py"]:
    if os.path.exists(p):
        sources.append(p)
for d in ("core", "plugins"):
    for f in os.listdir(d):
        if f.endswith(".py"):
            sources.append(os.path.join(d, f))
blob = "\n".join(io.open(p, encoding="utf-8", errors="ignore").read() for p in sources)

wired, orphan = [], []
for m in core_files:
    if m.startswith("_") or m in ("__init__",):
        continue
    pat = [r"\bimport\s+%s\b" % re.escape(m),
           r"\bfrom\s+core\s+import\s+[^\n]*\b%s\b" % re.escape(m),
           r"\bfrom\s+core\.%s\s+import\b" % re.escape(m),
           r"\bfrom\s+\.%s\s+import\b" % re.escape(m),
           r"core\.%s\b" % re.escape(m),
           # 【动态导入也要算】项目里大量用 `_mod("inner")` / `__import__("core."+name)`
           # 这种写法，静态 grep `import inner` 是找不到的 —— 我第一版审计就因此
           # 误报了 5 个"孤立模块"（inner/dream/chain/breakthrough/perspective 其实都接着）。
           r'_mod\(\s*["\']%s["\']\s*\)' % re.escape(m),
           r'["\']core\.%s["\']' % re.escape(m)]
    if any(re.search(x, blob) for x in pat):
        wired.append(m)
    else:
        orphan.append(m)

print("core 模块总数: %d" % (len(wired) + len(orphan)))
print("已接线: %d" % len(wired))
print("**孤立（从没被导入）: %d**" % len(orphan))
print()
if orphan:
    print("孤立模块清单：")
    for m in orphan:
        p = os.path.join("core", m + ".py")
        n = len(io.open(p, encoding="utf-8", errors="ignore").read())
        print("  %-26s %6d 字" % (m + ".py", n))
