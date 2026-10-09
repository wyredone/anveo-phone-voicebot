"""Length-prefixed JSON v1 over authenticated loopback TCP."""
from __future__ import annotations

import base64
import json
import secrets
import socket
import struct
import time
from pathlib import Path

from .pcm import FORMAT, FRAME_BYTES

MAX_MESSAGE = 16384


def token_file(path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
        path.chmod(0o600)
    except FileExistsError:
        pass
    token = path.read_text(encoding="ascii").strip()
    # Another launcher process may have exclusively created the file and be writing it.
    for _ in range(20):
        if token:
            break
        time.sleep(0.01)
        token = path.read_text(encoding="ascii").strip()
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid token file")
    return token


def encode(message: dict) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ValueError("Message too large")
    return struct.pack("!I", len(payload)) + payload


def exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError("Peer disconnected")
        data.extend(chunk)
    return bytes(data)


def read(sock: socket.socket) -> dict:
    size = struct.unpack("!I", exact(sock, 4))[0]
    if size <= 0 or size > MAX_MESSAGE:
        raise ValueError("Invalid message length")
    message = json.loads(exact(sock, size))
    if not isinstance(message, dict):
        raise ValueError("Expected object")
    return message


def audio_message(session_id: str, seq: int, pcm: bytes) -> dict:
    if len(pcm) != FRAME_BYTES:
        raise ValueError("Wire audio must be exactly one 20 ms frame")
    return {"type": "audio", "session_id": session_id, "seq": seq,
            "pcm": base64.b64encode(pcm).decode("ascii")}


def audio_payload(message: dict, session_id: str, expected_seq: int) -> bytes:
    if message.get("session_id") != session_id or message.get("seq") != expected_seq:
        raise ValueError("Wrong session or audio sequence")
    data = base64.b64decode(message["pcm"], validate=True)
    if len(data) != FRAME_BYTES:
        raise ValueError("Wrong PCM frame size")
    return data


def open_message(session_id: str, token: str) -> dict:
    return {"type": "open", "version": 1, "session_id": session_id,
            "token": token, "format": FORMAT}
