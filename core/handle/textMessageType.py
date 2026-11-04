from enum import Enum


class TextMessageType(Enum):
    """消息类型枚举"""
    HELLO = "hello"
    ABORT = "abort"
    LISTEN = "listen"
    IOT = "iot"
    MCP = "mcp"
    SERVER = "server"
    CHAT = "chat"      # UniApp 文本聊天
    IMAGE_CHAT = "image_chat"  # UniApp 图片聊天
    REPLY = "reply"    # 服务器回复
