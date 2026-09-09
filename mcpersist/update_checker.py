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
from urllib.parse import urlparse

import requests

from .paths import BASE_DIR
from .version import VERSION

REPO = "5TN1rcZRS79VAEFuUCRB/MCPersist"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

# GitHub redirects release-asset downloads through one of these; apply_update() only
# ever downloads from a URL GitHub's own API just handed us, but checking the host
# before fetching is a cheap, real guard against ever downloading-and-running
# something from wherever a URL happened to come from.
ALLOWED_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


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


# Static - every path/pid is passed in as a real process argument (see apply_update),
# never interpolated into this text. Building the equivalent command as a string
# (the previous approach) meant a single quote anywhere in the install path or a
# Windows username - "C:\Users\O'Brien\..." is a completely ordinary path - would
# break the quoting and could turn into something other than the intended command.
_UPDATER_PS1 = """param(
    [int]$ProcPid,
    [string]$ZipPath,
    [string]$DestDir,
    [string]$ExePath
)
while (Get-Process -Id $ProcPid -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 1
}
Expand-Archive -LiteralPath $ZipPath -DestinationPath $DestDir -Force
Start-Process -FilePath $ExePath
Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def apply_update(download_url):
    """Downloads the release zip, validates it, then launches a detached updater
    script and returns - the caller is expected to exit right after so the updater
    can safely overwrite this process's own files. Only meaningful for the packaged
    .exe (a fixed install directory to overwrite); raises if called from source."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only applies to the packaged .exe - use `git pull` for a source install.")

    host = urlparse(download_url).hostname
    if host not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"refusing to download from unexpected host {host!r}")

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
    updater_script = tmp_dir / "mcpersist_updater.ps1"
    updater_script.write_text(_UPDATER_PS1, encoding="utf-8")

    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(updater_script),
            "-ProcPid",
            str(os.getpid()),
            "-ZipPath",
            str(zip_path),
            "-DestDir",
            str(BASE_DIR),
            "-ExePath",
            exe_path,
        ],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
