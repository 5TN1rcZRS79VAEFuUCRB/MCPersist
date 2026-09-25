"""The actual logic behind `setup` and `configure-relay`: world detection, copying
the save, downloading a matching server, owner whitelisting - shared by the CLI and
the GUI so neither duplicates it."""

import contextlib
import json
import os
import shutil
import time
from pathlib import Path

from . import config, java_manager, javacheck, mojang, server_fabric, server_forge, server_neoforge, server_vanilla, world
from .actions import ActionResult
from .paths import SERVERS_DIR


def _refuse_while_active_world_running(switch_target=None):
    """Setting up or switching worlds repoints config.json; doing it while the active
    world's server or tunnel runs orphans them (and re-setup of the running world
    moves its folder aside while Minecraft has it open). Returns a refusal, or None
    when safe. Switching to the already-active world is allowed."""
    cfg = config.load()
    active = cfg.get("world_name")
    server_dir = config.server_dir(cfg)
    if not active or server_dir is None or switch_target == active:
        return None
    from . import actions

    try:
        st = actions.get_status(cfg, server_dir)
    except Exception:
        return None
    running = [name for name, up in (("server", st["server_running"]), ("tunnel", st["tunnel_running"])) if up]
    if not running:
        return None
    return ActionResult(
        False,
        [
            f"{active!r} still has its {' and '.join(running)} running. Stop it first (Stop on the "
            "status screen), then set up or switch worlds - otherwise it keeps running where "
            "MCPersist can no longer see or stop it."
        ],
    )


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
    """Same shape as detect_world_info, for a world that doesn't exist yet - a
    best-effort guess from the launcher's instance metadata."""
    return {
        "mc_version": world.read_prism_intended_version(instance_dir),
        "suggested_loader": world.detect_loader(instance_dir),
    }


def _is_path_safe_name(world_name):
    """The safety half of valid_new_world_name, without its length cap: no path
    separators or ".." (it becomes a folder under servers/) and no control characters
    (it's written into server.properties). Used alone for existing worlds, whose
    folder names are already legal."""
    if not world_name or not world_name.strip():
        return False
    if world_name in (".", ".."):
        return False
    if any(c in world_name for c in "\\/:*?\"<>|"):
        return False
    return all(ord(c) >= 0x20 for c in world_name)


def valid_new_world_name(world_name):
    if world_name and len(world_name) > 100:
        return False
    return _is_path_safe_name(world_name)


GAMEMODES = ("survival", "creative", "adventure", "spectator")
DIFFICULTIES = ("peaceful", "easy", "normal", "hard")
# Display label -> server.properties value, namespaced as modern (1.19+) servers expect.
LEVEL_TYPES = {
    "Default": "minecraft:normal",
    "Superflat": "minecraft:flat",
    "Large Biomes": "minecraft:large_biomes",
    "Amplified": "minecraft:amplified",
    "Single Biome": "minecraft:single_biome_surface",
}


def world_options(gamemode, difficulty, level_type, generate_structures, spawn_protection, seed=""):
    """The new-world generation settings for write_server_properties. Spawn protection
    is clamped to the GUI spinbox's [0, 500], and only the seed's first line is kept -
    a raw newline would inject an extra line into server.properties."""
    options = {
        "gamemode": gamemode,
        "difficulty": difficulty,
        "level-type": level_type,
        "generate-structures": "true" if generate_structures else "false",
        "spawn-protection": str(max(0, min(500, int(spawn_protection)))),
    }
    seed = (seed or "").strip().splitlines()
    if seed and seed[0]:
        options["level-seed"] = seed[0]
    return options


def write_eula(server_dir):
    (Path(server_dir) / "eula.txt").write_text("eula=true\n", encoding="utf-8")


def write_server_properties(server_dir, cfg, enable_whitelist, world_options=None):
    """world_options optionally carries the new-world generation settings (see
    world_options()) - only meaningful for a brand-new world."""
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
    if world_options:
        props.update(world_options)
    path = Path(server_dir) / "server.properties"
    if path.exists():
        # Re-setup keeps the owner's own settings: only the keys MCPersist manages
        # change, a whitelist that was on stays on, and a customised motd is kept.
        existing = read_server_properties(server_dir)
        if existing.get("white-list", "").lower() == "true":
            props["white-list"] = "true"
        if "motd" in existing:
            props.pop("motd")
        update_server_properties(server_dir, props)
        return
    path.write_text("".join(f"{k}={v}\n" for k, v in props.items()), encoding="utf-8")


# surrogateescape round-trips bytes that aren't valid UTF-8 (a hand-edited motd, say)
# unchanged, so reading and rewriting the file never fails on them or mangles them.
_PROPS_ENCODING = {"encoding": "utf-8", "errors": "surrogateescape"}


def _split_property(line):
    """(key, value) for a key=value line, or None for blanks and comments. Whitespace
    around the key and value is ignored, as Java's own Properties parser does."""
    stripped = line.strip()
    if not stripped or stripped[0] in "#!" or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip()


def read_server_properties(server_dir):
    """server.properties as a dict ({} if it doesn't exist yet)."""
    path = Path(server_dir) / "server.properties"
    if not path.exists():
        return {}
    return dict(filter(None, map(_split_property, path.read_text(**_PROPS_ENCODING).splitlines())))


def update_server_properties(server_dir, updates):
    """Patches specific keys in an existing server.properties, preserving everything
    else - safe to call before every start, so auto-sized settings stay current
    without clobbering things changed outside MCPersist (the whitelist toggle)."""
    path = Path(server_dir) / "server.properties"
    if not path.exists():
        return
    lines = path.read_text(**_PROPS_ENCODING).splitlines()
    seen = set()
    for i, line in enumerate(lines):
        kv = _split_property(line)
        # Every occurrence, not just the first: Java reads the last duplicate.
        if kv and kv[0] in updates:
            lines[i] = f"{kv[0]}={updates[kv[0]]}"
            seen.add(kv[0])
    lines.extend(f"{k}={v}" for k, v in updates.items() if k not in seen)
    path.write_text("\n".join(lines) + "\n", **_PROPS_ENCODING)


def merge_player_entry(path, entry):
    """Adds or updates one player in a whitelist.json/ops.json-style list, keeping
    everyone else and any fields already on that player's entry. Returns (added,
    note): added is False if they were already listed; note names where a corrupt
    original was set aside. Raises OSError if the file can't be read or written - an
    unreadable file is never treated as empty, which would drop every other player."""
    path = Path(path)
    players, salvage = [], None
    if path.exists():
        # utf-8-sig: Notepad and PowerShell save with a BOM, which plain utf-8 rejects.
        raw = path.read_text(encoding="utf-8-sig")
        try:
            loaded = json.loads(raw)
        except ValueError:
            loaded = None
        if isinstance(loaded, list):
            players = [p for p in loaded if isinstance(p, dict)]
        else:
            salvage = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")

    existing = next((p for p in players if str(p.get("uuid", "")).lower() == entry["uuid"].lower()), None)
    merged = {**entry, **(existing or {}), "uuid": entry["uuid"], "name": entry["name"]}
    players = [p for p in players if p is not existing] + [merged]

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(players, indent=2), encoding="utf-8")
        # The corrupt original is moved aside only once its replacement exists.
        if salvage:
            path.replace(salvage)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    note = None
    if salvage:
        note = (
            f"{path.name} wasn't readable as a player list, so it was set aside as {salvage.name} "
            "and a fresh one started - any players it listed need re-adding."
        )
    return existing is None, note


def write_whitelist(server_dir, uuid, name):
    return merge_player_entry(Path(server_dir) / "whitelist.json", {"uuid": uuid, "name": name})


def write_ops(server_dir, uuid, name):
    return merge_player_entry(
        Path(server_dir) / "ops.json", {"uuid": uuid, "name": name, "level": 4, "bypassesPlayerLimit": False}
    )


def _whitelist_owner(server_dir, uuid, name, lines):
    """Adds the owner to whitelist.json, appending any note to lines. Returns a failed
    ActionResult if the file couldn't be read or written, else None."""
    try:
        _, note = write_whitelist(server_dir, uuid, name)
    except OSError as e:
        return ActionResult(
            False,
            lines + [f"Couldn't update whitelist.json ({e}). Close anything that might have it open, then run setup again."],
        )
    if note:
        lines.append(note)
    return None


def _whitelist_result_lines(whitelisted, owner_name, not_whitelisted_reason):
    lines = []
    if whitelisted:
        lines.append(
            f"Whitelisted {owner_name!r}. Whitelist is ON - only whitelisted players can join."
        )
        lines.append(
            "To add a friend: have the server running, then either run "
            "`whitelist add <their Minecraft username>` via RCON/the server console, or have an "
            "op (like you) run `/whitelist add <username>` in-game. Minecraft usernames aren't "
            "case-sensitive, but it still needs to be spelled correctly."
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
    """Copies the world save and detects/whitelists the owner. Split from finish_setup
    so the owner can be shown before the slow server download."""
    if not _is_path_safe_name(world_name):
        return ActionResult(False, [f"{world_name!r} isn't a valid world name."])
    refusal = _refuse_while_active_world_running()
    if refusal:
        return refusal

    save_path = Path(instance_dir) / "saves" / world_name
    # Before anything is moved or copied: an open world makes the copy fail partway or
    # capture region files mid-write.
    if world.is_world_open(save_path):
        return ActionResult(
            False,
            [
                f"{world_name!r} is open in Minecraft right now. Save and Quit to Title (or close "
                "Minecraft), then continue - copying a world while the game is writing to it can "
                "produce a broken copy."
            ],
        )
    server_dir = SERVERS_DIR / world_name
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "logs").mkdir(exist_ok=True)

    lines = []
    # Moved aside, not overwritten: re-setup (the only way to change version or loader)
    # would otherwise replace the server's copy - with all its multiplayer progress - by
    # the stale singleplayer save.
    existing_world = server_dir / "world"
    if existing_world.exists():
        backup_name = f"world.replaced-{time.strftime('%Y%m%d-%H%M%S')}"
        existing_world.rename(server_dir / backup_name)
        lines.append(
            f"This world was already set up here, so its existing server copy was moved to "
            f"{backup_name!r} instead of being overwritten. If that's the copy people have "
            f"actually been playing on, that's where its progress is - nothing was deleted."
        )

    lines.append(f"Copying world save into {server_dir} ...")
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
        failure = _whitelist_owner(server_dir, owner_uuid, owner_name, lines)
        if failure:
            return failure
        not_whitelisted_reason = None
    else:
        # find_owner_uuid returns None for both "no players" and "several"; a UUID
        # with no name means Mojang's lookup failed (often just the network).
        if owner_uuid:
            why = "found the owner's account ID, but couldn't look up their username from Mojang"
        else:
            why = "zero or several players found in this world's saved data"
        not_whitelisted_reason = f"Couldn't automatically identify the world owner ({why})"
    from .actions import get_whitelist_info

    whitelist_on, whitelist_names = get_whitelist_info(server_dir)
    if not whitelisted and whitelist_on:
        # Re-setup of a server whose whitelist is already on: write_server_properties
        # keeps it on, so "leaving it OFF" plus the open-server disclaimer would be false.
        if whitelist_names:
            lines.append(
                f"{not_whitelisted_reason}. This server's existing whitelist stays ON with its "
                f"current players ({', '.join(whitelist_names)}) - nothing about who can join was changed."
            )
        else:
            lines.append(
                f"{not_whitelisted_reason}. This server's whitelist is ON but has nobody on it, so "
                "nobody - including you - can join. Add yourself with `run.bat whitelist-add <username>` "
                "before starting it."
            )
    else:
        lines.extend(_whitelist_result_lines(whitelisted, owner_name, not_whitelisted_reason))

    return ActionResult(
        True,
        lines,
        data={"owner_uuid": owner_uuid, "owner_name": owner_name, "whitelisted": whitelisted},
    )


def prepare_new_world(instance_dir, world_name, owner_username):
    """Sets up a server directory for a brand-new world, which the server generates on
    first start. The owner comes from a typed-in username - required, since the
    whitelist is always on for a new world."""
    if not owner_username or not owner_username.strip():
        return ActionResult(False, ["A Minecraft username is required - nobody can join a whitelisted server without one."])
    refusal = _refuse_while_active_world_running()
    if refusal:
        return refusal
    if not valid_new_world_name(world_name):
        return ActionResult(False, [f"{world_name!r} isn't a valid world name."])

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
        failure = _whitelist_owner(server_dir, owner_uuid, owner_name, lines)
        if failure:
            return failure
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


def _copy_mods_and_report(lines, instance_dir, server_dir):
    """Shared by every mod loader - they're all jar drops in mods/."""
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


def finish_setup(
    instance_dir, world_name, mc_version, loader, owner_uuid, owner_name, copy_mods=True, world_options=None
):
    """Downloads/installs the matching server, ops the owner if known, and writes eula,
    server.properties and config.json. Assumes prepare_world already ran. copy_mods
    is False for a brand-new world, which isn't ported from the instance.
    world_options goes to write_server_properties."""
    # Checked again: Start is one click away between prepare and finish.
    refusal = _refuse_while_active_world_running()
    if refusal:
        return refusal
    cfg = config.load()
    server_dir = SERVERS_DIR / world_name

    setup_failed = False
    lines = []
    if owner_uuid and owner_name:
        try:
            _, note = write_ops(server_dir, owner_uuid, owner_name)
            lines += [f"Opped {owner_name!r}."] + ([note] if note else [])
        except OSError as e:
            lines.append(f"WARNING: couldn't update ops.json ({e}) - {owner_name!r} wasn't opped.")

    # Resolved first: Forge's installer needs a real java. Mojang's manifest is
    # authoritative; world.py's table is only a fallback.
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

    jar_path = server_dir / "server.jar"
    lines.append(f"Downloading {loader} server for Minecraft {mc_version} ...")
    if loader == "vanilla":
        server_vanilla.download_server_jar(mc_version, jar_path)
    elif loader in ("forge", "neoforge"):
        # NeoForge installs and launches like modern Forge - only the installer source
        # and args file location differ.
        if loader == "forge":
            module, label = server_forge, "Forge"
            forge_version = server_forge.get_recommended_forge_version(mc_version)
            installer_path = server_dir / "forge-installer.jar"
            server_forge.download_installer(mc_version, forge_version, installer_path)
        else:
            module, label = server_neoforge, "NeoForge"
            forge_version = server_neoforge.get_latest_neoforge_version(mc_version)
            installer_path = server_dir / "neoforge-installer.jar"
            server_neoforge.download_installer(forge_version, installer_path)
        lines.append(f"{label} {forge_version} - running its installer ...")
        java_exe = shutil.which(cfg.get("java_path") or "java")
        if not java_exe:
            # A real failure, not a warning: there's no runnable server, and "Setup
            # complete." would point config.json at a world that can't start.
            setup_failed = True
            lines.append(
                f"ERROR: no working Java found, so {label}'s installer couldn't run - this world has no "
                f"runnable server yet. Install Java {required_java} (https://adoptium.net/) or fix "
                '"java_path" in config.json, then run setup again.'
            )
        else:
            server_forge.run_installer(java_exe, installer_path, server_dir, name=label)
            installer_path.unlink(missing_ok=True)
            if module.find_launch_args_file(server_dir, mc_version) is None:
                setup_failed = True
                lines.append(
                    "ERROR: the installer finished, but this app doesn't recognize the server "
                    "layout it produced (only modern Forge 1.17+ and NeoForge 1.20.2+ are "
                    "supported) - it won't start."
                )
        if copy_mods:
            _copy_mods_and_report(lines, instance_dir, server_dir)
    else:
        _, loader_version, installer_version = server_fabric.download_server_jar(mc_version, jar_path)
        lines.append(f"Fabric loader {loader_version}, installer {installer_version}")
        if copy_mods:
            _copy_mods_and_report(lines, instance_dir, server_dir)

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
    write_server_properties(
        server_dir, cfg, enable_whitelist=bool(owner_uuid and owner_name), world_options=world_options
    )
    config.save(cfg)
    write_world_meta(server_dir, cfg)

    lines.append(
        f"View/simulation distance set to {config.ensure_view_distance(cfg)}/"
        f"{config.ensure_simulation_distance(cfg)} based on this PC's specs - change this anytime "
        "in the Performance section of the status screen."
    )
    # config and meta are still written on failure, so fixing Java and re-running setup
    # picks up where this left off.
    if setup_failed:
        lines.append("Setup did NOT complete - see the error above. Fix it and run setup again.")
        return ActionResult(False, lines)
    lines.append("Setup complete.")
    return ActionResult(True, lines)


WORLD_META_FIELDS = (
    "instance_dir",
    "loader",
    "mc_version",
    "required_java_major",
    "memory_auto",
    "memory_mb",
    "performance_auto",
    "view_distance",
    "simulation_distance",
)


def write_world_meta(server_dir, cfg):
    """Snapshots the settings needed to make this world active again without re-running
    setup; the rest lives in its own server.properties/whitelist.json."""
    meta = {field: cfg.get(field) for field in WORLD_META_FIELDS}
    (Path(server_dir) / "mcpersist_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def list_known_servers():
    """Every world MCPersist has set up, found by scanning servers/ - the filesystem is
    the source of truth."""
    if not SERVERS_DIR.exists():
        return []
    found = []
    for entry in sorted(SERVERS_DIR.iterdir()):
        meta_path = entry / "mcpersist_meta.json"
        # ".importing-*" is an import in progress, possibly already holding a copied
        # mcpersist_meta.json.
        if not entry.is_dir() or entry.name.startswith(".") or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        found.append(
            {"world_name": entry.name, "loader": meta.get("loader"), "mc_version": meta.get("mc_version")}
        )
    return found


def import_worlds_from(source_root):
    """Recovers worlds from another MCPersist install (say, a manual reinstall). Each
    world folder is self-contained, so this is a folder copy. Accepts the old
    install's root or its servers/ folder, and skips (and reports) any world whose
    name already exists here."""
    source_root = Path(source_root)
    source_servers = source_root / "servers"
    if not source_servers.is_dir():
        source_servers = source_root

    try:
        source_servers = source_servers.resolve()
    except OSError:
        return ActionResult(False, [f"{source_root} isn't a folder this can read."])
    if source_servers == SERVERS_DIR.resolve():
        return ActionResult(False, ["That's already this install's own servers folder - nothing to import."])

    # Skip dot-prefixed dirs: a leftover ".importing-<name>" in the source is a partial
    # copy, not a world.
    candidates = [
        entry
        for entry in sorted(source_servers.iterdir())
        if entry.is_dir() and not entry.name.startswith(".") and (entry / "mcpersist_meta.json").exists()
    ] if source_servers.is_dir() else []

    if not candidates:
        return ActionResult(
            False,
            [
                f"No MCPersist worlds found under {source_servers} - pick the old install's main "
                'folder (the one with MCPersist.exe in it) or its "servers" folder directly.'
            ],
        )

    imported, skipped, failed = [], [], []
    for entry in candidates:
        dest = SERVERS_DIR / entry.name
        if dest.exists():
            skipped.append(entry.name)
            continue
        # Copied to a staging directory and renamed into place: a copy that dies halfway
        # must not leave a partial world that lists as valid (Minecraft would regenerate
        # the missing chunks as fresh terrain).
        staging = SERVERS_DIR / f".importing-{entry.name}"
        shutil.rmtree(staging, ignore_errors=True)  # leftover from an earlier interrupted attempt
        try:
            shutil.copytree(entry, staging)
            staging.rename(dest)
            imported.append(entry.name)
        except OSError as e:
            shutil.rmtree(staging, ignore_errors=True)
            failed.append(f"{entry.name} ({e})")

    lines = []
    if imported:
        lines.append(f"Imported {len(imported)} world(s): {', '.join(imported)}.")
        lines.append('Use "Set Up a Server" -> "Switch to a Previously Set-Up Server" to start using one.')
    if skipped:
        lines.append(
            f"Skipped {len(skipped)} world(s) already present here (not overwritten): {', '.join(skipped)}."
        )
    if failed:
        lines.append(f"Couldn't import {len(failed)} world(s): {', '.join(failed)}.")
        lines.append("Nothing partial was left behind for those - they can be retried.")
    return ActionResult(
        not failed or bool(imported),
        lines,
        data={"imported": imported, "skipped": skipped, "failed": failed},
    )


def switch_to_world(world_name):
    """Makes a previously set-up world the active one: repoints config.json using its
    saved metadata plus the rcon settings in its server.properties."""
    server_dir = SERVERS_DIR / world_name
    meta_path = server_dir / "mcpersist_meta.json"
    if not meta_path.exists():
        return ActionResult(
            False, [f"{world_name!r} doesn't look like a world MCPersist set up (no saved settings found)."]
        )
    refusal = _refuse_while_active_world_running(switch_target=world_name)
    if refusal:
        return refusal
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return ActionResult(False, [f"Couldn't read {world_name!r}'s saved settings: {e}"])

    cfg = config.load()
    cfg["world_name"] = world_name
    for field in WORLD_META_FIELDS:
        if field in meta:
            cfg[field] = meta[field]

    props = read_server_properties(server_dir)
    if props.get("rcon.port", "").isdigit():
        cfg["rcon_port"] = int(props["rcon.port"])
    if props.get("rcon.password"):
        cfg["rcon_password"] = props["rcon.password"]

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
