"""
小焦模型合并还原脚本
====================
把从 GitHub Release 下载的 4 个分片（.part0 ~ .part3）合并还原成完整的
`xiaojiao1.0-4B.gguf` 模型文件。

用法：
    python merge_model.py                       # 合并当前目录下的 .part* 分片
    python merge_model.py --out xiaojiao.gguf   # 指定输出文件名

会把分片按 .part0,.part1,... 顺序拼接成一个完整文件。
合并前请确认分片完整（SHA256 应与 Release 给出的值一致）。
"""
import os
import sys
import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="xiaojiao1.0-4B.gguf", help="输出文件路径")
    ap.add_argument("parts", nargs="*", default=None,
                    help="分片路径；不填则自动找当前目录下的 xiaojiao1.0-4B.gguf.part*")
    args = ap.parse_args()

    parts = args.parts
    if not parts:
        cwd = os.getcwd()
        parts = sorted(
            (os.path.join(cwd, n) for n in os.listdir(cwd) if n.startswith("xiaojiao1.0-4B.gguf.part")),
            key=lambda p: int(p.rsplit("part", 1)[-1]),
        )
    if not parts:
        print("❌ 没找到分片。请先下载 .part0~.part3 并放到当前目录。")
        sys.exit(1)

    print(f"待合并 {len(parts)} 个分片：")
    for p in parts:
        print("   ", p, f"({os.path.getsize(p) / 1e6:.1f} MB)")
    print("→ 输出:", args.out)
    print("正在合并...（进度条）")

    total = sum(os.path.getsize(p) for p in parts)
    done = 0
    with open(args.out, "wb") as out:
        for i, p in enumerate(parts):
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r  {pct}%", end="", flush=True)
    print(f"\n✅ 合并完成：{args.out} ({os.path.getsize(args.out) / 1e9:.2f} GB)")

    # 校验（可选）：给出文件大小供核对
    if os.path.getsize(args.out) == total:
        print("  大小校验通过。")
    else:
        print("  ⚠️ 大小不一致，请确认分片完整。")


if __name__ == "__main__":
    main()
