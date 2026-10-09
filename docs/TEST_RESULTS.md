# Phase 0 development test results — 10-09-26

Implementation and automated development tests are complete. Acceptance on the
actual Windows PC and external Anveo service remains pending because the connected
Windows device was offline during this work.

| Check | Result | Evidence / scope |
|---|---|---|
| Portable suite, Python 3.12.14 / Linux | PASS, 20 tests | Real loopback TCP, exact incoming WAV content, complete response PCM, malformed messages, authentication, queue bounds, session isolation, manual audio restoration |
| Native PJSUA2 conference callbacks | PASS | PJSIP 2.16: 160 input frames, 50 response frames, all 32,000 returned PCM bytes matched exactly, zero callback errors |
| Native SIP/RTP with AnveoPhone backend | PASS | Two local SIP endpoints, PCMU at 8 kHz, custom bridge at 16 kHz; 224 caller frames and 50 response frames; zero callback errors |
| PC routing isolation | PASS in native SIP harness | Microphone→call and call→speaker conference links absent while adapter owned call |
| Python compilation and CLI help | PASS | All bridge/test Python files compiled; CLI commands load |
| Windows native `.pyd` and DLL loading | NOT RUN | Run `doctor` with AnveoPhone's working Windows Python |
| Qt launcher on Windows | NOT RUN | Requires actual Windows environment and existing AnveoPhone dependencies |
| External Anveo registration and call | NOT RUN | No customer SIP credentials were used; Windows device offline |
| Human caller hears reply / identifiable speech capture | NOT RUN | Follow PHASE0_CHECKLIST.md |
| Voice-Engine AI/persona/STT/TTS | DEFERRED | Outside audio-only Phase 0 |

The SIP test used a generated 660 Hz caller signal and a generated 440 Hz return
signal. Measured signal energies were 1,803.59 and 446.04 respectively after codec
and resampling. These are signal measurements, not a human listening test.
Port/worker cleanup and process exit completed without native crashes.

Source inspected:

1. AnveoPhone commit `769b1c954d56ce91e0ab1a78d805a54510010b61`.
2. Voice-Engine commit `c31b7980edb80b0ea4212b71004ab39a77bc92a5`.
3. PJSIP tag `2.16`, source commit `6cab30cec44af36ba7c8a45ff7af2f74a8ed288c`,
   built with Python SWIG directors/thread support for the native tests.

The included GitHub Actions workflow schedules portable tests on Windows/Linux
with Python 3.10, 3.12 and 3.13. Those matrix jobs are not claimed as passing until
GitHub executes them. A successful local native test does not prove that a different
Windows binding has matching director/ABI support.

Upstream technical references:

1. [PJSIP audio frame manipulation](https://docs.pjsip.org/en/latest/specific-guides/audio/audio_frame_manipulation.html).
2. [PJSUA2 media API](https://docs.pjsip.org/en/latest/api/generated/pjsip/group/group__PJSUA2__MED.html).
3. [AnveoPhone](https://github.com/wyredone/anveophone).
4. [Voice-Engine](https://github.com/wyredone/Voice-Engine).
