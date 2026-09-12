# 接入资产测绘数据源（IP ↔ CVE 对应表）

**为什么需要这一步**：NVD 只发布「CVE → 受影响软件/版本（CPE）」，**从不上报任何公网 IP**。
所以"网络上所有含这些漏洞的 IP 地址并列表对应上"这类**资产测绘**诉求，NVD 永远答不了 ——
得由"全网扫描数据源"来回答。小焦把这件事做成了插件 `plugins/asset_intel.py`，两个方向分开处理：

| 方向 | 工具 | 需要 Key 吗 | 现在就能用吗 |
| --- | --- | --- | --- |
| **给 IP → 看它命中哪些 CVE** | `asset_intel_lookup` | ❌ 不需要（Shodan InternetDB 免费接口） | ✅ 开箱即用 |
| **给 CVE/关键词 → 查哪些 IP 受影响** | `asset_intel_search` | ✅ 需要一家数据源的 Key | 配完 Key 即用 |

问小焦"**资产测绘状态**"就能打印下面这张表：

| 数据源 | 状态 | 能干什么 |
| --- | --- | --- |
| Shodan InternetDB | ✅ 可用（免费，无需 Key） | IP → 该地址命中的 CVE / 开放端口 / 主机名 |
| ZoomEye | 看有没有配 Key | CVE/关键词 → 受影响 IP |
| Shodan 搜索 | 看有没有配 Key | CVE/关键词 → 受影响 IP（`vuln:` 过滤要会员） |
| Fofa | 看有没有配 Key | CVE/关键词 → 受影响 IP |

## 三步接入（反向查询才需要）

1. **拿一个 Key**（任选一家，都有免费额度）：
   - Shodan：<https://account.shodan.io/>（注册后在账号页看到 API Key）
   - ZoomEye：<https://www.zoomeye.org/profile>
   - Fofa：<https://fofa.info/personalData>
2. **填进去**（两种任选）：
   - 环境变量：`SHODAN_API_KEY` 或 `ZOOMEYE_API_KEY`，或 `FOFA_EMAIL` + `FOFA_KEY`
   - 或写文件 `plugins/asset_intel_keys.json`（已在 `.gitignore` 里，不会进仓库）：
     ```json
     {"shodan": "你的Key", "zoomeye": "你的Key", "fofa_email": "you@example.com", "fofa_key": "你的Key"}
     ```
3. **重启小焦**，然后对它说「**资产测绘状态**」——配好的数据源会显示 ✅。

## 用起来长什么样

```text
你：帮我查一下 1.1.1.1、8.8.8.8 这些地址命中了哪些漏洞
小焦：🛰️ 资产测绘 · IP → 漏洞（数据源：Shodan InternetDB，免费无需 Key）
      | IP | 主机名 | 开放端口 | 命中的 CVE |
      | 1.1.1.1 | one.one.one.one | 53, 80, 443, … | 无 |

你：vuln:CVE-2024-1234 这个漏洞，网段里有哪些 IP 中招？（配了 Key 之后）
小焦：🛰️ 资产测绘 · CVE/关键词 → IP（数据源：ZoomEye，共 1234 条）
      | IP | 国家 | 端口 | 应用 | 命中的 CVE |
```

## 纪律（跟抓取插件一致）

- 只查**公网 IP**：`10./127./192.168./172.16-31./169.254.` 一律拒绝并说明原因（"资产测绘"扫自己家网段没有意义）。
- 单个 IP 查不到（HTTP 404）如实写「Shodan 没有该地址的数据」，**不编造**。
- Key 只从环境变量或本地 Key 文件读，**绝不写进代码、不进日志、不进仓库**。
- 任何异常都转成中文可读说明返回，不把堆栈丢给用户。

## 想换/加数据源

插件契约（`plugins/asset_intel.py` 里照抄即可）：

```python
class MyProvider:
    def get_tool_descriptions(self):   # 声明工具（name/description/parameters）
        ...
    def execute(self, tool_name, params):   # 执行并返回**字符串**（Markdown 最好看）
        ...
```

丢进 `plugins/` 重启即被加载，小焦的工具表里就有了（`/api/settings` 能看到）。
