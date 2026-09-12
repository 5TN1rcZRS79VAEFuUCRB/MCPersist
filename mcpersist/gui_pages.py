"""The GUI's screens (status, setup wizard) as PySide6 widgets - all logic delegates
to actions.py/setup_flow.py, same as the CLI."""

import subprocess

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QGuiApplication

from . import actions, config, server_vanilla, setup_flow, update_checker
from .gui_worker import Worker, WorkerError
from .paths import BASE_DIR


def _open_folder(path):
    subprocess.Popen(["explorer", str(path)])


class StatusPage(QWidget):
    go_to_setup = Signal()

    def sizeHint(self):
        # Reports the actual content's natural size, not the QScrollArea's own
        # generic default sizeHint (a small, fixed suggestion unrelated to what's
        # inside it) - MainWindow.fit_to_current_page relies on this to size the
        # window to fit real content when it's short enough to (capping it at 780
        # regardless, with the scroll area handling whatever doesn't fit past that).
        return self._content.sizeHint()

    def minimumSizeHint(self):
        # A plain QScrollArea's own minimumSizeHint doesn't reflect its child
        # widget's minimum size (it's a small, generic default) - without this
        # override, MainWindow could be dragged narrower than the World/Memory/
        # Performance button rows actually need, cramming or clipping them
        # horizontally, undoing the width fix from a prior round. Deliberately
        # keeps the height low (not the content's full minimum height) - forcing
        # that back up would defeat the entire point of the QScrollArea, which
        # exists specifically so the window CAN be shorter than its content, with
        # a scrollbar for the rest, instead of being forced tall or clipped.
        return QSize(self._content.minimumSizeHint().width(), 200)

    def __init__(self):
        super().__init__()
        # Everything below lives in an inner widget wrapped in a QScrollArea,
        # instead of laid out directly on `self` - this page has grown a lot
        # (update banner, status, World/Memory/Performance groups, each with its
        # own wrapped detected-specs/message labels), and the window's own height
        # is deliberately capped (see MainWindow.fit_to_current_page) rather than
        # growing to fit arbitrarily tall content. A long action_msg (the
        # start/stop result, which includes full log file paths and wraps to
        # several lines - exactly what's on screen right after clicking Start) or
        # simply a taller stack of sections than fits under that cap previously
        # had nowhere to go but off the bottom edge, silently clipped with no way
        # to scroll to it. A QScrollArea makes "content taller than the window"
        # degrade to a scrollbar instead of lost/overlapping text, regardless of
        # font size, DPI scaling, or how much more this page grows later.
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self._content = QWidget()
        scroll = QScrollArea()
        scroll.setWidget(self._content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)

        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # ----- Update banner (hidden unless an update is actually found) -----
        self.update_box = QWidget()
        update_row = QHBoxLayout(self.update_box)
        update_row.setContentsMargins(0, 0, 0, 0)
        self.update_label = QLabel("")
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        update_row.addWidget(self.update_label, 1)
        self.update_btn = QPushButton("Update Now")
        self.update_btn.setFixedWidth(110)
        self.update_btn.clicked.connect(self.on_update_now)
        update_row.addWidget(self.update_btn)
        # Only shown alongside the failure banner - check_last_update_failure()
        # already captures the real reason (every step the updater script logged,
        # or the exact "exited immediately" error from update_checker.apply_update)
        # but nothing ever surfaced it beyond a generic "it failed" message. Without
        # this, diagnosing *why* an update failed on someone else's machine meant
        # asking them to go find a temp log file by hand.
        self.update_details_btn = QPushButton("Details")
        self.update_details_btn.setFixedWidth(70)
        self.update_details_btn.clicked.connect(self.show_update_failure_details)
        self.update_details_btn.setVisible(False)
        update_row.addWidget(self.update_details_btn)
        self.update_box.setVisible(False)
        layout.addWidget(self.update_box)
        self._pending_update = None
        # A leftover log here means the updater helper script ran last time and
        # failed partway through (see update_checker._UPDATER_PS1) - the app would
        # have just closed with no explanation, since the failure happened in a
        # detached process after this one already quit. Check once, up front, so
        # that gets surfaced instead of the user just seeing "update available"
        # again with no idea anything went wrong last time.
        self._last_update_failure = update_checker.check_last_update_failure()
        # The success-case counterpart to the failure check above - otherwise a
        # completed update gives zero feedback on the other side of the restart
        # either, which was a big part of why the whole thing looked like the app
        # just closed rather than doing something intentional.
        self._just_updated_to = update_checker.check_update_success()

        # ----- Status group -----
        status_box = QGroupBox()
        status_layout = QVBoxLayout(status_box)

        self.world_label = QLabel("No world configured yet")
        self.world_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        status_layout.addWidget(self.world_label)

        state_row = QHBoxLayout()
        state_row.setSpacing(24)
        self.server_label = QLabel("Server: -")
        self.tunnel_label = QLabel("Tunnel: -")
        self.whitelist_label = QLabel("Whitelist: -")
        state_row.addWidget(self.server_label)
        state_row.addWidget(self.tunnel_label)
        state_row.addWidget(self.whitelist_label)
        state_row.addStretch()
        status_layout.addLayout(state_row)

        addr_row = QHBoxLayout()
        self.address_label = QLabel("-")
        self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.address_label.setStyleSheet("font-family: Consolas, monospace;")
        copy_btn = QPushButton("Copy")
        copy_btn.setFixedWidth(70)
        copy_btn.clicked.connect(self.copy_address)
        addr_row.addWidget(self.address_label, 1)
        addr_row.addWidget(copy_btn)
        status_layout.addLayout(addr_row)

        # Whitelist doesn't get its own section - it's just a status/warning, not
        # something with its own actions to take (management is real Minecraft
        # commands, not a GUI control), so it lives here with the rest of the status.
        self.whitelist_warning = QLabel("")
        self.whitelist_warning.setWordWrap(True)
        self.whitelist_warning.setStyleSheet("color: #e74c3c; font-weight: bold;")
        status_layout.addWidget(self.whitelist_warning)
        whitelist_hint = QLabel("Friends can't join? Use /whitelist commands in Minecraft.")
        whitelist_hint.setStyleSheet("color: #888;")
        status_layout.addWidget(whitelist_hint)

        layout.addWidget(status_box)

        # ----- Primary actions -----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.restart_btn = QPushButton("Restart")
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.restart_btn.clicked.connect(self.on_restart)
        for b in (self.start_btn, self.stop_btn, self.restart_btn):
            b.setMinimumHeight(34)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.action_msg = QLabel("")
        self.action_msg.setStyleSheet("color: #888;")
        self.action_msg.setAlignment(Qt.AlignCenter)
        # Unlike every other status label in this file, this one was missing
        # setWordWrap - _on_action_done joins the start/stop results (which include
        # full log file paths) into one unwrapped string, which was stretching the
        # whole window wider to fit it on one line and never shrinking back after.
        self.action_msg.setWordWrap(True)
        layout.addWidget(self.action_msg)

        # ----- World group -----
        world_box = QGroupBox("World")
        world_layout = QVBoxLayout(world_box)
        setup_btn = QPushButton("Set Up a Server")
        setup_btn.clicked.connect(self.go_to_setup.emit)
        world_layout.addWidget(setup_btn)

        # For recovering worlds left behind in an old install folder - e.g. after
        # a manual reinstall (done by hand because self-update failed) into a
        # fresh folder that doesn't have the old servers/config.json in it. Each
        # world folder is fully self-contained, so this is just a folder copy;
        # "Switch to a Previously Set-Up Server" (in the setup wizard) is what
        # actually makes an imported one active.
        self.recover_btn = QPushButton("Recover Worlds from Old Install...")
        self.recover_btn.clicked.connect(self.on_recover_from_old_install)
        world_layout.addWidget(self.recover_btn)

        folder_row = QHBoxLayout()
        open_world_btn = QPushButton("Server Folder")
        open_world_btn.clicked.connect(self.open_world_folder)
        open_mods_btn = QPushButton("Mods Folder")
        open_mods_btn.clicked.connect(self.open_mods_folder)
        open_logs_btn = QPushButton("View Logs")
        open_logs_btn.clicked.connect(self.open_logs)
        folder_row.addWidget(open_world_btn)
        folder_row.addWidget(open_mods_btn)
        folder_row.addWidget(open_logs_btn)
        world_layout.addLayout(folder_row)
        layout.addWidget(world_box)

        # ----- Memory group -----
        memory_box = QGroupBox("Memory")
        memory_layout = QVBoxLayout(memory_box)

        self.ram_detected_label = QLabel("")
        self.ram_detected_label.setStyleSheet("color: #888;")
        self.ram_detected_label.setWordWrap(True)
        memory_layout.addWidget(self.ram_detected_label)

        ram_row = QHBoxLayout()
        self.ram_auto_check = QCheckBox("Auto")
        self.ram_auto_check.toggled.connect(self.on_ram_auto_toggled)
        self.ram_spin = QSpinBox()
        self.ram_spin.setSuffix(" GB")
        ram_save_btn = QPushButton("Save")
        ram_save_btn.setFixedWidth(70)
        ram_save_btn.clicked.connect(self.on_save_ram)
        ram_row.addWidget(self.ram_auto_check)
        ram_row.addWidget(self.ram_spin, 1)
        ram_row.addWidget(ram_save_btn)
        memory_layout.addLayout(ram_row)

        self.ram_msg = QLabel("")
        self.ram_msg.setWordWrap(True)
        self.ram_msg.setStyleSheet("color: #b45309;")
        memory_layout.addWidget(self.ram_msg)
        layout.addWidget(memory_box)

        # ----- Performance group -----
        perf_box = QGroupBox("Performance")
        perf_layout = QVBoxLayout(perf_box)

        self.perf_detected_label = QLabel("")
        self.perf_detected_label.setStyleSheet("color: #888;")
        self.perf_detected_label.setWordWrap(True)
        perf_layout.addWidget(self.perf_detected_label)

        perf_auto_row = QHBoxLayout()
        self.perf_auto_check = QCheckBox("Auto")
        self.perf_auto_check.toggled.connect(self.on_perf_auto_toggled)
        perf_auto_row.addWidget(self.perf_auto_check)
        perf_auto_row.addStretch()
        perf_layout.addLayout(perf_auto_row)

        perf_row = QHBoxLayout()
        perf_row.addWidget(QLabel("View distance"))
        self.view_spin = QSpinBox()
        perf_row.addWidget(self.view_spin, 1)
        perf_row.addWidget(QLabel("Simulation distance"))
        self.sim_spin = QSpinBox()
        perf_row.addWidget(self.sim_spin, 1)
        perf_save_btn = QPushButton("Save")
        perf_save_btn.setFixedWidth(70)
        perf_save_btn.clicked.connect(self.on_save_perf)
        perf_row.addWidget(perf_save_btn)
        perf_layout.addLayout(perf_row)

        self.perf_msg = QLabel("")
        self.perf_msg.setWordWrap(True)
        self.perf_msg.setStyleSheet("color: #b45309;")
        perf_layout.addWidget(self.perf_msg)
        layout.addWidget(perf_box)

        layout.addStretch()

        self.init_ram_controls()
        self.init_perf_controls()

        self._worker = None
        self._recover_worker = None
        # A single reusable timer, not a fresh QTimer.singleShot(...) per call -
        # start() on an already-running QTimer resets it rather than stacking a
        # second one, so triggering a new action (Start right after Stop, say)
        # while the previous one's message is still showing replaces the pending
        # clear instead of leaving the OLD one to fire on schedule and blank the
        # NEW message several seconds early.
        self._action_msg_clear_timer = QTimer(self)
        self._action_msg_clear_timer.setSingleShot(True)
        self._action_msg_clear_timer.timeout.connect(lambda: self.action_msg.setText(""))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self.refresh()

        # Checked once on startup, not on every poll - a GitHub API call every 4s
        # would be wasteful and risks hitting its rate limit for no benefit.
        self._update_worker = Worker(update_checker.check_latest_release)
        self._update_worker.finished_result.connect(self._on_update_check_done)
        self._update_worker.start()

    def _on_update_check_done(self, result):
        self._pending_update = result
        if self._just_updated_to:
            self.update_box.setVisible(True)
            self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self.update_label.setText(f"Updated to {self._just_updated_to} - all set.")
            self.update_btn.setVisible(False)
            QTimer.singleShot(8000, lambda: self.update_box.setVisible(False))
            return
        if self._last_update_failure:
            self.update_box.setVisible(True)
            self.update_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_label.setText(
                "The last update attempt failed partway through and MCPersist had to be "
                "reopened manually - it's safe to try again."
            )
            self.update_btn.setVisible(True)
            self.update_btn.setText("Try Again")
            # Always enabled here, not just when `result` already found an update -
            # check_latest_release() returns None both when we're genuinely current
            # AND when the check itself failed (network blip, rate limit), so tying
            # this to `result` could leave the one recovery button the user needs
            # disabled for the rest of the session over a transient failure.
            # on_update_now() re-runs the check itself if nothing's pending yet.
            self.update_btn.setEnabled(True)
            self.update_details_btn.setVisible(True)
            return
        if not result:
            return
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.update_label.setText(f"MCPersist {result['version']} is available.")
        self.update_btn.setVisible(True)
        self.update_btn.setText("Update Now")
        self.update_details_btn.setVisible(False)
        self.update_box.setVisible(True)

    def show_update_failure_details(self):
        box = QMessageBox(self)
        box.setWindowTitle("Update Failure Details")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("What the last update attempt actually logged, step by step:")
        box.setDetailedText(self._last_update_failure or "(nothing was logged)")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def on_update_now(self):
        if not self._pending_update:
            # Reachable from the failure-recovery banner, where the button stays
            # enabled even without a pending update (see _on_update_check_done) -
            # re-run the check itself instead of silently doing nothing.
            self.update_btn.setEnabled(False)
            self.update_label.setText("Checking for an update...")
            self._update_worker = Worker(update_checker.check_latest_release)
            self._update_worker.finished_result.connect(self._on_update_check_done)
            self._update_worker.start()
            return
        self.update_btn.setEnabled(False)
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.update_label.setText("Downloading update...")
        self._update_apply_worker = Worker(update_checker.apply_update, self._pending_update["download_url"])
        self._update_apply_worker.finished_result.connect(self._on_update_applied)
        self._update_apply_worker.start()

    def _on_update_applied(self, result):
        if isinstance(result, WorkerError):
            self.update_btn.setEnabled(True)
            self.update_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_label.setText(f"Update failed: {result.message}")
            return
        target_version = self._pending_update["version"]
        update_checker.mark_update_pending(target_version)
        self.update_label.setText(
            f"Updated to {target_version} - the window will close and reopen automatically "
            "in a moment. This is expected, not a crash."
        )
        window = self.window()
        # Without this, quit_app() below fires in the same instant as the label
        # change above, so the label is never actually seen - the whole update
        # just looks like the app closed for no reason. A couple of seconds is
        # enough to register it before the window disappears. apply_update()
        # already extracted and validated the new build into a staging directory
        # before this ever ran, so what happens after quitting is just a fast
        # directory copy + relaunch, not a fresh extraction that could still fail.
        QTimer.singleShot(2500, window.quit_app)

    def current_status(self):
        cfg = config.load()
        server_dir = config.server_dir(cfg)
        if not server_dir or not server_dir.exists():
            return None, cfg, server_dir
        return actions.get_status(cfg, server_dir), cfg, server_dir

    @staticmethod
    def _set_state_label(label, prefix, running):
        color = "#2ecc71" if running else "#e74c3c"
        word = "Running" if running else "Stopped"
        label.setText(f"{prefix}: <span style='color:{color}; font-weight:bold;'>{word}</span>")

    def refresh(self):
        st, cfg, server_dir = self.current_status()
        if st is None:
            self.world_label.setText("No world configured yet")
            self.server_label.setText("Server: -")
            self.tunnel_label.setText("Tunnel: -")
            self.whitelist_label.setText("Whitelist: -")
            self.whitelist_warning.setText("")
            self.address_label.setText("-")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.restart_btn.setEnabled(False)
            return

        self.world_label.setText(f"{st['world_name']} ({st['loader']} {st['mc_version']})")
        self._set_state_label(self.server_label, "Server", st["server_running"])
        self._set_state_label(self.tunnel_label, "Tunnel", st["tunnel_running"])
        self.address_label.setText(st["join_address"] or "(not assigned yet)")

        name_count = len(st["whitelist_names"])
        if st["whitelist_enabled"] is None:
            self.whitelist_label.setText("Whitelist: -")
            self.whitelist_warning.setText("")
        elif st["whitelist_enabled"]:
            self.whitelist_label.setText(
                f"Whitelist: <span style='color:#2ecc71; font-weight:bold;'>ON</span> "
                f"({name_count} player{'s' if name_count != 1 else ''})"
            )
            self.whitelist_warning.setText("")
        else:
            self.whitelist_label.setText(
                "Whitelist: <span style='color:#e74c3c; font-weight:bold;'>OFF</span>"
            )
            self.whitelist_warning.setText(
                "Whitelist is OFF - your server is reachable at a public address, and anyone with a "
                "Minecraft account can join. Run `whitelist on` and `whitelist add <username>` in the "
                "server console or in-game as soon as possible."
            )

        both_up = st["server_running"] and st["tunnel_running"]
        both_down = not st["server_running"] and not st["tunnel_running"]
        self.start_btn.setEnabled(not both_up)
        self.stop_btn.setEnabled(not both_down)
        self.restart_btn.setEnabled(True)

    def copy_address(self):
        text = self.address_label.text()
        if text and text != "-":
            QGuiApplication.clipboard().setText(text)

    def _run_blocking(self, verb, fn, *args):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.restart_btn.setEnabled(False)
        self.action_msg.setText(f"{verb}...")
        self._worker = Worker(fn, *args)
        self._worker.finished_result.connect(lambda results: self._on_action_done(verb, results))
        self._worker.start()

    def _on_action_done(self, verb, results):
        if isinstance(results, WorkerError):
            self.action_msg.setStyleSheet("color: #e74c3c;")
            self.action_msg.setText(f"{verb} failed: {results.message}")
            self.refresh()
            self._action_msg_clear_timer.start(10000)
            return
        all_ok = all(r.ok for r in results)
        lines = [line for r in results for line in r.lines]
        self.action_msg.setStyleSheet("color: #888;" if all_ok else "color: #e74c3c;")
        self.action_msg.setText(" / ".join(lines) if lines else f"{verb} - done.")
        self.refresh()
        self._action_msg_clear_timer.start(4000 if all_ok else 10000)

    def on_start(self):
        _, cfg, server_dir = self.current_status()
        if server_dir is None:
            return

        def do_start():
            return [actions.start_server(cfg, server_dir), actions.start_tunnel(cfg, server_dir)]

        self._run_blocking("Starting", do_start)

    def on_stop(self):
        _, cfg, server_dir = self.current_status()
        if server_dir is None:
            return

        def do_stop():
            return [actions.stop_server(cfg, server_dir), actions.stop_tunnel(server_dir)]

        self._run_blocking("Stopping", do_stop)

    def on_restart(self):
        _, cfg, server_dir = self.current_status()
        if server_dir is None:
            return

        def do_restart():
            return [
                actions.stop_server(cfg, server_dir),
                actions.stop_tunnel(server_dir),
                actions.start_server(cfg, server_dir),
                actions.start_tunnel(cfg, server_dir),
            ]

        self._run_blocking("Restarting", do_restart)

    def open_world_folder(self):
        _, _, server_dir = self.current_status()
        if server_dir is not None:
            _open_folder(server_dir)

    def open_mods_folder(self):
        _, _, server_dir = self.current_status()
        if server_dir is not None:
            mods_dir = server_dir / "mods"
            mods_dir.mkdir(exist_ok=True)
            _open_folder(mods_dir)

    def open_logs(self):
        _, _, server_dir = self.current_status()
        if server_dir is not None:
            _open_folder(server_dir / "logs")

    def on_recover_from_old_install(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select the old MCPersist install folder (or its \"servers\" folder)"
        )
        if not folder:
            return
        self.action_msg.setStyleSheet("color: #888;")
        self.action_msg.setText("Looking for worlds to recover...")
        # Every other worker-launching action (Start/Stop/Restart, Finish Setup)
        # disables its triggering control for the run's duration - this one didn't,
        # so clicking it again mid-scan/copy would overwrite self._recover_worker
        # with a new Worker before the first one (a QThread with no Qt parent) had
        # finished, dropping the only reference still keeping it alive - the same
        # hazard process_manager.py's _detached_procs list exists to avoid for
        # Popen objects, just for a QThread instead.
        self.recover_btn.setEnabled(False)
        self._recover_worker = Worker(setup_flow.import_worlds_from, folder)
        self._recover_worker.finished_result.connect(self._on_recover_done)
        self._recover_worker.start()

    def _on_recover_done(self, result):
        self.recover_btn.setEnabled(True)
        if isinstance(result, WorkerError):
            self.action_msg.setStyleSheet("color: #e74c3c;")
            self.action_msg.setText(f"Recovery failed: {result.message}")
            return
        self.action_msg.setStyleSheet("color: #888;" if result.ok else "color: #e74c3c;")
        self.action_msg.setText("\n".join(result.lines))

    def init_ram_controls(self):
        import psutil

        total_gb = max(1, int(psutil.virtual_memory().total / (1024**3)))
        recommended_gb = config.suggest_memory_mb() // 1024
        self.ram_detected_label.setText(
            f"Detected {total_gb} GB of system RAM - minimum {config.MIN_MEMORY_GB} GB, "
            f"recommended {recommended_gb} GB."
        )
        self.ram_spin.setRange(config.MIN_MEMORY_GB, max(config.MIN_MEMORY_GB, total_gb))

        cfg = config.load()
        is_auto = cfg.get("memory_auto", True)
        self.ram_auto_check.setChecked(is_auto)
        self.ram_spin.setEnabled(not is_auto)

        current_gb = max(config.MIN_MEMORY_GB, round(config.ensure_memory_mb(cfg) / 1024))
        self.ram_spin.setValue(min(current_gb, total_gb))
        self.ram_msg.setText("")

    def on_ram_auto_toggled(self, checked):
        self.ram_spin.setEnabled(not checked)
        if checked:
            recommended_gb = config.suggest_memory_mb() // 1024
            self.ram_spin.setValue(recommended_gb)

    def _restart_hint(self):
        st, _, _ = self.current_status()
        if st and st["server_running"]:
            return " Server is running on the old value - click Restart to apply."
        return " Takes effect next time you start the server."

    def on_save_ram(self):
        import psutil

        total_gb = max(1, int(psutil.virtual_memory().total / (1024**3)))
        is_auto = self.ram_auto_check.isChecked()
        chosen_gb = self.ram_spin.value()

        cfg = config.load()
        cfg["memory_auto"] = is_auto
        cfg["memory_mb"] = chosen_gb * 1024
        config.save(cfg)
        server_dir = config.server_dir(cfg)
        if server_dir:
            setup_flow.write_world_meta(server_dir, cfg)

        if is_auto:
            self.ram_msg.setText(
                f"Saved - auto mode, currently {chosen_gb} GB based on this machine's specs."
                + self._restart_hint()
            )
        elif chosen_gb >= total_gb:
            self.ram_msg.setText(
                f"Saved, but {chosen_gb} GB leaves little to no headroom for the OS on this "
                f"{total_gb} GB machine - the server may struggle or fail to start."
                + self._restart_hint()
            )
        else:
            self.ram_msg.setText("Saved." + self._restart_hint())

    def init_perf_controls(self):
        import psutil

        cores = psutil.cpu_count(logical=True) or 4
        self.perf_detected_label.setText(
            f"Detected {cores} CPU core(s) - recommended view distance "
            f"{config.suggest_view_distance()}, simulation distance {config.suggest_simulation_distance()}."
        )
        self.view_spin.setRange(config.MIN_VIEW_DISTANCE, config.MAX_DISTANCE)
        self.sim_spin.setRange(config.MIN_SIMULATION_DISTANCE, config.MAX_DISTANCE)

        cfg = config.load()
        is_auto = cfg.get("performance_auto", True)
        self.perf_auto_check.setChecked(is_auto)
        self.view_spin.setEnabled(not is_auto)
        self.sim_spin.setEnabled(not is_auto)

        self.view_spin.setValue(config.ensure_view_distance(cfg))
        self.sim_spin.setValue(config.ensure_simulation_distance(cfg))
        self.perf_msg.setText("")

    def on_perf_auto_toggled(self, checked):
        self.view_spin.setEnabled(not checked)
        self.sim_spin.setEnabled(not checked)
        if checked:
            self.view_spin.setValue(config.suggest_view_distance())
            self.sim_spin.setValue(config.suggest_simulation_distance())

    def on_save_perf(self):
        is_auto = self.perf_auto_check.isChecked()
        chosen_view = self.view_spin.value()
        chosen_sim = self.sim_spin.value()

        cfg = config.load()
        cfg["performance_auto"] = is_auto
        cfg["view_distance"] = chosen_view
        cfg["simulation_distance"] = chosen_sim
        config.save(cfg)
        server_dir = config.server_dir(cfg)
        if server_dir:
            setup_flow.write_world_meta(server_dir, cfg)

        if is_auto:
            self.perf_msg.setText(
                f"Saved - auto mode, currently {chosen_view}/{chosen_sim} based on this machine's specs."
                + self._restart_hint()
            )
        else:
            self.perf_msg.setText("Saved." + self._restart_hint())


class SetupPage(QWidget):
    done = Signal()
    cancelled = Signal()

    # Shared across every SetupPage visit within one app run - the version list
    # doesn't change while the app is open, so there's no reason to re-fetch it each
    # time the wizard is opened/closed.
    _version_list_cache = None

    def __init__(self):
        super().__init__()
        self._worker = None
        self.instance_dir = None
        self.world_name = None
        self.mc_version = None
        self.loader = None
        self.is_new_world = False
        self.owner_uuid = None
        self.owner_name = None
        self._version_worker = None
        self._pending_version_selection = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        title = QLabel("Set Up a Server")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        # Step 1 - all three ways to get a server going (pick an existing world,
        # generate a new one, or switch to one already fully set up) are one
        # choice on one page, not two disjoint flows - "Switch" used to be its
        # own separate box below this one, which was exactly backwards from how
        # someone actually thinks about it ("what am I doing?" is one decision,
        # not a decision plus a separate afterthought underneath).
        self.step1_box = QGroupBox("1. Get Started")
        step1_layout = QVBoxLayout(self.step1_box)
        step1_layout.setSpacing(10)

        mode_label = QLabel("What do you want to do?")
        mode_label.setStyleSheet("color: #888;")
        step1_layout.addWidget(mode_label)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        self.mode_existing_radio = QRadioButton("Select Existing World")
        self.mode_new_radio = QRadioButton("Generate New World")
        self.mode_switch_radio = QRadioButton("Switch to a Previously Set-Up Server")
        self.mode_existing_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_existing_radio)
        self.mode_group.addButton(self.mode_new_radio)
        self.mode_group.addButton(self.mode_switch_radio)
        for radio in (self.mode_existing_radio, self.mode_new_radio, self.mode_switch_radio):
            radio.toggled.connect(self.on_step1_mode_changed)
        mode_col.addWidget(self.mode_existing_radio)
        mode_col.addWidget(self.mode_new_radio)
        mode_col.addWidget(self.mode_switch_radio)
        step1_layout.addLayout(mode_col)

        # Existing/New World both need an instance to read from; Switch needs
        # neither (the world's already fully set up the first time around) - so
        # this block and the one below it swap places depending on the mode,
        # instead of the switch fields living in a whole separate box.
        self.instance_fields_box = QWidget()
        instance_fields_layout = QVBoxLayout(self.instance_fields_box)
        instance_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.detected_instances_box = QWidget()
        detected_layout = QVBoxLayout(self.detected_instances_box)
        detected_layout.setContentsMargins(0, 0, 0, 0)
        detected_layout.addWidget(QLabel("Detected Minecraft instances"))
        self.detected_instances_combo = QComboBox()
        self.detected_instances_combo.currentIndexChanged.connect(self.on_detected_instance_selected)
        detected_layout.addWidget(self.detected_instances_combo)
        instance_fields_layout.addWidget(self.detected_instances_box)
        self.detected_instances_box.setVisible(False)

        instance_fields_layout.addWidget(QLabel("Minecraft instance folder"))
        instance_dir_row = QHBoxLayout()
        self.instance_dir_edit = QLineEdit()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.on_browse_instance_dir)
        instance_dir_row.addWidget(self.instance_dir_edit, 1)
        instance_dir_row.addWidget(browse_btn)
        instance_fields_layout.addLayout(instance_dir_row)
        step1_layout.addWidget(self.instance_fields_box)

        self.switch_fields_box = QWidget()
        switch_fields_layout = QVBoxLayout(self.switch_fields_box)
        switch_fields_layout.setContentsMargins(0, 0, 0, 0)
        switch_fields_layout.addWidget(QLabel("Server to switch to"))
        self.switch_world_combo = QComboBox()
        switch_fields_layout.addWidget(self.switch_world_combo)
        self.switch_world_msg = QLabel("Switches immediately - no download or setup needed.")
        self.switch_world_msg.setWordWrap(True)
        self.switch_world_msg.setStyleSheet("color: #888;")
        switch_fields_layout.addWidget(self.switch_world_msg)
        step1_layout.addWidget(self.switch_fields_box)
        self.switch_fields_box.setVisible(False)

        # One button for all three modes - it relabels itself (Find Worlds /
        # Continue / Switch) rather than being a fourth separate control, since
        # there's only ever one meaningful next action for whichever mode is
        # currently selected.
        self.find_or_continue_btn = QPushButton("Find Worlds")
        self.find_or_continue_btn.clicked.connect(self.on_proceed_from_step1)
        step1_layout.addWidget(self.find_or_continue_btn)
        self.step1_msg = QLabel("")
        self.step1_msg.setWordWrap(True)
        step1_layout.addWidget(self.step1_msg)

        layout.addWidget(self.step1_box)

        # Step 2 - just shows whichever content step 1's mode choice calls for; it
        # doesn't offer its own mode choice anymore (that already happened above).
        self.step2_box = QGroupBox("2. World details")
        step2_layout = QVBoxLayout(self.step2_box)

        self.existing_world_box = QWidget()
        existing_layout = QVBoxLayout(self.existing_world_box)
        existing_layout.setContentsMargins(0, 0, 0, 0)
        existing_layout.addWidget(QLabel("World"))
        self.world_combo = QComboBox()
        self.world_combo.currentTextChanged.connect(self.on_world_selected)
        existing_layout.addWidget(self.world_combo)
        step2_layout.addWidget(self.existing_world_box)

        self.new_world_box = QWidget()
        new_layout = QVBoxLayout(self.new_world_box)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.addWidget(QLabel("New world name"))
        self.new_world_name_edit = QLineEdit()
        new_layout.addWidget(self.new_world_name_edit)
        new_layout.addWidget(QLabel("Your Minecraft username (required - the whitelist means nobody, including you, can join without it)"))
        self.owner_username_edit = QLineEdit()
        new_layout.addWidget(self.owner_username_edit)

        # World generation options - only meaningful the first time a world is ever
        # created (an already-generated existing world's terrain/seed can't
        # retroactively change), so these live only in this box, not the
        # existing-world path.
        mode_diff_row = QHBoxLayout()
        mode_diff_row.addWidget(QLabel("Game mode"))
        self.gamemode_combo = QComboBox()
        self.gamemode_combo.addItems(["Survival", "Creative", "Adventure", "Spectator"])
        mode_diff_row.addWidget(self.gamemode_combo, 1)
        mode_diff_row.addWidget(QLabel("Difficulty"))
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems(["Peaceful", "Easy", "Normal", "Hard"])
        self.difficulty_combo.setCurrentText("Easy")
        mode_diff_row.addWidget(self.difficulty_combo, 1)
        new_layout.addLayout(mode_diff_row)

        new_layout.addWidget(QLabel("World type"))
        self.level_type_combo = QComboBox()
        for label, value in setup_flow.LEVEL_TYPES.items():
            self.level_type_combo.addItem(label, value)
        new_layout.addWidget(self.level_type_combo)

        structures_prot_row = QHBoxLayout()
        self.generate_structures_check = QCheckBox("Generate structures")
        self.generate_structures_check.setChecked(True)
        structures_prot_row.addWidget(self.generate_structures_check)
        structures_prot_row.addWidget(QLabel("Spawn protection"))
        self.spawn_protection_spin = QSpinBox()
        self.spawn_protection_spin.setRange(0, 500)
        self.spawn_protection_spin.setValue(16)
        structures_prot_row.addWidget(self.spawn_protection_spin, 1)
        new_layout.addLayout(structures_prot_row)

        new_layout.addWidget(QLabel("Seed (optional - leave blank for random)"))
        self.seed_edit = QLineEdit()
        new_layout.addWidget(self.seed_edit)

        step2_layout.addWidget(self.new_world_box)
        self.new_world_box.setVisible(False)

        self.version_loader_box = QWidget()
        version_loader_layout = QVBoxLayout(self.version_loader_box)
        version_loader_layout.setContentsMargins(0, 0, 0, 0)
        version_loader_layout.addWidget(QLabel("Minecraft version"))
        self.mc_version_combo = QComboBox()
        version_loader_layout.addWidget(self.mc_version_combo)
        loader_row = QHBoxLayout()
        self.vanilla_radio = QRadioButton("Vanilla")
        self.fabric_radio = QRadioButton("Fabric")
        self.forge_radio = QRadioButton("Forge")
        loader_row.addWidget(self.vanilla_radio)
        loader_row.addWidget(self.fabric_radio)
        loader_row.addWidget(self.forge_radio)
        version_loader_layout.addLayout(loader_row)
        step2_layout.addWidget(self.version_loader_box)

        self.loader_warning = QLabel("")
        self.loader_warning.setWordWrap(True)
        self.loader_warning.setStyleSheet("color: #b45309;")
        step2_layout.addWidget(self.loader_warning)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.clicked.connect(self.on_prepare_world)
        step2_layout.addWidget(self.continue_btn)
        self.step2_box.setVisible(False)
        layout.addWidget(self.step2_box)

        # Step 3
        self.step3_box = QGroupBox("3. Owner & whitelist")
        step3_layout = QVBoxLayout(self.step3_box)
        self.prepare_msg = QTextEdit()
        self.prepare_msg.setReadOnly(True)
        self.prepare_msg.setFixedHeight(160)
        step3_layout.addWidget(self.prepare_msg)
        self.finish_btn = QPushButton("Finish Setup")
        self.finish_btn.clicked.connect(self.on_finish_setup)
        step3_layout.addWidget(self.finish_btn)
        self.step3_box.setVisible(False)
        layout.addWidget(self.step3_box)

        # Step 4
        self.step4_box = QGroupBox("4. Done")
        step4_layout = QVBoxLayout(self.step4_box)
        self.finish_msg = QTextEdit()
        self.finish_msg.setReadOnly(True)
        self.finish_msg.setFixedHeight(120)
        step4_layout.addWidget(self.finish_msg)
        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.done.emit)
        step4_layout.addWidget(done_btn)
        self.step4_box.setVisible(False)
        layout.addWidget(self.step4_box)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancelled.emit)
        layout.addWidget(cancel_btn)
        layout.addStretch()

    def _resize_window_to_fit(self):
        # Each step shows/hides a different set of boxes, so the window's ideal
        # height changes as the wizard progresses - MainWindow.fit_to_current_page
        # re-measures whichever page is current, but that only runs when the
        # stack itself switches pages, not on these in-page step transitions, so
        # it has to be poked here too. Guarded since this page can also be driven
        # standalone (outside a real MainWindow) during testing.
        window = self.window()
        if hasattr(window, "fit_to_current_page"):
            window.fit_to_current_page()

    def reset(self):
        instances = setup_flow.find_instances()
        self.detected_instances_combo.blockSignals(True)
        self.detected_instances_combo.clear()
        for instance in instances:
            self.detected_instances_combo.addItem(instance["name"], instance["path"])
        self.detected_instances_combo.blockSignals(False)
        self.detected_instances_box.setVisible(bool(instances))

        # Prefill from the first detected instance if there is one - still just a
        # starting point, the field underneath stays a plain editable text box for
        # anything not auto-detected (a different account, an instance outside the
        # launchers this looks for, etc).
        prefill = instances[0]["path"] if instances else setup_flow.default_instance_dir()
        self.instance_dir_edit.setText(prefill)
        self.step1_msg.setText("")
        self.new_world_name_edit.setText("")
        self.owner_username_edit.setText("")
        self.gamemode_combo.setCurrentIndex(0)  # Survival
        self.difficulty_combo.setCurrentText("Easy")
        self.level_type_combo.setCurrentIndex(0)  # Default
        self.generate_structures_check.setChecked(True)
        self.spawn_protection_spin.setValue(16)
        self.seed_edit.setText("")

        # "Switch to a Previously Set-Up Server" doesn't need an instance dir at
        # all - it's ready the moment the wizard opens rather than waiting on
        # Find Worlds - but the option itself only makes sense, and only shows
        # up, when there's actually something to switch to.
        known_servers = self._populate_switch_world_combo()
        self.mode_switch_radio.setVisible(bool(known_servers))

        self.mode_existing_radio.setChecked(True)
        self.on_step1_mode_changed()

        # Cleared explicitly, because this page is one long-lived instance rather
        # than a fresh widget per run (see tray_app.py) - anything left here carries
        # into the next setup. An owner resolved for a world the user then backed
        # out of would otherwise still be sitting here, ready to be written into a
        # different world's whitelist.json/ops.json.
        self.world_name = None
        self.owner_uuid = None
        self.owner_name = None
        self.is_new_world = False
        self._worker = None
        self.prepare_msg.setText("")
        self.finish_msg.setText("")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setText("Finish Setup")

        self.step1_box.setVisible(True)
        self.step2_box.setVisible(False)
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(False)
        self._resize_window_to_fit()

    def on_detected_instance_selected(self, index):
        path = self.detected_instances_combo.itemData(index)
        if path:
            self.instance_dir_edit.setText(path)

    def on_browse_instance_dir(self):
        start_dir = self.instance_dir_edit.text() or str(BASE_DIR)
        chosen = QFileDialog.getExistingDirectory(self, "Select Minecraft instance folder", start_dir)
        if chosen:
            self.instance_dir_edit.setText(chosen)

    def on_step1_mode_changed(self):
        """Relabels step 1's single action button and swaps its input fields to
        match whichever of the three modes is picked - none of the actual work
        (listing worlds, detecting loader info, switching) happens until it's
        actually clicked, in on_proceed_from_step1."""
        is_switch = self.mode_switch_radio.isChecked()
        is_new = self.mode_new_radio.isChecked()
        self.instance_fields_box.setVisible(not is_switch)
        self.switch_fields_box.setVisible(is_switch)
        if is_switch:
            self.find_or_continue_btn.setText("Switch")
        elif is_new:
            self.find_or_continue_btn.setText("Continue")
        else:
            self.find_or_continue_btn.setText("Find Worlds")

    def on_proceed_from_step1(self):
        if self.mode_switch_radio.isChecked():
            self.on_switch_to_server()
            return

        instance_dir = self.instance_dir_edit.text()
        result = setup_flow.list_worlds(instance_dir)
        if not result.ok:
            self.step1_msg.setText("\n".join(result.lines))
            return
        self.instance_dir = instance_dir

        if self.mode_new_radio.isChecked():
            # Generating a new world never needed the worlds list at all - only the
            # instance dir itself, for loader detection/mod copying later - so
            # there's nothing further to check here now that the dir's confirmed
            # to exist.
            self.step1_msg.setText("")
            self.on_mode_changed()
            self.step1_box.setVisible(False)
            self.step2_box.setVisible(True)
            self._resize_window_to_fit()
            return

        worlds = result.data["worlds"]
        if not worlds:
            self.step1_msg.setText(
                f'No existing worlds found under "{instance_dir}\\saves" - '
                'pick "Generate New World" above instead.'
            )
            return
        self.step1_msg.setText("\n".join(result.lines))
        self.world_combo.clear()
        self.world_combo.addItems(worlds)
        self.on_mode_changed()
        self.step1_box.setVisible(False)
        self.step2_box.setVisible(True)
        self._resize_window_to_fit()

    def on_mode_changed(self):
        is_existing = self.mode_existing_radio.isChecked()
        is_new = self.mode_new_radio.isChecked()

        self.existing_world_box.setVisible(is_existing)
        self.new_world_box.setVisible(is_new)

        if is_existing:
            self.on_world_selected(self.world_combo.currentText())
        elif is_new and self.instance_dir:
            info = setup_flow.detect_new_world_info(self.instance_dir)
            self._apply_detected_loader(info)

    def _populate_switch_world_combo(self):
        """Returns the known-servers list too, so reset() doesn't need a second,
        redundant scan of servers/ just to decide whether to show this section."""
        self.switch_world_combo.clear()
        servers = setup_flow.list_known_servers()
        if not servers:
            self.switch_world_combo.setEnabled(False)
            return servers
        self.switch_world_combo.setEnabled(True)
        for server in servers:
            label = f"{server['world_name']} ({server['loader']} {server['mc_version']})"
            self.switch_world_combo.addItem(label, server["world_name"])
        return servers

    def _apply_detected_loader(self, info):
        suggested = info["suggested_loader"]
        self._select_version(info["mc_version"])
        self.vanilla_radio.setChecked(suggested == "vanilla")
        self.fabric_radio.setChecked(suggested == "fabric")
        self.forge_radio.setChecked(suggested == "forge")
        if suggested == "neoforge":
            self.loader_warning.setText(
                "Detected NeoForge, which isn't supported yet - only Vanilla, Fabric, and Forge "
                "servers can be set up right now. Pick one below, but the world may not run "
                "correctly without its actual mod loader."
            )
        else:
            self.loader_warning.setText("")

    def _select_version(self, detected_version):
        """Populates (and caches) the version dropdown from Mojang's manifest, then
        selects detected_version in it if present. Fetching happens once per app run
        and in the background - switching worlds/modes while it's still loading just
        updates which version gets selected once it finishes."""
        self._pending_version_selection = detected_version
        # Only a non-empty cache counts as "already fetched" - an empty list means
        # the last attempt failed (list_release_versions() returns [] rather than
        # raising), and should be retried rather than treated as a permanent result
        # for the rest of the session.
        if SetupPage._version_list_cache:
            self._populate_version_combo(SetupPage._version_list_cache)
            return
        if self._version_worker is not None:
            return
        self.mc_version_combo.clear()
        self.mc_version_combo.addItem("Loading versions...")
        self.mc_version_combo.setEnabled(False)
        self._version_worker = Worker(server_vanilla.list_release_versions)
        self._version_worker.finished_result.connect(self._on_version_list_loaded)
        self._version_worker.start()

    def _on_version_list_loaded(self, versions):
        self._version_worker = None
        if versions:
            SetupPage._version_list_cache = versions
        self._populate_version_combo(versions or [])

    def _populate_version_combo(self, versions):
        self.mc_version_combo.setEnabled(True)
        self.mc_version_combo.clear()
        if not versions:
            # Offline / the fetch failed - fall back to whatever was already
            # detected (if anything) so setup can still proceed without network
            # access to Mojang's manifest, rather than leaving an empty dropdown.
            self.mc_version_combo.addItem(self._pending_version_selection or "1.21")
            return
        self.mc_version_combo.addItems(versions)
        if self._pending_version_selection and self._pending_version_selection in versions:
            self.mc_version_combo.setCurrentText(self._pending_version_selection)

    def on_world_selected(self, world_name):
        if not world_name:
            return
        info = setup_flow.detect_world_info(self.instance_dir, world_name)
        self._apply_detected_loader(info)

    def on_switch_to_server(self):
        world_name = self.switch_world_combo.currentData()
        if not world_name:
            return
        self.step1_box.setVisible(False)
        self.step4_box.setVisible(True)
        self._resize_window_to_fit()
        self.finish_msg.setText("Switching...")
        self._worker = Worker(setup_flow.switch_to_world, world_name)
        self._worker.finished_result.connect(self._on_switch_done)
        self._worker.start()

    def on_prepare_world(self):
        self.mc_version = self.mc_version_combo.currentText()
        if self.forge_radio.isChecked():
            self.loader = "forge"
        elif self.fabric_radio.isChecked():
            self.loader = "fabric"
        else:
            self.loader = "vanilla"
        generate_new = self.mode_new_radio.isChecked()
        self.is_new_world = generate_new

        if not self.mc_version_combo.isEnabled() or not self.mc_version:
            self.step1_msg.setText("")
            self.loader_warning.setText("Still loading the version list - wait a moment and try again.")
            return

        owner_username = None
        if generate_new:
            self.world_name = self.new_world_name_edit.text().strip()
            if not setup_flow.valid_new_world_name(self.world_name):
                self.step1_msg.setText("")
                self.loader_warning.setText('Please enter a valid world name (no \\ / : * ? " < > |).')
                return
            owner_username = self.owner_username_edit.text().strip()
            if not owner_username:
                self.step1_msg.setText("")
                self.loader_warning.setText(
                    "A Minecraft username is required - nobody can join a whitelisted server without one."
                )
                return
        else:
            self.world_name = self.world_combo.currentText()

        self.step2_box.setVisible(False)
        self.step3_box.setVisible(True)
        self._resize_window_to_fit()
        self.prepare_msg.setText("Preparing...")
        self.finish_btn.setEnabled(False)

        if generate_new:
            self._worker = Worker(setup_flow.prepare_new_world, self.instance_dir, self.world_name, owner_username)
        else:
            self._worker = Worker(setup_flow.prepare_world, self.instance_dir, self.world_name)
        self._worker.finished_result.connect(self._on_prepare_done)
        self._worker.start()

    def _on_prepare_done(self, result):
        # SetupPage is one long-lived instance reused via reset(), and a worker from
        # an abandoned run (cancelled mid-copy, then a different world started) still
        # delivers its result when it eventually finishes. Without this guard, a slow
        # earlier run landing after a later one would overwrite owner_uuid/owner_name
        # with the wrong world's owner - whitelisting and opping the world now being
        # set up to whoever owned the one that was abandoned.
        if self.sender() is not self._worker:
            return
        if isinstance(result, WorkerError):
            self.prepare_msg.setText(f"Failed: {result.message}")
            self.finish_btn.setEnabled(False)
            return
        self.prepare_msg.setText("\n".join(result.lines))
        # Enabled only when prepare actually SUCCEEDED. Checking just for WorkerError
        # missed ordinary ok=False results - most importantly "servers\<name> already
        # exists - pick a different world name", which is one line of text next to a
        # live button. Clicking Finish anyway ran finish_setup against that existing
        # server: regenerating its server.properties from the new-world options and,
        # because a failed prepare resolves no owner, rewriting it with
        # white-list=false - quietly turning an established, whitelisted server into
        # a publicly joinable one (see the disclaimer prepare_world itself prints).
        if not result.ok:
            self.owner_uuid = self.owner_name = None
            self.finish_btn.setEnabled(False)
            return
        self.owner_uuid = result.data.get("owner_uuid")
        self.owner_name = result.data.get("owner_name")
        self.finish_btn.setEnabled(True)

    def _on_switch_done(self, result):
        if isinstance(result, WorkerError):
            self.finish_msg.setText(f"Failed: {result.message}")
            return
        self.finish_msg.setText("\n".join(result.lines))

    def on_finish_setup(self):
        self.finish_btn.setEnabled(False)
        self.finish_btn.setText("Setting up...")

        world_options = None
        if self.is_new_world:
            world_options = {
                "gamemode": self.gamemode_combo.currentText().lower(),
                "difficulty": self.difficulty_combo.currentText().lower(),
                "level-type": self.level_type_combo.currentData(),
                "generate-structures": "true" if self.generate_structures_check.isChecked() else "false",
                "spawn-protection": str(self.spawn_protection_spin.value()),
            }
            # A raw newline in the seed would inject an extra line into
            # server.properties, same class of issue valid_new_world_name already
            # guards against for the world name - strip it down to one line.
            seed = self.seed_edit.text().strip().splitlines()
            if seed and seed[0]:
                world_options["level-seed"] = seed[0]

        self._worker = Worker(
            setup_flow.finish_setup,
            self.instance_dir,
            self.world_name,
            self.mc_version,
            self.loader,
            self.owner_uuid,
            self.owner_name,
            copy_mods=not self.is_new_world,
            world_options=world_options,
        )
        self._worker.finished_result.connect(self._on_finish_done)
        self._worker.start()

    def _on_finish_done(self, result):
        self.finish_btn.setEnabled(True)
        self.finish_btn.setText("Finish Setup")
        if isinstance(result, WorkerError):
            self.prepare_msg.setText(f"Setup failed: {result.message}")
            return
        self.finish_msg.setText("\n".join(result.lines))
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(True)
        self._resize_window_to_fit()


