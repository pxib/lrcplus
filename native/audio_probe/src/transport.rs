use std::collections::VecDeque;
use std::io::{self, BufRead, Write};
use std::path::Path;
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use lrcplus_audio_dsp_v1::LiveRate;

use crate::{
    media::Decoder,
    mix,
    output::Device,
    pitch::{PitchStage, BLOCK_FRAMES},
    Options, Result, MAX_TRANSPORT_GAIN,
};

const REPORT_INTERVAL: Duration = Duration::from_millis(33);
const DEVICE_TIMEOUT: Duration = Duration::from_secs(5);

enum Command {
    Play,
    Pause,
    Seek(u64, u64),
    Speed(f64),
    Pitch(f64),
    Volume(f32),
    Quit,
}

impl Command {
    fn parse(line: &str) -> Option<Self> {
        let mut fields = line.split_whitespace();
        let command = match fields.next()? {
            "play" => Self::Play,
            "pause" => Self::Pause,
            "seek" => Self::Seek(fields.next()?.parse().ok()?, fields.next()?.parse().ok()?),
            "speed" => {
                let speed = fields.next()?.parse().ok()?;
                if !(0.25..=2.0).contains(&speed) {
                    return None;
                }
                Self::Speed(speed)
            }
            "pitch" => {
                let pitch = fields.next()?.parse().ok()?;
                if !(-24.0..=24.0).contains(&pitch) {
                    return None;
                }
                Self::Pitch(pitch)
            }
            "volume" => {
                let gain = fields.next()?.parse().ok()?;
                if !(0.0..=MAX_TRANSPORT_GAIN).contains(&gain) {
                    return None;
                }
                Self::Volume(gain)
            }
            "quit" => Self::Quit,
            _ => return None,
        };
        fields.next().is_none().then_some(command)
    }
}

pub fn run(options: Options) -> Result<()> {
    let (send, commands) = mpsc::sync_channel(16);
    thread::spawn(move || {
        for line in io::stdin()
            .lock()
            .lines()
            .map_while(std::result::Result::ok)
        {
            if let Some(command) = Command::parse(&line) {
                if send.send(command).is_err() {
                    break;
                }
            }
        }
    });
    let mut decoder = Decoder::open(&options.path)?;
    let mut gain = options.volume;
    let mut speed = options.speed;
    let mut semitones = options.pitch;
    let library = options.pitch_library.as_deref();
    let mut audio = Playback::new(&decoder, speed, gain, library, semitones)?;
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    writeln!(
        stdout,
        "ready {} {} 4",
        decoder.duration_ms, audio.device.sample_rate
    )?;
    stdout.flush()?;

    let mut playing = false;
    let mut epoch = 0;
    let mut offset = 0;
    let mut heard_frames = 0;
    let mut advanced = Instant::now();
    let mut reported = Instant::now();
    loop {
        let command = if playing {
            audio.device.check_device()?;
            let should_wait = !audio.pump(&mut decoder)?;
            let frames = audio.device.audible_frames();
            if frames > heard_frames {
                heard_frames = frames;
                advanced = Instant::now();
            }
            let mut position = audio.position(frames, offset, decoder.duration_ms);
            if audio.drained() {
                audio.device.pause()?;
                playing = false;
                if decoder.duration_ms != 0 {
                    position = decoder.duration_ms;
                }
                report(&mut stdout, "ended", epoch, position, &audio)?;
            } else if advanced.elapsed() >= DEVICE_TIMEOUT {
                return Err("the output device stopped playing audio".into());
            } else if reported.elapsed() >= REPORT_INTERVAL {
                report(&mut stdout, "position", epoch, position, &audio)?;
                reported = Instant::now();
            }
            match commands.recv_timeout(if should_wait {
                Duration::from_millis(5)
            } else {
                Duration::ZERO
            }) {
                Ok(command) => command,
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => break,
            }
        } else {
            match commands.recv() {
                Ok(command) => command,
                Err(_) => break,
            }
        };
        match command {
            Command::Play => {
                playing = true;
                advanced = Instant::now();
            }
            Command::Pause => {
                audio.device.pause()?;
                playing = false;
                let frames = audio.device.audible_frames();
                let position = audio.position(frames, offset, decoder.duration_ms);
                report(&mut stdout, "position", epoch, position, &audio)?;
            }
            Command::Seek(next_epoch, milliseconds) => {
                audio.device.pause()?;
                audio = Playback::new(&decoder, speed, gain, library, semitones)?;
                let at_end = decoder.duration_ms != 0 && milliseconds >= decoder.duration_ms;
                offset = if at_end {
                    decoder.duration_ms
                } else {
                    milliseconds
                };
                if at_end {
                    audio.rate.finish();
                    audio.device.mark_final();
                } else {
                    decoder.seek(offset)?;
                }
                epoch = next_epoch;
                heard_frames = 0;
                advanced = Instant::now();
                report(&mut stdout, "position", epoch, offset, &audio)?;
            }
            Command::Speed(value) => {
                speed = value;
                audio.speed = value;
                if audio.pitch.is_none() {
                    audio.rate.set_ratio(speed * audio.base_ratio)?;
                }
            }
            Command::Pitch(value) => {
                semitones = value;
                audio.pitch_scale = pitch_scale(value);
            }
            Command::Volume(value) => {
                gain = value;
                audio.device.set_gain(value);
            }
            Command::Quit => break,
        }
    }
    Ok(())
}

fn pitch_scale(semitones: f64) -> f64 {
    (semitones / 12.0).exp2()
}

fn report(
    out: &mut impl Write,
    event: &str,
    epoch: u64,
    position: u64,
    audio: &Playback,
) -> io::Result<()> {
    let audible_speed =
        (10_000.0 * (audio.clock.step / audio.base_ratio).clamp(0.25, 2.0)).round() as u32;
    writeln!(
        out,
        "{event} {epoch} {position} {} {audible_speed}",
        audio.device.underrun_frames()
    )?;
    out.flush()
}

struct Playback {
    device: Device,
    rate: LiveRate,
    pitch: Option<PitchStage>,
    speed: f64,
    pitch_scale: f64,
    base_ratio: f64,
    source_rate: u32,
    packet_offset: usize,
    rendered: Vec<f32>,
    mixed: Vec<f32>,
    positions: Vec<f64>,
    clock: SourceClock,
}

impl Playback {
    fn new(
        decoder: &Decoder,
        speed: f64,
        gain: f32,
        library: Option<&Path>,
        semitones: f64,
    ) -> Result<Self> {
        let device = Device::for_transport()?;
        device.set_gain(gain);
        let base_ratio = f64::from(decoder.sample_rate) / f64::from(device.sample_rate);
        let ramp_frames = (device.sample_rate as usize / 40).max(1);
        let pitch_scale = pitch_scale(semitones);
        let pitch = library
            .map(|path| {
                PitchStage::open(
                    path,
                    device.sample_rate,
                    decoder.channels,
                    speed,
                    pitch_scale,
                )
            })
            .transpose()?;
        Ok(Self {
            rate: LiveRate::new(
                base_ratio * if pitch.is_some() { 1.0 } else { speed },
                base_ratio * 2.0,
                decoder.channels,
                ramp_frames,
            )?,
            device,
            pitch,
            speed,
            pitch_scale,
            base_ratio,
            source_rate: decoder.sample_rate,
            packet_offset: decoder.current_samples().len(),
            rendered: Vec::new(),
            mixed: Vec::new(),
            positions: Vec::new(),
            clock: SourceClock {
                step: base_ratio * speed,
                ..SourceClock::default()
            },
        })
    }

    fn pump(&mut self, decoder: &mut Decoder) -> Result<bool> {
        let available = self.device.available_frames().min(BLOCK_FRAMES);
        if available != 0 {
            if self.render(available) {
                if self.packet_offset == decoder.current_samples().len() {
                    self.packet_offset = 0;
                    if decoder.next()?.is_none() {
                        self.rate.finish();
                        return Ok(true);
                    }
                }
                let samples = decoder.current_samples();
                let end = (self.packet_offset + 1024 * decoder.channels).min(samples.len());
                self.rate.push(&samples[self.packet_offset..end]);
                self.packet_offset = end;
                return Ok(true);
            }
            mix(
                &self.rendered,
                decoder.channels,
                self.device.channels,
                1.0,
                &mut self.mixed,
            );
            // Output is limited to free slots. The device only frees slots
            // between the capacity check and the enqueue on this thread.
            let written = self.device.enqueue(&self.mixed);
            debug_assert_eq!(written, self.mixed.len());
            self.clock.pending.extend(&self.positions);
        }
        let final_block = self.rendering_finished();
        if final_block {
            self.device.mark_final();
        }
        if self.clock.heard != 0 || !self.clock.pending.is_empty() {
            self.device.start_when_ready(final_block)?;
        }
        Ok(available != 0 && !final_block)
    }

    /// Returns true when the resampler needs more decoded input.
    fn render(&mut self, available: usize) -> bool {
        self.rendered.clear();
        self.positions.clear();
        let Some(pitch) = &mut self.pitch else {
            self.rate
                .render(available, &mut self.rendered, &mut self.positions);
            return self.positions.is_empty() && !self.rate.drained();
        };
        pitch.read(available, &mut self.rendered, &mut self.positions);
        if !self.positions.is_empty() || pitch.finished() {
            return false;
        }
        self.rate
            .render(BLOCK_FRAMES, &mut self.rendered, &mut self.positions);
        if self.positions.is_empty() && !self.rate.drained() {
            return true;
        }
        pitch.process(
            &self.rendered,
            &self.positions,
            self.speed,
            self.pitch_scale,
            self.rate.drained(),
        );
        self.rendered.clear();
        self.positions.clear();
        pitch.read(available, &mut self.rendered, &mut self.positions);
        false
    }

    fn rendering_finished(&self) -> bool {
        self.rate.drained() && self.pitch.as_ref().is_none_or(PitchStage::drained)
    }

    fn drained(&self) -> bool {
        self.rendering_finished() && self.clock.pending.is_empty()
    }

    fn position(&mut self, frames: u64, offset: u64, duration: u64) -> u64 {
        let elapsed = self.clock.advance(frames) * 1000.0 / f64::from(self.source_rate);
        let position = offset + elapsed as u64;
        if duration == 0 {
            position
        } else {
            position.min(duration)
        }
    }
}

#[derive(Default)]
struct SourceClock {
    heard: u64,
    source: f64,
    step: f64,
    pending: VecDeque<f64>,
}

impl SourceClock {
    fn advance(&mut self, audible_frames: u64) -> f64 {
        let count = (audible_frames.saturating_sub(self.heard) as usize).min(self.pending.len());
        if count != 0 {
            let previous = if count == 1 {
                self.source
            } else {
                self.pending[count - 2]
            };
            self.source = self.pending[count - 1];
            self.step = self.source - previous;
            self.pending.drain(..count);
            self.heard += count as u64;
        }
        self.source
    }
}

#[cfg(test)]
mod tests {
    use super::{Command, SourceClock};

    #[test]
    fn volume_accepts_manual_boost_and_replaygain() {
        assert!(matches!(
            Command::parse("volume 2.5"),
            Some(Command::Volume(2.5))
        ));
        assert!(matches!(
            Command::parse("volume 3000"),
            Some(Command::Volume(3000.0))
        ));
        for line in ["volume -1", "volume 3000.1", "volume NaN", "volume inf"] {
            assert!(Command::parse(line).is_none(), "{line}");
        }
    }

    #[test]
    fn clock_uses_the_source_phase_of_audible_frames() {
        let mut clock = SourceClock::default();
        clock.pending.extend([1.0, 2.0, 3.0, 4.25, 5.75]);
        assert_eq!(clock.advance(0), 0.0);
        assert_eq!(clock.advance(2), 2.0);
        assert_eq!(clock.advance(1), 2.0);
        assert_eq!(clock.advance(4), 4.25);
        assert_eq!(clock.step, 1.25);
        assert_eq!(clock.advance(5), 5.75);
        assert_eq!(clock.step, 1.5);
        assert!(clock.pending.is_empty());
    }
}
