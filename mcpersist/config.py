"""Loads/saves config.json and resolves the memory/view-distance/simulation-distance
settings to use, each auto-sized from system specs unless overridden."""

import json
import os
import secrets

from .paths import CONFIG_PATH, SERVERS_DIR

DEFAULTS = {
    "instance_dir": None,
    "world_name": None,
    "loader": "vanilla",
    "mc_version": None,
    "java_path": "java",
    "java_auto": True,
    "required_java_major": None,
    "memory_auto": True,
    "memory_mb": None,
    "performance_auto": True,
    "view_distance": None,
    "simulation_distance": None,
    "rcon_port": 25575,
    "rcon_password": None,
    "join_address": None,
    # A hostname, not a bare IP: TLS certificate verification needs one.
    "relay_host": "relay.mcpersist.com",
    "relay_control_port": 7000,
    "relay_data_port": 7001,
    "subdomain": None,
    "relay_token": None,
    "public_domain": "mcpersist.com",
}


def load():
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    # utf-8-sig transparently strips a BOM if present (e.g. from Notepad) without
    # affecting plain utf-8 files, so hand-edited config.json can't crash startup.
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return {**DEFAULTS, **data}


def save(cfg):
    # Write-then-rename: os.replace is atomic, so a crash mid-write can't leave a
    # truncated config.json that breaks every launch.
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp_path, CONFIG_PATH)


def server_dir(cfg):
    if not cfg.get("world_name"):
        return None
    return SERVERS_DIR / cfg["world_name"]


def new_rcon_password():
    return secrets.token_urlsafe(18)


MIN_MEMORY_GB = 2  # below this, a Java-based Minecraft server generally can't boot reliably


def total_ram_gb():
    import psutil

    return max(1, int(psutil.virtual_memory().total / (1024**3)))


def suggest_memory_mb():
    """A flat, low-ceiling table rather than a share of total RAM: the server shares the
    PC with everything else, and a small world rarely benefits from a big heap."""
    total_gb = total_ram_gb()
    if total_gb <= 4:
        suggested_gb = max(MIN_MEMORY_GB, total_gb - 1)
    elif total_gb <= 8:
        suggested_gb = 2
    elif total_gb <= 16:
        suggested_gb = 4
    elif total_gb <= 32:
        suggested_gb = 6
    else:
        suggested_gb = 8
    return suggested_gb * 1024


def suggest_view_distance():
    """CPU-bound, and the server shares the CPU with everything else - so this favours
    running smoothly over maxing out the hardware."""
    cores = os.cpu_count() or 4
    if cores <= 4:
        return 6
    elif cores <= 8:
        return 8
    elif cores <= 12:
        return 10
    else:
        return 12


def suggest_simulation_distance():
    # Simulation distance drives ticking, which costs more per chunk than rendering -
    # keep it a bit below view distance.
    return max(4, suggest_view_distance() - 2)


MIN_VIEW_DISTANCE = 3
MIN_SIMULATION_DISTANCE = 2
MAX_DISTANCE = 32  # Minecraft's own ceiling for both


def _resolve_distance(cfg, key, suggest, minimum):
    """Freshly recomputed from current specs when performance_auto is on, otherwise the
    user's choice - clamped either way, since config.json can be hand-edited."""
    if cfg.get("performance_auto", True):
        return suggest()
    return min(MAX_DISTANCE, max(minimum, cfg.get(key) or suggest()))


def ensure_view_distance(cfg):
    return _resolve_distance(cfg, "view_distance", suggest_view_distance, MIN_VIEW_DISTANCE)


def ensure_simulation_distance(cfg):
    return _resolve_distance(cfg, "simulation_distance", suggest_simulation_distance, MIN_SIMULATION_DISTANCE)


def ensure_memory_mb(cfg):
    """Freshly computed from current specs when memory_auto is on, otherwise the user's
    memory_mb - with the MIN_MEMORY_GB floor enforced either way, since config.json
    can be hand-edited."""
    if cfg.get("memory_auto", True):
        return suggest_memory_mb()
    mb = cfg.get("memory_mb") or suggest_memory_mb()
    return max(mb, MIN_MEMORY_GB * 1024)
