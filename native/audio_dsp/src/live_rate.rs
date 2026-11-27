use std::f64::consts::PI;

use crate::streaming_rate::windowed_sinc;

const LOBES: f64 = 32.0;
const STEPS_PER_LOBE: f64 = 128.0;
const CUTOFF: f64 = 0.94;

/// A variable-rate resampler for mono or stereo PCM with a continuous source phase.
pub struct LiveRate {
    channels: usize,
    input: Vec<f32>,
    kernel: Vec<f32>,
    input_start: usize,
    total: usize,
    phase: f64,
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
                let index = ((tap as f64 - self.phase) * table_scale).abs();
                let left = index as usize;
                let blend = (index - left as f64) as f32;
                let weight = self.kernel[left] * (1.0 - blend) + self.kernel[left + 1] * blend;
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
    use super::LiveRate;

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
