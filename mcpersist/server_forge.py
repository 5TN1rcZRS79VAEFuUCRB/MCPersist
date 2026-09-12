"""Sets up a Forge server. Unlike vanilla/Fabric, Forge doesn't publish a directly
downloadable server jar - the only supported path is downloading its installer and
actually running it (java -jar ... --installServer), which does the real work of
resolving libraries and writing out a runnable server. Scoped to modern Forge
(1.17+), which launches via an @args-file rather than a single -jar - the same
tier of Minecraft version this app already targets elsewhere (see
world.required_java_major)."""

import subprocess
import zipfile
from pathlib import Path

import requests

PROMOTIONS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
MAVEN_BASE = "https://maven.minecraftforge.net/net/minecraftforge/forge"


def get_recommended_forge_version(mc_version):
    resp = requests.get(PROMOTIONS_URL, timeout=30)
    resp.raise_for_status()
    promos = resp.json().get("promos", {})
    forge_version = promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest")
    if not forge_version:
        raise ValueError(f"No Forge build available for Minecraft {mc_version!r}")
    return forge_version


def download_installer(mc_version, forge_version, dest_path):
    url = f"{MAVEN_BASE}/{mc_version}-{forge_version}/forge-{mc_version}-{forge_version}-installer.jar"
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    # Forge's maven doesn't publish a hash to check against, but this installer is
    # about to be run with java -jar --installServer - same reasoning as the
    # vanilla sha1 check and the Fabric jar check above: confirm it's actually a
    # well-formed jar before running it, rather than trusting a plain HTTPS GET
    # not to have been corrupted/truncated/swapped for an error page in transit.
    if not zipfile.is_zipfile(dest_path):
        Path(dest_path).unlink(missing_ok=True)
        raise ValueError(
            f"downloaded Forge installer for {mc_version}-{forge_version} isn't a valid jar - not using it"
        )

    return dest_path


def run_installer(java_path, installer_path, server_dir):
    """Runs the Forge installer in --installServer mode in server_dir - this is
    Forge's own supported way to produce a server, not something to reimplement by
    hand. CREATE_NO_WINDOW for the same reason as every other java.exe/powershell.exe
    child this app launches: without it, a console-subsystem process spawned from a
    --windowed (console-less) GUI can flash/allocate its own visible console."""
    result = subprocess.run(
        [java_path, "-jar", str(installer_path), "--installServer"],
        cwd=str(server_dir),
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-20:])
        raise RuntimeError(f"Forge installer failed (exit {result.returncode}):\n{tail}")
    return result.stdout


def find_launch_args_file(server_dir, mc_version=None):
    """Modern Forge (1.17+) doesn't produce one runnable server jar the way vanilla/
    Fabric do - it generates its actual launch classpath/args as a file under
    libraries/net/minecraftforge/forge/<version>/win_args.txt, meant to be passed to
    java as @file. Its own run.bat just wraps this same file, plus its own default
    heap flags (which this app supplies itself instead, via -Xmx/-Xms), so launching
    against the args file directly - skipping run.bat - keeps memory sizing under
    this app's control rather than Forge's generated default.

    Installing Forge again (for a different Minecraft version, say) leaves the
    previous version's directory in place alongside the new one, so more than one
    args file can legitimately exist here. Picking an arbitrary match would then
    launch the WRONG Minecraft version against the world - and silently, since
    every args file is equally valid-looking. The directories are named
    <mc_version>-<forge_version>, so when the caller knows which Minecraft version
    it wants (it almost always does - it's in config.json) that prefix identifies
    the right one exactly; the mtime fallback just prefers the most recently
    installed one when there's nothing to match against."""
    matches = list(Path(server_dir).glob("libraries/net/minecraftforge/forge/*/win_args.txt"))
    if not matches:
        return None
    if mc_version:
        exact = [m for m in matches if m.parent.name.startswith(f"{mc_version}-")]
        if exact:
            matches = exact
    return max(matches, key=lambda m: m.stat().st_mtime)
