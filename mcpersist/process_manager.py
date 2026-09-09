"""Launches/tracks detached background processes (the server and tunnel) via PID
files, plus a short-TEMP workaround for a Windows Java/AF_UNIX bug (see README)."""

import os
import subprocess
import time
from pathlib import Path

import psutil

DETACHED_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

# Java's NIO on Windows opens an AF_UNIX loopback socket internally, whose path length
# is capped (~108 bytes). A long user profile path (e.g. C:\Users\<long-name>\AppData\
# Local\Temp\...) can push the generated socket path over that limit and crash the JVM
# with "Unable to establish loopback connection" / "Invalid argument: connect". Giving
# Java processes a short TEMP/TMP avoids it; harmless for non-Java processes too.
SHORT_TMP_DIR = Path("C:/mctmp")


def launch_detached(cmd, cwd, log_path, short_tmp=False):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab", buffering=0)

    env = None
    if short_tmp:
        SHORT_TMP_DIR.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["TEMP"] = str(SHORT_TMP_DIR)
        env["TMP"] = str(SHORT_TMP_DIR)

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=DETACHED_FLAGS,
        close_fds=True,
        env=env,
    )
    return proc.pid


def write_pid(pid_path, pid):
    Path(pid_path).write_text(str(pid), encoding="utf-8")


def read_pid(pid_path):
    p = Path(pid_path)
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def is_running(pid):
    # Known limitation: this only checks that *some* process currently has this PID,
    # not that it's actually the one a *.pid file originally recorded - Windows can
    # reuse a PID once the original process exits. On a single-user desktop machine
    # this is rare enough (Windows doesn't reuse PIDs aggressively) not to be worth
    # the added complexity of verifying process identity (name/start time) at every
    # call site, but it's a real, understood gap, not an oversight - a stale pid file
    # coinciding with a reused PID could make an already-exited server/tunnel/GUI
    # instance look "running" until the file is manually cleared.
    if pid is None:
        return False
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def stop_pid(pid, graceful_timeout=15):
    if not is_running(pid):
        return
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=graceful_timeout)
        except psutil.TimeoutExpired:
            proc.kill()
    except psutil.Error:
        pass


def stop_server_gracefully(rcon_host, rcon_port, rcon_password, pid, graceful_timeout=30):
    stopped_via_rcon = False
    try:
        from . import rcon

        rcon.send_command(rcon_host, rcon_port, rcon_password, "stop")
        stopped_via_rcon = True
    except Exception:
        stopped_via_rcon = False

    if pid is None:
        return stopped_via_rcon

    deadline = time.time() + graceful_timeout
    while stopped_via_rcon and time.time() < deadline and is_running(pid):
        time.sleep(1)

    if is_running(pid):
        stop_pid(pid, graceful_timeout=10)

    return stopped_via_rcon
