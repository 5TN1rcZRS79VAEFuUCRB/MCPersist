"""The actual logic behind `setup` and `configure-relay`: world detection, copying
the save, downloading a matching server, owner whitelisting - shared by the CLI and
the GUI so neither duplicates it."""

import json
from pathlib import Path

from . import config, javacheck, mojang, server_fabric, server_vanilla, world
from .actions import ActionResult
from .paths import SERVERS_DIR


def default_instance_dir():
    return str(world.default_instance_dir() or "")


def list_worlds(instance_dir):
    if not instance_dir or not Path(instance_dir).exists():
        return ActionResult(False, [f"Instance folder not found: {instance_dir!r}"])
    saves = world.list_saves(instance_dir)
    if not saves:
        return ActionResult(False, [f"No worlds with a level.dat found under {instance_dir}\\saves"])
    return ActionResult(True, [], data={"worlds": [p.name for p in saves]})


def detect_world_info(instance_dir, world_name):
    save_path = Path(instance_dir) / "saves" / world_name
    return {
        "mc_version": world.read_mc_version(save_path),
        "suggested_loader": world.detect_loader(instance_dir),
    }


def write_eula(server_dir):
    (Path(server_dir) / "eula.txt").write_text("eula=true\n", encoding="utf-8")


def write_server_properties(server_dir, cfg, enable_whitelist):
    props = {
        "server-port": "25565",
        "level-name": "world",
        "online-mode": "true",
        "enable-rcon": "true",
        "rcon.port": str(cfg["rcon_port"]),
        "rcon.password": cfg["rcon_password"],
        "motd": f"{cfg['world_name']} (persistent, via MCPersist)",
        "white-list": "true" if enable_whitelist else "false",
    }
    path = Path(server_dir) / "server.properties"
    lines = [f"{k}={v}" for k, v in props.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_whitelist(server_dir, uuid, name):
    entry = [{"uuid": uuid, "name": name}]
    (Path(server_dir) / "whitelist.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")


def write_ops(server_dir, uuid, name):
    entry = [{"uuid": uuid, "name": name, "level": 4, "bypassesPlayerLimit": False}]
    (Path(server_dir) / "ops.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")


def prepare_world(instance_dir, world_name):
    """Copies the world save and detects/whitelists the owner. Split from
    finish_setup so callers can show the detected owner (and ask about cheats) before
    kicking off the slow server-jar download."""
    save_path = Path(instance_dir) / "saves" / world_name
    server_dir = SERVERS_DIR / world_name
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "logs").mkdir(exist_ok=True)

    lines = [f"Copying world save into {server_dir} ..."]
    world_dir = world.copy_world(save_path, server_dir, world_subdir_name="world")
    lines.append(
        "NOTE: this is a COPY. Your original singleplayer world is untouched. Once "
        "the server is running, join it as multiplayer instead of continuing the "
        "original singleplayer world, or the two will drift apart."
    )

    owner_uuid = world.find_owner_uuid(world_dir)
    owner_name = mojang.username_for_uuid(owner_uuid) if owner_uuid else None
    whitelisted = bool(owner_uuid and owner_name)
    if whitelisted:
        write_whitelist(server_dir, owner_uuid, owner_name)
        lines.append(
            f"Whitelisted {owner_name!r} (world owner, detected automatically). Whitelist is ON - "
            "only whitelisted players can join."
        )
        lines.append(
            "To add a friend: have the server running, then either run "
            "`whitelist add <their Minecraft username>` via RCON/the server console, or have an "
            "op (like you) run `/whitelist add <username>` in-game. Exact username, case-sensitive."
        )
        lines.append(
            "DISCLAIMER: leave the whitelist ON. Your server is reachable at a public address - "
            "port-scanning bots and random players routinely probe open Minecraft servers on the "
            "internet, and with online-mode on but no whitelist, any real Minecraft account could "
            "join and grief the world. Turning it off, even temporarily, is a real risk, not a "
            "hypothetical one."
        )
    else:
        found = "no players" if not owner_uuid else "more than one player"
        lines.append(
            f"Couldn't automatically identify the world owner ({found} found in this world's saved "
            "data) - leaving the whitelist OFF for now."
        )
        lines.append(
            "DISCLAIMER: with the whitelist off, ANYONE with a Minecraft account can join once the "
            "tunnel is up - your server is reachable at a public address, and port-scanning bots and "
            "random players do find and probe open servers on the internet. Turn it on as soon as "
            "possible: set white-list=true in server.properties and add players to whitelist.json "
            "(or run `whitelist add <username>` / `whitelist on` via RCON/in-game once you're an op), "
            "then restart."
        )

    return ActionResult(
        True,
        lines,
        data={"owner_uuid": owner_uuid, "owner_name": owner_name, "whitelisted": whitelisted},
    )


def finish_setup(instance_dir, world_name, mc_version, loader, owner_uuid, owner_name, allow_cheats):
    """Downloads the matching server jar, ops the owner if requested, and writes
    eula/server.properties/config.json. Assumes prepare_world already ran."""
    cfg = config.load()
    server_dir = SERVERS_DIR / world_name

    lines = []
    if owner_uuid and owner_name and allow_cheats:
        write_ops(server_dir, owner_uuid, owner_name)
        lines.append(f"Opped {owner_name!r}.")

    jar_path = server_dir / "server.jar"
    lines.append(f"Downloading {loader} server for Minecraft {mc_version} ...")
    if loader == "vanilla":
        server_vanilla.download_server_jar(mc_version, jar_path)
    else:
        _, loader_version, installer_version = server_fabric.download_server_jar(mc_version, jar_path)
        lines.append(f"Fabric loader {loader_version}, installer {installer_version}")
        mods, skipped_mods = server_fabric.copy_mods(instance_dir, server_dir)
        if mods:
            lines.append(f"Copied {len(mods)} mod(s) into the server's mods/ folder.")
            lines.append(
                "WARNING: client-only mods (rendering/HUD/etc.) can crash a dedicated server - "
                "if startup fails, remove them from the mods/ folder and try again."
            )
        if skipped_mods:
            lines.append(
                f"Skipped {len(skipped_mods)} mod(s) known to be incompatible with a dedicated "
                f"server (not copied): {', '.join(skipped_mods)}. e4mc specifically crashes the "
                "server the moment a player joins - MCPersist replaces what it does, so it's not "
                "needed anyway."
            )

    cfg.update(
        {
            "instance_dir": instance_dir,
            "world_name": world_name,
            "loader": loader,
            "mc_version": mc_version,
            "rcon_password": cfg.get("rcon_password") or config.new_rcon_password(),
        }
    )
    write_eula(server_dir)
    write_server_properties(server_dir, cfg, enable_whitelist=bool(owner_uuid and owner_name))
    config.save(cfg)

    required_java = world.required_java_major(mc_version, instance_dir=instance_dir)
    detected_java = javacheck.detected_major_version(cfg["java_path"])
    if detected_java is None:
        lines.append(f"WARNING: couldn't find Java on PATH. Minecraft {mc_version} needs Java {required_java}.")
    elif detected_java != required_java:
        lines.append(
            f"WARNING: detected Java {detected_java}, but Minecraft {mc_version} needs Java {required_java}."
        )
    else:
        lines.append(f"Java {detected_java} detected - matches what Minecraft {mc_version} needs.")

    lines.append("Setup complete.")
    return ActionResult(True, lines)


def configure_relay(relay_host, control_port, data_port, subdomain, token, public_domain):
    if not (relay_host and subdomain and token):
        return ActionResult(False, ["Relay host, subdomain, and token are all required."])

    cfg = config.load()
    cfg.update(
        {
            "relay_host": relay_host,
            "relay_control_port": int(control_port),
            "relay_data_port": int(data_port),
            "subdomain": subdomain,
            "relay_token": token,
            "public_domain": public_domain,
        }
    )
    if public_domain:
        cfg["join_address"] = f"{subdomain}.{public_domain}"
    config.save(cfg)

    lines = ["Relay configured."]
    if cfg.get("join_address"):
        lines.append(f"Join address: {cfg['join_address']}")
    return ActionResult(True, lines, data={"join_address": cfg.get("join_address")})
