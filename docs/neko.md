# 🐱 N.E.K.O. 猫娘集成 · 桌面猫娘伙伴

> **一句话**：小焦的桌面伙伴是 **N.E.K.O. 猫娘**——它**不是小焦自带的**，而是**独立开源项目 N.E.K.O.**（你本地部署）。小焦把它**集成**起来：启动时**先问你要不要一起拉起猫娘**（答 `y` 才拉起 `main_server` + `memory_server`，答 `n` 或非交互式则**不拉、也不影响小焦**），并后台**学习你与猫娘的对话**，让猫娘懂你的项目、小焦懂猫娘的说话方式。这是"**N.E.K.O. 的成熟 Live2D 猫娘壳 + 小焦本地内核**"。

---

## 1. 为什么对接 N.E.K.O.

> ⚠️ **先说明**：猫娘**不是小焦做的、也不是小焦自带的**——它是独立开源项目 **N.E.K.O.**（GitHub 上可下载，用户自己部署在本地，含 `launcher.py`）。小焦做的是**集成**：自动拉起它、学习它的记忆、给它装"小焦"插件。换句话说，**猫娘是别人研发的，小焦负责把它和自己无缝接起来**。

小焦选择**对接** N.E.K.O. 猫娘，因为它：

- 是**成熟、开箱即用**的 Live2D 猫娘应用（形象/动效/语音界面齐全）；
- 自带**记忆与人格**系统（`memory/`、`persona.json`），天然适合"记住主人"；
- 有**插件系统**，可以给它装"小焦安装助手"这类插件，把 N.E.K.O. 和本地小焦串起来；
- 目标是 **"N.E.K.O. 猫娘的形象壳 + 小焦的本地大脑/工具"**——猫娘负责陪伴，小焦负责干活。

> 小焦自己的 Electron 旧宠物已移除（不再维护）。桌面形象完全使用 **N.E.K.O. 猫娘**。

---

## 2. 一键启动融合

`python start_xiaojiao.py` 的 `main()` 现在这样做：

1. **llama-swap(9292)**：多大脑热切换管理器。
2. **聊天大脑**：由 llama-swap 托管（避免再起 8080 冗余直连）。
3. **Web(5000)**：小焦网页 / `/v1`。
4. **N.E.K.O. 猫娘**（`start_neko()`，你的是 **Steam 桌面客户端**）：
   - 拉起**桌面客户端 `N.E.K.O.exe`**（你看到的猫娘界面），它连带拉起后端；
   - 后端 `main_server(:48911)` + `memory_server(:48912)`（这两个是**后端端口**，不是网页入口）；
   - 后台起 **`learn_from_neko.py --daemon --interval 300`**（每 5 分钟学你与猫娘的对话）。
   > ⚠️ **主入口是桌面客户端 `N.E.K.O.exe`**，不是浏览器里的 `http://127.0.0.1:48911`（那只是后端服务端口）。
5. **打开小焦 Web** `http://127.0.0.1:5000`。

运行 `/api/env` 会检测 N.E.K.O. 各服务是否在线（48911/48912 等）。

### 端口一览

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| **5000** | 小焦 Web | 聊天 / `/v1` / 工具 / 记忆 |
| **9292** | llama-swap | 多大脑热切换 |
| **48911** | N.E.K.O. main_server | 猫娘**后端**服务（非网页入口，界面在桌面客户端 `N.E.K.O.exe`） |
| **48912** | N.E.K.O. memory_server | 猫娘记忆 |
| **48915** | N.E.K.O. agent flags | 猫娘 agent 开关 |

---

## 3. 猫娘记忆目录（哪里学的）

N.E.K.O. 的记忆在 `%LOCALAPPDATA%\N.E.K.O\memory\YUI\`：

- **`facts.json`**：关于主人的事实/偏好（`entity=master/user/self`，含"主人/碳基生物"关键词）。
- **`persona.json`**：猫娘说话风格（persona）。

`learn_from_neko.py` 会把这两份内容**学进小焦的记忆库** `xiaojiao_knowledge_memory.json`：

| 来源 | 学到哪 | 键 |
| --- | --- | --- |
| cats `facts.json` 中关于主人的事实 | 小焦记忆库 | `学会:...` |
| cats `persona.json` 说话风格 | 小焦记忆库 | `猫娘说话风格` |

这样 **小焦知道猫娘从你这里学到了什么**，猫娘（通过插件/喂知识）也知道小焦这个项目。

---

## 4. N.E.K.O. 插件系统（给小焦装"安装助手"）

N.E.K.O. 支持**插件**，插件分两类目录：

| 目录 | 类型 | 说明 |
| --- | --- | --- |
| `%LOCALAPPDATA%\N.E.K.O\plugins` | **市场/第三方插件** | ✅ 真正加载的地方 | 
| `bin\plugin\plugins` | 应用内置（app.asar 内） | ❌ 只读，放这里会**加载失败(入口点:0)** |

小焦的插件 `xiaojiao_install`（`xiaoJiao 安装助手`）放在 **`%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\`**：

- `plugin.toml`：`id = xiaojiao_install`，`entry = plugin.plugins.xiaojiao_install:XiaojiaoInstallPlugin`，`version = 1.0.0`。
- `__init__.py`：实现 `XiaojiaoInstallPlugin`：
  - `env_check` → 读小焦的 `XIAOJIAO_BASE/api/env`，返回一条完整安装体检；
  - `install_guide` → 返回 5 步安装指引。

> ℹ️ **仓库里有现成插件**：`neko_plugin/xiaojiao_install/`（含 `plugin.toml` + `__init__.py` + `README.md`）。clone 小焦仓库后，把整个 `xiaojiao_install/` 复制到 `%LOCALAPPDATA%\N.E.K.O\plugins\` 即可装到猫娘。

> ⚠️ **踩坑**：别把插件放进 `resources\bin\plugin\plugins\`（app.asar 内置只读区），否则加载报"入口点:0"。放 `%LOCALAPPDATA%\N.E.K.O\plugins\` 才行。

---

## 5. 猫娘说话风格

小焦可以把 N.E.K.O. 的 lanlan_prompt 人格改成"小焦"，让猫娘**懂小焦的说话方式**。反过来，小焦也能**学习猫娘的说话风格**（`猫娘说话风格` 键）。参考模板见 [xiaojiao-catgirl-style.md](xiaojiao-catgirl-style.md)。

---

## 6. 配置

| 项 | 说明 |
| --- | --- |
| `XIAOJIAO_NEKO_DIR` | 指向 N.E.K.O. 项目根目录（含 `N.E.K.O.exe`（Steam 版）或 `launcher.py`（源码版）） |
| `XIAOJIAO_BASE` | 小焦后端根目录（供 N.E.K.O. 插件调 `/api/env` 体检） |
| Steam 版候选 | `G:\SteamLibrary\steamapps\common\n.e.k.o`（`N.E.K.O.exe` 拉起 48911/48912） |
| 源码版候选 | `G:\moxing__xiaojiao\maoniang\N.E.K.O-main`、`G:\模型文件\猫娘\N.E.K.O-main`、`C:\NEKO\N.E.K.O-main` |

---

## 7. 相关文件

| 文件 | 作用 |
| --- | --- |
| `start_xiaojiao.py` | `start_neko()`：拉桌面客户端 `N.E.K.O.exe` + 后台学习 |
| `learn_from_neko.py` | 读猫娘 `facts.json`/`persona.json` → 写进小焦记忆库（可独立跑，也支持 `--daemon`） |
| `%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\` | N.E.K.O. 小焦安装助手插件 |
| `docs/xiaojiao-catgirl-style.md` | 猫娘说话风格模板 |
