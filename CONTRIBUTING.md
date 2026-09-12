# 贡献指南（CONTRIBUTING）

谢谢你愿意一起把小焦做得更好 🧡 这份指南只讲**怎么改才不会被退回**，不啰嗦。

---

## 1. 五分钟上手

```powershell
git clone https://github.com/jiangchuangege/xiaojiao-harness
cd xiaojiao-harness

python -m pip install -r requirements.txt        # 宽松版本（开发用）
# 或：python -m pip install -r requirements.lock  # 与验证环境完全一致

python tests/stress/run_all.py --offline         # 先跑离线用例，2 秒出结果
python start_xiaojiao.py                         # 启动（会先问要不要一起开猫娘）
```

打开 <http://127.0.0.1:5000>。没有模型也能跑：用配置里的任意 OpenAI 兼容 API，或把小脑三件套放进项目目录自动探测。

---

## 2. 分支与提交

| 规则 | 说明 |
| --- | --- |
| 不要直接改 `main` | 开 `feat/xxx`、`fix/xxx`、`release/stabilize-YYYYMMDD` |
| 一个改动一个 commit | 便于回滚；不要把三件事塞进一个提交 |
| **commit message 用中文** | 格式：`类型(范围): 做了什么（为什么）`，例如 `fix(scrapling): 下载 404 时不再落盘错误页` |
| 不 force push | 需要修正就再提交一个（历史可读比"干净"重要）|

类型建议：`feat` / `fix` / `docs` / `refactor` / `test` / `chore`。

---

## 3. 提交前必须自检（CI 也会跑）

```powershell
# ① 语法 + 离线用例（必需）
python -m py_compile <你改的文件>
python tests/stress/run_all.py --offline

# ② 动了抓取插件 → 跑完整压力测试（含联网，约 75 秒）
python tests/stress/run_all.py --json tests/stress/results.json --min-pass-rate 95

# ③ 动了文档/原理图 → 校验 Mermaid 语法（成对引号/括号/subgraph-end）
python tools/check_mermaid.py README.md docs/*.md ARCHITECTURE.md    # 若脚本不存在则人工核对
```

**通过率 < 95% 的 PR 会被 CI 挡下**，这是故意的：宁可红，也不要"看起来能用"。

---

## 4. 硬性约束（违反=退稿）

1. **禁止硬编码**：不写死绝对路径、IP、端口、API Key。路径一律「配置 → 环境变量 → 自动探测」三级。
2. **禁止明文密钥**：任何密钥只进 `xiaojiao_control.json`（已 gitignore）。日志/报错必须走 `xiaojiao_log.scrub()`，裸凭据（`sk-…`/`ghp_…`/JWT/AWS Key）不得出现。
3. **错误信息必须中文可读**：不许把 Python 堆栈/英文库报错直接抛给使用者。
4. **不许静默吞异常**：`except Exception: pass` 一律改成「记日志 + 明确降级」（有 `tools/fix_silent_except.py` 可用；确实故意吞的写 `# noqa: silent-ok` 并说明原因）。
5. **库/插件代码用 logging，不用 print**：交互式脚本（安装向导、启动横幅）除外。
6. **新功能要带测试**：放到 `tests/stress/`，能被 `run_all.py` 收集到。
7. **安全红线**：SSRF 拦截、robots 合规、同域限速、下载不逃逸目录 —— 不许为了"抓得到"而放宽。
8. **文档与代码同步**：改了配置项/工具数量/命令，必须同步 README、`docs/*.md`、`ARCHITECTURE.md`、`CHANGELOG.md`。

---

## 5. 加一个插件（最常见的贡献）

```python
# plugins/my_tool.py
from xiaojiao_log import get_logger
log = get_logger(__name__)


class MyTool:
    def get_tool_descriptions(self):
        return [{
            "name": "my_tool",
            "description": "一句话说清它干什么（≤60 字，模型靠它选工具）",
            "parameters": {"type": "object",
                           "properties": {"text": {"type": "string", "description": "text: 输入"}},
                           "required": ["text"]},
        }]

    def execute(self, tool_name, params):
        try:
            return '{"ok": true, "content": "处理完成"}'
        except Exception as e:
            log.warning("my_tool 失败: %s", e)
            return '{"error": "处理失败：%s"}' % str(e)[:120]     # 中文可读


def get_plugin():
    return MyTool()
```

要点：
- **必须返回字符串**（上层会统一归一化；返回 dict 曾导致 500，见 CHANGELOG）
- 描述写短、参数写清 —— 4B 级模型靠它选对工具
- 想被 `/metrics` 采集，额外实现 `metrics_prometheus()` 即可

---

## 6. 加一颗大脑 / 换一个小脑

- **大脑**：`xiaojiao_control.json → brain.api`（任意 OpenAI 兼容端点）或 `brain.llama.gguf`（本地）。不需要改代码。
- **小脑**：三件套 `*.pth` + `vocab*.pkl` + `model_config.json`，路径填 `brain.xiaojiao`；留空则自动探测。

---

## 7. 报 Bug / 提需求

请带上：**现象、复现步骤、期望结果、实际结果、日志片段**（`logs/xiaojiao.log`，注意先自查有没有密钥）。

- 🐛 [Bug Issue](.github/ISSUE_TEMPLATE/bug_report.yml)
- ✨ [Feature Issue](.github/ISSUE_TEMPLATE/feature_request.yml)
- 🔀 [Pull Request 模板](.github/PULL_REQUEST_TEMPLATE.md)

---

## 8. 行为准则

参与本项目即表示你同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。我们对新人友好，问题没有"太基础"这回事。

---

## 9. 免责声明

本项目为个人本地 AI 助手框架。抓取能力**仅用于公开可访问内容**；
请遵守目标站点条款与当地法律，**不得**用于绕过付费墙、破解版权或任何违法用途，后果由使用者自负。
