from __future__ import annotations

import json
import queue
import socket
import struct
import sys
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

from voicebot.host import HostBridge
from voicebot.phase1 import Phase1Responder, TurnDetector, wav_to_bridge_pcm
from voicebot.pcm import AudioBuffers, FORMAT, FRAME_BYTES, RATE, tone, wav_pcm
from voicebot.pjsip_port import create_port, require_capabilities
from voicebot.protocol import audio_message, audio_payload, encode, open_message, read, token_file
from voicebot.server import BridgeServer
from voicebot.transport import BridgeClient


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out")
        time.sleep(0.005)


class PCMTests(unittest.TestCase):
    def test_exact_size_and_silence(self):
        buffers = AudioBuffers()
        buffers.send(b"\x01\x02" * 3)
        self.assertEqual(buffers.request(10), b"\x01\x02" * 3 + bytes(4))
        self.assertEqual(buffers.underruns, 1)

    def test_partial_frames_preserved(self):
        buffers = AudioBuffers()
        pcm = tone(0.04)
        buffers.send(pcm)
        self.assertEqual(buffers.request(FRAME_BYTES) + buffers.request(FRAME_BYTES), pcm)

    def test_overflow_bounded(self):
        buffers = AudioBuffers(2)
        for _ in range(5):
            buffers.receive(bytes(FRAME_BYTES))
            buffers.send(bytes(FRAME_BYTES))
        self.assertEqual((buffers.rx_dropped, buffers.tx_dropped), (3, 3))
        self.assertEqual(buffers.incoming.qsize(), 2)

    def test_close_clears_stale_audio(self):
        buffers = AudioBuffers()
        buffers.send(tone(0.04))
        buffers.close()
        buffers.send(tone(0.02))
        self.assertEqual(buffers.request(FRAME_BYTES), bytes(FRAME_BYTES))
        self.assertEqual(buffers.outgoing.qsize(), 0)

    def test_bad_frame_capacity(self):
        for size in (0, 3, 9000):
            with self.assertRaises(ValueError):
                AudioBuffers().request(size)

    def test_short_response_drains_after_end_marker(self):
        buffers = AudioBuffers(prefill_frames=3)
        pcm = tone(0.02)
        buffers.send(pcm)
        self.assertEqual(buffers.request(FRAME_BYTES), bytes(FRAME_BYTES))
        buffers.finish_output()
        self.assertEqual(buffers.request(FRAME_BYTES), pcm)

    def test_wav_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, RATE, 0, "NONE", "none"))
                wav.writeframes(tone(0.02))
            self.assertEqual(wav_pcm(path), tone(0.02))
            with wave.open(str(path), "wb") as wav:
                wav.setparams((2, 2, 8000, 0, "NONE", "none"))
                wav.writeframes(bytes(40))
            with self.assertRaises(ValueError):
                wav_pcm(path)


class ProtocolTests(unittest.TestCase):
    def test_fragmented_messages(self):
        left, right = socket.socketpair()
        try:
            message = {"type": "test", "n": 1}
            wire = encode(message)
            left.sendall(wire[:2])
            left.sendall(wire[2:5])
            left.sendall(wire[5:])
            self.assertEqual(read(right), message)
        finally:
            left.close()
            right.close()

    def test_oversized_header_rejected(self):
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack("!I", 999999))
            with self.assertRaises(ValueError):
                read(right)
        finally:
            left.close()
            right.close()

    def test_wrong_session_sequence_base64_and_size(self):
        sid = str(uuid.uuid4())
        good = audio_message(sid, 0, bytes(FRAME_BYTES))
        for change in ({"session_id": str(uuid.uuid4())}, {"seq": 1}, {"pcm": "AA=="}, {"pcm": "!!"}):
            with self.assertRaises(ValueError):
                audio_payload({**good, **change}, sid, 0)

    def test_token_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private" / "token"
            self.assertEqual(token_file(path), token_file(path))
            self.assertEqual(len(token_file(path)), 64)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = BridgeServer("a" * 64, port=0, output_dir=self.temp.name)
        self.server.start()

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_full_duplex_real_localhost_pcm_and_wav(self):
        buffers = AudioBuffers()
        client = BridgeClient(buffers, "a" * 64, self.server.port)
        client.start()
        until(client.connected.is_set)
        incoming = tone(0.02, 660)
        output = bytearray()
        # Feed caller frames while collecting independently generated return audio.
        for _ in range(125):
            buffers.receive(incoming)
            output.extend(buffers.request(FRAME_BYTES))
            time.sleep(0.02)
        client.close()
        self.assertFalse(client.worker.is_alive())
        self.assertEqual(client.error, "")
        report = client.report
        self.assertGreaterEqual(report["rx_frames"], 120)
        self.assertEqual(report["tx_frames"], 50)
        self.assertTrue(report["bidirectional_pcm_observed"])
        self.assertFalse(report["live_caller_hearing_verified"])
        frames = [bytes(output[i:i + FRAME_BYTES]) for i in range(0, len(output), FRAME_BYTES)]
        received = b"".join(frame for frame in frames if any(frame))
        self.assertEqual(received, tone())
        with wave.open(report["capture_path"], "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, RATE))
            data = wav.readframes(wav.getnframes())
        self.assertEqual(data, incoming * report["rx_frames"])

    def test_bad_token_does_not_create_capture(self):
        client = BridgeClient(AudioBuffers(), "b" * 64, self.server.port)
        client.start()
        until(client.finished.is_set)
        self.assertTrue(client.error)
        self.assertFalse(client.connected.is_set())
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_server_loss_stops_output(self):
        client = BridgeClient(AudioBuffers(), "a" * 64, self.server.port)
        client.start()
        until(client.connected.is_set)
        self.server.close()
        until(client.finished.is_set)
        self.assertFalse(client.buffers.active)
        client.close()

    def test_back_to_back_sessions_no_state_leak(self):
        ids = []
        for _ in range(2):
            client = BridgeClient(AudioBuffers(), "a" * 64, self.server.port)
            client.start()
            until(client.connected.is_set)
            client.buffers.receive(tone(0.02))
            until(lambda: client.buffers.incoming.empty())
            time.sleep(0.03)
            client.close()
            ids.append(client.session_id)
        self.assertEqual(len(self.server.reports), 2)
        self.assertNotEqual(*ids)
        self.assertEqual(len(list(Path(self.temp.name).glob("*.wav"))), 2)

    def test_format_mismatch_rejected(self):
        with socket.create_connection(("127.0.0.1", self.server.port)) as sock:
            sock.settimeout(1)
            hello = open_message(str(uuid.uuid4()), "a" * 64)
            hello["format"] = {**FORMAT, "sample_rate": 8000}
            sock.sendall(encode(hello))
            with self.assertRaises(EOFError):
                read(sock)

    def test_phase1_generates_response_after_caller_turn(self):
        self.server.close()
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.server = BridgeServer("a" * 64, port=0, output_dir=self.temp.name,
                                   responder=Phase1Responder("Phase one test response."))
        self.server.start()
        buffers = AudioBuffers(prefill_frames=1)
        client = BridgeClient(buffers, "a" * 64, self.server.port)
        client.start()
        until(client.connected.is_set)
        collected = bytearray()
        for index in range(130):
            buffers.receive(tone(0.02, 660) if index < 20 else bytes(FRAME_BYTES))
            collected.extend(buffers.request(FRAME_BYTES))
            time.sleep(0.02)
        client.close()
        report = client.report
        self.assertEqual(client.error, "")
        self.assertEqual(report["mode"], "phase1")
        self.assertTrue(report["bidirectional_pcm_observed"])
        self.assertIn("caller audio detected", report["phase1_transcript"])
        self.assertEqual(report["phase1_response_source"], "fallback_tone")
        self.assertGreater(sum(byte != 0 for byte in collected), 1000)


class Phase1Tests(unittest.TestCase):
    def test_auto_backend_order_prefers_configured_local_tts(self):
        responder = Phase1Responder("Test response.", tts_backend="auto")
        expected = ["sapi"] if sys.platform == "win32" else []
        self.assertEqual(responder._backend_order(), expected)
        responder = Phase1Responder("Test response.", piper_model="voice.onnx", tts_backend="auto")
        expected = ["piper", "sapi"] if sys.platform == "win32" else ["piper"]
        self.assertEqual(responder._backend_order(), expected)

    def test_tone_backend_forces_fallback_pattern(self):
        responder = Phase1Responder("Test response.", tts_backend="tone")
        response = responder.respond(tone(0.4, 660))
        self.assertEqual(response.source, "fallback_tone")
        self.assertEqual(response.pcm, responder.respond(tone(0.4, 660)).pcm)

    def test_bad_tts_backend_rejected(self):
        with self.assertRaises(ValueError):
            Phase1Responder(tts_backend="wat")

    def test_turn_detector_waits_for_speech_then_silence(self):
        detector = TurnDetector(silence_seconds=0.06, min_speech_seconds=0.04)
        self.assertIsNone(detector.feed(bytes(FRAME_BYTES)))
        self.assertIsNone(detector.feed(tone(0.02, 660)))
        self.assertIsNone(detector.feed(tone(0.02, 660)))
        self.assertIsNone(detector.feed(bytes(FRAME_BYTES)))
        self.assertIsNone(detector.feed(bytes(FRAME_BYTES)))
        turn = detector.feed(bytes(FRAME_BYTES))
        self.assertIsNotNone(turn)
        self.assertGreaterEqual(len(turn), FRAME_BYTES * 5)

    def test_wav_to_bridge_pcm_converts_stereo_8k(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stereo.wav"
            samples = []
            for i in range(800):
                value = int(1000 * (i % 2))
                samples.extend([value, value])
            with wave.open(str(path), "wb") as wav:
                wav.setparams((2, 2, 8000, 0, "NONE", "none"))
                wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))
            pcm = wav_to_bridge_pcm(path)
        self.assertGreater(len(pcm), 3000)
        self.assertEqual(len(pcm) % 2, 0)


class FakeVector(list):
    def assign_from_bytes(self, data):
        self[:] = data


class FakeMedia:
    next_id = 1

    def __init__(self):
        self.id = FakeMedia.next_id
        FakeMedia.next_id += 1
        self.listeners = []

    def createPort(self, name, fmt):
        self.name, self.fmt = name, fmt

    def getPortId(self):
        return self.id

    def getPortInfo(self):
        return SimpleNamespace(listeners=self.listeners)

    def startTransmit(self, target):
        if target.id not in self.listeners:
            self.listeners.append(target.id)

    def stopTransmit(self, target):
        if target.id in self.listeners:
            self.listeners.remove(target.id)


class FakeFormat:
    def init(self, *values):
        self.values = values


def fake_pj():
    capture, speaker = FakeMedia(), FakeMedia()
    adm = SimpleNamespace(getCaptureDevMedia=lambda: capture, getPlaybackDevMedia=lambda: speaker)
    return SimpleNamespace(AudioMediaPort=FakeMedia, MediaFormatAudio=FakeFormat,
                           MediaFrame=object, ByteVector=FakeVector, PJMEDIA_FRAME_TYPE_AUDIO=1,
                           PJMEDIA_FORMAT_PCM=0, PJMEDIA_FRAME_TYPE_NONE=0,
                           PJMEDIA_TYPE_AUDIO=1, PJSUA_CALL_MEDIA_ACTIVE=1,
                           Endpoint=SimpleNamespace(instance=lambda: SimpleNamespace(audDevManager=lambda: adm)))


class PortAndHostTests(unittest.TestCase):
    def test_native_adapter_frame_semantics(self):
        pj = fake_pj()
        buffers = AudioBuffers()
        port = create_port(pj, buffers, "test")
        pcm = tone(0.02)
        frame = SimpleNamespace(type=1, buf=FakeVector(pcm), size=FRAME_BYTES)
        port.onFrameReceived(frame)
        self.assertEqual(buffers.incoming.get_nowait(), pcm)
        buffers.send(pcm)
        outgoing = SimpleNamespace(size=FRAME_BYTES, buf=FakeVector())
        port.onFrameRequested(outgoing)
        self.assertEqual(bytes(outgoing.buf), pcm)
        self.assertEqual(outgoing.type, pj.PJMEDIA_FRAME_TYPE_AUDIO)

    def test_missing_binding_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "AudioMediaPort"):
            require_capabilities(SimpleNamespace())

    def test_default_and_outgoing_calls_remain_manual(self):
        engine = SimpleNamespace(call=SimpleNamespace(direction="in", confirmed=True, ended=False))
        host = HostBridge(engine, fake_pj(), "a" * 64)
        host.tick()
        self.assertIsNone(host.call)
        host.armed = True
        engine.call.direction = "out"
        host.tick()
        self.assertIsNone(host.call)

    def test_attach_isolates_pc_and_restores_manual_audio(self):
        pj = fake_pj()
        capture = pj.Endpoint.instance().audDevManager().getCaptureDevMedia()
        speaker = pj.Endpoint.instance().audDevManager().getPlaybackDevMedia()
        media = FakeMedia()
        capture.startTransmit(media)
        media.startTransmit(speaker)
        call = SimpleNamespace(muted=False, monitor=True, ended=False)
        def mute(value):
            call.muted = value
            (capture.stopTransmit if value else capture.startTransmit)(media)
        def monitor(value):
            call.monitor = value
            (media.startTransmit if value else media.stopTransmit)(speaker)
        call.set_mute, call.set_monitor = mute, monitor
        messages = []
        engine = SimpleNamespace(bus=SimpleNamespace(emit=lambda event, **data: messages.append(data)),
                                 be=SimpleNamespace(stop_playback=lambda: None, stop_preview=lambda: None))
        host = HostBridge(engine, pj, "a" * 64)
        host.call = call
        host.client = SimpleNamespace(buffers=AudioBuffers(), session_id=str(uuid.uuid4()), close=lambda: None)
        host._attach(media)
        self.assertNotIn(media.id, capture.listeners)
        self.assertNotIn(speaker.id, media.listeners)
        self.assertIn(host.port.id, media.listeners)
        self.assertIn(media.id, host.port.listeners)
        host.release()
        self.assertIn(media.id, capture.listeners)
        self.assertIn(speaker.id, media.listeners)
        self.assertEqual((call.muted, call.monitor), (False, True))
        self.assertIsNone(host.port)

    def test_attach_removes_pc_routes_when_backend_mute_leaves_links(self):
        pj = fake_pj()
        capture = pj.Endpoint.instance().audDevManager().getCaptureDevMedia()
        speaker = pj.Endpoint.instance().audDevManager().getPlaybackDevMedia()
        media = FakeMedia()
        capture.startTransmit(media)
        media.startTransmit(speaker)
        call = SimpleNamespace(muted=False, monitor=True, ended=False)
        call.set_mute = lambda value: setattr(call, "muted", value)
        call.set_monitor = lambda value: setattr(call, "monitor", value)
        engine = SimpleNamespace(bus=SimpleNamespace(emit=lambda event, **data: None),
                                 be=SimpleNamespace(stop_playback=lambda: None, stop_preview=lambda: None))
        host = HostBridge(engine, pj, "a" * 64)
        host.call = call
        host.client = SimpleNamespace(buffers=AudioBuffers(), session_id=str(uuid.uuid4()), close=lambda: None)
        host._attach(media)
        self.assertNotIn(media.id, capture.listeners)
        self.assertNotIn(speaker.id, media.listeners)
        self.assertIn(host.port.id, media.listeners)
        self.assertIn(media.id, host.port.listeners)


if __name__ == "__main__":
    unittest.main()
