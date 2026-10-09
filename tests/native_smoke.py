"""Opt-in real PJSUA2 conference test. Needs a working native binding.

Run: python tests/native_smoke.py
No microphone, SIP account, credentials or external call is used.
"""
from __future__ import annotations

import gc
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebot.pcm import AudioBuffers, FRAME_BYTES, tone
from voicebot.pjsip_port import create_port
from voicebot.server import BridgeServer
from voicebot.transport import BridgeClient


def run():
    import pjsua2 as pj

    ep = pj.Endpoint()
    ep.libCreate()
    config = pj.EpConfig()
    config.uaConfig.threadCnt = 0
    config.uaConfig.mainThreadOnly = True
    config.logConfig.level = 1
    config.logConfig.consoleLevel = 1
    config.medConfig.clockRate = 16000
    config.medConfig.sndClockRate = 16000
    ep.libInit(config)
    ep.libStart()
    ep.audDevManager().setNullDev()
    source = bridge = sink = None
    client = server = None
    connected = []
    try:
        with tempfile.TemporaryDirectory() as folder:
            server = BridgeServer("a" * 64, port=0, output_dir=folder)
            server.start()
            buffers = AudioBuffers(prefill_frames=3)
            client = BridgeClient(buffers, "a" * 64, server.port)
            client.start()
            if not client.connected.wait(2):
                raise RuntimeError(client.error or "Bridge handshake failed")
            source_buffers = AudioBuffers(capacity=250)
            for _ in range(200):
                source_buffers.send(tone(0.02, 660))
            sink_buffers = AudioBuffers(capacity=250)
            source = create_port(pj, source_buffers, "test-source")
            bridge = create_port(pj, buffers, "test-bridge")
            sink = create_port(pj, sink_buffers, "test-sink")
            for a, b in ((source, bridge), (bridge, sink)):
                a.startTransmit(b)
                connected.append((a, b))
            deadline = time.monotonic() + 3.2
            while time.monotonic() < deadline:
                ep.libHandleEvents(10)
                time.sleep(0.005)
            for a, b in connected:
                a.stopTransmit(b)
            connected.clear()
            client.close()
            captured = []
            while not sink_buffers.incoming.empty():
                frame = sink_buffers.incoming.get_nowait()
                if any(frame):
                    captured.append(frame)
            received = b"".join(captured)
            report = client.report
            if not report or not report["bidirectional_pcm_observed"] or buffers.callback_errors:
                raise AssertionError("Native callback flow did not pass: " + str(report))
            if received != tone():
                raise AssertionError(f"Native return PCM differs: {len(received)} bytes, expected {len(tone())}")
            result = {"native_conference_test": "PASS", "pjsip_version": ep.libVersion().full,
                      "rx_frames": report["rx_frames"], "tx_frames": report["tx_frames"],
                      "returned_pcm_bytes": len(received), "callback_errors": buffers.callback_errors,
                      "external_sip_call_tested": False, "windows_binding_tested": False}
            print(json.dumps(result, indent=2))
            return result
    finally:
        for a, b in connected:
            a.stopTransmit(b)
        connected.clear()
        if client:
            client.close()
        if server:
            server.close()
        a = b = source = bridge = sink = None
        gc.collect()
        ep.libDestroy()


if __name__ == "__main__":
    run()
