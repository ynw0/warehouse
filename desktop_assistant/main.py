"""Windows 独立物料小助手。"""

import sys
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QPoint, QSettings, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QIcon
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget,
)


def resource_path(relative):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / relative


class Bridge(QObject):
    def __init__(self, ball):
        super().__init__(ball)
        self.ball = ball

    @Slot(str, str)
    def notify(self, title, body):
        self.ball.show_prompt(title, body)

    @Slot()
    def hideChat(self):
        self.ball.chat.hide()


class ChatWindow(QWidget):
    def __init__(self, ball, server):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.ball = ball
        self.setWindowTitle("物料小助手")
        self.resize(620, 640)
        self.setMinimumSize(340, 320)
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile("warehouse_assistant", self.view)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.page = QWebEnginePage(self.profile, self.view)
        self.channel = QWebChannel(self.page)
        self.bridge = Bridge(ball)
        self.channel.registerObject("desktop", self.bridge)
        self.page.setWebChannel(self.channel)
        self.view.setPage(self.page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.setUrl(QUrl(server.rstrip("/") + "/assistant/desktop"))

    def closeEvent(self, event):
        event.ignore()
        self.hide()


class PromptWindow(QWidget):
    def __init__(self, ball):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.ball = ball
        self.setObjectName("assistantPrompt")
        self.setStyleSheet("""
            QWidget#assistantPrompt { background:white; border:1px solid #cddaea; border-radius:12px; }
            QLabel { border:0; color:#253c56; font-size:13px; }
            QPushButton { border:1px solid #d8e4f1; border-radius:6px; padding:5px 12px; }
        """)
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setStyleSheet("font-weight:700; color:#245c9e;")
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        view = QPushButton("查看")
        later = QPushButton("稍后")
        view.clicked.connect(self.open_chat)
        later.clicked.connect(self.hide)
        buttons.addWidget(view)
        buttons.addWidget(later)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addLayout(buttons)

    def open_chat(self):
        self.hide()
        self.ball.show_chat()


class FloatingBall(QWidget):
    def __init__(self, server):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.settings = QSettings("WarehouseSuite", "Assistant")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(64, 64)
        icon_path = str(resource_path("static/assistant-icon.svg"))
        self.icon = QSvgWidget(icon_path, self)
        self.icon.setGeometry(5, 5, 54, 54)
        self.icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.chat = ChatWindow(self, server)
        self.prompt = PromptWindow(self)
        self.drag_start = None
        self.window_start = None
        self.dragged = False
        self.edge = ""
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_at_edge)
        self.tray = QSystemTrayIcon(QIcon(icon_path), self)
        self.tray.setToolTip("物料小助手")
        self.tray.messageClicked.connect(self.show_chat)
        self.tray.activated.connect(self.on_tray)
        self.menu = QMenu()
        show = QAction("打开小助手", self)
        show.triggered.connect(self.show_chat)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        switch_account = QAction("切换账号", self)
        switch_account.triggered.connect(self.switch_account)
        change_server = QAction("更换服务器", self)
        change_server.triggered.connect(self.change_server)
        self.menu.addAction(show)
        self.menu.addAction(switch_account)
        self.menu.addAction(change_server)
        self.menu.addSeparator()
        self.menu.addAction(quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = int(self.settings.value("x", screen.right() - self.width()))
        y = int(self.settings.value("y", screen.top() + 120))
        if not QGuiApplication.screenAt(QPoint(x + 32, y + 32)):
            x, y = screen.right() - self.width(), screen.top() + 120
        self.move(x, y)
        self.hide_timer.start(1200)

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_chat()

    def switch_account(self):
        self.chat.view.page().runJavaScript(
            "fetch('/api/assistant/desktop-logout', {method:'POST'}).then(() => location.reload())"
        )
        self.show_chat()

    def change_server(self):
        current = str(self.settings.value("server", ""))
        server, accepted = QInputDialog.getText(self.chat, "连接库房服务器", "请输入服务器地址", text=current)
        if not accepted:
            return
        server = server.strip().rstrip("/")
        parsed = urlparse(server)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            QMessageBox.critical(self.chat, "地址错误", "请输入完整的 http 或 https 服务器地址。")
            return
        self.settings.setValue("server", server)
        self.chat.view.setUrl(QUrl(server + "/assistant/desktop"))
        self.show_chat()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.tray.contextMenu().popup(QCursor.pos())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.hide_timer.stop()
            self.reveal()
            self.drag_start = event.globalPosition().toPoint()
            self.window_start = self.pos()
            self.dragged = False

    def mouseMoveEvent(self, event):
        if self.drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self.drag_start
        if delta.manhattanLength() < 5 and not self.dragged:
            return
        self.dragged = True
        point = self.window_start + delta
        screen = QGuiApplication.screenAt(event.globalPosition().toPoint()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        point.setX(max(area.left(), min(area.right() - self.width() + 1, point.x())))
        point.setY(max(area.top(), min(area.bottom() - self.height() + 1, point.y())))
        self.move(point)
        if self.chat.isVisible():
            self.position_chat()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.drag_start is None:
            return
        if not self.dragged:
            self.show_chat()
        else:
            self.settings.setValue("x", self.x())
            self.settings.setValue("y", self.y())
            self.hide_timer.start(1200)
        self.drag_start = None

    def enterEvent(self, event):
        self.hide_timer.stop()
        self.reveal()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.drag_start is None:
            self.hide_timer.start(1200)
        super().leaveEvent(event)

    def hide_at_edge(self):
        area = (QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()).availableGeometry()
        distances = {
            "left": abs(self.x() - area.left()),
            "right": abs(self.x() + self.width() - area.right() - 1),
            "top": abs(self.y() - area.top()),
            "bottom": abs(self.y() + self.height() - area.bottom() - 1),
        }
        edge = min(distances, key=distances.get)
        if distances[edge] > 24:
            self.edge = ""
            return
        self.edge = edge
        pos = self.pos()
        if edge == "left":
            pos.setX(area.left() - self.width() // 2)
        elif edge == "right":
            pos.setX(area.right() - self.width() // 2 + 1)
        elif edge == "top":
            pos.setY(area.top() - self.height() // 2)
        else:
            pos.setY(area.bottom() - self.height() // 2 + 1)
        self.move(pos)

    def reveal(self):
        if not self.edge:
            return
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))
        self.edge = ""

    def position_chat(self):
        area = (QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()).availableGeometry()
        size = self.chat.size()
        x = self.x() - size.width() - 8 if self.x() + self.width() + size.width() > area.right() else self.x() + self.width() + 8
        y = self.y()
        x = max(area.left(), min(x, area.right() - size.width() + 1))
        y = max(area.top(), min(y, area.bottom() - size.height() + 1))
        self.chat.move(x, y)

    def show_chat(self):
        self.reveal()
        self.position_chat()
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.view.page().runJavaScript("document.getElementById('aiFloatBox')?.classList.add('open')")

    def show_prompt(self, title, body):
        self.reveal()
        self.prompt.title.setText(title)
        self.prompt.body.setText(body)
        self.prompt.adjustSize()
        area = (QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()).availableGeometry()
        x = self.x() - self.prompt.width() - 8 if self.x() + self.width() + self.prompt.width() > area.right() else self.x() + self.width() + 8
        y = max(area.top(), min(self.y(), area.bottom() - self.prompt.height() + 1))
        self.prompt.move(max(area.left(), x), y)
        self.prompt.show()
        self.prompt.raise_()
        self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 10000)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    settings = QSettings("WarehouseSuite", "Assistant")
    server = str(settings.value("server", "")).strip()
    if not server:
        server, accepted = QInputDialog.getText(None, "连接库房服务器", "请输入服务器地址，例如 http://192.168.1.10:5000")
        if not accepted:
            return 0
        server = server.strip().rstrip("/")
        parsed = urlparse(server)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            QMessageBox.critical(None, "地址错误", "请输入完整的 http 或 https 服务器地址。")
            return 1
        settings.setValue("server", server)
    ball = FloatingBall(server)
    ball.show()
    ball.show_chat()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
