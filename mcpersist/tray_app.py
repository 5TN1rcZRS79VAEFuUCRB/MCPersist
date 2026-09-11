"""The GUI's entry point: the main window (see gui_pages.py for its screens).
No system tray icon - the server/tunnel already run as detached processes that
outlive this window regardless (see process_manager.py), so this app doesn't
need to keep running in the background at all for them to keep working; closing
the window quits it for real."""

import ctypes
import os
import sys
from pathlib import Path

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from . import process_manager
from .gui_pages import SetupPage, StatusPage
from .paths import BASE_DIR

# Prevents a second instance racing the first to start the server (or just
# showing stale/conflicting state) if the .exe gets double-clicked again while
# already running.
GUI_PID_PATH = BASE_DIR / "gui.pid"


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

        self.stack = SizedStackedWidget()
        self.setCentralWidget(self.stack)

        self.status_page = StatusPage()
        self.setup_page = SetupPage()

        self.stack.addWidget(self.status_page)
        self.stack.addWidget(self.setup_page)

        self.status_page.go_to_setup.connect(self.open_setup)
        self.setup_page.done.connect(self.back_to_status)
        self.setup_page.cancelled.connect(self.back_to_status)

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

    def quit_app(self):
        """Used directly (not via closeEvent) by the self-update flow, which needs
        to actually exit right after a successful update - there's no window-close
        involved there at all."""
        self._wait_for_pending_workers()
        QApplication.quit()

    def _wait_for_pending_workers(self):
        """Nothing here previously stopped a user from clicking Stop and then
        closing the window right after, before that action's background Worker
        (a QThread) had actually finished - destroying a QThread object while its
        underlying thread is still running is a real, if narrow, crash risk in
        its own right, separate from the actual process_manager.py
        finalizer-timing bug this session traced a real reproducible crash to
        (see _detached_procs there). Every path to quitting funnels through
        here, so this is the one place that can confirm nothing is still in
        flight, rather than every call site remembering to check. Waits up to
        5s per worker - long enough for a real Stop (RCON + graceful/kill
        timeouts) or setup step to finish normally, without hanging the quit
        forever if one is genuinely stuck.
        """
        candidates = (
            getattr(self.status_page, "_worker", None),
            getattr(self.status_page, "_update_worker", None),
            getattr(self.status_page, "_update_apply_worker", None),
            getattr(self.status_page, "_recover_worker", None),
            getattr(self.setup_page, "_worker", None),
            getattr(self.setup_page, "_version_worker", None),
        )
        for worker in candidates:
            if worker is not None and worker.isRunning():
                worker.wait(5000)

    def closeEvent(self, event):
        self._wait_for_pending_workers()
        event.accept()


def _gui_actually_running(pid):
    """process_manager.is_running only confirms *some* process currently has this
    PID - after the known native crash (which bypasses app.aboutToQuit, so gui.pid
    never gets cleaned up), Windows reusing that exact PID for any unrelated
    process would otherwise make a crashed instance look "still running" forever,
    permanently blocking relaunch with no indication of what actually happened.
    Confirming the PID's own executable path matches this one closes that gap."""
    if not process_manager.is_running(pid):
        return False
    try:
        exe = psutil.Process(pid).exe()
    except psutil.Error:
        return False
    try:
        return Path(exe).resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def main():
    if _gui_actually_running(process_manager.read_pid(GUI_PID_PATH)):
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
