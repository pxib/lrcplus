from dataclasses import replace
from math import log2

import pytest
from PySide6.QtMultimedia import QAudioFormat

from core import pcm_buffers
from core.pcm_playback import _Controls


@pytest.fixture(params=[False, True], ids=["python", "rust"])
def pcm_session(request, monkeypatch):
    if request.param:
        if pcm_buffers._native_api() is None:
            pytest.skip("The Rust PCM engine is unavailable.")
    else:
        monkeypatch.setattr(pcm_buffers, "_native_api", lambda: None)


def test_pcm_inbox_bounds_and_preserves_surround_layout(pcm_session):
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    fmt.setChannelConfig(QAudioFormat.ChannelConfig.ChannelConfigSurround5Dot1)
    inbox = pcm_buffers.make_inbox()
    size = fmt.bytesPerFrame() * 8
    for index in range(3):
        inbox.push(bytes([index]) * size, fmt, index * 1000, 2 * size)
    assert len(inbox) == 2 * size
    for index in (1, 2):
        data, decoded_format, timestamp = inbox.pop()
        assert data == bytes([index]) * size
        assert decoded_format == fmt
        assert decoded_format.channelConfig() == fmt.channelConfig()
        assert timestamp == index * 1000
    assert inbox.pop() is None
    inbox.push(bytes(size), fmt, -1, size)
    inbox.clear()
    assert not inbox
    assert inbox.pop() is None


def test_pcm_control_plan_preserves_a_live_speed_transition(pcm_session):
    inbox = pcm_buffers.make_inbox()
    initial = _Controls()
    active = replace(initial, enabled=True)
    assert inbox.configure(initial, active)
    faster = replace(active, live_speed=True, speed=1.5, shift=-12 * log2(1.5), gain=1.25)
    assert not inbox.configure(active, faster)
    plan = pcm_buffers.dsp_plan(faster, 1.2)
    assert plan.continuous and plan.pitched and plan.trim_pitch_padding
    assert plan.shift == pytest.approx(-12 * log2(1.2))
    assert plan.boost == 1.25
    replacement = replace(faster, device=object())
    assert inbox.configure(faster, replacement)


def test_pcm_output_clock_tracks_partial_writes_and_dropped_audio(pcm_session):
    fmt = QAudioFormat()
    fmt.setSampleRate(1000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    output = pcm_buffers.make_output(fmt)
    output.add_source(4, 1_000_000)
    output.add_resampled(2, 2.0)
    output.queue(b"aabb", padding=0, content=2)
    output.add_source(4, 1_020_000)
    output.add_resampled(4, 1.0)
    output.queue(b"ccddeeFF", padding=0, content=4)
    output.trim(pending_before=4, limit=10)
    assert output.peek(3) == b"bb"
    output.commit(2)
    assert output.peek(7) == b"ccddee"
    output.commit(4)
    assert output.peek(10) == b"eeFF"
    output.commit(4)
    assert output.peek(10) == b""
    assert output.written_bytes == 10
    assert output.position_ms(0) == 1002
    assert output.position_ms(1000) == 1020
    assert output.position_ms(5000) == 1024
