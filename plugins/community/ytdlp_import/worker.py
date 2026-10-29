"""Isolated yt-dlp helper. JSON requests on stdin; prefixed JSON events on stdout."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

PREFIX = "LYRICSPLUS_YTDLP "

if __package__:
    from .captions import convert_caption_file, caption_style_warning
else:
    # Isolated Python excludes the script directory from its import path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from captions import convert_caption_file, caption_style_warning


def emit(event, **values):
    print(PREFIX + json.dumps(dict(event=event, **values), ensure_ascii=True), flush=True)


def validate_url(value):
    value = str(value).strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or any(c.isspace() for c in value):
        raise ValueError("Paste a complete http:// or https:// URL for one video or audio track.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing login credentials are not supported.")
    return value


def identity(info):
    return str(info.get("extractor_key") or info.get("extractor") or "") + ":" + str(info.get("id") or "")


def single_track(info):
    if not isinstance(info, dict) or "entries" in info or info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Paste a single-track URL. Playlist and channel imports are not supported yet.")
    if info.get("is_live") or info.get("live_status") == "is_upcoming":
        raise ValueError("Live and upcoming broadcasts are not supported. Choose a finished recording.")
    if not info.get("id"):
        raise ValueError("The URL did not resolve to an identifiable audio/video track.")
    return info


def tool_paths(config, *, require=False):
    location = str(config.get("ffmpeg_location", "")).strip()
    if location:
        path = Path(location).expanduser()
        folder = path if path.is_dir() else path.parent
        ffmpeg = shutil.which("ffmpeg", path=str(folder))
        ffprobe = shutil.which("ffprobe", path=str(folder))
    else:
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if require and (not ffmpeg or not ffprobe):
        raise ValueError("FFmpeg and ffprobe are required. Install them or select their folder in the plugin settings.")
    runtime = str(config.get("js_runtime", "auto"))
    if runtime not in {"auto", "deno", "node", "quickjs"}:
        raise ValueError("Select Auto, Deno, Node, or QuickJS in the plugin settings.")
    configured = str(config.get("js_path", "")).strip()
    if configured and runtime == "auto":
        raise ValueError("Choose a JavaScript runtime type when specifying its executable path.")
    runtimes = {}
    for name in ([runtime] if runtime != "auto" else ["deno", "node", "quickjs"]):
        executable = configured or ("qjs" if name == "quickjs" else name)
        found = shutil.which(str(Path(executable).expanduser()))
        if found:
            runtimes[name] = {"path": found}
            if runtime == "auto":
                break
    if runtime != "auto" and not runtimes:
        raise ValueError(f"The selected {runtime} executable was not found. Check the plugin settings.")
    return {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "js_runtimes": runtimes}


class Logger:
    def debug(self, message):
        if not str(message).startswith("[debug]"):
            emit("log", message=str(message)[:2000])

    info = debug

    def warning(self, message):
        emit("log", message="Warning: " + str(message)[:2000])

    def error(self, message):
        emit("log", message=str(message)[:2000])


def options(config):
    tools = tool_paths(config)
    values = {
        "quiet": True, "no_warnings": False, "noprogress": True,
        "noplaylist": True, "playlistend": 1, "extract_flat": "in_playlist",
        "socket_timeout": 20, "retries": 2, "fragment_retries": 2,
        "logger": Logger(), "js_runtimes": tools["js_runtimes"],
        "cachedir": False, "windowsfilenames": True,
    }
    if tools["ffmpeg"]:
        values["ffmpeg_location"] = str(Path(tools["ffmpeg"]).parent)
    return values


def metadata(info):
    return {
        "identity": identity(info), "title": str(info.get("title") or info.get("id")),
        "artist": str(info.get("artist") or info.get("uploader") or ""),
        "duration": info.get("duration"), "url": str(info.get("webpage_url") or ""),
    }


def subtitle_tracks(info):
    tracks = []
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        for language, formats in sorted((info.get(key) or {}).items()):
            supported = [item for item in formats if item.get("ext") in {"vtt", "srt"}]
            if language == "live_chat" or not supported:
                continue
            name = next((item.get("name") for item in supported if item.get("name")), language)
            tracks.append({"language": language, "kind": kind, "name": str(name)})
    return tracks


def ass_subtitle_tracks(info):
    return [{"language": language, "kind": kind}
            for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions"))
            for language, formats in (info.get(key) or {}).items()
            if any(str(item.get("ext", "")).lower() in {"ass", "ssa"} for item in formats)]


def subtitle_options(request):
    selected = request.get("subtitle")
    if not selected:
        return {}
    if (not isinstance(selected, dict) or selected.get("kind") not in {"manual", "auto"}
            or not isinstance(selected.get("language"), str) or not selected["language"]):
        raise ValueError("Select a subtitle language and caption source.")
    return {"writesubtitles": selected["kind"] == "manual", "writeautomaticsub": selected["kind"] == "auto",
            "subtitleslangs": [re.escape(selected["language"])], "subtitlesformat": "vtt/srt"}


def validate_subtitle(info, request):
    selected = request.get("subtitle")
    if selected and not any(track["language"] == selected["language"] and track["kind"] == selected["kind"]
                            for track in subtitle_tracks(info)):
        raise ValueError("The selected subtitles are no longer available. Preview the URL again.")


def preview(url, config):
    from yt_dlp import YoutubeDL

    with YoutubeDL(options(config)) as ydl:
        info = single_track(ydl.extract_info(validate_url(url), download=False))
        result = metadata(info)
        result["subtitle_tracks"] = subtitle_tracks(info)
        result["ass_subtitle_tracks"] = ass_subtitle_tracks(info)
        thumbnail = info.get("thumbnail")
        if thumbnail and urlsplit(thumbnail).scheme in {"http", "https"}:
            try:
                with ydl.urlopen(thumbnail) as response:
                    data = response.read(2_000_001)
                if len(data) <= 2_000_000:
                    result["thumbnail"] = base64.b64encode(data).decode("ascii")
            except Exception:
                pass  # Artwork preview must not prevent importing audio.
        return result


def publish_bundle(stem, files, destination):
    """Publish matching files together, rolling back collisions without overwrites."""
    destination = Path(destination)
    for number in range(1, 10001):
        suffix = "" if number == 1 else f" ({number})"
        created = []
        try:
            result = {}
            for extension, source in files.items():
                source = Path(source)
                target = destination / (stem + suffix + extension)
                moved = False
                try:
                    os.link(source, target)
                except FileExistsError:
                    raise
                except OSError:
                    if os.name != "nt":
                        raise
                    source.rename(target)  # Windows rename refuses overwrites.
                    moved = True
                created.append((source, target, moved))
                result[extension] = target
            return result
        except OSError as error:
            collision = isinstance(error, FileExistsError)
            for source, target, moved in reversed(created):
                if moved:
                    target.rename(source)
                else:
                    target.unlink()
            if not collision:
                raise
    raise ValueError("Too many downloads have the same filename. Choose another folder.")


def publish_download(source, destination):
    source = Path(source)
    return publish_bundle(source.stem, {source.suffix: source}, destination)[source.suffix]


def collect_subtitles(info, request, stage):
    selected = request.get("subtitle")
    if not selected:
        return {}, {}
    caption = (info.get("requested_subtitles") or {}).get(selected["language"], {})
    if caption.get("filepath"):
        source = Path(caption["filepath"]).resolve()
    else:
        # Some processed format snapshots omit requested_subtitles. Only one
        # language/format was requested, and this staging directory is private
        # to the operation, so its single VTT/SRT is unambiguous.
        candidates = [path.resolve() for path in stage.iterdir()
                      if path.suffix.lower() in {".vtt", ".srt"} and path.is_file()]
        if len(candidates) != 1:
            raise ValueError("The selected subtitle file was not downloaded successfully.")
        source = candidates[0]
    if not source.is_relative_to(stage) or not source.is_file() or source.suffix.lower() not in {".vtt", ".srt"}:
        raise ValueError("The selected subtitle file was not downloaded successfully.")
    files = {source.suffix.lower(): source}
    result = {"subtitle": dict(selected), "subtitle_extension": source.suffix.lower()}
    style_warning = caption_style_warning(source.read_text(encoding="utf-8-sig", errors="replace"))
    if style_warning:
        result["subtitle_warning"] = style_warning
        emit("subtitle_warning", message=style_warning)
    if request.get("subtitles_as_lyrics", True):
        lyrics = stage / "converted-captions.lrc"
        try:
            convert_caption_file(source, lyrics, automatic=selected["kind"] == "auto")
            files[".lrc"] = lyrics
        except (OSError, ValueError) as error:
            result["warning"] = "Subtitles saved, but synced lyrics could not be created: " + str(error)
    return files, result


def subtitle_result(result, published, *, prefix=""):
    if "subtitle" not in result:
        return result
    result["subtitle_path"] = str(published[prefix + result.pop("subtitle_extension")])
    if prefix + ".lrc" in published:
        result["lyrics_path"] = str(published[prefix + ".lrc"])
    return result


def checked_stage(request):
    stage = Path(request["stage"]).resolve()
    destination = Path(request["folder"]).expanduser().resolve()
    if stage.parent != destination or not stage.name.startswith(".lyricsplus-import-") or not stage.is_dir():
        raise ValueError("Invalid download staging directory.")
    return stage, destination


def caption_suffix(selected):
    language = re.sub(r"[^a-zA-Z0-9_.-]", "_", selected["language"])[:64]
    return "." + language + "." + selected["kind"]


def download_subtitles(request):
    from yt_dlp import YoutubeDL

    stage, destination = checked_stage(request)
    audio = Path(request["audio_path"]).resolve()
    if audio.parent != destination or not audio.is_file() or audio.suffix.lower() not in {".m4a", ".mp3"}:
        raise ValueError("The existing audio file is unavailable.")
    if not request.get("subtitle"):
        raise ValueError("Select subtitles to download.")
    opts = options(request["config"])
    opts.update(subtitle_options(request))
    opts.update(skip_download=True, outtmpl=str(stage / "captions.%(ext)s"))
    with YoutubeDL(opts) as ydl:
        info = single_track(ydl.extract_info(validate_url(request["url"]), download=False))
        if identity(info) != request["identity"]:
            raise ValueError("The URL now points to a different track. Preview it again.")
        validate_subtitle(info, request)
        processed = ydl.process_ie_result(info, download=True)
    files, details = collect_subtitles(processed or info, request, stage)
    published = publish_bundle(audio.stem + caption_suffix(request["subtitle"]), files, destination)
    return dict(metadata(info), path=str(audio), **subtitle_result(details, published))


def download(request):
    from yt_dlp import YoutubeDL
    from yt_dlp.postprocessor import PostProcessor

    config = request["config"]
    tool_paths(config, require=True)
    stage, destination = checked_stage(request)
    codec = request.get("codec", "m4a")
    if codec not in {"m4a", "mp3"}:
        raise ValueError("Select M4A or MP3 audio.")
    captured = []

    class CaptureFinalFile(PostProcessor):
        def run(self, info):
            captured.append(info)
            return [], info

    last_progress = 0.0

    def progress(data):
        nonlocal last_progress
        now = time.monotonic()
        if data.get("status") == "finished":
            emit("processing", message="Converting audio and embedding metadata…")
        elif now - last_progress >= 0.15:
            last_progress = now
            emit("progress", downloaded=data.get("downloaded_bytes", 0),
                 total=data.get("total_bytes") or data.get("total_bytes_estimate"),
                 speed=data.get("speed"), eta=data.get("eta"))

    opts = options(config)
    opts.update(subtitle_options(request))
    opts.update({
        "format": "m4a/bestaudio/best" if codec == "m4a" else "bestaudio/best",
        "outtmpl": str(stage / "%(title).140B [%(id)s].%(ext)s"),
        "writethumbnail": True, "overwrites": False,
        "progress_hooks": [progress],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": "192"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "EmbedThumbnail"},
        ],
    })
    with YoutubeDL(opts) as ydl:
        info = single_track(ydl.extract_info(validate_url(request["url"]), download=False))
        if identity(info) != request["identity"]:
            raise ValueError("The URL now points to a different track. Preview it again before downloading.")
        validate_subtitle(info, request)
        ydl.add_post_processor(CaptureFinalFile(ydl), when="after_move")
        ydl.process_ie_result(info, download=True)
    if len(captured) != 1:
        raise ValueError("The downloader did not produce one completed audio file.")
    source = Path(captured[0]["filepath"]).resolve()
    if not source.is_relative_to(stage) or not source.is_file() or source.suffix != "." + codec:
        raise ValueError("The downloader returned an invalid audio file.")
    caption_files, details = collect_subtitles(captured[0], request, stage)
    prefix = caption_suffix(request["subtitle"]) if request.get("subtitle") else ""
    # Publish sidecars before the audio; audio becomes visible only when all
    # selected outputs have been written. Collisions keep a shared basename.
    files = {prefix + extension: path for extension, path in caption_files.items()}
    files[source.suffix] = source
    published = publish_bundle(source.stem, files, destination)
    return dict(metadata(info), path=str(published[source.suffix]), **subtitle_result(details, published, prefix=prefix))


def check_setup(config):
    from yt_dlp.version import __version__

    tools = tool_paths(config)
    lines = [f"yt-dlp {__version__}"]
    for name in ("ffmpeg", "ffprobe"):
        path = tools[name]
        lines.append(f"{name}: {path or 'not found'}")
        if path:
            result = subprocess.run([path, "-version"], capture_output=True, timeout=8,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError(f"{name} could not run. Check its installation.")
    if not tools["js_runtimes"]:
        lines.append("JavaScript runtime: not found. YouTube needs a supported Deno, Node, or QuickJS runtime.")
    for name, value in tools["js_runtimes"].items():
        result = subprocess.run([value["path"], "--version"], capture_output=True, timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        version = result.stdout.decode("utf-8", "replace").splitlines()
        lines.append(f"{name}: {version[0] if version else value['path']}")
    try:
        import yt_dlp_ejs  # noqa: F401
        lines.append("YouTube challenge scripts: installed")
    except ImportError:
        lines.append("YouTube challenge scripts: missing. Install/repair yt-dlp[default] in Settings > Dependencies, then restart.")
    return {"message": "\n".join(lines)}


def main():
    site = os.environ.get("LRCPLUS_DEPENDENCY_SITE")
    if site and site not in sys.path:
        sys.path.append(site)
    if os.name != "nt":
        os.setsid()  # Cancellation can stop the downloader and converter together.
    try:
        request = json.load(sys.stdin)
        action = request["action"]
        if action == "preview":
            result = preview(request["url"], request["config"])
        elif action == "download":
            result = download(request)
        elif action == "subtitles":
            result = download_subtitles(request)
        elif action == "check":
            result = check_setup(request["config"])
        else:
            raise ValueError("Unknown downloader operation.")
        emit("result", result=result)
        return 0
    except Exception as error:
        emit("error", message=str(error)[:4000])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
