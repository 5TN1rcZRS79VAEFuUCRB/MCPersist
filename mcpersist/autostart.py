"""Registers/removes start-on-login, via Task Scheduler or a Startup-folder fallback
if that trigger is blocked (some locked-down Windows images restrict it)."""

import os
import subprocess
from pathlib import Path

from .paths import BASE_DIR

TASK_NAME = "MCPersist"
RUN_BAT = BASE_DIR / "run.bat"
STARTUP_BAT = (
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "MCPersist.bat"
)


def _schtasks(*args):
    result = subprocess.run(["schtasks", *args, "/tn", TASK_NAME], capture_output=True, text=True)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def install():
    ok, out = _schtasks("/create", "/sc", "onlogon", "/tr", f'"{RUN_BAT}" start', "/rl", "limited", "/f")
    if ok:
        return True, "Installed via Task Scheduler.\n" + out
    # Some locked-down images block the "onlogon" trigger for standard users; a
    # Startup-folder entry needs no special privileges.
    STARTUP_BAT.write_text(f'@echo off\r\n"{RUN_BAT}" start\r\n', encoding="utf-8")
    return True, f"Task Scheduler unavailable ({out}).\nInstalled via Startup folder instead: {STARTUP_BAT}"


def remove():
    ok, out = _schtasks("/delete", "/f")
    removed_startup = STARTUP_BAT.exists()
    if removed_startup:
        STARTUP_BAT.unlink()
        out = "\n".join(filter(None, [out, f"Removed startup entry: {STARTUP_BAT}"]))
    return ok or removed_startup, out


def status():
    ok, out = _schtasks("/query")
    if ok:
        return True, "Task Scheduler entry found.\n" + out
    if STARTUP_BAT.exists():
        return True, f"Startup folder entry found: {STARTUP_BAT}"
    return False, "No autostart entry found (checked Task Scheduler and Startup folder)."
