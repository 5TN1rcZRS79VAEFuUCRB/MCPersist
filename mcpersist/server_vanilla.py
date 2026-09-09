"""Downloads the vanilla server jar matching a Minecraft version, via Mojang's public
version manifest."""

import requests

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def get_server_jar_url(mc_version):
    manifest = requests.get(VERSION_MANIFEST_URL, timeout=30).json()
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise ValueError(f"Minecraft version {mc_version!r} not found in Mojang's manifest")
    version_meta = requests.get(entry["url"], timeout=30).json()
    server = version_meta.get("downloads", {}).get("server")
    if server is None:
        raise ValueError(f"Minecraft version {mc_version!r} has no server download (too old?)")
    return server["url"]


def download_server_jar(mc_version, dest_path):
    url = get_server_jar_url(mc_version)
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest_path
