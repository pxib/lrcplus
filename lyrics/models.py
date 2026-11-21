from dataclasses import dataclass, field
from copy import deepcopy
from typing import Optional


class _LyricModel:
    __slots__ = ()

    def __deepcopy__(self, memo):
        # Default slot copying builds a temporary attribute dictionary for every
        # segment. Copy directly for document Undo while preserving shared lists
        # and segments through the usual deepcopy memo.
        result = object.__new__(type(self))
        memo[id(self)] = result
        for name in self.__dataclass_fields__:
            setattr(result, name, deepcopy(getattr(self, name), memo))
        return result


@dataclass(slots=True)
class LyricSegment(_LyricModel):
    start: float
    text: str
    ruby: Optional[str] = None
    instant: bool = False
    # Explicit end preserves pauses between karaoke chunks. When omitted,
    # the next segment start (or line end) is used.
    end: Optional[float] = None
    # "manual" means explicitly authored/imported ruby. "generated" means it
    # came from an automatic reading provider and may be regenerated safely.
    ruby_source: Optional[str] = None
    # Inline ruby spans keep furigana independent from karaoke boundaries.
    # Each tuple is (start_offset, end_offset, reading) within text.
    ruby_spans: list[tuple[int, int, str]] = field(default_factory=list)
    # Zero-based visual line within a multiline LRCX lyric.
    visual_line: int = 0
    # Session-only editor hint. It is copied by Undo but is not lyric content.
    needs_timing_review: bool = field(default=False, compare=False, metadata={"editor_only": True})
    # (reading character offset, seconds from segment start) for a full-chunk ruby.
    # Two cues at one offset are the preceding mora's end and the next mora's start.
    mora_cues: tuple[tuple[int, float], ...] = ()


@dataclass(slots=True)
class LyricLine(_LyricModel):
    start: float
    text: str
    segments: list[LyricSegment] = field(default_factory=list)
    end: Optional[float] = None
    # Explicit karaoke state. Do not infer this later from mutable segment data.
    has_karaoke: bool = False
    # Formatting is indexed into visible text, independently of timed chunks.
    # Each tuple is (start_offset, end_offset, bold, italic, strike, underline).
    style_spans: list[tuple[int, int, bool, bool, bool, bool]] = field(default_factory=list)
    # A single quote level applies to the whole timestamp, including newlines.
    is_blockquote: bool = False
    # Zero-based visual rows introduced by a translation break {\nt}.
    translation_lines: list[int] = field(default_factory=list)
    # User-selected reading words, in visible text coordinates. Timing chunks
    # remain independent; groups contain no whitespace and do not overlap.
    reading_groups: list[tuple[int, int]] = field(default_factory=list)
    # A playback fallback should stay implicit when saving line-timed lyrics.
    end_inferred: bool = field(default=False, compare=False, repr=False)
