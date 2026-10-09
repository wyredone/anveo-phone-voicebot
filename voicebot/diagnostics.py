"""Read-only checks with this exact Python interpreter; no SIP registration."""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import struct
import sys
from pathlib import Path


def diagnose(anveophone_dir: str, voice_engine_dir: str | None = None) -> dict:
    root = Path(anveophone_dir).resolve()
    result = {"python": sys.version, "executable": sys.executable,
              "platform": platform.platform(), "bits": struct.calcsize("P") * 8,
              "anveophone_dir": str(root), "binding_import": False,
              "live_callbacks_verified": False, "live_call_verified": False}
    try:
        sys.path.insert(0, str(root))
        dependencies = root / "dependencies"
        if (dependencies / "pjsua2.py").is_file():
            os.environ["PJSUA2_PATH"] = os.pathsep.join(
                item for item in (os.environ.get("PJSUA2_PATH", ""), str(dependencies)) if item)
        spec = importlib.util.spec_from_file_location("pjsua2_backend", root / "pjsua2_backend.py")
        if not spec or not spec.loader:
            raise RuntimeError("Missing pjsua2_backend.py")
        backend = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = backend
        spec.loader.exec_module(backend)
        if backend.pj is None:
            raise RuntimeError(backend.PJ_IMPORT_ERROR)
        from .pjsip_port import require_capabilities
        require_capabilities(backend.pj)
        result["binding_import"] = True
        result["binding_file"] = getattr(backend.pj, "__file__", "unknown")
        result["audio_media_port_exposed"] = True
        result["bytevector_fast_copy"] = hasattr(backend.pj.ByteVector, "assign_from_bytes")
        try:
            result["pjsip_version"] = backend.pj.Endpoint().libVersion().full
        except Exception:
            result["pjsip_version"] = "unknown"
        result["qt_available"] = importlib.util.find_spec("PySide6") is not None
    except Exception as exc:
        result["error"] = str(exc)
    if voice_engine_dir:
        voice = Path(voice_engine_dir).resolve()
        result["voice_engine_dir"] = str(voice)
        result["voice_engine_source_present"] = (voice / "wyres_voice_engine" / "controller.py").is_file()
        result["voice_engine_used_in_phase_0"] = False
    result["ready_for_manual_call_test"] = bool(result["binding_import"] and result.get("qt_available"))
    return result


def save_report(report: dict, path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
