"""应用版本解析工具。"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

_FALLBACK_VERSION = "0.1.0"


def _get_assets_dir() -> Path:
    """解析 assets 目录，兼容开发模式与打包产物。"""
    candidates = [
        Path(os.path.dirname(os.path.abspath(sys.executable))) / "assets",
        Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / "assets",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[2] / "assets"


def _read_bundled_version() -> str:
    version_file = _get_assets_dir() / "version.txt"
    if not version_file.is_file():
        return ""
    return version_file.read_text(encoding="utf-8-sig").strip()


def _read_pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    version = project.get("version", "")
    return version.strip() if isinstance(version, str) else ""


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """获取当前应用版本。"""
    env_version = os.getenv("BEAVERPUSH_VERSION", "").strip()
    if env_version:
        return env_version.removeprefix("v")

    bundled_version = _read_bundled_version()
    if bundled_version:
        return bundled_version

    try:
        return importlib.metadata.version("beaverpush")
    except importlib.metadata.PackageNotFoundError:
        pass

    pyproject_version = _read_pyproject_version()
    if pyproject_version:
        return pyproject_version

    return _FALLBACK_VERSION
