"""PCM session buffers and control policy, with a Python source-build fallback."""

from collections import deque
from ctypes import (Structure, byref, c_bool, c_double, c_int32, c_int64, c_size_t,
                    c_uint32, c_void_p, cast, string_at)
from functools import lru_cache
from math import ceil, isnan, log2
from types import SimpleNamespace
from weakref import finalize

from PySide6.QtMultimedia import QAudioFormat

from core.native_audio import audio_kernel


class _NativeControls(Structure):
    _fields_ = [("speed", c_double), ("shift", c_double), ("gain", c_double),
                ("enabled", c_bool), ("live_speed", c_bool), ("pitch_linked", c_bool)]

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.speed, settings.shift, settings.gain, settings.enabled,
                   settings.live_speed, settings.pitch_linked)


class _DspPlan(Structure):
    _fields_ = [("shift", c_double), ("boost", c_double), ("continuous", c_bool),
                ("pitched", c_bool), ("trim_pitch_padding", c_bool)]


class _Format(Structure):
    _fields_ = [("sample_rate", c_int32), ("channels", c_int32),
                ("sample_type", c_int32), ("channel_layout", c_uint32)]

    @classmethod
    def from_qt(cls, fmt):
        return cls(fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat().value,
                   fmt.channelConfig().value)

    def to_qt(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(self.sample_rate)
        fmt.setChannelCount(self.channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat(self.sample_type))
        if self.channel_layout:
            fmt.setChannelConfig(QAudioFormat.ChannelConfig(self.channel_layout))
        return fmt


class _DecodedBlock(Structure):
    _fields_ = [("data", c_void_p), ("size", c_size_t), ("timestamp", c_int64), ("format", _Format)]


@lru_cache(maxsize=1)
def _native_api():
    pointer, size = c_void_p, c_size_t
    signatures = {
        "inbox_new": (pointer, ()),
        "inbox_free": (None, (pointer,)),
        "inbox_clear": (None, (pointer,)),
        "inbox_len": (size, (pointer,)),
        "inbox_push": (None, (pointer, pointer, size)),
        "inbox_pop": (c_bool, (pointer, pointer)),
        "configure": (c_bool, (pointer, pointer, c_bool)),
        "dsp_plan": (None, (pointer, c_double, pointer)),
        "output_new": (pointer, (c_uint32, size)),
        "output_free": (None, (pointer,)),
        "output_len": (size, (pointer,)),
        "output_written": (size, (pointer,)),
        "output_add_source": (None, (pointer, size, c_int64)),
        "output_add_resampled": (None, (pointer, size, c_double)),
        "output_queue": (None, (pointer, pointer, size, size, size)),
        "output_trim": (None, (pointer, size, size)),
        "output_peek": (pointer, (pointer, size, pointer)),
        "output_commit": (None, (pointer, size)),
        "output_position": (c_double, (pointer, c_int64)),
    }
    functions = {}
    for name, (result, args) in signatures.items():
        function = audio_kernel(f"lrcplus_pcm_{name}_v1", args, result)
        if function is None:
            return None
        functions[name] = function
    return SimpleNamespace(**functions)


def make_inbox():
    api = _native_api()
    return _NativeInbox(api) if api else _PythonInbox()


def make_output(fmt):
    api = _native_api()
    return _NativeOutput(api, fmt) if api else _PythonOutput(fmt)


def dsp_plan(settings, rate):
    api = _native_api()
    if api:
        plan = _DspPlan()
        api.dsp_plan(byref(_NativeControls.from_settings(settings)), rate, byref(plan))
        return plan
    continuous = settings.live_speed and not settings.pitch_linked
    shift = settings.shift + 12 * log2(settings.speed / rate) if continuous else settings.shift
    return _DspPlan(shift, max(1.0, settings.gain), continuous,
                    continuous or abs(settings.shift) >= 0.001, settings.live_speed)


class _NativeInbox:
    def __init__(self, api):
        self._api = api
        self._handle = api.inbox_new()
        finalize(self, api.inbox_free, self._handle)

    def __len__(self):
        return self._api.inbox_len(self._handle)

    def clear(self):
        self._api.inbox_clear(self._handle)

    def configure(self, previous, settings):
        return self._api.configure(self._handle, byref(_NativeControls.from_settings(settings)),
                                   previous.device != settings.device)

    def push(self, data, fmt, timestamp, limit):
        block = _DecodedBlock(cast(data, c_void_p), len(data), timestamp, _Format.from_qt(fmt))
        self._api.inbox_push(self._handle, byref(block), limit)

    def pop(self):
        block = _DecodedBlock()
        if not self._api.inbox_pop(self._handle, byref(block)):
            return None
        # The mailbox lock covers this copy of the Rust-owned block.
        return string_at(block.data, block.size), block.format.to_qt(), block.timestamp


class _PythonInbox:
    def __init__(self):
        self._queue = deque()
        self._size = 0

    def __len__(self):
        return self._size

    def clear(self):
        self._queue.clear()
        self._size = 0

    def configure(self, previous, settings):
        pitch_restart = ((abs(previous.shift) < 0.001) != (abs(settings.shift) < 0.001)
                         or abs(previous.shift - settings.shift) >= 12.0)
        continuous = settings.live_speed and previous.pitch_linked == settings.pitch_linked
        return (previous.device != settings.device or previous.enabled != settings.enabled
                or (not continuous and (abs(previous.speed - settings.speed) >= 0.001 or pitch_restart)))

    def push(self, data, fmt, timestamp, limit):
        limit = max(len(data), limit)
        while self._queue and self._size + len(data) > limit:
            dropped, _, _ = self._queue.popleft()
            self._size -= len(dropped)
        self._queue.append((data, fmt, timestamp))
        self._size += len(data)

    def pop(self):
        if not self._queue:
            return None
        block = self._queue.popleft()
        self._size -= len(block[0])
        return block


class _NativeOutput:
    def __init__(self, api, fmt):
        self._api = api
        self._frame_size = fmt.bytesPerFrame()
        if fmt.sampleRate() <= 0 or self._frame_size <= 0:
            raise ValueError("Decoded audio has an invalid PCM format.")
        self._handle = api.output_new(fmt.sampleRate(), self._frame_size)
        finalize(self, api.output_free, self._handle)

    def __len__(self):
        return self._api.output_len(self._handle)

    @property
    def written_bytes(self):
        return self._api.output_written(self._handle)

    def add_source(self, frames, timestamp):
        self._api.output_add_source(self._handle, frames, timestamp)

    def add_resampled(self, frames, rate):
        self._api.output_add_resampled(self._handle, frames, rate)

    def queue(self, pcm, padding, content):
        if len(pcm) % self._frame_size or padding + content > len(pcm) // self._frame_size:
            raise ValueError("Processed audio has an invalid frame count.")
        self._api.output_queue(self._handle, pcm, len(pcm), padding, content)

    def trim(self, pending_before, limit):
        self._api.output_trim(self._handle, pending_before, limit)

    def peek(self, capacity):
        size = c_size_t()
        data = self._api.output_peek(self._handle, capacity, byref(size))
        return string_at(data, size.value) if size.value else b""

    def commit(self, size):
        self._api.output_commit(self._handle, size)

    def position_ms(self, played_us):
        position = self._api.output_position(self._handle, played_us)
        return None if isnan(position) else position


class _PythonOutput:
    """Carry source timestamps through DSP, the pending queue, and device writes."""

    def __init__(self, fmt):
        self._sample_rate = fmt.sampleRate()
        self._frame_size = fmt.bytesPerFrame()
        self._source_frames = 0
        self._source_frame = 0.0
        self.written_bytes = 0
        self._source = deque([(0, None)])
        self._resampled = deque()
        self._pending = deque()
        self._written = deque()
        self._pcm = bytearray()

    def __len__(self):
        return len(self._pcm)

    def add_source(self, frames, start_us):
        anchor_frame, anchor_us = self._source[-1]
        if start_us != -1:
            elapsed_us = self._to_us(self._source_frames - anchor_frame)
            if anchor_us is None or abs(start_us - anchor_us - elapsed_us) > 2000:
                self._source.append((self._source_frames, start_us))
        self._source_frames += frames

    def _to_us(self, frames):
        return frames * 1_000_000 / self._sample_rate

    def _source_position(self):
        frame = min(self._source_frames, self._source_frame)
        while len(self._source) > 1 and self._source[1][0] <= frame + 1e-7:
            self._source.popleft()
        start, timestamp = self._source[0]
        return None if timestamp is None else timestamp + self._to_us(frame - start)

    def add_resampled(self, frames, rate):
        if self._resampled and self._resampled[-1][1] == rate:
            previous, _ = self._resampled.pop()
            frames += previous
        self._resampled.append((frames, rate))

    def queue(self, pcm, padding, content):
        frames = len(pcm) // self._frame_size
        if padding:
            self._pending.append((padding * self._frame_size, self._source_position(), 0))
        remaining = content
        while remaining and self._resampled:
            span, rate = self._resampled.popleft()
            position = self._source_position()
            count = min(remaining, span)
            if len(self._source) > 1:
                boundary = (self._source[1][0] - self._source_frame) / rate
                count = min(count, max(1, ceil(boundary - 1e-9)))
            step = self._to_us(rate) / self._frame_size
            self._pending.append((count * self._frame_size, position, step))
            self._source_frame += count * rate
            remaining -= count
            if count < span:
                self._resampled.appendleft((span - count, rate))
        trailing = frames - padding - (content - remaining)
        if trailing:
            self._pending.append((trailing * self._frame_size, self._source_position(), 0))
        self._pcm.extend(pcm)

    def _consume(self, size, *, written):
        del self._pcm[:size]
        while size:
            count, position, step = self._pending.popleft()
            used = min(size, count)
            if written:
                self._written.append((self.written_bytes, position, step))
                self.written_bytes += used
            if used < count:
                next_position = None if position is None else position + used * step
                self._pending.appendleft((count - used, next_position, step))
            size -= used

    def trim(self, pending_before, limit):
        limit = max(limit, len(self._pcm) - pending_before)
        limit -= limit % self._frame_size
        self._consume(max(0, len(self._pcm) - limit), written=False)

    def peek(self, capacity):
        count = min(capacity, len(self._pcm))
        count -= count % self._frame_size
        return bytes(self._pcm[:count])

    def commit(self, size):
        self._consume(size, written=True)

    def position_ms(self, played_us):
        played = min(self.written_bytes, played_us * self._sample_rate * self._frame_size / 1_000_000)
        while len(self._written) > 1 and self._written[1][0] <= played:
            self._written.popleft()
        if self._written:
            start, position, step = self._written[0]
            if position is not None:
                return max(0, (position + (played - start) * step) / 1000)
        return None
