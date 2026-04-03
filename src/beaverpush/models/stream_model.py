"""
推流状态数据模型
================

定义推流通道在生命周期中可能处于的各种状态。

状态流转::

    IDLE ──start──▶ STARTING ──ffmpeg ready──▶ STREAMING
     ▲                                           │
     │                                         stop
     │                                           ▼
     └───────────────────────────────────── STOPPING
     ▲
     │  （任何阶段出错）
     └────────── ERROR

控制器 (:class:`~beaverpush.controllers.stream_controller.StreamController`)
根据 FFmpegWorker 的信号驱动状态流转，并将当前状态反映到视图层。
"""

from enum import Enum


class StreamState(Enum):
    """推流通道状态枚举。

    Members:
        IDLE:      空闲，可以启动推流
        STARTING:  正在启动 FFmpeg 进程
        STREAMING: FFmpeg 正在正常推流
        STOPPING:  正在停止推流（等待进程退出）
        RECONNECTING: 正在等待自动重连
        ERROR:     出现错误，推流已终止
    """

    IDLE = "idle"
    STARTING = "starting"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    ERROR = "error"
