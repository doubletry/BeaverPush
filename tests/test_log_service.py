"""log_service 模块单元测试"""

import os
import tempfile
from pathlib import Path
from unittest import mock


class TestSetupLogging:
    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_file = log_dir / "push_client.log"
        with mock.patch("push_client.services.log_service.LOG_DIR", log_dir), \
             mock.patch("push_client.services.log_service.logger") as mock_logger:
            from push_client.services.log_service import setup_logging
            setup_logging()
            assert log_dir.exists()

    def test_logger_importable(self):
        from push_client.services.log_service import logger
        assert logger is not None
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "warning")
