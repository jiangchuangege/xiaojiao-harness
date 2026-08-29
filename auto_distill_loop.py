"""
小焦 · 持续蒸馏训练（无间隔版）
每轮蒸馏结束后，等待5秒立刻开始下一轮
"""

import os
import time
import subprocess
import sys

# ========== 配置 ==========
DISTILL_SCRIPT = "distill_and_train.py"
LOG_FILE = "distill_loop.log"

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run_distill():
    """执行一次蒸馏训练"""
    log("🧠 开始蒸馏训练...")
    try:
        result = subprocess.run(
            [sys.executable, DISTILL_SCRIPT],
            capture_output=True,
            text=True,
            timeout=600
        )
        if result.returncode == 0:
            log("✅ 蒸馏完成")
            if result.stdout:
                lines = result.stdout.strip().split("\n")
                for line in lines[-3:]:
                    log(f"   {line}")
        else:
            log(f"❌ 蒸馏失败 (返回码: {result.returncode})")
            if result.stderr:
                log(f"   错误: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log("⏰ 蒸馏超时（超过10分钟）")
    except Exception as e:
        log(f"❌ 蒸馏异常: {e}")

def main():
    print("""
╔═══════════════════════════════════════════╗
║   小焦 · 无间断持续蒸馏                  ║
║   蒸馏完成后立即开始下一轮                ║
║   按 Ctrl+C 停止运行                     ║
╚═══════════════════════════════════════════╝
    """)
    
    log("启动无间断蒸馏模式")
    
    while True:
        try:
            run_distill()
            # 只等5秒就继续下一轮
            log("等待5秒后开始下一轮...")
            time.sleep(5)
                
        except KeyboardInterrupt:
            log("\n用户中断，退出")
            break
        except Exception as e:
            log(f"主循环异常: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()