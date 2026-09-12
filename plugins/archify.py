# plugins/archify.py
# Archify 完整能力桥接层（基于真实 CLI 设计）
# 验证时间：2026-09-13
# 验证版本：archify 2.14.0 / Node 环境已确认
import os
import re
import json
import time
import uuid
import shutil
import logging
import threading
import subprocess
from collections import defaultdict

LOG = logging.getLogger("archify")
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[archify] %(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(_h)

# ============ 路径（全部基于真实环境） ============
ARCHIFY_ROOT = r"C:\Users\Jiao\.dsh\profiles\web\node_modules\@tt-a1i\archify-dsh\skills\archify"
ARCHIFY_BIN = os.path.join(ARCHIFY_ROOT, "bin", "archify.mjs")
SCHEMAS_DIR = os.path.join(ARCHIFY_ROOT, "schemas")
EXAMPLES_DIR = os.path.join(ARCHIFY_ROOT, "examples")
SKILL_MD = os.path.join(ARCHIFY_ROOT, "SKILL.md")

PROJECT_ROOT = r"C:\xiaojiao\xiaojiao harness"
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "logs", "diagrams")
CACHE_DIR = os.path.join(PROJECT_ROOT, "logs", ".archify_cache")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")          # 校验失败日志落这里（archify_validate.log）
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# 真实存在的 5 种类型
DIAGRAM_TYPES = ("architecture", "workflow", "sequence", "dataflow", "lifecycle")

# 真实 CLI 命令（来自 --help 输出）
CLI_COMMANDS = {
    "render", "compare", "deliver", "preview", "validate",
    "inspect", "check", "visual-check", "guide", "examples",
    "doctor", "demo",
}

# 超时分级（秒）
TIMEOUT_QUICK = 30        # doctor / examples
TIMEOUT_READ = 60         # guide / inspect
TIMEOUT_VALIDATE = 180    # validate
TIMEOUT_RENDER = 240      # render / preview
TIMEOUT_DELIVER = 300     # deliver
TIMEOUT_CHECK = 120       # check / visual-check
TIMEOUT_COMPARE = 300     # compare

OUTPUT_RETENTION_DAYS = 7
MAX_OUTPUT_FILES = 300

# ============ 线程安全 ============
_LOCK = threading.RLock()

# ============ 缓存 ============
_CACHE = {
    "skill_md": None,
    "schemas": {},       # {type: content}
    "examples": {},      # {type: content}
    "common": None,
    "schemas_readme": None,
    "doctor": None,
    "examples_list": None,
}

# ============ 指标 ============
_METRICS = defaultdict(lambda: {"calls": 0, "ok": 0, "fail": 0, "total_s": 0.0, "max_s": 0.0})

def _metric(tool, ok, dt):
    m = _METRICS[tool]
    m["calls"] += 1
    m["ok" if ok else "fail"] += 1
    m["total_s"] += dt
    m["max_s"] = max(m["max_s"], dt)

def _metrics_snapshot():
    out = {}
    for k, v in _METRICS.items():
        out[k] = {
            "calls": v["calls"],
            "ok": v["ok"],
            "fail": v["fail"],
            "avg_s": round(v["total_s"] / v["calls"], 3) if v["calls"] else 0,
            "max_s": round(v["max_s"], 3),
        }
    return out


# ============ 路径安全 ============
# 允许中文名：真实缺陷（用户实测）——模型很自然会拿"小焦系统架构"当输出名，
# 老规则只认 [A-Za-z0-9_-.]+，于是交付直接失败（"非法文件名：小焦系统架构"）。
# 安全靠下面两道闸（basename 去目录 + 结果必须落在 OUTPUT_DIR 内 + 禁 .. 与路径分隔符），
# 而不是靠"只准英文"。
_SAFE_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,120}$")

def _safe_output_path(name, default_ext=".html"):
    if not name:
        return os.path.join(OUTPUT_DIR, f"diagram_{uuid.uuid4().hex[:8]}{default_ext}")
    name = os.path.basename(str(name).strip())
    if not _SAFE_NAME.match(name) or ".." in name:
        raise ValueError(f"非法文件名：{name}（不能含路径分隔符或 ..）")
    if not name.lower().endswith(default_ext):
        name += default_ext
    p = os.path.abspath(os.path.join(OUTPUT_DIR, name))
    if not p.startswith(os.path.abspath(OUTPUT_DIR)):
        raise ValueError("输出路径越界")
    return p


# ============ 输出清理 ============
def _cleanup_outputs():
    try:
        files = []
        now = time.time()
        for f in os.listdir(OUTPUT_DIR):
            p = os.path.join(OUTPUT_DIR, f)
            if not os.path.isfile(p):
                continue
            age_days = (now - os.path.getmtime(p)) / 86400
            if age_days > OUTPUT_RETENTION_DAYS:
                try:
                    os.remove(p)
                except Exception:
                    pass
            else:
                files.append((os.path.getmtime(p), p))
        if len(files) > MAX_OUTPUT_FILES:
            files.sort()
            for _, p in files[: len(files) - MAX_OUTPUT_FILES]:
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception as e:
        LOG.warning("输出清理失败：%s", e)


# ============ 缓存读取 ============
def _load_skill():
    with _LOCK:
        if _CACHE["skill_md"] is None:
            if not os.path.exists(SKILL_MD):
                raise FileNotFoundError(f"SKILL.md 不存在：{SKILL_MD}")
            with open(SKILL_MD, encoding="utf-8") as f:
                _CACHE["skill_md"] = f.read()
        return _CACHE["skill_md"]

def _load_schema(t):
    with _LOCK:
        if t not in _CACHE["schemas"]:
            p = os.path.join(SCHEMAS_DIR, f"{t}.schema.json")
            if not os.path.exists(p):
                _CACHE["schemas"][t] = f"未找到 {t}.schema.json"
            else:
                with open(p, encoding="utf-8") as f:
                    _CACHE["schemas"][t] = f.read()
        return _CACHE["schemas"][t]

def _load_common():
    with _LOCK:
        if _CACHE["common"] is None:
            p = os.path.join(SCHEMAS_DIR, "common.schema.json")
            if not os.path.exists(p):
                _CACHE["common"] = "common.schema.json 不存在"
            else:
                with open(p, encoding="utf-8") as f:
                    _CACHE["common"] = f.read()
        return _CACHE["common"]

def _load_schemas_readme():
    with _LOCK:
        if _CACHE["schemas_readme"] is None:
            p = os.path.join(SCHEMAS_DIR, "README.md")
            if not os.path.exists(p):
                _CACHE["schemas_readme"] = "schemas/README.md 不存在"
            else:
                with open(p, encoding="utf-8") as f:
                    _CACHE["schemas_readme"] = f.read()
        return _CACHE["schemas_readme"]

def _load_example(t):
    """真实命名规则：{name}.{type}.json"""
    with _LOCK:
        if t not in _CACHE["examples"]:
            content = None
            if os.path.isdir(EXAMPLES_DIR):
                for f in os.listdir(EXAMPLES_DIR):
                    if f.endswith(f".{t}.json"):
                        with open(os.path.join(EXAMPLES_DIR, f), encoding="utf-8") as fp:
                            content = fp.read()
                        break
            if content is None:
                content = f"未找到 .{t}.json 示例文件"
            _CACHE["examples"][t] = content
        return _CACHE["examples"][t]


# ============ 子进程执行 ============
def _run_node(args, timeout, cwd=ARCHIFY_ROOT):
    t0 = time.time()
    try:
        r = subprocess.run(
            ["node", ARCHIFY_BIN] + list(args),
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
            encoding="utf-8", errors="replace",
        )
        return r.returncode == 0, r.stdout or "", r.stderr or "", time.time() - t0
    except subprocess.TimeoutExpired:
        return False, "", f"超时（{timeout}s）", time.time() - t0
    except FileNotFoundError:
        return False, "", "未找到 node 命令，请确认 Node.js >= 18 已安装", time.time() - t0
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}", time.time() - t0


def _run_node_json(args, timeout):
    """带 --json 的命令，解析返回 JSON"""
    ok, out, err, dt = _run_node(args, timeout)
    if not ok:
        return False, None, err or out, dt
    try:
        return True, json.loads(out), "", dt
    except Exception as e:
        return False, None, f"JSON 解析失败：{e}\n原始：{out[:500]}", dt


def _write_spec(spec_json):
    uid = uuid.uuid4().hex[:8]
    p = os.path.join(CACHE_DIR, f"spec_{uid}.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(spec_json)
    return p


def _syntax_check(spec_json):
    try:
        json.loads(spec_json)
        return True, None
    except Exception as e:
        return False, f"JSON 语法错误：{e}"


# ============ validate 结果格式化 ============
def _fmt_validate(data):
    """把 validate 的 JSON 结果格式化成可读文本。

    **重要（性能根因）**：校验失败时把**所有**问题一次性列全，而不是只列前两条 ——
    模型以前只能看到头两条报错，改完再校验、再报两条、再改…实测同一张图来回校验 **7 次**、
    白烧 200+ 秒。现在输出「字段路径 + 错误类型 + 建议修法」的结构化清单，让模型**一次改完**。
    """
    lines = []
    ok = data.get("ok", False)
    lines.append(f"状态：{'PASS' if ok else 'FAIL'}")
    checks = data.get("checks", [])
    if checks:
        failed = [c for c in checks if not c.get("ok")]
        lines.append(f"\n【{len(checks)} 项检查｜失败 {len(failed)} 项】")
        for c in checks:
            mark = "✅" if c.get("ok") else "❌"
            lines.append(f"  {mark} {c.get('name')}")
            # 每一项的 details **全部**列出（原来只给 2 条，等于逼模型逐条试错）
            for d in c.get("details", []):
                lines.append(f"     {d}")
    comp = data.get("composition", {})
    if comp:
        s = comp.get("summary", {})
        lines.append(f"\n【合成】profile={comp.get('profile')} status={comp.get('status')}")
        lines.append(f"  errors={s.get('errors')} warnings={s.get('warnings')}")
        m = comp.get("metrics", {})
        if m:
            lines.append(f"  交叉={m.get('properCrossings')} 模糊走廊={m.get('ambiguousCorridors')} "
                         f"标签间距问题={m.get('labelRouteClearanceIssues')} 微段={m.get('microSegmentCount')}")
        issues = comp.get("issues", [])
        if issues:
            lines.append(f"\n【问题 {len(issues)} 条｜以下全部一次改完，不要逐条试】")
            for it in issues:                      # 不再截断到 10 条
                lines.append(f"  - {it.get('kind', '?')}: {it.get('message', '')[:150]}")
            lines.append(_suggest_fixes(issues))
    if not ok:
        lines.append("\n【一次改完，再校验一次】请把所有上面列出的问题一起修正后重新提交，"
                     "不要只改一条就重试。")
    return "\n".join(lines)


def _suggest_fixes(issues):
    """按问题类型给"建议修法"，让模型一次改到位（字段路径 + 错误类型 + 怎么改）。"""
    tips = {
        "label_route_clearance": "标签与连线路由冲突：把标签沿轴挪开 ≥12px，或改走另一条走廊",
        "relationship_crossings": "关系线交叉过多：调整节点顺序/分组，让同组节点相邻，减少跨组连线",
        "relationship_corridors": "走廊占用重复：给每条关系分配不同走廊（错开 1 格）",
        "ambiguous_corridors": "走廊语义模糊：减少并列走廊层数，或把同类关系合并为一条总线",
        "micro_segment": "出现过短线段：合并相邻共线节点，或把拐点对齐到栅格",
        "overlap": "元素重叠：增大节点间距或调整分区尺寸",
        "bounds": "超出画布：扩大画布或收紧元素间距",
        "orthogonal": "线段不正交：把折点对齐到 90°（Archify 只接受正交布线）",
    }
    kinds = []
    for it in issues:
        k = str(it.get("kind", "?"))
        if k not in kinds:
            kinds.append(k)
    if not kinds:
        return ""
    out = ["\n【建议修法】"]
    for k in kinds[:8]:
        out.append(f"  · {k} → {tips.get(k, '按上面的 message 定位到具体元素后修正')}")
    return "\n".join(out)


def _fix_hint(path, msg):
    """把一条 schema/校验报错翻成"该怎么改"的一句话（配合 _fmt_validate_error 用）。"""
    low = (msg or "").lower()
    if "additionalproperty" in low:
        prop = ""
        if "additionalProperty" in msg:
            prop = msg.split("additionalProperty")[-1].strip(" :\"}{")[:24]
        return "去掉不支持的字段 `%s`（schema 里没有它；位置按 schema 用允许的字段）" % (prop or "该字段")
    if "missingproperty" in low or "must have required property" in low:
        prop = ""
        if "missingProperty" in msg:
            prop = msg.split("missingProperty")[-1].strip(" :\"}{")[:24]
        return "补上必填字段 `%s`（先 archify_read_schema 看这个 type 的必填项）" % (prop or "缺失字段")
    if "must be equal to one of the allowed values" in low or "enum" in low:
        return "取值不在允许列表里（按 schema 的 enum 改）"
    if "must be array" in low or "must be object" in low or "must be string" in low:
        return "类型不对（按报错里说的类型改：array/object/string…）"
    if "minitems" in low or "at least" in low:
        return "条目太少（补足到 schema 要求的最少条数）"
    if "label" in low and ("overlap" in low or "clearance" in low):
        return ("标签与元素/连线重叠：给该关系加 `labelDy: 12`（或 -12）把标签挪开，"
                "或改 `labelAt`（`mid`/`start`/`end`），也可把相邻节点挪 20~40px；"
                "一次改完所有报它重叠的标签")
    if "label" in low and "segment" in low:
        return "标签压在连线上：给该关系设 `labelDy`（±12）或缩短该段走廊"
    if "crossing" in low:
        return "连线交叉过多：调整节点顺序/分组，让同组相邻"
    if "corridor" in low:
        return "走廊冲突：给每条关系错开一条走廊"
    if "orthogon" in low:
        return "存在非正交线段：把折点对齐到 90°"
    return "按报错里的 JSON 指针路径定位并修正"


def _fmt_validate_error(err_text, diagram_type=""):
    """把 validate 的**失败返回**整理成"一次能全改完"的清单。

    **这是"反复改 7 次"的真正根因**：node 校验失败时退出码非 0，原来的代码直接把整块
    JSON 原文甩回给模型（`校验失败（0.2s）：{"error":"…\\n  /components/0 …"}`）——
    转义的换行、几十条挤在一起，模型每次只能看清一两条，于是"改一条→校验→再改一条"。
    现在：把 error 字符串按行拆开、逐条列出「字段路径 + 报错 + 建议修法」，并明确要求一次改完。
    """
    raw = str(err_text or "").strip()
    data, body = {}, raw
    i = raw.find("{")
    if i >= 0:
        try:
            data = json.loads(raw[i:])
            body = str(data.get("error") or data.get("message") or raw[i:])
        except Exception:  # noqa: silent-ok — 不是 JSON 就按纯文本处理
            data = {}
    items = [l.strip(" ,") for l in body.replace("\\n", "\n").splitlines() if l.strip()]
    out = ["状态：FAIL", ""]
    if items:
        out.append("【全部报错 %d 条｜一次改完，不要逐条试】" % len(items))
        for it in items:
            out.append("  ❌ %s" % it[:220])
            hint = _fix_hint(it[:400], it)
            if hint:
                out.append("     → %s" % hint)
    else:
        out.append("【报错原文】\n%s" % raw[:1200])
    stage = data.get("stage")
    if stage:
        out.append("\n【阶段】%s" % stage)
    out.append("\n【一次改完，再校验一次】请把上面**所有**问题一起修正后重新提交；"
               "不要只改一条就重试（同一工具连续失败 3 次会被熔断）。")
    return "\n".join(out)


def _log_validate_fail(diagram_type, spec, text):
    """每次校验失败都把"失败原因摘要"记到 logs/archify_validate.log（便于复盘/统计）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        picked = []
        for l in str(text or "").splitlines():
            s = l.strip()
            if not s:
                continue
            if (s.startswith(("❌", "状态：", "【问题", "【", "- ", "· ")) or "❌" in s
                    or '"severity"' in s or '"message"' in s or '"kind"' in s
                    or "error" in s.lower() or "失败" in s):
                picked.append(s)
        if not picked:                    # 兜底：至少留下开头几行，别写一条空记录
            picked = [l.strip() for l in str(text or "").splitlines() if l.strip()][:6]
        with open(os.path.join(LOG_DIR, "archify_validate.log"), "a", encoding="utf-8") as f:
            f.write("[%s] type=%s spec_chars=%d 失败摘要行=%d\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), diagram_type, len(spec or ""), len(picked)))
            for l in picked[:30]:
                f.write("    %s\n" % l[:200])
    except Exception:  # noqa: silent-ok — 记日志失败不能影响校验本身
        pass


def _fmt_guide(data):
    """把 guide 的 JSON 结果格式化成可读文本"""
    if not data.get("ok"):
        return f"guide 失败：{data}"
    rec = data.get("recommendation", {})
    lines = [
        f"推荐类型：{rec.get('type')}",
        f"配方 ID：{rec.get('id')}",
        f"标题：{rec.get('title')}",
        f"置信度：{data.get('confidence')}",
        f"匹配信号：{data.get('matchedSignals')}",
        "",
        f"适用：{rec.get('useWhen')}",
        f"不适用：{rec.get('avoidWhen')}",
        "",
        "应包含：",
    ]
    for it in rec.get("include", []):
        lines.append(f"  - {it}")
    lines.append("")
    lines.append("官方提示词：")
    lines.append(rec.get("prompt", ""))
    alts = data.get("alternatives", [])
    if alts:
        lines.append(f"\n备选方案 {len(alts)} 个：")
        for a in alts[:3]:
            lines.append(f"  - {a.get('id')} ({a.get('type')}): {a.get('title')}")
    return "\n".join(lines)


# ============ 插件主类 ============
class ArchifyPlugin:

    def get_tool_descriptions(self):
        return [
            # 第 1 层：环境与知识
            {
                "name": "archify_doctor",
                "description": "检查 Archify 环境是否正常（Node版本、依赖、schema完整性）。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "archify_read_skill",
                "description": "读取 Archify 完整技能文档 SKILL.md。画图前必须读一次。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "archify_read_schema",
                "description": "读取指定类型的 schema + common schema。type: architecture/workflow/sequence/dataflow/lifecycle",
                "parameters": {
                    "type": "object",
                    "properties": {"diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)}},
                    "required": ["diagram_type"],
                },
            },
            {
                "name": "archify_read_example",
                "description": "读取指定类型的完整示例 JSON，照它的结构生成。",
                "parameters": {
                    "type": "object",
                    "properties": {"diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)}},
                    "required": ["diagram_type"],
                },
            },
            # 第 2 层：智能路由
            {
                "name": "archify_guide",
                "description": "按场景返回推荐图表类型、配方、官方提示词。中文场景可加 lang=zh。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scenario": {"type": "string"},
                        "lang": {"type": "string", "enum": ["en", "zh"]},
                    },
                    "required": ["scenario"],
                },
            },
            # 第 3 层：质量保障
            {
                "name": "archify_validate",
                "description": "校验 JSON 是否符合 Archify 规范，返回 9 项检查 + 合成结果。0 错误才可交付。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)},
                        "spec_json": {"type": "string"},
                        "quality": {"type": "string", "enum": ["standard", "showcase"]},
                    },
                    "required": ["diagram_type", "spec_json"],
                },
            },
            {
                "name": "archify_inspect",
                "description": "结构检查，查看 JSON 的图结构是否符合规范（不渲染）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)},
                        "spec_json": {"type": "string"},
                    },
                    "required": ["diagram_type", "spec_json"],
                },
            },
            {
                "name": "archify_check",
                "description": "检查已生成的 HTML 文件是否正常。",
                "parameters": {
                    "type": "object",
                    "properties": {"html_path": {"type": "string"}},
                    "required": ["html_path"],
                },
            },
            {
                "name": "archify_visual_check",
                "description": "对已生成的 HTML 做视觉检查，返回 JSON 报告。",
                "parameters": {
                    "type": "object",
                    "properties": {"html_path": {"type": "string"}},
                    "required": ["html_path"],
                },
            },
            # 第 4 层：渲染交付
            {
                "name": "archify_render",
                "description": "把 JSON 渲染成 HTML（基础渲染，不打开浏览器）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)},
                        "spec_json": {"type": "string"},
                        "output_name": {"type": "string"},
                        "quality": {"type": "string", "enum": ["standard", "showcase"]},
                    },
                    "required": ["diagram_type", "spec_json"],
                },
            },
            {
                "name": "archify_deliver",
                "description": "交付渲染（带 --json 报告），生成最终 HTML。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)},
                        "spec_json": {"type": "string"},
                        "output_name": {"type": "string"},
                        "quality": {"type": "string", "enum": ["standard", "showcase"]},
                    },
                    "required": ["diagram_type", "spec_json"],
                },
            },
            {
                "name": "archify_preview",
                "description": "预览渲染（不打开浏览器，服务端用）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_type": {"type": "string", "enum": list(DIAGRAM_TYPES)},
                        "spec_json": {"type": "string"},
                        "output_name": {"type": "string"},
                        "quality": {"type": "string", "enum": ["standard", "showcase"]},
                    },
                    "required": ["diagram_type", "spec_json"],
                },
            },
            {
                "name": "archify_compare",
                "description": "对比两张架构图，输出差异（仅 architecture 类型）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "base_json": {"type": "string"},
                        "head_json": {"type": "string"},
                        "output_name": {"type": "string"},
                    },
                    "required": ["base_json", "head_json"],
                },
            },
            # 第 5 层：自动化
            {
                "name": "archify_batch",
                "description": "批量渲染多个 spec。items: [{diagram_type, spec_json, output_name}]",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["items"],
                },
            },
            # 第 6 层：可观测
            {
                "name": "archify_metrics",
                "description": "查看桥接层调用统计。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        ]

    # ============ 执行入口 ============
    def execute(self, name, params):
        t0 = time.time()
        try:
            fn = self._dispatch(name)
            if fn is None:
                return None
            r = fn(params or {})
            _metric(name, True, time.time() - t0)
            return r
        except Exception as e:
            _metric(name, False, time.time() - t0)
            LOG.exception("%s 执行异常", name)
            return f"执行失败：{type(e).__name__}: {str(e)[:300]}"

    def _dispatch(self, name):
        return {
            "archify_doctor": self._do_doctor,
            "archify_read_skill": self._do_read_skill,
            "archify_read_schema": self._do_read_schema,
            "archify_read_example": self._do_read_example,
            "archify_guide": self._do_guide,
            "archify_validate": self._do_validate,
            "archify_inspect": self._do_inspect,
            "archify_check": self._do_check,
            "archify_visual_check": self._do_visual_check,
            "archify_render": self._do_render,
            "archify_deliver": self._do_deliver,
            "archify_preview": self._do_preview,
            "archify_compare": self._do_compare,
            "archify_batch": self._do_batch,
            "archify_metrics": self._do_metrics,
        }.get(name)

    # ============ 第 1 层：环境与知识 ============
    def _do_doctor(self, _):
        with _LOCK:
            if _CACHE["doctor"] is None:
                ok, out, err, dt = _run_node(["doctor"], TIMEOUT_QUICK)
                _CACHE["doctor"] = out if ok else f"doctor 失败（{dt:.1f}s）：{err[:300]}"
        return _CACHE["doctor"][:8000]

    def _do_read_skill(self, _):
        return _load_skill()[:30000]

    def _do_read_schema(self, p):
        t = p.get("diagram_type", "architecture")
        if t not in DIAGRAM_TYPES:
            return f"不支持的类型：{t}"
        return (f"=== {t}.schema.json ===\n{_load_schema(t)[:20000]}\n\n"
                f"=== common.schema.json ===\n{_load_common()[:10000]}\n\n"
                f"=== schemas/README.md ===\n{_load_schemas_readme()[:8000]}")

    def _do_read_example(self, p):
        t = p.get("diagram_type", "architecture")
        if t not in DIAGRAM_TYPES:
            return f"不支持的类型：{t}"
        return _load_example(t)[:30000]

    # ============ 第 2 层：智能路由 ============
    def _do_guide(self, p):
        s = (p.get("scenario") or "").strip()
        if not s:
            return "scenario 为空"
        lang = p.get("lang", "zh")
        ok, data, err, dt = _run_node_json(["guide", s, "--json", "--lang", lang], TIMEOUT_READ)
        if not ok:
            return f"guide 失败（{dt:.1f}s）：{err[:500]}"
        return _fmt_guide(data)

    # ============ 第 3 层：质量保障 ============
    def _do_validate(self, p):
        t = p.get("diagram_type", "architecture")
        spec = p.get("spec_json", "")
        quality = p.get("quality", "showcase")
        if t not in DIAGRAM_TYPES:
            return f"不支持的类型：{t}"
        if not spec.strip():
            return "spec_json 为空"
        ok, err = _syntax_check(spec)
        if not ok:
            return err
        spec_file = _write_spec(spec)
        ok, data, err, dt = _run_node_json(
            ["validate", t, spec_file, "--quality", quality, "--json"],
            TIMEOUT_VALIDATE,
        )
        if not ok:
            txt = _fmt_validate_error(err, t)          # 失败也要"一次列全 + 给修法"
            _log_validate_fail(t, spec, txt)
            return txt
        txt = f"耗时 {dt:.1f}s\n{_fmt_validate(data)}"
        if not data.get("ok"):
            _log_validate_fail(t, spec, txt)          # 失败就留痕（复盘"到底卡在哪几条"）
        return txt

    def _do_inspect(self, p):
        t = p.get("diagram_type", "architecture")
        spec = p.get("spec_json", "")
        if t not in DIAGRAM_TYPES:
            return f"不支持的类型：{t}"
        if not spec.strip():
            return "spec_json 为空"
        ok, err = _syntax_check(spec)
        if not ok:
            return err
        spec_file = _write_spec(spec)
        ok, out, err, dt = _run_node(["inspect", t, spec_file], TIMEOUT_VALIDATE)
        if not ok:
            return f"inspect 失败（{dt:.1f}s）：{err[:500]}"
        return out[:8000]

    def _do_check(self, p):
        path = p.get("html_path", "")
        if not path or not os.path.exists(path):
            return f"HTML 文件不存在：{path}"
        ok, out, err, dt = _run_node(["check", path], TIMEOUT_CHECK)
        if not ok:
            return f"check 失败（{dt:.1f}s）：{err[:500]}"
        return out[:5000]

    def _do_visual_check(self, p):
        path = p.get("html_path", "")
        if not path or not os.path.exists(path):
            return f"HTML 文件不存在：{path}"
        ok, data, err, dt = _run_node_json(["visual-check", path, "--json"], TIMEOUT_CHECK)
        if not ok:
            return f"visual-check 失败（{dt:.1f}s）：{err[:500]}"
        return json.dumps(data, ensure_ascii=False, indent=2)[:8000]

    # ============ 第 4 层：渲染交付 ============
    def _do_render(self, p):
        return self._render_common(p, mode="render")

    def _do_deliver(self, p):
        return self._render_common(p, mode="deliver")

    def _do_preview(self, p):
        return self._render_common(p, mode="preview")

    def _render_common(self, p, mode):
        t = p.get("diagram_type", "architecture")
        spec = p.get("spec_json", "")
        name = p.get("output_name", "")
        quality = p.get("quality", "showcase")
        if t not in DIAGRAM_TYPES:
            return f"不支持的类型：{t}"
        if not spec.strip():
            return "spec_json 为空"
        ok, err = _syntax_check(spec)
        if not ok:
            return err
        spec_file = _write_spec(spec)
        try:
            out_html = _safe_output_path(name, ".html")
        except ValueError as e:
            return f"输出文件名非法：{e}"
        args = [mode, t, spec_file, out_html, "--quality", quality]
        if mode == "deliver":
            args.append("--json")
        if mode == "preview":
            args.append("--no-open")
        timeout = TIMEOUT_DELIVER if mode == "deliver" else TIMEOUT_RENDER
        ok, out, err, dt = _run_node(args, timeout)
        _cleanup_outputs()
        if not ok:
            return f"{mode} 失败（{dt:.1f}s）：{err[:800] or out[:800]}"
        if os.path.exists(out_html):
            size = os.path.getsize(out_html)
            return f"图已生成：{out_html}\n大小：{size} 字节\n耗时：{dt:.1f}s\n{out[:2000]}"
        return f"{mode} 执行完成但没找到输出文件。stdout={out[:300]}"

    def _do_compare(self, p):
        base = p.get("base_json", "")
        head = p.get("head_json", "")
        name = p.get("output_name", "")
        if not base.strip() or not head.strip():
            return "base_json 或 head_json 为空"
        ok, err = _syntax_check(base)
        if not ok:
            return f"base {err}"
        ok, err = _syntax_check(head)
        if not ok:
            return f"head {err}"
        base_file = _write_spec(base)
        head_file = _write_spec(head)
        try:
            out_html = _safe_output_path(name, ".html")
        except ValueError as e:
            return f"输出文件名非法：{e}"
        ok, out, err, dt = _run_node(
            ["compare", "architecture", base_file, head_file, out_html, "--json"],
            TIMEOUT_COMPARE,
        )
        if not ok:
            return f"compare 失败（{dt:.1f}s）：{err[:800] or out[:800]}"
        if os.path.exists(out_html):
            return f"对比图已生成：{out_html}（{os.path.getsize(out_html)} 字节，{dt:.1f}s）"
        return f"compare 完成但没找到输出。stdout={out[:300]}"

    # ============ 第 5 层：自动化 ============
    def _do_batch(self, p):
        items = p.get("items") or []
        if not isinstance(items, list) or not items:
            return "items 为空"
        results = []
        for i, it in enumerate(items):
            t = it.get("diagram_type", "architecture")
            spec = it.get("spec_json", "")
            name = it.get("output_name", "")
            try:
                r = self._do_deliver({"diagram_type": t, "spec_json": spec, "output_name": name})
            except Exception as e:
                r = f"异常：{e}"
            results.append(f"[{i+1}/{len(items)}] {r}")
        return "\n".join(results)

    # ============ 第 6 层：可观测 ============
    def _do_metrics(self, _):
        snap = _metrics_snapshot()
        if not snap:
            return "暂无调用记录"
        lines = ["工具 | 调用 | 成功 | 失败 | 平均(s) | 最大(s)"]
        for k, v in snap.items():
            lines.append(f"{k} | {v['calls']} | {v['ok']} | {v['fail']} | {v['avg_s']} | {v['max_s']}")
        return "\n".join(lines)