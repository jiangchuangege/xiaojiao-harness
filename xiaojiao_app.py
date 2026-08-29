# -*- coding: utf-8 -*-
"""
小焦 · XiaoJiao Web —— 本地部署的「联网搜索 AI」

架构：
        ┌────────────┐   ┌────────────┐   ┌────────────┐
  用户 →│  Web UI    │→│  Agent     │→│  LLM 大脑   │
        │ (Flask)    │  │ 上下文/记忆│  │ (openai兼容)│
        └────────────┘  └─────┬──────┘  └─────┬──────┘
                              │               │
                              ▼               ▼
                         ├─ web 检索 ──► 网络知识（大脑的外脑）
                         └─ 记忆自学习 ─► 知识沉淀，回调用
"""
import os, sys, json, re, time, threading, webbrowser
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
import requests
import torch

# ================== 配置（读取「操控文件」xiaojiao_control.json） ==================
# 你想让小焦成为什么类型的模型、用什么大脑、开哪些工具，全部由这个文件决定。
def _load_control():
    c = {"model_name": "xiaojiao1.0-4B", "web_port": 5000,
         "brain": {"engine": "auto",
                   "api": {"base_url": os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
                           "api_key": os.environ.get("LLM_API_KEY", ""),
                           "model": os.environ.get("LLM_MODEL", "llama")}},
         "role": ("你是“小焦”（xiaojiao1.0-4B），一个本地部署的联网搜索 AI 助手。"
                  "擅长联网检索并像人一样自然、有条理地回答。先结论后展开，简洁中文，必要时分点。"
                  "不要机械复读，要自然接话。"),
         "capabilities": {"web_search": True, "memory": True, "context_len": 20, "auto_deep_think": True},
         "behavior": {"temperature": 0.7, "max_tokens": 2048}}
    p = "xiaojiao_control.json"
    if os.path.exists(p):
        try:
            c.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass
    return c

CONTROL = _load_control()

MODEL_NAME = CONTROL.get("model_name", "xiaojiao1.0-4B")
BRAIN = CONTROL.get("brain", {})
BRAIN_ENGINE = BRAIN.get("engine", "auto")          # auto | llama | xiaojiao | api
LLM_BASE = BRAIN.get("api", {}).get("base_url", "http://127.0.0.1:8080/v1")
LLM_KEY = BRAIN.get("api", {}).get("api_key", "")
LLM_MODEL = BRAIN.get("api", {}).get("model", MODEL_NAME)
SYSTEM_PROMPT = CONTROL.get("role", "")             # ← 人设/类型，改 control 文件即换模型人格
CAP = CONTROL.get("capabilities", {})
FULL_ACCESS = CONTROL.get("capabilities", {}).get("full_access", True)  # True=全权限(危险命令也不询问直接执行)；False=只读(每次执行都询问)
BEH = CONTROL.get("behavior", {})

HISTORY_FILE = "xiaojiao_history.json"              # 对话上下文（持久化）
MEMORY_FILE = "xiaojiao_knowledge_memory.json"      # 自学习记忆
MAX_HISTORY = int(CAP.get("context_len", 20))
WEB_TIMEOUT = 12
TEMPERATURE = float(BEH.get("temperature", 0.7))
MAX_TOKENS = int(BEH.get("max_tokens", 1024))


def reload_control():
    """从操控文件重新载入配置（设置页保存后即刻生效）。"""
    global CONTROL, MODEL_NAME, BRAIN_ENGINE, LLM_BASE, LLM_KEY, LLM_MODEL
    global SYSTEM_PROMPT, CAP, BEH, MAX_HISTORY, TEMPERATURE, MAX_TOKENS
    CONTROL = _load_control()
    MODEL_NAME = CONTROL.get("model_name", "xiaojiao1.0-4B")
    BRAIN = CONTROL.get("brain", {})
    BRAIN_ENGINE = BRAIN.get("engine", "auto")
    LLM_BASE = BRAIN.get("api", {}).get("base_url", "http://127.0.0.1:8080/v1")
    LLM_KEY = BRAIN.get("api", {}).get("api_key", "")
    LLM_MODEL = BRAIN.get("api", {}).get("model", MODEL_NAME)
    SYSTEM_PROMPT = CONTROL.get("role", "")
    CAP = CONTROL.get("capabilities", {})
    BEH = CONTROL.get("behavior", {})
    MAX_HISTORY = int(CAP.get("context_len", 20))
    TEMPERATURE = float(BEH.get("temperature", 0.7))
    MAX_TOKENS = int(BEH.get("max_tokens", 1024))


_ctlmtime = 0
def maybe_reload_control():
    """操控文件改动后自动热更新（人设/大脑/参数不必重启）。"""
    global _ctlmtime
    try:
        m = os.path.getmtime("xiaojiao_control.json")
    except Exception:
        return
    if m != _ctlmtime:
        _ctlmtime = m
        reload_control()
        global PLUGINS
        PLUGINS = load_plugins()
# ================== 插件系统 ==================
PLUGIN_SKILLS = []   # .md 技能/知识插件，会拼进人设


def _api_execute(manifest, tool_name, params):
    """执行 API 插件：按其 declaration 调 HTTP 接口。"""
    t = next((x for x in manifest.get("tools", []) if x.get("name") == tool_name), None)
    if not t:
        return None
    method = (t.get("method", "GET")).upper()
    url = t.get("url", "")
    for k, v in (params or {}).items():
        url = url.replace("{" + str(k) + "}", str(v))
    headers = t.get("headers", {})
    try:
        body = None
        if method in ("POST", "PUT", "PATCH"):
            body = (params or {}).get("body") or {k: v for k, v in (params or {}).items() if k not in t.get("body_exclude", [])}
        r = requests.request(method, url, params=(params or {}), json=body, headers=headers, timeout=t.get("timeout", 30))
        if t.get("response") == "json":
            j = r.json()
            fld = t.get("field")
            return str(j.get(fld) if fld else j)
        return r.text[:2000]
    except Exception as e:
        return f"API 插件执行失败：{e}"


def _make_api_plugin(manifest):
    """把一个 API 插件 manifest 变成可用插件实例（get_tool_descriptions/execute）。"""
    class _ApiPlugin:
        def get_tool_descriptions(self):
            return [{"name": t.get("name"), "description": t.get("description", ""),
                     "parameters": t.get("parameters", {"type": "object", "properties": {}})}
                    for t in manifest.get("tools", []) if t.get("name")]
        def execute(self, name, params):
            return _api_execute(manifest, name, params)
    return _ApiPlugin()


def load_plugins():
    """扫描 plugins/ 目录，支持三种插件类型：
       - .py    Python 工具插件（class 含 get_tool_descriptions/execute）
       - .json  API 插件（把 HTTP 接口声明成工具）
       - .md    技能/知识插件（内容拼进人设）
    返回 { 插件名: {"instance":..., "desc":[...], "type":..., "on":..., "path":...} }。
    """
    import importlib.util as ilu
    global PLUGIN_SKILLS
    plugins = {}
    for n in ("web_search", "memory"):
        plugins[n.replace("_", "-")] = {"builtin": True, "on": True, "type": "builtin",
                                        "desc": [{"name": n, "description": "小焦内置能力"}]}
    if not os.path.isdir("plugins"):
        return plugins
    for fn in os.listdir("plugins"):
        p = os.path.join("plugins", fn)
        base = os.path.splitext(fn)[0]
        try:
            if fn.endswith(".py") and not fn.startswith("__"):
                spec = ilu.spec_from_file_location(base, p)
                mod = ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr in dir(mod):
                    obj = getattr(mod, attr)
                    if isinstance(obj, type) and hasattr(obj, "get_tool_descriptions") and hasattr(obj, "execute"):
                        inst = obj()
                        desc = inst.get_tool_descriptions()
                        if desc:
                            plugins[base] = {"instance": inst, "desc": desc, "builtin": False, "type": "py", "path": p}
                        break
            elif fn.endswith(".json"):
                man = json.load(open(p, encoding="utf-8"))
                if man.get("type") == "skin":
                    plugins[base] = {"instance": None, "desc": [], "builtin": False, "type": "skin", "path": p, "manifest": man}
                else:
                    inst = _make_api_plugin(man)
                    desc = inst.get_tool_descriptions()
                    if desc:
                        plugins[base] = {"instance": inst, "desc": desc, "settings": man.get("settings", []),
                                         "builtin": False, "type": "api", "path": p, "manifest": man}
            elif fn.endswith(".md"):
                PLUGIN_SKILLS.append((base, open(p, encoding="utf-8").read().strip()))
            elif fn.endswith((".js", ".mjs")) and fn != "plugin_runner.js":
                import subprocess
                desc = []
                settings = []
                try:
                    r = subprocess.run(["node", os.path.join("plugins", "plugin_runner.js"), "describe", p],
                                       capture_output=True, text=True, timeout=30, encoding="utf-8")
                    desc = json.loads(r.stdout.strip()) if r.stdout.strip() else []
                    r2 = subprocess.run(["node", os.path.join("plugins", "plugin_runner.js"), "settings", p],
                                        capture_output=True, text=True, timeout=30, encoding="utf-8")
                    settings = json.loads(r2.stdout.strip()) if r2.stdout.strip() else []
                except Exception:
                    desc = []
                if desc:
                    plugins[base] = {"instance": None, "desc": desc, "settings": settings,
                                     "builtin": False, "type": "js", "path": p}
        except Exception:
            continue
    # 依据操控文件的插件开关
    for k in plugins:
        plugins[k]["on"] = CAP.get("plugins", {}).get(k, plugins[k].get("on", True))
    return plugins


PLUGINS = load_plugins()   # 插件注册表（设置页可开关）


def run_plugin(name, params):
    """调用某个插件（由 LLM/Agent 决定何时用）。支持 py / api / js。"""
    p = PLUGINS.get(name)
    if not p or not p.get("on"):
        return None
    # JS 插件：起 node 子进程执行
    if p.get("type") == "js":
        import subprocess
        try:
            r = subprocess.run(["node", os.path.join("plugins", "plugin_runner.js"), "exec",
                                p.get("path"), params.get("name"), json.dumps(params.get("params", {}), ensure_ascii=False)],
                               capture_output=True, text=True, timeout=90, encoding="utf-8")
            return (r.stdout or r.stderr).strip()
        except Exception as e:
            return f"JS插件执行失败：{e}"
    if "instance" not in p:
        return None
    try:
        return p["instance"].execute(params.get("name"), params.get("params", {}))
    except Exception:
        return None


_TOOL2PLUGIN = {}   # 工具名 -> 插件模块名


def _build_tools():
    """把内置工具 + 已启用的插件工具合并成给模型的功能列表。"""
    global _TOOL2PLUGIN
    _TOOL2PLUGIN = {}
    tools = list(TOOLS)
    for pname, p in PLUGINS.items():
        if p.get("builtin") or not p.get("on"):
            continue
        for t in p.get("desc", []):
            if isinstance(t, dict) and t.get("name"):
                _TOOL2PLUGIN[t["name"]] = pname
                tools.append({"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}})}})
    return tools

# ================== 小焦模型 ==================
# 大脑：优先用你创建的小焦模型（mini_gpt_model.pth）；若配置了外部 LLM 则优先外部。
XJ_READY = False
try:
    import xiaojiao_harness as xh
    if os.path.exists(xh.MODEL_PATH) and os.path.exists(xh.VOCAB_PATH):
        XJ_MODEL, XJ_C2I, XJ_I2C = xh.load_model()
        XJ_READY = True
        print("🧠 大脑：小焦模型 已加载")
    else:
        XJ_MODEL, XJ_C2I, XJ_I2C = None, None, None
except BaseException as e:
    XJ_MODEL, XJ_C2I, XJ_I2C = None, None, None
    print("🧠 未加载到小焦模型：", e)


def xiaojiao_reply(text):
    """用你创建的小焦模型生成一句话回复（承接语料格式：用户…小焦…）。"""
    if not XJ_READY:
        return None
    prompt = "用户" + text + "小焦"
    ids = [XJ_C2I.get(c, 0) for c in prompt]
    idx = torch.tensor([ids], dtype=torch.long, device=xh.DEVICE)
    resp = xh.generate(XJ_MODEL, idx, XJ_I2C)
    resp = resp.strip()
    for sep in ("\n", "用户", "小焦"):
        if sep in resp:
            resp = resp.split(sep)[0]
            break
    return resp.strip()


# ================== 工具：联网搜索 ==================
def _clean_html(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&#\d+;|&[a-z]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def web_search(query, num=6):
    """免密钥 Bing/Sogou 中文搜索，返回 [(标题, 内容)]。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    engines = [("https://cn.bing.com/search?q=", r'<li class="b_algo"[^>]*>(.*?)</li>'),
               ("https://www.sogou.com/web?query=", r'<div class="vrwrap"[^>]*>(.*?)</div>')]
    out, seen = [], set()
    for base, block_re in engines:
        try:
            r = requests.get(base + requests.utils.quote(query), headers=headers, timeout=WEB_TIMEOUT)
            if r.status_code != 200:
                continue
            for block in re.findall(block_re, r.text, re.S):
                h2 = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.S)
                if not h2:
                    h2 = re.search(r'<a[^>]*>(.*?)</a>', block, re.S)
                title = _clean_html(h2.group(1)) if h2 else ""
                if "›" in title:
                    title = title.split("›")[-1].strip()
                p = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
                content = _clean_html(p.group(1)) if p else ""
                content = content or title
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


# ================== 记忆（自学习） ==================
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            return json.load(open(MEMORY_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_memory(mem):
    try:
        json.dump(mem, open(MEMORY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def _key(text):
    # 用“最像内容词”的双字组做记忆键
    return "".join(re.findall(r"[\u4e00-\u9fff]{2,}", text or "")[:2]) or text[:4]


def remember(query, knowledge):
    """自学习：把本次联网学到的知识沉淀到记忆里，供以后检索。"""
    if not knowledge:
        return
    mem = load_memory()
    k = _key(query)
    mem.setdefault(k, {"q": query, "know": [], "ts": datetime.now().isoformat()})
    for item in knowledge:
        if item not in mem[k]["know"]:
            mem[k]["know"].append(item)
    mem[k]["know"] = mem[k]["know"][-8:]      # 每个主题最多留 8 条
    mem[k]["ts"] = datetime.now().isoformat()
    save_memory(mem)


def recall(query):
    """检索记忆：返回与 query 相关的历史学习到的知识（字符重合打分）。"""
    mem = load_memory()
    qset = set(query)
    scored = []
    for k, v in mem.items():
        overlap = len(qset & set(k)) + len(qset & set(v.get("q", "")))
        if overlap >= 2:
            scored.append((overlap, v))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:3]]


# ================== 上下文 ==================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            return json.load(open(HISTORY_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history(hist):
    try:
        json.dump(hist[-MAX_HISTORY:], open(HISTORY_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:
        pass


# ================== 会话存储（每个新对话一个会话，可切换） ==================
SESSIONS_FILE = "xiaojiao_sessions.json"


def _sessions():
    try:
        d = json.load(open(SESSIONS_FILE, encoding="utf-8"))
        if isinstance(d, dict) and "sessions" in d:
            return d
    except Exception:
        pass
    default = {"id": "default", "title": "新对话", "messages": []}
    return {"current": "default", "sessions": [default]}


def _save_sessions(d):
    try:
        json.dump(d, open(SESSIONS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_current_session():
    d = _sessions()
    cid = d.get("current")
    for s in d["sessions"]:
        if s["id"] == cid:
            return s, d
    return d["sessions"][0], d


def current_messages():
    s, _ = get_current_session()
    return s.get("messages", [])


def append_msg(role, content):
    s, d = get_current_session()
    s.setdefault("messages", []).append({"role": role, "content": content})
    if role == "用户" and len(s["messages"]) == 1 and not s.get("title") or s.get("title") == "新对话":
        s["title"] = content[:24]
    _save_sessions(d)


# ================== 大脑：LLM 调用 ==================
def llm_chat(messages):
    """调用 OpenAI 兼容 /chat/completions。失败返回 None。"""
    url = LLM_BASE.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_KEY:
        headers["Authorization"] = "Bearer " + LLM_KEY
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": TEMPERATURE,
               "max_tokens": MAX_TOKENS}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return None


def llm_online():
    host = LLM_BASE.split("//")[-1].split("/")[0]
    try:
        return requests.get("http://" + host + "/health", timeout=3).status_code == 200
    except Exception:
        return False


# ================== 工具（操控电脑，function calling） ==================
import subprocess

TOOLS = [
    {"type": "function", "function": {"name": "run_command", "description": "运行一条系统命令并返回输出",
     "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "number", "description": "超时秒数，默认30"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "open_app", "description": "打开一个应用或文件/网址",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "应用或文件或网址"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "列出目录内容",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "目录路径"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "读取一个文本文件的前若干字符",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "文件路径"},
                    "max_chars": {"type": "number", "description": "最多读多少字符"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "把文本写入文件",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "写入的内容"}}, "required": ["path", "content"]}}},
]


DANGEROUS_CMD = re.compile(r"\b(rm|del|rd|format|shutdown|reboot|mkfs|dd|reg\s+delete|taskkill\s+/f|net\s+user|netsh|icacls|takeown|chkdsk\s+/f|tskill|vssadmin)\b", re.I)
SAFE_ROOT = os.path.abspath(os.getcwd())
PENDING = None          # 待用户确认的危险动作 (name, args)


def is_dangerous(name, args):
    args = args or {}
    if name == "run_command":
        cmd = args.get("command", "")
        if DANGEROUS_CMD.search(cmd):
            return True
        # 命令看起来无害，但改动系统目录也谨慎
    if name == "write_file":
        p = (args.get("path") or "").lower().replace("\\", "/")
        for t in ("c:/windows", "c:/program files", "system32", "/etc/", "/var/", "/usr/", "c:/system"):
            if t in p:
                return True
    return False


def run_tool(name, args, force=False):
    global PENDING
    args = args or {}
    # 权限模式：Full access(默认)=所有命令直接执行、危险命令也不询问；Read-only=每次执行命令都询问
    if not force:
        ask = (not FULL_ACCESS and name in ("run_command", "write_file", "open_app"))
        if ask:
            PENDING = (name, args)
            desc = f"运行命令「{args.get('command','')}」" if name == "run_command" else f"写入文件「{args.get('path','')}」"
            return f"〔待确认〕小焦想执行：{desc}。请用户确认后再执行。"
    PENDING = None
    try:
        if name == "run_command":
            cmd = args.get("command", "")
            timeout = int(args.get("timeout", 30))
            if os.name == "nt":
                # PowerShell 才能运行 New-Item 等 cmdlet；强制 UTF-8 输出避免中文乱码
                cmd = '[Console]::OutputEncoding=[Text.Encoding]::UTF8;$OutputEncoding=[Text.Encoding]::UTF8;' + cmd
                res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                                     capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
            else:
                res = subprocess.run(cmd, shell=True, capture_output=True, encoding="utf-8",
                                     errors="replace", timeout=timeout)
            combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            return combined[:2500] or "(无输出)"
        if name == "open_app":
            webbrowser.open(args.get("path", ""))
            return "已打开 " + args.get("path", "")
        if name == "list_files":
            p = args.get("path", ".")
            return "\n".join(os.listdir(p))[:2500]
        if name == "read_file":
            p = args.get("path", "")
            n = int(args.get("max_chars", 2500))
            return open(p, encoding="utf-8", errors="replace").read(n)
        if name == "write_file":
            p, c = args.get("path", ""), args.get("content", "")
            parent = os.path.dirname(os.path.abspath(p)) if p else ""
            if parent:
                os.makedirs(parent, exist_ok=True)   # 自动建父目录
            with open(p, "w", encoding="utf-8") as f:
                f.write(c)
            return f"已写入 {p}"
    except FileNotFoundError as e:
        return f"文件/路径不存在：{e}"
    except Exception as e:
        return f"工具执行失败：{type(e).__name__}: {e}"
    # 插件工具（自定义）
    pn = _TOOL2PLUGIN.get(name)
    if pn:
        return run_plugin(pn, {"name": name, "params": args})
    return "未知工具"


def parse_xml_tool(text):
    """解析 Qwen 风格的 <tool_call><function=name><parameter=k>v</parameter>...</function></tool_call>。"""
    calls = []
    for m in re.finditer(r"<tool_call>\s*<function=([\w-]+)>(.*?)</function>\s*</tool_call>", text, re.S):
        name = m.group(1)
        params = dict(re.findall(r"<parameter=([\w-]+)>(.*?)</parameter>", m.group(2), re.S))
        calls.append((name, {k: v.strip() for k, v in params.items()}))
    return calls


def _map_tool(name, args):
    """工具别名：把不同模型叫法统一到小焦自己的工具上。"""
    name = (name or "").lower()
    if name in ("pwsh", "powershell", "cmd", "terminal", "shell", "bash", "sh", "exec", "run", "execute"):
        return "run_command", args
    return name, args


def llm_chat_tools(messages, max_rounds=6):
    """带 function calling 的大脑调用：模型自己“想”并调用工具（优先），循环直到给出最终回答。

    返回 (answer, tool_trace)。兼容 OpenAI tool_calls 与 Qwen <tool_call> XML。
    """
    url = LLM_BASE.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_KEY:
        headers["Authorization"] = "Bearer " + LLM_KEY
    m = list(messages)
    tool_trace = []
    for _ in range(max_rounds):
        payload = {"model": LLM_MODEL, "messages": m, "temperature": TEMPERATURE,
                   "max_tokens": MAX_TOKENS, "tools": _build_tools()}
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code != 200:
                return None, tool_trace
            msg = r.json()["choices"][0]["message"]
        except Exception:
            return None, tool_trace
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            content = msg.get("content") or ""
            xmlcalls = parse_xml_tool(content)
            if not xmlcalls:
                return content.strip(), tool_trace
            # Qwen XML 工具调用：执行并让模型基于结果续写
            m.append({"role": "assistant", "content": content})
            for name, args in xmlcalls:
                tname, targs = _map_tool(name, args)
                result = run_tool(tname, targs)
                tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
                if result.startswith("〔待确认〕"):
                    m.append({"role": "tool", "content": result})
                    return result, tool_trace
                m.append({"role": "tool", "content": result})
            continue
        # OpenAI 标准工具调用
        m.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tname, targs = _map_tool(fn.get("name", ""), args)
            result = run_tool(tname, targs)
            tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
            if result.startswith("〔待确认〕"):
                m.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
                return result, tool_trace
            m.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
    return None, tool_trace


# ===== 强制工具执行：意图检测 + 让模型生成工具JSON（兼容任何模型） =====
_TOOL_HINTS = [
    ("run_command", ["运行", "执行", "命令", "跑一下", "建文件夹", "创建文件夹", "新建目录", "建目录",
                     "创建目录", "mkdir", "删掉", "删除", "移动", "复制文件", "清理", "关机"]),
    ("write_file", ["创建文件", "新建文件", "写文件", "写入", "保存到", "保存为", "生成文件", "写成", "输出到文件"]),
    ("open_app", ["打开", "启动", "运行应用", "打开应用"]),
    ("list_files", ["列出", "查看目录", "列出文件", "有哪些文件", "看下目录", "list"]),
    ("read_file", ["读取", "查看文件", "读出", "读文件"]),
]


def detect_tool_intent(q):
    """粗略判断用户请求是否属于“执行类操作”，返回工具类型或 None（仅作兜底提示）。"""
    ql = q.lower()
    is_create = any(k in ql for k in ("创建", "新建", "写", "保存", "生成", "建立", "做一个", "写一个"))
    is_file = any(k in ql for k in (".txt", ".py", ".html", ".md", ".json", ".js", "index.", "文件", "file"))
    is_folder = any(k in ql for k in ("文件夹", "目录", "folder", "dir"))
    if is_create and is_file:
        return "write_file"
    if is_create and is_folder and not is_file:
        return "run_command"
    if any(k in ql for k in ("运行", "执行", "命令", "跑一下", "删掉", "删除", "移动", "复制", "清理", "关机", "格式化", "mkdir")):
        return "run_command"
    if any(k in ql for k in ("打开", "启动")):
        return "open_app"
    if any(k in ql for k in ("列出", "查看目录", "有哪些文件", "list")):
        return "list_files"
    if any(k in ql for k in ("读取", "查看文件", "读出", "读文件")):
        return "read_file"
    return None


def _llm_ask_raw(prompt):
    url = LLM_BASE.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LLM_KEY:
        headers["Authorization"] = "Bearer " + LLM_KEY
    try:
        r = requests.post(url, headers=headers,
                          json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.2, "max_tokens": MAX_TOKENS}, timeout=90)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


def plan_tool(user_input):
    """让大脑把请求转成一个工具调用 JSON，返回 (tool, args)；失败返回 (None, None)。"""
    prompt = ("用户请求：%s\n\n请把该请求转换为一个工具调用，只输出一个 JSON 对象，不要任何说明。\n"
              "可用工具：run_command(运行PowerShell命令,参数 command)、write_file(写文件,参数 path,content)、"
              "open_app(打开应用/文件,参数 path)、list_files(列目录,参数 path)、read_file(读文件,参数 path,max_chars)。\n"
              "JSON 格式：{\"tool\":\"工具名\",\"args\":{\"参数\":\"值\"}}\n"
              "例如：{\"tool\":\"write_file\",\"args\":{\"path\":\"C:/Users/Jiao/Desktop/a.txt\",\"content\":\"hi\"}}" % user_input)
    out = _llm_ask_raw(prompt)
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            if isinstance(j, dict) and j.get("tool"):
                return j["tool"], j.get("args", {})
        except Exception:
            pass
    return None, None


def _summarize_tool(user_input, result, tool):
    """让大脑基于工具结果给一句简短总结。"""
    prompt = ("你用 %s 工具执行了用户请求，结果如下：\n%s\n\n"
              "请用一句简短中文告诉用户完成了什么（例如：已在 XXX 创建了 YYY）。不要重复结果内容，不要科普。" % (tool, result[:1200]))
    s = _llm_ask_raw(prompt)
    return s or ("已完成（%s）。" % tool)


# ================== 智能体 ==================
def agent_run(user_input):
    """全部问题统一走这条流程：记忆 → 联网检索 → 大脑(小焦模型/外接LLM) → 记忆自学习。
    
    注：不再代理给 DSH 桥接（那会造成 小焦→桥接→小焦 的死循环）。
    DSH 兼容的正确方式是：DSH harness 连小焦的 /v1 当模型，DSH 的插件在 DSH 里自己跑。
    """
    # ===== 原有的 agent_run 逻辑 =====
    history = current_messages()

    # 1. 相关记忆（受操控文件 capabilities 控制）
    mem_text = ""
    if CAP.get("memory", True):
        mem = recall(user_input)
        mem_text = "\n".join(f"- {m['q']}：{m['know'][0]}" for m in mem[:2]) if mem else ""

    # 2. 联网检索（受操控文件 capabilities 控制）
    info = []
    if CAP.get("web_search", True):
        info = web_search(user_input, num=5)
    web_text = "\n".join(f"{t}：{c}" for t, c in info[:4]) if info else ""

    # 3. 大脑回答：遵循操控文件的 brain.engine
    answer = None
    tool_trace = []
    want_llm = llm_online() if BRAIN_ENGINE == "auto" else (BRAIN_ENGINE in ("llama", "api"))
    has_llm = want_llm and llm_online()

    # ① 优先让大模型自己“想”并调用工具（原生 function calling / <tool_call> XML）
    if has_llm:
        home = os.path.expanduser("~")
        desktop = os.path.join(home, "Desktop")
        path_ctx = ("\n[环境] 当前工作目录：%s；用户主目录：%s；桌面：%s。"
                    "凡是要创建文件/文件夹/读写文件，一律用绝对路径（如桌面文件用 %s\\文件名）。" % (os.getcwd(), home, desktop, desktop))
        skills = "\n\n[技能插件] " + "\n\n".join(c for _, c in PLUGIN_SKILLS) if PLUGIN_SKILLS else ""
        messages = [{"role": "system", "content": SYSTEM_PROMPT + path_ctx + skills}]
        for h in history[-MAX_HISTORY:]:
            messages.append({"role": "user" if h["role"] == "用户" else "assistant",
                             "content": h["content"]})
        context = ""
        if mem_text:
            context += "（相关记忆）\n" + mem_text + "\n\n"
        if web_text:
            context += "（联网检索到的资料）\n" + web_text + "\n\n"
        messages.append({"role": "user", "content": (context + "用户：" + user_input) if context else user_input})
        if CAP.get("run_tools", True):
            answer, tool_trace = llm_chat_tools(messages)   # 模型推理并调用工具
        else:
            answer = llm_chat(messages)

    # ② 兜底：大模型没调用任何工具，但这是"执行类操作" → 用 plan 强制生成一次工具调用
    if not tool_trace and CAP.get("run_tools", True) and has_llm:
        it = detect_tool_intent(user_input)
        if it:
            tname, targs = plan_tool(user_input)
            if tname:
                tname, targs = _map_tool(tname, targs)
                result = run_tool(tname, targs, force=True)
                tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
                answer = _summarize_tool(user_input, result, tname)

    # ③ 大模型不在线但有执行类操作 → 明确提示，不胡诌
    if not has_llm and CAP.get("run_tools", True):
        it = detect_tool_intent(user_input)
        if it:
            answer = ("⚠️ 需要执行工具操作「%s」，但当前没有可用的智能大脑（本地大模型未启动）。"
                      "请先运行 `python start_xiaojiao.py` 启动大模型，我再帮你真正执行。" % it)
            tool_trace = [{"tool": it, "args": {}, "result": "未执行：大模型未在线"}]

    # —— 注意：已停用自建小模型的语言生成（只会胡诌），绝不用于说话 ——

    # 4. 记忆自学习沉淀
    learned = [c for _, c in info[:3]]
    if learned and CAP.get("memory", True):
        remember(user_input, learned)

    # 5. 落地上下文
    if answer:
        needs_confirm = PENDING is not None and answer.startswith("〔待确认〕")
        return answer, True, info, needs_confirm, tool_trace

    # 6. 无任何可用大脑（本地大模型未连接）时的降级（只给一句简洁提示，不瞎输出联网内容）
    fallback = "🤖 本地大模型未连接（8080 未启动），小焦暂时没法回答。\n请先运行 `python start_xiaojiao.py` 启动大模型，或确认 8080 端口已就绪。"
    return fallback, False, [], False, []


app = Flask(__name__)


# ===== 扩展：真·文生视频（video_service / ComfyUI + Wan，按需切换模型）=====
_vdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_service")
if _vdir not in sys.path:
    sys.path.insert(0, _vdir)
try:
    from video_api import bp as _video_bp
    app.register_blueprint(_video_bp)
    print("🎬 视频服务已挂载（ComfyUI + Wan2.1，按需切换模型）")
except Exception as _e:
    print("⚠️ 视频服务未挂载:", _e)



def _hist_json():
    return current_messages()[-MAX_HISTORY:]



@app.route("/api/workspace")
def api_workspace():
    """列出项目文件夹内容(工作区)。"""
    root = os.path.dirname(os.path.abspath(__file__))
    out = []
    try:
        for name in sorted(os.listdir(root)):
            fp = os.path.join(root, name)
            if name.startswith(".") or name in ("__pycache__", "logs", "bak"):
                continue
            typ = "dir" if os.path.isdir(fp) else "file"
            if typ == "file":
                try: sz = "%.1fK" % (os.path.getsize(fp) / 1024)
                except Exception: sz = ""
            else:
                sz = ""
            out.append({"name": name, "type": typ, "size": sz})
    except Exception:
        pass
    return jsonify(out)


@app.route("/api/ws/open", methods=["POST"])
def api_ws_open():
    """读取项目内一个文本文件(工作区预览)。防目录穿越。"""
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").replace("\\", "/").strip()
    root = os.path.dirname(os.path.abspath(__file__))
    if not name or ".." in name or name.startswith("/"):
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    fp = os.path.normpath(os.path.join(root, name))
    if not fp.startswith(root) or not os.path.exists(fp) or os.path.isdir(fp):
        return jsonify({"ok": False, "error": "不存在"}), 404
    try:
        size = os.path.getsize(fp)
        if size > 200000:
            return jsonify({"ok": False, "error": "文件过大(>200KB)"}), 413
        return jsonify({"ok": True, "name": name, "content": open(fp, encoding="utf-8", errors="replace").read()[:200000]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/api/chat/pending")
def api_chat_pending():
    """当前会话最后一条小焦消息是否仍在生成(pending)；刷新后前端据此续显"正在回答"。"""
    try:
        msgs = current_messages()
        last = msgs[-1] if msgs else {}
        bot = last if last.get("role") == "小焦" else None
        pending = bool(bot and "__pending__" in str(bot.get("content", "")))
        return jsonify({"pending": pending, "content": "" if pending else (bot or {}).get("content", "")})
    except Exception as e:
        return jsonify({"pending": False, "content": ""})





@app.route("/api/sessions")
def api_sessions():
    d = _sessions()
    return jsonify({"current": d.get("current"),
                    "sessions": [{"id": s.get("id"), "title": s.get("title", "新对话"),
                                  "count": len(s.get("messages", []))} for s in d["sessions"]]})


@app.route("/api/session/new", methods=["POST"])
def api_session_new():
    import uuid
    d = _sessions()
    sid = uuid.uuid4().hex[:10]
    d["sessions"].insert(0, {"id": sid, "title": "新对话", "messages": []})
    d["current"] = sid
    _save_sessions(d)
    return jsonify({"ok": True, "id": sid})


@app.route("/api/session/<sid>")
def api_session(sid):
    d = _sessions()
    for s in d["sessions"]:
        if s["id"] == sid:
            d["current"] = sid
            _save_sessions(d)
            return jsonify({"id": sid, "title": s.get("title"), "messages": s.get("messages", [])})
    return jsonify({"error": "会话不存在"}), 404


@app.route("/")
def index():
    return render_template_string(HTML, model_name=MODEL_NAME)


@app.route("/api/history")
def api_history():
    return jsonify(_hist_json())


@app.route("/api/chat", methods=["POST"])
def api_chat():
    maybe_reload_control()
    data = request.get_json(force=True, silent=True) or {}
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "空消息"}), 400
    # 先写用户 + 占位(空)小焦消息：刷新后能读到"正在回答"
    append_msg("用户", user_input)
    append_msg("小焦", "⏳__pending__")
    answer, online, info, needs_confirm, tool_trace = agent_run(user_input)
    # 把占位小焦消息更新为真实回答（含最后那句提示）
    answer_final = answer
    if not answer_final:
        answer_final = "🤖 本地大模型未连接（8080 未启动），小焦暂时没法回答。请先运行 `python start_xiaojiao.py`。"
    s, d = get_current_session()
    for m in s.get("messages", []):
        if m.get("role") == "小焦" and "__pending__" in str(m.get("content", "")):
            m["content"] = answer_final
    _save_sessions(d)
    log_id = _record_interaction(user_input, answer_final, tool_trace)   # 内置·自动记录
    return jsonify({
        "answer": answer,
        "brain_online": online,
        "needs_confirm": needs_confirm,
        "tool_trace": tool_trace,
        "tools_on": bool(CAP.get("run_tools", True)),
        "session_id": get_current_session()[0].get("id"),
        "log_id": log_id,
        "sources": [{"title": t, "content": c} for t, c in info],
        "history": _hist_json(),
    })


# ========== 内置·持续学习（自动记录 + 自动打勾） ==========
ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(ROOT, "logs")
KNOW_FILE = os.path.join(ROOT, "self_learn", "little_brain_knowledge.txt")


def _record_interaction(user, answer, tool_trace):
    """答完自动记录这次交互（稳定记录），返回 log_id。"""
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_id = datetime.now().strftime("%Y%m%d_%H%M%S%f")
        rec = {"time": datetime.now().isoformat(timespec="seconds"),
               "log_id": log_id, "user": user, "final_reply": answer,
               "tool_trace": json.dumps(tool_trace or [], ensure_ascii=False)}
        with open(os.path.join(LOGS_DIR, "chat_history.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return log_id
    except Exception:
        return None


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """用户点 👍/👎/更正 → 记录反馈；被赞/高星/被更正的立刻灌进小脑知识库（稳定学习）。"""
    d = request.get_json(force=True, silent=True) or {}
    log_id, fb, corr = d.get("log_id", ""), d.get("feedback", ""), d.get("corrected", "")
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(os.path.join(LOGS_DIR, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                "log_id": log_id, "feedback": fb, "corrected_reply": corr},
                               ensure_ascii=False) + "\n")
        worth = bool(corr) or fb in ("good", "👍") or str(fb).strip("星") in ("4", "5")
        if worth and log_id:
            for ln in open(os.path.join(LOGS_DIR, "chat_history.jsonl"), encoding="utf-8"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("log_id") == log_id:
                    u, a = r.get("user", ""), (corr or r.get("final_reply", ""))
                    tt = r.get("tool_trace", "")
                    lesson = "用户 %s 小焦 %s%s" % (u, a, (" 用工具:%s" % tt) if tt else "")
                    os.makedirs(os.path.dirname(KNOW_FILE), exist_ok=True)
                    with open(KNOW_FILE, "a", encoding="utf-8") as kf:
                        kf.write(lesson + "\n")
                    pool = os.path.join(ROOT, "training_data_pool_clean.txt")
                    if os.path.exists(pool):
                        with open(pool, "a", encoding="utf-8") as pf:
                            pf.write(lesson + "\n")
                    break
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/api/brain")
def api_brain():
    """『小脑』完整数据：成长统计 + 学到的功能用法 + 反思。"""
    root = os.path.dirname(os.path.abspath(__file__))
    def cnt(rel):
        try:
            return sum(1 for _ in open(os.path.join(root, rel), encoding="utf-8"))
        except Exception:
            return 0
    def tail(rel, n=14):
        try:
            lines = [x.strip() for x in open(os.path.join(root, rel), encoding="utf-8") if x.strip()]
            return lines[-n:]
        except Exception:
            return []
    know = cnt(os.path.join("self_learn", "little_brain_knowledge.txt"))
    logs = cnt(os.path.join("logs", "chat_history.jsonl"))
    good = bad = corr = 0
    try:
        for ln in open(os.path.join(root, "logs", "feedback.jsonl"), encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            f = r.get("feedback", "")
            if f in ("good", "👍") or str(f).strip("星") in ("4", "5"):
                good += 1
            elif f in ("bad", "👎"):
                bad += 1
            if r.get("corrected_reply"):
                corr += 1
    except Exception:
        pass
    try:
        import sys as _s, os as _o
        _s.path.insert(0, os.path.join(root, "self_learn"))
        import vstore
        vc = vstore.count()
    except Exception:
        vc = 0
    lessons = tail(os.path.join("self_learn", "little_brain_knowledge.txt"))
    return jsonify({"know": know, "logs": logs, "good": good, "bad": bad, "corr": corr,
                    "vec": vc, "lessons": lessons})


@app.route("/api/growth")

def api_growth():
    """小脑成长指标(面板用)。"""
    root = os.path.dirname(os.path.abspath(__file__))
    def cnt(rel):
        try:
            return sum(1 for _ in open(os.path.join(root, rel), encoding="utf-8"))
        except Exception:
            return 0
    know = cnt(os.path.join("self_learn", "little_brain_knowledge.txt"))
    logs = cnt(os.path.join("logs", "chat_history.jsonl"))
    good = bad = corr = 0
    try:
        for ln in open(os.path.join(root, "logs", "feedback.jsonl"), encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            f = r.get("feedback", "")
            if f in ("good", "👍"):
                good += 1
            elif f in ("bad", "👎"):
                bad += 1
            elif str(f).strip("星") in ("4", "5"):
                good += 1
            if r.get("corrected_reply"):
                corr += 1
    except Exception:
        pass
    return jsonify({"know": know, "logs": logs, "good": good, "bad": bad, "corr": corr})


@app.route("/api/persona", methods=["POST"])
def api_persona():
    """切换人格：把 role 写回控制文件并生效。"""
    d = request.get_json(force=True, silent=True) or {}
    role = (d.get("role") or "").strip()
    if not role:
        return jsonify({"ok": False, "error": "人格不能为空"}), 400
    try:
        c = json.loads(open("xiaojiao_control.json", encoding="utf-8").read())
        c["role"] = role
        json.dump(c, open("xiaojiao_control.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        reload_control()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/access", methods=["GET", "POST"])

def api_access():
    """权限模式：GET=读当前；POST=切换 Full access(全权限)/Read-only(每次执行都询问)。"""
    global FULL_ACCESS
    if request.method == "GET":
        return jsonify({"full_access": FULL_ACCESS})
    d = request.get_json(force=True, silent=True) or {}
    on = d.get("full_access", not FULL_ACCESS)
    FULL_ACCESS = bool(on)
    try:
        cap = dict(CONTROL.get("capabilities", {})); cap["full_access"] = FULL_ACCESS
        control = json.loads(open("xiaojiao_control.json", encoding="utf-8").read())
        control["capabilities"] = cap
        json.dump(control, open("xiaojiao_control.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass
    return jsonify({"ok": True, "full_access": FULL_ACCESS})


@app.route("/api/tools_toggle", methods=["GET", "POST"])
def api_tools_toggle():
    """工具开关：读(GET) / 切换(POST)。开=必须执行工具，关=只聊天。"""
    if request.method == "POST":
        new_state = not bool(CAP.get("run_tools", True))
        cap = dict(CAP)
        cap["run_tools"] = new_state
        try:
            saved = {"model_name": MODEL_NAME, "brain": CONTROL.get("brain", {}),
                     "role": SYSTEM_PROMPT, "capabilities": cap, "behavior": BEH,
                     "models": _get_models()}
            json.dump(saved, open("xiaojiao_control.json", "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        reload_control()
        return jsonify({"ok": True, "tools_on": new_state})
    return jsonify({"ok": True, "tools_on": bool(CAP.get("run_tools", True))})


# ================== 模型管理（对接本地/外接模型，类似 dsh） ==================
def _get_models():
    return CONTROL.get("models", []) or []


def _save_control(brain=None, models=None):
    saved = {"model_name": MODEL_NAME,
             "brain": brain if brain else CONTROL.get("brain", {}),
             "role": SYSTEM_PROMPT, "capabilities": CAP, "behavior": BEH,
             "models": models if models is not None else _get_models(), "dsh": CONTROL.get("dsh", {})}
    json.dump(saved, open("xiaojiao_control.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    reload_control()


@app.route("/api/models", methods=["GET"])
def api_models():
    cur = None
    eng = BRAIN_ENGINE
    base = (CONTROL.get("brain", {}).get("api", {}).get("base_url", "") or "")
    for m in _get_models():
        if m.get("engine") == eng and (m.get("base_url", "") == base or not base):
            cur = m.get("name"); break
    return jsonify({"active": BRAIN_ENGINE, "current": cur, "models": _get_models()})


@app.route("/api/model/select", methods=["POST"])
def api_model_select():
    """切换当前大脑到某个已配置模型。"""
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    for m in _get_models():
        if m.get("name") == name:
            brain = dict(CONTROL.get("brain", {}))
            brain["engine"] = m.get("engine", "auto")
            if m.get("engine") == "api":
                brain["api"] = {"base_url": m.get("base_url", ""), "api_key": m.get("api_key", ""),
                                "model": m.get("model", "")}
            elif m.get("engine") == "llama":
                brain["api"] = {"base_url": m.get("base_url", "http://127.0.0.1:8080/v1"),
                                "api_key": "", "model": m.get("model", "xiaojiao1.0-4B")}
            _save_control(brain=brain)
            return jsonify({"ok": True, "engine": brain["engine"], "name": name})
    return jsonify({"ok": False, "error": "模型不存在"}), 404


@app.route("/api/model/add", methods=["POST"])
def api_model_add():
    """添加一个模型（对接本地模型/外接 API）。同名的覆盖。"""
    data = request.get_json(force=True, silent=True) or {}
    entry = {"name": (data.get("name") or "").strip(), "engine": data.get("engine", "api"),
             "base_url": data.get("base_url", ""), "api_key": data.get("api_key", ""),
             "model": data.get("model", "")}
    if not entry["name"]:
        return jsonify({"ok": False, "error": "模型名字不能为空"}), 400
    models = [m for m in _get_models() if m.get("name") != entry["name"]]
    models.append(entry)
    _save_control(models=models)
    return jsonify({"ok": True, "models": models})


@app.route("/api/model/delete", methods=["POST"])
def api_model_delete():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    models = [m for m in _get_models() if m.get("name") != name]
    _save_control(models=models)
    return jsonify({"ok": True, "models": models})


@app.route("/api/confirm", methods=["POST"])
def api_confirm():
    """执行刚才被挂起的危险动作（用户点“确认执行”后调用）。"""
    global PENDING
    if not PENDING:
        return jsonify({"ok": False, "error": "没有待确认的操作"}), 400
    name, args = PENDING
    PENDING = None
    result = run_tool(name, args, force=True)
    return jsonify({"ok": True, "result": result})


@app.route("/growth")
def page_growth():
    """小脑成长报告页(可分享)。"""
    import webbrowser as _wb
    return _growth_html()


def _growth_html():
    root = os.path.dirname(os.path.abspath(__file__))
    def cnt(rel):
        try:
            return sum(1 for _ in open(os.path.join(root, rel), encoding="utf-8"))
        except Exception:
            return 0
    know = cnt(os.path.join("self_learn", "little_brain_knowledge.txt"))
    logs = cnt(os.path.join("logs", "chat_history.jsonl"))
    good = bad = corr = 0
    try:
        for ln in open(os.path.join(root, "logs", "feedback.jsonl"), encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            f = r.get("feedback", "")
            if f in ("good", "👍") or str(f).strip("星") in ("4", "5"):
                good += 1
            elif f in ("bad", "👎"):
                bad += 1
            if r.get("corrected_reply"):
                corr += 1
    except Exception:
        pass
    bar = min(100, int(know / 5))
    try:
        import sys as _s, os as _o
        _s.path.insert(0, os.path.join(root, "self_learn"))
        import vstore
        vec = vstore.count()
    except Exception:
        vec = 0
    lessons = []
    try:
        ls = [x.strip() for x in open(os.path.join(root, "self_learn", "little_brain_knowledge.txt"), encoding="utf-8") if x.strip()]
        lessons = ls[-8:][::-1]
    except Exception:
        pass
    lhtml = "".join("<div class='bli'>" + (l[:64] + ("…" if len(l) > 64 else "")) + "</div>" for l in lessons) or "<div class='bli think'>还没学到东西，多聊几轮、点几个👍吧</div>"
    return ("""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小焦成长报告</title>
<style>*{box-sizing:border-box}body{margin:0;font-family:'Segoe UI',sans-serif;background:linear-gradient(160deg,#0e1116,#141a2e);color:#e8ebf3;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:32px 16px}
.wrap{max-width:760px;width:100%}
.hero{text-align:center;margin-bottom:22px}
.hero h1{font-size:30px;margin:0 0 8px;font-weight:800}
.hero .sub{color:#8b93a3;font-size:14px}
.big{font-size:72px;font-weight:800;background:linear-gradient(135deg,#5b5ff5,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;line-height:1}
.big-l{color:#8b93a3;font-size:14px;margin-top:6px}
.bar{height:12px;background:#0e1116;border:1px solid #232a3e;border-radius:8px;overflow:hidden;margin-top:12px}.bar i{display:block;height:100%;background:linear-gradient(90deg,#5b5ff5,#7c5cf0)}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:24px}
.card{background:#151a26;border:1px solid #2a3140;border-radius:16px;padding:20px;text-align:center}
.card .n{font-size:36px;font-weight:800;color:#5b5ff5}
.card .t{font-size:13px;color:#8b93a3;margin-top:6px}
.panel{background:#151a26;border:1px solid #2a3140;border-radius:16px;padding:20px;margin-top:24px}
.panel h3{margin:0 0 12px;font-size:16px;color:#cbd0dc}
.bli{font-size:13px;color:#c9d1d9;padding:8px 2px;border-bottom:1px solid #1a2030;line-height:1.6;font-family:Consolas,monospace}
.think{color:#6e7681;font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:26px}
.chip{background:#1a2030;border:1px solid #2a3140;border-radius:20px;padding:7px 14px;font-size:13px;color:#aab2c0}
.foot{text-align:center;color:#6e7681;font-size:12px;line-height:1.8;margin-top:30px}
@media(max-width:600px){.cards{grid-template-columns:1fr 1fr}.big{font-size:56px}}
</style></head><body><div class="wrap">
<div class="hero"><h1>🐳 小焦 · 小脑成长报告</h1><div class="sub">小脑跟着大脑学 · 别人靠算力，小脑靠文本</div></div>
<div class="card"><div class="big">@@KNOW@@</div><div class="big-l">小脑知识库累计（条）—— 越长越强</div>
<div class="bar"><i style="width:@@BAR@@%"></i></div></div>
<div class="cards">
<div class="card"><div class="n">@@VEC@@</div><div class="t">🧠 向量知识</div></div>
<div class="card"><div class="n">@@LOGS@@</div><div class="t">💬 交互记录</div></div>
<div class="card"><div class="n">@@GOOD@@</div><div class="t">👍 点赞</div></div>
<div class="card"><div class="n">@@BAD@@</div><div class="t">👎 踩</div></div>
<div class="card"><div class="n">@@CORR@@</div><div class="t">✏️ 被更正</div></div>
<div class="card"><div class="n">⭐</div><div class="t">持续学习中</div></div>
</div>
<div class="panel"><h3>🧠 最近学到</h3>@@LESSONS@@</div>
<div class="chips"><span class="chip">持续学习</span><span class="chip">DSH 插件生态</span><span class="chip">免密钥联网</span><span class="chip">多人格</span><span class="chip">本地隐私</span></div>
<div class="foot">它不会很多话，但会慢慢成为只属于你的那一只 🐳<br>xiaojiao-harness · 持续学习 · DSH 插件生态 · Made with ❤️</div>
</div></body></html>"""
        .replace("@@KNOW@@", str(know)).replace("@@BAR@@", str(bar))
        .replace("@@VEC@@", str(vec)).replace("@@LOGS@@", str(logs))
        .replace("@@GOOD@@", str(good)).replace("@@BAD@@", str(bad)).replace("@@CORR@@", str(corr))
        .replace("@@LESSONS@@", lhtml))


@app.route("/api/settings", methods=["GET"])

def api_settings_get():
    """返回当前配置 + 可用的插件（含开关状态）。"""
    plist = [{"name": k,
              "builtin": v.get("builtin", False),
              "type": v.get("type", "py"),
              "on": v.get("on", True),
              "manifest": v.get("manifest"),
              "settings": v.get("settings", []),
              "desc": v.get("desc", [])}
             for k, v in PLUGINS.items()]
    return jsonify({
        "control": {
            "model_name": MODEL_NAME,
            "brain": CONTROL.get("brain", {}),
            "role": SYSTEM_PROMPT,
            "capabilities": CAP,
            "behavior": BEH,
        },
        "plugins": plist,
    })


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    """保存设置：写回操控文件 → 热更新运行中的配置。

    以当前 CONTROL 为基础做合并，保留客户端没发的字段（如 brain.llama 大模型配置）。
    """
    data = request.get_json(force=True, silent=True) or {}
    got = data.get("control") or {}
    cur = CONTROL if isinstance(CONTROL, dict) else {}
    # 合并：brain 保留原有 llama/node，更新 engine/api；behavior/capabilities 逐键覆盖
    brain = dict(cur.get("brain", {}))
    new_brain = got.get("brain") or {}
    for k in ("engine", "api"):
        if k in new_brain:
            brain[k] = new_brain[k]
    # llama 深合并：保留 server/gguf/port，只更新 ctx 等
    if "llama" in new_brain:
        brain["llama"] = {**brain.get("llama", {}), **new_brain["llama"]}
    saved = {
        "model_name": got.get("model_name", cur.get("model_name", MODEL_NAME)),
        "brain": brain,
        "role": got.get("role", cur.get("role", SYSTEM_PROMPT)),
        "capabilities": {**cur.get("capabilities", {}), **(got.get("capabilities") or {})},
        "behavior": {**cur.get("behavior", {}), **(got.get("behavior") or {})},
        "models": _get_models(),
    }
    try:
        json.dump(saved, open("xiaojiao_control.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    reload_control()
    global PLUGINS
    PLUGINS = load_plugins()
    return jsonify({"ok": True, "model_name": MODEL_NAME})


# ================== OpenAI 兼容接口（供 Harness / 任意客户端接入） ==================
@app.route("/v1/models")
def v1_models():
    return jsonify({"object": "list", "data": [{"id": MODEL_NAME, "object": "model",
                                                "owned_by": "xiaojiao", "created": 0}]})


def _content_str(c):
    """把消息的 content 安全转成字符串（content 可能是 None / list(多模态) / str）。"""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c)


@app.route("/v1/chat/completions", methods=["POST"])
def v1_chat():
    maybe_reload_control()
    """OpenAI 兼容的对话接口：自动注入小焦人设 + 工具(操控电脑)，支持流式。

    任意 OpenAI 兼容客户端把 base_url 指向小焦即可接入，小焦会给模型上“小焦人格”。
    """
    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages") or []
    user_last = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            user_last = _content_str(m.get("content"))
            if user_last:
                break
    if not user_last:
        user_last = str(data.get("prompt", ""))
    try:
        answer, _online, _info, _nc, _tt = agent_run(str(user_last))
    except Exception as e:
        answer = "⚠️ 小焦处理出错：" + str(e)
    content = answer or "（暂无回答）"

    if data.get("stream"):
        return _sse(content)

    return jsonify({
        "id": "xiaojiao-chat", "object": "chat.completion", "model": MODEL_NAME,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _sse(content):
    """把最终答案包装成 OpenAI 兼容的 SSE 流，让 dsh 等客户端能正常接收。"""
    def gen():
        chunk = {"id": "xiaojiao", "object": "chat.completion.chunk", "model": MODEL_NAME,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": content},
                              "finish_reason": None}]}
        yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        done = {"id": "xiaojiao", "object": "chat.completion.chunk", "model": MODEL_NAME,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield "data: " + json.dumps(done, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
    return Response(gen(), mimetype="text/event-stream")


# ================== Web 界面（商标：小焦） ==================
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>小焦 · XiaoJiao</title><style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:#0e1116;color:#e8ebf3;height:100vh;display:flex;flex-direction:column;overflow:hidden}
  *{scrollbar-color:#2a3140 #11141c}
  *::-webkit-scrollbar{width:6px;height:6px}
  *::-webkit-scrollbar-thumb{background:#2a3140;border-radius:4px}
  *::-webkit-scrollbar-track{background:transparent}
  #app{display:flex;flex:1;min-height:0}
  #sidebar{width:250px;background:#10131d;border-right:1px solid #20263a;display:flex;flex-direction:column;flex-shrink:0;transition:width .2s}
  #sidebar.hidden{width:0;overflow:hidden;border-right:none}
  #sidebar .sh{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid #262b3a}
  #sidebar .sh .newchat{flex:1;background:#1f2533;border:1px solid #2a3140;color:#cbd0dc;border-radius:8px;padding:7px 10px;font-size:13px;cursor:pointer;text-align:left}
  #sidebar .sh .newchat:hover{background:#2a3140}
  #sidebar .cl{flex:0 0 auto;background:#1f2533;border:1px solid #2a3140;color:#8b93a3;border-radius:8px;padding:6px 9px;font-size:12px;cursor:pointer}
  #sessionList{flex:1;overflow-y:auto;padding:8px}
  .sess{display:block;width:100%;text-align:left;background:transparent;border:none;color:#cbd0dc;padding:9px 12px;border-radius:8px;font-size:13px;cursor:pointer;margin-bottom:4px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  .sess:hover{background:#1e2430}
  .sess.active{background:#2a3140;color:#fff}
  #main{flex:1;display:flex;flex-direction:column;min-width:0}
  header{padding:12px 20px;background:#161a24;border-bottom:1px solid #262b3a;display:flex;align-items:center;gap:12px}
  header .logo{font-size:22px;font-weight:800;background:linear-gradient(135deg,#f093fb,#f5576c);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  header .tag{font-size:12px;color:#7a8290;background:#1f2533;padding:4px 10px;border-radius:20px}
  header .sp{flex:1}
  .icon-btn{background:#1f2533;border:1px solid #2a3140;color:#cbd0dc;border-radius:10px;padding:8px 12px;cursor:pointer;font-size:13px}
  .icon-btn:hover{background:#2a3140}
  .iconselect{background:#1f2533;border:1px solid #2a3140;color:#cbd0dc;border-radius:10px;padding:8px 10px;font-size:13px;outline:none;max-width:230px}
  .icon-btn.on{background:#0f2b1c;border-color:#1f7a3d;color:#4ade80}
  .icon-btn.off{background:#2a1320;border-color:#8b1e2d;color:#f87171}
  .tooltrace{font-size:12px;color:#8b93a3;background:#12161f;border:1px solid #2a3140;border-radius:10px;padding:8px 12px;margin:4px 0 10px;white-space:pre-wrap}
  .tooltrace b{color:#4ade80}
  #feed{flex:1;overflow-y:auto;padding:24px;width:100%;display:flex;flex-direction:column;align-items:center}
  #feed>*{width:100%;max-width:860px}
  .m{display:flex;margin-bottom:14px;gap:10px}
  .m.user{justify-content:flex-end}.m.bot{justify-content:flex-start}
  .b{max-width:82%;padding:11px 16px;border-radius:16px;line-height:1.65;font-size:15px;white-space:pre-wrap;word-break:break-word;box-shadow:none}
  .user .b{background:linear-gradient(135deg,#5b5ff5,#7c5cf0);color:#fff;border-bottom-right-radius:5px}
  .bot .b{background:#151a24;border:1px solid #252b38;border-bottom-left-radius:5px}
  .src{font-size:11px;color:#8b93a3;margin-top:6px;padding-left:2px}
  .src b{color:#a78bfa}
  .srcbtn{background:#1a2030;border:1px solid #2a3140;color:#a78bfa;border-radius:14px;padding:4px 12px;font-size:12px;cursor:pointer;margin-top:6px;white-space:nowrap;width:auto;align-self:flex-start;display:inline-flex;align-items:center;gap:4px}
  .srcbtn:hover{background:#232c42;border-color:#405a99}
  .srcbtn:hover{background:#263349}
  .srcbox{display:none;white-space:normal;font-size:11px;color:#8b93a3;margin-top:6px;background:#11141c;border:1px solid #252b38;border-radius:8px;padding:8px 10px;max-height:180px;overflow-y:auto}
  .srci{margin-bottom:8px}
  .srci .st{color:#9bb0e1;font-size:12px;margin-bottom:2px}
  .srci .sc{color:#aab2c0;font-size:11px;line-height:1.5}
  .srcbox.show{display:block}
  .srcbox b{color:#a78bfa;margin-right:4px}
  .b pre.code{background:#0d1117;border:none;border-radius:0;padding:14px;overflow-x:auto;margin:0}
  .b pre.code::-webkit-scrollbar{height:5px;width:5px}
  .b pre.code::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
  .b pre.code::-webkit-scrollbar-track{background:transparent}
  .b pre.code code{font-family:Consolas,'Courier New',monospace;font-size:13px;white-space:pre;color:#c9d1d9}
  .b code{background:#2a3140;padding:1px 6px;border-radius:4px;font-family:Consolas,monospace;font-size:13px}
  .codebox{border:none;border-radius:10px;margin:10px 0;overflow:hidden;background:#0d1117}
  .codehead{display:flex;align-items:center;gap:8px;background:#0d1117;padding:10px 12px 0}
  .lang{padding:2px 8px;font-size:11px;font-weight:600;color:#6e7681;text-transform:uppercase;letter-spacing:.5px;background:transparent}
  .cp{margin-left:auto;background:transparent;border:1px solid #21262d;color:#7d8590;border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer}
  .cp:hover{background:#161b22;color:#e6edf3}
  .lang.python,.lang.py{color:#6e7681}.lang.js,.lang.javascript{color:#6e7681}
  .lang.bash,.lang.sh{color:#6e7681}.lang.html,.lang.css{color:#6e7681}.lang.json{color:#6e7681}
  .lang.cpp,.lang.c{color:#6e7681}.lang.java{color:#6e7681}.lang.sql{color:#6e7681}
  .cp{background:#1f2533;border:1px solid #2a3140;color:#cbd0dc;border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer}
  .cp:hover{background:#2a3140}
  .kw{color:#ff7b72}
  .b table{border-collapse:collapse;margin:8px 0;width:100%;font-size:13px}
  .b table th,.b table td{border:1px solid #2a3140;padding:6px 10px;text-align:left}
  .b table th{background:#1f2533;color:#cbd0dc}
  .msgbot{display:flex;gap:8px;margin-top:6px;align-items:center;padding-left:2px}
  .msgbot button{background:#1f2533;border:1px solid #2a3140;color:#8b93a3;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer}
  .msgbot button:hover{background:#2a3140}
  .msgbot .fb{font-size:14px;padding:2px 8px}
  .b strong{color:#fff}
  .b ul,.b ol{padding-left:20px;margin:6px 0}
  .b h1,.b h2,.b h3{color:#fff;margin:10px 0 6px}
  footer{padding:12px 20px;background:#11141c;border-top:1px solid #20263a}
  .bar{max-width:880px;margin:0 auto;display:flex;gap:10px}
  input,textarea,select{background:#0f1117;border:1px solid #2a3140;color:#e6e8ee;border-radius:10px;padding:11px 14px;font-size:14px;outline:none;width:100%;font-family:inherit}
  input:focus,textarea:focus,select:focus{border-color:#4f46e5}
  button{background:#4f46e5;color:#fff;border:none;border-radius:10px;padding:11px 22px;font-size:14px;cursor:pointer}
  button:hover{background:#6366f1}
  .think{color:#7a8290;font-size:13px;padding:6px 2px}
  .think{display:flex;align-items:center;gap:8px}
  .pvbar{height:4px;background:#0e1116;border-radius:3px;margin-top:6px;overflow:hidden;max-width:420px}
  .pvbar i? no
  .pvbar{height:4px;background:#0e1116;border-radius:3px;margin-top:6px;overflow:hidden;max-width:420px;display:block}
  .pvbar{transition:width .4s}
  .spin{width:13px;height:13px;border:2px solid #405a99;border-top-color:transparent;border-radius:50%;animation:spin .7s linear infinite;flex:0 0 auto}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* 设置面板 */
  #settings{position:fixed;inset:0;background:rgba(10,12,18,.94);z-index:10;overflow-y:auto;display:none}
  #settings.show{display:block}
  .panel{max-width:760px;margin:40px auto;background:#141822;border:1px solid #262b3a;border-radius:16px;padding:26px}
  .panel h2{font-size:20px;margin-bottom:18px}
  .field{margin-bottom:18px}
  .field label{display:block;font-size:13px;color:#8b93a3;margin-bottom:6px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .switch{display:flex;align-items:center;justify-content:space-between;background:#1e2430;border:1px solid #2a3140;border-radius:10px;padding:10px 14px;margin-bottom:10px}
  .switch .n{font-size:14px}
  .switch .d{font-size:11px;color:#8b93a3}
  .plug{margin:6px 0}
  .actions{display:flex;gap:12px;justify-content:flex-end;margin-top:16px}
  .btn-sec{background:#2a3140}
  /* 皮肤：仅默认暗色（鲸鱼娘皮肤已移除） */
  /* DSH 风格：顶部栏 + 侧栏底部工具 */
  header{background:#11141c;border-bottom:1px solid #20263a;display:flex;align-items:center;gap:10px;padding:10px 16px}
  header .brand{display:flex;align-items:center;gap:10px}
  header .logo{font-size:20px}
  header .tag{font-size:12px;color:#7a8290}
  .hdr-right{margin-left:auto;display:flex;align-items:center;gap:8px}
  #sidebar .sb-foot{margin-top:auto;padding:10px;border-top:1px solid #262b3a;display:flex;flex-direction:column;gap:6px}
  #sidebar .sb-foot .sbrow{display:flex;align-items:center;gap:6px;padding:8px 10px;border-radius:8px;color:#cbd0dc;font-size:13px;background:#1a2030;cursor:pointer}
  #sidebar .sb-foot .sbrow:hover{background:#232b3d}
  .perm{font-size:11px;color:#7a8290;padding:2px 6px;border:1px solid #2a3140;border-radius:6px}
  /* DSH 风格：侧栏/顶栏/底栏 */
  .sbtop{padding:10px}
  .newchat{width:100%;background:#1e2430;border:1px solid #2a3140;color:#cbd0dc;border-radius:8px;padding:8px;font-size:13px;cursor:pointer}
  .newchat:hover{background:#2a3140}
  .sbws{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;font-size:12px;color:#7a8290;border-bottom:1px solid #262b3a}
  .wsicons{letter-spacing:2px;cursor:pointer}
  .sbdocs{flex:1;overflow-y:auto;display:flex;flex-direction:column;min-height:0}
  .sbtabs{display:flex;gap:4px;padding:8px 10px;border-bottom:1px solid #262b3a}
  .sbtabs span{padding:4px 10px;border-radius:6px;font-size:12px;color:#8b93a3;cursor:pointer}
  .sbtabs span.on{background:#1e2430;color:#cbd0dc}
  .ws-ind{font-size:12px;color:#8b93a3;white-space:nowrap;display:flex;align-items:center;gap:4px}
  .badge2{font-size:11px;color:#7a8290;background:#1a2030;border:1px solid #2a3140;border-radius:10px;padding:2px 8px}
  .bar .iconselect{max-width:210px;flex:0 0 auto}
  /* 设置 左侧导航 */
  .setwrap{display:grid;grid-template-columns:190px 1fr;gap:24px;max-width:980px;margin:30px auto}
  .setnav{background:#11141c;border:1px solid #262b3a;border-radius:14px;padding:10px;height:fit-content}
  .setnav-item{display:flex;align-items:center;gap:8px;padding:10px 12px;border-radius:10px;font-size:14px;color:#cbd0dc;cursor:pointer}
  .setnav-item:hover{background:#1e2430}
  .setnav-item.active{background:#2a3140;color:#fff}
  .setbody{background:#141822;border:1px solid #262b3a;border-radius:14px;padding:22px}
  .sec{display:none}
  .sec.show{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:999}
  .modal{background:#151a26;border:1px solid #2a3140;border-radius:14px;padding:20px;width:min(420px,90vw);box-shadow:0 20px 60px #000a;color:#e8ebf3}
  .modal h3{font-size:15px;margin:0 0 12px;font-weight:600}
  .modal input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;font-size:14px}
  .modal .m-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:14px}
  .modal button{padding:7px 14px;border-radius:8px;border:1px solid #2a3140;background:#222a3e;color:#e8ebf3;cursor:pointer}
  .modal button.primary{background:linear-gradient(135deg,#5b5ff5,#7c5cf0);border:none;color:#fff}
  .brainbg{position:fixed;inset:0;background:rgba(5,7,12,.82);display:flex;align-items:center;justify-content:center;z-index:998;padding:24px}
  .brain{width:min(860px,96vw);max-height:92vh;overflow-y:auto;background:#11141e;border:1px solid #2a3140;border-radius:18px;padding:24px;box-shadow:0 30px 90px #000a;color:#e8ebf3}
  .brain-head{display:flex;align-items:center;gap:12px;margin-bottom:18px}
  .brain-head .logo{font-size:22px;font-weight:800}
  .brain-sub{color:#8b93a3;font-size:13px}
  .brain-head .x{margin-left:auto}
  .brain-stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px}
  .brain-stats .bs{background:#0e1116;border:1px solid #232a3e;border-radius:12px;padding:12px;text-align:center}
  .brain-stats .bs .n{font-size:24px;font-weight:700;color:#5b5ff5}
  .brain-stats .bs .t{font-size:11px;color:#8b93a3;margin-top:4px}
  .brain-cols{display:grid;grid-template-columns:1.4fr 1fr;gap:16px}
  .brain-col h4{margin:0 0 8px;font-size:14px;color:#cbd0dc}
  .brain-list{background:#0e1116;border:1px solid #232a3e;border-radius:12px;padding:10px;max-height:340px;overflow-y:auto}
  .brain-list .bli{font-size:12px;color:#aab2c0;padding:6px 4px;border-bottom:1px solid #1a2030;line-height:1.5}
  .brain-note{font-size:13px;color:#8b93a3;line-height:1.8}
  .wswrap{display:block;cursor:pointer}
  #wsPanel{margin:4px 8px;background:#0e1116;border:1px solid #232a3e;border-radius:10px;padding:8px;max-height:260px;overflow-y:auto}
  .wsitem{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;font-size:13px;color:#cbd0dc;cursor:pointer}
  .wsitem:hover{background:#1e2430}
  .wsitem .ic{flex:0 0 18px;font-size:15px}
  .wsitem .sz{margin-left:auto;font-size:11px;color:#6e7681}
</style></head><body>
<div id="app">
  <div id="sidebar">
    <div class="sbtop"><button class="newchat" onclick="newChat()">➕ 新会话</button></div>
    <div class="sbws"><span class="wswrap" onclick="toggleWorkspace()">🗂️ 工作区</span><span class="wsicons"><span class="wsc" title="搜索会话" onclick="openSearch()">🔍</span>&nbsp;<span class="wsc" title="打开设置" onclick="openSettings()">⚙️</span>&nbsp;<span class="wsc" title="刷新会话" onclick="loadSessions()">↻</span></span></div>
    <div class="sbdocs">
      <div class="sbtabs"><span class="on" onclick="setTab(this,1)">💬 对话</span><span onclick="setTab(this,2)">🧭 轨迹</span></div>
      <div id="sessionList"></div>
      <div id="traceList" style="display:none;text-align:center"></div>
    </div>
    <div class="sb-foot">
      <div class="sbrow" id="toolsRow" onclick="toggleTools()">🛠️ 工具调用 <span class="perm" id="toolsPerm">开</span></div>
      <div class="sbrow" onclick="openSettings()">⚙️ 设置</div>
    </div>
  </div>
  <div id="main">
<header>
  <button class="icon-btn sd-toggle" id="sdToggle" onclick="toggleSidebar()" title="收起/展开侧栏">⟨</button>
  <div class="brand"><span class="logo">🐳 小焦</span><span class="tag">harness · 标准模式</span><span class="badge2" id="taskBadge" style="display:none">⏳ 空闲</span></div>
  <div class="hdr-right">
    <button class="icon-btn" id="toolsBtn" onclick="toggleTools()">🛠️ 工具</button>
    <button class="icon-btn" onclick="openBrain()">🧠 小脑</button>
    <button class="icon-btn" onclick="openSettings()">⚙️ 设置</button>
    <button class="icon-btn" onclick="copyPage()">📄 Session log ⚡</button>
  </div>
</header>
<div id="feed"><div class="think">👋 你好，我是小焦。有问题直接问我，我会联网搜索并结合记忆回答。</div></div>
<footer><div class="bar">
  <span class="ws-ind" id="wsInd" onclick="toggleAccess()" title="点击：Full access(所有命令直接执行,危险也不询问)/Read-only(每次执行都询问)">🔐 Full access</span>
  <button class="icon-btn" onclick="makeVideo()" title="本地零算力生成视频">🎬</button>
  <input id="inp" placeholder="向小焦提问…（Enter 发送）" autocomplete="off" onkeydown="if(event.key==='Enter')send()"/>
  <select id="modelSel" class="iconselect" onchange="selectModel()"></select>
  <button onclick="send()" title="发送">➤</button>
</div></footer>
  </div>
</div>

<div id="modalBg" class="modal-bg" style="display:none">
  <div class="modal">
    <h3>🔍 搜索会话</h3>
    <input id="msq" placeholder="输入关键词，过滤会话…" onkeydown="if(event.key==='Enter')doSearch()"/>
    <div class="m-actions"><button onclick="closeSearch()">取消</button><button class="primary" onclick="doSearch()">确定</button></div>
  </div>
</div>
<div id="brainBg" class="brainbg" style="display:none">
  <div class="brain">
    <div class="brain-head"><span class="logo">🐳 小脑</span><span class="brain-sub">小焦真正自研的那颗会学习的脑</span><button class="icon-btn x" onclick="closeBrain()">✕</button></div>
    <div class="brain-stats" id="brainStats">读取中…</div>
    <div class="brain-cols">
      <div class="brain-col"><h4>🧠 学到的功能用法</h4><div id="brainLessons" class="brain-list">…</div></div>
      <div class="brain-col"><h4>🧭 说明</h4><div class="brain-note">
        「别人靠算力，小脑靠文本。」每次你点👍/被更正，大脑的好答案就写进小脑知识库，越长越强；小脑检索命中即可复用。<br><br>
        <button class="btn-sec" onclick="window.open('/growth')">📄 生成成长报告（可分享）</button>
      </div></div>
    </div>
  </div>
</div>
<div id="settings">
  <div class="setwrap">
    <div class="setnav">
      <div class="setnav-item active" data-sec="general" onclick="setSec(this,'general')">⚙️ 通用设置</div>
      <div class="setnav-item" data-sec="model" onclick="setSec(this,'model')">🎛️ 模型</div>
      <div class="setnav-item" data-sec="plugins" onclick="setSec(this,'plugins')">🧩 插件</div>
    </div>
    <div class="setbody">
      <div class="sec show" id="sec-general">
        <div class="field"><label>模型名称</label><input id="s_name"/></div>
        <div class="field"><label>大脑（engine：auto=自动 / llama=本地大模型 / api=外接API / xiaojiao=自建模型）</label>
          <select id="s_engine"><option value="auto">auto（自动）</option><option value="llama">llama（本地大模型）</option><option value="api">api（外接 OpenAI 兼容）</option><option value="xiaojiao">xiaojiao（自建模型）</option></select>
        </div>
        <div class="field"><label>模型上下文窗口 ctx（大模型一次能处理的 token 上限，在 Harness 里用长对话必需；越大越占显存，启动报 OOM 就调小）</label><input id="s_llm_ctx" type="number" min="2048" step="1024"/></div>
        <div class="row">
          <div class="field"><label>temperature</label><input id="s_temp" type="number" step="0.1" min="0" max="2"/></div>
          <div class="field"><label>max_tokens</label><input id="s_tokens" type="number" min="16"/></div>
        </div>
        <div class="row">
          <div class="field"><label>context_len（上下文轮数）</label><input id="s_ctx" type="number" min="1"/></div>
          <div class="field"><label>API Base URL（engine=api 时用）</label><input id="s_base"/></div>
        </div>
        <div class="field"><label>人设 / 类型（role）—— 改这里·小焦成为什么类型的模型</label>
          <textarea id="s_role" rows="6"></textarea></div>
        <div class="field"><label>能力开关</label>
          <div class="switch"><div><div class="n">操控电脑（工具调用）</div><div class="d">让大模型运行命令、读写文件、打开应用</div></div><label><input type="checkbox" id="s_tools"/></label></div>
        </div>
        <div class="actions">
          <button class="btn-sec" onclick="closeSettings()">取消</button>
          <button onclick="saveSettings()">💾 保存并生效</button>
        </div>
      </div>
      <div class="sec" id="sec-model">
        <div class="field"><label>模型管理（对接本地/外接模型）</label>
          <div id="s_model_list"></div>
          <div class="row" style="margin-top:10px">
            <div class="field"><label>名字</label><input id="s_m_name" placeholder="如 Qwen3.8"/></div>
            <div class="field"><label>类型</label><select id="s_m_engine"><option value="api">api（OpenAI兼容外接）</option><option value="llama">llama（本地大模型）</option><option value="xiaojiao">xiaojiao（自建模型）</option></select></div>
          </div>
          <div class="row">
            <div class="field"><label>Base URL</label><input id="s_m_base" placeholder="如 http://127.0.0.1:8080/v1"/></div>
            <div class="field"><label>API Key（可留空）</label><input id="s_m_key"/></div>
          </div>
          <div class="row">
            <div class="field"><label>模型名</label><input id="s_m_model" placeholder="如 deepseek-chat"/></div>
            <div class="field" style="display:flex;align-items:flex-end"><button class="btn-sec" onclick="addModel()">＋ 添加模型</button></div>
          </div>
          <div class="think" id="s_model_msg"></div>
        </div>
      </div>
      <div class="sec" id="sec-plugins">
        <div class="field"><label>插件（可开关）</label><div id="s_plugins"></div></div>
      </div>
      <div id="plugSecs"></div>
    </div>
  </div>
</div>

<script>
const feed=document.getElementById('feed'),inp=document.getElementById('inp');
const S=document.getElementById('settings');
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function inline(t){t=t.replace(/\*\*([^\n*]+)\*\*/g,'<strong>$1</strong>')
  .replace(/(^|\n)#{1,6}\s+([^\n]+)/g,'<h3>$2</h3>')
  .replace(/`([^`\n]+)`/g,'<code>$1</code>')
  .replace(/^[-*]\s+/gm,'· ')
  .replace(/\n/g,'<br>');return t;}
// 简单关键词高亮（在已转义文本上）
const KW={python:['def','import','from','print','class','return','if','else','for','while','in','not','and','or','try','except','self','None','True','False','lambda','with','as','elif'],
 js:['function','const','let','var','return','if','else','for','while','class','import','export','new','async','await'],
 javascript:['function','const','let','var','return','if','else','for','while','class','import','export','new','async','await'],
 bash:['echo','if','then','fi','for','do','done','git','cd','ls','rm','sudo'],sh:['echo','if','then','fi','for','do','done','git','cd','ls','rm','sudo']};
function hl(s,lang){const kw=KW[lang]||[];let r=s;kw.forEach(k=>{r=r.replace(new RegExp('\\b'+k+'\\b','g'),'<span class="kw">'+k+'</span>');});return r;}
function codeBlock(code,lang){
  const ln=(lang||'code');const safe=esc(code.replace(/\n$/,''));
  return '<div class="codebox"><div class="codehead"><span class="lang '+esc(ln)+'">'+esc(ln)+'</span><button class="cp" onclick="copyCode(this)">⧉ 复制</button></div><pre class="code"><code>'+safe+'</code></pre></div>';
}
function copyCode(btn){const pre=btn.closest('.codebox').querySelector('code');const t=pre.innerText;
  navigator.clipboard.writeText(t).then(()=>{btn.textContent='✓ 已复制';setTimeout(()=>btn.textContent='⧉ 复制',1200);}).catch(()=>{});}
function copyMsg(btn){const b=btn.closest('.m').querySelector('.b');
  navigator.clipboard.writeText(b.innerText).then(()=>{btn.textContent='✓ 已复制';setTimeout(()=>btn.textContent='复制',1200);}).catch(()=>{});}

function toggleSidebar(){const sb=document.getElementById('sidebar');const col=sb.classList.toggle('collapsed');
  const t=document.getElementById('sdToggle');if(t)t.textContent=col?'⟩':'⟨';}
function setTab(el,n){document.querySelectorAll('.sbtabs span').forEach(x=>x.classList.remove('on'));el.classList.add('on');
  document.getElementById('sessionList').style.display=n===1?'block':'none';
  document.getElementById('traceList').style.display=n===2?'block':'none';
  if(n===2)loadTrace();}
function loadTrace(){const el=document.getElementById('traceList');
  try{const r=JSON.parse(localStorage.getItem('xj_trace')||'[]');
    el.innerHTML=r.length?r.map(x=>'<div class="srci"><div class="st">'+esc(x.tool||'')+'</div><div class="sc">'+esc(String(x.result||'').slice(0,80))+'</div></div>').join(''):'<div class="think">暂无工具轨迹</div>';}catch(e){}}
let fullAccess=true;


async function toggleWorkspace(){const el=document.getElementById("wsPanel");if(el.style.display==="none"){el.style.display="block";await loadWorkspace();}else{el.style.display="none";}}
async function loadWorkspace(){try{const d=await (await fetch("/api/workspace")).json();const el=document.getElementById("wsPanel");
  el.innerHTML=d.length?d.map(function(x){return "<div class=\"wsitem\" data-n=\""+esc(x.name)+"\" onclick=\"openWsFile(this.dataset.n)\"><span class=\"ic\">"+(x.type==="dir"?"📁":"📄")+"</span><span>"+esc(x.name)+"</span><span class=\"sz\">"+esc(x.size)+"</span></div>";}).join(""):"<div class=\"think\">空</div>";}catch(e){document.getElementById("wsPanel").innerHTML="<div class=\"think\">读取失败</div>";}}
async function openWsFile(name){try{const d=await (await fetch("/api/ws/open",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name})})).json();
  if(!d.ok){alert(d.error||"无法打开");return;}
  const cur=document.getElementById("modalBg");cur.style.display="flex";
  cur.innerHTML="<div class=\"modal modal-wide\"><h3>📄 "+esc(d.name)+"</h3><pre class=\"wspre\">"+esc(d.content)+"</pre><div class=\"m-actions\"><button onclick=\"closeSearch()\">关闭</button></div></div>";
}catch(e){alert("读取失败");}}

async function makeVideo(){const q=prompt('输入视频场景（真·AI 文生视频，ComfyUI + Wan2.1）：');if(!q)return;
  const m=document.createElement('div');m.className='m bot';m.innerHTML='<div class="b">🎬 正在准备…</div>';feed.appendChild(m);feed.scrollTop=feed.scrollHeight;
  const b=m.querySelector('.b');
  try{const d=await (await fetch('/api/video',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:q})})).json();
   if(d.busy){b.innerHTML='⏳ 正在生成/切换模型中，请稍候…';return;}
   if(!d.ok){b.innerHTML='⚠️ '+esc(d.error||'启动失败');return;}
   try{localStorage.setItem('xj_video_job',d.job);}catch(e){}
   let n=0;
   const iv=setInterval(async()=>{n++;
     try{const st=await (await fetch('/api/video/status?job='+d.job)).json();
      if(st.state==='done'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){};b.innerHTML='<video src="'+st.url+'" controls style="max-width:100%;border-radius:12px"></video><div style="font-size:12px;color:#8b93a3;margin-top:6px">🎬 真·AI 视频 · '+esc(q)+'</div>';feed.scrollTop=feed.scrollHeight;}
      else if(st.state==='error'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){};b.innerHTML='⚠️ '+esc(st.message||'生成失败');}
      else if(st.state==='unknown'){clearInterval(iv);b.innerHTML='⚠️ 任务状态丢失（可能已结束或服务器重启）。<br>请点 🎬 重新生成，或到 ComfyUI(<b>127.0.0.1:8188</b>)看真实结果。';}
      else if(n*5>2700){clearInterval(iv);b.innerHTML='⏱️ 已等 '+Math.round(n*5/60)+' 分钟（超时）。到 ComfyUI(8188) 看是否仍在跑/已出片，或重新生成。';}
      else{var pr=(st.progress&&st.progress.max)?Math.round(100*st.progress.value/st.progress.max):0;var msg='🎬 '+((st.message||"生成中…")+(pr?'（第 '+st.progress.value+'/'+st.progress.max+' 步，'+pr+'%）':''))+'（已等 '+Math.round(n*5)+'s）';b.textContent=msg;if(pr>0){var bar=b.nextElementSibling;if(!bar||!bar.classList.contains("pvbar")){bar=document.createElement("div");bar.className="pvbar";b.after(bar);}bar.style.width=pr+"%";}}
     }catch(e){}
   },5000);
  }catch(e){b.innerHTML='⚠️ 出错了：'+esc(e.message);}}


async function resumeChat(){try{const p=await (await fetch('/api/chat/pending')).json();
  if(!p.pending){return;}
  const m=document.createElement('div');m.className='m bot';m.innerHTML='<div class="b"><span class="spin"></span> 正在回答（可先干别的，恢复中）…</div>';feed.appendChild(m);feed.scrollTop=feed.scrollHeight;
  const iv=setInterval(async()=>{try{const u=await (await fetch('/api/chat/pending')).json();
    if(!u.pending){clearInterval(iv);const b=m.querySelector('.b');b.innerHTML=u.content?renderMd(u.content):'（回答完成）';feed.scrollTop=feed.scrollHeight;}}catch(e){}},2500);
  }catch(e){}}


function syncLoop(){try{loadSessions();}catch(e){}
  // 服务器端当前视频任务 -> 顶部小提示(跨标签/刷新都在)
  try{fetch('/api/video/current').then(r=>r.json()).then(c=>{
    const v=(c.job||{});
    let pill=document.getElementById('syncPill');
    if(!pill){pill=document.createElement('span');pill.id='syncPill';pill.style.cssText='font-size:11px;padding:2px 8px;border-radius:10px;background:#5b5ff533;color:#a78bfa;margin-left:6px';const h=document.querySelector('.tag');if(h)h.after(pill);}
    const tb=document.getElementById('taskBadge');
    const act=(c.job&&['queued','switching','generating'].indexOf(c.job.state)>=0);
    if(tb){tb.style.display='';tb.textContent=act?'🎬 1 个后台任务：视频生成':'⏳ 空闲';tb.style.color=act?'#f0a848':'#7a8290';}
    if(act){pill.textContent='🎬 生成中';pill.style.display='';}
    else if(c.job&&c.job.state==='done'&&c.job.url){pill.textContent='🎬 完成';setTimeout(()=>{pill.style.display='none'},8000);}
    else{pill.style.display='none';}
  }).catch(()=>{});}catch(e){}}
setInterval(syncLoop,4000);

async function resumeVideoJob(){let job='';
  // 优先服务器端当前任务(跨浏览器/刷新/重启)
  try{const c=await (await fetch('/api/video/current')).json();
    if(c.job&&c.job.id){job=c.job.id;try{localStorage.setItem('xj_video_job',job);}catch(e){}}
  }catch(e){}
  if(!job){try{job=localStorage.getItem('xj_video_job')||'';}catch(e){}}
  if(!job)return;
  const m=document.createElement('div');m.className='m bot';m.innerHTML='<div class="b">🎬 恢复上次生成进度…</div>';feed.appendChild(m);
  const b=m.querySelector('.b');let n=0;
  const iv=setInterval(async()=>{n++;
    try{const st=await (await fetch('/api/video/status?job='+job)).json();
      if(st.state==='done'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){}
        b.innerHTML='<video src="'+st.url+'" controls style="max-width:100%;border-radius:12px"></video><div style="font-size:12px;color:#8b93a3;margin-top:6px">🎬 真·AI 视频（刷新前生成）</div>';feed.scrollTop=feed.scrollHeight;}
      else if(st.state==='error'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){};b.innerHTML='⚠️ '+esc(st.message||'生成失败');}
      else if(st.state==='unknown'){clearInterval(iv);b.innerHTML='⚠️ 任务状态丢失（可能已结束或服务器重启）。请重新生成，或到 8188 查看。';}
      else if(n*5>2700){clearInterval(iv);b.textContent='⏱️ 超时，到 8188 看是否完成。';}
      else{var pr=(st.progress&&st.progress.max)?Math.round(100*st.progress.value/st.progress.max):0;var msg='🎬 '+((st.message||"生成中…")+(pr?'（第 '+st.progress.value+'/'+st.progress.max+' 步，'+pr+'%）':''))+'（已等 '+Math.round(n*5)+'s）';b.textContent=msg;if(pr>0){var bar=b.nextElementSibling;if(!bar||!bar.classList.contains("pvbar")){bar=document.createElement("div");bar.className="pvbar";b.after(bar);}bar.style.width=pr+"%";}}
    }catch(e){}
  },5000);
}
function openBrain(){document.getElementById('brainBg').style.display='flex';loadBrain();}
function closeBrain(){document.getElementById('brainBg').style.display='none';}
async function loadBrain(){try{const d=await (await fetch('/api/brain')).json();
  document.getElementById('brainStats').innerHTML=
   '<div class="bs"><div class="n">'+d.know+'</div><div class="t">知识库(条)</div></div>'+
   '<div class="bs"><div class="n">'+d.vec+'</div><div class="t">向量知识</div></div>'+
   '<div class="bs"><div class="n">'+d.logs+'</div><div class="t">交互(次)</div></div>'+
   '<div class="bs"><div class="n">'+d.good+'</div><div class="t">👍 点赞</div></div>'+
   '<div class="bs"><div class="n">'+d.bad+'</div><div class="t">👎 踩</div></div>'+
   '<div class="bs"><div class="n">'+d.corr+'</div><div class="t">✏️ 更正</div></div>';
  const ls=document.getElementById('brainLessons');
  const arr=d.lessons||[];
  ls.innerHTML=arr.length?arr.slice(-12).reverse().map(x=>'<div class="bli">'+esc(x)+'</div>').join(''):'<div class="think">还没学到东西，多聊几轮、点几个👍吧</div>';
 }catch(e){document.getElementById('brainStats').textContent='读取失败';}}

function openSearch(){document.getElementById('modalBg').style.display='flex';const i=document.getElementById('msq');i.value='';i.focus();}
function closeSearch(){document.getElementById('modalBg').style.display='none';}
function doSearch(){const q=document.getElementById('msq').value.trim();if(!q){closeSearch();return;}
  const boxes=[...document.querySelectorAll('#sessionList .sess')];boxes.forEach(b=>{b.style.display=b.textContent.toLowerCase().includes(q.toLowerCase())?'':'none';});closeSearch();}
function toggleAccess(){const nv=!fullAccess;fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({full_access:nv})}).then(r=>r.json()).then(d=>{fullAccess=d.full_access;refreshAccess();});}
function refreshAccess(){const w=document.getElementById('wsInd');if(w){w.textContent=fullAccess?'🔐 Full access':'🔒 Read-only';w.style.color=fullAccess?'#4ade80':'#f87171';}}
async function loadAccess(){try{const d=await (await fetch('/api/access')).json();fullAccess=!!d.full_access;refreshAccess();}catch(e){}}

function copyPage(){const t=(document.getElementById('feed')?.innerText||'').trim()||'（暂无对话）';
  navigator.clipboard.writeText(t).then(()=>{alert('已复制当前会话日志');}).catch(()=>{});}
function renderTableBlock(text){
  // 把一组以 | 开头的行转成 <table>
  const rows=text.split('\n').filter(l=>l.trim().startsWith('|'));
  if(rows.length<2)return null;
  const clean=l=>l.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(c=>c.trim());
  let html='<table>';
  rows.forEach((r,i)=>{const cells=clean(r);if(cells.every(c=>!c.replace(/[-:]/g,'')))return;const tag=i===0?'th':'td';
    html+='<tr>'+cells.map(c=>'<'+tag+'>'+inline(esc(c))+'</'+tag+'>').join('')+'</tr>';});
  return html+'</table>';
}
function renderMd(text){
  text=text||'';
  const fence=/```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g;
  let out='',last=0,m;
  while((m=fence.exec(text))){
    const seg=text.slice(last,m.index);
    out+=renderBlocks(seg);
    out+=codeBlock(m[2],m[1]);
    last=fence.lastIndex;
  }
  out+=renderBlocks(text.slice(last));
  return out;
}
function renderBlocks(seg){
  // 按空行分块；块内若是表格则转 table，否则行内 md
  if(!seg)return '';
  let blocks=seg.split(/\n\s*\n/),html='';
  blocks.forEach(b=>{const t=renderTableBlock(b);html+=t?t:inline(esc(b));});
  return html;
}
function add(role,text,src){const m=document.createElement('div');m.className='m '+role;
 m.innerHTML='<div class="b">'+(role==='bot'?renderMd(text):esc(text))+'</div>';
 if(role==='bot'&&((''+text).indexOf('__pending__')>=0||text==='⏳')){m.innerHTML='<div class="b"><span class="spin"></span> 正在回答…</div>';feed.appendChild(m);return;}
   if(role==='bot'){const row=document.createElement('div');row.className='msgbot';
   row.innerHTML='<button onclick="copyMsg(this)">⧉ 复制</button>';m.appendChild(row);}
 feed.appendChild(m);
 if(src&&src.length){const t=document.createElement('button');t.className='srcbtn';t.textContent='🔎 查看来源 ('+src.length+')';
   t.onclick=()=>{if(!t._s){t._s=document.createElement('div');t._s.className='srcbox';t._s.innerHTML=src.map(x=>'<div class="srci"><div class="st">'+esc(x.title)+'</div><div class="sc">'+esc(x.content.slice(0,160))+'</div></div>').join('');t.after(t._s);}
     const show=t._s.classList.toggle('show');t.textContent=show?'🔎 收起来源 ('+src.length+')':'🔎 查看来源 ('+src.length+')';};
   feed.appendChild(t);}
 feed.scrollTop=feed.scrollHeight;}
async function send(){const t=inp.value.trim();if(!t)return;inp.value='';
 add('user',t);
 // 会动的"思考中"提示
 const th=document.createElement('div');th.className='think';th.innerHTML='<span class="spin"></span><span class="stag">正在理解你的问题…</span>';feed.appendChild(th);feed.scrollTop=feed.scrollHeight;
 const stages=['正在理解你的问题…','🌐 正在联网搜索…','💾 正在回忆记忆…','🧠 大脑正在思考…','✍️ 正在组织回答…'];let si=0;
 const timer=setInterval(()=>{si=(si+1)%stages.length;const s=th.querySelector('.stag');if(s)s.textContent=stages[si];},2200);
 try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t})});
  const d=await r.json();clearInterval(timer);th.remove();
  if(d.tool_trace&&d.tool_trace.length){try{localStorage.setItem('xj_trace',JSON.stringify(d.tool_trace.slice(0,10)));}catch(e){}
   const tt=document.createElement('div');tt.className='tooltrace';
    tt.innerHTML=d.tool_trace.map(x=>'🔧 调用 <b>'+esc(x.tool)+'</b> → '+esc((x.result||'').slice(0,200))).join('<br>');feed.appendChild(tt);}
  setToolsOn(d.tools_on);
  typeAnswer(d.answer,d.sources||[],d.log_id);
  if(d.needs_confirm){const m=document.createElement('div');m.className='m bot';
    m.innerHTML='<button class="icon-btn" onclick="confirmAction()">✅ 确认执行</button>';feed.appendChild(m);}}
 catch(e){clearInterval(timer);th.remove();add('bot','⚠️ 出错了：'+e.message);}
 loadSessions();
 feed.scrollTop=feed.scrollHeight;}
// 打字机式浮现回答
function typeAnswer(text,src,logId){
  const m=document.createElement('div');m.className='m bot';
  m.innerHTML='<div class="b"></div>';const b=m.querySelector('.b');feed.appendChild(m);
  let i=0;const step=Math.max(1,Math.round(text.length/120));const rl=setInterval(()=>{
    i+=step;b.innerHTML='';b.appendChild(document.createTextNode(text.slice(0,i)));
    const last=document.getElementById('feed').lastElementChild;feed.scrollTop=feed.scrollHeight;
    if(i>=text.length){clearInterval(rl);const bm=m.querySelector('.b');bm.innerHTML=renderMd(text);
      const row=document.createElement('div');row.className='msgbot';row.innerHTML=
        '<button onclick="copyMsg(this)">⧉ 复制</button><button class="fb" onclick="fb(this,\''+logId+'\',\'good\')">👍</button>'+
        '<button class="fb" onclick="fb(this,\''+logId+'\',\'bad\')">👎</button>';
      m.appendChild(row);addSrc(m,src);feed.scrollTop=feed.scrollHeight;}
  },14);
}
async function fb(btn,logId,fbv){try{await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({log_id:logId,feedback:fbv})});
  btn.textContent=(fbv==='good')?'👍✓':'👎';btn.disabled=true;btn.style.opacity=.6;}catch(e){}}
function addSrc(msrcEl,src){if(!src||!src.length)return;const t=document.createElement('button');t.className='srcbtn';t.textContent='🔎 查看来源 ('+src.length+')';
  t.onclick=()=>{if(!t._s){t._s=document.createElement('div');t._s.className='srcbox';t._s.innerHTML=src.map(x=>'<div class="srci"><div class="st">'+esc(x.title)+'</div><div class="sc">'+esc(String(x.content||'').slice(0,160))+'</div></div>').join('');t.after(t._s);}
    const show=t._s.classList.toggle('show');t.textContent=show?'🔎 收起来源 ('+src.length+')':'🔎 查看来源 ('+src.length+')';};
  msrcEl.appendChild(t);}
function setToolsOn(on){const b=document.getElementById('toolsBtn');b.className='icon-btn '+(on?'on':'off');b.textContent=(on?'🛠️ 工具 · 开':'🛠️ 工具 · 关');
 const p=document.getElementById('toolsPerm');if(p)p.textContent=on?'开':'关';
 const r=document.getElementById('toolsRow');if(r)r.style.color=on?'#4ade80':'#f87171';
 const w=document.getElementById('wsInd');if(w){w.textContent=on?'🔐 Full access':'🔒 Read-only';} }
async function toggleTools(){const r=await fetch('/api/tools_toggle',{method:'POST'});const d=await r.json();setToolsOn(d.tools_on);}
async function loadModels(){try{const r=await fetch('/api/models');const d=await r.json();const sel=document.getElementById('modelSel');let ms=(d&&d.models)||[];
  sel.innerHTML=ms.length?ms.map(m=>'<option value="'+esc(m.name)+'">'+esc(m.name)+'</option>').join(''):'<option value="">未配置模型</option>';
  sel.value=(d&&d.current)||((ms[0]&&ms[0].name)||'');}catch(e){document.getElementById('modelSel').innerHTML='<option value="">模型加载失败</option>';}}
async function selectModel(){const v=document.getElementById('modelSel').value;await fetch('/api/model/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v})});}
async function loadHistory(){try{const r=await fetch('/api/history');const hs=await r.json();if(Array.isArray(hs)&&hs.length){hs.forEach(h=>add(h.role==='用户'?'user':'bot',h.content));}}catch(e){}}
async function loadSessions(){try{const r=await fetch('/api/sessions');const d=await r.json();const el=document.getElementById('sessionList');
  el.innerHTML=(d.sessions||[]).map(s=>'<button class="sess '+(s.id===d.current?'active':'')+'" onclick="openSession(\''+s.id+'\')">'+esc(s.title||'新对话')+'</button>').join('')||'<div class="think">暂无会话</div>';}catch(e){}}
async function newChat(){await fetch('/api/session/new',{method:'POST'});clearFeed();loadSessions();}
async function openSession(id){const r=await fetch('/api/session/'+id);const d=await r.json();clearFeed();(d.messages||[]).forEach(h=>add(h.role==='用户'?'user':'bot',h.content));loadSessions();}
function clearFeed(){document.getElementById('feed').innerHTML='<div class="think">👋 新对话，问小焦一个问题…</div>';}
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('hidden');}
(async()=>{try{loadModels();}catch(e){}try{loadHistory();}catch(e){}try{loadSessions();}catch(e){}
 try{const r=await fetch('/api/tools_toggle');const d=await r.json();setToolsOn(d.tools_on);}catch(e){}
 resumeVideoJob();resumeChat();})();
async function confirmAction(){const r=await fetch('/api/confirm',{method:'POST'});const d=await r.json();
 add('bot',(d.result||'已执行').slice(0,1200));}
inp.addEventListener('keydown',e=>{if(e.key==='Enter')send();});

const plugEl=document.getElementById('s_plugins');let plugins=[];
function applyPersona(){const v=document.getElementById('s_persona').value;if(!v)return;
  fetch('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:v})}).then(r=>r.json()).then(d=>{const m=document.getElementById('personaMsg');if(m)m.textContent=d.ok?'✅ 人格已切换（下次对话生效）':'❌ '+d.error;});
}

async function openSettings(){
  setSec('general');
  const r=await fetch('/api/settings');const d=await r.json();const c=d.control;
  document.getElementById('s_name').value=c.model_name||'';
  document.getElementById('s_engine').value=(c.brain&&c.brain.engine)||'auto';
  document.getElementById('s_temp').value=(c.behavior&&c.behavior.temperature)??0.7;
  document.getElementById('s_tokens').value=(c.behavior&&c.behavior.max_tokens)??1024;
  document.getElementById('s_ctx').value=(c.capabilities&&c.capabilities.context_len)??20;
  document.getElementById('s_base').value=((c.brain&&c.brain.api&&c.brain.api.base_url)||'');
  document.getElementById('s_llm_ctx').value=((c.brain&&c.brain.llama&&c.brain.llama.ctx)||32768);
  document.getElementById('s_role').value=c.role||'';
  const ps=c.personas||[];const pe=document.getElementById('s_persona');
  if(pe&&ps.length){pe.innerHTML=ps.map(x=>'<option value="'+esc(x.role)+'">'+esc(x.name+' · '+x.desc)+'</option>').join('');pe.value=c.role||'';}
  document.getElementById('s_tools').checked = !!(c.capabilities&&c.capabilities.run_tools);
  plugins=d.plugins||[];
  plugEl.innerHTML=plugins.map((p,i)=>`<div class="switch"><div><div class="n">${p.name} <small style="color:#7a8290">${p.type||'py'}${p.builtin?' · 内置':''}</small></div><div class="d">${(p.desc[0]&&p.desc[0].description)||''}</div></div><label class="plug"><input type="checkbox" data-i="${i}" ${p.on?'checked':''}/></label></div>`).join('');
  loadModelList();
  buildPluginModules(d.plugins||[]);
  S.classList.add('show');
}
async function loadModelList(){const r=await fetch('/api/models');const d=await r.json();const el=document.getElementById('s_model_list');
  el.innerHTML=(d.models||[]).map(m=>`<div class="switch"><div><div class="n">${esc(m.name)} <small style="color:#7a8290">${esc(m.engine)}</small></div><div class="d">${esc(m.base_url||'')}</div></div><button class="btn-sec" onclick="delModel('${esc(m.name)}')">删除</button></div>`).join('')||'<div class="think">还没有模型</div>';
}
async function addModel(){const name=document.getElementById('s_m_name').value.trim();if(!name){document.getElementById('s_model_msg').textContent='❌ 名字必填';return;}
  const entry={name:name,engine:document.getElementById('s_m_engine').value,base_url:document.getElementById('s_m_base').value.trim(),api_key:document.getElementById('s_m_key').value.trim(),model:document.getElementById('s_m_model').value.trim()};
  const r=await fetch('/api/model/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(entry)});const d=await r.json();
  if(d.ok){document.getElementById('s_model_msg').textContent='✅ 已添加：'+name;loadModelList();loadModels();}else{document.getElementById('s_model_msg').textContent='❌ '+(d.error||'失败');}}
async function delModel(name){const r=await fetch('/api/model/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})});const d=await r.json();if(d.ok){loadModelList();loadModels();}}
function closeSettings(){S.classList.remove('show');}
function setSec(el,name){document.querySelectorAll('.sec').forEach(s=>s.classList.toggle('show',s.id==='sec-'+name));
  document.querySelectorAll('.setnav-item').forEach(n=>n.classList.toggle('active',n.getAttribute('data-sec')===name));}
// 按已安装插件动态生成设置模块：只有"有可调配置"的插件才出现
function buildPluginModules(plist){
  const nav=document.querySelector('.setnav');const holder=document.getElementById('plugSecs');
  holder.innerHTML='';
  (plist||[]).filter(p=>p.type!=='skin' && p.settings && p.settings.length).forEach(p=>{
    const key='sec-plug-'+p.name;
    nav.insertAdjacentHTML('beforeend','<div class="setnav-item" data-sec="'+p.name+'" onclick="setSec(this,\''+p.name+'\')">🔧 '+p.name+'</div>');
    const fields=(p.settings||[]).map(s=>'<div class="field"><label>'+esc(s.label||s.key)+'</label><input id="set-'+p.name+'-'+s.key+'" data-p="'+p.name+'" data-k="'+s.key+'" data-t="'+s.type+'" data-def="'+esc(String(s.default??''))+'" placeholder="默认: '+esc(String(s.default??''))+'"/></div>').join('');
    const tools=(p.desc||[]).map(d=>'<div class="switch"><div><div class="n">'+esc(d.name)+'</div><div class="d">'+esc(d.description||'')+'</div></div></div>').join('');
    holder.insertAdjacentHTML('beforeend','<div class="sec" id="'+key+'"><h3>🔧 '+esc(p.name)+'</h3>'+fields+'<div class="actions"><button class="btn-sec" onclick="savePluginSettings(\''+p.name+'\')">💾 保存插件设置</button></div><div class="think" id="msg-'+p.name+'"></div><h4 style="margin-top:16px">可用工具</h4>'+tools+'</div>');
  });
  try{Object.keys(localStorage).filter(k=>k.startsWith('xjset-')).forEach(k=>{const el=document.getElementById('set-'+k.slice(6));if(el){el.value=localStorage.getItem(k)||'';}});}catch(e){}
}
function savePluginSettings(name){
  try{
    document.querySelectorAll('#sec-plug-'+name+' input[data-p="'+name+'"]').forEach(inp=>{
      const k=inp.getAttribute('data-k');const t=inp.getAttribute('data-t');
      const v=(t==='boolean')?(inp.value==='true'):inp.value;
      localStorage.setItem('xjset-'+name+'-'+k, v);
    });
    const m=document.getElementById('msg-'+name);if(m)m.textContent='✅ 已保存（本机生效）';
  }catch(e){}
}
async function saveSettings(){
  const engine=document.getElementById('s_engine').value;
  const plugmap={};plugins.forEach((p,i)=>{plugin_checked=document.querySelector('#s_plugins input[data-i="'+i+'"]');plugmap[p.name]=!!(plugin_checked&&plugin_checked.checked);});
  const control={
    model_name:document.getElementById('s_name').value,
    brain:{engine:engine,llama:{ctx:+(document.getElementById('s_llm_ctx').value||32768)},api:{base_url:document.getElementById('s_base').value, api_key:'', model:document.getElementById('s_name').value}},
    role:document.getElementById('s_role').value,
    capabilities:{web_search:true,memory:true,run_tools:document.getElementById('s_tools').checked,context_len:+document.getElementById('s_ctx').value,plugins:plugmap},
    behavior:{temperature:+document.getElementById('s_temp').value, max_tokens:+document.getElementById('s_tokens').value}
  };
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({control:control})});
  const d=await r.json();if(d.ok){location.reload();}else{alert('保存失败：'+d.error);}
}
</script></body></html>"""


def main():
    print("=" * 46)
    print("  小焦 · XiaoJiao Web")
    print(f"  大脑(模型): {'✔ 小焦模型已加载' if XJ_READY else '✘ 未加载'}")
    print(f"  联网搜索:   ✔ Bing/Sogou")
    print(f"  记忆自学习: ✔ (xiaojiao_knowledge_memory.json)")
    print("=" * 46)
    # 端口优先级：--port 参数 > 操控文件 web_port > 环境变量 PORT > 默认5000
    port = 5000
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except Exception:
            port = 5000
    else:
        port = int(CONTROL.get("web_port", os.environ.get("PORT", 5000)))
    os.environ["PORT"] = str(port)
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()