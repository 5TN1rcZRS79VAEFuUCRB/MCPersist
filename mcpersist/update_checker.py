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
#
# Logs every step to $LogPath and retries the extract: a process disappearing from
# Get-Process doesn't guarantee every handle on its own files (especially the DLLs in
# _internal/) is released the same instant, and antivirus real-time scanning can also
# briefly hold a lock on a just-written exe - both look like a transient "file in use"
# failure from Expand-Archive. The original version had no error handling at all, so a
# failure here just silently killed the script before it ever reached Start-Process:
# the app would quit (having already handed off to this script) and nothing would come
# back, with zero record of what went wrong. On failure the log and zip are left in
# place (for diagnosis) instead of deleted, and the app checks for a leftover log on
# next launch to surface the failure instead of leaving the user with no feedback at all.
_UPDATER_PS1 = """param(
    [int]$ProcPid,
    [string]$ZipPath,
    [string]$DestDir,
    [string]$ExePath,
    [string]$LogPath
)

function Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -LiteralPath $LogPath -Value $line -ErrorAction SilentlyContinue
}

try {
    Log "Updater started for pid $ProcPid"
    while (Get-Process -Id $ProcPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 1
    }

    # Grace period after the process disappears, before touching its files.
    Start-Sleep -Seconds 2

    Log "Extracting $ZipPath to $DestDir"
    $extracted = $false
    for ($i = 1; $i -le 10; $i++) {
        try {
            Expand-Archive -LiteralPath $ZipPath -DestinationPath $DestDir -Force -ErrorAction Stop
            $extracted = $true
            break
        } catch {
            Log "Extract attempt $i/10 failed: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }

    if (-not $extracted) {
        Log "FAILED: could not extract update after 10 attempts - old install left in place"
        exit 1
    }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        Log "FAILED: $ExePath not found after extraction"
        exit 1
    }

    Log "Extraction OK, relaunching $ExePath"
    Start-Process -FilePath $ExePath
    Log "Update complete"
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
} catch {
    Log "FAILED: unexpected error: $($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
"""


def update_log_path():
    return Path(tempfile.gettempdir()) / "mcpersist_update.log"


def check_last_update_failure():
    """If the last self-update attempt left a failure log behind (see _UPDATER_PS1),
    returns its text and deletes it so it's only ever surfaced once. Returns None if
    the last attempt succeeded (the script deletes its own log on success) or no
    update was ever attempted."""
    log_path = update_log_path()
    if not log_path.exists():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    log_path.unlink(missing_ok=True)
    return text or None


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
    log_path = update_log_path()
    log_path.unlink(missing_ok=True)  # clear any already-surfaced leftover from a prior attempt

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
            "-LogPath",
            str(log_path),
        ],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
