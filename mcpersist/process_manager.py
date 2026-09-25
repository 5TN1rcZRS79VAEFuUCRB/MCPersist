"""Launches/tracks detached background processes (the server and tunnel) via PID
files, plus a short-TEMP workaround for a Windows Java/AF_UNIX bug (see README)."""

import os
import subprocess
import time
from pathlib import Path

import psutil

# CREATE_NO_WINDOW, not DETACHED_PROCESS: a console program started with no console at
# all can allocate its own visible one, and a visible console in QuickEdit mode freezes
# the server when clicked. CREATE_BREAKAWAY_FROM_JOB lets these outlive the GUI.
DETACHED_FLAGS = (
    subprocess.CREATE_NEW_PROCESS_GROUP
    | subprocess.CREATE_NO_WINDOW
    | subprocess.CREATE_BREAKAWAY_FROM_JOB
)

# Java's NIO opens an AF_UNIX socket under TEMP, and a long profile path overflows its
# ~108-byte limit ("Unable to establish loopback connection"). A short TEMP avoids it.
SHORT_TMP_DIR = Path("C:/mctmp")

# Every Popen stays referenced for our whole lifetime: one garbage-collected (and
# finalized) on a worker thread during shutdown crashed the process with an access
# violation.
_detached_procs = []


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
    # The child has its own handle - close ours now, not whenever GC gets to it.
    log_file.close()
    _detached_procs.append(proc)
    return proc.pid


# Pid files outlive their processes and PIDs get reused (after a restart, especially),
# so each file records the start time too, and read_pid only returns a PID that's still
# that same process.
_CREATE_TIME_TOLERANCE = 1.0


def write_pid(pid_path, pid):
    try:
        created = psutil.Process(pid).create_time()
    except psutil.Error:
        return  # already exited - nothing to track
    Path(pid_path).write_text(f"{pid} {created}", encoding="utf-8")


def read_pid(pid_path):
    try:
        pid_text, created_text = Path(pid_path).read_text(encoding="utf-8").split()
        pid, recorded = int(pid_text), float(created_text)
    except (ValueError, OSError):
        return None
    try:
        return pid if abs(psutil.Process(pid).create_time() - recorded) <= _CREATE_TIME_TOLERANCE else None
    except psutil.Error:
        # Gone (or not inspectable): not running, which callers treat the same as
        # no pid file at all.
        return None


def is_running(pid):
    if pid is None:
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
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
