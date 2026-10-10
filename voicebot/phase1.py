from __future__ import annotations

import json
import math
import shutil
import subprocess
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from .pcm import FRAME_BYTES, RATE, tone


def _pcm_stats(pcm: bytes) -> dict[str, float | int]:
    if not pcm:
        return {"peak": 0, "rms": 0}
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm[:len(pcm) - len(pcm) % 2])
    if not samples:
        return {"peak": 0, "rms": 0}
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return {"peak": peak, "rms": rms}


class TurnDetector:
    def __init__(self, silence_seconds: float = 1.0, max_turn_seconds: float = 5.0,
                 min_speech_seconds: float = 0.25, rms_threshold: int = 12, peak_threshold: int = 120):
        self.silence_frames = max(1, int(silence_seconds / 0.02))
        self.max_frames = max(1, int(max_turn_seconds / 0.02))
        self.min_speech_frames = max(1, int(min_speech_seconds / 0.02))
        self.rms_threshold = rms_threshold
        self.peak_threshold = peak_threshold
        self.frames: list[bytes] = []
        self.speech_frames = 0
        self.trailing_silence = 0
        self.started = False

    def feed(self, pcm: bytes) -> bytes | None:
        stats = _pcm_stats(pcm)
        voiced = stats["rms"] >= self.rms_threshold or stats["peak"] >= self.peak_threshold
        if voiced:
            self.started = True
            self.speech_frames += 1
            self.trailing_silence = 0
        elif self.started:
            self.trailing_silence += 1
        if self.started:
            self.frames.append(pcm)
        if not self.started:
            return None
        enough_speech = self.speech_frames >= self.min_speech_frames
        ended_by_silence = enough_speech and self.trailing_silence >= self.silence_frames
        ended_by_limit = len(self.frames) >= self.max_frames
        if ended_by_silence or ended_by_limit:
            turn = b"".join(self.frames)
            self.reset()
            return turn
        return None

    def reset(self):
        self.frames.clear()
        self.speech_frames = 0
        self.trailing_silence = 0
        self.started = False


def wav_to_bridge_pcm(path: str | Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        if width != 2:
            raise ValueError("Piper WAV must be 16-bit PCM")
        data = wav.readframes(wav.getnframes())
    if channels > 1:
        unpacked = struct.unpack("<" + "h" * (len(data) // 2), data)
        mono = []
        for i in range(0, len(unpacked), channels):
            group = unpacked[i:i + channels]
            mono.append(int(sum(group) / len(group)))
        data = struct.pack("<" + "h" * len(mono), *mono)
    if rate != RATE:
        samples = struct.unpack("<" + "h" * (len(data) // 2), data)
        if not samples:
            return b""
        out_len = max(1, int(len(samples) * RATE / rate))
        converted = []
        for index in range(out_len):
            src = index * rate / RATE
            lo = int(src)
            hi = min(lo + 1, len(samples) - 1)
            frac = src - lo
            converted.append(int(samples[lo] * (1 - frac) + samples[hi] * frac))
        data = struct.pack("<" + "h" * len(converted), *converted)
    return data


def fallback_response_pcm() -> bytes:
    return tone(0.28, 660) + tone(0.12, 0) + tone(0.28, 880) + tone(0.12, 0) + tone(0.45, 660)


@dataclass
class VoiceResponse:
    pcm: bytes
    text: str
    source: str
    transcript: str


class Phase1Responder:
    def __init__(self, response_text: str | None = None, piper_model: str | None = None,
                 piper_config: str | None = None, piper_exe: str = "piper"):
        self.response_text = response_text or "Hello. Phase one is connected. I heard you through the phone bridge."
        self.piper_model = piper_model
        self.piper_config = piper_config
        self.piper_exe = piper_exe

    def respond(self, caller_pcm: bytes) -> VoiceResponse:
        stats = _pcm_stats(caller_pcm)
        transcript = f"caller audio detected, {len(caller_pcm) / (RATE * 2):.2f} seconds, rms {stats['rms']}"
        text = self.response_text
        source = "fallback_tone"
        pcm = fallback_response_pcm()
        try:
            synthesized = self._piper(text)
            if synthesized:
                pcm = synthesized
                source = "piper"
        except Exception as exc:
            source = "fallback_tone_after_tts_error"
            print(json.dumps({"event": "tts_fallback", "error": str(exc)}), flush=True)
        return VoiceResponse(pcm=pcm, text=text, source=source, transcript=transcript)

    def _piper(self, text: str) -> bytes | None:
        if not self.piper_model:
            return None
        model = Path(self.piper_model)
        if not model.is_file():
            raise ValueError(f"Piper model not found: {model}")
        exe = shutil.which(self.piper_exe) or self.piper_exe
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "phase1-response.wav"
            command = [exe, "--model", str(model), "--output_file", str(output)]
            if self.piper_config:
                command.extend(["--config", self.piper_config])
            proc = subprocess.run(command, input=text, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "Piper failed")
            pcm = wav_to_bridge_pcm(output)
            if not pcm:
                raise RuntimeError("Piper produced empty audio")
            return pcm


def frame_count(pcm: bytes) -> int:
    return math.ceil(len(pcm) / FRAME_BYTES)
