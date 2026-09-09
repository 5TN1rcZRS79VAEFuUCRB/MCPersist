"""Start/stop/status logic for the server and tunnel, shared by the CLI and the GUI
so neither duplicates it."""

from dataclasses import dataclass, field

from . import config, javacheck, process_manager, tunnel_relay, world


@dataclass
class ActionResult:
    ok: bool
    lines: list = field(default_factory=list)
    data: dict = field(default_factory=dict)


def start_server(cfg, server_dir):
    server_pid_path = server_dir / "server.pid"
    if process_manager.is_running(process_manager.read_pid(server_pid_path)):
        return ActionResult(True, ["Server already running."])

    java_path = javacheck.find_java(cfg["java_path"])
    if not java_path:
        required = world.required_java_major(cfg["mc_version"], instance_dir=cfg.get("instance_dir"))
        return ActionResult(
            False,
            [
                f"Java not found on PATH (Minecraft {cfg['mc_version']} needs Java {required}).",
                "Install it from https://adoptium.net/, or set \"java_path\" in config.json, then try again.",
            ],
        )

    jar_path = server_dir / "server.jar"
    if not jar_path.exists():
        return ActionResult(False, [f"Missing {jar_path} - run `run.bat setup` again."])

    memory_mb = config.ensure_memory_mb(cfg)
    cmd = [java_path, f"-Xmx{memory_mb}M", f"-Xms{memory_mb}M", "-jar", str(jar_path), "nogui"]
    pid = process_manager.launch_detached(
        cmd, cwd=server_dir, log_path=server_dir / "logs" / "server.out.log", short_tmp=True
    )
    process_manager.write_pid(server_pid_path, pid)
    return ActionResult(True, [f"Server started (pid {pid}). Logs: {server_dir / 'logs' / 'server.out.log'}"])


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
    server_pid = process_manager.read_pid(server_dir / "server.pid")
    lines = []
    if process_manager.is_running(server_pid):
        lines.append("Stopping Minecraft server (RCON stop) ...")
        process_manager.stop_server_gracefully("127.0.0.1", cfg["rcon_port"], cfg["rcon_password"], server_pid)
    (server_dir / "server.pid").unlink(missing_ok=True)
    return ActionResult(True, lines)


def stop_tunnel(server_dir):
    tunnel_pid = process_manager.read_pid(server_dir / "tunnel.pid")
    lines = []
    if process_manager.is_running(tunnel_pid):
        lines.append("Stopping tunnel ...")
        process_manager.stop_pid(tunnel_pid)
    (server_dir / "tunnel.pid").unlink(missing_ok=True)
    return ActionResult(True, lines)


def get_status(cfg, server_dir):
    server_pid = process_manager.read_pid(server_dir / "server.pid")
    tunnel_pid = process_manager.read_pid(server_dir / "tunnel.pid")

    assigned_path = server_dir / "assigned_address.txt"
    if assigned_path.exists():
        join_address = assigned_path.read_text(encoding="utf-8").strip()
    else:
        join_address = cfg.get("join_address")

    return {
        "world_name": cfg.get("world_name"),
        "loader": cfg.get("loader"),
        "mc_version": cfg.get("mc_version"),
        "server_running": process_manager.is_running(server_pid),
        "server_pid": server_pid,
        "tunnel_running": process_manager.is_running(tunnel_pid),
        "tunnel_pid": tunnel_pid,
        "join_address": join_address,
    }
