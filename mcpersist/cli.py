"""The `run.bat ...` command-line interface - thin wrappers around actions.py and
setup_flow.py, which hold the actual logic (also used by the GUI)."""

import argparse
import sys

from . import actions, autostart, config, server_vanilla, setup_flow, update_checker, world
from .version import VERSION


def prompt(msg, default=None):
    suffix = f" [{default}]" if default is not None else ""
    # Strip a stray BOM (U+FEFF) too - some terminals and paste sources inject one, and
    # str.strip() keeps it.
    answer = input(f"{msg}{suffix}: ").replace("﻿", "").strip()
    return answer or default


def prompt_choice(msg, options):
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        # Same BOM strip as prompt(): a piped stdin can start with one, which would
        # never match isdigit() and eat every following line.
        raw = input(f"{msg} [1-{len(options)}]: ").replace("﻿", "").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("Please enter a valid number.")


def _prompt_loader(suggested_loader):
    loader_idx = prompt_choice(f"Server type (detected: {suggested_loader})", list(world.SUPPORTED_LOADERS))
    return world.SUPPORTED_LOADERS[loader_idx]


def _prompt_version(default_version):
    versions = server_vanilla.list_release_versions()
    if not versions:
        # Offline / the fetch failed - can't validate against anything, so just take
        # whatever they type rather than blocking setup entirely.
        return prompt("Minecraft version (e.g. 1.20.4)", default_version)
    default_version = default_version if default_version in versions else versions[0]
    while True:
        answer = prompt('Minecraft version to use (type "list" to see recent ones)', default_version)
        if answer in versions:
            return answer
        if answer.strip().lower() == "list":
            shown = versions[:20]
            print(", ".join(shown) + (", ..." if len(versions) > len(shown) else ""))
            continue
        print(f"{answer!r} isn't a recognized release version.")


def _prompt_world_options():
    """Only asked for a brand-new world - an existing world's terrain/seed can't change."""
    gamemode = setup_flow.GAMEMODES[prompt_choice("Game mode", [g.capitalize() for g in setup_flow.GAMEMODES])]
    difficulty = setup_flow.DIFFICULTIES[
        prompt_choice("Difficulty", [d.capitalize() for d in setup_flow.DIFFICULTIES])
    ]
    level_type_labels = list(setup_flow.LEVEL_TYPES)
    level_type = setup_flow.LEVEL_TYPES[level_type_labels[prompt_choice("World type", level_type_labels)]]
    generate_structures = (prompt("Generate structures? (y/n)", "y") or "y").strip().lower() != "n"
    spawn_protection = prompt("Spawn protection radius (blocks)", "16")
    seed = prompt("Seed (optional - leave blank for random)", "")
    return setup_flow.world_options(
        gamemode,
        difficulty,
        level_type,
        generate_structures,
        spawn_protection if spawn_protection.strip().lstrip("-").isdigit() else 16,
        seed,
    )


def _report(result):
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


def _active_server_dir(cfg, hint=" Run `run.bat setup` first."):
    server_dir = config.server_dir(cfg)
    if not server_dir or not server_dir.exists():
        print("No world configured yet." + hint)
        return None
    return server_dir


def cmd_setup(args):
    # Switching needs no Minecraft instance, so offer it before asking for one.
    known_servers = setup_flow.list_known_servers()
    if known_servers:
        idx = prompt_choice(
            "What do you want to do?",
            ["Set up a world from a Minecraft instance", "Switch to a previously set-up server"],
        )
        if idx == 1:
            labels = [f"{s['world_name']} ({s['loader']} {s['mc_version']})" for s in known_servers]
            server_idx = prompt_choice("Pick a server to switch to", labels)
            return _report(setup_flow.switch_to_world(known_servers[server_idx]["world_name"]))

    instance_dir = args.instance_dir
    if not instance_dir:
        detected = world.find_instances()
        if detected:
            labels = [f"{d['name']} - {d['path']}" for d in detected] + ["Type in a path manually"]
            idx = prompt_choice("Detected Minecraft instances", labels)
            instance_dir = detected[idx]["path"] if idx < len(detected) else None
        if not instance_dir:
            instance_dir = str(world.default_instance_dir() or "")
    instance_dir = prompt("Minecraft instance folder", instance_dir)

    worlds_result = setup_flow.list_worlds(instance_dir)
    if _report(worlds_result):
        return 1
    worlds = worlds_result.data["worlds"]

    mode_idx = 1
    if worlds:
        mode_idx = prompt_choice("What do you want to do?", ["Select an existing world", "Generate a new world"])

    generate_new = mode_idx == 1

    if generate_new:
        world_name = prompt("Name for the new world")
        while not setup_flow.valid_new_world_name(world_name):
            world_name = prompt('Please enter a valid name (no \\ / : * ? " < > |)')

        info = setup_flow.detect_new_world_info(instance_dir)
        mc_version = _prompt_version(info["mc_version"])
        loader = _prompt_loader(info["suggested_loader"])
        owner_username = prompt("Your Minecraft username (required - the whitelist means nobody can join without it)")
        while not owner_username or not owner_username.strip():
            owner_username = prompt("A username is required - nobody can join a whitelisted server without one")

        print()
        world_options = _prompt_world_options()

        print()
        prepare_result = setup_flow.prepare_new_world(instance_dir, world_name, owner_username)
        if _report(prepare_result):
            return 1
    else:
        world_options = None
        idx = prompt_choice("Pick a world to make persistent", worlds)
        world_name = worlds[idx]

        info = setup_flow.detect_world_info(instance_dir, world_name)
        mc_version = _prompt_version(info["mc_version"])
        loader = _prompt_loader(info["suggested_loader"])

        print()
        prepare_result = setup_flow.prepare_world(instance_dir, world_name)
        if _report(prepare_result):
            return 1

    owner_uuid = prepare_result.data.get("owner_uuid")
    owner_name = prepare_result.data.get("owner_name")

    print()
    finish_result = setup_flow.finish_setup(
        instance_dir,
        world_name,
        mc_version,
        loader,
        owner_uuid,
        owner_name,
        copy_mods=not generate_new,
        world_options=world_options,
    )
    if _report(finish_result):
        return 1

    print("\nRun `run.bat start` to bring the server (and tunnel) up.")
    return 0


def cmd_start(args):
    cfg = config.load()
    server_dir = _active_server_dir(cfg)
    if not server_dir:
        return 1
    return _report(actions.start_server(cfg, server_dir)) or _report(actions.start_tunnel(cfg, server_dir))


def cmd_stop(args):
    cfg = config.load()
    server_dir = _active_server_dir(cfg, hint="")
    if not server_dir:
        return 1
    _report(actions.stop_server(cfg, server_dir))
    _report(actions.stop_tunnel(server_dir))
    print("Stopped.")
    return 0


def cmd_restart(args):
    cmd_stop(args)
    return cmd_start(args)


def cmd_status(args):
    cfg = config.load()
    server_dir = _active_server_dir(cfg)
    if not server_dir:
        return 1

    st = actions.get_status(cfg, server_dir)
    print(f"World: {st['world_name']} ({st['loader']} {st['mc_version']})")
    print(f"Server:  {'RUNNING (pid ' + str(st['server_pid']) + ')' if st['server_running'] else 'stopped'}")
    print(f"Tunnel:  {'RUNNING (pid ' + str(st['tunnel_pid']) + ')' if st['tunnel_running'] else 'stopped'}")
    print(f"Join address: {st['join_address'] or '(not assigned yet - check logs/tunnel.out.log)'}")
    if st["whitelist_enabled"] is None:
        print("Whitelist: unknown (server.properties not found yet)")
    elif st["whitelist_enabled"]:
        names = ", ".join(st["whitelist_names"]) or "no one yet"
        print(f"Whitelist: ON ({names})")
    else:
        print("Whitelist: OFF - anyone with a Minecraft account can join. Run `run.bat whitelist-add <username>`.")
    return 0


def cmd_import_worlds(args):
    return _report(setup_flow.import_worlds_from(args.folder))


def cmd_whitelist_add(args):
    cfg = config.load()
    server_dir = _active_server_dir(cfg)
    if not server_dir:
        return 1
    return _report(actions.add_to_whitelist(cfg, server_dir, args.username))


def cmd_configure_relay(args):
    cfg = config.load()
    print(
        "Optional: without this, `run.bat start` already connects to the relay and gets a\n"
        "random address automatically. Only do this if you want a fixed, memorable one -\n"
        "get a subdomain + token from whoever runs the relay (`admin_cli.py add-user <name>`)."
    )
    # A hostname, not a bare IP: TLS certificate verification needs one, and an IP would
    # only fail later as a cryptic SSL error.
    relay_host = prompt("Relay hostname (e.g. relay.example.com - not a bare IP, TLS needs a real hostname)", cfg.get("relay_host"))
    control_port = prompt("Relay control port", str(cfg.get("relay_control_port") or 7000))
    data_port = prompt("Relay data port", str(cfg.get("relay_data_port") or 7001))
    subdomain = prompt("Your subdomain", cfg.get("subdomain"))
    token = prompt("Your token", cfg.get("relay_token"))
    public_domain = prompt("Public domain (e.g. tunnel.example.com)", cfg.get("public_domain"))

    print()
    return _report(setup_flow.configure_relay(relay_host, control_port, data_port, subdomain, token, public_domain))


def cmd_autostart(args):
    ok, out = getattr(autostart, args.action)()
    print(out)
    return 0 if ok else 1


def cmd_check_update(args):
    print(f"Running version: {VERSION}")
    result = update_checker.check_latest_release()
    if not result:
        print("You're on the latest version (or the check failed - see below if something looks wrong).")
        return 0
    print(f"A newer version is available: {result['version']}")
    print(f"  {result['release_url']}")
    print(
        "Running from source: `git pull` to update instead of downloading - self-update "
        "only applies to the packaged .exe, which you'll find in Releases."
    )
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

    p_wl = sub.add_parser("whitelist-add", help="Add a player to the whitelist (works whether or not the server is running)")
    p_wl.add_argument("username")
    p_wl.set_defaults(func=cmd_whitelist_add)

    p_import = sub.add_parser(
        "import-worlds", help="Recover worlds from another MCPersist install (e.g. after a manual reinstall)"
    )
    p_import.add_argument("folder", help="The old install's folder, or its servers folder directly")
    p_import.set_defaults(func=cmd_import_worlds)

    sub.add_parser("check-update", help="Check GitHub for a newer release").set_defaults(func=cmd_check_update)

    sub.add_parser("configure-relay", help="Set up your self-hosted relay subdomain + token").set_defaults(
        func=cmd_configure_relay
    )

    p_autostart = sub.add_parser("autostart", help="Manage start-on-login")
    p_autostart.add_argument("action", choices=["install", "remove", "status"])
    p_autostart.set_defaults(func=cmd_autostart)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
