"""Operator CLI, run on the relay's VPS, for issuing subdomain+token pairs to relay
users (add-user/remove-user/list-users) and checking live status (status)."""

import argparse
import json
import secrets
import time
from pathlib import Path

from users import add_user, load_users, remove_user

STATUS_PATH = Path(__file__).resolve().parent / "relay_status.json"


def cmd_add(args):
    token = secrets.token_urlsafe(24)
    add_user(args.subdomain, token)
    print(f"Added {args.subdomain!r}")
    print(f"  subdomain: {args.subdomain}")
    print(f"  token:     {token}")
    print("Give these to the user for their `run.bat configure-relay` step.")


def cmd_remove(args):
    remove_user(args.subdomain)
    print(f"Removed {args.subdomain!r}")


def cmd_list(args):
    users = load_users()
    if not users:
        print("No users registered.")
        return
    for subdomain in sorted(users):
        print(subdomain)


def _format_duration(seconds):
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def cmd_status(args):
    if not STATUS_PATH.exists():
        print("No status snapshot yet - is the relay actually running? (it writes one within a few seconds of starting)")
        return
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Couldn't read {STATUS_PATH}: {e}")
        return

    age = time.time() - data.get("updated_at", 0)
    print(f"Relay uptime: {_format_duration(data.get('uptime_seconds', 0))} (snapshot is {age:.0f}s old)")

    connected = data.get("connected_subdomains", [])
    if connected:
        print(f"Currently connected ({len(connected)}): {', '.join(connected)}")
    else:
        print("Currently connected: none")

    by_ip = data.get("concurrent_connections_by_ip", {})
    if by_ip:
        print("Concurrent connections by source IP:")
        for ip, count in sorted(by_ip.items(), key=lambda kv: -kv[1]):
            print(f"  {ip}: {count}")


def main():
    parser = argparse.ArgumentParser(prog="admin_cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add-user")
    p_add.add_argument("subdomain")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove-user")
    p_remove.add_argument("subdomain")
    p_remove.set_defaults(func=cmd_remove)

    sub.add_parser("list-users").set_defaults(func=cmd_list)
    sub.add_parser("status", help="Show live connection status (who's connected right now)").set_defaults(
        func=cmd_status
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
