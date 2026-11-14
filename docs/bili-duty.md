# 刷B站（bili_duty）· 让它在你不看电脑时替你把 B站 过一遍

> 一句话：**你不在的时候，它替你把 B站 上值得的东西挑出来**，写成一份能看的日报，
> 并且能回评论 / 发私信（都要你显式允许）。它不是"又一个 B站 客户端"，
> 而是**一个值班流程**：搜 → 挑 → 读字幕/转写 → 判断 → 记账 → 汇报。

- 代码：`bili_duty/`（运行体）+ `plugins/bili_duty.py`（插件外壳）
- 设置页：小焦界面里「刷B站」那一页（读的是插件自己的 `config.json` / `state.json`）
- 自测：`python tools/test_bili_plugin.py`（装载 / 路由 / 真跑 / 红线 / 环境）

---

## 一、它长什么样：三层，靠子进程隔开

```
小焦（xiaojiao_app.py）
  └─ plugins/bili_duty.py          ← 插件外壳：声明工具、校验必填参数、守红线（29 个 bili_* 工具）
       └─ 子进程：bili_duty/.venv 的 python
            └─ bili_duty/xj_bridge.py   ← 桥：按工具名调本目录的函数，JSON 回传
                 └─ bili_duty/mcp_server.py  ← 真正的 B站 客户端（bilibili_api + mcp，vendor 自上游）
```

**为什么不直接 import**：`mcp_server.py` 依赖 `bilibili_api` 和 `mcp`，这两个只装在
`bili_duty/.venv` 里；小焦自己的解释器没有，装进去会污染它的依赖。

- 好处：**插件崩了只影响这一条工具**，不会把小焦带下去；
- 代价：每次调用多约 0.3~1 秒的进程启动。

这条"隔一层"的边界是有代价换来的：B站 客户端的风控、cookie 过期、接口改版都会以**工具失败**
的形式出现，而不是让小焦整体挂掉。失败时工具**如实返回原因**，绝不编造结果。

---

## 二、29 个工具：按"能干什么"分组

全部以 `bili_` 开头，在 `plugins/bili_duty.py` 里声明。分组只是给人看的，模型看到的是完整清单。

| 想干什么 | 工具 |
|---|---|
| 找视频 | `bili_search`（关键词/排序/页数）、`bili_rank`、`bili_hot_videos`、`bili_weekly_hot`、`bili_hot_buzzwords`、`bili_recommend`、`bili_video_zones` |
| 看一个视频 | `bili_video_info`、`bili_subtitle`（**判断内容靠它**）、`bili_comments`、`bili_danmaku`、`bili_cover`、`bili_frames`、`bili_crawl` |
| 听声音 | `bili_transcribe_start` → `bili_transcribe_result`（无字幕时本地 ASR，`asrModel` 可配 small/medium） |
| 看人 | `bili_user_info`、`bili_user_videos` |
| 我的收藏 / 消息 | `bili_favorite_lists`、`bili_favorite_content`、`bili_unread_messages`、`bili_received_replies`、`bili_received_at_and_likes` |
| 写（**都受红线管**） | `bili_reply`（发评论/回复）、`bili_send_message`（私信） |
| 登录 | `bili_login`、`bili_login_check`、`bili_check_credential` |

### 红线（两条，写在插件里，不靠模型自觉）

1. **`bili_reply` 默认拒绝**：它会**往 B站 写内容**。必须调用方显式传 `force=true` 才发，
   插件自身还有第二道闸（`_guard("bili_reply")`）。
2. **缺必填参数在本地就拦**：不拿 B站 的 `-400` 糊弄用户；工具失败时**如实说原因**。

---

## 三、值班流程（一轮到底做什么）

值班人设与纪律写在 `bili_duty/duty.md`（`duty-charter v3`），要点：

```
搜（关键词 / 榜单 / 关注）
  → 判重（duty_seen_recent 看已阅表）
  → 读内容（bili_subtitle 优先；没有字幕才 bili_transcribe_start + result）
  → 判断值不值得留（"空也是一种结果"：0 字节/只有署名 = 无语音，记一笔跳过）
  → 记账（duty_seen_append / duty_report_append / duty_digest_append）
  → 汇报（reports/ 下当天的日报）
```

它跑在**最小权限 profile**（`headlessProfile`）上：**没有文件读写、没有命令行、不能自己上网抓页面**，
只能通过 `duty_*` 那几只固定路径的工具写自己的目录 —— 因为这一轮是**无人看着**跑的。

三条省钱纪律（写在 duty.md 里，有实测依据）：看够了就下结论、工具失败两次就记为跳过、
"空"是结果不是坏了（不要去写临时脚本反复重跑）。

### 调度：什么时候跑

`config.json` 里控制：`perDay`（每天几轮）、`peakValley` / `noon` / `night`（窗口），
`manualMax` / `manualCooldownMin`（你手动点"跑一轮"的上限与冷却）。
`state.json` 记这一轮的进度与配额，`run-lock.json` 防并发重复跑。

---

## 四、写回 B站 的那部分（评论 / 私信）

| 开关 | 作用 |
|---|---|
| `autoComment` | **自动评论并 @ 你**，默认关；关着时 `bili_reply` 会拒绝（除非调用方 `force=true`） |
| `replyToComments` / `replyPerRound` | 回复别人在你视频下的评论（条数上限） |
| `dmMode`（off / only / both）+ `dmUid` | 私信投递：关 / 只发给你 / 两边都发 |
| `mentionName` | 评论里 @ 的那个名字 |
| `keywords` / `excludeWords` | 挑视频的关键词与排除词 |
| `commentLength` | 评论长度档（short 等） |
| `personaPrompt` | 值班时用的口吻（与聊天人设分开，避免"值班腔"污染日常对话） |
| `modelProvider` / `modelName` | 这一轮用哪颗模型（空 = 跟随当前会话；`bili-duty` profile 里确定有的是 `deepseek-account` / `deepseek-official`） |

---

## 五、数据和文件都在哪

| 路径 | 是什么 | 入不入库 |
|---|---|---|
| `bili_duty/config.json` | 插件配置（设置页改的就是它） | 入库 |
| `bili_duty/state.json` | 本轮进度 / 配额 | 入库 |
| `bili_duty/seen.md` | **判重表**（挑过/跳过/评论过） | 入库 |
| `bili_duty/reports/` | 每天的日报 | 运行时产物 |
| `bili_duty/digest/` | 本月归档 | 运行时产物 |
| `bili_duty/out/` `tmp/` `.cache/` | 抓取与转写的中间产物 | 不入库 |
| `bili_duty/bili_credential.json` | B站 登录凭证 | **不入库** |
| `bili_duty/.venv/` `.hf-cache/` | 目录联接（指向原插件的 venv / 模型缓存） | 不入库 |

---

## 六、设置页与接口

设置页在界面「刷B站」那一页，所有数字都来自 `/api/bili_duty*`（后端读的是**真文件**，
`config.json` / `state.json` / `reports/*.md`，不是打桩）；画廊封面走 `/api/bili_duty/cover/<bv>`。

| 接口 | 用途 |
|---|---|
| `POST /api/bili_duty` | 读状态 / 改设置 / 跑一轮（`/run`） |
| `POST /api/bili_duty/login_start` → 轮询 | 扫码登录 B站 |
| `GET /api/bili_duty/gallery` | 画廊（挑过的视频） |
| `GET /api/bili_duty/cover/<bv>` | 封面字节（走本地代理，不把 B站 的图直挂到页面） |

---

## 七、自测与验收

```powershell
python tools/test_bili_plugin.py      # 17 条：装载 / 路由 / 真跑 / 红线 / 环境
python tools/test_bili_full.py        # 端到端（需要登录态与网络）
```

验收口径（人话版）：

1. **装载**：小焦启动日志里有「刷B站接口已挂载 + 自动轮已启动」；
2. **看得见**：界面上那页能读到真实配额与日报；
3. **红线**：`autoComment` 关着时 `bili_reply` 必须**拒绝**（这条要在自测里亮绿，不能靠人记得）；
4. **失败如实**：断开网络跑一次，工具返回的是原因，不是编出来的结果。

---

## 八、常见故障

| 症状 | 多半是 | 怎么办 |
|---|---|---|
| 所有工具都报 `NO_ADAPTER` / 找不到 python | `bili_duty/.venv` 联接断了（换机器/清理过目录） | 重建联接，或改 `config.json` 的 `pythonPath` |
| 401 / 风控 / 需要登录 | cookie 过期 | 设置页扫码登录（`bili_login`），确认 `bili_credential.json` 已更新 |
| 字幕一直空 | 这条视频**没有语音**（纯音乐/录屏） | 按"跳过（无语音）"记账，**不是坏了**，别反复重跑 |
| 转写很慢 | 本地 ASR 在跑，`asrModel` 越大越慢 | 平时用 `small`；重要长视频再上 `medium` |
| 一轮跑很久、token 花得多 | 它在反复权衡同一条 | 看 `duty.md` 那三条纪律；必要时调小 `fetchPerRound` / `searchPages` |

---

## 九、迁移与卸载

- **换机器**：把 `bili_duty/` 整个拷走，`config.json` 的 `pythonPath` 指到新 venv
  （插件会**优先**用本目录的 `.venv`，联接在就通常不用改）。
- **更新 B站 客户端**：替换 `mcp_server.py` 时，保留文件头列的那 6 处加固
  （白名单、凭证容错、原子写、风控透传、回执字段、封面/帧返回图片）。
- **卸载**：删 `plugins/bili_duty.py` 与 `bili_duty/xj_bridge.py` 即可让载体不再加载
  （其余文件留着不碍事，`seen.md` 这类历史记录也还在）。

---

## 十、它和"聊天"的边界

值班用的是**独立人设**（`personaPrompt`）和**最小权限 profile**，跟日常聊天那套是分开的：
值班腔不会带进聊天，聊天里的工具权限也不因为值班而被放宽。这条边界值得守住 ——
一个"无人看着还在外面替你说话"的流程，权限越大，出事时越难收。
