"""
服务层 (Service)
================

封装底层系统操作和外部工具调用，供 Controller 使用。

模块列表:
    - device_service  : 设备枚举（摄像头、屏幕、窗口）
    - ffmpeg_service  : FFmpeg 推流进程管理和命令构建
    - window_capture  : Win32 API 窗口画面捕获（PrintWindow + BitBlt）
"""
