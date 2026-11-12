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

## Read-only Talk pages

Each article has a **Talk** tab (**ノート** in Japanese). Talk pages follow the manual's wiki layout with topic contents, section permalinks, indented replies, signatures, and editorial status labels. They contain explicitly fictional, scripted discussions; readers cannot add topics, comment, or reply. No discussion data is submitted, fetched, or saved. Articles without authored discussions show an empty state.

The yellow discussion banner names the current article and presents reading guidance, discussion etiquette, article principles, and local reference links. **Start a new topic** opens the local fictional login page, including on empty Talk pages. Each comment's **Reply** link opens the same login page. The account page's return link preserves the Talk page or exact comment. These actions do not enable posting.

Comments display fictional UTC timestamps generated in authored order by the shared `talk-timestamps.js`; they never represent live user activity. Each article starts at a deterministic date and time between January 1 and November 30, 2026, capped at the current time. Gaps normally vary from 2–45 minutes within a topic and 2–48 hours between topics; authored `[randomNmo]` directives override the next comment's gap. Timestamps never exceed the current time (rounded down to the minute), so comments may share a timestamp when the limit is reached. Dates are repeatable for the same article IDs, topic IDs, comment order, and jump directives, except that a date capped at the current time can advance as time passes. Editing text or author names does not change them. Mirror jump directives between language editions to keep their chronology aligned. Each timestamp is a comment permalink. The three-dot disclosure beside **Reply** contains **Copy link**, with a live confirmation, a local-file clipboard fallback, and a selectable permalink if copying is unavailable. Escape or an outside click closes the disclosure. The latest-comment row links to the last included reply and its topic and shows an empty state when there are no replies. The banner adapts to dark mode and stacks its columns on small screens.

Use `#article-id/talk` or `#article-id/talk/topic-id` for direct links. Article, language, and account return links preserve the relevant route. Talk pages support dark mode, narrow screens, and printing. They require JavaScript; without it, the article text remains available and the Talk tab stays hidden.

### Adding or editing discussions

For a browser editor with a live preview, run this from the repository root:

```console
Scripts/python.exe tools/edit_manual_talk.py
```

It opens a local editor in your browser. Keep the terminal open while editing. Switch between **English** and **日本語** to edit the Markdown sources, and choose an article to preview its discussion. **Save to manual** (or Ctrl+S / Cmd+S) saves both Markdown files and rebuilds both HTML editions. Changes in either language stay in the editor when switching tabs. Japanese translations are optional: missing topics use English in the preview and saved manual. Translated topics must keep the English article and topic IDs. Fallback text is generated without copying it into the Japanese Markdown source. Refresh a manual tab after saving to see the result.

The editor runs only on this computer, needs no extra Python packages or internet connection, and uses the same validation and HTML generation as the command below. It detects changes made to source or HTML files outside the editor and refuses to overwrite them; copy your pending edits before using **Reload files** in that case. Closing or reloading the editor with unsaved changes prompts you first. To stop the helper, press Ctrl+C in its terminal. Use `--no-browser` to print the address without opening it, or `--port 8765` to choose a fixed port. Opening `talk-editor.html` directly displays the launch instructions; use the helper's localhost address to edit actual files.

For a text editor and command-line workflow:

Edit `talk/en.md` and `talk/ja.md`, then run this command from the repository root:

```console
Scripts/python.exe tools/generate_manual_talk.py
```

Any Python 3.10+ interpreter works; no packages are required. The command rebuilds the embedded Talk templates in both editions. Refresh the manual and open `#article-id/talk` to read your changes. Commit the Markdown sources and the generated HTML together. To check for outdated templates without changing files, append `--check`.

The files use a small Markdown-like format:

```markdown
# getting-started

## A clearer first step {first-step} | Open

### Wiki editor

Should we add an example here?

#### Ayami

Yes, preferably one with music.

##### Wiki editor

I'll add one.
```

`#` selects an existing article ID. `##` starts a topic with a title and an explicit `{topic-id}`. The `| Status` suffix is optional: `## A clearer first step {first-step}` works on its own and displays no status badge. An empty `|` also omits the badge. `### Author` starts a comment; each additional `#` adds a reply level: `#### Author` is depth one, `##### Author` depth two, `###### Author` depth three, and so on, with no fixed depth limit. A reply cannot skip a nesting level when going deeper; it may return to any earlier level. Visual indentation is capped at depth four to preserve reading space, and deeper replies display their actual depth as a localized label. This is a custom heading-based format, so headings deeper than standard Markdown's six levels work here. Blank lines separate paragraphs; wrapped lines within a paragraph are joined with spaces. Comment bodies, titles, statuses, and author names are plain text: HTML is escaped and inline Markdown is not interpreted. Status text is free-form and should be localized in Japanese.

To force a larger time jump, put `[randomNmo]` on its own line before the next comment heading. Change `N` to any whole number from 1 to 120: `[random1mo]` jumps up to one calendar month, `[random4mo]` up to four, and `[random6mo]` up to six. The directive replaces that comment's normal gap with a repeatable random interval of at least one minute, then caps the result at the current time. If no time remains, the timestamp stays at the limit. It is not displayed as comment text. For example:

```markdown
[random6mo]

#### Ayami

Coming back to this discussion after a while…
```

Place a directive inside an article, before a comment heading (it may also precede a topic heading). It applies once, to the next comment. A directive with no following comment or an invalid month count reports an error. The editor's live preview includes the resulting fictional timestamps.

English defines the discussion topics and their order. Japanese may omit topics, articles, or the entire source file; missing discussions fall back to English. Translated topics must keep the English article and topic IDs, with topic IDs unique across each edition. Existing discussions are included as examples. The generator owns **all** Talk templates: add new topics to these sources rather than editing the HTML, and remove a topic from both sources to delete it. It validates both editions before writing, calculates comment and participant counts, and adds author signatures. Timestamps and controls are still generated by `manual.js`. Templates remain outside article source views and search indexes; reading the manual requires no Python or build step.

## Fictional accounts

**Create account** and **Log in** open `account.html` or `ja-account.html`. These are local Easter egg pages styled after a wiki login form. An incomplete login, incomplete registration, or mismatched registration password confirmation shows “The supplied credentials could not be authenticated.” (localized in Japanese); other attempts show an Ayami-themed error. Each attempt shows a one-second loading state with localized button text and a spinner, then shows its error while preserving the entered fields and checkbox selection. Controls are disabled while waiting; switching forms or leaving cancels the delay. Reduced-motion settings hide the animated spinner. `account.js` checks native empty-field validity and compares registration passwords locally; it uses no network or storage APIs. Controls start disabled until the handlers are attached, fields have no names, forms use `method="dialog"`, and page policy blocks form actions and network connections.

**Forgot password?** opens the local `#password-reset` view, which always shows a Forbidden message and an Ayami remark. It contains no reset form and makes no recovery request.

The return link preserves the originating manual route. Language switching preserves the current form and return route. These pages share the manual's sidebar, theme, and responsive styles; their localized text is maintained in the two HTML files and `account.js`.
