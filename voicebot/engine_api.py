from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .pcm import RATE


@dataclass
class VoiceEngineResult:
    transcript: str
    response_text: str
    response_audio_wav: bytes
    raw: dict[str, object]


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return buffer.getvalue()


class VoiceEngineClient:
    def __init__(self, url: str, api_key: str = "", persona: str = "", timeout: float = 20.0):
        self.url = url
        self.api_key = api_key
        self.persona = persona
        self.timeout = max(1.0, timeout)

    def respond(self, caller_pcm: bytes, session_id: str, turn_index: int) -> VoiceEngineResult:
        wav_bytes = pcm_to_wav_bytes(caller_pcm)
        payload = {
            "session_id": session_id,
            "turn_index": turn_index,
            "persona": self.persona,
            "audio": {
                "encoding": "wav_pcm_s16le",
                "sample_rate": RATE,
                "channels": 1,
                "data_base64": base64.b64encode(wav_bytes).decode("ascii"),
            },
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"Voice Engine HTTP {exc.code}: {detail}") from exc
        data = json.loads(body.decode("utf-8") or "{}")
        audio = data.get("response_audio_wav_base64") or data.get("audio_wav_base64")
        nested_audio = data.get("audio")
        if not audio and isinstance(nested_audio, dict):
            audio = nested_audio.get("data_base64")
        return VoiceEngineResult(
            transcript=str(data.get("transcript") or data.get("recognized_text") or "").strip(),
            response_text=str(data.get("response_text") or data.get("text") or data.get("message") or "").strip(),
            response_audio_wav=base64.b64decode(str(audio)) if audio else b"",
            raw=data,
        )


class LocalVoiceEngineClient:
    def __init__(self, source_dir: str = "", config_path: str = "", persona: str = ""):
        self.source_dir = source_dir
        self.config_path = config_path
        self.persona = persona
        self._engine = None

    def respond(self, caller_pcm: bytes, session_id: str, turn_index: int) -> VoiceEngineResult:
        engine = self._load_engine()
        audio = self._pcm_to_float32(caller_pcm)
        result = engine.stt.transcribe(audio, RATE)
        transcript = str(getattr(result, "text", "") or "").strip()
        response_text = engine.brain.generate(transcript) if transcript else ""
        return VoiceEngineResult(
            transcript=transcript,
            response_text=response_text,
            response_audio_wav=b"",
            raw={
                "backend": "local_voice_engine",
                "persona": self.persona,
                "session_id": session_id,
                "turn_index": turn_index,
                "config_path": self.config_path,
            },
        )

    def _load_engine(self):
        if self._engine is not None:
            return self._engine
        if self.source_dir:
            source = Path(self.source_dir)
            if source.name == "wyres_voice_engine":
                source = source.parent
            sys.path.insert(0, str(source))
        from wyres_voice_engine import VoiceEngineController
        self._engine = VoiceEngineController.from_config(self.config_path or None)
        return self._engine

    @staticmethod
    def _pcm_to_float32(caller_pcm: bytes):
        try:
            import numpy as np
        except Exception as exc:
            raise RuntimeError(f"numpy unavailable for local Voice-Engine audio conversion: {exc}") from exc
        samples = np.frombuffer(caller_pcm, dtype="<i2").astype("float32")
        return samples / 32768.0
