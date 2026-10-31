"""Extract the supplied texture atlas using its existing grayscale alpha masks.

Run from any directory with the project's Python/PySide6 environment:
    python assets/scarlet/img/extract_spritemap.py

RGB pixels, tile dimensions, and grayscale mask values are copied unchanged.
The source sheet and the game's existing artwork are never overwritten.
"""

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage


HERE = Path(__file__).resolve().parent
TILE_SIZE = 256
SOURCE_SIZE = (1711, 5083)
ATLAS_SIZE = (1711, 1979)

# name, color tile origin, alpha-mask origin, position in the upper atlas.
TILES = (
    ("rumia-portraits", (25, 1992), (306, 1992), (25, 25)),
    ("rumia-portraits-alt", (587, 1992), (868, 1992), (306, 25)),
    ("rumia-effects", (1149, 1992), (1430, 1992), (587, 25)),
    ("rumia-sprites", (25, 2273), (306, 2273), (868, 25)),
    ("cirno-portraits", (25, 2554), (306, 2554), (25, 306)),
    ("cirno-effects", (587, 2554), (868, 2554), (306, 306)),
    ("cirno-sprites", (1149, 2554), (1430, 2554), (587, 306)),
    ("meiling-portraits", (25, 2835), (306, 2835), (25, 587)),
    ("meiling-portraits-alt", (587, 2835), (868, 2835), (306, 587)),
    ("meiling-effects", (1149, 2835), (1430, 2835), (587, 587)),
    ("patchouli-portraits", (25, 3116), (306, 3116), (25, 868)),
    ("patchouli-portraits-alt", (587, 3116), (868, 3116), (306, 868)),
    ("patchouli-effects", (1149, 3116), (1430, 3116), (587, 868)),
    ("sakuya-portraits", (25, 3397), (306, 3397), (25, 1149)),
    ("sakuya-portraits-alt", (587, 3397), (868, 3397), (306, 1149)),
    ("sakuya-effects", (1149, 3397), (1430, 3397), (587, 1149)),
    ("sakuya-sprites", (25, 3678), (306, 3678), (868, 1149)),
    ("remilia-portrait", (25, 3959), (587, 3959), (25, 1430)),
    ("remilia-portrait-alt", (306, 3959), (587, 3959), (306, 1430)),
    ("remilia-effects", (868, 3959), (1149, 3959), (587, 1430)),
    ("remilia-sprites", (25, 4240), (306, 4240), (868, 1430)),
    ("flandre-portrait", (25, 4521), (587, 4521), (25, 1711)),
    ("flandre-portrait-alt", (306, 4521), (587, 4521), (306, 1711)),
    ("flandre-portrait-tired", (868, 4521), (1149, 4521), (587, 1711)),
    ("flandre-effects", (25, 4802), (306, 4802), (868, 1711)),
    ("flandre-sprites", (587, 4802), (868, 4802), (1149, 1711)),
)


def extract_tile(sheet, color_origin, mask_origin):
    color = sheet.copy(QRect(*color_origin, TILE_SIZE, TILE_SIZE))
    color = color.convertToFormat(QImage.Format.Format_RGBA8888)
    mask = sheet.copy(QRect(*mask_origin, TILE_SIZE, TILE_SIZE))
    mask = mask.convertToFormat(QImage.Format.Format_Grayscale8)
    # At 256 pixels both images have tightly packed scanlines. Only the alpha
    # byte is replaced, including fractional coverage for glow and soft edges.
    color.bits()[3::4] = mask.constBits()
    return color


def save_png(image, path):
    if not image.save(str(path), "PNG"):
        raise OSError(f"Could not write {path}")


def export(source=HERE / "spritemap.png", destination=HERE / "transparent"):
    source, destination = Path(source), Path(destination)
    sheet = QImage(str(source))
    if sheet.isNull() or (sheet.width(), sheet.height()) != SOURCE_SIZE:
        raise ValueError(f"Expected the supplied {SOURCE_SIZE[0]} × {SOURCE_SIZE[1]} sprite sheet: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    atlas = QImage(*ATLAS_SIZE, QImage.Format.Format_RGBA8888)
    atlas.fill(0)
    metadata = {
        "source": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "atlas": "spritemap-transparent.png",
        "atlas_size": list(ATLAS_SIZE),
        "tile_size": TILE_SIZE,
        "tiles": {},
    }
    for name, color_origin, mask_origin, atlas_origin in TILES:
        tile = extract_tile(sheet, color_origin, mask_origin)
        filename = f"{name}.png"
        save_png(tile, destination / filename)
        # Copy bytes without blending or compositing, preserving the exact RGBA
        # values in both the individual tile and assembled transparent atlas.
        tile_data, atlas_data = tile.constBits(), atlas.bits()
        x, y = atlas_origin
        for row in range(TILE_SIZE):
            offset = (y + row) * atlas.bytesPerLine() + x * 4
            start = row * tile.bytesPerLine()
            atlas_data[offset:offset + TILE_SIZE * 4] = tile_data[start:start + TILE_SIZE * 4]
        metadata["tiles"][name] = {
            "file": filename,
            "atlas_rect": [*atlas_origin, TILE_SIZE, TILE_SIZE],
            "source_color_rect": [*color_origin, TILE_SIZE, TILE_SIZE],
            "source_mask_rect": [*mask_origin, TILE_SIZE, TILE_SIZE],
        }
    save_png(atlas, destination / metadata["atlas"])
    (destination / "sprites.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


if __name__ == "__main__":
    result = export()
    print(f"Exported {len(result['tiles'])} transparent tiles and {result['atlas']} to {HERE / 'transparent'}")
