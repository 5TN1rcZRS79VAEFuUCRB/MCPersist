"""HTTP GETs for the app's few network calls (version manifests, jar and Java
downloads, update checks) - plain urllib, since nothing here needs more."""

import http.client
import json
import os
import urllib.request
import zipfile
from pathlib import Path

from .version import VERSION

# urlopen raises these for HTTP error statuses, timeouts, dropped connections and
# truncated responses.
ERRORS = (OSError, http.client.HTTPException)


def _open(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": f"MCPersist/{VERSION}"})
    return urllib.request.urlopen(request, timeout=timeout)


def get_json(url, timeout=30):
    with _open(url, timeout) as resp:
        return json.load(resp)


def download_to_part(url, dest_path, hasher=None):
    """Streams url into <dest>.part and returns that path; the caller verifies it and
    then os.replace()s it over dest, so a dropped connection or failed check never
    touches a working file already at dest. Removes the .part on failure."""
    part = Path(str(dest_path) + ".part")
    try:
        with _open(url, 60) as resp, open(part, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return part


def download_jar(url, dest_path, what):
    """Downloads a jar about to be run with java whose source publishes no hash.
    Checking it's a well-formed zip still catches a truncated transfer or an error
    page served with a 200."""
    part = download_to_part(url, dest_path)
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise ValueError(f"downloaded {what} isn't a valid jar - not using it")
    os.replace(part, dest_path)
    return dest_path
