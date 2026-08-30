# plugins/music_generation.py —— 音乐生成插件(工具)：小焦说"生成音乐/曲子/旋律"时调用
# 本地 MusicGen(facebook/musicgen-small, 首次自动下载~1.5G) → 生成 wav → 前端播放
import os, sys, datetime, subprocess

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BASE)
_MUSIC_DIR = os.path.join(_ROOT, "media", "music")


def _free_vram():
    """让出显存给音乐模型(卸载 llama 大脑)。"""
    try:
        import video_service.model_switch as ms
        ms._llama_swap_unload("coder")
        ms._llama_swap_unload("xiaojiao")
    except Exception:
        pass
    try:
        import brain_manager as bm
        for k in list(bm.BRAINS.keys()):
            bm._full_stop(k)
    except Exception:
        pass


class MusicGeneration:
    def get_tool_descriptions(self):
        return [{
            "name": "generate_music",
            "description": "根据文字描述生成一段音乐(本地 MusicGen)。prompt=音乐描述(如'轻快的钢琴曲''电子游戏BGM'), duration=时长秒(默认5)。"
                           "第一次使用会自动下载模型(~1.5G)并安装依赖。返回音频链接。",
            "parameters": {"type": "object", "properties": {
                "prompt": {"type": "string", "description": "音乐描述(中文/英文)"},
                "duration": {"type": "number", "description": "时长秒数(默认5, 最大20)"}},
                "required": ["prompt"]},
        }]

    def execute(self, tool_name, params):
        if tool_name != "generate_music":
            return "未知工具"
        prompt = (params.get("prompt") or "").strip()[:120]
        dur = max(1, min(int(params.get("duration", 5)), 20))
        if not prompt:
            return "请输入音乐描述"
        try:
            _free_vram()
            # 首次: 装 audiocraft
            try:
                import audiocraft  # noqa
            except Exception:
                subprocess.run([sys.executable, "-m", "pip", "install", "audiocraft", "-q"], timeout=600)
            import torch
            from audiocraft.models import MusicGen
            from audiocraft.data.audio import audio_write
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            model = MusicGen.get_pretrained("facebook/musicgen-small", device=dev)
            model.set_generation_params(duration=dur)
            wav = model.generate([prompt])
            os.makedirs(_MUSIC_DIR, exist_ok=True)
            out = os.path.join(_MUSIC_DIR, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            audio_write(out, wav[0].cpu(), model.sample_rate, strategy="loudness")
            rel = os.path.join("media", "music", os.path.basename(out) + ".wav").replace("\\", "/")
            return "✅ 音乐生成完成：\n[music]" + rel + "[/music]\n🎵 " + prompt + "（" + str(dur) + "秒）"
        except Exception as e:
            return "⚠️ 音乐生成失败：" + str(e)[:200]


def get_plugin():
    return MusicGeneration()
