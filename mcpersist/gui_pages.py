"""The GUI's screens (status, setup wizard) as PySide6 widgets - all logic delegates
to actions.py/setup_flow.py, same as the CLI."""

import subprocess

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QGuiApplication

from . import actions, config, setup_flow
from .gui_worker import Worker
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
        state_row.addWidget(self.server_label)
        state_row.addWidget(self.tunnel_label)
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

        layout.addStretch()

        self.init_ram_controls()

        self._worker = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self.refresh()

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
            self.address_label.setText("-")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.restart_btn.setEnabled(False)
            return

        self.world_label.setText(f"{st['world_name']} ({st['loader']} {st['mc_version']})")
        self._set_state_label(self.server_label, "Server", st["server_running"])
        self._set_state_label(self.tunnel_label, "Tunnel", st["tunnel_running"])
        self.address_label.setText(st["join_address"] or "(not assigned yet)")

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

    def on_save_ram(self):
        import psutil

        total_gb = max(1, int(psutil.virtual_memory().total / (1024**3)))
        is_auto = self.ram_auto_check.isChecked()
        chosen_gb = self.ram_spin.value()

        cfg = config.load()
        cfg["memory_auto"] = is_auto
        cfg["memory_mb"] = chosen_gb * 1024
        config.save(cfg)

        if is_auto:
            self.ram_msg.setText(f"Saved - auto mode, currently {chosen_gb} GB based on this machine's specs.")
        elif chosen_gb >= total_gb:
            self.ram_msg.setText(
                f"Saved, but {chosen_gb} GB leaves little to no headroom for the OS on this "
                f"{total_gb} GB machine - the server may struggle or fail to start."
            )
        else:
            self.ram_msg.setText("Saved - takes effect next Start/Restart.")


class SetupPage(QWidget):
    done = Signal()
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self._worker = None
        self.instance_dir = None
        self.world_name = None
        self.mc_version = None
        self.loader = None
        self.owner_uuid = None
        self.owner_name = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        title = QLabel("Set Up New World")
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        # Step 1
        self.step1_box = QGroupBox("1. Find worlds")
        step1_layout = QVBoxLayout(self.step1_box)
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
        layout.addWidget(self.step1_box)

        # Step 2
        self.step2_box = QGroupBox("2. Choose world")
        step2_layout = QVBoxLayout(self.step2_box)
        step2_layout.addWidget(QLabel("World"))
        self.world_combo = QComboBox()
        self.world_combo.currentTextChanged.connect(self.on_world_selected)
        step2_layout.addWidget(self.world_combo)
        step2_layout.addWidget(QLabel("Minecraft version"))
        self.mc_version_edit = QLineEdit()
        step2_layout.addWidget(self.mc_version_edit)
        loader_row = QHBoxLayout()
        self.vanilla_radio = QRadioButton("Vanilla")
        self.fabric_radio = QRadioButton("Fabric")
        loader_row.addWidget(self.vanilla_radio)
        loader_row.addWidget(self.fabric_radio)
        step2_layout.addLayout(loader_row)
        self.loader_warning = QLabel("")
        self.loader_warning.setWordWrap(True)
        self.loader_warning.setStyleSheet("color: #b45309;")
        step2_layout.addWidget(self.loader_warning)
        continue_btn = QPushButton("Continue")
        continue_btn.clicked.connect(self.on_prepare_world)
        step2_layout.addWidget(continue_btn)
        self.step2_box.setVisible(False)
        layout.addWidget(self.step2_box)

        # Step 3
        self.step3_box = QGroupBox("3. Owner & whitelist")
        step3_layout = QVBoxLayout(self.step3_box)
        self.prepare_msg = QTextEdit()
        self.prepare_msg.setReadOnly(True)
        self.prepare_msg.setFixedHeight(160)
        step3_layout.addWidget(self.prepare_msg)
        self.cheats_check = QCheckBox("Give the owner command/cheat access")
        self.cheats_check.setChecked(True)
        step3_layout.addWidget(self.cheats_check)
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
        self.instance_dir_edit.setText(setup_flow.default_instance_dir())
        self.step1_msg.setText("")
        self.step1_box.setVisible(True)
        self.step2_box.setVisible(False)
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(False)

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
        self.step1_msg.setText("")
        self.instance_dir = instance_dir
        self.world_combo.clear()
        self.world_combo.addItems(result.data["worlds"])
        self.step1_box.setVisible(False)
        self.step2_box.setVisible(True)

    def on_world_selected(self, world_name):
        if not world_name:
            return
        info = setup_flow.detect_world_info(self.instance_dir, world_name)
        suggested = info["suggested_loader"]
        self.mc_version_edit.setText(info["mc_version"] or "")
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

    def on_prepare_world(self):
        self.world_name = self.world_combo.currentText()
        self.mc_version = self.mc_version_edit.text()
        self.loader = "fabric" if self.fabric_radio.isChecked() else "vanilla"

        self.step2_box.setVisible(False)
        self.step3_box.setVisible(True)
        self.prepare_msg.setText("Preparing...")
        self.finish_btn.setEnabled(False)

        self._worker = Worker(setup_flow.prepare_world, self.instance_dir, self.world_name)
        self._worker.finished_result.connect(self._on_prepare_done)
        self._worker.start()

    def _on_prepare_done(self, result):
        self.prepare_msg.setText("\n".join(result.lines))
        self.owner_uuid = result.data.get("owner_uuid")
        self.owner_name = result.data.get("owner_name")
        self.cheats_check.setVisible(bool(result.data.get("whitelisted")))
        self.finish_btn.setEnabled(True)

    def on_finish_setup(self):
        self.finish_btn.setEnabled(False)
        self.finish_btn.setText("Setting up...")
        allow_cheats = self.cheats_check.isChecked()

        self._worker = Worker(
            setup_flow.finish_setup,
            self.instance_dir,
            self.world_name,
            self.mc_version,
            self.loader,
            self.owner_uuid,
            self.owner_name,
            allow_cheats,
        )
        self._worker.finished_result.connect(self._on_finish_done)
        self._worker.start()

    def _on_finish_done(self, result):
        self.finish_btn.setEnabled(True)
        self.finish_btn.setText("Finish Setup")
        self.finish_msg.setText("\n".join(result.lines))
        self.step3_box.setVisible(False)
        self.step4_box.setVisible(True)


