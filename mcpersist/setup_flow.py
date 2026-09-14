"""The actual logic behind `setup` and `configure-relay`: world detection, copying
the save, downloading a matching server, owner whitelisting - shared by the CLI and
the GUI so neither duplicates it."""

import json
import shutil
import time
from pathlib import Path

from . import config, java_manager, javacheck, mojang, server_fabric, server_forge, server_neoforge, server_vanilla, world
from .actions import ActionResult
from .paths import SERVERS_DIR


def _refuse_while_active_world_running(switch_target=None):
    """Setting up or switching worlds repoints config.json at another world. Doing
    that while the active world's server or tunnel is still running orphans them:
    the app can no longer see or stop them, the next Start collides with the old
    server still holding port 25565, and a second tunnel gets refused by the relay.
    Re-running setup on the running world itself is worse - it moves the world
    folder aside while Minecraft has files open in it.

    Returns an ActionResult refusal, or None when it's safe to proceed. Switching to
    the world that's already active changes nothing, so that stays allowed."""
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


def default_instance_dir():
    return str(world.default_instance_dir() or "")


def find_instances():
    return world.find_instances()


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


def _is_path_safe_name(world_name):
    """The security-relevant half of valid_new_world_name, without its length cap.
    It becomes a directory name directly under servers/ (no separators/"..", so it
    can't escape that directory) and gets written verbatim into server.properties'
    motd line - also reject control characters (newlines in particular), which
    would otherwise inject extra lines into that file. Used on its own for an
    existing world's name (see prepare_world) - already a real directory on disk,
    so already a legal Windows path component, but the length cap below exists for
    freshly-typed-name sanity, not as part of the actual safety boundary, so it
    shouldn't reject a long name that was already fine as a real folder."""
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
# Display label -> the actual server.properties value. Namespaced ("minecraft:...")
# rather than the older bare-word values (DEFAULT/FLAT/...) - matches what modern
# (1.19+) servers, the only ones this app targets, actually expect.
LEVEL_TYPES = {
    "Default": "minecraft:normal",
    "Superflat": "minecraft:flat",
    "Large Biomes": "minecraft:large_biomes",
    "Amplified": "minecraft:amplified",
    "Single Biome": "minecraft:single_biome_surface",
}


def write_eula(server_dir):
    (Path(server_dir) / "eula.txt").write_text("eula=true\n", encoding="utf-8")


def write_server_properties(server_dir, cfg, enable_whitelist, world_options=None):
    """world_options optionally carries the new-world generation settings (gamemode,
    difficulty, level-type, generate-structures, spawn-protection, level-seed) - only
    meaningful the first time a brand-new world is created (an existing, already-
    generated world's terrain/seed can't retroactively change), so callers only pass
    it from the "Generate New World" path."""
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
        # Re-running setup on an existing server (the only way to change its
        # version or loader) used to regenerate this from scratch: every setting
        # the owner had changed (max-players, pvp, difficulty, ...) was lost, and
        # when the owner couldn't be re-detected it wrote white-list=false -
        # quietly opening a locked-down server to anyone. Existing settings are
        # kept; only the keys MCPersist manages are updated, and a whitelist that
        # was on stays on. The motd is left alone too, since it's often customised.
        existing = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k] = v
        if existing.get("white-list", "").strip().lower() == "true":
            props["white-list"] = "true"
        if "motd" in existing:
            props.pop("motd")
        update_server_properties(server_dir, props)
        return
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


def _merge_player_entry(path, entry):
    """Adds/updates one player in a whitelist.json/ops.json-style list, keeping
    everyone else. These used to be overwritten with just the owner, so re-running
    setup on an existing server removed every friend from its whitelist and ops.
    An unreadable file is kept aside rather than silently discarded."""
    path = Path(path)
    players = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise ValueError("not a list")
            players = [p for p in loaded if isinstance(p, dict)]
        except (ValueError, OSError):
            path.replace(path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"))
            players = []
    players = [p for p in players if str(p.get("uuid", "")).lower() != entry["uuid"].lower()]
    players.insert(0, entry)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(players, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_whitelist(server_dir, uuid, name):
    _merge_player_entry(Path(server_dir) / "whitelist.json", {"uuid": uuid, "name": name})


def write_ops(server_dir, uuid, name):
    _merge_player_entry(
        Path(server_dir) / "ops.json", {"uuid": uuid, "name": name, "level": 4, "bypassesPlayerLimit": False}
    )


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
    """Copies the world save and detects/whitelists the owner. Split from
    finish_setup so callers can show the detected owner (and ask about cheats) before
    kicking off the slow server-jar download."""
    if not _is_path_safe_name(world_name):
        return ActionResult(False, [f"{world_name!r} isn't a valid world name."])
    refusal = _refuse_while_active_world_running()
    if refusal:
        return refusal

    save_path = Path(instance_dir) / "saves" / world_name
    # Before anything is created, moved aside or copied: an open world makes the
    # copy fail partway (after an existing server copy was already moved aside)
    # or, without the lock, capture region files mid-write.
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
    # Moved aside rather than left for copy_world to clear: copy_world starts with
    # rmtree(dest), so re-running setup on a world that was already set up here
    # would destroy the server's copy - which by then holds however much
    # multiplayer progress has accumulated since - and replace it with the
    # (likely long-stale) singleplayer save. That's a reachable path, not a
    # hypothetical: re-running setup is the only way to change a world's
    # Minecraft version or mod loader, so wanting to do it on an existing world
    # is a normal thing to want. prepare_new_world already refuses outright in
    # its equivalent situation, but refusing here would block that legitimate
    # reason, so nothing is deleted and the user is told where the old copy went.
    # (The "your original singleplayer world is untouched" note below is about
    # the source save - it says nothing about this destination, which is exactly
    # why losing it here would be so easy to miss.)
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
        write_whitelist(server_dir, owner_uuid, owner_name)
        not_whitelisted_reason = None
    else:
        # find_owner_uuid returns None for both "no players" and "several"; a UUID
        # with no name means Mojang's lookup failed (often just the network) - which
        # used to be reported as "more than one player found".
        if owner_uuid:
            why = "found the owner's account ID, but couldn't look up their username from Mojang"
        else:
            why = "zero or several players found in this world's saved data"
        not_whitelisted_reason = f"Couldn't automatically identify the world owner ({why})"
    existing_props = server_dir / "server.properties"
    whitelist_already_on = existing_props.exists() and any(
        line.strip().lower() == "white-list=true"
        for line in existing_props.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if not whitelisted and whitelist_already_on:
        # Re-setup of a server whose whitelist is already on: write_server_properties
        # keeps it on and whitelist.json is untouched, so "leaving it OFF" plus the
        # open-server disclaimer would be false.
        lines.append(
            f"{not_whitelisted_reason}. This server's existing whitelist stays ON with its "
            "current players - nothing about who can join was changed."
        )
    else:
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


def _copy_mods_and_report(lines, instance_dir, server_dir):
    """Shared by Fabric and Forge - both are just jar drops in a mods/ folder as
    far as this is concerned, loader-specific only in name (it lives in
    server_fabric.py from when Fabric was the only mod loader supported)."""
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
    """Downloads/installs the matching server (vanilla/Fabric: a direct jar
    download; Forge: running its own installer, since it doesn't publish one), ops
    the owner if one was whitelisted, and writes eula/server.properties/
    config.json. Assumes prepare_world already ran.

    copy_mods controls whether the instance's current mods/ folder gets pulled in -
    only ever passed False for a brand-new world, which isn't "ported" from the
    instance the way an existing save is, so dragging along whatever mods that
    instance currently happens to have doesn't make sense the same way.

    world_options is passed straight through to write_server_properties - see there.

    The owner is always opped when known, not a choice - getting into a whitelisted
    server at all already means they're trusted enough to be there, so there's no
    real distinction left to ask about."""
    # Checked again here, not just in prepare: Start is one click away between the
    # two steps, and this is the call that installs over the server and repoints
    # config.json.
    refusal = _refuse_while_active_world_running()
    if refusal:
        return refusal
    cfg = config.load()
    server_dir = SERVERS_DIR / world_name

    setup_failed = False
    lines = []
    if owner_uuid and owner_name:
        write_ops(server_dir, owner_uuid, owner_name)
        lines.append(f"Opped {owner_name!r}.")

    # Resolved before the loader-specific block below, not after (as it used to
    # be) - Forge's installer is itself a jar that has to be run with a real java,
    # so it needs this settled first. Vanilla/Fabric don't need it this early, but
    # aren't harmed by it either.
    #
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

    jar_path = server_dir / "server.jar"
    lines.append(f"Downloading {loader} server for Minecraft {mc_version} ...")
    if loader == "vanilla":
        server_vanilla.download_server_jar(mc_version, jar_path)
    elif loader in ("forge", "neoforge"):
        # NeoForge installs and launches exactly like modern Forge (installer run
        # with --installServer, then a win_args.txt) - only where the installer
        # comes from and where the args file lands differ.
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
        java_exe = javacheck.find_java(cfg.get("java_path") or "java")
        if not java_exe:
            # Recorded as a real failure, not just a warning line: without the
            # installer having run there is no runnable server here at all, and
            # reporting "Setup complete." would make config.json point at a world
            # that cannot start, with the only explanation buried mid-log.
            setup_failed = True
            lines.append(
                f"ERROR: no working Java found, so {label}'s installer couldn't run - this world has no "
                f"runnable server yet. Install Java {required_java} (https://adoptium.net/) or fix "
                '"java_path" in config.json, then run setup again.'
            )
        else:
            module.run_installer(java_exe, installer_path, server_dir)
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
    # config/meta are still written even on failure, deliberately: the world is set
    # up apart from the missing server, so fixing Java and re-running setup (or
    # switching to it later) picks up right where this left off rather than starting
    # over. Only the reported outcome changes - "Setup complete." would be a lie.
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
        # ".importing-*" is an in-progress world import (see import_worlds_from),
        # which can already hold a copied mcpersist_meta.json while the rest of its
        # files are still being written - it would otherwise list as a normal,
        # selectable world mid-copy.
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
    """Recovers worlds from another MCPersist install (e.g. an old folder left
    behind after a manual reinstall, done by hand because self-update failed) -
    each world folder under servers/ is already fully self-contained
    (mcpersist_meta.json + server.properties + whitelist.json + the world save
    itself), the same thing that makes "Switch to a Previous Server" work at
    all, so recovering one is just copying that folder over - nothing further
    to reconstruct. Accepts either the old install's root folder or its
    servers/ folder directly, whichever the user happened to pick. Skips (and
    reports, rather than silently overwriting) any world whose name already
    exists in the current servers/ - if that one still has server.jar/a
    running config the user cares about, clobbering it would be a real loss,
    not a convenience."""
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

    # Dot-prefixed dirs are skipped for the same reason list_known_servers skips
    # them: a ".importing-<name>" left behind by an interrupted import in the
    # SOURCE install is a partial copy, not a world. Importing one would also
    # shadow the real thing - it sorts before the actual name, so it'd be copied
    # and then deleted again by the staging cleanup for the real world, while
    # still being counted as imported.
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
        # Copied into a staging directory and renamed into place, rather than
        # written straight to dest: copytree going directly to the final path
        # leaves a PARTIAL world behind if it dies halfway (a failing external
        # drive, a full disk, a file locked by another program). A partial copy is
        # worse than no copy - it can already contain mcpersist_meta.json, so it
        # lists as a perfectly valid world in "Switch to a Previously Set-Up
        # Server" while its region files are incomplete, and Minecraft regenerates
        # missing chunks as fresh terrain instead of reporting anything wrong.
        # Retrying the import wouldn't help either: dest.exists() would be true, so
        # it'd be reported as "already present (not overwritten)". The rename is
        # atomic within the same filesystem, so dest only ever exists complete.
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
    # Assigned unconditionally, unlike the rest above: leaving a stale value here
    # is actively harmful rather than merely out of date, because it's the Java
    # version the *previously active* world needed and start_server only
    # re-resolves it when it's None. Switching a 1.21 world to a 1.16.5 one would
    # otherwise launch 1.16.5 under Java 21, and the reverse dies instantly with
    # UnsupportedClassVersionError, surfaced only as "Server process exited
    # immediately". Worlds whose meta file predates this field being saved have
    # nothing to restore, so they get it cleared and re-resolved from scratch.
    cfg["required_java_major"] = meta.get("required_java_major")

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
