# Rust playback prototype

The command-line probe runs local audio through Symphonia decoding, LRCPlus's
windowed-sinc resampler, a bounded PCM queue, and CPAL device output. On Windows
the endpoint is the system default WASAPI device. The probe is built separately
from the app and uses its own command-line options. [Symphonia 0.5 guide](https://github.com/pdeljanov/Symphonia/blob/v0.5.5/GETTING_STARTED.md) · [CPAL](https://github.com/RustAudio/cpal)

## Run from the repository root

```powershell
cargo build --locked --release --manifest-path native/audio_probe/Cargo.toml --target-dir build/audio-probe
.\build\audio-probe\release\lrcplus-audio-probe.exe .\song.mp3 --speed 1.25 --seconds 15
```

Use the path to a local mono or stereo file. The probe supports common formats
including WAV, MP3, FLAC, Vorbis, and AAC or ALAC in M4A. `--speed` accepts
0.25–2.0 and changes pitch along with speed. `--seconds` limits the rendered
duration. Volume defaults to 0.25. Use `--volume 0` to exercise the device
silently, or `--decode-only` to run without an audio device.

The playback report shows rendered frames, callback count, and underrun frames.
Underrun frames count silence inserted when the device asked for audio before
the producer had supplied it. The queue holds up to a quarter second of PCM.

The prototype was built with the x64 Windows GNU Rust toolchain.
