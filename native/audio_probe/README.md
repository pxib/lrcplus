# Rust waveform and playback helper

The command-line probe runs local audio through Symphonia decoding, LRCPlus's
windowed-sinc resampler, a bounded PCM queue, and CPAL device output. On Windows
the endpoint is the system default WASAPI device. The Windows release bundles
the helper. [Symphonia 0.5 guide](https://github.com/pdeljanov/Symphonia/blob/v0.5.5/GETTING_STARTED.md) · [CPAL](https://github.com/RustAudio/cpal)

## Run from the repository root

```powershell
cargo build --locked --release --manifest-path native/audio_probe/Cargo.toml --target-dir build/audio-probe
.\build\audio-probe\release\lrcplus-audio-probe.exe .\song.mp3 --speed 1.25 --seconds 15
.\build\audio-probe\release\lrcplus-audio-probe.exe .\song.mp3 --waveform 1400
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

The player and editor use the helper for local waveforms when it is available.
The waveform command returns decoded duration and mono RMS buckets computed
at the source sample rate. It reads audio in packets and uses a second decoding
pass if the container's frame count is missing or inaccurate. The app falls
back to FFmpeg for unsupported files and source builds without the helper.

The prototype was built with the x64 Windows GNU Rust toolchain.

## Try it in LRCPlus on Windows

After building the release probe, start the app from the repository root.

```powershell
.\Scripts\python.exe main.py
```

For a local track on the system default audio device, Rust is selected at
track load when the speed is off 100% and pitch preservation is on, or when
an independent pitch shift is set. Qt is selected at track load for neutral
playback and for speed changes with pitch linked. Changes on an active Rust
track can use the same transport while it remains compatible. **Advanced →
Audio signal path** shows **Rust (Symphonia and WASAPI)** when the probe is
active. The probe supplies the playback position for the seek bar and lyrics.
The panel counts underrun frames from the latest track load or seek.

The Rust stream stays open as you drag the speed slider. It queues up to
64 ms of PCM. With **Link pitch to speed** on, it ramps the resampling rate
over 25 ms. The seek bar and lyrics follow the audio device's playback clock.

Turn **Link pitch to speed** off to keep the song's pitch steady while
changing speed or to adjust pitch separately. This mode uses the Rubber Band
R3 time stretcher from the installed Windows aubio package. Its processing
adds a short delay to control changes, and toggling the link rebuilds the
Rust stream at the current position. If the DLL is unavailable, independent
pitch uses the existing playback route. [Rubber Band integration notes](https://breakfastquay.com/rubberband/integration.html)

The transport applies manual volume and ReplayGain to the output stream. It
clips boosted PCM to the supported sample range. Tracks that need equalization,
gapless or crossfade, a non-default audio device, or PCM visualization use the
existing playback routes.

Set `LRCPLUS_RUST_PLAYBACK=1` to prefer Rust for all compatible local
tracks, or `LRCPLUS_RUST_PLAYBACK=0` to use the Qt baseline. Include both
the Python app and `lrcplus-audio-probe.exe` in Rust memory and CPU
measurements.

```powershell
$env:LRCPLUS_RUST_PLAYBACK = "0"
.\Scripts\python.exe main.py
```
