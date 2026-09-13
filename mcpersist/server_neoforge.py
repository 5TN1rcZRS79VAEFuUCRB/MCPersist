"""Sets up a NeoForge server. Same shape as Forge (see server_forge.py): no directly
downloadable server jar, so its installer is downloaded and run with
--installServer, and the result is launched via a generated win_args.txt.

Scoped to NeoForge proper (Minecraft 1.20.2+). NeoForge's 1.20.1 builds were a
Forge fork published under different coordinates and aren't handled here."""

import re
import zipfile
from pathlib import Path

import requests

from . import server_forge

VERSIONS_API = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
MAVEN_BASE = "https://maven.neoforged.net/releases/net/neoforged/neoforge"


def version_prefix(mc_version):
    """NeoForge versions encode the Minecraft version they're for, but not verbatim:
    Minecraft 1.21.1 -> 21.1.x, 1.21 -> 21.0.x, and (since Minecraft's year-based
    versioning) 26.2 -> 26.2.0.x, 26.1.1 -> 26.1.1.x. The trailing dot matters -
    without it, 21.1 would also match 21.10's builds."""
    parts = mc_version.strip().split(".")
    if not all(p.isdigit() for p in parts) or len(parts) < 2:
        raise ValueError(f"Unrecognized Minecraft version {mc_version!r}")
    if parts[0] == "1":
        minor = parts[2] if len(parts) > 2 else "0"
        return f"{parts[1]}.{minor}."
    patch = parts[2] if len(parts) > 2 else "0"
    return f"{parts[0]}.{parts[1]}.{patch}."


def _version_key(v):
    return [int(n) for n in re.findall(r"\d+", v.split("-")[0])]


def get_latest_neoforge_version(mc_version):
    """Newest stable build for this Minecraft version, or the newest beta if no
    stable one exists yet (common for the first weeks after a Minecraft release)."""
    prefix = version_prefix(mc_version)
    resp = requests.get(VERSIONS_API, timeout=30)
    resp.raise_for_status()
    candidates = [v for v in resp.json().get("versions", []) if v.startswith(prefix)]
    if not candidates:
        raise ValueError(f"No NeoForge build available for Minecraft {mc_version!r}")
    stable = [v for v in candidates if "-" not in v]
    return max(stable or candidates, key=_version_key)


def download_installer(neoforge_version, dest_path):
    url = f"{MAVEN_BASE}/{neoforge_version}/neoforge-{neoforge_version}-installer.jar"
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    # Same reasoning as server_forge.download_installer: this is about to be run
    # with java -jar, so confirm it's a well-formed jar first.
    if not zipfile.is_zipfile(dest_path):
        Path(dest_path).unlink(missing_ok=True)
        raise ValueError(f"downloaded NeoForge installer {neoforge_version} isn't a valid jar - not using it")
    return dest_path


def run_installer(java_path, installer_path, server_dir):
    return server_forge.run_installer(java_path, installer_path, server_dir, name="NeoForge")


def find_launch_args_file(server_dir, mc_version=None):
    """NeoForge's equivalent of server_forge.find_launch_args_file - same layout,
    under libraries/net/neoforged/neoforge/<neoforge_version>/win_args.txt. The
    directory is named by NeoForge version, not "<mc>-<forge>", so the "which
    Minecraft version" match uses version_prefix instead."""
    matches = list(Path(server_dir).glob("libraries/net/neoforged/neoforge/*/win_args.txt"))
    if not matches:
        return None
    if mc_version:
        try:
            prefix = version_prefix(mc_version)
            exact = [m for m in matches if m.parent.name.startswith(prefix)]
            if exact:
                matches = exact
        except ValueError:
            pass
    return max(matches, key=lambda m: m.stat().st_mtime)
