"""
Per-domain callback dispatchers for Telegram inline keyboard queries.
"""

from .nav import handle_nav_callback, NAV_DISPATCH_TABLE
from .cafe import handle_cafe_callback, CAFE_DISPATCH_TABLE

__all__ = [
    "handle_nav_callback",
    "NAV_DISPATCH_TABLE",
    "handle_cafe_callback",
    "CAFE_DISPATCH_TABLE",
]
