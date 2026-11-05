"""Release native audio output without blocking Python's other threads."""

import sys


def dispose_audio_sink(sink):
    if sys.platform == "win32":
        # PySide releases the GIL for stop(), but not reset(). Windows reset()
        # joins the native audio thread and can deadlock while holding the GIL.
        # Mute before stopping so any asynchronously drained audio is silent.
        sink.setVolume(0.0)
        # A suspended Windows stream cannot finish draining until resumed.
        sink.resume()
        sink.stop()
        sink.reset()
    else:
        # Other backends can synchronously drain on stop(); discard first.
        sink.reset()
        sink.stop()
    sink.deleteLater()
