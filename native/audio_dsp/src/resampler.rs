//! Convolve interleaved PCM with the windowed-sinc phases prepared by Python.

use std::slice;

/// # Safety
/// The caller must supply aligned `f64` buffers with `frame_count * channels`
/// input samples, `(phases + 1) * 2 * radius` coefficients, and space for
/// `output_count * channels` output samples. The output must not alias either
/// input. Counts, rate, and positions come from the streaming processor.
#[no_mangle]
pub unsafe extern "C" fn lrcplus_resample_v2(
    frames: *const f64,
    frame_count: usize,
    channels: usize,
    weights: *const f64,
    phases: usize,
    radius: usize,
    rate: f64,
    input_start: usize,
    origin: f64,
    output_start: usize,
    output_count: usize,
    output: *mut f64,
) {
    let taps = 2 * radius;
    let frames = unsafe { slice::from_raw_parts(frames, frame_count * channels) };
    let weights = unsafe { slice::from_raw_parts(weights, (phases + 1) * taps) };
    let output = unsafe { slice::from_raw_parts_mut(output, output_count * channels) };
    Filter::new(weights, phases, radius, rate, origin).render(
        frames,
        input_start,
        output_start,
        output,
        channels,
    );
}

pub(crate) struct Filter<'a> {
    weights: &'a [f64],
    phases: usize,
    radius: usize,
    rate: f64,
    origin: f64,
}

impl<'a> Filter<'a> {
    pub(crate) fn new(
        weights: &'a [f64],
        phases: usize,
        radius: usize,
        rate: f64,
        origin: f64,
    ) -> Self {
        Self {
            weights,
            phases,
            radius,
            rate,
            origin,
        }
    }

    pub(crate) fn render(
        &self,
        frames: &[f64],
        input_start: usize,
        output_start: usize,
        output: &mut [f64],
        channels: usize,
    ) {
        match channels {
            1 => self.render_channels::<1>(frames, input_start, output_start, output, channels),
            2 => self.render_channels::<2>(frames, input_start, output_start, output, channels),
            _ => self.render_channels::<0>(frames, input_start, output_start, output, channels),
        }
    }

    fn render_channels<const CHANNELS: usize>(
        &self,
        frames: &[f64],
        input_start: usize,
        output_start: usize,
        output: &mut [f64],
        channels: usize,
    ) {
        // Specialize the mono and stereo loops without changing the math for
        // other channel layouts.
        let channels = if CHANNELS == 0 { channels } else { CHANNELS };
        let taps = 2 * self.radius;
        let frame_count = frames.len() / channels;
        for (index, sample) in output.chunks_exact_mut(channels).enumerate() {
            let position = self.origin + (output_start + index) as f64 * self.rate;
            let integer = position.floor();
            let phase = (position - integer) * self.phases as f64;
            let left = phase as usize;
            let blend = phase - left as f64;
            let rows = &self.weights[left * taps..(left + 2) * taps];
            let (low, high) = rows.split_at(taps);
            let coefficients = low
                .iter()
                .zip(high)
                .map(|(a, b)| a * (1.0 - blend) + b * blend);

            let first = integer as isize - input_start as isize + 1 - self.radius as isize;
            sample.fill(0.0);
            if first >= 0 && first as usize + taps <= frame_count {
                let start = first as usize * channels;
                let window = &frames[start..start + taps * channels];
                for (frame, weight) in window.chunks_exact(channels).zip(coefficients) {
                    for (sum, value) in sample.iter_mut().zip(frame) {
                        *sum += weight * value;
                    }
                }
            } else {
                // The Python processor only reaches this path at a stream edge.
                for (tap, weight) in coefficients.enumerate() {
                    let frame = (first + tap as isize).clamp(0, frame_count as isize - 1) as usize;
                    let start = frame * channels;
                    for (sum, value) in sample.iter_mut().zip(&frames[start..start + channels]) {
                        *sum += weight * value;
                    }
                }
            }
        }
    }
}
