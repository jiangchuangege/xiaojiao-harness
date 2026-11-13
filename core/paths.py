# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
# 这就是"模型平等"和"变形金刚"的工程基础。
"""小焦 · 载体层 · 外部路径的统一解析（**不写死任何一台机器的路径**）

【为什么要有这一层】这些路径以前散在好几处，而且**写死过**，换台机器就得改代码 ——
用户的原话是「死路径别人有点不太愿意」：
  · `llama-swap.yaml` 里 4 条路由写死 `C:/llama/llama-server.exe` 与各自的 gguf；
  · `brain_manager.py` 的默认进程路径、`podcast_gen.py` 的 SD 模型、两个浏览器自测的 chrome；
  · `xiaojiao_app.py` 的「一键添加本地模型」当时也写死 `C:/llama/llama-server.exe`
    （**功能越方便，写死的路径传得越远**，所以收到这一层来）。

【解析顺序 —— 每一档都如实回报"靠什么找到的"】
  ① **环境变量**（用户自己指定，最优先，永远压过自动探测）；
  ② PATH / 项目目录 / 常见目录（毫秒级，启动时只走这两档）；
  ③ **深搜**（`where /r` 整盘）—— 只有安装器与 `tools/setup_paths.py --deep` 才走，
     因为它可能跑几十秒。
  找不到就返回空串 + 一句「怎么指定」，**绝不猜一个看起来像的路径**。

环境变量一览：`XIAOJIAO_LLAMA_SERVER` / `XIAOJIAO_GGUF` / `XIAOJIAO_LLAMA_SWAP` /
             `XIAOJIAO_CHROME` / `XIAOJIAO_SD_MODEL`
"""
import os
import shutil
import sys

__all__ = ["ENV_VARS", "find_llama_server", "find_gguf", "find_llama_swap", "find_chrome",
           "find_sd_model", "report", "fix_swap_cfg", "SWAP_CFG", "env_hint"]

_ROOTS = None


def _roots():
    """本项目根目录 + 它的上一级（有人把项目放在别处、模型放旁边，这两种都要能找到）。"""
    global _ROOTS
    if _ROOTS is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _ROOTS = [here, os.path.dirname(here)]
    return _ROOTS


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _norm(p):
    """统一成正斜杠 `C:/llama/xxx` —— 项目里 `llama-swap.yaml` 一直是这个写法，
    而且 Windows 的 API 与命令行两种斜杠都认（混着显示 `C:/llama\\xxx` 很难看）。"""
    return str(p or "").replace("\\", "/")


def _cand_dirs():
    """② 档要看的目录：项目目录、上一级、model/models、常见安装位置、下载、家目录。"""
    out = []
    for r in _roots():
        out += [r, os.path.join(r, "model"), os.path.join(r, "models"), os.path.join(r, "llama")]
    out += ["C:/llama", "C:/llama.cpp", "C:/tools/llama", "D:/llama",
            # 【真机实测补的】很多人把模型放大写中文目录（`C:\\模型`、`C:\\模型文件`），
            # 这既不违规也不罕见 —— 但原来不在候选里，于是"模型明明在机器上却识别不到"。
            "C:/模型", "C:/模型文件", "D:/模型", "D:/模型文件",
            _home("llama"), _home("llama.cpp"), _home("Downloads"), _home("下载"),
            _home("模型"), _home()]
    seen, uniq = set(), []
    for d in out:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


ENV_VARS = {
    "llama_server": "XIAOJIAO_LLAMA_SERVER",
    "gguf": "XIAOJIAO_GGUF",
    "llama_swap": "XIAOJIAO_LLAMA_SWAP",
    "chrome": "XIAOJIAO_CHROME",
    "sd_model": "XIAOJIAO_SD_MODEL",
}

SWAP_CFG = os.path.join(_roots()[0], "llama-swap.yaml")


def env_hint(key):
    """没找到时该说什么 —— 明写环境变量名，**不写"请自行配置"这种空话**。"""
    return "没找到（自己指定：set %s=完整路径）" % ENV_VARS.get(key, "XIAOJIAO_XXX")


def _by_env(key):
    name = ENV_VARS.get(key, "")
    v = (os.environ.get(name) or "").strip().strip('"')
    if v and os.path.exists(v):
        return v, "环境变量 %s" % name
    if v:
        return "", "环境变量 %s 指向的路径不存在：%s" % (name, v)
    return "", ""


def _first_existing(paths, how):
    for p in paths:
        if p and os.path.exists(p):
            return p, how % (os.path.basename(p) if "%s" in how else "")
    return "", ""


def _deep_exe(name, keywords):
    """③ 档：借 `install_all.py` 的全盘探测（它有一套"目录名不写死"的启发式 + `where /r` 兜底）。"""
    try:
        import install_all as _IA      # 同目录下的安装器（import 不会跑它的 main）
        p = _IA.discover_exe(name, keywords)
        return p or ""
    except Exception:      # noqa: silent-ok — 拿不到深搜能力就老实说只有前两档
        return ""


def _shallow_exe(cand_dirs):
    """在候选目录的**下一层**里找 llama-server.exe（含 llama.cpp 两种常见产物结构）。

    为什么需要这一层：`_cand_dirs()` 里都是**上层目录**（Downloads、项目根……），
    而实际的 exe 往往在它们的子目录里：
      · 官方预编译包解压后：`Downloads/llama-b4931-bin-win-cuda-x64/llama-server.exe`
      · 自己编译后：`llama.cpp/build/bin/Release/llama-server.exe`
    只查 `目录/llama-server.exe` 的话，上面两种都**看不见** ——
    用户会觉得"我明明下载了/编译好了，它就是不认"。
    """
    rels = (("llama-server.exe",), ("llama-server",),
            ("bin", "llama-server.exe"), ("bin", "llama-server"),
            ("Release", "llama-server.exe"),
            ("build", "bin", "llama-server.exe"), ("build", "bin", "llama-server"),
            ("build", "bin", "Release", "llama-server.exe"),
            ("build", "bin", "Release", "llama-server"))
    for d in cand_dirs:
        if not os.path.isdir(d):
            continue
        try:
            subs = [os.path.join(d, x) for x in sorted(os.listdir(d))
                    if os.path.isdir(os.path.join(d, x))]
        except Exception:      # noqa: silent-ok — 某个目录读不了不影响别的
            continue
        for sub in subs[:60]:          # 只看前 60 个子目录，避免 Downloads 特别大时拖慢启动
            for rel in rels:
                c = os.path.join(sub, *rel)
                if os.path.isfile(c):
                    return c, "在 %s 里找到" % _norm(os.path.dirname(os.path.abspath(c)))
    return "", ""


def find_llama_server(deep=False):
    """llama-server 可执行文件。返回 `(路径, 说明)`；找不到返回 `("", 怎么指定)`。"""
    p, how = _by_env("llama_server")
    if p or how:
        return p, how
    p = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if p:
        return p, "PATH 里找到"
    for d in _cand_dirs():
        if not os.path.isdir(d):
            continue
        for fn in ("llama-server.exe", "llama-server"):
            c = os.path.join(d, fn)
            if os.path.exists(c):
                return _norm(c), "在 %s 里找到" % _norm(os.path.abspath(d))
    # 再往下一层找（预编译包 / 自编译产物都在这层）
    p, how = _shallow_exe(_cand_dirs())
    if p:
        return _norm(p), how
    if deep:
        p = _deep_exe("llama-server.exe", ("llama", "大脑")) or _deep_exe("llama-server", ("llama",))
        if p:
            return p, "深搜（整盘）找到"
    return "", env_hint("llama_server")


def _gguf_magic_ok(path):
    """文件头是不是真的 `GGUF`（**只看 4 个字节**，几毫秒，不读整个 5GB）。

    为什么非要验一下：下面放宽了扩展名（允许 `.gguf_`），而"下划线结尾"很常见于
    **没下完**的临时文件或改过名的半成品。不验头就把它当模型交出去，
    启动会在加载阶段崩，而且报错位置离真正原因很远（用户看到的是"大脑起不来"）。
    验一下头，坏文件会被跳过并继续找下一个 —— 这是"宁可多找一个目录，也不交坏文件"。
    """
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except Exception:      # noqa: silent-ok — 读不了就当不合格，继续找别的
        return False


def _looks_gguf(fn):
    """文件名像不像模型。**标准是 `.gguf`**；另外容忍 `.gguf_` 这种改了名的真文件
    （真机实测：用户的模型叫 `xiaojiao.gguf_`，头是合法 GGUF v3、399 个张量）。
    `.part` / `.tmp` / `.download` 这类**明确表示没下完**的后缀一律不认。"""
    low = fn.lower()
    if low.endswith(".gguf"):
        return True
    if low.endswith(".gguf_"):        # 有人用下划线把文件"藏"起来（避免被工具乱扫）
        return True
    return False


def find_gguf(deep=False):
    """一个 GGUF 模型文件：项目目录 → 常见目录 → 深搜。**优先名字里带 xiaojiao 的**。"""
    p, how = _by_env("gguf")
    if p or how:
        return p, how
    best = ""
    loose = ""                             # 名字不规范但头合法的（`.gguf_`），当次选
    for d in _cand_dirs():
        if not os.path.isdir(d):
            continue
        try:
            for fn in sorted(os.listdir(d)):
                if not _looks_gguf(fn):
                    continue
                full = os.path.join(d, fn)
                if not os.path.isfile(full):
                    continue
                std = fn.lower().endswith(".gguf")
                if not std:
                    # 非标准名：必须验头，合格才纳入"次选"（标准命名的仍然优先）
                    if not _gguf_magic_ok(full):
                        continue
                    loose = loose or full
                    continue
                if "xiaojiao" in fn.lower():
                    return _norm(full), "在 %s 里找到（官方同名优先）" % _norm(os.path.abspath(d))
                best = best or full
        except Exception:      # noqa: silent-ok — 某个目录读不了不影响别的
            continue
    if best:
        return _norm(best), "在项目/常见目录里找到"
    if loose:
        return _norm(loose), "在 %s 里找到（文件名不是标准 .gguf，已校验 GGUF 头）" % _norm(
            os.path.dirname(os.path.abspath(loose)))
    if deep:
        try:
            import install_all as _IA
            p = _IA.discover_gguf() or ""
            if p:
                return p, "深搜（整盘）找到"
        except Exception:      # noqa: silent-ok
            pass
    return "", env_hint("gguf")


def find_llama_swap(deep=False):
    """llama-swap（多大脑热切换）。**它是可选的** —— 没有它就直连 llama-server。"""
    p, how = _by_env("llama_swap")
    if p or how:
        return p, how
    for r in _roots():
        for fn in ("llama-swap-x86_64-pc-windows-msvc.exe", "llama-swap.exe", "llama-swap"):
            c = os.path.join(r, "llama-swap", fn)
            if os.path.exists(c):
                return c, "在 llama-swap/ 目录里找到"
    p = shutil.which("llama-swap") or shutil.which("llama-swap.exe")
    if p:
        return p, "PATH 里找到"
    if deep:
        p = _deep_exe("llama-swap.exe", ("llama", "swap"))
        if p:
            return p, "深搜（整盘）找到"
    return "", "没找到（可选件；没有它就直连 llama-server，不影响聊天）"


def find_chrome(deep=False):
    """无头浏览器（两个渲染类自测用）。找不到就**如实跳过**，不假装跑过。"""
    p, how = _by_env("chrome")
    if p or how:
        return p, how
    p = shutil.which("chrome") or shutil.which("chrome.exe") or shutil.which("chromium")
    if p:
        return p, "PATH 里找到"
    cands = [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        _home("AppData/Local/Google/Chrome/Application/chrome.exe"),
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for d in _cand_dirs():
        cands.append(os.path.join(d, "chrome-win64", "chrome.exe"))
    for c in cands:
        if os.path.exists(c):
            return c, "在常见安装位置找到"
    return "", env_hint("chrome")


def find_sd_model(deep=False):
    """播客封面用的 SD 模型（可选件，`*.safetensors`）。"""
    p, how = _by_env("sd_model")
    if p or how:
        return p, how
    for d in _cand_dirs():
        if not os.path.isdir(d):
            continue
        try:
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(".safetensors") and (
                        "sd" in fn.lower() or "pruned" in fn.lower()):
                    return _norm(os.path.join(d, fn)), "在 %s 里找到" % _norm(os.path.abspath(d))
        except Exception:      # noqa: silent-ok
            continue
    return "", "没找到（可选件：只有播客封面用它。自己指定：set XIAOJIAO_SD_MODEL=完整路径）"


def report(deep=False):
    """一次把五项都解析出来，连同"靠什么找到的"——给安装器/体检工具用。"""
    out = []
    for name, fn in (("llama-server", find_llama_server), ("GGUF 模型", find_gguf),
                     ("llama-swap", find_llama_swap), ("浏览器", find_chrome),
                     ("SD 模型", find_sd_model)):
        p, how = fn(deep=deep)
        out.append({"what": name, "path": p, "how": how, "ok": bool(p),
                    "env": ENV_VARS[{ "llama-server": "llama_server", "GGUF 模型": "gguf",
                                      "llama-swap": "llama_swap", "浏览器": "chrome",
                                      "SD 模型": "sd_model"}[name]]})
    return out


# ---------- 把 llama-swap.yaml 里写死的路径改成"这台机器上真的存在"的 ----------
_CMD_RE = None


def _split_cmd(line):
    """拆 `    cmd: '……'`：返回 `(前缀, 引号, 内容, 后缀)`；不是 cmd 行就返回 None。"""
    import re
    m = re.match(r"^(\s*cmd:\s*)(['\"]?)(.*?)(\2)\s*$", line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), ""


def _swap_tokens(cmd, server, gguf):
    """在一条 cmd 里换掉 ①第一个 token（可执行文件）②`--model` 的值。其余字节不动。"""
    import re
    out, changed = cmd, []
    if server:
        m = re.match(r'^("[^"]+"|\'[^\']+\'|\S+)', out)
        if m and os.path.normcase(m.group(1).strip('"\'')) != os.path.normcase(server):
            old = m.group(1)
            out = server + out[len(old):]
            changed.append(("exe", old.strip('"\''), server))
    if gguf:
        m = re.search(r'(--model\s+)("[^"]+"|\'[^\']+\'|\S+)', out)
        if m:
            old = m.group(2).strip('"\'')
            if os.path.normcase(old) != os.path.normcase(gguf):
                q = '"' if (" " in gguf) else ""
                out = out[:m.start(2)] + q + gguf + q + out[m.end(2):]
                changed.append(("model", old, gguf))
    return out, changed


def fix_swap_cfg(server=None, gguf=None, path=None, only_missing=True, dry_run=True):
    """把 `llama-swap.yaml` 里不存在的 exe/模型路径换成本机探测到的。

    `only_missing=True`（默认）时**只改"指向的文件不存在"的那些** —— 在这台机器上跑得好好的
    配置不许被顺手改掉。返回 `{"ok","changes","backup","path","text"}`；`dry_run=True` 不落盘。
    """
    import io
    import time
    import re
    p = path or SWAP_CFG
    if not os.path.exists(p):
        return {"ok": False, "why": "没有这个文件：%s" % p, "changes": []}
    server = server if server is not None else find_llama_server()[0]
    gguf = gguf if gguf is not None else find_gguf()[0]
    raw = io.open(p, encoding="utf-8", errors="replace").read()
    lines = raw.split("\n")
    route, changes = "", []
    for i, ln in enumerate(lines):
        m = re.match(r"^  (\w[\w\-\.]*):\s*$", ln)
        if m:
            route = m.group(1)
            continue
        sp = _split_cmd(ln)
        if not sp:
            continue
        prefix, quote, cmd, _ = sp
        use_server, use_gguf = server, gguf
        if only_missing:
            mm = re.search(r'(--model\s+)("[^"]+"|\'[^\']+\'|\S+)', cmd)
            if mm and os.path.exists(mm.group(2).strip('"\'')):
                use_gguf = ""                     # 这条路由本来就能用 → 不动它
            mt = re.match(r'^("[^"]+"|\'[^\']+\'|\S+)', cmd)
            if mt and os.path.exists(mt.group(1).strip('"\'')):
                use_server = ""
        if not use_server and not use_gguf:
            continue
        newcmd, ch = _swap_tokens(cmd, use_server, use_gguf)
        if not ch:
            continue
        lines[i] = prefix + quote + newcmd + quote
        for kind, old, new in ch:
            changes.append({"route": route, "what": kind, "old": old, "new": new})
    text = "\n".join(lines)
    out = {"ok": True, "changes": changes, "path": p, "backup": "", "text": text}
    if dry_run or not changes:
        return out
    bak = os.path.join(_roots()[0], "logs", "backup_before_setup_paths",
                       time.strftime("%Y%m%d_%H%M%S"))
    try:
        os.makedirs(bak, exist_ok=True)
        io.open(os.path.join(bak, os.path.basename(p)), "w", encoding="utf-8").write(raw)
        out["backup"] = os.path.join(bak, os.path.basename(p))
    except Exception as e:      # noqa: silent-ok — 备份失败就不写（宁可不改）
        return {"ok": False, "why": "备份失败，一个字节都没动：%s" % e, "changes": changes}
    io.open(p, "w", encoding="utf-8").write(text)
    return out


if __name__ == "__main__":      # 手工看一眼（正式的体检用 `tools/setup_paths.py`）
    print("python core/paths.py 只是速览；用 python tools/setup_paths.py 做体检/改写")
    for r in report():
        print("  %-12s %-8s %s  ← %s" % (r["what"], "✅" if r["ok"] else "—",
                                         r["path"] or "（没找到）", r["how"]))
    sys.exit(0)
