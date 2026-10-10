"""Receiver: capture caller PCM and return static or generated response audio."""
from __future__ import annotations

import json
import math
import secrets
import select
import socket
import struct
import threading
import time
import uuid
import wave
from pathlib import Path

from .pcm import FORMAT, FRAME_BYTES, RATE, tone
from .phase1 import Phase1Responder, TurnDetector, frame_count
from .protocol import audio_message, audio_payload, encode, read


class BridgeServer:
    def __init__(self, token: str, port: int = 8765, output_dir: str | Path = "artifacts/calls",
                 response: bytes | None = None, capture: bool = True,
                 responder: Phase1Responder | None = None):
        self.token = token
        self.output_dir = Path(output_dir)
        self.response = tone() if response is None else response
        if len(self.response) % 2 or len(self.response) > RATE * 2 * 30:
            raise ValueError("Response must be aligned PCM, at most 30 seconds")
        self.responder = responder
        self.capture = capture
        self.stop_event = threading.Event()
        self.reports = []
        self.listener = socket.socket()
        # Exclusive by default on Windows; do not steal an existing listener.
        self.listener.bind(("127.0.0.1", port))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(2)
        self.listener.settimeout(0.2)
        self.worker = threading.Thread(target=self.serve, name="VoiceBotServer", daemon=True)

    def start(self):
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=2.5)
        self.listener.close()

    def serve(self):
        while not self.stop_event.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with sock:
                sock.settimeout(1.0)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    self._session(sock)
                except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
                    print(json.dumps({"event": "connection_error", "error": str(exc)}), flush=True)

    def _session(self, sock):
        hello = read(sock)
        if (hello.get("type") != "open" or hello.get("version") != 1 or
                not isinstance(hello.get("token"), str) or
                not secrets.compare_digest(hello["token"], self.token) or hello.get("format") != FORMAT):
            raise ValueError("Unauthorized or incompatible bridge handshake")
        session_id = str(uuid.UUID(hello["session_id"]))
        if session_id != hello["session_id"]:
            raise ValueError("Session ID must be canonical UUID")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Never overwrite an earlier capture, even if a client reuses a session ID.
        capture_id = f"{session_id}-{uuid.uuid4().hex[:8]}"
        capture_path = self.output_dir / (capture_id + ".wav")
        wav = wave.open(str(capture_path), "wb") if self.capture else None
        if wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        report = {"session_id": session_id, "format": FORMAT, "rx_frames": 0, "tx_frames": 0,
                  "nonzero_samples": 0, "peak": 0, "sum_squares": 0,
                  "capture_path": str(capture_path) if wav else None, "reason": "disconnect",
                  "mode": "phase1" if self.responder else "phase0"}
        sock.sendall(encode({"type": "ready", "session_id": session_id}))
        print(json.dumps({"event": "session_open", "session_id": session_id}), flush=True)
        detector = TurnDetector() if self.responder else None
        active_response = None if self.responder else self.response
        response_text = ""
        response_source = "static"
        transcript = ""
        deadline = time.monotonic() + (1.0 if active_response is not None else 3600.0)
        last_rx = time.monotonic()
        offset = 0
        clean_close = False
        try:
            while not self.stop_event.is_set():
                if select.select([sock], [], [], 0.005)[0]:
                    message = read(sock)
                    if message.get("type") == "close" and message.get("session_id") == session_id:
                        report["reason"] = "closed"
                        clean_close = True
                        break
                    if message.get("type") != "audio":
                        raise ValueError("Unexpected client message")
                    data = audio_payload(message, session_id, report["rx_frames"])
                    report["rx_frames"] += 1
                    last_rx = time.monotonic()
                    if wav:
                        wav.writeframesraw(data)
                    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
                    report["nonzero_samples"] += sum(sample != 0 for sample in samples)
                    report["peak"] = max(report["peak"], max(abs(sample) for sample in samples))
                    report["sum_squares"] += sum(sample * sample for sample in samples)
                    if detector and active_response is None:
                        turn = detector.feed(data)
                        if turn is not None:
                            generated = self.responder.respond(turn)
                            active_response = generated.pcm
                            response_text = generated.text
                            response_source = generated.source
                            transcript = generated.transcript
                            deadline = time.monotonic()
                            print(json.dumps({"event": "phase1_response_ready", "session_id": session_id,
                                              "source": response_source, "text": response_text,
                                              "transcript": transcript,
                                              "frames": frame_count(active_response)}), flush=True)
                now = time.monotonic()
                if now - last_rx > 5:
                    report["reason"] = "audio_timeout"
                    break
                if active_response is not None and now >= deadline and offset < len(active_response):
                    frame = active_response[offset:offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
                    sock.sendall(encode(audio_message(session_id, report["tx_frames"], frame)))
                    report["tx_frames"] += 1
                    offset += FRAME_BYTES
                    deadline = max(deadline + 0.02, now - 0.02)
                    if offset >= len(active_response):
                        sock.sendall(encode({"type": "audio_end", "session_id": session_id,
                                             "frames": report["tx_frames"]}))
        finally:
            if wav:
                wav.close()
            samples = report["rx_frames"] * FRAME_BYTES // 2
            sum_squares = report.pop("sum_squares")
            report["rms"] = math.sqrt(sum_squares / samples) if samples else 0
            report["rx_seconds"] = report["rx_frames"] * 0.02
            report["bidirectional_pcm_observed"] = bool(report["nonzero_samples"] and report["tx_frames"])
            report["live_caller_hearing_verified"] = False
            if self.responder:
                report["phase1_transcript"] = transcript
                report["phase1_response_text"] = response_text
                report["phase1_response_source"] = response_source
            report_path = self.output_dir / (capture_id + ".json")
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            self.reports.append(report)
            print(json.dumps({"event": "session_closed", **report}), flush=True)
        if clean_close:
            sock.sendall(encode({"type": "closed", "session_id": session_id, "report": report}))
