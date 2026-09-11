"""Launches/tracks detached background processes (the server and tunnel) via PID
files, plus a short-TEMP workaround for a Windows Java/AF_UNIX bug (see README)."""

import os
import subprocess
import time
from pathlib import Path

import psutil

# CREATE_NO_WINDOW, not DETACHED_PROCESS: java.exe (and python.exe, for the tunnel
# client) are console-subsystem executables. DETACHED_PROCESS gives them *no* console
# at all rather than a genuinely suppressed one, and a console-subsystem program
# started that way can end up allocating its own new, visible console as a fallback -
# exactly the "a cmd window popped up on screen" report this was chasing, and a
# real risk once it happens: a classic console left visible with QuickEdit mode on
# freezes the whole process the instant someone clicks into it to select text (it
# blocks on the next console write until the selection is cancelled), which is
# consistent with a real server.out.log showing a 42-second tick freeze and a stray
# keystroke right as every player got disconnected. CREATE_NO_WINDOW is the flag
# actually documented for "run a console app with no window at all" and was already
# confirmed to fix the equivalent problem for the self-updater's PowerShell child
# (see update_checker.py). CREATE_BREAKAWAY_FROM_JOB is kept for the same reason as
# there too: these processes are meant to outlive the GUI/tray process unconditionally.
DETACHED_FLAGS = (
    subprocess.CREATE_NEW_PROCESS_GROUP
    | subprocess.CREATE_NO_WINDOW
    | subprocess.CREATE_BREAKAWAY_FROM_JOB
)

# Java's NIO on Windows opens an AF_UNIX loopback socket internally, whose path length
# is capped (~108 bytes). A long user profile path (e.g. C:\Users\<long-name>\AppData\
# Local\Temp\...) can push the generated socket path over that limit and crash the JVM
# with "Unable to establish loopback connection" / "Invalid argument: connect". Giving
# Java processes a short TEMP/TMP avoids it; harmless for non-Java processes too.
SHORT_TMP_DIR = Path("C:/mctmp")

# launch_detached() only ever returns proc.pid, so without this the Popen object
# itself would immediately drop to zero references the instant the function
# returns - garbage collecting it right there, on whatever thread happened to
# call this (a background Worker thread, for Start/Stop actions). A Popen that's
# never had .wait() called on it still holds a live Windows process handle, and
# its __del__ finalizer running on a background thread while the interpreter
# starts shutting down (confirmed by direct reproduction: quitting shortly after
# Stop reliably crashed the process on exit with a native access violation, even
# after explicitly waiting for the action's QThread to finish first - the crash
# went away entirely once this stopped happening) is a well-known unsafe
# combination. These processes are meant to run detached and outlive us anyway
# (CREATE_BREAKAWAY_FROM_JOB), so keeping every Popen referenced for the rest of
# this process's own lifetime - never calling .wait() on it, deliberately -
# means its finalizer simply never runs here at all; the handle it holds gets
# reclaimed by Windows like any other of our handles when we ourselves exit,
# same as it would if we'd never wrapped the child in a Popen object at all.
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
    # The child gets its own duplicated handle to the file at Popen() time - our
    # copy isn't needed for the child to keep writing to it, and leaving it open
    # just means Python closes it whenever this now out-of-scope file object
    # happens to get garbage collected, same uncontrolled timing risk as proc
    # above. Closing it here, deterministically, avoids that entirely.
    log_file.close()
    _detached_procs.append(proc)
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
