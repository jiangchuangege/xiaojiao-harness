# -*- coding: utf-8 -*-
"""
小焦 · Scrapling MCP 桥接插件（scrapling_bridge）
=====================================================================
让小焦（含 4B 小脑）直接调用 Scrapling 的全部抓取能力：
HTTP 请求 / Playwright 浏览器渲染 / 隐身绕过 Cloudflare。

【外部依赖】（需 pip 安装）
  - scrapling[fetchers] >= 0.4.15   抓取内核（Fetcher / DynamicFetcher / StealthyFetcher）
  - mcp >= 1.0                      仅 "mcp" 模式需要（stdio / streamable-http MCP 客户端）
  - playwright + Chromium           浏览器抓取用；可自备 Chrome（executable_path 指定）
  安装:
      python -m pip install "scrapling[fetchers]" mcp -i https://pypi.tuna.tsinghua.edu.cn/simple

【两种运行模式】（配置 mode = auto | inproc | mcp）
  1) inproc（默认/回退）：进程内直接调用 Scrapling 抓取 API —— 零子进程、零协议开销、最稳。
     ★ 4B 模型场景推荐：少一层协议就少一处翻车点。
  2) mcp：本插件作为 MCP 客户端调用 Scrapling MCP 服务
     · stdio：自动拉起 `scrapling mcp --executable-path <chrome>`，JSON-RPC 走管道
     · http ：连 scrapling_mcp_url（如 http://127.0.0.1:8000/mcp）
  mode=auto 时：mcp 可用则走 mcp，否则自动回退 inproc（并在结果里标注实际模式）。

【小焦内置 Scrapling 抓取能力】想抓啥抓啥：
  网页正文 / 动态渲染页 / 接口 JSON / 批量列表 / 登录态页面 / 下载任意文件（PDF/EPUB/ZIP/图片…）

【对小焦暴露 17 个工具】= Scrapling 原生 13 个（1:1，名字与官方一致）+ 3 个增强 + 1 个兼容入口
  原生 13: make_request / bulk_get / fetch / bulk_fetch / stealthy_fetch / bulk_stealthy_fetch
           open_session / open_request_session / close_session / list_sessions
           session_fetch / session_make_request / screenshot
  增强 3 : get（make_request 的中文友好别名）/ scrape_with_selector（自适应选择器）
           / download（下载任意文件，Scrapling 原生没有）
  兼容 1 : browser_session（用 action 一个工具走完 open/fetch/screenshot/close）

【设计要点】
  · 异步桥接：专用事件循环线程 + ThreadPoolExecutor，绝不直接 asyncio.run()（避免事件循环冲突）
  · 连接池：MCP 会话持久化 + 健康检查 + 30 秒内自动重连
  · 超时控制：任何调用超过 timeout（默认 60s）强制取消，返回中文可读错误
  · 批量：URL 去重 / 限速 / 429 指数退避 / 代理轮换(≤5次) / 部分失败隔离
  · 安全：SSRF 100% 拦截、robots.txt 合规、同域限速、UA 合规、日志脱敏、结果不上传
  · 熔断：同一工具连续失败 N 次 → 暂停 T 秒 → 自动恢复（绝不永久禁用）
  · 返回统一：{"status": int, "url": str, "content": str, "error": str}（content 为 Markdown）

作者：为小焦定制  ·  Python 3.10+
"""
from __future__ import annotations

import asyncio
import atexit
import ipaddress
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import logging  # noqa: F401  （由 tools/fix_silent_except.py 注入）
try:
    from xiaojiao_log import get_logger
except Exception:  # 独立运行时退化为标准 logging
    def get_logger(name=None):
        return logging.getLogger(name or 'xiaojiao')
LOG = get_logger(__name__)

# =====================================================================
# 常量（所有魔法数字集中在此，便于调优）
# =====================================================================
DEFAULT_TIMEOUT: float = 60.0           # 单次调用总超时（秒）
DEFAULT_RATE_LIMIT: float = 1.0         # 同域/批量请求最小间隔（秒）
DEFAULT_MAX_RETRIES: int = 2            # 普通失败重试次数
BULK_BACKOFF_BASE: float = 1.0          # 429 退避基数（1s→2s→4s→8s）
BULK_MAX_RETRIES: int = 3               # 429 最大重试
PROXY_MAX_USES: int = 5                 # 单个代理最多使用次数
MAX_CONTENT_CHARS: int = 10000          # 单条内容最大字符数（超出截断）
MAX_DOWNLOAD_MB: int = 500              # 单文件下载大小上限（MB），防误下巨型文件撑爆磁盘
BULK_MAX_URLS: int = 200                # 单次批量最大 URL 数
CIRCUIT_THRESHOLD: int = 3              # 熔断阈值（连续失败次数）
CIRCUIT_COOLDOWN: float = 30.0          # 熔断恢复时间（秒）
MCP_INIT_TIMEOUT: float = 20.0          # MCP initialize 超时
MCP_RECONNECT_WINDOW: float = 30.0      # 崩溃后 30 秒内自动重连
STEALTH_MIN_GAP: float = 2.0            # 隐身模式连续请求最小间隔（秒）
HEALTH_CHECK_INTERVAL: float = 5.0      # 连接健康检查间隔
ROBOTS_CACHE_TTL: float = 3600.0        # robots.txt 缓存有效期
ROBOTS_TIMEOUT: float = 8.0             # robots.txt 拉取超时（秒）
ROBOTS_MAX_BYTES: int = 256 * 1024      # robots.txt 最大读取字节（防超大文件）
ROBOTS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 抓到的 JSON 展示上限：超过就美化后折叠（避免把几十 KB 压缩 JSON 糊满聊天窗）
JSON_DISPLAY_LINES: int = 120
JSON_DISPLAY_CHARS: int = 3500

# ---------- 会话回收（改进 1）默认值 ----------
DEFAULT_MAX_SESSIONS: int = 20          # 同时最多保留几个会话（超出踢最久未用）
DEFAULT_SESSION_TTL: int = 1800         # 单个会话最长存活 30 分钟（到期强制回收）
DEFAULT_SESSION_IDLE: int = 300         # 空闲 5 分钟没用 → 回收
SESSION_SWEEP_INTERVAL: int = 60        # 后台巡检间隔（秒）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SELECTOR_STORE = os.path.join(SCRIPT_DIR, ".scrapling_selectors.json")
CONTROL_FILE = os.path.join(os.path.dirname(SCRIPT_DIR), "xiaojiao_control.json")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (XiaoJiao-ScraplingBridge)")
# 下载文件用的标准浏览器头（很多站点的 WAF 会拒绝带自定义标识的 UA）
_DL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ---------- 批量并发（改进 4）默认值 ----------
DEFAULT_BATCH_CONCURRENCY: int = 3      # 跨域同时抓几个
DEFAULT_PER_DOMAIN_LIMIT: int = 1       # 同一域名同时最多几个请求（防把人家打挂）


@dataclass
class BatchConfig:
    """批量抓取的可配置策略（改进 4）。

    设计意图：原来的批量是**串行**的（1 个 URL 抓完再下一个），4 个域名也要 4 秒起步；
    但直接放开并发又容易把单个站点打挂。所以拆成两个维度：
      · `concurrency`      —— **跨域**并发上限（不同域名可以同时抓）
      · `per_domain_limit` —— **同域**并发上限（默认 1，永远不并发打同一个站）
    再加 `rate_limit`（同域最小间隔秒）与 `max_retries` / `backoff_base`（429 指数退避）。

    配置来源：xiaojiao_control.json → scrapling.batch {...}，或环境变量
    `XIAOJIAO_SCRAPLING_BATCH_CONCURRENCY` / `_PER_DOMAIN_LIMIT` / `_BATCH_RETRIES` / `_BATCH_BACKOFF`。
    非法值（如 concurrency=0）→ **不静默忽略**：记中文错误并在批量工具里明确返回可读错误。
    """
    concurrency: int = DEFAULT_BATCH_CONCURRENCY
    rate_limit: float = DEFAULT_RATE_LIMIT
    per_domain_limit: int = DEFAULT_PER_DOMAIN_LIMIT
    max_retries: int = BULK_MAX_RETRIES
    backoff_base: float = BULK_BACKOFF_BASE
    error: str = ""                     # 非空表示配置非法（中文原因）

    @staticmethod
    def load(scrapling_section: Dict[str, Any], base_rate: float) -> "BatchConfig":
        cfg = BatchConfig(rate_limit=base_rate)
        raw = (scrapling_section or {}).get("batch") or {}
        if isinstance(raw, dict):
            for k in ("concurrency", "per_domain_limit", "max_retries"):
                if raw.get(k) is not None:
                    try:
                        setattr(cfg, k, int(raw[k]))
                    except Exception:
                        cfg.error = "batch.%s 必须是整数（收到 %r）" % (k, raw[k])
            for k in ("rate_limit", "backoff_base"):
                if raw.get(k) is not None:
                    try:
                        setattr(cfg, k, float(raw[k]))
                    except Exception:
                        cfg.error = "batch.%s 必须是数字（收到 %r）" % (k, raw[k])
        envs = {"XIAOJIAO_SCRAPLING_BATCH_CONCURRENCY": ("concurrency", int),
                "XIAOJIAO_SCRAPLING_BATCH_PER_DOMAIN": ("per_domain_limit", int),
                "XIAOJIAO_SCRAPLING_BATCH_RETRIES": ("max_retries", int),
                "XIAOJIAO_SCRAPLING_BATCH_BACKOFF": ("backoff_base", float)}
        for env_k, (attr, cast) in envs.items():
            v = os.environ.get(env_k)
            if v:
                try:
                    setattr(cfg, attr, cast(float(v)))
                except Exception:
                    cfg.error = "%s 环境变量不是数字（收到 %r）" % (env_k, v)
        cfg.error = cfg.error or cfg.validate()
        return cfg

    def validate(self) -> str:
        """返回空串=合法；否则返回中文可读原因。"""
        if self.concurrency < 1:
            return "批量并发 concurrency 必须 ≥ 1（当前 %r）—— 请修正 xiaojiao_control.json 的 scrapling.batch 段" % self.concurrency
        if self.concurrency > BULK_MAX_URLS:
            return "批量并发 concurrency 不能超过单批上限 %d（当前 %r）" % (BULK_MAX_URLS, self.concurrency)
        if self.per_domain_limit < 1:
            return "同域并发 per_domain_limit 必须 ≥ 1（当前 %r）" % self.per_domain_limit
        if self.rate_limit < 0:
            return "同域间隔 rate_limit 不能为负（当前 %r）" % self.rate_limit
        if self.max_retries < 0 or self.max_retries > 10:
            return "max_retries 必须在 0~10 之间（当前 %r）" % self.max_retries
        if self.backoff_base < 0:
            return "backoff_base 不能为负（当前 %r）" % self.backoff_base
        return ""

    def describe(self) -> str:
        return ("并发 %d（跨域）/ 同域 %d / 同域间隔 %.1fs / 重试 %d 次 / 退避基数 %.1fs"
                % (self.concurrency, self.per_domain_limit, self.rate_limit,
                   self.max_retries, self.backoff_base))


def _load_scrapling_section() -> Dict[str, Any]:
    """读 xiaojiao_control.json 的 scrapling 段（供 BatchConfig 用；读不到返回空 dict）。"""
    try:
        with open(CONTROL_FILE, encoding="utf-8") as f:
            return (json.load(f) or {}).get("scrapling") or {}
    except Exception as e:
        logger.warning("读取 scrapling 配置段失败(用默认值): %s", sanitize(e))
        return {}


def _markdown_available() -> bool:
    """探测 markdownify 是否可用（Scrapling 的 markdown 提取依赖它）。

    设计意图：缺少该依赖时 Scrapling 会直接抛 ModuleNotFoundError；
    这里提前探测，缺了就改用 html 提取 + 内置 HTML→Markdown 转换，
    保证"返回给 4B 模型的 content 始终是 Markdown"，不因缺依赖而整体失败。
    """
    try:
        import markdownify  # noqa: F401
        return True
    except Exception:
        return False


_MD_OK = _markdown_available()
_EXTRACT_TYPE = "markdown" if _MD_OK else "html"

# 各 Scrapling 工具允许的参数（按 0.4.15 真实签名整理）。
# 设计意图：4B 模型/上层逻辑可能给出该工具不支持的参数（如给 HTTP 工具传浏览器参数），
# 这里统一过滤，避免 "unexpected keyword argument" 这类内部错误漏给模型。
_TOOL_PARAMS: Dict[str, set] = {
    "make_request": {"url", "method", "impersonate", "extraction_type", "css_selector",
                     "main_content_only", "params", "data", "json", "headers", "cookies",
                     "timeout", "follow_redirects", "max_redirects", "retries", "retry_delay",
                     "proxy", "proxy_auth", "auth", "verify", "http3", "stealthy_headers"},
    "bulk_get": {"urls", "impersonate", "extraction_type", "css_selector", "main_content_only",
                 "params", "headers", "cookies", "timeout", "follow_redirects", "max_redirects",
                 "retries", "retry_delay", "proxy", "proxy_auth", "auth", "verify", "http3",
                 "stealthy_headers"},
    "fetch": {"url", "extraction_type", "css_selector", "main_content_only", "headless",
              "google_search", "real_chrome", "wait", "proxy", "timezone_id", "locale",
              "extra_headers", "useragent", "cdp_url", "executable_path", "timeout",
              "disable_resources", "wait_selector", "cookies", "network_idle", "wait_selector_state"},
    "bulk_fetch": {"urls", "extraction_type", "css_selector", "main_content_only", "headless",
                   "google_search", "real_chrome", "wait", "proxy", "timezone_id", "locale",
                   "extra_headers", "useragent", "cdp_url", "executable_path", "timeout",
                   "disable_resources", "wait_selector", "cookies", "network_idle", "wait_selector_state"},
    "stealthy_fetch": {"url", "extraction_type", "css_selector", "main_content_only", "headless",
                       "google_search", "real_chrome", "wait", "proxy", "timezone_id", "locale",
                       "extra_headers", "useragent", "hide_canvas", "cdp_url", "executable_path",
                       "timeout", "disable_resources", "wait_selector", "cookies", "network_idle",
                       "wait_selector_state", "block_webrtc", "allow_webgl", "solve_cloudflare",
                       "additional_args"},
    "bulk_stealthy_fetch": {"urls", "extraction_type", "css_selector", "main_content_only", "headless",
                            "google_search", "real_chrome", "wait", "proxy", "timezone_id", "locale",
                            "extra_headers", "useragent", "hide_canvas", "cdp_url", "executable_path",
                            "timeout", "disable_resources", "wait_selector", "cookies", "network_idle",
                            "wait_selector_state", "block_webrtc", "allow_webgl", "solve_cloudflare",
                            "additional_args"},
    # ---- 会话 / 截图类（由 browser_session 工具按 action 分发）----
    "open_session": {"session_type", "session_id", "headless", "real_chrome", "timezone_id", "locale",
                     "useragent", "proxy", "cdp_url", "executable_path", "cookies", "hide_canvas",
                     "block_webrtc", "allow_webgl", "additional_args"},
    "open_request_session": {"session_id", "impersonate", "proxy"},
    "close_session": {"session_id"},
    "list_sessions": set(),
    "screenshot": {"url", "session_id", "image_type", "full_page", "quality", "wait",
                   "wait_selector", "wait_selector_state", "network_idle", "timeout"},
    "session_fetch": {"url", "session_id", "extraction_type", "css_selector", "main_content_only",
                      "wait", "timeout", "google_search", "network_idle", "load_dom",
                      "disable_resources", "wait_selector", "wait_selector_state", "extra_headers",
                      "blocked_domains", "solve_cloudflare"},
    "session_make_request": {"url", "session_id", "method", "extraction_type", "css_selector",
                             "main_content_only", "params", "data", "json", "headers", "cookies",
                             "timeout", "follow_redirects", "max_redirects", "retries", "retry_delay",
                             "auth", "verify", "http3", "stealthy_headers"},
}
# 浏览器类工具的 timeout 单位是【毫秒】，HTTP 类是【秒】——必须分别换算，否则会 60ms 就超时
_TIMEOUT_MS_TOOLS = frozenset({"fetch", "bulk_fetch", "stealthy_fetch", "bulk_stealthy_fetch",
                               "session_fetch", "screenshot"})


def _adapt_args(tool: str, args: Dict[str, Any], default_timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """按工具白名单过滤参数，并统一 timeout 单位（浏览器类转毫秒）。"""
    allowed = _TOOL_PARAMS.get(tool)
    out = {k: v for k, v in (args or {}).items() if (allowed is None or k in allowed)}
    # 只有该工具确实支持 timeout 参数时才附加（open_session/close_session/list_sessions 不支持）
    if allowed is None or "timeout" in allowed:
        try:
            t = float(out.get("timeout", default_timeout))
        except Exception:
            t = default_timeout
        if t <= 0:
            t = default_timeout
        out["timeout"] = int(t * 1000) if tool in _TIMEOUT_MS_TOOLS else t
    return out


def _guess_filename(url: str, default_ext: str = ".bin") -> str:
    """从 URL 推断安全文件名（去查询串/非法字符/防目录穿越）。"""
    try:
        name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path or ""))
    except Exception:
        name = ""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(". ")
    if not name or ".." in name:
        name = "file_" + time.strftime("%Y%m%d%H%M%S") + default_ext
    return name[:120]


def _out_dir(kind: str) -> str:
    """项目内的输出目录（downloads=下载件 / books=抓取的正文存档）。"""
    d = os.path.join(os.path.dirname(SCRIPT_DIR), kind)
    os.makedirs(d, exist_ok=True)
    return d


def _save_text_file(name: str, text: str, kind: str = "books") -> str:
    """把文本存成本地文件并返回路径（禁止越出输出目录）。"""
    d = os.path.abspath(_out_dir(kind))
    safe = _guess_filename(name, ".md") if name else ("book_" + time.strftime("%Y%m%d%H%M%S") + ".md")
    if not os.path.splitext(safe)[1]:
        safe += ".md"
    fp = os.path.abspath(os.path.join(d, safe))
    if not fp.startswith(d):                 # 防目录穿越
        fp = os.path.join(d, "book_" + time.strftime("%Y%m%d%H%M%S") + ".md")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(text or "")
    return fp


def _handle_screenshot(items: Sequence[Any]) -> Dict[str, Any]:
    """把截图结果（ImageContent，base64）落盘到 media/screenshot/，返回文件路径。

    设计意图：4B 模型看不了二进制图片；存成本地文件并把路径回给它/用户，双方都能直接用。
    """
    import base64 as _b64
    out_dir = os.path.join(os.path.dirname(SCRIPT_DIR), "media", "screenshot")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        return {"status": 0, "url": "", "content": "",
                "error": "截图目录创建失败（磁盘空间/权限）：%s" % sanitize(e)[:100]}
    paths: List[str] = []
    for it in items:
        try:
            d = it if isinstance(it, dict) else (it.model_dump() if hasattr(it, "model_dump") else {})
        except Exception:
            d = {}
        if str(d.get("type", "")) != "image" or not d.get("data"):
            continue
        ext = "jpg" if "jpeg" in str(d.get("mimeType", "")).lower() else "png"
        fp = os.path.join(out_dir, time.strftime("%Y%m%d%H%M%S") + "_%03d.%s" % (random.randint(0, 999), ext))
        try:
            with open(fp, "wb") as f:
                f.write(_b64.b64decode(d["data"]))
            paths.append(fp)
        except OSError as e:
            return {"status": 0, "url": "", "content": "",
                    "error": "截图保存失败：%s" % sanitize(e)[:100]}
    if not paths:
        return {"status": 0, "url": "", "content": "", "error": "截图未返回图片数据"}
    return {"status": 200, "url": "", "content": "📷 截图已保存：\n" + "\n".join(paths), "error": ""}

logger = logging.getLogger("xiaojiao.scrapling_bridge")
if not logger.handlers:              # 避免重复添加 handler
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[scrapling_bridge] %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# =====================================================================
# 工具函数：日志脱敏 / 内容标准化
# =====================================================================
_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(bearer\s+)(\S+)"),
    # ---- 裸凭据形态（没有 key= 前缀也要抹掉）----
    # 指标自测发现：sanitize() 原来只认"键值对"写法，像 sk-xxx 这种**裸密钥**会原样进日志/指标
    re.compile(r"(sk-[A-Za-z0-9_\-]{12,})"),                       # OpenAI 风格
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{16,})"),                   # GitHub token
    re.compile(r"(AKIA[0-9A-Z]{12,})"),                            # AWS Access Key
    re.compile(r"(xox[baprs]-[A-Za-z0-9\-]{10,})"),                # Slack
    re.compile(r"(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,})"),   # JWT
    re.compile(r"(?i)(password\s*[:=]\s*)(\S+)"),
]


def sanitize(text: Any) -> str:
    """日志脱敏：抹掉 API Key / Token / Cookie / Authorization / 裸凭据，禁止外泄。

    说明：分两类处理 —— ① 键值对形式保留键名、抹掉值（便于排查"是哪个字段"）；
    ② 裸凭据（sk-xxx / gho_xxx / JWT / AWS Key）整串替换为 ***。
    """
    s = str(text)
    for pat in _SECRET_PATTERNS:
        try:
            if pat.groups >= 2:
                s = pat.sub(lambda m: m.group(1) + "***", s)
            else:
                s = pat.sub("***", s)
        except Exception:
            continue
    return s


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def html_to_markdown(html: str) -> str:
    """极简 HTML→Markdown：没有依赖也能给 4B 模型一个可读的纯文本骨架。"""
    if not html:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", s)
    s = re.sub(r"(?i)<h1[^>]*>", "\n# ", s)
    s = re.sub(r"(?i)<h2[^>]*>", "\n## ", s)
    s = re.sub(r"(?i)<h3[^>]*>", "\n### ", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"(?i)<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"[\2](\1)", s)
    s = _HTML_TAG_RE.sub("", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        s = s.replace(a, b)
    s = _WS_RE.sub("\n\n", s)
    return s.strip()


def clip_content(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    """统一截断（JSON 超长截断；错误里也说明已截断，便于 4B 模型理解）。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（内容过长已截断，共 %d 字符）" % len(text)


# Setext 风格标题（标题\n==== / 标题\n----）→ ATX（# / ##）。
# 设计意图：Scrapling 输出的 Markdown 用 Setext，而小焦前端只认 ATX；
# 不转换的话标题会变成 "文本\n==========" 挤在正文里，又难看又难读。
_SETEXT_H1 = re.compile(r"^(?P<t>[^\n#|>\s][^\n]*?)\n=+[ \t]*$", re.M)
_SETEXT_H2 = re.compile(r"^(?P<t>[^\n#|>\s][^\n]*?)\n-{2,}[ \t]*$", re.M)


def normalize_markdown(s: str) -> str:
    """把 Setext 标题转成 ATX 标题（让小焦前端渲染成标题样式）。"""
    if not s:
        return ""
    s = _SETEXT_H1.sub(lambda m: "# " + m.group("t").strip(), s)
    s = _SETEXT_H2.sub(lambda m: "## " + m.group("t").strip(), s)
    return s


def _unescape_md_json(s: str) -> str:
    """去掉 Markdown 转换给 JSON 加的转义反斜杠（`\\_` `\\*` `\\[` …）。

    为什么：extraction_type=markdown 时，markdownify 会把 `_` `*` `[` 等转义成 `\\_` `\\*`，
    于是原本合法的 JSON 变成 `"NVD\\_CVE"` 这种**非法 JSON**，导致后续美化/解析全部失效。
    这里只清理"JSON 里本来就不合法"的转义（保留 \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX）。
    """
    return re.sub(r'\\([^"\\/bfnrtu])', r'\1', s)


def _shape_content(text: str) -> str:
    """把抓到的正文整理成"人看得下去"的样子（JSON 自动美化 + 智能截断）。

    为什么需要：直接抓 JSON API 时，原始响应是一整行几十 KB 的压缩 JSON，
    塞进聊天窗就是一大坨乱码般的文字（真实用户反馈："这咋看这么乱"）。
    这里统一做：美化缩进 → 超长按行截断 → 明确告知剩余内容怎么拿。
    """
    s = (text or "").strip()
    if not s or s[0] not in "[{":
        return text
    obj = None
    for cand in (s, _unescape_md_json(s)):
        try:
            obj = json.loads(cand)
            break
        except Exception:
            continue
    if obj is None:
        return text                      # 不是合法 JSON（或已被截断）→ 原样返回
    try:
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return text
    lines = pretty.splitlines()
    if len(pretty) <= JSON_DISPLAY_CHARS and len(lines) <= JSON_DISPLAY_LINES:
        return pretty
    keep = lines[:JSON_DISPLAY_LINES]
    out = "\n".join(keep)
    if len(out) > JSON_DISPLAY_CHARS:
        out = out[:JSON_DISPLAY_CHARS].rsplit("\n", 1)[0]
    return ("%s\n\n… ⋯ 内容较长已折叠显示（共 %d 行 / %d 字符）\n"
            "· 想看完整内容：说「存成文件」或加参数 save_to=xxx.json\n"
            "· 只想看某个字段：告诉小焦具体要哪些（例如「只列 id 和 baseScore」）" % (out, len(lines), len(pretty)))


def fmt_result(status: Any, url: str, content: str = "", error: str = "") -> str:
    """统一返回结构（字符串 JSON，交给小焦展示）。content 默认 Markdown。"""
    payload = {
        "status": int(status) if isinstance(status, (int, float, str)) and str(status).isdigit() else status,
        "url": url or "",
        "content": clip_content(_shape_content(content)),
        "error": error or "",
    }
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return json.dumps({"status": status, "url": url, "content": "", "error": "结果序列化失败"},
                          ensure_ascii=False)


def ok_msg(text: str) -> str:
    """纯文本成功消息（非抓取类工具用）。"""
    return text


# =====================================================================
# 配置
# =====================================================================
@dataclass
class BridgeConfig:
    """桥接配置：可由 xiaojiao_control.json 的 "scrapling" 段或环境变量覆盖。"""
    mode: str = "auto"                       # auto | inproc | mcp
    scrapling_mcp_url: str = ""              # http 模式地址，如 http://127.0.0.1:8000/mcp
    mcp_command: str = "scrapling"           # stdio 模式命令
    executable_path: str = ""                # 自备 Chrome 路径（留空则自动探测：配置 → 项目内 → 各盘关键词目录）
    proxy_list: List[str] = field(default_factory=list)
    rate_limit: float = DEFAULT_RATE_LIMIT
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    circuit_breaker_threshold: int = CIRCUIT_THRESHOLD
    circuit_breaker_timeout: float = CIRCUIT_COOLDOWN
    allow_robots_skip: bool = False          # True=robots 禁止也抓（默认严格遵守，禁止）
    headless: bool = True
    solve_cloudflare: bool = True            # 隐身模式尝试自动过 Cloudflare 验证
    # ---- 会话回收策略（改进 1）：见 SessionConfig / SessionManager ----
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_ttl: int = DEFAULT_SESSION_TTL
    session_idle: int = DEFAULT_SESSION_IDLE

    # ---- 会话回收策略（改进 1）----
    # 配置来源（优先级从高到低）：
    #   ① xiaojiao_control.json → scrapling.max_sessions / session_ttl / session_idle
    #   ② 环境变量 XIAOJIAO_SCRAPLING_MAX_SESSIONS / _SESSION_TTL / _SESSION_IDLE
    #   ③ 下面的默认值
    # 语义：ttl=会话最长存活（不管用不用），idle=多久没用就回收，max=同时最多几个（超了踢最久未用）
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_ttl: int = DEFAULT_SESSION_TTL
    session_idle: int = DEFAULT_SESSION_IDLE

    @staticmethod
    def load() -> "BridgeConfig":
        """读取配置：环境变量 > xiaojiao_control.json 的 scrapling 段 > 默认值。"""
        cfg = BridgeConfig()
        data: Dict[str, Any] = {}
        try:
            if os.path.exists(CONTROL_FILE):
                with open(CONTROL_FILE, encoding="utf-8") as f:
                    data = (json.load(f) or {}).get("scrapling") or {}
        except Exception as e:
            logger.warning("读取操控文件失败(用默认配置): %s", sanitize(e))
        if isinstance(data, dict):
            for k, v in data.items():
                if hasattr(cfg, k) and v is not None:
                    try:
                        setattr(cfg, k, v)
                    except Exception as e:
                        LOG.debug("忽略异常(%s:561): %s", __file__, 561, e)
        # 环境变量覆盖（部署/调试更方便）
        env_map = {
            "XIAOJIAO_SCRAPLING_MODE": "mode",
            "XIAOJIAO_SCRAPLING_MCP_URL": "scrapling_mcp_url",
            "XIAOJIAO_SCRAPLING_CHROME": "executable_path",
            "XIAOJIAO_SCRAPLING_TIMEOUT": "timeout",
            "XIAOJIAO_SCRAPLING_RATE": "rate_limit",
            "XIAOJIAO_SCRAPLING_MAX_SESSIONS": "max_sessions",
            "XIAOJIAO_SCRAPLING_SESSION_TTL": "session_ttl",
            "XIAOJIAO_SCRAPLING_SESSION_IDLE": "session_idle",
        }
        for env_k, attr in env_map.items():
            v = os.environ.get(env_k)
            if v:
                try:
                    if attr in ("timeout", "rate_limit"):
                        setattr(cfg, attr, float(v))
                    elif attr in ("max_sessions", "session_ttl", "session_idle"):
                        setattr(cfg, attr, int(float(v)))
                    else:
                        setattr(cfg, attr, v)
                except Exception as e:
                    LOG.debug("忽略异常(%s:584): %s", __file__, 584, e)
        if not cfg.executable_path:
            # 兜底探测：项目内 / 家目录 / 各盘关键词目录找 chrome.exe（不写死盘符与目录名）
            _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cands = [os.path.join(_here, "chrome-win64", "chrome.exe"),
                     os.path.expanduser("~\\chrome-win64\\chrome.exe")]
            try:
                import sys as _sys
                if _here not in _sys.path:
                    _sys.path.insert(0, _here)
                import install_all as _ia
                for _drv in _ia._drives():
                    for _t in _ia._top_dirs(_drv):
                        if _ia._hit_keyword(_t, ("chrome", "browser", "浏览器", "xiaojiao")) or _ia._hit_keyword(_t, _ia.DISCOVER_KEYWORDS):
                            _b = os.path.join(_drv, _t)
                            cands.append(os.path.join(_b, "chrome.exe"))
                            cands.append(os.path.join(_b, "chrome-win64", "chrome.exe"))
            except Exception as e:
                LOG.debug("忽略异常(%s:602): %s", __file__, 602, e)
            for cand in cands:
                if os.path.exists(cand):
                    cfg.executable_path = cand
                    break
        return cfg


# =====================================================================
# 一、异步桥接：专用事件循环线程（禁止直接 asyncio.run）
# =====================================================================
class AsyncRunner:
    """持久事件循环线程。

    设计意图：
      · Flask / 插件 execute() 都在同步线程里跑，而 Scrapling 抓取是异步 API；
        反复 asyncio.run() 会不断新建/销毁事件循环，既慢又容易与已在运行的循环冲突。
      · 这里单独起一条后台线程常驻一个事件循环，所有协程都提交进去执行；
        超时用 concurrent.futures 的 result(timeout) 控制，超时即强制取消，不会卡死小焦。
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._thread = threading.Thread(target=self._run_loop, name="xj-scrapling-loop", daemon=True)
            self._thread.start()
            self._ready.wait(10.0)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception as e:
                LOG.debug("忽略异常(%s:647): %s", __file__, 647, e)

    def run(self, coro, timeout: float = DEFAULT_TIMEOUT):
        """提交协程并等待结果；超时抛 TimeoutError（由上层转成中文错误）。"""
        self.start()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except _FutTimeout:
            fut.cancel()          # 强制取消，避免后台任务堆积
            raise TimeoutError("操作超时（%.0f 秒）" % timeout)

    def shutdown(self) -> None:
        try:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception as e:
            LOG.debug("忽略异常(%s:665): %s", __file__, 665, e)


# =====================================================================
# 二、安全防护：SSRF / robots.txt / 限速 / UA
# =====================================================================
class SecurityGuard:
    """安全闸门：SSRF 100% 拦截、robots.txt 合规、同域限速。

    设计意图：
      · 4B 模型可能被内容诱导去抓内网地址（127.0.0.1、192.168.x …），
        这里在**发起请求之前**做统一校验，命中即拒，绝不放行。
      · robots.txt 结果带缓存（默认 1 小时），避免每个 URL 都去拉一次。
    """

    BLOCKED_SCHEMES = ("file", "ftp", "gopher", "data", "javascript", "about", "chrome", "ws", "wss")
    PRIVATE_HOST_HINTS = ("localhost", "127.", "0.0.0.0", "::1", "metadata.google.internal")

    def __init__(self, rate_limit: float = DEFAULT_RATE_LIMIT, allow_robots_skip: bool = False) -> None:
        self.rate_limit = max(0.0, float(rate_limit))
        self.allow_robots_skip = allow_robots_skip
        self._last_hit: Dict[str, float] = {}
        self._robots: Dict[str, Tuple[float, Optional[urllib.robotparser.RobotFileParser], str]] = {}
        self._lock = threading.Lock()

    # ---------- SSRF ----------
    def check_ssrf(self, url: str) -> str:
        """返回空串=安全；否则返回中文拒绝原因。"""
        if not url or not isinstance(url, str):
            return "URL 为空"
        try:
            p = urllib.parse.urlparse(url.strip())
        except Exception:
            return "URL 格式不正确"
        scheme = (p.scheme or "").lower()
        if scheme in self.BLOCKED_SCHEMES:
            return "禁止访问 %s:// 协议地址（安全策略）" % scheme
        if scheme not in ("http", "https"):
            return "只支持 http/https 协议"
        host = (p.hostname or "").lower()
        if not host:
            return "URL 缺少主机名"
        if any(host == h or host.startswith(h) for h in self.PRIVATE_HOST_HINTS):
            return "禁止访问本机/内网地址（SSRF 防护）"
        # 域名解析后再校验一次（防 DNS 指向内网）
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return ""      # 解析失败交给后续请求报错（不当作 SSRF）
        for info in infos:
            ip = info[4][0]
            try:
                addr = ipaddress.ip_address(ip)
            except Exception:
                continue
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
                return "禁止访问内网/保留地址 %s（SSRF 防护）" % ip
        return ""

    # ---------- robots.txt ----------
    def _fetch_robots(self, root: str) -> Tuple[Optional[List[str]], str]:
        """自己拉 robots.txt 并返回 (行列表 or None, 说明)。

        为什么不用 urllib.robotparser 的 read()：
          · read() 遇到 401/403 会直接设 disallow_all=True → **整站被当成禁止抓取**；
            而现实里大量站点的 WAF 只是把 robots.txt 请求(默认 Python-urllib UA) 403 掉，
            站上根本没有 robots.txt（用浏览器 UA 去看是 404）。
          · 按 RFC 9309：4xx（含 401/403）与 5xx 都表示 robots.txt **不可用** → 不应阻塞抓取。
        所以这里显式区分「有规则」与「拿不到规则」两种情况。
        """
        url = root + "/robots.txt"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ROBOTS_UA,
                                                       "Accept": "text/plain,*/*"})
            with urllib.request.urlopen(req, timeout=ROBOTS_TIMEOUT) as r:
                raw = r.read(ROBOTS_MAX_BYTES)
            return raw.decode("utf-8", "ignore").splitlines(), ""
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return None, "robots.txt 被 WAF 拦(%d，按 RFC 9309 视为不可用) → 放行" % e.code
            return None, "该站没有 robots.txt(%d) → 放行" % e.code
        except Exception as e:
            return None, "robots.txt 拉取失败(%s) → 放行" % type(e).__name__

    def robots_allowed(self, url: str, user_agent: str = "*", ignore: bool = False) -> Tuple[bool, str]:
        """检查 robots.txt。返回 (是否允许, 原因)。

        ignore=True 表示**用户显式要求忽略**（单次生效，用于"这站我有权限抓"的场景）。
        """
        if ignore:
            return True, "用户显式要求忽略 robots.txt（本次放行）"
        try:
            p = urllib.parse.urlparse(url)
            root = "%s://%s" % (p.scheme, p.netloc)
        except Exception:
            return True, ""
        now = time.time()
        with self._lock:
            cached = self._robots.get(root)
            if cached and now - cached[0] < ROBOTS_CACHE_TTL:
                rp, note = cached[1], cached[2]
            else:
                rp, note = None, ""
                lines, note = self._fetch_robots(root)
                if lines is not None:
                    try:
                        rp = urllib.robotparser.RobotFileParser()
                        rp.parse(lines)
                    except Exception:
                        rp, note = None, "robots.txt 解析失败 → 放行"
                self._robots[root] = (now, rp, note)
        if rp is None:
            return True, note          # 没有规则 / 拿不到规则 → 不阻塞
        try:
            if rp.can_fetch(user_agent, url):
                return True, ""
            if self.allow_robots_skip:
                return True, "robots.txt 禁止但已配置忽略（allow_robots_skip=true）"
            host = urllib.parse.urlparse(url).netloc
            return False, ("robots.txt 明确禁止抓取 %s 的这个地址 —— 若你确认有权抓取，"
                           "可在 xiaojiao_control.json 的 scrapling 段设 allow_robots_skip=true，"
                           "或直接说「忽略 robots 抓一次」" % host)
        except Exception:
            return True, ""

    # ---------- 速率限制 ----------
    def wait_rate_limit(self, url: str) -> None:
        """同域请求间隔 ≥ rate_limit 秒（默认 1 秒）。"""
        try:
            host = urllib.parse.urlparse(url).netloc or "default"
        except Exception:
            host = "default"
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            gap = time.time() - last
            wait = self.rate_limit - gap
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.time()


# =====================================================================
# 三、熔断器：连续失败 → 暂时禁用 → 自动恢复
# =====================================================================
class CircuitBreaker:
    """按工具维度熔断。

    设计意图：
      · 某工具连续失败（网络断、目标站点挂了）时，继续重试只会拖慢小焦；
        连续失败达阈值后暂停该工具一段时间，期间直接返回中文提示，避免 4B 模型死磕。
      · 冷却结束自动恢复（半开状态），绝不永久禁用。
    """

    def __init__(self, threshold: int = CIRCUIT_THRESHOLD, cooldown: float = CIRCUIT_COOLDOWN) -> None:
        self.threshold = max(1, int(threshold))
        self.cooldown = float(cooldown)
        self._fail: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, tool: str) -> str:
        """返回空串=可调用；否则返回中文提示。"""
        with self._lock:
            until = self._open_until.get(tool, 0.0)
            if until and time.time() < until:
                left = int(until - time.time()) + 1
                return "工具暂时不可用（连续失败触发保护），请 %d 秒后重试" % left
            if until and time.time() >= until:
                # 冷却结束 → 自动恢复（半开）
                self._open_until.pop(tool, None)
                self._fail[tool] = 0
                logger.info("熔断恢复：%s", tool)
        return ""

    def record(self, tool: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._fail[tool] = 0
                self._open_until.pop(tool, None)
                return
            self._fail[tool] = self._fail.get(tool, 0) + 1
            if self._fail[tool] >= self.threshold:
                self._open_until[tool] = time.time() + self.cooldown
                logger.warning("熔断触发：%s 连续失败 %d 次，暂停 %.0f 秒", tool, self._fail[tool], self.cooldown)

    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {"fail_counts": dict(self._fail),
                    "open_until": {k: round(v, 1) for k, v in self._open_until.items()}}


# =====================================================================
# 四、批量管理：去重 / 限速 / 退避 / 代理轮换 / 部分失败隔离
# =====================================================================
class BatchManager:
    """批量抓取的公共逻辑。

    设计意图：
      · 4B 模型常给出重复 URL，先去重，避免白抓（验收：200 个 URL 含 50 重复 → 实际请求 ≤150）。
      · 逐个按限速节奏发起，单个失败只标记该项，不影响其它 URL（部分失败隔离）。
      · 429 触发指数退避 1→2→4→8 秒，最多重试 3 次。
      · 代理池轮换：同一代理最多用 5 次，避免代理被封耗尽。
    """

    def __init__(self, cfg: BridgeConfig, guard: SecurityGuard,
                 bcfg: Optional["BatchConfig"] = None) -> None:
        self.cfg = cfg
        self.guard = guard
        self.bcfg = bcfg or BatchConfig(rate_limit=getattr(cfg, "rate_limit", DEFAULT_RATE_LIMIT))
        self._proxy_uses: Dict[str, int] = {}
        self._proxy_lock = threading.Lock()
        self._domain_sem: Dict[str, threading.Semaphore] = {}
        self._domain_lock = threading.Lock()

    # ---------- 改进 4：跨域并发 + 同域串行 ----------
    def _sem_for(self, url: str) -> threading.Semaphore:
        """取该域名专属的并发闸门（同域最多 per_domain_limit 个同时进行）。"""
        try:
            host = urllib.parse.urlparse(url).netloc or "default"
        except Exception:
            host = "default"
        with self._domain_lock:
            sem = self._domain_sem.get(host)
            if sem is None:
                sem = threading.Semaphore(max(1, self.bcfg.per_domain_limit))
                self._domain_sem[host] = sem
            return sem

    def run_batch(self, fn: Callable[[str], Dict[str, Any]], urls: Sequence[str]) -> List[Dict[str, Any]]:
        """并发跑一批 URL，返回**与输入同序**的结果列表。

        并发模型（务实取舍）：
          · 插件对外的工具接口是**同步**的，而 Scrapling 内核跑在专用事件循环线程里
            （`AsyncRunner`）。在事件循环线程内部再用 asyncio.Semaphore 反而会自锁，
            所以这里用「有界线程池 + 每域信号量」实现同样的语义：
              - 跨域：最多 `concurrency` 个同时进行
              - 同域：最多 `per_domain_limit` 个同时进行（默认 1 = 串行，礼貌抓取）
              - 同域最小间隔仍由 SecurityGuard.wait_rate_limit 保证
        """
        if not urls:
            return []
        n = min(max(1, self.bcfg.concurrency), max(1, len(urls)))
        results: List[Optional[Dict[str, Any]]] = [None] * len(urls)

        def _wrapped(idx: int, url: str) -> None:
            with self._sem_for(url):
                try:
                    results[idx] = fn(url)
                except Exception as e:                       # 单个失败不影响其它 URL
                    results[idx] = {"status": 0, "url": url, "content": "",
                                    "error": "抓取失败：%s" % sanitize(e)[:120]}

        if n == 1:
            for i, u in enumerate(urls):
                _wrapped(i, u)
        else:
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="xj-bulk") as pool:
                futures = [pool.submit(_wrapped, i, u) for i, u in enumerate(urls)]
                for f in futures:
                    f.result()
        return [r if r is not None else {"status": 0, "url": urls[i], "content": "", "error": "未执行"}
                for i, r in enumerate(results)]

    @staticmethod
    def dedupe(urls: Sequence[str]) -> List[str]:
        """URL 去重（保持原顺序）。"""
        seen, out = set(), []
        for u in urls or []:
            if not isinstance(u, str):
                continue
            k = u.strip()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(k)
        return out

    def pick_proxy(self) -> Optional[str]:
        """轮换代理：优先用次数少的；单个代理使用 ≤ PROXY_MAX_USES 次。"""
        proxies = [p for p in (self.cfg.proxy_list or []) if p]
        if not proxies:
            return None
        with self._proxy_lock:
            avail = [p for p in proxies if self._proxy_uses.get(p, 0) < PROXY_MAX_USES]
            if not avail:
                self._proxy_uses.clear()          # 全部用满 → 重置计数（避免彻底没代理可用）
                avail = proxies
            p = random.choice(avail)
            self._proxy_uses[p] = self._proxy_uses.get(p, 0) + 1
            return p

    def prepare(self, urls: Sequence[str], ignore_robots: bool = False) -> Tuple[List[str], List[Dict[str, Any]]]:
        """去重 + 安全校验。返回 (可抓列表, 被拦截项列表)。"""
        clean = self.dedupe(urls)[:BULK_MAX_URLS]
        allowed, blocked = [], []
        for u in clean:
            reason = self.guard.check_ssrf(u)
            if reason:
                blocked.append({"status": 0, "url": u, "content": "", "error": reason})
                continue
            ok, why = self.guard.robots_allowed(u, USER_AGENT, ignore_robots)
            if not ok:
                blocked.append({"status": 0, "url": u, "content": "", "error": why})
                continue
            allowed.append(u)
        return allowed, blocked


# =====================================================================
# 六、指标采集（改进 2）：每个工具的调用/成功/失败/延迟/熔断次数
# =====================================================================
class MetricsCollector:
    """轻量指标收集器：**能看见**每个工具到底跑了多少次、慢在哪、失败多少。

    为什么需要：没有指标就只能靠"感觉"。出问题时至少要知道
    「是哪个工具在失败」「P95 有多慢」「熔断触发了几次」。

    指标（按工具维度）：calls / success / fail / total_latency / max_latency / min_latency /
                        circuit_breaks / last_error / last_called_at
    导出：`export()` 写 metrics.json；`to_prometheus()` 输出 Prometheus 文本格式（可直接抓取）。
    线程安全：插件可能被 Flask 多线程同时调用 → 全程加锁。
    """

    def __init__(self, path: str = "", enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self.path = path or os.path.join(os.path.dirname(SCRIPT_DIR), "logs", "scrapling_metrics.json")
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._total_calls = 0

    def record(self, tool: str, ok: bool, latency: float, circuit_break: bool = False,
               error: str = "") -> None:
        if not self.enabled:
            return
        with self._lock:
            m = self._tools.setdefault(tool, {
                "calls": 0, "success": 0, "fail": 0, "total_latency": 0.0,
                "max_latency": 0.0, "min_latency": 0.0, "circuit_breaks": 0,
                "last_error": "", "last_called_at": ""})
            m["calls"] += 1
            m["success" if ok else "fail"] += 1
            m["total_latency"] = round(m["total_latency"] + max(0.0, latency), 4)
            m["max_latency"] = round(max(m["max_latency"], latency), 4)
            m["min_latency"] = round(min(m["min_latency"], latency) if m["min_latency"] else latency, 4)
            if circuit_break:
                m["circuit_breaks"] += 1
            if error:
                m["last_error"] = sanitize(error)[:160]     # 脱敏后再存，避免密钥/Token 进指标
            m["last_called_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._total_calls += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            tools = {}
            for k, v in self._tools.items():
                calls = max(1, v["calls"])
                tools[k] = dict(v, avg_latency=round(v["total_latency"] / calls, 4))
            return {"started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_at)),
                    "uptime_seconds": round(time.time() - self.started_at, 1),
                    "total_calls": self._total_calls,
                    "tools": tools}

    def reset(self) -> None:
        with self._lock:
            self._tools.clear()
            self._total_calls = 0
            self.started_at = time.time()

    def export(self, path: str = "") -> str:
        """导出 metrics.json，返回文件路径。"""
        target = path or self.path
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)
            return target
        except OSError as e:
            logger.warning("指标导出失败: %s", sanitize(e))
            return ""

    def to_prometheus(self, extra: Optional[Dict[str, float]] = None) -> str:
        """输出 Prometheus 文本格式（# HELP / # TYPE + 样本行）。"""
        snap = self.snapshot()
        lines: List[str] = []

        def metric(name: str, help_text: str, mtype: str, rows: List[Tuple[Dict[str, str], float]]) -> None:
            lines.append("# HELP %s %s" % (name, help_text))
            lines.append("# TYPE %s %s" % (name, mtype))
            for labels, val in rows:
                if labels:
                    lb = ",".join('%s="%s"' % (k, str(v).replace('"', "'")) for k, v in labels.items())
                    lines.append("%s{%s} %s" % (name, lb, val))       # 有标签：metric{a="b"} v
                else:
                    lines.append("%s %s" % (name, val))               # 无标签：metric v（不能写成 metric{}）

        tools = snap["tools"]
        counters = [("calls", "calls_total", "调用总次数"), ("success", "success_total", "成功次数"),
                    ("fail", "fail_total", "失败次数"), ("circuit_breaks", "circuit_breaks_total", "熔断触发次数")]
        for key, suffix, desc in counters:
            metric("xiaojiao_scrapling_%s" % suffix, "抓取插件：%s" % desc, "counter",
                   [({"tool": t}, float(v[key])) for t, v in sorted(tools.items())])
        for key, suffix, desc in (("avg_latency", "latency_seconds_avg", "平均耗时（秒）"),
                                  ("max_latency", "latency_seconds_max", "最大耗时（秒）"),
                                  ("total_latency", "latency_seconds_total", "累计耗时（秒）")):
            metric("xiaojiao_scrapling_%s" % suffix, "抓取插件：%s" % desc, "gauge",
                   [({"tool": t}, float(v[key])) for t, v in sorted(tools.items())])
        for k, v in (extra or {}).items():
            metric("xiaojiao_scrapling_%s" % k, "抓取插件：%s" % k, "gauge", [({}, float(v))])
        lines.append("# HELP xiaojiao_scrapling_uptime_seconds 抓取插件运行时长（秒）")
        lines.append("# TYPE xiaojiao_scrapling_uptime_seconds gauge")
        lines.append("xiaojiao_scrapling_uptime_seconds %s" % snap["uptime_seconds"])
        return "\n".join(lines) + "\n"


# =====================================================================
# 五、会话回收（改进 1）：TTL / 空闲 / LRU 上限 + 后台巡检
# =====================================================================
class SessionManager:
    """会话生命周期管理：**不让浏览器会话无限堆积**。

    背景：`open_session` 每开一次就真起一个浏览器上下文，用户（或模型）忘了
    `close_session` 就会一直占内存，几十个会话能把机器拖垮 —— 而且没人会发现。

    三条回收规则（任一命中即回收）：
      ① **TTL**：会话自创建起最多活 `session_ttl` 秒（默认 30 分钟）
      ② **空闲**：`session_idle` 秒内没有任何操作（默认 5 分钟）
      ③ **上限**：同时存在超过 `max_sessions` 个（默认 20）时，**踢掉最久未使用**的
    后台线程每 `SESSION_SWEEP_INTERVAL` 秒（默认 60s）巡检一次；也可随时手动 sweep。

    设计取舍：
      · 只记录"我们自己开出来的会话"（register 时才登记），不去猜 Scrapling 内部状态；
      · 回收失败（会话已不存在）视为成功 —— 目标是"别留着"，不是"必须由我关掉"；
      · 线程是 daemon 且**首次登记时才启动**，避免 import 就拉线程（对测试/文档生成友好）。
    """

    def __init__(self, cfg: "BridgeConfig", closer: Optional[Callable[[str], Any]] = None,
                 interval: float = SESSION_SWEEP_INTERVAL, autostart: bool = True) -> None:
        self.max_sessions = self._pos(cfg.max_sessions, DEFAULT_MAX_SESSIONS, "max_sessions")
        self.ttl = self._pos(cfg.session_ttl, DEFAULT_SESSION_TTL, "session_ttl")
        self.idle = self._pos(cfg.session_idle, DEFAULT_SESSION_IDLE, "session_idle")
        self.interval = max(5.0, float(interval))
        self._closer = closer                      # 由 bridge 注入：真正调用 close_session
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._started = False
        self._autostart = autostart
        self.reclaimed: List[Dict[str, Any]] = []   # 最近回收记录（便于观测/测试）
        self.log = logger

    # ---------- 配置校验 ----------
    @staticmethod
    def _pos(value: Any, default: int, name: str) -> int:
        """把配置值转成正整数；非法值（0/负数/非数字）回退默认并给中文告警。"""
        try:
            v = int(value)
        except Exception:
            logger.warning("会话回收配置 %s 不是整数(%r)，已用默认值 %d", name, value, default)
            return default
        if v <= 0:
            logger.warning("会话回收配置 %s 必须为正整数(收到 %r)，已用默认值 %d", name, value, default)
            return default
        return v

    # ---------- 后台巡检 ----------
    def _ensure_thread(self) -> None:
        if self._started or not self._autostart:
            return
        self._started = True
        t = threading.Thread(target=self._loop, name="xj-session-sweeper", daemon=True)
        t.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep("periodic")
            except Exception as e:                 # 巡检线程绝不能死
                logger.warning("会话巡检异常(忽略并继续): %s", sanitize(e))

    def stop(self) -> None:
        self._stop.set()

    # ---------- 登记 / 续期 / 注销 ----------
    def register(self, sid: str, stype: str = "") -> None:
        if not sid:
            return
        with self._lock:
            now = time.time()
            self._sessions[sid] = {"created": now, "last_used": now, "type": stype or "?"}
        self._ensure_thread()
        self._enforce_limit()

    def touch(self, sid: str) -> None:
        with self._lock:
            rec = self._sessions.get(sid)
            if rec:
                rec["last_used"] = time.time()

    def forget(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            return {"active": len(self._sessions), "max_sessions": self.max_sessions,
                    "ttl_seconds": self.ttl, "idle_seconds": self.idle,
                    "sessions": [{"id": k, "type": v["type"],
                                  "age_s": round(now - v["created"], 1),
                                  "idle_s": round(now - v["last_used"], 1)}
                                 for k, v in self._sessions.items()],
                    "reclaimed_recent": self.reclaimed[-10:]}

    # ---------- 回收 ----------
    def _expired(self, now: float) -> List[Tuple[str, str]]:
        out = []
        with self._lock:
            for sid, rec in self._sessions.items():
                age, idle = now - rec["created"], now - rec["last_used"]
                if age > self.ttl:
                    out.append((sid, "ttl(%.0fs>%ds)" % (age, self.ttl)))
                elif idle > self.idle:
                    out.append((sid, "idle(%.0fs>%ds)" % (idle, self.idle)))
        return out

    def _enforce_limit(self) -> List[Tuple[str, str]]:
        """超过 max_sessions → 踢最久未使用的（LRU）。"""
        out = []
        with self._lock:
            overflow = len(self._sessions) - self.max_sessions
            if overflow <= 0:
                return out
            lru = sorted(self._sessions.items(), key=lambda kv: kv[1]["last_used"])[:overflow]
            out = [(sid, "lru(超出上限 %d)" % self.max_sessions) for sid, _ in lru]
        self._close_all(out)
        return out

    def sweep(self, reason: str = "manual") -> Dict[str, Any]:
        """执行一次回收，返回 {reclaimed: [...], active: n}。"""
        out = self._expired(time.time())
        out += self._enforce_limit()
        self._close_all(out, reason)
        return {"reclaimed": [{"id": s, "why": w} for s, w in out], "active": self.stats()["active"]}

    def _close_all(self, items: Sequence[Tuple[str, str]], reason: str = "") -> None:
        for sid, why in items:
            self.forget(sid)
            ok, err = True, ""
            if self._closer:
                try:
                    self._closer(sid)
                except Exception as e:
                    ok, err = False, str(e)[:120]
            self.reclaimed.append({"id": sid, "why": why, "closed": ok, "error": err,
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": reason})
            self.log.info("会话回收 %s（%s）%s", sid, why, "" if ok else "关闭失败: " + err)


# =====================================================================
# 五、自适应选择器
# =====================================================================
@dataclass
class SelectorRecord:
    """选择器指纹：保存时记录足够多的特征，元素移动/改版后仍能匹配回来。"""
    selector: str
    tag: str = ""
    classes: List[str] = field(default_factory=list)
    elem_id: str = ""
    text: str = ""
    parent_path: str = ""
    sibling_index: int = -1
    attrs: Dict[str, str] = field(default_factory=dict)
    saved_at: float = field(default_factory=time.time)


class SelectorManager:
    """自适应选择器：保存指纹 → 恢复时按相似度匹配 → 返回多候选并给置信度。

    设计意图：
      · 目标站点改版后，原 CSS 选择器往往失效；这里保存标签/class/id/文本/父路径/兄弟位置/属性集合，
        恢复时逐项加权比对，返回**全部候选 + 置信度**，而不是只挑第一个（避免选错元素）。
      · 目标元素被删除时返回 {"status":"not_found"} 结构化结果，绝不返回错误元素。
    """

    WEIGHTS = {"tag": 0.15, "classes": 0.25, "id": 0.15, "text": 0.25, "path": 0.10, "sibling": 0.10}

    def __init__(self, store_path: str = SELECTOR_STORE) -> None:
        self.store_path = store_path
        self._lock = threading.Lock()

    # ---------- 存取 ----------
    def _load(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.store_path):
                with open(self.store_path, encoding="utf-8") as f:
                    d = json.load(f)
                    return d if isinstance(d, dict) else {}
        except Exception as e:
            logger.warning("选择器库读取失败: %s", sanitize(e))
        return {}

    def _save(self, data: Dict[str, Any]) -> None:
        try:
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError as e:
            logger.warning("选择器库写入失败（磁盘/权限）: %s", sanitize(e))

    def save(self, name: str, rec: SelectorRecord) -> None:
        """保存选择器指纹（含标签/class/id/文本/父路径/兄弟位置/属性集合）。"""
        with self._lock:
            data = self._load()
            data[name] = rec.__dict__
            self._save(data)

    def load(self, name: str) -> Optional[SelectorRecord]:
        with self._lock:
            raw = self._load().get(name)
        if not isinstance(raw, dict):
            return None
        return SelectorRecord(
            selector=raw.get("selector", ""), tag=raw.get("tag", ""),
            classes=list(raw.get("classes") or []), elem_id=raw.get("elem_id", ""),
            text=raw.get("text", ""), parent_path=raw.get("parent_path", ""),
            sibling_index=int(raw.get("sibling_index", -1)), attrs=dict(raw.get("attrs") or {}),
            saved_at=float(raw.get("saved_at", 0.0)))

    # ---------- 相似度 ----------
    @staticmethod
    def _text_sim(a: str, b: str) -> float:
        a, b = (a or "")[:200], (b or "")[:200]
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def similarity(self, rec: SelectorRecord, cand: SelectorRecord) -> float:
        """加权相似度（0~1）。"""
        w = self.WEIGHTS
        score = 0.0
        score += w["tag"] * (1.0 if rec.tag and rec.tag == cand.tag else 0.0)
        if rec.classes or cand.classes:
            sa, sb = set(rec.classes), set(cand.classes)
            score += w["classes"] * (len(sa & sb) / max(1, len(sa | sb)))
        score += w["id"] * (1.0 if rec.elem_id and rec.elem_id == cand.elem_id else 0.0)
        score += w["text"] * self._text_sim(rec.text, cand.text)
        score += w["path"] * (1.0 if rec.parent_path and rec.parent_path == cand.parent_path else 0.0)
        score += w["sibling"] * (1.0 if rec.sibling_index >= 0 and rec.sibling_index == cand.sibling_index else 0.0)
        return round(min(1.0, score), 3)

    def rank(self, rec: SelectorRecord, candidates: Sequence[SelectorRecord],
             high_sim: float = 0.9) -> Dict[str, Any]:
        """对候选打分排序。

        返回 {"status": "ok"|"not_found", "best": {...}, "candidates": [...], "note": str}
        · 无候选 → not_found（结构化错误，不返回错误元素）
        · 存在多个高相似(>90%)候选 → 全部返回，不擅自只取第一个
        """
        if not candidates:
            return {"status": "not_found", "error": "元素不存在", "candidates": []}
        scored = []
        for c in candidates:
            s = self.similarity(rec, c)
            scored.append({"selector": c.selector, "confidence": s,
                           "text": clip_content(c.text, 200), "attrs": c.attrs})
        scored.sort(key=lambda x: -x["confidence"])
        high = [c for c in scored if c["confidence"] > high_sim]
        note = ""
        if len(high) > 1:
            note = "存在 %d 个高度相似(>90%%)候选，已全部返回，请人工/模型确认" % len(high)
        return {"status": "ok", "best": scored[0], "candidates": scored[:10],
                "ambiguous": len(high) > 1, "note": note}


# =====================================================================
# 六、MCP 客户端（stdio / http）+ inproc 回退
# =====================================================================
class MCPClient:
    """Scrapling MCP 客户端。

    设计意图：
      · 会话持久化：首次调用时建立连接并 initialize，之后复用（避免每次重建开销）。
      · 健康检查：调用前确认进程/连接仍有效，失效自动重连（崩溃后 30 秒内恢复）。
      · 超时：单次调用超过 timeout 即放弃并给出中文错误，绝不无限等待。
      · 4B 场景默认 auto：MCP 不可用则回退 inproc（进程内直连 Scrapling），保证"能用"优先。
    """

    def __init__(self, cfg: BridgeConfig) -> None:
        self.cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: Dict[int, "queue.Queue"] = {}
        self._reader_threads: List[threading.Thread] = []
        self._last_start = 0.0
        self._mode_actual = ""              # 实际生效模式：mcp-stdio / mcp-http / inproc
        self._inproc_server: Any = None
        self.available = False
        self.last_error = ""

    # ---------- 模式判定 ----------
    def resolve_mode(self) -> str:
        m = (self.cfg.mode or "auto").lower()
        if m == "inproc":
            return "inproc"
        if m == "mcp":
            return "mcp"
        return "auto"

    # ---------- stdio 传输 ----------
    def _start_stdio(self) -> bool:
        cmd = [self.cfg.mcp_command, "mcp"]
        if self.cfg.executable_path and os.path.exists(self.cfg.executable_path):
            cmd += ["--executable-path", self.cfg.executable_path]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.last_error = "Scrapling MCP 未运行，请先执行 scrapling mcp（启动失败：%s）" % sanitize(e)[:80]
            return False
        self._last_start = time.time()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if not self._initialize():
            return False
        self._mode_actual = "mcp-stdio"
        return True

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            mid = msg.get("id")
            if mid is not None and mid in self._pending:
                self._pending[mid].put(msg)

    def _read_stderr(self) -> None:
        """MCP 子进程的 stderr 只记日志（脱敏），不打断主流程。"""
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            if line.strip():
                logger.debug("mcp stderr: %s", sanitize(line.strip())[:200])

    def _send(self, obj: Dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """发一条 JSON-RPC 并等响应（超时保护）。"""
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._pending[rid] = __import__("queue").Queue()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = self._pending[rid].get(timeout=timeout)
        except Exception:
            raise TimeoutError("MCP 调用超时（%.0f 秒）" % timeout)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            raise RuntimeError(str(msg["error"])[:200])
        return msg.get("result") or {}

    def _initialize(self) -> bool:
        try:
            self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "xiaojiao", "version": "1.0"}},
                      timeout=MCP_INIT_TIMEOUT)
            with self._lock:
                self._next_id += 1
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            return True
        except Exception as e:
            self.last_error = "Scrapling MCP 未运行，请先执行 scrapling mcp（初始化失败：%s）" % sanitize(e)[:80]
            return False

    # ---------- http 传输 ----------
    def _start_http(self) -> bool:
        """streamable-http 模式：用 mcp 官方客户端库连接（未装则视为不可用）。"""
        try:
            from mcp import ClientSession                      # type: ignore
            from mcp.client.streamable_http import streamablehttp_client  # type: ignore
        except Exception:
            self.last_error = "MCP http 模式需要 mcp 包：pip install mcp"
            return False
        self._mode_actual = "mcp-http"
        self._http = (ClientSession, streamablehttp_client)
        return True

    # ---------- 对外 ----------
    def ensure(self) -> bool:
        """确保连接可用（健康检查 + 自动重连）。

        auto 模式的次序刻意设计为 **inproc 优先**：
          Scrapling 的 MCP 服务在出错时只回一句 "Error executing tool xxx"，
          4B 模型拿不到原因；而进程内直连能拿到真实异常并转成中文提示。
          需要严格 MCP 场景（如容器化部署、验收 MCP 通道）时把 mode 设为 "mcp"。
        """
        mode = self.resolve_mode()
        if self._healthy() or (self._mode_actual == "inproc" and self._inproc_server is not None):
            return True
        if mode == "inproc":
            return self._start_inproc()
        if mode == "mcp":
            if self.cfg.scrapling_mcp_url and self._start_http():
                self.available = True
                return True
            if self._start_stdio():
                self.available = True
                return True
            return False
        # auto：先进程内直连（错误可读、无子进程、延迟更低），不可用再退 MCP
        if self._start_inproc():
            self.available = True
            return True
        if self.cfg.scrapling_mcp_url and self._start_http():
            self.available = True
            return True
        if self._start_stdio():
            self.available = True
            return True
        return False

    def _healthy(self) -> bool:
        if self._mode_actual == "mcp-stdio":
            return bool(self._proc and self._proc.poll() is None)
        if self._mode_actual == "mcp-http":
            return True
        return False

    def _start_inproc(self) -> bool:
        """进程内直连 Scrapling（不经 MCP 协议）——最稳的保底路径。"""
        try:
            from scrapling.core.ai import ScraplingMCPServer      # type: ignore
        except Exception as e:
            self.last_error = ("未能加载 Scrapling（请先安装：python -m pip install "
                               "\"scrapling[fetchers]\"）: %s" % sanitize(e)[:100])
            return False
        try:
            self._inproc_server = ScraplingMCPServer(
                executable_path=self.cfg.executable_path or None)
            self._mode_actual = "inproc"
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = "Scrapling 初始化失败: %s" % sanitize(e)[:120]
            return False

    def call_tool(self, name: str, args: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """调用 Scrapling 工具（MCP 或 inproc），返回统一 dict。错误统一转中文可读。"""
        args = _adapt_args(name, args, default_timeout=self.cfg.timeout)   # 白名单过滤 + timeout 单位换算
        if not self.ensure():
            return {"status": 0, "url": args.get("url", ""), "content": "",
                    "error": self.last_error or "Scrapling MCP 未运行，请先执行 scrapling mcp"}
        try:
            if self._mode_actual == "inproc":
                res = self._call_inproc(name, args, timeout)
            else:
                res = self._call_mcp(name, args, timeout)
        except TimeoutError as e:
            res = {"status": 0, "url": args.get("url", ""), "content": "", "error": str(e)}
        except Exception as e:
            res = {"status": 0, "url": args.get("url", ""), "content": "",
                   "error": "抓取失败：%s" % sanitize(e)[:160]}
        # 英文原文（Pydantic / Playwright / 库里抛的）→ 中文可读
        if isinstance(res, dict) and res.get("error"):
            res["error"] = _humanize_error(res["error"])
        return res

    def _call_mcp(self, name: str, args: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        res = self._rpc("tools/call", {"name": name, "arguments": args}, timeout=timeout)
        # MCP 工具结果：content 里是文本块（Scrapling 返回 JSON 字符串）
        texts = []
        for item in (res.get("content") or []):
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        raw = "\n".join(texts).strip()
        # 截图类：MCP 返回 image 块（base64）→ 落盘成文件并回路径
        blocks = res.get("content") or []
        if any(isinstance(b, dict) and b.get("type") == "image" for b in blocks):
            return _handle_screenshot(blocks)
        # 工具执行失败：MCP 只给一句笼统说明，这里转成中文可读并给出排查建议
        if res.get("isError"):
            hint = raw[:200] or "未知错误"
            tip = ""
            if "markdownify" in raw:
                tip = "（缺少 markdownify：pip install markdownify）"
            elif "timeout" in raw.lower():
                tip = "（请求超时，可加大 timeout 或改用 get）"
            return {"status": 0, "url": args.get("url", ""), "content": "",
                    "error": "Scrapling 抓取失败：%s%s" % (hint, tip)}
        try:
            data = json.loads(raw)
        except Exception:
            data = {"status": 0, "url": args.get("url", ""), "content": raw, "error": ""}
        return self._normalize(data)

    def _call_inproc(self, name: str, args: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        srv = self._inproc_server

        def _to_dict(model: Any) -> Dict[str, Any]:
            try:
                if hasattr(model, "model_dump"):
                    return model.model_dump()
                if hasattr(model, "dict"):
                    return model.dict()
                if isinstance(model, dict):
                    return model
                return dict(model)
            except Exception:
                return {"status": 0, "url": "", "content": str(model), "error": ""}

        # 白名单内的方法名即 Scrapling MCP 的真实工具名（make_request / open_session / screenshot …）
        if name not in _TOOL_PARAMS:
            return {"status": 0, "url": args.get("url", ""), "content": "",
                    "error": "不支持的抓取动作：%s" % name}
        try:
            method = getattr(srv, name)
        except AttributeError:
            return {"status": 0, "url": args.get("url", ""), "content": "",
                    "error": "当前 Scrapling 版本不支持该动作：%s" % name}
        out = _RUNNER.run(method(**args), timeout=timeout)
        # 会话信息 / 会话列表：直接返回结构化文本
        if name in ("open_session", "open_request_session", "close_session", "list_sessions"):
            data = [_to_dict(x) for x in out] if isinstance(out, list) else _to_dict(out)
            return {"status": 200, "url": "", "content": json.dumps(data, ensure_ascii=False, default=str),
                    "error": ""}
        # 截图：把图片落盘，返回文件路径（小焦可直接展示/打开）
        if name == "screenshot":
            items = out if isinstance(out, list) else [out]
            return _handle_screenshot(items)
        if isinstance(out, list):
            return self._normalize([_to_dict(x) for x in out])
        return self._normalize(_to_dict(out))

    # ---------- 结果标准化 ----------
    @staticmethod
    def _normalize(data: Any) -> Dict[str, Any]:
        """把 Scrapling 的 {status, content:[str], url} 统一成 {status,url,content,error}。"""
        if isinstance(data, list):
            items = [MCPClient._normalize_one(x) for x in data]
            return {"status": 200 if items else 0, "url": "", "content":
                    json.dumps(items, ensure_ascii=False), "error": "", "items": items}
        return MCPClient._normalize_one(data)

    @staticmethod
    def _normalize_one(x: Any) -> Dict[str, Any]:
        if not isinstance(x, dict):
            return {"status": 0, "url": "", "content": str(x), "error": ""}
        status = x.get("status", 0)
        url = x.get("url", "") or ""
        c = x.get("content", "")
        if isinstance(c, list):
            c = "\n".join(str(i) for i in c)
        c = str(c or "")
        if c.lstrip().startswith("<"):            # HTML → Markdown，方便 4B 模型阅读
            c = html_to_markdown(c)
        c = normalize_markdown(c)                 # Setext 标题 → ATX，前端才能渲染成标题
        err = x.get("error", "") or ""
        if not err and isinstance(status, int) and status >= 400:
            err = "目标站点返回 HTTP %d" % status
        return {"status": status, "url": url, "content": clip_content(c), "error": err}

    def shutdown(self) -> None:
        """退出清理：确保不残留 Scrapling / Playwright 子进程。"""
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
        except Exception as e:
            LOG.debug("忽略异常(%s:1652): %s", __file__, 1652, e)


# =====================================================================
# 插件主体
# =====================================================================
_RUNNER = AsyncRunner()                # 全局事件循环线程（进程内共享）
_CONFIG = BridgeConfig.load()
_GUARD = SecurityGuard(_CONFIG.rate_limit, _CONFIG.allow_robots_skip)
# 改进 4：批量策略从 scrapling.batch 段读取（也可用环境变量覆盖）
_BATCH_CFG = BatchConfig.load(_load_scrapling_section(), _CONFIG.rate_limit)
if _BATCH_CFG.error:
    logger.error("批量配置不合法：%s（批量工具会返回该错误提示）", _BATCH_CFG.error)
_BATCH = BatchManager(_CONFIG, _GUARD, _BATCH_CFG)
_SELECTORS = SelectorManager()
_BREAKER = CircuitBreaker(_CONFIG.circuit_breaker_threshold, _CONFIG.circuit_breaker_timeout)
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="xj-scrapling")
_CLIENT = MCPClient(_CONFIG)
# 会话回收器（改进 1）：closer 用 _CLIENT 真正关掉会话；后台线程按需启动
_SESSIONS = SessionManager(_CONFIG, closer=lambda sid: _CLIENT.call_tool(
    "close_session", {"session_id": sid}, timeout=20))
atexit.register(_SESSIONS.stop)
# 指标采集（改进 2）
_METRICS = MetricsCollector()
_STEALTH_LAST = [0.0]                  # 隐身模式上次请求时间（限流用）


def _stealth_gap() -> None:
    """隐身模式：连续请求间隔 ≥ STEALTH_MIN_GAP 秒，降低触发频率检测的概率。"""
    gap = time.time() - _STEALTH_LAST[0]
    if gap < STEALTH_MIN_GAP:
        time.sleep(STEALTH_MIN_GAP - gap)
    _STEALTH_LAST[0] = time.time()


def _random_impersonate() -> str:
    """随机浏览器指纹版本：让每次请求指纹不同，避免被指纹关联。"""
    return random.choice(["chrome", "chrome110", "chrome116", "chrome120", "chrome124"])


class ScraplingBridge:
    """小焦插件：Scrapling MCP 桥接。

    对外暴露 17 个工具（原生 13 个 1:1 + 3 个增强 + 1 个兼容入口，把复杂度全部封装在内），
    4B 模型只需决定"抓哪个 URL"，不需要懂指纹/退避/代理/熔断。
    """

    def __init__(self) -> None:
        self._cfg = _CONFIG
        atexit.register(_CLIENT.shutdown)
        atexit.register(_RUNNER.shutdown)
        atexit.register(_POOL.shutdown, False)
        logger.info("Scrapling 桥接已加载（模式=%s，Chrome=%s）",
                    self._cfg.mode, self._cfg.executable_path or "内置 Chromium")

    # ------------------------------------------------------------------
    # 工具描述（≤60 中文字/条，避免 4B 模型选择困难）
    # ------------------------------------------------------------------
    def get_tool_descriptions(self) -> List[Dict[str, Any]]:
        def T(name: str, desc: str, props: Dict[str, Any], required: Optional[List[str]] = None):
            return {"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": required or []}}

        S_URL = {"type": "string", "description": "url: 要抓取的网址"}
        S_URLS = {"type": "array", "items": {"type": "string"}, "description": "urls: 网址列表"}
        S_SEL = {"type": "string", "description": "selector: CSS 选择器"}
        S_TO = {"type": "integer", "description": "timeout: 超时秒数，默认60"}
        S_WAIT = {"type": "string", "description": "wait_selector: 等元素出现再抓"}
        S_STEALTH = {"type": "boolean", "description": "stealth: 是否隐身，默认否"}
        S_ADAPT = {"type": "boolean", "description": "adaptive: 自适应匹配，默认是"}
        S_NAME = {"type": "string", "description": "name: 选择器存档名"}
        S_SAVE = {"type": "string", "description": "save_to: 并存成本地文件名(可选)"}
        S_FN = {"type": "string", "description": "filename: 保存文件名(可选)"}
        S_SID = {"type": "string", "description": "session_id: 会话ID(open 后返回)"}
        S_STYPE = {"type": "string", "description": "session_type: dynamic 或 stealthy，默认 dynamic"}
        S_FULL = {"type": "boolean", "description": "full_page: 是否整页截图，默认否"}
        S_IGN = {"type": "boolean", "description": "ignore_robots: 忽略robots.txt(默认否，仅在你确认有权抓取时用)"}

        return [
            # ---------- Scrapling 原生 13 个工具（1:1 暴露，名字与官方一致）----------
            T("make_request", "原生名·抓普通网页(纯HTTP，最快)。等同 get",
              {"url": S_URL, "timeout": S_TO, "save_to": S_SAVE, "ignore_robots": S_IGN}, ["url"]),
            T("open_session", "开浏览器会话(登录态用)。开完用 session_fetch 抓",
              {"session_type": S_STYPE, "session_id": S_SID}, []),
            T("open_request_session", "开HTTP会话(保持 cookie)。开完用 session_make_request",
              {"session_id": S_SID}, []),
            T("close_session", "关闭会话并释放资源。参数: session_id 必填", {"session_id": S_SID}, ["session_id"]),
            T("list_sessions", "列出当前所有会话", {}, []),
            T("session_fetch", "用已开会话抓页面(保持登录态/已过验证)",
              {"url": S_URL, "session_id": S_SID, "wait_selector": S_WAIT, "ignore_robots": S_IGN},
              ["url", "session_id"]),
            T("session_make_request", "用HTTP会话发请求(保持 cookie)",
              {"url": S_URL, "session_id": S_SID, "ignore_robots": S_IGN}, ["url", "session_id"]),
            T("screenshot", "给页面截图(可整页)，存 media/screenshot/ 并返回路径",
              {"url": S_URL, "session_id": S_SID, "full_page": S_FULL, "ignore_robots": S_IGN},
              ["url", "session_id"]),
            # ---------- 小焦增强（原生没有 / 更好用）----------
            T("get", "抓取网页(普通HTTP，快)。参数: url 必填",
              {"url": S_URL, "stealth": S_STEALTH, "timeout": S_TO, "save_to": S_SAVE, "ignore_robots": S_IGN}, ["url"]),
            T("bulk_get", "批量抓多个网页(普通HTTP，快)。参数: urls 必填",
              {"urls": S_URLS, "stealth": S_STEALTH, "ignore_robots": S_IGN}, ["urls"]),
            T("fetch", "用浏览器渲染抓取(能抓动态页面)",
              {"url": S_URL, "wait_selector": S_WAIT, "timeout": S_TO, "save_to": S_SAVE, "ignore_robots": S_IGN}, ["url"]),
            T("bulk_fetch", "批量用浏览器渲染抓取多个页面",
              {"urls": S_URLS, "ignore_robots": S_IGN}, ["urls"]),
            T("stealthy_fetch", "隐身抓取(绕Cloudflare)。仅在普通请求失败时用，开销大",
              {"url": S_URL, "timeout": S_TO, "save_to": S_SAVE, "ignore_robots": S_IGN}, ["url"]),
            T("bulk_stealthy_fetch", "批量隐身抓取(开销大，≤20个)",
              {"urls": S_URLS, "ignore_robots": S_IGN}, ["urls"]),
            T("scrape_with_selector", "按选择器抓取内容，自适应防改版",
              {"url": S_URL, "selector": S_SEL, "adaptive": S_ADAPT, "name": S_NAME, "ignore_robots": S_IGN},
              ["url", "selector"]),
            T("browser_session", "会话+截图的聚合入口(一个工具走完全流程):登录态抓取/整页截图",
              {"action": {"type": "string",
                          "description": "action: open/open_http/close/list/fetch/request/screenshot"},
               "url": S_URL, "session_id": {"type": "string", "description": "session_id: 会话ID"},
               "session_type": {"type": "string", "description": "session_type: dynamic或stealthy"},
               "full_page": {"type": "boolean", "description": "full_page: 是否整页截图"},
               "ignore_robots": S_IGN},
              ["action"]),
            T("download", "下载任意文件(PDF/EPUB/ZIP/图片/音视频等)存本地，返回路径",
              {"url": S_URL, "filename": S_FN, "ignore_robots": S_IGN}, ["url"]),
        ]

    # ------------------------------------------------------------------
    # 指标 / 会话 观测接口（改进 1 + 2：供 app 的 /metrics 与排障使用）
    # ------------------------------------------------------------------
    def metrics_snapshot(self) -> Dict[str, Any]:
        """返回全部指标（含会话统计与熔断状态），可直接 jsonify。"""
        snap = _METRICS.snapshot()
        snap["sessions"] = _SESSIONS.stats()
        snap["breaker"] = _BREAKER.state()
        return snap

    def metrics_prometheus(self) -> str:
        """Prometheus 文本格式（可直接被 /metrics 暴露）。"""
        sess = _SESSIONS.stats()
        return _METRICS.to_prometheus(extra={"sessions_active": sess["active"],
                                             "sessions_max": sess["max_sessions"]})

    def metrics_export(self, path: str = "") -> str:
        """把指标写成 metrics.json（默认 logs/scrapling_metrics.json）。"""
        return _METRICS.export(path)

    def sessions_stats(self) -> Dict[str, Any]:
        """会话回收器状态（活跃会话、上限、最近回收记录）。"""
        return _SESSIONS.stats()

    def sessions_sweep(self) -> Dict[str, Any]:
        """手动触发一次会话回收（运维/测试用）。"""
        return _SESSIONS.sweep("manual")

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------
    def execute(self, tool_name: str, params: Dict[str, Any]) -> str:
        """统一入口：所有异常都转成中文可读错误，绝不把 Python 堆栈丢给 4B 模型。

        改进 2：这里顺带把每次调用记进指标（次数/成功/失败/耗时/熔断），供 /metrics 与排障使用。
        """
        params = params or {}
        _t0 = time.time()

        def _done(out: str, circuit_break: bool = False) -> str:
            try:
                _err = (json.loads(out) or {}).get("error") or ""
            except Exception:
                _err = ""
            _METRICS.record(tool_name, ok=(not _is_err(out)) or _is_safety_block(out),
                            latency=time.time() - _t0, circuit_break=circuit_break, error=_err)
            return out

        try:
            # 熔断检查（每个工具独立）
            msg = _BREAKER.check(tool_name)
            if msg:
                return _done(fmt_result(0, params.get("url", ""), "", msg), circuit_break=True)

            handler = {
                # 小焦增强
                "get": self._do_get,
                "make_request": self._do_get,          # 原生名，等同 get
                "bulk_get": self._do_bulk_get,
                "fetch": self._do_fetch,
                "bulk_fetch": self._do_bulk_fetch,
                "stealthy_fetch": self._do_stealthy_fetch,
                "bulk_stealthy_fetch": self._do_bulk_stealthy_fetch,
                "scrape_with_selector": self._do_scrape_selector,
                "download": self._do_download,
                # 原生会话/截图 7 个：1:1 直通
                "open_session": lambda q: self._do_native_session("open_session", q),
                "open_request_session": lambda q: self._do_native_session("open_request_session", q),
                "close_session": lambda q: self._do_native_session("close_session", q),
                "list_sessions": lambda q: self._do_native_session("list_sessions", q),
                "session_fetch": lambda q: self._do_native_session("session_fetch", q),
                "session_make_request": lambda q: self._do_native_session("session_make_request", q),
                "screenshot": lambda q: self._do_native_session("screenshot", q),
                # 兼容入口
                "browser_session": self._do_browser_session,
            }.get(tool_name)
            if not handler:
                return _done(fmt_result(0, "", "", "未知工具：%s" % tool_name))

            out = handler(params)
            # 安全拦截（SSRF/robots）属于"正常拒绝"，不是工具故障 → 不计熔断，
            # 否则连续拦截几个内网地址就会把工具误判为不可用。
            _BREAKER.record(tool_name, (not _is_err(out)) or _is_safety_block(out))
            return _done(out)
        except TimeoutError as e:
            _BREAKER.record(tool_name, False)
            return _done(fmt_result(0, params.get("url", ""), "", str(e)))
        except Exception as e:                      # 兜底：任何异常都变中文可读
            _BREAKER.record(tool_name, False)
            logger.warning("工具 %s 异常: %s", tool_name, sanitize(e))
            return _done(fmt_result(0, params.get("url", ""), "", "抓取失败：%s" % sanitize(e)[:160]))

    # ------------------------------------------------------------------
    # 各工具实现
    # ------------------------------------------------------------------
    def _single(self, tool: str, url: str, extra: Dict[str, Any], stealth: bool = False,
                save_to: str = "", ignore_robots: bool = False) -> str:
        """单个 URL 抓取：SSRF/robots 校验 → 限速 → 调 MCP/inproc → 统一结果。

        save_to 非空时把正文存成本地文件（长文/连载章节直接落盘，不塞满对话）。
        ignore_robots=True 表示用户显式要求忽略 robots.txt（单次生效）。
        """
        url = (url or "").strip()
        reason = _GUARD.check_ssrf(url)
        if reason:
            return fmt_result(0, url, "", reason)
        ok, why = _GUARD.robots_allowed(url, USER_AGENT, ignore_robots)
        if not ok:
            return fmt_result(0, url, "", why)
        _GUARD.wait_rate_limit(url)
        if stealth:
            _stealth_gap()
        args = dict(extra)
        args["url"] = url
        args.setdefault("extraction_type", _EXTRACT_TYPE)
        args.setdefault("timeout", self._cfg.timeout)
        if self._cfg.executable_path:
            args.setdefault("executable_path", self._cfg.executable_path)
        proxy = _BATCH.pick_proxy()
        if proxy:
            args.setdefault("proxy", proxy)
        # 用户显式给了 timeout → ① 收敛重试（Scrapling 默认 retries=3，会把 6 秒超时拖成 22 秒）
        #                        ② 给客户端加硬上限，保证"说 6 秒就 6 秒量级返回"
        try:
            _ut = float(extra.get("timeout") or 0)
        except Exception:
            _ut = 0
        if _ut > 0:
            args.setdefault("retries", 1)
        _client_to = _bound_client_timeout(tool, _ut, self._cfg.timeout)
        res = _CLIENT.call_tool(tool, args, timeout=_client_to)
        if res.get("error"):
            return fmt_result(res.get("status", 0), url, res.get("content", ""), res["error"])
        _body = res.get("content", "") or ""
        if save_to:                       # 存文件（长文/连载）
            try:
                fp = _save_text_file(save_to, _body)
                return fmt_result(res.get("status", 0), url,
                                  "💾 已保存：%s（%d 字）\n\n%s" % (fp, len(_body), _body[:600]), "")
            except OSError as e:
                return fmt_result(res.get("status", 0), url, _body,
                                  "保存失败（磁盘空间/权限）：%s" % sanitize(e)[:100])
        return fmt_result(res.get("status", 0), url, _body, "")

    # ---- get ----
    @staticmethod
    def _with_timeout(p: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
        """把用户传的 timeout 带上（秒）。

        压力测试暴露的 bug：get / fetch / stealthy_fetch / scrape_with_selector 之前只用了
        配置里的默认 timeout（60s），**用户传的 timeout 被静默忽略** —— 6 秒超时却等了 11 秒才回来。
        """
        try:
            t = float(p.get("timeout")) if p.get("timeout") not in (None, "") else 0
        except Exception:
            t = 0
        if t > 0:
            extra["timeout"] = t
        return extra

    def _do_get(self, p: Dict[str, Any]) -> str:
        extra = self._with_timeout(p, {"method": "GET", "impersonate": _random_impersonate()})
        return self._single("make_request", p.get("url", ""), extra,
                            stealth=bool(p.get("stealth")), save_to=(p.get("save_to") or ""),
                            ignore_robots=bool(p.get("ignore_robots")))

    # ---- fetch ----
    def _do_fetch(self, p: Dict[str, Any]) -> str:
        extra: Dict[str, Any] = {"headless": self._cfg.headless}
        if p.get("wait_selector"):
            extra["wait_selector"] = p["wait_selector"]
        self._with_timeout(p, extra)
        return self._single("fetch", p.get("url", ""), extra, save_to=(p.get("save_to") or ""),
                            ignore_robots=bool(p.get("ignore_robots")))

    # ---- stealthy_fetch ----
    def _do_stealthy_fetch(self, p: Dict[str, Any]) -> str:
        extra: Dict[str, Any] = {
            "headless": self._cfg.headless,
            "solve_cloudflare": bool(self._cfg.solve_cloudflare),
            "block_webrtc": True,          # 防 WebRTC 泄露真实 IP
            "allow_webgl": True,
            "hide_canvas": True,           # 指纹随机化：Canvas
            "google_search": True,
        }
        return self._single("stealthy_fetch", p.get("url", ""), extra, stealth=True,
                            save_to=(p.get("save_to") or ""),
                            ignore_robots=bool(p.get("ignore_robots")))

    def _download_via_scrapling(self, url: str):
        """用 Scrapling 的浏览器指纹抓原始字节（应对 WAF 拒绝普通 UA 的场景）。

        返回 (bytes|None, status, content_type)。
        """
        async def _get():
            from scrapling.fetchers import FetcherSession
            async with FetcherSession(impersonate="chrome") as s:
                r = await s.get(url, stealthy_headers=True, follow_redirects="safe",
                                timeout=self._cfg.timeout)
                body = getattr(r, "body", None)
                if body is None:
                    body = getattr(r, "content", None)
                if isinstance(body, str):
                    body = body.encode("utf-8", "replace")
                ct = ""
                try:
                    ct = (r.headers.get("content-type") or "").split(";")[0].strip()
                except Exception as e:
                    LOG.debug("忽略异常(%s:1984): %s", __file__, 1984, e)
                return body, getattr(r, "status", 0), ct
        try:
            return _RUNNER.run(_get(), timeout=self._cfg.timeout)
        except Exception as e:
            logger.warning("指纹下载失败: %s", sanitize(e))
            return None, 0, ""

    # ---- download：下载任意文件（PDF/EPUB/TXT/ZIP/图片/音视频…）----
    def _do_download(self, p: Dict[str, Any]) -> str:
        """把 URL 指向的**文件**下载到本地 downloads/ 目录，返回保存路径与大小。

        设计意图：Scrapling 只负责"抓网页正文"，而 PDF/ZIP/图片这类文件必须直接下载；
        这里做流式下载 + 大小上限 + 安全校验（SSRF/robots/限速），存本地后用户可直接打开。
        """
        import requests as _rq
        url = (p.get("url") or "").strip()
        if not url:
            return fmt_result(0, "", "", "需要提供 url")
        reason = _GUARD.check_ssrf(url)
        if reason:
            return fmt_result(0, url, "", reason)
        ok, why = _GUARD.robots_allowed(url, USER_AGENT, bool(p.get("ignore_robots")))
        if not ok:
            return fmt_result(0, url, "", why)
        _GUARD.wait_rate_limit(url)

        base = os.path.abspath(_out_dir("downloads"))
        name = (p.get("filename") or "").strip() or _guess_filename(url)
        fp = os.path.abspath(os.path.join(base, name))
        _renamed = ""
        if not fp.startswith(base):                     # 防目录穿越
            fp = os.path.join(base, _guess_filename(url))
            _renamed = "（你给的文件名越界了，已安全改名）"
        limit = MAX_DOWNLOAD_MB * 1024 * 1024
        got = 0
        ctype = ""
        warn = ""
        _hdrs = {"User-Agent": _DL_UA, "Accept": "*/*",
                 "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8", "Referer": url}
        # 只有这些状态码才是"被 WAF/UA 拦"，值得换浏览器指纹再试；404/410 是真没有，别去下错误页
        _WAF_CODES = (401, 403, 406, 429)
        try:
            with _rq.get(url, headers=_hdrs, stream=True, timeout=self._cfg.timeout) as r:
                if r.status_code >= 400:
                    if r.status_code not in _WAF_CODES:
                        return fmt_result(r.status_code, url, "",
                                          "下载失败：目标返回 HTTP %d（%s）—— 该文件不存在或无权限，未保存任何文件"
                                          % (r.status_code,
                                             {404: "未找到", 410: "已删除"}.get(r.status_code, "请求被拒绝")))
                    blob, status, ct2 = self._download_via_scrapling(url)
                    if not blob or status >= 400:
                        return fmt_result(r.status_code, url, "",
                                          "下载失败：目标返回 HTTP %d（已尝试浏览器指纹下载仍失败）" % r.status_code)
                    if not os.path.splitext(fp)[1]:
                        _e = {"application/pdf": ".pdf", "application/epub+zip": ".epub",
                              "application/zip": ".zip", "text/plain": ".txt",
                              "text/html": ".html"}.get(ct2, "")
                        if _e:
                            fp += _e
                    with open(fp, "wb") as f:
                        f.write(blob)
                    return fmt_result(status, url, "💾 已下载（浏览器指纹通道）：%s\n大小：%.2f MB\n类型：%s"
                                      % (fp, len(blob) / 1048576.0, ct2 or "未知"), "")
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
                if not os.path.splitext(fp)[1]:
                    _ext = {"application/pdf": ".pdf", "application/epub+zip": ".epub",
                            "application/zip": ".zip", "text/plain": ".txt",
                            "text/html": ".html", "application/x-mobipocket-ebook": ".mobi"}.get(ctype, "")
                    if _ext:
                        fp += _ext
                with open(fp, "wb") as f:
                    for chunk in r.iter_content(65536):
                        got += len(chunk)
                        if got > limit:
                            break
                        f.write(chunk)
                    if got > limit:
                        f.close()
                        try:
                            os.remove(fp)
                        except OSError as e:
                            LOG.debug("忽略异常(%s:2066): %s", __file__, 2066, e)
                        return fmt_result(0, url, "", "文件超过 %d MB 上限，已中止（可先确认文件大小）" % MAX_DOWNLOAD_MB)
        except OSError as e:
            return fmt_result(0, url, "", "写入失败（磁盘空间/权限）：%s" % sanitize(e)[:100])
        except Exception as e:
            return fmt_result(0, url, "", "下载失败：%s" % sanitize(e)[:120])
        # 下到了 0 字节 / 或"要文件却拿到网页" → 明确告知，别让用户以为成功了
        if got == 0:
            try:
                os.remove(fp)
            except OSError as e:
                LOG.debug("忽略异常(%s:2077): %s", __file__, 2077, e)
            return fmt_result(0, url, "", "下载失败：目标返回空内容（0 字节），未保存文件")
        _want_bin = os.path.splitext(name)[1].lower() in (".pdf", ".epub", ".zip", ".mobi", ".exe", ".apk", ".mp4", ".mp3")
        if ctype == "text/html" and _want_bin:
            warn += "（注意：拿到的是 HTML 网页而不是你指定的 %s，可能命中了错误页/跳转页）" % os.path.splitext(name)[1]
        if _renamed:
            warn += _renamed
        if os.path.basename(fp) != name and name and not _renamed:
            warn += "（文件名按内容类型补了扩展名：%s）" % os.path.basename(fp)
        return fmt_result(200, url, "💾 已下载：%s\n大小：%.2f MB\n类型：%s%s" % (
            fp, got / 1048576.0, ctype or "未知", warn), "")

    # ---- 批量（统一走 BatchManager：去重/限速/退避/代理轮换/失败隔离） ----
    @staticmethod
    def _norm_urls(v: Any) -> Tuple[List[str], str]:
        """规范化 urls 参数。返回 (列表, 中文错误)。

        压力测试暴露的真实问题：直接传字符串会被 Python 当可迭代对象**逐字符拆开**
        （"https://a.com" → 14 个单字符"网址"），并静默返回 14 条无意义错误。
        这里一律先规范化，类型不对就明确报错。
        """
        if v is None or v == "" or v == [] or v == ():
            return [], "urls 为空：至少要给 1 个网址"
        if isinstance(v, str):
            return [v.strip()], ""                  # 单个字符串 → 当 1 个网址（友好放行）
        if isinstance(v, (list, tuple)):
            items = [str(u).strip() for u in v if isinstance(u, str) and str(u).strip()]
            if not items:
                return [], "urls 中没有有效网址（元素必须是字符串）"
            return items, ""
        return [], "urls 必须是网址列表（数组），或单个网址字符串；收到的是 %s" % type(v).__name__

    def _bulk(self, tool: str, urls: Any, extra: Dict[str, Any], stealth: bool = False,
              ignore_robots: bool = False) -> str:
        # 批量内部逐 URL 调用"单个"抓取工具：这样才能做去重/限速/退避/代理轮换/失败隔离；
        # 直接调 Scrapling 的 bulk_* 会一次性并发出去，上述控制全部失效。
        _single_of = {"bulk_get": "make_request", "bulk_fetch": "fetch", "bulk_stealthy_fetch": "stealthy_fetch"}
        base_tool = _single_of.get(tool, tool)
        clean, why = self._norm_urls(urls)
        if why:
            return fmt_result(0, "", "", why)
        # 改进 4：批量配置非法（如 concurrency=0）→ 明确中文报错，不静默降级
        if _BATCH.bcfg.error:
            return fmt_result(0, "", "", "批量配置不合法：%s" % _BATCH.bcfg.error)
        allowed, blocked = _BATCH.prepare(clean, ignore_robots)
        items: List[Dict[str, Any]] = list(blocked)
        if allowed:
            items.extend(_BATCH.run_batch(lambda u: self._bulk_one(base_tool, u, extra, stealth), allowed))
        used = len(allowed)
        ok_n = sum(1 for i in items if not i.get("error"))
        head = "批量%s完成：成功 %d / 共 %d（去重+安全过滤后实际请求 %d，%s）" % (
            "隐身抓取" if stealth else "抓取", ok_n, len(items), used, _BATCH.bcfg.describe())
        # 关键：全部失败时顶层必须是错误（否则调用方/熔断器会当成"成功"，一直死磕坏源）
        if ok_n == 0 and items:
            first = next((i.get("error") for i in items if i.get("error")), "未知原因")
            return json.dumps({"status": 0, "url": "", "content": head,
                               "error": "批量抓取全部失败（%d 个）：%s" % (len(items), str(first)[:120]),
                               "items": items}, ensure_ascii=False)
        return json.dumps({"status": 200, "url": "", "content": head,
                           "error": "", "items": items}, ensure_ascii=False)

    def _bulk_one(self, tool: str, url: str, extra: Dict[str, Any], stealth: bool) -> Dict[str, Any]:
        """单个 URL（批量内）：429 指数退避（基数/次数来自 BatchConfig，改进 4）。"""
        delay = max(0.0, _BATCH.bcfg.backoff_base)
        max_retries = max(0, int(_BATCH.bcfg.max_retries))
        last: Dict[str, Any] = {"status": 0, "url": url, "content": "", "error": "未执行"}
        for attempt in range(max_retries + 1):
            try:
                _GUARD.wait_rate_limit(url)
                if stealth:
                    _stealth_gap()
                args = dict(extra)
                args["url"] = url
                args.setdefault("extraction_type", _EXTRACT_TYPE)
                args.setdefault("timeout", self._cfg.timeout)
                if self._cfg.executable_path:
                    args.setdefault("executable_path", self._cfg.executable_path)
                proxy = _BATCH.pick_proxy()
                if proxy:
                    args.setdefault("proxy", proxy)
                res = _CLIENT.call_tool(tool, args, timeout=self._cfg.timeout)
                last = MCPClient._normalize_one(res)
                if last.get("status") == 429 and attempt < max_retries:
                    logger.info("429 退避 %.0fs：%s", delay, url)
                    time.sleep(delay)
                    delay *= 2
                    continue
                return last
            except TimeoutError as e:
                last = {"status": 0, "url": url, "content": "", "error": str(e)}
                break
            except Exception as e:
                last = {"status": 0, "url": url, "content": "", "error": "抓取失败：%s" % sanitize(e)[:120]}
                break
        return last

    def _do_bulk_get(self, p: Dict[str, Any]) -> str:
        return self._bulk("bulk_get", p.get("urls"),
                          self._with_timeout(p, {"impersonate": _random_impersonate()}),
                          stealth=bool(p.get("stealth")),
                          ignore_robots=bool(p.get("ignore_robots")))

    def _do_bulk_fetch(self, p: Dict[str, Any]) -> str:
        return self._bulk("fetch", p.get("urls"),
                          self._with_timeout(p, {"headless": self._cfg.headless}),
                          ignore_robots=bool(p.get("ignore_robots")))

    def _do_bulk_stealthy_fetch(self, p: Dict[str, Any]) -> str:
        extra = self._with_timeout(p, {"headless": self._cfg.headless,
                                       "solve_cloudflare": bool(self._cfg.solve_cloudflare),
                                       "hide_canvas": True, "block_webrtc": True})
        urls = p.get("urls")
        if isinstance(urls, (list, tuple)):
            urls = list(urls)[:20]             # 隐身开销大，限制 20 个
        return self._bulk("stealthy_fetch", urls, extra, stealth=True,
                          ignore_robots=bool(p.get("ignore_robots")))

    # ---- browser_session：会话管理 + 登录态抓取 + 截图（一个入口覆盖 7 个 MCP 会话工具）----
    def _session_call(self, tool: str, args: Dict[str, Any]) -> str:
        """调会话类工具并统一结果格式（英文错误统一转中文 + 超时也受用户 timeout 约束）。"""
        try:
            _ut = float(args.get("timeout") or 0)
        except Exception:
            _ut = 0
        if _ut > 0 and tool in _TIMEOUT_MS_TOOLS:
            _ut = _ut / 1000.0          # 浏览器类内部单位是毫秒
        res = _CLIENT.call_tool(tool, args, timeout=_bound_client_timeout(tool, _ut, self._cfg.timeout))
        if res.get("error"):
            return fmt_result(res.get("status", 0), args.get("url", ""),
                              res.get("content", ""), _humanize_error(res["error"]))
        return fmt_result(res.get("status", 0), args.get("url", ""), res.get("content", ""), "")

    # ---- Scrapling 原生 13 工具里的「会话/截图」7 个：1:1 暴露 ----
    def _do_native_session(self, tool: str, p: Dict[str, Any]) -> str:
        """原生会话类工具直通（open_session / open_request_session / close_session /
        list_sessions / session_fetch / session_make_request / screenshot）。

        带 URL 的三个同样过安全闸门（SSRF / robots / 限速），错误一律中文可读。
        """
        sid = (p.get("session_id") or "").strip()
        if tool == "open_session":
            stype = (p.get("session_type") or "dynamic").strip().lower()
            # 前置白名单：非法值绝不能落到 Scrapling（压力测试发现它会把这个会话注册进去，
            # 之后 list_sessions 每次都抛校验错误 → 列表功能被永久毒化）
            if stype not in ("dynamic", "stealthy", "static"):
                return fmt_result(0, "", "",
                                  "session_type 不合法：%s（只能是 dynamic / stealthy / static；"
                                  "dynamic=浏览器会话，stealthy=隐身浏览器，static=纯 HTTP 会话）" % stype)
            args: Dict[str, Any] = {"session_type": stype, "headless": self._cfg.headless}
            if sid:
                args["session_id"] = sid
            if self._cfg.executable_path:
                args["executable_path"] = self._cfg.executable_path
            out = self._session_call("open_session", args)
            _SESSIONS.register(_sid_of(out), stype)          # 改进 1：登记，纳入回收
            return out

        if tool == "open_request_session":
            args = {}
            if sid:
                args["session_id"] = sid
            out = self._session_call("open_request_session", args)
            _SESSIONS.register(_sid_of(out), "static")
            return out

        if tool == "close_session":
            if not sid:
                return fmt_result(0, "", "", "close_session 需要提供 session_id")
            out = self._session_call("close_session", {"session_id": sid})
            if not _is_err(out):
                _SESSIONS.forget(sid)                        # 用户主动关掉 → 从回收表移除
            return out

        if tool == "list_sessions":
            out = self._session_call("list_sessions", {})
            # 会话表里有"脏数据"（例如非法 session_type 残留）时 Scrapling 会整体校验失败；
            # 这时给出可读中文 + 可操作建议，而不是把 Pydantic 原文丢给模型（也避免被误熔断）。
            if _is_err(out) and "会话" in (json.loads(out).get("error") or ""):
                return fmt_result(0, "", "",
                                  "会话列表暂时读不出来（会话表里有异常数据，通常是曾经的非法参数残留）—— "
                                  "重启小焦即可清空会话表；或直接新开一个会话继续用（open_session / open_request_session）")
            return out

        # 下面三个都要 URL：安全闸门 + 限速
        url = (p.get("url") or "").strip()
        if not url:
            return fmt_result(0, "", "", "%s 需要提供 url" % tool)
        reason = _GUARD.check_ssrf(url)
        if reason:
            return fmt_result(0, url, "", reason)
        ok, why = _GUARD.robots_allowed(url, USER_AGENT, bool(p.get("ignore_robots")))
        if not ok:
            return fmt_result(0, url, "", why)
        _GUARD.wait_rate_limit(url)
        if not sid:
            return fmt_result(0, url, "",
                              "%s 需要提供 session_id（先调用 open_session / open_request_session）" % tool)

        if tool == "session_fetch":
            _SESSIONS.touch(sid)                       # 改进 1：用过就算"活着"，不会因空闲被回收
            # solve_cloudflare 只有 stealthy 会话支持，按需才传
            args = {"url": url, "session_id": sid, "extraction_type": _EXTRACT_TYPE}
            if p.get("wait_selector"):
                args["wait_selector"] = p["wait_selector"]
            if p.get("solve_cloudflare"):
                args["solve_cloudflare"] = True
            if p.get("blocked_domains"):
                args["blocked_domains"] = p["blocked_domains"]
            return self._session_call("session_fetch", args)

        if tool == "session_make_request":
            _SESSIONS.touch(sid)
            out = self._session_call("session_make_request",
                                     {"url": url, "session_id": sid, "method": "GET",
                                      "extraction_type": _EXTRACT_TYPE})
            return _session_mismatch_hint(out, sid, "session_make_request")

        if tool == "screenshot":
            _SESSIONS.touch(sid)
            out = self._session_call("screenshot",
                                     {"url": url, "session_id": sid,
                                      "image_type": p.get("image_type") or "png",
                                      "full_page": bool(p.get("full_page"))})
            return _session_mismatch_hint(out, sid, "screenshot")

        return fmt_result(0, url, "", "未知原生会话工具：%s" % tool)

    def _do_browser_session(self, p: Dict[str, Any]) -> str:
        """聚合入口（兼容旧用法）：一个工具按 action 走完会话全流程。

        action:
          open       开浏览器会话(dynamic/stealthy)：之后 session_fetch/screenshot 复用它
          open_http  开 HTTP 会话(static)：保持 cookie/连接，之后 session_make_request 复用
          close      关闭会话，释放资源
          list       列出当前所有会话
          fetch      用会话抓页面（**保持登录态 / 已过 Cloudflare 验证的浏览器**）
          request    用 HTTP 会话发请求（保持 cookie）
          screenshot 给页面截图（整页可选），图片存到 media/screenshot/ 并返回路径
        """
        action = (p.get("action") or "").strip().lower()
        _MAP = {"open": "open_session", "open_http": "open_request_session", "close": "close_session",
                "list": "list_sessions", "fetch": "session_fetch", "request": "session_make_request",
                "screenshot": "screenshot"}
        native = _MAP.get(action)
        if not native:
            return fmt_result(0, p.get("url", ""), "",
                              "未知 action：%s（可用 open/open_http/close/list/fetch/request/screenshot）" % action)
        return self._do_native_session(native, p)

    # ---- scrape_with_selector（自适应选择器，强制 adaptive） ----
    def _do_scrape_selector(self, p: Dict[str, Any]) -> str:
        url = (p.get("url") or "").strip()
        selector = (p.get("selector") or "").strip()
        name = (p.get("name") or "").strip() or _selector_key(url, selector)
        if not url or not selector:
            return fmt_result(0, url, "", "需要同时提供 url 和 selector")
        reason = _GUARD.check_ssrf(url)
        if reason:
            return fmt_result(0, url, "", reason)
        ok, why = _GUARD.robots_allowed(url, USER_AGENT, bool(p.get("ignore_robots")))
        if not ok:
            return fmt_result(0, url, "", why)
        _GUARD.wait_rate_limit(url)
        _t = self._with_timeout(p, {}).get("timeout", self._cfg.timeout)
        args: Dict[str, Any] = {"url": url, "css_selector": selector,
                                "extraction_type": _EXTRACT_TYPE, "main_content_only": False,
                                "method": "GET", "timeout": _t}
        if self._cfg.executable_path:
            args["executable_path"] = self._cfg.executable_path
        res = _CLIENT.call_tool("make_request", args, timeout=_t)
        if res.get("error"):
            # 自适应重试：改走浏览器渲染 + 等元素
            args2 = {"url": url, "css_selector": selector, "extraction_type": _EXTRACT_TYPE,
                     "wait_selector": selector, "headless": self._cfg.headless,
                     "timeout": _t}
            if self._cfg.executable_path:
                args2["executable_path"] = self._cfg.executable_path
            res = _CLIENT.call_tool("fetch", args2, timeout=_t)
        if res.get("error"):
            return fmt_result(res.get("status", 0), url, "", res["error"])
        # 选择器没匹配到 → 结构化 not_found，绝不"空内容 + 假装成功"
        # （压力测试实测：selector 不存在时之前返回 status=200 + 空正文，调用方以为抓到了东西）
        _body = res.get("content", "") or ""
        if not _body.strip():
            return json.dumps({"status": 0, "url": url, "content": "",
                               "error": ("选择器 %s 没有匹配到内容（页面里没有这个元素，或元素是空的）—— "
                                         "可换个选择器，或改用 fetch/get 抓整页" % selector),
                               "not_found": True,
                               "selector": selector,
                               "selector_name": name}, ensure_ascii=False)
        # 记录/更新选择器指纹（自适应：下次即使改版也能按相似度找回）
        rec = SelectorRecord(selector=selector, tag=_tag_of(selector), text=_body[:200],
                             attrs={}, saved_at=time.time())
        _SELECTORS.save(name, rec)
        return fmt_result(res.get("status", 0), url, _body, "")


# ---------- 小工具 ----------
def _humanize_error(msg: str) -> str:
    """把 Scrapling / Playwright / Pydantic 的英文原文错误转成 4B 模型看得懂的中文。

    压力测试暴露：非法 session_type、会话不存在、net::ERR_* 等英文原文会直接丢给模型和用户，
    模型看不懂、用户也不知道下一步做什么。这里做一次统一翻译（保留关键原文片段便于排查）。
    """
    if not msg:
        return msg
    m = str(msg)
    low = m.lower()
    if "ssrf protection" in low or "redirect to internal" in low:
        return "目标重定向到内网/本机地址，已被安全策略拦截（SSRF 防护）"
    if "curl: (28)" in m or "operation timed out" in low:
        return "请求超时：目标响应太慢（可加大 timeout，或改用更轻的 get）"
    if "curl: (6)" in m or "could not resolve host" in low:
        return "域名解析失败：这个域名不存在或本机 DNS 解析不了（检查网址是否拼错）"
    if "curl: (7)" in m and ("connect" in low or "refused" in low):
        return "连接失败：目标拒绝连接（服务没起/端口不对/被防火墙拦）"
    if "curl: (35)" in m or "ssl" in low and "error" in low:
        return "TLS/SSL 握手失败：目标证书或协议不兼容（可换 http 或换站点）"
    if "session" in low and "not found" in low:
        sid = ""
        try:
            sid = m.split("'")[1]
        except Exception as e:
            LOG.debug("忽略异常(%s:2400): %s", __file__, 2400, e)
        return ("会话 %s 不存在（可能已被关闭或服务重启过）—— 先调用 list_sessions 看有哪些会话，"
                "或重新 open_session / open_request_session 开一个" % (sid or "该"))
    if "input should be 'dynamic'" in low or "type=literal_error" in low or "validation error" in low:
        if "sessioninfo" in low or "session_type" in low:
            return ("会话类型不合法或已有会话数据异常：请用 session_type = dynamic / stealthy / static；"
                    "若只是列出会话报这个错，说明之前有非法会话残留 —— 重启小焦即可清掉（插件已加前置校验，不会再产生）")
        return "参数校验失败：%s" % m[:160]
    if "err_name_not_resolved" in low or "could not resolve host" in low:
        return "域名解析失败：这个域名不存在或本机 DNS 解析不了（检查网址是否拼错）"
    if "err_connection_refused" in low:
        return "连接被拒绝：目标端口没有服务在监听"
    if "err_connection_timed_out" in low or "timeout" in low:
        return "请求超时：目标响应太慢（可加大 timeout，或改用更轻的 get）"
    if "err_internet_disconnected" in low or "network is unreachable" in low:
        return "网络不可用：本机断网或被防火墙拦了"
    return m


def _bound_client_timeout(tool: str, user_timeout: float, default_timeout: float) -> float:
    """给"客户端等待"设硬上限：用户说 6 秒，就不能因为内核重试而拖到 22 秒。

    浏览器类工具的 timeout 单位是毫秒（调用方传入的是秒），这里统一按秒算。
    """
    if not user_timeout or user_timeout <= 0:
        return default_timeout
    return max(3.0, min(default_timeout, user_timeout + 5.0))


def _sid_of(out: str) -> str:
    """从 open_session / open_request_session 的返回里取出 session_id（取不到返回空串）。"""
    try:
        d = json.loads(out)
        body = d.get("content") or ""
        if isinstance(body, str):
            m = re.search(r'"session_id"\s*:\s*"([^"]+)"', body)
            if m:
                return m.group(1)
        if isinstance(body, dict):
            return str(body.get("session_id") or "")
    except Exception as e:
        LOG.debug("忽略异常(%s:2441): %s", __file__, 2441, e)
    return ""


def _session_mismatch_hint(out: str, sid: str, tool: str) -> str:
    """会话类型用错时给中文提示（Scrapling 原文是英文，4B 模型看不懂）。"""
    try:
        err = json.loads(out).get("error") or ""
    except Exception:
        return out
    if "dynamic" in err and "session" in err.lower():
        tip = ("会话 %s 是 dynamic（浏览器）会话：抓页面请用 session_fetch；"
               "要发 HTTP 请求/截图请先 open_request_session 或 open_session(session_type=stealthy)"
               % sid) if tool == "session_make_request" else \
              ("会话 %s 是 dynamic 会话，截图请用 open_session(session_type=stealthy) 或 open_request_session 再试" % sid)
        return fmt_result(0, "", "", "%s（%s）" % (err, tip))
    return out


def _is_err(out: str) -> bool:
    try:
        return bool(json.loads(out).get("error"))
    except Exception:
        return False


# 安全拦截类错误：属于"按策略正常拒绝"，不计入熔断（否则连续拦截会被误判成工具坏了）
_SAFETY_HINTS = ("SSRF 防护", "robots.txt", "禁止访问", "需要同时提供", "URL 缺少",
                 "只支持 http", "URL 为空", "URL 格式")


def _is_safety_block(out: str) -> bool:
    """判断返回是否是安全拦截（不计熔断失败）。"""
    try:
        err = json.loads(out).get("error") or ""
    except Exception:
        return False
    return any(h in err for h in _SAFETY_HINTS)


def _tag_of(selector: str) -> str:
    m = re.match(r"^\s*([a-zA-Z][\w-]*)", selector or "")
    return m.group(1).lower() if m else ""


def _selector_key(url: str, selector: str) -> str:
    """选择器存档键：域名 + 选择器，便于同一站点复用。"""
    try:
        host = urllib.parse.urlparse(url).netloc or "site"
    except Exception:
        host = "site"
    return "%s::%s" % (host, selector)


def get_plugin():
    """小焦插件加载约定：返回插件实例。"""
    return ScraplingBridge()
