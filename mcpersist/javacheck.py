"""Detects the installed Java version, so setup/start can warn if it doesn't match
what the Minecraft version needs."""

import re
import shutil
import subprocess


def find_java(java_path="java"):
    return shutil.which(java_path)


def detected_major_version(java_path="java"):
    exe = find_java(java_path)
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    output = result.stdout + result.stderr
    match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):
        return int(match.group(2))
    return major
