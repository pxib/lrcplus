from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
import json
import os

import requests

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

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = "LyricsPlus/1.0 (Discord Rich Presence plugin)"
MUSICBRAINZ_MIN_SCORE = 80

# Lyrics are intentionally batched so Discord RPC is not updated for every
# single line. A new lyric is published when playback enters the next block.
LYRIC_LINES_PER_UPDATE = 10
LYRIC_UPDATE_COOLDOWN = 5.0


class DiscordRpcPlugin:
    name = "Discord Rich Presence"
    version = "1.2.0"
    description = (
        "Show the current track on Discord with metadata and album artwork."
    )

    def __init__(self):
        self.context = None
        self.window = None
        self.rpc = None
        self.connected = False
        self._rpc_task_lock = threading.Lock()
        self._rpc_pending_task = None
        self._rpc_worker_active = False
        self._rpc_retry_after = 0.0
        self._rpc_unavailable_logged = False

        self.current_path = None
        self._last_payload = None

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
        context.on("app_ready", self.on_app_ready)

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
        if self.window is None:
            return

        line_index = getattr(self.window, "current_line", -1)
        lyrics = getattr(self.window, "lyrics", [])

        if not isinstance(line_index, int):
            return

        if line_index < 0 or line_index >= len(lyrics):
            return

        bucket = line_index // LYRIC_LINES_PER_UPDATE
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

    def _persistent_cover_cache_path(self):
        base = Path(os.getenv("APPDATA") or Path.home())
        return base / "LyricsPlus" / "cover_cache.json"

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

        image_data, extension = extracted
        filename = f"lyricsplus-cover{extension}"
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
                    "User-Agent": "LyricsPlus/1.0 Discord-RPC-Plugin"
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
            return None

    def _resolve_cover(self, path: Path, metadata):
        # Album covers only: MusicBrainz/Cover Art Archive first, then the
        # embedded cover inside this local audio file. Never use UI/Booru art.
        cover_url = self._musicbrainz_cover(path, metadata)
        if cover_url:
            return cover_url

        return self._upload_embedded_cover(path)

    # ------------------------------------------------------------------
    # Background cover resolution
    # ------------------------------------------------------------------

    def _cover_cache_key(self, path):
        return self._file_key(path)

    def _start_cover_resolution(self, path, metadata):
        """Resolve network artwork without blocking the Qt/UI thread."""
        key = self._cover_cache_key(path)

        if key in self._cover_jobs:
            return

        if (
            f"mb:{key}" in self._cover_cache
            or f"litterbox:{key}" in self._cover_cache
        ):
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
        return (
            self._cover_cache.get(f"mb:{key}")
            or self._cover_cache.get(f"litterbox:{key}")
        )

    # ------------------------------------------------------------------
    # Presence
    # ------------------------------------------------------------------

    def update_presence(self, force=False):
        path = self._audio_path()

        if path is None:
            self.current_path = None
            self.clear_presence()
            return

        player = getattr(self.window, "media_player", None)
        if player is None:
            return

        path_changed = self.current_path != path
        if path_changed:
            self._lyric_bucket = None
            self._current_rpc_lyric = None
            self._last_lyric_rpc_update = 0.0

        self.current_path = path

        metadata = self._local_metadata(path)
        title = metadata["title"]
        artist = metadata.get("artist") or "Unknown artist"
        album = metadata.get("album")

        is_playing = (
            player.playbackState()
            == player.PlaybackState.PlayingState
        )

        # Discord layout:
        #   details      -> Title
        #   state        -> Album | Current lyric
        #   large_text   -> Artist (shown when hovering the artwork)
        #
        # The artist remains part of the track metadata while the visible
        # state line is reserved for the album and periodically updated lyric.
        if self._current_rpc_lyric:
            context_text = album or "Unknown album"
            presence_state = f"{context_text} | {self._current_rpc_lyric}"
        else:
            presence_state = album or artist

        payload = {
            "details": title,
            "state": presence_state,
        }

        if ActivityType is not None:
            payload["activity_type"] = ActivityType.LISTENING

        # Never perform MusicBrainz, Cover Art Archive, or Litterbox network
        # requests on the UI thread. Publish the track immediately, then let a
        # background worker fill in the artwork on a later player tick.
        cover_url = self._cached_cover(path)

        if path_changed and not cover_url:
            self._start_cover_resolution(path, metadata)

        if cover_url:
            payload["large_image"] = cover_url
            payload["large_text"] = artist

        # Discord advances the timestamp itself while playing. When paused we
        # deliberately omit it so Discord does not pretend playback continues.
        if is_playing:
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
