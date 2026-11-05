"""Code-painted surfaces for photo, print, and cinema lyric cards."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen


def paint_cover(painter, rect, image):
    fitted = image.size().scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
    target = QRectF(rect.center().x() - fitted.width() / 2,
                    rect.center().y() - fitted.height() / 2, fitted.width(), fitted.height())
    painter.drawImage(target, image)


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
