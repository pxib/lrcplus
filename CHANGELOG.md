# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-10-28.2]

### Changed
- Bundled Noto Sans JP for library text, reducing Windows memory use when displaying Vietnamese and mixed Japanese/Vietnamese text.

### Fixed
- Settings and plugin settings dialogs now release their widgets on close, preventing memory growth when opened repeatedly.