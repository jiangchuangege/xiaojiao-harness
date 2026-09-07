# 更新日志 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。所有重要改动都会记录在这。

## [v1.0.0] - 当前

**🚀 首次正式发布**：N.E.K.O. 猫娘桌面伙伴 + 多大脑秒切 + 全新封面 + 社区规范。

### Added
- 🐱 **N.E.K.O. 猫娘桌面伙伴**：一键启动自动拉起你下载的猫娘服务（main_server:48911 / memory_server:48912），并后台学习你与猫娘的对话。
- 🧠 **多大脑·秒级切换**：`brain_manager.py`（RUN/WARM/OFF）+ llama-swap(9292)。
- 🎨 **全新 README 封面**：用户猫娘壁纸（`assets/xiaojiao_cover.jpg`）。
- 🎬 **真·文生视频**、🎙️ **播客大脑**、🎵 **音乐**、👁️ **视觉识图**、💰 **成本看板**。
- 📚 社区文件：`CODE_OF_CONDUCT.md`、`.github/ISSUE_TEMPLATE`、`.github/PULL_REQUEST_TEMPLATE.md`。

### Changed
- 模型表述改为**可插拔**（兼容任意 OpenAI 大模型），去掉写死的型号。
- 安装向导 `install_all.py`：模型检测改为**本地任意 GGUF 或 云端兼容 key 任一通过**，并补齐可选功能依赖检测（N.E.K.O./配音/封面/音乐）。

### Removed
- 旧 Electron 桌面宠物（`desktop/`、`jarvis_desktop.py`、`docs/jarvis-desktop.md`），桌面形象统一改用 N.E.K.O. 猫娘。

### Fixed
- 安装向导不再死锁 `xiaojiao1.0-4B` 型号。
- `/api/env` 模型检测改为模型无关（本地 GGUF 或云端 API 任一即算有）。
- 网页顶栏 "J.A.R.V.I.S." 改为 "🐱 猫娘"，`/pet` 重定向到 N.E.K.O. 页。

---

## v1.0.0 之前的演进快照（已并入当前版）

- v2.3.0：桌面宠物 / 视觉接口 / 成本看板 / 插件万能桥
- v2.2.0：会话侧边栏 / 会话持久化
- v2.1.0：聊天历史持久化 / 插件接入工具系统
- v2.0.0：原生 function calling、多步任务自动执行
- v1.0.0（初版）：完整壳 + 工具 + 记忆 + 会话 + 联网 + 插件

> 这些历史版本已统一收敛到 **v1.0.0**（当前发布）。
