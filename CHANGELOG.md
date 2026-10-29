# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-29.4]

### Added
- **LRC > Reload Renderer** reloads lyrics without restarting audio playback.
- Discord Rich Presence now supports custom text templates with a live preview, activity type selection, playback progress and paused activity controls, and restoring default settings.

### Fixed
- Song association changes refresh lyrics immediately, including while paused. Saving and closing the editor preserves the selected association.
- Settings labels and checkboxes now blend into their section backgrounds in light and dark themes, removing unwanted rectangular backgrounds.