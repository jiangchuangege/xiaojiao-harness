# -*- coding: utf-8 -*-
"""🐳 小焦 · 本地零算力视频生成器
不是 AI 扩散，而是用 PIL 逐帧合成"会动的视频卡片"：渐变背景 + 标题 + 粒子/关键词动画。
纯 CPU、零依赖，输出 GIF(默认) 或 mp4(有 ffmpeg 时)。8G 显存完全无压力。

用法：
  python video_gen.py "樱花飘落的春天"          # 生成 videos/xxx.gif
  python video_gen.py "数据增长的柱状图" --chart # 生成一个数据柱状图动画
"""
import os, sys, math, random, subprocess, datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("需要 Pillow：pip install Pillow"); sys.exit(1)

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "videos")
os.makedirs(OUT, exist_ok=True)

W, H, FPS, DUR = 960, 540, 24, 3.2
N = int(FPS * DUR)


def _font(size):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def lerp(a, b, t):
    return a + (b - a) * t


def _bg(t):
    # 随时间变化的渐变
    c1 = (int(lerp(30, 80, abs(math.sin(t * 0.5)))), int(lerp(30, 70, abs(math.sin(t * 0.4)))), int(lerp(60, 130, abs(math.sin(t * 0.3)))))
    c2 = (int(lerp(90, 30, abs(math.sin(t * 0.3)))), int(lerp(70, 40, abs(math.sin(t * 0.5)))), int(lerp(180, 120, abs(math.sin(t * 0.6)))))
    return c1, c2


def _frame(i, text):
    t = i / FPS
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    c1, c2 = _bg(t)
    # 竖版渐变
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(lerp(c1[0], c2[0], k)), int(lerp(c1[1], c2[1], k)), int(lerp(c1[2], c2[2], k))))
    # 漂浮粒子
    random.seed(42)
    for _ in range(26):
        r = random.randint(6, 22)
        x = (random.random() * W + t * random.randint(20, 80)) % W
        y = (random.random() * H + math.sin(t * 2 + random.random()) * 20) % H
        a = 60 + int(60 * (0.5 + 0.5 * math.sin(t * 3 + random.random()))) % 255
        d.ellipse([x - r, y - r, x + r, y + r], fill=(int(lerp(120, 200, random.random())), int(lerp(120, 220, random.random())), 255))
    # 标题(打字机浮现)
    title = text[:18]
    show = title[: max(1, int(len(title) * min(1, i / (FPS * 1.2))))]
    f = _font(46)
    tw = d.textlength(show or " ", font=f)
    d.text(((W - tw) / 2, H * 0.34), show or " ", font=f, fill=(255, 255, 255))
    # 副标题
    d.text((W * 0.08, H * 0.68), "小焦 · 本地零算力渲染  🐳  " + str(round(t, 1)) + "s", font=_font(22), fill=(220, 220, 235))
    # 底部装饰条
    kw = text.split()
    if kw:
        d.text((W * 0.08, H * 0.06), ("  ".join(kw[:3]))[:40], font=_font(18), fill=(180, 190, 220))
    return img


def make_video(text, as_mp4=False):
    safe = "".join(c for c in text if c.isalnum() or c in "_-")[:30] or "video"
    name = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + safe
    out_gif = os.path.join(OUT, name + ".gif")
    frames = [_frame(i, text) for i in range(N)]
    frames[0].save(out_gif, save_all=True, append_images=frames[1:], duration=1000 // FPS, loop=0)
    print("✅ 已生成 GIF:", out_gif, "| 帧:", N)
    if as_mp4:
        try:
            import imageio.v2 as iio
            omp4 = os.path.join(OUT, name + ".mp4")
            iio.mimsave(omp4, frames, fps=FPS, quality=8)
            print("✅ 已生成 MP4:", omp4)
            return omp4
        except Exception:
            print("   (无 imageio/ffmpeg, 保留 GIF)")
    return out_gif


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "欢迎来到小焦"
    mp4 = "--mp4" in sys.argv
    make_video(text, mp4)
