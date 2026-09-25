"""The GUI's screens (status, setup wizard) as PySide6 widgets - all logic delegates
to actions.py/setup_flow.py, same as the CLI."""

import os
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

from . import actions, config, server_vanilla, setup_flow, update_checker, world
from .gui_worker import Worker, error_text
from .paths import BASE_DIR
from .version import VERSION


def _open_folder(path):
    subprocess.Popen(["explorer", str(path)])


MUTED = "color: #888;"
WARN = "color: #b45309;"


def _label(text="", style="", wrap=False):
    label = QLabel(text)
    label.setStyleSheet(style)
    label.setWordWrap(wrap)
    return label


def _start_steps(cfg, server_dir):
    return [actions.start_server(cfg, server_dir), actions.start_tunnel(cfg, server_dir)]


def _stop_steps(cfg, server_dir):
    return [actions.stop_server(cfg, server_dir), actions.stop_tunnel(server_dir)]


def _fit_window(page):
    """Re-fits the window after a page shows, hides or rewords something - the
    window only re-measures on its own when switching pages. Guarded so a page can
    run standalone (outside MainWindow) in tests."""
    window = page.window()
    if hasattr(window, "fit_to_current_page"):
        window.fit_to_current_page()


class StatusPage(QWidget):
    go_to_setup = Signal()

    def sizeHint(self):
        # The content's natural size, not QScrollArea's small generic default -
        # fit_to_current_page sizes the window from it.
        return self._content.sizeHint()

    def minimumSizeHint(self):
        # The content's minimum width, so the window can't be dragged narrower than the
        # button rows need - but a low height, so the scroll area can still make the
        # window shorter than its content.
        return QSize(self._content.minimumSizeHint().width(), 200)

    def __init__(self):
        super().__init__()
        # Everything sits in a QScrollArea: the window's height is capped, so content
        # taller than that (a long start/stop message, a big font or DPI) scrolls
        # instead of being clipped.
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
        self.update_label = _label(style="color: #2ecc71; font-weight: bold;")
        update_row.addWidget(self.update_label, 1)
        self.update_btn = QPushButton("Update Now")
        self.update_btn.setFixedWidth(110)
        self.update_btn.clicked.connect(self.on_update_now)
        update_row.addWidget(self.update_btn)
        # Shown with the failure banner: what the updater actually logged, instead of
        # asking someone to find a temp log file.
        self.update_details_btn = QPushButton("Details")
        self.update_details_btn.setFixedWidth(70)
        self.update_details_btn.clicked.connect(self.show_update_failure_details)
        self.update_details_btn.setVisible(False)
        update_row.addWidget(self.update_details_btn)
        self.update_box.setVisible(False)
        layout.addWidget(self.update_box)
        self._pending_update = None
        # A leftover log means the detached updater failed after we quit - report it
        # once, up front.
        self._last_update_failure = update_checker.check_last_update_failure()
        # And the success case, so a completed update doesn't look like the app just
        # closed.
        self._just_updated_to = update_checker.check_update_success()

        # ----- Status group -----
        status_box = QGroupBox()
        status_layout = QVBoxLayout(status_box)

        self.world_label = _label("No world configured yet", style="font-weight: bold; font-size: 16px;")
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
        self.address_hint = _label(style=WARN, wrap=True)
        self.address_hint.setVisible(False)
        status_layout.addWidget(self.address_hint)

        # The whitelist is just a status and warning here - managing it is Minecraft
        # commands.
        self.whitelist_warning = _label(style="color: #e74c3c; font-weight: bold;", wrap=True)
        status_layout.addWidget(self.whitelist_warning)
        whitelist_hint = _label("Friends can't join? Use /whitelist commands in Minecraft.", style=MUTED)
        status_layout.addWidget(whitelist_hint)

        layout.addWidget(status_box)

        # ----- Primary actions -----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.restart_btn = QPushButton("Restart")
        for b, verb, steps in (
            (self.start_btn, "Starting", _start_steps),
            (self.stop_btn, "Stopping", _stop_steps),
            (self.restart_btn, "Restarting", lambda cfg, d: _stop_steps(cfg, d) + _start_steps(cfg, d)),
        ):
            b.clicked.connect(lambda _=False, verb=verb, steps=steps: self._run_on_world(verb, steps))
            b.setMinimumHeight(34)
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        # Wrapped: start/stop results include full log paths, which otherwise stretch
        # the window wider for good.
        self.action_msg = _label(style=MUTED, wrap=True)
        self.action_msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.action_msg)

        # ----- World group -----
        world_box = QGroupBox("World")
        world_layout = QVBoxLayout(world_box)
        setup_btn = QPushButton("Set Up a Server")
        setup_btn.clicked.connect(self.go_to_setup.emit)
        world_layout.addWidget(setup_btn)

        # Recovers worlds from an old install folder (say, after a manual reinstall).
        # Each world folder is self-contained, so it's a folder copy; "Switch to a
        # Previously Set-Up Server" makes one active.
        self.recover_btn = QPushButton("Recover Worlds from Old Install...")
        self.recover_btn.clicked.connect(self.on_recover_from_old_install)
        world_layout.addWidget(self.recover_btn)

        folder_row = QHBoxLayout()
        for label, sub in (("Server Folder", ""), ("Mods Folder", "mods"), ("View Logs", "logs")):
            folder_btn = QPushButton(label)
            folder_btn.clicked.connect(lambda _=False, sub=sub: self._open_server_subdir(sub))
            folder_row.addWidget(folder_btn)
        world_layout.addLayout(folder_row)
        layout.addWidget(world_box)

        # ----- Memory group -----
        memory_box = QGroupBox("Memory")
        memory_layout = QVBoxLayout(memory_box)

        self.ram_detected_label = _label(style=MUTED, wrap=True)
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

        self.ram_msg = _label(style=WARN, wrap=True)
        memory_layout.addWidget(self.ram_msg)
        layout.addWidget(memory_box)

        # ----- Performance group -----
        perf_box = QGroupBox("Performance")
        perf_layout = QVBoxLayout(perf_box)

        self.perf_detected_label = _label(style=MUTED, wrap=True)
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

        self.perf_msg = _label(style=WARN, wrap=True)
        perf_layout.addWidget(self.perf_msg)
        layout.addWidget(perf_box)

        layout.addStretch()
        version_label = _label(f"MCPersist v{VERSION}", style=MUTED)
        version_label.setAlignment(Qt.AlignRight)
        layout.addWidget(version_label)

        self.init_ram_controls()
        self.init_perf_controls()

        self._worker = None
        self._recover_worker = None
        # One reusable timer: start() resets it, so a new message isn't blanked early by
        # the previous one's pending clear.
        self._action_msg_clear_timer = QTimer(self)
        self._action_msg_clear_timer.setSingleShot(True)
        self._action_msg_clear_timer.timeout.connect(lambda: self.action_msg.setText(""))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self.refresh()

        # Checked once on startup, not on every poll - a GitHub API call every 4s
        # would be wasteful and risks hitting its rate limit for no benefit.
        self._check_for_update()

    def _check_for_update(self):
        self._update_worker = Worker(update_checker.check_latest_release)
        self._update_worker.finished_result.connect(self._on_update_check_done)
        self._update_worker.start()

    def _hide_update_box(self):
        self.update_box.setVisible(False)
        _fit_window(self)

    def _on_update_check_done(self, result):
        self._pending_update = result
        if self._just_updated_to:
            self.update_box.setVisible(True)
            self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self.update_label.setText(f"Updated to {self._just_updated_to} - all set.")
            self.update_btn.setVisible(False)
            _fit_window(self)
            QTimer.singleShot(8000, self._hide_update_box)
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
            # Always enabled: None also means the check itself failed, and this is the
            # one recovery button. on_update_now() re-checks if nothing's pending.
            self.update_btn.setEnabled(True)
            self.update_details_btn.setVisible(True)
            _fit_window(self)
            return
        if not result:
            return
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.update_label.setText(f"MCPersist {result['version']} is available.")
        self.update_btn.setVisible(True)
        self.update_btn.setText("Update Now")
        self.update_details_btn.setVisible(False)
        self.update_box.setVisible(True)
        _fit_window(self)

    def show_update_failure_details(self):
        box = QMessageBox(self)
        box.setWindowTitle("Update Failure Details")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("What the last update attempt actually logged, step by step:")
        box.setDetailedText(self._last_update_failure or "(nothing was logged)")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def on_update_now(self):
        # Refused while the tunnel runs: it's MCPersist.exe itself and holds the .exe
        # and _internal DLLs open, so the updater's copy would stop partway and leave an
        # install that won't launch. Stopping someone's world automatically isn't ours
        # to decide. The server (java.exe) holds nothing of ours, so updating with just
        # the server up is safe.
        st, _, _ = self.current_status()
        if st and st["tunnel_running"]:
            self.update_label.setStyleSheet("color: #b45309; font-weight: bold;")
            self.update_label.setText(
                "Stop the tunnel first, then update - the tunnel runs MCPersist.exe itself, "
                "so it holds the file the update needs to replace."
            )
            self.update_btn.setEnabled(True)
            _fit_window(self)
            return
        if not self._pending_update:
            # From the failure banner, with no update pending - re-run the check instead
            # of doing nothing.
            self.update_btn.setEnabled(False)
            self.update_label.setText("Checking for an update...")
            _fit_window(self)
            self._check_for_update()
            return
        self.update_btn.setEnabled(False)
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.update_label.setText("Downloading update...")
        _fit_window(self)
        self._update_apply_worker = Worker(update_checker.apply_update, self._pending_update["download_url"])
        self._update_apply_worker.finished_result.connect(self._on_update_applied)
        self._update_apply_worker.start()

    def _on_update_applied(self, result):
        if isinstance(result, Exception):
            self.update_btn.setEnabled(True)
            self.update_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_label.setText(f"Update failed: {error_text(result)}")
            _fit_window(self)
            return
        target_version = self._pending_update["version"]
        update_checker.mark_update_pending(target_version)
        self.update_label.setText(
            f"Updated to {target_version} - the window will close and reopen automatically "
            "in a moment. This is expected, not a crash."
        )
        # Two lines of text - the message read while the app closes.
        _fit_window(self)
        window = self.window()
        # Give the message time to be read before quitting; the update is already staged
        # and validated, so what follows is a quick copy and relaunch.
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
            self.address_hint.setVisible(False)
            self.address_hint.setText("")
            self._set_action_buttons(False, False, False)
            return

        self.world_label.setText(f"{st['world_name']} ({st['loader']} {st['mc_version']})")
        self._set_state_label(self.server_label, "Server", st["server_running"])
        self._set_state_label(self.tunnel_label, "Tunnel", st["tunnel_running"])
        self.address_label.setText(st["join_address"] or "(not assigned yet)")
        if st["join_address"] and not st.get("tunnel_problem"):
            hint = ""
        elif not st["tunnel_running"]:
            hint = "Your address is assigned once the tunnel is running - press Start."
        elif st.get("tunnel_problem"):
            problem = st["tunnel_problem"]
            # A refusal means the relay WAS reached, so firewall advice would send
            # the user after the wrong thing.
            if "already connected from this address" in problem:
                hint = (
                    "The relay allows one tunnel per internet connection, and another MCPersist "
                    "tunnel on your network is already connected. Stop that one first."
                )
            elif problem.startswith("registration failed"):
                hint = f"The relay refused this tunnel: {problem}"
            else:
                hint = (
                    f"Can't reach the relay: {problem}\n"
                    "If this keeps happening, a firewall or antivirus may be blocking MCPersist.exe."
                )
        else:
            hint = "Connecting to the relay..."
        if hint != self.address_hint.text():
            self.address_hint.setText(hint)
            self.address_hint.setVisible(bool(hint))
            _fit_window(self)

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

        # The 4s poll lands mid-action (a start can take ~12s, far longer if Java is
        # downloading) - don't re-enable the buttons then.
        if self._action_running():
            self._set_action_buttons(False, False, False)
            return
        both_up = st["server_running"] and st["tunnel_running"]
        both_down = not st["server_running"] and not st["tunnel_running"]
        self._set_action_buttons(not both_up, not both_down, True)

    def _set_action_buttons(self, start, stop, restart):
        self.start_btn.setEnabled(start)
        self.stop_btn.setEnabled(stop)
        self.restart_btn.setEnabled(restart)

    def copy_address(self):
        text = self.address_label.text()
        if text and text != "-":
            QGuiApplication.clipboard().setText(text)

    def _action_running(self):
        # A flag, not _worker.isRunning(): the result signal fires just before the
        # thread exits.
        return getattr(self, "_action_in_progress", False)

    def _run_blocking(self, verb, fn, *args):
        # Replacing a running Worker destroys a live QThread (Qt aborts) and runs two
        # start/stop sequences at once.
        if self._action_running():
            return
        self._action_in_progress = True
        self._set_action_buttons(False, False, False)
        self.action_msg.setText(f"{verb}...")
        self._worker = Worker(fn, *args)
        self._worker.finished_result.connect(lambda results: self._on_action_done(verb, results))
        self._worker.start()

    def _on_action_done(self, verb, results):
        self._action_in_progress = False
        if isinstance(results, Exception):
            self.action_msg.setStyleSheet("color: #e74c3c;")
            self.action_msg.setText(f"{verb} failed: {error_text(results)}")
            self.refresh()
            self._action_msg_clear_timer.start(10000)
            return
        all_ok = all(r.ok for r in results)
        lines = [line for r in results for line in r.lines]
        self.action_msg.setStyleSheet("color: #888;" if all_ok else "color: #e74c3c;")
        self.action_msg.setText(" / ".join(lines) if lines else f"{verb} - done.")
        self.refresh()
        self._action_msg_clear_timer.start(4000 if all_ok else 10000)

    def _run_on_world(self, verb, steps):
        _, cfg, server_dir = self.current_status()
        if server_dir is not None:
            self._run_blocking(verb, steps, cfg, server_dir)

    def _open_server_subdir(self, sub):
        _, _, server_dir = self.current_status()
        if server_dir is not None:
            (server_dir / sub).mkdir(exist_ok=True)
            _open_folder(server_dir / sub)

    def on_recover_from_old_install(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select the old MCPersist install folder (or its \"servers\" folder)"
        )
        if not folder:
            return
        self.action_msg.setStyleSheet("color: #888;")
        self.action_msg.setText("Looking for worlds to recover...")
        # Disabled while running, like every other worker-launching control: replacing a
        # running Worker would destroy a live QThread.
        self.recover_btn.setEnabled(False)
        self._recover_worker = Worker(setup_flow.import_worlds_from, folder)
        self._recover_worker.finished_result.connect(self._on_recover_done)
        self._recover_worker.start()

    def _on_recover_done(self, result):
        self.recover_btn.setEnabled(True)
        if isinstance(result, Exception):
            self.action_msg.setStyleSheet("color: #e74c3c;")
            self.action_msg.setText(f"Recovery failed: {error_text(result)}")
            return
        self.action_msg.setStyleSheet("color: #888;" if result.ok else "color: #e74c3c;")
        self.action_msg.setText("\n".join(result.lines))

    def init_ram_controls(self):
        total_gb = config.total_ram_gb()
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
        total_gb = config.total_ram_gb()
        is_auto = self.ram_auto_check.isChecked()
        chosen_gb = self.ram_spin.value()

        cfg = config.load()
        cfg["memory_auto"] = is_auto
        cfg["memory_mb"] = chosen_gb * 1024
        config.save(cfg)
        # exists(), not just a world name: server_dir() doesn't check the folder is
        # there, and a missing one made write_world_meta raise after the config was
        # already saved.
        server_dir = config.server_dir(cfg)
        if server_dir and server_dir.exists():
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
        cores = os.cpu_count() or 4
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
        if server_dir and server_dir.exists():  # see on_save_ram
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

    # Shared across wizard visits - the list doesn't change while the app runs.
    _version_list_cache = None

    def __init__(self):
        super().__init__()
        self._worker = None
        # Workers from abandoned runs still going when the wizard was re-entered - kept
        # referenced (destroying a running QThread crashes) and visible to
        # MainWindow._pending_workers, so quitting waits for them. See reset().
        self._stale_workers = []
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
        title = _label("Set Up a Server", style="font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        # Step 1 - pick an existing world, generate a new one, or switch to one already
        # set up: one choice.
        self.step1_box = QGroupBox("1. Get Started")
        step1_layout = QVBoxLayout(self.step1_box)
        step1_layout.setSpacing(10)

        mode_label = _label("What do you want to do?", style=MUTED)
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

        # Existing and new worlds need an instance; switching doesn't - so the fields
        # swap by mode.
        self.instance_fields_box = QWidget()
        instance_fields_layout = QVBoxLayout(self.instance_fields_box)
        instance_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.detected_instances_box = QWidget()
        detected_layout = QVBoxLayout(self.detected_instances_box)
        detected_layout.setContentsMargins(0, 0, 0, 0)
        # Reworded per mode in on_step1_mode_changed, since the instance is used
        # differently in each.
        self.detected_instances_label = QLabel("Detected Minecraft instances")
        detected_layout.addWidget(self.detected_instances_label)
        self.detected_instances_combo = QComboBox()
        self.detected_instances_combo.currentIndexChanged.connect(self.on_detected_instance_selected)
        detected_layout.addWidget(self.detected_instances_combo)
        instance_fields_layout.addWidget(self.detected_instances_box)
        self.detected_instances_box.setVisible(False)

        self.instance_dir_label = QLabel("Minecraft instance folder")
        instance_fields_layout.addWidget(self.instance_dir_label)
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
        self.switch_world_msg = _label("Switches immediately - no download or setup needed.", style=MUTED, wrap=True)
        switch_fields_layout.addWidget(self.switch_world_msg)
        step1_layout.addWidget(self.switch_fields_box)
        self.switch_fields_box.setVisible(False)

        # One button for all three modes, relabelled to match (Find Worlds / Continue /
        # Switch).
        self.find_or_continue_btn = QPushButton("Find Worlds")
        self.find_or_continue_btn.clicked.connect(self.on_proceed_from_step1)
        step1_layout.addWidget(self.find_or_continue_btn)
        self.step1_msg = _label(wrap=True)
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
        # Wrapped, or its ~526px width becomes the window's minimum for good.
        new_layout.addWidget(
            _label(
                "Your Minecraft username (required - the whitelist means nobody, including you, "
                "can join without it)",
                wrap=True,
            )
        )
        self.owner_username_edit = QLineEdit()
        new_layout.addWidget(self.owner_username_edit)

        # World generation options - only for a brand-new world.
        mode_diff_row = QHBoxLayout()
        mode_diff_row.addWidget(QLabel("Game mode"))
        self.gamemode_combo = QComboBox()
        self.gamemode_combo.addItems([g.capitalize() for g in setup_flow.GAMEMODES])
        mode_diff_row.addWidget(self.gamemode_combo, 1)
        mode_diff_row.addWidget(QLabel("Difficulty"))
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItems([d.capitalize() for d in setup_flow.DIFFICULTIES])
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
        self.loader_radios = {}
        for loader in world.SUPPORTED_LOADERS:
            self.loader_radios[loader] = QRadioButton("NeoForge" if loader == "neoforge" else loader.capitalize())
            loader_row.addWidget(self.loader_radios[loader])
        version_loader_layout.addLayout(loader_row)
        step2_layout.addWidget(self.version_loader_box)

        self.loader_warning = _label(style=WARN, wrap=True)
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

    def reset(self):
        instances = world.find_instances()
        self.detected_instances_combo.blockSignals(True)
        self.detected_instances_combo.clear()
        for instance in instances:
            self.detected_instances_combo.addItem(instance["name"], instance["path"])
        self.detected_instances_combo.blockSignals(False)
        self.detected_instances_box.setVisible(bool(instances))

        # Prefill from the first detected instance; the field stays editable for
        # anything not detected.
        prefill = instances[0]["path"] if instances else str(world.default_instance_dir() or "")
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

        # Switching needs no instance, and the option only shows when there's something
        # to switch to.
        known_servers = self._populate_switch_world_combo()
        self.mode_switch_radio.setVisible(bool(known_servers))

        self.mode_existing_radio.setChecked(True)
        self.on_step1_mode_changed()

        # Cleared explicitly: this page is one long-lived instance, and an owner left
        # from an abandoned run could otherwise be written into another world's
        # whitelist/ops.
        self.world_name = None
        self.owner_uuid = None
        self.owner_name = None
        self.is_new_world = False
        # Parked, not dropped: reassigning _worker makes _on_prepare_done ignore the
        # abandoned run, but it may still be copying a world, so keep it referenced and
        # visible to MainWindow._pending_workers.
        if self._worker is not None and self._worker.isRunning():
            self._stale_workers.append(self._worker)
        # Anything already finished can be forgotten, so this doesn't grow forever.
        self._stale_workers = [w for w in self._stale_workers if w.isRunning()]
        self._worker = None
        self.prepare_msg.setText("")
        self.finish_msg.setText("")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setText("Finish Setup")

        self.step1_box.setVisible(True)
        self.step2_box.setVisible(False)
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(False)
        _fit_window(self)

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
        """Relabels step 1's button and swaps its fields for the selected mode - the
        work happens on click, in on_proceed_from_step1."""
        is_switch = self.mode_switch_radio.isChecked()
        is_new = self.mode_new_radio.isChecked()
        # Only an existing save needs an instance.
        self.instance_fields_box.setVisible(not is_switch and not is_new)
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

        if self.mode_new_radio.isChecked():
            # A brand-new world has nothing to do with an instance: prepare_new_world
            # ignores it, mods aren't copied, and version and loader are chosen on the
            # next screen.
            self.instance_dir = ""
            self.step1_msg.setText("")
            self.on_mode_changed()
            self.step1_box.setVisible(False)
            self.step2_box.setVisible(True)
            _fit_window(self)
            return

        instance_dir = self.instance_dir_edit.text()
        result = setup_flow.list_worlds(instance_dir)
        if not result.ok:
            self.step1_msg.setText("\n".join(result.lines))
            return
        self.instance_dir = instance_dir

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
        _fit_window(self)

    def on_mode_changed(self):
        is_existing = self.mode_existing_radio.isChecked()
        is_new = self.mode_new_radio.isChecked()

        self.existing_world_box.setVisible(is_existing)
        self.new_world_box.setVisible(is_new)

        if is_existing:
            self.on_world_selected(self.world_combo.currentText())
        elif is_new:
            # _select_version populates the dropdown, so it must run for a new world;
            # None leaves the newest release selected.
            self._select_version(None)
            self.loader_radios["vanilla"].setChecked(True)
            self.loader_warning.setText("")

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
        self.loader_radios.get(suggested, self.loader_radios["vanilla"]).setChecked(True)
        self.loader_warning.setText("")

    def _select_version(self, detected_version):
        """Populates (and caches) the version dropdown from Mojang's manifest in the
        background, then selects detected_version if present. Switching worlds while
        it loads just changes which version is selected at the end."""
        self._pending_version_selection = detected_version
        # Only a non-empty cache counts - an empty list means the last fetch failed, so
        # retry.
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
            # Offline - fall back to whatever was detected, so setup can still go ahead.
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
        _fit_window(self)
        self.finish_msg.setText("Switching...")
        self._worker = Worker(setup_flow.switch_to_world, world_name)
        self._worker.finished_result.connect(self._on_switch_done)
        self._worker.start()

    def on_prepare_world(self):
        self.mc_version = self.mc_version_combo.currentText()
        self.loader = next((name for name, radio in self.loader_radios.items() if radio.isChecked()), "vanilla")
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
        _fit_window(self)
        self.prepare_msg.setText("Preparing...")
        self.finish_btn.setEnabled(False)

        if generate_new:
            self._worker = Worker(setup_flow.prepare_new_world, self.instance_dir, self.world_name, owner_username)
        else:
            self._worker = Worker(setup_flow.prepare_world, self.instance_dir, self.world_name)
        self._worker.finished_result.connect(self._on_prepare_done)
        self._worker.start()

    def _on_prepare_done(self, result):
        # A worker from an abandoned run still delivers its result later - ignore it, or
        # it would set the wrong world's owner (and whitelist and op them here).
        if self.sender() is not self._worker:
            return
        if isinstance(result, Exception):
            self.prepare_msg.setText(f"Failed: {error_text(result)}")
            self.finish_btn.setEnabled(False)
            return
        self.prepare_msg.setText("\n".join(result.lines))
        # Enabled only on success, not just "no exception": finishing after a failed
        # prepare (say, the name already exists) would rewrite that existing server's
        # settings with its whitelist turned off.
        if not result.ok:
            self.owner_uuid = self.owner_name = None
            self.finish_btn.setEnabled(False)
            return
        self.owner_uuid = result.data.get("owner_uuid")
        self.owner_name = result.data.get("owner_name")
        self.finish_btn.setEnabled(True)

    def _on_switch_done(self, result):
        if self.sender() is not self._worker:  # see _on_finish_done
            return
        if isinstance(result, Exception):
            self.finish_msg.setText(f"Failed: {error_text(result)}")
            return
        self.finish_msg.setText("\n".join(result.lines))

    def on_finish_setup(self):
        self.finish_btn.setEnabled(False)
        self.finish_btn.setText("Setting up...")

        world_options = None
        if self.is_new_world:
            world_options = setup_flow.world_options(
                self.gamemode_combo.currentText().lower(),
                self.difficulty_combo.currentText().lower(),
                self.level_type_combo.currentData(),
                self.generate_structures_check.isChecked(),
                self.spawn_protection_spin.value(),
                self.seed_edit.text(),
            )

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
        # Same staleness guard as _on_prepare_done: an abandoned finish reporting into a
        # newer run would show another world's result as this one's.
        if self.sender() is not self._worker:
            return
        self.finish_btn.setEnabled(True)
        self.finish_btn.setText("Finish Setup")
        if isinstance(result, Exception):
            self.prepare_msg.setText(f"Setup failed: {error_text(result)}")
            return
        self.finish_msg.setText("\n".join(result.lines))
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(True)
        _fit_window(self)


