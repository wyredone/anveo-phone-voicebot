r"""Opt-in localhost SIP/RTP test using the actual AnveoPhone backend.

python tests/native_sip.py --anveophone C:\path\to\anveophone
Requires a working PJSUA2 binding. No Anveo account or external call is used.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebot.host import HostBridge
from voicebot.pcm import RATE, tone
from voicebot.server import BridgeServer


def peer(target, source_path, output_path):
    import pjsua2 as pj

    ep = pj.Endpoint()
    ep.libCreate()
    cfg = pj.EpConfig()
    cfg.uaConfig.threadCnt = 0
    cfg.uaConfig.mainThreadOnly = True
    cfg.logConfig.level = cfg.logConfig.consoleLevel = 1
    cfg.medConfig.clockRate = RATE
    ep.libInit(cfg)
    transport = pj.TransportConfig()
    transport.boundAddress = "127.0.0.1"
    ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport)
    ep.libStart()
    ep.audDevManager().setNullDev()
    # Force common narrowband telephone audio and exercise resampling to bridge PCM.
    for codec in ep.codecEnum2():
        ep.codecSetPriority(codec.codecId, 255 if codec.codecId.startswith("PCMU/8000") else 0)
    acc = pj.Account()
    acfg = pj.AccountConfig()
    acfg.idUri = "sip:tester@127.0.0.1"
    acfg.mediaConfig.transportConfig.boundAddress = "127.0.0.1"
    acfg.mediaConfig.transportConfig.publicAddress = "127.0.0.1"
    acfg.mediaConfig.transportConfig.port = 0
    acc.create(acfg)
    player = pj.AudioMediaPlayer()
    player.createPlayer(str(source_path), pj.PJMEDIA_FILE_NO_LOOP)
    recorder = pj.AudioMediaRecorder()
    recorder.createRecorder(str(output_path))

    class TestCall(pj.Call):
        def __init__(self):
            super().__init__(acc)
            self.media = None
            self.ended = False

        def onCallState(self, prm):
            self.ended = self.getInfo().state == pj.PJSIP_INV_STATE_DISCONNECTED

        def onCallMediaState(self, prm):
            info = self.getInfo()
            for i, m in enumerate(info.media):
                if m.type == pj.PJMEDIA_TYPE_AUDIO and m.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                    self.media = self.getAudioMedia(i)
                    player.startTransmit(self.media)
                    self.media.startTransmit(recorder)

    call = TestCall()
    try:
        call.makeCall(f"sip:host@127.0.0.1:{target}", pj.CallOpParam(True))
        deadline = time.monotonic() + 4.5
        while time.monotonic() < deadline and not call.ended:
            ep.libHandleEvents(10)
            time.sleep(0.005)
        if call.media:
            player.stopTransmit(call.media)
            call.media.stopTransmit(recorder)
        call.hangup(pj.CallOpParam())
        deadline = time.monotonic() + 1
        while not call.ended and time.monotonic() < deadline:
            ep.libHandleEvents(10)
        return 0
    finally:
        call.media = None
        call = player = recorder = None
        gc.collect()
        acc.shutdown()
        acc = None
        ep.libDestroy()


def signal_energy(path, frequency):
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise AssertionError("Unexpected capture format")
        rate = wav.getframerate()
        data = wav.readframes(wav.getnframes())
    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
    c = sum(sample * math.cos(2 * math.pi * frequency * i / rate) for i, sample in enumerate(samples))
    s = sum(sample * math.sin(2 * math.pi * frequency * i / rate) for i, sample in enumerate(samples))
    return math.hypot(c, s) / max(1, len(samples))


def run(anveophone_dir, result_path):
    # Load binding before the source directory so an installed matching native wrapper wins.
    import pjsua2 as pj
    sys.path.insert(0, str(Path(anveophone_dir).resolve()))
    from pjsua2_backend import PJSUA2Backend

    class Engine:
        def __init__(self, backend):
            self.be = backend
            self.bus = SimpleNamespace(emit=lambda event, **data: print(json.dumps({"event": event, **data}), flush=True))

        @property
        def call(self):
            return self.be.current_call

    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        source = folder / "caller.wav"
        received = folder / "caller-heard.wav"
        with wave.open(str(source), "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            wav.writeframes(tone(5, 660))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            sip_port = reservation.getsockname()[1]
        backend = PJSUA2Backend()
        server = BridgeServer("a" * 64, port=0, output_dir=folder / "captures")
        server.start()
        bridge = HostBridge(Engine(backend), pj, "a" * 64, server.port, armed=True)
        process = None
        try:
            backend.initialize(listen_port=sip_port, poll_mode=True, log_level=1)
            backend.use_null_audio()
            backend.ensure_local_account(local_domain="127.0.0.1")
            account_cfg = pj.AccountConfig()
            account_cfg.idUri = backend.account.getInfo().uri
            account_cfg.regConfig.registerOnAdd = False
            account_cfg.mediaConfig.transportConfig.boundAddress = "127.0.0.1"
            account_cfg.mediaConfig.transportConfig.publicAddress = "127.0.0.1"
            account_cfg.mediaConfig.transportConfig.port = 0
            backend.account.modify(account_cfg)
            with (folder / "peer.log").open("w") as output:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--peer",
                                            str(sip_port), str(source), str(received)],
                                           stdout=output, stderr=subprocess.STDOUT, env=os.environ.copy())
                deadline = time.monotonic() + 10
                answered = False
                callback_errors = None
                negotiated_codec = ""
                while time.monotonic() < deadline and process.poll() is None:
                    backend.poll(0)
                    call = backend.current_call
                    if call and not call.confirmed and not call.ended and not answered:
                        backend.answer()  # explicit test harness only, not VoiceBot answering
                        answered = True
                    bridge.tick()
                    if call and call.confirmed and not call.ended and not negotiated_codec:
                        try:
                            negotiated_codec = call.getStreamInfo(0).codecName
                        except Exception:
                            pass
                    if bridge.client:
                        callback_errors = bridge.client.buffers.callback_errors
                    time.sleep(0.005)
                if process.poll() is None:
                    raise AssertionError("Local SIP peer timed out")
                if process.returncode != 0:
                    raise AssertionError((folder / "peer.log").read_text())
            bridge.release()
            if bridge.last_error or not server.reports:
                raise AssertionError("Host bridge failed: " + bridge.last_error)
            report = server.reports[-1]
            caller_energy = signal_energy(report["capture_path"], 660)
            response_energy = signal_energy(received, 440)
            if (caller_energy < 100 or response_energy < 100 or callback_errors != 0 or
                    not report["bidirectional_pcm_observed"]):
                print((folder / "peer.log").read_text(), flush=True)
                raise AssertionError(f"Audio failed: input {caller_energy}, output {response_energy}, errors {callback_errors}")
            result = {"native_sip_rtp_test": "PASS", "pjsip_version": backend.endpoint.libVersion().full,
                      "backend_source": str(Path(anveophone_dir).resolve()), "sip_peer_exit": process.returncode,
                      "caller_660hz_energy": round(caller_energy, 2), "response_440hz_energy": round(response_energy, 2),
                      "rx_frames": report["rx_frames"], "tx_frames": report["tx_frames"],
                      "callback_errors": callback_errors, "windows_binding_tested": False,
                      "negotiated_codec": negotiated_codec,
                      "external_anveo_call_tested": False}
            path = Path(result_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, indent=2))
            return result
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            bridge.release()
            server.close()
            backend.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--peer":
        raise SystemExit(peer(int(sys.argv[2]), sys.argv[3], sys.argv[4]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--anveophone", required=True)
    parser.add_argument("--report", default="artifacts/native-sip.json")
    args = parser.parse_args()
    run(args.anveophone, args.report)
