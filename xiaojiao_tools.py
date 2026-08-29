# -*- coding: utf-8 -*-
"""
小焦 · 工具调用服务（独立端口，默认 5003）
- 网页面板：列出所有工具，点开填参数即可执行
- HTTP API：POST /api/run  直接调用某个工具，方便脚本/Harness接入
复用 xiaojiao_app 的工具（含 PowerShell 修复 / 危险命令识别）。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, request, jsonify, render_template_string
import xiaojiao_app as app_mod

RUN_TOOL = app_mod.run_tool          # 工具执行（force=True 由用户前台调用，视为已授权）
app = Flask(__name__)

TOOLS_INFO = [
    {"name": "run_command", "desc": "运行 PowerShell 命令 / 脚本", "params": ["command", "timeout"]},
    {"name": "write_file", "desc": "把文本写入文件（自动建父目录）", "params": ["path", "content"]},
    {"name": "read_file", "desc": "读取文本文件前若干字符", "params": ["path", "max_chars"]},
    {"name": "list_files", "desc": "列出目录内容", "params": ["path"]},
    {"name": "open_app", "desc": "打开应用 / 文件 / 网址", "params": ["path"]},
]


@app.route("/")
def index():
    return render_template_string(HTML, tools=TOOLS_INFO)


@app.route("/api/tools")
def api_tools():
    return jsonify(TOOLS_INFO)


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or data.get("tool") or "").strip()
    force = bool(data.get("force", True))     # 前台/脚本调用视为授权，跳过危险确认等待
    # 兼容直接传 command / params
    if name.lower() in ("pwsh", "powershell", "cmd", "terminal", "shell", "bash", "sh", "exec", "run", "execute"):
        name = "run_command"
    args = {k: v for k, v in data.items() if k not in ("name", "tool", "force", "params")}
    if isinstance(data.get("params"), dict):
        args.update(data["params"])
    try:
        result = RUN_TOOL(name, args, force=force)
    except Exception as e:
        result = "工具异常：%s: %s" % (type(e).__name__, e)
    return jsonify({"ok": True, "tool": name, "result": result})


HTML = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小焦 · 工具调用</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e6e8ee;padding:26px}
header{max-width:820px;margin:0 auto 20px}
header h1{font-size:24px;background:linear-gradient(135deg,#f093fb,#f5576c);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
header .sub{color:#7a8290;font-size:13px;margin-top:6px}
.card{background:#161a24;border:1px solid #262b3a;border-radius:14px;padding:18px 22px;margin-bottom:16px;max-width:820px;margin-left:auto;margin-right:auto}
.card h2{font-size:16px;margin-bottom:12px}
.tool{border:1px solid #2a3140;background:#1a2030;border-radius:10px;padding:12px 16px;margin-bottom:10px}
.tool .tn{font-size:15px;font-weight:700}
.tool .td{font-size:12px;color:#8b93a3;margin:2px 0 10px}
.tool input,select{background:#0f1117;border:1px solid #2a3140;color:#e6e8ee;border-radius:8px;padding:8px 10px;font-size:13px;width:100%;margin-bottom:8px;font-family:inherit}
.tool textarea{background:#0f1117;border:1px solid #2a3140;color:#e6e8ee;border-radius:8px;padding:8px 10px;font-size:13px;width:100%;min-height:120px;font-family:Consolas,monospace}
button{background:#4f46e5;color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:13px;cursor:pointer}
button:hover{background:#6366f1}
#out{background:#0b0d12;border:1px solid #2a3140;border-radius:10px;padding:12px;white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px;min-height:40px;margin-top:12px;color:#a5d6ff}
label{font-size:12px;color:#8b93a3;display:block;margin-bottom:4px}
</style></head><body>
<header><h1>小焦 · 工具调用</h1><div class="sub">直接调用小焦的工具（命令/文件/目录/打开），用于手动操控或给脚本/Harness 接入。</div></header>
<div class="card"><h2>⚡ 快速运行命令</h2>
  <label>命令（PowerShell）</label>
  <input id="qcmd" placeholder="例如 New-Item -ItemType Directory -Path C:\test -Force"/>
  <button onclick="runCmd()">▶ 运行</button><div id="qout" class="out"></div>
</div>
<div class="card"><h2>各工具</h2>{% for t in tools %}
  <div class="tool"><div class="tn">{{ t.name }}</div><div class="td">{{ t.desc }}</div>
    {% for p in t.params %}<label>{{ p }}</label><input class="in" data-t="{{ t.name }}" data-p="{{ p }}"/>{% endfor %}
    <button onclick="runTool('{{ t.name }}')">▶ {{ t.name }}</button><div class="out" id="out-{{ t.name }}"></div>
  </div>
{% endfor %}</div>
<script>
function out(id,s){const e=document.getElementById(id);e.style.cssText='background:#0b0d12;border:1px solid #2a3140;border-radius:8px;padding:10px;font-family:Consolas,monospace;font-size:13px;margin-top:10px;white-space:pre-wrap;color:#a5d6ff';e.textContent=s;}
async function runTool(name){const inputs=document.querySelectorAll('.in[data-t="'+name+'"]');const args={};inputs.forEach(x=>{if(x.value)args[x.dataset.p]=x.value;});args.name=name;out('out-'+name,'⏳ 运行中...');
 const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});const d=await r.json();out('out-'+name, d.result||'(空)');}
async function runCmd(){const c=document.getElementById('qcmd').value;if(!c)return;out('qout','⏳ 运行中...');
 const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'run_command',command:c})});const d=await r.json();out('qout', d.result||'(空)');}
</script></body></html>"""


def main():
    port = int(os.environ.get("TOOLS_PORT", 5003))
    print("=" * 40)
    print("  小焦 · 工具调用服务")
    print(f"  http://127.0.0.1:{port}")
    print("  工具接口: POST /api/run  {name, command/path/content,...}")
    print("=" * 40)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
