# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-29.3]

### Fixed
- Scrolling lyrics now create widgets only around the viewport, keeping long documents responsive during playback, scrolling, and seeking. Recreated rows retain readings, karaoke timing, placeholders, and display settings.
- Local lyric scans and LRCLIB searches now run in the background, letting playback and the interface stay responsive. Late results can’t overwrite lyrics after a track change.