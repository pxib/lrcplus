use std::f64::consts::PI;

use crate::resampler::Filter;

const PHASES: usize = 1024;

/// A constant-rate, band-limited resampler for interleaved floating-point PCM.
pub struct StreamingRate {
    rate: f64,
    channels: usize,
    radius: usize,
    weights: Vec<f64>,
    input: Vec<f64>,
    rendered: Vec<f64>,
    total_frames: usize,
    input_start: usize,
    next_output: usize,
    finished: bool,
}

impl StreamingRate {
    /// `rate` is the number of source frames per output frame.
    pub fn new(rate: f64, channels: usize) -> Result<Self, &'static str> {
        if !(1.0 / 128.0..=64.0).contains(&rate) || !(1..=8).contains(&channels) {
            return Err("resampling ratio or channel count is out of range");
        }
        let (radius, weights) = if rate == 1.0 {
            (0, Vec::new())
        } else {
            make_filter(rate)
        };
        Ok(Self {
            rate,
            channels,
            radius,
            weights,
            input: Vec::new(),
            rendered: Vec::new(),
            total_frames: 0,
            input_start: 0,
            next_output: 0,
            finished: false,
        })
    }

    /// Append output from a block of complete PCM frames.
    pub fn push(&mut self, input: &[f32], output: &mut Vec<f32>) {
        assert_eq!(input.len() % self.channels, 0);
        assert!(!self.finished, "PCM received after end of stream");
        if self.rate == 1.0 {
            output.extend_from_slice(input);
            return;
        }
        self.input
            .extend(input.iter().map(|&sample| f64::from(sample)));
        self.total_frames += input.len() / self.channels;
        self.render(self.total_frames.saturating_sub(self.radius), output);
    }

    /// Append the final lookahead once, extending the last sample at the edge.
    pub fn finish(&mut self, output: &mut Vec<f32>) {
        if !self.finished && self.rate != 1.0 {
            self.render(self.total_frames, output);
        }
        self.finished = true;
        self.input.clear();
    }

    fn render(&mut self, available: usize, output: &mut Vec<f32>) {
        let stop = (available as f64 / self.rate).ceil() as usize;
        let frames = stop.saturating_sub(self.next_output);
        if frames == 0 {
            return;
        }
        self.rendered.resize(frames * self.channels, 0.0);
        Filter::new(&self.weights, PHASES, self.radius, self.rate, 0.0).render(
            &self.input,
            self.input_start,
            self.next_output,
            &mut self.rendered,
            self.channels,
        );
        output.extend(self.rendered.iter().map(|&sample| sample as f32));
        self.next_output = stop;

        let next_frame = (stop as f64 * self.rate).floor() as usize;
        let keep_from = (next_frame + 1).saturating_sub(self.radius);
        let discard = keep_from
            .saturating_sub(self.input_start)
            .min(self.input.len() / self.channels - 1);
        self.input.drain(..discard * self.channels);
        self.input_start += discard;
    }
}

fn make_filter(rate: f64) -> (usize, Vec<f64>) {
    let cutoff = 0.94 / rate.max(1.0);
    let radius = (32.0 / cutoff).ceil() as usize;
    let mut weights = Vec::with_capacity((PHASES + 1) * 2 * radius);
    for phase in 0..=PHASES {
        let fraction = phase as f64 / PHASES as f64;
        let start = weights.len();
        for tap in 0..2 * radius {
            let distance = fraction - (tap as f64 + 1.0 - radius as f64);
            weights.push(cutoff * windowed_sinc(PI * cutoff * distance, distance / radius as f64));
        }
        let total: f64 = weights[start..].iter().sum();
        for coefficient in &mut weights[start..] {
            *coefficient /= total;
        }
    }
    (radius, weights)
}

pub(crate) fn windowed_sinc(argument: f64, window_position: f64) -> f64 {
    let angle = PI * window_position;
    let window = if window_position.abs() < 1.0 {
        0.42 + 0.5 * angle.cos() + 0.08 * (2.0 * angle).cos()
    } else {
        0.0
    };
    let sinc = if argument == 0.0 {
        1.0
    } else {
        argument.sin() / argument
    };
    sinc * window
}

#[cfg(test)]
mod tests {
    use super::StreamingRate;

    #[test]
    fn packet_boundaries_preserve_the_phase_and_tail() {
        let input: Vec<f32> = (0..2003).map(|i| (i as f32 * 0.03).sin()).collect();
        for rate in [0.73, 1.0, 1.25, 2.0] {
            let render = |chunk| {
                let mut stream = StreamingRate::new(rate, 1).unwrap();
                let mut output = Vec::new();
                for input in input.chunks(chunk) {
                    stream.push(input, &mut output);
                }
                stream.finish(&mut output);
                let length = output.len();
                stream.finish(&mut output);
                assert_eq!(length, (input.len() as f64 / rate).ceil() as usize);
                assert_eq!(length, output.len());
                output
            };
            assert_eq!(render(2003), render(127));
        }
    }

    #[test]
    fn downsampling_rejects_a_tone_above_nyquist() {
        let input: Vec<f32> = (0..2400)
            .map(|i| (std::f32::consts::TAU * 0.35 * i as f32).sin())
            .collect();
        let mut stream = StreamingRate::new(2.0, 1).unwrap();
        let mut output = Vec::new();
        stream.push(&input, &mut output);
        stream.finish(&mut output);
        assert!(output[100..1100].iter().all(|sample| sample.abs() < 1e-3));
    }
}
