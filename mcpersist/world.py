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


# MultiMC-family launchers (Prism's own lineage) all use the same on-disk shape:
# <LauncherRoot>/instances/<name>/minecraft/ (or, on some forks/older versions,
# .minecraft/). Checked by folder name under %APPDATA% rather than anything more
# specific, so a launcher added later just needs its folder name added here.
_MULTIMC_FAMILY_LAUNCHERS = ("PrismLauncher", "MultiMC", "PolyMC", "ATLauncher")

# The Modrinth App (Theseus) uses a differently-named top-level folder
# ("profiles", not "instances") and a flat layout - Minecraft's own files sit
# directly under <profile>/, with no nested minecraft/.minecraft subfolder the
# way the MultiMC family has - confirmed against Modrinth's own support docs
# (%APPDATA%\ModrinthApp\profiles\<name>\saves\<world>\), not guessed. The
# second entry is where older installs kept the same layout before a rename.
_MODRINTH_LAUNCHERS = ("ModrinthApp", "com.modrinth.theseus")

# A directory "looks like" a real Minecraft instance if it has any of these - not
# launcher-specific parsing, so this works across launchers (and Minecraft versions)
# without needing to know each one's exact metadata format. A brand-new instance
# that's never actually been launched yet may have none of these, but there's
# nothing useful to promote from one anyway (no world to detect a version/loader
# from), so missing all of them is a reasonable thing to skip.
_INSTANCE_MARKERS = ("saves", "mods", "resourcepacks", "options.txt", "servers.dat")


def _looks_like_instance_dir(path):
    return any((path / marker).exists() for marker in _INSTANCE_MARKERS)


def find_instances():
    """Scans common launcher locations under %APPDATA% for Minecraft instance
    folders, so setup can offer a pick-list instead of requiring the instance
    folder to be typed in or browsed to by hand every time. Best-effort and
    heuristic (see _looks_like_instance_dir) rather than parsing each launcher's
    own format, so it degrades gracefully instead of needing an update every time
    a launcher changes its metadata - and manual entry/Browse still work
    regardless of what this finds. Returns a list of {"name", "path"} dicts,
    already de-duplicated by resolved path."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    appdata = Path(appdata)
    found = []
    seen_paths = set()

    def add(name, path):
        resolved = str(path.resolve())
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        found.append({"name": name, "path": resolved})

    vanilla = appdata / ".minecraft"
    if vanilla.exists() and _looks_like_instance_dir(vanilla):
        add("Official Launcher", vanilla)

    for launcher_name in _MULTIMC_FAMILY_LAUNCHERS:
        instances_dir = appdata / launcher_name / "instances"
        if not instances_dir.exists():
            continue
        try:
            entries = sorted(instances_dir.iterdir())
        except OSError:
            continue
        for instance_dir in entries:
            if not instance_dir.is_dir():
                continue
            nested = next(
                (instance_dir / sub for sub in ("minecraft", ".minecraft") if (instance_dir / sub).exists()),
                None,
            )
            if nested is not None and _looks_like_instance_dir(nested):
                add(f"{instance_dir.name} ({launcher_name})", nested)
            elif _looks_like_instance_dir(instance_dir):
                # Some launchers (e.g. ATLauncher) put Minecraft's own files
                # directly in the instance folder instead of a nested subfolder.
                add(f"{instance_dir.name} ({launcher_name})", instance_dir)

    for launcher_name in _MODRINTH_LAUNCHERS:
        profiles_dir = appdata / launcher_name / "profiles"
        if not profiles_dir.exists():
            continue
        try:
            entries = sorted(profiles_dir.iterdir())
        except OSError:
            continue
        for profile_dir in entries:
            if profile_dir.is_dir() and _looks_like_instance_dir(profile_dir):
                add(f"{profile_dir.name} (Modrinth App)", profile_dir)

    return found


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


SUPPORTED_LOADERS = ("vanilla", "fabric", "forge")


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


def read_prism_intended_version(instance_dir):
    """Prism Launcher records the instance's Minecraft version in instance.cfg (one
    level above the .minecraft folder) - used to prefill the version field when
    generating a brand-new world, since there's no level.dat yet to read it from."""
    cfg_path = Path(instance_dir).parent / "instance.cfg"
    if not cfg_path.exists():
        return None
    for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("IntendedVersion="):
            return line.split("=", 1)[1].strip() or None
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
