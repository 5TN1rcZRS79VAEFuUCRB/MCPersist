"""The GUI's entry point: the main window (see gui_pages.py for its screens). No tray
icon - the server and tunnel are detached processes that outlive this window, so
closing it quits for real."""

import ctypes
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from . import process_manager
from .gui_pages import SetupPage, StatusPage
from .paths import BASE_DIR
from .version import VERSION

# Stops a second instance (the .exe double-clicked again) racing the first.
GUI_PID_PATH = BASE_DIR / "gui.pid"

# Restored on every page switch: Qt widens a window for a wide page but never shrinks it
# back. The status page's natural minimum is ~478px.
DEFAULT_WIDTH = 480


class SizedStackedWidget(QStackedWidget):
    """A QStackedWidget sizes itself to its LARGEST page; report only the current page,
    so fit_to_current_page can shrink the window back down."""

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current else super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current else super().minimumSizeHint()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MCPersist v{VERSION}")
        self.resize(DEFAULT_WIDTH, 560)
        # Caps growth from a long unbreakable message (a file path) - Qt never shrinks
        # the window back.
        self.setMaximumWidth(700)

        self.stack = SizedStackedWidget()
        self.setCentralWidget(self.stack)

        self.status_page = StatusPage()
        self.setup_page = SetupPage()

        self.stack.addWidget(self.status_page)
        self.stack.addWidget(self.setup_page)

        self.status_page.go_to_setup.connect(self.open_setup)
        self.setup_page.done.connect(self.back_to_status)
        self.setup_page.cancelled.connect(self.back_to_status)

        # The first page needs the same deferred fit as every later switch, or a
        # word-wrapped label measured too early is clipped.
        self.fit_to_current_page()

    def open_setup(self):
        self.setup_page.reset()
        self.stack.setCurrentWidget(self.setup_page)
        self.fit_to_current_page()

    def back_to_status(self):
        self.stack.setCurrentWidget(self.status_page)
        self.status_page.refresh()
        self.fit_to_current_page()

    def fit_to_current_page(self):
        # Qt never shrinks a window back on its own, so re-measure the current page each
        # time it's shown - deferred one tick so the layout has settled.
        page = self.stack.currentWidget()
        if page is None:
            return

        def resize_to_fit():
            self.stack.updateGeometry()
            # Recompute the cached minimum first: Qt clamps a resize to the stale, wider
            # value otherwise.
            if self.layout() is not None:
                self.layout().invalidate()
                self.layout().activate()
            target_height = max(300, min(780, page.sizeHint().height() + 24))
            # Width back to DEFAULT_WIDTH (or the page's minimum, if wider) each time,
            # so it depends on the current page, not on which pages were visited.
            target_width = max(DEFAULT_WIDTH, page.minimumSizeHint().width())
            self.resize(target_width, target_height)

        QTimer.singleShot(0, resize_to_fit)

    def quit_app(self):
        """Used by the self-update flow, which quits without any window close."""
        self._when_idle(QApplication.quit)

    def _when_idle(self, then):
        """Runs `then` once no background task is running - quitting mid-task destroys a
        live QThread (Qt aborts the process) and kills the task partway (a
        half-copied world). Nothing blocks: the title says what's happening while
        this polls."""
        if not self._pending_workers():
            then()
            return
        self.setWindowTitle(f"MCPersist v{VERSION} - closing when the current task finishes...")
        if not getattr(self, "_idle_timer", None):
            self._idle_timer = QTimer(self)
            self._idle_timer.setInterval(250)
            self._idle_timer.timeout.connect(self._check_idle)
        self._idle_then = then
        self._idle_timer.start()

    def _check_idle(self):
        if self._pending_workers():
            return
        self._idle_timer.stop()
        self._idle_then()

    def _pending_workers(self):
        return [w for w in self._worker_candidates() if w is not None and w.isRunning()]

    def _worker_candidates(self):
        """Every Worker (QThread) the app can have in flight - extend this when a page
        gains one."""
        return (
            getattr(self.status_page, "_worker", None),
            getattr(self.status_page, "_update_worker", None),
            getattr(self.status_page, "_update_apply_worker", None),
            getattr(self.status_page, "_recover_worker", None),
            getattr(self.setup_page, "_worker", None),
            getattr(self.setup_page, "_version_worker", None),
            # Abandoned wizard runs can still be copying a world - exactly the ones
            # worth waiting for.
            *getattr(self.setup_page, "_stale_workers", ()),
        )

    def closeEvent(self, event):
        if self._pending_workers():
            event.ignore()
            self._when_idle(self.close)
            return
        event.accept()


def main():
    # read_pid matches the recorded start time, so a crashed instance's leftover
    # gui.pid can't match whatever process (the tunnel included) later reuses its PID.
    if process_manager.is_running(process_manager.read_pid(GUI_PID_PATH)):
        ctypes.windll.user32.MessageBoxW(
            0,
            "MCPersist is already running - check its window (it may be minimized "
            "or behind another one).",
            "MCPersist",
            0x40,  # MB_ICONINFORMATION
        )
        return
    process_manager.write_pid(GUI_PID_PATH, os.getpid())

    app = QApplication(sys.argv)
    app.aboutToQuit.connect(lambda: GUI_PID_PATH.unlink(missing_ok=True))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
