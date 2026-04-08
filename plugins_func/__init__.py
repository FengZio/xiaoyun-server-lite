"""
plugins_func 精简版
只包含必要的类型定义和注册机制，移除所有 IOT 设备相关功能
"""

from .register import Action, ActionResponse, ToolType, FunctionItem, register_function, all_function_registry
from .loadplugins import auto_import_modules

__all__ = [
    'Action',
    'ActionResponse',
    'ToolType',
    'FunctionItem',
    'register_function',
    'all_function_registry',
    'auto_import_modules',
]
