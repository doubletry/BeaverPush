"""single_instance 模块单元测试

使用 mock 替代真实的 QLocalServer / QLocalSocket。
"""

from unittest import mock

import pytest

from push_client.services.single_instance import SingleInstanceGuard


@pytest.fixture
def _patch_qt_network():
    """统一 patch QLocalSocket 和 QLocalServer。"""
    with mock.patch(
        "push_client.services.single_instance.QLocalSocket"
    ) as MockSocket, mock.patch(
        "push_client.services.single_instance.QLocalServer"
    ) as MockServer:
        yield MockSocket, MockServer


class TestSingleInstanceGuard:
    """单实例守卫测试"""

    def test_primary_instance_starts_server(self, _patch_qt_network):
        """首个实例应启动本地服务器并返回 True。"""
        MockSocket, MockServer = _patch_qt_network

        # 模拟无已有实例（连接失败）
        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = False

        # 模拟服务器启动成功
        server_inst = MockServer.return_value
        server_inst.listen.return_value = True

        guard = SingleInstanceGuard("test-app")
        assert guard.try_start() is True

        # 验证尝试了连接
        socket_inst.connectToServer.assert_called_once_with("test-app")
        # 验证清理了旧 socket 并启动服务器
        MockServer.removeServer.assert_called_once_with("test-app")
        server_inst.listen.assert_called_once_with("test-app")
        server_inst.newConnection.connect.assert_called_once()

    def test_secondary_instance_sends_activation(self, _patch_qt_network):
        """第二个实例应发送激活消息并返回 False。"""
        MockSocket, MockServer = _patch_qt_network

        # 模拟已有实例（连接成功）
        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = True
        socket_inst.waitForBytesWritten.return_value = True

        guard = SingleInstanceGuard("test-app")
        assert guard.try_start() is False

        # 验证发送了激活消息
        socket_inst.write.assert_called_once_with(b"activate")
        socket_inst.disconnectFromServer.assert_called_once()
        # 不应启动服务器
        MockServer.return_value.listen.assert_not_called()

    def test_server_listen_failure(self, _patch_qt_network):
        """服务器监听失败时应返回 False。"""
        MockSocket, MockServer = _patch_qt_network

        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = False

        server_inst = MockServer.return_value
        server_inst.listen.return_value = False

        guard = SingleInstanceGuard("test-app")
        assert guard.try_start() is False

    def test_new_connection_emits_activated(self, _patch_qt_network):
        """收到新连接时应发射 activated 信号。"""
        MockSocket, MockServer = _patch_qt_network

        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = False

        server_inst = MockServer.return_value
        server_inst.listen.return_value = True

        guard = SingleInstanceGuard("test-app")
        guard.try_start()

        # 获取 newConnection 连接的回调
        handler = server_inst.newConnection.connect.call_args[0][0]

        # 模拟传入连接
        incoming_socket = mock.MagicMock()
        server_inst.nextPendingConnection.return_value = incoming_socket

        # 连接信号检测
        with mock.patch.object(guard, "activated") as mock_signal:
            handler()
            mock_signal.emit.assert_called_once()
            incoming_socket.waitForReadyRead.assert_called_once_with(1000)
            incoming_socket.disconnectFromServer.assert_called_once()

    def test_new_connection_no_pending(self, _patch_qt_network):
        """nextPendingConnection 返回 None 时不崩溃。"""
        MockSocket, MockServer = _patch_qt_network

        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = False

        server_inst = MockServer.return_value
        server_inst.listen.return_value = True

        guard = SingleInstanceGuard("test-app")
        guard.try_start()

        handler = server_inst.newConnection.connect.call_args[0][0]
        server_inst.nextPendingConnection.return_value = None

        # 不应抛出异常
        handler()

    def test_different_app_ids_are_independent(self, _patch_qt_network):
        """不同 app_id 应该互不干扰。"""
        MockSocket, MockServer = _patch_qt_network

        socket_inst = MockSocket.return_value
        socket_inst.waitForConnected.return_value = False

        server_inst = MockServer.return_value
        server_inst.listen.return_value = True

        guard1 = SingleInstanceGuard("app-a")
        assert guard1.try_start() is True

        guard2 = SingleInstanceGuard("app-b")
        assert guard2.try_start() is True

        # 验证各自使用了自己的 app_id
        calls = socket_inst.connectToServer.call_args_list
        assert calls[0][0][0] == "app-a"
        assert calls[1][0][0] == "app-b"
