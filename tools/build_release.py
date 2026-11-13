"""Build Windows release assets for GitHub Releases."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from core.updater import UpdateError, parse_version


def set_app_version(path: Path, requested_version: str) -> tuple[str, str]:
    version = requested_version.strip()
    if version[:1].lower() == "v":
        version = version[1:]
    try:
        parsed_version = parse_version(version)
    except UpdateError as error:
        raise ValueError(str(error)) from error

    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as source_file:
        source = source_file.read()
    tree = ast.parse(source, filename=str(path))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
    ]
    if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Constant):
        raise ValueError(f"Expected exactly one string APP_VERSION assignment in {path}.")
    value_node = assignments[0].value
    if not isinstance(value_node.value, str) or value_node.lineno != value_node.end_lineno:
        raise ValueError(f"APP_VERSION must be a one-line string literal in {path}.")
    current_version = value_node.value
    try:
        if parsed_version <= parse_version(current_version):
            raise ValueError("The new version must be newer than the current APP_VERSION.")
    except UpdateError as error:
        raise ValueError(str(error)) from error

    lines = source.splitlines(keepends=True)
    line = lines[value_node.lineno - 1]
    original_literal = ast.get_source_segment(source, value_node)
    quote = original_literal[:3] if original_literal[:3] in {"'''", '"""'} else original_literal[:1]
    if quote not in {"'", '"', "'''", '"""'} or not original_literal.endswith(quote):
        raise ValueError("APP_VERSION must use a simple quoted string literal.")
    replacement = f"{quote}{version}{quote}"
    lines[value_node.lineno - 1] = (
        line[:value_node.col_offset] + replacement + line[value_node.end_col_offset:]
    )
    with path.open("w", encoding="utf-8", newline="") as target_file:
        target_file.write("".join(lines))
    return current_version, version


def windows_architecture() -> str:
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported Windows architecture: {platform.machine()}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a Windows LRCPlus release.")
    parser.add_argument(
        "--version",
        help="Set a newer APP_VERSION before building, e.g. 2026-11-02.0",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("Release packaging currently supports Windows only.")
    if args.version:
        old_version, new_version = set_app_version(
            PROJECT_ROOT / "core" / "app_info.py", args.version
        )
        print(f"Updated APP_VERSION: {old_version} -> {new_version}")
        app_version = new_version
    else:
        from core.app_info import APP_VERSION as app_version

    architecture = windows_architecture()
    dist = PROJECT_ROOT / "dist"
    build = PROJECT_ROOT / "build"
    build.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(PROJECT_ROOT / "updater.spec")],
        cwd=PROJECT_ROOT,
        check=True,
    )
    manifest_path = build / "lrcplus-update.json"
    manifest_path.write_text(json.dumps({
        "version": app_version,
        "architecture": architecture,
    }, indent=2), encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(PROJECT_ROOT / "main.spec")],
        cwd=PROJECT_ROOT,
        check=True,
    )

    app_folder = dist / "LRCPlus"
    required = (app_folder / "LRCPlus.exe", app_folder / "LRCPlusUpdater.exe",
                app_folder / "lrcplus-update.json",
                app_folder / "themedata" / "optionalthemes.json",
                app_folder / "docs" / "manual" / "index.html",
                app_folder / "docs" / "manual" / "ja.html",
                app_folder / "docs" / "manual" / "manual.js",
                app_folder / "docs" / "manual" / "manual.css")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("The packaged application is missing: " + ", ".join(missing))

    release_dir = dist / "release"
    release_dir.mkdir(exist_ok=True)
    basename = f"LRCPlus-v{app_version}-Windows-{architecture}"
    archive_path = release_dir / f"{basename}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_folder.rglob("*")):
            if path.is_file():
                archive.write(path, Path("LRCPlus") / path.relative_to(app_folder))

    digest = sha256_file(archive_path)
    checksum_path = release_dir / f"{basename}.zip.sha256"
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    print(f"Created {archive_path}")
    print(f"SHA-256 {digest}")
    print(f"Created {checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
