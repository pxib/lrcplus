"""Small Standard MIDI File reader for karaoke pitch lanes.

Supports format 0/1 files with PPQN timing, tempo changes, track names,
running status, and note on/off events. SMPTE-timed and format 2 files are
rejected because they do not provide a single shared song timeline.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import struct


class MidiImportError(ValueError):
    """Raised when a MIDI file cannot be represented as a song pitch lane."""


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if offset >= len(data):
            raise MidiImportError("Unexpected end of MIDI track")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset
    raise MidiImportError("Invalid variable-length MIDI value")


def _parse_track(data: bytes, track_index: int):
    offset = 0
    tick = 0
    running_status = None
    track_name = f"Track {track_index + 1}"
    notes = []
    tempos = []
    active = defaultdict(deque)

    while offset < len(data):
        delta, offset = _read_vlq(data, offset)
        tick += delta
        if offset >= len(data):
            break

        status = data[offset]
        if status & 0x80:
            offset += 1
            if status < 0xF0:
                running_status = status
        elif running_status is not None:
            status = running_status
        else:
            raise MidiImportError("MIDI track uses invalid running status")

        if status == 0xFF:
            if offset >= len(data):
                raise MidiImportError("Truncated MIDI meta event")
            meta_type = data[offset]
            offset += 1
            size, offset = _read_vlq(data, offset)
            payload = data[offset:offset + size]
            if len(payload) != size:
                raise MidiImportError("Truncated MIDI meta event")
            offset += size
            if meta_type == 0x03 and payload:
                track_name = payload.decode("utf-8", errors="replace").strip("\x00")
            elif meta_type == 0x51 and size == 3:
                tempos.append((tick, int.from_bytes(payload, "big")))
            elif meta_type == 0x2F:
                break
            continue

        if status in (0xF0, 0xF7):
            size, offset = _read_vlq(data, offset)
            offset += size
            if offset > len(data):
                raise MidiImportError("Truncated MIDI system-exclusive event")
            running_status = None
            continue

        event_type = status & 0xF0
        channel = status & 0x0F
        data_count = 1 if event_type in (0xC0, 0xD0) else 2
        if offset + data_count > len(data):
            raise MidiImportError("Truncated MIDI channel event")
        first = data[offset]
        second = data[offset + 1] if data_count == 2 else 0
        offset += data_count

        if event_type == 0x90 and second > 0:
            # Keep the attack velocity with the start tick. The velocity on
            # the matching note-off event is a release velocity (usually 0),
            # and must not replace the note's playback velocity.
            active[(channel, first)].append((tick, second))
        elif event_type == 0x80 or (event_type == 0x90 and second == 0):
            queue = active[(channel, first)]
            if queue:
                start_tick, velocity = queue.popleft()
                if tick > start_tick:
                    notes.append((start_tick, tick, first, velocity))

    for (channel, pitch), starts in active.items():
        for start_tick, velocity in starts:
            # Close hanging notes at the last tick seen in this track.
            if tick > start_tick:
                notes.append((start_tick, tick, pitch, velocity))

    return {"name": track_name, "notes": notes, "tempos": tempos}


def load_midi(path: str | Path) -> dict:
    """Load a MIDI file into named tracks with notes in seconds and MIDI pitch."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise MidiImportError(str(error)) from error

    if len(raw) < 14 or raw[:4] != b"MThd":
        raise MidiImportError("This file is not a Standard MIDI File")
    header_size = int.from_bytes(raw[4:8], "big")
    if header_size < 6 or 8 + header_size > len(raw):
        raise MidiImportError("Invalid MIDI header")
    file_format, track_count, division = struct.unpack(">HHH", raw[8:14])
    if file_format not in (0, 1):
        raise MidiImportError("Only MIDI format 0 and 1 are supported")
    if division & 0x8000 or division == 0:
        raise MidiImportError("SMPTE-timed MIDI files are not supported")

    offset = 8 + header_size
    parsed_tracks = []
    for track_index in range(track_count):
        if offset + 8 > len(raw) or raw[offset:offset + 4] != b"MTrk":
            raise MidiImportError("MIDI track chunk is missing or truncated")
        size = int.from_bytes(raw[offset + 4:offset + 8], "big")
        offset += 8
        chunk = raw[offset:offset + size]
        if len(chunk) != size:
            raise MidiImportError("MIDI track chunk is truncated")
        offset += size
        parsed_tracks.append(_parse_track(chunk, track_index))

    tempo_events = [(0, 500000)]
    for track in parsed_tracks:
        tempo_events.extend(track["tempos"])
    tempo_events.sort(key=lambda item: item[0])

    # Build cumulative real-time anchors for converting ticks through tempo changes.
    anchors = [(0, 0.0, tempo_events[0][1])]
    last_tick, seconds, tempo = anchors[0]
    for event_tick, event_tempo in tempo_events[1:]:
        seconds += (event_tick - last_tick) * tempo / (division * 1_000_000)
        anchors.append((event_tick, seconds, event_tempo))
        last_tick, tempo = event_tick, event_tempo

    def to_seconds(tick):
        anchor_tick, anchor_seconds, anchor_tempo = anchors[0]
        for candidate in anchors[1:]:
            if candidate[0] > tick:
                break
            anchor_tick, anchor_seconds, anchor_tempo = candidate
        return anchor_seconds + (tick - anchor_tick) * anchor_tempo / (division * 1_000_000)

    tracks = []
    for track in parsed_tracks:
        notes = [
            {
                "start": to_seconds(start),
                "end": to_seconds(end),
                "pitch": pitch,
                "velocity": velocity,
            }
            for start, end, pitch, velocity in track["notes"]
        ]
        notes.sort(key=lambda note: (note["start"], note["pitch"]))
        tracks.append({"name": track["name"], "notes": notes})

    if not any(track["notes"] for track in tracks):
        raise MidiImportError("No pitched notes were found in this MIDI file")
    return {"path": str(path), "tracks": tracks}
