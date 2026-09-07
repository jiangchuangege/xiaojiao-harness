import subprocess, sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
workdir = r"G:\模型文件\音乐模型\ACE-Step-1.5"
py = os.path.join(workdir, "python_embeded", "python.exe")
log = os.path.join(workdir, "_run.log")
# 设置 .env 里的配置(禁止 LLM 初始化等, 快速启动)
env = dict(os.environ)
env.update({
    "ACESTEP_INIT_LLM": "false",          # 不初始化 LLM(省下载/显存)
    "CHECK_UPDATE": "false",              # 跳过更新检查
    "ACESTEP_NO_INIT": "true",            # 启动不加载模型(首次请求再加载)
})
p = subprocess.Popen([py, "-m", "uvicorn", "acestep.api_server:app", "--host", "127.0.0.1", "--port", "8001"],
                     cwd=workdir, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
print("PID", p.pid, "启动中...", flush=True)
# 等 25s
for i in range(5):
    time.sleep(5)
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:8001/health"], capture_output=True, text=True, timeout=5)
        code = r.stdout.strip()
        print("  %ds /health HTTP=%s" % ((i+1)*5, code), flush=True)
        if code == "200":
            print("✅ 服务起来了", flush=True)
            break
    except Exception as e:
        print("  %ds 检查: %s" % ((i+1)*5, e), flush=True)
# 终止
try:
    p.terminate()
except Exception:
    pass
print("\n=== 日志 tail ===", flush=True)
try:
    print(open(log, encoding="utf-8", errors="ignore").read()[-1500:], flush=True)
except Exception as e:
    print("读日志失败:", e, flush=True)
