from __future__ import annotations

import json
import math
import sys
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
                 piper_config: str | None = None, piper_exe: str = "piper",
                 tts_backend: str = "auto", sapi_voice: str | None = None,
                 sapi_rate: int = 0, sapi_volume: int = 100):
        if tts_backend not in {"auto", "piper", "sapi", "tone"}:
            raise ValueError("tts_backend must be one of: auto, piper, sapi, tone")
        self.response_text = response_text or "Hello. Phase one is connected. I heard you through the phone bridge."
        self.piper_model = piper_model
        self.piper_config = piper_config
        self.piper_exe = piper_exe
        self.tts_backend = tts_backend
        self.sapi_voice = sapi_voice
        self.sapi_rate = max(-10, min(10, sapi_rate))
        self.sapi_volume = max(0, min(100, sapi_volume))

    def respond(self, caller_pcm: bytes) -> VoiceResponse:
        stats = _pcm_stats(caller_pcm)
        transcript = f"caller audio detected, {len(caller_pcm) / (RATE * 2):.2f} seconds, rms {stats['rms']}"
        text = self.response_text
        source = "fallback_tone"
        pcm = fallback_response_pcm()
        errors = []
        for backend in self._backend_order():
            try:
                synthesized = self._synthesize(backend, text)
                if synthesized:
                    return VoiceResponse(pcm=synthesized, text=text, source=backend, transcript=transcript)
            except Exception as exc:
                errors.append({"backend": backend, "error": str(exc)})
        if errors:
            source = "fallback_tone_after_tts_error"
            print(json.dumps({"event": "tts_fallback", "errors": errors}), flush=True)
        return VoiceResponse(pcm=pcm, text=text, source=source, transcript=transcript)

    def _backend_order(self) -> list[str]:
        if self.tts_backend == "tone":
            return []
        if self.tts_backend == "piper":
            return ["piper"]
        if self.tts_backend == "sapi":
            return ["sapi"]
        order = []
        if self.piper_model:
            order.append("piper")
        if sys.platform == "win32":
            order.append("sapi")
        return order

    def _synthesize(self, backend: str, text: str) -> bytes | None:
        if backend == "piper":
            return self._piper(text)
        if backend == "sapi":
            return self._sapi(text)
        raise ValueError(f"Unknown TTS backend: {backend}")

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

    def _sapi(self, text: str) -> bytes | None:
        if sys.platform != "win32":
            return None
        exe = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "phase1-response.wav"
            script = Path(folder) / "sapi.ps1"
            script.write_text(_SAPI_SCRIPT, encoding="utf-8")
            command = [
                exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-Text", text, "-Output", str(output), "-Rate", str(self.sapi_rate),
                "-Volume", str(self.sapi_volume),
            ]
            if self.sapi_voice:
                command.extend(["-Voice", self.sapi_voice])
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "SAPI failed")
            pcm = wav_to_bridge_pcm(output)
            if not pcm:
                raise RuntimeError("SAPI produced empty audio")
            return pcm


def frame_count(pcm: bytes) -> int:
    return math.ceil(len(pcm) / FRAME_BYTES)


_SAPI_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$Text,
    [Parameter(Mandatory=$true)][string]$Output,
    [int]$Rate = 0,
    [int]$Volume = 100,
    [string]$Voice = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice -ne "") {
        $synth.SelectVoice($Voice)
    }
    $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
    $synth.Volume = [Math]::Max(0, [Math]::Min(100, $Volume))
    $synth.SetOutputToWaveFile($Output)
    $synth.Speak($Text)
}
finally {
    $synth.Dispose()
}
"""
