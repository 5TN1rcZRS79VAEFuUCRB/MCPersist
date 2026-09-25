"""The relay: an asyncio TCP server that lets many people's MCPersist servers share
one public IP/port, routing each incoming Minecraft connection to the right one by
the subdomain the player typed, then piping bytes between them. See README.md for
the wire protocol and deployment steps."""

import argparse
import asyncio
import json
import random
import ssl
import time
import uuid
from collections import Counter, deque
from pathlib import Path

from mc_handshake import read_handshake
from users import load_users, verify
from auto_assignments import load_assignments, save_assignments, write_json_atomic
from auto_assignments import locked as auto_assignments_locked

CONNECT_TIMEOUT = 10
HANDSHAKE_TIMEOUT = 10  # a client that never finishes its handshake shouldn't hold a connection open forever
MAX_AUTO_CONNECTIONS_PER_IP = 3

# Two caps: MAX_CONNECTIONS_PER_IP stops one source sitting on lots of raw sockets
# (generous, so several players behind one NAT never hit it). MAX_PENDING_PER_SUBDOMAIN
# stops one backend being flooded with fake connects spread across many IPs - it counts
# only the brief negotiation, never established players.
MAX_CONNECTIONS_PER_IP = 20
MAX_PENDING_PER_SUBDOMAIN = 10

# A third: rapid connect/disconnect cycling never builds up concurrent sockets, so
# attempts are also counted over a rolling window (e4mc's relay allows 30/minute too).
CONN_RATE_WINDOW = 60
CONN_RATE_LIMIT = 30

# conn_attempts is otherwise only pruned when that IP tries again, so IPs that stop
# (scanners) would stay forever.
CLEANUP_INTERVAL = 300

# How often the status snapshot for admin_cli.py's `status` is refreshed.
STATUS_WRITE_INTERVAL = 10
STATUS_PATH = Path(__file__).resolve().parent / "relay_status.json"

# A tunnel client that dies without a FIN looks healthy to TCP indefinitely, holding its
# subdomain so its own reconnects are refused forever. Our own ping, with a reply
# required within PING_TIMEOUT, catches that.
PING_INTERVAL = 20
PING_TIMEOUT = 45

# Routine client-caused failures on the internet-facing control port (scanners, resets,
# dropped handshakes) - not logged, or they'd bury the lines that matter. Specific
# types, not OSError, so infrastructure errors like PermissionError stay loud. Not
# ValueError either: corrupt state files raise that too, and must be visible.
EXPECTED_CONTROL_ERRORS = (
    ConnectionError,  # covers reset / broken pipe / aborted - the peer just went away
    asyncio.TimeoutError,
    asyncio.IncompleteReadError,
    ssl.SSLError,  # a failed TLS handshake, i.e. anything that isn't a real client
)

# Established sessions have no other timeout, so a connection that goes silent forever
# would hold its sockets and a per-IP slot. Minecraft sends keepalives every second or
# so, so this never affects a real player.
PIPE_IDLE_TIMEOUT = 300

clients = {}  # subdomain -> StreamWriter of the control connection
pending = {}  # connection id -> asyncio.Future resolving to (data_reader, data_writer)
auto_conn_counts = Counter()  # source ip -> live auto-registered (unreserved) connections
conn_counts = Counter()  # source ip -> concurrent raw connections, across control+data+public
pending_by_subdomain = Counter()  # subdomain -> in-flight (not yet established) connect attempts
conn_attempts = {}  # source ip -> deque of monotonic timestamps of recent connection attempts

# Set in main(). handle_control/handle_data upgrade to TLS themselves, instead of
# start_server(ssl=...), so the rate limit and connection cap apply to every raw
# connection, not just ones that complete a handshake.
tls_context = None


def _acquire_conn_slot(peer_ip):
    """Called at the top of every handler, on all three ports: False (close it) once one
    source IP holds too many open sockets."""
    if peer_ip is None:
        return True  # can't identify the source (unusual) - let it through rather than break on it
    if conn_counts[peer_ip] >= MAX_CONNECTIONS_PER_IP:
        return False
    conn_counts[peer_ip] += 1
    return True


def _release_conn_slot(peer_ip):
    if peer_ip is not None:
        _decrement(conn_counts, peer_ip)


def _decrement(counter, key):
    """Counts back down, dropping keys that reach zero so the dicts don't grow forever."""
    counter[key] -= 1
    if counter[key] <= 0:
        del counter[key]


def _admit(writer):
    """Every handler's first step, on all three ports: returns (peer_ip, admitted).
    A connection over the rate limit or the per-IP cap is closed and not admitted."""
    peer_ip = (writer.get_extra_info("peername") or (None,))[0]
    if _rate_limited(peer_ip) or not _acquire_conn_slot(peer_ip):
        writer.close()
        return peer_ip, False
    return peer_ip, True


async def _upgrade_to_tls(writer):
    """Called after the rate-limit/connection-cap checks (see tls_context). True on
    success; otherwise closes the writer and returns False. Bounded by
    HANDSHAKE_TIMEOUT: silent connections would otherwise hang here forever and
    permanently use up that IP's connection slots."""
    try:
        await asyncio.wait_for(writer.start_tls(tls_context), timeout=HANDSHAKE_TIMEOUT)
        return True
    except Exception:
        writer.close()
        return False


def _rate_limited(peer_ip):
    """True if this source IP has made too many attempts within CONN_RATE_WINDOW. Called
    once per attempt; attempts simply age out."""
    if peer_ip is None:
        return False
    now = time.monotonic()
    cutoff = now - CONN_RATE_WINDOW
    attempts = conn_attempts.setdefault(peer_ip, deque())
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    if len(attempts) >= CONN_RATE_LIMIT:
        return True
    attempts.append(now)
    return False


ADJECTIVES = [
    "quiet", "brave", "lucky", "sunny", "cozy", "swift", "calm", "bold", "gentle", "merry",
    "clever", "eager", "fuzzy", "jolly", "mighty", "nimble", "plucky", "spry", "witty", "zesty",
]
NOUNS = [
    "badger", "otter", "falcon", "maple", "comet", "ember", "willow", "raven", "meadow", "pebble",
    "harbor", "lantern", "thistle", "sparrow", "granite", "cedar", "brook", "canyon", "orchid", "juniper",
]


def generate_unique_subdomain(taken):
    """adjective-noun, with a numeric suffix once the 400 plain names start running
    out (auto_assignments.json is never pruned, so a busy relay can get there)."""
    for attempt in range(1000):
        candidate = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
        if attempt >= 50:
            candidate += f"-{random.randint(100, 999)}"
        if candidate not in taken:
            return candidate
    return uuid.uuid4().hex[:12]


def get_or_assign_subdomain(peer_ip):
    """Auto (unreserved) clients get a subdomain tied to their source IP, permanently
    (until cleared from auto_assignments.json). People sharing an IP via CGNAT share
    it too - the accepted tradeoff over per-connection randomness."""
    # Held for the whole load-decide-save cycle (see auto_assignments.locked).
    with auto_assignments_locked():
        assignments = load_assignments()
        existing = assignments.get(peer_ip)
        if existing:
            return existing
        taken = set(clients.keys()) | set(load_users().keys()) | set(assignments.values())
        subdomain = generate_unique_subdomain(taken)
        assignments[peer_ip] = subdomain
        save_assignments(assignments)
        return subdomain


async def send_json(writer, obj):
    # drain() can stall forever on a peer that stops reading, holding its connection
    # slot open - so bound it.
    writer.write((json.dumps(obj) + "\n").encode("utf-8"))
    await asyncio.wait_for(writer.drain(), timeout=HANDSHAKE_TIMEOUT)


async def _ping_loop(writer, subdomain, last_seen):
    """Runs alongside handle_control's read loop: pings the client, and closes the
    connection if nothing has been heard within PING_TIMEOUT. Closing unblocks the
    handler's readline(), so its normal cleanup runs."""
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if time.monotonic() - last_seen[0] > PING_TIMEOUT:
                print(f"[control] {subdomain} timed out (no response to ping) - evicting stale connection", flush=True)
                writer.close()
                return
            try:
                await send_json(writer, {"type": "ping"})
            except Exception:
                # A failed ping write closes too - otherwise nothing would ever notice
                # this connection going dark.
                writer.close()
                return
    except asyncio.CancelledError:
        pass


async def handle_control(reader, writer):
    subdomain = None
    is_auto = False
    auto_counted = False
    ping_task = None
    peer_ip, admitted = _admit(writer)
    if not admitted:
        return
    try:
        if not await _upgrade_to_tls(writer):
            return
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
            if not line:
                return
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            # Bad JSON, bad UTF-8 and over-long lines are all ValueError. Caught
            # narrowly here, since corrupt state files raise the same types and must
            # still be logged below.
            writer.close()
            return
        # Valid JSON isn't necessarily an object ("[]", "1"), and .get() on one would
        # log a line per attempt.
        if not isinstance(msg, dict) or msg.get("type") != "register":
            writer.close()
            return

        requested_subdomain = msg.get("subdomain")
        token = msg.get("token")

        if requested_subdomain and token:
            if not verify(requested_subdomain, token):
                await send_json(writer, {"type": "error", "message": "invalid subdomain or token"})
                writer.close()
                return
            if requested_subdomain in clients:
                await send_json(writer, {"type": "error", "message": "subdomain already connected elsewhere"})
                writer.close()
                return
            subdomain = requested_subdomain
        else:
            is_auto = True
            if not peer_ip:
                await send_json(writer, {"type": "error", "message": "couldn't determine your address"})
                writer.close()
                return
            if auto_conn_counts[peer_ip] >= MAX_AUTO_CONNECTIONS_PER_IP:
                await send_json(writer, {"type": "error", "message": "too many connections from this address"})
                writer.close()
                return
            subdomain = get_or_assign_subdomain(peer_ip)
            if subdomain in clients:
                await send_json(writer, {"type": "error", "message": "already connected from this address"})
                writer.close()
                return
            auto_conn_counts[peer_ip] += 1
            auto_counted = True

        clients[subdomain] = writer
        await send_json(writer, {"type": "registered", "subdomain": subdomain})
        print(f"[control] {subdomain} registered{f' (auto, ip={peer_ip})' if is_auto else ''}", flush=True)

        last_seen = [time.monotonic()]
        ping_task = asyncio.create_task(_ping_loop(writer, subdomain, last_seen))

        while True:
            data = await reader.readline()
            if not data:
                break
            last_seen[0] = time.monotonic()
    except Exception as e:
        # Broad on purpose: this port is open to the internet, so it has to survive any
        # malformed input. Anything not in EXPECTED_CONTROL_ERRORS is logged - a
        # wrong-owned lock file once failed every auto registration with no trace at
        # all.
        if not isinstance(e, EXPECTED_CONTROL_ERRORS):
            print(f"[control] {peer_ip}: unexpected {type(e).__name__}: {e}", flush=True)
    finally:
        if ping_task:
            ping_task.cancel()
        if subdomain and clients.get(subdomain) is writer:
            del clients[subdomain]
            print(f"[control] {subdomain} disconnected", flush=True)
        if auto_counted:
            # Only if this connection incremented it: is_auto is also set on attempts
            # rejected before counting, and decrementing for those would undo a real
            # connection's count.
            _decrement(auto_conn_counts, peer_ip)
        _release_conn_slot(peer_ip)
        writer.close()


async def handle_data(reader, writer):
    # The slot only covers this connection's brief registration handshake; once
    # data_hello matches, the reader/writer belong to the waiting handle_public call.
    peer_ip, admitted = _admit(writer)
    if not admitted:
        return
    try:
        if not await _upgrade_to_tls(writer):
            return
        line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
        if not line:
            writer.close()
            return
        msg = json.loads(line.decode("utf-8"))
        if not isinstance(msg, dict) or msg.get("type") != "data_hello":  # see handle_control
            writer.close()
            return
        conn_id = msg.get("id")
        fut = pending.pop(conn_id, None)
        if fut is None or fut.done():
            writer.close()
            return
        fut.set_result((reader, writer))
    except Exception:
        writer.close()
    finally:
        _release_conn_slot(peer_ip)


async def pipe(reader, writer):
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=PIPE_IDLE_TIMEOUT)
            if not chunk:
                break
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), timeout=PIPE_IDLE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        # OSError, not just ConnectionReset/BrokenPipe: ssl.SSLError is one too, and
        # escaping here released the player's slot while the other direction was still
        # piping.
        pass
    finally:
        writer.close()


async def handle_public(reader, writer):
    peer = writer.get_extra_info("peername")
    # The slot is held for this whole call, including the piped session that follows
    # - a real player's connection counts against their source IP's quota too.
    peer_ip, admitted = _admit(writer)
    if not admitted:
        return
    subdomain = None
    reserved_pending_slot = False
    try:
        try:
            raw, server_address = await asyncio.wait_for(read_handshake(reader), timeout=HANDSHAKE_TIMEOUT)
        except Exception:
            return

        subdomain = server_address.rstrip(".").split(".")[0].lower()
        control_writer = clients.get(subdomain)
        if control_writer is None:
            print(f"[public] {peer}: no backend registered for {server_address!r}", flush=True)
            return

        # Only covers the negotiation below and is released before piping, so it caps
        # simultaneous fake connects per subdomain without limiting real players.
        if pending_by_subdomain[subdomain] >= MAX_PENDING_PER_SUBDOMAIN:
            print(f"[public] {peer}: too many in-flight connections for {subdomain!r} - dropping", flush=True)
            return
        pending_by_subdomain[subdomain] += 1
        reserved_pending_slot = True

        conn_id = str(uuid.uuid4())
        fut = asyncio.get_event_loop().create_future()
        pending[conn_id] = fut

        try:
            await send_json(control_writer, {"type": "connect", "id": conn_id})
        except Exception:
            pending.pop(conn_id, None)
            return

        try:
            data_reader, data_writer = await asyncio.wait_for(fut, timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            pending.pop(conn_id, None)
            print(f"[public] {peer}: backend for {subdomain!r} didn't respond in time", flush=True)
            return
        finally:
            _decrement(pending_by_subdomain, subdomain)
            reserved_pending_slot = False

        try:
            data_writer.write(raw)
            await asyncio.wait_for(data_writer.drain(), timeout=HANDSHAKE_TIMEOUT)
        except Exception:
            # The backend can reset or stall right as the handshake is replayed; close
            # data_writer here or it leaks.
            data_writer.close()
            return

        print(f"[public] {peer} -> {subdomain}", flush=True)
        await asyncio.gather(
            pipe(reader, data_writer),
            pipe(data_reader, writer),
        )
    finally:
        if reserved_pending_slot and subdomain:
            _decrement(pending_by_subdomain, subdomain)
        _release_conn_slot(peer_ip)
        writer.close()


def _write_status_snapshot(start_time):
    snapshot = {
        "updated_at": time.time(),
        "uptime_seconds": round(time.monotonic() - start_time, 1),
        "connected_subdomains": sorted(clients.keys()),
        "concurrent_connections_by_ip": dict(conn_counts),
    }
    write_json_atomic(STATUS_PATH, snapshot)


async def _background_loop(start_time):
    """Runs for the relay's lifetime: refreshes the status snapshot every tick and
    sweeps stale conn_attempts every CLEANUP_INTERVAL."""
    last_cleanup = time.monotonic()
    while True:
        await asyncio.sleep(STATUS_WRITE_INTERVAL)
        try:
            # This runs in the same gather() as the servers, so an exception here (a
            # disk-full status write) would crash the whole relay.
            _write_status_snapshot(start_time)

            now = time.monotonic()
            if now - last_cleanup >= CLEANUP_INTERVAL:
                last_cleanup = now
                cutoff = now - CONN_RATE_WINDOW
                stale = [ip for ip, attempts in conn_attempts.items() if not attempts or attempts[-1] < cutoff]
                for ip in stale:
                    del conn_attempts[ip]
        except Exception as e:
            print(f"[background] error in status/cleanup loop: {e}", flush=True)


DEFAULT_TLS_CERT = Path(__file__).resolve().parent / "certs" / "fullchain.pem"
DEFAULT_TLS_KEY = Path(__file__).resolve().parent / "certs" / "privkey.pem"


def _build_tls_context(cert_path, key_path):
    """The control/data channels carry per-user tokens over the internet, so TLS is
    mandatory. The public Minecraft port stays plain - vanilla clients don't speak
    TLS."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context


async def main():
    global tls_context
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--control-port", type=int, default=7000)
    parser.add_argument("--data-port", type=int, default=7001)
    parser.add_argument("--public-port", type=int, default=25565)
    parser.add_argument("--tls-cert", default=str(DEFAULT_TLS_CERT))
    parser.add_argument("--tls-key", default=str(DEFAULT_TLS_KEY))
    args = parser.parse_args()

    if not Path(args.tls_cert).exists() or not Path(args.tls_key).exists():
        raise SystemExit(
            f"TLS cert/key not found ({args.tls_cert}, {args.tls_key}) - the control/data channels require TLS "
            "and won't start without it. See relay/README.md for provisioning a certificate."
        )
    tls_context = _build_tls_context(args.tls_cert, args.tls_key)

    # No ssl= for control/data - see tls_context: TLS is upgraded in the handlers so the
    # rate limit and connection cap see every raw connection.
    control_server = await asyncio.start_server(handle_control, args.bind, args.control_port)
    data_server = await asyncio.start_server(handle_data, args.bind, args.data_port)
    public_server = await asyncio.start_server(handle_public, args.bind, args.public_port)

    print(
        f"relay listening: control :{args.control_port} (TLS)  data :{args.data_port} (TLS)  "
        f"public :{args.public_port} (plain)",
        flush=True,
    )

    start_time = time.monotonic()
    _write_status_snapshot(start_time)  # so admin_cli.py status has something to read immediately, not just after the first tick
    background_task = asyncio.create_task(_background_loop(start_time))

    async with control_server, data_server, public_server:
        await asyncio.gather(
            control_server.serve_forever(),
            data_server.serve_forever(),
            public_server.serve_forever(),
            background_task,
        )


if __name__ == "__main__":
    asyncio.run(main())
