import hashlib
import io
import json
import runpy
from datetime import datetime, timezone
from pathlib import Path
import stat
import threading
import urllib.error
import uuid
import zipfile

import pytest
from packaging.version import Version

from core import updater
from core import settings
import updater_helper


_build_release = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "tools" / "build_release.py")
)
_set_app_version = _build_release["set_app_version"]


def _release(tag="v2026-11-02.0", *, prerelease=False, assets=None):
    asset_name = f"LRCPlus-{tag}-Windows-x64.zip"
    return {
        "tag_name": tag,
        "name": f"LRCPlus {tag}",
        "published_at": "2026-11-02T12:00:00Z",
        "body": "Release notes",
        "prerelease": prerelease,
        "draft": False,
        "html_url": f"https://github.com/pxib/lrcplus/releases/tag/{tag}",
        "assets": assets if assets is not None else [{
            "name": asset_name,
            "browser_download_url": f"https://github.com/pxib/lrcplus/releases/download/{tag}/{asset_name}",
            "size": 1024,
            "digest": "sha256:" + "a" * 64,
        }],
    }


def test_date_versions_are_normalized_and_ordered():
    assert updater.parse_version("2026-11-02.0") > updater.parse_version("2026-11-01.4")
    assert updater.parse_version("v1.10.0") > updater.parse_version("v1.9.0")
    assert updater.parse_version("1.2.0") > updater.parse_version("1.2.0rc1")


@pytest.mark.parametrize("value", ["", "development", "2026-13-42.1", "vnot-a-version"])
def test_malformed_versions_are_rejected(value):
    with pytest.raises(updater.UpdateError):
        updater.parse_version(value)


def test_automatic_check_interval_and_disabled_setting():
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert updater.should_check_automatically({}, now)
    assert not updater.should_check_automatically({"automatic_check": False}, now)
    assert not updater.should_check_automatically({
        "last_attempt": "2026-11-02T00:01:00+00:00",
    }, now)
    assert updater.should_check_automatically({
        "last_attempt": "2026-11-01T11:59:59Z",
    }, now)
    assert updater.should_check_automatically({"last_attempt": "invalid"}, now)


def test_stable_lookup_uses_latest_endpoint_and_validates_asset(monkeypatch):
    release_data = _release()
    calls = []

    def fake_json(url):
        calls.append(url)
        return release_data

    monkeypatch.setattr(updater, "_github_json", fake_json)
    result = updater.get_latest_release("stable", "x64")

    assert calls == [f"{updater.API_ROOT}/releases/latest"]
    assert result.version == Version("2026.11.2.0")
    assert result.asset.sha256 == "a" * 64


def test_github_api_request_uses_expected_headers_and_timeout(monkeypatch):
    response = _DownloadResponse(
        b'{"ok": true}', url=f"{updater.API_ROOT}/releases/latest"
    )

    def fake_open(request, timeout):
        assert request.full_url == f"{updater.API_ROOT}/releases/latest"
        assert request.get_header("Accept") == "application/vnd.github+json"
        assert request.get_header("X-github-api-version") == "2022-11-28"
        assert timeout == 20
        return response

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_open)

    assert updater._github_json(f"{updater.API_ROOT}/releases/latest") == {"ok": True}


@pytest.mark.parametrize(("status", "message"), [
    (403, "rate-limited"),
    (429, "rate-limited"),
    (404, "No stable"),
    (503, "temporarily unavailable"),
])
def test_github_api_http_errors_are_actionable(monkeypatch, status, message):
    def fail_open(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://api.github.com", status, "failed", {}, None)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fail_open)

    with pytest.raises(updater.UpdateError, match=message):
        updater._github_json(f"{updater.API_ROOT}/releases/latest")


def test_github_api_handles_timeout_and_invalid_json(monkeypatch):
    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError(TimeoutError())),
    )
    with pytest.raises(updater.UpdateError, match="Could not connect"):
        updater._github_json(f"{updater.API_ROOT}/releases/latest")

    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(
            b"not json", url=f"{updater.API_ROOT}/releases/latest"
        ),
    )
    with pytest.raises(updater.UpdateError, match="invalid release information"):
        updater._github_json(f"{updater.API_ROOT}/releases/latest")


def test_github_api_rejects_untrusted_redirect(monkeypatch):
    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(
            b"{}", url="https://api.github.com.evil.example/releases"
        ),
    )

    with pytest.raises(updater.UpdateError, match="untrusted location"):
        updater._github_json(f"{updater.API_ROOT}/releases/latest")


def test_prerelease_lookup_selects_newest_version_including_stable(monkeypatch):
    releases = [
        _release("v2026-11-01.4"),
        _release("v2026-11-02.0rc1", prerelease=True),
        _release("v2026-11-01.5", prerelease=True),
    ]
    monkeypatch.setattr(updater, "_github_json", lambda _url: releases)

    result = updater.get_latest_release("prerelease", "x64")

    assert result.tag == "v2026-11-02.0rc1"
    assert result.prerelease is True


def test_exp_tag_compares_as_prerelease_and_matches_unsuffixed_asset(monkeypatch, tmp_path):
    release_data = _release("v2026-11-02.5")
    release_data["tag_name"] = "v2026-11-02.5-exp"
    release_data["prerelease"] = True
    release_data["assets"][0]["browser_download_url"] = (
        "https://github.com/pxib/lrcplus/releases/download/v2026-11-02.5-exp/"
        "LRCPlus-v2026-11-02.5-Windows-x64.zip"
    )
    monkeypatch.setattr(updater, "_github_json", lambda _url: [release_data])

    release = updater.find_update("2026-11-02.3", "prerelease", "x64")

    assert release is not None
    assert release.version == updater.parse_version("2026-11-02.5-exp")
    assert release.version < updater.parse_version("2026-11-02.5")
    assert release.package_version == "2026-11-02.5"
    request = updater.build_handoff_request(
        tmp_path / "LRCPlus", release, "x64", 1234,
        current_version="2026-11-02.3",
    )
    assert request["version"] == "2026-11-02.5"
    assert request["asset_name"] == "LRCPlus-v2026-11-02.5-Windows-x64.zip"


def test_update_lookup_never_offers_same_or_older_version(monkeypatch):
    monkeypatch.setattr(updater, "_github_json", lambda _url: _release("v2026-11-01.4"))

    assert updater.find_update("2026-11-01.4", "stable", "x64") is None
    assert updater.find_update("2026-11-02.0", "stable", "x64") is None


@pytest.mark.parametrize("bad_asset", [
    {"name": "LRCPlus-v2026-11-02.0-Windows-x64.zip", "size": 1,
     "digest": "sha256:" + "a" * 64,
     "browser_download_url": "http://github.com/pxib/lrcplus/releases/download/tag/file.zip"},
    {"name": "LRCPlus-v2026-11-02.0-Windows-x64.zip", "size": 1,
     "digest": None,
     "browser_download_url": "https://github.com/pxib/lrcplus/releases/download/tag/file.zip"},
    {"name": "LRCPlus-v2026-11-02.0-Windows-x64.zip", "size": 0,
     "digest": "sha256:" + "a" * 64,
     "browser_download_url": "https://github.com/pxib/lrcplus/releases/download/tag/file.zip"},
    {"name": "Source code (zip)", "size": 1,
     "digest": "sha256:" + "a" * 64,
     "browser_download_url": "https://github.com/pxib/lrcplus/archive/refs/tags/tag.zip"},
])
def test_release_rejects_missing_or_unsafe_assets(bad_asset):
    with pytest.raises(updater.UpdateError):
        updater._to_release(_release(assets=[bad_asset]), "x64")


def test_release_asset_must_match_tag_and_architecture():
    release_data = _release("v2026-11-02.0")
    release_data["assets"][0]["name"] = "LRCPlus-v2026-11-01.4-Windows-x64.zip"

    with pytest.raises(updater.UpdateError):
        updater._to_release(release_data, "x64")


def test_updater_fallback_prefers_staged_then_uses_installed_helper(tmp_path):
    staged = tmp_path / "staged" / "LRCPlus"
    installed = tmp_path / "installed" / "LRCPlus"
    staged.mkdir(parents=True)
    installed.mkdir(parents=True)
    installed_helper = installed / "LRCPlusUpdater.exe"
    installed_helper.write_bytes(b"installed helper")

    assert updater.find_updater_executable(staged, installed) == installed_helper

    staged_helper = staged / "LRCPlusUpdater.exe"
    staged_helper.write_bytes(b"staged helper")
    assert updater.find_updater_executable(staged, installed) == staged_helper


def test_update_settings_persist_without_replacing_other_preferences(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    settings.save_all_settings({"playback": {"volume": 37}})

    settings.save_update_settings({
        "automatic_check": False,
        "channel": "prerelease",
        "skipped_version": "v2026-11-02.0rc1",
        "last_checked": "2026-11-02T12:00:00+00:00",
        "last_attempt": "2026-11-02T12:00:00+00:00",
    })

    saved = settings.load_all_settings()
    assert saved["playback"] == {"volume": 37}
    assert settings.get_update_settings(saved)["channel"] == "prerelease"
    assert settings.get_update_settings(saved)["automatic_check"] is False


def test_update_settings_normalize_invalid_channel():
    assert settings.get_update_settings({"updates": {"channel": "nightly"}})["channel"] == "stable"


def test_release_builder_changes_only_the_canonical_version_literal(tmp_path):
    app_info = tmp_path / "app_info.py"
    original = (
        '"""App metadata."""\r\n\r\n'
        'APP_NAME = "LRCPlus"\r\n'
        'APP_VERSION = "2026-11-01.4"\r\n'
        'PROJECT_URL = "https://github.com/pxib/lrcplus"\r\n'
    )
    app_info.write_bytes(original.encode("utf-8"))

    previous, updated = _set_app_version(app_info, "v2026-11-02.0")

    assert (previous, updated) == ("2026-11-01.4", "2026-11-02.0")
    assert app_info.read_bytes().decode("utf-8") == original.replace(
        'APP_VERSION = "2026-11-01.4"', 'APP_VERSION = "2026-11-02.0"'
    )


@pytest.mark.parametrize("value", ["not-a-version", "2026-11-01.4", "2026-11-01.3"])
def test_release_builder_rejects_invalid_same_or_older_version(tmp_path, value):
    app_info = tmp_path / "app_info.py"
    source = 'APP_VERSION = "2026-11-01.4"\n'
    app_info.write_text(source, encoding="utf-8")

    with pytest.raises(ValueError):
        _set_app_version(app_info, value)

    assert app_info.read_text(encoding="utf-8") == source


def test_release_builder_uses_requested_version_in_manifest(tmp_path, monkeypatch):
    app_info = tmp_path / "core" / "app_info.py"
    app_info.parent.mkdir()
    app_info.write_text('APP_VERSION = "2026-11-01.4"\n', encoding="utf-8")
    builder_globals = _build_release["main"].__globals__
    monkeypatch.setattr(builder_globals["sys"], "platform", "win32")
    monkeypatch.setattr(builder_globals["subprocess"], "run", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(builder_globals, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(builder_globals, "windows_architecture", lambda: "x64")

    with pytest.raises(RuntimeError, match="packaged application is missing"):
        builder_globals["main"](["--version", "2026-11-02.0"])

    manifest = json.loads((tmp_path / "build" / "lrcplus-update.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2026-11-02.0"
    assert app_info.read_text(encoding="utf-8") == 'APP_VERSION = "2026-11-02.0"\n'


class _DownloadResponse:
    def __init__(self, content, *, url="https://release-assets.githubusercontent.com/file", length=None):
        self.content = io.BytesIO(content)
        self.headers = {"Content-Length": str(length)} if length is not None else {}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.content.read(size)


def test_download_streams_and_verifies_before_publishing(tmp_path, monkeypatch):
    content = b"verified release data"
    asset = updater.ReleaseAsset(
        "release.zip", "https://github.com/pxib/lrcplus/releases/download/tag/release.zip",
        len(content), hashlib.sha256(content).hexdigest(),
    )
    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(content, length=len(content)),
    )
    progress = []

    destination = updater.download_asset(
        asset, tmp_path / "release.zip", progress=lambda done, total: progress.append((done, total))
    )

    assert destination.read_bytes() == content
    assert progress[-1] == (len(content), len(content))
    assert not (tmp_path / "release.zip.part").exists()


def test_download_failure_removes_partial_file(tmp_path, monkeypatch):
    content = b"tampered"
    asset = updater.ReleaseAsset("release.zip", "https://github.com/file.zip", len(content), "0" * 64)
    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(content, length=len(content)),
    )

    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download_asset(asset, tmp_path / "release.zip")
    assert not (tmp_path / "release.zip").exists()
    assert not (tmp_path / "release.zip.part").exists()


def _zip_bytes(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return data.getvalue()


def _valid_package_files():
    manifest = json.dumps({"version": "2026-11-02.0", "architecture": "x64"})
    return [
        ("LRCPlus/LRCPlus.exe", b"application"),
        ("LRCPlus/LRCPlusUpdater.exe", b"updater"),
        ("LRCPlus/lrcplus-update.json", manifest),
        ("LRCPlus/_internal/runtime.dll", b"runtime"),
        ("LRCPlus/plugins/core/lyrics_display/plugin.py", b"new core plugin"),
    ]


def test_extract_valid_update_package(tmp_path):
    archive_path = tmp_path / "valid.zip"
    archive_path.write_bytes(_zip_bytes(_valid_package_files()))

    extracted = updater.extract_update_archive(
        archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
    )

    assert (extracted / "LRCPlus.exe").read_bytes() == b"application"
    assert (extracted / "_internal" / "runtime.dll").exists()


def test_extract_accepts_exact_versioned_legacy_release_root(tmp_path):
    root = "LRCPlus-v2026-11-02.0-Windows-x64"
    archive_path = tmp_path / "legacy-layout.zip"
    archive_path.write_bytes(_zip_bytes([
        (f"{root}/LRCPlus.exe", b"application"),
        (f"{root}/_internal/runtime.dll", b"runtime"),
    ]))

    extracted = updater.extract_update_archive(
        archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
    )

    assert (extracted / "LRCPlus.exe").read_bytes() == b"application"
    assert not (extracted / "LRCPlusUpdater.exe").exists()
    assert not (extracted / "lrcplus-update.json").exists()


def test_extract_rejects_unexpected_versioned_root(tmp_path):
    archive_path = tmp_path / "wrong-root.zip"
    archive_path.write_bytes(_zip_bytes([
        ("LRCPlus-v2026-11-02.1-Windows-x64/LRCPlus.exe", b"application"),
    ]))

    with pytest.raises(updater.UpdateError, match="unsafe path"):
        updater.extract_update_archive(
            archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
        )


@pytest.mark.parametrize("extra", [
    [("LRCPlus/../../outside.txt", b"escape")],
    [("LRCPlus/lrcplus.exe", b"case collision")],
])
def test_extract_rejects_traversal_and_case_collisions(tmp_path, extra):
    archive_path = tmp_path / "invalid.zip"
    archive_path.write_bytes(_zip_bytes(_valid_package_files() + extra))

    with pytest.raises(updater.UpdateError):
        updater.extract_update_archive(
            archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
        )
    assert not (tmp_path / "outside.txt").exists()


def test_extract_rejects_wrong_manifest_version(tmp_path):
    files = _valid_package_files()
    files[2] = (files[2][0], json.dumps({"version": "2026-11-01.4", "architecture": "x64"}))
    archive_path = tmp_path / "wrong-version.zip"
    archive_path.write_bytes(_zip_bytes(files))

    with pytest.raises(updater.UpdateError, match="version or architecture"):
        updater.extract_update_archive(
            archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
        )
    assert not (tmp_path / "stage" / "LRCPlus").exists()


def test_extract_rejects_symlinks_and_absolute_paths(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in _valid_package_files():
            archive.writestr(name, content)
        link = zipfile.ZipInfo("LRCPlus/redirect")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
        archive.writestr("/absolute.txt", "outside")
    archive_path = tmp_path / "unsafe.zip"
    archive_path.write_bytes(data.getvalue())

    with pytest.raises(updater.UpdateError):
        updater.extract_update_archive(
            archive_path, tmp_path / "stage", expected_version="2026-11-02.0", architecture="x64"
        )
    assert not (tmp_path / "outside").exists()


def test_extract_rejects_missing_executable_corrupt_zip_and_compression_bomb(tmp_path):
    missing_files = [item for item in _valid_package_files() if item[0] != "LRCPlus/LRCPlus.exe"]
    missing_archive = tmp_path / "missing.zip"
    missing_archive.write_bytes(_zip_bytes(missing_files))
    corrupt_archive = tmp_path / "corrupt.zip"
    corrupt_archive.write_bytes(b"not a zip archive")
    bomb_files = _valid_package_files() + [("LRCPlus/expanded.bin", b"0" * 2_000_000)]
    bomb_archive = tmp_path / "bomb.zip"
    bomb_archive.write_bytes(_zip_bytes(bomb_files))

    for archive_path in (missing_archive, corrupt_archive, bomb_archive):
        with pytest.raises(updater.UpdateError):
            updater.extract_update_archive(
                archive_path, tmp_path / f"stage-{archive_path.stem}",
                expected_version="2026-11-02.0", architecture="x64",
            )


def test_download_cancellation_removes_partial_file(tmp_path, monkeypatch):
    content = b"will not be downloaded"
    asset = updater.ReleaseAsset("release.zip", "https://github.com/file.zip", len(content), "0" * 64)
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        updater.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(content),
    )

    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download_asset(asset, tmp_path / "release.zip", cancel_event=cancelled)
    assert not (tmp_path / "release.zip.part").exists()


def _update_transaction(tmp_path, monkeypatch, *, legacy_layout=False):
    data_root = tmp_path / "user-data"
    monkeypatch.setenv("LRCPLUS_DATA_DIR", str(data_root))
    transaction = data_root / "updates" / "transactions" / uuid.uuid4().hex
    transaction.mkdir(parents=True)
    install_dir = tmp_path / "install" / "LRCPlus"
    (install_dir / "plugins" / "community" / "custom_plugin").mkdir(parents=True)
    (install_dir / "LRCPlus.exe").write_bytes(b"old application")
    (install_dir / "LRCPlusUpdater.exe").write_bytes(b"old trusted updater")
    (install_dir / "lrc_player_settings.json").write_text("{}", encoding="utf-8")
    (install_dir / "user-notes.txt").write_text("keep me", encoding="utf-8")
    (install_dir / "plugins" / "community" / "custom_plugin" / "plugin.py").write_text(
        "USER_PLUGIN = True", encoding="utf-8"
    )
    archive_path = transaction / "release.zip"
    package_files = _valid_package_files()
    if legacy_layout:
        root = "LRCPlus-v2026-11-02.0-Windows-x64"
        package_files = [
            (f"{root}/{name.split('/', 1)[1]}", content)
            for name, content in package_files
            if not name.casefold().endswith(("lrcplusupdater.exe", "lrcplus-update.json"))
        ]
    archive_path.write_bytes(_zip_bytes(package_files))
    request = {
        "install_dir": str(install_dir),
        "version": "2026-11-02.0",
        "current_version": "2026-11-01.4",
        "architecture": "x64",
        "asset_name": "LRCPlus-v2026-11-02.0-Windows-x64.zip",
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "size": archive_path.stat().st_size,
        "process_id": 1234,
    }
    request_path = transaction / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    return data_root, transaction, install_dir, request_path


class _StartedProcess:
    def poll(self):
        return None


def test_standalone_installer_preserves_user_files_and_retains_backup(tmp_path, monkeypatch):
    _data_root, transaction, install_dir, request_path = _update_transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(updater_helper, "_schedule_recovery", lambda _transaction: None)
    monkeypatch.setattr(updater_helper, "_remove_recovery", lambda _transaction_id: None)

    def launch(_args, **_kwargs):
        (transaction / "health.json").write_text("{}", encoding="utf-8")
        return _StartedProcess()

    monkeypatch.setattr(updater_helper.subprocess, "Popen", launch)

    result = updater_helper.install_update(request_path, wait_for_process=False)

    assert result == install_dir
    assert (install_dir / "LRCPlus.exe").read_bytes() == b"application"
    assert (install_dir / "user-notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (install_dir / "lrc_player_settings.json").read_text(encoding="utf-8") == "{}"
    assert (install_dir / "plugins" / "community" / "custom_plugin" / "plugin.py").exists()
    assert (install_dir / "plugins" / "core" / "lyrics_display" / "plugin.py").read_bytes() == b"new core plugin"
    assert (install_dir.parent / f"LRCPlus.backup-{transaction.name}" / "LRCPlus.exe").read_bytes() == b"old application"
    assert json.loads((transaction / "journal.json").read_text(encoding="utf-8"))["phase"] == "healthy"


def test_standalone_installer_reuses_existing_updater_for_legacy_release(tmp_path, monkeypatch):
    _data_root, transaction, install_dir, request_path = _update_transaction(
        tmp_path, monkeypatch, legacy_layout=True
    )
    monkeypatch.setattr(updater_helper, "_schedule_recovery", lambda _transaction: None)
    monkeypatch.setattr(updater_helper, "_remove_recovery", lambda _transaction_id: None)
    monkeypatch.setattr(
        updater_helper.subprocess, "Popen",
        lambda _args, **_kwargs: (
            (transaction / "health.json").write_text("{}", encoding="utf-8") or _StartedProcess()
        ),
    )

    updater_helper.install_update(request_path, wait_for_process=False)

    assert (install_dir / "LRCPlusUpdater.exe").read_bytes() == b"old trusted updater"
    manifest = json.loads((install_dir / "lrcplus-update.json").read_text(encoding="utf-8"))
    assert manifest == {"version": "2026-11-02.0", "architecture": "x64"}


def test_standalone_installer_rolls_back_if_new_app_cannot_launch(tmp_path, monkeypatch):
    _data_root, _transaction, install_dir, request_path = _update_transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(updater_helper, "_schedule_recovery", lambda _transaction: None)
    monkeypatch.setattr(updater_helper, "_remove_recovery", lambda _transaction_id: None)

    def fail_launch(*_args, **_kwargs):
        raise OSError("simulated launch failure")

    monkeypatch.setattr(updater_helper.subprocess, "Popen", fail_launch)

    with pytest.raises(OSError, match="simulated launch failure"):
        updater_helper.install_update(request_path, wait_for_process=False)

    assert (install_dir / "LRCPlus.exe").read_bytes() == b"old application"
    assert not list(install_dir.parent.glob("LRCPlus.failed-*"))


def test_recovery_restores_backup_after_interrupted_install(tmp_path, monkeypatch):
    data_root, transaction, install_dir, _request_path = _update_transaction(tmp_path, monkeypatch)
    backup = install_dir.with_name(f"LRCPlus.backup-{transaction.name}")
    install_dir.rename(backup)
    install_dir.mkdir()
    (install_dir / "LRCPlus.exe").write_bytes(b"partial update")
    journal = updater_helper._journal(transaction, install_dir, "2026-11-02.0", "installed")
    (transaction / "journal.json").write_text(json.dumps(journal), encoding="utf-8")

    updater_helper.recover_incomplete_transactions()

    assert (install_dir / "LRCPlus.exe").read_bytes() == b"old application"
    assert not backup.exists()


def test_recovery_restores_backup_if_power_fails_before_phase_update(tmp_path, monkeypatch):
    _data_root, transaction, install_dir, _request_path = _update_transaction(tmp_path, monkeypatch)
    backup = install_dir.with_name(f"LRCPlus.backup-{transaction.name}")
    install_dir.rename(backup)
    journal = updater_helper._journal(transaction, install_dir, "2026-11-02.0", "prepared")
    (transaction / "journal.json").write_text(json.dumps(journal), encoding="utf-8")

    updater_helper.recover_incomplete_transactions()

    assert (install_dir / "LRCPlus.exe").read_bytes() == b"old application"
    assert not backup.exists()


def test_health_acknowledgment_only_marks_active_install_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("LRCPLUS_DATA_DIR", str(tmp_path / "user-data"))
    transaction_id = uuid.uuid4().hex
    transaction = tmp_path / "user-data" / "updates" / "transactions" / transaction_id
    transaction.mkdir(parents=True)
    app_root = Path(updater.__file__).resolve().parents[1]
    (transaction / "journal.json").write_text(json.dumps({
        "install_dir": str(app_root),
        "version": "2026-11-02.0",
        "phase": "launching",
    }), encoding="utf-8")

    assert updater.acknowledge_update_health(transaction_id)
    assert json.loads((transaction / "health.json").read_text(encoding="utf-8"))["version"] == "2026-11-02.0"
    assert not updater.acknowledge_update_health("../outside")