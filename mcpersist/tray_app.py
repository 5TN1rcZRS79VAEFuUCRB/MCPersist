"""The GUI's entry point: the main window (see gui_pages.py for its screens) plus
the system tray icon it minimizes to."""

import ctypes
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QStackedWidget, QSystemTrayIcon

from . import actions, config, process_manager
from .gui_pages import SetupPage, StatusPage
from .paths import BASE_DIR

# Minimizing to tray means the window can be running with nothing visible to remind
# you of that - easy to forget and double-click the exe again. A second instance
# racing the first to start the server (or just showing stale/conflicting state)
# is exactly the kind of confusing mess this avoids.
GUI_PID_PATH = BASE_DIR / "gui.pid"

COLORS = {
    "running": QColor(46, 204, 113),
    "stopped": QColor(231, 76, 60),
    "partial": QColor(241, 196, 15),
    "unknown": QColor(149, 165, 166),
}


def make_icon(color):
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setBrush(color)
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pixmap)


def current_status():
    cfg = config.load()
    server_dir = config.server_dir(cfg)
    if not server_dir or not server_dir.exists():
        return None
    return actions.get_status(cfg, server_dir)


def status_color(status):
    if status is None:
        return COLORS["unknown"]
    if status["server_running"] and status["tunnel_running"]:
        return COLORS["running"]
    if status["server_running"] or status["tunnel_running"]:
        return COLORS["partial"]
    return COLORS["stopped"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCPersist")
        self.resize(420, 560)
        self._really_quit = False

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.status_page = StatusPage()
        self.setup_page = SetupPage()

        self.stack.addWidget(self.status_page)
        self.stack.addWidget(self.setup_page)

        self.status_page.go_to_setup.connect(self.open_setup)
        self.setup_page.done.connect(self.back_to_status)
        self.setup_page.cancelled.connect(self.back_to_status)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(make_icon(status_color(current_status())))
        self.tray.activated.connect(self.on_tray_activated)

        self.tray_menu = QMenu()
        self.show_action = QAction("Show Window")
        self.show_action.triggered.connect(self.show_window)
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(self.show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.update_tray)
        self.poll_timer.start(5000)
        self.update_tray()

    def open_setup(self):
        self.setup_page.reset()
        self.stack.setCurrentWidget(self.setup_page)

    def back_to_status(self):
        self.stack.setCurrentWidget(self.status_page)
        self.status_page.refresh()

    def update_tray(self):
        st = current_status()
        self.tray.setIcon(make_icon(status_color(st)))
        if st is None:
            self.tray.setToolTip("MCPersist - no world configured yet")
        else:
            addr = st["join_address"] or "not assigned yet"
            self.tray.setToolTip(
                f"MCPersist - {st['world_name']}\n"
                f"Server: {'running' if st['server_running'] else 'stopped'}\n"
                f"Tunnel: {'running' if st['tunnel_running'] else 'stopped'}\n"
                f"Join: {addr}"
            )

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._really_quit = True
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        if self._really_quit:
            event.accept()
            return
        event.ignore()
        self.hide()


def main():
    if process_manager.is_running(process_manager.read_pid(GUI_PID_PATH)):
        ctypes.windll.user32.MessageBoxW(
            0,
            "MCPersist is already running - check your system tray (it may be in the "
            "hidden icons area, near the clock).",
            "MCPersist",
            0x40,  # MB_ICONINFORMATION
        )
        return
    process_manager.write_pid(GUI_PID_PATH, os.getpid())

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(lambda: GUI_PID_PATH.unlink(missing_ok=True))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
