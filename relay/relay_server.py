"""The relay: an asyncio TCP server that lets many people's MCPersist servers share
one public IP/port, routing each incoming Minecraft connection to the right one by
the subdomain the player typed, then piping bytes between them. See README.md for
the wire protocol and deployment steps."""

import argparse
import asyncio
import json
import os
import random
import ssl
import time
import uuid
from pathlib import Path

from mc_handshake import read_handshake
from users import load_users, verify
from auto_assignments import load_assignments, save_assignments

CONNECT_TIMEOUT = 10
HANDSHAKE_TIMEOUT = 10  # a client that never finishes its handshake shouldn't hold a connection open forever
MAX_AUTO_CONNECTIONS_PER_IP = 3

# Two separate abuse patterns, two separate caps:
#  - MAX_CONNECTIONS_PER_IP guards against one source just opening a lot of raw
#    sockets (to any of the three ports) and sitting on them - a generous ceiling,
#    high enough that real multiplayer use (several people behind one NAT/CGNAT
#    IP joining at once) never gets close to it.
#  - MAX_PENDING_PER_SUBDOMAIN guards a specific backend from being hammered with
#    simultaneous fake "connect" attempts, which a flood spread across many source
#    IPs would otherwise dodge entirely if only MAX_CONNECTIONS_PER_IP existed. It
#    only counts the brief negotiation window (waiting for that backend's
#    data_hello), not established sessions, so real concurrent players on one
#    server are never affected by it.
MAX_CONNECTIONS_PER_IP = 20
MAX_PENDING_PER_SUBDOMAIN = 10

# A third, different abuse pattern from the two above: rapid connect/disconnect
# cycling. Each individual connection can be too brief to ever accumulate against
# MAX_CONNECTIONS_PER_IP (which only counts *concurrent* sockets), so a source
# opening and immediately closing connections in a tight loop would sail right
# through it. This counts attempts over a rolling window instead - same shape as
# e4mc's own relay, which rate-limits at 30/minute per IP.
CONN_RATE_WINDOW = 60
CONN_RATE_LIMIT = 30

# conn_attempts entries only get pruned when that IP makes another attempt - one
# that stops entirely (routine for scanner/bot traffic, already a confirmed real
# pattern in this relay's own logs) would otherwise leave a small stale entry in
# memory forever. Periodic cleanup (see _background_loop) bounds that instead of
# leaving it unbounded over a long-running process.
CLEANUP_INTERVAL = 300

# How often _background_loop refreshes the on-disk status snapshot - cheap (a
# handful of dict lookups + a small JSON write), and gives admin_cli.py's `status`
# command a near-live view without needing a live connection to the relay process
# itself.
STATUS_WRITE_INTERVAL = 10
STATUS_PATH = Path(__file__).resolve().parent / "relay_status.json"

# A control connection is only ever recognized as dead by a clean TCP close
# (reader.readline() returning EOF) - fine for a normal disconnect, but a tunnel
# client that gets force-killed, crashes, or drops off the network without sending
# a FIN leaves the socket looking perfectly healthy to the relay indefinitely (TCP
# has no built-in way to notice this on its own for a long time). That leaves the
# subdomain permanently occupied by a connection nothing is using anymore, so a
# real reconnection attempt gets rejected forever with "already connected from this
# address" - confirmed as a real occurrence, not just theoretical, from a client
# stuck reconnecting every 5s with no way to ever get back in. Sending our own ping
# and requiring a reply within PING_TIMEOUT catches that instead of trusting TCP to
# notice on its own.
PING_INTERVAL = 20
PING_TIMEOUT = 45

# Established player<->backend sessions have no other timeout covering them once
# piping starts (HANDSHAKE_TIMEOUT/CONNECT_TIMEOUT are already released by then, and
# CONN_RATE_LIMIT only gates *new* attempts) - a connection that completes the
# handshake and then just sits there, sending nothing forever (deliberately, or a
# genuinely frozen client with no clean FIN), would otherwise hold one of that IP's
# MAX_CONNECTIONS_PER_IP slots and both real sockets open indefinitely - a slow-loris
# gap the other protections don't cover. Real Minecraft traffic includes a keepalive
# packet roughly once a second in both directions, so this is generously loose enough
# to never affect an actual player, just to bound a truly-idle-forever connection.
PIPE_IDLE_TIMEOUT = 300

clients = {}  # subdomain -> StreamWriter of the control connection
pending = {}  # connection id -> asyncio.Future resolving to (data_reader, data_writer)
auto_conn_counts = {}  # source ip -> count of live auto-registered (unreserved) connections
conn_counts = {}  # source ip -> concurrent raw connections, across control+data+public
pending_by_subdomain = {}  # subdomain -> count of in-flight (not yet established) connect attempts
conn_attempts = {}  # source ip -> list of monotonic timestamps of recent connection attempts

# Set once in main() before the servers start accepting - handle_control/handle_data
# do the TLS upgrade themselves (see _upgrade_to_tls) rather than asyncio.start_server
# doing it via ssl=, specifically so the rate limiter/connection cap run against
# *every* raw connection attempt, not just ones that complete a valid handshake.
tls_context = None


def _acquire_conn_slot(peer_ip):
    """Called at the top of every connection handler, for all three ports. Returns
    False (caller should close immediately) once one source IP is holding too many
    open sockets at once - a cheap, blunt defense against a raw connection flood,
    independent of whatever that connection eventually turns out to be (a real
    player, a registration attempt, or nothing at all)."""
    if peer_ip is None:
        return True  # can't identify the source (unusual) - let it through rather than break on it
    if conn_counts.get(peer_ip, 0) >= MAX_CONNECTIONS_PER_IP:
        return False
    conn_counts[peer_ip] = conn_counts.get(peer_ip, 0) + 1
    return True


def _release_conn_slot(peer_ip):
    if peer_ip is None:
        return
    remaining = conn_counts.get(peer_ip, 0) - 1
    if remaining <= 0:
        conn_counts.pop(peer_ip, None)
    else:
        conn_counts[peer_ip] = remaining


async def _upgrade_to_tls(writer):
    """Called after the rate-limit/conn-cap checks, not before - see the module-level
    tls_context comment for why this is done as an explicit in-handler upgrade
    (writer.start_tls()) rather than asyncio.start_server(..., ssl=...) doing it
    implicitly. Returns True on success; on failure (garbage/non-TLS input, a
    mid-handshake disconnect, or simply never sending anything) closes the writer
    and returns False, same shape as _acquire_conn_slot's caller-checks-and-closes
    pattern.

    Wrapped in the same HANDSHAKE_TIMEOUT every other read in this file uses -
    without it, a connection that completes the raw TCP accept and then sends
    nothing at all hangs here forever, never reaching the finally block that
    releases its conn_slot. Confirmed as a real, cheap DoS by direct reproduction:
    MAX_CONNECTIONS_PER_IP (20 by default) silent connections from one IP
    permanently exhausted that IP's entire quota - across all three ports, since
    conn_counts is shared - with no recovery, ever."""
    try:
        await asyncio.wait_for(writer.start_tls(tls_context), timeout=HANDSHAKE_TIMEOUT)
        return True
    except Exception:
        writer.close()
        return False


def _rate_limited(peer_ip):
    """True if this source IP has made too many connection attempts within
    CONN_RATE_WINDOW - catches a rapid connect/disconnect burst that
    MAX_CONNECTIONS_PER_IP wouldn't, since a brief connection might never
    accumulate more than one or two *concurrent* slots no matter how many times
    it's repeated. Called once per connection attempt (unlike
    _acquire_conn_slot/_release_conn_slot, there's no matching release - an
    attempt either counts against the window or it doesn't, permanently, until
    it ages out)."""
    if peer_ip is None:
        return False
    now = time.monotonic()
    cutoff = now - CONN_RATE_WINDOW
    attempts = conn_attempts.setdefault(peer_ip, [])
    while attempts and attempts[0] < cutoff:
        attempts.pop(0)
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
    for _ in range(50):
        candidate = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
        if candidate not in taken:
            return candidate
    # Extremely unlikely fallback if the namespace is saturated (auto_assignments.json
    # entries are never pruned by design - see get_or_assign_subdomain - so a
    # long-lived, busy relay could realistically approach this over time). Still
    # checked against `taken`, same as the primary loop above - an unchecked
    # fallback could otherwise hand out a collision, silently recording two
    # different IPs against the same subdomain in auto_assignments.json.
    for _ in range(50):
        candidate = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(100, 999)}"
        if candidate not in taken:
            return candidate
    return f"{uuid.uuid4().hex[:12]}"


def get_or_assign_subdomain(peer_ip):
    """Auto (unreserved) clients get a persistent subdomain tied to their source IP -
    the same IP always gets the same subdomain back, forever (until manually cleared
    from auto_assignments.json). Note: this means people sharing a public IP via
    CGNAT would end up sharing an identity here too - a real limitation, not just a
    hypothetical one, but that's the tradeoff that was asked for over pure
    per-connection randomness."""
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
    # drain() only actually blocks once the transport's write buffer backs up -
    # normally near-instant, but a peer that stops reading (deliberately, or a
    # zero TCP receive window) can stall it indefinitely, same underlying pattern
    # as the _upgrade_to_tls timeout fix: every caller here (ping replies,
    # registration success/error, the connect notification to a backend) would
    # otherwise hang its connection - and the conn_slot it's still holding - open
    # forever instead of the caller's own except block ever getting a chance to
    # clean up.
    writer.write((json.dumps(obj) + "\n").encode("utf-8"))
    await asyncio.wait_for(writer.drain(), timeout=HANDSHAKE_TIMEOUT)


async def _ping_loop(writer, subdomain, last_seen):
    """Runs alongside handle_control's own read loop for the same connection -
    periodically pings the client and, if nothing has been heard from it (a pong or
    anything else) within PING_TIMEOUT, closes the connection so a genuinely dead
    client can't hold its subdomain hostage forever. Closing the writer here
    unblocks the main handler's own blocked readline() (a closed transport
    completes pending reads with EOF/an error), so the usual cleanup in its
    `finally` block still runs normally."""
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
                # Same treatment as the stale-timeout branch above, not a silent
                # exit - a failed/timed-out ping write (e.g. drain() backpressure)
                # otherwise leaves this task gone for good with nothing left to
                # ever detect this connection going dark later, quietly reopening
                # the exact "subdomain permanently occupied by a connection
                # nothing is using" bug PING_INTERVAL/PING_TIMEOUT exists to fix.
                writer.close()
                return
    except asyncio.CancelledError:
        pass


async def handle_control(reader, writer):
    subdomain = None
    is_auto = False
    auto_counted = False
    ping_task = None
    peer_ip = (writer.get_extra_info("peername") or (None,))[0]
    if _rate_limited(peer_ip):
        writer.close()
        return
    if not _acquire_conn_slot(peer_ip):
        writer.close()
        return
    try:
        if not await _upgrade_to_tls(writer):
            return
        line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
        if not line:
            return
        msg = json.loads(line.decode("utf-8"))
        if msg.get("type") != "register":
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
            if auto_conn_counts.get(peer_ip, 0) >= MAX_AUTO_CONNECTIONS_PER_IP:
                await send_json(writer, {"type": "error", "message": "too many connections from this address"})
                writer.close()
                return
            subdomain = get_or_assign_subdomain(peer_ip)
            if subdomain in clients:
                await send_json(writer, {"type": "error", "message": "already connected from this address"})
                writer.close()
                return
            auto_conn_counts[peer_ip] = auto_conn_counts.get(peer_ip, 0) + 1
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
    except Exception:
        # Deliberately broad, not just the handful of exception types a well-behaved
        # client can trigger (IncompleteReadError/TimeoutError/ConnectionResetError/
        # JSONDecodeError) - this port is open to the whole internet with no auth
        # required to reach this point (see is_auto below), so it also has to
        # survive genuinely malformed input, not just genuine clients disconnecting
        # oddly. Confirmed as a real gap, not hypothetical: a scanner sending
        # non-UTF8 bytes here previously hit an uncaught UnicodeDecodeError from
        # line.decode("utf-8") - harmless (asyncio's own handler logged it and moved
        # on), but every connection handler in this file should close cleanly on bad
        # input on its own instead of relying on that fallback, same as handle_data
        # and handle_public already do.
        pass
    finally:
        if ping_task:
            ping_task.cancel()
        if subdomain and clients.get(subdomain) is writer:
            del clients[subdomain]
            print(f"[control] {subdomain} disconnected", flush=True)
        if auto_counted and peer_ip and peer_ip in auto_conn_counts:
            # Only decrement if THIS connection is the one that incremented it above -
            # is_auto alone isn't enough to gate this: it's set True before the
            # auto-registration checks run, including the ones that reject and
            # return early (already at MAX_AUTO_CONNECTIONS_PER_IP, or "already
            # connected from this address") without ever incrementing the counter.
            # Decrementing unconditionally on any is_auto connection would let a
            # rejected duplicate attempt from an IP that already has a real,
            # counted auto connection wrongly drop that real connection's count to
            # zero - defeating MAX_AUTO_CONNECTIONS_PER_IP for every attempt after it.
            auto_conn_counts[peer_ip] -= 1
            if auto_conn_counts[peer_ip] <= 0:
                del auto_conn_counts[peer_ip]
        _release_conn_slot(peer_ip)
        writer.close()


async def handle_data(reader, writer):
    # Only guards this connection's own brief registration handshake (a few
    # hundred ms for a real client) - once a data_hello is matched, ownership of
    # reader/writer passes to whichever handle_public call is waiting on `fut`, so
    # the slot is freed here rather than tracking the session that follows. Still
    # closes off the actual risk this cap targets: a source opening many raw
    # connections here and never completing the handshake.
    peer_ip = (writer.get_extra_info("peername") or (None,))[0]
    if _rate_limited(peer_ip):
        writer.close()
        return
    if not _acquire_conn_slot(peer_ip):
        writer.close()
        return
    try:
        if not await _upgrade_to_tls(writer):
            return
        line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
        if not line:
            writer.close()
            return
        msg = json.loads(line.decode("utf-8"))
        if msg.get("type") != "data_hello":
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
    except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


async def handle_public(reader, writer):
    peer = writer.get_extra_info("peername")
    peer_ip = peer[0] if peer else None
    if _rate_limited(peer_ip):
        writer.close()
        return
    # Held for this whole call, including the piped session that follows - a real
    # player's connection legitimately counts against their source IP's quota here,
    # same as any other raw socket against this relay.
    if not _acquire_conn_slot(peer_ip):
        writer.close()
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

        # Only covers the negotiation below (waiting on this specific backend's
        # data_hello) - deliberately released before piping starts, so this caps how
        # many simultaneous fake "connect" attempts one subdomain's tunnel client can
        # be hit with (regardless of how many source IPs they're spread across),
        # without limiting how many real players it can actually serve at once.
        if pending_by_subdomain.get(subdomain, 0) >= MAX_PENDING_PER_SUBDOMAIN:
            print(f"[public] {peer}: too many in-flight connections for {subdomain!r} - dropping", flush=True)
            return
        pending_by_subdomain[subdomain] = pending_by_subdomain.get(subdomain, 0) + 1
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
            pending_by_subdomain[subdomain] -= 1
            if pending_by_subdomain[subdomain] <= 0:
                del pending_by_subdomain[subdomain]
            reserved_pending_slot = False

        try:
            data_writer.write(raw)
            await asyncio.wait_for(data_writer.drain(), timeout=HANDSHAKE_TIMEOUT)
        except Exception:
            # The backend accepted the data connection but then reset/stalled right
            # as we tried to replay the buffered handshake into it - a real,
            # reachable race (confirmed by review, not just theoretical), not just
            # the player's own socket. Without this, an exception here skips
            # straight to `finally`, which only ever closed `writer` (the public
            # socket) - data_writer was never closed anywhere else on this path and
            # would leak, relying on GC to eventually reclaim it.
            data_writer.close()
            return

        print(f"[public] {peer} -> {subdomain}", flush=True)
        await asyncio.gather(
            pipe(reader, data_writer),
            pipe(data_reader, writer),
        )
    finally:
        if reserved_pending_slot and subdomain:
            pending_by_subdomain[subdomain] -= 1
            if pending_by_subdomain[subdomain] <= 0:
                del pending_by_subdomain[subdomain]
        _release_conn_slot(peer_ip)
        writer.close()


def _write_status_snapshot(start_time):
    # Write-then-rename, same pattern as users.py/auto_assignments.py - admin_cli.py
    # reads this file from a completely separate process, so a reader can never see
    # a half-written snapshot.
    snapshot = {
        "updated_at": time.time(),
        "uptime_seconds": round(time.monotonic() - start_time, 1),
        "connected_subdomains": sorted(clients.keys()),
        "concurrent_connections_by_ip": dict(conn_counts),
    }
    tmp_path = STATUS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    os.replace(tmp_path, STATUS_PATH)


async def _background_loop(start_time):
    """Runs for the relay's whole lifetime: refreshes the status snapshot on every
    tick, and sweeps stale conn_attempts entries every CLEANUP_INTERVAL - one task
    instead of two separate timers, since the status-write cadence is already
    frequent enough to piggyback the much-less-frequent cleanup on top of."""
    last_cleanup = time.monotonic()
    while True:
        await asyncio.sleep(STATUS_WRITE_INTERVAL)
        try:
            # This coroutine runs inside the same top-level asyncio.gather() as the
            # three real servers (see main()) - unlike every per-connection handler,
            # which is its own isolated Task and can't bring the process down, an
            # unhandled exception here (a transient disk-full/permissions error
            # writing the status file, however rare) would propagate out of gather()
            # and crash the entire relay, dropping every live connection, just to
            # skip one status-file write. Not worth that risk for a purely
            # informational admin feature.
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
    """The control/data channels carry per-user tokens and are open to the whole
    internet with no other transport security - TLS here is mandatory, not
    optional, matching that this project moved to a real cert rather than adding
    an opt-in flag that would leave the plaintext gap open by default. The public
    Minecraft port (:25565) deliberately does NOT get wrapped here - that's raw
    Minecraft protocol traffic to vanilla clients, which have no concept of TLS at
    the transport layer and would simply fail to connect at all if it were."""
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

    # No ssl= here for control/data - handle_control/handle_data do the TLS upgrade
    # themselves (_upgrade_to_tls), specifically so the rate limiter/connection cap
    # run against every raw connection attempt, not just ones that complete a valid
    # handshake (asyncio.start_server(..., ssl=...) would only invoke the callback
    # after a successful handshake, letting anything that fails one bypass both
    # protections entirely - confirmed as a real gap, not hypothetical).
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
