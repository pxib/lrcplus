# Discord Rich Presence

Open **Settings > Plugins > Discord Rich Presence > Settings** to customize the
activity. Changes take effect when you save; restarting the plugin is unnecessary.

- Choose Listening to, Playing, Watching, or Competing in.
- Edit the details, state, and artwork hover text. Leave a field empty to hide it.
- Show or hide playback progress and activity while paused or stopped.
- Control lyric sharing, lines between lyric updates, and artwork sources.
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
