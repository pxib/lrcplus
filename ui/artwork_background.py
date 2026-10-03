from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QBuffer, QIODevice, QByteArray, QSize
from PySide6.QtGui import QColor, QLinearGradient, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None
    ImageFilter = None

PIL_AVAILABLE = Image is not None and ImageFilter is not None


def extract_embedded_cover(path):
    """Return embedded cover bytes, or None when the track has no artwork."""
    if MutagenFile is None:
        return None

    try:
        audio = MutagenFile(Path(path))
    except Exception as error:
        print(f"[Background] Could not read artwork: {error!r}")
        return None

    if audio is None:
        return None

    data = None

    # FLAC and other formats exposing a pictures collection.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        data = pictures[0].data

    tags = getattr(audio, "tags", None)

    # MP3 / ID3 APIC.
    if data is None and tags:
        try:
            apics = tags.getall("APIC")
        except Exception:
            apics = []

        if apics:
            data = apics[0].data

    # M4A / MP4 covr.
    if data is None and tags and "covr" in tags and tags["covr"]:
        data = bytes(tags["covr"][0])

    return data


def load_embedded_cover_thumbnail(path, size=QSize(52, 52)):
    """Decode embedded artwork directly near thumbnail size.

    This avoids temporarily expanding huge album covers when the library only
    needs a tiny icon.
    """
    data = extract_embedded_cover(path)
    if not data:
        return QPixmap()
    image_data = QByteArray(data)
    buffer = QBuffer(image_data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return QPixmap()
    reader = QImageReader(buffer)
    original = reader.size()
    if original.isValid() and size.isValid():
        target = original.scaled(size, Qt.AspectRatioMode.KeepAspectRatio)
        if target.isValid() and target != original:
            reader.setScaledSize(target)
    image = reader.read()
    buffer.close()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


class ArtworkBackgroundWidget(QWidget):
    """
    A reusable lyrics background.

    It paints album artwork behind its child widgets using a cover-fill crop,
    then applies a blur and dark overlay so foreground lyrics remain readable.
    """

    def __init__(self, content=None, parent=None):
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self._cached_background = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._background_enabled = True
        self._blur_mode = "quality"
        self._blur_bleed = False
        self._blur_radius = 12
        self._darkness = 155

        if content is not None:
            content.setParent(self)
            content.raise_()

    def set_content(self, content):
        content.setParent(self)
        content.setGeometry(self.rect())
        content.raise_()

    def set_artwork_bytes(self, data):
        pixmap = QPixmap()

        if data and pixmap.loadFromData(data):
            self._source_pixmap = pixmap
            self._rebuild_background()
            self.update()
            return True

        self.clear_artwork()
        return False

    def set_artwork_pixmap(self, pixmap):
        self._source_pixmap = QPixmap(pixmap)
        self._rebuild_background()
        self.update()

    def clear_artwork(self):
        self._source_pixmap = QPixmap()
        self._cached_background = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.update()

    def set_background_enabled(self, enabled):
        self._background_enabled = bool(enabled)
        self._rebuild_background()
        self.update()

    def set_blur_mode(self, mode):
        mode = str(mode or "quality").lower()
        if mode not in ("quality", "low_quality"):
            mode = "quality"

        if self._blur_mode != mode:
            self._blur_mode = mode
            self._rebuild_background()
            self.update()

    def set_blur_bleed(self, enabled):
        enabled = bool(enabled)
        if self._blur_bleed != enabled:
            self._blur_bleed = enabled
            self._rebuild_background()
            self.update()

    def set_blur_radius(self, radius):
        self._blur_radius = max(0, int(radius))
        self._rebuild_background()
        self.update()

    def set_darkness(self, darkness):
        self._darkness = max(0, min(255, int(darkness)))
        self._rebuild_background()
        self.update()

    def resizeEvent(self, event):
        for child in self.findChildren(
            QWidget,
            options=Qt.FindChildOption.FindDirectChildrenOnly,
        ):
            child.setGeometry(self.rect())

        # Rebuild only when the available background size changes.
        self._rebuild_background()
        super().resizeEvent(event)

    def _rebuild_background(self):
        """
        Build a ready-to-paint cover image once.

        This is cached, so lyric repainting never rescales or processes the
        artwork. The previous multi-copy blur approximation is intentionally
        removed here because its alpha compositing could produce a nearly
        black result on some Qt paint backends.
        """
        if self.size().isEmpty() or self._source_pixmap.isNull():
            self._cached_background = QPixmap()
            return

        # Keep the entire cover visible. Unlike KeepAspectRatioByExpanding,
        # this never crops or zooms into the artwork.
        scaled = self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Blur exactly once while rebuilding the cached background.
        if self._blur_radius > 0:
            if self._blur_mode == "quality":
                if not PIL_AVAILABLE:
                    raise RuntimeError(
                        "High quality blur requires Pillow (PIL), but it is "
                        "not installed. Install it with: pip install Pillow"
                    )

                image = scaled.toImage().convertToFormat(
                    QImage.Format.Format_RGBA8888
                )

                width = image.width()
                height = image.height()

                raw = image.bits().tobytes()
                pil_image = Image.frombytes(
                    "RGBA",
                    (width, height),
                    raw,
                )

                blur_radius = max(
                    0.0,
                    float(self._blur_radius),
                )

                if self._blur_bleed:
                    # Give the Gaussian blur transparent space outside the
                    # original artwork so the soft edges can spread outward.
                    # Three radii is enough room for the Gaussian tail.
                    padding = max(
                        1,
                        int(round(blur_radius * 3)),
                    )

                    canvas = Image.new(
                        "RGBA",
                        (
                            width + padding * 2,
                            height + padding * 2,
                        ),
                        (0, 0, 0, 0),
                    )
                    canvas.paste(
                        pil_image,
                        (padding, padding),
                    )

                    blurred_image = canvas.filter(
                        ImageFilter.GaussianBlur(
                            radius=blur_radius
                        )
                    )

                    qimage = QImage(
                        blurred_image.tobytes(),
                        blurred_image.width,
                        blurred_image.height,
                        QImage.Format.Format_RGBA8888,
                    ).copy()

                    scaled = QPixmap.fromImage(qimage)
                    print(
                        f"[Background] High quality Gaussian blur with "
                        f"edge bleed applied (radius={self._blur_radius})"
                    )

                else:
                    # Normal Gaussian blur remains inside the artwork bounds.
                    blurred_image = pil_image.filter(
                        ImageFilter.GaussianBlur(
                            radius=blur_radius
                        )
                    )

                    qimage = QImage(
                        blurred_image.tobytes(),
                        width,
                        height,
                        QImage.Format.Format_RGBA8888,
                    ).copy()

                    scaled = QPixmap.fromImage(qimage)
                    print(
                        f"[Background] High quality Gaussian blur applied "
                        f"(radius={self._blur_radius})"
                    )

            else:
                # SHRINK-O-VISION™: intentionally low-quality blur.
                # This mode remains contained because downsample/upscale
                # cannot create transparent Gaussian edge bleed.
                factor = max(0.12, 1.0 - (self._blur_radius / 34.0))
                small_size = scaled.size() * factor
                small = scaled.scaled(
                    small_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled = small.scaled(
                    scaled.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                print(
                    f"[Background] Low quality blur applied "
                    f"(radius={self._blur_radius})"
                )

        background = QPixmap(self.size())
        background.fill(Qt.GlobalColor.black)

        painter = QPainter(background)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.fillRect(
            background.rect(),
            QColor(0, 0, 0, self._darkness),
        )
        painter.end()

        self._cached_background = background

    def paintEvent(self, event):
        painter = QPainter(self)

        if not self._background_enabled or self._source_pixmap.isNull():
            # Intentional fallback instead of the old empty grey rectangle.
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0.0, QColor("#20242c"))
            gradient.setColorAt(0.55, QColor("#16191f"))
            gradient.setColorAt(1.0, QColor("#0d0f13"))
            painter.fillRect(self.rect(), gradient)
            painter.end()
            return

        if self._cached_background.isNull():
            self._rebuild_background()

        if not self._cached_background.isNull():
            painter.drawPixmap(0, 0, self._cached_background)

        painter.end()
