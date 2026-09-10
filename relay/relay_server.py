"""The relay: an asyncio TCP server that lets many people's MCPersist servers share
one public IP/port, routing each incoming Minecraft connection to the right one by
the subdomain the player typed, then piping bytes between them. See README.md for
the wire protocol and deployment steps."""

import argparse
import asyncio
import json
import random
import time
import uuid

from mc_handshake import read_handshake
from users import load_users, verify
from auto_assignments import load_assignments, save_assignments

CONNECT_TIMEOUT = 10
HANDSHAKE_TIMEOUT = 10  # a client that never finishes its handshake shouldn't hold a connection open forever
MAX_AUTO_CONNECTIONS_PER_IP = 3

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

clients = {}  # subdomain -> StreamWriter of the control connection
pending = {}  # connection id -> asyncio.Future resolving to (data_reader, data_writer)
auto_conn_counts = {}  # source ip -> count of live auto-registered (unreserved) connections

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
    # extremely unlikely fallback if the namespace is saturated
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(100, 999)}"


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
    writer.write((json.dumps(obj) + "\n").encode("utf-8"))
    await writer.drain()


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
                return
    except asyncio.CancelledError:
        pass


async def handle_control(reader, writer):
    subdomain = None
    is_auto = False
    ping_task = None
    peer_ip = (writer.get_extra_info("peername") or (None,))[0]
    try:
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
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError, json.JSONDecodeError):
        pass
    finally:
        if ping_task:
            ping_task.cancel()
        if subdomain and clients.get(subdomain) is writer:
            del clients[subdomain]
            print(f"[control] {subdomain} disconnected", flush=True)
        if is_auto and peer_ip and peer_ip in auto_conn_counts:
            auto_conn_counts[peer_ip] -= 1
            if auto_conn_counts[peer_ip] <= 0:
                del auto_conn_counts[peer_ip]
        writer.close()


async def handle_data(reader, writer):
    try:
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


async def pipe(reader, writer):
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()


async def handle_public(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        raw, server_address = await asyncio.wait_for(read_handshake(reader), timeout=HANDSHAKE_TIMEOUT)
    except Exception:
        writer.close()
        return

    subdomain = server_address.rstrip(".").split(".")[0].lower()
    control_writer = clients.get(subdomain)
    if control_writer is None:
        print(f"[public] {peer}: no backend registered for {server_address!r}", flush=True)
        writer.close()
        return

    conn_id = str(uuid.uuid4())
    fut = asyncio.get_event_loop().create_future()
    pending[conn_id] = fut

    try:
        await send_json(control_writer, {"type": "connect", "id": conn_id})
    except Exception:
        pending.pop(conn_id, None)
        writer.close()
        return

    try:
        data_reader, data_writer = await asyncio.wait_for(fut, timeout=CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        pending.pop(conn_id, None)
        print(f"[public] {peer}: backend for {subdomain!r} didn't respond in time", flush=True)
        writer.close()
        return

    data_writer.write(raw)
    await data_writer.drain()

    print(f"[public] {peer} -> {subdomain}", flush=True)
    await asyncio.gather(
        pipe(reader, data_writer),
        pipe(data_reader, writer),
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--control-port", type=int, default=7000)
    parser.add_argument("--data-port", type=int, default=7001)
    parser.add_argument("--public-port", type=int, default=25565)
    args = parser.parse_args()

    control_server = await asyncio.start_server(handle_control, args.bind, args.control_port)
    data_server = await asyncio.start_server(handle_data, args.bind, args.data_port)
    public_server = await asyncio.start_server(handle_public, args.bind, args.public_port)

    print(
        f"relay listening: control :{args.control_port}  data :{args.data_port}  public :{args.public_port}",
        flush=True,
    )

    async with control_server, data_server, public_server:
        await asyncio.gather(
            control_server.serve_forever(),
            data_server.serve_forever(),
            public_server.serve_forever(),
        )


if __name__ == "__main__":
    asyncio.run(main())
