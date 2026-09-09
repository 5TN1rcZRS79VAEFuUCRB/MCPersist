"""Persists the source-IP -> subdomain mapping for auto-registered (unreserved,
token-less) clients, so the same IP always gets the same subdomain back."""

import json
from pathlib import Path

ASSIGNMENTS_PATH = Path(__file__).resolve().parent / "auto_assignments.json"


def load_assignments():
    if not ASSIGNMENTS_PATH.exists():
        return {}
    return json.loads(ASSIGNMENTS_PATH.read_text(encoding="utf-8"))


def save_assignments(assignments):
    ASSIGNMENTS_PATH.write_text(json.dumps(assignments, indent=2), encoding="utf-8")
