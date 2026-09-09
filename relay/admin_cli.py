"""Operator CLI, run on the relay's VPS, for issuing subdomain+token pairs to relay
users (add-user/remove-user/list-users)."""

import argparse
import secrets

from users import add_user, load_users, remove_user


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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
