# 压力测试（tests/stress）

**真实调用，禁止模拟。** 这套测试既能在本机跑，也能在 CI 跑；结果可机读（`results.json`），
CI 用「通过率 < 95% 即失败」当质量闸门。

## 怎么跑

```powershell
# 全部用例（离线 + 联网，约 1~2 分钟）
python tests/stress/run_all.py

# 只跑离线用例（不联网：配置校验/安全闸门/会话回收/指标/脱敏/JSON 美化/参数校验）
python tests/stress/run_all.py --offline
$env:XJ_STRESS_OFFLINE=1; python tests/stress/run_all.py      # 等价写法

# 快速模式（跳过并发、熔断自愈等耗时项）
python tests/stress/run_all.py --quick

# 自定义结果文件与门槛
python tests/stress/run_all.py --json results.json --min-pass-rate 95
```

退出码：`0` = 通过率达标；`1` = 低于门槛（CI 据此判定失败）。

## 覆盖范围

| 文件 | 组 | 覆盖内容 |
| --- | --- | --- |
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

## CI

`.github/workflows/stress-test.yml`：

- **触发**：每天 03:00（UTC+8）定时 · 手动 `workflow_dispatch` · `plugins/scrapling_bridge.py` 或本目录变更时 · PR
- **门槛**：`--min-pass-rate 95`，低于即 job 失败
- **产物**：`results.json` / `_metrics.json` 上传为 artifact（保留 30 天）
- **摘要**：结果写入 GitHub Job Summary（总用例/通过/失败/通过率）

> 注：CI 环境无自备 Chrome，workflow 会先 `python -m playwright install chromium`；
> 若无网络或内核缺失，浏览器类用例会失败并被计入通过率 —— 这是**故意的**：
> 宁可让 CI 红，也不要假装通过。

## 本地实测基线

```
离线 42/43（1 跳过）· 100%
全部 67/67 · 100% · 74.3s
```
