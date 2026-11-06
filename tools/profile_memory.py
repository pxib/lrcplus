"""Launch LRCPlus and record Windows native/Python memory during normal use.

Example: Scripts/python.exe tools/profile_memory.py --trace --output memory.jsonl
Use --disable-karaoke to compare the same song without word highlighting.
Close the player normally to finish the allocation comparison. No forced GC or
working-set trimming is performed, so the measurements reflect normal use.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time
import tracemalloc


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        *[(name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage",
        )],
    ]


def process_memory():
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb,
    ):
        raise ctypes.WinError()
    return counters.WorkingSetSize, counters.PrivateUsage


def runtime_state(window):
    """Inspect active costs without instantiating deferred player components."""
    state = {"loaded_modules": [name for name in (
        "requests", "numpy", "core.pitch_stream", "pypinyin",
    ) if name in sys.modules]}
    if window is None:
        return state
    from PySide6.QtGui import QGuiApplication
    state["qt_platform"] = QGuiApplication.platformName()
    manager = getattr(window, "plugin_manager", None)
    plugins = getattr(manager, "plugins", {})
    state["loaded_plugins"] = sorted(plugins)
    karaoke = plugins.get("karaoke")
    timer = getattr(karaoke.instance, "timer", None) if karaoke is not None else None
    state["karaoke_timer_active"] = timer.isActive() if timer is not None else False
    state["karaoke_interval_ms"] = timer.interval() if timer is not None else None
    lines = getattr(window, "lyric_lines", [])
    state["lyric_lines"] = len(lines)
    state["timed_lyric_lines"] = sum(bool(getattr(line, "has_karaoke", False)) for line in lines)
    state["translated_lyric_lines"] = sum(bool(getattr(line, "translation_lines", [])) for line in lines)
    readings = sys.modules.get("lyrics.furigana")
    if readings is not None:
        state["reading_parser"] = readings.get_active_parser()
        state["reading_mode"] = readings.get_reading_mode()
        state["mecab_tagger_loaded"] = readings._MECAB_TAGGER is not None
        state["reading_cache_entries"] = len(readings._SEGMENT_CACHE)
    media = getattr(window, "media_player", None)
    if media is not None:
        state["playback_state"] = media.playbackState().name
        state["position_ms"] = media.position()
    state["renderers_ready"] = getattr(window, "_lyrics_renderers_ready", False)
    state["window_size"] = [window.width(), window.height()]
    state["device_pixel_ratio"] = window.devicePixelRatioF()
    state["lyric_font"] = getattr(window, "font_family", "")
    scrolling = getattr(window, "_scrolling_lyrics_widget", None)
    state["mounted_lyric_rows"] = len(scrolling.rows) if scrolling is not None else 0
    background = getattr(window, "lyrics_background", None)
    if background is not None:
        state["background_visible"] = background.isVisible()
        state["artwork_source_size"] = [background._source_pixmap.width(), background._source_pixmap.height()]
        state["artwork_cache_size"] = [background._cached_background.width(), background._cached_background.height()]
        state["encoded_artwork_bytes"] = len(background._artwork_bytes or b"")
    waveform = getattr(window, "waveform_widget", None)
    if waveform is not None:
        state["waveform_visible"] = waveform.isVisible()
        state["waveform_decoder_active"] = waveform._worker is not None
        state["waveform_peaks"] = len(waveform._peaks)
    state["pcm_route_active"] = getattr(window, "_pcm_route_active", False)
    state["speed"] = getattr(window, "playback_speed", 1.0)
    state["pitch_semitones"] = getattr(window, "pitch_semitones", 0.0)
    volume = getattr(window, "volume_slider", None)
    state["volume_percent"] = volume.value() if volume is not None else None
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=3, help="Sample interval in seconds")
    parser.add_argument("--output", type=Path, default=Path("memory.jsonl"))
    parser.add_argument("--trace", action="store_true", help="Track Python allocations (adds overhead)")
    parser.add_argument("--disable-karaoke", action="store_true",
                        help="Disable word highlighting for this run without changing saved settings")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Native memory sampling currently supports Windows only.")
    if args.interval < 1:
        parser.error("--interval must be at least one second.")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.trace:
        tracemalloc.start(10)

    import main as application
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    start = time.monotonic()
    baseline = None
    player_window = None
    finished = False
    output = args.output.open("w", encoding="utf-8")

    def sample():
        working_set, private = process_memory()
        active = QApplication.activeWindow()
        record = {
            "seconds": round(time.monotonic() - start, 3),
            "working_set_bytes": working_set, "private_bytes": private,
            "window": active.windowTitle() if active else "",
            "runtime": runtime_state(player_window),
        }
        tabs = getattr(active, "tabs", None)
        if tabs is not None:
            record["tab"] = tabs.tabText(tabs.currentIndex())
            if tabs.currentWidget() is getattr(active, "share_page", None):
                record["share_tab"] = active.share_tabs.tabText(active.share_tabs.currentIndex())
        if args.trace:
            record["python_bytes"], record["python_peak_bytes"] = tracemalloc.get_traced_memory()
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        output.flush()
        print(f'{record["seconds"]:7.1f}s  working set {working_set / 1048576:.1f} MiB'
              f'  private {private / 1048576:.1f} MiB  {record.get("tab", record["window"])}',
              flush=True)

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        sample()
        output.close()
        if baseline is not None:
            changes = tracemalloc.take_snapshot().compare_to(baseline, "lineno")
            destination = args.output.with_suffix(".allocations.txt")
            destination.write_text("\n".join(str(item) for item in changes[:40]) + "\n", encoding="utf-8")
            print(f"Python allocation growth: {destination.resolve()}", flush=True)

    class ProfiledPlayer(application.Player):
        def __init__(self, *positional, **keywords):
            nonlocal baseline, player_window
            if args.disable_karaoke:
                manager = keywords.get("plugin_manager")
                if manager is not None:
                    manager.unload("karaoke")
            super().__init__(*positional, **keywords)
            player_window = self
            self._memory_timer = QTimer(self)
            self._memory_timer.setInterval(round(args.interval * 1000))
            self._memory_timer.timeout.connect(sample)
            self._memory_timer.start()
            if args.trace:
                baseline = tracemalloc.take_snapshot()
            sample()
            QApplication.instance().aboutToQuit.connect(finish)

    application.Player = ProfiledPlayer
    print(f"Memory samples: {args.output.resolve()}", flush=True)
    # Profiler flags belong to this launcher, not the application's Qt parser.
    sys.argv = [str(Path(application.__file__))]
    try:
        return application.main()
    finally:
        finish()


if __name__ == "__main__":
    raise SystemExit(main())
