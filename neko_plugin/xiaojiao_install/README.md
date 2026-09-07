# 🐱 xiaojiao_install · N.E.K.O. 猫娘「装小焦」插件

> 让**你下载的 N.E.K.O. 猫娘**帮主人**安装 / 熟悉小焦 XiaoJiao** 的官方插件。猫娘点「🛠️ 装小焦」，就会读小焦后端的**真实环境体检**，告诉主人哪些装好了、哪些缺、缺的怎么补。

---

## 它能干嘛

| 功能（插件 entry） | 说明 |
| --- | --- |
| **`env_check`**（装小焦·环境体检） | 读小焦 `http://127.0.0.1:5000/api/env`，返回完整体检：✅ 已装项 / ❌ 缺失项 + 怎么修。缺什么会如实说，缺的会影响哪个功能也会讲清。 |
| **`install_guide`**（装小焦·安装指引） | 给分步安装指引：装依赖 → 摆大脑（任意 GGUF 或 任意 OpenAI 兼容 API，不写死型号）→ 一键启动 → 使用 → 体检。 |

- **不写死模型**：小焦 `/v1` 兼容 OpenAI，模型可插拔，本插件指引同样通用。
- **真实体检**：读取的是小焦后端 `run /api/env` 的真实结果（不是瞎猜），对应小焦的**协议级依赖检测**（本地大脑看端口、云端看 `/models`，通了才算通过）。

---

## 如何安装到 N.E.K.O.

### 位置
把整个 `xiaojiao_install/` 文件夹放到 N.E.K.O. 的**市场/第三方插件目录**：

```
%LOCALAPPDATA%\N.E.K.O\plugins\xiaojiao_install\
```

> ⚠️ **别放错**：放 `resources\bin\plugin\plugins\`（app.asar 内置只读区）会**加载失败(入口点:0)**。一定要放 `%LOCALAPPDATA%\N.E.K.O\plugins\`。

### 文件
| 文件 | 作用 |
| --- | --- |
| `plugin.toml` | 插件声明（id / 版本 / entry / 作者） |
| `__init__.py` | `XiaojiaoInstallPlugin` 实现（env_check + install_guide） |

### 装好后
- 重启 N.E.K.O.，猫娘即可用 `装小焦·环境体检` / `装小焦·安装指引`。
- 前提：**小焦后端在跑**（`http://127.0.0.1:5000`），否则体检会提示"小焦后端未响应，先讲讲安装步骤"。

---

## 相关文档
- 小焦依赖检测逻辑：[docs/dependency-check.md](../docs/dependency-check.md)
- N.E.K.O. 猫娘集成小焦：[docs/neko.md](../docs/neko.md)

> 这是小焦->N.E.K.O. 的桥：猫娘帮你装小焦，小焦反过来也能学猫娘与你对话（`learn_from_neko.py`）。
