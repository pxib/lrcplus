"""Shared end-boundary rules for karaoke editing and previews (seconds)."""


def next_lyric_start(lines, line):
    return min((candidate.start for candidate in lines if candidate.start > line.start), default=None)


def segment_end(line, row, fallback=None):
    segment = line.segments[row]
    if segment.end is not None:
        return segment.end
    for following in line.segments[row + 1:]:
        if following.text != "\n" and following.visual_line == segment.visual_line:
            return following.start
    return line.end if line.end is not None else fallback
