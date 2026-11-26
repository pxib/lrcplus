"""Align an existing lyric timeline with measured vocal activity."""

from statistics import median

import numpy as np

from lyrics.timing_drift import TimingCorrection, TimingMatch


VOCAL_FRAME_SECONDS = .02
_UNCERTAIN = "The vocals do not provide enough consistent timing cues for an automatic correction."


def sung_intervals(lines):
    """Return sung intervals, keeping authored ends and inferring missing ones."""
    timed = sorted((line.start, index, line.end) for index, line in enumerate(lines) if line.text.strip())
    starts = sorted({start for start, _, _ in timed})
    if len(starts) < 4 or not np.isfinite(starts).all() or starts[0] < 0 or starts[-1] - starts[0] < 10:
        raise ValueError("Automatic correction needs at least four timed lines spread across the song.")
    spacing = median(b - a for a, b in zip(starts, starts[1:]))
    following = dict(zip(starts, starts[1:] + [starts[-1] + spacing]))
    return [(index, start, end if end is not None and np.isfinite(end) and end > start
             else min(following[start], start + 1.2 * spacing)) for start, index, end in timed]


def _activity_score(times, labels, frames, activity, scale, offsets):
    positions = times * scale + offsets[:, None]
    levels = np.interp(positions, frames, activity, left=0, right=0)
    levels -= levels.mean(axis=1, keepdims=True)
    return (levels * labels).sum(axis=1) / np.maximum(np.linalg.norm(levels, axis=1), .01)


def _vocal_edges(intervals, activity, scale, offset):
    # Compare short windows on either side of each frame. A strong transition
    # needs both a quiet side and a sustained vocal side.
    width = 7
    sums = np.r_[0, np.cumsum(activity)]
    left = (sums[width:-width] - sums[:-2 * width]) / width
    right = (sums[2 * width:] - sums[width:-width]) / width
    starts = np.where((left < .3) & (right > .55), right - left, 0)
    ends = np.where((right < .3) & (left > .55), left - right, 0)
    matches = []

    def find(index, timestamp, strength):
        predicted = scale * timestamp + offset
        center = round(predicted / VOCAL_FRAME_SECONDS) - width
        first, last = max(0, center - 23), min(len(strength), center + 24)
        if first >= last:
            return
        frames = np.arange(first, last)
        best = frames[np.argmax(strength[first:last] - .012 * abs(frames - center))]
        if strength[best] >= .36:
            matches.append(TimingMatch(index, timestamp, float(best + width) * VOCAL_FRAME_SECONDS))

    previous_end = -1.0
    for index, start, end in intervals:
        if start - previous_end >= .32:
            find(index, start, starts)
        previous_end = max(end, previous_end)
    # The final release offers a check near the end of the song.
    index, _, end = max(intervals, key=lambda interval: interval[2])
    find(index, end, ends)
    return matches


def estimate_audio_timing(intervals, envelope, *, offset_ms=0, check_cancelled=None):
    """Find and independently verify a single shift and scale for the song."""
    envelope = np.asarray(envelope, dtype=float)
    if envelope.ndim != 1 or len(envelope) < 500 or not np.isfinite(envelope).all():
        raise ValueError(_UNCERTAIN)
    peak = np.percentile(envelope, 90)
    if peak < .004:
        raise ValueError(_UNCERTAIN)
    activity = np.clip((20 * np.log10(np.maximum(envelope / peak, .0001)) + 36) / 26, 0, 1)
    first, last = intervals[0][1], max(end for _, _, end in intervals)
    span = last - first
    duration = len(activity) * VOCAL_FRAME_SECONDS
    if .67 * span > duration:
        raise ValueError(_UNCERTAIN)
    frames = np.arange(len(activity)) * VOCAL_FRAME_SECONDS
    times = np.arange(first - 3, last + 3, max(.085, (span + 6) / 2400))
    inside = np.zeros(len(times), dtype=bool)
    near = inside.copy()
    for _, start, end in intervals:
        inside |= (times >= start + .12) & (times <= end - .18)
        near |= (times >= start - .16) & (times <= end + .14)
    silent = ~near
    if inside.sum() < 30 or silent.sum() < 30:
        raise ValueError(_UNCERTAIN)
    labels = inside[inside | silent].astype(float)
    times = times[inside | silent]
    labels -= labels.mean()
    labels /= np.linalg.norm(labels)

    # Score candidate shifts using lyric on/off regions rather than absolute
    # volume. The scale step keeps coarse errors under a second across the song.
    step = min(.012, .7 / span)
    candidates = []
    for scale in np.arange(.67, min(1.51, duration / span + step), step):
        if check_cancelled is not None:
            check_cancelled()
        offsets = np.arange(-first * scale, duration - last * scale + .001, .35)
        for group in np.array_split(offsets, max(1, (len(offsets) + 63) // 64)):
            if not len(group):
                continue
            scores = _activity_score(times, labels, frames, activity, scale, group)
            candidates.extend((float(score), float(scale), float(offset)) for score, offset in zip(scores, group))
    if not candidates:
        raise ValueError(_UNCERTAIN)
    score, scale, offset = max(candidates)
    alternative = max((quality for quality, slope, shift in candidates
                       if max(abs((slope - scale) * x + shift - offset) for x in (first, last)) > 2.5),
                      default=-1)
    if score < .60 or score - alternative < .06:
        raise ValueError(_UNCERTAIN)

    best = (score, scale, offset)
    for slope in np.arange(scale - step, scale + step + .0001, step / 8):
        offsets = np.arange(offset - .70, offset + .701, .045)
        offsets = offsets[(offsets + first * slope >= 0) & (offsets + last * slope <= duration)]
        if len(offsets):
            scores = _activity_score(times, labels, frames, activity, slope, offsets)
            index = np.argmax(scores)
            best = max(best, (float(scores[index]), float(slope), float(offsets[index])))
    _, scale, offset = best
    matches = _vocal_edges(intervals, activity, scale, offset)
    if (len(matches) < 4 or matches[0].original > first + .20 * span
            or matches[-1].original < first + .75 * span
            or not any(first + .25 * span <= match.original <= first + .70 * span for match in matches)):
        raise ValueError(_UNCERTAIN)
    source = np.array([match.original for match in matches])
    target = np.array([match.reference for match in matches])
    scale, offset = (float(value) for value in np.polyfit(source, target, 1))
    residual = float(np.max(abs(source * scale + offset - target)))
    if residual > .33:
        raise ValueError(_UNCERTAIN)
    # Audio edges can differ by a fraction of a syllable from authored timing.
    if max(abs((scale - 1) * x + offset - offset_ms / 1000) for x in (first, last)) < .28:
        return None
    return TimingCorrection(scale, offset, tuple(matches), residual)
