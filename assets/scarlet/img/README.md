# Supplied Touhou sprite atlas

`spritemap.png` is the original 1711 × 5083 image provided by the user. Its
lower pink section contains color textures beside matching grayscale alpha
masks. The upper blue section is a visual atlas of the same artwork.

The transparent exports are in **`transparent/`**:

- `spritemap-transparent.png`: the upper atlas layout, 1711 × 1979 pixels,
  with transparent margins and backgrounds.
- 26 individual, named **256 × 256 RGBA PNGs** for Rumia, Cirno, Meiling,
  Patchouli, Sakuya, Remilia, and Flandre: portraits, animation sheets, and
  effects. Each export retains the texture's full canvas and frame positions.
- `sprites.json`: exact source color/mask rectangles, atlas rectangles,
  output filenames, and the original file's SHA-256 hash.

For Flandre, start with `flandre-portrait.png`, `flandre-portrait-alt.png`,
`flandre-portrait-tired.png`, `flandre-sprites.png`, and `flandre-effects.png`.
The portrait files here are separate expressions; the animation/effects files
contain multiple frames that still need to be cropped by the consuming code.

To regenerate from the workspace root:

```powershell
.\Scripts\python.exe assets/scarlet/img/extract_spritemap.py
```

The extractor uses the existing PySide6 dependency and does not require Pillow
or an image-generation service. It copies the lower section's color pixels
without redrawing, resizing, filtering, or changing RGB values, and assigns the
matching grayscale mask to the alpha channel. White remains visible, black
becomes transparent, and gray keeps its fractional opacity. This preserves
black clothing, outlines, and soft effects. The combined atlas copies those
same RGBA pixels into their original positions. Rerunning replaces only the
derived files in `transparent/`; it never writes to `spritemap.png` or the
game's existing artwork.

The original sprite sheet's source URL and ripper credit have not been
provided. Touhou Project characters and original game artwork belong to
ZUN / Team Shanghai Alice. These supplied assets retain their upstream rights
and are not covered by LRCPlus's MIT license.
