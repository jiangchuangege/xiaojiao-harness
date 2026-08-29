"""
天气插件 - 示例
"""

import requests


class WeatherPlugin:
    """天气查询插件"""
    
    def get_tool_descriptions(self):
        return [
            {
                "name": "get_weather",
                "description": "查询城市天气",
                "parameters": {"city": "城市名称"}
            }
        ]
    
    def execute(self, tool_name, params):
        if tool_name != "get_weather":
            return None
        city = params.get("city", "北京")
        try:
            url = f"https://wttr.in/{city}?format=%C+%t"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                return f"{city}天气：{resp.text.strip()}"
            return "天气查询失败"
        except:
            return "天气服务暂时不可用"