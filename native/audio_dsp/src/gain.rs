use std::slice;

/// Apply gain to native-endian PCM in place. Format IDs 1 through 4
/// represent unsigned 8-bit, signed 16-bit, signed 32-bit, and float32.
///
/// # Safety
/// For a nonempty buffer the caller must provide exclusive access to
/// `len` writable bytes at `data`.
#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_gain_v1(
    data: *mut u8,
    len: usize,
    format: u8,
    gain: f64,
) -> bool {
    let width = match format {
        1 => 1,
        2 => 2,
        3 | 4 => 4,
        _ => return false,
    };
    if !gain.is_finite() || len % width != 0 || len > isize::MAX as usize {
        return false;
    }
    if len == 0 {
        return true;
    }
    if data.is_null() {
        return false;
    }
    let data = unsafe { slice::from_raw_parts_mut(data, len) };
    match format {
        1 => {
            let gain = gain as f32;
            for sample in data {
                let normalized = (f32::from(*sample) - 128.0) / 128.0;
                let amplified = (normalized * gain).clamp(-1.0, 1.0);
                *sample = (amplified * 128.0 + 128.0).round_ties_even() as u8;
            }
        }
        2 => {
            for sample in data.chunks_exact_mut(2) {
                let input = i16::from_ne_bytes([sample[0], sample[1]]);
                let output = (f64::from(input) * gain).round_ties_even() as i16;
                sample.copy_from_slice(&output.to_ne_bytes());
            }
        }
        3 => {
            for sample in data.chunks_exact_mut(4) {
                let input = i32::from_ne_bytes(sample.try_into().unwrap());
                let output = (f64::from(input) * gain).round_ties_even() as i32;
                sample.copy_from_slice(&output.to_ne_bytes());
            }
        }
        4 => {
            let gain = gain as f32;
            for sample in data.chunks_exact_mut(4) {
                let input = f32::from_ne_bytes(sample.try_into().unwrap());
                let output = (input * gain).clamp(-1.0, 1.0);
                sample.copy_from_slice(&output.to_ne_bytes());
            }
        }
        _ => unreachable!(),
    }
    true
}
