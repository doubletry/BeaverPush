"""
services/rtsp_url.py -- RTSP URL 构建与规范化
==============================================

从 ffmpeg_service.py 提取的 RTSP URL 相关纯函数。
负责 URL 构建、认证、规范化、脱敏。
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse, urlunparse

# RTSP URL 中的密码 / 授权码部分；用于把命令行中带凭据的 RTSP URL
# 脱敏后再写入日志，避免 ``%APPDATA%/BeaverPush/logs/*.log`` 留下明文密码。
_RTSP_CRED_RE = re.compile(r"(rtsp://[^:/@\s]+:)([^@\s]+)(@)", re.IGNORECASE)


def mask_sensitive_cmd(cmd: list[str]) -> str:
    """返回 ffmpeg 命令行的可读字符串，并把 RTSP URL 中的密码替换为 ``***``。

    仅用于日志输出；不会影响实际执行的命令列表。
    """
    masked = [_RTSP_CRED_RE.sub(r"\1***\3", arg) for arg in cmd]
    return " ".join(masked)


def normalize_rtsp_server(rtsp_server: str) -> str:
    """规范化 RTSP 服务器地址并校验基本格式。"""
    normalized = rtsp_server.strip()
    if "://" not in normalized:
        normalized = f"rtsp://{normalized}"

    parsed = urlparse(normalized)
    if (
        parsed.scheme != "rtsp"
        or not parsed.hostname
        # v2 所有权模型会自行拼接 /{username}/{machine}/{channel}，因此这里不接受额外基础路径。
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("RTSP 服务器地址格式不正确，应为 rtsp://host[:port]")
    return normalized


def _format_rtsp_netloc(hostname: str, port: int | None) -> str:
    """格式化 RTSP URL 的 netloc，并为 IPv6 主机补上方括号。"""
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{host}:{port}" if port else host


def build_authenticated_rtsp_url(
    rtsp_server: str,
    path_segments: list[str],
    username: str = "",
    auth_secret: str = "",
    *,
    mask_auth_secret: bool = False,
) -> str:
    """构建带认证信息的 RTSP URL。"""
    parsed = urlparse(normalize_rtsp_server(rtsp_server))
    netloc = _format_rtsp_netloc(parsed.hostname or "", parsed.port)
    if username and auth_secret:
        encoded_username = quote(username, safe="")
        encoded_secret = "***" if mask_auth_secret else quote(auth_secret, safe="")
        netloc = f"{encoded_username}:{encoded_secret}@{netloc}"

    # 第一级用户名仅保留 _ -；后续设备名/流名称保留 . _ -，与当前 UI/帮助文档规则一致。
    path = "/" + "/".join(
        quote(segment, safe="_-" if index == 0 else "._-")
        for index, segment in enumerate(path_segments)
    )
    return urlunparse((parsed.scheme, netloc, path, "", "", ""))
