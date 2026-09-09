"""The actual logic behind `setup` and `configure-relay`: world detection, copying
the save, downloading a matching server, owner whitelisting - shared by the CLI and
the GUI so neither duplicates it."""

import json
from pathlib import Path

from . import config, java_manager, javacheck, mojang, server_fabric, server_vanilla, world
from .actions import ActionResult
from .paths import SERVERS_DIR


def default_instance_dir():
    return str(world.default_instance_dir() or "")


def list_worlds(instance_dir):
    if not instance_dir or not Path(instance_dir).exists():
        return ActionResult(False, [f"Instance folder not found: {instance_dir!r}"])
    saves = world.list_saves(instance_dir)
    if not saves:
        # Not an error - an instance with no worlds yet is exactly when someone wants
        # to generate a brand-new one instead of picking an existing save.
        return ActionResult(
            True, [f"No existing worlds found under {instance_dir}\\saves."], data={"worlds": []}
        )
    return ActionResult(True, [], data={"worlds": [p.name for p in saves]})


def detect_world_info(instance_dir, world_name):
    save_path = Path(instance_dir) / "saves" / world_name
    return {
        "mc_version": world.read_mc_version(save_path),
        "suggested_loader": world.detect_loader(instance_dir),
    }


def detect_new_world_info(instance_dir):
    """Same shape as detect_world_info, but for a world that doesn't exist yet -
    there's no level.dat to read a version from, so it's a best-effort guess from the
    launcher's own instance metadata (still editable by the user either way)."""
    return {
        "mc_version": world.read_prism_intended_version(instance_dir),
        "suggested_loader": world.detect_loader(instance_dir),
    }


def valid_new_world_name(world_name):
    if not world_name or not world_name.strip() or len(world_name) > 100:
        return False
    # It becomes a directory name directly under servers/ (no separators/"..", so it
    # can't escape that directory) and gets written verbatim into server.properties'
    # motd line - also reject control characters (newlines in particular), which
    # would otherwise inject extra lines into that file.
    if world_name in (".", ".."):
        return False
    if any(c in world_name for c in "\\/:*?\"<>|"):
        return False
    return all(ord(c) >= 0x20 for c in world_name)


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
        "view-distance": str(config.ensure_view_distance(cfg)),
        "simulation-distance": str(config.ensure_simulation_distance(cfg)),
    }
    path = Path(server_dir) / "server.properties"
    lines = [f"{k}={v}" for k, v in props.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_server_properties(server_dir, updates):
    """Patches specific keys in an existing server.properties, preserving everything
    else - unlike write_server_properties (which regenerates the whole file, only
    appropriate at first setup), this is safe to call before every start so
    auto-sized settings like view-distance stay current without clobbering state that
    changes independently of MCPersist, like the whitelist toggle via RCON/in-game."""
    path = Path(server_dir) / "server.properties"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        if "=" not in line or line.strip().startswith("#"):
            continue
        key = line.split("=", 1)[0]
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_whitelist(server_dir, uuid, name):
    entry = [{"uuid": uuid, "name": name}]
    (Path(server_dir) / "whitelist.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")


def write_ops(server_dir, uuid, name):
    entry = [{"uuid": uuid, "name": name, "level": 4, "bypassesPlayerLimit": False}]
    (Path(server_dir) / "ops.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")


def _whitelist_result_lines(whitelisted, owner_name, not_whitelisted_reason):
    lines = []
    if whitelisted:
        lines.append(
            f"Whitelisted {owner_name!r}. Whitelist is ON - only whitelisted players can join."
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
        lines.append(f"{not_whitelisted_reason} - leaving the whitelist OFF for now.")
        lines.append(
            "DISCLAIMER: with the whitelist off, ANYONE with a Minecraft account can join once the "
            "tunnel is up - your server is reachable at a public address, and port-scanning bots and "
            "random players do find and probe open servers on the internet. Turn it on as soon as "
            "possible: set white-list=true in server.properties and add players to whitelist.json "
            "(or run `whitelist add <username>` / `whitelist on` via RCON/in-game once you're an op), "
            "then restart."
        )
    return lines


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
        not_whitelisted_reason = None
    else:
        found = "no players" if not owner_uuid else "more than one player"
        not_whitelisted_reason = (
            f"Couldn't automatically identify the world owner ({found} found in this world's "
            "saved data)"
        )
    lines.extend(_whitelist_result_lines(whitelisted, owner_name, not_whitelisted_reason))

    return ActionResult(
        True,
        lines,
        data={"owner_uuid": owner_uuid, "owner_name": owner_name, "whitelisted": whitelisted},
    )


def prepare_new_world(instance_dir, world_name, owner_username):
    """Sets up a server directory for a brand-new world - there's no existing save to
    copy, the Minecraft server generates it itself on first start. The owner can't be
    auto-detected from player data that doesn't exist yet, so it's resolved from a
    typed-in username instead - required, not optional, since with the whitelist on
    (always, for a new world - there's no existing owner to leave it off for) nobody
    including the owner can join without it."""
    if not owner_username or not owner_username.strip():
        return ActionResult(False, ["A Minecraft username is required - nobody can join a whitelisted server without one."])

    server_dir = SERVERS_DIR / world_name
    if server_dir.exists() and any(server_dir.iterdir()):
        return ActionResult(False, [f"{server_dir} already exists - pick a different world name."])
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "logs").mkdir(exist_ok=True)

    lines = [f"Creating a new world {world_name!r} - it will be generated on first start."]

    owner_uuid = owner_name = None
    whitelisted = False
    resolved = mojang.uuid_for_username(owner_username.strip())
    if resolved:
        owner_uuid, owner_name = resolved
        write_whitelist(server_dir, owner_uuid, owner_name)
        whitelisted = True
        not_whitelisted_reason = None
    else:
        not_whitelisted_reason = f"Couldn't find a Minecraft account named {owner_username!r}"
    lines.extend(_whitelist_result_lines(whitelisted, owner_name, not_whitelisted_reason))

    return ActionResult(
        True,
        lines,
        data={"owner_uuid": owner_uuid, "owner_name": owner_name, "whitelisted": whitelisted},
    )


def finish_setup(instance_dir, world_name, mc_version, loader, owner_uuid, owner_name):
    """Downloads the matching server jar, ops the owner if one was whitelisted, and
    writes eula/server.properties/config.json. Assumes prepare_world already ran.

    The owner is always opped when known, not a choice - getting into a whitelisted
    server at all already means they're trusted enough to be there, so there's no
    real distinction left to ask about."""
    cfg = config.load()
    server_dir = SERVERS_DIR / world_name

    lines = []
    if owner_uuid and owner_name:
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

    # Mojang's own manifest is authoritative and current; the hardcoded table in
    # world.py is a fallback guess for when that lookup isn't available (offline,
    # very old versions) - it goes stale every time a new Minecraft version bumps its
    # Java requirement, so prefer the live value whenever we can get one.
    required_java = server_vanilla.required_java_major(mc_version) or world.required_java_major(
        mc_version, instance_dir=instance_dir
    )
    cfg["required_java_major"] = required_java

    if not cfg.get("java_auto", True):
        detected_java = javacheck.detected_major_version(cfg["java_path"])
        if detected_java is None:
            lines.append(f'WARNING: "java_path" ({cfg["java_path"]!r}) was not found or unreadable.')
        elif detected_java != required_java:
            lines.append(
                f'WARNING: "java_path" is Java {detected_java}, but Minecraft {mc_version} needs Java '
                f"{required_java}."
            )
        else:
            lines.append(f"Java {detected_java} detected - matches what Minecraft {mc_version} needs.")
    else:
        lines.append(f"Getting Java {required_java} ...")
        try:
            cfg["java_path"] = java_manager.ensure_java(required_java)
            lines.append(f"Using Java {required_java}.")
        except Exception as e:
            lines.append(
                f"WARNING: couldn't get Java {required_java} automatically ({e}). Install it yourself "
                'from https://adoptium.net/, or set "java_path" in config.json.'
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
    write_world_meta(server_dir, cfg)

    lines.append(
        f"View/simulation distance set to {config.ensure_view_distance(cfg)}/"
        f"{config.ensure_simulation_distance(cfg)} based on this PC's specs - change this anytime "
        "in the Performance section of the status screen."
    )
    lines.append("Setup complete.")
    return ActionResult(True, lines)


WORLD_META_FIELDS = (
    "instance_dir",
    "loader",
    "mc_version",
    "memory_auto",
    "memory_mb",
    "performance_auto",
    "view_distance",
    "simulation_distance",
)


def write_world_meta(server_dir, cfg):
    """Snapshots the settings needed to make this world the active one again later
    without re-running setup - everything else (rcon port/password, whitelist state)
    already lives in this world's own server.properties/whitelist.json, so it doesn't
    need to be duplicated here too."""
    meta = {field: cfg.get(field) for field in WORLD_META_FIELDS}
    (Path(server_dir) / "mcpersist_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _backfill_active_world_meta():
    """A world set up before mcpersist_meta.json existed won't show up in
    list_known_servers() - there's no way to recover settings for a world that isn't
    the currently active one, since config.json only ever reflects whichever world is
    active right now. But the currently active world's settings are sitting right
    there in config.json, so if its server directory is missing metadata, write it now
    rather than leaving it invisible to "switch to a previous server" forever."""
    cfg = config.load()
    world_name = cfg.get("world_name")
    if not world_name:
        return
    server_dir = SERVERS_DIR / world_name
    if server_dir.exists() and not (server_dir / "mcpersist_meta.json").exists():
        write_world_meta(server_dir, cfg)


def list_known_servers():
    """Every world MCPersist has set up before, discovered by scanning servers/
    itself rather than tracked in a separate registry - the filesystem is already the
    source of truth, so this can't go stale relative to what's actually there."""
    if not SERVERS_DIR.exists():
        return []
    _backfill_active_world_meta()
    found = []
    for entry in sorted(SERVERS_DIR.iterdir()):
        meta_path = entry / "mcpersist_meta.json"
        if not entry.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        found.append(
            {"world_name": entry.name, "loader": meta.get("loader"), "mc_version": meta.get("mc_version")}
        )
    return found


def switch_to_world(world_name):
    """Makes a previously set-up world the active one - just repoints config.json at
    it using its own saved metadata (see write_world_meta) plus the rcon port/password
    already sitting in its server.properties. No re-download or owner-detection,
    since all of that already happened the first time this world was set up."""
    server_dir = SERVERS_DIR / world_name
    meta_path = server_dir / "mcpersist_meta.json"
    if not meta_path.exists():
        return ActionResult(
            False, [f"{world_name!r} doesn't look like a world MCPersist set up (no saved settings found)."]
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return ActionResult(False, [f"Couldn't read {world_name!r}'s saved settings: {e}"])

    cfg = config.load()
    cfg["world_name"] = world_name
    for field in WORLD_META_FIELDS:
        if field in meta:
            cfg[field] = meta[field]

    props_path = server_dir / "server.properties"
    if props_path.exists():
        for line in props_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("rcon.port="):
                cfg["rcon_port"] = int(line.split("=", 1)[1].strip() or cfg["rcon_port"])
            elif line.strip().startswith("rcon.password="):
                cfg["rcon_password"] = line.split("=", 1)[1].strip() or cfg["rcon_password"]

    config.save(cfg)
    return ActionResult(True, [f"Switched to {world_name!r}."])


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
