from dataclasses import dataclass


@dataclass(frozen=True)
class LyricSettingField:
    key: str
    label: str
    kind: str
    choices: tuple = ()
    bounds: tuple[int, int] = (0, 0)
    unit: str = ""
    local: bool = True
    local_label: str = ""
    tooltip: str = ""


LYRIC_FIELDS_BY_SECTION = {
    "Display": (
        LyricSettingField("scrolling_lyrics", "Display mode", "choice", (
            ("Compact", False), ("Scrolling", True),
        )),
        LyricSettingField("lyrics_direction", "Direction", "choice", (
            ("Horizontal", "horizontal"), ("Vertical", "vertical"), ("Auto (CJK-aware)", "auto"),
        ), local_label="Lyrics direction"),
        LyricSettingField("lyrics_alignment", "Lyric alignment", "choice", (
            ("Left", "left"), ("Center", "center"), ("Right", "right"),
        ), local_label="Lyrics alignment"),
        LyricSettingField("lyrics_padding", "Lyrics padding", "slider", bounds=(0, 24), unit="px"),
        LyricSettingField("vertical_padding", "Vertical padding", "slider", bounds=(0, 200), unit="px",
                          tooltip="Adds top and bottom padding when lyrics use Vertical direction."),
        LyricSettingField("focus_transition", "Focus transition", "checkbox",
                          tooltip="Bring the current line into focus while gently blurring and dimming surrounding lyrics."),
    ),
    "Readings": (
        LyricSettingField("lyric_layer_order", "Lyric layer order and visibility", "layers",
                          local_label="Lyric layer order"),
        LyricSettingField("show_lyrics", "Lyrics", "checkbox", local_label="Show lyrics"),
        LyricSettingField("show_translation", "Translation", "checkbox", local_label="Show translation"),
        LyricSettingField("show_ruby", "Furigana", "checkbox", local_label="Show furigana"),
        LyricSettingField("show_romaji", "Romaji", "checkbox", local_label="Show romaji"),
        LyricSettingField("all_romaji", "All romaji", "checkbox", local=False),
        LyricSettingField("ruby_position", "Furigana position", "choice", (
            ("Above lyrics", "above"), ("Below lyrics", "below"),
        ), local=False),
        LyricSettingField("furigana_parser", "Automatic parser", "choice", (
            ("Yomi (online, recommended)", "yomi"), ("MeCab (local, optional)", "mecab_local"),
        ), local=False, tooltip="Choose the automatic Japanese parser. Local MeCab requires "
                               "fugashi and a dictionary installed separately."),
        LyricSettingField("mecab_parse_mode", "MeCab parsing", "choice", (
            ("Batch parsing (faster)", "batch"), ("Individual lines (more accurate)", "line"),
        ), local=False, tooltip="Controls how local MeCab analyzes Japanese lyrics. Batch parsing "
                               "is faster but surrounding lines can occasionally affect tokenization. "
                               "Individual-line parsing isolates each lyric line for more consistent readings."),
        LyricSettingField("limited_reading_rendering", "Limited reading rendering", "checkbox",
                          tooltip="Only render furigana/pinyin for lyrics near the current line. "
                                  "This can improve performance on long lyrics."),
        LyricSettingField("limited_reading_range", "Nearby lyrics", "slider", bounds=(5, 15),
                          unit="lines", local_label="Limited reading range"),
        LyricSettingField("fade_in_readings", "Fade readings", "checkbox",
                          local_label="Fade in readings",
                          tooltip="Smoothly fade furigana or pinyin in and out as they enter or leave the rendered range."),
        LyricSettingField("ruby_padding", "Layer spacing", "slider", bounds=(0, 18), unit="px"),
        LyricSettingField("ruby_font_size", "Furigana font size", "spinbox", bounds=(6, 72), unit="pt"),
    ),
    "Typography": (
        LyricSettingField("font_family", "Font family", "font"),
        LyricSettingField("font_size", "Font size", "spinbox", bounds=(10, 96), unit="pt"),
        LyricSettingField("lyrics_color", "Lyrics color", "color"),
    ),
    "Highlighting": (
        LyricSettingField("highlight_current", "Highlight current lyric", "checkbox"),
        LyricSettingField("highlight_color", "Current lyric color", "color"),
        LyricSettingField("highlight_animation", "Highlight animation", "choice", (
            ("None", "none"), ("Fade", "fade"), ("Pop", "pop"), ("Scale", "scale"), ("Slide", "slide"),
        )),
        LyricSettingField("highlight_animation_duration", "Animation in duration", "slider",
                          bounds=(0, 2000), unit="ms"),
        LyricSettingField("highlight_animation_out_duration", "Animation out duration", "slider",
                          bounds=(0, 2000), unit="ms"),
        LyricSettingField("highlight_animation_easing", "Highlight animation easing", "choice", (
            ("Linear", "linear"), ("In Quad", "in_quad"), ("Out Quad", "out_quad"),
            ("In-Out Quad", "in_out_quad"), ("In Cubic", "in_cubic"), ("Out Cubic", "out_cubic"),
            ("In-Out Cubic", "in_out_cubic"), ("Out Back", "out_back"), ("Out Bounce", "out_bounce"),
        )),
    ),
    "Scrolling": (
        LyricSettingField("scrolling_mode", "Scrolling mode", "choice", (
            ("Automatic (timestamp-driven)", "automatic"),
            ("Continuous (independent of timestamps)", "continuous"),
        )),
        LyricSettingField("continuous_scroll_duration_based", "Calculate speed based on song duration", "checkbox"),
        LyricSettingField("continuous_scroll_speed", "Continuous scroll speed", "slider",
                          bounds=(1, 500), unit="px/s"),
        LyricSettingField("continuous_scroll_nudge_current", "Nudge to the center of current lyric when out of view",
                          "checkbox", local_label="Nudge current lyric into view"),
        LyricSettingField("scroll_animation_speed", "Scroll animation speed", "slider", bounds=(0, 1000), unit="ms"),
        LyricSettingField("scroll_animation_easing", "Scroll easing", "choice", (
            ("Linear", "linear"), ("In / Out Quad", "in_out_quad"), ("Out Cubic", "out_cubic"),
            ("Out Quart", "out_quart"), ("Out Expo", "out_expo"),
        )),
        LyricSettingField("scroll_behavior", "Scrolling behavior", "choice", (
            ("Center current lyric", "center"), ("Focus on current lyric", "focus_current"),
            ("Keep current lyric visible", "ensure_visible"), ("Do not auto-scroll", "off"),
        )),
    ),
}

LYRIC_FIELDS = {
    field.key: field for fields in LYRIC_FIELDS_BY_SECTION.values() for field in fields
}
LYRIC_KEYS = tuple(LYRIC_FIELDS)
LOCAL_LYRIC_KEYS = tuple(key for key, field in LYRIC_FIELDS.items() if field.local)
LYRIC_SECTIONS = {
    section: tuple(field.key for field in fields)
    for section, fields in LYRIC_FIELDS_BY_SECTION.items()
}
