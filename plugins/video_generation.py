# plugins/video_generation.py —— 视频生成插件(工具)：小焦说"生成视频"时调用
# 秒级切换(keep_warm)：ComfyUI/Wan 常驻时不重启, 直接调 ComfyUI API；未加载才启动。
import os, json, datetime, threading

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BASE)
_JOBS = {}  # 简单任务状态(与 video_service 一致)


def _port_up(port):
    import socket
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port)); return True
    except Exception:
        return False
    finally:
        s.close()


def _import_video():
    import sys
    sys.path.insert(0, os.path.join(_ROOT, "video_service"))
    import comfy_client as cc
    return cc


def _model_warm():
    """检测 ComfyUI 是否已加载/常驻(keep_warm)。简单探测 8188 + 缓存文件标记。"""
    return _port_up(8188)


class VideoGeneration:
    def get_tool_descriptions(self):
        return [{
            "name": "generate_video",
            "description": "根据场景描述生成一段真实视频(本地 ComfyUI+Wan2.1)。"
                           "输入中文场景描述，几秒到几分钟后返回视频链接。",
            "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "视频场景(中文)"}},
                           "required": ["prompt"]},
        }]

    def execute(self, tool_name, params):
        if tool_name != "generate_video":
            return "未知工具"
        prompt = (params.get("prompt") or "").strip()[:80]
        if not prompt:
            return "请输入视频场景"
        try:
            cc = _import_video()
            import config, model_switch as ms
            # 秒级：ComfyUI 已加载(Wan常驻)则直接用；否则启动
            if not _model_warm():
                ms.stop_brain()      # 让聊天大脑让出显存
                ms.start_comfy()     # 起视频大脑
            # 精炼提示词(小焦大脑, 快速)
            sys_p = ("你是专业电影导演，把用户的视频描述简洁改写成一段英文电影提示词(主体/光线/镜头/风格)，不超80字。只输出提示词。")
            import requests as _r
            base = "http://127.0.0.1:8080/v1"
            refined = prompt
            try:
                r = _r.post(base + "/chat/completions", json={"model": "xiaojiao1.0-4B",
                          "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": prompt}],
                          "max_tokens": 96, "temperature": 0.5, "chat_template_kwargs": {"enable_thinking": False}}, timeout=15)
                if r.status_code == 200:
                    out = (r.json()["choices"][0].get("message", {}).get("content") or "").strip()
                    if out:
                        refined = out
            except Exception:
                pass
            ckpt = config.find_checkpoint() or "dit_fp8.safetensors"
            wf = json.load(open(os.path.join(_ROOT, "video_service", "workflow_wan.json"), encoding="utf-8"))
            for n in wf.values():
                for k, v in n.get("inputs", {}).items():
                    if isinstance(v, str):
                        n["inputs"][k] = v.replace("__CKPT__", ckpt).replace("__POS__", refined)
            pid = cc.submit_workflow(wf)
            fn, sub, ftype = cc.wait_output(pid)
            out = os.path.join(config.OUT_DIR, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_video" + (os.path.splitext(fn)[1] or ".mp4"))
            cc.download_video(fn, sub, ftype, out)
            # keep_warm: 生成完不卸 Wan/不杀 ComfyUI(秒级下次)
            return "✅ 视频生成完成：/videos/" + os.path.basename(out) + "\n📝 提示词：" + refined
        except Exception as e:
            return "⚠️ 视频生成失败：" + str(e)[:200]


def get_plugin():
    return VideoGeneration()
