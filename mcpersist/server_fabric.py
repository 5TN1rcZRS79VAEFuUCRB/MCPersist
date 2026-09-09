"""Downloads a Fabric server jar (via Fabric's meta API) and copies the instance's
client mods into the server's mods folder as a starting point."""

import shutil
from pathlib import Path

import requests

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
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest_path, loader_version, installer_version


def copy_mods(instance_dir, server_dir):
    src = Path(instance_dir) / "mods"
    if not src.exists():
        return []
    dest = Path(server_dir) / "mods"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return sorted(p.name for p in dest.glob("*.jar"))
