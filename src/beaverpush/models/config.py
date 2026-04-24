"""
配置持久化管理模块
==================

负责应用配置的加载和保存，使用 JSON 文件存储。

配置文件位置:
    - Windows: ``%APPDATA%/BeaverPush/config.json``
    - 其他:    ``~/BeaverPush/config.json``

数据结构:
    - ``StreamConfig`` : 单路推流通道的参数（源类型、路径、编码器等）
    - ``AppConfig``    : 应用全局配置（RTSP 服务器地址 + 通道列表）

典型用法::

    cfg = load_config()          # 加载
    cfg.rtsp_server = "rtsp://..."
    cfg.add_stream(StreamConfig(name="stream1", ...))
    save_config(cfg)             # 保存
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict, fields as dataclass_fields

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BeaverPush"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class StreamConfig:
    """单路推流通道的配置参数。

    Attributes:
        name:         流名称，作为 RTSP URL 的最后一段路径
        source_type:  视频源类型 (``"video"``/``"camera"``/``"rtsp"``/``"screen"``/``"window"``/``"hikcamera"``)
        source_path:  视频文件路径 / 设备名 / RTSP URL / 屏幕偏移 / 窗口句柄 / 海康相机 SN
        rtsp_url:     完整的 RTSP 推流地址（由运行时拼接）
        loop:         是否循环播放（仅本地视频有效）
        preview:      是否启用 ffplay 预览
        video_codec:  视频编码器 (空字符串表示自动)
        width:        输出宽度（空字符串表示使用原始宽度）
        height:       输出高度
        framerate:    输出帧率
        bitrate:      输出码率 (如 ``"2M"``)
        auto_start:   是否自动开始推流（保存时记录推流状态，加载时自动恢复）
        hik_use_sdk_decode: 仅对 ``"hikcamera"`` 源类型有效；为 ``True`` 时
            使用海康 SDK 内置的 RAW→RGB 解码管线（默认），为 ``False`` 时
            回退到 OpenCV 解码路径。两条路径输出图像略有差异。
    """
    name: str = ""
    title: str = ""             # 通道标题（可由用户自定义）
    source_type: str = ""       # video / camera / rtsp / screen / window / hikcamera
    source_path: str = ""       # 文件路径 / 设备名 / RTSP URL / 屏幕索引 / hwnd / 海康相机 SN
    rtsp_url: str = ""
    loop: bool = False
    preview: bool = False
    video_codec: str = ""
    width: str = ""
    height: str = ""
    framerate: str = ""
    bitrate: str = ""
    auto_start: bool = False
    source_reconnect_interval: int = 5
    source_reconnect_max_attempts: int = 0
    hik_use_sdk_decode: bool = True


@dataclass
class AppConfig:
    """应用全局配置。

    Attributes:
        rtsp_server:     RTSP 服务器地址（如 ``"rtsp://192.168.1.100:8554"``）
        server_locked:   RTSP 服务器地址是否锁定
        username:        推流用户名（window-to-web 账户名，推流路径第一级）
        machine_name:    设备名（推流路径第二级，留空时使用主板 UUID）
        auth_secret:     认证授权码（密码或 API Key，会按当前设计明文保存在本地 config.json）
        streams:         推流通道配置列表，每个元素为 :class:`StreamConfig` 的字典形式
    """

    rtsp_server: str = ""
    server_locked: bool = False
    username: str = ""
    machine_name: str = ""
    auth_secret: str = ""
    server_reconnect_interval: int = 5
    server_reconnect_max_attempts: int = 0
    streams: list[dict] = field(default_factory=list)

    def add_stream(self, cfg: StreamConfig):
        """添加一路推流配置。"""
        self.streams.append(asdict(cfg))

    def remove_stream(self, index: int):
        """移除指定索引的推流配置。"""
        if 0 <= index < len(self.streams):
            self.streams.pop(index)


def load_stream_config(data: dict) -> StreamConfig:
    """从字典安全地构建 StreamConfig，忽略未知字段。"""
    known = {f.name for f in dataclass_fields(StreamConfig)}
    filtered = {k: v for k, v in data.items() if k in known}
    return StreamConfig(**filtered)


def load_config() -> AppConfig:
    """从文件加载配置"""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return AppConfig(
                rtsp_server=data.get("rtsp_server", ""),
                server_locked=data.get("server_locked", False),
                username=data.get("username", ""),
                machine_name=data.get(
                    "machine_name",
                    data.get("client_id", ""),  # 兼容旧配置：client_id → machine_name
                ),
                auth_secret=data.get("auth_secret", ""),
                server_reconnect_interval=int(data.get("server_reconnect_interval", 5) or 5),
                server_reconnect_max_attempts=int(
                    data.get(
                        # 兼容旧字段；由于缺少单位上下文，旧值会按原数字迁移到新字段。
                        "server_reconnect_max_attempts",
                        data.get("server_reconnect_duration", 0),
                    ) or 0
                ),
                streams=data.get("streams", []),
            )
        except Exception:
            pass
    return AppConfig()


def save_config(cfg: AppConfig):
    """保存配置到文件（原子写）。

    注意：``auth_secret`` 会按当前设计以明文写入本地 ``config.json``。

    采用「写临时文件 + ``os.replace`` 原子替换」的方式，避免在写入过程中
    进程崩溃 / 断电时把 ``config.json`` 留成半截 JSON 导致下次启动加载失败。
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(cfg), ensure_ascii=False, indent=2)
    tmp_file = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    tmp_file.write_text(payload, encoding="utf-8")
    # ``Path.replace`` 在 Windows 上也是原子的（覆盖目标文件）。
    tmp_file.replace(CONFIG_FILE)
