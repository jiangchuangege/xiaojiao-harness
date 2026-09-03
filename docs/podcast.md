# 🎙️ 播客大脑（小焦 · 播客生成）

小焦新增**播客大脑**：给它一个主题，它自己**写稿 → 配音 → 拼接 → 出封面**，生成一段真·中文播客。

## 打开
浏览器访问 **`http://127.0.0.1:5000/podcast`**，或调接口：

```
POST /api/podcast
{"topic":"AI 会取代人类工作吗?","host_a":"小李","host_b":"小焦","rounds":4,"style":"轻松有趣","build_cover":true}
→ {"ok":true,"jid":"..."}

GET  /api/podcast/status/<jid>   → 查进度/结果
```

## 它怎么生成（四步）
1. **写稿** — 用小焦聊天大脑(9292) 生成 `rounds` 轮双人播客对话。
2. **配音** — 用 Chatterbox TTS（复用小焦 `/api/tts` 那套，不二次占显存）逐句合成。
3. **拼接** — pydub 按对话顺序 + 静音间隔合成一个 mp3。
4. **封面** — 用 **SD1.5**（`v1-5-pruned-emaonly.safetensors`，默认 `G:\moxing\`）文生图出封面。

## 可自定义
- **主题** `topic`：随便写，决定内容。
- **主持人** `host_a` / `host_b`：两个主持人名字。
- **轮数** `rounds`：几轮对话（2/4/6/8）。
- **风格** `style`：轻松有趣 / 专业严谨 / 幽默吐槽 / 深度访谈。
- **封面开关** `build_cover`：要不要生成封面图（不生成更快、省显存）。

## 多脑切换机制
播客大脑已注册进 `brain_manager.BRAINS`（`podcast`）。遵循小焦"用时一个大脑上显卡、用别的就替换/卸载"的机制：
- 生成播客时，聊天大脑写稿完成后**释放**，TTS + SD1.5 上显存。
- 生成完，中间 wav 自动清理，只留 `xxx_podcast.mp3` + `xxx_cover.png` 成品。
- 需要聊天/视频时，播客大脑让位，互不打架。

## 依赖
- `diffusers` / `transformers` / `accelerate` / `PIL` / `safetensors`（封面，已装）
- `pydub` / `soundfile`（拼接，已装）
- Chatterbox TTS（小焦 `/api/tts` 已用）
- SD 模型路径可用 `XIAOJIAO_SD_MODEL` 环境变量改（默认 `G:\moxing\v1-5-pruned-emaonly.safetensors`）。

## 文件
- `podcast_service/podcast_gen.py` — 核心：写稿/配音/拼接/封面
- `podcast_service/podcast_api.py` — Blueprint：`/podcast` + `/api/podcast` + `/api/podcast/status`
