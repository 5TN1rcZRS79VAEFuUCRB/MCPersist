"""The tunnel client itself, run as a standalone detached process: registers with the
relay (see relay/relay_server.py) and bridges each player connection it's handed to
the local Minecraft server. Reconnects with backoff if the relay connection drops."""

import asyncio
import functools
import json
import ssl
from pathlib import Path

from . import config

RECONNECT_DELAY = 5
LOCAL_PORT = 25565
ASSIGNED_ADDRESS_PATH = Path("assigned_address.txt")
TUNNEL_START_MARKER = "tunnel starting"

# Bounds every network await (connect, TLS handshake, registration reply): a relay that
# accepts and then stalls would otherwise hang this forever, and main()'s reconnect loop
# would never run.
CONNECT_TIMEOUT = 15

# Bounds each player pipe's read/drain, so a dropped network path or hung local server
# can't leak a task and socket pair per player forever.
PIPE_IDLE_TIMEOUT = 300

# The relay pings every ~20s; without a timeout here a dead connection with no FIN hangs
# readline() forever, looking healthy while relaying nothing. Well above PING_INTERVAL
# to allow for jitter.
CONTROL_IDLE_TIMEOUT = 90

# Control/data channels are TLS; the system CA bundle verifies the relay's cert. Built
# on first use, not at import, so a broken certificate store goes through main()'s retry
# loop instead of crashing.
@functools.cache
def _get_tls_context():
    return ssl.create_default_context()


async def pipe(reader, writer):
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=PIPE_IDLE_TIMEOUT)
            if not chunk:
                break
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), timeout=PIPE_IDLE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        # OSError covers ConnectionResetError/BrokenPipeError/ssl.SSLError (all
        # subclasses) in one catch, same as relay_server.py's hardened pipe().
        pass
    finally:
        writer.close()


async def handle_connect(relay_host, data_port, conn_id):
    try:
        data_reader, data_writer = await asyncio.wait_for(
            asyncio.open_connection(relay_host, data_port, ssl=_get_tls_context(), server_hostname=relay_host),
            timeout=CONNECT_TIMEOUT,
        )
        data_writer.write((json.dumps({"type": "data_hello", "id": conn_id}) + "\n").encode("utf-8"))
        await asyncio.wait_for(data_writer.drain(), timeout=CONNECT_TIMEOUT)

        local_reader, local_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", LOCAL_PORT), timeout=CONNECT_TIMEOUT
        )
    except Exception as e:
        print(f"failed to bridge connection {conn_id}: {e}", flush=True)
        return

    await asyncio.gather(
        pipe(data_reader, local_writer),
        pipe(local_reader, data_writer),
    )


async def run_once(cfg):
    relay_host = cfg["relay_host"]
    control_port = cfg["relay_control_port"]
    data_port = cfg["relay_data_port"]
    subdomain = cfg.get("subdomain")
    token = cfg.get("relay_token")
    public_domain = cfg.get("public_domain")

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(relay_host, control_port, ssl=_get_tls_context(), server_hostname=relay_host),
        timeout=CONNECT_TIMEOUT,
    )
    # Closed on every return path - main() calls this in an endless reconnect loop, so a
    # leak here is one socket per cycle.
    try:
        register_msg = {"type": "register"}
        if subdomain and token:
            register_msg["subdomain"] = subdomain
            register_msg["token"] = token
        writer.write((json.dumps(register_msg) + "\n").encode("utf-8"))
        await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT)

        reply = await asyncio.wait_for(reader.readline(), timeout=CONNECT_TIMEOUT)
        if not reply:
            print("relay closed the connection during registration", flush=True)
            return
        msg = json.loads(reply.decode("utf-8"))
        if msg.get("type") != "registered":
            print(f"registration failed: {msg}", flush=True)
            return

        assigned_subdomain = msg.get("subdomain", subdomain)
        join_address = f"{assigned_subdomain}.{public_domain}" if public_domain else assigned_subdomain
        print(f"registered as {assigned_subdomain!r} on {relay_host} - join at {join_address}", flush=True)
        try:
            ASSIGNED_ADDRESS_PATH.write_text(join_address, encoding="utf-8")
        except OSError:
            pass

        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=CONTROL_IDLE_TIMEOUT)
            if not line:
                print("control connection closed by relay", flush=True)
                return
            msg = json.loads(line.decode("utf-8"))
            if msg.get("type") == "connect":
                asyncio.create_task(handle_connect(relay_host, data_port, msg["id"]))
            elif msg.get("type") == "ping":
                # Answer the relay's liveness check, so an idle tunnel isn't evicted as
                # stale.
                writer.write((json.dumps({"type": "pong"}) + "\n").encode("utf-8"))
                await asyncio.wait_for(writer.drain(), timeout=CONNECT_TIMEOUT)
    finally:
        writer.close()


async def main():
    cfg = config.load()
    # The log is appended across runs - actions.tunnel_log_problem stops at this
    # line so a previous run's errors aren't reported against this one.
    print(TUNNEL_START_MARKER, flush=True)
    while True:
        try:
            await run_once(cfg)
        except Exception as e:
            print(f"connection error: {e}", flush=True)
        print(f"reconnecting in {RECONNECT_DELAY}s ...", flush=True)
        await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(main())
