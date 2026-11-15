# Favorites and personal reading dictionaries

## Favorites

Click the heart beside the pitch controls to favorite the playing song. In the
library, click a track's heart in the Favorite column. The library and queue
right-click menus also let you add or remove selected tracks in one action.
For a mixed selection, **Add to favorites** favorites every selected track.

Turn on **Favorites** above the library to show favorite songs. This combines
with both track-detail searches and local lyric searches. Favorites survive
rescans and temporarily missing files, and do not change audio tags or queue
order. They identify local file paths; moving a file does not transfer its
favorite automatically.

## Reading dictionaries

Open **Settings > Lyrics > Global > Readings > Manage reading dictionaries**.
The left panel lists named dictionaries; its checkboxes enable or disable each
dictionary. **Manage** provides new, rename, delete, priority, import and export
actions. Use **Add entry** and edit the table's Reading, Word, Reading type,
Comment and Match columns. The search field filters the selected dictionary.

For example, enter `あす` as Reading, `明日` as Word, and select Japanese furigana.
Use kana for Japanese readings; the existing romaji display converts that
reading. Other reading types are Mandarin pinyin, Korean romanization,
Cantonese Jyutping, and Cantonese Yale. Select the corresponding reading mode
when generating lyrics; Auto uses its resolved language/provider.

Entries default to **Whole word**. They match literal text at the reading
parser's word boundaries, including phrases that span complete words. For
example, `私 → わたし` changes standalone `私` in `私は` while leaving `私立`
unchanged. Splitting a word into ruby display pieces or karaoke chunks does
not create new word boundaries.

When the parser supplies no word boundaries, matching conservatively keeps
unbroken text runs together. This avoids replacing characters inside compounds
when automatic reading providers are disabled or unavailable. Choose
**Inside words** explicitly for an entry that should match parts of larger words.
Existing dictionaries and Google Japanese Input imports default to Whole word.

The longest eligible match at each position wins. For identical words of the same
reading type, the first enabled dictionary wins; move dictionaries using
Manage to change priority. Duplicate words of the same type within a dictionary
must be resolved before saving.

Press **OK** in the manager, then **OK** in Settings to save. Cancel in either
window discards the corresponding draft. Changes refresh the current lyric
display without restarting playback. Explicit readings authored in a song
remain authoritative. Translation rows and karaoke timings are preserved.
Disabling or deleting an entry restores the original generated reading.

Export a selected dictionary as LRCPlus JSON or TSV. Import accepts those files
and Google Japanese Input's UTF-8 or BOM-marked UTF-16 tab-separated exports
(`Reading`, `Word`, `Part of speech`, `Comment`). LRCPlus TSV exports add an
optional fifth `Match` column (`word` or `substring`). Google entries become Japanese
furigana entries; part of speech is not used for pronunciation matching.
Imports create separate dictionaries instead of overwriting existing entries.

Dictionaries are stored in `reading-dictionaries.json` in the LRCPlus app-data
directory, with the previous saved version retained as `.bak`. Invalid existing
data is preserved and reported when opening the manager.
