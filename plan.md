# Feature ideas for LRCPlus

**Start with saved practice sections, then build a lyric coverage view and a recording match review.** Together they let someone return to a difficult line, find the songs that need lyric work, and catch lyrics for the wrong recording before saving.

Reviewed 50 distinct GitHub projects on November 25, 2026, using their READMEs, changelogs, or checked-in source. The sample includes desktop and mobile players, streaming clients, and music servers with playback interfaces. The survey records documented behavior. The LRCPlus column and roadmap are adaptations, and effort is relative to the current codebase.

LRCPlus already has session-scoped A–B markers, speed and pitch controls, waveform editing, local lyric-text search, editable provider previews, LRCX readings and karaoke timing, a playback queue, ReplayGain, and lyric cards. These ideas build on those workflows. [Local manual](docs/manual/index.html) · [Changelog](CHANGELOG.md) · [LRCX specification](LRCX_SPEC.md)

## Build first

### 1. Saved practice sections · Medium

Save a named passage from the existing A–B menu or a selection of timed lyric lines. Show saved passages for the current recording beside the waveform, with actions to jump to one, loop it, rename it, or delete it. A passage can optionally carry a speed and pitch override. Store recording-relative milliseconds in app data so saving a passage requires no lyric or audio file edit. For the first pass, use the existing local track identity and leave a section unavailable when its recording cannot be resolved.

**Done when** two sections on one track survive restart, a keyboard user can recall either, and recalling one while paused preserves pause. Check short ranges, per-section overrides, a changed audio duration, and sleep-timer precedence. Reuse the existing loop controls and [editor playback](ui/lyrics_editor_playback.py). [Sazanami](https://github.com/rsgarrido/sazanami/blob/master/README.md) · [Namida](https://github.com/namidaco/namida/blob/main/README.md) · [Gramophone](https://github.com/RealMrHex/Gramophone/blob/main/README.md)

### 2. Lyric coverage and review · Medium

Add a Library view showing the selected lyric source and coverage for a folder or playlist. Start with **Missing**, **Plain**, **Line-timed**, and **Word-timed**, with a separate **Needs timing review** flag. Count each status and give each row a direct action to Find Lyrics, open the editor, or inspect an existing timing issue. Derive coverage from existing resolution and parsing on a background worker. Refresh the affected row when a lyric is saved or its source changes. Keep the first version local and make provider search a per-row action.

**Done when** a mixed folder can be filtered by lyric state, saving a sidecar changes the row without a full rescan, and opening a timing warning focuses the relevant line. Distinguish a missing lyric from a file that failed to parse. Start with [library lyric search](core/library_lyrics.py) and [editor document actions](ui/lyrics_editor_document.py). [Navidrome](https://github.com/navidrome/navidrome/blob/master/README.md) · [Music App](https://github.com/dariomatias-dev/music_app/blob/main/README.md) · [SimpMusic](https://github.com/maxrave-dev/SimpMusic/blob/dev/README.md)

### 3. Recording match review · Medium

Show the playing track and the candidate side by side in Search Lyrics. Display the provider, lyric format, available duration and duration difference, and matching or conflicting version tags such as live, remaster, edit, and instrumental. Rank exact metadata and plausible duration first. Label missing metadata as unknown and let users preview and choose a result when several versions are plausible. Build on the existing editable preview and keep its drafts when browsing candidates. Try fingerprint lookup later if duration and version tags leave too many ambiguous matches.

**Done when** two candidates with the same title can be distinguished using available version and duration evidence, a manual selection survives reopening, and ranking alone never replaces an existing sidecar. Start with the [lyric search dialog](ui/lyrics_search_dialog.py). [Meelo](https://github.com/Arthi-chaud/Meelo/blob/main/README.md) · [Capsule](https://github.com/getcapsule/desktop/blob/master/README.md) · [Strawberry](https://github.com/strawberrymusicplayer/strawberry/blob/master/README.md)

## Build next

| Priority | Idea | First version and acceptance check |
| --- | --- | --- |
| 4 · Medium | Named queues | Save and switch between independent queues for listening, editing, and rehearsal. Persist order, duplicate entries, current item, and position. Keep missing entries visible and test restoring data from the current single-queue format. Start with [queue state](core/queue_controller.py) and [playlists](core/playlists.py). [Sazanami](https://github.com/rsgarrido/sazanami/blob/master/README.md) · [OuterTune](https://github.com/OuterTune/OuterTune/blob/lite/README.md) |
| 5 · Medium | Smart playlists for lyric work | Offer a small rule builder with **All** or **Any** matching and a live result count. Ship presets for **Favorites missing timed lyrics**, **Word timing needs review**, and **Recently played with karaoke lyrics**. Build on coverage from idea 2, existing [favorites](core/favorites.py), and [listening statistics](core/listening_statistics.py). Done when results update after editing lyrics or favoriting a track. [Strawberry](https://github.com/strawberrymusicplayer/strawberry/blob/master/README.md) · [Exaile](https://github.com/exaile/exaile/blob/master/README.md) · [Feishin](https://github.com/jeffvli/feishin/blob/development/README.md) |
| 6 · Small | Keyboard command palette | Search existing actions for **Find Lyrics**, **Open Editor**, **Save Practice Section**, and **Show Queue**. Check Ctrl+K for a shortcut conflict before assigning it. Done when commands respect editor focus, unavailable actions explain why, and Escape restores focus. [OpenWave](https://github.com/bebeyat/openwave/blob/main/README.md) · [cmus](https://github.com/cmus/cmus/blob/master/Doc/cmus.txt) |
| 7 · Medium | Audio signal path | Add a read-only details panel showing codec, active output, sample rate where available, ReplayGain, DSP state, and why gapless or crossfade is inactive. Derive the explanation from the running playback state. Done when it explains restrictions after a device, speed, or pitch change. Start with [audio control](core/audio_controller.py) and [audio sink](core/audio_sink.py). [Namida](https://github.com/namidaco/namida/blob/main/README.md) · [Soul Player](https://github.com/soulaudio/soul-player/blob/main/README.md) |
| 8 · Medium | Fullscreen rehearsal | Give selected lines, furigana, and optional translations a dedicated fullscreen layout with large transport controls, saved passage selection, and an optional count-in. Try it with the existing [lyric renderer](lyrics/furigana_widget.py) first. Done when Japanese ruby and translated lines remain legible at 125% and 150% Windows scaling and keyboard controls work throughout. [Retro Music Player](https://github.com/RetroMusicPlayer/RetroMusicPlayer/blob/dev/README.md) · [SimpMusic](https://github.com/maxrave-dev/SimpMusic/blob/dev/README.md) |

## Explore after the core features

- **Suggest possible sections.** Use repeated lyric text to propose chorus markers. Show the matching lines and let users confirm, rename, or dismiss each suggestion. Compare results on songs with repeated choruses, repeated filler lines, and untimed lyrics before considering audio analysis. [Gramophone](https://github.com/RealMrHex/Gramophone/blob/main/README.md)
- **Export a practice excerpt.** Try a plugin that exports a selected range from a local file with a matching lyric sidecar. Preview timestamps rebased to the excerpt, including words that cross its edges, and leave the original files untouched. Reuse the existing [plugin API](PLUGIN_API.md). [Muriel](https://github.com/fin6969/riff-muriel/blob/main/README.md)
- **Pilot one self-hosted library.** Try read-only OpenSubsonic browsing and playback through the existing [network playback](ui/network_playback.py) path. First confirm stable track identity, cached lyric source handling, and recovery after a server disconnect. [Amperfy](https://github.com/BLeeEZ/amperfy/blob/master/README.md) · [Feishin](https://github.com/jeffvli/feishin/blob/development/README.md)

For the first implementation ticket, wire **Save this loop** and **Recall saved loop** to the A–B menu for local tracks. Verify restart persistence, paused recall, media replacement with a shorter duration, and keyboard navigation before adding waveform markers or presets.

## Survey of 50 GitHub projects

| # | Project | Documented behavior | Idea for LRCPlus | Source |
| --- | --- | --- | --- | --- |
| 1 | Strawberry | Smart playlists, waveform seeking, and acoustic fingerprint-based tag lookup. | Use recording clues to rank lyric matches. | [GitHub](https://github.com/strawberrymusicplayer/strawberry/blob/master/README.md) |
| 2 | Clementine | Restores the current playlist and remembers the last played track when switching playlists. | Give named queues independent positions. | [GitHub](https://github.com/clementine-player/Clementine/blob/master/Changelog) |
| 3 | Quod Libet | Flexible metadata queries and editing, with Python plugins. | Use a simple rule builder over existing library data. | [GitHub](https://github.com/quodlibet/quodlibet/blob/main/README.rst) |
| 4 | DeaDBeeF | Plugin-backed resampling and configurable MP3 decoder selection. | Expose the active decoder and resampler in playback diagnostics. | [GitHub](https://github.com/DeaDBeeF-Player/deadbeef/blob/master/README) |
| 5 | Audacious | Dockable lyrics, artwork, and visualization plugins. | Give fullscreen rehearsal an optional compact companion view. | [GitHub](https://github.com/audacious-media-player/audacious/blob/master/README.md) |
| 6 | Exaile | Tabbed playlists and smart playlists with filtering. | Keep named queue tabs and computed collections distinct. | [GitHub](https://github.com/exaile/exaile/blob/master/README.md) |
| 7 | Museeks | Automatic library refresh, queue management, and speed control. | Refresh lyric coverage after local files change. | [GitHub](https://github.com/martpie/museeks/blob/master/README.md) |
| 8 | Nuclear | One queue and playlist UI across multiple sources, plus a plugin store. | Keep source selection attached to each recording. | [GitHub](https://github.com/nukeop/nuclear/blob/master/README.md) |
| 9 | fooyin | Waveform and spectrogram views, autoplaylists, and configurable transitions. | Use the editor timeline for rehearsal markers and actionable timing review. | [GitHub](https://github.com/fooyin/fooyin/blob/master/README.md) |
| 10 | cmus | Keyboard-driven views and a temporary queue that takes precedence over normal playback. | Make queue switching and the next playback source clear. | [GitHub](https://github.com/cmus/cmus/blob/master/Doc/cmus.txt) |
| 11 | musikcube | Keyboard-first navigation and optional server-backed playback. | Expose frequent player commands from the keyboard. | [GitHub](https://github.com/clangen/musikcube/blob/master/README.md) |
| 12 | Tauon | Swappable UI widgets and direct artist or track search shortcuts. | Add quick actions to look up an unmatched song. | [GitHub](https://github.com/Taiko2k/Tauon/blob/master/README.md) |
| 13 | Elisa | A Now Playing view with tabs for track metadata and lyrics. | Put lyric source and timing details beside the active lyrics. | [GitHub](https://github.com/KDE/elisa/blob/master/src/qml/ContextView.qml) |
| 14 | Amarok | Drag-and-drop playlist creation and lyric downloads. | Send selected tracks straight into a named queue. | [GitHub](https://github.com/KDE/amarok/blob/master/README) |
| 15 | Harmonoid | Lyrics from LRC, tags, and online sources, plus translations. | Show the active source and lyric format. | [GitHub](https://github.com/harmonoid/harmonoid/blob/master/README.md) |
| 16 | Dopamine | Desktop player focused on straightforward library organization and listening. | Keep the first queue and library views simple. | [GitHub](https://github.com/digimezzo/dopamine/blob/master/README.md) |
| 17 | Capsule | Imports playlist metadata separately from playback and resolves entries to available recordings. | Show ambiguous or unresolved matches for manual review. | [GitHub](https://github.com/getcapsule/desktop/blob/master/README.md) |
| 18 | Soul Player | Configurable crossfade curves and WASAPI exclusive output. | Explain the current output mode and when processing changes it. | [GitHub](https://github.com/soulaudio/soul-player/blob/main/README.md) |
| 19 | Muriel | Manual lyric timing, fullscreen karaoke, and local clip trimming. | Prototype rehearsal sections and optional local clip export. | [GitHub](https://github.com/fin6969/riff-muriel/blob/main/README.md) |
| 20 | OpenWave | Per-song lyric offset and a Ctrl+K command palette. | Surface lyric offset and common editor actions from one keyboard entry point. | [GitHub](https://github.com/bebeyat/openwave/blob/main/README.md) |
| 21 | Resonance | Local metadata and cover management alongside configurable shortcuts. | Make local fixes reachable from the library. | [GitHub](https://github.com/todouro/resonance/blob/main/README.md) |
| 22 | Amperfy | Offline mode, multiple server accounts, and playback shortcuts. | If remote libraries land, keep their offline sources and credentials scoped. | [GitHub](https://github.com/BLeeEZ/amperfy/blob/master/README.md) |
| 23 | Tempo | Browsing and discovery shaped by listening history and randomness. | Start with transparent local rules for rediscovery. | [GitHub](https://github.com/CappielloAntonio/tempo/blob/main/README.md) |
| 24 | Retro Music Player | Now Playing layouts with lyrics over artwork or in place of it. | Offer a lyric-first rehearsal preset. | [GitHub](https://github.com/RetroMusicPlayer/RetroMusicPlayer/blob/dev/README.md) |
| 25 | Auxio | Multi-value metadata, original dates, sort tags, and unified artist handling. | Prefer precise metadata before fuzzy title matching. | [GitHub](https://github.com/OxygenCobalt/Auxio/blob/dev/README.md) |
| 26 | Gelli | Partial-match search and library-scoped filters for Jellyfin music. | Allow lyric coverage to be filtered by library root. | [GitHub](https://github.com/dkanada/gelli/blob/master/README.md) |
| 27 | Vinyl Music Player | Android Auto and ReplayGain for local playback. | Keep normalization visible when playback moves between devices. | [GitHub](https://github.com/VinylMusicPlayer/VinylMusicPlayer/blob/master/README.md) |
| 28 | Symphony | Filename and path sorting in an offline player. | Make folder and filename visible when identical titles need disambiguation. | [GitHub](https://github.com/zyrouge/symphony/blob/main/README.md) |
| 29 | Harmony Music | Bookmarks for albums and artists, plus plain and synchronized lyrics. | Distinguish bookmarks for discovery from saved practice passages. | [GitHub](https://github.com/anandnet/Harmony-Music/blob/main/README.md) |
| 30 | ViMusic | Persistent queue and in-player editing of fetched synchronized lyrics. | Let the chosen recording and lyric draft survive a queue switch. | [GitHub](https://github.com/vfsfitvnm/ViMusic/blob/master/README.md) |
| 31 | RiMusic | Editable synchronized lyrics and separate speed and pitch controls. | Store rehearsal speed and pitch with each saved passage. | [GitHub](https://github.com/fast4x/RiMusic/blob/master/README.md) |
| 32 | OuterTune | Multiple queues and word-timed lyrics in formats including LRC and TTML. | Preserve queue context and show which timing data is available. | [GitHub](https://github.com/OuterTune/OuterTune/blob/lite/README.md) |
| 33 | InnerTune | Synchronized lyrics and a built-in lyrics translator. | Identify when a track has timing but lacks a translation. | [GitHub](https://github.com/z-huang/InnerTune/blob/dev/README.md) |
| 34 | SimpMusic | Word-by-word authoring, romanization, timing offset, and monthly recap playlists. | Use lyric editing coverage and listening history as rule inputs. | [GitHub](https://github.com/maxrave-dev/SimpMusic/blob/dev/README.md) |
| 35 | Metrolist | Shared listening and playlist imports from multiple clients and services. | Explore a portable queue format before adding live collaboration. | [GitHub](https://github.com/MetrolistGroup/Metrolist/blob/main/README.md) |
| 36 | Fossify Music Player | Offline playlists and customizable playback widgets. | Keep practice transport usable without accounts or network access. | [GitHub](https://github.com/FossifyOrg/Music-Player/blob/main/README.md) |
| 37 | Namida | Audio path inspection, per-track sound presets, and local-network data sync. | Prototype a signal path panel and remember per-track practice settings. | [GitHub](https://github.com/namidaco/namida/blob/main/README.md) |
| 38 | Sazanami | Multiple persistent queues, rules based on library and listening data, and an offline lyrics editor. | Model queue switching and a small rule builder on local data. | [GitHub](https://github.com/rsgarrido/sazanami/blob/master/README.md) |
| 39 | Feishin | Navidrome smart playlist editing and both timed and plain lyrics. | Give rule editing a live track count and a visible timing filter. | [GitHub](https://github.com/jeffvli/feishin/blob/development/README.md) |
| 40 | Sonixd | Large queues and playlists, with crossfade-based gapless approximation. | Keep queue switching responsive even with long lists. | [GitHub](https://github.com/jeffvli/sonixd/blob/main/README.md) |
| 41 | Navidrome | Configurable priority for embedded and sidecar lyric formats. | Show the selected lyric source and allow an explicit override. | [GitHub](https://github.com/navidrome/navidrome/blob/master/README.md) |
| 42 | LMS | Release grouping and multi-value tags such as mood and genre. | Display possible recording versions and let rules combine metadata. | [GitHub](https://github.com/epoupon/lms/blob/master/README.md) |
| 43 | Meelo | Groups album releases and connects related songs and track versions. | Make studio, live, and alternate versions visible before applying lyrics. | [GitHub](https://github.com/Arthi-chaud/Meelo/blob/main/README.md) |
| 44 | Psst | Spotify links can be opened through the search bar. | Let pasted song or playlist links open the existing import flow. | [GitHub](https://github.com/jpochyla/psst/blob/main/README.md) |
| 45 | spotify_player | Keyboard-first interaction, fuzzy search, and synchronized lyrics. | Reuse library search in a combined track and action launcher. | [GitHub](https://github.com/aome510/spotify-player/blob/master/README.md) |
| 46 | ncspot | Vim keybindings and IPC-based remote control. | Make a small set of rehearsal actions keyboard-friendly. | [GitHub](https://github.com/hrkfdn/ncspot/blob/main/README.md) |
| 47 | Moosync | Mixes tracks from several providers in one playlist. | Preserve per-item source and show unavailable tracks when restoring a queue. | [GitHub](https://github.com/Moosync/Moosync/blob/main/README.md) |
| 48 | Pear Desktop | Custom CSS themes and an extension system. | Try optional presentation features through the existing plugin API first. | [GitHub](https://github.com/pear-devs/pear-desktop/blob/master/README.md) |
| 49 | Music App | Searches inside playlists and exposes per-folder scan inclusion. | Apply coverage checks to a selected folder or playlist. | [GitHub](https://github.com/dariomatias-dev/music_app/blob/main/README.md) |
| 50 | Gramophone (RealMrHex) | Detects repeated hooks and remembers the playback position. | Explore section suggestions that users can confirm and save. | [GitHub](https://github.com/RealMrHex/Gramophone/blob/main/README.md) |
