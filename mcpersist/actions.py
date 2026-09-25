"""Start/stop/status logic for the server and tunnel, shared by the CLI and the GUI
so neither duplicates it."""

import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field

from . import config, java_manager, javacheck, mojang, process_manager, rcon, server_forge, server_neoforge


@dataclass
class ActionResult:
    ok: bool
    lines: list = field(default_factory=list)
    data: dict = field(default_factory=dict)


VALID_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def start_server(cfg, server_dir):
    server_pid_path = server_dir / "server.pid"
    if process_manager.is_running(process_manager.read_pid(server_pid_path)):
        return ActionResult(True, ["Server already running."])

    required = cfg["required_java_major"]  # resolved and saved at setup time
    # java_auto (the default) re-resolves a matching Java every start instead of
    # trusting a path from a past run; "java_auto": false trusts java_path as set.
    if not cfg.get("java_auto", True):
        java_path = shutil.which(cfg["java_path"])
        if not java_path:
            return ActionResult(False, [f'"java_path" in config.json ({cfg["java_path"]!r}) was not found.'])
        # A wrong Java launches fine, then dies with a cryptic
        # UnsupportedClassVersionError - say so up front instead.
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

    # Forge and NeoForge (1.17+) launch via an @args-file under libraries/ rather than
    # server.jar; that's the only real difference.
    args_finder = {
        "forge": server_forge.find_launch_args_file,
        "neoforge": server_neoforge.find_launch_args_file,
    }.get(cfg.get("loader"))
    forge_args_file = args_finder(server_dir, cfg.get("mc_version")) if args_finder else None
    jar_path = server_dir / "server.jar"
    if forge_args_file is None and not jar_path.exists():
        return ActionResult(False, [f"Missing {jar_path} - run `run.bat setup` again."])

    # Re-applied before every start so auto-sized settings stay current, without
    # touching anything else in the file.
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
        # Matches Forge's own run.bat: the args file relative to cwd (an absolute path
        # breaks ModLauncher), user_jvm_args.txt alongside, and our -Xmx/-Xms before
        # either @file, since win_args.txt ends in "-jar <shim>" and java treats
        # everything after -jar as program arguments.
        args_file_rel = forge_args_file.relative_to(server_dir)
        cmd = [java_path, f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M"]
        if (server_dir / "user_jvm_args.txt").exists():
            cmd.append("@user_jvm_args.txt")
        cmd += [f"@{args_file_rel}", "nogui"]
    else:
        cmd = [java_path, f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M", "-jar", str(jar_path), "nogui"]
    log_path = server_dir / "logs" / "server.out.log"
    # The log is appended to, so only what this launch writes counts - an earlier run's
    # "Done" would report a crashing server as started.
    log_start = log_path.stat().st_size if log_path.exists() else 0
    pid = process_manager.launch_detached(cmd, cwd=server_dir, log_path=log_path, short_tmp=True)
    process_manager.write_pid(server_pid_path, pid)

    # launch_detached only means the OS accepted the launch. Poll for a quick exit (a
    # locked world, a bad jar - with many mods that takes 7-13s), stopping early once
    # the log says "Done".
    def new_log_text():
        try:
            with open(log_path, "rb") as f:
                f.seek(log_start)
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    for _ in range(24):
        if not process_manager.is_running(pid):
            break
        if "Done (" in new_log_text():
            break
        time.sleep(0.5)
    if not process_manager.is_running(pid):
        server_pid_path.unlink(missing_ok=True)
        tail = "\n".join(new_log_text().splitlines()[-6:])
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

    log_path = server_dir / "logs" / "tunnel.out.log"
    if getattr(sys, "frozen", False):
        # sys.executable is MCPersist.exe itself here - "-m" would relaunch the GUI,
        # so pass the sentinel flag main_gui.py checks for instead.
        cmd = [sys.executable, "--tunnel-relay-run"]
    else:
        cmd = [sys.executable, "-m", "mcpersist.tunnel_relay_run"]
    pid = process_manager.launch_detached(cmd, cwd=server_dir, log_path=log_path)
    process_manager.write_pid(tunnel_pid_path, pid)
    return ActionResult(True, [f"Tunnel started (pid {pid}). Logs: {log_path}"])


def _stop(pid_path, label, stopper):
    """Stops the process in pid_path, forgetting the pid only once it's really gone:
    a process still running untracked would hold the world lock, and the next Start
    would launch a second one against it."""
    pid = process_manager.read_pid(pid_path)
    lines = []
    if process_manager.is_running(pid):
        lines.append(f"Stopping {label} ...")
        stopper(pid)
        if process_manager.is_running(pid):
            lines.append(
                f"WARNING: couldn't actually stop it (pid {pid} is still running) - try Stop again, "
                "or end the process yourself if it's stuck."
            )
            return ActionResult(False, lines)
    pid_path.unlink(missing_ok=True)
    return ActionResult(True, lines)


def stop_server(cfg, server_dir):
    return _stop(
        server_dir / "server.pid",
        "Minecraft server (RCON stop)",
        lambda pid: process_manager.stop_server_gracefully("127.0.0.1", cfg["rcon_port"], cfg["rcon_password"], pid),
    )


def stop_tunnel(server_dir):
    return _stop(server_dir / "tunnel.pid", "tunnel", process_manager.stop_pid)


def get_whitelist_info(server_dir):
    """Read straight from server.properties/whitelist.json, so it stays correct when
    changed by hand or in-game."""
    from .setup_flow import read_server_properties

    value = read_server_properties(server_dir).get("white-list")
    enabled = None if value is None else value.lower() == "true"

    names = []
    try:
        entries = json.loads((server_dir / "whitelist.json").read_text(encoding="utf-8-sig"))
        names = [e["name"] for e in entries if isinstance(e, dict) and e.get("name")]
    except (OSError, ValueError, TypeError):
        pass

    return enabled, names


_TUNNEL_ERROR_MARKERS = ("connection error", "registration failed", "closed the connection", "control connection closed")


def tunnel_log_problem(server_dir):
    """The tunnel's latest log line if it's a failure, so "no address yet" can say why
    (relay unreachable, registration refused) - a firewall blocking the tunnel looks
    the same otherwise."""
    log_path = server_dir / "logs" / "tunnel.out.log"
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line or line.startswith("reconnecting in"):
            continue
        if line == "tunnel starting":  # tunnel_relay_run.TUNNEL_START_MARKER - an earlier run's lines don't count
            return None
        return line if any(m in line for m in _TUNNEL_ERROR_MARKERS) else None
    return None


def get_status(cfg, server_dir):
    server_pid = process_manager.read_pid(server_dir / "server.pid")
    tunnel_pid = process_manager.read_pid(server_dir / "tunnel.pid")

    assigned_path = server_dir / "assigned_address.txt"
    if assigned_path.exists():
        join_address = assigned_path.read_text(encoding="utf-8").strip()
    else:
        join_address = cfg.get("join_address")

    whitelist_enabled, whitelist_names = get_whitelist_info(server_dir)

    tunnel_running = process_manager.is_running(tunnel_pid)
    return {
        # Checked even with a known address: assigned_address.txt outlives the
        # connection, so a tunnel that can no longer connect would otherwise look fine.
        "tunnel_problem": tunnel_log_problem(server_dir) if tunnel_running else None,
        "world_name": cfg.get("world_name"),
        "loader": cfg.get("loader"),
        "mc_version": cfg.get("mc_version"),
        "server_running": process_manager.is_running(server_pid),
        "server_pid": server_pid,
        "tunnel_running": tunnel_running,
        "tunnel_pid": tunnel_pid,
        "join_address": join_address,
        "whitelist_enabled": whitelist_enabled,
        "whitelist_names": whitelist_names,
    }


def add_to_whitelist(cfg, server_dir, username):
    """Adds a player to the whitelist - via RCON if the server's running, otherwise
    straight into whitelist.json for the next start."""
    username = username.strip()
    if not username:
        return ActionResult(False, ["Enter a Minecraft username."])
    if not VALID_USERNAME_RE.match(username):
        return ActionResult(
            False,
            [f"{username!r} isn't a valid Minecraft username (3-16 letters, digits, or underscores)."],
        )

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

    from .setup_flow import write_whitelist

    try:
        added, note = write_whitelist(server_dir, uuid, name)
    except OSError as e:
        return ActionResult(
            False,
            [
                f"Couldn't update whitelist.json ({e}) - {name!r} was not added. Close anything that "
                "might have the file open and try again."
            ],
        )
    if not added:
        return ActionResult(True, [f"{name!r} is already whitelisted."])
    return ActionResult(
        True,
        ([note] if note else []) + [f"Added {name!r} to the whitelist (server isn't running - takes effect on next start)."],
    )
