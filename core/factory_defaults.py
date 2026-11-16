"""Factory baseline chosen with the Settings recorder.

Only selected overrides belong here; omitted preferences retain their built-in
fallbacks. Keep this independent of settings storage so fresh installs and the
Restore factory defaults action share the same baseline.
"""

FACTORY_DEFAULTS = {
    "startup": {
        "restore_playlist": True,
    },
    "appearance": {
        "theme_mode": "dark",
        "theme_color": "#88c0d0",
        "theme_preset_name": "Nordic Frost",
        "theme_overrides": {
            "light": {
                "window": "#e7d6c7",
                "header": "#e7d6c7",
                "surface": "#f7ede4",
                "surface_alt": "#f0e6dd",
                "raised": "#dfcfc0",
                "raised_hover": "#eddccd",
                "pressed": "#e3d2c3",
                "text": "#37302a",
                "muted": "#68625c",
                "brand": "#39332d",
                "border": "#d5c4b5",
                "field_border": "#d0bfb1",
                "header_button_text": "#4b443e",
                "dock": "#5c422c",
                "dock_border": "#6e553e",
                "dock_control": "#684f39",
                "dock_control_hover": "#735a43",
                "dock_control_border": "#7a614a",
                "dock_disabled": "#614832",
                "dock_text": "#e3dcd6",
                "dock_muted": "#958f89",
                "seek_track": "#6f5f50",
                "slider_handle": "#f1ebe5",
                "scroll_handle": "#c7b7a8",
                "selection": "#e6c5a9",
                "item_hover": "#e7ceb8",
            },
            "dark": {
                "window": "#242c3a",
                "header": "#242c3a",
                "surface": "#333e50",
                "surface_alt": "#2c3646",
                "raised": "#465060",
                "raised_hover": "#3c4b5d",
                "pressed": "#44586a",
                "text": "#eceff4",
                "muted": "#a7b5c8",
                "brand": "#eceff4",
                "border": "#444b58",
                "field_border": "#505763",
                "header_button_text": "#eceff4",
                "dock": "#242c3a",
                "dock_border": "#364257",
                "dock_control": "#333e50",
                "dock_control_hover": "#3b475c",
                "dock_control_border": "#425168",
                "dock_disabled": "#212835",
                "dock_text": "#eceff4",
                "dock_muted": "#a7b5c8",
                "seek_track": "#88c0d0",
                "slider_handle": "#eceff4",
                "scroll_handle": "#88c0d0",
                "selection": "#714722",
                "item_hover": "#604730",
            },
        },
        "background_blur": 0,
    },
    "lyrics_global": {
        "furigana_parser": "yomi",
        "limited_reading_rendering": False,
        "highlight_animation": "none",
    },
    "listening_statistics": {
        "enabled": False,
    },
    "updates": {
        "automatic_check": False,
    },
}
