# -*- coding: utf-8 -*-
# 小焦系统本身不依赖任何具体模型。
# 它是完整的载体（器官齐全），模型是火种（可替换）。
# 接入任何模型 → 系统活；换任何模型 → 系统不变。
# 这就是"模型平等"和"变形金刚"的工程基础。
"""小焦 · 载体层 · 能力自增长（自写插件闭环，2026-10-17）

【这是什么】用户提了一个"我现在没有的能力"时，小焦**自己写一个插件补上**：
  说需求 → 生成插件代码 → **静态检查** → **子进程沙箱试跑** → 不过就**回喂模型自修** → 通过才落地。

【为什么敢做这件事】"让模型写代码"不新鲜，新鲜的是**每一道关都不是模型说了算**：
  ① 静态检查（`ast`）：必须是插件契约（`get_tool_descriptions` / `execute` / `get_plugin`），
     import 必须在白名单里，不许出现 `os.system` / `subprocess` / `eval` / `exec` / 删文件；
  ② 沙箱试跑：**另起子进程**、在临时目录 import 它、校验参数 schema 合规，
     再用最小参数调一次 `execute()`，要求"不抛异常、返回非空字符串"；
  ③ 自修复：检查没过就把**错误原文回喂模型**让它改，最多 2 轮
     （实测 4B 一次成功率大约一半，不给自修复等于抽奖）；
  ④ 落地不覆盖：文件名带时间戳，**同名绝不覆盖**（本项目"只加不删"）。

【边界（写死，不给开关）】
  · 只写 `plugins/gen_*.py`，不碰 `core/`、不碰 `xiaojiao_app.py`；
  · 危险调用直接判死（即使它"能跑"）；
  · 生成的工具说明必须带「什么时候用」（模型漏了由载体补，并如实记下补了几处）；
  · 落地后**要重启或热注册才生效** —— 明确告诉用户，不假装"已经能用了"。
"""
import ast
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time

LOG = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGINS = os.path.join(_ROOT, "plugins")

# import 白名单：只允许"纯功能"的库（这些不足以破坏系统）
_ALLOWED_IMPORTS = {
    "re", "json", "time", "math", "datetime", "random", "hashlib", "base64", "uuid",
    "os", "os.path", "pathlib", "collections", "itertools", "functools", "typing",
    "urllib", "urllib.parse", "urllib.request", "requests", "string", "textwrap",
    "csv", "io", "statistics", "decimal", "calendar", "html", "html.parser",
}
# 危险调用：出现即判不合格（"能跑"也不行）
_FORBIDDEN_CALLS = {
    "system", "popen", "spawn", "spawnl", "execv", "execve", "fork", "kill",
    "rmtree", "unlink", "remove", "removedirs", "rmdir",
    "eval", "exec", "compile", "__import__", "input",
    "setattr", "delattr", "globals", "locals", "vars",
}

_PROMPT = """你是小焦插件作者。为需求写一个**单文件 Python 插件**，严格遵守：
1) 一个类，含 `get_tool_descriptions(self)` 返回工具列表：
   [{{"name": "英文小写工具名（只允许字母数字下划线，例如 cn_num_to_int）",
     "description": "中文说明（含「什么时候用」一句具体场景，60 字内）",
     "parameters": {{"type": "object", "properties": {{"参数名": {{"type": "string",
     "description": "这个参数是什么"}}}}, "required": ["必填参数"]}}}}]
2) 含 `execute(self, tool_name, params)`：按 tool_name 分派；**一律返回中文字符串**；
   **绝不抛异常**（用 try/except 兜住，把原因写成中文返回）。
3) 文件末尾 `def get_plugin(): return 你的类()`
4) 只许 import 这些：{allowed}
5) 不许用 subprocess / os.system / eval / exec / 删文件 这类危险调用。
6) 代码尽量短（60 行以内），不要在文件里写示例调用。

【照着这两个真实插件的样子写（骨架对齐，别照抄内容）】
{examples}

【这类需求以前栽过的地方（务必避开）】
{lessons}

需求：{need}

只输出完整 Python 代码，不要解释、不要 Markdown 代码围栏。"""

_FIX_PROMPT = """你上一个 Python 插件没过检查。请**只修检查指出的问题**，其它保持不动，输出完整代码。

需求：{need}

【检查给的问题】
{why}

【你上一版代码】
{code}

要求仍然一样：类里有 get_tool_descriptions / execute，末尾 get_plugin()，
一律返回中文字符串、不抛异常，只许用这些 import：{allowed}。代码控制在 60 行内。
只输出完整 Python 代码，不要解释、不要代码围栏。"""


def _base_url_v1():
    """大脑服务地址（读控制文件；读不到用本机 9292）。"""
    try:
        p = os.path.join(_ROOT, "xiaojiao_control.json")
        c = json.load(open(p, encoding="utf-8"))
        b = str(((c.get("brain") or {}).get("api") or {}).get("base_url") or "").rstrip("/")
        if b:
            return b if b.endswith("/v1") else b + "/v1"
    except Exception as e:      # noqa: silent-ok — 读不到就用默认
        LOG.debug("自增长：读配置失败（用默认 9292）：%s", e)
    return "http://127.0.0.1:9292/v1"


def _sanitize_module_name(s):
    """从需求里起一个安全的模块名：gen_<ascii片段>_<时间戳>。"""
    t = re.sub(r"[^A-Za-z0-9_]", "", str(s or ""))[:20].lower() or "cap"
    return "gen_%s_%s" % (t, time.strftime("%Y%m%d_%H%M%S"))


def _ensure_contract_phrases(code, need):
    """把「什么时候用」补进**每个工具条目的那段说明**里。返回 (新代码, 补了几处)。

    【为什么只补工具级、不补参数级】第一版用一条正则把所有 `"description": "..."` 全补了，
    结果参数说明里也被塞进「什么时候用：…」——读起来很蠢，也让工具说明变噪声。
    现在按 `"name":` 切块，每块**只补第一个 description**（那就是工具级说明）。
    """
    short = re.sub(r"\s+", " ", str(need or "")).strip()[:28] or "需要用到这个能力时"
    fixed = {"n": 0}
    # 以 "name" 出现的位置切块：每块内第一次出现的 description 就是工具级说明
    parts = re.split(r'("name"\s*:)', code)
    out = [parts[0]]
    i = 1
    while i < len(parts):
        marker, body = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
        done = {"hit": False}

        def repl(m):
            if done["hit"]:
                return m.group(0)          # 同一块里的后续 description（参数级）不动
            done["hit"] = True
            text = m.group(1)
            if "什么时候用" in text:
                return m.group(0)
            fixed["n"] += 1
            tail = (" 什么时候用：%s" % short).replace('"', "")
            return '"description": "%s%s"' % (text, tail)

        body = re.sub(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', repl, body)
        out.append(marker)
        out.append(body)
        i += 2
    return "".join(out), fixed["n"]


def _existing_tool_names():
    """仓库里**现存的工具名集合**（扫 plugins/*.py 里声明的 name）。

    【为什么必须有这道检查】实测踩到：自增长生成的新插件声明了一个**和已有插件同名**的工具
    （`cn_num_to_int`），而它的 execute 里分派的是另一个名字 —— 结果调用时被别人的插件接住，
    返回"这里没有这个工具"。用户侧看到的就是"明明装了却调不通"。
    这个项目"只加不删"，所以撞名不会覆盖旧文件，但会让工具表与实现错位，必须挡在落地之前。
    """
    names = set()
    try:
        for fn in os.listdir(_PLUGINS):
            if not fn.endswith(".py"):
                continue
            try:
                src = io.open(os.path.join(_PLUGINS, fn), encoding="utf-8").read()
            except Exception:      # noqa: silent-ok — 单个文件读不了就跳过
                continue
            names.update(re.findall(r'"name"\s*:\s*"([A-Za-z][A-Za-z0-9_]{2,40})"', src))
    except Exception as e:      # noqa: silent-ok — 扫不出来就不拦（宁可漏拦也不误杀）
        LOG.debug("自增长：扫现有工具名失败（忽略）：%s", e)
    return names


def _tool_names_in(code):
    """这份代码里声明的工具名。"""
    return set(re.findall(r'"name"\s*:\s*"([A-Za-z][A-Za-z0-9_]{2,40})"', str(code or "")))


def _conflicts(code):
    """和现有工具撞名了吗？返回撞上的名字列表。"""
    try:
        return sorted(_tool_names_in(code) & _existing_tool_names())
    except Exception:      # noqa: silent-ok
        return []


def _capability_keywords(need):
    """从需求里挑「能力内核词」：去掉"帮我/写个/小工具/然后/告诉我"这些外壳。

    【为什么需要】实测漏网：两条需求
      「把中文数字（一、二…十、百、千）转成阿拉伯数字」
      「帮我写个小工具，把'九亿零五万'转换成纯阿拉伯数字，然后告诉我结果」
    说的是同一件事，可 2 字词覆盖率只有 40% 左右 → 被判"新需求"，于是又长了一个重复工具。
    外壳词是噪声，先把它们剔掉，再比"内核词"，重合度才反映真实能力是否重复。
    """
    s = str(need or "")
    for w in ("帮我", "帮忙", "给我", "写个", "写一个", "做一个", "做个", "小工具", "工具",
              "然后", "告诉", "我", "结果", "一下", "能够", "可以", "实现", "一个", "个",
              "纯", "的", "把", "将", "并", "再", "请"):
        s = s.replace(w, "")
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)


def _similar_existing(need):
    """**同类能力是不是已经有了**？有就返回已有工具名（提示别重复生成）。

    【为什么必须有这道闸】实测膨胀：连着几次要「中文数字↔阿拉伯数字」这类能力，
    自增长就长出了 `cn_num_to_int`、`int_to_cn_num`、`cn_num_to_int_v2` 等重叠工具。
    工具表越堆越乱，模型选工具反而更难（典型的"越强越不好用"）。

    【两道判据，任一命中就算重复】
      ① 需求内核词重合度 ≥0.6（先剔掉"帮我/写个小工具/然后"这些外壳词）；
      ② 需求与**已有工具的说明**里，能力内核词有 ≥2 个相同（覆盖"措辞完全不同的同一件事"）。
    判不准就放行 —— 宁可多长一个，也不误杀真新需求。
    """
    try:
        from core import experience as EXP

        def toks(s):
            t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s or ""))
            return set(t[i:i + 2] for i in range(len(t) - 1))

        core_need = _capability_keywords(need)
        want = toks(core_need) or toks(need)
        if not want:
            return ""
        # 已有工具的说明（从插件文件里扫 description）
        desc_toks = []
        try:
            for fn in os.listdir(_PLUGINS):
                if not fn.endswith(".py"):
                    continue
                try:
                    src = io.open(os.path.join(_PLUGINS, fn), encoding="utf-8").read()
                except Exception:      # noqa: silent-ok
                    continue
                for d in re.findall(r'"description"\s*:\s*"([^"]{4,200})"', src):
                    desc_toks.append(toks(d))
        except Exception:      # noqa: silent-ok
            desc_toks = []

        for row in EXP.load(limit=400):
            if row.get("kind") != "forge_ok":
                continue
            old_tools = str((row.get("extra") or {}).get("tools") or "")
            if not old_tools:
                continue
            ot = toks(_capability_keywords(row.get("need")))
            if ot and len(want & ot) / float(len(want)) >= 0.6:
                return old_tools.split(",")[0]
            # 判据②：内核词与已有工具说明有两个以上相同的 2 字词
            if len(want) >= 4 and any(len(want & dt) >= 2 for dt in desc_toks):
                hit = max(desc_toks, key=lambda dt: len(want & dt)) if desc_toks else set()
                if len(want & hit) >= 3:        # 再收一道，避免"数字/转换"这种泛词误判
                    return old_tools.split(",")[0]
    except Exception as e:      # noqa: silent-ok — 判不了就放行（不误杀新需求）
        LOG.debug("自增长：查同类能力失败（忽略）：%s", e)
    return ""


def static_check(code):
    """静态检查。返回 (ok, 问题列表)。问题用中文写，直接能给人看。"""
    problems = []
    src = str(code or "").strip()
    if not src:
        return False, ["生成结果是空的"]
    src = re.sub(r"^```(?:python)?\s*", "", src)
    src = re.sub(r"\s*```$", "", src).strip()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return False, ["语法错误：第 %s 行 %s" % (e.lineno, e.msg)]

    top_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "get_plugin" not in top_funcs:
        problems.append("缺少 `def get_plugin()`（插件契约要求）")

    has_desc = has_exec = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names = {f.name for f in node.body if isinstance(f, ast.FunctionDef)}
            if "get_tool_descriptions" in names:
                has_desc = True
            if "execute" in names:
                has_exec = True
    if not has_desc:
        problems.append("类里缺 `get_tool_descriptions()`")
    if not has_exec:
        problems.append("类里缺 `execute()`")

    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for m in mods:
            base = (m or "").split(".")[0]
            if m not in _ALLOWED_IMPORTS and base not in _ALLOWED_IMPORTS:
                problems.append("不允许的 import：%s" % m)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            if name in _FORBIDDEN_CALLS:
                problems.append("出现危险调用：%s（这个项目不允许）" % name)

    if not re.search(r"[\u4e00-\u9fff]", src):
        problems.append("代码里完全没有中文说明（模型会选不对工具）")
    return (not problems), problems


_SANDBOX_CODE = r'''
import importlib.util, json, re, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("forge_candidate", path)
mod = importlib.util.module_from_spec(spec)
sys.modules["forge_candidate"] = mod
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(json.dumps({"ok": False, "why": "import 失败：%s: %s" % (type(e).__name__, e)},
                     ensure_ascii=False)); raise SystemExit(0)
fn = getattr(mod, "get_plugin", None)
if not callable(fn):
    print(json.dumps({"ok": False, "why": "没有可调用的 get_plugin()"}, ensure_ascii=False))
    raise SystemExit(0)
try:
    plug = fn()
    descs = plug.get_tool_descriptions()
except Exception as e:
    print(json.dumps({"ok": False, "why": "get_tool_descriptions 抛异常：%s: %s"
                      % (type(e).__name__, e)}, ensure_ascii=False)); raise SystemExit(0)
if not isinstance(descs, list) or not descs:
    print(json.dumps({"ok": False, "why": "get_tool_descriptions 没有返回非空列表"},
                     ensure_ascii=False)); raise SystemExit(0)
bad = []
for d in descs:
    if not isinstance(d, dict) or not d.get("name"):
        bad.append("条目缺 name")
        continue
    nm = str(d.get("name"))
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,40}", nm):
        bad.append("%s：工具名必须是英文字母开头、只含字母数字下划线（中文名调不准）" % nm[:20])
    p = d.get("parameters") or {}
    if p and (p.get("type") != "object" or not isinstance(p.get("properties"), dict)):
        bad.append("%s 的 parameters 不是标准 JSON Schema" % d.get("name"))
    if "什么时候用" not in str(d.get("description") or ""):
        bad.append("%s 的说明里没有「什么时候用」" % d.get("name"))
if bad:
    print(json.dumps({"ok": False, "why": "；".join(bad[:4])}, ensure_ascii=False))
    raise SystemExit(0)
name = str(descs[0].get("name"))


def _sample_candidates(desc):
    """同一份 schema，给**几组**候选值（不同工具吃的输入不一样）。

    「十二万」适合"中文→数字"，但对"数字→中文"是非法的；
    「120000」反之。只拿一组去试会把好插件误杀（实测就这么误杀过）。
    所以给 3 组，**任一组能跑通就算能用**。
    """
    props = ((desc.get("parameters") or {}).get("properties") or {})
    req = ((desc.get("parameters") or {}).get("required") or list(props))
    outs = []
    for sample in ("十二万", "120000", "测试"):
        args = {}
        for k in req:
            t = str((props.get(k) or {}).get("type") or "string")
            if t in ("integer", "number"):
                args[k] = 120000
            elif t == "boolean":
                args[k] = True
            elif t == "array":
                args[k] = [sample]
            else:
                args[k] = sample
        outs.append(args)
    return outs


_bad_words = ("没给", "请给", "缺", "失败", "错误", "无法", "不支持", "参数不对")
_ok_sample = None
_tried = []
for args in _sample_candidates(descs[0]):
    try:
        out = plug.execute(name, args)
    except Exception as e:
        _tried.append("%s→异常 %s" % (list(args.values()), str(e)[:40]))
        continue
    if not isinstance(out, str):
        _tried.append("%s→返回类型 %s" % (list(args.values()), type(out).__name__))
        continue
    if not out.strip():
        _tried.append("%s→空串" % list(args.values()))
        continue
    if [w for w in _bad_words if w in out]:
        _tried.append("%s→%s" % (list(args.values()), out[:40]))
        continue
    _ok_sample = (args, out)
    break

if _ok_sample is None:
    print(json.dumps({"ok": False,
                      "why": "三组样例参数都跑不通（schema 和代码对不上，或内部逻辑有问题）：%s"
                             % "；".join(_tried[:3])}, ensure_ascii=False))
    raise SystemExit(0)
print(json.dumps({"ok": True, "tools": [d.get("name") for d in descs],
                  "sample": _ok_sample[1][:120], "args": _ok_sample[0]}, ensure_ascii=False))
'''


def sandbox_run(path, timeout=30):
    """在**子进程**里试跑生成的插件。返回 (ok, 说明, 工具名列表)。"""
    try:
        tmp = tempfile.mkdtemp(prefix="xj_forge_")
        script = os.path.join(tmp, "_sandbox.py")
        io.open(script, "w", encoding="utf-8").write(_SANDBOX_CODE)
        env = dict(os.environ)
        env["XIAOJIAO_FORGE_SANDBOX"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, script, path], capture_output=True, text=True,
                           timeout=timeout, cwd=tmp, env=env, encoding="utf-8",
                           errors="replace")
        out = (r.stdout or "").strip().splitlines()
        if not out:
            return False, "沙箱没有输出（stderr：%s）" % (r.stderr or "")[:200], []
        try:
            d = json.loads(out[-1])
        except Exception:
            return False, "沙箱输出看不懂：%s" % out[-1][:200], []
        return bool(d.get("ok")), str(d.get("why") or d.get("sample") or ""), \
            list(d.get("tools") or [])
    except subprocess.TimeoutExpired:
        return False, "沙箱试跑超时（%ds）" % timeout, []
    except Exception as e:
        return False, "沙箱跑不起来：%s: %s" % (type(e).__name__, str(e)[:120]), []


def _few_shot(max_files=2, max_lines=32):
    """从仓库现有插件里挑**最短的两个**当骨架样例，喂给生成模型。

    【为什么喂真代码而不是继续描述】实测：光靠文字说"要有 get_tool_descriptions/execute"，
    4B 仍然会漏（漏「什么时候用」、把工具名写成中文）。给它看两个**真实能跑**的插件骨架，
    比加三条要求有效得多 —— 这是"少说多给例子"的工程常识，也是喂样例而不是喂规则的原因。
    只读前 max_lines 行：只取骨架形态，不需要业务逻辑（也就不会把别人的业务抄错）。
    """
    try:
        files = []
        for fn in os.listdir(_PLUGINS):
            if not fn.endswith(".py") or fn.startswith("_") or fn == "forge.py":
                continue
            p = os.path.join(_PLUGINS, fn)
            try:
                with io.open(p, encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:      # noqa: silent-ok — 单个文件读不了就跳过
                continue
            files.append((len(lines), fn, lines[:max_lines]))
        files.sort()
        out = []
        for n, fn, lines in files[:max_files]:
            body = "".join(lines).rstrip()
            out.append("--- 样例：%s（共 %d 行，这里只看前 %d 行）---\n%s"
                       % (fn, n, min(n, max_lines), body))
        return "\n\n".join(out) if out else "（仓库里暂时没有可参考的插件）"
    except Exception as e:      # noqa: silent-ok — 拿不到样例也不能挡住生成
        LOG.debug("自增长：取样例失败（忽略）：%s", e)
        return "（样例取不到，按契约写即可）"


def _lessons_block(need):
    """把经验库里"这类需求以前栽过的坑"整理成几行，注入提示词。"""
    try:
        from core import experience as EXP
        rows = EXP.lessons("forge_fail", keyword=need, limit=3)
        if not rows:
            return "（还没有同类失败记录 —— 这是第一次做这类能力）"
        lines = []
        for r in rows:
            ls = str(r.get("lesson") or "").strip()
            why = str(r.get("why") or "").strip()[:60]
            if ls:
                lines.append("- %s（当时的问题：%s）" % (ls, why))
        return "\n".join(lines) if lines else "（有失败记录但没提炼出教训）"
    except Exception as e:      # noqa: silent-ok
        LOG.debug("自增长：取教训失败（忽略）：%s", e)
        return "（教训库不可用）"


def _ask_model(need):
    """用本机模型生成插件代码（失败返回空串，不抛异常）。"""
    try:
        import requests
        prompt = _PROMPT.format(allowed=", ".join(sorted(_ALLOWED_IMPORTS)),
                                need=str(need)[:400],
                                examples=_few_shot(), lessons=_lessons_block(need))
        for model in ("qwopus3.5-4b-coder-mtp-q5_k_m.gguf", "coder", "xiaojiao"):
            try:
                r = requests.post(_base_url_v1() + "/chat/completions", json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2800, "temperature": 0.2,
                }, timeout=180)
                if r.status_code == 200:
                    return str(((r.json().get("choices") or [{}])[0].get("message") or {})
                               .get("content") or "")
            except Exception:      # noqa: silent-ok — 换下一个模型再试
                continue
    except Exception as e:      # noqa: silent-ok — 生成失败就如实返回空
        LOG.warning("自增长：生成异常 %s: %s", type(e).__name__, e)
    return ""


def _ask_model_fix(need, code, why):
    """把检查结果回喂模型让它改。失败返回空串。"""
    try:
        import requests
        prompt = _FIX_PROMPT.format(need=str(need)[:300], why=str(why)[:600],
                                    code=str(code)[:4000],
                                    allowed=", ".join(sorted(_ALLOWED_IMPORTS)))
        for model in ("qwopus3.5-4b-coder-mtp-q5_k_m.gguf", "coder", "xiaojiao"):
            try:
                r = requests.post(_base_url_v1() + "/chat/completions", json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2800, "temperature": 0.1,
                }, timeout=180)
                if r.status_code == 200:
                    return str(((r.json().get("choices") or [{}])[0].get("message") or {})
                               .get("content") or "")
            except Exception:      # noqa: silent-ok — 换下一个模型再试
                continue
    except Exception as e:      # noqa: silent-ok
        LOG.warning("自增长：修复调用失败 %s: %s", type(e).__name__, e)
    return ""


def _strip_fence(code):
    t = re.sub(r"^```(?:python)?\s*", "", str(code or "").strip())
    return re.sub(r"\s*```$", "", t).strip()


def forge(need, generator=None, dry_run=False, timeout=30, max_repair=2, template_first=True):
    """完整闭环：生成 → 静态检查 →（不过则自修）→ 沙箱试跑 → 落地（或如实失败）。

    `generator`：callable(need) -> code；给了就**只**用它（自测打桩，不回落真调模型）。
    `dry_run=True`：走完全部检查但不写文件（评测/自测用）。
    `template_first=True`：先走**模板路径**（契约由载体写死、模型只填业务函数），
        失败再退回"整份插件自由生成"。实测这条路成功率高得多 —— 见 `forge_template`。
    """
    need = str(need or "").strip()
    if not need:
        return {"ok": False, "stage": "input", "why": "没说清要什么能力", "code": ""}
    # 需求太短 / 全是无意义字符 → 直接拒。
    # 实测踩到：自动改进那条链把需求取成了一个 `x`，模型拿它胡乱写了一份插件，
    # 而沙箱只校验「不抛异常、返回字符串」，于是垃圾居然落了地。
    # 判据：至少 6 个字符，且里面有汉字或英文字母（纯符号不算需求）。
    if len(need) < 6 or not re.search(r"[\u4e00-\u9fffA-Za-z]", need):
        return {"ok": False, "stage": "input",
                "why": "需求太短或没有实际含义（%r）—— 不生成，避免产出垃圾插件" % need[:20],
                "code": ""}

    # ---- 模板优先：契约错不了，模型只写一小段业务函数 ----
    if generator is None and template_first:
        # 先查同类能力：有了就别再长一个（防止工具表膨胀）
        _dup = _similar_existing(need)
        if _dup:
            msg = ("已经有这个能力了：`%s`（不重复生成）。直接用它，或把需求说得更具体一点。"
                   % _dup)
            LOG.info("自增长：%s 需求=「%s」", msg, need[:40])
            _remember(need, {"ok": True, "stage": "duplicate", "tools": [_dup]})
            return {"ok": True, "stage": "duplicate", "why": msg, "module": "",
                    "path": "", "tools": [_dup], "code": "", "via": "reuse"}
        tr = forge_template(need, dry_run=dry_run, timeout=timeout, max_repair=max_repair)
        if tr.get("ok"):
            return tr
        LOG.info("自增长：模板路径没成（%s）→ 退回整份自由生成", str(tr.get("why"))[:60])

    if generator is not None:
        try:
            code = str(generator(need) or "")
        except Exception as e:
            return {"ok": False, "stage": "generate",
                    "why": "生成器出错：%s: %s" % (type(e).__name__, str(e)[:100]), "code": ""}
        if not code.strip():
            return {"ok": False, "stage": "generate", "why": "生成器没有产出代码", "code": ""}
    else:
        code = _ask_model(need)
        if not code:
            return {"ok": False, "stage": "generate",
                    "why": "模型没产出代码（大脑可能没起）", "code": ""}
    code = _strip_fence(code)

    code, _n_fixed = _ensure_contract_phrases(code, need)
    if _n_fixed:
        LOG.info("自增长：载体补了 %d 处「什么时候用」（模型没写全，如实记下）", _n_fixed)

    can_repair = (generator is None) and max_repair > 0
    attempts = 1 + (max_repair if can_repair else 0)
    last = {"ok": False, "stage": "unknown", "why": "没走到任何检查", "code": code}

    for attempt in range(1, attempts + 1):
        ok, problems = static_check(code)
        if not ok:
            last = {"ok": False, "stage": "static", "why": "；".join(problems[:5]),
                    "problems": problems, "code": code}
            if can_repair and attempt < attempts:
                LOG.info("自增长：静态检查没过（%s）→ 第 %d 次自修", problems[0][:40], attempt)
                fix = _ask_model_fix(need, code, last["why"])
                if fix.strip():
                    code = _ensure_contract_phrases(_strip_fence(fix), need)[0]
                    continue
            break
        LOG.info("自增长：静态检查通过（第 %d 次）", attempt)

        # 撞名检查（撞了就没法安全落地：调用会被别人的插件接住）
        _cf = _conflicts(code)
        if _cf:
            last = {"ok": False, "stage": "conflict",
                    "why": "工具名和已有的撞了：%s（换个名字，例如加后缀 _v2 或用更准确的名字）"
                           % "、".join(_cf[:3]), "code": code}
            if can_repair and attempt < attempts:
                LOG.info("自增长：工具名撞车（%s）→ 第 %d 次自修", ",".join(_cf[:2]), attempt)
                fix = _ask_model_fix(need, code, last["why"])
                if fix.strip():
                    code = _ensure_contract_phrases(_strip_fence(fix), need)[0]
                    continue
            break

        module = _sanitize_module_name(need)
        target = os.path.join(_PLUGINS, module + ".py")
        if os.path.exists(target):          # 只加不删：同名不覆盖
            module = "%s_%d" % (module, int(time.time()))
            target = os.path.join(_PLUGINS, module + ".py")
        tmpdir = tempfile.mkdtemp(prefix="xj_forge_chk_")
        tmp_path = os.path.join(tmpdir, module + ".py")
        io.open(tmp_path, "w", encoding="utf-8").write(code)
        ok2, why2, tools = sandbox_run(tmp_path, timeout=timeout)
        if not ok2:
            last = {"ok": False, "stage": "sandbox", "why": why2, "code": code}
            if can_repair and attempt < attempts:
                LOG.info("自增长：沙箱没过（%s）→ 第 %d 次自修", str(why2)[:40], attempt)
                fix = _ask_model_fix(need, code, why2)
                if fix.strip():
                    code = _ensure_contract_phrases(_strip_fence(fix), need)[0]
                    continue
            break
        LOG.info("自增长：沙箱试跑通过（工具：%s，第 %d 次）", tools, attempt)

        if dry_run:
            _remember(need, {"ok": True, "stage": "dry-run", "tools": tools,
                             "attempts": attempt, "fixed": _n_fixed})
            return {"ok": True, "stage": "dry-run", "why": "全部检查通过（未落地）",
                    "module": module, "path": "", "tools": tools, "code": code,
                    "attempts": attempt, "fixed": _n_fixed}
        try:
            os.makedirs(_PLUGINS, exist_ok=True)
            io.open(target, "w", encoding="utf-8").write(code)
        except Exception as e:
            return {"ok": False, "stage": "write",
                    "why": "写不进 plugins/：%s: %s" % (type(e).__name__, str(e)[:100]),
                    "code": code}
        _remember(need, {"ok": True, "stage": "written", "tools": tools,
                         "attempts": attempt, "fixed": _n_fixed})
        return {"ok": True, "stage": "written", "why": "已落地（重启或热注册后生效）",
                "module": module, "path": target, "tools": tools, "code": code,
                "attempts": attempt, "fixed": _n_fixed}
    _remember(need, last)
    return last


_TEMPLATE_PROMPT = """你要给小焦补一个能力。**不要写整个插件**，只写一段"业务函数"。

【需求】{need}

请在下面几种形态里选一个（照抄它的函数签名），只输出**这个函数**（含 import 如果需要）：
1) 单输入文本/数字 → 文本：
def transform(text):
    # 你的实现
    return "结果字符串"
2) 两个输入 → 文本：
def transform(a, b):
    return "结果字符串"
3) 无输入（查本机状态之类）：
def transform():
    return "结果字符串"

硬要求：
· 只输出函数（和它需要的 import），**不要**写 class、不要写 get_plugin、不要写说明文字；
· 返回值必须是字符串（用户看到的就是它）；
· 用 try/except 兜住异常，出错也要返回中文说明，**绝不抛异常**；
· 只许用这些 import：{allowed}

{lessons}
"""

_TEMPLATE_PLUGIN = '''# 小焦 · 模板化生成的能力（2026-10-27，契约由载体写死、业务由模型填写）
"""小焦 · 自动生成的能力 · {cn_name}

【需求】{need}
【怎么来的】`core/plugin_forge.py` 的模板优先路径：契约（类/两个方法/get_plugin）
  由载体按仓库规范写死，模型只负责填 `transform()` 的业务实现 ——
  这样"契约写错/漏「什么时候用」/工具名用中文"这类失败从源头上就不存在了。
"""
{imports}

{body}


_TOOLS = [
    {{
        "name": "{tool_name}",
        "description": "{desc}",
        "parameters": {{
            "type": "object",
            "properties": {{
{propsdoc}
            }},
            "required": [{required}],
        }},
    }},
]


class GenPlugin:
    def get_tool_descriptions(self):
        return list(_TOOLS)

    def execute(self, tool_name, params):
        try:
            if tool_name != "{tool_name}":
                return "这个插件只有 {tool_name}（{cn_name}）。"
            return transform({call_args})
        except Exception as e:      # noqa: silent-ok — 插件异常一律转成中文
            return "执行时出错：%s" % str(e)[:100]


_PLUGIN = GenPlugin()


def get_plugin():
    return _PLUGIN
'''


def _pick_signature(body):
    """从模型给的函数体里认出它选了哪种形态：0/1/2 个参数。"""
    m = re.search(r"def\s+transform\s*\(([^)]*)\)", str(body or ""))
    if not m:
        return None
    args = [a.strip() for a in m.group(1).split(",") if a.strip()]
    return [a.split("=")[0].strip() for a in args]


def _mk_english_name(need):
    """给工具起一个合规英文名：优先用需求里的英文，否则按 hash 生成（稳定可复现）。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", str(need or ""))
    if words:
        name = "_".join(w.lower() for w in words[:3])
    else:
        import hashlib
        name = "gen_%s" % hashlib.md5(str(need or "").encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    if not re.match(r"^[a-z]", name):
        name = "gen_" + name
    return name[:40]


def forge_template(need, dry_run=False, timeout=30, max_repair=2):
    """模板优先路径：模型只写业务函数，契约由载体拼。成功率高得多。

    返回结构与 `forge()` 一致（ok/stage/why/module/path/tools/code）。
    """
    need = str(need or "").strip()
    if not need:
        return {"ok": False, "stage": "input", "why": "没说清要什么能力", "code": ""}
    try:
        import requests
    except Exception:
        return {"ok": False, "stage": "generate", "why": "环境里没有 requests", "code": ""}

    prompt = _TEMPLATE_PROMPT.format(allowed=", ".join(sorted(_ALLOWED_IMPORTS)),
                                     need=need[:300], lessons=_lessons_block(need))
    body = ""
    for model in ("qwopus3.5-4b-coder-mtp-q5_k_m.gguf", "coder", "xiaojiao"):
        try:
            r = requests.post(_base_url_v1() + "/chat/completions", json={
                "model": model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1200, "temperature": 0.2,
            }, timeout=180)
            if r.status_code == 200:
                body = _strip_fence(((r.json().get("choices") or [{}])[0].get("message") or {})
                                    .get("content") or "")
                if "def transform" in body:
                    break
                body = ""
        except Exception:      # noqa: silent-ok — 换下一个模型
            continue
    if not body:
        return {"ok": False, "stage": "generate", "why": "模型没写出业务函数", "code": ""}

    args = _pick_signature(body)

    def _assemble(b):
        """把业务函数 + 载体写死的契约拼成完整插件。"""
        sig = _pick_signature(b) or []
        props, docs, req = [], [], []
        for a in sig:
            props.append('                "%s": {"type": "string", "description": "参数 %s"}'
                         % (a, a))
            req.append('"%s"' % a)
        imports = "\n".join(l for l in b.split("\n")
                            if re.match(r"\s*(import|from)\s", l))
        body_only = "\n".join(l for l in b.split("\n")
                              if not re.match(r"\s*(import|from)\s", l))
        tool = _mk_english_name(need)
        desc = ("%s" % need)[:40].replace('"', "")
        return _TEMPLATE_PLUGIN.format(
            cn_name=need[:40].replace('"', ""), need=need[:200].replace('"', ""),
            imports=imports, body=body_only.strip(),
            tool_name=tool, desc=desc + "。什么时候用：" + need[:24].replace('"', ""),
            propsdoc="\n".join(props), required=", ".join(req),
            call_args=", ".join('(params or {}).get("%s")' % a for a in sig),
        )

    code = _assemble(body)
    module = _sanitize_module_name(need)
    target = os.path.join(_PLUGINS, module + ".py")
    if os.path.exists(target):
        module = "%s_%d" % (module, int(time.time()))
        target = os.path.join(_PLUGINS, module + ".py")

    last = {"ok": False, "stage": "unknown", "why": "没走到检查", "code": code}
    attempts = 1 + max(0, max_repair)
    for attempt in range(1, attempts + 1):
        ok, problems = static_check(code)
        if not ok:
            last = {"ok": False, "stage": "static", "why": "；".join(problems[:5]), "code": code}
            if attempt < attempts:
                fix = _ask_model_fix(need, code, last["why"])
                if fix.strip():
                    body = _strip_fence(fix)
                    if "def transform" in body:
                        code = _assemble(body)
                        continue
            break
        _cf = _conflicts(code)
        if _cf:
            last = {"ok": False, "stage": "conflict", "why": "工具名撞车：%s" % ",".join(_cf[:2]),
                    "code": code}
            break
        tmpdir = tempfile.mkdtemp(prefix="xj_forge_tpl_")
        tmp_path = os.path.join(tmpdir, module + ".py")
        io.open(tmp_path, "w", encoding="utf-8").write(code)
        ok2, why2, tools = sandbox_run(tmp_path, timeout=timeout)
        if not ok2:
            last = {"ok": False, "stage": "sandbox", "why": why2, "code": code}
            if attempt < attempts:
                fix = _ask_model_fix(need, code, why2)
                if fix.strip():
                    body = _strip_fence(fix)
                    if "def transform" in body:
                        code = _assemble(body)
                        continue
            break
        if dry_run:
            _remember(need, {"ok": True, "stage": "dry-run", "tools": tools, "attempts": attempt})
            return {"ok": True, "stage": "dry-run", "why": "模板路径全部检查通过（未落地）",
                    "module": module, "path": "", "tools": tools, "code": code,
                    "attempts": attempt, "fixed": 0, "via": "template"}
        try:
            os.makedirs(_PLUGINS, exist_ok=True)
            io.open(target, "w", encoding="utf-8").write(code)
        except Exception as e:
            return {"ok": False, "stage": "write",
                    "why": "写不进 plugins/：%s" % str(e)[:100], "code": code}
        _remember(need, {"ok": True, "stage": "written", "tools": tools, "attempts": attempt})
        return {"ok": True, "stage": "written", "why": "已落地（重启或热注册后生效）",
                "module": module, "path": target, "tools": tools, "code": code,
                "attempts": attempt, "fixed": 0, "via": "template"}
    _remember(need, last)
    return last


def _remember(need, result):
    """把这次成与败写进经验库（成功率、教训都从这里长出来）。失败不影响主流程。"""
    try:
        from core import experience as EXP
        EXP.remember_forge(need, result)
    except Exception as e:      # noqa: silent-ok
        LOG.debug("自增长：记经验失败（忽略）：%s", e)
