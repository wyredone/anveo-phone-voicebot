from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.8 fallback is not used by this project.
    ZoneInfo = None


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts" / "test-runs"


def now_local() -> datetime:
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo("America/Chicago"))


def timestamp() -> str:
    return now_local().strftime("%m-%d-%y_%H-%M-%S")


def display_time() -> str:
    return now_local().strftime("%m-%d-%y %I:%M %p")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def safe_name(name: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    return "".join(ch if ch in allowed else "_" for ch in name).strip("_") or "step"


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def command_line(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


class TestRun:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.started = now_local()
        self.run_id = f"phase0-{timestamp()}"
        self.run_dir = ARTIFACTS / self.run_id
        self.logs_dir = self.run_dir / "logs"
        self.results: list[dict] = []
        self.notes: list[str] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def log_step(self, name: str, command: list[str], timeout: int | None = None) -> dict:
        started = time.monotonic()
        started_at = display_time()
        log_path = self.logs_dir / f"{len(self.results) + 1:02d}_{safe_name(name)}.log"
        result = {
            "name": name,
            "status": "UNKNOWN",
            "returncode": None,
            "started_at": started_at,
            "duration_seconds": None,
            "command": command_line(command),
            "log": rel(log_path),
        }
        header = [
            f"Step: {name}",
            f"Started: {started_at}",
            f"Working directory: {ROOT}",
            f"Command: {command_line(command)}",
            "",
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            result["returncode"] = proc.returncode
            result["status"] = "PASS" if proc.returncode == 0 else "FAIL"
            output = proc.stdout
        except subprocess.TimeoutExpired as exc:
            result["status"] = "TIMEOUT"
            result["returncode"] = None
            output = (exc.stdout or "") + (exc.stderr or "")
            output += f"\n\nTimed out after {timeout} seconds.\n"
        except Exception:
            result["status"] = "ERROR"
            output = traceback.format_exc()
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        write_text(log_path, "\n".join(header) + output)
        self.results.append(result)
        print(f"{result['status']:7} {name} ({result['duration_seconds']}s)")
        return result

    def skipped(self, name: str, reason: str) -> dict:
        result = {
            "name": name,
            "status": "SKIPPED",
            "returncode": None,
            "started_at": display_time(),
            "duration_seconds": 0,
            "command": "",
            "log": "",
            "reason": reason,
        }
        self.results.append(result)
        print(f"SKIPPED {name}: {reason}")
        return result

    def write_environment(self):
        env = {
            "run_id": self.run_id,
            "started_at": self.started.strftime("%m-%d-%y %I:%M %p"),
            "python_executable": sys.executable,
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cwd": str(ROOT),
            "path": os.environ.get("PATH", ""),
            "anveophone": self.args.anveophone,
            "voice_engine": self.args.voice_engine,
            "skip_native": self.args.skip_native,
            "quick": self.args.quick,
        }
        write_text(self.run_dir / "environment.json", json.dumps(env, indent=2) + "\n")

    def run(self):
        self.write_environment()
        if self.args.anveophone:
            command = [sys.executable, "-m", "voicebot", "doctor", "--anveophone", self.args.anveophone,
                       "--report", str(self.run_dir / "doctor.json")]
            if self.args.voice_engine:
                command.extend(["--voice-engine", self.args.voice_engine])
            self.log_step("doctor", command, self.args.timeout)
        else:
            self.skipped("doctor", "Pass --anveophone PATH to check the Windows AnveoPhone/PJSUA2 binding.")

        self.log_step("portable_unit_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                      self.args.timeout)

        if self.args.quick:
            self.skipped("native_smoke", "Quick mode requested.")
            self.skipped("native_sip", "Quick mode requested.")
        elif self.args.skip_native:
            self.skipped("native_smoke", "--skip-native requested.")
            self.skipped("native_sip", "--skip-native requested.")
        else:
            self.log_step("native_smoke", [sys.executable, "tests/native_smoke.py"], self.args.timeout)
            if self.args.anveophone:
                self.log_step("native_sip", [sys.executable, "tests/native_sip.py",
                                             "--anveophone", self.args.anveophone,
                                             "--report", str(self.run_dir / "native-sip.json")],
                              self.args.timeout)
            else:
                self.skipped("native_sip", "Pass --anveophone PATH to run the localhost SIP/RTP test.")

        self.copy_recent_call_artifacts()
        self.write_summary()
        archive = self.make_zip()
        print()
        print(f"Results folder: {self.run_dir}")
        print(f"Send this ZIP back: {archive}")
        return 1 if any(item["status"] in {"FAIL", "TIMEOUT", "ERROR"} for item in self.results) else 0

    def copy_recent_call_artifacts(self):
        calls = ROOT / "artifacts" / "calls"
        if not calls.exists():
            return
        target = self.run_dir / "recent-calls"
        copied = 0
        cutoff = time.time() - 86400
        for path in calls.glob("*"):
            if path.is_file() and path.stat().st_mtime >= cutoff:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / path.name)
                copied += 1
        if copied:
            self.notes.append(f"Copied {copied} recent call artifact(s) from artifacts/calls.")

    def write_summary(self):
        summary = {
            "run_id": self.run_id,
            "started_at": self.started.strftime("%m-%d-%y %I:%M %p"),
            "finished_at": display_time(),
            "results": self.results,
            "notes": self.notes,
            "send_back": [
                rel(self.run_dir / "summary.json"),
                rel(self.run_dir / "summary.md"),
                rel(self.run_dir / f"{self.run_id}.zip"),
            ],
        }
        write_text(self.run_dir / "summary.json", json.dumps(summary, indent=2) + "\n")
        lines = [
            f"# Phase 0 Test Run {self.run_id}",
            "",
            f"- Started: {summary['started_at']}",
            f"- Finished: {summary['finished_at']}",
            f"- Python: `{sys.executable}`",
            f"- Platform: `{platform.platform()}`",
            "",
            "| Step | Status | Seconds | Log |",
            "|---|---:|---:|---|",
        ]
        for item in self.results:
            log = item.get("log") or item.get("reason", "")
            lines.append(f"| {item['name']} | {item['status']} | {item['duration_seconds']} | {log} |")
        if self.notes:
            lines.extend(["", "## Notes", ""])
            lines.extend(f"- {note}" for note in self.notes)
        lines.extend([
            "",
            "## What To Send Back",
            "",
            f"Send `{self.run_id}.zip`, or send `summary.json` plus the `logs` folder if ZIP upload is blocked.",
            "",
        ])
        write_text(self.run_dir / "summary.md", "\n".join(lines))

    def make_zip(self) -> Path:
        archive = self.run_dir / f"{self.run_id}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in self.run_dir.rglob("*"):
                if path == archive or not path.is_file():
                    continue
                zf.write(path, path.relative_to(self.run_dir))
        return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Phase 0 diagnostics/tests and package logs for troubleshooting."
    )
    parser.add_argument("--anveophone", help=r"Path to AnveoPhone source folder, e.g. F:\anveophone")
    parser.add_argument("--voice-engine", help=r"Optional Voice-Engine source folder, e.g. F:\Voice-Engine")
    parser.add_argument("--skip-native", action="store_true", help="Skip native PJSUA2 tests.")
    parser.add_argument("--quick", action="store_true", help="Run doctor and portable tests only.")
    parser.add_argument("--timeout", type=int, default=90, help="Seconds before a single step times out.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = TestRun(args)
    try:
        return runner.run()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        runner.notes.append("Run interrupted by user.")
        runner.write_summary()
        runner.make_zip()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
