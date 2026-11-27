import math
import shutil
import subprocess
import sys
import tempfile
import threading
from array import array

from PySide6.QtCore import Signal, QThread

from core.native_audio import audio_probe_executable


SAMPLE_RATE = 2000


def find_ffmpeg():
    return shutil.which("ffmpeg")


def _waveform_peaks_from_pcm(pcm_file, samples, cancelled=None):
    pcm_file.seek(0, 2)
    total_bytes = pcm_file.tell()
    total = total_bytes // 2
    if total <= 0:
        return [], 0

    bucket_count = min(max(200, int(samples)), total)
    peaks = []
    scale = 32768.0 * 32768.0
    for index in range(bucket_count):
        if cancelled is not None and cancelled():
            return [], 0
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


def _parse_native_waveform(output, samples):
    version, duration, *peaks = output.split()
    duration_ms = int(duration)
    values = [float(peak) for peak in peaks]
    if (version != b"waveform-v1" or not 1 <= duration_ms <= 2_147_483_647
            or not 1 <= len(values) <= samples
            or not all(0.0 <= value <= 1.0 for value in values)):
        raise ValueError("Invalid response from the Rust waveform decoder.")
    return values, duration_ms


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

    def _run_decoder(self, command, stdout):
        if self._is_cancelled():
            return None
        process = subprocess.Popen(
            command, stdout=stdout, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
            output, stderr = process.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            _, stderr = process.communicate()
            if self._is_cancelled():
                return None
            raise RuntimeError(
                stderr.decode("utf-8", "replace").strip() or "Waveform decoder timed out."
            )
        finally:
            with self._process_lock:
                self._process = None
        if self._is_cancelled():
            return None
        if process.returncode:
            raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "Could not decode the audio.")
        return output

    def _decode(self):
        native_error = None
        executable = audio_probe_executable()
        if executable is not None and self.samples <= 10_000:
            try:
                output = self._run_decoder(
                    [str(executable), self.path, "--waveform", str(self.samples)], subprocess.PIPE,
                )
                return None if output is None else _parse_native_waveform(output, self.samples)
            except (OSError, RuntimeError, ValueError) as exc:
                native_error = exc
        if self._is_cancelled():
            return None
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            if native_error:
                raise RuntimeError(f"{native_error} FFmpeg was not found for fallback.")
            raise RuntimeError("ffmpeg was not found.")
        command = [
            ffmpeg, "-v", "error", "-i", self.path, "-ac", "1",
            "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
        ]
        with tempfile.TemporaryFile() as decoded_audio:
            self._run_decoder(command, decoded_audio)
            if self._is_cancelled():
                return None
            peaks, frames = _waveform_peaks_from_pcm(decoded_audio, self.samples, self._is_cancelled)
        if self._is_cancelled():
            return None
        if frames == 0:
            raise RuntimeError("No audio samples were decoded.")
        return peaks, max(1, round(frames * 1000 / SAMPLE_RATE))

    def run(self):
        try:
            if not self._is_cancelled():
                data = self._decode()
                if data is not None and not self._is_cancelled():
                    self.loaded.emit(*data)
        except Exception as exc:
            if not self._is_cancelled():
                self.failed.emit(str(exc))

