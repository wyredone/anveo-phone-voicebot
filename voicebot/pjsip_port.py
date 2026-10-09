"""Create a SWIG director subclass only after importing the selected binding."""
from __future__ import annotations

from .pcm import AudioBuffers, RATE


def require_capabilities(pj):
    required = ("AudioMediaPort", "MediaFormatAudio", "MediaFrame", "ByteVector",
                "PJMEDIA_FRAME_TYPE_AUDIO", "PJMEDIA_FORMAT_PCM")
    missing = [name for name in required if not hasattr(pj, name)]
    if missing:
        raise RuntimeError("PJSUA2 binding lacks: " + ", ".join(missing) +
                           ". Install a matching Python wrapper/native build with AudioMediaPort directors.")


def create_port(pj, buffers: AudioBuffers, name: str):
    require_capabilities(pj)

    class PCMPort(pj.AudioMediaPort):
        def onFrameReceived(self, frame):
            try:
                if frame.type == pj.PJMEDIA_FRAME_TYPE_AUDIO:
                    buffers.receive(bytes(frame.buf)[:frame.size])
            except Exception:
                buffers.callback_errors += 1

        def onFrameRequested(self, frame):
            try:
                pcm = buffers.request(frame.size)
                vector = pj.ByteVector()
                if hasattr(vector, "assign_from_bytes"):
                    vector.assign_from_bytes(pcm)
                else:
                    for byte in pcm:
                        vector.append(byte)
                frame.buf = vector
                frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
                frame.size = len(pcm)
            except Exception:
                buffers.callback_errors += 1
                frame.type = pj.PJMEDIA_FRAME_TYPE_NONE
                frame.size = 0

    port = PCMPort()
    fmt = pj.MediaFormatAudio()
    fmt.init(pj.PJMEDIA_FORMAT_PCM, RATE, 1, 20000, 16)
    port.createPort(name, fmt)
    return port
