from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QBuffer, QIODevice, QByteArray, QSize
from PySide6.QtGui import QColor, QLinearGradient, QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

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


def compact_background_artwork(data):
    """Keep a bounded display copy; never change the original audio/artwork file."""
    if not data:
        return data
    image_data = QByteArray(data)
    buffer = QBuffer(image_data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return data
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    original = reader.size()
    if not original.isValid():
        return data
    limit = QSize(1024, 1024)
    oversized = original.width() > limit.width() or original.height() > limit.height()
    if not oversized and len(data) <= 512 * 1024:
        return data
    if oversized:
        reader.setScaledSize(original.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return data
    # Enforce the cap even for image plugins that ignore scaled decoding.
    if image.width() > limit.width() or image.height() > limit.height():
        image = image.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    encoded = QByteArray()
    output = QBuffer(encoded)
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    # Preserve transparency; opaque playback backgrounds use a compact JPEG.
    if not image.save(output, "PNG" if image.hasAlphaChannel() else "JPEG", 85):
        return data
    return bytes(encoded)


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

    It lays out album artwork behind its child widgets, then applies a blur
    and dark overlay so foreground lyrics remain readable.
    """

    def __init__(self, content=None, parent=None, *, defer_hidden_rendering=False):
        super().__init__(parent)
        self._defer_hidden_rendering = defer_hidden_rendering
        self._background_cache_key = None
        self._source_pixmap = QPixmap()
        self._source_decode_key = None
        self._artwork_bytes = None
        self._cached_background = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._background_enabled = True
        self._background_layout = "fit"
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
        data = compact_background_artwork(data)
        if self._rendering_deferred():
            # Inspect the header without expanding the image into pixels.
            image_data = QByteArray(data or b"")
            buffer = QBuffer(image_data)
            if buffer.open(QIODevice.OpenModeFlag.ReadOnly) and QImageReader(buffer).canRead():
                self._artwork_bytes = data
                self._source_pixmap = QPixmap()
                self._source_decode_key = None
                self._rebuild_background()
                self.update()
                return True
            self.clear_artwork()
            return False
        pixmap = self._decode_artwork_bytes(data)
        if not pixmap.isNull():
            self._artwork_bytes = data
            self._source_pixmap = pixmap
            self._source_decode_key = self._decode_key()
            self._rebuild_background()
            self.update()
            return True

        self.clear_artwork()
        return False

    def _rendering_deferred(self):
        return self._defer_hidden_rendering and (not self.isVisible() or not self._background_enabled)

    def _decode_key(self):
        if self._background_layout in ("center", "tile"):
            return (self._background_layout,)
        return (self._background_layout, self.width(), self.height(), self.devicePixelRatioF())

    def _decode_artwork_bytes(self, data):
        pixmap = QPixmap()

        if data:
            image_data = QByteArray(data)
            buffer = QBuffer(image_data)
            if buffer.open(QIODevice.OpenModeFlag.ReadOnly):
                reader = QImageReader(buffer)
                reader.setAutoTransform(True)
                original_size = reader.size()
                # Decode for the actual display density, rather than always
                # retaining twice the viewport resolution on ordinary screens.
                density = max(1.0, self.devicePixelRatioF())
                max_size = QSize(
                    max(1, round(self.width() * density)),
                    max(1, round(self.height() * density)),
                )
                if (
                    original_size.isValid()
                    and self._background_layout not in ("center", "tile")
                    and (
                        original_size.width() > max_size.width()
                        or original_size.height() > max_size.height()
                    )
                ):
                    if self._background_layout == "stretch":
                        scaled_size = QSize(
                            min(original_size.width(), max_size.width()),
                            min(original_size.height(), max_size.height()),
                        )
                    else:
                        aspect_mode = (
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding
                            if self._background_layout == "fill"
                            else Qt.AspectRatioMode.KeepAspectRatio
                        )
                        scaled_size = original_size.scaled(max_size, aspect_mode)
                        # Fill can request an upscale on the shorter side.
                        # Keep the original rather than expanding during decode.
                        if scaled_size.width() > original_size.width():
                            scaled_size = original_size
                    if scaled_size.isValid() and scaled_size != original_size:
                        reader.setScaledSize(scaled_size)
                image = reader.read()
                buffer.close()
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)

        return pixmap

    def set_artwork_pixmap(self, pixmap):
        self._artwork_bytes = None
        self._source_decode_key = None
        self._source_pixmap = QPixmap(pixmap)
        self._rebuild_background()
        self.update()

    def clear_artwork(self):
        self._artwork_bytes = None
        self._source_decode_key = None
        self._source_pixmap = QPixmap()
        self._cached_background = QPixmap()
        self._background_cache_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.update()

    def set_background_enabled(self, enabled):
        self._background_enabled = bool(enabled)
        self._rebuild_background()
        self.update()

    def set_background_layout(self, mode):
        mode = str(mode or "fit").lower()
        if mode not in ("center", "fill", "fit", "stretch", "tile"):
            mode = "fit"
        if self._background_layout != mode:
            self._background_layout = mode
            # Center and Tile need the image's original dimensions, even if
            # it was previously decoded smaller for a scaled layout.
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

    def _artwork_rect(self):
        size = self.size()
        if self._background_layout == "center":
            size = self._source_pixmap.size()
        elif self._background_layout == "fit":
            size = self._source_pixmap.size().scaled(size, Qt.AspectRatioMode.KeepAspectRatio)
            size = size.expandedTo(QSize(1, 1))
        return QRectF(
            (self.width() - size.width()) // 2,
            (self.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )

    def _paint_artwork(self, painter, target):
        if self._background_layout == "tile":
            painter.drawTiledPixmap(target, self._source_pixmap)
            return

        source = QRectF(self._source_pixmap.rect())
        if self._background_layout == "fill":
            # Crop in source coordinates so extreme aspect ratios never need
            # an oversized intermediate image.
            ratio = max(target.width() / source.width(), target.height() / source.height())
            width = target.width() / ratio
            height = target.height() / ratio
            source = QRectF((source.width() - width) / 2, (source.height() - height) / 2,
                            width, height)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target, self._source_pixmap, source)

    def _blur_artwork(self, target, scale):
        size = (target.size().toSize() * scale).expandedTo(QSize(1, 1))
        image = QImage(size, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.scale(size.width() / target.width(), size.height() / target.height())
        self._paint_artwork(painter, QRectF(0, 0, target.width(), target.height()))
        painter.end()

        if self._blur_mode == "low_quality":
            factor = max(0.12, 1.0 - (self._blur_radius / 34.0))
            small_size = (size * factor).expandedTo(QSize(1, 1))
            return image.scaled(small_size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation), target

        try:
            from PIL import Image, ImageFilter
        except ImportError as error:
            raise RuntimeError(
                "High quality blur requires Pillow (PIL), but it is "
                "not installed. Install it with: pip install Pillow"
            ) from error

        radius = self._blur_radius * scale
        pixels = Image.frombuffer(
            "RGBA", (size.width(), size.height()), image.constBits(), "raw", "RGBA", 0, 1,
        )
        if self._blur_bleed:
            # Leave room for the Gaussian tail and map the padding back to the
            # original layout coordinates when compositing.
            padding = max(1, round(radius * 3))
            canvas = Image.new("RGBA", (size.width() + 2 * padding, size.height() + 2 * padding))
            canvas.paste(pixels, (padding, padding))
            pixels = canvas
            dx = padding * target.width() / size.width()
            dy = padding * target.height() / size.height()
            target = target.adjusted(-dx, -dy, dx, dy)

        blurred = pixels.filter(ImageFilter.GaussianBlur(radius))
        return QImage(blurred.tobytes(), blurred.width, blurred.height,
                      QImage.Format.Format_RGBA8888), target

    def _rebuild_background(self):
        if self._rendering_deferred():
            self._cached_background = QPixmap()
            self._background_cache_key = None
            if self._artwork_bytes:
                self._source_pixmap = QPixmap()
                self._source_decode_key = None
            return
        if self._artwork_bytes and self._source_decode_key != self._decode_key():
            self._source_pixmap = self._decode_artwork_bytes(self._artwork_bytes)
            self._source_decode_key = self._decode_key()
        if self.size().isEmpty() or self._source_pixmap.isNull():
            self._cached_background = QPixmap()
            self._background_cache_key = None
            return
        key = (self._source_pixmap.cacheKey(), self.width(), self.height(),
               self._background_layout, self._blur_mode, self._blur_bleed,
               self._blur_radius, self._darkness)
        if key == self._background_cache_key and not self._cached_background.isNull():
            return

        self._cached_background = QPixmap()
        target = self._artwork_rect()
        # Keep subtle blurs at full resolution. Stronger Gaussian blurs can
        # render and cache at half resolution with a matching blur radius.
        scale = 0.5 if self._blur_mode == "quality" and self._blur_radius >= 6 else 1.0
        background_size = (self.size() * scale).expandedTo(QSize(1, 1))
        blurred = QImage()
        if self._blur_radius and not target.isEmpty():
            blurred, target = self._blur_artwork(target, scale)

        background = QPixmap(background_size)
        background.fill(Qt.GlobalColor.black)
        painter = QPainter(background)
        painter.scale(background.width() / self.width(), background.height() / self.height())
        if not blurred.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, blurred)
        elif not target.isEmpty():
            self._paint_artwork(painter, target)
        painter.fillRect(self.rect(), QColor(0, 0, 0, self._darkness))
        painter.end()

        self._cached_background = background
        self._background_cache_key = key

    def showEvent(self, event):
        super().showEvent(event)
        if self._defer_hidden_rendering:
            self._rebuild_background()

    def hideEvent(self, event):
        if self._defer_hidden_rendering:
            self._cached_background = QPixmap()
            self._background_cache_key = None
            if self._artwork_bytes:
                self._source_pixmap = QPixmap()
                self._source_decode_key = None
        super().hideEvent(event)

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
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), self._cached_background)

        painter.end()
