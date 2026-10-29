# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-29]

### Added
- Dependency Manager in Settings to install, repair, check, and remove optional plugin libraries.
- Setup notices for enabled plugins with missing or incompatible dependencies.
- Automatic private Python runtime setup for Windows builds, with libraries stored in the user's application data folder (Windows: %LOCALAPPDATA%\LyricsPlus)
- Dependency changes take effect after restarting LyricsPlus; failed installations preserve the previous working environment.

### Fixed
- Cantonese readings in packaged Windows builds now use the private Python runtime for their worker processes.
