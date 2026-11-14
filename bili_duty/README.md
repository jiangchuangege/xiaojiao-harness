# B站插件 · 接入小焦说明

> 📖 **完整文档（值班流程 / 29 个工具分组 / 红线 / 配置项 / 常见故障）在
> [`docs/bili-duty.md`](../docs/bili-duty.md)** —— 本文件只留"这一层怎么接上的"。

本目录是 **B站能力**（搜索 / 评论 / 字幕 / 弹幕 / 榜单 / UP主 / 封面画面）的运行体，
由 `plugins/bili_duty.py` 注册成小焦的工具（**29 个**，全部以 `bili_` 开头）。

## 它是怎么接上的

```
小焦（xiaojiao_app.py）
  └─ plugins/bili_duty.py          ← 插件外壳：声明工具、校验必填参数、守住红线
       └─ 子进程：bili_duty/.venv 的 python
            └─ bili_duty/xj_bridge.py   ← 桥：按工具名调本目录里的函数，JSON 回传
                 └─ bili_duty/mcp_server.py  ← 真正的 B站 客户端（依赖 bilibili_api + mcp）
```

**为什么不直接 import**：`mcp_server.py` 依赖 `bilibili_api` 和 `mcp`，这两个只在
本目录自带的 `.venv` 里；小焦自己的解释器没有。装进小焦环境会污染它，所以走子进程。
好处是插件崩了只影响这一条工具，不会把小焦带下去；代价是每次调用多约 0.3~1 秒进程启动。

## 目录内容

| 路径 | 说明 |
|---|---|
| `plugins/bili_duty.py`（在上层目录） | 插件外壳：14 个工具 + 红线闸门 |
| `xj_bridge.py` | 子进程桥（新增，本仓库的接入层） |
| `mcp_server.py` | B站 客户端实现（vendor 自上游，已加白名单/原子写/风控透传等加固） |
| `config.json` | 插件配置（`pythonPath` 已指向本目录的 `.venv`） |
| `bili_credential.json` | B站 登录凭证（复用原插件的登录态） |
| `duty.md` / `seen.md` / `comments.md` | 原插件的“值班”人设与已阅记录（小焦不自动跑，保留备查） |
| `.venv/` | **目录联接**，指向原插件环境的 venv（不入库，见 `.gitignore`） |
| `.hf-cache/` | **目录联接**，指向语音转写模型缓存（不入库） |

## 红线（重要）

- `bili_reply` 会**往 B站 写内容**（发评论）。默认**拒绝**：
  必须调用方显式传 `force=true` 才发。插件自身还有第二道闸（`_guard("bili_reply")`）。
- 所有工具失败时**如实返回原因**，绝不编造结果；缺必填参数在本地就拦（不拿 B站 的 -400 糊弄用户）。

## 自测

```powershell
python tools/test_bili_plugin.py     # 17 条：装载 / 路由 / 真跑 / 红线 / 环境
```

## 换环境 / 迁移

- 换机器：把本目录整体拷走，`config.json` 的 `pythonPath` 指到新 venv 即可
  （插件会**优先**用本目录的 `.venv`，所以只要联接/venv 在，通常不用改配置）。
- 更新 B站 客户端：替换 `mcp_server.py` 时，注意保留文件头注释里列的那 6 处加固
  （白名单、凭证容错、原子写、风控透传、回执字段、封面/帧返回图片）。
- 卸载：删 `plugins/bili_duty.py` 与 `bili_duty/xj_bridge.py` 即可让小焦不再加载（其余文件留着不碍事）。
