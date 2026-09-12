"""
搜索插件 - 示例
"""

import webbrowser
from urllib.parse import quote_plus


class SearchPlugin:
    """搜索插件"""
    
    def get_tool_descriptions(self):
        return [
            {"name": "web_search", "description": "上网搜资料(百度)。什么时候用：要查新闻/百科/天气/任何外部**信息**；输入 query(只写内容关键词，别把「帮我搜」这类功能字放进去)；输出 搜索结果列表", "parameters": {"query": "搜索关键词"}}
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