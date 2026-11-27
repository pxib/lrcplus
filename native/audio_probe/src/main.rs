mod media;
mod output;

use std::error::Error;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Instant;

use lrcplus_audio_dsp_v1::StreamingRate;

const USAGE: &str = "Usage: lrcplus-audio-probe <file> [--speed 0.25..2] [--volume 0..1] \
[--seconds N] [--decode-only]\n\
Speed changes pitch. Volume defaults to 0.25. Seconds limits the output duration.";

type Result<T> = std::result::Result<T, Box<dyn Error>>;

struct Options {
    path: PathBuf,
    speed: f64,
    volume: f32,
    seconds: Option<f64>,
    decode_only: bool,
}

impl Options {
    fn parse() -> Result<Option<Self>> {
        let mut args = std::env::args_os().skip(1);
        let path = args.next().ok_or(USAGE)?;
        if path == "--help" || path == "-h" {
            println!("{USAGE}");
            return Ok(None);
        }
        let mut options = Self {
            path: path.into(),
            speed: 1.0,
            volume: 0.25,
            seconds: None,
            decode_only: false,
        };
        while let Some(flag) = args.next() {
            let flag = flag.to_str().ok_or("invalid option")?;
            if flag == "--decode-only" {
                options.decode_only = true;
                continue;
            }
            if !matches!(flag, "--speed" | "--volume" | "--seconds") {
                return Err(format!("unknown option {flag}\n{USAGE}").into());
            }
            let value: f64 = args
                .next()
                .and_then(|value| value.to_str()?.parse().ok())
                .ok_or_else(|| format!("expected a number after {flag}"))?;
            match flag {
                "--speed" if (0.25..=2.0).contains(&value) => options.speed = value,
                "--volume" if (0.0..=1.0).contains(&value) => options.volume = value as f32,
                "--seconds" if value.is_finite() && value > 0.0 => options.seconds = Some(value),
                _ => return Err(format!("value out of range for {flag}\n{USAGE}").into()),
            }
        }
        Ok(Some(options))
    }
}

fn main() -> ExitCode {
    match Options::parse().and_then(|options| options.map_or(Ok(()), run)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run(options: Options) -> Result<()> {
    let started = Instant::now();
    let mut decoder = media::Decoder::open(&options.path)?;
    let mut device = if options.decode_only {
        None
    } else {
        Some(output::Device::open()?)
    };
    let (output_rate, output_channels) = device
        .as_ref()
        .map_or((decoder.sample_rate, decoder.channels), |device| {
            (device.sample_rate, device.channels)
        });
    let output_limit = options.seconds.map_or(usize::MAX, |seconds| {
        (seconds * f64::from(output_rate)).round() as usize
    });
    if output_limit == 0 {
        return Err("requested duration is shorter than one output frame".into());
    }

    let ratio = f64::from(decoder.sample_rate) * options.speed / f64::from(output_rate);
    let mut resampler = StreamingRate::new(ratio, decoder.channels)?;
    println!(
        "Source: {} Hz, {} channels",
        decoder.sample_rate, decoder.channels
    );
    println!(
        "Output: {output_rate} Hz, {output_channels} channels, {:.2}x, volume {:.2}",
        options.speed, options.volume
    );
    if let Some(device) = &device {
        println!("Device: {}", device.name);
    }
    let input_channels = decoder.channels;
    let mut decoded_frames = 0;
    let mut rendered = Vec::new();
    let mut mixed = Vec::new();
    let mut produced = 0;
    let mut peak = 0.0_f32;
    let mut deliver = |pcm: &[f32]| -> Result<bool> {
        let frames = (pcm.len() / input_channels).min(output_limit - produced);
        mix(
            &pcm[..frames * input_channels],
            input_channels,
            output_channels,
            options.volume,
            &mut mixed,
        );
        peak = mixed
            .iter()
            .fold(peak, |peak, sample| peak.max(sample.abs()));
        if let Some(device) = &mut device {
            device.push(&mixed)?;
        }
        produced += frames;
        Ok(produced == output_limit)
    };

    'decode: while let Some(packet) = decoder.next()? {
        // Bound the output of a single resampling pass even for large codec packets.
        for block in packet.chunks(1024 * input_channels) {
            decoded_frames += block.len() / input_channels;
            rendered.clear();
            resampler.push(block, &mut rendered);
            if deliver(&rendered)? {
                break 'decode;
            }
        }
    }
    rendered.clear();
    resampler.finish(&mut rendered);
    deliver(&rendered)?;
    if produced == 0 {
        return Err("no audio frames decoded".into());
    }
    if let Some(device) = &mut device {
        let metrics = device.finish(produced)?;
        println!(
            "Device callbacks: {}, underrun frames: {}",
            metrics.callbacks, metrics.underrun_frames
        );
    }
    println!("Decoded {decoded_frames} frames, rendered {produced} frames ({:.3}s), peak {peak:.3}, elapsed {:.3}s",
             produced as f64 / f64::from(output_rate), started.elapsed().as_secs_f64());
    Ok(())
}

fn mix(
    input: &[f32],
    input_channels: usize,
    output_channels: usize,
    volume: f32,
    output: &mut Vec<f32>,
) {
    output.clear();
    let level = |sample: f32| {
        if sample.is_finite() {
            (sample * volume).clamp(-1.0, 1.0)
        } else {
            0.0
        }
    };
    for frame in input.chunks_exact(input_channels) {
        let left = frame[0];
        let right = frame.get(1).copied().unwrap_or(left);
        if output_channels == 1 {
            output.push(level(0.5 * left + 0.5 * right));
        } else {
            output.extend([level(left), level(right)]);
            output.resize(output.len() + output_channels - 2, 0.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mix;

    #[test]
    fn mixes_mono_and_stereo_for_the_endpoint() {
        let mut output = Vec::new();
        mix(&[0.5, -0.5, 0.25, 0.75], 2, 1, 1.0, &mut output);
        assert_eq!(output, [0.0, 0.5]);
        mix(&[0.5, f32::NAN], 1, 4, 0.5, &mut output);
        assert_eq!(output, [0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
    }
}
