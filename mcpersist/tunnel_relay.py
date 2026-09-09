"""Launches tunnel_relay_run.py as a detached background process."""

import sys
from pathlib import Path

from . import process_manager


def launch(server_dir):
    log_path = Path(server_dir) / "logs" / "tunnel.out.log"
    cmd = [sys.executable, "-m", "mcpersist.tunnel_relay_run"]
    pid = process_manager.launch_detached(cmd, cwd=server_dir, log_path=log_path)
    return pid, log_path
