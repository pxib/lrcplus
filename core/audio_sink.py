"""Discard pending audio when releasing an output sink."""


def dispose_audio_sink(sink):
    # stop() may drain queued audio, and resuming a suspended Windows stream
    # makes samples already in the device buffer audible even after muting.
    # reset() discards both buffers without restarting playback.
    sink.reset()
    sink.stop()
    sink.deleteLater()
