"""
单实例保护
==========

通过 ``QLocalServer`` / ``QLocalSocket`` 确保同一时刻只运行一个应用实例。

当第二个实例启动时，它会向已有实例发送激活消息，然后自行退出；
已有实例收到消息后通过 ``activated`` 信号通知主窗口显示到前台。

使用方式::

    guard = SingleInstanceGuard("BeaverPush")
    if not guard.try_start():
        sys.exit(0)
    guard.activated.connect(bring_window_to_front)
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard(QObject):
    """单实例守卫。

    Signals:
        activated(): 另一个实例尝试启动时发送此信号。
    """

    activated = Signal()

    def __init__(self, app_id: str, parent: QObject | None = None):
        super().__init__(parent)
        self._app_id = app_id
        self._server: QLocalServer | None = None

    def try_start(self) -> bool:
        """尝试成为主实例。

        Returns:
            ``True`` 表示当前是主实例，可以继续运行；
            ``False`` 表示已有实例在运行，已发送激活消息，应退出。
        """
        # 尝试连接已有实例
        socket = QLocalSocket(self)
        socket.connectToServer(self._app_id)
        if socket.waitForConnected(500):
            # 已有实例在运行 → 发送激活消息后退出
            socket.write(b"activate")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return False

        # 当前是主实例 → 启动服务器监听后续实例
        self._server = QLocalServer(self)
        # 清理上次异常退出遗留的 socket 文件
        QLocalServer.removeServer(self._app_id)
        if not self._server.listen(self._app_id):
            return False
        self._server.newConnection.connect(self._on_new_connection)
        return True

    def _on_new_connection(self):
        """处理来自其他实例的连接。"""
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        if socket:
            socket.waitForReadyRead(1000)
            self.activated.emit()
            socket.disconnectFromServer()
