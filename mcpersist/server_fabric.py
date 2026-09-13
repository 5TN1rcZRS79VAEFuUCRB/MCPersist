"""Downloads a Fabric server jar (via Fabric's meta API) and copies the instance's
client mods into the server's mods folder as a starting point."""

import os
import shutil
import zipfile
from pathlib import Path

import requests

from . import server_vanilla

META_BASE = "https://meta.fabricmc.net/v2/versions"


def get_recommended_loader_version(mc_version):
    resp = requests.get(f"{META_BASE}/loader/{mc_version}", timeout=30)
    resp.raise_for_status()
    entries = resp.json()
    if not entries:
        raise ValueError(f"No Fabric loader available for Minecraft {mc_version!r}")
    stable = next((e for e in entries if e["loader"]["stable"]), entries[0])
    return stable["loader"]["version"]


def get_recommended_installer_version():
    resp = requests.get(f"{META_BASE}/installer", timeout=30)
    resp.raise_for_status()
    entries = resp.json()
    stable = next((e for e in entries if e.get("stable")), entries[0])
    return stable["version"]


def download_server_jar(mc_version, dest_path, loader_version=None, installer_version=None):
    loader_version = loader_version or get_recommended_loader_version(mc_version)
    installer_version = installer_version or get_recommended_installer_version()
    url = f"{META_BASE}/loader/{mc_version}/{loader_version}/{installer_version}/server/jar"
    # Into a .part file first - see server_vanilla.download_to_part for why writing
    # over an existing server.jar directly isn't safe.
    part = server_vanilla.download_to_part(url, dest_path)

    # Fabric's meta API doesn't publish a hash to check against (unlike Mojang's
    # manifest, which server_vanilla.py verifies against), but this is still about
    # to be run as a server process - confirming it's actually a well-formed jar
    # catches a corrupted/truncated transfer or an unexpected non-jar response
    # (an error page served with a 200, say) instead of silently handing a bad
    # file to java.
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise ValueError(f"downloaded Fabric server jar for {mc_version} isn't a valid jar - not using it")
    os.replace(part, dest_path)

    return dest_path, loader_version, installer_version


# Mods known to be incompatible with running as a dedicated server, excluded rather
# than copied. e4mc specifically crashes the whole server tick loop the moment any
# player finishes joining (NoSuchMethodError in its command registration - it's built
# against different Minecraft mappings than what ships server-side), and anyone coming
# to MCPersist from e4mc is very likely to still have it in their client mods folder.
EXCLUDED_MOD_NAME_PARTS = ("e4mc",)


def copy_mods(instance_dir, server_dir):
    src = Path(instance_dir) / "mods"
    if not src.exists():
        return [], []
    dest = Path(server_dir) / "mods"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied, skipped = [], []
    for jar in sorted(src.glob("*.jar")):
        if any(part in jar.name.lower() for part in EXCLUDED_MOD_NAME_PARTS):
            skipped.append(jar.name)
            continue
        shutil.copy2(jar, dest / jar.name)
        copied.append(jar.name)
    return copied, skipped
