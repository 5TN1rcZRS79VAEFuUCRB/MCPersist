"""Operator CLI, run on the relay's VPS, for issuing subdomain+token pairs to relay
users (add-user/remove-user/list-users) and checking live status (status)."""

import argparse
import json
import secrets
import time
from pathlib import Path

from auto_assignments import load_assignments, save_assignments
from users import add_user, load_users, remove_user

STATUS_PATH = Path(__file__).resolve().parent / "relay_status.json"


def cmd_add(args):
    # Auto (unreserved) clients get a persistent subdomain tied to their source IP
    # that never expires on its own (see relay_server.get_or_assign_subdomain) -
    # without this check, reserving that same name here would silently create two
    # registrations racing for one subdomain: whichever one is currently connected
    # wins, and the other gets rejected with "already connected elsewhere" forever
    # (the auto client reconnects with backoff indefinitely), with no way to tell
    # from `add-user`'s own output that this happened.
    assignments = load_assignments()
    auto_owner_ip = next((ip for ip, sub in assignments.items() if sub == args.subdomain), None)
    if auto_owner_ip:
        print(
            f"Refusing: {args.subdomain!r} is currently auto-assigned to IP {auto_owner_ip!r} "
            f"(see auto_assignments.json). Reserving it here too would leave two registrations "
            "fighting over the same name. Free it first with:\n"
            f"  python3 admin_cli.py release-auto-assignment {args.subdomain}\n"
            "then re-run this - only do that once you're sure the person behind that IP is fine "
            "getting a new random name next time they connect."
        )
        return
    token = secrets.token_urlsafe(24)
    add_user(args.subdomain, token)
    print(f"Added {args.subdomain!r}")
    print(f"  subdomain: {args.subdomain}")
    print(f"  token:     {token}")
    print("Give these to the user for their `run.bat configure-relay` step.")


def cmd_release_auto(args):
    assignments = load_assignments()
    owner_ip = next((ip for ip, sub in assignments.items() if sub == args.subdomain), None)
    if owner_ip is None:
        print(f"{args.subdomain!r} isn't currently auto-assigned to anyone - nothing to release.")
        return
    del assignments[owner_ip]
    save_assignments(assignments)
    print(
        f"Released {args.subdomain!r} (was auto-assigned to {owner_ip!r}). If that IP has a live "
        "connection under this name right now, it keeps it until that connection actually drops - "
        "this only clears the reservation for its *next* connection, which will get a fresh random name."
    )


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

    p_release = sub.add_parser(
        "release-auto-assignment",
        help="Clear an auto-assigned (unreserved) subdomain so it can be reserved with add-user",
    )
    p_release.add_argument("subdomain")
    p_release.set_defaults(func=cmd_release_auto)

    sub.add_parser("list-users").set_defaults(func=cmd_list)
    sub.add_parser("status", help="Show live connection status (who's connected right now)").set_defaults(
        func=cmd_status
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
