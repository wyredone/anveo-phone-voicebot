from __future__ import annotations

import json
import math
import sys
import shutil
import subprocess
import struct
import tempfile
import wave
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from .engine_api import LocalVoiceEngineClient, VoiceEngineClient
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


def _pcm_samples(pcm: bytes) -> tuple[int, ...]:
    return struct.unpack("<" + "h" * (len(pcm) // 2), pcm[:len(pcm) - len(pcm) % 2])


def prepare_stt_pcm(pcm: bytes, rms_threshold: int = 80, peak_threshold: int = 800,
                    pad_frames: int = 10, target_peak: int = 14000) -> bytes:
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)
              if len(pcm[i:i + FRAME_BYTES]) == FRAME_BYTES]
    voiced = []
    for index, frame in enumerate(frames):
        stats = _pcm_stats(frame)
        if stats["rms"] >= rms_threshold or stats["peak"] >= peak_threshold:
            voiced.append(index)
    if voiced:
        start = max(0, voiced[0] - pad_frames)
        end = min(len(frames), voiced[-1] + pad_frames + 1)
        pcm = b"".join(frames[start:end])
    samples = _pcm_samples(pcm)
    if not samples:
        return b""
    peak = max(abs(sample) for sample in samples)
    if peak <= 0:
        return pcm
    scale = min(8.0, target_peak / peak)
    if scale <= 1.05:
        return pcm
    normalized = [max(-32768, min(32767, int(sample * scale))) for sample in samples]
    return struct.pack("<" + "h" * len(normalized), *normalized)


class TurnDetector:
    def __init__(self, silence_seconds: float = 1.0, max_turn_seconds: float = 5.0,
                 min_speech_seconds: float = 0.5, rms_threshold: int = 100, peak_threshold: int = 1000):
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


def _wav_to_bridge_pcm(wav) -> bytes:
    channels = wav.getnchannels()
    width = wav.getsampwidth()
    rate = wav.getframerate()
    if width != 2:
        raise ValueError("WAV must be 16-bit PCM")
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


def wav_to_bridge_pcm(path: str | Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        return _wav_to_bridge_pcm(wav)


def wav_bytes_to_bridge_pcm(data: bytes) -> bytes:
    with wave.open(BytesIO(data), "rb") as wav:
        return _wav_to_bridge_pcm(wav)


def fallback_response_pcm() -> bytes:
    return tone(0.28, 660) + tone(0.12, 0) + tone(0.28, 880) + tone(0.12, 0) + tone(0.45, 660)


@dataclass
class VoiceResponse:
    pcm: bytes
    text: str
    source: str
    transcript: str
    transcript_source: str
    transcript_errors: list[dict[str, str]]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class PreparedAudio:
    pcm: bytes
    source: str
    errors: list[dict[str, str]]


@dataclass
class Transcript:
    text: str
    source: str
    errors: list[dict[str, str]]


class AppointmentDialog:
    def __init__(self):
        self.reset()

    def reset(self):
        self.stage = "opening"
        self.intent = ""
        self.caller_name = ""
        self.requested_time = ""
        self.notes = ""
        self.turns: list[dict[str, str]] = []

    def handle(self, transcript: str) -> tuple[str, dict[str, object]]:
        clean = " ".join(transcript.split())
        lowered = clean.lower()
        if not clean:
            text = "I heard audio, but I could not recognize the words yet. Please say your name and the reason for your call."
            return text, self.summary()

        if self._is_appointment(lowered):
            self.intent = "appointment"
        if not self.caller_name:
            self.caller_name = self._extract_name(clean)
        if not self.requested_time:
            self.requested_time = self._extract_time(clean)
        if not self.notes and self._is_appointment(lowered):
            self.notes = clean

        if self.stage == "opening":
            if self.intent == "appointment":
                if not self.caller_name:
                    self.stage = "collect_name"
                    text = "Sure, I can help with a dental appointment request. What name should I put on it?"
                elif not self.requested_time:
                    self.stage = "collect_time"
                    text = f"Thanks {self.caller_name}. What day and time would work best for the dental appointment?"
                else:
                    self.stage = "confirm"
                    text = self._confirmation()
            elif self._is_greeting(lowered):
                self.stage = "collect_reason"
                text = "Hello. This is the Anveo voice bot test line. Please say your name and how I can help."
            else:
                self.stage = "collect_reason"
                text = "Thanks. I can help collect an appointment request, message, or hours question. What do you need today?"
        elif self.stage == "collect_reason":
            if self.intent == "appointment":
                if not self.caller_name:
                    self.stage = "collect_name"
                    text = "Sure, I can help with a dental appointment request. What name should I put on it?"
                elif not self.requested_time:
                    self.stage = "collect_time"
                    text = f"Thanks {self.caller_name}. What day and time would work best?"
                else:
                    self.stage = "confirm"
                    text = self._confirmation()
            else:
                text = "I captured that. For this Phase three test, please try saying that you need a dental appointment."
        elif self.stage == "collect_name":
            if not self.caller_name:
                self.caller_name = clean
            if not self.requested_time:
                self.stage = "collect_time"
                text = f"Thanks {self.caller_name}. What day and time would work best?"
            else:
                self.stage = "confirm"
                text = self._confirmation()
        elif self.stage == "collect_time":
            if not self.requested_time:
                self.requested_time = clean
            self.stage = "confirm"
            text = self._confirmation()
        else:
            text = "Thanks. I already have the appointment request summary for this Phase three test."

        self.turns.append({"transcript": clean, "response": text, "stage": self.stage})
        return text, self.summary()

    def summary(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "intent": self.intent,
            "caller_name": self.caller_name,
            "requested_time": self.requested_time,
            "notes": self.notes,
            "turn_count": len(self.turns),
        }

    def _confirmation(self) -> str:
        name = self.caller_name or "the caller"
        when = self.requested_time or "the requested time"
        return f"Got it. I have an appointment request for {name}, preferred time {when}. A future phase will send this to the office."

    @staticmethod
    def _is_greeting(text: str) -> bool:
        return any(word in text for word in ("hello", "hi", "hey", "aloe"))

    @staticmethod
    def _is_appointment(text: str) -> bool:
        return any(word in text for word in ("appointment", "schedule", "booking", "book", "dentist", "dental"))

    @staticmethod
    def _extract_name(text: str) -> str:
        lowered = text.lower()
        markers = ("my name is ", "this is ", "i am ", "i'm ")
        for marker in markers:
            index = lowered.find(marker)
            if index >= 0:
                candidate = text[index + len(marker):].strip(" .,!?:;")
                lower_candidate = candidate.lower()
                stops = [lower_candidate.find(stop) for stop in (" and ", " i need", " need ", " calling ") if lower_candidate.find(stop) >= 0]
                if stops:
                    candidate = candidate[:min(stops)].strip(" .,!?:;")
                words = candidate.split()
                if words:
                    return " ".join(words[:3])
        return ""

    @staticmethod
    def _extract_time(text: str) -> str:
        lowered = text.lower()
        words = text.split()
        day_words = {"today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday",
                     "saturday", "sunday", "morning", "afternoon", "evening", "noon"}
        for word in words:
            cleaned = word.lower().strip(".,!?")
            if cleaned in day_words or any(char.isdigit() for char in cleaned):
                return " ".join(words[max(0, words.index(word) - 1):min(len(words), words.index(word) + 4)])
        return ""


class Phase1Responder:
    def __init__(self, response_text: str | None = None, piper_model: str | None = None,
                 piper_config: str | None = None, piper_exe: str = "piper",
                 tts_backend: str = "auto", sapi_voice: str | None = None,
                 sapi_rate: int = 0, sapi_volume: int = 100, tts_timeout: float = 8.0,
                 stt_backend: str = "auto", stt_timeout: float = 8.0,
                 response_mode: str = "fixed", voice_engine_url: str = "",
                 voice_engine_api_key: str = "", voice_engine_persona: str = "",
                 voice_engine_timeout: float = 20.0, voice_engine_dir: str = "",
                 voice_engine_config: str = ""):
        if tts_backend not in {"auto", "piper", "sapi", "tone"}:
            raise ValueError("tts_backend must be one of: auto, piper, sapi, tone")
        if stt_backend not in {"auto", "sapi", "off"}:
            raise ValueError("stt_backend must be one of: auto, sapi, off")
        if response_mode not in {"fixed", "echo", "rules", "appointment", "engine"}:
            raise ValueError("response_mode must be one of: fixed, echo, rules, appointment, engine")
        self.response_text = response_text or "Hello. Phase one is connected. I heard you through the phone bridge."
        self.piper_model = piper_model
        self.piper_config = piper_config
        self.piper_exe = piper_exe
        self.tts_backend = tts_backend
        self.sapi_voice = sapi_voice
        self.sapi_rate = max(-10, min(10, sapi_rate))
        self.sapi_volume = max(0, min(100, sapi_volume))
        self.tts_timeout = max(1.0, tts_timeout)
        self.stt_backend = stt_backend
        self.stt_timeout = max(1.0, stt_timeout)
        self.response_mode = response_mode
        self._prepared: PreparedAudio | None = None
        self.appointment_dialog = AppointmentDialog()
        self.session_id = ""
        self.turn_index = 0
        if voice_engine_url:
            self.voice_engine = VoiceEngineClient(voice_engine_url, voice_engine_api_key,
                                                  voice_engine_persona, voice_engine_timeout)
        elif voice_engine_dir or voice_engine_config:
            self.voice_engine = LocalVoiceEngineClient(voice_engine_dir, voice_engine_config,
                                                       voice_engine_persona)
        else:
            self.voice_engine = None
        self.voice_engine_persona = voice_engine_persona

    def start_session(self, session_id: str):
        self.session_id = session_id
        self.turn_index = 0
        self.appointment_dialog.reset()

    def prepare(self) -> PreparedAudio:
        if self._prepared:
            return self._prepared
        self._prepared = self._build_response_audio()
        event = {
            "event": "phase1_response_prepared",
            "source": self._prepared.source,
            "frames": frame_count(self._prepared.pcm),
            "seconds": round(len(self._prepared.pcm) / (RATE * 2), 3),
        }
        if self._prepared.errors:
            event["errors"] = self._prepared.errors
        print(json.dumps(event), flush=True)
        return self._prepared

    def respond(self, caller_pcm: bytes) -> VoiceResponse:
        if self.response_mode == "engine":
            return self._voice_engine_response(caller_pcm)
        stats = _pcm_stats(caller_pcm)
        signal_summary = f"caller audio detected, {len(caller_pcm) / (RATE * 2):.2f} seconds, rms {stats['rms']}"
        transcript = self.transcribe(caller_pcm)
        transcript_text = transcript.text or signal_summary
        if self.response_mode == "fixed":
            response_text = self.response_text
            prepared = self.prepare()
        else:
            metadata: dict[str, object] = {}
            if self.response_mode == "rules":
                response_text = self._rules_response_text(transcript.text)
            elif self.response_mode == "appointment":
                response_text, metadata = self.appointment_dialog.handle(transcript.text)
            else:
                response_text = self._echo_response_text(transcript.text)
            prepared = self._build_response_audio(response_text)
            event_name = "phase3_appointment_response_prepared" if self.response_mode == "appointment" else "phase2_dynamic_response_prepared"
            event = {"event": event_name,
                              "source": prepared.source,
                              "frames": frame_count(prepared.pcm),
                              "seconds": round(len(prepared.pcm) / (RATE * 2), 3),
                              "text": response_text}
            if metadata:
                event["appointment"] = metadata
            print(json.dumps(event), flush=True)
        return VoiceResponse(pcm=prepared.pcm, text=response_text,
                             source=prepared.source, transcript=transcript_text,
                             transcript_source=transcript.source,
                             transcript_errors=transcript.errors,
                             metadata=metadata if self.response_mode == "appointment" else {})

    def _voice_engine_response(self, caller_pcm: bytes) -> VoiceResponse:
        self.turn_index += 1
        errors = []
        metadata: dict[str, object] = {"persona": self.voice_engine_persona,
                                       "turn_index": self.turn_index}
        if self.voice_engine:
            try:
                result = self.voice_engine.respond(caller_pcm, self.session_id, self.turn_index)
                response_text = result.response_text or "I heard you. The voice engine returned no response text."
                if result.response_audio_wav:
                    prepared = PreparedAudio(wav_bytes_to_bridge_pcm(result.response_audio_wav),
                                             "voice_engine_audio", [])
                else:
                    prepared = self._build_response_audio(response_text)
                    prepared = PreparedAudio(prepared.pcm, f"voice_engine_text_{prepared.source}", prepared.errors)
                engine_backend = str(result.raw.get("backend") or "voice_engine_http")
                metadata.update({"engine": engine_backend,
                                 "raw_keys": sorted(str(key) for key in result.raw.keys())})
                print(json.dumps({"event": "voice_engine_response_prepared",
                                  "source": prepared.source,
                                  "frames": frame_count(prepared.pcm),
                                  "seconds": round(len(prepared.pcm) / (RATE * 2), 3),
                                  "text": response_text,
                                  "transcript": result.transcript,
                                  "persona": self.voice_engine_persona}), flush=True)
                return VoiceResponse(pcm=prepared.pcm, text=response_text,
                                     source=prepared.source,
                                     transcript=result.transcript,
                                     transcript_source="voice_engine" if result.transcript else "voice_engine_none",
                                     transcript_errors=prepared.errors,
                                     metadata=metadata)
            except Exception as exc:
                errors.append({"backend": "voice_engine", "error": str(exc)})
                print(json.dumps({"event": "voice_engine_fallback", "errors": errors,
                                  "persona": self.voice_engine_persona}), flush=True)
        else:
            errors.append({"backend": "voice_engine", "error": "VOICEBOT_ENGINE_URL is not configured"})
        response_text = "The voice engine is not available yet. I am falling back to the local appointment test."
        prepared = self._build_response_audio(response_text)
        metadata.update({"engine": "voice_engine_http", "fallback": True})
        return VoiceResponse(pcm=prepared.pcm, text=response_text,
                             source=f"voice_engine_fallback_{prepared.source}",
                             transcript="", transcript_source="voice_engine_error",
                             transcript_errors=errors + prepared.errors,
                             metadata=metadata)

    def _echo_response_text(self, transcript: str) -> str:
        clean = " ".join(transcript.split())
        if not clean:
            return "I heard audio, but I could not recognize the words yet. Phase two speech recognition is connected."
        if len(clean) > 160:
            clean = clean[:157].rstrip() + "..."
        return f"I heard you say: {clean}. Phase two speech recognition is connected."

    def _rules_response_text(self, transcript: str) -> str:
        clean = " ".join(transcript.lower().split())
        if not clean:
            return "I heard audio, but I could not recognize the words yet. Please say your name and the reason for your call."
        if any(word in clean for word in ("appointment", "schedule", "booking", "book")):
            return "I heard that you are asking about scheduling. Please leave your name, phone number, and the appointment details."
        if any(word in clean for word in ("message", "voicemail", "call me", "callback", "call back")):
            return "I can take a message. Please say your name, phone number, and the message after the tone in a future phase."
        if any(word in clean for word in ("hours", "open", "close", "closed")):
            return "I heard a question about hours. Business-hours lookup is not connected yet, but the intent was detected."
        if any(word in clean for word in ("hello", "hi", "hey", "aloe")):
            return "Hello. This is the Anveo voice bot test line. Please say your name and how I can help."
        if len(clean) > 140:
            clean = clean[:137].rstrip() + "..."
        return f"I heard you say: {clean}. I do not have a business rule for that yet, but recognition is working."

    def transcribe(self, caller_pcm: bytes) -> Transcript:
        errors = []
        for backend in self._stt_backend_order():
            try:
                text = self._recognize(backend, caller_pcm)
                if text:
                    return Transcript(text=text, source=backend, errors=errors)
            except Exception as exc:
                errors.append({"backend": backend, "error": str(exc)})
        if errors:
            print(json.dumps({"event": "stt_fallback", "errors": errors}), flush=True)
        return Transcript(text="", source="none", errors=errors)

    def _build_response_audio(self, text: str | None = None) -> PreparedAudio:
        source = "fallback_tone"
        pcm = fallback_response_pcm()
        errors = []
        text = self.response_text if text is None else text
        for backend in self._backend_order():
            try:
                synthesized = self._synthesize(backend, text)
                if synthesized:
                    return PreparedAudio(pcm=synthesized, source=backend, errors=errors)
            except Exception as exc:
                errors.append({"backend": backend, "error": str(exc)})
        if errors:
            source = "fallback_tone_after_tts_error"
            print(json.dumps({"event": "tts_fallback", "errors": errors}), flush=True)
        return PreparedAudio(pcm=pcm, source=source, errors=errors)

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

    def _stt_backend_order(self) -> list[str]:
        if self.stt_backend == "off":
            return []
        if self.stt_backend == "sapi":
            return ["sapi"]
        return ["sapi"] if sys.platform == "win32" else []

    def _synthesize(self, backend: str, text: str) -> bytes | None:
        if backend == "piper":
            return self._piper(text)
        if backend == "sapi":
            return self._sapi(text)
        raise ValueError(f"Unknown TTS backend: {backend}")

    def _recognize(self, backend: str, caller_pcm: bytes) -> str | None:
        if backend == "sapi":
            return self._sapi_recognize(caller_pcm)
        raise ValueError(f"Unknown STT backend: {backend}")

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
                                  stderr=subprocess.PIPE, timeout=self.tts_timeout)
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
                                  text=True, timeout=self.tts_timeout)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "SAPI failed")
            pcm = wav_to_bridge_pcm(output)
            if not pcm:
                raise RuntimeError("SAPI produced empty audio")
            return pcm

    def _sapi_recognize(self, caller_pcm: bytes) -> str | None:
        if sys.platform != "win32":
            return None
        exe = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        stt_pcm = prepare_stt_pcm(caller_pcm)
        with tempfile.TemporaryDirectory() as folder:
            wav_path = Path(folder) / "caller-turn.wav"
            script = Path(folder) / "sapi-recognize.ps1"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                wav.writeframes(stt_pcm)
            script.write_text(_SAPI_RECOGNITION_SCRIPT, encoding="utf-8")
            command = [
                exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-WavPath", str(wav_path),
            ]
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, timeout=self.stt_timeout)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "SAPI recognition failed")
            data = json.loads(proc.stdout or "{}")
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            text = str(data.get("text") or "").strip()
            if not text:
                stats = _pcm_stats(stt_pcm)
                print(json.dumps({"event": "stt_no_match", "source": "sapi",
                                  "confidence": data.get("confidence", 0.0),
                                  "seconds": round(len(stt_pcm) / (RATE * 2), 3),
                                  "rms": stats["rms"], "peak": stats["peak"]}), flush=True)
                return None
            return text


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


_SAPI_RECOGNITION_SCRIPT = r"""
param(
    [Parameter(Mandatory=$true)][string]$WavPath
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
try {
    $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $engine.SetInputToWaveFile($WavPath)
    $result = $engine.Recognize()
    if ($null -eq $result) {
        @{ text = ""; confidence = 0.0 } | ConvertTo-Json -Compress
    } else {
        @{ text = $result.Text; confidence = $result.Confidence } | ConvertTo-Json -Compress
    }
}
catch {
    @{ text = ""; confidence = 0.0; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
finally {
    $engine.Dispose()
}
"""
