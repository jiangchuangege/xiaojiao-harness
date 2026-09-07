# 请先读了这些再提 PR 🧡

感谢你想帮小焦变好！提 Pull Request 前，请确认：

## ✅ 遵守
- **不要把个人数据/密钥/大文件提交进仓库**（`:gitignore` 已列出，别改坏它）。例如 `xiaojiao_control.json`（含 key）、`*.pth`、`*.gguf`、`*.mp4`、`*.log`、训练语料等。
- 改动 **README / docs** 时，让它和当前功能一致（尤其别把"模型"写死成某个型号，小焦兼容任意 OpenAI 大模型）。
- 代码改动请跑通一个最小验证（语法/启动不报错）。

## 📝 PR 描述请写清
- **做了什么**（一句话或几条）
- **为什么做**（解决什么问题/加了什么能力）
- **影响范围**（改了哪些文件、和哪些现有行为相关）
- 如带截图 / 测试结果更好。

## 🔧 开发提示
- 加能力最省事：写一个**插件**（`plugins/*.py` / `.js` / `.json` / `.skill.md`），详见 `docs/extend.md`。
- 改壳本身：`xiaojiao_app.py`（Web+Agent）、`start_xiaojiao.py`（一键启动）、`brain_manager.py`（多脑调度）。

有任何问题，先开个 **Issue** 聊聊，别闷头改。🐱
