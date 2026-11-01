"""Color karaoke glyphs once, without stacking normal and effect text."""

import math

from PySide6.QtGui import QColor, QLinearGradient, QBrush, QPen


def eased_progress(progress, easing):
    value = max(0.0, min(1.0, progress))
    if easing == "ease_in":
        return value * value
    if easing == "ease_out":
        return 1.0 - (1.0 - value) ** 2
    if easing == "ease_in_out":
        return 2 * value * value if value < 0.5 else 1 - (-2 * value + 2) ** 2 / 2
    return value


def text_color_pen(color):
    """Use the same glyph rasterization path as an animated gradient brush."""
    gradient = QLinearGradient(0, 0, 1, 0)
    gradient.setColorAt(0, QColor(color))
    gradient.setColorAt(1, QColor(color))
    return QPen(QBrush(gradient), 1)


def word_lift_amount(position, start, end):
    """A fixed-speed rise and settle, capped to fit very short syllables."""
    duration = end - start
    if duration <= 0 or position <= start or position >= end:
        return 0.0
    attack = min(0.12, duration / 2)
    release = min(0.16, duration / 2)
    rise = eased_progress((position - start) / attack, "ease_out")
    settle = eased_progress((end - position) / release, "ease_out")
    return rise * settle


def sweep_effect_pen(style, normal, highlight, left, extent, progress, *,
                     font_height, vertical=False, softness=8, glow_intensity=70,
                     glow_radius=1, shimmer_width=10):
    """Put both sung and unsung colors in one brush to preserve glyph coverage."""
    progress = max(0.0, min(1.0, progress))
    if progress <= 0.0:
        return text_color_pen(normal)
    if progress >= 1.0 and style != "rainbow":
        return text_color_pen(highlight)

    normal, highlight = QColor(normal), QColor(highlight)
    extent = max(1.0, extent)
    # Effect widths are visual properties of the font, independent of timing
    # group length, line wrapping, and how many glyphs share one timestamp.
    font_height = max(1.0, float(font_height))
    edge = left + extent * progress
    epsilon = 0.001
    sung = highlight
    if style == "pulse":
        strength = math.sin(math.pi * progress) * (0.5 + 0.5 * math.sin(6 * math.pi * progress))
        sung = highlight.lighter(round(100 + 70 * strength))

    if style == "rainbow":
        def rainbow(value):
            color = QColor.fromHsvF((value + progress * 0.35) % 1.0, 0.7, 1.0)
            color.setAlpha(highlight.alpha())
            return color

        stops = [(left + extent * progress * index / 12,
                  rainbow(progress * index / 12)) for index in range(13)]
        if progress < 1.0:
            boundary = edge + min(epsilon, extent * (1 - progress) / 2)
            stops.extend(((boundary, normal), (left + extent, normal)))
    elif style == "soft":
        feather = font_height * softness / 20.0
        stops = [(edge, highlight), (edge + feather, normal)]
    elif style == "glow":
        # A luminous edge within the glyphs replaces the old offset text copies.
        band = font_height * glow_radius * 0.12
        bright = highlight.lighter(round(100 + 100 * glow_intensity / 255))
        stops = [(edge - band, highlight), (edge, bright), (edge + band, normal)]
    elif style == "shimmer":
        band = font_height * shimmer_width / 20.0
        stops = [(edge - band, highlight), (edge - band * 0.25, highlight.lighter(180)),
                 (edge, highlight), (edge + epsilon, normal)]
    elif style == "spotlight":
        band = font_height * 0.6
        stops = [(edge - band, highlight), (edge - band * 0.3, highlight.lighter(170)),
                 (edge, highlight.lighter(230)), (edge + epsilon, normal)]
    else:
        stops = [(edge, sung), (edge + epsilon, normal)]

    start, end = stops[0][0], stops[-1][0]
    gradient = (QLinearGradient(0, start, 0, end) if vertical
                else QLinearGradient(start, 0, end, 0))
    for position, color in stops:
        gradient.setColorAt((position - start) / (end - start), color)
    return QPen(QBrush(gradient), 1)
