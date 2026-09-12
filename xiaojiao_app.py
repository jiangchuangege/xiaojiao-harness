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
from flask import Flask, request, jsonify, render_template_string, Response, redirect
import requests
import torch
import logging  # noqa: F401  （由 tools/fix_silent_except.py 注入）
try:
    from xiaojiao_log import get_logger
except Exception:  # 独立运行时退化为标准 logging
    def get_logger(name=None):
        return logging.getLogger(name or 'xiaojiao')
LOG = get_logger(__name__)

# ================== 配置（读取「操控文件」xiaojiao_control.json） ==================
# 你想让小焦成为什么类型的模型、用什么大脑、开哪些工具，全部由这个文件决定。
# 操控文件的**绝对路径**（模块级）：以前这个路径只是 _load_control() 里的局部变量 _CFG，
# 而 /api/persona 却直接引用 _CFG → 切人格一定 NameError 500（真实缺陷，ruff F821 抓出来的）。
CONTROL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json")


def strip_search_rules(role):
    """把混进人设里的**规则文本**剥掉，只留纯人设。

    role 只管人设（身份/性格/说话风格）。检索铁律、工具规则、技能清单、插件清单都该由
    代码独立拼接 —— 一旦被写进 role，就会出现三个真问题：改人设丢规则、加插件得手改人设、
    role 越写越长把人设淹没。

    历史遗留（真实事故）：`/api/tools_toggle` 与 `_save_control` 曾把**合成后**的
    SYSTEM_PROMPT 当人设存回控制文件，线上累积过 **11 份**铁律，每轮白背 3.5KB 提示词。
    所以这里把**所有**规则类片段都切掉（[检索铁律]/[工具铁律]/[简洁原则]/[代码工作流]/
    [技能插件]/[插件清单]/[环境]… 起始到下一个同级别段），且对"界面回传的合成人设"幂等。
    """
    s = role or ""
    cuts = []
    for mark in ("\n[检索铁律]", "[检索铁律]", "\n[工具铁律]", "[工具铁律]",
                 "\n[简洁原则]", "[简洁原则]", "\n[代码工作流]", "[代码工作流]",
                 "\n[技能插件]", "[技能插件]", "\n[插件清单]", "[插件清单]",
                 "\n【当前已加载", "【当前已加载", "\n[环境]", "[环境]", "\n[工具用法]", "[工具用法]"):
        i = s.find(mark)
        if i != -1:
            cuts.append(i)
    if cuts:
        s = s[:min(cuts)]
    return s.strip()


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
    # 优先读"模块所在目录"的操控文件（不受启动目录影响）；没有就退回相对路径，保持老行为
    for p in (CONTROL_FILE, "xiaojiao_control.json"):
        if not os.path.exists(p):
            continue
        try:
            c.update(json.load(open(p, encoding="utf-8")))
            break
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 44, e)
    # 自愈：老版本把人设写脏了（铁律被反复拼进去）→ 读进来就剥干净，内存里永远是纯人设
    if isinstance(c.get("role"), str):
        c["role"] = strip_search_rules(c["role"])
    return c

CONTROL = _load_control()
if isinstance(CONTROL, dict):
    CONTROL.setdefault("dsh", {}).setdefault("enabled", True)  # DSH 桥接永远默认开(防静默翻false)

MODEL_NAME = CONTROL.get("model_name", "xiaojiao1.0-4B")
BRAIN = CONTROL.get("brain", {})
BRAIN_ENGINE = BRAIN.get("engine", "auto")          # auto | llama | xiaojiao | api
LLM_BASE = BRAIN.get("api", {}).get("base_url", "http://127.0.0.1:8080/v1")
LLM_KEY = BRAIN.get("api", {}).get("api_key", "")
LLM_MODEL = BRAIN.get("api", {}).get("model", MODEL_NAME)
# 检索铁律：写死在代码里，而不是只写在 control 文件里 —— 用户换人设/换模型也不会把这条规矩弄丢。
# 真实缺陷防复发：小焦曾把功能字「用」当关键词去搜，搜回来的是"用（汉语汉字）"百科词条。
# ============================================================================
# 系统提示词的分层（**架构约定**：role 只负责人设，规则与清单由代码独立管理）
#   最终提示词 = role（纯人设） + _SEARCH_RULES（检索铁律） + _TOOL_RULES（工具规则）
#                + _plugin_list()（插件清单，从 PLUGINS 动态生成）
# 这样：改人设不丢规则；加插件不用手改人设；role 不会被规则淹没。
# ============================================================================
_SEARCH_RULES = (
    "\n[检索铁律] "
    "① 调用 web_search 时，query 只能是**内容关键词**（如「最近的漏洞 CVE」「2026 年 AI 新闻」），"
    "禁止把「用/搜/找/抓/看/搞/请/帮」这类功能字、语气词或整句话当检索词；"
    "② 漏洞/CVE/高危 类问题**优先**调用 collect_vulnerabilities(days=7, severity=\"HIGH\", limit=5)，"
    "不要用新闻搜索代替；"
    "③ 用户没说清要搜什么时，先反问「请告诉我你要搜索的具体关键词」，绝不用单个字去搜；"
    "④ **检索到的资料必须真读进去**：回答要落在资料的具体内容上（标题、数字、结论、原文措辞），"
    "不许把资料当摆设、自己另编一套；资料里没有的就直说没有；"
    "⑤ 用户让你**别搜/停止搜索/直接用某个工具**时，就照办：一次搜索都不要再发。"
)

_PLUGIN_LIST_TEMPLATE = "\n【当前已加载的工具（可直接调用）】\n%s"

# 工具规则（同样写死在代码里，不往 role 里塞）。
# 真实缺陷 a：原来是"凡是要帮我做实事都必须先调工具"，云端模型于是把寒暄也当"实事"——
#             实测一句"你好"它连调 read_file / get_ip / read_file / list_files 四个工具，
#             147 秒才吐出一句问候。工具是给"做事"用的，不是给聊天用的。
# 真实缺陷 b：用户让"用 Archify 画架构图"，模型不知道手上有 archify_* 工具（清单在 role 里、
#             没人维护），转头去联网搜「用」字。所以下面还要点明"先看上面清单里有没有现成工具"。
_TOOL_RULES = ("\n[工具铁律] "
               "① 只有当用户**明确要你做事**（写/改文件、建网页、跑命令、查资料、读文件、"
               "查 IP、抓网页、画图…）时才调用对应工具；寒暄、闲聊、概念解释、单纯问答"
               "**一个工具都不要调**（别为了打招呼去 read_file / list_files / get_ip）。"
               "② **做事之前先看上面那份工具清单**：用户点名某个插件/工具（如 Archify）时，"
               "直接调那个工具，**不要**改用 web_search 去搜；清单里没有的才说「没有这个工具」。"
               "③ 多步任务按顺序拆开做（先校验/先读文件，再产出结果），每步都调对应工具。"
               "④ **校验/报错必须一次性改完**：`archify_validate`（或任何校验类工具）失败时，"
               "要**按返回的全部报错一起修**，改好再校验**一次**；禁止「改一条→校验→再改一条」"
               "这种逐条试错（实测同一张图来回校验 7 次、白烧 200 多秒）。同一工具连续失败 3 次"
               "会被系统熔断，把最后一次报错直接摆给用户。"
               "⑤ 工具报错就把真实错误原样告诉用户并说明怎么修，不要自己编一个成功结果。"
               "\n[简洁原则] 回答要极简：只给结果/代码/结论，不要寒暄、不要说【好的我来帮你】、"
               "不要复述问题、不要多余解释。写代码只输出代码块。"
               "\n[代码工作流] 写/改代码请这样：① 先 read_file 看相关文件再动手；"
               "② 新增用 write_file，修改用 edit_file 精准替换；"
               "③ 改完用 run_command 验证（Python 用 python -c 语法检查、JS 用 node --check、"
               "或直接运行看结果）；④ 有报错就读出来修复。不要凭空猜测文件内容。")


def _plugin_list(plugins=None, max_tools=90):
    """从 PLUGINS 动态生成"插件清单"，让模型知道**现在到底有哪些工具**。

    为什么动态生成：以前这份清单写在 role 里，用户每加一个插件都得手改人设 —— 加完还常常
    忘，于是模型压根不知道新工具有、转头去联网搜（用户实测：让 Archify 画架构图，
    它跑去搜"用"字）。清单跟着 PLUGINS 走，重启/开关插件即自动生效。

    注意要**显式传入**刚加载好的插件表：`PLUGINS = load_plugins()` 这句赋值发生在函数返回
    **之后**，此刻全局 PLUGINS 还是旧的 —— 直接读全局会生成"当前无可用工具"（真实踩过）。
    """
    try:
        rows = plugins if plugins is not None else globals().get("PLUGINS") or {}
        items = []
        for pname, p in (rows or {}).items():
            if not p.get("on") or p.get("type") == "skin":
                continue
            for t in (p.get("desc") or []):
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                d = (t.get("description") or "").strip().replace("\n", " ")
                if len(d) > 46:
                    d = d[:46] + "…"
                items.append("- %s：%s" % (t["name"], d or pname))
        if not items:
            return _PLUGIN_LIST_TEMPLATE % "当前无可用工具"
        seen, uniq = set(), []
        for it in items:
            k = it.split("：")[0]
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        body = "\n".join(uniq[:max_tools])
        if len(uniq) > max_tools:
            body += "\n- …（另有 %d 个工具，见设置页「插件」）" % (len(uniq) - max_tools)
        return _PLUGIN_LIST_TEMPLATE % body
    except Exception as e:      # 清单生成失败绝不能拖垮提示词
        LOG.debug("忽略异常(%s:%d): %s", __file__, 76, e)
        return _PLUGIN_LIST_TEMPLATE % "当前无可用工具"


def compose_system_prompt(role, plugins=None):
    """**唯一**的系统提示词合成入口：人设 + 检索铁律 + 工具规则 + 动态插件清单。

    为什么单独立个函数：`reload_control()`（切人设/改配置后调用）原来直接
    `SYSTEM_PROMPT = CONTROL.get("role","")`，把规则全丢了 —— 缺陷会悄悄复发。
    每条规则都只加**一份**：先剥掉人设里历史遗留的规则文本，再按固定顺序拼。
    """
    return strip_search_rules(role) + _SEARCH_RULES + _TOOL_RULES + _plugin_list(plugins)


def refresh_system_prompt(plugins=None):
    """重建全局 SYSTEM_PROMPT（插件加载/开关后调用，让新工具立刻进清单）。"""
    global SYSTEM_PROMPT
    try:
        SYSTEM_PROMPT = compose_system_prompt(CONTROL.get("role", ""), plugins)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 96, e)
    return SYSTEM_PROMPT


SYSTEM_PROMPT = compose_system_prompt(CONTROL.get("role", ""))   # ← 人设/类型，改 control 文件即换模型人格
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
    CONTROL.setdefault("dsh", {}).setdefault("enabled", True)
    MODEL_NAME = CONTROL.get("model_name", "xiaojiao1.0-4B")
    BRAIN = CONTROL.get("brain", {})
    BRAIN_ENGINE = BRAIN.get("engine", "auto")
    LLM_BASE = BRAIN.get("api", {}).get("base_url", "http://127.0.0.1:8080/v1")
    LLM_KEY = BRAIN.get("api", {}).get("api_key", "")
    LLM_MODEL = BRAIN.get("api", {}).get("model", MODEL_NAME)
    SYSTEM_PROMPT = compose_system_prompt(CONTROL.get("role", ""))   # 走统一合成，别把检索铁律丢掉
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


def _make_tools_plugin(man):
    """把 OpenAI/Claude/DSH 风格的 tools 清单转成小焦插件实例(适配器)。
    OpenAI: {"tools":[{"type":"function","function":{"name","description","parameters"}}]}
    Claude: {"tools":[{"name","description","input_schema"}]}  (input_schema 转 parameters)
    DSH:    {"tools":[{"name","description","parameters","url"}]}  (url 为直接调用的 API)
    """
    tools = man.get("tools") or []
    descs = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) and "function" in t else t
        if not isinstance(fn, dict):
            continue
        name = fn.get("name") or ""
        if not name:
            continue
        params = fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}}
        if not isinstance(params, dict) or params.get("type") is None:
            params = dict(params or {}); params.setdefault("type", "object"); params.setdefault("properties", params.get("properties") or {})
        descs.append({"name": name, "description": fn.get("description", ""),
                      "url": fn.get("url", t.get("url", "")), "manifest": man})
    if not descs:
        return None
    return _ToolsPlugin(descs)


class _ToolsPlugin:
    """从 OpenAI/Claude/DSH 工具清单生成的插件(可调用外部API或提示运行时)。"""
    def __init__(self, descs):
        self._descs = descs
        self._tp = {}
        for d in descs:
            self._tp[d["name"]] = d
    def get_tool_descriptions(self):
        return [{"name": d["name"], "description": d["description"],
                 "url": d.get("url", ""),
                 "parameters": {"type": "object", "properties": {}}} for d in self._descs]

    def has_url(self, tool_name):
        """这个工具到底能不能执行（清单里带 url 才算）。

        为什么单独给方法：`get_tool_descriptions()` 返回的是**给模型看的**精简结构，
        某些工具（旧版清单）里没带 url —— 直接拿它判"能不能执行"会把
        `plugins/ip.json` 这种**带 url 的正常插件**也误杀（这个坑我踩过：修幻影工具时
        把 get_ip/get_ip_info 一起过滤掉了）。判据必须看插件**自己的**清单。
        """
        d = self._tp.get(tool_name) or {}
        return bool(d.get("url") or (d.get("manifest") or {}).get("url"))
    def execute(self, tool_name, params):
        d = self._tp.get(tool_name)
        if not d:
            return "未知工具"
        url = d.get("url") or (d.get("manifest") or {}).get("url", "")
        if url:
            try:
                import requests as _rq
                rr = _rq.post(url, json=params, timeout=20)
                return rr.text[:800]
            except Exception as e:
                return "调用失败: " + str(e)[:80]
        return "该工具「%s」来自外部清单(OpenAI/Claude/DSH)，已在本地注册；实际执行需对应运行时或填写 url。" % tool_name


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
                sys.modules[base] = mod          # 注册进 sys.modules：Py3.13 下 dataclass/typing 等依赖它
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    sys.modules.pop(base, None)  # 加载失败则清理，避免污染 sys.modules
                    raise
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
                elif man.get("tools"):
                    inst = _make_tools_plugin(man)
                    desc = inst.get_tool_descriptions() if inst else []
                    if desc:
                        plugins[base] = {"instance": inst, "desc": desc, "builtin": False, "type": "tools", "path": p, "manifest": man}
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
    # **架构约定**：插件清单是动态生成的，插件一变就重建提示词 —— 这样往 plugins/ 丢一个
    # 新 .py、重启小焦，新工具自动出现在模型的工具清单里，**不需要**任何人去改 role。
    try:
        refresh_system_prompt(plugins)      # 必须把刚加载好的表传进去（全局变量此刻还是旧的）
    except Exception as e:  # noqa: silent-ok — 清单重建失败不影响插件本身加载
        LOG.debug("忽略异常(%s:%d): %s", __file__, 180, e)
    return plugins


PLUGINS = load_plugins()   # 插件注册表（设置页可开关）


def _tool_result_str(r):
    """把插件/工具返回统一成字符串（dict/list → JSON；None → 空串），
    避免下游 result[:n] 切片对非字符串崩溃（如插件返回 {"error": ...} 导致 KeyError/TypeError）。"""
    if r is None:
        return ""
    if isinstance(r, str):
        return r
    try:
        return json.dumps(r, ensure_ascii=False)
    except Exception:
        return str(r)


def run_plugin(name, params):
    """调用某个插件（由 LLM/Agent 决定何时用）。支持 py / api / js。始终返回字符串。"""
    p = PLUGINS.get(name)
    if not p or not p.get("on"):
        return ""
    # JS 插件：起 node 子进程执行
    if p.get("type") == "js":
        import subprocess
        try:
            r = subprocess.run(["node", os.path.join("plugins", "plugin_runner.js"), "exec",
                                p.get("path"), params.get("name"), json.dumps(params.get("params", {}), ensure_ascii=False)],
                               capture_output=True, text=True, timeout=90, encoding="utf-8")
            return (r.stdout or r.stderr or "").strip()
        except Exception as e:
            return f"JS插件执行失败：{e}"
    if "instance" not in p:
        return ""
    try:
        return _tool_result_str(p["instance"].execute(params.get("name"), params.get("params", {})))
    except Exception:
        return ""


_TOOL2PLUGIN = {}   # 工具名 -> 插件模块名


_COST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_daily.json")
_CLOUD_BASELINE = 0.000008  # 全云端基线: 按 deepseek-chat 入0.002/出0.008 每token约合
_CLOUD_IN, _CLOUD_OUT = 0.002, 0.008  # 元/1K token (deepseek-chat 参考价)


_TTS_FILES = ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"]


def _find_tts_model_dir():
    """自动识别 Chatterbox 模型目录(不写死): 环境变量/配置/扫描常见位置, 找到含全部文件的目录。"""
    import glob as _g
    # ① 环境变量
    env_dir = os.environ.get("XIAOJIAO_TTS_MODEL", "")
    if env_dir and os.path.isdir(env_dir) and all(os.path.exists(os.path.join(env_dir, f)) for f in _TTS_FILES):
        return env_dir
    # ② 控制文件配置
    _cfg_dir = CONTROL.get("brain", {}).get("tts_model_dir", "")
    if _cfg_dir and os.path.isdir(_cfg_dir) and all(os.path.exists(os.path.join(_cfg_dir, f)) for f in _TTS_FILES):
        return _cfg_dir
    # ③ 扫描常见位置（项目目录 / 家目录 / 下载；再按关键词扫盘 —— 不写死用户路径）
    cands = [os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
             os.path.expanduser("~"), os.path.join(os.path.expanduser("~"), "Downloads"),
             os.path.join(os.path.expanduser("~"), "Documents"), "C:\\llama"]
    try:
        import install_all as _ia
        _kws = ("语音", "tts", "voice", "model", "模型", "xiaojiao") + tuple(_ia.DISCOVER_KEYWORDS)
        for _drv in _ia._drives():
            for _t in _ia._top_dirs(_drv):
                if _ia._hit_keyword(_t, _kws) or _ia._hit_keyword(_t, ("downloads", "下载")):
                    cands.append(os.path.join(_drv, _t))
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 344, e)
    for c in cands:
        try:
            subs = [c] + [os.path.join(c, x) for x in os.listdir(c) if os.path.isdir(os.path.join(c, x))]
        except Exception:
            continue
        for d in subs:
            if all(os.path.exists(os.path.join(d, f)) for f in _TTS_FILES):
                return d
    return None


def _record_usage(usage, model=""):
    """记录一次调用的 token 用量(本地=免费, 云端=计费)。写入当日成本文件。"""
    try:
        u = usage or {}
        pt = int(u.get("prompt_tokens") or 0)
        ct = int(u.get("completion_tokens") or 0)
        if not pt and not ct:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        is_cloud = ("api." in LLM_BASE or "deepseek" in LLM_BASE.lower() or "openai" in LLM_BASE.lower())
        d = {}
        if os.path.exists(_COST_FILE):
            try:
                d = json.load(open(_COST_FILE, encoding="utf-8"))
            except Exception:
                d = {}
        day = d.setdefault(today, {"calls": 0, "local_tokens": 0, "cloud_tokens": 0, "cost": 0.0})
        day["calls"] += 1
        if is_cloud:
            day["cloud_tokens"] += pt + ct
            day["cost"] += (pt / 1000.0) * _CLOUD_IN + (ct / 1000.0) * _CLOUD_OUT
        else:
            day["local_tokens"] += pt + ct
        json.dump(d, open(_COST_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 381, e)


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
                # **真实缺陷**：外部清单类插件（`{"tools":[{...}]}` 且**没写 url**）里的工具
                # 其实**执行不了**（execute 只会回一句"需对应运行时或填写 url"）。原来照样塞给
                # 模型 → 模型真的去调它，拿到一句废话，用户看到的就是"调用了工具却没结果"
                # （实测截图上就出现过 `调用 get_time → 需填写 url` 这种徽标）。
                # 判据问插件自己的清单（has_url）——**不能**看 get_tool_descriptions() 的返回值，
                # 那里面本来就不带 url，会把 plugins/ip.json 这种正常插件一起误杀。
                _inst = p.get("instance")
                if hasattr(_inst, "has_url") and not _inst.has_url(t["name"]):
                    continue
                _TOOL2PLUGIN[t["name"]] = pname
                tools.append({"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}})}})
    # 规范化: 每个工具的 parameters 必须是 JSON Schema object(严格API如deepseek要求)
    for t in tools:
        fn = t.get("function") or {}
        prm = fn.get("parameters")
        if not isinstance(prm, dict) or prm.get("type") is None:
            prm = dict(prm or {})
            prm.setdefault("type", "object")
            prm.setdefault("properties", prm.get("properties") or {})
            fn["parameters"] = prm
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


# ================== 检索词清洗（真实缺陷：小焦曾把功能字「用」当关键词去搜） ==================
# 复盘：用户说"用搜索工具找漏洞" —— 模型把整句/单个功能字直接丢给 web_search，
# 搜出来的是"用（汉语汉字）"这种百科词条，完全跑偏。
# 修法：① 代码层强制清洗（不管模型/上层给的是什么）；② 清洗后仍无内容 → 反问用户要关键词，绝不用单字硬搜。
_SEARCH_CMD_MARKERS = ("搜", "查", "找", "抓", "爬", "检索", "联网", "上网", "搜索", "工具")
_SEARCH_FILLERS = (
    "用搜索工具", "搜索工具", "联网搜索", "联网查一下", "联网查", "上网搜一下", "上网搜", "网上搜",
    "帮我搜一下", "帮我搜", "帮忙搜", "帮我查一下", "帮我查", "帮忙查", "帮我找一下", "帮我找",
    "给我搜", "给我查", "给我找", "搜索一下", "搜一下", "查一下", "找一下", "抓一下", "爬一下",
    "搜索", "检索", "联网", "上网", "网上", "帮我", "帮忙", "请问", "麻烦", "谢谢", "一下",
    "一个", "一些", "给我", "来个", "给出", "列一下", "看看", "瞧瞧", "找找", "找一找",
    "写个", "帮我写个", "做一个", "搞一个", "查查", "搜搜",
)
# 单字功能/语气词：只在"开头或两侧带空格"时算噪声，避免误伤"未来/在线/用户"这类真词
_SEARCH_FUNC_CHARS = "用搜找抓查看搞请帮要想来去呗吧的了呢吗啊呀把给让我你它他她是个些就都还很这那与和在有"
_SEARCH_MEANINGLESS = set(_SEARCH_FUNC_CHARS)
# 空格后可以直接删的"纯助词/动作词"（删了不会把真词切坏：在线/未来/用户 都不在这个集合里）
_SEARCH_MID_FUNC = "的了是用搜找查抓看请帮"
# 纯寒暄/自我介绍：这种话不该拿去联网搜（搜出来只会是"你（汉语文字）_百度百科"这类词条）
_SEARCH_GREETINGS = {
    "你好", "您好", "哈喽", "在吗", "在么", "谢谢", "多谢", "辛苦了", "早", "早上好", "晚上好",
    "你是谁", "你叫什么", "你叫啥", "介绍一下你", "自我介绍", "hi", "hello", "hey", "thanks",
    "thank you", "ok", "好的", "嗯", "哦", "在不在",
}
SEARCH_KEYWORD_HINT = "请告诉我你要搜索的具体关键词（例如：最近的漏洞 CVE、2026 年 AI 新闻）。"


def _has_search_marker(s):
    """句子里有没有"检索动作词" —— 有才是命令式（可以大胆删功能字），没有就当裸关键词保守处理。"""
    return any(m in s for m in _SEARCH_CMD_MARKERS)


def extract_search_keywords(text):
    """把「用联网搜一下最近的漏洞」这类口语指令清洗成真正能用的检索关键词。

    · 命令式（含 搜/查/找/抓/联网/工具…）：删掉动作词、语气词、标点 → "漏洞"；
    · 裸关键词（用户直接甩词，如"看雪安全"）：只做保守清洗，绝不删词内的字（不能变成"雪安全"）。
    """
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"[，。！？；：、,.!?;:\"'“”‘’（）()\[\]【】<>《》~]+", " ", s)
    for w in sorted(_SEARCH_FILLERS, key=len, reverse=True):
        s = s.replace(w, " ")
    s = re.sub(r"\s+", " ", s).strip()
    if _has_search_marker(text or ""):
        # ⚠️ 真实缺陷（用户实测）：以前这里是**贪婪删掉开头一串功能字**，
        # 于是"帮我搜索一下 你好"洗完只剩"好"，搜出来是"好（汉语文字）_百度百科"。
        # 现在改成"逐个删，但必须给内容留够 2 个字"——"你好"不会被拆，"找漏洞"能洗成"漏洞"。
        while len(s) >= 3 and s[0] in _SEARCH_MEANINGLESS:
            s = s[1:].lstrip()
        # 空格后出现的**纯助词/动作词**也算噪声（"apache 的漏洞" → "apache 漏洞"）；
        # 但不动"在线/未来"这类会把真词切坏的字符。
        s = re.sub(r"(?<=\s)[%s]+(?=\s|[\u4e00-\u9fa5]|$)" % _SEARCH_MID_FUNC, " ", s)
        s = re.sub(r"(^|\s)[%s](?=\s|$)" % _SEARCH_FUNC_CHARS, r"\1", s)            # 独立成词的功能字
    return re.sub(r"\s+", " ", s).strip()


# 当前这次对话的用户原话（供工具层判断"模型是不是只截了一个碎片"）
_CTX = {"user_input": ""}


def _better_search_query(model_q, user_text):
    """模型给的检索词常常只是用户整句里的**一个碎片**，这时改用整句清洗后的关键词。

    真实缺陷（用户实测）：说"最近 AI 新闻"，模型只把"最近"丢给搜索 → 搜回来的是
    "最近（李圣杰2006年演唱的歌曲）""最近（汉语词语）_百度百科" 这种词条，答非所问。
    判据很保守：只有当"模型给的词**确实是用户这句话的一部分**、且整句能洗出更长的关键词"时才替换。
    """
    mq = (model_q or "").strip()
    if not mq or not user_text:
        return mq
    uq, _ = resolve_search_query(user_text)
    if uq and mq != uq and mq in uq and len(uq) > len(mq):
        LOG.info("检索词过短/碎片化，已改用整句关键词：%r → %r", mq[:40], uq[:60])
        return uq
    return mq


def _is_meaningless_query(q):
    """清洗后的关键词是不是"根本没内容"（空 / 单个功能字 / 全是标点 / 纯寒暄）。

    真实缺陷：模型有时会把「用」「你」这种字当检索词丢给 web_search，
    搜回来的是"你（汉语文字）_百度百科"这类词条 —— 跟用户想问的毫无关系。
    """
    q = (q or "").strip()
    if not q:
        return True
    if len(q) == 1 and q in _SEARCH_MEANINGLESS:
        return True
    if q.lower() in _SEARCH_GREETINGS:          # 寒暄/自我介绍类，本来就不该联网搜
        return True
    return all((ch in _SEARCH_MEANINGLESS) or (not ch.isalnum()) for ch in q)


def _strip_think(text):
    """剥掉模型输出的思维块标签（`<think>…</think>` / `<thinking>` / 残留的半个标签）。

    真实缺陷：模型偶尔把空的 `<think></think>` 一起吐到正文里，聊天窗就显示成
    两行莫名其妙的标签。这里按"整块删掉 + 残留标签删掉 + 顺带清空多余空行"处理。
    """
    s = str(text or "")
    # 注意：这里必须连**单独的闭标签**也算命中（`</think>` 里并没有 "<think" 这个子串）——
    # 早期版本就是因为这个判断写窄了，"</think>" 残留在正文里。
    if not re.search(r"</?(?:think|thinking|reasoning)>", s, re.I):
        return s
    s = re.sub(r"(?is)<(think|thinking|reasoning)>.*?</\1>", "", s)      # 成对：整块删
    s = re.sub(r"(?is)</?(think|thinking|reasoning)>", "", s)            # 未闭合/残留：只删标签
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def resolve_search_query(text):
    """检索统一闸门：返回 (可用关键词, 错误提示)。关键词为空时**必须**提示用户，不许硬搜。"""
    raw = (text or "").strip()
    q = extract_search_keywords(raw)
    if _is_meaningless_query(q):
        LOG.warning("检索词无效，已拒绝搜索（原文=%r，清洗后=%r）", raw[:60], q[:60])
        return "", SEARCH_KEYWORD_HINT
    if _is_vuln_query(q) and "cve" not in q.lower():
        q = (q + " CVE").strip()          # 漏洞类检索自动带上 CVE，避免搜出无关新闻
    return q, ""


# ================== 漏洞查询直通（NVD 结构化数据，不让模型"看新闻猜漏洞"） ==================
# 复盘：以前"抓最近 7 天的高危漏洞"拿到的是 1999 年数据、受影响软件全是 n/a、5 条只总结 1 条。
# 现在改为：识别到"要漏洞清单"的意图 → 直接调插件 collect_vulnerabilities → 表格原样给用户。
_VULN_WORDS = ("漏洞", "cve-", "cve ", "cve编号", "cve编号", "0day", "零日", "exploit",
               "安全公告", "补丁公告", "高危")
_VULN_DATA_HINTS = ("搜", "查", "找", "抓", "看", "要", "给", "列", "汇总", "统计", "整理", "总结",
                    "最新", "最近", "近期", "今日", "今天", "本周", "这周", "本月", "这个月",
                    "这几天", "近几天", "天", "条", "高危", "严重", "紧急", "级别", "等级")


def _is_vuln_query(text):
    t = (text or "").lower()
    return any(w in t for w in _VULN_WORDS)


def detect_vulnerability_query(text):
    """识别"要看漏洞清单"的意图 → 返回 collect_vulnerabilities 的参数；识别不到返回 None。

    只认"要数据"的说法（含 搜/查/找/抓/最新/最近/高危/N天…）；
    纯概念提问（"什么是漏洞"）不拦，仍交给大脑正常回答。
    """
    raw = (text or "").strip()
    low = raw.lower()
    if not _is_vuln_query(raw) or not any(h in low for h in _VULN_DATA_HINTS):
        return None
    days = 7
    m = re.search(r"(\d+)\s*天", raw)
    if m:
        days = int(m.group(1))
    elif any(k in raw for k in ("今天", "今日", "当日")):
        days = 1
    elif any(k in raw for k in ("昨天", "昨日")):
        days = 2
    elif any(k in raw for k in ("一个月", "本月", "这个月", "近一月")):
        days = 30
    elif any(k in raw for k in ("一周", "本周", "这周", "七天", "7 天")):
        days = 7
    days = max(1, min(days, 120))                     # NVD 官方限制：时间窗 ≤ 120 天
    severity = "HIGH"
    if any(k in low for k in ("严重", "致命", "critical", "紧急")):
        severity = "CRITICAL"
    elif any(k in low for k in ("高危", "high")):
        severity = "HIGH"
    elif any(k in low for k in ("中危", "medium")):
        severity = "MEDIUM"
    elif any(k in low for k in ("低危", "low")):
        severity = "LOW"
    elif any(k in low for k in ("全部", "所有", "不限", "any")):
        severity = "ANY"
    limit = 5
    m2 = re.search(r"(\d+)\s*(条|个|项|款)", raw)
    if m2:
        limit = int(m2.group(1))
    return {"days": days, "severity": severity, "limit": max(1, min(limit, 50))}


def _query_variants(q):
    """同一意图的多种写法，按"最干净"排前面。

    真实缺陷（用户实测）：中文搜索引擎对"最近 X"这种前缀极不友好 —— 搜"最近的漏洞 CVE"、
    "最近 AI 新闻"返回的全是歌曲《最近》/词典词条，因为引擎基本只认第一个词。
    所以依次尝试：原词 → **只用主题词** → 去掉时间词 → 时间词后置。
    """
    out = [q]
    toks = _query_tokens(q)
    if toks:
        out.append(" ".join(toks))                    # 最干净：只留主题词
    for w in _SEARCH_TIME_WORDS:
        if q.startswith(w) and len(q) > len(w):
            rest = q[len(w):].lstrip(" 的了是")
            if rest:
                out.append(rest)                      # 去掉时间词
                out.append("%s %s" % (rest, w))        # 时间词后置
    m = re.match(r"^(\d{4})\s*年?\s*(.+)$", q)         # 开头是年份也一样：引擎会只认年份
    if m and len(m.group(2)) >= 2:
        out.append(m.group(2))
        out.append("%s %s" % (m.group(2), m.group(1)))
    seen, uniq = set(), []
    for v in out:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq[:3]                                   # 最多试 3 种，别把用户等急了


_JUNK_TITLE_HINTS = ("_百度百科", "百度百科", "维基词典", "汉语国学", "的意思_", "怎么读",
                     "新华字典", "词典", "在线翻译")
_SEARCH_TIME_WORDS = ("最近", "最新", "近期", "这几天", "近几天", "今天", "今日", "本周", "这周", "本月")


def _query_tokens(query):
    """查询的"主题词"（用于相关度判断）：去掉时间词/助词/疑问尾巴，中英分开切。

    "最近AI新闻" → ['AI', '新闻']；"最近的漏洞 CVE" → ['漏洞', 'CVE']。
    时间词本身不带主题信息，参与打分只会把《最近》这种噪音顶上来。
    """
    q = query or ""
    for w in _SEARCH_TIME_WORDS:
        q = q.replace(w, " ")
    q = re.sub(r"[的了是]", " ", q)
    q = re.sub(r"(有哪些|有什么|是什么|怎么样|怎么办|怎么|多少|什么|吗|呢|啊|吧|[?？。！!]+)\s*$", " ", q)
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#_\-]*|[\u4e00-\u9fa5]{2,}", q)
    return [t for t in toks if len(t) >= 2]


def _search_relevance(query, title, content):
    """结果与查询的相关度 → (分数, 主题词覆盖率)。

    覆盖率是判断"这批结果到底有没有跑题"的关键指标：搜"最近的漏洞 CVE"却全是
    歌曲《最近》时，覆盖率 0；真正讲漏洞的结果覆盖率会到 1。排序用分数，换写法用覆盖率。
    """
    toks = _query_tokens(query)
    text = ("%s %s" % (title or "", content or "")).lower()
    hit = sum(1 for t in toks if t.lower() in text)
    gram = 0.0
    for t in toks:
        if t.lower() in text:
            continue
        if re.search(r"[\u4e00-\u9fa5]", t):
            gram += sum(0.5 for i in range(len(t) - 1) if t[i:i + 2] in text)
    score = 2.0 * hit + gram
    if toks and any(h in (title or "") for h in _JUNK_TITLE_HINTS):
        score -= 3.0                                             # 词典/词条类：明显偏题
    cov = (hit / len(toks)) if toks else 1.0
    return score, cov


def _search_engines(query, limit=8):
    """依次问 Bing / Sogou / DuckDuckGo，返回去重后的 [(标题, 链接, 内容)]。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    engines = [("https://cn.bing.com/search?q=", r'<li class="b_algo"[^>]*>(.*?)</li>'),
               ("https://www.sogou.com/web?query=", r'<div class="vrwrap"[^>]*>(.*?)</div>'),
               ("https://html.duckduckgo.com/html/?q=", r'<div class="result[^"]*"[^>]*>(.*?)</div>')]
    out, seen = [], set()
    for base, block_re in engines:
        try:
            r = requests.get(base + requests.utils.quote(query), headers=headers, timeout=WEB_TIMEOUT)
            if r.status_code != 200:
                continue
            for block in re.findall(block_re, r.text, re.S):
                h2 = re.search(r'<h2[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.S)
                url = ""
                am = re.search(r'href="([^"]+)"', block, re.S)
                if am:
                    u = am.group(1)
                    if u.startswith("http") and not u.startswith("https://cn.bing.com/images"):
                        url = u
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
                    out.append((title, url, content))
                if len(out) >= limit:
                    break
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def web_search(query, num=6):
    """免密钥 Bing/Sogou/DuckDuckGo 中文搜索，返回 [(标题, 链接, 内容)]。

    两道保险：
      ① 检索词清洗（上层可能丢进整句、功能字或只言片语）；
      ② **多变体 + 相关度排序**：中文引擎对"最近 X"只认第一个词，所以先试原词，
         结果里连一个内容词都命中不了就换写法（去时间词 / 时间词后置），并把
         词典词条类噪音降权 —— 保证用户拿到的是跟主题相关的结果。
    """
    query = extract_search_keywords(query)
    if _is_meaningless_query(query):
        LOG.warning("检索词无效，已跳过搜索：%r", str(query)[:60])
        return []
    fallback = []
    for v in _query_variants(query):
        hits = _search_engines(v, limit=max(num, 8))
        if not hits:
            continue
        hits.sort(key=lambda x: _search_relevance(query, x[0], x[2])[0], reverse=True)
        top = hits[:num]
        _sc, _cov = _search_relevance(query, top[0][0], top[0][2])
        if _cov >= 0.6:                              # 主题词覆盖够高 → 这批结果是对的，不再多问引擎
            LOG.info("检索命中：%r（%d 条，覆盖率 %.0f%%）", v[:40], len(top), _cov * 100)
            return top
        if not fallback:
            fallback = top
        LOG.info("检索词 %r 结果跑题（覆盖率 %.0f%%），换写法重试", v[:40], _cov * 100)
    return fallback[:num]


# ================== 检索引用校验（回答到底有没有"真读"资料） ==================
# 真实需求：光搜到没用 —— 得看模型有没有把资料读进去。这里用"资料里的**独有**用语"
# 去回答里找：独有 = 出现在资料但**不在用户问题**里。命中越多，说明越是在照着资料说；
# 一个都命中不了，多半是自己另编了一套（或者干脆没读），这时前端会标注出来。
_GROUND_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "http", "https", "www", "com",
    "首页", "登录", "注册", "更多", "详情", "相关", "推荐", "广告", "网站", "页面", "内容",
    "查看", "了解", "点击", "我们", "你们", "他们", "可以", "以下", "关于", "最新", "最近",
}


def _grounding_applies(question, sources, answer):
    """这条回答需不需要"必须引用资料"？

    写代码/跑命令这类请求本来就不该拿新闻资料去对答案，硬校验只会误报；
    检索本身就跑题（覆盖率低）时也不能怪模型没读。这两种情况都不给结论。
    """
    if not sources:
        return False
    _act = re.compile(r"(写|生成|实现|做个|做一个|创建一个|新建|改一下|修复|优化|重构|运行|执行|命令|脚本|代码|函数|类|页面|网页|插件|安装|配置|部署)")
    if _act.search(question or "") and "```" in (answer or ""):
        return False
    try:
        covs = [_search_relevance(question, s[0], s[2])[1] for s in sources[:5] if len(s) >= 3]
    except Exception:
        covs = []
    if covs and max(covs) < 0.4:
        return False
    return True


def _grounding(answer, sources, question=""):
    """判断回答是否真的基于检索资料 → {"matched", "considered", "grounded"}。

    只看"资料里独有"的词（排除问题里已有的），避免"把问题复述一遍"就算读过。
    不适用（写代码/检索跑题/没有资料）时 grounded=None，前端不显示任何标记。
    """
    src_text = " ".join("%s %s" % (s[0], s[2]) for s in (sources or [])[:5] if len(s) >= 3)
    ans = str(answer or "")
    if not src_text.strip() or not ans.strip() or not _grounding_applies(question, sources, ans):
        return {"matched": 0, "considered": 0, "grounded": None}
    q_toks = set(_query_tokens(question or "") + re.findall(r"[A-Za-z0-9]{2,}", question or ""))
    lat = {t for t in re.findall(r"[A-Za-z][A-Za-z0-9.+#_-]{3,}", src_text)
           if t.lower() not in _GROUND_STOP}
    cjk = {t for t in re.findall(r"[\u4e00-\u9fa5]{2,4}", src_text) if t not in _GROUND_STOP}
    uniq = [t for t in (lat | cjk) if t not in q_toks and t.lower() not in (x.lower() for x in q_toks)]
    if not uniq:
        return {"matched": 0, "considered": 0, "grounded": None}
    matched = sum(1 for t in uniq if t.lower() in ans.lower())
    considered = len(uniq)
    # 命中 2 个以上独有词就算"确实读了资料"（中文 2-gram 噪音大，所以要求 ≥2）
    return {"matched": matched, "considered": considered, "grounded": matched >= 2}


def _grounding_note(g):
    """给界面的一句话提示。

    **按用户要求关掉界面提示**（"没必要提示这个"）：这条"⚠️ 这条回答基本没用到检索资料"
    在正常聊天里也常出现，属于打扰。核对数据仍然照算（`/api/chat` 的 `grounding` 字段、
    压测报告里都在用），只是不再往聊天界面上挂徽标；要看就点「查看来源」。
    """
    return ""


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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 503, e)


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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 554, e)


# ================== 会话存储（每个新对话一个会话，可切换） ==================
SESSIONS_FILE = "xiaojiao_sessions.json"


def _sessions():
    try:
        d = json.load(open(SESSIONS_FILE, encoding="utf-8"))
        if isinstance(d, dict) and "sessions" in d:
            return d
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 567, e)
    default = {"id": "default", "title": "新对话", "messages": []}
    return {"current": "default", "sessions": [default]}


def _save_sessions(d):
    try:
        json.dump(d, open(SESSIONS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 576, e)


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
# 最近一次大脑调用失败的真实原因（给用户看 + 落 WARNING 日志）。空串 = 没失败过。
# **真实缺陷**：原来非 200 直接 `return None`，连一行日志都没有 —— 用户只看到
# 「模型调用出错（可能是连接超时/限流）」这种猜谜提示；实测 API Key 失效(401)
# 也照样这么糊过去，查都没法查。现在真实状态码 + 服务端原话一定带出来。
_LAST_LLM_ERROR = ""
_LLM_ERR_LOGGED = set()
# 云端调用成败流水（最近 20 次）：用来区分"偶发拒签"和"持续拒签"，好告诉用户到底是谁的问题
_LLM_STAT = {"ok": 0, "fail": 0, "recent": []}
# 云端熔断：连续被拒就先"歇一会儿"用本地大脑，别继续硬打（人家的免费档有频率限制，
# 打越猛越全是 401，用户还得干等 —— 实测就是"连打 5 次全 401，静默两分钟后单发就通了"）
_CLOUD_BREAK = {"fails": 0, "until": 0.0, "cooldown": 60.0}
# 本次请求是不是"云端授权失败、自动改用本地大脑"答的（回答里会如实说明）
_USED_LOCAL_FALLBACK = {"on": False, "model": "", "reason": ""}
_LOCAL_PROBE = {"at": 0.0, "model": ""}


def _local_brain_model(force=False):
    """本机 llama-swap 上真正可用的模型 id（探到缓存 60 秒；探不到返回空串）。

    为什么要它：云端 API Key 一旦失效（401/403），小焦原来只会反复回一句"模型调用出错"，
    用户完全没法用。而**本地大脑就在本机**（llama-swap 9292），完全能顶上 —— 所以云端
    授权失败时自动兜到本地，并在回答里如实说明，而不是把用户卡死在一句模板上。

    注意：llama-swap 换模型/加载模型时 `/v1/models` 会短暂失败 —— 那时候**不能**当作
    "本地没有大脑"，否则兜底链就断了（实测就踩过这个坑）。所以：探不到时退回上次探到的，
    再不行用已知的本地默认模型名，保证兜底这条路永远有目标。
    """
    global _LOCAL_PROBE
    if not force and _LOCAL_PROBE["model"] and (time.time() - _LOCAL_PROBE["at"]) < 60:
        return _LOCAL_PROBE["model"]
    port = int((CONTROL.get("brain", {}) or {}).get("llama_swap_port", 9292) or 9292)
    base = "http://127.0.0.1:%d/v1" % port
    for _try in range(2):
        try:
            r = requests.get(base + "/models", timeout=5)
            if r.status_code == 200:
                ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
                # llama-swap 里 coder 是写代码用的，聊天优先用它之外的模型
                pick = next((i for i in ids if "coder" not in str(i).lower()), (ids[0] if ids else ""))
                if pick:
                    _LOCAL_PROBE = {"at": time.time(), "model": pick, "base": base}
                    return pick
        except Exception as e:  # noqa: silent-ok — 本地没起来就正常走云端，不要因此报错
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1030, e)
        time.sleep(0.5)
    # 探测失败：用上次探到的（可能只是 llama-swap 正在换模型），否则用本地默认名
    fallback = _LOCAL_PROBE.get("model") or "xiaojiao"
    LOG.warning("本地大脑探测失败，仍按 %s 走兜底（llama-swap 可能正在换模型）", fallback)
    _LOCAL_PROBE = {"at": time.time(), "model": fallback, "base": base}
    return fallback


def _llm_targets():
    """本次请求可以试的大脑目标（首选配置 + 云端失败时的本地兜底）。

    云端**连续被拒**时会先"歇一会儿"（熔断 60 秒）：这段时间直接走本地大脑，不再硬打 ——
    人家的免费档有频率限制，越打越全是 401，用户还要干等；歇够了自动放行再试云端。
    """
    primary = {"url": (LLM_BASE or "").rstrip("/") + "/chat/completions",
               "key": LLM_KEY, "model": LLM_MODEL, "local": _is_local_base(LLM_BASE)}
    out = [primary]
    if not primary["local"]:
        _lm = _local_brain_model()
        if _lm:
            out.append({"url": _LOCAL_PROBE.get("base", "http://127.0.0.1:9292/v1") + "/chat/completions",
                        "key": "", "model": _lm, "local": True})
        if time.time() < _CLOUD_BREAK["until"] and len(out) > 1:
            LOG.info("云端大脑处于熔断冷却中（还剩 %.0f 秒），本次直接用本地大脑",
                     _CLOUD_BREAK["until"] - time.time())
            out = out[1:]                     # 冷却期内：只留本地，不再打云端
    return out


def _cloud_break_note(is_local, ok):
    """按"是否本地目标/成功与否"维护云端熔断计数。

    约定：`is_local=True` 表示这次调用的是本地大脑 —— 只要它成了，说明有可用的兜底，
    云端连续失败计数归零（下一条消息会重新试云端）；`is_local=False` 且失败则累加，
    累到 3 次就冷却 60 秒，期间直接用本地大脑（不再浪费对方的频率额度）。
    """
    if is_local:
        if ok:
            _CLOUD_BREAK["fails"] = 0
            _CLOUD_BREAK["until"] = 0.0
        return
    if not ok:
        _CLOUD_BREAK["fails"] += 1
        if _CLOUD_BREAK["fails"] >= 3 and time.time() >= _CLOUD_BREAK["until"]:
            _CLOUD_BREAK["until"] = time.time() + _CLOUD_BREAK["cooldown"]
            LOG.warning("云端大脑连续 %d 次失败 → 熔断 %.0f 秒（改用本地大脑，稍后自动重试云端）",
                        _CLOUD_BREAK["fails"], _CLOUD_BREAK["cooldown"])
    else:
        _CLOUD_BREAK["fails"] = 0
        _CLOUD_BREAK["until"] = 0.0


def _llm_headers(t):
    h = {"Content-Type": "application/json"}
    if t.get("key"):
        h["Authorization"] = "Bearer " + t["key"]
    return h


def _fallback_worthy(status):
    """这些失败值得再试 / 值得换本地大脑（授权/路由/限流/网络），而不是直接放弃。"""
    return status in (400, 401, 402, 403, 404, 429, 500, 502, 503)


def _llm_post(target, payload, timeout=90, tries=4):
    """往某个大脑目标 POST 一次（带重试）。返回 (response 或 None, 最后一次的状态码, 正文)。

    **为什么必须重试**：实测某家网关**同一个 Key、同一个请求**连打 10 次，结果是
    [401, 200, 401, 401, …, 200] —— 三成成功、七成回 "Invalid token"。这是网关偶发拒签，
    不是用户 Key 填错。只试一次：运气不好就回一句"模型调用出错"或直接切本地，白丢成功率。

    **超时不重试**：对方"慢"和"拒"是两回事（实测慢起来一次 100 秒以上）。超时还硬重试
    只会让用户干等好几分钟 —— 直接交给本地大脑顶上，回答先出来。
    """
    import time as _t
    resp = None
    status = None
    for i in range(max(1, tries)):
        try:
            resp = requests.post(target["url"], headers=_llm_headers(target), json=payload, timeout=timeout)
            status = resp.status_code
            if status == 200:
                _llm_stat(True)
                _cloud_break_note(target.get("local"), True)     # 本地成功 → 云端熔断计数归零
                return resp, 200, ""
            if not _fallback_worthy(status):
                _llm_stat(False)
                _cloud_break_note(target.get("local"), False)
                return resp, status, resp.text
            _llm_stat(False)
        except requests.exceptions.Timeout:
            _llm_stat(False)
            _cloud_break_note(target.get("local"), False)
            return None, None, "超时（%ds 内没返回；对方慢，不是被拒）" % timeout
        except Exception as e:
            resp = None
            status = None
            _llm_stat(False)
            if i == tries - 1:
                _cloud_break_note(target.get("local"), False)
                return None, None, "%s: %s" % (type(e).__name__, str(e)[:120])
        if i < tries - 1:
            _t.sleep(0.7 * (i + 1))                 # 0.7s / 1.4s / 2.1s 退避，别把网关打爆
    if resp is None:
        return None, None, "无响应"
    _cloud_break_note(target.get("local"), False)
    return resp, status, resp.text


def _scrub_secret(s):
    """错误信息里可能回显密钥 → 一律打码后再使用。"""
    return re.sub(r"(sk-|ghp_|Bearer\s+)[A-Za-z0-9\-_]{6,}", r"\1***", s or "")


def _llm_error_text(body):
    """把服务端返回的原始错误**收拾成人话**。

    真实缺陷：原来直接把 `{"error":{"code":"","message":"Invalid token (request id:
    20260912…)","type":"AgnesAI_error"}}` 整串糊到聊天里 —— 用户看到的就是"乱码/格式乱了"。
    现在只取一句 message，去掉 request id 这种噪音，并限长。
    """
    s = _scrub_secret(str(body or "")).strip()
    if not s:
        return ""
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                s = str(err.get("message") or err.get("code") or "")
            elif err:
                s = str(err)
            elif j.get("message"):
                s = str(j["message"])
    except Exception:  # noqa: silent-ok — 不是 JSON 就按纯文本处理
        pass
    s = re.sub(r"\s*\((?:request id|request_id)[^)]*\)", "", s)   # 去掉 request id 噪音
    s = re.sub(r"\s+", " ", s).strip(" {}[]\"")
    return s[:80]


def _note_llm_error(tag, status=None, body=""):
    """记下**真实**失败原因：状态码 + 中文解释 + 服务端原话（打码后、收拾成人话）。"""
    global _LAST_LLM_ERROR
    msg = _llm_error_text(body)
    if status is not None:
        hint = {401: "API Key 无效或已过期", 402: "额度不足", 403: "无权访问该模型",
                404: "接口地址或模型名不对", 422: "请求参数不被接受", 429: "触发限流",
                500: "服务端内部错误", 502: "网关错误", 503: "服务暂不可用"}.get(int(status), "")
        _LAST_LLM_ERROR = "HTTP %s%s%s" % (int(status), (" · %s" % hint) if hint else "",
                                           ("（服务端说：%s）" % msg) if msg else "")
    else:
        _LAST_LLM_ERROR = msg or "未知错误（无响应）"
    if _LAST_LLM_ERROR not in _LLM_ERR_LOGGED:
        _LLM_ERR_LOGGED.add(_LAST_LLM_ERROR)
        LOG.warning("大脑调用失败（%s）：HTTP %s ｜ %s ｜ 接口 %s ｜ 模型 %s",
                    tag, status if status is not None else "-", _LAST_LLM_ERROR, LLM_BASE, LLM_MODEL)


def _llm_stat_note():
    """区分"偶发拒签"和"持续被拒"，好让用户知道到底是谁的问题（自己的 Key 还是服务商）。"""
    _rec = _LLM_STAT["recent"]
    _n, _k = len(_rec), sum(_rec)
    if _n < 4:
        return ""
    if _k == 0:
        return ("\n📉 最近 %d 次云端调用**全部被拒** —— 这更像服务商那边的问题（额度/密钥状态/网关），"
                "不是你填错了。建议去 Agnes 控制台看一眼密钥与额度，或过一会儿再试。" % _n)
    if _k < _n:
        return "\n📈 最近 %d 次云端调用成功 %d 次（忽好忽坏＝服务商网关偶发拒签），已自动重试过。" % (_n, _k)
    return ""


def llm_error_suffix():
    """把真实原因 + 一句"该怎么办"拼到给用户看的提示后面（没失败过就什么都不加）。

    还会说清"这是**偶发**还是**持续**"：实测 Agnes 网关同一个 Key 会出现
    [401,200,401,401,200,…] 这种忽好忽坏（/chat 更是连打 10 次全 401）——
    不区分的话，用户只会以为"我 Key 填错了"，然后在配置里瞎改。
    """
    if not _LAST_LLM_ERROR:
        return ""
    tip = "\n👉 怎么办：设置 → 大脑 里换一个可用的 API Key，或直接切「本地大脑」（本地模型不需要 Key）。"
    return "\n\n🔎 真实原因：%s%s%s" % (_LAST_LLM_ERROR, _llm_stat_note(), tip)


def _llm_stat(ok):
    """记录一次云端调用成败（最近 20 次），用于区分偶发/持续失败。"""
    _LLM_STAT["ok" if ok else "fail"] += 1
    _rec = _LLM_STAT["recent"]
    _rec.append(1 if ok else 0)
    del _rec[:-20]


def llm_fallback_note():
    """云端挂了、这次是本地大脑顶上时，回答末尾如实标注一句（别让用户以为是云端答的）。

    **真实缺陷**：这个标志原来只置位不复位 → 一旦某次兜底过，**之后每次回答**都会挂上
    "本次回答由本地大脑完成（ ）"（原因还是空的），用户看着像小焦坏了。现在按请求复位，
    且带上"这一次"的真实原因。
    """
    if not _USED_LOCAL_FALLBACK["on"]:
        return ""
    _why = _USED_LOCAL_FALLBACK.get("reason") or _LAST_LLM_ERROR
    _why = ("（%s）" % _why) if _why else ""
    return ("\n\n---\n\nℹ️ 本次回答由**本地大脑**（%s）完成：你选的云端大脑调用失败%s。%s"
            "要恢复云端：设置 → 大脑 里更新 API Key。"
            % (_USED_LOCAL_FALLBACK["model"], _why, _llm_stat_note()))


def llm_chat(messages):
    """调用 OpenAI 兼容 /chat/completions（云端授权失败会自动兜到本地大脑）。

    失败返回 None（真实原因记进 _LAST_LLM_ERROR）。
    """
    payload = {"messages": messages, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}
    for _t in _llm_targets():
        _p = dict(payload, model=_t["model"])
        resp, code, body = _llm_post(_t, _p, timeout=90)
        if code == 200 and resp is not None:
            if _t["local"] and not _is_local_base(LLM_BASE):
                _USED_LOCAL_FALLBACK.update({"on": True, "model": _t["model"], "reason": _LAST_LLM_ERROR})
            return resp.json()["choices"][0]["message"]["content"].strip()
        _note_llm_error("chat", code, body)
        if code is not None and not _fallback_worthy(code):
            break
    return None


def llm_online():
    """大脑是否在线(本地或外部API)。/health 优先; 外部API可能无/health -> 端口能连通即算在线。"""
    raw = LLM_BASE or ""
    host = raw.split("//")[-1].split("/")[0]  # host:port
    if not host:
        return False
    # 外部 API(engine=api)已配置 -> 直接视为在线(调用端处理真实错误, 不误判未连接)
    if BRAIN_ENGINE == "api":
        return True
    # 1) /health
    try:
        if requests.get("http://" + host + "/health", timeout=3).status_code == 200:
            return True
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 633, e)
    # 2) 端口连通(外部API如deepseek可能无/health, 但端口可达)
    try:
        h, _, pt = host.rpartition(":")
        port = int(pt) if pt else 443
        h = h or host
        import socket
        s = socket.create_connection((h, port), 3)
        s.close()
        return True
    except Exception:
        return False


# ================== 工具（操控电脑，function calling） ==================
import subprocess

TOOLS = [
    {"type": "function", "function": {"name": "check_env", "description": "检测电脑环境装没装东西(只读): 检查 python/git/node/ffmpeg 等是否安装及版本。用于'帮我装环境/配置'场景, 只给建议不执行。",
     "parameters": {"type": "object", "properties": {"items": {"type": "string", "description": "要检测的工具, 逗号分隔"}}, "required": []}}},
    {"type": "function", "function": {"name": "suggest_organize", "description": "整理文件建议(只读): 扫描一个目录, 按类型/日期给出整理到哪里的建议。用于'整理桌面/文件夹'。只给建议清单, 确认才移动。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要整理的目录"}}, "required": ["path"]}}},
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
    {"type": "function", "function": {"name": "edit_file", "description": "在文本文件里精准替换一段内容(第一次出现的)。用于改代码/配置。path=文件, old_string=原文, new_string=新文。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "文件路径"}, "old_string": {"type": "string", "description": "要被替换的原文"}, "new_string": {"type": "string", "description": "替换成的新文"}}, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "search_files", "description": "按文件名模式查找文件(glob)。path=目录, pattern=如 *.txt 或 **/*.py。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要搜索的目录"}, "pattern": {"type": "string", "description": "文件名模式如 *.txt"}}, "required": ["path", "pattern"]}}},
    {"type": "function", "function": {"name": "grep_files", "description": "在目录里的文件中搜索文本/正则内容。path=目录, pattern=关键词或正则。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "要搜索的目录"}, "pattern": {"type": "string", "description": "关键词或正则"}}, "required": ["path", "pattern"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "读取一个网址/接口返回的内容(网页文本)。url=完整地址。",
     "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "网址"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "ask_user", "description": "向用户提问并给出选项，等待用户选择。用于需要用户拍板时。question=问题, options=选项列表(逗号分隔)。",
     "parameters": {"type": "object", "properties": {"question": {"type": "string", "description": "要问的问题"}, "options": {"type": "string", "description": "选项，逗号分隔"}}, "required": ["question"]}}},
    {"type": "function", "function": {"name": "background", "description": "在后台运行一条命令(不阻塞)，立即返回任务id。稍后用 background_result 查结果。用于耗时任务。",
     "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "要后台运行的命令"}, "timeout": {"type": "number", "description": "超时秒数默认120"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "background_result", "description": "查询后台任务(background启动的)的结果。job_id=任务id。",
     "parameters": {"type": "object", "properties": {"job_id": {"type": "string", "description": "后台任务id"}}, "required": ["job_id"]}}},
]


DANGEROUS_CMD = re.compile(r"\b(rm|del|rd|format|shutdown|reboot|mkfs|dd|reg\s+delete|taskkill\s+/f|net\s+user|netsh|icacls|takeown|chkdsk\s+/f|tskill|vssadmin)\b", re.I)
SAFE_ROOT = os.path.abspath(os.getcwd())
PENDING = None          # 待用户确认的危险动作 (name, args)
# 语音模型全局缓存（懒加载）。**必须定义在这里**：以前 `api_voice_warm` 里没写 `global`，
# 模型加载完就丢进局部变量被回收 —— 预热等于白热，而且 `or _asr_model is None` 还可能抛 UnboundLocalError。
_asr_model = None       # Whisper（语音识别）
_tts_model = None       # Chatterbox（语音合成）


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


_BG = {}  # 后台任务


def _bg_run(jid, cmd, timeout):
    try:
        import subprocess as _sp
        r = _sp.run(cmd, shell=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        _BG[jid] = {"state": "done", "result": ((r.stdout or "") + (r.stderr or ""))[:2500] or "(无输出)"}
    except Exception as e:
        _BG[jid] = {"state": "error", "result": str(e)}


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
        if name == "web_search":
            raw_q = str(args.get("query", "") or ""); n = int(args.get("num", 5))
            if not raw_q.strip():
                return SEARCH_KEYWORD_HINT
            _user_text = _CTX.get("user_input", "")
            _user_q, _ = resolve_search_query(_user_text) if _user_text else ("", "")
            # 用户这句话本身就没有可检索内容（"你好"/"用"/"帮我搜一下"），模型却拿其中一个碎片来搜
            # → 直接拒绝，别去搜"好（汉语文字）_百度百科"这种词条（用户实测就是这个现象）。
            if not _user_q and raw_q.strip() and raw_q.strip() in _user_text:
                LOG.warning("用户这句话无可检索内容，拒绝搜索（模型给的词=%r）", raw_q.strip()[:40])
                return "（这句话里没有需要联网查的内容）" + SEARCH_KEYWORD_HINT
            q, hint = resolve_search_query(raw_q)      # 强制清洗：功能字/整句都不许直接拿去搜
            if not q:
                return hint
            q = _better_search_query(q, _user_text)    # 模型只给碎片 → 用整句关键词
            res = web_search(q, num=n)
            _head = ""
            if q != raw_q.strip():                     # 清洗/升级过就如实说明，方便用户核对
                _head = "（已把「%s」清洗成检索关键词「%s」）\n" % (raw_q.strip()[:40], q)
            return _head + ("\n".join("%s%s：%s" % (t, (" [%s]" % u) if u else "", c) for t, u, c in res[:n])
                            or "(无结果)")
        if name == "check_env":
            items = (args.get("items") or "python,git,node,ffmpeg")
            out = []
            for it in items.split(","):
                it = it.strip()
                if not it:
                    continue
                try:
                    # Windows: where 工具 或 --version
                    r = subprocess.run(["where", it], capture_output=True, text=True, timeout=5)
                    ver = ""
                    for vf in ["--version", "-v", "-V"]:
                        try:
                            vr = subprocess.run([it, vf], capture_output=True, text=True, timeout=5)
                            if vr.stdout.strip():
                                ver = vr.stdout.strip().split("\n")[0][:40]; break
                        except Exception as e:
                            LOG.debug("忽略异常(%s:%d): %s", __file__, 752, e)
                    if r.returncode == 0:
                        out.append("✅ %s 已安装%s" % (it, ("，版本: " + ver) if ver else ""))
                    else:
                        out.append("❌ %s 未安装" % it)
                except Exception:
                    out.append("❌ %s 未安装(需下载)" % it)
            return "\n".join(out) + "\n（只做了检测，需要装哪个告诉我，我给下载方案，你确认后执行。）"
        if name == "suggest_organize":
            d = args.get("path", ".")
            if not os.path.isdir(d):
                return "目录不存在: " + d
            by = {}
            for f in os.listdir(d):
                fp = os.path.join(d, f)
                if os.path.isfile(fp):
                    ext = os.path.splitext(f)[1].lower().lstrip(".") or "无后缀"
                    by.setdefault(ext, []).append(f)
            if not by:
                return "该目录没有文件"
            lines = ["📁 建议整理到以下文件夹（只建议，移动前我会先问你确认）："]
            for ext, fs in sorted(by.items(), key=lambda x: -len(x[1]))[:8]:
                lines.append("  - 「%s」→ 放 %s 文件夹（%d 个）" % (ext, ("图片" if ext in ("png","jpg","jpeg","gif","bmp") else "文档" if ext in ("txt","md","doc","docx","pdf") else "视频" if ext in ("mp4","mov","mkv") else ext + "_文件"), len(fs)))
            return "\n".join(lines) + ("\n（只给建议，你确认我才移动文件）")
        if name == "capture_screen":
            try:
                from PIL import ImageGrab
                out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "screen")
                os.makedirs(out_dir, exist_ok=True)
                fp = os.path.join(out_dir, datetime.now().strftime("%Y%m%d%H%M%S") + ".png")
                img = ImageGrab.grab()
                img.save(fp)
                rel = "/media/screen/" + os.path.basename(fp)
                return "已截屏: " + rel + "（分析可看这张图；我无法直接「看图」，你可以描述或让我用OCR读文字）"
            except Exception as e:
                return "截屏失败: " + str(e)[:80]
        if name == "screen_text":
            try:
                from PIL import ImageGrab
                out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media", "screen")
                os.makedirs(out_dir, exist_ok=True)
                fp = os.path.join(out_dir, datetime.now().strftime("%Y%m%d%H%M%S") + ".png")
                ImageGrab.grab().save(fp)
                try:
                    import pytesseract
                    txt = pytesseract.image_to_string(fp, lang="chi_sim+eng")
                    return "屏幕文字: " + (txt.strip()[:1500] or "(未识别到)")
                except Exception:
                    return "已截屏(OCR需另装tesseract): " + fp
            except Exception as e:
                return "截图失败: " + str(e)[:80]
        if name == "run_command":
            cmd = args.get("command", "")
            timeout = int(args.get("timeout", 30))
            if os.name == "nt":
                # PowerShell 才能运行 New-Item 等 cmdlet；强制 UTF-8 输出避免中文乱码
                # 模型常用 bash 语法(&& / ||) -> 转成 PowerShell 顺序执行 ;
                import re as _re
                cmd = _re.sub(r"&&", ";", cmd); cmd = _re.sub(r"\|\|", ";", cmd)
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
        if name == "edit_file":
            p, o, n = args.get("path", ""), args.get("old_string", ""), args.get("new_string", "")
            t = open(p, encoding="utf-8").read()
            if o not in t:
                return "未找到要替换的原文"
            open(p, "w", encoding="utf-8").write(t.replace(o, n, 1))
            return "已替换 %s 中第一处匹配" % p
        if name == "search_files":
            import glob as _g
            p = args.get("path", "."); pat = args.get("pattern", "*")
            r = _g.glob(os.path.join(p, pat), recursive=True)
            return ("\n".join(r[:80]) + ("\n..." if len(r) > 80 else ""))[:2500] or "(无匹配)"
        if name == "grep_files":
            import re as _re
            p = args.get("path", "."); pat = args.get("pattern", "")
            hits = []
            for root, ds, fs in os.walk(p):
                ds[:] = [d for d in ds if d not in ("node_modules", ".git", "__pycache__")]
                for f in fs[:300]:
                    try:
                        for i, l in enumerate(open(os.path.join(root, f), encoding="utf-8", errors="ignore"), 1):
                            if _re.search(pat, l):
                                hits.append("%s:%d: %s" % (os.path.join(root, f), i, l.strip()[:70]))
                                if len(hits) >= 30:
                                    break
                    except Exception as e:
                        LOG.debug("忽略异常(%s:%d): %s", __file__, 863, e)
                    if len(hits) >= 30:
                        break
                if len(hits) >= 30:
                    break
            return ("\n".join(hits))[:2500] or "(无匹配)"
        if name == "fetch_url":
            u = args.get("url", "")
            if not u:
                return "缺少 url"
            import requests as _rq
            try:
                return _rq.get(u, timeout=20).text[:2500] or "(空)"
            except Exception as e:
                return "抓取失败: %s" % str(e)[:120]
        if name == "ask_user":
            q = args.get("question", ""); opts = args.get("options", "")
            return "〔待选择〕" + q + ("\n选项: " + opts if opts else "")
        if name == "background":
            cmd = args.get("command", ""); to = int(args.get("timeout", 120))
            jid = str(int(time.time() * 1000))
            _BG[jid] = {"state": "running"}
            threading.Thread(target=_bg_run, args=(jid, cmd, to), daemon=True).start()
            return "已在后台运行，任务id: %s（用 background_result 查询）" % jid
        if name == "background_result":
            jid = args.get("job_id", "")
            j = _BG.get(jid)
            if not j:
                return "未知任务"
            return "%s: %s" % (j["state"], (j.get("result") or "")[:2000])
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


def llm_chat_tools(messages, max_rounds=6, lean=False):
    """带 function calling 的大脑调用：模型自己“想”并调用工具（优先），循环直到给出最终回答。

    返回 (answer, tool_trace)。兼容 OpenAI tool_calls 与 Qwen <tool_call> XML。

    云端大脑授权失败（401/403/404/429…）时自动兜到**本地大脑**再试一次 —— 免得用户被
    "一句固定的模型调用出错"卡死（真实事故：Agnes Key 失效后整机等于残废）。
    """
    # 内存守卫: 生成前卸载另一个 llama 模型——8G 上保证单个 llama 占满显存(防龟速/OOM)
    try:
        if LLM_MODEL in ("coder", "xiaojiao"):
            import video_service.model_switch as _ms
            _other = "xiaojiao" if LLM_MODEL == "coder" else "coder"
            _ms._llama_swap_unload(_other)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 938, e)
    m = list(messages)
    tool_trace = []
    _targets = _llm_targets()
    _ti = 0                                          # 当前在用哪个大脑目标
    _fail_streak = {"tool": "", "n": 0}              # 同一工具连续失败次数（熔断用）
    for _ in range(max_rounds):
        _t = _targets[_ti]
        payload = {"model": _t["model"], "messages": m, "temperature": TEMPERATURE,
                   "max_tokens": (200 if lean else MAX_TOKENS),
                   "tools": ([] if lean else _build_tools())}
        try:
            r, _code, _body = _llm_post(_t, payload, timeout=120)
            if _code != 200 or r is None:
                _note_llm_error("chat+tools", _code, _body)      # 真实原因必须留痕
                if (_code is None or _fallback_worthy(_code)) and _ti + 1 < len(_targets):
                    _ti += 1                 # 换成下一个目标（通常是本地大脑）再试
                    _targets[_ti]["local"] = True
                    LOG.warning("云端大脑不可用，自动改用本地大脑（%s）继续回答", _targets[_ti]["model"])
                    continue
                return None, tool_trace
            if _t.get("local") and not _is_local_base(LLM_BASE):
                _USED_LOCAL_FALLBACK.update({"on": True, "model": _t["model"], "reason": _LAST_LLM_ERROR})
            msg = r.json()["choices"][0]["message"]
            try:
                _record_usage(r.json().get("usage"), _t["model"])
            except Exception as e:
                LOG.debug("忽略异常(%s:%d): %s", __file__, 958, e)
        except Exception as e:
            _note_llm_error("chat+tools", None, "%s: %s" % (type(e).__name__, e))
            if _ti + 1 < len(_targets):
                _ti += 1
                LOG.warning("云端大脑连不上，自动改用本地大脑（%s）继续回答", _targets[_ti]["model"])
                continue
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
                result = _tool_result_str(run_tool(tname, targs))
                _tripped = _tool_breaker(_fail_streak, tname, result, tool_trace)
                tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
                if _tripped:
                    return _tripped, tool_trace
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
            result = _tool_result_str(run_tool(tname, targs))
            _tripped = _tool_breaker(_fail_streak, tname, result, tool_trace)
            tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
            if _tripped:
                return _tripped, tool_trace
            if result.startswith("〔待确认〕"):
                m.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
                return result, tool_trace
            m.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
    # 循环到上限但已执行工具 -> 用工具结果生成总结(不让用户看到空/报错)
    if tool_trace:
        # 挑一个"成功"的结果最后展示(跳过 路径不存在/失败/Error)
        ok = [t for t in tool_trace if not any(k in (t.get("result") or "") for k in ("路径不存在", "失败", "Error", "error", "不（可用", "not found", "不存在"))]
        last = ok[-1] if ok else tool_trace[-1]
        res = (last.get("result") or "")[:160]
        return ("✅ 已完成「%s」%s" % (last.get("tool"), ("：" + res) if res else "")), tool_trace
    return None, tool_trace


def _tool_failed(result):
    """这次工具调用算不算"失败"（用于熔断计数）。

    只认明确的失败信号：中文失败词 / Error / 校验 FAIL / 异常前缀。
    成功的正文里偶尔也会出现"失败"两字（比如"失败重试机制"），所以要求它出现在前 200 字内。
    """
    head = str(result or "")[:200]
    if not head.strip():
        return True
    for k in ("校验失败", "失败：", "调用失败", "工具执行失败", "插件异常", "异常：",
              "Error", "error:", "Traceback", "不存在", "非法", "禁止", "无效"):
        if k in head:
            return True
    if re.search(r"状态：FAIL|FAIL\b", head):
        return True
    return False


def _tool_breaker(streak, tool, result, tool_trace):
    """同一工具**连续失败**达到 3 次就熔断：返回给用户的错误文本（并标注"已熔断"）。

    真实缺陷（用户实测）：让 Archify 画架构图，`archify_validate` 被反复调用 **7 次** ——
    模型每次只按一条报错改一点、改完再校验，来回烧掉 200 多秒。这里做三层防护：
      · 提示词里要求"所有报错一次改完"（_TOOL_RULES ④）
      · 校验插件一次返回**全部**报错（archify 的 _fmt_validate）
      · 工具循环兜底熔断：连续 3 次失败就停，把最后一次报错原样摆给用户
    同一次调用成功即清零（换个工具也会重新计数）。
    """
    ok = not _tool_failed(result)
    if ok:
        streak["tool"], streak["n"] = "", 0
        return ""
    if streak.get("tool") == tool:
        streak["n"] = streak.get("n", 0) + 1
    else:
        streak["tool"], streak["n"] = tool, 1
    if streak["n"] >= 3:
        LOG.warning("工具 %s 连续 %d 次失败 → 熔断（停止重试，把最后一次报错交给用户）",
                    tool, streak["n"])
        tool_trace.append({"tool": tool, "args": {},
                           "result": "已熔断：连续 %d 次失败，停止重试" % streak["n"]})
        return ("⚠️ **已熔断：%s 连续 %d 次失败，停止重试。**\n\n"
                "最后一次报错原文如下（请据此修正后再说一次，或直接看这条报错）：\n\n```\n%s\n```"
                % (tool, streak["n"], str(result or "").strip()[:1800]))
    return ""


def _workflow_needs_more_rounds(user_input):
    """多步插件工作流（画图/批量/交付类）需要更多轮工具调用。

    真实缺陷（用户实测）：让 Archify 画架构图，它的工作流是 8 步（读技能 → 取指南 → 读 schema
    → 读示例 → 校验 → 交付 → 视觉核对），而工具循环上限只有 6 轮 → 走到一半被截断，
    最后回给用户的竟是"✅ 已完成「archify_read_example」：{…}" 这种中间产物，
    图根本没交付。这里按"点名了多步工具/含工作流关键词"把上限放宽。
    """
    q = (user_input or "").lower()
    if any(k in q for k in ("画图", "画一张", "架构图", "流程图", "时序图", "数据流图", "状态图",
                            "draw", "diagram", "archify")):
        return 14
    return 6


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
    is_file = any(k in ql for k in (".txt", ".py", ".html", ".md", ".json", ".js", "index.", "文件", "file",
                                   "html", "网页", "网站", "页面", "自我介绍", "文档", "代码", "内容", "博客", "h5"))
    is_folder = any(k in ql for k in ("文件夹", "目录", "folder", "dir"))
    if is_create and is_file:
        return "write_file"
    if is_create and is_folder and not is_file:
        return "run_command"
    if any(k in ql for k in ("运行", "执行", "命令", "跑一下", "删掉", "删除", "移动", "复制", "清理", "关机", "格式化", "mkdir", "安装", "卸载", "重启", "启动服务")):
        return "run_command"
    if any(k in ql for k in ("打开", "启动")):
        return "open_app"
    if any(k in ql for k in ("列出", "查看目录", "有哪些文件", "list", "看看有什么")):
        return "list_files"
    if any(k in ql for k in ("读取", "查看文件", "读出", "读文件")):
        return "read_file"
    # 任何"到+某路径"(桌面/文件夹/目录/Downloads) + 做实事动词 -> 视为写文件
    if any(k in ql for k in ("到", "存到", "放", "输入", "打开")) and any(k in ql for k in ("桌面", "文件夹", "目录", "downloads", "路径", "位置", "school")) and is_create:
        return "write_file"
    return None


def _llm_ask_raw(prompt):
    """一次性小提问（给工具结果写总结用）；同样享受"重试 + 云→本地兜底"。"""
    for _t in _llm_targets():
        r, code, _body = _llm_post(_t, {"model": _t["model"],
                                       "messages": [{"role": "user", "content": prompt}],
                                       "temperature": 0.2, "max_tokens": MAX_TOKENS}, timeout=90, tries=2)
        if code == 200 and r is not None:
            try:
                return r.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                LOG.debug("忽略异常(%s:%d): %s", __file__, 1051, e)
    return ""


# ===== 抓取意图直通（scrapling_bridge 插件）=====
# 4B 模型自己不会稳定地选择抓取工具，这里用规则兜底：
# 用户说"抓/爬 + 网址"时，直接构造工具调用交给插件执行，保证"说抓就抓"，不让模型胡编代码。
_SCRAPE_TOOL_HINTS = [
    ("stealthy_fetch", ("隐身", "stealthy", "cloudflare", "绕过防护", "过验证", "被墙")),
    ("fetch", ("浏览器", "渲染", "动态页面", "js渲染", "js 渲染", "登录后", "点开")),
    ("get", ("抓取", "爬取", "爬一下", "抓一下", "抓个", "抓网页", "取网页", "请求网页", "抓取网页")),
]
_SCRAPE_URL_RE = re.compile(r"https?://[^\s，。；、）)\]\"']+")
_SCRAPE_DOMAIN_RE = re.compile(r"\b([a-z0-9][a-z0-9\-]*\.(?:com|cn|org|net|io|dev|gov|edu|ai|co|me|app)"
                               r"(?:/[^\s，。；、）)\]\"']*)?)", re.I)
# 用户显式要求忽略 robots.txt（默认严格遵守，只有明确说了才放行）
_SCRAPE_IGNORE_ROBOTS_HINTS = ("忽略robots", "忽略 robots", "无视robots", "无视 robots",
                               "不管robots", "不管 robots", "不看robots", "跳过robots",
                               "ignore robots")


def _detect_scrape_intent(q):
    """识别"抓网页"意图 → 返回 (工具名, 参数)；识别不到返回 None。

    支持的表达：抓取/爬一下/抓网页 + 网址（可带多个网址 → 自动走批量工具）。
    """
    ql = (q or "").lower()
    tool = None
    for name, kws in _SCRAPE_TOOL_HINTS:
        if any(k.lower() in ql for k in kws):
            tool = name
            break
    if not tool:
        return None
    urls = _SCRAPE_URL_RE.findall(q or "")
    if not urls:
        m = _SCRAPE_DOMAIN_RE.search(q or "")
        if m:
            urls = ["https://" + m.group(1)]
    if not urls:
        return None
    # 用户显式说"忽略 robots / 不管robots / 无视robots" → 本次放行（默认仍严格遵守）
    ignore = any(k in ql for k in _SCRAPE_IGNORE_ROBOTS_HINTS)
    if len(urls) > 1:      # 多个网址 → 批量工具
        bulk = {"get": "bulk_get", "fetch": "bulk_fetch", "stealthy_fetch": "bulk_stealthy_fetch"}[tool]
        args = {"urls": urls[:20]}
        if ignore:
            args["ignore_robots"] = True
        return bulk, args
    args = {"url": urls[0]}
    if ignore:
        args["ignore_robots"] = True
    return tool, args


def _trace_summary(res: str) -> str:
    """把抓取结果压成一行摘要，避免把原始 JSON 塞进工具轨迹（界面好读、4B 也好读）。"""
    try:
        d = json.loads(res)
    except Exception:
        return (res or "")[:160]
    if not isinstance(d, dict):
        return (res or "")[:160]
    if d.get("items"):
        ok = sum(1 for i in d["items"] if not i.get("error"))
        return "批量抓取：成功 %d / 共 %d" % (ok, len(d["items"]))
    if d.get("error"):
        return "失败：" + str(d["error"])[:120]
    _c = d.get("content") or ""
    return "已抓取 %s（HTTP %s，正文 %d 字）" % (d.get("url", ""), d.get("status", ""), len(_c))


def _auto_outline(body: str, limit: int = 6) -> str:
    """规则兜底解读：抽出标题/链接/要点。模型不可用或输出太短时用它，保证用户总有结构可看。"""
    body = body or ""
    lines = []
    for h in re.findall(r"^#{1,3}\s+(.+)$", body, re.M)[:3]:
        lines.append("· 标题：%s" % h.strip()[:60])
    for t, u in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", body)[:3]:
        lines.append("· 链接：%s" % (t.strip()[:40] or u))
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if len(p.strip()) > 20]
    for p in paras[:3]:
        lines.append("· 内容：%s" % p[:80].replace("\n", " "))
    return "\n".join(lines[:limit])


def _fence_body(text: str) -> str:
    """按内容类型给抓取正文套上合适的展示格式。

    JSON → ```json 代码块（前端渲染成带"复制"按钮、限高滚动的代码框）
    含 ``` 的文本 → 原样（避免嵌套围栏把后面的内容吃掉）
    其它（Markdown/HTML→MD）→ 原样，交给 Markdown 渲染
    """
    s = (text or "").strip()
    if not s:
        return s
    if "```" in s:
        return s
    if s[0] in "[{":
        for cand in (s, re.sub(r'\\([^"\\/bfnrtu])', r'\1', s)):   # 兼容 Markdown 转义过的 JSON
            try:
                json.loads(cand)
                return "```json\n" + cand + "\n```"
            except Exception:
                continue
        # 美化后被折叠的 JSON（尾部带了"已折叠"说明，严格解析必然失败）→ 首行是 { 或 [ 就当 JSON 展示
        _first = (s.splitlines() or [""])[0].strip()
        if _first in ("{", "["):
            return "```json\n" + s + "\n```"
    return s


def _explain_content(text: str, url: str = "") -> str:
    """让大脑对抓到的内容做**逐条解读**，帮用户快速看懂含义、快速上手。

    设计意图：抓取只给"原料"，用户（尤其面对陌生网页/英文页）看不懂重点。
    这里让大脑按固定结构讲一遍：这是什么页面 → 关键要点 → 怎么用。
    强调"只依据抓到的内容、不要编造"，避免小模型幻觉；模型不可用则退回规则提纲。
    """
    body = (text or "")[:3000]
    if not body.strip():
        return ""
    prompt = (
        "下面是刚抓取到的网页内容（来源：%s）：\n---\n%s\n---\n\n"
        "请用中文逐条解读，帮用户快速看懂这个页面、知道怎么用：\n"
        "第一行：一句话说明这是什么页面；\n"
        "然后列 3-6 条要点，每条以「· 」开头，简短直白；\n"
        "若页面里有可点的链接或可用的数据，说明它能用来干什么。\n"
        "只根据上面的内容讲，不要编造；总字数不超过 400。" % (url or "网页", body)
    )
    try:
        out = (_llm_ask_raw(prompt) or "").strip()
    except Exception:
        out = ""
    if len(out) < 20:                     # 模型没给出有效解读 → 规则兜底
        out = _auto_outline(text)
    return out


# ===== 【用户使用时学习】小脑从"实际使用"中积累工具经验 =====
# 设计意图：不是从插件代码/文档学，而是**用户每次让小焦干活时**，
# 把"什么需求 → 用了哪个工具 → 参数 → 结果（成功/失败+反思）"沉淀成经验：
#   · 成功 → 记住正确用法，下次同类需求直接照做（命中检索即可复用，不用重新推理）
#   · 失败 → 记住原因与"下次怎么改"，形成反思
# 存两份：可读日志 tool_skills.txt + 向量库（语义检索）。
_SELF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "self_learn")
_SKILL_LOG = os.path.join(_SELF_DIR, "tool_skills.txt")


def _reflect(tool: str, err: str) -> str:
    """失败反思：给出"下次怎么改"，让小脑积累的是经验而不只是报错。"""
    e = err or ""
    if "robots" in e:
        return "遇到 robots 限制要提示用户换站点或说明原因"
    if "SSRF" in e or "禁止访问" in e:
        return "内网/本机地址属安全拦截，直接告诉用户不可抓"
    if "超时" in e or "timeout" in e.lower():
        return "可加大 timeout，或改用更轻的 get"
    if "markdownify" in e:
        return "缺 markdownify 依赖，pip install markdownify"
    if "MCP 未运行" in e:
        return "应先启动 Scrapling（scrapling mcp）"
    if "session_id" in e:
        return "会话类操作要先 action=open 开会话"
    return "下次先检查参数与网络再重试"


def _learn_skill(user_input: str, tool: str, args, ok: bool, detail: str) -> None:
    """把一次工具使用沉淀成小脑的"能力经验"（用户使用时学习）。"""
    if not tool:
        return
    try:
        try:
            _args = json.dumps(args or {}, ensure_ascii=False)[:120]
        except Exception:
            _args = str(args)[:120]
        if ok:
            line = "用户 %s → 小焦用「%s」成功%s：%s" % (
                (user_input or "")[:50], tool, (" 参数" + _args) if _args else "", (detail or "")[:100])
        else:
            line = "用户 %s → 小焦用「%s」失败：%s；经验：%s" % (
                (user_input or "")[:50], tool, (detail or "")[:80], _reflect(tool, detail))
        os.makedirs(_SELF_DIR, exist_ok=True)
        with open(_SKILL_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:                            # 向量库：语义检索命中即可复用
            if _SELF_DIR not in sys.path:
                sys.path.insert(0, _SELF_DIR)
            import vstore
            vstore.add(line, tag="tool_skill")
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1242, e)
    except Exception as e:
        try:
            print("学习沉淀失败(不影响使用): %s" % str(e)[:80])
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1247, e)


def _recall_skills(query: str, k: int = 3) -> str:
    """检索小脑学到的"工具用法"，注入给大脑参考（越用越会）。"""
    try:
        if _SELF_DIR not in sys.path:
            sys.path.insert(0, _SELF_DIR)
        import vstore
        r = vstore.search(query, k=k, threshold=0.12)
        if not r.get("hit"):
            return ""
        return "\n".join("- " + str(t[1])[:120] for t in (r.get("top") or [])[:k])
    except Exception:
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
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1279, e)
    return None, None


# ================== 「别搜 / 直接用工具」闸门 ==================
# 真实缺陷（用户实测截图）：用户说"用 Archify 画一张小焦系统的架构图"，小焦跑去**联网搜「用」字**
# 搜回"用（汉语文字）_百度百科"；用户接着命令"停止搜索。不要搜「用」字。请直接调用 archify_doctor
# 工具" —— 它又把"停止"当检索词搜了一遍。三条规则治它：
#   ① 用户明确说"别搜/停止搜索/不要搜/别联网" → 一次搜索都不发；
#   ② 用户在点名**已加载的工具/插件**（archify_deliver、scrapling、net_ip…）→ 不搜，让工具去干；
#   ③ 点名"某工具"时把它作为直连目标，真去调，而不是嘴上答应。
_SEARCH_OFF_HINTS = ("别搜", "不要搜", "不用搜", "不准搜", "不要搜索", "停止搜索", "停止搜",
                     "别再搜", "不许搜", "不要联网", "别联网", "不用联网", "别查百科",
                     "不要用搜索", "禁止搜索", "不要调用搜索", "直接调用", "直接调",
                     "别再搜索", "no search", "don't search")


def _search_forbidden(text):
    """用户是不是明确要求"别搜了"（命令式）。"""
    q = (text or "").lower()
    return any(h in q for h in _SEARCH_OFF_HINTS)


def _tool_named_options():
    """当前已加载的工具名 + 插件名（供"用户点名了某个工具"识别用）。"""
    names = set()
    try:
        for pname, p in (globals().get("PLUGINS") or {}).items():
            if not p.get("on"):
                continue
            names.add(str(pname).lower())
            for t in (p.get("desc") or []):
                nm = (t or {}).get("name")
                if nm:
                    names.add(str(nm).lower())
    except Exception as e:  # noqa: silent-ok — 识别失败就当没点名
        LOG.debug("忽略异常(%s:%d): %s", __file__, 700, e)
    return names


def _named_tools(text):
    """用户这句话点名了哪些已加载的工具（按名字长度降序，避免 scrapling 命中 scrapling_bridge）。

    为什么需要它：点名工具时**不该联网搜**，而且最好直接调那个工具 ——
    用户实测就是"让 Archify 画图，它去搜『用』字"，报错后还得再骂一句"停止搜索"。
    """
    q = (text or "").lower()
    hit = []
    for n in _tool_named_options():
        if len(n) >= 3 and n in q and "web_search" not in n and "search" not in n:
            hit.append(n)
    hit.sort(key=len, reverse=True)
    # 去掉被更长名字包含的短名（如 scrapling 与 scrapling_bridge 同时命中）
    out = []
    for n in hit:
        if not any(n != m and n in m for m in hit):
            out.append(n)
    return out


def real_tool_names():
    """真正注册在工具表里的名字（`_TOOL2PLUGIN` 的键）。"""
    try:
        _build_tools()
        return list(_TOOL2PLUGIN.keys())
    except Exception:
        return []


def _noarg_named_tool(text):
    """用户点名了一个**不需要参数**的工具 → 直接返回它的名字（让主流程真去调）。

    为什么：用户的原话是"请直接调用 archify_doctor 工具，检查 Archify 环境" —— 既然是
    零参数工具，最稳的做法是**直接调用**并把结果摆出来，而不是再让模型自己决定调不调
    （实测它会转头去联网搜"停止"）。需要参数的工具不在这里抢，交给模型按工作流编排。
    """
    named = _named_tools(text)
    if not named:
        return ""
    try:
        tools = _build_tools()
        for nm in named:
            for t in tools:
                fn = (t or {}).get("function") or {}
                if str(fn.get("name", "")).lower() != nm:
                    continue
                prm = fn.get("parameters") or {}
                props = prm.get("properties") or {}
                req = prm.get("required") or []
                # 零参数，或者参数全是**可选**的（给空 {} 走默认值也安全）→ 可以直接调
                if nm in _TOOL2PLUGIN and not req:
                    return fn["name"]
    except Exception as e:  # noqa: silent-ok — 识别失败就交回模型
        LOG.debug("忽略异常(%s:%d): %s", __file__, 730, e)
    return ""


# ================== 「别搜 / 直接用工具」闸门结束 ==================


def _scrape_direct(user_input, tool_trace):
    """"抓一下 <url>" 这类明确指令的**直通**执行：真调抓取工具，把正文原样给用户。

    抽成函数的原因：这条路径要在**模型之前**（规则先判，稳定）和**模型之后**（兜底）各用一次。
    抓取结果直接给用户看，不让模型"总结"（它会把正文吃掉）。
    返回 (answer 或 None, tool_trace)。
    """
    _sc = _detect_scrape_intent(user_input)
    if not _sc:
        return None, tool_trace
    try:
        _build_tools()                  # 填充 _TOOL2PLUGIN，确保插件工具可被调用
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1538, e)
    _tn, _ta = _sc
    _res = _tool_result_str(run_tool(_tn, _ta, force=True))
    tool_trace = list(tool_trace or []) + [{"tool": _tn, "args": _ta, "result": _trace_summary(_res)}]
    try:
        _jd = json.loads(_res)
        _err = (_jd.get("error") or "").strip()
        _body = (_jd.get("content") or "").strip()
        if _err:
            return "⚠️ 抓取失败：%s" % _err, tool_trace
        if _jd.get("items"):                       # 批量抓取：逐项给正文 + 解读
            _lines = []
            for _idx, _it in enumerate(_jd["items"]):
                _h = "**%s** · HTTP %s" % (_it.get("url"), _it.get("status"))
                if _it.get("error"):
                    _lines.append(_h + "\n\n⚠️ " + str(_it["error"]))
                    continue
                _c = (_it.get("content") or "")[:1500]
                _lines.append(_h + "\n\n" + _fence_body(_c))
                if _idx < 3:                          # 逐条解读（限前 3 条，避免过慢）
                    _e = _explain_content(_c, _it.get("url", ""))
                    if _e:
                        _lines.append("📖 **解读**\n\n" + _e)
            return "🌐 批量抓取完成\n\n" + "\n\n---\n\n".join(_lines), tool_trace
        # 单页：正文 + 解读
        _ans = "🌐 **%s** · HTTP %s\n\n%s" % (
            _jd.get("url", ""), _jd.get("status", ""),
            _fence_body(_body[:4000]) or "(页面无正文)")
        _exp = _explain_content(_body, _jd.get("url", ""))
        if _exp:
            _ans += "\n\n---\n\n📖 **小焦解读**\n\n" + _exp
        return _ans, tool_trace
    except Exception:
        return (_res[:3000], tool_trace)             # 非 JSON 就原样给


def _summarize_tool(user_input, result, tool):
    """让大脑基于工具结果给一句简短总结。"""
    prompt = ("你用 %s 工具执行了用户请求，结果如下：\n%s\n\n"
              "请用一句简短中文告诉用户完成了什么（例如：已在 XXX 创建了 YYY）。不要重复结果内容，不要科普。" % (tool, result[:1200]))
    s = _llm_ask_raw(prompt)
    return s or ("已完成（%s）。" % tool)


def _asks_asset_list(text):
    """用户是不是在问"资产/IP/主机"（NVD 给不了这类数据，必须当面说清）。

    真实缺陷：不管问"抓一下漏洞"还是"把所有含这些漏洞的 IP 列出来"，回复都是同一张 NVD 表，
    用户看到的就是"一直是这个模板，一点没变" —— 因为代码只判了"漏洞意图"，没判"要的是资产清单"。
    """
    ql = (text or "").lower()
    if not ql:
        return False
    if re.search(r"(?<![a-z])ip(?![a-z])", ql):        # ip / 公网ip / IP地址（避开 zip、clip 这类词）
        return True
    if re.search(r"ip\s*地址", ql):
        return True
    if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", ql):   # 直接甩了几个 IP 过来，也算资产诉求
        return True
    return any(k in ql for k in ("主机", "资产", "受影响的机器", "哪些机器", "哪些服务器", "网段"))


def _asset_result_text(res):
    """把工具返回的 JSON/纯文本统一取成正文（失败就给一句中文说明）。

    资产测绘插件与 `net_ip` 都用它：JSON 就取 content/error，纯文本就原样返回。
    """
    try:
        _j = json.loads(res)
        if isinstance(_j, dict):
            return str(_j.get("content") or _j.get("error") or "").strip()
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1521, e)
    return str(res or "").strip()


def _asset_answer(user_input, vuln_table):
    """资产类提问的回答：真的去查资产数据源，查不到就说清差什么、怎么补。

    - 问题里带了 IP → 走 `asset_intel_lookup`（Shodan InternetDB，免费无 Key）真查出
      "这个 IP 命中了哪些 CVE"，并列表对应上；
    - 只给了 CVE/关键词（"全网哪些 IP 受影响"）→ 走 `asset_intel_search`；没配 Key 时
      插件会返回一段**中文可操作**的说明（去哪拿 Key、填哪里、怎么验证），直接给用户看；
    - 最后仍然附上 NVD 漏洞本身，方便先按受影响软件/版本筛查。
    """
    _ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", user_input or "")
    _cves = re.findall(r"CVE-\d{4}-\d{4,7}", user_input or "", re.I)
    try:
        _build_tools()                       # 确保资产插件的三个工具已注册
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1540, e)
    head = ""
    if _ips:
        head = _asset_result_text(_tool_result_str(run_tool(
            "asset_intel_lookup", {"ips": ", ".join(_ips), "cves": ", ".join(_cves)}, force=True)))
    else:
        _q = _cves[0] if _cves else resolve_search_query(user_input)[0]
        if _q:
            head = _asset_result_text(_tool_result_str(run_tool(
                "asset_intel_search", {"query": ("vuln:%s" % _q) if _cves else _q, "limit": 10}, force=True)))
    if not head:
        head = ("⚠️ 资产测绘这一步没返回内容（插件 `plugins/asset_intel.py` 在不在？"
                "对我说「资产测绘状态」可以看各数据源是否可用）。")
    return head + "\n\n---\n\n**这些漏洞本身（NVD 实时数据）**：\n\n" + vuln_table


def _asks_own_ip(text):
    """是不是在问"我的公网 IP / 本机 IP / 你给我显示 IP"（要直连 net_ip 真查）。

    触发条件三条同时成立：① 提到 IP；② 指向自己/当前/对方；③ **没有给出具体 IP**
    （用户给了具体 IP 那是要查那个地址，别抢答）。
    """
    q = text or ""
    return bool(re.search(r"(?<![a-z])ip(?![a-z])|ip\s*地址", q, re.I)
                and re.search(r"(我|我的|本机|自己|当前|这台|这电脑|你)", q)
                and not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", q))


# （_TOOL_RULES 已上移到提示词分层区，见文件顶部「系统提示词的分层」）


# ================== 智能体 ==================
def agent_run(user_input, lean=False):
    """全部问题统一走这条流程：记忆 → 联网检索 → 大脑(小焦模型/外接LLM) → 记忆自学习。
    
    注：不再代理给 DSH 桥接（那会造成 小焦→桥接→小焦 的死循环）。
    DSH 兼容的正确方式是：DSH harness 连小焦的 /v1 当模型，DSH 的插件在 DSH 里自己跑。
    """
    # ===== 原有的 agent_run 逻辑 =====
    _USED_LOCAL_FALLBACK.update({"on": False, "model": "", "reason": ""})   # 每次提问复位兜底标签
    _CTX["user_input"] = user_input          # 工具层要用（判断模型是否只给了碎片检索词）
    history = current_messages()

    # 1. 相关记忆（受操控文件 capabilities 控制）
    mem_text = ""
    if CAP.get("memory", True):
        mem = recall(user_input)
        mem_text = "\n".join(f"- {m['q']}：{m['know'][0]}" for m in mem[:2]) if mem else ""

    # 1b. 漏洞查询直通（结构化数据）：NVD 表格直接给用户看，不让模型再"提取"一遍
    #     真实缺陷防复发：走这条路就不会出现"1999 年数据 / 受影响软件 n/a / 5 条只总结 1 条"。
    answer = None
    tool_trace = []
    if CAP.get("run_tools", True):
        _vq = detect_vulnerability_query(user_input)
        if _vq:
            try:
                _build_tools()          # 填充 _TOOL2PLUGIN，确保插件工具可被调用
            except Exception as e:
                LOG.debug("忽略异常(%s:%d): %s", __file__, 1465, e)
            _vres = _tool_result_str(run_tool("collect_vulnerabilities", _vq, force=True))
            try:
                _vj = json.loads(_vres)
            except Exception:
                _vj = {}
            _vbody = str((_vj or {}).get("content") or "").strip()
            _verr = str((_vj or {}).get("error") or "").strip()
            if not _vbody and not _verr and "未知工具" in _vres:
                LOG.warning("漏洞查询不可用：抓取插件未加载，回退常规检索")
            else:
                _rows = max(0, sum(1 for _l in _vbody.splitlines() if _l.startswith("|")) - 2)
                tool_trace.append({"tool": "collect_vulnerabilities", "args": _vq,
                                   "result": "NVD 漏洞表：%d 行（%s，最近 %d 天）"
                                             % (_rows, _vq["severity"], _vq["days"])})
                answer = _vbody or ("⚠️ 漏洞查询失败：%s" % (_verr or "接口没有返回内容，请稍后重试"))
                if _asks_asset_list(user_input):
                    # **真实缺陷**：用户问的是"含这些漏洞的 IP / 主机 / 资产"，而这条路只会
                    # 把同一张 NVD 表原样吐回去 —— 于是不管怎么问，看到的都是"那张表，一点没变"。
                    # NVD 根本没有 IP 数据。现在：真去查资产数据源（插件 asset_intel），
                    # 查得到就给「IP ↔ CVE」对应表；查不到（没配 Key）就说清差什么、怎么配。
                    answer = _asset_answer(user_input, _vbody)

    # 1b2. 资产测绘"状态/数据源"类提问 → 直通插件（4B 模型不会自己选这个工具，
    #      实测问"资产测绘状态"它自己写了一篇科普，用户要的是"哪个数据源能用"）。
    if CAP.get("run_tools", True) and answer is None and re.search(
            r"资产测绘|数据源|测绘状态|asset", (user_input or ""), re.I):
        try:
            _build_tools()
            _ares = _asset_result_text(_tool_result_str(run_tool("asset_intel_status", {}, force=True)))
            if _ares:
                tool_trace.append({"tool": "asset_intel_status", "args": {},
                                   "result": "资产测绘数据源状态"})
                answer = _ares
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1520, e)

    # 1b3. 问"现在几点/今天几号" → 直接用真实时间回答，不交给模型
    #      真实缺陷：这一步原来靠模型自己答，实测它会**编日期**（答"2025 年 1 月 13 日"），
    #      或者干脆说"我无法获取实时时间"。时间是最不该猜的东西，规则直答最稳。
    if answer is None and re.search(r"(现在|当前|今天|此刻).{0,4}(几点|时间|日期|几号|星期|礼拜)|"
                                    r"(几点|几号|星期几|what time|current time)", user_input or "", re.I):
        _now = datetime.now()
        _wd = "一二三四五六日"[_now.weekday()]
        answer = ("🕐 现在是 **%s**（%s，星期%s）\n\n- 北京时间（本机时区）：%s"
                  % (_now.strftime("%Y-%m-%d %H:%M:%S"), _now.strftime("%A"),
                     _wd, _now.strftime("%Y-%m-%d %H:%M:%S")))
        tool_trace.append({"tool": "now", "args": {}, "result": "系统时钟直答"})

    # 1b4. 问"我的公网 IP / 本机 IP / 你给我显示 IP" → 直接调 net_ip 摆出**真实结果**
    #      真实缺陷（用户实测）：这一步原来交给模型 → 它嘴上说"我通过 net_ip 查了"，
    #      内容却是模板占位符（`IP 地址: [查询结果]`）；换个问法（"你现在可以显示IP了吗"）
    #      它甚至**编了一个 IP**（103.152.24.108）。"查出来的东西"最不该由模型转述。
    #      触发条件：提到 IP ＋ 指向自己/当前 ＋ **没给具体 IP**（给了具体 IP 是要查那个 IP，别抢）。
    if answer is None and _asks_own_ip(user_input):
        try:
            _build_tools()
            _ipres = _asset_result_text(_tool_result_str(run_tool("net_ip", {}, force=True)))
            if _ipres and "未知工具" not in _ipres:
                tool_trace.append({"tool": "net_ip", "args": {}, "result": "公网 IP 查询（插件直答）"})
                answer = "🌐 **我的公网 IP（net_ip 实测）**\n\n```\n%s\n```" % _ipres.strip()
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1560, e)

    # 1b5. 用户点名了"零参数工具"（如 archify_doctor / 资产测绘状态）→ 直接调它
    #      真实缺陷（用户实测）：说"请直接调用 archify_doctor 工具"，小焦却把"停止"拿去联网搜。
    if answer is None and CAP.get("run_tools", True):
        _nt = _noarg_named_tool(user_input)
        if _nt:
            try:
                _res = _asset_result_text(_tool_result_str(run_tool(_nt, {}, force=True)))
                if _res and "未知工具" not in _res:
                    tool_trace.append({"tool": _nt, "args": {}, "result": "用户点名，直接调用"})
                    answer = "🔧 **%s** 实测结果：\n\n```\n%s\n```" % (_nt, _res.strip()[:2500])
            except Exception as e:
                LOG.debug("忽略异常(%s:%d): %s", __file__, 1570, e)

    # 2. 联网检索（受操控文件 capabilities 控制）
    #    检索词必须先过闸门：整句/功能字一律清洗，清洗后为空就干脆不搜（不再拿"用"去搜百科）。
    #    另有两道硬闸：用户说"别搜/停止搜索"，或点名了已加载的工具 → 一次搜索都不发。
    info = []
    _named = _named_tools(user_input)
    if CAP.get("web_search", True) and answer is None:
        if _search_forbidden(user_input):
            LOG.info("用户明确要求别搜，跳过自动检索：%s", (user_input or "")[:40])
        elif _named:
            LOG.info("用户点名了工具 %s，跳过自动检索（直接走工具）", _named[:3])
        else:
            _q, _qhint = resolve_search_query(user_input)
            if _q:
                info = web_search(_q, num=5)
            elif _qhint:
                LOG.info("跳过自动检索：%s", _qhint)
    web_text = "\n".join((f"{t}：{c}" if len(t)==3 else f"{t}：{c}") for t, c in [ (x[0],x[2]) for x in info[:4] ]) if info else ""

    # 3. 大脑回答：遵循操控文件的 brain.engine
    want_llm = llm_online() if BRAIN_ENGINE == "auto" else (BRAIN_ENGINE in ("llama", "api"))
    has_llm = want_llm and llm_online()

    # ②b 抓取类意图**直通**（"抓一下 <url>" 这种明确指令，规则先判，别让模型自己挑工具）
    #     真实缺陷（本轮实测）：这句话原来交给模型，云端模型有时挑 `fetch`、有时挑
    #     `fetch_url` 连调五六次，回答里还不带代码块 —— 同一句话每次结果不一样，
    #     JSON 展示因此时好时坏。规则能判的（用户点名了动作 + 给了 URL）就规则直通，
    #     稳定、快、也不需要模型。下面 ② 里仍保留同一段兜底。
    if answer is None and CAP.get("run_tools", True) and _detect_scrape_intent(user_input):
        answer, tool_trace = _scrape_direct(user_input, tool_trace)

    # ① 优先让大模型自己“想”并调用工具（原生 function calling / <tool_call> XML）
    if has_llm and answer is None:
        home = os.path.expanduser("~")
        desktop = os.path.join(home, "Desktop")
        path_ctx = ("\n[环境] 当前时间：%s（本地时间，回答「现在几点/今天几号」必须用它，不要自己猜）；"
                    "当前工作目录：%s；用户主目录：%s；桌面：%s。"
                    "凡是要创建文件/文件夹/读写文件，一律用绝对路径（如桌面文件用 %s\\文件名）。"
                    % (time.strftime("%Y-%m-%d %H:%M:%S %A"), os.getcwd(), home, desktop, desktop))
        # 运行时只再补两样：工具用法细则 + .md 技能文档（规则与清单已在 SYSTEM_PROMPT 里，
        # 这里绝不能再拼一遍 _TOOL_RULES，否则提示词白涨一大截）
        tool_guidance = "\n[工具用法] 写文件/建网站/代码用 write_file(路径用 Windows 绝对路径, 会自动建目录); 查信息/运行命令用 run_command(PowerShell 语法, 不能用并字连接命令要用分号; 不要用 run_command 去写文件)。\n"
        skills = "\n\n[技能插件] " + "\n\n".join(c for _, c in PLUGIN_SKILLS) if PLUGIN_SKILLS else ""
        skills = tool_guidance + skills
        if lean:
            # 语音精简模式: 短提示, 不背工具/技能, 生成快
            messages = [{"role": "system", "content": (SYSTEM_PROMPT[:240] + "\n[语音对话] 请简短、口语化、直接回答，一两句话；不要调用工具、不要长篇大论、不要列表。")}]
        else:
            messages = [{"role": "system", "content": SYSTEM_PROMPT + path_ctx + skills}]
        for h in history[-MAX_HISTORY:]:
            messages.append({"role": "user" if h["role"] == "用户" else "assistant",
                             "content": h["content"]})
        context = ""
        if mem_text:
            context += "（相关记忆）\n" + mem_text + "\n\n"
        if web_text:
            context += "（联网检索到的资料）\n" + web_text + "\n\n"
        _skills = _recall_skills(user_input)      # 小脑从过去"实际使用"里学到的工具经验
        if _skills:
            context += "（小脑学到的工具用法，可参考）\n" + _skills + "\n\n"
        messages.append({"role": "user", "content": (context + "用户：" + user_input) if context else user_input})
        if CAP.get("run_tools", True):
            answer, tool_trace = llm_chat_tools(messages, lean=lean,
                                                max_rounds=_workflow_needs_more_rounds(user_input))
        else:
            answer = llm_chat(messages)

    # ② 兜底：抓取类意图直通（模型没自己调工具时走这条）
    if not tool_trace and CAP.get("run_tools", True) and has_llm:
        if _detect_scrape_intent(user_input):
            answer, tool_trace = _scrape_direct(user_input, tool_trace)

    # ②b 兜底：其它"执行类操作" → 用 plan 强制生成一次工具调用
    if not tool_trace and CAP.get("run_tools", True) and has_llm:
        it = detect_tool_intent(user_input)
        if it:
            tname, targs = plan_tool(user_input)
            if tname:
                tname, targs = _map_tool(tname, targs)
                result = _tool_result_str(run_tool(tname, targs, force=True))
                tool_trace.append({"tool": tname, "args": targs, "result": result[:800]})
                answer = _summarize_tool(user_input, result, tname)

    # ③ 大模型不在线但有执行类操作 → 明确提示，不胡诌
    if not has_llm and answer is None and CAP.get("run_tools", True):
        it = detect_tool_intent(user_input)
        if it:
            answer = ("⚠️ 需要执行工具操作「%s」，但当前没有可用的智能大脑（本地大模型未启动）。"
                      "请先运行 `python start_xiaojiao.py` 启动大模型，我再帮你真正执行。" % it)
            tool_trace = [{"tool": it, "args": {}, "result": "未执行：大模型未在线"}]

    # —— 注意：已停用自建小模型的语言生成（只会胡诌），绝不用于说话 ——

    # 4. 记忆自学习沉淀
    learned = [c for _, _, c in info[:3]]
    if learned and CAP.get("memory", True):
        remember(user_input, learned)

    # 4b. 【用户使用时学习】把这次用到的工具经验沉淀进小脑（成功记用法、失败记反思）
    try:
        for _t in (tool_trace or []):
            _r = str(_t.get("result") or "")
            _bad = any(k in _r for k in ("失败", "错误", "Error", "error", "禁止", "超时", "不可用"))
            _learn_skill(user_input, _t.get("tool", ""), _t.get("args"), not _bad, _r)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1427, e)

    # 5. 落地上下文（顺手剥掉模型偶尔吐出的 <think> 思维标签，别让标签进聊天记录）
    if answer:
        answer = _strip_think(answer)
        _fb = llm_fallback_note()                # 云端挂了、本地顶上 → 回答里如实标注
        if _fb and not any(k in answer for k in ("本地大脑", "本地兜底")):
            answer += _fb
        needs_confirm = PENDING is not None and answer.startswith("〔待确认〕")
        return answer, True, info, needs_confirm, tool_trace

    # 6. 无任何可用大脑（本地大模型未连接）时的降级（只给一句简洁提示，不瞎输出联网内容）
    fallback = ("🤖 大脑没有应答，这一问没答上。请确认已选中的模型（本地大脑/API）配置正确、端口可达。"
                + llm_error_suffix())
    return fallback, False, [], False, []




app = Flask(__name__)


@app.after_request
def _no_cache(resp):
    """禁用浏览器缓存: 改了前端代码, 刷新即拿到最新(不用Ctrl+F5清缓存)。"""
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ================== Agent 预设切换（presets/） ==================
_PRESETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")

# 插件生成失败时的**兜底模板**（真实缺陷：这里原来写的是未定义变量 TPL，
# 于是"模型生成失败 → 给你一个模板"这条兜底路径一进去就 NameError 500）。
# 模板契约与 /api/plugin/generate 的提示词一致：get_tool_descriptions() + execute() + get_plugin()。
_PLUGIN_TPL = '''# -*- coding: utf-8 -*-
"""{desc_short} —— 小焦插件模板（自动生成）

改法：把 execute() 里的 TODO 换成你的真实逻辑，重启小焦即生效。
"""
from __future__ import annotations

from typing import Any, Dict, List


class MyPlugin:
    """插件契约：get_tool_descriptions() 声明工具，execute(tool_name, params) 执行。"""

    def get_tool_descriptions(self) -> List[Dict[str, Any]]:
        return [{{
            "name": "{tname}",
            "description": "TODO：一句话说明这个工具干什么（≤60 字，4B 模型才好选）",
            "parameters": {{"type": "object",
                           "properties": {{"text": {{"type": "string", "description": "text: 输入"}}}},
                           "required": []}},
        }}]

    def execute(self, tool_name: str, params: Dict[str, Any]) -> str:
        params = params or {{}}
        if tool_name != "{tname}":
            return "未知工具：%s" % tool_name
        text = str(params.get("text") or "").strip()
        # TODO: 在这里实现你的逻辑；异常请自己接住并返回**中文可读**的说明
        return "收到：%s（模板占位，请在 plugins/{fname} 里实现）" % (text or "（空）")


def get_plugin():
    return MyPlugin()
'''


def _list_preset_files():
    if not os.path.isdir(_PRESETS_DIR):
        return []
    return sorted([f for f in os.listdir(_PRESETS_DIR) if f.endswith(".json")])


@app.route("/pet")
def api_pet():
    """猫娘桌面伙伴：重定向到 N.E.K.O. 猫娘(48911)。"""
    return redirect("http://127.0.0.1:48911")




@app.route("/api/voice/warm", methods=["POST"])
def api_voice_warm():
    """语音通话预热: 启动即加载 聊天脑(4B) + 识别(whisper) + 发声(chatterbox) 到内存, 保证首次交互快。
    用户主要用语音, 所以语音优先; 切到别的才换。"""
    global _asr_model, _tts_model          # 不声明 global 的话，模型会被丢进局部变量、预热白做
    import os as _os
    warm = {"chat": False, "asr": False, "tts": False, "err": ""}
    # ① 聊天脑 4B 加载(llama-swap 预热, 保持加载不卸载)
    try:
        import requests as _r
        _r.post(LLM_BASE.rstrip("/") + "/chat/completions",
                json={"model": LLM_MODEL, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 1}, timeout=60)
        warm["chat"] = True
    except Exception as e:
        warm["err"] = "chat:" + str(e)[:40]
    # ② 识别 whisper 加载
    try:
        _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from faster_whisper import WhisperModel
        if _asr_model is None:
            _asr_model = WhisperModel("base", device="cpu", compute_type="int8")
        warm["asr"] = True
    except Exception as e:
        warm["err"] += " asr:" + str(e)[:40]
    # ③ 发声 chatterbox 加载(若已装)
    try:
        import perth as _perth
        if getattr(_perth, "PerthImplicitWatermarker", None) is None:
            _perth.PerthImplicitWatermarker = _perth.DummyWatermarker
        from chatterbox import ChatterboxTTS
        from pathlib import Path
        _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        if _tts_model is None:
            _mdir = _find_tts_model_dir()
            _tts_model = ChatterboxTTS.from_local(Path(_mdir), device="cuda") if _mdir else ChatterboxTTS.from_pretrained(device="cuda")
        warm["tts"] = True
    except Exception as e:
        warm["err"] += " tts:" + str(e)[:40]
    return jsonify({"ok": warm["chat"] or warm["asr"], "warm": warm})


@app.route("/api/asr", methods=["POST"])
def api_asr():
    """离线语音识别(听): faster-whisper, 接收音频, 返回文字。本地离线, 国内可用。"""
    import tempfile
    global _asr_model
    f = request.files.get("audio")
    if not f:
        return jsonify({"ok": False, "error": "缺少 audio"}), 400
    try:
        import os as _osenv
        _osenv.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 国内镜像, 下模型不走外网
        from faster_whisper import WhisperModel
        if _asr_model is None:
            _asr_model = WhisperModel("base", device="cpu", compute_type="int8")  # CPU避开cuBLAS12缺失; 短句够快
        tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        f.save(tmp.name)
        segments, _info = _asr_model.transcribe(tmp.name, language="zh", beam_size=5)
        text = "".join(seg.text for seg in segments).strip()
        try:
            os.remove(tmp.name)
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1531, e)
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        return jsonify({"ok": False, "error": "识别失败: " + str(e)[:120]}), 500


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """文字转语音(自然): 用 Chatterbox(顶级开源) 生成 wav, 返回音频URL。缺库则提示安装。"""
    import os as _os, datetime as _dt
    d = request.get_json(force=True, silent=True) or {}
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "缺少 text"}), 400
    try:
        import chatterbox
    except ImportError:
        return jsonify({"ok": False, "error": "需 pip install chatterbox-tts torchaudio", "need_install": True})
    try:
        global _tts_model
        if _tts_model is None:
            import perth as _perth
            if getattr(_perth, "PerthImplicitWatermarker", None) is None:
                _perth.PerthImplicitWatermarker = _perth.DummyWatermarker  # 用水印占位, 跳过模型, 照常出音
            from chatterbox import ChatterboxTTS
            from pathlib import Path
            _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            _mdir = _find_tts_model_dir()
            if _mdir:
                _tts_model = ChatterboxTTS.from_local(Path(_mdir), device="cuda")
            else:
                _tts_model = ChatterboxTTS.from_pretrained(device="cuda")
        wav = _tts_model.generate(text)
        out_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "media", "tts")
        _os.makedirs(out_dir, exist_ok=True)
        out = _os.path.join(out_dir, _dt.datetime.now().strftime("%Y%m%d%H%M%S") + ".wav")
        import torchaudio
        torchaudio.save(out, wav.cpu(), _tts_model.sr)
        rel = "/media/tts/" + _os.path.basename(out)
        return jsonify({"ok": True, "url": rel, "text": text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:160]}), 500


@app.route("/cost")
def api_cost_page():
    """成本看板页: 今日调用/花费/节省 + 历史表。"""
    _c = api_cost().get_json()
    days = _c.get("days", {})
    rows = ""
    for d, v in sorted(days.items(), reverse=True)[:7]:
        rows += "<tr><td>" + str(d) + "</td><td>" + str(v.get("calls", 0)) + "</td><td>" + str(v.get("local_tokens", 0)) + "</td><td>" + str(v.get("cloud_tokens", 0)) + "</td><td>¥" + "%.4f" % v.get("cost", 0.0) + "</td><td>¥" + "%.4f" % v.get("saved", 0.0) + "</td></tr>"
    if not rows:
        rows = "<tr><td colspan='6' class='think'>还没有记录，聊几句就有了</td></tr>"
    css = "<style>*{box-sizing:border-box}body{margin:0;font-family:Segoe UI,sans-serif;background:#0e1116;color:#e8ebf3;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}.w{max-width:720px;width:100%}h1{color:#e8ebf3;margin:0 0 6px}.sub{color:#8b93a3;font-size:14px;margin-bottom:18px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:18px}.card{background:#151a26;border:1px solid #2a3140;border-radius:14px;padding:18px;text-align:center}.card .n{font-size:30px;font-weight:800;color:#45d483}.card .t{font-size:12px;color:#8b93a3;margin-top:6px}.card.cloud .n{color:#a78bfa}.card.calls .n{color:#5b5ff5}table{width:100%;border-collapse:collapse;background:#151a26;border:1px solid #2a3140;border-radius:14px;overflow:hidden}th,td{padding:10px 12px;border-bottom:1px solid #1a2030;font-size:13px;text-align:center;color:#cbd0dc}th{background:#1a2233;color:#8b93a3}.think{color:#6e7681;text-align:center;padding:20px}a{color:#a78bfa;text-decoration:none}.back{display:inline-block;margin-top:16px;font-size:13px}</style>"
    html = ('<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>成本看板</title>' + css + '</head><body><div class="w">'
            + '<h1>小焦成本看板</h1><div class="sub">本地免费 · 云端按量 · 智能调度省钱</div>'
            + '<div class="cards"><div class="card calls"><div class="n">' + str(_c.get("calls", 0)) + '</div><div class="t">今日调用</div></div>'
            + '<div class="card"><div class="n">¥' + ("%.4f" % _c.get("cost", 0.0)) + '</div><div class="t">今日花费</div></div>'
            + '<div class="card cloud"><div class="n">¥' + ("%.4f" % _c.get("saved", 0.0)) + '</div><div class="t">今日节省</div></div></div>'
            + '<table><tr><th>日期</th><th>调用数</th><th>本地token</th><th>云端token</th><th>花费</th><th>节省</th></tr>' + rows + '</table>'
            + '<a class="back" href="/">回到小焦</a></div></body></html>')
    return html

@app.route("/api/screen")
def api_screen():
    """一键截屏: 返回截图URL(宠物/网页可显示)。用于看屏幕/看报错。"""
    import os as _o
    from PIL import ImageGrab
    try:
        out_dir = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "media", "screen")
        _o.makedirs(out_dir, exist_ok=True)
        fp = _o.path.join(out_dir, datetime.now().strftime("%Y%m%d%H%M%S") + ".png")
        ImageGrab.grab().save(fp)
        rel = "/media/screen/" + _o.path.basename(fp)
        return jsonify({"ok": True, "url": rel})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:100]}), 500


@app.route("/api/vision")
def api_vision():
    """截图并让视觉模型「看懂」屏幕(用于看报错/界面)。视觉模型走配置(env XIAOJIAO_VISION_URL/MODEL), 未配则OCR兜底。"""
    import os as _o, subprocess as _sp
    from PIL import ImageGrab
    try:
        root = _o.path.dirname(_o.path.abspath(__file__))
        out_dir = _o.path.join(root, "media", "screen")
        _o.makedirs(out_dir, exist_ok=True)
        fp = _o.path.join(out_dir, datetime.now().strftime("%Y%m%d%H%M%S") + ".png")
        ImageGrab.grab().save(fp)
        rel = "/media/screen/" + _o.path.basename(fp)
    except Exception as e:
        return jsonify({"ok": False, "error": "截屏失败: " + str(e)[:80]}), 500
    q = (request.args.get("q") or "描述一下这个屏幕画面，重点看有没有报错/错误信息/安装界面")
    vurl = _o.environ.get("XIAOJIAO_VISION_URL", "")
    vmodel = _o.environ.get("XIAOJIAO_VISION_MODEL", "")
    if vurl:
        # 有视觉模型: 截图base64发给它
        try:
            import base64
            b64 = base64.b64encode(open(fp, "rb").read()).decode()
            import requests as _rq
            rr = _rq.post(vurl + "/chat/completions", json={
                "model": vmodel or "qwen2.5-vl",
                "messages": [{"role": "user", "content": [{"type": "text", "text": q},
                                                          {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
                "max_tokens": 600}, timeout=120)
            ans = rr.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            return jsonify({"ok": True, "url": rel, "desc": (ans or "").strip()[:900], "engine": "vlm"})
        except Exception as e:
            return jsonify({"ok": True, "url": rel, "desc": "视觉模型分析失败: " + str(e)[:100], "engine": "vlm-fail"})
    # OCR 兜底
    try:
        import pytesseract
        txt = pytesseract.image_to_string(fp, lang="chi_sim+eng").strip()
        return jsonify({"ok": True, "url": rel, "desc": ("屏幕文字: " + (txt[:900] or "(未识别到)"))[:900], "engine": "ocr"})
    except Exception:
        return jsonify({"ok": True, "url": rel, "desc": "已保存截图(未装视觉模型/OCR，tesseract 或 Qwen2.5-VL 可启用真看图)", "engine": "none"})


@app.route("/api/cost")
def api_cost():
    """当日成本看板: 调用数/本地token/云端token/花费/相比全云端节省。"""
    today = datetime.now().strftime("%Y-%m-%d")
    d = {}
    if os.path.exists(_COST_FILE):
        try:
            d = json.load(open(_COST_FILE, encoding="utf-8"))
        except Exception:
            d = {}
    day = d.get(today, {"calls": 0, "local_tokens": 0, "cloud_tokens": 0, "cost": 0.0})
    # 节省 = 若全部走云端(基线价) - 实际花费
    all_tokens = day.get("local_tokens", 0) + day.get("cloud_tokens", 0)
    saved = (all_tokens / 1000.0) * (_CLOUD_IN + _CLOUD_OUT) / 2.0 - day.get("cost", 0.0)
    return jsonify({"date": today, "calls": day.get("calls", 0),
                    "local_tokens": day.get("local_tokens", 0), "cloud_tokens": day.get("cloud_tokens", 0),
                    "cost": round(day.get("cost", 0.0), 4), "saved": round(max(saved, 0.0), 4),
                    "days": d})


@app.route("/api/plugin/generate", methods=["POST"])
def api_plugin_generate():
    """自然语言造插件: 说需求 -> 用编码大脑生成插件代码 -> 自动注册即用。失败给模板。"""
    import ast as _ast
    d = request.get_json(force=True, silent=True) or {}
    desc = (d.get("description") or "").strip()
    name = (d.get("name") or "myplugin").strip().lower().replace(" ", "_")
    if not desc:
        return jsonify({"ok": False, "error": "缺少描述"}), 400
    if not name.endswith(".py"):
        name = name + ("" if name.endswith("_plugin") else "_plugin")
    tname = name.replace(".py", "") + "_run"
    prompt = ("你是小焦插件生成器。为需求生成 Python 插件 {file}.py。要求: 1) class MyPlugin 有 get_tool_descriptions() 返回 [{{\"name\":\"{tn}\",...}}]; 2) execute(tool_name,params) 实现; 3) 末尾 def get_plugin(): return MyPlugin(). 只输出完整Python代码。\n需求: {desc}").format(file=name, tn=tname, desc=desc)
    code = ""
    try:
        import requests as _rq
        rr = _rq.post(LLM_BASE.rstrip("/") + "/chat/completions",
                     json={"model": "coder", "messages": [{"role": "system", "content": "你只输出 python 代码"}, {"role": "user", "content": prompt}],
                           "max_tokens": 1600, "temperature": 0.3}, timeout=180)
        code = (rr.json().get("choices", [{}])[0].get("message", {}).get("content") or "").replace("```python", "").replace("```", "").strip()
        if not (code and "get_tool_descriptions" in code and "get_plugin" in code):
            code = ""
        else:
            _ast.parse(code)
    except Exception:
        code = ""
    if not code:
        code = _PLUGIN_TPL.format(fname=name, tname=tname, desc_short=desc[:40])
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins", name)
    try:
        open(fp, "w", encoding="utf-8").write(code)
    except Exception as e:
        return jsonify({"ok": False, "error": "写插件失败: " + str(e)[:80]}), 500
    try:
        global PLUGINS
        PLUGINS = load_plugins()
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1709, e)
    return jsonify({"ok": True, "name": name.split(".py")[0], "file": "plugins/" + name, "note": "已生成并注册，设置->插件 可开关"})


@app.route("/api/presets")
def api_presets():
    """列出所有预设。

    `current` 必须回**文件名**：前端的下拉选项 value 就是文件名（`编程助手.json`），
    而这里原来直接回 `CONTROL["preset"]`（那是**显示名** `编程助手`）—— 名字对不上
    `sel.value` 就设不进去，下拉于是回落到占位项"🎭 预设"，用户看到的就是
    "选好的预设名字不见了"。所以这里做一次「显示名 → 文件名」的换算，并额外带上
    `current_name` 供界面显示。
    """
    res = []
    for f in _list_preset_files():
        try:
            d = json.load(open(os.path.join(_PRESETS_DIR, f), encoding="utf-8-sig"))
            res.append({"name": d.get("name", f[:-5]), "file": f, "engine": d.get("brain", {}).get("engine", "auto"),
                        "desc": d.get("desc") or (d.get("role") or "")[:70]})
        except Exception:
            res.append({"name": f[:-5], "file": f, "engine": "?"})
    cur = CONTROL.get("preset", "") or ""
    cur_file = cur if cur.endswith(".json") else ""
    cur_name = ""
    for p in res:
        if cur_file and p["file"] == cur_file:
            cur_name = p["name"]
            break
        if not cur_file and cur and p["name"] == cur:
            cur_file, cur_name = p["file"], p["name"]
            break
    return jsonify({"presets": res, "current": cur_file, "current_name": cur_name})


@app.route("/api/presets", methods=["POST"])
def api_presets_create():
    """新建自定义预设(复制 parent 改名)。"""
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "我的预设").strip()
    parent = d.get("parent", "default.json")
    pf = os.path.join(_PRESETS_DIR, parent)
    base = {}
    if os.path.exists(pf):
        try:
            base = json.load(open(pf, encoding="utf-8-sig"))
        except Exception:
            base = {}
    base["name"] = name
    base["desc"] = "自定义预设（可到 presets/ 编辑）"
    import uuid
    fn = "preset_%s.json" % uuid.uuid4().hex[:6]
    json.dump(base, open(os.path.join(_PRESETS_DIR, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return jsonify({"ok": True, "file": fn, "name": name})


@app.route("/api/presets/delete", methods=["POST"])
def api_presets_delete():
    """删除预设文件。"""
    d = request.get_json(force=True, silent=True) or {}
    file = d.get("file", "")
    if not file.endswith(".json"):
        file += ".json"
    fp = os.path.join(_PRESETS_DIR, file)
    if os.path.exists(fp):
        os.remove(fp)
        return jsonify({"ok": True, "file": file})
    return jsonify({"ok": False, "error": "不存在"}), 404


@app.route("/api/presets/detail")
def api_presets_detail():
    """读取单个预设内容(供 Web 编辑)。"""
    file = request.args.get("file", "")
    if not file.endswith(".json"):
        file += ".json"
    fp = os.path.join(_PRESETS_DIR, file)
    if not os.path.exists(fp):
        return jsonify({"ok": False, "error": "不存在"}), 404
    d = json.load(open(fp, encoding="utf-8-sig"))
    return jsonify({"ok": True, "file": file, "data": d})


@app.route("/api/presets/save", methods=["POST"])
def api_presets_save():
    """保存编辑后的预设(写回文件; 若为当前预设则热更新)。"""
    d = request.get_json(force=True, silent=True) or {}
    file = d.get("file", "")
    if not file.endswith(".json"):
        file += ".json"
    fp = os.path.join(_PRESETS_DIR, file)
    data = d.get("data", {})
    try:
        old = json.load(open(fp, encoding="utf-8-sig")) if os.path.exists(fp) else {}
    except Exception:
        old = {}
    # 只覆盖提供的键(保留未提供的)
    for k, v in data.items():
        old[k] = v
    json.dump(old, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 若保存的是当前预设 → 热更新
    if old.get("name") == CONTROL.get("preset"):
        _deep_merge(CONTROL, old)
        json.dump(CONTROL, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        reload_control()
    return jsonify({"ok": True, "file": file})


@app.route("/api/presets/load", methods=["POST"])
def api_presets_load():
    """加载预设：合并到操控文件 + 热更新配置(不重启)。"""
    d = request.get_json(force=True, silent=True) or {}
    file = (d.get("file") or d.get("name") or "").strip()
    if not file.endswith(".json"):
        file += ".json"
    fp = os.path.join(_PRESETS_DIR, file)
    if not os.path.exists(fp):
        return jsonify({"ok": False, "error": "预设不存在: %s" % file}), 404
    try:
        preset = json.load(open(fp, encoding="utf-8-sig"))
    except Exception as e:
        return jsonify({"ok": False, "error": "预设解析失败: %s" % e}), 400
    # 合并到 CONTROL(深合并, 保留未在预设里的配置)
    _deep_merge(CONTROL, preset)
    CONTROL["preset"] = preset.get("name", file[:-5])
    # **真实缺陷**：预设只写 `brain.engine=llama`（如"编程助手"）时，深合并会**保留原来的
    # 云端 brain.api**（base_url 还指着 Agnes）→ 出现"引擎说本地、地址是云端"的四不像，
    # 结果每次提问都失败。这里做一次一致性校正：引擎是本地就把地址/Key/模型名对齐到本地。
    _b = CONTROL.get("brain", {}) or {}
    if str(_b.get("engine", "")).lower() in ("llama", "auto") and not _is_local_base((_b.get("api") or {}).get("base_url", "")):
        _port = int(_b.get("llama_swap_port", 9292) or 9292)
        _bm = _local_brain_model()
        _b["engine"] = "llama"
        _b["api"] = {"base_url": "http://127.0.0.1:%d/v1" % _port, "api_key": "", "model": _bm or "xiaojiao"}
        LOG.info("预设要求本地引擎 → 已把大脑地址对齐到本地 %s（模型 %s）", _b["api"]["base_url"], _b["api"]["model"])
    json.dump(CONTROL, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    reload_control()  # 热更新内存配置, 无需重启
    # 回给前端足够的信息：前端要用它提示"联网/工具开关"到底变成什么了
    return jsonify({"ok": True, "preset": CONTROL.get("preset"),
                    "role": (CONTROL.get("role") or "")[:60],
                    "capabilities": CAP, "engine": BRAIN_ENGINE,
                    "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS})


@app.route("/api/presets/current")
def api_presets_current():
    """当前预设状态。"""
    return jsonify({"current": CONTROL.get("preset", ""), "engine": BRAIN_ENGINE,
                    "web_search": CAP.get("web_search", True), "memory": CAP.get("memory", True),
                    "run_tools": CAP.get("run_tools", True)})


def _deep_merge(base_dict, override):
    """递归合并 override 到 base_dict(不回退未提到的键)。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base_dict.get(k), dict):
            _deep_merge(base_dict[k], v)
        else:
            base_dict[k] = v


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

# 🧠 大脑仓库监控面板(app_monitor.py)：/monitor + /api/monitor
try:
    import app_monitor as _mon
    app.register_blueprint(_mon.bp)
except Exception as _me:
    print("⚠️ 监控面板未加载:", _me)

# 🎙️ 播客大脑(podcast_service/)：/podcast + /api/podcast
_pdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "podcast_service")
if _pdir not in sys.path:
    sys.path.insert(0, _pdir)
try:
    from podcast_api import bp as _pod_bp
    app.register_blueprint(_pod_bp)
    print("🎙️ 播客大脑已挂载（LLM写稿 + Chatterbox配音 + SD1.5封面）")
except Exception as _pe:
    print("⚠️ 播客大脑未挂载:", _pe)



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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1894, e)
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


@app.route("/api/session/delete", methods=["POST"])
def api_session_delete():
    """删除一个会话（侧栏每个会话后面的 ✕ 用的就是它）。

    以前**根本没有这个接口**，所以侧栏只有"新建/切换"，会话越堆越多删不掉。
    删最后一个会话时自动补一个空会话，保证界面永远有可用的当前会话。
    """
    import uuid
    payload = request.get_json(force=True, silent=True) or {}
    sid = (payload.get("id") or "").strip()
    if not sid:
        return jsonify({"ok": False, "error": "缺少会话 id"}), 400
    store = _sessions()                              # ⚠️ 别把变量名复用成请求体（第一版就是这么错的）
    before = len(store.get("sessions", []))
    store["sessions"] = [s for s in store.get("sessions", []) if s.get("id") != sid]
    if len(store["sessions"]) == before:
        return jsonify({"ok": False, "error": "会话不存在（可能已经被删了）"}), 404
    was_current = (store.get("current") == sid)
    if was_current:
        if not store["sessions"]:                    # 删光了就补一个空会话
            store["sessions"] = [{"id": uuid.uuid4().hex[:10], "title": "新对话", "messages": []}]
        store["current"] = store["sessions"][0]["id"]
    _save_sessions(store)
    LOG.info("删除会话 %s（剩余 %d 个，当前=%s）", sid, len(store["sessions"]), store.get("current"))
    return jsonify({"ok": True, "deleted": sid, "current": store.get("current"),
                    "was_current": was_current, "left": len(store["sessions"])})




_DISCOVER_CACHE = {"_started": False}


def _discover_probe():
    """后台慢探测：全盘找 ComfyUI / 视频模型根 / llama-swap（复用安装器探测函数）。"""
    d = {}
    try:
        import install_all as _ia
        d["comfy"] = _ia.discover_comfy() or ""
        d["video_root"] = _ia.discover_video_root() or ""
        d["swap"] = _ia.discover_exe("llama-swap.exe", ("llama-swap", "swap", "秒切")) or ""
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 1980, e)
    _DISCOVER_CACHE.update(d)


def _discover_paths(kick=True):
    """体检/引导用：拿 ComfyUI / 视频模型根 / llama-swap 的真实位置。

    优先级：控制文件(xiaojiao_control.json) → 环境变量 → 全盘自动探测（复用 install_all）。
    **不写死任何用户路径**；配置/环境变量毫秒级返回，慢的全盘扫描丢后台线程，结果缓存复用，
    这样体检页面永远不会因为扫盘而卡住。
    """
    if kick and not _DISCOVER_CACHE.get("_started"):
        _DISCOVER_CACHE["_started"] = True
        try:
            threading.Thread(target=_discover_probe, daemon=True).start()
        except Exception as e:
            LOG.debug("忽略异常(%s:%d): %s", __file__, 1996, e)
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(root, "xiaojiao_control.json"), encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    b = c.get("brain", {}) or {}
    comfy = (b.get("comfy_dir") or os.environ.get("XIAOJIAO_COMFY_DIR") or "").strip()
    swap = (os.environ.get("XIAOJIAO_LLAMA_SWAP") or "").strip()
    vroot = ""
    # 视频模型可能就在 ComfyUI 目录里，或在便携包外层任意一层（零成本检查，不用扫盘）
    _d = comfy.rstrip("\\/")
    for _ in range(5):
        if not _d:
            break
        if os.path.exists(os.path.join(_d, "dit_fp8.safetensors")):
            vroot = _d
            break
        _nd = os.path.dirname(_d)
        if _nd == _d:
            break
        _d = _nd
    if not vroot and comfy:
        for cand in (os.path.join(comfy, "models", "diffusion_models"),
                     os.path.join(comfy, "models", "checkpoints")):
            if os.path.exists(os.path.join(cand, "dit_fp8.safetensors")):
                vroot = cand
                break
    return {"comfy": _DISCOVER_CACHE.get("comfy") or comfy,
            "video_root": _DISCOVER_CACHE.get("video_root") or vroot,
            "swap": _DISCOVER_CACHE.get("swap") or swap}


@app.route("/api/env")
def api_env():
    """环境检查：检测用户电脑缺什么(安装向导)。"""
    import os, socket, shutil, subprocess, json as _j
    from flask import jsonify
    def port_up(pp):
        try:
            socket.create_connection(("127.0.0.1", pp), 0.8).close(); return True
        except Exception:
            return False
    def exe(pp): return shutil.which(pp) or (os.path.exists(pp) and pp) or None
    def exists(pp): return os.path.exists(pp)
    items = []
    def add(name, ok, info, need="", dl=""):
        items.append({"name": name, "ok": ok, "info": info, "need": need, "dl": dl})
    # 读取配置(不死写路径)
    import json as _j
    _cfg = {}
    try:
        _cfg = _j.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), encoding="utf-8"))
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2051, e)
    _ll = _cfg.get("brain", {}).get("llama", {}) or {}
    _ap = _cfg.get("brain", {}).get("api", {}) or {}    # Python
    add("Python", True, "v" + __import__("sys").version.split()[0], "已装", "")
    # llama-server(大脑, 路径走配置/环境变量)
    ls = os.environ.get("XIAOJIAO_LLAMA_SERVER") or _ll.get("server") or "llama-server"
    ls_ok = os.path.exists(ls) or shutil.which(ls) is not None
    add("llama-server(聊天大脑)", ls_ok, "本地大脑引擎" + ("，已装" if ls_ok else "，未找到"), "做法：下载 llama.cpp 便携版 → 解压 → 设环境变量 XIAOJIAO_LLAMA_SERVER=你的路径\\llama-server.exe", "github.com/ggml-org/llama.cpp/releases")
    gf = os.environ.get("XIAOJIAO_LLAMA_GGUF") or _ll.get("gguf") or ""
    # ---- 大脑模型检测: 本地 GGUF 文件 或 云端 key 之外, 按"协议连通"真验一次 ----
    # 本地: 看大脑服务端口是否在线(socket 连通)
    _local_port = None
    try:
        import urllib.parse as _up
        _local_port = _up.urlparse(_ap.get("base_url") or "http://127.0.0.1:9292/v1").port or 9292
    except Exception:
        _local_port = 9292
    _local_online = port_up(_local_port)
    _has_local_gf = bool(gf) and exists(gf)
    # 云端: 真发一次 OpenAI 兼容 GET /models 探测(超时短, 连通即算有)
    _cloud_ok = False; _cloud_info = ""
    _ck = (_ap.get("api_key") or "").strip(); _cu = (_ap.get("base_url") or "").strip(); _cm = (_ap.get("model") or "").strip()
    if _ck and _cu:
        try:
            _r = requests.get(_cu.rstrip("/") + "/models", headers={"Authorization": "Bearer " + _ck}, timeout=3)
            _cloud_ok = (_r.status_code == 200)
            _cloud_info = ("已连(%s)" % _r.status_code) if _cloud_ok else ("不通(HTTP %s)" % _r.status_code)
        except Exception as _e:
            _cloud_info = "连接失败: %s" % str(_e)[:40]
    _model_ok = _local_online or _cloud_ok
    if _model_ok:
        _parts = []
        if _local_online:
            _parts.append("本地大脑(:%d 在线)" % _local_port)
        if _cloud_ok:
            _parts.append("云端 " + (_cm or "兼容模型") + " " + _cloud_info)
        _minfo = (" + ".join(_parts)) or "已配置"
        _mneed = ""
    else:
        _minfo = "未连通"
        _mneed = "做法：启动 start_xiaojiao 让本地大脑(:%d)上线, 或配置任意 OpenAI 兼容 API(base_url+key+model)" % _local_port
    add("对话/工具模型", _model_ok, _minfo, _mneed, "")
    # 大脑在线(llama-swap 端口, 从配置读)
    _bp = 9292
    try:
        import urllib.parse as _up
        _bp = _up.urlparse(_ap.get("base_url") or "http://127.0.0.1:9292/v1").port or 9292
    except Exception:
        _bp = 9292
    add("聊天大脑(llama-swap:%d) 在线" % _bp, port_up(_bp), "现在" + ("在线" if port_up(_bp) else "未启动"), "启动后自动拉起", "")
    # ComfyUI + 视频模型（配置 → 环境变量 → 全盘自动探测；不写死任何路径）
    _dp = _discover_paths()
    comfy = _dp.get("comfy") or ""
    if comfy and not os.path.exists(os.path.join(comfy, "main.py")):
        comfy = ""   # 配的路径失效就当作没找到（下面提示怎么补）
    add("ComfyUI(视频大脑)", bool(comfy), ("位于 " + comfy if comfy else "未找到"), "做法：下载 ComfyUI 便携版(N卡版) → 解压 → 设 XIAOJIAO_COMFY_DIR=你的\\ComfyUI 目录", "github.com/comfyanonymous/ComfyUI/releases")
    _vroot = (_dp.get("video_root") or "").rstrip("\\/")
    def _find_model(*names):
        bases = [b for b in [_vroot,
                             os.path.join(comfy, "models", "diffusion_models") if comfy else "",
                             os.path.join(comfy, "models", "text_encoders") if comfy else "",
                             os.path.join(comfy, "models", "vae") if comfy else ""] if b]
        for b in bases:
            for n in names:
                for ext in ["", ".safetensors"]:
                    p = os.path.join(b, n + ext)
                    if exists(p): return p
        return ""
    ck = _find_model("dit_fp8")
    tc = _find_model("umt5_fp8")
    va = _find_model("vae_fp8")
    add("视频模型三件套(Wan2.1)", exists(ck) and exists(tc) and exists(va),
        "模型/编码器/VAE " + ("齐全" if exists(ck) and exists(tc) and exists(va) else "缺"), "下 dit_fp8/umt5/vae 放对应目录", "")
    # 视频大脑(8188): 按需启动(生成视频时才起, 不算缺/不用装, ok=True 以免猫娘误报"缺")
    _v8 = port_up(8188)
    add("视频大脑(8188) 在线", True if _v8 else True, "现在" + ("在线" if _v8 else "未启动(按需,生成视频时自动拉起,正常)"), "生成时自动起", "")
    # llama-swap(热切换)：环境变量 → 全盘自动探测（不写死路径）
    sw = _dp.get("swap") or ""
    ok = bool(sw) and os.path.exists(sw)
    if not ok and port_up(9292):
        # 路径还没探测出来，但它确实在跑 → 不算缺（避免误报"未找到"）
        sw, ok = "llama-swap.exe", True
    add("llama-swap(秒切管理)", ok, ("位于 " + os.path.basename(sw) if ok else "未找到"), "做法：解压 llama-swap.exe → 设 XIAOJIAO_LLAMA_SWAP=路径", "github.com/mostlygeek/llama-swap/releases")
    add("llama-swap(9292) 在线", port_up(9292), "多大脑秒切管理" + ("在线" if port_up(9292) else "未启动"), "start_xiaojiao 会自动拉起", "")
    # Node.js(.js 插件)
    add("Node.js(js插件)", shutil.which("node") is not None, "运行 .js 插件用" + ("，已装" if shutil.which("node") else "，未装"), "做法：去 nodejs.org 下载 LTS 版安装(一路默认)", "nodejs.org")
    # GPU
    gpu = False
    try:
        r2 = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8)
        if r2.returncode == 0:
            gpu = r2.stdout.strip()
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2144, e)
    add("NVIDIA GPU + 显存", bool(gpu), gpu or "未检测到", "需 N 卡", "")
    missing = [i for i in items if not i["ok"]]
    return jsonify({"items": items, "missing": [i["name"] for i in missing], "ok": not missing})

@app.route("/favicon.ico")
def favicon():
    """内联 SVG 图标：避免浏览器控制台一直报 favicon 404，也不额外增加文件。"""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           '<text y="50" font-size="50">🧡</text></svg>')
    return Response(svg, mimetype="image/svg+xml")


@app.route("/metrics")
def metrics_endpoint():
    """抓取插件指标（Prometheus 文本格式）。

    数据来自 plugins/scrapling_bridge.py 的 MetricsCollector：每个工具的调用/成功/失败/
    延迟/熔断次数 + 活跃会话数。没有依赖 Prometheus 客户端库，直接输出文本格式，
    既可被 Prometheus 抓取，也可 `curl http://127.0.0.1:5000/metrics` 人眼看。
    """
    for _name, _p in (PLUGINS or {}).items():
        _inst = _p.get("instance")
        if _inst and hasattr(_inst, "metrics_prometheus"):
            try:
                return Response(_inst.metrics_prometheus(), mimetype="text/plain; version=0.0.4; charset=utf-8")
            except Exception as e:
                return Response("# 指标读取失败：%s\n" % str(e)[:120],
                                mimetype="text/plain; charset=utf-8")
    return Response("# 抓取插件（scrapling_bridge）未加载，暂无指标\n",
                    mimetype="text/plain; charset=utf-8")


@app.route("/api/scrapling/metrics")
def scrapling_metrics_json():
    """同一份指标的 JSON 视图（含活跃会话明细、熔断状态），方便前端/排障查看。"""
    for _name, _p in (PLUGINS or {}).items():
        _inst = _p.get("instance")
        if _inst and hasattr(_inst, "metrics_snapshot"):
            try:
                return jsonify(_inst.metrics_snapshot())
            except Exception as e:
                return jsonify({"error": "指标读取失败：%s" % str(e)[:120]}), 500
    return jsonify({"error": "抓取插件未加载"}), 404


@app.route("/api/message", methods=["POST"])
def api_message():
    """保存一条消息到当前会话历史(如视频结果)，刷新后仍在。"""
    d = request.get_json(force=True, silent=True) or {}
    role = d.get("role") or "小焦"
    content = (d.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "空消息"}), 400
    append_msg(role, content)
    return jsonify({"ok": True})


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
    lean = bool((request.get_json(force=True, silent=True) or {}).get("lean", False))
    answer, online, info, needs_confirm, tool_trace = agent_run(user_input, lean=lean)
    answer = _strip_think(answer)                  # 双保险：任何路径的 <think> 都不许进正文/会话
    # 把占位小焦消息更新为真实回答（含最后那句提示）
    answer_final = answer
    if not answer_final:
        answer_final = "🤖 大脑没有应答，这一问没答上。请确认模型配置正确、端口可达。" + llm_error_suffix()
    s, d = get_current_session()
    for m in s.get("messages", []):
        if m.get("role") == "小焦" and "__pending__" in str(m.get("content", "")):
            m["content"] = answer_final
    _save_sessions(d)
    log_id = _record_interaction(user_input, answer_final, tool_trace)   # 内置·自动记录
    # 回答是否真的用上了检索资料（"只搜到不算，读进去才算"）
    g = _grounding(answer, info, user_input)
    if g.get("grounded") is False:
        LOG.warning("回答疑似未引用检索资料（命中 %d / 候选 %d）：问题=%r",
                    g.get("matched"), g.get("considered"), user_input[:40])
    return jsonify({
        "answer": answer,
        "brain_online": online,
        "needs_confirm": needs_confirm,
        "tool_trace": tool_trace,
        "tools_on": bool(CAP.get("run_tools", True)),
        "session_id": get_current_session()[0].get("id"),
        "log_id": log_id,
        "sources": [{"title": t, "content": c, "url": u} for t, u, c in info],
        "grounding": g,
        "grounding_note": _grounding_note(g),
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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2329, e)
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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2371, e)
    return jsonify({"know": know, "logs": logs, "good": good, "bad": bad, "corr": corr})


@app.route("/api/persona", methods=["POST"])
def api_persona():
    """切换人格：把 role 写回控制文件并生效。"""
    d = request.get_json(force=True, silent=True) or {}
    role = strip_search_rules((d.get("role") or "").strip())   # 合成人设/脏人设 → 只存纯人设
    if not role:
        return jsonify({"ok": False, "error": "人格不能为空"}), 400
    try:
        c = json.loads(open(CONTROL_FILE, encoding="utf-8").read())
        c["role"] = role
        json.dump(c, open(CONTROL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
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
        json.dump(control, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2408, e)
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
                     # 存**纯人设**，不是合成后的 SYSTEM_PROMPT（否则每切一次开关就多存一份铁律）
                     "role": strip_search_rules(CONTROL.get("role") or SYSTEM_PROMPT),
                     "capabilities": cap, "behavior": BEH,
                     "models": _get_models()}
            json.dump(saved, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"),
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
    # 关键: models 若没显式传, 就**合并**当前与传入的, 绝不因"保存通用设置"而清空用户加的外接模型
    existing = _get_models()
    merged = models if models is not None else existing
    # 若来自 brain 切换等只传部分, 仍保留全部现有 models
    saved = {"model_name": MODEL_NAME,
             "brain": brain if brain else dict(CONTROL.get("brain", {})),
             # 同样只存纯人设（原因见 /api/tools_toggle）
             "role": strip_search_rules(CONTROL.get("role") or SYSTEM_PROMPT),
             "capabilities": CAP, "behavior": BEH,
             "models": (merged if merged else existing),
             "dsh": CONTROL.get("dsh", {})}
    json.dump(saved, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"),
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


def _is_local_base(url):
    """base_url 是不是指向本机（本地大脑）。"""
    u = (url or "").lower()
    return any(h in u for h in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]", "://::1"))


def _local_served_model(base_url, want):
    """问一下本地服务**真的**提供哪些模型，返回一个能用的 model id（问不到就原样返回 want）。

    真实缺陷：控制文件里的"本地模型"条目曾把 model 存成云端模型名（agnes-2.5-flash）而
    base_url 指向本机 9292 —— 用户在界面上选了"本地模型"照样不能用（llama-swap 直接 404
    no router for requested model），还看不出来为什么。这里在切换时对一次账。
    """
    try:
        r = requests.get((base_url or "").rstrip("/") + "/models", timeout=3)
        if r.status_code == 200:
            ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
            if ids and want not in ids:
                return ids[0], ids
            if ids:
                return want, ids
    except Exception as e:  # noqa: silent-ok — 探测失败就按原样用，绝不因对账而挡住切换
        LOG.debug("忽略异常(%s:%d): %s", __file__, 3075, e)
    return want, []


def _cloud_key_problem(base, key, model):
    """切到云端模型时先探一下：通不通**当场**告诉用户，别等他问半天才发现。

    真实事故：用户切到云端模型后每次提问都只得到一句"模型调用出错"，而他并不知道问题在 Key 上。

    **判据只用 chat**：`GET /models` 在这里不可信 —— 实测连"空 Key / 乱写的 Key"都能时不时
    拿到 200（网关/缓存时不时不校验令牌）；拿它当"Key 有效"会把结论带偏（我就被带偏过一次）。
    """
    url = (base or "").rstrip("/") + "/chat/completions"
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    try:
        # 只等 20 秒：这是给用户"当场反馈"用的探测，不该让"切模型"这个动作卡住半分钟
        r = requests.post(url, headers=hdr, timeout=20,
                          json={"model": model, "messages": [{"role": "user", "content": "在的"}],
                                "max_tokens": 4})
        if r.status_code == 200:
            return "已切到云端大脑 %s（实测可通）" % model
        if r.status_code in (401, 403):
            return ("这个云端 Key 被服务商拒了（HTTP %s）→ 小焦先用本地大脑顶；"
                    "跑 `python tools/check_cloud_brain.py` 可确诊是哪把 Key 的问题" % r.status_code)
        return "已切到云端大脑 %s，但接口返回 HTTP %s（小焦会先用本地大脑顶）" % (model, r.status_code)
    except requests.exceptions.Timeout:
        # 超时 ≠ Key 错：实测这家冷启动能到 100 秒以上，报成"Key 被拒"会把人带偏
        return ("已切到云端大脑 %s：首次探测 20 秒没返回（对方慢，不是 Key 错）→ "
                "小焦会先本地大脑顶着，稍后自动再试云端" % model)
    except Exception as e:
        return "云端接口连不上（%s）→ 小焦会先用本地大脑顶" % str(e)[:40]


@app.route("/api/model/select", methods=["POST"])
def api_model_select():
    """切换当前大脑到某个已配置模型。"""
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    for m in _get_models():
        if m.get("name") == name:
            brain = dict(CONTROL.get("brain", {}))
            engine = m.get("engine", "auto")
            base = m.get("base_url", "")
            model = m.get("model", "")
            note = ""
            # 指向本机的一律按本地大脑处理：本地服务不需要 Key，而条目里存的 engine
            # 可能是当初加模型时随手填的 "api"（那样会把本地地址当云端用，必失败）。
            if _is_local_base(base):
                engine = "llama"
                if engine != m.get("engine"):
                    note = "已按本地大脑接入（本地服务不需要 API Key）"
            brain["engine"] = engine
            if engine == "llama":
                model, ids = _local_served_model(base or "http://127.0.0.1:9292/v1", model)
                if ids and model != (m.get("model") or ""):
                    note = (note + "；" if note else "") + "本地服务实际提供 %s，已改用 %s" % ("/".join(ids), model)
                brain["api"] = {"base_url": base or "http://127.0.0.1:9292/v1",
                                "api_key": "", "model": model or "xiaojiao"}
            else:
                brain["api"] = {"base_url": base, "api_key": m.get("api_key", ""), "model": model}
                note = _cloud_key_problem(base, m.get("api_key", ""), model)   # 云端 Key 不通就当场说
            _save_control(brain=brain)
            return jsonify({"ok": True, "engine": brain["engine"], "name": name,
                            "model": brain["api"]["model"], "note": note})
    return jsonify({"ok": False, "error": "模型不存在"}), 404


@app.route("/api/model/addlocal", methods=["POST"])
def api_model_addlocal():
    """一键添加本地模型(GGUF)：自动写 llama-swap.yaml + brain_manager + 下拉, 重启llama-swap。
    参数: name=显示名, gguf=模型文件绝对路径, ctx=上下文(默认20000)。"""
    import subprocess as _sp
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    gguf = (d.get("gguf") or "").strip()
    if not name or not gguf:
        return jsonify({"ok": False, "error": "需要 name 和 gguf 路径"}), 400
    if not os.path.exists(gguf):
        return jsonify({"ok": False, "error": "模型文件不存在: " + gguf}), 400
    ctx = int(d.get("ctx", 20000))
    mid = name.lower().replace(" ", "-")
    root = os.path.dirname(os.path.abspath(__file__))
    # ① llama-swap.yaml 加模型
    yp = os.path.join(root, "llama-swap.yaml")
    ys = open(yp, encoding="utf-8").read()
    if ("  " + mid + ":") not in ys:
        gg = gguf.replace("\\", "/")
        ys = ys.rstrip() + ("\n  %s:\n    cmd: \"C:/llama/llama-server.exe --port ${PORT} --model %s -c %d --reasoning off\"\n    ttl: 0\n    useModelName: %s\n" % (mid, gg, ctx, mid))
        open(yp, "w", encoding="utf-8").write(ys)
    # ② brain_manager BRAINS 加
    bp = os.path.join(root, "brain_manager.py")
    bs = open(bp, encoding="utf-8").read()
    if '"%s"' % mid not in bs:
        bs = bs.replace("    # 未来扩展(示例, 加进 BRAINS 即可被调度):",
                        '    "%s": {  # 新增: %s\n        "name": "%s", "port": 9292,\n        "type": "llama", "vram_gb": 5.0, "state": "OFF",\n    },\n    # 未来扩展(示例, 加进 BRAINS 即可被调度):' % (mid, name, name), 1)
        open(bp, "w", encoding="utf-8").write(bs)
    # ③ 下拉模型加
    CONTROL.setdefault("models", [])
    if not any(m.get("model") == mid for m in CONTROL["models"]):
        CONTROL["models"].append({"name": name, "engine": "llama",
                                  "base_url": "http://127.0.0.1:9292/v1", "api_key": "", "model": mid})
        json.dump(CONTROL, open(os.path.join(root, "xiaojiao_control.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # ④ 重启 llama-swap（路径自动探测，不写死）
    try:
        _sw = _discover_paths().get("swap") or ""
        if _sw and os.path.exists(_sw):
            _sp.Popen(["powershell", "-NoProfile", "-Command",
                       "Get-Process llama-swap -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 1; "
                       "Start-Process '%s' -ArgumentList '-config \\\"%s\\\" -listen 127.0.0.1:9292' -WindowStyle Hidden" % (_sw.replace("'", "''"), yp)])
        else:
            return jsonify({"ok": True, "model_id": mid, "name": name,
                            "note": "已写入配置；但没找到 llama-swap.exe，请手动重启它（或设 XIAOJIAO_LLAMA_SWAP）"})
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2529, e)
    return jsonify({"ok": True, "model_id": mid, "name": name, "note": "llama-swap 正在重启, 约10秒后可用"})


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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2600, e)
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
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 2614, e)
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
            # 回**纯人设**：铁律由 compose_system_prompt 每轮自动拼，界面里不用背它，
            # 否则用户点一次保存就把铁律又存进控制文件（老版本就是这么攒到 11 份的）。
            "role": strip_search_rules(CONTROL.get("role") or SYSTEM_PROMPT),
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
        "role": strip_search_rules(got.get("role") or cur.get("role") or SYSTEM_PROMPT),
        "capabilities": {**cur.get("capabilities", {}), **(got.get("capabilities") or {})},
        "behavior": {**cur.get("behavior", {}), **(got.get("behavior") or {})},
        "models": _get_models(),
        "dsh": cur.get("dsh", {}),
        "preset": cur.get("preset", ""),
        "_engine": cur.get("_engine", ""),
    }
    try:
        json.dump(saved, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xiaojiao_control.json"), "w", encoding="utf-8"),
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
  /* 会话行：左边是会话按钮，右边是删除 ✕（平时淡、悬停才明显，避免误点） */
  .srow{display:flex;align-items:center;gap:2px;margin-bottom:4px;border-radius:8px}
  .srow:hover{background:#1e2430}
  .srow.active{background:#2a3140}
  .srow .sess{flex:1;min-width:0;display:block;width:auto;text-align:left;background:transparent;border:none;color:#cbd0dc;padding:9px 10px 9px 12px;border-radius:8px;font-size:13px;cursor:pointer;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  .srow .sess:hover{background:transparent}
  .srow.active .sess{color:#fff}
  .sdel{flex:0 0 auto;width:26px;height:26px;margin-right:4px;background:transparent;border:none;color:#6e7681;
        border-radius:6px;font-size:12px;cursor:pointer;opacity:0;transition:.12s;padding:0}
  .srow:hover .sdel,.srow.active .sdel{opacity:.75}
  .sdel:hover{background:#3a2230;color:#f87171;opacity:1}
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
  #feed>*:has(.tblwrap){max-width:100%}   /* 带表格的消息放宽到整栏宽，别把表格挤成两行 */
  .m{display:flex;margin-bottom:14px;gap:10px;flex-wrap:wrap}
  .m.user{justify-content:flex-end}.m.bot{justify-content:flex-start}
  .b{max-width:82%;padding:11px 16px;border-radius:16px;line-height:1.65;font-size:15px;white-space:pre-wrap;word-break:break-word;box-shadow:none}
  .user .b{background:linear-gradient(135deg,#5b5ff5,#7c5cf0);color:#fff;border-bottom-right-radius:5px}
  .bot .b{background:#151a24;border:1px solid #252b38;border-bottom-left-radius:5px}
  .src{font-size:11px;color:#8b93a3;margin-top:6px;padding-left:2px}
  .src b{color:#a78bfa}
  .srcbtn{background:#1a2030;border:1px solid #2a3140;color:#a78bfa;border-radius:14px;padding:4px 12px;font-size:12px;cursor:pointer;margin-top:6px;white-space:nowrap;width:auto;align-self:flex-start;display:inline-flex;align-items:center;gap:4px}
  .srcbtn:hover{background:#232c42;border-color:#405a99}
  .srcbtn:hover{background:#263349}
  .srcbox{display:none;white-space:normal;font-size:11px;color:#8b93a3;margin-top:6px;background:#11141c;border:1px solid #252b38;border-radius:8px;padding:8px 10px;max-height:180px;overflow-y:auto;flex-basis:100%;width:100%;box-sizing:border-box}
  .srci{margin-bottom:8px}
  .srci .st{color:#9bb0e1;font-size:12px;margin-bottom:2px}
  .srci .sc{color:#aab2c0;font-size:11px;line-height:1.5}
  .srcbox.show{display:block}
  .srcbox b{color:#a78bfa;margin-right:4px}
  /* 代码块：底色必须干净纯色。
     ⚠️ 真实缺陷：`.b code{background:#2a3140}` 会**连代码块里的 code 元素一起**上色，
     于是整块代码躺在一个浅灰方块上（用户看到的"白色背景印记/阴影"就是这么来的）。
     所以这里显式把 pre 内的 code 背景/内边距/阴影全部清掉。 */
  .b pre.code{background:#0d1117;border:none;border-radius:0;padding:14px 16px;overflow-x:auto;margin:0;box-shadow:none;text-shadow:none}
  .b pre.code::-webkit-scrollbar{height:6px;width:6px}
  .b pre.code::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
  .b pre.code::-webkit-scrollbar-track{background:transparent}
  .b pre.code code{font-family:Consolas,'Courier New',monospace;font-size:13px;line-height:1.7;white-space:pre;color:#c9d1d9;background:none;padding:0;border-radius:0;border:none;box-shadow:none;text-shadow:none}
  .b pre.code,.b pre.code *,.b table,.b table *{text-shadow:none;box-shadow:none}
  /* 行内 code（正文里的小代码）才需要浅底 */
  .b code{background:#242b39;padding:1px 6px;border-radius:4px;font-family:Consolas,monospace;font-size:13px;color:#e6edf3}
  .codebox{border:none;border-radius:10px;margin:12px 0;overflow:hidden;background:#0d1117;box-shadow:none}
  .codehead{display:flex;align-items:center;gap:8px;background:#0d1117;padding:10px 12px 0}
  .lang{padding:2px 8px;font-size:11px;font-weight:600;color:#6e7681;text-transform:uppercase;letter-spacing:.5px;background:transparent}
  .cp{margin-left:auto;background:transparent;border:1px solid #21262d;color:#7d8590;border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer}
  .cp:hover{background:#161b22;color:#e6edf3}
  /* 语法高亮配色（深色下高对比、不刺眼） */
  .tk-kw{color:#ff7b72}
  .tk-str{color:#a5d6ff}
  .tk-num{color:#79c0ff}
  .tk-com{color:#7d8794;font-style:italic}
  .tk-fn{color:#d2a8ff}
  .tk-key{color:#7ee787}
  .tk-bool{color:#79c0ff}
  .tk-tag{color:#7ee787}
  .tk-attr{color:#79c0ff}
  .tk-op{color:#ffa657}
  .tk-var{color:#ffa657}
  .kw{color:#ff7b72}
  .lang.python,.lang.py{color:#6e7681}.lang.js,.lang.javascript{color:#6e7681}
  .lang.bash,.lang.sh{color:#6e7681}.lang.html,.lang.css{color:#6e7681}.lang.json{color:#6e7681}
  /* JSON / 长文本块：限高 + 纵向滚动，避免一大坨内容把聊天窗糊满 */
  .codebox.lang-json pre.code,.codebox.lang-text pre.code{max-height:380px;overflow-y:auto}
  .codebox.lang-json pre.code::-webkit-scrollbar,.codebox.lang-text pre.code::-webkit-scrollbar{width:6px}
  .codebox.lang-json pre.code::-webkit-scrollbar-thumb,.codebox.lang-text pre.code::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
  /* ```markdown 围栏：直接渲染成正常排版（表格/标题/列表），左侧细线表示"这块来自代码围栏" */
  .b .mdfence{border-left:2px solid #2f3b52;padding:2px 0 2px 12px;margin:8px 0}
  .lang.cpp,.lang.c{color:#6e7681}.lang.java{color:#6e7681}.lang.sql{color:#6e7681}
  .cp{background:#1f2533;border:1px solid #2a3140;color:#cbd0dc;border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer}
  .cp:hover{background:#2a3140}
  /* 表格：给足留白、宽表横向滚动、短列不许被折断（以前 HIG H / CVE-2026- 这种断字很难看） */
  .b .tblwrap{overflow-x:auto;margin:10px 0;border:1px solid #2a3140;border-radius:10px;background:#101520}
  .b table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px;margin:0}
  .b table th,.b table td{border:none;border-bottom:1px solid #232a38;border-right:1px solid #1b2230;padding:11px 15px;text-align:left;line-height:1.65;vertical-align:top;word-break:keep-all;overflow-wrap:anywhere}
  .b table th{background:#182031;color:#9fb0d0;font-weight:600;white-space:nowrap}
  .b table tr:last-child td{border-bottom:none}
  .b table th:last-child,.b table td:last-child{border-right:none}
  .b table tbody tr:nth-child(even) td{background:#131926}
  .b table td:not(:last-child){white-space:nowrap}   /* 只有最后一列（摘要）允许折行 */
  /* 带表格/代码的消息给更宽的容器，别把内容挤在小框里 */
  .b.wide{max-width:100%}
  .m.widem{max-width:100%}
  .msgbot{display:flex;gap:8px;margin-top:6px;align-items:center;padding-left:2px}
  /* 检索引用校验标记：绿色=确实读了资料；黄色=疑似没读（自己编的） */
  .gnd{margin-top:8px;font-size:11px;color:#6ee7a8;background:#0f1d18;border:1px solid #1d3a2c;
       border-radius:8px;padding:5px 9px;display:inline-block}
  .gnd.bad{color:#fbbf24;background:#1d1908;border-color:#4a3c12}
  .msgbot button{background:#1f2533;border:1px solid #2a3140;color:#8b93a3;border-radius:8px;padding:4px 10px;font-size:12px;cursor:pointer}
  .msgbot button:hover{background:#2a3140}
  .msgbot .fb{font-size:14px;padding:2px 8px}
  .b strong{color:#fff}
  /* Markdown 标题（抓取正文常用）*/
  .b .mdh{font-size:16px;font-weight:700;color:#fff;margin:10px 0 6px;padding-bottom:5px;border-bottom:1px solid #2a3140}
  .b .mdh:first-child{margin-top:2px}
  .b a{color:#a78bfa;text-decoration:none;border-bottom:1px solid #a78bfa55}
  .b a:hover{color:#c4b5fd;border-bottom-color:#c4b5fd}
  /* 抓取结果卡片：把"抓来的网页内容"和对话正文区分开 */
  .fetchcard{background:#0f1520;border:1px solid #223049;border-left:3px solid #45d483;border-radius:10px;padding:12px 14px;margin:8px 0;font-size:14px;line-height:1.7;color:#cbd0dc}
  .fetchhead{font-size:12px;color:#45d483;margin-bottom:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .fetchhead .u{color:#8b93a3;font-weight:400}
  .b ul,.b ol{padding-left:20px;margin:6px 0}
  .b h1,.b h2,.b h3{color:#fff;margin:10px 0 6px}
  footer{padding:10px 20px 18px;background:transparent;border-top:none}
  .bar{max-width:880px;margin:0 auto;display:flex;gap:10px}
  /* 居中输入区（Composer）：像现代 AI 客户端那样收成一张卡片，宽度统一、视觉焦点明确 */
  .composer{max-width:820px;margin:0 auto;background:#12161f;border:1px solid #262d3d;border-radius:16px;
            padding:10px 12px 8px;box-shadow:0 8px 28px rgba(0,0,0,.28);transition:border-color .15s}
  .composer:focus-within{border-color:#4f46e5;box-shadow:0 8px 28px rgba(79,70,229,.18)}
  .cmp-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
  .chip-sel{width:auto;background:#171d29;border:1px solid #2a3140;color:#cbd0dc;border-radius:999px;
            padding:5px 12px;font-size:12px;cursor:pointer;outline:none}
  .chip-sel:hover{background:#1e2635;border-color:#3a4560}
  .chip-btn{background:#171d29;border:1px solid #2a3140;color:#cbd0dc;border-radius:999px;
            padding:5px 12px;font-size:12px;cursor:pointer}
  .chip-btn:hover{background:#212a3a;border-color:#405a99}
  .cmp-input{display:flex;align-items:flex-end;gap:10px}
  .cmp-input textarea{flex:1;background:transparent;border:none;outline:none;color:#e8ebf3;font-size:15px;
                      line-height:1.6;resize:none;max-height:220px;padding:8px 4px;font-family:inherit}
  .cmp-send{flex:0 0 auto;width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,#5b5ff5,#7c5cf0);
            border:none;color:#fff;font-size:16px;cursor:pointer;padding:0}
  .cmp-send:hover{filter:brightness(1.12)}
  .cmp-hint{font-size:11px;color:#5f6a7d;margin-top:8px;padding-left:4px;min-height:14px}
  /* 空状态欢迎卡（居中） */
  .welcome{max-width:820px;margin:6vh auto 0;text-align:center}
  .welcome .wl{font-size:30px;font-weight:800;background:linear-gradient(135deg,#8b8ff8,#c4b5fd);
               -webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:10px}
  .welcome .ws{color:#8b93a3;font-size:14px;margin-bottom:22px}
  .welcome .chips{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
  .welcome .chip{background:#141a26;border:1px solid #262d3d;color:#cbd0dc;border-radius:12px;
                 padding:10px 14px;font-size:13px;cursor:pointer;transition:.15s}
  .welcome .chip:hover{background:#1c2432;border-color:#4f46e5;color:#fff;transform:translateY(-1px)}
  /* 轻提示（toast）—— 放在**顶部居中**：原来 bottom:120px 正好压在输入区那排
     「预设 / 模型 / 工具」胶囊上，弹提示时把用户正在点的下拉盖住，看着像"字没了"。 */
  #toast{position:fixed;left:50%;top:16px;transform:translateX(-50%) translateY(-10px);opacity:0;
         background:#1b2231;border:1px solid #3a4560;color:#e8ebf3;padding:10px 16px;border-radius:12px;
         font-size:13px;z-index:90;pointer-events:none;transition:.2s;max-width:70vw;
         box-shadow:0 10px 30px rgba(0,0,0,.35)}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
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
  .phead{font-size:12px;color:#7a8290;margin:16px 0 8px}
  .pgroup{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
  .pcard{background:#0e1116;border:1px solid #262b3a;border-radius:12px;padding:14px;position:relative;cursor:pointer}
  .pcard:hover{border-color:#405a99}
  .pcard .pinfo .pname{font-weight:700;font-size:14px}
  .pcard .ptag{font-size:10px;background:#1a2233;color:#7a8290;border-radius:6px;padding:1px 6px;margin-left:6px}
  .pcard .cur{background:#1b3a2b;color:#45d483;border-radius:6px;padding:1px 6px;font-size:10px;margin-left:6px}
  .pcard .pdesc{color:#aab2c0;font-size:12px;margin:6px 0}
  .pcard .pfile{color:#5b5f6e;font-size:11px}
  .pcard .picons{position:absolute;right:10px;bottom:10px;display:flex;gap:8px;font-size:15px}
  .pcard .picons span{cursor:pointer;color:#7a8290}
  .pcard .picons span:hover{color:#a78bfa}
  .padd{border:1px dashed #262b3a;border-radius:12px;padding:14px;text-align:center;color:#a78bfa;cursor:pointer;font-size:14px}
  .padd:hover{border-color:#405a99}
  .setnav-item{display:flex;align-items:center;gap:8px;padding:10px 12px;border-radius:10px;font-size:14px;color:#cbd0dc;cursor:pointer}
  .setnav-item:hover{background:#1e2430}
  .setnav-item.active{background:#2a3140;color:#fff}
  .setbody{background:#141822;border:1px solid #262b3a;border-radius:14px;padding:22px}
  .sec{display:none}
  .sec.show{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:999}
  .modal{background:#151a26;border:1px solid #2a3140;border-radius:14px;padding:20px;width:min(420px,90vw);box-shadow:0 20px 60px #000a;color:#e8ebf3}
  .modal h3{font-size:15px;margin:0 0 18px;font-weight:600}
  .modal label{font-size:12px;color:#8b93a3;display:block;margin:18px 0 8px;font-weight:600}
  .modal input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;font-size:14px;margin-bottom:4px}
  .modal .m-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:26px;padding-top:16px;border-top:1px solid #2a3140}
  .modal button{padding:8px 16px;border-radius:8px;border:1px solid #2a3140;background:#222a3e;color:#e8ebf3;cursor:pointer}
  .splash{position:fixed;inset:0;background:#05070c;display:flex;align-items:center;justify-content:center;z-index:9999;animation:spaout .6s ease 1.6s forwards}
  @keyframes spaout{to{opacity:0;visibility:hidden;pointer-events:none}}
  .s-inner{text-align:center}
  .s-logo{font-size:44px;font-weight:800;background:linear-gradient(90deg,#ff6bcb,#a78bfa);-webkit-background-clip:text;background-clip:text;color:transparent}
  .s-bar{width:220px;height:6px;background:#1a2030;border-radius:3px;margin:18px auto 10px;overflow:hidden}
  .s-bar span{display:block;height:100%;width:40%;background:linear-gradient(90deg,#ff6bcb,#a78bfa);border-radius:3px;animation:sl 1.2s infinite}
  @keyframes sl{0%{margin-left:-40%}100%{margin-left:100%}}
  .s-msg{color:#8b93a3;font-size:13px}
  .modal-env{width:min(560px,94vw)}
  .envlist{max-height:55vh;overflow:auto;font-size:13px}
  .envitem{display:flex;align-items:center;gap:10px;padding:8px 10px;border-bottom:1px solid #1a2030}
  .envitem .st{width:20px;text-align:center}
  .envitem.ok .st{color:#45d483}.envitem.no .st{color:#ff6b6b}
  .envitem .nm{flex:1;color:#e8ebf3}
  .envitem .inf{color:#7a8290;font-size:12px}
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
  <div class="brand"><span class="logo">🐳 小焦</span><span class="tag">harness · 标准模式</span><span class="badge2" id="taskBadge" style="display:none">⏳ 空闲</span> <a href="/monitor" style="font-size:11px;color:#a78bfa;margin-left:6px">🧠 监控</a> <a href="http://127.0.0.1:48911" style="font-size:11px;color:#45d483;margin-left:6px">🐱 猫娘</a> <span id="costBadge" style="font-size:11px;color:#8b93a3;margin-left:8px"></span></div>
  <div class="hdr-right">
    <button class="icon-btn" id="toolsBtn" onclick="toggleTools()">🛠️ 工具</button>
    <button class="icon-btn" onclick="openBrain()">🧠 小脑</button>
    <button class="icon-btn" onclick="openSettings()">⚙️ 设置</button>
    <button class="icon-btn" onclick="copyPage()">📄 Session log ⚡</button>
  </div>
</header>
<div id="feed"></div>
<footer>
  <div class="composer" id="composer">
    <div class="cmp-top">
      <select id="presetSel" class="chip-sel" onchange="selectPreset(this.value)" title="Agent 预设：人格 + 大脑 + 工具开关，选中即生效"></select>
      <select id="modelSel" class="chip-sel" onchange="selectModel()" title="当前模型"></select>
      <span class="ws-ind" id="wsInd" onclick="toggleAccess()" title="点击：Full access(所有命令直接执行)/Read-only(每次执行都询问)">🔐 Full access</span>
      <button class="chip-btn" onclick="openVideo()" title="本地零算力生成视频">🎬 视频</button>
      <button class="chip-btn" id="toolsBtn" onclick="toggleTools()" title="工具调用开关">🛠️ 工具</button>
    </div>
    <div class="cmp-input">
      <textarea id="inp" rows="1" placeholder="向小焦提问…（Enter 发送，Shift+Enter 换行）" autocomplete="off"
                oninput="autoGrow(this)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send();}"></textarea>
      <button class="cmp-send" onclick="send()" title="发送">➤</button>
    </div>
    <div class="cmp-hint"><span id="cmpHint">小焦会联网检索 · 抓取网页 · 查 NVD 漏洞 · 写文件</span></div>
  </div>
</footer>
  </div>
</div>

<div id="modalBg" class="modal-bg" style="display:none">
  <div class="modal">
    <h3>🔍 搜索会话</h3>
    <input id="msq" placeholder="输入关键词，过滤会话…" onkeydown="if(event.key==='Enter')doSearch()"/>
    <div class="m-actions"><button onclick="closeSearch()">取消</button><button class="primary" onclick="doSearch()">确定</button></div>
  </div>
</div>
<div id="editBg" class="modal-bg" style="display:none">
  <div class="modal modal-env">
    <h3>✏️ 编辑预设</h3>
    <label style="font-size:12px;color:#8b93a3">名称</label><input id="eName" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;margin:4px 0 8px">
    <label style="font-size:12px;color:#8b93a3">人格 / 人设(role)</label><textarea id="eRole" rows="3" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;resize:vertical;margin:4px 0 8px"></textarea>
    <label style="font-size:12px;color:#8b93a3">大脑引擎</label>
    <select id="eEngine" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;margin:4px 0 8px"><option value="auto">auto(自动)</option><option value="llama">llama(本地)</option><option value="api">api(外接)</option><option value="xiaojiao">xiaojiao(自建)</option></select>
    <label style="font-size:12px;color:#8b93a3">上下文 ctx</label><input id="eCtx" type="number" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;margin:4px 0 8px">
    <label style="font-size:12px;color:#8b93a3">工具开关</label>
    <div style="display:flex;gap:16px;font-size:13px;margin:6px 0"><label><input type="checkbox" id="eSearch"> 联网搜索</label><label><input type="checkbox" id="eMem"> 记忆</label><label><input type="checkbox" id="eTools"> 工具/代码执行</label></div>
    <label style="font-size:12px;color:#8b93a3">温度 temperature</label><input id="eTemp" type="number" step="0.1" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;margin:4px 0 8px">
    <label style="font-size:12px;color:#8b93a3">max_tokens</label><input id="eMax" type="number" style="width:100%;padding:8px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3;margin:4px 0 8px">
    <div class="m-actions"><button onclick="closeEdit()">取消</button><button class="primary" onclick="savePreset()">💾 保存预设</button></div>
  </div>
</div>
<div id="videoBg" class="modal-bg" style="display:none">
  <div class="modal modal-video">
    <h3>🎬 生成视频</h3>
    <div style="display:flex;align-items:center;gap:10px;margin:0 0 10px">
      <span style="font-size:12px;color:#8b93a3">生成引擎：</span>
      <select id="vengine" onchange="setVideoMode(this.value)" style="width:auto;padding:6px 10px;border-radius:8px;border:1px solid #2a3140;background:#0e1116;color:#e8ebf3">
        <option value="api">☁️ 云端 Agnes API（免费，不占显存）</option>
        <option value="local">💻 本地 ComfyUI+Wan2.1</option>
      </select>
      <span id="vmodeH" style="font-size:11px;color:#7a8290"></span>
    </div>
    <p style="color:#8b93a3;font-size:13px;margin:0 0 10px">输入场景，小焦生成真视频。云端 API 快且不占显存，本地用 ComfyUI+Wan2.1。</p>
    <textarea id="vq" placeholder="例如：樱花飘落的海边、一只猫在阳光下打盹…" rows="3" style="width:100%;resize:vertical"></textarea>
    <div id="vpv" class="vpv" style="display:none"></div>
    <div id="vconfirm" class="m-actions" style="display:none"><button onclick="openVideo()">重输</button><button class="primary" onclick="confirmVideo()">✅ 确认并生成</button></div>
    <div class="m-actions"><button onclick="closeVideo()">取消</button><button class="primary" onclick="startVideo()">✨ 精炼提示词</button></div>
  </div>
</div>
<div id="addLocalBg" class="modal-bg" style="display:none">
  <div class="modal modal-env">
    <h3>🗄️ 一键添加本地模型</h3>
    <label>模型显示名</label><input id="lm_name" placeholder="如 数学大脑">
    <label>GGUF 文件绝对路径</label><input id="lm_gguf" placeholder="如 D:/models/xxx.gguf（填你自己模型的真实路径）">
    <label>上下文 ctx（默认 20000）</label><input id="lm_ctx" type="number" value="20000">
    <div class="m-actions"><button onclick="closeAddLocal()">取消</button><button class="primary" onclick="saveAddLocal()">🚀 一键添加</button></div>
    <div class="think" id="lm_msg" style="margin-top:12px"></div>
  </div>
</div>
<div id="envBg" class="modal-bg" style="display:none">
  <div class="modal modal-env">
    <h3>🛠️ 环境检查（安装向导）</h3>
    <div id="envList" class="envlist"><div class="think">正在检测…</div></div>
    <div class="m-actions"><button onclick="closeEnv()">关闭</button></div>
  </div>
</div>
<div id="splash" class="splash"><div class="s-inner"><div class="s-logo">小焦</div><div class="s-bar"><span></span></div><div class="s-msg">暖机中，正在优化页面…</div></div></div>
<div id="brainBg" class="brainbg" style="display:none">
  <div class="brain">
    <div class="brain-head"><span class="logo">🐳 小脑</span><span class="brain-sub">小焦真正自研的那颗会学习的脑</span><button class="icon-btn x" onclick="closeBrain()">✕</button></div>
    <div class="brain-stats" id="brainStats">读取中…</div>
    <div class="brain-cols">
      <div class="brain-col"><h4>🧠 学到的功能用法</h4><div id="brainLessons" class="brain-list">…</div></div>
      <div class="brain-col"><h4>🧭 说明</h4><div id="brainPrompt" class="brain-list" style="margin-bottom:10px"></div>
  <div class="brain-note">
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
      <div class="setnav-item" data-sec="presets" onclick="setSec(this,'presets')">🎭 Agent 预设</div>
    </div>
    <div class="setbody">
      <div class="sec" id="sec-presets">
      <h3>🎭 Agent 预设</h3>
      <p style="color:#7a8290;font-size:13px;margin:4px 0 14px">预设 = 人格 + 大脑 + 工具开关。选中即切换（不重启）。</p>
      <div class="phead">内置</div>
      <div id="presetCards" class="pgroup"></div>
      <div class="phead">自定义</div>
      <div class="padd" onclick="createPreset()">＋ 用「创造模式」创作自定义预设</div>
    </div>
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
            <div class="field" style="display:flex;align-items:flex-end;gap:10px"><button class="btn-sec" onclick="addModel()">＋ 添加模型(API/外接)</button><button class="btn-sec" style="margin-left:8px" onclick="addLocalModel()">🗄️ 一键加本地GGUF</button></div>
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
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')
  .replace(/^[-*]\s+/gm,'· ')
  .replace(/\n/g,'<br>');return t;}
// ===== 语法高亮：按语言分词上色（关键字/字符串/数字/注释/函数名/JSON 键…）=====
// 规则里**不能有捕获组**（否则分组下标会错位），一律用 (?:...)。
const HLRULES={
  json:[['key',/"(?:\\.|[^"\\])*"(?=\s*:)/],['str',/"(?:\\.|[^"\\])*"/],['bool',/\b(?:true|false|null)\b/],['num',/-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/]],
  python:[['com',/#[^\n]*/],['str',/["]{3}[\s\S]*?["]{3}|[']{3}[\s\S]*?[']{3}|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/],
    ['kw',/\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|lambda|yield|pass|break|continue|in|is|not|and|or|None|True|False|self|async|await|global|nonlocal|raise|assert|del|match|case)\b/],
    ['fn',/\b[A-Za-z_]\w*(?=\()/],['num',/\b\d+(?:\.\d+)?\b/],['op',/[+\-*/%=<>!&|^~]+/]],
  js:[['com',/\/\/[^\n]*|\/\*[\s\S]*?\*\//],['str',/`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/],
    ['kw',/\b(?:function|const|let|var|return|if|else|for|while|do|class|extends|new|async|await|try|catch|finally|throw|typeof|instanceof|import|export|default|from|of|in|null|undefined|true|false|this|super)\b/],
    ['fn',/\b[A-Za-z_$][\w$]*(?=\()/],['num',/\b\d+(?:\.\d+)?\b/],['op',/[+\-*/%=<>!&|^~?:]+/]],
  bash:[['com',/#[^\n]*/],['str',/"(?:\\.|[^"\\])*"|'[^']*'/],
    ['kw',/\b(?:echo|if|then|fi|for|do|done|while|case|esac|function|export|source|cd|ls|rm|cp|mv|mkdir|git|python|python3|pip|curl|cat|grep|find|chmod|chown|sudo|apt|brew|winget|powershell|set|start|stop)\b/],
    ['var',/\$\{?\w+\}?/],['op',/[|&><=]+/]],
  sql:[['com',/--[^\n]*/],['str',/'(?:[^']|'')*'/],
    ['kw',/\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|ALTER|DROP|INDEX|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|AND|OR|NOT|NULL|AS|DISTINCT|COUNT|SUM|AVG|MAX|MIN|PRIMARY|KEY|FOREIGN|REFERENCES)\b/i],
    ['num',/\b\d+\b/]],
  css:[['com',/\/\*[\s\S]*?\*\//],['kw',/@[\w-]+/],['str',/"[^"]*"|'[^']*'/],
    ['attr',/[.#]?[A-Za-z-][\w-]*(?=\s*\{)/],['fn',/[A-Za-z-]+(?=\s*:)/],['num',/-?\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms)?\b/]],
  html:[['com',/<!--[\s\S]*?-->/],['tag',/<\/?[A-Za-z][\w-]*|\/?>/],['attr',/[A-Za-z-]+(?==)/],['str',/"[^"]*"|'[^']*'/]],
};
function _hlGeneric(code){       // 没有对应语言的规则时：只认字符串/数字/注释，别乱上色
  const rules=[['com',/#[^\n]*|\/\/[^\n]*/],['str',/"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'/],['num',/\b\d+(?:\.\d+)?\b/]];
  return rules;
}
function hl(code,lang){
  const key=(lang||'').toLowerCase();
  const alias={py:'python',python3:'python',javascript:'js',node:'js',ts:'js',typescript:'js',
               shell:'bash',zsh:'bash',powershell:'bash',ps1:'bash',curl:'bash',
               mysql:'sql',postgres:'sql',yml:'bash',yaml:'bash'};
  const rules=HLRULES[key]||HLRULES[alias[key]]||_hlGeneric(code);
  if(!code||code.length>40000)return esc(code);   // 超大文本不高亮，避免卡顿
  let re;
  try{re=new RegExp(rules.map(r=>'('+r[1].source+')').join('|'),'gm');}catch(e){return esc(code);}
  let out='',last=0,m,guard=0;
  while((m=re.exec(code))&&guard++<20000){
    out+=esc(code.slice(last,m.index));
    let gi=0;for(let i=1;i<=rules.length;i++){if(m[i]!==undefined){gi=i;break;}}
    out+='<span class="tk-'+rules[gi-1][0]+'">'+esc(m[0])+'</span>';
    last=m.index+m[0].length;
    if(m[0]==='')re.lastIndex++;
  }
  return out+esc(code.slice(last));
}
function codeBlock(code,lang){
  const ln=(lang||'code');const raw=code.replace(/\n$/,'');
  return '<div class="codebox lang-'+esc(ln)+'"><div class="codehead"><span class="lang '+esc(ln)+'">'+esc(ln)+'</span><button class="cp" onclick="copyCode(this)">⧉ 复制</button></div><pre class="code"><code>'+hl(raw,ln)+'</code></pre></div>';
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

async function loadVideoMode(){try{const d=await (await fetch('/api/video/mode')).json();if(d.ok){const sel=document.getElementById('vengine');sel.value=d.mode||'api';updateVmodeH();}}catch(e){}}
function updateVmodeH(){const sel=document.getElementById('vengine');const h=document.getElementById('vmodeH');if(!h)return;
  if(sel.value==='api'){h.textContent='（云端，约1-2分钟，省显存）';}
  else{h.textContent='（本地，需显存切换）';}}
async function setVideoMode(v){try{const d=await (await fetch('/api/video/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:v})})).json();if(d.ok)updateVmodeH();}catch(e){}}
async function openVideo(){document.getElementById('videoBg').style.display='flex';const i=document.getElementById('vq');i.value='';i.focus();document.getElementById('vpv').style.display='none';document.getElementById('vconfirm').style.display='none';loadVideoMode();}
function closeVideo(){document.getElementById('videoBg').style.display='none';}
async function startVideo(){const q=document.getElementById('vq').value.trim();if(!q){return;}
  const pv=document.getElementById('vpv');pv.style.display='block';pv.innerHTML='⏳ 小焦正在精炼提示词…';document.getElementById('vconfirm').style.display='none';
  try{const d=await (await fetch('/api/video/refine?prompt='+encodeURIComponent(q))).json();
   pv.innerHTML='<div style="font-size:12px;color:#8b93a3;margin-bottom:4px">✅ 小焦改写后的提示词（英文给视频模型，画质最好）：</div><div style="font-size:13px;color:#a78bfa;line-height:1.6;background:#0e1116;border:1px solid #2a3140;border-radius:8px;padding:8px">'+esc(d.refined)+'</div><div style="font-size:12px;color:#8b93a3;margin-top:6px">📖 中文大意：<span style="color:#cbd0dc">'+esc(d.zh||'电影级画面、柔和光线、清晰细节、顺滑运镜。')+'</span></div><div style="font-size:12px;color:#6e7681;margin-top:6px">满意就点「确认并生成」；不满意重新输入。</div>';
   document.getElementById('vconfirm').style.display='flex';
   window._vq=q; window._vr=d.refined;
  }catch(e){pv.innerHTML='⚠️ '+esc(e.message);}}
async function confirmVideo(){const q=window._vq||'', rf=window._vr||'';
  const sel=document.getElementById('vengine');const isApi=sel&&sel.value==='api';
  // api 模式用原话(云端直接生成, 不绕本地精炼); local 模式用精炼后的英文提示词
  const usePrompt=isApi?q:(rf||q);
  closeVideo();
  const m=document.createElement('div');m.className='m bot';m.innerHTML='<div class="b">🎬 小焦'+(isApi?'已发起云端视频生成…':'已学习，正在切换视频模型…')+'</div>';feed.appendChild(m);feed.scrollTop=feed.scrollHeight;
  const b=m.querySelector('.b');
  try{const d=await (await fetch('/api/video',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:q,refined:usePrompt})})).json();
   if(d.busy){b.innerHTML='⏳ 正在生成/切换模型中，请稍候…';return;}
   if(!d.ok){b.innerHTML='⚠️ '+esc(d.error||'启动失败');return;}
   try{localStorage.setItem('xj_video_job',d.job);}catch(e){}
   let n=0,sp=false;
   const iv=setInterval(async()=>{n++;
     try{const st=await (await fetch('/api/video/status?job='+d.job)).json();
      if(st.state==='done'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){}
        b.innerHTML='<video src="'+st.url+'" controls style="max-width:100%;border-radius:12px"></video><div style="font-size:12px;color:#8b93a3;margin-top:6px">🎬 真·AI 视频</div>'+(st.refined_prompt?'<div class="vpvmini" style="margin-top:4px">📝 提示词：<span style="color:#a78bfa">'+esc(st.refined_prompt)+'</span></div>':'');feed.scrollTop=feed.scrollHeight;
        try{fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:'小焦',content:'🎬 视频生成完成：\n[video]'+st.url+'[/video]'+(st.refined_prompt?'\n📝 提示词：'+st.refined_prompt:'')})});}catch(e){}}
      else if(st.state==='error'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){};b.innerHTML='⚠️ '+esc(st.message||'生成失败');}
      else if((st.refined_prompt)&&!sp){b.innerHTML='🎬 正在生成视频…<div class="vpvmini" style="margin-top:6px">📝 用提示词：<span style="color:#a78bfa">'+esc(st.refined_prompt)+'</span></div>';sp=true;}
      else if(st.state==='unknown'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){}b.textContent='（视频任务已结束，如需生成请重新点 🎬）';}
      else if(n*5>2700){clearInterval(iv);b.innerHTML='⏱️ 超时，到 ComfyUI(8188) 看是否完成。';}
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
      else if(st.state==='unknown'){clearInterval(iv);try{localStorage.removeItem('xj_video_job');}catch(e){}m.remove();}
      else if(n*5>2700){clearInterval(iv);b.textContent='⏱️ 超时，到 8188 看是否完成。';}
      else{var pr=(st.progress&&st.progress.max)?Math.round(100*st.progress.value/st.progress.max):0;var msg='🎬 '+((st.message||"生成中…")+(pr?'（第 '+st.progress.value+'/'+st.progress.max+' 步，'+pr+'%）':''))+'（已等 '+Math.round(n*5)+'s）';b.textContent=msg;if(pr>0){var bar=b.nextElementSibling;if(!bar||!bar.classList.contains("pvbar")){bar=document.createElement("div");bar.className="pvbar";b.after(bar);}bar.style.width=pr+"%";}}
    }catch(e){}
  },5000);
}
function loadCost(){try{fetch('/api/cost').then(r=>r.json()).then(d=>{
  const el=document.getElementById('costBadge');if(el)el.textContent='💸 今日节省 ¥'+d.saved+' · '+d.calls+'次';
});}catch(e){}}
// ===== 预设：选中即生效（人格 + 大脑 + 工具开关）=====
// 真实缺陷：以前 loadPresets() 里 `if(d.current)sel.value=d.current` 写在判空之外，
// 而页面上根本没有 #presetSel 元素 → 抛 TypeError 被外层 try/catch 吞掉；
// loadPresetCards() 又从来没人调用 → "Agent 预设"面板永远空白，用户设置半天"跟没生效一模一样"。
function toast(msg,ms){let t=document.getElementById('toast');
  if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('show');clearTimeout(t._tm);
  t._tm=setTimeout(()=>t.classList.remove('show'),ms||2600);}
function fillPresetSelect(list,current,currentName){
  const sel=document.getElementById('presetSel');if(!sel)return;
  sel.innerHTML='<option value="">🎭 预设</option>'+(list||[]).map(p=>'<option value="'+esc(p.file)+'">🎭 '+esc(p.name)+'</option>').join('');
  if(current){sel.value=current;}
  // 名字也匹配一次：万一后端只给到"显示名"（老接口/自定义预设），别让下拉掉回占位项
  if(!sel.value&&currentName){const hit=(list||[]).find(p=>p.name===currentName);if(hit)sel.value=hit.file;}
  if(!sel.value){const hit=(list||[]).find(p=>p.name===current);if(hit)sel.value=hit.file;}
}
function loadPresets(){try{fetch('/api/presets').then(r=>r.json()).then(d=>{
  window._presets=d.presets||[];
  fillPresetSelect(window._presets,d.current||'',d.current_name||'');
  const h=document.getElementById('cmpHint');
  if(h&&(d.current_name||d.current))h.textContent='当前预设：'+(d.current_name||d.current)+' · 联网检索 · 抓取网页 · 查 NVD 漏洞 · 写文件';
  loadPresetCards();
}).catch(()=>{});}catch(e){}}
function _afterPreset(d,file){
  if(!d.ok){toast('⚠️ 加载失败：'+(d.error||''));return;}
  const c=d.capabilities||{};
  toast('✅ 已切换预设：'+(d.preset||'')+' · 联网'+(c.web_search===false?'关':'开')
        +' · 工具'+(c.run_tools===false?'关':'开'),3800);
  const h=document.getElementById('cmpHint');
  if(h)h.textContent='当前预设：'+(d.preset||'')+' · 人格与工具开关已立即生效';
  setToolsOn(c.run_tools!==false);
  loadPresets();
}
function selectPreset(file){if(!file)return;
  fetch('/api/presets/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:file})})
   .then(r=>r.json()).then(d=>_afterPreset(d,file)).catch(e=>toast('⚠️ 加载失败：'+e));}
function loadPresetCards(){try{fetch('/api/presets').then(r=>r.json()).then(d=>{
  window._presets=d.presets||[];
  const el=document.getElementById('presetCards');if(!el)return;
  el.innerHTML=window._presets.length?window._presets.map(p=>'<div class="pcard'+(p.file===d.current?' on':'')+'" onclick="loadPreset(\''+esc(p.file)+'\')">'+
    '<div class="pinfo"><span class="pname">'+esc(p.name)+'</span><span class="ptag">'+(p.file===d.current?'当前使用':'点击切换')+'</span></div>'+
    '<div class="pdesc">'+esc(p.desc||'')+'</div><div class="pfile">'+esc(p.file)+'</div>'+
    '<div class="picons"><span title="编辑" onclick="event.stopPropagation();editPreset(\''+esc(p.file)+'\')">✏️</span><span title="复制" onclick="event.stopPropagation();duplicatePreset(\''+esc(p.file)+'\')">⧉</span><span title="使用" onclick="event.stopPropagation();loadPreset(\''+esc(p.file)+'\')">📂</span><span title="删除" onclick="event.stopPropagation();delPreset(\''+esc(p.file)+'\')">🗑️</span></div></div>').join('')
    :'<div class="think">还没有预设，点下面「＋ 创作自定义预设」</div>';
}).catch(()=>{});}catch(e){}}
function loadPreset(file){fetch('/api/presets/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:file})})
  .then(r=>r.json()).then(d=>_afterPreset(d,file)).catch(e=>toast('⚠️ 加载失败：'+e));}
function duplicatePreset(file){fetch('/api/presets',{method:'GET'}).then(r=>r.json()).then(async d=>{const p=(d.presets||[]).find(x=>x.file===file);const n=p?(p.name+'·副本'):'新预设';await fetch('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,parent:file})});loadPresetCards();});}
function editPreset(file){fetch('/api/presets/detail?file='+file).then(r=>r.json()).then(d=>{
  const x=d.data||{};
  document.getElementById('eName').value=x.name||'';document.getElementById('eRole').value=x.role||'';
  document.getElementById('eEngine').value=(x.brain&&x.brain.engine)||'auto';document.getElementById('eCtx').value=(x.brain&&x.brain.llama&&x.brain.llama.ctx)||20000;
  const cap=x.capabilities||{};document.getElementById('eSearch').checked=cap.web_search!==false;document.getElementById('eMem').checked=cap.memory!==false;document.getElementById('eTools').checked=cap.run_tools!==false;
  document.getElementById('eTemp').value=(x.behavior&&x.behavior.temperature)||0.7;document.getElementById('eMax').value=(x.behavior&&x.behavior.max_tokens)||1024;
  window._efile=file;document.getElementById('editBg').style.display='flex';});}
function closeEdit(){document.getElementById('editBg').style.display='none';}
function savePreset(){const data={name:document.getElementById('eName').value, role:document.getElementById('eRole').value,
  brain:{engine:document.getElementById('eEngine').value, llama:{ctx:+document.getElementById('eCtx').value||20000}},
  capabilities:{web_search:document.getElementById('eSearch').checked, memory:document.getElementById('eMem').checked, run_tools:document.getElementById('eTools').checked},
  behavior:{temperature:+document.getElementById('eTemp').value||0.7, max_tokens:+document.getElementById('eMax').value||1024}};
  fetch('/api/presets/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:window._efile,data:data})}).then(r=>r.json()).then(()=>{fetch('/api/presets/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:window._efile})}).then(()=>{alert('✅ 已保存并应用此预设(人格已切换)');closeEdit();location.reload();});});}
function delPreset(file){if(!confirm('确定删除预设 '+file+' 吗？'))return;fetch('/api/presets/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:file})}).then(r=>r.json()).then(()=>{alert('✅ 已删除');loadPresetCards();}).catch(e=>alert('删除失败：'+e));}
function createPreset(){fetch('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'我的预设',parent:'default.json'})}).then(r=>r.json()).then(d=>{alert('已创建自定义预设（可到 presets/ 编辑，或选它试试）');loadPresetCards();});}
function openEnv(){document.getElementById('envBg').style.display='flex';loadEnv();}
function closeEnv(){document.getElementById('envBg').style.display='none';}
async function loadEnv(){try{const d=await (await fetch('/api/env')).json();
  const el=document.getElementById('envList');
  el.innerHTML=d.items.map(function(i){return '<div class="envitem '+(i.ok?'ok':'no')+'"><div class="st">'+(i.ok?'✓':'✗')+'</div><div class="nm">'+esc(i.name)+'<div class="inf">'+esc(i.info)+(i.ok?'':'<div style="color:#f0a848;margin-top:4px">🔧 '+esc(i.need)+(i.dl?'<br><a href="'+esc(i.dl)+'" target="_blank" style="color:#a78bfa">⬇ 去下载</a>':'')+'</div>')+'</div></div></div>';}).join('');
  const b=document.createElement('div');b.className='envitem '+(d.ok?'ok':'no');b.innerHTML='<div class="st">'+(d.ok?'✓':'✗')+'</div><div class="nm">'+(d.ok?'✅ 环境齐全，可直接用':'⚠️ 有 '+((d.missing||[]).length)+' 项待处理')+'</div>';el.appendChild(b);
 }catch(e){document.getElementById('envList').textContent='检测失败';}}
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
  // 电影设计提示词学习库(视频精炼, 小脑越用越准)
  try{const pk=await (await fetch('/api/video/promptkb')).json();
    const el=document.getElementById('brainPrompt');
    if(el){el.innerHTML='<div class="bli think">🎬 电影设计提示词学习库：<b style="color:#a78bfa">已学 '+pk.count+' 条</b></div>'+
      (pk.recent||[]).map(x=>'<div class="bli">📝 '+esc(String(x).slice(0,120))+'</div>').join('');}
  }catch(e){}
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
function stripThink(t){                 // 兜底：模型偶尔把 <think></think> 吐进正文
  return (t||'').replace(/<(think|thinking|reasoning)>[\s\S]*?<\/\1>/gi,'')
                .replace(/<\/?(think|thinking|reasoning)>/gi,'')
                .replace(/\n{3,}/g,'\n\n').trim();
}
function renderTableBlock(text){
  // 把一组以 | 开头的行转成 <table>（外面套一层横向滚动容器：宽表也不挤）
  const rows=text.split('\n').filter(l=>l.trim().startsWith('|'));
  if(rows.length<2)return null;
  const clean=l=>l.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(c=>c.trim());
  let html='<table>';
  rows.forEach((r,i)=>{const cells=clean(r);if(cells.every(c=>!c.replace(/[-:]/g,'')))return;const tag=i===0?'th':'td';
    html+='<tr>'+cells.map(c=>'<'+tag+'>'+inline(esc(c))+'</'+tag+'>').join('')+'</tr>';});
  return '<div class="tblwrap">'+html+'</table></div>';
}
function renderMd(text){
  text=stripThink(text||'');
  const fence=/```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g;
  let out='',last=0,m;
  while((m=fence.exec(text))){
    const seg=text.slice(last,m.index);
    const lang=(m[1]||'').toLowerCase();
    out+=renderBlocks(seg);
    // ```markdown / ```md 里本来就是 Markdown（表格/标题/列表），渲染出来比当代码显示好读得多；
    // 其它语言（json/python/…）仍按代码块显示，保留「⧉ 复制」按钮。
    if(lang==='markdown'||lang==='md'){
      out+='<div class="mdfence">'+renderBlocks(m[2])+'</div>';
    }else{
      out+=codeBlock(m[2],m[1]);
    }
    last=fence.lastIndex;
  }
  out+=renderBlocks(text.slice(last));
  return out;
}
function renderBlocks(seg){
  // 按空行分块；识别：分割线/引用块/表格，否则行内 md
  if(!seg)return '';
  let blocks=seg.split(/\n\s*\n/),html='';
  blocks.forEach((b,bi)=>{
    if(bi>0)html+='<br>';          // 空行分块 → 块之间补一个换行，避免段落粘连成一行
    const line=(b||'').trim();
    // --- / *** / ___ 分割线
    if(/^([-*_])\1{2,}\s*$/.test(line)){html+='<hr style="border:none;border-top:1px solid #2a3140;margin:12px 0">';return;}
    // #/#/### 标题
    const hm=b.match(/^(#{1,3})\s+(.+)/);
    if(hm){html+='<div class="mdh">'+esc(hm[2])+'</div>';return;}
    // > 引用块(多行)
    const qm=b.match(/^((?:\s*>.*\n?)+)/);
    if(qm){
      const inner=qm[1].split('\n').map(l=>l.replace(/^\s*>\s?/,'')).join('\n');
      html+='<blockquote style="border-left:3px solid #405a99;margin:6px 0;padding:2px 12px;color:#aab2c0;background:#131a2b;border-radius:8px">'+inline(esc(inner))+'</blockquote>';
      return;
    }
    const t=renderTableBlock(b);
    html+= t?t:inline(esc(b));
  });
  return html;
}
function add(role,text,src){const w=document.querySelector('#feed .welcome');if(w)w.remove();
 const m=document.createElement('div');m.className='m '+role;
 let vm='';text=(''+text);
 if(role==='bot'&&text.indexOf('[video]')>=0){const mu=text.match(/\[video\]([^\[\]]+)\[\/video\]/);if(mu){vm='<video src="'+esc(mu[1])+'" controls style="max-width:100%;border-radius:12px;margin:4px 0"></video>';text=text.replace(mu[0],'');}}
 if(role==='bot'&&text.indexOf('[music]')>=0){const mu=text.match(/\[music\]([^\[\]]+)\[\/music\]/);if(mu){vm+='<audio src="'+esc(mu[1])+'" controls style="width:100%;margin:4px 0"></audio>';text=text.replace(mu[0],'');}}
 const _html=(role==='bot'?renderMd(text):esc(text));
 m.innerHTML='<div class="b'+(_html.indexOf('<table')>=0?' wide':'')+'">'+_html+'</div>'+vm;
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
  typeAnswer(d.answer,d.sources||[],d.log_id,d.grounding_note||'');
  if(d.needs_confirm){const m=document.createElement('div');m.className='m bot';
    m.innerHTML='<button class="icon-btn" onclick="confirmAction()">✅ 确认执行</button>';feed.appendChild(m);}}
 catch(e){clearInterval(timer);th.remove();add('bot','⚠️ 出错了：'+e.message);}
 loadSessions();
 feed.scrollTop=feed.scrollHeight;}
// 打字机式浮现回答
function typeAnswer(text,src,logId,note){
  const m=document.createElement('div');m.className='m bot';
  text=stripThink(text);
  m.innerHTML='<div class="b"></div>';const b=m.querySelector('.b');feed.appendChild(m);
  let i=0;const step=Math.max(1,Math.round(text.length/120));const rl=setInterval(()=>{
    i+=step;b.innerHTML='';b.appendChild(document.createTextNode(text.slice(0,i)));
    feed.scrollTop=feed.scrollHeight;
    if(i>=text.length){clearInterval(rl);const bm=m.querySelector('.b');
      const full=renderMd(text);
      if(full.indexOf('<table')>=0){bm.classList.add('wide');m.classList.add('widem');}
      bm.innerHTML=full;
      // 注：检索引用校验的徽标已按用户要求撤掉（正常聊天里太吵，见过"这条回答基本没用到
      // 检索资料"的打扰提示）。核对数据仍在 /api/chat 的 grounding 字段里，压测与
      // 「查看来源」照常使用，所以 note 参数保留但不再渲染。
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
  // 没有显式配置模型时，不要显示"未配置模型"误导用户 —— 自动模式其实用的是本地大脑/llama-swap
  if(!ms.length){
    const auto=d&&d.active==='auto';
    sel.innerHTML='<option value="">'+(auto?'自动（本地大脑 :9292）':'未配置模型（点「设置」添加）')+'</option>';
    return;
  }
  sel.innerHTML=ms.map(m=>'<option value="'+esc(m.name)+'">'+esc(m.name)+'</option>').join('');
  sel.value=(d&&d.current)||((ms[0]&&ms[0].name)||'');}catch(e){document.getElementById('modelSel').innerHTML='<option value="">模型加载失败</option>';}}
async function selectModel(){const v=document.getElementById('modelSel').value;
  try{const r=await fetch('/api/model/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v})});
    const d=await r.json();
    if(d&&d.note)toast((d.ok?'✅ ':'⚠️ ')+d.note);          // 选中就能立刻知道能不能用、坏在哪
  }catch(e){toast('⚠️ 切换失败：'+e);}}
async function loadHistory(){try{const r=await fetch('/api/history');const hs=await r.json();if(Array.isArray(hs)&&hs.length){hs.forEach(h=>add(h.role==='用户'?'user':'bot',h.content));}}catch(e){}}
async function loadSessions(){try{const r=await fetch('/api/sessions');const d=await r.json();const el=document.getElementById('sessionList');
  el.innerHTML=(d.sessions||[]).map(s=>'<div class="srow'+(s.id===d.current?' active':'')+'">'
      +'<button class="sess" onclick="openSession(\''+s.id+'\')" title="'+esc(String(s.count||0))+' 条消息">'+esc(s.title||'新对话')+'</button>'
      +'<button class="sdel" title="删除这个会话" onclick="delSession(event,\''+s.id+'\')">✕</button>'
    +'</div>').join('')||'<div class="think">暂无会话</div>';}catch(e){}}
async function delSession(ev,id){
  if(ev){ev.stopPropagation();ev.preventDefault();}
  if(!confirm('删除这个会话？该会话的聊天记录会一起删掉（不可恢复）'))return;
  try{
    const r=await fetch('/api/session/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})});
    const d=await r.json();
    if(!d.ok){toast('⚠️ '+(d.error||'删除失败'));return;}
    toast('🗑️ 已删除会话');
    await loadSessions();
    if(d.was_current){clearFeed();await loadHistory();}     // 删的是当前会话 → 界面跟着切过去
  }catch(e){toast('⚠️ 删除失败：'+e);}
}
async function newChat(){await fetch('/api/session/new',{method:'POST'});clearFeed();loadSessions();}
async function openSession(id){const r=await fetch('/api/session/'+id);const d=await r.json();clearFeed();(d.messages||[]).forEach(h=>add(h.role==='用户'?'user':'bot',h.content));loadSessions();}
function clearFeed(){document.getElementById('feed').innerHTML='';renderWelcome();}
// 空状态：居中的欢迎卡 + 可点的示例（比一行灰字好看，也让新用户知道能干什么）
function renderWelcome(){
  const f=document.getElementById('feed');if(!f||f.children.length)return;
  const chips=[['抓取最近 7 天的高危漏洞','🔐 查漏洞'],['最近 AI 新闻','📰 搜新闻'],
               ['抓一下 https://example.com','🌐 抓网页'],['用 Python 写个计算斐波那契的脚本','🐍 写代码'],
               ['记住：我偏好用中文注释','🧠 存记忆']];
  f.innerHTML='<div class="welcome"><div class="wl">你好，我是小焦 🐳</div>'
    +'<div class="ws">本地部署 · 会联网检索 · 会抓网页 · 会查 NVD 漏洞 · 会写文件；每一步都能在左侧「轨迹」里看到</div>'
    +'<div class="chips">'+chips.map(c=>'<div class="chip" onclick="useChip(this)">'+esc(c[1])+'</div>').join('')+'</div></div>';
}
function useChip(el){
  const map={'🔐 查漏洞':'抓取最近 7 天的高危漏洞','📰 搜新闻':'最近 AI 新闻','🌐 抓网页':'抓一下 https://example.com',
             '🐍 写代码':'用 Python 写个计算斐波那契的脚本','🧠 存记忆':'记住：我偏好用中文注释'};
  inp.value=map[el.textContent.trim()]||el.textContent.trim();autoGrow(inp);inp.focus();
}
function autoGrow(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,220)+'px';}
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('hidden');}
function hideSplash(){const sp=document.getElementById('splash');if(sp){sp.style.transition='opacity .5s';sp.style.opacity='0';setTimeout(function(){sp.remove();},500);}}
  (async()=>{try{loadModels();}catch(e){}try{loadHistory();}catch(e){}try{loadSessions();}catch(e){}try{loadPresets();}catch(e){}try{loadCost();}catch(e){}
 try{const r=await fetch('/api/tools_toggle');const d=await r.json();setToolsOn(d.tools_on);}catch(e){}
 try{renderWelcome();}catch(e){}
 try{const ta=document.getElementById('inp');if(ta)autoGrow(ta);}catch(e){}
 resumeVideoJob();resumeChat();})();
async function confirmAction(){const r=await fetch('/api/confirm',{method:'POST'});const d=await r.json();
 add('bot',(d.result||'已执行').slice(0,1200));}
inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});

const plugEl=document.getElementById('s_plugins');let plugins=[];
function applyPersona(){const v=document.getElementById('s_persona').value;if(!v)return;
  fetch('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:v})}).then(r=>r.json()).then(d=>{const m=document.getElementById('personaMsg');if(m)m.textContent=d.ok?'✅ 人格已切换（下次对话生效）':'❌ '+d.error;});
}

async function openSettings(){try{loadPresetCards();}catch(e){}
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
async function addLocalModel(){document.getElementById('addLocalBg').style.display='flex';}
function closeAddLocal(){document.getElementById('addLocalBg').style.display='none';}
function saveAddLocal(){
  const name=document.getElementById('lm_name').value.trim();
  const gguf=document.getElementById('lm_gguf').value.trim();
  const ctx=document.getElementById('lm_ctx').value;
  if(!name||!gguf){alert('请填模型名和 GGUF 路径');return;}
  document.getElementById('lm_msg').textContent='⏳ 正在配置并重启 llama-swap…';
  fetch('/api/model/addlocal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,gguf:gguf,ctx:parseInt(ctx)||20000})}).then(r=>r.json()).then(d=>{
    document.getElementById('lm_msg').textContent=d.ok?('✅ 已添加：'+d.name+'，llama-swap 重启中，约10秒后可用'):('❌ '+d.error);
    if(d.ok)setTimeout(()=>location.reload(),12000);
  });
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
    # 启动即预热"路径自动探测"（ComfyUI / llama-swap / 视频模型），体检页面秒开、不卡盘
    try:
        _discover_paths(kick=True)
        print("  🔎 路径自动探测已在后台预热(ComfyUI / llama-swap / 视频模型)")
    except Exception as e:
        LOG.debug("忽略异常(%s:%d): %s", __file__, 3608, e)
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
