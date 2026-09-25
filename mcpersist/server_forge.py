"""Sets up a Forge server. Forge publishes no server jar - its installer has to be run
(java -jar ... --installServer). Scoped to modern Forge (1.17+), which launches via
an @args-file."""

import subprocess
from pathlib import Path

from . import net

PROMOTIONS_URL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
MAVEN_BASE = "https://maven.minecraftforge.net/net/minecraftforge/forge"


def get_recommended_forge_version(mc_version):
    promos = net.get_json(PROMOTIONS_URL).get("promos", {})
    forge_version = promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest")
    if not forge_version:
        raise ValueError(f"No Forge build available for Minecraft {mc_version!r}")
    return forge_version


def download_installer(mc_version, forge_version, dest_path):
    url = f"{MAVEN_BASE}/{mc_version}-{forge_version}/forge-{mc_version}-{forge_version}-installer.jar"
    return net.download_jar(url, dest_path, f"Forge installer for {mc_version}-{forge_version}")


def run_installer(java_path, installer_path, server_dir, name="Forge"):
    """Runs the Forge installer in --installServer mode in server_dir. CREATE_NO_WINDOW
    so java doesn't flash a console from the windowed GUI."""
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
        raise RuntimeError(f"{name} installer failed (exit {result.returncode}):\n{tail}")
    return result.stdout


def find_launch_args_file(server_dir, mc_version=None):
    """Modern Forge's launch args live in
    libraries/net/minecraftforge/forge/<mc>-<forge>/win_args.txt, passed to java as
    @file. Launching it directly (not Forge's run.bat) keeps -Xmx/-Xms ours.
    Reinstalling leaves older version directories behind, so prefer the one matching
    mc_version - picking another would silently run the wrong Minecraft version."""
    return find_args_file(server_dir, "minecraftforge/forge", f"{mc_version}-" if mc_version else None)


def find_args_file(server_dir, maven_path, dir_prefix):
    """Newest libraries/net/<maven_path>/*/win_args.txt, preferring directories named
    with dir_prefix (the wanted Minecraft version) when any exist."""
    matches = list(Path(server_dir).glob(f"libraries/net/{maven_path}/*/win_args.txt"))
    exact = [m for m in matches if dir_prefix and m.parent.name.startswith(dir_prefix)]
    return max(exact or matches, key=lambda m: m.stat().st_mtime, default=None)
