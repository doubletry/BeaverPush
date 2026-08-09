"""
controllers/_utils.py -- 控制器层共享工具函数
==============================================

提取自 StreamController 和 AppController 的重复静态方法。
"""

from __future__ import annotations


def parse_positive_int(value: str, default: int) -> int:
    """将字符串解析为正整数，失败或非正数时返回默认值。

    Args:
        value: 待解析的字符串。
        default: 解析失败或结果非正时返回的默认值。

    Returns:
        解析后的正整数或默认值。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_int(value: str, default: int) -> int:
    """将字符串解析为非负整数，失败或负数时返回默认值。

    Args:
        value: 待解析的字符串。
        default: 解析失败或结果为负时返回的默认值。

    Returns:
        解析后的非负整数或默认值。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
