"""Filesystem locations MCPersist reads/writes - resolved relative to the project
root when run from source, or next to the .exe when running as a PyInstaller build
(otherwise a --onefile build would write config/servers into a temp extraction
directory that gets wiped on exit)."""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

SERVERS_DIR = BASE_DIR / "servers"
BIN_DIR = BASE_DIR / "bin"
CONFIG_PATH = BASE_DIR / "config.json"

SERVERS_DIR.mkdir(exist_ok=True)
BIN_DIR.mkdir(exist_ok=True)
