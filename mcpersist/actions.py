"""Start/stop/status logic for the server and tunnel, shared by the CLI and the GUI
so neither duplicates it."""

import json
import time
from dataclasses import dataclass, field

from . import config, java_manager, javacheck, mojang, process_manager, rcon, server_forge, server_vanilla, tunnel_relay, world


@dataclass
class ActionResult:
    ok: bool
    lines: list = field(default_factory=list)
    data: dict = field(default_factory=dict)


def start_server(cfg, server_dir):
    server_pid_path = server_dir / "server.pid"
    if process_manager.is_running(process_manager.read_pid(server_pid_path)):
        return ActionResult(True, ["Server already running."])

    # required_java_major is resolved (from Mojang's manifest, authoritative) and
    # persisted at setup time. For a config from before that existed, try the same
    # live lookup here (and cache it) rather than falling straight to world.py's
    # hardcoded table, which goes stale every time a new Minecraft version bumps its
    # Java requirement - falling back to it is a last resort, not the first guess.
    required = cfg.get("required_java_major")
    if required is None:
        required = server_vanilla.required_java_major(cfg["mc_version"]) or world.required_java_major(
            cfg["mc_version"], instance_dir=cfg.get("instance_dir")
        )
        cfg["required_java_major"] = required
        config.save(cfg)
    # java_auto (default True, same pattern as memory_auto): re-resolves a matching
    # Java every start rather than trusting a path persisted from a past run, so a
    # correction here (or in the required-version lookup) actually takes effect
    # instead of being stuck on whatever was auto-downloaded once. Only a deliberate
    # "java_auto": false override skips this and trusts "java_path" exactly as set.
    if not cfg.get("java_auto", True):
        java_path = javacheck.find_java(cfg["java_path"])
        if not java_path:
            return ActionResult(False, [f'"java_path" in config.json ({cfg["java_path"]!r}) was not found.'])
        # Checked here, not just at setup time: an outdated/wrong Java launches fine
        # (find_java only checks it exists) but crashes the server instantly with a
        # cryptic UnsupportedClassVersionError - catching the mismatch before
        # launching gives a clear message instead of a doomed process.
        detected = javacheck.detected_major_version(cfg["java_path"])
        if detected is not None and detected != required:
            return ActionResult(
                False,
                [
                    f"Installed Java is version {detected}, but Minecraft {cfg['mc_version']} needs Java "
                    f"{required} - starting would just crash immediately.",
                    f"Install Java {required} from https://adoptium.net/, or point \"java_path\" in "
                    "config.json at it, then try again.",
                ],
            )
    else:
        try:
            java_path = java_manager.ensure_java(required)
        except Exception as e:
            return ActionResult(
                False,
                [
                    f"Couldn't get a working Java {required} automatically: {e}",
                    "Install it yourself from https://adoptium.net/, or set \"java_path\" in config.json, "
                    "then try again.",
                ],
            )

    # Forge (1.17+) doesn't produce a directly runnable server.jar the way vanilla/
    # Fabric do - it's launched via an @args-file under libraries/ instead (see
    # server_forge.find_launch_args_file). Everything else about starting it is
    # identical, so this is the only real branch point.
    forge_args_file = server_forge.find_launch_args_file(server_dir) if cfg.get("loader") == "forge" else None
    jar_path = server_dir / "server.jar"
    if forge_args_file is None and not jar_path.exists():
        return ActionResult(False, [f"Missing {jar_path} - run `run.bat setup` again."])

    # Re-applied fresh before every start, same reasoning as the memory sizing below -
    # stays current if performance_auto is on and the hardware or config changes,
    # without touching anything else in the file (the whitelist toggle, in particular,
    # can change independently via RCON/in-game).
    from . import setup_flow

    setup_flow.update_server_properties(
        server_dir,
        {
            "view-distance": config.ensure_view_distance(cfg),
            "simulation-distance": config.ensure_simulation_distance(cfg),
        },
    )

    memory_mb = config.ensure_memory_mb(cfg)
    if forge_args_file is not None:
        # Matches Forge's own generated run.bat exactly (java @user_jvm_args.txt
        # @libraries/.../win_args.txt nogui), not a reconstruction from first
        # principles - confirmed by direct reproduction that deviating from it
        # breaks the launch: an *absolute* path for the args file (instead of
        # relative to server_dir, which this always runs with as cwd) made
        # ModLauncher fail applying its access transformers to core Minecraft
        # server classes before the server ever really started. user_jvm_args.txt
        # is real Forge output too (normally just commented-out placeholders for
        # -Xmx/-Xms) - referencing it costs nothing and matches what Forge itself
        # expects to be read alongside win_args.txt. Our own -Xmx/-Xms still have
        # to come *before* either @file, since win_args.txt's own content ends in
        # "-jar <shim>" - once a "-jar" appears, java treats everything after it
        # as program arguments, not JVM flags, so anything meant as a JVM flag
        # (ours included) has to be placed earlier than that.
        args_file_rel = forge_args_file.relative_to(server_dir)
        cmd = [java_path, f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M"]
        if (server_dir / "user_jvm_args.txt").exists():
            cmd.append("@user_jvm_args.txt")
        cmd += [f"@{args_file_rel}", "nogui"]
    else:
        cmd = [java_path, f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M", "-jar", str(jar_path), "nogui"]
    log_path = server_dir / "logs" / "server.out.log"
    pid = process_manager.launch_detached(cmd, cwd=server_dir, log_path=log_path, short_tmp=True)
    process_manager.write_pid(server_pid_path, pid)

    # launch_detached() only confirms the OS accepted the launch, not that the server
    # actually came up - a world directory already locked by another still-running
    # server process (see stop_server's pid-file fix above for how that could happen),
    # a corrupt jar, or any other fast-crash cause all look identical to success
    # without this check. Polling rather than one fixed sleep: a real doomed launch
    # (e.g. the world-lock case) only fails *after* the JVM boots and 50+ mods load -
    # confirmed by reproduction to take 7-13s, not the couple seconds a single sleep
    # would catch - but exits as soon as the log reports "Done" too, so a normal
    # successful start isn't stuck waiting out the full window on the common path.
    for _ in range(24):
        if not process_manager.is_running(pid):
            break
        if log_path.exists() and "Done" in log_path.read_text(encoding="utf-8", errors="replace"):
            break
        time.sleep(0.5)
    if not process_manager.is_running(pid):
        server_pid_path.unlink(missing_ok=True)
        tail = ""
        if log_path.exists():
            tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-6:])
        return ActionResult(
            False,
            [
                "Server process exited immediately - it didn't actually start.",
                tail if tail else f"Check {log_path} for details.",
            ],
        )
    return ActionResult(True, [f"Server started (pid {pid}). Logs: {log_path}"])


def start_tunnel(cfg, server_dir):
    tunnel_pid_path = server_dir / "tunnel.pid"
    if process_manager.is_running(process_manager.read_pid(tunnel_pid_path)):
        return ActionResult(True, ["Tunnel already running."])

    if not cfg.get("relay_host"):
        return ActionResult(False, ['No relay configured - check "relay_host" in config.json.'])

    pid, log_path = tunnel_relay.launch(server_dir)
    process_manager.write_pid(tunnel_pid_path, pid)
    return ActionResult(True, [f"Tunnel started (pid {pid}). Logs: {log_path}"])


def stop_server(cfg, server_dir):
    server_pid_path = server_dir / "server.pid"
    server_pid = process_manager.read_pid(server_pid_path)
    lines = []
    if process_manager.is_running(server_pid):
        lines.append("Stopping Minecraft server (RCON stop) ...")
        process_manager.stop_server_gracefully("127.0.0.1", cfg["rcon_port"], cfg["rcon_password"], server_pid)
        # Only forget the pid if the process is actually gone - deleting it
        # unconditionally (the previous behavior) meant a stop that failed to
        # actually kill the process (RCON unreachable, or it just took longer than
        # the graceful/kill timeouts) left the app believing "stopped" while a real
        # server was still running and still holding the world's directory lock.
        # The next Start then launched a second java.exe against the same world,
        # which correctly refused to run (Minecraft's own lock, not a new bug there)
        # while the original, now-untracked process kept running indefinitely -
        # exactly "says stopped but still running, still connectable" in practice.
        if process_manager.is_running(server_pid):
            lines.append(
                f"WARNING: couldn't actually stop it (pid {server_pid} is still running) - "
                "leaving it tracked as running rather than losing track of it. Try Stop again, "
                "or end the process yourself if it's stuck."
            )
            return ActionResult(False, lines)
    server_pid_path.unlink(missing_ok=True)
    return ActionResult(True, lines)


def stop_tunnel(server_dir):
    tunnel_pid_path = server_dir / "tunnel.pid"
    tunnel_pid = process_manager.read_pid(tunnel_pid_path)
    lines = []
    if process_manager.is_running(tunnel_pid):
        lines.append("Stopping tunnel ...")
        process_manager.stop_pid(tunnel_pid)
        if process_manager.is_running(tunnel_pid):
            lines.append(f"WARNING: couldn't actually stop it (pid {tunnel_pid} is still running).")
            return ActionResult(False, lines)
    tunnel_pid_path.unlink(missing_ok=True)
    return ActionResult(True, lines)


def get_whitelist_info(server_dir):
    """Reads whitelist status straight from server.properties/whitelist.json rather
    than tracking it separately - stays correct even if someone edits those by hand or
    toggles it via RCON/in-game (`whitelist on`/`off`/`add`/`remove`) rather than
    through MCPersist."""
    enabled = None
    props_path = server_dir / "server.properties"
    if props_path.exists():
        for line in props_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("white-list="):
                enabled = line.split("=", 1)[1].strip().lower() == "true"
                break

    names = []
    wl_path = server_dir / "whitelist.json"
    if wl_path.exists():
        try:
            names = [e.get("name") for e in json.loads(wl_path.read_text(encoding="utf-8")) if e.get("name")]
        except (json.JSONDecodeError, OSError):
            names = []

    return enabled, names


def get_status(cfg, server_dir):
    server_pid = process_manager.read_pid(server_dir / "server.pid")
    tunnel_pid = process_manager.read_pid(server_dir / "tunnel.pid")

    assigned_path = server_dir / "assigned_address.txt"
    if assigned_path.exists():
        join_address = assigned_path.read_text(encoding="utf-8").strip()
    else:
        join_address = cfg.get("join_address")

    whitelist_enabled, whitelist_names = get_whitelist_info(server_dir)

    return {
        "world_name": cfg.get("world_name"),
        "loader": cfg.get("loader"),
        "mc_version": cfg.get("mc_version"),
        "server_running": process_manager.is_running(server_pid),
        "server_pid": server_pid,
        "tunnel_running": process_manager.is_running(tunnel_pid),
        "tunnel_pid": tunnel_pid,
        "join_address": join_address,
        "whitelist_enabled": whitelist_enabled,
        "whitelist_names": whitelist_names,
    }


def add_to_whitelist(cfg, server_dir, username):
    """Adds a player to the whitelist - via RCON if the server's running (takes effect
    immediately), or directly into whitelist.json if it's not (takes effect on next
    start). Either way this is the one thing setup's whitelist disclaimer tells people
    to do, so it's worth being a real button instead of instructions to follow by hand."""
    username = username.strip()
    if not username:
        return ActionResult(False, ["Enter a Minecraft username."])

    server_pid = process_manager.read_pid(server_dir / "server.pid")
    if process_manager.is_running(server_pid):
        try:
            body = rcon.send_command("127.0.0.1", cfg["rcon_port"], cfg["rcon_password"], f"whitelist add {username}")
            return ActionResult(True, [body.strip() or f"Ran `whitelist add {username}`."])
        except Exception as e:
            return ActionResult(False, [f"RCON command failed: {e}"])

    resolved = mojang.uuid_for_username(username)
    if not resolved:
        return ActionResult(False, [f"Couldn't find a Minecraft account named {username!r}."])
    uuid, name = resolved

    wl_path = server_dir / "whitelist.json"
    entries = []
    if wl_path.exists():
        try:
            entries = json.loads(wl_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    if any(e.get("uuid") == uuid for e in entries):
        return ActionResult(True, [f"{name!r} is already whitelisted."])
    entries.append({"uuid": uuid, "name": name})
    wl_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return ActionResult(True, [f"Added {name!r} to the whitelist (server isn't running - takes effect on next start)."])
