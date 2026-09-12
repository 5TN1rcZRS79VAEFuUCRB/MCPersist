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

# The one width the window should normally be. Kept as a constant because
# fit_to_current_page actively restores it: Qt widens a window when a page's
# minimum width exceeds the current one and then never shrinks it back, so
# without restoring it, one wide step of the setup wizard permanently widened the
# window for the rest of the session - including back on the status page, which
# needs less. Sized to the status page's own natural minimum (~478px).
DEFAULT_WIDTH = 480


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
        # 420 used to be enough, but the status page's own natural (minimum) width
        # is now ~478px - the World section's 3-button folder row and the
        # Memory/Performance Save-button rows don't have room to shrink any
        # further (their buttons are already at their own minimum), so at 420 Qt
        # can't lay them out without cramming - confirmed by direct measurement
        # (StatusPage.sizeHint().width() == 478 well before this session's other
        # changes, so this wasn't something the scroll-area fix introduced).
        self.resize(DEFAULT_WIDTH, 560)
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

        # Every later page switch calls fit_to_current_page(), specifically to
        # remeasure after a deferred tick so word-wrapped labels (Memory/Performance's
        # detected-specs text, in particular) get their real wrapped height instead of
        # whatever their first, possibly-premature layout pass computed - but the
        # very first page shown here never got that treatment, only the fixed
        # 420x560 above. A word-wrapped label mismeasured on its first-ever layout
        # pass (a real, common Qt timing quirk, not specific to this app) could end
        # up visually clipped within whatever height that first pass allocated it.
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
            # Recompute the cached layout minimum BEFORE resizing. Qt clamps a
            # resize up to the window's current minimum, and that minimum is
            # cached - so if the page just became narrower, the resize below would
            # be clamped to the stale, wider value and the window would stay wide
            # with nothing resizing it again. Confirmed by direct measurement:
            # without this, removing an over-wide widget left the window at its
            # old width even though the constraint had already dropped back.
            if self.layout() is not None:
                self.layout().invalidate()
                self.layout().activate()
            target_height = max(300, min(780, page.sizeHint().height() + 24))
            # Width is restored, not merely preserved. Passing self.width() through
            # (what this used to do) meant any page that had once forced the window
            # wider left it wider forever, since Qt won't shrink it back on its own
            # - so the window's width silently depended on which screens you'd
            # visited. Going back to DEFAULT_WIDTH each time makes it a function of
            # the page you're on instead. The max() is because Qt enforces the
            # page's minimum anyway: a page that genuinely needs more still gets it,
            # rather than being cramped.
            target_width = max(DEFAULT_WIDTH, page.minimumSizeHint().width())
            self.resize(target_width, target_height)

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
            # Runs the user abandoned by re-entering the wizard, which can still be
            # copying a world (see SetupPage.reset) - they're exactly the ones worth
            # waiting on, since quitting mid-copy is what leaves a half-copied world.
            *getattr(self.setup_page, "_stale_workers", ()),
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
    Confirming the PID's own executable path matches this one closes most of that
    gap - but not all of it: the tunnel client subprocess runs via that exact same
    executable too (see main_gui.py's --tunnel-relay-run re-invocation, and
    tunnel_relay.py's source-mode equivalent), so a recycled PID landing on a
    tunnel subprocess instead of an unrelated program would pass the exe check
    while still not actually being the GUI - confirmed by direct reproduction, not
    just reasoning about it. Checking the command line for that subprocess's own
    signature closes the rest of the gap."""
    if not process_manager.is_running(pid):
        return False
    try:
        proc = psutil.Process(pid)
        exe = proc.exe()
        cmdline = proc.cmdline()
    except psutil.Error:
        return False
    try:
        if Path(exe).resolve() != Path(sys.executable).resolve():
            return False
    except OSError:
        return False
    return not any("tunnel_relay_run" in arg or arg == "--tunnel-relay-run" for arg in cmdline)


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
