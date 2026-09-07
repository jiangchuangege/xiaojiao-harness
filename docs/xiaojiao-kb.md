# 小焦 XiaoJiao 项目知识库（喂给 AI/猫娘的知识）

> 用途：让 AI（猫娘/N.E.K.O. 或小焦自己）在对话中**真正懂小焦这个项目**——代码、原理、模块、部署、接口。随项目更新同步维护。

---

## 1. 一句话定位
**小焦 = 一个本地优先的 AI 伙伴。** 底层用你自己的大模型（llama.cpp GGUF 本地推理），上面套了人格/记忆/工具/插件，并接了**多大脑秒级切换**（聊天/编码/视频/播客/音乐），还能连 **DeepSeek 云端**当更强大脑。它既会陪你聊天，也能真·干活（写文件/跑命令）、真·生成视频/播客/音乐。

## 2. 核心模块（都在项目根目录）
| 文件/目录 | 干什么 |
|---|---|
| `xiaojiao_app.py` | **主应用**（Flask, 端口 5000）：Web UI + 人格 + 工具 + 记忆 + 会话 + `/v1` OpenAI 兼容接口 |
| `start_xiaojiao.py` | **一键启动**：起 llama-swap(9292) 大脑 + Web(5000) + N.E.K.O. 猫娘(48911/48912) |
| `brain_manager.py` | **多大脑调度中心**：聊天/视频/播客/云视频大脑注册表，RUN/WARM/OFF 秒级切换 |
| `video_service/` | **文生视频**：本地 ComfyUI+Wan2.1 **或** 云端(Agnes 免费 API)，按需切换 |
| `podcast_service/` | **播客大脑**：LLM 写稿 → Chatterbox 配音 → pydub 拼接 → SD1.5 封面 |
| `music_service/` | **ACE-Step 音乐大脑**（调它自带 API server :8001） |
| `learn_from_neko.py` | **学 N.E.K.O. 猫娘与主人的对话** → 小焦记忆库（支持 `--daemon` 后台每 5 分钟学） |
| `plugins/` | **四类插件生态**：py / js / api(json) / skill(md) |
| `docs/` | 全套文档：架构/quickstart/podcast/video/猫娘(N.E.K.O.)等 |
| `xiaojiao_control.json` | 操控文件：人格(role)/大脑(brain)/模型(models)/行为 |

## 3. 大脑（可插拔、秒级切换）
- 由 `xiaojiao_control.json` 的 `brain.engine` 决定：`llama`(本地) / `auto`(自动) / `api`(外接) 
- 本地大脑走 **llama-swap(9292)**（llama-server 托管，多模型热切换）
- 模型：聊天 4B（`xiaojiao`）、编码 8B（`coder`）；DeepSeek 走 `brain.api`
- **8G 显存规则**：显存(RUN)只 1 个 + 内存(WARM)留 1 个；切换=顶掉旧 WARM→当前去 WARM→目标上显存；生成视频/音乐前会先卸载另一个 llama

## 4. 视频生成（`video_service/`）
- **模式**：`api`（云端 Agnes，免费 1RPM 节流，不占显存）或 `local`（本地 ComfyUI+Wan2.1）
- 网页点 🎬 → 精炼提示词 → 切大脑 → 生成 → 回显
- 云端接口：`POST https://apihub.agnes-ai.com/v1/videos`（Bearer key, model=agnes-video-*, mode=ti2vid）→ 轮询 `/agnesapi`

## 5. 播客生成（`podcast_service/`）
- `/podcast` 页面 + `POST /api/podcast` + `/api/podcast/status/<jid>`
- 流程：LLM 写双人中文对话稿（可指定分钟数, 默认15）→ Chatterbox TTS 逐句 → pydub 拼 mp3 → SD1.5 出封面
- 参数：topic / host_a / host_b / rounds / style / minutes

## 6. 桌面猫娘伙伴（N.E.K.O.）
- **当前**：N.E.K.O. 猫娘（`G:\moxing__xiaojiao\maoniang\N.E.K.O-main`，端口 48911/48912）——成熟 Live2D 猫娘 App，目标"N.E.K.O. 形象壳 + 小焦本地内核"。
- **互相学习**：`learn_from_neko.py` 读猫娘 `facts.json`/`persona.json`（`%LOCALAPPDATA%\N.E.K.O\memory\YUI\`）→ 写进小焦记忆库（`学会:*` / `猫娘说话风格`）。
- **N.E.K.O. 插件**：`%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\`，提供「装小焦」体检 + 安装指引。

## 7. 部署/启动
```powershell
cd C:\xiaojiao\xiaojiao harness
pip install -r requirements.txt
python start_xiaojiao.py      # 起 9292 大脑 + 5000 网页 + N.E.K.O. 猫娘(48911/48912)
```
- 网页：`http://127.0.0.1:5000` ｜ 猫娘：`http://127.0.0.1:48911` ｜ 播客：`/podcast` ｜ `/v1` 给 dsh/客户端 ｜ 成本看板 `/cost`
- N.E.K.O.: `uv sync` → `build_frontend.bat` → `python -m app.memory_server` + `python -m app.main_server` → `http://127.0.0.1:48911`

## 8. 常用接口速查
- `POST /api/chat`：主对话（带记忆/工具）
- `POST /api/tts`：Chatterbox 文字→语音
- `POST /api/video`：文生视频（mode 由 `/api/video/mode` 切 api/local）
- `POST /api/podcast`：生成播客
- `GET /api/env`：装小焦环境体检 ✅/❌
- `GET /api/models` / `POST /api/model/add`：模型管理（OpenAI 兼容自动识别）

## 9. 人格（role）
小焦的人设在 `xiaojiao_control.json` 的 `role` 字段 + 网页设置可改。现在想让它**学 N.E.K.O. 猫娘的说话方式**（独立人格、简洁口语、不说教、不重复、有自己兴趣）——见 `docs/xiaojiao-catgirl-style.md`。
