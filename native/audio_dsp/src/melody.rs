//! Select distinct pitches from batches of FFT magnitudes.

use std::slice;

/// # Safety
/// The caller must supply aligned, disjoint buffers of `rows * bins` input
/// magnitudes and `rows * notes` output strengths. The inclusive search band
/// must have one neighboring bin on either side.
#[no_mangle]
pub unsafe extern "C" fn lrcplus_melody_peaks_v1(
    spectra: *const f32,
    rows: usize,
    bins: usize,
    first_bin: usize,
    last_bin: usize,
    bin_hz: f64,
    first_note: i32,
    notes: usize,
    pitches: *mut f32,
) {
    let spectra = unsafe { slice::from_raw_parts(spectra, rows * bins) };
    let pitches = unsafe { slice::from_raw_parts_mut(pitches, rows * notes) };
    let mut fundamentals = Vec::new();
    for (spectrum, pitches) in spectra.chunks_exact(bins).zip(pitches.chunks_exact_mut(notes)) {
        let band = &spectrum[first_bin..=last_bin];
        // A NaN in the band makes NumPy's row maximum NaN and suppresses the row.
        if band.iter().any(|magnitude| magnitude.is_nan()) {
            continue;
        }
        let floor = (0.055 * band.iter().copied().fold(0.0, f32::max)).max(0.0015);
        fundamentals.clear();
        for (index, neighbors) in spectrum[first_bin - 1..=last_bin + 1].windows(3).enumerate() {
            let magnitude = neighbors[1];
            // An equal right neighbor belongs to this peak, an equal left one doesn't.
            if !(magnitude >= floor && magnitude > neighbors[0] && magnitude >= neighbors[2]) {
                continue;
            }
            let [left, peak, right] = [neighbors[0], magnitude, neighbors[2]]
                .map(|value| value.max(1e-12).ln());
            let curvature = f64::from(left - 2.0 * peak + right);
            let offset = if curvature < 0.0 {
                0.5 * f64::from(left - right) / curvature
            } else {
                0.0
            };
            let frequency = ((first_bin + index) as f64 + offset) * bin_hz;
            if fundamentals.iter().any(|&(base, strength)| {
                magnitude <= strength && is_harmonic(frequency, base)
            }) {
                continue;
            }
            fundamentals.push((frequency, magnitude));
            let midi = (69.0 + 12.0 * (frequency / 440.0).log2()).round_ties_even() as i32;
            let column = midi - first_note;
            if let Some(pitch) = pitches.get_mut(column as usize) {
                if magnitude > *pitch {
                    *pitch = magnitude;
                }
            }
        }
    }
}

fn is_harmonic(frequency: f64, base: f64) -> bool {
    let multiple = (frequency / base).round_ties_even();
    (2.0..=6.0).contains(&multiple)
        && (frequency - multiple * base).abs() <= (frequency * 0.012).max(2.5)
}
