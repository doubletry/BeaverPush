"""config 模块单元测试"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from beaverpush.models.config import (
    AppConfig, StreamConfig, load_config, save_config, load_stream_config,
)


class TestStreamConfig:
    def test_defaults(self):
        cfg = StreamConfig()
        assert cfg.name == ""
        assert cfg.title == ""
        assert cfg.source_type == ""
        assert cfg.source_path == ""
        assert cfg.loop is False
        assert cfg.preview is False
        assert cfg.video_codec == ""
        assert cfg.width == ""
        assert cfg.height == ""
        assert cfg.framerate == ""
        assert cfg.bitrate == ""
        assert cfg.auto_start is False
        assert cfg.source_reconnect_interval == 5
        assert cfg.source_reconnect_max_attempts == 0

    def test_custom_values(self):
        cfg = StreamConfig(
            name="stream1",
            title="我的通道",
            source_type="video",
            source_path="/test.mp4",
            loop=True,
            video_codec="libx264",
            width="1920",
            height="1080",
            framerate="30",
            bitrate="2M",
            source_reconnect_interval=8,
            source_reconnect_max_attempts=0,
        )
        assert cfg.name == "stream1"
        assert cfg.title == "我的通道"
        assert cfg.source_type == "video"
        assert cfg.width == "1920"
        assert cfg.bitrate == "2M"
        assert cfg.source_reconnect_interval == 8
        assert cfg.source_reconnect_max_attempts == 0


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.rtsp_server == ""
        assert cfg.server_locked is False
        assert cfg.username == ""
        assert cfg.machine_name == ""
        assert cfg.auth_secret == ""
        assert cfg.server_reconnect_interval == 5
        assert cfg.server_reconnect_max_attempts == 0
        assert cfg.launch_at_startup is False
        assert cfg.streams == []

    def test_config_dir_uses_beaverpush_name(self):
        from beaverpush.models.config import CONFIG_DIR
        assert CONFIG_DIR.name == "BeaverPush"

    def test_has_v2_auth_fields(self):
        """验证 AppConfig 包含 v2 认证字段（无全局默认参数）"""
        cfg = AppConfig(username="alice", machine_name="pc1", auth_secret="AKsecret")
        assert cfg.username == "alice"
        assert cfg.machine_name == "pc1"
        assert cfg.auth_secret == "AKsecret"
        assert not hasattr(cfg, "default_codec")
        assert not hasattr(cfg, "default_width")
        assert not hasattr(cfg, "default_height")
        assert not hasattr(cfg, "default_fps")
        assert not hasattr(cfg, "default_bitrate")

    def test_add_and_remove_stream(self):
        cfg = AppConfig()
        stream = StreamConfig(name="s1", source_type="video")
        cfg.add_stream(stream)
        assert len(cfg.streams) == 1
        assert cfg.streams[0]["name"] == "s1"
        cfg.remove_stream(0)
        assert len(cfg.streams) == 0


class TestConfigPersistence:
    def test_save_and_load(self, tmp_path):
        config_file = tmp_path / "config.json"
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file), \
             mock.patch("beaverpush.models.config.CONFIG_DIR", tmp_path):
            cfg = AppConfig(
                rtsp_server="rtsp://test:8554",
                server_locked=True,
                username="alice",
                machine_name="pc1",
                auth_secret="AKsecret123",
                server_reconnect_interval=7,
                server_reconnect_max_attempts=9,
            )
            stream = StreamConfig(
                name="s1",
                source_type="screen",
                source_reconnect_interval=9,
                source_reconnect_max_attempts=0,
            )
            cfg.add_stream(stream)
            save_config(cfg)

            loaded = load_config()
            assert loaded.rtsp_server == "rtsp://test:8554"
            assert loaded.server_locked is True
            assert loaded.username == "alice"
            assert loaded.machine_name == "pc1"
            assert loaded.auth_secret == "AKsecret123"
            assert loaded.server_reconnect_interval == 7
            assert loaded.server_reconnect_max_attempts == 9
            assert len(loaded.streams) == 1
            assert loaded.streams[0]["name"] == "s1"
            assert loaded.streams[0]["source_reconnect_interval"] == 9
            assert loaded.streams[0]["source_reconnect_max_attempts"] == 0

    def test_load_legacy_server_reconnect_duration_field(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "rtsp_server": "rtsp://test:8554",
                    "server_reconnect_interval": 5,
                    "server_reconnect_duration": 12,
                    "streams": [],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file):
            loaded = load_config()
        assert loaded.server_reconnect_max_attempts == 12

    def test_load_legacy_client_id_to_machine_name(self, tmp_path):
        """旧配置中的 client_id 应迁移到 machine_name。"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "rtsp_server": "rtsp://test:8554",
                    "client_id": "old_client_uuid",
                    "streams": [],
                }
            ),
            encoding="utf-8",
        )
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file):
            loaded = load_config()
        assert loaded.machine_name == "old_client_uuid"
        assert loaded.username == ""

    def test_load_missing_file(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file):
            cfg = load_config()
            assert cfg.rtsp_server == ""
            assert cfg.username == ""
            assert cfg.machine_name == ""
            assert cfg.auth_secret == ""
            assert cfg.launch_at_startup is False

    def test_launch_at_startup_round_trip(self, tmp_path):
        """``launch_at_startup`` 应能正确序列化与反序列化。"""
        config_file = tmp_path / "config.json"
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file), \
             mock.patch("beaverpush.models.config.CONFIG_DIR", tmp_path):
            save_config(AppConfig(launch_at_startup=True))
            loaded = load_config()
            assert loaded.launch_at_startup is True

    def test_load_legacy_without_launch_at_startup(self, tmp_path):
        """旧配置文件缺少 ``launch_at_startup`` 字段时应回退为 False。"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"rtsp_server": "rtsp://test:8554", "streams": []}),
            encoding="utf-8",
        )
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file):
            loaded = load_config()
        assert loaded.launch_at_startup is False

    def test_save_is_atomic_writes_via_tmp_file(self, tmp_path):
        """``save_config`` 必须先写 ``.tmp`` 再原子替换，
        防止崩溃 / 断电时把 ``config.json`` 留成半截 JSON。"""
        config_file = tmp_path / "config.json"
        # 预先放置一个合法旧配置；模拟"中途崩溃"时它必须保持完整。
        original = "{\"rtsp_server\": \"rtsp://old\"}"
        config_file.write_text(original, encoding="utf-8")

        def boom(self, target):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated crash before atomic replace")

        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file), \
             mock.patch("beaverpush.models.config.CONFIG_DIR", tmp_path), \
             mock.patch("pathlib.Path.replace", boom):
            with pytest.raises(RuntimeError):
                save_config(AppConfig(rtsp_server="rtsp://new"))
        # 旧文件保持原样，未被截断
        assert config_file.read_text(encoding="utf-8") == original

        # 正常路径仍然能完整覆盖
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file), \
             mock.patch("beaverpush.models.config.CONFIG_DIR", tmp_path):
            save_config(AppConfig(rtsp_server="rtsp://new"))
            assert load_config().rtsp_server == "rtsp://new"


class TestLoadStreamConfig:
    def test_normal_data(self):
        data = {"name": "s1", "source_type": "video", "title": "我的通道"}
        cfg = load_stream_config(data)
        assert cfg.name == "s1"
        assert cfg.title == "我的通道"
        assert cfg.source_type == "video"

    def test_extra_keys_ignored(self):
        data = {"name": "s1", "source_type": "video", "unknown_field": True}
        cfg = load_stream_config(data)
        assert cfg.name == "s1"
        assert not hasattr(cfg, "unknown_field")

    def test_missing_keys_use_defaults(self):
        data = {"name": "s1"}
        cfg = load_stream_config(data)
        assert cfg.name == "s1"
        assert cfg.source_type == ""
        assert cfg.title == ""
        assert cfg.source_reconnect_interval == 5
        assert cfg.source_reconnect_max_attempts == 0

    def test_empty_data(self):
        cfg = load_stream_config({})
        assert cfg.name == ""

    def test_load_corrupt_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not json", encoding="utf-8")
        with mock.patch("beaverpush.models.config.CONFIG_FILE", config_file):
            cfg = load_config()
            assert cfg.rtsp_server == ""
