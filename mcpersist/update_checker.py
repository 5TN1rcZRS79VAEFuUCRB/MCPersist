"""Checks GitHub Releases for a newer version and, for the packaged .exe, can
download and apply the update itself - so using MCPersist doesn't mean remembering to
go check GitHub by hand. Self-replacing a running app's own files is inherently a bit
risky, so this sticks to a standard, well-understood pattern: download the new build,
hand off to a detached helper script that waits for this process to exit, extracts
over the install directory, and relaunches - rather than anything cleverer."""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

from .paths import BASE_DIR
from .version import VERSION

REPO = "5TN1rcZRS79VAEFuUCRB/MCPersist"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _parse_version(v):
    v = v.strip().lower().lstrip("v")
    parts = []
    for p in v.split("."):
        if not p.isdigit():
            break
        parts.append(int(p))
    return tuple(parts)


def check_latest_release():
    """Returns {"version": ..., "download_url": ..., "release_url": ...} if a newer
    release is available, else None. Never raises - a failed check (offline, GitHub
    down, rate-limited) just means "nothing to report", not an error to surface."""
    try:
        resp = requests.get(LATEST_RELEASE_API, timeout=10, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name", "")
        if not tag or _parse_version(tag) <= _parse_version(VERSION):
            return None
        asset = next((a for a in data.get("assets", []) if a.get("name", "").endswith(".zip")), None)
        if not asset:
            return None
        return {
            "version": tag,
            "download_url": asset["browser_download_url"],
            "release_url": data.get("html_url", f"https://github.com/{REPO}/releases"),
        }
    except Exception:
        return None


def _validate_zip_entries(zf, dest_dir):
    """Same zip-slip guard as java_manager - refuse to extract anything that would
    land outside dest_dir, before writing a single byte."""
    dest_dir = dest_dir.resolve()
    for member in zf.infolist():
        target = (dest_dir / member.filename).resolve()
        if target != dest_dir and dest_dir not in target.parents:
            raise ValueError(f"refusing to extract {member.filename!r} - escapes the install directory")


def apply_update(download_url):
    """Downloads the release zip, validates it, then launches a detached updater
    script and returns - the caller is expected to exit right after so the updater
    can safely overwrite this process's own files. Only meaningful for the packaged
    .exe (a fixed install directory to overwrite); raises if called from source."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only applies to the packaged .exe - use `git pull` for a source install.")

    tmp_dir = Path(tempfile.gettempdir())
    zip_path = tmp_dir / "mcpersist_update.zip"
    resp = requests.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    with zipfile.ZipFile(zip_path) as zf:
        _validate_zip_entries(zf, BASE_DIR)

    exe_path = str(BASE_DIR / "MCPersist.exe")
    pid = os.getpid()
    updater_script = tmp_dir / "mcpersist_updater.bat"
    updater_script.write_text(
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto wait\r\n"
        ")\r\n"
        f'powershell -NoProfile -Command "Expand-Archive -LiteralPath \'{zip_path}\' '
        f"-DestinationPath '{BASE_DIR}' -Force\"\r\n"
        f'start "" "{exe_path}"\r\n'
        f'del "{zip_path}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(updater_script)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
