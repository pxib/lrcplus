"""Code-painted surfaces for lyric card templates."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QTextDocument


def paint_cover(painter, rect, image, style=None):
    shadow = style is not None and style.cover_shadow_enabled and style.cover_shadow_opacity > 0
    fitted = image.size().scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
    target = QRectF(rect.center().x() - fitted.width() / 2,
                    rect.center().y() - fitted.height() / 2, fitted.width(), fitted.height())
    radius = min(style.cover_radius, target.width() / 2, target.height() / 2) if style else 0
    shape = QPainterPath()
    shape.addRoundedRect(target, radius, radius)
    painter.save()
    if shadow:
        from PIL import Image, ImageFilter
        scale = style.export_scale
        spread = style.cover_shadow_blur * 3
        shadow_rect = target.united(target.translated(style.cover_shadow_offset_x, style.cover_shadow_offset_y))
        shadow_rect.adjust(-spread, -spread, spread, spread)
        width, height = max(1, round(shadow_rect.width() * scale)), max(1, round(shadow_rect.height() * scale))
        layer = QImage(width, height, QImage.Format.Format_RGBA8888)
        layer.fill(Qt.GlobalColor.transparent)
        shadow_painter = QPainter(layer)
        shadow_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shadow_painter.scale(scale, scale)
        shadow_painter.translate(-shadow_rect.left(), -shadow_rect.top())
        color = QColor(0, 0, 0, round(255 * style.cover_shadow_opacity / 100))
        shadow_painter.fillPath(shape.translated(style.cover_shadow_offset_x, style.cover_shadow_offset_y), color)
        shadow_painter.end()
        pixels = Image.frombytes("RGBA", (width, height), bytes(layer.constBits()))
        pixels = pixels.filter(ImageFilter.GaussianBlur(style.cover_shadow_blur * scale))
        shadow_image = QImage(pixels.tobytes(), width, height, QImage.Format.Format_RGBA8888).copy()
        painter.drawImage(shadow_rect, shadow_image)
    painter.save()
    if radius:
        painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    painter.drawImage(target, image)
    painter.restore()
    if style is not None and style.cover_border_width:
        width = min(style.cover_border_width, target.width() / 2, target.height() / 2)
        edge = target.adjusted(width / 2, width / 2, -width / 2, -width / 2)
        painter.setPen(QPen(QColor(style.cover_border_color), width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(edge, max(0, radius - width / 2), max(0, radius - width / 2))
    painter.restore()


def _blurred_cover(image, size):
    """Blur a small crop before upscaling; no artwork files or extra fetches."""
    from PIL import Image, ImageFilter
    size = size.toSize().scaled(QSize(320, 320), Qt.AspectRatioMode.KeepAspectRatio)
    scale = max(size.width() / image.width(), size.height() / image.height())
    crop_width, crop_height = size.width() / scale, size.height() / scale
    crop = QRectF((image.width() - crop_width) / 2, (image.height() - crop_height) / 2,
                  crop_width, crop_height)
    result = QImage(size, QImage.Format.Format_RGBA8888)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(result.rect()), image, crop)
    painter.end()
    pixels = Image.frombytes("RGBA", (size.width(), size.height()), bytes(result.constBits()))
    pixels = pixels.filter(ImageFilter.GaussianBlur(8))
    return QImage(pixels.tobytes(), size.width(), size.height(), QImage.Format.Format_RGBA8888).copy()


def cinema_bar_height(style):
    return min(88, round(style.height * .08))


def paint_notebook_rules(painter, style, body_rect, document, lyrics_y, accent):
    """Rule the page from the fitted text's baselines, including wrapped rows."""
    rows = []
    if isinstance(document, QTextDocument):
        block = document.begin()
        while block.isValid():
            layout = block.layout()
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                rows.append(lyrics_y + layout.position().y() + line.y() + line.ascent() + 2)
            block = block.next()
    pitch = QFontMetricsF(document.defaultFont()).lineSpacing() * style.line_spacing / 100
    if not rows:
        # Vertical writing retains a ruled page without horizontal text baselines.
        rows = [body_rect.top()]
    leading = rows[1] - rows[0] if len(rows) > 1 else pitch
    trailing = rows[-1] - rows[-2] if len(rows) > 1 else pitch
    y = rows[0] - max(1, leading)
    while y >= body_rect.top():
        rows.insert(0, y)
        y -= max(1, leading)
    y = rows[-1] + max(1, trailing)
    while y <= body_rect.bottom():
        rows.append(y)
        y += max(1, trailing)
    color = QColor(accent)
    color.setAlpha(75)
    painter.save()
    painter.setClipRect(body_rect)
    painter.setPen(QPen(color, 1))
    for y in rows:
        y = round(y) + .5  # Keep each one-pixel rule crisp at export resolution.
        painter.drawLine(QPointF(body_rect.left(), y), QPointF(body_rect.right(), y))
    painter.restore()


def paint_new_template(painter, style, colors, body_rect, photo_rect, cover):
    background, end, _, _, _, accent = colors
    border = QColor(accent)
    border.setAlpha(75)
    if style.template == "Polaroid":
        frame = QRectF(32, 32, style.width - 64, style.height - 64)
        painter.fillRect(frame.translated(8, 10), QColor(0, 0, 0, 45))
        painter.fillRect(frame, QColor(background))
        painter.setPen(QPen(border, 2))
        painter.drawRect(frame)
        painter.save()
        painter.setClipRect(photo_rect)
        gradient = QLinearGradient(photo_rect.topLeft(), photo_rect.bottomRight())
        gradient.setColorAt(0, QColor(end))
        gradient.setColorAt(1, QColor(accent))
        painter.fillRect(photo_rect, gradient)
        if cover is not None:
            painter.drawImage(photo_rect, _blurred_cover(cover, photo_rect.size()))
            wash = QColor(background)
            wash.setAlpha(155)
            painter.fillRect(photo_rect, wash)
        else:
            glow = QColor(background)
            glow.setAlpha(90)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            radius = photo_rect.height() * .55
            painter.drawEllipse(photo_rect.center(), radius, radius)
        painter.restore()
        painter.setPen(QPen(border, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(photo_rect)
    elif style.template == "Editorial":
        painter.setPen(QPen(border, 2))
        painter.drawLine(style.padding, int(body_rect.top() - 24),
                         style.width - style.padding, int(body_rect.top() - 24))
        painter.fillRect(QRectF(style.padding + 12, body_rect.top() + 12,
                                4, min(120, body_rect.height() - 24)), QColor(accent))
    elif style.template == "Cinema":
        if cover is not None and style.background_mode == "Gradient":
            painter.drawImage(QRectF(0, 0, style.width, style.height),
                              _blurred_cover(cover, QRectF(0, 0, style.width, style.height).size()))
            wash = QColor(background)
            wash.setAlpha(170)
            painter.fillRect(QRectF(0, 0, style.width, style.height), wash)
        shade = QLinearGradient(0, 0, 0, style.height)
        for stop, alpha in ((0, 230), (.35, 25), (1, 220)):
            color = QColor(background)
            color.setAlpha(alpha)
            shade.setColorAt(stop, color)
        painter.fillRect(QRectF(0, 0, style.width, style.height), shade)
        bar = cinema_bar_height(style)
        painter.fillRect(QRectF(0, 0, style.width, bar), QColor(background))
        painter.fillRect(QRectF(0, style.height - bar, style.width, bar), QColor(background))
        painter.setPen(QPen(border, 2))
        painter.drawLine(0, bar, style.width, bar)
        painter.drawLine(0, style.height - bar, style.width, style.height - bar)
    elif style.template == "Notebook":
        # Keep the margin and binding outside the lyric and metadata boxes.
        painter.setPen(QPen(QColor(accent), 2))
        margin_x = max(40, style.padding - 20)
        painter.drawLine(margin_x, 40, margin_x, style.height - 40)
        painter.setPen(QPen(border, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for y in range(72, style.height - 48, 96):
            painter.drawEllipse(QRectF(20, y, 12, 12))
    elif style.template == "Ticket":
        painter.setPen(QPen(border, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(24, 24, style.width - 48, style.height - 48), 16, 16)
        # The perforation sits in the gap above the footer's song details.
        perforation_y = body_rect.bottom() + 32
        pen = QPen(QColor(accent), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(style.padding, round(perforation_y),
                         style.width - style.padding, round(perforation_y))
        painter.setPen(QPen(QColor(accent), 2))
        for x in (24, style.width - 24):
            painter.drawEllipse(QRectF(x - 12, perforation_y - 12, 24, 24))
        # Small registration bars give the footer a printed ticket treatment.
        for index, height in enumerate((16, 24, 12, 24, 20, 12, 24, 16)):
            painter.fillRect(QRectF(style.width / 2 - 30 + index * 8,
                                    style.height - 36, 3, height / 2), QColor(accent))
    elif style.template == "Art Deco":
        painter.setPen(QPen(QColor(accent), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(24, 24, style.width - 48, style.height - 48))
        painter.setPen(QPen(border, 1))
        painter.drawRect(QRectF(36, 36, style.width - 72, style.height - 72))
        # Stepped corner ornaments stay within the minimum card padding.
        painter.setPen(QPen(QColor(accent), 2))
        for x, direction_x in ((24, 1), (style.width - 24, -1)):
            for y, direction_y in ((24, 1), (style.height - 24, -1)):
                for inset in (0, 6, 12):
                    corner_x = x + direction_x * inset
                    corner_y = y + direction_y * inset
                    length = 72 - inset * 2
                    painter.drawLine(round(corner_x), round(corner_y),
                                     round(corner_x + direction_x * length), round(corner_y))
                    painter.drawLine(round(corner_x), round(corner_y),
                                     round(corner_x), round(corner_y + direction_y * length))
    elif style.template == "Streaming":
        panel = QColor(accent)
        panel.setAlpha(22)
        painter.setPen(QPen(border, 2))
        painter.setBrush(panel)
        painter.drawRoundedRect(QRectF(24, 24, style.width - 48, style.height - 48), 36, 36)
        # A compact waveform separates the excerpt from its song attribution.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        for index, height in enumerate((8, 16, 28, 18, 36, 24, 12)):
            painter.drawRoundedRect(QRectF(style.padding + index * 10,
                                          body_rect.bottom() + 32 - height / 2, 4, height), 2, 2)
    elif style.template == "Receipt":
        # Cut-paper edges and print rules leave custom fills visible.
        painter.setPen(QPen(QColor(accent), 2))
        for y, direction in ((24, 1), (style.height - 24, -1)):
            edge = QPainterPath(QPointF(24, y))
            for x in range(24, style.width - 24, 24):
                edge.lineTo(min(x + 12, style.width - 24), y + direction * 10)
                edge.lineTo(min(x + 24, style.width - 24), y)
            painter.drawPath(edge)
        painter.setPen(QPen(border, 2, Qt.PenStyle.DashLine))
        painter.drawLine(style.padding, round(body_rect.top() - 24),
                         style.width - style.padding, round(body_rect.top() - 24))
        painter.drawLine(style.padding, style.height - 44, style.width - style.padding, style.height - 44)
        # Decorative bars are deliberately abstract, rather than a scannable code.
        x = style.width / 2 - 100
        for index in range(40):
            width = 1 + (index * 7 % 3)
            painter.fillRect(QRectF(x, style.height - 72, width, 18), QColor(accent))
            x += width + 3
    elif style.template == "Mixtape":
        tape = QRectF(photo_rect.center().x() - min(260, photo_rect.width() / 2),
                      photo_rect.top(), min(520, photo_rect.width()), photo_rect.height())
        tint = QColor(accent)
        tint.setAlpha(24)
        painter.setPen(QPen(QColor(accent), 3))
        painter.setBrush(tint)
        painter.drawRoundedRect(tape, 14, 14)
        window = tape.adjusted(28, 30, -28, -32)
        painter.setPen(QPen(border, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(window, 28, 28)
        painter.drawLine(QPointF(window.left() + 54, window.center().y()),
                         QPointF(window.right() - 54, window.center().y()))
        for x in (window.left() + 40, window.right() - 40):
            center = QPointF(x, window.center().y())
            painter.setPen(QPen(QColor(accent), 3))
            painter.drawEllipse(center, 24, 24)
            painter.drawEllipse(center, 8, 8)
        painter.setPen(QPen(border, 2))
        for x in (tape.left() + 12, tape.right() - 12):
            for y in (tape.top() + 12, tape.bottom() - 12):
                painter.drawEllipse(QPointF(x, y), 2, 2)
        painter.drawLine(QPointF(tape.center().x() - 60, tape.bottom() - 14),
                         QPointF(tape.center().x() + 60, tape.bottom() - 14))
