"""Seam 1 smoke test: runs a real headless Fabric server with the mod and checks it from outside.

Usage: python test/smoke.py <path to mcpersist-fabric-*.jar>

Needs Java for the target Minecraft version on PATH, or its path in $JAVA. Downloads the
Fabric server launcher and Fabric API into a temporary directory. Accepts the Minecraft
EULA for that throwaway server.
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

MINECRAFT_VERSION = "26.3"
FABRIC_META = "https://meta.fabricmc.net/v2/versions"


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "mcpersist-smoke-test"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def download(url, dest):
    request = urllib.request.Request(url, headers={"User-Agent": "mcpersist-smoke-test"})
    with urllib.request.urlopen(request) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)


def prepare_server(root, mod_jar):
    loader = get_json(f"{FABRIC_META}/loader/{MINECRAFT_VERSION}")[0]["loader"]["version"]
    installer = get_json(f"{FABRIC_META}/installer")[0]["version"]
    download(f"{FABRIC_META}/loader/{MINECRAFT_VERSION}/{loader}/{installer}/server/jar", root / "server.jar")

    mods = root / "mods"
    mods.mkdir()
    query = urllib.parse.urlencode({"game_versions": f'["{MINECRAFT_VERSION}"]', "loaders": '["fabric"]'})
    versions = get_json(f"https://api.modrinth.com/v2/project/fabric-api/version?{query}")
    download(versions[0]["files"][0]["url"], mods / "fabric-api.jar")
    shutil.copy(mod_jar, mods)

    (root / "eula.txt").write_text("eula=true\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    (root / "server.properties").write_text(f"online-mode=false\nserver-port={port}\nlevel-type=minecraft:flat\n")
    # Keep CI off the relay; this test is about the server, not the tunnel.
    (root / "config" / "mcpersist").mkdir(parents=True)
    (root / "config" / "mcpersist" / "mcpersist.toml").write_text("hostEnabled = false\n")


class Server:
    def __init__(self, root):
        self.log = []
        self.lines = queue.Queue()
        self.process = subprocess.Popen(
            [os.environ.get("JAVA", "java"), "-Xmx1G", "-jar", "server.jar", "nogui"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            self.log.append(line)
            self.lines.put(line)

    def wait_for(self, text, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=1)
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if text in line:
                return line
        fail(f"never saw {text!r}", self)

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()


def fail(message, server=None):
    if server:
        if server.process.poll() is None:
            server.process.kill()
    sys.exit(f"FAIL: {message}")


def main():
    mod_jar = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prepare_server(root, mod_jar)
        server = Server(root)
        server.wait_for("Done (", timeout=300)

        # Listing commands evaluates every command's permission check, including
        # /e4mc's; before the fix this crashed the tick loop on 1.21.11+ (e4mc#228).
        server.command("help")
        server.wait_for("/e4mc", timeout=30)

        server.command("stop")
        try:
            code = server.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            fail("server did not stop", server)
        output = "".join(server.log)
        if "broker req" in output:
            fail("mod contacted the relay broker despite hostEnabled = false", server)
        for crash in ("Exception in server tick loop", "NoSuchMethodError", "This crash report"):
            if crash in output:
                fail(f"server log contains {crash!r}", server)
        if code != 0:
            fail(f"server exited with code {code}", server)
    print("PASS: server ran the mod, listed /e4mc for the console, and stopped cleanly")


if __name__ == "__main__":
    main()
