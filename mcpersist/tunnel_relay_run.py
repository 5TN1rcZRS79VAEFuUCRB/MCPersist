"""The tunnel client itself, run as a standalone detached process: registers with the
relay (see relay/relay_server.py) and bridges each player connection it's handed to
the local Minecraft server. Reconnects with backoff if the relay connection drops."""

import asyncio
import json
import ssl
from pathlib import Path

from . import config

RECONNECT_DELAY = 5
LOCAL_PORT = 25565
ASSIGNED_ADDRESS_PATH = Path("assigned_address.txt")

# The control/data channels carry per-user tokens and now require TLS on the relay
# side (see relay/relay_server.py) - the default system CA bundle is enough to
# verify a real Let's Encrypt cert, same as any normal HTTPS client. The local
# connection to the Minecraft server itself (127.0.0.1) is never wrapped - that's
# plain loopback traffic to a process on this same machine, nothing to encrypt.
_TLS_CONTEXT = ssl.create_default_context()


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


async def handle_connect(relay_host, data_port, conn_id):
    try:
        data_reader, data_writer = await asyncio.open_connection(
            relay_host, data_port, ssl=_TLS_CONTEXT, server_hostname=relay_host
        )
        data_writer.write((json.dumps({"type": "data_hello", "id": conn_id}) + "\n").encode("utf-8"))
        await data_writer.drain()

        local_reader, local_writer = await asyncio.open_connection("127.0.0.1", LOCAL_PORT)
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

    reader, writer = await asyncio.open_connection(
        relay_host, control_port, ssl=_TLS_CONTEXT, server_hostname=relay_host
    )
    register_msg = {"type": "register"}
    if subdomain and token:
        register_msg["subdomain"] = subdomain
        register_msg["token"] = token
    writer.write((json.dumps(register_msg) + "\n").encode("utf-8"))
    await writer.drain()

    reply = await reader.readline()
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
        line = await reader.readline()
        if not line:
            print("control connection closed by relay", flush=True)
            return
        msg = json.loads(line.decode("utf-8"))
        if msg.get("type") == "connect":
            asyncio.create_task(handle_connect(relay_host, data_port, msg["id"]))
        elif msg.get("type") == "ping":
            # The relay's own liveness check (see relay_server.py's PING_TIMEOUT) -
            # answering keeps this connection's subdomain from being evicted as
            # stale while it's still genuinely alive and just has no players.
            writer.write((json.dumps({"type": "pong"}) + "\n").encode("utf-8"))
            await writer.drain()


async def main():
    cfg = config.load()
    while True:
        try:
            await run_once(cfg)
        except Exception as e:
            print(f"connection error: {e}", flush=True)
        print(f"reconnecting in {RECONNECT_DELAY}s ...", flush=True)
        await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(main())
