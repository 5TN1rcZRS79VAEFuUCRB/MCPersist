"""Downloads the vanilla server jar matching a Minecraft version, via Mojang's public
version manifest."""

import functools
import hashlib
import os

from . import net

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

# Cached per run: one setup fetches the manifest and a version's own manifest
# several times over (version dropdown, required Java, the jar download).
@functools.cache
def _get_manifest():
    return net.get_json(VERSION_MANIFEST_URL)


def list_release_versions():
    """Release versions from Mojang's manifest, newest first, for the version picker
    (snapshots don't suit a persistent server). Returns [] on any failure, so a
    network hiccup doesn't block setup."""
    try:
        manifest = _get_manifest()
        return [v["id"] for v in manifest["versions"] if v.get("type") == "release"]
    except Exception:
        return []


@functools.cache
def get_version_meta(mc_version):
    manifest = _get_manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise ValueError(f"Minecraft version {mc_version!r} not found in Mojang's manifest")
    return net.get_json(entry["url"])


def _get_server_download(mc_version):
    version_meta = get_version_meta(mc_version)
    server = version_meta.get("downloads", {}).get("server")
    if server is None:
        raise ValueError(f"Minecraft version {mc_version!r} has no server download (too old?)")
    return server


def required_java_major(mc_version):
    """The required Java version from Mojang's per-version manifest - authoritative,
    unlike world.py's fallback table, which goes stale with each new requirement.
    None if unavailable."""
    try:
        return get_version_meta(mc_version).get("javaVersion", {}).get("majorVersion")
    except Exception:
        return None


def download_server_jar(mc_version, dest_path):
    server = _get_server_download(mc_version)
    hasher = hashlib.sha1()
    part = net.download_to_part(server["url"], dest_path, hasher)

    # Mojang publishes a sha1 for every download; this jar is about to be executed, so
    # check it.
    expected_sha1 = server.get("sha1")
    if expected_sha1 and hasher.hexdigest() != expected_sha1:
        part.unlink(missing_ok=True)
        raise ValueError(
            f"downloaded server jar for {mc_version} failed integrity check "
            f"(expected sha1 {expected_sha1}, got {hasher.hexdigest()}) - not using it"
        )
    os.replace(part, dest_path)
    return dest_path
