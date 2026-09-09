"""The `run.bat ...` command-line interface - thin wrappers around actions.py and
setup_flow.py, which hold the actual logic (also used by the GUI)."""

import argparse
import subprocess
import sys
from pathlib import Path

from . import actions, autostart, config, setup_flow
from .paths import BASE_DIR


def prompt(msg, default=None):
    suffix = f" [{default}]" if default is not None else ""
    # Strip a stray BOM (U+FEFF) too - some terminals/paste sources inject one, and
    # plain str.strip() doesn't remove it, which previously let a BOM-only paste
    # silently pass as "non-empty" input.
    answer = input(f"{msg}{suffix}: ").replace("﻿", "").strip()
    return answer or default


def prompt_choice(msg, options):
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(f"{msg} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("Please enter a valid number.")


def cmd_setup(args):
    instance_dir = args.instance_dir or setup_flow.default_instance_dir()
    instance_dir = prompt("Minecraft instance folder", instance_dir)

    worlds_result = setup_flow.list_worlds(instance_dir)
    for line in worlds_result.lines:
        print(line)
    if not worlds_result.ok:
        return 1
    worlds = worlds_result.data["worlds"]

    idx = prompt_choice("Pick a world to make persistent", worlds)
    world_name = worlds[idx]

    info = setup_flow.detect_world_info(instance_dir, world_name)
    mc_version = info["mc_version"]
    if not mc_version:
        mc_version = prompt("Couldn't auto-detect the Minecraft version, please enter it (e.g. 1.20.4)")
    else:
        print(f"Detected Minecraft version: {mc_version}")

    if info["suggested_loader"] in ("forge", "neoforge"):
        print(
            f"WARNING: detected {info['suggested_loader'].title()}, which isn't supported yet - "
            "only Vanilla and Fabric servers can be set up right now. Pick one below, but the "
            "world may not run correctly without its actual mod loader."
        )
    loader_idx = prompt_choice(f"Server type (detected: {info['suggested_loader']})", ["vanilla", "fabric"])
    loader = ["vanilla", "fabric"][loader_idx]

    print()
    prepare_result = setup_flow.prepare_world(instance_dir, world_name)
    for line in prepare_result.lines:
        print(line)

    owner_uuid = prepare_result.data.get("owner_uuid")
    owner_name = prepare_result.data.get("owner_name")
    allow_cheats = False
    if prepare_result.data.get("whitelisted"):
        answer = prompt(
            f"Give {owner_name!r} command/cheat access on this server (like allowing cheats on LAN)?", "y"
        )
        allow_cheats = answer.lower().startswith("y")

    print()
    finish_result = setup_flow.finish_setup(instance_dir, world_name, mc_version, loader, owner_uuid, owner_name, allow_cheats)
    for line in finish_result.lines:
        print(line)
    if not finish_result.ok:
        return 1

    print("\nRun `run.bat start` to bring the server (and tunnel) up.")
    return 0


def cmd_start(args):
    cfg = config.load()
    server_dir = config.server_dir(cfg)
    if not server_dir or not server_dir.exists():
        print("No world configured yet. Run `run.bat setup` first.")
        return 1

    server_result = actions.start_server(cfg, server_dir)
    for line in server_result.lines:
        print(line)
    if not server_result.ok:
        return 1

    tunnel_result = actions.start_tunnel(cfg, server_dir)
    for line in tunnel_result.lines:
        print(line)
    if not tunnel_result.ok:
        return 1

    return 0


def cmd_stop(args):
    cfg = config.load()
    server_dir = config.server_dir(cfg)
    if not server_dir or not server_dir.exists():
        print("No world configured yet.")
        return 1

    for line in actions.stop_server(cfg, server_dir).lines:
        print(line)
    for line in actions.stop_tunnel(server_dir).lines:
        print(line)
    print("Stopped.")
    return 0


def cmd_restart(args):
    cmd_stop(args)
    return cmd_start(args)


def cmd_status(args):
    cfg = config.load()
    server_dir = config.server_dir(cfg)
    if not server_dir or not server_dir.exists():
        print("No world configured yet. Run `run.bat setup` first.")
        return 1

    st = actions.get_status(cfg, server_dir)
    print(f"World: {st['world_name']} ({st['loader']} {st['mc_version']})")
    print(f"Server:  {'RUNNING (pid ' + str(st['server_pid']) + ')' if st['server_running'] else 'stopped'}")
    print(f"Tunnel:  {'RUNNING (pid ' + str(st['tunnel_pid']) + ')' if st['tunnel_running'] else 'stopped'}")
    print(f"Join address: {st['join_address'] or '(not assigned yet - check logs/tunnel.out.log)'}")
    return 0


def cmd_set_address(args):
    cfg = config.load()
    cfg["join_address"] = args.address
    config.save(cfg)
    print(f"Join address set to: {args.address}")
    return 0


def cmd_configure_relay(args):
    cfg = config.load()
    print(
        "Optional: without this, `run.bat start` already connects to the relay and gets a\n"
        "random address automatically. Only do this if you want a fixed, memorable one -\n"
        "get a subdomain + token from whoever runs the relay (`admin_cli.py add-user <name>`)."
    )
    relay_host = prompt("Relay host (VPS IP or hostname)", cfg.get("relay_host"))
    control_port = prompt("Relay control port", str(cfg.get("relay_control_port") or 7000))
    data_port = prompt("Relay data port", str(cfg.get("relay_data_port") or 7001))
    subdomain = prompt("Your subdomain", cfg.get("subdomain"))
    token = prompt("Your token", cfg.get("relay_token"))
    public_domain = prompt("Public domain (e.g. tunnel.example.com)", cfg.get("public_domain"))

    result = setup_flow.configure_relay(relay_host, control_port, data_port, subdomain, token, public_domain)
    print()
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


def cmd_autostart(args):
    if args.action == "install":
        ok, out = autostart.install()
    elif args.action == "remove":
        ok, out = autostart.remove()
    else:
        ok, out = autostart.status()
    print(out)
    return 0 if ok else 1


def cmd_tray(args):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen(
        [str(pythonw), "-m", "mcpersist.tray_app"],
        cwd=str(BASE_DIR),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    print("Tray icon launched.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="mcpersist")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Promote a singleplayer world to a persistent server")
    p_setup.add_argument("--instance-dir", dest="instance_dir", default=None)
    p_setup.set_defaults(func=cmd_setup)

    sub.add_parser("start", help="Start the server and tunnel").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="Stop the server and tunnel").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="Restart the server and tunnel").set_defaults(func=cmd_restart)
    sub.add_parser("status", help="Show server/tunnel status and join address").set_defaults(func=cmd_status)

    p_addr = sub.add_parser("set-address", help="Record/override the persistent join address")
    p_addr.add_argument("address")
    p_addr.set_defaults(func=cmd_set_address)

    sub.add_parser("configure-relay", help="Set up your self-hosted relay subdomain + token").set_defaults(
        func=cmd_configure_relay
    )

    p_autostart = sub.add_parser("autostart", help="Manage start-on-login")
    p_autostart.add_argument("action", choices=["install", "remove", "status"])
    p_autostart.set_defaults(func=cmd_autostart)

    sub.add_parser("tray", help="Launch the system tray icon (no console window)").set_defaults(func=cmd_tray)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
