# 小焦 · 插件开发指南

给小焦写一个**插件** = 往 `plugins/` 目录放一个 `.py` 文件。它会**自动注册**成小焦可调用的工具，模型自己会决定何时调用它。简单、无需改主程序。

## 一、写在哪
放这里：`C:\xiaojiao\xiaojiao harness\plugins\你的插件名.py`

## 二、插件长什么样
每个插件是一个**类**，提供两个东西：
1. `get_tool_descriptions()` —— 返回工具的"名字/说明/参数"，告诉小焦这个工具能干嘛。
2. `execute(name, params)` —— 真正执行，返回结果给用户。

最小示例（时间助手）：
```python
# plugins/my_time.py
import datetime

class MyTimePlugin:
    """时间助手插件"""
    def get_tool_descriptions(self):
        return [
            {"name": "get_time",                 # 工具名（小焦会调这个）
             "description": "获取当前日期和时间",
             "parameters": {"type": "object", "properties": {}}}   # 无参数
        ]

    def execute(self, name, params):
        if name == "get_time":
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return None
```

带参数的示例（天气）：
```python
# plugins/my_weather.py
import requests
class MyWeatherPlugin:
    def get_tool_descriptions(self):
        return [
            {"name": "get_weather",
             "description": "查询城市天气",
             "parameters": {"type": "object",
                            "properties": {"city": {"type": "string", "description": "城市名"}},
                            "required": ["city"]}}
        ]
    def execute(self, name, params):
        if name == "get_weather":
            city = params.get("city", "北京")
            r = requests.get(f"https://wttr.in/{city}?format=%C+%t", timeout=8)
            return f"{city}天气：{r.text.strip()}"
        return None
```

## 三、怎么生效
1. 把 `.py` 放进 `plugins/`。
2. **重启小焦**（`python start_xiaojiao.py`）。
3. 插件自动注册：设置页「插件」里能看到并开关；模型也能调用。

> 你的插件放在 `plugins/` 后，小焦启动会**自动扫描**，遇到含 `get_tool_descriptions` + `execute` 的类就注册。想让它"必须用"/"默认开"，在 `xiaojiao_control.json` 的 `capabilities.plugins` 里把该插件名设为 `true`。

## 四、`execute` 的返回约定
- 返回 `string`：直接展示给用户/给模型继续推理。
- 返回 `None`：表示"这个工具不用/没做"，模型会继续。

## 五、检查插件被识别
```powershell
# 列出当前已注册的插件
python -c "import xiaojiao_app as x; print(list(x.PLUGINS.keys()))"
```
会打印如 `['web-search','memory','search','weather','my_time','my_weather']`。

## 六、注意事项
- 插件名 = 文件名（不含 `.py`）。
- 每个工具名（`get_weather` 等）要**唯一**，别和内置的 `run_command / write_file / open_app / list_files / read_file` 重名。
- `execute` 里别做太耗时的操作；异常会被包成 `插件异常：…` 返回。
- 想联网/访问文件，直接用 `requests` / `os` / `datetime` 等标准库。

> 危险：插件 `execute` 能做的事就是你的代码能做的事。写安全、自己可信的插件。
