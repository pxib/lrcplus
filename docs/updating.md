# LRCPlus Updates

## User behavior

LRCPlus checks GitHub Releases eight seconds after startup, then no more than once every 24 hours. Automatic checks can be disabled in **Settings > General**. The update channel can be set to **Stable** or **Prerelease**. Manual checks remain available when automatic checks are disabled. Failed automatic checks are logged to the console and do not interrupt playback; manual checks show an actionable message.

An available release is shown in a non-modal dialog with plain-text release notes. Users can update, dismiss the notice, skip that version, or open the GitHub release. Installation always requires confirmation. The download runs in a worker, can be canceled or hidden, and does not change the installed app until the standalone updater has revalidated the package.

Settings are stored in the existing per-user LRCPlus settings file. Managed tools and other app data remain under the per-user data directory. The updater keeps a complete backup of the previous installation and retains it after startup health is confirmed. A per-user Windows `RunOnce` recovery action is registered before the live install is moved; it is removed after a successful startup acknowledgment or rollback.

## Release asset contract

The updater accepts only a release asset matching this exact name:

```text
LRCPlus-v<APP_VERSION>-Windows-x64.zip
LRCPlus-v<APP_VERSION>-Windows-arm64.zip
```

The ZIP must contain a single `LRCPlus/` root with `LRCPlus.exe`, `LRCPlusUpdater.exe`, and `lrcplus-update.json`. The JSON manifest must contain the exact `APP_VERSION` and target architecture. The build process also emits a `.zip.sha256` sidecar for publisher-side checks.

The updater reads the asset's `digest` field from GitHub's official Releases API and requires a SHA-256 digest. Missing digests, unexpected names, unsupported architectures, malformed manifests, or unsafe ZIP entries fail closed. Source archives are never selected. The ZIP is downloaded over HTTPS, streamed to a `.part` file, checked against the API asset size and SHA-256, then published to the transaction directory.

This digest detects corruption and ensures the download matches the digest returned by GitHub over HTTPS. It is **not a publisher signature**: the digest and ZIP come from the same GitHub release service. No independently trusted signing key or signed manifest is configured in this repository yet.

## Test safely

Run the local fake-update scenarios from the repository root:

```powershell
.\Scripts\python.exe tools\test_update.py
```

The script creates a synthetic release package, verifies and installs it into a temporary `LRCPlus/` directory, checks settings/plugin/unknown-file preservation and backup retention, then simulates a failed launch and checks rollback. It does not contact GitHub, change `APP_VERSION`, launch an executable, write to the real install directory, or leave temporary files behind. This exercises the download verifier with mocked bytes and the real staging/installer code; it does not test the Qt dialogs, live GitHub networking, Windows process waiting, or RunOnce recovery.

## Build and publish

Build releases on the Windows architecture being published, using the project's supported Python environment and PyInstaller:

```powershell
python -m pip install pyinstaller
python tools/build_release.py
```

The script builds the one-file standard-library updater first, generates the version/architecture manifest, builds the existing one-folder application, and creates the ZIP and checksum under `dist/release/`. Review and test both assets, then upload them to a GitHub Release in `pxib/lrcplus` whose tag is `APP_VERSION` (optionally prefixed with `v`). Mark ordinary releases stable and experimental releases as prereleases. After uploading, verify that GitHub's release API reports a non-empty `sha256:...` `digest` for the ZIP asset; otherwise the client will reject the release.

The release ZIP must preserve the top-level `LRCPlus/` directory. Do not upload source archives as application assets. Existing installations without the updater must receive an updater-enabled build manually once; subsequent compatible releases can update in-app.

## Version tags

PEP 440 versions are accepted, including `v` prefixes. LRCPlus's `YYYY-MM-DD.N` form is normalized to the equivalent numeric PEP 440 release `YYYY.MM.DD.N`; prerelease suffixes such as `rc1`, `a1`, and `b1` retain their ordering. The exact `-exp` suffix is treated as a `.dev0` prerelease for comparison, so tag `v2026-11-02.5-exp` is newer than `.3` but older than final `.5`. Since release assets may omit `-exp`, the updater matches the archive and manifest using package version `2026-11-02.5` while retaining the full tag for release ordering and display. Invalid calendar dates and ambiguous identifiers are rejected. Stable checks use GitHub's latest-stable endpoint. Prerelease checks scan published releases and select the greatest compatible version, including stable releases. Equal or older versions are never offered.

## Preservation and recovery

The updater replaces the complete application folder only after the running executable exits. It independently verifies the ZIP and manifest, extracts with path traversal, symlink, special-file, duplicate-name, case-collision, entry-count, and expanded-size checks, and stages the package before swapping directories. It preserves legacy `lrc_player_settings.json`, unknown top-level files/directories not included in the new package, and plugin folders whose IDs are absent from the incoming package. A plugin with the same ID as a bundled plugin is treated as application-owned because the current plugin system does not distinguish user and bundled copies; the incoming copy wins. This limitation should be resolved by separating user-installed plugins before promising preservation for same-ID overrides.

A transaction journal records each phase. On ordinary failures the helper restores the backup and verifies that the old executable exists. If Windows restarts during the directory swap, the registered per-user recovery helper runs at the next sign-in and restores the backup unless the new app has written its startup-health marker. The health marker confirms that the new main window initialized; it is not an extended playback or plugin health test. The backup is intentionally retained after success; automatic cleanup is not enabled.

The existing install directory must be named `LRCPlus`. If the app is installed under a renamed directory, the helper rejects it rather than guessing. The update is Windows-only and currently supports x64 and ARM64 packages.
