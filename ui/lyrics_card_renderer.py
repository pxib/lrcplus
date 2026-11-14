"""Full-resolution lyric card layout, text fitting and background rendering."""

from dataclasses import dataclass, replace
from functools import lru_cache
import math
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QFontMetricsF, QImage, QLinearGradient,
    QImageReader, QPainter, QPen, QTextDocument, QTextOption, QTextBlockFormat, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import QApplication

from core.fonts import library_font
from ui.lyrics_card_text import CardLyrics, apply_character_styles, apply_block_quotes, quote_bar_rects
from ui.lyrics_card_ruby import ruby_layout_source, install_ruby_objects, clone_ruby_handler
from ui.lyrics_card_decorations import undecorated_document, paint_decorations
from ui.lyrics_card_templates import cinema_bar_height, paint_cover, paint_new_template, paint_notebook_rules
from ui.lyrics_card_vertical import VerticalLyrics, fit_vertical_lyrics


CARD_SIZES = {
    "Square · 1080 × 1080": (1080, 1080),
    "Portrait · 1080 × 1350": (1080, 1350),
    "Landscape · 1920 × 1080": (1920, 1080),
    "Story · 1080 × 1920": (1080, 1920),
}
CARD_QUALITIES = {"Standard · 1×": 1, "High · 2×": 2, "Ultra · 3×": 3}
CARD_THEMES = {
    "Midnight": ("#101827", "#263750", "#f3f6fc", "#afbed3", "#7dd3fc"),
    "Paper": ("#faf5eb", "#eae1d0", "#292722", "#665d50", "#986b35"),
    "Rose": ("#331f35", "#69455c", "#fff1f5", "#e6bdce", "#fda4af"),
}
CARD_TEMPLATES = ("Classic", "Spotlight", "Record sleeve", "Polaroid", "Editorial", "Cinema",
                  "Notebook", "Ticket", "Art Deco", "Streaming", "Receipt", "Mixtape")
WATERMARK_POSITIONS = ("bottom_right", "bottom_left", "top_right", "top_left")
DETAILS_PLACEMENTS = {"Below title": "below_title", "Beside cover": "beside_cover"}
WRITING_MODES = {"Horizontal": "horizontal", "Vertical · right to left": "vertical_rl",
                 "Vertical · left to right": "vertical_lr"}
RUBY_POSITIONS = ("auto", "above", "below", "right", "left")
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "media" / "logo.png"
# Always leave room for the widest outline, including when effects are off.
# Painting settings must not change line wrapping, fitting, or block placement.
TEXT_PAINT_PADDING = 12


@dataclass(frozen=True)
class CardStyle:
    width: int = 1080
    height: int = 1080
    theme: str = "Midnight"
    font_family: str = ""
    font_size: int = 56
    centered: bool = False
    title: str = ""
    credit: str = ""
    album: str = ""
    details_placement: str = "below_title"
    template: str = "Classic"
    colors: tuple[str, ...] = ()
    background_mode: str = "Gradient"
    gradient_angle: int = 45
    image_path: str = ""
    image_blur: int = 0
    image_dim: int = 35
    image_zoom: int = 100
    crop_x: int = 50
    crop_y: int = 50
    padding: int = 84
    line_spacing: int = 100
    vertical_position: int = 50
    show_album_cover: bool = False
    cover_size: int = 160
    # An empty alignment keeps older centered-only styles compatible.
    alignment: str = ""
    writing_mode: str = "horizontal"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    stroke_enabled: bool = False
    stroke_color: str = "#000000"
    stroke_width: int = 2
    shadow_enabled: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: int = 60
    shadow_blur: int = 8
    shadow_offset_x: int = 4
    shadow_offset_y: int = 4
    watermark_enabled: bool = False
    watermark_width: int = 180
    watermark_opacity: int = 45
    watermark_position: str = "bottom_right"
    preserve_lrcx_formatting: bool = True
    include_furigana: bool = True
    furigana_scale: int = 50
    ruby_position: str = "auto"
    ruby_gap: int = 0
    ruby_color: str = ""
    ruby_inherit_stroke: bool = True
    ruby_inherit_shadow: bool = True
    title_font_family: str = ""
    title_font_size: int = 0
    title_color: str = ""
    credit_font_family: str = ""
    credit_font_size: int = 0
    credit_color: str = ""
    export_scale: int = 1
    cover_radius: int = 0
    cover_border_width: int = 0
    cover_border_color: str = "#ffffff"
    cover_shadow_enabled: bool = False
    cover_shadow_opacity: int = 35
    cover_shadow_blur: int = 8
    cover_shadow_offset_x: int = 0
    cover_shadow_offset_y: int = 8
    cover_placement: str = "auto"


def _document(text, family, pixels, width, centered=False, line_spacing=100, lyric_style=None, source=None):
    words = []
    if source is not None and source.readings and lyric_style is not None and lyric_style.include_furigana:
        source, words = ruby_layout_source(source)
        text = source.text
    document = QTextDocument()
    document.setDocumentMargin(0)
    font = QFont(family) if family else library_font(QApplication.font())
    font.setPixelSize(pixels)
    if lyric_style is not None:
        font.setBold(lyric_style.bold)
        font.setItalic(lyric_style.italic)
        font.setUnderline(lyric_style.underline)
        font.setStrikeOut(lyric_style.strikeout)
    document.setDefaultFont(font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    alignment = lyric_style.alignment if lyric_style is not None else ""
    option.setAlignment({"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignCenter,
                         "right": Qt.AlignmentFlag.AlignRight}.get(
                             alignment, Qt.AlignmentFlag.AlignCenter if centered else Qt.AlignmentFlag.AlignLeft))
    document.setDefaultTextOption(option)
    # Plain text preserves literal markup, Unicode and explicit line breaks.
    document.setPlainText(text)
    if source is not None:
        apply_character_styles(document, source, font)
    if lyric_style is not None and lyric_style.stroke_enabled:
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        character_format = QTextCharFormat()
        pen = QPen(QColor(lyric_style.stroke_color), lyric_style.stroke_width * 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        character_format.setTextOutline(pen)
        cursor.mergeCharFormat(character_format)
    inline_fonts = {(bool(span[2]), bool(span[3])) for span in source.styles} if source is not None else set()
    styled_bearings = lyric_style is not None and (lyric_style.bold or lyric_style.italic or any(b or i for b, i in inline_fonts))
    if line_spacing != 100 or styled_bearings:
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(line_spacing, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        if styled_bearings:
            # Synthetic italics can extend beyond their advance width. Leave
            # room for the actual ink before wrapping and aligning the text.
            glyphs = {char for char in text if not char.isspace() and char not in "\ufffc\u200b"}
            left = right = 0.0
            for bold, italic in {(False, False), *inline_fonts}:
                variant = QFont(font)
                variant.setBold(font.bold() or bold)
                variant.setItalic(font.italic() or italic)
                metrics = QFontMetricsF(variant)
                for char in glyphs:
                    bounds = metrics.boundingRect(char)
                    left = max(left, -bounds.left())
                    right = max(right, bounds.right() - metrics.horizontalAdvance(char))
            block_format.setLeftMargin(math.ceil(left))
            block_format.setRightMargin(math.ceil(right))
        cursor.mergeBlockFormat(block_format)
    if source is not None:
        apply_block_quotes(document, source, max(14, math.ceil(QFontMetricsF(font).height() / 2)))
    if words:
        inline_style = replace(lyric_style, alignment="left", centered=False, stroke_enabled=False, include_furigana=False)
        def make_inline(contents, size):
            child = _document(contents.text, family, size, 1_000_000, lyric_style=inline_style, source=contents)
            child.setTextWidth(math.ceil(child.idealWidth()))
            return child
        install_ruby_objects(document, words, font, lyric_style.furigana_scale, make_inline, lyric_style)
    document.setTextWidth(width)
    return document


def _effect_insets(style):
    stroke = style.stroke_width if style.stroke_enabled else 0
    left = top = right = bottom = stroke
    if style.shadow_enabled and style.shadow_opacity:
        spread = stroke + style.shadow_blur * 3
        left = max(left, spread - style.shadow_offset_x)
        right = max(right, spread + style.shadow_offset_x)
        top = max(top, spread - style.shadow_offset_y)
        bottom = max(bottom, spread + style.shadow_offset_y)
    return left, top, right, bottom


def _paint_document(painter, document, x, y, color, quote_bars=True, part="all"):
    painter.save()
    painter.translate(x, y)
    handler = getattr(document, "_card_ruby_handler", None)
    if part == "ruby":
        if handler is not None:
            handler.paint_readings(painter, document, color)
            paint_decorations(painter, document, QColor(color), band="reading")
        painter.restore()
        return
    if quote_bars:
        bar_color = QColor(color)
        bar_color.setAlpha(round(bar_color.alpha() * .5))
        for rect in quote_bar_rects(document):
            painter.fillRect(rect, bar_color)
    context = QAbstractTextDocumentLayout.PaintContext()
    context.palette.setColor(context.palette.ColorRole.Text, QColor(color))
    if handler is not None:
        handler.color = QColor(color)
        handler.paint_part = part
    undecorated_document(document).documentLayout().draw(painter, context)
    paint_decorations(painter, document, QColor(color), band="base" if part == "base" else None)
    if handler is not None:
        handler.paint_part = "all"
    painter.restore()


def _paint_lyrics(painter, document, x, y, color, style):
    fill_document = None
    if style.stroke_enabled and not isinstance(document, VerticalLyrics):
        # Qt centers the outline on the glyph edge. Repaint the fill above
        # it so a thick stroke cannot cover narrow letter stems.
        fill_document = document.clone()
        clone_ruby_handler(document, fill_document)
        cursor = QTextCursor(fill_document)
        cursor.select(QTextCursor.SelectionType.Document)
        character_format = QTextCharFormat()
        character_format.setTextOutline(QPen(Qt.PenStyle.NoPen))
        cursor.mergeCharFormat(character_format)

    def paint_glyphs(target, part="all"):
        if isinstance(document, VerticalLyrics):
            document.paint(target, x, y, color, style, part)
            return
        _paint_document(target, document, x, y, color, part=part)
        if fill_document is not None:
            if part == "ruby" and not style.ruby_inherit_stroke:
                return
            fill_part = "base" if part == "all" and not style.ruby_inherit_stroke else part
            _paint_document(target, fill_document, x, y, color, quote_bars=False, part=fill_part)

    if not style.shadow_enabled or not style.shadow_opacity:
        paint_glyphs(painter)
        return
    from PIL import Image, ImageFilter
    scale = style.export_scale
    width, height = style.width * scale, style.height * scale
    layer = QImage(width, height, QImage.Format.Format_RGBA8888)
    layer.fill(Qt.GlobalColor.transparent)
    layer_painter = QPainter(layer)
    layer_painter.scale(scale, scale)
    layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    layer_painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    paint_glyphs(layer_painter, "all" if style.ruby_inherit_shadow else "base")
    layer_painter.end()
    # Blur the alpha mask so all scripts, decorations and the stroke cast
    # the same shadow. Its color stays independent of the lyric fill.
    pixels = Image.frombuffer("RGBA", (width, height), layer.constBits(), "raw", "RGBA", 0, 1)
    alpha = pixels.getchannel("A")
    if style.shadow_blur:
        alpha = alpha.filter(ImageFilter.GaussianBlur(style.shadow_blur * scale))
    alpha = alpha.point([round(value * style.shadow_opacity / 100) for value in range(256)])
    shadow_color = QColor(style.shadow_color)
    shadow = Image.new("RGBA", pixels.size, (shadow_color.red(), shadow_color.green(), shadow_color.blue(), 0))
    shadow.putalpha(alpha)
    shadow_image = QImage(shadow.tobytes(), width, height, QImage.Format.Format_RGBA8888)
    painter.drawImage(QRectF(style.shadow_offset_x, style.shadow_offset_y, style.width, style.height), shadow_image)
    painter.drawImage(QRectF(0, 0, style.width, style.height), layer)
    if not style.ruby_inherit_shadow:
        paint_glyphs(painter, "ruby")


def _fitted_document(text, style, size, width, height, centered=False, line_spacing=100, styled=False, source=None):
    for pixels in range(size, min(size, 18) - 1, -2):
        document = _document(text, style.font_family, pixels, width, centered, line_spacing,
                             lyric_style=style if styled else None, source=source)
        fits_width = not hasattr(document, "_card_ruby_handler") or document.idealWidth() <= width + .5
        if document.size().height() <= height and fits_width:
            return document
    if source is not None and source.readings:
        raise ValueError("The lyrics or furigana are too long for this card. Check fewer lines, reduce reading size, or choose a larger card.")
    raise ValueError("These lyrics are too long for this card. Check fewer lines or choose a taller card.")


def theme_colors(theme, template):
    background, end, text, muted, accent = CARD_THEMES[theme]
    return (background, end, text, muted if template == "Classic" else text, muted, accent)


@lru_cache(maxsize=2)
def _image_background(path, modified, file_size, width, height, zoom, crop_x, crop_y, blur):
    # Decode once near export size; cache crop/blur independently of text styling.
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    original = reader.size()
    decode_limit = max(3840, width, height)
    if original.isValid() and max(original.width(), original.height()) > decode_limit:
        reader.setScaledSize(original.scaled(QSize(decode_limit, decode_limit), Qt.AspectRatioMode.KeepAspectRatio))
    source = reader.read()
    if source.isNull():
        raise ValueError("Could not read the background image. Choose another image.")
    scale = max(width / source.width(), height / source.height()) * zoom / 100
    crop_width, crop_height = width / scale, height / scale
    crop = QRectF((source.width() - crop_width) * crop_x / 100,
                  (source.height() - crop_height) * crop_y / 100, crop_width, crop_height)
    result = QImage(width, height, QImage.Format.Format_RGBA8888)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(0, 0, width, height), source, crop)
    painter.end()
    if blur:
        from PIL import Image, ImageFilter
        pixels = Image.frombytes("RGBA", (width, height), bytes(result.constBits()))
        pixels = pixels.filter(ImageFilter.GaussianBlur(blur))
        result = QImage(pixels.tobytes(), width, height, QImage.Format.Format_RGBA8888).copy()
    return result


def card_background(style, colors):
    background, gradient_end = colors[:2]
    scale = style.export_scale
    result = QImage(style.width * scale, style.height * scale, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(background))
    painter = QPainter(result)
    painter.scale(scale, scale)
    rect = QRectF(0, 0, style.width, style.height)
    try:
        if style.background_mode == "Gradient":
            angle = math.radians(style.gradient_angle)
            dx, dy = math.cos(angle), math.sin(angle)
            distance = (abs(dx) * style.width + abs(dy) * style.height) / 2
            cx, cy = style.width / 2, style.height / 2
            gradient = QLinearGradient(cx - dx * distance, cy - dy * distance,
                                       cx + dx * distance, cy + dy * distance)
            gradient.setColorAt(0, QColor(background))
            gradient.setColorAt(1, QColor(gradient_end))
            painter.fillRect(rect, gradient)
        elif style.background_mode == "Image":
            try:
                stat = Path(style.image_path).stat() if style.image_path else None
            except OSError:
                stat = None
            if stat is None:
                raise ValueError("The background image is missing. Choose an image or switch to a color background.")
            image = _image_background(style.image_path, stat.st_mtime_ns, stat.st_size,
                                      style.width * scale, style.height * scale, style.image_zoom,
                                      style.crop_x, style.crop_y, style.image_blur * scale)
            painter.drawImage(rect, image)
            painter.fillRect(rect, QColor(0, 0, 0, round(255 * style.image_dim / 100)))
    finally:
        painter.end()
    return result


@lru_cache(maxsize=1)
def _watermark_logo(path, modified, file_size):
    image = QImage(path)
    if image.isNull():
        raise ValueError("Could not load the LRCPlus logo. Turn off the watermark to export this card.")
    return image


def clear_card_image_caches():
    """Release full-resolution buffers when a card workspace is closed.

    Existing previews own their rendered images. Other open workspaces can
    rebuild these shared acceleration caches on their next render.
    """
    _image_background.cache_clear()
    _watermark_logo.cache_clear()


def _watermark(style):
    if not style.watermark_enabled or not style.watermark_opacity:
        return None
    try:
        stat = LOGO_PATH.stat()
    except OSError:
        raise ValueError("Could not load the LRCPlus logo. Turn off the watermark to export this card.") from None
    image = _watermark_logo(str(LOGO_PATH), stat.st_mtime_ns, stat.st_size)
    width = style.watermark_width
    height = max(1, round(width * image.height() / image.width()))
    x = style.width - 48 - width if style.watermark_position.endswith("right") else 48
    y = 48 if style.watermark_position.startswith("top") else style.height - 48 - height
    return image, QRectF(x, y, width, height)


def _song_details(style, width):
    """Fit metadata in stable text boxes, independently of paint effects."""
    entries = []
    height = 0
    credit = " · ".join(value.strip() for value in (style.credit, style.album) if value.strip())
    title_size = style.title_font_size or (64 if style.template == "Editorial" else 30)
    for text, pixels, color_index in ((style.title.strip(), title_size, 3), (credit, style.credit_font_size or 26, 4)):
        if not text:
            continue
        scale = min(1, pixels / style.font_size)
        family = style.title_font_family if color_index == 3 else style.credit_font_family
        effects = replace(style, font_family=family or style.font_family,
                          bold=style.template == "Editorial" and color_index == 3,
                          italic=False, underline=False, strikeout=False,
                          alignment="left", centered=False, include_furigana=False,
                          stroke_width=max(1, round(style.stroke_width * scale)),
                          shadow_blur=round(style.shadow_blur * scale),
                          shadow_offset_x=round(style.shadow_offset_x * scale),
                          shadow_offset_y=round(style.shadow_offset_y * scale))
        left = top = right = bottom = TEXT_PAINT_PADDING
        try:
            if width - left - right < 48:
                raise ValueError("Not enough room for metadata.")
            document = _fitted_document(text, effects, pixels, width - left - right, 160, styled=True)
        except ValueError:
            raise ValueError("The song details are too large for this template. Shorten the details or choose another template.") from None
        if entries:
            height += 12
        entries.append((document, left, height + top, color_index, effects))
        height += top + document.size().height() + bottom
    return entries, height


def render_lyric_card(text, style, cover_image=None):
    """Render at export resolution; reject overflow instead of clipping lyrics."""
    if type(style.export_scale) is not int or style.export_scale not in CARD_QUALITIES.values():
        raise ValueError("Choose Standard, High, or Ultra card quality.")
    source = text.trimmed() if isinstance(text, CardLyrics) else None
    text = source.text if source is not None else str(text).strip()
    if source is not None:
        if not style.include_furigana:
            source = replace(source, readings=())
        if not style.preserve_lrcx_formatting:
            source = source.without_formatting()
        if not source.has_formatting:
            source = None
    if not text.strip():
        raise ValueError("Select some lyrics to create an image card.")
    colors = style.colors or theme_colors(style.theme, style.template)
    background, gradient_end, foreground, title_color, credit_color, accent = colors
    margin = style.padding
    watermark = _watermark(style)
    top_margin = bottom_margin = margin
    if style.template == "Cinema":
        top_margin = bottom_margin = max(margin, cinema_bar_height(style) + 24)
    elif style.template == "Receipt":
        bottom_margin = max(margin, 100)
    if watermark is not None:
        reserved = watermark[1].height() + 48 + 24
        if style.watermark_position.startswith("top"):
            top_margin = max(top_margin, reserved)
        else:
            bottom_margin = max(bottom_margin, reserved)
    content_x = margin
    content_width = style.width - 2 * margin
    sidebar_width = min(420, round(style.width * .30))
    sidebar_padding = min(64, margin)
    has_cover = style.show_album_cover and cover_image is not None and not cover_image.isNull()
    shadow_space = (style.cover_shadow_blur * 3 + max(abs(style.cover_shadow_offset_x),
                                                    abs(style.cover_shadow_offset_y))
                    if has_cover and style.cover_shadow_enabled and style.cover_shadow_opacity else 0)
    cover_gap = max(32, shadow_space + 12) if shadow_space else 32
    beside_cover = has_cover and (style.details_placement == "beside_cover" or style.cover_placement != "auto")
    cover_on_left = beside_cover and style.cover_placement != "right"
    sidebar_details = style.template == "Record sleeve" and not beside_cover
    if style.template == "Record sleeve":
        content_x = sidebar_width + min(64, margin)
        content_width = style.width - content_x - margin
    cover_size = min(style.cover_size, sidebar_width - 2 * sidebar_padding) if sidebar_details else style.cover_size
    metadata_x = sidebar_padding if sidebar_details else content_x
    metadata_width = sidebar_width - 2 * sidebar_padding if sidebar_details else content_width
    photo_rect = None
    if style.template == "Mixtape":
        photo_rect = QRectF(content_x, top_margin, content_width, 144)
        top_margin += 180
    if has_cover and not sidebar_details and (style.template != "Polaroid" or beside_cover):
        metadata_width -= cover_size + cover_gap
    details, details_height = _song_details(style, metadata_width)
    cover_rect = None
    if style.template == "Polaroid":
        photo_height = min(cover_size, (style.height - top_margin - bottom_margin) * .32)
        photo_rect = QRectF(content_x, top_margin, cover_size if beside_cover else content_width, photo_height)
        if beside_cover:
            block_height = max(photo_height, details_height)
            photo_rect.moveTop(top_margin + (block_height - photo_height) / 2)
            if cover_on_left:
                metadata_x += cover_size + cover_gap
            else:
                photo_rect.moveLeft(content_x + content_width - cover_size)
            details_y = top_margin + (block_height - details_height) / 2
            body_top = top_margin + block_height + max(44, shadow_space + 12)
        else:
            details_y = photo_rect.bottom() + (max(20, shadow_space + 12) if shadow_space else 20)
            body_top = details_y + details_height + 32
        if has_cover:
            cover_rect = photo_rect
        body_bottom = style.height - bottom_margin
    elif sidebar_details:
        details_y = style.height - bottom_margin - details_height
        if has_cover:
            cover_rect = QRectF((sidebar_width - cover_size) / 2, top_margin, cover_size, cover_size)
        if details and details_y < top_margin + (cover_size if has_cover else 208) + cover_gap:
            raise ValueError("The song details and artwork are too tall for this template. Shorten the details or choose another template.")
        body_top = top_margin
        body_bottom = style.height - bottom_margin
    else:
        block_height = max(details_height, cover_size if has_cover else 0)
        footer_details = style.template in ("Spotlight", "Ticket", "Streaming")
        block_y = style.height - bottom_margin - block_height if footer_details else top_margin
        details_y = block_y + (block_height - details_height) / 2 if beside_cover else block_y
        if has_cover:
            cover_x = content_x if cover_on_left else content_x + content_width - cover_size
            cover_rect = QRectF(cover_x, block_y + (block_height - cover_size) / 2, cover_size, cover_size)
            if cover_on_left:
                metadata_x += cover_size + cover_gap
        if footer_details:
            body_top = top_margin + (120 if style.template == "Spotlight" else 24)
            body_bottom = block_y - max(48 if style.template == "Spotlight" else 64, shadow_space + 12)
        else:
            body_top = block_y + block_height + (max(54, shadow_space + 12) if block_height else 34)
            body_bottom = style.height - bottom_margin
    if style.template == "Editorial":
        content_x += 48
        content_width -= 48
        body_top += 32
    elif style.template == "Cinema":
        body_top += 96
    body_height = body_bottom - body_top
    effect_left = effect_top = effect_right = effect_bottom = TEXT_PAINT_PADDING
    lyric_width = content_width - effect_left - effect_right
    lyric_height = body_height - effect_top - effect_bottom
    lyric_offset_x = 0
    if style.writing_mode in ("vertical_rl", "vertical_lr"):
        lyrics = fit_vertical_lyrics(text.strip(), style, lyric_width, lyric_height, source)
        alignment = style.alignment or ("center" if style.centered else "left")
        lyric_offset_x = (lyric_width - lyrics.size().width()) * {"left": 0, "center": .5, "right": 1}.get(alignment, 0)
    else:
        lyrics = _fitted_document(text.strip(), style, style.font_size, lyric_width,
                                  lyric_height, style.centered, style.line_spacing, styled=True, source=source)

    lyrics_y = body_top + effect_top + (lyric_height - lyrics.size().height()) * style.vertical_position / 100
    result = card_background(style, colors)
    painter = QPainter(result)
    painter.scale(style.export_scale, style.export_scale)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        border = QColor(accent)
        border.setAlpha(50)
        def draw(document, x, y, color):
            _paint_document(painter, document, x, y, color)

        if style.template == "Spotlight":
            quotation = _document("“", style.font_family, 150, content_width)
            draw(quotation, margin - 8, top_margin - 48, accent)
            painter.setPen(QPen(border, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for radius in (180, 220, 260):
                painter.drawEllipse(QRectF(style.width - radius, style.height - radius, radius * 2, radius * 2))
            painter.fillRect(QRectF(margin, block_y - 24, 54, 4), QColor(accent))
        elif style.template == "Record sleeve":
            panel = QColor(foreground)
            panel.setAlpha(12)
            painter.fillRect(QRectF(0, 0, sidebar_width, style.height), panel)
            painter.setPen(QPen(border, 2))
            painter.drawLine(int(sidebar_width), int(top_margin), int(sidebar_width), int(style.height - bottom_margin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if not has_cover or beside_cover:
                center_x, center_y = sidebar_width / 2, top_margin + 104
                for radius in (102, 96, 82, 68, 54, 40):
                    painter.drawEllipse(QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(accent))
                painter.drawEllipse(QRectF(center_x - 26, center_y - 26, 52, 52))
                painter.setBrush(QColor(background))
                painter.drawEllipse(QRectF(center_x - 5, center_y - 5, 10, 10))
        elif style.template == "Classic":
            painter.setPen(QPen(border, 2))
            painter.drawRoundedRect(QRectF(32, 32, style.width - 64, style.height - 64), 24, 24)
            painter.fillRect(QRectF(margin, body_top - 34, 54, 4), QColor(accent))
        else:
            paint_new_template(painter, style, colors, QRectF(content_x, body_top, content_width, body_height),
                               photo_rect, cover_image if has_cover else None)
        if style.template == "Notebook":
            paint_notebook_rules(painter, style, QRectF(content_x, body_top, content_width, body_height),
                                 lyrics, lyrics_y, accent)

        for document, dx, dy, color_index, effects in details:
            painter.save()
            painter.setClipRect(QRectF(metadata_x, details_y + dy - TEXT_PAINT_PADDING,
                                      metadata_width, document.size().height() + 2 * TEXT_PAINT_PADDING))
            detail_color = style.title_color if color_index == 3 else style.credit_color
            _paint_lyrics(painter, document, metadata_x + dx, details_y + dy, detail_color or colors[color_index], effects)
            painter.restore()
        painter.save()
        painter.setClipRect(QRectF(content_x, body_top, content_width, body_height))
        _paint_lyrics(painter, lyrics, content_x + effect_left + lyric_offset_x, lyrics_y, foreground, style)
        painter.restore()
        if has_cover:
            paint_cover(painter, cover_rect, cover_image, style)
        if watermark is not None:
            painter.save()
            painter.setOpacity(style.watermark_opacity / 100)
            painter.drawImage(watermark[1], watermark[0])
            painter.restore()
    finally:
        painter.end()
    return result

