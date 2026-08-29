# dsh_bridge/main.py
"""
DSH 桥接服务：让 DeepSeek Harness 社区插件能在小焦中运行。
通过 DeepSeek Harness Python SDK 驱动，把 DSH 生态接到小焦 /v1 上。
"""
import os
import sys
import time
from pathlib import Path
from flask import Flask, request, jsonify

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
XIAOJIAO_API_URL = os.environ.get("XIAOJIAO_API", "http://127.0.0.1:5000/v1")
DSH_HOME = str(PROJECT_ROOT / "dsh_home")        # SDK 要求显式 dsh_home
SESSION_ROOT = str(PROJECT_ROOT / "dsh_sessions")

# 尝试导入真正的 SDK（deepseek-harness-sdk 提供 DeepSeekHarness）
DSH_AVAILABLE = False
try:
    from deepseek_harness import DeepSeekHarness
    DSH_AVAILABLE = True
except Exception as e:
    print(f"⚠️ 未能导入 DeepSeekHarness，DSH 插件功能将受限。")
    print(f"   请运行: pip install deepseek-harness-sdk")
    print(f"   详情: {e}")

app = Flask(__name__)


def run_dsh_task(user_input: str, session_id: str = "default"):
    os.makedirs(DSH_HOME, exist_ok=True)
    os.makedirs(SESSION_ROOT, exist_ok=True)

    # 没有 SDK → 直接调小焦 /v1（降级，但可用）
    if not DSH_AVAILABLE:
        import requests
        try:
            resp = requests.post(
                f"{XIAOJIAO_API_URL}/chat/completions",
                json={"model": "xiaojiao1.0-4B",
                      "messages": [{"role": "user", "content": user_input}],
                      "stream": False},
                timeout=120,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            return f"错误: 小焦 API 返回 {resp.status_code}"
        except Exception as e:
            return f"错误: {str(e)}"

    try:
        with DeepSeekHarness(
            dsh_home=DSH_HOME,
            cwd=str(PROJECT_ROOT),
            provider="deepseek-official",
            model="xiaojiao1.0-4B",
            base_url=XIAOJIAO_API_URL,     # 让小焦当 DSH 的大脑
            api_key="not-needed",
            max_tokens=8192,
        ) as harness:
            result = harness.run(user_input, session_id=session_id)
            return result.final_response or ""
    except Exception as e:
        return f"DSH 执行错误: {str(e)}"


@app.route('/run', methods=['POST'])
def handle_run():
    data = request.get_json(silent=True) or {}
    user_input = data.get('user_input', '')
    session_id = data.get('session_id', 'default')
    if not user_input:
        return jsonify({"error": "user_input is required"}), 400
    try:
        reply = run_dsh_task(user_input, session_id)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "dsh_available": DSH_AVAILABLE})


if __name__ == "__main__":
    print("🌉 DSH 桥接服务启动 (端口 5001)")
    print(f"   小焦 API: {XIAOJIAO_API_URL}")
    print(f"   DSH 可用: {DSH_AVAILABLE}")
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
