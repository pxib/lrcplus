# Local user manual

Open `index.html` directly in a browser. No server, installation, network connection, or build step is needed. Keep this folder in the repository layout: the sidebar logo and favicon reference `../../assets/media/`.

Use **Dark mode** beside Print to switch appearance. The manual initially follows the system theme, then remembers an explicit choice in browser storage when available. If storage is restricted for local files, the toggle still works for the current session. Print uses the light palette, and screenshots retain their original colors.

## Editing articles

Articles are ordinary HTML inside `#articles` in `index.html`. Each article needs a unique `id`, a `data-title`, an `h1`, and a `.subtitle` paragraph. Give section headings unique IDs. Links use `#article-id` or `#article-id/section-id`; they work when opened from disk.

`manual.js` creates article contents, section permalinks, breadcrumbs, related links, and the local search index. Add a space-separated `data-related` list to an article to select its related pages. Update the static A–Z index and sidebar when adding an article. With JavaScript disabled, all articles remain readable in one document.

## Reference accuracy

Settings defaults were checked against `core/settings.py` and `core/lyrics_settings.py`. Labels and scope were checked against `ui/settings_dialog.py`. In particular, the Japanese parser is global-only, while other supported lyric display preferences can have per-file overrides.

Plugin guides describe the bundled source plugins. Declared requirements come from their `manifest.json` files; controls and workflows were checked against the corresponding implementations. Recheck these sources after changing app behavior.

## Screenshots

`assets/screenshots/` contains captures of real Qt widgets with isolated preferences, an empty library, and three original demonstration lyric lines. No user collection, saved preferences, credentials, or personal lyrics were used. The editor view has no optional providers loaded, as its caption explains.

Numbered callouts are HTML overlays, not alterations to the captures. Their positions and labels are recorded in `assets/screenshots/annotations.json`; keep that file and the markup in sync when replacing a capture. The full-size image links provide an unobstructed view.

## Verification

Check direct `file://` opening, article and section links, screenshot callouts, related articles, search, print layout, and narrow-screen table scrolling. Search and navigation require no external requests. App features such as online lyrics, translation, and online reading generation retain their own connection requirements.
