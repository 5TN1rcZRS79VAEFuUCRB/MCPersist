"""Seam 1 smoke test: runs a real headless Fabric server with the mod and checks it from outside.

Usage: python test/smoke.py <path to mcpersist-fabric-*.jar>

Needs Java for the target Minecraft version on PATH, or its path in $JAVA. Downloads the
Fabric server launcher and Fabric API into a temporary directory. Accepts the Minecraft
EULA for these throwaway servers.

With $MCPERSIST_RELAY set to an mcpersist-relay binary, also checks world addresses against
a local relay (needs openssl and keytool on PATH).
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


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def download_server(cache, mod_jar):
    loader = get_json(f"{FABRIC_META}/loader/{MINECRAFT_VERSION}")[0]["loader"]["version"]
    installer = get_json(f"{FABRIC_META}/installer")[0]["version"]
    download(f"{FABRIC_META}/loader/{MINECRAFT_VERSION}/{loader}/{installer}/server/jar", cache / "server.jar")
    query = urllib.parse.urlencode({"game_versions": f'["{MINECRAFT_VERSION}"]', "loaders": '["fabric"]'})
    versions = get_json(f"https://api.modrinth.com/v2/project/fabric-api/version?{query}")
    download(versions[0]["files"][0]["url"], cache / "fabric-api.jar")
    shutil.copy(mod_jar, cache / "mod.jar")


def prepare_server(root, cache, mod_config):
    shutil.copy(cache / "server.jar", root)
    (root / "mods").mkdir()
    shutil.copy(cache / "fabric-api.jar", root / "mods")
    shutil.copy(cache / "mod.jar", root / "mods")
    (root / "eula.txt").write_text("eula=true\n")
    (root / "server.properties").write_text(
        f"online-mode=false\nserver-port={free_port()}\nlevel-type=minecraft:flat\n"
    )
    (root / "config" / "mcpersist").mkdir(parents=True)
    (root / "config" / "mcpersist" / "mcpersist.toml").write_text(mod_config)


class Server:
    def __init__(self, root, jvm_args=()):
        self.log = []
        self.lines = queue.Queue()
        self.process = subprocess.Popen(
            [os.environ.get("JAVA", "java"), "-Xmx1G", *jvm_args, "-jar", "server.jar", "nogui"],
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

    def stop_cleanly(self):
        self.command("stop")
        try:
            code = self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            fail("server did not stop", self)
        output = "".join(self.log)
        for crash in ("Exception in server tick loop", "NoSuchMethodError", "This crash report"):
            if crash in output:
                fail(f"server log contains {crash!r}", self)
        if code != 0:
            fail(f"server exited with code {code}", self)
        return output


def fail(message, server=None):
    if server:
        if server.process.poll() is None:
            server.process.kill()
    sys.exit(f"FAIL: {message}")


def test_commands(cache):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Keep this scenario off any relay; it's about the server, not the tunnel.
        prepare_server(root, cache, "hostEnabled = false\n")
        server = Server(root)
        server.wait_for("Done (", timeout=300)
        # Listing commands evaluates every command's permission check, including
        # /e4mc's; before the fix this crashed the tick loop on 1.21.11+ (e4mc#228).
        server.command("help")
        server.wait_for("/e4mc", timeout=30)
        if "broker req" in server.stop_cleanly():
            fail("mod contacted the relay broker despite hostEnabled = false")
    print("PASS: server ran the mod, listed /e4mc for the console, and stopped cleanly")


BASE_DOMAIN = "relay.test"


def start_relay(relay_bin, root):
    """A local relay whose self-signed certificate the server's JVM is told to trust."""
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
         "-keyout", root / "key.pem", "-out", root / "cert.pem", "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
        check=True, capture_output=True,
    )
    # The JDK's own CAs plus the relay's certificate: the server still downloads over HTTPS.
    settings = subprocess.run(
        [os.environ.get("JAVA", "java"), "-XshowSettings:properties", "-version"],
        check=True, capture_output=True, text=True,
    ).stderr
    java_home = next(line.split("=", 1)[1].strip() for line in settings.splitlines() if "java.home =" in line)
    subprocess.run(
        ["keytool", "-importkeystore", "-noprompt", "-srckeystore", Path(java_home) / "lib" / "security" / "cacerts",
         "-srcstorepass", "changeit", "-destkeystore", root / "trust.p12", "-deststoretype", "PKCS12",
         "-deststorepass", "changeit"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["keytool", "-importcert", "-noprompt", "-alias", "relay", "-file", root / "cert.pem",
         "-keystore", root / "trust.p12", "-storetype", "PKCS12", "-storepass", "changeit"],
        check=True, capture_output=True,
    )
    quic_port = free_port()
    env = dict(
        os.environ,
        QUICLIME_CERT_PATH=str(root / "cert.pem"),
        QUICLIME_KEY_PATH=str(root / "key.pem"),
        QUICLIME_BASE_DOMAIN=BASE_DOMAIN,
        QUICLIME_DB_PATH=str(root / "names.sqlite"),
        QUICLIME_BIND_ADDR_QUIC=f"127.0.0.1:{quic_port}",
        QUICLIME_BIND_ADDR_WEB=f"127.0.0.1:{free_port()}",
        QUICLIME_BIND_ADDR_MC=f"127.0.0.1:{free_port()}",
        RUST_LOG="info",
    )
    relay = subprocess.Popen([relay_bin], env=env)
    jvm_args = [f"-Djavax.net.ssl.trustStore={root / 'trust.p12'}", "-Djavax.net.ssl.trustStorePassword=changeit"]
    mod_config = (
        "useBroker = false\n"
        'relayHost = "localhost"\n'
        f"relayPort = {quic_port}\n"
        "dialtoneHostEnabled = false\n"
    )
    return relay, jvm_args, mod_config


def run_for_domain(root, jvm_args):
    server = Server(root, jvm_args)
    # The address can arrive before or after the server finishes starting.
    line = server.wait_for("Domain assigned: ", timeout=300)
    if not any("Done (" in logged for logged in server.log):
        server.wait_for("Done (", timeout=300)
    server.stop_cleanly()
    return line.split("Domain assigned: ", 1)[1].strip()


def test_stable_address(cache, relay_bin):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        relay, jvm_args, mod_config = start_relay(relay_bin, root)
        try:
            server_root = root / "server"
            server_root.mkdir()
            prepare_server(server_root, cache, mod_config)
            settings = server_root / "world" / "mcpersist.properties"
            settings.parent.mkdir()
            settings.write_text("persistent=true\nkey=smoke-test-world-key-0123456789\n")

            first = run_for_domain(server_root, jvm_args)
            second = run_for_domain(server_root, jvm_args)
            if not first.endswith("." + BASE_DOMAIN):
                fail(f"unexpected domain {first!r}")
            if first != second:
                fail(f"persistent world changed address: {first!r} then {second!r}")

            settings.write_text("persistent=false\nkey=smoke-test-world-key-0123456789\n")
            third = run_for_domain(server_root, jvm_args)
            if third == first:
                fail("a world with persistence off kept its permanent address")
        finally:
            relay.kill()
    print(f"PASS: persistent world kept {first} across restarts; with persistence off it got {third}")


def main():
    mod_jar = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        download_server(cache, mod_jar)
        test_commands(cache)
        relay_bin = os.environ.get("MCPERSIST_RELAY")
        if relay_bin:
            test_stable_address(cache, relay_bin)
        else:
            print("SKIP: stable-address check (set MCPERSIST_RELAY to an mcpersist-relay binary)")


if __name__ == "__main__":
    main()
