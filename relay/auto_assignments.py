"""Persists the source-IP -> subdomain mapping for auto-registered (unreserved,
token-less) clients, so the same IP always gets the same subdomain back."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    # fcntl is POSIX-only; on Windows (local testing only, one process) locked() is a
    # no-op.
    fcntl = None

ASSIGNMENTS_PATH = Path(__file__).resolve().parent / "auto_assignments.json"
LOCK_PATH = Path(__file__).resolve().parent / "auto_assignments.lock"


@contextmanager
def locked():
    """Serializes a whole read-modify-write across processes - the relay and
    admin_cli.py both load, mutate and save this file, and without the lock one can
    silently discard the other's change. Hold it for the entire cycle, not just the
    save."""
    if fcntl is None:
        yield
        return
    try:
        if not LOCK_PATH.exists():
            LOCK_PATH.touch()
            keep_owner(LOCK_PATH, LOCK_PATH.parent)  # a root-created lock file locked the relay out once
        lock_file = open(LOCK_PATH, "r+")
    except OSError as e:
        # The race this prevents is rare and self-healing, but a wrong-owned lock file
        # failing every auto registration is an outage - so degrade to unlocked instead.
        print(f"[auto_assignments] couldn't acquire lock ({e}) - proceeding without it", flush=True)
        yield
        return
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    finally:
        lock_file.close()


def load_assignments():
    if not ASSIGNMENTS_PATH.exists():
        return {}
    return json.loads(ASSIGNMENTS_PATH.read_text(encoding="utf-8"))


def save_assignments(assignments):
    write_json_atomic(ASSIGNMENTS_PATH, assignments)


def write_json_atomic(path, data):
    """Write-then-rename: os.replace is atomic, so a crash mid-write can't leave a
    truncated file (or a half-written status snapshot) behind."""
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    keep_owner(tmp_path, path)
    os.replace(tmp_path, path)


def keep_owner(new_path, target_path):
    """admin_cli.py usually runs as root while the relay runs as its own user: give the
    new file the owner of the file it replaces (or of the directory) so root never
    takes over the relay's state. No-op when not root, and on Windows."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    ref = target_path if target_path.exists() else target_path.parent
    st = ref.stat()
    os.chown(new_path, st.st_uid, st.st_gid)
