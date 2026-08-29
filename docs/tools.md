# 工具说明

小焦能"动手干活"，靠的是一套工具。模型会自己决定用哪个、参数是什么，框架负责执行。

## 内置工具

| 工具 | 干嘛的 | 参数 |
| --- | --- | --- |
| `run_command` | 跑一条 PowerShell 命令 | `command` |
| `write_file` | 写一个文件（自动建目录） | `path`,`content` |
| `read_file` | 读一个文件（默认取前 2500 字） | `path`,`max_chars` |
| `list_files` | 列目录 | `path` |
| `open_app` | 打开应用/文件 | `path` |

## 危险命令

遇到 `rm / del / format / shutdown / reg delete / taskkill /f` 这类，或往系统目录写文件，小焦**不会直接执行**，会先挂起来，等你在网页点「✅ 确认执行」。

## 多步执行

模型会自行推理"建目录 → 写文件 → 打开"这几步，逐个调工具，最后给你一句总结。比如"在桌面建 tests1 文件夹写个 index.html 并打开"，它会：
1. `run_command`（建文件夹）
2. `write_file`（写 index.html）
3. `run_command`（打开页面）

## 工具的入口

- **网页**：小焦自己在对话里决定调不调。
- **直接调**：`python xiaojiao_tools.py` 起在 `http://127.0.0.1:5003`，`POST /api/run` 传 `{"name":"write_file","command/path/content":...}`。

## 插件也算工具

`plugins/` 里的插件会自动注册成工具，模型同样能调。见 [PLUGINS.md](PLUGINS.md)。
