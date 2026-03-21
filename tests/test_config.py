"""config 模块单元测试"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from push_client.models.config import (
    AppConfig, StreamConfig, load_config, save_config,
)


class TestStreamConfig:
    def test_defaults(self):
        cfg = StreamConfig()
        assert cfg.name == ""
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

    def test_custom_values(self):
        cfg = StreamConfig(
            name="stream1",
            source_type="video",
            source_path="/test.mp4",
            loop=True,
            video_codec="libx264",
            width="1920",
            height="1080",
            framerate="30",
            bitrate="2M",
        )
        assert cfg.name == "stream1"
        assert cfg.source_type == "video"
        assert cfg.width == "1920"
        assert cfg.bitrate == "2M"


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.rtsp_server == ""
        assert cfg.server_locked is False
        assert cfg.client_id == ""
        assert cfg.streams == []

    def test_has_client_id(self):
        """验证 AppConfig 包含 client_id 字段（无全局默认参数）"""
        cfg = AppConfig(client_id="client01")
        assert cfg.client_id == "client01"
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
        with mock.patch("push_client.models.config.CONFIG_FILE", config_file), \
             mock.patch("push_client.models.config.CONFIG_DIR", tmp_path):
            cfg = AppConfig(
                rtsp_server="rtsp://test:8554",
                server_locked=True,
                client_id="my_client",
            )
            stream = StreamConfig(name="s1", source_type="screen")
            cfg.add_stream(stream)
            save_config(cfg)

            loaded = load_config()
            assert loaded.rtsp_server == "rtsp://test:8554"
            assert loaded.server_locked is True
            assert loaded.client_id == "my_client"
            assert len(loaded.streams) == 1
            assert loaded.streams[0]["name"] == "s1"

    def test_load_missing_file(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        with mock.patch("push_client.models.config.CONFIG_FILE", config_file):
            cfg = load_config()
            assert cfg.rtsp_server == ""
            assert cfg.client_id == ""

    def test_load_corrupt_file(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("not json", encoding="utf-8")
        with mock.patch("push_client.models.config.CONFIG_FILE", config_file):
            cfg = load_config()
            assert cfg.rtsp_server == ""
