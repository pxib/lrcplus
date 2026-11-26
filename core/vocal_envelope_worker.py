"""Extract a 50 Hz vocal energy envelope in an isolated Python process."""

import os
import subprocess
import sys


def main(ffmpeg, audio, output):
    site = os.environ.get("LRCPLUS_DEPENDENCY_SITE")
    if site and site not in sys.path:
        sys.path.append(site)
    try:
        import numpy as np
        import torch
        from demucs.apply import apply_model
        from demucs.hf import load_safetensors_model
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import LocalEntryNotFoundError
    except ImportError:
        return 2

    model_args = ("adefossez/HTDemucs", "955717e8.safetensors")
    try:
        weights = hf_hub_download(*model_args, local_files_only=True)
    except LocalEntryNotFoundError:
        try:
            weights = hf_hub_download(*model_args)
        except Exception as error:
            raise ValueError("Could not download the Demucs vocal model. Check your connection and try again.") from error

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    model = load_safetensors_model(weights).eval()
    rate = model.samplerate
    # Bound the decoded buffer and the model's output for unusually long tracks.
    decoded = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", audio,
         "-map", "0:a:0", "-t", "601", "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if decoded.returncode:
        raise ValueError("FFmpeg could not decode the loaded track.")
    signal = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, 2)
    if len(signal) > 600 * rate:
        raise ValueError("Vocal analysis supports tracks up to 10 minutes.")
    if len(signal) < 10 * rate:
        raise ValueError("The loaded track is too short for vocal timing analysis.")
    waveform = torch.from_numpy(signal.T.copy())
    del signal, decoded
    mono = waveform.mean(dim=0)
    deviation = mono.std()
    if float(deviation) < .00001:
        raise ValueError("There is too little audio to identify vocal timing.")
    waveform.sub_(mono.mean()).div_(deviation)
    del mono
    with torch.inference_mode():
        sources = apply_model(model, waveform[None], shifts=0, split=True, overlap=.25, device="cpu")
    vocals = sources[0, model.sources.index("vocals")].mean(dim=0).numpy()
    frame = rate // 50
    blocks = vocals[:len(vocals) // frame * frame].reshape(-1, frame)
    np.save(output, np.sqrt(np.mean(blocks ** 2, axis=1)), allow_pickle=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(*sys.argv[1:]))
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
