"""Persists the source-IP -> subdomain mapping for auto-registered (unreserved,
token-less) clients, so the same IP always gets the same subdomain back."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    # fcntl is POSIX-only - the relay itself only ever runs on Linux in
    # production, but this module also gets imported for local testing on
    # Windows (this project's whole dev workflow tests relay code locally
    # before deploying it). No real cross-process contention exists in that
    # single-process test scenario anyway, so locked() below just degrades to
    # a no-op there rather than making the module unimportable on Windows.
    fcntl = None

ASSIGNMENTS_PATH = Path(__file__).resolve().parent / "auto_assignments.json"
LOCK_PATH = Path(__file__).resolve().parent / "auto_assignments.lock"


@contextmanager
def locked():
    """Serializes a full read-modify-write cycle against this file across
    processes - the relay itself (auto-assigning a new IP) and admin_cli.py
    (run separately, interactively, on the same box for release-auto-assignment)
    both do load_assignments() -> mutate -> save_assignments(). The
    write-then-rename in save_assignments already prevents a torn/corrupt file,
    but not a lost update: without this lock, both processes could load before
    either saves, and whichever saves second would silently clobber the other's
    change (e.g. the relay's brand-new auto-assignment for some IP, discarded by
    an admin's unrelated release-auto-assignment call landing at the same
    instant). Every read-modify-write against this file should hold this for its
    entire span, not just around the final save."""
    if fcntl is None:
        yield
        return
    try:
        LOCK_PATH.touch(exist_ok=True)
        lock_file = open(LOCK_PATH, "r+")
    except OSError as e:
        # The race this prevents is rare and self-healing (worst case: an IP gets
        # a fresh random subdomain instead of keeping its old one) - confirmed the
        # hard way that failing hard here instead is far worse: a lock file that
        # ends up wrong-owned (e.g. accidentally created by a one-off root-run
        # script instead of the mcrelay service user) made every single auto
        # registration raise and get silently rejected, which is a real outage for
        # a cosmetic correctness improvement. Degrading to unlocked instead keeps
        # registrations working; the exception is still visible to whichever
        # caller's own broad handler logs it, just without an outage attached.
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
    # Write-then-rename rather than overwriting in place: os.replace is atomic, so a
    # crash mid-write can never leave a truncated/corrupt file behind - which would
    # otherwise silently break every future auto-registration (the resulting
    # JSONDecodeError gets swallowed by relay_server's broad exception handling)
    # until someone notices and manually fixes the file.
    tmp_path = ASSIGNMENTS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(assignments, indent=2), encoding="utf-8")
    os.replace(tmp_path, ASSIGNMENTS_PATH)
