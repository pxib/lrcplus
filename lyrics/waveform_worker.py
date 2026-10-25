import math
import subprocess
import tempfile
import threading
from array import array
import sys
import shutil

from PySide6.QtCore import Signal, QThread

def find_ffmpeg():
    return shutil.which("ffmpeg")


def _waveform_peaks_from_pcm(pcm_file, samples):
    pcm_file.seek(0, 2)
    total_bytes = pcm_file.tell()
    total = total_bytes // 2
    if total <= 0:
        return [], 0

    bucket_count = min(max(200, int(samples)), total)
    peaks = []
    scale = 32768.0 * 32768.0
    for index in range(bucket_count):
        start = (index * total) // bucket_count
        end = max(start + 1, ((index + 1) * total) // bucket_count)
        pcm_file.seek(start * 2)
        bucket = array("h")
        bucket.frombytes(pcm_file.read((end - start) * 2))
        if sys.byteorder != "little":
            bucket.byteswap()
        count = len(bucket)
        acc = sum(sample * sample for sample in bucket)
        peaks.append(min(1.0, math.sqrt(acc / (count * scale))))
    return peaks, total


class _WaveformWorker(QThread):
    loaded = Signal(list, int)
    failed = Signal(str)

    def __init__(self, path, samples=1400, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.samples = max(200, int(samples))
        self._process = None
        self._process_lock = threading.Lock()
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()
        self.requestInterruption()
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    def _is_cancelled(self):
        return self._cancelled.is_set() or self.isInterruptionRequested()

    def run(self):
        try:
            if self._is_cancelled():
                return

            # Decode through ffmpeg so MP3/FLAC/M4A/etc. work uniformly.
            # A mono 8 kHz stream is plenty for a visual overview and keeps
            # memory/CPU use low even for long songs.
            ffmpeg = find_ffmpeg()
            if ffmpeg is None:
                raise FileNotFoundError("ffmpeg was not found.")

            cmd = [
                ffmpeg, "-v", "error",
                "-i", self.path,
                "-ac", "1",
                "-ar", "2000",
                "-f", "s16le",
                "-",
            ]
            with tempfile.TemporaryFile() as decoded_audio:
                process = subprocess.Popen(
                    cmd,
                    stdout=decoded_audio,
                    stderr=subprocess.PIPE,
                )
                with self._process_lock:
                    self._process = process
                    interrupted = self._is_cancelled()

                if interrupted and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass

                try:
                    _, stderr = process.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    _, stderr = process.communicate()
                    if not self._is_cancelled():
                        raise RuntimeError(
                            stderr.decode("utf-8", "replace").strip()
                            or "ffmpeg timed out while decoding the audio."
                        )
                    return
                finally:
                    with self._process_lock:
                        if self._process is process:
                            self._process = None

                if self._is_cancelled():
                    return
                if process.returncode != 0:
                    raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "ffmpeg could not decode the audio.")

                peaks, total = _waveform_peaks_from_pcm(
                    decoded_audio, self.samples
                )
            if total <= 0:
                raise RuntimeError("No audio samples were decoded.")

            if not self._is_cancelled():
                self.loaded.emit(peaks, max(1, round(total / 2000 * 1000)))
        except FileNotFoundError:
            if not self._is_cancelled():
                self.failed.emit("ffmpeg was not found.")
        except Exception as exc:
            if not self._is_cancelled():
                self.failed.emit(str(exc))

