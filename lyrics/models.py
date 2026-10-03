from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LyricSegment:
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


@dataclass
class LyricLine:
    start: float
    text: str
    segments: list[LyricSegment] = field(default_factory=list)
    end: Optional[float] = None
    # Explicit karaoke state. Do not infer this later from mutable segment data.
    has_karaoke: bool = False
