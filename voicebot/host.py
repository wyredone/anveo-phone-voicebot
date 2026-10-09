"""Attach to AnveoPhone from its Qt thread without modifying its source."""
from __future__ import annotations

import time

from .pcm import AudioBuffers
from .pjsip_port import create_port
from .transport import BridgeClient


class HostBridge:
    def __init__(self, engine, pj, token: str, port: int = 8765, armed: bool = False):
        self.engine, self.pj, self.token, self.port_number = engine, pj, token, port
        self.armed = armed
        self.call = None
        self.client = None
        self.media = None
        self.port = None
        self.saved = None
        self.deadline = 0.0
        self.failed_call = None
        self.last_error = ""

    def log(self, message):
        self.engine.bus.emit("log", msg="VoiceBot: " + message)

    def arm(self):
        if self.call is None:
            self.armed = True
            self.failed_call = None
            self.log("Armed for one manually answered incoming test call.")

    def tick(self):
        """Run on Qt main thread, outside PJSIP call callbacks."""
        current = self.engine.call
        if self.call is not None:
            if (current is not self.call or self.call.ended or not self.call.confirmed):
                self.release()
                return
            if self.client.finished.is_set() or time.monotonic() > self.deadline and self.port is None:
                self.last_error = self.client.error or "Bridge connection timed out"
                self.log(self.last_error + "; restoring manual call audio.")
                self.failed_call = self.call
                self.release()
                return
            if self.client.connected.is_set():
                try:
                    info = self.call.getInfo()
                    active = [i for i, m in enumerate(info.media)
                              if m.type == self.pj.PJMEDIA_TYPE_AUDIO and m.status == self.pj.PJSUA_CALL_MEDIA_ACTIVE]
                    if len(active) != 1:
                        self._detach()
                        return
                    media = self.call.getAudioMedia(active[0])
                    if self.media is None or self.media.getPortId() != media.getPortId():
                        self._detach()
                        self._attach(media)
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log("Attach failed: " + str(exc))
                    self.failed_call = self.call
                    self.release()
        elif (self.armed and current is not None and current is not self.failed_call and
              current.direction == "in" and current.confirmed and not current.ended):
            self.armed = False
            self.call = current
            self.client = BridgeClient(AudioBuffers(prefill_frames=3), self.token, self.port_number)
            self.deadline = time.monotonic() + 3.0
            self.client.start()

    def _attach(self, media):
        if self.saved is None:
            self.saved = (self.call.muted, self.call.monitor)
        self.media = media
        self.port = create_port(self.pj, self.client.buffers, "voicebot-" + self.client.session_id)
        # Prevent local microphone/speaker feedback. PJSIP clock/device is kept alive.
        self.call.set_mute(True)
        self.call.set_monitor(False)
        self.engine.be.stop_playback()
        self.engine.be.stop_preview()
        adm = self.pj.Endpoint.instance().audDevManager()
        capture = adm.getCaptureDevMedia()
        speaker = adm.getPlaybackDevMedia()
        # Backend mute helpers swallow native errors. Verify actual conference routing.
        if media.getPortId() in list(capture.getPortInfo().listeners):
            raise RuntimeError("PC microphone is still connected to call")
        if speaker.getPortId() in list(media.getPortInfo().listeners):
            raise RuntimeError("Call is still connected to PC speaker")
        media.startTransmit(self.port)
        self.port.startTransmit(media)
        self.log("Live PCM connected; PC microphone and speaker isolated.")

    def _detach(self):
        if self.port is not None and self.media is not None and not self.call.ended:
            for source, target in ((self.media, self.port), (self.port, self.media)):
                try:
                    source.stopTransmit(target)
                except Exception:
                    pass  # media may already be gone after hangup
        self.media = None
        self.port = None  # native destructor unregisters port; called outside call callbacks
        if self.client:
            self.client.buffers.clear_output()

    def release(self):
        self.armed = False
        call, saved = self.call, self.saved
        self._detach()
        if self.client:
            self.client.close()
            self.log("Session ended: " + str(self.client.buffers.stats()))
        if call is not None and saved is not None and not call.ended:
            call.set_mute(saved[0])
            call.set_monitor(saved[1])
        self.call = self.client = self.saved = None


def launch(anveophone_dir: str, token: str, port: int, armed: bool):
    from pathlib import Path
    import sys

    root = Path(anveophone_dir).resolve()
    if not (root / "anveophone" / "app.py").is_file():
        raise ValueError("Select the AnveoPhone source folder containing anveophone/app.py")
    sys.path.insert(0, str(root))
    from PySide6 import QtCore, QtGui, QtWidgets
    from anveophone.app import build
    from anveophone.config.paths import APP_NAME, ORG
    from anveophone.logging_setup import setup_logging
    from anveophone.services.backend import HAVE_PJ, PJ_ERR, backend_mod
    from anveophone.ui.theme import STYLE
    from .pjsip_port import require_capabilities

    if not HAVE_PJ:
        raise RuntimeError(PJ_ERR)
    require_capabilities(backend_mod.pj)
    setup_logging()
    qapp = QtWidgets.QApplication([sys.argv[0]])
    qapp.setApplicationName(APP_NAME)
    qapp.setOrganizationName(ORG)
    qapp.setStyleSheet(STYLE)
    app = build()
    if not app.engine.ready:
        app.shutdown()
        raise RuntimeError("AnveoPhone SIP endpoint did not initialize")
    # Refuse a test launch when another feature would automatically answer the call.
    if app.autoans.enabled:
        app.shutdown()
        raise RuntimeError("Disable AnveoPhone Auto-answer before running the manual Phase 0 test.")
    bridge = HostBridge(app.engine, backend_mod.pj, token, port, armed)
    timer = QtCore.QTimer()
    timer.timeout.connect(bridge.tick)
    timer.start(20)
    shortcuts = [QtGui.QShortcut(QtGui.QKeySequence(key), app.window) for key in ("F8", "F9")]
    shortcuts[0].activated.connect(bridge.arm)
    shortcuts[1].activated.connect(bridge.release)
    qapp.aboutToQuit.connect(timer.stop)
    qapp.aboutToQuit.connect(bridge.release)
    qapp.aboutToQuit.connect(app.shutdown)
    app.window.setWindowTitle(app.window.windowTitle() + " — Phase 0 (F8 arm / F9 manual audio)")
    app.window.show()
    bridge.log("Armed for one incoming call." if armed else "Inactive. F8 arms one incoming test call.")
    return qapp.exec()
