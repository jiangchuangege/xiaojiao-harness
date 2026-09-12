# 小焦 v1.2.0 · 稳定化落地版

> **一句话**：这一版不加花哨功能，只做一件事 —— 让小焦**能长时间稳定地跑**：
> 看得见（指标 / 日志）、收得回（会话自动回收）、跑得快（批量并发）、验得了（压力测试 + CI）。

**升级建议：可直接升级，无破坏性变更**（配置项只增不改，旧配置继续有效）。

---

## ✨ 新增

### 📊 看得见：指标与统一日志
- `GET /metrics` —— Prometheus 文本格式，可直接被监控系统抓取
- `GET /api/scrapling/metrics` —— JSON 视图（含活跃会话明细、熔断状态）
- 每次工具调用自动记录：`calls / success / fail / total_latency / avg_latency / max_latency / circuit_breaks / last_error`
- 统一日志 `xiaojiao_log.py`：`logs/xiaojiao.log`（5MB×3 轮转），**写日志前全链路脱敏**（`sk-…` / `ghp_…` / `AKIA…` / JWT → `***`）

### 🧹 收得回：会话自动回收（SessionManager）
`open_session` 每开一次就真起一个浏览器，忘了关会一直占内存。现在三条规则任一命中**自动回收并真关闭**：
- **TTL**（`session_ttl`，默认 30 分钟）
- **空闲**（`session_idle`，默认 5 分钟）
- **上限**（`max_sessions`，默认 20，超出踢最久未用 LRU）

后台每 60 秒巡检；配置写错（0 / 负数 / 非数字）**回退默认并中文告警**。

### ⚡ 跑得快：批量并发可配置（BatchConfig）
| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `batch.concurrency` | 3 | **跨域**同时抓几个 |
| `batch.per_domain_limit` | 1 | **同域**同时最多几个（默认串行，不做恶邻居）|
| `batch.rate_limit` / `max_retries` / `backoff_base` | 1.0 / 3 / 1.0 | 同域间隔、重试、退避 |

实测 3 个不同域名：**6.20s → 0.92s（提速 85%）**；同域仍然串行。配置非法会**明确报中文错误**，不静默忽略。

### 🧪 验得了：压力测试 + CI
- `tests/stress/`：离线 + 联网两套真实调用用例（**67 项**），结果机读 `results.json`
- `.github/workflows/stress-test.yml`：每天 03:00 定时 / 手动 / 插件或测试变更触发；**通过率 < 95% 直接失败**；产物上传 artifact
- 本地实测：**67/67 通过 · 100% · 74 秒**（含并发、熔断自愈、SSRF、会话回收）

### 📐 文档
- **`ARCHITECTURE.md`**：模块职责、请求生命周期时序图、插件契约、扩展点、已知限制（5 张原理图）
- **`CONTRIBUTING.md`**：分支/提交规范 + 8 条硬性约束 + 插件模板
- **`docs/release-and-rollback.md`**：五步发版 + 6 种回滚场景 + git 被墙时的 API 发布兜底
- **`tools/check_mermaid.py`**：图语法自检（已接入 CI），全仓 **39 个图 0 问题**

---

## 🐞 修复

- **静默吞异常**：审计发现 `except: pass` **103 处**、裸 `except:` 19 处 —— 出错时无声无息。已改为「记日志 + 明确降级」，裸 `except` 加上 `Exception` 限定（不再吞掉 `Ctrl+C`）。
- **`sanitize()` 漏抹裸凭据**：原来只认 `key=value`，`sk-xxx` 这种会原样进日志/指标。已补齐常见凭据形态。
- **Prometheus 无标签指标格式错误**（输出成 `metric{}`）。
- **抓取 JSON 糊成一坨**：Markdown 转义会让响应变成非法 JSON，导致美化与代码块全部失效。现在自动修复转义 + 美化缩进 + 超长折叠。
- **`except: pass` 之外**：删除插件加载器里永远不可达的重复分支（死代码）。

---

## 🔧 变更

- 依赖清单修正：移除已下线的 `deepseek-harness`；修正 `chatterbox-tts` 版本约束（原 `>=0.8` 会导致 `pip install -r requirements.txt` **必然失败**）。
- 新增 `requirements.lock`（37 个包精确锁定）与 `xiaojiao_control.json.example`（脱敏配置模板）。
- 仓库卫生：日志归档进 `logs/`、`.gitignore` 加固（含显式忽略项目内字面量 `~` 目录）。

---

## ⚠️ 已知问题

1. **浏览器内核**：默认依赖自备 Chrome；没有的话执行 `python -m scrapling install`（CI 已自动安装）。
2. **会话表脏数据**：若历史上用过非法 `session_type`，需重启一次小焦清空（新版本已加前置校验，不再产生）。
3. **24 小时长跑**：目前以「连续调用 + 堆增长 + 熔断自愈」抽样替代，长跑需独立环境。
4. **指标覆盖范围**：目前为抓取插件维度；全进程 CPU/内存侧写尚未纳入。

---

## 🚀 升级步骤

```powershell
git pull
python -m pip install -r requirements.txt      # 或 -r requirements.lock 保持一致
python tests/stress/run_all.py --offline       # 30 秒自检
python start_xiaojiao.py                       # 启动（会问是否一起开猫娘）
```

**新增配置（可选，不填就用默认）**：

```json
"scrapling": {
  "max_sessions": 20, "session_ttl": 1800, "session_idle": 300,
  "batch": { "concurrency": 3, "per_domain_limit": 1, "rate_limit": 1.0, "max_retries": 3, "backoff_base": 1.0 }
}
```

---

## 🙏 说明

- **无破坏性变更**：旧配置、旧插件、旧记忆数据继续可用。
- 完整改动见 [CHANGELOG.md](https://github.com/jiangchuangege/xiaojiao-harness/blob/main/CHANGELOG.md)。
- ⚠️ **免责声明**：抓取能力仅用于**公开可访问**内容；请遵守目标站点条款与当地法律，不得用于绕过付费墙、破解版权或任何违法用途，后果由使用者自负。
