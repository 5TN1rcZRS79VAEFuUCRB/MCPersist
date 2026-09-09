"""Persists the source-IP -> subdomain mapping for auto-registered (unreserved,
token-less) clients, so the same IP always gets the same subdomain back."""

import json
import os
from pathlib import Path

ASSIGNMENTS_PATH = Path(__file__).resolve().parent / "auto_assignments.json"


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
