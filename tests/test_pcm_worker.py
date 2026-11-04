import threading
import time

import numpy as np
from PySide6.QtMultimedia import QAudioFormat

from core.pcm_worker import PcmWorker


def wait_until(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert predicate()


def audio_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def test_worker_does_not_block_ui_and_discards_result_after_seek(qapp, monkeypatch):
    worker = PcmWorker()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    threads = []
    original = worker._process

    def blocked(job, reset):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        try:
            return original(job, reset)
        finally:
            finished.set()

    monkeypatch.setattr(worker, "_process", blocked)
    received = []
    worker.ready.connect(received.append)
    fmt = audio_format()
    data = np.full((1024, 2), 0.25, np.float32).tobytes()
    try:
        worker.submit(data, fmt, 1, 0, 1)
        assert entered.wait(2)
        assert threads[0] != threading.get_ident()
        # These calls must return while native processing is still blocked.
        for _ in range(50):
            worker.submit(data, fmt, 1, 0, 1)
        assert sum(len(job[0]) for job in worker._pending) <= fmt.bytesForDuration(120000)
        worker.reset()
        release.set()
        wait_until(qapp, lambda: worker._future is None)
        assert finished.is_set()
        assert not received
        worker.submit(bytes(len(data)), fmt, 1, 0, 1)
        wait_until(qapp, lambda: len(received) == 1)
        assert received[0][0] == bytes(len(data))
    finally:
        release.set()
        worker.close()


def test_workers_own_independent_pitch_histories(qapp):
    workers = [PcmWorker(), PcmWorker()]
    received = [[], []]
    fmt = audio_format()
    tone = (0.25 * np.sin(2 * np.pi * 440 * np.arange(24000) / 48000)).astype(np.float32)
    try:
        for index, worker in enumerate(workers):
            worker.ready.connect(received[index].append)
            source = np.column_stack((tone, tone)) if index == 0 else np.zeros((24000, 2), np.float32)
            worker.submit(source.tobytes(), fmt, 1, 5, 1)
        wait_until(qapp, lambda: all(received))
        assert np.any(np.frombuffer(received[0][0][0], np.float32))
        assert not np.any(np.frombuffer(received[1][0][0], np.float32))
    finally:
        for worker in workers:
            worker.close()


def test_worker_reports_processing_failure_on_gui_thread(qapp, monkeypatch):
    worker = PcmWorker()
    errors = []

    def fail(job, reset):
        raise ValueError("bad PCM")

    monkeypatch.setattr(worker, "_process", fail)
    worker.failed.connect(lambda error: errors.append((error, threading.get_ident())))
    try:
        worker.submit(bytes(8192), audio_format(), 1, 5, 1)
        wait_until(qapp, lambda: bool(errors))
        assert errors == [("bad PCM", threading.get_ident())]
    finally:
        worker.close()


def test_close_during_native_processing_does_not_wait_or_deliver_audio(qapp, monkeypatch):
    worker = PcmWorker()
    entered = threading.Event()
    release = threading.Event()
    received = []

    def blocked(job, reset):
        entered.set()
        assert release.wait(5)
        return bytes(8192), audio_format(), True

    monkeypatch.setattr(worker, "_process", blocked)
    worker.ready.connect(received.append)
    try:
        worker.submit(bytes(8192), audio_format(), 1, 0, 1)
        assert entered.wait(2)
        future = worker._future
        worker.close()
        assert not future.done()
        release.set()
        future.result(timeout=2)
        qapp.processEvents()
        assert not received
        worker.close()
    finally:
        release.set()
        worker.close()
