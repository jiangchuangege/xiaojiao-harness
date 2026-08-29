"""
搜索插件 - 示例
"""

import webbrowser
from urllib.parse import quote_plus


class SearchPlugin:
    """搜索插件"""
    
    def get_tool_descriptions(self):
        return [
            {"name": "web_search", "description": "在百度搜索信息", "parameters": {"query": "搜索关键词"}}
        ]
    
    def execute(self, tool_name, params):
        if tool_name != "web_search":
            return None
        query = params.get("query", "")
        if not query:
            return "请告诉我你想搜索什么"
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}"
        webbrowser.open(url)
        return f"已打开百度搜索：{query}"