//! Bounded PCM queues, DSP control decisions, and the rendered playback clock.

use std::collections::VecDeque;
use std::ptr;
use std::slice;
use std::sync::Mutex;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Controls {
    speed: f64,
    shift: f64,
    gain: f64,
    enabled: bool,
    live_speed: bool,
    pitch_linked: bool,
}

impl Default for Controls {
    fn default() -> Self {
        Self {
            speed: 1.0,
            shift: 0.0,
            gain: 1.0,
            enabled: false,
            live_speed: false,
            pitch_linked: false,
        }
    }
}

impl Controls {
    fn requires_reset(self, next: Self, device_changed: bool) -> bool {
        let pitch_restart = (self.shift.abs() < 0.001) != (next.shift.abs() < 0.001)
            || (self.shift - next.shift).abs() >= 12.0;
        let continuous = next.live_speed && self.pitch_linked == next.pitch_linked;
        device_changed
            || self.enabled != next.enabled
            || (!continuous && ((self.speed - next.speed).abs() >= 0.001 || pitch_restart))
    }

    fn plan(self, rate: f64) -> DspPlan {
        let continuous = self.live_speed && !self.pitch_linked;
        let shift = if continuous {
            self.shift + 12.0 * (self.speed / rate).log2()
        } else {
            self.shift
        };
        DspPlan {
            shift,
            boost: self.gain.max(1.0),
            continuous,
            pitched: continuous || self.shift.abs() >= 0.001,
            trim_pitch_padding: self.live_speed,
        }
    }
}

#[repr(C)]
pub struct DspPlan {
    shift: f64,
    boost: f64,
    continuous: bool,
    pitched: bool,
    trim_pitch_padding: bool,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Format {
    sample_rate: i32,
    channels: i32,
    sample_type: i32,
    channel_layout: u32,
}

#[repr(C)]
pub struct DecodedBlock {
    data: *const u8,
    len: usize,
    timestamp: i64,
    format: Format,
}

struct OwnedBlock {
    data: Vec<u8>,
    timestamp: i64,
    format: Format,
}

#[derive(Default)]
pub struct Inbox {
    queued: VecDeque<OwnedBlock>,
    queued_bytes: usize,
    borrowed: Option<OwnedBlock>,
    controls: Controls,
}

impl Inbox {
    fn push(&mut self, block: OwnedBlock, limit: usize) {
        let size = block.data.len();
        let limit = limit.max(size);
        while self.queued_bytes > limit - size {
            match self.queued.pop_front() {
                Some(next) => self.queued_bytes -= next.data.len(),
                None => break,
            }
        }
        self.queued_bytes += size;
        self.queued.push_back(block);
    }

    fn pop(&mut self) -> Option<DecodedBlock> {
        self.borrowed = self.queued.pop_front();
        let block = self.borrowed.as_ref()?;
        self.queued_bytes -= block.data.len();
        Some(DecodedBlock {
            data: block.data.as_ptr(),
            len: block.data.len(),
            timestamp: block.timestamp,
            format: block.format,
        })
    }
}

struct ClockSpan {
    bytes: usize,
    position: Option<f64>,
    step: f64,
}

struct WrittenSpan {
    start: usize,
    position: Option<f64>,
    step: f64,
}

pub struct Output {
    sample_rate: f64,
    frame_size: usize,
    total_source: usize,
    next_source: f64,
    anchors: VecDeque<(usize, Option<i64>)>,
    resampled: VecDeque<(usize, f64)>,
    pending: VecDeque<ClockSpan>,
    written: VecDeque<WrittenSpan>,
    written_bytes: usize,
    pcm: VecDeque<u8>,
}

impl Output {
    fn new(sample_rate: u32, frame_size: usize) -> Self {
        Self {
            sample_rate: f64::from(sample_rate),
            frame_size,
            total_source: 0,
            next_source: 0.0,
            anchors: VecDeque::from([(0, None)]),
            resampled: VecDeque::new(),
            pending: VecDeque::new(),
            written: VecDeque::new(),
            written_bytes: 0,
            pcm: VecDeque::new(),
        }
    }

    fn to_us(&self, frames: f64) -> f64 {
        frames * 1_000_000.0 / self.sample_rate
    }

    fn add_source(&mut self, frames: usize, timestamp: i64) {
        let (anchor, anchor_us) = *self.anchors.back().unwrap();
        // Small differences are decoder rounding. A missing block introduces
        // a new source timestamp at the matching output sample.
        let elapsed = self.to_us((self.total_source - anchor) as f64);
        if timestamp != -1 && anchor_us.is_none_or(|us| (timestamp as f64 - us as f64 - elapsed).abs() > 2000.0) {
            self.anchors.push_back((self.total_source, Some(timestamp)));
        }
        self.total_source += frames;
    }

    fn source_position(&mut self) -> Option<f64> {
        let frame = self.next_source.min(self.total_source as f64);
        while self.anchors.get(1).is_some_and(|&(start, _)| start as f64 <= frame + 1e-7) {
            self.anchors.pop_front();
        }
        let (start, timestamp) = self.anchors[0];
        timestamp.map(|time| time as f64 + self.to_us(frame - start as f64))
    }

    fn add_resampled(&mut self, mut frames: usize, rate: f64) {
        if let Some(&(previous, last_rate)) = self.resampled.back() {
            if last_rate == rate {
                self.resampled.pop_back();
                frames += previous;
            }
        }
        self.resampled.push_back((frames, rate));
    }

    fn queue(&mut self, pcm: &[u8], padding: usize, content: usize) {
        let frames = pcm.len() / self.frame_size;
        if padding != 0 {
            self.push_span(padding, 0.0);
        }
        // Pitch correction may hold frames across decoder callbacks or supply
        // padding. Advance source time only for real, resampled input.
        let mut remaining = content;
        while remaining != 0 {
            let Some((span, rate)) = self.resampled.pop_front() else { break; };
            let position = self.source_position();
            let mut count = remaining.min(span);
            if let Some(&(boundary, _)) = self.anchors.get(1) {
                let distance = (boundary as f64 - self.next_source) / rate;
                count = count.min((distance - 1e-9).ceil().max(1.0) as usize);
            }
            self.pending.push_back(ClockSpan {
                bytes: count * self.frame_size,
                position,
                step: self.to_us(rate) / self.frame_size as f64,
            });
            self.next_source += count as f64 * rate;
            remaining -= count;
            if count < span {
                self.resampled.push_front((span - count, rate));
            }
        }
        let trailing = frames - padding - (content - remaining);
        if trailing != 0 {
            self.push_span(trailing, 0.0);
        }
        self.pcm.extend(pcm.iter().copied());
    }

    fn push_span(&mut self, frames: usize, step: f64) {
        let position = self.source_position();
        self.pending.push_back(ClockSpan { bytes: frames * self.frame_size, position, step });
    }

    fn consume(&mut self, mut size: usize, written: bool) {
        self.pcm.drain(..size);
        while size != 0 {
            let mut span = self.pending.pop_front().unwrap();
            let used = size.min(span.bytes);
            if written {
                self.written.push_back(WrittenSpan {
                    start: self.written_bytes,
                    position: span.position,
                    step: span.step,
                });
                self.written_bytes += used;
            }
            if used < span.bytes {
                span.bytes -= used;
                span.position = span.position.map(|position| position + used as f64 * span.step);
                self.pending.push_front(span);
            }
            size -= used;
        }
    }

    fn trim(&mut self, pending_before: usize, limit: usize) {
        let added = self.pcm.len().saturating_sub(pending_before);
        let limit = limit.max(added);
        let limit = limit - limit % self.frame_size;
        self.consume(self.pcm.len().saturating_sub(limit), false);
    }

    fn position_ms(&mut self, played_us: i64) -> Option<f64> {
        let played = (played_us as f64 * self.sample_rate * self.frame_size as f64 / 1_000_000.0)
            .min(self.written_bytes as f64);
        while self.written.get(1).is_some_and(|span| span.start as f64 <= played) {
            self.written.pop_front();
        }
        let span = self.written.front()?;
        span.position.map(|position| ((position + (played - span.start as f64) * span.step) / 1000.0).max(0.0))
    }
}

// The C ABI is internal. Python serializes inbox transfers across Qt threads
// and copies borrowed input/output slices before the next mutation. The mutexes
// also allow read-only counters to be inspected from another thread.

#[no_mangle]
pub extern "C" fn lrcplus_pcm_inbox_new_v1() -> *mut Mutex<Inbox> {
    Box::into_raw(Box::new(Mutex::new(Inbox::default())))
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_inbox_free_v1(inbox: *mut Mutex<Inbox>) {
    drop(unsafe { Box::from_raw(inbox) });
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_inbox_clear_v1(inbox: *const Mutex<Inbox>) {
    let mut inbox = unsafe { &*inbox }.lock().unwrap();
    inbox.queued.clear();
    inbox.queued_bytes = 0;
    inbox.borrowed = None;
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_inbox_len_v1(inbox: *const Mutex<Inbox>) -> usize {
    unsafe { &*inbox }.lock().unwrap().queued_bytes
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_inbox_push_v1(inbox: *const Mutex<Inbox>, block: *const DecodedBlock, limit: usize) {
    let block = unsafe { &*block };
    let data = unsafe { slice::from_raw_parts(block.data, block.len) }.to_vec();
    unsafe { &*inbox }.lock().unwrap().push(OwnedBlock { data, timestamp: block.timestamp, format: block.format }, limit);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_inbox_pop_v1(inbox: *const Mutex<Inbox>, block: *mut DecodedBlock) -> bool {
    let Some(next) = unsafe { &*inbox }.lock().unwrap().pop() else { return false; };
    unsafe { *block = next; }
    true
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_configure_v1(inbox: *const Mutex<Inbox>, controls: *const Controls, device_changed: bool) -> bool {
    let mut inbox = unsafe { &*inbox }.lock().unwrap();
    let controls = unsafe { *controls };
    let reset = inbox.controls.requires_reset(controls, device_changed);
    inbox.controls = controls;
    reset
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_dsp_plan_v1(controls: *const Controls, rate: f64, out: *mut DspPlan) {
    unsafe { *out = (*controls).plan(rate); }
}

#[no_mangle]
pub extern "C" fn lrcplus_pcm_output_new_v1(sample_rate: u32, frame_size: usize) -> *mut Mutex<Output> {
    Box::into_raw(Box::new(Mutex::new(Output::new(sample_rate, frame_size))))
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_free_v1(output: *mut Mutex<Output>) {
    drop(unsafe { Box::from_raw(output) });
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_len_v1(output: *const Mutex<Output>) -> usize {
    unsafe { &*output }.lock().unwrap().pcm.len()
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_written_v1(output: *const Mutex<Output>) -> usize {
    unsafe { &*output }.lock().unwrap().written_bytes
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_add_source_v1(output: *const Mutex<Output>, frames: usize, timestamp: i64) {
    unsafe { &*output }.lock().unwrap().add_source(frames, timestamp);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_add_resampled_v1(output: *const Mutex<Output>, frames: usize, rate: f64) {
    unsafe { &*output }.lock().unwrap().add_resampled(frames, rate);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_queue_v1(output: *const Mutex<Output>, pcm: *const u8, len: usize, padding: usize, content: usize) {
    let pcm = unsafe { slice::from_raw_parts(pcm, len) };
    unsafe { &*output }.lock().unwrap().queue(pcm, padding, content);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_trim_v1(output: *const Mutex<Output>, pending_before: usize, limit: usize) {
    unsafe { &*output }.lock().unwrap().trim(pending_before, limit);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_peek_v1(output: *const Mutex<Output>, capacity: usize, size: *mut usize) -> *const u8 {
    let mut output = unsafe { &*output }.lock().unwrap();
    let count = capacity.min(output.pcm.len());
    let count = count - count % output.frame_size;
    unsafe { *size = count; }
    if count == 0 { ptr::null() } else { output.pcm.make_contiguous().as_ptr() }
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_commit_v1(output: *const Mutex<Output>, size: usize) {
    unsafe { &*output }.lock().unwrap().consume(size, true);
}

#[no_mangle]
pub unsafe extern "C" fn lrcplus_pcm_output_position_v1(output: *const Mutex<Output>, played_us: i64) -> f64 {
    unsafe { &*output }.lock().unwrap().position_ms(played_us).unwrap_or(f64::NAN)
}
