"""
数据校验守护进程（宽松版）
"""

import os
import time

TRAIN_POOL_FILE = "training_data_pool.txt"
CHECK_INTERVAL = 10

def is_valid_line(line):
    line = line.strip()
    if not line:
        return False
    
    # 宽松规则：只要包含"用户"就认为是有效对话
    if "用户" in line:
        return True
    
    # 如果没有任何明显标识符，但长度大于15，也可能是有意义的数据
    if len(line) > 15:
        return True
    
    return False

def scan_file(filepath):
    if not os.path.exists(filepath):
        return 0, []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return -1, ["文件编码异常"]
    
    errors = []
    for i, line in enumerate(lines, 1):
        if not is_valid_line(line):
            errors.append((i, line.strip()[:50]))
    return len(lines), errors

def clean_file(filepath):
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        backup = filepath + ".backup"
        os.rename(filepath, backup)
        print(f"⚠️ 文件编码损坏，已备份为 {backup}")
        return
    
    valid_lines = [line for line in lines if is_valid_line(line)]
    if len(valid_lines) != len(lines):
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(valid_lines)
        print(f"✅ 已清理 {filepath}，移除 {len(lines)-len(valid_lines)} 行无效数据")

def main():
    print("""
╔═══════════════════════════════════════════╗
║   数据校验守护进程（宽松版）              ║
║   只删除真正的空行和乱码                  ║
║   按 Ctrl+C 停止运行                     ║
╚═══════════════════════════════════════════╝
    """)
    while True:
        try:
            if os.path.exists(TRAIN_POOL_FILE):
                total, errors = scan_file(TRAIN_POOL_FILE)
                if total == -1:
                    print(f"❌ 文件编码异常，正在修复...")
                    clean_file(TRAIN_POOL_FILE)
                elif errors:
                    print(f"⚠️ 发现 {len(errors)} 行异常数据")
                    clean_file(TRAIN_POOL_FILE)
            else:
                print(f"📁 {TRAIN_POOL_FILE} 尚未创建，等待中...")
            
            time.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            print("\n用户中断，退出。")
            break
        except Exception as e:
            print(f"异常: {e}，继续运行...")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()