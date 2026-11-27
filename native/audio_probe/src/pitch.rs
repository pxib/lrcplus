use std::collections::VecDeque;
use std::ffi::c_void;
use std::path::Path;
use std::ptr::NonNull;

use crate::Result;

pub const BLOCK_FRAMES: usize = 512;

type State = *mut c_void;
type New = unsafe extern "C" fn(u32, u32, i32, f64, f64) -> State;
type Delete = unsafe extern "C" fn(State);
type SetRatio = unsafe extern "C" fn(State, f64);
type SetFrames = unsafe extern "C" fn(State, u32);
type GetFrames = unsafe extern "C" fn(State) -> u32;
type GetCount = unsafe extern "C" fn(State) -> i32;
type Process = unsafe extern "C" fn(State, *const *const f32, u32, i32);
type Retrieve = unsafe extern "C" fn(State, *const *mut f32, u32) -> u32;

struct Api {
    _library: dll::Library,
    delete: Delete,
    set_time: SetRatio,
    set_pitch: SetRatio,
    start_pad: GetFrames,
    start_delay: GetFrames,
    process: Process,
    available: GetCount,
    retrieve: Retrieve,
}

pub struct PitchStage {
    api: Api,
    state: NonNull<c_void>,
    channels: usize,
    speed: f64,
    pitch: f64,
    trim: Option<usize>,
    input: [Vec<f32>; 2],
    output: [Vec<f32>; 2],
    timeline: Timeline,
    finished: bool,
}

impl PitchStage {
    pub fn open(path: &Path, rate: u32, channels: usize, speed: f64, pitch: f64) -> Result<Self> {
        if !(1..=2).contains(&channels) {
            return Err("pitch processing requires mono or stereo PCM".into());
        }
        let library = dll::Library::open(path)?;
        // These signatures come from rubberband-c.h. The library stays alive
        // until after its processing state is deleted.
        let (new, engine, set_block, api) = unsafe {
            (
                library.symbol::<New>(c"rubberband_new")?,
                library.symbol::<GetCount>(c"rubberband_get_engine_version")?,
                library.symbol::<SetFrames>(c"rubberband_set_max_process_size")?,
                Api {
                    delete: library.symbol(c"rubberband_delete")?,
                    set_time: library.symbol(c"rubberband_set_time_ratio")?,
                    set_pitch: library.symbol(c"rubberband_set_pitch_scale")?,
                    start_pad: library.symbol(c"rubberband_get_preferred_start_pad")?,
                    start_delay: library.symbol(c"rubberband_get_start_delay")?,
                    process: library.symbol(c"rubberband_process")?,
                    available: library.symbol(c"rubberband_available")?,
                    retrieve: library.symbol(c"rubberband_retrieve")?,
                    _library: library,
                },
            )
        };
        // Real-time, single thread, consistent pitch, shared stereo analysis,
        // short window, and the R3 engine used by the existing Qt route.
        let options = 0x24110001;
        let state =
            NonNull::new(unsafe { new(rate, channels as u32, options, 1.0 / speed, pitch) })
                .ok_or("could not create the Rubber Band time stretcher")?;
        if unsafe { engine(state.as_ptr()) } != 3 {
            unsafe { (api.delete)(state.as_ptr()) };
            return Err("the installed Rubber Band DLL does not support R3".into());
        }
        unsafe { set_block(state.as_ptr(), BLOCK_FRAMES as u32) };
        Ok(Self {
            api,
            state,
            channels,
            speed,
            pitch,
            trim: None,
            input: std::array::from_fn(|_| vec![0.0; BLOCK_FRAMES]),
            output: std::array::from_fn(|_| vec![0.0; BLOCK_FRAMES]),
            timeline: Timeline::default(),
            finished: false,
        })
    }

    pub fn process(
        &mut self,
        pcm: &[f32],
        positions: &[f64],
        speed: f64,
        pitch: f64,
        finish: bool,
    ) {
        let frames = positions.len();
        debug_assert!(frames <= BLOCK_FRAMES && !self.finished);
        debug_assert_eq!(pcm.len(), frames * self.channels);
        if self.speed != speed {
            unsafe { (self.api.set_time)(self.state.as_ptr(), 1.0 / speed) };
            self.speed = speed;
        }
        if self.pitch != pitch {
            unsafe { (self.api.set_pitch)(self.state.as_ptr(), pitch) };
            self.pitch = pitch;
        }
        if self.trim.is_none() {
            let pad = unsafe { (self.api.start_pad)(self.state.as_ptr()) } as usize;
            self.trim = Some(unsafe { (self.api.start_delay)(self.state.as_ptr()) } as usize);
            for offset in (0..pad).step_by(BLOCK_FRAMES) {
                self.process_planes((pad - offset).min(BLOCK_FRAMES), false);
            }
        }
        for (i, frame) in pcm.chunks_exact(self.channels).enumerate() {
            for (channel, &sample) in frame.iter().enumerate() {
                self.input[channel][i] = sample;
            }
        }
        if let Some(&end) = positions.last() {
            self.timeline.add(end, frames as f64 / speed);
        }
        self.process_planes(frames, finish);
        self.finished = finish;
    }

    fn process_planes(&self, frames: usize, finish: bool) {
        let input = self.input.each_ref().map(|plane| plane.as_ptr());
        // The pointers cover the supplied frame count and Rubber Band reads
        // one plane per configured channel.
        unsafe {
            (self.api.process)(
                self.state.as_ptr(),
                input.as_ptr(),
                frames as u32,
                i32::from(finish),
            )
        };
    }

    pub fn finished(&self) -> bool {
        self.finished
    }

    pub fn drained(&self) -> bool {
        self.finished && (self.timeline.pending.is_empty() || self.available() == 0)
    }

    fn available(&self) -> usize {
        unsafe { (self.api.available)(self.state.as_ptr()).max(0) as usize }
    }

    fn retrieve(&mut self, frames: usize) -> usize {
        let output = self.output.each_mut().map(|plane| plane.as_mut_ptr());
        debug_assert!(frames <= BLOCK_FRAMES);
        unsafe { (self.api.retrieve)(self.state.as_ptr(), output.as_ptr(), frames as u32) as usize }
    }

    pub fn read(&mut self, limit: usize, samples: &mut Vec<f32>, positions: &mut Vec<f64>) {
        let mut trim = self.trim.unwrap_or(0);
        while trim != 0 {
            let skip = trim.min(self.available()).min(BLOCK_FRAMES);
            if skip == 0 {
                self.trim = Some(trim);
                return;
            }
            trim -= self.retrieve(skip);
        }
        if self.trim.is_some() {
            self.trim = Some(0);
        }
        // R3 can return padding after the final block. Only queue audio that
        // has a source position derived from the submitted input and tempo.
        let frames = limit
            .min(self.available())
            .min(self.timeline.pending.len())
            .min(BLOCK_FRAMES);
        let frames = if frames == 0 {
            0
        } else {
            self.retrieve(frames)
        };
        for i in 0..frames {
            samples.extend(self.output[..self.channels].iter().map(|plane| plane[i]));
        }
        positions.extend(self.timeline.pending.drain(..frames));
    }
}

impl Drop for PitchStage {
    fn drop(&mut self) {
        unsafe { (self.api.delete)(self.state.as_ptr()) };
    }
}

#[derive(Default)]
struct Timeline {
    source: f64,
    output: f64,
    pending: VecDeque<f64>,
}

impl Timeline {
    fn add(&mut self, source_end: f64, output_frames: f64) {
        let output_end = self.output + output_frames;
        // Carry fractional output length across blocks so rounding cannot
        // accumulate as the tempo changes.
        for frame in (self.output.floor() as u64 + 1)..=output_end.floor() as u64 {
            let ratio = (frame as f64 - self.output) / output_frames;
            self.pending
                .push_back(self.source + ratio * (source_end - self.source));
        }
        self.source = source_end;
        self.output = output_end;
    }
}

mod dll {
    use std::ffi::CStr;
    use std::path::Path;

    use crate::Result;

    #[cfg(windows)]
    use std::{ffi::c_void, os::windows::ffi::OsStrExt, ptr::NonNull};

    #[cfg(windows)]
    #[link(name = "kernel32")]
    extern "system" {
        fn LoadLibraryExW(name: *const u16, file: *mut c_void, flags: u32) -> *mut c_void;
        fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
        fn FreeLibrary(module: *mut c_void) -> i32;
    }

    pub struct Library(#[cfg(windows)] NonNull<c_void>);

    impl Library {
        pub fn open(path: &Path) -> Result<Self> {
            #[cfg(windows)]
            {
                let path = path.canonicalize()?;
                let name: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
                // Look for dependencies beside the DLL and in default safe directories.
                let module = unsafe { LoadLibraryExW(name.as_ptr(), std::ptr::null_mut(), 0x1100) };
                NonNull::new(module).map(Self).ok_or_else(|| {
                    format!(
                        "could not load Rubber Band: {}",
                        std::io::Error::last_os_error()
                    )
                    .into()
                })
            }
            #[cfg(not(windows))]
            {
                let _ = path;
                Err("this pitch module requires a Windows Rubber Band DLL".into())
            }
        }

        /// The caller supplies the exact ABI and type of the named function.
        pub unsafe fn symbol<T: Copy>(&self, name: &CStr) -> Result<T> {
            #[cfg(windows)]
            {
                let address = unsafe { GetProcAddress(self.0.as_ptr(), name.as_ptr().cast()) };
                if address.is_null() {
                    return Err(
                        "the installed Rubber Band DLL does not expose the required API".into(),
                    );
                }
                debug_assert_eq!(std::mem::size_of::<T>(), std::mem::size_of::<*mut c_void>());
                Ok(unsafe { std::mem::transmute_copy(&address) })
            }
            #[cfg(not(windows))]
            {
                let _ = name;
                Err("this pitch module requires a Windows Rubber Band DLL".into())
            }
        }
    }

    #[cfg(windows)]
    impl Drop for Library {
        fn drop(&mut self) {
            unsafe { FreeLibrary(self.0.as_ptr()) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Timeline;

    #[test]
    fn fractional_timing_carries_across_tempo_changes() {
        let mut timeline = Timeline::default();
        timeline.add(3.0, 2.5);
        timeline.add(6.0, 1.5);
        let positions: Vec<_> = timeline.pending.into_iter().collect();
        assert_eq!(positions.len(), 4);
        assert!(positions
            .iter()
            .zip([1.2, 2.4, 4.0, 6.0])
            .all(|(a, b)| (a - b).abs() < 1e-9));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires LRCPLUS_TEST_RUBBERBAND pointing to the installed Rubber Band DLL"]
    fn preserves_tone_during_live_tempo_changes_and_applies_an_independent_shift() {
        use super::{PitchStage, BLOCK_FRAMES};

        let library = std::env::var_os("LRCPLUS_TEST_RUBBERBAND").unwrap();
        let hz = 48_000;
        let input: Vec<_> = (0..144_017)
            .flat_map(|frame| {
                let sample =
                    (std::f64::consts::TAU * 440.0 * frame as f64 / f64::from(hz)).sin() as f32;
                [0.4 * sample, 0.2 * sample]
            })
            .collect();
        let mut pitch = PitchStage::open(library.as_ref(), hz, 2, 0.75, 1.0).unwrap();
        let mut output = Vec::new();
        let mut clock = Vec::new();
        let shifted = 2.0_f64.powf(7.0 / 12.0);
        let length = input.len() / 2;
        for start in (0..length).step_by(BLOCK_FRAMES) {
            let end = (start + BLOCK_FRAMES).min(length);
            let points: Vec<_> = (start + 1..=end).map(|frame| frame as f64).collect();
            let speed = if start < 48_000 { 0.75 } else { 1.5 };
            let scale = if start < 96_000 { 1.0 } else { shifted };
            pitch.process(
                &input[2 * start..2 * end],
                &points,
                speed,
                scale,
                end == length,
            );
            loop {
                let before = clock.len();
                pitch.read(BLOCK_FRAMES, &mut output, &mut clock);
                if clock.len() == before {
                    break;
                }
            }
        }
        assert!(pitch.drained());
        assert_eq!(clock.len() * 2, output.len());
        assert!(clock.windows(2).all(|pair| pair[0] < pair[1]));
        assert!(clock.last().copied().unwrap() >= length as f64 - 2000.0);
        for (start, end, expected) in [(16_000, 84_000, 440.0), (106_000, 117_000, 440.0 * shifted)]
        {
            let samples = &output[2 * start..2 * end];
            let crossing: Vec<_> = samples
                .chunks_exact(2)
                .zip(samples[2..].chunks_exact(2))
                .enumerate()
                .filter_map(|(i, (a, b))| (a[0] <= 0.0 && b[0] > 0.0).then_some(i))
                .collect();
            let mut errors: Vec<_> = crossing
                .windows(2)
                .map(|p| (f64::from(hz) / (p[1] - p[0]) as f64 - expected).abs())
                .collect();
            errors.sort_by(f64::total_cmp);
            assert!(errors[errors.len() * 9 / 10] < 14.0);
            assert!(samples.iter().all(|sample| sample.is_finite()));
        }
    }
}
