"""Loads/saves the reserved subdomain -> token registry (users.json), managed by
admin_cli.py and checked by relay_server.py during registration."""

import json
from pathlib import Path

USERS_PATH = Path(__file__).resolve().parent / "users.json"


def load_users():
    if not USERS_PATH.exists():
        return {}
    return json.loads(USERS_PATH.read_text(encoding="utf-8"))


def save_users(users):
    USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")


def add_user(subdomain, token):
    users = load_users()
    users[subdomain] = {"token": token}
    save_users(users)


def remove_user(subdomain):
    users = load_users()
    users.pop(subdomain, None)
    save_users(users)


def verify(subdomain, token):
    entry = load_users().get(subdomain)
    return entry is not None and entry.get("token") == token
