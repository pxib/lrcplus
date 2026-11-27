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
the producer had supplied it. The standalone playback command queues up to
a quarter second of PCM.

The prototype was built with the x64 Windows GNU Rust toolchain.

## Try it in LRCPlus on Windows

After building the release probe, start the app from the repository root.

```powershell
$env:LRCPLUS_RUST_PLAYBACK = "1"
.\Scripts\python.exe main.py
```

Open a local track on the system default audio device. **Advanced → Audio
signal path** shows **Rust prototype (Symphonia and WASAPI)** when the probe
is active. The probe supplies the playback position for the seek bar and
lyrics. Use the app controls to pause, resume, and seek. The panel shows
how many output frames the probe filled with silence when PCM ran out,
counted from the latest track load or seek.

Enable **Link pitch to speed** to change speed while playing. The Rust stream
stays open as you drag, with up to 64 ms of queued PCM and a 25 ms rate ramp.
The seek bar and lyrics use the source position of the samples the device has
played through the ramp. Tracks that need independent pitch, equalization,
gapless or crossfade, a non-default audio device, PCM visualization, or gain
above 100% use the existing playback routes.

To compare memory and CPU with a regular launch, use the same track and
settings. Include both the Python app and `lrcplus-audio-probe.exe` in the
Rust run. Clear the flag before launching the baseline.

```powershell
Remove-Item Env:LRCPLUS_RUST_PLAYBACK
.\Scripts\python.exe main.py
```
