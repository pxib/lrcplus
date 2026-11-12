# Local user manual

Open `index.html` directly in a browser. No server, installation, network connection, or build step is needed. Keep this folder in the repository layout: the sidebar logo and favicon reference `../../assets/media/`.

Use **Dark mode** beside Print to switch appearance. The manual initially follows the system theme, then remembers an explicit choice in browser storage when available. If storage is restricted for local files, the toggle still works for the current session. Print uses the light palette, and screenshots retain their original colors.

Use **View source** beside Read to inspect the current article's HTML offline. The read-only view excludes generated contents, section links, and related links. Select **Read** to return to the article; source links such as `#getting-started/source` can be bookmarked. Source viewing requires JavaScript.

## Editing articles

Articles are ordinary HTML inside `#articles` in `index.html`. Each article needs a unique `id`, a `data-title`, an `h1`, and a `.subtitle` paragraph. Give section headings unique IDs. Links use `#article-id` or `#article-id/section-id`; they work when opened from disk.

`manual.js` creates article contents, section permalinks, breadcrumbs, related links, and the local search index. Add a space-separated `data-related` list to an article to select its related pages. Update the static A–Z index and sidebar when adding an article. With JavaScript disabled, all articles remain readable in one document. Character articles use `class="character-article"` for a right-hand profile box and `data-toc="after-lead"` to place generated contents after the introduction, immediately before the first section. The profile stacks with the text on narrow screens and in print.

## Reference accuracy

Settings defaults were checked against `core/settings.py` and `core/lyrics_settings.py`. Labels and scope were checked against `ui/settings_dialog.py`. In particular, the Japanese parser is global-only, while other supported lyric display preferences can have per-file overrides.

Plugin guides describe the bundled source plugins. Declared requirements come from their `manifest.json` files; controls and workflows were checked against the corresponding implementations. Recheck these sources after changing app behavior.

## Screenshots

`assets/screenshots/` contains captures of real Qt widgets with isolated preferences, an empty library, and three original demonstration lyric lines. No user collection, saved preferences, credentials, or personal lyrics were used. The editor view has no optional providers loaded, as its caption explains.

Numbered callouts are HTML overlays, not alterations to the captures. Their positions and labels are recorded in `assets/screenshots/annotations.json`; keep that file and the markup in sync when replacing a capture. The full-size image links provide an unobstructed view.

## Verification

Check direct `file://` opening, article and section links, screenshot callouts, related articles, search, print layout, and narrow-screen table scrolling. Search and navigation require no external requests. App features such as online lyrics, translation, and online reading generation retain their own connection requirements.

## Zero gravity Easter egg

Outside text fields, enter **↑ ↑ ↓ ↓ ← → ← → B A** (no Enter required). Words throughout the current article lift off the page, drift gently, bounce off the document edges, and can be dragged and thrown with a mouse or touch. Thrown words gradually lose momentum and settle; untouched words keep their gentle drift. Scrolling stays enabled: words move with the document, and the restore button stays on screen. On touchscreens, swipe between words to scroll and drag a word to throw it. Reduced-motion preferences keep words still until thrown. **Escape** or **Restore gravity** restores the original text nodes and focus. Navigation, resizing, printing, and leaving the page also end the effect. It works offline in both languages and adds no dependencies or saved state.

## Languages

The manual has two complete offline editions: `index.html` (English) and `ja.html` (Japanese). Use the **Languages** section at the bottom of the sidebar to switch editions. With JavaScript enabled, the switch preserves the article, section, search query, or source view. Without JavaScript, both editions remain readable and the language links open the other edition's main page.

Keep article IDs, section IDs, screenshot callout IDs, related-page IDs, and asset paths identical between editions. Translate the article body, `data-title`, navigation, image descriptions, and accessibility labels. Japanese keeps the app's English control names and screenshots so readers can identify the corresponding controls; settings tables show both Japanese and English names. Format syntax, shortcuts, filenames, package requirements, and provider names retain their technical spelling.

`manual.js` selects interface messages from the document's `lang` attribute. Add translated generated labels there, and localized contents-toggle labels in `manual.css`. Article search indexes only the currently open edition; changing the manual language does not change the app's language or lyric translation target. View source shows the current edition's article HTML. Review both editions when changing the manual.

## Fictional accounts

**Create account** and **Log in** open `account.html` or `ja-account.html`. These are local Easter egg pages styled after a wiki login form. An incomplete login, incomplete registration, or mismatched registration password confirmation shows “The supplied credentials could not be authenticated.” (localized in Japanese); other attempts show an Ayami-themed error. Each attempt shows a one-second loading state with localized button text and a spinner, then shows its error while preserving the entered fields and checkbox selection. Controls are disabled while waiting; switching forms or leaving cancels the delay. Reduced-motion settings hide the animated spinner. `account.js` checks native empty-field validity and compares registration passwords locally; it uses no network or storage APIs. Controls start disabled until the handlers are attached, fields have no names, forms use `method="dialog"`, and page policy blocks form actions and network connections.

**Forgot password?** opens the local `#password-reset` view, which always shows a Forbidden message and an Ayami remark. It contains no reset form and makes no recovery request.

The return link preserves the originating manual route. Language switching preserves the current form and return route. These pages share the manual's sidebar, theme, and responsive styles; their localized text is maintained in the two HTML files and `account.js`.
