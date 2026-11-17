# English Katakana

Optional English pronunciation annotations using the offline
[alkana](https://github.com/zomysan/alkana.py) dictionary.

1. Open Settings → Dependencies and install the English Katakana dependency.
2. Ensure English Katakana is enabled in Settings → Plugins, then restart LRCPlus.
3. In the lyrics editor's reading generation controls, choose
   **English → Katakana (alkana)** under **Generate as**, then generate readings.

The provider is never selected by Auto. English source text, punctuation and
spacing stay intact; katakana is added as a reading. Unknown words remain
without a generated reading. Contractions with straight or curly apostrophes
use the same dictionary lookup. There is no neural fallback or automatic
guessing for missing plurals, names, or inflections.

After generating readings for a timestamp or all lyrics, the editor warns if
English words remain unannotated and lists up to 20 distinct missing words.
Translation rows and words with existing manual readings are excluded.

To fill gaps or change spellings, add an **English katakana** entry in Reading
Dictionaries, or edit a song's readings directly. The existing document
generation-mode tag remembers an explicit choice when saving lyrics.

Alkana 0.0.3 is GPL-2.0 licensed and credits the Bilingual Emacspeak Project
dictionary. It is an optional dependency, not bundled into the core requirements.

The dictionary loads only in a temporary helper process when readings are
requested. That process exits after conversion; the player retains only lyric
readings. Generate All batches the song into one helper request. Restart after
updating from 1.0.0 to release any dictionary already imported by that version.
