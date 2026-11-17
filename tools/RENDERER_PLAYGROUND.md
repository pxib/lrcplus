# Renderer Playground

From the repository root, run:

```powershell
.\Scripts\python.exe tools\renderer_playground.py
```

The standalone PySide6 window uses the player's `FuriganaWidget` and LRCX parser.
It is a development tool and is explicitly excluded from both PyInstaller builds.
Choose a preset or paste timestamped LRC/LRCX, then select the row to inspect.
Use the slider, seconds field, or Play button to inspect karaoke without audio.
The selected row stays mounted while time changes, including before and after
its timestamp. Simultaneous rows can be selected individually.

Change viewport dimensions, font sizes, wrapping, direction, alignment, ruby,
romaji, and sweep style. Bounds marks the preview widget's rectangle. Last paint
reports CPU time spent in the production paint handler, including layout work
when needed; it is not GPU frame time or an FPS benchmark.

Save case writes the LRCX, controls, selected row, and timestamp as JSON. Load
case restores them. PNG exports the preview at the specified pixel dimensions,
including the background and bounds overlay when enabled. Turn bounds off for
a clean image. Cases do not embed fonts or automatic reading provider data;
matching fonts and provider settings are needed to reproduce those differences.

The preview uses the renderer's normal cached/background reading generation.
Authored `{base|reading}` annotations are useful for reproducible reading cases.
Player preferences are not edited. Share-card layouts and scrolling between
rows are outside this tool's current scope.
