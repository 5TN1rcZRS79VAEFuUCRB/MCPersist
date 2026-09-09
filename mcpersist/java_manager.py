"""Auto-downloads a matching Eclipse Temurin JRE from Adoptium's public API when the
Java version a world needs isn't already available - similar to how launchers like
Prism manage their own bundled Java installs, so users don't have to go hunt down and
install the right JDK themselves."""

import shutil
import zipfile

import requests

from . import javacheck
from .paths import BIN_DIR

DOWNLOAD_URL = "https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jre/hotspot/normal/eclipse"


def portable_java_exe(major):
    return BIN_DIR / f"java{major}" / "bin" / "java.exe"


def download_java(major):
    """Downloads and extracts a Temurin JRE for the given major version into
    bin/java<major>/, returning the java.exe path. Raises on failure - callers turn
    that into a user-facing message rather than launching a doomed server."""
    dest_dir = portable_java_exe(major).parent.parent
    tmp_zip = BIN_DIR / f"java{major}_download.zip"
    extract_dir = BIN_DIR / f"java{major}_extract_tmp"

    resp = requests.get(DOWNLOAD_URL.format(major=major), stream=True, timeout=120)
    resp.raise_for_status()
    with open(tmp_zip, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(tmp_zip) as zf:
        zf.extractall(extract_dir)
    tmp_zip.unlink()

    # The zip contains one top-level folder (e.g. "jdk-21.0.5+11-jre") whose exact
    # name changes with every patch release - move its contents to a stable,
    # version-agnostic path instead of depending on that name.
    inner = next(p for p in extract_dir.iterdir() if p.is_dir())
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    inner.rename(dest_dir)
    shutil.rmtree(extract_dir, ignore_errors=True)

    java_exe = dest_dir / "bin" / "java.exe"
    if not java_exe.exists():
        raise RuntimeError(f"downloaded Java {major}, but {java_exe} is missing (unexpected archive layout)")
    return java_exe


def ensure_java(required_major):
    """Returns a working java.exe path for the given major version - preferring
    whatever's already available (a previously downloaded portable JRE, or a matching
    system install already on PATH) over downloading a new one."""
    portable = portable_java_exe(required_major)
    if portable.exists():
        return str(portable)

    system_java = javacheck.find_java("java")
    if system_java and javacheck.detected_major_version("java") == required_major:
        return system_java

    return str(download_java(required_major))
