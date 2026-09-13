"""Loads/saves the reserved subdomain -> token registry (users.json), managed by
admin_cli.py and checked by relay_server.py during registration."""

import hmac
import json
import os
from pathlib import Path

from auto_assignments import keep_owner

USERS_PATH = Path(__file__).resolve().parent / "users.json"


def load_users():
    if not USERS_PATH.exists():
        return {}
    return json.loads(USERS_PATH.read_text(encoding="utf-8"))


def save_users(users):
    # Write-then-rename: os.replace is atomic, so a crash mid-write (admin_cli.py is
    # run interactively, but the relay itself could still be killed at a bad moment)
    # can't leave a truncated file that breaks every registration until fixed by hand.
    tmp_path = USERS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(users, indent=2), encoding="utf-8")
    keep_owner(tmp_path, USERS_PATH)
    os.replace(tmp_path, USERS_PATH)


def add_user(subdomain, token):
    users = load_users()
    users[subdomain] = {"token": token}
    save_users(users)


def remove_user(subdomain):
    """Returns whether the user existed."""
    users = load_users()
    if users.pop(subdomain, None) is None:
        return False
    save_users(users)
    return True


def verify(subdomain, token):
    entry = load_users().get(subdomain)
    if entry is None or not token:
        return False
    return hmac.compare_digest(entry.get("token", ""), token)
