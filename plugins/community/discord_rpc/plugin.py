from __future__ import annotations

import hashlib
from io import BytesIO
import threading
import time
from pathlib import Path
import json
import os
import random
import re
import unicodedata

import requests
from PIL import Image, ImageOps

from lyrics.furigana import detect_lyrics_collection_language, get_reading_mode

try:
    from pypresence import Presence
    from pypresence.types import ActivityType
except ImportError:
    Presence = None
    ActivityType = None

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None


DISCORD_CLIENT_ID = "1539512946437390406"

LITTERBOX_UPLOAD_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
LITTERBOX_EXPIRY = "12h"
LITTERBOX_EXPIRY_SECONDS = 12 * 60 * 60
LITTERBOX_CACHE_SAFETY_SECONDS = 10 * 60
LITTERBOX_MAX_COVER_EDGE = 512
LITTERBOX_MAX_UPLOAD_BYTES = 1_000_000
# Bound decoding work independently of the much smaller upload limits.
LITTERBOX_MAX_INPUT_BYTES = 20_000_000
LITTERBOX_MAX_INPUT_PIXELS = 25_000_000

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = "LRCPlus/1.0 (Discord Rich Presence plugin)"
MUSICBRAINZ_MIN_SCORE = 80

# Lyrics are intentionally batched so Discord RPC is not updated for every
# single line. A new lyric is published when playback enters the next block.
LYRIC_LINES_MIN = 1
LYRIC_LINES_MAX = 15
LYRIC_LINES_DEFAULT = 10
LYRIC_UPDATE_COOLDOWN = 5.0

DEFAULT_SETTINGS = {
    "activity_type": "listening",
    "details_template": "{title}",
    "state_template": "{album_or_artist}{lyric_suffix}",
    "large_text_template": "{artist}",
    "show_timestamps": True,
    "show_when_paused": True,
    "include_lyrics": True,
    "lyric_lines_per_update": LYRIC_LINES_DEFAULT,
    "lookup_online_artwork": True,
    "upload_embedded_artwork": True,
    "placeholder_language": "auto",
}

ACTIVITY_TYPES = {
    "listening": "Listening to",
    "playing": "Playing",
    "watching": "Watching",
    "competing": "Competing in",
}
PLACEHOLDER_LANGUAGES = {
    "auto": "Auto (from lyrics)", "jp": "Japanese", "kr": "Korean",
    "zh": "Chinese", "hk": "Cantonese", "vi": "Vietnamese",
}
CLASSICAL_CHINESE_CHANCE = 0.1
PLACEHOLDER_URLS = {
    # Use the PNG files themselves; the share links return HTML viewer pages.
    "playing": {
        "jp": "https://cdn.nest.rip/uploads/a4e5f6dc-d3a2-432f-84fa-1ce25d603aeb.png",
        "kr": "https://cdn.nest.rip/uploads/3767537a-97af-4218-b13b-9795ecd2df17.png",
        "zh": "https://cdn.nest.rip/uploads/b68f16c5-60bf-4627-aaf8-49b10f178278.png",
        "hk": "https://cdn.nest.rip/uploads/d33d7b42-954a-486d-bf88-f09407d80651.png",
        "vi": "https://cdn.nest.rip/uploads/f8c0989b-b820-4b24-b340-6e82df92dee5.png",
        "lzh": "https://cdn.nest.rip/uploads/7e0eea83-ffeb-4e8f-bde2-bf81185c04f3.png",
    },
    "paused": {
        "jp": "https://cdn.nest.rip/uploads/1e846fcc-350a-4cba-a78e-d1352b16493d.png",
        "kr": "https://cdn.nest.rip/uploads/ddf02f86-59e0-470c-a59d-4461898d5bdd.png",
        "zh": "https://cdn.nest.rip/uploads/41af192b-6eb8-4ee1-ac22-9c1c3c111caf.png",
        "hk": "https://cdn.nest.rip/uploads/eef908ba-0db8-4e46-8bc9-81f17e7cf0c7.png",
        "vi": "https://cdn.nest.rip/uploads/ed6bcecd-a8ac-4c30-ae20-4331fb097cb8.png",
        "lzh": "https://cdn.nest.rip/uploads/f2109380-2839-4af2-9813-1b9cacc2cb71.png",
    },
    "idle": {
        "jp": "https://cdn.nest.rip/uploads/eb89a75f-d175-4f2b-a0a2-ad6806410623.png",
        "kr": "https://cdn.nest.rip/uploads/3e764aaa-7fdb-4b6a-87a7-c47614029e89.png",
        "zh": "https://cdn.nest.rip/uploads/437c2f60-6b80-4c2f-81c1-bbd4436054f6.png",
        "hk": "https://cdn.nest.rip/uploads/0c0465d2-f5b8-4fc8-b8f2-2a664e472297.png",
        "vi": "https://cdn.nest.rip/uploads/d3240dac-f982-485e-9b7f-374e6334fa38.png",
        "lzh": "https://cdn.nest.rip/uploads/145d4baa-7d93-4aa2-998a-02f5c30da20b.png",
    },
}
_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]")
_VIETNAMESE = re.compile(r"[ăâđêôơư\u1ea0-\u1ef9]", re.IGNORECASE)
_CANTONESE_MARKERS = frozenset_CANTONESE_MARKERS = frozenset(
    "嘅咗唔喺啲佢哋咁嚟乜咩冇畀嗰俾啱噉喎嗱"
    "睇搵攞嘢瞓嚿𠵱"
    "咪囉囖嘞啩噃㗎㖭"
    "𨳊㞗閪"
    )
TEMPLATE_KEYS = ("details_template", "state_template", "large_text_template")


class DiscordRpcPlugin:
    name = "Discord Rich Presence"
    version = "1.3.0"
    description = (
        "Show the current track on Discord with metadata and album artwork."
    )

    def __init__(self):
        self.context = None
        self.window = None
        self.settings = dict(DEFAULT_SETTINGS)
        self.rpc = None
        self.connected = False
        self._rpc_task_lock = threading.Lock()
        self._rpc_pending_task = None
        self._rpc_worker_active = False
        self._rpc_retry_after = 0.0
        self._rpc_unavailable_logged = False

        self.current_path = None
        self._last_payload = None
        self._last_cover = None
        self._placeholder_detection_key = None
        self._placeholder_lyrics = None
        self._detected_placeholder_language = "jp"
        self._placeholder_easter_egg_track = None
        self._classical_chinese_easter_egg = None

        # Per-session caches. No repeated lookups or uploads for the same file.
        self._metadata_cache = {}
        self._cover_cache = {}
        self._cover_jobs = set()
        self._persistent_cover_cache = self._load_persistent_cover_cache()

        # MusicBrainz asks clients to stay within its request rate limit.
        self._last_musicbrainz_request = 0.0

        # Discord lyric publishing state.
        self._lyric_bucket = None
        self._current_rpc_lyric = None
        self._last_lyric_rpc_update = 0.0

    def on_load(self, context):
        self.context = context
        self.settings = {
            key: context.api.get_setting(key, default)
            for key, default in DEFAULT_SETTINGS.items()
        }
        self.settings["lyric_lines_per_update"] = (
            self._normalize_lyric_lines_per_update(
                self.settings["lyric_lines_per_update"]
            )
        )
        if (
            not isinstance(self.settings["activity_type"], str)
            or self.settings["activity_type"] not in ACTIVITY_TYPES
        ):
            self.settings["activity_type"] = DEFAULT_SETTINGS["activity_type"]
        for key in TEMPLATE_KEYS:
            if not isinstance(self.settings[key], str):
                self.settings[key] = DEFAULT_SETTINGS[key]
        self.settings["placeholder_language"] = self._placeholder_language_setting()
        context.on("app_ready", self.on_app_ready)
        context.api.register_settings_widget(
            "Discord Rich Presence",
            self.create_settings_widget,
            apply=self.apply_settings_widget,
            description="Customize your activity text, playback display, lyrics, and artwork sources.",
            order=20,
        )

    @staticmethod
    def _normalize_lyric_lines_per_update(value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = LYRIC_LINES_DEFAULT
        return max(LYRIC_LINES_MIN, min(LYRIC_LINES_MAX, value))

    def on_unload(self, context):
        self.disconnect()

    def on_app_ready(self, window):
        self.window = window

        player = getattr(window, "media_player", None)
        if player is None:
            return

        player.mediaStatusChanged.connect(self.on_media_status_changed)
        player.playbackStateChanged.connect(self.on_playback_state_changed)
        player.positionChanged.connect(self.on_position_changed)

        self.update_presence()

    # ------------------------------------------------------------------
    # Discord connection
    # ------------------------------------------------------------------

    def _schedule_rpc_task(self, action, payload=None):
        with self._rpc_task_lock:
            self._rpc_pending_task = (action, payload)
            if self._rpc_worker_active:
                return
            self._rpc_worker_active = True

        threading.Thread(
            target=self._run_rpc_tasks,
            daemon=True,
            name="DiscordRichPresence",
        ).start()

    def _ensure_rpc_connected(self):
        if self.connected and self.rpc is not None:
            return True
        if time.monotonic() < self._rpc_retry_after:
            return False
        if Presence is None:
            if not self._rpc_unavailable_logged:
                self.log(
                    "Discord RPC is unavailable because pypresence is not installed."
                )
                self._rpc_unavailable_logged = True
            return False

        rpc = None
        try:
            rpc = Presence(DISCORD_CLIENT_ID)
            rpc.connect()
            self.rpc = rpc
            self.connected = True
            self._rpc_retry_after = 0.0
            self.log("Connected to Discord RPC.")
            return True
        except Exception as error:
            if rpc is not None:
                try:
                    rpc.close()
                except Exception:
                    pass
            self.rpc = None
            self.connected = False
            self._rpc_retry_after = time.monotonic() + 10.0
            self.log(f"Discord RPC is not available: {error!r}")
            return False

    def _run_rpc_tasks(self):
        while True:
            with self._rpc_task_lock:
                task = self._rpc_pending_task
                self._rpc_pending_task = None
                if task is None:
                    self._rpc_worker_active = False
                    return

            action, payload = task
            try:
                if action == "update":
                    if not self._ensure_rpc_connected():
                        continue
                    values, frozen_payload = payload
                    self.rpc.update(**values)
                    self._last_payload = frozen_payload
                elif action == "clear":
                    if self.rpc is not None:
                        self.rpc.clear()
                    self._last_payload = None
                elif action == "disconnect":
                    if self.rpc is not None:
                        try:
                            self.rpc.clear()
                        except Exception:
                            pass
                    self._close_rpc()
            except Exception as error:
                self.log(f"Discord RPC {action} failed: {error!r}")
                self._rpc_retry_after = time.monotonic() + 10.0
                self._close_rpc()

    def _close_rpc(self):
        rpc = self.rpc
        self.rpc = None
        self.connected = False
        self._last_payload = None
        if rpc is not None:
            try:
                rpc.close()
            except Exception:
                pass

    def disconnect(self):
        self._schedule_rpc_task("disconnect")
        self._last_payload = None

    def clear_presence(self):
        self._schedule_rpc_task("clear")
        self._last_payload = None

    # ------------------------------------------------------------------
    # Player events
    # ------------------------------------------------------------------

    def on_media_status_changed(self, _status):
        self.update_presence()

    def on_playback_state_changed(self, _state):
        self.update_presence(force=True)

    def on_position_changed(self, _position_ms):
        # Track changes are detected here without spamming Discord every tick.
        if self.current_path != self._audio_path():
            self._lyric_bucket = None
            self._current_rpc_lyric = None
            self._last_lyric_rpc_update = 0.0
            self.update_presence(force=True)
        elif self.current_path:
            player = getattr(self.window, "media_player", None)
            if player is not None:
                image = self._cached_cover(self.current_path) or self._placeholder_image(
                    self._playback_status(player)
                )
                # Refresh for background artwork or newly loaded lyrics even
                # when lyric sharing is disabled.
                if image != self._last_cover:
                    self.update_presence()

        self._maybe_publish_lyric()

    def _audio_path(self):
        """Return only the currently playing local audio file.

        Discord RPC deliberately does not consume the UI/background artwork
        source. Booru URLs, in-memory artwork, and any non-file values are
        rejected here so cover resolution can only inspect the audio file's
        own metadata or its MusicBrainz album match.
        """
        if self.window is None:
            return None

        path = getattr(self.window, "current_audio_path", None)
        if not path:
            return None

        path = Path(path)
        if not path.is_file():
            self.log(
                f"Ignoring non-local RPC artwork/audio source: {path!s}"
            )
            return None

        return path

    # ------------------------------------------------------------------
    # Rate-limited lyric publishing
    # ------------------------------------------------------------------

    def _maybe_publish_lyric(self):
        if not self.settings.get("include_lyrics", True) or self.window is None:
            return

        line_index = getattr(self.window, "current_line", -1)
        lyrics = getattr(self.window, "lyrics", [])

        if not isinstance(line_index, int):
            return

        if line_index < 0 or line_index >= len(lyrics):
            return

        lines_per_update = self._normalize_lyric_lines_per_update(
            self.settings.get("lyric_lines_per_update", LYRIC_LINES_DEFAULT)
        )
        bucket = line_index // lines_per_update
        now = time.monotonic()

        # The same bucket never needs another update. A new bucket must also
        # wait for the cooldown, so rapid seeking cannot spam Discord.
        if bucket == self._lyric_bucket:
            return

        if now - self._last_lyric_rpc_update < LYRIC_UPDATE_COOLDOWN:
            return

        try:
            lyric = str(lyrics[line_index][1]).strip()
        except (IndexError, TypeError):
            return

        if not lyric:
            return

        self._lyric_bucket = bucket
        self._current_rpc_lyric = lyric
        self._last_lyric_rpc_update = now

        self.log(
            f"Discord lyric update: line {line_index + 1} "
            f"(bucket {bucket + 1})"
        )
        self.update_presence(force=True)

    # ------------------------------------------------------------------
    # Local metadata
    # ------------------------------------------------------------------

    def _file_key(self, path: Path):
        try:
            stat = path.stat()
            raw = (
                f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
            ).encode("utf-8")
        except OSError:
            raw = str(path).encode("utf-8")

        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _first_tag(tags, *keys):
        if not tags:
            return None

        for key in keys:
            value = tags.get(key)
            if value is None:
                continue

            if isinstance(value, (list, tuple)):
                value = value[0] if value else None

            if value is not None:
                value = str(value).strip()
                if value:
                    return value

        return None

    def _local_metadata(self, path: Path):
        key = self._file_key(path)
        if key in self._metadata_cache:
            return self._metadata_cache[key]

        metadata = {
            "title": path.stem,
            "artist": None,
            "album": None,
        }

        if MutagenFile is None:
            self._metadata_cache[key] = metadata
            return metadata

        try:
            audio = MutagenFile(path, easy=True)
            tags = getattr(audio, "tags", None) if audio else None

            title = self._first_tag(tags, "title")
            artist = self._first_tag(tags, "artist", "albumartist")
            album = self._first_tag(tags, "album")

            if title:
                metadata["title"] = title
            if artist:
                metadata["artist"] = artist
            if album:
                metadata["album"] = album

        except Exception as error:
            self.log(f"Could not read metadata from {path.name}: {error!r}")

        self._metadata_cache[key] = metadata
        return metadata

    # ------------------------------------------------------------------
    # Embedded cover extraction
    # ------------------------------------------------------------------

    def _extract_cover(self, path: Path):
        if MutagenFile is None:
            return None

        try:
            audio = MutagenFile(path)
        except Exception as error:
            self.log(f"Could not read album art from {path.name}: {error!r}")
            return None

        if audio is None:
            return None

        mime = "image/jpeg"
        data = None

        pictures = getattr(audio, "pictures", None)
        if pictures:
            picture = pictures[0]
            data = picture.data
            mime = getattr(picture, "mime", None) or mime

        if data is None:
            tags = getattr(audio, "tags", None)
            if tags:
                try:
                    apics = tags.getall("APIC")
                except Exception:
                    apics = []

                if apics:
                    apic = apics[0]
                    data = apic.data
                    mime = getattr(apic, "mime", None) or mime

        if data is None:
            tags = getattr(audio, "tags", None)
            if tags and "covr" in tags and tags["covr"]:
                cover = tags["covr"][0]
                data = bytes(cover)
                imageformat = getattr(cover, "imageformat", None)
                if imageformat is not None and "PNG" in str(imageformat).upper():
                    mime = "image/png"

        if not data:
            return None

        extension = ".png" if "png" in mime.lower() else ".jpg"
        return data, extension

    # ------------------------------------------------------------------
    # MusicBrainz + Cover Art Archive
    # ------------------------------------------------------------------

    def _musicbrainz_get(self, endpoint, params=None):
        elapsed = time.monotonic() - self._last_musicbrainz_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        try:
            response = requests.get(
                f"{MUSICBRAINZ_API}{endpoint}",
                params=params or {},
                headers={
                    "User-Agent": MUSICBRAINZ_USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=10,
            )
            self._last_musicbrainz_request = time.monotonic()
            response.raise_for_status()
            return response.json()
        except Exception as error:
            self._last_musicbrainz_request = time.monotonic()
            self.log(f"MusicBrainz lookup failed: {error!r}")
            return None

    def _find_musicbrainz_release(self, metadata):
        title = metadata.get("title")
        artist = metadata.get("artist")
        album = metadata.get("album")

        # Prefer the album/release when available because it gives us the
        # correct release-level cover art directly.
        if album and artist:
            query = f'release:"{album}" AND artist:"{artist}"'
            data = self._musicbrainz_get(
                "/release/",
                {"query": query, "fmt": "json", "limit": 5},
            )
            releases = (data or {}).get("releases", [])

            if releases:
                best = max(
                    releases,
                    key=lambda item: int(item.get("score", 0)),
                )
                if int(best.get("score", 0)) >= MUSICBRAINZ_MIN_SCORE:
                    return best.get("id"), best.get("title")

        # If there is no reliable album tag, search the recording and use one
        # of its linked releases.
        if title and artist:
            query = f'recording:"{title}" AND artist:"{artist}"'
            data = self._musicbrainz_get(
                "/recording/",
                {"query": query, "fmt": "json", "limit": 5},
            )
            recordings = (data or {}).get("recordings", [])

            if recordings:
                best = max(
                    recordings,
                    key=lambda item: int(item.get("score", 0)),
                )

                if int(best.get("score", 0)) >= MUSICBRAINZ_MIN_SCORE:
                    recording_id = best.get("id")
                    if recording_id:
                        recording = self._musicbrainz_get(
                            f"/recording/{recording_id}",
                            {"inc": "releases", "fmt": "json"},
                        )
                        releases = (recording or {}).get("releases", [])
                        if releases:
                            release = releases[0]
                            return release.get("id"), release.get("title")

        return None, None

    def _cover_art_url(self, archive_url):
        """Return the best usable image URL from a Cover Art Archive response."""
        response = requests.get(
            archive_url,
            timeout=10,
            headers={"User-Agent": MUSICBRAINZ_USER_AGENT},
        )
        response.raise_for_status()
        archive = response.json()

        images = archive.get("images") or []
        front = next((image for image in images if image.get("front")), None)
        image = front or (images[0] if images else None)

        if not image:
            return None

        thumbnails = image.get("thumbnails") or {}
        return (
            thumbnails.get("500")
            or thumbnails.get("250")
            or thumbnails.get("1200")
            or image.get("image")
        )

    def _musicbrainz_cover(self, path: Path, metadata):
        key = self._file_key(path)
        cache_key = f"mb:{key}"

        if cache_key in self._cover_cache:
            return self._cover_cache[cache_key]

        release_id, release_title = self._find_musicbrainz_release(metadata)
        if not release_id:
            self.log(
                f"MusicBrainz found no confident release match for {path.name}."
            )
            self._cover_cache[cache_key] = None
            return None

        # 1. Try release-level artwork.
        try:
            cover_url = self._cover_art_url(
                f"https://coverartarchive.org/release/{release_id}"
            )
            if cover_url:
                self._cover_cache[cache_key] = cover_url
                if release_title and not metadata.get("album"):
                    metadata["album"] = release_title
                self.log(
                    f"Using Cover Art Archive artwork for release {release_id}."
                )
                return cover_url
            self.log(
                f"No usable release artwork found for {release_id}; "
                f"trying its release group."
            )
        except requests.HTTPError as error:
            if getattr(error.response, "status_code", None) == 404:
                self.log(
                    f"No Cover Art Archive entry for release {release_id}; "
                    f"trying its release group."
                )
            else:
                self.log(
                    f"Release-level Cover Art Archive lookup failed: {error!r}"
                )
        except Exception as error:
            self.log(
                f"Release-level Cover Art Archive lookup failed: {error!r}"
            )

        # 2. A specific release can have no CAA entry while its release group
        # still has the album artwork. Ask MusicBrainz for that relationship.
        try:
            release_data = self._musicbrainz_get(
                f"/release/{release_id}",
                {"inc": "release-groups", "fmt": "json"},
            )
            release_group = (release_data or {}).get("release-group") or {}
            group_id = release_group.get("id")

            if not group_id:
                raise RuntimeError("MusicBrainz returned no release-group ID")

            cover_url = self._cover_art_url(
                f"https://coverartarchive.org/release-group/{group_id}"
            )
            if cover_url:
                self._cover_cache[cache_key] = cover_url
                if release_title and not metadata.get("album"):
                    metadata["album"] = release_title
                self.log(
                    f"Using Cover Art Archive artwork for release group "
                    f"{group_id}."
                )
                return cover_url

            self.log(
                f"No usable artwork found for release group {group_id}."
            )
        except requests.HTTPError as error:
            if getattr(error.response, "status_code", None) == 404:
                self.log(
                    "No Cover Art Archive entry exists for the release group."
                )
            else:
                self.log(
                    f"Release-group Cover Art Archive lookup failed: {error!r}"
                )
        except Exception as error:
            self.log(
                f"Release-group artwork lookup failed: {error!r}"
            )

        self._cover_cache[cache_key] = None
        self.log("No Cover Art Archive artwork found; using embedded art.")
        return None

    # ------------------------------------------------------------------
    # Litterbox fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _prepare_cover_upload(image_data):
        """Validate artwork and prepare a bounded copy without changing tags."""
        if len(image_data) > LITTERBOX_MAX_INPUT_BYTES:
            raise ValueError("Embedded cover exceeds the 20 MB input limit.")

        with Image.open(BytesIO(image_data), formats=("JPEG", "PNG")) as source:
            if source.width * source.height > LITTERBOX_MAX_INPUT_PIXELS:
                raise ValueError("Embedded cover exceeds the 25 megapixel input limit.")

            # Validate even small images before passing their original bytes on.
            source.load()
            extension = ".png" if source.format == "PNG" else ".jpg"
            if (max(source.size) <= LITTERBOX_MAX_COVER_EDGE
                    and len(image_data) <= LITTERBOX_MAX_UPLOAD_BYTES):
                return image_data, extension

            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (LITTERBOX_MAX_COVER_EDGE, LITTERBOX_MAX_COVER_EDGE),
                Image.Resampling.LANCZOS,
            )
            if extension == ".png":
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                if output.tell() <= LITTERBOX_MAX_UPLOAD_BYTES:
                    return output.getvalue(), ".png"

            # JPEG is a smaller fallback for PNGs that exceed the byte limit.
            # Composite transparency onto white instead of producing black edges.
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.new("RGB", image.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")

            for quality in (85, 70, 55):
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= LITTERBOX_MAX_UPLOAD_BYTES:
                    return output.getvalue(), ".jpg"

        raise ValueError("Prepared cover exceeds the 1 MB upload limit.")

    def _persistent_cover_cache_path(self):
        base = Path(os.getenv("APPDATA") or Path.home())
        return base / "LRCPlus" / "cover_cache.json"

    def _load_persistent_cover_cache(self):
        path = self._persistent_cover_cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("entries", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_persistent_cover_cache(self):
        path = self._persistent_cover_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"version": 1, "entries": self._persistent_cover_cache},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            self.log(f"Could not save persistent cover cache: {error!r}")

    def _persistent_litterbox_key(self, path: Path):
        try:
            stat = path.stat()
            return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return str(path.resolve())

    def _upload_embedded_cover(self, path: Path):
        key = f"litterbox:{self._file_key(path)}"
        if key in self._cover_cache:
            return self._cover_cache[key]

        persistent_key = self._persistent_litterbox_key(path)
        cached = self._persistent_cover_cache.get(persistent_key)
        now = time.time()
        if isinstance(cached, dict):
            url = cached.get("url")
            expires_at = float(cached.get("expires_at", 0))
            if isinstance(url, str) and url.startswith("https://") and expires_at - now > LITTERBOX_CACHE_SAFETY_SECONDS:
                self._cover_cache[key] = url
                self.log("Reusing cached Litterbox album cover URL.")
                return url

        extracted = self._extract_cover(path)
        if extracted is None:
            self.log(f"No embedded album cover found for {path.name}.")
            self._cover_cache[key] = None
            return None

        try:
            image_data, extension = self._prepare_cover_upload(extracted[0])
        except Exception as error:
            self.log(f"Skipping embedded album cover upload: {error!r}")
            self._cover_cache[key] = None
            return None

        filename = f"lrcplus-cover{extension}"
        mime = "image/png" if extension == ".png" else "image/jpeg"

        try:
            response = requests.post(
                LITTERBOX_UPLOAD_URL,
                data={"reqtype": "fileupload", "time": LITTERBOX_EXPIRY},
                files={
                    "fileToUpload": (
                        filename,
                        image_data,
                        mime,
                    )
                },
                headers={
                    "User-Agent": "LRCPlus/1.0 Discord-RPC-Plugin"
                },
                timeout=30,
            )

            if not response.ok:
                raise RuntimeError(
                    f"Litterbox returned HTTP {response.status_code}: "
                    f"{response.text.strip()!r}"
                )

            url = response.text.strip()
            if not url.startswith("https://"):
                raise RuntimeError(
                    f"Unexpected Litterbox response: {url!r}"
                )

            self._cover_cache[key] = url
            self._persistent_cover_cache[persistent_key] = {
                "url": url,
                "expires_at": time.time() + LITTERBOX_EXPIRY_SECONDS,
            }
            self._save_persistent_cover_cache()
            self.log(
                f"Uploaded embedded album cover to Litterbox "
                f"(expires in {LITTERBOX_EXPIRY})."
            )
            return url

        except Exception as error:
            self.log(f"Album cover upload failed: {error!r}")
            self._cover_cache[key] = None
            return None

    def _resolve_cover(self, path: Path, metadata):
        # Album covers only: MusicBrainz/Cover Art Archive first, then the
        # embedded cover inside this local audio file. Never use UI/Booru art.
        if self.settings.get("lookup_online_artwork", True):
            cover_url = self._musicbrainz_cover(path, metadata)
            if cover_url:
                return cover_url

        if self.settings.get("upload_embedded_artwork", True):
            return self._upload_embedded_cover(path)

        return None

    # ------------------------------------------------------------------
    # Background cover resolution
    # ------------------------------------------------------------------

    def _cover_cache_key(self, path):
        return self._file_key(path)

    def _start_cover_resolution(self, path, metadata):
        """Resolve network artwork without blocking the Qt/UI thread."""
        key = self._cover_cache_key(path)

        lookup_online = self.settings.get("lookup_online_artwork", True)
        upload_embedded = self.settings.get("upload_embedded_artwork", True)
        if not lookup_online and not upload_embedded:
            return

        if key in self._cover_jobs:
            return

        needs_online_lookup = (
            lookup_online and f"mb:{key}" not in self._cover_cache
        )
        needs_embedded_upload = (
            upload_embedded and f"litterbox:{key}" not in self._cover_cache
        )
        if not needs_online_lookup and not needs_embedded_upload:
            return

        # Copy the small metadata dict so the worker owns its input.
        metadata_copy = dict(metadata)
        path_copy = Path(path)

        self._cover_jobs.add(key)

        worker = threading.Thread(
            target=self._cover_worker,
            args=(path_copy, metadata_copy, key),
            daemon=True,
            name=f"DiscordCover-{path_copy.name}",
        )
        worker.start()

    def _cover_worker(self, path, metadata, key):
        try:
            self.log(f"Resolving album artwork in background for {path.name}.")
            self._resolve_cover(path, metadata)
            self.log(f"Album artwork resolution finished for {path.name}.")
        except Exception as error:
            self.log(
                f"Background album artwork resolution failed for "
                f"{path.name}: {error!r}"
            )
        finally:
            self._cover_jobs.discard(key)

    def _cached_cover(self, path):
        key = self._cover_cache_key(path)
        if self.settings.get("lookup_online_artwork", True):
            cover_url = self._cover_cache.get(f"mb:{key}")
            if cover_url:
                return cover_url
        if self.settings.get("upload_embedded_artwork", True):
            return self._cover_cache.get(f"litterbox:{key}")
        return None

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    def _placeholder_language_setting(self):
        language = self.settings.get("placeholder_language")
        if isinstance(language, str) and language in PLACEHOLDER_LANGUAGES:
            return language
        return DEFAULT_SETTINGS["placeholder_language"]

    def _placeholder_language(self):
        language = self._placeholder_language_setting()
        if language != "auto":
            return language

        lines = getattr(self.window, "lyric_lines", None)
        if not lines:
            lines = getattr(self.window, "lyrics", ())
        if not lines:
            return "jp"

        mode = get_reading_mode()
        key = (self.current_path, id(lines), len(lines), mode)
        if key != self._placeholder_detection_key:
            texts = []
            for line in lines:
                if isinstance(line, (tuple, list)):
                    text = line[1] if len(line) > 1 else ""
                elif isinstance(line, str):
                    text = line
                else:
                    text = getattr(line, "text", "")
                texts.append(str(text or ""))

            text = "\n".join(texts)
            detected = detect_lyrics_collection_language(texts)
            if detected == "japanese":
                language = "jp"
            elif _HANGUL.search(text):
                language = "kr"
            elif detected == "chinese":
                if mode == "furigana":
                    language = "jp"
                elif mode in {"jyutping", "yale"}:
                    language = "hk"
                elif mode == "pinyin":
                    language = "zh"
                else:
                    language = "hk" if _CANTONESE_MARKERS.intersection(text) else "zh"
            elif _VIETNAMESE.search(unicodedata.normalize("NFC", text)):
                language = "vi"
            else:
                language = "jp"

            self._placeholder_detection_key = key
            # Keep the collection alive so its id cannot be reused by a later
            # track while the cached detection still refers to it.
            self._placeholder_lyrics = lines
            self._detected_placeholder_language = language
        return self._detected_placeholder_language

    def _placeholder_image(self, status):
        state = {"Playing": "playing", "Paused": "paused"}.get(status, "idle")
        language = self._placeholder_language()
        if self.current_path != self._placeholder_easter_egg_track:
            self._placeholder_easter_egg_track = self.current_path
            self._classical_chinese_easter_egg = None
        if language in {"zh", "hk"} and self.current_path is not None:
            # Roll once when the track first has a Chinese/Cantonese fallback.
            # Lyric reloads, presence updates, and pause/resume keep the result.
            if self._classical_chinese_easter_egg is None:
                self._classical_chinese_easter_egg = (
                    random.random() < CLASSICAL_CHINESE_CHANCE
                )
            if self._classical_chinese_easter_egg:
                return PLACEHOLDER_URLS[state]["lzh"]
        return PLACEHOLDER_URLS[state][language]

    def _presence_text(self, metadata, status, *, settings=None, lyric=None):
        settings = self.settings if settings is None else settings
        if lyric is None:
            lyric = self._current_rpc_lyric
        lyric = lyric if settings.get("include_lyrics", True) else None
        artist = metadata.get("artist") or "Unknown artist"
        album = metadata.get("album")
        values = {
            "title": metadata.get("title") or "Unknown title",
            "artist": artist,
            "album": album or "Unknown album",
            "album_or_artist": album or artist,
            "lyric": lyric or "",
            "lyric_suffix": f" | {lyric}" if lyric else "",
            "status": status,
        }
        result = {}
        for key, field in zip(TEMPLATE_KEYS, ("details", "state", "large_text")):
            template = settings.get(key, DEFAULT_SETTINGS[key])
            if not isinstance(template, str):
                template = DEFAULT_SETTINGS[key]
            # Replace only named tokens; braces in lyrics or metadata remain
            # literal, and user text is never evaluated as a format expression.
            text = re.sub(
                r"\{([a-z_]+)\}",
                lambda match: str(values.get(match[1], match[0])),
                template,
            )
            text = " ".join(text.split())[:128]
            if text:
                # Discord requires at least two characters for details/state.
                # Invisible padding keeps single-character titles usable.
                if field in {"details", "state"} and len(text) == 1:
                    text += "\u200b"
                result[field] = text
        return result

    def create_settings_widget(self, parent):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QFormLayout,
            QGroupBox,
            QLabel,
            QLineEdit,
            QPushButton,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )

        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        display = QGroupBox("Activity display")
        display_form = QFormLayout(display)
        display_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        widget.activity_type = QComboBox()
        for value, label in ACTIVITY_TYPES.items():
            widget.activity_type.addItem(label, value)
        widget.activity_type.setCurrentIndex(
            max(0, widget.activity_type.findData(self.settings["activity_type"]))
        )
        display_form.addRow("Activity type:", widget.activity_type)
        for key, label in zip(
            TEMPLATE_KEYS, ("Details:", "State:", "Artwork hover text:")
        ):
            editor = QLineEdit(self.settings[key])
            editor.setToolTip("Leave empty to hide this text. Text is limited to 128 characters after substitution.")
            setattr(widget, key, editor)
            display_form.addRow(label, editor)
        help_text = QLabel(
            "Use {title}, {artist}, {album}, {lyric}, or {status} (Playing, Paused, Stopped). "
            "{album_or_artist} uses the artist when no album is available. "
            "{lyric_suffix} adds ' | lyric' only when lyrics are available. "
            "Unknown placeholders are shown literally."
        )
        help_text.setWordWrap(True)
        display_form.addRow(help_text)
        widget.show_timestamps = QCheckBox("Show playback progress")
        widget.show_timestamps.setChecked(self.settings["show_timestamps"])
        display_form.addRow(widget.show_timestamps)
        widget.show_when_paused = QCheckBox("Keep activity visible while paused or stopped")
        widget.show_when_paused.setChecked(self.settings["show_when_paused"])
        display_form.addRow(widget.show_when_paused)
        layout.addWidget(display)

        preview_box = QGroupBox("Text preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_note = QLabel("Uses the current track when available, otherwise sample text. Artwork hover text appears only with artwork.")
        preview_note.setWordWrap(True)
        preview_layout.addWidget(preview_note)
        widget.text_preview = QLabel()
        widget.text_preview.setTextFormat(Qt.TextFormat.PlainText)
        widget.text_preview.setWordWrap(True)
        preview_layout.addWidget(widget.text_preview)
        layout.addWidget(preview_box)

        widget.include_lyrics = QCheckBox(
            "Include current lyrics in Discord activity"
        )
        widget.include_lyrics.setChecked(
            self.settings.get("include_lyrics", True)
        )
        widget.include_lyrics.setToolTip(
            "Periodically shares the current lyric line in your Discord activity."
        )
        layout.addWidget(widget.include_lyrics)

        widget.lyric_lines_per_update = QSpinBox()
        widget.lyric_lines_per_update.setRange(
            LYRIC_LINES_MIN, LYRIC_LINES_MAX
        )
        widget.lyric_lines_per_update.setValue(
            self._normalize_lyric_lines_per_update(
                self.settings.get(
                    "lyric_lines_per_update", LYRIC_LINES_DEFAULT
                )
            )
        )
        lyric_interval_form = QFormLayout()
        lyric_interval_form.addRow(
            "Lines per status sync:", widget.lyric_lines_per_update
        )
        layout.addLayout(lyric_interval_form)

        widget.lyric_rate_warning = QLabel(
            "Values below 3 may trigger frequent updates and hit Discord RPC rate limits."
        )
        widget.lyric_rate_warning.setWordWrap(True)
        widget.lyric_rate_warning.setStyleSheet("color: #C06030;")
        layout.addWidget(widget.lyric_rate_warning)

        def update_lyric_controls():
            enabled = widget.include_lyrics.isChecked()
            widget.lyric_lines_per_update.setEnabled(enabled)
            widget.lyric_rate_warning.setVisible(
                enabled and widget.lyric_lines_per_update.value() < 3
            )

        widget.include_lyrics.toggled.connect(update_lyric_controls)
        widget.lyric_lines_per_update.valueChanged.connect(
            update_lyric_controls
        )
        update_lyric_controls()

        widget.lookup_online_artwork = QCheckBox(
            "Look up album artwork online"
        )
        widget.lookup_online_artwork.setChecked(
            self.settings.get("lookup_online_artwork", True)
        )
        widget.lookup_online_artwork.setToolTip(
            "Uses MusicBrainz and the Cover Art Archive to find album artwork."
        )
        layout.addWidget(widget.lookup_online_artwork)

        widget.upload_embedded_artwork = QCheckBox(
            "Upload embedded artwork to a temporary image host"
        )
        widget.upload_embedded_artwork.setChecked(
            self.settings.get("upload_embedded_artwork", True)
        )
        widget.upload_embedded_artwork.setToolTip(
            "Uploads artwork embedded in the audio file to Litterbox. The link expires after 12 hours."
        )
        layout.addWidget(widget.upload_embedded_artwork)

        widget.placeholder_language = QComboBox()
        for value, label in PLACEHOLDER_LANGUAGES.items():
            widget.placeholder_language.addItem(label, value)
        widget.placeholder_language.setCurrentIndex(
            widget.placeholder_language.findData(self._placeholder_language_setting())
        )
        placeholder_form = QFormLayout()
        placeholder_form.addRow("Fallback artwork language:", widget.placeholder_language)
        layout.addLayout(placeholder_form)

        def update_preview():
            settings = {
                key: getattr(widget, key).text() for key in TEMPLATE_KEYS
            }
            settings["include_lyrics"] = widget.include_lyrics.isChecked()
            path = self._audio_path()
            metadata = self._local_metadata(path) if path else {
                "title": "Song title", "artist": "Artist", "album": "Album"
            }
            status = "Playing"
            player = getattr(self.window, "media_player", None)
            if player is not None:
                status = self._playback_status(player)
            fields = self._presence_text(
                metadata, status, settings=settings,
                lyric=self._current_rpc_lyric if path else "Current lyric line",
            )
            widget.text_preview.setText(
                f"{widget.activity_type.currentText()} LRCPlus\n"
                f"Details: {fields.get('details', '(hidden)')}\n"
                f"State: {fields.get('state', '(hidden)')}\n"
                f"Artwork hover: {fields.get('large_text', '(hidden)')}"
            )

        for key in TEMPLATE_KEYS:
            getattr(widget, key).textChanged.connect(update_preview)
        widget.activity_type.currentIndexChanged.connect(update_preview)
        widget.include_lyrics.toggled.connect(update_preview)
        update_preview()

        def restore_defaults():
            widget.activity_type.setCurrentIndex(
                widget.activity_type.findData(DEFAULT_SETTINGS["activity_type"])
            )
            for key in TEMPLATE_KEYS:
                getattr(widget, key).setText(DEFAULT_SETTINGS[key])
            for key in (
                "show_timestamps", "show_when_paused", "include_lyrics",
                "lookup_online_artwork", "upload_embedded_artwork",
            ):
                getattr(widget, key).setChecked(DEFAULT_SETTINGS[key])
            widget.lyric_lines_per_update.setValue(LYRIC_LINES_DEFAULT)
            widget.placeholder_language.setCurrentIndex(
                widget.placeholder_language.findData(DEFAULT_SETTINGS["placeholder_language"])
            )

        widget.restore_defaults = QPushButton("Restore defaults")
        widget.restore_defaults.clicked.connect(restore_defaults)
        layout.addWidget(widget.restore_defaults)
        layout.addStretch()
        return widget

    def apply_settings_widget(self, widget):
        values = {
            "activity_type": widget.activity_type.currentData(),
            "show_timestamps": widget.show_timestamps.isChecked(),
            "show_when_paused": widget.show_when_paused.isChecked(),
            "include_lyrics": widget.include_lyrics.isChecked(),
            "lyric_lines_per_update": (
                self._normalize_lyric_lines_per_update(
                    widget.lyric_lines_per_update.value()
                )
            ),
            "lookup_online_artwork": widget.lookup_online_artwork.isChecked(),
            "upload_embedded_artwork": widget.upload_embedded_artwork.isChecked(),
            "placeholder_language": widget.placeholder_language.currentData(),
        }
        values.update({key: getattr(widget, key).text() for key in TEMPLATE_KEYS})
        lyrics_changed = values["include_lyrics"] != self.settings.get(
            "include_lyrics", True
        )
        lyric_interval_changed = (
            values["lyric_lines_per_update"]
            != self.settings.get(
                "lyric_lines_per_update", LYRIC_LINES_DEFAULT
            )
        )
        self.settings.update(values)

        for key, value in values.items():
            self.context.api.set_setting(key, value)

        if lyrics_changed:
            self._lyric_bucket = None
            self._current_rpc_lyric = None
            self._last_lyric_rpc_update = 0.0
        elif lyric_interval_changed:
            self._lyric_bucket = None
            self._maybe_publish_lyric()

        if self.window is not None:
            self.update_presence(force=True)

    @staticmethod
    def _playback_status(player):
        state = player.playbackState()
        if state == player.PlaybackState.PlayingState:
            return "Playing"
        if state == player.PlaybackState.PausedState:
            return "Paused"
        return "Stopped"

    def update_presence(self, force=False):
        path = self._audio_path()

        if path is None:
            self.current_path = None
            self._placeholder_easter_egg_track = None
            self._classical_chinese_easter_egg = None
            self.clear_presence()
            return

        player = getattr(self.window, "media_player", None)
        if player is None:
            return

        path_changed = self.current_path != path
        if path_changed:
            self._placeholder_easter_egg_track = None
            self._classical_chinese_easter_egg = None
            self._lyric_bucket = None
            self._current_rpc_lyric = None
            self._last_lyric_rpc_update = 0.0

        self.current_path = path

        status = self._playback_status(player)
        is_playing = status == "Playing"
        if not is_playing and not self.settings.get("show_when_paused", True):
            self.clear_presence()
            return

        metadata = self._local_metadata(path)
        payload = self._presence_text(metadata, status)
        hover_text = payload.pop("large_text", None)

        if ActivityType is not None:
            activity = self.settings.get("activity_type", "listening").upper()
            payload["activity_type"] = getattr(ActivityType, activity, ActivityType.LISTENING)

        # Never perform MusicBrainz, Cover Art Archive, or Litterbox network
        # requests on the UI thread. Publish the track immediately, then let a
        # background worker fill in the artwork on a later player tick.
        cover_url = self._cached_cover(path)

        if (path_changed or force) and not cover_url:
            self._start_cover_resolution(path, metadata)

        payload["large_image"] = cover_url or self._placeholder_image(status)
        self._last_cover = payload["large_image"]
        if hover_text:
            payload["large_text"] = hover_text

        # Discord advances the timestamp itself while playing. When paused we
        # deliberately omit it so Discord does not pretend playback continues.
        if is_playing and self.settings.get("show_timestamps", True):
            position_ms = max(0, int(player.position()))
            duration_ms = max(0, int(player.duration()))
            now = time.time()

            payload["start"] = int(now - position_ms / 1000.0)

            if duration_ms > 0:
                remaining_ms = max(0, duration_ms - position_ms)
                payload["end"] = int(now + remaining_ms / 1000.0)

        frozen_payload = tuple(
            sorted((key, repr(value)) for key, value in payload.items())
        )

        if not force and frozen_payload == self._last_payload:
            return

        self._schedule_rpc_task("update", (payload, frozen_payload))

    def log(self, message):
        if self.context is not None:
            self.context.log(message)
        else:
            print(f"[Plugin:discord_rpc] {message}")


PLUGIN_CLASS = DiscordRpcPlugin
