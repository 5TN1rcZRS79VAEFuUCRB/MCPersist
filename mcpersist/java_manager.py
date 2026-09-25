"""Auto-downloads a matching Eclipse Temurin JRE from Adoptium when the Java version a
world needs isn't already available, like launchers such as Prism do."""

import shutil
import zipfile

from . import javacheck, net
from .paths import BIN_DIR

DOWNLOAD_URL = "https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jre/hotspot/normal/eclipse"


def portable_java_exe(major):
    return BIN_DIR / f"java{major}" / "bin" / "java.exe"


def safe_extract(zf, dest_dir):
    """extractall(), refusing (before writing anything) any "zip slip" entry whose
    ../ path would land outside dest_dir. Defense-in-depth for downloaded archives."""
    dest_dir = dest_dir.resolve()
    for member in zf.infolist():
        target = (dest_dir / member.filename).resolve()
        if target != dest_dir and dest_dir not in target.parents:
            raise ValueError(f"refusing to extract {member.filename!r} - escapes the target directory")
    zf.extractall(dest_dir)


def download_java(major):
    """Downloads and extracts a Temurin JRE for the given major version into
    bin/java<major>/, returning the java.exe path. Raises on failure - callers turn
    that into a user-facing message rather than launching a doomed server."""
    dest_dir = portable_java_exe(major).parent.parent
    tmp_zip = BIN_DIR / f"java{major}_download.zip"
    extract_dir = BIN_DIR / f"java{major}_extract_tmp"

    tmp_zip = net.download_to_part(DOWNLOAD_URL.format(major=major), tmp_zip)
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(tmp_zip) as zf:
        safe_extract(zf, extract_dir)
    tmp_zip.unlink()

    # The zip's single top-level folder is named per patch release - move it to a stable
    # path.
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
    """A working java.exe for the given major version, preferring a previously
    downloaded JRE or a matching system Java over a new download."""
    portable = portable_java_exe(required_major)
    if portable.exists():
        return str(portable)

    system_java = shutil.which("java")
    if system_java and javacheck.detected_major_version("java") == required_major:
        return system_java

    return str(download_java(required_major))
