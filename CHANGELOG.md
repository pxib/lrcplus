# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-30.5]

### Added
- Added lyric formatting: `**bold**`, `*italic*`, `~~strikethrough~~`, and `<u>underline</u>`.
- Added single-level blockquotes using `> `, with an indent and a vertical bar spanning all rows of a lyric.
- Added formatting buttons and keyboard shortcuts, plus an **Insert Newline** control above the lyric field.

### Changed
- Moved **Shift Timing** and **Validate Timing** next to the lyric offset controls.

### Fixed
- Fixed `{\n}` rendering and multiline row spacing, preserving furigana and karaoke timing.
- Fixed batched reading generation losing text after a visual newline.
- Made underline and strikethrough continuous across spaces and furigana padding.
- Fixed LRCX furigana prefetch skipping lines with content segments, which caused MeCab to run as playback reached each line.
- Corrected logs that labeled ordinary LRCX content and reading tokens as karaoke.
- Files declaring `[format:LRCX]` now load correctly even with a `.lrc` extension.