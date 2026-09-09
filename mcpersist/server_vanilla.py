"""Downloads the vanilla server jar matching a Minecraft version, via Mojang's public
version manifest."""

import requests

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def list_release_versions():
    """Release-type Minecraft versions from Mojang's manifest, newest first - powers
    the setup wizard's version dropdown. Excludes snapshots/betas/alphas, which
    aren't a good fit for what's meant to be a persistent server. Returns [] (not an
    exception) on any failure - callers fall back to whatever version was already
    detected, if any, so a network hiccup doesn't block setup entirely."""
    try:
        manifest = requests.get(VERSION_MANIFEST_URL, timeout=30).json()
        return [v["id"] for v in manifest["versions"] if v.get("type") == "release"]
    except Exception:
        return []


def get_version_meta(mc_version):
    manifest = requests.get(VERSION_MANIFEST_URL, timeout=30).json()
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise ValueError(f"Minecraft version {mc_version!r} not found in Mojang's manifest")
    return requests.get(entry["url"], timeout=30).json()


def get_server_jar_url(mc_version):
    version_meta = get_version_meta(mc_version)
    server = version_meta.get("downloads", {}).get("server")
    if server is None:
        raise ValueError(f"Minecraft version {mc_version!r} has no server download (too old?)")
    return server["url"]


def required_java_major(mc_version):
    """The authoritative required Java version, straight from Mojang's own per-version
    manifest - used instead of guessing from a hardcoded version table (world.py's
    fallback), which goes stale every time a new Minecraft version bumps the
    requirement (it did: 26.2 needs Java 25, not the 21 a fixed table assumed).
    Returns None if unavailable (very old versions, network issues)."""
    try:
        return get_version_meta(mc_version).get("javaVersion", {}).get("majorVersion")
    except Exception:
        return None


def download_server_jar(mc_version, dest_path):
    url = get_server_jar_url(mc_version)
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest_path
