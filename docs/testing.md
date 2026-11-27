Run the routine developer suite with:

```powershell
python -m pytest -q
```

Rendering tests marked `render_matrix` retain every parameter value and every
pair of parameter values. Their selection is deterministic. Tests marked
`exhaustive` cover expensive full encounter simulations and additional real pip
install scenarios. The routine suite still exercises a real dependency
installation, shared dependency removal, and import checks.

Run every original rendering combination and all expensive checks with:

```powershell
python -m pytest -q --exhaustive
```

CI uses the exhaustive command for pull requests and pushes to `release`.
Superseded runs on the same ref are cancelled. Feature branches with a pull
request are tested by the pull request event.

For a focused change, pass a test file or node ID. A directly selected rendering
case runs even when it is outside the routine matrix. Directly selecting a test
marked `exhaustive` also requires `--exhaustive`.

```powershell
python -m pytest -q tests/test_queue.py
python -m pytest -q --exhaustive tests/test_dependencies.py
```

The shared test fixtures flush Qt's deferred deletion events after every test.
GUI fixtures must still close their windows, stop their workers, and call
`deleteLater()`; `processEvents()` alone does not deliver deferred deletions when
no Qt event loop is running. This prevents retained UI trees from slowing later
tests without reusing application state between cases.

## Windows release ZIP

Install a Rust toolchain for the build's Windows architecture and put `cargo`
on PATH. Run these commands from the repository root:

```powershell
python -m pip install -r requirements.txt pyinstaller
python tools/build_release.py
python tools/smoke_release.py
```

The smoke check extracts the ZIP for the current app version and architecture,
launches `LRCPlus.exe` with a temporary profile, and verifies WAV decoding,
seeking, the Rust PCM engine, speed resampling, melody detection, and an LRCX
save and reopen. It decodes the WAV separately with Qt's `QAudioDecoder` and checks the player's
transport without sending sound to an output device. On a host with an audio
endpoint it checks play and pause. On a host without one it verifies that
LRCPlus pauses a play attempt. Windows CI runs it alongside the exhaustive
source suite.

To exercise the Rust audio library from a source checkout, build it before
running the PCM, resampling, and melody tests:

```powershell
cargo build --locked --release --manifest-path native/audio_dsp/Cargo.toml --target-dir build/audio-dsp
python -m pytest tests/test_pcm_buffers.py tests/test_pcm_rate.py tests/test_rhythm_melody.py
```
