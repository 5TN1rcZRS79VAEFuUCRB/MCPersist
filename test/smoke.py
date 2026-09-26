"""Seam 1 smoke test: runs a real headless Fabric server with the mod and checks it from outside.

Usage: python test/smoke.py <path to mcpersist-fabric-*.jar>

Needs Java for the target Minecraft version on PATH, or its path in $JAVA. Downloads the
Fabric server launcher and Fabric API into a temporary directory. Accepts the Minecraft
EULA for these throwaway servers.

With $MCPERSIST_RELAY set to an mcpersist-relay binary, also checks world addresses against
a local relay (needs openssl and keytool on PATH).
"""

import atexit
import faulthandler
import json
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
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


GSON_JAR = "https://repo1.maven.org/maven2/com/google/code/gson/gson/2.11.0/gson-2.11.0.jar"


def download_server(cache, mod_jar):
    loader = get_json(f"{FABRIC_META}/loader/{MINECRAFT_VERSION}")[0]["loader"]["version"]
    (cache / "loader.txt").write_text(loader)
    download(GSON_JAR, cache / "gson.jar")
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
        if "using relay" in server.stop_cleanly():
            fail("mod contacted the relay despite hostEnabled = false")
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
    mc_port = free_port()
    env = dict(
        os.environ,
        QUICLIME_CERT_PATH=str(root / "cert.pem"),
        QUICLIME_KEY_PATH=str(root / "key.pem"),
        QUICLIME_BASE_DOMAIN=BASE_DOMAIN,
        QUICLIME_DB_PATH=str(root / "names.sqlite"),
        QUICLIME_BIND_ADDR_QUIC=f"127.0.0.1:{quic_port}",
        QUICLIME_BIND_ADDR_WEB=f"127.0.0.1:{free_port()}",
        QUICLIME_BIND_ADDR_MC=f"127.0.0.1:{mc_port}",
        RUST_LOG="info",
    )
    relay = subprocess.Popen([relay_bin], env=env)
    jvm_args = [f"-Djavax.net.ssl.trustStore={root / 'trust.p12'}", "-Djavax.net.ssl.trustStorePassword=changeit"]
    mod_config = (
        'relayHost = "localhost"\n'
        f"relayPort = {quic_port}\n"
        "dialtoneHostEnabled = false\n"
    )
    return relay, jvm_args, mod_config, mc_port


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
        relay, jvm_args, mod_config, _ = start_relay(relay_bin, root)
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


def varint(n):
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def packet(packet_id, body):
    data = varint(packet_id) + body
    return varint(len(data)) + data


def test_relayed_leave(cache, relay_bin):
    """A player who joins through the relay and leaves is dropped at once, not after the 30 s timeout."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        relay, jvm_args, mod_config, mc_port = start_relay(relay_bin, root)
        try:
            server_root = root / "server"
            server_root.mkdir()
            prepare_server(server_root, cache, mod_config)
            # The scripted player isn't whitelisted.
            with open(server_root / "server.properties", "a") as properties:
                properties.write("white-list=false\n")
            server = Server(server_root, jvm_args)
            domain = server.wait_for("Domain assigned: ", timeout=300).split("Domain assigned: ", 1)[1].strip()
            if not any("Done (" in logged for logged in server.log):
                server.wait_for("Done (", timeout=300)
            host = domain.encode()
            with socket.create_connection(("127.0.0.1", mc_port), timeout=15) as player:
                # Handshake for 26.3 (protocol 777), then Login Start as an offline-mode player.
                player.sendall(packet(0, varint(777) + varint(len(host)) + host + struct.pack(">H", 25565) + varint(2)))
                player.sendall(packet(0, varint(6) + b"Leaver" + uuid.uuid4().bytes))
                if not player.recv(4096):
                    fail("the server never answered a login through the relay", server)
            left = time.monotonic()
            line = server.wait_for("lost connection", timeout=45)
            if "Leaver" not in line or "white-listed" in line or "Outdated" in line:
                fail(f"the server refused the scripted player: {line.strip()}", server)
            took = time.monotonic() - left
            server.stop_cleanly()
        finally:
            relay.kill()
    if took > 5:
        fail(f"a player who left through the relay stayed for {took:.0f} s: {line.strip()}")
    print(f"PASS: a player who left through the relay was dropped after {took:.1f} s")


def process_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_process(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        os.kill(pid, 15)
    deadline = time.monotonic() + 60
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(1)


def wait_in_file(path, text, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if text in line:
                    return line
        time.sleep(1)
    if path.exists():
        sys.stdout.write(path.read_text(encoding="utf-8", errors="replace")[-8000:])
    fail(f"never saw {text!r} in {path}")


HOST = ("11111111-2222-3333-4444-555555555555", "SmokeHost")
FRIEND = ("66666666-7777-8888-9999-000000000000", "SmokeFriend")
ADDED = ("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "SmokeAdded")
REMOVED = ("12121212-3434-5656-7878-909090909090", "SmokeRemoved")
NIL_UUID = "00000000-0000-0000-0000-000000000000"


# Autostart entries go under this home, not the real one.
HOME = Path(tempfile.mkdtemp(prefix="mcpersist-home-"))
atexit.register(shutil.rmtree, HOME, ignore_errors=True)


def handoff_jvm(cache):
    classpath = os.pathsep.join([str(cache / "mod.jar"), str(cache / "gson.jar")])
    return [os.environ.get("JAVA", "java"), f"-Duser.home={HOME}", "-cp", classpath, "link.e4mc.handoff.Handoff"]


def handoff_env():
    return dict(os.environ, XDG_CONFIG_HOME=str(HOME / ".config"))


def autostart_files():
    if os.name == "nt":
        return sorted((HOME / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup").glob("mcpersist-*.cmd"))
    if sys.platform == "darwin":
        return sorted((HOME / "Library/LaunchAgents").glob("link.mcpersist.*.plist"))
    units = HOME / ".config/systemd/user"
    return sorted(units.glob("mcpersist-*.service")) + sorted((units / "default.target.wants").glob("mcpersist-*.service"))


def handoff_action(cache, world, servers, action):
    world_args = ["--world", str(world)] if world else []
    return subprocess.run(
        [*handoff_jvm(cache), "--action", action, *world_args, "--servers", str(servers)],
        capture_output=True, text=True, timeout=180, env=handoff_env(),
    ).stdout.strip()


def hand_off(cache, game, world, servers):
    """Runs the handoff entry point as the mod would, without a game window."""
    return subprocess.run(
        [*handoff_jvm(cache),
         "--world", str(world), "--mods", str(game / "mods"), "--config", str(game / "config"),
         "--servers", str(servers), "--minecraft", MINECRAFT_VERSION,
         "--loader", (cache / "loader.txt").read_text(), "--xmx", "768M",
         "--host", f"{HOST[0]}:{HOST[1]}", "--players", f"{FRIEND[0]}:{FRIEND[1]},{REMOVED[0]}:{REMOVED[1]}",
         "--whitelist", str(game / "whitelist.json")],
        capture_output=True, text=True, timeout=300, env=handoff_env(),
    )


def test_handoff(cache, relay_bin):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        relay = None
        if relay_bin:
            relay, jvm_args, mod_config, _ = start_relay(relay_bin, root)
            # The handoff runs the server with the game's JVM settings, not the test's, so
            # trust the relay's certificate through the JVM's environment instead.
            os.environ["JAVA_TOOL_OPTIONS"] = " ".join(jvm_args)
        else:
            mod_config = "hostEnabled = false\n"
        try:
            game = root / "game"
            world = game / "saves" / "Handoff World"
            world.mkdir(parents=True)
            (world / "mcpersist.properties").write_text("persistent=true\nkey=smoke-test-handoff-key-0123456789\n")
            # A migrated world that stored the host under the all-zeros UUID.
            (world / "players" / "data").mkdir(parents=True)
            (world / "players" / "data" / f"{NIL_UUID}.dat").write_bytes(b"host inventory")
            (game / "mods").mkdir()
            shutil.copy(cache / "mod.jar", game / "mods" / "mcpersist.jar")
            shutil.copy(cache / "fabric-api.jar", game / "mods")
            with zipfile.ZipFile(game / "mods" / "client-only.jar", "w") as jar:
                jar.writestr("fabric.mod.json", json.dumps(
                    {"schemaVersion": 1, "id": "clientonly", "version": "1", "environment": "client"}))
            (game / "config" / "mcpersist").mkdir(parents=True)
            (game / "config" / "mcpersist" / "mcpersist.toml").write_text(mod_config)
            servers = game / "mcpersist" / "servers"
            # The host whitelisted someone who hasn't joined yet, and removed someone who joined this
            # session and whom the background server still lists from an earlier handoff.
            (game / "whitelist.json").write_text(json.dumps(
                [{"uuid": FRIEND[0], "name": FRIEND[1]}, {"uuid": ADDED[0], "name": ADDED[1]}]))
            (servers / world.name).mkdir(parents=True)
            (servers / world.name / "whitelist.json").write_text(json.dumps([{"uuid": REMOVED[0], "name": REMOVED[1]}]))

            domains = []
            for attempt in range(2 if relay else 1):
                result = hand_off(cache, game, world, servers)
                if result.returncode != 0:
                    fail(f"handoff failed: {result.stderr}")
                server_dir = Path(result.stdout.strip().splitlines()[-1])
                host_data = world / "players" / "data" / f"{HOST[0]}.dat"
                if not host_data.exists() or host_data.read_bytes() != b"host inventory":
                    fail("host player data under the all-zeros UUID was not recovered")
                if server_dir != servers / world.name:
                    fail(f"unexpected server folder {server_dir}")
                pid = int((server_dir / "mcpersist.pid").read_text())
                console = server_dir / "logs" / "mcpersist-console.log"
                try:
                    wait_in_file(console, "Done (", timeout=300)
                    if not process_alive(pid):
                        fail("server did not outlive the handoff process")
                    if relay:
                        domains.append(wait_in_file(console, "Domain assigned: ", timeout=60).split("Domain assigned: ", 1)[1].strip())
                    if attempt == 0:
                        check_server_folder(server_dir, world)
                        second = hand_off(cache, game, world, servers)
                        if second.returncode == 0 or "open in another game or server" not in second.stderr:
                            fail(f"second handoff while the server runs was not refused: {second.stderr}")
                    if handoff_action(cache, world, servers, "status") != "running":
                        fail("status doesn't report the running server")
                    stopped = handoff_action(cache, world, servers, "stop")
                    if stopped != "stopped":
                        sys.stdout.write(console.read_text(encoding="utf-8", errors="replace")[-6000:])
                        fail(f"server wasn't stopped by its own stop command: {stopped!r}")
                    if process_alive(pid):
                        fail("server still running after stop")
                    log = console.read_text(encoding="utf-8", errors="replace")
                    if "Stopping server" not in log or "ThreadedAnvilChunkStorage" not in log and "Saving worlds" not in log:
                        fail("server wasn't stopped cleanly (no save on shutdown)")
                    if handoff_action(cache, world, servers, "status") != "stopped":
                        fail("status still reports a stopped server as running")
                    if attempt == 0:
                        check_autostart(cache, world, servers, server_dir, console)
                finally:
                    if process_alive(pid):
                        stop_process(pid)
            if relay and domains[0] != domains[1]:
                fail(f"handed-off world changed address: {domains}")
        finally:
            os.environ.pop("JAVA_TOOL_OPTIONS", None)
            if relay:
                relay.kill()
    print("PASS: handoff ran the world in place in a detached server, whitelisted, host as op, stopped cleanly, autostarted"
          + (f" at {domains[0]} both times" if relay else ""))


def test_failed_start(cache):
    """A mod that crashes the background server: reported once, world untouched, no retry."""
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / "game"
        world = game / "saves" / "Crashing World"
        world.mkdir(parents=True)
        (world / "mcpersist.properties").write_text("persistent=true\nkey=smoke-test-crash-key-0123456789\n")
        (game / "mods").mkdir()
        shutil.copy(cache / "mod.jar", game / "mods" / "mcpersist.jar")
        shutil.copy(cache / "fabric-api.jar", game / "mods")
        with zipfile.ZipFile(game / "mods" / "crashes.jar", "w") as jar:
            jar.writestr("fabric.mod.json", json.dumps({
                "schemaVersion": 1, "id": "crashes", "version": "1", "environment": "*",
                "entrypoints": {"main": ["does.not.Exist"]}}))
        (game / "config" / "mcpersist").mkdir(parents=True)
        (game / "config" / "mcpersist" / "mcpersist.toml").write_text("hostEnabled = false\n")
        servers = game / "mcpersist" / "servers"
        world_files = sorted(p.name for p in world.iterdir())

        result = hand_off(cache, game, world, servers)
        if result.returncode != 0:
            fail(f"handoff failed: {result.stderr}")
        pid = int((servers / world.name / "mcpersist.pid").read_text())
        deadline = time.monotonic() + 300
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(1)
        if process_alive(pid):
            stop_process(pid)
            fail("the crashing server kept running")

        problems = handoff_action(cache, None, servers, "problems").splitlines()
        if len(problems) != 1 or not problems[0].startswith("failed\t") or "mcpersist-console.log" not in problems[0]:
            fail(f"failure not reported with its log: {problems}")
        if handoff_action(cache, None, servers, "problems"):
            fail("failure reported more than once")
        if sorted(p.name for p in world.iterdir()) != world_files:
            fail(f"failed start changed the world: {sorted(p.name for p in world.iterdir())}")
        if handoff_action(cache, world, servers, "status") != "stopped":
            fail("crashed server was restarted")
    print("PASS: a background server that failed to start was reported once, didn't touch the world, and wasn't retried")


def check_autostart(cache, world, servers, server_dir, console):
    """The login entry exists, starts the server like a login would, and is removed by disable."""
    entries = autostart_files()
    expected = 2 if os.name != "nt" and sys.platform != "darwin" else 1
    if len(entries) != expected:
        fail(f"expected {expected} autostart file(s), found {entries}")
    text = entries[0].read_text()
    if "link.e4mc.handoff.Launcher" not in text or str(server_dir.absolute()) not in text:
        fail(f"autostart entry doesn't run the launcher for {server_dir}: {text}")

    # What the entry runs at login.
    console.unlink()
    boot = subprocess.Popen(
        [os.environ.get("JAVA", "java"), "-cp", str(server_dir / "mcpersist-launcher.jar"),
         "link.e4mc.handoff.Launcher", str(server_dir.absolute())])
    try:
        wait_in_file(console, "Done (", timeout=300)
        if handoff_action(cache, world, servers, "status") != "running":
            fail("the autostart launcher didn't start the server")
        if handoff_action(cache, world, servers, "disable") != "stopped":
            fail("disable didn't stop the server with its own stop command")
        if autostart_files():
            fail(f"disable left autostart files behind: {autostart_files()}")
        boot.wait(timeout=60)
    finally:
        if boot.poll() is None:
            boot.kill()


def check_server_folder(server_dir, world):
    mods = sorted(p.name for p in (server_dir / "mods").glob("*.jar"))
    if "client-only.jar" in mods or "mcpersist.jar" not in mods or "fabric-api.jar" not in mods:
        fail(f"wrong server mods: {mods}")
    props = {}
    for line in (server_dir / "server.properties").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            props[key] = re.sub(r"\\(.)", r"\1", value)  # undo java.util.Properties escaping
    expected_level = str(world.absolute()).replace(os.sep, "/")
    expected = {"level-name": expected_level, "accepts-transfers": "true", "server-ip": "127.0.0.1",
                "white-list": "true", "enforce-whitelist": "true", "online-mode": "true"}
    if any(props.get(key) != value for key, value in expected.items()):
        fail(f"wrong server.properties: {props}")
    whitelist = {(e["uuid"], e["name"]) for e in json.loads((server_dir / "whitelist.json").read_text())}
    if whitelist != {HOST, FRIEND, ADDED}:
        fail(f"wrong whitelist: {whitelist}")
    ops = json.loads((server_dir / "ops.json").read_text())
    if [(e["uuid"], e["name"], e["level"]) for e in ops] != [(*HOST, 4)]:
        fail(f"wrong ops: {ops}")
    launch = (server_dir / "mcpersist-launch.txt").read_text().splitlines()
    if "-Xmx768M" not in launch:
        fail(f"server not launched with the game's -Xmx: {launch}")
    if not (world / "level.dat").exists():
        fail("the server did not run the world in place")


def main():
    # Windows consoles can't print everything a server logs (e.g. "μs"); a failed print
    # would kill the thread reading the server's output.
    sys.stdout.reconfigure(errors="replace")
    # If a run hangs, show where every thread is stuck.
    faulthandler.dump_traceback_later(900, repeat=True)
    mod_jar = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        download_server(cache, mod_jar)
        test_commands(cache)
        relay_bin = os.environ.get("MCPERSIST_RELAY")
        if relay_bin:
            test_stable_address(cache, relay_bin)
            test_relayed_leave(cache, relay_bin)
        else:
            print("SKIP: stable-address check (set MCPERSIST_RELAY to an mcpersist-relay binary)")
        test_handoff(cache, relay_bin)
        test_failed_start(cache)


if __name__ == "__main__":
    main()
