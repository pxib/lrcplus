use std::f64::consts::PI;

use crate::streaming_rate::windowed_sinc;

const LOBES: f64 = 32.0;
const STEPS_PER_LOBE: f64 = 128.0;
const CUTOFF: f64 = 0.94;
const MAX_PERIOD: usize = 256;
const MAX_FIXED_WEIGHTS: usize = 65_536;

enum SteadyRate {
    Pending,
    Fixed(FixedRate),
    Variable,
}

struct FixedRate {
    phases: Vec<FixedPhase>,
    base: usize,
    advance: usize,
    index: usize,
}

struct FixedPhase {
    first: isize,
    end: f64,
    weights: Vec<f32>,
}

impl FixedRate {
    fn new(ratio: f64, phase: f64, kernel: &[f32]) -> Option<Self> {
        // Integer source and device rates often repeat a short sequence of
        // fractions. Skip long periods so a control change cannot build a large filter.
        let period = (1..=MAX_PERIOD).find(|&steps| {
            let frames = (ratio * steps as f64).round();
            frames > 0.0 && (frames / steps as f64 - ratio).abs() <= ratio * f64::EPSILON
        })?;
        let advance = (ratio * period as f64).round() as usize;
        let cutoff = CUTOFF / ratio.max(1.0);
        let radius = LOBES / cutoff;
        if (2.0 * radius).ceil() as usize * period > MAX_FIXED_WEIGHTS {
            return None;
        }
        let scale = cutoff * STEPS_PER_LOBE;
        let position = |index| phase.fract() + (index * advance) as f64 / period as f64;
        let phases = (0..period)
            .map(|index| {
                let center = position(index);
                let first = (center - radius).ceil() as isize;
                let last = (center + radius).floor() as isize;
                let mut weights: Vec<_> = (first..=last)
                    .map(|tap| kernel_weight(kernel, tap as f64 - center, scale))
                    .collect();
                let total: f32 = weights.iter().sum();
                for weight in &mut weights {
                    *weight /= total;
                }
                FixedPhase {
                    first,
                    end: position(index + 1),
                    weights,
                }
            })
            .collect();
        Some(Self {
            phases,
            base: phase.floor() as usize,
            advance,
            index: 0,
        })
    }
}

fn kernel_weight(kernel: &[f32], distance: f64, scale: f64) -> f32 {
    let index = (distance * scale).abs();
    let left = index as usize;
    let blend = (index - left as f64) as f32;
    kernel[left] * (1.0 - blend) + kernel[left + 1] * blend
}

/// A variable-rate resampler for mono or stereo PCM with a continuous source phase.
pub struct LiveRate {
    channels: usize,
    input: Vec<f32>,
    kernel: Vec<f32>,
    input_start: usize,
    total: usize,
    phase: f64,
    steady: SteadyRate,
    ratio: f64,
    target: f64,
    step: f64,
    ramp_frames: usize,
    remaining: usize,
    history: usize,
    max_ratio: f64,
    eof: bool,
}

impl LiveRate {
    pub fn new(
        ratio: f64,
        max_ratio: f64,
        channels: usize,
        ramp_frames: usize,
    ) -> Result<Self, &'static str> {
        if !(1.0 / 128.0..=64.0).contains(&max_ratio)
            || !(1.0 / 128.0..=max_ratio).contains(&ratio)
            || !(1..=2).contains(&channels)
            || ramp_frames == 0
        {
            return Err("live resampling ratio, channel count, or ramp length is out of range");
        }
        // A single table covers every ratio. Scaling tap distances narrows the
        // low-pass bandwidth as the ratio increases, without rebuilding filters.
        let kernel = (0..=(LOBES * STEPS_PER_LOBE) as usize)
            .map(|i| {
                let distance = i as f64 / STEPS_PER_LOBE;
                windowed_sinc(PI * distance, distance / LOBES) as f32
            })
            .chain([0.0])
            .collect();
        Ok(Self {
            channels,
            input: Vec::new(),
            kernel,
            input_start: 0,
            total: 0,
            phase: 0.0,
            steady: SteadyRate::Pending,
            ratio,
            target: ratio,
            step: 0.0,
            ramp_frames,
            remaining: 0,
            history: (LOBES * max_ratio.max(1.0) / CUTOFF).ceil() as usize,
            max_ratio,
            eof: false,
        })
    }

    pub fn set_ratio(&mut self, ratio: f64) -> Result<(), &'static str> {
        if !(1.0 / 128.0..=self.max_ratio).contains(&ratio) {
            return Err("live resampling ratio is out of range");
        }
        if ratio != self.target {
            self.target = ratio;
            self.steady = SteadyRate::Pending;
            if self.phase == 0.0 {
                self.ratio = ratio;
            } else {
                self.remaining = self.ramp_frames;
                self.step = (ratio - self.ratio) / self.remaining as f64;
            }
        }
        Ok(())
    }

    pub fn push(&mut self, input: &[f32]) {
        assert_eq!(input.len() % self.channels, 0);
        self.input.extend_from_slice(input);
        self.total += input.len() / self.channels;
    }

    pub fn finish(&mut self) {
        self.eof = true;
    }

    pub fn drained(&self) -> bool {
        self.eof && self.phase >= self.total as f64
    }

    /// Append PCM and the source phase at the end of each output frame.
    pub fn render(&mut self, limit: usize, pcm: &mut Vec<f32>, positions: &mut Vec<f64>) {
        if self.channels == 1 {
            self.render_channels::<1>(limit, pcm, positions);
        } else {
            self.render_channels::<2>(limit, pcm, positions);
        }
        // Keep the history needed even if the next speed command widens the filter.
        let keep_from = (self.phase.floor() as usize).saturating_sub(self.history);
        let discard = keep_from.min(self.total).saturating_sub(self.input_start);
        self.input.drain(..discard * self.channels);
        self.input_start += discard;
    }

    fn render_channels<const CHANNELS: usize>(
        &mut self,
        limit: usize,
        pcm: &mut Vec<f32>,
        positions: &mut Vec<f64>,
    ) {
        let ramp = limit.min(self.remaining);
        if ramp != 0 {
            self.render_variable::<CHANNELS>(ramp, pcm, positions);
            if self.remaining != 0 {
                return;
            }
        }
        let limit = limit - ramp;
        if limit == 0 || self.phase >= self.total as f64 {
            return;
        }
        if self.ratio == 1.0 && self.phase.fract() == 0.0 {
            let start = self.phase as usize;
            let end = start + limit.min(self.total - start);
            pcm.extend_from_slice(
                &self.input
                    [(start - self.input_start) * CHANNELS..(end - self.input_start) * CHANNELS],
            );
            positions.extend((start + 1..=end).map(|frame| frame as f64));
            self.phase = end as f64;
            return;
        }
        if matches!(self.steady, SteadyRate::Pending) {
            self.steady = FixedRate::new(self.ratio, self.phase, &self.kernel)
                .map_or(SteadyRate::Variable, SteadyRate::Fixed);
        }
        if let SteadyRate::Fixed(fixed) = &mut self.steady {
            for _ in 0..limit {
                let phase = &fixed.phases[fixed.index];
                let first = fixed.base as isize + phase.first;
                let end = first + phase.weights.len() as isize;
                if self.phase >= self.total as f64 || (!self.eof && end > self.total as isize) {
                    break;
                }
                let mut sums = [0.0; CHANNELS];
                if first >= 0 && end <= self.total as isize {
                    let start = (first as usize - self.input_start) * CHANNELS;
                    let window = &self.input[start..start + phase.weights.len() * CHANNELS];
                    for (frame, &weight) in window.chunks_exact(CHANNELS).zip(&phase.weights) {
                        for (sum, &sample) in sums.iter_mut().zip(frame) {
                            *sum += sample * weight;
                        }
                    }
                } else {
                    for (tap, &weight) in phase.weights.iter().enumerate() {
                        let frame = (first + tap as isize).clamp(0, self.total as isize - 1);
                        let start = (frame as usize - self.input_start) * CHANNELS;
                        for (sum, &sample) in
                            sums.iter_mut().zip(&self.input[start..start + CHANNELS])
                        {
                            *sum += sample * weight;
                        }
                    }
                }
                pcm.extend(sums);
                self.phase = fixed.base as f64 + phase.end;
                positions.push(self.phase.min(self.total as f64));
                fixed.index += 1;
                if fixed.index == fixed.phases.len() {
                    fixed.index = 0;
                    fixed.base += fixed.advance;
                }
            }
        } else {
            self.render_variable::<CHANNELS>(limit, pcm, positions);
        }
    }

    fn render_variable<const CHANNELS: usize>(
        &mut self,
        limit: usize,
        pcm: &mut Vec<f32>,
        positions: &mut Vec<f64>,
    ) {
        for _ in 0..limit {
            let cutoff = CUTOFF / self.ratio.max(1.0);
            let radius = LOBES / cutoff;
            let first = (self.phase - radius).ceil() as isize;
            let last = (self.phase + radius).floor() as isize;
            if self.phase >= self.total as f64 || (!self.eof && last >= self.total as isize) {
                break;
            }
            let table_scale = cutoff * STEPS_PER_LOBE;
            let mut samples = [0.0; CHANNELS];
            let mut weight_sum = 0.0;
            for tap in first..=last {
                let weight = kernel_weight(&self.kernel, tap as f64 - self.phase, table_scale);
                let frame =
                    (tap.clamp(0, self.total as isize - 1) as usize - self.input_start) * CHANNELS;
                for (sample, &source) in
                    samples.iter_mut().zip(&self.input[frame..frame + CHANNELS])
                {
                    *sample += source * weight;
                }
                weight_sum += weight;
            }
            pcm.extend(samples.map(|sample| sample / weight_sum));
            self.phase += self.ratio;
            positions.push(self.phase.min(self.total as f64));
            if self.remaining != 0 {
                self.remaining -= 1;
                self.ratio = if self.remaining == 0 {
                    self.target
                } else {
                    self.ratio + self.step
                };
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{LiveRate, SteadyRate};

    #[test]
    fn unity_copies_samples_without_lookahead() {
        let input = [0.1, 0.7, -0.4, 0.2, 0.9, -0.8];
        let mut rate = LiveRate::new(1.0, 2.0, 2, 100).unwrap();
        let (mut pcm, mut positions) = (Vec::new(), Vec::new());
        rate.push(&input[..2]);
        rate.render(100, &mut pcm, &mut positions);
        assert_eq!(pcm, input[..2]);
        rate.push(&input[2..]);
        rate.finish();
        rate.render(100, &mut pcm, &mut positions);
        assert_eq!(pcm, input);
        assert_eq!(positions, [1.0, 2.0, 3.0]);
        assert!(rate.drained());
    }

    #[test]
    fn fixed_conversion_matches_the_variable_filter_and_finishes_at_the_source_end() {
        let frames = 4093_usize;
        let input: Vec<f32> = (0..frames)
            .flat_map(|i| [(i as f32 * 0.17).sin(), (i as f32 * 0.9).cos()])
            .collect();
        for (advance, period) in [(147, 160), (147, 128), (160, 147)] {
            let render = |mut rate: LiveRate| {
                let (mut pcm, mut positions) = (Vec::new(), Vec::new());
                for chunk in input.chunks(74) {
                    rate.push(chunk);
                    rate.render(1024, &mut pcm, &mut positions);
                }
                rate.finish();
                rate.render(1024, &mut pcm, &mut positions);
                (rate, pcm, positions)
            };
            let ratio = advance as f64 / period as f64;
            let (fixed, pcm, positions) = render(LiveRate::new(ratio, 2.0, 2, 100).unwrap());
            let mut variable = LiveRate::new(ratio, 2.0, 2, 100).unwrap();
            variable.steady = SteadyRate::Variable;
            let (_, reference, reference_positions) = render(variable);
            assert!(matches!(fixed.steady, SteadyRate::Fixed(_)));
            assert_eq!(pcm.len(), reference.len());
            assert_eq!(positions.len(), (frames * period).div_ceil(advance));
            assert_eq!(positions.last(), Some(&(frames as f64)));
            assert!(positions.windows(2).all(|pair| pair[0] < pair[1]));
            for (cached, original) in positions.iter().zip(reference_positions) {
                assert!((cached - original).abs() < 1e-8);
            }
            for (cached, original) in pcm.iter().zip(reference) {
                assert!((cached - original).abs() < 2e-6);
            }
        }
    }

    #[test]
    fn ramp_back_to_unity_keeps_the_fractional_phase() {
        let mut rate = LiveRate::new(1.0, 2.0, 1, 4).unwrap();
        rate.push(&[0.8; 256]);
        let (mut pcm, mut positions) = (Vec::new(), Vec::new());
        rate.render(3, &mut pcm, &mut positions);
        rate.set_ratio(1.25).unwrap();
        rate.render(7, &mut pcm, &mut positions);
        rate.set_ratio(1.0).unwrap();
        rate.render(8, &mut pcm, &mut positions);
        assert!(matches!(rate.steady, SteadyRate::Fixed(_)));
        assert_eq!(&positions[13..], &[15.75, 16.75, 17.75, 18.75, 19.75]);
        assert!(pcm.iter().all(|sample| (sample - 0.8).abs() < 1e-6));
    }

    #[test]
    fn retargeting_preserves_phase_and_ramps_from_the_current_ratio() {
        let mut rate = LiveRate::new(1.0, 2.0, 1, 4).unwrap();
        rate.push(&[0.5; 256]);
        let mut pcm = Vec::new();
        let mut positions = Vec::new();
        rate.render(3, &mut pcm, &mut positions);
        rate.set_ratio(1.5).unwrap();
        rate.render(2, &mut pcm, &mut positions);
        rate.set_ratio(0.75).unwrap();
        rate.render(5, &mut pcm, &mut positions);

        assert_eq!(&positions[..5], &[1.0, 2.0, 3.0, 4.0, 5.125]);
        let steps: Vec<_> = positions[4..].windows(2).map(|p| p[1] - p[0]).collect();
        assert_eq!(steps, [1.25, 1.125, 1.0, 0.875, 0.75]);
        assert!(pcm.iter().all(|sample| (sample - 0.5).abs() < 1e-6));
    }

    #[test]
    fn changing_rate_preserves_pcm_across_packet_boundaries() {
        let audio: Vec<f32> = (0..4093).map(|i| (i as f32 * 0.04).sin()).collect();
        let render = |chunk| {
            let mut rate = LiveRate::new(0.75, 2.0, 1, 100).unwrap();
            let mut pcm = Vec::new();
            let mut positions = Vec::new();
            let mut remaining = audio.chunks(chunk);
            for (frame, speed) in [(199, 1.5), (327, 0.75), (752, 2.0)] {
                while positions.len() < frame {
                    rate.render(frame - positions.len(), &mut pcm, &mut positions);
                    if positions.len() < frame {
                        rate.push(remaining.next().unwrap());
                    }
                }
                rate.set_ratio(speed).unwrap();
            }
            for input in remaining {
                rate.push(input);
            }
            rate.finish();
            rate.render(10_000, &mut pcm, &mut positions);
            assert!(rate.drained());
            assert_eq!(positions.last(), Some(&(audio.len() as f64)));
            assert!(positions.windows(2).all(|pair| pair[0] < pair[1]));
            for (&sample, &phase) in pcm.iter().skip(1).zip(&positions) {
                if (100.0..(audio.len() as f64 - 100.0)).contains(&phase) {
                    assert!((sample - (phase as f32 * 0.04).sin()).abs() < 2e-4);
                }
            }
            (pcm, positions)
        };
        assert_eq!(render(77), render(1024));
    }

    #[test]
    fn downsampling_filters_frequencies_above_nyquist() {
        let input: Vec<f32> = (0..2400)
            .map(|i| (std::f32::consts::TAU * 0.35 * i as f32).sin())
            .collect();
        let mut rate = LiveRate::new(2.0, 2.0, 1, 32).unwrap();
        rate.push(&input);
        let mut pcm = Vec::new();
        rate.render(1100, &mut pcm, &mut Vec::new());
        assert!(pcm[100..].iter().all(|sample| sample.abs() < 1e-3));
    }
}
