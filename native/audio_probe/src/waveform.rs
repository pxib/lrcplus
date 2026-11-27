use std::io::{self, Write};
use std::path::Path;

use crate::{media::Decoder, Result};

pub fn run(path: &Path, bucket_count: usize) -> Result<()> {
    let decoder = Decoder::open(path)?;
    let rate = decoder.sample_rate;
    let expected = decoder.frame_count_hint;
    let (mut rms, frames) = collect(decoder, expected, bucket_count)?;
    if frames == 0 {
        return Err("no audio frames decoded".into());
    }
    if frames != expected {
        // Container durations can be estimates or include codec padding.
        let decoder = Decoder::open(path)?;
        if decoder.sample_rate != rate {
            return Err("the source changed while generating the waveform".into());
        }
        let (peaks, actual) = collect(decoder, frames, bucket_count)?;
        if actual != frames {
            return Err("the source changed while generating the waveform".into());
        }
        rms = peaks;
    }
    let duration_ms = (u128::from(frames) * 1000 + u128::from(rate) / 2) / u128::from(rate);
    let mut out = io::BufWriter::new(io::stdout().lock());
    write!(out, "waveform-v1 {}", duration_ms.max(1))?;
    for peak in rms {
        write!(out, " {peak:.8}")?;
    }
    writeln!(out)?;
    out.flush()?;
    Ok(())
}

fn collect(mut decoder: Decoder, expected: u64, bucket_count: usize) -> Result<(Vec<f64>, u64)> {
    let channels = decoder.channels;
    let mut buckets = Buckets::new(expected, bucket_count);
    let mut total = 0;
    while let Some(packet) = decoder.next()? {
        buckets.push(packet, channels);
        total += (packet.len() / channels) as u64;
    }
    Ok((buckets.rms, total))
}

struct Buckets {
    total: u64,
    count: u64,
    frames: u64,
    energy: f64,
    rms: Vec<f64>,
}

impl Buckets {
    fn new(total: u64, count: usize) -> Self {
        let count = total.min(count as u64);
        Self {
            total,
            count,
            frames: 0,
            energy: 0.0,
            rms: Vec::with_capacity(count as usize),
        }
    }

    fn boundary(&self, index: usize) -> u64 {
        (index as u128 * u128::from(self.total) / u128::from(self.count)) as u64
    }

    fn push(&mut self, mut packet: &[f32], channels: usize) {
        while !packet.is_empty() && self.frames < self.total {
            let index = self.rms.len();
            let end = self.boundary(index + 1);
            let count = (end - self.frames).min((packet.len() / channels) as u64);
            let (samples, remaining) = packet.split_at(count as usize * channels);
            packet = remaining;
            for frame in samples.chunks_exact(channels) {
                let left = f64::from(frame[0]);
                let mono = frame
                    .get(1)
                    .map_or(left, |&right| (left + f64::from(right)) * 0.5);
                if mono.is_finite() {
                    self.energy += mono.clamp(-1.0, 1.0).powi(2);
                }
            }
            self.frames += count;
            if self.frames == end {
                let width = end - self.boundary(index);
                self.rms.push((self.energy / width as f64).sqrt());
                self.energy = 0.0;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Buckets;

    #[test]
    fn rms_buckets_split_packets_and_mix_stereo_to_mono() {
        let mut buckets = Buckets::new(5, 2);
        buckets.push(&[0.5, -0.5, 1.0, 1.0, 0.5, 0.5], 2);
        buckets.push(&[0.5, 0.5, -0.5, -0.5], 2);
        assert_eq!(buckets.rms, [(0.5_f64).sqrt(), 0.5]);
    }

    #[test]
    fn a_short_input_never_creates_empty_buckets() {
        let mut buckets = Buckets::new(2, 1400);
        buckets.push(&[0.5, -0.25, 1.0], 1);
        assert_eq!(buckets.rms, [0.5, 0.25]);
    }
}
