"""Reads Minecraft world saves: finding worlds in an instance folder, detecting the
Minecraft version/mod loader, copying a save, and identifying its owner."""

import os
import re
import shutil
from pathlib import Path

import nbtlib

from . import javacheck


def default_instance_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / ".minecraft"
    return path if path.exists() else None


# MultiMC-family launchers share one layout: <root>/instances/<name>/minecraft/ (or
# .minecraft/). A new one just needs its folder name here.
_MULTIMC_FAMILY_LAUNCHERS = ("PrismLauncher", "MultiMC", "PolyMC", "ATLauncher")

# The Modrinth App keeps profiles/<name>/ with Minecraft's files directly inside; the
# second name is its older folder.
_MODRINTH_LAUNCHERS = ("ModrinthApp", "com.modrinth.theseus")

# A folder looks like a Minecraft instance if it has any of these - no launcher-specific
# parsing. A never-launched instance has none, but also nothing to set up from.
_INSTANCE_MARKERS = ("saves", "mods", "resourcepacks", "options.txt", "servers.dat")


def _looks_like_instance_dir(path):
    return any((path / marker).exists() for marker in _INSTANCE_MARKERS)


def find_instances():
    """Minecraft instance folders found under %APPDATA%, as [{"name", "path"}]
    de-duplicated by resolved path. Best-effort, so setup can offer a pick-list;
    typing or browsing still works regardless."""
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

    launchers = [(name, "instances", name) for name in _MULTIMC_FAMILY_LAUNCHERS]
    launchers += [(name, "profiles", "Modrinth App") for name in _MODRINTH_LAUNCHERS]
    for folder, subdir, label in launchers:
        try:
            entries = sorted((appdata / folder / subdir).iterdir())
        except OSError:
            continue
        for instance_dir in entries:
            if not instance_dir.is_dir():
                continue
            # MultiMC-family launchers nest Minecraft's files in minecraft/ or
            # .minecraft/; ATLauncher and the Modrinth App don't.
            nested = next((instance_dir / sub for sub in ("minecraft", ".minecraft") if (instance_dir / sub).exists()), None)
            for candidate in (nested, instance_dir):
                if candidate is not None and _looks_like_instance_dir(candidate):
                    add(f"{instance_dir.name} ({label})", candidate)
                    break

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


SUPPORTED_LOADERS = ("vanilla", "fabric", "forge", "neoforge")


def _detect_loader_from_curseforge_manifest(instance_dir):
    """CurseForge wraps Forge/NeoForge/Fabric; its minecraftinstance.json isn't
    documented, so this is best-effort and returns None when unsure, leaving it to
    the other checks."""
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
            if any(part in jar.name.lower() for part in ("fabric-loader", "fabric-api")):
                return "fabric"

    return "vanilla"


def is_world_open(save_path):
    """True if Minecraft has this save open (it holds session.lock while loaded) -
    copying it then fails partway or captures region files mid-write."""
    import msvcrt

    lock_path = Path(save_path) / "session.lock"
    if not lock_path.exists():
        return False
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return True
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def copy_world(save_path, dest_dir, world_subdir_name="world"):
    dest = Path(dest_dir) / world_subdir_name
    if dest.exists():
        shutil.rmtree(dest)
    # session.lock belongs to whichever process has the world open - the server
    # creates its own, and copying the game's one is what fails if it's locked.
    shutil.copytree(save_path, dest, ignore=shutil.ignore_patterns("session.lock"))
    return dest


def find_owner_uuid(server_world_dir):
    """The owner's UUID: a fresh singleplayer world has exactly one <uuid>.dat in its
    player data. None for 0 or 2+ rather than guessing. Checks both playerdata/ and
    the 26.2+ players/data/ layout."""
    for candidate in ("players/data", "playerdata"):
        playerdata_dir = Path(server_world_dir) / candidate
        if not playerdata_dir.exists():
            continue
        uuids = [p.stem for p in playerdata_dir.glob("*.dat") if not p.stem.endswith("_old")]
        if not uuids:
            continue  # an empty new-layout folder shouldn't hide data in the old one
        if len(uuids) == 1:
            return uuids[0]
        return None
    return None


def _read_instance_cfg(instance_dir, key):
    """A value from Prism Launcher's instance.cfg (one level above the .minecraft
    folder), or None."""
    cfg_path = Path(instance_dir).parent / "instance.cfg"
    if not cfg_path.exists():
        return None
    for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def read_prism_java_major(instance_dir):
    value = _read_instance_cfg(instance_dir, "JavaVersion")
    return javacheck.parse_major(value) if value else None


def read_prism_intended_version(instance_dir):
    """Prefills the version for a brand-new world, which has no level.dat to read."""
    return _read_instance_cfg(instance_dir, "IntendedVersion")


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
    # Year-based versions (26.1+) need Java 25 - only reached when Mojang's manifest is
    # unavailable.
    if major >= 26:
        return 25
    if (major, minor, patch) >= (1, 20, 5):
        return 21
    if (major, minor, patch) >= (1, 17, 0):
        return 17
    return 8
