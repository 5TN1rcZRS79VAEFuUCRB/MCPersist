"""Downloads the vanilla server jar matching a Minecraft version, via Mojang's public
version manifest."""

import hashlib
from pathlib import Path

import requests

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

# A single setup can hit this manifest 2-3+ times independently (the version
# dropdown, required_java_major, get_server_jar_url each fetch it separately) -
# it's a few hundred KB and doesn't change within one run of the app, so caching it
# in-process avoids re-downloading the same thing repeatedly for no benefit.
_manifest_cache = None


def _get_manifest():
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = requests.get(VERSION_MANIFEST_URL, timeout=30).json()
    return _manifest_cache


def list_release_versions():
    """Release-type Minecraft versions from Mojang's manifest, newest first - powers
    the setup wizard's version dropdown. Excludes snapshots/betas/alphas, which
    aren't a good fit for what's meant to be a persistent server. Returns [] (not an
    exception) on any failure - callers fall back to whatever version was already
    detected, if any, so a network hiccup doesn't block setup entirely."""
    try:
        manifest = _get_manifest()
        return [v["id"] for v in manifest["versions"] if v.get("type") == "release"]
    except Exception:
        return []


def get_version_meta(mc_version):
    manifest = _get_manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise ValueError(f"Minecraft version {mc_version!r} not found in Mojang's manifest")
    return requests.get(entry["url"], timeout=30).json()


def _get_server_download(mc_version):
    version_meta = get_version_meta(mc_version)
    server = version_meta.get("downloads", {}).get("server")
    if server is None:
        raise ValueError(f"Minecraft version {mc_version!r} has no server download (too old?)")
    return server


def get_server_jar_url(mc_version):
    return _get_server_download(mc_version)["url"]


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
    server = _get_server_download(mc_version)
    resp = requests.get(server["url"], stream=True, timeout=60)
    resp.raise_for_status()
    hasher = hashlib.sha1()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            hasher.update(chunk)

    # Mojang's manifest publishes a sha1 for every download - this is a jar that's
    # about to be executed as a server process, so it's worth actually checking
    # instead of trusting a plain HTTPS GET not to have been corrupted or tampered
    # with in transit (a compromised/misbehaving CDN edge, not just a threat from
    # Mojang's own infrastructure).
    expected_sha1 = server.get("sha1")
    if expected_sha1 and hasher.hexdigest() != expected_sha1:
        Path(dest_path).unlink(missing_ok=True)
        raise ValueError(
            f"downloaded server jar for {mc_version} failed integrity check "
            f"(expected sha1 {expected_sha1}, got {hasher.hexdigest()}) - not using it"
        )
    return dest_path
