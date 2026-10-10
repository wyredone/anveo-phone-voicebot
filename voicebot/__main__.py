from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="AnveoPhone VoiceBot — localhost PCM bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check Windows interpreter and PJSUA2 binding")
    doctor.add_argument("--anveophone", required=True)
    doctor.add_argument("--voice-engine")
    doctor.add_argument("--report", default="artifacts/doctor.json")
    server = commands.add_parser("serve", help="Capture PCM and return test audio")
    server.add_argument("--wav", help="Optional 16 kHz mono 16-bit PCM response, <=30 seconds")
    server.add_argument("--output", default="artifacts/calls")
    server.add_argument("--no-capture", action="store_true", help="Only save PCM statistics")
    ai_server = commands.add_parser("serve-ai", help="Phase 1: detect a caller turn and return generated/TTS audio")
    ai_server.add_argument("--output", default="artifacts/calls")
    ai_server.add_argument("--no-capture", action="store_true", help="Only save PCM statistics")
    ai_server.add_argument("--response-text", default="Hello. Phase one is connected. I heard you through the phone bridge.")
    ai_server.add_argument("--response-mode", choices=("fixed", "echo", "rules"), default="rules",
                           help="fixed speaks --response-text; echo repeats caller text; rules uses simple receptionist replies")
    ai_server.add_argument("--piper-model", help="Optional Piper .onnx voice model for spoken response")
    ai_server.add_argument("--piper-config", help="Optional Piper model JSON config")
    ai_server.add_argument("--piper-exe", default="piper", help="Piper executable name or full path")
    ai_server.add_argument("--tts-backend", choices=("auto", "piper", "sapi", "tone"), default="auto",
                           help="Response voice backend. auto uses Piper when configured, then Windows SAPI.")
    ai_server.add_argument("--sapi-voice", help="Optional Windows SAPI voice name")
    ai_server.add_argument("--sapi-rate", type=int, default=0, help="Windows SAPI rate, -10 to 10")
    ai_server.add_argument("--sapi-volume", type=int, default=100, help="Windows SAPI volume, 0 to 100")
    ai_server.add_argument("--tts-timeout", type=float, default=8.0,
                           help="Seconds to wait while preparing local TTS before falling back to tones")
    ai_server.add_argument("--stt-backend", choices=("auto", "sapi", "off"), default="auto",
                           help="Caller speech recognition backend. auto uses Windows SAPI when available.")
    ai_server.add_argument("--stt-timeout", type=float, default=8.0,
                           help="Seconds to wait for local speech recognition")
    host = commands.add_parser("host", help="Launch AnveoPhone with optional live media adapter")
    host.add_argument("--anveophone", required=True)
    host.add_argument("--arm-next-call", action="store_true")
    for command in (server, ai_server, host):
        command.add_argument("--port", type=int, default=8765)
        command.add_argument("--token-file", default=str(Path(__file__).resolve().parents[1] / ".local" / "bridge.token"))
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            from .diagnostics import diagnose, save_report
            report = diagnose(args.anveophone, args.voice_engine)
            save_report(report, args.report)
            print(json.dumps(report, indent=2))
            return 0 if report["ready_for_manual_call_test"] else 1
        from .protocol import token_file
        token = token_file(args.token_file)
        if args.command == "host":
            from .host import launch
            return launch(args.anveophone, token, args.port, args.arm_next_call)
        from .pcm import wav_pcm
        from .server import BridgeServer
        if args.command == "serve-ai":
            from .phase1 import Phase1Responder
            responder = Phase1Responder(args.response_text, args.piper_model, args.piper_config, args.piper_exe,
                                        args.tts_backend, args.sapi_voice, args.sapi_rate, args.sapi_volume,
                                        args.tts_timeout, args.stt_backend, args.stt_timeout,
                                        args.response_mode)
            if args.response_mode == "fixed":
                responder.prepare()
            bridge = BridgeServer(token, args.port, args.output, capture=not args.no_capture, responder=responder)
            test_audio = "phase1 generated response"
        else:
            bridge = BridgeServer(token, args.port, args.output,
                                  wav_pcm(args.wav) if args.wav else None, not args.no_capture)
            test_audio = args.wav or "440 Hz tone"
        print(json.dumps({"event": "listening", "host": "127.0.0.1", "port": bridge.port,
                          "test_audio": test_audio, "capture": not args.no_capture}), flush=True)
        try:
            bridge.serve()
        except KeyboardInterrupt:
            pass
        finally:
            bridge.close()
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
