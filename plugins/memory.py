"""
记忆插件 - 示例
"""

import os
import datetime


class MemoryPlugin:
    """记忆管理插件"""
    
    def get_tool_descriptions(self):
        return [
            {"name": "save_memory", "description": "把用户明确要求记住的事存进长期记忆。什么时候用：用户说「记住…」「以后都…」；输入 content；输出 保存结果", "parameters": {"content": "要记住的内容"}},
            {"name": "read_memory", "description": "读回所有已保存的记忆。什么时候用：用户问「你记得什么」或需要核对偏好；无参数；输出 记忆列表", "parameters": {}}
        ]
    
    def execute(self, tool_name, params):
        if tool_name == "save_memory":
            content = params.get("content", "")
            with open("xiaojiao_memory.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now()} - {content}\n")
            return f"已记住：{content}"
        
        if tool_name == "read_memory":
            if not os.path.exists("xiaojiao_memory.txt"):
                return "还没有任何记忆"
            with open("xiaojiao_memory.txt", "r", encoding="utf-8") as f:
                return f.read()
        
        return None