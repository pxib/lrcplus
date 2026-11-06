"""Single-line metadata lists with glyph-checked fonts and bounded layouts."""

from collections import OrderedDict
import math

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.fonts import library_font
from ui.content_fonts import FontRunResolver, graphemes


class ContentListDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self._resolvers = {}
        self._layouts = OrderedDict()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        font = library_font(option.font)
        font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
        option.font = font
        option.fontMetrics = QFontMetrics(font)

    def _layout(self, text, font, width, elide, direction):
        key = (text, font.key(), width, elide, direction)
        if key in self._layouts:
            self._layouts.move_to_end(key)
            return self._layouts[key]
        font_key = font.key()
        if font_key not in self._resolvers:
            if len(self._resolvers) >= 8:
                self._resolvers.clear()
            self._resolvers[font_key] = FontRunResolver(font)
        resolver = self._resolvers[font_key]

        def shape(value):
            layout = QTextLayout(value, font, self.parent().viewport())
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            option.setTextDirection(direction)
            layout.setTextOption(option)
            layout.setFormats(resolver.formats(value))
            layout.beginLayout()
            line = layout.createLine()
            if line.isValid():
                line.setLineWidth(1_000_000)
            layout.endLayout()
            return layout, line

        layout, line = shape(text)
        if (line.isValid() and width is not None and line.naturalTextWidth() > width
                and elide != Qt.TextElideMode.ElideNone):
            clusters = [cluster for _, _, cluster in graphemes(text)]
            low, high = 0, len(clusters)
            layout, line = shape("…")
            while low < high:
                count = (low + high + 1) // 2
                if elide == Qt.TextElideMode.ElideLeft:
                    value = "…" + "".join(clusters[len(clusters) - count:])
                elif elide == Qt.TextElideMode.ElideMiddle:
                    front = (count + 1) // 2
                    value = "".join(clusters[:front]) + "…" + "".join(clusters[len(clusters) - (count - front):])
                else:
                    value = "".join(clusters[:count]) + "…"
                candidate, candidate_line = shape(value)
                if candidate_line.naturalTextWidth() <= width:
                    low = count
                    layout, line = candidate, candidate_line
                else:
                    high = count - 1
        self._layouts[key] = (layout, line)
        if len(self._layouts) > 128:
            self._layouts.popitem(last=False)
        return layout, line

    def sizeHint(self, option, index):
        value = QStyleOptionViewItem(option)
        self.initStyleOption(value, index)
        _, line = self._layout(value.text, value.font, None,
                               Qt.TextElideMode.ElideNone, value.direction)
        standard = super().sizeHint(option, index)
        if not line.isValid():
            return standard
        return QSize(max(standard.width(), math.ceil(line.naturalTextWidth()) + 8),
                     max(standard.height(), math.ceil(line.height()) + 4))

    def paint(self, painter, option, index):
        value = QStyleOptionViewItem(option)
        self.initStyleOption(value, index)
        style = value.widget.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, value, value.widget)
        layout, line = self._layout(value.text, value.font, max(0, rect.width()),
                                    value.textElideMode, value.direction)
        value.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, value, painter, value.widget)
        if not line.isValid():
            return
        group = (QPalette.ColorGroup.Disabled if not value.state & QStyle.StateFlag.State_Enabled
                 else QPalette.ColorGroup.Active if value.state & QStyle.StateFlag.State_Active
                 else QPalette.ColorGroup.Inactive)
        role = (QPalette.ColorRole.HighlightedText if value.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.setPen(value.palette.color(group, role))
        x = rect.x()
        if value.displayAlignment & Qt.AlignmentFlag.AlignRight:
            x += rect.width() - line.naturalTextWidth()
        elif value.displayAlignment & Qt.AlignmentFlag.AlignHCenter:
            x += (rect.width() - line.naturalTextWidth()) / 2
        layout.draw(painter, QPointF(x, rect.y() + (rect.height() - line.height()) / 2))
        painter.restore()


def configure_content_list(view):
    font = library_font(view.font())
    font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
    view.setFont(font)
    view.setWordWrap(False)
    view.setUniformItemSizes(True)
    view.setItemDelegate(ContentListDelegate(view))
