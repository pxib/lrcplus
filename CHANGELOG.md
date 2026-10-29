# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-29.2]

### Added
- Inline ASS/SSA subtitle warnings, including qualified warnings for VTT captions with ASS-style effect markers.
- Subtitle language/source selection in yt-dlp Audio Import, original VTT/SRT downloads, optional LRC conversion, and subtitle-only imports for existing audio.
- Plugin API lyric attachments that refresh the current track without restarting playback or replacing authored lyric files.
- Optional yt-dlp Audio Import plugin with URL previews, M4A/MP3 downloads, metadata/artwork, cancellation, retries, and remembered source URLs.
- Plugin API v4 local audio import method for library/queue integration, and optional dependency extras support for matching yt-dlp components.
- Queue manager with drag-and-drop and multi-track reordering, Play next, Add to queue, and library context-menu actions.
- Named saved playlists with load, append, update, rename, and delete controls, plus local M3U/M3U8 import and export.

### Fixed
- Styled subtitle conversion now collapses repeated outline/color redraws, joins vertical CJK character columns, and handles consecutive cues without blank separators.
- Oversized lyric imports are rejected before creating scrolling widgets, preventing malformed subtitle conversions from freezing playback.
- Repeated queue entries retain their own playback position, including when restoring a session with missing files.
