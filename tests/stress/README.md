# 压力测试（tests/stress）

**真实调用，禁止模拟。** 这套测试既能在本机跑，也能在 CI 跑；结果可机读（`results.json`），
CI 用「通过率 < 95% 即失败」当质量闸门。

## 怎么跑

```powershell
# 全部用例（离线 + 安全 + 联网，约 75 秒）
python tests/stress/run_all.py

# 只跑离线 + 安全用例（3 秒，不联网）
python tests/stress/run_all.py --offline

# 实机验收（需要小焦正在运行）
python tests/stress/live_check.py

# UI 真渲染检查（需要小焦正在运行 + playwright）
python tests/stress/ui_check.py --out ui_chat.png

# 30 分钟极速压力测试（整机端到端 + 插件直连，需小焦正在运行；自己在终端跑）
python tests/stress/stability_30m.py --minutes 30

# 快速模式 / 自定义门槛 / 自定义结果文件
python tests/stress/run_all.py --quick
python tests/stress/run_all.py --json results.json --min-pass-rate 95
```

退出码：`0` = 通过率达标；`1` = 低于门槛（CI 据此判定失败）。

## 覆盖范围

| 文件 | 组 | 覆盖内容 |
| --- | --- | --- |
| `run_all.py` | 编排 | 三套件顺序执行 + 通过率门槛 + 退出码 |
| `live_check.py` | **实机** | 对**正在运行**的小焦发真实请求（端点/体检/抓取/JSON 展示/SSRF/指标/日志）——发布后的"最后一公里"自检 |
| `ui_check.py` | **UI 渲染** | 用 Playwright **真开浏览器**：发消息 → 等回答 → 检查 `pre.code`/链接/标题块 → 截图 + 控制台错误 |
| `test_units.py` | 安全 | SSRF 10 类内网/危险地址 100% 拦截、合法 URL 放行 |
| | 脱敏 | `sk-…` / `ghp_…` / `AKIA…` / JWT / 键值对，一律打码 |
| | 展示 | JSON 美化缩进、Markdown 转义修复、超长折叠、非 JSON 原样 |
| | 会话回收 | LRU 踢最久未用、非法配置回退、TTL 回收、后台巡检线程 |
| | 指标 | 计数/延迟/熔断次数、错误脱敏、Prometheus 文本格式、落盘 |
| | 批量配置 | 7 种非法配置 → 中文错误；合法配置字段生效 |
| | 工具集 | 原生 13 工具 1:1 全暴露、总数 17 |
| | 参数校验 | 空参/非法参/缺参 → 中文错误，绝不裸异常 |
| `test_network.py` | 抓取 | `get` / `make_request` / `fetch` / `stealthy_fetch` / `scrape_with_selector` 真实抓取 |
| | 批量 | 去重、部分失败隔离、结果保序、字符串规范化、空列表报错 |
| | 会话 | `session_fetch` / `session_make_request` / `screenshot` / `list_sessions` + 回收器登记 |
| | 对抗 | 重定向型 SSRF、参数注入、页面内容注入、10000 字符 URL、特殊字符、超时纪律、5 并发不串数据、熔断触发与 30 秒自愈 |
| `stability_30m.py` | **长稳** | 30 分钟极速压测，**真实执行、禁止模拟**：整机对话/接口 + 插件工具直连，见下节 |

## 30 分钟极速压力测试（`stability_30m.py`）

用 30 分钟的高密度真实压力，等效替代原 24 小时长跑：覆盖**整机端到端**（默认）与**插件直连**两层。

```powershell
python tests/stress/stability_30m.py                      # 整机 + 插件，30 分钟
python tests/stress/stability_30m.py --target app         # 只压整机（不动插件）
python tests/stress/stability_30m.py --minutes 1 --interval 4 --chat-every 30   # 1 分钟自检
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--target` | `both` | `app` 整机 / `plugin` 插件 / `both` 两者 |
| `--base` | `http://127.0.0.1:5000` | 小焦地址 |
| `--minutes` | `30` | 时长 |
| `--interval` | `10` | 每轮间隔（秒）—— 密度比 24h 方案高 |
| `--chat-every` | `60` | 每多少秒发一次真实对话 |
| `--mem-abs-mb` | `10` | 内存判定的绝对增量地板 |
| `--out` / `--json` | `logs/stability_30m.md` / `.json` | 报告与机读明细 |

**内存泄漏判定（两条同时成立才判泄漏）**：

1. 绝对增量 > **10 MB**（`--mem-abs-mb`）
2. 相对涨幅 > **30%**

只有「涨得够多」且「涨得够快」才算泄漏 —— 极低基数（如 0.19MB → 0.20MB）不会被百分比放大而误判。
采样每 30 秒一次，对象为本进程堆/工作集 + 小焦进程工作集；出现真泄漏立即中止并打印原始数据。

**其他自动核对**：HTTP 非 2xx、会话回收、熔断触发 + 30 秒自愈、NVD 退避重试、工具异常。
测试自建会话，结束（含异常退出）后全部删除，不污染你自己的会话。
退出码：`0` = PASS，`1` = FAIL。

## CI

`.github/workflows/stress-test.yml`：

- **触发**：每天 03:00（UTC+8）定时 · 手动 `workflow_dispatch` · `plugins/scrapling_bridge.py` 或本目录变更时 · PR
- **门槛**：`--min-pass-rate 95`，低于即 job 失败
- **产物**：`results.json` / `_metrics.json` 上传为 artifact（保留 30 天）
- **摘要**：结果写入 GitHub Job Summary（总用例/通过/失败/通过率）

> 注：CI 环境无自备 Chrome，workflow 会先 `python -m playwright install chromium`；
> 若无网络或内核缺失，浏览器类用例会失败并被计入通过率 —— 这是**有意设计**：
> 通过率不达标时 CI 直接失败，不做兜底放行。

## 本地实测基线

```
离线 182/183（1 跳过）· 100% · 9.0s
全部 217/217 · 100%
实机验收 live_check 35/35 · UI 样式 21/21 · 预设 13/13
```
