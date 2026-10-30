# Discord Rich Presence

Open **Settings > Plugins > Discord Rich Presence > Settings** to customize the
activity. Changes take effect when you save; restarting the plugin is unnecessary.

- Choose Listening to, Playing, Watching, or Competing in.
- Edit the details, state, and artwork hover text. Leave a field empty to hide it.
- Show or hide playback progress and activity while paused or stopped.
- Control lyric sharing, lines between lyric updates, and artwork sources.
- Detect fallback artwork language from lyrics, or choose Japanese, Korean,
  Chinese, Cantonese, or Vietnamese manually.
- Check the text preview before saving, or use Restore defaults to reset the form.
  Cancel discards edits, including a reset.

Text templates support these placeholders:

| Placeholder | Value |
| --- | --- |
| `{title}` | Track title, falling back to the filename |
| `{artist}` | Artist, or `Unknown artist` |
| `{album}` | Album, or `Unknown album` |
| `{album_or_artist}` | Album, falling back to the artist |
| `{lyric}` | Published lyric line; empty when disabled or unavailable |
| `{lyric_suffix}` | ` | ` followed by the lyric; empty when disabled or unavailable |
| `{status}` | `Playing`, `Paused`, or `Stopped` |

For example, use `{artist} - {title}` for details and `{status} | {album}`
for state. Unknown placeholders remain literal. Each rendered field is limited
to 128 characters. Single-character details/state text is padded invisibly to
meet [Discord's minimum length](https://discord.com/developers/docs/social-sdk/classdiscordpp_1_1Activity.html).
Artwork hover text appears only when artwork is available.
The preview uses the current track when available and sample text otherwise.

Lyric updates retain a five-second minimum cooldown. Playback progress appears
only while playing. Artwork comes from MusicBrainz/Cover Art Archive or embedded
audio artwork uploaded to Litterbox, according to your existing artwork settings.
Embedded artwork uploads are limited to 512 pixels on the longest edge and 1 MB,
preserving aspect ratio without enlarging smaller covers. Only the upload copy
is resized; artwork in the audio file stays unchanged. JPEG and PNG covers are
accepted; invalid images and inputs over 20 MB or 25 million pixels are skipped.

When neither source provides a cover (or both are disabled), the activity uses
the playing, paused, or idle album placeholder through its direct CDN image URL
(share-page links return HTML and cannot be displayed as Discord artwork).
**Fallback artwork language** defaults to **Auto (from lyrics)**: kana selects
Japanese, Hangul selects Korean, Han characters select Chinese, and Vietnamese
diacritics select Vietnamese. Cantonese reading modes (Jyutping/Yale) or
distinctive Cantonese characters select the
Cantonese variant; Furigana/Pinyin modes help resolve Han-only lyrics. Unknown,
unsupported, and missing lyrics use Japanese. A manual language choice is also
available for ambiguous lyrics.

Chinese and Cantonese fallback artwork has a 10% chance per track to use a hidden
Classical Chinese variant. The result stays the same through presence updates,
lyric reloads, and pause/resume; switching tracks allows a new roll. Classical
Chinese is an easter egg and is not listed in the language selector.

A placeholder also appears while artwork resolves in the background, then gives
way to the real cover. The language refreshes when lyrics arrive during playback,
even with lyric sharing disabled. Failed Litterbox uploads are remembered for the
session to avoid repeated attempts. The placeholder URLs work independently
of MusicBrainz and Litterbox and require no Discord application asset uploads.
