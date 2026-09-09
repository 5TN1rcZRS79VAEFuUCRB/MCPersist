"""Loads/saves config.json and resolves the memory (RAM) allocation to use."""

import json
import secrets

from .paths import CONFIG_PATH, SERVERS_DIR

DEFAULTS = {
    "instance_dir": None,
    "world_name": None,
    "loader": "vanilla",
    "mc_version": None,
    "java_path": "java",
    "memory_auto": True,
    "memory_mb": None,
    "rcon_port": 25575,
    "rcon_password": None,
    "join_address": None,
    "relay_host": "5.161.120.124",
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
    merged = dict(DEFAULTS)
    merged.update(data)
    return merged


def save(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def server_dir(cfg):
    if not cfg.get("world_name"):
        return None
    return SERVERS_DIR / cfg["world_name"]


def new_rcon_password():
    return secrets.token_urlsafe(18)


MIN_MEMORY_GB = 2  # below this, a Java-based Minecraft server generally can't boot reliably


def suggest_memory_mb():
    """A background-persistent server on the user's own PC has to share it with
    everything else they're doing (gaming, browsing) - unlike a dedicated headless
    box. A small friend-group survival world rarely benefits much from a big heap
    regardless of how much RAM the machine has, so this is a flat, low-ceiling table
    rather than a percentage split - the goal is "enough for Minecraft," not "half
    the machine," so most of the RAM stays free for whatever else is running."""
    import psutil

    total_gb = max(1, int(psutil.virtual_memory().total / (1024**3)))
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


def ensure_memory_mb(cfg):
    """Resolves the memory allocation to use right now. When memory_auto is on
    (the default), this is always freshly computed from current specs - not just
    once - so it stays correct even if the machine's hardware changes later. When
    off, uses the user's explicit memory_mb choice, with the MIN_MEMORY_GB floor
    enforced regardless (a Java-based Minecraft server generally can't boot reliably
    below that, even if someone hand-edits config.json to something lower)."""
    if cfg.get("memory_auto", True):
        return suggest_memory_mb()
    mb = cfg.get("memory_mb") or suggest_memory_mb()
    return max(mb, MIN_MEMORY_GB * 1024)
