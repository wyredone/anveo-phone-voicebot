# Local audio contract v1

| Field | Value |
|---|---|
| Transport | TCP on 127.0.0.1 only; default 8765 |
| Authentication | Shared random 256-bit token, `.local/bridge.token` |
| Encoding | Signed PCM 16-bit little endian |
| Sample rate | 16,000 Hz |
| Channels | 1 |
| Wire frame | 20 ms / 320 samples / 640 bytes |
| Call identity | Per-attachment canonical UUID, not a reused SIP call slot |
| Ordering | Independent sequence numbers beginning at zero in each direction |
| Bounds | 16 KiB message maximum; 25-frame queues (500 ms) |

Each message is a network-order unsigned 32-bit payload length followed by UTF-8
JSON. An `open` includes version, session_id, token and exact format. The server
replies `ready` with the same session_id. `audio` carries session_id, seq and
base64 `pcm`. `audio_end` carries session_id and the total response frame count,
allowing a short final response to drain below the jitter prefill threshold.
A `close` requests session teardown; `closed` returns the capture report.
Reject wrong tokens, format, session, sequence, frame length and malformed messages.
The client does not reconnect a failed active call or send old audio into a new call.

PJSIP may deliver callback frames in different sizes. The client repackets incoming
PCM to 640-byte wire frames; the callback buffer can split or combine outbound frames.
The conference bridge resamples negotiated telephone codecs into the custom port's
16 kHz PCM format. Do not reinterpret PCMU/PCMA bytes as PCM.

All native media control runs on the Qt/main PJSIP thread, outside call callbacks.
Media callbacks only copy PCM into/from bounded queues. Network and file work stay
outside these callbacks. Caller recording is a separate stream from response audio.

Failure closes the output queue and produces silence until the host's next tick
releases the port. Native/media setup failures restore the previous manual call state.
Remote/local hold detaches media; resume attaches the new media object. F9 releases
ownership. A new F8 arm is required for another call after release/failure.

Later STT adapters should convert s16le to normalized float PCM explicitly, and
use independent persona/history/cancellation state per UUID. TTS should synthesize
into session-specific audio and feed this transport without PC speaker playback.

## Voice-Engine turn contract

Voice-Engine mode can call the local `wyres_voice_engine` package directly via
`VOICEBOT_ENGINE_DIR` and `VOICEBOT_ENGINE_CONFIG`. In local mode the bridge sends
caller PCM to `engine.stt.transcribe(...)`, sends the transcript to
`engine.brain.generate(...)`, then speaks the returned text through the bridge TTS.

HTTP mode posts each detected caller turn to `VOICEBOT_ENGINE_URL`. The request
uses JSON and includes a 16 kHz mono 16-bit WAV wrapped in base64:

```json
{
  "session_id": "call-session-uuid",
  "turn_index": 1,
  "persona": "front-desk",
  "audio": {
    "encoding": "wav_pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "data_base64": "..."
  }
}
```

If `VOICEBOT_ENGINE_API_KEY` is set, the bridge sends it as
`Authorization: Bearer ...`. The key is never printed in logs.

Expected response:

```json
{
  "transcript": "caller words",
  "response_text": "persona response",
  "response_audio_wav_base64": "optional 16-bit WAV audio"
}
```

When `response_audio_wav_base64` is present, the bridge plays that audio. When the
engine returns text only, local TTS speaks `response_text`. Reports use
`mode: voice_engine` and record each turn in `phase_interactions`.
