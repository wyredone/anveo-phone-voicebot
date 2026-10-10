# Anveo Phone VoiceBot

Standalone, audio-only proof of concept for connecting AnveoPhone's live call media
to a separate localhost process. Voice-Engine personas, STT, AI and TTS are planned
for later phases. This milestone sends a test tone or supplied WAV and captures
incoming caller PCM.

Phase 1 has started as an opt-in response loop: after the caller speaks and pauses,
the receiver generates return audio and sends it through the same live PCM bridge.
It can use Piper when configured, Windows SAPI speech by default on Windows, or a
distinct fallback tone pattern. Phase 3 adds a local appointment test flow that
can collect a simple dental appointment request over multiple turns.

## Windows quick start

HTML guides:

1. [QuickStart](docs/QUICKSTART.html)
2. [User Guide](docs/USER_GUIDE.html)

Menu launcher:

1. `launch_app.bat`
2. Optional first run with paths: `launch_app.bat "F:\anveophone" "C:\path\to\python.exe"`
3. Choose **A** for the normal Voice-Engine call test, **B** for the local
   appointment fallback, **C** for diagnostics, **D** for settings, or **X** for
   the older phase/debug tools.

1. Download/extract this repository. Keep the complete folder together.
2. Close any running AnveoPhone instance. Use its working Python interpreter;
   the native `_pjsua2.pyd` must match that interpreter and its DLL dependencies.
3. In normal AnveoPhone settings, disable **Auto-answer** before this test.
4. Open a terminal in this project and run `launch_phase0.bat`. The default
   AnveoPhone folder is `E:\anveophone`; pass a different folder as the first
   argument only if needed. Supply its Python as a second argument if needed:
   `launch_phase0.bat "E:\anveophone" "C:\path\to\python.exe"`.
5. Call your Anveo number from a test phone and **answer manually** in AnveoPhone.
   Speak for at least five seconds. The caller should hear a one-second 440 Hz tone.
6. Hang up. Inspect `artifacts/calls/*.wav` and the corresponding JSON report.
   **F8** arms another test call; **F9** releases the bridge and restores manual audio.
7. Close both test windows when finished. Start normal AnveoPhone normally afterward.

The launcher's default interpreter selection follows AnveoPhone's MSYS2 preference,
then `python`. If your normal launcher uses `py -3`, supply the actual executable
printed by that interpreter's `sys.executable`. No dependencies are installed automatically;
Phase 0 itself uses the Python standard library. AnveoPhone supplies Qt and PJSUA2.

## Separate commands

1. `python -m voicebot doctor --anveophone "F:\anveophone" --voice-engine "F:\Voice-Engine"`
2. `python -m voicebot serve`
3. In a second terminal: `python -m voicebot host --anveophone "F:\anveophone" --arm-next-call`

Use the **same token file and port** for both commands. Defaults share this checkout's
`.local/bridge.token` and loopback TCP port 8765. If port 8765 is occupied, pass
`--port 8766` to both. Without `--arm-next-call`, the adapter stays inactive until F8.
An outgoing call is never claimed. No incoming call is answered by VoiceBot.

For an audible spoken response, supply your own uncompressed 16 kHz, mono, 16-bit
WAV (maximum 30 seconds): `python -m voicebot serve --wav "F:\test_response.wav"`.
This can be a Voice-Engine-generated file converted to the required format.
It is a static response, not AI conversation.

Test captures contain caller audio. Use a test call and a tester aware of capture.
Use `serve --no-capture` to keep only signal statistics instead of WAV files.

## Phase 1 response loop

Run from the menu under **X** Advanced Tools, or directly:

1. `launch_phase1.bat`
2. Answer one incoming test call manually.
3. Speak for at least a moment, then pause for about one second.
4. Listen for the generated response. On Windows it should use SAPI speech by
   default. With Piper configured, Piper is used first. If TTS fails, it falls
   back to the tone pattern.

The Phase 1 server now prepares response audio before it listens for a call. Look
for `phase1_response_prepared` in the server window. `source: sapi` means Windows
speech is ready. `fallback_tone_after_tts_error` means it will play the tone
pattern and the same line includes the TTS error.

Optional Piper setup before launching:

```bat
set VOICEBOT_PIPER_MODEL=C:\path\to\voice.onnx
set VOICEBOT_PIPER_CONFIG=C:\path\to\voice.onnx.json
launch_phase1.bat
```

Optional Windows SAPI voice selection:

```bat
set VOICEBOT_SAPI_VOICE=Microsoft Zira Desktop
launch_phase1.bat
```

The matching advanced server command is:

```bat
python -m voicebot serve-ai --response-text "Hello. Phase one is connected."
```

Per-call JSON reports include `mode: phase1`, a placeholder transcript summary,
the response text, and the response source (`piper`, `fallback_tone`, or
`fallback_tone_after_tts_error`). On Windows, a successful built-in spoken response
reports `phase1_response_source: sapi`.

Phase 2 starts caller speech recognition. With `--stt-backend auto`, Windows uses
SAPI dictation recognition on the caller turn. Reports include
`phase1_transcript_source`; `sapi` means recognized words were logged, while `none`
means recognition returned nothing or failed and `phase1_transcript_errors` has
details. The launcher now runs `--response-mode rules`, so the spoken response uses
simple receptionist-style rules for greetings, scheduling, messages and hours. Use
`--response-mode echo` to repeat recognized caller text, or `--response-mode fixed`
to keep the known static phrase.

## Phase 3 appointment test

Run from the menu with **B**, or directly:

1. `launch_phase3.bat`
2. Answer one incoming test call manually.
3. Say hello, ask for a dental appointment, then answer the follow-up prompts.
4. Hang up and inspect `artifacts/calls/*.json`.

The report includes `mode: phase3`, `phase_interactions`, and an
`appointment_summary` with the detected intent, caller name when captured, requested
time when captured, notes, and current dialogue stage.

## Voice-Engine test

This path sends the caller turn to Voice-Engine so the selected persona/config can
own STT and AI response generation. It can use the local `wyres_voice_engine`
package directly, or an HTTP endpoint if you wrap Voice-Engine as a service.

Local package mode defaults to `E:\Voice-Engine-main` and
`E:\Voice-Engine-main\config.json`, so the normal menu path is:

```bat
launch_app.bat
```

Choose **A**. To override from the command line:

```bat
set VOICEBOT_ENGINE_DIR=E:\Voice-Engine-main
set VOICEBOT_ENGINE_CONFIG=E:\Voice-Engine-main\config.json
set VOICEBOT_ENGINE_PERSONA=front-desk
launch_voice_engine.bat
```

HTTP service mode:

```bat
set VOICEBOT_ENGINE_URL=http://127.0.0.1:9000/turn
set VOICEBOT_ENGINE_API_KEY=your-key-if-needed
set VOICEBOT_ENGINE_PERSONA=front-desk
launch_voice_engine.bat
```

The menu launcher exposes the same flow as **A**. API keys are read from the
environment and are not written to `.local` config. The launcher Settings screen
can save the Voice-Engine folder, config path, persona, optional HTTP URL, timeout,
SAPI voice, and Piper paths.

Request contract sent to `VOICEBOT_ENGINE_URL`:

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

Response contract:

```json
{
  "transcript": "caller words",
  "response_text": "persona response",
  "response_audio_wav_base64": "optional 16-bit WAV audio"
}
```

If HTTP mode returns audio, the caller hears that audio. If the engine returns text
only, including local package mode today, the bridge speaks it with local TTS.
Reports use `mode: voice_engine`.

## Validation

1. Portable tests: `python -m unittest discover -s tests -v` or `test_phase0.bat`.
2. Logged test bundle for troubleshooting:
   `python run_phase0_tests.py --anveophone "F:\anveophone"` or
   `run_phase0_tests.bat "F:\anveophone"`.
   Send back the ZIP created under `artifacts/test-runs`.
3. Native conference test with a working PJSUA2 binding: `python tests/native_smoke.py`.
4. Native SIP/RTP test with a working binding:
   `python tests/native_sip.py --anveophone "F:\anveophone"`.
5. Real Windows/Anveo acceptance checklist: [docs/PHASE0_CHECKLIST.md](docs/PHASE0_CHECKLIST.md).

A positive `bidirectional_pcm_observed` report shows that nonzero caller PCM arrived
and response frames were sent. It does **not** certify that the caller heard them.
The diagnostic deliberately marks live callbacks and live call verification as false
until separately tested. See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) for what was
actually verified during development.

## Architecture and boundaries

1. The host launcher imports AnveoPhone, builds its normal GUI and registration,
   and attaches an adapter from this project. Neither upstream repository is modified.
2. PJSUA2 conference media routes `call → custom PCM port → bounded input queue`.
   A network worker sends those frames to the standalone localhost receiver.
3. The receiver writes input PCM to WAV and returns paced response frames.
4. The worker queues response PCM; PJSUA2's request callback feeds it into the call.
   Missing audio produces correctly sized silence. A short prefill absorbs jitter.
5. While the bridge owns the call, PC microphone/speaker routes are disconnected
   and checked, and existing WAV playback is stopped. F9, failure or teardown restores
   the prior mute/monitor state on a still-active call.

The adapter intentionally relies on the inspected AnveoPhone backend's call object
and existing methods. It fails visibly when the required binding or media contract
is unavailable. It is single-call Phase 0 code, not a finished unattended receptionist.

## Next phase contract

See [docs/PROTOCOL.md](docs/PROTOCOL.md). Keep SIP in AnveoPhone, add session-local
speech/brain/TTS processing behind the PCM receiver, then add explicit caller/schedule
rules. Unmatched calls must continue to ring normally. Barge-in, fallback message
capture, tools, persona settings and production reconnect handling are deferred.
