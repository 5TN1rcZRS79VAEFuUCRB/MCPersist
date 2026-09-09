"""Registers/removes start-on-login, via Task Scheduler or a Startup-folder fallback
if that trigger is blocked (some locked-down Windows images restrict it)."""

import os
import subprocess
from pathlib import Path

from .paths import BASE_DIR

TASK_NAME = "MCPersist"


def _run_bat():
    return BASE_DIR / "run.bat"


def _startup_dir():
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_bat():
    return _startup_dir() / "MCPersist.bat"


def install():
    tr_value = f'"{_run_bat()}" start'
    result = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/sc", "onlogon", "/tr", tr_value, "/rl", "limited", "/f"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, "Installed via Task Scheduler.\n" + (result.stdout + result.stderr).strip()

    # Some locked-down Windows images block the "onlogon" trigger for standard users
    # even though Task Scheduler itself works - fall back to a Startup-folder entry,
    # which needs no special privileges.
    startup_bat = _startup_bat()
    startup_bat.write_text(f'@echo off\r\n"{_run_bat()}" start\r\n', encoding="utf-8")
    return True, (
        f"Task Scheduler unavailable ({(result.stdout + result.stderr).strip()}).\n"
        f"Installed via Startup folder instead: {startup_bat}"
    )


def remove():
    task_result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    startup_bat = _startup_bat()
    removed_startup = startup_bat.exists()
    if removed_startup:
        startup_bat.unlink()

    ok = task_result.returncode == 0 or removed_startup
    parts = [(task_result.stdout + task_result.stderr).strip()]
    if removed_startup:
        parts.append(f"Removed startup entry: {startup_bat}")
    return ok, "\n".join(p for p in parts if p)


def status():
    task_result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME],
        capture_output=True,
        text=True,
    )
    if task_result.returncode == 0:
        return True, "Task Scheduler entry found.\n" + (task_result.stdout + task_result.stderr).strip()

    startup_bat = _startup_bat()
    if startup_bat.exists():
        return True, f"Startup folder entry found: {startup_bat}"

    return False, "No autostart entry found (checked Task Scheduler and Startup folder)."
