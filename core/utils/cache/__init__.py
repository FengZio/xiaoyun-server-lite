"""
缓存模块
"""

from .manager import cache_manager, GlobalCacheManager
from .config import CacheType, CacheConfig
from .strategies import CacheStrategy, CacheEntry

__all__ = [
    "cache_manager",
    "GlobalCacheManager",
    "CacheType",
    "CacheConfig",
    "CacheStrategy",
    "CacheEntry",
]
