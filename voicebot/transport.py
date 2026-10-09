"""Per-call network worker. Never invoke PJSIP from this thread."""
from __future__ import annotations

import queue
import select
import socket
import threading
import uuid

from .pcm import AudioBuffers, FRAME_BYTES
from .protocol import audio_message, audio_payload, encode, open_message, read


class BridgeClient:
    def __init__(self, buffers: AudioBuffers, token: str, port: int = 8765,
                 session_id: str | None = None):
        self.buffers = buffers
        self.token = token
        self.port = port
        self.session_id = session_id or str(uuid.uuid4())
        self.connected = threading.Event()
        self.finished = threading.Event()
        self.stopping = threading.Event()
        self.error = ""
        self.report = None
        self.worker = threading.Thread(target=self._run, name="VoiceBotPCM", daemon=True)

    def start(self):
        self.worker.start()

    def close(self):
        self.buffers.close()
        self.stopping.set()
        self.worker.join(timeout=2.5)

    def _run(self):
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.0) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(encode(open_message(self.session_id, self.token)))
                reply = read(sock)
                if reply != {"type": "ready", "session_id": self.session_id}:
                    raise ValueError("Bridge handshake rejected")
                self.connected.set()
                pending = b""
                rx_seq = tx_seq = 0
                while not self.stopping.is_set():
                    # Limit work per iteration so outbound load cannot starve inbound audio.
                    for _ in range(5):
                        try:
                            pending += self.buffers.incoming.get_nowait()
                        except queue.Empty:
                            break
                    while len(pending) >= FRAME_BYTES:
                        frame, pending = pending[:FRAME_BYTES], pending[FRAME_BYTES:]
                        sock.sendall(encode(audio_message(self.session_id, tx_seq, frame)))
                        tx_seq += 1
                    if select.select([sock], [], [], 0.005)[0]:
                        message = read(sock)
                        if message == {"type": "audio_end", "session_id": self.session_id, "frames": rx_seq}:
                            self.buffers.finish_output()
                            continue
                        if message.get("type") != "audio":
                            raise ValueError("Unexpected server message")
                        self.buffers.send(audio_payload(message, self.session_id, rx_seq))
                        rx_seq += 1
                sock.sendall(encode({"type": "close", "session_id": self.session_id}))
                # Audio already in flight may precede the final report.
                while True:
                    reply = read(sock)
                    if reply.get("type") == "closed" and reply.get("session_id") == self.session_id:
                        self.report = reply.get("report")
                        break
        except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
            self.error = str(exc)
        finally:
            self.connected.clear()
            self.buffers.close()
            self.finished.set()
