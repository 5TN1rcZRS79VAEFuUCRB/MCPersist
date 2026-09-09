"""A minimal Source RCON protocol client - used to send `stop` to the Minecraft
server for a clean shutdown, and to run admin commands (whitelist, op)."""

import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2


def _send_packet(sock, packet_id, packet_type, body):
    payload = struct.pack("<ii", packet_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(payload)) + payload)


def _read_packet(sock):
    length = struct.unpack("<i", _recv_exact(sock, 4))[0]
    data = _recv_exact(sock, length)
    packet_id, packet_type = struct.unpack("<ii", data[:8])
    body = data[8:-2].decode("utf-8", errors="replace")
    return packet_id, packet_type, body


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("RCON connection closed unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_command(host, port, password, command, timeout=5):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        _send_packet(sock, 1, SERVERDATA_AUTH, password)
        auth_id, _, _ = _read_packet(sock)
        if auth_id == -1:
            raise PermissionError("RCON authentication failed")

        _send_packet(sock, 2, SERVERDATA_EXECCOMMAND, command)
        _, _, body = _read_packet(sock)
        return body
