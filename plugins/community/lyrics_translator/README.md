# Lyrics Translator

One optional plugin for **Argos Translate (poor quality)**, **Azure AI Translator**,
**Google Cloud Translation**, **DeepL API**, and **MyMemory**. Translation runs only
when requested and results can be edited before applying or saving.

Inside the **Lyrics Editor**, open the **Translations** tab to translate
the current document, including unsaved edits. Review the results and choose
**Apply to lyrics editor** to insert translation rows as one undoable edit.
Use the editor's normal Save action when ready. The tab appears when this
optional plugin is loaded and keeps its results while you switch tabs.
**Reload editor lyrics** refreshes the source after other edits. Existing
translations are preserved. Closing the editor cancels any running translation.
The editor warns before discarding unapplied or running work.
Reloading lyrics also asks before discarding reviewed drafts. The standalone
Argos window warns on close or Escape, and recognizes a successful save so
already-saved results do not trigger an unnecessary warning.

Select table rows using Ctrl/Shift and choose **Translate selected rows** to
translate or retry just those lines. Other drafts remain intact. **Translate
missing rows** fills only empty rows or rows without a draft for the chosen
language pair. Quota details include a Missing rows / Selected rows selector.
Results for different language pairs are kept separate when previewing/applying.
Editing an existing draft retains its original language pair. Clear the cell or
explicitly retranslate it to assign a new pair.

Cloud providers are included and work without Argos dependencies. See
[cloud setup and encrypted API keys](CLOUD_PROVIDERS.md). Settings and encrypted
keys from the former Argos/cloud plugins migrate automatically to this plugin.

## Offline Argos setup

1. Install the optional Argos dependency in **Settings > Dependencies**,
   then restart as requested. Argos includes native libraries and its dependency
   download can be substantial; it is not bundled into the main application.
2. Open a song with timed `.lrc` or `.lrcx` lyrics.
3. Choose **Plugins > Argos Translate (poor quality)…**.
4. Download the desired language packages from the
   [official package index](https://www.argosopentech.com/argospm/index/) and use
   **Install model file…** to install each `.argosmodel` in the selected folder.
5. Select source and target languages and choose **Translate missing rows**.
6. Review/edit the translation column. **Save translated copy…** creates a
   separate `.lrcx` file and associates it with the original song.

The plugin shows available direct or intermediate-language routes from installed
packages. For example, Japanese → Chinese can use Japanese → English and
English → Chinese packages; the dialog displays the intermediate language.
Intermediate translation can reduce quality further. Package availability and
output quality vary. The plugin does not download models automatically.

Lyrics are processed on the CPU in a cancellable helper process. It uses Argos
language packages, their tokenizers, and CTranslate2 directly with the existing
lyric boundaries, so sentence-segmentation models are not downloaded at runtime.
The Argos provider calls no remote translation service.

Each translation is paired with its source row index, including lyrics sharing
a timestamp. Repeated text is translated once per run. Rows already containing
`{\nt}` are preserved and skipped; empty timed rows are also skipped. Plain-text
lyrics without timestamps are not supported. Originals, ruby, styles, word timing,
line ends, offsets, and document metadata are retained by the host's Plugin API v5
helpers. Output defaults to `<lyrics>.<target>.machine.lrcx` and the source lyric
file cannot be chosen as the destination.

Changing songs while a translation runs does not retarget its result. The dialog
shows the captured source song and saving associates the copy with that song.

References: [Argos API and language packages](https://argos-translate.readthedocs.io/en/latest/),
[Argos package translation implementation](https://github.com/argosopentech/argos-translate/blob/master/argostranslate/translate.py),
[CTranslate2 translation API](https://opennmt.net/CTranslate2/python/ctranslate2.Translator.html).
