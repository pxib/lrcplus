# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-30.2]

### Added
- Add MusicBrainz as a provider for yt-dlp album covers. (yt-dlp)
- Added download speed in Mbps, displayed alongside the remaining time. (yt-dlp)
- Added album information, full release dates in the user's regional date format, and source audio codec, bitrate, and sample rate to track previews. Upload dates are labeled separately when release dates are unavailable; missing metadata stays hidden. (yt-dlp)
- Added an estimated download size before the Download button, updated for the selected audio format and hidden when unknown or reusing an existing file. (yt-dlp)
- Added playing, paused, and idle album placeholders in Japanese, Korean, Chinese, and Cantonese when album artwork is unavailable. Language follows the lyrics, defaults to Japanese, and can be selected manually. (Discord Rich Presence)

### Changed
- Restore last volume percentage when unmuting.
- Hid empty hints, so those areas only appear when they have content.
- Limited embedded artwork uploads to 512 pixels on the longest edge and 1 MB, preserving aspect ratio and the original artwork. Invalid images and inputs over 20 MB or 25 megapixels are skipped. (Discord Rich Presence)

### Fixed
- Reduced renderer lag on long lyrics with many pinyin or furigana annotations by caching text layout and karaoke timing lookups and skipping offscreen text drawing.
- Fixed MusicBrainz cover matching when YouTube credits include a composer alongside the primary artist confirmed by the uploader. (yt-dlp)
- Fixed placeholder images by using direct CDN image URLs instead of share pages. (Discord Rich Presence)
- Prevented repeated failed artwork uploads within a session and refreshed placeholders when lyrics or real album artwork become available. (Discord Rich Presence)
- Fixed converter mishandled YouTube’s rolling captions, causing each line to appear three times.
