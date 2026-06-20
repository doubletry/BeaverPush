"""
services/codec_registry.py -- 编码器可用性注册表
================================================

消除 stream_card.py 中的 CODEC_OPTIONS 全局可变状态。
通过单例模式管理编码器选项，提供显式的更新和监听机制。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from .log_service import logger


class CodecRegistry:
    """编码器可用性注册表（单例）。

    管理当前机器可用的编码器列表，提供：
    - get_codecs(): 获取当前可用编码器列表
    - set_available(available): 根据硬件探测结果更新可用编码器
    - register_listener(callback): 注册编码器变更回调

    使用方式::

        registry = CodecRegistry.instance()
        registry.set_available(["libx264", "h264_nvenc"])
        # 或
        registry.register_listener(lambda codecs: print(codecs))
    """

    _instance: ClassVar[CodecRegistry | None] = None

    def __init__(self) -> None:
        # 全部可能的编码器（顺序即 UI 顺序）
        self._all_codecs: list[str] = [
            "自动", "copy",
            "libx264", "libx265",
            "h264_nvenc", "hevc_nvenc",
            "h264_qsv", "hevc_qsv",
        ]
        # 当前可用的编码器（初始为全部）
        self._available_codecs: list[str] = self._all_codecs[:]
        # 变更监听器列表
        self._listeners: list[Callable[[list[str]], None]] = []

    @classmethod
    def instance(cls) -> CodecRegistry:
        """获取单例实例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None

    def get_codecs(self) -> list[str]:
        """获取当前可用的编码器列表。"""
        return self._available_codecs[:]

    def get_all_codecs(self) -> list[str]:
        """获取全部可能的编码器列表（不受硬件探测影响）。"""
        return self._all_codecs[:]

    def set_available(self, available: list[str]) -> None:
        """根据硬件探测结果更新可用编码器。

        * 始终保留 ``"自动"`` 与 ``"copy"`` 两个非编码器选项。
        * 顺序按 ``_all_codecs`` 原始顺序保留，便于下拉框稳定。

        Args:
            available: 硬件探测到的可用编码器列表。
        """
        keep = set(available) | {"自动", "copy"}
        new_codecs = [c for c in self._all_codecs if c in keep]

        if new_codecs == self._available_codecs:
            return  # 无变化，不通知

        self._available_codecs = new_codecs
        logger.info("编码器可用性已更新: {}", new_codecs)

        # 通知所有监听器
        for listener in self._listeners:
            try:
                listener(new_codecs)
            except Exception:
                logger.exception("编码器变更监听器异常")

    def register_listener(self, callback: Callable[[list[str]], None]) -> None:
        """注册编码器变更回调。

        Args:
            callback: 接收新编码器列表的回调函数。
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[list[str]], None]) -> None:
        """取消注册编码器变更回调。

        Args:
            callback: 之前注册的回调函数。
        """
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass
