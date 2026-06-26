from __future__ import annotations

import json
import os
import socket
import struct
from typing import Any


DISCOVERY_PORT = 51333
DEFAULT_TCP_PORT = 51334
DISCOVER_REQUEST = b"HRD_DISCOVER_V1"
ANNOUNCE_TYPE = "hrd_announce_v1"
MAX_HEADER = 1024 * 1024


class ProtocolError(RuntimeError):
    pass


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def send_packet(sock: socket.socket, message: dict[str, Any], payload: bytes = b"") -> None:
    header = dict(message)
    header["payload_len"] = len(payload)
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_HEADER:
        raise ProtocolError("header is too large")
    sock.sendall(struct.pack("!I", len(encoded)))
    sock.sendall(encoded)
    if payload:
        sock.sendall(payload)


def recv_packet(sock: socket.socket) -> tuple[dict[str, Any], bytes]:
    header_len = struct.unpack("!I", recv_exact(sock, 4))[0]
    if header_len <= 0 or header_len > MAX_HEADER:
        raise ProtocolError(f"invalid header length: {header_len}")
    header = json.loads(recv_exact(sock, header_len).decode("utf-8"))
    payload_len = int(header.get("payload_len", 0))
    if payload_len < 0:
        raise ProtocolError("invalid payload length")
    payload = recv_exact(sock, payload_len) if payload_len else b""
    return header, payload


def socket_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "min": min(values),
        "avg": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def write_json(path: str, data: dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)
