"""autostart_service 单元测试

由于真实注册表写入仅在 Windows 上有效，这里通过 ``monkeypatch`` 替换 ``winreg``
模块，验证模块行为，确保非 Windows 平台也能跑过。
"""

from __future__ import annotations

import sys
import types

import pytest

from beaverpush.services import autostart_service


# ---------- 平台无关 ----------

def test_is_supported_matches_platform():
    assert autostart_service.is_supported() == (sys.platform == "win32")


def test_is_launched_minimized_detects_flag():
    assert autostart_service.is_launched_minimized(["app.exe"]) is False
    assert autostart_service.is_launched_minimized(["app.exe", "--minimized"]) is True


# ---------- 非 Windows 平台行为：所有写操作 no-op，返回 False ----------

class TestNonWindowsBehavior:
    @pytest.fixture
    def force_unsupported(self, monkeypatch):
        monkeypatch.setattr(autostart_service, "is_supported", lambda: False)

    def test_enable_returns_false(self, force_unsupported):
        assert autostart_service.enable() is False

    def test_disable_returns_false(self, force_unsupported):
        assert autostart_service.disable() is False

    def test_is_enabled_returns_false(self, force_unsupported):
        assert autostart_service.is_enabled() is False

    def test_get_registered_command_returns_none(self, force_unsupported):
        assert autostart_service.get_registered_command() is None


# ---------- 模拟 winreg，验证读写注册表的调用与逻辑 ----------

class FakeKey:
    def __init__(self, store: dict):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeWinreg:
    """最小化的 winreg 模拟实现，仅覆盖 service 用到的 API。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        # {(hive, subkey): {value_name: (data, vtype)}}
        self.values: dict[tuple, dict] = {}

    def OpenKey(self, hive, subkey, _reserved, _access):
        store = self.values.setdefault((hive, subkey), {})
        return FakeKey(store)

    def QueryValueEx(self, key: FakeKey, name: str):
        if name not in key._store:
            raise FileNotFoundError(name)
        return key._store[name]

    def SetValueEx(self, key: FakeKey, name: str, _reserved, vtype, data):
        key._store[name] = (data, vtype)

    def DeleteValue(self, key: FakeKey, name: str):
        if name not in key._store:
            raise FileNotFoundError(name)
        del key._store[name]


@pytest.fixture
def fake_winreg(monkeypatch):
    fake = FakeWinreg()
    module = types.ModuleType("winreg")
    # 拷贝 FakeWinreg 的属性到模块上
    for attr in ("HKEY_CURRENT_USER", "KEY_READ", "KEY_WRITE", "REG_SZ",
                 "OpenKey", "QueryValueEx", "SetValueEx", "DeleteValue"):
        setattr(module, attr, getattr(fake, attr))
    monkeypatch.setitem(sys.modules, "winreg", module)
    monkeypatch.setattr(autostart_service, "is_supported", lambda: True)
    monkeypatch.setattr(
        autostart_service, "_executable_command",
        lambda: '"C:\\Program Files\\BeaverPush\\BeaverPush.exe" --minimized'
    )
    return fake


class TestRegistryBehavior:
    def test_is_enabled_false_when_missing(self, fake_winreg):
        assert autostart_service.is_enabled() is False

    def test_enable_writes_run_value(self, fake_winreg):
        assert autostart_service.enable() is True
        store = fake_winreg.values[("HKCU", autostart_service.RUN_KEY_PATH)]
        data, vtype = store[autostart_service.RUN_VALUE_NAME]
        assert "BeaverPush.exe" in data
        assert "--minimized" in data
        assert vtype == FakeWinreg.REG_SZ

    def test_is_enabled_true_after_enable(self, fake_winreg):
        autostart_service.enable()
        assert autostart_service.is_enabled() is True

    def test_get_registered_command_returns_value(self, fake_winreg):
        autostart_service.enable()
        cmd = autostart_service.get_registered_command()
        assert cmd is not None
        assert "--minimized" in cmd

    def test_disable_removes_value(self, fake_winreg):
        autostart_service.enable()
        assert autostart_service.disable() is True
        assert autostart_service.is_enabled() is False

    def test_disable_is_idempotent(self, fake_winreg):
        # 未设置过也应当返回 True
        assert autostart_service.disable() is True

    def test_sync_true_enables(self, fake_winreg):
        autostart_service.sync(True)
        assert autostart_service.is_enabled() is True

    def test_sync_false_disables(self, fake_winreg):
        autostart_service.enable()
        autostart_service.sync(False)
        assert autostart_service.is_enabled() is False

    def test_enable_overwrites_old_path(self, fake_winreg):
        # 模拟用户卸载/换路径后注册表里的旧值
        store = fake_winreg.values.setdefault(
            ("HKCU", autostart_service.RUN_KEY_PATH), {}
        )
        store[autostart_service.RUN_VALUE_NAME] = ("C:\\old\\path.exe", FakeWinreg.REG_SZ)
        autostart_service.enable()
        new_cmd, _ = store[autostart_service.RUN_VALUE_NAME]
        assert "BeaverPush.exe" in new_cmd
        assert "old\\path.exe" not in new_cmd


# ---------- 写入失败时的容错 ----------

class TestWriteFailure:
    def test_enable_returns_false_on_oserror(self, monkeypatch):
        monkeypatch.setattr(autostart_service, "is_supported", lambda: True)

        module = types.ModuleType("winreg")
        module.HKEY_CURRENT_USER = "HKCU"
        module.KEY_READ = 1
        module.KEY_WRITE = 2
        module.REG_SZ = 1

        def _raise_open(*_a, **_kw):
            raise OSError("permission denied")

        module.OpenKey = _raise_open
        monkeypatch.setitem(sys.modules, "winreg", module)

        assert autostart_service.enable() is False
        assert autostart_service.disable() is False
        assert autostart_service.is_enabled() is False
