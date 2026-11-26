"""Persistent track scheduler, independent of the import window."""
from copy import deepcopy
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from lyrics.timing_drift import estimate_timing_correction, load_timing_reference
from .job import ImportJob, YOUTUBE_AGE_SESSION_HINT, is_youtube_age_restriction
from .worker import validate_url, validate_youtube_session_url


TERMINAL = {"completed", "failed", "cancelled", "skipped"}
SESSION_RETRY = "YouTube session cleared. Use Retry with YouTube session to continue."


def source_key(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"}:
        video = parse_qs(parsed.query).get("v", [None])[0]
        if video:
            return "youtube:" + video
    if host == "youtu.be":
        return "youtube:" + parsed.path.strip("/")
    return url


def _check_lyrics_drift(result):
    lyrics = Path(result.get("lyrics_path") or "")
    subtitles = Path(result.get("subtitle_path") or "")
    if lyrics.suffix.lower() not in {".lrc", ".lrcx"} or not lyrics.is_file():
        return "Needs timing review", "No timed lyric file is available."
    if subtitles.suffix.lower() not in {".srt", ".vtt"} or not subtitles.is_file():
        return "Needs timing review", "No timed subtitles are available for this recording."
    if not result.get("lyrics_source") and lyrics.stem == subtitles.stem:
        return "Timing from subtitles", "The attached lyrics were generated from this recording's subtitles."
    try:
        lines, offset = load_timing_reference(lyrics)
        reference, reference_offset = load_timing_reference(
            subtitles, automatic_captions=(result.get("subtitle") or {}).get("kind") == "auto",
        )
        correction = estimate_timing_correction(lines, reference, offset_ms=offset,
                                                reference_offset_ms=reference_offset)
        if correction is None:
            return "Timing matches subtitles", "Matched lyric lines closely follow this recording's subtitles."
        duration = result.get("duration")
        correction.apply(lines, offset_ms=offset, duration_ms=duration * 1000 if isinstance(duration, (int, float)) else 0)
    except (OSError, ValueError, OverflowError) as error:
        return "Needs timing review", str(error)
    return ("Timing correction available",
            f"{len(correction.matches)} lines matched in {subtitles.name}. Open Timing tools → "
            f"Correct Timing Drift in the lyrics editor for this song.")


class DownloadQueue(QObject):
    changed = Signal()
    error = Signal(str)
    nightly_suggested = Signal()

    def __init__(self, api, parent=None, *, job_factory=ImportJob):
        super().__init__(parent)
        self.api = api
        self.job_factory = job_factory
        self.jobs = {}
        self._youtube_sessions = {}
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setSingleShot(True)
        self._schedule_timer.timeout.connect(self.pump)
        self.stopping = False
        self.last_error = ""
        try:
            self.limit = max(1, min(4, int(api.get_setting("concurrent_downloads", 2))))
        except (ValueError, TypeError, OverflowError):
            self.limit = 2
        saved = api.get_setting("download_queue", [])
        self.items = []
        for item in saved if isinstance(saved, list) else []:
            if (not isinstance(item, dict) or not isinstance(item.get("request"), dict)
                    or not isinstance(item.get("id"), str) or not item["id"]
                    or not isinstance(item.get("state"), str)):
                continue
            item = deepcopy(item)
            if any(existing["id"] == item["id"] for existing in self.items):
                continue
            request = item["request"]
            request.pop("youtube_session", None)
            if (not isinstance(request.get("config"), dict) or not isinstance(request.get("folder"), str)
                    or not request["folder"] or not isinstance(request.get("url"), str)
                    or request.get("codec") not in {"m4a", "mp3"}):
                continue
            try:
                validate_url(request.get("url", ""))
            except ValueError:
                continue
            if item.get("state") == "completed" and (not isinstance(item.get("result"), dict)
                                                       or not isinstance(item["result"].get("path"), str)):
                item.update(state="failed", message="Missing saved download result.")
            item["title"] = str(item.get("title") or request["url"])
            item.setdefault("imported", False)
            if item.get("state") not in TERMINAL:
                item.update(state="waiting", message="Restored; ready to restart.")
            self.items.append(item)
        self.paused = any(
            item["state"] == "waiting" or (item["state"] == "completed" and not item["imported"])
            for item in self.items
        )

    def _save(self):
        saved = deepcopy(self.items)
        for item in saved:
            # Preview artwork is transient; restored jobs always re-prepare.
            item["request"].pop("musicbrainz_cover", None)
            item["request"].pop("info", None)
            item.pop("next_action", None)
            if item["id"] in self._youtube_sessions and item["state"] not in TERMINAL:
                item.update(state="failed", message=SESSION_RETRY, details=SESSION_RETRY)
        try:
            self.api.set_setting("download_queue", saved)
            self.last_error = ""
        except OSError as error:
            self.last_error = "Could not save download queue: " + str(error)
            self.error.emit(self.last_error)
        self.changed.emit()

    def enqueue(self, entries, options, *, force=False, youtube_session=None):
        # Validate the whole batch before changing the queue.
        if youtube_session is not None and len(entries) != 1:
            raise ValueError("Choose one YouTube video to use a session.")
        validate = validate_url if youtube_session is None else validate_youtube_session_url
        entries = [dict(entry, url=validate(entry["url"])) for entry in entries]
        added = 0
        for entry in entries:
            key = source_key(entry["url"])
            if not force and any(source_key(item["request"]["url"]) == key
                                 and item["request"].get("codec") == options.get("codec")
                                 and item["state"] not in TERMINAL
                                 for item in self.items):
                continue
            request = deepcopy(options)
            request.pop("youtube_session", None)
            request.update(url=entry["url"], force=force)
            item_id = uuid4().hex
            self.items.append({"id": item_id, "title": entry.get("title") or entry["url"],
                               "request": request, "state": "waiting", "message": "Waiting",
                               "imported": False})
            if youtube_session is not None:
                self._youtube_sessions[item_id] = dict(youtube_session)
            added += 1
        self._save()
        self._schedule_timer.start(0)
        return added

    def set_paused(self, paused):
        self.paused = bool(paused)
        if not self.paused:
            self._import_ready()
            self.pump()
        self._save()

    def set_limit(self, value):
        self.limit = max(1, min(4, int(value)))
        self.api.set_setting("concurrent_downloads", self.limit)
        self.pump()

    def pump(self):
        if self.stopping or self.paused:
            return
        for item in self.items:
            if len(self.jobs) >= self.limit:
                break
            if item["state"] == "waiting" and item["id"] not in self.jobs:
                self._start(item, "preview")

    def _start(self, item, action):
        request = deepcopy(item["request"])
        request["action"] = action
        if action != "lyrics" and item["id"] in self._youtube_sessions:
            request["youtube_session"] = self._youtube_sessions[item["id"]]
        item.update(state="preparing" if action == "preview" else "downloading", message=action.title())
        job = self.job_factory(request, self)
        self.jobs[item["id"]] = job
        job.event_received.connect(lambda event: self._event(item, event))
        job.succeeded.connect(lambda result: self._success(item, action, result))
        job.failed.connect(lambda message: self._fail(item, message, used_youtube_session="youtube_session" in request))
        job.cancelled.connect(lambda: item.update(state="cancelled", message="Cancelled"))
        job.finished.connect(lambda: self._finished(item, job))
        self._save()
        job.start()

    def _event(self, item, event):
        kind = event.get("event")
        if kind == "progress":
            total = event.get("total") or 0
            downloaded = event.get("downloaded") or 0
            known_size = (isinstance(total, (int, float)) and math.isfinite(total) and total > 0
                          and isinstance(downloaded, (int, float)) and math.isfinite(downloaded))
            item["message"] = f"Downloading {max(0, min(99, int(downloaded / total * 100)))}%" if known_size else "Downloading"
            speed, eta = event.get("speed"), event.get("eta")
            if isinstance(speed, (int, float)) and math.isfinite(speed) and speed > 0:
                item["message"] += f" · {speed / 1_000_000:.2f} MB/s"
            if isinstance(eta, (int, float)) and math.isfinite(eta) and eta >= 0:
                item["message"] += f" · {eta:.0f}s remaining"
        elif kind in {"processing", "subtitle_warning", "log"}:
            message = str(event.get("message", ""))
            item["message"] = message
            item["details"] = (item.get("details", "") + "\n" + message)[-16000:]
        self.changed.emit()

    def _success(self, item, action, result):
        if item["state"] == "cancelled":
            return
        if action == "preview":
            request = item["request"]
            request["identity"] = result["identity"]
            item["title"] = result.get("title") or item["title"]
            if not request.get("force") and any(
                    other is not item and other["state"] not in TERMINAL
                    and other["request"].get("identity") == result["identity"]
                    and other["request"].get("codec") == request["codec"] for other in self.items):
                item.update(state="skipped", message="This track is already downloading.")
                return
            selected = request.get("subtitle")
            if selected and selected not in [{"language": track["language"], "kind": track["kind"]}
                                             for track in result.get("subtitle_tracks", [])]:
                request["subtitle"] = None
                item["details"] = "Preferred subtitles unavailable; downloading audio without subtitles."
            if result.get("musicbrainz_cover"):
                request["musicbrainz_cover"] = dict(result["musicbrainz_cover"], data=result.get("thumbnail", ""))
            history = self.api.get_setting("downloads", {})
            entry = history.get(result["identity"] + ":" + request["codec"], {}) if isinstance(history, dict) else {}
            entry = entry if isinstance(entry, dict) else {}
            existing = entry.get("path") if isinstance(entry.get("path"), str) else None
            if existing and Path(existing).is_file() and not request.get("force"):
                selected = request.get("subtitle")
                needs_subtitles = selected and (entry.get("subtitle") != selected
                                               or not Path(entry.get("subtitle_path") or "").is_file())
                needs_lyrics = request["config"].get("automatically_find_lyrics") and not Path(entry.get("lyrics_path") or "").is_file()
                if needs_subtitles or needs_lyrics:
                    request.update(audio_path=existing, info=result, existing_lyrics_path=entry.get("lyrics_path"),
                                   folder=str(Path(existing).parent))
                    item["existing_entry"] = entry
                    item["next_action"] = "subtitles" if needs_subtitles else "lyrics"
                else:
                    item.update(state="completed", message="Using existing download", result=dict(entry, identity=result["identity"]))
            else:
                item["next_action"] = "download"
        else:
            if action == "subtitles" and "lyrics_path" in result and "lyrics_source" not in result:
                item.get("existing_entry", {}).pop("lyrics_source", None)
            result = dict(item.pop("existing_entry", {}), **result)
            if not result.get("path") or not Path(result["path"]).is_file():
                self._fail(item, "The downloaded file could not be found.")
                return
            item.update(state="completed", message="Completed", result=result)
            item["details"] = "\n".join(filter(None, (item.get("details"), result.get("warning"),
                                                       result.get("subtitle_warning"), result.get("lyrics_message"))))
            item["request"].pop("musicbrainz_cover", None)
            item["request"].pop("info", None)
            history = self.api.get_setting("downloads", {})
            history = dict(history) if isinstance(history, dict) else {}
            history[item["request"]["identity"] + ":" + item["request"]["codec"]] = result
            try:
                self.api.set_setting("downloads", dict(list(history.items())[-1000:]))
            except OSError as error:
                item["details"] = "Could not save download history: " + str(error)

    def _fail(self, item, message, *, used_youtube_session=False):
        details = str(message)
        if is_youtube_age_restriction(details):
            if used_youtube_session:
                message = "YouTube reported an age restriction with the selected session. " + YOUTUBE_AGE_SESSION_HINT
            else:
                message = "YouTube reported an age restriction. Select this track and choose Retry with YouTube session…"
            details = f"{message}\n\n{details}"
        item.update(state="failed", message=str(message), details=details)

    def _finished(self, item, job):
        self.jobs.pop(item["id"], None)
        if item["state"] == "failed" and getattr(job, "nightly_candidate", False) and not self.stopping:
            self.nightly_suggested.emit()
        job.deleteLater()
        action = item.pop("next_action", None)
        if action and item["state"] != "cancelled" and not self.stopping:
            self._start(item, action)
        else:
            self._youtube_sessions.pop(item["id"], None)
            self._import_ready()
            self._save()
        if not self.stopping:
            self._schedule_timer.start(0)

    def _import_ready(self):
        # Complete library/playback imports in selection order, even when later
        # workers finish first. Failed/cancelled tracks do not block successors.
        for item in self.items:
            if item["state"] not in TERMINAL:
                break
            if item["state"] != "completed" or item.get("imported"):
                continue
            result, request = item["result"], item["request"]
            try:
                if result.get("lyrics_path") and (result.get("lyrics_source") in {"LRCLIB", "existing"}
                                                   or request.get("subtitles_as_lyrics")):
                    self.api.attach_lyrics(result["path"], result["lyrics_path"])
                self.api.import_audio_files([result["path"]], add_to_queue=request.get("add_to_queue", True))
                item["imported"] = True
                item["message"] = "Added to library and playback queue" if request.get("add_to_queue", True) else "Added to library"
            except (OSError, ValueError, RuntimeError) as error:
                self._fail(item, "File saved, but import failed: " + str(error))
                continue
            if request["config"].get("review_lyrics_drift"):
                status, details = _check_lyrics_drift(result)
                item["message"] += " · " + status
                item["details"] = "\n".join(filter(None, (item.get("details"), f"{status}. {details}")))

    def cancel(self, ids):
        for item in self.items:
            if item["id"] in ids and item["state"] not in TERMINAL:
                item.update(state="cancelled", message="Cancelled")
                self._youtube_sessions.pop(item["id"], None)
                job = self.jobs.get(item["id"])
                if job:
                    job.cancel()
        self._import_ready()
        self._save()

    def retry(self, ids):
        for item in self.items:
            if item["id"] in ids and item["state"] in {"failed", "cancelled"} and item["id"] not in self.jobs:
                item.update(state="waiting", message="Waiting", imported=False)
        self._save()
        self.pump()

    def retry_with_youtube_session(self, item, source):
        if item["state"] != "failed" or item["id"] in self.jobs or item not in self.items:
            return
        validate_youtube_session_url(item["request"]["url"])
        self._youtube_sessions[item["id"]] = dict(source)
        self.retry({item["id"]})

    def remove(self, ids):
        self.items = [item for item in self.items if item["id"] not in ids or item["id"] in self.jobs
                      or (item["state"] == "completed" and not item.get("imported"))]
        self._import_ready()
        self._save()
        self.pump()

    def shutdown(self):
        self.stopping = True
        self._schedule_timer.stop()
        interrupted = set(self.jobs)
        authenticated = set(self._youtube_sessions)
        for job in list(self.jobs.values()):
            job.shutdown()
        for item in self.items:
            if item["id"] in authenticated:
                item.update(state="failed", message=SESSION_RETRY, details=SESSION_RETRY)
            elif item["state"] not in TERMINAL or item["id"] in interrupted:
                item.update(state="waiting", message="Interrupted; ready to restart.")
        self._youtube_sessions.clear()
        self._save()
