# 🎵 音乐生成插件（小焦 MusicGen）

小焦现在能**根据文字描述生成音乐**（本地 MusicGen，Meta）。

## 开启
**设置 → 插件** 确认「music_generation」已启用（默认开）。

## 怎么用
对小焦说：
> "用 generate_music 生成一段 5 秒的轻快的钢琴曲"

小焦会调用 `generate_music(prompt, duration)` 工具 → 生成 wav → 聊天里出现 **🎵 音频播放器**。

## 工具
`generate_music(prompt, duration)`
- `prompt`：音乐描述（中文/英文，如"电子游戏BGM""轻柔冥想曲"）
- `duration`：时长秒数（默认 5，最大 20）

## 依赖（首次需装）
```
pip install audiocraft
```
- 首次使用插件会自动 `pip install audiocraft` + 下载 **MusicGen-small** 模型（~1.5G）。
- 生成时**临时卸载大脑腾显存**，生成完恢复。

## 流程
```
小焦调用 generate_music → 卸载 llama 大脑(腾显存)
  → MusicGen 生成 wav → 存 media/music/xxx.wav
  → [music]media/music/xxx.wav[/music] → 前端 <audio> 播放
```
