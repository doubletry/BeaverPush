"""beaverpush 包常量"""

import os
import sys
from pathlib import Path

from ._version import get_app_version

# 应用名称（窗口标题、托盘提示、应用名兼用）
APP_NAME = "BeaverPush - 河狸推流"
APP_VERSION = get_app_version()


def _get_assets_dir() -> Path:
    """解析 assets 目录，兼容开发模式与 Nuitka standalone 打包。"""
    # 优先检查可执行文件同级的 assets 目录（打包 / 安装后）
    exe_assets = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / "assets"
    if exe_assets.is_dir():
        return exe_assets
    # 开发模式: 从源码目录结构推导
    return Path(__file__).resolve().parent.parent.parent / "assets"


ASSETS_DIR = _get_assets_dir()

# 应用图标路径（窗口图标和托盘图标兼用）
APP_ICON_PATH = str(ASSETS_DIR / "beaver_logo.ico")
