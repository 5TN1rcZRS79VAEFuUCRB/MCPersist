"""Reads Minecraft world saves: finding worlds in an instance folder, detecting the
Minecraft version/mod loader, copying a save, and identifying its owner."""

import os
import re
import shutil
from pathlib import Path

import nbtlib


def default_instance_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / ".minecraft"
    return path if path.exists() else None


def list_saves(instance_dir):
    saves_dir = Path(instance_dir) / "saves"
    if not saves_dir.exists():
        return []
    result = []
    for entry in sorted(saves_dir.iterdir()):
        if entry.is_dir() and (entry / "level.dat").exists():
            result.append(entry)
    return result


def read_mc_version(save_path):
    level_dat = Path(save_path) / "level.dat"
    try:
        nbt_file = nbtlib.load(level_dat)
        data = nbt_file.get("Data", {})
        version = data.get("Version", {})
        name = version.get("Name")
        return str(name) if name is not None else None
    except Exception:
        return None


SUPPORTED_LOADERS = ("vanilla", "fabric")


def _detect_loader_from_curseforge_manifest(instance_dir):
    """CurseForge isn't itself a loader - it's a launcher wrapping Forge/NeoForge/
    Fabric underneath. minecraftinstance.json's exact schema isn't officially
    documented, so this is best-effort: if the field we expect isn't there in the
    shape we expect, just report "unknown" and let the other, more reliable checks
    (versions/ folder naming, fabric mod jars) decide instead."""
    manifest_path = Path(instance_dir) / "minecraftinstance.json"
    if not manifest_path.exists():
        return None
    try:
        import json

        data = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        name = (data.get("baseModLoader") or {}).get("name", "")
        name = name.lower()
        if "neoforge" in name:
            return "neoforge"
        if "forge" in name:
            return "forge"
        if "fabric" in name:
            return "fabric"
    except Exception:
        pass
    return None


def detect_loader(instance_dir):
    instance_dir = Path(instance_dir)

    from_curseforge = _detect_loader_from_curseforge_manifest(instance_dir)
    if from_curseforge:
        return from_curseforge

    versions_dir = instance_dir / "versions"
    if versions_dir.exists():
        names = " ".join(p.name.lower() for p in versions_dir.iterdir() if p.is_dir())
        if "neoforge" in names:
            return "neoforge"
        if "forge" in names:
            return "forge"

    mods_dir = instance_dir / "mods"
    if mods_dir.exists():
        for jar in mods_dir.glob("*.jar"):
            if "fabric-loader" in jar.name.lower() or re.search(r"fabric-api", jar.name, re.I):
                return "fabric"

    return "vanilla"


def copy_world(save_path, dest_dir, world_subdir_name="world"):
    dest = Path(dest_dir) / world_subdir_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(save_path, dest)
    return dest


def find_owner_uuid(server_world_dir):
    """A singleplayer world's per-player-data folder has one <uuid>.dat per player
    who's ever spawned in it - for a fresh singleplayer world that's exactly one file,
    and its name is the owner's UUID. Ambiguous cases (0 or 2+) return None rather
    than guessing wrong. Checks both the traditional path (playerdata/) and the
    layout newer Minecraft versions (26.2+) restructured it into (players/data/)."""
    for candidate in ("players/data", "playerdata"):
        playerdata_dir = Path(server_world_dir) / candidate
        if not playerdata_dir.exists():
            continue
        uuids = [p.stem for p in playerdata_dir.glob("*.dat") if not p.stem.endswith("_old")]
        if len(uuids) == 1:
            return uuids[0]
        return None
    return None


def read_prism_java_major(instance_dir):
    """Prism Launcher records the Java version it resolved for an instance in
    instance.cfg (one level above the .minecraft folder) - trust that over our
    own version->Java guess table, since it's launcher-maintained and won't go
    stale as new Minecraft versions ship."""
    cfg_path = Path(instance_dir).parent / "instance.cfg"
    if not cfg_path.exists():
        return None
    for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("JavaVersion="):
            value = line.split("=", 1)[1].strip()
            match = re.match(r"^(\d+)", value)
            if match:
                return int(match.group(1))
    return None


def required_java_major(mc_version_str, instance_dir=None):
    if instance_dir:
        from_prism = read_prism_java_major(instance_dir)
        if from_prism:
            return from_prism

    if not mc_version_str:
        return 17
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", mc_version_str)
    if not match:
        return 17
    major, minor = int(match.group(1)), int(match.group(2))
    patch = int(match.group(3) or 0)
    if (major, minor, patch) >= (1, 20, 5):
        return 21
    if (major, minor, patch) >= (1, 17, 0):
        return 17
    return 8
