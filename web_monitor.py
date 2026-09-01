"""
小焦 · 蒸馏监控面板（适配 massive_distill.py）
修复：添加缺失的 import re
"""

import os
import json
import time
import threading
import datetime
import re  # 🆕 添加缺失的导入
import subprocess
import sys
from flask import Flask, render_template_string, jsonify, request

app = Flask(__name__)

# ========== 配置 ==========
KNOWLEDGE_FILE = "xiaojiao_knowledge.txt"
MEMORY_FILE = "xiaojiao_memory.txt"
TRAIN_POOL_FILE = "training_data_pool.txt"
MODEL_FILE = "mini_gpt_model.pth"
LOG_FILE = "massive_distill.log"

status = {
    "last_update": time.time(),
    "knowledge_count": 0,
    "memory_count": 0,
    "train_pool_size": 0,
    "model_exists": False,
    "model_size_mb": 0,
    "llm_online": False,
    "last_distill": "从未",
    "recent_logs": [],
    "is_training": False,
}

def update_status():
    if os.path.exists(KNOWLEDGE_FILE):
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            status["knowledge_count"] = len(f.read().split("-" * 50))
    else:
        status["knowledge_count"] = 0

    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            status["memory_count"] = len([l for l in f.readlines() if l.strip()])
    else:
        status["memory_count"] = 0

    if os.path.exists(TRAIN_POOL_FILE):
        with open(TRAIN_POOL_FILE, "r", encoding="utf-8") as f:
            status["train_pool_size"] = len([l for l in f.readlines() if l.strip()])
    else:
        status["train_pool_size"] = 0

    if os.path.exists(MODEL_FILE):
        status["model_exists"] = True
        status["model_size_mb"] = round(os.path.getsize(MODEL_FILE) / (1024 * 1024), 2)
    else:
        status["model_exists"] = False
        status["model_size_mb"] = 0

    try:
        import requests
        resp = requests.get("http://127.0.0.1:%s/health" % os.environ.get("LLAMA_PORT", "8080"), timeout=2)
        status["llm_online"] = resp.status_code == 200
    except:
        status["llm_online"] = False

    # 从 massive_distill.log 读取日志和最后蒸馏时间
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            status["recent_logs"] = lines[-20:]
            for line in reversed(lines):
                if "当前主题" in line or "训练完成" in line or "生成" in line:
                    match = re.search(r'\[(.*?)\]', line)
                    if match:
                        status["last_distill"] = match.group(1)
                        break
    else:
        status["recent_logs"] = ["暂无日志"]

    status["last_update"] = time.time()


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小焦 · 蒸馏监控</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e4e6eb; padding: 24px; min-height: 100vh; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; display: inline-block; }
        .subtitle { color: #888; margin-bottom: 24px; font-size: 14px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: #1e2028; border-radius: 16px; padding: 18px 22px; border: 1px solid #2a2d3a; }
        .card .label { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
        .card .value { font-size: 28px; font-weight: 600; color: #fff; }
        .card .value.green { color: #4ade80; }
        .card .value.orange { color: #fb923c; }
        .card .value.blue { color: #60a5fa; }
        .card .value.purple { color: #a78bfa; }
        .card .value.red { color: #f87171; }
        .card .sub { font-size: 12px; color: #666; margin-top: 4px; }
        .section { background: #1e2028; border-radius: 16px; padding: 18px 22px; border: 1px solid #2a2d3a; margin-bottom: 20px; }
        .section h2 { font-size: 16px; font-weight: 600; margin-bottom: 10px; }
        .log-box { background: #0b0d12; border-radius: 10px; padding: 14px; font-family: monospace; font-size: 12px; line-height: 1.6; max-height: 280px; overflow-y: auto; color: #b0b3c0; border: 1px solid #2a2d3a; }
        .btn { background: #2a2d3a; border: none; color: #e4e6eb; padding: 8px 20px; border-radius: 10px; font-size: 13px; cursor: pointer; font-weight: 500; }
        .btn:hover { background: #3d4055; }
        .btn.primary { background: #4f46e5; color: white; }
        .btn.primary:hover { background: #6366f1; }
        .flex { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .badge { font-size: 12px; background: #2a2d3a; padding: 2px 12px; border-radius: 20px; color: #aaa; }
        @media (max-width: 600px) { .grid { grid-template-columns: 1fr 1fr; } body { padding: 14px; } .card .value { font-size: 22px; } }
    </style>
</head>
<body>
    <div class="container">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
            <div><h1>🧠 小焦 · 蒸馏监控</h1><div class="subtitle">持续自进化状态面板</div></div>
            <div class="flex">
                <span class="badge" id="statusBadge">● 运行中</span>
                <button class="btn primary" onclick="triggerDistill()">⚡ 立即蒸馏</button>
                <button class="btn" onclick="refresh()">🔄 刷新</button>
            </div>
        </div>

        <div class="grid" id="statsGrid">
            <div class="card"><div class="label">📚 知识片段</div><div class="value blue">{{ stats.knowledge_count }}</div><div class="sub">xiaojiao_knowledge.txt</div></div>
            <div class="card"><div class="label">🧠 记忆条目</div><div class="value purple">{{ stats.memory_count }}</div><div class="sub">xiaojiao_memory.txt</div></div>
            <div class="card"><div class="label">📊 训练池</div><div class="value green">{{ stats.train_pool_size }}</div><div class="sub">training_data_pool.txt</div></div>
            <div class="card"><div class="label">🤖 小模型</div><div class="value {{ 'green' if stats.model_exists else 'red' }}">{% if stats.model_exists %}✅ {{ stats.model_size_mb }} MB{% else %}❌ 不存在{% endif %}</div><div class="sub">mini_gpt_model.pth</div></div>
            <div class="card"><div class="label">🌐 LLM 服务</div><div class="value {{ 'green' if stats.llm_online else 'red' }}">{{ '✅ 在线' if stats.llm_online else '❌ 离线' }}</div><div class="sub">http://127.0.0.1:8080</div></div>
            <div class="card"><div class="label">⏳ 上次蒸馏</div><div class="value orange">{{ stats.last_distill }}</div><div class="sub">最后更新</div></div>
        </div>

        <div class="section">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                <h2>📋 实时日志</h2>
                <span style="color: #666; font-size: 12px;">最近 {{ logs|length }} 条</span>
            </div>
            <div class="log-box" id="logBox">
                {% for line in logs %}
                    <div>{{ line }}</div>
                {% else %}
                    <div style="color: #555;">暂无日志</div>
                {% endfor %}
            </div>
        </div>

        <div style="text-align: center; color: #444; font-size: 11px; margin-top: 20px;">
            小焦蒸馏系统 · 数据实时更新
        </div>
    </div>

    <script>
        function refresh() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('statsGrid').innerHTML = renderStats(data);
                    document.getElementById('logBox').innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
                });
        }
        function renderStats(s) {
            return `
                <div class="card"><div class="label">📚 知识片段</div><div class="value blue">${s.knowledge_count}</div><div class="sub">xiaojiao_knowledge.txt</div></div>
                <div class="card"><div class="label">🧠 记忆条目</div><div class="value purple">${s.memory_count}</div><div class="sub">xiaojiao_memory.txt</div></div>
                <div class="card"><div class="label">📊 训练池</div><div class="value green">${s.train_pool_size}</div><div class="sub">training_data_pool.txt</div></div>
                <div class="card"><div class="label">🤖 小模型</div><div class="value ${s.model_exists ? 'green' : 'red'}">${s.model_exists ? '✅ ' + s.model_size_mb + ' MB' : '❌ 不存在'}</div><div class="sub">mini_gpt_model.pth</div></div>
                <div class="card"><div class="label">🌐 LLM 服务</div><div class="value ${s.llm_online ? 'green' : 'red'}">${s.llm_online ? '✅ 在线' : '❌ 离线'}</div><div class="sub">http://127.0.0.1:8080</div></div>
                <div class="card"><div class="label">⏳ 上次蒸馏</div><div class="value orange">${s.last_distill}</div><div class="sub">最后更新</div></div>
            `;
        }
        function triggerDistill() {
            if (confirm('确定要立即执行蒸馏吗？')) {
                fetch('/api/distill', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => { alert(data.message); refresh(); });
            }
        }
        setInterval(refresh, 30000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    update_status()
    return render_template_string(HTML_TEMPLATE, stats=status, logs=status["recent_logs"])

@app.route('/api/status')
def api_status():
    update_status()
    return jsonify({
        "knowledge_count": status["knowledge_count"],
        "memory_count": status["memory_count"],
        "train_pool_size": status["train_pool_size"],
        "model_exists": status["model_exists"],
        "model_size_mb": status["model_size_mb"],
        "llm_online": status["llm_online"],
        "last_distill": status["last_distill"],
        "logs": status["recent_logs"],
    })

@app.route('/api/distill', methods=['POST'])
def api_distill():
    def run_distill():
        status["is_training"] = True
        try:
            # 直接运行 massive_distill.py，但不要等待它结束（避免阻塞）
            subprocess.Popen([sys.executable, "massive_distill.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"启动蒸馏失败：{e}")
        finally:
            status["is_training"] = False
            update_status()
    threading.Thread(target=run_distill, daemon=True).start()
    return jsonify({"message": "蒸馏已启动，请查看日志面板"})

if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════╗
║   小焦 · 监控面板                        ║
║   访问 http://127.0.0.1:5000              ║
║   按 Ctrl+C 停止                         ║
╚═══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)