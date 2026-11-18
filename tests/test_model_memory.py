"""Compact models must preserve document copying and library persistence."""

from copy import copy, deepcopy
from dataclasses import asdict, replace
import pickle

import pytest

from core.music_library import LibraryTrack
from lyrics.models import LyricLine, LyricSegment


@pytest.mark.parametrize("model", [
    LibraryTrack("song.mp3", "Song", artist="Artist", duration=123.5),
    LyricSegment(1, "word", ruby="reading", end=2, ruby_source="manual",
                 ruby_spans=[(0, 4, "reading")], visual_line=1, needs_timing_review=True),
    LyricLine(1, "word", [LyricSegment(1, "word")], end=2, has_karaoke=True,
              style_spans=[(0, 4, True, False, False, False)], is_blockquote=True,
              translation_lines=[1], reading_groups=[(0, 4)]),
])
def test_models_preserve_all_fields_when_copied_or_serialized(model):
    for result in (copy(model), deepcopy(model), pickle.loads(pickle.dumps(model)), replace(model)):
        assert result is not model
        assert asdict(result) == asdict(model)


def test_undo_copy_preserves_shared_references_without_sharing_mutable_document_data():
    segment = LyricSegment(1, "word", ruby_spans=[(0, 4, "reading")], needs_timing_review=True)
    line = LyricLine(1, "word word", [segment, segment], reading_groups=[(0, 4)])
    original = [line, line]
    snapshot = deepcopy(original)
    assert snapshot[0] is snapshot[1]
    assert snapshot[0].segments[0] is snapshot[0].segments[1]
    assert snapshot[0].segments is not line.segments
    assert snapshot[0].segments[0].ruby_spans is not segment.ruby_spans
    assert snapshot[0].segments[0].needs_timing_review
    snapshot[0].segments[0].ruby_spans.clear()
    snapshot[0].reading_groups.clear()
    assert segment.ruby_spans == [(0, 4, "reading")]
    assert line.reading_groups == [(0, 4)]


def test_library_deserialization_keeps_defaults_and_ignores_unknown_fields():
    track = LibraryTrack.from_dict({"path": "song.mp3", "title": "Song", "future_field": True})
    assert track == LibraryTrack("song.mp3", "Song")
    assert LibraryTrack.from_dict(track.to_dict()) == track
    assert LibraryTrack.from_dict({}).path is None
