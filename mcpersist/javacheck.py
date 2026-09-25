"""Detects the installed Java version, so setup/start can warn if it doesn't match
what the Minecraft version needs."""

import re
import shutil
import subprocess


def parse_major(version_string):
    """The Java major version from a version string. The legacy form puts it second
    ("1.8.0_312" is Java 8); modern ones lead with it ("25.0.1")."""
    match = re.search(r"(\d+)(?:\.(\d+))?", version_string)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):
        return int(match.group(2))
    return major


def detected_major_version(java_path="java"):
    exe = shutil.which(java_path)
    if not exe:
        return None
    try:
        # CREATE_NO_WINDOW: from the windowed GUI, java.exe would otherwise open a
        # visible console.
        result = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    match = re.search(r'version "([^"]+)"', result.stdout + result.stderr)
    return parse_major(match.group(1)) if match else None
