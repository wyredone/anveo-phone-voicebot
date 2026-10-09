from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="AnveoPhone VoiceBot — audio-only Phase 0")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check Windows interpreter and PJSUA2 binding")
    doctor.add_argument("--anveophone", required=True)
    doctor.add_argument("--voice-engine")
    doctor.add_argument("--report", default="artifacts/doctor.json")
    server = commands.add_parser("serve", help="Capture PCM and return test audio")
    server.add_argument("--wav", help="Optional 16 kHz mono 16-bit PCM response, <=30 seconds")
    server.add_argument("--output", default="artifacts/calls")
    server.add_argument("--no-capture", action="store_true", help="Only save PCM statistics")
    host = commands.add_parser("host", help="Launch AnveoPhone with optional live media adapter")
    host.add_argument("--anveophone", required=True)
    host.add_argument("--arm-next-call", action="store_true")
    for command in (server, host):
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
        bridge = BridgeServer(token, args.port, args.output,
                              wav_pcm(args.wav) if args.wav else None, not args.no_capture)
        print(json.dumps({"event": "listening", "host": "127.0.0.1", "port": bridge.port,
                          "test_audio": args.wav or "440 Hz tone", "capture": not args.no_capture}), flush=True)
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
