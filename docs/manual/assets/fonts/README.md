# Cantonese manual fonts

`CactusClassicalSerif-Regular.woff` supplies article and section headings.
`ChocolateClassicalSans-Regular.woff` supplies body text and navigation.

These are lossless WOFF 1 wrappers of the user's installed Regular TrueType
fonts. All font tables and glyphs are retained. CSS tries the installed family
first, then the bundled file, so the manual also works offline on computers
without either font installed. No Google Fonts requests are made.

Both families contain Regular only. Browsers synthesize bold and italic where
requested by the existing manual styles.

Upstream projects:

- [Cactus Classical Serif](https://github.com/MoonlitOwen/CactusSerif)
- [Chocolate Classical Sans](https://github.com/MoonlitOwen/ChocolateSans)

Both fonts use the SIL Open Font License 1.1; see `OFL.txt`. The Chocolate
installation carries a 2025 copyright notice; the Google Fonts distribution
also credits 2024. The upstream Cactus license spells its copyright project
name “Caactus”; that notice is reproduced as supplied.
