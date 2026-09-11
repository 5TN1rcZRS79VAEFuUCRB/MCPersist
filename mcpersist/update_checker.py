"""Checks GitHub Releases for a newer version and, for the packaged .exe, can
download and apply the update itself - so using MCPersist doesn't mean remembering to
go check GitHub by hand. Windows won't let a running program overwrite its own loaded
.exe/DLLs, so quitting to hand off to a detached helper script can't be avoided
entirely - but everything that CAN be checked while still running (the download is a
real zip, it's not zip-slipping anywhere, it actually contains MCPersist.exe) happens
before ever committing to that quit: apply_update() extracts into a staging directory
and validates it right here, so a bad download shows up as a normal, visible "Update
failed" with the app still open, not a mystery after the point of no return. The
handoff script's job then shrinks to just moving that already-proven-good staging
directory into place and relaunching, rather than a fresh extraction of its own that
could still fail for reasons this process could have caught first."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
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
# StagingDir already holds a fully extracted, already-validated update (see
# apply_update) - this script's only real job is waiting for the old process to
# actually exit, then copying that proven-good content over the install directory.
# Logs every step to $LogPath and retries the copy: a process disappearing from
# Get-Process doesn't guarantee every handle on its own files (especially the DLLs in
# _internal/) is released the same instant, and antivirus real-time scanning can also
# briefly hold a lock on a just-written exe - both look like a transient "file in use"
# failure from Copy-Item. On failure the log and staging directory are left in place
# (for diagnosis) instead of deleted, and the app checks for a leftover log on next
# launch to surface the failure instead of leaving the user with no feedback at all.
_UPDATER_PS1 = """param(
    [int]$ProcPid,
    [string]$StagingDir,
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

    Log "Copying already-validated update from $StagingDir to $DestDir"
    $copied = $false
    for ($i = 1; $i -le 10; $i++) {
        try {
            Copy-Item -Path (Join-Path $StagingDir '*') -Destination $DestDir -Recurse -Force -ErrorAction Stop
            $copied = $true
            break
        } catch {
            Log "Copy attempt $i/10 failed: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }

    if (-not $copied) {
        Log "FAILED: could not copy update after 10 attempts - old install left in place"
        exit 1
    }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        Log "FAILED: $ExePath not found after copy"
        exit 1
    }

    Log "Copy OK, relaunching $ExePath"
    Start-Process -FilePath $ExePath
    Log "Update complete"
    Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
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


def _update_pending_path():
    return Path(tempfile.gettempdir()) / "mcpersist_update_pending.txt"


def mark_update_pending(target_version):
    """Called right before the app quits to hand off to the updater, so the next
    launch can tell the user "you're now on vX.Y.Z" instead of the update's outcome
    being completely invisible - right now a failure gets surfaced
    (check_last_update_failure) but success didn't get any confirmation at all,
    which is a big part of why the whole restart looked like nothing happened."""
    try:
        _update_pending_path().write_text(target_version, encoding="utf-8")
    except OSError:
        pass


def check_update_success():
    """If the previous launch quit to apply an update, returns the version this
    instance is now actually running - but only if VERSION for real matches what
    was expected, not just because an update was attempted. That keeps this honest
    if the update didn't actually take effect (in which case check_last_update_failure
    is what surfaces it instead) rather than claiming success just because a restart
    happened. Only ever returned once - the marker is cleared either way."""
    marker_path = _update_pending_path()
    if not marker_path.exists():
        return None
    try:
        target = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    marker_path.unlink(missing_ok=True)
    # mark_update_pending() is handed check_latest_release()'s "version" field
    # verbatim, which is the raw GitHub tag name (e.g. "v0.1.24") - VERSION itself
    # never has that "v" prefix, so comparing them as plain strings without
    # normalizing first meant this could never match, confirmed by a real end-to-end
    # test: the marker was written and consumed correctly, but the success banner
    # never appeared because "0.1.24" != "v0.1.24".
    return VERSION if target and _parse_version(target) == _parse_version(VERSION) else None


def apply_update(download_url):
    """Downloads the release zip, extracts and validates it into a staging
    directory (still running - real failures here become a normal "Update
    failed" the user sees immediately, not a mystery after quitting), then
    launches a detached updater script and returns - the caller is expected to
    exit right after so the updater can safely move that already-proven-good
    content over this process's own files. Only meaningful for the packaged
    .exe (a fixed install directory to overwrite); raises if called from source."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only applies to the packaged .exe - use `git pull` for a source install.")

    host = urlparse(download_url).hostname
    if host not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"refusing to download from unexpected host {host!r}")

    tmp_dir = Path(tempfile.gettempdir())
    zip_path = tmp_dir / "mcpersist_update.zip"
    staging_dir = tmp_dir / "mcpersist_update_staging"
    log_path = update_log_path()
    log_path.unlink(missing_ok=True)  # clear any already-surfaced leftover from a prior attempt

    resp = requests.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)  # leftover from an earlier failed/interrupted attempt
    staging_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        _validate_zip_entries(zf, staging_dir)
        zf.extractall(staging_dir)
    zip_path.unlink(missing_ok=True)

    if not (staging_dir / "MCPersist.exe").exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise RuntimeError("the downloaded update doesn't contain MCPersist.exe - not applying it")

    exe_path = str(BASE_DIR / "MCPersist.exe")
    updater_script = tmp_dir / "mcpersist_updater.ps1"
    updater_script.write_text(_UPDATER_PS1, encoding="utf-8")

    proc = subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(updater_script),
            "-ProcPid",
            str(os.getpid()),
            "-StagingDir",
            str(staging_dir),
            "-DestDir",
            str(BASE_DIR),
            "-ExePath",
            exe_path,
            "-LogPath",
            str(log_path),
        ],
        # CREATE_NO_WINDOW, not DETACHED_PROCESS - confirmed by real, repeated
        # reproduction (not just reading about it) that this distinction is the actual
        # bug: DETACHED_PROCESS gives the child *no* console at all, and a plain
        # powershell.exe -File child launched that way from a --windowed (console-less)
        # parent reliably starts, then stalls forever before its very first line of
        # script even runs - never writes a log line, never touches a file, no error,
        # nothing - because PowerShell's own host initialization apparently doesn't
        # handle having zero console object gracefully. CREATE_NO_WINDOW instead gives
        # it a real console that's just hidden, which starts up fine.
        # CREATE_BREAKAWAY_FROM_JOB is kept too - cheap insurance so this survives
        # independent of whatever job object (if any) its parent happens to be in,
        # matching the actual intent: outlive the parent unconditionally.
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        ),
        close_fds=True,
    )

    # Popen succeeding only means Windows accepted the launch request - it says
    # nothing about whether the script actually ran. A machine-level execution
    # policy (Group Policy's MachinePolicy/UserPolicy scopes - which
    # -ExecutionPolicy Bypass on the command line cannot override, unlike the
    # process-level policy) or antivirus blocking an unsigned script makes
    # PowerShell exit almost immediately, before it ever reaches the script's
    # own try/catch and Log calls - so no log file gets written either.
    # Without this check that failure was completely silent on both ends: the
    # caller (_on_update_applied) already commits to quitting right after this
    # returns, so the app just closes and never comes back, and the next
    # launch's check_last_update_failure() finds nothing to report since no log
    # was ever created. A brief liveness check turns that into a real, visible
    # "Update failed" the user actually sees, instead of the app silently
    # vanishing - which is almost certainly what a "the update doesn't work,
    # it doesn't even restart itself" report actually was, on a machine where
    # this specific block applies (confirmed the launch itself works correctly
    # via a real end-to-end self-update on an unrestricted machine - this
    # guards the case where it can't, not the common case).
    time.sleep(1.5)
    if proc.poll() is not None:
        raise RuntimeError(
            f"the updater process exited immediately (code {proc.returncode}) - PowerShell "
            "script execution may be blocked by policy or antivirus on this machine"
        )
