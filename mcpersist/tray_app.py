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

# Without this the app is just raw, unstyled default Qt widgets on whatever the
# OS theme happens to be - flat, cramped, no visual hierarchy. This gives every
# window a consistent look and marks the "move forward" button in each screen
# (objectName "primaryButton") so the intended next action stands out from
# secondary ones like Browse/Cancel.
APP_STYLESHEET = """
QWidget {
    background-color: #1e1f22;
    color: #e6e6e6;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #3a3b3f;
    border-radius: 6px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    color: #cfd2d6;
}
QLineEdit, QComboBox, QTextEdit, QSpinBox {
    background-color: #28292d;
    border: 1px solid #3a3b3f;
    border-radius: 4px;
    padding: 5px 7px;
    selection-background-color: #4CAF50;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus {
    border: 1px solid #4CAF50;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QPushButton {
    background-color: #34363b;
    border: 1px solid #46484d;
    border-radius: 5px;
    padding: 7px 14px;
}
QPushButton:hover {
    background-color: #3b3e43;
    border: 1px solid #57595e;
}
QPushButton:pressed {
    background-color: #2a2c30;
}
QPushButton:disabled {
    color: #6b6d72;
    background-color: #28292d;
}
QPushButton#primaryButton {
    background-color: #3f8f49;
    border: 1px solid #4CAF50;
    color: white;
    font-weight: bold;
}
QPushButton#primaryButton:hover {
    background-color: #4CAF50;
}
QPushButton#primaryButton:pressed {
    background-color: #357a3d;
}
QRadioButton, QCheckBox {
    spacing: 7px;
    background: transparent;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 14px;
    height: 14px;
}
QRadioButton::indicator {
    border-radius: 8px;
    border: 2px solid #5a5c61;
    background: transparent;
}
QRadioButton::indicator:checked {
    border: 2px solid #4CAF50;
    background-color: #4CAF50;
}
QRadioButton::indicator:hover {
    border: 2px solid #4CAF50;
}
QScrollBar:vertical {
    background: #1e1f22;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a3b3f;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


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


class SizedStackedWidget(QStackedWidget):
    """A QStackedWidget sizes itself to fit the LARGEST page it holds, by
    design - every other page then carries that page's dead space too. Since
    the status page and the setup wizard have very different natural heights,
    that made every page as tall as whichever one needed the most room.
    Overriding these two hints to reflect only the current page is what lets
    MainWindow.fit_to_current_page actually shrink the window back down."""

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current else super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current else super().minimumSizeHint()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCPersist")
        self.resize(420, 560)
        # A long unbroken status message (a full file path with no spaces to wrap
        # at, say) can otherwise force the window to stretch far wider than
        # intended to fit it on one line, and Qt doesn't shrink it back down again
        # once that happens - a hard cap means the worst case is wrapped/clipped
        # text in a normal-sized window, not a window that grows and stays huge.
        self.setMaximumWidth(700)
        self._really_quit = False

        self.stack = SizedStackedWidget()
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
        self.fit_to_current_page()

    def back_to_status(self):
        self.stack.setCurrentWidget(self.status_page)
        self.status_page.refresh()
        self.fit_to_current_page()

    def fit_to_current_page(self):
        # The window was given a one-time size in __init__, and Qt never
        # shrinks a manually-resized window back down on its own - so whichever
        # page needed the most room (usually the setup wizard's) would leave
        # every shorter page (the status view, or an early wizard step) with a
        # big dead strip of empty space below its last widget. Re-measuring the
        # currently-visible page's actual content each time it's shown fixes
        # that. Deferred one tick so the layout has settled after the widgets
        # this page just showed/hid actually take effect.
        page = self.stack.currentWidget()
        if page is None:
            return

        def resize_to_fit():
            self.stack.updateGeometry()
            target_height = max(300, min(780, page.sizeHint().height() + 24))
            self.resize(self.width(), target_height)

        QTimer.singleShot(0, resize_to_fit)

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
    app.setStyleSheet(APP_STYLESHEET)
    app.aboutToQuit.connect(lambda: GUI_PID_PATH.unlink(missing_ok=True))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
