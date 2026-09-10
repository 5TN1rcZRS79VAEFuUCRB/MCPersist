"""The GUI's screens (status, setup wizard) as PySide6 widgets - all logic delegates
to actions.py/setup_flow.py, same as the CLI."""

import subprocess

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSystemTrayIcon,
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

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
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
        setup_btn = QPushButton("Set Up New World")
        setup_btn.clicked.connect(self.go_to_setup.emit)
        world_layout.addWidget(setup_btn)

        folder_row = QHBoxLayout()
        open_world_btn = QPushButton("World Folder")
        open_world_btn.clicked.connect(self.open_world_folder)
        open_logs_btn = QPushButton("View Logs")
        open_logs_btn.clicked.connect(self.open_logs)
        folder_row.addWidget(open_world_btn)
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
            self.update_btn.setText("Try Again")
            self.update_btn.setEnabled(bool(result))
            return
        if not result:
            return
        self.update_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        self.update_label.setText(f"MCPersist {result['version']} is available.")
        self.update_btn.setText("Update Now")
        self.update_box.setVisible(True)

    def on_update_now(self):
        if not self._pending_update:
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
        self.update_label.setText(f"Updated to {target_version} - restarting now...")
        window = self.window()
        window.tray.showMessage(
            "MCPersist",
            f"Updating to {target_version}. The window will close and reopen "
            "automatically in a few seconds - this is expected, not a crash.",
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )
        # Without this, quit_app() below fires in the same instant as the label/
        # tray message above, so neither is ever actually seen - the whole update
        # just looks like the app closed for no reason. A couple of seconds is
        # enough to register both before the window (and tray icon) disappear.
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
            QTimer.singleShot(10000, lambda: self.action_msg.setText(""))
            return
        all_ok = all(r.ok for r in results)
        lines = [line for r in results for line in r.lines]
        self.action_msg.setStyleSheet("color: #888;" if all_ok else "color: #e74c3c;")
        self.action_msg.setText(" / ".join(lines) if lines else f"{verb} - done.")
        self.refresh()
        QTimer.singleShot(4000 if all_ok else 10000, lambda: self.action_msg.setText(""))

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

    def open_logs(self):
        _, _, server_dir = self.current_status()
        if server_dir is not None:
            _open_folder(server_dir / "logs")

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
        self.owner_uuid = None
        self.owner_name = None
        self._version_worker = None
        self._pending_version_selection = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        title = QLabel("Set Up New World")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        # Step 1
        self.step1_box = QGroupBox("1. Find worlds")
        step1_layout = QVBoxLayout(self.step1_box)

        self.detected_instances_box = QWidget()
        detected_layout = QVBoxLayout(self.detected_instances_box)
        detected_layout.setContentsMargins(0, 0, 0, 0)
        detected_layout.addWidget(QLabel("Detected Minecraft instances"))
        self.detected_instances_combo = QComboBox()
        self.detected_instances_combo.currentIndexChanged.connect(self.on_detected_instance_selected)
        detected_layout.addWidget(self.detected_instances_combo)
        step1_layout.addWidget(self.detected_instances_box)
        self.detected_instances_box.setVisible(False)

        step1_layout.addWidget(QLabel("Minecraft instance folder"))
        instance_dir_row = QHBoxLayout()
        self.instance_dir_edit = QLineEdit()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.on_browse_instance_dir)
        instance_dir_row.addWidget(self.instance_dir_edit, 1)
        instance_dir_row.addWidget(browse_btn)
        step1_layout.addLayout(instance_dir_row)
        find_btn = QPushButton("Find Worlds")
        find_btn.clicked.connect(self.on_find_worlds)
        step1_layout.addWidget(find_btn)
        self.step1_msg = QLabel("")
        self.step1_msg.setWordWrap(True)
        step1_layout.addWidget(self.step1_msg)

        # A previously set-up server needs no Minecraft instance at all to switch
        # to - it was already fully set up the first time around. This used to be
        # a third option buried behind picking an instance and clicking Find
        # Worlds first, which made no sense for a mode that doesn't touch either -
        # it's a fully independent path now, available the moment the wizard
        # opens, not gated behind the existing/new-world flow above at all.
        self.switch_section = QWidget()
        switch_section_layout = QVBoxLayout(self.switch_section)
        switch_section_layout.setContentsMargins(0, 0, 0, 0)
        switch_section_layout.addWidget(QLabel("— or —"))
        switch_section_layout.addWidget(QLabel("Switch to a previously set-up server"))
        self.switch_world_combo = QComboBox()
        switch_section_layout.addWidget(self.switch_world_combo)
        self.switch_world_msg = QLabel("Switches immediately - no download or setup needed.")
        self.switch_world_msg.setWordWrap(True)
        self.switch_world_msg.setStyleSheet("color: #888;")
        switch_section_layout.addWidget(self.switch_world_msg)
        switch_btn = QPushButton("Switch")
        switch_btn.clicked.connect(self.on_switch_to_server)
        switch_section_layout.addWidget(switch_btn)
        step1_layout.addWidget(self.switch_section)
        self.switch_section.setVisible(False)

        layout.addWidget(self.step1_box)

        # Step 2
        self.step2_box = QGroupBox("2. Choose world")
        step2_layout = QVBoxLayout(self.step2_box)

        mode_row = QHBoxLayout()
        self.mode_existing_radio = QRadioButton("Select Existing World")
        self.mode_new_radio = QRadioButton("Generate New World")
        self.mode_existing_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_existing_radio)
        self.mode_group.addButton(self.mode_new_radio)
        self.mode_existing_radio.toggled.connect(self.on_mode_changed)
        mode_row.addWidget(self.mode_existing_radio)
        mode_row.addWidget(self.mode_new_radio)
        step2_layout.addLayout(mode_row)

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
        loader_row.addWidget(self.vanilla_radio)
        loader_row.addWidget(self.fabric_radio)
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

        # Independent of everything above - doesn't need an instance dir at all,
        # so it's ready the moment the wizard opens rather than waiting on Find
        # Worlds. Hidden entirely when there's nothing to switch to yet.
        known_servers = self._populate_switch_world_combo()
        self.switch_section.setVisible(bool(known_servers))

        self.step1_box.setVisible(True)
        self.step2_box.setVisible(False)
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(False)

    def on_detected_instance_selected(self, index):
        path = self.detected_instances_combo.itemData(index)
        if path:
            self.instance_dir_edit.setText(path)

    def on_browse_instance_dir(self):
        start_dir = self.instance_dir_edit.text() or str(BASE_DIR)
        chosen = QFileDialog.getExistingDirectory(self, "Select Minecraft instance folder", start_dir)
        if chosen:
            self.instance_dir_edit.setText(chosen)

    def on_find_worlds(self):
        instance_dir = self.instance_dir_edit.text()
        result = setup_flow.list_worlds(instance_dir)
        if not result.ok:
            self.step1_msg.setText("\n".join(result.lines))
            return
        self.step1_msg.setText("\n".join(result.lines))
        self.instance_dir = instance_dir
        self.world_combo.clear()
        worlds = result.data["worlds"]
        self.world_combo.addItems(worlds)
        # No existing worlds to pick from - generating a new one is the only option.
        self.mode_new_radio.setChecked(not worlds)
        self.mode_existing_radio.setChecked(bool(worlds))
        self.mode_existing_radio.setEnabled(bool(worlds))
        self.on_mode_changed()
        self.step1_box.setVisible(False)
        self.step2_box.setVisible(True)

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
        if suggested in ("forge", "neoforge"):
            self.loader_warning.setText(
                f"Detected {suggested.title()}, which isn't supported yet - only Vanilla and "
                "Fabric servers can be set up right now. Pick one below, but the world may not "
                "run correctly without its actual mod loader."
            )
        else:
            self.loader_warning.setText("")

    def _select_version(self, detected_version):
        """Populates (and caches) the version dropdown from Mojang's manifest, then
        selects detected_version in it if present. Fetching happens once per app run
        and in the background - switching worlds/modes while it's still loading just
        updates which version gets selected once it finishes."""
        self._pending_version_selection = detected_version
        if SetupPage._version_list_cache is not None:
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
        SetupPage._version_list_cache = versions or []
        self._populate_version_combo(SetupPage._version_list_cache)

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
        self.finish_msg.setText("Switching...")
        self._worker = Worker(setup_flow.switch_to_world, world_name)
        self._worker.finished_result.connect(self._on_switch_done)
        self._worker.start()

    def on_prepare_world(self):
        self.mc_version = self.mc_version_combo.currentText()
        self.loader = "fabric" if self.fabric_radio.isChecked() else "vanilla"
        generate_new = self.mode_new_radio.isChecked()

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
        self.prepare_msg.setText("Preparing...")
        self.finish_btn.setEnabled(False)

        if generate_new:
            self._worker = Worker(setup_flow.prepare_new_world, self.instance_dir, self.world_name, owner_username)
        else:
            self._worker = Worker(setup_flow.prepare_world, self.instance_dir, self.world_name)
        self._worker.finished_result.connect(self._on_prepare_done)
        self._worker.start()

    def _on_prepare_done(self, result):
        if isinstance(result, WorkerError):
            self.prepare_msg.setText(f"Failed: {result.message}")
            self.finish_btn.setEnabled(False)
            return
        self.prepare_msg.setText("\n".join(result.lines))
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

        self._worker = Worker(
            setup_flow.finish_setup,
            self.instance_dir,
            self.world_name,
            self.mc_version,
            self.loader,
            self.owner_uuid,
            self.owner_name,
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


