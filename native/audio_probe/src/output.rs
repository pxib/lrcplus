use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread::sleep;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, SampleFormat, SizedSample, Stream, StreamConfig};
use rtrb::{Consumer, Producer, RingBuffer};

use crate::Result;

const WAIT: Duration = Duration::from_millis(2);
const DEVICE_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Default)]
struct State {
    finished: AtomicBool,
    failed: AtomicBool,
    callbacks: AtomicU64,
    frames_submitted: AtomicU64,
    underrun_frames: AtomicU64,
    play_until_ns: AtomicU64,
}

pub struct Metrics {
    pub callbacks: u64,
    pub underrun_frames: u64,
}

pub struct Device {
    stream: Stream,
    producer: Producer<f32>,
    state: Arc<State>,
    origin: Instant,
    capacity: usize,
    prefill: usize,
    running: bool,
    pub name: String,
    pub sample_rate: u32,
    pub channels: usize,
}

impl Device {
    pub fn open() -> Result<Self> {
        let output = cpal::default_host()
            .default_output_device()
            .ok_or("no default audio device")?;
        let name = output
            .name()
            .unwrap_or_else(|_| "default audio device".to_owned());
        let supported = output.default_output_config()?;
        let config = supported.config();
        let sample_rate = config.sample_rate.0;
        let channels = usize::from(config.channels);
        if sample_rate == 0 || channels == 0 {
            return Err("invalid output device configuration".into());
        }
        let capacity = (sample_rate as usize / 4).max(1) * channels;
        let prefill = (sample_rate as usize / 8).max(1) * channels;
        let (producer, consumer) = RingBuffer::<f32>::new(capacity);
        let state = Arc::new(State::default());
        let origin = Instant::now();
        let stream = match supported.sample_format() {
            SampleFormat::F32 => {
                build::<f32>(&output, &config, consumer, Arc::clone(&state), origin)
            }
            SampleFormat::I16 => {
                build::<i16>(&output, &config, consumer, Arc::clone(&state), origin)
            }
            SampleFormat::I32 => {
                build::<i32>(&output, &config, consumer, Arc::clone(&state), origin)
            }
            SampleFormat::U16 => {
                build::<u16>(&output, &config, consumer, Arc::clone(&state), origin)
            }
            format => return Err(format!("unsupported device sample format: {format}").into()),
        }?;
        Ok(Self {
            stream,
            producer,
            state,
            origin,
            capacity,
            prefill,
            running: false,
            name,
            sample_rate,
            channels,
        })
    }

    fn start(&mut self) -> Result<()> {
        if !self.running {
            self.stream.play()?;
            self.running = true;
        }
        Ok(())
    }

    pub fn push(&mut self, mut samples: &[f32]) -> Result<()> {
        let mut last_write = Instant::now();
        while !samples.is_empty() {
            self.check_device()?;
            let (written, rest) = self.producer.push_partial_slice(samples);
            samples = rest;
            if !self.running && self.capacity - self.producer.slots() >= self.prefill {
                self.start()?;
            }
            if written.is_empty() {
                if last_write.elapsed() >= DEVICE_TIMEOUT {
                    return Err("the output device stopped consuming audio".into());
                }
                sleep(WAIT);
            } else {
                last_write = Instant::now();
            }
        }
        Ok(())
    }

    pub fn finish(&mut self, frames: usize) -> Result<Metrics> {
        self.state.finished.store(true, Ordering::Release);
        self.start()?;
        let started = Instant::now();
        loop {
            self.check_device()?;
            let submitted = self.state.frames_submitted.load(Ordering::Acquire);
            let play_until = self.state.play_until_ns.load(Ordering::Relaxed);
            if submitted >= frames as u64
                && self.origin.elapsed().as_nanos() >= u128::from(play_until)
            {
                break;
            }
            if started.elapsed() >= DEVICE_TIMEOUT {
                return Err("timed out waiting for the last audio frames to play".into());
            }
            sleep(WAIT);
        }
        self.stream.pause()?;
        Ok(Metrics {
            callbacks: self.state.callbacks.load(Ordering::Relaxed),
            underrun_frames: self.state.underrun_frames.load(Ordering::Relaxed),
        })
    }

    fn check_device(&self) -> Result<()> {
        if self.state.failed.load(Ordering::Acquire) || self.producer.is_abandoned() {
            Err("the output device failed".into())
        } else {
            Ok(())
        }
    }
}

fn build<T>(
    device: &cpal::Device,
    config: &StreamConfig,
    mut consumer: Consumer<f32>,
    state: Arc<State>,
    origin: Instant,
) -> Result<Stream>
where
    T: SizedSample + FromSample<f32>,
{
    let channels = usize::from(config.channels);
    let rate = u64::from(config.sample_rate.0);
    let failure = Arc::clone(&state);
    Ok(device.build_output_stream(
        config,
        move |output: &mut [T], info| {
            // The callback borrows ready PCM and adds silence on starvation.
            // It never decodes, allocates, or waits for the producer.
            let frames = (output.len().min(consumer.slots())) / channels;
            let samples = frames * channels;
            if let Ok(chunk) = consumer.read_chunk(samples) {
                let (first, second) = chunk.as_slices();
                for (out, &sample) in output[..samples].iter_mut().zip(first.iter().chain(second)) {
                    *out = T::from_sample(sample);
                }
                chunk.commit_all();
            }
            output[samples..].fill(T::from_sample(0.0));
            state.callbacks.fetch_add(1, Ordering::Relaxed);
            if !state.finished.load(Ordering::Acquire) {
                state
                    .underrun_frames
                    .fetch_add((output.len() / channels - frames) as u64, Ordering::Relaxed);
            }
            if frames != 0 {
                let timestamp = info.timestamp();
                let delay = timestamp
                    .playback
                    .duration_since(&timestamp.callback)
                    .unwrap_or_default();
                let audio_duration = Duration::from_nanos(frames as u64 * 1_000_000_000 / rate);
                let play_until = origin.elapsed() + delay + audio_duration;
                state
                    .play_until_ns
                    .store(play_until.as_nanos() as u64, Ordering::Relaxed);
                state
                    .frames_submitted
                    .fetch_add(frames as u64, Ordering::Release);
            }
        },
        move |error| {
            eprintln!("Audio device: {error}");
            failure.failed.store(true, Ordering::Release);
        },
        None,
    )?)
}
