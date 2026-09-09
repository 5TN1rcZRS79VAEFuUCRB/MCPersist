"""Launches tunnel_relay_run.py as a detached background process."""

import sys
from pathlib import Path

from . import process_manager


def launch(server_dir):
    log_path = Path(server_dir) / "logs" / "tunnel.out.log"
    if getattr(sys, "frozen", False):
        # sys.executable is MCPersist.exe itself here, not a python.exe - re-invoking
        # it with "-m" would just relaunch the whole GUI. Pass a sentinel flag that
        # main_gui.py checks for instead, so the re-exec runs the tunnel client.
        cmd = [sys.executable, "--tunnel-relay-run"]
    else:
        cmd = [sys.executable, "-m", "mcpersist.tunnel_relay_run"]
    pid = process_manager.launch_detached(cmd, cwd=server_dir, log_path=log_path)
    return pid, log_path
