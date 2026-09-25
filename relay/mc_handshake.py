"""VarInt + Minecraft handshake packet parsing - reads just enough of the protocol
to learn the hostname a connecting client typed, for subdomain-based routing."""


# Real handshake packets are a few dozen bytes; this just needs to be generous enough
# for a long hostname, not a limit an attacker can use to make us buffer megabytes
# from a connection that never sends any real data.
MAX_HANDSHAKE_LENGTH = 2048


def _read_varint_from_bytes(data, pos):
    num = 0
    for i in range(5):
        b = data[pos]
        pos += 1
        num |= (b & 0x7F) << (7 * i)
        if not (b & 0x80):
            return num, pos
    raise ValueError("VarInt too big")


async def read_handshake(reader):
    """Reads a Minecraft handshake packet (length-prefixed: VarInt length, then
    packet-id, protocol-version, VarInt-prefixed server_address string, port,
    next-state). Returns (raw_bytes, server_address) - raw_bytes is exactly what was
    read off the wire, so it can be replayed verbatim to the real backend server."""
    length_bytes = b""
    while True:
        length_bytes += await reader.readexactly(1)
        if not length_bytes[-1] & 0x80:
            break
        if len(length_bytes) >= 5:
            raise ValueError("VarInt too big")
    length, _ = _read_varint_from_bytes(length_bytes, 0)

    if length > MAX_HANDSHAKE_LENGTH:
        raise ValueError(f"handshake packet too large ({length} bytes)")

    payload = await reader.readexactly(length)
    raw = length_bytes + payload

    pos = 0
    _packet_id, pos = _read_varint_from_bytes(payload, pos)
    _protocol_version, pos = _read_varint_from_bytes(payload, pos)
    addr_len, pos = _read_varint_from_bytes(payload, pos)
    server_address = payload[pos : pos + addr_len].decode("utf-8", errors="replace")

    # Legacy Forge/FML clients append a "\0FML\0..." marker to the hostname.
    server_address = server_address.split("\x00")[0]

    return raw, server_address
