"""Compose independent lyric layers using the shared timed-text layout."""

from PySide6.QtGui import QFontMetrics

from core.lyrics_settings import normalize_layer_order
from lyrics.romaji import all_romaji_texts


_MUSIC_NOTES = str.maketrans("", "", "♩♪♫♬")


class DisplayLayersMixin:
    def set_lyric_layers(self, order, show_lyrics=True, show_romaji=False,
                         show_translation=True, show_furigana=True):
        config = (tuple(normalize_layer_order(order)), bool(show_lyrics),
                  bool(show_romaji), bool(show_translation), bool(show_furigana))
        changed = (getattr(self, "lyric_layers", None) != config
                   or self.show_ruby != bool(show_furigana)
                   or self.show_romaji != bool(show_romaji) or self.all_romaji)
        self.show_ruby = bool(show_furigana)
        self.show_romaji = bool(show_romaji)
        self.all_romaji = False
        if not changed:
            return
        self.lyric_layers = config
        self._update_content_geometry()
        self.update()

    def _build_layered_segments(self):
        order, lyrics, romaji, translation, furigana = self.lyric_layers
        visible = dict(zip(("lyrics", "romaji", "translation", "furigana"),
                           (lyrics, romaji, translation, furigana)))
        rows = [[]]
        for piece in self._visual_source_segments():
            if piece.get("_line_break"):
                rows.append([])
            else:
                rows[-1].append(piece)
        originals = [row for row in rows if row and not row[0].get("translation")]
        translations = [row for row in rows if row and row[0].get("translation")]
        saved = self.show_ruby, self.show_romaji, self.all_romaji
        saved_font = self.base_font
        saved_wrapping = self.wrap_text, self.wrap_max_lines
        result = []
        y_offset = 0
        x_offset = 0
        self._layer_layout_active = True
        self.show_ruby = self.show_romaji = self.all_romaji = False
        if self.direction != "vertical" and getattr(self, "wrap_display_layers", True):
            self.wrap_text, self.wrap_max_lines = True, -1
        try:
            for layer in order:
                if not visible[layer]:
                    continue
                use_reading_font = layer == "furigana" or (layer == "romaji" and lyrics)
                self.base_font = self.reading_font if use_reading_font else saved_font
                layer_rows = translations if layer == "translation" else originals
                source = []
                for row in layer_rows:
                    if layer == "furigana" and not any(p.get("reading") for p in row):
                        continue
                    romanized = all_romaji_texts(row) if layer == "romaji" else None
                    row_source = []
                    for index, original in enumerate(row):
                        piece = dict(original)
                        if layer == "romaji":
                            piece["text"] = romanized[index]
                        elif layer == "furigana":
                            piece["text"] = piece.get("reading") or piece["text"]
                        if lyrics and layer in {"romaji", "furigana"}:
                            piece["text"] = piece["text"].translate(_MUSIC_NOTES)
                        piece.update(reading=None, display_layer=layer,
                                     use_reading_font=use_reading_font)
                        row_source.append(piece)
                    if layer in {"romaji", "furigana"} and not any(
                        piece["text"].strip() for piece in row_source
                    ):
                        continue
                    if source:
                        source.append({"text": "", "_line_break": True,
                                       "_next_translation": layer == "translation"})
                    source.extend(row_source)
                if not source:
                    continue
                self._layer_visual_segments = source
                prepared = self._build_prepared_segments()
                if self.direction == "vertical":
                    if prepared:
                        left = min(p["x"] for p in prepared)
                        right = max(p["x"] + p["width"] for p in prepared)
                        shift = x_offset - left + self.PADDING
                        for piece in prepared:
                            for key in ("x", "base_x", "karaoke_x"):
                                if piece.get(key) is not None:
                                    piece[key] += shift
                        x_offset += right - left + self.PADDING + self.ruby_padding
                else:
                    metrics = QFontMetrics(self.base_font)
                    height = max((p["base_y"] + metrics.descent() + self.BOTTOM_MARGIN
                                  for p in prepared), default=0)
                    if result:
                        y_offset += self.ruby_padding
                    for piece in prepared:
                        piece["base_y"] += y_offset
                    y_offset += height
                # Each layer keeps the authored timing but owns its visual spans.
                for piece in prepared:
                    for key in ("karaoke_group", "timing_group", "_reading_group"):
                        if piece.get(key) is not None:
                            piece[key] = (layer, piece[key])
                result.extend(prepared)
        finally:
            self.show_ruby, self.show_romaji, self.all_romaji = saved
            self.base_font = saved_font
            self.wrap_text, self.wrap_max_lines = saved_wrapping
            self._layer_layout_active = False
            self.__dict__.pop("_layer_visual_segments", None)
        self._horizontal_layout_height = max(1, y_offset)
        self._layer_layout_width = max(1, x_offset + self.PADDING)
        self._all_romaji_x_scale = 1.0
        return result
