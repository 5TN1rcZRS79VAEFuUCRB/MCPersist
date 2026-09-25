"""Checks GitHub Releases for a newer version and, for the packaged .exe, applies it.
Windows won't let a running program overwrite its own files, so a detached helper
script does the final copy after we quit - but everything checkable (a real zip, no
zip slip, contains MCPersist.exe) is checked first, in a staging directory, so a bad
download fails visibly with the app still open."""

import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from . import java_manager, net, process_manager
from .paths import BASE_DIR
from .version import VERSION

REPO = "5TN1rcZRS79VAEFuUCRB/MCPersist"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

# GitHub serves release assets from these; checking the host is a cheap guard against
# downloading and running something from anywhere else.
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
    """{"version", "download_url", "release_url"} if a newer release exists, else None.
    Never raises - a failed check just means nothing to report."""
    try:
        data = net.get_json(LATEST_RELEASE_API, timeout=10)
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


# Static: every path and pid is passed as a real argument, never interpolated into this
# text (a quote in a Windows username would break it). The script waits for us to exit,
# then copies the already-validated StagingDir over the install, logging every step to
# $LogPath. On failure the log is kept, and the next launch reports it.
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

    # Copy-Item isn't all-or-nothing: hitting a locked file (the tunnel runs
    # MCPersist.exe itself) leaves new _internal files beside the old exe, which won't
    # launch. So first wait until every file to be replaced can be opened exclusively,
    # then back them up and restore on failure. [IO.Directory]::GetFiles keeps the exact
    # (possibly 8.3 short) prefix, which the path arithmetic below relies on.
    $stagingRoot = $StagingDir.TrimEnd('\\')
    $targets = @([System.IO.Directory]::GetFiles($stagingRoot, '*', 'AllDirectories') | ForEach-Object {
        Join-Path $DestDir $_.Substring($stagingRoot.Length + 1)
    } | Where-Object { Test-Path -LiteralPath $_ })
    Log "Update replaces $($targets.Count) existing file(s)"

    $unlocked = $false
    for ($i = 1; $i -le 15; $i++) {
        $blocked = $null
        foreach ($t in $targets) {
            try {
                $fs = [System.IO.File]::Open($t, 'Open', 'ReadWrite', 'None')
                $fs.Close()
            } catch {
                $blocked = $t
                break
            }
        }
        if (-not $blocked) { $unlocked = $true; break }
        Log "Waiting for files to be released (attempt $i/15): $blocked is in use"
        Start-Sleep -Seconds 2
    }
    if (-not $unlocked) {
        Log "FAILED: $blocked stayed in use (is the MCPersist tunnel still running?) - nothing was changed, old install left in place"
        exit 1
    }

    $backupDir = "$stagingRoot.backup"
    Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($t in $targets) {
        $b = Join-Path $backupDir $t.Substring($DestDir.TrimEnd('\\').Length + 1)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $b) | Out-Null
        Copy-Item -LiteralPath $t -Destination $b -Force -ErrorAction Stop
    }

    Log "Copying already-validated update from $StagingDir to $DestDir"
    $copied = $false
    for ($i = 1; $i -le 3; $i++) {
        try {
            Copy-Item -Path (Join-Path $StagingDir '*') -Destination $DestDir -Recurse -Force -ErrorAction Stop
            $copied = $true
            break
        } catch {
            Log "Copy attempt $i/3 failed: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }

    if (-not $copied) {
        try {
            Copy-Item -Path (Join-Path $backupDir '*') -Destination $DestDir -Recurse -Force -ErrorAction Stop
            Log "FAILED: could not copy update - restored the previous install's files, old install left in place"
        } catch {
            Log "FAILED: could not copy update, and restoring the previous files also failed ($($_.Exception.Message)) - reinstall MCPersist from the releases page; the previous files are in $backupDir"
        }
        exit 1
    }
    Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue

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


UPDATE_LOG_PATH = Path(tempfile.gettempdir()) / "mcpersist_update.log"
UPDATE_PENDING_PATH = Path(tempfile.gettempdir()) / "mcpersist_update_pending.txt"


def _consume(path):
    """A marker file's text (None if missing, empty or unreadable), deleted so it's
    only ever reported once."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    path.unlink(missing_ok=True)
    return text or None


def check_last_update_failure():
    """The log a failed self-update left behind (see _UPDATER_PS1), or None - the
    script deletes its own log on success."""
    return _consume(UPDATE_LOG_PATH)


def mark_update_pending(target_version):
    """Written right before quitting to hand off to the updater, so the next launch
    can confirm the update instead of it looking like the app just closed."""
    try:
        UPDATE_PENDING_PATH.write_text(target_version, encoding="utf-8")
    except OSError:
        pass


def check_update_success():
    """The version now running, if the previous launch quit to update to exactly this
    version - an update that didn't take effect is reported by the failure log instead.
    The marker holds the raw GitHub tag ("v0.1.24"), hence comparing parsed versions."""
    target = _consume(UPDATE_PENDING_PATH)
    return VERSION if target and _parse_version(target) == _parse_version(VERSION) else None


def apply_update(download_url):
    """Downloads the release zip and extracts and validates it into a staging directory
    while still running, so failures here are an ordinary visible error. Then
    launches the detached updater and returns - the caller must exit right after.
    Packaged .exe only; raises when run from source."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only applies to the packaged .exe - use `git pull` for a source install.")

    host = urlparse(download_url).hostname
    if host not in ALLOWED_DOWNLOAD_HOSTS:
        raise ValueError(f"refusing to download from unexpected host {host!r}")

    tmp_dir = Path(tempfile.gettempdir())
    zip_path = tmp_dir / "mcpersist_update.zip"
    staging_dir = tmp_dir / "mcpersist_update_staging"
    log_path = UPDATE_LOG_PATH
    log_path.unlink(missing_ok=True)  # clear any already-surfaced leftover from a prior attempt

    zip_path = net.download_to_part(download_url, zip_path)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)  # leftover from an earlier failed/interrupted attempt
    staging_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        java_manager.safe_extract(zf, staging_dir)
    zip_path.unlink(missing_ok=True)

    if not (staging_dir / "MCPersist.exe").exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise RuntimeError("the downloaded update doesn't contain MCPersist.exe - not applying it")

    exe_path = str(BASE_DIR / "MCPersist.exe")
    updater_script = tmp_dir / "mcpersist_updater.ps1"
    updater_script.write_text(_UPDATER_PS1, encoding="utf-8")

    # Not DETACHED_PROCESS: powershell.exe started with no console at all from the
    # windowed GUI stalls forever before running a line (see DETACHED_FLAGS).
    proc = process_manager.popen_detached(
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
    )

    # Popen succeeding doesn't mean the script ran: a machine-level execution policy
    # (which -ExecutionPolicy Bypass can't override) or antivirus makes PowerShell exit
    # before it can log anything. Without this check the app would just close and never
    # come back, with nothing reported.
    time.sleep(1.5)
    if proc.poll() is not None:
        raise RuntimeError(
            f"the updater process exited immediately (code {proc.returncode}) - PowerShell "
            "script execution may be blocked by policy or antivirus on this machine"
        )
