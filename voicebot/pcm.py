"""Wire format and bounded callback buffers; no third-party dependencies."""
from __future__ import annotations

import math
import queue
import struct
import threading
import wave
from pathlib import Path

RATE = 16000
FRAME_MS = 20
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2
FORMAT = {"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1,
          "frame_ms": FRAME_MS, "frame_bytes": FRAME_BYTES}


def tone(seconds: float = 1.0, frequency: float = 440.0) -> bytes:
    return b"".join(struct.pack("<h", int(4000 * math.sin(2 * math.pi * frequency * i / RATE)))
                    for i in range(int(seconds * RATE)))


def wav_pcm(path: str | Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (RATE, 1, 2, "NONE"):
            raise ValueError("Test WAV must be uncompressed 16,000 Hz mono 16-bit PCM.")
        if wav.getnframes() > RATE * 30:
            raise ValueError("Test WAV must be at most 30 seconds.")
        return wav.readframes(wav.getnframes())


class AudioBuffers:
    """Media callbacks only copy bytes. Network and disk run elsewhere."""

    def __init__(self, capacity: int = 25, prefill_frames: int = 1):
        self.incoming: queue.Queue[bytes] = queue.Queue(capacity)
        self.outgoing: queue.Queue[bytes] = queue.Queue(capacity)
        self.active = True
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.rx_dropped = 0
        self.tx_dropped = 0
        self.underruns = 0
        self.callback_errors = 0
        self._tail = b""
        self._lock = threading.Lock()
        self._prefill = max(1, min(capacity, prefill_frames))
        self._playing = False
        self._output_complete = False

    def _offer(self, target: queue.Queue, data: bytes, counter: str):
        try:
            target.put_nowait(data)
        except queue.Full:
            # Drop newest rather than build unbounded latency.
            setattr(self, counter, getattr(self, counter) + 1)

    def receive(self, data: bytes):
        if self.active and data and len(data) % 2 == 0:
            self.rx_bytes += len(data)
            self._offer(self.incoming, data, "rx_dropped")

    def send(self, data: bytes):
        if self.active and data and len(data) % 2 == 0:
            self._offer(self.outgoing, data, "tx_dropped")

    def request(self, size: int) -> bytes:
        if size <= 0 or size > 8192 or size % 2:
            raise ValueError("Invalid PCM frame capacity")
        with self._lock:
            if not self.active:
                return bytes(size)
            if self._output_complete and self.outgoing.empty() and not self._tail:
                return bytes(size)
            if not self._playing:
                if self.outgoing.qsize() < self._prefill and not self._output_complete:
                    return bytes(size)
                self._playing = True
            while len(self._tail) < size:
                try:
                    self._tail += self.outgoing.get_nowait()
                except queue.Empty:
                    break
            result, self._tail = self._tail[:size], self._tail[size:]
            self.tx_bytes += len(result)
            if len(result) < size:
                self.underruns += 1
                self._playing = False
            return result.ljust(size, b"\0")

    def clear_output(self):
        with self._lock:
            self._tail = b""
            self._playing = False
            self._output_complete = False
            while True:
                try:
                    self.outgoing.get_nowait()
                except queue.Empty:
                    break

    def finish_output(self):
        """Drain a short final response even when it is smaller than prefill."""
        self._output_complete = True

    def close(self):
        self.active = False
        self.clear_output()

    def stats(self):
        return {key: getattr(self, key) for key in (
            "rx_bytes", "tx_bytes", "rx_dropped", "tx_dropped", "underruns", "callback_errors")}
