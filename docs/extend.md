# 给小焦加玩法 / 加工具

小焦最值钱的一点是：**它的能力能随便加**。加一个 `.py`，它就多一个工具。下面把各种玩法都列出来，挑你喜欢的抄。

---

## 1. 最常用：写插件

小焦启动时会扫 `plugins/` 目录，里面凡是带 `get_tool_descriptions` 和 `execute` 的类，都会自动注册成它能调用的工具。

```python
# plugins/my_time.py
import datetime

class MyTimePlugin:
    def get_tool_descriptions(self):
        return [{
            "name": "get_time",
            "description": "看下现在几点",
            "parameters": {"type": "object", "properties": {}}
        }]

    def execute(self, name, params):
        if name == "get_time":
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return None
```

放进去 → 重启 → 小焦就能用 `get_time` 了。**就这么简单。**

---

## 2. 带参数的工具

工具可以要参数，小焦会看 `get_tool_descriptions` 里的 `parameters` 告诉你它能传什么。

```python
# plugins/my_weather.py
import requests

class MyWeatherPlugin:
    def get_tool_descriptions(self):
        return [{
            "name": "get_weather",
            "description": "查某个城市的天气",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string", "description": "城市名，如 北京"}},
                           "required": ["city"]}
        }]
    def execute(self, name, params):
        if name == "get_weather":
            city = params.get("city", "北京")
            data = (requests.get(f"https://wttr.in/{city}?format=%C+%t", timeout=8).text or "")
            return f"{city}天气：{data.strip()}"
        return None
```

---

## 3. 工具型 vs 数据型

- **工具型**：让模型"动一下"，比如查天气、算账、查 IP、做翻译。
- **数据型**：给模型"多一份知识"或"多一条渠道"，比如读一个配置文件、查一个数据库、拉一个接口。

写法一样，都在 `execute` 里决定。

---

## 4. 一堆现成的插件点子（照抄就改一下）

| 插件名 | 干嘛 | execute 里写 |
| --- | --- | --- |
| 计算器 | 算数学 | `eval` / `sympy` |
| 翻译 | 中英互译 | 调 google/tatoeba 免费接口 |
| 查 IP | 公网 IP + 归属 | `requests.get('https://api.ipify.org')` |
| 二维码 | 生成二维码图片 | `pip install qrcode` 然后存成 png |
| 汇率 | 外币换算 | 调汇率免费 API |
| 备忘录 | 记一条到本地文件 | 读写 json/txt |
| 定时提醒 | 到点提醒你 | `threading.Timer`（注意别太频繁）|
| 读系统信息 | CPU/内存/磁盘 | `psutil` |
| 生成图片 | 用模型/接口画图 | 调你的图像接口返回 url |
| 网页摘要 | 抓一个网页摘要 | `requests` + `BeautifulSoup` |

> 写的时候**记得 import**（`requests`/`json`/`datetime` 等），别用没装的库；`execute` 里别写太慢的活，异常会被包成 `插件异常：…`。

---

## 5. 怎么生效 / 调试

1. 把 `.py` 放进 `plugins/`。
2. 重启小焦（`python start_xiaojiao.py`）。
3. 看是不是被识别：
   ```powershell
   python -c "import xiaojiao_app as x; print(list(x.PLUGINS.keys()))"
   ```
4. 在设置 → 插件里也能看到并开关。

> 注意：插件名 = 文件名；工具名（如 `get_weather`）别和内置的 `run_command / write_file / open_app / list_files / read_file` 重名。

---

## 6. 改人设 / 改行为（不算插件，但同样能"加玩法"）

- **改人设**：`xiaojiao_control.json` 的 `role`。写清楚"你是谁、怎么回、要不要用工具、用哪个文件名"。
- **改参数**：`behavior.temperature`（温度，越高越浪）、`max_tokens`（回答多长）、`brain.llama.ctx`（上下文窗口多大）。

---

## 7. 加模型

设置 → 模型管理 → 添加。填 `名字 / 类型(api·llama·xiaojiao) / BaseURL / API Key / 模型名`，加完它出现在顶部下拉里，可切换。**人格和工具是壳的，换底座不影响。**

---

## 8. 更高级的玩法思路

下面这几个更进阶一点，我一条条说清楚怎么做，你照着思路走就行。

- **套在别的服务里（把小焦当"外挂大脑"）**
  小焦的 `/v1` 是 OpenAI 兼容的。任何支持自定义 API 地址的 Agent 框架、工作流、机器人，只要把它配置里的"模型地址 / base_url"填成小焦的 `/v1`，你的框架就能用小焦来推理。框架负责调度和流程，小焦负责"会说话、会搞事"的那部分。你在那边的工具里找到模型配置，填 `http://127.0.0.1:5000/v1` 就好。

- **多工具串起来（让小焦自己编排）**
  你写几个各干各的插件（一个查天气、一个记备忘录、一个查时间）。不用你告诉它"先查再记"——把小焦人设里说清楚目标（比如"帮我根据天气决定要不要提醒"），它自己会先调天气的，看完结果再决定要不要调备忘录的。你只要把每个插件写好，把"要达成什么"写进人设就行。

- **接外部 API（让小焦能用你们的系统）**
  把你或公司已有的 HTTP 接口包成插件。插件的 `execute` 里，去请求那个接口，把返回结果整理成一句人话返回给小焦。这样小焦就等于多了一条"触达你们系统"的路。步骤：看你们接口文档 → 在插件的执行逻辑里发请求 → 把返回转成文字交回去。

- **加记忆分支（让小焦记住你想要的）**
  小焦本来就会把学到的东西写进记忆。想加自己的持久化（比如记住你的偏好、标签、某类数据），就在插件里读写自己的文件，或改一改记忆读写那块。让它长期记住"你喜欢什么、你常干什么"。

- **当命令行用（脚本里直接调它干活）**
  `xiaojiao_tools.py`(5003) 提供 `/api/run`。你在自己的脚本里直接 POST 过去，就能让小焦跑命令、写文件、读文件，不用开网页。适合做自动化：脚本遇到要建文件/跑命令的活，甩给小焦。

---

**一句话**：小焦的"工具"就是 `plugins/` 里的一个类。你想要什么能力，照着写一个，重启就能用。想加多少加多少。
