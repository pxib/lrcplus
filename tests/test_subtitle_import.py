from pathlib import Path
import sys
from types import ModuleType

import pytest

from lyrics.parser import load_lrc
from plugins.community.ytdlp_import import worker
from plugins.community.ytdlp_import.captions import captions_to_lrc, convert_caption_file, parse_captions
from plugins.community.ytdlp_import.captions import caption_style_warning
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.job import ImportJob
from plugins.community.ytdlp_import.plugin import DEFAULTS


VTT = """WEBVTT
Kind: captions
Language: en

NOTE an ignored comment
00:00:00.000 --> 00:00:01.000
not a lyric

STYLE
::cue {color: white;}

first-cue
00:00:01.250 --> 00:00:03.000 align:start position:0%
<v Singer><c.green>Hello &amp; <00:00:02.000>world</c></v>
Second line

00:00:05.000 --> 00:00:06.500
Goodbye
"""


def test_vtt_conversion_handles_metadata_markup_multiline_and_silent_gaps(tmp_path):
    cues = parse_captions(VTT)
    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (1250, 3000, "Hello & world\nSecond line"), (5000, 6500, "Goodbye"),
    ]
    path = tmp_path / "captions.lrc"
    path.write_text(captions_to_lrc(VTT), encoding="utf-8")
    assert load_lrc(path) == [(1.25, "Hello & world"), (1.25, "Second line"), (3.0, ""),
                              (5.0, "Goodbye"), (6.5, "")]


def test_dense_styled_captions_do_not_expand_into_thousands_of_lyric_rows():
    # Uploaded captions can redraw outlines and foreground every video frame.
    blocks = ["WEBVTT\n\n"]
    for frame in range(13000):
        start, end = frame * 10, (frame + 1) * 10
        def stamp(value):
            return f"00:{value // 60000:02d}:{value // 1000 % 60:02d}.{value % 1000:03d}"
        for color in ("black", "black", "white"):
            blocks.append(f"{stamp(start)} --> {stamp(end)} line:28%\n<c.{color}>人\n人\n歌\nう</c>\n\n")
    output = captions_to_lrc("".join(blocks))
    assert output.splitlines() == ["[00:00.000]人人歌う", "[02:10.000]"]


def test_consecutive_cues_without_blank_separators_are_parsed_separately():
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFirst\n00:00:03.000 --> 00:00:04.000\nSecond\n"
    cues = parse_captions(text)
    assert [cue.text for cue in cues] == ["First", "Second"]
    assert captions_to_lrc(text).splitlines() == ["[00:01.000]First", "[00:02.000]", "[00:03.000]Second", "[00:04.000]"]


def test_redraw_cleanup_preserves_authored_multiline_and_refrains_after_silence():
    text = """WEBVTT

00:00:01.000 --> 00:00:02.000
First line
Second line

00:00:01.000 --> 00:00:02.000
<b>First line</b>
Second line

00:00:04.000 --> 00:00:05.000
First line
Second line
"""
    assert captions_to_lrc(text).splitlines() == ["[00:01.000]First line", "[00:01.000]Second line", "[00:02.000]",
                                                "[00:04.000]First line", "[00:04.000]Second line", "[00:05.000]"]


def test_conversion_limit_rejects_runaway_output_before_writing_lyrics(tmp_path, monkeypatch):
    from plugins.community.ytdlp_import import captions

    monkeypatch.setattr(captions, "MAX_CONVERTED_LINES", 1)
    source, target = tmp_path / "captions.vtt", tmp_path / "captions.lrc"
    source.write_text(VTT, encoding="utf-8")
    with pytest.raises(ValueError, match="limit"):
        convert_caption_file(source, target)
    assert not target.exists()


def test_srt_conversion_preserves_unicode_and_literal_timestamp_text(tmp_path):
    source = tmp_path / "captions.srt"
    source.write_bytes("1\r\n01:02:03,456 --> 01:02:05,789\r\n<b>日本語</b><br>literal [00:10.00]\r\n".encode("utf-16"))
    target = tmp_path / "captions.lrc"
    convert_caption_file(source, target)
    assert load_lrc(target) == [(3723.456, "日本語"), (3723.456, "literal ［00:10.00］"), (3725.789, "")]


def test_automatic_rolling_captions_remove_overlapping_repeats_but_keep_later_refrains(tmp_path):
    text = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello

00:00:02.000 --> 00:00:04.000
Hello world

00:00:03.000 --> 00:00:05.000
Hello world
Next line

00:00:04.000 --> 00:00:06.000
Next line

00:00:10.000 --> 00:00:11.000
Hello world
"""
    output = captions_to_lrc(text, automatic=True)
    assert "[00:01.000]Hello world" in output
    assert "[00:03.000]Next line" in output
    assert output.count("Next line") == 1
    assert output.count("Hello world") == 2
    assert "[00:10.000]Hello world" in output
    assert captions_to_lrc(text).count("Next line") == 2  # Authored captions remain intact.


@pytest.mark.parametrize("text", ["WEBVTT\n\n", "not subtitles", "00:00:02.000 --> 00:00:01.000\nbackwards",
                                       "00:99:00.000 --> 01:00:00.000\ninvalid"])
def test_invalid_caption_conversion_reports_a_clear_error(text):
    with pytest.raises(ValueError, match="No readable timed cues"):
        captions_to_lrc(text)


def test_caption_catalog_distinguishes_manual_and_automatic_sources():
    formats = [{"ext": "vtt", "name": "English"}]
    info = {"subtitles": {"en": formats, "live_chat": [{"ext": "json"}], "other": [{"ext": "ass"}]},
            "automatic_captions": {"en": formats, "ja": [{"ext": "srt", "name": "Japanese"}]}}
    assert worker.subtitle_tracks(info) == [
        {"language": "en", "kind": "manual", "name": "English"},
        {"language": "en", "kind": "auto", "name": "English"},
        {"language": "ja", "kind": "auto", "name": "Japanese"},
    ]
    opts = worker.subtitle_options({"subtitle": {"language": "en.*", "kind": "auto"}})
    assert opts["writeautomaticsub"] and not opts["writesubtitles"]
    assert opts["subtitleslangs"] == [r"en\.\*"]


def test_ass_metadata_is_reported_even_when_no_vtt_alternative_exists():
    info = {"subtitles": {"en": [{"ext": "ass"}], "ja": [{"ext": "ssa"}, {"ext": "vtt"}]}}
    assert worker.ass_subtitle_tracks(info) == [{"language": "en", "kind": "manual"},
                                               {"language": "ja", "kind": "manual"}]
    assert [track["language"] for track in worker.subtitle_tracks(info)] == ["ja"]


@pytest.mark.parametrize("text", ["[Script Info]\nTitle: Caption", r"{\pos(20,30)}Caption", r"{\k20}Caption"])
def test_ass_style_warning_detects_headers_and_override_effects(text):
    assert caption_style_warning(text).startswith("ASS/SSA-style subtitles detected")


def test_converted_ass_style_warning_is_qualified_and_ordinary_vtt_does_not_warn():
    assert "possibly converted from ASS/SSA" in caption_style_warning("WEBVTT\nStyle:\n::cue(c.color000000) { color: black; }")
    assert caption_style_warning(VTT) == ""  # Ordinary named color classes and formatting.
    assert caption_style_warning("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nPlain text") == ""


def test_bundle_collision_preserves_existing_lyrics_and_keeps_new_outputs_together(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    files = {}
    for extension in (".en.manual.vtt", ".en.manual.lrc", ".m4a"):
        source = stage / ("song" + extension)
        source.write_text("new" + extension, encoding="utf-8")
        files[extension] = source
    original = tmp_path / "song.en.manual.lrc"
    original.write_text("authored lyrics", encoding="utf-8")
    published = worker.publish_bundle("song", files, tmp_path)
    assert {path.name for path in published.values()} == {
        "song (2).en.manual.vtt", "song (2).en.manual.lrc", "song (2).m4a",
    }
    assert original.read_text(encoding="utf-8") == "authored lyrics"
    assert not (tmp_path / "song.en.manual.vtt").exists()  # Rolled back after the LRC collision.


@pytest.fixture
def fake_downloader(tmp_path, monkeypatch):
    calls = []
    module = ModuleType("yt_dlp")
    pp_module = ModuleType("yt_dlp.postprocessor")

    class PostProcessor:
        def __init__(self, *args):
            pass

    class YoutubeDL:
        def __init__(self, options):
            self.options = options
            self.capture = None
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {"id": "song", "extractor_key": "Test", "title": "Song",
                    "subtitles": {"en": [{"ext": "vtt"}]}, "automatic_captions": {"en": [{"ext": "vtt"}]}}

        def add_post_processor(self, capture, **kwargs):
            self.capture = capture

        def process_ie_result(self, info, **kwargs):
            stage = Path(self.options["outtmpl"]).parent
            if self.options.get("writesubtitles") or self.options.get("writeautomaticsub"):
                caption = stage / "captions.en.vtt"
                caption.write_text(VTT, encoding="utf-8")
                info["requested_subtitles"] = {"en": {"filepath": str(caption), "ext": "vtt"}}
            if not self.options.get("skip_download"):
                audio = stage / "Song [song].m4a"
                audio.write_bytes(b"audio bytes")
                info["filepath"] = str(audio)
                snapshot = dict(info)
                snapshot.pop("requested_subtitles", None)
                self.capture.run(snapshot)
            return info

    module.YoutubeDL = YoutubeDL
    pp_module.PostProcessor = PostProcessor
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    monkeypatch.setitem(sys.modules, "yt_dlp.postprocessor", pp_module)
    monkeypatch.setattr(worker, "tool_paths", lambda *args, **kwargs: {"ffmpeg": None, "js_runtimes": {}})
    return calls


def subtitle_request(tmp_path, **values):
    stage = tmp_path / ".lyricsplus-import-test"
    stage.mkdir(exist_ok=True)
    return dict(config={}, stage=str(stage), folder=str(tmp_path), url="https://example.com/song",
                codec="m4a", identity="Test:song", subtitle={"language": "en", "kind": "manual"},
                subtitles_as_lyrics=True, **values)


def test_audio_download_includes_original_captions_and_convertible_lyrics(tmp_path, fake_downloader):
    result = worker.download(subtitle_request(tmp_path))
    assert Path(result["path"]).read_bytes() == b"audio bytes"
    assert Path(result["subtitle_path"]).read_text(encoding="utf-8") == VTT
    assert load_lrc(result["lyrics_path"])[0] == (1.25, "Hello & world")
    assert fake_downloader[0]["writesubtitles"] and not fake_downloader[0]["writeautomaticsub"]
    assert Path(result["lyrics_path"]).name.startswith(Path(result["path"]).stem)


def test_subtitles_only_download_reuses_audio_and_preserves_authored_lyrics(tmp_path, fake_downloader):
    audio = tmp_path / "song.m4a"
    audio.write_bytes(b"existing audio")
    authored = tmp_path / "song.lrc"
    authored.write_text("[00:00.000]Authored", encoding="utf-8")
    result = worker.download_subtitles(subtitle_request(tmp_path, audio_path=str(audio)))
    assert fake_downloader[0]["skip_download"]
    assert result["path"] == str(audio)
    assert audio.read_bytes() == b"existing audio"
    assert authored.read_text(encoding="utf-8") == "[00:00.000]Authored"
    assert result["lyrics_path"] != str(authored)


def test_subtitle_selection_disappearing_aborts_before_audio_download(tmp_path, fake_downloader):
    request = subtitle_request(tmp_path)
    request["subtitle"]["language"] = "missing"
    with pytest.raises(ValueError, match="no longer available"):
        worker.download(request)
    assert list(Path(request["stage"]).iterdir()) == []


def test_caption_conversion_can_be_disabled(tmp_path, fake_downloader):
    request = subtitle_request(tmp_path)
    request["subtitles_as_lyrics"] = False
    result = worker.download(request)
    assert "subtitle_path" in result and "lyrics_path" not in result


def test_conversion_failure_keeps_audio_and_original_subtitles(tmp_path, fake_downloader, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("No readable timed cues were found in the subtitles.")

    monkeypatch.setattr(worker, "convert_caption_file", fail)
    result = worker.download(subtitle_request(tmp_path))
    assert Path(result["path"]).exists()
    assert Path(result["subtitle_path"]).exists()
    assert "lyrics_path" not in result
    assert "could not be created" in result["warning"]


class CaptionAPI:
    def __init__(self):
        self.settings = {}
        self.attached = []
        self.imports = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def attach_lyrics(self, audio, lyrics):
        self.attached.append((audio, lyrics))

    def import_audio_files(self, paths, **kwargs):
        self.imports.append(paths)


def test_ass_warning_banner_follows_selection_and_survives_progress(qapp):
    dialog = ImportDialog(CaptionAPI(), DEFAULTS)
    try:
        dialog._succeeded("preview", {"identity": "Test:song", "title": "Song", "subtitle_tracks": [
            {"language": "en", "kind": "manual", "name": "English"},
            {"language": "ja", "kind": "manual", "name": "Japanese"},
        ], "ass_subtitle_tracks": [{"language": "en", "kind": "manual"}]})
        assert not dialog.subtitle_warning.isHidden()
        dialog.subtitle_combo.setCurrentIndex(1)
        assert "ASS/SSA subtitles detected" in dialog.subtitle_warning.text()
        dialog.subtitle_combo.setCurrentIndex(2)
        assert dialog.subtitle_warning.isHidden()
        dialog._event({"event": "subtitle_warning", "message": "Styled captions detected, possibly converted from ASS/SSA."})
        dialog._event({"event": "progress", "downloaded": 20, "total": 100})
        assert not dialog.subtitle_warning.isHidden()
        assert "possibly converted" in dialog.subtitle_warning.text()
        dialog.url.setText("https://example.com/another")
        assert dialog.subtitle_warning.isHidden()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_dialog_requests_subtitles_without_redownloading_existing_audio(qapp, tmp_path, monkeypatch):
    api = CaptionAPI()
    audio = tmp_path / "song.m4a"
    audio.touch()
    api.settings["downloads"] = {"Test:song:m4a": {"path": str(audio)}}
    requests = []
    monkeypatch.setattr(ImportJob, "start", lambda self: requests.append(self.request))
    dialog = ImportDialog(api, DEFAULTS)
    try:
        dialog.url.setText("https://example.com/song")
        dialog._succeeded("preview", {"identity": "Test:song", "title": "Song", "subtitle_tracks": [
            {"language": "en", "kind": "manual", "name": "English"},
            {"language": "en", "kind": "auto", "name": "English"},
        ]})
        dialog.subtitle_combo.setCurrentIndex(2)
        assert dialog.download_button.text() == "Get subtitles"
        dialog.download_button.click()
        assert requests[0]["action"] == "subtitles"
        assert requests[0]["audio_path"] == str(audio)
        assert requests[0]["subtitle"] == {"language": "en", "kind": "auto"}
        assert requests[0]["subtitles_as_lyrics"]
        assert not dialog.subtitle_combo.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_completed_subtitles_are_attached_and_remembered_before_import(qapp, tmp_path):
    api = CaptionAPI()
    audio, native, lyrics = [tmp_path / name for name in ("song.m4a", "song.en.manual.vtt", "song.en.manual.lrc")]
    for path in (audio, native, lyrics):
        path.touch()
    dialog = ImportDialog(api, DEFAULTS)
    try:
        dialog.info = {"identity": "Test:song"}
        result = {"path": str(audio), "subtitle_path": str(native), "lyrics_path": str(lyrics),
                  "subtitle": {"language": "en", "kind": "manual"}}
        dialog._succeeded("subtitles", result)
        assert api.attached == [(str(audio), str(lyrics))]
        assert api.imports == [[str(audio)]]
        assert api.settings["downloads"]["Test:song:m4a"]["subtitle_path"] == str(native)
        dialog.url.setText("https://example.com/another")
        assert dialog.subtitle_combo.count() == 1
        assert not dialog.subtitles_as_lyrics.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
