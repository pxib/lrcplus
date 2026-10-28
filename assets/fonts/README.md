# Library font

`NotoSansJP-Regular.otf` is the unmodified Japanese regional subset from
[Noto Sans CJK 2.004](https://github.com/notofonts/noto-cjk/tree/Sans2.004).
It includes Japanese glyphs, Vietnamese Latin characters, and combining accents.

Source:
https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf

The font is distributed under the accompanying upstream SIL Open Font License
in `LICENSE`. Qt registers it for this application only. The existing PyInstaller
configuration includes the whole `assets` directory, including this font and
its license.
